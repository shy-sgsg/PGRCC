"""Contract tests for the equivalent-vs-physical mechanism pilot."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "run_equivalent_vs_physical_calibration.py"
CONFIG = REPO_ROOT / "configs" / "research" / "equivalent_vs_physical_calibration_pilot.json"

EXPECTED_MECHANISMS = {
    "pure_equivalent_mismatch",
    "fast_time_channel_delay",
    "servo_pointing",
    "platform_velocity",
    "true_decorrelation",
}
EXPECTED_MODES = {"Mode-A", "Mode-B"}
EXPECTED_METHODS = {
    "M0",
    "M1",
    "M2",
    "M3",
    "M4",
    "M5",
    "M6",
    "P1",
    "P2",
    "PK",
    "PK+R",
}
EXPECTED_FILES = {
    "manifest.json",
    "method_contract.csv",
    "gamma_recovery.csv",
    "clutter_metrics.csv",
    "target_transfer.csv",
    "detection_metrics.csv",
    "track_metrics.csv",
    "decision_matrix.csv",
    "report.md",
}
ALLOWED_DECISIONS = {
    "GO_HIERARCHICAL_CALIBRATION",
    "GO_EQUIVALENT_CALIBRATION_ONLY",
    "GO_PHYSICAL_CALIBRATION_ONLY",
    "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE",
}
PHYSICAL_STATUS_VALUES = {
    "RADAR_ESTIMATED",
    "SENSOR_PRIOR_ONLY",
    "PRIOR_PLUS_RADAR_RESIDUAL",
    "KNOWN_TRUTH",
    "NOT_EVALUABLE",
}


def run_pilot(tmp_path: Path) -> Path:
    output_root = tmp_path / "pilot"
    subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--config",
            str(CONFIG),
            "--output-root",
            str(output_root),
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    return output_root


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_pilot_emits_required_manifest_files_cases_modes_and_truth_blind_audit(tmp_path: Path) -> None:
    output_root = run_pilot(tmp_path)

    for filename in EXPECTED_FILES:
        assert (output_root / filename).is_file(), filename

    manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pilot_status"] == "passed"
    assert manifest["ai_training"] is False
    assert manifest["router_enabled"] is False
    assert manifest["raw_arrays_written"] is False
    assert {case["mechanism"] for case in manifest["cases"]} == EXPECTED_MECHANISMS
    assert {mode["mode_id"] for mode in manifest["modes"]} == EXPECTED_MODES
    assert manifest["decision_label"] in ALLOWED_DECISIONS
    assert manifest["decision_label"] != "GO_PHYSICS_AI"
    assert manifest["truth_blind_input_audit"]["status"] == "passed"

    forbidden = ("truth", "target_truth", "system_truth", "gamma_true")
    for entry in manifest["truth_blind_input_audit"]["entries"]:
        assert entry["truth_used_in_estimator"] is False
        joined = " ".join(entry["estimator_input_paths"]).lower()
        assert not any(term in joined for term in forbidden)


def test_method_contract_lists_required_equivalent_and_physical_methods(tmp_path: Path) -> None:
    output_root = run_pilot(tmp_path)
    rows = read_csv(output_root / "method_contract.csv")

    assert {row["method_id"] for row in rows} == EXPECTED_METHODS
    method_names = {row["method_id"]: row["method_name"] for row in rows}
    assert method_names["M0"] == "uncalibrated_subtraction_proxy"
    assert method_names["M1"] == "ordinary_complex_subtraction"
    assert method_names["M0"] != "Current"
    assert method_names["M1"] != "Current"
    assert method_names["M2"] == "SCC"
    assert method_names["M3"] == "DDC"
    assert method_names["M4"] == "Robust DDC"
    assert method_names["M5"] == "DDC-RB"
    assert method_names["M6"] == "Robust DDC-RB"
    assert method_names["P1"] == "blind physical"
    assert method_names["P2"] == "physical+robust"
    assert method_names["PK"] == "known physical"
    assert method_names["PK+R"] == "known physical+robust"
    assert all(row["ai_training"] == "false" for row in rows)


def test_fast_time_delay_is_separate_range_registration_not_doppler_only(tmp_path: Path) -> None:
    output_root = run_pilot(tmp_path)
    rows = read_csv(output_root / "gamma_recovery.csv")
    delay_rows = [row for row in rows if row["mechanism"] == "fast_time_channel_delay"]

    assert delay_rows
    assert {row["mode"] for row in delay_rows if row["method_id"].startswith("M")} == EXPECTED_MODES
    assert any(row["method_id"] == "P1" and row["physical_status"] == "RADAR_ESTIMATED" for row in delay_rows)
    assert any(row["method_id"] == "P2" and row["physical_status"] == "RADAR_ESTIMATED" for row in delay_rows)
    assert any(row["method_id"] == "PK" and row["physical_status"] == "KNOWN_TRUTH" for row in delay_rows)
    assert any(row["method_id"] == "PK+R" and row["physical_status"] == "KNOWN_TRUTH" for row in delay_rows)
    assert all(row["physical_observable"] == "fast_time_range_registration" for row in delay_rows)
    assert all(row["doppler_only_claim"] == "false" for row in delay_rows)
    physical_delay_rows = [
        row for row in delay_rows if row["method_id"] in {"P1", "P2", "PK", "PK+R"}
    ]
    assert any(abs(float(row["range_registration_error_samples"])) < 1e-12 for row in physical_delay_rows)


def test_physical_residual_rows_are_kept_for_explicit_status_vocabulary(tmp_path: Path) -> None:
    output_root = run_pilot(tmp_path)
    rows = read_csv(output_root / "clutter_metrics.csv")
    delay_rows = [row for row in rows if row["mechanism"] == "fast_time_channel_delay"]

    residual_rows = {
        row["method_id"]: row
        for row in delay_rows
        if row["method_id"] in {"P2", "PK+R"}
    }
    assert residual_rows["P2"]["status"] == "RADAR_ESTIMATED"
    assert residual_rows["PK+R"]["status"] == "KNOWN_TRUTH"
    assert all(row["status"] != "NOT_EVALUABLE" for row in residual_rows.values())


def test_mechanism_rows_preserve_physical_state_and_decorrelation_boundaries(tmp_path: Path) -> None:
    output_root = run_pilot(tmp_path)
    gamma_rows = read_csv(output_root / "gamma_recovery.csv")
    clutter_rows = read_csv(output_root / "clutter_metrics.csv")

    mechanisms = {row["mechanism"] for row in gamma_rows}
    assert EXPECTED_MECHANISMS <= mechanisms

    servo = [row for row in gamma_rows if row["mechanism"] == "servo_pointing"]
    velocity = [row for row in gamma_rows if row["mechanism"] == "platform_velocity"]
    decorrelation = [row for row in clutter_rows if row["mechanism"] == "true_decorrelation"]

    assert any(row["physical_observable"] == "servo_pointing_bias_deg" for row in servo)
    assert any(row["physical_observable"] == "platform_velocity_bias_mps" for row in velocity)
    assert any(row["method_id"] == "P1" and row["physical_status"] == "SENSOR_PRIOR_ONLY" for row in servo)
    assert any(row["method_id"] == "P1" and row["physical_status"] == "SENSOR_PRIOR_ONLY" for row in velocity)
    assert all(row["physical_status"] in PHYSICAL_STATUS_VALUES for row in servo + velocity)
    assert all(
        row["physical_status"] != "RADAR_ESTIMATED"
        for row in servo + velocity
        if row["method_id"] in {"P1", "P2"}
    )
    assert decorrelation
    floor_rows = [
        row for row in decorrelation if row["mode"] == "Mode-A" and row["method_id"] in {"M2", "M6"}
    ]
    assert all(float(row["single_coefficient_residual_floor"]) > 0.0 for row in floor_rows)
    assert all(row["irreducible_decorrelation_status"] == "OK" for row in floor_rows)


def test_downstream_detection_track_metrics_are_not_invented(tmp_path: Path) -> None:
    output_root = run_pilot(tmp_path)
    detection_rows = read_csv(output_root / "detection_metrics.csv")
    track_rows = read_csv(output_root / "track_metrics.csv")
    target_rows = read_csv(output_root / "target_transfer.csv")

    allowed_status = {"NOT_EVALUABLE", "historical_reference"}
    assert detection_rows
    assert track_rows
    assert target_rows
    assert all(row["status"] in allowed_status for row in detection_rows)
    assert all(row["status"] in allowed_status for row in track_rows)
    assert all(row["status"] in allowed_status for row in target_rows)
    assert all(row["pd"] in {"NOT_EVALUABLE", "historical_reference"} for row in detection_rows)
    assert all(row["pfa"] in {"NOT_EVALUABLE", "historical_reference"} for row in detection_rows)
    assert all(row["track_pd"] in {"NOT_EVALUABLE", "historical_reference"} for row in track_rows)
    assert all("mechanism-only pilot" in row["reason"] for row in detection_rows if row["status"] == "NOT_EVALUABLE")
    assert all("production TrackManager" in row["reason"] for row in track_rows if row["status"] == "NOT_EVALUABLE")


def test_decision_matrix_uses_allowed_non_ai_label_and_records_gate_statuses(tmp_path: Path) -> None:
    output_root = run_pilot(tmp_path)
    rows = read_csv(output_root / "decision_matrix.csv")
    report = (output_root / "report.md").read_text(encoding="utf-8")

    assert rows
    labels = {row["decision_label"] for row in rows}
    assert len(labels) == 1
    decision = labels.pop()
    assert decision in ALLOWED_DECISIONS
    assert decision == "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE"
    assert "GO_PHYSICS_AI" not in report
    assert "GO_COUPLED_PHYSICAL_STATE_STUDY" not in report
    assert {row["gate_item"] for row in rows} >= {
        "Class-E coverage",
        "Class-P need",
        "Class-D limit",
        "Downstream safety",
        "AI gate",
    }
    assert all(row["status"] in {"PASS", "CLOSED", "NOT_EVALUABLE", "historical_reference"} for row in rows)


def test_mode_separation_and_physical_observable_audits_are_explicit(tmp_path: Path) -> None:
    output_root = run_pilot(tmp_path)
    manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
    gamma_rows = read_csv(output_root / "gamma_recovery.csv")

    mode_audit = manifest["mode_separation_audit"]
    assert mode_audit["Mode-A"]["estimator_source"] == "OFF=C+N"
    assert mode_audit["Mode-B"]["estimator_source"] == "ON=S+C+N"
    assert mode_audit["Mode-A"]["off_on_observables_distinct"] is True
    assert mode_audit["Mode-B"]["off_on_observables_distinct"] is True

    delay_audit = manifest["mechanism_observable_audit"]["fast_time_channel_delay"]
    assert delay_audit["phase_model"] == "exp(-j*2*pi*f*delay_samples)"
    assert delay_audit["frequency_axis_count"] >= 2
    assert delay_audit["physical_estimator_input_paths"] == [
        "F1",
        "F2",
        "fast_time_frequency_cycles_per_sample",
    ]

    physical_audit = manifest["physical_estimator_audit"]
    assert physical_audit["blind_rows_use_observable_values"] is True
    assert physical_audit["blind_rows_read_evaluator_truth"] is False
    delay_p2 = next(
        row
        for row in gamma_rows
        if row["mechanism"] == "fast_time_channel_delay" and row["method_id"] == "P2"
    )
    assert delay_p2["physical_correction_applied"] == "true"
    assert float(delay_p2["residual_power"]) >= 0.0


def test_decorrelation_floor_is_measured_from_cross_correlation_and_reached_by_local_fit(tmp_path: Path) -> None:
    output_root = run_pilot(tmp_path)
    manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
    rows = read_csv(output_root / "clutter_metrics.csv")

    audit = manifest["decorrelation_audit"]
    assert audit["empirical_coherence_source"] == "cross_correlation(F1,F2,clutter_support)"
    assert audit["floor_check_status"] == "passed"
    d1_rows = [
        row
        for row in rows
        if row["mechanism"] == "true_decorrelation"
        and row["mode"] == "Mode-A"
        and row["method_id"] in {"M2", "M6"}
    ]
    assert d1_rows
    for row in d1_rows:
        floor = float(row["single_coefficient_residual_floor"])
        residual = float(row["clutter_residual_power"])
        assert abs(residual - floor) <= max(1e-12, floor * 1e-8)
