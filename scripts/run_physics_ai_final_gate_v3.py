#!/usr/bin/env python3
"""Run the frozen Test-V3 physics-only final-gate matrix.

The runner uses fresh 2026120000 seeds, exact OFF/ON/TO pairing, OFF-derived
frozen J5/J6 correction, and a pre-registered deterministic J7 selector. No
AI training or learned parameter fitting is performed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_target_transfer as transfer  # noqa: E402
import run_joint_physics_formal_matrix as formal  # noqa: E402
import run_joint_physics_calibration as calibration  # noqa: E402
import run_joint_physics_selective_v2 as selective_v2  # noqa: E402
from experiment_provenance import (  # noqa: E402
    run_provenance_post,
    run_provenance_start,
)
from safe_expert_selector import (  # noqa: E402
    COMPLEXITY_FEATURE_NAMES,
    ComplexitySelectorThresholds,
    FEATURE_NAMES,
    J5,
    J6,
    SELECTOR_PROFILES,
    extract_inference_features,
    select_complexity_aware_expert_with_reason,
    select_safe_expert_with_reason,
)


CURRENT = "J0_Current"
J7 = "J7_Deterministic_Safe_Expert_Selector"
ORACLE = "Oracle_Safe_Lambda_0_EvaluationOnly"
EXPERT_METHODS = (J5, J6)
V3_FAMILIES = ("zero", "M1", "M2", "M3", "M1+M2", "M1+M3",
               "M2+M3", "M1+M2+M3")
V3_SNR = (10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0)
V3_SEED_BASE = 2026120000


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
        "nvidia_smi": run(["nvidia-smi"]),
        "free_h": run(["free", "-h"]),
        "df_h_workspace": run(["df", "-h", str(ROOT)]),
    }


def design_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for level, snr_db in enumerate(V3_SNR):
        for family_index, label in enumerate(V3_FAMILIES):
            index = level * len(V3_FAMILIES) + family_index
            mechanisms = set() if label == "zero" else set(label.split("+"))
            desired_radial = (-18.0, -8.0, 0.0, 8.0, 18.0,
                              -14.0, 4.0, 14.0)[family_index]
            tangent = (-12.0, -4.0, 5.0, 11.0, 2.0, -8.0, 7.0, 13.0)[level]
            theta = math.radians(-55.0 + 4.0 * family_index)
            look = np.asarray([-math.sin(theta), -math.cos(theta)])
            orthogonal = np.asarray([-look[1], look[0]])
            velocity = desired_radial * look + tangent * orthogonal
            specs.append({
                "scene_id": f"test_v3_{index:03d}",
                "split": "test_v3",
                "label": label,
                "seed": V3_SEED_BASE + index,
                "squint_deg": float(-0.32 + 0.08 * family_index),
                "scan_min_deg": float(-20.0 + 5.0 * family_index),
                "look_angle_deg": float(-55.0 + 4.0 * family_index),
                "range_bin": int(350 + (index * 173) % 1200),
                "snr_db": float(snr_db),
                "ve_mps": float(velocity[0]),
                "vn_mps": float(velocity[1]),
                "texture_sigma": float(0.25 + 0.05 * ((index + level) % 8)),
                "rho": (float(0.55 + 0.04 * (level % 5))
                        if "M3" in mechanisms else 1.0),
                "delay_ns": (float(4.0 + 0.8 * (level % 5))
                             if "M1" in mechanisms else 0.0),
                "phase_deg": (float(0.20 + 0.05 * (level % 5))
                              if "M2" in mechanisms else 0.0),
                "multi_target": False,
                "near_clutter_ridge": bool(index % 5 == 0),
                "high_speed_target": bool(abs(desired_radial) > 15.0),
                "include_target_only": True,
            })
    return specs


def selector_features_for_root(root: Path) -> dict[str, float]:
    _, _, initial = calibration.case_observables(
        root, 1300.0, 80.0, include_selective_surface=True)
    return extract_inference_features(initial["summary"])


def method_roots_for_scene(
        spec: dict[str, Any], base: dict[str, Any], output: Path,
        build_dir: Path, gate: dict[str, Any], positive_gate: dict[str, Any],
        thresholds: Any,
        complexity_thresholds: ComplexitySelectorThresholds | None = None,
) -> tuple[dict[str, dict[str, Path]], dict[str, Any], dict[str, float], dict[str, str], dict[str, Any]]:
    scene = formal.run_scene(spec, base, output, build_dir, True)
    if scene.get("status") != "pass":
        raise RuntimeError(f"scene failed: {spec['scene_id']} {scene}")
    formal.enrich_scene(scene, output, spec)
    scene_root = Path(scene["scene_root"])
    original = {
        role: Path(scene["roles"][role]["root"])
        for role in ("target_off", "target_on", "target_only")
    }
    plans: dict[str, dict[str, Any]] = {}
    roots: dict[str, dict[str, Path]] = {CURRENT: dict(original)}
    for method in EXPERT_METHODS:
        plans[method] = transfer.make_frozen_plan(
            method, original["target_off"], gate, positive_gate,
            scene_root / "_plans")
        roots[method] = {}
        for role in ("target_off", "target_on", "target_only"):
            roots[method][role] = Path(transfer.run_frozen_method(
                original[role], plans[method],
                scene_root / "frozen" / method / role, build_dir)["root"])
    features = selector_features_for_root(original["target_off"])
    if complexity_thresholds is None:
        selected, reason = select_safe_expert_with_reason(features, thresholds)
    else:
        selected, reason = select_complexity_aware_expert_with_reason(
            features, thresholds, complexity_thresholds)
    roots[J7] = dict(roots.get(selected, roots[CURRENT]))
    roots[ORACLE] = dict(original)
    selector_row = {
        "split": spec["split"],
        "scene_id": spec["scene_id"],
        "selected_method": selected,
        "selection_reason": reason,
        **features,
    }
    compact = {
        "spec": spec,
        "status": scene["status"],
        "exact_pairing": "same deterministic background_input_dir for OFF/ON/TO",
        "j5_branch": plans[J5].get("branch"),
        "j6_branch": plans[J6].get("branch"),
        "j7_selected_method": selected,
        "j7_selection_reason": reason,
        "oracle_lambda": 0.0,
    }
    return roots, plans, features, selector_row, compact


def build_rows(spec: dict[str, Any], roots: dict[str, dict[str, Path]],
               output_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    current_raw = transfer.transfer_rows_for_method(
        spec, CURRENT, roots[CURRENT], roots[CURRENT]["target_on"])
    current_rows = {row["target_id"]: row for row in current_raw}
    transfer_rows: list[dict[str, Any]] = []
    detection_rows: list[dict[str, Any]] = []
    cfar_rows: list[dict[str, Any]] = []
    methods = (CURRENT, J5, J6, J7, ORACLE)
    for method in methods:
        raw = transfer.transfer_rows_for_method(
            spec, method, roots[method], roots[CURRENT]["target_on"])
        enriched = transfer.enrich_transfer_rows(raw, current_rows)
        for row in enriched:
            row["method"] = method
            row["oracle_lambda"] = 0.0 if method == ORACLE else None
            row["j7_routing"] = method == J7
        transfer_rows.extend(enriched)
        for row in enriched:
            detection_rows.append({
                "split": spec["split"], "scene_id": spec["scene_id"],
                "family": spec["label"], "snr_db": spec["snr_db"],
                "method": method, "target_id": row["target_id"],
                "on_hit": row["on_hit"], "off_hit": row["off_hit"],
                "paired_causal_hit": row["paired_causal_hit"],
                "on_cfar_margin_db": row["on_cfar_margin_db"],
                "off_cfar_margin_db": row["off_cfar_margin_db"],
                "delta_cfar_margin_db": row["delta_cfar_margin_db"],
                "paired_hit_definition": "on_hit AND NOT off_hit",
            })
        for role in ("target_off", "target_on"):
            row = selective_v2.production_cfar_row(
                roots[method][role], spec["split"], spec["scene_id"], role, method)
            row["oracle_lambda"] = 0.0 if method == ORACLE else None
            cfar_rows.append(row)
    if output_root.exists():
        shutil.rmtree(output_root)
    return transfer_rows, detection_rows, cfar_rows


def stats(values: list[Any]) -> dict[str, Any]:
    data = np.asarray([finite(value) for value in values], dtype=float)
    data = data[np.isfinite(data)]
    if not data.size:
        return {"count": 0, "mean": None, "median": None, "p05": None,
                "min": None}
    return {
        "count": int(data.size), "mean": float(np.mean(data)),
        "median": float(np.median(data)), "p05": float(np.percentile(data, 5)),
        "min": float(np.min(data)),
    }


def summary_rows(transfer_rows: list[dict[str, Any]],
                 detection_rows: list[dict[str, Any]],
                 cfar_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    methods = (CURRENT, J5, J6, J7, ORACLE)
    rows: list[dict[str, Any]] = []
    for method in methods:
        tr = [row for row in transfer_rows if row["method"] == method]
        det = [row for row in detection_rows if row["method"] == method]
        off = [row for row in cfar_rows if row["method"] == method
               and row["role"] == "target_off"]
        causal = stats([row["L_causal_dB"] for row in tr])
        target_only = stats([row["L_target_only_dB"] for row in tr])
        rows.append({
            "method": method,
            "target_count": len(tr),
            **{f"L_causal_{key}": value for key, value in causal.items()},
            **{f"L_target_only_{key}": value for key, value in target_only.items()},
            "paired_hit_count": sum(bool(row["paired_causal_hit"]) for row in det),
            "paired_causal_pd": (float(np.mean([bool(row["paired_causal_hit"])
                                                  for row in det]))
                                  if det else math.nan),
            "on_hit_count": sum(bool(row["on_hit"]) for row in det),
            "off_hit_count": sum(bool(row["off_hit"]) for row in det),
            "target_off_pfa_mean": float(np.mean(
                [finite(row.get("pfa")) for row in off])) if off else math.nan,
            "target_off_false_clusters_mean": float(np.mean(
                [finite(row.get("false_clusters")) for row in off])) if off else math.nan,
        })
    return rows


def cfar_delta_rows(cfar_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    baseline = {(row["scene_id"], row["role"]): row
                for row in cfar_rows if row["method"] == CURRENT}
    rows: list[dict[str, Any]] = []
    for method in (J5, J6, J7, ORACLE):
        for row in cfar_rows:
            if row["method"] != method or row["role"] != "target_off":
                continue
            base = baseline[(row["scene_id"], row["role"])]
            rows.append({
                "scene_id": row["scene_id"], "method": method,
                "pfa_delta_vs_current": finite(row.get("pfa")) - finite(base.get("pfa")),
                "false_clusters_delta_vs_current": (
                    finite(row.get("false_clusters")) - finite(base.get("false_clusters"))),
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs/research/physics_ai_final_gate_v3.json")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/physics_ai_final_gate_v3")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--formal-dir", type=Path,
                        default=ROOT / "outputs/physics_adaptive_selective_v2_formal")
    args = parser.parse_args()
    config = json.loads(args.config.resolve().read_text(encoding="utf-8"))
    if config.get("ai_training") is not False:
        raise RuntimeError("Test-V3 requires ai_training=false")
    formal_dir = args.formal_dir.resolve()
    gate_path = formal_dir / "null_gate.json"
    positive_gate_path = formal_dir / "positive_quality_gate.json"
    provenance_start = run_provenance_start(
        ROOT, (args.config.resolve(), gate_path, positive_gate_path))
    if provenance_start["source_worktree_dirty_before"] is not False:
        raise SystemExit("Test-V3 requires a clean source worktree at start")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    positive_gate = json.loads(positive_gate_path.read_text(encoding="utf-8"))
    base = formal.base_scenario()
    specs = design_specs()
    thresholds = SELECTOR_PROFILES[config["j7_selector"]["profile"]]
    complexity_config = config["j7_1_selector"]
    complexity_thresholds = ComplexitySelectorThresholds(
        beta2_abs_min_deg_per_pulse2=float(
            complexity_config["beta2_abs_min_deg_per_pulse2"]),
        joint_vs_p1_residual_coherence_gain_min=float(
            complexity_config["joint_vs_p1_residual_coherence_gain_min"]),
    )
    before = resource_snapshot()
    all_transfer: list[dict[str, Any]] = []
    all_detection: list[dict[str, Any]] = []
    all_cfar: list[dict[str, Any]] = []
    selector_rows: list[dict[str, Any]] = []
    scene_records: list[dict[str, Any]] = []
    for index, spec in enumerate(specs, 1):
        print(f"[test-v3] {index}/{len(specs)} {spec['scene_id']}", flush=True)
        scene_output = output / "scenes" / spec["scene_id"]
        roots, plans, features, selector_row, compact = method_roots_for_scene(
            spec, base, scene_output, args.build_dir.resolve(), gate, positive_gate,
            thresholds, complexity_thresholds)
        transfer_rows, detection_rows, cfar_rows = build_rows(
            spec, roots, scene_output)
        all_transfer.extend(transfer_rows)
        all_detection.extend(detection_rows)
        all_cfar.extend(cfar_rows)
        selector_rows.append(selector_row)
        scene_records.append(compact)

    write_csv(output / "v3_transfer_rows.csv", all_transfer)
    write_csv(output / "v3_detection_rows.csv", all_detection)
    write_csv(output / "v3_cfar_rows.csv", all_cfar)
    write_csv(output / "v3_summary.csv",
              summary_rows(all_transfer, all_detection, all_cfar))
    write_csv(output / "v3_cfar_delta_vs_current.csv", cfar_delta_rows(all_cfar))
    write_csv(output / "v3_inference_visible_features.csv", selector_rows)
    write_csv(output / "v3_scene_summary.csv", [
        {
            "split": item["spec"]["split"],
            "scene_id": item["spec"]["scene_id"],
            "family": item["spec"]["label"],
            "snr_db": item["spec"]["snr_db"],
            "seed": item["spec"]["seed"],
            "status": item["status"],
            "j5_branch": item["j5_branch"],
            "j6_branch": item["j6_branch"],
            "j7_selected_method": item["j7_selected_method"],
            "j7_selection_reason": item["j7_selection_reason"],
            "oracle_lambda": item["oracle_lambda"],
        }
        for item in scene_records
    ])
    summary = summary_rows(all_transfer, all_detection, all_cfar)
    provenance_post = run_provenance_post(ROOT, provenance_start)
    manifest = {
        "schema_version": 1,
        "analysis": "Physics-AI Final Gate Test-V3",
        "source_commit": provenance_start["source_commit"],
        "source_worktree_dirty_before": provenance_start[
            "source_worktree_dirty_before"],
        "worktree_dirty": provenance_post["source_worktree_dirty_after"],
        "provenance_start": provenance_start,
        "provenance_post": provenance_post,
        "ai_training": False,
        "config_path": str(args.config.resolve()),
        "config": config,
        "formal_source_commit": json.loads(
            (formal_dir / "formal_matrix_manifest.json").read_text(
                encoding="utf-8")).get("source_commit"),
        "design": {
            "scene_count": len(specs),
            "families": list(V3_FAMILIES),
            "snr_db": list(V3_SNR),
            "seed_min": min(spec["seed"] for spec in specs),
            "seed_max": max(spec["seed"] for spec in specs),
            "no_v2_seed_reuse": True,
            "roles": {"OFF": "C+N", "ON": "S+C+N", "TO": "S-only"},
            "same_background_for_paired_roles": True,
        },
        "methods": [CURRENT, J5, J6, J7, ORACLE],
        "selector": {
            "profile": config["j7_selector"]["profile"],
            "variant": config["j7_selector"].get("variant", "J7"),
            "feature_names": list(FEATURE_NAMES) + list(COMPLEXITY_FEATURE_NAMES),
            "truth_fields_in_input": False,
            "threshold_source": "Phase-C frozen calibration+validation",
            "complexity_thresholds": config.get("j7_1_selector"),
        },
        "oracle": {
            "evaluation_only": True,
            "safe_lambda": 0.0,
            "reason": "Phase-D all nonzero lambda values failed Target-Safe constraints",
        },
        "counts": {
            "scene_count": len(scene_records),
            "transfer_rows": len(all_transfer),
            "detection_rows": len(all_detection),
            "cfar_rows": len(all_cfar),
            "selector_rows": len(selector_rows),
        },
        "outputs": {
            "summary": "v3_summary.csv",
            "transfer": "v3_transfer_rows.csv",
            "detection": "v3_detection_rows.csv",
            "cfar": "v3_cfar_rows.csv",
            "cfar_delta": "v3_cfar_delta_vs_current.csv",
            "selector": "v3_inference_visible_features.csv",
            "scene_summary": "v3_scene_summary.csv",
        },
        "cleanup": {
            "raw_scene_cleanup": True,
            "retained": "compact Test-V3 evidence, manifest, resources",
        },
        "summary": summary,
        "scene_records": scene_records,
    }
    (output / "resource_snapshot_before.json").write_text(
        json.dumps(json_safe(before), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (output / "resource_snapshot_after.json").write_text(
        json.dumps(json_safe(resource_snapshot()), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (output / "provenance_start.json").write_text(
        json.dumps(json_safe(provenance_start), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (output / "provenance_post.json").write_text(
        json.dumps(json_safe(provenance_post), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (output / "physics_ai_final_gate_v3_manifest.json").write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps(json_safe({
        "output_dir": str(output),
        "scene_count": len(scene_records),
        "transfer_rows": len(all_transfer),
        "detection_rows": len(all_detection),
        "cfar_rows": len(all_cfar),
        "ai_training": False,
    }), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
