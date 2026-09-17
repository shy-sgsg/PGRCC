#!/usr/bin/env python3
"""Estimate servo pointing from target-free four-channel clutter only.

The estimator accepts one target-free C+N packet stream and reported context.
It intentionally has no target, truth, known-error, or ON-minus-OFF input
path.  The fit is a deterministic one-dimensional grid search over a physical
reported-angle model with per-family robust residuals.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import analyze_four_channel_observables as observables  # noqa: E402
import finite_range_geometry_model as geometry  # noqa: E402


FEATURE_SCHEMA = "clutter_only_servo_features_v1"
ESTIMATE_SCHEMA = "clutter_only_servo_estimate_v1"
METHOD = "clutter_only_six_pair_phase_ridge_p38_power_huber_grid_v1"
PAIR_DEFINITIONS = observables.PAIR_DEFINITIONS
CAUSAL_DATA_SOURCES = (
    "six_pair_clutter_phase",
    "doppler_ridge_displacement",
    "p38_phase_slope",
    "multi_beam_power_secondary",
    "reported_angle_geometry_platform",
)
PRIMARY_FAMILIES = ("phase", "ridge", "p38")
DEFAULT_GRID = {
    "grid_min_deg": -0.75,
    "grid_max_deg": 0.75,
    "grid_step_deg": 0.0025,
}
DEFAULT_WEIGHTS = {
    "phase": 1.0,
    "ridge": 1.0,
    "p38": 0.75,
    "power": 0.25,
}
FEATURE_FIELDS = frozenset({
    "theta_deg",
    "slant_range_m",
    "beam_id",
    "pair_id",
    "clutter_phase_rad",
    "doppler_ridge_hz",
    "phase_slope_rad_per_rad",
    "power_db",
    "coherence",
    "source_id",
})
_FORBIDDEN_TOKENS = frozenset({
    "target",
    "truth",
    "known",
    "servo",
    "error",
    "delta",
    "oracle",
    "on",
    "off",
})
_CONTEXT_FIELDS = frozenset({
    "reported_channel_positions_m",
    "channel_positions_m",
    "reported_geometry",
    "reported_platform",
    "platform",
    "reported_platform_position_m",
    "platform_position_m",
    "reported_reference_platform_position_m",
    "reference_platform_position_m",
    "reported_scan",
    "scan",
    "waveform",
    "range_processing",
    "reported_simulation_geometry",
    "simulation_geometry",
    "reported_scene",
    "scene",
    "representative_range_sample",
    "representative_slant_range_m",
    "source_id",
    "input_path",
})
_PACKET_FIELDS = frozenset({
    "packet_index",
    "prt_counter",
    "beam_id",
    "theta_deg",
    "theta_cmd_deg",
    "range_sample",
    "range_bin",
    "slant_range_m",
    "channels",
    "source_id",
})


def _tokens(name: object) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", str(name).lower()) if token}


def _restricted_name(name: object) -> bool:
    return bool(_tokens(name) & _FORBIDDEN_TOKENS)


def _finite(value: object, name: str) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _section(context: Mapping[str, object], *names: str) -> Mapping[str, object]:
    for name in names:
        value = context.get(name)
        if value is not None:
            return _mapping(value, name)
    return {}


def _validate_nested_keys(value: object, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _restricted_name(key):
                raise ValueError(f"target/truth-related input is not allowed: {path}.{key}")
            _validate_nested_keys(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_nested_keys(child, f"{path}[{index}]")


def _validate_reported_context(context: Mapping[str, object]) -> None:
    if not isinstance(context, Mapping):
        raise ValueError("reported_context must be an object")
    for key in context:
        if _restricted_name(key):
            raise ValueError(f"target/truth-related input is not allowed: {key}")
        if key not in _CONTEXT_FIELDS:
            raise ValueError(f"unsupported reported context key: {key}")
    _validate_nested_keys(context, "reported_context")


def _reported_positions(context: Mapping[str, object]) -> np.ndarray:
    value: object = context.get("reported_channel_positions_m")
    if value is None:
        value = context.get("channel_positions_m")
    if value is None:
        geometry_context = context.get("reported_geometry")
        if isinstance(geometry_context, Mapping):
            value = geometry_context.get("channel_positions_m")
    if value is None:
        raise ValueError("reported channel geometry is required; no nominal fallback is allowed")
    positions = np.asarray(value, dtype=np.float64)
    if positions.shape != (4, 3) or not np.all(np.isfinite(positions)):
        raise ValueError("reported channel geometry must contain four finite 3-vectors")
    return positions


def _reported_metadata(context: Mapping[str, object]) -> dict[str, object]:
    _validate_reported_context(context)
    platform = dict(_section(context, "reported_platform", "platform"))
    scan = dict(_section(context, "reported_scan", "scan"))
    waveform = dict(_section(context, "waveform"))
    range_processing = dict(_section(context, "range_processing"))
    simulation_geometry = dict(
        _section(context, "reported_simulation_geometry", "simulation_geometry")
    )
    scene = dict(_section(context, "reported_scene", "scene"))
    if "ground_z_m" not in scene:
        scene["ground_z_m"] = 0.0
    position_value = context.get("reported_platform_position_m")
    if position_value is None:
        position_value = context.get("platform_position_m")
    if position_value is None:
        position_value = [
            platform.get("position_x_m", 0.0),
            platform.get("position_y_m", 0.0),
            platform.get("height_m", 0.0),
        ]
    position = np.asarray(position_value, dtype=np.float64)
    if position.shape != (3,) or not np.all(np.isfinite(position)):
        raise ValueError("reported platform position must be a finite 3-vector")
    reference_value = context.get("reported_reference_platform_position_m")
    if reference_value is None:
        reference_value = context.get("reference_platform_position_m")
    if reference_value is None:
        reference_value = position.tolist()
    reference = np.asarray(reference_value, dtype=np.float64)
    if reference.shape != (3,) or not np.all(np.isfinite(reference)):
        raise ValueError("reported reference platform position must be a finite 3-vector")
    metadata: dict[str, object] = {
        "platform": platform,
        "scan": scan,
        "waveform": waveform,
        "range_processing": range_processing,
        "simulation_geometry": simulation_geometry,
        "scene": scene,
        "reference_platform_position_m": reference.tolist(),
    }
    return metadata


def _platform_position(context: Mapping[str, object]) -> np.ndarray:
    metadata = _reported_metadata(context)
    platform = _mapping(metadata["platform"], "reported platform")
    position = np.asarray(
        [
            platform.get("position_x_m", 0.0),
            platform.get("position_y_m", 0.0),
            platform.get("height_m", 0.0),
        ],
        dtype=np.float64,
    )
    explicit = context.get("reported_platform_position_m", context.get("platform_position_m"))
    if explicit is not None:
        position = np.asarray(explicit, dtype=np.float64)
    if position.shape != (3,) or not np.all(np.isfinite(position)):
        raise ValueError("reported platform position must be a finite 3-vector")
    return position


def _fc_hz(context: Mapping[str, object]) -> float:
    waveform = _section(context, "waveform")
    if waveform.get("fc_hz") is not None:
        value = _finite(waveform["fc_hz"], "waveform.fc_hz")
    else:
        value = _finite(waveform.get("fc_ghz", 0.0), "waveform.fc_ghz") * 1.0e9
    if value <= 0.0:
        raise ValueError("reported carrier frequency must be positive")
    return value


def _model_inputs(
    context: Mapping[str, object],
) -> tuple[dict[str, object], np.ndarray, np.ndarray, float, float]:
    """Validate and materialize immutable reported inputs for a grid fit."""
    metadata = _reported_metadata(context)
    positions = _reported_positions(context)
    platform = _mapping(metadata["platform"], "reported platform")
    explicit = context.get("reported_platform_position_m", context.get("platform_position_m"))
    if explicit is None:
        explicit = [
            platform.get("position_x_m", 0.0),
            platform.get("position_y_m", 0.0),
            platform.get("height_m", 0.0),
        ]
    platform_position = np.asarray(explicit, dtype=np.float64)
    if platform_position.shape != (3,) or not np.all(np.isfinite(platform_position)):
        raise ValueError("reported platform position must be a finite 3-vector")
    speed = _finite(platform.get("speed_mps", 0.0), "reported_platform.speed_mps")
    return metadata, positions, platform_position, _fc_hz(context), speed


def _expected_clutter_phase_from_inputs(
    theta_deg: float,
    slant_range_m: float,
    model_inputs: tuple[dict[str, object], np.ndarray, np.ndarray, float, float],
    i: int,
    j: int,
) -> float:
    metadata, positions, platform_position, fc_hz, _ = model_inputs
    return geometry.finite_range_pair_phase_rad(
        _finite(theta_deg, "theta_deg"),
        _finite(slant_range_m, "slant_range_m"),
        platform_position,
        positions,
        int(i),
        int(j),
        fc_hz,
        metadata,
    )


def _axis_basis(axis: str) -> tuple[float, float]:
    if axis in {"east", "track_right"}:
        return 1.0, 0.0
    if axis in {"west", "track_left"}:
        return -1.0, 0.0
    if axis in {"north", "track_forward"}:
        return 0.0, 1.0
    if axis in {"south", "track_backward"}:
        return 0.0, -1.0
    raise ValueError(f"unsupported local axis: {axis}")


def _vectorized_phase_predictions(
    theta_deg: np.ndarray,
    slant_range_m: np.ndarray,
    delta_grid_deg: np.ndarray,
    model_inputs: tuple[dict[str, object], np.ndarray, np.ndarray, float, float],
    i: int,
    j: int,
) -> np.ndarray:
    """Evaluate the shared finite-range phase formula over a candidate grid."""
    metadata, positions, platform_position, fc_hz, _ = model_inputs
    platform = _mapping(metadata["platform"], "reported platform")
    simulation_geometry = _mapping(metadata["simulation_geometry"], "reported simulation geometry")
    scene = _mapping(metadata["scene"], "reported scene")
    reference = np.asarray(metadata["reference_platform_position_m"], dtype=np.float64)
    ground_z = _finite(scene.get("ground_z_m", 0.0), "ground_z_m")
    slant = np.asarray(slant_range_m, dtype=np.float64).reshape(-1, 1)
    if np.any(~np.isfinite(slant)) or np.any(slant <= 0.0):
        raise ValueError("slant_range_m must be positive and finite")
    use_ground = bool(simulation_geometry.get("use_ground_range_for_position", True))
    range_geometry = str(simulation_geometry.get("range_geometry", "algorithm"))
    vertical = float(reference[2]) - ground_z
    if use_ground and range_geometry != "slant":
        radicand = slant * slant - vertical * vertical
        if np.any(radicand < 0.0):
            raise ValueError("slant range is shorter than the platform-ground height")
        ground_range = np.sqrt(np.maximum(radicand, 0.0))
    else:
        ground_range = slant
    heading = _finite(
        platform.get("heading_deg", simulation_geometry.get("platform_heading_deg", 90.0)),
        "platform heading",
    )
    squint_side = int(platform.get("squint_side", simulation_geometry.get("squint_side", 1)))
    if squint_side not in {-1, 1}:
        raise ValueError("squint_side must be -1 or 1")
    theta_offset = _finite(
        simulation_geometry.get("beam_theta_offset_deg", 0.0),
        "beam_theta_offset_deg",
    )
    side_dir = -90.0 if squint_side == 1 else 90.0
    theta = np.asarray(theta_deg, dtype=np.float64).reshape(-1, 1) + np.asarray(delta_grid_deg, dtype=np.float64).reshape(1, -1)
    target_azimuth = np.radians(heading - side_dir + theta + theta_offset)
    east = np.cos(target_azimuth)
    north = np.sin(target_azimuth)
    xe, xn = _axis_basis(str(simulation_geometry.get("local_x_axis", "north")))
    ye, yn = _axis_basis(str(simulation_geometry.get("local_y_axis", "east")))
    horizontal_x = xe * east + xn * north
    horizontal_y = ye * east + yn * north
    target = np.stack(
        (
            reference[0] + horizontal_x * ground_range,
            reference[1] + horizontal_y * ground_range,
            np.full_like(horizontal_x, ground_z),
        ),
        axis=-1,
    )
    transmit = np.linalg.norm(target - platform_position.reshape(1, 1, 3), axis=-1)
    receive_i = np.linalg.norm(
        target - (platform_position + positions[i]).reshape(1, 1, 3), axis=-1
    )
    receive_j = np.linalg.norm(
        target - (platform_position + positions[j]).reshape(1, 1, 3), axis=-1
    )
    del transmit
    sign = _finite(metadata.get("carrier_phase_sign", geometry.DEFAULT_CARRIER_PHASE_SIGN), "carrier_phase_sign")
    phase = sign * 2.0 * math.pi * (receive_i - receive_j) / (geometry.C / fc_hz)
    return np.arctan2(np.sin(phase), np.cos(phase))


def _prf_hz(context: Mapping[str, object]) -> float:
    value = _finite(_section(context, "waveform").get("prf_hz", 0.0), "waveform.prf_hz")
    if value <= 0.0:
        raise ValueError("reported PRF must be positive")
    return value


def _range_from_context(context: Mapping[str, object]) -> float | None:
    if context.get("representative_slant_range_m") is not None:
        return _finite(context["representative_slant_range_m"], "representative_slant_range_m")
    if context.get("representative_range_sample") is not None:
        metadata = _reported_metadata(context)
        return geometry.slant_range_from_sample_m(
            _finite(context["representative_range_sample"], "representative_range_sample"),
            metadata,
        )
    return None


def expected_clutter_phase_rad(
    theta_deg: float,
    slant_range_m: float,
    reported_context: Mapping[str, object],
    i: int,
    j: int,
) -> float:
    """Return the exact reported-context pair phase used by the fit."""
    return _expected_clutter_phase_from_inputs(
        theta_deg,
        slant_range_m,
        _model_inputs(reported_context),
        i,
        j,
    )


def expected_doppler_ridge_hz(
    theta_deg: float, reported_context: Mapping[str, object]
) -> float:
    """Return the nominal stationary-ground clutter ridge Doppler."""
    _, _, _, fc_hz, speed = _model_inputs(reported_context)
    wavelength = geometry.C / fc_hz
    return float(-2.0 * speed * math.sin(math.radians(_finite(theta_deg, "theta_deg"))) / wavelength)


def expected_phase_slope_rad_per_rad(
    theta_deg: float,
    slant_range_m: float,
    reported_context: Mapping[str, object],
    i: int,
    j: int,
    step_deg: float = 1.0e-3,
) -> float:
    """Return the local phase-vs-angle slope of the reported model."""
    step = _finite(step_deg, "step_deg")
    if step <= 0.0:
        raise ValueError("step_deg must be positive")
    model_inputs = _model_inputs(reported_context)
    plus = _expected_clutter_phase_from_inputs(
        theta_deg + step, slant_range_m, model_inputs, i, j
    )
    minus = _expected_clutter_phase_from_inputs(
        theta_deg - step, slant_range_m, model_inputs, i, j
    )
    difference = geometry.wrap_phase(plus - minus)
    return float(difference / math.radians(2.0 * step))


def _pair_phase(pair: Mapping[str, object], positions: np.ndarray, i: int, j: int) -> float | None:
    value = pair.get("right_minus_left_phase_rad")
    if positions[i, 0] == positions[j, 0]:
        value = pair.get("phase_rad")
    if value is None:
        return None
    result = _finite(value, "clutter phase")
    return geometry.wrap_phase(result)


def _energy_centroid(channels: np.ndarray) -> float | None:
    power = np.sum(np.abs(channels) ** 2, axis=1, dtype=np.float64)
    total = float(np.sum(power, dtype=np.float64))
    if total <= 0.0 or not math.isfinite(total):
        return None
    indices = np.arange(power.size, dtype=np.float64)
    return float(np.dot(indices, power) / total)


def _circular_mean(values: Sequence[float], weights: Sequence[float]) -> float | None:
    if not values:
        return None
    angles = np.asarray(values, dtype=np.float64)
    weight_array = np.asarray(weights, dtype=np.float64)
    valid = np.isfinite(angles) & np.isfinite(weight_array) & (weight_array > 0.0)
    if not np.any(valid):
        return None
    vector = np.sum(weight_array[valid] * np.exp(1j * angles[valid]))
    if abs(vector) <= 0.0 or not np.isfinite(vector.real) or not np.isfinite(vector.imag):
        return None
    return float(np.angle(vector))


def _linear_slope(x: Sequence[float], y: Sequence[float], name: str) -> float | None:
    x_array = np.asarray(x, dtype=np.float64)
    y_array = np.asarray(y, dtype=np.float64)
    valid = np.isfinite(x_array) & np.isfinite(y_array)
    if int(np.count_nonzero(valid)) < 3:
        return None
    x_valid = x_array[valid]
    y_valid = np.unwrap(y_array[valid])
    if np.ptp(x_valid) <= 0.0:
        return None
    design = np.column_stack((x_valid, np.ones_like(x_valid)))
    try:
        coefficient, _, rank, _ = np.linalg.lstsq(design, y_valid, rcond=None)
    except np.linalg.LinAlgError as exc:
        raise ValueError(f"{name} slope fit failed") from exc
    if int(rank) < 2 or not np.all(np.isfinite(coefficient)):
        return None
    return float(coefficient[0])


def _packet_beam_id(packet: Mapping[str, object], index: int, context: Mapping[str, object]) -> int:
    if packet.get("beam_id") is not None:
        value = int(packet["beam_id"])
        if value < 0:
            raise ValueError("beam_id must be non-negative")
        return value
    waveform = _section(context, "waveform")
    scan = _section(context, "reported_scan", "scan")
    pulses = int(scan.get("pulses_per_beam", waveform.get("pulse_num", 0)))
    if pulses <= 0:
        raise ValueError("beam_id is required when reported pulse_num is absent")
    return index // pulses


def extract_clutter_features(
    target_free_packets: Iterable[Mapping[str, object]],
    reported_context: Mapping[str, object],
) -> dict[str, object]:
    """Extract one beam/pair feature row from target-free packet IQ only."""
    _validate_reported_context(reported_context)
    positions = _reported_positions(reported_context)
    metadata = _reported_metadata(reported_context)
    packets = list(target_free_packets)
    if not packets:
        raise ValueError("target-free packet stream is empty")
    grouped: dict[int, list[dict[str, object]]] = {}
    source_id = str(reported_context.get("source_id", "target_free_c_plus_n"))
    for packet_index, packet_value in enumerate(packets):
        packet = _mapping(packet_value, f"packet[{packet_index}]")
        for key in packet:
            if _restricted_name(key):
                raise ValueError(f"target/truth-related packet key is not allowed: {key}")
        unknown_packet_fields = set(packet) - _PACKET_FIELDS
        if unknown_packet_fields:
            raise ValueError(
                f"unsupported target-free packet fields: {sorted(unknown_packet_fields)}"
            )
        channels = np.asarray(packet.get("channels"), dtype=np.complex128)
        if channels.ndim != 2 or channels.shape[1] != 4 or channels.shape[0] <= 0:
            raise ValueError("target-free packet channels must have shape (sample_count, 4)")
        if not np.all(np.isfinite(channels.real)) or not np.all(np.isfinite(channels.imag)):
            raise ValueError("target-free packet channels must be finite")
        theta_value = packet.get("theta_deg", packet.get("theta_cmd_deg"))
        theta = _finite(theta_value, f"packet[{packet_index}].theta_deg")
        beam_id = _packet_beam_id(packet, packet_index, reported_context)
        range_sample = packet.get("range_sample", packet.get("range_bin"))
        if range_sample is None:
            range_sample = _energy_centroid(channels)
        if range_sample is None:
            raise ValueError(f"packet[{packet_index}] has no usable reported range sample")
        slant = packet.get("slant_range_m")
        if slant is None:
            slant = geometry.slant_range_from_sample_m(_finite(range_sample, "range_sample"), metadata)
        slant_range = _finite(slant, f"packet[{packet_index}].slant_range_m")
        if slant_range <= 0.0:
            raise ValueError("slant_range_m must be positive")
        observable = observables.compute_pair_observables(
            channels,
            channel_positions_m=positions,
        )
        pair_values: dict[str, float | None] = {}
        pair_coherence: dict[str, float | None] = {}
        for pair_name, i, j in PAIR_DEFINITIONS:
            pair = _mapping(observable["pairs"][pair_name], f"pair {pair_name}")
            pair_values[pair_name] = _pair_phase(pair, positions, i, j)
            coherence = pair.get("coherence")
            pair_coherence[pair_name] = None if coherence is None else _finite(coherence, "coherence")
        power = float(np.mean(np.abs(channels) ** 2, dtype=np.float64))
        power_db = None if power <= 0.0 else float(10.0 * math.log10(power))
        channel_means = np.mean(channels, axis=0)
        selected_channel = int(np.argmax(np.abs(channel_means)))
        slow_signal = complex(channel_means[selected_channel])
        grouped.setdefault(beam_id, []).append({
            "theta_deg": theta,
            "slant_range_m": slant_range,
            "beam_id": beam_id,
            "pair_values": pair_values,
            "pair_coherence": pair_coherence,
            "power_db": power_db,
            "slow_signal": slow_signal,
            "prt_counter": int(packet.get("prt_counter", packet_index)),
            "source_id": str(packet.get("source_id", source_id)),
        })

    beam_values: dict[int, dict[str, object]] = {}
    prf_hz = _prf_hz(reported_context)
    for beam_id, values in sorted(grouped.items()):
        theta = float(np.median([float(value["theta_deg"]) for value in values]))
        slant = float(np.median([float(value["slant_range_m"]) for value in values]))
        pair_values: dict[str, float | None] = {}
        pair_coherence: dict[str, float | None] = {}
        for pair_name, _, _ in PAIR_DEFINITIONS:
            phases = [
                float(value["pair_values"][pair_name])
                for value in values
                if value["pair_values"][pair_name] is not None
            ]
            coherences = [
                max(0.05, float(value["pair_coherence"][pair_name]))
                for value in values
                if value["pair_coherence"][pair_name] is not None
            ]
            pair_values[pair_name] = _circular_mean(phases, coherences)
            pair_coherence[pair_name] = (
                float(np.mean(coherences)) if coherences else None
            )
        signals = np.asarray([value["slow_signal"] for value in values], dtype=np.complex128)
        times = np.asarray([int(value["prt_counter"]) for value in values], dtype=np.float64) / prf_hz
        valid_signal = np.isfinite(signals.real) & np.isfinite(signals.imag) & (np.abs(signals) > 1.0e-12)
        doppler: float | None = None
        if int(np.count_nonzero(valid_signal)) >= 3 and np.ptp(times[valid_signal]) > 0.0:
            phase = np.unwrap(np.angle(signals[valid_signal]))
            doppler = float(np.polyfit(times[valid_signal], phase, 1)[0] / (2.0 * math.pi))
        powers = [float(value["power_db"]) for value in values if value["power_db"] is not None]
        beam_values[beam_id] = {
            "theta_deg": theta,
            "slant_range_m": slant,
            "pair_values": pair_values,
            "pair_coherence": pair_coherence,
            "doppler_ridge_hz": doppler,
            "power_db": float(np.mean(powers)) if powers else None,
            "source_ids": sorted({str(value["source_id"]) for value in values}),
        }

    phase_slopes: dict[str, float | None] = {}
    beam_ids = sorted(beam_values)
    for pair_name, _, _ in PAIR_DEFINITIONS:
        phase_slopes[pair_name] = _linear_slope(
            [float(beam_values[beam_id]["theta_deg"]) for beam_id in beam_ids],
            [
                float(beam_values[beam_id]["pair_values"][pair_name])
                for beam_id in beam_ids
                if beam_values[beam_id]["pair_values"][pair_name] is not None
            ][: len(beam_ids)],
            f"{pair_name} clutter phase",
        )

    rows: list[dict[str, object]] = []
    for beam_id in beam_ids:
        beam = beam_values[beam_id]
        for pair_name, _, _ in PAIR_DEFINITIONS:
            rows.append({
                "theta_deg": beam["theta_deg"],
                "slant_range_m": beam["slant_range_m"],
                "beam_id": beam_id,
                "pair_id": pair_name,
                "clutter_phase_rad": beam["pair_values"][pair_name],
                "doppler_ridge_hz": beam["doppler_ridge_hz"],
                "phase_slope_rad_per_rad": phase_slopes[pair_name],
                "power_db": beam["power_db"],
                "coherence": beam["pair_coherence"][pair_name],
                "source_id": ";".join(beam["source_ids"]),
            })
    return {
        "schema": FEATURE_SCHEMA,
        "rows": rows,
        "reported_context": dict(reported_context),
        "causal_data_sources": list(CAUSAL_DATA_SOURCES),
        "contains_target_truth": False,
        "contains_on_off_difference": False,
        "source_ids": sorted({str(row["source_id"]) for row in rows}),
        "input_audit": {
            "target_free_c_plus_n_only": True,
            "target_truth_used": False,
            "known_error_used": False,
            "servo_truth_used": False,
            "on_off_difference_used": False,
        },
    }


def load_target_free_packets(
    off_raw_path: Path | str,
    reported_context: Mapping[str, object],
) -> list[dict[str, object]]:
    """Load exactly one target-free C+N raw stream with reported metadata."""
    _validate_reported_context(reported_context)
    waveform = _section(reported_context, "waveform")
    pulse_len = int(waveform.get("pulse_len", 0))
    channel_count = int(waveform.get("new_protocol_channel_count", 4))
    iq_data_type = str(waveform.get("iq_data_type", "float32"))
    if pulse_len <= 0 or channel_count != 4:
        raise ValueError("target-free loader requires waveform pulse_len and four channels")
    scan = _section(reported_context, "reported_scan", "scan")
    pulses_per_beam = int(scan.get("pulses_per_beam", waveform.get("pulse_num", 1)))
    if pulses_per_beam <= 0:
        raise ValueError("reported pulse_num must be positive")
    packets: list[dict[str, object]] = []
    for index, packet in enumerate(
        observables.iter_raw_packets(Path(off_raw_path), pulse_len, channel_count, iq_data_type)
    ):
        item = dict(packet)
        item["beam_id"] = index // pulses_per_beam
        packets.append(item)
    if not packets:
        raise ValueError(f"target-free raw stream is empty: {off_raw_path}")
    return packets


def _wrap_residual(value: np.ndarray | float) -> np.ndarray | float:
    if isinstance(value, np.ndarray):
        return np.arctan2(np.sin(value), np.cos(value))
    return geometry.wrap_phase(float(value))


def _robust_scale(values: Sequence[float], floor: float) -> float:
    array = np.asarray(values, dtype=np.float64)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return float(floor)
    center = float(np.median(array))
    mad = float(np.median(np.abs(array - center)))
    scale = 1.4826 * mad
    if scale <= 0.0:
        scale = float(np.std(array))
    return max(float(floor), scale)


def _huber(value: np.ndarray | float, threshold: float = 1.345) -> np.ndarray | float:
    array = np.asarray(value, dtype=np.float64)
    absolute = np.abs(array)
    result = np.where(
        absolute <= threshold,
        0.5 * array * array,
        threshold * (absolute - 0.5 * threshold),
    )
    return float(result) if result.ndim == 0 else result


def _feature_rows(features: Mapping[str, object]) -> list[dict[str, object]]:
    rows_value = features.get("rows")
    if not isinstance(rows_value, Sequence) or isinstance(rows_value, (str, bytes)):
        raise ValueError("clutter features must contain a rows sequence")
    rows: list[dict[str, object]] = []
    for index, value in enumerate(rows_value):
        row = _mapping(value, f"feature row {index}")
        unknown = set(row) - FEATURE_FIELDS
        if unknown:
            raise ValueError(f"unsupported clutter feature fields: {sorted(unknown)}")
        if any(_restricted_name(key) for key in row):
            raise ValueError("target/truth-related feature fields are not allowed")
        rows.append(dict(row))
    if not rows:
        raise ValueError("clutter features contain no rows")
    return rows


def _unique_beam_rows(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    selected: dict[int, dict[str, object]] = {}
    for row in rows:
        beam = int(row["beam_id"])
        selected.setdefault(beam, dict(row))
    return [selected[key] for key in sorted(selected)]


def _family_records(
    rows: Sequence[Mapping[str, object]],
    family: str,
) -> list[dict[str, object]]:
    if family == "phase":
        return [
            dict(row) for row in rows
            if row.get("clutter_phase_rad") is not None
            and math.isfinite(float(row["clutter_phase_rad"]))
        ]
    if family == "ridge":
        return [
            dict(row) for row in _unique_beam_rows(rows)
            if row.get("doppler_ridge_hz") is not None
            and math.isfinite(float(row["doppler_ridge_hz"]))
        ]
    if family == "p38":
        return [
            dict(row) for row in rows
            if row.get("phase_slope_rad_per_rad") is not None
            and math.isfinite(float(row["phase_slope_rad_per_rad"]))
        ]
    if family == "power":
        return [
            dict(row) for row in _unique_beam_rows(rows)
            if row.get("power_db") is not None
            and math.isfinite(float(row["power_db"]))
        ]
    raise ValueError(f"unsupported feature family: {family}")


def _phase_prediction(
    row: Mapping[str, object],
    delta: float,
    context: Mapping[str, object],
    model_inputs: tuple[dict[str, object], np.ndarray, np.ndarray, float, float] | None = None,
) -> float:
    pair_name = str(row["pair_id"])
    pair_lookup = {name: (i, j) for name, i, j in PAIR_DEFINITIONS}
    if pair_name not in pair_lookup:
        raise ValueError(f"unsupported pair_id: {pair_name}")
    return _expected_clutter_phase_from_inputs(
        float(row["theta_deg"]) + delta,
        float(row["slant_range_m"]),
        _model_inputs(context) if model_inputs is None else model_inputs,
        *pair_lookup[pair_name],
    )


def _phase_prediction_tables(
    records: Sequence[Mapping[str, object]],
    grid: np.ndarray,
    model_inputs: tuple[dict[str, object], np.ndarray, np.ndarray, float, float],
) -> dict[str, np.ndarray]:
    pair_lookup = {name: (i, j) for name, i, j in PAIR_DEFINITIONS}
    tables: dict[str, np.ndarray] = {}
    for pair_name, i, j in PAIR_DEFINITIONS:
        selected = [row for row in records if str(row["pair_id"]) == pair_name]
        if not selected:
            continue
        tables[pair_name] = _vectorized_phase_predictions(
            np.asarray([float(row["theta_deg"]) for row in selected], dtype=np.float64),
            np.asarray([float(row["slant_range_m"]) for row in selected], dtype=np.float64),
            grid,
            model_inputs,
            *pair_lookup[pair_name],
        )
    return tables


def _p38_prediction_table(
    records: Sequence[Mapping[str, object]],
    grid: np.ndarray,
    model_inputs: tuple[dict[str, object], np.ndarray, np.ndarray, float, float],
) -> np.ndarray:
    pair_lookup = {name: (i, j) for name, i, j in PAIR_DEFINITIONS}
    step_deg = 1.0e-3
    output = np.zeros((len(records), grid.size), dtype=np.float64)
    for pair_name, i, j in PAIR_DEFINITIONS:
        indices = [index for index, row in enumerate(records) if str(row["pair_id"]) == pair_name]
        if not indices:
            continue
        theta = np.asarray([float(records[index]["theta_deg"]) for index in indices])
        slant = np.asarray([float(records[index]["slant_range_m"]) for index in indices])
        plus = _vectorized_phase_predictions(theta + step_deg, slant, grid, model_inputs, i, j)
        minus = _vectorized_phase_predictions(theta - step_deg, slant, grid, model_inputs, i, j)
        values = np.arctan2(np.sin(plus - minus), np.cos(plus - minus)) / math.radians(2.0 * step_deg)
        output[np.asarray(indices), :] = values
    return output


def _ridge_prediction_table(
    records: Sequence[Mapping[str, object]],
    grid: np.ndarray,
    model_inputs: tuple[dict[str, object], np.ndarray, np.ndarray, float, float],
) -> np.ndarray:
    _, _, _, fc_hz, speed = model_inputs
    wavelength = geometry.C / fc_hz
    theta = np.asarray([float(row["theta_deg"]) for row in records], dtype=np.float64).reshape(-1, 1)
    return -2.0 * speed * np.sin(np.radians(theta + grid.reshape(1, -1))) / wavelength


def _phase_component(
    records: Sequence[Mapping[str, object]],
    delta: float,
    context: Mapping[str, object],
    scale: float,
    model_inputs: tuple[dict[str, object], np.ndarray, np.ndarray, float, float] | None = None,
    candidate_index: int | None = None,
    prediction_tables: Mapping[str, np.ndarray] | None = None,
) -> tuple[float, float]:
    values: list[float] = []
    weights: list[float] = []
    for pair_name, _, _ in PAIR_DEFINITIONS:
        pair_rows = [row for row in records if str(row["pair_id"]) == pair_name]
        if len(pair_rows) < 3:
            continue
        if prediction_tables is not None and candidate_index is not None:
            predicted = prediction_tables[pair_name][:, candidate_index]
        else:
            predicted = np.asarray([
                _phase_prediction(row, delta, context, model_inputs)
                for row in pair_rows
            ])
        residual = np.asarray([
            float(_wrap_residual(float(row["clutter_phase_rad"]) - prediction))
            for row, prediction in zip(pair_rows, predicted)
        ])
        coherence = np.asarray([
            max(0.05, float(row.get("coherence", 1.0))) for row in pair_rows
        ])
        intercept = _circular_mean(residual.tolist(), coherence.tolist()) or 0.0
        values.extend([float(_wrap_residual(value - intercept)) for value in residual])
        weights.extend(coherence.tolist())
    if not values:
        return math.inf, math.inf
    normalized = np.asarray(values, dtype=np.float64) / scale
    return float(np.sum(np.asarray(weights) * _huber(normalized)) / len(values)), float(
        math.sqrt(np.average(np.asarray(values) ** 2, weights=np.asarray(weights)))
    )


def _ridge_component(
    records: Sequence[Mapping[str, object]],
    delta: float,
    context: Mapping[str, object],
    scale: float,
    model_inputs: tuple[dict[str, object], np.ndarray, np.ndarray, float, float] | None = None,
    candidate_index: int | None = None,
    prediction_table: np.ndarray | None = None,
) -> tuple[float, float]:
    if len(records) < 3:
        return math.inf, math.inf
    observed = np.asarray([float(row["doppler_ridge_hz"]) for row in records])
    if prediction_table is not None and candidate_index is not None:
        predicted = prediction_table[:, candidate_index]
    else:
        if model_inputs is None:
            model_inputs = _model_inputs(context)
        _, _, _, fc_hz, speed = model_inputs
        wavelength = geometry.C / fc_hz
        predicted = np.asarray([
            -2.0 * speed * math.sin(math.radians(float(row["theta_deg"]) + delta)) / wavelength
            for row in records
        ])
    intercept = float(np.median(observed - predicted))
    residual = observed - predicted - intercept
    normalized = residual / scale
    return float(np.mean(_huber(normalized))), float(math.sqrt(np.mean(residual * residual)))


def _p38_component(
    records: Sequence[Mapping[str, object]],
    delta: float,
    context: Mapping[str, object],
    scale: float,
    model_inputs: tuple[dict[str, object], np.ndarray, np.ndarray, float, float] | None = None,
    candidate_index: int | None = None,
    prediction_table: np.ndarray | None = None,
) -> tuple[float, float]:
    if len(records) < 3:
        return math.inf, math.inf
    if prediction_table is not None and candidate_index is not None:
        values = [
            float(row["phase_slope_rad_per_rad"]) - float(prediction_table[index, candidate_index])
            for index, row in enumerate(records)
        ]
    else:
        if model_inputs is None:
            model_inputs = _model_inputs(context)
        values = []
        for row in records:
            pair_name = str(row["pair_id"])
            pair_lookup = {name: (i, j) for name, i, j in PAIR_DEFINITIONS}
            if pair_name not in pair_lookup:
                continue
            i, j = pair_lookup[pair_name]
            step_deg = 1.0e-3
            plus = _expected_clutter_phase_from_inputs(
                float(row["theta_deg"]) + delta + step_deg,
                float(row["slant_range_m"]),
                model_inputs,
                i,
                j,
            )
            minus = _expected_clutter_phase_from_inputs(
                float(row["theta_deg"]) + delta - step_deg,
                float(row["slant_range_m"]),
                model_inputs,
                i,
                j,
            )
            expected_slope = geometry.wrap_phase(plus - minus) / math.radians(2.0 * step_deg)
            values.append(float(row["phase_slope_rad_per_rad"]) - expected_slope)
    if not values:
        return math.inf, math.inf
    residual = np.asarray(values, dtype=np.float64)
    return float(np.mean(_huber(residual / scale))), float(math.sqrt(np.mean(residual * residual)))


def _power_component(
    records: Sequence[Mapping[str, object]], delta: float, context: Mapping[str, object], scale: float
) -> tuple[float, float]:
    if len(records) < 3:
        return math.inf, math.inf
    scan = _section(context, "reported_scan", "scan")
    beam_width = max(1.0e-6, _finite(scan.get("beam_width_deg", 4.0), "beam_width_deg"))
    observed = np.asarray([float(row["power_db"]) for row in records])
    theta = np.asarray([float(row["theta_deg"]) + delta for row in records])
    normalized_offset = np.clip(theta / beam_width, -0.999, 0.999)
    predicted = 20.0 * np.log10(np.maximum(np.cos(0.5 * math.pi * normalized_offset), 1.0e-3))
    intercept = float(np.median(observed - predicted))
    residual = observed - predicted - intercept
    return float(np.mean(_huber(residual / scale))), float(math.sqrt(np.mean(residual * residual)))


def _grid(config: Mapping[str, object]) -> tuple[np.ndarray, float]:
    values = dict(DEFAULT_GRID)
    values.update({key: config[key] for key in DEFAULT_GRID if key in config})
    low = _finite(values["grid_min_deg"], "grid_min_deg")
    high = _finite(values["grid_max_deg"], "grid_max_deg")
    step = _finite(values["grid_step_deg"], "grid_step_deg")
    if step <= 0.0 or high <= low:
        raise ValueError("servo grid requires grid_max_deg > grid_min_deg and positive step")
    count = int(round((high - low) / step))
    if count <= 0 or count > 2_000_000 or not math.isclose(low + count * step, high, abs_tol=1.0e-9):
        raise ValueError("servo grid range must be an integral, bounded number of steps")
    return low + step * np.arange(count + 1, dtype=np.float64), step


def _result_base(
    rows: Sequence[Mapping[str, object]],
    features: Mapping[str, object],
) -> dict[str, object]:
    source_ids = sorted({str(row.get("source_id", "unknown")) for row in rows})
    return {
        "schema": ESTIMATE_SCHEMA,
        "method": METHOD,
        "estimate_deg": None,
        "uncertainty_deg": None,
        "fit_rmse": None,
        "phase_rmse": None,
        "ridge_rmse": None,
        "p38_rmse": None,
        "clutter_cancellation_db": None,
        "model_mismatch": False,
        "fallback_reason": None,
        "status": "FALLBACK_UNIDENTIFIABLE",
        "action": "KEEP_CURRENT",
        "causal_data_sources": list(CAUSAL_DATA_SOURCES),
        "source_ids": source_ids,
        "contains_target_truth": bool(features.get("contains_target_truth", False)),
        "contains_on_off_difference": bool(features.get("contains_on_off_difference", False)),
        "input_audit": {
            "target_free_c_plus_n_only": True,
            "target_truth_used": False,
            "known_error_used": False,
            "servo_truth_used": False,
            "on_off_difference_used": False,
        },
    }


def estimate_clutter_only_servo(
    features: Mapping[str, object],
    grid_config: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Fit a servo angle from target-free feature rows with a fixed grid."""
    if not isinstance(features, Mapping):
        raise ValueError("features must be an object returned by extract_clutter_features")
    rows = _feature_rows(features)
    context = features.get("reported_context")
    if not isinstance(context, Mapping):
        raise ValueError("clutter features must retain reported_context")
    _validate_reported_context(context)
    if bool(features.get("contains_target_truth", False)):
        raise ValueError("target truth is not allowed in clutter-only estimator")
    if bool(features.get("contains_on_off_difference", False)):
        raise ValueError("ON-OFF difference is not allowed in clutter-only estimator")
    config = {} if grid_config is None else dict(grid_config)
    for key in config:
        if _restricted_name(key):
            raise ValueError(f"target/truth-related grid key is not allowed: {key}")
    grid, step = _grid(config)
    model_inputs = _model_inputs(context)
    weights = dict(DEFAULT_WEIGHTS)
    configured_weights = config.get("weights", {})
    if configured_weights is not None:
        configured_weights = _mapping(configured_weights, "weights")
        for key, value in configured_weights.items():
            if key not in weights:
                raise ValueError(f"unsupported feature weight: {key}")
            weights[key] = _finite(value, f"weights.{key}")
            if weights[key] < 0.0:
                raise ValueError("feature weights must be non-negative")

    records = {family: _family_records(rows, family) for family in (*PRIMARY_FAMILIES, "power")}
    valid_primary = [
        family for family in PRIMARY_FAMILIES
        if len(records[family]) >= 3
        and (family != "phase" or len({str(row["pair_id"]) for row in records[family]}) >= 2)
    ]
    result = _result_base(rows, features)
    result["feature_counts"] = {family: len(records[family]) for family in records}
    if len(valid_primary) < 2:
        result.update({
            "status": "FALLBACK_MODEL_MISMATCH",
            "model_mismatch": True,
            "fallback_reason": "insufficient_primary_feature_families",
        })
        return result

    scales = {
        "phase": _robust_scale(
            [float(row["clutter_phase_rad"]) for row in records["phase"]], 0.05
        ),
        "ridge": _robust_scale(
            [float(row["doppler_ridge_hz"]) for row in records["ridge"]], 1.0
        ),
        "p38": _robust_scale(
            [float(row["phase_slope_rad_per_rad"]) for row in records["p38"]], 0.01
        ),
        "power": _robust_scale(
            [float(row["power_db"]) for row in records["power"]], 0.1
        ),
    }
    family_scores: dict[str, np.ndarray] = {}
    family_rmse: dict[str, np.ndarray] = {}
    phase_tables = _phase_prediction_tables(records["phase"], grid, model_inputs)
    ridge_table = _ridge_prediction_table(records["ridge"], grid, model_inputs)
    p38_table = _p38_prediction_table(records["p38"], grid, model_inputs)
    for family in (*PRIMARY_FAMILIES, "power"):
        if not records[family] or weights[family] == 0.0:
            continue
        scores: list[float] = []
        rmses: list[float] = []
        for candidate_index, candidate in enumerate(grid):
            if family == "phase":
                score, rmse = _phase_component(
                    records[family],
                    float(candidate),
                    context,
                    scales[family],
                    model_inputs,
                    candidate_index,
                    phase_tables,
                )
            elif family == "ridge":
                score, rmse = _ridge_component(
                    records[family],
                    float(candidate),
                    context,
                    scales[family],
                    model_inputs,
                    candidate_index,
                    ridge_table,
                )
            elif family == "p38":
                score, rmse = _p38_component(
                    records[family],
                    float(candidate),
                    context,
                    scales[family],
                    model_inputs,
                    candidate_index,
                    p38_table,
                )
            else:
                score, rmse = _power_component(records[family], float(candidate), context, scales[family])
            scores.append(float(score))
            rmses.append(float(rmse))
        family_scores[family] = np.asarray(scores, dtype=np.float64)
        family_rmse[family] = np.asarray(rmses, dtype=np.float64)

    total = np.zeros(grid.size, dtype=np.float64)
    for family, scores in family_scores.items():
        total += weights[family] * scores
    valid_total = np.isfinite(total)
    if not np.any(valid_total):
        result.update({
            "status": "FALLBACK_UNIDENTIFIABLE",
            "fallback_reason": "no_finite_objective",
        })
        return result
    best_index = int(np.nanargmin(total))
    best_value = float(total[best_index])
    family_estimates = {
        family: float(grid[int(np.nanargmin(scores))])
        for family, scores in family_scores.items()
        if family in valid_primary and np.any(np.isfinite(scores))
    }
    family_primary_values = [family_estimates[family] for family in valid_primary if family in family_estimates]
    family_spread = (
        max(family_primary_values) - min(family_primary_values)
        if family_primary_values else math.inf
    )
    nearby = np.flatnonzero(total <= best_value + max(1.0e-12, 1.0e-12 * max(1.0, abs(best_value))))
    boundary = best_index == 0 or best_index == grid.size - 1
    unique = nearby.size == 1 and not boundary
    mismatch_reason: str | None = None
    if family_spread > max(0.08, 4.0 * step):
        mismatch_reason = "primary_feature_family_disagreement"
    elif not unique:
        mismatch_reason = "objective_non_unique_or_grid_boundary"
    if mismatch_reason is not None:
        result.update({
            "status": "FALLBACK_MODEL_MISMATCH",
            "model_mismatch": True,
            "fallback_reason": mismatch_reason,
            "family_estimates_deg": family_estimates,
            "family_spread_deg": family_spread,
            "objective_min": best_value,
            "objective_components": {
                family: float(weights[family] * scores[best_index])
                for family, scores in family_scores.items()
            },
        })
        return result

    estimate = float(grid[best_index])
    left = float(total[best_index - 1])
    right = float(total[best_index + 1])
    curvature = (left - 2.0 * best_value + right) / (step * step)
    uncertainty = max(step, min(0.75, math.sqrt(2.0 / max(curvature, 1.0e-12))))
    components = {
        family: float(weights[family] * scores[best_index])
        for family, scores in family_scores.items()
    }
    rmse_values = {
        family: float(family_rmse[family][best_index])
        for family in family_rmse
        if np.isfinite(family_rmse[family][best_index])
    }
    action = "NO_CORRECTION_NEEDED" if abs(estimate) <= uncertainty else "APPLY_ESTIMATED_CORRECTION"
    result.update({
        "estimate_deg": estimate,
        "uncertainty_deg": uncertainty,
        "fit_rmse": float(math.sqrt(best_value / max(sum(weights.values()), 1.0))),
        "phase_rmse": rmse_values.get("phase"),
        "ridge_rmse": rmse_values.get("ridge"),
        "p38_rmse": rmse_values.get("p38"),
        "status": "VALID",
        "action": action,
        "model_mismatch": False,
        "fallback_reason": None,
        "objective_min": best_value,
        "objective_curvature": curvature,
        "objective_components": components,
        "family_estimates_deg": family_estimates,
        "family_spread_deg": family_spread,
        "feature_scales": scales,
    })
    return result


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(_json_safe(value), stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="one target-free C+N four-channel raw file")
    parser.add_argument("--context", type=Path, help="JSON reported-context file")
    parser.add_argument("--output", type=Path, help="optional JSON output path")
    parser.add_argument("--seed", type=int, default=0, help="recorded deterministic seed")
    args = parser.parse_args(argv)
    if args.input is None or args.context is None:
        parser.error("--input and --context are required unless --help is requested")
    context = json.loads(args.context.read_text(encoding="utf-8"))
    packets = load_target_free_packets(args.input, context)
    features = extract_clutter_features(packets, context)
    result = estimate_clutter_only_servo(features, {"seed": args.seed})
    if args.output is not None:
        _write_json(args.output, result)
    print(json.dumps(_json_safe(result), ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result["status"] == "VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
