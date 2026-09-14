#!/usr/bin/env python3
"""Run the moving-target servo end-to-end pilot.

The estimator under test is S1 and receives only OFF=C+N plus reported
context.  ON=S+C+N and TO=S are generated as paired evaluation roles.  S0,
S1, S1K and Sassist are kept as separate branch identities; Sassist is a
target-assisted reference and is never reported as clutter-only performance.

The default matrix can be run with ``--skip-core`` to audit information flow
and estimator stability without producing a large production result tree.
Production CUDA/CFAR evaluation is opt-in and uses the existing GMTI_core
entry point and P4 matcher; no proxy detector or tracker is introduced.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import platform
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import gmti_eval_match as eval_match  # noqa: E402
import run_clutter_only_servo_pilot as clutter_pilot  # noqa: E402
import run_p4_truth_eval as p4_eval  # noqa: E402
import run_unknown_system_error_end_to_end as generic_e2e  # noqa: E402
import run_unknown_system_error_servo_pilot as target_assisted  # noqa: E402


TEMPLATE = clutter_pilot.TEMPLATE
SIMULATOR = clutter_pilot.SIMULATOR
GMTI_CORE = clutter_pilot.GMTI_CORE
MATCH_CONFIG = ROOT / "configs/eval/match_config.json"
HEADER_BYTES = clutter_pilot.HEADER_BYTES
DEFAULT_ERRORS_DEG = (0.20, -0.20)
DEFAULT_SEEDS = (101, 202, 303)
DEFAULT_TEXTURES = ("low_texture", "high_texture")
DEFAULT_RANGES_M = (7500.0, 10000.0)
BRANCHES = ("S0", "S1", "S1K", "Sassist")
ROLES = ("OFF", "ON", "TO")


def scene_contract() -> dict[str, dict[str, object]]:
    """Return the fixed scene classes and their true target states."""

    return {
        "target_free": {
            "target_enabled": False,
            "target_velocity_class": "none",
            "ve_mps": 0.0,
            "vn_mps": 0.0,
            "evaluation_scope": "target_free_calibration_only",
        },
        "slow_near_ridge": {
            "target_enabled": True,
            "target_velocity_class": "slow_near_clutter_ridge",
            "ve_mps": 0.0,
            "vn_mps": 59.0,
            "evaluation_scope": "moving_target",
        },
        "medium": {
            "target_enabled": True,
            "target_velocity_class": "medium_relative_radial_velocity",
            "ve_mps": 0.0,
            "vn_mps": 45.0,
            "evaluation_scope": "moving_target",
        },
        "fast": {
            "target_enabled": True,
            "target_velocity_class": "fast_relative_radial_velocity",
            "ve_mps": 0.0,
            "vn_mps": 15.0,
            "evaluation_scope": "moving_target",
        },
    }


def role_contract() -> dict[str, dict[str, object]]:
    """Describe the causal role of OFF, ON and TO files."""

    return {
        "OFF": {
            "definition": "C+N",
            "use": "calibration_estimator_input",
            "target_bearing": False,
            "target_only": False,
        },
        "ON": {
            "definition": "S+C+N",
            "use": "target_bearing_evaluation_only",
            "target_bearing": True,
            "target_only": False,
        },
        "TO": {
            "definition": "S",
            "use": "causal_target_transfer_evaluation_only",
            "target_bearing": False,
            "target_only": True,
        },
    }


def production_cfar_signature(settings: Mapping[str, object]) -> str:
    """Return a stable signature for the common Current/CFAR parameter block."""

    if not isinstance(settings, Mapping):
        raise ValueError("production Current/CFAR settings must be an object")
    return json.dumps(_json_safe(settings), sort_keys=True, separators=(",", ":"))


def estimator_input_audit(
    branch: str,
    input_paths: Sequence[str | Path],
    contains_target_truth: bool,
    contains_nominal_target: bool,
    contains_known_servo_error: bool,
    contains_servo_truth: bool,
) -> dict[str, object]:
    """Reject forbidden information before it reaches the S1 estimator."""

    if branch not in BRANCHES:
        raise ValueError(f"unsupported E2E branch: {branch}")
    normalized = [str(path).replace("\\", "/") for path in input_paths]
    contains_on_or_to = any(
        "/on/" in path or path.endswith("/on") or "/to/" in path or path.endswith("/to")
        for path in normalized
    )
    forbidden = (
        contains_target_truth,
        contains_nominal_target,
        contains_known_servo_error,
        contains_servo_truth,
    )
    if branch == "S1":
        if contains_on_or_to:
            raise ValueError("S1 estimator cannot receive ON or TO packets")
        if not normalized or any("/off/" not in path and not path.startswith("off/") for path in normalized):
            raise ValueError("S1 estimator requires OFF=C+N input only")
        if any(forbidden):
            raise ValueError("S1 estimator received target/truth/known-error metadata")
    return {
        "branch": branch,
        "input_paths": [str(path) for path in input_paths],
        "off_only": branch == "S1" and not contains_on_or_to and not any(forbidden),
        "contains_on_or_to": contains_on_or_to,
        "contains_target_truth": bool(contains_target_truth),
        "contains_nominal_target": bool(contains_nominal_target),
        "contains_known_servo_error": bool(contains_known_servo_error),
        "contains_servo_truth": bool(contains_servo_truth),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.integer, int)):
        return int(value)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    command = ["git", *args]
    if not (ROOT / ".git").exists() and (ROOT / ".git-real").is_dir():
        command = ["git", "--git-dir=.git-real", "--work-tree=.", *args]
    result = subprocess.run(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


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


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _run_logged(command: Sequence[str], log_path: Path) -> tuple[int, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(str(item) for item in command) + "\n")
        log.flush()
        completed = subprocess.run(
            [str(item) for item in command],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log.write(f"exit_code={completed.returncode}\n")
    return int(completed.returncode), time.perf_counter() - started


def _parse_float_list(values: Sequence[str], name: str) -> list[float]:
    result: list[float] = []
    for raw in values:
        for item in raw.split(","):
            if item.strip():
                result.append(float(item))
    if not result or any(not math.isfinite(value) for value in result):
        raise ValueError(f"{name} must contain finite values")
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


def _prepare_template(template: Mapping[str, object], compact_input: bool) -> dict[str, object]:
    config = target_assisted._prepare_template(template)
    config["truth_output"] = True
    if compact_input:
        waveform = config.get("waveform")
        processing = config.get("range_processing")
        if not isinstance(waveform, dict) or not isinstance(processing, dict):
            raise ValueError("prepared waveform/range_processing must be objects")
        waveform.update({"pulse_len": 4096, "pulse_num": 8})
        processing.update({"range_fft_len": 4096, "range_crop_len": 4096})
    return config


def _role_config(
    template: Mapping[str, object],
    case_root: Path,
    role: str,
    scene_name: str,
    scene: Mapping[str, object],
    error_deg: float,
    seed: int,
    texture: str,
    range_center_m: float,
) -> dict[str, object]:
    if role not in ROLES:
        raise ValueError(f"unsupported role: {role}")
    if texture not in clutter_pilot.TEXTURE_CONFIGS:
        raise ValueError(f"unsupported texture: {texture}")
    config = copy.deepcopy(template)
    case_id = (
        f"servo_gmti_{scene_name}_{texture}_{range_center_m:.0f}m_"
        f"{error_deg:+.3f}deg_seed_{seed}"
    ).replace("+", "p").replace("-", "m").replace(".", "p")
    role_root = case_root / role.lower()
    config["case_id"] = f"{case_id}_{role.lower()}"
    config["output_dir"] = str(role_root.resolve())
    config["paired_background_output_dir"] = (
        str((case_root / "off").resolve()) if role == "ON" else ""
    )
    random_cfg = config.get("random")
    if not isinstance(random_cfg, dict):
        raise ValueError("prepared random config is not an object")
    random_cfg["random_seed"] = int(seed)
    scene_cfg = config.get("scene")
    if not isinstance(scene_cfg, dict):
        raise ValueError("prepared scene is not an object")
    scene_cfg["range_min_m"] = float(range_center_m) - 500.0
    scene_cfg["range_max_m"] = float(range_center_m) + 500.0
    scene_cfg["signal_only"] = role == "TO"
    area = scene_cfg.get("area_clutter")
    if not isinstance(area, dict):
        raise ValueError("prepared area_clutter is not an object")
    area.update(clutter_pilot.TEXTURE_CONFIGS[texture])
    targets = config.get("targets")
    if not isinstance(targets, list) or not targets or not isinstance(targets[0], dict):
        raise ValueError("prepared target list is invalid")
    target = targets[0]
    target["target_id"] = f"SERVO_GMTI_{scene_name.upper()}"
    target["enabled"] = bool(scene["target_enabled"])
    init = target.get("init")
    if not isinstance(init, dict):
        raise ValueError("prepared target init is invalid")
    init["beam_id"] = 3
    init["expected_bin"] = clutter_pilot._range_sample(range_center_m, config)
    init["azimuth_offset_deg"] = 0.0
    if bool(scene["target_enabled"]):
        target["motion"] = {
            "type": "enu_velocity",
            "ve_mps": float(scene["ve_mps"]),
            "vn_mps": float(scene["vn_mps"]),
        }
    else:
        target["motion"] = {"type": "static"}
    target["amplitude"] = {"type": "snr_db", "snr_db": 30.0}
    target["visibility"] = {"type": "gaussian", "single_beam_only": False, "visible_beam_span": -1}
    config["servo_angle_error"] = {
        "enabled": True,
        "true_minus_reported_deg": float(error_deg),
    }
    return config


def _find_period_file(root: Path) -> Path:
    return target_assisted._find_period_file(root)


def _target_truth(root: Path) -> list[dict[str, str]]:
    for name in ("target_truth_beam_summary.csv", "truth_targets_by_beam.csv"):
        rows = _read_csv(root / "truth" / name)
        if rows:
            return p4_eval.visible_truths(rows)
    return []


def _estimate_row(
    case: Mapping[str, object],
    branch: str,
    result: Mapping[str, object],
    audit: Mapping[str, object],
) -> dict[str, object]:
    if branch == "S1":
        estimate = result.get("estimate_deg")
        status = result.get("status")
        method = result.get("method")
        uncertainty = result.get("uncertainty_deg")
        phase_rmse = result.get("phase_rmse")
        ridge_rmse = result.get("ridge_rmse")
        fit_rmse = result.get("fit_rmse")
        mismatch = result.get("model_mismatch")
        fallback = result.get("fallback_reason")
        sources = result.get("causal_data_sources")
    else:
        estimate = result.get("estimated_true_minus_reported_deg")
        status = "VALID" if result.get("fit_status") == "fit" else result.get("status")
        method = result.get("method", target_assisted.SERVO_ESTIMATOR_NAME)
        uncertainty = result.get("uncertainty_deg")
        phase_rmse = result.get("mean_phase_residual_rms_rad")
        ridge_rmse = None
        fit_rmse = result.get("fit_rmse")
        mismatch = result.get("model_mismatch")
        fallback = result.get("fallback_reason")
        sources = result.get("causal_data_sources", target_assisted.SERVO_CAUSAL_DATA_SOURCES)
    return {
        "case_id": case["case_id"],
        "scene_class": case["scene_class"],
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
        "clutter_cancellation_db": None,
        "model_mismatch": mismatch,
        "fallback_reason": fallback,
        "status": status,
        "causal_data_sources": sources,
        "input_role": "OFF_C_PLUS_N_TARGET_FREE_ONLY" if branch == "S1" else "PAIRED_ON_MINUS_OFF_TARGET_RESIDUAL",
        "target_truth_used": audit.get("contains_target_truth", False),
        "nominal_target_used": audit.get("contains_nominal_target", False),
        "known_servo_error_used": audit.get("contains_known_servo_error", False),
        "servo_truth_used": audit.get("contains_servo_truth", False),
        "source_ids": result.get("source_ids") or audit.get("input_paths", []),
    }


def _not_applicable_assisted_row(case: Mapping[str, object]) -> dict[str, object]:
    return _estimate_row(
        case,
        "Sassist",
        {
            "status": "NOT_APPLICABLE_TARGET_FREE",
            "method": target_assisted.SERVO_ESTIMATOR_NAME,
            "causal_data_sources": list(target_assisted.SERVO_CAUSAL_DATA_SOURCES),
        },
        {
            "contains_target_truth": False,
            "contains_nominal_target": False,
            "contains_known_servo_error": False,
            "contains_servo_truth": False,
            "input_paths": [],
        },
    )


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
        decision = "KEEP_CURRENT"
        applied = 0.0
        source = "reported_header_current"
        status = "CURRENT"
        audit = estimator_input_audit("S0", [case["on_raw"]], False, False, False, False)
        estimate = uncertainty = None
        mismatch = False
        fallback = None
    elif branch == "S1K":
        decision = "NO_CORRECTION_NEEDED" if abs(error) < 1.0e-12 else "APPLY_KNOWN_ERROR_CORRECTION"
        applied = error
        source = "known_error_upper_bound"
        status = "KNOWN_EVALUATION"
        audit = estimator_input_audit("S1K", [case["on_raw"]], False, False, True, False)
        estimate = error
        uncertainty = 0.0
        mismatch = False
        fallback = None
    else:
        if estimate_row is None:
            raise RuntimeError(f"missing estimator row for {branch}")
        estimate = estimate_row.get("estimate_deg")
        uncertainty = estimate_row.get("uncertainty_deg")
        status = str(estimate_row.get("status"))
        mismatch = bool(estimate_row.get("model_mismatch", False))
        fallback = estimate_row.get("fallback_reason")
        if status not in {"VALID", "fit"} or estimate is None:
            decision = "KEEP_CURRENT"
            applied = 0.0
            source = "fallback_current"
        else:
            threshold = max(
                float(deadband_deg),
                float(uncertainty) if uncertainty not in (None, "") else 0.0,
            )
            if abs(float(estimate)) <= threshold:
                decision = "NO_CORRECTION_NEEDED"
                applied = 0.0
                source = "deadband_current"
            else:
                decision = "APPLY_ESTIMATED_CORRECTION"
                applied = float(estimate)
                source = f"{branch.lower()}_estimate"
        if branch == "S1":
            audit = estimator_input_audit("S1", [case["off_raw"]], False, False, False, False)
        elif bool(case["target_enabled"]):
            audit = estimator_input_audit("Sassist", [case["on_raw"], case["off_raw"]], False, True, False, False)
        else:
            audit = estimator_input_audit("Sassist", [], False, False, False, False)
    return {
        "case_id": case["case_id"],
        "scene_class": case["scene_class"],
        "error_deg": error,
        "seed": case["seed"],
        "texture": case["texture"],
        "range_center_m": case["range_center_m"],
        "branch": branch,
        "status": status,
        "decision": decision,
        "estimate_deg": estimate,
        "uncertainty_deg": uncertainty,
        "zero_error_deadband_deg": deadband_deg,
        "applied_correction_deg": applied,
        "correction_source": source,
        "model_mismatch": mismatch,
        "fallback_reason": fallback,
        "causal_data_sources": (
            estimate_row.get("causal_data_sources") if estimate_row is not None
            else clutter_pilot.branch_contract()[branch]["causal_data_sources"]
        ),
        "target_truth_used": audit["contains_target_truth"],
        "nominal_target_used": audit["contains_nominal_target"],
        "known_servo_error_used": audit["contains_known_servo_error"],
        "servo_truth_used": audit["contains_servo_truth"],
        "input_paths": audit["input_paths"],
    }


def _packet_bytes(config: Mapping[str, object]) -> int:
    waveform = config.get("waveform")
    if not isinstance(waveform, Mapping):
        raise ValueError("waveform is required")
    return (
        int(waveform["pulse_len"])
        * int(waveform["new_protocol_channel_count"])
        * 2
        * 4
        + HEADER_BYTES
    )


def _production_xml_signature(path: Path) -> str:
    tree = ET.parse(path)
    root = tree.getroot()
    for node in root.iter():
        if node.tag in {"GMTI_data_new", "result_add"}:
            node.text = "<role-specific-path>"
    return hashlib.sha256(ET.tostring(root, encoding="utf-8")).hexdigest()


def _finite_mean(values: Sequence[object]) -> float | None:
    numeric = []
    for value in values:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            numeric.append(number)
    return None if not numeric else float(np.mean(numeric))


def _rmse(values: Sequence[object]) -> float | None:
    numeric = []
    for value in values:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            numeric.append(number)
    return None if not numeric else float(math.sqrt(np.mean(np.square(numeric))))


def _evaluate_result(
    result_root: Path,
    truth_rows: Sequence[Mapping[str, str]],
    match_config: Mapping[str, object],
) -> dict[str, object]:
    result_dir = generic_e2e._find_result_dir(result_root)
    detection_rows = _read_csv(result_dir / "detection_results.csv")
    matches, _, _ = eval_match.one_to_one_match(
        detection_rows,
        list(truth_rows),
        match_config,
        "det_id",
        use_beam=True,
        use_range=True,
        use_velocity=True,
    )
    errors = {
        key: [row.get(key) for row in matches]
        for key in (
            "position_error_m",
            "range_error_m",
            "velocity_error_mps",
            "azimuth_error_deg",
        )
    }
    detection_count = len(detection_rows)
    false_alarm_count = max(0, detection_count - len(matches))
    return {
        "result_dir": str(result_dir),
        "detection_count": detection_count,
        "truth_count": len(truth_rows),
        "matched_count": len(matches),
        "false_alarm_count": false_alarm_count,
        "target_pd_evaluation_only": (
            None if not truth_rows else len(matches) / len(truth_rows)
        ),
        "false_hit_fraction_evaluation_only": (
            None if not detection_rows else false_alarm_count / detection_count
        ),
        "mean_position_error_m": _finite_mean(errors["position_error_m"]),
        "mean_range_error_m": _finite_mean(errors["range_error_m"]),
        "mean_velocity_error_mps": _finite_mean(errors["velocity_error_mps"]),
        "mean_azimuth_error_deg": _finite_mean(errors["azimuth_error_deg"]),
        "range_rmse_m": _rmse(errors["range_error_m"]),
        "velocity_rmse_mps": _rmse(errors["velocity_error_mps"]),
        "azimuth_rmse_deg": _rmse(errors["azimuth_error_deg"]),
        "position_rmse_m": _rmse(errors["position_error_m"]),
    }


def _payload_energy(path: Path, packet_bytes: int) -> float | None:
    """Compute target-only payload energy without using any truth fields."""

    total = 0.0
    count = 0
    with path.open("rb") as stream:
        while True:
            packet = stream.read(packet_bytes)
            if not packet:
                break
            if len(packet) != packet_bytes:
                return None
            values = np.frombuffer(packet[HEADER_BYTES:], dtype="<f4")
            total += float(np.dot(values.astype(np.float64), values.astype(np.float64)))
            count += 1
    return total if count else None


def _aggregate_estimates(
    estimate_rows: Sequence[Mapping[str, object]],
    decision_rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for scene_name in scene_contract():
        for branch in ("S1", "Sassist"):
            selected = [
                row for row in estimate_rows
                if row["scene_class"] == scene_name and row["branch"] == branch
                and row.get("estimate_deg") not in (None, "")
            ]
            residuals = [float(row["estimate_deg"]) - float(row["error_deg"]) for row in selected]
            all_rows = [
                row for row in estimate_rows
                if row["scene_class"] == scene_name and row["branch"] == branch
            ]
            decisions = [
                row for row in decision_rows
                if row["scene_class"] == scene_name and row["branch"] == branch
            ]
            output.append({
                "scene_class": scene_name,
                "branch": branch,
                "case_count": len(all_rows),
                "estimate_count": len(residuals),
                "bias_deg": _finite_mean(residuals),
                "rmse_deg": _rmse(residuals),
                "mae_deg": _finite_mean([abs(value) for value in residuals]),
                "mean_uncertainty_deg": _finite_mean([row.get("uncertainty_deg") for row in selected]),
                "fallback_rate": (
                    None if not all_rows else sum(str(row.get("status")) not in {"VALID", "fit"} for row in all_rows) / len(all_rows)
                ),
                "model_mismatch_rate": (
                    None if not all_rows else sum(bool(row.get("model_mismatch")) for row in all_rows) / len(all_rows)
                ),
                "decision_count": len(decisions),
                "keep_current_count": sum(row.get("decision") == "KEEP_CURRENT" for row in decisions),
                "evaluation_scope": "target_evaluation_only_later_rows",
            })
    return output


def run_pilot(
    output_root: Path,
    simulator: Path = SIMULATOR,
    core: Path = GMTI_CORE,
    scene_names: Sequence[str] = tuple(scene_contract()),
    errors_deg: Sequence[float] = DEFAULT_ERRORS_DEG,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    textures: Sequence[str] = DEFAULT_TEXTURES,
    ranges_m: Sequence[float] = DEFAULT_RANGES_M,
    skip_core: bool = False,
) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty output: {output_root}")
    if not simulator.is_file():
        raise RuntimeError(f"missing Stage2 simulator: {simulator}")
    if not skip_core and not core.is_file():
        raise RuntimeError(f"missing GMTI_core: {core}")
    contract = scene_contract()
    unknown_scenes = set(scene_names) - set(contract)
    if unknown_scenes or not scene_names:
        raise ValueError(f"unsupported or empty scene set: {sorted(unknown_scenes)}")
    if not errors_deg or not seeds or not textures or not ranges_m:
        raise ValueError("servo GMTI E2E requires non-empty levels")
    if any(texture not in clutter_pilot.TEXTURE_CONFIGS for texture in textures):
        raise ValueError(f"unsupported texture: {textures}")
    if any(not math.isfinite(float(value)) for value in (*errors_deg, *ranges_m)):
        raise ValueError("servo errors and ranges must be finite")
    source_status = _git("status", "--short", "--untracked-files=all")
    source_commit = _git("rev-parse", "HEAD")
    gpu = target_assisted._probe_gpu()
    if not skip_core and int(gpu["returncode"]) != 0:
        raise RuntimeError("nvidia-smi failed; refusing to claim production servo E2E")
    output_root.mkdir(parents=True, exist_ok=True)
    compact_input = bool(skip_core)
    template = _prepare_template(json.loads(TEMPLATE.read_text(encoding="utf-8")), compact_input)
    _write_json(output_root / "prepared_template.json", template)

    combinations = [
        (scene_name, float(error), int(seed), texture, float(range_center))
        for scene_name in scene_names
        for error in errors_deg
        for seed in seeds
        for texture in textures
        for range_center in ranges_m
    ]
    case_records: list[dict[str, object]] = []
    estimate_rows: list[dict[str, object]] = []
    case_index_rows: list[dict[str, object]] = []
    for case_index, (scene_name, error_deg, seed, texture, range_center_m) in enumerate(combinations, 1):
        scene = contract[scene_name]
        case_root = output_root / "cases" / f"case_{case_index:03d}"
        case_root.mkdir(parents=True, exist_ok=True)
        on_config = _role_config(template, case_root, "ON", scene_name, scene, error_deg, seed, texture, range_center_m)
        to_config = _role_config(template, case_root, "TO", scene_name, scene, error_deg, seed, texture, range_center_m)
        on_config_path = case_root / "on.run.json"
        to_config_path = case_root / "to.run.json"
        _write_json(on_config_path, on_config)
        _write_json(to_config_path, to_config)
        on_rc, on_elapsed = _run_logged([str(simulator), "--config", str(on_config_path)], case_root / "on.stage2.log")
        if on_rc != 0:
            raise RuntimeError(f"Stage2 ON failed for {on_config_path}")
        to_rc, to_elapsed = _run_logged([str(simulator), "--config", str(to_config_path)], case_root / "to.stage2.log")
        if to_rc != 0:
            raise RuntimeError(f"Stage2 TO failed for {to_config_path}")
        on_root = case_root / "on"
        off_root = case_root / "off"
        to_root = case_root / "to"
        on_raw = _find_period_file(on_root)
        off_raw = _find_period_file(off_root)
        to_raw = _find_period_file(to_root)
        source_xml = on_root / "config/temp_config_stage2_period_0000.xml"
        off_xml = off_root / "config/temp_config_stage2_period_0000.xml"
        to_xml = to_root / "config/temp_config_stage2_period_0000.xml"
        resolved_path = on_root / "scenario_resolved.json"
        required = (source_xml, off_xml, to_xml, resolved_path)
        if any(not path.is_file() for path in required):
            raise RuntimeError(f"missing servo E2E artifact in {case_root}")
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        context = clutter_pilot._reported_context(
            on_config,
            f"off_c_plus_n:{on_config['case_id']}",
            range_center_m,
        )
        s1_audit = estimator_input_audit("S1", [off_raw], False, False, False, False)
        packets = clutter_pilot.clutter_only.load_target_free_packets(off_raw, context)
        features = clutter_pilot.clutter_only.extract_clutter_features(packets, context)
        s1_result = clutter_pilot.clutter_only.estimate_clutter_only_servo(features, {"seed": seed})
        _write_json(case_root / "clutter_only_servo_estimator.json", s1_result)
        del packets
        del features
        target_enabled = bool(scene["target_enabled"])
        if target_enabled:
            assist_result, assist_rows = target_assisted._estimate_servo(on_raw, off_raw, on_config, resolved, seed)
            sassist_audit = estimator_input_audit("Sassist", [on_raw, off_raw], False, True, False, False)
            _write_json(case_root / "target_assisted_servo_estimator.json", assist_result)
            _write_rows(case_root / "target_assisted_servo_observables.csv", assist_rows)
        else:
            assist_result = {"status": "NOT_APPLICABLE_TARGET_FREE"}
            sassist_audit = {
                "branch": "Sassist",
                "input_paths": [],
                "off_only": False,
                "contains_on_or_to": False,
                "contains_target_truth": False,
                "contains_nominal_target": False,
                "contains_known_servo_error": False,
                "contains_servo_truth": False,
            }
        case_id = str(on_config["case_id"])
        if case_id.endswith("_on"):
            case_id = case_id[:-3]
        case: dict[str, object] = {
            "case_index": case_index,
            "case_id": case_id,
            "case_root": str(case_root),
            "scene_class": scene_name,
            "target_velocity_class": scene["target_velocity_class"],
            "target_enabled": target_enabled,
            "ve_mps": scene["ve_mps"],
            "vn_mps": scene["vn_mps"],
            "error_deg": error_deg,
            "seed": seed,
            "texture": texture,
            "range_center_m": range_center_m,
            "stage2_on_exit_code": on_rc,
            "stage2_to_exit_code": to_rc,
            "stage2_on_elapsed_s": on_elapsed,
            "stage2_to_elapsed_s": to_elapsed,
            "scenario_config_on": str(on_config_path),
            "scenario_config_to": str(to_config_path),
            "resolved_scenario": str(resolved_path),
            "source_xml": str(source_xml),
            "off_xml": str(off_xml),
            "to_xml": str(to_xml),
            "on_raw": str(on_raw),
            "off_raw": str(off_raw),
            "to_raw": str(to_raw),
            "on_raw_sha256": _sha256(on_raw),
            "off_raw_sha256": _sha256(off_raw),
            "to_raw_sha256": _sha256(to_raw),
            "s1_estimator_input_audit": s1_audit,
            "sassist_estimator_input_audit": sassist_audit,
            "s1_estimator": s1_result,
            "sassist_estimator": assist_result,
        }
        case_records.append(case)
        estimate_rows.append(_estimate_row(case, "S1", s1_result, s1_audit))
        estimate_rows.append(
            _estimate_row(case, "Sassist", assist_result, sassist_audit)
            if target_enabled else _not_applicable_assisted_row(case)
        )
        case_index_rows.append({
            "case_index": case_index,
            "case_id": case_id,
            "scene_class": scene_name,
            "target_enabled": target_enabled,
            "target_velocity_class": scene["target_velocity_class"],
            "ve_mps": scene["ve_mps"],
            "vn_mps": scene["vn_mps"],
            "error_deg": error_deg,
            "seed": seed,
            "texture": texture,
            "range_center_m": range_center_m,
            "stage2_on_exit_code": on_rc,
            "stage2_to_exit_code": to_rc,
            "stage2_on_elapsed_s": on_elapsed,
            "stage2_to_elapsed_s": to_elapsed,
            "on_raw": str(on_raw),
            "off_raw": str(off_raw),
            "to_raw": str(to_raw),
            "S1_estimator_input": "OFF_C_PLUS_N_ONLY",
            "ON_use": "evaluation_only",
            "TO_use": "evaluation_only",
        })
        print(
            f"[servo-gmti][stage2] {case_index}/{len(combinations)} "
            f"scene={scene_name} texture={texture} range={range_center_m:.0f}m "
            f"error={error_deg:+.3f}deg seed={seed} "
            f"S1={s1_result.get('status')} Sassist={assist_result.get('fit_status', assist_result.get('status'))}",
            flush=True,
        )

    s1_rows = [row for row in estimate_rows if row["branch"] == "S1"]
    sa_rows = [row for row in estimate_rows if row["branch"] == "Sassist"]
    deadbands = {"S1": _zero_deadband(s1_rows), "Sassist": _zero_deadband(sa_rows)}
    decision_rows: list[dict[str, object]] = []
    estimate_by_case_branch = {(str(row["case_id"]), str(row["branch"])): row for row in estimate_rows}
    for case in case_records:
        for branch in BRANCHES:
            decision_rows.append(_decision(
                case,
                branch,
                estimate_by_case_branch.get((str(case["case_id"]), branch)),
                float(deadbands.get(branch) or 0.0),
            ))
    _write_rows(output_root / "case_index.csv", case_index_rows)
    _write_rows(output_root / "servo_estimates.csv", estimate_rows)
    _write_rows(output_root / "servo_decisions.csv", decision_rows)

    core_rows: list[dict[str, object]] = []
    e2e_rows: list[dict[str, object]] = []
    target_transfer_rows: list[dict[str, object]] = []
    production_signatures: list[str] = []
    match_config = json.loads(MATCH_CONFIG.read_text(encoding="utf-8"))
    for case in case_records:
        case_id = str(case["case_id"])
        truth_rows = _target_truth(Path(case["on_raw"]).parents[1])
        decision_by_branch = {
            branch: next(row for row in decision_rows if row["case_id"] == case_id and row["branch"] == branch)
            for branch in BRANCHES
        }
        if skip_core:
            for branch in BRANCHES:
                for role in ROLES:
                    e2e_rows.append({
                        "case_id": case_id,
                        "scene_class": case["scene_class"],
                        "seed": case["seed"],
                        "error_deg": case["error_deg"],
                        "texture": case["texture"],
                        "range_center_m": case["range_center_m"],
                        "branch": branch,
                        "role": role,
                        "evaluation_scope": "not_evaluated_core_skipped",
                        "core_status": "SKIPPED",
                        "target_pd_evaluation_only": None,
                        "false_hit_fraction_evaluation_only": None,
                        "causal_target_transfer_db_evaluation_only": None,
                    })
            continue
        packet_bytes = _packet_bytes(template)
        role_paths = {
            "OFF": Path(case["off_raw"]),
            "ON": Path(case["on_raw"]),
            "TO": Path(case["to_raw"]),
        }
        role_xml = {
            "OFF": Path(case["off_xml"]),
            "ON": Path(case["source_xml"]),
            "TO": Path(case["to_xml"]),
        }
        branch_role_raw: dict[tuple[str, str], Path] = {}
        for branch in BRANCHES:
            decision = decision_by_branch[branch]
            applied = float(decision.get("applied_correction_deg") or 0.0)
            for role in ROLES:
                source = role_paths[role]
                if branch == "S0" or abs(applied) <= 0.0:
                    branch_role_raw[(branch, role)] = source
                else:
                    destination = Path(case["case_root"]) / "corrections" / branch / f"{role.lower()}.bin"
                    target_assisted._rewrite_header_angle_with_layout(source, destination, packet_bytes, applied)
                    branch_role_raw[(branch, role)] = destination
        for branch in BRANCHES:
            for role in ROLES:
                view = dict(case)
                view["case_root"] = str(Path(case["case_root"]))
                view["source_xml"] = str(role_xml[role])
                row = target_assisted._run_core_branch(
                    view,
                    f"{role}_{branch}",
                    branch_role_raw[(branch, role)],
                    core,
                )
                row.update({
                    "branch": branch,
                    "role": role,
                    "calibration_mode": decision_by_branch[branch]["status"],
                    "correction_source": decision_by_branch[branch]["correction_source"],
                    "input_role": role_contract()[role]["use"],
                    "production_settings_signature": _production_xml_signature(Path(row["production_xml"])),
                })
                production_signatures.append(str(row["production_settings_signature"]))
                if role in {"ON", "TO"} and truth_rows:
                    p4_path = Path(row["result_root"]) / "p4_eval"
                    p4_result = generic_e2e._run_p4_eval(
                        generic_e2e._find_result_dir(Path(row["result_root"])),
                        Path(case["case_root"]) / "on" / "truth",
                        Path(row["result_root"]),
                    )
                else:
                    p4_result = {"p4_eval_status": "not_evaluated"}
                direct = _evaluate_result(Path(row["result_root"]), truth_rows, match_config)
                row.update({
                    "p4_eval_status": p4_result.get("p4_eval_status"),
                    **direct,
                    "evaluation_scope": "target_evaluation_only" if truth_rows else "target_free_false_alarm_only",
                    "causal_target_transfer_db_evaluation_only": None,
                })
                core_rows.append(row)
                e2e_rows.append({
                    "case_id": case_id,
                    "scene_class": case["scene_class"],
                    "seed": case["seed"],
                    "error_deg": case["error_deg"],
                    "texture": case["texture"],
                    "range_center_m": case["range_center_m"],
                    "branch": branch,
                    "role": role,
                    "evaluation_scope": row["evaluation_scope"],
                    "core_status": row["status"],
                    "csi_cancellation_db": (
                        row.get("csi_summary", {}).get("CA_ROI_dB")
                        if isinstance(row.get("csi_summary"), Mapping) else None
                    ),
                    "target_pd_evaluation_only": row.get("target_pd_evaluation_only"),
                    "false_hit_fraction_evaluation_only": row.get("false_hit_fraction_evaluation_only"),
                    "mean_position_error_m": row.get("mean_position_error_m"),
                    "mean_range_error_m": row.get("mean_range_error_m"),
                    "mean_velocity_error_mps": row.get("mean_velocity_error_mps"),
                    "mean_azimuth_error_deg": row.get("mean_azimuth_error_deg"),
                    "position_rmse_m": row.get("position_rmse_m"),
                    "range_rmse_m": row.get("range_rmse_m"),
                    "velocity_rmse_mps": row.get("velocity_rmse_mps"),
                    "azimuth_rmse_deg": row.get("azimuth_rmse_deg"),
                    "causal_target_transfer_db_evaluation_only": None,
                })
        if truth_rows:
            by_role_branch = {(row["role"], row["branch"]): row for row in core_rows if row["case_id"] == case_id}
            for branch in BRANCHES:
                on_eval = by_role_branch[("ON", branch)]
                off_eval = by_role_branch[("OFF", branch)]
                to_eval = by_role_branch[("TO", branch)]
                on_target = int(on_eval.get("matched_count", 0))
                off_target = int(off_eval.get("matched_count", 0))
                causal_hit = bool(on_target > 0 and off_target == 0)
                current_to = by_role_branch[("TO", "S0")]
                target_only_payload_db = None
                energy = _payload_energy(branch_role_raw[(branch, "TO")], packet_bytes)
                current_energy = _payload_energy(branch_role_raw[("S0", "TO")], packet_bytes)
                if energy is not None and current_energy is not None and energy > 0.0 and current_energy > 0.0:
                    target_only_payload_db = 10.0 * math.log10(energy / current_energy)
                target_transfer_rows.append({
                    "case_id": case_id,
                    "scene_class": case["scene_class"],
                    "seed": case["seed"],
                    "error_deg": case["error_deg"],
                    "texture": case["texture"],
                    "range_center_m": case["range_center_m"],
                    "branch": branch,
                    "on_target_pd_evaluation_only": on_eval.get("target_pd_evaluation_only"),
                    "off_target_roi_match_count_evaluation_only": off_target,
                    "target_only_pd_evaluation_only": to_eval.get("target_pd_evaluation_only"),
                    "paired_causal_target_hit_evaluation_only": causal_hit,
                    "false_hit_fraction_on_evaluation_only": on_eval.get("false_hit_fraction_evaluation_only"),
                    "false_hit_fraction_off_evaluation_only": off_eval.get("false_hit_fraction_evaluation_only"),
                    "causal_target_transfer_db_evaluation_only": None,
                    "target_only_payload_transfer_db_evaluation_only": target_only_payload_db,
                    "causal_transfer_definition": "ON target match AND OFF target-ROI match absent; payload transfer is TO raw payload energy relative to S0",
                    "target_position_error_m": on_eval.get("mean_position_error_m"),
                    "target_range_error_m": on_eval.get("mean_range_error_m"),
                    "target_velocity_error_mps": on_eval.get("mean_velocity_error_mps"),
                    "target_azimuth_error_deg": on_eval.get("mean_azimuth_error_deg"),
                })

    _write_rows(output_root / "core_metrics.csv", core_rows)
    _write_rows(output_root / "e2e_metrics.csv", e2e_rows)
    _write_rows(output_root / "target_transfer.csv", target_transfer_rows)
    aggregate_rows = _aggregate_estimates(estimate_rows, decision_rows)
    _write_rows(output_root / "aggregate_metrics.csv", aggregate_rows)
    source_status_after = _git("status", "--short", "--untracked-files=all")
    manifest: dict[str, object] = {
        "schema": "servo_gmti_moving_target_e2e_v1",
        "status": "completed",
        "ai_training": False,
        "ai_router": False,
        "scene_contract": contract,
        "role_contract": role_contract(),
        "branch_contract": clutter_pilot.branch_contract(),
        "estimator_input_contract": {
            "S1": "OFF=C+N raw plus reported context only",
            "Sassist": "paired ON-OFF target residual; evaluation reference only",
            "forbidden_for_S1": ["ON", "TO", "target truth", "nominal target metadata", "known injected servo error", "servo truth"],
        },
        "levels": {
            "scene_classes": list(scene_names),
            "errors_deg": [float(value) for value in errors_deg],
            "seeds": [int(value) for value in seeds],
            "textures": list(textures),
            "range_centers_m": [float(value) for value in ranges_m],
            "case_count": len(case_records),
        },
        "source": {
            "working_directory": str(ROOT),
            "source_commit_before_run": source_commit,
            "worktree_status_before_run": source_status,
            "worktree_status_after_run_tracked_only": _git("status", "--short", "--untracked-files=no"),
            "template": str(TEMPLATE.resolve()),
            "template_sha256": _sha256(TEMPLATE),
            "simulator": str(simulator.resolve()),
            "simulator_sha256": _sha256(simulator),
            "gmticore": str(core.resolve()) if core.is_file() else None,
            "gmticore_sha256": _sha256(core) if core.is_file() else None,
        },
        "execution": {
            "command": [str(item) for item in sys.argv],
            "python": sys.version,
            "platform": platform.platform(),
            "gpu_probe_before_run": gpu,
            "core_skipped": skip_core,
            "compact_estimator_input": compact_input,
            "production_settings_signature_match": (
                None if not production_signatures else len(set(production_signatures)) == 1
            ),
        },
        "counts": {
            "case_count": len(case_records),
            "estimate_rows": len(estimate_rows),
            "decision_rows": len(decision_rows),
            "core_rows": len(core_rows),
            "e2e_rows": len(e2e_rows),
            "target_transfer_rows": len(target_transfer_rows),
            "stage2_on_success": all(int(case["stage2_on_exit_code"]) == 0 for case in case_records),
            "stage2_to_success": all(int(case["stage2_to_exit_code"]) == 0 for case in case_records),
            "core_success": skip_core or all(row.get("status") == "completed" for row in core_rows),
        },
        "artifacts": {
            "case_index": str((output_root / "case_index.csv").resolve()),
            "servo_estimates": str((output_root / "servo_estimates.csv").resolve()),
            "servo_decisions": str((output_root / "servo_decisions.csv").resolve()),
            "aggregate_metrics": str((output_root / "aggregate_metrics.csv").resolve()),
            "e2e_metrics": str((output_root / "e2e_metrics.csv").resolve()),
            "target_transfer": str((output_root / "target_transfer.csv").resolve()),
            "core_metrics": str((output_root / "core_metrics.csv").resolve()),
        },
        "aggregate_metrics": aggregate_rows,
        "case_records": case_records,
        "limitations": [
            "S1 is the only clutter-only estimator; ON and TO are evaluation-only and are not supplied to it.",
            "Sassist is target-assisted and must not be reported as target-free clutter-only performance.",
            "Target-free cases have no target Pd or target transfer denominator; those fields remain null.",
            "Target Pd is interpreted with target-selection, truth-gate, position and velocity audits; it is not an amplitude-gain claim.",
            "TrackManager/PIPE causal retention is not part of this single-period pilot and is handled by the later production gate.",
            "A skip-core matrix is estimator/information-flow evidence only; it is not production CUDA, Pd or Pfa evidence.",
        ],
    }
    _write_json(output_root / "manifest.json", manifest)
    del source_status_after
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", "--output-root", dest="output_root", type=Path,
                        default=ROOT / "outputs/servo_gmti_e2e_20260914")
    parser.add_argument("--simulator", type=Path, default=SIMULATOR)
    parser.add_argument("--gmticore", type=Path, default=GMTI_CORE)
    parser.add_argument("--scene", dest="scenes", nargs="+", default=list(scene_contract()))
    parser.add_argument("--errors-deg", nargs="+", default=[str(value) for value in DEFAULT_ERRORS_DEG])
    parser.add_argument("--seeds", nargs="+", default=[str(value) for value in DEFAULT_SEEDS])
    parser.add_argument("--textures", nargs="+", default=list(DEFAULT_TEXTURES))
    parser.add_argument("--ranges-m", nargs="+", default=[str(value) for value in DEFAULT_RANGES_M])
    parser.add_argument("--skip-core", action="store_true", help="skip production GMTI_core and retain evaluation fields as null")
    args = parser.parse_args(argv)
    manifest = run_pilot(
        args.output_root.resolve(),
        args.simulator.resolve(),
        args.gmticore.resolve(),
        scene_names=list(args.scenes),
        errors_deg=_parse_float_list(args.errors_deg, "errors-deg"),
        seeds=_parse_int_list(args.seeds, "seeds"),
        textures=list(args.textures),
        ranges_m=_parse_float_list(args.ranges_m, "ranges-m"),
        skip_core=args.skip_core,
    )
    print(json.dumps({
        "status": manifest["status"],
        "output_root": str(args.output_root.resolve()),
        "counts": manifest["counts"],
        "core_skipped": manifest["execution"]["core_skipped"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
