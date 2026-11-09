import pytest

from scripts.phase_r.analyze_r22_v3_3_residual_task_gap import (
    _aggregate_association_position_metrics,
    _association_position_errors,
)


def test_association_position_errors_uses_only_matched_truth_opportunities():
    rows = [
        {"sample_key": "T1/3/50", "matched": "True", "position_error_m": "12.5"},
        {"sample_key": "T2/3/50", "matched": "False", "position_error_m": ""},
    ]

    assert _association_position_errors(rows, "case-1") == {"T1/3/50": 12.5}


def test_association_position_errors_rejects_missing_error_for_a_matched_opportunity():
    rows = [{"sample_key": "T1/3/50", "matched": "True", "position_error_m": ""}]

    with pytest.raises(ValueError, match="matched association lacks finite position_error_m"):
        _association_position_errors(rows, "case-1")


def test_full_period_position_metrics_use_method_common_truth_opportunities():
    truth_keys = {"T1/0/50", "T2/0/50", "T3/0/50"}
    case_units = {
        "case-1": {
            "PK": {"seed": 1, "task_status": "EVALUABLE", "valid_keys": truth_keys,
                   "association_position_errors": {"T1/0/50": 3.0, "T2/0/50": 4.0}},
            "Fixed_D3": {"seed": 1, "task_status": "EVALUABLE", "valid_keys": truth_keys,
                         "association_position_errors": {"T1/0/50": 5.0, "T3/0/50": 2.0}},
            "Huber_D3_ON": {"seed": 1, "task_status": "EVALUABLE", "valid_keys": truth_keys,
                            "association_position_errors": {"T1/0/50": 4.0, "T3/0/50": 3.0}},
            "M1_Physics_Matched": {"seed": 1, "task_status": "EVALUABLE", "valid_keys": truth_keys,
                                   "association_position_errors": {"T1/0/50": 2.0, "T3/0/50": 4.0}},
        }
    }

    own_common, pairwise, _ = _aggregate_association_position_metrics(case_units, [1])
    m1 = next(row for row in own_common if row["method_label"] == "M1_Physics_Matched")
    fixed_huber = next(
        row for row in pairwise
        if row["method_a"] == "Fixed_D3" and row["method_b"] == "Huber_D3_ON"
    )

    assert m1["truth_opportunities"] == 3
    assert m1["own_success_count"] == 2
    assert m1["fourway_common_success_count"] == 1
    assert m1["fourway_common_success_rmse_m"] == 2.0
    assert fixed_huber["common_success_count"] == 2
    assert fixed_huber["rmse_difference_b_minus_a_m"] < 0.0
