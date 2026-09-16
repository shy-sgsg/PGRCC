"""Pure channel-delay estimation, theory, and paired-statistics helpers.

The estimator operates only on observable F1/F2 calibration samples.  A
positive delay means that channel 2 is delayed relative to channel 1, so the
cross spectrum is formed as ``C12 = X1 * conj(X2)`` and has a positive phase
slope.  This module deliberately has no file I/O, truth input, or SciPy
dependency.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from typing import Any

import numpy as np

from scripts.estimate_channel_delay import fit_weighted_line


_MIN_CALIBRATION_SAMPLES = 8
_TWO_PI = 2.0 * math.pi
_NORMAL_95 = 1.959963984540054
_EPS = np.finfo(float).eps
_METHODS = (
    "D1_ordinary_LS",
    "D2_weighted_LS",
    "D3_Huber_weighted_LS",
)


def _as_pulse_matrix(value: np.ndarray, name: str) -> np.ndarray:
    """Return a complex128 pulse-by-sample matrix for a 1-D/2-D input."""

    try:
        array = np.asarray(value, dtype=np.complex128)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a numeric 1-D or 2-D array") from exc
    if array.ndim == 1:
        array = array[np.newaxis, :]
    elif array.ndim != 2:
        raise ValueError(f"{name} must be a 1-D or 2-D array")
    if array.shape[0] == 0 or array.shape[1] == 0:
        raise ValueError(f"{name} must not be empty")
    return array


def _prepare_inputs(
    f1_time: np.ndarray,
    f2_time: np.ndarray,
    fs_hz: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Validate inputs and return normalized arrays plus paired finite mask."""

    try:
        sample_rate = float(fs_hz)
    except (TypeError, ValueError) as exc:
        raise ValueError("fs_hz must be a positive finite scalar") from exc
    if not math.isfinite(sample_rate) or sample_rate <= 0.0:
        raise ValueError("fs_hz must be a positive finite scalar")

    first = _as_pulse_matrix(f1_time, "f1_time")
    second = _as_pulse_matrix(f2_time, "f2_time")
    if first.shape != second.shape:
        raise ValueError("f1_time and f2_time must have equal shape")
    finite = np.isfinite(first) & np.isfinite(second)
    return first, second, finite, sample_rate


def _safe_inputs(
    first: np.ndarray,
    second: np.ndarray,
    finite: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Zero only non-finite paired samples before FFT/correlation operations."""

    return np.where(finite, first, 0.0j), np.where(finite, second, 0.0j)


def _elapsed(start: float) -> float:
    return max(0.0, float(time.perf_counter() - start))


def _fallback_row(
    method: str,
    reason: str,
    support_count: int,
    start: float,
    *,
    input_finite_count: int | None = None,
) -> dict[str, object]:
    """Create the common structured fallback schema."""

    row: dict[str, object] = {
        "method": method,
        "status": "fallback",
        "delta_tau_ns": None,
        "finite": False,
        "residual_phase_rms_rad": None,
        "runtime_sec": _elapsed(start),
        "support_count": int(max(0, support_count)),
        "fallback_reason": reason,
    }
    if input_finite_count is not None:
        row["input_finite_count"] = int(input_finite_count)
    return row


def _success_row(
    method: str,
    delay_ns: float,
    residual_rms_rad: float | None,
    runtime_sec: float,
    support_count: int,
    *,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    delay = float(delay_ns)
    residual = None
    if residual_rms_rad is not None and math.isfinite(float(residual_rms_rad)):
        residual = max(0.0, float(residual_rms_rad))
    row: dict[str, object] = {
        "method": method,
        "status": "ok",
        "delta_tau_ns": delay,
        "finite": bool(math.isfinite(delay)),
        "residual_phase_rms_rad": residual,
        "runtime_sec": max(0.0, float(runtime_sec)),
        "support_count": int(max(0, support_count)),
        "fallback_reason": None,
    }
    if extra:
        row.update(extra)
    return row


def _cross_spectrum(
    first: np.ndarray,
    second: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return the observable C12 spectrum and its FFT frequency axis."""

    spectrum1 = np.fft.fft(first, axis=1)
    spectrum2 = np.fft.fft(second, axis=1)
    cross = np.sum(spectrum1 * np.conj(spectrum2), axis=0)
    frequency = np.fft.fftfreq(first.shape[1], d=1.0)
    return cross, frequency


def _residual_phase_rms(
    first: np.ndarray,
    second: np.ndarray,
    fs_hz: float,
    delay_ns: float,
) -> float | None:
    """Measure phase residual after removing a returned delay estimate."""

    cross, frequency_cycles_per_sample = _cross_spectrum(first, second)
    frequency_hz = frequency_cycles_per_sample * fs_hz
    mask = (
        (frequency_hz > 0.0)
        & np.isfinite(cross)
        & np.isfinite(frequency_hz)
        & (np.abs(cross) > _EPS)
    )
    if int(np.count_nonzero(mask)) < 2:
        return None
    order = np.argsort(frequency_hz[mask])
    selected_frequency = frequency_hz[mask][order]
    selected_phase = np.unwrap(np.angle(cross[mask][order]))
    predicted = _TWO_PI * selected_frequency * float(delay_ns) * 1.0e-9
    phase_offset = float(np.angle(np.mean(np.exp(1j * (selected_phase - predicted)))))
    residual = np.angle(np.exp(1j * (selected_phase - predicted - phase_offset)))
    value = float(np.sqrt(np.mean(residual * residual)))
    return value if math.isfinite(value) else None


def estimate_delay_d1_d2_d3(
    f1_time: np.ndarray,
    f2_time: np.ndarray,
    fs_hz: float,
) -> dict[str, dict[str, object]]:
    """Estimate delay with ordinary, weighted, and Huber-weighted LS fits.

    The existing production estimator supplies the D1/D2/D3 fitting
    semantics.  This wrapper adds the Stage-1 row schema and converts
    insufficient observable support into a structured fallback.
    """

    start = time.perf_counter()
    first, second, finite, sample_rate = _prepare_inputs(f1_time, f2_time, fs_hz)
    finite_count = int(np.count_nonzero(finite))
    if finite_count < _MIN_CALIBRATION_SAMPLES:
        return {
            method: _fallback_row(
                method,
                "insufficient_calibration_support",
                finite_count,
                start,
                input_finite_count=finite_count,
            )
            for method in _METHODS
        }

    safe_first, safe_second = _safe_inputs(first, second, finite)
    spectrum1 = np.fft.fft(safe_first, axis=1)
    spectrum2 = np.fft.fft(safe_second, axis=1)
    cross = np.sum(spectrum1 * np.conj(spectrum2), axis=0)
    frequency = np.fft.fftfreq(first.shape[1], d=1.0 / sample_rate)
    positive = frequency > 0.0
    magnitude = np.abs(cross)
    if not np.any(positive):
        return {
            method: _fallback_row(
                method,
                "insufficient_calibration_support",
                finite_count,
                time.perf_counter(),
                input_finite_count=finite_count,
            )
            for method in _METHODS
        }
    threshold = float(np.percentile(magnitude[positive], 80.0))
    mask = positive & np.isfinite(magnitude) & (magnitude >= max(threshold, _EPS))
    if int(np.count_nonzero(mask)) < 3:
        return {
            method: _fallback_row(
                method,
                "insufficient_calibration_support",
                int(np.count_nonzero(mask)),
                time.perf_counter(),
                input_finite_count=finite_count,
            )
            for method in _METHODS
        }
    order = np.argsort(frequency[mask])
    fit_frequency = frequency[mask][order]
    fit_phase = np.unwrap(np.angle(cross[mask][order]))
    fit_magnitude = magnitude[mask][order]
    rows: dict[str, dict[str, object]] = {}
    for method, robust in (
        ("D1_ordinary_LS", False),
        ("D2_weighted_LS", False),
        ("D3_Huber_weighted_LS", True),
    ):
        method_start = time.perf_counter()
        weights = np.ones_like(fit_magnitude) if method == "D1_ordinary_LS" else fit_magnitude
        try:
            fit = fit_weighted_line(fit_frequency, fit_phase, weights, robust=robust)
            fit_runtime = _elapsed(method_start)
        except (FloatingPointError, ValueError, np.linalg.LinAlgError) as exc:
            reason = (
                "insufficient_calibration_support"
                if "support" in str(exc).lower()
                else "estimator_error"
            )
            rows[method] = _fallback_row(
                method,
                reason,
                finite_count,
                method_start,
                input_finite_count=finite_count,
            )
            continue
        raw_slope = fit.get("slope_rad_per_hz")
        raw_delay = (
            float(raw_slope) / _TWO_PI * 1.0e9
            if raw_slope is not None
            else None
        )
        try:
            delay = float(raw_delay) if raw_delay is not None else math.nan
        except (TypeError, ValueError):
            delay = math.nan
        if not math.isfinite(delay):
            rows[method] = _fallback_row(
                method,
                "insufficient_calibration_support",
                int(fit.get("n", 0) or 0),
                method_start,
                input_finite_count=finite_count,
            )
            continue
        raw_residual = fit.get("rmse_rad")
        try:
            residual = float(raw_residual) if raw_residual is not None else None
        except (TypeError, ValueError):
            residual = None
        support_count = int(fit.get("n", 0) or 0)
        rows[method] = _success_row(
            method,
            delay,
            residual,
            fit_runtime,
            support_count,
            extra={
                "input_finite_count": finite_count,
                "pulse_count": int(first.shape[0]),
                "sample_count": int(first.shape[1]),
                "phase_slope_rad_per_hz": fit.get("slope_rad_per_hz"),
                "r2": fit.get("r2"),
                "confidence": fit.get("confidence"),
                "cross_spectrum_definition": "X1*conj(X2)",
            },
        )
    return rows

def _quadratic_peak_lag(values: np.ndarray, lags: np.ndarray) -> float | None:
    """Return a peak lag with bounded three-point parabolic interpolation."""

    magnitudes = np.abs(np.asarray(values, dtype=np.complex128))
    valid = np.isfinite(magnitudes) & np.isfinite(lags)
    if not np.any(valid):
        return None
    valid_indices = np.flatnonzero(valid)
    peak_local = int(np.argmax(magnitudes[valid]))
    peak_index = int(valid_indices[peak_local])
    if not math.isfinite(float(magnitudes[peak_index])) or magnitudes[peak_index] <= _EPS:
        return None
    offset = 0.0
    if 0 < peak_index < magnitudes.size - 1:
        left = float(magnitudes[peak_index - 1])
        center = float(magnitudes[peak_index])
        right = float(magnitudes[peak_index + 1])
        denominator = left - 2.0 * center + right
        if math.isfinite(denominator) and abs(denominator) > _EPS:
            candidate = 0.5 * (left - right) / denominator
            if math.isfinite(candidate):
                offset = float(np.clip(candidate, -0.5, 0.5))
    result = float(lags[peak_index]) + offset
    return result if math.isfinite(result) else None


def _linear_cross_correlation(
    first: np.ndarray,
    second: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Aggregate C12's time correlation and its standard signed lag axis.

    ``np.correlate(x1, x2)`` peaks at ``-delay_samples`` when channel 2 is
    delayed.  Callers negate that internal C12 lag for the public positive
    channel-2-delay convention.
    """

    sample_count = first.shape[1]
    correlation = np.zeros(2 * sample_count - 1, dtype=np.complex128)
    for row_first, row_second in zip(first, second):
        correlation += np.correlate(row_first, row_second, mode="full")
    lags = np.arange(-(sample_count - 1), sample_count, dtype=np.float64)
    return correlation, lags


def estimate_delay_cross_correlation(
    f1_time: np.ndarray,
    f2_time: np.ndarray,
    fs_hz: float,
) -> dict[str, object]:
    """Estimate signed delay from the ordinary time-domain cross-correlation."""

    start = time.perf_counter()
    first, second, finite, sample_rate = _prepare_inputs(f1_time, f2_time, fs_hz)
    finite_count = int(np.count_nonzero(finite))
    if finite_count < _MIN_CALIBRATION_SAMPLES:
        return _fallback_row(
            "cross_correlation",
            "insufficient_calibration_support",
            finite_count,
            start,
            input_finite_count=finite_count,
        )
    safe_first, safe_second = _safe_inputs(first, second, finite)
    correlation, lags = _linear_cross_correlation(safe_first, safe_second)
    lag_samples = _quadratic_peak_lag(correlation, lags)
    if lag_samples is None:
        return _fallback_row(
            "cross_correlation",
            "insufficient_calibration_support",
            finite_count,
            start,
            input_finite_count=finite_count,
        )
    delay_ns = -lag_samples / sample_rate * 1.0e9
    return _success_row(
        "cross_correlation",
        delay_ns,
        _residual_phase_rms(safe_first, safe_second, sample_rate, delay_ns),
        _elapsed(start),
        finite_count,
        extra={
            "input_finite_count": finite_count,
            "pulse_count": int(first.shape[0]),
            "sample_count": int(first.shape[1]),
            "lag_samples": float(lag_samples),
            "cross_spectrum_definition": "X1*conj(X2)",
            "internal_lag_convention": "C12_time_correlation_lag; reported_delay_ns=-lag_samples/fs",
        },
    )


def _next_power_of_two(value: int) -> int:
    result = 1
    while result < value:
        result <<= 1
    return result


def estimate_delay_gcc_phat(
    f1_time: np.ndarray,
    f2_time: np.ndarray,
    fs_hz: float,
) -> dict[str, object]:
    """Estimate signed delay from the GCC-PHAT correlation peak."""

    start = time.perf_counter()
    first, second, finite, sample_rate = _prepare_inputs(f1_time, f2_time, fs_hz)
    finite_count = int(np.count_nonzero(finite))
    if finite_count < _MIN_CALIBRATION_SAMPLES:
        return _fallback_row(
            "gcc_phat",
            "insufficient_calibration_support",
            finite_count,
            start,
            input_finite_count=finite_count,
        )

    safe_first, safe_second = _safe_inputs(first, second, finite)
    sample_count = int(first.shape[1])
    fft_size = _next_power_of_two(2 * sample_count - 1)
    phat_spectrum = np.zeros(fft_size, dtype=np.complex128)
    frequency_support_count = 0
    for row_first, row_second in zip(safe_first, safe_second):
        spectrum1 = np.fft.fft(row_first, n=fft_size)
        spectrum2 = np.fft.fft(row_second, n=fft_size)
        cross = spectrum1 * np.conj(spectrum2)
        magnitude = np.abs(cross)
        supported = np.isfinite(cross) & (magnitude > _EPS)
        frequency_support_count = max(
            frequency_support_count,
            int(np.count_nonzero(supported)),
        )
        phat_spectrum += np.where(supported, cross / np.maximum(magnitude, _EPS), 0.0j)

    if frequency_support_count == 0 or not np.any(np.abs(phat_spectrum) > _EPS):
        return _fallback_row(
            "gcc_phat",
            "insufficient_calibration_support",
            finite_count,
            start,
            input_finite_count=finite_count,
        )

    correlation = np.fft.ifft(phat_spectrum)
    raw_lags = np.arange(fft_size, dtype=np.float64)
    signed_lags = np.where(raw_lags <= fft_size // 2, raw_lags, raw_lags - fft_size)
    in_linear_support = (signed_lags >= -(sample_count - 1)) & (signed_lags <= sample_count - 1)
    support_indices = np.flatnonzero(in_linear_support)
    order = np.argsort(signed_lags[support_indices])
    ordered_indices = support_indices[order]
    lag_samples = _quadratic_peak_lag(correlation[ordered_indices], signed_lags[ordered_indices])
    if lag_samples is None:
        return _fallback_row(
            "gcc_phat",
            "insufficient_calibration_support",
            finite_count,
            start,
            input_finite_count=finite_count,
        )

    delay_ns = -lag_samples / sample_rate * 1.0e9
    return _success_row(
        "gcc_phat",
        delay_ns,
        _residual_phase_rms(safe_first, safe_second, sample_rate, delay_ns),
        _elapsed(start),
        finite_count,
        extra={
            "input_finite_count": finite_count,
            "pulse_count": int(first.shape[0]),
            "sample_count": sample_count,
            "lag_samples": float(lag_samples),
            "frequency_support_count": frequency_support_count,
            "cross_spectrum_definition": "X1*conj(X2)",
            "internal_lag_convention": "C12_ifft_lag; reported_delay_ns=-lag_samples/fs",
        },
    )


def delay_method_suite(
    f1_time: np.ndarray,
    f2_time: np.ndarray,
    fs_hz: float,
) -> list[dict[str, object]]:
    """Run the three spectral methods and both traditional baselines."""

    spectral = estimate_delay_d1_d2_d3(f1_time, f2_time, fs_hz)
    return [
        spectral["D1_ordinary_LS"],
        spectral["D2_weighted_LS"],
        spectral["D3_Huber_weighted_LS"],
        estimate_delay_cross_correlation(f1_time, f2_time, fs_hz),
        estimate_delay_gcc_phat(f1_time, f2_time, fs_hz),
    ]


def weighted_slope_variance(
    frequency_hz: np.ndarray,
    phase_rad: np.ndarray,
    weights: np.ndarray,
) -> dict[str, float]:
    """Fit a weighted phase slope and report its normal 95% interval."""

    try:
        frequency = np.asarray(frequency_hz, dtype=np.float64).reshape(-1)
        phase = np.asarray(phase_rad, dtype=np.float64).reshape(-1)
        weight = np.asarray(weights, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise ValueError("frequency_hz, phase_rad, and weights must be numeric arrays") from exc
    if not (frequency.size == phase.size == weight.size):
        raise ValueError("frequency_hz, phase_rad, and weights must have equal size")
    valid = np.isfinite(frequency) & np.isfinite(phase) & np.isfinite(weight) & (weight > 0.0)
    if int(np.count_nonzero(valid)) < 2:
        raise ValueError("weighted slope requires at least two finite supported samples")
    frequency = frequency[valid]
    phase = phase[valid]
    weight = weight[valid]
    weight_sum = float(np.sum(weight))
    if not math.isfinite(weight_sum) or weight_sum <= 0.0:
        raise ValueError("weights must have positive finite sum")
    frequency_mean = float(np.sum(weight * frequency) / weight_sum)
    phase_mean = float(np.sum(weight * phase) / weight_sum)
    centered_frequency = frequency - frequency_mean
    sxx_w = float(np.sum(weight * centered_frequency * centered_frequency))
    if not math.isfinite(sxx_w) or sxx_w <= _EPS:
        raise ValueError("frequency support must have non-zero weighted spread")
    slope = float(np.sum(weight * centered_frequency * (phase - phase_mean)) / sxx_w)
    intercept = phase_mean - slope * frequency_mean
    residual = phase - (intercept + slope * frequency)
    effective_count = float(weight_sum * weight_sum / np.sum(weight * weight))
    residual_dof = max(effective_count - 2.0, 1.0)
    residual_variance = float(np.sum(weight * residual * residual) / residual_dof)
    slope_variance = max(0.0, residual_variance / sxx_w)
    delay_variance_sec2 = slope_variance / (_TWO_PI * _TWO_PI)
    delay_std_ns = math.sqrt(max(0.0, delay_variance_sec2)) * 1.0e9
    delay_ns = slope / _TWO_PI * 1.0e9
    return {
        "weighted_mean": frequency_mean,
        "weighted_frequency_mean_hz": frequency_mean,
        "weighted_phase_mean_rad": phase_mean,
        "slope_rad_per_hz": slope,
        "slope_variance": float(slope_variance),
        "delay_variance_sec2": float(delay_variance_sec2),
        "delay_std_ns": float(delay_std_ns),
        "ci95_low_ns": float(delay_ns - _NORMAL_95 * delay_std_ns),
        "ci95_high_ns": float(delay_ns + _NORMAL_95 * delay_std_ns),
        "effective_sample_count": effective_count,
        "estimated_delay_ns": float(delay_ns),
        "intercept_rad": float(intercept),
        "sxx_w": float(sxx_w),
        "residual_variance_rad2": float(residual_variance),
    }


def residual_phase_from_delay_error(
    frequency_hz: np.ndarray,
    error_ns: float,
) -> np.ndarray:
    """Return epsilon_phi(f) = 2*pi*f*epsilon_tau for a delay error."""

    try:
        error = float(error_ns)
    except (TypeError, ValueError) as exc:
        raise ValueError("error_ns must be a finite scalar") from exc
    if not math.isfinite(error):
        raise ValueError("error_ns must be a finite scalar")
    frequency = np.asarray(frequency_hz, dtype=np.float64)
    if not np.all(np.isfinite(frequency)):
        raise ValueError("frequency_hz must contain only finite values")
    return _TWO_PI * frequency * error * 1.0e-9


def approximate_two_channel_cancellation_loss(
    frequency_hz: np.ndarray,
    error_ns: float,
) -> dict[str, float]:
    """Report the explicitly approximate sin^2(epsilon_phi/2) cancellation loss."""

    phase = residual_phase_from_delay_error(frequency_hz, error_ns)
    if phase.size == 0:
        raise ValueError("frequency_hz must not be empty")
    approximate_loss = np.sin(phase / 2.0) ** 2
    return {
        "mean_residual_phase_abs_rad": float(np.mean(np.abs(phase))),
        "max_residual_phase_abs_rad": float(np.max(np.abs(phase))),
        "rms_residual_phase_rad": float(np.sqrt(np.mean(phase * phase))),
        "mean_approximate_loss": float(np.mean(approximate_loss)),
        "max_approximate_loss": float(np.max(approximate_loss)),
        "approximate_loss_sin2_mean": float(np.mean(approximate_loss)),
        "approximate_loss_sin2_max": float(np.max(approximate_loss)),
    }


def _binomial_probability_tail(n: int, successes: int) -> float:
    """Return P[X <= successes] for X ~ Binomial(n, 0.5), stably."""

    if successes < 0:
        return 0.0
    if successes >= n:
        return 1.0
    probability = math.ldexp(1.0, -n)
    tail = probability
    for index in range(successes):
        probability *= (n - index) / float(index + 1)
        tail += probability
    return float(np.clip(tail, 0.0, 1.0))


def paired_mcnemar_exact(
    current_hits: Sequence[bool],
    comparison_hits: Sequence[bool],
) -> dict[str, object]:
    """Compute a two-sided exact McNemar p-value from paired hit decisions."""

    current = [bool(value) for value in current_hits]
    comparison = [bool(value) for value in comparison_hits]
    if len(current) != len(comparison):
        raise ValueError("current_hits and comparison_hits must have equal length")
    n_pairs = len(current)
    both_hit = sum(a and b for a, b in zip(current, comparison))
    both_miss = sum((not a) and (not b) for a, b in zip(current, comparison))
    current_miss_comparison_hit = sum((not a) and b for a, b in zip(current, comparison))
    current_hit_comparison_miss = sum(a and (not b) for a, b in zip(current, comparison))
    discordant = current_miss_comparison_hit + current_hit_comparison_miss
    if discordant == 0:
        p_value: float | None = 1.0 if n_pairs else None
        status = "ok" if n_pairs else "NOT_EVALUABLE"
    else:
        lower_tail = _binomial_probability_tail(
            discordant,
            min(current_miss_comparison_hit, current_hit_comparison_miss),
        )
        # Under H0 the two discordant directions are symmetric, so the exact
        # two-sided probability is twice the one lower tail; no subtraction
        # from one is needed for the opposite tail.
        p_value = min(1.0, 2.0 * lower_tail)
        p_value = min(1.0, max(0.0, float(p_value)))
        status = "ok"
    return {
        "status": status,
        "n_pairs": n_pairs,
        "concordant_both_hit": int(both_hit),
        "concordant_both_miss": int(both_miss),
        "discordant_current_miss_comparison_hit": int(current_miss_comparison_hit),
        "discordant_current_hit_comparison_miss": int(current_hit_comparison_miss),
        "discordant_pairs": int(discordant),
        "p_value_two_sided": p_value,
    }


def paired_bootstrap_ci(
    values_a: Sequence[float],
    values_b: Sequence[float],
    block_ids: Sequence[object],
    trials: int,
    seed: int,
) -> dict[str, object]:
    """Bootstrap paired differences by resampling scene blocks.

    Each block contributes one paired mean difference.  This keeps periods
    from a single scene together and gives every scene equal weight.
    """

    try:
        a = np.asarray(list(values_a), dtype=np.float64).reshape(-1)
        b = np.asarray(list(values_b), dtype=np.float64).reshape(-1)
        blocks = list(block_ids)
        trial_count = int(trials)
    except (TypeError, ValueError) as exc:
        raise ValueError("values, block_ids, and trials must be valid sequences") from exc
    if a.size != b.size or a.size != len(blocks):
        raise ValueError("values_a, values_b, and block_ids must have equal length")
    if a.size == 0:
        return {
            "status": "NOT_EVALUABLE",
            "n_pairs": 0,
            "n_blocks": 0,
            "trials": trial_count,
            "observed_difference": None,
            "ci95_low": None,
            "ci95_high": None,
        }
    if trial_count <= 0:
        raise ValueError("trials must be positive")
    if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b))):
        raise ValueError("values_a and values_b must contain only finite values")

    block_positions: dict[object, list[int]] = {}
    for index, block in enumerate(blocks):
        try:
            block_positions.setdefault(block, []).append(index)
        except TypeError as exc:
            raise ValueError("block_ids must contain hashable identifiers") from exc
    block_differences = np.asarray(
        [float(np.mean(b[indexes]) - np.mean(a[indexes])) for indexes in block_positions.values()],
        dtype=np.float64,
    )
    n_blocks = int(block_differences.size)
    observed = float(np.mean(block_differences))
    rng = np.random.default_rng(seed)
    bootstrap = np.empty(trial_count, dtype=np.float64)
    for trial in range(trial_count):
        selected = rng.integers(0, n_blocks, size=n_blocks)
        bootstrap[trial] = float(np.mean(block_differences[selected]))
    low, high = np.percentile(bootstrap, [2.5, 97.5])
    return {
        "status": "ok",
        "n_pairs": int(a.size),
        "n_blocks": n_blocks,
        "trials": trial_count,
        "seed": int(seed),
        "observed_difference": observed,
        "ci95_low": float(low),
        "ci95_high": float(high),
        "bootstrap_std": float(np.std(bootstrap)),
        "difference_definition": "mean_b_minus_mean_a_by_scene_block",
    }


def recovery_ratio(
    ideal_or_upper: float,
    current: float,
    blind: float,
    direction: str,
) -> dict[str, object]:
    """Compute recovered fraction, guarding a zero recoverable space."""

    try:
        ideal = float(ideal_or_upper)
        current_value = float(current)
        blind_value = float(blind)
    except (TypeError, ValueError) as exc:
        raise ValueError("recovery values must be finite scalars") from exc
    if not all(math.isfinite(value) for value in (ideal, current_value, blind_value)):
        raise ValueError("recovery values must be finite scalars")
    normalized_direction = str(direction).strip().lower().replace("-", "_")
    if normalized_direction == "higher_is_better":
        recoverable = ideal - current_value
        actual = blind_value - current_value
    elif normalized_direction == "lower_is_better":
        recoverable = current_value - ideal
        actual = current_value - blind_value
    else:
        raise ValueError("direction must be higher_is_better or lower_is_better")
    result: dict[str, object] = {
        "status": "ok",
        "direction": normalized_direction,
        "recoverable_space": float(recoverable),
        "actual_recovered": float(actual),
        "recovery_ratio": None,
    }
    if recoverable == 0.0:
        result["status"] = "NOT_EVALUABLE"
        return result
    result["recovery_ratio"] = float(actual / recoverable)
    return result


def _analytic_lfm_spectrum(
    fs_hz: float,
    bandwidth_hz: float,
    pulse_samples: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return a genuinely one-sided, positive-frequency complex-LFM support."""

    try:
        sample_rate = float(fs_hz)
        bandwidth = float(bandwidth_hz)
        sample_count = int(pulse_samples)
    except (TypeError, ValueError) as exc:
        raise ValueError("fs_hz, bandwidth_hz, and pulse_samples must be numeric") from exc
    if not math.isfinite(sample_rate) or sample_rate <= 0.0:
        raise ValueError("fs_hz must be a positive finite scalar")
    if not math.isfinite(bandwidth) or bandwidth <= 0.0:
        raise ValueError("bandwidth_hz must be a positive finite scalar")
    if bandwidth > sample_rate / 2.0:
        raise ValueError("bandwidth_hz must not exceed the Nyquist frequency fs_hz/2")
    if sample_count < 2:
        raise ValueError("pulse_samples must be at least two")

    frequency = np.fft.fftfreq(sample_count, d=1.0 / sample_rate)
    support = (frequency > 0.0) & (frequency <= bandwidth)
    if int(np.count_nonzero(support)) < _MIN_CALIBRATION_SAMPLES:
        raise ValueError("bandwidth and pulse_samples provide insufficient calibration support")
    normalized_frequency = frequency / max(bandwidth, _EPS)
    spectrum = np.zeros(sample_count, dtype=np.complex128)
    spectrum[support] = np.exp(1j * math.pi * normalized_frequency[support] ** 2)
    return frequency, spectrum


def _generate_wideband_lfm_pair(
    delay_ns: float,
    snr_db: float,
    rng: np.random.Generator,
    fs_hz: float,
    bandwidth_hz: float,
    pulse_samples: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate a deterministic wideband complex chirp with independent noise."""

    frequency, spectrum = _analytic_lfm_spectrum(fs_hz, bandwidth_hz, pulse_samples)
    reference = np.fft.ifft(spectrum)
    delay_phase = np.exp(-1j * _TWO_PI * frequency * float(delay_ns) * 1.0e-9)
    delayed = np.fft.ifft(spectrum * delay_phase)
    signal_power = float(np.mean(np.abs(reference) ** 2))
    noise_power = signal_power / (10.0 ** (float(snr_db) / 10.0))
    noise_scale = math.sqrt(max(noise_power, 0.0) / 2.0)
    noise1 = noise_scale * (
        rng.standard_normal(pulse_samples) + 1j * rng.standard_normal(pulse_samples)
    )
    noise2 = noise_scale * (
        rng.standard_normal(pulse_samples) + 1j * rng.standard_normal(pulse_samples)
    )
    return reference + noise1, delayed + noise2


def _mc_summary(
    delay_error_ns: float,
    snr_db: float,
    method: str,
    errors: list[float],
    runtimes: list[float],
    fallback_count: int,
    trials: int,
    fs_hz: float,
) -> dict[str, object]:
    error_array = np.asarray(errors, dtype=np.float64)
    if error_array.size:
        bias = float(np.mean(error_array))
        rmse = float(np.sqrt(np.mean(error_array * error_array)))
        std = float(np.std(error_array, ddof=1 if error_array.size > 1 else 0))
        standard_error = std / math.sqrt(error_array.size)
        ci_low = bias - _NORMAL_95 * standard_error
        ci_high = bias + _NORMAL_95 * standard_error
        outlier_threshold = max(1.0, 3.0 * (1.0e9 / fs_hz))
        outlier_rate: float | None = float(
            np.mean(np.abs(error_array) > outlier_threshold)
        )
    else:
        bias = rmse = std = ci_low = ci_high = outlier_rate = None
    return {
        "delay_error_ns": float(delay_error_ns),
        "snr_db": float(snr_db),
        "method": method,
        "bias_ns": bias,
        "rmse_ns": rmse,
        "std_ns": std,
        "ci95_low_ns": ci_low,
        "ci95_high_ns": ci_high,
        "outlier_rate": outlier_rate,
        "fallback_rate": float(fallback_count / trials),
        "fallback_count": int(fallback_count),
        "finite_estimate_count": int(error_array.size),
        "trials": int(trials),
        "mean_runtime_sec": float(np.mean(runtimes)) if runtimes else 0.0,
    }


def run_parameter_monte_carlo(
    delay_errors_ns: Sequence[float],
    snr_db_values: Sequence[float],
    trials: int,
    seed: int,
    fs_hz: float,
    bandwidth_hz: float,
    pulse_samples: int,
) -> list[dict[str, object]]:
    """Run estimator-only Monte Carlo over delay and SNR working points."""

    try:
        delays = [float(value) for value in delay_errors_ns]
        snrs = [float(value) for value in snr_db_values]
        trial_count = int(trials)
        sample_rate = float(fs_hz)
        bandwidth = float(bandwidth_hz)
        sample_count = int(pulse_samples)
    except (TypeError, ValueError) as exc:
        raise ValueError("Monte Carlo parameters must be numeric") from exc
    if trial_count <= 0:
        raise ValueError("trials must be positive")
    if not delays or not snrs:
        return []
    if not math.isfinite(sample_rate) or sample_rate <= 0.0:
        raise ValueError("fs_hz must be a positive finite scalar")
    if not math.isfinite(bandwidth) or bandwidth <= 0.0 or bandwidth > sample_rate / 2.0:
        raise ValueError("bandwidth_hz must be positive and no greater than fs_hz/2 (Nyquist)")
    if sample_count < 2:
        raise ValueError("pulse_samples must be at least two")
    if not all(math.isfinite(value) for value in delays + snrs):
        raise ValueError("delay_errors_ns and snr_db_values must be finite")

    rng = np.random.default_rng(seed)
    accumulators: dict[tuple[float, float, str], dict[str, Any]] = {}
    for delay in delays:
        for snr in snrs:
            for method in (*_METHODS, "cross_correlation", "gcc_phat"):
                accumulators[(delay, snr, method)] = {
                    "errors": [],
                    "runtimes": [],
                    "fallback_count": 0,
                }
            for _ in range(trial_count):
                first, second = _generate_wideband_lfm_pair(
                    delay,
                    snr,
                    rng,
                    sample_rate,
                    bandwidth,
                    sample_count,
                )
                rows = delay_method_suite(first, second, sample_rate)
                for row in rows:
                    method = str(row["method"])
                    accumulator = accumulators[(delay, snr, method)]
                    runtime = float(row.get("runtime_sec", 0.0) or 0.0)
                    accumulator["runtimes"].append(max(0.0, runtime))
                    estimate = row.get("delta_tau_ns")
                    if row.get("finite") and estimate is not None and math.isfinite(float(estimate)):
                        accumulator["errors"].append(float(estimate) - delay)
                    else:
                        accumulator["fallback_count"] += 1

    rows: list[dict[str, object]] = []
    for delay in delays:
        for snr in snrs:
            for method in (*_METHODS, "cross_correlation", "gcc_phat"):
                accumulator = accumulators[(delay, snr, method)]
                rows.append(
                    _mc_summary(
                        delay,
                        snr,
                        method,
                        accumulator["errors"],
                        accumulator["runtimes"],
                        int(accumulator["fallback_count"]),
                        trial_count,
                        sample_rate,
                    )
                )
    return rows
