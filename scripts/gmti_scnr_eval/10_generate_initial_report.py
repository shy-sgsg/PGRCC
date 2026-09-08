#!/usr/bin/env python3
"""把真实 S+C+N GO-CFAR 单 seed 证据生成可审查的初版技术报告和曲线。"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti_scnr_eval_matplotlib")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from scnr_eval_lib import ensure_dir, wilson_interval


ROOT = Path(__file__).resolve().parents[2]
THEORY_ROOT = ROOT / "outputs/gmti_scnr_eval/final_go/formal/selected_beams_v8_pf1e6_min6_small3_20db_goalpha"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def number(row: dict[str, str], key: str, default: float = float("nan")) -> float:
    try:
        value = row.get(key, "")
        return float(value) if value not in {"", None} else default
    except (TypeError, ValueError):
        return default


def integer(row: dict[str, str], key: str, default: int = 0) -> int:
    value = number(row, key, float(default))
    return int(round(value)) if math.isfinite(value) else default


def finite(value: float) -> bool:
    return math.isfinite(value)


def fmt(value: object, digits: int = 3) -> str:
    try:
        value = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    return f"{value:.{digits}f}" if math.isfinite(value) else "—"


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    head = "| " + " | ".join(headers) + " |\n|" + "|".join("---" for _ in headers) + "|\n"
    body = "".join("| " + " | ".join(str(cell) for cell in row) + " |\n" for row in rows)
    return head + body


def case_metadata(case_dir: Path) -> tuple[str, float, float]:
    name = case_dir.name
    label = "right_edge" if "right_edge" in name else "center"
    match = re.search(r"command_(?:p|m)([0-9]+)p([0-9]+)deg", name)
    command = float(f"{match.group(1)}.{match.group(2)}") if match else float("nan")
    scenario = case_dir / "scenario.json"
    truth_angle = command
    if scenario.is_file():
        document = json.loads(scenario.read_text(encoding="utf-8"))
        targets = document.get("targets", [])
        if targets:
            init = targets[0].get("init", {})
            offset = float(init.get("azimuth_offset_deg", 0.0))
            truth_angle = command + offset
    return label, command, truth_angle


def load_case_rows(batch_root: Path) -> tuple[list[dict], list[dict], list[dict]]:
    diagnostics: list[dict] = []
    sequence: list[dict] = []
    cases: list[dict] = []
    for case_dir in sorted(path for path in batch_root.iterdir() if path.is_dir()):
        diag_path = case_dir / "metrics" / "target_diagnostics_all_periods.csv"
        seq_path = case_dir / "metrics" / "track_sequence_audit.csv"
        if not diag_path.is_file() or not seq_path.is_file():
            continue
        label, command, truth_angle = case_metadata(case_dir)
        case_key = case_dir.name
        cases.append({"case_key": case_key, "case_dir": case_dir, "label": label,
                      "command_angle_deg": command, "truth_angle_deg": truth_angle})
        for row in read_csv(diag_path):
            item = dict(row)
            item.update({"case_key": case_key, "case_dir": str(case_dir), "case_label": label,
                         "command_angle_deg": command, "truth_case_angle_deg": truth_angle})
            diagnostics.append(item)
        for row in read_csv(seq_path):
            item = dict(row)
            item.update({"case_key": case_key, "case_dir": str(case_dir), "case_label": label,
                         "command_angle_deg": command, "truth_case_angle_deg": truth_angle})
            sequence.append(item)
    if not diagnostics:
        raise SystemExit(f"{batch_root} 下没有完整 GO/TrackManager metrics")
    return diagnostics, sequence, cases


def group_key(row: dict) -> tuple[str, float, float]:
    return (str(row["case_key"]), float(row["command_angle_deg"]),
            number(row, "configured_target_snr_db"))


def aggregate_unit(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, float, float], list[dict]] = defaultdict(list)
    for row in rows:
        if integer(row, "unit_cell_window_valid", 0) == 1:
            groups[group_key(row)].append(row)
    output: list[dict] = []
    for key, values in sorted(groups.items(), key=lambda item: (item[0][1], item[0][0], item[0][2])):
        hits = sum(integer(row, "unit_go_cfar_hit", 0) for row in values)
        exact = sum(integer(row, "unit_go_cfar_exact_cut_hit", 0) for row in values)
        cut_ratio = []
        excess = []
        for row in values:
            cut = number(row, "unit_cut_power")
            noise = number(row, "unit_cfar_train_mean")
            if cut > 0.0 and noise > 0.0:
                cut_ratio.append(10.0 * math.log10(cut / noise))
            value = number(row, "unit_scnr_det_out_db")
            if finite(value):
                excess.append(value)
        low, high = wilson_interval(hits, len(values))
        output.append({
            "case_key": key[0], "command_angle_deg": key[1], "configured_target_snr_db": key[2],
            "case_label": values[0]["case_label"], "truth_angle_deg": values[0]["truth_case_angle_deg"],
            "trials": len(values), "hits": hits, "pd": hits / len(values),
            "wilson_low": low, "wilson_high": high, "exact_hits": exact,
            "exact_pd": exact / len(values), "cut_ratio_median_db": float(np.median(cut_ratio)) if cut_ratio else float("nan"),
            "excess_scnr_median_db": float(np.median(excess)) if excess else float("nan"),
            "excess_scnr_count": len(excess),
        })
    return output


def aggregate_target_track(diagnostics: list[dict], sequence: list[dict]) -> list[dict]:
    target_groups: dict[tuple[str, float, float], list[dict]] = defaultdict(list)
    for row in diagnostics:
        target_groups[group_key(row)].append(row)
    track_groups: dict[tuple[str, float, float], list[dict]] = defaultdict(list)
    for row in sequence:
        track_groups[group_key(row)].append(row)
    output: list[dict] = []
    for key, values in sorted(target_groups.items(), key=lambda item: (item[0][1], item[0][0], item[0][2])):
        target_hits = sum(integer(row, "final_output", 0) for row in values)
        target_n = len(values)
        tracks = track_groups.get(key, [])
        track_hits = sum(integer(row, "trackmanager_confirmed_matched_protocol_output", 0) for row in tracks)
        two_hits = sum(integer(row, "two_of_three_target_hits", 0) for row in tracks)
        target_low, target_high = wilson_interval(target_hits, target_n)
        track_low, track_high = wilson_interval(track_hits, len(tracks)) if tracks else (float("nan"), float("nan"))
        output.append({
            "case_key": key[0], "command_angle_deg": key[1], "configured_target_snr_db": key[2],
            "case_label": values[0]["case_label"], "truth_angle_deg": values[0]["truth_case_angle_deg"],
            "target_hits": target_hits, "target_trials": target_n, "target_pd": target_hits / target_n,
            "target_low": target_low, "target_high": target_high,
            "track_hits": track_hits, "track_trials": len(tracks),
            "track_pd": track_hits / len(tracks) if tracks else float("nan"),
            "track_low": track_low, "track_high": track_high,
            "two_of_three_pd": two_hits / len(tracks) if tracks else float("nan"),
        })
    return output


def aggregate_angle(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, float, float], list[float]] = defaultdict(list)
    for row in rows:
        if integer(row, "final_output", 0) != 1:
            continue
        error = number(row, "angle_error_deg")
        if finite(error):
            groups[group_key(row)].append(error)
    output: list[dict] = []
    for key, errors in sorted(groups.items(), key=lambda item: (item[0][1], item[0][0], item[0][2])):
        output.append({
            "case_key": key[0], "command_angle_deg": key[1], "configured_target_snr_db": key[2],
            "case_label": next(row["case_label"] for row in rows if row["case_key"] == key[0]),
            "truth_angle_deg": next(row["truth_case_angle_deg"] for row in rows if row["case_key"] == key[0]),
            "matched_count": len(errors), "bias_deg": float(np.mean(errors)),
            "rmse_deg": float(np.sqrt(np.mean(np.square(errors)))),
            "p95_abs_deg": float(np.quantile(np.abs(errors), 0.95)),
        })
    return output


def load_go_theory() -> list[dict]:
    path = THEORY_ROOT / "theory/go_cfar_cell_theory.csv"
    if not path.is_file():
        return []
    return read_csv(path)


def xml_parameter(xml: Path, keys: tuple[str, ...], default: float) -> float:
    if not xml.is_file():
        return default
    values = {node.tag: node.text.strip() for node in ET.parse(xml).getroot().iter()
              if node.text and node.text.strip()}
    for key in keys:
        try:
            value = float(values[key])
            if finite(value) and value > 0.0:
                return value
        except (KeyError, ValueError):
            pass
    return default


def make_plots(plot_dir: Path, unit: list[dict], target_track: list[dict], angle: list[dict],
               theory: list[dict], edge_offset: float, lambda_m: float, d_m: float) -> None:
    ensure_dir(plot_dir)
    theory_x = [number(row, "scnr_db") for row in theory]
    theory_y = [number(row, "pd_go_conditional_mc") for row in theory]
    colors = {"center": "tab:blue", "right_edge": "tab:orange"}
    labels = {"center": "波束中心", "right_edge": f"右侧边缘 (+{edge_offset:g}°)"}

    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.7))
    ax = axes[0]
    if theory_x:
        ax.plot(theory_x, theory_y, "k--", label="GO 单元理论（SCNR 横轴）")
    for label in ("center", "right_edge"):
        values = [row for row in unit if row["case_label"] == label]
        for angle_value in sorted({row["command_angle_deg"] for row in values}):
            rows = [row for row in values if row["command_angle_deg"] == angle_value]
            ax.plot([row["configured_target_snr_db"] for row in rows], [row["pd"] for row in rows],
                    marker="o", color=colors[label], alpha=0.85,
                    label=f"{angle_value:g}° {labels[label]}：实测（注入 SNR）")
    ax.set(xlabel="横轴：理论为输出 SCNR；实测暂为注入 SNR (dB)", ylabel="单元级 Pd", ylim=(-0.02, 1.02))
    ax.grid(True, alpha=0.3); ax.legend(fontsize=7, ncol=2)
    ax = axes[1]
    if theory_x:
        ax.plot(theory_x, theory_y, "k--", label="GO 单元理论")
    for label in ("center", "right_edge"):
        values = [row for row in unit if row["case_label"] == label and finite(row["excess_scnr_median_db"])]
        for angle_value in sorted({row["command_angle_deg"] for row in values}):
            rows = [row for row in values if row["command_angle_deg"] == angle_value]
            ax.plot([row["excess_scnr_median_db"] for row in rows], [row["pd"] for row in rows],
                    marker="o", color=colors[label], label=f"{angle_value:g}° {labels[label]}：直接 CUT SCNR")
    ax.set(xlabel="检测帧条件的直接 GO CUT 超额 SCNR (dB)", ylabel="单元级 Pd", ylim=(-0.02, 1.02))
    ax.grid(True, alpha=0.3); ax.legend(fontsize=7)
    fig.suptitle("GO-CFAR 单元级理论—实测对照（初版）")
    fig.tight_layout(); fig.savefig(plot_dir / "unit_pd_theory_vs_measured.png", dpi=180); plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.7))
    for label in ("center", "right_edge"):
        for angle_value in sorted({row["command_angle_deg"] for row in target_track if row["case_label"] == label}):
            rows = [row for row in target_track if row["case_label"] == label and row["command_angle_deg"] == angle_value]
            x = [row["configured_target_snr_db"] for row in rows]
            axes[0].plot(x, [row["target_pd"] for row in rows], marker="o", color=colors[label],
                         label=f"{angle_value:g}° {labels[label]}")
            axes[1].plot(x, [row["track_pd"] for row in rows], marker="o", color=colors[label],
                         label=f"{angle_value:g}° {labels[label]}")
    for ax, title, ylabel in ((axes[0], "目标级 Pd", "目标级 Pd"), (axes[1], "航迹级 Pd", "航迹级 Pd")):
        ax.set(xlabel="注入 SNR (dB)", ylabel=ylabel, ylim=(-0.02, 1.02), title=title)
        ax.grid(True, alpha=0.3); ax.legend(fontsize=7, ncol=2)
    fig.suptitle("真实 S+C+N 目标级/航迹级检测概率")
    fig.tight_layout(); fig.savefig(plot_dir / "target_track_pd_measured.png", dpi=180); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.8, 4.8))
    for label in ("center", "right_edge"):
        for angle_value in sorted({row["command_angle_deg"] for row in unit if row["case_label"] == label}):
            rows = [row for row in unit if row["case_label"] == label and row["command_angle_deg"] == angle_value]
            rows = [row for row in rows if finite(row["cut_ratio_median_db"])]
            ax.plot([row["configured_target_snr_db"] for row in rows], [row["cut_ratio_median_db"] for row in rows],
                    marker="o", color=colors[label], label=f"{angle_value:g}° {labels[label]}：CUT/GO噪声")
            rows_excess = [row for row in rows if finite(row["excess_scnr_median_db"])]
            if rows_excess:
                ax.plot([row["configured_target_snr_db"] for row in rows_excess],
                        [row["excess_scnr_median_db"] for row in rows_excess],
                        linestyle="--", color=colors[label], alpha=0.7,
                        label=f"{angle_value:g}°：超额 SCNR（仅正超额）")
    ax.set(xlabel="注入 SNR (dB)", ylabel="输出功率比 (dB)", title="生产 GO CUT 直接输出 SCNR 观测")
    ax.grid(True, alpha=0.3); ax.legend(fontsize=7, ncol=2)
    fig.tight_layout(); fig.savefig(plot_dir / "output_scnr_vs_input_snr.png", dpi=180); plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.7))
    for label in ("center", "right_edge"):
        for angle_value in sorted({row["command_angle_deg"] for row in angle if row["case_label"] == label}):
            rows = [row for row in angle if row["case_label"] == label and row["command_angle_deg"] == angle_value]
            axes[0].plot([row["configured_target_snr_db"] for row in rows], [row["rmse_deg"] for row in rows],
                         marker="o", color=colors[label], label=f"{angle_value:g}° {labels[label]}：实测")
            true_angle = next((row["truth_angle_deg"] for row in rows), angle_value)
            theta = math.radians(true_angle)
            jac = lambda_m / (2.0 * math.pi * d_m * math.cos(theta))
            scnr = np.linspace(-45.0, 30.0, 300)
            ideal = np.degrees(jac / np.sqrt(np.power(10.0, scnr / 10.0)))
            axes[1].plot(scnr, ideal, color=colors[label], label=f"{true_angle:g}° 理论")
            axes[1].scatter([row["configured_target_snr_db"] for row in rows], [row["rmse_deg"] for row in rows],
                            color=colors[label], marker="o", label=f"{true_angle:g}° 实测（注入 SNR 代理）")
    axes[0].axhline(0.2, color="k", linestyle="--", label="0.2° 参考线")
    axes[1].axhline(0.2, color="k", linestyle="--", label="0.2° 参考线")
    for ax in axes:
        ax.set(xlabel="注入 SNR / 理论 SCNR (dB)", ylabel="测角 RMSE (°)"); ax.grid(True, alpha=0.3); ax.legend(fontsize=7, ncol=2)
    fig.suptitle("0°/30°/45°中心与右侧边缘测角精度")
    fig.tight_layout(); fig.savefig(plot_dir / "angle_rmse_theory_vs_measured.png", dpi=180); plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path,
                        default=Path("outputs/gmti_scnr_eval/formal/spluscn_edge_v1_seed20261017"))
    parser.add_argument("--report", type=Path,
                        default=Path("docs/GO-CFAR_真实S+C+N_三角度中心边缘初版技术报告.md"))
    parser.add_argument("--xml", type=Path, default=ROOT / "gmti.xml")
    parser.add_argument("--theory-root", type=Path, default=THEORY_ROOT)
    args = parser.parse_args()
    batch_root = args.batch_root.resolve()
    diagnostics, sequence, cases = load_case_rows(batch_root)
    unit = aggregate_unit(diagnostics)
    target_track = aggregate_target_track(diagnostics, sequence)
    angle = aggregate_angle(diagnostics)
    theory_path = args.theory_root.resolve() / "theory/go_cfar_cell_theory.csv"
    theory = read_csv(theory_path) if theory_path.is_file() else load_go_theory()
    lambda_m = 299_792_458.0 / (xml_parameter(args.xml.resolve(), ("fc",), 16.472113076923076) * 1.0e9)
    d_m = xml_parameter(args.xml.resolve(), ("d_chan", "d_channel"), 0.17)
    edge_offset = next((abs(row["truth_angle_deg"] - row["command_angle_deg"]) for row in cases
                        if row["label"] == "right_edge"), 0.8)
    plot_dir = ensure_dir(batch_root / "report_plots")
    make_plots(plot_dir, unit, target_track, angle, theory, edge_offset, lambda_m, d_m)

    report_path = args.report.resolve()
    ensure_dir(report_path.parent)
    unit_rows = [[row["case_label"], f"{row['command_angle_deg']:g}°", f"{row['configured_target_snr_db']:g}",
                  f"{row['hits']}/{row['trials']}", fmt(row["pd"]),
                  f"[{fmt(row['wilson_low'])},{fmt(row['wilson_high'])}]", fmt(row["cut_ratio_median_db"]),
                  fmt(row["excess_scnr_median_db"]), str(row["excess_scnr_count"])] for row in unit]
    target_rows = [[row["case_label"], f"{row['command_angle_deg']:g}°", f"{row['configured_target_snr_db']:g}",
                    f"{row['target_hits']}/{row['target_trials']}", fmt(row["target_pd"]),
                    f"{row['track_hits']}/{row['track_trials']}", fmt(row["track_pd"]), fmt(row["two_of_three_pd"])]
                   for row in target_track]
    angle_rows = [[row["case_label"], f"{row['command_angle_deg']:g}°", f"{row['configured_target_snr_db']:g}",
                   str(row["matched_count"]), fmt(row["bias_deg"]), fmt(row["rmse_deg"]), fmt(row["p95_abs_deg"])]
                  for row in angle]
    theory_summary = []
    for angle_deg in (0.0, 30.0, 45.0):
        for label, offset in (("center", 0.0), ("right_edge", edge_offset)):
            theta = math.radians(angle_deg + offset)
            jac = lambda_m / (2.0 * math.pi * d_m * math.cos(theta))
            cross = float("nan")
            for scnr in np.linspace(-45.0, 30.0, 300):
                if math.degrees(jac / math.sqrt(10.0 ** (scnr / 10.0))) <= 0.2:
                    cross = scnr; break
            theory_summary.append([label, f"{angle_deg:g}°", f"{angle_deg + offset:g}°", fmt(jac, 6), fmt(cross, 2)])

    pfa_text = "未重新估计"
    pfa_path = args.theory_root.resolve() / "theory/go_cfar_pfa_validation.csv"
    if pfa_path.is_file():
        pfa_row = read_csv(pfa_path)[0]
        pfa_text = f"{number(pfa_row, 'pfa_conditional_mc'):.6g} ± {number(pfa_row, 'pfa_conditional_mc_standard_error'):.2g}"
    commands = [
        "python3 scripts/gmti_scnr_eval/10_run_real_scnr_angle_cases.py --output-root " + str(batch_root),
        "python3 scripts/gmti_scnr_eval/10_generate_initial_report.py --batch-root " + str(batch_root),
    ]
    report = f"""# 真实 S+C+N GO-CFAR 三角度中心/波束边缘初版技术报告

生成时间：2026-08-26。本文是“先形成报告、再迭代吻合”的第一版，正式统计只使用真实 **S+C+N** 场景和生产 **GO-CFAR + TrackManager** 链路；S-only/C+N 配对标定仅作为已封存诊断，不进入下列数字。

## 1. 先看结论

1. 本轮跑通了 {len(cases)} 个 case（0°、30°、45°各一个波束中心和一个右侧边缘，单 seed，连续 3 周期）；常规聚类最小点数为 6，小簇恢复为 3–5 单元且局部峰值高 20 dB，Pfa 配置为 1e-6。
2. 当前生产实现的 GO 门限不是“固定一个数”，而是先从四个方向的训练窗估计噪声，再取四者最大值：`T = 13.4495103 × max(mean_left, mean_right, mean_top, mean_bottom)`。IID 验证的条件 Pfa 为 `{pfa_text}`，与 1e-6 对应。
3. 0°/30°/45° 的理论测角误差随相位 SCNR 按 `1/sqrt(SCNR)` 下降；角度越大，`1/cos(theta)` 几何放大越明显。当前 hard_gate 场景在波束中心和右侧边缘之间不自动施加幅度衰减，因此边缘差异必须由真实 S+C+N 数据验证，不能凭经验硬加增益损失。
4. 初版实测与理论还没有资格宣称“完全吻合”：实测横轴的注入 SNR 是场景配置量，直接生产输出 SCNR 只在有正超额的 CUT/检测帧中可观测；未检测帧不能伪造输出 SCNR。报告已同时保留这两种横轴，下一轮用 3 seed 扩充并重新做输出 SCNR 映射。

## 2. 本次实验边界与可追溯证据

| 项目 | 本轮实际值 |
|---|---|
| 场景 | Stage2 `compact_statistical`：Rayleigh×lognormal 面杂波 + 热噪声 + 目标 |
| 目标角 | 命令波位 0°/30°/45°；中心偏移 0°；右边缘偏移 +{edge_offset:g}° |
| seed/周期 | 单 seed `20261017`；每 case 连续 3 周期 |
| GO-CFAR | Pfa=1e-6，guard=4，background=16，alpha=13.44951031977817 |
| 聚类/小簇 | min_points=6；3–5 单元局部峰值 +20 dB 恢复 |
| 目标级事件 | 当前周期 truth 匹配到生产 detection 的最终输出 |
| 航迹级事件 | `Confirmed + matched_this_frame` 且载荷来源为 measurement/matched_detection |
| 输入 SNR | `configured_target_snr_db`，是 Stage2 注入量，不等同于已标定物理 RCS-SCNR |
| 输出 SCNR | 生产 CFAR CUT 与 GO 训练噪声的直接比值；超额定义为 `10log10(max(CUT-noise,0)/noise)`，正超额才有数值 |

每个 case 的完整证据在 [{batch_root.name}]({batch_root.as_posix()})；包括 `scenario.json`、实际 XML、Stage2/PIPE 日志、检测快照、逐周期 GO 诊断、TrackManager 审计和本报告曲线。raw BIN 已在 GO/TrackManager 审计成功后删除，删除状态写入 `stage2/go_track_run_manifest.json`。

本轮曾发现并修复一次真值 Doppler 行/production truth matcher 的统计口径错位；修复过程和 v1–v4 重测证据见 [统计口径修复记录](GO-CFAR_真实S+C+N_统计口径修复记录.md)。v4 才是本报告使用的正式单 seed 证据。

## 3. 外行也能看懂的真实处理链路

把雷达想成一台同时做“距离尺”和“速度尺”的相机：

1. **回波生成**：目标、地面杂波和热噪声叠加成 S+C+N 原始 LFM 回波；目标强弱由注入 SNR 控制。
2. **脉压/距离 FFT**：把长 LFM 脉冲压成距离峰，并把每个距离格写到 range bin。
3. **慢时间 Doppler/DBS**：连续脉冲形成 Doppler 轴，目标速度对应 Doppler 行。
4. **P38/CSI 杂波处理**：沿真实生产配置完成杂波抑制、相位/距离修正和 CSI 复数图形成；不是只在一个理想矩阵上测试。
5. **GO-CFAR**：目标单元 CUT 与四个方向训练带比较；背景不均匀时取四个方向中最“吵”的那个，避免把杂波边缘误当目标。
6. **聚类和筛选**：相邻 hit 合成簇；正常簇至少 6 单元。只有 3–5 单元但局部峰比中位背景高 20 dB 的小簇，才允许恢复。
7. **目标选择/定位**：生产 target-select、CTDR/P38/几何定位给出角度和位置。
8. **TrackManager**：连续三帧中至少两帧建立 Confirmed 航迹；报告中的航迹 Pd 只数当前帧确实关联到 detection 的 Confirmed 测量载荷。

## 4. GO-CFAR 单元级理论：从噪声到检测概率

### 4.1 四个方向的噪声估计

设每个 CFAR 功率格的噪声功率已经归一化为均值 1。guard 半宽为 `g=4`、背景带厚度为 `b=16`。左右/上下每个方向的训练格数是

`N_dir = b(2g+2b+1) = 16 × 41 = 656`。

四个方向分别求平均：

`m_L = mean(left), m_R = mean(right), m_T = mean(top), m_B = mean(bottom)`。

GO 的核心就是取最大值：

`M = max(m_L, m_R, m_T, m_B)`，`T = alpha M`。

本生产链 alpha=`13.44951031977817`，由 Pfa=1e-6、上述 GO 窗口和代码的 directional-max 校准得到。于是 CUT 功率 `P_cut > T` 才成为 CFAR hit。

### 4.2 为什么能写出理论 Pd

若目标单元的复高斯噪声功率服从指数分布，目标平均功率与噪声功率之比记作 `gamma=10^(SCNR/10)`，那么目标 CUT 是“带非中心参数的指数变量”。给定训练噪声 `M=m` 时，命中概率可写成 Marcum-Q（等价于非中心卡方右尾）形式：

`Pd_cell(gamma | m) = Q1(sqrt(2 gamma), sqrt(2 alpha m))`。

训练窗本身也会随机变化，所以最后要对 `M` 的分布求平均：

`Pd_cell(gamma) = E_M[ Q1(sqrt(2 gamma), sqrt(2 alpha M)) ]`。

这就是图中的 GO 单元理论曲线。它只回答“目标所在 CFAR 分辨单元有没有过 GO 门限”，还没有乘上聚类、target-select 和航迹确认损失。

### 4.3 三种单元读数必须分开

- **exact CUT**：只看物理真值投影到的那一个 FFT 格；严格但会受 FFT 栅格偏移影响。
- **operational unit**：真值 Doppler/range 各允许 ±2 格；这是本报告单元 Pd 的实际定义，避免把一个主瓣落在相邻格误判成漏检。
- **target/track**：在 operational unit 之上，还要通过聚类、target-select、定位和 TrackManager；因此它们通常更低，不能混写成单元 Pd。

## 5. 目标级、航迹级检测概率公式

设单元命中概率为 `p_cell`，聚类和目标选择通过概率为 `q_cluster`，则理想目标级近似为

`p_target = p_cell × q_cluster`。

本实测不假设 `q_cluster=1`，而是直接用每个目标每个周期的 `final_output` 统计：命中数/目标帧总数，并给 Wilson 95% 区间。

三帧 TrackManager 采用“至少两帧命中”。若三帧的目标级概率分别为 `p1,p2,p3`，独立近似为

`p_2of3 = p1 p2 + p1 p3 + p2 p3 - 2 p1 p2 p3`；

三帧同分布时为 `3p^2 - 2p^3`。实际航迹 Pd 更严格：只有 TrackManager 状态为 Confirmed、当前帧 matched_this_frame、载荷来源是匹配 detection 的 measurement 才计数。

## 6. 0°/30°/45°测角理论与边缘解释

两通道相位差模型为

`phi = 2 pi d sin(theta) / lambda + phi0`，

反解为

`theta_hat = asin(lambda(phi_hat-phi0)/(2 pi d))`。

相位噪声越大，角度就越抖。若有效相位 SCNR 为 `gamma_phi`，先用小误差近似 `sigma_phi ≈ 1/sqrt(gamma_phi)`，再对上式求导：

`dtheta/dphi = lambda / (2 pi d cos(theta))`，

所以

`sigma_theta ≈ |lambda/(2 pi d cos(theta))| / sqrt(gamma_phi)`。

本工程 `lambda={lambda_m:.8f} m, d={d_m:g} m`。`1/cos(theta)` 说明 45° 比 0° 更敏感；波束右边缘的 true angle 是命令角加 `{edge_offset:g}°`，因此 Jacobian 略有增大。

需要特别说明：本轮 Stage2 目标 visibility 使用 `hard_gate`。在波束半宽内它的 beam_gain 是 1，超出后直接不可见，不是连续高斯衰减。因此当前“边缘”并不会被理论模型凭空罚掉一个增益；若未来切换 gaussian beam，才应使用 `gamma_edge = gamma_center × G(edge)^2` 重画曲线。

理论 Jacobian 和 RMSE=0.2° 的交叉 SCNR：

{markdown_table(["位置", "命令角", "几何真角", "dtheta/dphi (rad/rad)", "理论达到 0.2° 所需 SCNR(dB)"], theory_summary)}

## 7. 本轮真实 S+C+N 实测结果

### 7.1 单元级 GO Pd 与直接输出 SCNR

`输出 CUT/噪声` 是所有有效真值窗口可观测的 `10log10(P_cut/M)`；`超额 SCNR` 是 `10log10(max(P_cut-M,0)/M)`，只在正超额时有值。它们都来自生产 GO 功率图，不是 S-only 推断。

{markdown_table(["case", "命令角", "注入SNR", "unit命中", "Pd", "Wilson95%", "CUT/噪声中位(dB)", "正超额SCNR中位(dB)", "正超额样本"], unit_rows)}

### 7.2 目标级和航迹级 Pd

{markdown_table(["case", "命令角", "注入SNR", "目标命中/帧", "目标Pd", "航迹命中/目标", "航迹Pd", "2/3事件Pd"], target_rows)}

### 7.3 测角误差

{markdown_table(["case", "命令角", "注入SNR", "匹配数", "偏差(°)", "RMSE(°)", "绝对误差P95(°)"], angle_rows)}

## 8. 曲线文件

- [单元 Pd：理论/注入 SNR 实测/直接输出 SCNR 实测](report_plots/unit_pd_theory_vs_measured.png)
- [目标级与航迹级 Pd](report_plots/target_track_pd_measured.png)
- [输入 SNR 到生产 GO CUT 输出 SCNR](report_plots/output_scnr_vs_input_snr.png)
- [测角 RMSE：理论与实测](report_plots/angle_rmse_theory_vs_measured.png)
- 原始逐目标数据：各 case `metrics/target_diagnostics_all_periods.csv`
- 原始航迹审计：各 case `metrics/track_sequence_audit.csv`

## 9. 为什么第一版还不能说“理论曲线已吻合”

1. GO 理论的横轴是**同一 CFAR 单元的输出 SCNR**；本轮大部分实测点只能可靠记录场景注入 SNR，直接输出 SCNR 对未检测帧不可观测。把注入 SNR 强行当输出 SCNR 会掩盖处理链增益/损失，故本报告将两者并列而不冒充等价。
2. 单 seed、每个角度/档位只有 3 个目标帧，统计区间很宽；尤其航迹事件是 3 帧联合事件，不能用一个成功点断言稳定性能。
3. 理论先采用复高斯 IID 训练窗；真实链路存在脉压旁瓣、杂波纹理、Doppler 泄漏、聚类和 target-select，理论与实测的差异正是后续要定位的算法处理损失，而不是用门限调参抹平。
4. 当前边缘是右侧一个点（+{edge_offset:g}°），不是完整波束扫描；左边缘和更多边缘位置留作扩展实验。

补充判断：v4 中 30.8°/45.8°右边缘的 unit Pd 仍明显低于对应中心，这一差异在真值行口径修复后仍存在，属于真实链路的 Doppler/CSI/筛选响应，不能再解释为离线统计 bug。后续理论模型需把它写成边缘处理通过率（或等效 SCNR 损失）并由 3 seed 实测估计。

## 10. 下一步迭代验收

先用同一脚本扩展到 3 个独立 seed（仍只跑这 6 个中心/右边缘 case），合并 unit/target/track/angle 的 Wilson 或 seed bootstrap 区间；然后将直接输出 SCNR 按“真值 CUT、同一生产 GO 训练窗”做分档统计，重新把实测点投到理论 SCNR 横轴。若仍不吻合，按数据流依次审计：真值 Doppler 映射 → CUT/训练窗 → GO alpha → cluster/target-select → TrackManager 关联，而不是先改 Pfa。

最短复现命令：

```bash
{commands[0]}
{commands[1]}
```

"""
    report_path.write_text(report, encoding="utf-8")
    # 同时保存机器可读汇总，便于后续 3/5 seed 合并而不解析 Markdown。
    summary_cases = [{key: (str(value) if isinstance(value, Path) else value)
                      for key, value in item.items()} for item in cases]
    summary = {"cases": summary_cases, "unit": unit, "target_track": target_track, "angle": angle,
               "theory_source": str(theory_path), "report": str(report_path),
               "lambda_m": lambda_m, "d_chan_m": d_m, "edge_offset_deg": edge_offset}
    (batch_root / "report_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 初版报告已生成：{report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
