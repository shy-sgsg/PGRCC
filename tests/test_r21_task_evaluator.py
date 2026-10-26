import pytest

from scripts.phase_r.r21_task_evaluator import (
    audit_doppler_opportunity_v3,
    audit_range_bin_axis_v3,
    evaluate_targets_v3,
    metric_status_v3,
    summarize_track_lineage_v3,
    validate_truth_range_bin_v3,
)


def _range_axis(scene_len=4096, production_len=4096, scene_start=3864, production_start=3864):
    return audit_range_bin_axis_v3(
        {
            "range_fft_len": 12288,
            "range_crop_start": scene_start,
            "range_crop_len": scene_len,
        },
        {
            "range_fft_len": 12288,
            "range_crop_start": production_start,
            "range_compress_len": production_len,
        },
    )


def test_range_bin_axis_must_match_scene_crop_and_production_output_geometry():
    valid = _range_axis()
    assert valid["status"] == "RANGE_AXIS_VERIFIED"
    assert valid["range_bin_count"] == 4096
    assert _range_axis(scene_len=4096, production_len=4095)["status"] == "RANGE_AXIS_MISMATCH"
    assert _range_axis(scene_start=3864, production_start=3865)["status"] == "RANGE_AXIS_MISMATCH"


@pytest.mark.parametrize(
    "truth,expected_reason",
    [
        ({"range_bin": None, "expected_bin": 3}, "missing_range_bin"),
        ({"range_bin": float("nan"), "expected_bin": 3}, "invalid_range_bin"),
        ({"range_bin": 3.2, "expected_bin": 3}, "non_integer_range_bin"),
        ({"range_bin": -1, "expected_bin": -1}, "range_bin_outside_scene_axis"),
        ({"range_bin": 4096, "expected_bin": 4096}, "range_bin_outside_scene_axis"),
        ({"range_bin": 4, "expected_bin": 3}, "range_bin_disagrees_with_expected_bin"),
    ],
)
def test_invalid_truth_range_bins_fail_closed_without_rounding(truth, expected_reason):
    result = validate_truth_range_bin_v3(truth, _range_axis())
    assert result["status"] == "INVALID_TRUTH_RANGE_BIN"
    assert expected_reason in result["reasons"]


def test_truth_range_bin_accepts_exact_integer_on_the_production_crop_axis():
    result = validate_truth_range_bin_v3(
        {"range_bin": "2048", "expected_bin": "2048"}, _range_axis(),
    )
    assert result == {
        "status": "TRUTH_RANGE_BIN_VERIFIED",
        "range_bin": 2048,
        "range_bin_count": 4096,
        "reasons": [],
    }


@pytest.mark.parametrize(
    "first,spacing,expected_row,orientation",
    [
        (500.0, 10.0, 65, "increasing_frequency"),
        (1490.0, -10.0, 34, "decreasing_frequency"),
    ],
)
def test_doppler_full_opportunity_audit_preserves_signed_axis_orientation(
    first, spacing, expected_row, orientation,
):
    result = audit_doppler_opportunity_v3(
        {"af_motion_truth_hz": 120.0, "af_geometry_truth_hz": 30.0,
         "af_total_truth_hz": 150.0},
        {"doppler_axis_first_hz": first, "doppler_bin_spacing_hz": spacing,
         "doppler_row_count": 100, "doppler_axis_uniform": 1, "prf_hz": 1000.0},
        range_rate_closure_error_hz=0.0,
        range_rate_closure_tolerance_hz=0.01,
    )
    assert result["status"] == "MAPPING_VERIFIED"
    assert result["axis_prf_closure_hz"] == 0.0
    assert result["row_orientation"] == orientation
    assert result["expected_cfar_row"] == expected_row


def test_doppler_opportunity_requires_range_rate_and_component_closure():
    axis = {"doppler_axis_first_hz": -500.0, "doppler_bin_spacing_hz": 10.0,
            "doppler_row_count": 100, "doppler_axis_uniform": 1, "prf_hz": 1000.0}
    truth = {"af_motion_truth_hz": -100.0, "af_geometry_truth_hz": -50.0,
             "af_total_truth_hz": -150.0}
    bad_range_rate = audit_doppler_opportunity_v3(
        truth, axis, range_rate_closure_error_hz=0.5,
        range_rate_closure_tolerance_hz=0.01,
    )
    assert bad_range_rate["status"] == "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED"
    assert "range_rate_doppler_closure_failed" in bad_range_rate["reasons"]
    bad_components = audit_doppler_opportunity_v3(
        dict(truth, af_total_truth_hz=-149.0), axis,
        range_rate_closure_error_hz=0.0, range_rate_closure_tolerance_hz=0.01,
    )
    assert "truth_component_closure_failed" in bad_components["reasons"]


def test_per_opportunity_doppler_failure_is_excluded_even_if_other_rows_are_valid():
    range_axis = _range_axis()
    axis = {"doppler_axis_first_hz": -500.0, "doppler_bin_spacing_hz": 10.0,
            "doppler_row_count": 100, "doppler_axis_uniform": 1, "prf_hz": 1000.0}
    truths = [
        {"target_id": "T0", "period_id": 0, "beam_id": 50, "visible": "1",
         "range_bin": 100, "expected_bin": 100, "range_m": 10_000.0,
         "e_mid": 0.0, "n_mid": 0.0, "af_motion_truth_hz": -100.0,
         "af_geometry_truth_hz": -50.0, "af_total_truth_hz": -150.0},
        {"target_id": "T1", "period_id": 1, "beam_id": 50, "visible": "1",
         "range_bin": 101, "expected_bin": 101, "range_m": 10_000.0,
         "e_mid": 0.0, "n_mid": 0.0, "af_motion_truth_hz": -100.0,
         "af_geometry_truth_hz": -50.0, "af_total_truth_hz": -150.0},
    ]
    good = audit_doppler_opportunity_v3(
        truths[0], axis, range_rate_closure_error_hz=0.0,
        range_rate_closure_tolerance_hz=0.01,
    )
    good.update({"period_id": 0, "beam_id": 50, "target_id": "T0"})
    bad = audit_doppler_opportunity_v3(
        truths[1], axis, range_rate_closure_error_hz=0.2,
        range_rate_closure_tolerance_hz=0.01,
    )
    bad.update({"period_id": 1, "beam_id": 50, "target_id": "T1"})
    detections = [
        {"det_id": "D0", "period_id": 0, "beam_id": 50, "range_bin": 100,
         "range_m": 10_000.0, "row": 35, "new_e": 0.0, "new_n": 0.0,
         "af_unwrapped_hz": -150.0, "prf_hz": 1000.0},
        {"det_id": "D1", "period_id": 1, "beam_id": 50, "range_bin": 101,
         "range_m": 10_000.0, "row": 35, "new_e": 0.0, "new_n": 0.0,
         "af_unwrapped_hz": -150.0, "prf_hz": 1000.0},
    ]
    result = evaluate_targets_v3(
        truths, detections, doppler_rows=100,
        doppler_axis_rows=[dict(axis, period_id=0, beam_id=50), dict(axis, period_id=1, beam_id=50)],
        range_axis_audit=range_axis,
        doppler_opportunity_audits=[good, bad],
    )
    assert result["evaluator_version"] == "r21-task-eval-v3"
    assert result["status"] == "EVALUABLE_WITH_EXCLUDED_OPPORTUNITIES"
    assert result["metrics"]["truth_opportunities"] == 1
    assert result["metrics"]["unique_hits"] == 1
    assert result["metrics"]["invalid_truth_opportunities"] == 1
    assert result["per_opportunity_status"][1]["status"] == "NOT_EVALUABLE"


def test_condition_with_no_physics_verified_opportunities_is_not_evaluable():
    truth = {"target_id": "T0", "period_id": 0, "beam_id": 50, "visible": "1",
             "range_bin": 100, "expected_bin": 100, "range_m": 10_000.0,
             "e_mid": 0.0, "n_mid": 0.0, "af_motion_truth_hz": -100.0,
             "af_geometry_truth_hz": -50.0, "af_total_truth_hz": -150.0}
    audit = audit_doppler_opportunity_v3(
        truth, {"doppler_axis_first_hz": -500.0, "doppler_bin_spacing_hz": 10.0,
                "doppler_row_count": 100, "doppler_axis_uniform": 1, "prf_hz": 1000.0},
        range_rate_closure_error_hz=1.0, range_rate_closure_tolerance_hz=0.01,
    )
    audit.update({"period_id": 0, "beam_id": 50, "target_id": "T0"})
    result = evaluate_targets_v3(
        [truth], [], doppler_rows=100, doppler_axis_rows=[],
        range_axis_audit=_range_axis(), doppler_opportunity_audits=[audit],
    )
    assert result["status"] == "NOT_EVALUABLE_NO_VALID_PHYSICS_VERIFIED_OPPORTUNITIES"
    assert result["metrics"]["truth_opportunities"] == 0
    assert result["metrics"]["unique_hits"] is None
    assert result["metrics"]["misses"] is None
    assert result["metrics"]["truth_opportunity_hit_fraction"] is None


def _observation(period, track, det, *, matched=True, payload=True):
    return {
        "period_id": period,
        "beam_id": 50,
        "track_id": track,
        "state": "Confirmed",
        "matched_this_frame": matched,
        "det_id": det,
        "output_payload": payload,
    }


def test_track_semantics_separate_never_matched_ids_from_mixed_ids_and_payload_rows():
    result = summarize_track_lineage_v3(
        observations=[
            _observation(0, "track-A", "D-target-0"),
            _observation(1, "track-A", "D-unmatched-A"),
            _observation(1, "track-N", "D-unmatched-N"),
            _observation(2, "track-B", "D-target-2"),
        ],
        target_matched_detection_keys={(0, 50, "D-target-0"), (2, 50, "D-target-2")},
        target_id_by_detection={(0, 50, "D-target-0"): "T0", (2, 50, "D-target-2"): "T0"},
        visible_truth_periods=[0, 1, 2],
    )
    assert result["status"] == "EVALUABLE"
    assert result["unique_track_ids_with_any_unmatched_payload"] == 2
    assert result["false_confirmed_track_ids_without_any_target_match"] == 1
    assert result["false_confirmed_track_ids"] == ["track-N"]
    assert result["mixed_target_matched_and_unmatched_track_ids"] == ["track-A"]
    assert result["unmatched_confirmed_payload_count"] == 2
    assert result["unmatched_payload_after_target_match_count"] == 1
    assert result["target_matched_period_ids"] == [0, 2]
    assert result["observed_sequential_target_track_handoffs"] == 1


def test_track_semantics_audit_track_id_associated_with_multiple_truths():
    result = summarize_track_lineage_v3(
        observations=[
            _observation(0, "track-X", "D0"),
            _observation(1, "track-X", "D1"),
        ],
        target_matched_detection_keys={(0, 50, "D0"), (1, 50, "D1")},
        target_id_by_detection={(0, 50, "D0"): "T0", (1, 50, "D1"): "T1"},
        visible_truth_periods=[0, 1],
    )
    assert result["track_ids_associated_with_multiple_target_ids"] == ["track-X"]
    assert result["multi_target_track_id_count"] == 1


def test_missing_post_confirmation_metric_has_explicit_non_evaluable_status():
    assert metric_status_v3(
        metric="post_confirmation_match_rate",
        value=None,
        base_status="evaluable",
        target_confirmation_period=None,
    ) == "NOT_EVALUABLE_NO_TARGET_CONFIRMATION"
    assert metric_status_v3(
        metric="post_confirmation_match_rate",
        value=0.0,
        base_status="evaluable",
        target_confirmation_period=1,
    ) == "EVALUABLE"
    assert metric_status_v3(
        metric="target_detection_hits",
        value=0,
        base_status="EVALUABLE",
    ) == "EVALUABLE"
