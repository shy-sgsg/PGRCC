#!/usr/bin/env python3
"""Paired scene and production orchestration helpers for delay Stage-1.

The first part of this module is deliberately side-effect-light.  It creates
scenario documents and prepares physical correction inputs, while the later
Stage-1 tasks add simulator/production execution and compact aggregation.
All scientific estimator calls operate on fused F1/F2 data; raw four-channel
IQ is retained only as the production input representation and audit source.
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
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

# When invoked as ``python scripts/run_delay_stage1_formal.py`` Python places
# ``scripts/`` (rather than the repository root) on sys.path.  Keep the
# command-line entry point equivalent to ``python -m`` without changing the
# package imports used by tests and callers.
_SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""} and str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from scripts.analyze_four_channel_observables import iter_raw_packets
from scripts.audit_two_channel_error_observability import fuse_protocol_channels_to_f1_f2
from scripts.estimate_channel_delay import rewrite_float32_protocol_delay


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/research/channel_delay_stage1_formal.json"
TEMPLATE = ROOT / "simulator/scenarios/beam50_one_targets_continuous.run.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
PIPE = ROOT / "build/GMTI_pipe_core"
SHM_INTEGRATION = ROOT / "scripts/run_shm_phase1_integration.sh"
DEFAULT_THEORETICAL_PFA = 1.0e-6
DEFAULT_ADDITIVE_TOLERANCE = 5.0e-5
_CONDITIONS = (
    "A0_Ideal",
    "A1_Current_unknown_error",
    "A2_Known_error_correction_upper_bound",
    "A3_Blind_target_free_estimated_correction",
)
_ROLES = ("on", "off", "target_only")
_FOUR_CHANNEL_GEOMETRY = {
    "mode": "reported_channel_positions",
    "channel_1": {"name": "left_upper", "x_m": -0.085, "y_m": 0.0, "z_m": 0.085},
    "channel_2": {"name": "right_upper", "x_m": 0.085, "y_m": 0.0, "z_m": 0.085},
    "channel_3": {"name": "left_lower", "x_m": -0.085, "y_m": 0.0, "z_m": -0.085},
    "channel_4": {"name": "right_lower", "x_m": 0.085, "y_m": 0.0, "z_m": -0.085},
}


def _finite_float(value: object, name: str) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _set_nested(mapping: dict[str, object], key: str, value: object) -> None:
    mapping[key] = value


def build_stage1_branch_contract() -> dict[str, object]:
    """Return the frozen four-condition comparison and provenance contract."""

    requirements: dict[str, dict[str, object]] = {
        "A0_Ideal": {
            "description": "zero delay error and nominal processor state",
            "delay_error_source": "zero",
            "correction_applied": False,
            "truth_used_in_estimator": False,
            "evaluation_only": False,
        },
        "A1_Current_unknown_error": {
            "description": "injected delay error with no processor correction",
            "delay_error_source": "injected_scene_only",
            "correction_applied": False,
            "truth_used_in_estimator": False,
            "evaluation_only": False,
        },
        "A2_Known_error_correction_upper_bound": {
            "description": "A1 input physically corrected with injected truth",
            "delay_error_source": "injected_scene",
            "correction_applied": True,
            "correction_source": "truth_evaluation_only",
            "truth_used_in_estimator": False,
            "truth_used_to_apply_correction": True,
            "evaluation_only": True,
        },
        "A3_Blind_target_free_estimated_correction": {
            "description": "A1 input physically corrected from target-free A1 OFF estimate",
            "delay_error_source": "injected_scene",
            "correction_applied": True,
            "correction_source": "A1_OFF_target_free_estimate",
            "truth_used_in_estimator": False,
            "truth_used_to_apply_correction": False,
            "evaluation_only": False,
        },
    }
    return {
        "schema_version": 1,
        "conditions": list(_CONDITIONS),
        "requirements": requirements,
        "shared_scene_identity": [
            "seed",
            "target",
            "clutter",
            "noise",
            "beam",
            "range",
            "velocity",
            "snr",
            "production_settings",
        ],
        "scientific_input": {
            "source": "four_channel_protocol_iq",
            "f1": "(channel_1 + channel_3) / 2",
            "f2": "(channel_2 + channel_4) / 2",
            "algorithm_input": "F1/F2 only",
            "raw_4ch_allowed_for": ["debug", "protocol_validation", "fusion_verification"],
        },
        "paired_roles": {
            "on": "S+C+N",
            "off": "C+N",
            "target_only": "S",
            "identity_rule": "same seed/background/target/production settings",
        },
        "ai_training": False,
        "router_enabled": False,
        "native_four_channel_stap": False,
    }


def audit_additive_triplet(
    on: np.ndarray,
    off: np.ndarray,
    target_only: np.ndarray,
    tolerance: float,
) -> dict[str, object]:
    """Audit the paired linear identity ``ON - OFF - TO == 0``.

    Invalid shape, non-finite data, or an invalid tolerance is a scientific
    gap, not a passing result.  The function never silently broadcasts arrays.
    """

    try:
        tol = float(tolerance)
    except (TypeError, ValueError):
        tol = math.nan
    base: dict[str, object] = {
        "status": "NOT_EVALUABLE",
        "max_abs_error": None,
        "rms_error": None,
        "sample_count": 0,
        "tolerance": tol if math.isfinite(tol) else None,
        "reason": "invalid_tolerance",
    }
    if not math.isfinite(tol) or tol < 0.0:
        return base
    try:
        arrays = tuple(np.asarray(value) for value in (on, off, target_only))
    except (TypeError, ValueError):
        base["reason"] = "invalid_array"
        return base
    if any(array.ndim == 0 for array in arrays):
        base["reason"] = "scalar_array_not_supported"
        return base
    if not (arrays[0].shape == arrays[1].shape == arrays[2].shape):
        base["reason"] = "shape_mismatch"
        return base
    count = int(arrays[0].size)
    base["sample_count"] = count
    if count == 0:
        base["reason"] = "empty_array"
        return base
    try:
        values = tuple(array.astype(np.complex128, copy=False) for array in arrays)
    except (TypeError, ValueError, OverflowError):
        base["reason"] = "non_numeric_array"
        return base
    if not all(bool(np.all(np.isfinite(array))) for array in values):
        base["reason"] = "non_finite_array"
        return base
    residual = values[0] - values[1] - values[2]
    magnitude = np.abs(residual)
    max_error = float(np.max(magnitude))
    rms_error = float(np.sqrt(np.mean(magnitude * magnitude)))
    base.update({"max_abs_error": max_error, "rms_error": rms_error})
    if max_error <= tol:
        base.update({"status": "passed", "reason": "on_minus_off_minus_target_within_tolerance"})
    else:
        base["reason"] = "on_minus_off_minus_target_exceeds_tolerance"
    return base


def _scenario_template(config: Mapping[str, object]) -> dict[str, object]:
    """Copy a simulator scenario and validate the fields used by this stage."""

    candidate: object = config.get("scenario", config)
    if not isinstance(candidate, Mapping):
        raise ValueError("config must be a simulator scenario mapping")
    scenario = copy.deepcopy(dict(candidate))
    waveform = scenario.get("waveform")
    random_cfg = scenario.get("random")
    scene = scenario.get("scene")
    targets = scenario.get("targets")
    if not isinstance(waveform, dict):
        raise ValueError("simulator scenario waveform object is required")
    if not isinstance(random_cfg, dict):
        raise ValueError("simulator scenario random object is required")
    if not isinstance(scene, dict):
        raise ValueError("simulator scenario scene object is required")
    if not isinstance(targets, list) or not targets or not all(isinstance(item, dict) for item in targets):
        raise ValueError("simulator scenario must contain a non-empty target list")
    channel_count = int(waveform.get("new_protocol_channel_count", 0))
    if channel_count not in {2, 4}:
        raise ValueError("waveform.new_protocol_channel_count must be 2 or 4 before Stage-1 expansion")
    waveform["new_protocol_channel_count"] = 4
    waveform["iq_data_type"] = "float32"
    waveform.setdefault("new_protocol_read_channel_1", 1)
    waveform.setdefault("new_protocol_read_channel_2", 2)
    scenario["channel_geometry"] = copy.deepcopy(_FOUR_CHANNEL_GEOMETRY)
    scene["output_signal_domain"] = "raw_lfm"
    scenario.setdefault("truth_output", True)
    return scenario


def _apply_scene_identity(scenario: dict[str, object], identity: Mapping[str, object]) -> None:
    random_cfg = scenario["random"]
    scene = scenario["scene"]
    targets = scenario["targets"]
    waveform = scenario["waveform"]
    assert isinstance(random_cfg, dict)
    assert isinstance(scene, dict)
    assert isinstance(targets, list)
    assert isinstance(waveform, dict)

    target_identity = identity.get("target")
    if target_identity is not None:
        if not isinstance(target_identity, Mapping):
            raise ValueError("scene_identity.target must be an object")
        if not targets or not isinstance(targets[0], dict):
            raise ValueError("scenario must contain a target object")
        targets[0].update(copy.deepcopy(dict(target_identity)))
    clutter_identity = identity.get("clutter")
    if clutter_identity is not None:
        if not isinstance(clutter_identity, Mapping):
            raise ValueError("scene_identity.clutter must be an object")
        area = scene.setdefault("area_clutter", {})
        if not isinstance(area, dict):
            raise ValueError("scene.area_clutter must be an object")
        area.update(copy.deepcopy(dict(clutter_identity)))
    noise_identity = identity.get("noise")
    if noise_identity is not None:
        if not isinstance(noise_identity, Mapping):
            raise ValueError("scene_identity.noise must be an object")
        noise = scene.setdefault("thermal_noise", {})
        if not isinstance(noise, dict):
            raise ValueError("scene.thermal_noise must be an object")
        noise.update(copy.deepcopy(dict(noise_identity)))
    production_identity = identity.get("production")
    if production_identity is not None:
        if not isinstance(production_identity, Mapping):
            raise ValueError("scene_identity.production must be an object")
        production = scenario.setdefault("production", {})
        if not isinstance(production, dict):
            raise ValueError("scenario.production must be an object")
        production.update(copy.deepcopy(dict(production_identity)))

    if "seed" in identity:
        seed = int(identity["seed"])
        random_cfg["random_seed"] = seed
    if "random_seed" in identity:
        random_cfg["random_seed"] = int(identity["random_seed"])
    if "period_count" in identity:
        count = int(identity["period_count"])
        if count <= 0:
            raise ValueError("scene_identity.period_count must be positive")
        random_cfg["period_count"] = count
    if "beam_id" in identity:
        beam_id = int(identity["beam_id"])
        if beam_id <= 0:
            raise ValueError("scene_identity.beam_id must be positive")
        random_cfg["beam_start"] = beam_id
        for target in targets:
            init = target.setdefault("init", {})
            if isinstance(init, dict):
                init["beam_id"] = beam_id
    velocity_value = identity.get("target_velocity_mps", identity.get("velocity_mps"))
    if velocity_value is not None:
        speed = _finite_float(velocity_value, "target_velocity_mps")
        if speed <= 0.0:
            raise ValueError("target_velocity_mps must be positive")
        ve = -0.8944271909999159 * speed
        vn = -0.4472135954999579 * speed
        for target in targets:
            motion = target.setdefault("motion", {})
            if isinstance(motion, dict):
                motion.update({"type": "enu_velocity", "ve_mps": ve, "vn_mps": vn})
    snr_value = identity.get("target_snr_db", identity.get("snr_db"))
    if snr_value is not None:
        snr = _finite_float(snr_value, "target_snr_db")
        for target in targets:
            amplitude = target.setdefault("amplitude", {})
            if isinstance(amplitude, dict):
                amplitude.update({"type": "snr_db", "snr_db": snr})
    if "expected_bin" in identity:
        expected_bin = int(identity["expected_bin"])
        for target in targets:
            init = target.setdefault("init", {})
            if isinstance(init, dict):
                init["expected_bin"] = expected_bin
    if "range_m" in identity:
        range_m = _finite_float(identity["range_m"], "range_m")
        scene["range_min_m"] = range_m - 500.0
        scene["range_max_m"] = range_m + 500.0
        for target in targets:
            init = target.setdefault("init", {})
            if isinstance(init, dict):
                init.setdefault("range_m", range_m)
    if "texture_sigma" in identity or "clutter_rho" in identity:
        area = scene.setdefault("area_clutter", {})
        if not isinstance(area, dict):
            raise ValueError("scene.area_clutter must be an object")
        if "texture_sigma" in identity:
            area["texture_sigma"] = _finite_float(identity["texture_sigma"], "texture_sigma")
        if "clutter_rho" in identity:
            rho = _finite_float(identity["clutter_rho"], "clutter_rho")
            if not -1.0 <= rho <= 1.0:
                raise ValueError("clutter_rho must be in [-1, 1]")
            area["temporal_correlation_rho"] = rho
    # Preserve any additional registered identity values for audit without
    # interpreting them as a hidden Cartesian product.
    scenario["stage1_scene_identity"] = copy.deepcopy(dict(identity))


def _set_delay(scenario: dict[str, object], delay_error_ns: float) -> None:
    impairments = scenario.setdefault("channel_impairments", {})
    if not isinstance(impairments, dict):
        raise ValueError("channel_impairments must be an object")
    impairments.update({
        "enabled": bool(delay_error_ns != 0.0),
        "channel_time_delay_ns": delay_error_ns,
        "channel_fixed_phase_mismatch_deg": 0.0,
        "per_pulse_phase_drift_deg": 0.0,
        "channel_range_shift_samples": 0.0,
    })


def _role_scenario(
    base: Mapping[str, object],
    condition: str,
    role: str,
    output_dir: Path,
    background_dir: Path,
    delay_error_ns: float,
    *,
    background_source_condition: str,
) -> dict[str, object]:
    scenario = copy.deepcopy(dict(base))
    scene = scenario["scene"]
    targets = scenario["targets"]
    assert isinstance(scene, dict)
    assert isinstance(targets, list)
    stage1_delay = 0.0 if condition == "A0_Ideal" else delay_error_ns
    _set_delay(scenario, stage1_delay)
    scenario["output_dir"] = str(output_dir.resolve())
    scenario["truth_output"] = role in {"on", "target_only"}
    scene["signal_only"] = role == "target_only"
    scene["output_signal_domain"] = "raw_lfm"
    for target in targets:
        if isinstance(target, dict):
            target["enabled"] = role != "off"
    scenario["stage1_condition"] = condition
    scenario["stage1_role"] = role
    scenario["stage1_pair_id"] = str(background_dir.resolve())
    scenario["stage1_background_source_condition"] = background_source_condition
    scenario["stage1_pairing"] = {
        "on": "S+C+N",
        "off": "C+N",
        "target_only": "S",
        # The simulator's legacy parser searches JSON key names globally;
        # keep audit metadata distinct from the top-level runtime key so an
        # A0 writer is not accidentally parsed as a background reader.
        "background_source_dir": str(background_dir.resolve()),
        "same_seed_and_scene_identity": True,
        "same_production_settings": True,
    }
    random_cfg = scenario.get("random")
    scene_identity = scenario.get("stage1_scene_identity", {})
    seed = None
    if isinstance(random_cfg, Mapping):
        seed = random_cfg.get("random_seed")
    scenario["pairing_provenance"] = {
        "seed": seed,
        "scene_identity": copy.deepcopy(scene_identity),
        "background_source": str(background_dir.resolve()),
        "condition": condition,
        "role": role,
    }
    # The simulator only permits writing a paired background from a zero-error
    # full scene.  A0 ON owns that generation; every other role reuses it.
    if condition == "A0_Ideal" and role == "on":
        scenario["paired_background_output_dir"] = str(background_dir.resolve())
        scenario.pop("background_input_dir", None)
    else:
        scenario["background_input_dir"] = str(background_dir.resolve())
        scenario.pop("paired_background_output_dir", None)
    return scenario


def build_scene_variants(
    config: Mapping[str, object],
    scene_identity: Mapping[str, object],
    delay_error_ns: float,
    case_root: Path,
) -> dict[str, Path]:
    """Write paired A0/A1 ON/OFF/TO scenario documents.

    A0 ON materializes the exact C+N background once.  A1 reuses those bytes
    and applies the non-zero channel delay after target injection, so its ON,
    OFF and target-only outputs remain algebraically paired while satisfying
    the simulator's zero-impairment background-writer constraint.
    """

    delay = _finite_float(delay_error_ns, "delay_error_ns")
    case_root = Path(case_root)
    base = _scenario_template(config)
    _apply_scene_identity(base, scene_identity)
    case_root.mkdir(parents=True, exist_ok=True)
    background_dir = case_root / "A0_background"
    result: dict[str, Path] = {}
    for condition in ("A0_Ideal", "A1_Current_unknown_error"):
        short = "A0" if condition.startswith("A0") else "A1"
        for role in _ROLES:
            role_name = {"on": "ON", "off": "OFF", "target_only": "TO"}[role]
            key = f"{short}_{role_name}"
            scenario = _role_scenario(
                base,
                condition,
                role,
                case_root / key,
                background_dir,
                delay,
                background_source_condition="A0_Ideal_zero_delay",
            )
            scenario["case_id"] = f"{scenario.get('case_id', 'stage1')}_{key}"
            path = case_root / "scenarios" / f"{key}.json"
            _write_json(path, scenario)
            result[key] = path
    metadata = {
        "schema_version": 1,
        "delay_error_ns": delay,
        "scene_identity": dict(scene_identity),
        "background_dir": str(background_dir.resolve()),
        "variants": {
            key: {"path": str(path.resolve()), "sha256": _sha256(path)}
            for key, path in result.items()
        },
        "pairing_rule": "A0 ON writes zero-error C+N; all A0/A1 OFF/TO and A1 ON reuse it",
    }
    _write_json(case_root / "scenes_manifest.json", metadata)
    return result


def _layout_value(layout: Mapping[str, object], key: str) -> object:
    if key not in layout:
        raise ValueError(f"layout missing required field: {key}")
    return layout[key]


def prepare_condition_inputs(
    case_root: Path,
    condition: str,
    period_paths: Sequence[Path],
    calibration_input: Path,
    delay_truth_ns: float,
    delay_estimate_ns: float | None,
    layout: Mapping[str, object],
) -> dict[str, object]:
    """Prepare raw or physically corrected period inputs for one condition.

    This function intentionally does not estimate from ``calibration_input``;
    the caller must supply the already-produced target-free estimate.  That
    makes the truth-blind boundary explicit and auditable.
    """

    if condition not in _CONDITIONS:
        raise ValueError(f"unknown Stage-1 condition: {condition}")
    # Validate the truth-blind estimate before unrelated path checks so a
    # missing A3 estimate is never hidden by an empty calibration fixture.
    estimate = None if delay_estimate_ns is None else _finite_float(delay_estimate_ns, "delay_estimate_ns")
    if condition == "A3_Blind_target_free_estimated_correction" and estimate is None:
        raise ValueError("A3 requires a finite target-free delay estimate; refusing Current fallback")
    paths = [Path(path) for path in period_paths]
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError("period_paths must contain existing files")
    calibration = Path(calibration_input)
    calibration_exists = calibration.is_file()
    truth = _finite_float(delay_truth_ns, "delay_truth_ns")
    pulse_len = int(_layout_value(layout, "pulse_len"))
    channel_count = int(_layout_value(layout, "channel_count"))
    fs_hz = _finite_float(_layout_value(layout, "fs_hz"), "layout.fs_hz")
    indices_value = _layout_value(layout, "correction_channel_indices")
    if not isinstance(indices_value, Sequence) or isinstance(indices_value, (str, bytes)):
        raise ValueError("layout.correction_channel_indices must be a sequence")
    indices = tuple(int(index) for index in indices_value)
    if pulse_len <= 0 or channel_count != 4 or not indices:
        raise ValueError("layout requires positive pulse_len, four channels and correction indices")
    should_correct = condition in {
        "A2_Known_error_correction_upper_bound",
        "A3_Blind_target_free_estimated_correction",
    }
    if condition == "A3_Blind_target_free_estimated_correction" and estimate is None:
        raise ValueError("A3 requires a finite target-free A1 OFF estimate; refusing Current fallback")
    if condition == "A2_Known_error_correction_upper_bound":
        applied_delay = truth
        source = "truth_evaluation_only"
        truth_to_apply = True
    elif condition == "A3_Blind_target_free_estimated_correction":
        applied_delay = float(estimate)
        source = "A1_OFF_target_free_estimate"
        truth_to_apply = False
    else:
        applied_delay = None
        source = "none"
        truth_to_apply = False

    corrected: list[Path] = []
    audits: list[dict[str, object]] = []
    if should_correct:
        correction_root = Path(case_root) / "prepared_inputs" / condition
        correction_root.mkdir(parents=True, exist_ok=True)
        for index, source_path in enumerate(paths):
            destination = correction_root / f"period_{index:04d}.bin"
            audit = rewrite_float32_protocol_delay(
                source_path,
                destination,
                pulse_len=pulse_len,
                channel_count=channel_count,
                fs_hz=fs_hz,
                delta_tau_sec=float(applied_delay) * 1.0e-9,
                channel_indices=indices,
            )
            audits.append(dict(audit))
            corrected.append(destination)
        output_paths = corrected
    else:
        output_paths = paths
    return {
        "schema_version": 1,
        "condition": condition,
        "raw_paths": paths,
        "corrected_paths": corrected,
        "period_paths": output_paths,
        "calibration_input": calibration,
        "calibration_input_exists": calibration_exists,
        "calibration_source": "A1_OFF_target_free_C_plus_N_only",
        "delay_truth_ns": truth,
        "delay_estimate_ns": estimate,
        "estimate": applied_delay if should_correct else None,
        "applied_delay_ns": applied_delay,
        "delay_residual_ns": (
            float(applied_delay) - truth if applied_delay is not None else None
        ),
        "residual_ns": (
            truth - float(applied_delay) if applied_delay is not None else None
        ),
        "correction_source": source,
        "source": source,
        "truth_used_in_estimator": False,
        "truth_used_to_apply_correction": truth_to_apply,
        "correction_applied": should_correct,
        "correction_audit": audits,
        "layout": {
            "pulse_len": pulse_len,
            "channel_count": channel_count,
            "fs_hz": fs_hz,
            "correction_channel_indices": list(indices),
        },
    }


def _layer(
    status: str,
    *,
    value: object = None,
    denominator: object = None,
    definition: str | None = None,
    reason: str | None = None,
    **extra: object,
) -> dict[str, object]:
    """Return one waterfall layer with an explicit metric contract.

    A missing denominator is intentionally represented as ``None``.  It is
    not silently changed to one, because that would turn a production log
    without valid-CUT accounting into an empirical Pfa claim.
    """

    result: dict[str, object] = {
        "status": status,
        "value": value,
        "denominator": denominator,
        "definition": definition,
    }
    if reason is not None:
        result["reason"] = reason
    result.update(extra)
    return result


def _csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _track_id(row: Mapping[str, object]) -> str | None:
    value = row.get("track_id")
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "confirmed"}


def _false_track_ids(debug_dir: Path | None) -> tuple[set[str], Path | None, str | None]:
    if debug_dir is None or not debug_dir.is_dir():
        return set(), None, "track_debug_dir_missing"
    results_path = debug_dir / "track_results.csv"
    if results_path.is_file():
        rows = _csv_rows(results_path)
        if rows and "track_id" in rows[0]:
            selected = {
                track_id
                for row in rows
                if ("is_output" not in row or _truthy(row.get("is_output")))
                and (track_id := _track_id(row)) is not None
            }
            return selected, results_path, None if selected else "no_output_track_ids"
    states_path = debug_dir / "track_states.csv"
    rows = _csv_rows(states_path)
    if not rows:
        return set(), states_path, "track_states_missing_or_empty"
    if "track_id" not in rows[0]:
        return set(), states_path, "track_id_column_missing"
    selected: set[str] = set()
    for row in rows:
        state = str(row.get("state", "")).strip().lower()
        if state and state not in {"confirmed", "output"}:
            continue
        if "is_output" in row and not _truthy(row.get("is_output")):
            continue
        value = _track_id(row)
        if value is not None:
            selected.add(value)
    return selected, states_path, None if selected else "no_confirmed_output_track_ids"


def evaluate_target_off_waterfall(
    result_dir: Path,
    debug_dir: Path | None,
    cfar_summary: Mapping[str, object],
    theoretical_pfa: float,
) -> dict[str, object]:
    """Keep CFAR, cluster, protocol and track false-alarm layers separate."""

    pfa = _finite_float(theoretical_pfa, "theoretical_pfa")
    if not 0.0 < pfa < 1.0:
        raise ValueError("theoretical_pfa must be in (0, 1)")
    valid_cut_raw = cfar_summary.get("valid_cut_count")
    hit_raw = cfar_summary.get("hit_cells")
    try:
        valid_cut = int(valid_cut_raw) if valid_cut_raw is not None else 0
        hit_cells = int(hit_raw) if hit_raw is not None else 0
    except (TypeError, ValueError):
        valid_cut, hit_cells = 0, 0
    if valid_cut > 0 and hit_cells >= 0:
        cell = _layer(
            "passed",
            value=float(hit_cells) / float(valid_cut),
            denominator=valid_cut,
            definition="empirical structured-clutter false-hit fraction: hit_cells / valid_cut_count",
            hit_cell_count=hit_cells,
            valid_cut_count=valid_cut,
            metric_name="empirical_structured_clutter_false_hit_fraction",
        )
    else:
        cell = _layer(
            "NOT_EVALUABLE",
            denominator=max(0, valid_cut),
            definition="empirical structured-clutter false-hit fraction: hit_cells / valid_cut_count",
            reason="valid_cut_count_missing_or_non_positive",
            hit_cell_count=max(0, hit_cells),
            valid_cut_count=max(0, valid_cut),
            metric_name="empirical_structured_clutter_false_hit_fraction",
        )

    cluster_ids = cfar_summary.get("cluster_ids")
    if isinstance(cluster_ids, Sequence) and not isinstance(cluster_ids, (str, bytes)):
        unique_ids = {str(value) for value in cluster_ids if str(value).strip()}
        clusters = _layer(
            "passed",
            value=len(unique_ids),
            denominator=1,
            definition="explicit false-cluster count in one target-off evaluation",
            cluster_ids=sorted(unique_ids),
        )
    else:
        try:
            count = int(cfar_summary.get("clusters"))
        except (TypeError, ValueError):
            count = -1
        clusters = (
            _layer(
                "passed",
                value=count,
                denominator=1,
                definition="explicit false-cluster count in one target-off evaluation",
                source="cfar_summary.clusters",
            )
            if count >= 0
            else _layer(
                "NOT_EVALUABLE",
                denominator=None,
                definition="explicit false-cluster count in one target-off evaluation",
                reason="explicit_cluster_count_missing",
            )
        )

    result_path = Path(result_dir)
    detection_files = sorted(result_path.glob("detection_results_GMTI*.csv"))
    if detection_files:
        detection_count = sum(len(_csv_rows(path)) for path in detection_files)
        protocol = _layer(
            "passed",
            value=detection_count,
            denominator=1,
            definition="all production OFF detection rows; no truth match is possible",
            source_paths=[str(path) for path in detection_files],
        )
    else:
        protocol = _layer(
            "NOT_EVALUABLE",
            denominator=None,
            definition="all production OFF detection rows; no truth match is possible",
            reason="production_detection_csv_missing",
        )

    track_ids, track_source, track_reason = _false_track_ids(Path(debug_dir) if debug_dir is not None else None)
    tracks = (
        _layer(
            "passed",
            value=len(track_ids),
            denominator=1,
            definition="unique production debug track IDs with Confirmed/output evidence",
            unique_track_ids=sorted(track_ids),
            source_path=str(track_source),
        )
        if track_reason is None
        else _layer(
            "NOT_EVALUABLE",
            denominator=None,
            definition="unique production debug track IDs with Confirmed/output evidence",
            reason=track_reason,
            source_path=str(track_source) if track_source else None,
        )
    )
    return {
        "schema_version": 1,
        "theoretical_go_cfar_cell_pfa": pfa,
        "cell_false_hit_fraction": cell,
        "empirical_structured_clutter_false_hit_fraction": cell,
        "false_clusters": clusters,
        "protocol_false_detections": protocol,
        "false_tracks": tracks,
        "layer_definitions": {
            "cell_false_hit_fraction": "hit_cells / valid_cut_count when the denominator is exported",
            "false_clusters": "explicit OFF CFAR cluster count or IDs",
            "protocol_false_detections": "rows in production detection CSV",
            "false_tracks": "unique production debug track IDs with Confirmed/output evidence",
        },
    }


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def _run_logged(
    command: Sequence[object],
    log_path: Path,
    *,
    timeout_s: float = 1800.0,
) -> tuple[int, float]:
    """Run one external stage with an auditable command and elapsed time."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    command_text = shlex.join(str(item) for item in command)
    returncode = 127
    with log_path.open("w", encoding="utf-8") as stream:
        stream.write(f"$ {command_text}\n")
        stream.flush()
        try:
            completed = subprocess.run(
                [str(item) for item in command],
                cwd=ROOT,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=float(timeout_s),
            )
            returncode = int(completed.returncode)
        except FileNotFoundError as exc:
            stream.write(f"\n[file_not_found] {exc}\n")
        except subprocess.TimeoutExpired:
            returncode = 124
            stream.write("\n[timeout_expired]=true\n")
        elapsed = time.monotonic() - start
        stream.write(f"\n[exit_code]={returncode}\n[elapsed_sec]={elapsed:.3f}\n")
    return returncode, elapsed


def _scenario_output_dir(scenario_path: Path) -> Path:
    scenario = _read_json(scenario_path)
    raw = scenario.get("output_dir")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"scenario output_dir is missing: {scenario_path}")
    value = Path(raw)
    return value.resolve() if value.is_absolute() else (ROOT / value).resolve()


def _period_paths_from_output(output_dir: Path, period_count: int) -> list[Path]:
    """Resolve the simulator's one-file-per-period manifest without guessing."""

    manifest_path = output_dir / "data" / "period_files.csv"
    rows = _csv_rows(manifest_path)
    by_period: dict[int, Path] = {}
    for row in rows:
        try:
            period = int(row.get("period_id", "-1"))
        except (TypeError, ValueError):
            continue
        raw = Path(str(row.get("file", "")))
        path = raw if raw.is_absolute() else output_dir / raw
        by_period[period] = path.resolve()
    result: list[Path] = []
    for period in range(int(period_count)):
        path = by_period.get(period)
        if path is None:
            candidates = sorted((output_dir / "data").glob(f"*period_{period:04d}.bin"))
            path = candidates[0].resolve() if candidates else None
        if path is None or not path.is_file():
            raise FileNotFoundError(f"missing simulator period file {period}: {output_dir}")
        result.append(path)
    return result


def _load_channels_from_periods(
    period_paths: Sequence[Path],
    layout: Mapping[str, object],
) -> np.ndarray:
    arrays: list[np.ndarray] = []
    for path in period_paths:
        for packet in iter_raw_packets(
            path,
            int(layout["pulse_len"]),
            int(layout["channel_count"]),
            str(layout["iq_data_type"]),
        ):
            channels = np.asarray(packet["channels"], dtype=np.complex128)
            if channels.ndim != 2 or channels.shape[1] != int(layout["channel_count"]):
                raise ValueError(f"unexpected channel shape in {path}: {channels.shape}")
            arrays.append(channels)
    if not arrays:
        raise ValueError("period inputs contain no validated packets")
    return np.concatenate(arrays, axis=0)


def _load_fused_from_periods(
    period_paths: Sequence[Path],
    layout: Mapping[str, object],
) -> tuple[np.ndarray, np.ndarray]:
    """Load F1/F2 as ``(packet, fast_time)`` matrices.

    A raw packet contains one pulse-by-sample matrix.  Concatenating packet
    samples before FFT would create artificial phase discontinuities at every
    packet boundary and violates the estimator's pulse model, so packet
    boundaries are retained here while the additivity audit may still use the
    flattened channel loader above.
    """

    f1_rows: list[np.ndarray] = []
    f2_rows: list[np.ndarray] = []
    pulse_len = int(layout["pulse_len"])
    channel_count = int(layout["channel_count"])
    iq_data_type = str(layout["iq_data_type"])
    for path in period_paths:
        for packet in iter_raw_packets(path, pulse_len, channel_count, iq_data_type):
            channels = np.asarray(packet["channels"], dtype=np.complex128)
            if channels.shape != (pulse_len, 4):
                raise ValueError(
                    "Stage-1 estimator requires four protocol channels before "
                    f"F1/F2 fusion, got {channels.shape}"
                )
            f1_rows.append(0.5 * (channels[:, 0] + channels[:, 2]))
            f2_rows.append(0.5 * (channels[:, 1] + channels[:, 3]))
    if not f1_rows:
        raise ValueError("period inputs contain no validated packets")
    return np.stack(f1_rows, axis=0), np.stack(f2_rows, axis=0)


def _write_rows(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(str(key))
    with path.open("w", encoding="utf-8", newline="") as stream:
        if not fields:
            return
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(_json_safe(dict(row)))


def _merge_binary_files(paths: Sequence[Path], destination: Path) -> Path:
    if not paths:
        raise ValueError("cannot merge an empty input list")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        for source in paths:
            if not source.is_file():
                raise FileNotFoundError(source)
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, output, length=8 * 1024 * 1024)
    if destination.stat().st_size <= 0:
        raise ValueError(f"merged input is empty: {destination}")
    return destination


def _input_periods(input_path: Path) -> list[Path]:
    path = Path(input_path)
    if path.is_dir():
        candidates = sorted(path.glob("period_*.bin"))
        if not candidates:
            candidates = sorted(path.glob("*.bin"))
        return [item.resolve() for item in candidates if item.is_file()]
    return [path.resolve()] if path.is_file() else []


def _production_branch_contract_metadata(branch_name: str) -> dict[str, object]:
    condition = branch_name
    if "_" in branch_name:
        condition = branch_name.rsplit("_", 1)[0]
    role = branch_name.rsplit("_", 1)[-1] if "_" in branch_name else None
    return {
        "branch_name": branch_name,
        "condition": condition,
        "role": role,
        "scientific_input": "four_channel_protocol_iq_fused_to_f1_f2",
        "truth_used_in_estimator": False,
        "production_gate_policy": "shared_configure_xml_and_production_track_manager",
    }


def run_production_branch(
    case_root: Path,
    branch_name: str,
    input_path: Path,
    source_xml: Path,
    truth_by_period: Mapping[int, Mapping[str, str]],
    period_count: int,
    input_mode: str,
    layout: Mapping[str, object],
    *,
    truth_path: Path | None = None,
    xml_overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Run one condition/role through the existing production TrackManager.

    ``input_path`` may be a directory of period files or one concatenated
    local-test file.  The production evaluator and TrackManager are imported
    from the established E2E runner; this function only supplies per-branch
    paths and preserves the same XML gate overrides.
    """

    if input_mode not in {"local", "shm"}:
        raise ValueError("input_mode must be local or shm")
    from scripts import run_track_manager_e2e as e2e

    branch_root = Path(case_root) / "production" / branch_name
    result_dir = branch_root / "result"
    configured_debug_dir = branch_root / "track_debug"
    branch_root.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {
        **_production_branch_contract_metadata(branch_name),
        "input_mode": input_mode,
        "input_path": str(Path(input_path).resolve()),
        "source_xml": str(Path(source_xml).resolve()),
        "period_count_expected": int(period_count),
        "truth_period_count": len(truth_by_period),
        "xml_overrides": dict(xml_overrides or {}),
        "status": "not_started",
    }
    periods = _input_periods(Path(input_path))
    if not periods:
        record.update({"status": "NOT_EVALUABLE", "reason": "production_input_missing"})
        _write_json(branch_root / "branch_manifest.json", record)
        return record
    if not Path(source_xml).is_file():
        record.update({"status": "NOT_EVALUABLE", "reason": "source_xml_missing"})
        _write_json(branch_root / "branch_manifest.json", record)
        return record
    if not SIMULATOR.is_file() or not e2e.PIPE.is_file():
        record.update({
            "status": "NOT_EVALUABLE",
            "reason": "production_binary_missing",
            "simulator": str(SIMULATOR),
            "pipe": str(e2e.PIPE),
        })
        _write_json(branch_root / "branch_manifest.json", record)
        return record
    if input_mode == "shm" and not e2e.SHM_INTEGRATION.is_file():
        record.update({"status": "NOT_EVALUABLE", "reason": "shm_wrapper_missing"})
        _write_json(branch_root / "branch_manifest.json", record)
        return record

    merged_input = Path(input_path)
    if merged_input.is_dir():
        merged_input = _merge_binary_files(periods, branch_root / "input_merged.bin")
    xml_path = branch_root / "production.xml"
    configure_rc, configure_log = e2e._configure_xml(
        Path(source_xml),
        xml_path,
        result_dir,
        configured_debug_dir,
        layout,
        xml_overrides=xml_overrides,
    )
    record.update({
        "configuration_returncode": configure_rc,
        "configuration_log": str(configure_log),
        "production_xml": str(xml_path),
        "period_paths": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in periods
        ],
    })
    if configure_rc != 0:
        record.update({"status": "configuration_failed"})
        _write_json(branch_root / "branch_manifest.json", record)
        return record
    xml_audit = e2e._audit_runtime_xml_layout(xml_path, layout)
    record["runtime_xml_protocol_layout"] = xml_audit
    if xml_audit.get("status") != "passed":
        record.update({"status": "configuration_audit_failed"})
        _write_json(branch_root / "branch_manifest.json", record)
        return record

    if input_mode == "local":
        command: list[object] = [
            e2e.PIPE,
            "--config", xml_path,
            "--result-dir", result_dir,
            "--track-debug-dir", branch_root / "track_debug_runs",
            "--track-debug-dump", "on",
            "--runtime-mode=debug",
            "--runtime-diagnostics=on",
            "--local-test",
        ]
        command.extend(f"{index + 1}={path}" for index, path in enumerate(periods))
        log_path = branch_root / "gmt_pipe_core.log"
        rc, elapsed = e2e._run_logged(command, log_path, timeout_s=1800.0)
        pipe_run_dir = None
    else:
        runs_root = branch_root / "integration_runs"
        command = [
            e2e.SHM_INTEGRATION,
            xml_path,
            merged_input,
            runs_root,
            1,
            1_000_000,
            1_048_576,
            int(period_count),
            300_000,
            "", "", "", "",
            900_000,
        ]
        log_path = branch_root / "shm_integration.log"
        environment = os.environ.copy()
        environment["GMTI_RUNTIME_MODE"] = "debug"
        rc, elapsed = e2e._run_logged(
            command,
            log_path,
            env=environment,
            timeout_s=1080.0,
        )
        pipe_run_dir = e2e._find_pipe_run(log_path, runs_root)
        if pipe_run_dir is not None:
            result_dir = pipe_run_dir / "result"

    debug_dir = e2e._find_debug_dir(result_dir, configured_debug_dir)
    pipe_metrics = e2e._parse_pipe_runtime_metrics(pipe_run_dir, log_path)
    cfar_log_paths: list[Path] = [log_path]
    if pipe_run_dir is not None:
        cfar_log_paths.extend(pipe_run_dir.rglob("gmticore.log"))
    cfar_summary = e2e._parse_cfar_summaries(cfar_log_paths)
    geometry_paths: list[Path] = []
    geometry_roots = [result_dir, debug_dir, branch_root]
    for root in geometry_roots:
        if root is None or not root.is_dir():
            continue
        geometry_paths.extend(
            candidate
            for pattern in ("cfar_geometry_diagnostics.csv", "cfar_geometry_diagnostics.json")
            for candidate in root.rglob(pattern)
            if candidate.is_file()
        )
    geometry_paths = sorted(set(path.resolve() for path in geometry_paths))
    geometry_summary: dict[str, object]
    if geometry_paths:
        try:
            from scripts.cfar_geometry_audit import load_geometry_rows

            geometry_rows = load_geometry_rows(geometry_paths)
            valid_cut_count = sum(int(row["valid_cut_count"]) for row in geometry_rows)
            hit_cut_count = sum(int(row["hit_cut_count"]) for row in geometry_rows)
            geometry_summary = {
                "status": "evaluable" if valid_cut_count > 0 else "NOT_EVALUABLE",
                "valid_cut_count": valid_cut_count,
                "hit_cut_count": hit_cut_count,
                "threshold_test_count": sum(int(row["threshold_test_count"]) for row in geometry_rows),
                "cell_pfa": (
                    float(hit_cut_count) / float(valid_cut_count)
                    if valid_cut_count > 0 else None
                ),
                "definition": "hit_cut_count / valid_cut_count",
                "reason": None if valid_cut_count > 0 else "valid_cut_count_missing_or_non_positive",
                "source_paths": [str(path) for path in geometry_paths],
                "rows": geometry_rows,
            }
            # The OFF waterfall must consume this physical denominator.  The
            # log's hit_cells field is retained only as a separate diagnostic.
            cfar_summary = dict(cfar_summary)
            cfar_summary.update({
                "valid_cut_count": valid_cut_count,
                "hit_cut_count": hit_cut_count,
                "hit_cells": hit_cut_count,
                "cell_pfa": geometry_summary["cell_pfa"],
            })
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            geometry_summary = {
                "status": "NOT_EVALUABLE",
                "valid_cut_count": 0,
                "hit_cut_count": 0,
                "cell_pfa": None,
                "definition": "hit_cut_count / valid_cut_count",
                "reason": f"cfar_geometry_invalid:{exc}",
                "source_paths": [str(path) for path in geometry_paths],
                "rows": [],
            }
    else:
        geometry_summary = {
            "status": "NOT_EVALUABLE",
            "valid_cut_count": 0,
            "hit_cut_count": 0,
            "cell_pfa": None,
            "definition": "hit_cut_count / valid_cut_count",
            "reason": "cfar_geometry_diagnostics_missing",
            "source_paths": [],
            "rows": [],
        }
    if debug_dir is None:
        audit: dict[str, object] = {
            "status": "missing",
            "total_violations": None,
            "protocol_target_failures": {"missing_track_debug_dir": 1},
        }
    else:
        try:
            from scripts.audit_track_manager_run import audit_debug_dir

            audit = audit_debug_dir(debug_dir)
        except (OSError, ValueError, KeyError) as exc:
            audit = {
                "status": "error",
                "total_violations": None,
                "protocol_target_failures": {"audit_exception": 1},
                "audit_exception": str(exc),
            }
    metric_row, cycle_rows, payload_rows = e2e._evaluate_branch(
        branch_name,
        result_dir,
        debug_dir,
        truth_by_period,
        int(period_count),
        audit,
        cfar_summary,
    )
    off_waterfall = None
    role = str(record.get("role", ""))
    if role == "OFF":
        off_waterfall = evaluate_target_off_waterfall(
            result_dir,
            debug_dir,
            cfar_summary,
            DEFAULT_THEORETICAL_PFA,
        )
    id_switch_audit_path: Path | None = None
    id_switch_rows: list[dict[str, object]] = []
    id_switch_status = "NOT_APPLICABLE"
    id_switch_reason: str | None = None
    id_switch_sources: dict[str, object] = {
        "track_debug_dir": str(debug_dir) if debug_dir is not None else None,
        "detection_dir": str(result_dir),
        "payload_path": (
            str(debug_dir / "track_output_payloads.csv")
            if debug_dir is not None else None
        ),
        "truth_path": str(truth_path) if truth_path is not None else None,
    }
    if role == "ON":
        id_switch_audit_path = branch_root / "id_switch_audit.csv"
        try:
            from scripts.audit_delay_track_id_switch import (
                audit_track_id_switches,
                classification_counts,
                missing_production_field_counts,
                write_audit,
            )

            payload_path = (
                debug_dir / "track_output_payloads.csv"
                if debug_dir is not None
                else branch_root / "missing_track_output_payloads.csv"
            )
            detection_files = sorted(result_dir.glob("detection_results_GMTI*.csv"))
            truth_file = (
                truth_path / "truth_targets_by_beam.csv"
                if truth_path is not None and truth_path.is_dir()
                else truth_path
            )
            missing_audit_inputs = []
            if debug_dir is None or not debug_dir.is_dir():
                missing_audit_inputs.append("track_debug_dir")
            if not payload_path.is_file():
                missing_audit_inputs.append("track_output_payloads.csv")
            if not detection_files:
                missing_audit_inputs.append("detection_results_GMTI*.csv")
            if truth_file is None or not truth_file.is_file():
                missing_audit_inputs.append("truth_targets_by_beam.csv")
            if missing_audit_inputs:
                id_switch_status = "NOT_EVALUABLE"
                id_switch_reason = "missing_audit_inputs:" + ",".join(missing_audit_inputs)
            else:
                id_switch_rows = audit_track_id_switches(
                    debug_dir,
                    result_dir,
                    payload_path,
                    truth_path,
                )
                id_switch_status = "passed"
            write_audit(id_switch_audit_path, id_switch_rows)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            id_switch_status = "error"
            id_switch_reason = str(exc)
            try:
                from scripts.audit_delay_track_id_switch import write_audit

                write_audit(id_switch_audit_path, [])
            except (OSError, ValueError):
                pass
    id_switch_counts = (
        classification_counts(id_switch_rows)
        if role == "ON"
        else None
    )
    id_switch_missing_field_counts = (
        missing_production_field_counts(id_switch_rows)
        if role == "ON"
        else None
    )
    production_status = "passed" if int(rc) == 0 and audit.get("status") == "pass" else "failed"
    record.update({
        "production_returncode": int(rc),
        "production_elapsed_sec": float(elapsed),
        "production_log": str(log_path),
        "pipe_run_dir": str(pipe_run_dir) if pipe_run_dir is not None else None,
        "result_dir": str(result_dir),
        "track_debug_dir": str(debug_dir) if debug_dir is not None else None,
        "pipe_runtime_metrics": pipe_metrics,
        "cfar_summary": cfar_summary,
        "cfar_geometry": geometry_summary,
        "audit": audit,
        "track_audit_total_violations": audit.get("total_violations"),
        "metrics": metric_row,
        "cycle_rows": cycle_rows,
        "protocol_rows": payload_rows,
        "target_off_waterfall": off_waterfall,
        "id_switch_audit_path": str(id_switch_audit_path) if id_switch_audit_path else None,
        "id_switch_audit_row_count": len(id_switch_rows) if role == "ON" else None,
        "id_switch_classification_counts": id_switch_counts,
        "id_switch_missing_production_field_counts": id_switch_missing_field_counts,
        "id_switch_audit_status": id_switch_status,
        "id_switch_audit_reason": id_switch_reason,
        "id_switch_audit_sources": id_switch_sources if role == "ON" else None,
        "production_status": production_status,
        "status": production_status,
    })
    _write_rows(branch_root / "cycle_metrics.csv", cycle_rows)
    _write_rows(branch_root / "protocol_payload_audit.csv", payload_rows)
    _write_json(branch_root / "branch_manifest.json", record)
    return record


def _load_stage1_template(config: Mapping[str, object]) -> dict[str, object]:
    candidate: object = config.get("scenario_template", config.get("scenario"))
    if isinstance(candidate, str):
        return _read_json(Path(candidate))
    if isinstance(candidate, Mapping) and "waveform" in candidate:
        return copy.deepcopy(dict(candidate))
    if "waveform" in config:
        return copy.deepcopy(dict(config))
    return _read_json(TEMPLATE)


def _select_delay_estimate(rows: Sequence[Mapping[str, object]]) -> tuple[float | None, str | None]:
    preference = (
        "D3_Huber_weighted_LS",
        "D2_weighted_LS",
        "D1_ordinary_LS",
    )
    by_method = {str(row.get("method")): row for row in rows}
    for method in preference:
        row = by_method.get(method)
        if row is None:
            continue
        value = row.get("delta_tau_ns")
        try:
            value_float = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if math.isfinite(value_float):
            return value_float, method
    return None, None


def _condition_role_periods(
    scene_runs: Mapping[str, Mapping[str, object]],
    condition: str,
    role: str,
    period_count: int,
) -> list[Path]:
    short = "A0" if condition == "A0_Ideal" else "A1"
    key = f"{short}_{role}"
    item = scene_runs.get(key)
    if not isinstance(item, Mapping) or int(item.get("returncode", 1)) != 0:
        return []
    output = item.get("output_dir")
    if not isinstance(output, str):
        return []
    try:
        return _period_paths_from_output(Path(output), period_count)
    except (FileNotFoundError, OSError, ValueError):
        return []


def _prepare_role_inputs(
    case_root: Path,
    condition: str,
    role: str,
    period_paths: Sequence[Path],
    calibration_input: Path,
    delay_truth_ns: float,
    delay_estimate_ns: float | None,
    layout: Mapping[str, object],
) -> dict[str, object]:
    return prepare_condition_inputs(
        case_root / "inputs" / condition / role,
        condition,
        period_paths,
        calibration_input,
        delay_truth_ns,
        delay_estimate_ns,
        layout,
    )


def run_stage1_case(
    config: Mapping[str, object],
    scene_identity: Mapping[str, object],
    output_root: Path,
    input_mode: str,
) -> dict[str, object]:
    """Execute one paired case through simulation, calibration and production."""

    if input_mode not in {"local", "shm"}:
        raise ValueError("input_mode must be local or shm")
    base = _load_stage1_template(config)
    delay_value = scene_identity.get("delay_error_ns", config.get("delay_error_ns"))
    if delay_value is None:
        raise ValueError("scene_identity.delay_error_ns or config.delay_error_ns is required")
    delay = _finite_float(delay_value, "delay_error_ns")
    case_root = Path(output_root).resolve()
    case_root.mkdir(parents=True, exist_ok=True)
    if any(case_root.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty case output: {case_root}")
    variants = build_scene_variants(base, scene_identity, delay, case_root / "scenes")
    manifest: dict[str, object] = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_root": str(case_root),
        "input_mode": input_mode,
        "scene_identity": dict(scene_identity),
        "delay_error_ns": delay,
        "branch_contract": build_stage1_branch_contract(),
        "ai_training": False,
        "router_enabled": False,
        "scene_variants": {key: str(path) for key, path in variants.items()},
        "simulation": {},
        "conditions": {},
        "additive_audits": {},
        "status": "not_started",
    }
    if not SIMULATOR.is_file():
        manifest.update({"status": "NOT_EVALUABLE", "reason": "simulator_binary_missing"})
        _write_json(case_root / "case_manifest.json", manifest)
        return manifest

    # A0 ON must run first because it materializes the exact zero-error C+N
    # background consumed by every other paired scene.
    simulation_order = ["A0_ON", "A0_OFF", "A0_TO", "A1_ON", "A1_OFF", "A1_TO"]
    scene_runs: dict[str, dict[str, object]] = {}
    for key in simulation_order:
        scenario_path = variants[key]
        output_dir = _scenario_output_dir(scenario_path)
        log_path = case_root / "scenes" / "logs" / f"{key}.simulate.log"
        returncode, elapsed = _run_logged(
            [SIMULATOR, "--config", scenario_path],
            log_path,
            timeout_s=1800.0,
        )
        scene_runs[key] = {
            "scenario_path": str(scenario_path),
            "scenario_sha256": _sha256(scenario_path),
            "output_dir": str(output_dir),
            "log": str(log_path),
            "returncode": returncode,
            "elapsed_sec": elapsed,
            "status": "passed" if returncode == 0 else "failed",
        }
    manifest["simulation"] = scene_runs

    try:
        from scripts import run_track_manager_e2e as e2e

        scenario = _read_json(variants["A0_ON"])
        layout = e2e._protocol_layout_from_scenario(scenario)
    except (ImportError, OSError, ValueError, KeyError) as exc:
        manifest.update({"status": "NOT_EVALUABLE", "reason": f"layout_resolution_failed:{exc}"})
        _write_json(case_root / "case_manifest.json", manifest)
        return manifest
    manifest["protocol_layout"] = layout

    period_count_value = scene_identity.get(
        "period_count",
        scenario.get("random", {}).get("period_count", 0)
        if isinstance(scenario.get("random"), Mapping)
        else 0,
    )
    period_count = int(period_count_value)
    if period_count <= 0:
        manifest.update({"status": "NOT_EVALUABLE", "reason": "period_count_invalid"})
        _write_json(case_root / "case_manifest.json", manifest)
        return manifest

    scene_periods: dict[str, list[Path]] = {
        key: _condition_role_periods(
            scene_runs,
            "A0_Ideal" if key.startswith("A0") else "A1_Current_unknown_error",
            "ON" if key.endswith("_ON") else ("OFF" if key.endswith("_OFF") else "TO"),
            period_count,
        )
        for key in simulation_order
    }
    calibration_periods = scene_periods["A1_OFF"]
    if not calibration_periods:
        manifest.update({"status": "completed_with_gaps", "reason": "A1_OFF_calibration_input_missing"})
        _write_json(case_root / "case_manifest.json", manifest)
        return manifest
    calibration_input = _merge_binary_files(
        calibration_periods,
        case_root / "inputs" / "A1_OFF_target_free_calibration.bin",
    )

    # The only estimator input is the target-free A1 OFF C+N stream after F1/F2
    # fusion.  The injected delay is retained in the manifest for evaluation,
    # but is never passed to this call.
    try:
        from scripts.delay_stage1_core import delay_method_suite

        f1, f2 = _load_fused_from_periods(calibration_periods, layout)
        estimator_rows = delay_method_suite(f1, f2, float(layout["fs_hz"]))
        estimated_delay, selected_method = _select_delay_estimate(estimator_rows)
        estimator = {
            "status": "estimated" if estimated_delay is not None else "failed",
            "method_rows": estimator_rows,
            "selected_method": selected_method,
            "selected_delay_ns": estimated_delay,
            "input_path": str(calibration_input),
            "input_role": "A1_OFF_target_free_C_plus_N_only",
            "scientific_input": "F1=(C1+C3)/2, F2=(C2+C4)/2",
            "truth_used_in_estimator": False,
        }
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        estimated_delay = None
        estimator = {
            "status": "failed",
            "input_path": str(calibration_input),
            "input_role": "A1_OFF_target_free_C_plus_N_only",
            "truth_used_in_estimator": False,
            "error": str(exc),
        }
    manifest["delay_estimator"] = estimator

    raw_condition_periods = {
        "A0_Ideal": {
            role: scene_periods[f"A0_{role}"] for role in ("ON", "OFF", "TO")
        },
        "A1_Current_unknown_error": {
            role: scene_periods[f"A1_{role}"] for role in ("ON", "OFF", "TO")
        },
    }
    prepared: dict[str, dict[str, dict[str, object]]] = {}
    condition_sources = {
        "A0_Ideal": raw_condition_periods["A0_Ideal"],
        "A1_Current_unknown_error": raw_condition_periods["A1_Current_unknown_error"],
        "A2_Known_error_correction_upper_bound": raw_condition_periods["A1_Current_unknown_error"],
        "A3_Blind_target_free_estimated_correction": raw_condition_periods["A1_Current_unknown_error"],
    }
    for condition in _CONDITIONS:
        prepared[condition] = {}
        for role in ("ON", "OFF", "TO"):
            try:
                prepared[condition][role] = _prepare_role_inputs(
                    case_root,
                    condition,
                    role,
                    condition_sources[condition][role],
                    calibration_input,
                    delay,
                    estimated_delay,
                    layout,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                prepared[condition][role] = {
                    "condition": condition,
                    "role": role,
                    "status": "NOT_EVALUABLE",
                    "reason": str(exc),
                    "truth_used_in_estimator": False,
                    "correction_applied": False,
                    "period_paths": [],
                }

    # Additive audits are performed on payload samples, not packet headers,
    # because packet counters and timestamps are transport metadata.
    for condition in _CONDITIONS:
        role_arrays: dict[str, np.ndarray] = {}
        for role in ("ON", "OFF", "TO"):
            input_values = prepared[condition][role].get("period_paths", [])
            if isinstance(input_values, list) and input_values and all(isinstance(item, Path) for item in input_values):
                try:
                    role_arrays[role] = _load_channels_from_periods(input_values, layout)
                except (OSError, ValueError, RuntimeError):
                    pass
        if len(role_arrays) == 3:
            manifest["additive_audits"][condition] = audit_additive_triplet(
                role_arrays["ON"],
                role_arrays["OFF"],
                role_arrays["TO"],
                DEFAULT_ADDITIVE_TOLERANCE,
            )
        else:
            manifest["additive_audits"][condition] = {
                "status": "NOT_EVALUABLE",
                "reason": "one_or_more_condition_role_inputs_missing",
                "max_abs_error": None,
                "rms_error": None,
                "sample_count": 0,
                "tolerance": DEFAULT_ADDITIVE_TOLERANCE,
            }

    source_xml = Path(str(scene_runs["A0_ON"]["output_dir"])) / "config" / "temp_config_stage2_period_0000.xml"
    truth: dict[int, dict[str, str]] = {}
    try:
        truth = e2e._truth_by_period(Path(str(scene_runs["A0_ON"]["output_dir"])), period_count)
    except (OSError, ValueError, KeyError):
        truth = {}
    production_records: dict[str, dict[str, object]] = {}
    for condition in _CONDITIONS:
        role_records: dict[str, object] = {}
        for role in ("ON", "OFF", "TO"):
            prep = prepared[condition][role]
            period_values = prep.get("period_paths", [])
            if not isinstance(period_values, list) or not period_values or not all(isinstance(item, Path) for item in period_values):
                role_records[role] = {
                    "status": "NOT_EVALUABLE",
                    "reason": prep.get("reason", "prepared_input_missing"),
                    "condition": condition,
                    "role": role,
                }
                continue
            input_dir = case_root / "inputs" / "production" / condition / role
            input_dir.mkdir(parents=True, exist_ok=True)
            for index, source in enumerate(period_values):
                link = input_dir / f"period_{index:04d}.bin"
                if not link.exists():
                    link.symlink_to(source.resolve())
            branch = f"{condition}_{role}"
            role_records[role] = run_production_branch(
                case_root,
                branch,
                input_dir,
                source_xml,
                truth,
                period_count,
                input_mode,
                layout,
                truth_path=Path(str(scene_runs["A0_ON"]["output_dir"])) / "truth",
            )
        production_records[condition] = role_records
    manifest["conditions"] = {
        condition: {
            "input_preparation": prepared[condition],
            "production": production_records[condition],
            "truth_used_in_estimator": False,
            "correction_applied": condition in {
                "A2_Known_error_correction_upper_bound",
                "A3_Blind_target_free_estimated_correction",
            },
        }
        for condition in _CONDITIONS
    }
    manifest["calibration_input"] = {
        "path": str(calibration_input),
        "bytes": calibration_input.stat().st_size,
        "sha256": _sha256(calibration_input),
    }
    manifest["source_xml"] = str(source_xml)
    manifest["git"] = _git_snapshot()
    all_simulation_passed = all(item.get("status") == "passed" for item in scene_runs.values())
    all_additive_passed = all(
        isinstance(value, Mapping) and value.get("status") == "passed"
        for value in manifest["additive_audits"].values()
    )
    all_production_passed = all(
        isinstance(record, Mapping) and record.get("status") == "passed"
        for roles in production_records.values()
        for record in roles.values()
    )
    manifest["status"] = (
        "completed"
        if all_simulation_passed and all_additive_passed and all_production_passed and estimated_delay is not None
        else "completed_with_gaps"
    )
    _write_json(case_root / "case_manifest.json", manifest)
    return manifest


def _git_snapshot() -> dict[str, object]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    tracked_status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    untracked_status = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--directory"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    untracked = [
        line.strip()
        for line in untracked_status.splitlines()
        if line.strip()
    ]
    non_output_untracked = [path for path in untracked if not path.startswith("outputs/")]
    return {
        "commit": commit,
        # Formal provenance treats tracked source/config changes as dirty.  A
        # separate untracked inventory is retained because this workspace
        # intentionally keeps large, untracked evidence roots outside commits.
        "dirty": bool(tracked_status.strip()),
        "dirty_tracked": bool(tracked_status.strip()),
        "untracked_count": len(untracked),
        "untracked_output_count": len(untracked) - len(non_output_untracked),
        "untracked_non_output_count": len(non_output_untracked),
        "untracked_non_output_examples": non_output_untracked[:20],
    }


def _artifact_identity(path: Path) -> dict[str, object]:
    candidate = Path(path).resolve()
    if not candidate.is_file():
        return {"path": str(candidate), "exists": False, "sha256": None}
    return {
        "path": str(candidate),
        "exists": True,
        "bytes": candidate.stat().st_size,
        "sha256": _sha256(candidate),
    }


def _build_identity() -> dict[str, object]:
    return {
        "simulate_stage2_statistical": _artifact_identity(SIMULATOR),
        "GMTI_pipe_core": _artifact_identity(PIPE),
    }


def _collect_diagnostic_taps(output_root: Path) -> dict[str, list[str]]:
    patterns = {
        "cfar_geometry": ("*cfar*geometry*.csv", "*cfar*geometry*.json"),
        "track_association_v2": ("track_association_audit_v2.csv",),
        "pipe_payload": ("track_output_payloads.csv", "*payload*audit*.csv"),
    }
    result: dict[str, list[str]] = {}
    for key, suffixes in patterns.items():
        paths: set[str] = set()
        for pattern in suffixes:
            for candidate in output_root.rglob(pattern):
                if candidate.is_file():
                    paths.add(str(candidate.relative_to(output_root)))
        result[key] = sorted(paths)
    return result


def load_stage1_config(path: Path = CONFIG) -> dict[str, object]:
    """Load the single declared Stage-1 configuration document."""

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    if not candidate.is_file():
        raise FileNotFoundError(f"Stage-1 config does not exist: {candidate}")
    return _read_json(candidate.resolve())


def _parse_list_values(
    raw_values: object,
    name: str,
    converter: type[int] | type[float],
    *,
    positive: bool = False,
) -> list[int] | list[float]:
    """Parse repeated and comma-separated CLI values with finite checks."""

    if raw_values is None:
        return []
    if isinstance(raw_values, (str, int, float)):
        items: list[object] = [raw_values]
    elif isinstance(raw_values, Sequence) and not isinstance(raw_values, (bytes, bytearray)):
        items = list(raw_values)
    else:
        raise ValueError(f"{name} must be a comma-separated or repeated list")
    parsed: list[int] | list[float] = []
    for item in items:
        for token in str(item).split(","):
            token = token.strip()
            if not token:
                raise ValueError(f"{name} must not contain an empty value")
            try:
                value = converter(token)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} contains an invalid value: {token!r}") from exc
            if converter is float and not math.isfinite(float(value)):
                raise ValueError(f"{name} must contain only finite values")
            if positive and float(value) <= 0.0:
                raise ValueError(f"{name} must contain only positive values")
            parsed.append(value)
    if not parsed:
        raise ValueError(f"{name} must not be empty")
    # A duplicate override does not add a new scientific case and can make
    # paired counts ambiguous; preserve first occurrence deterministically.
    unique: list[int] | list[float] = []
    for value in parsed:
        if value not in unique:
            unique.append(value)
    return unique


def _config_list(
    section: Mapping[str, object],
    key: str,
    name: str,
    converter: type[int] | type[float],
    *,
    positive: bool = False,
) -> list[int] | list[float]:
    if key not in section:
        raise ValueError(f"Stage-1 config is missing {name}")
    return _parse_list_values(section[key], name, converter, positive=positive)


def _working_point_registry(config: Mapping[str, object]) -> dict[str, dict[str, object]]:
    value = config.get("working_points")
    if not isinstance(value, list) or not value:
        raise ValueError("Stage-1 config working_points must be a non-empty list")
    registry: dict[str, dict[str, object]] = {}
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("each Stage-1 working point must be an object")
        name = str(item.get("name", "")).strip()
        if not name or name in registry:
            raise ValueError(f"working point name is missing or duplicated: {name!r}")
        registry[name] = copy.deepcopy(dict(item))
    return registry


def _selected_working_point_names(
    args: argparse.Namespace,
    mode_config: Mapping[str, object],
    registry: Mapping[str, Mapping[str, object]],
) -> list[str]:
    override = _parse_list_values(
        getattr(args, "working_point", None),
        "--working-point",
        str,
    ) if getattr(args, "working_point", None) is not None else []
    names = [str(value) for value in override] if override else [
        str(value) for value in mode_config.get("working_points", [])
    ]
    if "registered" in names:
        if len(names) != 1:
            raise ValueError("--working-point registered cannot be combined with named points")
        names = [str(value) for value in mode_config.get("working_points", [])]
    if not names:
        raise ValueError("at least one working point must be selected")
    unknown = [name for name in names if name not in registry]
    if unknown:
        raise ValueError(f"unknown working point(s): {', '.join(unknown)}")
    return names


def _stage1_case_blocks(
    config: Mapping[str, object],
    args: argparse.Namespace,
) -> dict[str, object]:
    """Resolve pilot/formal case groups without inventing cross-group cases."""

    mode = str(getattr(args, "mode", "pilot"))
    if mode not in {"pilot", "formal"}:
        raise ValueError("--mode must be pilot or formal")
    registry = _working_point_registry(config)
    mode_config = config.get(mode)
    if not isinstance(mode_config, Mapping):
        raise ValueError(f"Stage-1 config is missing {mode} selection")
    selected_names = _selected_working_point_names(args, mode_config, registry)

    cli_seeds = (
        _parse_list_values(getattr(args, "seeds", None), "--seeds", int)
        if getattr(args, "seeds", None) is not None else []
    )
    cli_velocities = (
        _parse_list_values(
            getattr(args, "target_velocities_mps", None),
            "--target-velocities-mps",
            float,
            positive=True,
        )
        if getattr(args, "target_velocities_mps", None) is not None else []
    )
    cli_snrs = (
        _parse_list_values(getattr(args, "snr_db", None), "--snr-db", float)
        if getattr(args, "snr_db", None) is not None else []
    )
    case_blocks: list[dict[str, object]] = []

    if mode == "formal":
        groups = mode_config.get("case_groups")
        if not isinstance(groups, Mapping):
            raise ValueError("formal.case_groups must be an object")
        for group_name in selected_names:
            group = groups.get(group_name)
            if not isinstance(group, Mapping):
                raise ValueError(f"formal case group is missing: {group_name}")
            point_name = str(group.get("point", group_name))
            if point_name not in registry:
                raise ValueError(f"case group {group_name} refers to unknown point {point_name}")
            configured_seeds = list(_config_list(group, "seeds", f"formal.{group_name}.seeds", int))
            configured_velocities = list(_config_list(
                group,
                "target_velocities_mps",
                f"formal.{group_name}.target_velocities_mps",
                float,
                positive=True,
            ))
            configured_snrs = list(_config_list(group, "target_snr_db", f"formal.{group_name}.target_snr_db", float))
            # Formal command-line values select within the registered group;
            # they must not manufacture combinations absent from the frozen
            # case-group registry.
            seeds = [value for value in configured_seeds if not cli_seeds or value in cli_seeds]
            velocities = [value for value in configured_velocities if not cli_velocities or value in cli_velocities]
            snrs = [value for value in configured_snrs if not cli_snrs or value in cli_snrs]
            if not seeds or not velocities or not snrs:
                raise ValueError(f"formal overrides select zero cases in group {group_name}")
            for seed in seeds:
                for velocity in velocities:
                    for snr in snrs:
                        case_blocks.append({
                            "group": group_name,
                            "working_point": point_name,
                            "seed": int(seed),
                            "target_velocity_mps": float(velocity),
                            "target_snr_db": float(snr),
                            "point": copy.deepcopy(registry[point_name]),
                        })
    else:
        seeds = cli_seeds or list(_config_list(mode_config, "seeds", "pilot.seeds", int))
        velocities = cli_velocities or list(_config_list(
            mode_config,
            "target_velocities_mps",
            "pilot.target_velocities_mps",
            float,
            positive=True,
        ))
        snrs = cli_snrs or list(_config_list(mode_config, "target_snr_db", "pilot.target_snr_db", float))
        for point_name in selected_names:
            for seed in seeds:
                for velocity in velocities:
                    for snr in snrs:
                        case_blocks.append({
                            "group": point_name,
                            "working_point": point_name,
                            "seed": int(seed),
                            "target_velocity_mps": float(velocity),
                            "target_snr_db": float(snr),
                            "point": copy.deepcopy(registry[point_name]),
                        })
    if not case_blocks:
        raise ValueError("selection resolved to zero Stage-1 case blocks")

    cli_delays = (
        _parse_list_values(
            getattr(args, "delay_errors_ns", None),
            "--delay-errors-ns",
            float,
        )
        if getattr(args, "delay_errors_ns", None) is not None else []
    )
    e2e_delays = cli_delays or list(_config_list(
        mode_config,
        "delay_errors_ns",
        f"{mode}.delay_errors_ns",
        float,
    ))
    mc_config = config.get("monte_carlo")
    if not isinstance(mc_config, Mapping):
        raise ValueError("Stage-1 config monte_carlo must be an object")
    if cli_delays:
        mc_delays = list(cli_delays)
    elif mode == "formal":
        mc_delays = list(_config_list(mc_config, "delay_errors_ns", "monte_carlo.delay_errors_ns", float))
    else:
        mc_delays = list(e2e_delays)
    if getattr(args, "snr_db", None) is not None:
        mc_snrs = list(cli_snrs)
    elif mode == "formal":
        mc_snrs = list(_config_list(mc_config, "target_snr_db", "monte_carlo.target_snr_db", float))
    else:
        mc_snrs = []
        for block in case_blocks:
            value = float(block["target_snr_db"])
            if value not in mc_snrs:
                mc_snrs.append(value)
    try:
        mc_trials = int(getattr(args, "mc_trials", None) or mc_config.get("trials"))
    except (TypeError, ValueError) as exc:
        raise ValueError("mc_trials must be an integer") from exc
    if mc_trials < 100:
        raise ValueError("mc_trials must be at least 100 for Stage-1 evidence")
    period_count = int(getattr(args, "period_count", None) or mode_config.get("period_count", 0))
    if period_count <= 0:
        raise ValueError("period_count must be positive")
    return {
        "mode": mode,
        "working_points": selected_names,
        "cases": case_blocks,
        "e2e_delay_errors_ns": [float(value) for value in e2e_delays],
        "level1_mc_delay_errors_ns": [float(value) for value in mc_delays],
        "level1_mc_snr_db": [float(value) for value in mc_snrs],
        "mc_trials": mc_trials,
        "period_count": period_count,
        "mc_seed": int(mc_config.get("seed", 0)),
        "mc_random_seed_namespace": f"channel_delay_stage1/{mode}/seed-{int(mc_config.get('seed', 0))}",
    }


def resolve_stage1_selection(config: Mapping[str, object], args: argparse.Namespace) -> dict[str, object]:
    """Public pure selection helper used by the CLI and contract tests."""

    return _stage1_case_blocks(config, args)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run paired channel-delay Stage-1 estimator and production cases."
    )
    parser.add_argument("--mode", choices=("pilot", "formal"), default="pilot")
    parser.add_argument(
        "--evidence-version",
        choices=("formal-v1", "formal-v2"),
        default="formal-v1",
        help="label the evidence root; formal-v2 requires explicit registered delay arguments",
    )
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    parser.add_argument("--delay-errors-ns", "--delay-errors", dest="delay_errors_ns", action="append")
    parser.add_argument("--seeds", action="append")
    parser.add_argument("--target-velocities-mps", action="append")
    parser.add_argument("--snr-db", action="append")
    parser.add_argument("--working-point", action="append")
    parser.add_argument("--mc-trials", type=int)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--input-mode", choices=("local", "shm"), default="local")
    parser.add_argument("--period-count", type=int)
    parser.add_argument(
        "--skip-cuda",
        action="store_true",
        help="run Level-1 estimator MC and record Level-2 as an explicit gap",
    )
    parser.add_argument(
        "--cleanup-raw",
        action="store_true",
        help="after each completed case, remove generated .bin files and keep audit evidence",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="resume an existing Stage-1 output root, skipping completed case IDs",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        help="process at most this many not-yet-completed cases in this invocation",
    )
    return parser


def _validate_output_root(path: Path, *, resume: bool = False) -> Path:
    root = Path(path).expanduser().resolve()
    if root.exists():
        if not root.is_dir():
            raise ValueError(f"output root is not a directory: {root}")
        if resume:
            manifest_path = root / "manifest.json"
            if not manifest_path.is_file():
                raise ValueError(f"resume output root is missing manifest.json: {root}")
            return root
        if any(root.iterdir()):
            raise ValueError(f"refuse to overwrite non-empty output root: {root}")
    else:
        root.mkdir(parents=True, exist_ok=False)
    return root


def _gpu_status() -> dict[str, object]:
    command = [
        "nvidia-smi",
        "--query-gpu=name,pstate,power.draw,temperature.gpu,utilization.gpu,clocks.sm",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "command": shlex.join(command),
            "returncode": None,
            "error": str(exc),
        }
    return {
        "available": result.returncode == 0 and bool(result.stdout.strip()),
        "command": shlex.join(command),
        "returncode": int(result.returncode),
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _disk_status(path: Path) -> dict[str, object]:
    target = Path(path).resolve()
    probe = target if target.exists() else target.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError as exc:
        return {"path": str(probe), "error": str(exc)}
    return {
        "path": str(probe),
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "free_bytes": int(usage.free),
    }


def _path_is_within(path: Path, root: Path) -> bool:
    """Return whether ``path`` is inside ``root`` without resolving links."""

    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _manifest_hashes(value: object) -> dict[str, str]:
    """Collect already-recorded path/hash pairs from a case manifest."""

    result: dict[str, str] = {}
    if isinstance(value, Mapping):
        path_value = value.get("path")
        sha_value = value.get("sha256")
        if isinstance(path_value, str) and isinstance(sha_value, str):
            result[str(Path(path_value).resolve())] = sha_value
        for item in value.values():
            result.update(_manifest_hashes(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            result.update(_manifest_hashes(item))
    return result


def cleanup_stage1_raw(
    case_root: Path,
    *,
    allow_incomplete: bool = False,
) -> dict[str, object]:
    """Remove only generated ``.bin`` files after evidence capture.

    The production and simulator outputs are large and reproducible.  This
    explicit opt-in cleanup keeps JSON/CSV/XML/log/debug evidence and records
    every removed path.  Existing SHA-256 values from the case manifest are
    carried into the cleanup ledger; files not previously hashed are marked as
    derived intermediate outputs instead of being silently presented as
    hashed inputs.  Symlinks are removed only when their target stays inside
    this exact case root.
    """

    root = Path(case_root).expanduser().resolve()
    manifest_path = root / "case_manifest.json"
    if not root.is_dir():
        raise ValueError(f"raw cleanup requires a case root directory: {root}")
    if manifest_path.is_file():
        manifest = _read_json(manifest_path)
    elif allow_incomplete and (root / "scenes" / "scenes_manifest.json").is_file():
        manifest = {
            "status": "orphaned",
            "reason": "case_manifest_missing_after_interrupted_execution",
        }
    else:
        raise ValueError(f"raw cleanup requires a case manifest or scenes manifest: {root}")
    case_status = str(manifest.get("status"))
    allowed_statuses = {"completed"}
    if allow_incomplete:
        allowed_statuses.update({"completed_with_gaps", "orphaned"})
    if case_status not in allowed_statuses:
        raise ValueError(
            f"raw cleanup requires case status in {sorted(allowed_statuses)}, "
            f"got {manifest.get('status')!r}: {root}"
        )
    hashes = _manifest_hashes(manifest)
    candidates = sorted(root.rglob("*.bin"))
    records: list[dict[str, object]] = []
    removed_bytes = 0
    removed_files = 0
    retained_external_symlinks = 0
    for path in candidates:
        relative = path.relative_to(root)
        absolute = path.resolve()
        if path.is_symlink():
            if not _path_is_within(absolute, root):
                retained_external_symlinks += 1
                records.append({
                    "path": str(relative),
                    "kind": "symlink",
                    "status": "retained_external_target",
                    "target": os.readlink(path),
                })
                continue
            target = os.readlink(path)
            path.unlink()
            removed_files += 1
            records.append({
                "path": str(relative),
                "kind": "symlink",
                "status": "removed",
                "target": target,
            })
            continue
        if not path.is_file() or not _path_is_within(absolute, root):
            records.append({
                "path": str(relative),
                "kind": "file",
                "status": "retained_outside_case_root",
            })
            continue
        size = int(path.stat().st_size)
        record: dict[str, object] = {
            "path": str(relative),
            "kind": "generated_binary",
            "bytes": size,
            "sha256": hashes.get(str(absolute)),
            "sha256_status": (
                "recorded_in_case_manifest"
                if str(absolute) in hashes
                else "not_recorded_before_cleanup_derived_intermediate"
            ),
        }
        path.unlink()
        removed_files += 1
        removed_bytes += size
        record["status"] = "removed"
        records.append(record)
    report = {
        "schema_version": 1,
        "case_root": str(root),
        "status": case_status,
        "policy": "remove_generated_bin_only_keep_compact_audit_files",
        "allow_incomplete": bool(allow_incomplete),
        "removed_file_count": removed_files,
        "removed_bytes": removed_bytes,
        "retained_external_symlink_count": retained_external_symlinks,
        "records": records,
        "remaining_bin_count": sum(1 for path in root.rglob("*.bin") if path.is_file()),
    }
    _write_json(root / "raw_cleanup.json", report)
    return report


def _number_token(value: float) -> str:
    sign = "m" if float(value) < 0.0 else "p"
    magnitude = abs(float(value))
    return sign + (f"{magnitude:.6f}".rstrip("0").rstrip(".")).replace(".", "d")


def _case_slug(block: Mapping[str, object], delay_error_ns: float) -> str:
    raw = (
        f"{block['working_point']}__seed_{int(block['seed'])}"
        f"__v_{_number_token(float(block['target_velocity_mps']))}"
        f"__snr_{_number_token(float(block['target_snr_db']))}"
        f"__delay_{_number_token(delay_error_ns)}ns"
    )
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)


def _scene_identity_for_block(
    block: Mapping[str, object],
    delay_error_ns: float,
    period_count: int,
) -> dict[str, object]:
    point = block.get("point")
    if not isinstance(point, Mapping):
        raise ValueError("resolved case block is missing working point metadata")
    return {
        "case_id": _case_slug(block, delay_error_ns),
        "working_point": str(block["working_point"]),
        "group": str(block.get("group", block["working_point"])),
        "seed": int(block["seed"]),
        "period_count": int(period_count),
        "beam_id": int(point["beam_id"]),
        "expected_bin": int(point["expected_bin"]),
        "range_m": float(point["range_m"]),
        "target_velocity_mps": float(block["target_velocity_mps"]),
        "target_snr_db": float(block["target_snr_db"]),
        "texture_sigma": float(point["texture_sigma"]),
        "clutter_rho": float(point["clutter_rho"]),
        "target": {"target_id": "stage1_target_1", "enabled": True},
        "clutter": {
            "texture_sigma": float(point["texture_sigma"]),
            "temporal_correlation_rho": float(point["clutter_rho"]),
        },
        "delay_error_ns": float(delay_error_ns),
    }


def _case_summary(case_manifest: Mapping[str, object], case_root: Path) -> dict[str, object]:
    return {
        "case_root": str(case_root),
        "case_manifest": str(case_root / "case_manifest.json"),
        "status": case_manifest.get("status", "unknown"),
        "reason": case_manifest.get("reason"),
        "scene_identity": case_manifest.get("scene_identity"),
        "delay_estimator": case_manifest.get("delay_estimator"),
        "additive_audits": case_manifest.get("additive_audits"),
        "raw_cleanup": case_manifest.get("raw_cleanup"),
        "conditions_present": sorted(
            str(key) for key in case_manifest.get("conditions", {})
        ) if isinstance(case_manifest.get("conditions"), Mapping) else [],
    }


def run_stage1_cli(
    args: argparse.Namespace,
    *,
    command_args: Sequence[str] | None = None,
) -> dict[str, object]:
    """Run Level-1 MC and selected Level-2 cases, always leaving a manifest."""

    config_path = Path(args.config).expanduser().resolve()
    template_path = Path(args.template).expanduser().resolve()
    config = load_stage1_config(config_path)
    if not template_path.is_file():
        raise FileNotFoundError(f"scenario template does not exist: {template_path}")
    selection = resolve_stage1_selection(config, args)
    resume = bool(getattr(args, "resume", False))
    evidence_version = str(getattr(args, "evidence_version", "formal-v1"))
    max_cases_value = getattr(args, "max_cases", None)
    if max_cases_value is not None and int(max_cases_value) <= 0:
        raise ValueError("--max-cases must be positive")
    command_values = list(command_args) if command_args is not None else list(sys.argv[1:])
    if evidence_version == "formal-v2":
        if str(args.mode) != "formal":
            raise ValueError("formal-v2 requires --mode formal")
        if not any(
            token == "--delay-errors-ns" or token.startswith("--delay-errors-ns=")
            or token == "--delay-errors" or token.startswith("--delay-errors=")
            for token in (str(value) for value in command_values)
        ):
            raise ValueError("formal-v2 requires an explicit --delay-errors-ns sweep")
        working_point_values = getattr(args, "working_point", None) or []
        if not any(
            token == "--working-point" or token.startswith("--working-point=")
            for token in (str(value) for value in command_values)
        ) or "registered" not in [str(value) for value in working_point_values]:
            raise ValueError("formal-v2 requires the registered working-point blocks")
    output_root = _validate_output_root(Path(args.output_root), resume=resume)
    protected_v1_roots = {
        (ROOT / "outputs/formal_delay_stage1_20260916").resolve(),
        (ROOT / "outputs/formal_evidence/stage1_delay").resolve(),
    }
    if evidence_version == "formal-v2" and output_root in protected_v1_roots:
        raise ValueError("formal-v2 output root must be independent of immutable Formal-v1")
    gpu_before = _gpu_status()
    disk_before = _disk_status(output_root)
    config_for_case = copy.deepcopy(config)
    config_for_case["scenario_template"] = str(template_path)
    full_command = [sys.executable, str(Path(__file__).resolve()), *command_values]
    config_sha256 = _sha256(config_path)
    template_sha256 = _sha256(template_path)
    git_before = _git_snapshot()
    build_identity = _build_identity()
    input_hashes = {
        "config": config_sha256,
        "template": template_sha256,
        "simulator": build_identity["simulate_stage2_statistical"],
        "pipe": build_identity["GMTI_pipe_core"],
    }
    if resume:
        manifest = _read_json(output_root / "manifest.json")
        if manifest.get("evidence_version") != evidence_version:
            raise ValueError("resume evidence version does not match the existing run manifest")
        if manifest.get("mode") != selection["mode"]:
            raise ValueError("resume mode does not match the existing run manifest")
        if manifest.get("resolved") != selection:
            raise ValueError("resume selection does not match the existing run manifest")
        existing_config = manifest.get("config")
        existing_template = manifest.get("template")
        if not isinstance(existing_config, Mapping) or existing_config.get("sha256") != config_sha256:
            raise ValueError("resume config hash does not match the existing run manifest")
        if not isinstance(existing_template, Mapping) or existing_template.get("sha256") != template_sha256:
            raise ValueError("resume template hash does not match the existing run manifest")
        if not isinstance(manifest.get("cases"), list):
            raise ValueError("resume manifest cases must be a list")
        history = manifest.get("resume_history")
        if not isinstance(history, list):
            history = []
        history.append({
            "command": {
                "argv": full_command,
                "shell": shlex.join(str(item) for item in full_command),
            },
            "resumed_at_utc": datetime.now(timezone.utc).isoformat(),
            "gpu_status": gpu_before,
            "disk_status": disk_before,
        })
        manifest["resume_history"] = history
        manifest["attempt"] = int(manifest.get("attempt", 1)) + 1
        manifest["resume"] = True
        manifest["source_commit_at_resume"] = git_before.get("commit")
        manifest["source_dirty_at_resume"] = bool(git_before.get("dirty"))
        manifest["raw_cleanup_requested"] = bool(
            manifest.get("raw_cleanup_requested", False)
            or bool(getattr(args, "cleanup_raw", False))
        )
    else:
        manifest = {
            "schema_version": 1,
            "evidence_version": evidence_version,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": selection["mode"],
            "command": {
                "argv": full_command,
                "shell": shlex.join(str(item) for item in full_command),
            },
            "config": {
                "path": str(config_path),
                "sha256": config_sha256,
            },
            "template": {
                "path": str(template_path),
                "sha256": template_sha256,
            },
            "resolved": selection,
            "delay_range_labels": copy.deepcopy(config.get("delay_ranges_ns")),
            "git": git_before,
            "source_commit_before": git_before.get("commit"),
            "source_dirty_before": bool(git_before.get("dirty")),
            "source_dirty_tracked_before": bool(git_before.get("dirty_tracked", git_before.get("dirty"))),
            "gpu_status_before": gpu_before,
            "disk_status_before": disk_before,
            "build_identity": build_identity,
            "input_hashes": input_hashes,
            "attempt": 1,
            "resume": False,
            "ai_training": False,
            "router_enabled": False,
            "native_four_channel_stap": False,
            "diagnostic_taps": _collect_diagnostic_taps(output_root),
            "output_roots": {
                "formal_v1": str(ROOT / "outputs/formal_evidence/stage1_delay"),
                "formal_v2": str(output_root),
            },
            "raw_cleanup_requested": bool(getattr(args, "cleanup_raw", False)),
            "mc_summary": {"status": "not_started"},
            "cases": [],
            "status": "not_started",
        }
    if not (output_root / "config_snapshot.json").is_file():
        _write_json(output_root / "config_snapshot.json", config)
    if not (output_root / "template_snapshot.json").is_file():
        _write_json(output_root / "template_snapshot.json", _read_json(template_path))
    _write_json(output_root / "manifest.json", manifest)
    expected_case_count = len(selection["cases"]) * len(selection["e2e_delay_errors_ns"])

    def checkpoint(last_case_id: str | None = None) -> None:
        completed_count = sum(
            1 for item in manifest["cases"]
            if isinstance(item, Mapping) and item.get("status") == "completed"
        )
        manifest["progress"] = {
            "completed_case_count": completed_count,
            "processed_case_count": len(manifest["cases"]),
            "expected_case_count": expected_case_count,
            "last_case_id": last_case_id,
            "status": "running",
        }
        _write_json(output_root / "manifest.json", manifest)

    case_records = manifest["cases"]
    assert isinstance(case_records, list)
    completed_case_ids = {
        str(item.get("scene_identity", {}).get("case_id"))
        for item in case_records
        if isinstance(item, Mapping)
        and item.get("status") == "completed"
        and isinstance(item.get("scene_identity"), Mapping)
    }
    interrupted_cases = manifest.get("interrupted_cases")
    if not isinstance(interrupted_cases, list):
        interrupted_cases = []
    manifest["interrupted_cases"] = interrupted_cases
    processed_this_invocation = 0

    mc_ok = False
    try:
        from scripts.delay_stage1_core import STAGE1_METHOD_SUITE, run_parameter_monte_carlo

        mc_config = config["monte_carlo"]
        if not isinstance(mc_config, Mapping):
            raise ValueError("Stage-1 config monte_carlo must be an object")
        mc_rows = run_parameter_monte_carlo(
            selection["level1_mc_delay_errors_ns"],
            selection["level1_mc_snr_db"],
            int(selection["mc_trials"]),
            int(selection["mc_seed"]),
            float(mc_config["fs_hz"]),
            float(mc_config["bandwidth_hz"]),
            int(mc_config["pulse_samples"]),
        )
        for row in mc_rows:
            row["random_seed_namespace"] = selection["mc_random_seed_namespace"]
            row["sampling_rate_hz"] = float(mc_config["fs_hz"])
            row["bandwidth_hz"] = float(mc_config["bandwidth_hz"])
            row["pulse_samples"] = int(mc_config["pulse_samples"])
        mc_path = output_root / "monte_carlo" / "estimator_summary.csv"
        _write_rows(mc_path, mc_rows)
        expected_mc_rows = (
            len(selection["level1_mc_delay_errors_ns"])
            * len(selection["level1_mc_snr_db"])
            * len(STAGE1_METHOD_SUITE)
        )
        mc_ok = len(mc_rows) == expected_mc_rows
        manifest["mc_summary"] = {
            "status": "passed" if mc_ok else "incomplete",
            "path": str(mc_path),
            "row_count": len(mc_rows),
            "expected_row_count": expected_mc_rows,
            "trials_per_delay_snr_method": int(selection["mc_trials"]),
            "methods": sorted({str(row.get("method")) for row in mc_rows}),
            "random_seed_namespace": selection["mc_random_seed_namespace"],
            "rows": mc_rows,
        }
        _write_json(output_root / "manifest.json", manifest)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        manifest["mc_summary"] = {
            "status": "failed",
            "error": str(exc),
        }

    if bool(args.skip_cuda):
        for block in selection["cases"]:
            for delay in selection["e2e_delay_errors_ns"]:
                if max_cases_value is not None and processed_this_invocation >= int(max_cases_value):
                    break
                manifest["cases"].append({
                    "status": "SKIPPED",
                    "reason": "cuda_execution_skipped_by_cli",
                    "scene_identity": _scene_identity_for_block(
                        block,
                        float(delay),
                        int(selection["period_count"]),
                    ),
                })
                checkpoint(
                    str(manifest["cases"][-1]["scene_identity"]["case_id"])
                    if isinstance(manifest["cases"][-1], Mapping)
                    and isinstance(manifest["cases"][-1].get("scene_identity"), Mapping)
                    else None
                )
                processed_this_invocation += 1
            if max_cases_value is not None and processed_this_invocation >= int(max_cases_value):
                break
    else:
        for block in selection["cases"]:
            for delay in selection["e2e_delay_errors_ns"]:
                if max_cases_value is not None and processed_this_invocation >= int(max_cases_value):
                    break
                scene_identity = _scene_identity_for_block(
                    block,
                    float(delay),
                    int(selection["period_count"]),
                )
                case_root = output_root / "cases" / str(scene_identity["case_id"])
                case_id = str(scene_identity["case_id"])
                if case_id in completed_case_ids:
                    continue
                if case_root.exists() and any(case_root.iterdir()):
                    cleanup_record: dict[str, object]
                    try:
                        cleanup_record = cleanup_stage1_raw(case_root, allow_incomplete=True)
                    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as cleanup_exc:
                        cleanup_record = {
                            "status": "failed",
                            "error": str(cleanup_exc),
                        }
                    interrupted_root = output_root / "interrupted_cases" / (
                        f"{case_id}__{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
                    )
                    interrupted_root.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(case_root), str(interrupted_root))
                    interrupted_cases.append({
                        "case_id": case_id,
                        "original_root": str(case_root),
                        "preserved_root": str(interrupted_root),
                        "cleanup": cleanup_record,
                    })
                try:
                    case_manifest = run_stage1_case(
                        config_for_case,
                        scene_identity,
                        case_root,
                        str(args.input_mode),
                    )
                    if bool(getattr(args, "cleanup_raw", False)) and case_manifest.get("status") == "completed":
                        try:
                            case_manifest["raw_cleanup"] = cleanup_stage1_raw(case_root)
                        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as cleanup_exc:
                            case_manifest["raw_cleanup"] = {
                                "status": "failed",
                                "error": str(cleanup_exc),
                            }
                        _write_json(case_root / "case_manifest.json", case_manifest)
                    manifest["cases"].append(_case_summary(case_manifest, case_root))
                except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                    case_root.mkdir(parents=True, exist_ok=True)
                    failure = {
                        "schema_version": 1,
                        "status": "failed",
                        "reason": str(exc),
                        "scene_identity": scene_identity,
                        "truth_used_in_estimator": False,
                        "ai_training": False,
                        "router_enabled": False,
                        "native_four_channel_stap": False,
                    }
                    _write_json(case_root / "case_manifest.json", failure)
                    manifest["cases"].append(_case_summary(failure, case_root))
                checkpoint(str(scene_identity["case_id"]))
                processed_this_invocation += 1
            if max_cases_value is not None and processed_this_invocation >= int(max_cases_value):
                break
    case_records = manifest["cases"]
    assert isinstance(case_records, list)
    # A bounded ``--max-cases`` invocation can have only completed records
    # while the registered matrix still has unprocessed cases.  Completion
    # must therefore be judged against the resolved expected count, not just
    # the records present in this checkpoint.
    all_cases_complete = (
        len(case_records) == expected_case_count
        and bool(case_records)
        and all(
            item.get("status") == "completed"
            for item in case_records
            if isinstance(item, Mapping)
        )
    )
    if not mc_ok:
        manifest["status"] = "completed_with_gaps"
    elif bool(args.skip_cuda):
        manifest["status"] = "completed_with_gaps"
    elif all_cases_complete:
        manifest["status"] = "completed"
    elif max_cases_value is not None:
        manifest["status"] = "in_progress"
    else:
        manifest["status"] = "completed_with_gaps"
    manifest["progress"] = {
        "completed_case_count": sum(
            1 for item in case_records
            if isinstance(item, Mapping) and item.get("status") == "completed"
        ),
        "processed_case_count": len(case_records),
        "expected_case_count": expected_case_count,
        "last_case_id": (
            case_records[-1].get("scene_identity", {}).get("case_id")
            if case_records
            and isinstance(case_records[-1], Mapping)
            and isinstance(case_records[-1].get("scene_identity"), Mapping)
            else None
        ),
        "processed_this_invocation": processed_this_invocation,
        "status": manifest["status"],
    }
    manifest["git_after"] = _git_snapshot()
    manifest["source_commit_after"] = manifest["git_after"].get("commit")
    manifest["source_dirty_after"] = bool(manifest["git_after"].get("dirty"))
    manifest["source_dirty_tracked_after"] = bool(
        manifest["git_after"].get("dirty_tracked", manifest["git_after"].get("dirty"))
    )
    manifest["gpu_status_after"] = _gpu_status()
    manifest["build_identity_after"] = _build_identity()
    manifest["diagnostic_taps"] = _collect_diagnostic_taps(output_root)
    manifest["disk_status_after"] = _disk_status(output_root)
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        manifest = run_stage1_cli(args, command_args=argv)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "status": manifest["status"],
        "output_root": str(Path(args.output_root).expanduser().resolve()),
        "case_count": len(manifest.get("cases", [])),
        "mc_status": manifest.get("mc_summary", {}).get("status")
        if isinstance(manifest.get("mc_summary"), Mapping) else None,
    }, ensure_ascii=False))
    return 0 if manifest["status"] in {"completed", "completed_with_gaps", "in_progress"} else 1


__all__ = [
    "audit_additive_triplet",
    "build_scene_variants",
    "build_stage1_branch_contract",
    "evaluate_target_off_waterfall",
    "build_arg_parser",
    "cleanup_stage1_raw",
    "load_stage1_config",
    "prepare_condition_inputs",
    "resolve_stage1_selection",
    "run_stage1_cli",
    "run_production_branch",
    "run_stage1_case",
]


if __name__ == "__main__":
    raise SystemExit(main())
