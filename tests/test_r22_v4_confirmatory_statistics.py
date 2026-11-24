from __future__ import annotations

import math

import pytest

from scripts.phase_r.r22_v4_confirmatory_statistics import (
    aggregate_seed_level_primary,
    aggregate_h0_seed_results,
    bootstrap_mean_interval,
    joint_bootstrap_monte_carlo_audit,
    h0_seed_statistics,
    h0_zero_event_sample_size,
    joint_position_coverage_power,
    joint_position_coverage_power_grid,
    position_power,
    position_test_contract,
    zero_event_upper_bound,
)


HISTORICAL_POSITION_SD = 4.7967835646439765


def _planned_keys(rows):
    return sorted({
        (float(row["speed_mps"]), str(row["target_id"]), int(row["period"]))
        for row in rows
    })


def test_position_schemes_match_distinct_confirmatory_claims():
    scheme_a = position_test_contract(scheme="A", mde_m=3.0)
    scheme_b = position_test_contract(scheme="B", mde_m=3.0)

    assert scheme_a["null_boundary_delta_m"] == 0.0
    assert scheme_b["null_boundary_delta_m"] == -3.0
    assert scheme_a["success_rule"] == "two_sided_95pct_studentized_bootstrap_t_upper_below_zero"
    assert scheme_b["success_rule"] == "two_sided_95pct_studentized_bootstrap_t_upper_below_minus_mde"

    p_a = position_power(
        n=30, seed_sd=HISTORICAL_POSITION_SD, true_improvement_m=3.0,
        scheme="A", mde_m=3.0,
    )["power"]
    p_b_at_boundary = position_power(
        n=30, seed_sd=HISTORICAL_POSITION_SD, true_improvement_m=3.0,
        scheme="B", mde_m=3.0,
    )["power"]
    p_b_above_boundary = position_power(
        n=30, seed_sd=HISTORICAL_POSITION_SD, true_improvement_m=6.0,
        scheme="B", mde_m=3.0,
    )["power"]

    assert 0.88 < p_a < 0.94
    assert 0.015 < p_b_at_boundary < 0.035
    assert abs(p_b_above_boundary - p_a) < 1e-10
    scheme_a = position_power(
        n=30, seed_sd=HISTORICAL_POSITION_SD, true_improvement_m=3.0,
        scheme="A", mde_m=3.0,
    )
    assert 0.45 < scheme_a["mde_point_estimate_pass_probability"] < 0.55


def test_position_power_distinguishes_one_sided_and_two_sided_upper_bounds():
    two_sided = position_power(
        n=30, seed_sd=HISTORICAL_POSITION_SD, true_improvement_m=3.0,
            scheme="A", mde_m=0.0, upper_confidence=0.975,
    )
    one_sided_95 = position_power(
        n=30, seed_sd=HISTORICAL_POSITION_SD, true_improvement_m=3.0,
        scheme="A", mde_m=0.0, upper_confidence=0.95,
    )

    assert two_sided["critical_t"] > one_sided_95["critical_t"]
    assert one_sided_95["power"] > two_sided["power"]
    assert math.isclose(two_sided["alpha_at_boundary"], 0.025)
    assert math.isclose(one_sided_95["alpha_at_boundary"], 0.05)


@pytest.mark.parametrize("n", [20, 30, 40, 60, 100])
@pytest.mark.parametrize("mde", [3.0, 6.0, 12.0])
def test_strict_position_power_is_finite_when_effect_is_far_below_mde(n: int, mde: float):
    result = position_power(
        n=n, seed_sd=HISTORICAL_POSITION_SD, true_improvement_m=0.0,
        scheme="B", mde_m=mde,
    )
    assert math.isfinite(result["power"])
    assert 0.0 <= result["power"] <= 1.0


def test_joint_power_is_reproducible_and_reports_marginal_and_joint_events():
    result_a = joint_position_coverage_power(
        n=30,
        position_seed_sd_m=HISTORICAL_POSITION_SD,
        coverage_seed_sd_pp=0.4949237263957897,
        position_true_improvement_m=6.0,
        coverage_true_delta_pp=0.0,
        position_mde_m=3.0,
        coverage_ni_margin_pp=0.5,
        correlation=0.4,
        variance_scale=1.0,
        replicates=12_000,
        seed=22029,
    )
    result_b = joint_position_coverage_power(
        n=30,
        position_seed_sd_m=HISTORICAL_POSITION_SD,
        coverage_seed_sd_pp=0.4949237263957897,
        position_true_improvement_m=6.0,
        coverage_true_delta_pp=0.0,
        position_mde_m=3.0,
        coverage_ni_margin_pp=0.5,
        correlation=0.4,
        variance_scale=1.0,
        replicates=12_000,
        seed=22029,
    )

    assert result_a == result_b
    assert 0.0 <= result_a["joint_pass_probability"] <= 1.0
    assert result_a["joint_pass_probability"] <= result_a["position_pass_probability"]
    assert result_a["joint_pass_probability"] <= result_a["coverage_ni_pass_probability"]
    assert result_a["independent_unit"] == "seed"

    scheme_a = joint_position_coverage_power(
        n=30, position_seed_sd_m=HISTORICAL_POSITION_SD,
        coverage_seed_sd_pp=0.4949237263957897,
        position_true_improvement_m=3.0, coverage_true_delta_pp=0.0,
        position_mde_m=3.0, coverage_ni_margin_pp=0.5,
        correlation=0.4, replicates=12_000, seed=22029,
        position_scheme="A",
    )
    assert scheme_a["position_statistical_pass_probability"] > scheme_a[
        "position_pass_probability"
    ]
    assert abs(scheme_a["position_point_estimate_mde_pass_probability"] - 0.5) < 0.03


def test_joint_power_grid_contains_schemes_and_correlation_sensitivity():
    rows = joint_position_coverage_power_grid(
        position_seed_sd_m=HISTORICAL_POSITION_SD,
        coverage_seed_sd_pp=0.4949237263957897,
        sample_sizes=(20,), position_mdes_m=(3.0,),
        coverage_ni_margins_pp=(0.5,),
        true_position_improvements_m=(3.0,), true_coverage_deltas_pp=(0.0,),
        variance_scales=(1.0,), correlations=(-0.5, 0.5),
        replicates=4_000, seed=22029,
    )
    assert len(rows) == 4
    assert {row["position_scheme"] for row in rows} == {"A", "B"}
    assert {row["assumed_seed_correlation"] for row in rows} == {-0.5, 0.5}


def test_paired_bootstrap_monte_carlo_uses_seed_clusters_and_checks_joint_coverage():
    result = joint_bootstrap_monte_carlo_audit(
        n=10, position_seed_sd_m=2.0, coverage_seed_sd_pp=0.5,
        position_true_delta_m=-1.0, coverage_true_delta_pp=0.0,
        position_boundary_m=0.0, coverage_ni_margin_pp=0.5,
        correlation=0.3, datasets=200, bootstrap_replicates=300,
        seed=22029, position_scheme="A", position_mde_m=1.0,
    )
    assert result["independent_unit"] == "synthetic seed"
    assert 0.0 <= result["joint_pass_probability"] <= 1.0
    assert 0.0 <= result["position_interval_coverage"] <= 1.0
    assert "not a GMTI experiment" in result["simulation_kind"]


def test_seed_bootstrap_is_deterministic_and_uses_seed_level_values():
    values = [-4.0, -3.0, -2.0, -1.0]
    first = bootstrap_mean_interval(values, replicates=4_000, seed=22029)
    second = bootstrap_mean_interval(values, replicates=4_000, seed=22029)

    assert first == second
    assert first["independent_unit"] == "seed"
    assert first["n"] == 4
    assert first["mean"] == -2.5
    assert first["lower"] < first["mean"] < first["upper"]
    bca = bootstrap_mean_interval(values, replicates=4_000, seed=22029, method="bca")
    assert bca["method"] == "seed-cluster BCa bootstrap"
    assert bca["lower"] < bca["mean"] < bca["upper"]
    studentized = bootstrap_mean_interval(
        values, replicates=4_000, seed=22029, method="bootstrap_t",
    )
    assert studentized["method"] == "seed-cluster studentized bootstrap-t"
    assert studentized["lower"] < studentized["mean"] < studentized["upper"]


def test_primary_endpoint_equal_weights_speeds_and_retains_missing_opportunities():
    rows = []
    for speed, m1_errors, fixed_errors in (
        (0.5, [3.0, 3.0], [4.0, 4.0]),
        (2.5, [2.0], [3.0]),
        (6.7, [1.0, 1.0, 1.0], [2.0, 2.0, 2.0]),
    ):
        count = max(len(m1_errors), len(fixed_errors)) + 1
        for i in range(count):
            rows.append({
                "seed": 101,
                "speed_mps": speed,
                "target_id": f"t{i}",
                "period": 0,
                "m1_matched": i < len(m1_errors),
                "fixed_matched": i < len(fixed_errors),
                "m1_position_error_m": m1_errors[i] if i < len(m1_errors) else None,
                "fixed_position_error_m": fixed_errors[i] if i < len(fixed_errors) else None,
                "production_status": "PASSED",
                "truth_status": "VALID",
            })

    result = aggregate_seed_level_primary(
        rows, planned_opportunity_keys=_planned_keys(rows),
    )
    seed = result["seed_results"][0]

    assert seed["status"] == "EVALUABLE_LOW_COMMON_SUPPORT"
    assert seed["speed_strata_count"] == 3
    assert seed["primary_delta_m"] == pytest.approx(-1.0)
    assert seed["total_opportunity_count"] == 9
    assert seed["common_success_count"] == 6
    assert seed["m1_coverage"] < 1.0
    assert result["independent_unit"] == "seed"


def test_primary_endpoint_zero_common_success_and_technical_failure_are_not_dropped():
    rows = []
    for speed in (0.5, 2.5, 6.7):
        rows.append({
            "seed": 202,
            "speed_mps": speed,
            "target_id": f"target-{speed}",
            "period": 0,
            "m1_matched": False,
            "fixed_matched": True,
            "m1_position_error_m": None,
            "fixed_position_error_m": 3.0,
            "production_status": "PASSED",
            "truth_status": "VALID",
        })
    rows.append({**rows[-1], "seed": 303, "production_status": "FAILED"})

    result = aggregate_seed_level_primary(
        rows, planned_opportunity_keys=_planned_keys(rows),
    )
    by_seed = {row["seed"]: row for row in result["seed_results"]}

    assert by_seed[202]["status"] == "NOT_EVALUABLE_NO_COMMON_SUCCESS"
    assert by_seed[202]["primary_delta_m"] is None
    assert by_seed[303]["status"] == "TECHNICAL_FAILURE"
    assert result["seed_count_planned"] == 2
    assert result["seed_count_evaluable"] == 0


def test_primary_endpoint_rejects_duplicate_identity_nonfinite_error_and_bad_truth():
    base = {
        "seed": 1, "speed_mps": 0.5, "target_id": "t1", "period": 0,
        "m1_matched": True, "fixed_matched": True,
        "m1_position_error_m": 2.0, "fixed_position_error_m": 3.0,
        "production_status": "PASSED", "truth_status": "VALID",
    }
    with pytest.raises(ValueError, match="duplicate opportunity"):
        aggregate_seed_level_primary(
            [base, dict(base)],
            planned_opportunity_keys=[(0.5, "t1", 0), (2.5, "t2.5", 0), (6.7, "t6.7", 0)],
        )
    malformed = [
        {**base, "speed_mps": speed, "target_id": f"t{speed}"}
        for speed in (0.5, 2.5, 6.7)
    ]
    with pytest.raises(ValueError, match="finite non-negative"):
        aggregate_seed_level_primary(
            [
                {**row, "m1_position_error_m": float("nan")} if row["speed_mps"] == 0.5 else row
                for row in malformed
            ],
            planned_opportunity_keys=_planned_keys(malformed),
        )
    with pytest.raises(ValueError, match="truth identity"):
        aggregate_seed_level_primary(
            [{**base, "target_id": ""}],
            planned_opportunity_keys=[(0.5, "t1", 0), (2.5, "t2", 0), (6.7, "t3", 0)],
        )


def test_primary_endpoint_marks_silent_missing_opportunity_rows_inconclusive():
    rows = []
    for speed in (0.5, 2.5, 6.7):
        for target_id in ("t0", "t1"):
            rows.append({
                "seed": 404, "speed_mps": speed, "target_id": target_id, "period": 0,
                "m1_matched": True, "fixed_matched": True,
                "m1_position_error_m": 1.0, "fixed_position_error_m": 2.0,
                "production_status": "PASSED", "truth_status": "VALID",
            })
    rows.remove(next(row for row in rows if row["speed_mps"] == 2.5 and row["target_id"] == "t1"))
    planned = [
        (speed, target_id, 0)
        for speed in (0.5, 2.5, 6.7)
        for target_id in ("t0", "t1")
    ]
    result = aggregate_seed_level_primary(rows, planned_opportunity_keys=planned)
    seed = result["seed_results"][0]
    assert seed["status"] == "NOT_EVALUABLE_MISSING_OPPORTUNITY_RECORDS"
    assert seed["missing_opportunity_counts_by_speed"] == {"2.5": 1}
    assert result["seed_count_evaluable"] == 0


def test_h0_reports_seed_events_period_fraction_and_unique_track_ids_separately():
    result = h0_seed_statistics(
        period_events=[False, True, False, True],
        valid_periods=[True, True, True, True],
        false_confirmed_track_ids=["trk-1", "trk-1", "trk-2"],
    )

    assert result["seed_any_event"] == 1
    assert result["period_event_count"] == 2
    assert result["period_event_fraction"] == 0.5
    assert result["false_confirmed_unique_track_count"] == 2
    assert result["independent_unit"] == "seed"
    zero_event_bound = zero_event_upper_bound(30, confidence=0.95)
    assert zero_event_bound == pytest.approx(0.09503385285530411)


def test_h0_missing_or_invalid_period_does_not_count_as_no_event():
    result = h0_seed_statistics(
        period_events=[False, None, False],
        valid_periods=[True, False, True],
        false_confirmed_track_ids=[],
    )

    assert result["status"] == "NOT_EVALUABLE_INCOMPLETE_PERIODS"
    assert result["seed_any_event"] is None
    assert result["period_event_fraction"] is None


def test_h0_aggregation_keeps_seed_as_unit_and_reports_candidate_zero_event_n():
    rows = [
        h0_seed_statistics(
            period_events=[False, False], valid_periods=[True, True],
            false_confirmed_track_ids=[],
        ) | {"seed": seed}
        for seed in (1, 2, 3)
    ]
    result = aggregate_h0_seed_results(
        rows, planned_seeds=(1, 2, 3), bootstrap_replicates=500, bootstrap_seed=22029,
    )
    assert result["status"] == "EVALUABLE_COMPLETE_H0_SEED_SET"
    assert result["seed_any_event"]["event_seed_count"] == 0
    assert result["seed_any_event"]["one_sided_95pct_zero_event_upper_bound"] is not None
    assert result["period_event_fraction"]["periods_treated_as_independent"] is False
    assert h0_zero_event_sample_size(probability_upper_limit=0.05) == 59


def test_h0_incomplete_seed_set_cannot_pass_or_report_zero_event_bound():
    row = h0_seed_statistics(
        period_events=[False, False], valid_periods=[True, True],
        false_confirmed_track_ids=[],
    ) | {"seed": 1}
    result = aggregate_h0_seed_results(
        [row], planned_seeds=(1, 2), bootstrap_replicates=200,
    )
    assert result["status"] == "INCOMPLETE_H0_SEED_SET"
    assert result["missing_or_invalid_seeds"] == [2]
    assert result["seed_any_event"]["one_sided_95pct_zero_event_upper_bound"] is None
