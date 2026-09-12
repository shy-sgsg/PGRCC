#!/usr/bin/env python3
"""Targeted J8 replay: clutter-support-only CSI calibration.

J8 keeps the production Current branch outside measured clutter support and
soft-blends a frozen OFF-derived J5/P1 or J6/joint correction only inside the
support mask.  This is a three-case development replay covering a fast target,
a range/support-edge target, and a slow target near a clutter ridge.  It is
not a formal matrix and it never trains AI.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_target_transfer as transfer  # noqa: E402
import run_joint_physics_calibration as calibration  # noqa: E402
import run_joint_physics_formal_matrix as formal  # noqa: E402
import run_joint_physics_selective_v2 as selective_v2  # noqa: E402
from experiment_provenance import (  # noqa: E402
    run_provenance_post,
    run_provenance_start,
)
from run_mechanism_aware_oracle import (  # noqa: E402
    patch_xml,
    run_logged,
    single_manifest,
    summary_metrics,
)


CURRENT = "J0_Current"
J8_P1 = "J8_SupportOnly_P1"
J8_JOINT = "J8_SupportOnly_Joint"
J8_METHODS = (J8_P1, J8_JOINT)


def finite(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.floating, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def resource_snapshot() -> dict[str, str]:
    def run(command: list[str]) -> str:
        result = subprocess.run(command, cwd=ROOT, text=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, check=False)
        return result.stdout.strip()
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "nvidia_smi": run(["nvidia-smi"]),
        "free_h": run(["free", "-h"]),
        "df_h_workspace": run(["df", "-h", str(ROOT)]),
    }


def make_specs() -> list[dict[str, Any]]:
    source = transfer.design_specs("audit", (14.0, 16.0, 14.0, 16.0))
    selected = [dict(source[0]), dict(source[5]), dict(source[7])]
    labels = ("fast_target", "support_edge_target", "slow_near_ridge_target")
    for index, (spec, label) in enumerate(zip(selected, labels)):
        spec["split"] = "j8_support_only"
        spec["scene_id"] = f"j8_{label}"
        spec["label"] = label
        if label == "support_edge_target":
            spec["range_bin"] = 1500
            spec["squint_deg"] = 0.25
        elif label == "slow_near_ridge_target":
            spec["ve_mps"] = 0.0
            spec["vn_mps"] = 0.0
            spec["near_clutter_ridge"] = True
            spec["high_speed_target"] = False
        spec["j8_case_role"] = label
        # Keep the source seed namespace disjoint from formal Test-V3 while
        # retaining the deterministic geometry of the targeted design.
        spec["seed"] = 2026130000 + index
    return selected


def run_support_method(
    role_root: Path,
    plan: Mapping[str, Any],
    output_root: Path,
    build_dir: Path,
    support_percentile: float,
    edge_guard_percentile: float,
) -> dict[str, Any]:
    if plan.get("fallback") or (
            not plan.get("apply_delay") and not plan.get("apply_phase")):
        manifest = single_manifest(role_root)
        return {
            "status": "alias_current",
            "root": str(role_root.resolve()),
            "manifest": str(manifest.resolve()),
            "metrics": summary_metrics(manifest),
            "steps": [{"kind": "identity"}],
        }
    raw = calibration.raw_input(role_root)
    xml = calibration.xml_path(role_root)
    corrected = output_root / "stage2/data/j8_support_corrected_period_0000.bin"
    steps = calibration.apply_support_only_raw_correction(
        raw, corrected, xml,
        plan["estimated_delay_ns"] if plan.get("apply_delay") else None,
        plan.get("phase_values") if plan.get("apply_phase") else None,
        support_percentile, edge_guard_percentile)
    result_dir = output_root / "stage2/algorithm_result/period_0000"
    output_xml = output_root / "stage2/config/j8_support_config.xml"
    patch_xml(xml, corrected, result_dir, output_xml)
    rc = run_logged(
        [str(build_dir.resolve() / "GMTI_core"), str(output_xml),
         "--runtime-mode=debug", "--runtime-diagnostics=on"],
        output_root / "gmticore.log")
    if corrected.is_file():
        corrected.unlink()
    if rc != 0:
        raise RuntimeError(f"J8 replay failed for {role_root}, exit={rc}")
    manifest = single_manifest(output_root)
    return {
        "status": "pass",
        "root": str(output_root.resolve()),
        "manifest": str(manifest.resolve()),
        "metrics": summary_metrics(manifest),
        "steps": steps,
    }


def run_one_scene(
    spec: dict[str, Any], base: dict[str, Any], output: Path, build_dir: Path,
    gate: dict[str, Any], positive_gate: dict[str, Any],
    support_percentile: float, edge_guard_percentile: float,
    safety: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    scene = formal.run_scene(spec, base, output, build_dir, True)
    if scene.get("status") != "pass":
        raise RuntimeError(f"scene failed: {spec['scene_id']} {scene}")
    formal.enrich_scene(scene, output, spec)
    scene_root = Path(scene["scene_root"])
    original = {role: Path(scene["roles"][role]["root"])
                for role in ("target_off", "target_on", "target_only")}
    plans = {
        J8_P1: transfer.make_frozen_plan(
            "J5_Selective_Physics_Calibration", original["target_off"], gate,
            positive_gate, scene_root / "_j8_plans"),
        J8_JOINT: transfer.make_frozen_plan(
            "J6_Joint_Phase_Surface", original["target_off"], gate,
            positive_gate, scene_root / "_j8_plans"),
    }
    roots: dict[str, dict[str, Path]] = {CURRENT: dict(original)}
    step_records: list[dict[str, Any]] = []
    for method in J8_METHODS:
        roots[method] = {}
        for role in ("target_off", "target_on", "target_only"):
            result = run_support_method(
                original[role], plans[method],
                scene_root / "j8" / method / role, build_dir,
                support_percentile, edge_guard_percentile)
            roots[method][role] = Path(result["root"])
            step_records.append({
                "method": method, "role": role,
                "steps": result["steps"],
            })
    current_raw = transfer.transfer_rows_for_method(
        spec, CURRENT, roots[CURRENT], original["target_on"])
    current_rows = {row["target_id"]: row for row in current_raw}
    transfer_rows: list[dict[str, Any]] = []
    cfar_rows: list[dict[str, Any]] = []
    for method in (CURRENT, *J8_METHODS):
        raw_rows = transfer.transfer_rows_for_method(
            spec, method, roots[method], original["target_on"])
        enriched = transfer.enrich_transfer_rows(raw_rows, current_rows)
        for row in enriched:
            row["method"] = method
            row["support_percentile"] = support_percentile
            row["edge_guard_percentile"] = edge_guard_percentile
        transfer_rows.extend(enriched)
        for role in ("target_off", "target_on"):
            row = selective_v2.production_cfar_row(
                roots[method][role], spec["split"], spec["scene_id"], role, method)
            row["support_percentile"] = support_percentile
            row["edge_guard_percentile"] = edge_guard_percentile
            cfar_rows.append(row)
    current_metrics = summary_metrics(single_manifest(roots[CURRENT]["target_off"]))
    current_target_rows = [row for row in transfer_rows if row["method"] == CURRENT]
    current_detected_targets = {
        str(row["target_id"]) for row in current_target_rows if truthy(row.get("on_hit"))
    }
    causal_floor = finite(safety.get("causal_transfer_floor_db"), -0.25)
    pfa_delta_max = finite(safety.get("target_off_pfa_delta_max"), 0.0)
    cluster_delta_max = finite(
        safety.get("target_off_false_clusters_delta_max"), 0.0)
    preserve_current = truthy(
        safety.get("require_current_detected_targets_preserved", True))
    method_metrics: list[dict[str, Any]] = []
    for method in J8_METHODS:
        metrics = summary_metrics(single_manifest(roots[method]["target_off"]))
        rows = [row for row in transfer_rows if row["method"] == method]
        baseline = [row for row in transfer_rows if row["method"] == CURRENT]
        lcausal = np.asarray([finite(row.get("L_causal_dB")) for row in rows])
        lcausal = lcausal[np.isfinite(lcausal)]
        target_loss = np.asarray([finite(row.get("L_target_only_dB")) for row in rows])
        target_loss = target_loss[np.isfinite(target_loss)]
        off = [row for row in cfar_rows if row["method"] == method
               and row["role"] == "target_off"]
        current_off = [row for row in cfar_rows if row["method"] == CURRENT
                       and row["role"] == "target_off"]
        pfa = float(np.mean([finite(row.get("pfa")) for row in off]))
        current_pfa = float(np.mean([finite(row.get("pfa")) for row in current_off]))
        clusters = float(np.mean([finite(row.get("false_clusters")) for row in off]))
        current_clusters = float(np.mean(
            [finite(row.get("false_clusters")) for row in current_off]))
        candidate_detected_targets = {
            str(row["target_id"]) for row in rows if truthy(row.get("on_hit"))
        }
        lost_current_targets = sorted(
            current_detected_targets - candidate_detected_targets)
        causal_transfer_ok = bool(
            lcausal.size and float(np.min(lcausal)) >= causal_floor)
        target_preservation_ok = not preserve_current or not lost_current_targets
        pfa_ok = math.isfinite(pfa - current_pfa) and pfa - current_pfa <= pfa_delta_max
        clusters_ok = (math.isfinite(clusters - current_clusters)
                       and clusters - current_clusters <= cluster_delta_max)
        safety_failures = []
        if not causal_transfer_ok:
            safety_failures.append("causal_transfer_floor")
        if not target_preservation_ok:
            safety_failures.append("current_target_lost")
        if not pfa_ok:
            safety_failures.append("target_off_pfa_delta")
        if not clusters_ok:
            safety_failures.append("target_off_false_clusters_delta")
        method_metrics.append({
            "split": spec["split"], "scene_id": spec["scene_id"],
            "case_role": spec["j8_case_role"], "method": method,
            "cancellation_db": finite(metrics.get("cancellation_db")),
            "scnr_gain_db_vs_current": (
                finite(metrics.get("cancellation_db")) -
                finite(current_metrics.get("cancellation_db"))),
            "residual_p95_rad": finite(metrics.get("phase_p95_rad")),
            "L_causal_min_dB": float(np.min(lcausal)) if lcausal.size else math.nan,
            "L_target_only_min_dB": float(np.min(target_loss)) if target_loss.size else math.nan,
            "off_pfa_delta_vs_current": pfa - current_pfa,
            "off_false_clusters_delta_vs_current": clusters - current_clusters,
            "current_detected_target_count": len(current_detected_targets),
            "candidate_detected_target_count": len(candidate_detected_targets),
            "current_detected_targets_lost": len(lost_current_targets),
            "causal_transfer_ok": causal_transfer_ok,
            "target_preservation_ok": target_preservation_ok,
            "target_off_pfa_ok": pfa_ok,
            "target_off_false_clusters_ok": clusters_ok,
            "safe": not safety_failures,
            "safety_failures": ",".join(safety_failures),
            "causal_transfer_floor_db": causal_floor,
            "target_off_pfa_delta_max": pfa_delta_max,
            "target_off_false_clusters_delta_max": cluster_delta_max,
            "support_percentile": support_percentile,
            "edge_guard_percentile": edge_guard_percentile,
            "zero_correction_identity": all(
                item["method"] == method and item["steps"]
                for item in step_records if item["method"] == method),
        })
    compact = {
        "spec": spec,
        "status": scene["status"],
        "exact_pairing": "same deterministic background_input_dir for OFF/ON/TO",
        "plans": {method: transfer.plan_summary(plans[method]) for method in J8_METHODS},
        "step_records": step_records,
    }
    if scene_root.exists():
        shutil.rmtree(scene_root)
    return transfer_rows, cfar_rows, compact | {"method_metrics": method_metrics}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/j8_support_only_targeted")
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs/research/j8_support_only.json")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--formal-dir", type=Path,
                        default=ROOT / "outputs/physics_adaptive_selective_v2_formal")
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("ai_training") is not False:
        raise RuntimeError("J8 replay requires ai_training=false")
    formal_dir = args.formal_dir.resolve()
    gate_path = formal_dir / "null_gate.json"
    positive_gate_path = formal_dir / "positive_quality_gate.json"
    start = run_provenance_start(ROOT, (config_path, gate_path, positive_gate_path))
    if start["source_worktree_dirty_before"] is not False:
        raise SystemExit("J8 replay requires a clean source worktree at start")
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    base = formal.base_scenario()
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    positive_gate = json.loads(positive_gate_path.read_text(encoding="utf-8"))
    before = resource_snapshot()
    if not before["nvidia_smi"] or "NVIDIA-SMI has failed" in before["nvidia_smi"]:
        raise SystemExit("J8 replay requires a visible CUDA device")
    specs = make_specs()
    transfer_rows: list[dict[str, Any]] = []
    cfar_rows: list[dict[str, Any]] = []
    scene_records: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    for index, spec in enumerate(specs, 1):
        print(f"[j8] {index}/{len(specs)} {spec['scene_id']}", flush=True)
        tr, cfar, compact = run_one_scene(
            spec, base, output, args.build_dir.resolve(), gate, positive_gate,
            float(config["support_percentile"]),
            float(config["edge_guard_percentile"]),
            config["safety"])
        transfer_rows.extend(tr)
        cfar_rows.extend(cfar)
        scene_records.append(compact)
        metrics.extend(compact["method_metrics"])
    write_csv(output / "j8_transfer_rows.csv", transfer_rows)
    write_csv(output / "j8_cfar_rows.csv", cfar_rows)
    write_csv(output / "j8_method_metrics.csv", metrics)
    (output / "resource_snapshot_before.json").write_text(
        json.dumps(json_safe(before), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    after = resource_snapshot()
    (output / "resource_snapshot_after.json").write_text(
        json.dumps(json_safe(after), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    post = run_provenance_post(ROOT, start)
    (output / "provenance_start.json").write_text(
        json.dumps(json_safe(start), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (output / "provenance_post.json").write_text(
        json.dumps(json_safe(post), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "analysis": "J8 Clutter-Support-Only Calibration targeted replay",
        "source_commit": start["source_commit"],
        "source_worktree_dirty_before": start["source_worktree_dirty_before"],
        "provenance_start": start,
        "provenance_post": post,
        "ai_training": False,
        "design": {
            "scene_count": len(specs),
            "case_roles": [spec["j8_case_role"] for spec in specs],
            "methods": list(J8_METHODS),
            "support_domain": "pulse-frequency CSI cross-spectrum magnitude",
            "outside_support": "Current/un calibrated spectrum preserved",
            "edge": "percentile soft blend guard",
            "zero_correction": "source bytes copied unchanged",
        },
        "support_percentile": config["support_percentile"],
        "edge_guard_percentile": config["edge_guard_percentile"],
        "safety": config["safety"],
        "counts": {"scene_count": len(scene_records),
                   "transfer_rows": len(transfer_rows),
                   "cfar_rows": len(cfar_rows),
                   "method_metric_rows": len(metrics)},
        "outputs": {
            "transfer": "j8_transfer_rows.csv",
            "cfar": "j8_cfar_rows.csv",
            "metrics": "j8_method_metrics.csv",
        },
        "cleanup": {"raw_scene_cleanup": True,
                     "retained": "compact metrics, CFAR/transfer rows, plans, provenance"},
        "scene_records": scene_records,
    }
    (output / "j8_support_only_manifest.json").write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps(json_safe({"output_dir": str(output),
                                "scene_count": len(specs),
                                "ai_training": False}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
