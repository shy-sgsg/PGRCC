#!/usr/bin/env python3
"""Run a compact production TrackManager/PIPE continuous-target study.

The study deliberately keeps the production detector, association gates and
TrackManager in the loop.  Smoke mode uses three consecutive periods; the
formal default uses five.  Both use one physical beam while testing
confirmation, same-period association and protocol-output provenance.

The three branches share one simulated echo and one production processor.  The
blind branch must apply a delay estimated from an independent target-free raw
input, while the known branch applies the injected delay only as an upper-bound
reference.  A missing correction is a ``NOT_EVALUABLE`` result rather than a
silent fallback to Current.
"""

from __future__ import annotations

import argparse
import csv
import copy
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
import xml.etree.ElementTree as ET

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.estimate_channel_delay import (
    estimate_from_raw_time_arrays,
    rewrite_float32_protocol_delay,
)
from scripts.analyze_four_channel_observables import iter_raw_packets
from scripts.audit_two_channel_error_observability import fuse_protocol_channels_to_f1_f2

TEMPLATE = ROOT / "simulator/scenarios/beam50_one_targets_continuous.run.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
PIPE = ROOT / "build/GMTI_pipe_core"
SHM_INTEGRATION = ROOT / "scripts/run_shm_phase1_integration.sh"

PERIOD_COUNT = 5
PACKET_HEADER_BYTES = 256
SAMPLES_PER_IQ = 2
BYTES_PER_SAMPLE = 4
DELAY_ERROR_NS = 4.0
MAX_POSITION_MATCH_M = 1000.0
MAX_RANGE_MATCH_M = 150.0


def branch_contract() -> dict[str, object]:
    """Return the immutable comparison contract used by tests and manifests."""

    return {
        "branches": ["Current", "blind_calibrated", "known_error_calibrated"],
        "shared_rules": {
            "same_simulated_echo": True,
            "same_production_pipe": True,
            "same_cfar": True,
            "same_track_manager": True,
            "same_confirmation_rule": True,
            "same_protocol_eligibility_gate": True,
        },
        "ai_training": False,
        "router_enabled": False,
        "scientific_input": "4ch_protocol_iq_fused_to_f1_f2",
        "protocol_channel_count": 4,
        "fusion_pairs": [[1, 3], [2, 4]],
        "blind_calibrated_status": "estimated_correction_required",
        "known_error_calibrated_status": "known_error_correction_upper_bound",
        "correction_requirements": {
            "Current": {
                "correction_applied": False,
                "correction_source": "none",
            },
            "blind_calibrated": {
                "correction_applied": True,
                "correction_source": "target_free_phase_vs_frequency",
                "truth_used_in_estimator": False,
            },
            "known_error_calibrated": {
                "correction_applied": True,
                "correction_source": "truth_evaluation_only",
                "truth_used_in_estimator": False,
                "truth_used_to_apply_correction": True,
                "evaluation_only": True,
            },
        },
    }


def branch_evaluation_gate(
    branch: str,
    *,
    correction_applied: bool,
    correction_source: str,
    delay_estimate_ns: float | None,
    delay_reference_ns: float | None,
) -> dict[str, object]:
    """Validate that a TrackManager correction branch is truly evaluable.

    ``Current`` is the uncorrected baseline.  Both comparison branches require
    a finite applied value and a non-empty provenance string.  The known branch
    additionally requires the truth reference used only to evaluate its upper
    bound.  This gate is intentionally metadata-based: it never decides a
    scientific success threshold or turns a missing input into a no-op run.
    """

    if branch not in branch_contract()["branches"]:  # type: ignore[operator]
        raise ValueError(f"unknown TrackManager branch: {branch}")
    estimate = None if delay_estimate_ns is None else float(delay_estimate_ns)
    reference = None if delay_reference_ns is None else float(delay_reference_ns)
    finite_estimate = estimate is not None and math.isfinite(estimate)
    finite_reference = reference is not None and math.isfinite(reference)
    result: dict[str, object] = {
        "branch": branch,
        "correction_applied": bool(correction_applied),
        "correction_source": str(correction_source),
        "delay_estimate_ns": estimate,
        "delay_reference_ns": reference,
        "delay_residual_ns": (
            estimate - reference if finite_estimate and finite_reference else None
        ),
    }
    if branch == "Current":
        if correction_applied or str(correction_source).strip() != "none":
            result.update({
                "status": "NOT_EVALUABLE",
                "reason": "Current must remain uncorrected with correction_source=none",
            })
            return result
        result.update({"status": "evaluable", "reason": "uncorrected_baseline"})
        return result
    if not correction_applied:
        result.update({
            "status": "NOT_EVALUABLE",
            "reason": "correction_applied=false; comparison branch did not modify input",
        })
        return result
    if str(correction_source).strip() in {"", "none"}:
        result.update({
            "status": "NOT_EVALUABLE",
            "reason": "correction_source is empty",
        })
        return result
    if not finite_estimate:
        result.update({
            "status": "NOT_EVALUABLE",
            "reason": "finite applied delay estimate is missing",
        })
        return result
    if branch == "known_error_calibrated" and not finite_reference:
        result.update({
            "status": "NOT_EVALUABLE",
            "reason": "known upper-bound branch is missing its truth reference",
        })
        return result
    result.update({"status": "evaluable", "reason": "correction_metadata_verified"})
    return result


def _target_velocity_components(speed_mps: float) -> tuple[float, float]:
    if not math.isfinite(float(speed_mps)) or float(speed_mps) <= 0.0:
        raise ValueError("target velocity must be finite and positive")
    return -0.8944271909999159 * float(speed_mps), -0.4472135954999579 * float(speed_mps)


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _write_rows(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in materialized:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        if not fields:
            return
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(_json_safe(materialized))


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _number(row: Mapping[str, object], key: str, default: float = math.nan) -> float:
    try:
        value = float(row.get(key, default))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _integer(row: Mapping[str, object], key: str, default: int = -1) -> int:
    value = _number(row, key, float(default))
    return int(value) if math.isfinite(value) else default


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _command_text(command: Iterable[object]) -> str:
    return " ".join(subprocess.list2cmdline([str(item)]) for item in command)


def _run_logged(
    command: list[object],
    log_path: Path,
    *,
    cwd: Path = ROOT,
    env: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
) -> tuple[int, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as stream:
        stream.write("$ " + _command_text(command) + "\n")
        stream.flush()
        try:
            result = subprocess.run(
                [str(item) for item in command],
                cwd=cwd,
                env=dict(env) if env is not None else None,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout_s,
            )
            returncode = result.returncode
        except subprocess.TimeoutExpired:
            stream.write("\n[timeout_expired]=true\n")
            returncode = 124
    elapsed = time.monotonic() - started
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(f"\n[exit_code]={returncode}\n[elapsed_sec]={elapsed:.3f}\n")
    return returncode, elapsed


def _git_identity() -> dict[str, object]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return {"git_commit": commit, "git_dirty": bool(status.strip())}


def _gpu_status() -> dict[str, object]:
    command = [
        "nvidia-smi",
        "--query-gpu=name,driver_version,pstate,clocks.sm,clocks.mem,power.draw,power.limit,temperature.gpu,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    return {
        "command": _command_text(command),
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _disk_status(path: Path) -> dict[str, object]:
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "used_bytes": usage.total - usage.free,
    }


def _latest_dir(root: Path, prefix: str) -> Path | None:
    values = sorted(
        (path for path in root.glob(prefix + "*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
    )
    return values[-1] if values else None


def _period_id_from_result(value: object) -> int:
    match = re.search(r"(\d+)$", str(value))
    if match is None:
        return -1
    # Production result IDs are one-based while Stage2 truth periods are zero-based.
    return max(0, int(match.group(1)) - 1)


def _result_number(path: Path) -> int:
    match = re.search(r"GMTI(\d+)", path.name)
    return int(match.group(1)) if match else -1


def _wrap180(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def _angle_error_deg(estimated: float, truth: float) -> float:
    if not math.isfinite(estimated) or not math.isfinite(truth):
        return math.nan
    # Bearing is a line-of-sight angle; retain the evaluator's 180-degree
    # periodicity so a wrap does not become a false 360-degree error.
    return abs((estimated - truth + 90.0) % 180.0 - 90.0)


def _rmse(values: Iterable[float]) -> float | None:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        return None
    return math.sqrt(sum(value * value for value in finite) / len(finite))


def _protocol_layout_from_scenario(scenario: Mapping[str, object]) -> dict[str, object]:
    """Derive the packet/fusion contract from the generated scenario metadata."""

    waveform = scenario.get("waveform")
    scan = scenario.get("scan")
    random_cfg = scenario.get("random")
    if not isinstance(waveform, Mapping) or not isinstance(scan, Mapping) or not isinstance(random_cfg, Mapping):
        raise ValueError("scenario waveform, scan and random metadata are required")
    pulse_len = int(waveform.get("pulse_len", 0))
    pulse_count = int(waveform.get("pulse_num", 0))
    channel_count = int(waveform.get("new_protocol_channel_count", 0))
    fs_hz = float(waveform.get("fs_mhz", 0.0)) * 1.0e6
    iq_data_type = str(waveform.get("iq_data_type", "float32")).lower()
    beam_id = int(random_cfg.get("beam_start", 0))
    beam_index_base = int(scan.get("beam_index_base", 1))
    scan_min_deg = float(scan.get("scan_min_deg", math.nan))
    scan_step_deg = float(scan.get("scan_step_deg", math.nan))
    read_channel_1 = int(waveform.get("new_protocol_read_channel_1", 0))
    read_channel_2 = int(waveform.get("new_protocol_read_channel_2", 0))
    if pulse_len <= 0 or pulse_count <= 0 or channel_count != 4:
        raise ValueError("TrackManager Phase-I runner requires positive pulse metadata and four protocol channels")
    if iq_data_type not in {"float32", "float", "f32"}:
        raise ValueError("TrackManager delay correction requires float32 protocol IQ")
    if not math.isfinite(fs_hz) or fs_hz <= 0.0:
        raise ValueError("scenario waveform fs_mhz must be finite and positive")
    if beam_id <= 0 or read_channel_1 <= 0 or read_channel_2 <= 0:
        raise ValueError("scenario beam and read-channel metadata must be positive")
    if not math.isfinite(scan_min_deg) or not math.isfinite(scan_step_deg):
        raise ValueError("scenario scan angle metadata must be finite")
    packet_bytes = (
        PACKET_HEADER_BYTES
        + pulse_len * channel_count * SAMPLES_PER_IQ * BYTES_PER_SAMPLE
    )
    return {
        "header_bytes": PACKET_HEADER_BYTES,
        "pulse_len": pulse_len,
        "pulse_count": pulse_count,
        "channel_count": channel_count,
        "iq_data_type": "float32",
        "fs_hz": fs_hz,
        "packet_bytes": packet_bytes,
        "beam_id": beam_id,
        "beam_index_base": beam_index_base,
        "expected_theta_deg": scan_min_deg + (beam_id - beam_index_base) * scan_step_deg,
        "scan_min_deg": scan_min_deg,
        "scan_step_deg": scan_step_deg,
        "read_channel_1": read_channel_1,
        "read_channel_2": read_channel_2,
        "correction_channel_indices": [read_channel_2 - 1],
        "fusion_pairs": [[1, 3], [2, 4]],
        "enable_four_channel_fusion": True,
        "four_channel_phase_compensation_enable": True,
        "fusion_channel_3": 3,
        "fusion_channel_4": 4,
    }


def _build_scenario(
    case_root: Path,
    seed: int,
    period_count: int,
    target_velocity_mps: float,
    target_snr_db: float,
    delay_error_ns: float,
) -> tuple[Path, dict[str, object]]:
    payload = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"scenario template is not an object: {TEMPLATE}")
    scenario = copy.deepcopy(payload)
    simulation_root = case_root / "simulation"
    random_cfg = scenario.setdefault("random", {})
    if not isinstance(random_cfg, dict):
        raise RuntimeError("continuous-target template random config must be an object")
    targets = scenario.get("targets")
    if not isinstance(targets, list) or not targets or not isinstance(targets[0], dict):
        raise RuntimeError("continuous-target template must contain one target object")
    target = targets[0]
    init = target.setdefault("init", {})
    if not isinstance(init, dict):
        raise RuntimeError("continuous-target target init must be an object")
    beam_id = int(random_cfg.get("beam_start", init.get("beam_id", 1)))
    if beam_id <= 0:
        raise RuntimeError("continuous-target beam_start must be positive")
    scenario["case_id"] = (
        f"track_manager_continuous_beam{beam_id}_seed_{seed}_"
        f"v{target_velocity_mps:.3f}_snr{target_snr_db:.1f}"
    )
    scenario["output_dir"] = str(simulation_root)
    scenario["truth_output"] = True
    random_cfg = scenario.setdefault("random", {})
    random_cfg.update({
        "period_start": 0,
        "period_count": period_count,
        "beam_start": beam_id,
        "beam_count": 1,
        "random_seed": seed,
        "four_channel_phase_center_mode": "physical",
    })
    waveform = scenario.get("waveform")
    if not isinstance(waveform, dict):
        raise RuntimeError("continuous-target template waveform must be an object")
    waveform.update({
        "iq_data_type": "float32",
        "new_protocol_channel_count": 4,
        "new_protocol_read_channel_1": 1,
        "new_protocol_read_channel_2": 2,
    })
    scenario["channel_geometry"] = {
        "mode": "reported_channel_positions",
        "channel_1": {"name": "left_upper", "x_m": -0.085, "y_m": 0.0, "z_m": 0.085},
        "channel_2": {"name": "right_upper", "x_m": 0.085, "y_m": 0.0, "z_m": 0.085},
        "channel_3": {"name": "left_lower", "x_m": -0.085, "y_m": 0.0, "z_m": -0.085},
        "channel_4": {"name": "right_lower", "x_m": 0.085, "y_m": 0.0, "z_m": -0.085},
    }
    simulation_cfg = scenario.setdefault("simulation", {})
    simulation_cfg["beam_pattern_mode"] = "hard_gate"
    scene = scenario.setdefault("scene", {})
    area = scene.setdefault("area_clutter", {})
    area.update({
        "enabled": True,
        "model": "continuous_texture",
        "mean_power": 0.02,
        "texture_sigma": 0.25,
        "spatial_cell_m": 30.0,
        "temporal_correlation_rho": 0.98,
    })
    noise = scene.setdefault("thermal_noise", {})
    noise.update({"enabled": True, "noise_power": 0.001})
    target.update({"target_id": "TRACK_CONTINUOUS_TARGET", "enabled": True})
    init.update({
        "type": str(init.get("type", "beam_bin_azimuth_offset")),
        "beam_id": beam_id,
        "azimuth_offset_deg": float(init.get("azimuth_offset_deg", 0.0)),
    })
    target.setdefault("motion", {}).update({
        "type": "enu_velocity",
        "ve_mps": _target_velocity_components(target_velocity_mps)[0],
        "vn_mps": _target_velocity_components(target_velocity_mps)[1],
    })
    target.setdefault("amplitude", {}).update({"type": "snr_db", "snr_db": target_snr_db})
    target.setdefault("visibility", {}).update({
        "type": "hard_gate",
        "single_beam_only": True,
        "visible_beam_span": 0,
    })
    impairments = scenario.setdefault("channel_impairments", {})
    if not isinstance(impairments, dict):
        raise RuntimeError("continuous-target template channel_impairments must be an object")
    impairments.update({
        "enabled": abs(float(delay_error_ns)) > 0.0,
        "channel_time_delay_ns": float(delay_error_ns),
        "channel_fixed_phase_mismatch_deg": 0.0,
        "per_pulse_phase_drift_deg": 0.0,
        "channel_range_shift_samples": 0.0,
    })
    case_root.mkdir(parents=True, exist_ok=True)
    scenario_path = case_root / "scenario.run.json"
    _write_json(scenario_path, scenario)
    return scenario_path, scenario


def _build_calibration_scenario(
    case_root: Path,
    scenario: Mapping[str, object],
    seed: int,
) -> tuple[Path, dict[str, object]]:
    """Clone the target-plus scenario as an independent target-free calibration run."""

    calibration = copy.deepcopy(dict(scenario))
    calibration["case_id"] = f"{scenario.get('case_id', 'track_manager')}_target_free_calibration"
    calibration_root = case_root / "calibration" / "simulation"
    calibration["output_dir"] = str(calibration_root)
    random_cfg = calibration.setdefault("random", {})
    if not isinstance(random_cfg, dict):
        raise RuntimeError("calibration random config must be an object")
    random_cfg["random_seed"] = int(seed)
    targets = calibration.get("targets")
    if not isinstance(targets, list) or not targets:
        raise RuntimeError("calibration scenario must contain the target list")
    for target in targets:
        if isinstance(target, dict):
            target["enabled"] = False
    calibration["calibration_input_role"] = "target_free_C_plus_N_only"
    path = case_root / "calibration" / "scenario.target_free.json"
    _write_json(path, calibration)
    return path, calibration


def _period_files(simulation_root: Path, period_count: int) -> list[Path]:
    manifest = simulation_root / "data/period_files.csv"
    rows = _read_csv(manifest)
    paths: list[Path] = []
    for index in range(period_count):
        expected_name = f"stage2_statistical_newprotocol_period_{index:04d}.bin"
        row = next((item for item in rows if Path(item.get("file", "")).name == expected_name), None)
        candidate = simulation_root / "data" / expected_name
        if row is not None:
            raw = Path(row.get("file", ""))
            candidate = raw if raw.is_absolute() else simulation_root / raw
        if not candidate.is_file():
            raise RuntimeError(f"missing generated period input: {candidate}")
        paths.append(candidate.resolve())
    return paths


def _audit_protocol_input(
    path: Path,
    expected_packets: int,
    expected_theta_deg: float,
    layout: Mapping[str, object],
) -> dict[str, object]:
    errors: list[str] = []
    header_bytes = int(layout["header_bytes"])
    packet_bytes_expected = int(layout["packet_bytes"])
    expected_bytes = expected_packets * packet_bytes_expected
    actual_bytes = path.stat().st_size if path.is_file() else -1
    if actual_bytes != expected_bytes:
        errors.append(f"bytes={actual_bytes} expected={expected_bytes}")
    counters: list[int] = []
    theta_values: list[float] = []
    if path.is_file():
        with path.open("rb") as stream:
            for index in range(expected_packets):
                header = stream.read(header_bytes)
                if len(header) != header_bytes:
                    errors.append(f"short_header_packet_{index}={len(header)}")
                    break
                magic_head = int.from_bytes(header[0:8], "little")
                version = header[8]
                packet_bytes = int.from_bytes(header[9:13], "little")
                counter = int.from_bytes(header[20:24], "little")
                theta_x100 = int.from_bytes(header[218:220], "little", signed=True)
                magic_tail = int.from_bytes(header[248:256], "little")
                counters.append(counter)
                theta_values.append(theta_x100 / 100.0)
                for passed, label in (
                    (magic_head == 0x5A5A5A5A5A5A5A5A, "magic_head"),
                    (version == 9, "version"),
                    (packet_bytes == packet_bytes_expected, "packet_bytes"),
                    (counter == index, "counter"),
                    (theta_x100 == round(expected_theta_deg * 100.0), "theta"),
                    (magic_tail == 0x5B5B5B5B5B5B5B5B, "magic_tail"),
                ):
                    if not passed and len(errors) < 20:
                        errors.append(f"packet_{index}_{label}")
                stream.seek(packet_bytes_expected - header_bytes, os.SEEK_CUR)
    contiguous = counters == list(range(expected_packets))
    if not contiguous:
        errors.append("prt_counters_not_contiguous")
    return {
        "status": "passed" if not errors else "failed",
        "path": str(path),
        "bytes": actual_bytes,
        "expected_bytes": expected_bytes,
        "packet_bytes": packet_bytes_expected,
        "pulse_len": int(layout["pulse_len"]),
        "pulse_count_per_period": int(layout["pulse_count"]),
        "channel_count": int(layout["channel_count"]),
        "fs_hz": float(layout["fs_hz"]),
        "expected_theta_deg": expected_theta_deg,
        "checked_packets": len(counters),
        "counter_first": counters[0] if counters else None,
        "counter_last": counters[-1] if counters else None,
        "theta_values_deg": sorted(set(theta_values)),
        "error_count": len(errors),
        "errors": errors,
    }


def _audit_runtime_xml_layout(
    source_xml: Path,
    expected_layout: Mapping[str, object],
) -> dict[str, object]:
    """Verify that the generated production XML retained the scenario layout."""

    errors: list[str] = []
    values: dict[str, object] = {}
    try:
        root = ET.parse(source_xml).getroot()
        for key in (
            "pulse_len",
            "new_protocol_channel_count",
            "new_protocol_read_channel_1",
            "new_protocol_read_channel_2",
            "iq_data_type",
            "enable_four_channel_fusion",
            "four_channel_phase_compensation_enable",
            "four_channel_fusion_channel_3",
            "four_channel_fusion_channel_4",
        ):
            element = next((item for item in root.iter() if item.tag == key), None)
            if element is None or element.text is None:
                errors.append(f"missing_xml_{key}")
                continue
            values[key] = element.text.strip()
        fs_element = next((item for item in root.iter() if item.tag == "fs"), None)
        if fs_element is None or fs_element.text is None:
            errors.append("missing_xml_fs")
        else:
            values["fs_hz"] = float(fs_element.text.strip()) * 1.0e6
    except (ET.ParseError, OSError, ValueError) as exc:
        errors.append(f"xml_parse_error:{exc}")
    comparisons = {
        "pulse_len": int(expected_layout["pulse_len"]),
        "new_protocol_channel_count": int(expected_layout["channel_count"]),
        "new_protocol_read_channel_1": int(expected_layout["read_channel_1"]),
        "new_protocol_read_channel_2": int(expected_layout["read_channel_2"]),
        "iq_data_type": str(expected_layout["iq_data_type"]),
        "enable_four_channel_fusion": bool(expected_layout["enable_four_channel_fusion"]),
        "four_channel_phase_compensation_enable": bool(
            expected_layout["four_channel_phase_compensation_enable"]
        ),
        "four_channel_fusion_channel_3": int(expected_layout["fusion_channel_3"]),
        "four_channel_fusion_channel_4": int(expected_layout["fusion_channel_4"]),
        "fs_hz": float(expected_layout["fs_hz"]),
    }
    for key, expected in comparisons.items():
        actual = values.get(key)
        if key == "fs_hz":
            matched = actual is not None and math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1.0)
        elif key == "iq_data_type":
            matched = str(actual).lower() == str(expected).lower()
        elif isinstance(expected, bool):
            actual_text = str(actual).strip().lower()
            actual_bool = actual_text in {"1", "true", "yes", "on"}
            matched = actual is not None and actual_bool == expected
        else:
            try:
                matched = int(float(actual)) == int(expected)
            except (TypeError, ValueError):
                matched = False
        if not matched:
            errors.append(f"{key}={actual!r} expected={expected!r}")
    return {
        "status": "passed" if not errors else "failed",
        "path": str(source_xml),
        "values": values,
        "expected": dict(comparisons),
        "errors": errors,
    }


def _concat_inputs(period_paths: list[Path], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        for source in period_paths:
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, output, length=8 * 1024 * 1024)
    return destination


def _load_f1_f2_from_raw(
    path: Path,
    layout: Mapping[str, object],
) -> tuple[np.ndarray, np.ndarray]:
    """Read four-channel packets and fuse them before delay estimation."""

    first_rows: list[np.ndarray] = []
    second_rows: list[np.ndarray] = []
    for packet in iter_raw_packets(
        path,
        int(layout["pulse_len"]),
        int(layout["channel_count"]),
        str(layout["iq_data_type"]),
    ):
        channels = np.asarray(packet["channels"])
        first, second = fuse_protocol_channels_to_f1_f2(channels)
        first_rows.append(np.asarray(first, dtype=np.complex128))
        second_rows.append(np.asarray(second, dtype=np.complex128))
    if not first_rows:
        raise ValueError(f"protocol input contains no packets: {path}")
    return np.stack(first_rows), np.stack(second_rows)


def _estimate_delay_from_calibration(
    calibration_input: Path,
    layout: Mapping[str, object],
) -> dict[str, object]:
    """Estimate delay from target-free four-channel IQ after F1/F2 fusion."""

    channel1, channel2 = _load_f1_f2_from_raw(calibration_input, layout)
    result = estimate_from_raw_time_arrays(channel1, channel2, float(layout["fs_hz"]))
    candidates = (
        "D3_Huber_weighted_LS",
        "D2_weighted_LS",
        "D1_ordinary_LS",
    )
    selected_name = None
    selected = None
    for name in candidates:
        candidate = result["aggregate"].get(name)
        if isinstance(candidate, Mapping):
            value = candidate.get("delta_tau_ns")
            if value is not None and math.isfinite(float(value)):
                selected_name = name
                selected = candidate
                break
    if selected_name is None or selected is None:
        raise RuntimeError("target-free delay estimator produced no finite method")
    return {
        "status": "estimated",
        "method": selected_name,
        "delta_tau_ns": float(selected["delta_tau_ns"]),
        "input_path": str(calibration_input),
        "input_role": "target_free_C_plus_N_only",
        "scientific_input": "F1=(C1+C3)/2, F2=(C2+C4)/2",
        "protocol_channel_count": int(layout["channel_count"]),
        "correction_channel_indices": list(layout["correction_channel_indices"]),
        "truth_used_in_estimator": False,
        "all_methods": result["aggregate"],
    }


def _prepare_corrected_inputs(
    case_root: Path,
    branch: str,
    period_paths: list[Path],
    delay_ns: float,
    source: str,
    layout: Mapping[str, object],
) -> dict[str, object]:
    """Rewrite each period with a declared delay correction and audit it."""

    correction_root = case_root / "prepared_inputs" / branch
    correction_root.mkdir(parents=True, exist_ok=True)
    corrected_periods: list[Path] = []
    audits: list[dict[str, object]] = []
    for index, period_path in enumerate(period_paths):
        destination = correction_root / f"period_{index:04d}.bin"
        audit = rewrite_float32_protocol_delay(
            period_path,
            destination,
            pulse_len=int(layout["pulse_len"]),
            channel_count=int(layout["channel_count"]),
            fs_hz=float(layout["fs_hz"]),
            delta_tau_sec=float(delay_ns) * 1.0e-9,
            channel_indices=tuple(int(index) for index in layout["correction_channel_indices"]),
        )
        audits.append(audit)
        corrected_periods.append(destination)
    merged = _concat_inputs(corrected_periods, correction_root / "corrected_input.bin")
    packets = sum(int(item["packets_rewritten"]) for item in audits)
    if packets <= 0 or not merged.is_file() or merged.stat().st_size <= 0:
        raise RuntimeError(f"correction produced no input for branch {branch}")
    return {
        "branch": branch,
        "period_paths": corrected_periods,
        "merged_input": merged,
        "correction_applied": True,
        "correction_source": source,
        "applied_delay_ns": float(delay_ns),
        "scientific_input": "4ch_protocol_iq_fused_to_f1_f2",
        "protocol_channel_count": int(layout["channel_count"]),
        "correction_channel_indices": list(layout["correction_channel_indices"]),
        "correction_audit": audits,
        "truth_used_in_estimator": False,
        "truth_used_to_apply_correction": source == "truth_evaluation_only",
    }


def _configure_xml(
    source_xml: Path,
    destination: Path,
    result_dir: Path,
    track_debug_dir: Path,
    layout: Mapping[str, object],
    xml_overrides: Mapping[str, object] | None = None,
) -> tuple[int, str]:
    overrides: dict[str, object] = {
        "result_add": result_dir,
        "track_debug_dir": track_debug_dir,
        "new_protocol_file_first_beam": int(layout["beam_id"]),
        "new_protocol_file_scan_beam_count": 1,
        "new_protocol_file_period_index": 0,
        "shm_scan_beam_count": 1,
        "shm_prt_counter_phase": 0,
        "wavepos_st": int(layout["beam_id"]),
        "wavepos_ed": int(layout["beam_id"]),
        "wavepos_skip": 1,
        "min_points": 3,
        "pf": "1e-6",
        "cfar_type": "GO",
        "cfar_doppler_circular": "true",
        "dynamic_cfar_enable": "false",
        "csi_detection_band_mode": "union",
        "track_output_state_source": "measurement",
        "enable_four_channel_fusion": "true",
        "four_channel_phase_compensation_enable": "true",
        "four_channel_fusion_channel_3": int(layout["fusion_channel_3"]),
        "four_channel_fusion_channel_4": int(layout["fusion_channel_4"]),
        "track_idx_window": 3,
        "track_truth_threshold": 2,
        "track_confirm_window": 3,
        "track_confirm_hits": 2,
        "track_max_missed": 2,
        "track_tentative_max_missed": 1,
        "track_gate_m": 300,
        "track_v_max": 50,
        "track_assignment_mode": 0,
        "track_debug_dump": "true",
        "track_debug_dump_level": 1,
        "track_debug_level": 1,
        "track_debug_frames": 0,
        "detection_results_csv_dump": "true",
        "csi_metrics_enable": "false",
        "csi_metrics_dump_power_maps": "false",
        "dbs_mosaic_use_gpu": "false",
        "wavepos_parallel": 0,
    }
    # Callers may select a research-only branch, but the shared production
    # defaults above remain authoritative for every downstream setting.
    if xml_overrides:
        overrides.update(dict(xml_overrides))
    command: list[object] = [
        sys.executable,
        ROOT / "scripts/configure_gmti_xml.py",
        "--input",
        source_xml,
        "--output",
        destination,
    ]
    for key, value in overrides.items():
        command.extend(["--set", f"{key}={value}"])
    log_path = destination.with_suffix(".configure.log")
    returncode, _ = _run_logged(command, log_path)
    return returncode, str(log_path)


def _find_debug_dir(result_dir: Path, configured_debug_dir: Path) -> Path | None:
    candidates: list[Path] = []
    branch_root = result_dir.parent
    for root in (
        configured_debug_dir,
        result_dir / "track_debug",
        result_dir / "track_debug_runs",
        branch_root / "track_debug_runs",
    ):
        if not root.exists():
            continue
        if (root / "track_frames.csv").is_file():
            candidates.append(root)
        candidates.extend(path.parent for path in root.rglob("track_frames.csv"))
    if not candidates:
        return None
    return sorted(set(candidates), key=lambda path: path.stat().st_mtime)[-1]


def _find_pipe_run(log_path: Path, runs_root: Path) -> Path | None:
    if log_path.is_file():
        text = log_path.read_text(encoding="utf-8", errors="replace")
        matches = re.findall(r"\[INTEGRATION\] run_dir=(.+)", text)
        if matches:
            path = Path(matches[-1].strip())
            if path.is_dir():
                return path
    return _latest_dir(runs_root, "run_")


def _parse_key_value_tokens(line: str) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, raw in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)", line):
        if raw.lower() in {"true", "false"}:
            values[key] = raw.lower() == "true"
            continue
        try:
            values[key] = int(raw, 0)
            continue
        except ValueError:
            pass
        try:
            values[key] = float(raw)
            continue
        except ValueError:
            values[key] = raw
    return values


def _parse_pipe_runtime_metrics(
    pipe_run_dir: Path | None,
    integration_log: Path,
) -> dict[str, object]:
    """Summarize the production SHM/PIPE evidence without replacing its logs."""

    if pipe_run_dir is None:
        return {"status": "not_applicable", "input_mode": "local"}
    logs_dir = pipe_run_dir / "logs"
    key_metrics_path = logs_dir / "key_metrics.log"
    client_path = logs_dir / "pipe_client.log"
    if not key_metrics_path.is_file():
        return {
            "status": "missing",
            "input_mode": "shm",
            "key_metrics_log": str(key_metrics_path),
        }
    key_text = key_metrics_path.read_text(encoding="utf-8", errors="replace")
    metric_rows = [
        _parse_key_value_tokens(line)
        for line in re.findall(r"^\[SHM\]\[METRICS\]\s+(.+)$", key_text, re.MULTILINE)
    ]
    result_rows = [
        _parse_key_value_tokens(line)
        for line in re.findall(r"^\[RESULT\]\[METRICS\]\s+(.+)$", key_text, re.MULTILINE)
    ]
    client_rows: list[dict[str, object]] = []
    if client_path.is_file():
        client_text = client_path.read_text(encoding="utf-8", errors="replace")
        client_rows = [
            _parse_key_value_tokens(line)
            for line in re.findall(r"^\[PIPE-CLIENT\]\s+(.+)$", client_text, re.MULTILINE)
        ]
        client_rows = [row for row in client_rows if "result_index" in row]
    last = metric_rows[-1] if metric_rows else {}
    successful_cycles = sum(bool(row.get("success", False)) for row in metric_rows)
    successful_results = sum(bool(row.get("success", False)) for row in result_rows)
    integration_text = integration_log.read_text(encoding="utf-8", errors="replace")
    integration_pass = "[INTEGRATION] PASS" in integration_text
    status = "passed" if (
        integration_pass
        and metric_rows
        and successful_cycles == len(metric_rows)
        and result_rows
        and successful_results == len(result_rows)
        and len(client_rows) == len(result_rows)
    ) else "failed"
    return {
        "status": status,
        "input_mode": "shm",
        "key_metrics_log": str(key_metrics_path),
        "pipe_client_log": str(client_path),
        "cycle_metric_count": len(metric_rows),
        "result_metric_count": len(result_rows),
        "pipe_client_result_count": len(client_rows),
        "successful_cycle_count": successful_cycles,
        "successful_result_count": successful_results,
        "completed_cycles": last.get("completed_cycles"),
        "dropped_incomplete": last.get("dropped_incomplete"),
        "dropped_backpressure": last.get("dropped_backpressure"),
        "ring_overrun": last.get("ring_overrun"),
        "gaps": last.get("gaps"),
        "duplicates": last.get("duplicates"),
        "resync": last.get("resync"),
        "valid_prt": last.get("valid_prt"),
        "invalid_prt": last.get("invalid_prt"),
        "cycle_metrics": metric_rows,
        "result_metrics": result_rows,
        "pipe_client_results": client_rows,
    }


def _parse_cfar_summaries(
    log_paths: Iterable[Path],
) -> dict[str, object]:
    """Read production CFAR hit/cluster summaries without reimplementing CFAR."""

    summaries: list[dict[str, int]] = []
    pattern = re.compile(
        r"\[CFAR\]\[SUMMARY\].*?hit_cells=(\d+).*?clusters=(\d+)"
        r".*?selected=(\d+)"
    )
    seen: set[Path] = set()
    for path in log_paths:
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        content = path.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(content):
            summaries.append({
                "hit_cells": int(match.group(1)),
                "clusters": int(match.group(2)),
                "selected": int(match.group(3)),
            })
    return {
        "status": "passed" if summaries else "missing",
        "summary_count": len(summaries),
        "hit_cells": sum(row["hit_cells"] for row in summaries),
        "clusters": sum(row["clusters"] for row in summaries),
        "selected": sum(row["selected"] for row in summaries),
        "summaries": summaries,
    }


def _truth_by_period(simulation_root: Path, period_count: int) -> dict[int, dict[str, str]]:
    rows = _read_csv(simulation_root / "truth/truth_targets_by_beam.csv")
    visible = [row for row in rows if row.get("visible", "0") == "1"]
    result: dict[int, dict[str, str]] = {}
    for row in visible:
        period = _integer(row, "period_id", -1)
        if 0 <= period < period_count:
            if period in result:
                raise RuntimeError(f"more than one visible target row in period {period}")
            result[period] = row
    return result


def _load_detection_snapshots(result_dir: Path) -> tuple[dict[int, list[dict[str, str]]], list[dict[str, object]]]:
    by_period: dict[int, list[dict[str, str]]] = {}
    cycle_rows: list[dict[str, object]] = []
    paths = sorted(result_dir.glob("detection_results_GMTI*.csv"), key=_result_number)
    for index, path in enumerate(paths):
        rows = _read_csv(path)
        period = index
        by_period[period] = rows
        cycle_rows.append({
            "result_id": _result_number(path),
            "period_id": period,
            "detection_count": len(rows),
            "path": str(path),
        })
    return by_period, cycle_rows


def _evaluate_branch(
    branch: str,
    result_dir: Path,
    debug_dir: Path | None,
    truth: Mapping[int, Mapping[str, str]],
    expected_periods: int,
    audit: Mapping[str, object],
    cfar_summary: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    if cfar_summary is None:
        cfar_summary = {"status": "missing", "selected": 0, "hit_cells": 0, "clusters": 0}
    detection_by_period, cycle_rows = _load_detection_snapshots(result_dir)
    payloads = _read_csv((debug_dir or result_dir) / "track_output_payloads.csv")
    detections = _read_csv((debug_dir or result_dir) / "track_detections.csv")
    states = _read_csv((debug_dir or result_dir) / "track_states.csv")

    detection_angle_by_key: dict[tuple[int, int], float] = {}
    for period, rows in detection_by_period.items():
        for index, row in enumerate(rows):
            detection_angle_by_key[(period, index)] = _number(row, "theta_used_deg")

    eligible_payloads = 0
    matched_payloads = 0
    false_payloads = 0
    false_track_ids: set[int] = set()
    eligible_track_ids: set[int] = set()
    positions: list[float] = []
    speeds: list[float] = []
    angles: list[float] = []
    matched_periods: set[int] = set()
    best_match_by_period: dict[int, tuple[float, int]] = {}
    protocol_rows: list[dict[str, object]] = []
    for payload in payloads:
        period = _period_id_from_result(payload.get("result_id", ""))
        track_id = _integer(payload, "track_id")
        det_index = _integer(payload, "matched_det_index")
        target = truth.get(period)
        output_e = _number(payload, "output_e")
        output_n = _number(payload, "output_n")
        target_e = _number(target or {}, "e_mid")
        target_n = _number(target or {}, "n_mid")
        position_error = math.hypot(output_e - target_e, output_n - target_n)
        range_error = math.nan
        raw_detection = detection_by_period.get(period, [])
        if 0 <= det_index < len(raw_detection):
            range_error = abs(_number(raw_detection[det_index], "range_m") - _number(target or {}, "range_m"))
        eligible = True
        if audit.get("status") != "pass":
            eligible = False
        if period not in truth:
            eligible = False
        eligible_payloads += int(eligible)
        matched = bool(
            eligible
            and math.isfinite(position_error)
            and position_error <= MAX_POSITION_MATCH_M
            and (not math.isfinite(range_error) or range_error <= MAX_RANGE_MATCH_M)
        )
        matched_payloads += int(matched)
        false_payloads += int(eligible and not matched)
        if eligible:
            eligible_track_ids.add(track_id)
            if not matched:
                false_track_ids.add(track_id)
        if matched:
            matched_periods.add(period)
            positions.append(position_error)
            truth_speed = math.hypot(_number(target or {}, "ve_mps"), _number(target or {}, "vn_mps"))
            output_speed = _number(payload, "output_speed")
            if math.isfinite(output_speed) and math.isfinite(truth_speed):
                speeds.append(abs(output_speed - truth_speed))
            estimated_angle = detection_angle_by_key.get((period, det_index), math.nan)
            angle_error = _angle_error_deg(estimated_angle, _number(target or {}, "azimuth_deg"))
            if math.isfinite(angle_error):
                angles.append(angle_error)
            previous = best_match_by_period.get(period)
            if previous is None or position_error < previous[0]:
                best_match_by_period[period] = (position_error, track_id)
        protocol_rows.append({
            "branch": branch,
            "period_id": period,
            "track_id": track_id,
            "matched_det_index": det_index,
            "resolved_source": payload.get("resolved_source", ""),
            "eligible": int(eligible),
            "matched_truth": int(matched),
            "position_error_m": position_error,
            "range_error_m": range_error,
        })

    visible_count = len(truth)
    visible_periods = sorted(truth)
    selected_matches = [
        (period, best_match_by_period[period][1])
        for period in visible_periods
        if period in best_match_by_period
    ]
    id_switches = sum(
        left_id != right_id and right_period == left_period + 1
        for (left_period, left_id), (right_period, right_id)
        in zip(selected_matches, selected_matches[1:])
    )
    matched_period_count = len(matched_periods)
    confirmation_periods = visible_periods[1:]
    matched_after_confirmation = sum(
        period in matched_periods for period in confirmation_periods
    )
    detection_total = sum(len(rows) for rows in detection_by_period.values())
    detection_matched = 0
    detection_period_matched = 0
    for period, rows in detection_by_period.items():
        target = truth.get(period)
        if target is None:
            continue
        te = _number(target, "e_mid")
        tn = _number(target, "n_mid")
        trange = _number(target, "range_m")
        period_has_match = False
        for row in rows:
            distance = math.hypot(_number(row, "new_e") - te, _number(row, "new_n") - tn)
            range_error = abs(_number(row, "range_m") - trange)
            if distance <= MAX_POSITION_MATCH_M and (not math.isfinite(range_error) or range_error <= MAX_RANGE_MATCH_M):
                detection_matched += 1
                period_has_match = True
        detection_period_matched += int(period_has_match)
    selected_clusters = int(cfar_summary.get("selected", 0) or 0)
    cluster_target_count = detection_period_matched if cfar_summary.get("status") == "passed" else None
    cluster_false_alarm_count = (
        max(0, selected_clusters - int(cluster_target_count))
        if cluster_target_count is not None else None
    )
    cluster_false_alarm_rate = (
        cluster_false_alarm_count / selected_clusters
        if cluster_false_alarm_count is not None and selected_clusters else None
    )
    false_track_count = len(false_track_ids)
    track_output_count = len(eligible_track_ids)
    detection_record_false_count = detection_total - detection_matched
    detection_opportunity_pd = (
        detection_period_matched / visible_count if visible_count else None
    )
    cfar_summary_passed = cfar_summary.get("status") == "passed"
    metric_row: dict[str, object] = {
        "branch": branch,
        "expected_periods": expected_periods,
        "truth_visible_periods": visible_count,
        "detection_snapshot_periods": len(detection_by_period),
        "target_detection_opportunity_count": visible_count,
        "target_detection_hit_count": detection_period_matched,
        "target_detection_pd": detection_opportunity_pd,
        "target_detection_definition": (
            "one target detection opportunity per visible truth period; hit if any production "
            "detection record matches that period's target"
        ),
        "raw_detection_count": detection_total,
        "detection_match_count": detection_matched,
        "detection_record_count": detection_total,
        "detection_record_target_match_count": detection_matched,
        "detection_record_false_alarm_count": detection_record_false_count,
        "detection_record_false_alarm_rate": (
            detection_record_false_count / detection_total if detection_total else None
        ),
        "detection_record_definition": (
            "production detection_results rows matched against truth; not CFAR hit-cell count"
        ),
        "period_detection_match_count": detection_period_matched,
        "period_detection_pd": detection_opportunity_pd,
        "detection_pd": None,
        "detection_pd_status": "not_evaluable_no_independent_detection_opportunity_denominator",
        "raw_detection_pd": None,
        "raw_detection_pd_status": "not_evaluable_no_independent_detection_opportunity_denominator",
        "cell_false_hit_count": None,
        "cell_false_hit_fraction": None,
        "cell_false_hit_status": (
            "not_evaluable_no_target_cell_link_or_valid_cut_denominator"
        ),
        "raw_empirical_false_hit_fraction": None,
        "raw_empirical_false_hit_status": "not_evaluable_detection_records_are_not_cfar_cells",
        "eligible_protocol_payload_count": eligible_payloads,
        "matched_protocol_payload_count": matched_payloads,
        "matched_protocol_period_count": matched_period_count,
        "matched_protocol_periods": sorted(matched_periods),
        "false_track_count": false_track_count,
        "false_track_rate": false_track_count / track_output_count if track_output_count else None,
        "false_track_payload_count": false_payloads,
        "track_output_count": track_output_count,
        "unique_false_track_count": false_track_count,
        "unique_false_track_rate": false_track_count / track_output_count if track_output_count else None,
        "cfar_hit_cell_count": cfar_summary.get("hit_cells"),
        "cfar_cluster_count": cfar_summary.get("clusters"),
        "cfar_selected_cluster_count": selected_clusters if cfar_summary_passed else None,
        "cfar_pfa": None,
        "cfar_pfa_status": (
            "not_evaluable_target_on_without_valid_cut_or_target_off_denominator"
            if cfar_summary_passed else "not_evaluable_missing_cfar_summary"
        ),
        "cluster_false_alarm_count": cluster_false_alarm_count,
        "cluster_false_alarm_rate": cluster_false_alarm_rate,
        "cluster_false_alarm_definition": (
            "selected CFAR clusters minus one target-associated cluster per matched target period"
            if cfar_summary.get("status") == "passed" else None
        ),
        "cluster_false_alarm_status": (
            "period_association_proxy_no_cluster_id"
            if cfar_summary.get("status") == "passed" else "not_evaluable_missing_cfar_summary"
        ),
        "protocol_detection_false_alarm_count": None,
        "protocol_detection_false_alarm_rate": None,
        "protocol_detection_false_alarm_status": (
            "not_evaluable_payloads_do_not_preserve_production_detection_identity"
        ),
        "protocol_payload_false_alarm_count": false_payloads,
        "protocol_payload_false_alarm_rate": (
            false_payloads / eligible_payloads if eligible_payloads else None
        ),
        "track_pd_all_visible": matched_period_count / visible_count if visible_count else None,
        "track_pd_after_confirmation": (
            matched_after_confirmation / len(confirmation_periods)
            if confirmation_periods else None
        ),
        "track_continuity": matched_period_count / visible_count if visible_count else None,
        "track_continuity_after_confirmation": (
            matched_after_confirmation / len(confirmation_periods)
            if confirmation_periods else None
        ),
        "track_id_switch_count": id_switches,
        "angle_rmse_deg": _rmse(angles),
        "velocity_rmse_mps_ground_speed": _rmse(speeds),
        "position_rmse_m": _rmse(positions),
        "track_debug_payload_count": len(payloads),
        "track_debug_detection_rows": len(detections),
        "track_debug_state_rows": len(states),
        "track_audit_status": audit.get("status", "missing"),
        "track_audit_total_violations": audit.get("total_violations"),
        "protocol_target_failures": audit.get("protocol_target_failures", {}),
    }
    return metric_row, cycle_rows, protocol_rows


def _run_branch(
    case_root: Path,
    branch: str,
    input_mode: str,
    branch_input: Mapping[str, object],
    source_xml: Path,
    truth: Mapping[int, Mapping[str, str]],
    period_count: int,
    result_timeout_ms: int,
    input_rate_bytes_per_sec: int,
    protocol_layout: Mapping[str, object] | None = None,
) -> dict[str, object]:
    branch_root = case_root / "branches" / branch
    result_dir = branch_root / "result"
    configured_debug_dir = branch_root / "track_debug"
    if branch_root.exists() and any(branch_root.iterdir()):
        raise RuntimeError(f"refuse to overwrite existing branch output: {branch_root}")
    branch_root.mkdir(parents=True, exist_ok=True)
    correction_applied = bool(branch_input.get("correction_applied", False))
    correction_source = str(branch_input.get("correction_source", "none"))
    estimate_value = branch_input.get("delay_estimate_ns")
    reference_value = branch_input.get("delay_reference_ns")
    delay_estimate_ns = None if estimate_value is None else float(estimate_value)
    delay_reference_ns = None if reference_value is None else float(reference_value)
    gate = branch_evaluation_gate(
        branch,
        correction_applied=correction_applied,
        correction_source=correction_source,
        delay_estimate_ns=delay_estimate_ns,
        delay_reference_ns=delay_reference_ns,
    )
    record: dict[str, object] = {
        "branch": branch,
        "contract": branch_contract(),
        "input_mode": input_mode,
        "correction_applied": correction_applied,
        "correction_source": correction_source,
        "delay_estimate_ns": delay_estimate_ns,
        "delay_reference_ns": delay_reference_ns,
        "delay_residual_ns": gate.get("delay_residual_ns"),
        "correction_audit": branch_input.get("correction_audit", []),
        "protocol_layout": dict(protocol_layout or {}),
        "truth_used_in_estimator": bool(branch_input.get("truth_used_in_estimator", False)),
        "truth_used_to_apply_correction": bool(branch_input.get("truth_used_to_apply_correction", False)),
        "branch_evaluation": gate,
        "production_result_dir": str(result_dir),
        "status": gate["status"] if gate["status"] != "evaluable" else "not_started",
    }
    if gate["status"] != "evaluable":
        _write_json(branch_root / "branch_manifest.json", record)
        return record
    period_values = branch_input.get("period_paths")
    if not isinstance(period_values, list) or not all(isinstance(path, Path) for path in period_values):
        raise RuntimeError(f"invalid prepared period inputs for branch {branch}")
    period_paths = [path for path in period_values if isinstance(path, Path)]
    merged_value = branch_input.get("merged_input")
    if not isinstance(merged_value, Path):
        raise RuntimeError(f"invalid prepared merged input for branch {branch}")
    merged_input = merged_value
    xml_path = branch_root / "production.xml"
    configure_rc, configure_log = _configure_xml(
        source_xml, xml_path, result_dir, configured_debug_dir,
        protocol_layout or {},
    )
    record.update({
        "configuration_returncode": configure_rc,
        "configuration_log": configure_log,
        "status": "configuration_failed" if configure_rc else "not_started",
    })
    if configure_rc:
        _write_json(branch_root / "branch_manifest.json", record)
        return record

    configured_runtime_layout = _audit_runtime_xml_layout(xml_path, protocol_layout or {})
    record["runtime_xml_protocol_layout"] = configured_runtime_layout
    if configured_runtime_layout["status"] != "passed":
        record["status"] = "configuration_audit_failed"
        _write_json(branch_root / "branch_manifest.json", record)
        return record

    if input_mode == "local":
        echo_specs = [f"{index + 1}={path}" for index, path in enumerate(period_paths)]
        command: list[object] = [
            PIPE,
            "--config",
            xml_path,
            "--result-dir",
            result_dir,
            "--track-debug-dir",
            branch_root / "track_debug_runs",
            "--track-debug-dump",
            "on",
            "--runtime-mode=debug",
            "--runtime-diagnostics=on",
            "--local-test",
            *echo_specs,
        ]
        log_path = branch_root / "gmt_pipe_core.log"
        rc, elapsed = _run_logged(command, log_path, timeout_s=result_timeout_ms / 1000.0)
        pipe_run_dir = None
    else:
        runs_root = branch_root / "integration_runs"
        command = [
            SHM_INTEGRATION,
            xml_path,
            merged_input,
            runs_root,
            1,
            input_rate_bytes_per_sec,
            1_048_576,
            period_count,
            300_000,
            "",
            "",
            "",
            "",
            result_timeout_ms,
        ]
        log_path = branch_root / "shm_integration.log"
        environment = os.environ.copy()
        environment["GMTI_RUNTIME_MODE"] = "debug"
        rc, elapsed = _run_logged(
            command,
            log_path,
            env=environment,
            timeout_s=(result_timeout_ms / 1000.0) + 180.0,
        )
        pipe_run_dir = _find_pipe_run(log_path, runs_root)
        if pipe_run_dir is not None:
            result_dir = pipe_run_dir / "result"

    debug_dir = _find_debug_dir(result_dir, configured_debug_dir)
    if debug_dir is None and configured_debug_dir.is_dir():
        debug_dir = configured_debug_dir
    pipe_runtime_metrics = _parse_pipe_runtime_metrics(pipe_run_dir, log_path)
    cfar_log_paths = [log_path]
    if pipe_run_dir is not None:
        cfar_log_paths.extend(pipe_run_dir.rglob("gmticore.log"))
    cfar_summary = _parse_cfar_summaries(cfar_log_paths)
    audit: dict[str, object]
    if debug_dir is None:
        audit = {
            "status": "missing",
            "total_violations": None,
            "protocol_target_failures": {"missing_track_debug_dir": 1},
        }
    else:
        from scripts.audit_track_manager_run import audit_debug_dir

        try:
            audit = audit_debug_dir(debug_dir)
        except (OSError, ValueError, KeyError) as exc:
            audit = {
                "status": "error",
                "total_violations": None,
                "protocol_target_failures": {"audit_exception": 1},
                "audit_exception": str(exc),
            }
    metric_row, cycle_rows, protocol_rows = _evaluate_branch(
        branch, result_dir, debug_dir, truth, period_count, audit, cfar_summary
    )
    metric_row.update({
        "correction_applied": correction_applied,
        "correction_source": correction_source,
        "delay_estimate_ns": delay_estimate_ns,
        "delay_reference_ns": delay_reference_ns,
        "delay_residual_ns": gate.get("delay_residual_ns"),
    })
    production_status = "passed" if rc == 0 and audit.get("status") == "pass" else "failed"
    target_status = (
        "positive_track_output_observed"
        if metric_row["matched_protocol_payload_count"]
        else "no_eligible_protocol_target_observed"
    )
    record.update({
        "production_returncode": rc,
        "production_elapsed_sec": elapsed,
        "production_log": str(log_path),
        "pipe_run_dir": str(pipe_run_dir) if pipe_run_dir else None,
        "pipe_runtime_metrics": pipe_runtime_metrics,
        "cfar_summary": cfar_summary,
        "result_dir": str(result_dir),
        "track_debug_dir": str(debug_dir) if debug_dir else None,
        "production_status": production_status,
        "target_status": target_status,
        "metrics": metric_row,
        "cycle_rows": cycle_rows,
        "protocol_rows": protocol_rows,
        "audit": audit,
        "status": production_status,
    })
    _write_json(branch_root / "branch_manifest.json", record)
    _write_rows(branch_root / "cycle_metrics.csv", cycle_rows)
    _write_rows(branch_root / "protocol_payload_audit.csv", protocol_rows)
    return record


def _run_case(
    output_root: Path,
    seed: int,
    period_count: int,
    input_mode: str,
    result_timeout_ms: int,
    input_rate_bytes_per_sec: int,
    target_velocity_mps: float,
    target_snr_db: float,
    delay_error_ns: float,
) -> dict[str, object]:
    case_root = (
        output_root
        / f"seed_{seed:03d}"
        / f"velocity_{target_velocity_mps:.3f}"
        / f"snr_{target_snr_db:.1f}"
    )
    if case_root.exists() and any(case_root.iterdir()):
        raise RuntimeError(f"refuse to overwrite existing case output: {case_root}")
    case_root.mkdir(parents=True, exist_ok=True)
    scenario_path, scenario = _build_scenario(
        case_root,
        seed,
        period_count,
        target_velocity_mps,
        target_snr_db,
        delay_error_ns,
    )
    protocol_layout = _protocol_layout_from_scenario(scenario)
    stage2_log = case_root / "stage2_simulation.log"
    simulation_rc, simulation_elapsed = _run_logged(
        [SIMULATOR, "--config", scenario_path],
        stage2_log,
        timeout_s=1800.0,
    )
    case: dict[str, object] = {
        "seed": seed,
        "target_velocity_mps": target_velocity_mps,
        "target_snr_db": target_snr_db,
        "delay_error_ns": delay_error_ns,
        "period_count": period_count,
        "beam_id": protocol_layout["beam_id"],
        "protocol_layout": protocol_layout,
        "scenario": str(scenario_path),
        "scenario_sha256": _sha256(scenario_path),
        "simulation_returncode": simulation_rc,
        "simulation_elapsed_sec": simulation_elapsed,
        "simulation_log": str(stage2_log),
        "branches": [],
    }
    if simulation_rc:
        case["status"] = "simulation_failed"
        _write_json(case_root / "case_manifest.json", case)
        return case
    simulation_root = Path(str(scenario["output_dir"]))
    period_paths = _period_files(simulation_root, period_count)
    calibration_path, calibration_scenario = _build_calibration_scenario(
        case_root, scenario, seed
    )
    calibration_log = case_root / "calibration_simulation.log"
    calibration_rc, calibration_elapsed = _run_logged(
        [SIMULATOR, "--config", calibration_path],
        calibration_log,
        timeout_s=1800.0,
    )
    case.update({
        "calibration_scenario": str(calibration_path),
        "calibration_scenario_sha256": _sha256(calibration_path),
        "calibration_simulation_returncode": calibration_rc,
        "calibration_simulation_elapsed_sec": calibration_elapsed,
        "calibration_simulation_log": str(calibration_log),
    })
    if calibration_rc:
        case["status"] = "calibration_simulation_failed"
        _write_json(case_root / "case_manifest.json", case)
        return case
    calibration_root = Path(str(calibration_scenario["output_dir"]))
    calibration_period_paths = _period_files(calibration_root, period_count)
    input_root = case_root / "input"
    merged_input = _concat_inputs(period_paths, input_root / "target_unknown_delay.bin")
    calibration_input = _concat_inputs(
        calibration_period_paths,
        input_root / "target_free_calibration.bin",
    )
    input_audit = _audit_protocol_input(
        merged_input,
        period_count * int(protocol_layout["pulse_count"]),
        float(protocol_layout["expected_theta_deg"]),
        protocol_layout,
    )
    calibration_audit = _audit_protocol_input(
        calibration_input,
        period_count * int(protocol_layout["pulse_count"]),
        float(protocol_layout["expected_theta_deg"]),
        protocol_layout,
    )
    truth = _truth_by_period(simulation_root, period_count)
    source_xml = simulation_root / "config" / "temp_config_stage2_period_0000.xml"
    if not source_xml.is_file():
        raise RuntimeError(f"missing simulator XML: {source_xml}")
    runtime_xml_layout = _audit_runtime_xml_layout(source_xml, protocol_layout)
    case.update({
        "simulation_root": str(simulation_root),
        "period_files": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in period_paths
        ],
        "calibration_period_files": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in calibration_period_paths
        ],
        "continuous_input": {
            "path": str(merged_input),
            "bytes": merged_input.stat().st_size,
            "sha256": _sha256(merged_input),
        },
        "calibration_input": {
            "path": str(calibration_input),
            "bytes": calibration_input.stat().st_size,
            "sha256": _sha256(calibration_input),
        },
        "input_protocol_audit": input_audit,
        "calibration_protocol_audit": calibration_audit,
        "runtime_xml_protocol_layout": runtime_xml_layout,
        "truth_visible_periods": sorted(truth),
        "source_xml": str(source_xml),
    })
    if (
        input_audit["status"] != "passed"
        or calibration_audit["status"] != "passed"
        or runtime_xml_layout["status"] != "passed"
    ):
        case["status"] = "input_audit_failed"
        _write_json(case_root / "case_manifest.json", case)
        return case

    try:
        delay_estimator = _estimate_delay_from_calibration(calibration_input, protocol_layout)
    except (OSError, ValueError, RuntimeError, SystemExit) as exc:
        delay_estimator = {
            "status": "failed",
            "input_path": str(calibration_input),
            "input_role": "target_free_C_plus_N_only",
            "truth_used_in_estimator": False,
            "error": str(exc),
        }
    case["delay_estimator"] = delay_estimator
    branch_inputs: dict[str, dict[str, object]] = {
        "Current": {
            "branch": "Current",
            "period_paths": period_paths,
            "merged_input": merged_input,
            "correction_applied": False,
            "correction_source": "none",
            "delay_estimate_ns": None,
            "delay_reference_ns": delay_error_ns,
            "truth_used_in_estimator": False,
        },
    }
    estimated_delay = delay_estimator.get("delta_tau_ns")
    if delay_estimator.get("status") == "estimated" and estimated_delay is not None:
        branch_inputs["blind_calibrated"] = _prepare_corrected_inputs(
            case_root,
            "blind_calibrated",
            period_paths,
            float(estimated_delay),
            "target_free_phase_vs_frequency",
            protocol_layout,
        )
        branch_inputs["blind_calibrated"]["delay_estimate_ns"] = float(estimated_delay)
        branch_inputs["blind_calibrated"]["delay_reference_ns"] = delay_error_ns
        branch_inputs["blind_calibrated"]["truth_used_in_estimator"] = False
    else:
        branch_inputs["blind_calibrated"] = {
            "branch": "blind_calibrated",
            "period_paths": [],
            "merged_input": None,
            "correction_applied": False,
            "correction_source": "target_free_phase_vs_frequency",
            "delay_estimate_ns": None,
            "delay_reference_ns": delay_error_ns,
            "truth_used_in_estimator": False,
        }
    branch_inputs["known_error_calibrated"] = _prepare_corrected_inputs(
        case_root,
        "known_error_calibrated",
        period_paths,
        delay_error_ns,
        "truth_evaluation_only",
        protocol_layout,
    )
    branch_inputs["known_error_calibrated"]["delay_estimate_ns"] = delay_error_ns
    branch_inputs["known_error_calibrated"]["delay_reference_ns"] = delay_error_ns

    branch_records: list[dict[str, object]] = []
    for branch in branch_contract()["branches"]:  # type: ignore[union-attr]
        branch_records.append(_run_branch(
            case_root,
            str(branch),
            input_mode,
            branch_inputs[str(branch)],
            source_xml,
            truth,
            period_count,
            result_timeout_ms,
            input_rate_bytes_per_sec,
            protocol_layout,
        ))
    case["branches"] = branch_records
    case["status"] = (
        "completed"
        if all(record.get("status") == "passed" for record in branch_records)
        else "completed_with_gaps"
    )
    _write_json(case_root / "case_manifest.json", case)
    return case


def _parse_int_list(values: Iterable[str], name: str) -> list[int]:
    result: list[int] = []
    for raw in values:
        for item in str(raw).split(","):
            if item.strip():
                result.append(int(item))
    if not result:
        raise ValueError(f"{name} cannot be empty")
    return result


def _parse_float_list(values: Iterable[str], name: str) -> list[float]:
    result: list[float] = []
    for raw in values:
        for item in str(raw).split(","):
            if item.strip():
                value = float(item)
                if not math.isfinite(value):
                    raise ValueError(f"{name} values must be finite")
                result.append(value)
    if not result:
        raise ValueError(f"{name} cannot be empty")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="run one seed, one velocity and one SCNR")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--input-mode", choices=("shm", "local"), default="shm")
    parser.add_argument(
        "--period-count",
        type=int,
        choices=(3, 4, 5, 6, 7),
        default=PERIOD_COUNT,
        help="periods per case; smoke uses 3, formal supports the requested 5-7 range",
    )
    parser.add_argument("--seeds", nargs="+", default=None, help="seed list, comma-separated or repeated")
    parser.add_argument(
        "--target-velocities-mps",
        nargs="+",
        default=["6.7,12.0"],
        help="target ground-speed values in m/s, comma-separated or repeated",
    )
    parser.add_argument(
        "--snr-db",
        nargs="+",
        default=["30,35"],
        help="target SCNR/SNR injection values in dB, comma-separated or repeated",
    )
    parser.add_argument(
        "--delay-error-ns",
        type=float,
        default=DELAY_ERROR_NS,
        help="unknown channel-2 delay injected into the formal echo",
    )
    parser.add_argument("--result-timeout-ms", type=int, default=900_000)
    parser.add_argument(
        "--input-rate-bytes-per-sec",
        type=int,
        default=1_000_000,
        help="SHM producer pacing; keep below the measured production cycle throughput",
    )
    args = parser.parse_args()
    if not SIMULATOR.is_file() or not PIPE.is_file():
        raise SystemExit("missing build/simulate_stage2_statistical or build/GMTI_pipe_core")
    if args.input_mode == "shm" and not SHM_INTEGRATION.is_file():
        raise SystemExit(f"missing SHM integration wrapper: {SHM_INTEGRATION}")
    if args.input_rate_bytes_per_sec <= 0:
        raise SystemExit("--input-rate-bytes-per-sec must be positive")
    if not math.isfinite(args.delay_error_ns) or args.delay_error_ns <= 0.0:
        raise SystemExit("--delay-error-ns must be finite and positive")
    try:
        requested_seeds = _parse_int_list(args.seeds or ["101,202,303"], "--seeds")
        requested_velocities = _parse_float_list(args.target_velocities_mps, "--target-velocities-mps")
        requested_snrs = _parse_float_list(args.snr_db, "--snr-db")
    except (TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    if any(value <= 0.0 for value in requested_velocities):
        raise SystemExit("--target-velocities-mps values must be positive")
    if args.smoke:
        seeds = requested_seeds[:1]
        target_velocities = requested_velocities[:1]
        snr_values = requested_snrs[:1]
    else:
        seeds = requested_seeds
        target_velocities = requested_velocities
        snr_values = requested_snrs
    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise SystemExit(f"refuse to overwrite non-empty output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "created_at": datetime.now().astimezone().isoformat(),
        "output_root": str(output_root),
        "smoke": bool(args.smoke),
        "input_mode": args.input_mode,
        "period_count": args.period_count,
        "input_rate_bytes_per_sec": args.input_rate_bytes_per_sec,
        "seeds": seeds,
        "target_velocities_mps": target_velocities,
        "snr_db": snr_values,
        "delay_error_ns": args.delay_error_ns,
        "branch_contract": branch_contract(),
        "ai_training": False,
        "router_enabled": False,
        "platform": platform.platform(),
        "python": sys.version,
        "git": _git_identity(),
        "gpu_status_before": _gpu_status(),
        "disk_status_before": _disk_status(output_root),
        "commands": {
            "simulator": str(SIMULATOR),
            "pipe": str(PIPE),
            "input_mode": args.input_mode,
        },
        "cases": [],
    }
    _write_json(output_root / "manifest.json", manifest)
    cases: list[dict[str, object]] = []
    for seed in seeds:
        for target_velocity_mps in target_velocities:
            for target_snr_db in snr_values:
                case = _run_case(
                    output_root,
                    seed,
                    args.period_count,
                    args.input_mode,
                    args.result_timeout_ms,
                    args.input_rate_bytes_per_sec,
                    target_velocity_mps,
                    target_snr_db,
                    args.delay_error_ns,
                )
                cases.append(case)
                manifest["cases"] = cases
                _write_json(output_root / "manifest.json", manifest)

    metric_rows: list[dict[str, object]] = []
    protocol_rows: list[dict[str, object]] = []
    for case in cases:
        for branch_record in case.get("branches", []):
            if not isinstance(branch_record, Mapping):
                continue
            metrics = branch_record.get("metrics")
            if isinstance(metrics, Mapping):
                row = dict(metrics)
                row["seed"] = case.get("seed")
                row["target_velocity_mps"] = case.get("target_velocity_mps")
                row["target_snr_db"] = case.get("target_snr_db")
                row["case_status"] = case.get("status")
                metric_rows.append(row)
            rows = branch_record.get("protocol_rows")
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, Mapping):
                        copied = dict(row)
                        copied["seed"] = case.get("seed")
                        copied["target_velocity_mps"] = case.get("target_velocity_mps")
                        copied["target_snr_db"] = case.get("target_snr_db")
                        protocol_rows.append(copied)
    _write_rows(output_root / "track_branch_metrics.csv", metric_rows)
    _write_rows(output_root / "track_protocol_payload_audit.csv", protocol_rows)
    manifest["gpu_status_after"] = _gpu_status()
    manifest["disk_status_after"] = _disk_status(output_root)
    manifest["metric_rows"] = len(metric_rows)
    manifest["protocol_rows"] = len(protocol_rows)
    branch_statuses = [
        branch.get("status")
        for case in cases
        for branch in case.get("branches", [])
        if isinstance(branch, Mapping)
    ]
    manifest["status"] = (
        "completed"
        if cases
        and all(case.get("status") == "completed" for case in cases)
        and all(status == "passed" for status in branch_statuses)
        else "completed_with_gaps"
    )
    _write_json(output_root / "manifest.json", manifest)
    print(json.dumps({
        "status": manifest["status"],
        "output_root": str(output_root),
        "metric_rows": len(metric_rows),
        "protocol_rows": len(protocol_rows),
    }, ensure_ascii=False))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
