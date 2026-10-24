import numpy as np

from scripts.phase_r.evaluate_r7_task_metrics import (
    _cfar_counts,
    evaluate_case,
    summarize_target_only_response,
    track_associations_from_debug,
)


def _truth(**overrides):
    row = {
        "period_id": "0",
        "beam_id": "50",
        "target_id": "target-0",
        "visible": "1",
        "range_bin": "100",
        "af_total_truth_hz": "-150",
        "e_mid": "0",
        "n_mid": "0",
        "ve_mps": "3",
        "vn_mps": "4",
        "vr_self_mps": "2",
        "azimuth_deg": "10",
    }
    row.update({key: str(value) for key, value in overrides.items()})
    return row


def _detection(det_id, row, col, **overrides):
    value = {
        "det_id": str(det_id),
        "period_id": "0",
        "beam_id": "50",
        "range_bin": str(col),
        "row": str(row),
        "col": str(col),
        "fd_ctr_unwrapped": "0",
        "prf_hz": "1300",
        "theta_used_deg": "11",
        "new_e": "3",
        "new_n": "4",
        "platform_e": "-3",
        "platform_n": "-4",
        "radial_velocity_mps": "2.5",
        "ambiguity_status": "resolved",
    }
    value.update({key: str(item) for key, item in overrides.items()})
    return value


def _lineage(row, col, *, selected=1, raw=1):
    return {
        "period_id": "0",
        "beam_id": "50",
        "cluster_index": str(row),
        "cluster_row": str(row),
        "cluster_col": str(col),
        "selected": str(selected),
        "raw_detection": str(raw),
    }


def test_unique_union_hit_cells_sum_validated_counts_across_periods():
    rows = []
    for period, dynamic_hits, full_hits, union_hits in (
        (0, 134, 157, 181),
        (1, 99, 109, 124),
        (2, 98, 108, 127),
    ):
        rows.extend([
            {"period_id": period, "beam_id": 50, "branch": "union_dynamic_in_band",
             "hit_cut_count": dynamic_hits, "valid_cut_count": 527280},
            {"period_id": period, "beam_id": 50, "branch": "union_full_band",
             "hit_cut_count": full_hits, "valid_cut_count": 527280},
            {"period_id": period, "beam_id": 50, "branch": "unique_union_audit",
             "unique_union_hit_cells": union_hits, "valid_cut_count": 527280,
             "audit_verified": True},
        ])

    result = _cfar_counts(rows, expected_period_beam_keys={(0, 50), (1, 50), (2, 50)})

    assert result["cfar_branch_hit_cells"] == 705
    assert result["unique_union_hit_cells"]["count"] == 432
    assert result["unique_union_hit_cells"]["valid_cut_count"] == 1_581_840
    assert result["unique_union_hit_cells"]["observed_hit_fraction"] == 432 / 1_581_840


def test_unique_union_hit_cells_cover_multiple_beams_without_branch_overlap_sum():
    rows = []
    expected = {(period, beam) for period in (0, 1) for beam in (50, 51)}
    for period, beam in sorted(expected):
        rows.extend([
            {"period_id": period, "beam_id": beam, "branch": "union_dynamic_in_band",
             "hit_cut_count": 10, "valid_cut_count": 100},
            {"period_id": period, "beam_id": beam, "branch": "union_full_band",
             "hit_cut_count": 12, "valid_cut_count": 100},
            {"period_id": period, "beam_id": beam, "branch": "unique_union_audit",
             "unique_union_hit_cells": 15, "valid_cut_count": 100, "audit_verified": True},
        ])

    result = _cfar_counts(rows, expected_period_beam_keys=expected)

    assert result["cfar_branch_hit_cells"] == 88
    assert result["unique_union_hit_cells"]["count"] == 60
    assert result["unique_union_hit_cells"]["valid_cut_count"] == 400


def test_unique_union_rows_with_duplicates_are_not_evaluable():
    row = {
        "period_id": 0, "beam_id": 50, "branch": "unique_union_audit",
        "unique_union_hit_cells": 7, "valid_cut_count": 100, "audit_verified": True,
    }

    result = _cfar_counts(
        [row, dict(row)], expected_period_beam_keys={(0, 50)},
    )

    assert result["unique_union_hit_cells"]["status"] == "NOT_EVALUABLE_DUPLICATE_UNION_ROWS"
    assert result["unique_union_hit_cells"]["count"] is None


def test_conflicting_unique_union_rows_are_not_evaluable():
    first = {
        "period_id": 0, "beam_id": 50, "branch": "unique_union_audit",
        "unique_union_hit_cells": 7, "valid_cut_count": 100, "audit_verified": True,
    }
    second = {**first, "unique_union_hit_cells": 8}

    result = _cfar_counts(
        [first, second], expected_period_beam_keys={(0, 50)},
    )

    assert result["unique_union_hit_cells"]["status"] == "NOT_EVALUABLE_CONFLICTING_UNION_ROWS"
    assert result["unique_union_hit_cells"]["count"] is None


def test_unique_union_rows_missing_expected_period_beam_are_not_evaluable():
    row = {
        "period_id": 0, "beam_id": 50, "branch": "unique_union_audit",
        "unique_union_hit_cells": 7, "valid_cut_count": 100, "audit_verified": True,
    }

    result = _cfar_counts(
        [row], expected_period_beam_keys={(0, 50), (1, 50)},
    )

    assert result["unique_union_hit_cells"]["status"] == "NOT_EVALUABLE_MISSING_PERIOD_BEAM_ROWS"
    assert result["unique_union_hit_cells"]["count"] is None


def test_unique_union_rows_without_verified_numeric_audit_are_not_evaluable():
    row = {
        "period_id": 0, "beam_id": 50, "branch": "unique_union_audit",
        "unique_union_hit_cells": 7, "valid_cut_count": 100,
    }

    result = _cfar_counts(
        [row], expected_period_beam_keys={(0, 50)},
    )

    assert result["unique_union_hit_cells"]["status"] == "NOT_EVALUABLE_UNVERIFIED_AUDIT"
    assert result["unique_union_hit_cells"]["count"] is None


def test_on_assignment_is_one_to_one_and_minimizes_normalized_bin_residual():
    detections = [
        _detection(0, 51, 100),  # cost = (0/2)^2 + (1/2)^2
        _detection(1, 50, 102),  # cost = (2/2)^2 + (0/2)^2
    ]
    tracks = [
        {"period_id": 0, "beam_id": 50, "det_id": 0, "track_id": 7,
         "confirmed": 1, "matched_this_frame": 1, "output_speed": 5},
        {"period_id": 0, "beam_id": 50, "det_id": 1, "track_id": 8,
         "confirmed": 1, "matched_this_frame": 1, "output_speed": 6},
    ]

    result = evaluate_case(
        mode="ON",
        truths=[_truth()],
        final_detections=detections,
        lineage_rows=[_lineage(51, 100), _lineage(50, 102)],
        track_associations=tracks,
        doppler_rows=130,
    )

    metrics = result["metrics"]
    assert metrics["truth_opportunities"] == 1
    assert metrics["unique_correct_detections"] == 1
    assert metrics["misses"] == 0
    assert metrics["false_associations"] == 1
    assert metrics["correct_track_associations"] == 1
    assert metrics["false_track_associations"] == 1
    assert result["association_evidence"][0]["det_id"] == "0"
    assert result["association_evidence"][0]["normalized_residual"] == 0.5
    assert result["association_evidence"][0]["period_id"] == 0
    assert metrics["parameter_errors"]["position_2d_rmse_m"] == 5.0
    assert metrics["parameter_errors"]["angle_rmse_deg"] == 0.0
    assert metrics["parameter_errors"]["ground_speed_rmse_mps"] == 0.0
    association = result["association_evidence"][0]
    assert association["position_error_m"] == 5.0
    assert association["angle_error_deg"] == 0.0
    assert association["ground_speed_error_mps"] == 0.0
    assert association["radial_velocity_error_mps"] == 0.5
    assert association["radial_velocity_status"] == "evaluable"


def test_circular_doppler_gate_accepts_wraparound_within_two_bins():
    truth = _truth(af_total_truth_hz=-660)
    detection = _detection(0, 1, 100)

    result = evaluate_case(
        mode="ON",
        truths=[truth],
        final_detections=[detection],
        lineage_rows=[_lineage(1, 100)],
        doppler_rows=130,
    )

    assert result["association_evidence"][0]["truth_row"] == 129
    assert result["association_evidence"][0]["doppler_delta_bins"] == 2
    assert result["metrics"]["unique_correct_detections"] == 1


def test_recorded_production_doppler_axis_maps_signed_truth_row_to_the_actual_cfar_row():
    truth = _truth(
        range_bin="2048",
        af_total_truth_hz="-4633.1002422",
        row_truth="-4",
    )
    detection = _detection(0, 49, 2048, fd_ctr_unwrapped="0")
    lineage = [
        {
            "record_type": "beam_summary",
            "period_id": "0",
            "beam_id": "50",
            "postfilter_cluster_count": "1",
            "selected_candidate_count": "1",
            "raw_detection_count": "1",
            "doppler_axis_first_hz": "-1228.8201304884583",
            "doppler_bin_spacing_hz": "10",
            "doppler_row_count": "130",
        },
        _lineage(49, 2048),
    ]

    result = evaluate_case(
        mode="ON",
        truths=[truth],
        final_detections=[detection],
        lineage_rows=lineage,
        doppler_rows=130,
    )

    association = result["association_evidence"][0]
    assert association["truth_row"] == 50
    assert association["detection_row"] == 49
    assert association["doppler_delta_bins"] == 1
    assert association["signed_doppler_delta_bins"] == -1
    assert abs(association["folded_doppler_frequency_error_hz"] - (-5.7199)) < 0.001
    assert abs(
        result["metrics"]["parameter_errors"]["folded_doppler_frequency_rmse_hz"]
        - 5.7199
    ) < 0.001
    assert result["metrics"]["unique_correct_detections"] == 1


def test_legacy_period_axis_sidecar_overrides_missing_detection_center():
    result = evaluate_case(
        mode="ON",
        truths=[_truth(range_bin="2048", af_total_truth_hz="-4633.1002422",
                       row_truth="-4")],
        final_detections=[_detection(0, 49, 2048, fd_ctr_unwrapped="0")],
        doppler_axis_rows=[
            {"period_id": 0, "beam_id": 50, "doppler_axis_first_hz": -1228.8201304884583,
             "doppler_bin_spacing_hz": 10, "doppler_row_count": 130,
             "source_meta": "R6 cfar_GMTI01_beam050_meta.txt"}
        ],
        doppler_rows=130,
    )

    assert result["lineage"]["status"] == "NOT_AVAILABLE"
    assert result["metrics"]["unique_correct_detections"] == 1
    assert result["association_evidence"][0]["truth_row"] == 50


def test_signed_geometry_truth_row_is_not_used_when_production_doppler_axis_is_missing():
    result = evaluate_case(
        mode="ON",
        truths=[_truth(row_truth="50")],
        final_detections=[_detection(0, 50, 100, fd_ctr_unwrapped="", prf_hz="")],
        lineage_rows=None,
        doppler_rows=130,
    )

    assert result["metrics"]["unique_correct_detections"] == 0
    assert result["association_evidence"][0]["miss_reason"] == (
        "no_one_to_one_production_detection_within_frozen_gate"
    )
    assert result["candidate_pair_evidence"][0]["gate_result"] == (
        "missing_bin_or_doppler_axis"
    )


def test_off_detection_near_truth_is_only_reported_as_an_h0_detection():
    result = evaluate_case(
        mode="OFF",
        truths=[_truth()],
        final_detections=[_detection(0, 50, 100)],
        lineage_rows=[
            {"record_type": "beam_summary", "period_id": "0", "beam_id": "50",
             "postfilter_cluster_count": "1", "selected_candidate_count": "1",
             "raw_detection_count": "1"},
            _lineage(50, 100),
        ],
        cfar_rows=[
            {"period_id": "0", "beam_id": "50", "branch": "dynamic",
             "hit_cut_count": "4", "cluster_count": "1"},
            {"period_id": "0", "beam_id": "50", "branch": "full",
             "hit_cut_count": "5", "cluster_count": "1"},
        ],
        track_frames=[{"num_tracks": "2", "num_confirmed": "1"}],
    )

    metrics = result["metrics"]
    assert metrics["cfar_branch_hit_cells"] == 9
    assert metrics["postfilter_clusters"] == 1
    assert metrics["false_clusters"] == 1
    assert metrics["final_detections"] == 1
    assert metrics["false_detections"] == 1
    assert metrics["tracks"] == 2
    assert metrics["false_confirmed_tracks"] == 1
    assert not {"truth_opportunities", "pd", "target_rmse", "parameter_errors"} & metrics.keys()
    assert result["association_evidence"] == []


def test_legacy_cfar_geometry_zero_is_not_misreported_as_zero_postfilter_clusters():
    result = evaluate_case(
        mode="OFF",
        final_detections=[_detection(0, 50, 100)],
        lineage_rows=None,
        cfar_rows=[
            {"period_id": "0", "beam_id": "50", "branch": "union_dynamic_in_band",
             "hit_cut_count": "4", "cluster_count": "0"},
        ],
        doppler_rows=130,
    )

    assert result["metrics"]["cfar_branch_hit_cells"] == 4
    assert result["metrics"]["false_detections"] == 1
    assert result["metrics"]["false_clusters"] is None
    assert result["metrics"]["cluster_status"] == "NOT_EVALUABLE_LEGACY_PRE_CLUSTER_SUMMARY"


def test_rejected_cluster_cannot_associate_to_a_nearby_truth():
    result = evaluate_case(
        mode="ON",
        truths=[_truth()],
        final_detections=[],
        lineage_rows=[_lineage(50, 100, selected=0, raw=0)],
        doppler_rows=130,
    )

    assert result["metrics"]["misses"] == 1
    assert result["metrics"]["unique_correct_detections"] == 0
    assert result["lineage"]["postfilter_clusters"] == 1
    assert result["lineage"]["selected_candidates"] == 0
    assert result["lineage"]["raw_detections"] == 0


def test_unresolved_radial_velocity_is_not_given_a_fabricated_error():
    result = evaluate_case(
        mode="ON",
        truths=[_truth()],
        final_detections=[
            _detection(0, 50, 100, ambiguity_status="unresolved",
                       radial_velocity_mps="-123")
        ],
        lineage_rows=[_lineage(50, 100)],
        doppler_rows=130,
    )

    radial = result["metrics"]["parameter_errors"]["radial_velocity"]
    assert radial["status"] == "NOT_EVALUABLE"
    assert radial["unresolved_count"] == 1
    assert radial["count"] == 0
    assert radial["rmse_mps"] is None


def test_unknown_ambiguity_status_does_not_make_radial_velocity_evaluable():
    result = evaluate_case(
        mode="ON",
        truths=[_truth()],
        final_detections=[
            _detection(0, 50, 100, ambiguity_status="unknown",
                       radial_velocity_mps="2.5")
        ],
        lineage_rows=[_lineage(50, 100)],
        doppler_rows=130,
    )

    radial = result["metrics"]["parameter_errors"]["radial_velocity"]
    assert radial["status"] == "NOT_EVALUABLE"
    assert radial["count"] == 0
    assert radial["rmse_mps"] is None


def test_angle_error_uses_platform_to_truth_and_estimate_bearings_not_beam_azimuth():
    result = evaluate_case(
        mode="ON",
        truths=[_truth(e_mid="0", n_mid="0", azimuth_deg="10")],
        final_detections=[
            _detection(0, 50, 100, theta_used_deg="11",
                       platform_e="-3", platform_n="-4")
        ],
        doppler_rows=130,
    )

    errors = result["metrics"]["parameter_errors"]
    assert errors["angle_error_count"] == 1
    assert errors["angle_rmse_deg"] == 0.0


def test_legacy_final_detection_remains_candidate_but_lineage_is_not_claimed():
    result = evaluate_case(
        mode="ON",
        truths=[_truth()],
        final_detections=[_detection(0, 50, 100)],
        lineage_rows=None,
        doppler_rows=130,
    )

    assert result["lineage"]["status"] == "NOT_AVAILABLE"
    assert result["metrics"]["unique_correct_detections"] == 1
    assert result["association_evidence"][0]["candidate_source"] == "final_production_detection"


def test_target_only_response_reports_energy_phase_and_observability():
    result = summarize_target_only_response(
        before=[1 + 0j, 0j],
        after=[0 + 2j, 0j],
    )

    assert result["status"] == "evaluable"
    assert result["input_energy"] == 1.0
    assert result["output_energy"] == 4.0
    assert result["energy_ratio"] == 4.0
    assert result["amplitude_ratio"] == 2.0
    assert result["phase_error_rad"] > 1.5
    assert result["phase_error_rad"] < 1.6
    assert result["observable"] is True


def test_zero_target_only_reference_is_not_declared_observable():
    result = summarize_target_only_response(before=[0j, 0j], after=[1 + 0j, 2 + 0j])

    assert result["status"] == "NOT_EVALUABLE"
    assert result["observable"] is False
    assert result["amplitude_ratio"] is None


def test_target_only_response_accepts_numpy_roi_arrays():
    result = summarize_target_only_response(
        before=np.asarray([1 + 0j, 2 + 0j]),
        after=np.asarray([0 + 1j, 0 + 2j]),
    )

    assert result["status"] == "evaluable"
    assert result["amplitude_ratio"] == 1.0


def test_track_associations_require_confirmed_output_and_current_detection_match():
    associations = track_associations_from_debug(
        track_detections=[
            {"result_id": "1", "det_index": "0", "matched": "1",
             "matched_track_id": "7"},
            {"result_id": "1", "det_index": "1", "matched": "1",
             "matched_track_id": "8"},
        ],
        track_payloads=[
            {"result_id": "1", "track_id": "7", "matched_det_index": "0",
             "output_speed": "5"},
            # A tentative/unconfirmed track has no payload and cannot count.
        ],
        final_detections=[
            {"period_id": "0", "beam_id": "50", "det_id": "0",
             "result_id": "GMTI01"},
            {"period_id": "0", "beam_id": "50", "det_id": "1",
             "result_id": "GMTI01"},
        ],
        result_to_period={"1": 0},
    )

    assert associations == [
        {"period_id": 0, "beam_id": 50, "det_id": "0", "track_id": "7",
         "confirmed": 1, "matched_this_frame": 1, "output_speed": 5.0}
    ]


def test_to_without_legacy_cluster_lineage_reports_cluster_count_not_evaluable():
    result = evaluate_case(mode="TO", lineage_rows=None)

    assert result["metrics"]["postfilter_clusters"] is None
    assert result["metrics"]["lineage_status"] == "NOT_AVAILABLE"
