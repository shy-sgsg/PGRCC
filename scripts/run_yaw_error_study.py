#!/usr/bin/env python3
"""Run the yaw-first attitude observability pilot.

This study keeps pitch and roll at zero and separates three controls:
``baseline`` (no attitude or servo error), ``yaw`` (true platform heading is
perturbed while servo error is zero), and ``servo`` (servo pointing is
perturbed while the platform heading is zero).  The Stage2 echo is generated
from the true yaw/servo condition.  The deterministic estimator sees only one
OFF C+N stream and nominal reported geometry; target-assisted ON-OFF output is
kept as a separate reference for moving-target controls.

The pilot is offline evidence.  It does not fit yaw, pitch and roll jointly,
does not invoke AI, and does not claim that the current production processor
has an independently reported yaw field.  Known correction is an
evaluation-only parameter reference; blind decisions keep Current whenever
the target-free feature model is unidentifiable or mismatched.
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
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import estimate_clutter_only_servo as clutter_only  # noqa: E402
import run_clutter_only_servo_pilot as clutter_pilot  # noqa: E402
import run_unknown_system_error_servo_pilot as target_assisted  # noqa: E402


TEMPLATE = ROOT / "configs/research/unknown_system_error_baseline_pilot.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
HEADER_BYTES = 256
DEFAULT_YAW_ERRORS_DEG = (0.0, -0.5, 0.5, -1.0, 1.0)
DEFAULT_SEEDS = (101, 202)
DEFAULT_TEXTURES = ("low_texture", "high_texture")
DEFAULT_RANGES_M = (7500.0, 10000.0)
BASE_REPORTED_YAW_DEG = 0.0
PITCH_DEG = 0.0
ROLL_DEG = 0.0
MAX_SUPPORTED_YAW_ERROR_DEG = 2.0
CONDITIONS = ("baseline", "yaw", "servo")
SCENES = ("target_free", "moving_target")

ATTITUDE_SOURCE_CONTRACT = {
    "true_yaw_deg": {
        "meaning": "physical platform heading used by the Stage2 echo and true geometry",
        "consumers": [
            "true_platform_heading",
            "clutter_look_geometry",
            "target_relative_geometry",
            "echo_phase",
        ],
    },
    "reported_yaw_deg": {
        "meaning": "nominal heading supplied to the deterministic reported-geometry model",
        "consumers": [
            "reported_geometry",
            "beam_steering_reference",
            "angle_conversion",
        ],
    },
    "pitch_deg": PITCH_DEG,
    "roll_deg": ROLL_DEG,
}


def yaw_error_levels() -> tuple[float, ...]:
    return DEFAULT_YAW_ERRORS_DEG


def attitude_source_contract() -> dict[str, object]:
    return copy.deepcopy(ATTITUDE_SOURCE_CONTRACT)


def condition_contract() -> dict[str, dict[str, object]]:
    return {
        "baseline": {
            "yaw_true_minus_reported_deg": 0.0,
            "servo_true_minus_reported_deg": 0.0,
            "parameter": "none",
        },
        "yaw": {
            "yaw_true_minus_reported_deg": "case_value",
            "servo_true_minus_reported_deg": 0.0,
            "parameter": "yaw",
        },
        "servo": {
            "yaw_true_minus_reported_deg": 0.0,
            "servo_true_minus_reported_deg": "case_value",
            "parameter": "servo",
        },
    }


def require_yaw_first_state(mapping: Mapping[str, object]) -> tuple[float, float]:
    """Validate the non-joint yaw-first state and return true/reported yaw."""

    if bool(mapping.get("joint_attitude_fit", False)):
        raise ValueError("yaw-first pilot does not allow a joint attitude fit")
    try:
        true_yaw = float(mapping["yaw_true_deg"])
        reported_yaw = float(mapping["yaw_reported_deg"])
        pitch = float(mapping.get("pitch_deg", 0.0))
        roll = float(mapping.get("roll_deg", 0.0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("yaw-first state requires numeric yaw/pitch/roll fields") from exc
    if not all(math.isfinite(value) for value in (true_yaw, reported_yaw, pitch, roll)):
        raise ValueError("yaw-first state must be finite")
    if abs(pitch) > 1.0e-12 or abs(roll) > 1.0e-12:
        raise ValueError("pitch and roll must remain zero in the yaw-first pilot")
    return true_yaw, reported_yaw


def _path_tokens(path: object) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(path).lower())
        if token
    }


def estimator_input_audit(
    paths: Sequence[object],
    context: Mapping[str, object],
    *,
    contains_target_truth: bool,
    contains_known_yaw_error: bool,
    contains_true_yaw: bool,
) -> dict[str, object]:
    """Audit the OFF-only deterministic yaw estimator input boundary."""

    if not paths:
        raise ValueError("yaw estimator requires one OFF input path")
    path_values = [str(path) for path in paths]
    if any(token in {"on", "to"} for path in path_values for token in _path_tokens(path)):
        raise ValueError("blind yaw estimator cannot consume ON/TO input")
    if contains_target_truth or contains_known_yaw_error or contains_true_yaw:
        raise ValueError("blind yaw estimator cannot consume target truth, known yaw, or true yaw")
    if not isinstance(context, Mapping):
        raise ValueError("reported yaw context must be an object")
    if "reported_yaw_deg" not in context:
        raise ValueError("reported_yaw_deg is required in the estimator audit context")
    reported_yaw = float(context["reported_yaw_deg"])
    if not math.isfinite(reported_yaw):
        raise ValueError("reported_yaw_deg must be finite")
    if len(path_values) != 1:
        raise ValueError("blind yaw estimator requires exactly one OFF input path")
    return {
        "off_only": True,
        "input_paths": path_values,
        "input_role": "OFF_C_PLUS_N_TARGET_FREE_ONLY",
        "contains_target_truth": False,
        "contains_known_yaw_error": False,
        "contains_true_yaw": False,
        "reported_yaw_deg": reported_yaw,
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
    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _run_logged(command: Sequence[str], log_path: Path) -> tuple[int, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(str(item) for item in command) + "\n")
        completed = subprocess.run([str(item) for item in command], cwd=ROOT,
                                   stdout=log, stderr=subprocess.STDOUT, check=False)
        log.write(f"exit_code={completed.returncode}\n")
    return int(completed.returncode), time.perf_counter() - started


def _probe_gpu() -> dict[str, object]:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,driver_version,pstate,temperature.gpu,power.draw,power.limit,utilization.gpu,memory.used,memory.total", "--format=csv,noheader"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False,
    )
    return {"returncode": result.returncode, "output": result.stdout}


def _probe_disk() -> dict[str, object]:
    result = subprocess.run(["df", "-h", str(ROOT), "/tmp"], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, check=False)
    return {"returncode": result.returncode, "output": result.stdout}


def _parse_float_list(values: Sequence[str], name: str) -> list[float]:
    result: list[float] = []
    for raw in values:
        for item in raw.split(","):
            if item.strip():
                value = float(item)
                if not math.isfinite(value):
                    raise ValueError(f"{name} must be finite")
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


def _find_period_file(case_root: Path) -> Path:
    manifest = case_root / "data/period_files.csv"
    if not manifest.is_file():
        raise RuntimeError(f"missing Stage2 period manifest: {manifest}")
    with manifest.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise RuntimeError(f"yaw pilot requires one period: {manifest} -> {len(rows)}")
    raw = Path(rows[0]["file"])
    candidates = [raw] if raw.is_absolute() else [case_root / raw, ROOT / raw, raw]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(f"missing Stage2 period file: {candidates}")


def _prepare_template(template: Mapping[str, object], compact_input: bool) -> dict[str, object]:
    config = target_assisted._prepare_template(template)
    geometry = config.get("simulation_geometry")
    if not isinstance(geometry, dict):
        raise ValueError("simulation_geometry must be an object")
    geometry.update({
        "platform_heading_source": "fixed_angle",
        "platform_heading_deg": BASE_REPORTED_YAW_DEG,
    })
    config["attitude"] = {
        "yaw_true_deg": BASE_REPORTED_YAW_DEG,
        "yaw_reported_deg": BASE_REPORTED_YAW_DEG,
        "pitch_deg": PITCH_DEG,
        "roll_deg": ROLL_DEG,
        "joint_attitude_fit": False,
    }
    if compact_input:
        waveform = config.get("waveform")
        processing = config.get("range_processing")
        if not isinstance(waveform, dict) or not isinstance(processing, dict):
            raise ValueError("prepared waveform/range_processing must be objects")
        waveform.update({"pulse_len": 4096, "pulse_num": 8})
        processing.update({"range_fft_len": 4096, "range_crop_len": 4096})
    return config


def _range_sample(range_center_m: float, config: Mapping[str, object]) -> int:
    waveform = config.get("waveform")
    processing = config.get("range_processing")
    if not isinstance(waveform, Mapping) or not isinstance(processing, Mapping):
        raise ValueError("waveform/range_processing are required")
    fs_hz = float(waveform["fs_mhz"]) * 1.0e6
    delay_s = float(processing.get("sample_delay_us", 0.0)) * 1.0e-6
    sample = (2.0 * float(range_center_m) / 299792458.0 - delay_s) * fs_hz
    if not math.isfinite(sample) or sample <= 0.0:
        raise ValueError("range center does not map to a positive sample")
    return int(round(sample))


def _condition_parameters(condition: str, case_value_deg: float) -> tuple[float, float, float]:
    if condition not in CONDITIONS:
        raise ValueError(f"unsupported attitude condition: {condition}")
    yaw_error = case_value_deg if condition == "yaw" else 0.0
    servo_error = case_value_deg if condition == "servo" else 0.0
    true_yaw = BASE_REPORTED_YAW_DEG + yaw_error
    require_yaw_first_state({
        "yaw_true_deg": true_yaw,
        "yaw_reported_deg": BASE_REPORTED_YAW_DEG,
        "pitch_deg": PITCH_DEG,
        "roll_deg": ROLL_DEG,
        "joint_attitude_fit": False,
    })
    return true_yaw, yaw_error, servo_error


def _case_config(
    template: Mapping[str, object],
    case_root: Path,
    scene: str,
    condition: str,
    case_value_deg: float,
    seed: int,
    texture: str,
    range_center_m: float,
) -> dict[str, object]:
    if scene not in SCENES:
        raise ValueError(f"unsupported scene: {scene}")
    if texture not in clutter_pilot.TEXTURE_CONFIGS:
        raise ValueError(f"unsupported texture: {texture}")
    true_yaw, yaw_error, servo_error = _condition_parameters(condition, case_value_deg)
    config = copy.deepcopy(template)
    tag = f"{case_value_deg:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")
    config["case_id"] = f"yaw_{scene}_{condition}_{tag}deg_seed_{seed}"
    output_dir = case_root / ("on" if scene == "moving_target" else "off")
    config["output_dir"] = str(output_dir.resolve())
    config["paired_background_output_dir"] = (
        str((case_root / "off").resolve()) if scene == "moving_target" else ""
    )
    random_cfg = config.get("random")
    if not isinstance(random_cfg, dict):
        raise ValueError("random config is not an object")
    random_cfg["random_seed"] = int(seed)
    scene_cfg = config.get("scene")
    if not isinstance(scene_cfg, dict):
        raise ValueError("scene config is not an object")
    scene_cfg.update({
        "range_min_m": float(range_center_m) - 500.0,
        "range_max_m": float(range_center_m) + 500.0,
        "signal_only": False,
    })
    area = scene_cfg.get("area_clutter")
    if not isinstance(area, dict):
        raise ValueError("area_clutter config is not an object")
    area.update(clutter_pilot.TEXTURE_CONFIGS[texture])
    targets = config.get("targets")
    if not isinstance(targets, list) or not targets or not isinstance(targets[0], dict):
        raise ValueError("target list is invalid")
    target = targets[0]
    target["target_id"] = "YAW_MOVING_TARGET"
    target["enabled"] = scene == "moving_target"
    init = target.get("init")
    if not isinstance(init, dict):
        raise ValueError("target init is not an object")
    init.update({"beam_id": 3, "expected_bin": _range_sample(range_center_m, config), "azimuth_offset_deg": 0.0})
    target["motion"] = (
        {"type": "enu_velocity", "ve_mps": 0.0, "vn_mps": 45.0}
        if scene == "moving_target" else {"type": "static"}
    )
    target["amplitude"] = {"type": "snr_db", "snr_db": 30.0}
    target["visibility"] = {"type": "gaussian", "single_beam_only": False, "visible_beam_span": -1}
    geometry = config.get("simulation_geometry")
    if not isinstance(geometry, dict):
        raise ValueError("simulation_geometry is not an object")
    geometry["platform_heading_source"] = "fixed_angle"
    geometry["platform_heading_deg"] = true_yaw
    config["servo_angle_error"] = {
        "enabled": abs(servo_error) > 1.0e-12,
        "true_minus_reported_deg": servo_error,
    }
    config["attitude"] = {
        "yaw_true_deg": true_yaw,
        "yaw_reported_deg": BASE_REPORTED_YAW_DEG,
        "yaw_true_minus_reported_deg": yaw_error,
        "servo_true_minus_reported_deg": servo_error,
        "pitch_deg": PITCH_DEG,
        "roll_deg": ROLL_DEG,
        "joint_attitude_fit": False,
        "condition": condition,
        "scene": scene,
    }
    return config


def _reported_context(config: Mapping[str, object], source_id: str, range_center_m: float) -> dict[str, object]:
    context = clutter_pilot._reported_context(config, source_id, range_center_m)
    context["representative_slant_range_m"] = float(range_center_m)
    geometry = context.get("reported_simulation_geometry")
    if not isinstance(geometry, dict):
        geometry = {}
    geometry.update({
        "platform_heading_source": "fixed_angle",
        "platform_heading_deg": BASE_REPORTED_YAW_DEG,
    })
    context["reported_simulation_geometry"] = geometry
    return context


def _estimate_blind(off_raw: Path, context: Mapping[str, object], seed: int) -> tuple[dict[str, object], dict[str, object]]:
    audit_context = {
        "reported_yaw_deg": BASE_REPORTED_YAW_DEG,
        "reported_geometry": {"platform_heading_deg": BASE_REPORTED_YAW_DEG},
    }
    audit = estimator_input_audit(
        [off_raw], audit_context,
        contains_target_truth=False,
        contains_known_yaw_error=False,
        contains_true_yaw=False,
    )
    packets = clutter_only.load_target_free_packets(off_raw, context)
    # Compact packets do not carry a trusted range field.  Use the configured
    # reported scene-range center as the range support instead of allowing a
    # noise energy centroid to create a below-ground slant range.
    reported_range = context.get("representative_slant_range_m")
    if reported_range is None:
        raise ValueError("reported range center is required for yaw estimator")
    for packet in packets:
        packet["slant_range_m"] = float(reported_range)
    features = clutter_only.extract_clutter_features(packets, context)
    result = clutter_only.estimate_clutter_only_servo(features, {"seed": seed})
    result = dict(result)
    result.update({
        "method": "deterministic_target_free_effective_yaw_from_clutter_v1",
        "calibration_mode": "target_free_deterministic_effective_geometry",
        "target_truth_used": False,
        "true_yaw_used": False,
        "known_yaw_error_used": False,
        "pitch_deg": PITCH_DEG,
        "roll_deg": ROLL_DEG,
        "reported_yaw_deg": BASE_REPORTED_YAW_DEG,
        "max_supported_yaw_error_deg": MAX_SUPPORTED_YAW_ERROR_DEG,
        "input_audit": audit,
        "causal_data_sources": list(clutter_only.CAUSAL_DATA_SOURCES),
    })
    estimate = result.get("estimate_deg")
    if result.get("status") == "VALID" and estimate not in (None, ""):
        if abs(float(estimate)) > MAX_SUPPORTED_YAW_ERROR_DEG:
            result["status"] = "FALLBACK_MODEL_MISMATCH"
            result["model_mismatch"] = True
            result["fallback_reason"] = "effective_yaw_estimate_outside_supported_error_bound"
            result["estimate_deg"] = None
    return result, audit


def _estimate_assisted(
    on_raw: Optional[Path], off_raw: Path, config: Mapping[str, object], resolved: Mapping[str, object], seed: int,
) -> dict[str, object]:
    if on_raw is None:
        return {
            "status": "NOT_APPLICABLE_TARGET_FREE",
            "fit_status": "not_applicable",
            "estimate_deg": None,
            "target_truth_used": False,
            "causal_data_sources": [],
        }
    result, _ = target_assisted._estimate_servo(on_raw, off_raw, config, resolved, seed)
    return {
        "status": "VALID" if result.get("fit_status") == "fit" else "FALLBACK_UNIDENTIFIABLE",
        "fit_status": result.get("fit_status"),
        "estimate_deg": result.get("estimated_true_minus_reported_deg"),
        "uncertainty_deg": result.get("uncertainty_deg"),
        "phase_rmse": result.get("mean_phase_residual_rms_rad"),
        "target_truth_used": False,
        "causal_data_sources": [
            "paired ON-OFF target residual",
            "six pair cross-channel phases/coherences",
            "reported beam angles",
            "configured nominal target/range hypothesis",
        ],
        "source_id": "paired_on_minus_off_effective_geometry_reference",
    }


def _parameter_error(case: Mapping[str, object]) -> float:
    condition = str(case["condition"])
    if condition == "yaw":
        return float(case["yaw_true_minus_reported_deg"])
    if condition == "servo":
        return float(case["servo_true_minus_reported_deg"])
    return 0.0


def _decision(case: Mapping[str, object], branch: str, blind: Mapping[str, object]) -> dict[str, object]:
    parameter_error = _parameter_error(case)
    if branch == "A0_Current":
        return {
            "status": "CURRENT",
            "decision": "KEEP_CURRENT",
            "estimate_deg": None,
            "applied_correction_deg": 0.0,
            "correction_source": "reported_geometry_current",
            "known_yaw_error_used": False,
            "target_truth_used": False,
            "input_role": "reported_geometry_current",
            "input_paths": [case["off_raw"]],
        }
    if branch == "AK_Known_Correction":
        return {
            "status": "KNOWN_EVALUATION",
            "decision": "NO_CORRECTION_NEEDED" if abs(parameter_error) < 1.0e-12 else "APPLY_KNOWN_ERROR_CORRECTION",
            "estimate_deg": parameter_error,
            "applied_correction_deg": parameter_error,
            "correction_source": "known_yaw_or_servo_error_evaluation_only",
            "known_yaw_error_used": True,
            "target_truth_used": False,
            "input_role": "evaluation_only_known_attitude_error",
            "input_paths": [case["off_raw"]],
        }
    estimate = blind.get("estimate_deg")
    valid = blind.get("status") == "VALID" and estimate not in (None, "")
    if valid:
        value = float(estimate)
        action = "NO_CORRECTION_NEEDED" if abs(value) <= 0.05 else "APPLY_ESTIMATED_CORRECTION"
        applied = 0.0 if action == "NO_CORRECTION_NEEDED" else value
        source = "deadband_current" if applied == 0.0 else "blind_deterministic_effective_yaw"
        status = "VALID" if applied != 0.0 else "VALID_WITHIN_DEADBAND"
    else:
        action = "KEEP_CURRENT"
        applied = 0.0
        source = "fallback_current"
        status = str(blind.get("status", "FALLBACK_UNIDENTIFIABLE"))
    return {
        "status": status,
        "decision": action,
        "estimate_deg": estimate if valid else None,
        "applied_correction_deg": applied,
        "correction_source": source,
        "known_yaw_error_used": False,
        "target_truth_used": False,
        "input_role": "OFF_C_PLUS_N_TARGET_FREE_ONLY",
        "input_paths": blind.get("input_audit", {}).get("input_paths", [case["off_raw"]]),
    }


def _aggregate(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["scene"]), str(row["condition"]), str(row["branch"]))].append(row)
    output: list[dict[str, object]] = []
    for (scene, condition, branch), selected in sorted(groups.items()):
        residuals = [
            float(row["estimate_deg"]) - _parameter_error(row)
            for row in selected if row.get("estimate_deg") not in (None, "")
        ]
        output.append({
            "scene": scene,
            "condition": condition,
            "branch": branch,
            "case_count": len(selected),
            "estimate_count": len(residuals),
            "bias_deg": None if not residuals else float(np.mean(residuals)),
            "rmse_deg": None if not residuals else float(np.sqrt(np.mean(np.square(residuals)))),
            "mae_deg": None if not residuals else float(np.mean(np.abs(residuals))),
            "fallback_rate": None if not selected else float(sum(
                str(row.get("status", "")).startswith("FALLBACK")
                for row in selected
            ) / len(selected)),
            "known_error_evaluation_only": branch == "AK_Known_Correction",
            "pitch_deg": PITCH_DEG,
            "roll_deg": ROLL_DEG,
            "target_truth_used": False,
        })
    return output


def run_pilot(
    output_root: Path,
    simulator: Path,
    errors_deg: Sequence[float],
    seeds: Sequence[int],
    textures: Sequence[str],
    ranges_m: Sequence[float],
    scenes: Sequence[str],
    *,
    compact_input: bool,
    source_commit_override: Optional[str] = None,
    worktree_dirty_override: Optional[bool] = None,
) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty output: {output_root}")
    if not TEMPLATE.is_file() or not simulator.is_file():
        raise RuntimeError("yaw study template or simulator is missing")
    if any(texture not in clutter_pilot.TEXTURE_CONFIGS for texture in textures):
        raise ValueError(f"unsupported texture list: {textures}")
    if any(scene not in SCENES for scene in scenes):
        raise ValueError(f"unsupported scene list: {scenes}")
    errors = [float(value) for value in errors_deg]
    seeds_int = [int(value) for value in seeds]
    ranges = [float(value) for value in ranges_m]
    if not errors or not seeds_int or not textures or not ranges or not scenes:
        raise ValueError("yaw study levels cannot be empty")
    if any(not math.isfinite(value) for value in (*errors, *ranges)):
        raise ValueError("yaw errors and ranges must be finite")
    output_root.mkdir(parents=True, exist_ok=True)
    gpu = _probe_gpu()
    template = _prepare_template(json.loads(TEMPLATE.read_text(encoding="utf-8")), compact_input)
    _write_json(output_root / "prepared_template.json", template)
    case_records: list[dict[str, object]] = []
    case_rows: list[dict[str, object]] = []
    estimate_rows: list[dict[str, object]] = []
    decision_rows: list[dict[str, object]] = []
    combinations = [
        (scene, condition, error, seed, texture, range_center)
        for scene in scenes for condition in CONDITIONS for error in errors
        for seed in seeds_int for texture in textures for range_center in ranges
    ]
    for case_index, (scene, condition, error, seed, texture, range_center) in enumerate(combinations, 1):
        case_root = output_root / "cases" / f"case_{case_index:03d}"
        case_root.mkdir(parents=True, exist_ok=True)
        config = _case_config(template, case_root, scene, condition, error, seed, texture, range_center)
        config_path = case_root / "scenario.run.json"
        _write_json(config_path, config)
        stage2_log = case_root / "stage2.log"
        rc, elapsed = _run_logged([str(simulator), "--config", str(config_path)], stage2_log)
        if rc != 0:
            raise RuntimeError(f"Stage2 failed for {config_path}; see {stage2_log}")
        off_raw = _find_period_file(case_root / "off")
        on_raw = _find_period_file(case_root / "on") if scene == "moving_target" else None
        source_xml = (case_root / "on/config/temp_config_stage2_period_0000.xml"
                      if on_raw is not None else case_root / "off/config/temp_config_stage2_period_0000.xml")
        resolved_path = (case_root / "on/scenario_resolved.json"
                         if on_raw is not None else case_root / "off/scenario_resolved.json")
        if not source_xml.is_file() or not resolved_path.is_file():
            raise RuntimeError(f"missing yaw study Stage2 artifacts for {case_root}")
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        context = _reported_context(config, f"off_c_plus_n:{config['case_id']}", range_center)
        blind, blind_audit = _estimate_blind(off_raw, context, seed)
        _write_json(case_root / "yaw_blind_estimator.json", blind)
        assisted = _estimate_assisted(on_raw, off_raw, config, resolved, seed)
        _write_json(case_root / "yaw_assisted_reference.json", assisted)
        true_yaw, yaw_error, servo_error = _condition_parameters(condition, error)
        case: dict[str, object] = {
            "case_index": case_index,
            "case_id": str(config["case_id"]),
            "case_root": str(case_root),
            "scene": scene,
            "condition": condition,
            "case_value_deg": error,
            "yaw_true_deg": true_yaw,
            "yaw_reported_deg": BASE_REPORTED_YAW_DEG,
            "yaw_true_minus_reported_deg": yaw_error,
            "servo_true_minus_reported_deg": servo_error,
            "pitch_deg": PITCH_DEG,
            "roll_deg": ROLL_DEG,
            "seed": seed,
            "texture": texture,
            "range_center_m": range_center,
            "stage2_exit_code": rc,
            "stage2_elapsed_s": elapsed,
            "scenario_config": str(config_path),
            "resolved_scenario": str(resolved_path),
            "source_xml": str(source_xml),
            "on_raw": None if on_raw is None else str(on_raw),
            "off_raw": str(off_raw),
            "on_raw_sha256": None if on_raw is None else _sha256(on_raw),
            "off_raw_sha256": _sha256(off_raw),
            "blind_input_audit": blind_audit,
            "blind_estimator": blind,
            "target_assisted_reference": assisted,
        }
        case_records.append(case)
        _write_json(case_root / "attitude_source_snapshot.json", {
            "case_id": case["case_id"],
            "scene": scene,
            "condition": condition,
            "true_yaw_deg": true_yaw,
            "reported_yaw_deg": BASE_REPORTED_YAW_DEG,
            "yaw_true_minus_reported_deg": yaw_error,
            "servo_true_minus_reported_deg": servo_error,
            "pitch_deg": PITCH_DEG,
            "roll_deg": ROLL_DEG,
            "joint_attitude_fit": False,
            "true_source": "simulation_geometry.platform_heading_deg",
            "reported_source": "reported_geometry_nominal_heading_deg",
            "servo_source": "servo_angle_error.true_minus_reported_deg",
        })
        for branch in ("A0_Current", "AK_Known_Correction", "A1_Blind_Deterministic"):
            decision = _decision(case, branch, blind)
            row = {
                "case_id": case["case_id"],
                "scene": scene,
                "condition": condition,
                "case_value_deg": error,
                "yaw_true_deg": true_yaw,
                "yaw_reported_deg": BASE_REPORTED_YAW_DEG,
                "yaw_true_minus_reported_deg": yaw_error,
                "servo_true_minus_reported_deg": servo_error,
                "pitch_deg": PITCH_DEG,
                "roll_deg": ROLL_DEG,
                "seed": seed,
                "texture": texture,
                "range_center_m": range_center,
                "branch": branch,
                **decision,
                "causal_data_sources": (
                    list(clutter_only.CAUSAL_DATA_SOURCES)
                    if branch == "A1_Blind_Deterministic"
                    else ("known attitude error" if branch == "AK_Known_Correction" else "reported geometry")
                ),
            }
            decision_rows.append(row)
        estimate_rows.append({
            "case_id": case["case_id"],
            "scene": scene,
            "condition": condition,
            "case_value_deg": error,
            "yaw_true_minus_reported_deg": yaw_error,
            "servo_true_minus_reported_deg": servo_error,
            "pitch_deg": PITCH_DEG,
            "roll_deg": ROLL_DEG,
            "seed": seed,
            "texture": texture,
            "range_center_m": range_center,
            "branch": "A1_Blind_Deterministic",
            "method": blind.get("method"),
            "status": blind.get("status"),
            "estimate_deg": blind.get("estimate_deg"),
            "uncertainty_deg": blind.get("uncertainty_deg"),
            "model_mismatch": blind.get("model_mismatch"),
            "fallback_reason": blind.get("fallback_reason"),
            "target_truth_used": False,
            "input_role": blind_audit["input_role"],
        })
        case_rows.append({
            "case_index": case_index,
            "case_id": case["case_id"],
            "scene": scene,
            "condition": condition,
            "case_value_deg": error,
            "yaw_true_deg": true_yaw,
            "yaw_reported_deg": BASE_REPORTED_YAW_DEG,
            "yaw_true_minus_reported_deg": yaw_error,
            "servo_true_minus_reported_deg": servo_error,
            "pitch_deg": PITCH_DEG,
            "roll_deg": ROLL_DEG,
            "seed": seed,
            "texture": texture,
            "range_center_m": range_center,
            "stage2_exit_code": rc,
            "stage2_elapsed_s": elapsed,
            "on_raw": "" if on_raw is None else str(on_raw),
            "off_raw": str(off_raw),
            "on_raw_sha256": "" if on_raw is None else _sha256(on_raw),
            "off_raw_sha256": _sha256(off_raw),
            "blind_input_role": blind_audit["input_role"],
            "blind_status": blind.get("status"),
            "blind_estimate_deg": blind.get("estimate_deg"),
            "assisted_status": assisted.get("status"),
        })
        print(
            f"[yaw][stage2] {case_index}/{len(combinations)} scene={scene} condition={condition} "
            f"value={error:+.3f}deg texture={texture} range={range_center:.0f}m seed={seed} "
            f"blind={blind.get('status')}:{blind.get('estimate_deg')}", flush=True,
        )
    _write_rows(output_root / "case_index.csv", case_rows)
    _write_rows(output_root / "attitude_estimates.csv", estimate_rows)
    _write_rows(output_root / "attitude_decisions.csv", decision_rows)
    aggregate = _aggregate(decision_rows)
    _write_rows(output_root / "aggregate_metrics.csv", aggregate)
    source_status = _git("status", "--short", "--untracked-files=no")
    source_commit = source_commit_override or _git("rev-parse", "HEAD")
    source_dirty = bool(source_status) if worktree_dirty_override is None else bool(worktree_dirty_override)
    manifest: dict[str, object] = {
        "schema": "yaw_first_attitude_study_v1",
        "status": "completed",
        "ai_training": False,
        "ai_router": False,
        "joint_attitude_fit": False,
        "attitude_source_contract": attitude_source_contract(),
        "condition_contract": condition_contract(),
        "levels": {
            "yaw_error_deg": [float(value) for value in errors],
            "seeds": seeds_int,
            "textures": list(textures),
            "range_centers_m": ranges,
            "scenes": list(scenes),
            "pitch_deg": PITCH_DEG,
            "roll_deg": ROLL_DEG,
            "max_supported_yaw_error_deg": MAX_SUPPORTED_YAW_ERROR_DEG,
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
        },
        "execution": {
            "python": sys.version,
            "platform": platform.platform(),
            "gpu_probe_before_run": gpu,
            "disk_probe_before_run": _probe_disk(),
            "core_skipped": True,
            "compact_input": compact_input,
        },
        "counts": {
            "case_count": len(case_records),
            "stage2_completed": len(case_records),
            "estimate_rows": len(estimate_rows),
            "decision_rows": len(decision_rows),
            "blind_status_counts": {
                status: sum(row.get("status") == status for row in estimate_rows)
                for status in ("VALID", "FALLBACK_MODEL_MISMATCH", "FALLBACK_UNIDENTIFIABLE")
            },
        },
        "artifacts": {
            "prepared_template": str((output_root / "prepared_template.json").resolve()),
            "case_index": str((output_root / "case_index.csv").resolve()),
            "attitude_estimates": str((output_root / "attitude_estimates.csv").resolve()),
            "attitude_decisions": str((output_root / "attitude_decisions.csv").resolve()),
            "aggregate_metrics": str((output_root / "aggregate_metrics.csv").resolve()),
        },
        "aggregate_metrics": aggregate,
        "case_records": case_records,
        "limitations": [
            "This is an offline yaw-first observability pilot; pitch and roll are fixed at zero.",
            "Yaw and servo are generated as separate one-factor conditions; the target-assisted reference estimates effective geometry and cannot distinguish their causes.",
            "The current production protocol has no independently consumed yaw field in this pilot; reported yaw is a nominal geometry context, not an operational header claim.",
            "Core/TrackManager/PIPE and target Pd/Pfa are not evaluated in this compact attitude stage.",
        ],
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", "--output-root", dest="output_root", type=Path,
                        default=ROOT / "outputs/yaw_error_study_20260915")
    parser.add_argument("--simulator", type=Path, default=SIMULATOR)
    parser.add_argument("--errors-deg", nargs="+",
                        default=[str(value) for value in DEFAULT_YAW_ERRORS_DEG])
    parser.add_argument("--seeds", nargs="+", default=[str(value) for value in DEFAULT_SEEDS])
    parser.add_argument("--textures", nargs="+", default=list(DEFAULT_TEXTURES))
    parser.add_argument("--ranges-m", nargs="+",
                        default=[str(value) for value in DEFAULT_RANGES_M])
    parser.add_argument("--scene", nargs="+", choices=SCENES, default=list(SCENES))
    parser.add_argument("--smoke", action="store_true",
                        help="one compact yaw/servo/baseline target-free case")
    parser.add_argument("--source-commit", default=None)
    parser.add_argument("--worktree-dirty-before", choices=("true", "false"), default=None)
    args = parser.parse_args(argv)
    if args.smoke:
        errors = [0.0, 0.5, -0.5]
        seeds = [101]
        textures = ["low_texture"]
        ranges = [8750.0]
        scenes = ["target_free", "moving_target"]
        compact = True
    else:
        errors = _parse_float_list(args.errors_deg, "errors-deg")
        seeds = _parse_int_list(args.seeds, "seeds")
        textures = list(args.textures)
        ranges = _parse_float_list(args.ranges_m, "ranges-m")
        scenes = list(args.scene)
        compact = True
    manifest = run_pilot(
        args.output_root.resolve(), args.simulator.resolve(), errors, seeds, textures, ranges, scenes,
        compact_input=compact,
        source_commit_override=args.source_commit,
        worktree_dirty_override=(None if args.worktree_dirty_before is None
                                 else args.worktree_dirty_before == "true"),
    )
    print(json.dumps({"status": manifest["status"], "output_root": str(args.output_root.resolve()),
                      "counts": manifest["counts"]}, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
