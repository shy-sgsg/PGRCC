import pytest

from scripts.phase_r.r19_task_evaluator import common_success_keys, rmse_from_samples
from scripts.phase_r.r20_task_evaluator import (
    evaluate_targets_v2,
    expected_doppler_row_from_physics,
    map_track_observation_periods,
    range_rate_doppler_from_history,
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


def test_range_history_independently_recovers_signed_bistatic_doppler():
    center_utc = 4.95
    pulse_rate = 1300.0
    range_rate = 43.4052588868
    rows = [
        {
            "target_id": "T0", "period_id": 0, "pulse_id": pulse,
            "utc": center_utc + (pulse - 65) / pulse_rate,
            "range_m": 87_919.0 + range_rate * (pulse - 65) / pulse_rate,
        }
        for pulse in range(60, 71)
    ]
    result = range_rate_doppler_from_history(
        rows,
        {"target_id": "T0", "period_id": 0, "utc_mid": center_utc},
        carrier_frequency_hz=16e9,
        carrier_phase_sign=-1,
    )
    assert result["status"] == "VERIFIED_RANGE_RATE_DOPPLER"
    assert result["range_rate_mps"] == pytest.approx(range_rate, abs=1e-8)
    assert result["frequency_hz"] == pytest.approx(-2 * range_rate / (299_792_458 / 16e9), abs=1e-6)
    assert result["sample_count"] == 7


def test_range_history_rejects_insufficient_samples_and_bad_waveform_identity():
    rows = [
        {"target_id": "T0", "period_id": 0, "pulse_id": 1, "utc": 0.0, "range_m": 100.0},
        {"target_id": "T0", "period_id": 0, "pulse_id": 2, "utc": 1.0, "range_m": 101.0},
    ]
    result = range_rate_doppler_from_history(
        rows, {"target_id": "T0", "period_id": 0, "utc_mid": 0.5},
        carrier_frequency_hz=16e9, carrier_phase_sign=-1,
    )
    assert result["status"] == "NOT_EVALUABLE_INSUFFICIENT_RANGE_SAMPLES"
    invalid = range_rate_doppler_from_history(
        rows, {"target_id": "T0", "period_id": 0, "utc_mid": 0.5},
        carrier_frequency_hz=0, carrier_phase_sign=-1,
    )
    assert invalid["status"] == "NOT_EVALUABLE_INVALID_WAVEFORM_IDENTITY"


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


@pytest.mark.parametrize(
    ("truth_utc", "production_utc", "result_id", "period_id"),
    [
        (4.95, 4.949999809265, "GMTI01", 0),
        (11.05, 11.050000190735, "GMTI02", 1),
        (17.15, 17.149999618530, "GMTI03", 2),
    ],
)
def test_float32_utc_quantization_never_changes_exact_result_period_mapping(
    truth_utc, production_utc, result_id, period_id,
):
    result_number = int(result_id.removeprefix("GMTI"))
    mapped = map_track_observation_periods(
        [{"result_id": result_id, "track_id": "7", "utc": production_utc}],
        [{"result_id": result_id, "track_id": "7", "utc": truth_utc}],
        {1: 0, 2: 1, 3: 2},
    )
    assert mapped["observations"][0]["period_id"] == period_id
    assert mapped["events"][0]["period_id"] == period_id
    assert mapped["utc_period_conflicts_avoided"] == 1
    assert result_number == period_id + 1


def test_track_period_mapping_fails_closed_for_missing_state_or_event_result_id():
    period_map = {1: 0, 2: 1, 3: 2}
    with pytest.raises(ValueError, match="unmapped TrackManager state"):
        map_track_observation_periods([{"result_id": "GMTI04", "track_id": "7"}], [], period_map)
    with pytest.raises(ValueError, match="unmapped TrackManager event"):
        map_track_observation_periods([], [{"result_id": "unknown", "track_id": "7"}], period_map)


def test_track_confirmation_denominator_keeps_unmatched_confirmation_period_as_a_miss():
    observations = [
        {"period_id": 0, "beam_id": 50, "track_id": "7", "state": "Tentative",
         "matched_this_frame": True, "det_id": "D0", "output_payload": False},
        {"period_id": 1, "beam_id": 50, "track_id": "7", "state": "Confirmed",
         "matched_this_frame": False, "det_id": "", "output_payload": True},
        {"period_id": 2, "beam_id": 50, "track_id": "7", "state": "Confirmed",
         "matched_this_frame": True, "det_id": "D2", "output_payload": True},
    ]
    result = summarize_track_lineage_v2(
        observations=observations,
        target_matched_detection_keys={(0, 50, "D0"), (2, 50, "D2")},
        visible_truth_periods=[0, 1, 2],
    )
    assert result["first_target_confirmation_period"] == 1
    assert result["post_confirmation_truth_periods"] == [1, 2]
    assert result["post_confirmation_matched_periods"] == 1
    assert result["post_confirmation_match_rate"] == 0.5


def test_track_summary_reports_multi_target_lineage_and_same_period_parallel_tracks():
    observations = [
        {"period_id": 0, "beam_id": 50, "track_id": "track-A", "state": "Confirmed",
         "matched_this_frame": True, "det_id": "DA", "output_payload": True},
        {"period_id": 0, "beam_id": 50, "track_id": "track-B", "state": "Confirmed",
         "matched_this_frame": True, "det_id": "DB", "output_payload": True},
    ]
    result = summarize_track_lineage_v2(
        observations=observations,
        target_matched_detection_keys={(0, 50, "DA"), (0, 50, "DB")},
        target_id_by_detection={(0, 50, "DA"): "T0", (0, 50, "DB"): "T1"},
        visible_truth_periods=[0],
    )
    by_target = {row["target_id"]: row for row in result["target_lineage_by_target"]}
    assert result["maximum_simultaneous_target_track_count"] == 2
    assert by_target["T0"]["matched_period_ids"] == [0]
    assert by_target["T1"]["matched_period_ids"] == [0]
    assert by_target["T0"]["track_ids"] == ["track-A"]
    assert by_target["T1"]["track_ids"] == ["track-B"]


def test_track_summary_does_not_promote_confirmed_unmatched_payload_to_target_lineage():
    result = summarize_track_lineage_v2(
        observations=[{
            "period_id": 0, "beam_id": 50, "track_id": "false-track", "state": "Confirmed",
            "matched_this_frame": True, "det_id": "Dfalse", "output_payload": True,
        }],
        target_matched_detection_keys=set(),
        visible_truth_periods=[0],
    )
    assert result["target_matched_periods"] == 0
    assert result["false_confirmed_track_count"] == 1
    assert result["post_confirmation_match_rate"] is None


def test_duplicate_track_observation_is_an_explicit_data_error():
    observation = {"period_id": 0, "beam_id": 50, "track_id": "7", "state": "Confirmed",
                   "matched_this_frame": True, "det_id": "D0", "output_payload": True}
    with pytest.raises(ValueError, match="duplicate TrackManager observation"):
        summarize_track_lineage_v2(
            observations=[observation, dict(observation)],
            target_matched_detection_keys={(0, 50, "D0")},
            visible_truth_periods=[0],
        )


def test_doppler_axis_validation_rejects_missing_invalid_and_nonuniform_axes():
    truth_row = {"af_motion_truth_hz": -100, "af_geometry_truth_hz": -50}
    missing = expected_doppler_row_from_physics(truth_row, {})
    assert missing["status"] == "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED"
    axis = {
        "doppler_axis_first_hz": -500, "doppler_bin_spacing_hz": 10,
        "doppler_row_count": 100, "doppler_axis_uniform": 1, "prf_hz": 1000,
    }
    nonuniform = expected_doppler_row_from_physics(truth_row, dict(axis, doppler_axis_uniform=0))
    assert nonuniform["status"] == "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED"
    invalid = expected_doppler_row_from_physics(truth_row, dict(axis, doppler_bin_spacing_hz=0))
    assert invalid["status"] == "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED"
    mismatched = expected_doppler_row_from_physics(truth_row, dict(axis, doppler_row_count=99))
    assert mismatched["status"] == "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED"
    assert mismatched["reason"] == "production_axis_does_not_close_prf"


def test_parameter_denominators_and_missing_reasons_are_independent_by_parameter():
    truths = [truth("T0", 0), truth("T1", 1), truth("T2", 2)]
    detections = [
        detection("D0", 0, ambiguity_status="unresolved"),
        detection("D1", 1, platform_e=None, platform_n=None, ambiguity_status="resolved"),
    ]
    result = evaluate_targets_v2(truths, detections, doppler_rows=100)
    parameters = result["parameter_evaluability"]
    assert parameters["angle_deg"]["truth_opportunities"] == 3
    assert parameters["angle_deg"]["valid_parameter_opportunities"] == 1
    assert parameters["angle_deg"]["missing_reasons"] == {
        "missing_platform_position": 1, "no_matched_detection": 1,
    }
    assert parameters["radial_velocity_mps"]["valid_parameter_opportunities"] == 1
    assert parameters["radial_velocity_mps"]["missing_reasons"]["ambiguity_unresolved"] == 1
    assert parameters["radial_velocity_mps"]["missing_reasons"]["no_matched_detection"] == 1


def test_no_matched_detection_marks_every_parameter_missing_and_common_set_empty():
    result = evaluate_targets_v2([truth()], [], doppler_rows=100)
    assert result["metrics"]["truth_opportunities"] == 1
    assert result["metrics"]["misses"] == 1
    assert all(
        metric["missing_parameter_opportunities"] == 1
        and metric["missing_reasons"] == {"no_matched_detection": 1}
        for metric in result["parameter_evaluability"].values()
    )
    assert common_success_keys({"A0": [], "B": []}) == set()


def test_duplicate_parameter_opportunity_samples_fail_closed():
    samples = [
        {"sample_key": "T0/0/50", "error": 1.0},
        {"sample_key": "T0/0/50", "error": 2.0},
    ]
    with pytest.raises(ValueError, match="duplicate RMSE opportunity key"):
        rmse_from_samples(samples)
    with pytest.raises(ValueError, match="duplicate common-success opportunity"):
        common_success_keys({"A0": samples, "B": [{"sample_key": "T0/0/50", "error": 0.5}]})


def test_identity_detects_binary_and_config_mismatch_but_allows_paired_input_hashes():
    result = validate_comparison_identity_v2(
        [identity(), identity(condition="Known", delay_error_ns=4, input_iq_sha256="iq-known")],
        SCHEMA,
    )
    assert result["status"] == "IDENTITY_VALID"
    assert result["role_variable_values"]["input_iq_sha256"] == ["iq-a0", "iq-known"]
    assert validate_comparison_identity_v2(
        [identity(), identity(binary_sha256="other")], SCHEMA,
    )["status"] == "IDENTITY_MISMATCH"
    assert validate_comparison_identity_v2(
        [identity(), identity(config_sha256="other")], SCHEMA,
    )["status"] == "IDENTITY_MISMATCH"


def test_track_loss_after_confirmation_is_a_postconfirmation_miss():
    result = summarize_track_lineage_v2(
        observations=[{
            "period_id": 1, "beam_id": 50, "track_id": "7", "state": "Confirmed",
            "matched_this_frame": True, "det_id": "D1", "output_payload": True,
        }],
        target_matched_detection_keys={(1, 50, "D1")},
        target_id_by_detection={(1, 50, "D1"): "T0"},
        visible_truth_periods=[0, 1, 2],
    )
    assert result["first_target_confirmation_period"] == 1
    assert result["post_confirmation_truth_periods"] == [1, 2]
    assert result["post_confirmation_matched_periods"] == 1
    assert result["post_confirmation_miss_periods"] == [2]
    assert result["post_confirmation_match_rate"] == 0.5


def test_track_metric_separates_matched_target_from_coexisting_unmatched_payload():
    result = summarize_track_lineage_v2(
        observations=[
            {"period_id": 0, "beam_id": 50, "track_id": "target", "state": "Confirmed",
             "matched_this_frame": True, "det_id": "D0", "output_payload": True},
            {"period_id": 0, "beam_id": 50, "track_id": "false", "state": "Confirmed",
             "matched_this_frame": True, "det_id": "DF", "output_payload": True},
        ],
        target_matched_detection_keys={(0, 50, "D0")},
        visible_truth_periods=[0],
    )
    assert result["target_matched_period_ids"] == [0]
    assert result["unmatched_confirmed_payload_count"] == 1
    assert result["false_confirmed_track_count"] == 1


def test_track_root_cause_absence_does_not_turn_lineage_into_id_switch_explanation():
    result = summarize_track_lineage_v2(
        observations=[{
            "period_id": 0, "beam_id": 50, "track_id": "7", "state": "Confirmed",
            "matched_this_frame": True, "det_id": "D0", "output_payload": True,
        }],
        target_matched_detection_keys={(0, 50, "D0")},
        visible_truth_periods=[0],
    )
    assert result["track_fragmentation_semantics"].startswith("adjacent matched target periods")
    assert "cause" not in result["track_fragmentation_semantics"].lower()
