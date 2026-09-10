#!/usr/bin/env python3
"""Run the deterministic Physics-Adaptive Joint CSI Calibration V1 replay.

The module keeps the production Current result as J0 and adds independent
raw-observable replays:

* J1: D3 delay-only;
* J2: P1 phase-only;
* J3: D3 correction, then P1 re-estimation and correction;
* J4: D3 correction, then P2 re-estimation and correction.

All estimates are formed from measured channel-1/channel-2 fast-time samples.
Scenario truth is deliberately not read here.  A null-derived gate may block
correction and returns the production Current result instead.  The script is
also importable: the pure estimator/gate/correction-decision functions are
used by formula-level tests and by the formal matrix runner.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from estimate_channel_delay import (  # noqa: E402
    estimate_from_raw_time_arrays,
    load_raw_channels,
)
from estimate_temporal_phase import estimate_from_slow_time  # noqa: E402
from experiment_provenance import git_provenance  # noqa: E402
from run_mechanism_aware_oracle import (  # noqa: E402
    inverse_channel_impairment_frequency_domain,
    inverse_channel_phase_trajectory_frequency_domain,
    patch_xml,
    run_logged,
    single_manifest,
    summary_metrics,
    xml_frequency_hz,
    xml_int,
)


METHODS = (
    "J0_Current",
    "J1_D3_delay_only",
    "J2_P1_phase_only",
    "J3_D3_P1_joint",
    "J4_D3_P2_joint",
)
SELECTIVE_METHODS = (
    "J5_Selective_Physics_Calibration",
    "J6_Joint_Phase_Surface",
)
ABLATION_METHODS = (
    "A_P1_then_D3",
    "A_D3_original_P1",
)
ALL_METHODS = METHODS + SELECTIVE_METHODS + ABLATION_METHODS
CALIBRATABLE = "CALIBRATABLE"
UNCERTAIN = "UNCERTAIN"
DECORRELATED = "DECORRELATED"
ACTIVE = "ACTIVE"
INACTIVE = "INACTIVE"
EPS = 1.0e-12


def finite(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.floating, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    return value


def db_ratio(numerator: float, denominator: float) -> float:
    if not (math.isfinite(numerator) and math.isfinite(denominator)):
        return math.nan
    return 10.0 * math.log10(max(numerator, np.finfo(float).tiny) /
                              max(denominator, np.finfo(float).tiny))


def _fit_summary(fit: dict[str, Any]) -> dict[str, Any]:
    """Drop arrays from an estimator fit while preserving auditable scalars."""
    fields = (
        "delta_tau_ns", "slope_rad_per_hz", "intercept_rad", "r2",
        "rmse_rad", "confidence", "row_count", "frequency_bin_count",
        "pulse_count", "slope_rad_per_pulse", "slope_deg_per_pulse",
        "trajectory_rmse_rad", "n",
    )
    result: dict[str, Any] = {}
    for key, value in fit.items():
        if key not in fields or value is None:
            continue
        if isinstance(value, (np.floating, float)):
            result[key] = float(value) if math.isfinite(float(value)) else None
        elif isinstance(value, (np.integer, int)):
            result[key] = int(value)
        else:
            result[key] = value
    return result


def _trajectory(fit_result: dict[str, Any], name: str) -> np.ndarray:
    values = fit_result.get("trajectories", {}).get(name)
    if values is None:
        return np.empty(0, dtype=np.float64)
    return np.asarray(values, dtype=np.float64)


def _coherence_metrics(
    spectrum1: np.ndarray,
    spectrum2: np.ndarray,
    positive: np.ndarray,
) -> dict[str, Any]:
    """Return global and per-pulse coherence for an explicitly corrected pair."""
    if spectrum1.shape != spectrum2.shape or spectrum1.ndim != 2:
        raise ValueError("spectra must be equal-shape 2-D arrays")
    if positive.ndim != 1 or positive.size != spectrum1.shape[1]:
        raise ValueError("positive-frequency mask length mismatch")
    left = np.asarray(spectrum1[:, positive], dtype=np.complex128)
    right = np.asarray(spectrum2[:, positive], dtype=np.complex128)
    cross = left * np.conj(right)
    denom1 = np.sum(np.abs(left) ** 2)
    denom2 = np.sum(np.abs(right) ** 2)
    global_value = (abs(np.sum(cross)) / math.sqrt(float(denom1 * denom2))
                    if denom1 > 0.0 and denom2 > 0.0 else math.nan)
    pulse_denom = np.sqrt(
        np.sum(np.abs(left) ** 2, axis=1) * np.sum(np.abs(right) ** 2, axis=1))
    pulse_cross = np.abs(np.sum(cross, axis=1))
    pulse = np.divide(
        pulse_cross, pulse_denom, out=np.zeros_like(pulse_cross), where=pulse_denom > 0.0)
    return {
        "global": finite(global_value),
        "pulse_median": finite(np.median(pulse)) if pulse.size else math.nan,
        "pulse_p05": finite(np.percentile(pulse, 5.0)) if pulse.size else math.nan,
        "pulse_p95": finite(np.percentile(pulse, 95.0)) if pulse.size else math.nan,
    }


def _corrected_spectrum2(
    spectrum2: np.ndarray,
    frequency_hz: np.ndarray,
    delay_ns: float = 0.0,
    phase_correction_deg: np.ndarray | None = None,
) -> np.ndarray:
    """Apply the same channel-2 correction signs used by the raw replay."""
    if spectrum2.ndim != 2 or frequency_hz.size != spectrum2.shape[1]:
        raise ValueError("spectrum/frequency dimensions do not match")
    result = np.asarray(spectrum2, dtype=np.complex128)
    if math.isfinite(delay_ns) and abs(delay_ns) > 0.0:
        result = result * np.exp(1j * 2.0 * np.pi * frequency_hz[None, :]
                                 * (delay_ns * 1.0e-9))
    if phase_correction_deg is not None:
        phase = np.asarray(phase_correction_deg, dtype=np.float64)
        if phase.ndim != 1 or phase.size != spectrum2.shape[0]:
            raise ValueError("phase correction trajectory length mismatch")
        # inverse_channel_phase_trajectory_frequency_domain multiplies channel
        # 2 by exp(-j*phase_values).  Keep this expression in the audit path
        # so residual coherence has exactly the replay convention.
        result = result * np.exp(-1j * np.deg2rad(phase)[:, None])
    return result


def estimate_observables_from_arrays(
    channel1: np.ndarray,
    channel2: np.ndarray,
    fs_hz: float,
    prf_hz: float,
    raw_support_percentile: float = 80.0,
    include_selective_surface: bool = False,
) -> dict[str, Any]:
    """Estimate D3/P1/P2 and inference-visible support statistics.

    The returned mapping contains private NumPy trajectories for replay and a
    JSON-safe ``summary`` mapping for manifests.  No truth or scenario fields
    are accepted by this function.
    """
    x1 = np.asarray(channel1, dtype=np.complex128)
    x2 = np.asarray(channel2, dtype=np.complex128)
    if x1.shape != x2.shape or x1.ndim != 2:
        raise ValueError("channel arrays must be equal-shape 2-D [pulse, sample]")
    if fs_hz <= 0.0 or prf_hz <= 0.0:
        raise ValueError("fs_hz and prf_hz must be positive")

    delay_result = estimate_from_raw_time_arrays(
        x1, x2, fs_hz, raw_support_percentile)
    spectrum1 = np.fft.fft(x1, axis=1)
    spectrum2 = np.fft.fft(x2, axis=1)
    frequency = np.fft.fftfreq(x1.shape[1], d=1.0 / fs_hz)
    positive = frequency > 0.0
    phase_result = estimate_from_slow_time(
        spectrum1, spectrum2, positive, prf_hz)

    cross = spectrum1[:, positive] * np.conj(spectrum2[:, positive])
    denom1 = np.sum(np.abs(spectrum1[:, positive]) ** 2)
    denom2 = np.sum(np.abs(spectrum2[:, positive]) ** 2)
    global_coherence = (abs(np.sum(cross)) / math.sqrt(float(denom1 * denom2))
                        if denom1 > 0.0 and denom2 > 0.0 else math.nan)
    pulse_denom = np.sqrt(
        np.sum(np.abs(spectrum1[:, positive]) ** 2, axis=1) *
        np.sum(np.abs(spectrum2[:, positive]) ** 2, axis=1))
    pulse_cross = np.abs(np.sum(cross, axis=1))
    pulse_coherence = np.divide(
        pulse_cross, pulse_denom, out=np.zeros_like(pulse_cross), where=pulse_denom > 0.0)
    p1_name = "P1_robust_constant_linear"
    p2_name = "P2_robust_quadratic"
    d3_name = "D3_Huber_weighted_LS"
    d3 = delay_result["aggregate"][d3_name]
    p1 = phase_result["fits"][p1_name]
    p2 = phase_result["fits"][p2_name]
    summary = {
        "delay": _fit_summary(d3),
        "phase_p1": _fit_summary(p1),
        "phase_p2": _fit_summary(p2),
        "coherence": {
            "global": finite(global_coherence),
            "pulse_median": finite(np.median(pulse_coherence)),
            "pulse_p05": finite(np.percentile(pulse_coherence, 5.0)),
            "pulse_p95": finite(np.percentile(pulse_coherence, 95.0)),
            "frequency_bin_count": int(np.count_nonzero(positive)),
            "pulse_count": int(x1.shape[0]),
        },
        "input": {
            "shape": [int(x1.shape[0]), int(x1.shape[1])],
            "fs_hz": float(fs_hz),
            "prf_hz": float(prf_hz),
            # A discrete record has a finite unambiguous delay window.  This
            # bound is derived from the measured sample count and sample rate;
            # it is not an injected mismatch value.
            "max_supported_delay_ns": float(
                0.5 * x1.shape[1] / fs_hz * 1.0e9),
            "support_percentile": float(raw_support_percentile),
            "cross_spectrum": "sum_pulses(FFT(channel1)*conj(FFT(channel2)))",
            "truth_used_in_estimator": False,
        },
    }
    if include_selective_surface:
        p1_correction = -_trajectory(phase_result, p1_name)
        p2_correction = -_trajectory(phase_result, p2_name)
        corrected_p1 = _corrected_spectrum2(
            spectrum2, frequency, finite(d3.get("delta_tau_ns")), p1_correction)
        corrected_p2 = _corrected_spectrum2(
            spectrum2, frequency, finite(d3.get("delta_tau_ns")), p2_correction)
        residual_p1 = _coherence_metrics(spectrum1, corrected_p1, positive)
        residual_p2 = _coherence_metrics(spectrum1, corrected_p2, positive)
        joint = estimate_joint_phase_surface_from_spectra(
            spectrum1, spectrum2, frequency, positive, prf_hz,
            initial_delay_ns=finite(d3.get("delta_tau_ns")),
            initial_phase_p1_deg_per_pulse=finite(p1.get("slope_deg_per_pulse")),
            initial_phase_p2_deg_per_pulse=finite(p2.get("slope_deg_per_pulse")),
        )
        summary["coherence"]["residual_p1_global"] = residual_p1["global"]
        summary["coherence"]["residual_p1_pulse_median"] = residual_p1["pulse_median"]
        summary["coherence"]["residual_p2_global"] = residual_p2["global"]
        summary["coherence"]["residual_p2_pulse_median"] = residual_p2["pulse_median"]
        summary["coherence"]["residual_joint_global"] = joint["residual_coherence_global"]
        summary["coherence"]["residual_joint_pulse_median"] = joint["residual_coherence_pulse_median"]
        summary["joint_surface"] = joint["summary"]
    return {
        "summary": summary,
        "delay_ns": finite(d3.get("delta_tau_ns")),
        "delay_confidence": finite(d3.get("confidence"), 0.0),
        "phase_p1_observed": _trajectory(phase_result, p1_name),
        "phase_p2_observed": _trajectory(phase_result, p2_name),
        "phase_p1_correction": -_trajectory(phase_result, p1_name),
        "phase_p2_correction": -_trajectory(phase_result, p2_name),
        "phase_p1_slope_deg_per_pulse": finite(p1.get("slope_deg_per_pulse")),
        "phase_p2_slope_deg_per_pulse": finite(p2.get("slope_deg_per_pulse")),
        "phase_p1_confidence": finite(p1.get("confidence"), 0.0),
        "phase_p2_confidence": finite(p2.get("confidence"), 0.0),
        "coherence": finite(global_coherence),
        "pulse_coherence_median": finite(np.median(pulse_coherence)),
        "residual_p1": residual_p1 if include_selective_surface else None,
        "residual_p2": residual_p2 if include_selective_surface else None,
        "joint_surface": joint if include_selective_surface else None,
    }


def estimate_joint_phase_surface_from_spectra(
    spectrum1: np.ndarray,
    spectrum2: np.ndarray,
    frequency_hz: np.ndarray,
    positive: np.ndarray,
    prf_hz: float,
    initial_delay_ns: float | None = None,
    initial_phase_p1_deg_per_pulse: float | None = None,
    initial_phase_p2_deg_per_pulse: float | None = None,
    iterations: int = 8,
) -> dict[str, Any]:
    """Fit the measured joint phase surface without scenario truth.

    The fit uses the circular residual
    ``angle(exp(1j * (observed - model)))`` and Huber IRLS weights.  Frequency
    and pulse axes are centered/scaled only for numerical conditioning; the
    returned coefficients are converted back to physical units.  The fit is
    intentionally pure NumPy so formula-level tests can exercise it without a
    production case or a CUDA device.
    """
    left = np.asarray(spectrum1, dtype=np.complex128)
    right = np.asarray(spectrum2, dtype=np.complex128)
    frequency = np.asarray(frequency_hz, dtype=np.float64).reshape(-1)
    valid_frequency = np.asarray(positive, dtype=bool).reshape(-1)
    if left.shape != right.shape or left.ndim != 2:
        raise ValueError("spectra must be equal-shape 2-D arrays")
    if frequency.size != left.shape[1] or valid_frequency.size != left.shape[1]:
        raise ValueError("frequency axis/mask length mismatch")
    if prf_hz <= 0.0:
        raise ValueError("prf_hz must be positive")
    f = frequency[valid_frequency]
    if f.size < 4:
        raise ValueError("joint surface needs at least four positive-frequency bins")
    pulses = np.arange(left.shape[0], dtype=np.float64)
    if pulses.size < 4:
        raise ValueError("joint surface needs at least four pulses")
    cross = left[:, valid_frequency] * np.conj(right[:, valid_frequency])
    observed = np.angle(cross)
    magnitude = np.abs(cross)
    finite = np.isfinite(observed) & np.isfinite(magnitude) & (magnitude > EPS)
    if int(np.count_nonzero(finite)) < 12:
        raise ValueError("joint surface has insufficient finite cross-spectrum support")

    f_center = float(np.mean(f))
    f_scale = max(float(np.std(f)), 1.0)
    p_center = float(np.mean(pulses))
    p_scale = max(float(np.std(pulses)), 1.0)
    ff, pp = np.meshgrid((f - f_center) / f_scale,
                         (pulses - p_center) / p_scale)
    design = np.column_stack([
        np.ones(ff.size, dtype=np.float64),
        ff.reshape(-1),
        pp.reshape(-1),
        (pp ** 2).reshape(-1),
    ])
    y_wrapped = observed.reshape(-1)
    weights = magnitude.reshape(-1)
    good = finite.reshape(-1)
    design = design[good]
    y_wrapped = y_wrapped[good]
    weights = weights[good]
    weights = weights / max(float(np.median(weights)), EPS)

    # Unwrapping is used only to obtain an initial basin.  All subsequent
    # updates use circular residuals, so a phase wrap or isolated outlier
    # cannot become a truth-derived gate.
    unwrapped = np.unwrap(np.unwrap(observed, axis=1), axis=0).reshape(-1)[good]
    normal = design.T @ (weights[:, None] * design)
    rhs = design.T @ (weights * unwrapped)
    try:
        coefficients = np.linalg.solve(normal, rhs)
    except np.linalg.LinAlgError:
        coefficients = np.linalg.lstsq(normal, rhs, rcond=None)[0]

    # If the stable one-dimensional fits are available, use them only as an
    # observable initializer.  They are not used as a positive acceptance
    # threshold and do not receive any scenario parameter.
    if initial_delay_ns is not None and math.isfinite(float(initial_delay_ns)):
        coefficients[1] = 2.0 * np.pi * float(initial_delay_ns) * 1.0e-9 * f_scale
    if (initial_phase_p1_deg_per_pulse is not None and
            math.isfinite(float(initial_phase_p1_deg_per_pulse))):
        coefficients[2] = math.radians(float(initial_phase_p1_deg_per_pulse)) * p_scale
    # A P2 slope is an observable initializer for the linear term only.  The
    # quadratic term remains estimated by the joint surface itself.
    if (initial_phase_p2_deg_per_pulse is not None and
            math.isfinite(float(initial_phase_p2_deg_per_pulse)) and
            initial_phase_p1_deg_per_pulse is None):
        coefficients[2] = math.radians(float(initial_phase_p2_deg_per_pulse)) * p_scale
    model = design @ coefficients
    for _ in range(max(1, int(iterations))):
        residual = np.angle(np.exp(1j * (y_wrapped - model)))
        scale = max(1.4826 * float(np.median(np.abs(
            residual - np.median(residual)))), 1.0e-3)
        standardized = np.abs(residual) / scale
        robust = np.where(standardized <= 1.345, 1.0,
                          1.345 / np.maximum(standardized, EPS))
        effective = weights * robust
        target = model + residual
        normal = design.T @ (effective[:, None] * design)
        rhs = design.T @ (effective * target)
        try:
            updated = np.linalg.solve(normal, rhs)
        except np.linalg.LinAlgError:
            updated = np.linalg.lstsq(normal, rhs, rcond=None)[0]
        if float(np.max(np.abs(updated - coefficients))) < 1.0e-10:
            coefficients = updated
            break
        coefficients = updated
        model = design @ coefficients
    model = design @ coefficients
    residual = np.angle(np.exp(1j * (y_wrapped - model)))
    effective = weights * np.where(
        np.abs(residual) <= 1.345 * max(1.4826 * float(np.median(
            np.abs(residual - np.median(residual)))), 1.0e-3),
        1.0,
        (1.345 * max(1.4826 * float(np.median(
            np.abs(residual - np.median(residual)))), 1.0e-3)) /
        np.maximum(np.abs(residual), EPS),
    )
    rmse = math.sqrt(float(np.sum(effective * residual ** 2)) /
                     max(float(np.sum(effective)), EPS))

    full_model = (coefficients[0] + coefficients[1] * ff +
                  coefficients[2] * pp + coefficients[3] * pp ** 2)
    corrected_cross = cross * np.exp(-1j * full_model)
    residual_global = (abs(np.sum(corrected_cross)) /
                       max(float(np.sum(np.abs(corrected_cross))), EPS))
    pulse_denominator = np.sum(np.abs(corrected_cross), axis=1)
    pulse_coherence = np.abs(np.sum(corrected_cross, axis=1)) / np.maximum(
        pulse_denominator, EPS)
    tau_ns = float(coefficients[1] / (2.0 * np.pi * f_scale) * 1.0e9)
    beta2_rad = float(coefficients[3] / (p_scale ** 2))
    # The fitted basis is centered at p_center.  Convert the centered linear
    # coefficient back to the requested physical polynomial coefficient at
    # p=0: c1/p_scale = beta1 + 2*beta2*p_center.
    beta1_rad = float(coefficients[2] / p_scale - 2.0 * beta2_rad * p_center)
    beta1_deg = float(np.degrees(beta1_rad))
    beta2_deg = float(np.degrees(beta2_rad))
    confidence = float(np.clip(residual_global *
                               max(0.0, 1.0 - rmse / np.pi), 0.0, 1.0) *
                      min(1.0, design.shape[0] / 128.0))
    phase_correction = -(
        np.degrees(coefficients[2] * (pulses - p_center) / p_scale) +
        np.degrees(coefficients[3] * ((pulses - p_center) / p_scale) ** 2)
    )
    summary = {
        "tau_ns": tau_ns,
        "beta1_deg_per_pulse": beta1_deg,
        "beta2_deg_per_pulse2": beta2_deg,
        "intercept_rad_centered": float(coefficients[0]),
        "rmse_rad": float(rmse),
        "confidence": confidence,
        "support": int(design.shape[0]),
        "residual_coherence_global": float(residual_global),
        "residual_coherence_pulse_median": float(np.median(pulse_coherence)),
        "frequency_center_hz": f_center,
        "frequency_scale_hz": f_scale,
        "pulse_center": p_center,
        "pulse_scale": p_scale,
        "model": "angle(C12(p,f)) = b + 2*pi*f*tau + beta1*p + beta2*p^2 + epsilon",
        "truth_used_in_estimator": False,
    }
    return {
        "summary": summary,
        "tau_ns": tau_ns,
        "beta1_deg_per_pulse": beta1_deg,
        "beta2_deg_per_pulse2": beta2_deg,
        "intercept_rad_centered": float(coefficients[0]),
        "rmse_rad": float(rmse),
        "confidence": confidence,
        "support": int(design.shape[0]),
        "residual_coherence_global": float(residual_global),
        "residual_coherence_pulse_median": float(np.median(pulse_coherence)),
        "phase_correction_deg_by_pulse": phase_correction,
    }


def compact_observables(observables: dict[str, Any]) -> dict[str, Any]:
    """Return only JSON-safe inference-visible estimator evidence."""
    return json.loads(json.dumps(json_safe(observables["summary"]), allow_nan=False))


def calibrate_null_gate(
    null_observations: list[dict[str, Any]],
    calibration_set_id: str,
    deadband_quantile: float = 0.99,
    confidence_quantile: float = 0.05,
    decorrelation_quantile: float = 0.01,
) -> dict[str, Any]:
    """Freeze deadbands and confidence/fallback thresholds from null only."""
    if len(null_observations) < 3:
        raise ValueError("at least three null observations are required")

    def values(path: tuple[str, ...]) -> np.ndarray:
        data = []
        for row in null_observations:
            item: Any = row
            for key in path:
                item = item[key]
            value = finite(item)
            if math.isfinite(value):
                data.append(value)
        if len(data) < 3:
            raise ValueError(f"null population lacks finite values: {path}")
        return np.asarray(data, dtype=np.float64)

    delay_abs = np.abs(values(("delay", "delta_tau_ns")))
    phase_abs = np.abs(values(("phase_p1", "slope_deg_per_pulse")))
    phase_p2_abs = np.abs(values(("phase_p2", "slope_deg_per_pulse")))
    delay_conf = values(("delay", "confidence"))
    phase_conf = values(("phase_p1", "confidence"))
    phase_p2_conf = values(("phase_p2", "confidence"))
    delay_rmse = values(("delay", "rmse_rad"))
    phase_rmse = values(("phase_p1", "rmse_rad"))
    phase_p2_rmse = values(("phase_p2", "rmse_rad"))
    pulse_coherence = values(("coherence", "pulse_median"))
    def optional_values(path: tuple[str, ...]) -> np.ndarray | None:
        data: list[float] = []
        for row in null_observations:
            item: Any = row
            try:
                for key in path:
                    item = item[key]
            except (KeyError, TypeError):
                continue
            value = finite(item)
            if math.isfinite(value):
                data.append(value)
        return np.asarray(data, dtype=np.float64) if len(data) >= 3 else None

    residual_p1 = optional_values(("coherence", "residual_p1_pulse_median"))
    residual_p2 = optional_values(("coherence", "residual_p2_pulse_median"))
    residual_joint = optional_values(("coherence", "residual_joint_pulse_median"))
    joint_conf = optional_values(("joint_surface", "confidence"))
    joint_beta = optional_values(("joint_surface", "beta1_deg_per_pulse"))
    joint_beta2 = optional_values(("joint_surface", "beta2_deg_per_pulse2"))
    selective_thresholds: dict[str, float] = {}
    if residual_p1 is not None:
        selective_thresholds["residual_coherence_p1_pulse_floor"] = float(
            np.quantile(residual_p1, decorrelation_quantile))
    if residual_p2 is not None:
        selective_thresholds["residual_coherence_p2_pulse_floor"] = float(
            np.quantile(residual_p2, decorrelation_quantile))
    if residual_joint is not None:
        selective_thresholds["residual_coherence_joint_pulse_floor"] = float(
            np.quantile(residual_joint, decorrelation_quantile))
    if joint_conf is not None:
        selective_thresholds["confidence_joint"] = float(
            np.quantile(joint_conf, confidence_quantile))
    if joint_beta is not None and joint_beta2 is not None:
        joint_magnitude = np.sqrt(joint_beta ** 2 + joint_beta2 ** 2)
        selective_thresholds["epsilon_phase_surface_deg_per_pulse"] = float(
            np.quantile(joint_magnitude, deadband_quantile))
    result = {
        "schema_version": 2 if selective_thresholds else 1,
        "calibration_set_id": calibration_set_id,
        "source": "zero-mismatch C+N raw-observable null population only",
        "ai_training": False,
        "quantiles": {
            "deadband": deadband_quantile,
            "confidence": confidence_quantile,
            "decorrelation": decorrelation_quantile,
        },
        "sample_count": len(null_observations),
        "thresholds": {
            "epsilon_tau_ns": float(np.quantile(delay_abs, deadband_quantile)),
            "epsilon_phase_deg_per_pulse": float(np.quantile(phase_abs, deadband_quantile)),
            "epsilon_phase_p2_deg_per_pulse": float(np.quantile(
                phase_p2_abs, deadband_quantile)),
            "confidence_tau": float(np.quantile(delay_conf, confidence_quantile)),
            "confidence_phase": float(np.quantile(phase_conf, confidence_quantile)),
            "confidence_phase_p2": float(np.quantile(phase_p2_conf, confidence_quantile)),
            "max_rmse_tau_rad": float(np.quantile(delay_rmse, deadband_quantile)),
            "max_rmse_phase_rad": float(np.quantile(phase_rmse, deadband_quantile)),
            "max_rmse_phase_p2_rad": float(np.quantile(phase_p2_rmse, deadband_quantile)),
            "decorrelation_pulse_coherence": float(np.quantile(
                pulse_coherence, decorrelation_quantile)),
        },
        "null_summary": {
            "delay_abs_min_ns": float(np.min(delay_abs)),
            "delay_abs_max_ns": float(np.max(delay_abs)),
            "phase_abs_min_deg_per_pulse": float(np.min(phase_abs)),
            "phase_abs_max_deg_per_pulse": float(np.max(phase_abs)),
            "pulse_coherence_min": float(np.min(pulse_coherence)),
            "pulse_coherence_max": float(np.max(pulse_coherence)),
        },
    }
    if selective_thresholds:
        result["thresholds"].update(selective_thresholds)
        result["selective_calibration"] = {
            "purpose": "false-activation/decorrelation floors only; positive mismatch quality is frozen separately from calibration+validation",
            "available_fields": sorted(selective_thresholds),
            "null_rmse_not_positive_gate": True,
        }
    return result


def classify_state(
    observables: dict[str, Any], gate: dict[str, Any], phase_method: str = "P1",
) -> dict[str, Any]:
    """Classify from observable coherence/fit quality without truth rho."""
    thresholds = gate["thresholds"]
    delay = observables["summary"]["delay"]
    if phase_method not in {"P1", "P2"}:
        raise ValueError(f"unknown phase method: {phase_method}")
    phase_key = "phase_p1" if phase_method == "P1" else "phase_p2"
    phase = observables["summary"][phase_key]
    coherence = observables["summary"]["coherence"]
    pulse_coherence = finite(coherence.get("pulse_median"))
    reasons: list[str] = []
    if (not math.isfinite(pulse_coherence) or
            pulse_coherence < float(thresholds["decorrelation_pulse_coherence"])):
        return {
            "state": DECORRELATED,
            "reasons": ["pulse_coherence_below_null_floor"],
            "delay_active": False,
            "phase_active": False,
        }

    delay_conf = finite(delay.get("confidence"), 0.0)
    phase_conf = finite(phase.get("confidence"), 0.0)
    delay_rmse = finite(delay.get("rmse_rad"))
    phase_rmse = finite(phase.get("rmse_rad"))
    delay_estimate = finite(delay.get("delta_tau_ns"))
    max_supported_delay = finite(
        observables["summary"].get("input", {}).get("max_supported_delay_ns"),
        math.inf)
    phase_conf_threshold = float(thresholds.get(
        "confidence_phase_p2" if phase_method == "P2" else "confidence_phase",
        thresholds["confidence_phase"]))
    phase_rmse_threshold = float(thresholds.get(
        "max_rmse_phase_p2_rad" if phase_method == "P2" else "max_rmse_phase_rad",
        thresholds["max_rmse_phase_rad"]))
    phase_deadband = float(thresholds.get(
        "epsilon_phase_p2_deg_per_pulse" if phase_method == "P2" else "epsilon_phase_deg_per_pulse",
        thresholds["epsilon_phase_deg_per_pulse"]))
    if delay_conf < float(thresholds["confidence_tau"]):
        reasons.append("delay_confidence_below_null_floor")
    if phase_conf < phase_conf_threshold:
        reasons.append("phase_confidence_below_null_floor")
    if not math.isfinite(delay_rmse) or delay_rmse > float(thresholds["max_rmse_tau_rad"]):
        reasons.append("delay_fit_residual_above_null_envelope")
    if math.isfinite(delay_estimate) and abs(delay_estimate) > max_supported_delay:
        reasons.append("delay_outside_unambiguous_raw_window")
    if not math.isfinite(phase_rmse) or phase_rmse > phase_rmse_threshold:
        reasons.append("phase_fit_residual_above_null_envelope")
    state = CALIBRATABLE if not reasons else UNCERTAIN
    delay_active = (state == CALIBRATABLE and
                    abs(delay_estimate) > float(thresholds["epsilon_tau_ns"]) and
                    delay_conf >= float(thresholds["confidence_tau"]))
    phase_active = (state == CALIBRATABLE and
                    abs(finite(phase.get("slope_deg_per_pulse"))) > phase_deadband and
                    phase_conf >= phase_conf_threshold)
    if not delay_active:
        reasons.append("delay_inside_null_deadband")
    if not phase_active:
        reasons.append("phase_inside_null_deadband")
    return {
        "state": state,
        "reasons": reasons,
        "delay_active": delay_active,
        "phase_active": phase_active,
    }


def _threshold(gate: dict[str, Any], name: str, default: float) -> float:
    value = finite(gate.get("thresholds", {}).get(name), default)
    return value if math.isfinite(value) else default


def classify_selective_state(
    observables: dict[str, Any],
    gate: dict[str, Any],
    phase_method: str = "P1",
    positive_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify decorrelation, delay and phase independently.

    Null-derived thresholds here control false activation and global
    decorrelation only.  A supplied ``positive_gate`` is expected to be
    frozen from calibration+validation and is never learned from the test
    split.  In particular, the V1 null RMSE envelopes are not used as a
    positive mismatch gate.
    """
    if phase_method not in {"P1", "P2", "JOINT"}:
        raise ValueError(f"unknown selective phase method: {phase_method}")
    summary = observables.get("summary", observables)
    thresholds = gate.get("thresholds", {})
    delay = summary.get("delay", {})
    if phase_method == "JOINT":
        phase = summary.get("joint_surface", {})
        phase_conf_key = "confidence_joint"
        phase_rmse_key = "rmse_joint_rad"
        phase_deadband = _threshold(
            gate, "epsilon_phase_surface_deg_per_pulse", 0.0)
        phase_value = math.hypot(
            finite(phase.get("beta1_deg_per_pulse")),
            finite(phase.get("beta2_deg_per_pulse2")))
        residual_key = "residual_joint_pulse_median"
    else:
        phase = summary.get("phase_p1" if phase_method == "P1" else "phase_p2", {})
        phase_conf_key = "confidence_phase" if phase_method == "P1" else "confidence_phase_p2"
        phase_rmse_key = "rmse_phase_rad" if phase_method == "P1" else "rmse_phase_p2_rad"
        phase_deadband = _threshold(
            gate,
            "epsilon_phase_deg_per_pulse" if phase_method == "P1"
            else "epsilon_phase_p2_deg_per_pulse", 0.0)
        phase_value = finite(phase.get("slope_deg_per_pulse"))
        residual_key = ("residual_p1_pulse_median" if phase_method == "P1"
                        else "residual_p2_pulse_median")

    coherence = summary.get("coherence", {})
    pulse_coherence = finite(coherence.get("pulse_median"))
    raw_floor = _threshold(gate, "decorrelation_pulse_coherence", -math.inf)
    global_veto = (not math.isfinite(pulse_coherence) or pulse_coherence < raw_floor)
    residual_value = finite(coherence.get(residual_key))
    residual_floor = _threshold(gate, {
        "P1": "residual_coherence_p1_pulse_floor",
        "P2": "residual_coherence_p2_pulse_floor",
        "JOINT": "residual_coherence_joint_pulse_floor",
    }[phase_method], -math.inf)
    residual_ok = (not math.isfinite(residual_floor) or
                   (math.isfinite(residual_value) and residual_value >= residual_floor))

    delay_estimate = finite(delay.get("delta_tau_ns"))
    delay_confidence = finite(delay.get("confidence"), 0.0)
    delay_rmse = finite(delay.get("rmse_rad"))
    max_supported = finite(summary.get("input", {}).get(
        "max_supported_delay_ns"), math.inf)
    delay_conf_ok = delay_confidence >= _threshold(gate, "confidence_tau", math.inf)
    delay_support_ok = math.isfinite(delay_estimate) and abs(delay_estimate) <= max_supported
    delay_positive = (positive_gate or {}).get("delay", {})
    delay_rmse_limit = finite(delay_positive.get("rmse_max_rad"))
    delay_positive_conf = finite(delay_positive.get("confidence_min"))
    delay_rmse_ok = (math.isfinite(delay_rmse) and
                     (not math.isfinite(delay_rmse_limit) or delay_rmse <= delay_rmse_limit))
    if math.isfinite(delay_positive_conf):
        delay_conf_ok = delay_confidence >= delay_positive_conf
    delay_quality = delay_conf_ok and delay_support_ok and delay_rmse_ok
    delay_deadband = abs(delay_estimate) <= _threshold(gate, "epsilon_tau_ns", math.inf)

    phase_confidence = finite(phase.get("confidence"), 0.0)
    phase_rmse = finite(phase.get("rmse_rad"))
    phase_conf_ok = phase_confidence >= _threshold(gate, phase_conf_key, math.inf)
    phase_positive = (positive_gate or {}).get(
        "joint" if phase_method == "JOINT" else "phase", {})
    phase_rmse_limit = finite(phase_positive.get("rmse_max_rad"))
    phase_positive_conf = finite(phase_positive.get("confidence_min"))
    phase_rmse_ok = (math.isfinite(phase_rmse) and
                     (not math.isfinite(phase_rmse_limit) or phase_rmse <= phase_rmse_limit))
    if math.isfinite(phase_positive_conf):
        phase_conf_ok = phase_confidence >= phase_positive_conf
    phase_quality = phase_conf_ok and phase_rmse_ok and residual_ok
    phase_deadband_inside = math.isfinite(phase_value) and abs(phase_value) <= phase_deadband

    delay_active = bool(not global_veto and delay_quality and not delay_deadband)
    phase_active = bool(not global_veto and phase_quality and not phase_deadband_inside)
    delay_state = (DECORRELATED if global_veto else
                   ACTIVE if delay_active else INACTIVE if delay_quality else UNCERTAIN)
    phase_state = (DECORRELATED if global_veto else
                   ACTIVE if phase_active else INACTIVE if phase_quality else UNCERTAIN)
    reasons: list[str] = []
    if global_veto:
        reasons.append("pulse_coherence_below_null_floor")
    if not delay_quality:
        reasons.append("delay_independent_quality_failed")
    elif delay_deadband:
        reasons.append("delay_inside_null_deadband")
    if not phase_quality:
        reasons.append("phase_independent_quality_failed")
    elif phase_deadband_inside:
        reasons.append("phase_inside_null_deadband")
    if phase_method != "JOINT" and not residual_ok:
        reasons.append("residual_coherence_below_null_floor")
    state = (DECORRELATED if global_veto else
             CALIBRATABLE if (delay_quality or phase_quality) else UNCERTAIN)
    return {
        "state": state,
        "decorrelation_state": DECORRELATED if global_veto else CALIBRATABLE,
        "global_veto": global_veto,
        "delay_state": delay_state,
        "phase_state": phase_state,
        "delay_active": delay_active,
        "phase_active": phase_active,
        "delay_quality": delay_quality,
        "phase_quality": phase_quality,
        "delay_deadband_inside": delay_deadband,
        "phase_deadband_inside": phase_deadband_inside,
        "pulse_coherence": pulse_coherence,
        "residual_coherence": residual_value,
        "phase_method": phase_method,
        "reasons": reasons,
    }


def selective_correction_decision(
    method: str,
    initial: dict[str, Any],
    gate: dict[str, Any],
    positive_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select one of D0P0/D1P0/D0P1/D1P1 without a phase/delay veto chain."""
    if method not in SELECTIVE_METHODS:
        raise ValueError(f"unknown selective method: {method}")
    phase_method = "JOINT" if method == "J6_Joint_Phase_Surface" else "P1"
    state = classify_selective_state(initial, gate, phase_method, positive_gate)
    branch = f"D{int(state['delay_active'])}P{int(state['phase_active'])}"
    if state["global_veto"]:
        return {**state, "branch": "D0P0", "apply_delay": False,
                "apply_phase": False, "fallback": True,
                "fallback_reason": "global_decorrelation_veto"}
    return {**state, "branch": branch,
            "apply_delay": bool(state["delay_active"]),
            "apply_phase": bool(state["phase_active"]),
            "fallback": False, "fallback_reason": None}


def correction_decision(
    method: str, initial: dict[str, Any], gate: dict[str, Any],
) -> dict[str, Any]:
    """Return the pre-correction decision for one method."""
    phase_method = "P2" if method == "J4_D3_P2_joint" else "P1"
    state = classify_state(initial, gate, phase_method)
    if method == "J0_Current":
        return {**state, "apply_delay": False, "apply_phase": False,
                "phase_method": None, "fallback": True, "fallback_reason": "current_baseline"}
    if state["state"] != CALIBRATABLE:
        return {**state, "apply_delay": False, "apply_phase": False,
                "phase_method": None, "fallback": True,
                "fallback_reason": f"state_{state['state'].lower()}"}
    if method == "J1_D3_delay_only":
        apply_delay, apply_phase, phase_method = state["delay_active"], False, None
    elif method == "J2_P1_phase_only":
        apply_delay, apply_phase, phase_method = False, state["phase_active"], "P1"
    elif method in {"J3_D3_P1_joint", "A_D3_original_P1"}:
        apply_delay, apply_phase, phase_method = state["delay_active"], state["phase_active"], "P1"
    elif method == "J4_D3_P2_joint":
        apply_delay, apply_phase, phase_method = state["delay_active"], state["phase_active"], "P2"
    elif method == "A_P1_then_D3":
        apply_delay, apply_phase, phase_method = state["delay_active"], state["phase_active"], "P1"
    else:
        raise ValueError(f"unknown joint method: {method}")
    if not apply_delay and not apply_phase:
        return {**state, "apply_delay": False, "apply_phase": False,
                "phase_method": phase_method, "fallback": True,
                "fallback_reason": "both_components_inside_null_deadband"}
    return {**state, "apply_delay": apply_delay, "apply_phase": apply_phase,
            "phase_method": phase_method, "fallback": False, "fallback_reason": None}


def raw_input(case: Path) -> Path:
    paths = sorted((case / "stage2/data").glob("*period_0000.bin"))
    if len(paths) != 1:
        raise RuntimeError(f"expected one raw period under {case}: {paths}")
    return paths[0]


def xml_path(case: Path) -> Path:
    path = case / "stage2/config/temp_config_stage2_period_0000.xml"
    if not path.is_file():
        raise RuntimeError(f"missing production XML: {path}")
    return path


def case_observables(
    case: Path,
    prf_hz: float,
    support_percentile: float,
    include_selective_surface: bool = False,
) -> tuple[Path, Path, dict[str, Any]]:
    case = case.resolve()
    xml = xml_path(case)
    raw = raw_input(case)
    pulse_len = xml_int(xml, "pulse_len", 11840)
    channel_count = xml_int(xml, "new_protocol_channel_count", 4)
    fs_hz = xml_frequency_hz(xml, "fs", 60.0e6)
    x1, x2 = load_raw_channels(raw, pulse_len, channel_count, 1, 2)
    return raw, xml, estimate_observables_from_arrays(
        x1, x2, fs_hz, prf_hz, support_percentile,
        include_selective_surface=include_selective_surface)


def _phase_for_method(observables: dict[str, Any], phase_method: str | None) -> np.ndarray:
    if phase_method == "JOINT":
        return np.asarray(observables["joint_surface"]["phase_correction_deg_by_pulse"],
                          dtype=np.float64)
    if phase_method == "P2":
        return observables["phase_p2_correction"]
    return observables["phase_p1_correction"]


def apply_raw_correction(
    source: Path,
    target: Path,
    xml: Path,
    delay_ns: float | None,
    phase_deg_by_pulse: np.ndarray | None,
    phase_first: bool = False,
) -> list[dict[str, Any]]:
    """Apply independent frequency-domain delay/phase operations in order."""
    steps: list[dict[str, Any]] = []
    current = source
    temp_paths: list[Path] = []
    try:
        operations: list[tuple[str, Any]] = []
        if phase_first and phase_deg_by_pulse is not None:
            operations.append(("phase", phase_deg_by_pulse))
        if delay_ns is not None:
            operations.append(("delay", float(delay_ns)))
        if not phase_first and phase_deg_by_pulse is not None:
            operations.append(("phase", phase_deg_by_pulse))
        if not operations:
            raise ValueError("at least one correction operation is required")
        for index, (kind, value) in enumerate(operations):
            destination = target if index == len(operations) - 1 else target.with_name(
                target.name + f".step{index}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if kind == "delay":
                transform = inverse_channel_impairment_frequency_domain(
                    current, destination, xml, float(value), 0.0)
            else:
                transform = inverse_channel_phase_trajectory_frequency_domain(
                    current, destination, xml, np.asarray(value, dtype=np.float64))
            steps.append({"kind": kind, **transform})
            if current != source:
                temp_paths.append(current)
            current = destination
        return steps
    finally:
        for path in temp_paths:
            if path != target and path.is_file():
                path.unlink()


def current_metrics(case: Path) -> tuple[Path, dict[str, float]]:
    manifest = single_manifest(case.resolve())
    return manifest, summary_metrics(manifest)


def replay_method(
    case: Path,
    method: str,
    output_dir: Path,
    gate: dict[str, Any],
    initial: dict[str, Any],
    source_raw: Path,
    source_xml: Path,
    current_manifest_path: Path,
    current: dict[str, float],
    build_dir: Path,
    prf_hz: float,
    support_percentile: float,
    positive_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if method in SELECTIVE_METHODS and "joint_surface" not in initial.get("summary", {}):
        raw_x1, raw_x2 = load_raw_channels(
            source_raw,
            xml_int(source_xml, "pulse_len", 11840),
            xml_int(source_xml, "new_protocol_channel_count", 4), 1, 2)
        initial = estimate_observables_from_arrays(
            raw_x1, raw_x2,
            xml_frequency_hz(source_xml, "fs", 60.0e6),
            prf_hz, support_percentile, include_selective_surface=True)
    decision = (selective_correction_decision(method, initial, gate, positive_gate)
                if method in SELECTIVE_METHODS else
                correction_decision(method, initial, gate))
    if method == "J6_Joint_Phase_Surface":
        estimated_delay = finite(initial["summary"].get("joint_surface", {}).get("tau_ns"))
        estimated_phase = finite(initial["summary"].get("joint_surface", {}).get(
            "beta1_deg_per_pulse"))
    else:
        estimated_delay = finite(initial["summary"]["delay"].get("delta_tau_ns"))
        estimated_phase = finite(initial["summary"]["phase_p1"].get("slope_deg_per_pulse"))
    base_row: dict[str, Any] = {
        "method": method,
        "case": str(case.resolve()),
        "state": decision["state"],
        "state_reasons": decision["reasons"],
        "delay_active": decision["delay_active"],
        "phase_active": decision["phase_active"],
        "decorrelation_state": decision.get("decorrelation_state"),
        "global_veto": decision.get("global_veto", False),
        "delay_state": decision.get("delay_state"),
        "phase_state": decision.get("phase_state"),
        "branch": decision.get("branch"),
        "estimated_delay_ns": estimated_delay,
        "estimated_phase_slope_deg_per_pulse": estimated_phase,
        "current_cancellation_db": current.get("cancellation_db"),
        "current_coherence": current.get("coherence"),
        "current_phase_rmse_rad": current.get("phase_rmse_rad"),
        "current_manifest": str(current_manifest_path.resolve()),
        "fallback": bool(decision["fallback"]),
        "fallback_reason": decision["fallback_reason"],
        "status": "fallback_current" if decision["fallback"] else "pending",
        "truth_used_in_estimator": False,
    }
    if decision["fallback"]:
        base_row.update({
            "manifest": str(current_manifest_path.resolve()),
            "estimated_cancellation_db": current.get("cancellation_db"),
            "headroom_gain_db_vs_current": 0.0,
            "estimated_coherence": current.get("coherence"),
            "estimated_phase_rmse_rad": current.get("phase_rmse_rad"),
        })
        return base_row
    if method in SELECTIVE_METHODS and not decision["apply_delay"] and not decision["apply_phase"]:
        # D0P0 is an explicit selective branch.  It is a no-op current replay,
        # not a hidden global fallback, unless the global decorrelation veto
        # above already returned fallback_current.
        base_row.update({
            "manifest": str(current_manifest_path.resolve()),
            "estimated_cancellation_db": current.get("cancellation_db"),
            "headroom_gain_db_vs_current": 0.0,
            "estimated_coherence": current.get("coherence"),
            "estimated_phase_rmse_rad": current.get("phase_rmse_rad"),
            "status": "no_op_current",
        })
        return base_row

    method_dir = output_dir / method
    method_dir.mkdir(parents=True, exist_ok=True)
    corrected_raw = method_dir / "stage2/data/joint_corrected_period_0000.bin"
    delay_ns = (finite(initial["summary"].get("joint_surface", {}).get("tau_ns"))
                if method == "J6_Joint_Phase_Surface" and decision["apply_delay"] else
                finite(initial["summary"]["delay"].get("delta_tau_ns"))
                if decision["apply_delay"] else None)
    phase_method = decision["phase_method"]
    phase_values = (_phase_for_method(initial, phase_method)
                    if decision["apply_phase"] and method != "J3_D3_P1_joint" else None)
    intermediate: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []

    if method == "A_P1_then_D3":
        phase_values = _phase_for_method(initial, "P1") if decision["apply_phase"] else None
        steps = apply_raw_correction(source_raw, corrected_raw, source_xml,
                                     delay_ns, phase_values, phase_first=True)
    elif method == "A_D3_original_P1":
        phase_values = _phase_for_method(initial, "P1") if decision["apply_phase"] else None
        steps = apply_raw_correction(source_raw, corrected_raw, source_xml,
                                     delay_ns, phase_values, phase_first=False)
    elif method in {"J3_D3_P1_joint", "J4_D3_P2_joint",
                    "J5_Selective_Physics_Calibration"}:
        # The phase fit is deliberately rerun on the delay-corrected raw data.
        if decision["apply_delay"]:
            delay_path = method_dir / "stage2/data/.delay_corrected.bin"
            delay_steps = apply_raw_correction(source_raw, delay_path, source_xml,
                                               delay_ns, None)
            corrected_x1, corrected_x2 = load_raw_channels(
                delay_path, xml_int(source_xml, "pulse_len", 11840),
                xml_int(source_xml, "new_protocol_channel_count", 4), 1, 2)
            corrected_obs = estimate_observables_from_arrays(
                corrected_x1, corrected_x2,
                xml_frequency_hz(source_xml, "fs", 60.0e6),
                prf_hz, support_percentile,
                include_selective_surface=(method == "J5_Selective_Physics_Calibration"))
            intermediate["after_delay"] = compact_observables(corrected_obs)
            phase_method = "P2" if method == "J4_D3_P2_joint" else "P1"
            corrected_decision = (
                classify_selective_state(corrected_obs, gate, phase_method, positive_gate)
                if method == "J5_Selective_Physics_Calibration" else
                classify_state(corrected_obs, gate, phase_method))
            if (method != "J5_Selective_Physics_Calibration" and
                    corrected_decision["state"] == DECORRELATED):
                if delay_path.is_file():
                    delay_path.unlink()
                base_row.update({"status": "fallback_current",
                                 "fallback": True,
                                 "fallback_reason": "decorrelated_after_delay_correction"})
                return base_row
            phase_active = bool(corrected_decision["phase_active"])
            phase_values = _phase_for_method(corrected_obs, phase_method) if phase_active else None
            steps = delay_steps
            if phase_values is not None:
                phase_steps = apply_raw_correction(delay_path, corrected_raw, source_xml,
                                                   None, phase_values)
                steps += phase_steps
            else:
                shutil.copyfile(delay_path, corrected_raw)
            if delay_path.is_file():
                delay_path.unlink()
        else:
            phase_method = "P2" if method == "J4_D3_P2_joint" else "P1"
            phase_values = (_phase_for_method(initial, phase_method)
                            if decision["apply_phase"] else None)
            steps = apply_raw_correction(source_raw, corrected_raw, source_xml,
                                         None, phase_values)
    else:
        steps = apply_raw_correction(source_raw, corrected_raw, source_xml,
                                     delay_ns, phase_values)

    result_dir = method_dir / "stage2/algorithm_result/period_0000"
    output_xml = method_dir / "stage2/config/joint_calibration_config.xml"
    patch_xml(source_xml, corrected_raw, result_dir, output_xml)
    log_path = method_dir / "gmticore.log"
    return_code = run_logged(
        [str(build_dir.resolve() / "GMTI_core"), str(output_xml),
         "--runtime-mode=debug", "--runtime-diagnostics=on"], log_path)
    base_row.update({
        "correction_steps": steps,
        "reestimated_phase": method in {"J3_D3_P1_joint", "J4_D3_P2_joint",
                                         "J5_Selective_Physics_Calibration"},
        "intermediate_observables": intermediate,
        "phase_method": phase_method,
        "gmticore_exit_code": return_code,
    })
    if return_code != 0:
        base_row["status"] = "gmticore_failed"
        if corrected_raw.is_file():
            corrected_raw.unlink()
        return base_row
    manifest = single_manifest(method_dir)
    estimated = summary_metrics(manifest)
    base_row.update({
        "status": "pass",
        "manifest": str(manifest.resolve()),
        "estimated_cancellation_db": estimated.get("cancellation_db"),
        "headroom_gain_db_vs_current": estimated.get("cancellation_db", math.nan) - current.get("cancellation_db", math.nan),
        "estimated_coherence": estimated.get("coherence"),
        "estimated_phase_rmse_rad": estimated.get("phase_rmse_rad"),
        "correction_activation": {
            "delay": delay_ns is not None,
            "phase": phase_values is not None,
        },
    })
    if corrected_raw.is_file():
        corrected_raw.unlink()
    return base_row


def write_rows(output: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "method", "status", "state", "fallback", "fallback_reason",
        "delay_active", "phase_active", "decorrelation_state", "global_veto",
        "delay_state", "phase_state", "branch", "estimated_delay_ns",
        "estimated_phase_slope_deg_per_pulse", "current_cancellation_db",
        "estimated_cancellation_db", "headroom_gain_db_vs_current",
        "current_coherence", "estimated_coherence", "current_phase_rmse_rad",
        "estimated_phase_rmse_rad", "reestimated_phase", "phase_method",
        "gmticore_exit_code", "manifest", "current_manifest",
    ]
    with (output / "joint_calibration_summary.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True,
                        help="one production target-on/off case directory")
    parser.add_argument("--gate-json", type=Path, required=True,
                        help="null-calibrated gate JSON")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--prf-hz", type=float, default=1300.0)
    parser.add_argument("--raw-support-percentile", type=float, default=80.0)
    parser.add_argument("--positive-gate-json", type=Path, default=None,
                        help="calibration+validation positive quality gate for J5/J6")
    parser.add_argument("--method", dest="methods", action="append",
                        choices=ALL_METHODS,
                        help="repeat to select methods; default is J0-J4")
    args = parser.parse_args()
    case = args.case.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    gate = json.loads(args.gate_json.resolve().read_text(encoding="utf-8"))
    positive_gate = (
        json.loads(args.positive_gate_json.resolve().read_text(encoding="utf-8"))
        if args.positive_gate_json is not None else None
    )
    methods = args.methods or list(METHODS)
    source_raw, source_xml, initial = case_observables(
        case, args.prf_hz, args.raw_support_percentile,
        include_selective_surface=bool(set(methods) & set(SELECTIVE_METHODS)))
    current_manifest_path, current = current_metrics(case)
    rows: list[dict[str, Any]] = []
    for method in methods:
        rows.append(replay_method(
            case, method, output, gate, initial, source_raw, source_xml,
            current_manifest_path, current, args.build_dir.resolve(),
            args.prf_hz, args.raw_support_percentile, positive_gate))
    write_rows(output, rows)
    (output / "joint_observables.json").write_text(
        json.dumps(json_safe({
            "schema_version": 1,
            "case": str(case),
            "truth_used_in_estimator": False,
            "observables": compact_observables(initial),
            "gate": gate,
            "positive_gate": positive_gate,
        }), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "method_version": "Physics-Adaptive Joint Calibration V1",
        "ai_training": False,
        "truth_used_in_estimator": False,
        **git_provenance(ROOT),
        "case": str(case),
        "gate": str(args.gate_json.resolve()),
        "positive_gate": (str(args.positive_gate_json.resolve())
                          if args.positive_gate_json else None),
        "methods": methods,
        "rows": rows,
        "definitions": {
            "J0_Current": "production Current with no correction",
            "J1_D3_delay_only": "raw D3 delay estimate followed by independent frequency-domain correction",
            "J2_P1_phase_only": "raw P1 phase trajectory followed by per-pulse frequency-domain correction",
            "J3_D3_P1_joint": "D3 correction, corrected-raw P1 re-estimation, then phase correction",
            "J4_D3_P2_joint": "D3 correction, corrected-raw P2 re-estimation, then phase correction",
            "J5_Selective_Physics_Calibration": "independent delay/phase gates with D0P0/D1P0/D0P1/D1P1 and decorrelation veto",
            "J6_Joint_Phase_Surface": "robust circular IRLS fit of delay plus linear/quadratic pulse phase surface",
            "fallback": "UNCERTAIN/DECORRELATED/null-deadband returns Current metrics",
        },
    }
    (output / "joint_calibration_manifest.json").write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    failed = sum(row.get("status") == "gmticore_failed" for row in rows)
    print(json.dumps({"output_dir": str(output), "case": str(case),
                      "methods": methods, "failed": failed}, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
