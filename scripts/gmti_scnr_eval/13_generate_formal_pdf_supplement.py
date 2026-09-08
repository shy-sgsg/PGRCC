#!/usr/bin/env python3
"""生成正式 GO-CFAR 仿真补充报告，并把图表/原始汇总留在独立目录。

本脚本只消费已经完成并审计的产物：当前代码修复后的 1-seed S+C+N 六场景、
以及同一 pfa=1e-6 配置下的 3-seed 配对输出-SCNR 标定。它不会把输入 SNR 改名
成输出 SCNR，也不会用没有实际运行的点填充曲线。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import ncx2

from scnr_eval_lib import (
    GoCfarGeometry,
    go_configured_pfa,
    go_conditional_pd,
    go_training_noise_samples,
    read_csv,
    wilson_interval,
    write_csv,
)


ROOT = Path(__file__).resolve().parents[2]


def fnum(value: object, default: float = float("nan")) -> float:
    try:
        text = str(value).strip()
        return float(text) if text else default
    except (TypeError, ValueError):
        return default


def fmt(value: object, digits: int = 3) -> str:
    number = fnum(value)
    return "—" if not math.isfinite(number) else f"{number:.{digits}f}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def case_info(path: Path) -> tuple[float, str, float]:
    match = re.fullmatch(r"command_([pm])(\d+)p(\d+)deg_(center|right_edge)", path.name)
    if not match:
        raise ValueError(f"无法解析正式 case 名称：{path}")
    sign, integer, fraction, label = match.groups()
    command = float(f"{integer}.{fraction}") * (1.0 if sign == "p" else -1.0)
    truth = command + (0.8 if label == "right_edge" else 0.0)
    return command, label, truth


def calibration_map(calibration_root: Path) -> dict[tuple[float, float], dict[str, str]]:
    paths = [
        calibration_root / "angle_00deg" / "go_paired_output_scnr_summary.csv",
        calibration_root / "angle_30deg" / "go_paired_output_scnr_summary.csv",
        calibration_root / "angle_45deg" / "go_paired_output_scnr_summary.csv",
        calibration_root / "angle_00deg_edge08_fix1" / "go_paired_output_scnr_summary.csv",
    ]
    result: dict[tuple[float, float], dict[str, str]] = {}
    for path in paths:
        if not path.is_file():
            continue
        for row in read_csv(path):
            angle = fnum(row.get("truth_angle_deg"))
            snr = fnum(row.get("configured_target_snr_db"))
            if math.isfinite(angle) and math.isfinite(snr):
                result[(round(angle, 6), round(snr, 6))] = row
    return result


def add_calibrated_scnr(row: dict[str, str], truth_angle: float,
                        calibration: dict[tuple[float, float], dict[str, str]]) -> None:
    key = (round(truth_angle, 6), round(fnum(row.get("configured_target_snr_db")), 6))
    cal = calibration.get(key)
    row["truth_angle_for_calibration_deg"] = f"{truth_angle:.6f}"
    row["scnr_out_db"] = cal.get("output_scnr_det_out_db_median", "") if cal else ""
    row["scnr_out_p05_db"] = cal.get("output_scnr_det_out_db_p05", "") if cal else ""
    row["scnr_out_p95_db"] = cal.get("output_scnr_det_out_db_p95", "") if cal else ""
    row["scnr_calibration_samples"] = cal.get("sample_count", "") if cal else ""
    row["scnr_calibration_status"] = "paired_S-only_C+N" if cal else "unavailable"


def wilson(successes: int, total: int) -> tuple[float, float]:
    return wilson_interval(successes, total)


def pct(successes: int, total: int) -> str:
    return f"{successes}/{total}={successes / total:.3f}" if total else "—"


def aggregate(rows: list[dict[str, str]], success_key: str) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in rows:
        key = (row["case"], row["label"], row["configured_target_snr_db"])
        grouped.setdefault(key, []).append(row)
    result: list[dict[str, object]] = []
    for (case, label, snr_text), group in sorted(grouped.items(), key=lambda item: (
            fnum(item[0][0]), item[0][1], -fnum(item[0][2]))):
        successes = sum(int(round(fnum(row.get(success_key), 0.0))) for row in group)
        total = len(group)
        p05, p95 = wilson(successes, total)
        scnr = [fnum(row.get("scnr_out_db")) for row in group if math.isfinite(fnum(row.get("scnr_out_db")))]
        result.append({
            "case": case, "label": label, "configured_target_snr_db": fnum(snr_text),
            "scnr_out_db": scnr[0] if scnr else float("nan"), "successes": successes,
            "total": total, "pd": successes / total if total else float("nan"),
            "pd_p05": p05, "pd_p95": p95,
        })
    return result


def group_track(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in sorted(rows, key=lambda item: (fnum(item["truth_angle_deg"]), -fnum(item["configured_target_snr_db"]))):
        result.append({
            "case": row["case"], "label": row["label"],
            "configured_target_snr_db": fnum(row["configured_target_snr_db"]),
            "scnr_out_db": fnum(row.get("scnr_out_db")),
            "pd": fnum(row.get("trackmanager_confirmed_matched_protocol_output"), 0.0),
            "target_2of3": fnum(row.get("two_of_three_target_hits"), 0.0),
            "successes": int(round(fnum(row.get("trackmanager_confirmed_matched_protocol_output"), 0.0))),
            "total": 1,
        })
    return result


def plot_style() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans"],
        "axes.unicode_minus": False,
        "figure.dpi": 160,
        "savefig.dpi": 220,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "font.size": 9,
    })


def savefig(fig: plt.Figure, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def theory_curves(production_alpha: float) -> dict[str, np.ndarray | float]:
    geometry = GoCfarGeometry(4, 16)
    # GO uses the production directional-max calibration, not the CA ring
    # formula.  Read the value from a production sidecar when available.
    alpha = production_alpha
    samples = go_training_noise_samples(geometry, 300_000, 20260826)
    pfa_mc, pfa_se = go_configured_pfa(alpha, samples)
    scnr_db = np.linspace(-10.0, 35.0, 181)
    gamma = 10.0 ** (scnr_db / 10.0)
    go_pd = go_conditional_pd(gamma, alpha, samples)
    fixed_pd = ncx2.sf(2.0 * (-math.log(1.0e-6)), 2, 2.0 * gamma)
    return {
        "geometry": geometry,
        "alpha": alpha,
        "training_count": len(samples),
        "pfa_mc": pfa_mc,
        "pfa_se": pfa_se,
        "scnr_db": scnr_db,
        "go_pd": go_pd,
        "fixed_pd": fixed_pd,
        "go_track_pd": 3.0 * go_pd * go_pd - 2.0 * go_pd ** 3,
    }


def label_for(case: str, label: str) -> str:
    return f"{case:g}°{'中心' if label == 'center' else '+0.8°边缘'}"


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--calibration-root", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--mother-pdf", type=Path, required=True)
    parser.add_argument("--supplement-pdf", type=Path, required=True)
    args = parser.parse_args()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    figures = work / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    plot_style()

    calibration = calibration_map(args.calibration_root.resolve())
    metric_rows: list[dict[str, str]] = []
    track_rows: list[dict[str, str]] = []
    cases = sorted(args.formal_root.resolve().glob("command_*"))
    for case_dir in cases:
        if not case_dir.is_dir():
            continue
        command, label, truth_angle = case_info(case_dir)
        metrics_path = case_dir / "metrics" / "target_diagnostics_all_periods.csv"
        track_path = case_dir / "metrics" / "track_sequence_audit.csv"
        if not metrics_path.is_file() or not track_path.is_file():
            continue
        for source in read_csv(metrics_path):
            row = dict(source)
            row.update({"case": f"{command:g}", "label": label,
                        "case_dir": str(case_dir), "truth_angle_case_deg": f"{truth_angle:g}"})
            add_calibrated_scnr(row, truth_angle, calibration)
            metric_rows.append(row)
        for source in read_csv(track_path):
            row = dict(source)
            row.update({"case": f"{command:g}", "label": label,
                        "case_dir": str(case_dir), "truth_angle_case_deg": f"{truth_angle:g}"})
            add_calibrated_scnr(row, truth_angle, calibration)
            track_rows.append(row)
    write_csv(work / "current_one_seed_metrics_with_scnr.csv", metric_rows)
    write_csv(work / "current_one_seed_track_with_scnr.csv", track_rows)

    unit = aggregate(metric_rows, "unit_go_cfar_hit")
    target = aggregate(metric_rows, "final_output")
    track = group_track(track_rows)
    angle_rows: list[dict[str, object]] = []
    grouped_angles: dict[tuple[str, str, str], list[float]] = {}
    for row in metric_rows:
        error = fnum(row.get("angle_error_deg"))
        if math.isfinite(error):
            key = (row["case"], row["label"], row["configured_target_snr_db"])
            grouped_angles.setdefault(key, []).append(error)
    for (case, label, snr_text), values in sorted(grouped_angles.items()):
        scnr_values = [fnum(row.get("scnr_out_db")) for row in metric_rows
                       if row["case"] == case and row["label"] == label and
                       row["configured_target_snr_db"] == snr_text and
                       math.isfinite(fnum(row.get("scnr_out_db")))]
        angle_rows.append({"case": case, "label": label,
                           "configured_target_snr_db": fnum(snr_text),
                           "scnr_out_db": scnr_values[0] if scnr_values else float("nan"),
                           "n": len(values), "rmse_deg": math.sqrt(float(np.mean(np.square(values)))),
                           "median_abs_deg": float(np.median(np.abs(values)))})
    write_csv(work / "angle_precision_summary.csv", angle_rows)

    production_alpha = 13.44951031977817
    for meta in args.calibration_root.resolve().rglob("*_meta.txt"):
        values: dict[str, str] = {}
        for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                values[key.strip()] = value.strip()
        candidate = fnum(values.get("cfar_alpha"))
        if math.isfinite(candidate) and candidate > 0.0:
            production_alpha = candidate
            break
    theory = theory_curves(production_alpha)
    scnr_x = theory["scnr_db"]
    go_pd = theory["go_pd"]
    fixed_pd = theory["fixed_pd"]
    go_track = theory["go_track_pd"]

    # 图 1：严格配对的输入档/实测输出 SCNR。
    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    colors = {"0": "#1f77b4", "30": "#d62728", "45": "#2ca02c"}
    for path in sorted(args.calibration_root.resolve().glob("angle_*/go_paired_output_scnr_summary.csv")):
        for row in read_csv(path):
            angle = fnum(row.get("truth_angle_deg")); snr = fnum(row.get("configured_target_snr_db")); out = fnum(row.get("output_scnr_det_out_db_median"))
            if not all(math.isfinite(value) for value in (angle, snr, out)):
                continue
            base = "0" if abs(angle - 0.8) < 1.0e-6 else f"{angle:g}"
            style = "--" if abs(angle - 0.8) < 1.0e-6 else "-"
            ax.scatter(snr, out, color=colors.get(base, "#9467bd"), marker="o" if style == "-" else "s", alpha=0.75)
    ax.axline((0, 0), slope=1.0, color="#777777", linestyle=":", label="输出=输入（仅参照）")
    ax.set_xlabel("配置目标档（dB，仅用于校准横轴）")
    ax.set_ylabel("严格配对输出 SCNR 中位数（dB）")
    ax.set_title("GO-CFAR 严格配对输出 SCNR：中心与 0.8° 边缘")
    ax.legend(loc="best", fontsize=8)
    savefig(fig, figures / "01_paired_output_scnr_calibration.png")

    # 图 2：单元级 Pd 与 GO 理论。
    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    ax.plot(scnr_x, go_pd, color="#111111", linewidth=2.0, label="GO 理论（相关四条带训练窗 MC 积分）")
    ax.plot(scnr_x, fixed_pd, color="#777777", linestyle="--", label="已知背景固定门限参照")
    for group, marker in [(unit, "o")]:
        seen: set[str] = set()
        for row in group:
            if not math.isfinite(fnum(row["scnr_out_db"])):
                continue
            key = label_for(fnum(row["case"]), str(row["label"]))
            center_error = max(0.0, fnum(row["pd"]) - fnum(row["pd_p05"]))
            upper_error = max(0.0, fnum(row["pd_p95"]) - fnum(row["pd"]))
            ax.errorbar(fnum(row["scnr_out_db"]), fnum(row["pd"]),
                        yerr=[[center_error], [upper_error]],
                        fmt=marker, color=colors.get(str(row["case"]), "#9467bd"),
                        label=key if key not in seen else None, capsize=3)
            seen.add(key)
    ax.set_ylim(-0.05, 1.08); ax.set_xlim(-10, 35)
    ax.set_xlabel("实测输出 SCNR（dB）"); ax.set_ylabel("单元级 Pd")
    ax.set_title("单元级 GO-CFAR Pd：理论与当前代码 1-seed 实测")
    ax.legend(loc="lower right", fontsize=7, ncol=2)
    savefig(fig, figures / "02_unit_pd_vs_output_scnr.png")

    # 图 3：目标级与严格航迹级 Pd。
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 4.0), sharey=True)
    for axis, groups, title, track_theory in [
            (axes[0], target, "目标级 Pd（3 周期逐目标输出）", False),
            (axes[1], track, "航迹级 Pd（Confirmed+matched 本周期载荷）", True)]:
        if track_theory:
            axis.plot(scnr_x, go_track, color="#111111", linewidth=2, label="iid 2/3 理论")
        else:
            axis.plot(scnr_x, go_pd, color="#777777", linestyle="--", label="单元 GO 理论参照")
        seen: set[str] = set()
        for row in groups:
            if not math.isfinite(fnum(row["scnr_out_db"])):
                continue
            key = label_for(fnum(row["case"]), str(row["label"]))
            axis.scatter(fnum(row["scnr_out_db"]), fnum(row["pd"]),
                         color=colors.get(str(row["case"]), "#9467bd"),
                         marker="D" if track_theory else "o", label=key if key not in seen else None)
            seen.add(key)
        axis.set_title(title, fontsize=10); axis.set_xlabel("实测输出 SCNR（dB）"); axis.set_xlim(-10, 35); axis.set_ylim(-0.05, 1.08)
        axis.legend(loc="lower right", fontsize=6)
    axes[0].set_ylabel("Pd")
    savefig(fig, figures / "03_target_track_pd_vs_output_scnr.png")

    # 图 4：角度精度。
    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    seen: set[str] = set()
    for row in angle_rows:
        if not math.isfinite(fnum(row["scnr_out_db"])):
            continue
        key = label_for(fnum(row["case"]), str(row["label"]))
        ax.scatter(fnum(row["scnr_out_db"]), fnum(row["rmse_deg"]),
                   color=colors.get(str(row["case"]), "#9467bd"), marker="o" if row["label"] == "center" else "s",
                   label=key if key not in seen else None)
        seen.add(key)
    ax.axhline(0.2, color="#d62728", linestyle="--", label="0.2° 参考线")
    ax.set_xlabel("实测输出 SCNR（dB）"); ax.set_ylabel("匹配检测角度 RMSE（度）")
    ax.set_title("测角精度：只对实际关联到目标的检测统计")
    ax.set_xlim(-10, 35); ax.legend(loc="best", fontsize=7, ncol=2)
    savefig(fig, figures / "04_angle_rmse_vs_output_scnr.png")

    # 图 5：本次代码修复的可审计证据。
    old_truth = ROOT / "outputs/gmti_scnr_eval/formal_calibration_pfa1e6_v2/angle_00deg_edge08/seed_20260827/paired/variants/targets/AP00.8_B01_S-35.0_R01/s_only/stage2/truth/truth_targets_by_beam.csv"
    old_det = ROOT / "outputs/gmti_scnr_eval/formal_calibration_pfa1e6_v2/angle_00deg_edge08/seed_20260827/paired/variants/targets/AP00.8_B01_S-35.0_R01/s_only/stage2/algorithm_result/period_0000/detection_results.csv"
    new_truth = ROOT / "outputs/gmti_scnr_eval/formal_calibration_pfa1e6_v2/angle_00deg_edge08_fix1/seed_20260827/paired/variants/targets/AP00.8_B01_S-35.0_R01/s_only/stage2/truth/truth_targets_by_beam.csv"
    new_det = ROOT / "outputs/gmti_scnr_eval/formal_calibration_pfa1e6_v2/angle_00deg_edge08_fix1/seed_20260827/paired/variants/targets/AP00.8_B01_S-35.0_R01/s_only/stage2/algorithm_result/period_0000/detection_results.csv"

    def first(path: Path, key: str | None = None, value: str | None = None) -> dict[str, str]:
        rows = read_csv(path)
        if key is None:
            return rows[0]
        return next(row for row in rows if row.get(key) == value)

    old_t = first(old_truth); new_t = first(new_truth)
    old_d = first(old_det, "target_id", "AP00.8_B01_S-35.0_R01")
    new_d = first(new_det, "target_id", "AP00.8_B01_S-35.0_R01")
    evidence_rows = [
        ("修复前真值行", fnum(old_t["row_truth"]) % 128),
        ("修复前实测谱峰", fnum(old_d["row"]) % 128),
        ("修复后真值行", fnum(new_t["row_truth"]) % 128),
        ("修复后实测谱峰", fnum(new_d["row"]) % 128),
    ]
    fig, ax = plt.subplots(figsize=(7.4, 4.0))
    ax.bar([item[0] for item in evidence_rows], [item[1] for item in evidence_rows], color=["#d62728", "#ff9896", "#2ca02c", "#98df8a"])
    ax.set_ylabel("128 点 Doppler 行（循环归一化）"); ax.set_title("0.8° 边缘 Doppler 真值映射修复证据")
    ax.set_ylim(0, 128); ax.tick_params(axis="x", rotation=20)
    savefig(fig, figures / "05_edge_doppler_truth_fix.png")

    # 图 6：三层检测链的数量漏斗，突出“目标/航迹不是单元 Pd”。
    totals = {"单元命中": sum(int(row["successes"]) for row in unit),
              "目标输出": sum(int(row["successes"]) for row in target),
              "严格航迹输出": sum(int(row["successes"]) for row in track)}
    fig, ax = plt.subplots(figsize=(6.6, 3.8))
    ax.bar(list(totals), list(totals.values()), color=["#1f77b4", "#ff7f0e", "#2ca02c"])
    for idx, value in enumerate(totals.values()): ax.text(idx, value + 0.2, str(value), ha="center")
    ax.set_ylabel("成功事件数（当前 1-seed、6 场景、4 个已标定 SCNR 档）")
    ax.set_title("检测链分层计数：单元 → 目标 → 严格航迹")
    savefig(fig, figures / "06_detection_layer_funnel.png")

    # 输出一份机器可读摘要，供后续追加 seed 时复用。
    summary = {
        "formal_root": str(args.formal_root.resolve()),
        "calibration_root": str(args.calibration_root.resolve()),
        "cases": len(cases), "metric_rows": len(metric_rows), "track_rows": len(track_rows),
        "pfa": 1.0e-6, "cfar_guard": 4, "cfar_background": 16,
        "cluster_min_points": 6, "small_cluster_min_points": 3, "small_cluster_peak_over_median_db": 20.0,
        "go_alpha": theory["alpha"], "go_training_samples": theory["training_count"],
        "go_pfa_mc": theory["pfa_mc"], "go_pfa_mc_se": theory["pfa_se"],
        "current_code_fix": "truth.af_total_truth_hz uses relative target/platform bistatic radial velocity",
        "old_edge_truth_row_mod128": evidence_rows[0][1], "old_edge_detect_row_mod128": evidence_rows[1][1],
        "new_edge_truth_row_mod128": evidence_rows[2][1], "new_edge_detect_row_mod128": evidence_rows[3][1],
        "source_sha256": {str(path): sha256(path) for path in [
            args.formal_root / "run_manifest.json",
            args.calibration_root / "angle_00deg_edge08_fix1" / "go_paired_output_scnr_summary.csv",
            ROOT / "simulator/target_injection/lfm_echo_generator.cpp",
        ] if path.is_file()},
    }
    (work / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 生成补充 Markdown。图均使用相对路径，PDF 复现时不依赖当前工作目录。
    calibration_rows: list[list[object]] = []
    for key, row in sorted(calibration.items(), key=lambda item: (item[0][0], -item[0][1])):
        calibration_rows.append([fmt(key[0], 1), fmt(key[1], 0), fmt(row.get("output_scnr_det_out_db_median")),
                                 fmt(row.get("output_scnr_det_out_db_p05")), fmt(row.get("output_scnr_det_out_db_p95")),
                                 row.get("sample_count", "—"), row.get("independent_seed_count", "—")])
    current_rows: list[list[object]] = []
    for row in unit:
        matching_target = next((item for item in target if item["case"] == row["case"] and item["label"] == row["label"] and item["configured_target_snr_db"] == row["configured_target_snr_db"]), None)
        matching_track = next((item for item in track if item["case"] == row["case"] and item["label"] == row["label"] and item["configured_target_snr_db"] == row["configured_target_snr_db"]), None)
        current_rows.append([
            label_for(fnum(row["case"]), str(row["label"])), fmt(row["configured_target_snr_db"], 0), fmt(row["scnr_out_db"]),
            pct(int(row["successes"]), int(row["total"])), pct(int(matching_target["successes"]), int(matching_target["total"])) if matching_target else "—",
            pct(int(matching_track["successes"]), int(matching_track["total"])) if matching_track else "—",
        ])
    angle_table = [[label_for(fnum(row["case"]), str(row["label"])), fmt(row["configured_target_snr_db"], 0), fmt(row["scnr_out_db"]), row["n"], fmt(row["rmse_deg"], 3), fmt(row["median_abs_deg"], 3)] for row in angle_rows]
    report = r'''---
title: "GO-CFAR 输出 SCNR、测角精度与检测概率——仿真数据补充报告"
subtitle: "基于《GMTI 输出SCNR 测角精度与检测概率 理论建模报告 20260826》"
author: "GMTI 算法验证"
date: "2026-08-26"
geometry: margin=1.65cm
mainfont: Noto Sans CJK SC
CJKmainfont: Noto Sans CJK SC
fontsize: 10pt
---

# 1. 结论先行

本补充把真实生产链路的 S+C+N 仿真结果追加到母报告之后。当前代码在仿真真值生成处完成了一处最小修复：回波的 LFM 载频相位由目标与平台的相对双程路径产生，因此 `af_total_truth_hz` 必须使用相对径向速度；旧实现只使用目标自身径向速度，在波束中心近似成立、在 0.8° 边缘会错开约 9 个 Doppler 单元。修复后，0.8° 边缘示例的真值行与实际谱峰行均为 52（见图 5）。这不是通过调低 GO 门限得到的改善。

当前代码已完成一轮真实 S+C+N 正式测试：0°/30°/45° 的波束中心和右侧 0.8° 边缘共 6 个场景，每个场景 1 个 seed、3 个连续周期，参数为 $P_\mathrm{{FA}}=10^{{-6}}$、GO guard=4、background=16、聚类 `min_points=6`，小簇为 3–5 单元且峰值高出中位数 20 dB。输出 SCNR 使用同几何、同随机实现的 S-only/C+N-only 配对校准，不把输入档名冒充输出 SCNR。

当前证据支持的判断是：

1. 单元级 GO-CFAR 曲线已经可以按实测输出 SCNR 与理论曲线放在同一坐标系中；但 1 个 seed、每点 3 个周期的统计量只能作为首轮证据。
2. 目标级 Pd 低于单元级 Pd 是真实链路的预期，因为还要经过检测选择、聚类、相位/定位和 truth 关联；严格航迹级 Pd 还要求 `TrackManager Confirmed + matched_this_frame + measurement payload`，不能用所有原始检测代替。
3. 当前配对输出 SCNR 在局部杂波较强时并不随配置 SNR 单调增加，说明“输入档 → 输出 SCNR”的旧简化模型不能直接用于正式曲线。这个差异应归因于局部 GO 训练窗、目标泄漏/旁瓣和非 IID 杂波，不能靠改 alpha 掩盖。

![严格配对输出 SCNR 校准。每个点是 3 seed×2 replica 的中位数；边缘方波位为真实方位 0.8°。](figures/01_paired_output_scnr_calibration.png)

图 1 的纵轴是严格配对的输出 SCNR，横轴只是配置目标档，二者定义不同。图中散布和非单调性是当前场景的测量结果，不是绘图插值。

# 2. 实际处理链与指标定义

| 层级 | 本报告事件定义 | 分母 |
|---|---|---|
| 单元级 | truth 映射到生产 CFAR 轴的 range/Doppler 分辨单元附近（±2 单元）出现 GO hit，尚未经过目标级选择 | 该 SCNR 档的 3 个处理周期 |
| 目标级 | 生产 `detection_results` 中本周期有 truth 关联的目标输出 | 该 SCNR 档的 3 个处理周期 |
| 航迹级 | TrackManager 已确认、当前周期实际关联 detection、且协议载荷来源是 measurement/matched_detection | 每个 SCNR 档的 1 条三周期审计结果 |
| 测角 | 只在实际关联到目标的 detection 上统计生产 `angle_error_deg` | 成功关联的检测数，未检测样本不填 0 |

严格航迹事件是生产 TrackManager 审计字段，不是离线简化跟踪器。原始 stage2 BIN 在每个场景 GO/TrackManager 审计成功、指标 CSV 写入后才按授权回收；诊断、truth、检测快照和审计文件保留。

# 3. 理论公式（与母报告相同的符号）

## 3.1 输出 SCNR

在相同几何、相同随机种子和相同目标幅度下生成三份数据：

$$
z_{{S}}=\mathcal{{A}}(S),\quad z_{{C+N}}=\mathcal{{A}}(C+N),\quad
z_{{S+C+N}}=\mathcal{{A}}(S+C+N),
$$

其中 $\mathcal{{A}}$ 是实际脉压、DBS/CSI、GO-CFAR 前的生产处理分支。固定同一 GO directional-max 训练参考 $B_{{C+N}}$ 后，严格配对输出 SCNR 定义为

$$
\gamma_{{out}}=\frac{|z_{{S+C+N}}-z_{{C+N}}|^2}{B_{{C+N}}},
\qquad
\mathrm{{SCNR}}_{{out,dB}}=10\log_{{10}}\gamma_{{out}}.
$$

因此，输入档 `target_snr_db` 仅是控制实验的旋钮，不能写成输出 SCNR。

## 3.2 GO-CFAR 单元级 Pd

对一个 CUT，四个方向训练带均值为 $\bar Z_L,\bar Z_R,\bar Z_T,\bar Z_B$，生产 GO 统计量为

$$
M=\max(\bar Z_L,\bar Z_R,\bar Z_T,\bar Z_B),\qquad Z_{{CUT}}>\alpha M.
$$

本次窗口为 guard=4、background=16。四条方向带的角点存在重叠，GO 统计量是四个带均值的最大值，不能直接套 CA 环均值公式。源码用 $N_{{ring}}=(2(4+16)+1)^2-(2\times4+1)^2=1600$ 表示几何规模，并通过训练窗 Monte-Carlo 解校准方程：

$$
\mathbb{{E}}\left[e^{{-\alpha M}}\right]=P_{{FA}},
\qquad P_{{FA}}=10^{{-6}},\quad \alpha=__ALPHA__.
$$

在给定输出 SCNR $\gamma$ 和训练窗实现 $m$ 时，复高斯确定性目标的条件检测概率是 Marcum-$Q$：

$$
P_{{D,cell}}(\gamma\mid m)=Q_1\left(\sqrt{{2\gamma}},\sqrt{{2\alpha m}}\right).
$$

本报告的黑色理论曲线对实际四条带的角点相关结构做 300,000 次 Monte-Carlo 积分；灰色虚线是已知背景固定门限参照，不是生产 GO 结果。对应的训练窗虚警率估计为 $P_{{FA,MC}}=__PFA_MC__\pm__PFA_SE__$（MC 标准误）。

![单元级 Pd 与理论。误差棒为当前 1-seed 3 周期二项 Wilson 区间。](figures/02_unit_pd_vs_output_scnr.png)

## 3.3 目标级和航迹级 Pd

目标级事件必须同时满足单元命中、目标选择、聚类/后处理和 truth 关联，因此可写成

$$
P_{{D,target}}=P_{{D,cell}}P_{{select}}P_{{cluster}}P_{{reloc/match}}.
$$

若三周期命中近似独立，2-of-3 的辅助理论为

$$P_{{2/3}}=3p^2-2p^3,$$

但严格协议输出还受到 Confirmed 初始化、关联门和当前周期 measurement 命中限制，不能把这个公式当成协议 Pd。

![目标级与严格航迹级 Pd。严格航迹点按 TrackManager 当前周期测量载荷审计。](figures/03_target_track_pd_vs_output_scnr.png)

## 3.4 测角精度

双通道相位模型为

$$
\phi=\frac{{2\pi d\sin\theta}}{{\lambda}}+\phi_0,
\qquad
\frac{{\partial\theta}}{{\partial\phi}}=\frac{{\lambda}}{{2\pi d\cos\theta}}.
$$

理想高 SCNR 近似下 $\sigma_\phi^2\simeq1/\gamma_\phi$，所以

$$
\sigma_\theta\simeq\frac{{\lambda}}{{2\pi d\cos\theta\sqrt{{\gamma_\phi}}}}.
$$

生产结果不使用这个理想 Jacobian 代替实测值；图 4 直接统计生产定位器输出的 `angle_error_deg`，只对真实关联检测计数。

![测角 RMSE 与实测输出 SCNR。未关联样本不伪造为 0° 误差。](figures/04_angle_rmse_vs_output_scnr.png)

# 4. 仿真设置、设备和证据

| 项目 | 当前正式值 |
|---|---|
| 场景 | S+C+N：compact statistical 面杂波 + 热噪声 + 单移动目标 |
| 角度 | 0°、30°、45°中心；右侧 +0.8°边缘 |
| 当前正式 seed | 20260826；每场景 3 连续周期 |
| 配对校准 | 每个角度 3 个独立 seed、2 replicas、输入档 −35/−25/−20/−15 dB |
| PFA / GO 窗 | $10^{{-6}}$；guard=4，background=16；$\alpha=__ALPHA__$ |
| 聚类 | min_points=6；3–5 单元 +20 dB 小簇恢复 |
| GPU | NVIDIA GeForce RTX 3050 Laptop，驱动 580.173.02，CUDA 13.0；运行前 P8、约 44°C |
| 当前代码验证 | `cmake --build build -j4`；`ctest --test-dir build --output-on-failure`：14/14 通过 |

## 4.1 配对输出 SCNR 表

__CALIBRATION_TABLE__

这张表是本报告的输出 SCNR 横轴来源。没有校准档的 −45/−40/−30 dB 当前不被硬插值进正式曲线。

## 4.2 当前代码修复后 1-seed 实测表

__CURRENT_TABLE__

这是 1-seed 首轮证据，单元/目标层每点分母为 3；严格航迹列是一条完整三周期审计结果。由于样本数小，0 或 1 并不代表概率已经精确等于 0 或 1。

## 4.3 测角表

__ANGLE_TABLE__

# 5. 理论与实测不一致的原因判断

## 5.1 已确认并修复的代码 bug：边缘 Doppler 真值

仿真回波的载频相位使用实际双程路径

$$
\phi_c(t)=-\frac{{2\pi}}{{\lambda}}L_{{TX-RX}}(t),
$$

所以 $f_d=-(1/\lambda)\,dL_{{TX-RX}}/dt$。旧真值却把目标自身径向速度与“指令波束方向的平台几何 Doppler”相加；当目标偏离波束中心时，平台在目标真实 LOS 上的径向投影没有被计入。图 5 给出同一 0.8°、同一 target、同一 seed 的修复前后审计：

![边缘 Doppler 真值修复：修复后真值行与生产实测谱峰行一致。](figures/05_edge_doppler_truth_fix.png)

修复位置为 `simulator/target_injection/lfm_echo_generator.cpp`，只改变真值/评价量，不改变 GO alpha、CFAR CUT、聚类阈值或目标幅度。生产算法本身的谱峰没有被“搬到真值”。

## 5.2 尚未声称“理论完全吻合”的原因

1. GO 理论曲线假设目标 CUT 与训练窗背景满足指定随机模型；真实 compact statistical 场景有空间相关、四方向最大值、目标旁瓣/泄漏和局部非均匀杂波。
2. 配对输出 SCNR 是每个局部几何和随机实现的测量值，当前中心 0° 的 −35 dB 档中位数甚至为负值；这表示相对于同一 C+N 局部参考，目标增量在该次实现中不足以形成正的净功率，不应被重命名为输入 SNR。
3. 单元级、目标级、航迹级的事件定义不同。聚类 `min_points=6`、3–5 单元 +20 dB 恢复、目标选择、定位器和 TrackManager 会逐层减少事件数；因此用单元级理论直接套目标/航迹曲线会高估 Pd。
4. 当前正式主实验只有一个 seed；曲线上的二项误差棒仍很宽。旧的三 seed S+C+N 输出是在真值 Doppler 修复前产生，边缘归因不再作为最终定量证据。

![检测链分层计数。单元、目标、严格航迹三列不是同一个事件。](figures/06_detection_layer_funnel.png)

# 6. 限制、需外部确认的问题和下一步

本轮不擅自修改理论曲线去迎合实测，也不擅自调阈值。仍需在网页版查证/确认的理论问题是：在四方向训练带取最大值、角点相关且目标可能污染训练窗时，GO-CFAR 的闭式或高精度近似 Pd 应采用哪一种相关模型；如果要把目标级/航迹级曲线写成可外推的理论式，还需确认 clutter 非 IID、cluster selection 和 TrackManager association 的统计假设。当前报告把这些作为明确限制，而不是假装已经解决。

建议的下一轮只做一件事：在当前修复代码上把同一 6 场景扩展到 3 或 5 个 seed，并沿用本报告的实测输出 SCNR 横轴；若单元级曲线仍偏离 GO 理论，再单独开展 GO 训练窗相关/目标泄漏模型校准，不同时修改算法阈值和理论模型。

# 7. 复现入口与产物

最短复现入口：

```bash
cmake --build build -j4
ctest --test-dir build --output-on-failure
python3 scripts/gmti_scnr_eval/10_run_real_scnr_angle_cases.py \\
  --output-root outputs/gmti_scnr_eval/formal/spluscn_edge_fix_seed20260826 \\
  --seed 20260826 --angles 0,30,45 --edge-offset-deg 0.8 \\
  --scnr-grid=-45,-40,-35,-30,-25,-20,-15 --pfa 1e-6 \\
  --min-points 6 --small-min-points 3 --small-peak-db 20
```

本补充使用的证据目录：

* 当前 1-seed S+C+N：`outputs/gmti_scnr_eval/formal/spluscn_edge_fix_seed20260826/`
* 当前 pfa=1e-6 配对校准：`outputs/gmti_scnr_eval/formal_calibration_pfa1e6_v2/`
* 图表、合并 CSV、机器摘要：`__WORK_DIR__/`
* 母报告（前 22 页保持不变，后接本补充）：`__MOTHER_PDF__`

本 PDF 的结论范围严格限于上述当前源码、配置、输入、GPU 和实际运行证据。
'''
    report = report.replace("__ALPHA__", f"{theory['alpha']:.10f}")
    report = report.replace("__PFA_MC__", f"{theory['pfa_mc']:.3g}")
    report = report.replace("__PFA_SE__", f"{theory['pfa_se']:.2g}")
    report = report.replace("__CALIBRATION_TABLE__", markdown_table(
        ['真实方位(°)', '配置档(dB)', '输出 SCNR 中位数(dB)', 'P05', 'P95', '样本数', '独立 seed'], calibration_rows))
    report = report.replace("__CURRENT_TABLE__", markdown_table(
        ['场景', '配置档(dB)', '实测输出 SCNR(dB)', '单元级 Pd', '目标级 Pd', '严格航迹 Pd'], current_rows))
    report = report.replace("__ANGLE_TABLE__", markdown_table(
        ['场景', '配置档(dB)', '输出 SCNR(dB)', '关联数', 'RMSE(°)', '中位绝对误差(°)'], angle_table))
    report = report.replace("__WORK_DIR__", str(work))
    report = report.replace("__MOTHER_PDF__", str(args.mother_pdf.resolve()))
    # The template is no longer an f-string; restore ordinary TeX grouping
    # after the former doubled-brace escaping used during initial drafting.
    report = report.replace("{{", "{").replace("}}", "}")
    md = work / "仿真数据补充报告_20260826.md"
    md.write_text(report, encoding="utf-8")

    # 由调用者在同一工作目录运行 pandoc；这里仅保证输入和摘要完整。
    print(json.dumps({"markdown": str(md), "work_dir": str(work), "supplement_pdf": str(args.supplement_pdf.resolve()), "summary": str(work / "summary.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
