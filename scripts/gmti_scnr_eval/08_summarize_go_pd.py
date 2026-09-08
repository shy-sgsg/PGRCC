#!/usr/bin/env python3
"""按 0°/30°/45°汇总正式 GO-CFAR 目标级和航迹级 Pd，并给出置信区间。"""

from __future__ import annotations

import argparse
import math
import os
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti_scnr_eval_matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from scnr_eval_lib import ensure_dir, read_csv, wilson_interval, write_csv


def number(row: dict[str, str], key: str, default: float = float("nan")) -> float:
    try:
        value = row.get(key, "")
        return float(value) if value not in {"", None} else default
    except ValueError:
        return default


def integer(row: dict[str, str], key: str, default: int = 0) -> int:
    value = number(row, key, float(default))
    return int(round(value)) if math.isfinite(value) else default


def bootstrap_ci(values_by_seed: dict[str, tuple[int, int]], seed: int,
                 samples: int = 2000) -> tuple[float, float]:
    """按随机 seed 重采样，避免把同一 seed 的多个目标当独立场景。"""
    keys = sorted(values_by_seed)
    if not keys:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    draws = np.empty(samples, dtype=np.float64)
    for i in range(samples):
        selected = rng.choice(keys, size=len(keys), replace=True)
        successes = sum(values_by_seed[key][0] for key in selected)
        total = sum(values_by_seed[key][1] for key in selected)
        draws[i] = successes / total if total else float("nan")
    return float(np.nanquantile(draws, 0.025)), float(np.nanquantile(draws, 0.975))


def bootstrap_rmse(errors_by_seed: dict[str, list[float]], seed: int,
                   samples: int = 2000) -> tuple[float, float]:
    keys = sorted(key for key, values in errors_by_seed.items() if values)
    if not keys:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    draws = np.empty(samples, dtype=np.float64)
    for i in range(samples):
        selected = rng.choice(keys, size=len(keys), replace=True)
        errors = [error for key in selected for error in errors_by_seed[key]]
        draws[i] = math.sqrt(float(np.mean(np.square(errors)))) if errors else float("nan")
    return float(np.nanquantile(draws, 0.025)), float(np.nanquantile(draws, 0.975))


def three_frame_formula(p1: float, p2: float, p3: float) -> float:
    """独立但允许各周期 Pd 不同的“两次命中”概率。"""
    return p1 * p2 + p1 * p3 + p2 * p3 - 2.0 * p1 * p2 * p3


def bootstrap_three_frame_formula(
        frame_counts_by_seed: dict[str, tuple[tuple[int, int], tuple[int, int], tuple[int, int]]],
        seed: int, samples: int = 2000) -> tuple[float, float]:
    """按 seed 重采样后重新估计 p1/p2/p3，保留同 seed 内的相关性。"""
    keys = sorted(frame_counts_by_seed)
    if not keys:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    draws = np.empty(samples, dtype=np.float64)
    for i in range(samples):
        selected = rng.choice(keys, size=len(keys), replace=True)
        frame_success = [0, 0, 0]
        frame_total = [0, 0, 0]
        for key in selected:
            for frame, (success, total) in enumerate(frame_counts_by_seed[key]):
                frame_success[frame] += success
                frame_total[frame] += total
        probabilities = [
            frame_success[frame] / frame_total[frame] if frame_total[frame] else float("nan")
            for frame in range(3)
        ]
        draws[i] = three_frame_formula(*probabilities) if all(
            math.isfinite(value) for value in probabilities) else float("nan")
    return float(np.nanquantile(draws, 0.025)), float(np.nanquantile(draws, 0.975))


def target_scnr_medians(path: Path) -> dict[tuple[str, str], float]:
    """以每条目标三帧中实际测得的 GO CUT SCNR 中位数作横轴标定。

    未输出的帧没有生产 CUT 导出，因而此量严格标记为“检测到帧条件中位数”，
    不把它伪装成未检测帧的可观测 SCNR。
    """
    diagnostics = path / "target_diagnostics_all_periods.csv"
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    if not diagnostics.is_file():
        return {}
    for row in read_csv(diagnostics):
        value = number(row, "scnr_det_out_db")
        if math.isfinite(value) and integer(row, "final_output") == 1:
            grouped[(row.get("seed", ""), row.get("target_id", ""))].append(value)
    return {key: float(np.median(values)) for key, values in grouped.items() if values}


def paired_output_scnr_map(paths: list[Path], minimum_samples: int) -> dict[tuple[float, float], float]:
    """读取独立 S-only/C+N 配对标定，不从“检测到的帧”反推横轴。"""
    if not paths:
        return {}
    values: dict[tuple[float, float], float] = {}
    insufficient: list[str] = []
    for path in paths:
        if not path.is_file():
            raise SystemExit(f"找不到 GO 输出 SCNR 配对标定：{path}")
        for row in read_csv(path):
            angle = number(row, "truth_angle_deg")
            injection = number(row, "configured_target_snr_db")
            output = number(row, "output_scnr_det_out_db_median")
            sample_count = integer(row, "sample_count", 0)
            if math.isfinite(angle) and math.isfinite(injection) and math.isfinite(output):
                key = (round(angle, 8), round(injection, 8))
                if sample_count < minimum_samples:
                    insufficient.append(f"{angle:g}°/{injection:g}dB={sample_count}")
                    continue
                if key in values:
                    raise SystemExit(f"配对标定对同一 angle/SCNR 重复：{angle:g}°, {injection:g} dB")
                values[key] = output
    if insufficient:
        raise SystemExit(
            f"配对输出 SCNR 标定样本不足（要求每个 angle/SCNR ≥{minimum_samples}）：" +
            ", ".join(insufficient))
    if not values:
        raise SystemExit("配对标定没有有效 truth_angle_deg/output_scnr_det_out_db_median")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, required=True,
                        help="包含各 seed metrics/track_sequence_audit.csv 的目录")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260827)
    parser.add_argument("--paired-output-scnr-summary", type=Path, action="append", default=[],
                        help="可重复给出 04_calibrate_go_output_scnr.py 的 go_paired_output_scnr_summary.csv")
    parser.add_argument("--require-paired-output-scnr", action="store_true",
                        help="拒绝任何未覆盖 angle/SCNR 的正式 Pd 分组")
    parser.add_argument("--minimum-paired-calibration-samples", type=int, default=0,
                        help="每个 angle/SCNR 允许用于横轴的最小配对标定样本数")
    args = parser.parse_args()
    root = args.batch_root.resolve()
    out = ensure_dir(args.output_dir.resolve())
    if args.minimum_paired_calibration_samples < 0:
        raise SystemExit("minimum-paired-calibration-samples 不能为负")
    paired_scnr = paired_output_scnr_map(
        [path.resolve() for path in args.paired_output_scnr_summary],
        args.minimum_paired_calibration_samples)
    sequence_files = sorted(root.rglob("track_sequence_audit.csv"))
    if not sequence_files:
        raise SystemExit(f"未在 {root} 找到 track_sequence_audit.csv")

    sequence_rows: list[dict] = []
    angle_errors: dict[tuple[float, float], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    phase_errors: dict[tuple[float, float], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    phase_scnr_values: dict[tuple[float, float], list[float]] = defaultdict(list)
    for sequence_file in sequence_files:
        metrics_dir = sequence_file.parent
        median_scnr = target_scnr_medians(metrics_dir)
        for row in read_csv(sequence_file):
            seed_id = row.get("seed", "")
            target_id = row.get("target_id", "")
            angle = number(row, "truth_angle_deg")
            injection = number(row, "configured_target_snr_db")
            if not (math.isfinite(angle) and math.isfinite(injection) and target_id):
                continue
            row = dict(row)
            row["source_metrics_dir"] = str(metrics_dir)
            value = median_scnr.get((seed_id, target_id))
            row["detected_frame_conditional_output_scnr_median_db"] = "" if value is None else value
            sequence_rows.append(row)
        diagnostic_file = metrics_dir / "target_diagnostics_all_periods.csv"
        if diagnostic_file.is_file():
            for row in read_csv(diagnostic_file):
                if row.get("cfar_type") != "GO":
                    continue
                angle = number(row, "truth_angle_deg")
                injection = number(row, "configured_target_snr_db")
                phase_scnr = number(row, "scnr_phase_eff_db_calibrated")
                if math.isfinite(angle) and math.isfinite(injection) and math.isfinite(phase_scnr):
                    phase_scnr_values[(angle, injection)].append(phase_scnr)
                if integer(row, "final_output") != 1:
                    continue
                error = number(row, "angle_error_deg")
                if math.isfinite(angle) and math.isfinite(injection) and math.isfinite(error):
                    angle_errors[(angle, injection)][row.get("seed", "")].append(error)
                phase_error = number(row, "phase_error_rad")
                if math.isfinite(angle) and math.isfinite(injection) and math.isfinite(phase_error):
                    phase_errors[(angle, injection)][row.get("seed", "")].append(phase_error)
    if not sequence_rows:
        raise SystemExit("没有可用于 GO-Pd 汇总的三帧目标记录")
    write_csv(out / "track_sequence_audit_all.csv", sequence_rows)

    grouped: dict[tuple[float, float], list[dict]] = defaultdict(list)
    for row in sequence_rows:
        grouped[(number(row, "truth_angle_deg"), number(row, "configured_target_snr_db"))].append(row)
    if args.require_paired_output_scnr:
        missing = [
            f"{angle:g}°/{injection:g}dB" for angle, injection in sorted(grouped)
            if (round(angle, 8), round(injection, 8)) not in paired_scnr
        ]
        if missing:
            raise SystemExit("缺少正式 Pd 横轴所需的配对输出 SCNR 标定：" + ", ".join(missing))
    pd_rows: list[dict] = []
    for index, ((angle, injection), rows) in enumerate(sorted(grouped.items())):
        target_success = sum(
            integer(row, "frame1_target_output") + integer(row, "frame2_target_output") +
            integer(row, "frame3_target_output") for row in rows)
        target_total = 3 * len(rows)
        target_pd = target_success / target_total
        target_low, target_high = wilson_interval(target_success, target_total)
        track_success = sum(integer(row, "trackmanager_confirmed_matched_protocol_output") for row in rows)
        track_pd = track_success / len(rows)
        track_low, track_high = wilson_interval(track_success, len(rows))
        two_of_three = sum(integer(row, "two_of_three_target_hits") for row in rows)
        two_of_three_pd = two_of_three / len(rows)
        seed_target: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
        seed_track: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
        seed_frame_counts: dict[str, list[list[int]]] = defaultdict(
            lambda: [[0, 0], [0, 0], [0, 0]])
        frame_success = [0, 0, 0]
        for row in rows:
            seed = row.get("seed", "")
            frame_hits = [integer(row, f"frame{frame}_target_output") for frame in range(1, 4)]
            for frame, hit in enumerate(frame_hits):
                frame_success[frame] += hit
                seed_frame_counts[seed][frame][0] += hit
                seed_frame_counts[seed][frame][1] += 1
            old_success, old_total = seed_target[seed]
            seed_target[seed] = (old_success + sum(frame_hits),
                                 old_total + 3)
            old_success, old_total = seed_track[seed]
            seed_track[seed] = (old_success + integer(row, "trackmanager_confirmed_matched_protocol_output"),
                                old_total + 1)
        target_boot_low, target_boot_high = bootstrap_ci(
            seed_target, args.seed + 10 * index, args.bootstrap_samples)
        track_boot_low, track_boot_high = bootstrap_ci(
            seed_track, args.seed + 10 * index + 1, args.bootstrap_samples)
        frame_pds = [success / len(rows) for success in frame_success]
        frame_counts_for_bootstrap = {
            seed: tuple((count[0], count[1]) for count in counts)
            for seed, counts in seed_frame_counts.items()
        }
        formula_track = three_frame_formula(*frame_pds)
        formula_boot_low, formula_boot_high = bootstrap_three_frame_formula(
            frame_counts_for_bootstrap, args.seed + 10 * index + 2, args.bootstrap_samples)
        scnr_values = [number(row, "detected_frame_conditional_output_scnr_median_db") for row in rows]
        scnr_values = [value for value in scnr_values if math.isfinite(value)]
        pd_rows.append({
            "cfar_type": "GO", "truth_angle_deg": angle,
            "configured_target_snr_db": injection,
            "target_sequences": len(rows), "target_frame_trials": target_total,
            "target_pd": target_pd, "target_pd_wilson95_low": target_low,
            "target_pd_wilson95_high": target_high,
            "target_pd_seed_bootstrap95_low": target_boot_low,
            "target_pd_seed_bootstrap95_high": target_boot_high,
            "two_of_three_detection_event_pd": two_of_three_pd,
            "trackmanager_protocol_track_pd": track_pd,
            "track_pd_wilson95_low": track_low, "track_pd_wilson95_high": track_high,
            "track_pd_seed_bootstrap95_low": track_boot_low,
            "track_pd_seed_bootstrap95_high": track_boot_high,
            "frame1_target_pd": frame_pds[0], "frame2_target_pd": frame_pds[1],
            "frame3_target_pd": frame_pds[2],
            "three_frame_independence_formula": formula_track,
            "three_frame_formula_seed_bootstrap95_low": formula_boot_low,
            "three_frame_formula_seed_bootstrap95_high": formula_boot_high,
            "three_frame_formula_definition": "p1*p2+p1*p3+p2*p3-2*p1*p2*p3；p1/p2/p3 按各周期独立估计",
            "trackmanager_observed_minus_three_frame_formula": track_pd - formula_track,
            "paired_calibrated_output_scnr_db": paired_scnr.get(
                (round(angle, 8), round(injection, 8)), float("nan")),
            "paired_calibrated_output_scnr_definition": (
                "04 配对 S-only/C+N 固定单元 output SCNR" if paired_scnr else
                "未提供配对标定；不得将下列检测条件中位数作为未检测帧 SCNR"),
            "output_scnr_definition": "检测到帧的 production GO CUT (cut-noise)/noise 中位数；未检测帧不可直接观测",
            "detected_frame_conditional_output_scnr_median_db": float(np.median(scnr_values)) if scnr_values else float("nan"),
            "detected_frame_conditional_output_scnr_count": len(scnr_values),
        })
    write_csv(out / "go_target_and_track_pd_summary.csv", pd_rows)

    angle_rows: list[dict] = []
    for index, ((angle, injection), by_seed) in enumerate(sorted(angle_errors.items())):
        errors = [value for values in by_seed.values() for value in values]
        if not errors:
            continue
        array = np.asarray(errors, dtype=np.float64)
        low, high = bootstrap_rmse(by_seed, args.seed + 1000 + index, args.bootstrap_samples)
        phase_by_seed = phase_errors.get((angle, injection), {})
        phase_values = [value for values in phase_by_seed.values() for value in values]
        phase_array = np.asarray(phase_values, dtype=np.float64)
        phase_low, phase_high = bootstrap_rmse(phase_by_seed, args.seed + 2000 + index, args.bootstrap_samples)
        calibrated_phase_values = phase_scnr_values.get((angle, injection), [])
        angle_rows.append({
            "cfar_type": "GO", "truth_angle_deg": angle,
            "configured_target_snr_db": injection, "matched_detection_count": len(array),
            "angle_bias_deg": float(np.mean(array)), "angle_std_deg": float(np.std(array, ddof=1)) if len(array) > 1 else 0.0,
            "angle_rmse_deg": float(math.sqrt(float(np.mean(array * array)))),
            "angle_rmse_seed_bootstrap95_low": low, "angle_rmse_seed_bootstrap95_high": high,
            "angle_abs_error_p95_deg": float(np.quantile(np.abs(array), 0.95)),
            "phase_error_sample_count": len(phase_array),
            "phase_bias_rad": float(np.mean(phase_array)) if len(phase_array) else float("nan"),
            "phase_std_rad": float(np.std(phase_array, ddof=1)) if len(phase_array) > 1 else (0.0 if len(phase_array) == 1 else float("nan")),
            "phase_rmse_rad": float(math.sqrt(float(np.mean(phase_array * phase_array)))) if len(phase_array) else float("nan"),
            "phase_rmse_seed_bootstrap95_low": phase_low,
            "phase_rmse_seed_bootstrap95_high": phase_high,
            "scnr_phase_eff_calibrated_db": float(np.median(calibrated_phase_values)) if calibrated_phase_values else float("nan"),
            "scnr_phase_eff_calibrated_sample_count": len(calibrated_phase_values),
        })
    write_csv(out / "go_angle_error_summary.csv", angle_rows)

    for key, value, ylabel, title, filename in (
        ("target_pd", "target_pd", "目标级 Pd", "GO-CFAR 目标级检测概率", "F10_go_target_pd.png"),
        ("trackmanager_protocol_track_pd", "trackmanager_protocol_track_pd", "航迹级 Pd", "GO-CFAR 三帧航迹检测概率", "F11_go_track_pd.png"),
    ):
        fig, axis = plt.subplots(figsize=(7.4, 4.6))
        for angle in sorted({row["truth_angle_deg"] for row in pd_rows}):
            rows = [row for row in pd_rows if row["truth_angle_deg"] == angle]
            rows.sort(key=lambda row: row["configured_target_snr_db"])
            x = [row["paired_calibrated_output_scnr_db"] if math.isfinite(row["paired_calibrated_output_scnr_db"])
                 else row["configured_target_snr_db"] for row in rows]
            axis.plot(x, [row[value] for row in rows],
                      marker="o", label=f"{angle:g}°" if key == "target_pd" else f"{angle:g}° TrackManager")
            if key == "trackmanager_protocol_track_pd":
                axis.plot(x,
                          [row["three_frame_independence_formula"] for row in rows],
                          marker="x", linestyle="--", alpha=0.85,
                          label=f"{angle:g}° 独立两命中公式")
        axis.axhline(0.9, color="tab:red", linestyle="--", label="Pd=0.90")
        axis.set(xlabel="配对标定输出 SCNR (dB；未标定点回退为 Stage2 注入值)", ylabel=ylabel,
                 ylim=(-0.02, 1.02), title=title)
        axis.grid(True, alpha=0.3); axis.legend(); fig.tight_layout(); fig.savefig(out / filename, dpi=180); plt.close(fig)
    if angle_rows:
        fig, axis = plt.subplots(figsize=(7.4, 4.6))
        for angle in sorted({row["truth_angle_deg"] for row in angle_rows}):
            rows = [row for row in angle_rows if row["truth_angle_deg"] == angle]
            rows.sort(key=lambda row: row["configured_target_snr_db"])
            axis.plot([row["configured_target_snr_db"] for row in rows], [row["angle_rmse_deg"] for row in rows],
                      marker="o", label=f"{angle:g}°")
        axis.axhline(0.2, color="tab:red", linestyle="--", label="RMSE=0.2°")
        axis.set(xlabel="Stage2 注入 SCNR (dB)", ylabel="方位 RMSE (°)", title="GO-CFAR 匹配目标测角精度")
        axis.grid(True, alpha=0.3); axis.legend(); fig.tight_layout(); fig.savefig(out / "F09_go_angle_rmse.png", dpi=180); plt.close(fig)
    print(f"[PASS] 已汇总 {len(sequence_rows)} 条目标序列、{len(pd_rows)} 个 GO-Pd 分组：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
