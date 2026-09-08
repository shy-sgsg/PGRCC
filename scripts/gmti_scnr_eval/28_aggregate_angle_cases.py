#!/usr/bin/env python3
"""Aggregate the completed center/edge angle cases into auditable CSV/plots.

Each input case is one already-audited real-CUDA production run.  This script
does not refit thresholds, recompute detections, or pool incompatible seeds;
it only joins the per-case curve/period tables and produces descriptive
center-versus-edge figures.  The x axis of every probability figure is the
measured output SCNR.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def num(value: object, default: float = float("nan")) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def fmt(value: object, digits: int = 3) -> str:
    result = num(value)
    return f"{result:.{digits}f}" if math.isfinite(result) else "—"


def safe_name(label: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in label)


def parse_case(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("case 格式必须为 label=directory")
    label, directory = value.split("=", 1)
    if not label or not directory:
        raise argparse.ArgumentTypeError("case 的 label 和 directory 不能为空")
    return label, Path(directory)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", type=parse_case, required=True,
                        help="label=case_dir，可重复六次")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    curve_rows: list[dict[str, object]] = []
    period_rows: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []

    for label, supplied in args.case:
        case = supplied.resolve()
        manifest_path = case / "manifest.json"
        curve_path = case / "standard_mc_curve_summary.csv"
        period_path = case / "standard_mc_period_metrics.csv"
        for required in (manifest_path, curve_path, period_path):
            if not required.is_file():
                raise RuntimeError(f"案例 {label} 缺少 {required}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        position = "edge" if abs(num(manifest.get("target_angle_offset_deg"))) > 1e-9 else "center"
        command_angle = num(manifest.get("angle_deg"))
        physical_angle = num(manifest.get("target_angle_deg"))
        summary_rows.append({
            "case": label,
            "position": position,
            "command_angle_deg": command_angle,
            "physical_target_angle_deg": physical_angle,
            "target_angle_offset_deg": num(manifest.get("target_angle_offset_deg")),
            "seed": manifest.get("seed", ""),
            "period_count": manifest.get("period_count", ""),
            "screen_group_count": manifest.get("screen_group_count", ""),
            "calibration_slope": num(manifest.get("calibration_slope")),
            "calibration_intercept": num(manifest.get("calibration_intercept")),
            "calibration_r2": num(manifest.get("calibration_r2")),
            "calibration_max_abs_bias_db": num(manifest.get("calibration_max_abs_bias_db")),
            "calibration_sanity_pass": manifest.get("calibration_sanity_pass", ""),
            "raw_cleanup": manifest.get("raw_cleanup", ""),
            "source_dir": str(case),
        })
        for row in read_csv(curve_path):
            curve_rows.append({
                "case": label,
                "position": position,
                "command_angle_deg": command_angle,
                "physical_target_angle_deg": physical_angle,
                **row,
            })
        for row in read_csv(period_path):
            period_rows.append({
                "case": label,
                "position": position,
                "command_angle_deg": command_angle,
                "physical_target_angle_deg": physical_angle,
                **row,
            })

    write_csv(out / "angle_case_curve.csv", curve_rows)
    write_csv(out / "angle_case_period_audit.csv", period_rows)
    write_csv(out / "angle_case_summary.csv", summary_rows)

    colors = plt.get_cmap("tab10")
    labels = [row[0] for row in args.case]
    by_case = {label: [row for row in curve_rows if row["case"] == label] for label in labels}
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8), sharex=False)
    for index, (field, title) in enumerate((
        ("unit_pd", "Unit GO Pd"),
        ("target_pd", "Target Pd"),
        ("track_pd", "Truth track Pd (2 of 3)"),
    )):
        ax = axes[index]
        for color_index, label in enumerate(labels):
            rows = sorted(by_case[label], key=lambda row: num(row.get("output_scnr_db")))
            x = [num(row.get("output_scnr_db")) for row in rows]
            y = [num(row.get(field)) for row in rows]
            ax.plot(x, y, marker="o", linewidth=1.2, label=label,
                    color=colors(color_index % 10))
        ax.set_xlabel("Measured output SCNR (dB)")
        ax.set_ylabel("Probability")
        ax.set_ylim(-0.05, 1.05)
        ax.grid(True, alpha=0.3)
        ax.set_title(title)
    axes[-1].legend(fontsize=7, loc="lower right")
    fig.tight_layout()
    fig.savefig(out / "angle_cases_probability_vs_output_scnr.png", dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 5.2))
    for color_index, label in enumerate(labels):
        rows = sorted(by_case[label], key=lambda row: num(row.get("output_scnr_db")))
        x = [num(row.get("output_scnr_db")) for row in rows if math.isfinite(num(row.get("angle_rmse_deg")))]
        y = [num(row.get("angle_rmse_deg")) for row in rows if math.isfinite(num(row.get("angle_rmse_deg")))]
        if x:
            ax.plot(x, y, marker="o", linewidth=1.2, label=label, color=colors(color_index % 10))
    ax.set_xlabel("Measured output SCNR (dB)")
    ax.set_ylabel("Matched angle RMSE (deg)")
    ax.grid(True, alpha=0.3)
    ax.set_title("Angle RMSE versus output SCNR (finite truth matches only)")
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(out / "angle_cases_angle_rmse_vs_output_scnr.png", dpi=180)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5))
    x = list(range(len(summary_rows)))
    axes[0].bar(x, [num(row.get("calibration_slope")) for row in summary_rows], color="#4472c4")
    axes[0].axhline(1.0, color="black", linewidth=0.8)
    axes[0].set_ylabel("Slope")
    axes[0].set_title("Output-SCNR calibration slope")
    axes[1].bar(x, [num(row.get("calibration_r2")) for row in summary_rows], color="#70ad47")
    axes[1].set_ylim(0.9, 1.01)
    axes[1].set_ylabel("R²")
    axes[1].set_title("Calibration R²")
    axes[2].bar(x, [num(row.get("calibration_max_abs_bias_db")) for row in summary_rows], color="#ed7d31")
    axes[2].set_ylabel("Max |bias| (dB)")
    axes[2].set_title("Calibration max absolute bias")
    for ax in axes:
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=55, ha="right", fontsize=8)
        ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "angle_cases_calibration_audit.png", dpi=180)
    plt.close(fig)

    table_lines = [
        "# Angle-case aggregation",
        "",
        "六个案例均为真实 CUDA、S+C+N（当前配置关闭面积杂波、保留热噪声）生产链路；本表只汇总，不重新拟合阈值。",
        "",
        "| Case | Position | Command angle | Physical target angle | Seed | Slope | R² | Max bias (dB) | Sanity |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary_rows:
        table_lines.append(
            f"| {row['case']} | {row['position']} | {fmt(row['command_angle_deg'], 1)} | "
            f"{fmt(row['physical_target_angle_deg'], 1)} | {row['seed']} | "
            f"{fmt(row['calibration_slope'])} | {fmt(row['calibration_r2'], 4)} | "
            f"{fmt(row['calibration_max_abs_bias_db'])} | {row['calibration_sanity_pass']} |"
        )
    table_lines += [
        "",
        "图 `angle_cases_probability_vs_output_scnr.png` 的横轴是实际测得 output SCNR；",
        "`angle_cases_angle_rmse_vs_output_scnr.png` 只绘制存在 truth-matched angle 的档位。",
        "每个案例只有一个 seed、每档三个 screen period，因此这些曲线是模块验收和问题定位证据，",
        "不是最终多 seed 概率置信区间。中心 30°/45°及边缘 0°的 calibration_sanity 为 false，",
        "这在报告中保留为待多 seed 复核项，不通过改变门限掩盖。",
    ]
    (out / "angle_case_aggregation_report.md").write_text("\n".join(table_lines) + "\n", encoding="utf-8")
    print(f"[PASS] 角度案例汇总完成：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
