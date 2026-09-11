#!/usr/bin/env python3
"""Evaluate the frozen Test-V3 Physics-AI final gate offline."""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
CURRENT = "J0_Current"
J5 = "J5_Selective_Physics_Calibration"
J6 = "J6_Joint_Phase_Surface"
J7 = "J7_Deterministic_Safe_Expert_Selector"
ORACLE = "Oracle_Safe_Lambda_0_EvaluationOnly"
METHODS = (J5, J6, J7, ORACLE)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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


def stats(rows: list[dict[str, str]], field: str) -> dict[str, Any]:
    values = np.asarray([finite(row.get(field)) for row in rows], dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"count": 0, "mean": None, "median": None, "p05": None,
                "min": None}
    return {"count": int(values.size), "mean": float(np.mean(values)),
            "median": float(np.median(values)),
            "p05": float(np.percentile(values, 5)),
            "min": float(np.min(values))}


def method_rows(rows: list[dict[str, str]], method: str) -> list[dict[str, str]]:
    return [row for row in rows if row.get("method") == method]


def paired_pd_by_snr(rows: list[dict[str, str]], method: str) -> dict[str, float]:
    output: dict[str, float] = {}
    selected = method_rows(rows, method)
    for snr in sorted({row["snr_db"] for row in selected}, key=float):
        group = [row for row in selected if row["snr_db"] == snr]
        output[snr] = float(np.mean([
            row["paired_causal_hit"].lower() == "true" for row in group]))
    return output


def cfar_delta(cfar_rows: list[dict[str, str]], method: str,
               field: str) -> float:
    current = {
        (row["scene_id"], row["role"]): finite(row.get(field))
        for row in cfar_rows if row["method"] == CURRENT
    }
    deltas = []
    for row in cfar_rows:
        if row["method"] != method or row["role"] != "target_off":
            continue
        value = finite(row.get(field))
        baseline = current.get((row["scene_id"], row["role"]), math.nan)
        if math.isfinite(value) and math.isfinite(baseline):
            deltas.append(value - baseline)
    return float(np.mean(deltas)) if deltas else math.nan


def evaluate_method(transfer_rows: list[dict[str, str]],
                    detection_rows: list[dict[str, str]],
                    cfar_rows: list[dict[str, str]], method: str,
                    causal_floor_db: float) -> dict[str, Any]:
    transfer = method_rows(transfer_rows, method)
    detection = method_rows(detection_rows, method)
    baseline_pd = float(np.mean([
        row["paired_causal_hit"].lower() == "true"
        for row in method_rows(detection_rows, CURRENT)]))
    pd = float(np.mean([
        row["paired_causal_hit"].lower() == "true"
        for row in detection]))
    baseline_by_snr = paired_pd_by_snr(detection_rows, CURRENT)
    candidate_by_snr = paired_pd_by_snr(detection_rows, method)
    pd_by_snr_ok = all(candidate_by_snr.get(snr, -math.inf) >= value
                        for snr, value in baseline_by_snr.items())
    pfa_delta = cfar_delta(cfar_rows, method, "pfa")
    cluster_delta = cfar_delta(cfar_rows, method, "false_clusters")
    causal = stats(transfer, "L_causal_dB")
    target_only = stats(transfer, "L_target_only_dB")
    causal_ok = causal["min"] is not None and causal["min"] >= causal_floor_db
    pd_overall_ok = pd >= baseline_pd
    pfa_ok = math.isfinite(pfa_delta) and pfa_delta <= 0.0
    cluster_ok = math.isfinite(cluster_delta) and cluster_delta <= 0.0
    return {
        "method": method,
        "target_count": len(transfer),
        "L_causal_mean_dB": causal["mean"],
        "L_causal_median_dB": causal["median"],
        "L_causal_p05_dB": causal["p05"],
        "L_causal_min_dB": causal["min"],
        "L_target_only_mean_dB": target_only["mean"],
        "L_target_only_p05_dB": target_only["p05"],
        "paired_causal_pd": pd,
        "current_paired_causal_pd": baseline_pd,
        "paired_pd_no_loss_overall": pd_overall_ok,
        "paired_pd_no_loss_by_snr": pd_by_snr_ok,
        "paired_pd_by_snr": candidate_by_snr,
        "current_paired_pd_by_snr": baseline_by_snr,
        "pfa_delta_vs_current": pfa_delta,
        "false_clusters_delta_vs_current": cluster_delta,
        "causal_transfer_floor_db": causal_floor_db,
        "causal_transfer_ok": causal_ok,
        "pfa_acceptable": pfa_ok,
        "false_clusters_acceptable": cluster_ok,
        "method_passes_physics_gate": bool(
            causal_ok and pd_overall_ok and pd_by_snr_ok and pfa_ok and cluster_ok),
    }


def evaluate(output_dir: Path, config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    manifest = json.loads(
        (output_dir / "physics_ai_final_gate_v3_manifest.json").read_text(
            encoding="utf-8"))
    transfer_rows = read_csv(output_dir / "v3_transfer_rows.csv")
    detection_rows = read_csv(output_dir / "v3_detection_rows.csv")
    cfar_rows = read_csv(output_dir / "v3_cfar_rows.csv")
    selector_rows = read_csv(output_dir / "v3_inference_visible_features.csv")
    causal_floor = float(config["final_gate_guardrails"]["causal_transfer_floor_db"])
    method_results = [evaluate_method(
        transfer_rows, detection_rows, cfar_rows, method, causal_floor)
        for method in METHODS]
    allowed_feature_columns = {
        "split", "scene_id", "selected_method", "selection_reason",
        "joint_confidence", "joint_rmse_rad", "residual_coherence",
        "raw_coherence", "support_fraction", "delay_confidence",
        "phase_confidence",
        "tau_ns", "beta1_deg_per_pulse", "beta2_deg_per_pulse2",
        "d3_j6_delay_disagreement_ns",
        "p1_j6_phase_disagreement_deg_per_pulse", "phase_signal", "delay_signal",
        "delay_extrapolation_ratio",
    }
    feature_columns = set(selector_rows[0]) if selector_rows else set()
    forbidden_columns = sorted(feature_columns - allowed_feature_columns)
    leakage_ok = not forbidden_columns and config["j7_selector"]["truth_fields_in_input"] is False
    j7 = next(row for row in method_results if row["method"] == J7)
    nontrivial_pass = any(
        row["method"] in (J5, J6, J7) and row["method_passes_physics_gate"]
        for row in method_results)
    decision = "REOPEN_AI_ROUTER" if nontrivial_pass and leakage_ok else "FINAL_NO_GO_AI"
    reasons = []
    if not j7["causal_transfer_ok"]:
        reasons.append("J7 causal target transfer floor failed")
    if not j7["pfa_acceptable"]:
        reasons.append("J7 target-off Pfa regressed")
    if not j7["false_clusters_acceptable"]:
        reasons.append("J7 target-off false clusters regressed")
    if not j7["paired_pd_no_loss_overall"] or not j7["paired_pd_no_loss_by_snr"]:
        reasons.append("J7 paired causal Pd no-loss condition failed")
    if not leakage_ok:
        reasons.append("J7 inference feature schema contains forbidden fields")
    if not reasons:
        reasons.append("J7 passed frozen physics guardrails; residual AI value requires router review")
    selector_counts: dict[str, int] = {}
    for row in selector_rows:
        method = row["selected_method"]
        selector_counts[method] = selector_counts.get(method, 0) + 1
    return {
        "schema_version": 1,
        "analysis": "Physics-AI Final Gate Test-V3",
        "source_commit": manifest.get("source_commit"),
        "v3_manifest": str((output_dir / "physics_ai_final_gate_v3_manifest.json").resolve()),
        "config_path": str(config_path.resolve()),
        "ai_training": False,
        "decision": decision,
        "decision_reasons": reasons,
        "j7_selector_counts": selector_counts,
        "j7_inference_feature_leakage_ok": leakage_ok,
        "j7_forbidden_feature_columns": forbidden_columns,
        "method_results": method_results,
        "guardrails": config["final_gate_guardrails"],
        "limitations": [
            "Test-V3 is a physics-only gate and does not authorize AI training.",
            "Oracle_Safe_Lambda_0 is an identity evaluation baseline, not a gain claim.",
            "Pfa and false-cluster checks are descriptive paired scene means under this frozen matrix.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/physics_ai_final_gate_v3")
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs/research/physics_ai_final_gate_v3.json")
    args = parser.parse_args()
    result = evaluate(args.output_dir.resolve(), args.config.resolve())
    rows = []
    for item in result["method_results"]:
        row = dict(item)
        row.pop("paired_pd_by_snr", None)
        row.pop("current_paired_pd_by_snr", None)
        rows.append(row)
    write_csv(args.output_dir.resolve() / "final_gate_method_results.csv", rows)
    (args.output_dir.resolve() / "physics_ai_final_gate_decision.json").write_text(
        json.dumps(json_safe(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps(json_safe({
        "decision": result["decision"],
        "reasons": result["decision_reasons"],
        "j7_selector_counts": result["j7_selector_counts"],
        "ai_training": False,
    }), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
