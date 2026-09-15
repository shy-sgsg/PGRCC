#!/usr/bin/env python3
"""Run a compact production TrackManager/PIPE continuous-target study.

The study deliberately keeps the production detector, association gates and
TrackManager in the loop.  It uses one physical beam and three consecutive
periods so the result is small enough for a repeatable regression while still
testing confirmation, same-period association and protocol-output provenance.

``blind_calibrated`` is currently an explicit fallback-to-Current branch and
``known_error_calibrated`` is an evaluation-only label.  Neither branch
injects truth or a speculative correction into production.  This distinction
is part of the output contract and prevents a no-op comparison from being
reported as a calibration gain.
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


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TEMPLATE = ROOT / "simulator/scenarios/beam50_one_targets_continuous.run.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
PIPE = ROOT / "build/GMTI_pipe_core"
SHM_INTEGRATION = ROOT / "scripts/run_shm_phase1_integration.sh"

PERIOD_COUNT = 3
BEAM_ID = 50
PULSE_NUM = 130
PACKET_HEADER_BYTES = 256
PULSE_LEN = 11820
CHANNEL_COUNT = 2
SAMPLES_PER_IQ = 2
BYTES_PER_SAMPLE = 4
PACKET_BYTES = (
    PACKET_HEADER_BYTES
    + PULSE_LEN * CHANNEL_COUNT * SAMPLES_PER_IQ * BYTES_PER_SAMPLE
)
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
        "blind_calibrated_status": "fallback_to_current",
        "known_error_calibrated_status": "evaluation_only",
        "blind_correction_applied": False,
        "known_correction_applied": False,
    }


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


def _build_scenario(
    case_root: Path,
    seed: int,
    period_count: int,
) -> tuple[Path, dict[str, object]]:
    payload = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"scenario template is not an object: {TEMPLATE}")
    scenario = copy.deepcopy(payload)
    simulation_root = case_root / "simulation"
    scenario["case_id"] = f"track_manager_continuous_beam{BEAM_ID}_seed_{seed}"
    scenario["output_dir"] = str(simulation_root)
    scenario["truth_output"] = True
    random_cfg = scenario.setdefault("random", {})
    random_cfg.update({
        "period_start": 0,
        "period_count": period_count,
        "beam_start": BEAM_ID,
        "beam_count": 1,
        "random_seed": seed,
    })
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
    targets = scenario.get("targets")
    if not isinstance(targets, list) or not targets:
        raise RuntimeError("continuous-target template must contain one target")
    target = targets[0]
    target.update({"target_id": "TRACK_CONTINUOUS_TARGET", "enabled": True})
    target.setdefault("init", {}).update({
        "type": "beam_bin_azimuth_offset",
        "beam_id": BEAM_ID,
        "expected_bin": 2048,
        "azimuth_offset_deg": 0.0,
    })
    target.setdefault("motion", {}).update({
        "type": "enu_velocity",
        "ve_mps": -6.0,
        "vn_mps": -3.0,
    })
    target.setdefault("amplitude", {}).update({"type": "snr_db", "snr_db": 35.0})
    target.setdefault("visibility", {}).update({
        "type": "hard_gate",
        "single_beam_only": True,
        "visible_beam_span": 0,
    })
    case_root.mkdir(parents=True, exist_ok=True)
    scenario_path = case_root / "scenario.run.json"
    _write_json(scenario_path, scenario)
    return scenario_path, scenario


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


def _audit_protocol_input(path: Path, expected_packets: int, expected_theta_deg: float) -> dict[str, object]:
    errors: list[str] = []
    expected_bytes = expected_packets * PACKET_BYTES
    actual_bytes = path.stat().st_size if path.is_file() else -1
    if actual_bytes != expected_bytes:
        errors.append(f"bytes={actual_bytes} expected={expected_bytes}")
    counters: list[int] = []
    theta_values: list[float] = []
    if path.is_file():
        with path.open("rb") as stream:
            for index in range(expected_packets):
                header = stream.read(PACKET_HEADER_BYTES)
                if len(header) != PACKET_HEADER_BYTES:
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
                    (packet_bytes == PACKET_BYTES, "packet_bytes"),
                    (counter == index, "counter"),
                    (theta_x100 == round(expected_theta_deg * 100.0), "theta"),
                    (magic_tail == 0x5B5B5B5B5B5B5B5B, "magic_tail"),
                ):
                    if not passed and len(errors) < 20:
                        errors.append(f"packet_{index}_{label}")
                stream.seek(PACKET_BYTES - PACKET_HEADER_BYTES, os.SEEK_CUR)
    contiguous = counters == list(range(expected_packets))
    if not contiguous:
        errors.append("prt_counters_not_contiguous")
    return {
        "status": "passed" if not errors else "failed",
        "path": str(path),
        "bytes": actual_bytes,
        "expected_bytes": expected_bytes,
        "checked_packets": len(counters),
        "counter_first": counters[0] if counters else None,
        "counter_last": counters[-1] if counters else None,
        "theta_values_deg": sorted(set(theta_values)),
        "error_count": len(errors),
        "errors": errors,
    }


def _concat_inputs(period_paths: list[Path], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        for source in period_paths:
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, output, length=8 * 1024 * 1024)
    return destination


def _configure_xml(
    source_xml: Path,
    destination: Path,
    result_dir: Path,
    track_debug_dir: Path,
) -> tuple[int, str]:
    overrides: dict[str, object] = {
        "result_add": result_dir,
        "track_debug_dir": track_debug_dir,
        "new_protocol_file_first_beam": BEAM_ID,
        "new_protocol_file_scan_beam_count": 1,
        "new_protocol_file_period_index": 0,
        "shm_scan_beam_count": 1,
        "shm_prt_counter_phase": 0,
        "wavepos_st": BEAM_ID,
        "wavepos_ed": BEAM_ID,
        "wavepos_skip": 1,
        "min_points": 3,
        "pf": "1e-6",
        "cfar_type": "GO",
        "cfar_doppler_circular": "true",
        "dynamic_cfar_enable": "false",
        "csi_detection_band_mode": "union",
        "track_output_state_source": "measurement",
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
    for root in (configured_debug_dir, result_dir / "track_debug", result_dir / "track_debug_runs"):
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
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
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
    for period, rows in detection_by_period.items():
        target = truth.get(period)
        if target is None:
            continue
        te = _number(target, "e_mid")
        tn = _number(target, "n_mid")
        trange = _number(target, "range_m")
        for row in rows:
            distance = math.hypot(_number(row, "new_e") - te, _number(row, "new_n") - tn)
            range_error = abs(_number(row, "range_m") - trange)
            if distance <= MAX_POSITION_MATCH_M and (not math.isfinite(range_error) or range_error <= MAX_RANGE_MATCH_M):
                detection_matched += 1
                break
    metric_row: dict[str, object] = {
        "branch": branch,
        "expected_periods": expected_periods,
        "truth_visible_periods": visible_count,
        "detection_snapshot_periods": len(detection_by_period),
        "raw_detection_count": detection_total,
        "raw_detection_pd": detection_matched / visible_count if visible_count else None,
        "raw_empirical_false_hit_fraction": (
            (detection_total - detection_matched) / detection_total if detection_total else None
        ),
        "eligible_protocol_payload_count": eligible_payloads,
        "matched_protocol_payload_count": matched_payloads,
        "matched_protocol_period_count": matched_period_count,
        "matched_protocol_periods": sorted(matched_periods),
        "false_track_count": false_payloads,
        "false_track_rate": false_payloads / eligible_payloads if eligible_payloads else None,
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
    period_paths: list[Path],
    merged_input: Path,
    source_xml: Path,
    truth: Mapping[int, Mapping[str, str]],
    period_count: int,
    result_timeout_ms: int,
    input_rate_bytes_per_sec: int,
) -> dict[str, object]:
    branch_root = case_root / "branches" / branch
    result_dir = branch_root / "result"
    configured_debug_dir = branch_root / "track_debug"
    if branch_root.exists() and any(branch_root.iterdir()):
        raise RuntimeError(f"refuse to overwrite existing branch output: {branch_root}")
    branch_root.mkdir(parents=True, exist_ok=True)
    xml_path = branch_root / "production.xml"
    configure_rc, configure_log = _configure_xml(
        source_xml, xml_path, result_dir, configured_debug_dir
    )
    record: dict[str, object] = {
        "branch": branch,
        "contract": branch_contract(),
        "input_mode": input_mode,
        "blind_correction_applied": False,
        "known_correction_applied": False,
        "configuration_returncode": configure_rc,
        "configuration_log": configure_log,
        "production_result_dir": str(result_dir),
        "status": "configuration_failed" if configure_rc else "not_started",
    }
    if configure_rc:
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
        branch, result_dir, debug_dir, truth, period_count, audit
    )
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
) -> dict[str, object]:
    case_root = output_root / f"seed_{seed:03d}"
    if case_root.exists() and any(case_root.iterdir()):
        raise RuntimeError(f"refuse to overwrite existing case output: {case_root}")
    case_root.mkdir(parents=True, exist_ok=True)
    scenario_path, scenario = _build_scenario(case_root, seed, period_count)
    stage2_log = case_root / "stage2_simulation.log"
    simulation_rc, simulation_elapsed = _run_logged(
        [SIMULATOR, "--config", scenario_path],
        stage2_log,
        timeout_s=1800.0,
    )
    case: dict[str, object] = {
        "seed": seed,
        "period_count": period_count,
        "beam_id": BEAM_ID,
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
    input_root = case_root / "input"
    merged_input = _concat_inputs(period_paths, input_root / "continuous_3period.bin")
    input_audit = _audit_protocol_input(
        merged_input,
        period_count * PULSE_NUM,
        -60.0 + (BEAM_ID - 1) * 2.0,
    )
    truth = _truth_by_period(simulation_root, period_count)
    source_xml = simulation_root / "config" / "temp_config_stage2_period_0000.xml"
    if not source_xml.is_file():
        raise RuntimeError(f"missing simulator XML: {source_xml}")
    case.update({
        "simulation_root": str(simulation_root),
        "period_files": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in period_paths
        ],
        "continuous_input": {
            "path": str(merged_input),
            "bytes": merged_input.stat().st_size,
            "sha256": _sha256(merged_input),
        },
        "input_protocol_audit": input_audit,
        "truth_visible_periods": sorted(truth),
        "source_xml": str(source_xml),
    })
    if input_audit["status"] != "passed":
        case["status"] = "input_audit_failed"
        _write_json(case_root / "case_manifest.json", case)
        return case

    branch_records: list[dict[str, object]] = []
    for branch in branch_contract()["branches"]:  # type: ignore[union-attr]
        branch_records.append(_run_branch(
            case_root,
            str(branch),
            input_mode,
            period_paths,
            merged_input,
            source_xml,
            truth,
            period_count,
            result_timeout_ms,
            input_rate_bytes_per_sec,
        ))
    case["branches"] = branch_records
    case["status"] = "completed"
    _write_json(case_root / "case_manifest.json", case)
    return case


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="run one seed; still uses three periods and all branches")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--input-mode", choices=("shm", "local"), default="shm")
    parser.add_argument("--period-count", type=int, choices=(3, 4, 5), default=PERIOD_COUNT)
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
    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise SystemExit(f"refuse to overwrite non-empty output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    seeds = [101] if args.smoke else [101, 202, 303]
    manifest: dict[str, object] = {
        "created_at": datetime.now().astimezone().isoformat(),
        "output_root": str(output_root),
        "smoke": bool(args.smoke),
        "input_mode": args.input_mode,
        "period_count": args.period_count,
        "input_rate_bytes_per_sec": args.input_rate_bytes_per_sec,
        "seeds": seeds,
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
        case = _run_case(
            output_root,
            seed,
            args.period_count,
            args.input_mode,
            args.result_timeout_ms,
            args.input_rate_bytes_per_sec,
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
                row["case_status"] = case.get("status")
                metric_rows.append(row)
            rows = branch_record.get("protocol_rows")
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, Mapping):
                        copied = dict(row)
                        copied["seed"] = case.get("seed")
                        protocol_rows.append(copied)
    _write_rows(output_root / "track_branch_metrics.csv", metric_rows)
    _write_rows(output_root / "track_protocol_payload_audit.csv", protocol_rows)
    manifest["gpu_status_after"] = _gpu_status()
    manifest["disk_status_after"] = _disk_status(output_root)
    manifest["metric_rows"] = len(metric_rows)
    manifest["protocol_rows"] = len(protocol_rows)
    manifest["status"] = "completed"
    _write_json(output_root / "manifest.json", manifest)
    print(json.dumps({
        "status": manifest["status"],
        "output_root": str(output_root),
        "metric_rows": len(metric_rows),
        "protocol_rows": len(protocol_rows),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
