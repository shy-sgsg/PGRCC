from __future__ import annotations

import pytest

from scripts.phase_r import r22_experiment_matrix as matrix


DESIGN = matrix.DEFAULT_DESIGN


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
