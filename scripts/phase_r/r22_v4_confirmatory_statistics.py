#!/usr/bin/env python3
"""Seed-level statistical contracts for the R22-V4 confirmatory design.

This module contains planning calculations and deterministic evaluators only.
It does not run Stage2, Production, CUDA, or a GMTI experiment.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from scipy.stats import beta, nct, norm, t


DEFAULT_SPEEDS_MPS = (0.5, 2.5, 6.7)
SUCCESS_PRODUCTION_STATES = {"PASSED", "OK", "SUCCEEDED", "SUCCESS"}
SUCCESS_TRUTH_STATES = {"VALID", "PASSED", "OK", "TRUE"}


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _bootstrap_interval(
    values: np.ndarray,
    bootstrap_statistics: np.ndarray,
    *,
    confidence: float,
    method: str,
) -> tuple[float, float]:
    alpha = (1.0 - confidence) / 2.0
    if method == "percentile":
        lower, upper = np.quantile(
            bootstrap_statistics, [alpha, 1.0 - alpha], method="linear",
        )
    elif method == "bca":
        observed = float(np.mean(values))
        less = np.count_nonzero(bootstrap_statistics < observed)
        ties = np.count_nonzero(bootstrap_statistics == observed)
        proportion = (less + 0.5 * ties) / bootstrap_statistics.size
        epsilon = 0.5 / bootstrap_statistics.size
        z0 = float(norm.ppf(min(1.0 - epsilon, max(epsilon, proportion))))
        jackknife = (float(np.sum(values)) - values) / (values.size - 1)
        centered = jackknife - float(np.mean(jackknife))
        sum_squares = float(np.sum(centered * centered))
        acceleration = (
            float(np.sum(centered ** 3)) / (6.0 * sum_squares ** 1.5)
            if sum_squares > 0.0 else 0.0
        )
        adjusted: list[float] = []
        for quantile in (alpha, 1.0 - alpha):
            z_alpha = float(norm.ppf(quantile))
            denominator = 1.0 - acceleration * (z0 + z_alpha)
            if abs(denominator) < 1e-12:
                raise ArithmeticError("BCa adjusted quantile denominator is singular")
            adjusted_z = z0 + (z0 + z_alpha) / denominator
            adjusted.append(float(norm.cdf(adjusted_z)))
        lower, upper = np.quantile(
            bootstrap_statistics,
            [min(1.0, max(0.0, adjusted[0])), min(1.0, max(0.0, adjusted[1]))],
            method="linear",
        )
    elif method == "bootstrap_t":
        lower_q, upper_q = np.quantile(
            bootstrap_statistics, [alpha, 1.0 - alpha], method="linear",
        )
        observed_se = float(np.std(values, ddof=1) / math.sqrt(values.size))
        estimate = float(np.mean(values))
        lower, upper = estimate - upper_q * observed_se, estimate - lower_q * observed_se
    else:
        raise ValueError("bootstrap interval method must be percentile, bca, or bootstrap_t")
    return float(lower), float(upper)


def _studentized_bootstrap_statistics(
    observed: np.ndarray, resamples: np.ndarray,
) -> np.ndarray:
    n = observed.size
    estimates = resamples.mean(axis=1)
    standard_errors = resamples.std(axis=1, ddof=1) / math.sqrt(n)
    numerator = estimates - float(np.mean(observed))
    result = np.empty_like(numerator)
    nonzero = standard_errors > 0.0
    result[nonzero] = numerator[nonzero] / standard_errors[nonzero]
    zero_and_positive = ~nonzero & (numerator > 0.0)
    zero_and_negative = ~nonzero & (numerator < 0.0)
    zero_and_tied = ~nonzero & (numerator == 0.0)
    result[zero_and_positive] = math.inf
    result[zero_and_negative] = -math.inf
    result[zero_and_tied] = 0.0
    return result


def _as_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
    raise ValueError(f"{field} must be an explicit boolean value")


def _nct_lower_tail(critical: float, degrees_freedom: int, noncentrality: float) -> float:
    """Evaluate the lower noncentral-t tail without unstable far-left CDF calls."""
    if noncentrality > 0.0:
        probability = float(nct.sf(critical, degrees_freedom, -noncentrality))
    else:
        probability = float(nct.cdf(-critical, degrees_freedom, noncentrality))
    if not math.isfinite(probability):
        raise ArithmeticError(
            "noncentral-t power is numerically non-finite; use a validated tail calculation"
        )
    return min(1.0, max(0.0, probability))


def position_test_contract(
    *, scheme: str, mde_m: float, interval_confidence: float = 0.95,
    interval_method: str = "bootstrap_t",
) -> dict[str, Any]:
    """Describe one of the two non-equivalent position success claims.

    ``interval_confidence=.95`` means a two-sided interval. With the default
    studentized bootstrap-t rule, interval inversion gives the upper endpoint
    as ``estimate - q_0.025(T*) * observed_se``. The lower-tail 2.5th
    percentile is used because inversion reverses the quantile order. The
    boundary type-I error is .025, not .05.
    """
    margin = _finite(mde_m)
    if margin is None or margin < 0:
        raise ValueError("mde_m must be finite and non-negative")
    if scheme not in {"A", "B"}:
        raise ValueError("scheme must be 'A' or 'B'")
    if interval_method not in {"percentile", "bca", "bootstrap_t"}:
        raise ValueError("interval_method must be percentile, bca, or bootstrap_t")
    confidence = _finite(interval_confidence)
    if confidence is None or not 0.5 < confidence < 1.0:
        raise ValueError("interval_confidence must be between 0.5 and 1")
    alpha = 1.0 - confidence
    upper_confidence = 1.0 - alpha / 2.0
    boundary = 0.0 if scheme == "A" else -margin
    return {
        "scheme": scheme,
        "contrast": "RMSE_M1_minus_RMSE_Fixed_D3_m; negative favors M1",
        "mde_m": margin,
        "null_boundary_delta_m": boundary,
        "alternative": f"mean_delta_m < {boundary:g}",
        "interval": f"two_sided_seed_cluster_{interval_method}",
        "interval_confidence": confidence,
        "upper_endpoint_confidence": upper_confidence,
        "upper_endpoint_studentized_statistic_quantile": alpha / 2.0,
        "upper_endpoint_formula": f"mean - q_{alpha / 2.0:g}(T*) * observed_se",
        "alpha_at_boundary": 1.0 - upper_confidence,
        "success_rule": (
            "two_sided_95pct_studentized_bootstrap_t_upper_below_zero" if scheme == "A"
            else "two_sided_95pct_studentized_bootstrap_t_upper_below_minus_mde"
        ) if confidence == 0.95 and interval_method == "bootstrap_t" else (
            "two_sided_95pct_ci_upper_below_zero" if scheme == "A"
            else "two_sided_95pct_ci_upper_below_minus_mde"
        ) if confidence == 0.95 else "upper_endpoint_below_null_boundary",
        "claim": (
            "statistical evidence of a negative mean difference; MDE is a separate"
            " point-estimate engineering target and does not prove the true effect exceeds MDE"
            if scheme == "A" else
            "statistical evidence that mean improvement exceeds the approved MDE"
        ),
        "independent_unit": "seed",
    }


def position_power(
    *, n: int, seed_sd: float, true_improvement_m: float, scheme: str,
    mde_m: float, interval_confidence: float = 0.95,
    upper_confidence: float | None = None,
) -> dict[str, Any]:
    """Exact noncentral-t planning power for the matching t-interval rule.

    Improvement is positive when the observed contrast is negative.  This is
    a planning model under normally distributed independent seed-level
    contrasts, not a claim that the V3.3 variance transports to V4.
    """
    _positive_int("n", n)
    sd = _finite(seed_sd)
    improvement = _finite(true_improvement_m)
    if sd is None or sd <= 0:
        raise ValueError("seed_sd must be finite and positive")
    if improvement is None:
        raise ValueError("true_improvement_m must be finite")
    contract = position_test_contract(
        scheme=scheme, mde_m=mde_m, interval_confidence=interval_confidence,
    )
    boundary = float(contract["null_boundary_delta_m"])
    true_delta = -improvement
    bound_confidence = (
        float(upper_confidence) if upper_confidence is not None
        else 1.0 - (1.0 - float(interval_confidence)) / 2.0
    )
    if not 0.5 < bound_confidence < 1.0:
        raise ValueError("upper_confidence must be between 0.5 and 1")
    critical = float(t.ppf(bound_confidence, n - 1))
    noncentrality = (true_delta - boundary) * math.sqrt(n) / sd
    power = _nct_lower_tail(critical, n - 1, noncentrality)
    point_estimate_pass = float(norm.cdf(
        (improvement - float(mde_m)) * math.sqrt(n) / sd
    ))
    return {
        "scheme": scheme,
        "n": n,
        "seed_sd_m": sd,
        "true_improvement_m": improvement,
        "true_delta_m": true_delta,
        "null_boundary_delta_m": boundary,
        "mde_m": float(mde_m),
        "interval_confidence": float(interval_confidence),
        "upper_endpoint_confidence": bound_confidence,
        "alpha_at_boundary": 1.0 - bound_confidence,
        "critical_t": critical,
        "noncentrality": noncentrality,
        "power": power,
        "statistical_pass_probability": power,
        "mde_point_estimate_pass_probability": point_estimate_pass,
        "method": "exact noncentral-t power for normal independent seed contrasts",
        "variance_source_limit": "historical V3.3 batch SD is planning sensitivity only",
    }


def bootstrap_mean_interval(
    values: Sequence[float], *, replicates: int = 10_000, seed: int = 22_029,
    confidence: float = 0.95, method: str = "bootstrap_t",
) -> dict[str, Any]:
    """Bootstrap CI resampling whole independent seed contrasts."""
    _positive_int("replicates", replicates)
    data = np.asarray([_finite(value) for value in values], dtype=np.float64)
    if data.ndim != 1 or data.size < 2 or not np.isfinite(data).all():
        raise ValueError("values must contain at least two finite seed-level observations")
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be between 0.5 and 1")
    if method not in {"percentile", "bca", "bootstrap_t"}:
        raise ValueError("method must be 'percentile', 'bca', or 'bootstrap_t'")
    mean = float(data.mean())
    observed_sd = float(data.std(ddof=1))
    result: dict[str, Any] = {
        "n": int(data.size),
        "mean": mean,
        "lower": None,
        "upper": None,
        "confidence": float(confidence),
        "method": (
            "seed-cluster studentized bootstrap-t" if method == "bootstrap_t" else
            f"seed-cluster {'BCa' if method == 'bca' else 'percentile'} bootstrap"
        ),
        "interval_algorithm": method,
        "replicates": replicates,
        "rng": f"NumPy PCG64 seed {seed}",
        "independent_unit": "seed",
        "degenerate_bootstrap_replicate_count": 0,
        "nonfinite_bootstrap_statistic_count": 0,
    }
    if observed_sd == 0.0:
        result["status"] = "NOT_EVALUABLE_ZERO_OBSERVED_VARIANCE"
        result["reason"] = "zero observed seed-level variance cannot establish zero uncertainty"
        return result
    rng = np.random.Generator(np.random.PCG64(seed))
    means = np.empty(replicates, dtype=np.float64)
    # Bound temporary memory for larger seed registries/replicate counts.
    chunk_size = max(1, min(replicates, 1_000_000 // data.size))
    offset = 0
    while offset < replicates:
        count = min(chunk_size, replicates - offset)
        indices = rng.integers(0, data.size, size=(count, data.size))
        samples = data[indices]
        sampled_means = samples.mean(axis=1)
        if method == "bootstrap_t":
            standard_errors = samples.std(axis=1, ddof=1) / math.sqrt(data.size)
            result["degenerate_bootstrap_replicate_count"] += int(
                np.count_nonzero(standard_errors == 0.0)
            )
            statistics_chunk = _studentized_bootstrap_statistics(data, samples)
            result["nonfinite_bootstrap_statistic_count"] += int(
                np.count_nonzero(~np.isfinite(statistics_chunk))
            )
            means[offset:offset + count] = statistics_chunk
        else:
            means[offset:offset + count] = sampled_means
        offset += count
    if result["degenerate_bootstrap_replicate_count"]:
        result["status"] = "NOT_EVALUABLE_DEGENERATE_BOOTSTRAP_VARIANCE"
        result["reason"] = (
            "at least one seed bootstrap replicate has zero standard error; "
            "the studentized reference distribution is not accepted"
        )
        return result
    if not np.isfinite(means).all():
        result["status"] = "NOT_EVALUABLE_NONFINITE_BOOTSTRAP_STATISTICS"
        result["reason"] = "bootstrap statistics contain non-finite values"
        return result
    lower, upper = _bootstrap_interval(
        data, means, confidence=confidence, method=method,
    )
    if not (math.isfinite(float(lower)) and math.isfinite(float(upper))):
        result["status"] = "NOT_EVALUABLE_NONFINITE_INTERVAL"
        result["reason"] = "bootstrap interval endpoints are non-finite"
        return result
    result.update({"status": "EVALUABLE", "lower": float(lower), "upper": float(upper)})
    return result


def bootstrap_monte_carlo_audit(
    *, n: int, seed_sd: float, true_delta: float, null_boundary: float,
    datasets: int = 2_000, bootstrap_replicates: int = 2_000,
    seed: int = 22_029, confidence: float = 0.95,
    interval_method: str = "bootstrap_t",
) -> dict[str, Any]:
    """Audit seed-cluster bootstrap coverage and rejection in synthetic seeds.

    Each synthetic dataset represents independent seeds.  Inner bootstrap
    resampling uses the whole seed-level contrast. The returned rates are
    Monte Carlo method checks, not GMTI performance evidence.
    """
    _positive_int("n", n)
    _positive_int("datasets", datasets)
    _positive_int("bootstrap_replicates", bootstrap_replicates)
    sd = _finite(seed_sd)
    delta = _finite(true_delta)
    boundary = _finite(null_boundary)
    if sd is None or sd <= 0 or delta is None or boundary is None:
        raise ValueError("seed_sd must be positive and means/boundary finite")
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be between 0.5 and 1")
    if interval_method not in {"percentile", "bca", "bootstrap_t"}:
        raise ValueError("interval_method must be percentile, bca, or bootstrap_t")

    rng = np.random.Generator(np.random.PCG64(seed))
    critical = float(t.ppf(1.0 - (1.0 - confidence) / 2.0, n - 1))
    covered = 0
    rejected = 0
    invalid_datasets = 0
    # Keep peak allocation bounded; every sample remains clustered by seed.
    batch_size = max(1, min(64, 2_000_000 // (n * bootstrap_replicates)))
    done = 0
    while done < datasets:
        batch = min(batch_size, datasets - done)
        observed = rng.normal(delta, sd, size=(batch, n))
        sample_means = observed.mean(axis=1)
        sample_sds = observed.std(axis=1, ddof=1)
        lower = np.empty(batch, dtype=np.float64)
        upper = np.empty(batch, dtype=np.float64)
        for i in range(batch):
            if sample_sds[i] == 0.0:
                invalid_datasets += 1
                lower[i] = upper[i] = math.nan
                continue
            indices = rng.integers(0, n, size=(bootstrap_replicates, n))
            samples = observed[i, indices]
            if interval_method == "bootstrap_t":
                standard_errors = samples.std(axis=1, ddof=1) / math.sqrt(n)
                if np.any(standard_errors == 0.0):
                    invalid_datasets += 1
                    lower[i] = upper[i] = math.nan
                    continue
            means = (
                _studentized_bootstrap_statistics(observed[i], samples)
                if interval_method == "bootstrap_t" else samples.mean(axis=1)
            )
            if not np.isfinite(means).all():
                invalid_datasets += 1
                lower[i] = upper[i] = math.nan
                continue
            lower[i], upper[i] = _bootstrap_interval(
                observed[i], means, confidence=confidence, method=interval_method,
            )
            if not (math.isfinite(float(lower[i])) and math.isfinite(float(upper[i]))):
                invalid_datasets += 1
                lower[i] = upper[i] = math.nan
        if invalid_datasets == 0:
            covered += int(np.count_nonzero((lower <= delta) & (delta <= upper)))
            # Use the selected two-sided interval's upper endpoint rule.
            rejected += int(np.count_nonzero(upper < boundary))
        done += batch

    complete = invalid_datasets == 0
    coverage_rate = covered / datasets if complete else None
    rejection_rate = rejected / datasets if complete else None
    return {
        "status": "EVALUABLE" if complete else "INCONCLUSIVE_INVALID_BOOTSTRAP_DATASET",
        "n": n,
        "seed_sd": sd,
        "true_delta": delta,
        "null_boundary": boundary,
        "datasets": datasets,
        "bootstrap_replicates_per_dataset": bootstrap_replicates,
        "confidence": confidence,
        "bootstrap_interval": f"two-sided {interval_method}; upper endpoint follows the selected seed bootstrap rule",
        "interval_algorithm": interval_method,
        "evaluable_dataset_count": datasets if complete else None,
        "invalid_dataset_count": invalid_datasets,
        "coverage_probability": coverage_rate,
        "coverage_monte_carlo_se": (
            math.sqrt(coverage_rate * (1.0 - coverage_rate) / datasets) if complete else None
        ),
        "rejection_probability": rejection_rate,
        "rejection_monte_carlo_se": (
            math.sqrt(rejection_rate * (1.0 - rejection_rate) / datasets) if complete else None
        ),
        "independent_unit": "synthetic seed",
        "simulation_kind": "CPU synthetic seed-level Monte Carlo; not a GMTI experiment",
        "critical_t_reference_only": critical,
        "rng": f"NumPy PCG64 seed {seed}",
    }


def joint_position_coverage_power(
    *, n: int, position_seed_sd_m: float, coverage_seed_sd_pp: float,
    position_true_improvement_m: float, coverage_true_delta_pp: float,
    position_mde_m: float, coverage_ni_margin_pp: float,
    correlation: float, variance_scale: float = 1.0,
    replicates: int = 100_000, seed: int = 22_029,
    interval_confidence: float = 0.95, position_scheme: str = "B",
) -> dict[str, Any]:
    """Monte Carlo joint pass probability for paired seed-level endpoints.

    Scheme A requires CI upper < 0 and the point estimate to reach MDE. Scheme
    B requires CI upper < -MDE. Coverage uses noninferiority. Dependence is
    explicit; this is a planning approximation, not actual bootstrap power.
    """
    _positive_int("n", n)
    _positive_int("replicates", replicates)
    position_sd = _finite(position_seed_sd_m)
    coverage_sd = _finite(coverage_seed_sd_pp)
    improvement = _finite(position_true_improvement_m)
    coverage_delta = _finite(coverage_true_delta_pp)
    mde = _finite(position_mde_m)
    margin = _finite(coverage_ni_margin_pp)
    rho = _finite(correlation)
    scale = _finite(variance_scale)
    confidence = _finite(interval_confidence)
    if position_sd is None or position_sd <= 0 or coverage_sd is None or coverage_sd <= 0:
        raise ValueError("both seed-level SDs must be finite and positive")
    if None in (improvement, coverage_delta, mde, margin, rho, scale, confidence):
        raise ValueError("joint power inputs must be finite")
    if mde < 0 or margin < 0 or scale <= 0 or not -1.0 <= rho <= 1.0:
        raise ValueError("margins, variance scale, or correlation are out of range")
    if not 0.5 < confidence < 1.0:
        raise ValueError("interval_confidence must be between 0.5 and 1")
    if position_scheme not in {"A", "B"}:
        raise ValueError("position_scheme must be 'A' or 'B'")

    rng = np.random.Generator(np.random.PCG64(seed))
    x = rng.standard_normal((replicates, n))
    z = rng.standard_normal((replicates, n))
    y = rho * x + math.sqrt(max(0.0, 1.0 - rho * rho)) * z
    x_mean = x.mean(axis=1)
    y_mean = y.mean(axis=1)
    x_sd = x.std(axis=1, ddof=1)
    y_sd = y.std(axis=1, ddof=1)
    critical = float(t.ppf(1.0 - (1.0 - confidence) / 2.0, n - 1))

    actual_position_sd = position_sd * scale
    actual_coverage_sd = coverage_sd * scale
    true_position_delta = -float(improvement)
    position_upper = (
        true_position_delta + actual_position_sd * x_mean
        + critical * actual_position_sd * x_sd / math.sqrt(n)
    )
    coverage_lower = (
        float(coverage_delta) + actual_coverage_sd * y_mean
        - critical * actual_coverage_sd * y_sd / math.sqrt(n)
    )
    position_mean_estimate = true_position_delta + actual_position_sd * x_mean
    position_statistical_pass = (
        position_upper < 0.0 if position_scheme == "A"
        else position_upper < -float(mde)
    )
    position_point_estimate_pass = -position_mean_estimate >= float(mde)
    position_pass = (
        position_statistical_pass & position_point_estimate_pass
        if position_scheme == "A" else position_statistical_pass
    )
    coverage_pass = coverage_lower > -float(margin)
    joint_pass = position_pass & coverage_pass

    def rate(mask: np.ndarray) -> tuple[float, float]:
        probability = float(mask.mean())
        return probability, math.sqrt(probability * (1.0 - probability) / replicates)

    position_rate, position_mcse = rate(position_pass)
    position_statistical_rate, position_statistical_mcse = rate(position_statistical_pass)
    position_point_rate, position_point_mcse = rate(position_point_estimate_pass)
    coverage_rate, coverage_mcse = rate(coverage_pass)
    joint_rate, joint_mcse = rate(joint_pass)
    return {
        "n": n,
        "position_seed_sd_m": position_sd,
        "coverage_seed_sd_pp": coverage_sd,
        "variance_scale": scale,
        "effective_position_seed_sd_m": actual_position_sd,
        "effective_coverage_seed_sd_pp": actual_coverage_sd,
        "position_true_improvement_m": float(improvement),
        "position_true_delta_m": true_position_delta,
        "position_mde_m": float(mde),
        "coverage_true_delta_pp": float(coverage_delta),
        "coverage_ni_margin_pp": float(margin),
        "assumed_seed_level_correlation": float(rho),
        "position_pass_probability": position_rate,
        "position_pass_monte_carlo_se": position_mcse,
        "position_statistical_pass_probability": position_statistical_rate,
        "position_statistical_pass_monte_carlo_se": position_statistical_mcse,
        "position_point_estimate_mde_pass_probability": position_point_rate,
        "position_point_estimate_mde_pass_monte_carlo_se": position_point_mcse,
        "position_scheme": position_scheme,
        "coverage_ni_pass_probability": coverage_rate,
        "coverage_ni_pass_monte_carlo_se": coverage_mcse,
        "joint_pass_probability": joint_rate,
        "joint_pass_monte_carlo_se": joint_mcse,
        "replicates": replicates,
        "interval_confidence": confidence,
        "interval_rule": "two-sided 95% t-reference interval; seed is resampling unit",
        "independent_unit": "seed",
        "rng": f"NumPy PCG64 seed {seed}",
        "simulation_kind": "CPU synthetic seed-level Monte Carlo; not a GMTI experiment",
    }


def joint_position_coverage_power_grid(
    *, position_seed_sd_m: float, coverage_seed_sd_pp: float,
    sample_sizes: Sequence[int], position_mdes_m: Sequence[float],
    coverage_ni_margins_pp: Sequence[float],
    true_position_improvements_m: Sequence[float],
    true_coverage_deltas_pp: Sequence[float], variance_scales: Sequence[float],
    correlations: Sequence[float], replicates: int = 10_000,
    seed: int = 22_029, interval_confidence: float = 0.95,
) -> list[dict[str, Any]]:
    """Calculate a reproducible sensitivity grid, reusing seed-level draws.

    The broad grid uses t-reference intervals for computational tractability.
    ``joint_bootstrap_monte_carlo_audit`` separately checks the studentized
    seed-cluster bootstrap rule at selected boundary and alternative cases.
    """
    _positive_int("replicates", replicates)
    position_sd = _finite(position_seed_sd_m)
    coverage_sd = _finite(coverage_seed_sd_pp)
    if position_sd is None or position_sd <= 0 or coverage_sd is None or coverage_sd <= 0:
        raise ValueError("both seed-level SDs must be finite and positive")
    rows: list[dict[str, Any]] = []
    scenario_id = 0
    for n in sample_sizes:
        _positive_int("sample size", int(n))
        for correlation in correlations:
            rho = _finite(correlation)
            if rho is None or not -1.0 <= rho <= 1.0:
                raise ValueError("correlations must be between -1 and 1")
            draw_seed = seed + int(n) * 1009 + int(round((rho + 1.0) * 1_000_000))
            rng = np.random.Generator(np.random.PCG64(draw_seed))
            x = rng.standard_normal((replicates, int(n)))
            z = rng.standard_normal((replicates, int(n)))
            y = rho * x + math.sqrt(max(0.0, 1.0 - rho * rho)) * z
            x_mean, x_sd = x.mean(axis=1), x.std(axis=1, ddof=1)
            y_mean, y_sd = y.mean(axis=1), y.std(axis=1, ddof=1)
            critical = float(t.ppf(1.0 - (1.0 - interval_confidence) / 2.0, int(n) - 1))
            for variance_scale in variance_scales:
                scale = _finite(variance_scale)
                if scale is None or scale <= 0:
                    raise ValueError("variance_scales must be positive")
                sigma_p = position_sd * scale
                sigma_c = coverage_sd * scale
                for mde in position_mdes_m:
                    p_mde = _finite(mde)
                    if p_mde is None or p_mde < 0:
                        raise ValueError("position_mdes_m must be finite and non-negative")
                    for margin in coverage_ni_margins_pp:
                        c_margin = _finite(margin)
                        if c_margin is None or c_margin < 0:
                            raise ValueError("coverage_ni_margins_pp must be finite and non-negative")
                        for improvement in true_position_improvements_m:
                            p_improvement = _finite(improvement)
                            if p_improvement is None:
                                raise ValueError("true position improvements must be finite")
                            position_upper = (
                                -p_improvement + sigma_p * x_mean
                                + critical * sigma_p * x_sd / math.sqrt(int(n))
                            )
                            position_mean = -p_improvement + sigma_p * x_mean
                            position_stat_a = position_upper < 0.0
                            position_stat_b = position_upper < -p_mde
                            position_point = -position_mean >= p_mde
                            for coverage_delta in true_coverage_deltas_pp:
                                c_delta = _finite(coverage_delta)
                                if c_delta is None:
                                    raise ValueError("true coverage deltas must be finite")
                                coverage_lower = (
                                    c_delta + sigma_c * y_mean
                                    - critical * sigma_c * y_sd / math.sqrt(int(n))
                                )
                                coverage_pass = coverage_lower > -c_margin
                                c_rate = float(coverage_pass.mean())
                                for scheme in ("A", "B"):
                                    position_pass = (
                                        position_stat_a & position_point
                                        if scheme == "A" else position_stat_b
                                    )
                                    joint = position_pass & coverage_pass
                                    p_rate = float(position_pass.mean())
                                    j_rate = float(joint.mean())
                                    p_stat_rate = float((position_stat_a if scheme == "A" else position_stat_b).mean())
                                    point_rate = float(position_point.mean())
                                    scenario_id += 1
                                    rows.append({
                                        "scenario_id": scenario_id,
                                        "position_scheme": scheme,
                                        "n": int(n),
                                        "position_mde_m": p_mde,
                                        "coverage_ni_margin_pp": c_margin,
                                        "true_position_improvement_m": p_improvement,
                                        "true_coverage_delta_pp": c_delta,
                                        "variance_scale": scale,
                                        "effective_position_seed_sd_m": sigma_p,
                                        "effective_coverage_seed_sd_pp": sigma_c,
                                        "assumed_seed_correlation": rho,
                                        "position_pass_probability": p_rate,
                                        "position_statistical_pass_probability": p_stat_rate,
                                        "position_point_estimate_mde_pass_probability": point_rate,
                                        "coverage_ni_pass_probability": c_rate,
                                        "joint_pass_probability": j_rate,
                                        "position_mc_se": math.sqrt(p_rate * (1.0 - p_rate) / replicates),
                                        "coverage_mc_se": math.sqrt(c_rate * (1.0 - c_rate) / replicates),
                                        "joint_mc_se": math.sqrt(j_rate * (1.0 - j_rate) / replicates),
                                        "replicates": replicates,
                                        "interval_rule": (
                                            "Scheme A: CI upper < 0 and point estimate reaches MDE; "
                                            "Scheme B: CI upper < -MDE; t-reference planning interval"
                                        ),
                                        "independent_unit": "seed",
                                        "rng": f"NumPy PCG64 seed {draw_seed}",
                                        "simulation_kind": "CPU synthetic seed-level Monte Carlo; not GMTI performance",
                                    })
    return rows


def joint_bootstrap_monte_carlo_audit(
    *, n: int, position_seed_sd_m: float, coverage_seed_sd_pp: float,
    position_true_delta_m: float, coverage_true_delta_pp: float,
    position_boundary_m: float, coverage_ni_margin_pp: float,
    correlation: float, datasets: int = 1_000,
    bootstrap_replicates: int = 1_000, seed: int = 22_029,
    confidence: float = 0.95, position_scheme: str = "B",
    position_mde_m: float = 0.0, interval_method: str = "bootstrap_t",
) -> dict[str, Any]:
    """Audit the planned paired-seed bootstrap joint rule.

    Bootstrap draws resample the same seed indices for position and coverage,
    preserving their pairing. The Monte Carlo samples are synthetic.
    """
    _positive_int("n", n)
    _positive_int("datasets", datasets)
    _positive_int("bootstrap_replicates", bootstrap_replicates)
    sig_p = _finite(position_seed_sd_m)
    sig_c = _finite(coverage_seed_sd_pp)
    mu_p = _finite(position_true_delta_m)
    mu_c = _finite(coverage_true_delta_pp)
    boundary = _finite(position_boundary_m)
    margin = _finite(coverage_ni_margin_pp)
    mde = _finite(position_mde_m)
    rho = _finite(correlation)
    if any(value is None for value in (sig_p, sig_c, mu_p, mu_c, boundary, margin, rho, mde)):
        raise ValueError("bootstrap audit inputs must be finite")
    if sig_p <= 0 or sig_c <= 0 or margin < 0 or mde < 0 or not -1.0 <= rho <= 1.0:
        raise ValueError("bootstrap audit SD, margin, or correlation is invalid")
    if position_scheme not in {"A", "B"}:
        raise ValueError("position_scheme must be 'A' or 'B'")
    if interval_method not in {"percentile", "bca", "bootstrap_t"}:
        raise ValueError("interval_method must be percentile, bca, or bootstrap_t")
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be between 0.5 and 1")

    rng = np.random.Generator(np.random.PCG64(seed))
    position_covered = coverage_covered = 0
    position_pass_count = position_stat_pass_count = position_point_pass_count = 0
    coverage_pass_count = joint_pass_count = 0
    invalid_datasets = 0
    for _ in range(datasets):
        x = rng.normal(mu_p, sig_p, size=n)
        z = rng.normal(0.0, 1.0, size=n)
        y = mu_c + sig_c * (rho * ((x - mu_p) / sig_p) + math.sqrt(max(0.0, 1.0 - rho * rho)) * z)
        if float(x.std(ddof=1)) == 0.0 or float(y.std(ddof=1)) == 0.0:
            invalid_datasets += 1
            continue
        indices = rng.integers(0, n, size=(bootstrap_replicates, n))
        samples_x = x[indices]
        samples_y = y[indices]
        if interval_method == "bootstrap_t" and (
            np.any(samples_x.std(axis=1, ddof=1) == 0.0)
            or np.any(samples_y.std(axis=1, ddof=1) == 0.0)
        ):
            invalid_datasets += 1
            continue
        boot_x = (
            _studentized_bootstrap_statistics(x, samples_x)
            if interval_method == "bootstrap_t" else samples_x.mean(axis=1)
        )
        boot_y = (
            _studentized_bootstrap_statistics(y, samples_y)
            if interval_method == "bootstrap_t" else samples_y.mean(axis=1)
        )
        p_lower, p_upper = _bootstrap_interval(
            x, boot_x, confidence=confidence, method=interval_method,
        )
        c_lower, c_upper = _bootstrap_interval(
            y, boot_y, confidence=confidence, method=interval_method,
        )
        if not all(math.isfinite(float(value)) for value in (p_lower, p_upper, c_lower, c_upper)):
            invalid_datasets += 1
            continue
        p_stat_pass = bool(p_upper < boundary)
        p_point_pass = bool(x.mean() <= -mde)
        p_pass = p_stat_pass and p_point_pass if position_scheme == "A" else p_stat_pass
        c_pass = bool(c_lower > -margin)
        position_covered += int(p_lower <= mu_p <= p_upper)
        coverage_covered += int(c_lower <= mu_c <= c_upper)
        position_pass_count += int(p_pass)
        position_stat_pass_count += int(p_stat_pass)
        position_point_pass_count += int(p_point_pass)
        coverage_pass_count += int(c_pass)
        joint_pass_count += int(p_pass and c_pass)

    complete = invalid_datasets == 0

    def rate(count: int) -> tuple[float | None, float | None]:
        if not complete:
            return None, None
        p = count / datasets
        return p, math.sqrt(p * (1.0 - p) / datasets)

    p_cov, p_cov_se = rate(position_covered)
    c_cov, c_cov_se = rate(coverage_covered)
    p_pass, p_pass_se = rate(position_pass_count)
    p_stat_pass, p_stat_pass_se = rate(position_stat_pass_count)
    p_point_pass, p_point_pass_se = rate(position_point_pass_count)
    c_pass, c_pass_se = rate(coverage_pass_count)
    joint, joint_se = rate(joint_pass_count)
    return {
        "status": "EVALUABLE" if complete else "INCONCLUSIVE_INVALID_BOOTSTRAP_DATASET",
        "n": n,
        "datasets": datasets,
        "evaluable_dataset_count": datasets if complete else None,
        "invalid_dataset_count": invalid_datasets,
        "bootstrap_replicates_per_dataset": bootstrap_replicates,
        "position_seed_sd_m": sig_p,
        "coverage_seed_sd_pp": sig_c,
        "position_true_delta_m": mu_p,
        "coverage_true_delta_pp": mu_c,
        "position_success_boundary_m": boundary,
        "position_scheme": position_scheme,
        "position_mde_m": mde,
        "coverage_ni_margin_pp": margin,
        "assumed_seed_correlation": rho,
        "interval": f"paired seed-cluster {interval_method} bootstrap",
        "interval_algorithm": interval_method,
        "confidence": confidence,
        "position_interval_coverage": p_cov,
        "position_interval_coverage_mc_se": p_cov_se,
        "coverage_interval_coverage": c_cov,
        "coverage_interval_coverage_mc_se": c_cov_se,
        "position_pass_probability": p_pass,
        "position_pass_mc_se": p_pass_se,
        "position_statistical_pass_probability": p_stat_pass,
        "position_statistical_pass_mc_se": p_stat_pass_se,
        "position_point_estimate_mde_pass_probability": p_point_pass,
        "position_point_estimate_mde_pass_mc_se": p_point_pass_se,
        "coverage_ni_pass_probability": c_pass,
        "coverage_ni_pass_mc_se": c_pass_se,
        "joint_pass_probability": joint,
        "joint_pass_mc_se": joint_se,
        "independent_unit": "synthetic seed",
        "rng": f"NumPy PCG64 seed {seed}",
        "simulation_kind": "CPU synthetic seed-level Monte Carlo; not a GMTI experiment",
    }


def aggregate_seed_level_primary(
    rows: Iterable[Mapping[str, Any]], *,
    expected_speeds: Sequence[float] = DEFAULT_SPEEDS_MPS,
    planned_seeds: Sequence[int] | None = None,
    planned_opportunity_keys: Sequence[tuple[float, str, int]] | None = None,
    minimum_common_support_per_speed: int | None = None,
) -> dict[str, Any]:
    """Build the equal-speed-weighted M1−Fixed D3 endpoint without silent drops.

    Input has exactly one row per seed/speed/target/period opportunity. Misses
    remain in the coverage denominator. Position RMSE is conditional on the
    common correctly matched opportunities; a speed stratum with zero common
    successes makes that seed's primary contrast non-evaluable. A positive
    per-speed support floor is an explicit design input; an unset floor keeps
    the descriptive estimate but excludes it from confirmatory inference.
    """
    speeds = tuple(float(value) for value in expected_speeds)
    if not speeds or len(set(speeds)) != len(speeds):
        raise ValueError("expected_speeds must be non-empty and unique")
    if minimum_common_support_per_speed is not None:
        _positive_int("minimum_common_support_per_speed", minimum_common_support_per_speed)
    expected_by_speed: dict[float, set[tuple[str, int]]] | None = None
    if planned_opportunity_keys is not None:
        expected_by_speed = {speed: set() for speed in speeds}
        for item in planned_opportunity_keys:
            if len(item) != 3:
                raise ValueError("planned opportunity key must be (speed_mps,target_id,period)")
            speed, target_id, period = float(item[0]), str(item[1]).strip(), int(item[2])
            if speed not in expected_by_speed or not target_id or period < 0:
                raise ValueError("planned opportunity registry contains an invalid identity")
            key = (target_id, period)
            if key in expected_by_speed[speed]:
                raise ValueError(f"planned opportunity registry contains duplicate identity: {speed}/{key}")
            expected_by_speed[speed].add(key)
        if any(not expected_by_speed[speed] for speed in speeds):
            raise ValueError("planned opportunity registry must cover every speed stratum")
    grouped: dict[int, dict[float, list[Mapping[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    seen: set[tuple[int, float, str, int]] = set()
    observed_seeds: set[int] = set()
    for row in rows:
        try:
            seed = int(row["seed"])
            speed = float(row["speed_mps"])
            target_id = str(row["target_id"]).strip()
            period = int(row["period"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("opportunity is missing seed/speed/target/period identity") from exc
        if not target_id:
            raise ValueError("truth identity target_id must be non-empty")
        if speed not in speeds:
            raise ValueError(f"unexpected speed stratum: {speed}")
        m1_match = _as_bool(row.get("m1_matched"), field="m1_matched")
        fixed_match = _as_bool(row.get("fixed_matched"), field="fixed_matched")
        if m1_match:
            value = _finite(row.get("m1_position_error_m"))
            if value is None or value < 0:
                raise ValueError("common matched opportunities require finite non-negative position errors")
        if fixed_match:
            value = _finite(row.get("fixed_position_error_m"))
            if value is None or value < 0:
                raise ValueError("common matched opportunities require finite non-negative position errors")
        key = (seed, speed, target_id, period)
        if key in seen:
            raise ValueError(f"duplicate opportunity identity: {key}")
        if expected_by_speed is not None and (target_id, period) not in expected_by_speed[speed]:
            raise ValueError(f"observed opportunity is outside the planned truth registry: {key}")
        seen.add(key)
        observed_seeds.add(seed)
        grouped[seed][speed].append(row)

    if planned_seeds is not None:
        planned = [int(value) for value in planned_seeds]
        if len(planned) != len(set(planned)):
            raise ValueError("planned_seeds contains duplicates")
        unexpected = observed_seeds - set(planned)
        if unexpected:
            raise ValueError(f"observed seeds are absent from planned registry: {sorted(unexpected)}")
        seed_ids = planned
    else:
        seed_ids = sorted(observed_seeds)

    seed_results: list[dict[str, Any]] = []
    for seed in seed_ids:
        strata = grouped.get(seed, {})
        m1_fallback_periods: set[tuple[float, int]] = set()
        fixed_fallback_periods: set[tuple[float, int]] = set()
        for speed, speed_rows in strata.items():
            for row in speed_rows:
                period = int(row["period"])
                m1_status = str(
                    row.get("m1_fallback_status") or row.get("m1_estimator_status") or ""
                ).strip().upper()
                fixed_status = str(
                    row.get("fixed_fallback_status") or row.get("fixed_estimator_status") or ""
                ).strip().upper()
                if "ZERO_CORRECTION_FALLBACK" in m1_status or "ZERO-CORRECTION-FALLBACK" in m1_status:
                    m1_fallback_periods.add((speed, period))
                if "ZERO_CORRECTION_FALLBACK" in fixed_status or "ZERO-CORRECTION-FALLBACK" in fixed_status:
                    fixed_fallback_periods.add((speed, period))
        base: dict[str, Any] = {
            "seed": seed,
            "status": "EVALUABLE",
            "primary_delta_m": None,
            "descriptive_primary_delta_m": None,
            "minimum_common_support_per_speed": minimum_common_support_per_speed,
            "speed_strata_count": 0,
            "total_opportunity_count": sum(len(values) for values in strata.values()),
            "common_success_count": 0,
            "m1_hit_count": 0,
            "fixed_hit_count": 0,
            "m1_coverage": None,
            "fixed_coverage": None,
            "m1_zero_correction_fallback_period_count": len(m1_fallback_periods),
            "fixed_zero_correction_fallback_period_count": len(fixed_fallback_periods),
            "fallback_policy": "fallback remains in all planned denominators; counts are per speed-period",
            "speed_results": [],
        }
        if not strata:
            base["status"] = "NOT_EVALUABLE_MISSING_CASE"
            seed_results.append(base)
            continue

        if expected_by_speed is None:
            base["status"] = "NOT_EVALUABLE_OPPORTUNITY_REGISTRY_MISSING"
            base["reason"] = "an exact planned target-period identity registry is required"
            seed_results.append(base)
            continue

        production_states = {
            str(row.get("production_status", "")).strip().upper()
            for speed_rows in strata.values() for row in speed_rows
        }
        if production_states - SUCCESS_PRODUCTION_STATES:
            base["status"] = "TECHNICAL_FAILURE"
            base["technical_failure_states"] = sorted(production_states - SUCCESS_PRODUCTION_STATES)
            seed_results.append(base)
            continue
        truth_states = {
            str(row.get("truth_status", "")).strip().upper()
            for speed_rows in strata.values() for row in speed_rows
        }
        if truth_states - SUCCESS_TRUTH_STATES:
            base["status"] = "DATA_OR_TRUTH_FAILURE"
            base["truth_failure_states"] = sorted(truth_states - SUCCESS_TRUTH_STATES)
            seed_results.append(base)
            continue
        if set(strata) != set(speeds):
            base["status"] = "NOT_EVALUABLE_MISSING_SPEED_STRATUM"
            base["missing_speeds_mps"] = [speed for speed in speeds if speed not in strata]
            seed_results.append(base)
            continue

        missing_opportunities = {
            speed: sorted(expected_by_speed[speed] - {
                (str(row["target_id"]).strip(), int(row["period"]))
                for row in strata.get(speed, [])
            })
            for speed in speeds
        }
        missing_opportunity_counts = {
            str(speed): len(values) for speed, values in missing_opportunities.items() if values
        }
        if missing_opportunity_counts:
            base["status"] = "NOT_EVALUABLE_MISSING_OPPORTUNITY_RECORDS"
            base["missing_opportunity_counts_by_speed"] = missing_opportunity_counts
            base["planned_opportunity_count"] = sum(len(expected_by_speed[speed]) for speed in speeds)
            base["observed_opportunity_count"] = base["total_opportunity_count"]
            seed_results.append(base)
            continue
        speed_deltas: list[float] = []
        total_count = m1_hits = fixed_hits = common_count = 0
        speed_results: list[dict[str, Any]] = []
        for speed in speeds:
            speed_rows = strata[speed]
            m1_values: list[float] = []
            fixed_values: list[float] = []
            for row in speed_rows:
                m1_match = _as_bool(row.get("m1_matched"), field="m1_matched")
                fixed_match = _as_bool(row.get("fixed_matched"), field="fixed_matched")
                total_count += 1
                m1_hits += int(m1_match)
                fixed_hits += int(fixed_match)
                if not (m1_match and fixed_match):
                    continue
                m1_error = _finite(row.get("m1_position_error_m"))
                fixed_error = _finite(row.get("fixed_position_error_m"))
                if m1_error is None or fixed_error is None or m1_error < 0 or fixed_error < 0:
                    raise ValueError(
                        "common matched opportunities require finite non-negative position errors"
                    )
                m1_values.append(m1_error)
                fixed_values.append(fixed_error)
            count = len(m1_values)
            common_count += count
            speed_delta = None
            if count:
                m1_rmse = math.sqrt(statistics.fmean(value * value for value in m1_values))
                fixed_rmse = math.sqrt(statistics.fmean(value * value for value in fixed_values))
                speed_delta = m1_rmse - fixed_rmse
                speed_deltas.append(speed_delta)
            speed_results.append({
                "speed_mps": speed,
                "opportunity_count": len(speed_rows),
                "m1_hit_count": sum(_as_bool(row.get("m1_matched"), field="m1_matched") for row in speed_rows),
                "fixed_hit_count": sum(_as_bool(row.get("fixed_matched"), field="fixed_matched") for row in speed_rows),
                "common_success_count": count,
                "m1_common_rmse_m": (
                    math.sqrt(statistics.fmean(value * value for value in m1_values)) if count else None
                ),
                "fixed_common_rmse_m": (
                    math.sqrt(statistics.fmean(value * value for value in fixed_values)) if count else None
                ),
                "position_delta_m": speed_delta,
                "support_flag": (
                    "NO_COMMON_SUCCESS" if count == 0 else
                    "MINIMUM_SUPPORT_UNAPPROVED" if minimum_common_support_per_speed is None else
                    "BELOW_MINIMUM_COMMON_SUPPORT"
                    if count < minimum_common_support_per_speed else "OK"
                ),
            })
        base.update({
            "total_opportunity_count": total_count,
            "m1_hit_count": m1_hits,
            "fixed_hit_count": fixed_hits,
            "common_success_count": common_count,
            "m1_coverage": m1_hits / total_count if total_count else None,
            "fixed_coverage": fixed_hits / total_count if total_count else None,
            "speed_results": speed_results,
            "speed_strata_count": len(speed_deltas),
        })
        if len(speed_deltas) != len(speeds):
            base["status"] = "NOT_EVALUABLE_NO_COMMON_SUCCESS"
        else:
            descriptive_delta = statistics.fmean(speed_deltas)
            base["descriptive_primary_delta_m"] = descriptive_delta
            if minimum_common_support_per_speed is None:
                base["status"] = "NOT_EVALUABLE_MINIMUM_COMMON_SUPPORT_UNAPPROVED"
            elif any(result["support_flag"] == "BELOW_MINIMUM_COMMON_SUPPORT" for result in speed_results):
                base["status"] = "NOT_EVALUABLE_BELOW_MINIMUM_COMMON_SUPPORT"
            else:
                base["primary_delta_m"] = descriptive_delta
                base["status"] = "EVALUABLE"
        seed_results.append(base)

    evaluable = [row for row in seed_results if row["primary_delta_m"] is not None]
    return {
        "independent_unit": "seed",
        "contrast": "M1_CAUSAL_PREFIX minus R22_R9_Fixed_D3; negative favors M1",
        "speed_weights": {str(speed): 1.0 / len(speeds) for speed in speeds},
        "position_estimand": "equal-speed mean of common-success RMSE differences within seed",
        "coverage_estimand": "all planned target-period opportunities; misses retained",
        "minimum_common_support_per_speed": minimum_common_support_per_speed,
        "position_common_success_policy": (
            "zero common successes makes the seed position contrast not evaluable; "
            "a null per-speed support floor is unapproved and excludes descriptive contrasts "
            "from inference; otherwise every speed stratum must meet the explicit floor"
        ),
        "planned_opportunity_registry_verified": expected_by_speed is not None,
        "planned_opportunity_counts_by_speed": (
            {str(speed): len(expected_by_speed[speed]) for speed in speeds}
            if expected_by_speed is not None else None
        ),
        "seed_count_planned": len(seed_ids),
        "seed_count_evaluable": len(evaluable),
        "seed_results": seed_results,
    }


def zero_event_upper_bound(n: int, *, confidence: float = 0.95) -> float:
    """Exact one-sided binomial upper bound when zero seed events are observed."""
    _positive_int("n", n)
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return 1.0 - (1.0 - confidence) ** (1.0 / n)


def binomial_clopper_pearson_interval(
    events: int, n: int, *, confidence: float = 0.95,
) -> tuple[float, float]:
    _positive_int("n", n)
    if isinstance(events, bool) or not isinstance(events, int) or not 0 <= events <= n:
        raise ValueError("events must be an integer between 0 and n")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    tail = (1.0 - confidence) / 2.0
    lower = 0.0 if events == 0 else float(beta.ppf(tail, events, n - events + 1))
    upper = 1.0 if events == n else float(beta.ppf(1.0 - tail, events + 1, n - events))
    return lower, upper


def binomial_clopper_pearson_upper_bound(
    events: int, n: int, *, confidence: float = 0.95,
) -> float:
    """Exact one-sided binomial upper confidence bound for an event probability."""
    _positive_int("n", n)
    if isinstance(events, bool) or not isinstance(events, int) or not 0 <= events <= n:
        raise ValueError("events must be an integer between 0 and n")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return 1.0 if events == n else float(beta.ppf(confidence, events + 1, n - events))


def h0_seed_statistics(
    *, period_events: Sequence[bool | None], valid_periods: Sequence[bool],
    false_confirmed_track_ids: Sequence[str], expected_period_count: int | None = None,
) -> dict[str, Any]:
    """Summarize one independent H0 seed, preserving period and track units."""
    if len(period_events) != len(valid_periods):
        raise ValueError("period_events and valid_periods lengths differ")
    expected = len(period_events) if expected_period_count is None else expected_period_count
    _positive_int("expected_period_count", expected)
    normalized_valid = [_as_bool(value, field="valid_period") for value in valid_periods]
    normalized_events: list[bool | None] = []
    for value in period_events:
        normalized_events.append(
            None if value is None else _as_bool(value, field="period_event")
        )
    complete = (
        len(period_events) == expected
        and all(normalized_valid)
        and all(value is not None for value in normalized_events)
    )
    unique_tracks = {str(value).strip() for value in false_confirmed_track_ids if str(value).strip()}
    if not complete:
        return {
            "status": "NOT_EVALUABLE_INCOMPLETE_PERIODS",
            "seed_any_event": None,
            "period_event_count": None,
            "period_event_fraction": None,
            "valid_period_count": sum(bool(value) for value in valid_periods),
            "expected_period_count": expected,
            "false_confirmed_unique_track_count": len(unique_tracks),
            "false_confirmed_unique_track_ids": sorted(unique_tracks),
            "independent_unit": "seed",
        }
    event_count = sum(bool(value) for value in normalized_events)
    return {
        "status": "EVALUABLE_H0_SEED",
        "seed_any_event": int(event_count > 0),
        "period_event_count": event_count,
        "period_event_fraction": event_count / expected,
        "valid_period_count": expected,
        "expected_period_count": expected,
        "false_confirmed_unique_track_count": len(unique_tracks),
        "false_confirmed_unique_track_ids": sorted(unique_tracks),
        "event_definition": "at least one frozen postfilter false Detection in a valid period",
        "period_unit_note": "periods are nested within seed; seed is the inferential unit",
        "track_count_note": "unique false confirmed track IDs within this seed",
        "independent_unit": "seed",
    }


def aggregate_h0_seed_results(
    seed_rows: Sequence[Mapping[str, Any]], *, planned_seeds: Sequence[int] | None = None,
    confidence: float = 0.95, bootstrap_replicates: int = 10_000,
    bootstrap_seed: int = 22_029,
) -> dict[str, Any]:
    """Aggregate H0 endpoints at the independent-seed level.

    Period events and confirmed tracks are nested within seed and never treated
    as independent Bernoulli trials.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    _positive_int("bootstrap_replicates", bootstrap_replicates)
    by_seed: dict[int, Mapping[str, Any]] = {}
    for row in seed_rows:
        seed = row.get("seed")
        if isinstance(seed, bool):
            raise ValueError("H0 seed identity must be an integer")
        try:
            seed = int(seed)
        except (TypeError, ValueError) as exc:
            raise ValueError("H0 seed identity must be an integer") from exc
        if seed in by_seed:
            raise ValueError(f"duplicate H0 seed result: {seed}")
        by_seed[seed] = row
    if planned_seeds is None:
        seed_ids = sorted(by_seed)
    else:
        seed_ids = [int(seed) for seed in planned_seeds]
        if len(seed_ids) != len(set(seed_ids)):
            raise ValueError("planned H0 seed registry contains duplicates")
        unexpected = set(by_seed) - set(seed_ids)
        if unexpected:
            raise ValueError(f"H0 result contains unplanned seeds: {sorted(unexpected)}")

    completed: list[Mapping[str, Any]] = []
    missing_seeds: list[int] = []
    for seed in seed_ids:
        row = by_seed.get(seed)
        if row is None or row.get("status") != "EVALUABLE_H0_SEED":
            missing_seeds.append(seed)
            continue
        if row.get("seed_any_event") not in (0, 1):
            raise ValueError(f"H0 seed event must be 0/1: {seed}")
        fraction = _finite(row.get("period_event_fraction"))
        track_count = row.get("false_confirmed_unique_track_count")
        if fraction is None or not 0.0 <= fraction <= 1.0:
            raise ValueError(f"H0 period event fraction is invalid: {seed}")
        if isinstance(track_count, bool) or not isinstance(track_count, int) or track_count < 0:
            raise ValueError(f"H0 unique false-track count is invalid: {seed}")
        completed.append(row)

    n = len(completed)
    events = sum(int(row["seed_any_event"]) for row in completed)
    event_rate = events / n if n else None
    event_ci = binomial_clopper_pearson_interval(events, n, confidence=confidence) if n else None
    period_fractions = [float(row["period_event_fraction"]) for row in completed]
    track_counts = [float(row["false_confirmed_unique_track_count"]) for row in completed]
    false_track_event_seeds = sum(count > 0.0 for count in track_counts)
    false_track_event_rate = false_track_event_seeds / n if n else None
    false_track_event_ci = (
        binomial_clopper_pearson_interval(false_track_event_seeds, n, confidence=confidence)
        if n else None
    )
    complete_seed_set = not missing_seeds and len(completed) == len(seed_ids)
    return {
        "status": "EVALUABLE_COMPLETE_H0_SEED_SET" if complete_seed_set else "INCOMPLETE_H0_SEED_SET",
        "independent_unit": "seed",
        "planned_seed_count": len(seed_ids),
        "evaluable_seed_count": n,
        "missing_or_invalid_seed_count": len(missing_seeds),
        "missing_or_invalid_seeds": missing_seeds,
        "seed_any_event": {
            "event_seed_count": events,
            "evaluable_seed_count": n,
            "rate": event_rate,
            "two_sided_exact_binomial_ci": list(event_ci) if event_ci else None,
            "one_sided_95pct_upper_bound": (
                binomial_clopper_pearson_upper_bound(events, n, confidence=0.95)
                if n and complete_seed_set else None
            ),
            "one_sided_95pct_zero_event_upper_bound": (
                zero_event_upper_bound(n, confidence=0.95)
                if n and events == 0 and complete_seed_set else None
            ),
            "zero_event_bound_interpretation": (
                "seed-any-event probability; no extrapolation to full-system Pfa"
            ),
        },
        "false_confirmed_track_event_rate": {
            "event_seed_count": false_track_event_seeds,
            "evaluable_seed_count": n,
            "rate": false_track_event_rate,
            "two_sided_exact_binomial_ci": (
                list(false_track_event_ci) if false_track_event_ci else None
            ),
            "one_sided_95pct_upper_bound": (
                binomial_clopper_pearson_upper_bound(
                    false_track_event_seeds, n, confidence=0.95,
                ) if n and complete_seed_set else None
            ),
            "event_definition": "at least one unique false confirmed TrackManager track ID in the seed",
            "counting_unit": "independent seed",
        },
        "period_event_fraction": {
            "seed_mean": statistics.fmean(period_fractions) if n else None,
            "seed_cluster_bootstrap_t_ci": (
                bootstrap_mean_interval(
                    period_fractions, replicates=bootstrap_replicates,
                    seed=bootstrap_seed, confidence=confidence,
                ) if n >= 2 else None
            ),
            "periods_treated_as_independent": False,
        },
        "false_confirmed_unique_tracks_per_seed": {
            "seed_mean": statistics.fmean(track_counts) if n else None,
            "seed_cluster_bootstrap_t_ci": (
                bootstrap_mean_interval(
                    track_counts, replicates=bootstrap_replicates,
                    seed=bootstrap_seed + 1, confidence=confidence,
                ) if n >= 2 else None
            ),
            "counting_unit": "unique confirmed track ID within seed",
            "track_observations_treated_as_independent": False,
        },
        "scope": "beam-50, ten-period H0 task; not 61-beam or full-system Pfa",
        "missing_seed_rule": "do not replace seeds; incomplete seed set cannot pass the confirmatory H0 gate",
    }


def h0_zero_event_sample_size(
    *, probability_upper_limit: float, confidence: float = 0.95,
) -> int:
    """Minimum independent seeds needed for a zero-event exact upper bound."""
    limit = _finite(probability_upper_limit)
    if limit is None or not 0.0 < limit < 1.0:
        raise ValueError("probability_upper_limit must be between 0 and 1")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return math.ceil(math.log(1.0 - confidence) / math.log(1.0 - limit))
