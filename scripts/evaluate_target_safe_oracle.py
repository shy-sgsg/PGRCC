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
from experiment_provenance import (  # noqa: E402
    git_provenance,
    run_provenance_post,
    run_provenance_start,
)


METHODS = ("J0_Current", "J5_Selective_Physics_Calibration",
           "J6_Joint_Phase_Surface")
EXPERT_METHODS = METHODS[1:]
LAMBDAS = (0.0, 0.25, 0.5, 0.75, 1.0)
EXPERT_LAMBDAS = (0.25, 0.5, 0.75, 1.0)


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


def select_safe_candidate(candidate_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Select the highest-cancellation safe candidate for one scene."""
    safe_candidates = [row for row in candidate_rows if bool(row.get("safe"))]
    safe_candidates.sort(
        key=lambda row: (finite(row.get("cancellation_db"), -math.inf),
                         -float(row.get("lambda", 0.0))), reverse=True)
    if safe_candidates:
        return safe_candidates[0]
    current = [row for row in candidate_rows if row.get("method") == "J0_Current"]
    if len(current) != 1:
        raise ValueError("one Current identity candidate is required")
    return current[0]


def run_one_scene(spec: dict[str, Any], base: dict[str, Any], output: Path,
                  build_dir: Path, gate: dict[str, Any],
                  positive_gate: dict[str, Any],
                  safety: Mapping[str, Any]) -> tuple[
                      list[dict[str, Any]], list[dict[str, Any]],
                      list[dict[str, Any]], list[dict[str, Any]],
                      dict[str, Any]]:
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
    metrics_by_candidate: dict[tuple[str, float], dict[str, Any]] = {}
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
        off_manifest = transfer.single_manifest(roots["target_off"])
        metrics = transfer.summary_metrics(off_manifest)
        metrics_by_candidate[(method, float(lambda_value))] = {
            "cancellation_db": finite(metrics.get("cancellation_db")),
            "residual_p95_rad": finite(metrics.get("phase_p95_rad")),
            "coherence": finite(metrics.get("coherence")),
            "phase_rmse_rad": finite(metrics.get("phase_rmse_rad")),
        }

    # Current and lambda=0 are the same identity replay. Keep one baseline row
    # and one lambda=0 row for each expert so the identity assertion is visible.
    emit_variant("J0_Current", 0.0, original_roots)
    for method in EXPERT_METHODS:
        for lambda_value in EXPERT_LAMBDAS:
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
            "lambda_values": list(EXPERT_LAMBDAS),
        })
    current_metrics = metrics_by_candidate[("J0_Current", 0.0)]
    current_transfer = group(transfer_rows, "J0_Current", 0.0)
    current_detection = group(detection_rows, "J0_Current", 0.0)
    current_off = [row for row in group(cfar_rows, "J0_Current", 0.0)
                   if row.get("role") == "target_off"]
    current_on_by_target = {
        str(row.get("target_id")): bool(row.get("on_hit"))
        for row in current_detection
    }
    current_pfa = finite(np.mean(finite_values(current_off, "pfa")))
    current_clusters = finite(np.mean(
        finite_values(current_off, "false_clusters")))
    candidate_rows: list[dict[str, Any]] = []
    candidates = [("J0_Current", 0.0),
                  *[(method, lambda_value) for method in EXPERT_METHODS
                    for lambda_value in EXPERT_LAMBDAS]]
    for method, lambda_value in candidates:
        candidate_transfer = group(transfer_rows, method, lambda_value)
        candidate_detection = group(detection_rows, method, lambda_value)
        candidate_off = [row for row in group(cfar_rows, method, lambda_value)
                         if row.get("role") == "target_off"]
        candidate_metrics = metrics_by_candidate[(method, float(lambda_value))]
        lcausal = finite_values(candidate_transfer, "L_causal_dB")
        target_count = len(candidate_transfer)
        current_detected_lost = sum(
            current_hit and not bool(next(
                (row.get("on_hit") for row in candidate_detection
                 if str(row.get("target_id")) == target_id), False))
            for target_id, current_hit in current_on_by_target.items())
        pfa = finite(np.mean(finite_values(candidate_off, "pfa")))
        clusters = finite(np.mean(
            finite_values(candidate_off, "false_clusters")))
        pfa_delta = pfa - current_pfa
        clusters_delta = clusters - current_clusters
        causal_ok = bool(lcausal.size and
                         float(np.min(lcausal)) >= float(
                             safety["causal_transfer_floor_db"]))
        target_preservation_ok = (
            not bool(safety["require_current_detected_targets_preserved"])
            or current_detected_lost == 0)
        pfa_ok = (math.isfinite(pfa_delta) and
                  pfa_delta <= float(safety["target_off_pfa_delta_max"]))
        clusters_ok = (math.isfinite(clusters_delta) and
                       clusters_delta <= float(
                           safety["target_off_false_clusters_delta_max"]))
        safe = bool(causal_ok and target_preservation_ok and pfa_ok and clusters_ok)
        cancellation = candidate_metrics["cancellation_db"]
        current_cancellation = current_metrics["cancellation_db"]
        scnr_gain = (cancellation - current_cancellation
                     if math.isfinite(cancellation)
                     and math.isfinite(current_cancellation) else math.nan)
        candidate_rows.append({
            "split": spec["split"], "scene_id": spec["scene_id"],
            "family": spec["label"], "snr_db": spec["snr_db"],
            "method": method, "lambda": lambda_value,
            "cancellation_db": cancellation,
            "scnr_gain_db_vs_current": scnr_gain,
            "residual_p95_rad": candidate_metrics["residual_p95_rad"],
            "coherence": candidate_metrics["coherence"],
            "phase_rmse_rad": candidate_metrics["phase_rmse_rad"],
            "L_causal_min_dB": (float(np.min(lcausal))
                                 if lcausal.size else math.nan),
            "L_causal_mean_dB": (float(np.mean(lcausal))
                                  if lcausal.size else math.nan),
            "paired_hit_count": sum(bool(row.get("paired_causal_hit"))
                                   for row in candidate_detection),
            "on_hit_count": sum(bool(row.get("on_hit"))
                                for row in candidate_detection),
            "off_hit_count": sum(bool(row.get("off_hit"))
                                 for row in candidate_detection),
            "target_count": target_count,
            "current_detected_targets_lost": current_detected_lost,
            "target_preservation_ok": target_preservation_ok,
            "off_pfa": pfa,
            "off_pfa_delta_vs_current": pfa_delta,
            "off_false_clusters": clusters,
            "off_false_clusters_delta_vs_current": clusters_delta,
            "causal_transfer_floor_db": float(
                safety["causal_transfer_floor_db"]),
            "causal_transfer_ok": causal_ok,
            "pfa_acceptable": pfa_ok,
            "false_clusters_acceptable": clusters_ok,
            "safe": safe,
        })
    selected = select_safe_candidate(candidate_rows)
    safe_candidates = [row for row in candidate_rows if row["safe"]]
    selection = {
        "split": spec["split"], "scene_id": spec["scene_id"],
        "selected_method": selected["method"],
        "selected_lambda": selected["lambda"],
        "safe_oracle_headroom_db": max(
            0.0, finite(selected.get("scnr_gain_db_vs_current"), 0.0)),
        "safe_candidate_count": len(safe_candidates),
        "selection_definition": "per-scene safe candidate with highest clutter cancellation_db; Current is always a safe identity baseline",
    }
    compact = {
        "spec": spec,
        "status": scene["status"],
        "exact_pairing": "same deterministic background_input_dir for OFF/ON/TO",
        "plans": plan_records,
        "selection": selection,
    }
    if scene_root.exists():
        shutil.rmtree(scene_root)
    return transfer_rows, detection_rows, cfar_rows, candidate_rows, compact


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
                    cfar_rows: list[dict[str, Any]],
                    safety: Mapping[str, Any]) -> list[dict[str, Any]]:
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
        transfer_floor = row["L_causal_min_dB"] >= float(
            safety["causal_transfer_floor_db"])
        pd_overall = row["paired_causal_pd"] >= current["paired_causal_pd"]
        pd_by_snr = all(candidate_by_snr[snr] >= current_by_snr[snr]
                         for snr in current_by_snr)
        pfa_delta = row["off_pfa_mean"] - current_pfa
        cluster_delta = row["off_false_clusters_mean"] - current_clusters
        pfa_ok = pfa_delta <= float(safety["target_off_pfa_delta_max"])
        clusters_ok = cluster_delta <= float(
            safety["target_off_false_clusters_delta_max"])
        output.append({
            "method": row["method"],
            "lambda": row["lambda"],
            "causal_transfer_floor_db": float(safety["causal_transfer_floor_db"]),
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


def aggregate_candidate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate per-scene candidates without replacing the scene evidence."""
    output: list[dict[str, Any]] = []
    keys = sorted({(str(row["method"]), float(row["lambda"])) for row in rows})
    for method, lambda_value in keys:
        selected = [row for row in rows if row["method"] == method
                    and math.isclose(float(row["lambda"]), lambda_value)]
        def values(field: str) -> np.ndarray:
            return finite_values(selected, field)
        lcausal = values("L_causal_min_dB")
        gain = values("scnr_gain_db_vs_current")
        cancellation = values("cancellation_db")
        output.append({
            "method": method,
            "lambda": lambda_value,
            "scene_count": len(selected),
            "safe_scene_count": sum(bool(row["safe"]) for row in selected),
            "safe_scene_fraction": (float(np.mean([bool(row["safe"]) for row in selected]))
                                     if selected else math.nan),
            "cancellation_db_mean": (float(np.mean(cancellation))
                                      if cancellation.size else math.nan),
            "scnr_gain_db_vs_current_mean": (float(np.mean(gain))
                                              if gain.size else math.nan),
            "L_causal_min_p05_dB": (float(np.percentile(lcausal, 5))
                                     if lcausal.size else math.nan),
            "L_causal_min_worst_dB": (float(np.min(lcausal))
                                       if lcausal.size else math.nan),
            "paired_hit_count": sum(int(row["paired_hit_count"]) for row in selected),
            "target_count": sum(int(row["target_count"]) for row in selected),
        })
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/target_safe_oracle_v2")
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs/research/target_safe_oracle_v2.json")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--formal-dir", type=Path,
                        default=ROOT / "outputs/physics_adaptive_selective_v2_formal")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    config_path = args.config.resolve()
    formal_dir = args.formal_dir.resolve()
    oracle_config = json.loads(config_path.read_text(encoding="utf-8"))
    if oracle_config.get("ai_training") is not False:
        raise RuntimeError("Target-Safe Oracle requires ai_training=false")
    gate_path = formal_dir / "null_gate.json"
    positive_gate_path = formal_dir / "positive_quality_gate.json"
    provenance_start = run_provenance_start(
        ROOT, (config_path, gate_path, positive_gate_path))
    if provenance_start["source_worktree_dirty_before"] is not False:
        raise SystemExit("Target-Safe Oracle requires a clean source worktree at start")
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    positive_gate = json.loads(positive_gate_path.read_text(encoding="utf-8"))
    safety = oracle_config["safety"]
    base = formal.base_scenario()
    specs = make_specs()
    before = resource_snapshot()
    if not before["nvidia_smi"] or "NVIDIA-SMI has failed" in before["nvidia_smi"]:
        raise SystemExit("Target-Safe Oracle requires a visible CUDA device")
    transfer_rows: list[dict[str, Any]] = []
    detection_rows: list[dict[str, Any]] = []
    cfar_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    scene_records: list[dict[str, Any]] = []
    for index, spec in enumerate(specs, 1):
        print(f"[target-safe-oracle-v2] {index}/{len(specs)} {spec['scene_id']}",
              flush=True)
        tr, det, cfar, candidates, compact = run_one_scene(
            spec, base, output, args.build_dir.resolve(), gate, positive_gate,
            safety)
        transfer_rows.extend(tr)
        detection_rows.extend(det)
        cfar_rows.extend(cfar)
        candidate_rows.extend(candidates)
        scene_records.append(compact)

    selected_rows = [record["selection"] for record in scene_records]
    headroom_values = finite_values(selected_rows, "safe_oracle_headroom_db")
    safe_oracle_headroom = (float(np.mean(headroom_values))
                            if headroom_values.size else 0.0)
    summary = aggregate_candidate_rows(candidate_rows)
    write_csv(output / "oracle_transfer_rows.csv", transfer_rows)
    write_csv(output / "oracle_detection_rows.csv", detection_rows)
    write_csv(output / "oracle_cfar_rows.csv", cfar_rows)
    write_csv(output / "oracle_candidate_metrics.csv", candidate_rows)
    write_csv(output / "oracle_scene_selection.csv", selected_rows)
    write_csv(output / "oracle_summary.csv", summary)
    (output / "resource_snapshot_before.json").write_text(
        json.dumps(json_safe(before), ensure_ascii=False, indent=2,
                   allow_nan=False) + "\n", encoding="utf-8")
    after = resource_snapshot()
    (output / "resource_snapshot_after.json").write_text(
        json.dumps(json_safe(after), ensure_ascii=False, indent=2,
                   allow_nan=False) + "\n", encoding="utf-8")
    provenance_post = run_provenance_post(ROOT, provenance_start)
    (output / "provenance_start.json").write_text(
        json.dumps(json_safe(provenance_start), ensure_ascii=False, indent=2,
                   allow_nan=False) + "\n", encoding="utf-8")
    (output / "provenance_post.json").write_text(
        json.dumps(json_safe(provenance_post), ensure_ascii=False, indent=2,
                   allow_nan=False) + "\n", encoding="utf-8")
    formal_manifest = formal_dir / "formal_matrix_manifest.json"
    formal_source_commit = (json.loads(formal_manifest.read_text(encoding="utf-8"))
                            .get("source_commit") if formal_manifest.is_file() else None)
    manifest = {
        "schema_version": 2,
        "analysis": "Per-scene Target-Safe Physics Oracle",
        "source_commit": provenance_start["source_commit"],
        "source_worktree_dirty_before": provenance_start[
            "source_worktree_dirty_before"],
        "worktree_dirty": provenance_post["source_worktree_dirty_after"],
        "provenance_start": provenance_start,
        "provenance_post": provenance_post,
        "ai_training": False,
        "evaluation_only": True,
        "formal_source_commit": formal_source_commit,
        "safe_oracle_headroom_db": safe_oracle_headroom,
        "design": {
            "scene_count": len(specs),
            "snr_values_db": sorted({float(spec["snr_db"]) for spec in specs}),
            "families": list(transfer.FAMILIES),
            "seed_namespace": "2026111000 audit replay, targeted 12-scene development set",
            "roles": {"OFF": "C+N", "ON": "S+C+N", "TO": "S-only"},
            "same_seed_geometry_mismatch": True,
            "primary_calibration_source": "target_off",
            "not_used_for_threshold_fit": True,
            "candidate_policy": "Current plus J5/J6 lambda in {0.25,0.5,0.75,1}; select independently per scene",
        },
        "lambda_values": {"J5": list(EXPERT_LAMBDAS), "J6": list(EXPERT_LAMBDAS)},
        "lambda_definition": (
            "scale frozen OFF-derived estimated delay and phase correction; "
            "Current is the explicit identity baseline"),
        "constraints": safety,
        "identity_checks": {
            "current_is_explicit_baseline": True,
            "lambda_zero_expert_not_used_as_candidate": True,
            "lambda_one_equals_full_frozen_plan": True,
        },
        "counts": {
            "scene_count": len(scene_records),
            "transfer_rows": len(transfer_rows),
            "detection_rows": len(detection_rows),
            "cfar_rows": len(cfar_rows),
            "candidate_rows": len(candidate_rows),
        },
        "outputs": {
            "oracle_transfer_rows": "oracle_transfer_rows.csv",
            "oracle_detection_rows": "oracle_detection_rows.csv",
            "oracle_cfar_rows": "oracle_cfar_rows.csv",
            "oracle_candidate_metrics": "oracle_candidate_metrics.csv",
            "oracle_scene_selection": "oracle_scene_selection.csv",
            "oracle_summary": "oracle_summary.csv",
        },
        "cleanup": {
            "raw_scene_cleanup": True,
            "retained": "compact candidate metrics, transfer/detection/CFAR rows, per-scene selection, provenance and resource snapshots",
        },
        "scene_records": scene_records,
    }
    (output / "target_safe_oracle_v2_manifest.json").write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2,
                   allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(json_safe({
        "output_dir": str(output),
        "scene_count": len(scene_records),
        "safe_oracle_headroom_db": safe_oracle_headroom,
        "selected_methods": {
            method: sum(row["selected_method"] == method for row in selected_rows)
            for method in ("J0_Current", *EXPERT_METHODS)
        },
        "ai_training": False,
    }), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
