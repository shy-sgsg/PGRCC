#!/usr/bin/env python3
"""汇总 125 目标 Monte Carlo，输出轻量 CSV、PNG、Markdown 和 HTML。"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import numpy as np


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"empty output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def f(row: dict[str, str], key: str, default: float = math.nan) -> float:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def i(row: dict[str, str], key: str) -> int:
    return int(round(f(row, key, 0.0)))


def aggregate(trials: list[dict[str, str]]) -> list[dict[str, object]]:
    groups: dict[tuple[int, float], list[dict[str, str]]] = {}
    for row in trials:
        groups.setdefault((i(row, "min_points"), f(row, "SCNR_target")), []).append(row)
    output: list[dict[str, object]] = []
    for (min_points, scnr), rows in sorted(groups.items()):
        target_total = sum(i(row, "target_total") for row in rows)
        target_detected = sum(i(row, "target_detected") for row in rows)
        track_total = sum(i(row, "track_total") for row in rows)
        track_detected = sum(i(row, "track_detected") for row in rows)
        production_track_detected = sum(i(row, "production_track_detected") for row in rows)
        angle_count = sum(i(row, "angle_count") for row in rows)
        angle_sum = sum(f(row, "angle_sum", 0.0) for row in rows)
        angle_abs_sum = sum(f(row, "angle_abs_sum", 0.0) for row in rows)
        angle_square_sum = sum(f(row, "angle_square_sum", 0.0) for row in rows)
        linear_values = [f(row, "measured_output_SCNR_linear") for row in rows]
        linear_values = [value for value in linear_values if math.isfinite(value) and value > 0.0]
        output.append({
            "min_points": min_points,
            "SCNR_target": scnr,
            "mc_count": len(rows),
            "target_total": target_total,
            "target_detected": target_detected,
            "Pd_target": target_detected / target_total if target_total else math.nan,
            "track_total": track_total,
            "track_detected": track_detected,
            "Pd_track": track_detected / track_total if track_total else math.nan,
            "production_track_detected": production_track_detected,
            "production_Pd_track": production_track_detected / track_total if track_total else math.nan,
            "angle_count": angle_count,
            "angle_bias": angle_sum / angle_count if angle_count else math.nan,
            "angle_MAE": angle_abs_sum / angle_count if angle_count else math.nan,
            "angle_RMSE": math.sqrt(angle_square_sum / angle_count) if angle_count else math.nan,
            "angle_sum": angle_sum,
            "angle_abs_sum": angle_abs_sum,
            "angle_square_sum": angle_square_sum,
            "measured_output_SCNR": 10.0 * math.log10(float(np.mean(linear_values))) if linear_values else math.nan,
            "track_metric": rows[0].get("track_metric", ""),
        })
    return output


def validate(rows: list[dict[str, object]], *, expected_mc: int | None,
             allow_min_points_subset: bool = False) -> None:
    if not rows:
        raise ValueError("no trial rows")
    combinations = {(int(row["min_points"]), float(row["SCNR_target"])) for row in rows}
    expected_levels = {13.0 + 0.5 * index for index in range(25)}
    if {level for _, level in combinations} != expected_levels:
        raise ValueError("SCNR grid is not 13:0.5:25 dB")
    if not allow_min_points_subset and {mp for mp, _ in combinations} != set(range(3, 9)):
        raise ValueError("min_points grid is not 3..8")
    for row in rows:
        if int(row["target_total"]) != 15 * int(row["mc_count"]):
            raise ValueError("target denominator is not 15 per Monte Carlo")
        if int(row["track_total"]) != 5 * int(row["mc_count"]):
            raise ValueError("track denominator is not 5 per Monte Carlo")
        if row["track_metric"] != "truth_target_detected_at_least_2_of_3_periods":
            raise ValueError("primary track metric is not the accepted two-of-three definition")
        if expected_mc is not None and int(row["mc_count"]) != expected_mc:
            raise ValueError(f"expected {expected_mc} Monte Carlo rows per combination")


def build_plots(rows: list[dict[str, object]], output: Path,
                trials: list[dict[str, str]]) -> list[Path]:
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    colors = {mp: color for mp, color in zip(range(3, 9), plt.cm.viridis(np.linspace(0.05, 0.95, 6)))}

    def line_plot(field: str, ylabel: str, filename: str, title: str) -> None:
        fig, ax = plt.subplots(figsize=(8.6, 5.0), constrained_layout=True)
        for mp in range(3, 9):
            group = sorted((r for r in rows if int(r["min_points"]) == mp), key=lambda r: float(r["SCNR_target"]))
            ax.plot([float(r["SCNR_target"]) for r in group], [float(r[field]) for r in group],
                    marker="o", markersize=2.8, label=f"min_points={mp}", color=colors[mp])
        ax.set(xlabel="Target SCNR (dB)", ylabel=ylabel, title=title)
        ax.grid(True, alpha=0.25)
        ax.legend(ncol=2)
        path = output / filename
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths.append(path)

    line_plot("Pd_target", "Target-level Pd", "target_pd_vs_scnr.png", "Target Pd vs SCNR")
    line_plot("Pd_track", "Track Pd (truth target detected ≥2/3 periods)", "track_pd_vs_scnr.png", "Track Pd vs SCNR")
    line_plot("angle_RMSE", "Angle RMSE (deg)", "angle_rmse_vs_scnr.png", "Angle RMSE vs SCNR")
    line_plot("angle_bias", "Angle bias (deg)", "angle_bias_vs_scnr.png", "Angle bias vs SCNR")
    line_plot("measured_output_SCNR", "Measured output SCNR (dB)", "target_vs_measured_scnr.png", "Target SCNR vs measured output SCNR")
    line_plot("Pd_target", "Target-level Pd", "min_points_target_pd_comparison.png", "Six min_points target Pd comparison")
    line_plot("Pd_track", "Track Pd", "min_points_track_pd_comparison.png", "Six min_points track Pd comparison")
    line_plot("angle_RMSE", "Angle RMSE (deg)", "min_points_angle_rmse_comparison.png", "Six min_points angle RMSE comparison")

    fig, ax = plt.subplots(figsize=(8.6, 5.0), constrained_layout=True)
    for mp in range(3, 9):
        group = sorted((r for r in rows if int(r["min_points"]) == mp), key=lambda r: float(r["SCNR_target"]))
        ax.plot([float(r["SCNR_target"]) for r in group], [float(r["angle_RMSE"]) for r in group],
                color=colors[mp], linewidth=2.0, label=f"overall min_points={mp}")
        # One light marker per MC×SCNR value is added by the caller's trial table
        # only when available; this panel remains the required aggregate curve.
    ax.set(xlabel="Target SCNR (dB)", ylabel="Angle RMSE (deg)", title="Overall angle RMSE curves")
    ax.grid(True, alpha=0.25)
    ax.legend(ncol=2)
    path = output / "rmse_overall_curves.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(path)

    fig, ax = plt.subplots(figsize=(8.6, 5.0), constrained_layout=True)
    for mp in range(3, 9):
        points = [trial for trial in trials if i(trial, "min_points") == mp and math.isfinite(f(trial, "angle_RMSE"))]
        ax.scatter([f(trial, "SCNR_target") for trial in points],
                   [f(trial, "angle_RMSE") for trial in points],
                   s=8, alpha=0.18, color=colors[mp], label=f"MC points min_points={mp}")
        group = sorted((r for r in rows if int(r["min_points"]) == mp), key=lambda r: float(r["SCNR_target"]))
        ax.plot([float(r["SCNR_target"]) for r in group], [float(r["angle_RMSE"]) for r in group],
                color=colors[mp], linewidth=2.0)
    ax.set(xlabel="Target SCNR (dB)", ylabel="Single-MC angle RMSE (deg)",
           title="Single-MC RMSE scatter and all-valid-sample RMSE curves")
    ax.grid(True, alpha=0.25)
    ax.legend(ncol=2, fontsize=8)
    path = output / "rmse_single_mc_scatter.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(path)
    return paths


def write_report(output: Path, rows: list[dict[str, object]], plots: list[Path], expected_mc: int | None) -> None:
    lines = [
        "# GMTI min_points × output SCNR Monte Carlo",
        "",
        f"统计输入：`trial_results.csv`；每个组合目标分母为 15×MC，航迹分母为 5×MC。当前 MC 数：{expected_mc if expected_mc is not None else '未指定'}。",
        "",
        "航迹级 Pd 使用用户确认的口径：真实目标在三个周期中至少有两次生产检测即计为成功。生产 TrackManager 严格身份确认结果保存在 `production_Pd_track`。",
        "",
        "总角度 RMSE 直接由所有有效角度误差平方和计算，不对每次 Monte Carlo 的 RMSE 做简单平均。",
        "",
        "## 图表",
        "",
    ]
    lines.extend(f"- [{path.name}](figures/{path.name})" for path in plots)
    lines.extend(["", "## 汇总字段", "", "`summary_by_min_points_scnr.csv` 保存每个 min_points×SCNR 的完整分母、Pd、测角 Bias/MAE/RMSE、实测输出 SCNR 及生产航迹对照。"])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    html = "<html><meta charset='utf-8'><title>GMTI Monte Carlo</title><body>" + "\n".join(
        f"<h1>GMTI min_points × output SCNR Monte Carlo</h1><p>{lines[2]}</p><p>{lines[4]}</p>" +
        "".join(f"<p><img src='figures/{path.name}' style='max-width:900px'></p>" for path in plots) +
        "</body></html>"
    )
    (output / "report.html").write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-mc", type=int, default=None)
    parser.add_argument("--allow-min-points-subset", action="store_true",
                        help="pilot-only: permit a subset of 3..8")
    args = parser.parse_args()
    trials = read_csv(args.input / "trial_results.csv")
    summary = aggregate(trials)
    validate(summary, expected_mc=args.expected_mc,
             allow_min_points_subset=args.allow_min_points_subset)
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "summary_by_min_points_scnr.csv", summary)
    plots = build_plots(summary, args.output / "figures", trials)
    write_report(args.output, summary, plots, args.expected_mc)
    print(f"summary_rows={len(summary)} plots={len(plots)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
