from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.phase_r.r22_v3_case_evaluator import classify_method_level_case
from scripts.phase_r.r22_v3_experiment_matrix import (
    DEFAULT_DESIGN,
    V3_1_DESIGN,
    V3_1_DESIGN_ID,
    V2_DESIGN,
    V3_DESIGN_ID,
    V3_2_DESIGN_FILE_SHA256,
    build_execution_metadata,
    expected_method_units,
    load_v3_design,
    validate_design_identity,
    validate_output_roots,
    validate_resume_target,
)
from scripts.phase_r import r22_experiment_matrix as matrix
from scripts.phase_r import r22_v3_experiment_matrix as v3_runner


def _valid_run_manifest():
    return {
        "design": {
            "seed": 1932001,
            "delay_error_ns": 4.0,
            "target_count": 20,
            "period_count": 10,
        },
        "status_labels": {"IMPLEMENTED": True, "EXECUTED": True},
        "area_clutter_gate": {
            "status": "passed",
            "continuous_area_packets": 1300,
            "continuous_area_samples": 6508782,
            "mean_power": 0.02215967,
            "ground_range_min_m": 84286.71,
            "ground_range_max_m": 85289.21,
        },
        "evidence_gate": {
            "causal_triplets": "passed",
            "truth_used_in_estimator": False,
            "fallback_to_current": False,
        },
        "source_identity": {"commit": "source-sha"},
        "stage2_binary": {"sha256": "stage2-sha"},
        "production_binary": {
            "backend": "local Native CUDA",
            "use_corex": False,
            "sha256": "production-sha",
        },
    }


def _valid_pipeline_manifest():
    return {
        "status": "completed_with_gaps",
        "pair_identity": {
            "seed": 1932001,
            "delay_error_ns": 4.0,
            "target_id": "target-set-1",
            "background_id": "background-set-1",
            "production_identity": "native-cuda-1",
        },
        "simulation": {
            role: {"status": "passed", "returncode": 0}
            for role in ("A0_OFF", "A0_ON", "A0_TO")
        },
        "truth_used_in_estimator": False,
        "evidence_gate": {
            "causal_triplets": "passed",
            "fallback_to_current": False,
        },
    }


def _task_rows(method_id: str, mode: str, *, status: str = "EVALUABLE", reason: str = ""):
    return [
        {"method_id": method_id, "mode": mode, "role": "OFF",
         "status": "NOT_APPLICABLE_H0_ROLE", "reason": ""},
        {"method_id": method_id, "mode": mode, "role": "ON",
         "status": status, "reason": reason},
        {"method_id": method_id, "mode": mode, "role": "TO",
         "status": status, "reason": reason},
    ]


def _branch(method_id: str, mode: str, role: str):
    name = f"{method_id}_{mode.lower().replace('-', '')}_{role}"
    branch = {
        "method_id": method_id,
        "mode": mode,
        "role": role,
        "branch_name": name,
        "status": "passed",
        "production_status": "passed",
        "production_returncode": 0,
        "truth_used_in_estimator": False,
        "cfar_geometry": {
            "branch_geometry_status": "evaluable",
            "branch_summaries": {
                key: {
                    "status": "evaluable",
                    "period_count": 10,
                    "period_ids": list(range(10)),
                    "valid_cut_count": 5272800,
                }
                for key in ("union_dynamic_in_band", "union_full_band")
            },
        },
        "cfar_summary": {
            "status": "passed",
            "expected_period_count": 10,
            "valid_cut_denominator_status": "passed",
            "valid_cut_denominator_row_count": 20,
            "branch_summaries": {
                key: {
                    "status": "evaluable",
                    "period_count": 10,
                    "period_ids": list(range(10)),
                    "valid_cut_count": 5272800,
                }
                for key in ("union_dynamic_in_band", "union_full_band")
            },
            "summary_count": 10,
            "summaries": [{"hit_cells": 0} for _ in range(10)],
        },
    }
    if method_id == "C4":
        config = {
            "research_calibration_enable": "true",
            "research_calibration_method": "robust_ddc_rb",
            "research_calibration_mode": mode,
            "research_calibration_min_support": 8,
            "research_calibration_range_band_bins": 133,
            "research_calibration_robust_phase_threshold_rad": 0.35,
        }
        branch["xml_overrides"] = dict(config)
        branch["runtime_calibration_config"] = dict(config)
    return branch


def _expected():
    return [
        {"method_id": "C2", "mode": "Mode-A"},
        {"method_id": "C4", "mode": "Mode-B"},
    ]


def test_recorded_c4_support_failure_preserves_other_method_and_v2_status():
    tasks = _task_rows("C2", "Mode-A") + _task_rows(
        "C4", "Mode-B", status="NOT_EVALUABLE", reason="reference_status_not_ok"
    )
    branches = [
        _branch("C2", "Mode-A", role) for role in ("OFF", "ON", "TO")
    ] + [_branch("C4", "Mode-B", "ON")]
    reference_rows = [{
        "method_id": "C4",
        "mode": "Mode-B",
        "status": "NOT_EVALUABLE",
        "support_count": "7",
        "reason": "insufficient_robust_support",
        "truth_used_in_estimator": "false",
    }]

    result = classify_method_level_case(
        _valid_run_manifest(), _valid_pipeline_manifest(), tasks, branches,
        reference_rows, _expected(), min_support=8, recorded=True,
        original_matrix_status="GATE_FAILED",
    )

    assert result["scenario_status"] == "CASE_RECORDED_GATE_PASS_NOT_REPLAYED"
    assert result["original_matrix_status"] == "GATE_FAILED"
    assert result["applicable_count"] == 2
    assert result["evaluable_count"] == 1
    states = {(r["method_id"], r["mode"]): r["status"] for r in result["method_units"]}
    assert states == {
        ("C2", "Mode-A"): "EVALUABLE",
        ("C4", "Mode-B"): "REFERENCE_NOT_EVALUABLE",
    }
    assert result["continue_matrix"] is False


def test_shared_area_failure_cannot_be_downgraded_to_method_gap():
    run = _valid_run_manifest()
    run["area_clutter_gate"]["continuous_area_packets"] = 0
    result = classify_method_level_case(
        run, _valid_pipeline_manifest(), _task_rows("C2", "Mode-A"),
        [_branch("C2", "Mode-A", role) for role in ("OFF", "ON", "TO")],
        [], [{"method_id": "C2", "mode": "Mode-A"}], min_support=8,
    )

    assert result["scenario_status"] == "CASE_INVALID"
    assert result["method_units"][0]["status"] == "NOT_RUN_GLOBAL_GATE"
    assert result["continue_matrix"] is False


def test_unexplained_task_failure_stops_instead_of_becoming_method_gap():
    result = classify_method_level_case(
        _valid_run_manifest(), _valid_pipeline_manifest(),
        _task_rows("C2", "Mode-A", status="NOT_EVALUABLE", reason="unknown_failure"),
        [_branch("C2", "Mode-A", role) for role in ("OFF", "ON", "TO")],
        [], [{"method_id": "C2", "mode": "Mode-A"}], min_support=8,
    )

    assert result["scenario_status"] == "CASE_UNKNOWN_STOP"
    assert result["method_units"][0]["status"] == "UNKNOWN_STOP"
    assert result["continue_matrix"] is False


def test_support_failure_without_matching_gamma_evidence_is_unknown():
    result = classify_method_level_case(
        _valid_run_manifest(), _valid_pipeline_manifest(),
        _task_rows("C4", "Mode-B", status="NOT_EVALUABLE",
                   reason="reference_status_not_ok"),
        [_branch("C4", "Mode-B", "ON")], [],
        [{"method_id": "C4", "mode": "Mode-B"}], min_support=8,
    )

    assert result["scenario_status"] == "CASE_UNKNOWN_STOP"
    assert result["method_units"][0]["status"] == "UNKNOWN_STOP"
    assert result["method_units"][0]["reason"] == "task_failure_without_reference_evidence"


def test_c4_runtime_xml_cannot_lower_frozen_support_gate():
    branches = [_branch("C4", "Mode-B", role) for role in ("OFF", "ON", "TO")]
    branches[1]["xml_overrides"]["research_calibration_min_support"] = 7
    result = classify_method_level_case(
        _valid_run_manifest(), _valid_pipeline_manifest(), _task_rows("C4", "Mode-B"),
        branches, [], [{"method_id": "C4", "mode": "Mode-B"}], min_support=8,
    )

    assert result["scenario_status"] == "CASE_UNKNOWN_STOP"
    assert result["method_units"][0]["reason"] == "on_c4_min_support_mismatch"


def test_missing_or_duplicate_expected_task_rows_do_not_shrink_denominator():
    rows = _task_rows("C2", "Mode-A")
    rows.append(dict(rows[1]))
    result = classify_method_level_case(
        _valid_run_manifest(), _valid_pipeline_manifest(), rows,
        [_branch("C2", "Mode-A", role) for role in ("OFF", "ON", "TO")],
        [], _expected(), min_support=8,
    )

    assert result["applicable_count"] == 2
    assert result["scenario_status"] == "CASE_UNKNOWN_STOP"
    assert result["method_units"][-1]["status"] == "UNKNOWN_STOP"


def test_zero_delay_not_applicable_rows_are_outside_frozen_unit_denominator():
    rows = _task_rows("C0", "not_applicable")
    rows.append({"method_id": "P1", "mode": "not_applicable", "role": "ALL",
                 "status": "NOT_APPLICABLE_ZERO_DELAY_REFERENCE", "reason": ""})
    result = classify_method_level_case(
        _valid_run_manifest(), _valid_pipeline_manifest(), rows,
        [_branch("C0", "not_applicable", role) for role in ("OFF", "ON", "TO")],
        [], [{"method_id": "C0", "mode": "not_applicable"}], min_support=8,
    )

    assert result["applicable_count"] == 1
    assert result["evaluable_count"] == 1
    assert result["scenario_status"] == "CASE_VALID"


def test_v3_2_design_preserves_scientific_factors_and_freezes_exact_screening_coverage():
    v2 = matrix.load_design(V2_DESIGN)
    v3_1 = matrix.load_design(V3_1_DESIGN)
    v3 = load_v3_design(DEFAULT_DESIGN)
    v2_specs = matrix.build_case_specs(v2, "screening")
    v3_specs = matrix.build_case_specs(v3, "screening")

    assert len(v3_specs) == 288
    assert [
        matrix.r22.case_identifier(
            phase=item["phase"], seed=item["seed"], speed_mps=item["speed_mps"],
            snr_db=item["snr_db"], delay_ns=item["delay_ns"],
            target_count=item["target_count"], period_count=item["period_count"],
        ) for item in v3_specs
    ] == [
        matrix.r22.case_identifier(
            phase=item["phase"], seed=item["seed"], speed_mps=item["speed_mps"],
            snr_db=item["snr_db"], delay_ns=item["delay_ns"],
            target_count=item["target_count"], period_count=item["period_count"],
        ) for item in v2_specs
    ]
    assert sum(len(expected_method_units(v3, item)) for item in v3_specs) == 4320
    assert len(expected_method_units(v3, next(x for x in v3_specs if x["delay_ns"] == 0))) == 12
    assert len(expected_method_units(v3, next(x for x in v3_specs if x["delay_ns"] == 4))) == 18
    v3_only = {
        "design_id", "state", "supersedes_design_id", "entered_from_commit",
        "period_policy", "minimum_period_count_if_resource_gate_fails",
        "v3_1_resource_replay_provenance", "v3_2_identity_provenance",
    }
    for key in set(v2) - v3_only:
        assert v3[key] == v2[key]
    v3_2_only = {
        "design_id", "state", "supersedes_design_id", "entered_from_commit",
        "v3_2_identity_provenance",
    }
    assert {k: v for k, v in v3.items() if k not in v3_2_only} == {
        k: v for k, v in v3_1.items() if k not in v3_2_only
    }
    assert v3["period_count"] == 10
    assert "do not reduce periods" in v3["period_policy"]
    assert v3["design_id"] == V3_DESIGN_ID
    assert v3["supersedes_design_id"] == V3_1_DESIGN_ID
    prior_provenance = v3["v3_1_resource_replay_provenance"]
    assert prior_provenance["diagnostic_only"] is True
    assert prior_provenance["diagnostic_not_in_new_screening_denominator"] is True
    assert prior_provenance["scientific_factors_changed"] is False
    assert "all 288 cases" in prior_provenance["new_screening_policy"]
    identity = v3["v3_2_identity_provenance"]
    assert identity["parent_matrix_status"] == "UNKNOWN_STOP"
    assert identity["parent_matrix_manifest_design_canonical_sha256"] == matrix._design_digest(v3_1)
    assert identity["committed_parent_design_file_sha256"] == v3_runner._sha256(V3_1_DESIGN)
    assert identity["parent_matrix_manifest_design_canonical_sha256"] == "696dce5b18fab9209f57c7d49922ff3c1ae4ea52f0a524c34cede8e8b933e635"
    assert identity["committed_parent_design_file_sha256"] == "26e202fca197ae1f62cfad099cdf895de7b834eed1622c553e80b8cd2b94a839"
    assert "exactly equals the canonical digest" in identity["parent_digest_reconciliation"]
    assert identity["new_matrix_from_queue_start"] is True
    assert identity["parent_cases_and_method_units_excluded"] is True
    assert identity["scientific_factors_changed"] is False
    assert identity["quality_gates_changed"] is False
    assert validate_design_identity(DEFAULT_DESIGN)["design_file_sha256"] == V3_2_DESIGN_FILE_SHA256


def test_v3_loader_rejects_v2_design_identity():
    with pytest.raises(ValueError, match="V3.2 design_id"):
        load_v3_design(V2_DESIGN)


def test_v3_loader_rejects_terminal_v3_1_design():
    with pytest.raises(ValueError, match="V3.1 is terminal"):
        load_v3_design(V3_1_DESIGN)


def test_v3_2_rejects_design_path_or_frozen_sha_mismatch(tmp_path, monkeypatch):
    copied_design = tmp_path / DEFAULT_DESIGN.name
    copied_design.write_bytes(DEFAULT_DESIGN.read_bytes())
    with pytest.raises(ValueError, match="committed design path"):
        validate_design_identity(copied_design)

    monkeypatch.setattr(v3_runner, "V3_2_DESIGN_FILE_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="frozen design file SHA mismatch"):
        validate_design_identity(DEFAULT_DESIGN)


def test_v3_2_cannot_resume_terminal_v3_1_root(tmp_path):
    output = tmp_path / "old-v3-matrix"
    output.mkdir()
    (output / "matrix_manifest.json").write_text(json.dumps({
        "design_id": V3_1_DESIGN_ID,
        "status": "UNKNOWN_STOP",
    }))
    with pytest.raises(ValueError, match="expected design"):
        validate_resume_target(output, resume=True)


def test_v3_2_records_exact_design_path_and_memory_preflight(tmp_path, monkeypatch):
    stage2 = tmp_path / "stage2"
    production = tmp_path / "production"
    stage2.write_bytes(b"stage2")
    production.write_bytes(b"production")
    gpu_snapshot = {
        "status": "available",
        "gpus": [{"memory.used": 1 * 1024**3, "memory.free": 3 * 1024**3,
                  "memory.total": 4 * 1024**3}],
    }
    disk_preflight_roots = []

    def fake_native_preflight(_binary, disk_root):
        disk_preflight_roots.append(Path(disk_root))
        return {"binary": {"sha256": "production-sha"}, "gpu": {"available": True}}

    monkeypatch.setattr(v3_runner.r21, "_native_cuda_preflight", fake_native_preflight)
    monkeypatch.setattr(v3_runner, "_gpu_device_identity", lambda: ["test-gpu"])
    process_snapshot = {"status": "available", "processes": []}
    monkeypatch.setattr(v3_runner, "_gpu_process_snapshot", lambda: process_snapshot)
    monkeypatch.setattr(v3_runner, "collect_resource_snapshot", lambda _root: {
        "captured_at_utc": "2026-11-06T00:00:00Z",
        "gpu": gpu_snapshot,
        "memory_available_bytes": 8 * 1024**3,
    })
    monkeypatch.setattr(v3_runner, "_git_output", lambda *args: {
        "rev-parse": "source-commit", "branch": "test-branch", "status": "",
    }[args[1]])
    monkeypatch.setattr(v3_runner.r22, "file_identity", lambda path: {
        "path": str(Path(path).resolve()), "exists": Path(path).is_file(),
        "bytes": Path(path).stat().st_size if Path(path).is_file() else 0,
        "sha256": "file-sha",
    })

    metadata = build_execution_metadata(
        load_v3_design(DEFAULT_DESIGN), design_path=DEFAULT_DESIGN,
        output_root=tmp_path / "matrix", raw_output_root=tmp_path / "raw",
        compact_archive_root=tmp_path / "archives", stage2_binary=stage2,
        production_binary=production,
    )
    identity = metadata["execution_identity"]
    assert identity["design_path"] == str(DEFAULT_DESIGN.resolve())
    assert identity["design_file_sha256"] == V3_2_DESIGN_FILE_SHA256
    assert identity["design_file"]["sha256"] == "file-sha"
    assert metadata["preflight_snapshot"]["gpu_resource_snapshot"] == gpu_snapshot
    assert metadata["preflight_snapshot"]["gpu_resource_snapshot"]["gpus"][0]["memory.free"] == 3 * 1024**3
    assert metadata["preflight_snapshot"]["gpu_resource_snapshot"]["gpus"][0]["memory.used"] == 1 * 1024**3
    assert metadata["preflight_snapshot"]["gpu_process_snapshot"] == process_snapshot
    assert disk_preflight_roots == [tmp_path]


def test_v3_roots_reject_v2_reuse_and_root_overlap(tmp_path):
    with pytest.raises(ValueError, match="V2 output root"):
        validate_output_roots(
            tmp_path / "r22_full_v2_compact", tmp_path / "v3-raw", tmp_path / "v3-archive"
        )
    with pytest.raises(ValueError, match="must be distinct"):
        validate_output_roots(tmp_path / "same", tmp_path / "same", tmp_path / "archive")


def test_v3_resume_rejects_failed_matrix_manifest(tmp_path):
    output = tmp_path / "v3-matrix"
    output.mkdir()
    (output / "matrix_manifest.json").write_text(json.dumps({
        "design_id": V3_DESIGN_ID, "status": "GATE_FAILED",
    }))
    with pytest.raises(RuntimeError, match="GATE_FAILED"):
        validate_resume_target(output, resume=True)


def test_v3_cli_rejects_v2_design_before_gpu_preflight(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/phase_r/r22_v3_experiment_matrix.py"
    result = subprocess.run([
        sys.executable, str(script), "--design", str(V2_DESIGN), "--phase", "screening",
        "--output-root", str(tmp_path / "matrix"), "--raw-output-root", str(tmp_path / "raw"),
        "--compact-archive-root", str(tmp_path / "archives"),
    ], capture_output=True, text=True, check=False)

    assert result.returncode == 2
    assert "V3.2 design_id" in result.stderr
    assert not (tmp_path / "matrix").exists()


def test_v3_cli_rejects_v2_root_and_failed_resume_without_gpu_preflight(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/phase_r/r22_v3_experiment_matrix.py"
    common = [
        sys.executable, str(script), "--phase", "screening",
        "--raw-output-root", str(tmp_path / "raw"),
        "--compact-archive-root", str(tmp_path / "archives"),
    ]
    reused = subprocess.run(
        common + ["--output-root", str(tmp_path / "r22_full_v2_compact")],
        capture_output=True, text=True, check=False,
    )
    assert reused.returncode == 2
    assert "V2 output root" in reused.stderr

    failed_root = tmp_path / "failed-v3"
    failed_root.mkdir()
    (failed_root / "matrix_manifest.json").write_text(json.dumps({
        "design_id": V3_DESIGN_ID, "status": "GATE_FAILED",
    }))
    failed_resume = subprocess.run(
        common + ["--output-root", str(failed_root), "--resume"],
        capture_output=True, text=True, check=False,
    )
    assert failed_resume.returncode == 2
    assert "GATE_FAILED" in failed_resume.stderr
