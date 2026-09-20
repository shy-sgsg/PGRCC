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
    "GO_COUPLED_PHYSICAL_STATE_STUDY",
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
    assert method_names["M0"] == "Current"
    assert method_names["M1"] == "ordinary subtraction"
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
    assert any(row["method_id"] == "P1" and row["physical_status"] == "OK" for row in delay_rows)
    assert any(row["method_id"] == "PK" and row["physical_status"] == "OK" for row in delay_rows)
    assert all(row["physical_observable"] == "fast_time_range_registration" for row in delay_rows)
    assert all(row["doppler_only_claim"] == "false" for row in delay_rows)
    physical_delay_rows = [
        row for row in delay_rows if row["method_id"] in {"P1", "P2", "PK", "PK+R"}
    ]
    assert any(abs(float(row["range_registration_error_samples"])) < 1e-12 for row in physical_delay_rows)


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
    assert any(row["method_id"] == "P1" and row["physical_status"] == "OK" for row in servo)
    assert any(row["method_id"] == "P1" and row["physical_status"] == "OK" for row in velocity)
    assert decorrelation
    assert all(float(row["single_coefficient_residual_floor"]) > 0.0 for row in decorrelation)
    assert all(row["irreducible_decorrelation_status"] == "OK" for row in decorrelation)


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
    assert "GO_PHYSICS_AI" not in report
    assert {row["gate_item"] for row in rows} >= {
        "Class-E coverage",
        "Class-P need",
        "Class-D limit",
        "Downstream safety",
        "AI gate",
    }
    assert all(row["status"] in {"PASS", "CLOSED", "NOT_EVALUABLE", "historical_reference"} for row in rows)
