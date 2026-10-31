"""Deterministic C2 delay fit matched to Stage2 and R8 geometry.

The estimator uses raw four-channel protocol data's physical C1/C2 pair.  It
removes the reported electronic-scan group delay, then profiles one unknown
constant complex gain while fitting Stage2's zero-extended linear fractional
interpolator.  An arbitrary unknown frequency response is rejected as
non-identifiable.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from scripts.estimate_channel_delay import fit_weighted_line


_C_MPS = 299_792_458.0
_TWO_PI = 2.0 * math.pi
_EPS = np.finfo(np.float64).eps
_M1 = "M1_Physics_Matched_Delay"


def stage2_linear_delay_response(
    delay_samples: float,
    frequency_hz: np.ndarray,
    fs_hz: float,
) -> np.ndarray:
    """Return Stage2's interior frequency response for a signed delay < 1 sample."""

    delay = float(delay_samples)
    sample_rate = float(fs_hz)
    frequency = np.asarray(frequency_hz, dtype=np.float64)
    if not math.isfinite(delay) or not -1.0 < delay < 1.0:
        raise ValueError("delay_samples must be finite and lie in (-1, 1)")
    if not math.isfinite(sample_rate) or sample_rate <= 0.0:
        raise ValueError("fs_hz must be finite and positive")
    if not np.all(np.isfinite(frequency)):
        raise ValueError("frequency_hz must be finite")
    if delay >= 0.0:
        return (1.0 - delay) + delay * np.exp(-1j * _TWO_PI * frequency / sample_rate)
    magnitude = -delay
    return (1.0 - magnitude) + magnitude * np.exp(1j * _TWO_PI * frequency / sample_rate)


def stage2_linear_fractional_delay(samples: np.ndarray, delay_samples: float) -> np.ndarray:
    """Apply the Stage2 linear interpolator with its zero-extended pulse edges."""

    values = np.asarray(samples, dtype=np.complex128)
    was_vector = values.ndim == 1
    if was_vector:
        values = values[np.newaxis, :]
    if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] < 2:
        raise ValueError("samples must be a nonempty 1-D or 2-D pulse matrix")
    delay = float(delay_samples)
    if not math.isfinite(delay) or not -1.0 < delay < 1.0:
        raise ValueError("delay_samples must be finite and lie in (-1, 1)")

    shifted = np.zeros_like(values)
    if delay > 0.0:
        shifted[:, 1:] = (1.0 - delay) * values[:, 1:] + delay * values[:, :-1]
    elif delay < 0.0:
        advance = -delay
        shifted[:, :-1] = (1.0 - advance) * values[:, :-1] + advance * values[:, 1:]
    else:
        shifted = values.copy()
    return shifted[0] if was_vector else shifted


def compute_electronic_pair_group_delay_sec(
    *,
    theta_deg: float,
    range_m: float,
    height_m: float,
    reference_offset_m: tuple[float, float, float],
    source_offset_m: tuple[float, float, float],
    squint_side: int = 1,
) -> float:
    """Compute the reported C1-to-C2 path delay using the Native electronic LOS model."""

    theta = math.radians(float(theta_deg))
    range_value = float(range_m)
    height = float(height_m)
    if not all(math.isfinite(value) for value in (theta, range_value, height)):
        raise ValueError("theta, range, and height must be finite")
    if range_value <= 0.0 or height < 0.0 or height >= range_value:
        raise ValueError("geometry requires range_m > height_m >= 0")
    if squint_side not in (-1, 1):
        raise ValueError("squint_side must be -1 or 1")
    reference = np.asarray(reference_offset_m, dtype=np.float64)
    source = np.asarray(source_offset_m, dtype=np.float64)
    if reference.shape != (3,) or source.shape != (3,):
        raise ValueError("channel offsets must each contain three ENU components")
    if not np.all(np.isfinite(reference)) or not np.all(np.isfinite(source)):
        raise ValueError("channel offsets must be finite")

    horizontal = math.sqrt(range_value * range_value - height * height)
    direction = np.asarray((
        -squint_side * horizontal * math.sin(theta) / range_value,
        -squint_side * horizontal * math.cos(theta) / range_value,
        -height / range_value,
    ))

    def path_offset(offset: np.ndarray) -> float:
        dot = float(np.dot(direction, offset))
        offset_sq = float(np.dot(offset, offset))
        normalized = max(
            0.0,
            1.0 - 2.0 * dot / range_value + offset_sq / (range_value * range_value),
        )
        return (-2.0 * dot + offset_sq / range_value) / (math.sqrt(normalized) + 1.0)

    return (path_offset(source) - path_offset(reference)) / _C_MPS


def _bandlimited_shift_by_pulse(
    samples: np.ndarray,
    delay_sec: np.ndarray,
    fs_hz: float,
) -> np.ndarray:
    """Apply nominal physical group delay with zero-padded Fourier interpolation."""

    pulse_count, sample_count = samples.shape
    padding = sample_count
    padded = np.pad(samples, ((0, 0), (padding, padding)))
    frequency = np.fft.fftfreq(padded.shape[1], d=1.0 / float(fs_hz))
    phase = np.exp(
        -1j * _TWO_PI * delay_sec[:, np.newaxis] * frequency[np.newaxis, :]
    )
    shifted = np.fft.ifft(np.fft.fft(padded, axis=1) * phase, axis=1)
    return shifted[:, padding:padding + sample_count]


def _predicted_cross_spectrum(
    delay_samples: float,
    frequency_hz: np.ndarray,
    fs_hz: float,
    cross_geometry: np.ndarray,
    edge_first: np.ndarray,
    edge_last: np.ndarray,
) -> np.ndarray:
    response = stage2_linear_delay_response(delay_samples, frequency_hz, fs_hz)
    predicted = np.conj(response) * cross_geometry
    if delay_samples > 0.0:
        predicted -= (1.0 - delay_samples) * edge_first + delay_samples * edge_last
    elif delay_samples < 0.0:
        advance = -delay_samples
        predicted -= np.exp(-1j * _TWO_PI * frequency_hz / fs_hz) * (
            (1.0 - advance) * edge_last + advance * edge_first
        )
    return predicted


def _profile_complex_gain(
    observed: np.ndarray,
    predicted: np.ndarray,
    weights: np.ndarray,
) -> tuple[float, complex, float, float]:
    denominator = float(np.sum(weights * np.abs(predicted) ** 2))
    observed_energy = float(np.sum(weights * np.abs(observed) ** 2))
    if denominator <= _EPS or observed_energy <= _EPS:
        return math.inf, 0.0j, math.inf, math.inf
    gain = np.sum(weights * np.conj(predicted) * observed) / denominator
    error = observed - gain * predicted
    relative_residual = math.sqrt(float(np.sum(weights * np.abs(error) ** 2)) / observed_energy)
    phase_error = np.angle(observed * np.conj(gain * predicted))
    phase_rmse = math.sqrt(float(np.sum(weights * phase_error**2)) / float(np.sum(weights)))
    return relative_residual**2, complex(gain), relative_residual, phase_rmse


def _golden_section_minimum(function: Any, left: float, right: float) -> float:
    ratio = (math.sqrt(5.0) - 1.0) / 2.0
    x1 = right - ratio * (right - left)
    x2 = left + ratio * (right - left)
    f1 = function(x1)
    f2 = function(x2)
    for _ in range(64):
        if f1 <= f2:
            right, x2, f2 = x2, x1, f1
            x1 = right - ratio * (right - left)
            f1 = function(x1)
        else:
            left, x1, f1 = x1, x2, f2
            x2 = left + ratio * (right - left)
            f2 = function(x2)
    return 0.5 * (left + right)


def _slope_baselines(
    observed_cross: np.ndarray,
    geometry_cross: np.ndarray,
    frequency_hz: np.ndarray,
    mask: np.ndarray,
) -> dict[str, dict[str, float | int | None]]:
    ratio = observed_cross[mask] / geometry_cross[mask]
    order = np.argsort(frequency_hz[mask])
    frequency = frequency_hz[mask][order]
    phase = np.unwrap(np.angle(ratio[order]))
    magnitude = np.abs(observed_cross[mask][order])
    baselines: dict[str, dict[str, float | int | None]] = {}
    for name, weights, robust in (
        ("D1_ordinary_LS", np.ones_like(magnitude), False),
        ("D2_weighted_LS", magnitude, False),
        ("D3_Huber_weighted_LS", magnitude, True),
    ):
        fit = fit_weighted_line(frequency, phase, weights, robust=robust)
        slope = fit.get("slope_rad_per_hz")
        delay_ns = float(slope) / _TWO_PI * 1.0e9 if slope is not None else None
        baselines[name] = {
            "delta_tau_ns": delay_ns if delay_ns is not None and math.isfinite(delay_ns) else None,
            "support_count": int(fit.get("n", 0) or 0),
            "phase_rmse_rad": fit.get("rmse_rad"),
            "r2": fit.get("r2"),
        }
    return baselines


def estimate_physics_matched_delay(
    channel1_time: np.ndarray,
    channel2_time: np.ndarray,
    *,
    fs_hz: float,
    carrier_hz: float,
    geometry_delay_sec: float | np.ndarray,
    carrier_phase_sign: int,
    relative_response_model: str,
    support_percentile: float = 80.0,
    min_support_bins: int = 8,
    min_frequency_span_hz: float = 3.0e6,
    delay_bounds_samples: tuple[float, float] = (-0.49, 0.49),
    maximum_normalized_model_residual: float = 0.35,
) -> dict[str, Any]:
    """Fit Stage2 C2 delay from blind physical C1/C2 fast-time observations.

    The generated production chain in the model is: reported C1/C2 geometry
    path delay, followed by Stage2's zero-extended fractional interpolator on
    C2.  One frequency-flat complex gain is profiled out analytically.  Input
    arrays must contain only the observable IQ and reported geometry; no delay
    truth, paired reference input, or detection result is accepted.
    """

    first = np.asarray(channel1_time, dtype=np.complex128)
    second = np.asarray(channel2_time, dtype=np.complex128)
    if first.ndim != 2 or second.shape != first.shape or first.shape[0] == 0 or first.shape[1] < 16:
        raise ValueError("C1/C2 inputs must be equal nonempty [pulse, fast_time] matrices")
    if not np.all(np.isfinite(first)) or not np.all(np.isfinite(second)):
        raise ValueError("C1/C2 inputs must be finite")
    sample_rate = float(fs_hz)
    carrier = float(carrier_hz)
    if not math.isfinite(sample_rate) or sample_rate <= 0.0:
        raise ValueError("fs_hz must be finite and positive")
    if not math.isfinite(carrier) or carrier <= 0.0:
        raise ValueError("carrier_hz must be finite and positive")
    if carrier_phase_sign not in (-1, 1):
        raise ValueError("carrier_phase_sign must be -1 or 1")
    if not 0.0 <= support_percentile < 100.0:
        raise ValueError("support_percentile must lie in [0, 100)")
    if min_support_bins < 3:
        raise ValueError("min_support_bins must be at least three")
    if not math.isfinite(min_frequency_span_hz) or min_frequency_span_hz < 0.0:
        raise ValueError("min_frequency_span_hz must be finite and nonnegative")
    lower, upper = (float(delay_bounds_samples[0]), float(delay_bounds_samples[1]))
    if not math.isfinite(lower) or not math.isfinite(upper) or not -1.0 < lower < upper < 1.0:
        raise ValueError("delay_bounds_samples must be ordered inside (-1, 1)")
    if not math.isfinite(maximum_normalized_model_residual) or maximum_normalized_model_residual <= 0.0:
        raise ValueError("maximum_normalized_model_residual must be finite and positive")

    geometry = np.asarray(geometry_delay_sec, dtype=np.float64)
    if geometry.ndim == 0:
        geometry = np.full(first.shape[0], float(geometry), dtype=np.float64)
    if geometry.shape != (first.shape[0],) or not np.all(np.isfinite(geometry)):
        raise ValueError("geometry_delay_sec must be finite scalar or one value per pulse")

    if relative_response_model != "constant_complex_gain":
        return {
            "method": _M1,
            "status": "not_identifiable",
            "delta_tau_ns": None,
            "delay_samples": None,
            "fallback_reason": "unknown_frequency_response_can_absorb_delay",
            "relative_response_model": relative_response_model,
            "truth_used_in_estimator": False,
            "baselines": {},
        }

    geometry_shift = _bandlimited_shift_by_pulse(first, geometry, sample_rate)
    geometry_shift *= np.exp(1j * carrier_phase_sign * _TWO_PI * carrier * geometry)[:, None]

    frequency = np.fft.fftfreq(first.shape[1], d=1.0 / sample_rate)
    first_spectrum = np.fft.fft(first, axis=1)
    observed_spectrum = np.fft.fft(second, axis=1)
    geometry_spectrum = np.fft.fft(geometry_shift, axis=1)
    observed_cross = np.sum(first_spectrum * np.conj(observed_spectrum), axis=0)
    geometry_cross = np.sum(first_spectrum * np.conj(geometry_spectrum), axis=0)
    edge_first = np.sum(first_spectrum * np.conj(geometry_shift[:, 0])[:, None], axis=0)
    edge_last = np.sum(first_spectrum * np.conj(geometry_shift[:, -1])[:, None], axis=0)

    magnitude = np.abs(observed_cross)
    geometry_magnitude = np.abs(geometry_cross)
    valid = (
        np.isfinite(magnitude)
        & np.isfinite(geometry_magnitude)
        & (magnitude > _EPS)
        & (geometry_magnitude > _EPS)
        & (np.abs(frequency) > 0.0)
    )
    if not np.any(valid):
        return {
            "method": _M1,
            "status": "insufficient_support",
            "delta_tau_ns": None,
            "delay_samples": None,
            "fallback_reason": "no_nonzero_cross_spectral_support",
            "support_count": 0,
            "baselines": {},
            "truth_used_in_estimator": False,
        }
    threshold = float(np.percentile(magnitude[valid], support_percentile))
    mask = valid & (magnitude >= max(threshold, _EPS))
    support_count = int(np.count_nonzero(mask))
    frequency_span = float(np.ptp(frequency[mask])) if support_count else 0.0
    if support_count < min_support_bins or frequency_span < min_frequency_span_hz:
        return {
            "method": _M1,
            "status": "insufficient_support",
            "delta_tau_ns": None,
            "delay_samples": None,
            "fallback_reason": "insufficient_frequency_support_or_span",
            "support_count": support_count,
            "frequency_span_hz": frequency_span,
            "baselines": {},
            "truth_used_in_estimator": False,
        }

    weights = np.maximum(magnitude[mask], _EPS)
    weights = weights / float(np.mean(weights))
    observed = observed_cross[mask]
    selected_frequency = frequency[mask]

    def fit_at(delay: float) -> tuple[float, complex, float, float]:
        predicted_all = _predicted_cross_spectrum(
            delay, frequency, sample_rate, geometry_cross, edge_first, edge_last
        )
        return _profile_complex_gain(observed, predicted_all[mask], weights)

    coarse = np.linspace(lower, upper, 401)
    coarse_scores = np.asarray([fit_at(float(value))[0] for value in coarse])
    best_index = int(np.argmin(coarse_scores))
    if best_index == 0 or best_index == len(coarse) - 1:
        return {
            "method": _M1,
            "status": "boundary_solution",
            "delta_tau_ns": None,
            "delay_samples": None,
            "fallback_reason": "best_fit_reached_declared_delay_bound",
            "support_count": support_count,
            "frequency_span_hz": frequency_span,
            "baselines": _slope_baselines(observed_cross, geometry_cross, frequency, mask),
            "truth_used_in_estimator": False,
        }
    best_delay = _golden_section_minimum(
        lambda value: fit_at(value)[0],
        float(coarse[best_index - 1]),
        float(coarse[best_index + 1]),
    )
    _, fitted_gain, relative_residual, phase_rmse = fit_at(best_delay)
    baselines = _slope_baselines(observed_cross, geometry_cross, frequency, mask)
    result = {
        "method": _M1,
        "status": "ok" if relative_residual <= maximum_normalized_model_residual else "model_mismatch",
        "delta_tau_ns": best_delay / sample_rate * 1.0e9,
        "delay_samples": best_delay,
        "fallback_reason": None if relative_residual <= maximum_normalized_model_residual else "stage2_physics_model_residual_exceeds_gate",
        "support_count": support_count,
        "frequency_span_hz": frequency_span,
        "normalized_model_residual": relative_residual,
        "phase_rmse_rad": phase_rmse,
        "fitted_conjugate_gain": {
            "real": float(fitted_gain.real),
            "imag": float(fitted_gain.imag),
            "magnitude": float(abs(fitted_gain)),
            "phase_rad": float(np.angle(fitted_gain)),
        },
        "geometry_delay_sec_min": float(np.min(geometry)),
        "geometry_delay_sec_max": float(np.max(geometry)),
        "relative_response_model": relative_response_model,
        "delay_bounds_samples": [lower, upper],
        "truth_used_in_estimator": False,
        "baselines": baselines,
    }
    if result["status"] == "model_mismatch":
        result["delta_tau_ns"] = None
        result["delay_samples"] = None
    return result
