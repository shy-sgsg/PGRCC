"""Contract tests for the synthetic two-channel complex calibration sanity runner."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "run_two_channel_complex_calibration_sanity.py"
CONFIG = REPO_ROOT / "configs" / "research" / "equivalent_vs_physical_calibration_pilot.json"
EXPECTED_CASES = {"T1", "T2", "T3", "T4", "T5"}
EXPECTED_MODES = {"Mode-A", "Mode-B"}


def run_sanity(tmp_path: Path) -> Path:
    output_root = tmp_path / "sanity"
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


def test_sanity_runner_writes_manifest_with_all_cases_modes_and_schema(tmp_path: Path) -> None:
    output_root = run_sanity(tmp_path)

    manifest = json.loads((output_root / "sanity_manifest.json").read_text(encoding="utf-8"))
    assert manifest["sanity_status"] == "passed"
    assert {case["case_id"] for case in manifest["cases"]} == EXPECTED_CASES
    assert {mode["mode_id"] for mode in manifest["modes"]} == EXPECTED_MODES
    assert manifest["theory_coherence_floor"]["status"] == "passed"
    assert manifest["theory_coherence_floor"]["value"] >= manifest["thresholds"]["coherence_floor_min"]

    for filename in (
        "sanity_manifest.json",
        "gamma_recovery.csv",
        "clutter_metrics.csv",
        "target_transfer.csv",
    ):
        assert (output_root / filename).is_file()


def test_estimator_inputs_exclude_truth_and_target_truth(tmp_path: Path) -> None:
    output_root = run_sanity(tmp_path)
    manifest = json.loads((output_root / "sanity_manifest.json").read_text(encoding="utf-8"))

    forbidden = ("truth", "target_truth", "system_truth", "gamma_true")
    for entry in manifest["estimator_input_audit"]:
        assert entry["truth_used_in_estimator"] is False
        joined = " ".join(entry["estimator_input_paths"]).lower()
        assert not any(term in joined for term in forbidden)
        assert entry["estimator_input_paths"] == ["F1", "F2", "clutter_support"]


def test_gamma_recovery_records_explicit_statuses_for_t1_to_t5(tmp_path: Path) -> None:
    output_root = run_sanity(tmp_path)
    rows = read_csv(output_root / "gamma_recovery.csv")

    assert {row["case_id"] for row in rows} == EXPECTED_CASES
    assert {row["mode"] for row in rows} == EXPECTED_MODES
    assert {"case_id", "mode", "method", "status", "gamma_error_abs_max", "assertion_status"} <= set(rows[0])
    assert all(row["status"] in {"OK", "PARTIAL", "NOT_EVALUABLE"} for row in rows)
    assert all(row["assertion_status"] == "passed" for row in rows)
    assert not any(row["method"] == "Current" for row in rows)


def test_target_contamination_case_shows_robust_mode_b_improvement(tmp_path: Path) -> None:
    output_root = run_sanity(tmp_path)
    rows = read_csv(output_root / "clutter_metrics.csv")
    t4_rows = {row["mode"]: row for row in rows if row["case_id"] == "T4"}

    assert set(t4_rows) == EXPECTED_MODES
    ordinary_on = float(t4_rows["Mode-B"]["ordinary_on_ddc_residual_power"])
    mode_b = float(t4_rows["Mode-B"]["clutter_residual_power"])
    assert t4_rows["Mode-B"]["assertion_status"] == "passed"
    assert mode_b < ordinary_on * 0.5
    assert int(t4_rows["Mode-B"]["robust_excluded_count"]) > 0


def test_target_transfer_downstream_fields_are_not_evaluable(tmp_path: Path) -> None:
    output_root = run_sanity(tmp_path)
    rows = read_csv(output_root / "target_transfer.csv")

    assert rows
    for row in rows:
        assert row["target_pd"] == "NOT_EVALUABLE"
        assert row["track_pd"] == "NOT_EVALUABLE"
        assert row["target_localization_rmse"] == "NOT_EVALUABLE"
        assert row["status"] == "NOT_EVALUABLE"
