#!/usr/bin/env python3
"""Run the target-free true/reported platform-velocity pilot.

The pilot separates the physical platform velocity from the velocity carried by
the protocol header.  The simulator uses ``velocity_true_mps`` for platform
motion, clutter phase and target geometry, while ``velocity_reported_mps`` is
written to the INS/header fields.  ``new_protocol_velocity_source=header`` is
explicitly selected so the reported value reaches the production CTDR/P38 and
motion-compensation path.

V0 is the current reported-header condition.  V1K is a known-error,
evaluation-only correction.  V1 is a deterministic blind estimator consuming
one target-free OFF C+N raw stream plus reported metadata; it never receives
true velocity, target truth, ON data, or the known error.  No AI model or
router is trained or invoked.  Core runs are optional and use the existing
GMTI_core entry point for an evaluation branch only.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import platform
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import analyze_stage3_p38 as p38  # noqa: E402
import run_unknown_system_error_servo_pilot as target_assisted  # noqa: E402


TEMPLATE = ROOT / "configs/research/unknown_system_error_end_to_end_pilot.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
GMTI_CORE = ROOT / "build/GMTI_core"
HEADER_BYTES = 256
OFF_VN_MPS = 128
OFF_VE_MPS = 132
OFF_VD_MPS = 136
OFF_SPEED_MPS = 140
OFF_THETA_DEG_X100 = 218
DEFAULT_TRUE_VELOCITY_MPS = 60.0
LIGHT_SPEED_MPS = 299792458.0
DEFAULT_DELTA_V_MPS = (0.0, -0.05, 0.05, -0.1, 0.1, -0.2, 0.2, -0.5, 0.5)
DEFAULT_SEEDS = (101, 202, 303)
DEFAULT_TEXTURES = ("low_texture", "high_texture")
DEFAULT_RANGES_M = (7500.0, 10000.0)
DEFAULT_MAX_SUPPORTED_ERROR_MPS = 1.0
BRANCH_NAMES = ("V0_Current", "V1K_Known_Correction", "V1_Blind_Deterministic")
CAUSAL_DATA_SOURCES = (
    "clutter_doppler_ridge_displacement",
    "p38_phase_slope",
    "ctdr_residual",
    "four_channel_slow_time_phase",
)
VELOCITY_SOURCE_CONTRACT = {
    "true_velocity_mps": {
        "meaning": "physical platform state used to generate the echo and truth",
        "consumers": [
            "true_platform_trajectory",
            "echo_phase",
            "clutter_doppler",
            "target_relative_geometry",
        ],
    },
    "reported_velocity_mps": {
        "meaning": "reported platform state available to the processing chain",
        "consumers": [
            "ins_header",
            "ctdr",
            "p38",
            "clutter_ridge_model",
            "steering",
            "velocity_conversion",
        ],
    },
}
TEXTURE_CONFIGS = {
    "low_texture": {
        "model": "continuous_texture",
        "mean_power": 0.05,
        "texture_sigma": 0.10,
        "spatial_cell_m": 20.0,
        "temporal_correlation_rho": 0.95,
    },
    "high_texture": {
        "model": "continuous_texture",
        "mean_power": 0.20,
        "texture_sigma": 0.65,
        "spatial_cell_m": 60.0,
        "temporal_correlation_rho": 0.995,
    },
}


def delta_velocity_levels() -> tuple[float, ...]:
    """Return the symmetric velocity-error matrix in m/s.

    The sign convention for this public study is
    ``delta_v_reported_minus_true_mps = reported - true``.  Estimator output
    uses the correction convention ``estimated_true_minus_reported_mps``.
    """

    return DEFAULT_DELTA_V_MPS


def require_velocity_pair(mapping: Mapping[str, object]) -> tuple[float, float]:
    """Require an explicit true/reported pair in a study-facing mapping."""

    true_present = "velocity_true_mps" in mapping
    reported_present = "velocity_reported_mps" in mapping
    if true_present != reported_present:
        raise ValueError("velocity_true_mps and velocity_reported_mps are required together")
    if not true_present:
        raise ValueError("legacy speed_mps is not sufficient for the split pilot")
    try:
        true_value = float(mapping["velocity_true_mps"])
        reported_value = float(mapping["velocity_reported_mps"])
    except (TypeError, ValueError) as exc:
        raise ValueError("velocity pair must be numeric") from exc
    if not all(math.isfinite(value) and value > 0.0 for value in (true_value, reported_value)):
        raise ValueError("velocity_true_mps and velocity_reported_mps must be finite and positive")
    return true_value, reported_value


def velocity_source_contract() -> dict[str, dict[str, object]]:
    """Return the explicit physical-versus-reported source mapping."""

    return copy.deepcopy(VELOCITY_SOURCE_CONTRACT)


def branch_contract() -> dict[str, dict[str, object]]:
    """Return the information conditions for V0, V1K and blind V1."""

    return {
        "V0_Current": {
            "name": "Current",
            "information_condition": "unknown_velocity_error",
            "calibration_mode": "current_reported_header",
            "estimator_input_role": "reported_header_without_correction",
            "known_error_evaluation_only": False,
            "target_truth_used": False,
            "causal_data_sources": ["reported INS/header velocity"],
        },
        "V1K_Known_Correction": {
            "name": "Known velocity correction",
            "information_condition": "known_velocity_error",
            "calibration_mode": "known_error_upper_bound",
            "estimator_input_role": "evaluation_only_known_error",
            "known_error_evaluation_only": True,
            "target_truth_used": False,
            "causal_data_sources": ["configured true-minus-reported velocity error"],
        },
        "V1_Blind_Deterministic": {
            "name": "Blind deterministic velocity estimator",
            "information_condition": "unknown_velocity_error",
            "calibration_mode": "target_free_deterministic",
            "estimator_input_role": "OFF_C_PLUS_N_TARGET_FREE_ONLY",
            "known_error_evaluation_only": False,
            "target_truth_used": False,
            "causal_data_sources": list(CAUSAL_DATA_SOURCES),
        },
    }


def _contains_key(value: object, needles: Sequence[str], path: str = "input") -> Optional[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).lower()
            if any(needle in key_text for needle in needles):
                return f"{path}.{key}"
            found = _contains_key(child, needles, f"{path}.{key}")
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found = _contains_key(child, needles, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def estimator_input_audit(
    input_paths: Sequence[str | Path],
    context: Mapping[str, object],
    *,
    contains_target_truth: bool,
    contains_known_velocity_error: bool,
    contains_true_velocity: bool,
) -> dict[str, object]:
    """Audit the blind estimator information boundary.

    Only OFF target-free input paths and a reported-only context are accepted.
    The context is deliberately checked recursively because a nested true or
    known field would otherwise be an information leak even if its top-level
    name looked harmless.
    """

    normalized = [str(path).replace("\\", "/") for path in input_paths]
    if not normalized:
        raise ValueError("blind velocity estimator requires an OFF input path")
    if any("/on/" in path or path.endswith("/on") or "/to/" in path or path.endswith("/to") for path in normalized):
        raise ValueError("blind velocity estimator accepts OFF C+N only")
    if contains_target_truth or contains_known_velocity_error or contains_true_velocity:
        raise ValueError("blind velocity estimator received truth or known velocity information")
    forbidden = _contains_key(
        context,
        ("true_velocity", "velocity_true", "known_velocity", "delta_velocity", "target_truth", "servo_truth"),
    )
    if forbidden is not None:
        raise ValueError(f"blind velocity context contains forbidden field: {forbidden}")
    reported = context.get("velocity_reported_mps")
    if reported is None:
        platform_context = context.get("reported_platform", {})
        if isinstance(platform_context, Mapping):
            reported = platform_context.get("velocity_reported_mps", platform_context.get("speed_mps"))
    if reported is None:
        raise ValueError("blind velocity context must provide velocity_reported_mps")
    try:
        reported_value = float(reported)
    except (TypeError, ValueError) as exc:
        raise ValueError("reported velocity must be numeric") from exc
    if not math.isfinite(reported_value) or reported_value <= 0.0:
        raise ValueError("reported velocity must be finite and positive")
    return {
        "input_paths": normalized,
        "off_only": True,
        "input_role": "OFF_C_PLUS_N_TARGET_FREE_ONLY",
        "contains_target_truth": False,
        "contains_known_velocity_error": False,
        "contains_true_velocity": False,
        "reported_velocity_mps": reported_value,
    }


def _finite_candidates(values: Mapping[str, object]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, value in values.items():
        if value is None or value == "":
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0.0:
            result[key] = number
    return result


def combine_velocity_observables(
    *,
    reported_velocity_mps: float,
    ridge_velocity_mps: Optional[float],
    p38_velocity_mps: Optional[float],
    ctdr_velocity_mps: Optional[float],
    slow_time_velocity_mps: Optional[float],
    residuals: Mapping[str, float],
    max_supported_error_mps: float = DEFAULT_MAX_SUPPORTED_ERROR_MPS,
) -> dict[str, object]:
    """Fuse deterministic target-free velocity candidates with a quality gate."""

    if not math.isfinite(float(reported_velocity_mps)) or float(reported_velocity_mps) <= 0.0:
        raise ValueError("reported_velocity_mps must be finite and positive")
    if not math.isfinite(float(max_supported_error_mps)) or float(max_supported_error_mps) <= 0.0:
        raise ValueError("max_supported_error_mps must be finite and positive")
    candidates = _finite_candidates({
        "ridge": ridge_velocity_mps,
        "p38": p38_velocity_mps,
        "ctdr": ctdr_velocity_mps,
        "slow_time": slow_time_velocity_mps,
    })
    if not candidates:
        raise ValueError("at least one finite velocity observable is required")
    values = np.asarray(list(candidates.values()), dtype=np.float64)
    estimate = float(np.mean(values))
    spread = float(np.max(values) - np.min(values)) if values.size else math.nan
    residual_values = [float(value) for value in residuals.values() if math.isfinite(float(value))]
    max_abs_residual = max((abs(value) for value in residual_values), default=None)
    if values.size < 2:
        status = "FALLBACK_UNIDENTIFIABLE"
        fallback_reason = "fewer_than_two_independent_velocity_observables"
        model_mismatch = False
    elif spread > 20.0:
        status = "FALLBACK_MODEL_MISMATCH"
        fallback_reason = "velocity_observable_disagreement_over_20_mps"
        model_mismatch = True
    elif max_abs_residual is not None and max_abs_residual > float(max_supported_error_mps):
        status = "FALLBACK_MODEL_MISMATCH"
        fallback_reason = "velocity_candidate_outside_supported_error_bound"
        model_mismatch = True
    else:
        status = "VALID"
        fallback_reason = None
        model_mismatch = False
    if residual_values:
        residual_rmse = float(np.sqrt(np.mean(np.square(residual_values))))
    else:
        residual_rmse = float(np.std(values)) if values.size > 1 else None
    return {
        "status": status,
        "estimate_velocity_mps": estimate,
        "estimate_delta_v_mps": estimate - float(reported_velocity_mps),
        "reported_velocity_mps": float(reported_velocity_mps),
        "candidate_velocities_mps": candidates,
        "candidate_count": int(values.size),
        "candidate_spread_mps": spread,
        "max_abs_residual_mps": max_abs_residual,
        "max_supported_error_mps": float(max_supported_error_mps),
        "uncertainty_mps": None if residual_rmse is None else max(0.01, residual_rmse),
        "residual_rmse_mps": residual_rmse,
        "model_mismatch": model_mismatch,
        "fallback_reason": fallback_reason,
        "causal_data_sources": list(CAUSAL_DATA_SOURCES),
        "target_truth_used": False,
        "known_velocity_error_used": False,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(_json_safe(value), stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def _write_rows(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not materialized:
        path.write_text("\n", encoding="utf-8")
        return
    fields: list[str] = []
    for row in materialized:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(_json_safe(materialized))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(*args: str) -> str:
    command = ["git", *args]
    if not (ROOT / ".git").exists() and (ROOT / ".git-real").is_dir():
        command = ["git", "--git-dir=.git-real", "--work-tree=.", *args]
    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _run_logged(command: Sequence[str], log_path: Path, env: Optional[Mapping[str, str]] = None) -> tuple[int, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(str(item) for item in command) + "\n")
        completed = subprocess.run(
            [str(item) for item in command],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=None if env is None else dict(env),
            check=False,
        )
        log.write(f"exit_code={completed.returncode}\n")
    return int(completed.returncode), time.perf_counter() - started


def _probe_gpu() -> dict[str, object]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,pstate,temperature.gpu,power.draw,power.limit,utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return {"returncode": result.returncode, "output": result.stdout}


def _probe_disk() -> dict[str, object]:
    result = subprocess.run(
        ["df", "-h", str(ROOT), "/tmp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return {"returncode": result.returncode, "output": result.stdout}


def _parse_float_list(values: Sequence[str], name: str) -> list[float]:
    result: list[float] = []
    for raw in values:
        for item in raw.split(","):
            if item.strip():
                value = float(item)
                if not math.isfinite(value):
                    raise ValueError(f"{name} must be finite: {item}")
                result.append(value)
    if not result:
        raise ValueError(f"{name} cannot be empty")
    return result


def _parse_int_list(values: Sequence[str], name: str) -> list[int]:
    result: list[int] = []
    for raw in values:
        for item in raw.split(","):
            if item.strip():
                result.append(int(item))
    if not result:
        raise ValueError(f"{name} cannot be empty")
    return result


def _texture_config(texture: str) -> Mapping[str, object]:
    if texture not in TEXTURE_CONFIGS:
        raise ValueError(f"unsupported texture: {texture}")
    return TEXTURE_CONFIGS[texture]


def _range_sample(range_center_m: float, config: Mapping[str, object]) -> int:
    waveform = config.get("waveform", {})
    processing = config.get("range_processing", {})
    if not isinstance(waveform, Mapping) or not isinstance(processing, Mapping):
        raise ValueError("waveform/range_processing are required")
    fs_hz = float(waveform.get("fs_mhz", 60.0)) * 1.0e6
    delay_sec = float(processing.get("sample_delay_us", 0.0)) * 1.0e-6
    sample = (2.0 * float(range_center_m) / LIGHT_SPEED_MPS - delay_sec) * fs_hz
    if not math.isfinite(sample) or sample <= 0.0:
        raise ValueError("range center does not map to a positive sample")
    return int(round(sample))


def _prepare_template(template: Mapping[str, object], compact_input: bool) -> dict[str, object]:
    config = target_assisted._prepare_template(template)
    waveform = config.get("waveform")
    processing = config.get("range_processing")
    if not isinstance(waveform, dict) or not isinstance(processing, dict):
        raise ValueError("prepared waveform/range_processing must be objects")
    if compact_input:
        waveform.update({"pulse_len": 4096, "pulse_num": 16})
        processing.update({"range_fft_len": 4096, "range_crop_start": 0, "range_crop_len": 4096})
    else:
        waveform.update({"pulse_len": 8192, "pulse_num": 130})
        processing.update({"range_fft_len": 8192, "range_crop_start": 0, "range_crop_len": 4096})
    config["truth_output"] = False
    config["scene_mode"] = "full"
    config.pop("paired_background_output_dir", None)
    targets = config.get("targets")
    if not isinstance(targets, list) or not targets or not isinstance(targets[0], dict):
        raise ValueError("prepared target list is invalid")
    targets[0]["enabled"] = False
    targets[0]["target_id"] = "VELOCITY_STUDY_TARGET_DISABLED"
    return config


def _case_config(
    template: Mapping[str, object],
    case_root: Path,
    delta_reported_minus_true_mps: float,
    seed: int,
    texture: str,
    range_center_m: float,
) -> dict[str, object]:
    config = copy.deepcopy(template)
    tag = f"{delta_reported_minus_true_mps:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")
    case_id = f"velocity_error_{tag}mps_{texture}_{range_center_m:.0f}m_seed_{seed}"
    config["case_id"] = case_id
    config["output_dir"] = str((case_root / "off").resolve())
    random_cfg = config.get("random")
    if not isinstance(random_cfg, dict):
        raise ValueError("prepared random config is invalid")
    random_cfg.update({"random_seed": int(seed), "period_start": 0, "period_count": 1, "beam_start": 1, "beam_count": 5})
    scene = config.get("scene")
    if not isinstance(scene, dict):
        raise ValueError("prepared scene is invalid")
    scene.update({
        "range_min_m": float(range_center_m) - 500.0,
        "range_max_m": float(range_center_m) + 500.0,
        "mode": "full",
        "output_signal_domain": "raw_lfm",
        "signal_only": False,
    })
    area = scene.get("area_clutter")
    if not isinstance(area, dict):
        raise ValueError("scene.area_clutter is invalid")
    area.update(_texture_config(texture))
    platform_cfg = config.get("platform")
    if not isinstance(platform_cfg, dict):
        raise ValueError("platform config is required")
    true_velocity = DEFAULT_TRUE_VELOCITY_MPS
    reported_velocity = true_velocity + float(delta_reported_minus_true_mps)
    if reported_velocity <= 0.0:
        raise ValueError("reported velocity must stay positive")
    platform_cfg.update({
        "velocity_true_mps": true_velocity,
        "velocity_reported_mps": reported_velocity,
        # C++ keeps speed_mps as a legacy alias and requires it to equal the
        # reported value when an explicit split is present.
        "speed_mps": reported_velocity,
        "velocity_source": "header",
    })
    return config


def _find_period_file(case_root: Path) -> Path:
    manifest = case_root / "data/period_files.csv"
    if not manifest.is_file():
        raise RuntimeError(f"missing period manifest: {manifest}")
    with manifest.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise RuntimeError(f"velocity study requires one period: {manifest} -> {len(rows)}")
    raw = Path(rows[0]["file"])
    candidates = [raw] if raw.is_absolute() else [case_root / raw, ROOT / raw, raw]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(f"missing period raw file: {candidates}")


def _channel_positions(config: Mapping[str, object]) -> list[list[float]]:
    geometry = config.get("channel_geometry")
    if not isinstance(geometry, Mapping):
        raise ValueError("channel_geometry is required")
    positions: list[list[float]] = []
    for index in range(1, 5):
        item = geometry.get(f"channel_{index}")
        if not isinstance(item, Mapping):
            raise ValueError(f"channel_{index} geometry is required")
        reported = item.get("reported", item)
        if not isinstance(reported, Mapping):
            raise ValueError(f"channel_{index}.reported geometry is invalid")
        positions.append([float(reported["x_m"]), float(reported["y_m"]), float(reported["z_m"])])
    return positions


def _reported_context(config: Mapping[str, object], source_id: str, range_center_m: float) -> dict[str, object]:
    waveform = config.get("waveform")
    scan = config.get("scan")
    platform = config.get("platform")
    processing = config.get("range_processing")
    geometry = config.get("simulation_geometry")
    if not all(isinstance(item, Mapping) for item in (waveform, scan, platform, processing)):
        raise ValueError("reported context needs waveform/scan/platform/range_processing")
    assert isinstance(platform, Mapping)
    reported_velocity = float(
        platform.get("velocity_reported_mps", platform.get("speed_mps", 0.0))
    )
    reported_platform = {
        "speed_mps": reported_velocity,
        "velocity_reported_mps": reported_velocity,
        "height_m": float(platform.get("height_m", 6000.0)),
        "origin_lat_deg": float(platform.get("origin_lat_deg", 0.0)),
        "origin_lon_deg": float(platform.get("origin_lon_deg", 0.0)),
        "origin_alt_m": float(platform.get("origin_alt_m", 0.0)),
        "projection_ref_lon_deg": float(platform.get("projection_ref_lon_deg", 117.0)),
        "squint_side": int(platform.get("squint_side", 1)),
    }
    assert isinstance(waveform, Mapping)
    assert isinstance(scan, Mapping)
    assert isinstance(processing, Mapping)
    reported_waveform = dict(waveform)
    reported_scan = dict(scan)
    reported_scan["pulses_per_beam"] = int(reported_waveform.get("pulse_num", 0))
    prf_hz = float(reported_waveform.get("prf_hz", 0.0))
    pulse_num = int(reported_waveform.get("pulse_num", 0))
    beam_count = int(reported_scan.get("beam_count", 0))
    center_packet = (beam_count // 2) * pulse_num + pulse_num // 2
    reference_position = [
        reported_velocity * center_packet / prf_hz,
        0.0,
        reported_platform["height_m"],
    ]
    return {
        "velocity_reported_mps": reported_velocity,
        "reported_channel_positions_m": _channel_positions(config),
        "reported_platform": reported_platform,
        "reported_scan": reported_scan,
        "waveform": reported_waveform,
        "range_processing": dict(processing),
        "reported_simulation_geometry": dict(geometry) if isinstance(geometry, Mapping) else {},
        "reported_reference_platform_position_m": reference_position,
        "representative_range_sample": _range_sample(range_center_m, config),
        "source_id": source_id,
    }


def _load_header_velocities(path: Path, packet_bytes: int) -> dict[str, object]:
    data = path.read_bytes()
    if packet_bytes <= HEADER_BYTES or len(data) % packet_bytes != 0:
        raise ValueError(f"raw file does not match packet layout: {path}")
    vn: list[float] = []
    ve: list[float] = []
    vd: list[float] = []
    speed: list[float] = []
    theta: list[float] = []
    for index in range(len(data) // packet_bytes):
        offset = index * packet_bytes
        vn.append(float(struct.unpack_from("<f", data, offset + OFF_VN_MPS)[0]))
        ve.append(float(struct.unpack_from("<f", data, offset + OFF_VE_MPS)[0]))
        vd.append(float(struct.unpack_from("<f", data, offset + OFF_VD_MPS)[0]))
        speed.append(float(struct.unpack_from("<f", data, offset + OFF_SPEED_MPS)[0]))
        theta.append(float(struct.unpack_from("<h", data, offset + OFF_THETA_DEG_X100)[0]) / 100.0)
    return {
        "packet_count": len(vn),
        "vn_mps": vn,
        "ve_mps": ve,
        "vd_mps": vd,
        "speed_mps": speed,
        "theta_deg": theta,
    }


def _rewrite_header_velocity(source: Path, destination: Path, packet_bytes: int, target_velocity_mps: float) -> dict[str, object]:
    if not math.isfinite(target_velocity_mps) or target_velocity_mps <= 0.0:
        raise ValueError("target header velocity must be finite and positive")
    original = source.read_bytes()
    if packet_bytes <= HEADER_BYTES or len(original) % packet_bytes != 0:
        raise ValueError(f"raw file does not match packet layout: {source}")
    data = bytearray(original)
    for index in range(len(data) // packet_bytes):
        offset = index * packet_bytes
        struct.pack_into("<f", data, offset + OFF_VN_MPS, float(target_velocity_mps))
        struct.pack_into("<f", data, offset + OFF_VE_MPS, 0.0)
        struct.pack_into("<f", data, offset + OFF_VD_MPS, 0.0)
        struct.pack_into("<f", data, offset + OFF_SPEED_MPS, float(target_velocity_mps))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    source_payload = hashlib.sha256()
    destination_payload = hashlib.sha256()
    for begin in range(0, len(data), packet_bytes):
        source_payload.update(original[begin + HEADER_BYTES:begin + packet_bytes])
        destination_payload.update(data[begin + HEADER_BYTES:begin + packet_bytes])
    source_headers = _load_header_velocities(source, packet_bytes)
    destination_headers = _load_header_velocities(destination, packet_bytes)
    return {
        "source": str(source),
        "destination": str(destination),
        "packet_count": len(data) // packet_bytes,
        "source_first_vn_mps": source_headers["vn_mps"][0] if source_headers["vn_mps"] else None,
        "destination_first_vn_mps": destination_headers["vn_mps"][0] if destination_headers["vn_mps"] else None,
        "target_velocity_mps": float(target_velocity_mps),
        "payload_sha256": destination_payload.hexdigest(),
        "source_payload_sha256": source_payload.hexdigest(),
        "payload_unchanged": destination_payload.hexdigest() == source_payload.hexdigest(),
        "raw_sha256": _sha256(destination),
    }


def _weighted_line(x: np.ndarray, y: np.ndarray, weight: np.ndarray) -> tuple[float, float]:
    valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(weight) & (weight > 0.0)
    if int(np.count_nonzero(valid)) < 2:
        raise ValueError("too few finite line-fit samples")
    xv = x[valid]
    yv = y[valid]
    wv = weight[valid]
    sw = float(np.sum(wv))
    mx = float(np.sum(wv * xv) / sw)
    my = float(np.sum(wv * yv) / sw)
    dx = xv - mx
    variance = float(np.sum(wv * dx * dx))
    if variance <= np.finfo(float).eps * sw:
        raise ValueError("degenerate line-fit support")
    slope = float(np.sum(wv * dx * (yv - my)) / variance)
    return slope, my - slope * mx


def _parabolic_peak(frequency: np.ndarray, power: np.ndarray, index: int) -> float:
    if index <= 0 or index >= power.size - 1:
        return float(frequency[index])
    y0, y1, y2 = np.log(np.maximum(power[index - 1:index + 2], np.finfo(float).tiny))
    denominator = y0 - 2.0 * y1 + y2
    if abs(float(denominator)) <= 1.0e-12:
        return float(frequency[index])
    offset = 0.5 * float(y0 - y2) / float(denominator)
    step = float(frequency[index + 1] - frequency[index])
    return float(frequency[index] + np.clip(offset, -0.5, 0.5) * step)


def _beam_doppler_candidates(
    rc: np.ndarray,
    theta_deg: np.ndarray,
    context: Mapping[str, object],
    range_slice: slice,
) -> tuple[Optional[float], Optional[float], dict[str, object]]:
    waveform = context["waveform"]
    scan = context["reported_scan"]
    assert isinstance(waveform, Mapping)
    assert isinstance(scan, Mapping)
    prf_hz = float(waveform["prf_hz"])
    pulse_num = int(waveform["pulse_num"])
    fc_hz = float(waveform["fc_ghz"]) * 1.0e9
    wavelength = LIGHT_SPEED_MPS / fc_hz
    reported = float(context["velocity_reported_mps"])
    beam_count = int(scan["beam_count"])
    if rc.shape[0] != beam_count * pulse_num:
        raise ValueError("raw packet count does not match reported beam/pulse layout")
    grouped = rc.reshape(beam_count, pulse_num, rc.shape[1], rc.shape[2])
    angles = np.median(theta_deg.reshape(beam_count, pulse_num), axis=1)
    x_values: list[float] = []
    ridge_frequencies: list[float] = []
    ridge_speeds: list[float] = []
    slow_frequencies: list[float] = []
    slow_speeds: list[float] = []
    beam_rows: list[dict[str, object]] = []
    frequency = np.fft.fftshift(np.fft.fftfreq(pulse_num, d=1.0 / prf_hz))
    for beam_index, angle in enumerate(angles):
        projection = abs(math.sin(math.radians(float(angle))))
        if projection < 0.01:
            continue
        data = grouped[beam_index, :, range_slice, :]
        spectrum = np.fft.fftshift(np.fft.fft(data, axis=0), axes=0)
        power = np.sum(np.abs(spectrum) ** 2, axis=(1, 2), dtype=np.float64)
        expected = 2.0 * reported * projection / wavelength
        lower = max(1.0, 0.25 * expected)
        upper = min(0.5 * prf_hz - 1.0, 1.9 * expected + 20.0)
        valid = (np.abs(frequency) >= lower) & (np.abs(frequency) <= upper)
        if int(np.count_nonzero(valid)) < 1:
            continue
        masked_power = np.where(valid, power, -np.inf)
        peak_index = int(np.argmax(masked_power))
        if not math.isfinite(float(power[peak_index])) or power[peak_index] <= 0.0:
            continue
        peak_frequency = abs(_parabolic_peak(frequency, power, peak_index))
        speed = peak_frequency * wavelength / (2.0 * projection)
        adjacent = np.sum(data[1:] * np.conj(data[:-1]), axis=(1, 2), dtype=np.complex128)
        adjacent_sum = complex(np.sum(adjacent))
        slow_frequency = abs(float(np.angle(adjacent_sum))) * prf_hz / (2.0 * math.pi)
        slow_speed = slow_frequency * wavelength / (2.0 * projection)
        weight = float(power[peak_index]) / max(float(np.median(power[valid])), 1.0e-18)
        weight = min(100.0, max(1.0, weight))
        x_values.append(projection)
        ridge_frequencies.append(peak_frequency)
        ridge_speeds.append(speed)
        slow_frequencies.append(slow_frequency)
        slow_speeds.append(slow_speed)
        beam_rows.append({
            "beam_index_1based": beam_index + 1,
            "theta_reported_deg": float(angle),
            "sin_abs": projection,
            "expected_doppler_abs_hz_from_reported": expected,
            "ridge_doppler_abs_hz": peak_frequency,
            "ridge_velocity_mps": speed,
            "slow_time_doppler_abs_hz": slow_frequency,
            "slow_time_velocity_mps": slow_speed,
            "weight": weight,
        })
    if len(x_values) < 2:
        return None, None, {"beam_rows": beam_rows, "ridge_rmse_mps": None, "slow_time_rmse_mps": None}
    x = np.asarray(x_values, dtype=np.float64)
    ridge_y = np.asarray(ridge_frequencies, dtype=np.float64)
    slow_y = np.asarray(slow_frequencies, dtype=np.float64)
    weights = np.asarray([float(row["weight"]) for row in beam_rows], dtype=np.float64)
    try:
        ridge_slope, _ = _weighted_line(x, ridge_y, weights)
        slow_slope, _ = _weighted_line(x, slow_y, weights)
    except ValueError:
        return None, None, {"beam_rows": beam_rows, "ridge_rmse_mps": None, "slow_time_rmse_mps": None}
    ridge_velocity = abs(ridge_slope) * wavelength / 2.0
    slow_velocity = abs(slow_slope) * wavelength / 2.0
    ridge_prediction = ridge_slope * x
    slow_prediction = slow_slope * x
    ridge_rmse_hz = float(np.sqrt(np.average(np.square(ridge_y - ridge_prediction), weights=weights)))
    slow_rmse_hz = float(np.sqrt(np.average(np.square(slow_y - slow_prediction), weights=weights)))
    return ridge_velocity, slow_velocity, {
        "beam_rows": beam_rows,
        "ridge_slope_hz_per_sin": ridge_slope,
        "slow_time_slope_hz_per_sin": slow_slope,
        "ridge_rmse_mps": ridge_rmse_hz * wavelength / 2.0,
        "slow_time_rmse_mps": slow_rmse_hz * wavelength / 2.0,
        "ridge_velocity_candidates_mps": ridge_speeds,
        "slow_time_velocity_candidates_mps": slow_speeds,
    }


def _ctdr_candidate(rc: np.ndarray, context: Mapping[str, object], range_slice: slice) -> tuple[Optional[float], dict[str, object]]:
    waveform = context["waveform"]
    assert isinstance(waveform, Mapping)
    prf_hz = float(waveform["prf_hz"])
    d_chan_m = float(waveform.get("d_chan_m", 0.17))
    data = rc[:, range_slice, :]
    normalized: list[float] = []
    for lag in range(1, min(5, data.shape[0] // 4) + 1):
        cross = np.sum(data[lag:] * np.conj(data[:-lag]), dtype=np.complex128)
        denom = math.sqrt(
            float(np.sum(np.abs(data[lag:]) ** 2, dtype=np.float64))
            * float(np.sum(np.abs(data[:-lag]) ** 2, dtype=np.float64))
        )
        normalized.append(abs(complex(cross)) / denom if denom > 0.0 else 0.0)
    if not normalized or max(normalized) < 0.02:
        return None, {"lag_correlations": normalized, "selected_lag": None}
    selected_lag = int(np.argmax(np.asarray(normalized))) + 1
    equivalent_spacing = 0.5 * abs(d_chan_m)
    reported = float(context["velocity_reported_mps"])
    expected_lag = max(1, int(round(equivalent_spacing * prf_hz / reported)))
    # The raw temporal process has an AR(1) correlation in addition to the
    # configured CTDR delay.  A lag-1 maximum therefore is not evidence for a
    # different platform speed.  Keep it as an auditable residual, but only
    # expose a velocity candidate when the observed maximum supports the
    # reported CTDR lag.
    lag_supported = selected_lag == expected_lag
    estimate = equivalent_spacing * prf_hz / selected_lag
    return (float(estimate) if lag_supported else None), {
        "lag_correlations": normalized,
        "selected_lag": selected_lag,
        "expected_lag_from_reported": expected_lag,
        "lag_supported": lag_supported,
        "ctdr_residual_lag": selected_lag - expected_lag,
        "equivalent_two_way_spacing_m": equivalent_spacing,
    }


def _six_pair_phase_features(rc: np.ndarray, context: Mapping[str, object], range_slice: slice) -> dict[str, object]:
    positions = np.asarray(context["reported_channel_positions_m"], dtype=np.float64)
    grouped = rc.reshape(
        int(context["reported_scan"]["beam_count"]),
        int(context["waveform"]["pulse_num"]),
        rc.shape[1],
        rc.shape[2],
    )
    pairs = (("C13", 0, 2), ("C24", 1, 3), ("C12", 0, 1), ("C14", 0, 3), ("C23", 1, 2), ("C34", 2, 3))
    pair_features: dict[str, object] = {}
    all_rmse: list[float] = []
    all_coherence: list[float] = []
    for name, left, right in pairs:
        phases: list[float] = []
        coherences: list[float] = []
        for beam in grouped:
            cross = np.sum(beam[:, range_slice, left] * np.conj(beam[:, range_slice, right]), axis=1, dtype=np.complex128)
            power_left = np.sum(np.abs(beam[:, range_slice, left]) ** 2, dtype=np.float64)
            power_right = np.sum(np.abs(beam[:, range_slice, right]) ** 2, dtype=np.float64)
            denom = np.sqrt(power_left * power_right)
            valid = denom > 0.0
            if np.any(valid):
                phases.extend(np.angle(cross[valid]).tolist())
                coherences.extend(np.clip(np.abs(cross[valid]) / denom[valid], 0.0, 1.0).tolist())
        if phases:
            phase_array = np.asarray(phases, dtype=np.float64)
            center = float(np.angle(np.mean(np.exp(1j * phase_array))))
            residual = np.angle(np.exp(1j * (phase_array - center)))
            rmse = float(np.sqrt(np.mean(np.square(residual))))
            coherence = float(np.mean(coherences)) if coherences else None
        else:
            rmse = None
            coherence = None
        pair_features[name] = {"phase_rmse_rad": rmse, "mean_coherence": coherence}
        if rmse is not None:
            all_rmse.append(rmse)
        if coherence is not None:
            all_coherence.append(coherence)
    return {
        "pairs": pair_features,
        "six_pair_phase_rmse_rad": None if not all_rmse else float(np.mean(all_rmse)),
        "six_pair_mean_coherence": None if not all_coherence else float(np.mean(all_coherence)),
        "reported_channel_positions_used_m": positions.tolist(),
    }


def _p38_candidate(rc: np.ndarray, context: Mapping[str, object], range_slice: slice) -> tuple[Optional[float], dict[str, object]]:
    waveform = context["waveform"]
    scan = context["reported_scan"]
    assert isinstance(waveform, Mapping)
    assert isinstance(scan, Mapping)
    prf_hz = float(waveform["prf_hz"])
    pulse_num = int(waveform["pulse_num"])
    reported = float(context["velocity_reported_mps"])
    fc_hz = float(waveform["fc_ghz"]) * 1.0e9
    baseline_m = abs(float(waveform.get("d_chan_m", 0.17)))
    try:
        selected = np.arange(rc.shape[1])[range_slice]
        max_angle = max(abs(float(scan["scan_min_deg"])), abs(float(scan["scan_min_deg"]) + float(scan["scan_step_deg"]) * (int(scan["beam_count"]) - 1)))
        support_bw = max(160.0, 2.0 * reported * math.sin(math.radians(max_angle + 1.0)) / (LIGHT_SPEED_MPS / fc_hz))
        estimates: list[float] = []
        fit_rows: list[dict[str, object]] = []
        for beam_index in range(int(scan["beam_count"])):
            block = rc[beam_index * pulse_num:(beam_index + 1) * pulse_num]
            center = p38.estimate_doppler_center(block[:, range_slice, 0], prf_hz)
            raw_fft = np.fft.fftshift(np.fft.fft(block, axis=0), axes=0)
            recentered, shift = p38.production_dbs_recenter(raw_fft, center, prf_hz)
            bin_hz = prf_hz / pulse_num
            shifted_to_raw = np.asarray(shift["shifted_to_raw_fft_row"], dtype=np.int64)
            fa = -0.5 * prf_hz + np.arange(pulse_num, dtype=np.float64) * bin_hz + center
            support = np.abs(fa - center) <= min(0.49 * prf_hz, support_bw)
            f1 = 0.5 * (recentered[:, selected, 0] + recentered[:, selected, 2])
            f2 = 0.5 * (recentered[:, selected, 1] + recentered[:, selected, 3])
            cross = np.sum(f1 * np.conj(f2), axis=1)
            energy = np.sqrt(np.sum(np.abs(f1) ** 2, axis=1) * np.sum(np.abs(f2) ** 2, axis=1))
            fit = p38.robust_phase_fit(fa[support], cross[support], energy[support], min_coherence=0.03, min_peak_energy_fraction=0.0, inlier_threshold_rad=0.7)
            slope = float(fit["k"])
            estimate = math.pi * baseline_m / abs(slope) if abs(slope) > 1.0e-9 else math.nan
            valid = (
                math.isfinite(estimate)
                and 5.0 <= estimate <= 300.0
                and float(fit["inlier_ratio"]) >= 0.35
                and float(fit["rmse_rad"]) <= 1.2
            )
            fit_rows.append({
                "beam_index_1based": beam_index + 1,
                "fit_k_rad_per_hz": slope,
                "fit_rmse_rad": float(fit["rmse_rad"]),
                "fit_inlier_ratio": float(fit["inlier_ratio"]),
                "fit_sample_count": int(fit["sample_count"]),
                "doppler_center_hz": center,
                "dbs_start_raw_fft_row": int(shift["start_raw_fft_row"]),
                "support_raw_fft_rows": [int(value) for value in shifted_to_raw[np.flatnonzero(support)]],
                "velocity_mps": float(estimate) if valid else None,
                "valid": valid,
            })
            if valid:
                estimates.append(float(estimate))
        valid = len(estimates) >= 2
        return (float(np.median(np.asarray(estimates))) if valid else None), {
            "fit_rows": fit_rows,
            "velocity_candidates_mps": estimates,
            "fit_rmse_rad": float(np.mean([row["fit_rmse_rad"] for row in fit_rows])) if fit_rows else None,
            "fit_inlier_ratio": float(np.mean([row["fit_inlier_ratio"] for row in fit_rows])) if fit_rows else None,
            "support_bw_hz": support_bw,
            "valid": valid,
        }
    except (ValueError, FloatingPointError, IndexError, ZeroDivisionError) as exc:
        return None, {"valid": False, "fallback_reason": f"p38_observable_error:{exc}"}


def estimate_velocity_from_target_free_raw(
    raw_path: Path,
    context: Mapping[str, object],
    *,
    max_supported_error_mps: float = DEFAULT_MAX_SUPPORTED_ERROR_MPS,
) -> dict[str, object]:
    """Estimate true platform velocity from one target-free OFF raw stream."""

    audit = estimator_input_audit(
        [raw_path], context,
        contains_target_truth=False,
        contains_known_velocity_error=False,
        contains_true_velocity=False,
    )
    waveform = context.get("waveform")
    processing = context.get("range_processing")
    scan = context.get("reported_scan")
    if not isinstance(waveform, Mapping) or not isinstance(processing, Mapping) or not isinstance(scan, Mapping):
        raise ValueError("reported context is missing waveform/range/scan fields")
    pulse_num = int(waveform["pulse_num"])
    pulse_len = int(waveform["pulse_len"])
    channel_count = int(waveform.get("new_protocol_channel_count", 4))
    raw = p38.read_new_protocol(raw_path, pulse_num * int(scan["beam_count"]), pulse_len, channel_count)
    fs_hz = float(waveform["fs_mhz"]) * 1.0e6
    bandwidth_hz = float(waveform["bandwidth_mhz"]) * 1.0e6
    pulse_width_s = float(waveform["tr_us"]) * 1.0e-6
    fft_len = int(processing["range_fft_len"])
    crop_start = int(processing.get("range_crop_start", 0))
    crop_len = int(processing["range_crop_len"])
    rc = p38.range_compress(raw, fs_hz, bandwidth_hz, pulse_width_s, fft_len, crop_start, crop_len)
    center_sample = int(context["representative_range_sample"]) - crop_start
    center_sample = max(0, min(crop_len - 1, center_sample))
    half_window = min(160, max(16, crop_len // 12))
    lo = max(0, center_sample - half_window)
    hi = min(crop_len, center_sample + half_window + 1)
    if hi - lo < 8:
        raise ValueError("target-free range support is too short")
    range_slice = slice(lo, hi)
    theta = np.asarray(_load_header_velocities(raw_path, HEADER_BYTES + pulse_len * channel_count * 2 * 4)["theta_deg"], dtype=np.float64)
    ridge_velocity, slow_velocity, doppler_features = _beam_doppler_candidates(rc, theta, context, range_slice)
    ctdr_velocity, ctdr_features = _ctdr_candidate(rc, context, range_slice)
    p38_velocity, p38_features = _p38_candidate(rc, context, range_slice)
    pair_features = _six_pair_phase_features(rc, context, range_slice)
    beam_rows = doppler_features.get("beam_rows", [])
    power_by_beam: list[float] = []
    grouped = rc.reshape(int(scan["beam_count"]), pulse_num, crop_len, channel_count)
    for beam in grouped:
        power_by_beam.append(float(np.mean(np.abs(beam[:, range_slice, :]) ** 2)))
    power_array = np.asarray(power_by_beam, dtype=np.float64)
    power_spread_db = float(10.0 * np.log10(max(np.max(power_array), 1.0e-30) / max(np.min(power_array), 1.0e-30))) if power_array.size else None
    residuals = {
        "ridge": float(ridge_velocity - float(context["velocity_reported_mps"])) if ridge_velocity is not None else math.nan,
        "p38": float(p38_velocity - float(context["velocity_reported_mps"])) if p38_velocity is not None else math.nan,
        "ctdr": float(ctdr_velocity - float(context["velocity_reported_mps"])) if ctdr_velocity is not None else math.nan,
        "slow_time": float(slow_velocity - float(context["velocity_reported_mps"])) if slow_velocity is not None else math.nan,
    }
    combined = combine_velocity_observables(
        reported_velocity_mps=float(context["velocity_reported_mps"]),
        ridge_velocity_mps=ridge_velocity,
        p38_velocity_mps=p38_velocity,
        ctdr_velocity_mps=ctdr_velocity,
        slow_time_velocity_mps=slow_velocity,
        residuals=residuals,
        max_supported_error_mps=max_supported_error_mps,
    )
    result = {
        "method": "deterministic_target_free_clutter_velocity_fusion_v1",
        "input_role": "OFF_C_PLUS_N_TARGET_FREE_ONLY",
        "input_audit": audit,
        "raw_path": str(raw_path),
        "raw_sha256": _sha256(raw_path),
        "status": combined["status"],
        "estimate_velocity_mps": combined["estimate_velocity_mps"] if combined["status"] == "VALID" else None,
        "estimate_delta_v_mps": combined["estimate_delta_v_mps"] if combined["status"] == "VALID" else None,
        "reported_velocity_mps": combined["reported_velocity_mps"],
        "candidate_velocities_mps": combined["candidate_velocities_mps"],
        "candidate_count": combined["candidate_count"],
        "candidate_spread_mps": combined["candidate_spread_mps"],
        "max_abs_residual_mps": combined["max_abs_residual_mps"],
        "max_supported_error_mps": combined["max_supported_error_mps"],
        "uncertainty_mps": combined["uncertainty_mps"],
        "residual_rmse_mps": combined["residual_rmse_mps"],
        "ridge_velocity_mps": ridge_velocity,
        "p38_velocity_mps": p38_velocity,
        "ctdr_velocity_mps": ctdr_velocity,
        "slow_time_velocity_mps": slow_velocity,
        "ridge_rmse_mps": doppler_features.get("ridge_rmse_mps"),
        "slow_time_rmse_mps": doppler_features.get("slow_time_rmse_mps"),
        "p38_phase_slope_rmse_rad": p38_features.get("fit_rmse_rad"),
        "p38_phase_slope_rad_per_hz": p38_features.get("fit_k_rad_per_hz"),
        "six_pair_clutter_phase_rmse_rad": pair_features.get("six_pair_phase_rmse_rad"),
        "six_pair_mean_coherence": pair_features.get("six_pair_mean_coherence"),
        "multi_beam_power_spread_db": power_spread_db,
        "range_support": {"first_crop_bin": lo, "last_crop_bin": hi - 1, "center_crop_bin": center_sample},
        "doppler_observables": doppler_features,
        "ctdr_observable": ctdr_features,
        "p38_observable": p38_features,
        "six_pair_observables": pair_features,
        "clutter_cancellation_db": None,
        "model_mismatch": combined["model_mismatch"],
        "fallback_reason": combined["fallback_reason"],
        "causal_data_sources": list(CAUSAL_DATA_SOURCES),
        "target_truth_used": False,
        "known_velocity_error_used": False,
        "ai_training": False,
        "ai_router": False,
    }
    return result


def _decision_row(case: Mapping[str, object], branch: str, estimate: Mapping[str, object]) -> dict[str, object]:
    delta_reported_minus_true = float(case["delta_velocity_reported_minus_true_mps"])
    reported = float(case["velocity_reported_mps"])
    true_velocity = float(case["velocity_true_mps"])
    if branch == "V0_Current":
        status = "CURRENT"
        action = "KEEP_CURRENT"
        correction = 0.0
        source = "reported_header_current"
        estimate_delta = None
        known_used = False
        audit = {"off_only": False, "contains_true_velocity": False, "contains_known_velocity_error": False, "contains_target_truth": False, "input_paths": [case["raw_path"]]}
    elif branch == "V1K_Known_Correction":
        status = "KNOWN_EVALUATION"
        action = "NO_CORRECTION_NEEDED" if abs(delta_reported_minus_true) < 1.0e-12 else "APPLY_KNOWN_ERROR_CORRECTION"
        correction = true_velocity - reported
        source = "known_velocity_error_upper_bound"
        estimate_delta = correction
        known_used = True
        audit = {"off_only": False, "contains_true_velocity": False, "contains_known_velocity_error": True, "contains_target_truth": False, "input_paths": [case["raw_path"]]}
    else:
        estimate_delta = estimate.get("estimate_delta_v_mps")
        uncertainty = float(estimate["uncertainty_mps"]) if estimate.get("uncertainty_mps") not in (None, "") else 0.0
        if estimate.get("status") != "VALID" or estimate_delta is None:
            status = str(estimate.get("status", "FALLBACK_UNIDENTIFIABLE"))
            action = "KEEP_CURRENT"
            correction = 0.0
            source = "fallback_current"
        elif abs(float(estimate_delta)) <= max(0.05, uncertainty):
            status = "VALID_WITHIN_DEADBAND"
            action = "NO_CORRECTION_NEEDED"
            correction = 0.0
            source = "deadband_current"
        else:
            status = "VALID"
            action = "APPLY_ESTIMATED_CORRECTION"
            correction = float(estimate_delta)
            source = "blind_deterministic_estimate"
        known_used = False
        audit = estimate.get("input_audit", {})
    return {
        "case_id": case["case_id"],
        "branch": branch,
        "delta_velocity_reported_minus_true_mps": delta_reported_minus_true,
        "delta_velocity_true_minus_reported_mps": -delta_reported_minus_true,
        "velocity_true_mps_evaluation_only": true_velocity,
        "velocity_reported_mps": reported,
        "status": status,
        "decision": action,
        "estimate_delta_v_mps": estimate_delta,
        "applied_header_correction_mps": correction,
        "correction_source": source,
        "known_velocity_error_used": known_used,
        "target_truth_used": bool(audit.get("contains_target_truth", False)),
        "contains_true_velocity": bool(audit.get("contains_true_velocity", False)),
        "input_role": branch_contract()[branch]["estimator_input_role"],
        "input_paths": audit.get("input_paths", [case["raw_path"]]),
        "causal_data_sources": (
            list(CAUSAL_DATA_SOURCES)
            if branch == "V1_Blind_Deterministic"
            else branch_contract()[branch]["causal_data_sources"]
        ),
    }


def _aggregate(rows: Sequence[Mapping[str, object]], branch: str) -> dict[str, object]:
    selected = [row for row in rows if row.get("branch") == branch]
    true_errors = [
        float(row["estimate_delta_v_mps"]) - float(row["delta_velocity_true_minus_reported_mps"])
        for row in selected
        if row.get("estimate_delta_v_mps") not in (None, "")
    ]
    return {
        "branch": branch,
        "case_count": len(selected),
        "estimate_count": len(true_errors),
        "bias_estimated_true_minus_reported_mps": None if not true_errors else float(np.mean(true_errors)),
        "rmse_estimated_true_minus_reported_mps": None if not true_errors else float(np.sqrt(np.mean(np.square(true_errors)))),
        "mae_estimated_true_minus_reported_mps": None if not true_errors else float(np.mean(np.abs(true_errors))),
        "fallback_rate": None if not selected else float(sum(
            row.get("estimate_delta_v_mps") in (None, "") or
            str(row.get("status", "")).startswith("FALLBACK")
            for row in selected
        ) / len(selected)),
        "known_error_evaluation_only": branch == "V1K_Known_Correction",
        "target_truth_used": False,
        "causal_data_sources": (
            list(CAUSAL_DATA_SOURCES)
            if branch == "V1_Blind_Deterministic"
            else branch_contract()[branch]["causal_data_sources"]
        ),
        "evaluation_scope": "velocity_error_evaluation_only",
    }


def run_pilot(
    output_root: Path,
    simulator: Path,
    core: Path,
    deltas_v_mps: Sequence[float],
    seeds: Sequence[int],
    textures: Sequence[str],
    ranges_m: Sequence[float],
    *,
    skip_core: bool,
    max_supported_error_mps: float = DEFAULT_MAX_SUPPORTED_ERROR_MPS,
    source_commit_override: Optional[str] = None,
    worktree_dirty_override: Optional[bool] = None,
) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty output: {output_root}")
    if not TEMPLATE.is_file() or not simulator.is_file():
        raise RuntimeError("velocity study template or simulator is missing")
    if not skip_core and not core.is_file():
        raise RuntimeError(f"GMTI_core is missing: {core}")
    if any(texture not in TEXTURE_CONFIGS for texture in textures):
        raise ValueError(f"unsupported texture list: {textures}")
    deltas = [float(value) for value in deltas_v_mps]
    seed_values = [int(value) for value in seeds]
    ranges = [float(value) for value in ranges_m]
    if not deltas or not seed_values or not textures or not ranges:
        raise ValueError("velocity study levels cannot be empty")
    if any(not math.isfinite(value) for value in (*deltas, *ranges)):
        raise ValueError("velocity levels and ranges must be finite")
    if not math.isfinite(float(max_supported_error_mps)) or float(max_supported_error_mps) <= 0.0:
        raise ValueError("max_supported_error_mps must be finite and positive")
    output_root.mkdir(parents=True, exist_ok=True)
    gpu = _probe_gpu()
    if not skip_core and int(gpu["returncode"]) != 0:
        raise RuntimeError("nvidia-smi failed; refusing to claim a CUDA velocity study")
    template = _prepare_template(json.loads(TEMPLATE.read_text(encoding="utf-8")), compact_input=skip_core)
    _write_json(output_root / "prepared_template.json", template)
    case_records: list[dict[str, object]] = []
    case_rows: list[dict[str, object]] = []
    estimate_rows: list[dict[str, object]] = []
    combinations = [(delta, seed, texture, range_center) for delta in deltas for seed in seed_values for texture in textures for range_center in ranges]
    for case_index, (delta, seed, texture, range_center) in enumerate(combinations, 1):
        case_root = output_root / "cases" / f"case_{case_index:03d}"
        case_root.mkdir(parents=True, exist_ok=True)
        config = _case_config(template, case_root, delta, seed, texture, range_center)
        config_path = case_root / "scenario.run.json"
        _write_json(config_path, config)
        stage2_log = case_root / "stage2.log"
        rc, elapsed = _run_logged([str(simulator), "--config", str(config_path)], stage2_log)
        if rc != 0:
            raise RuntimeError(f"Stage2 failed for {config_path}; see {stage2_log}")
        raw_path = _find_period_file(case_root / "off")
        source_xml = case_root / "off/config/temp_config_stage2_period_0000.xml"
        resolved_path = case_root / "off/scenario_resolved.json"
        if not source_xml.is_file() or not resolved_path.is_file():
            raise RuntimeError(f"missing resolved Stage2 artifacts for {case_root}")
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        context = _reported_context(config, f"off_c_plus_n:{config['case_id']}", range_center)
        audit = estimator_input_audit([raw_path], context, contains_target_truth=False, contains_known_velocity_error=False, contains_true_velocity=False)
        waveform = config["waveform"]
        assert isinstance(waveform, Mapping)
        packet_bytes = HEADER_BYTES + int(waveform["pulse_len"]) * int(waveform["new_protocol_channel_count"]) * 2 * 4
        header = _load_header_velocities(raw_path, packet_bytes)
        reported_velocity = DEFAULT_TRUE_VELOCITY_MPS + delta
        header_error = max((abs(float(value) - reported_velocity) for value in header["vn_mps"]), default=0.0)
        if header_error > 0.011:
            raise RuntimeError(f"reported velocity header invariant failed for {case_root}: {header_error}")
        estimator = estimate_velocity_from_target_free_raw(
            raw_path,
            context,
            max_supported_error_mps=max_supported_error_mps,
        )
        _write_json(case_root / "velocity_estimator.json", estimator)
        case_id = str(config["case_id"])
        case: dict[str, object] = {
            "case_index": case_index,
            "case_id": case_id,
            # Compatibility alias consumed by the existing target-assisted
            # core evaluator.  The study-facing source of truth remains the
            # explicit reported-minus-true field below.
            "error_deg": delta,
            "case_root": str(case_root),
            "delta_velocity_reported_minus_true_mps": delta,
            "delta_velocity_true_minus_reported_mps": -delta,
            "velocity_true_mps": DEFAULT_TRUE_VELOCITY_MPS,
            "velocity_reported_mps": reported_velocity,
            "seed": seed,
            "texture": texture,
            "range_center_m": range_center,
            "stage2_exit_code": rc,
            "stage2_elapsed_s": elapsed,
            "scenario_config": str(config_path),
            "resolved_scenario": str(resolved_path),
            "source_xml": str(source_xml),
            "raw_path": str(raw_path),
            "raw_sha256": _sha256(raw_path),
            "resolved_platform": resolved.get("platform"),
            "header_velocity_audit": {
                "first_vn_mps": header["vn_mps"][0] if header["vn_mps"] else None,
                "first_speed_mps": header["speed_mps"][0] if header["speed_mps"] else None,
                "max_abs_vn_error_mps": header_error,
                "source": "reported_velocity_trajectory",
            },
            "estimator_input_audit": audit,
            "estimator": estimator,
        }
        case_records.append(case)
        estimate_rows.append({
            "case_index": case_index,
            "case_id": case_id,
            "delta_velocity_reported_minus_true_mps": delta,
            "delta_velocity_true_minus_reported_mps": -delta,
            "velocity_reported_mps": reported_velocity,
            "seed": seed,
            "texture": texture,
            "range_center_m": range_center,
            **{key: value for key, value in estimator.items() if key not in {"input_audit", "doppler_observables", "ctdr_observable", "p38_observable", "six_pair_observables"}},
        })
        case_rows.append({
            "case_index": case_index,
            "case_id": case_id,
            "delta_velocity_reported_minus_true_mps": delta,
            "velocity_true_mps_evaluation_only": DEFAULT_TRUE_VELOCITY_MPS,
            "velocity_reported_mps": reported_velocity,
            "seed": seed,
            "texture": texture,
            "range_center_m": range_center,
            "stage2_exit_code": rc,
            "stage2_elapsed_s": elapsed,
            "raw_path": str(raw_path),
            "raw_sha256": case["raw_sha256"],
            "header_max_abs_vn_error_mps": header_error,
            "blind_input_role": audit["input_role"],
        })
        print(
            f"[velocity][stage2] {case_index}/{len(combinations)} delta_reported_minus_true={delta:+.3f}m/s "
            f"texture={texture} range={range_center:.0f}m seed={seed} "
            f"V1={estimator.get('status')}:{estimator.get('estimate_delta_v_mps')}",
            flush=True,
        )

    decision_rows: list[dict[str, object]] = []
    branch_inputs: list[tuple[dict[str, object], str, Path, dict[str, object]]] = []
    for case in case_records:
        estimator = case["estimator"]
        assert isinstance(estimator, Mapping)
        for branch in BRANCH_NAMES:
            row = _decision_row(case, branch, estimator)
            decision_rows.append(row)
            case.setdefault("decisions", {})[branch] = row
            if skip_core:
                continue
            raw_path = Path(case["raw_path"])
            packet_bytes = HEADER_BYTES + int(template["waveform"]["pulse_len"]) * int(template["waveform"]["new_protocol_channel_count"]) * 2 * 4  # type: ignore[index]
            if branch == "V0_Current":
                branch_raw = raw_path
            elif branch == "V1K_Known_Correction":
                branch_raw = Path(case["case_root"]) / "corrections/V1K_Known_Correction/data.bin"
                _rewrite_header_velocity(raw_path, branch_raw, packet_bytes, float(case["velocity_true_mps"]))
            elif row["decision"] == "APPLY_ESTIMATED_CORRECTION":
                branch_raw = Path(case["case_root"]) / "corrections/V1_Blind_Deterministic/data.bin"
                _rewrite_header_velocity(raw_path, branch_raw, packet_bytes, float(case["velocity_reported_mps"]) + float(row["estimate_delta_v_mps"]))
            else:
                branch_raw = raw_path
            branch_inputs.append((case, branch, branch_raw, {"correction_source": row["correction_source"], "input_role": row["input_role"]}))

    _write_rows(output_root / "case_index.csv", case_rows)
    _write_rows(output_root / "velocity_estimates.csv", estimate_rows)
    _write_rows(output_root / "velocity_decisions.csv", decision_rows)
    core_rows: list[dict[str, object]] = []
    if not skip_core:
        for index, (case, branch, raw_path, branch_meta) in enumerate(branch_inputs, 1):
            print(f"[velocity][cuda] {index}/{len(branch_inputs)} {case['case_id']} {branch}", flush=True)
            row = target_assisted._run_core_branch(case, branch, raw_path, core)
            row.update(branch_meta)
            core_rows.append(row)
    _write_rows(output_root / "core_metrics.csv", core_rows)
    aggregate_rows = [_aggregate(decision_rows, branch) for branch in BRANCH_NAMES]
    _write_rows(output_root / "aggregate_metrics.csv", aggregate_rows)
    all_core_ok = skip_core or all(row.get("status") == "completed" for row in core_rows)
    source_status = _git("status", "--short", "--untracked-files=no")
    source_commit = source_commit_override or _git("rev-parse", "HEAD")
    source_dirty = bool(source_status) if worktree_dirty_override is None else bool(worktree_dirty_override)
    manifest: dict[str, object] = {
        "schema": "true_reported_velocity_error_study_v1",
        "status": "completed" if all_core_ok else "completed_with_failed_core",
        "ai_training": False,
        "ai_router": False,
        "target_free_estimator_only": True,
        "velocity_source_contract": velocity_source_contract(),
        "branch_contract": branch_contract(),
        "estimator_contract": {
            "method": "deterministic_target_free_clutter_velocity_fusion_v1",
            "input_rule": "one OFF C+N raw stream plus reported context",
            "causal_data_sources": list(CAUSAL_DATA_SOURCES),
            "target_truth_used": False,
            "known_velocity_error_used": False,
            "true_velocity_in_estimator_input": False,
            "observables": ["six_pair_clutter_phase", "doppler_ridge_displacement", "p38_phase_slope", "ctdr_residual", "multi_beam_power"],
            "fallback_policy": "KEEP_CURRENT on unidentifiable or model mismatch",
            "max_supported_error_mps": float(max_supported_error_mps),
        },
        "levels": {
            "delta_velocity_reported_minus_true_mps": deltas,
            "delta_velocity_true_minus_reported_mps": [-value for value in deltas],
            "seeds": seed_values,
            "textures": list(textures),
            "range_centers_m": ranges,
            "case_count": len(case_records),
        },
        "source": {
            "working_directory": str(ROOT),
            "source_commit_before_run": source_commit,
            "worktree_status_before_run": source_status,
            "worktree_dirty_before_run": source_dirty,
            "template": str(TEMPLATE.resolve()),
            "template_sha256": _sha256(TEMPLATE),
            "simulator": str(simulator.resolve()),
            "simulator_sha256": _sha256(simulator),
            "gmticore": str(core.resolve()) if core.is_file() else None,
            "gmticore_sha256": _sha256(core) if core.is_file() else None,
        },
        "execution": {
            "python": sys.version,
            "platform": platform.platform(),
            "gpu_probe_before_run": gpu,
            "disk_probe_before_run": _probe_disk(),
            "working_directory": str(ROOT),
            "core_skipped": skip_core,
            "compact_estimator_input": skip_core,
            "max_supported_error_mps": float(max_supported_error_mps),
        },
        "counts": {
            "case_count": len(case_records),
            "stage2_completed": len(case_records),
            "estimate_rows": len(estimate_rows),
            "decision_rows": len(decision_rows),
            "core_rows": len(core_rows),
            "core_success": all_core_ok,
            "blind_status_counts": {status: sum(row.get("status") == status for row in estimate_rows) for status in ("VALID", "FALLBACK_MODEL_MISMATCH", "FALLBACK_UNIDENTIFIABLE")},
        },
        "artifacts": {
            "prepared_template": str((output_root / "prepared_template.json").resolve()),
            "case_index": str((output_root / "case_index.csv").resolve()),
            "velocity_estimates": str((output_root / "velocity_estimates.csv").resolve()),
            "velocity_decisions": str((output_root / "velocity_decisions.csv").resolve()),
            "aggregate_metrics": str((output_root / "aggregate_metrics.csv").resolve()),
            "core_metrics": str((output_root / "core_metrics.csv").resolve()),
        },
        "aggregate_metrics": aggregate_rows,
        "case_records": case_records,
        "limitations": [
            "This is an offline deterministic target-free pilot; it is not an operational blind velocity estimator.",
            "V1K uses the configured error only as an evaluation upper bound and never supplies it to V1.",
            "Target Pd/false-hit, TrackManager/PIPE retention and yaw/pitch coupling are not evaluated in this stage.",
            "The compact skip-core matrix is not a production CUDA result; production Current/CFAR semantics remain unchanged.",
        ],
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", "--output-root", dest="output_root", type=Path, default=ROOT / "outputs/velocity_error_study_20260915")
    parser.add_argument("--simulator", type=Path, default=SIMULATOR)
    parser.add_argument("--gmticore", type=Path, default=GMTI_CORE)
    parser.add_argument("--delta-v-mps", "--deltas-v-mps", dest="deltas_v_mps", nargs="+", default=[str(value) for value in DEFAULT_DELTA_V_MPS])
    parser.add_argument("--seeds", nargs="+", default=[str(value) for value in DEFAULT_SEEDS])
    parser.add_argument("--textures", nargs="+", default=list(DEFAULT_TEXTURES))
    parser.add_argument("--ranges-m", nargs="+", default=[str(value) for value in DEFAULT_RANGES_M])
    parser.add_argument(
        "--max-supported-error-mps",
        type=float,
        default=DEFAULT_MAX_SUPPORTED_ERROR_MPS,
        help="explicit pilot quality-gate bound for a blind correction (default: 1.0 m/s)",
    )
    parser.add_argument("--skip-core", action="store_true", help="do not run GMTI_core; output is a compact estimator matrix")
    parser.add_argument("--smoke", action="store_true", help="one compact target-free case; core is skipped unless explicitly omitted in a later full run")
    parser.add_argument("--source-commit", default=None)
    parser.add_argument("--worktree-dirty-before", choices=("true", "false"), default=None)
    args = parser.parse_args(argv)
    if args.smoke:
        deltas = [0.2]
        seeds = [101]
        textures = ["low_texture"]
        ranges = [8750.0]
        skip_core = True
    else:
        deltas = _parse_float_list(args.deltas_v_mps, "delta-v-mps")
        seeds = _parse_int_list(args.seeds, "seeds")
        textures = list(args.textures)
        ranges = _parse_float_list(args.ranges_m, "ranges-m")
        skip_core = bool(args.skip_core)
    manifest = run_pilot(
        args.output_root.resolve(),
        args.simulator.resolve(),
        args.gmticore.resolve(),
        deltas,
        seeds,
        textures,
        ranges,
        skip_core=skip_core,
        max_supported_error_mps=args.max_supported_error_mps,
        source_commit_override=args.source_commit,
        worktree_dirty_override=None if args.worktree_dirty_before is None else args.worktree_dirty_before == "true",
    )
    print(json.dumps({"status": manifest["status"], "output_root": str(args.output_root.resolve()), "counts": manifest["counts"]}, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
