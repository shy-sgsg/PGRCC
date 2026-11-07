from scripts.phase_r.r21_finalize_evidence import (
    ground_speed_parameter_samples,
    pairwise_common_success_rows,
)


def test_pairwise_common_success_uses_only_shared_opportunities():
    rows = pairwise_common_success_rows(
        seed=1001,
        metric="angle_deg",
        samples_by_method={
            "M1": [
                {"sample_key": "target/0/50", "error": 3.0},
                {"sample_key": "target/1/50", "error": 4.0},
            ],
            "Huber": [
                {"sample_key": "target/0/50", "error": 5.0},
                {"sample_key": "target/2/50", "error": 12.0},
            ],
        },
        pairs=[("M1", "Huber")],
    )

    assert rows == [
        {
            "seed": 1001,
            "metric": "angle_deg",
            "method": "M1",
            "reference": "Huber",
            "status": "EVALUABLE_DESCRIPTIVE_COMMON_SUCCESS",
            "method_success_count": 2,
            "reference_success_count": 2,
            "common_success_opportunities": 1,
            "method_rmse_common": 3.0,
            "reference_rmse_common": 5.0,
            "rmse_difference_method_minus_reference": -2.0,
            "common_sample_keys": '["target/0/50"]',
        }
    ]


def test_pairwise_common_success_marks_empty_intersection_not_evaluable():
    rows = pairwise_common_success_rows(
        seed=1002,
        metric="radial_velocity_mps",
        samples_by_method={
            "M1": [{"sample_key": "target/0/50", "error": 1.0}],
            "Known": [{"sample_key": "target/1/50", "error": 2.0}],
        },
        pairs=[("M1", "Known")],
    )

    assert rows[0]["status"] == "NOT_EVALUABLE_NO_COMMON_SUCCESS"
    assert rows[0]["common_success_opportunities"] == 0
    assert rows[0]["method_rmse_common"] is None
    assert rows[0]["reference_rmse_common"] is None


def test_ground_speed_samples_require_one_current_confirmed_payload():
    samples = ground_speed_parameter_samples(
        truths=[
            {"target_id": "target", "period_id": "0", "beam_id": "50", "ve_mps": "3", "vn_mps": "4"},
            {"target_id": "target", "period_id": "1", "beam_id": "50", "ve_mps": "0", "vn_mps": "5"},
        ],
        associations=[
            {"sample_key": "target/0/50", "matched": True, "det_id": "D0"},
            {"sample_key": "target/1/50", "matched": True, "det_id": "D1"},
        ],
        observations=[
            {
                "period_id": "0", "beam_id": "50", "det_id": "D0", "track_id": "7",
                "state": "Confirmed", "matched_this_frame": "true", "output_payload": "true",
                "payload_rows_for_key": "1", "output_speed": "10",
            },
            {
                "period_id": "1", "beam_id": "50", "det_id": "D1", "track_id": "8",
                "state": "Tentative", "matched_this_frame": "true", "output_payload": "true",
                "payload_rows_for_key": "1", "output_speed": "5",
            },
        ],
    )

    assert samples == [{"sample_key": "target/0/50", "error": 5.0}]


def test_ground_speed_samples_exclude_duplicate_confirmed_payloads():
    samples = ground_speed_parameter_samples(
        truths=[{"target_id": "target", "period_id": "0", "beam_id": "50", "ve_mps": "3", "vn_mps": "4"}],
        associations=[{"sample_key": "target/0/50", "matched": True, "det_id": "D0"}],
        observations=[
            {
                "period_id": "0", "beam_id": "50", "det_id": "D0", "track_id": track,
                "state": "Confirmed", "matched_this_frame": "true", "output_payload": "true",
                "payload_rows_for_key": "2", "output_speed": "10",
            }
            for track in ("7", "8")
        ],
    )

    assert samples == []
