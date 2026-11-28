from scripts.phase_r.analyze_r22_v4_p0_freeze_readiness import (
    observed_period_handoffs,
    pairwise_common_rmse,
    result_period,
)


def test_pairwise_rmse_uses_only_finite_common_successes():
    common, rmse_a, rmse_b = pairwise_common_rmse(
        {"a": 3.0, "b": 4.0, "only_a": 100.0, "invalid": None},
        {"a": 4.0, "b": 3.0, "only_b": 100.0, "invalid": 9.0},
    )

    assert common == ["a", "b"]
    assert rmse_a == 3.5355339059327378
    assert rmse_b == 3.5355339059327378


def test_track_result_id_maps_to_zero_based_period():
    assert result_period("GMTI01") == 0
    assert result_period("GMTI10") == 9


def test_track_handoffs_compare_consecutive_observed_periods():
    # The evaluator compares adjacent observed target periods, even when the
    # period numbers have a gap; it does not count every calendar transition.
    assert observed_period_handoffs({
        1: {"track-a"},
        4: {"track-b"},
        5: {"track-b"},
        8: {"track-c"},
    }) == 2
