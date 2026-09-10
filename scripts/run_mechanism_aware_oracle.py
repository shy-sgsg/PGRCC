#!/usr/bin/env python3
"""对已确认退化的 M1/M2/M3 强档运行机制对应的 deterministic Oracle。

 M1/M2 在 raw LFM 上用已知注入参数做逆变换，再重新运行同一个
 ``GMTI_core``；M3 在当前 CSI tap 的 F1/F2 上依次比较 Current、逐行 phase
 oracle、逐行 complex-LS、range-block complex-LS 和短窗 Wiener/LMMSE。
脚本不训练 AI，也不把 truth 送入生产估计器。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = ROOT / "scripts/audit_csi_residual_texture.py"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from experiment_provenance import git_provenance  # noqa: E402

CASES = {
    "M1_fractional_channel_delay": {
        "variant": ROOT / "outputs/ai_csi_model_mismatch_formal_m1_seed2026091011/M1_fractional_channel_delay/strong",
        "mechanism": "fractional_channel_delay",
        "oracle": "inverse_known_fractional_delay_on_raw_channel_2",
    },
    "M2_pulse_varying_differential_phase": {
        "variant": ROOT / "outputs/ai_csi_model_mismatch_formal_m2_seed2026091011/M2_pulse_varying_differential_phase/strong",
        "mechanism": "pulse_varying_differential_phase",
        "oracle": "inverse_known_phase_trajectory_on_raw_channel_2",
    },
    "M3_internal_clutter_motion": {
        "variant": ROOT / "outputs/ai_csi_model_mismatch_formal_m3_seed2026091011/M3_internal_clutter_motion/strong_motion",
        "mechanism": "temporal_decorrelation",
        "oracle": "row_phase_current_amplitude_oracle",
    },
}


def cases_for_root(run_root: Path | None) -> dict[str, dict[str, Any]]:
    if run_root is None:
        return CASES
    return {
        "M1_fractional_channel_delay": {
            **CASES["M1_fractional_channel_delay"],
            "variant": run_root / "M1_fractional_channel_delay/strong",
        },
        "M2_pulse_varying_differential_phase": {
            **CASES["M2_pulse_varying_differential_phase"],
            "variant": run_root / "M2_pulse_varying_differential_phase/strong",
        },
        "M3_internal_clutter_motion": {
            **CASES["M3_internal_clutter_motion"],
            "variant": run_root / "M3_internal_clutter_motion/strong_motion",
        },
    }


def finite(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run_logged(command: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write("$ " + " ".join(command) + "\n")
        handle.flush()
        completed = subprocess.run(command, cwd=ROOT, stdout=handle,
                                   stderr=subprocess.STDOUT, check=False)
    return int(completed.returncode)


def single_manifest(variant: Path) -> Path:
    manifests = sorted((variant / "stage2/algorithm_result/period_0000/csi_metrics").glob(
        "*/csi_roi_manifest.csv"))
    if len(manifests) != 1:
        raise RuntimeError(f"CSI manifest 数量不是 1：{variant} -> {manifests}")
    return manifests[0]


def csi_row(manifest: Path, roi: str = "active_support_unmasked") -> dict[str, str]:
    summary = manifest.with_name("csi_metric_tap_summary.csv")
    rows = read_rows(summary)
    for row in rows:
        if row.get("roi_name") == roi:
            return row
    raise RuntimeError(f"CSI summary 缺少 ROI={roi}：{summary}")


def summary_metrics(manifest: Path) -> dict[str, float]:
    row = csi_row(manifest)
    return {
        "cancellation_db": finite(row.get("CA_ROI_dB")),
        "coherence": finite(row.get("phase_model_weighted_coherence")),
        "phase_rmse_rad": finite(row.get("phase_model_weighted_circular_rmse_rad")),
        "phase_p95_rad": finite(row.get("phase_model_p95_abs_residual_rad")),
    }


def patch_xml(xml_path: Path, input_path: Path, result_dir: Path, output_xml: Path) -> None:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    data_node = root.find(".//GMTI_data_new")
    result_node = root.find(".//result_add")
    if data_node is None or result_node is None:
        raise RuntimeError(f"XML 缺少 GMTI_data_new/result_add：{xml_path}")
    data_node.text = str(input_path.resolve())
    result_node.text = str(result_dir.resolve())
    output_xml.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_xml, encoding="utf-8", xml_declaration=True)


def xml_float(xml_path: Path, tag: str, default: float) -> float:
    root = ET.parse(xml_path).getroot()
    node = root.find(f".//{tag}")
    return finite(node.text) if node is not None and node.text else default


def xml_int(xml_path: Path, tag: str, default: int) -> int:
    value = xml_float(xml_path, tag, float(default))
    return int(value) if math.isfinite(value) else default


def xml_frequency_hz(xml_path: Path, tag: str, default_hz: float) -> float:
    value = xml_float(xml_path, tag, default_hz)
    return value if value > 1.0e6 else value * 1.0e6


def inverse_channel_impairment(
    source: Path,
    target: Path,
    xml_path: Path,
    delay_ns: float,
    phase_drift_deg: float,
) -> dict[str, Any]:
    pulse_len = xml_int(xml_path, "pulse_len", 11840)
    channel_count = xml_int(xml_path, "new_protocol_channel_count", 4)
    pulse_num = xml_int(xml_path, "pulse_num", 128)
    iq_type = "".join((ET.parse(xml_path).getroot().findtext(".//iq_data_type") or "float32").lower().split())
    if iq_type not in {"float32", "float"}:
        raise RuntimeError(f"Oracle 当前只支持 float32 raw LFM，实际为 {iq_type}")
    bytes_per_packet = 256 + pulse_len * channel_count * 2 * 4
    raw = np.fromfile(source, dtype=np.uint8)
    if raw.size % bytes_per_packet != 0:
        raise RuntimeError(f"raw 文件不是完整 packet：{source} bytes={raw.size}")
    packets = raw.reshape((-1, bytes_per_packet))
    if packets.shape[0] < pulse_num:
        raise RuntimeError(f"raw packet 数不足：{source} packets={packets.shape[0]} pulse_num={pulse_num}")
    payload = packets[:, 256:]
    values = payload.view("<f4").reshape(packets.shape[0], pulse_len, channel_count, 2)
    channel2 = values[:, :, 1, 0].astype(np.float64) + 1j * values[:, :, 1, 1].astype(np.float64)
    sample_shift = delay_ns * 1.0e-9 * xml_frequency_hz(xml_path, "fs", 60.0e6)
    indices = np.arange(pulse_len, dtype=np.float64)
    corrected = np.empty_like(channel2)
    for pulse_id in range(packets.shape[0]):
        phase = np.exp(-1j * math.radians(phase_drift_deg) * float(pulse_id))
        dephased = channel2[pulse_id] * phase
        source_indices = indices + sample_shift
        corrected[pulse_id] = (
            np.interp(source_indices, indices, dephased.real, left=0.0, right=0.0)
            + 1j * np.interp(source_indices, indices, dephased.imag, left=0.0, right=0.0)
        )
    values[:, :, 1, 0] = corrected.real.astype(np.float32)
    values[:, :, 1, 1] = corrected.imag.astype(np.float32)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw.tofile(target)
    return {
        "source": str(source.resolve()),
        "target": str(target.resolve()),
        "packet_count": int(packets.shape[0]),
        "pulse_len": pulse_len,
        "sample_shift": sample_shift,
        "delay_ns": delay_ns,
        "phase_drift_deg_per_pulse": phase_drift_deg,
        "operation": "dephase_then_inverse_fractional_shift_channel_2",
    }


def inverse_channel_impairment_frequency_domain(
    source: Path,
    target: Path,
    xml_path: Path,
    delay_ns: float,
    phase_drift_deg: float,
) -> dict[str, Any]:
    """Undo channel-2 phase drift and delay with an independent FFT phase ramp.

    The production impairment uses finite-support linear interpolation.  This
    correction deliberately uses a zero-padded FFT and a frequency-domain
    phase ramp, so it is an independent cross-check rather than a reuse of
    the simulator's interpolation implementation.
    """
    pulse_len = xml_int(xml_path, "pulse_len", 11840)
    channel_count = xml_int(xml_path, "new_protocol_channel_count", 4)
    iq_type = "".join((ET.parse(xml_path).getroot().findtext(".//iq_data_type") or "float32").lower().split())
    if iq_type not in {"float32", "float"}:
        raise RuntimeError(f"frequency-domain correction only supports float32 raw LFM, got {iq_type}")
    bytes_per_packet = 256 + pulse_len * channel_count * 2 * 4
    raw = np.fromfile(source, dtype=np.uint8)
    if raw.size % bytes_per_packet != 0:
        raise RuntimeError(f"raw file is not complete packets: {source} bytes={raw.size}")
    packets = raw.reshape((-1, bytes_per_packet))
    payload = packets[:, 256:]
    values = payload.view("<f4").reshape(packets.shape[0], pulse_len, channel_count, 2)
    channel2 = values[:, :, 1, 0].astype(np.float64) + 1j * values[:, :, 1, 1].astype(np.float64)
    fft_len = 1 << int(math.ceil(math.log2(max(2, 2 * pulse_len))))
    frequency_hz = np.fft.fftfreq(fft_len, d=1.0 / xml_frequency_hz(xml_path, "fs", 60.0e6))
    delay_sec = delay_ns * 1.0e-9
    ramp = np.exp(1j * 2.0 * math.pi * frequency_hz * delay_sec)
    corrected = np.empty_like(channel2)
    for pulse_id in range(packets.shape[0]):
        dephased = channel2[pulse_id] * np.exp(-1j * math.radians(phase_drift_deg) * float(pulse_id))
        padded = np.zeros(fft_len, dtype=np.complex128)
        padded[:pulse_len] = dephased
        corrected[pulse_id] = np.fft.ifft(np.fft.fft(padded) * ramp)[:pulse_len]
    values[:, :, 1, 0] = corrected.real.astype(np.float32)
    values[:, :, 1, 1] = corrected.imag.astype(np.float32)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw.tofile(target)
    return {
        "source": str(source.resolve()),
        "target": str(target.resolve()),
        "packet_count": int(packets.shape[0]),
        "pulse_len": pulse_len,
        "sample_shift": delay_ns * 1.0e-9 * xml_frequency_hz(xml_path, "fs", 60.0e6),
        "delay_ns": delay_ns,
        "phase_drift_deg_per_pulse": phase_drift_deg,
        "operation": "dephase_then_zero_padded_fft_fractional_shift_channel_2",
        "fft_length": fft_len,
    }


def inverse_channel_phase_trajectory_frequency_domain(
    source: Path,
    target: Path,
    xml_path: Path,
    phase_deg_by_pulse: np.ndarray,
) -> dict[str, Any]:
    """Undo a measured per-pulse channel-2 phase trajectory in raw samples."""
    pulse_len = xml_int(xml_path, "pulse_len", 11840)
    channel_count = xml_int(xml_path, "new_protocol_channel_count", 4)
    iq_type = "".join((ET.parse(xml_path).getroot().findtext(".//iq_data_type") or "float32").lower().split())
    if iq_type not in {"float32", "float"}:
        raise RuntimeError(f"phase trajectory correction only supports float32 raw LFM, got {iq_type}")
    bytes_per_packet = 256 + pulse_len * channel_count * 2 * 4
    raw = np.fromfile(source, dtype=np.uint8)
    if raw.size % bytes_per_packet != 0:
        raise RuntimeError(f"raw file is not complete packets: {source} bytes={raw.size}")
    packets = raw.reshape((-1, bytes_per_packet))
    if phase_deg_by_pulse.size < packets.shape[0]:
        raise RuntimeError("phase trajectory is shorter than raw pulse count")
    payload = packets[:, 256:]
    values = payload.view("<f4").reshape(packets.shape[0], pulse_len, channel_count, 2)
    channel2 = values[:, :, 1, 0].astype(np.float64) + 1j * values[:, :, 1, 1].astype(np.float64)
    corrected = channel2 * np.exp(-1j * np.deg2rad(phase_deg_by_pulse[:packets.shape[0]])[:, None])
    values[:, :, 1, 0] = corrected.real.astype(np.float32)
    values[:, :, 1, 1] = corrected.imag.astype(np.float32)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw.tofile(target)
    return {
        "source": str(source.resolve()),
        "target": str(target.resolve()),
        "packet_count": int(packets.shape[0]),
        "pulse_len": pulse_len,
        "phase_source": "estimated raw F1/F2 trajectory",
        "operation": "dephase_channel_2_by_measured_pulse_trajectory",
    }


def current_variant_metrics(variant: Path) -> dict[str, float]:
    manifest = single_manifest(variant)
    return summary_metrics(manifest)


def _db(value: float) -> float:
    return 10.0 * math.log10(max(float(value), np.finfo(float).tiny))


def _row_or_block_complex_ls(
    f1: np.ndarray,
    f2: np.ndarray,
    valid: np.ndarray,
    az_st: int,
    az_ed: int,
    rg_st: int,
    rg_ed: int,
    block_bins: int,
) -> np.ndarray:
    alpha = np.ones(f1.shape, dtype=np.complex128)
    for row in range(az_st, az_ed + 1):
        for start in range(rg_st, rg_ed + 1, block_bins):
            end = min(rg_ed + 1, start + block_bins)
            mask = valid[row, start:end]
            if not np.any(mask):
                continue
            a = f1[row, start:end][mask]
            b = f2[row, start:end][mask]
            denominator = float(np.sum(np.abs(b) ** 2))
            if denominator > 1.0e-12:
                alpha[row, start:end] = np.sum(a * np.conj(b)) / denominator
    return alpha


def _short_wiener_alpha(
    f1: np.ndarray,
    f2: np.ndarray,
    valid: np.ndarray,
    az_st: int,
    az_ed: int,
    rg_st: int,
    rg_ed: int,
    row_radius: int = 2,
    range_radius: int = 8,
) -> np.ndarray:
    """Estimate a local complex scalar from measured F1/F2 only."""
    alpha = np.ones(f1.shape, dtype=np.complex128)
    for row in range(az_st, az_ed + 1):
        row_start = max(az_st, row - row_radius)
        row_end = min(az_ed + 1, row + row_radius + 1)
        for col in range(rg_st, rg_ed + 1):
            col_start = max(rg_st, col - range_radius)
            col_end = min(rg_ed + 1, col + range_radius + 1)
            mask = valid[row_start:row_end, col_start:col_end]
            if not np.any(mask):
                continue
            a = f1[row_start:row_end, col_start:col_end][mask]
            b = f2[row_start:row_end, col_start:col_end][mask]
            denominator = float(np.sum(np.abs(b) ** 2))
            if denominator > 1.0e-12:
                # A small diagonal term keeps weak-support cells finite while
                # remaining entirely data-driven.
                alpha[row, col] = np.sum(a * np.conj(b)) / (denominator + 1.0e-3 * denominator)
    return alpha


def _m3_method_metrics(
    name: str,
    f1: np.ndarray,
    f2: np.ndarray,
    alpha: np.ndarray,
    valid: np.ndarray,
    before: np.ndarray,
    current_after: np.ndarray,
    balanced_amplitude: bool,
) -> dict[str, Any]:
    target = alpha * f2
    if balanced_amplitude:
        a_abs = np.abs(f1)
        b_abs = np.abs(target)
        residual = np.where(a_abs > 0.0, np.minimum(a_abs, b_abs) / a_abs * f1, 0.0) - np.where(
            b_abs > 0.0, np.minimum(a_abs, b_abs) / b_abs * target, 0.0)
    else:
        residual = f1 - target
    residual_power = np.abs(residual) ** 2
    weights = np.minimum(np.abs(f1) ** 2, np.abs(f2) ** 2)
    selected = valid & np.isfinite(residual_power) & np.isfinite(weights)
    before_mean = float(np.mean(before[selected])) if np.any(selected) else math.nan
    after_mean = float(np.mean(residual_power[selected])) if np.any(selected) else math.nan
    current_mean = float(np.mean(current_after[selected])) if np.any(selected) else math.nan
    phase = np.angle(f1 * np.conj(target))
    phase_selected = selected & np.isfinite(phase) & (weights > 0.0)
    if np.any(phase_selected):
        phase_rmse = float(np.sqrt(np.average(
            phase[phase_selected] ** 2, weights=weights[phase_selected])))
    else:
        phase_rmse = math.nan
    a = f1[phase_selected]
    b = target[phase_selected]
    denominator = math.sqrt(float(np.sum(np.abs(a) ** 2) * np.sum(np.abs(b) ** 2)))
    coherence = float(abs(np.sum(a * np.conj(b))) / denominator) if denominator > 0.0 else math.nan
    cancellation = _db(before_mean / max(after_mean, np.finfo(float).tiny)) if after_mean >= 0.0 else math.nan
    current_cancellation = _db(before_mean / current_mean) if current_mean > 0.0 else math.nan
    return {
        "method": name,
        "cancellation_db": cancellation,
        "phase_rmse_rad": phase_rmse,
        "coherence": coherence,
        "headroom_gain_db_vs_current": cancellation - current_cancellation,
        "target_preservation_status": "not_evaluable_from_F1_F2_clutter_tap",
        "pfa_status": "not_evaluable_from_F1_F2_clutter_tap",
        "valid_cell_count": int(np.sum(selected)),
    }


def m3_oracle_methods(
    f1: np.ndarray,
    f2: np.ndarray,
    before: np.ndarray,
    current_after: np.ndarray,
    valid: np.ndarray,
    az_st: int,
    az_ed: int,
    rg_st: int,
    rg_ed: int,
    range_block_bins: int = 64,
) -> list[dict[str, Any]]:
    """Return O1..O5 diagnostics from measured F1/F2 without truth."""
    rows, cols = f1.shape
    valid = np.asarray(valid, dtype=bool).copy()
    active = np.zeros((rows, cols), dtype=bool)
    active[az_st:az_ed + 1, rg_st:rg_ed + 1] = True
    valid &= active
    row_alpha = np.ones(rows, dtype=np.complex128)
    for row in range(az_st, az_ed + 1):
        mask = valid[row]
        if not np.any(mask):
            continue
        b = f2[row, mask]
        denominator = float(np.sum(np.abs(b) ** 2))
        if denominator > 1.0e-12:
            row_alpha[row] = np.sum(f1[row, mask] * np.conj(b)) / denominator
    row_phase = np.ones(rows, dtype=np.complex128)
    nonzero = np.abs(row_alpha) > 1.0e-12
    row_phase[nonzero] = row_alpha[nonzero] / np.abs(row_alpha[nonzero])
    alpha_o2 = np.repeat(row_phase[:, None], cols, axis=1)
    alpha_o3 = np.repeat(row_alpha[:, None], cols, axis=1)
    alpha_o4 = _row_or_block_complex_ls(
        f1, f2, valid, az_st, az_ed, rg_st, rg_ed, range_block_bins)
    alpha_o5 = _short_wiener_alpha(
        f1, f2, valid, az_st, az_ed, rg_st, rg_ed)
    current_cancel = _db(float(np.mean(before[valid])) /
                         float(np.mean(current_after[valid]))) if np.any(valid) else math.nan
    rows_out = [{
        "method": "O1_Current",
        "cancellation_db": current_cancel,
        "phase_rmse_rad": math.nan,
        "coherence": math.nan,
        "headroom_gain_db_vs_current": 0.0,
        "target_preservation_status": "not_evaluable_from_F1_F2_clutter_tap",
        "pfa_status": "not_evaluable_from_F1_F2_clutter_tap",
        "valid_cell_count": int(np.sum(valid)),
    }]
    rows_out.append(_m3_method_metrics(
        "O2_row_phase_current_amplitude_oracle", f1, f2, alpha_o2,
        valid, before, current_after, balanced_amplitude=True))
    rows_out.append(_m3_method_metrics(
        "O3_row_complex_scalar_LS", f1, f2, alpha_o3,
        valid, before, current_after, balanced_amplitude=False))
    rows_out.append(_m3_method_metrics(
        "O4_range_block_complex_LS", f1, f2, alpha_o4,
        valid, before, current_after, balanced_amplitude=False))
    rows_out.append(_m3_method_metrics(
        "O5_short_Wiener_LMMSE", f1, f2, alpha_o5,
        valid, before, current_after, balanced_amplitude=False))
    return rows_out


def raw_oracle_case(name: str, spec: dict[str, Any], output_dir: Path,
                    build_dir: Path, correction_method: str) -> dict[str, Any]:
    variant = Path(spec["variant"])
    scenario = json.loads((variant / "scenario.json").read_text(encoding="utf-8"))
    xml_path = variant / "stage2/config/temp_config_stage2_period_0000.xml"
    input_path = Path(ET.parse(xml_path).getroot().findtext(".//GMTI_data_new") or "")
    impairments = scenario.get("channel_impairments", {})
    case_dir = output_dir / name
    result_dir = case_dir / "stage2/algorithm_result/period_0000"
    corrected_path = case_dir / "stage2/data/oracle_corrected_period_0000.bin"
    delay_ns = finite(impairments.get("channel_time_delay_ns"))
    phase_drift_deg = finite(impairments.get("per_pulse_phase_drift_deg"))
    if correction_method == "frequency":
        transform = inverse_channel_impairment_frequency_domain(
            input_path, corrected_path, xml_path, delay_ns, phase_drift_deg)
    else:
        transform = inverse_channel_impairment(
            input_path, corrected_path, xml_path, delay_ns, phase_drift_deg)
    oracle_xml = case_dir / "stage2/config/oracle_config.xml"
    patch_xml(xml_path, corrected_path, result_dir, oracle_xml)
    gmti_log = case_dir / "gmticore.log"
    gmti_rc = run_logged(
        [str(build_dir / "GMTI_core"), str(oracle_xml), "--runtime-mode=debug",
         "--runtime-diagnostics=on"], gmti_log)
    row: dict[str, Any] = {
        "family": name,
        "mechanism": spec["mechanism"],
        "oracle": spec["oracle"],
        "correction_method": correction_method,
        "variant": str(variant.resolve()),
        "status": "gmticore_failed" if gmti_rc else "pending",
        "gmticore_exit_code": gmti_rc,
        "input_truth_not_used_by_estimator": True,
        "transform": transform,
    }
    if gmti_rc != 0:
        return row
    manifest = single_manifest(case_dir)
    audit_dir = case_dir / "residual_texture"
    audit_rc = run_logged(
        [sys.executable, str(AUDIT_SCRIPT), "--manifest", str(manifest),
         "--output-dir", str(audit_dir), "--period-id", "0", "--beam-id", "1"],
        case_dir / "residual_texture.log",
    )
    row["audit_exit_code"] = audit_rc
    if audit_rc != 0:
        row["status"] = "residual_audit_failed"
        return row
    current = current_variant_metrics(variant)
    oracle = summary_metrics(manifest)
    row.update({
        "status": "pass",
        "current": current,
        "oracle_metrics": oracle,
        "delta_cancellation_db": oracle["cancellation_db"] - current["cancellation_db"],
        "delta_phase_rmse_rad": oracle["phase_rmse_rad"] - current["phase_rmse_rad"],
        "delta_coherence": oracle["coherence"] - current["coherence"],
        "manifest": str(manifest.resolve()),
        "residual_summary": str((audit_dir / "residual_texture_summary.json").resolve()),
        "headroom_interpretation": "positive cancellation delta and lower phase RMSE support a recoverable calibration-model component",
    })
    return row


def row_oracle_case(name: str, spec: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    variant = Path(spec["variant"])
    manifest = single_manifest(variant)
    row = next(iter(read_rows(manifest)))
    rows = int(row["rows"])
    cols = int(row["cols"])
    az_st, az_ed = int(row["az_st"]), int(row["az_ed"])
    rg_st, rg_ed = int(row["rg_st"]), int(row["rg_ed"])
    f1 = np.load(row["channel1_real_path"]) + 1j * np.load(row["channel1_imag_path"])
    f2 = np.load(row["channel2_real_path"]) + 1j * np.load(row["channel2_imag_path"])
    before = np.load(row["before_power_path"])
    current_after = np.load(row["after_power_path"])
    oracle_after = np.zeros((rows, cols), dtype=np.float32)
    oracle_phase = np.full((rows, cols), np.nan, dtype=np.float32)
    coherence = np.full(rows, np.nan, dtype=np.float64)
    bypass = np.zeros(rows, dtype=bool)
    for r in range(az_st, az_ed + 1):
        a = f1[r, rg_st:rg_ed + 1].astype(np.complex128)
        b = f2[r, rg_st:rg_ed + 1].astype(np.complex128)
        e1 = float(np.sum(np.abs(a) ** 2))
        e2 = float(np.sum(np.abs(b) ** 2))
        cross = np.sum(a * np.conj(b))
        rho = abs(cross) / math.sqrt(e1 * e2) if e1 > 0.0 and e2 > 0.0 else 0.0
        coherence[r] = min(1.0, max(0.0, rho))
        if rho < 0.5:
            bypass[r] = True
            residual = a
            phase = np.zeros_like(b)
        else:
            phase_factor = cross / abs(cross) if abs(cross) > 0.0 else 1.0 + 0.0j
            b2 = phase_factor * b
            aa, bb = np.abs(a), np.abs(b2)
            m = np.minimum(aa, bb)
            residual = np.where(aa > 0.0, m / aa * a, 0.0) - np.where(bb > 0.0, m / bb * b2, 0.0)
            phase = np.angle(a * np.conj(b2))
        oracle_after[r, rg_st:rg_ed + 1] = np.abs(residual).astype(np.float32) ** 2
        oracle_phase[r, rg_st:rg_ed + 1] = np.asarray(phase, dtype=np.float32)
    support = np.s_[az_st:az_ed + 1, rg_st:rg_ed + 1]
    p1 = np.abs(f1) ** 2
    p2 = np.abs(f2) ** 2
    pmin = np.minimum(p1, p2)
    active_finite = (
        np.isfinite(pmin[support]) & np.isfinite(f1[support].real) &
        np.isfinite(f2[support].real)
    )
    active_values = pmin[support][active_finite]
    energy_floor = float(np.percentile(active_values, 5.0))
    strong_ceiling = float(np.percentile(active_values, 99.0))
    valid_support = active_finite & (pmin[support] >= max(energy_floor, 1.0e-12)) & (
        pmin[support] <= strong_ceiling)
    before_mean = float(np.mean(before[support]))
    current_mean = float(np.mean(current_after[support]))
    oracle_mean = float(np.mean(oracle_after[support]))

    def db(value: float) -> float:
        return 10.0 * math.log10(max(value, np.finfo(float).tiny))

    def rmse(values: np.ndarray) -> float:
        finite_values = values[np.isfinite(values)]
        return float(np.sqrt(np.mean(finite_values ** 2))) if finite_values.size else math.nan

    oracle_phase_values = oracle_phase[support][valid_support].astype(np.float64)
    oracle_phase_weights = pmin[support][valid_support].astype(np.float64)
    oracle_phase_rmse = (
        float(np.sqrt(np.sum(oracle_phase_weights * oracle_phase_values ** 2) /
                      np.sum(oracle_phase_weights)))
        if oracle_phase_values.size and np.sum(oracle_phase_weights) > 0.0 else math.nan
    )

    valid_mask = np.zeros((rows, cols), dtype=bool)
    valid_mask[az_st:az_ed + 1, rg_st:rg_ed + 1] = valid_support
    methods = m3_oracle_methods(
        f1, f2, before, current_after, valid_mask,
        az_st, az_ed, rg_st, rg_ed)

    case_dir = output_dir / name
    case_dir.mkdir(parents=True, exist_ok=True)
    np.save(case_dir / "oracle_after_power.npy", oracle_after)
    np.save(case_dir / "oracle_phase_residual_rad.npy", oracle_phase)
    np.save(case_dir / "oracle_row_coherence.npy", coherence)
    with (case_dir / "m3_oracle_methods.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        fields = [
            "method", "cancellation_db", "phase_rmse_rad", "coherence",
            "headroom_gain_db_vs_current", "target_preservation_status",
            "pfa_status", "valid_cell_count",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(methods)
    current_metrics = current_variant_metrics(variant)
    oracle_cancellation = db(before_mean / oracle_mean)
    row = {
        "family": name,
        "mechanism": spec["mechanism"],
        "oracle": spec["oracle"],
        "variant": str(variant.resolve()),
        "status": "pass",
        "input_truth_not_used_by_estimator": True,
        "current": current_metrics,
        "oracle_metrics": {
            "cancellation_db": oracle_cancellation,
            # A phase oracle does not create coherence; it only uses the
            # measured F1/F2 relation more accurately. Keep this equal to the
            # Current tap coherence instead of comparing different statistics.
            "coherence": current_metrics["coherence"],
            "row_coherence_median": float(np.nanmedian(coherence[az_st:az_ed + 1])),
            "phase_rmse_rad": oracle_phase_rmse,
            "bypass_fraction": float(np.mean(bypass[az_st:az_ed + 1])),
        },
        "oracle_methods": methods,
        "delta_cancellation_db": oracle_cancellation - current_metrics["cancellation_db"],
        "delta_phase_rmse_rad": oracle_phase_rmse - current_metrics["phase_rmse_rad"],
        "delta_coherence": 0.0,
        "manifest": str(manifest.resolve()),
        "oracle_artifacts": str(case_dir.resolve()),
        "headroom_interpretation": "row-wise complex-LS phase improves cancellation only within this small headroom; the remaining residual is consistent with temporal decorrelation rather than a recoverable single phase model",
    }
    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "family", "mechanism", "oracle", "status", "gmticore_exit_code",
        "delta_cancellation_db", "delta_phase_rmse_rad", "delta_coherence",
        "current_cancellation_db", "oracle_cancellation_db", "current_phase_rmse_rad",
        "oracle_phase_rmse_rad", "current_coherence", "oracle_coherence",
        "oracle_bypass_fraction", "input_truth_not_used_by_estimator", "manifest",
        "residual_summary", "oracle_artifacts", "headroom_interpretation",
    ]
    flat: list[dict[str, Any]] = []
    for row in rows:
        current = row.get("current", {})
        oracle = row.get("oracle_metrics", {})
        flat.append({
            **row,
            "current_cancellation_db": current.get("cancellation_db"),
            "oracle_cancellation_db": oracle.get("cancellation_db"),
            "current_phase_rmse_rad": current.get("phase_rmse_rad"),
            "oracle_phase_rmse_rad": oracle.get("phase_rmse_rad"),
            "current_coherence": current.get("coherence"),
            "oracle_coherence": oracle.get("coherence"),
            "oracle_bypass_fraction": oracle.get("bypass_fraction"),
        })
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flat)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/m3_corrected_audit/oracle_methods")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--run-root", type=Path, default=None,
                        help="使用一个 model-mismatch seed 的独立输出根目录")
    parser.add_argument("--family", choices=("all", *CASES.keys()), default="all")
    parser.add_argument("--correction-method", choices=("time", "frequency"), default="time",
                        help="M1/M2 raw correction implementation; M3 row oracle ignores this")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    case_map = cases_for_root(args.run_root.resolve() if args.run_root else None)
    selected = list(case_map) if args.family == "all" else [args.family]
    rows: list[dict[str, Any]] = []
    for name in selected:
        spec = case_map[name]
        if name == "M3_internal_clutter_motion":
            row = row_oracle_case(name, spec, output_dir)
        else:
            row = raw_oracle_case(name, spec, output_dir, args.build_dir.resolve(),
                                  args.correction_method)
        rows.append(row)
        (output_dir / f"{name}.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    # A one-family rerun must not discard already completed Oracle evidence
    # from the other families when rebuilding the consolidated CSV.
    for name in case_map:
        if name in selected:
            continue
        existing = output_dir / f"{name}.json"
        if existing.is_file():
            rows.append(json.loads(existing.read_text(encoding="utf-8")))
    write_csv(output_dir / "mechanism_oracle_summary.csv", rows)
    manifest = {
        "schema_version": 1,
        "ai_training": False,
        **git_provenance(ROOT),
        "run_root": str(args.run_root.resolve()) if args.run_root else None,
        "correction_method": args.correction_method,
        "family_selection": selected,
        "cases": rows,
        "interpretation": {
            "M1": "raw inverse fractional-delay replay; production GMTI is rerun",
            "M2": "raw inverse phase trajectory replay; production GMTI is rerun",
            "M3": "row-wise phase upper bound only; temporal decorrelation itself is not inverted",
        },
    }
    (output_dir / "mechanism_oracle_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    failed = sum(row.get("status") != "pass" for row in rows)
    print(json.dumps({"output_dir": str(output_dir), "completed_this_invocation": len(selected),
                      "consolidated_cases": len(rows), "failed": failed,
                      "ai_training": False}, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
