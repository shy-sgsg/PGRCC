#!/usr/bin/env python3
"""Audit why the V1 global gate prevented independent corrections.

This is a read-only audit of the compact V1 formal manifest.  It does not
rerun CUDA and it never uses mechanism truth to form an estimator.  The
mechanism label is retained only as an evaluation key so that gate decisions
can be compared with known Oracle headroom after the run.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


METHODS = (
    "J0_Current",
    "J1_D3_delay_only",
    "J2_P1_phase_only",
    "J3_D3_P1_joint",
    "J4_D3_P2_joint",
)
CORRECTION_METHODS = METHODS[1:]
DECORRELATED = "DECORRELATED"
UNCERTAIN = "UNCERTAIN"
CALIBRATABLE = "CALIBRATABLE"


def finite(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def finite_values(values: Iterable[Any]) -> list[float]:
    result = [finite(value) for value in values]
    return [value for value in result if math.isfinite(value)]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               allow_nan=False) + "\n", encoding="utf-8")


def phase_section(observables: dict[str, Any], method: str) -> tuple[str, dict[str, Any]]:
    phase_method = "P2" if method == "J4_D3_P2_joint" else "P1"
    return phase_method, observables["phase_p2" if phase_method == "P2" else "phase_p1"]


def independent_flags(observables: dict[str, Any], gate: dict[str, Any], method: str) -> dict[str, Any]:
    """Reconstruct the V1 component-wise evidence without changing V1."""
    thresholds = gate["thresholds"]
    delay = observables.get("delay", {})
    phase_method, phase = phase_section(observables, method)
    coherence = observables.get("coherence", {})
    pulse_coherence = finite(coherence.get("pulse_median"))
    delay_estimate = finite(delay.get("delta_tau_ns"))
    delay_confidence = finite(delay.get("confidence"), 0.0)
    delay_rmse = finite(delay.get("rmse_rad"))
    max_supported = finite(observables.get("input", {}).get(
        "max_supported_delay_ns"), math.inf)
    phase_estimate = finite(phase.get("slope_deg_per_pulse"))
    phase_confidence = finite(phase.get("confidence"), 0.0)
    phase_rmse = finite(phase.get("rmse_rad"))

    coherence_ok = (math.isfinite(pulse_coherence) and
                    pulse_coherence >= finite(thresholds.get(
                        "decorrelation_pulse_coherence"), -math.inf))
    delay_confidence_ok = delay_confidence >= finite(thresholds.get(
        "confidence_tau"), math.inf)
    delay_fit_ok = (math.isfinite(delay_rmse) and
                    delay_rmse <= finite(thresholds.get("max_rmse_tau_rad"), -math.inf))
    delay_support_ok = (math.isfinite(delay_estimate) and
                        abs(delay_estimate) <= max_supported)
    phase_confidence_key = ("confidence_phase_p2" if phase_method == "P2"
                            else "confidence_phase")
    phase_rmse_key = ("max_rmse_phase_p2_rad" if phase_method == "P2"
                      else "max_rmse_phase_rad")
    phase_deadband_key = ("epsilon_phase_p2_deg_per_pulse" if phase_method == "P2"
                          else "epsilon_phase_deg_per_pulse")
    phase_confidence_ok = phase_confidence >= finite(
        thresholds.get(phase_confidence_key), math.inf)
    phase_fit_ok = (math.isfinite(phase_rmse) and
                    phase_rmse <= finite(thresholds.get(phase_rmse_key), -math.inf))
    delay_deadband_inside = (math.isfinite(delay_estimate) and
                             abs(delay_estimate) <= finite(
                                 thresholds.get("epsilon_tau_ns"), -math.inf))
    phase_deadband_inside = (math.isfinite(phase_estimate) and
                             abs(phase_estimate) <= finite(
                                 thresholds.get(phase_deadband_key), -math.inf))
    delay_quality_ok = coherence_ok and delay_confidence_ok and delay_fit_ok and delay_support_ok
    phase_quality_ok = coherence_ok and phase_confidence_ok and phase_fit_ok
    return {
        "phase_method": phase_method,
        "pulse_coherence": pulse_coherence,
        "coherence_ok": coherence_ok,
        "delay_estimate_ns": delay_estimate,
        "delay_confidence": delay_confidence,
        "delay_rmse_rad": delay_rmse,
        "delay_confidence_ok": delay_confidence_ok,
        "delay_fit_ok": delay_fit_ok,
        "delay_support_ok": delay_support_ok,
        "delay_quality_ok": delay_quality_ok,
        "delay_deadband_inside": delay_deadband_inside,
        "delay_active_independent": delay_quality_ok and not delay_deadband_inside,
        "phase_estimate_deg_per_pulse": phase_estimate,
        "phase_confidence": phase_confidence,
        "phase_rmse_rad": phase_rmse,
        "phase_confidence_ok": phase_confidence_ok,
        "phase_fit_ok": phase_fit_ok,
        "phase_quality_ok": phase_quality_ok,
        "phase_deadband_inside": phase_deadband_inside,
        "phase_active_independent": phase_quality_ok and not phase_deadband_inside,
    }


def blocker(flags: dict[str, Any]) -> str:
    if not flags["coherence_ok"]:
        return "coherence"
    delay_bad = not flags["delay_quality_ok"]
    phase_bad = not flags["phase_quality_ok"]
    if delay_bad and phase_bad:
        return "delay_and_phase"
    if delay_bad:
        return "delay"
    if phase_bad:
        return "phase"
    return "none"


def oracle_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (str(row.get("split")), str(row.get("scene_id")),
            str(row.get("role")), str(row.get("method")))


def build_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    gate = manifest["null_gate"]
    oracle_rows = {
        oracle_key(row): row for row in manifest.get("oracle_recovery", {}).get("rows", [])
    }
    output: list[dict[str, Any]] = []
    for scene in manifest["scene_records"]:
        spec = scene["spec"]
        family = str(spec["label"])
        required_delay = "M1" in set(family.split("+"))
        required_phase = "M2" in set(family.split("+"))
        for role, observables in scene.get("observables", {}).items():
            if role not in {"target_off", "target_on"}:
                continue
            for method in CORRECTION_METHODS:
                method_row = scene.get("methods", {}).get(role, {}).get(method, {})
                flags = independent_flags(observables, gate, method)
                recovery = oracle_rows.get((spec["split"], spec["scene_id"], role, method), {})
                current = finite(recovery.get("current_cancellation_db"))
                oracle = finite(recovery.get("oracle_cancellation_db"))
                opportunity = (oracle - current if math.isfinite(oracle) and
                               math.isfinite(current) else math.nan)
                state = str(method_row.get("state", ""))
                fallback = bool(method_row.get("fallback"))
                global_uncertain = state == UNCERTAIN
                global_decorrelated = state == DECORRELATED
                old_delay_blocked = required_delay and not flags["delay_active_independent"]
                old_phase_blocked = required_phase and not flags["phase_active_independent"]
                output.append({
                    "split": spec["split"],
                    "scene_id": spec["scene_id"],
                    "actual_family": family,
                    "role": role,
                    "method": method,
                    "required_delay": required_delay,
                    "required_phase": required_phase,
                    "state": state,
                    "fallback": fallback,
                    "fallback_reason": method_row.get("fallback_reason"),
                    "global_uncertain": global_uncertain,
                    "global_decorrelated": global_decorrelated,
                    "blocker": blocker(flags),
                    "delay_estimate_ns": flags["delay_estimate_ns"],
                    "delay_confidence": flags["delay_confidence"],
                    "delay_rmse_rad": flags["delay_rmse_rad"],
                    "delay_deadband_inside": flags["delay_deadband_inside"],
                    "delay_confidence_ok": flags["delay_confidence_ok"],
                    "delay_fit_ok": flags["delay_fit_ok"],
                    "delay_support_ok": flags["delay_support_ok"],
                    "delay_active_independent": flags["delay_active_independent"],
                    "phase_method": flags["phase_method"],
                    "phase_estimate_deg_per_pulse": flags["phase_estimate_deg_per_pulse"],
                    "phase_confidence": flags["phase_confidence"],
                    "phase_rmse_rad": flags["phase_rmse_rad"],
                    "phase_deadband_inside": flags["phase_deadband_inside"],
                    "phase_confidence_ok": flags["phase_confidence_ok"],
                    "phase_fit_ok": flags["phase_fit_ok"],
                    "phase_active_independent": flags["phase_active_independent"],
                    "pulse_coherence": flags["pulse_coherence"],
                    "estimated_gain_db": finite(method_row.get("headroom_gain_db_vs_current"), 0.0),
                    "oracle_cancellation_db": oracle,
                    "current_cancellation_db": current,
                    "oracle_headroom_db": finite(recovery.get("recoverable_headroom_db")),
                    "recovery_ratio": finite(recovery.get("recovery_ratio")),
                    "fallback_opportunity_cost_db": opportunity,
                    "uncertain_headroom_gt_0_5_db": bool(global_uncertain and opportunity > 0.5),
                    "uncertain_headroom_gt_1_db": bool(global_uncertain and opportunity > 1.0),
                    "old_delay_blocked": old_delay_blocked,
                    "old_phase_blocked": old_phase_blocked,
                })
    return output


def group_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["split"], row["actual_family"], row["method"])].append(row)
    output: list[dict[str, Any]] = []
    for (split, family, method), items in sorted(groups.items()):
        costs = finite_values(item["fallback_opportunity_cost_db"] for item in items)
        output.append({
            "split": split,
            "actual_family": family,
            "method": method,
            "role_count": len(items),
            "global_uncertain_count": sum(item["global_uncertain"] for item in items),
            "global_decorrelated_count": sum(item["global_decorrelated"] for item in items),
            "fallback_count": sum(item["fallback"] for item in items),
            "independent_delay_active_count": sum(item["delay_active_independent"] for item in items),
            "independent_phase_active_count": sum(item["phase_active_independent"] for item in items),
            "old_delay_blocked_count": sum(item["old_delay_blocked"] for item in items),
            "old_phase_blocked_count": sum(item["old_phase_blocked"] for item in items),
            "uncertain_headroom_gt_0_5_db_count": sum(item["uncertain_headroom_gt_0_5_db"] for item in items),
            "uncertain_headroom_gt_1_db_count": sum(item["uncertain_headroom_gt_1_db"] for item in items),
            "median_fallback_opportunity_cost_db": statistics.median(costs) if costs else math.nan,
            "max_fallback_opportunity_cost_db": max(costs) if costs else math.nan,
        })
    return output


def activation_confusion(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs = (
        ("M1_delay", lambda row: row["required_delay"]),
        ("M2_phase", lambda row: row["required_phase"]),
        ("M1+M2_delay_branch", lambda row: row["actual_family"] == "M1+M2" and row["required_delay"]),
        ("M1+M2_phase_branch", lambda row: row["actual_family"] == "M1+M2" and row["required_phase"]),
    )
    output: list[dict[str, Any]] = []
    for name, predicate in specs:
        selected = [row for row in rows if predicate(row)]
        if not selected:
            continue
        output.append({
            "audit_slice": name,
            "row_count": len(selected),
            "scene_count": len({(row["split"], row["scene_id"]) for row in selected}),
            "global_uncertain_count": sum(row["global_uncertain"] for row in selected),
            "global_decorrelated_count": sum(row["global_decorrelated"] for row in selected),
            "fallback_count": sum(row["fallback"] for row in selected),
            "independent_delay_active_count": sum(row["delay_active_independent"] for row in selected),
            "independent_phase_active_count": sum(row["phase_active_independent"] for row in selected),
            "old_delay_blocked_count": sum(row["old_delay_blocked"] for row in selected),
            "old_phase_blocked_count": sum(row["old_phase_blocked"] for row in selected),
            "uncertain_headroom_gt_0_5_db_count": sum(row["uncertain_headroom_gt_0_5_db"] for row in selected),
            "uncertain_headroom_gt_1_db_count": sum(row["uncertain_headroom_gt_1_db"] for row in selected),
        })
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("method_version") != "Physics-Adaptive Joint Calibration V1":
        raise SystemExit("refusing a non-V1 manifest")
    rows = build_rows(manifest)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "gate_failure_rows.csv", rows)
    write_csv(output / "gate_failure_by_family.csv", group_summary(rows))
    write_csv(output / "activation_confusion.csv", activation_confusion(rows))
    opportunity = [row for row in rows if math.isfinite(row["oracle_headroom_db"])]
    write_csv(output / "fallback_opportunity_cost.csv", opportunity)
    write_json(output / "audit_manifest.json", {
        "schema_version": 1,
        "source_manifest": str(manifest_path),
        "source_commit": manifest.get("source_commit"),
        "source_v1_immutable": True,
        "ai_training": False,
        "row_count": len(rows),
        "definitions": {
            "fallback_opportunity_cost": "Oracle cancellation - Current cancellation",
            "independent_delay_active": "V1 observable delay quality/deadband checks without phase quality veto",
            "independent_phase_active": "V1 observable phase quality/deadband checks without delay quality veto",
            "mechanism_labels": "evaluation-only grouping; never used by estimator or gate",
        },
    })
    print(json.dumps({
        "source_commit": manifest.get("source_commit"),
        "output_dir": str(output),
        "row_count": len(rows),
        "opportunity_rows": len(opportunity),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
