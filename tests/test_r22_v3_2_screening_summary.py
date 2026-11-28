from __future__ import annotations

import io
import zipfile

import pytest

from scripts.phase_r.summarize_r22_v3_2_screening import (
    _branch_condition_for_unit,
    _validate_background_reuse_mappings,
    compute_recovery,
    expected_production_branch_names,
    latest_attempts_by_case,
    parse_task_endpoint,
    same_opportunity_sets,
    _validate_case_run_identity,
)


def test_non_evaluable_reference_gap_is_not_counted_as_a_miss():
    endpoint = parse_task_endpoint({
        "status": "NOT_EVALUABLE",
        "hits": "",
        "misses": "",
        "evaluable_truth_opportunities": "",
        "hit_fraction": "",
        "reason": "reference_status_not_ok",
    })

    assert endpoint == {
        "status": "NOT_EVALUABLE",
        "hits": None,
        "misses": None,
        "opportunities": None,
        "hit_rate": None,
        "reason": "reference_status_not_ok",
    }


def test_evaluable_task_row_checks_counts_and_rate():
    endpoint = parse_task_endpoint({
        "status": "EVALUABLE",
        "hits": "3",
        "misses": "7",
        "evaluable_truth_opportunities": "10",
        "hit_fraction": "0.3",
    })

    assert endpoint["opportunities"] == 10
    assert endpoint["hit_rate"] == pytest.approx(0.3)

    with pytest.raises(ValueError, match=r"hits \+ misses"):
        parse_task_endpoint({
            "status": "EVALUABLE",
            "hits": "3",
            "misses": "6",
            "evaluable_truth_opportunities": "10",
            "hit_fraction": "0.3",
        })


def test_recovery_uses_known_minus_current_as_recoverable_space():
    result = compute_recovery(
        current_rate=0.1,
        known_rate=0.9,
        estimated_rate=0.7,
    )

    assert result["recoverable_space"] == pytest.approx(0.8)
    assert result["actual_recovered"] == pytest.approx(0.6)
    assert result["recovery_ratio"] == pytest.approx(0.75)
    assert result["known_minus_estimated"] == pytest.approx(0.2)


def test_recovery_ratio_is_undefined_when_known_does_not_exceed_current():
    result = compute_recovery(
        current_rate=0.7,
        known_rate=0.7,
        estimated_rate=0.8,
    )

    assert result["recoverable_space"] == pytest.approx(0.0)
    assert result["recovery_ratio"] is None


def test_latest_attempt_does_not_fall_back_to_an_older_attempt():
    selected = latest_attempts_by_case([
        {"case_id": "case-a", "attempt": 1, "status": "PASSED"},
        {"case_id": "case-a", "attempt": 2, "status": "RUNNING"},
        {"case_id": "case-b", "attempt": 1, "status": "PASSED"},
    ])

    assert selected["case-a"]["status"] == "RUNNING"
    assert selected["case-a"]["attempt"] == 2
    assert selected["case-b"]["status"] == "PASSED"


def test_pair_requires_identical_valid_truth_opportunity_sets():
    assert same_opportunity_sets({("0", "50", "T1")}, {("0", "50", "T1")})
    assert not same_opportunity_sets(
        {("0", "50", "T1"), ("1", "50", "T2")},
        {("0", "50", "T1")},
    )


def test_method_reference_gap_omits_only_its_unexecuted_off_and_to_branches():
    names = expected_production_branch_names(
        [
            ("C3", "Mode-B"),
            ("C4", "Mode-B"),
            ("R22_M1_Physics_Matched", "not_applicable"),
        ],
        {
            ("C3", "Mode-B"): "EVALUABLE",
            ("C4", "Mode-B"): "REFERENCE_NOT_EVALUABLE",
            ("R22_M1_Physics_Matched", "not_applicable"): "EVALUABLE",
        },
        {
            ("C3", "Mode-B"): "C3_modeb",
            ("C4", "Mode-B"): "C4_modeb",
            ("R22_M1_Physics_Matched", "not_applicable"): "R22_M1_Physics_Matched",
        },
    )

    assert names == {
        "C3_modeb_OFF",
        "C3_modeb_ON",
        "C3_modeb_TO",
        "C4_modeb_ON",
        "R22_M1_Physics_Matched_OFF",
        "R22_M1_Physics_Matched_ON",
        "R22_M1_Physics_Matched_TO",
    }


def test_r22_not_applicable_task_identity_maps_to_production_method_branch():
    assert _branch_condition_for_unit(
        "R22_M1_Physics_Matched",
        "not_applicable",
        "R22_M1_Physics_Matched_not_applicable",
    ) == "R22_M1_Physics_Matched"
    assert _branch_condition_for_unit(
        "A0", "not_applicable", "A0_not_applicable"
    ) == "A0_not_applicable"


def test_background_reuse_mapping_requires_matching_periods_and_roles():
    content = b"period_id,mapping_status\n0,matched\n1,matched\n"
    names = (
        "case/scenes/A0_OFF/reports/background_reuse_mapping.csv",
        "case/scenes/A0_TO/reports/background_reuse_mapping.csv",
        "case/scenes/A1_ON/reports/background_reuse_mapping.csv",
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, content)
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        assert _validate_background_reuse_mappings(archive, 2) == (
            "PASSED_IDENTICAL_MATCHED_THREE_ROLE_MAPPINGS"
        )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, content if name == names[0] else b"period_id,mapping_status\n0,matched\n1,unmatched\n")
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        with pytest.raises(ValueError, match="not byte-identical"):
            _validate_background_reuse_mappings(archive, 2)


def test_archived_run_identity_must_match_frozen_case_and_keep_ai_disabled():
    spec = {
        "case_id": "case-1", "seed": 10, "speed_mps": 1.5, "snr_db": 20.0,
        "delay_ns": 4.0, "target_count": 20, "period_count": 10,
        "first_beam": 50, "beam_count": 1,
    }
    record = {"case_id": "case-1", "seed": 10}
    run = {
        "case_id": "case-1",
        "design": {
            "seed": 10, "radial_speed_mps": 1.5, "target_snr_db": 20.0,
            "delay_error_ns": 4.0, "target_count": 20, "period_count": 10,
            "first_beam": 50, "beam_count": 1, "ai_training": False,
            "router_enabled": False,
        },
    }
    _validate_case_run_identity(run, record, spec)
    run["design"]["radial_speed_mps"] = 2.5
    with pytest.raises(ValueError, match="differs from frozen"):
        _validate_case_run_identity(run, record, spec)
