#!/usr/bin/env python3
"""Emit compact evidence for the equivalent-vs-physical mechanism pilot."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


NOT_EVALUABLE = "NOT_EVALUABLE"
HISTORICAL_REFERENCE = "historical_reference"
ALLOWED_DECISIONS = {
    "GO_HIERARCHICAL_CALIBRATION",
    "GO_EQUIVALENT_CALIBRATION_ONLY",
    "GO_PHYSICAL_CALIBRATION_ONLY",
    "GO_COUPLED_PHYSICAL_STATE_STUDY",
}


METHOD_CONTRACT = [
    {
        "method_id": "M0",
        "method_name": "Current",
        "class": "current",
        "mode_applicability": "Mode-A;Mode-B",
        "estimator_inputs": "none",
        "truth_blind": "true",
        "ai_training": "false",
        "notes": "uncalibrated F2-F1 production-domain baseline",
    },
    {
        "method_id": "M1",
        "method_name": "ordinary subtraction",
        "class": "equivalent",
        "mode_applicability": "Mode-A;Mode-B",
        "estimator_inputs": "none",
        "truth_blind": "true",
        "ai_training": "false",
        "notes": "direct F2-F1 subtraction with no fitted Gamma",
    },
    {
        "method_id": "M2",
        "method_name": "SCC",
        "class": "equivalent",
        "mode_applicability": "Mode-A;Mode-B",
        "estimator_inputs": "F1;F2;clutter_support",
        "truth_blind": "true",
        "ai_training": "false",
        "notes": "single complex coefficient",
    },
    {
        "method_id": "M3",
        "method_name": "DDC",
        "class": "equivalent",
        "mode_applicability": "Mode-A;Mode-B",
        "estimator_inputs": "F1;F2;clutter_support",
        "truth_blind": "true",
        "ai_training": "false",
        "notes": "Doppler-dependent complex coefficient",
    },
    {
        "method_id": "M4",
        "method_name": "Robust DDC",
        "class": "equivalent",
        "mode_applicability": "Mode-A;Mode-B",
        "estimator_inputs": "F1;F2;clutter_support",
        "truth_blind": "true",
        "ai_training": "false",
        "notes": "robust DDC; Mode-B is the online contaminated-input use; target truth is not an estimator input",
    },
    {
        "method_id": "M5",
        "method_name": "DDC-RB",
        "class": "equivalent",
        "mode_applicability": "Mode-A;Mode-B",
        "estimator_inputs": "F1;F2;clutter_support",
        "truth_blind": "true",
        "ai_training": "false",
        "notes": "range-band and Doppler local complex coefficient",
    },
    {
        "method_id": "M6",
        "method_name": "Robust DDC-RB",
        "class": "equivalent",
        "mode_applicability": "Mode-A;Mode-B",
        "estimator_inputs": "F1;F2;clutter_support",
        "truth_blind": "true",
        "ai_training": "false",
        "notes": "robust DDC-RB; Mode-B is the online contaminated-input use; target truth is not an estimator input",
    },
    {
        "method_id": "P1",
        "method_name": "blind physical",
        "class": "physical",
        "mode_applicability": "Mode-A",
        "estimator_inputs": "physical_observable",
        "truth_blind": "true",
        "ai_training": "false",
        "notes": "mechanism-specific state estimate from observable metadata, not target truth",
    },
    {
        "method_id": "P2",
        "method_name": "physical+robust",
        "class": "physical",
        "mode_applicability": "Mode-B",
        "estimator_inputs": "physical_observable;F1;F2;clutter_support",
        "truth_blind": "true",
        "ai_training": "false",
        "notes": "physical correction combined with robust equivalent cleanup",
    },
    {
        "method_id": "PK",
        "method_name": "known physical",
        "class": "known_physical_upper_bound",
        "mode_applicability": "evaluator_only",
        "estimator_inputs": "known_error_state",
        "truth_blind": "false",
        "ai_training": "false",
        "notes": "known-error correction upper bound; not deployable",
    },
    {
        "method_id": "PK+R",
        "method_name": "known physical+robust",
        "class": "known_physical_upper_bound",
        "mode_applicability": "evaluator_only",
        "estimator_inputs": "known_error_state;F1;F2;clutter_support",
        "truth_blind": "false",
        "ai_training": "false",
        "notes": "known physical upper bound followed by robust residual calibration",
    },
]


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _pilot_not_evaluable_rows(cases: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    detection_rows: list[dict[str, str]] = []
    track_rows: list[dict[str, str]] = []
    for case in cases:
        detection_rows.append(
            {
                "case_id": case["case_id"],
                "mechanism": case["mechanism"],
                "method_id": "pilot",
                "status": NOT_EVALUABLE,
                "pd": NOT_EVALUABLE,
                "pfa": NOT_EVALUABLE,
                "angle_rmse": NOT_EVALUABLE,
                "position_rmse": NOT_EVALUABLE,
                "velocity_rmse": NOT_EVALUABLE,
                "reason": "mechanism-only pilot has no production detector, CFAR valid-CUT denominator, or truth-matched detections",
                "evidence_path": "",
            }
        )
        track_rows.append(
            {
                "case_id": case["case_id"],
                "mechanism": case["mechanism"],
                "method_id": "pilot",
                "status": NOT_EVALUABLE,
                "track_pd": NOT_EVALUABLE,
                "track_id_switch": NOT_EVALUABLE,
                "track_position_rmse": NOT_EVALUABLE,
                "track_velocity_rmse": NOT_EVALUABLE,
                "reason": "production TrackManager and PIPE audit were not run in this mechanism-only pilot",
                "evidence_path": "",
            }
        )
    detection_rows.append(
        {
            "case_id": "historical_channel_delay",
            "mechanism": "fast_time_channel_delay",
            "method_id": "PK",
            "status": HISTORICAL_REFERENCE,
            "pd": HISTORICAL_REFERENCE,
            "pfa": HISTORICAL_REFERENCE,
            "angle_rmse": HISTORICAL_REFERENCE,
            "position_rmse": HISTORICAL_REFERENCE,
            "velocity_rmse": HISTORICAL_REFERENCE,
            "reason": "historical channel-delay formal evidence is retained with its original source identity and limitations",
            "evidence_path": "docs/AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md",
        }
    )
    track_rows.append(
        {
            "case_id": "historical_channel_delay",
            "mechanism": "fast_time_channel_delay",
            "method_id": "PK",
            "status": HISTORICAL_REFERENCE,
            "track_pd": HISTORICAL_REFERENCE,
            "track_id_switch": HISTORICAL_REFERENCE,
            "track_position_rmse": HISTORICAL_REFERENCE,
            "track_velocity_rmse": HISTORICAL_REFERENCE,
            "reason": "historical TrackManager/PIPE limits are referenced, not recomputed or promoted",
            "evidence_path": "docs/AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md",
        }
    )
    return detection_rows, track_rows


def _target_rows(cases: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for case in cases:
        for mode in ("Mode-A", "Mode-B"):
            rows.append(
                {
                    "case_id": case["case_id"],
                    "mechanism": case["mechanism"],
                    "mode": mode,
                    "method_id": "pilot",
                    "status": NOT_EVALUABLE,
                    "target_pd": NOT_EVALUABLE,
                    "target_transfer_status": NOT_EVALUABLE,
                    "target_localization_rmse": NOT_EVALUABLE,
                    "reason": "mechanism-only pilot has no production target injection/detection/transfer measurement",
                    "evidence_path": "",
                }
            )
    rows.append(
        {
            "case_id": "historical_target_transfer",
            "mechanism": "target_transfer_archive",
            "mode": "historical",
            "method_id": "archive",
            "status": HISTORICAL_REFERENCE,
            "target_pd": HISTORICAL_REFERENCE,
            "target_transfer_status": HISTORICAL_REFERENCE,
            "target_localization_rmse": HISTORICAL_REFERENCE,
            "reason": "historical target-transfer audit is preserved as an archive reference only",
            "evidence_path": "docs/AI_CSI_26_TargetTransfer与目标保护审计.md",
        }
    )
    return rows


def _decision_rows(observations: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    thresholds = observations.get("thresholds", {})
    residual_limit = float(thresholds.get("residual_power_max", float("inf")))
    pure_equivalent_rows = [
        row
        for row in observations["clutter_rows"]
        if row["mechanism"] == "pure_equivalent_mismatch"
        and row["method_id"] in {"M2", "M3", "M4", "M5", "M6"}
    ]
    equivalent_ok = any(
        row["status"] in {"OK", "PARTIAL"}
        and row["clutter_residual_power"] not in {"", "nan", "NaN"}
        and float(row["clutter_residual_power"]) <= residual_limit
        for row in pure_equivalent_rows
    )
    physical_mechanisms = {"fast_time_channel_delay", "servo_pointing", "platform_velocity"}
    physical_observable_rows = all(
        any(
            row["mechanism"] == mechanism
            and row["method_id"] in {"P1", "P2"}
            and row["physical_status"] == "OK"
            for row in observations["gamma_rows"]
        )
        for mechanism in physical_mechanisms
    )
    physical_correction_evaluable = (
        physical_observable_rows
        and observations.get("physical_correction_status", NOT_EVALUABLE) == "measured"
    )
    decorrelation_ok = any(
        row["mechanism"] == "true_decorrelation" and row["irreducible_decorrelation_status"] == "OK"
        for row in observations["clutter_rows"]
    )
    downstream_status = NOT_EVALUABLE

    if equivalent_ok and physical_correction_evaluable:
        decision = "GO_HIERARCHICAL_CALIBRATION"
    elif equivalent_ok:
        decision = "GO_COUPLED_PHYSICAL_STATE_STUDY" if physical_observable_rows else "GO_EQUIVALENT_CALIBRATION_ONLY"
    elif physical_correction_evaluable:
        decision = "GO_PHYSICAL_CALIBRATION_ONLY"
    else:
        decision = "GO_COUPLED_PHYSICAL_STATE_STUDY"
    if decision not in ALLOWED_DECISIONS:
        raise ValueError(f"invalid decision label: {decision}")

    rows = [
        {
            "gate_item": "Class-E coverage",
            "status": "PASS" if equivalent_ok else NOT_EVALUABLE,
            "decision_label": decision,
            "evidence": "pure equivalent rows show a measured M2-M6 residual at or below the configured residual limit",
        },
        {
            "gate_item": "Class-P need",
            "status": "PASS" if physical_correction_evaluable else NOT_EVALUABLE,
            "decision_label": decision,
            "evidence": (
                "delay, servo, and velocity retain separate physical observables, but no production physical correction tap was run"
                if physical_observable_rows and not physical_correction_evaluable
                else "physical correction evidence is measured"
            ),
        },
        {
            "gate_item": "Class-D limit",
            "status": "PASS" if decorrelation_ok else NOT_EVALUABLE,
            "decision_label": decision,
            "evidence": "true decorrelation keeps a nonzero single-coefficient residual floor",
        },
        {
            "gate_item": "Downstream safety",
            "status": downstream_status,
            "decision_label": decision,
            "evidence": "Pd/Pfa/TrackManager/PIPE were not measured in this mechanism-only pilot",
        },
        {
            "gate_item": "AI gate",
            "status": "CLOSED",
            "decision_label": decision,
            "evidence": "non-AI deterministic/equivalent/physical pilot only; no router or training",
        },
    ]
    return decision, rows


def emit_evidence(observations: dict[str, Any], output_root: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    cases = observations["cases"]
    decision_label, decision_rows = _decision_rows(observations)
    detection_rows, track_rows = _pilot_not_evaluable_rows(cases)
    target_rows = _target_rows(cases)

    write_csv(
        output_root / "method_contract.csv",
        METHOD_CONTRACT,
        [
            "method_id",
            "method_name",
            "class",
            "mode_applicability",
            "estimator_inputs",
            "truth_blind",
            "ai_training",
            "notes",
        ],
    )
    write_csv(
        output_root / "gamma_recovery.csv",
        observations["gamma_rows"],
        [
            "case_id",
            "mechanism",
            "mode",
            "method_id",
            "method_name",
            "status",
            "gamma_error_abs_max",
            "residual_power",
            "physical_observable",
            "observable_domain",
            "calibration_scope",
            "physical_status",
            "physical_estimate",
            "physical_error",
            "range_registration_error_samples",
            "doppler_only_claim",
            "truth_used_in_estimator",
            "estimator_input_paths",
            "support_count",
            "excluded_count",
        ],
    )
    write_csv(
        output_root / "clutter_metrics.csv",
        observations["clutter_rows"],
        [
            "case_id",
            "mechanism",
            "mode",
            "method_id",
            "method_name",
            "status",
            "clutter_residual_power",
            "single_coefficient_residual_floor",
            "empirical_coherence",
            "irreducible_decorrelation_status",
            "physical_state_claim",
            "reason",
        ],
    )
    write_csv(
        output_root / "target_transfer.csv",
        target_rows,
        [
            "case_id",
            "mechanism",
            "mode",
            "method_id",
            "status",
            "target_pd",
            "target_transfer_status",
            "target_localization_rmse",
            "reason",
            "evidence_path",
        ],
    )
    write_csv(
        output_root / "detection_metrics.csv",
        detection_rows,
        [
            "case_id",
            "mechanism",
            "method_id",
            "status",
            "pd",
            "pfa",
            "angle_rmse",
            "position_rmse",
            "velocity_rmse",
            "reason",
            "evidence_path",
        ],
    )
    write_csv(
        output_root / "track_metrics.csv",
        track_rows,
        [
            "case_id",
            "mechanism",
            "method_id",
            "status",
            "track_pd",
            "track_id_switch",
            "track_position_rmse",
            "track_velocity_rmse",
            "reason",
            "evidence_path",
        ],
    )
    write_csv(
        output_root / "decision_matrix.csv",
        decision_rows,
        ["gate_item", "status", "decision_label", "evidence"],
    )

    audit_entries = observations["truth_blind_entries"]
    audit_pass = all(
        not entry["truth_used_in_estimator"]
        and not any(
            token in " ".join(entry["estimator_input_paths"]).lower()
            for token in ("truth", "target_truth", "system_truth", "gamma_true")
        )
        for entry in audit_entries
    )
    manifest = {
        "schema": "equivalent_vs_physical_calibration_pilot.v1",
        "pilot_status": "passed" if audit_pass and decision_label in ALLOWED_DECISIONS else "failed",
        "decision_label": decision_label,
        "seed": observations["seed"],
        "dimensions": observations["dimensions"],
        "ai_training": False,
        "router_enabled": False,
        "native_four_channel_stap": False,
        "raw_arrays_written": False,
        "cases": cases,
        "modes": observations["modes"],
        "truth_blind_input_audit": {
            "status": "passed" if audit_pass else "failed",
            "entries": audit_entries,
        },
        "historical_references": observations["historical_references"],
        "physical_correction_status": observations.get("physical_correction_status", NOT_EVALUABLE),
        "provenance": observations.get("provenance", {}),
        "preserved_sanity_evidence": observations.get("preserved_sanity_evidence", []),
        "evidence_files": {
            "observations": "observations.json",
            "method_contract": "method_contract.csv",
            "gamma_recovery": "gamma_recovery.csv",
            "clutter_metrics": "clutter_metrics.csv",
            "target_transfer": "target_transfer.csv",
            "detection_metrics": "detection_metrics.csv",
            "track_metrics": "track_metrics.csv",
            "decision_matrix": "decision_matrix.csv",
            "report": "report.md",
        },
        "limitations": [
            "first mechanism-only pilot; no production Pd/Pfa measurement",
            "no production angle, position, velocity, or TrackManager measurement",
            "known physical rows are evaluator upper bounds, not deployable estimators",
            "blind physical rows use declared sensor-prior observables and retain nonzero evaluator error; they do not prove physical-state recovery",
            "fast-time delay is represented in a fast-time frequency/range-registration domain and is not treated as a Doppler-only Gamma estimate",
        ],
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_root / "report.md").write_text(_report_text(manifest, decision_rows), encoding="utf-8")
    return manifest


def _report_text(manifest: dict[str, Any], decision_rows: list[dict[str, str]]) -> str:
    gate_lines = "\n".join(
        f"| {row['gate_item']} | {row['status']} | {row['evidence']} |" for row in decision_rows
    )
    reference_lines = "\n".join(
        f"- `{reference['path']}` — {reference['limitation']}"
        for reference in manifest.get("historical_references", [])
    )
    return (
        "# Equivalent vs Physical Calibration Pilot\n\n"
        f"Status: `{manifest['pilot_status']}`\n\n"
        f"Decision label: `{manifest['decision_label']}`\n\n"
        f"Physical correction status: `{manifest.get('physical_correction_status', NOT_EVALUABLE)}`\n\n"
        "This is a deterministic mechanism-only pilot. It records explicit "
        "`NOT_EVALUABLE` statuses for production Pd/Pfa/angle/position/velocity/"
        "TrackManager metrics that were not run.\n\n"
        "Mode-A estimates use target-free OFF support and Mode-B estimates use the ON "
        "observable without target truth. The blind physical rows use only their "
        "declared sensor-prior observable; known-physical rows are evaluator upper "
        "bounds.\n\n"
        "Evidence files: `manifest.json`, `observations.json`, `method_contract.csv`, "
        "`gamma_recovery.csv`, `clutter_metrics.csv`, `target_transfer.csv`, "
        "`detection_metrics.csv`, `track_metrics.csv`, `decision_matrix.csv`, and "
        "`report.md`.\n\n"
        "Historical references (not promoted to new measured rows):\n"
        f"{reference_lines}\n\n"
        "| Gate item | Status | Evidence |\n"
        "|---|---|---|\n"
        f"{gate_lines}\n\n"
        "Historical channel-delay and target-transfer paths are retained as archive "
        "references with their original limitations; they are not recomputed here.\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    observations = json.loads(args.observations.read_text(encoding="utf-8"))
    manifest = emit_evidence(observations, args.output_root)
    print(json.dumps({"pilot_status": manifest["pilot_status"], "output_root": str(args.output_root)}, sort_keys=True))
    return 0 if manifest["pilot_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
