#!/usr/bin/env python3
"""第一阶段 CSI Current/Oracle 离线实验。

该脚本只处理当前副本内由 Stage2 生成器产生的一波位、一个周期的配对
C+N/S+C+N 新协议 BIN。数据路径、脉压、四通道合成、CTDR 对齐、DBS、
距离向相位校正、P38 和默认 CSI 算子均按当前源码实现复现；没有调用
目标真值来学习对消权重。所有 Oracle 参数都由配对的背景 C+N 图估计，
再固定应用到 S+C+N 图。

GPU 驱动不可用时，本脚本提供可审计的 CPU 研究证据，不把它表述为生产
CUDA 运行结果。它刻意只保存小型 CSV/PNG/JSON，不保存整幅复数中间图。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import struct
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


C0 = 299_792_458.0
TWO_PI = 2.0 * math.pi
GO_ALPHA_CACHE: Dict[Tuple[float, int, int], float] = {}


def scalar(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    return float(value.strip())


def integer(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    return int(float(value.strip()))


def boolean(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Params:
    pulse_len: int
    pulse_num: int
    channel_count: int
    iq_type: str
    range_fft_len: int
    range_crop_start: int
    range_compress_len: int
    fs_hz: float
    fc_hz: float
    bandwidth_hz: float
    pulse_duration_s: float
    prf_hz: float
    d_channel_m: float
    sample_delay_s: float
    beam_width_deg: float
    squint_side: int
    carrier_phase_sign: int
    rg_st: int
    rg_ed: int
    calib_coef: float
    read_channel_1: int
    read_channel_2: int
    fusion_channel_3: int
    fusion_channel_4: int
    phase_comp_enable: bool
    four_channel_fusion: bool
    offsets_m: Tuple[Tuple[float, float, float], ...]
    p38_min_coherence: float = 0.05
    p38_min_peak_energy_fraction: float = 0.05
    p38_min_sample_count: int = 8
    p38_min_inlier_ratio: float = 0.60
    p38_max_rmse_rad: float = 0.60
    cfar_pfa: float = 1.0e-6
    cfar_guard: int = 4
    cfar_background: int = 16

    @classmethod
    def from_xml(cls, path: Path) -> "Params":
        root = ET.parse(path).getroot()
        values: Dict[str, str] = {}
        for node in root.iter():
            if node.text is not None and node.text.strip():
                values[node.tag] = node.text.strip()

        def freq_hz(name: str, scale: float) -> float:
            raw = scalar(values.get(name), 0.0)
            return raw if raw > 1.0e6 else raw * scale

        offsets = []
        for channel in range(1, 5):
            offsets.append(
                (
                    scalar(values.get(f"four_channel_ch{channel}_x_m"), 0.0),
                    scalar(values.get(f"four_channel_ch{channel}_y_m"), 0.0),
                    scalar(values.get(f"four_channel_ch{channel}_z_m"), 0.0),
                )
            )
        return cls(
            pulse_len=integer(values.get("pulse_len"), 11820),
            pulse_num=integer(values.get("pulse_num"), 130),
            channel_count=integer(values.get("new_protocol_channel_count"), 4),
            iq_type=values.get("iq_data_type", "float32"),
            range_fft_len=integer(values.get("range_fft_len"), 12288),
            range_crop_start=integer(values.get("range_crop_start"), 3864),
            range_compress_len=integer(values.get("range_compress_len"), 4096),
            fs_hz=freq_hz("fs", 1.0e6),
            fc_hz=freq_hz("fc", 1.0e9),
            bandwidth_hz=freq_hz("Br", 1.0e6),
            pulse_duration_s=scalar(values.get("Tr"), 130.0) * 1.0e-6,
            prf_hz=scalar(values.get("PRF"), 1300.0),
            d_channel_m=scalar(values.get("d_chan"), 0.17),
            sample_delay_s=scalar(values.get("sample_delay_us"), 488.0) * 1.0e-6,
            beam_width_deg=scalar(values.get("beam_width_deg"), 2.28),
            squint_side=integer(values.get("squint_side"), 1),
            carrier_phase_sign=integer(values.get("four_channel_carrier_phase_sign"), -1),
            rg_st=integer(values.get("rg_st"), 1),
            rg_ed=integer(values.get("rg_ed"), 4095),
            calib_coef=scalar(values.get("calib_coef"), 1.0),
            read_channel_1=integer(values.get("new_protocol_read_channel_1"), 1),
            read_channel_2=integer(values.get("new_protocol_read_channel_2"), 2),
            fusion_channel_3=integer(values.get("four_channel_fusion_channel_3"), 3),
            fusion_channel_4=integer(values.get("four_channel_fusion_channel_4"), 4),
            phase_comp_enable=boolean(
                values.get("four_channel_phase_compensation_enable"), True
            ),
            four_channel_fusion=boolean(
                values.get("enable_four_channel_fusion"), True
            ),
            offsets_m=tuple(offsets),
            p38_min_peak_energy_fraction=scalar(
                values.get("p38_min_peak_row_energy_fraction"), 0.05
            ),
            p38_min_sample_count=integer(
                values.get("p38_refit_min_sample_count"), 8
            ),
            p38_min_inlier_ratio=scalar(
                values.get("p38_refit_min_inlier_ratio"), 0.60
            ),
            p38_max_rmse_rad=scalar(
                values.get("p38_refit_max_rmse_rad"), 0.60
            ),
            cfar_pfa=scalar(values.get("pf"), 1.0e-6),
            cfar_guard=integer(values.get("cfar_guard_cells"), 4),
            cfar_background=integer(values.get("cfar_background_cells"), 16),
        )


def load_header(header: bytes) -> Dict[str, float]:
    """读取 NewProtocolLayout.hpp 中本实验用到的帧头字段。"""

    return {
        "utc": struct.unpack_from("<f", header, 16)[0],
        "prt_counter": struct.unpack_from("<I", header, 20)[0],
        "lat_deg": struct.unpack_from("<d", header, 104)[0],
        "lon_deg": struct.unpack_from("<d", header, 112)[0],
        "height_m": struct.unpack_from("<d", header, 120)[0],
        "vn_mps": struct.unpack_from("<f", header, 128)[0],
        "ve_mps": struct.unpack_from("<f", header, 132)[0],
        "theta_deg": struct.unpack_from("<h", header, 218)[0] / 100.0,
    }


def read_raw(path: Path, p: Params) -> Tuple[np.ndarray, Dict[str, float]]:
    bytes_per_iq = 2 if p.iq_type.lower() in {"int16", "iq_int16", "short", "s16", "i16"} else 4
    packet_bytes = 256 + p.pulse_len * p.channel_count * 2 * bytes_per_iq
    raw_bytes = np.fromfile(path, dtype=np.uint8)
    if raw_bytes.size == 0 or raw_bytes.size % packet_bytes:
        raise RuntimeError(
            f"{path}: 文件大小 {raw_bytes.size} 不是 PRT 大小 {packet_bytes} 的整数倍"
        )
    packets = raw_bytes.reshape((-1, packet_bytes))
    if packets.shape[0] < p.pulse_num:
        raise RuntimeError(f"{path}: PRT 数 {packets.shape[0]} 小于配置 {p.pulse_num}")
    packets = packets[: p.pulse_num]
    header = load_header(packets[0, :256].tobytes())
    payload = packets[:, 256:]
    if bytes_per_iq == 4:
        values = np.ascontiguousarray(payload).view("<f4").reshape(
            p.pulse_num, p.pulse_len, p.channel_count, 2
        )
    else:
        values = np.ascontiguousarray(payload).view("<i2").reshape(
            p.pulse_num, p.pulse_len, p.channel_count, 2
        )
    iq = values[..., 0].astype(np.float64) + 1j * values[..., 1].astype(np.float64)
    return iq, header


def stable_path_offset(
    reference_range_m: np.ndarray,
    los: np.ndarray,
    offset: Tuple[float, float, float],
) -> np.ndarray:
    dot = los[0] * offset[0] + los[1] * offset[1] + los[2] * offset[2]
    offset_sq = offset[0] ** 2 + offset[1] ** 2 + offset[2] ** 2
    normalized = 1.0 - 2.0 * dot / reference_range_m + offset_sq / reference_range_m**2
    radial = np.sqrt(np.maximum(0.0, normalized))
    return (-2.0 * dot + offset_sq / reference_range_m) / (radial + 1.0)


def fusion_compensation(p: Params, header: Dict[str, float]) -> Tuple[np.ndarray, np.ndarray]:
    """对应 NewProtocolReader.cpp::buildFusionCompensationAtRange。"""

    n = np.arange(p.pulse_len, dtype=np.float64)
    ranges = 0.5 * C0 * (p.sample_delay_s + n / p.fs_hz)
    horizontal = np.sqrt(np.maximum(0.0, ranges**2 - max(0.0, header["height_m"]) ** 2))
    theta = math.radians(header["theta_deg"])
    side = -1.0 if p.squint_side == 1 else 1.0
    target = np.vstack(
        (
            side * horizontal * math.sin(theta),
            side * horizontal * math.cos(theta),
            np.full_like(horizontal, -max(0.0, header["height_m"])),
        )
    )
    los = target / ranges[None, :]
    wavelength = C0 / p.fc_hz
    phase_scale = p.carrier_phase_sign * TWO_PI / wavelength

    def pair(ref_channel: int, source_channel: int) -> np.ndarray:
        ref_delta = stable_path_offset(
            ranges, los, p.offsets_m[ref_channel - 1]
        )
        source_delta = stable_path_offset(
            ranges, los, p.offsets_m[source_channel - 1]
        )
        return np.exp(1j * phase_scale * (ref_delta - source_delta))

    return pair(p.read_channel_1, p.fusion_channel_3), pair(
        p.read_channel_2, p.fusion_channel_4
    )


def fuse_four_channels(
    raw: np.ndarray,
    p: Params,
    header: Dict[str, float],
    residual_override: Tuple[np.ndarray, np.ndarray] | None = None,
) -> Tuple[np.ndarray, Tuple[np.ndarray, np.ndarray]]:
    c1 = raw[:, :, p.read_channel_1 - 1]
    c2 = raw[:, :, p.read_channel_2 - 1]
    if not p.four_channel_fusion or p.channel_count < 4:
        return np.stack((c1, c2), axis=-1), (
            np.ones(p.pulse_len, dtype=np.complex128),
            np.ones(p.pulse_len, dtype=np.complex128),
        )
    c3 = raw[:, :, p.fusion_channel_3 - 1]
    c4 = raw[:, :, p.fusion_channel_4 - 1]
    if p.phase_comp_enable:
        comp13, comp24 = fusion_compensation(p, header)
        c3 = c3 * comp13[None, :]
        c4 = c4 * comp24[None, :]
        if residual_override is None:
            residual13 = np.sum(c1 * np.conj(c3), axis=0)
            residual24 = np.sum(c2 * np.conj(c4), axis=0)
            residual13 /= np.maximum(np.abs(residual13), 1.0e-300)
            residual24 /= np.maximum(np.abs(residual24), 1.0e-300)
        else:
            residual13, residual24 = residual_override
        c1 = 0.5 * (c1 + c3 * residual13[None, :])
        c2 = 0.5 * (c2 + c4 * residual24[None, :])
        return np.stack((c1, c2), axis=-1), (residual13, residual24)
    return np.stack((0.5 * (c1 + c3), 0.5 * (c2 + c4)), axis=-1), (
        np.ones(p.pulse_len, dtype=np.complex128),
        np.ones(p.pulse_len, dtype=np.complex128),
    )


def pulse_compress(fused: np.ndarray, p: Params) -> np.ndarray:
    """对应 pulseCompression.cpp 的零填充 FFT、频域相乘、逆 FFT、连续裁剪。"""

    chirp_rate = p.bandwidth_hz / p.pulse_duration_s
    frequencies = np.fft.fftfreq(p.range_fft_len, d=1.0 / p.fs_hz)
    hf = np.exp(1j * math.pi * frequencies**2 / chirp_rate)
    spectrum = np.fft.fft(fused, n=p.range_fft_len, axis=1)
    compressed = np.fft.ifft(spectrum * hf[None, :, None], axis=1)
    start = p.range_crop_start
    stop = start + p.range_compress_len
    if start < 0 or stop > p.range_fft_len:
        raise RuntimeError("range crop 超出 range_fft_len")
    return compressed[:, start:stop, :]


def estimate_fa_ctr(data1: np.ndarray, p: Params) -> float:
    corr = np.sum(data1[1:, :] * np.conj(data1[:-1, :]))
    return p.prf_hz / TWO_PI * float(np.angle(corr))


def current_axis(fa_ctr: float, p: Params) -> np.ndarray:
    return -0.5 * p.prf_hz + np.arange(p.pulse_num, dtype=np.float64) * (
        p.prf_hz / p.pulse_num
    ) + fa_ctr


def support_indices(axis: np.ndarray, fa_ctr: float, p: Params) -> Tuple[int, int, float, float]:
    support_half_deg = max(2.0, 0.5 * p.beam_width_deg)
    wavelength = C0 / p.fc_hz
    speed = 60.0
    bw = 2.0 * abs(speed) * math.sin(math.radians(support_half_deg)) / wavelength
    df = abs(axis[1] - axis[0]) if axis.size > 1 else 0.0
    fd_st = fa_ctr - bw - 0.5 * max(0.0, df)
    fd_ed = fa_ctr + bw + 0.5 * max(0.0, df)
    az_st = int(np.argmin(np.abs(axis - fd_st)))
    az_ed = int(np.argmin(np.abs(axis - fd_ed)))
    return az_st, az_ed, fd_st, fd_ed


def integer_align(data: np.ndarray, shift: int) -> np.ndarray:
    if shift == 0:
        return data.copy()
    out = np.zeros_like(data)
    if shift > 0:
        if shift < data.shape[0]:
            out[:-shift] = data[shift:]
    else:
        count = -shift
        if count < data.shape[0]:
            out[count:] = data[:-count]
    return out


def fractional_align(data: np.ndarray, shift: float) -> np.ndarray:
    """对 ch1 做 out[row]=data[row+shift] 的线性慢时间重采样。"""

    rows = np.arange(data.shape[0], dtype=np.float64) + shift
    valid = (rows >= 0.0) & (rows <= data.shape[0] - 1)
    low = np.floor(np.clip(rows, 0, data.shape[0] - 1)).astype(np.int64)
    high = np.minimum(low + 1, data.shape[0] - 1)
    frac = rows - low
    out = np.zeros_like(data)
    out[valid] = (1.0 - frac[valid, None]) * data[low[valid]] + frac[valid, None] * data[high[valid]]
    return out


def dbs_shift_index(fa_ctr: float, p: Params) -> int:
    center_num = math.floor((fa_ctr + 0.5 * p.prf_hz) / p.prf_hz * p.pulse_num) + 1
    cstart = center_num - p.pulse_num // 2
    k_1b = cstart
    while k_1b < 1:
        k_1b += p.pulse_num
    while k_1b > p.pulse_num:
        k_1b -= p.pulse_num
    return k_1b - 1


def rd_from_compressed(
    compressed: np.ndarray, shift: float, fa_ctr: float, p: Params
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    ch1 = compressed[:, :, 0]
    ch2 = compressed[:, :, 1]
    if abs(shift - round(shift)) < 1.0e-12:
        aligned1 = integer_align(ch1, int(round(shift)))
    else:
        aligned1 = fractional_align(ch1, shift)
    aligned2 = ch2.copy()
    preflip = np.where(np.arange(p.pulse_num)[:, None] % 2 == 0, 1.0, -1.0)
    spec1 = np.fft.fft(aligned1 * preflip, axis=0)
    spec2 = np.fft.fft(aligned2 * preflip, axis=0)
    k = dbs_shift_index(fa_ctr, p)
    if k:
        spec1 = np.roll(spec1, -k, axis=0)
        spec2 = np.roll(spec2, -k, axis=0)
    return spec1, spec2, current_axis(fa_ctr, p), k


def wrap_pi(values: np.ndarray | float) -> np.ndarray | float:
    return (np.asarray(values) + math.pi) % TWO_PI - math.pi


def weighted_line(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> Tuple[float, float] | None:
    valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(w) & (w > 0.0)
    if np.count_nonzero(valid) < 2:
        return None
    xv, yv, wv = x[valid], y[valid], w[valid]
    mean_x = np.sum(wv * xv) / np.sum(wv)
    mean_y = np.sum(wv * yv) / np.sum(wv)
    variance = np.sum(wv * (xv - mean_x) ** 2)
    if variance <= np.finfo(float).eps * np.sum(wv):
        return None
    k = float(np.sum(wv * (xv - mean_x) * (yv - mean_y)) / variance)
    return k, float(mean_y - k * mean_x)


def fit_p38(
    f1: np.ndarray,
    f2: np.ndarray,
    axis: np.ndarray,
    support_mask: np.ndarray,
    p: Params,
    shift: float,
) -> Dict[str, float | str | np.ndarray]:
    cols = slice(max(0, p.rg_st), min(f1.shape[1], p.rg_ed + 1))
    row_indices = np.flatnonzero(support_mask)
    cross = np.sum(f1[row_indices, cols] * np.conj(f2[row_indices, cols]), axis=1)
    energy1 = np.sum(np.abs(f1[row_indices, cols]) ** 2, axis=1)
    energy2 = np.sum(np.abs(f2[row_indices, cols]) ** 2, axis=1)
    energy = np.sqrt(np.maximum(0.0, energy1 * energy2))
    support_axis = axis[row_indices]
    finite = np.isfinite(support_axis) & np.isfinite(cross) & np.isfinite(energy) & (energy > 1.0e-12)
    if not np.any(finite):
        return phase_fallback(axis, p, shift, "theory_fixed_slope")
    median_energy = float(np.median(energy[finite]))
    q90 = float(np.quantile(energy[finite], 0.90))
    floor = max(1.0e-12, 1.0e-6 * median_energy, p.p38_min_peak_energy_fraction * q90)
    magnitude = np.abs(cross)
    coherence = np.divide(magnitude, np.maximum(energy, 1.0e-300))
    accepted = finite & (energy > floor) & (magnitude > 1.0e-15) & (coherence >= 0.05)
    if np.count_nonzero(accepted) < 2:
        return phase_fallback(axis, p, shift, "theory_fixed_slope")

    x = support_axis[accepted]
    raw_phase = np.angle(cross[accepted])
    order = np.argsort(x)
    x = x[order]
    raw_phase = raw_phase[order]
    accepted_indices = np.flatnonzero(accepted)[order]
    relative_energy = np.minimum(1.0, energy[accepted_indices] / median_energy)
    base_weight = np.sqrt(np.maximum(0.0, relative_energy)) * coherence[accepted_indices] ** 2
    unwrapped = np.unwrap(raw_phase)
    fit = weighted_line(x, unwrapped, base_weight)
    if fit is None:
        return phase_fallback(axis, p, shift, "theory_fixed_slope")
    k, b = fit
    huber_delta = 0.20
    for _ in range(16):
        residual = wrap_pi(raw_phase - (k * x + b))
        abs_residual = np.abs(residual)
        huber = np.where(abs_residual <= huber_delta, 1.0, huber_delta / np.maximum(abs_residual, 1.0e-300))
        refined = weighted_line(x, (k * x + b) + residual, base_weight * huber)
        if refined is None:
            break
        new_k, new_b = refined
        change = abs(new_k - k) * (x[-1] - x[0]) * 0.5 + abs(new_b - b)
        k, b = new_k, new_b
        if change <= 1.0e-11:
            break
    for _ in range(4):
        residual = wrap_pi(raw_phase - (k * x + b))
        inlier = np.abs(residual) <= 0.35
        refined = weighted_line(x, (k * x + b) + residual, base_weight * inlier)
        if refined is None:
            break
        new_k, new_b = refined
        k, b = new_k, new_b
    residual = wrap_pi(raw_phase - (k * x + b))
    inlier = np.abs(residual) <= 0.35
    rmse = float(np.sqrt(np.sum(base_weight[inlier] * residual[inlier] ** 2) / np.maximum(np.sum(base_weight[inlier]), 1.0e-300))) if np.any(inlier) else math.inf
    inlier_ratio = float(np.count_nonzero(inlier) / x.size)
    min_samples = min(int(np.count_nonzero(accepted)), max(2, p.p38_min_sample_count))
    valid = bool(np.count_nonzero(inlier) >= min_samples and inlier_ratio >= p.p38_min_inlier_ratio and rmse <= p.p38_max_rmse_rad)
    if not valid:
        return phase_fallback(axis, p, shift, "theory_fixed_slope")
    phase_rows = wrap_pi(k * axis + b)
    return {
        "k": float(k),
        "b": float(b),
        "rmse": rmse,
        "inlier_ratio": inlier_ratio,
        "sample_count": float(x.size),
        "source": "observed",
        "phase_rows": phase_rows,
    }


def phase_fallback(axis: np.ndarray, p: Params, shift: float, source: str) -> Dict[str, float | str | np.ndarray]:
    raw_k = p.carrier_phase_sign * TWO_PI * (0.5 * p.d_channel_m) / 60.0
    k = raw_k + TWO_PI * shift / p.prf_hz
    return {
        "k": float(k),
        "b": 0.0,
        "rmse": math.nan,
        "inlier_ratio": 0.0,
        "sample_count": 0.0,
        "source": source,
        "phase_rows": wrap_pi(k * axis),
    }


def fit_range_phase(f1: np.ndarray, f2: np.ndarray, p: Params) -> np.ndarray:
    """对应 rg_correct.cpp 的 arg(sum_az(F1*conj(F2))) + 二次拟合。"""

    cross = np.sum(f1[:, p.rg_st : p.rg_ed + 1] * np.conj(f2[:, p.rg_st : p.rg_ed + 1]), axis=0)
    phase = np.angle(cross)
    x = np.arange(p.rg_st + 1, p.rg_ed + 2, dtype=np.float64)
    valid = np.isfinite(phase) & np.isfinite(x)
    if np.count_nonzero(valid) < 3:
        return np.zeros(f1.shape[1], dtype=np.float64)
    coeff = np.polyfit(x[valid], phase[valid], 2)
    for _ in range(3):
        residual = phase - np.polyval(coeff, x)
        inlier = valid & (np.abs(residual) < 0.1 * math.pi)
        if np.count_nonzero(inlier) < 3:
            break
        coeff = np.polyfit(x[inlier], phase[inlier], 2)
    full_x = np.arange(1, f1.shape[1] + 1, dtype=np.float64)
    return np.polyval(coeff, full_x)


def row_alpha(f1: np.ndarray, f2: np.ndarray, p: Params) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    region1 = f1[:, p.rg_st : p.rg_ed + 1]
    region2 = f2[:, p.rg_st : p.rg_ed + 1]
    cross = np.sum(region1 * np.conj(region2), axis=1)
    energy2 = np.sum(np.abs(region2) ** 2, axis=1)
    alpha = cross / np.maximum(energy2, 1.0e-300)
    coherence = np.abs(cross) / np.maximum(
        np.sqrt(np.sum(np.abs(region1) ** 2, axis=1) * energy2), 1.0e-300
    )
    return alpha, coherence, cross


def measured_support(
    f1: np.ndarray, f2: np.ndarray, current_mask: np.ndarray, p: Params
) -> np.ndarray:
    alpha, coherence, _ = row_alpha(f1, f2, p)
    row_power = 0.5 * (
        np.mean(np.abs(f1[:, p.rg_st : p.rg_ed + 1]) ** 2, axis=1)
        + np.mean(np.abs(f2[:, p.rg_st : p.rg_ed + 1]) ** 2, axis=1)
    )
    outside = row_power[~current_mask]
    floor = float(np.median(outside)) if outside.size else float(np.median(row_power))
    candidate = current_mask & (coherence >= 0.05) & (row_power >= floor * 2.0)
    if np.count_nonzero(candidate) < 8:
        return current_mask.copy()
    lo = int(np.flatnonzero(candidate)[0])
    hi = int(np.flatnonzero(candidate)[-1])
    mask = np.zeros_like(current_mask)
    mask[lo : hi + 1] = True
    return mask


def current_cancel(
    f1: np.ndarray, f2: np.ndarray, phase_rows: np.ndarray, support: np.ndarray
) -> np.ndarray:
    out = f1.copy()
    for r in np.flatnonzero(support):
        a = f1[r]
        b2 = np.exp(1j * phase_rows[r]) * f2[r]
        aa = np.abs(a)
        bb = np.abs(b2)
        m = np.minimum(aa, bb)
        a_eq = np.divide(m, np.maximum(aa, 1.0e-300)) * a
        b_eq = np.divide(m, np.maximum(bb, 1.0e-300)) * b2
        out[r] = a_eq - b_eq
    return out


def oracle_amplitude_cancel(
    f1: np.ndarray,
    f2: np.ndarray,
    phase_rows: np.ndarray,
    alpha_abs: np.ndarray,
    support: np.ndarray,
) -> np.ndarray:
    out = f1.copy()
    for r in np.flatnonzero(support):
        out[r] = f1[r] - alpha_abs[r] * np.exp(1j * phase_rows[r]) * f2[r]
    return out


def direct_weight_cancel(
    f1: np.ndarray, f2: np.ndarray, alpha: np.ndarray, support: np.ndarray
) -> np.ndarray:
    out = f1.copy()
    for r in np.flatnonzero(support):
        out[r] = f1[r] - alpha[r] * f2[r]
    return out


def go_alpha(pfa: float, guard: int, background: int) -> float:
    key = (float(pfa), int(guard), int(background))
    if key in GO_ALPHA_CACHE:
        return GO_ALPHA_CACHE[key]
    # 与 include/go_cfar_alpha.hpp 相同的四向带结构；随机流只用于校准
    # 阈值，不进入任何数据或输出摘要。
    rng = np.random.Generator(np.random.MT19937(0x9E3779B9))
    samples = 262_144
    center_cells = background * (2 * guard + 1)
    corner_cells = background * background
    strip_cells = background * (2.0 * (guard + background) + 1.0)
    tl = rng.gamma(corner_cells, 1.0, samples)
    tc = rng.gamma(center_cells, 1.0, samples)
    tr = rng.gamma(corner_cells, 1.0, samples)
    lc = rng.gamma(center_cells, 1.0, samples)
    rc = rng.gamma(center_cells, 1.0, samples)
    bl = rng.gamma(corner_cells, 1.0, samples)
    bc = rng.gamma(center_cells, 1.0, samples)
    br = rng.gamma(corner_cells, 1.0, samples)
    maximum = np.maximum.reduce(
        ((tl + lc + bl) / strip_cells, (tr + rc + br) / strip_cells,
         (tl + tc + tr) / strip_cells, (bl + bc + br) / strip_cells)
    )

    def probability(alpha: float) -> float:
        return float(np.mean(np.exp(-alpha * maximum)))

    low, high = 0.0, 1.0
    while probability(high) > pfa:
        high *= 2.0
    for _ in range(48):
        mid = 0.5 * (low + high)
        if probability(mid) > pfa:
            low = mid
        else:
            high = mid
    GO_ALPHA_CACHE[key] = 0.5 * (low + high)
    return GO_ALPHA_CACHE[key]


def rect_sum(integral: np.ndarray, r0: np.ndarray, r1: np.ndarray, c0: np.ndarray, c1: np.ndarray) -> np.ndarray:
    return (
        integral[r1 + 1, c1 + 1]
        - integral[r0, c1 + 1]
        - integral[r1 + 1, c0]
        + integral[r0, c0]
    )


def go_cfar(
    detect: np.ndarray,
    truth_row: int,
    truth_col: int,
    p: Params,
) -> Dict[str, float]:
    power = np.abs(detect) ** 2
    h, w = power.shape
    g, b = p.cfar_guard, p.cfar_background
    radius = g + b
    rows = np.arange(h, dtype=np.int64)[:, None]
    cols = np.arange(radius, w - radius, dtype=np.int64)[None, :]
    rowpad = np.concatenate((power[-radius:], power, power[:radius]), axis=0)
    padded = np.pad(rowpad, ((0, 0), (radius, radius)), mode="constant")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(axis=0).cumsum(axis=1)

    def rect(r0: np.ndarray, r1: np.ndarray, c0: np.ndarray, c1: np.ndarray) -> np.ndarray:
        return rect_sum(integral, r0, r1, c0, c1)

    full = rect(rows, rows + 2 * radius, cols, cols + 2 * radius)
    guard_sum = rect(
        rows + radius - g,
        rows + radius + g,
        cols + radius - g,
        cols + radius + g,
    )
    total_bg = (2 * radius + 1) ** 2 - (2 * g + 1) ** 2
    # 源生产配置是 GO；保留四向带的精确几何，不把 CA 系数误用于 GO。
    left = rect(rows, rows + 2 * radius, cols, cols + radius - g - 1) / ((2 * radius + 1) * b)
    right = rect(rows, rows + 2 * radius, cols + radius + g + 1, cols + 2 * radius) / ((2 * radius + 1) * b)
    top = rect(rows, rows + radius - g - 1, cols, cols + 2 * radius) / ((2 * radius + 1) * b)
    bottom = rect(rows + radius + g + 1, rows + 2 * radius, cols, cols + 2 * radius) / ((2 * radius + 1) * b)
    noise_level = np.maximum.reduce((left, right, top, bottom))
    alpha = go_alpha(p.cfar_pfa, g, b)
    threshold = alpha * noise_level
    cut_power = power[:, radius : w - radius]
    hits = cut_power > threshold
    target_rows = slice(max(0, truth_row - 2), min(h, truth_row + 3))
    target_cols_start = max(radius, truth_col - 2)
    target_cols_stop = min(w - radius, truth_col + 3)
    target_col_slice = slice(target_cols_start - radius, target_cols_stop - radius)
    target_hits = hits[target_rows, target_col_slice]
    target_power = cut_power[target_rows, target_col_slice]
    outside = hits.copy()
    outside[target_rows, target_col_slice] = False
    return {
        "Pd": float(bool(np.any(target_hits))),
        "false_alarm_count": float(np.count_nonzero(outside)),
        "cfar_alpha": float(alpha),
        "target_peak_power": float(np.max(target_power)) if target_power.size else 0.0,
        "cfar_candidate_cells": float(np.count_nonzero(hits)),
    }


@dataclass
class Alignment:
    name: str
    shift: float
    f1_bg: np.ndarray
    f2_bg: np.ndarray
    f1_target: np.ndarray
    f2_target: np.ndarray
    axis: np.ndarray
    dbs_k: int
    range_phase: np.ndarray
    p38: Dict[str, float | str | np.ndarray]
    alpha: np.ndarray
    coherence: np.ndarray
    row_phase: np.ndarray
    current_support: np.ndarray
    oracle_support: np.ndarray


def make_alignment(
    name: str,
    shift: float,
    bg_compressed: np.ndarray,
    target_compressed: np.ndarray,
    fa_ctr: float,
    axis: np.ndarray,
    current_support: np.ndarray,
    p: Params,
    estimation_source: str = "background",
) -> Alignment:
    f1_bg_raw, f2_bg_raw, _, dbs_k = rd_from_compressed(bg_compressed, shift, fa_ctr, p)
    f1_target_raw, f2_target_raw, _, _ = rd_from_compressed(target_compressed, shift, fa_ctr, p)
    if estimation_source == "target":
        estimate_f1_raw, estimate_f2_raw = f1_target_raw, f2_target_raw
    elif estimation_source == "background":
        estimate_f1_raw, estimate_f2_raw = f1_bg_raw, f2_bg_raw
    else:
        raise ValueError(f"unknown alignment estimation source: {estimation_source}")
    range_phase = fit_range_phase(estimate_f1_raw, estimate_f2_raw, p)
    correction = np.exp(1j * range_phase)[None, :]
    f1_bg, f2_bg = f1_bg_raw, f2_bg_raw * correction
    f1_target, f2_target = f1_target_raw, f2_target_raw * correction
    estimate_f1, estimate_f2 = (
        (f1_target, f2_target) if estimation_source == "target" else (f1_bg, f2_bg)
    )
    p38 = fit_p38(estimate_f1, estimate_f2, axis, current_support, p, shift)
    alpha, coherence, _ = row_alpha(f1_bg, f2_bg, p)
    row_phase = np.angle(alpha)
    oracle_support = measured_support(f1_bg, f2_bg, current_support, p)
    return Alignment(
        name=name,
        shift=shift,
        f1_bg=f1_bg,
        f2_bg=f2_bg,
        f1_target=f1_target,
        f2_target=f2_target,
        axis=axis,
        dbs_k=dbs_k,
        range_phase=range_phase,
        p38=p38,
        alpha=alpha,
        coherence=coherence,
        row_phase=row_phase,
        current_support=current_support,
        oracle_support=oracle_support,
    )


def variant_outputs(
    a: Alignment,
    phase_mode: str,
    amplitude_mode: str,
    support_mode: str,
    direct_weight: bool = False,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if phase_mode == "oracle":
        phase = a.row_phase
    else:
        phase = np.asarray(a["p38"]["phase_rows"] if isinstance(a, dict) else a.p38["phase_rows"])
    support = a.oracle_support if support_mode == "oracle" else a.current_support
    if direct_weight:
        bg = direct_weight_cancel(a.f1_bg, a.f2_bg, a.alpha, support)
        target = direct_weight_cancel(a.f1_target, a.f2_target, a.alpha, support)
    elif amplitude_mode == "oracle":
        amp = np.abs(a.alpha)
        bg = oracle_amplitude_cancel(a.f1_bg, a.f2_bg, phase, amp, support)
        target = oracle_amplitude_cancel(a.f1_target, a.f2_target, phase, amp, support)
    else:
        bg = current_cancel(a.f1_bg, a.f2_bg, phase, support)
        target = current_cancel(a.f1_target, a.f2_target, phase, support)
    det_bg = a.f2_bg.copy()
    det_target = a.f2_target.copy()
    det_bg[support] = bg[support]
    det_target[support] = target[support]
    return bg, target, det_bg, det_target


def db(value: float) -> float:
    return float(10.0 * math.log10(max(value, 1.0e-300)))


def metrics(
    a: Alignment,
    bg_out: np.ndarray,
    target_out: np.ndarray,
    det_bg: np.ndarray,
    det_target: np.ndarray,
    truth_row: int,
    truth_col: int,
    active_support: np.ndarray,
    p: Params,
) -> Dict[str, float]:
    r0, r1 = max(0, truth_row - 2), min(a.f1_bg.shape[0], truth_row + 3)
    c0, c1 = max(0, truth_col - 2), min(a.f1_bg.shape[1], truth_col + 3)
    roi = (slice(r0, r1), slice(c0, c1))
    before_power = np.minimum(np.abs(a.f1_bg[roi]), np.abs(a.f2_bg[roi])) ** 2
    after_power = np.abs(bg_out[roi]) ** 2
    signal_before = a.f1_target[roi] - a.f1_bg[roi]
    signal_after = target_out[roi] - bg_out[roi]
    before_clutter = float(np.mean(before_power))
    after_clutter = float(np.mean(after_power))
    signal_power_before = float(np.mean(np.abs(signal_before) ** 2))
    signal_power_after = float(np.mean(np.abs(signal_after) ** 2))
    input_scnr = db(signal_power_before / max(before_clutter, 1.0e-300))
    output_scnr = db(signal_power_after / max(after_clutter, 1.0e-300))
    cfar = go_cfar(det_target, truth_row, truth_col, p)
    active_power = np.abs(bg_out[active_support, :]) ** 2
    active_median = float(np.median(active_power)) if active_power.size else 0.0
    high_energy_threshold = active_median * 10.0 ** (15.0 / 10.0)
    return {
        "CA_ROI_dB": db(before_clutter / max(after_clutter, 1.0e-300)),
        "CSR_ROI_proxy_dB": db(before_clutter / max(after_clutter, 1.0e-300)),
        "input_SCNR_dB": input_scnr,
        "output_SCNR_dB": output_scnr,
        "SCNR_improvement_dB": output_scnr - input_scnr,
        "target_loss_dB": db(signal_power_after / max(signal_power_before, 1.0e-300)),
        "target_signal_power_before": signal_power_before,
        "target_signal_power_after": signal_power_after,
        "background_power_before": before_clutter,
        "background_power_after": after_clutter,
        "residual_high_energy_count": float(
            np.count_nonzero(active_power > high_energy_threshold)
        ),
        "residual_high_energy_points": float(
            np.count_nonzero(active_power > high_energy_threshold)
        ),
        "residual_high_energy_threshold_db_over_active_median": 15.0,
        **cfar,
    }


def write_csv(path: Path, rows: Iterable[Dict[str, object]]) -> None:
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: List[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def plot_results(single: List[Dict[str, object]], loo: List[Dict[str, object]], out_dir: Path) -> None:
    names = [str(row["variant"]) for row in single]
    scnr = [float(row["SCNR_improvement_dB"]) for row in single]
    fig, ax = plt.subplots(figsize=(10, 4.8))
    ax.bar(names, scnr, color=["#4c78a8"] + ["#f58518"] * (len(names) - 1))
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_ylabel("SCNR improvement (dB)")
    ax.set_title("Current vs Oracle CSI cancellation")
    ax.tick_params(axis="x", rotation=25)
    fig.tight_layout()
    fig.savefig(out_dir / "current_vs_oracle_scnr_improvement.png", dpi=150)
    plt.close(fig)

    current = next(row for row in single if row["variant"] == "Current")
    all_row = next(row for row in single if row["variant"] == "Oracle All")
    loo_map = {str(row["variant"]): float(row["SCNR_improvement_dB"]) for row in loo}
    labels = ["Phase", "Amplitude", "Delay", "Support", "Residual gap"]
    contributions = [
        float(all_row["SCNR_improvement_dB"]) - loo_map.get("LOO_phase", float(all_row["SCNR_improvement_dB"])),
        float(all_row["SCNR_improvement_dB"]) - loo_map.get("LOO_amplitude", float(all_row["SCNR_improvement_dB"])),
        float(all_row["SCNR_improvement_dB"]) - loo_map.get("LOO_delay", float(all_row["SCNR_improvement_dB"])),
        float(all_row["SCNR_improvement_dB"]) - loo_map.get("LOO_support", float(all_row["SCNR_improvement_dB"])),
        float(all_row["SCNR_improvement_dB"]) - float(current["SCNR_improvement_dB"]),
    ]
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.bar(labels, contributions, color="#54a24b")
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_ylabel("Attributable SCNR gap (dB)")
    ax.set_title("Single-error-out attribution (offline proxy)")
    ax.tick_params(axis="x", rotation=15)
    fig.tight_layout()
    fig.savefig(out_dir / "mismatch_contribution.png", dpi=150)
    plt.close(fig)

    chosen = ["CA_ROI_dB", "target_loss_dB", "Pd"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5))
    for axis, key in zip(axes, chosen):
        values = [float(row[key]) for row in single]
        axis.bar(names, values, color="#e45756")
        axis.set_title(key)
        axis.tick_params(axis="x", rotation=25)
        if key == "Pd":
            axis.set_ylim(0.0, 1.1)
    fig.suptitle("Cancellation quality, target preservation and detection proxy")
    fig.tight_layout()
    fig.savefig(out_dir / "ca_target_loss_pd.png", dpi=150)
    plt.close(fig)


def read_truth(path: Path) -> Tuple[int, int, str, float]:
    with path.open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    return int(float(row["row_truth"])), int(float(row["expected_bin"])), row["target_id"], float(row["af_total_truth_hz"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path("outputs/ai_csi_oracle/stage2_target/data/stage2_statistical_newprotocol_period_0000.bin"))
    parser.add_argument("--background", type=Path, default=Path("outputs/ai_csi_oracle/stage2_background/data/stage2_statistical_newprotocol_period_0000.bin"))
    parser.add_argument("--xml", type=Path, default=Path("outputs/ai_csi_oracle/stage2_target/config/temp_config_stage2_period_0000.xml"))
    parser.add_argument("--truth", type=Path, default=Path("outputs/ai_csi_oracle/stage2_target/truth/truth_targets_by_beam.csv"))
    parser.add_argument("--out", type=Path, default=Path("outputs/ai_csi_oracle"))
    parser.add_argument(
        "--case-id",
        default="ai_csi_stage2_typical_1beam_20260908",
        help="写入汇总的场景 ID，便于把典型/非理想场景合并",
    )
    args = parser.parse_args()

    p = Params.from_xml(args.xml)
    truth_row, truth_col, target_id, target_fd = read_truth(args.truth)
    bg_raw, bg_header = read_raw(args.background, p)
    target_raw, target_header = read_raw(args.target, p)
    if bg_raw.shape != target_raw.shape:
        raise RuntimeError("C+N 与 S+C+N 原始数组尺寸不一致")

    bg_fused, residual = fuse_four_channels(bg_raw, p, bg_header)
    # Stage2 生成器先冻结 C+N 包，再把目标叠加到同一包；沿用背景估计的
    # 四通道残余因子，保证后续差分只代表目标回波而不是目标驱动的估计漂移。
    target_fused, _ = fuse_four_channels(target_raw, p, target_header, residual)
    bg_compressed = pulse_compress(bg_fused, p)
    target_compressed = pulse_compress(target_fused, p)
    bg_compressed[:, :, 1] *= p.calib_coef
    target_compressed[:, :, 1] *= p.calib_coef
    fa_ctr = estimate_fa_ctr(bg_compressed[:, :, 0], p)
    axis = current_axis(fa_ctr, p)
    az_st, az_ed, fd_st, fd_ed = support_indices(axis, fa_ctr, p)
    current_support = np.zeros(p.pulse_num, dtype=bool)
    current_support[az_st : az_ed + 1] = True
    speed = 60.0
    delay_s = (0.5 * p.d_channel_m) / speed
    shift_truth = delay_s * p.prf_hz
    shift_current = float(round(shift_truth))

    align_current = make_alignment(
        "integer_current", shift_current, bg_compressed, target_compressed,
        fa_ctr, axis, current_support, p, estimation_source="target"
    )
    align_oracle_delay = make_alignment(
        "fractional_oracle_delay", shift_truth, bg_compressed, target_compressed,
        fa_ctr, axis, current_support, p, estimation_source="background"
    )
    state_by_name = {
        "current": align_current,
        "oracle_delay": align_oracle_delay,
    }

    specs = [
        ("Current", "current", "current", "current", "current", False),
        ("Oracle phase", "current", "oracle", "current", "current", False),
        ("Oracle amplitude", "current", "current", "oracle", "current", False),
        ("Oracle delay", "oracle_delay", "current", "current", "current", False),
        ("Oracle support", "current", "current", "current", "oracle", False),
        ("Oracle weight", "current", "oracle", "oracle", "current", True),
        ("Oracle All", "oracle_delay", "oracle", "oracle", "oracle", True),
    ]
    single_rows: List[Dict[str, object]] = []
    for variant, state_name, phase_mode, amplitude_mode, support_mode, direct in specs:
        alignment = state_by_name[state_name]
        bg_out, target_out, det_bg, det_target = variant_outputs(
            alignment, phase_mode, amplitude_mode, support_mode, direct
        )
        row: Dict[str, object] = {
            "variant": variant,
            "experiment": args.case_id,
            "target_id": target_id,
            "alignment_shift_prt": alignment.shift,
            "alignment_shift_truth_prt": shift_truth,
            "alignment_estimation_source": "target_observation" if state_name == "current" else "background_clutter_noise",
            "support_mode": support_mode,
            "phase_mode": phase_mode,
            "amplitude_mode": "oracle_complex_ls" if direct else amplitude_mode,
            "direct_weight_from_background_only": int(direct),
            "p38_k_rad_per_hz": float(alignment.p38["k"]),
            "p38_b_rad": float(alignment.p38["b"]),
            "p38_rmse_rad": float(alignment.p38["rmse"]),
            "p38_fit_source": str(alignment.p38["source"]),
            "support_row_start": int(np.flatnonzero(alignment.oracle_support if support_mode == "oracle" else current_support)[0]),
            "support_row_end": int(np.flatnonzero(alignment.oracle_support if support_mode == "oracle" else current_support)[-1]),
        }
        active_support = alignment.oracle_support if support_mode == "oracle" else current_support
        row.update(metrics(alignment, bg_out, target_out, det_bg, det_target, truth_row, truth_col, active_support, p))
        single_rows.append(row)

    loo_specs = [
        ("LOO_phase", "oracle_delay", "current", "oracle", "oracle", False),
        ("LOO_amplitude", "oracle_delay", "oracle", "current", "oracle", False),
        ("LOO_delay", "current", "oracle", "oracle", "oracle", False),
        ("LOO_support", "oracle_delay", "oracle", "oracle", "current", False),
    ]
    loo_rows: List[Dict[str, object]] = []
    for variant, state_name, phase_mode, amplitude_mode, support_mode, direct in loo_specs:
        alignment = state_by_name[state_name]
        bg_out, target_out, det_bg, det_target = variant_outputs(
            alignment, phase_mode, amplitude_mode, support_mode, direct
        )
        row = {
            "variant": variant,
            "experiment": args.case_id,
            "target_id": target_id,
            "all_truth_except": variant.removeprefix("LOO_"),
            "alignment_shift_prt": alignment.shift,
            "alignment_estimation_source": "target_observation" if state_name == "current" else "background_clutter_noise",
            "support_mode": support_mode,
            "phase_mode": phase_mode,
            "amplitude_mode": amplitude_mode,
            "p38_k_rad_per_hz": float(alignment.p38["k"]),
            "p38_b_rad": float(alignment.p38["b"]),
            "p38_rmse_rad": float(alignment.p38["rmse"]),
            "p38_fit_source": str(alignment.p38["source"]),
        }
        active_support = alignment.oracle_support if support_mode == "oracle" else current_support
        row.update(metrics(alignment, bg_out, target_out, det_bg, det_target, truth_row, truth_col, active_support, p))
        loo_rows.append(row)

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "single_factor_summary.csv", single_rows)
    write_csv(out_dir / "leave_one_out_summary.csv", loo_rows)

    current = next(row for row in single_rows if row["variant"] == "Current")
    oracle_all = next(row for row in single_rows if row["variant"] == "Oracle All")
    overall = {
        "experiment": args.case_id,
        "target_id": target_id,
        "target_fd_truth_hz": target_fd,
        "target_row_truth": truth_row,
        "target_range_bin_truth": truth_col,
        "fa_ctr_wrapped_hz": fa_ctr,
        "axis_row0_hz": float(axis[0]),
        "axis_df_hz": float(axis[1] - axis[0]),
        "dynamic_support_row_start": az_st,
        "dynamic_support_row_end": az_ed,
        "dynamic_support_fd_start_hz": fd_st,
        "dynamic_support_fd_end_hz": fd_ed,
        "physical_delay_s": delay_s,
        "physical_delay_prt": shift_truth,
        "current_integer_delay_prt": shift_current,
        "go_cfar_alpha": go_alpha(p.cfar_pfa, p.cfar_guard, p.cfar_background),
        "current_scnr_improvement_dB": current["SCNR_improvement_dB"],
        "oracle_all_scnr_improvement_dB": oracle_all["SCNR_improvement_dB"],
        "signed_oracle_minus_current_scnr_dB": float(oracle_all["SCNR_improvement_dB"]) - float(current["SCNR_improvement_dB"]),
        "recoverable_scnr_gap_dB": max(0.0, float(oracle_all["SCNR_improvement_dB"]) - float(current["SCNR_improvement_dB"])),
        "gpu_production_run": False,
        "evidence_boundary": "CPU offline replay of current source operators; CUDA production execution not claimed because no NVIDIA driver is available",
    }
    with (out_dir / "oracle_overall_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(overall), lineterminator="\n")
        writer.writeheader()
        writer.writerow(overall)

    manifest = {
        "script": "scripts/run_ai_csi_oracle.py",
        "case_id": args.case_id,
        "target_bin": str(args.target),
        "background_bin": str(args.background),
        "xml": str(args.xml),
        "truth": str(args.truth),
        "params": {
            "pulse_num": p.pulse_num,
            "pulse_len": p.pulse_len,
            "channel_count": p.channel_count,
            "range_fft_len": p.range_fft_len,
            "range_crop_start": p.range_crop_start,
            "range_compress_len": p.range_compress_len,
            "fc_hz": p.fc_hz,
            "fs_hz": p.fs_hz,
            "prf_hz": p.prf_hz,
            "d_channel_m": p.d_channel_m,
            "calib_coef": p.calib_coef,
        },
        "fa_ctr_wrapped_hz": fa_ctr,
        "support_rows": [az_st, az_ed],
        "alignment": {"truth_prt": shift_truth, "current_prt": shift_current},
        "fusion_residual_phase_mean_rad": [
            float(np.angle(np.mean(residual[0]))),
            float(np.angle(np.mean(residual[1]))),
        ],
        "parameter_estimation_policy": "Current range-phase/P38 use the measured S+C+N target observation; Oracle phase/amplitude/support/weight use only paired C+N, and the resulting weight is fixed before applying it to S+C+N. Target truth is used only for evaluation ROI/labels.",
        "metrics_definition": "CA/CSR use the source tap's min(|F1|,|F2|)^2 C+N ROI proxy; SCNR and target loss use paired S+C+N minus C+N; residual_high_energy_points count active-support C+N output cells above 15 dB over the active median; Pd/false alarms are a CPU GO-CFAR cell proxy.",
        "source_provenance": [
            "src/dbs/NewProtocolReader.cpp",
            "src/pulseCompression.cpp",
            "src/alignFFTAndDBS.cpp",
            "src/processOnePeriod.cpp",
            "src/clutter_CSI.cpp",
            "src/rg_correct.cpp",
            "src/computeDynamicSupportDomain.cpp",
            "src/gpu/gpu_kernels.cu",
        ],
    }
    with (out_dir / "oracle_analysis_manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    plot_results(single_rows, loo_rows, out_dir)
    print(json.dumps(overall, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # keep a concise command-line failure
        print(f"[ai-csi-oracle][ERR] {exc}", file=sys.stderr)
        raise
