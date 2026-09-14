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
import importlib.util
import json
import math
import struct
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

import numpy as np


_GEOMETRY_MODEL_SPEC = importlib.util.spec_from_file_location(
    "pgrcc_finite_range_geometry_model",
    Path(__file__).with_name("finite_range_geometry_model.py"),
)
if _GEOMETRY_MODEL_SPEC is None or _GEOMETRY_MODEL_SPEC.loader is None:
    raise RuntimeError("cannot load finite_range_geometry_model.py")
GEOMETRY_MODEL = importlib.util.module_from_spec(_GEOMETRY_MODEL_SPEC)
_GEOMETRY_MODEL_SPEC.loader.exec_module(GEOMETRY_MODEL)


C = float(GEOMETRY_MODEL.C)
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

    return float(GEOMETRY_MODEL.wrap_phase(value))


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
    try:
        slant_range = GEOMETRY_MODEL.slant_range_from_sample_m(
            range_sample, metadata
        )
        platform = metadata.get("platform", {})
        if not isinstance(platform, Mapping):
            raise ValueError("metadata.platform must be an object")
        height = float(platform.get("height_m", metadata.get("platform_height_m", 6000.0)))
        return np.asarray(
            GEOMETRY_MODEL.reported_los_unit(
                theta_deg,
                slant_range,
                [0.0, 0.0, height],
                metadata,
            ),
            dtype=np.float64,
        )
    except (TypeError, ValueError):
        return np.full(3, np.nan, dtype=np.float64)


def exact_receive_channel_path_length(
    target_position_m: Sequence[float],
    platform_position_m: Sequence[float],
    channel_position_m: Sequence[float],
) -> float:
    """Return the exact two-way path used by the Stage2 echo model.

    The transmitter is at ``platform_position_m`` and the receiver is at
    ``platform_position_m + channel_position_m``.  This helper is explicitly
    evaluation-only: the blind estimator above must continue to use reported
    geometry and its nominal LOS model.
    """

    return float(
        GEOMETRY_MODEL.finite_range_channel_path_m(
            target_position_m,
            platform_position_m,
            channel_position_m,
        )
    )


def exact_pair_phase_rad(
    target_position_m: Sequence[float],
    platform_position_m: Sequence[float],
    channel_positions_m: Sequence[Sequence[float]],
    i: int,
    j: int,
    fc_hz: float,
    carrier_phase_sign: float = -1.0,
) -> float:
    """Return the exact pair phase ``phase_i - phase_j`` for evaluation."""

    positions = _validate_channel_positions(channel_positions_m)
    if i not in range(4) or j not in range(4) or i == j:
        raise ValueError("i and j must be distinct channel indices in [0, 3]")
    if not math.isfinite(fc_hz) or fc_hz <= 0.0:
        raise ValueError("fc_hz must be positive and finite")
    if not math.isfinite(carrier_phase_sign):
        raise ValueError("carrier_phase_sign must be finite")
    wavelength = C / float(fc_hz)
    path_i = GEOMETRY_MODEL.finite_range_channel_path_m(
        target_position_m, platform_position_m, positions[i]
    )
    path_j = GEOMETRY_MODEL.finite_range_channel_path_m(
        target_position_m, platform_position_m, positions[j]
    )
    return float(
        GEOMETRY_MODEL.wrap_phase(
            float(carrier_phase_sign)
            * 2.0
            * math.pi
            * (path_i - path_j)
            / wavelength
        )
    )


def nominal_pair_phase_rad(
    theta_deg: float,
    range_sample: float,
    metadata: Mapping[str, object],
    channel_positions_m: Sequence[Sequence[float]],
    i: int,
    j: int,
    fc_hz: Optional[float] = None,
) -> float:
    """Return the blind nominal pair phase for an evaluation comparison."""

    positions = _validate_channel_positions(channel_positions_m)
    if i not in range(4) or j not in range(4) or i == j:
        raise ValueError("i and j must be distinct channel indices in [0, 3]")
    if fc_hz is None:
        waveform = metadata.get("waveform", {})
        fc_hz = float(
            waveform.get("fc_hz", waveform.get("fc_ghz", 16.0) * 1.0e9)
        )
        if fc_hz < 1.0e6:
            fc_hz *= 1.0e9
    if not math.isfinite(float(fc_hz)) or float(fc_hz) <= 0.0:
        raise ValueError("fc_hz must be positive and finite")
    platform = metadata.get("platform", {})
    if not isinstance(platform, Mapping):
        raise ValueError("metadata.platform must be an object")
    height = float(platform.get("height_m", metadata.get("platform_height_m", 6000.0)))
    slant_range = GEOMETRY_MODEL.slant_range_from_sample_m(
        range_sample, metadata
    )
    return float(
        GEOMETRY_MODEL.linear_pair_phase_rad(
            theta_deg,
            slant_range,
            [0.0, 0.0, height],
            positions,
            i,
            j,
            float(fc_hz),
            metadata,
        )
    )


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


def observed_range_sample(
    channels: np.ndarray,
    start: int,
    end: int,
) -> float:
    """Estimate the reported range location from observed block energy.

    A finite range block is wider than the Stage2 beam-centre impulse.  Using
    the block midpoint introduces a deterministic near-field phase error.  A
    common all-channel energy centroid stays within the unknown-only IQ
    contract and preserves fractional delay information without using scene
    truth or a configured calibration range.
    """

    if channels.ndim != 2 or channels.shape[1] <= 0:
        raise ValueError("channels must be a two-dimensional channel matrix")
    if start < 0 or end <= start or end > channels.shape[0]:
        raise ValueError("range block is outside the channel matrix")
    block = channels[start:end]
    finite = np.all(np.isfinite(block.real), axis=1) & np.all(
        np.isfinite(block.imag), axis=1
    )
    if not np.any(finite):
        return float(start + end - 1) * 0.5
    energy = np.sum(np.abs(block[finite]) ** 2, axis=1, dtype=np.float64)
    total = float(np.sum(energy, dtype=np.float64))
    if not math.isfinite(total) or total <= 0.0:
        return float(start + end - 1) * 0.5
    indices = np.arange(start, end, dtype=np.float64)[finite]
    result = float(np.sum(indices * energy, dtype=np.float64) / total)
    return result if math.isfinite(result) else float(start + end - 1) * 0.5


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


def _geometry_metadata_platform_height(metadata: Mapping[str, object]) -> float:
    platform = metadata.get("platform", {})
    if not isinstance(platform, Mapping):
        raise ValueError("metadata.platform must be an object")
    try:
        height = float(platform.get("height_m", metadata.get("platform_height_m", 6000.0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata platform height must be finite") from exc
    if not math.isfinite(height):
        raise ValueError("metadata platform height must be finite")
    return height


def _reported_platform_position(
    packet_index: int,
    metadata: Mapping[str, object],
) -> np.ndarray:
    """Resolve the reported platform state for one packet."""

    platform = metadata.get("platform", {})
    waveform = metadata.get("waveform", {})
    if not isinstance(platform, Mapping) or not isinstance(waveform, Mapping):
        raise ValueError("metadata platform and waveform must be objects")
    explicit = platform.get("position_m", metadata.get("platform_position_m"))
    if explicit is not None:
        position = np.asarray(explicit, dtype=np.float64)
        if position.shape != (3,) or not np.all(np.isfinite(position)):
            raise ValueError("reported platform_position_m must be a finite 3-vector")
        return position
    speed = float(platform.get("speed_mps", metadata.get("platform_speed_mps", 0.0)))
    prf = float(waveform.get("prf_hz", metadata.get("prf_hz", 0.0)))
    height = _geometry_metadata_platform_height(metadata)
    x0 = float(platform.get("position_x_m", 0.0))
    y0 = float(platform.get("position_y_m", 0.0))
    if not all(math.isfinite(value) for value in (speed, prf, height, x0, y0)):
        raise ValueError("reported platform state must be finite")
    if prf <= 0.0:
        raise ValueError("reported platform PRF must be positive")
    # The Stage2 pilot's reported local trajectory is along local x.
    return np.asarray(
        [x0 + speed * float(packet_index) / prf, y0, height],
        dtype=np.float64,
    )


def _assert_unknown_geometry_input(
    value: object,
    path: str = "input",
) -> None:
    """Reject truth/known-error fields from a blind estimator input."""

    forbidden = (
        "truth",
        "true_geometry",
        "true_channel",
        "known_error",
        "servo_truth",
        "target_truth",
    )
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(token in normalized for token in forbidden):
                raise ValueError(f"blind geometry input contains forbidden field: {path}.{key}")
            _assert_unknown_geometry_input(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_unknown_geometry_input(child, f"{path}[{index}]")


def _baseline_candidate_positions(
    reported_positions: np.ndarray,
    delta_m: float,
    metadata: Mapping[str, object],
) -> np.ndarray:
    indices_value = metadata.get("baseline_error_channel_indices", (1, 3))
    try:
        indices = tuple(int(item) for item in indices_value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("baseline_error_channel_indices must be channel indices") from exc
    if not indices or any(index not in range(4) for index in indices):
        raise ValueError("baseline_error_channel_indices must be non-empty indices in [0,3]")
    axis = metadata.get("baseline_error_axis", "x")
    if axis not in {"x", 0}:
        raise ValueError("only the reported x-axis baseline error is supported")
    candidate = reported_positions.copy()
    candidate[list(indices), 0] += float(delta_m)
    return candidate


def _geometry_prediction(
    row: Mapping[str, object],
    positions: np.ndarray,
    fc_hz: float,
    metadata: Mapping[str, object],
    model: str,
) -> float:
    theta = float(row["theta_deg"])
    slant_range = float(row["slant_range_m"])
    platform = row["platform_position_m"]
    i = int(row["pair_i"])
    j = int(row["pair_j"])
    if model == "v2":
        return float(
            GEOMETRY_MODEL.finite_range_pair_phase_rad(
                theta,
                slant_range,
                platform,  # type: ignore[arg-type]
                positions,
                i,
                j,
                fc_hz,
                metadata,
            )
        )
    return float(
        GEOMETRY_MODEL.linear_pair_phase_rad(
            theta,
            slant_range,
            platform,  # type: ignore[arg-type]
            positions,
            i,
            j,
            fc_hz,
            metadata,
        )
    )


def _normalize_geometry_observations(
    observations: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    if not observations:
        raise ValueError("geometry observations must not be empty")
    normalized: list[dict[str, object]] = []
    for index, source in enumerate(observations):
        if not isinstance(source, Mapping):
            raise ValueError(f"geometry observation {index} must be an object")
        _assert_unknown_geometry_input(source, f"observations[{index}]")
        required = (
            "theta_deg",
            "slant_range_m",
            "platform_position_m",
            "pair_i",
            "pair_j",
            "observed_phase_rad",
        )
        missing = [key for key in required if key not in source]
        if missing:
            raise ValueError(
                f"geometry observation {index} missing fields: {', '.join(missing)}"
            )
        theta = float(source["theta_deg"])
        slant = float(source["slant_range_m"])
        platform = np.asarray(source["platform_position_m"], dtype=np.float64)
        i = int(source["pair_i"])
        j = int(source["pair_j"])
        phase = float(source["observed_phase_rad"])
        if (
            not math.isfinite(theta)
            or not math.isfinite(slant)
            or slant <= 0.0
            or platform.shape != (3,)
            or not np.all(np.isfinite(platform))
            or i not in range(4)
            or j not in range(4)
            or i == j
            or not math.isfinite(phase)
        ):
            raise ValueError(f"geometry observation {index} contains invalid values")
        coherence = float(source.get("coherence", 1.0))
        weight = float(source.get("weight", max(1.0e-12, coherence)))
        noise_sigma = float(source.get("noise_sigma_rad", 0.0))
        if (
            not math.isfinite(coherence)
            or coherence < 0.0
            or coherence > 1.0
            or not math.isfinite(weight)
            or weight <= 0.0
            or not math.isfinite(noise_sigma)
            or noise_sigma < 0.0
        ):
            raise ValueError(f"geometry observation {index} contains invalid quality fields")
        normalized.append(
            {
                "theta_deg": theta,
                "slant_range_m": slant,
                "platform_position_m": [float(value) for value in platform],
                "pair_i": i,
                "pair_j": j,
                "observed_phase_rad": _wrap_phase(phase),
                "coherence": coherence,
                "weight": weight,
                "noise_sigma_rad": noise_sigma,
                "beam_id": source.get("beam_id"),
                "pulse_index": source.get("pulse_index"),
                "source_id": str(source.get("source_id", f"observation_{index}")),
            }
        )
    if len({(row["pair_i"], row["pair_j"]) for row in normalized}) == 0:
        raise ValueError("geometry observations contain no pair")
    return normalized


def _geometry_delta_score(
    delta_m: float,
    observations: Sequence[Mapping[str, object]],
    reported_positions: np.ndarray,
    fc_hz: float,
    metadata: Mapping[str, object],
    model: str,
) -> dict[str, object]:
    candidate = _baseline_candidate_positions(reported_positions, delta_m, metadata)
    grouped: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
    for row in observations:
        prediction = _geometry_prediction(row, candidate, fc_hz, metadata, model)
        residual = _wrap_phase(float(row["observed_phase_rad"]) - prediction)
        key = (int(row["pair_i"]), int(row["pair_j"]))
        grouped.setdefault(key, []).append(
            (residual, float(row["weight"]), float(row["theta_deg"]), prediction)
        )
    intercepts: dict[str, float] = {}
    diagnostics: list[dict[str, object]] = []
    total_loss = 0.0
    total_weight = 0.0
    residual_values: list[float] = []
    residual_weights: list[float] = []
    for pair, values in sorted(grouped.items()):
        z = sum(weight * complex(math.cos(residual), math.sin(residual))
                for residual, weight, _, _ in values)
        intercept = _wrap_phase(float(math.atan2(z.imag, z.real)))
        intercepts[f"C{pair[0] + 1}{pair[1] + 1}"] = intercept
        for residual, weight, theta, prediction in values:
            after = _wrap_phase(residual - intercept)
            abs_after = abs(after)
            # A fixed phase scale is only the robust-loss transition.  It is
            # never used as the correction deadband.
            huber = 0.5 * abs_after * abs_after if abs_after <= 1.0 else abs_after - 0.5
            total_loss += weight * huber
            total_weight += weight
            residual_values.append(after)
            residual_weights.append(weight)
            diagnostics.append(
                {
                    "theta_deg": theta,
                    "pair_i": pair[0],
                    "pair_j": pair[1],
                    "predicted_phase_rad": prediction,
                    "residual_rad": after,
                }
            )
    residual_array = np.asarray(residual_values, dtype=np.float64)
    weight_array = np.asarray(residual_weights, dtype=np.float64)
    rmse = math.sqrt(
        float(np.sum(weight_array * residual_array * residual_array))
        / max(1.0e-12, total_weight)
    )
    return {
        "objective": float(total_loss / max(1.0e-12, total_weight)),
        "pair_intercepts_rad": intercepts,
        "diagnostics": diagnostics,
        "rmse_rad": rmse,
        "weight_sum": total_weight,
    }


def estimate_unknown_geometry_from_observations(
    observations: Sequence[Mapping[str, object]],
    reported_channel_positions_m: Sequence[Sequence[float]],
    fc_hz: float,
    metadata: Mapping[str, object],
    *,
    model: str = "v2",
    max_abs_delta_m: float = 0.02,
    grid_count: int = 801,
) -> Dict[str, object]:
    """Estimate baseline error from reported-context pair phases only."""

    if model in {"v1", "v1_linear"}:
        fit_model = "v1"
        model_label = "v1_linear_los"
    elif model in {"v2", "v2_exact"}:
        fit_model = "v2"
        model_label = "v2_exact_finite_range"
    else:
        raise ValueError("model must be v1 or v2")
    if not math.isfinite(float(fc_hz)) or float(fc_hz) <= 0.0:
        raise ValueError("fc_hz must be positive and finite")
    max_delta = float(max_abs_delta_m)
    if not math.isfinite(max_delta) or max_delta <= 0.0:
        raise ValueError("max_abs_delta_m must be positive and finite")
    if int(grid_count) < 21:
        raise ValueError("grid_count must be at least 21")
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be an object")
    _assert_unknown_geometry_input(metadata, "metadata")
    positions = _validate_channel_positions(reported_channel_positions_m)
    rows = _normalize_geometry_observations(observations)
    theta_count = len({round(float(row["theta_deg"]), 9) for row in rows})
    if theta_count < 2:
        return {
            "schema": "unknown_only_baseline_estimator_v2",
            "fit_status": "fallback_unidentifiable",
            "status": "FALLBACK_UNIDENTIFIABLE",
            "action": "fallback_current",
            "estimated_baseline_error_m": None,
            "uncertainty_m": None,
            "deadband_m": None,
            "observation_count": len(rows),
            "theta_count": theta_count,
            "model": model_label,
        }

    coarse = np.linspace(-max_delta, max_delta, int(grid_count))
    coarse_results = [
        _geometry_delta_score(float(delta), rows, positions, float(fc_hz), metadata, fit_model)
        for delta in coarse
    ]
    best_index = int(np.argmin([float(item["objective"]) for item in coarse_results]))
    best_delta = float(coarse[best_index])
    step = float(coarse[1] - coarse[0])
    refine = np.linspace(
        max(-max_delta, best_delta - 2.0 * step),
        min(max_delta, best_delta + 2.0 * step),
        81,
    )
    refined_results = [
        _geometry_delta_score(float(delta), rows, positions, float(fc_hz), metadata, fit_model)
        for delta in refine
    ]
    best_index = int(np.argmin([float(item["objective"]) for item in refined_results]))
    best_delta = float(refine[best_index])
    fit = refined_results[best_index]

    derivative_step = min(1.0e-5, max_delta / 100.0)
    plus_positions = _baseline_candidate_positions(positions, best_delta + derivative_step, metadata)
    minus_positions = _baseline_candidate_positions(positions, best_delta - derivative_step, metadata)
    sensitivities: list[float] = []
    exact_linear_disagreements: list[float] = []
    for row in rows:
        plus = _geometry_prediction(row, plus_positions, float(fc_hz), metadata, fit_model)
        minus = _geometry_prediction(row, minus_positions, float(fc_hz), metadata, fit_model)
        sensitivities.append(_wrap_phase(plus - minus) / (2.0 * derivative_step))
        if fit_model == "v2":
            exact = plus
            linear = _geometry_prediction(row, positions, float(fc_hz), metadata, "v1")
            exact_linear_disagreements.append(abs(_wrap_phase(exact - linear)))
        else:
            exact = _geometry_prediction(row, positions, float(fc_hz), metadata, "v2")
            linear = _geometry_prediction(row, positions, float(fc_hz), metadata, "v1")
            exact_linear_disagreements.append(abs(_wrap_phase(exact - linear)))
    sensitivity_array = np.asarray(
        [abs(value) for value in sensitivities if math.isfinite(value) and abs(value) > 1.0e-12],
        dtype=np.float64,
    )
    if sensitivity_array.size == 0:
        return {
            "schema": "unknown_only_baseline_estimator_v2",
            "fit_status": "fallback_unidentifiable",
            "status": "FALLBACK_UNIDENTIFIABLE",
            "action": "fallback_current",
            "estimated_baseline_error_m": None,
            "uncertainty_m": None,
            "deadband_m": None,
            "observation_count": len(rows),
            "theta_count": theta_count,
            "model": model_label,
        }
    weighted_information = float(
        sum(float(row["weight"]) * sensitivity * sensitivity
            for row, sensitivity in zip(rows, sensitivities))
    )
    rmse = float(fit["rmse_rad"])
    supplied_noise = [
        float(row["noise_sigma_rad"])
        for row in rows
        if float(row["noise_sigma_rad"]) > 0.0
    ]
    phase_noise = max(
        1.0e-12,
        rmse,
        float(np.median(np.asarray(supplied_noise, dtype=np.float64)))
        if supplied_noise
        else 0.0,
    )
    uncertainty = phase_noise / math.sqrt(max(1.0e-12, weighted_information))
    physical_floor = phase_noise / float(np.median(sensitivity_array))
    uncertainty_m = float(max(uncertainty, physical_floor))
    deadband_m = float(max(2.0 * uncertainty_m, physical_floor))
    estimate = float(best_delta)
    status = (
        "NO_CORRECTION_NEEDED"
        if abs(estimate) <= deadband_m
        else "APPLY_ESTIMATED_CORRECTION"
    )
    diagnostics = []
    for row, sensitivity, disagreement in zip(
        rows, sensitivities, exact_linear_disagreements
    ):
        diagnostics.append(
            {
                "theta_deg": row["theta_deg"],
                "slant_range_m": row["slant_range_m"],
                "pair_i": row["pair_i"],
                "pair_j": row["pair_j"],
                "beam_id": row["beam_id"],
                "pulse_index": row["pulse_index"],
                "sensitivity_rad_per_m": sensitivity,
                "exact_linear_disagreement_rad": disagreement,
            }
        )
    return {
        "schema": "unknown_only_baseline_estimator_v2",
        "fit_status": "fit",
        "status": status,
        "action": "fallback_current" if status == "NO_CORRECTION_NEEDED" else "apply_estimate",
        "estimated_baseline_error_m": estimate,
        "uncertainty_m": uncertainty_m,
        "deadband_m": deadband_m,
        "physical_sensitivity_rad_per_m": float(np.median(sensitivity_array)),
        "sensitivity_rad_per_m": float(np.median(sensitivity_array)),
        "phase_noise_rad": phase_noise,
        "fit_rmse_rad": rmse,
        "model_disagreement_mean_abs_rad": float(np.mean(exact_linear_disagreements)),
        "model_disagreement_p95_rad": float(np.percentile(exact_linear_disagreements, 95.0)),
        "pair_intercepts_rad": fit["pair_intercepts_rad"],
        "observation_count": len(rows),
        "pair_count": len({(row["pair_i"], row["pair_j"]) for row in rows}),
        "theta_count": theta_count,
        "range_count": len({round(float(row["slant_range_m"]), 6) for row in rows}),
        "diagnostics": diagnostics,
        "model": {
            "version": model_label,
            "uses_truth": False,
            "uses_ideal_reference": False,
            "correction_deadband": "derived from observed phase noise and local physical sensitivity",
            "objective": "per-pair circular intercept plus deterministic Huber phase residual",
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
    geometry_model: str = "v1",
) -> Dict[str, object]:
    """Estimate a right-vs-left along-track offset from one unknown-only file.

    This function accepts exactly one echo file.  It never loads ideal data,
    target truth, known parameters, or future-period metrics.  The nominal
    phase model is computed from reported geometry plus packet theta/range
    metadata; a per-pair constant intercept absorbs fixed electronic phase.
    """

    if pair_mode not in {"six_pair", "single_pair"}:
        raise ValueError("pair_mode must be six_pair or single_pair")
    if geometry_model not in {"v1", "v1_linear", "v2", "v2_exact"}:
        raise ValueError("geometry_model must be v1 or v2")
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
    exact_observations: list[dict[str, object]] = []
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
                if geometry_model in {"v2", "v2_exact"}:
                    try:
                        range_sample = observed_range_sample(channels, start, end)
                        slant_range_m = GEOMETRY_MODEL.slant_range_from_sample_m(
                            range_sample, metadata
                        )
                        platform_position = _reported_platform_position(
                            int(packet["packet_index"]), metadata
                        )
                    except (TypeError, ValueError):
                        continue
                    exact_observations.append(
                        {
                            "theta_deg": theta,
                            "slant_range_m": slant_range_m,
                            "platform_position_m": [
                                float(value) for value in platform_position
                            ],
                            "beam_id": beam_index,
                            "pulse_index": int(packet["packet_index"]) % max(1, pulses_per_beam),
                            "pair_i": i,
                            "pair_j": j,
                            "observed_phase_rad": float(pair["phase_rad"]),
                            "coherence": float(pair["coherence"]),
                            "weight": float(pair["weight"]),
                            "noise_sigma_rad": 0.0,
                            "source_id": str(input_path),
                        }
                    )
                    continue
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

    if geometry_model in {"v2", "v2_exact"}:
        result = estimate_unknown_geometry_from_observations(
            exact_observations,
            positions,
            fc_hz,
            metadata,
            model="v2",
            max_abs_delta_m=max_abs_delta_m,
        )
        result.update(
            {
                "operational_blind": True,
                "input_path": str(input_path),
                "packet_count": packet_count,
                "pair_mode": pair_mode,
                "single_pair": single_pair,
                "nominal_channel_positions_m": [
                    [float(value) for value in position] for position in positions
                ],
                "geometry_model": "v2",
            }
        )
        return result

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
