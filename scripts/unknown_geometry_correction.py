#!/usr/bin/env python3
"""Apply a nominal-model correction for an estimated four-channel geometry offset.

The public correction path accepts one scalar estimated first-axis offset and
nominal metadata only.  It does not load Stage2 truth, an ideal reference, or
the known-error value.  The separate known-error upper-bound in an evaluator
may call the same function with the evaluator-supplied scalar.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np


C = 299_792_458.0
HEADER_BYTES = 256
THETA_OFFSET_BYTES = 218


def _finite(value: object, name: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _local_look_vector(
    theta_deg: float,
    metadata: Mapping[str, object],
    platform_velocity_enu_mps: tuple[float, float] | None = None,
) -> np.ndarray:
    geometry = _object(metadata.get("simulation_geometry", {}), "simulation_geometry")
    platform = _object(metadata.get("platform", {}), "platform")
    source = str(geometry.get("platform_heading_source", "fixed_angle"))
    if source == "velocity" and platform_velocity_enu_mps is not None:
        ve, vn = platform_velocity_enu_mps
        heading_deg = math.degrees(math.atan2(vn, ve))
    else:
        heading_deg = _finite(
            platform.get("heading_deg", geometry.get("platform_heading_deg", 90.0)),
            "platform heading",
        )
    squint_side = int(
        platform.get("squint_side", geometry.get("squint_side", 1))
    )
    theta_offset = _finite(
        geometry.get("beam_theta_offset_deg", 0.0), "beam theta offset"
    )
    side_dir = -90.0 if squint_side == 1 else 90.0
    beam_center_dir = side_dir - (theta_deg + theta_offset)
    target_azimuth = heading_deg - beam_center_dir
    east = math.cos(math.radians(target_azimuth))
    north = math.sin(math.radians(target_azimuth))

    def component(axis: str) -> tuple[float, float]:
        if axis in {"east", "track_right"}:
            return 1.0, 0.0
        if axis in {"west", "track_left"}:
            return -1.0, 0.0
        if axis in {"north", "track_forward"}:
            return 0.0, 1.0
        if axis in {"south", "track_backward"}:
            return 0.0, -1.0
        raise ValueError(f"unsupported local axis: {axis}")

    x_e, x_n = component(str(geometry.get("local_x_axis", "north")))
    y_e, y_n = component(str(geometry.get("local_y_axis", "east")))
    return np.asarray(
        [x_e * east + x_n * north, y_e * east + y_n * north], dtype=np.float64
    )


def nominal_los_unit(
    theta_deg: float,
    range_sample: float,
    metadata: Mapping[str, object],
    platform_velocity_enu_mps: tuple[float, float] | None = None,
) -> np.ndarray:
    """Return the nominal local-frame LOS used by the Stage2 geometry model."""

    waveform = _object(metadata.get("waveform", {}), "waveform")
    platform = _object(metadata.get("platform", {}), "platform")
    geometry = _object(metadata.get("simulation_geometry", {}), "simulation_geometry")
    fs_hz = waveform.get("fs_hz")
    if fs_hz is None:
        fs_hz = _finite(waveform.get("fs_mhz", 0.0), "waveform.fs_mhz") * 1.0e6
    fs_hz = _finite(fs_hz, "waveform.fs_hz")
    if fs_hz <= 0.0:
        raise ValueError("waveform.fs_hz must be positive")
    sample_delay_sec = waveform.get("sample_delay_sec")
    if sample_delay_sec is None:
        range_processing = _object(
            metadata.get("range_processing", {}), "range_processing"
        )
        sample_delay_sec = _finite(
            range_processing.get("sample_delay_us", 0.0),
            "range_processing.sample_delay_us",
        ) * 1.0e-6
    sample_delay_sec = _finite(sample_delay_sec, "waveform.sample_delay_sec")
    slant_range = 0.5 * C * (
        _finite(range_sample, "range_sample") / fs_hz + sample_delay_sec
    )
    if slant_range <= 0.0:
        return np.full(3, np.nan, dtype=np.float64)
    height = _finite(platform.get("height_m", 6000.0), "platform.height_m")
    ground_z = _finite(_object(metadata.get("scene", {}), "scene").get("ground_z_m", 0.0), "scene.ground_z_m")
    use_ground = bool(geometry.get("use_ground_range_for_position", True))
    range_geometry = str(geometry.get("range_geometry", "algorithm"))
    ground_range = slant_range
    if use_ground and range_geometry != "slant":
        ground_range = math.sqrt(max(0.0, slant_range * slant_range - (height - ground_z) ** 2))
    horizontal = _local_look_vector(
        _finite(theta_deg, "theta_deg"), metadata, platform_velocity_enu_mps
    )
    los = np.asarray(
        [
            horizontal[0] * ground_range / slant_range,
            horizontal[1] * ground_range / slant_range,
            (ground_z - height) / slant_range,
        ],
        dtype=np.float64,
    )
    length = float(np.linalg.norm(los))
    if length <= 0.0 or not math.isfinite(length):
        return np.full(3, np.nan, dtype=np.float64)
    return los / length


def geometry_phase_error_rad(
    theta_deg: float,
    range_sample: float,
    metadata: Mapping[str, object],
    delta_m: float,
    platform_velocity_enu_mps: tuple[float, float] | None = None,
) -> float:
    """Return true-minus-reported carrier phase for channels 2 and 4."""

    delta = _finite(delta_m, "delta_m")
    waveform = _object(metadata.get("waveform", {}), "waveform")
    fc_hz = _finite(waveform.get("fc_hz", 0.0), "waveform.fc_hz")
    if fc_hz <= 0.0:
        raise ValueError("waveform.fc_hz must be positive")
    carrier_phase_sign = int(metadata.get("carrier_phase_sign", -1))
    los = nominal_los_unit(
        theta_deg, range_sample, metadata, platform_velocity_enu_mps
    )
    if not np.all(np.isfinite(los)):
        return math.nan
    # Stage2 uses sign*2*pi/lambda times the receive path.  For a small
    # reported-to-true displacement d, |target-(platform+d)|-|target-platform|
    # is -dot(los,d) to first order.
    return float(
        carrier_phase_sign * 2.0 * math.pi / (C / fc_hz) * (-los[0] * delta)
    )


def _geometry_phase_error_vector(
    theta_deg: float,
    samples: np.ndarray,
    metadata: Mapping[str, object],
    delta_m: float,
    platform_velocity_enu_mps: tuple[float, float] | None = None,
) -> np.ndarray:
    """Vectorized form of :func:`geometry_phase_error_rad` for one packet.

    Stage2 headers normally keep the beam angle and platform velocity fixed
    over a packet.  Keeping the scalar public model and using this equivalent
    vector form in the file loop avoids millions of Python scalar calls while
    preserving the same nominal reported-geometry model.
    """

    delta = _finite(delta_m, "delta_m")
    waveform = _object(metadata.get("waveform", {}), "waveform")
    platform = _object(metadata.get("platform", {}), "platform")
    geometry = _object(metadata.get("simulation_geometry", {}), "simulation_geometry")
    fc_hz = _finite(waveform.get("fc_hz", 0.0), "waveform.fc_hz")
    if fc_hz <= 0.0:
        raise ValueError("waveform.fc_hz must be positive")
    fs_hz = waveform.get("fs_hz")
    if fs_hz is None:
        fs_hz = _finite(waveform.get("fs_mhz", 0.0), "waveform.fs_mhz") * 1.0e6
    fs_hz = _finite(fs_hz, "waveform.fs_hz")
    if fs_hz <= 0.0:
        raise ValueError("waveform.fs_hz must be positive")
    sample_delay_sec = waveform.get("sample_delay_sec")
    if sample_delay_sec is None:
        range_processing = _object(metadata.get("range_processing", {}), "range_processing")
        sample_delay_sec = _finite(
            range_processing.get("sample_delay_us", 0.0),
            "range_processing.sample_delay_us",
        ) * 1.0e-6
    sample_delay_sec = _finite(sample_delay_sec, "waveform.sample_delay_sec")
    slant_range = 0.5 * C * (np.asarray(samples, dtype=np.float64) / fs_hz + sample_delay_sec)
    height = _finite(platform.get("height_m", 6000.0), "platform.height_m")
    ground_z = _finite(_object(metadata.get("scene", {}), "scene").get("ground_z_m", 0.0), "scene.ground_z_m")
    use_ground = bool(geometry.get("use_ground_range_for_position", True))
    range_geometry = str(geometry.get("range_geometry", "algorithm"))
    ground_range = slant_range.copy()
    if use_ground and range_geometry != "slant":
        ground_range = np.sqrt(np.maximum(0.0, slant_range * slant_range - (height - ground_z) ** 2))
    horizontal = _local_look_vector(theta_deg, metadata, platform_velocity_enu_mps)
    los_x = horizontal[0] * ground_range / slant_range
    carrier_phase_sign = int(metadata.get("carrier_phase_sign", -1))
    return carrier_phase_sign * 2.0 * math.pi / (C / fc_hz) * (-los_x * delta)


def decide_unknown_geometry_correction(
    estimated_delta_m: float | None,
    uncertainty_m: float,
    deadband_m: float,
) -> dict[str, object]:
    """Decide whether an estimate is large enough to leave Current.

    ``deadband_m`` is an evaluation-calibrated minimum effect size and
    ``uncertainty_m`` is the current estimate-quality allowance.  The larger
    one is used as the no-action threshold.  This function does not alter IQ;
    callers must route ``fallback_current`` to the uncorrected path.
    """

    uncertainty = _finite(uncertainty_m, "uncertainty_m")
    deadband = _finite(deadband_m, "deadband_m")
    if uncertainty < 0.0 or deadband < 0.0:
        raise ValueError("uncertainty_m and deadband_m must be non-negative")
    if estimated_delta_m is None:
        return {
            "schema": "unknown_geometry_correction_decision_v1",
            "status": "FALLBACK_UNIDENTIFIABLE",
            "action": "fallback_current",
            "estimated_delta_m": None,
            "uncertainty_m": uncertainty,
            "deadband_m": deadband,
            "effective_threshold_m": max(uncertainty, deadband),
            "apply_delta_m": 0.0,
        }
    estimate = _finite(estimated_delta_m, "estimated_delta_m")
    threshold = max(uncertainty, deadband)
    no_correction = abs(estimate) <= threshold
    return {
        "schema": "unknown_geometry_correction_decision_v1",
        "status": "NO_CORRECTION_NEEDED" if no_correction else "APPLY_ESTIMATED_CORRECTION",
        "action": "fallback_current" if no_correction else "apply_estimate",
        "estimated_delta_m": estimate,
        "uncertainty_m": uncertainty,
        "deadband_m": deadband,
        "effective_threshold_m": threshold,
        "apply_delta_m": 0.0 if no_correction else estimate,
    }


def derive_deadband_from_sweep(
    rows: Sequence[Mapping[str, object]],
    truth_key: str = "truth_delta_m",
    estimate_key: str = "six_estimate_m",
) -> dict[str, object]:
    """Derive a no-action band from a truth-labelled baseline sweep.

    The zero-error absolute estimate is the measured estimator/model floor.
    Symmetric non-zero levels independently report local sensitivity, so the
    band is tied to the sweep rather than an arbitrary subtraction constant.
    This helper is evaluator-side; it is not called by the blind estimator.
    """

    grouped: dict[float, list[float]] = {}
    for row in rows:
        try:
            truth = float(row[truth_key])
            estimate = float(row[estimate_key])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(truth) or not math.isfinite(estimate):
            continue
        grouped.setdefault(truth, []).append(estimate)
    zero_values = grouped.get(0.0, [])
    if not zero_values:
        raise ValueError("baseline sweep must contain a finite zero-error level")
    zero_errors = [abs(value) for value in zero_values]
    slopes: list[float] = []
    for level in sorted(grouped):
        if level <= 0.0 or -level not in grouped:
            continue
        plus = sum(grouped[level]) / len(grouped[level])
        minus = sum(grouped[-level]) / len(grouped[-level])
        slope = (plus - minus) / (2.0 * level)
        if math.isfinite(slope):
            slopes.append(slope)
    if not slopes:
        raise ValueError("baseline sweep must contain at least one symmetric non-zero pair")
    sensitivity = float(np.median(np.asarray(slopes, dtype=np.float64)))
    deadband = float(max(zero_errors))
    return {
        "schema": "unknown_geometry_deadband_from_sweep_v1",
        "zero_level_case_count": len(zero_values),
        "zero_level_max_abs_error_m": deadband,
        "zero_level_mean_abs_error_m": float(sum(zero_errors) / len(zero_errors)),
        "symmetric_sensitivity_m_per_m": sensitivity,
        "symmetric_sensitivity_values_m_per_m": slopes,
        "deadband_m": deadband,
        "derivation": "deadband=max(|estimate-truth|) at truth=0; sensitivity=median symmetric finite difference",
        "uses_truth": True,
        "evaluator_only": True,
    }


def _iq_dtype(iq_data_type: str) -> np.dtype:
    normalized = iq_data_type.lower()
    if normalized in {"int16", "iq_int16", "short", "s16", "i16"}:
        return np.dtype("<i2")
    if normalized in {"float32", "float", "f32"}:
        return np.dtype("<f4")
    raise ValueError(f"unsupported iq_data_type: {iq_data_type}")


def apply_unknown_geometry_correction(
    source: Path | str,
    destination: Path | str,
    metadata: Mapping[str, object],
    estimated_delta_m: float,
    channel_indices: Sequence[int] = (2, 4),
    reported_channel_positions_m: Sequence[Sequence[float]] | None = None,
) -> dict[str, object]:
    """Correct raw NewProtocol IQ using only an estimated geometry scalar."""

    source_path = Path(source)
    destination_path = Path(destination)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if destination_path.exists():
        raise FileExistsError(destination_path)
    delta = _finite(estimated_delta_m, "estimated_delta_m")
    indices = tuple(int(index) - 1 for index in channel_indices)
    if not indices or any(index < 0 for index in indices):
        raise ValueError("channel_indices must contain positive channel numbers")
    waveform = _object(metadata.get("waveform", {}), "waveform")
    pulse_len = int(_finite(waveform.get("pulse_len", 0), "waveform.pulse_len"))
    channel_count = int(
        _finite(waveform.get("new_protocol_channel_count", 0), "waveform.new_protocol_channel_count")
    )
    if pulse_len <= 0 or channel_count <= 0 or any(index >= channel_count for index in indices):
        raise ValueError("invalid packet dimensions or correction channel")
    if reported_channel_positions_m is not None:
        if len(reported_channel_positions_m) != channel_count:
            raise ValueError("reported channel geometry length does not match packet")
        for position in reported_channel_positions_m:
            if len(position) != 3 or not all(math.isfinite(float(value)) for value in position):
                raise ValueError("reported channel positions must be finite xyz triples")
    dtype = _iq_dtype(str(waveform.get("iq_data_type", "float32")))
    scalar_bytes = dtype.itemsize
    packet_bytes = HEADER_BYTES + pulse_len * channel_count * 2 * scalar_bytes
    if source_path.resolve() == destination_path.resolve():
        raise ValueError("source and destination must differ")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    phase_abs_chunks: list[np.ndarray] = []
    phase_cache: dict[tuple[float, float | None, float | None], np.ndarray] = {}
    packet_count = 0
    with source_path.open("rb") as input_stream, destination_path.open("wb") as output_stream:
        while True:
            header = input_stream.read(HEADER_BYTES)
            if not header:
                break
            if len(header) != HEADER_BYTES:
                raise ValueError(f"truncated header at packet {packet_count}")
            declared = struct.unpack_from("<I", header, 9)[0]
            if declared != packet_bytes:
                raise ValueError(
                    f"packet {packet_count} declares {declared} bytes; expected {packet_bytes}"
                )
            payload = input_stream.read(packet_bytes - HEADER_BYTES)
            if len(payload) != packet_bytes - HEADER_BYTES:
                raise ValueError(f"truncated payload at packet {packet_count}")
            values = np.frombuffer(payload, dtype=dtype).copy()
            iq = values.reshape(pulse_len, channel_count, 2)
            channels = iq[:, :, 0].astype(np.float64) + 1j * iq[:, :, 1].astype(np.float64)
            theta_deg = struct.unpack_from("<h", header, THETA_OFFSET_BYTES)[0] / 100.0
            # NewProtocol stores platform velocity as vn@128, ve@132.
            vn = struct.unpack_from("<f", header, 128)[0]
            ve = struct.unpack_from("<f", header, 132)[0]
            velocity = (float(ve), float(vn)) if math.isfinite(ve) and math.isfinite(vn) else None
            cache_key = (
                round(theta_deg, 6),
                None if velocity is None else round(velocity[0], 6),
                None if velocity is None else round(velocity[1], 6),
            )
            phases = phase_cache.get(cache_key)
            if phases is None:
                phases = _geometry_phase_error_vector(
                    theta_deg,
                    np.arange(pulse_len, dtype=np.float64),
                    metadata,
                    delta,
                    velocity,
                )
                phase_cache[cache_key] = phases
            if not np.all(np.isfinite(phases)):
                raise ValueError(f"non-finite nominal phase model at packet {packet_count}")
            factor = np.exp(-1j * phases)
            for index in indices:
                channels[:, index] *= factor
                phase_abs_chunks.append(np.abs(phases))
            if dtype == np.dtype("<i2"):
                for index in indices:
                    iq[:, index, 0] = np.rint(
                        np.clip(channels[:, index].real, -32768.0, 32767.0)
                    ).astype(dtype)
                    iq[:, index, 1] = np.rint(
                        np.clip(channels[:, index].imag, -32768.0, 32767.0)
                    ).astype(dtype)
            else:
                for index in indices:
                    iq[:, index, 0] = channels[:, index].real.astype(dtype)
                    iq[:, index, 1] = channels[:, index].imag.astype(dtype)
            output_stream.write(header)
            output_stream.write(values.tobytes())
            packet_count += 1
    if packet_count == 0:
        raise ValueError("source contains no packets")
    phase_array = np.concatenate(phase_abs_chunks) if phase_abs_chunks else np.empty(0, dtype=np.float64)
    return {
        "schema": "unknown_geometry_correction_v1",
        "source": str(source_path),
        "destination": str(destination_path),
        "packet_count": packet_count,
        "pulse_len": pulse_len,
        "channel_count": channel_count,
        "corrected_channels": [int(index) + 1 for index in indices],
        "estimated_delta_m": delta,
        "phase_abs_mean_rad": float(np.mean(phase_array)),
        "phase_abs_p95_rad": float(np.percentile(phase_array, 95.0)),
        "phase_abs_max_rad": float(np.max(phase_array)),
        "uses_truth": False,
        "uses_ideal_reference": False,
    }


__all__ = [
    "apply_unknown_geometry_correction",
    "decide_unknown_geometry_correction",
    "derive_deadband_from_sweep",
    "geometry_phase_error_rad",
    "nominal_los_unit",
]
