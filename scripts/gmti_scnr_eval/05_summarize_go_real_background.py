#!/usr/bin/env python3
"""汇总正式生产 GO-CFAR 图中的 truth 分辨单元检测结果。

本脚本只使用 ``05_extract_go_diagnostics.py`` 已从生产 GO 功率图/命中图提取的
``unit_*`` 字段。主 Pd 是物理 truth 分辨单元内（range/Doppler 各 ±2 bin）的
生产 GO hit，严格的单一 FFT CUT 命中另行给出；两者都不以最终 cluster、
target_select 或 truth match 代替单元级检测。
"""

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


def number(row: dict[str, str], field: str, default: float = float("nan")) -> float:
    try:
        value = row.get(field, "")
        return float(value) if value not in {"", None} else default
    except (TypeError, ValueError):
        return default


def integer(row: dict[str, str], field: str, default: int = 0) -> int:
    value = number(row, field, float(default))
    return int(round(value)) if math.isfinite(value) else default


def finite(values: list[float]) -> list[float]:
    return [value for value in values if math.isfinite(value)]


def median(rows: list[dict[str, str]], field: str) -> float:
    values = finite([number(row, field) for row in rows])
    return float(np.median(values)) if values else float("nan")


def mean(rows: list[dict[str, str]], field: str) -> float:
    values = finite([number(row, field) for row in rows])
    return float(np.mean(values)) if values else float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, required=True,
                        help="包含正式 coarse/refine seed metrics 的父目录")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minimum-trials", type=int, default=200,
                        help="每个正式 angle/SCNR 指定单元所需最小试验数")
    args = parser.parse_args()
    if args.minimum_trials < 1:
        raise SystemExit("minimum-trials 必须为正")
    root, out = args.batch_root.resolve(), ensure_dir(args.output_dir.resolve())
    diagnostic_files = sorted(root.rglob("target_diagnostics_all_periods.csv"))
    if not diagnostic_files:
        raise SystemExit(f"未在 {root} 找到正式 target_diagnostics_all_periods.csv")
    grouped: dict[tuple[float, float], list[dict[str, str]]] = defaultdict(list)
    input_files: list[str] = []
    for path in diagnostic_files:
        input_files.append(str(path))
        for row in read_csv(path):
            if row.get("cfar_type") != "GO" or integer(row, "unit_cell_window_valid") != 1:
                continue
            hit = number(row, "unit_go_cfar_hit")
            angle, injection = number(row, "truth_angle_deg"), number(row, "configured_target_snr_db")
            if not (math.isfinite(hit) and math.isfinite(angle) and math.isfinite(injection)):
                continue
            grouped[(angle, injection)].append(row)
    if not grouped:
        raise SystemExit("正式诊断未包含指定 truth 单元的 GO 命中结果；请确认 diagnostic-beams 覆盖目标波位")
    insufficient = [f"{angle:g}°/{injection:g}dB={len(rows)}"
                    for (angle, injection), rows in sorted(grouped.items())
                    if len(rows) < args.minimum_trials]
    if insufficient:
        raise SystemExit("实际统计背景 GO 单元样本不足：" + ", ".join(insufficient))
    summaries: list[dict[str, object]] = []
    for (angle, injection), rows in sorted(grouped.items()):
        hits = sum(integer(row, "unit_go_cfar_hit") for row in rows)
        exact_hits = sum(integer(row, "unit_go_cfar_exact_cut_hit") for row in rows)
        total = len(rows)
        low, high = wilson_interval(hits, total)
        left = mean(rows, "unit_cfar_left_mean")
        right = mean(rows, "unit_cfar_right_mean")
        top = mean(rows, "unit_cfar_top_mean")
        bottom = mean(rows, "unit_cfar_bottom_mean")
        directional = finite([left, right, top, bottom])
        ratio_db = (10.0 * math.log10(max(directional) / min(directional))
                    if directional and min(directional) > 0.0 else float("nan"))
        summaries.append({
            "cfar_type": "GO", "truth_angle_deg": angle,
            "configured_target_snr_db": injection,
            "paired_calibrated_output_scnr_db": median(rows, "scnr_det_out_db_calibrated"),
            "trial_count": total, "unit_go_cfar_hits": hits,
            "unit_go_cfar_pd": hits / total,
            "unit_go_cfar_pd_wilson95_low": low,
            "unit_go_cfar_pd_wilson95_high": high,
            "unit_go_cfar_exact_cut_hits": exact_hits,
            "unit_go_cfar_exact_cut_pd": exact_hits / total,
            "unit_cfar_train_mean_median": median(rows, "unit_cfar_train_mean"),
            "unit_cfar_threshold_median": median(rows, "unit_cfar_threshold"),
            "unit_cut_power_median": median(rows, "unit_cut_power"),
            "unit_scnr_det_out_db_median_detected_or_not": median(rows, "unit_scnr_det_out_db"),
            "unit_directional_mean_spread_db": ratio_db,
            "cell_definition": (
                "physical truth Doppler projected through the production CFAR axis; "
                "unit_go_cfar_pd uses a ±2 Doppler/range-bin resolution-cell gate, while "
                "unit_go_cfar_exact_cut_pd is the exact projected FFT CUT; neither includes "
                "clustering, target_select, relocalization, or truth matching"),
            "background_definition": (
                "compact_statistical: rayleigh_lognormal_texture area clutter + thermal noise; "
                "same production CSI/GO-CFAR path"),
        })
    write_csv(out / "go_cfar_real_background_sweep.csv", summaries)
    figure, axis = plt.subplots(figsize=(7.5, 4.7))
    for angle in sorted({float(row["truth_angle_deg"]) for row in summaries}):
        rows = [row for row in summaries if float(row["truth_angle_deg"]) == angle]
        rows.sort(key=lambda row: float(row["configured_target_snr_db"]))
        xs = [float(row["paired_calibrated_output_scnr_db"])
              if math.isfinite(float(row["paired_calibrated_output_scnr_db"]))
              else float(row["configured_target_snr_db"]) for row in rows]
        ys = [float(row["unit_go_cfar_pd"]) for row in rows]
        low = [float(row["unit_go_cfar_pd_wilson95_low"]) for row in rows]
        high = [float(row["unit_go_cfar_pd_wilson95_high"]) for row in rows]
        axis.errorbar(xs, ys, yerr=[np.maximum(0.0, np.subtract(ys, low)),
                                    np.maximum(0.0, np.subtract(high, ys))],
                      marker="o", capsize=3, label=f"{angle:g}°")
    axis.axhline(0.9, color="tab:red", linestyle="--", label="Pd=0.90")
    axis.set(xlabel="配对标定输出 SCNR (dB)", ylabel="truth 分辨单元 GO 检测概率",
             ylim=(-0.02, 1.02), title="F08 紧凑统计杂波背景下的生产 GO-CFAR 单元 Pd")
    axis.grid(True, alpha=0.3)
    axis.legend()
    figure.tight_layout()
    figure.savefig(out / "F08_go_real_background_cell_pd.png", dpi=180)
    plt.close(figure)
    print(f"[PASS] 已汇总 {len(summaries)} 组正式统计背景 GO 单元 Pd：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
