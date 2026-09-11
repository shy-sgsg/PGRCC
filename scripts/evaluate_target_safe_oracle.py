#!/usr/bin/env python3
"""Evaluation-only Target-Safe Physics Oracle with frozen correction strength.

For each paired OFF/ON/TO scene, J5/J6 parameters are estimated from OFF once,
then the same frozen correction is replayed at lambda in {0,.25,.5,.75,1}.
Lambda scales the estimated delay and phase correction; it never changes the
estimator, gate, or calibration source. No truth is read by the estimator.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_target_transfer as transfer  # noqa: E402
import run_joint_physics_formal_matrix as formal  # noqa: E402
import run_joint_physics_selective_v2 as selective_v2  # noqa: E402
from experiment_provenance import git_provenance  # noqa: E402


METHODS = ("J0_Current", "J5_Selective_Physics_Calibration",
           "J6_Joint_Phase_Surface")
EXPERT_METHODS = METHODS[1:]
LAMBDAS = (0.0, 0.25, 0.5, 0.75, 1.0)
CAUSAL_TRANSFER_FLOOR_DB = -0.25


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


def finite(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


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


def scale_frozen_plan(plan: Mapping[str, Any], lambda_value: float) -> dict[str, Any]:
    """Scale only the frozen correction parameters, preserving gate decisions."""

    if lambda_value not in LAMBDAS:
        raise ValueError(f"unsupported lambda: {lambda_value}")
    scaled = copy.copy(dict(plan))
    scaled["lambda"] = float(lambda_value)
    if lambda_value == 0.0 or plan.get("fallback"):
        scaled["fallback"] = True
        scaled["apply_delay"] = False
        scaled["apply_phase"] = False
        scaled["phase_values"] = None
        return scaled
    scaled["fallback"] = False
    if plan.get("apply_delay"):
        scaled["estimated_delay_ns"] = (
            finite(plan.get("estimated_delay_ns"), 0.0) * lambda_value)
    if plan.get("apply_phase") and plan.get("phase_values") is not None:
        scaled["phase_values"] = np.asarray(plan["phase_values"],
                                             dtype=np.float64) * lambda_value
    return scaled


def make_specs() -> list[dict[str, Any]]:
    # Use the transition SNR pair and the audit seed namespace. This is an
    # evaluation-only paired replay of Phase B scenes, not a fit set.
    specs = transfer.design_specs("audit", (14.0, 16.0, 14.0, 16.0))[:12]
    output: list[dict[str, Any]] = []
    for index, source in enumerate(specs):
        spec = dict(source)
        spec["split"] = "target_safe_oracle"
        spec["scene_id"] = f"target_safe_oracle_{index:03d}"
        output.append(spec)
    return output


def variant_detection_rows(rows: list[dict[str, Any]],
                           method: str, lambda_value: float) -> list[dict[str, Any]]:
    fields = ("split", "scene_id", "family", "snr_db", "target_id",
              "on_hit", "off_hit", "paired_causal_hit",
              "on_cfar_margin_db", "off_cfar_margin_db",
              "delta_cfar_margin_db", "paired_hit_definition")
    return [{"method": method, "lambda": lambda_value,
             **{field: row.get(field) for field in fields}}
            for row in rows]


def run_one_scene(spec: dict[str, Any], base: dict[str, Any], output: Path,
                  build_dir: Path, gate: dict[str, Any],
                  positive_gate: dict[str, Any]) -> tuple[
                      list[dict[str, Any]], list[dict[str, Any]],
                      list[dict[str, Any]], dict[str, Any]]:
    scene = formal.run_scene(spec, base, output, build_dir, True)
    if scene.get("status") != "pass":
        raise RuntimeError(f"scene failed: {spec['scene_id']} {scene}")
    formal.enrich_scene(scene, output, spec)
    scene_root = Path(scene["scene_root"])
    original_roots = {
        role: Path(scene["roles"][role]["root"])
        for role in ("target_off", "target_on", "target_only")
    }
    on_manifest_path, on_manifest = transfer.load_manifest_row(
        original_roots["target_on"])
    on_map = transfer.load_complex_map(
        on_manifest_path, on_manifest["after_real_path"],
        on_manifest["after_imag_path"])
    centers = transfer.target_centers(
        original_roots["target_on"], on_manifest_path, on_manifest, on_map.shape)
    plans = {
        method: transfer.make_frozen_plan(
            method, original_roots["target_off"], gate, positive_gate,
            scene_root / "_oracle_plans")
        for method in EXPERT_METHODS
    }
    current_raw = transfer.transfer_rows_for_method(
        spec, "J0_Current", original_roots, original_roots["target_on"])
    current_rows = {row["target_id"]: row for row in current_raw}
    transfer_rows: list[dict[str, Any]] = []
    detection_rows: list[dict[str, Any]] = []
    cfar_rows: list[dict[str, Any]] = []
    plan_records: list[dict[str, Any]] = []

    def emit_variant(method: str, lambda_value: float,
                     roots: dict[str, Path]) -> None:
        raw = transfer.transfer_rows_for_method(
            spec, method, roots, original_roots["target_on"])
        enriched = transfer.enrich_transfer_rows(raw, current_rows)
        for row in enriched:
            row["lambda"] = lambda_value
        transfer_rows.extend(enriched)
        detection_rows.extend(variant_detection_rows(enriched, method,
                                                      lambda_value))
        for role in ("target_off", "target_on"):
            row = selective_v2.production_cfar_row(
                roots[role], spec["split"], spec["scene_id"], role, method)
            row["lambda"] = lambda_value
            cfar_rows.append(row)

    # Current and lambda=0 are the same identity replay. Keep one baseline row
    # and one lambda=0 row for each expert so the identity assertion is visible.
    emit_variant("J0_Current", 0.0, original_roots)
    for method in EXPERT_METHODS:
        for lambda_value in LAMBDAS:
            scaled = scale_frozen_plan(plans[method], lambda_value)
            roots: dict[str, Path] = {}
            if lambda_value == 0.0 or plans[method].get("fallback"):
                roots = dict(original_roots)
            else:
                for role in ("target_off", "target_on", "target_only"):
                    roots[role] = Path(transfer.run_frozen_method(
                        original_roots[role], scaled,
                        scene_root / "oracle" / method / f"lambda_{lambda_value:g}" / role,
                        build_dir)["root"])
            emit_variant(method, lambda_value, roots)
        plan_records.append({
            "method": method,
            "source_role": "target_off",
            "branch": plans[method].get("branch"),
            "apply_delay": plans[method].get("apply_delay"),
            "apply_phase": plans[method].get("apply_phase"),
            "estimated_delay_ns": plans[method].get("estimated_delay_ns"),
            "estimated_phase_slope_deg_per_pulse": plans[method].get(
                "estimated_phase_slope_deg_per_pulse"),
            "estimated_beta2_deg_per_pulse2": plans[method].get(
                "estimated_beta2_deg_per_pulse2"),
            "truth_used_in_estimator": False,
            "lambda_values": list(LAMBDAS),
        })
    compact = {
        "spec": spec,
        "status": scene["status"],
        "exact_pairing": "same deterministic background_input_dir for OFF/ON/TO",
        "plans": plan_records,
    }
    if scene_root.exists():
        shutil.rmtree(scene_root)
    return transfer_rows, detection_rows, cfar_rows, compact


def group(rows: list[dict[str, Any]], method: str, lambda_value: float,
          key: str | None = None, value: Any = None) -> list[dict[str, Any]]:
    output = [row for row in rows if row.get("method") == method
              and math.isclose(finite(row.get("lambda")), lambda_value)]
    if key is not None:
        output = [row for row in output if row.get(key) == value]
    return output


def finite_values(rows: list[dict[str, Any]], field: str) -> np.ndarray:
    values = np.asarray([finite(row.get(field)) for row in rows], dtype=float)
    return values[np.isfinite(values)]


def summary_row(transfer_rows: list[dict[str, Any]],
                detection_rows: list[dict[str, Any]],
                cfar_rows: list[dict[str, Any]], method: str,
                lambda_value: float) -> dict[str, Any]:
    tr = group(transfer_rows, method, lambda_value)
    det = group(detection_rows, method, lambda_value)
    off = [row for row in group(cfar_rows, method, lambda_value)
           if row.get("role") == "target_off"]
    lcausal = finite_values(tr, "L_causal_dB")
    ltarget = finite_values(tr, "L_target_only_dB")
    pfa = finite_values(off, "pfa")
    clusters = finite_values(off, "false_clusters")
    def mean_or_nan(values: np.ndarray) -> float:
        return float(np.mean(values)) if values.size else math.nan
    def percentile_or_nan(values: np.ndarray, q: float) -> float:
        return float(np.percentile(values, q)) if values.size else math.nan
    return {
        "method": method,
        "lambda": lambda_value,
        "target_count": len(tr),
        "L_causal_mean_dB": mean_or_nan(lcausal),
        "L_causal_median_dB": float(np.median(lcausal)) if lcausal.size else math.nan,
        "L_causal_p05_dB": percentile_or_nan(lcausal, 5),
        "L_causal_min_dB": float(np.min(lcausal)) if lcausal.size else math.nan,
        "L_target_only_mean_dB": mean_or_nan(ltarget),
        "L_target_only_p05_dB": percentile_or_nan(ltarget, 5),
        "paired_hit_count": sum(bool(row.get("paired_causal_hit")) for row in det),
        "paired_causal_pd": (
            float(np.mean([bool(row.get("paired_causal_hit")) for row in det]))
            if det else math.nan),
        "on_hit_count": sum(bool(row.get("on_hit")) for row in det),
        "off_hit_count": sum(bool(row.get("off_hit")) for row in det),
        "off_pfa_mean": mean_or_nan(pfa),
        "off_false_clusters_mean": mean_or_nan(clusters),
    }


def constraint_rows(summary: list[dict[str, Any]],
                    detection_rows: list[dict[str, Any]],
                    cfar_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current = next(row for row in summary
                   if row["method"] == "J0_Current")
    current_det = group(detection_rows, "J0_Current", 0.0)
    current_by_snr: dict[str, float] = {}
    for snr in sorted({str(row.get("snr_db")) for row in current_det}):
        rows = [row for row in current_det if str(row.get("snr_db")) == snr]
        current_by_snr[snr] = float(np.mean(
            [bool(row.get("paired_causal_hit")) for row in rows]))
    current_off = [row for row in group(cfar_rows, "J0_Current", 0.0)
                   if row.get("role") == "target_off"]
    current_pfa = float(np.mean(finite_values(current_off, "pfa")))
    current_clusters = float(np.mean(
        finite_values(current_off, "false_clusters")))
    output = []
    for row in summary:
        if row["method"] not in EXPERT_METHODS:
            continue
        det = group(detection_rows, row["method"], row["lambda"])
        candidate_by_snr = {
            snr: float(np.mean([bool(item.get("paired_causal_hit"))
                               for item in det if str(item.get("snr_db")) == snr]))
            for snr in current_by_snr
        }
        transfer_floor = row["L_causal_min_dB"] >= CAUSAL_TRANSFER_FLOOR_DB
        pd_overall = row["paired_causal_pd"] >= current["paired_causal_pd"]
        pd_by_snr = all(candidate_by_snr[snr] >= current_by_snr[snr]
                         for snr in current_by_snr)
        pfa_delta = row["off_pfa_mean"] - current_pfa
        cluster_delta = row["off_false_clusters_mean"] - current_clusters
        pfa_ok = pfa_delta <= 0.0
        clusters_ok = cluster_delta <= 0.0
        output.append({
            "method": row["method"],
            "lambda": row["lambda"],
            "causal_transfer_floor_db": CAUSAL_TRANSFER_FLOOR_DB,
            "L_causal_min_dB": row["L_causal_min_dB"],
            "causal_transfer_ok": transfer_floor,
            "paired_pd": row["paired_causal_pd"],
            "current_paired_pd": current["paired_causal_pd"],
            "paired_pd_no_loss_overall": pd_overall,
            "paired_pd_no_loss_by_snr": pd_by_snr,
            "off_pfa_delta_vs_current": pfa_delta,
            "off_false_clusters_delta_vs_current": cluster_delta,
            "pfa_acceptable": pfa_ok,
            "false_clusters_acceptable": clusters_ok,
            "safe": bool(transfer_floor and pd_overall and pd_by_snr
                          and pfa_ok and clusters_ok),
        })
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/target_safe_oracle")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--formal-dir", type=Path,
                        default=ROOT / "outputs/physics_adaptive_selective_v2_formal")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    formal_dir = args.formal_dir.resolve()
    gate = json.loads((formal_dir / "null_gate.json").read_text(encoding="utf-8"))
    positive_gate = json.loads(
        (formal_dir / "positive_quality_gate.json").read_text(encoding="utf-8"))
    base = formal.base_scenario()
    specs = make_specs()
    before = resource_snapshot()
    transfer_rows: list[dict[str, Any]] = []
    detection_rows: list[dict[str, Any]] = []
    cfar_rows: list[dict[str, Any]] = []
    scene_records: list[dict[str, Any]] = []
    for index, spec in enumerate(specs, 1):
        print(f"[target-safe-oracle] {index}/{len(specs)} {spec['scene_id']}",
              flush=True)
        tr, det, cfar, compact = run_one_scene(
            spec, base, output, args.build_dir.resolve(), gate, positive_gate)
        transfer_rows.extend(tr)
        detection_rows.extend(det)
        cfar_rows.extend(cfar)
        scene_records.append(compact)

    write_csv(output / "oracle_transfer_rows.csv", transfer_rows)
    write_csv(output / "oracle_detection_rows.csv", detection_rows)
    write_csv(output / "oracle_cfar_rows.csv", cfar_rows)
    summary = [
        summary_row(transfer_rows, detection_rows, cfar_rows, "J0_Current", 0.0),
        *[summary_row(transfer_rows, detection_rows, cfar_rows, method, lambda_value)
          for method in EXPERT_METHODS for lambda_value in LAMBDAS],
    ]
    constraints = constraint_rows(summary, detection_rows, cfar_rows)
    write_csv(output / "oracle_summary.csv", summary)
    write_csv(output / "oracle_constraints.csv", constraints)
    safe_lambdas = {
        method: [row["lambda"] for row in constraints
                 if row["method"] == method and row["safe"]]
        for method in EXPERT_METHODS
    }
    best_safe = {
        method: (max(values) if values else None)
        for method, values in safe_lambdas.items()
    }
    provenance = git_provenance(ROOT)
    manifest = {
        "schema_version": 1,
        "analysis": "Target-Safe Physics Oracle",
        **provenance,
        "ai_training": False,
        "evaluation_only": True,
        "formal_source_commit": json.loads(
            (formal_dir / "formal_matrix_manifest.json").read_text(
                encoding="utf-8")).get("source_commit"),
        "design": {
            "scene_count": len(specs),
            "snr_values_db": [14.0, 16.0],
            "families": list(transfer.FAMILIES),
            "seed_namespace": "2026111000 audit replay",
            "roles": {"OFF": "C+N", "ON": "S+C+N", "TO": "S-only"},
            "same_seed_geometry_mismatch": True,
            "primary_calibration_source": "target_off",
            "not_used_for_threshold_fit": True,
        },
        "lambda_values": list(LAMBDAS),
        "lambda_definition": (
            "scale frozen OFF-derived estimated delay and phase correction; "
            "lambda=0 is Current identity and lambda=1 is full frozen replay"),
        "constraints": {
            "causal_transfer_floor_db": CAUSAL_TRANSFER_FLOOR_DB,
            "paired_causal_pd": "no loss overall and at every SNR point",
            "pfa": "target-off mean delta <= 0 versus Current",
            "false_clusters": "target-off mean delta <= 0 versus Current",
        },
        "identity_checks": {
            "lambda_zero_equals_current": True,
            "lambda_one_equals_full_frozen_plan": True,
        },
        "safe_lambdas": safe_lambdas,
        "best_safe_lambda": best_safe,
        "counts": {
            "scene_count": len(scene_records),
            "transfer_rows": len(transfer_rows),
            "detection_rows": len(detection_rows),
            "cfar_rows": len(cfar_rows),
        },
        "outputs": {
            "oracle_transfer_rows": "oracle_transfer_rows.csv",
            "oracle_detection_rows": "oracle_detection_rows.csv",
            "oracle_cfar_rows": "oracle_cfar_rows.csv",
            "oracle_summary": "oracle_summary.csv",
            "oracle_constraints": "oracle_constraints.csv",
        },
        "cleanup": {
            "raw_scene_cleanup": True,
            "retained": "compact transfer, detection, CFAR, summary, constraints, manifest, resource snapshots",
        },
        "scene_records": scene_records,
    }
    (output / "resource_snapshot_before.json").write_text(
        json.dumps(json_safe(before), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (output / "resource_snapshot_after.json").write_text(
        json.dumps(json_safe(resource_snapshot()), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (output / "target_safe_oracle_manifest.json").write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps(json_safe({
        "output_dir": str(output),
        "scene_count": len(scene_records),
        "best_safe_lambda": best_safe,
        "ai_training": False,
    }), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
