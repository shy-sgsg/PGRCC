#!/usr/bin/env python3
"""Audit Phase-I unknown-error observability from production-equivalent F1/F2.

The scientific input boundary in this module is deliberately narrow: a four
channel protocol packet is first fused as ``F1=(C1+C3)/2`` and
``F2=(C2+C4)/2``.  All observables and sensitivity classifications are then
computed from F1/F2.  Six raw channel pairs are not used to manufacture an
independent-observability claim.

The CLI runs a one-parameter-at-a-time Stage2 matrix.  Its deterministic
estimators only see the generated protocol IQ and reported configuration;
truth values remain in case metadata for post-hoc evaluation and never enter
the observable calculation.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


try:  # Import works both as ``scripts.foo`` and when run from scripts/.
    from scripts.analyze_four_channel_observables import iter_raw_packets
except ModuleNotFoundError:  # pragma: no cover - exercised by direct invocation.
    from analyze_four_channel_observables import iter_raw_packets


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = ROOT / "configs/research/unknown_system_error_true_geometry_pilot.json"
DEFAULT_OUTPUT_ROOT = ROOT / "outputs/two_channel_error_observability"
DEFAULT_SIMULATOR = ROOT / "build/simulate_stage2_statistical"

STATE_NAMES = (
    "channel_delay_error",
    "inter_pulse_phase_error",
    "baseline_geometry_error",
    "servo_angle_error",
    "platform_velocity_error",
    "yaw_error",
)

OBSERVABLE_NAMES = (
    "cross_channel_phase_rad",
    "phase_vs_frequency_slope_rad_per_hz",
    "phase_vs_pulse_slope_rad_per_pulse",
    "phase_vs_beam_angle_slope_rad_per_sin_theta",
    "range_block_phase_residual_rad",
    "p38_slope_residual_hz",
    "p38_intercept_residual_rad",
    "clutter_doppler_ridge_hz",
    "clutter_ridge_vs_angle_slope_hz_per_deg",
    "ctdr_residual_rad",
    "slow_time_phase_slope_rad_per_pulse",
    "f1_f2_coherence",
    "csi_residual_power_db",
)

OBSERVABLE_METADATA = {
    "cross_channel_phase_rad": {
        "source_id": "protocol_f1_f2",
        "axis_group": "range_frequency_pulse",
        "range_group": "all_range_bins",
        "angle_group": "beam_angle_grouped",
        "pulse_group": "all_pulse_rows",
    },
    "phase_vs_frequency_slope_rad_per_hz": {
        "source_id": "protocol_f1_f2",
        "axis_group": "frequency",
        "range_group": "range_block_summed",
        "angle_group": "all_beam_angles",
        "pulse_group": "pulse_averaged",
    },
    "phase_vs_pulse_slope_rad_per_pulse": {
        "source_id": "protocol_f1_f2",
        "axis_group": "pulse",
        "range_group": "all_range_bins",
        "angle_group": "beam_angle_grouped",
        "pulse_group": "pulse_rows",
    },
    "phase_vs_beam_angle_slope_rad_per_sin_theta": {
        "source_id": "protocol_f1_f2",
        "axis_group": "beam_angle",
        "range_group": "all_range_bins",
        "angle_group": "unique_beam_angles",
        "pulse_group": "pulse_averaged",
    },
    "range_block_phase_residual_rad": {
        "source_id": "protocol_f1_f2",
        "axis_group": "range_block",
        "range_group": "eight_or_fewer_range_blocks",
        "angle_group": "all_beam_angles",
        "pulse_group": "all_pulse_rows",
    },
    "p38_slope_residual_hz": {
        "source_id": "protocol_f1_f2",
        "axis_group": "pulse_to_doppler",
        "range_group": "all_range_bins",
        "angle_group": "beam_angle_grouped",
        "pulse_group": "pulse_rows",
    },
    "p38_intercept_residual_rad": {
        "source_id": "protocol_f1_f2",
        "axis_group": "pulse_intercept",
        "range_group": "all_range_bins",
        "angle_group": "beam_angle_grouped",
        "pulse_group": "pulse_rows",
    },
    "clutter_doppler_ridge_hz": {
        "source_id": "protocol_f1_f2",
        "axis_group": "slow_time_fft",
        "range_group": "range_averaged",
        "angle_group": "all_beam_angles",
        "pulse_group": "slow_time_rows",
    },
    "clutter_ridge_vs_angle_slope_hz_per_deg": {
        "source_id": "protocol_f1_f2",
        "axis_group": "slow_time_fft_vs_angle",
        "range_group": "range_averaged",
        "angle_group": "unique_beam_angles",
        "pulse_group": "repeated_pulses_per_angle_required",
    },
    "ctdr_residual_rad": {
        "source_id": "protocol_f1_f2",
        "axis_group": "split_slow_time",
        "range_group": "all_range_bins",
        "angle_group": "beam_angle_grouped",
        "pulse_group": "first_vs_second_half",
    },
    "slow_time_phase_slope_rad_per_pulse": {
        "source_id": "protocol_f1_f2",
        "axis_group": "slow_time_phase",
        "range_group": "all_range_bins",
        "angle_group": "beam_angle_grouped",
        "pulse_group": "pulse_rows",
    },
    "f1_f2_coherence": {
        "source_id": "protocol_f1_f2",
        "axis_group": "cross_channel_coherence",
        "range_group": "all_range_bins",
        "angle_group": "all_beam_angles",
        "pulse_group": "all_pulse_rows",
    },
    "csi_residual_power_db": {
        "source_id": "protocol_f1_f2",
        "axis_group": "complex_ls_residual",
        "range_group": "all_range_bins",
        "angle_group": "all_beam_angles",
        "pulse_group": "all_pulse_rows",
    },
}

DEFAULT_PERTURATIONS = {
    "channel_delay_error": 0.5,          # ns
    "inter_pulse_phase_error": 0.5,      # deg/pulse
    "baseline_geometry_error": 0.5,      # mm
    "servo_angle_error": 0.05,           # deg
    "platform_velocity_error": 0.05,     # m/s
    "yaw_error": 0.05,                   # deg
}

EXTERNAL_PRIORS = (
    "INS velocity/yaw or attitude solution",
    "servo encoder or beam-pointing telemetry",
    "factory channel calibration and baseline survey",
    "temporal prior across adjacent processing cycles",
)

NEAR_CONFOUNDING_THRESHOLD = 0.90
ZERO_CONTROL_DELTA_TOLERANCE = 1.0e-9


def _finite_array(value: object, *, name: str) -> np.ndarray:
    array = np.asarray(value)
    if not np.all(np.isfinite(array.real if np.iscomplexobj(array) else array)):
        raise ValueError(f"{name} contains non-finite values")
    return array


def fuse_protocol_channels_to_f1_f2(channels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fuse protocol channels using the production ``(1,3)/(2,4)`` rule."""

    values = np.asarray(channels)
    if values.ndim < 2 or values.shape[-1] != 4:
        raise ValueError("four-channel protocol input must have shape (..., 4)")
    if not np.iscomplexobj(values):
        values = values.astype(np.complex128)
    _finite_array(values, name="four-channel protocol input")
    f1 = 0.5 * (values[..., 0] + values[..., 2])
    f2 = 0.5 * (values[..., 1] + values[..., 3])
    return f1, f2


def _wrap_phase(value: np.ndarray | float) -> np.ndarray | float:
    wrapped = (np.asarray(value) + math.pi) % (2.0 * math.pi) - math.pi
    if np.ndim(value) == 0:
        return float(wrapped)
    return wrapped


def _fit_line(
    x: Sequence[float],
    y: Sequence[float],
    *,
    unwrap_phase: bool = True,
) -> dict[str, float | int | None]:
    xx = np.asarray(x, dtype=np.float64).reshape(-1)
    yy = np.asarray(y, dtype=np.float64).reshape(-1)
    if xx.shape != yy.shape:
        raise ValueError("fit axes must have equal lengths")
    valid = np.isfinite(xx) & np.isfinite(yy)
    xx = xx[valid]
    yy = yy[valid]
    if xx.size < 2 or float(np.ptp(xx)) <= np.finfo(float).eps:
        return {"n": int(xx.size), "slope": None, "intercept": None, "rmse": None}
    order = np.argsort(xx, kind="mergesort")
    xx = xx[order]
    yy = yy[order]
    if unwrap_phase:
        yy = np.unwrap(yy)
    design = np.column_stack((xx, np.ones(xx.size, dtype=np.float64)))
    coefficients, _, rank, _ = np.linalg.lstsq(design, yy, rcond=None)
    if int(rank) < 2:
        return {"n": int(xx.size), "slope": None, "intercept": None, "rmse": None}
    residual = yy - design @ coefficients
    return {
        "n": int(xx.size),
        "slope": float(coefficients[0]),
        "intercept": float(coefficients[1]),
        "rmse": float(np.sqrt(np.mean(residual * residual))),
    }


def _phase_and_coherence(cross: np.ndarray, f1: np.ndarray, f2: np.ndarray) -> tuple[float | None, float | None]:
    total = complex(np.sum(cross, dtype=np.complex128))
    p1 = float(np.sum(np.abs(f1) ** 2, dtype=np.float64))
    p2 = float(np.sum(np.abs(f2) ** 2, dtype=np.float64))
    denominator = math.sqrt(max(0.0, p1 * p2))
    if abs(total) == 0.0 or not math.isfinite(total.real) or not math.isfinite(total.imag):
        phase = None
    else:
        phase = float(math.atan2(total.imag, total.real))
    if denominator <= 0.0 or not math.isfinite(denominator):
        coherence = None
    else:
        coherence = float(np.clip(abs(total) / denominator, 0.0, 1.0))
    return phase, coherence


def _group_phase_by_angle(
    phase_by_row: np.ndarray,
    coherence_by_row: np.ndarray,
    beam_angle_deg: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    valid = np.isfinite(beam_angle_deg) & np.isfinite(phase_by_row) & np.isfinite(coherence_by_row)
    if not np.any(valid):
        return np.empty(0), np.empty(0), np.empty(0)
    grouped: list[tuple[float, float, float]] = []
    for angle in sorted(set(float(value) for value in beam_angle_deg[valid])):
        selected = valid & (beam_angle_deg == angle)
        if not np.any(selected):
            continue
        phases = phase_by_row[selected]
        weights = np.maximum(coherence_by_row[selected], 1.0e-12)
        z = np.sum(weights * np.exp(1j * phases))
        grouped.append((angle, float(np.angle(z)), float(np.mean(weights))))
    if not grouped:
        return np.empty(0), np.empty(0), np.empty(0)
    array = np.asarray(grouped, dtype=np.float64)
    return array[:, 0], array[:, 1], array[:, 2]


def compute_f1_f2_observables(
    f1: np.ndarray,
    f2: np.ndarray,
    *,
    frequency_hz: Sequence[float] | None = None,
    pulse_index: Sequence[float] | None = None,
    beam_angle_deg: Sequence[float] | None = None,
    prf_hz: float = 1.0,
) -> dict[str, float | None]:
    """Compute the unified observable vector from F1/F2 only.

    Arrays use ``[observation, range/frequency-bin]``.  An observation is a
    protocol pulse/beam row.  The function does not use target truth, true
    geometry or raw six-pair channels.
    """

    first = np.asarray(f1)
    second = np.asarray(f2)
    if first.shape != second.shape or first.ndim != 2:
        raise ValueError("F1/F2 must be equal-shape 2-D arrays")
    if not np.iscomplexobj(first):
        first = first.astype(np.complex128)
    if not np.iscomplexobj(second):
        second = second.astype(np.complex128)
    _finite_array(first, name="F1")
    _finite_array(second, name="F2")
    rows, bins = first.shape
    if rows < 1 or bins < 1:
        raise ValueError("F1/F2 must contain at least one observation and one bin")
    if not math.isfinite(float(prf_hz)) or float(prf_hz) <= 0.0:
        raise ValueError("prf_hz must be finite and positive")
    frequency = np.arange(bins, dtype=np.float64) if frequency_hz is None else np.asarray(frequency_hz, dtype=np.float64).reshape(-1)
    pulse = np.arange(rows, dtype=np.float64) if pulse_index is None else np.asarray(pulse_index, dtype=np.float64).reshape(-1)
    angle = np.zeros(rows, dtype=np.float64) if beam_angle_deg is None else np.asarray(beam_angle_deg, dtype=np.float64).reshape(-1)
    if frequency.size != bins:
        raise ValueError("frequency_hz length does not match F1/F2 bins")
    if pulse.size != rows or angle.size != rows:
        raise ValueError("pulse_index/beam_angle_deg lengths must match F1/F2 rows")
    if not np.all(np.isfinite(frequency)) or not np.all(np.isfinite(pulse)) or not np.all(np.isfinite(angle)):
        raise ValueError("observable axes must be finite")

    cross = first * np.conj(second)
    phase, coherence = _phase_and_coherence(cross, first, second)
    cross_by_bin = np.sum(cross, axis=0, dtype=np.complex128)
    bin_phase = np.angle(cross_by_bin)
    frequency_fit = _fit_line(frequency, bin_phase)
    cross_by_row = np.sum(cross, axis=1, dtype=np.complex128)
    row_phase = np.angle(cross_by_row)
    row_power = np.sum(np.abs(first) ** 2, axis=1, dtype=np.float64)
    row_power_2 = np.sum(np.abs(second) ** 2, axis=1, dtype=np.float64)
    row_denominator = np.sqrt(np.maximum(row_power * row_power_2, 0.0))
    row_coherence = np.divide(
        np.abs(cross_by_row), row_denominator,
        out=np.zeros(rows, dtype=np.float64), where=row_denominator > 0.0,
    )
    pulse_fit = _fit_line(pulse, row_phase)
    grouped_angle, grouped_phase, grouped_weight = _group_phase_by_angle(
        row_phase, row_coherence, angle
    )
    angle_fit = _fit_line(np.sin(np.deg2rad(grouped_angle)), grouped_phase)

    block_count = min(8, bins)
    block_phases: list[float] = []
    for block in np.array_split(np.arange(bins), block_count):
        block_cross = np.sum(cross[:, block], dtype=np.complex128)
        if abs(block_cross) > 0.0:
            block_phases.append(float(np.angle(block_cross)))
    if block_phases and phase is not None:
        block_residual = float(np.sqrt(np.mean(np.asarray(_wrap_phase(np.asarray(block_phases) - phase)) ** 2)))
    else:
        block_residual = None

    p38_slope_hz = None
    p38_intercept = None
    if pulse_fit["slope"] is not None:
        p38_slope_hz = float(pulse_fit["slope"]) * float(prf_hz) / (2.0 * math.pi)
        p38_intercept = float(_wrap_phase(float(pulse_fit["intercept"])))

    ridge_hz = None
    if rows >= 2:
        slow_series = np.mean(first, axis=1)
        slow_frequency = np.fft.fftshift(np.fft.fftfreq(rows, d=1.0 / float(prf_hz)))
        slow_spectrum = np.abs(np.fft.fftshift(np.fft.fft(slow_series)))
        if np.any(np.isfinite(slow_spectrum)) and float(np.max(slow_spectrum)) > 0.0:
            ridge_hz = float(slow_frequency[int(np.nanargmax(slow_spectrum))])

    ridge_angle_slope = None
    if grouped_angle.size >= 2 and rows >= 4:
        # When each angle has repeated pulses, calculate a ridge per angle;
        # single-pulse-per-beam inputs are honestly reported as unsupported.
        ridge_rows: list[tuple[float, float]] = []
        for value in sorted(set(float(item) for item in angle)):
            selected = angle == value
            if int(np.count_nonzero(selected)) < 2:
                continue
            series = np.mean(first[selected], axis=1)
            local_freq = np.fft.fftshift(np.fft.fftfreq(series.size, d=1.0 / float(prf_hz)))
            spectrum = np.abs(np.fft.fftshift(np.fft.fft(series)))
            if float(np.max(spectrum)) > 0.0:
                ridge_rows.append((value, float(local_freq[int(np.argmax(spectrum))])))
        if len(ridge_rows) >= 2:
            ridge_array = np.asarray(ridge_rows, dtype=np.float64)
            ridge_fit = _fit_line(ridge_array[:, 0], ridge_array[:, 1], unwrap_phase=False)
            ridge_angle_slope = ridge_fit["slope"]

    ctdr_residual = None
    if rows >= 4:
        midpoint = rows // 2
        first_fit = _fit_line(pulse[:midpoint], row_phase[:midpoint])
        second_fit = _fit_line(pulse[midpoint:], row_phase[midpoint:])
        if first_fit["slope"] is not None and second_fit["slope"] is not None and pulse_fit["slope"] is not None:
            ctdr_residual = float(_wrap_phase(float(first_fit["slope"]) - float(second_fit["slope"])))

    denominator = float(np.sum(np.abs(second) ** 2, dtype=np.float64))
    alpha = np.sum(first * np.conj(second), dtype=np.complex128) / denominator if denominator > 0.0 else 0.0j
    residual_power = float(np.sum(np.abs(first - alpha * second) ** 2, dtype=np.float64))
    signal_power = float(np.sum(np.abs(first) ** 2, dtype=np.float64))
    residual_db = 10.0 * math.log10(max(residual_power, np.finfo(float).tiny) / max(signal_power, np.finfo(float).tiny))

    result: dict[str, float | None] = {
        "cross_channel_phase_rad": phase,
        "phase_vs_frequency_slope_rad_per_hz": frequency_fit["slope"],
        "phase_vs_pulse_slope_rad_per_pulse": pulse_fit["slope"],
        "phase_vs_beam_angle_slope_rad_per_sin_theta": angle_fit["slope"],
        "range_block_phase_residual_rad": block_residual,
        "p38_slope_residual_hz": p38_slope_hz,
        "p38_intercept_residual_rad": p38_intercept,
        "clutter_doppler_ridge_hz": ridge_hz,
        "clutter_ridge_vs_angle_slope_hz_per_deg": ridge_angle_slope,
        "ctdr_residual_rad": ctdr_residual,
        "slow_time_phase_slope_rad_per_pulse": pulse_fit["slope"],
        "f1_f2_coherence": coherence,
        "csi_residual_power_db": residual_db,
    }
    return result


def central_difference_sensitivity(
    plus: Mapping[str, object],
    minus: Mapping[str, object],
    *,
    perturbation: float,
) -> dict[str, float | None]:
    """Return ``(observable(+h)-observable(-h))/(2h)`` without truth input."""

    if not math.isfinite(float(perturbation)) or float(perturbation) <= 0.0:
        raise ValueError("perturbation must be finite and positive")
    keys = sorted(set(plus) | set(minus))
    result: dict[str, float | None] = {}
    for key in keys:
        try:
            high = float(plus[key])
            low = float(minus[key])
        except (KeyError, TypeError, ValueError):
            result[key] = None
            continue
        if not math.isfinite(high) or not math.isfinite(low):
            result[key] = None
        else:
            result[key] = (high - low) / (2.0 * float(perturbation))
    return result


def summarize_seed_stability(
    rows: Iterable[Mapping[str, object]],
    *,
    near_zero_threshold: float = 1.0e-12,
) -> list[dict[str, object]]:
    """Summarize sensitivity sign and spread across the available seeds.

    The summary is descriptive rather than a pass/fail gate: no fixed seed
    count or spread threshold is assumed.  A missing/non-finite sensitivity is
    excluded from the finite summary and remains visible through the counts.
    """

    if not math.isfinite(float(near_zero_threshold)) or float(near_zero_threshold) < 0.0:
        raise ValueError("near_zero_threshold must be finite and non-negative")
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        parameter = str(row.get("parameter", ""))
        observable = str(row.get("observable", ""))
        if not parameter or not observable:
            continue
        raw_seed = row.get("seed", "")
        try:
            seed: int | str = int(raw_seed)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            seed = str(raw_seed)
        group = grouped.setdefault(
            (parameter, observable),
            {"seeds": set(), "finite": []},
        )
        observed_seeds = group["seeds"]
        if isinstance(observed_seeds, set):
            observed_seeds.add(seed)
        try:
            value = float(row.get("sensitivity"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        finite_values = group["finite"]
        if isinstance(finite_values, list):
            finite_values.append((seed, value))

    summary: list[dict[str, object]] = []
    for (parameter, observable) in sorted(grouped):
        group = grouped[(parameter, observable)]
        entries = group["finite"]
        observed_seeds = group["seeds"]
        if not isinstance(entries, list) or not isinstance(observed_seeds, set):
            continue
        values = np.asarray([value for _, value in entries], dtype=np.float64)
        seed_ids = sorted(observed_seeds, key=lambda item: str(item))
        finite_seed_ids = sorted({seed for seed, _ in entries}, key=lambda item: str(item))
        if values.size == 0:
            summary.append({
                "parameter": parameter,
                "observable": observable,
                "seed_count": len(seed_ids),
                "seed_ids": ",".join(str(seed) for seed in seed_ids),
                "finite_seed_count": 0,
                "finite_seed_ids": "",
                "median_sensitivity": None,
                "min_sensitivity": None,
                "max_sensitivity": None,
                "sign_consistent": None,
                "relative_spread": None,
            })
            continue
        signs = np.sign(values[np.abs(values) > float(near_zero_threshold)])
        sign_consistent: bool | None
        if signs.size == 0:
            sign_consistent = True
        else:
            sign_consistent = bool(np.all(signs == signs[0]))
        median = float(np.median(values))
        spread_denominator = max(abs(median), float(near_zero_threshold), np.finfo(float).tiny)
        summary.append({
            "parameter": parameter,
            "observable": observable,
            "seed_count": len(seed_ids),
            "seed_ids": ",".join(str(seed) for seed in seed_ids),
            "finite_seed_count": int(values.size),
            "finite_seed_ids": ",".join(str(seed) for seed in finite_seed_ids),
            "median_sensitivity": median,
            "min_sensitivity": float(np.min(values)),
            "max_sensitivity": float(np.max(values)),
            "sign_consistent": sign_consistent,
            "relative_spread": float((np.max(values) - np.min(values)) / spread_denominator),
        })
    return summary


def summarize_zero_control_delta(
    zero_cases: Mapping[tuple[object, object], Sequence[float]],
    *,
    observable_names: Sequence[str],
    reference_parameter: str,
    tolerance: float = 1.0e-9,
) -> dict[str, object]:
    """Compare each zero-error case with an explicit same-seed reference.

    The reference is another error-free simulator case, not a truth-derived
    expected observable vector.  Using the same seed isolates the
    ``state_parameter`` bookkeeping field from numerical/simulator variation.
    ``tolerance`` is a numerical repeatability tolerance in each observable's
    native units; it is intentionally reported rather than hidden in a pass
    claim.
    """

    if not math.isfinite(float(tolerance)) or float(tolerance) < 0.0:
        raise ValueError("tolerance must be finite and non-negative")
    names = [str(name) for name in observable_names]
    parsed: dict[tuple[str, object], np.ndarray] = {}
    for key, values in zero_cases.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise ValueError("zero case keys must be (parameter, seed) tuples")
        vector = np.asarray(values, dtype=np.float64).reshape(-1)
        if vector.size != len(names) or not np.all(np.isfinite(vector)):
            raise ValueError("zero case vectors must match observable_names and be finite")
        parsed[(str(key[0]), key[1])] = vector
    seeds = sorted({seed for _, seed in parsed}, key=lambda value: str(value))
    rows: list[dict[str, object]] = []
    reference_case_by_seed: dict[str, str | None] = {}
    finite_deltas: list[float] = []
    missing_references: list[str] = []
    for seed in seeds:
        reference_key = (str(reference_parameter), seed)
        reference = parsed.get(reference_key)
        reference_case_by_seed[str(seed)] = (
            f"{reference_parameter}/seed_{seed}" if reference is not None else None
        )
        if reference is None:
            missing_references.append(str(seed))
            continue
        for (parameter, case_seed), vector in sorted(parsed.items(), key=lambda item: str(item[0])):
            if case_seed != seed:
                continue
            delta = vector - reference
            max_abs = float(np.max(np.abs(delta))) if delta.size else 0.0
            finite_deltas.append(max_abs)
            rows.append({
                "parameter": parameter,
                "seed": seed,
                "reference_parameter": reference_parameter,
                "reference_case": f"{reference_parameter}/seed_{seed}",
                "max_abs_delta": max_abs,
                "l2_delta": float(np.linalg.norm(delta)),
                "delta": delta.tolist(),
            })
    max_abs_delta = max(finite_deltas, default=math.inf if missing_references else 0.0)
    status = "passed" if not missing_references and max_abs_delta <= float(tolerance) else "failed"
    return {
        "status": status,
        "tolerance": float(tolerance),
        "observable_names": names,
        "reference_parameter": str(reference_parameter),
        "reference_case_by_seed": reference_case_by_seed,
        "missing_references": missing_references,
        "cases": rows,
        "max_abs_delta": max_abs_delta,
        "meaning": "zero-error case delta against an explicit same-seed error-free reference",
    }


def classify_observability(
    sensitivity_matrix: Sequence[Sequence[float]],
    *,
    state_names: Sequence[str],
    observable_names: Sequence[str],
    zero_control: Sequence[float] | None = None,
    cosine_threshold: float = 0.98,
    significant_threshold: float = 1.0e-9,
) -> dict[str, object]:
    """Classify columns using unit-balanced rank, conditioning and confounding.

    Observable rows have different physical units (Hz, radians, dB, etc.).
    Therefore the identifiability matrix first L2-balances each observable row
    and then L2-normalizes state columns.  Raw sensitivities are retained for
    magnitude/sign reporting; the balanced matrix is used for rank, condition
    number and column cosine so a large-unit observable cannot dominate the
    confounding decision.
    """

    matrix = np.asarray(sensitivity_matrix, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape != (len(observable_names), len(state_names)):
        raise ValueError("sensitivity matrix shape does not match observable/state names")
    if not np.all(np.isfinite(matrix)):
        raise ValueError("sensitivity matrix contains non-finite values")
    if not 0.0 < float(cosine_threshold) <= 1.0:
        raise ValueError("cosine_threshold must be in (0, 1]")
    if float(significant_threshold) < 0.0 or not math.isfinite(float(significant_threshold)):
        raise ValueError("significant_threshold must be finite and non-negative")
    raw_column_scales = np.linalg.norm(matrix, axis=0)
    observable_row_scales = np.linalg.norm(matrix, axis=1)
    safe_row_scales = np.where(
        observable_row_scales > np.finfo(float).eps,
        observable_row_scales,
        1.0,
    )
    classification_matrix = matrix / safe_row_scales[:, None]
    classification_column_scales = np.linalg.norm(classification_matrix, axis=0)
    safe_classification_scales = np.where(
        classification_column_scales > np.finfo(float).eps,
        classification_column_scales,
        1.0,
    )
    scaled = classification_matrix / safe_classification_scales[None, :]
    singular_values = np.linalg.svd(scaled, compute_uv=False)
    rank_tolerance = np.finfo(float).eps * max(scaled.shape) * (float(singular_values[0]) if singular_values.size else 1.0)
    scaled_rank = int(np.sum(singular_values > rank_tolerance)) if singular_values.size else 0
    condition_number = (
        float(singular_values[0] / singular_values[-1])
        if singular_values.size and singular_values[-1] > rank_tolerance else math.inf
    )
    cosine = np.eye(len(state_names), dtype=np.float64)
    for left in range(len(state_names)):
        for right in range(left):
            denominator = float(
                np.linalg.norm(classification_matrix[:, left])
                * np.linalg.norm(classification_matrix[:, right])
            )
            value = (
                float(
                    np.dot(classification_matrix[:, left], classification_matrix[:, right])
                    / denominator
                )
                if denominator > 0.0
                else 0.0
            )
            cosine[left, right] = cosine[right, left] = value
    if zero_control is None:
        zero = np.zeros(len(observable_names), dtype=np.float64)
    else:
        zero = np.asarray(zero_control, dtype=np.float64).reshape(-1)
        if zero.size != len(observable_names) or not np.all(np.isfinite(zero)):
            raise ValueError("zero_control must match observable_names and be finite")

    status_by_state: dict[str, dict[str, object]] = {}
    for index, state in enumerate(state_names):
        column = matrix[:, index]
        norm = float(raw_column_scales[index])
        balanced_column = classification_matrix[:, index]
        normalized_norm = float(classification_column_scales[index])
        significant = int(
            np.count_nonzero(np.abs(balanced_column) > float(significant_threshold))
        )
        raw_significant = int(
            np.count_nonzero(np.abs(column) > float(significant_threshold))
        )
        confounded_with = [
            str(state_names[other])
            for other in range(len(state_names))
            if other != index and abs(float(cosine[index, other])) >= float(cosine_threshold)
        ]
        near_confounded_with = [
            str(state_names[other])
            for other in range(len(state_names))
            if other != index
            and NEAR_CONFOUNDING_THRESHOLD <= abs(float(cosine[index, other])) < float(cosine_threshold)
        ]
        if norm <= float(significant_threshold):
            status = "no measurable sensitivity"
            external_prior: list[str] = list(EXTERNAL_PRIORS)
        elif confounded_with:
            status = "not independently observable from current two-channel data"
            external_prior = list(EXTERNAL_PRIORS)
        else:
            status = "candidate independently observable"
            external_prior = []
        status_by_state[str(state)] = {
            "status": status,
            "sensitivity_norm": norm,
            "balanced_sensitivity_norm": normalized_norm,
            "significant_observable_count": significant,
            "raw_significant_observable_count": raw_significant,
            "confounded_with": confounded_with,
            "near_confounded_with": near_confounded_with,
            "zero_control_baseline_max_abs": float(np.max(np.abs(zero))) if zero.size else 0.0,
            "external_prior": external_prior if (confounded_with or near_confounded_with) else [],
        }
    pair_observability: list[dict[str, object]] = []
    for left in range(len(state_names)):
        for right in range(left):
            absolute_cosine = abs(float(cosine[left, right]))
            if absolute_cosine >= float(cosine_threshold):
                pair_status = "not independently observable from current two-channel data"
            elif absolute_cosine >= NEAR_CONFOUNDING_THRESHOLD:
                pair_status = "near-confounded; not independently observable from current two-channel data"
            else:
                pair_status = "separable"
            pair_observability.append({
                "state_a": str(state_names[right]),
                "state_b": str(state_names[left]),
                "cosine": float(cosine[left, right]),
                "absolute_cosine": absolute_cosine,
                "status": pair_status,
                "external_prior": list(EXTERNAL_PRIORS) if pair_status != "separable" else [],
            })
    return {
        "state_names": [str(value) for value in state_names],
        "observable_names": [str(value) for value in observable_names],
        "raw_matrix": matrix.tolist(),
        "classification_matrix": classification_matrix.tolist(),
        "scaled_matrix": scaled.tolist(),
        "observable_row_scales": safe_row_scales.tolist(),
        "column_scales": raw_column_scales.tolist(),
        "classification_column_scales": safe_classification_scales.tolist(),
        "scaled_rank": scaled_rank,
        "raw_rank": int(np.linalg.matrix_rank(matrix)),
        "condition_number": condition_number,
        "column_cosine": cosine.tolist(),
        "status_by_state": status_by_state,
        "pair_observability": pair_observability,
        "zero_control_baseline_max_abs": float(np.max(np.abs(zero))) if zero.size else 0.0,
        # Retain the old key as a compatibility alias.  It is a baseline
        # observable magnitude, not a claim that a sensitivity delta is zero.
        "zero_control_max_abs": float(np.max(np.abs(zero))) if zero.size else 0.0,
        "classification_rule": {
            "cosine_threshold": float(cosine_threshold),
            "near_confounding_threshold": NEAR_CONFOUNDING_THRESHOLD,
            "significant_threshold": float(significant_threshold),
            "row_balance": "observable-row L2 norm",
            "column_balance": "state-column L2 norm after row balance",
            "zero_control": "zero-error baseline observable, not a sensitivity delta",
            "pair_status": "near-confounded pairs carry the exact not-independently-observable phrase",
            "external_prior": list(EXTERNAL_PRIORS),
        },
    }


def _number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _base_error_free_config(template: Mapping[str, object]) -> dict[str, object]:
    config = copy.deepcopy(dict(template))
    impairment = config.setdefault("channel_impairments", {})
    if not isinstance(impairment, dict):
        raise ValueError("channel_impairments must be an object")
    for key in (
        "baseline_error_m", "channel_amp_mismatch_db", "channel_fixed_phase_mismatch_deg",
        "channel_phase_jitter_std_deg", "channel_range_shift_samples", "channel_time_delay_ns",
        "channel_noise_power_ratio_db", "iq_gain_imbalance_db", "iq_phase_imbalance_deg",
        "per_pulse_phase_drift_deg", "per_beam_phase_bias_deg", "sample_clock_error_ppm",
        "channel_drop_probability", "channel_saturation_level",
    ):
        impairment[key] = 0.0
    impairment["enabled"] = False

    geometry = config.setdefault("channel_geometry", {})
    if not isinstance(geometry, dict):
        raise ValueError("channel_geometry must be an object")
    for index in range(1, 5):
        item = geometry.get(f"channel_{index}")
        if not isinstance(item, dict):
            continue
        reported = item.get("reported", item)
        if not isinstance(reported, Mapping):
            raise ValueError(f"channel_{index}.reported must be an object")
        item["true"] = copy.deepcopy(dict(reported))

    platform_cfg = config.setdefault("platform", {})
    if not isinstance(platform_cfg, dict):
        raise ValueError("platform must be an object")
    reported_velocity = _number(
        platform_cfg.get("velocity_reported_mps", platform_cfg.get("speed_mps", 60.0)),
        60.0,
    )
    platform_cfg["speed_mps"] = reported_velocity
    platform_cfg["velocity_true_mps"] = reported_velocity
    platform_cfg["velocity_reported_mps"] = reported_velocity

    simulation_geometry = config.setdefault("simulation_geometry", {})
    if not isinstance(simulation_geometry, dict):
        raise ValueError("simulation_geometry must be an object")
    reported_heading = _number(simulation_geometry.get("platform_heading_deg", 0.0))
    simulation_geometry["platform_heading_source"] = "fixed_angle"
    simulation_geometry["platform_heading_deg"] = reported_heading
    servo = config.setdefault("servo_angle_error", {})
    if not isinstance(servo, dict):
        raise ValueError("servo_angle_error must be an object")
    servo.update({"enabled": False, "true_minus_reported_deg": 0.0})
    config["attitude"] = {
        "yaw_true_deg": reported_heading,
        "yaw_reported_deg": reported_heading,
        "yaw_true_minus_reported_deg": 0.0,
        "servo_true_minus_reported_deg": 0.0,
        "pitch_deg": 0.0,
        "roll_deg": 0.0,
        "joint_attitude_fit": False,
    }
    return config


def build_case_config(
    template: Mapping[str, object],
    parameter: str,
    value: float,
) -> dict[str, object]:
    """Create a zeroed base config with exactly one injected state error."""

    if parameter not in STATE_NAMES:
        raise ValueError(f"unsupported state parameter: {parameter}")
    if not math.isfinite(float(value)):
        raise ValueError("case perturbation must be finite")
    config = _base_error_free_config(template)
    impairment = config["channel_impairments"]
    platform_cfg = config["platform"]
    simulation_geometry = config["simulation_geometry"]
    servo = config["servo_angle_error"]
    scene = config.get("scene")
    range_processing = config.get("range_processing")
    if not isinstance(scene, dict) or not isinstance(range_processing, dict):
        raise ValueError("scene and range_processing must be objects")
    area = scene.get("area_clutter")
    if isinstance(area, Mapping) and str(area.get("model", "")) == "beam_center_clutter":
        calibration_range_m = _number(area.get("calibration_range_m"), 0.0)
        if calibration_range_m <= 0.0:
            raise ValueError("beam_center_clutter requires positive calibration_range_m")
        range_min_m = _number(scene.get("range_min_m"), 0.0)
        range_max_m = _number(scene.get("range_max_m"), 0.0)
        if not range_min_m < calibration_range_m < range_max_m:
            raise ValueError(
                "beam_center_clutter calibration_range_m must be inside scene range window"
            )
        # Compact raw-LFM cases retain the template's pulse duration but may
        # shorten the sampled fast-time buffer.  Center the configured
        # two-way slant delay in that buffer so the observable is physical
        # clutter/target response rather than thermal-noise-only data.
        range_processing["sample_delay_us"] = (
            2.0 * calibration_range_m / 299_792_458.0 * 1.0e6
        )
    targets = config.get("targets")
    if isinstance(targets, list):
        for target in targets:
            if isinstance(target, dict):
                target["enabled"] = False
    if parameter == "channel_delay_error":
        impairment["enabled"] = abs(float(value)) > 0.0
        impairment["channel_time_delay_ns"] = float(value)
    elif parameter == "inter_pulse_phase_error":
        impairment["enabled"] = abs(float(value)) > 0.0
        impairment["per_pulse_phase_drift_deg"] = float(value)
    elif parameter == "baseline_geometry_error":
        geometry = config["channel_geometry"]
        for channel_name in ("channel_2", "channel_4"):
            item = geometry.get(channel_name)
            if not isinstance(item, dict) or not isinstance(item.get("true"), dict):
                raise ValueError(f"missing true geometry for {channel_name}")
            item["true"]["x_m"] = _number(item["true"].get("x_m")) + float(value) * 1.0e-3
    elif parameter == "servo_angle_error":
        servo.update({"enabled": abs(float(value)) > 0.0, "true_minus_reported_deg": float(value)})
    elif parameter == "platform_velocity_error":
        reported = _number(platform_cfg.get("velocity_reported_mps"), 60.0)
        platform_cfg["velocity_true_mps"] = reported + float(value)
        platform_cfg["velocity_reported_mps"] = reported
        platform_cfg["speed_mps"] = reported
    elif parameter == "yaw_error":
        reported = _number(config["attitude"].get("yaw_reported_deg"), 0.0)
        simulation_geometry["platform_heading_deg"] = reported + float(value)
        config["attitude"]["yaw_true_deg"] = reported + float(value)
        config["attitude"]["yaw_true_minus_reported_deg"] = float(value)
    config["state_parameter"] = parameter
    config["state_value"] = float(value)
    return config


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.integer, int)):
        return int(value)
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_rows(path: Path, rows: Iterable[Mapping[str, object]], fields: Sequence[str] | None = None) -> None:
    materialized = [dict(row) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        resolved: list[str] = []
        for row in materialized:
            for key in row:
                if key not in resolved:
                    resolved.append(key)
        fields = resolved
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields), extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in materialized:
            writer.writerow(_json_safe(row))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _probe(command: Sequence[str]) -> dict[str, object]:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    return {
        "command": list(command),
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _git_identity() -> dict[str, object]:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False)
    return {
        "commit": commit.stdout.strip() if commit.returncode == 0 else None,
        "dirty": bool(status.stdout.strip()),
        "status_output": status.stdout.strip(),
    }


def _period_paths(simulation_root: Path) -> list[Path]:
    manifest_path = simulation_root / "data/period_files.csv"
    if not manifest_path.is_file():
        raise RuntimeError(f"missing period manifest: {manifest_path}")
    with manifest_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    paths: list[Path] = []
    for row in rows:
        raw = Path(str(row.get("file", "")))
        candidate = raw if raw.is_absolute() else simulation_root / raw
        if not candidate.is_file():
            raise RuntimeError(f"missing period raw file: {candidate}")
        paths.append(candidate.resolve())
    if not paths:
        raise RuntimeError(f"period manifest has no files: {manifest_path}")
    return paths


def load_f1_f2_protocol(
    paths: Sequence[Path],
    *,
    pulse_len: int,
    channel_count: int,
    iq_data_type: str,
    allow_two_channel_debug: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Read protocol packets and return F1, F2, header angles and counters.

    Phase-I scientific runs must enter through the four-channel protocol and
    production pair fusion.  A two-channel direct path is retained only for
    an explicit non-scientific debug caller; the CLI never enables it.
    """

    f1_rows: list[np.ndarray] = []
    f2_rows: list[np.ndarray] = []
    angles: list[float] = []
    counters: list[float] = []
    if channel_count == 2 and not allow_two_channel_debug:
        raise ValueError(
            "Phase-I scientific protocol input requires four channels; "
            "set allow_two_channel_debug=True only for an explicit debug caller"
        )
    if channel_count not in (2, 4):
        raise ValueError("protocol input requires four channels")
    for path in paths:
        for packet in iter_raw_packets(path, pulse_len, channel_count, iq_data_type):
            channels = np.asarray(packet["channels"])
            if channel_count == 4:
                first, second = fuse_protocol_channels_to_f1_f2(channels)
            else:
                first = channels[:, 0]
                second = channels[:, 1]
            f1_rows.append(np.asarray(first, dtype=np.complex128))
            f2_rows.append(np.asarray(second, dtype=np.complex128))
            angles.append(float(packet["theta_cmd_deg"]))
            counters.append(float(packet["prt_counter"]))
    if not f1_rows:
        raise ValueError("protocol input contains no packets")
    return np.stack(f1_rows), np.stack(f2_rows), np.asarray(angles), np.asarray(counters)


def _frequency_axis(config: Mapping[str, object], bins: int) -> np.ndarray:
    waveform = config.get("waveform")
    if not isinstance(waveform, Mapping):
        raise ValueError("waveform configuration is required")
    scene = config.get("scene", {})
    if isinstance(scene, Mapping) and str(scene.get("output_signal_domain", "")) == "raw_lfm":
        fs_hz = _number(waveform.get("fs_mhz"), 0.0) * 1.0e6
        if fs_hz <= 0.0:
            raise ValueError("waveform fs_mhz must be positive for raw_lfm")
        return np.fft.fftfreq(bins, d=1.0 / fs_hz)
    bandwidth_hz = _number(waveform.get("bandwidth_mhz"), 0.0) * 1.0e6
    tr_sec = _number(waveform.get("tr_us"), 0.0) * 1.0e-6
    if bandwidth_hz <= 0.0 or tr_sec <= 0.0:
        raise ValueError("waveform bandwidth_mhz and tr_us must be positive")
    return (np.arange(bins, dtype=np.float64) - (bins - 1.0) * 0.5) * (2.0 * bandwidth_hz / tr_sec)


def _run_case(
    *,
    template: Mapping[str, object],
    simulator: Path,
    output_root: Path,
    parameter: str,
    value: float,
    seed: int,
    period_count: int,
    compact: bool,
) -> dict[str, object]:
    sign = "zero" if value == 0.0 else ("plus" if value > 0.0 else "minus")
    case_root = output_root / "cases" / parameter / sign / f"seed_{seed:03d}"
    case_root.mkdir(parents=True, exist_ok=True)
    simulation_root = case_root / "simulation"
    config = build_case_config(template, parameter, value)
    config["case_id"] = f"two_channel_observability_{parameter}_{sign}_seed_{seed}"
    config["output_dir"] = str(simulation_root.resolve())
    random_cfg = config.setdefault("random", {})
    if not isinstance(random_cfg, dict):
        raise ValueError("random configuration is required")
    random_cfg.update({"period_start": 0, "period_count": int(period_count), "random_seed": int(seed)})
    if compact:
        waveform = config.get("waveform")
        processing = config.get("range_processing")
        if not isinstance(waveform, dict) or not isinstance(processing, dict):
            raise ValueError("waveform/range_processing are required")
        waveform["pulse_len"] = min(int(waveform.get("pulse_len", 2048)), 2048)
        waveform["range_fft_len"] = waveform["pulse_len"]
        processing["range_fft_len"] = waveform["pulse_len"]
        processing["range_crop_len"] = waveform["pulse_len"]
        waveform["pulse_num"] = min(int(waveform.get("pulse_num", 4)), 8)
        targets = config.get("targets")
        if isinstance(targets, list):
            for target in targets:
                if not isinstance(target, dict):
                    continue
                init = target.get("init")
                if isinstance(init, dict) and "expected_bin" in init:
                    init["expected_bin"] = min(
                        int(init["expected_bin"]), int(processing["range_crop_len"]) - 1
                    )
    scene = config.get("scene")
    if not isinstance(scene, dict):
        raise ValueError("scene configuration is required")
    # Stage2 rejects range-compressed output when raw target/scatterer
    # components are enabled.  The raw-LFM source is transformed to its
    # measured FFT axis in _frequency_axis after packet parsing.
    scene["output_signal_domain"] = "raw_lfm"
    config_path = case_root / "scenario.json"
    _write_json(config_path, config)
    log_path = case_root / "simulator.log"
    with log_path.open("w", encoding="utf-8") as log:
        command = [str(simulator), "--config", str(config_path)]
        log.write("$ " + " ".join(command) + "\n")
        completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
        log.write(f"exit_code={completed.returncode}\n")
    record: dict[str, object] = {
        "parameter": parameter,
        "value": value,
        "sign": sign,
        "seed": seed,
        "scenario": str(config_path),
        "scenario_sha256": _sha256(config_path),
        "simulator_log": str(log_path),
        "simulation_returncode": completed.returncode,
        "truth_not_used_by_observables": True,
        "raw_files": [],
        "raw_files_removed": False,
    }
    if completed.returncode != 0:
        record["status"] = "simulation_failed"
        _write_json(case_root / "case_manifest.json", record)
        return record

    waveform = config.get("waveform")
    if not isinstance(waveform, Mapping):
        raise ValueError("waveform configuration is required")
    paths = _period_paths(simulation_root)
    raw_records = []
    for path in paths:
        raw_records.append({"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)})
    first, second, angles, counters = load_f1_f2_protocol(
        paths,
        pulse_len=int(waveform["pulse_len"]),
        channel_count=int(waveform["new_protocol_channel_count"]),
        iq_data_type=str(waveform.get("iq_data_type", "float32")),
    )
    scene_cfg = config.get("scene")
    if isinstance(scene_cfg, Mapping) and str(scene_cfg.get("output_signal_domain", "")) == "raw_lfm":
        first = np.fft.fft(first, axis=1)
        second = np.fft.fft(second, axis=1)
    frequency = _frequency_axis(config, first.shape[1])
    observables = compute_f1_f2_observables(
        first,
        second,
        frequency_hz=frequency,
        pulse_index=np.arange(first.shape[0], dtype=np.float64),
        beam_angle_deg=angles,
        prf_hz=_number(waveform.get("prf_hz"), 1.0),
    )
    sample_delay_us = _number(
        (config.get("range_processing", {}) if isinstance(config.get("range_processing"), Mapping) else {}).get(
            "sample_delay_us"
        ),
        0.0,
    )
    fs_hz = _number(waveform.get("fs_mhz"), 0.0) * 1.0e6
    range_axis_m = (
        0.5 * 299_792_458.0
        * (sample_delay_us * 1.0e-6 + np.arange(first.shape[1], dtype=np.float64) / fs_hz)
        if fs_hz > 0.0 else np.full(first.shape[1], np.nan)
    )
    range_block_count = int(min(8, first.shape[1]))
    range_block_boundaries: list[dict[str, object]] = []
    for block in range(range_block_count):
        start = int(math.floor(block * first.shape[1] / range_block_count))
        end = int(math.floor((block + 1) * first.shape[1] / range_block_count)) - 1
        range_block_boundaries.append({
            "block": block,
            "start_bin": start,
            "end_bin": end,
            "range_min_m": float(range_axis_m[start]),
            "range_max_m": float(range_axis_m[end]),
            "frequency_min_hz": float(np.min(frequency[start:end + 1])),
            "frequency_max_hz": float(np.max(frequency[start:end + 1])),
        })
    record.update({
        "status": "passed",
        "raw_files": raw_records,
        "packet_count": int(first.shape[0]),
        "theta_values_deg": sorted(set(float(value) for value in angles)),
        "prt_counter_first": float(counters[0]),
        "prt_counter_last": float(counters[-1]),
        "pulse_count": int(first.shape[0]),
        "range_bin_count": int(first.shape[1]),
        "range_block_count": range_block_count,
        "range_axis_kind": "fast_time_slant_range_m",
        "range_axis_min_m": float(np.min(range_axis_m)),
        "range_axis_max_m": float(np.max(range_axis_m)),
        "frequency_axis_kind": "measured_fft_frequency_hz",
        "frequency_axis_min_hz": float(np.min(frequency)),
        "frequency_axis_max_hz": float(np.max(frequency)),
        "range_block_boundaries": range_block_boundaries,
        "angle_count": int(len(set(float(value) for value in angles))),
        "angle_min_deg": float(np.min(angles)),
        "angle_max_deg": float(np.max(angles)),
        "angle_span_deg": float(np.ptp(angles)),
        "observables": observables,
        "scientific_input": "F1=(C1+C3)/2, F2=(C2+C4)/2",
        "protocol_channel_count": int(waveform["new_protocol_channel_count"]),
    })
    _write_json(case_root / "observables.json", observables)
    # Keep hashes and compact observables; raw data are not a second result source.
    for path in paths:
        path.unlink()
    record["raw_files_removed"] = True
    _write_json(case_root / "case_manifest.json", record)
    return record


def _write_observability_report(
    path: Path,
    *,
    manifest: Mapping[str, object],
    classification: Mapping[str, object],
) -> None:
    lines = [
        "# 双通道系统误差可观测性审计",
        "",
        "本报告只把四通道协议先融合为 F1=(C1+C3)/2、F2=(C2+C4)/2 后的观测用于科学结论。",
        "原始六 pair 只可用于调试，不能把它们的额外空间信息计入本矩阵。",
        "",
        f"- 状态向量：`{', '.join(STATE_NAMES)}`",
        f"- 观测数：{len(OBSERVABLE_NAMES)}；条件数：`{classification.get('condition_number')}`",
        f"- scaled rank：`{classification.get('scaled_rank')}` / {len(STATE_NAMES)}",
        f"- ai_training：`{manifest.get('ai_training')}`；router_enabled：`{manifest.get('router_enabled')}`",
        "",
        "## 判读",
        "",
        "判别矩阵先按观测行 L2 平衡、再按状态列 L2 归一化；原始灵敏度用于物理量级。"
        "zero control 是零误差基线观测，不是灵敏度差分；符号、range/angle 覆盖和 seed 稳定性"
        "必须与列余弦一起阅读。",
    ]
    status_by_state = classification.get("status_by_state", {})
    if isinstance(status_by_state, Mapping):
        for state in STATE_NAMES:
            row = status_by_state.get(state, {})
            if not isinstance(row, Mapping):
                continue
            lines.append(
                f"- `{state}`：{row.get('status')}；显著观测 {row.get('significant_observable_count')}；"
                f"confounded_with={row.get('confounded_with')}；"
                f"near_confounded_with={row.get('near_confounded_with')}"
            )
            if (
                row.get("status") == "not independently observable from current two-channel data"
                or row.get("near_confounded_with")
            ):
                lines.append(
                    "  - 需要外部先验确认：INS、servo encoder、factory calibration 或 temporal prior。"
                )
    pairs = classification.get("pair_observability", [])
    if isinstance(pairs, Sequence):
        lines.extend(["", "## Pair-level observability"])
        for pair in pairs:
            if isinstance(pair, Mapping):
                lines.append(
                    f"- `{pair.get('state_a')}` vs `{pair.get('state_b')}`："
                    f"cosine={pair.get('cosine')}；status={pair.get('status')}"
                )
    lines.extend([
        "",
        "## 信息条件和限制",
        "",
        "A0 Ideal、A1 Current + unknown error、A2 Known-error correction upper bound、A3 Blind estimated correction 的 CSI/CFAR/Track 结果必须另在对应端到端产物中报告；本审计矩阵只回答局部观测是否对状态变化有独立响应。",
        "A2 的 truth 不能进入 blind estimator；如果校正没有真实施加，端到端分支必须标记 `NOT_EVALUABLE`。",
        "",
        "原始 case manifest、range_angle_coverage.csv、sensitivity_by_seed.csv 和"
        "sensitivity_stability.csv 是本报告的证据来源。",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_list(raw_values: Sequence[str], *, integer: bool = False) -> list[float | int]:
    values: list[float | int] = []
    for raw in raw_values:
        for item in str(raw).split(","):
            if not item.strip():
                continue
            values.append(int(item) if integer else float(item))
    if not values:
        raise ValueError("list argument cannot be empty")
    return values


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--simulator", type=Path, default=DEFAULT_SIMULATOR)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--seeds", nargs="+", default=["101"])
    parser.add_argument("--parameters", nargs="+", choices=STATE_NAMES, default=list(STATE_NAMES))
    parser.add_argument("--period-count", type=int, default=1)
    parser.add_argument("--compact", action="store_true", help="use the compact raw-IQ pilot layout")
    parser.add_argument("--delay-step-ns", type=float, default=DEFAULT_PERTURATIONS["channel_delay_error"])
    parser.add_argument("--phase-step-deg-per-pulse", type=float, default=DEFAULT_PERTURATIONS["inter_pulse_phase_error"])
    parser.add_argument("--geometry-step-mm", type=float, default=DEFAULT_PERTURATIONS["baseline_geometry_error"])
    parser.add_argument("--servo-step-deg", type=float, default=DEFAULT_PERTURATIONS["servo_angle_error"])
    parser.add_argument("--velocity-step-mps", type=float, default=DEFAULT_PERTURATIONS["platform_velocity_error"])
    parser.add_argument("--yaw-step-deg", type=float, default=DEFAULT_PERTURATIONS["yaw_error"])
    args = parser.parse_args(argv)
    template_path = args.template.resolve()
    simulator = args.simulator.resolve()
    output_root = args.output_root.resolve()
    if not template_path.is_file():
        raise SystemExit(f"missing template: {template_path}")
    if not simulator.is_file():
        raise SystemExit(f"missing simulator: {simulator}")
    if args.period_count <= 0:
        raise SystemExit("--period-count must be positive")
    if output_root.exists() and any(output_root.iterdir()):
        raise SystemExit(f"refuse to overwrite non-empty output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    seeds = [int(value) for value in _parse_list(args.seeds, integer=True)]
    steps = {
        "channel_delay_error": float(args.delay_step_ns),
        "inter_pulse_phase_error": float(args.phase_step_deg_per_pulse),
        "baseline_geometry_error": float(args.geometry_step_mm),
        "servo_angle_error": float(args.servo_step_deg),
        "platform_velocity_error": float(args.velocity_step_mps),
        "yaw_error": float(args.yaw_step_deg),
    }
    for name in args.parameters:
        if not math.isfinite(steps[name]) or steps[name] <= 0.0:
            raise SystemExit(f"perturbation for {name} must be finite and positive")
    template = json.loads(template_path.read_text(encoding="utf-8"))
    if not isinstance(template, Mapping):
        raise SystemExit("template must be a JSON object")
    manifest: dict[str, object] = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(),
        "output_root": str(output_root),
        "template": str(template_path),
        "template_sha256": _sha256(template_path),
        "simulator": str(simulator),
        "parameters": list(args.parameters),
        "seeds": seeds,
        "period_count": args.period_count,
        "compact": bool(args.compact),
        "perturbations": steps,
        "state_names": list(STATE_NAMES),
        "observable_names": list(OBSERVABLE_NAMES),
        "observable_metadata": copy.deepcopy(OBSERVABLE_METADATA),
        "scientific_input": "F1=(C1+C3)/2, F2=(C2+C4)/2",
        "ai_training": False,
        "router_enabled": False,
        "platform": platform.platform(),
        "python": sys.version,
        "git": _git_identity(),
        "gpu_probe": _probe(["nvidia-smi"]),
        "cases": [],
    }
    _write_json(output_root / "manifest.json", manifest)
    _write_json(output_root / "observable_metadata.json", OBSERVABLE_METADATA)

    records: list[dict[str, object]] = []
    for parameter in args.parameters:
        for value, sign in ((0.0, "zero"), (steps[parameter], "plus"), (-steps[parameter], "minus")):
            for seed in seeds:
                record = _run_case(
                    template=template,
                    simulator=simulator,
                    output_root=output_root,
                    parameter=parameter,
                    value=value,
                    seed=seed,
                    period_count=args.period_count,
                    compact=bool(args.compact),
                )
                # Keep a human-readable sign even if a caller changes value formatting.
                record["sign"] = sign
                records.append(record)
                manifest["cases"] = records
                _write_json(output_root / "manifest.json", manifest)

    observable_rows: list[dict[str, object]] = []
    by_key: dict[tuple[str, int, str], Mapping[str, object]] = {}
    for record in records:
        if record.get("status") != "passed":
            continue
        observables = record.get("observables")
        if not isinstance(observables, Mapping):
            continue
        key = (str(record["parameter"]), int(record["seed"]), str(record["sign"]))
        by_key[key] = observables
        for name in OBSERVABLE_NAMES:
            observable_rows.append({
                "parameter": record["parameter"],
                "seed": record["seed"],
                "sign": record["sign"],
                "perturbation_value": record["value"],
                "observable": name,
                "value": observables.get(name),
                "scientific_input": record.get("scientific_input"),
                **OBSERVABLE_METADATA[name],
            })
    _write_rows(
        output_root / "observables.csv",
        observable_rows,
        fields=(
            "parameter", "seed", "sign", "perturbation_value", "observable", "value",
            "scientific_input", "source_id", "axis_group", "range_group", "angle_group",
            "pulse_group",
        ),
    )

    raw_matrix = np.full((len(OBSERVABLE_NAMES), len(args.parameters)), np.nan, dtype=np.float64)
    sensitivity_rows: list[dict[str, object]] = []
    missing_sensitivity: list[str] = []
    for col, parameter in enumerate(args.parameters):
        per_seed: list[np.ndarray] = []
        for seed in seeds:
            plus = by_key.get((parameter, seed, "plus"))
            minus = by_key.get((parameter, seed, "minus"))
            if plus is None or minus is None:
                missing_sensitivity.append(f"{parameter}/seed_{seed}")
                continue
            delta = central_difference_sensitivity(plus, minus, perturbation=steps[parameter])
            vector = np.asarray([
                float(delta[name]) if delta.get(name) is not None and math.isfinite(float(delta[name])) else np.nan
                for name in OBSERVABLE_NAMES
            ], dtype=np.float64)
            if np.all(np.isfinite(vector)):
                per_seed.append(vector)
            for row_name, value in zip(OBSERVABLE_NAMES, vector):
                sensitivity_rows.append({
                    "parameter": parameter,
                    "seed": seed,
                    "observable": row_name,
                    "sensitivity": value,
                    "perturbation_unit": {
                        "channel_delay_error": "ns",
                        "inter_pulse_phase_error": "deg_per_pulse",
                        "baseline_geometry_error": "mm",
                        "servo_angle_error": "deg",
                        "platform_velocity_error": "mps",
                        "yaw_error": "deg",
                    }[parameter],
                })
        if per_seed:
            raw_matrix[:, col] = np.median(np.vstack(per_seed), axis=0)
    _write_rows(
        output_root / "sensitivity_by_seed.csv",
        sensitivity_rows,
        fields=("parameter", "seed", "observable", "sensitivity", "perturbation_unit"),
    )
    stability_rows = summarize_seed_stability(sensitivity_rows)
    _write_rows(
        output_root / "sensitivity_stability.csv",
        stability_rows,
        fields=(
            "parameter", "observable", "seed_count", "seed_ids", "finite_seed_count",
            "finite_seed_ids", "median_sensitivity", "min_sensitivity", "max_sensitivity",
            "sign_consistent", "relative_spread",
        ),
    )
    coverage_rows = [
        {
            "parameter": record.get("parameter"),
            "sign": record.get("sign"),
            "seed": record.get("seed"),
            "value": record.get("value"),
            "status": record.get("status"),
            "packet_count": record.get("packet_count"),
            "pulse_count": record.get("pulse_count"),
            "range_bin_count": record.get("range_bin_count"),
            "range_block_count": record.get("range_block_count"),
            "range_axis_kind": record.get("range_axis_kind"),
            "range_axis_min_m": record.get("range_axis_min_m"),
            "range_axis_max_m": record.get("range_axis_max_m"),
            "frequency_axis_kind": record.get("frequency_axis_kind"),
            "frequency_axis_min_hz": record.get("frequency_axis_min_hz"),
            "frequency_axis_max_hz": record.get("frequency_axis_max_hz"),
            "range_block_boundaries_json": json.dumps(
                record.get("range_block_boundaries", []),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "angle_count": record.get("angle_count"),
            "angle_min_deg": record.get("angle_min_deg"),
            "angle_max_deg": record.get("angle_max_deg"),
            "angle_span_deg": record.get("angle_span_deg"),
        }
        for record in records
    ]
    _write_rows(
        output_root / "range_angle_coverage.csv",
        coverage_rows,
        fields=(
            "parameter", "sign", "seed", "value", "status", "packet_count", "pulse_count",
            "range_bin_count", "range_block_count", "angle_count", "angle_min_deg",
            "angle_max_deg", "angle_span_deg", "range_axis_kind", "range_axis_min_m",
            "range_axis_max_m", "frequency_axis_kind", "frequency_axis_min_hz",
            "frequency_axis_max_hz", "range_block_boundaries_json",
        ),
    )
    _write_rows(
        output_root / "sensitivity_matrix_raw.csv",
        [
            {"observable": name, **{parameter: raw_matrix[row, col] for col, parameter in enumerate(args.parameters)}}
            for row, name in enumerate(OBSERVABLE_NAMES)
        ],
        fields=("observable", *args.parameters),
    )

    finite_matrix = np.nan_to_num(raw_matrix, nan=0.0)
    zero_cases: dict[tuple[str, int], np.ndarray] = {}
    for parameter in args.parameters:
        for seed in seeds:
            observables = by_key.get((parameter, seed, "zero"))
            if observables is None:
                continue
            zero_cases[(parameter, seed)] = np.asarray(
                [float(observables[name]) for name in OBSERVABLE_NAMES],
                dtype=np.float64,
            )
    zero_vectors = list(zero_cases.values())
    zero_control = np.median(np.vstack(zero_vectors), axis=0) if zero_vectors else np.zeros(len(OBSERVABLE_NAMES))
    zero_control_delta = summarize_zero_control_delta(
        zero_cases,
        observable_names=OBSERVABLE_NAMES,
        reference_parameter=str(args.parameters[0]),
        tolerance=ZERO_CONTROL_DELTA_TOLERANCE,
    )
    classification = classify_observability(
        finite_matrix,
        state_names=args.parameters,
        observable_names=OBSERVABLE_NAMES,
        zero_control=zero_control,
    )
    classification["zero_control_delta"] = zero_control_delta
    _write_json(output_root / "observability_summary.json", classification)
    _write_rows(
        output_root / "classification.csv",
        [
            {"state": state, **dict(values)}
            for state, values in classification["status_by_state"].items()
        ],
    )
    _write_rows(
        output_root / "pair_observability.csv",
        classification["pair_observability"],
        fields=(
            "state_a", "state_b", "cosine", "absolute_cosine", "status",
            "external_prior",
        ),
    )
    _write_rows(
        output_root / "sensitivity_matrix_scaled.csv",
        [
            {
                "observable": name,
                **{
                    parameter: classification["scaled_matrix"][row][col]
                    for col, parameter in enumerate(args.parameters)
                },
            }
            for row, name in enumerate(OBSERVABLE_NAMES)
        ],
        fields=("observable", *args.parameters),
    )
    _write_json(
        output_root / "zero_control.json",
        {
            "observable_names": list(OBSERVABLE_NAMES),
            "values": zero_control,
            "zero_control_delta": zero_control_delta,
            "meaning": "zero-error baseline observable plus same-seed error-free reference deltas",
        },
    )
    _write_observability_report(output_root / "observability_report.md", manifest=manifest, classification=classification)
    manifest.update({
        "case_count": len(records),
        "passed_case_count": sum(record.get("status") == "passed" for record in records),
        "failed_cases": [record for record in records if record.get("status") != "passed"],
        "missing_sensitivity": missing_sensitivity,
        "raw_matrix_path": str(output_root / "sensitivity_matrix_raw.csv"),
        "scaled_matrix_path": str(output_root / "sensitivity_matrix_scaled.csv"),
        "stability_path": str(output_root / "sensitivity_stability.csv"),
        "observable_metadata_path": str(output_root / "observable_metadata.json"),
        "range_angle_coverage_path": str(output_root / "range_angle_coverage.csv"),
        "classification_path": str(output_root / "classification.csv"),
        "pair_observability_path": str(output_root / "pair_observability.csv"),
        "zero_control_path": str(output_root / "zero_control.json"),
        "zero_control_delta_status": zero_control_delta["status"],
        "zero_control_delta_max_abs": zero_control_delta["max_abs_delta"],
        "zero_control_delta_tolerance": zero_control_delta["tolerance"],
        "scaled_rank": classification["scaled_rank"],
        "raw_rank": classification["raw_rank"],
        "condition_number": classification["condition_number"],
        "classification_matrix": "observable-row L2 balance followed by state-column L2 normalization",
        "status": (
            "completed"
            if not missing_sensitivity
            and not any(record.get("status") != "passed" for record in records)
            and zero_control_delta["status"] == "passed"
            else "completed_with_gaps"
        ),
    })
    _write_json(output_root / "manifest.json", manifest)
    print(json.dumps({
        "status": manifest["status"],
        "output_root": str(output_root),
        "case_count": len(records),
        "scaled_rank": classification["scaled_rank"],
        "condition_number": classification["condition_number"],
    }, ensure_ascii=False))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
