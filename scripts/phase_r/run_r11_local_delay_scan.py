#!/usr/bin/env python3
"""Prepare, execute and evaluate the frozen seed303 R11 Native CUDA delay scan."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from scripts import run_track_manager_e2e as e2e  # noqa: E402
from scripts.estimate_channel_delay import rewrite_float32_protocol_delay  # noqa: E402
from scripts.gmti_eval_match import one_to_one_match  # noqa: E402
from scripts.run_delay_stage1_formal import audit_fused_input_closure  # noqa: E402


OUT = ROOT / "outputs/phase_r11_local_delay_scan_20261024"
CASE = ROOT / "outputs/phase_r8_unknown_delay_multiseed_20261023/seed303/cases/base__seed_303__v_p6d7__snr_p30__delay_p4ns"
RAW_A0 = CASE / "scenes/A0_ON/data/stage2_statistical_newprotocol_period_0001.bin"
RAW_A1 = CASE / "scenes/A1_ON/data/stage2_statistical_newprotocol_period_0001.bin"
TRUTH_A0 = CASE / "scenes/A0_ON/truth/truth_targets_by_beam.csv"
TRUTH_A1 = CASE / "scenes/A1_ON/truth/truth_targets_by_beam.csv"
SOURCE_XML = CASE / "scenes/A0_ON/config/temp_config_stage2_period_0000.xml"
A0_BRANCH = CASE / "production/A0_Ideal_ON"
R8_KNOWN_BRANCH_MANIFEST = CASE / "production/A2_Known_error_correction_upper_bound_ON/branch_manifest.json"
R7_BINARY = ROOT / "build_r7/GMTI_pipe_core"
EXPECTED_BINARY_SHA256 = "de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00"
EXPECTED_A1_SHA256 = "f7506d535f54e77da8f74843b7cf2e7506e656f45d2430ab81ee6bfb6c236a0f"
EXPECTED_A0_SHA256 = "8f2e089f89ad29080146837cd93d964e29ed1daf6e39b17a2e472a2f42905b81"
DELAYS_NS = (3.009965, 3.209965, 3.409965, 3.609965, 3.809965, 4.000000)
TRUE_DELAY_NS = 4.0
FS_HZ = 60_000_000.0
PULSE_LEN = 11_820
CHANNEL_COUNT = 4
PACKET_COUNT = 130
BEAM_ID = 50
PERIOD_ID = 1
RESULT_ID = 2
TARGET_ROW = 48
TARGET_COL = 2154
CFAR_INPUT_TOLERANCE = 1.0e-3
KNOWN_PHASE_TOLERANCE_RAD = 1.0e-3
KNOWN_POSITION_TOLERANCE_M = 1.0
KNOWN_ANGLE_TOLERANCE_DEG = 1.0e-3
JACOBIAN_CROSSRANGE_TOLERANCE_M = 1.0
LAYOUT = {
    "pulse_len": PULSE_LEN,
    "channel_count": CHANNEL_COUNT,
    "read_channel_1": 1,
    "read_channel_2": 2,
    "iq_data_type": "float32",
    "enable_four_channel_fusion": True,
    "four_channel_phase_compensation_enable": True,
    "fusion_channel_3": 3,
    "fusion_channel_4": 4,
    "fs_hz": FS_HZ,
    "beam_id": BEAM_ID,
}
XML_OVERRIDES = {
    "csi_metrics_enable": "true",
    "csi_metrics_dump_power_maps": "true",
    "csi_metrics_dump_intermediate_maps": "true",
    "csi_metrics_beam_id": BEAM_ID,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def csv_write(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(str(key))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def git_identity() -> dict[str, Any]:
    def output(args: list[str]) -> str:
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)
        return result.stdout.strip()

    return {
        "commit": output(["git", "rev-parse", "HEAD"]),
        "branch": output(["git", "branch", "--show-current"]),
        "tracked_worktree_status": output(["git", "status", "--short", "--untracked-files=no"]),
        "runner_sha256": sha256(Path(__file__).resolve()),
    }


def target_truth(path: Path) -> dict[str, Any]:
    matching = [
        row for row in csv_rows(path)
        if row.get("period_id") == str(PERIOD_ID)
        and row.get("beam_id") == str(BEAM_ID)
        and row.get("target_id") == "stage1_target_1"
        and row.get("visible", "1") not in {"0", "false", "False"}
    ]
    if len(matching) != 1:
        raise RuntimeError(f"expected one visible R11 truth row in {path}, found {len(matching)}")
    return matching[0]


def protocol_channels(path: Path) -> np.ndarray:
    packet_bytes = 256 + PULSE_LEN * CHANNEL_COUNT * 2 * 4
    size = path.stat().st_size
    if size <= 0 or size % packet_bytes:
        raise ValueError(f"invalid protocol byte count: {path} ({size})")
    packets = size // packet_bytes
    if packets != PACKET_COUNT:
        raise ValueError(f"expected {PACKET_COUNT} packets in {path}, found {packets}")
    output = np.empty((packets, PULSE_LEN, CHANNEL_COUNT), dtype=np.complex64)
    with path.open("rb") as stream:
        for packet_index in range(packets):
            header = stream.read(256)
            payload = stream.read(packet_bytes - 256)
            if len(header) != 256 or len(payload) != packet_bytes - 256:
                raise ValueError(f"truncated packet {packet_index} in {path}")
            if int.from_bytes(header[9:13], "little") != packet_bytes:
                raise ValueError(f"packet {packet_index} declares the wrong packet size in {path}")
            iq = np.frombuffer(payload, dtype="<f4").reshape(PULSE_LEN, CHANNEL_COUNT, 2)
            output[packet_index] = iq[..., 0] + 1j * iq[..., 1]
    if not np.isfinite(output.real).all() or not np.isfinite(output.imag).all():
        raise ValueError(f"non-finite protocol IQ in {path}")
    return output


def audit_rewrite(source: Path, destination: Path) -> dict[str, Any]:
    packet_bytes = 256 + PULSE_LEN * CHANNEL_COUNT * 2 * 4
    changed_packets = 0
    with source.open("rb") as left, destination.open("rb") as right:
        for packet_index in range(PACKET_COUNT):
            header_left, header_right = left.read(256), right.read(256)
            payload_left = left.read(packet_bytes - 256)
            payload_right = right.read(packet_bytes - 256)
            if len(header_left) != 256 or len(header_right) != 256:
                raise ValueError("truncated protocol header during rewrite audit")
            if header_left != header_right:
                raise ValueError(f"packet header changed at packet {packet_index}")
            iq_left = np.frombuffer(payload_left, dtype="<f4").reshape(PULSE_LEN, 4, 2)
            iq_right = np.frombuffer(payload_right, dtype="<f4").reshape(PULSE_LEN, 4, 2)
            if not np.array_equal(iq_left[:, (0, 2, 3), :], iq_right[:, (0, 2, 3), :]):
                raise ValueError(f"non-target channel changed at packet {packet_index}")
            if not np.array_equal(iq_left[:, 1, :], iq_right[:, 1, :]):
                changed_packets += 1
    if source.stat().st_size != destination.stat().st_size:
        raise ValueError("input and corrected output sizes differ")
    corrected = protocol_channels(destination)
    return {
        "status": "passed",
        "packet_count": PACKET_COUNT,
        "header_bytes_unchanged": True,
        "channels_1_3_4_bitwise_unchanged": True,
        "channel_2_packets_changed": changed_packets,
        "all_iq_finite": True,
        "input_bytes": source.stat().st_size,
        "output_bytes": destination.stat().st_size,
        "output_channel_2_nonzero_samples": int(np.count_nonzero(corrected[:, :, 1])),
    }


def complex_metrics(reference: np.ndarray, candidate: np.ndarray) -> dict[str, float]:
    if reference.shape != candidate.shape or reference.size == 0:
        raise ValueError(f"complex array shape mismatch: {reference.shape} vs {candidate.shape}")
    ref = reference.astype(np.complex128, copy=False)
    cand = candidate.astype(np.complex128, copy=False)
    ref_power = float(np.mean(np.abs(ref) ** 2))
    if not math.isfinite(ref_power) or ref_power <= 0.0:
        raise ValueError("reference complex power is invalid")
    error = cand - ref
    gain = np.sum(cand * np.conj(ref)) / np.sum(np.abs(ref) ** 2)
    return {
        "relative_rms": float(math.sqrt(float(np.mean(np.abs(error) ** 2)) / ref_power)),
        "reference_mean_power": ref_power,
        "error_mean_power": float(np.mean(np.abs(error) ** 2)),
        "max_abs_error": float(np.max(np.abs(error))),
        "ls_gain_real": float(gain.real),
        "ls_gain_imag": float(gain.imag),
        "ls_gain_magnitude": float(abs(gain)),
        "ls_gain_phase_rad": float(np.angle(gain)),
    }


def input_closure(reference: np.ndarray, candidate: np.ndarray) -> dict[str, Any]:
    ref_f1 = 0.5 * (reference[:, :, 0] + reference[:, :, 2])
    ref_f2 = 0.5 * (reference[:, :, 1] + reference[:, :, 3])
    can_f1 = 0.5 * (candidate[:, :, 0] + candidate[:, :, 2])
    can_f2 = 0.5 * (candidate[:, :, 1] + candidate[:, :, 3])
    return {
        "ch1": complex_metrics(reference[:, :, 0], candidate[:, :, 0]),
        "ch2": complex_metrics(reference[:, :, 1], candidate[:, :, 1]),
        "ch3": complex_metrics(reference[:, :, 2], candidate[:, :, 2]),
        "ch4": complex_metrics(reference[:, :, 3], candidate[:, :, 3]),
        "F1": complex_metrics(ref_f1, can_f1),
        "F2": complex_metrics(ref_f2, can_f2),
    }


def known_reference_record() -> dict[str, Any]:
    manifest = json.loads(R8_KNOWN_BRANCH_MANIFEST.read_text(encoding="utf-8"))
    rows = manifest.get("period_paths", [])
    row = next((item for item in rows if str(item.get("path", "")).endswith("period_0001.bin")), None)
    if not isinstance(row, Mapping):
        return {"status": "NOT_AVAILABLE", "reason": "known_period1_input_not_in_manifest"}
    path = Path(str(row["path"]))
    actual = sha256(path) if path.is_file() else None
    return {
        "status": "available" if actual == row.get("sha256") else "manifest_only_or_hash_mismatch",
        "path": str(path),
        "bytes": row.get("bytes"),
        "sha256": row.get("sha256"),
        "actual_sha256": actual,
    }


def analytic_delay_response(correction_ns: float) -> dict[str, float]:
    frequencies = np.fft.fftfreq(PULSE_LEN, d=1.0 / FS_HZ)
    omega = 2.0 * np.pi * frequencies / FS_HZ
    true_d = TRUE_DELAY_NS * 1.0e-9 * FS_HZ
    correction_d = correction_ns * 1.0e-9 * FS_HZ
    h_true = (1.0 - true_d) + true_d * np.exp(-1j * omega)
    h_correction = (1.0 - correction_d) + correction_d * np.exp(-1j * omega)
    residual = h_true / h_correction
    passband = np.abs(frequencies) <= 25.0e6
    phase = np.angle(residual[passband])
    return {
        "residual_delay_ns": TRUE_DELAY_NS - correction_ns,
        "residual_samples": (TRUE_DELAY_NS - correction_ns) * 1.0e-9 * FS_HZ,
        "passband_magnitude_min": float(np.min(np.abs(residual[passband]))),
        "passband_magnitude_max": float(np.max(np.abs(residual[passband]))),
        "passband_phase_min_rad": float(np.min(phase)),
        "passband_phase_max_rad": float(np.max(phase)),
        "passband_phase_rms_rad": float(np.sqrt(np.mean(phase * phase))),
    }


def baseline_target() -> dict[str, Any]:
    detections_path = A0_BRANCH / "result/detection_results_GMTI02.csv"
    detections = [
        row for row in csv_rows(detections_path)
        if row.get("period_id") == str(PERIOD_ID) and row.get("beam_id") == str(BEAM_ID)
    ]
    exact = [row for row in detections if row.get("row") == str(TARGET_ROW) and row.get("col") == str(TARGET_COL)]
    if len(exact) != 1:
        raise RuntimeError(f"expected one A0 production detection at target cell, found {len(exact)}")
    real_maps = list((A0_BRANCH / "result/csi_metrics").glob("*/period001_beam050_csi_after_real.npy"))
    imag_maps = list((A0_BRANCH / "result/csi_metrics").glob("*/period001_beam050_csi_after_imag.npy"))
    if len(real_maps) != 1 or len(imag_maps) != 1:
        raise RuntimeError(f"expected one A0 period-1 CSI complex pair, got {len(real_maps)} real/{len(imag_maps)} imag")
    real_array = np.load(real_maps[0], mmap_mode="r")
    imag_array = np.load(imag_maps[0], mmap_mode="r")
    if real_array.shape != (130, 4096) or imag_array.shape != real_array.shape:
        raise RuntimeError(f"A0 CSI map shape mismatch: {real_array.shape}, {imag_array.shape}")
    z = complex(float(real_array[TARGET_ROW, TARGET_COL]), float(imag_array[TARGET_ROW, TARGET_COL]))
    return {
        "branch": str(A0_BRANCH),
        "detection_csv": str(detections_path),
        "detection_csv_sha256": sha256(detections_path),
        "detection": exact[0],
        "csi_after": {"real": z.real, "imag": z.imag},
        "csi_real_map": {"path": str(real_maps[0]), "sha256": sha256(real_maps[0])},
        "csi_imag_map": {"path": str(imag_maps[0]), "sha256": sha256(imag_maps[0])},
    }


def initial_manifest() -> dict[str, Any]:
    truth0, truth1 = target_truth(TRUTH_A0), target_truth(TRUTH_A1)
    truth_fields = ("e_mid", "n_mid", "range_bin", "expected_bin", "azimuth_deg")
    truth_match = all(truth0.get(key) == truth1.get(key) for key in truth_fields)
    if not truth_match:
        raise RuntimeError("A0/A1 period-1 target truth rows do not match")
    if sha256(RAW_A0) != EXPECTED_A0_SHA256 or sha256(RAW_A1) != EXPECTED_A1_SHA256:
        raise RuntimeError("seed303 period-1 raw input SHA does not match the frozen design")
    if sha256(R7_BINARY) != EXPECTED_BINARY_SHA256:
        raise RuntimeError("R7 Native CUDA production binary SHA does not match the frozen design")
    if not SOURCE_XML.is_file() or not TRUTH_A0.is_file() or not TRUTH_A1.is_file():
        raise FileNotFoundError("one or more R11 source configuration/truth files are missing")
    known = known_reference_record()
    if known.get("actual_sha256") != known.get("sha256"):
        raise RuntimeError("R8 Known Correction period-1 reference file is unavailable or hash-mismatched")
    return {
        "experiment": "PGRCC Phase R11 single-period physical channel-2 delay scan",
        "state": "DESIGN_FROZEN",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_identity": git_identity(),
        "binary": {"path": str(R7_BINARY), "sha256": sha256(R7_BINARY), "USE_COREX": "OFF"},
        "source_xml": {"path": str(SOURCE_XML), "sha256": sha256(SOURCE_XML)},
        "runner": {"path": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__).resolve())},
        "scene": {"seed": 303, "period_id": PERIOD_ID, "beam_id": BEAM_ID, "target_cell": [TARGET_ROW, TARGET_COL], "target_id": "stage1_target_1"},
        "raw_inputs": {
            "A0_ON": {"path": str(RAW_A0), "bytes": RAW_A0.stat().st_size, "sha256": sha256(RAW_A0)},
            "A1_ON_plus4ns": {"path": str(RAW_A1), "bytes": RAW_A1.stat().st_size, "sha256": sha256(RAW_A1)},
        },
        "truth": {
            "A0_path": str(TRUTH_A0), "A1_path": str(TRUTH_A1),
            "period1_beam50_rows_match": truth_match,
            "target_row": truth0,
        },
        "known_4ns_reference": known,
        "r8_a0_production_reference": baseline_target(),
        "correction_ns": list(DELAYS_NS),
        "operator": {
            "model": "inverse_stage2_zero_extended_linear_interpolator",
            "formula": "x[n]=(y[n]-d*x[n-1])/(1-d), x[0]=0, d=correction_ns*1e-9*fs_hz",
            "channel_index_zero_based": 1,
            "sampling_rate_hz": FS_HZ,
            "pulse_len": PULSE_LEN,
            "channel_count": CHANNEL_COUNT,
            "packet_count": PACKET_COUNT,
            "target_cell": [TARGET_ROW, TARGET_COL],
        },
        "frozen_gates": {
            "known_f1_f2_relative_rms_max": CFAR_INPUT_TOLERANCE,
            "known_raw_phase_abs_delta_max_rad": KNOWN_PHASE_TOLERANCE_RAD,
            "known_position_abs_delta_max_m": KNOWN_POSITION_TOLERANCE_M,
            "known_angle_abs_delta_max_deg": KNOWN_ANGLE_TOLERANCE_DEG,
            "jacobian_crossrange_abs_residual_max_m": JACOBIAN_CROSSRANGE_TOLERANCE_M,
            "production_requires": [
                "exit_code_zero", "runtime_xml_and_config_dump", "cuda_new_protocol_preprocess_log",
                "csi_cancellation_log", "cfar_summary_log", "timing_scope_log",
                "nonempty_period1_csi_summary", "period1_valid_cfar_cut_branch",
                "cluster_detection_lineage", "track_debug_files",
            ],
        },
        "branches": {},
    }


def manifest_path() -> Path:
    return OUT / "experiment_manifest.json"


def load_manifest() -> dict[str, Any]:
    path = manifest_path()
    if not path.is_file():
        raise FileNotFoundError(f"R11 manifest missing: {path}; run --mode prepare first")
    return json.loads(path.read_text(encoding="utf-8"))


def prepare() -> None:
    if OUT.exists() and manifest_path().exists():
        existing = load_manifest()
        if existing.get("state") not in {"DESIGN_FROZEN", "PREPARE_FAILED"}:
            raise FileExistsError(f"R11 output already progressed to {existing.get('state')}; preserve and resume explicitly")
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = initial_manifest()
    reference = protocol_channels(RAW_A0)
    correction_records: list[dict[str, Any]] = []
    gate_errors: list[str] = []
    for delay_ns in DELAYS_NS:
        condition = f"delay_{delay_ns:.6f}ns"
        branch_root = OUT / "conditions" / condition
        input_path = branch_root / "input" / "period_0001_corrected.bin"
        branch_root.mkdir(parents=True, exist_ok=True)
        rewrite_record = rewrite_float32_protocol_delay(
            RAW_A1,
            input_path,
            pulse_len=PULSE_LEN,
            channel_count=CHANNEL_COUNT,
            fs_hz=FS_HZ,
            delta_tau_sec=delay_ns * 1.0e-9,
            channel_index=1,
            simulator_linear_fractional_delay_inverse=True,
        )
        integrity = audit_rewrite(RAW_A1, input_path)
        candidate = protocol_channels(input_path)
        closure = input_closure(reference, candidate)
        function_audit = audit_fused_input_closure([RAW_A0], [input_path], LAYOUT, tolerance=CFAR_INPUT_TOLERANCE)
        known_hash_match = None
        if delay_ns == TRUE_DELAY_NS:
            known_hash_match = sha256(input_path) == manifest["known_4ns_reference"]["sha256"]
            if not known_hash_match:
                gate_errors.append("4ns_corrected_input_sha_mismatch_with_R8_Known")
            for channel in ("F1", "F2"):
                if closure[channel]["relative_rms"] > CFAR_INPUT_TOLERANCE:
                    gate_errors.append(f"4ns_{channel}_input_closure_exceeds_1e-3")
        correction_records.append({
            "correction_ns": delay_ns,
            "condition": condition,
            "raw_input_path": str(RAW_A1),
            "raw_input_sha256": sha256(RAW_A1),
            "corrected_input_path": str(input_path),
            "corrected_input_bytes": input_path.stat().st_size,
            "corrected_input_sha256": sha256(input_path),
            "rewrite_record": rewrite_record,
            "rewrite_integrity": integrity,
            "channel_and_fusion_closure": closure,
            "existing_closure_audit": function_audit,
            "analytic_residual_operator": analytic_delay_response(delay_ns),
            "known_reference_sha_match": known_hash_match,
        })
        json_write(branch_root / "input_preparation.json", correction_records[-1])
        print(f"prepared {condition}: SHA-256={correction_records[-1]['corrected_input_sha256']} F2_RMS={closure['F2']['relative_rms']:.8g}")
    csv_write(OUT / "input_correction_records.csv", [
        {
            "correction_ns": row["correction_ns"],
            "condition": row["condition"],
            "raw_input_sha256": row["raw_input_sha256"],
            "corrected_input_sha256": row["corrected_input_sha256"],
            "bytes": row["corrected_input_bytes"],
            "rewrite_status": row["rewrite_integrity"]["status"],
            "non_target_channels_bitwise_unchanged": row["rewrite_integrity"]["channels_1_3_4_bitwise_unchanged"],
            "ch2_relative_rms_to_A0": row["channel_and_fusion_closure"]["ch2"]["relative_rms"],
            "F1_relative_rms_to_A0": row["channel_and_fusion_closure"]["F1"]["relative_rms"],
            "F2_relative_rms_to_A0": row["channel_and_fusion_closure"]["F2"]["relative_rms"],
            "F2_phase_rms_to_A0_rad": row["channel_and_fusion_closure"]["F2"]["ls_gain_phase_rad"],
            "known_4ns_hash_match": row["known_reference_sha_match"],
        }
        for row in correction_records
    ])
    manifest["input_corrections"] = correction_records
    manifest["input_gate"] = {"status": "passed" if not gate_errors else "failed", "errors": gate_errors}
    manifest["state"] = "PREPARED" if not gate_errors else "PREPARE_FAILED"
    json_write(manifest_path(), manifest)
    if gate_errors:
        raise RuntimeError("R11 input gate failed: " + ", ".join(gate_errors))


def nvidia_snapshot(label: str) -> dict[str, Any]:
    device_dir = OUT / "device"
    device_dir.mkdir(parents=True, exist_ok=True)
    query_args = [
        "nvidia-smi",
        "--query-gpu=name,driver_version,pstate,temperature.gpu,power.draw,power.limit,utilization.gpu,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]
    query = subprocess.run(query_args, cwd=ROOT, capture_output=True, text=True, check=False)
    details = subprocess.run(["nvidia-smi", "-q"], cwd=ROOT, capture_output=True, text=True, check=False)
    (device_dir / f"nvidia-smi-{label}.txt").write_text(
        "$ " + shlex.join(["nvidia-smi", "-q"]) + "\n" + details.stdout + details.stderr,
        encoding="utf-8",
    )
    if query.returncode != 0 or details.returncode != 0:
        raise RuntimeError(f"nvidia-smi failed during {label}: {query.stderr or details.stderr}")
    return {
        "query": query.stdout.strip(),
        "details_path": str(device_dir / f"nvidia-smi-{label}.txt"),
        "details_sha256": sha256(device_dir / f"nvidia-smi-{label}.txt"),
        "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def normalized_xml_sha(path: Path) -> str:
    root = ET.parse(path).getroot()
    ignored = {"result_add", "track_debug_dir"}
    normalized = [(node.tag, (node.text or "").strip()) for node in root.iter() if node.tag not in ignored]
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def production_gate(branch_root: Path, return_code: int) -> dict[str, Any]:
    result_dir = branch_root / "result"
    log_path = branch_root / "gmt_pipe_core.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    csi_paths = sorted(result_dir.rglob("csi_metric_tap_summary.csv")) if result_dir.is_dir() else []
    csi_rows = [row for path in csi_paths for row in csv_rows(path)]
    csi_period1 = [
        row for row in csi_rows
        if row.get("period_id") == str(PERIOD_ID) and row.get("beam_id") == str(BEAM_ID)
    ]
    geometry_path = result_dir / "debug/cfar_geometry_diagnostics.csv"
    geometry_rows = [
        row for row in csv_rows(geometry_path)
        if row.get("period_id") == str(PERIOD_ID) and row.get("beam_id") == str(BEAM_ID)
    ]
    valid_geometry = [
        row for row in geometry_rows
        if (int(row.get("valid_cut_count", "0") or 0) > 0)
        and row.get("hit_rate_status") == "evaluable"
    ]
    stage_counts = {
        "cuda_preprocess": log_text.count("new_protocol_gpu_preprocess: enabled"),
        "csi_cancellation": log_text.count("[CSI][CANCELLATION]"),
        "cfar_summary": log_text.count("[CFAR][SUMMARY]"),
        "timing_scope": log_text.count("[TIMING-SCOPE]"),
    }
    debug_dir = branch_root / "track_debug_runs"
    debug_files = sorted(str(path) for path in debug_dir.rglob("*.csv")) if debug_dir.exists() else []
    required_debug = ("track_frames.csv", "track_detections.csv", "track_output_payloads.csv")
    reasons: list[str] = []
    if return_code != 0:
        reasons.append("production_exit_code_nonzero")
    if not (branch_root / "production.xml").is_file():
        reasons.append("runtime_xml_missing")
    if not (result_dir / "runtime_config_dump.json").is_file():
        reasons.append("runtime_config_dump_missing")
    if not log_path.is_file():
        reasons.append("production_log_missing")
    for name, count in stage_counts.items():
        if count <= 0:
            reasons.append(f"production_log_missing_{name}")
    if not csi_period1:
        reasons.append("period1_csi_summary_missing")
    if not geometry_path.is_file() or not valid_geometry:
        reasons.append("period1_valid_cfar_cut_branch_missing")
    for name in required_debug:
        if not any(Path(path).name == name for path in debug_files):
            reasons.append(f"track_debug_missing_{name}")
    return {
        "status": "passed" if not reasons else "NOT_EVALUABLE",
        "reasons": reasons,
        "production_returncode": return_code,
        "runtime_xml_exists": (branch_root / "production.xml").is_file(),
        "runtime_config_dump_exists": (result_dir / "runtime_config_dump.json").is_file(),
        "production_log": str(log_path),
        "production_log_sha256": sha256(log_path) if log_path.is_file() else None,
        "runtime_log_stage_counts": stage_counts,
        "csi_summary_paths": [str(path) for path in csi_paths],
        "csi_summary_row_count": len(csi_rows),
        "period1_csi_row_count": len(csi_period1),
        "cfar_geometry_path": str(geometry_path),
        "period1_cfar_geometry_rows": geometry_rows,
        "valid_period1_cfar_branches": [row.get("branch") for row in valid_geometry],
        "valid_cut_count_by_branch": {row.get("branch"): int(row["valid_cut_count"]) for row in valid_geometry},
        "cfar_numerical_diagnostics_exists": (result_dir / "debug/cfar_numerical_diagnostics.csv").is_file(),
        "cluster_detection_lineage_exists": (result_dir / "debug/cluster_detection_lineage.csv").is_file(),
        "track_debug_files": debug_files,
    }


def extract_target_cell_taps(branch_root: Path, delay_ns: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    result_dir = branch_root / "result"
    real_maps = list(result_dir.rglob(f"period{PERIOD_ID:03d}_beam{BEAM_ID:03d}_csi_after_real.npy"))
    imag_maps = list(result_dir.rglob(f"period{PERIOD_ID:03d}_beam{BEAM_ID:03d}_csi_after_imag.npy"))
    if len(real_maps) != 1 or len(imag_maps) != 1:
        raise RuntimeError(f"expected one CSI after map pair for period {PERIOD_ID}, found {len(real_maps)}/{len(imag_maps)}")
    csi_dir = real_maps[0].parent
    map_files = sorted(csi_dir.glob(f"period{PERIOD_ID:03d}_beam{BEAM_ID:03d}_*.npy"))
    map_records: list[dict[str, Any]] = []
    values: dict[str, dict[str, Any]] = {}
    cleanup: list[dict[str, Any]] = []
    for path in map_files:
        array = np.load(path, mmap_mode="r")
        record = {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "target_cell_value": None,
        }
        if array.ndim == 2 and array.shape == (130, 4096):
            value = float(array[TARGET_ROW, TARGET_COL])
            record["target_cell_value"] = value
            stem = path.name.removesuffix(".npy").removeprefix(f"period{PERIOD_ID:03d}_beam{BEAM_ID:03d}_")
            suffix = "real" if stem.endswith("_real") else "imag" if stem.endswith("_imag") else None
            base = stem.rsplit("_", 1)[0] if suffix else stem
            values.setdefault(base, {})[suffix or "value"] = value
            values[base][suffix + "_map" if suffix else "value_map"] = record
            cleanup.append({**record, "action": "removed_after_verified_cell_extraction"})
        else:
            record["retention"] = "small_axis_or_non_grid_map"
        map_records.append(record)
    tap_rows: list[dict[str, Any]] = []
    for tap_name, tap in sorted(values.items()):
        if "real" in tap and "imag" in tap:
            z = complex(tap["real"], tap["imag"])
            tap_rows.append({
                "delay_ns": delay_ns,
                "period_id": PERIOD_ID,
                "beam_id": BEAM_ID,
                "row": TARGET_ROW,
                "col": TARGET_COL,
                "tap": tap_name,
                "real": z.real,
                "imag": z.imag,
                "magnitude": abs(z),
                "phase_rad": float(np.angle(z)),
                "real_map_path": tap["real_map"]["path"],
                "real_map_sha256": tap["real_map"]["sha256"],
                "imag_map_path": tap["imag_map"]["path"],
                "imag_map_sha256": tap["imag_map"]["sha256"],
            })
        elif "value" in tap:
            tap_rows.append({
                "delay_ns": delay_ns,
                "period_id": PERIOD_ID,
                "beam_id": BEAM_ID,
                "row": TARGET_ROW,
                "col": TARGET_COL,
                "tap": tap_name,
                "real": tap["value"],
                "imag": "",
                "magnitude": abs(tap["value"]),
                "phase_rad": "",
                "real_map_path": tap["value_map"]["path"],
                "real_map_sha256": tap["value_map"]["sha256"],
                "imag_map_path": "",
                "imag_map_sha256": "",
            })
    if not any(row["tap"] == "csi_after" for row in tap_rows):
        raise RuntimeError("csi_after complex value was not extracted from the registered target cell")
    for record in cleanup:
        Path(record["path"]).unlink()
    return tap_rows, map_records


def run_logged(command: list[str], log_path: Path, env: Mapping[str, str], timeout_s: float = 1800.0) -> tuple[int, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as stream:
        stream.write("$ USE_COREX=OFF " + shlex.join(command) + "\n")
        stream.flush()
        try:
            result = subprocess.run(
                command, cwd=ROOT, env=dict(env), stdout=stream, stderr=subprocess.STDOUT,
                check=False, timeout=timeout_s,
            )
            return_code = result.returncode
        except subprocess.TimeoutExpired:
            stream.write("\n[timeout_expired]=true\n")
            return_code = 124
    elapsed = time.monotonic() - started
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(f"\n[exit_code]={return_code}\n[elapsed_sec]={elapsed:.3f}\n")
    return return_code, elapsed


def run_production(attempt: int = 1) -> None:
    manifest = load_manifest()
    if attempt < 1:
        raise ValueError("attempt must be a positive integer")
    prior_state = manifest.get("state")
    allowed_states = {"PREPARED", "PRODUCTION_IN_PROGRESS"} if attempt == 1 else {
        "PRODUCTION_STOPPED_GATE_FAILURE", "PRODUCTION_STOPPED_CONFIGURATION_FAILURE"
    }
    if prior_state not in allowed_states:
        raise RuntimeError(f"R11 input gate is not passed: state={manifest.get('state')}")
    if manifest.get("input_gate", {}).get("status") != "passed":
        raise RuntimeError("R11 input gate did not pass")
    if sha256(R7_BINARY) != EXPECTED_BINARY_SHA256:
        raise RuntimeError("R7 binary changed after design freeze")
    if os.environ.get("USE_COREX", "OFF").upper() != "OFF":
        raise RuntimeError("USE_COREX must remain OFF for the local native CUDA run")
    attempts = manifest.setdefault("production_attempts", {})
    if prior_state.startswith("PRODUCTION_STOPPED"):
        original_runner = manifest.get("runner", {})
        attempts.setdefault("01", {
            "state": prior_state,
            "branches": manifest.get("branches", {}),
            "gpu_before": manifest.get("gpu_before"),
            "runner_sha256": original_runner.get("sha256"),
            "note": "preserved failed attempt; no science comparison because standalone input period offset was invalid",
        })
    attempt_key = f"{attempt:02d}"
    if attempt_key in attempts and attempts[attempt_key].get("state") != "PRODUCTION_IN_PROGRESS":
        raise FileExistsError(f"R11 production attempt {attempt_key} already exists; preserve it")
    if attempt > 1:
        manifest["branches"] = {}
    attempt_record = attempts.setdefault(attempt_key, {"attempt": attempt, "branches": {}})
    attempt_record["state"] = "PRODUCTION_IN_PROGRESS"
    attempt_record["runner_sha256"] = sha256(Path(__file__).resolve())
    attempt_record["local_test_selector"] = f"{RESULT_ID}=<standalone period-1 input>"
    attempt_record["input_file_period_index"] = 0
    attempt_record["stage2_period_id"] = PERIOD_ID
    attempt_record["gpu_before"] = nvidia_snapshot(f"before-attempt-{attempt:02d}")
    manifest["runner"] = {"path": str(Path(__file__).resolve()), "sha256": attempt_record["runner_sha256"]}
    manifest["gpu_before"] = attempt_record["gpu_before"]
    manifest["state"] = "PRODUCTION_IN_PROGRESS"
    json_write(manifest_path(), manifest)
    expected_semantic_xml_sha = None
    for delay_ns in DELAYS_NS:
        condition = f"delay_{delay_ns:.6f}ns"
        branch_root = (OUT / "conditions" if attempt == 1 else OUT / f"attempt_{attempt:02d}/conditions") / condition
        input_path = OUT / "conditions" / condition / "input/period_0001_corrected.bin"
        branch_manifest_path = branch_root / "branch_manifest.json"
        if branch_manifest_path.is_file():
            prior = json.loads(branch_manifest_path.read_text(encoding="utf-8"))
            if prior.get("production_gate", {}).get("status") == "passed":
                continue
            raise FileExistsError(f"branch already exists without a passing gate; preserve it: {branch_root}")
        result_dir = branch_root / "result"
        track_debug_dir = branch_root / "track_debug"
        branch_root.mkdir(parents=True, exist_ok=True)
        xml_path = branch_root / "production.xml"
        configure_rc, configure_log = e2e._configure_xml(
            SOURCE_XML, xml_path, result_dir, track_debug_dir, LAYOUT, XML_OVERRIDES
        )
        xml_audit = e2e._audit_runtime_xml_layout(xml_path, LAYOUT) if xml_path.is_file() else {"status": "missing"}
        semantic_sha = normalized_xml_sha(xml_path) if xml_path.is_file() else None
        if configure_rc != 0 or xml_audit.get("status") != "passed":
            branch = {
                "condition": condition, "correction_ns": delay_ns, "status": "configuration_failed",
                "configure_returncode": configure_rc, "configure_log": configure_log,
                "runtime_xml": str(xml_path), "runtime_xml_audit": xml_audit,
            }
            json_write(branch_manifest_path, branch)
            manifest["branches"][condition] = branch
            manifest["state"] = "PRODUCTION_STOPPED_CONFIGURATION_FAILURE"
            json_write(manifest_path(), manifest)
            raise RuntimeError(f"R11 runtime XML gate failed for {condition}")
        if expected_semantic_xml_sha is None:
            expected_semantic_xml_sha = semantic_sha
        elif semantic_sha != expected_semantic_xml_sha:
            raise RuntimeError(f"R11 runtime XML semantics changed for {condition}")
        command = [
            str(R7_BINARY), "--config", str(xml_path), "--result-dir", str(result_dir),
            "--track-debug-dir", str(branch_root / "track_debug_runs"),
            "--track-debug-dump", "on", "--runtime-mode=debug", "--runtime-diagnostics=on",
            "--local-test", f"{RESULT_ID}={input_path}",
        ]
        env = os.environ.copy()
        env["USE_COREX"] = "OFF"
        env["GMTI_RUNTIME_MODE"] = "debug"
        log_path = branch_root / "gmt_pipe_core.log"
        return_code, elapsed = run_logged(command, log_path, env)
        gate = production_gate(branch_root, return_code)
        branch = {
            "condition": condition,
            "attempt": attempt,
            "correction_ns": delay_ns,
            "status": "EXECUTED" if return_code == 0 else "FAILED",
            "command": {"argv": command, "shell": shlex.join(command), "environment": {"USE_COREX": "OFF", "GMTI_RUNTIME_MODE": "debug"}},
            "production_binary": {"path": str(R7_BINARY), "sha256": sha256(R7_BINARY)},
            "input": {"path": str(input_path), "bytes": input_path.stat().st_size, "sha256": sha256(input_path)},
            "source_xml": {"path": str(SOURCE_XML), "sha256": sha256(SOURCE_XML)},
            "runtime_xml": {"path": str(xml_path), "sha256": sha256(xml_path), "semantic_sha256": semantic_sha},
            "runtime_xml_audit": xml_audit,
            "input_file_period_index": 0,
            "stage2_period_id": PERIOD_ID,
            "configure_returncode": configure_rc,
            "configure_log": str(configure_log),
            "production_log": str(log_path),
            "production_elapsed_sec": elapsed,
            "production_gate": gate,
            "status": "passed" if gate["status"] == "passed" else "NOT_EVALUABLE",
        }
        if gate["status"] == "passed":
            try:
                tap_rows, map_records = extract_target_cell_taps(branch_root, delay_ns)
                branch["target_cell_tap_count"] = len(tap_rows)
                branch["csi_map_inventory"] = map_records
                branch["csi_maps_cleanup"] = [
                    {"path": row["path"], "bytes": row["bytes"], "sha256": row["sha256"], "action": "removed_after_verified_cell_extraction"}
                    for row in map_records if row.get("target_cell_value") is not None
                ]
                tap_csv = OUT / "target_cell_csi_responses.csv"
                existing = csv_rows(tap_csv)
                csv_write(tap_csv, existing + tap_rows)
                cleanup_rows = csv_rows(OUT / "csi_map_cleanup_ledger.csv")
                cleanup_rows.extend(branch["csi_maps_cleanup"])
                csv_write(OUT / "csi_map_cleanup_ledger.csv", cleanup_rows)
                branch["target_cell_csi_response_csv"] = str(tap_csv)
            except Exception as exc:
                branch["target_cell_extraction_error"] = f"{type(exc).__name__}: {exc}"
                branch["status"] = "NOT_EVALUABLE"
        json_write(branch_manifest_path, branch)
        manifest["branches"][condition] = branch
        attempt_record["branches"][condition] = branch
        manifest["state"] = "PRODUCTION_IN_PROGRESS" if branch["status"] == "passed" else "PRODUCTION_STOPPED_GATE_FAILURE"
        attempt_record["state"] = manifest["state"]
        json_write(manifest_path(), manifest)
        print(f"production {condition}: {branch['status']} ({elapsed:.2f}s)")
        if branch["status"] != "passed":
            raise RuntimeError(f"R11 production gate failed at {condition}; preserving branch evidence")
    attempt_record["gpu_after"] = nvidia_snapshot(f"after-attempt-{attempt:02d}")
    attempt_record["state"] = "PRODUCTION_EXECUTED"
    manifest["gpu_after"] = attempt_record["gpu_after"]
    manifest["state"] = "PRODUCTION_EXECUTED"
    manifest["runtime_xml_semantic_sha256"] = expected_semantic_xml_sha
    json_write(manifest_path(), manifest)


def wrap_rad(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def localization_metrics(detection: Mapping[str, Any], truth: Mapping[str, Any]) -> dict[str, float]:
    platform_e = float(detection["platform_e"])
    platform_n = float(detection["platform_n"])
    truth_e = float(truth["e_mid"])
    truth_n = float(truth["n_mid"])
    estimate_e = float(detection["new_e"])
    estimate_n = float(detection["new_n"])
    de, dn = truth_e - platform_e, truth_n - platform_n
    distance = math.hypot(de, dn)
    if distance <= 0:
        raise ValueError("truth LOS has zero EN length")
    xe, xn = estimate_e - truth_e, estimate_n - truth_n
    truth_angle = math.atan2(dn, de)
    estimate_angle = math.atan2(estimate_n - platform_n, estimate_e - platform_e)
    angle_error = math.degrees(wrap_rad(estimate_angle - truth_angle))
    unit_e, unit_n = de / distance, dn / distance
    crossrange_error = -xe * unit_n + xn * unit_e
    range_error = xe * unit_e + xn * unit_n
    return {
        "angle_error_deg": angle_error,
        "crossrange_error_m": crossrange_error,
        "range_error_m": range_error,
        "position_error_2d_m": math.hypot(xe, xn),
        "estimate_e": estimate_e,
        "estimate_n": estimate_n,
        "truth_e": truth_e,
        "truth_n": truth_n,
        "platform_e": platform_e,
        "platform_n": platform_n,
    }


def run_match(detections: list[dict[str, str]], truth: list[dict[str, str]]) -> dict[str, Any] | None:
    if not detections or not truth:
        return None
    match_config = json.loads((ROOT / "configs/eval/localization_match_config.json").read_text(encoding="utf-8"))
    matches, _, _ = one_to_one_match(
        detections, truth, match_config, "det_id", use_beam=True, use_range=True, use_velocity=False
    )
    if not matches:
        return None
    item = matches[0]
    return {
        "detection": detections[int(item["item_index"])],
        "truth": truth[int(item["truth_index"])],
        "match_record": item,
    }


def target_tap(delay_ns: float, tap_name: str) -> dict[str, Any] | None:
    rows = csv_rows(OUT / "target_cell_csi_responses.csv")
    values = [row for row in rows if float(row.get("delay_ns", "nan")) == delay_ns and row.get("tap") == tap_name]
    return values[0] if len(values) == 1 else None


def analyze() -> None:
    manifest = load_manifest()
    if manifest.get("state") not in {"PRODUCTION_EXECUTED", "EVALUATED"}:
        raise RuntimeError(f"R11 production is incomplete: {manifest.get('state')}")
    if len(manifest.get("branches", {})) != len(DELAYS_NS):
        raise RuntimeError("R11 requires all six frozen production branches")
    for condition, branch in manifest["branches"].items():
        if branch.get("production_gate", {}).get("status") != "passed":
            raise RuntimeError(f"R11 production gate did not pass for {condition}")
    truth_row = target_truth(TRUTH_A0)
    truth = [row for row in csv_rows(TRUTH_A0) if row.get("period_id") == str(PERIOD_ID) and row.get("beam_id") == str(BEAM_ID) and row.get("visible", "1") not in {"0", "false", "False"}]
    baseline = manifest["r8_a0_production_reference"]["detection"]
    baseline_loc = localization_metrics(baseline, truth_row)
    baseline_phase = float(baseline["ctdr_raw_phase_rad"])
    baseline_csi = complex(manifest["r8_a0_production_reference"]["csi_after"]["real"], manifest["r8_a0_production_reference"]["csi_after"]["imag"])
    baseline_jacobian = float(baseline["loc_jacobian_crossrange_m_per_phase_rad"])
    try:
        stage1 = __import__("scripts.run_delay_stage1_formal", fromlist=["_load_fused_from_periods"])
        a0_f1, a0_f2 = stage1._load_fused_from_periods([RAW_A0], LAYOUT)
        a1_f1, a1_f2 = stage1._load_fused_from_periods([RAW_A1], LAYOUT)
    except Exception as exc:
        raise RuntimeError(f"cannot calculate raw A1 input closure: {exc}") from exc
    raw_a1_closure = {
        "F1_relative_rms": complex_metrics(a0_f1, a1_f1)["relative_rms"],
        "F2_relative_rms": complex_metrics(a0_f2, a1_f2)["relative_rms"],
    }

    metrics_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for delay_ns in DELAYS_NS:
        condition = f"delay_{delay_ns:.6f}ns"
        branch = manifest["branches"][condition]
        result_dir = Path(branch["runtime_xml"]["path"]).parent / "result"
        detection_path = result_dir / "detection_results_GMTI02.csv"
        detections = [
            row for row in csv_rows(detection_path)
            if row.get("period_id") == str(PERIOD_ID) and row.get("beam_id") == str(BEAM_ID)
        ]
        for detection in detections:
            candidate_rows.append({
                "correction_ns": delay_ns,
                "period_id": PERIOD_ID,
                "beam_id": BEAM_ID,
                "det_id": detection.get("det_id"),
                "row": detection.get("row"),
                "col": detection.get("col"),
                "range_bin": detection.get("range_bin"),
                "power": detection.get("power"),
                "cfar_margin_db": detection.get("cfar_margin_db"),
                "ctdr_raw_phase_rad": detection.get("ctdr_raw_phase_rad"),
                "new_e": detection.get("new_e"),
                "new_n": detection.get("new_n"),
            })
        exact = [
            row for row in detections
            if row.get("row") == str(TARGET_ROW) and row.get("col") == str(TARGET_COL)
        ]
        if len(exact) > 1:
            exact.sort(key=lambda row: float(row.get("power", "-inf") or "-inf"), reverse=True)
        detection = exact[0] if exact else None
        matched = run_match(detections, truth)
        matched_detection = matched["detection"] if matched else None
        fixed_cell_target_match = bool(
            detection is not None and matched_detection is not None
            and detection.get("det_id") == matched_detection.get("det_id")
        )
        loc = localization_metrics(matched_detection, truth_row) if matched_detection is not None else {}
        loc_fixed = localization_metrics(detection, truth_row) if detection is not None else {}
        phase = finite_float(detection.get("ctdr_raw_phase_rad")) if detection else None
        phase_delta = wrap_rad(phase - baseline_phase) if phase is not None else None
        csi_row = target_tap(delay_ns, "csi_after")
        csi_value = complex(float(csi_row["real"]), float(csi_row["imag"])) if csi_row else None
        csi_ratio = csi_value / baseline_csi if csi_value is not None and baseline_csi != 0 else None
        fixed_jacobian = finite_float(detection.get("loc_jacobian_crossrange_m_per_phase_rad")) if detection else None
        fixed_cross_delta = (
            loc_fixed["crossrange_error_m"] - baseline_loc["crossrange_error_m"]
            if detection and loc_fixed else None
        )
        predicted_cross = baseline_jacobian * phase_delta if phase_delta is not None and detection is not None else None
        closure_residual = (
            fixed_cross_delta - predicted_cross
            if fixed_cross_delta is not None and predicted_cross is not None else None
        )
        lineage_path = result_dir / "debug/cluster_detection_lineage.csv"
        lineage_rows = csv_rows(lineage_path)
        fixed_cluster = next((
            row for row in lineage_rows
            if row.get("record_type") == "cluster"
            and row.get("period_id") == str(PERIOD_ID)
            and row.get("beam_id") == str(BEAM_ID)
            and row.get("cluster_row") == str(TARGET_ROW)
            and row.get("cluster_col") == str(TARGET_COL)
        ), None)
        matched_cluster = next((
            row for row in lineage_rows
            if matched_detection is not None
            and row.get("record_type") == "cluster"
            and row.get("period_id") == str(PERIOD_ID)
            and row.get("beam_id") == str(BEAM_ID)
            and row.get("cluster_row") == matched_detection.get("row")
            and row.get("cluster_col") == matched_detection.get("col")
        ), None)
        metrics_rows.append({
            "correction_ns": delay_ns,
            "residual_delay_ns": TRUE_DELAY_NS - delay_ns,
            "branch_gate": branch["production_gate"]["status"],
            "input_f2_relative_rms_to_A0": next(row["channel_and_fusion_closure"]["F2"]["relative_rms"] for row in manifest["input_corrections"] if row["correction_ns"] == delay_ns),
            "input_ch2_relative_rms_to_A0": next(row["channel_and_fusion_closure"]["ch2"]["relative_rms"] for row in manifest["input_corrections"] if row["correction_ns"] == delay_ns),
            "raw_a1_f1_relative_rms_to_A0": raw_a1_closure["F1_relative_rms"],
            "raw_a1_f2_relative_rms_to_A0": raw_a1_closure["F2_relative_rms"],
            "csi_after_real": csi_value.real if csi_value is not None else "",
            "csi_after_imag": csi_value.imag if csi_value is not None else "",
            "csi_after_magnitude": abs(csi_value) if csi_value is not None else "",
            "csi_after_phase_rad": float(np.angle(csi_value)) if csi_value is not None else "",
            "csi_after_ratio_magnitude_to_A0": abs(csi_ratio) if csi_ratio is not None else "",
            "csi_after_ratio_phase_to_A0_rad": float(np.angle(csi_ratio)) if csi_ratio is not None else "",
            "fixed_cell_detection": bool(detection),
            "target_match": bool(matched),
            "fixed_cell_target_match": fixed_cell_target_match,
            "matched_candidate_class": "same_fixed_cell" if fixed_cell_target_match else "alternate_candidate" if matched_detection else "no_target_match",
            "target_match_cell": f"{matched_detection.get('row')},{matched_detection.get('col')}" if matched_detection else "",
            "target_match_det_id": matched_detection.get("det_id", "") if matched_detection else "",
            "fixed_cell_cluster_index": fixed_cluster.get("cluster_index", "") if fixed_cluster else "",
            "fixed_cell_cluster_selected": fixed_cluster.get("selected", "") if fixed_cluster else "",
            "matched_candidate_cluster_index": matched_cluster.get("cluster_index", "") if matched_cluster else "",
            "matched_candidate_cluster_selected": matched_cluster.get("selected", "") if matched_cluster else "",
            "fixed_cell_phase_rad": finite_float(detection.get("ctdr_raw_phase_rad")) if detection else "",
            "fixed_cell_phase_delta_to_A0_rad": phase_delta if detection and phase_delta is not None else "",
            "fixed_cell_corrected_phase_rad": finite_float(detection.get("phase_rad")) if detection else "",
            "fixed_cell_range_phase_correction_rad": finite_float(detection.get("range_phase_correction_rad")) if detection else "",
            "fixed_cell_af_used_hz": finite_float(detection.get("af_used_hz")) if detection else "",
            "fixed_cell_jacobian_crossrange_m_per_phase_rad": fixed_jacobian if fixed_jacobian is not None else "",
            "fixed_cell_theta_used_deg": finite_float(detection.get("theta_used_deg")) if detection else "",
            "fixed_cell_new_e": finite_float(detection.get("new_e")) if detection else "",
            "fixed_cell_new_n": finite_float(detection.get("new_n")) if detection else "",
            "matched_target_production_raw_phase_rad": finite_float(matched_detection.get("ctdr_raw_phase_rad")) if matched_detection else "",
            "matched_target_production_phase_rad": finite_float(matched_detection.get("phase_rad")) if matched_detection else "",
            "matched_target_range_phase_correction_rad": finite_float(matched_detection.get("range_phase_correction_rad")) if matched_detection else "",
            "matched_target_af_used_hz": finite_float(matched_detection.get("af_used_hz")) if matched_detection else "",
            "matched_target_angle_error_deg": loc.get("angle_error_deg", ""),
            "matched_target_crossrange_error_m": loc.get("crossrange_error_m", ""),
            "matched_target_range_error_m": loc.get("range_error_m", ""),
            "matched_target_position_error_2d_m": loc.get("position_error_2d_m", ""),
            "fixed_cell_diagnostic_target_angle_error_deg": loc_fixed.get("angle_error_deg", ""),
            "fixed_cell_diagnostic_crossrange_error_m": loc_fixed.get("crossrange_error_m", ""),
            "fixed_cell_diagnostic_range_error_m": loc_fixed.get("range_error_m", ""),
            "fixed_cell_diagnostic_position_error_2d_m": loc_fixed.get("position_error_2d_m", ""),
            "fixed_cell_diagnostic_delta_crossrange_from_A0_m": fixed_cross_delta if fixed_cross_delta is not None else "",
            "fixed_cell_diagnostic_predicted_delta_crossrange_from_A0_m": predicted_cross if predicted_cross is not None else "",
            "fixed_cell_diagnostic_jacobian_closure_residual_m": closure_residual if closure_residual is not None else "",
            "fixed_cell_diagnostic_jacobian_closure_gate": abs(closure_residual) <= JACOBIAN_CROSSRANGE_TOLERANCE_M if closure_residual is not None else "NOT_EVALUABLE",
            "fixed_cell_cfar_margin_db": finite_float(detection.get("cfar_margin_db")) if detection else "",
            "target_match_cfar_margin_db": finite_float(matched_detection.get("cfar_margin_db")) if matched_detection else "",
            "detection_csv": str(detection_path),
            "cluster_lineage_csv": str(lineage_path),
            "track_debug_dir": str(Path(branch["production_gate"]["track_debug_files"][0]).parent) if branch["production_gate"]["track_debug_files"] else "",
        })
    csv_write(OUT / "scan_metrics.csv", metrics_rows)
    csv_write(OUT / "production_candidate_detections.csv", candidate_rows)
    known_row = next(row for row in metrics_rows if row["correction_ns"] == TRUE_DELAY_NS)
    known_gate: dict[str, Any]
    if not known_row["fixed_cell_target_match"]:
        known_gate = {"status": "NOT_EVALUABLE", "reason": "4ns_fixed_cell_was_not_matched_as_the_target_by_the_frozen_evaluator"}
    else:
        a0_loc = baseline_loc
        known_phase_delta = abs(float(known_row["fixed_cell_phase_delta_to_A0_rad"]))
        known_position_delta = abs(float(known_row["fixed_cell_diagnostic_position_error_2d_m"]) - a0_loc["position_error_2d_m"])
        known_angle_delta = abs(float(known_row["fixed_cell_diagnostic_target_angle_error_deg"]) - a0_loc["angle_error_deg"])
        failures = []
        if known_phase_delta > KNOWN_PHASE_TOLERANCE_RAD:
            failures.append("raw_phase_delta_exceeds_0.001_rad")
        if known_position_delta > KNOWN_POSITION_TOLERANCE_M:
            failures.append("position_delta_exceeds_1m")
        if known_angle_delta > KNOWN_ANGLE_TOLERANCE_DEG:
            failures.append("angle_error_delta_exceeds_0.001deg")
        known_gate = {
            "status": "passed" if not failures else "failed",
            "failures": failures,
            "raw_phase_abs_delta_rad": known_phase_delta,
            "position_error_abs_delta_m": known_position_delta,
            "angle_error_abs_delta_deg": known_angle_delta,
        }
    manifest["raw_A1_input_closure_to_A0"] = raw_a1_closure
    manifest["analysis_runner_sha256"] = sha256(Path(__file__).resolve())
    manifest["target_match_rule"] = {
        "config_path": str(ROOT / "configs/eval/localization_match_config.json"),
        "config_sha256": sha256(ROOT / "configs/eval/localization_match_config.json"),
        "matcher_source_path": str(ROOT / "scripts/gmti_eval_match.py"),
        "matcher_source_sha256": sha256(ROOT / "scripts/gmti_eval_match.py"),
    }
    manifest["analysis_note"] = "matched-target errors are from the frozen one-to-one evaluator; fixed-cell geometry is reported separately as a production detection diagnostic when the evaluator assigns another candidate"
    manifest["known_production_reference_gate"] = known_gate
    manifest["analysis_state"] = "EVALUATED" if known_gate["status"] == "passed" else known_gate["status"]
    manifest["state"] = "EVALUATED" if known_gate["status"] == "passed" else "EVALUATION_STOPPED_KNOWN_GATE"
    json_write(manifest_path(), manifest)
    make_charts(metrics_rows)
    print(f"analysis state={manifest['state']} rows={len(metrics_rows)}")


def make_charts(rows: list[dict[str, Any]]) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        (OUT / "charts/NOT_AVAILABLE.txt").parent.mkdir(parents=True, exist_ok=True)
        (OUT / "charts/NOT_AVAILABLE.txt").write_text(f"matplotlib import failed: {exc}\n", encoding="utf-8")
        return
    chart_root = OUT / "charts"
    chart_root.mkdir(parents=True, exist_ok=True)
    x = np.array([float(row["correction_ns"]) for row in rows])
    def series(key: str) -> np.ndarray:
        return np.array([float(row[key]) if row[key] not in ("", None, "NOT_EVALUABLE") else np.nan for row in rows])
    labels = ["solid line: fixed cell (48,2154); hollow markers: evaluator-matched candidate (cell may differ); one seed/period"]
    fig, ax = plt.subplots(figsize=(8.5, 5.0))
    ax.plot(x, series("input_f2_relative_rms_to_A0"), "o-", label="F2 relative RMS to A0")
    ax.plot(x, series("input_ch2_relative_rms_to_A0"), "s--", label="physical ch2 relative RMS to A0")
    ax.set_yscale("log")
    ax.set(xlabel="physical ch2 correction (ns)", ylabel="relative RMS to A0 (log scale)", title="Input closure vs correction")
    ax.grid(True, alpha=0.25); ax.legend()
    fig.text(0.5, 0.01, labels[0], ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.04, 1, 1)); fig.savefig(chart_root / "R11_input_closure.png", dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.5, 5.0))
    ax.plot(x, series("csi_after_ratio_phase_to_A0_rad"), "o-", label="CSI target-cell phase vs A0")
    ax.plot(x, series("fixed_cell_phase_delta_to_A0_rad"), "s--", label="Production CTDR raw phase vs A0")
    ax.set(xlabel="physical ch2 correction (ns)", ylabel="wrapped phase difference (rad)", title="Fixed-cell phase response (unmatched points are diagnostic)")
    ax.grid(True, alpha=0.25); ax.legend()
    fig.text(0.5, 0.01, labels[0], ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.04, 1, 1)); fig.savefig(chart_root / "R11_phase_response.png", dpi=160); plt.close(fig)

    fig, left = plt.subplots(figsize=(8.5, 5.0))
    left.plot(x, series("fixed_cell_diagnostic_target_angle_error_deg"), "o-", color="tab:blue", label="fixed-cell diagnostic angle error")
    left.scatter(x, series("matched_target_angle_error_deg"), marker="o", facecolors="none", edgecolors="tab:blue", label="evaluator-matched candidate angle error")
    left.set(xlabel="physical ch2 correction (ns)", ylabel="signed angle error (deg)", title="Fixed-cell geometry and evaluator-matched candidate")
    left.grid(True, alpha=0.25)
    right = left.twinx()
    right.plot(x, series("fixed_cell_diagnostic_position_error_2d_m"), "s--", color="tab:red", label="fixed-cell diagnostic 2D position error")
    right.scatter(x, series("matched_target_position_error_2d_m"), marker="s", facecolors="none", edgecolors="tab:red", label="evaluator-matched candidate 2D error")
    right.set_ylabel("2D EN position error (m)")
    handles1, labels1 = left.get_legend_handles_labels(); handles2, labels2 = right.get_legend_handles_labels()
    left.legend(handles1 + handles2, labels1 + labels2, loc="best")
    fig.text(0.5, 0.01, labels[0], ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.04, 1, 1)); fig.savefig(chart_root / "R11_angle_position.png", dpi=160); plt.close(fig)

    actual = series("fixed_cell_diagnostic_delta_crossrange_from_A0_m")
    predicted = series("fixed_cell_diagnostic_predicted_delta_crossrange_from_A0_m")
    finite = np.isfinite(actual) & np.isfinite(predicted)
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    if finite.any():
        ax.scatter(predicted[finite], actual[finite], marker="o")
        low = float(min(np.min(predicted[finite]), np.min(actual[finite])))
        high = float(max(np.max(predicted[finite]), np.max(actual[finite])))
        ax.plot([low, high], [low, high], "k--", linewidth=1, label="y=x")
        ax.legend()
    ax.set(xlabel="A0 Jacobian predicted fixed-cell Δcross-range (m)", ylabel="production EN fixed-cell Δcross-range (m)", title="Fixed-cell phase-to-position closure (diagnostic)")
    ax.grid(True, alpha=0.25)
    fig.text(0.5, 0.01, labels[0], ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.04, 1, 1)); fig.savefig(chart_root / "R11_jacobian_closure.png", dpi=160); plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("prepare", "run", "analyze"), required=True)
    parser.add_argument("--attempt", type=int, default=1, help="production attempt number; retries preserve earlier evidence")
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare()
    elif args.mode == "run":
        run_production(args.attempt)
    else:
        analyze()


if __name__ == "__main__":
    main()
