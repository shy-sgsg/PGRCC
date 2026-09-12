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
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
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
                    guardrails: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one method using only numeric guardrails from JSON config."""
    causal_floor_db = float(guardrails["causal_transfer_floor_db"])
    pd_no_loss_required = bool(guardrails["paired_pd_no_loss"])
    pfa_delta_max = float(guardrails["target_off_pfa_delta_max"])
    cluster_delta_max = float(guardrails["target_off_false_clusters_delta_max"])
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
    pd_overall_ok = (not pd_no_loss_required) or pd >= baseline_pd
    pd_by_snr_ok = (not pd_no_loss_required) or pd_by_snr_ok
    pfa_ok = math.isfinite(pfa_delta) and pfa_delta <= pfa_delta_max
    cluster_ok = math.isfinite(cluster_delta) and cluster_delta <= cluster_delta_max
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
        "target_off_pfa_delta_max": pfa_delta_max,
        "target_off_false_clusters_delta_max": cluster_delta_max,
        "paired_pd_no_loss_required": pd_no_loss_required,
        "causal_transfer_ok": causal_ok,
        "pfa_acceptable": pfa_ok,
        "false_clusters_acceptable": cluster_ok,
        "method_passes_physics_gate": bool(
            causal_ok and pd_overall_ok and pd_by_snr_ok and pfa_ok and cluster_ok),
    }


def _resolve_from_config(path_value: str | None, config_path: Path) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def load_safe_oracle_evidence(config: Mapping[str, Any], config_path: Path) -> dict[str, Any]:
    """Load the per-scene safe-oracle result without treating absence as proof."""
    oracle_config = config.get("target_safe_oracle", {})
    manifest_path = _resolve_from_config(
        oracle_config.get("result_manifest"), config_path)
    if manifest_path is None or not manifest_path.is_file():
        return {
            "status": "not_proven",
            "reason": "target-safe oracle manifest is missing",
            "manifest": str(manifest_path) if manifest_path else None,
            "safe_oracle_headroom_db": None,
        }
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    start = manifest.get("provenance_start", {})
    dirty_before = start.get("source_worktree_dirty_before",
                             manifest.get("source_worktree_dirty_before"))
    headroom = finite(manifest.get("safe_oracle_headroom_db"))
    configured_min = float(oracle_config["safe_oracle_headroom_min_db"])
    if dirty_before is not False:
        return {
            "status": "not_proven",
            "reason": "target-safe oracle source_worktree_dirty_before is not false",
            "manifest": str(manifest_path),
            "safe_oracle_headroom_db": headroom,
            "source_worktree_dirty_before": dirty_before,
        }
    if not math.isfinite(headroom):
        return {
            "status": "not_proven",
            "reason": "target-safe oracle did not publish finite headroom",
            "manifest": str(manifest_path),
            "safe_oracle_headroom_db": None,
            "source_worktree_dirty_before": dirty_before,
        }
    return {
        "status": "proven",
        "reason": "per-scene safe-oracle replay published finite headroom",
        "manifest": str(manifest_path),
        "safe_oracle_headroom_db": headroom,
        "safe_oracle_headroom_min_db": configured_min,
        "source_worktree_dirty_before": dirty_before,
        "scene_count": manifest.get("counts", {}).get("scene_count"),
    }


def classify_final_gate(method_results: list[dict[str, Any]],
                        leakage_ok: bool,
                        oracle_evidence: Mapping[str, Any],
                        config: Mapping[str, Any]) -> dict[str, Any]:
    """Classify the three explicit final-gate states.

    A safe J7 result alone never reopens AI.  Reopening requires a finite,
    positive per-scene Safe Oracle headroom and failure of deterministic
    recovery to capture that headroom.
    """
    oracle_config = config["target_safe_oracle"]
    min_headroom = float(oracle_config["safe_oracle_headroom_min_db"])
    min_deterministic_gain = float(
        oracle_config["deterministic_solved_min_gain_db"])
    test_v3_development_only = bool(config.get("test_v3_development_only", False))
    deterministic_solved: list[str] = []
    if not test_v3_development_only:
        for row in method_results:
            if row["method"] not in (J5, J6, J7):
                continue
            gain = finite(row.get("L_causal_mean_dB"))
            if (row["method_passes_physics_gate"] and math.isfinite(gain)
                    and gain > min_deterministic_gain and leakage_ok):
                deterministic_solved.append(row["method"])
    headroom = finite(oracle_evidence.get("safe_oracle_headroom_db"))
    oracle_has_headroom = (
        oracle_evidence.get("status") == "proven"
        and math.isfinite(headroom)
        and headroom > min_headroom)
    if deterministic_solved:
        decision = "NO_GO_AI"
        reason = "deterministic physics method recovered measurable safe headroom"
    elif not oracle_has_headroom:
        decision = "FINAL_NO_GO_AI"
        reason = ("safe oracle has no measurable headroom or its evidence is "
                  "not proven")
    else:
        decision = "REOPEN_AI_ROUTER"
        reason = ("safe oracle has measurable residual headroom, but no "
                  "deterministic method recovered it")
    return {
        "decision": decision,
        "reason": reason,
        "deterministic_solved_methods": deterministic_solved,
        "safe_oracle_has_headroom": oracle_has_headroom,
        "safe_oracle_headroom_db": headroom if math.isfinite(headroom) else None,
        "safe_oracle_headroom_min_db": min_headroom,
        "deterministic_solved_min_gain_db": min_deterministic_gain,
        "test_v3_development_only": test_v3_development_only,
        "deterministic_evidence_status": (
            "not_used_for_final_gate" if test_v3_development_only
            else "evaluated"),
        "j7_safety_alone_can_reopen": False,
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
    guardrails = config["final_gate_guardrails"]
    method_results = [evaluate_method(
        transfer_rows, detection_rows, cfar_rows, method, guardrails)
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
        "abs_beta2_deg_per_pulse2",
        "joint_vs_p1_residual_coherence_gain",
        "complexity_route_reason",
    }
    feature_columns = set(selector_rows[0]) if selector_rows else set()
    forbidden_columns = sorted(feature_columns - allowed_feature_columns)
    leakage_ok = not forbidden_columns and config["j7_selector"]["truth_fields_in_input"] is False
    j7 = next(row for row in method_results if row["method"] == J7)
    oracle_evidence = load_safe_oracle_evidence(config, config_path)
    classification = classify_final_gate(
        method_results, leakage_ok, oracle_evidence, config)
    decision = classification["decision"]
    reasons = []
    development_diagnostics = []
    diagnostic_reasons = []
    if not j7["causal_transfer_ok"]:
        diagnostic_reasons.append("J7 causal target transfer floor failed")
    if not j7["pfa_acceptable"]:
        diagnostic_reasons.append("J7 target-off Pfa regressed")
    if not j7["false_clusters_acceptable"]:
        diagnostic_reasons.append("J7 target-off false clusters regressed")
    if not j7["paired_pd_no_loss_overall"] or not j7["paired_pd_no_loss_by_snr"]:
        diagnostic_reasons.append("J7 paired causal Pd no-loss condition failed")
    if not leakage_ok:
        diagnostic_reasons.append("J7 inference feature schema contains forbidden fields")
    if config.get("test_v3_development_only", False):
        development_diagnostics.extend(diagnostic_reasons)
        reasons.append("Test-V3 metrics are development evidence and are excluded from the final gate")
    else:
        reasons.extend(diagnostic_reasons)
    reasons.append(classification["reason"])
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
        "decision_classification": classification,
        "test_v3_development_only": bool(
            config.get("test_v3_development_only", False)),
        "development_diagnostics": development_diagnostics,
        "j7_selector_counts": selector_counts,
        "j7_inference_feature_leakage_ok": leakage_ok,
        "j7_forbidden_feature_columns": forbidden_columns,
        "method_results": method_results,
        "guardrails": config["final_gate_guardrails"],
        "target_safe_oracle": oracle_evidence,
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
