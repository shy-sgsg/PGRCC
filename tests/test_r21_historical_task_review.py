from scripts.phase_r.r21_historical_task_review import (
    _normalize_metric_statuses,
    _r8_r12_regression_rows,
)


def test_metric_evaluability_rows_never_pair_evaluable_status_with_blank_value():
    rows = [
        {
            "dataset": "R8", "dataset_key": "seed-test", "condition": "B", "role": "ON",
            "metric": "post_confirmation_match_rate", "value": None, "status": "evaluable",
        },
        {
            "dataset": "R8", "dataset_key": "seed-test", "condition": "B", "role": "ON",
            "metric": "target_detection_hits", "value": 0, "status": "evaluable",
        },
        {
            "dataset": "R8", "dataset_key": "seed-test", "condition": "B", "role": "ON",
            "metric": "position_rmse", "value": None, "status": "EVALUABLE",
        },
    ]
    track_rows = [{
        "dataset": "R8", "dataset_key": "seed-test", "condition": "B", "role": "ON",
        "first_target_confirmation_period": None,
    }]
    result = _normalize_metric_statuses(rows, track_rows)
    assert [row["status"] for row in result] == [
        "NOT_EVALUABLE_NO_TARGET_CONFIRMATION",
        "EVALUABLE",
        "NOT_EVALUABLE_MISSING_VALUE",
    ]


def test_no_valid_physics_verified_opportunities_do_not_report_zero_detection_metrics():
    rows = [
        {"dataset": "R8", "dataset_key": "seed-101", "condition": "A0_Ideal", "role": "ON",
         "metric": "target_detection_hits", "value": 0, "numerator": 0, "denominator": 0,
         "status": "EVALUABLE"},
        {"dataset": "R8", "dataset_key": "seed-101", "condition": "A0_Ideal", "role": "ON",
         "metric": "target_detection_misses", "value": 0, "numerator": 0, "denominator": 0,
         "status": "EVALUABLE"},
        {"dataset": "R8", "dataset_key": "seed-101", "condition": "A0_Ideal", "role": "ON",
         "metric": "target_detection_hit_fraction", "value": None, "numerator": 0, "denominator": 0,
         "status": "NOT_EVALUABLE_NO_VALID_PHYSICS_VERIFIED_OPPORTUNITIES"},
    ]
    task_rows = [{
        "dataset": "R8", "dataset_key": "seed-101", "condition": "A0_Ideal", "role": "ON",
        "status": "NOT_EVALUABLE_NO_VALID_PHYSICS_VERIFIED_OPPORTUNITIES",
        "truth_opportunities": 0,
    }]
    result = _normalize_metric_statuses(rows, [], task_rows)
    assert [row["status"] for row in result] == [
        "NOT_EVALUABLE_NO_VALID_PHYSICS_VERIFIED_OPPORTUNITIES",
    ] * 3
    assert [row["value"] for row in result] == [None, None, None]
    assert [row["numerator"] for row in result] == [None, None, None]


def test_r8_r12_regression_compares_saved_values_and_preserves_invalid_evaluation():
    saved = [
        {"dataset": "R8", "dataset_key": "seed-202", "case_id": "r8-202",
         "seed": 202, "condition": "A0_Ideal", "role": "ON",
         "metric": "target_detection_hits", "value": "2", "status": "EVALUABLE"},
        {"dataset": "R8", "dataset_key": "seed-101", "case_id": "r8-101",
         "seed": 101, "condition": "A0_Ideal", "role": "ON",
         "metric": "target_detection_hits", "value": "", "status": "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED"},
    ]
    task_rows = [
        {"dataset": "R8", "dataset_key": "seed-202", "case_id": "r8-202",
         "seed": 202, "condition": "A0_Ideal", "role": "ON",
         "hits": 2, "status": "EVALUABLE"},
        {"dataset": "R8", "dataset_key": "seed-101", "case_id": "r8-101",
         "seed": 101, "condition": "A0_Ideal", "role": "ON",
         "hits": None, "status": "NOT_EVALUABLE_NO_VALID_PHYSICS_VERIFIED_OPPORTUNITIES"},
    ]
    result = _r8_r12_regression_rows(saved, task_rows)
    assert result[0]["comparison_status"] == "VALUE_UNCHANGED"
    assert result[0]["r21_value"] == 2.0
    assert result[1]["comparison_status"] == "NON_EVALUABLE_BOTH"
    assert result[1]["r21_value"] is None
    assert result[1]["r21_status"] == "NOT_EVALUABLE_NO_VALID_PHYSICS_VERIFIED_OPPORTUNITIES"
