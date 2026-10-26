import pytest

from scripts.phase_r.r20_task_evaluator import (
    evaluate_targets_v2,
    expected_doppler_row_from_physics,
    map_track_observation_periods,
    summarize_track_lineage_v2,
    validate_comparison_identity_v2,
)


def truth(target_id="T0", period_id=0, **changes):
    row = {
        "target_id": target_id,
        "period_id": period_id,
        "beam_id": 50,
        "visible": "1",
        "range_bin": 100,
        "range_m": 10_000.0,
        "e_mid": 0.0,
        "n_mid": 0.0,
        "af_total_truth_hz": -150.0,
        "af_motion_truth_hz": -100.0,
        "af_geometry_truth_hz": -50.0,
        "vr_self_mps": 3.0,
    }
    row.update(changes)
    return row


def detection(det_id="D0", period_id=0, **changes):
    row = {
        "det_id": det_id,
        "period_id": period_id,
        "beam_id": 50,
        "range_bin": 100,
        "range_m": 10_000.0,
        "row": 35,
        "new_e": 10.0,
        "new_n": 5.0,
        "fd_ctr_unwrapped": 0.0,
        "prf_hz": 1000.0,
        "af_unwrapped_hz": -150.0,
        "ambiguity_status": "resolved",
        "radial_velocity_mps": 2.0,
        "platform_e": -1.0,
        "platform_n": -1.0,
    }
    row.update(changes)
    return row


SCHEMA = {
    "required_equal": ["seed", "binary_sha256", "config_sha256", "background_id"],
    "role_variable": ["condition", "delay_error_ns", "input_iq_sha256"],
    "optional_reported": ["runner_commit", "snapshot_runner_commit"],
}


def identity(**changes):
    row = {
        "seed": 606,
        "binary_sha256": "bin",
        "config_sha256": "cfg",
        "background_id": "bg606",
        "condition": "A0",
        "delay_error_ns": 0,
        "input_iq_sha256": "iq-a0",
        "runner_commit": "runner-a",
    }
    row.update(changes)
    return row


def test_identity_schema_allows_intervention_inputs_to_differ_but_reports_them():
    result = validate_comparison_identity_v2(
        [identity(), identity(condition="B", delay_error_ns=4, input_iq_sha256="iq-b", runner_commit="runner-b")],
        SCHEMA,
    )
    assert result["status"] == "IDENTITY_VALID"
    assert result["role_variable_values"]["input_iq_sha256"] == ["iq-a0", "iq-b"]
    assert result["optional_reported_values"]["runner_commit"] == ["runner-a", "runner-b"]


def test_identity_schema_marks_missing_required_schema_incomplete():
    incomplete = identity()
    del incomplete["config_sha256"]
    result = validate_comparison_identity_v2([identity(), incomplete], SCHEMA)
    assert result["status"] == "IDENTITY_INCOMPLETE"
    assert any("config_sha256" in row["fields"] for row in result["missing_fields"])


def test_identity_schema_rejects_mismatch_of_invariant_fields():
    result = validate_comparison_identity_v2(
        [identity(), identity(background_id="different")], SCHEMA,
    )
    assert result["status"] == "IDENTITY_MISMATCH"
    assert "background_id" in result["mismatches"]


def test_invalid_truth_is_audited_but_excluded_from_valid_miss_denominator():
    result = evaluate_targets_v2(
        [truth(), truth("bad", 1, e_mid="nan")],
        [detection()], doppler_rows=100,
    )
    metrics = result["metrics"]
    assert metrics["visible_truth_opportunities_total"] == 2
    assert metrics["truth_opportunities"] == 1
    assert metrics["invalid_truth_opportunities"] == 1
    assert metrics["misses"] == 0
    assert metrics["unique_hits"] == 1


def test_range_bin_fallback_only_applies_when_detection_field_is_missing():
    missing = detection(col=100)
    del missing["range_bin"]
    valid_result = evaluate_targets_v2([truth()], [missing], doppler_rows=100)
    assert valid_result["metrics"]["unique_hits"] == 1
    assert valid_result["metrics"]["range_bin_fallback_count"] == 1

    invalid = detection(col=100, range_bin="invalid")
    invalid_result = evaluate_targets_v2([truth()], [invalid], doppler_rows=100)
    assert invalid_result["metrics"]["unique_hits"] == 0
    assert invalid_result["metrics"]["present_invalid_range_bin_count"] == 1
    assert invalid_result["metrics"]["valid_detection_rows"] == 0
    assert invalid_result["metrics"]["invalid_detection_rows"] == 1
    assert any(row["reason"] == "invalid_present_range_bin" for row in invalid_result["candidate_pair_audit"])


def test_parameter_evaluability_uses_all_valid_truth_opportunities():
    result = evaluate_targets_v2(
        [truth(), truth("T1", 1)], [detection()], doppler_rows=100,
    )
    radial = result["parameter_evaluability"]["radial_velocity_mps"]
    assert radial["truth_opportunities"] == 2
    assert radial["matched_detection_opportunities"] == 1
    assert radial["valid_parameter_opportunities"] == 1
    assert radial["missing_parameter_opportunities"] == 1
    assert radial["missing_reasons"]["no_matched_detection"] == 1


def test_physical_doppler_mapper_folds_truth_and_respects_saved_axis_orientation():
    axis = {
        "doppler_axis_first_hz": -500.0,
        "doppler_bin_spacing_hz": 10.0,
        "doppler_row_count": 100,
        "doppler_axis_uniform": 1,
        "prf_hz": 1000.0,
    }
    result = expected_doppler_row_from_physics(
        {"af_motion_truth_hz": 160.0, "af_geometry_truth_hz": 0.0}, axis,
    )
    assert result["folded_truth_hz"] == pytest.approx(160.0)
    assert result["expected_row"] == 66

    reversed_axis = dict(axis, doppler_axis_first_hz=490.0, doppler_bin_spacing_hz=-10.0)
    reversed_result = expected_doppler_row_from_physics(
        {"af_motion_truth_hz": 160.0, "af_geometry_truth_hz": 0.0}, reversed_axis,
    )
    assert reversed_result["expected_row"] == 33


def test_track_period_mapping_uses_result_ids_and_rejects_event_conflicts():
    states = [{"result_id": "2", "track_id": "7", "utc": "11.050000190735"}]
    events = [{"result_id": "GMTI02", "period_id": "2", "track_id": "7", "utc": "11.05"}]
    mapped = map_track_observation_periods(states, events, {1: 0, 2: 1})
    assert mapped["observations"][0]["period_id"] == 1
    assert mapped["events"][0]["period_id"] == 1
    assert mapped["utc_period_conflicts_avoided"] == 1
    assert mapped["event_reported_period_mismatch_count"] == 1
    with pytest.raises(ValueError, match="unmapped TrackManager event"):
        map_track_observation_periods(states, [dict(events[0], result_id="GMTI04")], {1: 0, 2: 1})


def test_track_metrics_separate_observations_unique_periods_parallel_tracks_and_duplicates():
    observations = [
        {"period_id": 0, "beam_id": 50, "track_id": "7", "state": "Confirmed", "matched_this_frame": True,
         "det_id": "D0", "output_payload": True},
        {"period_id": 0, "beam_id": 50, "track_id": "8", "state": "Confirmed", "matched_this_frame": True,
         "det_id": "D0", "output_payload": True},
        {"period_id": 1, "beam_id": 50, "track_id": "7", "state": "Confirmed", "matched_this_frame": True,
         "det_id": "D1", "output_payload": True},
    ]
    result = summarize_track_lineage_v2(
        observations=observations,
        target_matched_detection_keys={(0, 50, "D0"), (1, 50, "D1")},
        visible_truth_periods=[0, 1, 2],
        payload_duplicate_key_count=1,
    )
    assert result["confirmed_observation_count"] == 3
    assert result["confirmed_unique_period_count"] == 2
    assert result["maximum_simultaneous_target_track_count"] == 2
    assert result["periods_with_simultaneous_target_tracks"] == 1
    assert result["post_confirmation_truth_periods"] == [0, 1, 2]
    assert result["post_confirmation_matched_periods"] == 2
    assert result["payload_duplicate_key_count"] == 1
    assert result["track_fragmentation_count_observed"] == 0
