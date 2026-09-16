#!/usr/bin/env python3
"""Paired scene and production orchestration helpers for delay Stage-1.

The first part of this module is deliberately side-effect-light.  It creates
scenario documents and prepares physical correction inputs, while the later
Stage-1 tasks add simulator/production execution and compact aggregation.
All scientific estimator calls operate on fused F1/F2 data; raw four-channel
IQ is retained only as the production input representation and audit source.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from scripts.estimate_channel_delay import rewrite_float32_protocol_delay


ROOT = Path(__file__).resolve().parents[1]
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
        "background_input_dir": str(background_dir.resolve()),
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


def _layer(status: str, *, value: object = None, reason: str | None = None, **extra: object) -> dict[str, object]:
    result: dict[str, object] = {"status": status, "value": value}
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
            hit_cell_count=hit_cells,
            valid_cut_count=valid_cut,
            metric_name="empirical_structured_clutter_false_hit_fraction",
        )
    else:
        cell = _layer(
            "NOT_EVALUABLE",
            reason="valid_cut_count_missing_or_non_positive",
            hit_cell_count=max(0, hit_cells),
            valid_cut_count=max(0, valid_cut),
            metric_name="empirical_structured_clutter_false_hit_fraction",
        )

    cluster_ids = cfar_summary.get("cluster_ids")
    if isinstance(cluster_ids, Sequence) and not isinstance(cluster_ids, (str, bytes)):
        unique_ids = {str(value) for value in cluster_ids if str(value).strip()}
        clusters = _layer("passed", value=len(unique_ids), cluster_ids=sorted(unique_ids))
    else:
        try:
            count = int(cfar_summary.get("clusters"))
        except (TypeError, ValueError):
            count = -1
        clusters = (
            _layer("passed", value=count, source="cfar_summary.clusters")
            if count >= 0
            else _layer("NOT_EVALUABLE", reason="explicit_cluster_count_missing")
        )

    result_path = Path(result_dir)
    detection_files = sorted(result_path.glob("detection_results_GMTI*.csv"))
    if detection_files:
        detection_count = sum(len(_csv_rows(path)) for path in detection_files)
        protocol = _layer(
            "passed",
            value=detection_count,
            source_paths=[str(path) for path in detection_files],
            definition="all production OFF detection rows; no truth match is possible",
        )
    else:
        protocol = _layer("NOT_EVALUABLE", reason="production_detection_csv_missing")

    track_ids, track_source, track_reason = _false_track_ids(Path(debug_dir) if debug_dir is not None else None)
    tracks = (
        _layer("passed", value=len(track_ids), unique_track_ids=sorted(track_ids), source_path=str(track_source))
        if track_reason is None
        else _layer("NOT_EVALUABLE", reason=track_reason, source_path=str(track_source) if track_source else None)
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


__all__ = [
    "audit_additive_triplet",
    "build_scene_variants",
    "build_stage1_branch_contract",
    "evaluate_target_off_waterfall",
    "prepare_condition_inputs",
]
