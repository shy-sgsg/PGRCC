#!/usr/bin/env python3
"""Run the true-geometry unknown-error GMTI end-to-end pilot.

The calibration input is a separate multi-beam ``unknown_off`` Stage2 file.
The estimator sees only that file, reported channel positions, and nominal
waveform/platform/beam metadata.  The target cases are generated separately
with exact paired C+N background and target-only output:

* B0: production four-channel protocol pair fusion -> current two-channel CSI;
* B1/B1K: the same production path after blind-estimated/known-upper-bound
  geometry correction;
* B2/B3/B3K: an offline four-channel JDL-3x4 conventional STAP reference,
  with uncalibrated, blind-estimated, and known-error inputs.

B2/B3/B3K are explicitly an offline scientific reference, not a production
CUDA STAP implementation.  No AI model or router is trained or invoked.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_baseline_benchmark as baseline  # noqa: E402
import run_model_mismatch_audit as production_audit  # noqa: E402
import run_unknown_system_error_matrix as matrix  # noqa: E402
from unknown_geometry_correction import (  # noqa: E402
    apply_unknown_geometry_correction,
)


E2E_TEMPLATE = ROOT / "configs/research/unknown_system_error_end_to_end_pilot.json"
CALIBRATION_TEMPLATE = ROOT / "configs/research/unknown_system_error_true_geometry_pilot.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
GMTI_CORE = ROOT / "build/GMTI_core"
P4_EVAL = ROOT / "scripts/run_p4_truth_eval.py"
MATCH_CONFIG = ROOT / "configs/eval/match_config.json"
BASELINE_DELTA_M = 0.0025
DEFAULT_VELOCITIES = ((-3.0, 2.0), (-6.0, 4.0), (-12.0, 8.0))


def condition_contract() -> dict[str, dict[str, object]]:
    """Describe the comparison conditions and their information interfaces."""

    return {
        "B0_Current": {
            "label": "B0 Current",
            "interface": "four_channel_protocol_pair_fusion_to_two_channel_CSI",
            "correction_source": "none",
            "blind_estimated": False,
            "production_cuda": True,
        },
        "B1_Blind_Estimated_Current": {
            "label": "B1 blind estimated Current",
            "interface": "four_channel_protocol_pair_fusion_to_two_channel_CSI",
            "correction_source": "unknown_only_blind_estimate",
            "blind_estimated": True,
            "production_cuda": True,
        },
        "B1K_Known_Current": {
            "label": "B1K known-error correction Current",
            "interface": "four_channel_protocol_pair_fusion_to_two_channel_CSI",
            "correction_source": "known_error_upper_bound",
            "blind_estimated": False,
            "production_cuda": True,
        },
        "B2_Uncalibrated_4ch_STAP": {
            "label": "B2 uncalibrated four-channel conventional STAP",
            "interface": "four_channel_offline_conventional_STAP_reference",
            "correction_source": "none",
            "blind_estimated": False,
            "production_cuda": False,
        },
        "B3_Blind_4ch_STAP": {
            "label": "B3 blind calibrated four-channel conventional STAP",
            "interface": "four_channel_offline_conventional_STAP_reference",
            "correction_source": "unknown_only_blind_estimate",
            "blind_estimated": True,
            "production_cuda": False,
        },
        "B3K_Known_4ch_STAP": {
            "label": "B3K known-error four-channel conventional STAP",
            "interface": "four_channel_offline_conventional_STAP_reference",
            "correction_source": "known_error_upper_bound",
            "blind_estimated": False,
            "production_cuda": False,
        },
    }


def target_transfer_conditions() -> tuple[str, ...]:
    """Names for target-only transfer checks, separate from C+N cancellation."""

    return ("target_only_current", "target_only_blind", "target_only_known")


def validate_moving_velocities(
    velocities: Iterable[tuple[float, float] | Sequence[float]],
) -> list[tuple[float, float]]:
    normalized: list[tuple[float, float]] = []
    for item in velocities:
        if len(item) != 2:
            raise ValueError(f"velocity must be (ve_mps, vn_mps): {item}")
        ve = float(item[0])
        vn = float(item[1])
        if not math.isfinite(ve) or not math.isfinite(vn):
            raise ValueError(f"velocity must be finite: {item}")
        if math.hypot(ve, vn) <= 0.0:
            raise ValueError("end-to-end target cases must all be moving; static velocity is not allowed")
        normalized.append((ve, vn))
    if not normalized:
        raise ValueError("at least one moving target velocity is required")
    return normalized


def recovery_metrics(
    current_value: float,
    known_value: float,
    estimated_value: float,
    metric_direction: str,
    zero_tolerance: float = 1.0e-12,
) -> dict[str, Optional[float]]:
    """Return the signed recovery metrics with an explicit direction."""

    current = float(current_value)
    known = float(known_value)
    estimated = float(estimated_value)
    if metric_direction == "higher_is_better":
        recoverable = known - current
        actual = estimated - current
    elif metric_direction == "lower_is_better":
        recoverable = current - known
        actual = current - estimated
    else:
        raise ValueError("metric_direction must be higher_is_better or lower_is_better")
    ratio = None if abs(recoverable) <= zero_tolerance else actual / recoverable
    return {
        "recoverable_space_known_minus_current": recoverable,
        "actual_recovered_estimated_minus_current": actual,
        "recovery_ratio": ratio,
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _float_or_none(value: object) -> Optional[float]:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "--git-dir=.git-real", "--work-tree=.", *args],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _find_period_file(case_dir: Path) -> Path:
    manifest = case_dir / "data/period_files.csv"
    if not manifest.is_file():
        raise RuntimeError(f"Stage2 period manifest is missing: {manifest}")
    with manifest.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise RuntimeError(f"end-to-end pilot expects one period: {manifest} -> {len(rows)}")
    raw = Path(rows[0]["file"])
    candidates = [raw] if raw.is_absolute() else [case_dir / raw, ROOT / raw, raw]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(f"Stage2 period file is missing: {candidates}")


def _run_logged(command: Sequence[str], log_path: Path, cwd: Path = ROOT) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(str(item) for item in command) + "\n")
        log.flush()
        completed = subprocess.run(
            [str(item) for item in command],
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log.write(f"exit_code={completed.returncode}\n")
    return int(completed.returncode)


def _run_stage2(config_path: Path, log_path: Path, simulator: Path) -> None:
    if not simulator.is_file():
        raise RuntimeError(f"missing Stage2 simulator: {simulator}")
    rc = _run_logged([str(simulator), "--config", str(config_path)], log_path)
    if rc != 0:
        raise RuntimeError(f"Stage2 failed for {config_path}; see {log_path}")


def _calibration_config(
    template: Mapping[str, object], output_dir: Path, seed: int
) -> dict[str, object]:
    config = copy.deepcopy(template)
    config["case_id"] = "unknown_system_error_end_to_end_calibration"
    config["output_dir"] = str(output_dir.resolve())
    config["truth_output"] = False
    config.setdefault("random", {})["random_seed"] = int(seed)  # type: ignore[union-attr]
    scene = config.setdefault("scene", {})
    scene["signal_only"] = False  # type: ignore[index]
    for target in config.get("targets", []):
        if isinstance(target, dict):
            target["enabled"] = False
    return config


def _target_config(
    template: Mapping[str, object],
    case_id: str,
    output_dir: Path,
    ve_mps: float,
    vn_mps: float,
    seed: int,
    signal_only: bool,
    paired_background_output_dir: Optional[Path],
) -> dict[str, object]:
    config = copy.deepcopy(template)
    config["case_id"] = case_id
    config["output_dir"] = str(output_dir.resolve())
    config["truth_output"] = True
    config["paired_background_output_dir"] = (
        str(paired_background_output_dir.resolve())
        if paired_background_output_dir is not None
        else ""
    )
    config.setdefault("random", {})["random_seed"] = int(seed)  # type: ignore[union-attr]
    scene = config.setdefault("scene", {})
    scene["signal_only"] = bool(signal_only)  # type: ignore[index]
    targets = config.setdefault("targets", [])
    if not targets:
        raise ValueError("end-to-end template must contain one target")
    target = targets[0]
    if not isinstance(target, dict):
        raise ValueError("target configuration must be an object")
    target["enabled"] = True
    motion = target.setdefault("motion", {})
    if not isinstance(motion, dict):
        raise ValueError("target.motion must be an object")
    motion["ve_mps"] = float(ve_mps)
    motion["vn_mps"] = float(vn_mps)
    impairments = config.setdefault("channel_impairments", {})
    if not isinstance(impairments, dict):
        raise ValueError("channel_impairments must be an object")
    if impairments.get("baseline_error_m", 0.0) not in (0, 0.0):
        raise ValueError("end-to-end template must use true geometry, not the legacy baseline injector")
    impairments["enabled"] = False
    return config


def _run_calibration(
    output_root: Path,
    simulator: Path,
    seed: int,
) -> dict[str, object]:
    template = json.loads(CALIBRATION_TEMPLATE.read_text(encoding="utf-8"))
    calibration_root = output_root / "calibration"
    calibration_root.mkdir(parents=True, exist_ok=True)
    config_path = calibration_root / "calibration.run.json"
    config = _calibration_config(template, calibration_root / "raw", seed)
    _write_json(config_path, config)
    log_path = calibration_root / "stage2.log"
    _run_stage2(config_path, log_path, simulator)
    raw_path = _find_period_file(calibration_root / "raw")
    resolved_path = calibration_root / "raw/scenario_resolved.json"
    if not resolved_path.is_file():
        raise RuntimeError(f"calibration resolved scenario is missing: {resolved_path}")
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    allowed_metadata = matrix.estimator_metadata(resolved)
    positions = matrix.OBS.load_channel_positions(CALIBRATION_TEMPLATE, source="reported")
    start, end = matrix._estimator_range_window(template, 1024, 32)
    layout = matrix._layout(template)
    estimate = matrix.OBS.estimate_unknown_baseline(
        raw_path,
        pulse_len=int(layout["pulse_len"]),
        channel_count=int(layout["channel_count"]),
        iq_data_type=str(layout["iq_data_type"]),
        fc_hz=float(layout["fc_hz"]),
        nominal_channel_positions_m=positions,
        metadata=allowed_metadata,
        pulses_per_beam=int(layout["pulses_per_beam"]),
        pair_mode="six_pair",
        range_block_size=32,
        range_start=start,
        range_end=end,
        max_abs_delta_m=0.02,
        min_coherence=0.6,
    )
    compact = copy.deepcopy(estimate)
    compact.pop("input_path", None)
    _write_json(calibration_root / "unknown_only_estimator.json", compact)
    _write_json(calibration_root / "estimator_allowed_metadata.json", allowed_metadata)
    if estimate.get("fit_status") != "fit":
        raise RuntimeError(f"unknown-only calibration did not fit: {estimate.get('fit_status')}")
    estimated = _float_or_none(estimate.get("estimated_baseline_error_m"))
    if estimated is None:
        raise RuntimeError("unknown-only calibration returned no estimated baseline")
    return {
        "config_path": config_path,
        "raw_path": raw_path,
        "resolved_path": resolved_path,
        "estimator": compact,
        "metadata": allowed_metadata,
        "reported_positions_m": positions,
        "range_start": start,
        "range_end": end,
        "estimated_delta_m": estimated,
        "truth_delta_m_for_outer_evaluation": BASELINE_DELTA_M,
    }


def _prepare_target_case(
    output_root: Path,
    template: Mapping[str, object],
    simulator: Path,
    velocity_index: int,
    ve_mps: float,
    vn_mps: float,
) -> dict[str, object]:
    speed_tag = f"ve_{ve_mps:+.3f}_vn_{vn_mps:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")
    case_root = output_root / "cases" / f"velocity_{velocity_index:02d}_{speed_tag}"
    on_root = case_root / "on"
    off_root = case_root / "off"
    target_only_root = case_root / "target_only"
    case_root.mkdir(parents=True, exist_ok=True)
    template_copy = copy.deepcopy(template)
    on_config = _target_config(
        template_copy,
        f"unknown_system_error_e2e_v{velocity_index:02d}_on",
        on_root,
        ve_mps,
        vn_mps,
        7001 + velocity_index,
        False,
        off_root,
    )
    on_config_path = case_root / "on.run.json"
    _write_json(on_config_path, on_config)
    _run_stage2(on_config_path, case_root / "on.stage2.log", simulator)
    target_only_config = _target_config(
        template_copy,
        f"unknown_system_error_e2e_v{velocity_index:02d}_target_only",
        target_only_root,
        ve_mps,
        vn_mps,
        7001 + velocity_index,
        True,
        None,
    )
    target_only_config_path = case_root / "target_only.run.json"
    _write_json(target_only_config_path, target_only_config)
    _run_stage2(target_only_config_path, case_root / "target_only.stage2.log", simulator)
    on_raw = _find_period_file(on_root)
    off_raw = _find_period_file(off_root)
    target_only_raw = _find_period_file(target_only_root)
    on_xml = on_root / "config/temp_config_stage2_period_0000.xml"
    off_xml = off_root / "config/temp_config_stage2_period_0000.xml"
    target_only_xml = target_only_root / "config/temp_config_stage2_period_0000.xml"
    truth = on_root / "truth/truth_targets_by_beam.csv"
    resolved = on_root / "scenario_resolved.json"
    for path in (on_xml, off_xml, target_only_xml, truth, resolved):
        if not path.is_file():
            raise RuntimeError(f"target case artifact is missing: {path}")
    return {
        "case_id": f"velocity_{velocity_index:02d}",
        "velocity_index": velocity_index,
        "ve_mps": float(ve_mps),
        "vn_mps": float(vn_mps),
        "speed_mps": float(math.hypot(ve_mps, vn_mps)),
        "case_root": case_root,
        "on_raw": on_raw,
        "off_raw": off_raw,
        "target_only_raw": target_only_raw,
        "on_xml": on_xml,
        "off_xml": off_xml,
        "target_only_xml": target_only_xml,
        "truth": truth,
        "resolved": resolved,
        "configs": [on_config_path, target_only_config_path],
    }


def _prepare_corrections(
    case: Mapping[str, object],
    metadata: Mapping[str, object],
    reported_positions: Sequence[Sequence[float]],
    estimated_delta_m: float,
    known_delta_m: float,
) -> dict[str, dict[str, Path]]:
    case_root = Path(case["case_root"])
    source_paths = {
        "on": Path(case["on_raw"]),
        "off": Path(case["off_raw"]),
        "target_only": Path(case["target_only_raw"]),
    }
    result: dict[str, dict[str, Path]] = {
        "original": dict(source_paths),
        "blind": {},
        "known": {},
    }
    for correction_name, delta_m in (("blind", estimated_delta_m), ("known", known_delta_m)):
        for role, source in source_paths.items():
            destination = case_root / "corrections" / correction_name / f"{role}.bin"
            apply_unknown_geometry_correction(
                source,
                destination,
                metadata,
                delta_m,
                reported_channel_positions_m=reported_positions,
            )
            result[correction_name][role] = destination
    return result


def _set_xml_path(xml_path: Path, raw_path: Path, result_root: Path) -> None:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    parameter = root.find(".//GMTI_parameter")
    if parameter is None:
        raise RuntimeError(f"XML has no GMTI_parameter: {xml_path}")
    data_node = parameter.find("GMTI_data_new")
    result_node = parameter.find("result_add")
    if data_node is None or result_node is None:
        raise RuntimeError(f"XML has no GMTI_data_new/result_add: {xml_path}")
    data_node.text = str(raw_path.resolve())
    result_node.text = str((result_root / "stage2/algorithm_result/period_0000").resolve())
    debug = parameter.find("debug_pc_peak")
    if debug is not None:
        debug.text = "0"
    scene_truth = parameter.find("pc_peak_scene_truth")
    if scene_truth is not None:
        scene_truth.text = ""
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _read_first_timing(result_root: Path) -> dict[str, object]:
    paths = sorted(result_root.rglob("timing_metrics.csv"))
    if not paths:
        return {}
    rows = _read_csv_rows(paths[0])
    if not rows:
        return {}
    by_scope = {row.get("scope_name"): row for row in rows if row.get("scope_name")}
    selected = (
        by_scope.get("main_total")
        or by_scope.get("gmti_processing")
        or rows[0]
    )
    timing: dict[str, object] = dict(selected)
    timing["selected_scope"] = selected.get("scope_name")
    for scope_name, field_name in (
        ("main_total", "main_total_ms"),
        ("gmti_processing", "gmti_processing_ms"),
        ("dbs_processing", "dbs_processing_ms"),
        ("detection", "detection_ms"),
    ):
        row = by_scope.get(scope_name)
        if row is not None:
            timing[field_name] = _float_or_none(row.get("elapsed_ms"))
    timing["runtime_total_ms"] = timing.get("main_total_ms") or timing.get("gmti_processing_ms")
    timing["timing_csv"] = str(paths[0])
    return timing


def _read_runtime_config(result_root: Path) -> dict[str, object]:
    paths = sorted(result_root.rglob("runtime_config_dump.json"))
    if not paths:
        return {}
    try:
        value = json.loads(paths[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_generic_cfar(log_path: Path) -> dict[str, object]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = re.findall(r"\[CFAR\]\[SUMMARY\].*", text)
    if not lines:
        return {
            "summary_found": False,
            "hit_cells": None,
            "clusters": None,
            "selected": None,
            "test_cells": None,
            "pfa": None,
        }
    line = lines[-1]
    values: dict[str, object] = {"summary_found": True, "summary_line": line}
    for key, raw in re.findall(r"([A-Za-z][A-Za-z0-9_]*)=([-+0-9.eE]+)", line):
        number = _float_or_none(raw)
        if number is not None and number.is_integer():
            values[key] = int(number)
        else:
            values[key] = number
    hit = _float_or_none(values.get("hit_cells"))
    test_cells = _float_or_none(
        values.get("test_cells", values.get("valid_test_cells", values.get("cfar_test_cells")))
    )
    values["hit_cells"] = None if hit is None else int(hit)
    values["clusters"] = values.get("clusters")
    values["selected"] = values.get("selected")
    values["test_cells"] = test_cells
    values["pfa"] = None if hit is None or test_cells is None or test_cells <= 0.0 else hit / test_cells
    return values


def _find_result_dir(production_root: Path) -> Path:
    manifests = sorted(production_root.rglob("run_manifest.json"))
    if manifests:
        return manifests[0].parent
    detections = sorted(production_root.rglob("detection_results.csv"))
    if detections:
        return detections[0].parent
    return production_root / "stage2/algorithm_result/period_0000"


def _run_p4_eval(
    result_dir: Path,
    truth_dir: Path,
    production_root: Path,
) -> dict[str, object]:
    detection = result_dir / "detection_results.csv"
    if not detection.is_file():
        return {
            "p4_eval_status": "missing_detection_results",
            "target_detection_pd": None,
            "target_detection_count": None,
            "target_false_alarm_count": None,
        }
    eval_dir = production_root / "p4_eval"
    eval_log = production_root / "p4_eval.log"
    rc = _run_logged(
        [
            sys.executable,
            str(P4_EVAL),
            "--output-dir",
            str(result_dir),
            "--truth-dir",
            str(truth_dir),
            "--match-config",
            str(MATCH_CONFIG),
            "--report-dir",
            str(eval_dir),
        ],
        eval_log,
    )
    if rc != 0:
        return {
            "p4_eval_status": f"failed_exit_{rc}",
            "target_detection_pd": None,
            "target_detection_count": None,
            "target_false_alarm_count": None,
        }
    rows = _read_csv_rows(eval_dir / "detection_metrics.csv")
    row = rows[0] if rows else {}
    return {
        "p4_eval_status": "completed",
        "target_detection_pd": _float_or_none(row.get("recall")),
        "target_detection_count": _float_or_none(row.get("detection_count")),
        "target_false_alarm_count": _float_or_none(row.get("false_alarm_count")),
        "target_matched_count": _float_or_none(row.get("matched_count")),
        "target_mean_position_error_m": _float_or_none(row.get("mean_position_error_m")),
        "target_mean_velocity_error_mps": _float_or_none(row.get("mean_velocity_error_mps")),
    }


def _run_production(
    case: Mapping[str, object],
    condition_id: str,
    split: str,
    raw_path: Path,
    source_xml: Path,
    output_root: Path,
    core: Path,
) -> dict[str, object]:
    production_root = output_root / "production" / str(case["case_id"]) / condition_id / split
    production_root.mkdir(parents=True, exist_ok=True)
    xml_path = production_root / "config.xml"
    shutil.copy2(source_xml, xml_path)
    _set_xml_path(xml_path, raw_path, production_root)
    production_audit.patch_production_xml(xml_path)
    log_path = production_root / "gmticore.log"
    command = [str(core), str(xml_path), "--runtime-mode=debug", "--runtime-diagnostics=on"]
    rc = _run_logged(command, log_path)
    row: dict[str, object] = {
        "case_id": case["case_id"],
        "condition_id": condition_id,
        "split": split,
        "ve_mps": case["ve_mps"],
        "vn_mps": case["vn_mps"],
        "production_exit_code": rc,
        "raw_path": str(raw_path),
        "raw_sha256": _sha256(raw_path),
        "production_root": str(production_root),
        "status": "failed" if rc != 0 else "completed",
    }
    if rc != 0:
        return row
    manifest = production_audit.find_single_manifest(production_root)
    summary = production_audit.read_csi_summary(manifest)
    manifest_rows = _read_csv_rows(manifest)
    active_manifest = next(
        (item for item in manifest_rows if item.get("roi_name") == "active_support_unmasked"),
        manifest_rows[0] if manifest_rows else {},
    )
    cfar = _read_generic_cfar(log_path)
    result_dir = _find_result_dir(production_root)
    runtime_config = _read_runtime_config(production_root)
    detection_config = runtime_config.get("detection", {})
    if not isinstance(detection_config, Mapping):
        detection_config = {}
    guard_cells = _float_or_none(detection_config.get("cfar_guard_cells"))
    background_cells = _float_or_none(detection_config.get("cfar_background_cells"))
    range_cells = _float_or_none(
        runtime_config.get("pulse_compression", {}).get("pc_crop_len")
        if isinstance(runtime_config.get("pulse_compression", {}), Mapping)
        else None
    )
    active_rows = None
    if active_manifest.get("az_st") is not None and active_manifest.get("az_ed") is not None:
        try:
            active_rows = float(int(active_manifest["az_ed"]) - int(active_manifest["az_st"]) + 1)
        except (TypeError, ValueError):
            active_rows = None
    cfar_hit = _float_or_none(cfar.get("hit_cells"))
    cfar_test_cells = None
    cfar_pfa_empirical = None
    if (
        cfar_hit is not None
        and active_rows is not None
        and range_cells is not None
        and guard_cells is not None
        and background_cells is not None
    ):
        radius = int(guard_cells + background_cells)
        cfar_test_cells = active_rows * max(0.0, range_cells - 2.0 * radius)
        if cfar_test_cells > 0.0:
            cfar_pfa_empirical = cfar_hit / cfar_test_cells
    p4 = (
        _run_p4_eval(result_dir, Path(case["truth"]).parent, production_root)
        if split in {"on", "target_only"}
        else {
            "p4_eval_status": "not_applicable_target_off",
            "target_detection_pd": None,
            "target_detection_count": None,
            "target_false_alarm_count": None,
        }
    )
    row.update({
        "status": "completed_with_missing_cfar_summary" if not cfar.get("summary_found") else "completed",
        "csi_manifest": str(manifest),
        "csi_valid_cells": _float_or_none(summary.get("valid_cells")),
        "csi_cancellation_db": _float_or_none(summary.get("CA_ROI_dB")),
        "csi_coherence": _float_or_none(summary.get("phase_model_weighted_coherence")),
        "csi_rmse_rad": _float_or_none(summary.get("phase_model_weighted_circular_rmse_rad")),
        "csi_status": summary.get("status"),
        "csi_az_st": _float_or_none(active_manifest.get("az_st")),
        "csi_az_ed": _float_or_none(active_manifest.get("az_ed")),
        "csi_rg_st": _float_or_none(active_manifest.get("rg_st")),
        "csi_rg_ed": _float_or_none(active_manifest.get("rg_ed")),
        "cfar_hit_cells": cfar.get("hit_cells"),
        "cfar_clusters": cfar.get("clusters"),
        "cfar_selected": cfar.get("selected"),
        "cfar_test_cells": cfar_test_cells if cfar_test_cells is not None else cfar.get("test_cells"),
        "cfar_pfa": cfar_pfa_empirical if cfar_pfa_empirical is not None else cfar.get("pfa"),
        "cfar_pfa_definition": (
            "empirical_hit_cells_over_dynamic_band_valid_CUTs"
            if cfar_pfa_empirical is not None
            else "not_emitted"
        ),
        "cfar_configured_pfa": _float_or_none(detection_config.get("pf")),
        "cfar_summary_found": cfar.get("summary_found"),
        "timing": _read_first_timing(production_root),
        **p4,
    })
    return row


def _case_for_offline(case: Mapping[str, object]) -> dict[str, object]:
    return {
        "case_id": str(case["case_id"]),
        "label": "true_geometry_unknown_error_e2e",
        "motion": {"ve_mps": float(case["ve_mps"]), "vn_mps": float(case["vn_mps"])},
        "target_snr_db": 15.0,
        "platform_speed_mps": 60.0,
        "platform_height_m": 6000.0,
        "scan_min_deg": 0.0,
        "scan_step_deg": 2.0,
        "beam_theta_offset_deg": 0.0,
    }


def _run_offline_stap(
    case: Mapping[str, object],
    condition_id: str,
    raw_paths: Mapping[str, Path],
    output_root: Path,
) -> dict[str, object]:
    case_data_paths: dict[str, Path | bool] = {
        "case_root": Path(case["case_root"]),
        "target_bin": raw_paths["on"],
        "background_bin": raw_paths["off"],
        "target_xml": Path(case["on_xml"]),
        "truth": Path(case["truth"]),
        "keep_data": True,
    }
    data = baseline.load_case_data(_case_for_offline(case), case_data_paths)
    result = baseline.run_method(data, "JDL-3x4 reduced STAP")
    metrics = baseline.output_metrics(data, result)
    row: dict[str, object] = {
        "case_id": case["case_id"],
        "condition_id": condition_id,
        "ve_mps": case["ve_mps"],
        "vn_mps": case["vn_mps"],
        "speed_mps": case["speed_mps"],
        "status": "completed",
        "offline_scientific_baseline": True,
        "method": "JDL-3x4 reduced STAP",
        "raw_target_path": str(raw_paths["on"]),
        "raw_background_path": str(raw_paths["off"]),
        "raw_target_sha256": _sha256(raw_paths["on"]),
        "raw_background_sha256": _sha256(raw_paths["off"]),
        **metrics,
    }
    del result
    del data
    return row


def _production_rows_by(
    rows: Sequence[Mapping[str, object]],
    case_id: object,
    condition_id: str,
    split: str,
) -> Mapping[str, object]:
    for row in rows:
        if row.get("case_id") == case_id and row.get("condition_id") == condition_id and row.get("split") == split:
            return row
    raise RuntimeError(f"missing production row: {case_id} {condition_id} {split}")


def _offline_row_by(
    rows: Sequence[Mapping[str, object]], case_id: object, condition_id: str
) -> Mapping[str, object]:
    for row in rows:
        if row.get("case_id") == case_id and row.get("condition_id") == condition_id:
            return row
    raise RuntimeError(f"missing offline row: {case_id} {condition_id}")


def _recovery_rows(
    cases: Sequence[Mapping[str, object]],
    production_rows: Sequence[Mapping[str, object]],
    offline_rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    production_metrics = (
        ("csi_cancellation_db", "higher_is_better"),
        ("csi_coherence", "higher_is_better"),
        ("csi_rmse_rad", "lower_is_better"),
    )
    for case in cases:
        case_id = case["case_id"]
        current = _production_rows_by(production_rows, case_id, "B0_Current", "off")
        estimated = _production_rows_by(production_rows, case_id, "B1_Blind_Estimated_Current", "off")
        known = _production_rows_by(production_rows, case_id, "B1K_Known_Current", "off")
        for metric, direction in production_metrics:
            values = [
                _float_or_none(current.get(metric)),
                _float_or_none(known.get(metric)),
                _float_or_none(estimated.get(metric)),
            ]
            if all(value is not None for value in values):
                recovery = recovery_metrics(values[0], values[1], values[2], direction)  # type: ignore[arg-type]
            else:
                recovery = {
                    "recoverable_space_known_minus_current": None,
                    "actual_recovered_estimated_minus_current": None,
                    "recovery_ratio": None,
                }
            rows.append({
                "case_id": case_id,
                "ve_mps": case["ve_mps"],
                "vn_mps": case["vn_mps"],
                "comparison": "B1_vs_B0",
                "metric": metric,
                "metric_direction": direction,
                "current_condition": "B0_Current",
                "estimated_condition": "B1_Blind_Estimated_Current",
                "known_condition": "B1K_Known_Current",
                "current_value": values[0],
                "estimated_value": values[2],
                "known_value": values[1],
                **recovery,
            })
        current_offline = _offline_row_by(offline_rows, case_id, "B2_Uncalibrated_4ch_STAP")
        estimated_offline = _offline_row_by(offline_rows, case_id, "B3_Blind_4ch_STAP")
        known_offline = _offline_row_by(offline_rows, case_id, "B3K_Known_4ch_STAP")
        for metric, direction in (
            ("SCNR_improvement_dB", "higher_is_better"),
            ("background_residual_p95_power", "lower_is_better"),
            ("background_residual_p99_power", "lower_is_better"),
            ("Pd", "higher_is_better"),
            ("target_loss_dB", "higher_is_better"),
        ):
            values = [
                _float_or_none(current_offline.get(metric)),
                _float_or_none(known_offline.get(metric)),
                _float_or_none(estimated_offline.get(metric)),
            ]
            if all(value is not None for value in values):
                recovery = recovery_metrics(values[0], values[1], values[2], direction)  # type: ignore[arg-type]
            else:
                recovery = {
                    "recoverable_space_known_minus_current": None,
                    "actual_recovered_estimated_minus_current": None,
                    "recovery_ratio": None,
                }
            rows.append({
                "case_id": case_id,
                "ve_mps": case["ve_mps"],
                "vn_mps": case["vn_mps"],
                "comparison": "B3_vs_B2",
                "metric": metric,
                "metric_direction": direction,
                "current_condition": "B2_Uncalibrated_4ch_STAP",
                "estimated_condition": "B3_Blind_4ch_STAP",
                "known_condition": "B3K_Known_4ch_STAP",
                "current_value": values[0],
                "estimated_value": values[2],
                "known_value": values[1],
                **recovery,
            })
    return rows


def _provenance(command_line: Sequence[str], simulator: Path, core: Path) -> dict[str, object]:
    return {
        "working_directory": str(ROOT),
        "command": [str(item) for item in command_line],
        "source_commit_before_run": _git("rev-parse", "HEAD"),
        "worktree_status_before": _git("status", "--short", "--untracked-files=all"),
        "worktree_dirty_before": bool(_git("status", "--short", "--untracked-files=all")),
        "simulator": str(simulator.resolve()),
        "simulator_sha256": _sha256(simulator) if simulator.is_file() else None,
        "gmticore": str(core.resolve()),
        "gmticore_sha256": _sha256(core) if core.is_file() else None,
        "python": sys.version,
        "platform": platform.platform(),
        "gpu_probe": _probe_gpu(),
        "disk_probe": _probe_disk(),
    }


def _probe_gpu() -> dict[str, object]:
    completed = subprocess.run(
        ["nvidia-smi"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False
    )
    return {"returncode": completed.returncode, "output": completed.stdout}


def _probe_disk() -> dict[str, object]:
    completed = subprocess.run(
        ["df", "-h", str(ROOT), "/tmp"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False
    )
    return {"returncode": completed.returncode, "output": completed.stdout}


def run_end_to_end(
    output_root: Path,
    simulator: Path = SIMULATOR,
    core: Path = GMTI_CORE,
    velocities: Sequence[tuple[float, float]] = DEFAULT_VELOCITIES,
) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty end-to-end output: {output_root}")
    if not E2E_TEMPLATE.is_file() or not CALIBRATION_TEMPLATE.is_file():
        raise RuntimeError("end-to-end templates are missing")
    if not simulator.is_file() or not core.is_file():
        raise RuntimeError(f"missing executable(s): {simulator}, {core}")
    normalized_velocities = validate_moving_velocities(velocities)
    output_root.mkdir(parents=True, exist_ok=True)
    template = json.loads(E2E_TEMPLATE.read_text(encoding="utf-8"))
    calibration = _run_calibration(output_root, simulator, 404)
    estimated_delta = float(calibration["estimated_delta_m"])
    resolved = json.loads(Path(calibration["resolved_path"]).read_text(encoding="utf-8"))
    metadata = calibration["metadata"]
    reported_positions = calibration["reported_positions_m"]
    cases: list[dict[str, object]] = []
    for index, (ve_mps, vn_mps) in enumerate(normalized_velocities):
        case = _prepare_target_case(output_root, template, simulator, index, ve_mps, vn_mps)
        resolved_case = json.loads(Path(case["resolved"]).read_text(encoding="utf-8"))
        case["estimator_metadata"] = matrix.estimator_metadata(resolved_case)
        case["corrections"] = _prepare_corrections(
            case,
            case["estimator_metadata"],
            reported_positions,
            estimated_delta,
            BASELINE_DELTA_M,
        )
        cases.append(case)
    del resolved

    production_rows: list[dict[str, object]] = []
    offline_rows: list[dict[str, object]] = []
    for case in cases:
        corrections = case["corrections"]
        if not isinstance(corrections, Mapping):
            raise RuntimeError("correction paths are malformed")
        production_path_sets = {
            "B0_Current": corrections["original"],
            "B1_Blind_Estimated_Current": corrections["blind"],
            "B1K_Known_Current": corrections["known"],
        }
        for condition_id, path_set in production_path_sets.items():
            if not isinstance(path_set, Mapping):
                raise RuntimeError(f"malformed production path set: {condition_id}")
            production_rows.extend([
                _run_production(case, condition_id, "on", Path(path_set["on"]), Path(case["on_xml"]), output_root, core),
                _run_production(case, condition_id, "off", Path(path_set["off"]), Path(case["off_xml"]), output_root, core),
                _run_production(case, condition_id, "target_only", Path(path_set["target_only"]), Path(case["target_only_xml"]), output_root, core),
            ])
        offline_path_sets = {
            "B2_Uncalibrated_4ch_STAP": corrections["original"],
            "B3_Blind_4ch_STAP": corrections["blind"],
            "B3K_Known_4ch_STAP": corrections["known"],
        }
        for condition_id, path_set in offline_path_sets.items():
            if not isinstance(path_set, Mapping):
                raise RuntimeError(f"malformed offline path set: {condition_id}")
            offline_rows.append(_run_offline_stap(
                case,
                condition_id,
                {"on": Path(path_set["on"]), "off": Path(path_set["off"])},
                output_root,
            ))

    recovery_rows = _recovery_rows(cases, production_rows, offline_rows)
    _write_rows(output_root / "production_metrics.csv", production_rows)
    _write_rows(output_root / "offline_stap_metrics.csv", offline_rows)
    _write_rows(output_root / "recovery_metrics.csv", recovery_rows)
    transfer_rows = [
        row for row in production_rows
        if row.get("split") == "target_only"
    ]
    _write_rows(output_root / "target_only_transfer.csv", transfer_rows)
    target_off_rows = [
        row for row in production_rows
        if row.get("split") == "off"
    ]
    _write_rows(output_root / "target_off_false_cluster_metrics.csv", target_off_rows)
    all_production_ok = all(
        row.get("production_exit_code") == 0 and row.get("csi_manifest")
        for row in production_rows
    )
    all_offline_ok = all(row.get("status") == "completed" for row in offline_rows)
    p4_rows = [row for row in production_rows if row.get("split") in {"on", "target_only"}]
    p4_complete = all(row.get("p4_eval_status") == "completed" for row in p4_rows)
    manifest: dict[str, object] = {
        "schema": "unknown_system_error_true_geometry_end_to_end_pilot_v1",
        "status": "completed" if all_production_ok and all_offline_ok and p4_complete else "completed_with_missing_or_failed_evidence",
        "ai_training": False,
        "estimator_mode": "unknown_only_blind_online_separate_calibration_file",
        "operational_blind": True,
        "known_error_upper_bound_used_only_for_evaluation": True,
        "source": _provenance(sys.argv, simulator, core),
        "templates": {
            "end_to_end": str(E2E_TEMPLATE.resolve()),
            "end_to_end_sha256": _sha256(E2E_TEMPLATE),
            "calibration": str(CALIBRATION_TEMPLATE.resolve()),
            "calibration_sha256": _sha256(CALIBRATION_TEMPLATE),
        },
        "calibration": {
            "input_role": "separate_unknown_off_four_channel_IQ",
            "input_path": str(calibration["raw_path"]),
            "input_sha256": _sha256(Path(calibration["raw_path"])),
            "config_path": str(calibration["config_path"]),
            "resolved_path": str(calibration["resolved_path"]),
            "allowed_metadata_keys": sorted(metadata),
            "forbidden_inputs": [
                "ideal_off", "ideal_on", "truth", "known_error_parameters",
                "future metrics", "true channel positions", "target truth",
            ],
            "estimator_result": calibration["estimator"],
            "estimated_delta_m": estimated_delta,
            "outer_evaluation_truth_delta_m": BASELINE_DELTA_M,
            "range_start": calibration["range_start"],
            "range_end": calibration["range_end"],
        },
        "target_cases": [
            {
                "case_id": case["case_id"],
                "velocity_index": case["velocity_index"],
                "ve_mps": case["ve_mps"],
                "vn_mps": case["vn_mps"],
                "speed_mps": case["speed_mps"],
                "moving_target_required": True,
                "raw_files": {
                    correction: {
                        role: {
                            "path": str(path),
                            "bytes": path.stat().st_size,
                            "sha256": _sha256(path),
                        }
                        for role, path in paths.items()
                    }
                    for correction, paths in case["corrections"].items()
                },
                "truth": str(case["truth"]),
                "truth_sha256": _sha256(Path(case["truth"])),
            }
            for case in cases
        ],
        "condition_contract": condition_contract(),
        "target_transfer_conditions": target_transfer_conditions(),
        "comparison_metrics": {
            "production_background": "csi_cancellation_db, csi_coherence, csi_rmse_rad from active_support_unmasked on target-off C+N",
            "offline_reference": "SCNR improvement, residual P95/P99, Pd, target loss from JDL-3x4 reduced STAP",
            "recovery": "Known-Current and Estimated-Current with direction recorded per metric",
            "target_only": "production target-only P4 transfer rows, separate from C+N cancellation",
            "target_off": "production target-off CFAR hit/cluster counts and Pfa when valid test-cell denominator is emitted",
        },
        "counts": {
            "velocity_case_count": len(cases),
            "production_row_count": len(production_rows),
            "offline_row_count": len(offline_rows),
            "recovery_row_count": len(recovery_rows),
            "production_success": all_production_ok,
            "offline_success": all_offline_ok,
            "p4_evaluation_complete": p4_complete,
        },
        "artifacts": {
            "production_metrics": str((output_root / "production_metrics.csv").resolve()),
            "offline_stap_metrics": str((output_root / "offline_stap_metrics.csv").resolve()),
            "recovery_metrics": str((output_root / "recovery_metrics.csv").resolve()),
            "target_only_transfer": str((output_root / "target_only_transfer.csv").resolve()),
            "target_off_false_clusters": str((output_root / "target_off_false_cluster_metrics.csv").resolve()),
        },
        "limitations": [
            "B2/B3/B3K are the repository's offline JDL-3x4 conventional STAP scientific reference, not production CUDA four-channel STAP.",
            "The target scene uses one controlled beam and beam-center clutter; the independent multi-beam calibration is the identifiability evidence.",
            "No servo/beam-pointing or platform-velocity unknown parameter is estimated in this phase.",
            "TrackManager/PIPE causal target retention is not run by this pilot.",
            "Production Pfa is reported only when the log provides a valid CFAR test-cell denominator; hit/cluster counts are retained otherwise.",
        ],
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def _parse_velocities(values: Sequence[str]) -> list[tuple[float, float]]:
    parsed: list[tuple[float, float]] = []
    for value in values:
        pieces = value.split(",")
        if len(pieces) != 2:
            raise ValueError(f"velocity must be VE,VN: {value}")
        parsed.append((float(pieces[0]), float(pieces[1])))
    return validate_moving_velocities(parsed)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs/unknown_system_error_end_to_end_20260914",
    )
    parser.add_argument("--simulator", type=Path, default=SIMULATOR)
    parser.add_argument("--gmticore", type=Path, default=GMTI_CORE)
    parser.add_argument(
        "--velocities",
        nargs="+",
        default=[f"{ve},{vn}" for ve, vn in DEFAULT_VELOCITIES],
        help="moving target velocities as VE,VN pairs",
    )
    args = parser.parse_args(argv)
    velocities = _parse_velocities(args.velocities)
    manifest = run_end_to_end(
        args.output_root.resolve(),
        simulator=args.simulator.resolve(),
        core=args.gmticore.resolve(),
        velocities=velocities,
    )
    print(json.dumps({
        "status": manifest["status"],
        "output_root": str(args.output_root.resolve()),
        "estimated_delta_m": manifest["calibration"]["estimated_delta_m"],
        "counts": manifest["counts"],
    }, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
