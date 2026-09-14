#!/usr/bin/env python3
"""Run the target-free clutter-only servo calibration pilot.

S1 is the estimator under test and receives one target-free C+N OFF raw
stream plus reported context.  Sassist is a separate target-assisted
reference branch that receives paired ON/OFF target evidence.  S0 and S1K
are evaluation branches; none of their information is passed to S1.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import platform
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import estimate_clutter_only_servo as clutter_only  # noqa: E402
import run_unknown_system_error_servo_pilot as target_assisted  # noqa: E402


TEMPLATE = ROOT / "configs/research/unknown_system_error_baseline_pilot.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
GMTI_CORE = ROOT / "build/GMTI_core"
HEADER_BYTES = 256
DEFAULT_ERRORS_DEG = (0.0, 0.05, -0.05, 0.10, -0.10, 0.20, -0.20, 0.50, -0.50)
DEFAULT_SEEDS = (101, 202)
DEFAULT_TEXTURES = ("low_texture", "high_texture")
DEFAULT_RANGES_M = (7500.0, 8750.0, 10000.0)
BRANCH_NAMES = ("S0", "S1", "S1K", "Sassist")
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


def branch_contract() -> dict[str, dict[str, object]]:
    """Return the four information conditions used by this pilot."""
    return {
        "S0": {
            "name": "Current",
            "calibration_mode": "current_reported",
            "estimator_input_role": "TARGET_ON_CURRENT_RAW_FOR_EVALUATION",
            "on_off_difference_used": False,
            "target_truth_used": False,
            "known_error_used": False,
            "servo_truth_used": False,
            "causal_data_sources": ["reported header angle", "target-on evaluation raw"],
        },
        "S1": {
            "name": "clutter_only",
            "calibration_mode": "clutter_only",
            "estimator_input_role": "OFF_C_PLUS_N_TARGET_FREE_ONLY",
            "on_off_difference_used": False,
            "target_truth_used": False,
            "known_error_used": False,
            "servo_truth_used": False,
            "causal_data_sources": list(clutter_only.CAUSAL_DATA_SOURCES),
        },
        "S1K": {
            "name": "known_servo",
            "calibration_mode": "known_error_upper_bound",
            "estimator_input_role": "EVALUATION_ONLY_KNOWN_ERROR",
            "on_off_difference_used": False,
            "target_truth_used": False,
            "known_error_used": True,
            "servo_truth_used": False,
            "causal_data_sources": ["configured known true-minus-reported servo error"],
        },
        "Sassist": {
            "name": "target_assisted",
            "calibration_mode": "target_assisted",
            "estimator_input_role": "PAIRED_ON_MINUS_OFF_TARGET_RESIDUAL",
            "on_off_difference_used": True,
            "target_truth_used": False,
            "known_error_used": False,
            "servo_truth_used": False,
            "causal_data_sources": [
                "paired ON-OFF target residual",
                "six pair cross-channel phases/coherences",
                "reported header beam angles",
                "configured nominal target/range hypothesis",
            ],
        },
    }


def estimator_input_audit(
    branch: str,
    input_paths: Sequence[str | Path],
    contains_on_off_difference: bool,
    contains_target_truth: bool,
    contains_known_error: bool,
    contains_servo_truth: bool,
) -> dict[str, object]:
    """Validate and record the information supplied to a branch estimator."""
    if branch not in BRANCH_NAMES:
        raise ValueError(f"unsupported servo pilot branch: {branch}")
    if branch == "S1" and any(
        ("/on/" in str(path).replace("\\", "/") or str(path).endswith("/on"))
        for path in input_paths
    ):
        raise ValueError("S1 clutter-only estimator cannot receive ON target data")
    if branch == "S1" and any((
        contains_on_off_difference,
        contains_target_truth,
        contains_known_error,
        contains_servo_truth,
    )):
        raise ValueError("S1 clutter-only estimator received a forbidden information source")
    return {
        "branch": branch,
        "input_paths": [str(path) for path in input_paths],
        "target_free_c_plus_n_only": branch == "S1"
        and not contains_on_off_difference
        and not contains_target_truth
        and not contains_known_error
        and not contains_servo_truth,
        "contains_on_off_difference": bool(contains_on_off_difference),
        "contains_target_truth": bool(contains_target_truth),
        "contains_known_error": bool(contains_known_error),
        "contains_servo_truth": bool(contains_servo_truth),
    }


def evaluation_only_fields(
    evaluated: bool,
    target_pd: object = None,
    false_hit_fraction: object = None,
    causal_target_transfer_db: object = None,
) -> dict[str, object]:
    """Return nullable target-evaluation fields with explicit scope."""
    if not evaluated:
        target_pd = None
        false_hit_fraction = None
        causal_target_transfer_db = None
    return {
        "evaluation_scope": "target_evaluation_only" if evaluated else "not_evaluated",
        "target_pd_evaluation_only": target_pd,
        "false_hit_fraction_evaluation_only": false_hit_fraction,
        "causal_target_transfer_db_evaluation_only": causal_target_transfer_db,
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


def _parse_float_list(values: Sequence[str], name: str) -> list[float]:
    result: list[float] = []
    for raw in values:
        for item in raw.split(","):
            if not item.strip():
                continue
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
            if not item.strip():
                continue
            result.append(int(item))
    if not result:
        raise ValueError(f"{name} cannot be empty")
    return result


def _prepare_template(template: Mapping[str, object]) -> dict[str, object]:
    config = target_assisted._prepare_template(template)
    config["truth_output"] = False
    return config


def _range_sample(range_center_m: float, config: Mapping[str, object]) -> int:
    waveform = config["waveform"]
    processing = config["range_processing"]
    if not isinstance(waveform, Mapping) or not isinstance(processing, Mapping):
        raise ValueError("prepared waveform/range_processing must be objects")
    fs_hz = float(waveform.get("fs_hz", float(waveform.get("fs_mhz", 60.0)) * 1.0e6))
    delay_sec = float(processing.get("sample_delay_us", 0.0)) * 1.0e-6
    sample = (2.0 * float(range_center_m) / clutter_only.geometry.C - delay_sec) * fs_hz
    if not math.isfinite(sample) or sample <= 0.0:
        raise ValueError(f"range center does not map to positive sample: {range_center_m}")
    return int(round(sample))


def _case_config(
    template: Mapping[str, object],
    case_root: Path,
    error_deg: float,
    seed: int,
    texture: str,
    range_center_m: float,
) -> dict[str, object]:
    if texture not in TEXTURE_CONFIGS:
        raise ValueError(f"unsupported texture: {texture}")
    config = copy.deepcopy(template)
    case_id = (
        f"clutter_servo_{texture}_{range_center_m:.0f}m_{error_deg:+.3f}deg_seed_{seed}"
        .replace("+", "p")
        .replace("-", "m")
        .replace(".", "p")
    )
    config["case_id"] = case_id
    config["output_dir"] = str((case_root / "on").resolve())
    config["paired_background_output_dir"] = str((case_root / "off").resolve())
    random_cfg = config.get("random")
    if not isinstance(random_cfg, dict):
        raise ValueError("prepared random config is not an object")
    random_cfg["random_seed"] = int(seed)
    scene = config.get("scene")
    if not isinstance(scene, dict):
        raise ValueError("prepared scene is not an object")
    scene["range_min_m"] = float(range_center_m) - 500.0
    scene["range_max_m"] = float(range_center_m) + 500.0
    area = scene.get("area_clutter")
    if not isinstance(area, dict):
        raise ValueError("prepared area_clutter is not an object")
    area.update(TEXTURE_CONFIGS[texture])
    targets = config.get("targets")
    if not isinstance(targets, list) or not targets or not isinstance(targets[0], dict):
        raise ValueError("prepared target list is invalid")
    target = targets[0]
    target["target_id"] = "CLUTTER_SERVO_EVAL_TARGET"
    target["enabled"] = True
    init = target.get("init")
    if not isinstance(init, dict):
        raise ValueError("prepared target init is invalid")
    init["expected_bin"] = _range_sample(range_center_m, config)
    config["servo_angle_error"] = {
        "enabled": True,
        "true_minus_reported_deg": float(error_deg),
    }
    return config


def _channel_positions(config: Mapping[str, object]) -> list[list[float]]:
    channel_geometry = config.get("channel_geometry")
    if not isinstance(channel_geometry, Mapping):
        raise ValueError("channel_geometry is required")
    positions: list[list[float]] = []
    for index in range(1, 5):
        item = channel_geometry.get(f"channel_{index}")
        if not isinstance(item, Mapping):
            raise ValueError(f"channel_{index} geometry is required")
        reported = item.get("reported", item)
        if not isinstance(reported, Mapping):
            raise ValueError(f"channel_{index}.reported geometry is invalid")
        positions.append([
            float(reported["x_m"]),
            float(reported["y_m"]),
            float(reported["z_m"]),
        ])
    return positions


def _reported_context(config: Mapping[str, object], source_id: str, range_center_m: float) -> dict[str, object]:
    waveform = config.get("waveform")
    scan = config.get("scan")
    platform = config.get("platform")
    processing = config.get("range_processing")
    simulation_geometry = config.get("simulation_geometry")
    scene = config.get("scene")
    if not all(isinstance(value, Mapping) for value in (waveform, scan, platform, processing)):
        raise ValueError("config lacks reported waveform/scan/platform/range context")
    if not isinstance(simulation_geometry, Mapping):
        simulation_geometry = {}
    if not isinstance(scene, Mapping):
        scene = {}
    reported_platform = dict(platform)
    reported_scan = dict(scan)
    reported_waveform = dict(waveform)
    reported_scan["pulses_per_beam"] = int(reported_waveform.get("pulse_num", 0))
    speed = float(reported_platform.get("speed_mps", 0.0))
    beam_count = int(reported_scan.get("beam_count", 0))
    pulse_num = int(reported_waveform.get("pulse_num", 0))
    prf_hz = float(reported_waveform.get("prf_hz", 0.0))
    reference_sample = (beam_count // 2) * pulse_num + pulse_num // 2
    reference_position = [
        float(reported_platform.get("position_x_m", 0.0)) + speed * reference_sample / prf_hz,
        float(reported_platform.get("position_y_m", 0.0)),
        float(reported_platform.get("height_m", 0.0)),
    ]
    return {
        "reported_channel_positions_m": _channel_positions(config),
        "reported_platform": reported_platform,
        "reported_scan": reported_scan,
        "waveform": reported_waveform,
        "range_processing": dict(processing),
        "reported_simulation_geometry": dict(simulation_geometry),
        "reported_scene": {"ground_z_m": float(scene.get("ground_z_m", 0.0))},
        "reported_reference_platform_position_m": reference_position,
        "representative_range_sample": _range_sample(range_center_m, config),
        "source_id": source_id,
    }


def _estimate_row(
    case: Mapping[str, object],
    branch: str,
    result: Mapping[str, object],
    input_audit: Mapping[str, object],
) -> dict[str, object]:
    if branch == "S1":
        estimate = result.get("estimate_deg")
        phase_rmse = result.get("phase_rmse")
        ridge_rmse = result.get("ridge_rmse")
        status = result.get("status")
        method = result.get("method")
        uncertainty = result.get("uncertainty_deg")
        model_mismatch = result.get("model_mismatch")
        fallback_reason = result.get("fallback_reason")
        causal_sources = result.get("causal_data_sources")
        fit_rmse = result.get("fit_rmse")
    elif branch == "Sassist":
        estimate = result.get("estimated_true_minus_reported_deg")
        phase_rmse = result.get("mean_phase_residual_rms_rad")
        ridge_rmse = None
        status = "VALID" if result.get("fit_status") == "fit" else "FALLBACK_UNIDENTIFIABLE"
        method = "target_assisted_six_pair_phase_plus_multibeam_power_quadratic_pilot"
        uncertainty = result.get("uncertainty_deg")
        model_mismatch = None
        fallback_reason = None if status == "VALID" else "quadratic_peak_unidentifiable"
        causal_sources = [
            "paired ON-OFF target residual",
            "six pair cross-channel phases/coherences",
            "reported header beam angles",
            "configured nominal target/range hypothesis",
        ]
        fit_rmse = None
    else:
        raise ValueError(f"unsupported estimator branch: {branch}")
    return {
        "case_id": case["case_id"],
        "error_deg": case["error_deg"],
        "seed": case["seed"],
        "texture": case["texture"],
        "range_center_m": case["range_center_m"],
        "branch": branch,
        "calibration_mode": "clutter_only" if branch == "S1" else "target_assisted",
        "method": method,
        "estimate_deg": estimate,
        "uncertainty_deg": uncertainty,
        "fit_rmse": fit_rmse,
        "phase_rmse": phase_rmse,
        "ridge_rmse": ridge_rmse,
        "clutter_cancellation_db": result.get("clutter_cancellation_db"),
        "model_mismatch": model_mismatch,
        "fallback_reason": fallback_reason,
        "status": status,
        "causal_data_sources": causal_sources,
        "input_role": branch_contract()[branch]["estimator_input_role"],
        "target_truth_used": input_audit.get("contains_target_truth"),
        "on_off_difference_used": input_audit.get("contains_on_off_difference"),
        "known_error_used": input_audit.get("contains_known_error"),
        "servo_truth_used": input_audit.get("contains_servo_truth"),
        "source_ids": result.get("source_ids") or input_audit.get("input_paths", []),
    }


def _zero_deadband(rows: Sequence[Mapping[str, object]]) -> float | None:
    values = [
        abs(float(row["estimate_deg"]))
        for row in rows
        if abs(float(row["error_deg"])) < 1.0e-12
        and row.get("estimate_deg") not in (None, "")
        and str(row.get("status")) in {"VALID", "fit"}
    ]
    return max(values) if values else None


def _decision(
    case: Mapping[str, object],
    branch: str,
    estimate_row: Mapping[str, object] | None,
    deadband_deg: float,
) -> dict[str, object]:
    error = float(case["error_deg"])
    if branch == "S0":
        action = "KEEP_CURRENT"
        applied = 0.0
        source = "reported_header_current"
        status = "CURRENT"
        estimate = None
        uncertainty = None
        fallback_reason = None
        model_mismatch = False
        audit = estimator_input_audit(branch, [case["on_raw"]], False, False, False, False)
    elif branch == "S1K":
        action = "NO_CORRECTION_NEEDED" if abs(error) < 1.0e-12 else "APPLY_KNOWN_ERROR_CORRECTION"
        applied = error
        source = "known_error_upper_bound"
        status = "KNOWN_EVALUATION"
        estimate = error
        uncertainty = 0.0
        fallback_reason = None
        model_mismatch = False
        audit = estimator_input_audit(branch, [case["on_raw"]], False, False, True, False)
    else:
        if estimate_row is None:
            raise RuntimeError(f"missing estimator row for {branch}")
        estimate = estimate_row.get("estimate_deg")
        uncertainty = estimate_row.get("uncertainty_deg")
        model_mismatch = bool(estimate_row.get("model_mismatch", False))
        fallback_reason = estimate_row.get("fallback_reason")
        fit_status = str(estimate_row.get("status"))
        if fit_status not in {"VALID", "fit"} or estimate is None:
            action = "KEEP_CURRENT"
            applied = 0.0
            source = "fallback_current"
        else:
            threshold = max(
                deadband_deg,
                float(uncertainty) if uncertainty is not None else 0.0,
            )
            if abs(float(estimate)) <= threshold:
                action = "NO_CORRECTION_NEEDED"
                applied = 0.0
                source = "deadband_current"
            else:
                action = "APPLY_ESTIMATED_CORRECTION"
                applied = float(estimate)
                source = f"{branch.lower()}_estimate"
        status = fit_status
        audit = estimator_input_audit(
            branch,
            [case["off_raw"]] if branch == "S1" else [case["on_raw"], case["off_raw"]],
            branch == "Sassist",
            False,
            False,
            False,
        )
    return {
        "case_id": case["case_id"],
        "error_deg": error,
        "seed": case["seed"],
        "texture": case["texture"],
        "range_center_m": case["range_center_m"],
        "branch": branch,
        "calibration_mode": branch_contract()[branch]["calibration_mode"],
        "method": None if estimate_row is None else estimate_row.get("method"),
        "status": status,
        "decision": action,
        "estimate_deg": estimate,
        "uncertainty_deg": uncertainty,
        "zero_error_deadband_deg": deadband_deg,
        "applied_correction_deg": applied,
        "correction_source": source,
        "model_mismatch": model_mismatch,
        "fallback_reason": fallback_reason,
        "causal_data_sources": (
            estimate_row.get("causal_data_sources")
            if estimate_row is not None
            else branch_contract()[branch]["causal_data_sources"]
        ),
        "target_truth_used": audit["contains_target_truth"],
        "on_off_difference_used": audit["contains_on_off_difference"],
        "known_error_used": audit["contains_known_error"],
        "servo_truth_used": audit["contains_servo_truth"],
        "input_paths": audit["input_paths"],
    }


def _aggregate_branch(
    branch: str,
    estimate_rows: Sequence[Mapping[str, object]],
    decision_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    selected = [row for row in estimate_rows if row.get("branch") == branch]
    residuals = [
        float(row["estimate_deg"]) - float(row["error_deg"])
        for row in selected
        if row.get("estimate_deg") not in (None, "")
    ]
    uncertainties = [
        float(row["uncertainty_deg"])
        for row in selected
        if row.get("uncertainty_deg") not in (None, "")
    ]
    fallbacks = sum(
        str(row.get("status")) not in {"VALID", "fit"}
        for row in selected
    )
    mismatches = sum(bool(row.get("model_mismatch")) for row in selected)
    phase = [float(row["phase_rmse"]) for row in selected if row.get("phase_rmse") not in (None, "")]
    ridge = [float(row["ridge_rmse"]) for row in selected if row.get("ridge_rmse") not in (None, "")]
    result: dict[str, object] = {
        "branch": branch,
        "case_count": len(selected),
        "estimate_count": len(residuals),
        "bias_deg": None if not residuals else float(np.mean(residuals)),
        "rmse_deg": None if not residuals else float(np.sqrt(np.mean(np.square(residuals)))),
        "mae_deg": None if not residuals else float(np.mean(np.abs(residuals))),
        "mean_uncertainty_deg": None if not uncertainties else float(np.mean(uncertainties)),
        "fallback_rate": None if not selected else float(fallbacks / len(selected)),
        "model_mismatch_rate": None if not selected else float(mismatches / len(selected)),
        "mean_phase_rmse": None if not phase else float(np.mean(phase)),
        "mean_ridge_rmse": None if not ridge else float(np.mean(ridge)),
        "mean_clutter_cancellation_db": None,
        "decision_count": sum(1 for row in decision_rows if row.get("branch") == branch),
    }
    result.update(evaluation_only_fields(False))
    return result


def run_pilot(
    output_root: Path,
    simulator: Path,
    core: Path,
    errors_deg: Sequence[float],
    seeds: Sequence[int],
    textures: Sequence[str],
    ranges_m: Sequence[float],
    skip_core: bool = False,
    source_commit_override: Optional[str] = None,
    worktree_dirty_override: Optional[bool] = None,
) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty output: {output_root}")
    if not TEMPLATE.is_file():
        raise RuntimeError(f"missing template: {TEMPLATE}")
    if not simulator.is_file():
        raise RuntimeError(f"missing simulator: {simulator}")
    if not skip_core and not core.is_file():
        raise RuntimeError(f"missing GMTI_core: {core}")
    if not errors_deg or not seeds or not textures or not ranges_m:
        raise ValueError("clutter-only servo pilot requires non-empty levels")
    if any(texture not in TEXTURE_CONFIGS for texture in textures):
        raise ValueError(f"unsupported texture in {textures}")
    errors = [float(value) for value in errors_deg]
    seeds_int = [int(value) for value in seeds]
    ranges = [float(value) for value in ranges_m]
    if any(not math.isfinite(value) for value in (*errors, *ranges)):
        raise ValueError("servo errors and range centers must be finite")
    output_root.mkdir(parents=True, exist_ok=True)
    gpu = target_assisted._probe_gpu()
    if not skip_core and int(gpu["returncode"]) != 0:
        raise RuntimeError("nvidia-smi failed; refusing to claim a CUDA servo pilot")
    template = _prepare_template(json.loads(TEMPLATE.read_text(encoding="utf-8")))
    _write_json(output_root / "prepared_template.json", template)
    case_records: list[dict[str, object]] = []
    estimate_rows: list[dict[str, object]] = []
    case_index_rows: list[dict[str, object]] = []
    combinations = [
        (error, seed, texture, range_center)
        for error in errors
        for seed in seeds_int
        for texture in textures
        for range_center in ranges
    ]
    for case_index, (error_deg, seed, texture, range_center_m) in enumerate(combinations, 1):
        case_root = output_root / "cases" / f"case_{case_index:03d}"
        case_root.mkdir(parents=True, exist_ok=True)
        config = _case_config(template, case_root, error_deg, seed, texture, range_center_m)
        config_path = case_root / "scenario.run.json"
        _write_json(config_path, config)
        stage2_log = case_root / "stage2.log"
        rc, elapsed = target_assisted._run_logged(
            [str(simulator), "--config", str(config_path)], stage2_log
        )
        if rc != 0:
            raise RuntimeError(f"Stage2 failed for {config_path}; see {stage2_log}")
        on_root = case_root / "on"
        off_root = case_root / "off"
        on_raw = target_assisted._find_period_file(on_root)
        off_raw = target_assisted._find_period_file(off_root)
        source_xml = on_root / "config/temp_config_stage2_period_0000.xml"
        resolved_path = on_root / "scenario_resolved.json"
        for required in (source_xml, resolved_path):
            if not required.is_file():
                raise RuntimeError(f"missing clutter servo pilot artifact: {required}")
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        context = _reported_context(
            config,
            f"off_c_plus_n:{config['case_id']}",
            range_center_m,
        )
        clutter_audit = estimator_input_audit(
            "S1", [off_raw], False, False, False, False
        )
        packets = clutter_only.load_target_free_packets(off_raw, context)
        features = clutter_only.extract_clutter_features(packets, context)
        clutter_result = clutter_only.estimate_clutter_only_servo(features, {"seed": seed})
        _write_json(case_root / "clutter_only_servo_estimator.json", clutter_result)
        del packets
        del features
        target_result, target_pair_rows = target_assisted._estimate_servo(
            on_raw, off_raw, config, resolved, seed
        )
        target_audit = estimator_input_audit(
            "Sassist", [on_raw, off_raw], True, False, False, False
        )
        _write_json(case_root / "target_assisted_servo_estimator.json", target_result)
        _write_rows(case_root / "target_assisted_servo_observables.csv", target_pair_rows)
        case_id = str(config["case_id"])
        case: dict[str, object] = {
            "case_index": case_index,
            "case_id": case_id,
            "case_root": str(case_root),
            "error_deg": error_deg,
            "seed": seed,
            "texture": texture,
            "range_center_m": range_center_m,
            "stage2_exit_code": rc,
            "stage2_elapsed_s": elapsed,
            "scenario_config": str(config_path),
            "resolved_scenario": str(resolved_path),
            "source_xml": str(source_xml),
            "on_raw": str(on_raw),
            "off_raw": str(off_raw),
            "on_raw_sha256": target_assisted._sha256(on_raw),
            "off_raw_sha256": target_assisted._sha256(off_raw),
            "clutter_input_audit": clutter_audit,
            "target_assisted_input_audit": target_audit,
            "clutter_estimator": clutter_result,
            "target_assisted_estimator": target_result,
        }
        case_records.append(case)
        estimate_rows.append(_estimate_row(case, "S1", clutter_result, clutter_audit))
        estimate_rows.append(_estimate_row(case, "Sassist", target_result, target_audit))
        case_index_rows.append({
            "case_index": case_index,
            "case_id": case_id,
            "error_deg": error_deg,
            "seed": seed,
            "texture": texture,
            "range_center_m": range_center_m,
            "stage2_exit_code": rc,
            "stage2_elapsed_s": elapsed,
            "on_raw": str(on_raw),
            "off_raw": str(off_raw),
            "on_raw_sha256": case["on_raw_sha256"],
            "off_raw_sha256": case["off_raw_sha256"],
            "S1_input_role": clutter_audit["target_free_c_plus_n_only"],
            "Sassist_input_role": target_audit["branch"],
        })
        print(
            f"[clutter-servo][stage2] {case_index}/{len(combinations)} "
            f"texture={texture} range={range_center_m:.0f}m "
            f"error={error_deg:+.3f}deg seed={seed} "
            f"S1={clutter_result.get('status')}:{clutter_result.get('estimate_deg')} "
            f"Sassist={target_result.get('fit_status')}:{target_result.get('estimated_true_minus_reported_deg')}",
            flush=True,
        )

    s1_rows = [row for row in estimate_rows if row["branch"] == "S1"]
    assist_rows = [row for row in estimate_rows if row["branch"] == "Sassist"]
    deadbands = {"S1": _zero_deadband(s1_rows), "Sassist": _zero_deadband(assist_rows)}
    decision_rows: list[dict[str, object]] = []
    branch_inputs: list[tuple[dict[str, object], str, Path, dict[str, object]]] = []
    estimate_by_case_branch = {
        (str(row["case_id"]), str(row["branch"])): row for row in estimate_rows
    }
    packet_len = (
        int(template["waveform"]["pulse_len"])  # type: ignore[index]
        * int(template["waveform"]["new_protocol_channel_count"])  # type: ignore[index]
        * 2
        * 4
        + HEADER_BYTES
    )
    for case in case_records:
        case_id = str(case["case_id"])
        for branch in BRANCH_NAMES:
            estimate_row = estimate_by_case_branch.get((case_id, branch))
            deadband = deadbands.get(branch) or 0.0
            decision = _decision(case, branch, estimate_row, deadband)
            decision_rows.append(decision)
            case.setdefault("decisions", {})[branch] = decision
            if skip_core:
                continue
            raw_on = Path(case["on_raw"])
            if branch == "S0":
                raw_path = raw_on
                header_audit = {"correction_deg": 0.0, "fallback_current": True, "payload_unchanged": True}
            else:
                applied = float(decision["applied_correction_deg"])
                if abs(applied) > 0.0:
                    raw_path = Path(case["case_root"]) / "corrections" / branch / "on.bin"
                    header_audit = target_assisted._rewrite_header_angle_with_layout(
                        raw_on, raw_path, packet_len, applied
                    )
                else:
                    raw_path = raw_on
                    header_audit = {
                        "correction_deg": 0.0,
                        "fallback_current": True,
                        "payload_unchanged": True,
                        "raw_sha256": target_assisted._sha256(raw_on),
                    }
            case.setdefault("header_audits", {})[branch] = header_audit
            branch_inputs.append((
                case,
                branch,
                raw_path,
                {
                    "calibration_mode": decision["calibration_mode"],
                    "correction_source": decision["correction_source"],
                    "input_role": branch_contract()[branch]["estimator_input_role"],
                },
            ))
    _write_rows(output_root / "case_index.csv", case_index_rows)
    _write_rows(output_root / "servo_estimates.csv", estimate_rows)
    _write_rows(output_root / "servo_decisions.csv", decision_rows)
    core_rows: list[dict[str, object]] = []
    if not skip_core:
        for index, (case, branch, raw_path, branch_meta) in enumerate(branch_inputs, 1):
            print(
                f"[clutter-servo][cuda] {index}/{len(branch_inputs)} "
                f"{case['case_id']} {branch}",
                flush=True,
            )
            row = target_assisted._run_core_branch(case, branch, raw_path, core)
            row.update(branch_meta)
            core_rows.append(row)
            if row["status"] != "completed":
                raise RuntimeError(f"GMTI_core failed for {case['case_id']} {branch}; see {row['log_path']}")
    _write_rows(output_root / "core_metrics.csv", core_rows)
    aggregate_rows = [
        _aggregate_branch(branch, estimate_rows, decision_rows)
        for branch in ("S1", "Sassist")
    ]
    _write_rows(output_root / "aggregate_metrics.csv", aggregate_rows)
    source_status = target_assisted._git("status", "--short", "--untracked-files=all")
    source_commit = source_commit_override or target_assisted._git("rev-parse", "HEAD")
    source_dirty = bool(source_status) if worktree_dirty_override is None else bool(worktree_dirty_override)
    manifest: dict[str, object] = {
        "schema": "clutter_only_servo_pilot_v1",
        "status": "completed" if skip_core or all(row.get("status") == "completed" for row in core_rows) else "completed_with_failed_core",
        "ai_training": False,
        "ai_router": False,
        "branch_contract": branch_contract(),
        "estimator_input_audits": {
            "S1": {
                "target_free_c_plus_n_only": True,
                "contains_on_off_difference": False,
                "contains_target_truth": False,
                "contains_known_error": False,
                "contains_servo_truth": False,
                "source_rule": "one OFF C+N four-channel raw stream plus reported context",
            },
            "Sassist": {
                "target_free_c_plus_n_only": False,
                "contains_on_off_difference": True,
                "contains_target_truth": False,
                "contains_known_error": False,
                "contains_servo_truth": False,
                "source_rule": "paired ON-OFF target residual and target metadata; evaluation reference only",
            },
        },
        "levels": {
            "errors_deg": errors,
            "seeds": seeds_int,
            "textures": list(textures),
            "range_centers_m": ranges,
            "case_count": len(case_records),
        },
        "grid": {
            "min_deg": -0.75,
            "max_deg": 0.75,
            "step_deg": 0.0025,
            "weights": {"phase": 1.0, "ridge": 1.0, "p38": 0.75, "power": 0.25},
        },
        "fallback_policy": {
            "S1_zero_error_deadband_deg": deadbands["S1"],
            "Sassist_zero_error_deadband_deg": deadbands["Sassist"],
            "model_mismatch_action": "KEEP_CURRENT",
            "unidentifiable_action": "KEEP_CURRENT",
        },
        "source": {
            "working_directory": str(ROOT),
            "source_commit_before_run": source_commit,
            "worktree_status_before_run": source_status,
            "worktree_dirty_before_run": source_dirty,
            "template": str(TEMPLATE.resolve()),
            "simulator": str(simulator.resolve()),
            "gmticore": str(core.resolve()) if core.is_file() else None,
        },
        "execution": {
            "python": sys.version,
            "platform": platform.platform(),
            "gpu_probe_before_run": gpu,
            "working_directory": str(ROOT),
            "core_skipped": skip_core,
        },
        "counts": {
            "case_count": len(case_records),
            "estimate_rows": len(estimate_rows),
            "decision_rows": len(decision_rows),
            "core_rows": len(core_rows),
            "core_success": skip_core or all(row.get("status") == "completed" for row in core_rows),
        },
        "artifacts": {
            "case_index": str((output_root / "case_index.csv").resolve()),
            "servo_estimates": str((output_root / "servo_estimates.csv").resolve()),
            "servo_decisions": str((output_root / "servo_decisions.csv").resolve()),
            "aggregate_metrics": str((output_root / "aggregate_metrics.csv").resolve()),
            "core_metrics": str((output_root / "core_metrics.csv").resolve()),
        },
        "aggregate_metrics": aggregate_rows,
        "case_records": case_records,
        "limitations": [
            "S1 is a deterministic target-free clutter-only pilot; this run does not establish production online use.",
            "Sassist is a separate target-assisted reference and must not be reported under the S1 clutter-only estimator name.",
            "Target Pd, false-hit fraction, causal target transfer, TrackManager/PIPE retention, velocity coupling and yaw/pitch coupling are evaluation fields for later stages and are not populated here.",
            "The known-error S1K branch is an evaluator-only upper bound; it is not an estimator input.",
        ],
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", "--output-root", dest="output_root", type=Path,
                        default=ROOT / "outputs/clutter_only_servo_pilot_20260914_v1")
    parser.add_argument("--simulator", type=Path, default=SIMULATOR)
    parser.add_argument("--gmticore", type=Path, default=GMTI_CORE)
    parser.add_argument("--errors-deg", nargs="+", default=[str(value) for value in DEFAULT_ERRORS_DEG])
    parser.add_argument("--seeds", nargs="+", default=[str(value) for value in DEFAULT_SEEDS])
    parser.add_argument("--textures", nargs="+", default=list(DEFAULT_TEXTURES))
    parser.add_argument("--ranges-m", nargs="+", default=[str(value) for value in DEFAULT_RANGES_M])
    parser.add_argument("--skip-core", action="store_true", help="never run or claim GMTI_core results")
    parser.add_argument("--source-commit", default=None)
    parser.add_argument("--worktree-dirty-before", choices=("true", "false"), default=None)
    args = parser.parse_args(argv)
    manifest = run_pilot(
        args.output_root.resolve(),
        args.simulator.resolve(),
        args.gmticore.resolve(),
        _parse_float_list(args.errors_deg, "errors-deg"),
        _parse_int_list(args.seeds, "seeds"),
        list(args.textures),
        _parse_float_list(args.ranges_m, "ranges-m"),
        skip_core=args.skip_core,
        source_commit_override=args.source_commit,
        worktree_dirty_override=(
            None if args.worktree_dirty_before is None
            else args.worktree_dirty_before == "true"
        ),
    )
    print(json.dumps({
        "status": manifest["status"],
        "output_root": str(args.output_root.resolve()),
        "counts": manifest["counts"],
        "fallback_policy": manifest["fallback_policy"],
    }, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
