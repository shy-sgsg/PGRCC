import pytest

from scripts.phase_r import analyze_r22_v3_3_residual_task_gap as audit
from scripts.phase_r.analyze_r22_v3_3_residual_task_gap import (
    _aggregate_association_position_metrics,
    _association_position_errors,
)


def _output_audit(*args, **kwargs):
    implementation = getattr(audit, "_audit_production_output", None)
    assert callable(implementation), "production output audit is not implemented"
    return implementation(*args, **kwargs)


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


def test_production_output_audit_counts_matched_and_unmatched_detections_without_calling_clusters_false():
    cycle_rows = [
        {"result_id": "1", "period_id": "0", "detection_count": "2"},
        {"result_id": "2", "period_id": "1", "detection_count": "1"},
    ]
    lineage_rows = [
        {"record_type": "beam_summary", "period_id": "0", "beam_id": "50", "postfilter_cluster_count": "2", "selected_candidate_count": "2", "raw_detection_count": "2"},
        {"record_type": "cluster", "period_id": "0", "beam_id": "50", "selected": "1", "raw_detection": "1"},
        {"record_type": "cluster", "period_id": "0", "beam_id": "50", "selected": "1", "raw_detection": "1"},
        {"record_type": "beam_summary", "period_id": "1", "beam_id": "50", "postfilter_cluster_count": "1", "selected_candidate_count": "1", "raw_detection_count": "1"},
        {"record_type": "cluster", "period_id": "1", "beam_id": "50", "selected": "1", "raw_detection": "1"},
    ]
    associations = [
        {"period_id": "0", "beam_id": "50", "sample_key": "T1/0/50", "matched": "True", "det_id": "D1"},
        {"period_id": "0", "beam_id": "50", "sample_key": "T2/0/50", "matched": "False", "det_id": ""},
        {"period_id": "1", "beam_id": "50", "sample_key": "T3/1/50", "matched": "True", "det_id": "D2"},
        {"period_id": "1", "beam_id": "50", "sample_key": "T4/1/50", "matched": "False", "det_id": ""},
    ]

    periods, summary = _output_audit(
        cycle_rows, lineage_rows, associations, task_status="EVALUABLE",
        expected_period_count=2, expected_opportunities_per_period=2,
    )

    assert summary["final_detection_count"] == 3
    assert summary["truth_matched_final_detection_count"] == 2
    assert summary["truth_unmatched_final_detection_count"] == 1
    assert summary["target_missed_opportunity_count"] == 2
    assert summary["non_target_cluster_count"] is None
    assert summary["non_target_cluster_status"] == "NOT_EVALUABLE_NO_CLUSTER_TRUTH_ASSOCIATION"
    assert periods[0]["truth_unmatched_final_detection_count"] == 1


def test_production_output_audit_does_not_turn_non_evaluable_associations_into_zero_misses():
    cycle_rows = [{"result_id": "1", "period_id": "0", "detection_count": "1"}]
    lineage_rows = [
        {"record_type": "beam_summary", "period_id": "0", "beam_id": "50", "postfilter_cluster_count": "1", "selected_candidate_count": "1", "raw_detection_count": "1"},
        {"record_type": "cluster", "period_id": "0", "beam_id": "50", "selected": "1", "raw_detection": "1"},
    ]

    _, summary = _output_audit(
        cycle_rows, lineage_rows, [], task_status="NOT_EVALUABLE",
        expected_period_count=1, expected_opportunities_per_period=2,
    )

    assert summary["target_missed_opportunity_count"] is None
    assert summary["truth_unmatched_final_detection_count"] is None
    assert summary["target_association_status"] == "NOT_EVALUABLE_TASK_STATUS"


def test_production_output_audit_rejects_detection_count_mismatch():
    cycle_rows = [{"result_id": "1", "period_id": "0", "detection_count": "2"}]
    lineage_rows = [
        {"record_type": "beam_summary", "period_id": "0", "beam_id": "50", "postfilter_cluster_count": "1", "selected_candidate_count": "1", "raw_detection_count": "1"},
        {"record_type": "cluster", "period_id": "0", "beam_id": "50", "selected": "1", "raw_detection": "1"},
    ]

    with pytest.raises(ValueError, match="cycle_metrics detection_count disagrees"):
        _output_audit(
            cycle_rows, lineage_rows, [], task_status="NOT_EVALUABLE",
            expected_period_count=1, expected_opportunities_per_period=2,
        )


def test_production_output_audit_checks_retained_detection_rows_only_for_retained_periods():
    cycle_rows = [
        {"result_id": "1", "period_id": "0", "detection_count": "2"},
        {"result_id": "2", "period_id": "1", "detection_count": "1"},
    ]
    lineage_rows = [
        {"record_type": "beam_summary", "period_id": "0", "beam_id": "50", "postfilter_cluster_count": "2", "selected_candidate_count": "2", "raw_detection_count": "2"},
        {"record_type": "cluster", "period_id": "0", "beam_id": "50", "selected": "1", "raw_detection": "1"},
        {"record_type": "cluster", "period_id": "0", "beam_id": "50", "selected": "1", "raw_detection": "1"},
        {"record_type": "beam_summary", "period_id": "1", "beam_id": "50", "postfilter_cluster_count": "1", "selected_candidate_count": "1", "raw_detection_count": "1"},
        {"record_type": "cluster", "period_id": "1", "beam_id": "50", "selected": "1", "raw_detection": "1"},
    ]

    try:
        periods, summary = _output_audit(
            cycle_rows, lineage_rows, [], task_status="NOT_EVALUABLE",
            expected_period_count=2, retained_detection_counts={1: 1},
        )
    except TypeError as exc:
        pytest.fail(f"output audit must accept the retained period subset: {exc}")

    assert summary["final_detection_count"] == 3
    assert periods[1]["final_detection_count"] == 1


def test_production_output_aggregation_reports_output_and_truth_association_coverage_separately():
    labels = [method["label"] for method in audit.METHODS]
    case_units = {
        "case-1": {
            label: {
                "case_id": "case-1", "seed": 1, "speed_mps": 0.5, "snr_db": 10.0,
                "task_status": "EVALUABLE",
                "production_output": {
                    "production_output_status": "EVALUABLE",
                    "target_association_status": "EVALUABLE_FROZEN_TARGET_ASSOCIATIONS",
                    "final_detection_count": 4,
                    "postfilter_cluster_count": 4,
                    "truth_unmatched_final_detection_count": 2,
                    "target_missed_opportunity_count": 3,
                },
                "production_output_periods": [],
            }
            for label in labels
        },
        "case-2": {
            label: {
                "case_id": "case-2", "seed": 1, "speed_mps": 1.5, "snr_db": 10.0,
                "task_status": "NOT_EVALUABLE",
                "production_output": {
                    "production_output_status": "PRODUCTION_OUTPUTS_ONLY",
                    "target_association_status": "NOT_EVALUABLE_TASK_STATUS",
                    "final_detection_count": 5,
                    "postfilter_cluster_count": 5,
                    "truth_unmatched_final_detection_count": None,
                    "target_missed_opportunity_count": None,
                },
                "production_output_periods": [],
            }
            for label in labels
        },
    }

    aggregate = getattr(audit, "_aggregate_production_outputs", None)
    assert callable(aggregate), "production output aggregation is not implemented"
    _, _, seed_rows, summary_rows = aggregate(case_units, [1])
    pk_seed = next(row for row in seed_rows if row["method_label"] == "PK")
    pk_summary = next(row for row in summary_rows if row["method_label"] == "PK")

    assert pk_seed["production_output_evaluable_case_count"] == 2
    assert pk_seed["target_association_evaluable_case_count"] == 1
    assert pk_seed["mean_truth_unmatched_final_detections_per_evaluable_case"] == 2.0
    assert pk_summary["mean_seed_production_output_coverage"] == pytest.approx(2 / 12)
    assert pk_summary["mean_seed_target_association_coverage"] == pytest.approx(1 / 12)
