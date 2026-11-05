from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.phase_r import r22_experiment_matrix as matrix


DESIGN = matrix.DEFAULT_DESIGN
ROOT = Path(__file__).resolve().parents[1]


def test_matrix_script_supports_direct_cli_invocation():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/phase_r/r22_experiment_matrix.py"), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--phase" in result.stdout


def test_frozen_r22_design_expands_exact_case_counts_and_h0_is_target_free():
    design = matrix.load_design(DESIGN)

    expected = {
        "engineering": 12,
        "screening": 288,
        "confirmatory": 180,
        "dense": 48,
        "bridge": 6,
        "h0": 60,
    }
    for phase, count in expected.items():
        cases = matrix.build_case_specs(design, phase)
        assert len(cases) == count
        assert all(item["phase"] == phase for item in cases)

    assert {item["target_count"] for item in matrix.build_case_specs(design, "engineering")} == {20}
    assert {item["target_count"] for item in matrix.build_case_specs(design, "dense")} == {40}
    h0_cases = matrix.build_case_specs(design, "h0")
    assert {item["target_count"] for item in h0_cases} == {0}
    assert {item["delay_ns"] for item in h0_cases} == {0.0, 4.0}


def test_design_rejects_overlapping_independent_seed_sets():
    design = matrix.load_design(DESIGN)
    design["seed_sets"]["confirmatory"] = [design["seed_sets"]["engineering"][0]]

    with pytest.raises(ValueError, match="independent seed sets overlap"):
        matrix.validate_design(design)


def test_matrix_checkpoint_resume_does_not_rerun_completed_cases(tmp_path):
    design = {
        "schema_version": 1,
        "design_id": "test-design",
        "seed_sets": {"engineering": [101], "screening": [201],
                      "confirmatory": [301], "h0": [501]},
        "method_ids": ["C0"],
        "calibration_modes": ["Mode-A"],
        "common": {"first_beam": 50, "beam_count": 1, "area": {}},
        "phases": {
            "engineering": {"seed_set": "engineering", "target_count": 20,
                            "period_count": 5, "conditions": [
                                {"condition_id": "c0", "radial_speed_mps": 2.5,
                                 "target_snr_db": 20.0, "delay_error_ns": 4.0} ]},
        },
    }
    calls = []

    def fake_runner(output_root, **kwargs):
        calls.append((output_root, kwargs["seed"]))
        return {"implementation_result": "passed",
                "status_labels": {"EVALUABLE": True},
                "all_production_branches_passed": True}

    first = matrix.run_matrix(
        design, phase="engineering", output_root=tmp_path,
        run_case_fn=fake_runner,
    )
    second = matrix.run_matrix(
        design, phase="engineering", output_root=tmp_path,
        run_case_fn=fake_runner, resume=True,
    )

    assert first["completed_cases"] == 1
    assert second["skipped_cases"] == 1
    assert len(calls) == 1


def test_matrix_persists_stable_identity_and_appends_resume_preflight_snapshot(tmp_path):
    design = {
        "schema_version": 1,
        "design_id": "execution-identity-test",
        "seed_sets": {"engineering": [101], "screening": [201],
                      "confirmatory": [301], "h0": [501]},
        "method_ids": ["C0"],
        "calibration_modes": ["Mode-A"],
        "common": {"first_beam": 50, "beam_count": 1, "area": {}},
        "phases": {"engineering": {"seed_set": "engineering", "target_count": 20,
                                     "period_count": 5, "conditions": [
                                         {"condition_id": "c0", "radial_speed_mps": 2.5,
                                          "target_snr_db": 20.0, "delay_error_ns": 4.0}]}},
    }
    output_root = tmp_path / "matrix"

    def fake_runner(case_root, **kwargs):
        case_root.mkdir(parents=True)
        return {"implementation_result": "passed", "status_labels": {"EVALUABLE": True},
                "all_production_branches_passed": True}

    identity = {"design_sha256": "design", "binary_sha256": "binary"}
    first = matrix.run_matrix(
        design, phase="engineering", output_root=output_root,
        run_case_fn=fake_runner,
        manifest_metadata={"execution_identity": identity,
                           "preflight_snapshot": {"gpu_load": 97}},
    )
    second = matrix.run_matrix(
        design, phase="engineering", output_root=output_root,
        run_case_fn=fake_runner, resume=True,
        manifest_metadata={"execution_identity": identity,
                           "preflight_snapshot": {"gpu_load": 81}},
    )

    assert first["execution_identity"] == identity
    assert second["preflight_snapshots"] == [{"gpu_load": 97}, {"gpu_load": 81}]
    with pytest.raises(ValueError, match="changed V3 source, configuration, or binary identity"):
        matrix.run_matrix(
            design, phase="engineering", output_root=output_root,
            run_case_fn=fake_runner, resume=True,
            manifest_metadata={"execution_identity": {"design_sha256": "changed"}},
        )


def test_matrix_writes_running_case_checkpoint_before_starting_runner(tmp_path):
    design = {
        "schema_version": 1,
        "design_id": "pre-run-checkpoint-design",
        "seed_sets": {"engineering": [101], "screening": [201],
                      "confirmatory": [301], "h0": [501]},
        "method_ids": ["C0"],
        "calibration_modes": ["Mode-A"],
        "common": {"first_beam": 50, "beam_count": 1, "area": {}},
        "phases": {
            "engineering": {"seed_set": "engineering", "target_count": 20,
                            "period_count": 5, "conditions": [
                                {"condition_id": "c0", "radial_speed_mps": 2.5,
                                 "target_snr_db": 20.0, "delay_error_ns": 4.0} ]},
        },
    }
    output_root = tmp_path / "matrix"
    raw_root = tmp_path / "raw"

    def inspect_checkpoint(case_root, **kwargs):
        manifest = json.loads((output_root / "matrix_manifest.json").read_text())
        assert manifest["status"] == "RUNNING"
        assert manifest["cases"][0]["status"] == "RUNNING"
        assert Path(manifest["cases"][0]["output_root"]) == case_root
        return {"implementation_result": "passed",
                "status_labels": {"EVALUABLE": True},
                "all_production_branches_passed": True}

    result = matrix.run_matrix(
        design, phase="engineering", output_root=output_root,
        raw_output_root=raw_root, run_case_fn=inspect_checkpoint,
    )
    assert result["status"] == "COMPLETED"


def test_interrupted_case_requires_explicit_retry_and_preserves_prior_attempt(tmp_path):
    parameters = inspect.signature(matrix.run_matrix).parameters
    assert "retry_interrupted" in parameters
    design = {
        "schema_version": 1,
        "design_id": "interrupted-retry-design",
        "seed_sets": {"engineering": [101], "screening": [201],
                      "confirmatory": [301], "h0": [501]},
        "method_ids": ["C0"],
        "calibration_modes": ["Mode-A"],
        "common": {"first_beam": 50, "beam_count": 1, "area": {}},
        "phases": {
            "engineering": {"seed_set": "engineering", "target_count": 20,
                            "period_count": 5, "conditions": [
                                {"condition_id": "c0", "radial_speed_mps": 2.5,
                                 "target_snr_db": 20.0, "delay_error_ns": 4.0} ]},
        },
    }
    output_root = tmp_path / "matrix"
    raw_root = tmp_path / "raw"
    case_id = matrix.r22.case_identifier(
        phase="engineering", seed=101, speed_mps=2.5, snr_db=20.0,
        delay_ns=4.0, target_count=20, period_count=5,
    )
    prior_output = raw_root / "engineering" / case_id / "attempt_001"
    prior_output.mkdir(parents=True)
    (prior_output / "partial.log").write_text("interrupted", encoding="utf-8")
    output_root.mkdir()
    manifest = {
        "schema_version": 1,
        "design_id": design["design_id"],
        "design_sha256": matrix._design_digest(design),
        "phase": "engineering",
        "status": "RUNNING",
        "planned_case_count": 1,
        "cases": [{"case_id": case_id, "condition_id": "c0", "seed": 101,
                   "attempt": 1, "status": "RUNNING",
                   "output_root": str(prior_output), "started_at_utc": "earlier"}],
    }
    (output_root / "matrix_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    runner_calls = []

    def successful_runner(case_root, **kwargs):
        runner_calls.append(case_root)
        case_root.mkdir(parents=True)
        (case_root / "result.csv").write_text("hits\n8\n", encoding="utf-8")
        return {"implementation_result": "passed",
                "status_labels": {"EVALUABLE": True},
                "all_production_branches_passed": True}

    interrupted = matrix.run_matrix(
        design, phase="engineering", output_root=output_root,
        raw_output_root=raw_root, run_case_fn=successful_runner, resume=True,
    )
    assert interrupted["status"] == "INTERRUPTED_REVIEW_REQUIRED"
    assert interrupted["cases"][0]["status"] == "INTERRUPTED"
    assert not runner_calls
    assert (prior_output / "partial.log").is_file()

    retried = matrix.run_matrix(
        design, phase="engineering", output_root=output_root,
        raw_output_root=raw_root, run_case_fn=successful_runner, resume=True,
        retry_interrupted=True,
    )
    assert retried["status"] == "COMPLETED"
    assert len(retried["cases"]) == 2
    assert retried["cases"][0]["status"] == "INTERRUPTED"
    assert retried["cases"][1]["attempt"] == 2
    assert len(runner_calls) == 1
    assert (prior_output / "partial.log").is_file()


def test_retry_attempt_number_survives_compacting_prior_raw_attempt(tmp_path):
    design = {
        "schema_version": 1,
        "design_id": "compacted-interrupted-retry-design",
        "seed_sets": {"engineering": [101], "screening": [201],
                      "confirmatory": [301], "h0": [501]},
        "method_ids": ["C0"],
        "calibration_modes": ["Mode-A"],
        "common": {"first_beam": 50, "beam_count": 1, "area": {}},
        "phases": {
            "engineering": {"seed_set": "engineering", "target_count": 20,
                            "period_count": 5, "conditions": [
                                {"condition_id": "c0", "radial_speed_mps": 2.5,
                                 "target_snr_db": 20.0, "delay_error_ns": 4.0} ]},
        },
    }
    output_root = tmp_path / "matrix"
    raw_root = tmp_path / "raw"
    case_id = matrix.r22.case_identifier(
        phase="engineering", seed=101, speed_mps=2.5, snr_db=20.0,
        delay_ns=4.0, target_count=20, period_count=5,
    )
    archived_attempt = raw_root / "engineering" / case_id / "attempt_001"
    output_root.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "design_id": design["design_id"],
        "design_sha256": matrix._design_digest(design),
        "phase": "engineering",
        "status": "INTERRUPTED_REVIEW_REQUIRED",
        "planned_case_count": 1,
        "cases": [{"case_id": case_id, "condition_id": "c0", "seed": 101,
                   "attempt": 1, "status": "INTERRUPTED",
                   "output_root": str(archived_attempt),
                   "compact_archive": {"archive_path": "attempt_001.zip"},
                   "raw_output_removed_after_failure_archive": True}],
    }
    (output_root / "matrix_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def successful_runner(case_root, **kwargs):
        case_root.mkdir(parents=True)
        return {"implementation_result": "passed",
                "status_labels": {"EVALUABLE": True},
                "all_production_branches_passed": True}

    result = matrix.run_matrix(
        design, phase="engineering", output_root=output_root,
        raw_output_root=raw_root, run_case_fn=successful_runner, resume=True,
        retry_interrupted=True,
    )

    assert result["status"] == "COMPLETED"
    assert result["cases"][0]["status"] == "INTERRUPTED"
    assert result["cases"][1]["attempt"] == 2
    assert Path(result["cases"][1]["output_root"]).name == "attempt_002"
    assert not archived_attempt.exists()


def test_compact_matrix_archives_passed_case_and_checks_archive_before_resume(tmp_path):
    parameters = inspect.signature(matrix.run_matrix).parameters
    assert {"raw_output_root", "compact_archive_root"} <= set(parameters), (
        "matrix runner cannot separate temporary outputs from compact evidence"
    )

    design = {
        "schema_version": 1,
        "design_id": "compact-test-design",
        "seed_sets": {"engineering": [101], "screening": [201],
                      "confirmatory": [301], "h0": [501]},
        "method_ids": ["C0"],
        "calibration_modes": ["Mode-A"],
        "common": {"first_beam": 50, "beam_count": 1, "area": {}},
        "phases": {
            "engineering": {"seed_set": "engineering", "target_count": 20,
                            "period_count": 5, "conditions": [
                                {"condition_id": "c0", "radial_speed_mps": 2.5,
                                 "target_snr_db": 20.0, "delay_error_ns": 4.0} ]},
        },
    }
    output_root = tmp_path / "matrix"
    raw_root = tmp_path / "raw"
    archive_root = tmp_path / "archives"
    calls = []

    def fake_runner(case_root, **kwargs):
        calls.append(case_root)
        (case_root / "audit").mkdir(parents=True)
        (case_root / "audit" / "result.csv").write_text("hits\n8\n", encoding="utf-8")
        (case_root / "audit" / "plot.png").write_bytes(b"plot")
        return {"implementation_result": "passed",
                "status_labels": {"EVALUABLE": True},
                "all_production_branches_passed": True}

    first = matrix.run_matrix(
        design, phase="engineering", output_root=output_root,
        raw_output_root=raw_root, compact_archive_root=archive_root,
        run_case_fn=fake_runner,
    )

    record = first["cases"][0]
    archive_path = Path(record["compact_archive"]["archive_path"])
    assert first["status"] == "COMPLETED"
    assert record["compact_archive"]["raw_case_removed"] is True
    assert not Path(record["output_root"]).exists()
    assert archive_path.is_file()
    assert record["raw_output_removed_after_archive_verification"] is True

    second = matrix.run_matrix(
        design, phase="engineering", output_root=output_root,
        raw_output_root=raw_root, compact_archive_root=archive_root,
        run_case_fn=fake_runner, resume=True,
    )
    assert second["skipped_cases"] == 1
    assert len(calls) == 1

    archive_path.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="archive SHA-256 mismatch"):
        matrix.run_matrix(
            design, phase="engineering", output_root=output_root,
            raw_output_root=raw_root, compact_archive_root=archive_root,
            run_case_fn=fake_runner, resume=True,
        )
    assert len(calls) == 1


def test_matrix_resumes_archive_failure_without_rerunning_passed_case(tmp_path, monkeypatch):
    design = {
        "schema_version": 1,
        "design_id": "archive-resume-test-design",
        "seed_sets": {"engineering": [101], "screening": [201],
                      "confirmatory": [301], "h0": [501]},
        "method_ids": ["C0"],
        "calibration_modes": ["Mode-A"],
        "common": {"first_beam": 50, "beam_count": 1, "area": {}},
        "phases": {
            "engineering": {"seed_set": "engineering", "target_count": 20,
                            "period_count": 5, "conditions": [
                                {"condition_id": "c0", "radial_speed_mps": 2.5,
                                 "target_snr_db": 20.0, "delay_error_ns": 4.0} ]},
        },
    }
    output_root = tmp_path / "matrix"
    raw_root = tmp_path / "raw"
    archive_root = tmp_path / "archives"
    calls = []

    def fake_runner(case_root, **kwargs):
        calls.append(case_root)
        (case_root / "audit").mkdir(parents=True)
        (case_root / "audit" / "result.csv").write_text("hits\n8\n", encoding="utf-8")
        return {"implementation_result": "passed",
                "status_labels": {"EVALUABLE": True},
                "all_production_branches_passed": True}

    real_archive = matrix.archive_case_directory
    archive_attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal archive_attempts
        archive_attempts += 1
        if archive_attempts == 1:
            raise OSError("simulated archive interruption")
        return real_archive(*args, **kwargs)

    monkeypatch.setattr(matrix, "archive_case_directory", fail_once)
    first = matrix.run_matrix(
        design, phase="engineering", output_root=output_root,
        raw_output_root=raw_root, compact_archive_root=archive_root,
        run_case_fn=fake_runner,
    )

    assert first["status"] == "ARCHIVE_PENDING"
    assert first["cases"][0]["status"] == "PASSED"
    assert first["cases"][0]["compact_archive_status"] == "PENDING"
    raw_case = Path(first["cases"][0]["output_root"])
    assert raw_case.is_dir()

    second = matrix.run_matrix(
        design, phase="engineering", output_root=output_root,
        raw_output_root=raw_root, compact_archive_root=archive_root,
        run_case_fn=fake_runner, resume=True,
    )

    assert second["status"] == "COMPLETED"
    assert second["skipped_cases"] == 1
    assert len(calls) == 1
    assert not raw_case.exists()
    assert second["cases"][0]["compact_archive_status"] == "VERIFIED_AND_RAW_REMOVED"


def _v3_small_design(design_id: str, condition_count: int = 2):
    return {
        "schema_version": 1,
        "design_id": design_id,
        "seed_sets": {"engineering": [101], "screening": [201],
                      "confirmatory": [301], "h0": [501]},
        "method_ids": ["C0"],
        "calibration_modes": ["Mode-A"],
        "common": {"first_beam": 50, "beam_count": 1, "area": {}},
        "phases": {
            "screening": {"seed_set": "screening", "target_count": 20,
                          "period_count": 5, "conditions": [
                              {"condition_id": f"c{index}", "radial_speed_mps": 2.5 + index,
                               "target_snr_db": 20.0, "delay_error_ns": 4.0}
                              for index in range(condition_count)]},
        },
    }


def test_v3_archives_method_gap_and_continues_to_next_case(tmp_path):
    design = _v3_small_design("v3-method-gap-continuation")
    output_root = tmp_path / "v3-matrix"
    raw_root = tmp_path / "v3-raw"
    archive_root = tmp_path / "v3-archives"
    calls = []

    def fake_runner(case_root, **kwargs):
        calls.append(kwargs["snr_db"])
        case_root.mkdir(parents=True)
        (case_root / "result.txt").write_text("native-run-placeholder")
        return {"case_index": len(calls)}

    def classify_case(case_root, result):
        state = "CASE_VALID_WITH_METHOD_GAPS" if result["case_index"] == 1 else "CASE_VALID"
        return {"scenario_status": state, "continue_matrix": True,
                "method_units": [{"method_id": "C0", "mode": "Mode-A",
                                  "status": "REFERENCE_NOT_EVALUABLE" if result["case_index"] == 1 else "EVALUABLE"}]}

    result = matrix.run_matrix(
        design, phase="screening", output_root=output_root,
        raw_output_root=raw_root, compact_archive_root=archive_root,
        failure_archive_root=archive_root / "failures",
        run_case_fn=fake_runner, case_evaluator_fn=classify_case,
    )

    assert result["status"] == "COMPLETED"
    assert [row["status"] for row in result["cases"]] == ["PASSED_WITH_METHOD_GAPS", "PASSED"]
    assert [row["case_evaluation"]["scenario_status"] for row in result["cases"]] == [
        "CASE_VALID_WITH_METHOD_GAPS", "CASE_VALID",
    ]
    assert len(calls) == 2
    assert result["cases"][0]["compact_archive_status"] == "VERIFIED_AND_RAW_REMOVED"


def test_v3_resume_sha_checks_and_skips_terminal_method_gap_case(tmp_path):
    design = _v3_small_design("v3-method-gap-resume", condition_count=1)
    output_root = tmp_path / "v3-matrix"
    raw_root = tmp_path / "v3-raw"
    archive_root = tmp_path / "v3-archives"
    calls = []

    def fake_runner(case_root, **kwargs):
        calls.append(case_root)
        case_root.mkdir(parents=True)
        (case_root / "result.txt").write_text("case evidence")
        return {}

    def classify_case(case_root, result):
        return {"scenario_status": "CASE_VALID_WITH_METHOD_GAPS", "continue_matrix": True,
                "method_units": [{"method_id": "C0", "mode": "Mode-A",
                                  "status": "REFERENCE_NOT_EVALUABLE"}]}

    first = matrix.run_matrix(
        design, phase="screening", output_root=output_root,
        raw_output_root=raw_root, compact_archive_root=archive_root,
        failure_archive_root=archive_root / "failures",
        run_case_fn=fake_runner, case_evaluator_fn=classify_case,
    )
    second = matrix.run_matrix(
        design, phase="screening", output_root=output_root,
        raw_output_root=raw_root, compact_archive_root=archive_root,
        failure_archive_root=archive_root / "failures",
        run_case_fn=fake_runner, case_evaluator_fn=classify_case, resume=True,
    )

    assert first["cases"][0]["status"] == "PASSED_WITH_METHOD_GAPS"
    assert second["skipped_cases"] == 1
    assert len(calls) == 1
    archive = Path(first["cases"][0]["compact_archive"]["archive_path"])
    archive.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="archive SHA-256 mismatch"):
        matrix.run_matrix(
            design, phase="screening", output_root=output_root,
            raw_output_root=raw_root, compact_archive_root=archive_root,
            failure_archive_root=archive_root / "failures",
            run_case_fn=fake_runner, case_evaluator_fn=classify_case, resume=True,
        )
    assert len(calls) == 1


def test_v3_global_failure_is_archived_separately_and_stops(tmp_path):
    design = _v3_small_design("v3-global-failure-archive", condition_count=1)
    output_root = tmp_path / "v3-matrix"
    raw_root = tmp_path / "v3-raw"
    archive_root = tmp_path / "v3-archives"
    failure_root = archive_root / "failures"

    def fake_runner(case_root, **kwargs):
        case_root.mkdir(parents=True)
        (case_root / "shared-gate.json").write_text('{"status":"failed"}')
        return {"case_index": 1}

    def classify_case(case_root, result):
        return {"scenario_status": "CASE_INVALID", "continue_matrix": False,
                "scenario_reason": "shared_area_gate_failed", "method_units": []}

    result = matrix.run_matrix(
        design, phase="screening", output_root=output_root,
        raw_output_root=raw_root, compact_archive_root=archive_root,
        failure_archive_root=failure_root,
        run_case_fn=fake_runner, case_evaluator_fn=classify_case,
    )

    record = result["cases"][0]
    assert result["status"] == "GATE_FAILED"
    assert record["status"] == "GATE_FAILED"
    assert Path(record["compact_archive"]["archive_path"]).is_relative_to(failure_root)
    assert not Path(record["output_root"]).exists()
    with pytest.raises(RuntimeError, match="phase previously failed its gate"):
        matrix.run_matrix(
            design, phase="screening", output_root=output_root,
            raw_output_root=raw_root, compact_archive_root=archive_root,
            failure_archive_root=failure_root,
            run_case_fn=fake_runner, case_evaluator_fn=classify_case, resume=True,
        )
def test_v2_completed_with_gaps_fails_and_gate_failed_resume_is_refused(tmp_path):
    assert matrix._case_state("screening", {
        "implementation_result": "completed_with_gaps",
        "status_labels": {"EVALUABLE": False},
        "all_production_branches_passed": False,
    }) == "GATE_FAILED"

    design = _v3_small_design("v2-fail-closed-regression", condition_count=1)
    output_root = tmp_path / "matrix"
    output_root.mkdir()
    (output_root / "matrix_manifest.json").write_text(json.dumps({
        "schema_version": 1, "design_id": design["design_id"],
        "design_sha256": matrix._design_digest(design), "phase": "screening",
        "status": "GATE_FAILED", "planned_case_count": 1, "cases": [],
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="phase previously failed its gate"):
        matrix.run_matrix(design, phase="screening", output_root=output_root, resume=True)


def test_case_gate_requires_explicit_production_branch_success():
    result = {
        "implementation_result": "passed",
        "status_labels": {"EVALUABLE": True},
    }

    assert matrix._case_state("engineering", result) == "GATE_FAILED"


def test_h0_case_can_be_evaluable_with_target_only_branch_gaps():
    result = {
        "implementation_result": "completed_with_gaps",
        "status_labels": {"EVALUABLE": True},
        "evaluation_summary": {"independent_h0_seed_rows": 10},
        "all_production_branches_passed": False,
    }

    assert matrix._case_state("h0", result) == "H0_EVALUABLE_WITH_GAPS"
