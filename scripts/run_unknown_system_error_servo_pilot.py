#!/usr/bin/env python3
"""Run the true-vs-reported servo-angle unknown-error pilot.

This is a deterministic, model-based pilot for the next unknown-system-error
stage.  The simulator uses ``theta_true = theta_reported + delta`` for the
physical target/clutter geometry and beam gain, while the packet header keeps
``theta_reported``.  Each case emits a paired target-plus-background file and
an empty C+N file.  The unknown-side estimator sees only their difference,
reported channel geometry, reported beam angles, and nominal waveform/scene
metadata.  It consumes six pair phases plus the multi-beam target-power
profile; it never reads servo truth or the known offset.

Known correction is an evaluator-only upper bound: it rewrites only the
protocol header angle in a copy of the raw file.  The raw payload is checked
byte-for-byte unchanged.  The unknown branch applies the pilot estimate only
when its fit is outside the zero-error deadband/uncertainty; otherwise it
falls back to the original reported-angle file.  No AI model or router is
trained or invoked.
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

import analyze_four_channel_observables as observables  # noqa: E402
import run_model_mismatch_audit as production_audit  # noqa: E402


TEMPLATE = ROOT / "configs/research/unknown_system_error_baseline_pilot.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
GMTI_CORE = ROOT / "build/GMTI_core"
HEADER_BYTES = 256
THETA_OFFSET = 218
DEFAULT_ERRORS_DEG = (0.0, 0.05, -0.05, 0.10, -0.10, 0.20, -0.20, 0.50, -0.50)
DEFAULT_SEEDS = (101, 202)
PAIR_DEFINITIONS = observables.PAIR_DEFINITIONS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        writer = csv.DictWriter(
            stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(_json_safe(materialized))


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "--git-dir=.git-real", "--work-tree=.", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _run_logged(
    command: Sequence[str], log_path: Path, env: Optional[Mapping[str, str]] = None
) -> tuple[int, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(str(item) for item in command) + "\n")
        if env is not None:
            for key in ("GMTI_CFAR_DUMP_BEAM",):
                if key in env:
                    log.write(f"{key}={env[key]}\n")
        log.flush()
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


def _find_period_file(case_root: Path) -> Path:
    manifest = case_root / "data/period_files.csv"
    if not manifest.is_file():
        raise RuntimeError(f"missing Stage2 period manifest: {manifest}")
    with manifest.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise RuntimeError(f"servo pilot requires one period: {manifest} -> {len(rows)}")
    raw = Path(rows[0]["file"])
    candidates = [raw] if raw.is_absolute() else [case_root / raw, ROOT / raw, raw]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(f"missing Stage2 period file: {candidates}")


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _prepare_template(template: Mapping[str, object]) -> dict[str, object]:
    config = copy.deepcopy(template)
    scan = config.setdefault("scan", {})
    if not isinstance(scan, dict):
        raise ValueError("scan must be an object")
    scan.update({
        "scan_min_deg": -4.0,
        "scan_step_deg": 2.0,
        "beam_count": 5,
        "beam_width_deg": 4.0,
        "beam_index_base": 1,
    })
    waveform = config.setdefault("waveform", {})
    if not isinstance(waveform, dict):
        raise ValueError("waveform must be an object")
    # Keep a slant range above the 6 km platform height and a compact, valid
    # production range crop.  The target is at raw sample 3500 (~8.75 km).
    waveform.update({
        "pulse_len": 8192,
        # The production beam-quality gate needs a full INS update aperture;
        # eight PRTs span only 6 ms and are shorter than the 25 Hz navigation
        # update period.
        "pulse_num": 130,
        "tr_us": 20.0,
        "new_protocol_channel_count": 4,
        "new_protocol_read_channel_1": 1,
        "new_protocol_read_channel_2": 2,
    })
    range_processing = config.setdefault("range_processing", {})
    if not isinstance(range_processing, dict):
        raise ValueError("range_processing must be an object")
    range_processing.update({
        "range_fft_len": 8192,
        "range_crop_start": 0,
        "range_crop_len": 4096,
        "sample_delay_us": 0.0,
    })
    random_cfg = config.setdefault("random", {})
    if not isinstance(random_cfg, dict):
        raise ValueError("random must be an object")
    random_cfg.update({"period_start": 0, "period_count": 1, "beam_start": 1, "beam_count": 5})
    scene = config.setdefault("scene", {})
    if not isinstance(scene, dict):
        raise ValueError("scene must be an object")
    scene.update({"mode": "full", "output_signal_domain": "raw_lfm", "signal_only": False})
    scene["range_min_m"] = 5000.0
    scene["range_max_m"] = 12000.0
    scene["azimuth_min_deg"] = -8.0
    scene["azimuth_max_deg"] = 8.0
    area = scene.setdefault("area_clutter", {})
    if not isinstance(area, dict):
        raise ValueError("scene.area_clutter must be an object")
    area.update({"enabled": False, "model": "continuous_texture"})
    noise = scene.setdefault("thermal_noise", {})
    if not isinstance(noise, dict):
        raise ValueError("scene.thermal_noise must be an object")
    noise.update({"enabled": True, "noise_power": 0.001, "include_target_only": False})
    channel_geometry = config.get("channel_geometry")
    if not isinstance(channel_geometry, dict):
        raise ValueError("channel_geometry must be an object")
    # This pilot isolates servo pointing.  Keep true and reported receiver
    # phase centres identical; geometry-error and servo-error confounding is a
    # later matrix dimension, not silently mixed into this pilot.
    for index in range(1, 5):
        item = channel_geometry.get(f"channel_{index}")
        if not isinstance(item, dict):
            raise ValueError(f"channel_geometry.channel_{index} must be an object")
        if "reported" in item:
            reported = item["reported"]
            if not isinstance(reported, dict):
                raise ValueError(f"channel_{index}.reported must be an object")
            item["true"] = copy.deepcopy(reported)
        else:
            item["true"] = {
                "x_m": item.get("x_m"),
                "y_m": item.get("y_m"),
                "z_m": item.get("z_m"),
            }
    targets = config.get("targets")
    if not isinstance(targets, list) or not targets or not isinstance(targets[0], dict):
        raise ValueError("template must contain one target object")
    target = targets[0]
    target.update({"target_id": "SERVO_PILOT_TARGET", "enabled": True})
    init = target.setdefault("init", {})
    if not isinstance(init, dict):
        raise ValueError("target.init must be an object")
    init.update({"type": "beam_bin_azimuth_offset", "beam_id": 3, "expected_bin": 3500, "azimuth_offset_deg": 0.0})
    target["motion"] = {"type": "static"}
    target["amplitude"] = {"type": "snr_db", "snr_db": 30.0}
    target["visibility"] = {"type": "gaussian", "single_beam_only": False, "visible_beam_span": -1}
    config["servo_angle_error"] = {"enabled": True, "true_minus_reported_deg": 0.0}
    config["truth_output"] = True
    config["scene_mode"] = "full"
    return config


def _case_config(
    template: Mapping[str, object],
    case_root: Path,
    error_deg: float,
    seed: int,
) -> dict[str, object]:
    config = copy.deepcopy(template)
    config["case_id"] = f"servo_error_{error_deg:+.3f}_seed_{seed}".replace("+", "p").replace("-", "m").replace(".", "p")
    config["output_dir"] = str((case_root / "on").resolve())
    config["paired_background_output_dir"] = str((case_root / "off").resolve())
    random_cfg = config.get("random")
    if not isinstance(random_cfg, dict):
        raise ValueError("prepared random config is not an object")
    random_cfg["random_seed"] = int(seed)
    servo = config.get("servo_angle_error")
    if not isinstance(servo, dict):
        raise ValueError("prepared servo_angle_error is not an object")
    servo["enabled"] = True
    servo["true_minus_reported_deg"] = float(error_deg)
    return config


def _load_raw_delta(
    on_path: Path,
    off_path: Path,
    pulse_len: int,
    channel_count: int,
) -> tuple[np.ndarray, np.ndarray, int]:
    packet_bytes = HEADER_BYTES + pulse_len * channel_count * 2 * 4
    on_raw = np.fromfile(on_path, dtype=np.uint8)
    off_raw = np.fromfile(off_path, dtype=np.uint8)
    if on_raw.size != off_raw.size or on_raw.size % packet_bytes != 0:
        raise ValueError(
            f"paired raw layout mismatch: on={on_raw.size} off={off_raw.size} packet_bytes={packet_bytes}"
        )
    packet_count = on_raw.size // packet_bytes
    on_packets = on_raw.reshape(packet_count, packet_bytes)
    off_packets = off_raw.reshape(packet_count, packet_bytes)
    on_payload = on_packets[:, HEADER_BYTES:].copy().view("<f4").reshape(
        packet_count, pulse_len, channel_count, 2
    )
    off_payload = off_packets[:, HEADER_BYTES:].copy().view("<f4").reshape(
        packet_count, pulse_len, channel_count, 2
    )
    on_complex = on_payload[..., 0] + 1j * on_payload[..., 1]
    off_complex = off_payload[..., 0] + 1j * off_payload[..., 1]
    delta = on_complex - off_complex
    theta_reported = np.asarray(
        [struct.unpack_from("<h", on_packets[i].tobytes(), THETA_OFFSET)[0] / 100.0
         for i in range(packet_count)],
        dtype=np.float64,
    )
    return delta, theta_reported, packet_count


def _wrap_phase(value: float) -> float:
    return float(math.atan2(math.sin(value), math.cos(value)))


def _quadratic_peak(theta: np.ndarray, power: np.ndarray, weights: np.ndarray) -> Optional[float]:
    valid = np.isfinite(theta) & np.isfinite(power) & np.isfinite(weights) & (power > 0.0) & (weights > 0.0)
    if int(np.count_nonzero(valid)) < 3:
        return None
    x = theta[valid]
    y = np.log(np.maximum(power[valid], np.finfo(np.float64).tiny))
    w = np.sqrt(weights[valid])
    design = np.column_stack((x * x, x, np.ones_like(x)))
    try:
        coeff, _, rank, _ = np.linalg.lstsq(design * w[:, None], y * w, rcond=None)
    except np.linalg.LinAlgError:
        return None
    if int(rank) < 3 or not np.all(np.isfinite(coeff)) or coeff[0] >= -1.0e-12:
        return None
    peak = -float(coeff[1]) / (2.0 * float(coeff[0]))
    return peak if math.isfinite(peak) else None


def _estimate_servo(
    on_path: Path,
    off_path: Path,
    config: Mapping[str, object],
    resolved: Mapping[str, object],
    seed: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    waveform = config.get("waveform", {})
    scan = config.get("scan", {})
    range_processing = config.get("range_processing", {})
    if not isinstance(waveform, Mapping) or not isinstance(scan, Mapping) or not isinstance(range_processing, Mapping):
        raise ValueError("servo estimator requires waveform/scan/range_processing objects")
    pulse_len = int(waveform["pulse_len"])
    channel_count = int(waveform["new_protocol_channel_count"])
    beam_count = int(scan["beam_count"])
    pulses_per_beam = int(waveform["pulse_num"])
    delta, theta_headers, packet_count = _load_raw_delta(on_path, off_path, pulse_len, channel_count)
    if packet_count != beam_count * pulses_per_beam:
        raise ValueError(
            f"unexpected beam/pulse layout: packets={packet_count} expected={beam_count * pulses_per_beam}"
        )
    grouped = delta.reshape(beam_count, pulses_per_beam, pulse_len, channel_count)
    theta_grouped = theta_headers.reshape(beam_count, pulses_per_beam)
    theta = np.median(theta_grouped, axis=1)
    target = config["targets"][0]  # type: ignore[index]
    if not isinstance(target, Mapping):
        raise ValueError("target is not an object")
    init = target.get("init", {})
    if not isinstance(init, Mapping):
        raise ValueError("target.init is not an object")
    target_beam = int(init["beam_id"])
    beam_index_base = int(scan.get("beam_index_base", 1))
    target_nominal_theta = float(scan["scan_min_deg"]) + (
        target_beam - beam_index_base
    ) * float(scan["scan_step_deg"]) + float(init.get("azimuth_offset_deg", 0.0))
    center_sample = int(range_processing.get("range_crop_start", 0)) + int(init["expected_bin"])
    echo_samples = max(1, int(round(float(waveform["tr_us"]) * 1.0e-6 * float(waveform["fs_mhz"]) * 1.0e6)))
    lo = max(0, center_sample - echo_samples - 32)
    hi = min(pulse_len, center_sample + echo_samples + 32)
    if hi <= lo:
        raise ValueError(f"invalid target raw window: {lo}:{hi}")

    target_window = grouped[:, :, lo:hi, :]
    power_by_pulse = np.sum(np.abs(target_window) ** 2, axis=(2, 3), dtype=np.float64)
    beam_power = np.median(power_by_pulse, axis=1)
    pair_rows: list[dict[str, object]] = []
    pair_coherences: list[np.ndarray] = []
    phase_residuals: list[np.ndarray] = []
    reported_positions = observables.load_channel_positions(TEMPLATE, source="reported")
    fc_hz = float(waveform["fc_ghz"]) * 1.0e9
    for name, i, j in PAIR_DEFINITIONS:
        a = target_window[:, :, :, i]
        b = target_window[:, :, :, j]
        cross = np.sum(a * np.conj(b), axis=(1, 2), dtype=np.complex128)
        p_i = np.sum(np.abs(a) ** 2, axis=(1, 2), dtype=np.float64)
        p_j = np.sum(np.abs(b) ** 2, axis=(1, 2), dtype=np.float64)
        denominator = np.sqrt(np.maximum(0.0, p_i * p_j))
        coherence = np.divide(
            np.abs(cross), denominator,
            out=np.zeros_like(denominator), where=denominator > 0.0,
        )
        observed_phase = np.angle(cross)
        nominal_phase = np.asarray([
            observables.nominal_pair_phase_rad(
                float(theta_value), center_sample, resolved, reported_positions,
                i, j, fc_hz=fc_hz,
            )
            for theta_value in theta
        ], dtype=np.float64)
        residual = np.asarray([
            _wrap_phase(float(obs - nominal))
            for obs, nominal in zip(observed_phase, nominal_phase)
        ], dtype=np.float64)
        pair_coherences.append(coherence)
        phase_residuals.append(residual)
        for beam_id in range(beam_count):
            pair_rows.append({
                "beam_id_0based": beam_id,
                "beam_id_1based": beam_id + beam_index_base,
                "theta_reported_deg": theta[beam_id],
                "pair": name,
                "observed_phase_rad": observed_phase[beam_id],
                "nominal_phase_rad": nominal_phase[beam_id],
                "phase_residual_rad": residual[beam_id],
                "coherence": coherence[beam_id],
            })
    coherence = np.mean(np.vstack(pair_coherences), axis=0)
    phase_rms = np.sqrt(np.mean(np.vstack(phase_residuals) ** 2, axis=0))
    phase_weight = np.exp(-0.5 * (phase_rms / 0.75) ** 2)
    fit_weight = np.clip(coherence, 0.05, 1.0) * np.clip(phase_weight, 0.05, 1.0)
    peak = _quadratic_peak(theta, beam_power, fit_weight)
    estimate: Optional[float] = None if peak is None else target_nominal_theta - peak
    bootstrap_peaks: list[float] = []
    rng = np.random.default_rng(int(seed) + 0x53E2)
    if peak is not None:
        for _ in range(128):
            indices = rng.integers(0, pulses_per_beam, size=(beam_count, pulses_per_beam))
            sampled = np.take_along_axis(power_by_pulse, indices, axis=1)
            sampled_power = np.median(sampled, axis=1)
            sampled_peak = _quadratic_peak(theta, sampled_power, fit_weight)
            if sampled_peak is not None:
                bootstrap_peaks.append(sampled_peak)
    if len(bootstrap_peaks) >= 8:
        uncertainty = max(0.01, 0.5 * float(np.percentile(bootstrap_peaks, 97.5) - np.percentile(bootstrap_peaks, 2.5)))
    else:
        uncertainty = None
    fit_status = "fit" if estimate is not None else "fallback_unidentifiable"
    summary: dict[str, object] = {
        "fit_status": fit_status,
        "estimator_name": "six_pair_phase_plus_multibeam_power_quadratic_pilot",
        "truth_used_in_estimator": False,
        "input_on_path": str(on_path),
        "input_off_path": str(off_path),
        "beam_count": beam_count,
        "pulses_per_beam": pulses_per_beam,
        "target_raw_window_start": lo,
        "target_raw_window_end_exclusive": hi,
        "target_nominal_theta_deg": target_nominal_theta,
        "fitted_beam_peak_theta_reported_deg": peak,
        "estimated_true_minus_reported_deg": estimate,
        "uncertainty_deg": uncertainty,
        "mean_six_pair_coherence": float(np.mean(coherence)) if coherence.size else None,
        "min_six_pair_coherence": float(np.min(coherence)) if coherence.size else None,
        "mean_phase_residual_rms_rad": float(np.mean(phase_rms)) if phase_rms.size else None,
        "max_phase_residual_rms_rad": float(np.max(phase_rms)) if phase_rms.size else None,
        "bootstrap_peak_count": len(bootstrap_peaks),
        "nominal_inputs": {
            "reported_channel_positions_m": reported_positions.tolist(),
            "scan_min_deg": scan["scan_min_deg"],
            "scan_step_deg": scan["scan_step_deg"],
            "beam_width_deg": scan["beam_width_deg"],
            "expected_bin": init["expected_bin"],
        },
    }
    for beam_id in range(beam_count):
        pair_rows.append({
            "beam_id_0based": beam_id,
            "beam_id_1based": beam_id + beam_index_base,
            "theta_reported_deg": theta[beam_id],
            "pair": "SUMMARY",
            "beam_target_power": beam_power[beam_id],
            "six_pair_coherence": coherence[beam_id],
            "phase_residual_rms_rad": phase_rms[beam_id],
            "fit_weight": fit_weight[beam_id],
        })
    return summary, pair_rows


def _rewrite_header_angle_with_layout(
    source: Path, destination: Path, packet_bytes: int, correction_deg: float
) -> dict[str, object]:
    data = bytearray(source.read_bytes())
    if packet_bytes <= HEADER_BYTES or len(data) % packet_bytes != 0:
        raise ValueError(f"raw file does not match packet layout: {source}")
    original_angles: list[float] = []
    corrected_angles: list[float] = []
    for packet_index in range(len(data) // packet_bytes):
        offset = packet_index * packet_bytes + THETA_OFFSET
        reported = struct.unpack_from("<h", data, offset)[0] / 100.0
        corrected = reported + float(correction_deg)
        encoded = int(round(corrected * 100.0))
        if encoded < -32768 or encoded > 32767:
            raise ValueError(f"corrected servo angle overflows protocol field: {corrected}")
        struct.pack_into("<h", data, offset, encoded)
        original_angles.append(reported)
        corrected_angles.append(encoded / 100.0)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    payload_hash = hashlib.sha256()
    source_payload_hash = hashlib.sha256()
    source_data = source.read_bytes()
    for begin in range(0, len(data), packet_bytes):
        payload_hash.update(data[begin + HEADER_BYTES:begin + packet_bytes])
        source_payload_hash.update(source_data[begin + HEADER_BYTES:begin + packet_bytes])
    return {
        "source": str(source),
        "destination": str(destination),
        "correction_deg": float(correction_deg),
        "packet_count": len(data) // packet_bytes,
        "source_first_header_theta_deg": original_angles[0] if original_angles else None,
        "corrected_first_header_theta_deg": corrected_angles[0] if corrected_angles else None,
        "payload_sha256": payload_hash.hexdigest(),
        "source_payload_sha256": source_payload_hash.hexdigest(),
        "payload_unchanged": payload_hash.hexdigest() == source_payload_hash.hexdigest(),
        "header_quantization_max_abs_error_deg": max(
            (abs((a + correction_deg) - b) for a, b in zip(original_angles, corrected_angles)),
            default=0.0,
        ),
        "raw_sha256": _sha256(destination),
    }


def _set_core_xml(source_xml: Path, raw_path: Path, result_root: Path) -> Path:
    tree = ET.parse(source_xml)
    root = tree.getroot()
    parameter = root.find(".//GMTI_parameter")
    if parameter is None:
        raise RuntimeError(f"missing GMTI_parameter in {source_xml}")
    data_node = parameter.find("GMTI_data_new")
    result_node = parameter.find("result_add")
    if data_node is None or result_node is None:
        raise RuntimeError(f"missing GMTI_data_new/result_add in {source_xml}")
    data_node.text = str(raw_path.resolve())
    result_node.text = str((result_root / "stage2/algorithm_result/period_0000").resolve())
    for tag, value in (("debug_pc_peak", "0"), ("pc_peak_scene_truth", "")):
        node = parameter.find(tag)
        if node is not None:
            node.text = value
    output_xml = result_root / "config/production.xml"
    output_xml.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_xml, encoding="utf-8", xml_declaration=True)
    production_audit.patch_production_xml(output_xml)
    return output_xml


def _read_cfar_summary(log_path: Path) -> dict[str, object]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = re.findall(r"\[CFAR\]\[SUMMARY\].*", text)
    values: dict[str, object] = {"summary_found": bool(lines), "summary_line": lines[-1] if lines else None}
    if lines:
        for key, raw in re.findall(r"([A-Za-z][A-Za-z0-9_]*)=([-+0-9.eE]+)", lines[-1]):
            number = float(raw)
            values[key] = int(number) if number.is_integer() else number
    return values


def _read_core_metrics(result_root: Path, log_path: Path) -> dict[str, object]:
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    beam_error_lines = re.findall(r"\[fusion\]\[BEAM-ERR\].*", log_text)
    scan_quality_failure_lines = re.findall(
        r"\[fusion\]\[ERR\] scan beam quality gate failed.*", log_text
    )
    detections = sorted(result_root.rglob("detection_results*.csv"))
    detection_count = None
    margin_count = 0
    if detections:
        rows = _read_csv(detections[0])
        detection_count = len(rows)
        margin_count = sum(
            1 for row in rows
            if row.get("cfar_margin_db") not in (None, "", "nan", "NaN")
        )
    csi_summaries = sorted(result_root.rglob("csi_metric_tap_summary.csv"))
    csi_row: dict[str, str] = {}
    if csi_summaries:
        csi_rows = _read_csv(csi_summaries[0])
        csi_row = next(
            (row for row in csi_rows if row.get("roi_name") == "active_support_unmasked"),
            csi_rows[0] if csi_rows else {},
        )
    runtime = sorted(result_root.rglob("runtime_config_dump.json"))
    return {
        "detection_csv": str(detections[0]) if detections else None,
        "detection_count": detection_count,
        "detection_margin_count": margin_count,
        "cfar": _read_cfar_summary(log_path),
        "csi_summary": csi_row,
        "runtime_config": str(runtime[0]) if runtime else None,
        "internal_beam_error_count": len(beam_error_lines),
        "internal_scan_quality_failure_count": len(scan_quality_failure_lines),
        "internal_quality_valid": not beam_error_lines and not scan_quality_failure_lines,
        "internal_beam_error_examples": beam_error_lines[:3],
        "internal_scan_quality_failure_examples": scan_quality_failure_lines[:3],
    }


def _run_core_branch(
    case: Mapping[str, object],
    branch: str,
    raw_path: Path,
    core: Path,
) -> dict[str, object]:
    branch_root = Path(case["case_root"]) / "core" / branch
    source_xml = Path(case["source_xml"])
    xml_path = _set_core_xml(source_xml, raw_path, branch_root)
    env = os.environ.copy()
    env["GMTI_CFAR_DUMP_BEAM"] = "1,2,3,4,5"
    log_path = branch_root / "gmticore.log"
    rc, elapsed = _run_logged(
        [str(core), str(xml_path), "--runtime-mode=debug", "--runtime-diagnostics=on"],
        log_path,
        env,
    )
    row: dict[str, object] = {
        "case_id": case["case_id"],
        "error_deg": case["error_deg"],
        "seed": case["seed"],
        "branch": branch,
        "input_raw": str(raw_path),
        "input_raw_sha256": _sha256(raw_path),
        "source_xml": str(source_xml),
        "production_xml": str(xml_path),
        "result_root": str(branch_root),
        "log_path": str(log_path),
        "gmticore_exit_code": rc,
        "elapsed_s": elapsed,
        "status": "completed" if rc == 0 else "core_failed",
    }
    if rc == 0:
        row.update(_read_core_metrics(branch_root, log_path))
        if not row.get("internal_quality_valid", False):
            row["status"] = "completed_with_internal_quality_failure"
    return row


def _tag_error(value: float) -> str:
    if abs(value) < 1.0e-12:
        return "zero"
    return ("p" if value > 0 else "m") + f"{abs(value):.3f}deg".replace(".", "p")


def run_pilot(
    output_root: Path,
    simulator: Path,
    core: Path,
    errors_deg: Sequence[float],
    seeds: Sequence[int],
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
    if not errors_deg or not seeds:
        raise ValueError("servo pilot requires at least one error and one seed")
    errors = [float(value) for value in errors_deg]
    seeds_int = [int(value) for value in seeds]
    if any(not math.isfinite(value) for value in errors):
        raise ValueError("servo errors must be finite")
    output_root.mkdir(parents=True, exist_ok=True)
    gpu = _probe_gpu()
    if not skip_core and int(gpu["returncode"]) != 0:
        raise RuntimeError("nvidia-smi failed; refusing to claim a CUDA servo pilot")
    template = _prepare_template(json.loads(TEMPLATE.read_text(encoding="utf-8")))
    _write_json(output_root / "prepared_template.json", template)
    case_rows: list[dict[str, object]] = []
    estimate_rows: list[dict[str, object]] = []
    case_records: list[dict[str, object]] = []
    for case_index, (error_deg, seed) in enumerate(
        ((error, seed) for error in errors for seed in seeds), 1
    ):
        case_root = output_root / "cases" / f"case_{case_index:03d}_{_tag_error(error_deg)}_seed_{seed}"
        case_root.mkdir(parents=True, exist_ok=True)
        config = _case_config(template, case_root, error_deg, seed)
        config_path = case_root / "scenario.run.json"
        _write_json(config_path, config)
        stage2_log = case_root / "stage2.log"
        rc, elapsed = _run_logged(
            [str(simulator), "--config", str(config_path)], stage2_log
        )
        if rc != 0:
            raise RuntimeError(f"Stage2 failed for {config_path}; see {stage2_log}")
        on_root = case_root / "on"
        off_root = case_root / "off"
        on_raw = _find_period_file(on_root)
        off_raw = _find_period_file(off_root)
        source_xml = on_root / "config/temp_config_stage2_period_0000.xml"
        resolved_path = on_root / "scenario_resolved.json"
        servo_truth_path = on_root / "truth/servo_angle_truth.csv"
        for required in (source_xml, resolved_path, servo_truth_path):
            if not required.is_file():
                raise RuntimeError(f"missing servo pilot artifact: {required}")
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        estimator, pair_rows = _estimate_servo(on_raw, off_raw, config, resolved, seed)
        _write_json(case_root / "unknown_servo_estimator.json", estimator)
        _write_rows(case_root / "servo_observables.csv", pair_rows)
        truth_rows = _read_csv(servo_truth_path)
        if not truth_rows:
            raise RuntimeError(f"servo truth is empty: {servo_truth_path}")
        header_error = max(
            abs(float(row["header_theta_deg"]) - float(row["theta_reported_deg"]))
            for row in truth_rows
        )
        truth_error = max(
            abs(float(row["theta_true_deg"]) - float(row["theta_reported_deg"]) - error_deg)
            for row in truth_rows
        )
        if header_error > 0.011 or truth_error > 1.0e-9:
            raise RuntimeError(
                f"servo separation invariant failed for {case_root}: header_error={header_error} truth_error={truth_error}"
            )
        case_id = str(config["case_id"])
        case_record: dict[str, object] = {
            "case_index": case_index,
            "case_id": case_id,
            "case_root": str(case_root),
            "error_deg": error_deg,
            "seed": seed,
            "stage2_exit_code": rc,
            "stage2_elapsed_s": elapsed,
            "scenario_config": str(config_path),
            "resolved_scenario": str(resolved_path),
            "servo_truth": str(servo_truth_path),
            "source_xml": str(source_xml),
            "on_raw": str(on_raw),
            "off_raw": str(off_raw),
            "on_raw_sha256": _sha256(on_raw),
            "off_raw_sha256": _sha256(off_raw),
            "servo_truth_rows": len(truth_rows),
            "header_matches_reported_max_abs_deg": header_error,
            "truth_matches_configured_offset_max_abs_deg": truth_error,
            "resolved_servo_angle_error": resolved.get("servo_angle_error"),
            "estimator": estimator,
        }
        case_records.append(case_record)
        case_rows.append({
            "case_index": case_index,
            "case_id": case_id,
            "error_deg": error_deg,
            "seed": seed,
            "stage2_exit_code": rc,
            "stage2_elapsed_s": elapsed,
            "on_raw": str(on_raw),
            "off_raw": str(off_raw),
            "on_raw_sha256": _sha256(on_raw),
            "off_raw_sha256": _sha256(off_raw),
            "servo_truth_rows": len(truth_rows),
            "header_matches_reported_max_abs_deg": header_error,
            "truth_matches_configured_offset_max_abs_deg": truth_error,
        })
        estimate_rows.append({
            "case_index": case_index,
            "case_id": case_id,
            "error_deg": error_deg,
            "seed": seed,
            **estimator,
        })
        print(
            f"[servo][stage2] {case_index}/{len(errors) * len(seeds)} "
            f"error={error_deg:+.3f}deg seed={seed} "
            f"estimate={estimator.get('estimated_true_minus_reported_deg')} "
            f"status={estimator.get('fit_status')}",
            flush=True,
        )

    zero_estimates = [
        float(row["estimated_true_minus_reported_deg"])
        for row in estimate_rows
        if abs(float(row["error_deg"])) < 1.0e-12
        and row.get("fit_status") == "fit"
        and row.get("estimated_true_minus_reported_deg") is not None
    ]
    if not zero_estimates:
        raise RuntimeError("servo pilot has no fit at zero error; cannot derive fallback deadband")
    deadband_deg = max(abs(value) for value in zero_estimates)
    decision_rows: list[dict[str, object]] = []
    branch_inputs: list[tuple[dict[str, object], str, Path, dict[str, object]]] = []
    for case in case_records:
        estimator = case["estimator"]
        if not isinstance(estimator, Mapping):
            raise RuntimeError("malformed estimator record")
        estimate = estimator.get("estimated_true_minus_reported_deg")
        uncertainty = estimator.get("uncertainty_deg")
        fit_status = estimator.get("fit_status")
        if fit_status != "fit" or estimate is None:
            action = "FALLBACK_UNIDENTIFIABLE"
            applied = 0.0
            decision_uncertainty = None
        else:
            estimate_value = float(estimate)
            uncertainty_value = float(uncertainty) if uncertainty is not None else 0.0
            threshold = max(deadband_deg, uncertainty_value)
            if abs(estimate_value) <= threshold:
                action = "NO_CORRECTION_NEEDED"
                applied = 0.0
            else:
                action = "APPLY_ESTIMATED_CORRECTION"
                applied = estimate_value
            decision_uncertainty = uncertainty_value
        decision = {
            "case_id": case["case_id"],
            "error_deg": case["error_deg"],
            "seed": case["seed"],
            "fit_status": fit_status,
            "estimated_error_deg": estimate,
            "uncertainty_deg": decision_uncertainty,
            "zero_error_deadband_deg": deadband_deg,
            "decision": action,
            "applied_unknown_correction_deg": applied,
            "correction_source": "none_fallback_current" if applied == 0.0 else "unknown_pilot_estimate",
            "truth_used_for_decision": False,
        }
        decision_rows.append(decision)
        case["unknown_decision"] = decision
        if not skip_core:
            packet_len = int(template["waveform"]["pulse_len"]) * int(template["waveform"]["new_protocol_channel_count"]) * 2 * 4 + HEADER_BYTES  # type: ignore[index]
            raw_on = Path(case["on_raw"])
            if action == "APPLY_ESTIMATED_CORRECTION":
                unknown_raw = Path(case["case_root"]) / "corrections/unknown/on.bin"
                header_audit = _rewrite_header_angle_with_layout(
                    raw_on, unknown_raw, packet_len, applied
                )
            else:
                unknown_raw = raw_on
                header_audit = {
                    "source": str(raw_on),
                    "destination": str(raw_on),
                    "correction_deg": 0.0,
                    "payload_unchanged": True,
                    "fallback_current": True,
                    "raw_sha256": _sha256(raw_on),
                }
            known_raw = Path(case["case_root"]) / "corrections/known/on.bin"
            known_audit = _rewrite_header_angle_with_layout(
                raw_on, known_raw, packet_len, float(case["error_deg"])
            )
            case["known_header_audit"] = known_audit
            case["unknown_header_audit"] = header_audit
            branch_inputs.extend([
                (case, "current_reported", raw_on, {"correction_source": "reported_header"}),
                (case, "known_true_header", known_raw, {"correction_source": "known_error_upper_bound"}),
                (case, "unknown_estimated_or_fallback", unknown_raw, {"correction_source": decision["correction_source"]}),
            ])
    _write_rows(output_root / "case_index.csv", case_rows)
    _write_rows(output_root / "servo_estimates.csv", estimate_rows)
    _write_rows(output_root / "servo_decisions.csv", decision_rows)

    core_rows: list[dict[str, object]] = []
    if not skip_core:
        for index, (case, branch, raw_path, branch_meta) in enumerate(branch_inputs, 1):
            print(
                f"[servo][cuda] {index}/{len(branch_inputs)} "
                f"{case['case_id']} {branch}", flush=True
            )
            row = _run_core_branch(case, branch, raw_path, core)
            row.update(branch_meta)
            core_rows.append(row)
            if row["status"] != "completed":
                raise RuntimeError(f"GMTI_core failed for {case['case_id']} {branch}; see {row['log_path']}")
    _write_rows(output_root / "core_metrics.csv", core_rows)
    all_core_ok = skip_core or all(row.get("status") == "completed" for row in core_rows)
    source_status = _git("status", "--short", "--untracked-files=all")
    source_commit = source_commit_override or _git("rev-parse", "HEAD")
    source_dirty = (
        bool(source_status)
        if worktree_dirty_override is None
        else bool(worktree_dirty_override)
    )
    manifest: dict[str, object] = {
        "schema": "unknown_system_error_servo_angle_pilot_v1",
        "status": "completed" if all_core_ok else "completed_with_failed_core",
        "ai_training": False,
        "ai_router": False,
        "unknown_estimator": {
            "name": "six_pair_phase_plus_multibeam_power_quadratic_pilot",
            "operational": False,
            "truth_used": False,
            "inputs": [
                "paired ON-OFF target residual",
                "six pair cross-channel phases/coherences",
                "reported header beam angles",
                "nominal waveform/range/beam metadata",
            ],
            "correction": "header-only evaluation copy; raw physical payload is never edited",
        },
        "known_error_upper_bound": {
            "used_only_for_evaluation": True,
            "source": "configured true_minus_reported_deg",
            "payload_unchanged_required": True,
        },
        "servo_model": {
            "true_angle_definition": "theta_true_deg = theta_reported_deg + true_minus_reported_deg",
            "physical_echo_and_beam_gain_use": "theta_true_deg",
            "protocol_header_and_core_input_use": "theta_reported_deg",
            "truth_artifact": "truth/servo_angle_truth.csv",
            "geometry_error_confounding": "disabled: true and reported receiver positions identical",
        },
        "levels": {
            "errors_deg": errors,
            "seeds": seeds_int,
            "beam_count": 5,
            "scan_min_deg": -4.0,
            "scan_step_deg": 2.0,
            "beam_width_deg": 4.0,
        },
        "fallback_policy": {
            "zero_error_deadband_deg": deadband_deg,
            "deadband_source": "maximum absolute zero-error pilot estimate; no empirical subtraction",
            "uncertainty_rule": "fallback when abs(estimate) <= max(deadband, bootstrap_uncertainty)",
            "unidentifiable_rule": "FALLBACK_UNIDENTIFIABLE",
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
        },
        "counts": {
            "case_count": len(case_records),
            "stage2_completed": len(case_records),
            "estimate_rows": len(estimate_rows),
            "core_rows": len(core_rows),
            "core_success": all_core_ok,
            "decision_counts": {
                key: sum(1 for row in decision_rows if row["decision"] == key)
                for key in ("NO_CORRECTION_NEEDED", "APPLY_ESTIMATED_CORRECTION", "FALLBACK_UNIDENTIFIABLE")
            },
        },
        "artifacts": {
            "prepared_template": str((output_root / "prepared_template.json").resolve()),
            "case_index": str((output_root / "case_index.csv").resolve()),
            "servo_estimates": str((output_root / "servo_estimates.csv").resolve()),
            "servo_decisions": str((output_root / "servo_decisions.csv").resolve()),
            "core_metrics": str((output_root / "core_metrics.csv").resolve()),
        },
        "case_records": case_records,
        "limitations": [
            "This is a deterministic estimator pilot, not a production online servo estimator.",
            "The pilot isolates servo pointing with no receiver geometry error and no area clutter; platform velocity and coupled nuisance matrix are pending.",
            "The estimator uses paired target residuals and a configured nominal target/range hypothesis; it is not a blind scene-wide detector.",
            "No causal TrackManager/PIPE acceptance or servo-specific Pd/Pfa claim is made here.",
        ],
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


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


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root", type=Path,
        default=ROOT / "outputs/unknown_system_error_servo_pilot_20260914_v1",
    )
    parser.add_argument("--simulator", type=Path, default=SIMULATOR)
    parser.add_argument("--gmticore", type=Path, default=GMTI_CORE)
    parser.add_argument("--errors-deg", nargs="+", default=[str(value) for value in DEFAULT_ERRORS_DEG])
    parser.add_argument("--seeds", nargs="+", default=[str(value) for value in DEFAULT_SEEDS])
    parser.add_argument("--skip-core", action="store_true", help="generate and estimate only; never claim CUDA results")
    parser.add_argument("--source-commit", default=None, help="override recorded source commit for clean-archive runs")
    parser.add_argument(
        "--worktree-dirty-before", choices=("true", "false"), default=None,
        help="override recorded source dirty state for clean-archive runs",
    )
    args = parser.parse_args(argv)
    errors = _parse_float_list(args.errors_deg, "errors-deg")
    seeds = _parse_int_list(args.seeds, "seeds")
    manifest = run_pilot(
        args.output_root.resolve(),
        args.simulator.resolve(),
        args.gmticore.resolve(),
        errors,
        seeds,
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
        "deadband_deg": manifest["fallback_policy"]["zero_error_deadband_deg"],
    }, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
