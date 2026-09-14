#!/usr/bin/env python3
"""Read four-channel protocol IQ and form auditable pair observables.

The module deliberately stays deterministic and model-based.  It does not
train a network or select an algorithm.  Its purpose is to expose the six
pairwise cross-channel observables needed by the unknown-system-error pilot
and to fit a simple angle-dependent phase model when the data support it.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

import numpy as np


C = 299_792_458.0
HEADER_BYTES = 256
# Kept only for backwards-compatible unit-level calls.  File analysis and
# research runners must pass the reported geometry parsed from their config;
# the analyzer must not silently invent the array geometry.
DEFAULT_CHANNEL_X_M = (-0.085, 0.085, -0.085, 0.085)
PAIR_DEFINITIONS: Tuple[Tuple[str, int, int], ...] = (
    ("C13", 0, 2),
    ("C24", 1, 3),
    ("C12", 0, 1),
    ("C14", 0, 3),
    ("C23", 1, 2),
    ("C34", 2, 3),
)
HORIZONTAL_PAIRS = ("C12", "C14", "C23", "C34")


def _wrap_phase(value: float) -> float:
    """Return a phase in [-pi, pi]."""

    return math.atan2(math.sin(value), math.cos(value))


def _phase_or_none(value: complex) -> Optional[float]:
    if abs(value) == 0.0 or not (math.isfinite(value.real) and math.isfinite(value.imag)):
        return None
    return float(math.atan2(value.imag, value.real))


def _validate_channel_positions(
    channel_positions_m: Optional[Sequence[Sequence[float]]] = None,
    channel_x_m: Optional[Sequence[float]] = None,
) -> np.ndarray:
    if channel_positions_m is not None:
        positions = np.asarray(channel_positions_m, dtype=np.float64)
        if positions.shape != (4, 3) or not np.all(np.isfinite(positions)):
            raise ValueError("channel_positions_m must contain four finite [x,y,z] values")
        return positions
    if channel_x_m is None:
        channel_x_m = DEFAULT_CHANNEL_X_M
    x = np.asarray(channel_x_m, dtype=np.float64)
    if x.shape != (4,) or not np.all(np.isfinite(x)):
        raise ValueError("channel_x_m must contain four finite values")
    return np.column_stack((x, np.zeros(4), np.zeros(4)))


def load_channel_positions(
    config_path: Path | str,
    source: str = "reported",
) -> np.ndarray:
    """Load reported or true channel positions from a Stage2 JSON config.

    The true positions are an evaluation/simulation field.  Blind estimator
    callers must request ``source="reported"`` and pass only that result.
    """

    if source not in {"reported", "true"}:
        raise ValueError("source must be reported or true")
    with Path(config_path).open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    geometry = config.get("channel_geometry", {})
    if source == "reported":
        positions = geometry.get("reported_channel_positions")
        if positions is None:
            positions = [
                geometry.get(f"channel_{index}", {})
                for index in range(1, 5)
            ]
    else:
        positions = geometry.get("true_channel_positions")
        if positions is None:
            positions = [
                geometry.get(f"channel_{index}", {}).get("true", {})
                for index in range(1, 5)
            ]
    if len(positions) != 4:
        raise ValueError(f"{source} channel geometry must contain four positions")
    parsed = []
    for index, item in enumerate(positions, start=1):
        if isinstance(item, Mapping):
            if "x_m" not in item and source in {"reported", "true"}:
                nested_key = "reported" if source == "reported" else "true"
                item = item.get(nested_key, item)
            x = item.get("x_m")
            y = item.get("y_m")
            z = item.get("z_m")
        else:
            if len(item) != 3:
                raise ValueError(f"channel {index} position must contain three values")
            x, y, z = item
        parsed.append([x, y, z])
    return _validate_channel_positions(parsed)


def compute_pair_observables(
    channels: np.ndarray,
    channel_x_m: Sequence[float] = DEFAULT_CHANNEL_X_M,
    channel_positions_m: Optional[Sequence[Sequence[float]]] = None,
) -> Dict[str, object]:
    """Compute C13, C24, C12, C14, C23 and C34 for one PRT.

    Cross-correlation uses ``channel_i * conj(channel_j)``.  The additional
    ``right_minus_left_phase_rad`` field puts horizontal pairs into one
    geometric sign convention, independent of the pair's storage order.
    """

    samples = np.asarray(channels)
    if samples.ndim != 2 or samples.shape[1] != 4:
        raise ValueError("channels must have shape (sample_count, 4)")
    if not np.iscomplexobj(samples):
        samples = samples.astype(np.complex128)
    positions = _validate_channel_positions(channel_positions_m, channel_x_m)
    x = positions[:, 0]

    pairs: Dict[str, Dict[str, object]] = {}
    for name, i, j in PAIR_DEFINITIONS:
        a = samples[:, i]
        b = samples[:, j]
        valid = (
            np.isfinite(a.real)
            & np.isfinite(a.imag)
            & np.isfinite(b.real)
            & np.isfinite(b.imag)
        )
        a_valid = a[valid]
        b_valid = b[valid]
        cross = np.sum(a_valid * np.conj(b_valid), dtype=np.complex128)
        power_a = float(np.sum(np.abs(a_valid) ** 2, dtype=np.float64))
        power_b = float(np.sum(np.abs(b_valid) ** 2, dtype=np.float64))
        denominator = math.sqrt(power_a * power_b)
        coherence: Optional[float]
        if denominator > 0.0 and math.isfinite(denominator):
            coherence = float(np.clip(abs(cross) / denominator, 0.0, 1.0))
        else:
            coherence = None
        phase = _phase_or_none(complex(cross))
        if phase is None:
            right_minus_left = None
        elif x[i] > x[j]:
            right_minus_left = _wrap_phase(phase)
        elif x[i] < x[j]:
            right_minus_left = _wrap_phase(-phase)
        else:
            right_minus_left = None
        pairs[name] = {
            "channel_i": i + 1,
            "channel_j": j + 1,
            "effective_sample_count": int(np.count_nonzero(valid)),
            "cross_real": float(cross.real),
            "cross_imag": float(cross.imag),
            "cross_magnitude": float(abs(cross)),
            "coherence": coherence,
            "phase_rad": phase,
            "right_minus_left_phase_rad": right_minus_left,
            "x_delta_m": float(x[j] - x[i]),
            "position_i_m": [float(value) for value in positions[i]],
            "position_j_m": [float(value) for value in positions[j]],
            "position_delta_m": [
                float(value) for value in (positions[j] - positions[i])
            ],
        }

    c13 = pairs["C13"]["phase_rad"]
    c12 = pairs["C12"]["phase_rad"]
    c23 = pairs["C23"]["phase_rad"]
    if c13 is None or c12 is None or c23 is None:
        closure = None
    else:
        closure = _wrap_phase(float(c12) + float(c23) - float(c13))
    return {
        "pairs": pairs,
        "closure_phase_C12_C23_minus_C13_rad": closure,
    }


def fit_angle_phase_model(
    theta_deg: Sequence[float],
    phase_rad: Sequence[float],
    weights: Optional[Sequence[float]] = None,
) -> Dict[str, object]:
    """Fit ``phase = slope * sin(theta) + intercept`` with phase unwrapping."""

    theta = np.asarray(theta_deg, dtype=np.float64).reshape(-1)
    phase = np.asarray(phase_rad, dtype=np.float64).reshape(-1)
    if theta.shape != phase.shape:
        raise ValueError("theta_deg and phase_rad must have the same length")
    if weights is None:
        weight = np.ones(theta.shape, dtype=np.float64)
    else:
        weight = np.asarray(weights, dtype=np.float64).reshape(-1)
        if weight.shape != theta.shape:
            raise ValueError("weights must have the same length as theta_deg")
    valid = np.isfinite(theta) & np.isfinite(phase) & np.isfinite(weight) & (weight > 0.0)
    theta = theta[valid]
    phase = phase[valid]
    weight = weight[valid]
    result: Dict[str, object] = {
        "fit_status": "underdetermined",
        "n_beams": int(theta.size),
        "slope_rad_per_sin_theta": None,
        "intercept_rad": None,
        "rmse_rad": None,
        "r2": None,
        "confidence": {"status": "not_estimable"},
    }
    if theta.size < 2:
        return result
    x = np.sin(np.deg2rad(theta))
    if np.unique(np.round(x, decimals=12)).size < 2:
        return result
    order = np.argsort(x, kind="mergesort")
    x_sorted = x[order]
    y_sorted = np.unwrap(phase[order])
    w_sorted = weight[order]
    design = np.column_stack((x_sorted, np.ones(x_sorted.shape, dtype=np.float64)))
    sqrt_w = np.sqrt(w_sorted)
    weighted_design = design * sqrt_w[:, None]
    weighted_y = y_sorted * sqrt_w
    if np.linalg.matrix_rank(weighted_design) < 2:
        return result
    coefficients, _, _, _ = np.linalg.lstsq(weighted_design, weighted_y, rcond=None)
    predicted = design @ coefficients
    residual = y_sorted - predicted
    weight_sum = float(np.sum(w_sorted))
    rmse = math.sqrt(float(np.sum(w_sorted * residual * residual)) / weight_sum)
    centered = y_sorted - float(np.sum(w_sorted * y_sorted) / weight_sum)
    total = float(np.sum(w_sorted * centered * centered))
    r2 = None if total == 0.0 else 1.0 - float(np.sum(w_sorted * residual * residual)) / total
    result.update(
        {
            "fit_status": "fit",
            "slope_rad_per_sin_theta": float(coefficients[0]),
            "intercept_rad": _wrap_phase(float(coefficients[1])),
            "rmse_rad": float(rmse),
            "r2": None if r2 is None else float(r2),
            "confidence": {
                "status": "quality_fields_only",
                "n_beams": int(theta.size),
                "weight_sum": weight_sum,
                "rmse_rad": float(rmse),
                "r2": None if r2 is None else float(r2),
                "not_a_calibrated_probability": True,
            },
        }
    )
    return result


def estimate_baseline_error(
    fit: Mapping[str, object],
    fc_hz: float,
    nominal_baseline_m: float = 0.0,
) -> Optional[float]:
    """Convert a fitted phase slope to a baseline error in metres."""

    slope = fit.get("slope_rad_per_sin_theta")
    if fit.get("fit_status") != "fit" or slope is None or not math.isfinite(float(slope)):
        return None
    if not math.isfinite(fc_hz) or fc_hz <= 0.0:
        raise ValueError("fc_hz must be positive and finite")
    return float(float(slope) * C / (2.0 * math.pi * fc_hz) - nominal_baseline_m)


def _iq_dtype(iq_data_type: str) -> np.dtype:
    normalized = iq_data_type.lower()
    if normalized in {"int16", "iq_int16", "short", "s16"}:
        return np.dtype("<i2")
    if normalized in {"float32", "float", "f32"}:
        return np.dtype("<f4")
    raise ValueError(f"unsupported iq_data_type: {iq_data_type}")


def iter_raw_packets(
    path: Path | str,
    pulse_len: int,
    channel_count: int,
    iq_data_type: str,
) -> Iterator[Dict[str, object]]:
    """Yield validated packets from a stage2 new-protocol binary file."""

    path = Path(path)
    if pulse_len <= 0 or channel_count <= 0:
        raise ValueError("pulse_len and channel_count must be positive")
    dtype = _iq_dtype(iq_data_type)
    scalar_bytes = dtype.itemsize
    packet_bytes = HEADER_BYTES + pulse_len * channel_count * 2 * scalar_bytes
    with path.open("rb") as stream:
        packet_index = 0
        while True:
            header = stream.read(HEADER_BYTES)
            if not header:
                break
            if len(header) != HEADER_BYTES:
                raise ValueError(f"truncated protocol header at packet {packet_index}")
            declared_bytes = struct.unpack_from("<I", header, 9)[0]
            if declared_bytes != packet_bytes:
                raise ValueError(
                    f"packet {packet_index} declares {declared_bytes} bytes; "
                    f"expected {packet_bytes} for the supplied layout"
                )
            payload = stream.read(packet_bytes - HEADER_BYTES)
            if len(payload) != packet_bytes - HEADER_BYTES:
                raise ValueError(f"truncated protocol payload at packet {packet_index}")
            values = np.frombuffer(payload, dtype=dtype)
            expected_values = pulse_len * channel_count * 2
            if values.size != expected_values:
                raise ValueError(f"invalid payload scalar count at packet {packet_index}")
            iq = values.reshape(pulse_len, channel_count, 2)
            channels = iq[:, :, 0].astype(np.float64) + 1j * iq[:, :, 1].astype(np.float64)
            if not np.all(np.isfinite(channels)):
                raise ValueError(f"non-finite IQ value at packet {packet_index}")
            theta_cmd_deg = struct.unpack_from("<h", header, 218)[0] / 100.0
            prt_counter = struct.unpack_from("<I", header, 20)[0]
            yield {
                "packet_index": packet_index,
                "prt_counter": int(prt_counter),
                "theta_cmd_deg": float(theta_cmd_deg),
                "channels": channels,
            }
            packet_index += 1


def _local_look_vector(theta_deg: float, metadata: Mapping[str, object]) -> np.ndarray:
    """Return the nominal horizontal look vector in the local x/y frame."""

    geometry = metadata.get("simulation_geometry", {})
    platform = metadata.get("platform", {})
    speed = float(platform.get("speed_mps", metadata.get("platform_speed_mps", 60.0)))
    heading = platform.get("heading_deg")
    if heading is None:
        heading = geometry.get("platform_heading_deg", 90.0)
    heading_deg = float(heading)
    if geometry.get("platform_heading_source", "fixed_angle") == "velocity":
        # The pilot uses local x=north and a positive x velocity.  Preserve an
        # explicit heading when supplied; otherwise use the nominal +x track.
        heading_deg = float(geometry.get("platform_heading_deg", heading_deg))
        if not math.isfinite(heading_deg) and speed > 0.0:
            heading_deg = 90.0
    squint_side = int(
        platform.get("squint_side", geometry.get("squint_side", 1))
    )
    theta_offset = float(geometry.get("beam_theta_offset_deg", 0.0))
    side_dir = -90.0 if squint_side == 1 else 90.0
    beam_center_dir = side_dir - (theta_deg + theta_offset)
    target_azimuth = heading_deg - beam_center_dir
    east = math.cos(math.radians(target_azimuth))
    north = math.sin(math.radians(target_azimuth))
    local_x_axis = str(geometry.get("local_x_axis", "north"))
    local_y_axis = str(geometry.get("local_y_axis", "east"))

    def component(axis: str) -> Tuple[float, float]:
        if axis in {"east", "track_right"}:
            return 1.0, 0.0
        if axis in {"west", "track_left"}:
            return -1.0, 0.0
        if axis in {"north", "track_forward"}:
            return 0.0, 1.0
        if axis in {"south", "track_backward"}:
            return 0.0, -1.0
        raise ValueError(f"unsupported local axis: {axis}")

    xe, xn = component(local_x_axis)
    ye, yn = component(local_y_axis)
    return np.array([xe * east + xn * north, ye * east + yn * north], dtype=np.float64)


def _nominal_los_unit(
    theta_deg: float,
    range_sample: float,
    metadata: Mapping[str, object],
) -> np.ndarray:
    waveform = metadata.get("waveform", {})
    platform = metadata.get("platform", {})
    geometry = metadata.get("simulation_geometry", {})
    if "fs_hz" in waveform:
        fs_hz = float(waveform["fs_hz"])
    elif "fs_mhz" in waveform:
        fs_hz = float(waveform["fs_mhz"]) * 1.0e6
    else:
        fs_hz = float(metadata.get("fs_hz", 60.0))
    if "sample_delay_sec" in waveform:
        sample_delay_sec = float(waveform["sample_delay_sec"])
    elif "sample_delay_us" in metadata.get("range_processing", {}):
        sample_delay_sec = float(
            metadata["range_processing"]["sample_delay_us"]
        ) * 1.0e-6
    else:
        sample_delay_sec = float(metadata.get("sample_delay_us", 0.0)) * 1.0e-6
    slant_range = 0.5 * C * (float(range_sample) / fs_hz + sample_delay_sec)
    if not math.isfinite(slant_range) or slant_range <= 0.0:
        return np.full(3, np.nan, dtype=np.float64)
    height = float(platform.get("height_m", metadata.get("platform_height_m", 6000.0)))
    ground_z = float(metadata.get("scene", {}).get("ground_z_m", 0.0))
    ground_range = slant_range
    range_geometry = str(
        geometry.get("range_geometry", metadata.get("range_geometry", "algorithm"))
    )
    use_ground = bool(
        geometry.get("use_ground_range_for_position", True)
    )
    if use_ground and range_geometry != "slant":
        ground_range = math.sqrt(
            max(0.0, slant_range * slant_range - (height - ground_z) ** 2)
        )
    horizontal = _local_look_vector(theta_deg, metadata)
    los = np.array(
        [
            horizontal[0] * ground_range / slant_range,
            horizontal[1] * ground_range / slant_range,
            (ground_z - height) / slant_range,
        ],
        dtype=np.float64,
    )
    norm = float(np.linalg.norm(los))
    if norm <= 0.0 or not math.isfinite(norm):
        return np.full(3, np.nan, dtype=np.float64)
    return los / norm


def _block_pair_observable(
    channels: np.ndarray,
    start: int,
    end: int,
    i: int,
    j: int,
) -> Optional[Dict[str, float]]:
    a = channels[start:end, i]
    b = channels[start:end, j]
    valid = (
        np.isfinite(a.real)
        & np.isfinite(a.imag)
        & np.isfinite(b.real)
        & np.isfinite(b.imag)
    )
    if not np.any(valid):
        return None
    a = a[valid]
    b = b[valid]
    cross = np.sum(a * np.conj(b), dtype=np.complex128)
    power_i = float(np.sum(np.abs(a) ** 2, dtype=np.float64))
    power_j = float(np.sum(np.abs(b) ** 2, dtype=np.float64))
    denominator = math.sqrt(power_i * power_j)
    phase = _phase_or_none(complex(cross))
    if phase is None or denominator <= 0.0:
        return None
    coherence = float(np.clip(abs(cross) / denominator, 0.0, 1.0))
    return {
        "phase_rad": float(phase),
        "coherence": coherence,
        "weight": float(max(1.0e-12, coherence * denominator)),
        "effective_sample_count": float(np.count_nonzero(valid)),
    }


def _circular_pair_fit(
    observations: Sequence[Mapping[str, float]],
    max_abs_delta_m: float,
    pair_name: str,
    grid_count: int = 801,
) -> Dict[str, object]:
    if not observations:
        return {
            "fit_status": "fallback_unidentifiable",
            "pair": pair_name,
            "observation_count": 0,
            "estimated_delta_d_m": None,
            "intercept_rad": None,
            "resultant_length": None,
            "rmse_rad": None,
        }
    residual = np.asarray([float(row["residual_rad"]) for row in observations])
    projection = np.asarray([float(row["projection_rad_per_m"]) for row in observations])
    weight = np.asarray([float(row["weight"]) for row in observations])
    valid = np.isfinite(residual) & np.isfinite(projection) & np.isfinite(weight) & (weight > 0.0)
    residual = residual[valid]
    projection = projection[valid]
    weight = weight[valid]
    if residual.size < 3:
        return {
            "fit_status": "fallback_unidentifiable",
            "pair": pair_name,
            "observation_count": int(residual.size),
            "estimated_delta_d_m": None,
            "intercept_rad": None,
            "resultant_length": None,
            "rmse_rad": None,
        }

    if np.ptp(projection) <= 1.0e-9:
        z = np.sum(weight * np.exp(1j * residual))
        total_weight = float(np.sum(weight))
        intercept = _wrap_phase(float(np.angle(z)))
        residual_after = np.asarray(
            [_wrap_phase(float(value) - intercept) for value in residual],
            dtype=np.float64,
        )
        rmse = math.sqrt(
            float(np.sum(weight * residual_after ** 2)) /
            max(1.0e-12, total_weight)
        )
        return {
            "fit_status": "control_fit",
            "pair": pair_name,
            "observation_count": int(residual.size),
            "inlier_count": int(residual.size),
            "inlier_fraction": 1.0,
            "estimated_delta_d_m": None,
            "intercept_rad": intercept,
            "resultant_length": float(abs(z) / max(1.0e-12, total_weight)),
            "rmse_rad": float(rmse),
            "weight_sum": total_weight,
            "confidence": {
                "status": "control_pair_quality",
                "resultant_length": float(abs(z) / max(1.0e-12, total_weight)),
                "rmse_rad": float(rmse),
                "not_a_calibrated_probability": True,
            },
        }

    def score(delta: float, select: Optional[np.ndarray] = None) -> Tuple[float, float, float]:
        if select is None:
            r = residual
            p = projection
            w = weight
        else:
            r = residual[select]
            p = projection[select]
            w = weight[select]
        z = np.sum(w * np.exp(1j * (r - delta * p)))
        total = float(np.sum(w))
        if total <= 0.0:
            return -math.inf, 0.0, 0.0
        resultant = float(abs(z) / total)
        intercept = _wrap_phase(float(np.angle(z)))
        return resultant, intercept, total

    coarse = np.linspace(-max_abs_delta_m, max_abs_delta_m, max(3, grid_count))
    coarse_scores = [score(float(delta))[0] for delta in coarse]
    best_index = int(np.argmax(coarse_scores))
    best_delta = float(coarse[best_index])
    step = float(coarse[1] - coarse[0])
    refine = np.linspace(
        max(-max_abs_delta_m, best_delta - 2.0 * step),
        min(max_abs_delta_m, best_delta + 2.0 * step),
        41,
    )
    refine_scores = [score(float(delta))[0] for delta in refine]
    best_delta = float(refine[int(np.argmax(refine_scores))])
    resultant, intercept, total_weight = score(best_delta)
    residual_after = np.asarray(
        [_wrap_phase(float(r) - best_delta * float(p) - intercept)
         for r, p in zip(residual, projection)],
        dtype=np.float64,
    )
    abs_residual = np.abs(residual_after)
    median = float(np.median(abs_residual))
    threshold = max(0.35, min(1.2, 2.5 * max(median, 1.0e-6)))
    inliers = abs_residual <= threshold
    fit_residual = residual
    fit_weight = weight
    if int(np.count_nonzero(inliers)) >= 3 and int(np.count_nonzero(inliers)) < residual.size:
        refined_scores = [score(float(delta), inliers)[0] for delta in refine]
        best_delta = float(refine[int(np.argmax(refined_scores))])
        resultant, intercept, total_weight = score(best_delta, inliers)
        fit_residual = residual[inliers]
        fit_weight = weight[inliers]
        residual_after = np.asarray(
            [_wrap_phase(float(r) - best_delta * float(p) - intercept)
             for r, p in zip(fit_residual, projection[inliers])],
            dtype=np.float64,
        )
        abs_residual = np.abs(residual_after)
        inlier_count = int(np.count_nonzero(inliers))
    else:
        inlier_count = int(residual.size)
    rmse = math.sqrt(
        float(np.sum(fit_weight * residual_after ** 2)) /
        max(1.0e-12, float(np.sum(fit_weight)))
    )
    return {
        "fit_status": "fit",
        "pair": pair_name,
        "observation_count": int(residual.size),
        "inlier_count": inlier_count,
        "inlier_fraction": float(inlier_count / max(1, residual.size)),
        "estimated_delta_d_m": best_delta,
        "intercept_rad": intercept,
        "resultant_length": resultant,
        "rmse_rad": float(rmse),
        "weight_sum": total_weight,
        "confidence": {
            "status": "quality_fields_only",
            "resultant_length": resultant,
            "rmse_rad": float(rmse),
            "inlier_fraction": float(inlier_count / max(1, residual.size)),
            "not_a_calibrated_probability": True,
        },
    }


def _joint_circular_fit(
    observations_by_pair: Mapping[str, Sequence[Mapping[str, float]]],
    max_abs_delta_m: float,
    grid_count: int = 801,
) -> Dict[str, object]:
    """Fit one delta with an independent constant intercept per pair."""

    arrays = {}
    for pair_name, rows in observations_by_pair.items():
        residual = np.asarray([float(row["residual_rad"]) for row in rows])
        projection = np.asarray([float(row["projection_rad_per_m"]) for row in rows])
        weight = np.asarray([float(row["weight"]) for row in rows])
        valid = np.isfinite(residual) & np.isfinite(projection) & np.isfinite(weight) & (weight > 0.0)
        if np.count_nonzero(valid) < 3:
            continue
        arrays[pair_name] = (residual[valid], projection[valid], weight[valid])
    if not arrays:
        return {
            "fit_status": "fallback_unidentifiable",
            "estimated_delta_d_m": None,
            "pair_intercepts_rad": {},
            "rmse_rad": None,
            "resultant_length": None,
        }

    def score(delta: float, masks: Optional[Mapping[str, np.ndarray]] = None):
        total_score = 0.0
        total_weight = 0.0
        intercepts = {}
        for pair_name, (residual, projection, weight) in arrays.items():
            mask = None if masks is None else masks.get(pair_name)
            r = residual if mask is None else residual[mask]
            p = projection if mask is None else projection[mask]
            w = weight if mask is None else weight[mask]
            if r.size == 0:
                continue
            z = np.sum(w * np.exp(1j * (r - delta * p)))
            pair_weight = float(np.sum(w))
            total_score += float(abs(z))
            total_weight += pair_weight
            intercepts[pair_name] = _wrap_phase(float(np.angle(z)))
        return total_score / max(1.0e-12, total_weight), intercepts, total_weight

    coarse = np.linspace(-max_abs_delta_m, max_abs_delta_m, max(3, grid_count))
    coarse_scores = [score(float(delta))[0] for delta in coarse]
    best_delta = float(coarse[int(np.argmax(coarse_scores))])
    step = float(coarse[1] - coarse[0])
    refine = np.linspace(
        max(-max_abs_delta_m, best_delta - 2.0 * step),
        min(max_abs_delta_m, best_delta + 2.0 * step),
        41,
    )
    refine_scores = [score(float(delta))[0] for delta in refine]
    best_delta = float(refine[int(np.argmax(refine_scores))])
    resultant, intercepts, total_weight = score(best_delta)
    masks = {}
    inlier_count = 0
    total_count = 0
    fit_residuals = []
    fit_weights = []
    for pair_name, (residual, projection, weight) in arrays.items():
        intercept = float(intercepts[pair_name])
        after = np.asarray(
            [_wrap_phase(float(r) - best_delta * float(p) - intercept)
             for r, p in zip(residual, projection)],
            dtype=np.float64,
        )
        threshold = max(0.35, min(1.2, 2.5 * max(float(np.median(np.abs(after))), 1.0e-6)))
        mask = np.abs(after) <= threshold
        if int(np.count_nonzero(mask)) >= 3:
            masks[pair_name] = mask
            inlier_count += int(np.count_nonzero(mask))
            fit_residuals.extend(after[mask].tolist())
            fit_weights.extend(weight[mask].tolist())
        else:
            masks[pair_name] = np.ones(after.shape, dtype=bool)
            inlier_count += int(after.size)
            fit_residuals.extend(after.tolist())
            fit_weights.extend(weight.tolist())
        total_count += int(after.size)
    robust_resultant, robust_intercepts, robust_weight = score(best_delta, masks)
    # Recompute residuals with the robust per-pair intercepts.
    fit_residuals = []
    fit_weights = []
    for pair_name, (residual, projection, weight) in arrays.items():
        intercept = float(robust_intercepts[pair_name])
        mask = masks[pair_name]
        after = np.asarray(
            [_wrap_phase(float(r) - best_delta * float(p) - intercept)
             for r, p in zip(residual[mask], projection[mask])],
            dtype=np.float64,
        )
        fit_residuals.extend(after.tolist())
        fit_weights.extend(weight[mask].tolist())
    rmse = math.sqrt(
        float(np.sum(np.asarray(fit_weights) * np.asarray(fit_residuals) ** 2)) /
        max(1.0e-12, float(np.sum(fit_weights)))
    )
    return {
        "fit_status": "fit",
        "estimated_delta_d_m": best_delta,
        "pair_intercepts_rad": robust_intercepts,
        "resultant_length": robust_resultant,
        "rmse_rad": float(rmse),
        "weight_sum": robust_weight,
        "observation_count": total_count,
        "inlier_count": inlier_count,
        "inlier_fraction": float(inlier_count / max(1, total_count)),
        "confidence": {
            "status": "quality_fields_only",
            "resultant_length": robust_resultant,
            "rmse_rad": float(rmse),
            "inlier_fraction": float(inlier_count / max(1, total_count)),
            "not_a_calibrated_probability": True,
        },
    }


def estimate_unknown_baseline(
    input_path: Path | str,
    pulse_len: int,
    channel_count: int,
    iq_data_type: str,
    fc_hz: float,
    nominal_channel_positions_m: Sequence[Sequence[float]],
    metadata: Mapping[str, object],
    pulses_per_beam: int = 1,
    pair_mode: str = "six_pair",
    single_pair: str = "C12",
    range_block_size: int = 32,
    range_start: int = 0,
    range_end: Optional[int] = None,
    max_abs_delta_m: float = 0.02,
    min_coherence: float = 0.0,
) -> Dict[str, object]:
    """Estimate a right-vs-left along-track offset from one unknown-only file.

    This function accepts exactly one echo file.  It never loads ideal data,
    target truth, known parameters, or future-period metrics.  The nominal
    phase model is computed from reported geometry plus packet theta/range
    metadata; a per-pair constant intercept absorbs fixed electronic phase.
    """

    if pair_mode not in {"six_pair", "single_pair"}:
        raise ValueError("pair_mode must be six_pair or single_pair")
    if single_pair not in {item[0] for item in PAIR_DEFINITIONS}:
        raise ValueError(f"unsupported single_pair: {single_pair}")
    if range_block_size <= 0:
        raise ValueError("range_block_size must be positive")
    if not math.isfinite(min_coherence) or min_coherence < 0.0 or min_coherence > 1.0:
        raise ValueError("min_coherence must be in [0,1]")
    positions = _validate_channel_positions(nominal_channel_positions_m)
    if not math.isfinite(fc_hz) or fc_hz <= 0.0:
        raise ValueError("fc_hz must be positive and finite")
    wavelength = C / fc_hz
    carrier_phase_sign = float(metadata.get("carrier_phase_sign", -1.0))
    right_indicator = np.array([0.0, 1.0, 0.0, 1.0], dtype=np.float64)
    selected_pairs = (
        tuple(item[0] for item in PAIR_DEFINITIONS)
        if pair_mode == "six_pair"
        else (single_pair,)
    )
    observations: Dict[str, List[Dict[str, float]]] = {
        name: [] for name in selected_pairs
    }
    closure_values: List[float] = []
    closure_observation_count = 0
    packet_count = 0
    for packet in iter_raw_packets(input_path, pulse_len, channel_count, iq_data_type):
        packet_count += 1
        channels = packet["channels"]
        lo = max(0, int(range_start))
        hi = channels.shape[0] if range_end is None else min(channels.shape[0], int(range_end))
        if hi <= lo:
            continue
        beam_index = int(packet["packet_index"]) // max(1, pulses_per_beam)
        theta = float(packet["theta_cmd_deg"])
        for start in range(lo, hi, range_block_size):
            end = min(hi, start + range_block_size)
            block_obs: Dict[str, float] = {}
            block_coherence: Dict[str, float] = {}
            for pair_name, i, j in PAIR_DEFINITIONS:
                if pair_name not in observations:
                    continue
                pair = _block_pair_observable(channels, start, end, i, j)
                if pair is None:
                    continue
                if float(pair["coherence"]) < min_coherence:
                    continue
                range_sample = 0.5 * (start + end - 1)
                los = _nominal_los_unit(theta, range_sample, metadata)
                if not np.all(np.isfinite(los)):
                    continue
                pair_delta = positions[j] - positions[i]
                nominal_phase = carrier_phase_sign * 2.0 * math.pi * float(
                    np.dot(los, pair_delta)
                ) / wavelength
                right_pair_sign = float(right_indicator[j] - right_indicator[i])
                projection = carrier_phase_sign * 2.0 * math.pi * float(
                    los[0] * right_pair_sign
                ) / wavelength
                residual = _wrap_phase(float(pair["phase_rad"]) - nominal_phase)
                row = {
                    "packet_index": float(packet["packet_index"]),
                    "beam_index": float(beam_index),
                    "block_start": float(start),
                    "block_end": float(end),
                    "theta_cmd_deg": theta,
                    "range_sample": float(range_sample),
                    "observed_phase_rad": float(pair["phase_rad"]),
                    "nominal_phase_rad": float(_wrap_phase(nominal_phase)),
                    "residual_rad": residual,
                    "projection_rad_per_m": projection,
                    "coherence": float(pair["coherence"]),
                    "weight": float(pair["weight"]),
                }
                observations[pair_name].append(row)
                block_obs[pair_name] = float(pair["phase_rad"])
                block_coherence[pair_name] = float(pair["coherence"])
            closure_pairs = ("C12", "C23", "C13")
            if (
                set(closure_pairs).issubset(block_obs)
                and all(block_coherence[name] >= min_coherence for name in closure_pairs)
            ):
                closure_values.append(
                    _wrap_phase(
                        block_obs["C12"] + block_obs["C23"] - block_obs["C13"]
                    )
                )
                closure_observation_count += 1

    pair_fits = {
        name: _circular_pair_fit(rows, max_abs_delta_m, name)
        for name, rows in observations.items()
    }
    informative = [
        fit for fit in pair_fits.values()
        if fit.get("fit_status") == "fit" and fit.get("estimated_delta_d_m") is not None
    ]
    all_pair_present = all(
        len(observations.get(name, [])) >= 3 for name in selected_pairs
    )
    if not informative:
        return {
            "schema": "unknown_only_baseline_estimator_v1",
            "fit_status": "fallback_unidentifiable",
            "operational_blind": True,
            "input_path": str(input_path),
            "packet_count": packet_count,
            "pair_mode": pair_mode,
            "single_pair": single_pair,
            "estimated_baseline_error_m": None,
            "estimated_delta_phi_lr_rad": None,
            "pair_fits": pair_fits,
            "closure_phase_residual_rad": None
            if not closure_values else float(np.mean(np.abs(closure_values))),
            "closure_observation_count": closure_observation_count,
            "cross_pair_consistency": {"status": "not_estimable"},
        }
    deltas = np.asarray([float(fit["estimated_delta_d_m"]) for fit in informative])
    if pair_mode == "six_pair" and not all_pair_present:
        status = "fallback_unidentifiable"
    else:
        status = "fit"
    estimate = float(np.median(deltas))
    spread = float(np.max(deltas) - np.min(deltas)) if deltas.size else None
    global_fit = _joint_circular_fit(
        {name: observations[name] for name in selected_pairs}, max_abs_delta_m
    )
    if pair_mode == "six_pair" and global_fit.get("fit_status") != "fit":
        status = "fallback_unidentifiable"
    intercepts = {
        name: fit.get("intercept_rad") for name, fit in pair_fits.items()
    }
    return {
        "schema": "unknown_only_baseline_estimator_v1",
        "fit_status": status,
        "operational_blind": True,
        "input_path": str(input_path),
        "packet_count": packet_count,
        "pair_mode": pair_mode,
        "single_pair": single_pair,
        "nominal_channel_positions_m": [
            [float(value) for value in position] for position in positions
        ],
        "estimated_baseline_error_m": estimate if status == "fit" else None,
        "estimated_delta_phi_lr_rad": (
            None
            if intercepts.get("C12") is None
            else _wrap_phase(-float(intercepts["C12"]))
        ),
        "pair_intercepts_rad": intercepts,
        "pair_fits": pair_fits,
        "global_fit": global_fit,
        "cross_pair_consistency": {
            "status": "quality_fields_only" if spread is not None else "not_estimable",
            "pair_count": int(deltas.size),
            "median_delta_d_m": estimate,
            "max_minus_min_delta_d_m": spread,
            "not_a_calibrated_probability": True,
        },
        "closure_phase_residual_rad": None
        if not closure_values else float(np.mean(np.abs(closure_values))),
        "closure_phase_p95_rad": None
        if not closure_values else float(np.percentile(np.abs(closure_values), 95.0)),
        "closure_observation_count": closure_observation_count,
        "observation_counts": {
            name: len(rows) for name, rows in observations.items()
        },
        "model": {
            "equation": "phi_obs - phi_nominal = projection*delta_d + delta_phi_pair",
            "projection": "carrier_sign*2*pi/lambda*(right_j-right_i)*los_x",
            "range_blocks": range_block_size,
            "uses_truth": False,
            "uses_ideal_reference": False,
        },
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _beam_rows_from_accumulator(
    accumulator: Mapping[Tuple[int, str], Dict[str, object]],
    channel_x_m: Sequence[float] = DEFAULT_CHANNEL_X_M,
    channel_positions_m: Optional[Sequence[Sequence[float]]] = None,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    positions = _validate_channel_positions(channel_positions_m, channel_x_m)
    x = positions[:, 0]
    for (beam_index, pair_name), value in sorted(accumulator.items()):
        cross = complex(value["cross"])
        phase = _phase_or_none(cross)
        denominator = value.get("denominator")
        if denominator is None:
            denominator = math.sqrt(
                max(0.0, float(value.get("power_i", 0.0)))
                * max(0.0, float(value.get("power_j", 0.0)))
            )
        _, i, j = next(item for item in PAIR_DEFINITIONS if item[0] == pair_name)
        if phase is None:
            right_minus_left = None
        elif x[i] > x[j]:
            right_minus_left = _wrap_phase(phase)
        elif x[i] < x[j]:
            right_minus_left = _wrap_phase(-phase)
        else:
            right_minus_left = None
        rows.append(
            {
                "beam_index": beam_index,
                "theta_cmd_deg": float(value["theta_cmd_deg"]),
                "pair": pair_name,
                "effective_sample_count": int(value["sample_count"]),
                "cross_real": float(cross.real),
                "cross_imag": float(cross.imag),
                "cross_magnitude": float(abs(cross)),
                "phase_rad": phase,
                "right_minus_left_phase_rad": right_minus_left,
                "coherence": (
                    None
                    if float(denominator) <= 0.0
                    else float(np.clip(abs(cross) / float(denominator), 0.0, 1.0))
                ),
            }
        )
    return rows


def analyze_file(
    path: Path | str,
    pulse_len: int,
    channel_count: int,
    iq_data_type: str,
    fc_hz: float,
    pulses_per_beam: int = 1,
    channel_x_m: Sequence[float] = DEFAULT_CHANNEL_X_M,
    output_dir: Optional[Path | str] = None,
    channel_positions_m: Optional[Sequence[Sequence[float]]] = None,
) -> Dict[str, object]:
    """Analyze one raw file and optionally write packet/beam CSV artifacts."""

    path = Path(path)
    if channel_count != 4:
        raise ValueError("four-channel observables require channel_count=4")
    if pulses_per_beam <= 0:
        raise ValueError("pulses_per_beam must be positive")
    positions = _validate_channel_positions(channel_positions_m, channel_x_m)
    packet_rows: List[Dict[str, object]] = []
    accumulator: Dict[Tuple[int, str], Dict[str, object]] = {}
    packet_count = 0
    sample_count = 0
    for packet in iter_raw_packets(path, pulse_len, channel_count, iq_data_type):
        packet_index = int(packet["packet_index"])
        beam_index = packet_index // pulses_per_beam
        pulse_index = packet_index % pulses_per_beam
        observable = compute_pair_observables(
            packet["channels"], channel_positions_m=positions
        )
        packet_row: Dict[str, object] = {
            "packet_index": packet_index,
            "beam_index": beam_index,
            "pulse_index": pulse_index,
            "prt_counter": int(packet["prt_counter"]),
            "theta_cmd_deg": float(packet["theta_cmd_deg"]),
            "closure_phase_C12_C23_minus_C13_rad": observable[
                "closure_phase_C12_C23_minus_C13_rad"
            ],
        }
        sample_count += int(packet["channels"].shape[0])
        for pair_name, pair in observable["pairs"].items():
            for field in (
                "effective_sample_count",
                "cross_real",
                "cross_imag",
                "cross_magnitude",
                "coherence",
                "phase_rad",
                "right_minus_left_phase_rad",
            ):
                packet_row[f"{pair_name}_{field}"] = pair[field]
            key = (beam_index, pair_name)
            current = accumulator.setdefault(
                key,
                {
                    "theta_cmd_deg": float(packet["theta_cmd_deg"]),
                    "sample_count": 0,
                    "cross": 0.0 + 0.0j,
                    "power_i": 0.0,
                    "power_j": 0.0,
                },
            )
            current["sample_count"] = int(current["sample_count"]) + int(
                pair["effective_sample_count"]
            )
            current["cross"] = complex(current["cross"]) + complex(
                float(pair["cross_real"]), float(pair["cross_imag"])
            )
            channels = packet["channels"]
            _, i, j = next(item for item in PAIR_DEFINITIONS if item[0] == pair_name)
            valid = np.isfinite(channels[:, i].real) & np.isfinite(channels[:, i].imag)
            valid &= np.isfinite(channels[:, j].real) & np.isfinite(channels[:, j].imag)
            current["power_i"] = float(current["power_i"]) + float(
                np.sum(np.abs(channels[valid, i]) ** 2, dtype=np.float64)
            )
            current["power_j"] = float(current["power_j"]) + float(
                np.sum(np.abs(channels[valid, j]) ** 2, dtype=np.float64)
            )
        packet_rows.append(packet_row)
        packet_count += 1

    for value in accumulator.values():
        value["denominator"] = math.sqrt(
            max(0.0, float(value["power_i"]))
            * max(0.0, float(value["power_j"]))
        )
    beam_rows = _beam_rows_from_accumulator(
        accumulator, channel_positions_m=positions
    )
    pair_summary: Dict[str, Dict[str, object]] = {}
    for pair_name in (item[0] for item in PAIR_DEFINITIONS):
        selected = [row for row in packet_rows if row[f"{pair_name}_coherence"] is not None]
        coherences = [float(row[f"{pair_name}_coherence"]) for row in selected]
        phases = [
            float(row[f"{pair_name}_right_minus_left_phase_rad"])
            for row in selected
            if row[f"{pair_name}_right_minus_left_phase_rad"] is not None
        ]
        pair_summary[pair_name] = {
            "packet_count_with_power": len(selected),
            "mean_coherence": None if not coherences else float(np.mean(coherences)),
            "mean_abs_phase_rad": None if not phases else float(np.mean(np.abs(phases))),
        }

    fit_by_pair: Dict[str, Dict[str, object]] = {}
    for pair_name, i, j in PAIR_DEFINITIONS:
        selected = [
            row
            for row in beam_rows
            if row["pair"] == pair_name
            and (
                row["right_minus_left_phase_rad"] is not None
                if positions[i, 0] != positions[j, 0]
                else row["phase_rad"] is not None
            )
        ]
        phase_field = (
            "right_minus_left_phase_rad"
            if positions[i, 0] != positions[j, 0]
            else "phase_rad"
        )
        fit_by_pair[pair_name] = fit_angle_phase_model(
            [row["theta_cmd_deg"] for row in selected],
            [row[phase_field] for row in selected],
            [max(1.0, float(row["effective_sample_count"])) for row in selected],
        )

    summary: Dict[str, object] = {
        "schema": "four_channel_observables_v1",
        "input": {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "pulse_len": pulse_len,
            "channel_count": channel_count,
            "iq_data_type": iq_data_type,
            "fc_hz": fc_hz,
            "pulses_per_beam": pulses_per_beam,
            "channel_x_m": [float(value) for value in positions[:, 0]],
            "channel_positions_m": [
                [float(value) for value in position] for position in positions
            ],
        },
        "packet_count": packet_count,
        "sample_count_total": sample_count,
        "beam_count": 0 if not beam_rows else max(int(row["beam_index"]) for row in beam_rows) + 1,
        "pairs": pair_summary,
        "angle_phase_fit_by_pair": fit_by_pair,
        "beam_rows": beam_rows,
    }
    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        _write_csv(out / "pair_observables.csv", packet_rows)
        _write_csv(out / "beam_observables.csv", beam_rows)
        with (out / "observable_summary.json").open("w", encoding="utf-8") as stream:
            json.dump(summary, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
    summary.pop("beam_rows", None)
    return summary


def paired_phase_difference(
    reference_path: Path | str,
    compared_path: Path | str,
    pulse_len: int,
    channel_count: int,
    iq_data_type: str,
    pulses_per_beam: int,
    channel_x_m: Sequence[float] = DEFAULT_CHANNEL_X_M,
) -> Dict[str, object]:
    """Compare matched raw files using all horizontal pair observables."""

    reference = iter_raw_packets(reference_path, pulse_len, channel_count, iq_data_type)
    compared = iter_raw_packets(compared_path, pulse_len, channel_count, iq_data_type)
    accum: Dict[Tuple[int, str], Dict[str, object]] = {}
    reference_count = 0
    for reference_packet, compared_packet in zip(reference, compared):
        if reference_packet["prt_counter"] != compared_packet["prt_counter"]:
            raise ValueError("paired files have different PRT counters")
        if abs(float(reference_packet["theta_cmd_deg"]) - float(compared_packet["theta_cmd_deg"])) > 1.0e-6:
            raise ValueError("paired files have different beam angles")
        beam_index = int(reference_packet["packet_index"]) // pulses_per_beam
        reference_obs = compute_pair_observables(reference_packet["channels"], channel_x_m)
        compared_obs = compute_pair_observables(compared_packet["channels"], channel_x_m)
        for pair_name in HORIZONTAL_PAIRS:
            ref_pair = reference_obs["pairs"][pair_name]
            cur_pair = compared_obs["pairs"][pair_name]
            ref_phase = ref_pair["right_minus_left_phase_rad"]
            cur_phase = cur_pair["right_minus_left_phase_rad"]
            if ref_phase is None or cur_phase is None:
                continue
            weight = float(
                min(
                    int(ref_pair["effective_sample_count"]),
                    int(cur_pair["effective_sample_count"]),
                )
            )
            ref_coh = ref_pair["coherence"]
            cur_coh = cur_pair["coherence"]
            if ref_coh is not None and cur_coh is not None:
                weight *= max(0.0, min(float(ref_coh), float(cur_coh)))
            if weight <= 0.0:
                continue
            delta = _wrap_phase(float(cur_phase) - float(ref_phase))
            key = (beam_index, pair_name)
            current = accum.setdefault(
                key,
                {
                    "theta_cmd_deg": float(reference_packet["theta_cmd_deg"]),
                    "z": 0.0 + 0.0j,
                    "weight": 0.0,
                },
            )
            current["z"] = complex(current["z"]) + weight * complex(
                math.cos(delta), math.sin(delta)
            )
            current["weight"] = float(current["weight"]) + weight
        reference_count += 1
    try:
        next(reference)
        raise ValueError("paired files have different packet counts")
    except StopIteration:
        pass
    try:
        next(compared)
        raise ValueError("paired files have different packet counts")
    except StopIteration:
        pass

    rows: List[Dict[str, object]] = []
    for (beam_index, pair_name), value in sorted(accum.items()):
        z = complex(value["z"])
        weight = float(value["weight"])
        rows.append(
            {
                "beam_index": beam_index,
                "theta_cmd_deg": float(value["theta_cmd_deg"]),
                "pair": pair_name,
                "phase_error_rad": _phase_or_none(z),
                "resultant_length": None if weight <= 0.0 else float(abs(z) / weight),
                "weight": weight,
            }
        )
    fit_by_pair: Dict[str, Dict[str, object]] = {}
    for pair_name in HORIZONTAL_PAIRS:
        selected = [
            row
            for row in rows
            if row["pair"] == pair_name and row["phase_error_rad"] is not None
        ]
        fit_by_pair[pair_name] = fit_angle_phase_model(
            [row["theta_cmd_deg"] for row in selected],
            [row["phase_error_rad"] for row in selected],
            [row["weight"] for row in selected],
        )
    selected_phase = [
        abs(float(row["phase_error_rad"]))
        for row in rows
        if row["phase_error_rad"] is not None
    ]
    return {
        "schema": "paired_four_channel_phase_difference_v1",
        "reference_path": str(reference_path),
        "compared_path": str(compared_path),
        "packet_count": reference_count,
        "rows": rows,
        "fit_by_pair": fit_by_pair,
        "mean_abs_phase_error_rad": None
        if not selected_phase
        else float(np.mean(selected_phase)),
    }


def _write_csv(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fieldnames: List[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _load_cli_scenario(path: Optional[Path]) -> Dict[str, object]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pulse-len", type=int)
    parser.add_argument("--channel-count", type=int, default=4)
    parser.add_argument("--iq-data-type")
    parser.add_argument("--fc-hz", type=float)
    parser.add_argument("--pulses-per-beam", type=int)
    args = parser.parse_args(argv)
    scenario = _load_cli_scenario(args.scenario)
    waveform = scenario.get("waveform", {})
    pulse_len = args.pulse_len or int(waveform.get("pulse_len", 0))
    channel_count = args.channel_count or int(waveform.get("new_protocol_channel_count", 4))
    iq_data_type = args.iq_data_type or str(waveform.get("iq_data_type", "float32"))
    fc_hz = args.fc_hz or float(waveform.get("fc_ghz", 16.0)) * 1.0e9
    pulses_per_beam = args.pulses_per_beam or int(waveform.get("pulse_num", 1))
    if pulse_len <= 0:
        parser.error("pulse length must be supplied directly or in --scenario")
    channel_positions = None
    if args.scenario is not None:
        channel_positions = load_channel_positions(args.scenario, source="reported")
    summary = analyze_file(
        args.input,
        pulse_len,
        channel_count,
        iq_data_type,
        fc_hz,
        pulses_per_beam=pulses_per_beam,
        channel_positions_m=channel_positions,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
