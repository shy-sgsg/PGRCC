#!/usr/bin/env python3
"""Shared reported-metadata finite-range geometry model.

The model is intentionally independent of the experiment runners.  Both
evaluation code and blind estimators can call it with reported channel
positions, reported platform state, reported beam angle, and a range value.
No true geometry or target truth is accepted as an implicit global input.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

import numpy as np


C = 299_792_458.0
DEFAULT_CARRIER_PHASE_SIGN = -1.0
DEFAULT_CHANNEL_POSITIONS_M = np.asarray(
    [
        [-0.085, 0.0, 0.085],
        [0.085, 0.0, 0.085],
        [-0.085, 0.0, -0.085],
        [0.085, 0.0, -0.085],
    ],
    dtype=np.float64,
)


def _finite_scalar(value: object, name: str) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _vector(value: Sequence[float], name: str) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64)
    if result.shape != (3,) or not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must be a finite 3-vector")
    return result


def _channel_positions(value: Sequence[Sequence[float]]) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64)
    if result.shape != (4, 3) or not np.all(np.isfinite(result)):
        raise ValueError("channel_positions_m must contain four finite 3-vectors")
    return result


def _section(metadata: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = metadata.get(key, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"metadata.{key} must be an object")
    return value


def _axis_component(axis: str) -> tuple[float, float]:
    if axis in {"east", "track_right"}:
        return 1.0, 0.0
    if axis in {"west", "track_left"}:
        return -1.0, 0.0
    if axis in {"north", "track_forward"}:
        return 0.0, 1.0
    if axis in {"south", "track_backward"}:
        return 0.0, -1.0
    raise ValueError(f"unsupported local axis: {axis}")


def _horizontal_look(theta_deg: float, metadata: Mapping[str, object]) -> np.ndarray:
    geometry = _section(metadata, "simulation_geometry")
    platform = _section(metadata, "platform")
    theta = _finite_scalar(theta_deg, "theta_deg")
    heading = platform.get("heading_deg", geometry.get("platform_heading_deg", 90.0))
    heading_deg = _finite_scalar(heading, "platform heading")
    squint_side = int(platform.get("squint_side", geometry.get("squint_side", 1)))
    if squint_side not in {-1, 1}:
        raise ValueError("squint_side must be -1 or 1")
    theta_offset = _finite_scalar(
        geometry.get("beam_theta_offset_deg", 0.0),
        "beam_theta_offset_deg",
    )
    side_dir = -90.0 if squint_side == 1 else 90.0
    beam_center_dir = side_dir - (theta + theta_offset)
    target_azimuth = math.radians(heading_deg - beam_center_dir)
    east = math.cos(target_azimuth)
    north = math.sin(target_azimuth)
    local_x = str(geometry.get("local_x_axis", "north"))
    local_y = str(geometry.get("local_y_axis", "east"))
    xe, xn = _axis_component(local_x)
    ye, yn = _axis_component(local_y)
    return np.asarray(
        [xe * east + xn * north, ye * east + yn * north],
        dtype=np.float64,
    )


def _range_components(
    slant_range_m: float,
    platform_z_m: float,
    metadata: Mapping[str, object],
) -> tuple[float, float]:
    slant = _finite_scalar(slant_range_m, "slant_range_m")
    if slant <= 0.0:
        raise ValueError("slant_range_m must be positive")
    geometry = _section(metadata, "simulation_geometry")
    scene = _section(metadata, "scene")
    ground_z = _finite_scalar(scene.get("ground_z_m", 0.0), "ground_z_m")
    range_geometry = str(
        geometry.get("range_geometry", metadata.get("range_geometry", "algorithm"))
    )
    use_ground = bool(geometry.get("use_ground_range_for_position", True))
    vertical = platform_z_m - ground_z
    if use_ground and range_geometry != "slant":
        radicand = slant * slant - vertical * vertical
        if radicand < 0.0:
            raise ValueError("slant range is shorter than the platform-ground height")
        ground = math.sqrt(radicand)
    else:
        ground = slant
    return ground, ground_z


def slant_range_from_sample_m(
    range_sample: float,
    metadata: Mapping[str, object],
) -> float:
    """Convert a reported range sample index to slant range in metres."""

    sample = _finite_scalar(range_sample, "range_sample")
    waveform = _section(metadata, "waveform")
    processing = _section(metadata, "range_processing")
    if "fs_hz" in waveform:
        fs_hz = _finite_scalar(waveform["fs_hz"], "waveform.fs_hz")
    elif "fs_mhz" in waveform:
        fs_hz = _finite_scalar(waveform["fs_mhz"], "waveform.fs_mhz") * 1.0e6
    elif "fs_hz" in metadata:
        fs_hz = _finite_scalar(metadata["fs_hz"], "metadata.fs_hz")
    else:
        fs_hz = 60.0e6
    if fs_hz <= 0.0:
        raise ValueError("sample-rate must be positive")
    if "sample_delay_sec" in waveform:
        delay_sec = _finite_scalar(
            waveform["sample_delay_sec"],
            "waveform.sample_delay_sec",
        )
    elif "sample_delay_us" in processing:
        delay_sec = _finite_scalar(
            processing["sample_delay_us"],
            "range_processing.sample_delay_us",
        ) * 1.0e-6
    else:
        delay_sec = _finite_scalar(
            metadata.get("sample_delay_us", 0.0),
            "sample_delay_us",
        ) * 1.0e-6
    result = 0.5 * C * (sample / fs_hz + delay_sec)
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError("reported range sample does not map to positive slant range")
    return result


def _los_unit(
    theta_deg: float,
    slant_range_m: float,
    platform_position_m: Sequence[float],
    metadata: Mapping[str, object],
) -> np.ndarray:
    platform = _vector(platform_position_m, "platform_position_m")
    ground_range, ground_z = _range_components(
        slant_range_m,
        float(platform[2]),
        metadata,
    )
    slant = _finite_scalar(slant_range_m, "slant_range_m")
    horizontal = _horizontal_look(theta_deg, metadata)
    los = np.asarray(
        [
            horizontal[0] * ground_range / slant,
            horizontal[1] * ground_range / slant,
            (ground_z - platform[2]) / slant,
        ],
        dtype=np.float64,
    )
    norm = float(np.linalg.norm(los))
    if not math.isfinite(norm) or norm <= 0.0:
        raise ValueError("finite-range LOS is not finite")
    return los / norm


def reported_los_unit(
    theta_deg: float,
    slant_range_m: float,
    platform_position_m: Sequence[float],
    metadata: Mapping[str, object],
) -> np.ndarray:
    """Return the normalized LOS implied by reported geometry metadata."""

    return _los_unit(theta_deg, slant_range_m, platform_position_m, metadata)


def beam_target_position_m(
    theta_deg: float,
    slant_range_m: float,
    platform_position_m: Sequence[float],
    metadata: Mapping[str, object],
) -> np.ndarray:
    """Return the target position implied by reported beam/range metadata."""

    platform = _vector(platform_position_m, "platform_position_m")
    ground_range, ground_z = _range_components(
        slant_range_m,
        float(platform[2]),
        metadata,
    )
    horizontal = _horizontal_look(theta_deg, metadata)
    target = np.asarray(
        [
            platform[0] + horizontal[0] * ground_range,
            platform[1] + horizontal[1] * ground_range,
            ground_z,
        ],
        dtype=np.float64,
    )
    if not np.all(np.isfinite(target)):
        raise ValueError("beam target position is not finite")
    return target


def finite_range_channel_path_m(
    target_position_m: Sequence[float],
    platform_position_m: Sequence[float],
    channel_position_m: Sequence[float],
) -> float:
    """Return common transmit plus channel-specific receive path length."""

    target = _vector(target_position_m, "target_position_m")
    platform = _vector(platform_position_m, "platform_position_m")
    channel = _vector(channel_position_m, "channel_position_m")
    transmit = float(np.linalg.norm(target - platform))
    receive = float(np.linalg.norm(target - (platform + channel)))
    result = transmit + receive
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError("finite-range channel path must be positive and finite")
    return result


def wrap_phase(value: float) -> float:
    """Wrap a scalar phase to [-pi, pi]."""

    result = _finite_scalar(value, "phase")
    return math.atan2(math.sin(result), math.cos(result))


def finite_range_pair_phase_rad(
    theta_deg: float,
    slant_range_m: float,
    platform_position_m: Sequence[float],
    channel_positions_m: Sequence[Sequence[float]],
    i: int,
    j: int,
    fc_hz: float,
    metadata: Mapping[str, object],
) -> float:
    """Return exact finite-range phase_i minus phase_j for a reported context."""

    positions = _channel_positions(channel_positions_m)
    if i not in range(4) or j not in range(4) or i == j:
        raise ValueError("i and j must be distinct channel indices in [0, 3]")
    carrier = _finite_scalar(fc_hz, "fc_hz")
    if carrier <= 0.0:
        raise ValueError("fc_hz must be positive")
    sign = _finite_scalar(
        metadata.get("carrier_phase_sign", DEFAULT_CARRIER_PHASE_SIGN),
        "carrier_phase_sign",
    )
    target = beam_target_position_m(
        theta_deg,
        slant_range_m,
        platform_position_m,
        metadata,
    )
    path_i = finite_range_channel_path_m(target, platform_position_m, positions[i])
    path_j = finite_range_channel_path_m(target, platform_position_m, positions[j])
    return wrap_phase(sign * 2.0 * math.pi * (path_i - path_j) / (C / carrier))


def linear_pair_phase_rad(
    theta_deg: float,
    slant_range_m: float,
    platform_position_m: Sequence[float],
    channel_positions_m: Sequence[Sequence[float]],
    i: int,
    j: int,
    fc_hz: float,
    metadata: Mapping[str, object],
) -> float:
    """Return the v1 linear LOS phase approximation."""

    positions = _channel_positions(channel_positions_m)
    if i not in range(4) or j not in range(4) or i == j:
        raise ValueError("i and j must be distinct channel indices in [0, 3]")
    carrier = _finite_scalar(fc_hz, "fc_hz")
    if carrier <= 0.0:
        raise ValueError("fc_hz must be positive")
    sign = _finite_scalar(
        metadata.get("carrier_phase_sign", DEFAULT_CARRIER_PHASE_SIGN),
        "carrier_phase_sign",
    )
    los = _los_unit(theta_deg, slant_range_m, platform_position_m, metadata)
    return wrap_phase(
        sign
        * 2.0
        * math.pi
        * float(np.dot(los, positions[j] - positions[i]))
        / (C / carrier)
    )


def linear_baseline_sensitivity_rad_per_m(
    theta_deg: float,
    slant_range_m: float,
    platform_position_m: Sequence[float],
    i: int,
    j: int,
    fc_hz: float,
    metadata: Mapping[str, object],
) -> float:
    """Return v1 phase sensitivity to moving right-side channels in x."""

    if i not in range(4) or j not in range(4) or i == j:
        raise ValueError("i and j must be distinct channel indices in [0, 3]")
    carrier = _finite_scalar(fc_hz, "fc_hz")
    if carrier <= 0.0:
        raise ValueError("fc_hz must be positive")
    sign = _finite_scalar(
        metadata.get("carrier_phase_sign", DEFAULT_CARRIER_PHASE_SIGN),
        "carrier_phase_sign",
    )
    los = _los_unit(theta_deg, slant_range_m, platform_position_m, metadata)
    right_indicator = np.asarray([0.0, 1.0, 0.0, 1.0])
    return (
        sign
        * 2.0
        * math.pi
        / (C / carrier)
        * float(los[0] * (right_indicator[j] - right_indicator[i]))
    )


def compare_exact_linear_phase(
    theta_deg: float,
    slant_range_m: float,
    platform_position_m: Sequence[float],
    channel_positions_m: Sequence[Sequence[float]],
    i: int,
    j: int,
    fc_hz: float,
    metadata: Mapping[str, object],
    derivative_step_m: float = 1.0e-6,
) -> dict[str, float]:
    """Compare v2 and v1 phases and estimate local x-position sensitivity."""

    step = _finite_scalar(derivative_step_m, "derivative_step_m")
    if step <= 0.0:
        raise ValueError("derivative_step_m must be positive")
    positions = _channel_positions(channel_positions_m)
    exact = finite_range_pair_phase_rad(
        theta_deg,
        slant_range_m,
        platform_position_m,
        positions,
        i,
        j,
        fc_hz,
        metadata,
    )
    linear = linear_pair_phase_rad(
        theta_deg,
        slant_range_m,
        platform_position_m,
        positions,
        i,
        j,
        fc_hz,
        metadata,
    )
    plus = positions.copy()
    minus = positions.copy()
    plus[j, 0] += step
    minus[j, 0] -= step
    plus_phase = finite_range_pair_phase_rad(
        theta_deg,
        slant_range_m,
        platform_position_m,
        plus,
        i,
        j,
        fc_hz,
        metadata,
    )
    minus_phase = finite_range_pair_phase_rad(
        theta_deg,
        slant_range_m,
        platform_position_m,
        minus,
        i,
        j,
        fc_hz,
        metadata,
    )
    sensitivity = wrap_phase(plus_phase - minus_phase) / (2.0 * step)
    return {
        "exact_phase_rad": exact,
        "linear_phase_rad": linear,
        "disagreement_rad": wrap_phase(exact - linear),
        "sensitivity_rad_per_m": sensitivity,
    }


__all__ = [
    "C",
    "DEFAULT_CHANNEL_POSITIONS_M",
    "beam_target_position_m",
    "compare_exact_linear_phase",
    "finite_range_channel_path_m",
    "finite_range_pair_phase_rad",
    "linear_baseline_sensitivity_rad_per_m",
    "linear_pair_phase_rad",
    "reported_los_unit",
    "slant_range_from_sample_m",
    "wrap_phase",
]
