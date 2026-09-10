#!/usr/bin/env python3
"""审计 phase-corrected Current CSI 的二维残差、门控行和慢时间纹理。

输入必须来自显式开启 ``csi_metrics_dump_power_maps`` 和
``csi_metrics_dump_intermediate_maps`` 的生产 tap。脚本不重新实现 CSI；它只
读取 production tap 中当前对齐后的复数 F1/F2、P38 拟合和坐标轴，并生成审计
旁路产物。旧 tap 若没有 channel{1,2}_{real,imag}_path 会明确失败，避免把
pre-CSI CTDR 快照误当作当前 F1/F2。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np


EPS = 1.0e-12


def as_float(value: str) -> float:
    return float(value)


def path_from_manifest(value: str, manifest: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (manifest.parent / path).resolve()


def read_selected_row(manifest: Path, period_id: int | None, beam_id: int | None) -> dict[str, str]:
    with manifest.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit(f"manifest 没有数据行：{manifest}")
    selected = [row for row in rows
                if (period_id is None or int(row["period_id"]) == period_id)
                and (beam_id is None or int(row["beam_id"]) == beam_id)]
    if len(selected) != 1:
        raise SystemExit(
            f"manifest 选择不唯一：period_id={period_id!r}, beam_id={beam_id!r}, "
            f"匹配 {len(selected)} 行；请显式指定两个 id")
    return selected[0]


def load_array(row: dict[str, str], key: str, manifest: Path) -> np.ndarray:
    value = row.get(key, "")
    if not value:
        raise SystemExit(f"manifest 缺少 {key}；请用新版 production CSI tap 重跑 probe")
    path = path_from_manifest(value, manifest)
    if not path.is_file():
        raise SystemExit(f"找不到 {key} 指向的数组：{path}")
    return np.asarray(np.load(path), dtype=np.float64)


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def finite_values(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    selected = np.asarray(values[mask], dtype=np.float64)
    return selected[np.isfinite(selected)]


def weighted_phase_stats(values: np.ndarray, weights: np.ndarray | None = None) -> dict[str, object]:
    values = np.asarray(values, dtype=np.float64)
    good = np.isfinite(values)
    values = values[good]
    if weights is None:
        weights = np.ones(values.shape, dtype=np.float64)
    else:
        weights = np.asarray(weights, dtype=np.float64)[good]
    good_weight = np.isfinite(weights) & (weights > 0.0)
    values = values[good_weight]
    weights = weights[good_weight]
    if values.size == 0 or float(np.sum(weights)) <= 0.0:
        return {"n": 0, "circular_mean_rad": None, "circular_std_rad": None,
                "rmse_rad": None, "p50_abs_rad": None, "p90_abs_rad": None,
                "p95_abs_rad": None, "resultant_length": None}
    total = float(np.sum(weights))
    unit = np.exp(1j * values)
    mean_vector = np.sum(weights * unit) / total
    resultant = float(np.clip(abs(mean_vector), 0.0, 1.0))
    circular_std = math.sqrt(max(0.0, -2.0 * math.log(max(resultant, EPS))))
    return {
        "n": int(values.size),
        "circular_mean_rad": float(np.angle(mean_vector)),
        "circular_std_rad": circular_std,
        "rmse_rad": float(math.sqrt(np.sum(weights * values * values) / total)),
        "p50_abs_rad": float(np.percentile(np.abs(values), 50.0)),
        "p90_abs_rad": float(np.percentile(np.abs(values), 90.0)),
        "p95_abs_rad": float(np.percentile(np.abs(values), 95.0)),
        "resultant_length": resultant,
    }


def scalar_stats(values: np.ndarray) -> dict[str, object]:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"n": 0, "median": None, "mean": None, "std": None,
                "cv": None, "p90": None, "p95": None}
    mean = float(np.mean(values))
    return {
        "n": int(values.size),
        "median": float(np.median(values)),
        "mean": mean,
        "std": float(np.std(values, ddof=1)) if values.size > 1 else 0.0,
        "cv": float(np.std(values) / abs(mean)) if abs(mean) > EPS else None,
        "p90": float(np.percentile(values, 90.0)),
        "p95": float(np.percentile(values, 95.0)),
    }


def phase_group_row(values: np.ndarray, weights: np.ndarray, label: object,
                    x: object, n_total: int) -> dict[str, object]:
    stats = weighted_phase_stats(values, weights)
    stats.update({"index": label, "coordinate": x, "n_total": int(n_total)})
    return stats


def fit_line(x: np.ndarray, y: np.ndarray) -> dict[str, object]:
    good = np.isfinite(x) & np.isfinite(y)
    x = np.asarray(x[good], dtype=np.float64)
    y = np.asarray(y[good], dtype=np.float64)
    if x.size < 3 or float(np.ptp(x)) <= EPS:
        return {"n": int(x.size), "slope": None, "intercept": None, "r2": None,
                "residual_std": None}
    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    residual = y - predicted
    denominator = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - float(np.sum(residual ** 2)) / denominator if denominator > EPS else 1.0
    return {"n": int(x.size), "slope": float(slope), "intercept": float(intercept),
            "r2": float(r2), "residual_std": float(np.std(residual))}


def group_phase(values: np.ndarray, weights: np.ndarray, groups: Sequence[int],
                coordinates: np.ndarray, fields: Sequence[str]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for group in groups:
        mask = np.asarray(coordinates == group)
        stats = weighted_phase_stats(values[mask], weights[mask])
        stats.update({"group": int(group), "coordinate": float(group), "n_total": int(np.sum(mask))})
        output.append(stats)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="production csi_roi_manifest.csv，必须是新版复数 tap")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--period-id", type=int, default=None)
    parser.add_argument("--beam-id", type=int, default=None)
    parser.add_argument("--energy-percentile", type=float, default=5.0,
                        help="有效 clutter cell 的 min(|F1|²,|F2|²) 下分位数")
    parser.add_argument("--strong-percentile", type=float, default=99.0,
                        help="排除强点/目标的 min power 上分位数")
    parser.add_argument("--range-block-bins", type=int, default=64)
    parser.add_argument("--prf-hz", type=float, default=1300.0)
    parser.add_argument("--chirp-bandwidth-mhz", type=float, default=50.0)
    parser.add_argument("--chirp-duration-us", type=float, default=130.0)
    args = parser.parse_args()
    if not (0.0 <= args.energy_percentile < args.strong_percentile <= 100.0):
        raise SystemExit("分位数必须满足 0 <= energy < strong <= 100")
    if args.range_block_bins <= 0 or args.prf_hz <= 0.0:
        raise SystemExit("range-block-bins 和 prf-hz 必须为正")

    manifest = args.manifest.resolve()
    row = read_selected_row(manifest, args.period_id, args.beam_id)
    f1 = load_array(row, "channel1_real_path", manifest) + 1j * load_array(
        row, "channel1_imag_path", manifest)
    f2 = load_array(row, "channel2_real_path", manifest) + 1j * load_array(
        row, "channel2_imag_path", manifest)
    fa = load_array(row, "fa_axis_path", manifest).reshape(-1)
    range_axis = load_array(row, "range_axis_path", manifest).reshape(-1)
    if f1.shape != f2.shape or f1.ndim != 2 or f1.shape != (fa.size, range_axis.size):
        raise SystemExit(
            f"F1/F2/axis 形状不一致：F1={f1.shape}, F2={f2.shape}, "
            f"fa={fa.shape}, range={range_axis.shape}")
    rows, cols = f1.shape
    az_st, az_ed = int(row["az_st"]), int(row["az_ed"])
    rg_st, rg_ed = int(row["rg_st"]), int(row["rg_ed"])
    az_st, az_ed = max(0, az_st), min(rows - 1, az_ed)
    rg_st, rg_ed = max(0, rg_st), min(cols - 1, rg_ed)
    if az_st > az_ed or rg_st > rg_ed:
        raise SystemExit("manifest 的 active support 为空")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    p1 = np.abs(f1) ** 2
    p2 = np.abs(f2) ** 2
    pmin = np.minimum(p1, p2)
    active = np.zeros((rows, cols), dtype=bool)
    active[az_st:az_ed + 1, rg_st:rg_ed + 1] = True
    finite = active & np.isfinite(p1) & np.isfinite(p2) & np.isfinite(f1.real)
    active_power = pmin[finite]
    if active_power.size == 0:
        raise SystemExit("active support 没有有限功率")
    energy_floor = float(np.percentile(active_power, args.energy_percentile))
    strong_ceiling = float(np.percentile(active_power, args.strong_percentile))
    valid = finite & (pmin >= max(energy_floor, EPS)) & (pmin <= strong_ceiling)
    weights = pmin

    cross = f1 * np.conj(f2)
    cross_sum = np.sum(cross[:, rg_st:rg_ed + 1], axis=1)
    e1 = np.sum(p1[:, rg_st:rg_ed + 1], axis=1)
    e2 = np.sum(p2[:, rg_st:rg_ed + 1], axis=1)
    row_rho = np.divide(np.abs(cross_sum), np.sqrt(e1 * e2),
                        out=np.zeros(rows, dtype=np.float64), where=(e1 > 0.0) & (e2 > 0.0))
    row_alpha_ls = np.divide(cross_sum, e2, out=np.zeros(rows, dtype=np.complex128), where=e2 > 0.0)
    threshold = 0.5
    gate_pass = np.zeros(rows, dtype=bool)
    gate_pass[az_st:az_ed + 1] = row_rho[az_st:az_ed + 1] >= threshold
    gate_bypass = np.zeros(rows, dtype=bool)
    gate_bypass[az_st:az_ed + 1] = ~gate_pass[az_st:az_ed + 1]
    support_outside = ~active
    support_outside_rows = np.ones(rows, dtype=bool)
    support_outside_rows[az_st:az_ed + 1] = False
    alpha_current = np.exp(1j * (float(row["p38_k_rad_per_hz"]) * fa +
                                 float(row["p38_b_rad"])))
    phase_residual = np.angle(f1 * np.conj(alpha_current[:, None] * f2))
    phase_map = np.full((rows, cols), np.nan, dtype=np.float32)
    phase_map[valid] = phase_residual[valid].astype(np.float32)
    amplitude_residual = 10.0 * np.log10(np.divide(p1, p2, out=np.full_like(p1, np.nan), where=p2 > EPS))
    amplitude_map = np.full((rows, cols), np.nan, dtype=np.float32)
    amplitude_map[valid] = amplitude_residual[valid].astype(np.float32)
    np.save(output_dir / "phase_residual_map.npy", phase_map)
    np.save(output_dir / "amplitude_residual_db_map.npy", amplitude_map)
    np.save(output_dir / "coherence_gate_row_map.npy", np.repeat(row_rho[:, None], cols, axis=1).astype(np.float32))
    np.save(output_dir / "alpha_current_row.npy", alpha_current.astype(np.complex64))

    phase_values = phase_residual[valid]
    phase_weights = weights[valid]
    amplitude_values = amplitude_residual[valid]
    phase_summary = weighted_phase_stats(phase_values, phase_weights)
    amplitude_summary = scalar_stats(amplitude_values)
    amplitude_ratio = np.sqrt(np.divide(p1, p2, out=np.full_like(p1, np.nan), where=p2 > EPS))
    equalized_ratio = np.divide(np.minimum(np.sqrt(p1), np.sqrt(p2)),
                                np.maximum(np.sqrt(p1), np.sqrt(p2)),
                                out=np.full_like(p1, np.nan), where=np.maximum(p1, p2) > EPS)
    equalized_summary = scalar_stats(equalized_ratio[valid])

    # 三类 row 必须独立报告；下面的 all-active summary 仅用于总览，不能替代
    # 这些分组结果。support-outside 没有 active clutter cell，保留 n=0 以免
    # 调用者误把未处理区当成“没有残差”。
    class_masks = {
        "coherent_csi": valid & np.repeat(gate_pass[:, None], cols, axis=1),
        "h0_bypass": valid & np.repeat(gate_bypass[:, None], cols, axis=1),
        "support_outside": valid & np.repeat(support_outside_rows[:, None], cols, axis=1),
    }
    class_summary: dict[str, dict[str, object]] = {}
    class_records: list[dict[str, object]] = []
    for class_name, class_mask in class_masks.items():
        phase_class = weighted_phase_stats(phase_residual[class_mask], weights[class_mask])
        amplitude_class = scalar_stats(amplitude_residual[class_mask])
        equalized_class = scalar_stats(equalized_ratio[class_mask])
        class_summary[class_name] = {
            "phase_residual": phase_class,
            "amplitude_residual_db": amplitude_class,
            "min_amplitude_equalization_ratio": equalized_class,
        }
        class_records.append({"row_class": class_name, "phase_n": phase_class["n"],
                              "phase_circular_mean_rad": phase_class["circular_mean_rad"],
                              "phase_circular_std_rad": phase_class["circular_std_rad"],
                              "phase_rmse_rad": phase_class["rmse_rad"],
                              "phase_p50_abs_rad": phase_class["p50_abs_rad"],
                              "phase_p90_abs_rad": phase_class["p90_abs_rad"],
                              "phase_p95_abs_rad": phase_class["p95_abs_rad"],
                              "amplitude_n": amplitude_class["n"],
                              "amplitude_median_db": amplitude_class["median"],
                              "amplitude_std_db": amplitude_class["std"],
                              "amplitude_cv": amplitude_class["cv"],
                              "equalization_ratio_median": equalized_class["median"]})
    write_csv(output_dir / "residual_by_row_class.csv",
              ["row_class", "phase_n", "phase_circular_mean_rad", "phase_circular_std_rad",
               "phase_rmse_rad", "phase_p50_abs_rad", "phase_p90_abs_rad", "phase_p95_abs_rad",
               "amplitude_n", "amplitude_median_db", "amplitude_std_db", "amplitude_cv",
               "equalization_ratio_median"], class_records)

    row_records: list[dict[str, object]] = []
    for index in range(rows):
        kind = "support_outside" if index < az_st or index > az_ed else (
            "coherent_csi" if gate_pass[index] else "h0_bypass")
        row_records.append({
            "row": index, "doppler_hz": float(fa[index]), "class": kind,
            "coherence_rho": float(row_rho[index]), "gate_threshold": threshold,
            "gate_pass": int(gate_pass[index]), "active_support": int(az_st <= index <= az_ed),
            "alpha_ls_real": float(row_alpha_ls[index].real),
            "alpha_ls_imag": float(row_alpha_ls[index].imag),
            "alpha_current_phase_rad": float(np.angle(alpha_current[index])),
        })
    write_csv(output_dir / "coherence_gate_rows.csv",
              ["row", "doppler_hz", "class", "coherence_rho", "gate_threshold",
               "gate_pass", "active_support", "alpha_ls_real", "alpha_ls_imag",
               "alpha_current_phase_rad"], row_records)

    phase_doppler_records: list[dict[str, object]] = []
    for index in range(az_st, az_ed + 1):
        cell_mask = valid[index]
        stats = weighted_phase_stats(phase_residual[index, cell_mask], weights[index, cell_mask])
        phase_doppler_records.append({"row": index, "doppler_hz": float(fa[index]),
                                      "gate_class": "coherent_csi" if gate_pass[index] else "h0_bypass",
                                      **stats})
    write_csv(output_dir / "phase_residual_vs_doppler.csv",
              ["row", "doppler_hz", "gate_class", "n", "circular_mean_rad",
               "circular_std_rad", "rmse_rad", "p50_abs_rad", "p90_abs_rad",
               "p95_abs_rad", "resultant_length"], phase_doppler_records)

    phase_range_records: list[dict[str, object]] = []
    range_freq = (range_axis - float(np.mean(range_axis))) * 2.0 * (
        args.chirp_bandwidth_mhz * 1.0e6) / (args.chirp_duration_us * 1.0e-6) / 299792458.0
    range_groups = list(range(0, cols, args.range_block_bins))
    for group_start in range_groups:
        group_end = min(cols, group_start + args.range_block_bins)
        mask = valid[:, group_start:group_end]
        values = phase_residual[:, group_start:group_end][mask]
        group_weights = weights[:, group_start:group_end][mask]
        stats = weighted_phase_stats(values, group_weights)
        phase_range_records.append({
            "range_block_start": group_start, "range_block_end_exclusive": group_end,
            "range_m": float(np.mean(range_axis[group_start:group_end])),
            "range_frequency_relative_hz": float(np.mean(range_freq[group_start:group_end])),
            **stats,
        })
    write_csv(output_dir / "phase_residual_vs_range.csv",
              ["range_block_start", "range_block_end_exclusive", "range_m",
               "range_frequency_relative_hz", "n", "circular_mean_rad", "circular_std_rad",
               "rmse_rad", "p50_abs_rad", "p90_abs_rad", "p95_abs_rad", "resultant_length"],
              phase_range_records)

    amplitude_doppler_records: list[dict[str, object]] = []
    for index in range(az_st, az_ed + 1):
        cell_mask = valid[index]
        amplitude_doppler_records.append({"row": index, "doppler_hz": float(fa[index]),
                                          "gate_class": "coherent_csi" if gate_pass[index] else "h0_bypass",
                                          **scalar_stats(amplitude_residual[index, cell_mask])})
    write_csv(output_dir / "amplitude_residual_vs_doppler.csv",
              ["row", "doppler_hz", "gate_class", "n", "median", "mean", "std", "cv", "p90", "p95"],
              amplitude_doppler_records)

    amplitude_range_records: list[dict[str, object]] = []
    for group_start in range_groups:
        group_end = min(cols, group_start + args.range_block_bins)
        amplitude_range_records.append({
            "range_block_start": group_start, "range_block_end_exclusive": group_end,
            "range_m": float(np.mean(range_axis[group_start:group_end])),
            "range_frequency_relative_hz": float(np.mean(range_freq[group_start:group_end])),
            **scalar_stats(amplitude_residual[:, group_start:group_end][valid[:, group_start:group_end]])})
    write_csv(output_dir / "amplitude_residual_vs_range.csv",
              ["range_block_start", "range_block_end_exclusive", "range_m",
               "range_frequency_relative_hz", "n", "median", "mean", "std", "cv", "p90", "p95"],
              amplitude_range_records)

    block_count = len(range_groups)
    block_coherence = np.full((rows, block_count), np.nan, dtype=np.float32)
    for index in range(az_st, az_ed + 1):
        for block_index, group_start in enumerate(range_groups):
            group_end = min(cols, group_start + args.range_block_bins)
            cell_mask = valid[index, group_start:group_end]
            if not np.any(cell_mask):
                continue
            a = f1[index, group_start:group_end][cell_mask]
            b = f2[index, group_start:group_end][cell_mask]
            denominator = math.sqrt(float(np.sum(np.abs(a) ** 2)) * float(np.sum(np.abs(b) ** 2)))
            if denominator > EPS:
                block_coherence[index, block_index] = float(np.clip(abs(np.sum(a * np.conj(b))) / denominator, 0.0, 1.0))
    np.save(output_dir / "coherence_block_map.npy", block_coherence)
    coherence_doppler_records = []
    for index in range(az_st, az_ed + 1):
        values = block_coherence[index]
        values = values[np.isfinite(values)]
        coherence_doppler_records.append({"row": index, "doppler_hz": float(fa[index]),
                                          "gate_class": "coherent_csi" if gate_pass[index] else "h0_bypass",
                                          **scalar_stats(values)})
    write_csv(output_dir / "coherence_vs_doppler.csv",
              ["row", "doppler_hz", "gate_class", "n", "median", "mean", "std", "cv", "p90", "p95"],
              coherence_doppler_records)
    coherence_range_records = []
    for block_index, group_start in enumerate(range_groups):
        values = block_coherence[az_st:az_ed + 1, block_index]
        values = values[np.isfinite(values)]
        group_end = min(cols, group_start + args.range_block_bins)
        coherence_range_records.append({"range_block_start": group_start,
                                        "range_block_end_exclusive": group_end,
                                        "range_m": float(np.mean(range_axis[group_start:group_end])),
                                        **scalar_stats(values)})
    write_csv(output_dir / "coherence_vs_range.csv",
              ["range_block_start", "range_block_end_exclusive", "range_m", "n", "median",
               "mean", "std", "cv", "p90", "p95"], coherence_range_records)

    # 当前 tap 没有暴露 FFT 前的原始脉冲矩阵；这是把 production F1/F2 经
    # 当前 alpha 修正后 IFFT 回 slow-time 的可复现诊断，不冒充独立 raw-LFM tap。
    f1_slow = np.fft.ifft(np.fft.ifftshift(f1, axes=0), axis=0)
    f2_slow = np.fft.ifft(np.fft.ifftshift(alpha_current[:, None] * f2, axes=0), axis=0)
    slow_pmin = np.minimum(np.abs(f1_slow) ** 2, np.abs(f2_slow) ** 2)
    slow_active = np.zeros((rows, cols), dtype=bool)
    slow_active[:, rg_st:rg_ed + 1] = True
    slow_good_power = slow_pmin[slow_active & np.isfinite(slow_pmin)]
    slow_floor = float(np.percentile(slow_good_power, args.energy_percentile)) if slow_good_power.size else EPS
    slow_ceiling = float(np.percentile(slow_good_power, args.strong_percentile)) if slow_good_power.size else float("inf")
    slow_phase = np.angle(f1_slow * np.conj(f2_slow))
    slow_valid = slow_active & np.isfinite(slow_phase) & (slow_pmin >= max(slow_floor, EPS)) & (slow_pmin <= slow_ceiling)
    pulse_records = []
    for pulse in range(rows):
        mask = slow_valid[pulse]
        stats = weighted_phase_stats(slow_phase[pulse, mask], slow_pmin[pulse, mask])
        pulse_records.append({"pulse": pulse, "time_s": float(pulse / args.prf_hz), **stats})
    write_csv(output_dir / "pulse_phase_residual.csv",
              ["pulse", "time_s", "n", "circular_mean_rad", "circular_std_rad", "rmse_rad",
               "p50_abs_rad", "p90_abs_rad", "p95_abs_rad", "resultant_length"], pulse_records)

    pulse_phase = np.array([record["circular_mean_rad"] if record["circular_mean_rad"] is not None else np.nan
                            for record in pulse_records], dtype=np.float64)
    pulse_fit = fit_line(np.arange(rows, dtype=np.float64), np.unwrap(pulse_phase))
    doppler_mean = np.array([record["circular_mean_rad"] if record["circular_mean_rad"] is not None else np.nan
                             for record in phase_doppler_records], dtype=np.float64)
    doppler_fit = fit_line(fa[az_st:az_ed + 1], np.unwrap(doppler_mean))
    range_mean = np.array([record["circular_mean_rad"] if record["circular_mean_rad"] is not None else np.nan
                           for record in phase_range_records], dtype=np.float64)
    range_frequency_fit = fit_line(
        np.array([record["range_frequency_relative_hz"] for record in phase_range_records], dtype=np.float64),
        np.unwrap(range_mean))
    delay_fit = dict(range_frequency_fit)
    if delay_fit["slope"] is not None:
        delay_fit["delta_tau_ns"] = float(delay_fit["slope"] / (2.0 * math.pi) * 1.0e9)
    else:
        delay_fit["delta_tau_ns"] = None

    active_rows = az_ed - az_st + 1
    pass_rows = int(np.sum(gate_pass[az_st:az_ed + 1]))
    bypass_rows = active_rows - pass_rows
    signature = "random_or_mixed"
    doppler_span = abs(float(doppler_fit["slope"]) * float(np.ptp(fa[az_st:az_ed + 1]))) if doppler_fit["slope"] is not None else 0.0
    range_span = abs(float(range_frequency_fit["slope"]) * float(np.ptp(range_freq))) if range_frequency_fit["slope"] is not None else 0.0
    if doppler_fit["r2"] is not None and range_frequency_fit["r2"] is not None:
        if float(range_frequency_fit["r2"]) >= 0.7 and range_span >= max(0.05, doppler_span):
            signature = "range_trend_or_wideband_delay"
        elif float(doppler_fit["r2"]) >= 0.7 and doppler_span >= 0.05:
            signature = "Doppler_trend"
        elif float(doppler_fit["r2"]) < 0.3 and float(range_frequency_fit["r2"]) < 0.3:
            signature = "constant_offset_or_random_residual"
        else:
            signature = "nonlinear_or_mixed_trend"

    summary = {
        "schema_version": 1,
        "input_manifest": str(manifest),
        "input_identity": {
            "case_id": row.get("case_id"), "run_id": row.get("run_id"),
            "result_id": row.get("result_id"), "period_id": int(row["period_id"]),
            "beam_id": int(row["beam_id"]), "shape": [rows, cols],
        },
        "definitions": {
            "phase_residual": "angle(F1 * conj(exp(+j*(p38_k*fa+p38_b)) * F2))",
            "row_coherence": "abs(sum(F1*conj(F2))) / sqrt(sum(|F1|^2)*sum(|F2|^2)) over production range support",
            "coherent_csi_rows": "rho >= 0.5",
            "h0_bypass_rows": "rho < 0.5",
            "support_outside_rows": f"rows outside [{az_st},{az_ed}]",
            "valid_clutter_cells": "active support, finite, min channel power between configured 5th and 99th percentiles",
            "slow_time_phase": "IFFT-derived diagnostic from production F1 and P38-corrected F2; raw pre-Doppler pulses are not exported by this tap",
        },
        "thresholds": {
            "coherence_gate": threshold, "energy_percentile": args.energy_percentile,
            "strong_percentile": args.strong_percentile, "energy_floor_min_power": energy_floor,
            "strong_ceiling_min_power": strong_ceiling, "slow_time_energy_floor": slow_floor,
            "slow_time_strong_ceiling": slow_ceiling,
        },
        "row_classification": {
            "active_rows": active_rows, "coherent_csi_rows": pass_rows,
            "h0_bypass_rows": bypass_rows, "support_outside_rows": rows - active_rows,
            "coherence_gate_pass_fraction": pass_rows / active_rows if active_rows else None,
            "coherence_gate_bypass_fraction": bypass_rows / active_rows if active_rows else None,
            "rho_min": float(np.min(row_rho[az_st:az_ed + 1])),
            "rho_median": float(np.median(row_rho[az_st:az_ed + 1])),
            "rho_max": float(np.max(row_rho[az_st:az_ed + 1])),
        },
        "phase_residual": {"summary": phase_summary, "signature": signature,
                           "by_row_class": class_summary,
                           "vs_doppler_linear_fit": doppler_fit,
                           "vs_range_frequency_linear_fit": delay_fit},
        "amplitude_residual_db": amplitude_summary,
        "amplitude_ratio_linear": scalar_stats(amplitude_ratio[valid]),
        "min_amplitude_equalization_ratio": equalized_summary,
        "slow_time_phase": {"fit_vs_pulse": pulse_fit},
        "artifacts": {
            "phase_residual_map": str(output_dir / "phase_residual_map.npy"),
            "amplitude_residual_db_map": str(output_dir / "amplitude_residual_db_map.npy"),
            "coherence_gate_row_map": str(output_dir / "coherence_gate_row_map.npy"),
            "coherence_block_map": str(output_dir / "coherence_block_map.npy"),
            "coherence_gate_rows": str(output_dir / "coherence_gate_rows.csv"),
            "residual_by_row_class": str(output_dir / "residual_by_row_class.csv"),
            "phase_residual_vs_doppler": str(output_dir / "phase_residual_vs_doppler.csv"),
            "phase_residual_vs_range": str(output_dir / "phase_residual_vs_range.csv"),
            "amplitude_residual_vs_doppler": str(output_dir / "amplitude_residual_vs_doppler.csv"),
            "amplitude_residual_vs_range": str(output_dir / "amplitude_residual_vs_range.csv"),
            "coherence_vs_doppler": str(output_dir / "coherence_vs_doppler.csv"),
            "coherence_vs_range": str(output_dir / "coherence_vs_range.csv"),
            "pulse_phase_residual": str(output_dir / "pulse_phase_residual.csv"),
        },
    }
    (output_dir / "residual_texture_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "phase_signature": signature,
                      "coherence_gate_pass_fraction": summary["row_classification"]["coherence_gate_pass_fraction"],
                      "coherence_gate_bypass_fraction": summary["row_classification"]["coherence_gate_bypass_fraction"],
                      "phase_rmse_rad": phase_summary["rmse_rad"],
                      "wideband_delay_ns": delay_fit["delta_tau_ns"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
