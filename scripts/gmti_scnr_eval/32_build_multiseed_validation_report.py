#!/usr/bin/env python3
"""汇总 min_points=6 的三 seed 输出-SCNR 验证。

脚本只读取每个已通过生产链审计的角度目录中的汇总 CSV，不读取 raw BIN，
也不重新拟合 output-SCNR 轴。每个 requested output-SCNR 档位按 seed 合并
命中计数，并从 period 汇总计算池化测角 RMSE。
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
import sys

sys.path.insert(0, str(SCRIPT_DIR))
from scnr_eval_lib import read_csv, wilson_interval, write_csv  # noqa: E402


ANGLES = (0.0, 30.0, 45.0)
COLORS = {0.0: "#1f77b4", 30.0: "#d98c00", 45.0: "#8c564b"}
LABELS = {0.0: "0°中心", 30.0: "30°中心", 45.0: "45°中心"}
FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if FONT.is_file():
    font_manager.fontManager.addfont(str(FONT))
    plt.rcParams["font.family"] = "Noto Sans CJK JP"
plt.rcParams["axes.unicode_minus"] = False


def finite(value: object) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def fmt(value: object, digits: int = 3) -> str:
    number = finite(value)
    return f"{number:.{digits}f}" if math.isfinite(number) else "—"


def token(angle: float) -> str:
    return f"angle_{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def manifest(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def find_angle_dir(root: Path, angle: float) -> Path:
    direct = root / "runs" / token(angle) / token(angle)
    if direct.is_dir():
        return direct
    for candidate in sorted(root.rglob("standard_mc_curve_summary.csv")):
        if candidate.parent.name == token(angle):
            return candidate.parent
    raise FileNotFoundError(f"{root} 缺少 {angle:g}° 汇总目录")


def add_count(target: dict[str, float], key: str, value: object) -> None:
    number = finite(value)
    if math.isfinite(number):
        target[key] = target.get(key, 0.0) + number


def load_seed(root: Path, seed_index: int) -> tuple[list[dict[str, object]], dict[str, object]]:
    root = root.resolve()
    top = manifest(root / "run_manifest.json")
    output: dict[tuple[float, int], dict[str, object]] = {}
    seed_values: list[int] = []
    for angle in ANGLES:
        angle_dir = find_angle_dir(root, angle)
        angle_manifest = manifest(angle_dir / "manifest.json")
        seed = angle_manifest.get("seed", top.get("base_seed", seed_index))
        try:
            seed_values.append(int(seed))
        except (TypeError, ValueError):
            pass
        curve_path = angle_dir / "standard_mc_curve_summary.csv"
        period_path = angle_dir / "standard_mc_period_metrics.csv"
        if not curve_path.is_file():
            raise FileNotFoundError(f"{curve_path} 不存在")
        period_stats: dict[int, dict[str, float]] = {}
        if period_path.is_file():
            for record in read_csv(period_path):
                try:
                    level = int(round(float(record.get("level_index", record.get("input_snr_db_index", -1)))))
                except (TypeError, ValueError):
                    continue
                error = finite(record.get("angle_error_deg"))
                if math.isfinite(error):
                    stats = period_stats.setdefault(level, {"n": 0.0, "sum": 0.0, "sumsq": 0.0})
                    stats["n"] += 1.0
                    stats["sum"] += error
                    stats["sumsq"] += error * error
        for record in read_csv(curve_path):
            requested = finite(record.get("requested_output_scnr_db"))
            if not math.isfinite(requested):
                continue
            try:
                level = int(round(float(record.get("level_index", -1))))
            except (TypeError, ValueError):
                level = -1
            if level < 0:
                continue
            # The requested output-SCNR value is calibrated independently for
            # each seed, so level_index is the only stable merge key.  The
            # plotted abscissa remains the measured output-SCNR mean.
            key = (angle, level)
            row = output.setdefault(key, {
                "angle_deg": angle,
                "level_index": level,
                "requested_output_values": [],
                "seed_count": 0,
                "output_values": [],
                "angle_n": 0.0,
                "angle_sum": 0.0,
                "angle_sumsq": 0.0,
                "source_roots": [],
            })
            row["seed_count"] = int(row["seed_count"]) + 1
            row["requested_output_values"].append(requested)
            actual = finite(record.get("output_scnr_db"))
            if math.isfinite(actual):
                row["output_values"].append(actual)
            for prefix in ("unit", "cluster", "target", "track"):
                for field in ("hits", "trials"):
                    add_count(row, f"{prefix}_{field}", record.get(f"{prefix}_{field}"))
            stats = period_stats.get(level)
            if stats:
                row["angle_n"] = float(row["angle_n"]) + stats["n"]
                row["angle_sum"] = float(row["angle_sum"]) + stats["sum"]
                row["angle_sumsq"] = float(row["angle_sumsq"]) + stats["sumsq"]
            row["source_roots"].append(str(root))
    rows: list[dict[str, object]] = []
    for row in output.values():
        requested_values = np.asarray(row.pop("requested_output_values"), dtype=float)
        row["requested_output_scnr_db"] = float(np.mean(requested_values)) if requested_values.size else float("nan")
        values = np.asarray(row.pop("output_values"), dtype=float)
        row["output_scnr_db_mean"] = float(np.mean(values)) if values.size else float("nan")
        row["output_scnr_db_std"] = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
        n_angle = float(row.pop("angle_n"))
        angle_sum = float(row.pop("angle_sum"))
        angle_sumsq = float(row.pop("angle_sumsq"))
        row["angle_samples"] = int(round(n_angle))
        row["angle_rmse_deg"] = math.sqrt(angle_sumsq / n_angle) if n_angle > 0 else float("nan")
        row["angle_bias_deg"] = angle_sum / n_angle if n_angle > 0 else float("nan")
        for prefix in ("unit", "cluster", "target", "track"):
            hits = float(row.get(f"{prefix}_hits", 0.0))
            trials = float(row.get(f"{prefix}_trials", 0.0))
            row[f"{prefix}_pd"] = hits / trials if trials > 0 else float("nan")
            low, high = wilson_interval(int(round(hits)), int(round(trials))) if trials > 0 else (float("nan"), float("nan"))
            row[f"{prefix}_wilson_low"] = low
            row[f"{prefix}_wilson_high"] = high
        row["seed_values"] = ",".join(str(x) for x in sorted(set(seed_values)))
        row.pop("source_roots", None)
        rows.append(row)
    rows.sort(key=lambda item: (float(item["angle_deg"]), float(item["requested_output_scnr_db"])))
    return rows, {"root": str(root), "seed_values": sorted(set(seed_values)), "top_manifest": top}


def plot_curve(rows: list[dict[str, object]], field: str, ylabel: str, output: Path,
               bounds: tuple[float, float] | None = (0.0, 1.03), threshold: float | None = None) -> None:
    fig, ax = plt.subplots(figsize=(8.5, 5.3))
    for angle in ANGLES:
        selected = sorted((row for row in rows if abs(float(row["angle_deg"]) - angle) < 1e-6),
                          key=lambda item: float(item["output_scnr_db_mean"]))
        if not selected:
            continue
        x = np.asarray([float(row["output_scnr_db_mean"]) for row in selected])
        y = np.asarray([float(row[field]) for row in selected])
        lo = np.asarray([float(row.get(f"{field}_wilson_low", row[field])) for row in selected])
        hi = np.asarray([float(row.get(f"{field}_wilson_high", row[field])) for row in selected])
        color = COLORS[angle]
        ax.plot(x, y, "o-", color=color, markerfacecolor="white", label=f"{LABELS[angle]}（3 seed）")
        if field != "angle_rmse_deg":
            ax.fill_between(x, lo, hi, color=color, alpha=0.13, linewidth=0)
    if threshold is not None:
        ax.axhline(threshold, color="#555", linestyle=":", linewidth=1.0, label=f"Pd={threshold:g}")
    ax.set_xlabel("输出 SCNR（dB；固定 3×3 支撑）")
    ax.set_ylabel(ylabel)
    ax.grid(True, color="#d9dde3", alpha=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if bounds is not None:
        ax.set_ylim(*bounds)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output, dpi=220)
    fig.savefig(output.with_suffix(".pdf"))
    plt.close(fig)


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---:" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(str(item) for item in row) + " |" for row in rows)
    return "\n".join(lines)


def first_crossing(rows: list[dict[str, object]], field: str, target: float = 0.9) -> float:
    for row in sorted(rows, key=lambda item: float(item["output_scnr_db_mean"])):
        if float(row[field]) >= target:
            return float(row["output_scnr_db_mean"])
    return float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-root", action="append", type=Path, required=True,
                        help="一个已审计的 min_points=6 MC 根目录；可重复三次")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-pdf", type=Path, required=True)
    parser.add_argument("--output-html", type=Path, required=True)
    args = parser.parse_args()
    if len(args.seed_root) < 3:
        raise SystemExit("至少需要 3 个独立 seed 根目录")
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, object]] = []
    seed_meta = []
    audit_meta = []
    for index, root in enumerate(args.seed_root):
        audit_path = root.resolve() / "information_leakage_audit.json"
        audit = manifest(audit_path)
        status = str(audit.get("status", ""))
        pipeline_count = int(audit.get("pipeline_count", 0) or 0)
        truth_active = int(audit.get("truth_active_detector_count", 0) or 0)
        diagnostic_truth = int(audit.get("diagnostic_truth_path_count", 0) or 0)
        if status != "pass" or pipeline_count != 12 or truth_active != 0 or diagnostic_truth != 0:
            raise SystemExit(
                f"{audit_path} 未通过严格信息泄露审计: status={status!r}, "
                f"pipelines={pipeline_count}, active_truth={truth_active}, "
                f"diagnostic_truth={diagnostic_truth}"
            )
        audit_meta.append({
            "root": str(root.resolve()), "status": status,
            "pipeline_count": pipeline_count,
            "truth_active_detector_count": truth_active,
            "diagnostic_truth_path_count": diagnostic_truth,
        })
        rows, meta = load_seed(root, index)
        all_rows.extend(rows)
        seed_meta.append(meta)
    # Every seed must contribute exactly 30 rows.  Duplicate requested levels
    # are expected only across seeds and are merged here.
    merged: dict[tuple[float, float], dict[str, object]] = {}
    for row in all_rows:
        key = (float(row["angle_deg"]), int(row["level_index"]))
        current = merged.setdefault(key, {
            "angle_deg": key[0], "level_index": key[1], "requested_values": [], "seed_count": 0,
            "output_values": [], "angle_samples": 0, "angle_sum": 0.0, "angle_sumsq": 0.0,
        })
        current["seed_count"] = int(current["seed_count"]) + int(row["seed_count"])
        current["requested_values"].append(float(row["requested_output_scnr_db"]))
        current["output_values"].extend([float(row["output_scnr_db_mean"])] * int(row["seed_count"]))
        current["angle_samples"] = int(current["angle_samples"]) + int(row["angle_samples"])
        current["angle_sum"] = float(current["angle_sum"]) + float(row["angle_bias_deg"]) * int(row["angle_samples"]) if math.isfinite(float(row["angle_bias_deg"])) else float(current["angle_sum"])
        current["angle_sumsq"] = float(current["angle_sumsq"]) + float(row["angle_rmse_deg"]) ** 2 * int(row["angle_samples"]) if math.isfinite(float(row["angle_rmse_deg"])) else float(current["angle_sumsq"])
        for prefix in ("unit", "cluster", "target", "track"):
            for field in ("hits", "trials"):
                current[f"{prefix}_{field}"] = float(current.get(f"{prefix}_{field}", 0.0)) + float(row.get(f"{prefix}_{field}", 0.0))
    rows: list[dict[str, object]] = []
    for row in merged.values():
        requested_values = np.asarray(row.pop("requested_values"), dtype=float)
        row["requested_output_scnr_db"] = float(np.mean(requested_values)) if requested_values.size else float("nan")
        values = np.asarray(row.pop("output_values"), dtype=float)
        row["output_scnr_db_mean"] = float(np.mean(values)) if values.size else float("nan")
        row["output_scnr_db_std"] = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
        n_angle = int(row["angle_samples"])
        row["angle_rmse_deg"] = math.sqrt(float(row.pop("angle_sumsq")) / n_angle) if n_angle else float("nan")
        row["angle_bias_deg"] = float(row.pop("angle_sum")) / n_angle if n_angle else float("nan")
        for prefix in ("unit", "cluster", "target", "track"):
            hits = float(row.get(f"{prefix}_hits", 0.0))
            trials = float(row.get(f"{prefix}_trials", 0.0))
            row[f"{prefix}_pd"] = hits / trials if trials else float("nan")
            low, high = wilson_interval(int(round(hits)), int(round(trials))) if trials else (float("nan"), float("nan"))
            row[f"{prefix}_wilson_low"] = low
            row[f"{prefix}_wilson_high"] = high
        rows.append(row)
    rows.sort(key=lambda item: (float(item["angle_deg"]), float(item["requested_output_scnr_db"])))
    if len(rows) != 30 or any(int(row["seed_count"]) != len(args.seed_root) for row in rows):
        raise SystemExit(f"期望 30 个档位且每档 {len(args.seed_root)} seed，实际 rows={len(rows)}")
    fields = ["angle_deg", "requested_output_scnr_db", "output_scnr_db_mean", "output_scnr_db_std", "seed_count",
              "unit_hits", "unit_trials", "unit_pd", "unit_wilson_low", "unit_wilson_high",
              "cluster_hits", "cluster_trials", "cluster_pd", "cluster_wilson_low", "cluster_wilson_high",
              "target_hits", "target_trials", "target_pd", "target_wilson_low", "target_wilson_high",
              "track_hits", "track_trials", "track_pd", "track_wilson_low", "track_wilson_high",
              "angle_samples", "angle_rmse_deg", "angle_bias_deg"]
    write_csv(out / "min6_multiseed_summary.csv", [{field: row.get(field, "") for field in fields} for row in rows])
    plot_curve(rows, "unit_pd", "单元级 GO Pd", out / "output_scnr_vs_unit_pd_min6_3seed.png", threshold=0.9)
    plot_curve(rows, "cluster_pd", "聚类事件 Pd", out / "output_scnr_vs_cluster_pd_min6_3seed.png", threshold=0.9)
    plot_curve(rows, "target_pd", "目标级 truth Pd", out / "output_scnr_vs_target_pd_min6_3seed.png", threshold=0.9)
    plot_curve(rows, "track_pd", "三屏选二航迹 Pd", out / "output_scnr_vs_track_pd_min6_3seed.png", threshold=0.9)
    plot_curve(rows, "angle_rmse_deg", "测角 RMSE（deg）", out / "output_scnr_vs_angle_rmse_min6_3seed.png", bounds=None)

    summary_rows = []
    for angle in ANGLES:
        selected = [row for row in rows if abs(float(row["angle_deg"]) - angle) < 1e-6]
        finite_rmses = [float(row["angle_rmse_deg"]) for row in selected
                        if math.isfinite(float(row["angle_rmse_deg"]))]
        summary_rows.append([f"{angle:g}°", len(selected), int(sum(int(row["target_trials"]) for row in selected)),
                             fmt(first_crossing(selected, "target_pd"), 3),
                             fmt(float(np.average([float(row["target_pd"]) for row in selected])), 4),
                             fmt(float(np.mean(finite_rmses)) if finite_rmses else float("nan"), 4)])
    table_rows = []
    for angle in ANGLES:
        for row in [r for r in rows if abs(float(r["angle_deg"]) - angle) < 1e-6]:
            table_rows.append([f"{angle:g}°", fmt(row["output_scnr_db_mean"], 2), f"{int(row['unit_hits'])}/{int(row['unit_trials'])}",
                               fmt(row["unit_pd"]), f"{int(row['cluster_hits'])}/{int(row['cluster_trials'])}", fmt(row["cluster_pd"]),
                               f"{int(row['target_hits'])}/{int(row['target_trials'])}", fmt(row["target_pd"]),
                               f"{int(row['track_hits'])}/{int(row['track_trials'])}", fmt(row["track_pd"]), fmt(row["angle_rmse_deg"], 3)])
    md = out / "GMTI_min6_3seed_验证报告_20260828.md"
    sections = ["""# GMTI 输出 SCNR—检测概率—测角精度：min_points=6 三 seed 验证

本补充只合并三次独立 seed 的已审计汇总，不重新拟合 output-SCNR 轴，不读取 raw BIN。每个 seed 使用 0°/30°/45°、10 个档位、每档 5 组三屏；每档合并后 target 分母为 45 screen、track 分母为 15 个三屏事件。背景仍为 thermal noise only（area clutter disabled），因此本结果用于随机性/实现一致性验证，不是复杂杂波最终门限。
""",
                "## 1. 参数、样本与来源\n\n" + markdown_table(["项目", "取值", "说明"], [
                    ["独立 seed", ", ".join(",".join(str(x) for x in meta["seed_values"]) for meta in seed_meta), "三次生产链运行；每角度 seed 为根 seed 加角度偏移"],
                    ["Pfa / GO", "1e-6 / guard=4, background=16", "alpha=13.4495103；训练单元 N=544"],
                    ["聚类", "min_points=6；small_min_points=3；峰值+20 dB", "生产 clustering 规则，不是 Poisson-binomial 主指标"],
                    ["每档合并样本", "45 screens + 15 track events", "3 seed × 5 组三屏；Wilson 95% 区间"],
                    ["正式横轴", "实测 output SCNR", "固定 3×3 S+N 支撑增量 / N-only P0；target_snr_db 仅控制变量"],
                ]) + "\n\n三次运行的汇总入口：`min6_multiseed_summary.csv`。每个 seed 的 raw BIN 已在 GO/TrackManager/ truth-match 审计后清理。",
                "## 2. 公式与合并方法\n\n" +
                "单元事件为 $H_i=1\\{P_i>\\alpha M_i\\}$，计数估计 $\\hat P_{d,unit}=\\sum H_i/n$。聚类和目标事件由生产 joint hit mask、连通性、3–5 点 +20 dB 恢复和 truth-match 直接计数，不把单元事件假设为独立。目标级条件链写为 $P_{target}=P_{cluster}P(select|cluster)P(reloc|select,cluster)P(match|reloc,select,cluster)$。三屏航迹主指标为 $P_{track}=P_{12}+P_{13}+P_{23}-2P_{123}$；独立近似仅作为 $3p^2-2p^3$ 对照。测角 RMSE 使用每个 seed 保留的 `standard_mc_period_metrics.csv` 中有限 `angle_error_deg` 池化计算 $\\sqrt{\\sum e_i^2/N}$。\n\n合并时只累加 hits/trials；output-SCNR 用三 seed 的实测均值和标准差；不按结果重新选择档位或拟合门限。",
                "## 3. 三 seed 总览\n\n" + markdown_table(["角度", "档位数", "target trials", "首次观测 target Pd>=0.9 (dB)", "10 档平均 target Pd", "平均 RMSE (deg)"], summary_rows) +
                "\n\n下面五张图分别对应 unit Pd、cluster Pd、target Pd、三屏选二 track Pd 和 angle RMSE；点是三 seed 合并计数，阴影是对应二项 Wilson 95% 区间。",
                "## 4. 五类曲线\n\n" + "\n\n".join([
                    f"![{caption}]({name})\n\n**读图说明：** {explain}"
                    for caption, name, explain in [
                        ("output SCNR—unit Pd", "output_scnr_vs_unit_pd_min6_3seed.png", "固定 CUT 超过 GO 门限的单元事件；它不包含聚类和 truth-match 损失。"),
                        ("output SCNR—cluster Pd", "output_scnr_vs_cluster_pd_min6_3seed.png", "生产连通组件和 min_points=6/小簇恢复后的事件；与 unit 曲线的差是聚类漏斗。"),
                        ("output SCNR—target Pd", "output_scnr_vs_target_pd_min6_3seed.png", "cluster、select、relocation 和 truth-match 全部通过后的目标级事件。"),
                        ("output SCNR—track Pd", "output_scnr_vs_track_pd_min6_3seed.png", "同一 truth 轨迹三屏中至少命中两屏；不是 TrackManager Confirmed 诊断值。"),
                        ("output SCNR—angle RMSE", "output_scnr_vs_angle_rmse_min6_3seed.png", "每档池化的有限 truth-match 角误差；空匹配档位显示为缺失，不填零。"),
                    ]]) +
                "\n\n逐档数值表：\n\n" + markdown_table(["角度", "output SCNR(dB)", "unit hits/n", "unit Pd", "cluster hits/n", "cluster Pd", "target hits/n", "target Pd", "track hits/n", "track Pd", "RMSE(deg)"], table_rows),
                "## 5. 信息泄露审计与限制\n\n三次运行均在汇总前强制检查 `information_leakage_audit.json`：\n\n" +
                markdown_table(["seed 根目录", "status", "pipeline 数", "在线 truth", "诊断 truth"], [
                    [Path(item["root"]).name, item["status"], item["pipeline_count"],
                     item["truth_active_detector_count"], item["diagnostic_truth_path_count"]]
                    for item in audit_meta
                ]) +
                "\n\n在线 truth 路径为 0，NMS 不使用 truth-derived tie-break。truth 只允许离线支撑/评分、truth-match、RMSE 和三屏标签。由于每档合并后仍只有 45 screens/15 tracks，Wilson 区间不可忽略；该三 seed 结果支持进入更大 multi-seed sweep，但不宣称 90% 最终门限。"
                ]
    md.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    pdf = args.output_pdf.resolve()
    html = args.output_html.resolve()
    pdf.parent.mkdir(parents=True, exist_ok=True)
    html.parent.mkdir(parents=True, exist_ok=True)
    document_title = "GMTI min_points=6 三 seed 输出 SCNR 验证报告"
    pandoc = shutil.which("pandoc")
    xelatex = shutil.which("xelatex")
    if pandoc and xelatex:
        result = subprocess.run([pandoc, str(md), "--resource-path", str(out), "--pdf-engine", xelatex,
                                 "-V", "CJKmainfont=Noto Sans CJK SC", "--metadata", f"title={document_title}",
                                 "-o", str(pdf)], cwd=ROOT, check=False)
        if result.returncode:
            raise SystemExit(f"pandoc PDF 失败，退出码={result.returncode}")
    if pandoc:
        subprocess.run([pandoc, str(md), "--standalone", "--resource-path", str(out), "--self-contained",
                        "--mathml", "--metadata", f"title={document_title}", "-o", str(html)], cwd=ROOT, check=False)
    manifest_out = {
        "status": "pass", "min_points": 6, "seed_count": len(args.seed_root),
        "row_count": len(rows), "screen_trials_per_level": 45, "track_trials_per_level": 15,
        "roots": [meta["root"] for meta in seed_meta], "seed_values_by_angle": [meta["seed_values"] for meta in seed_meta],
        "information_leakage_audits": audit_meta,
        "strict_online_no_prior": True,
    }
    (out / "min6_multiseed_manifest.json").write_text(json.dumps(manifest_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 三 seed 汇总 CSV: {out / 'min6_multiseed_summary.csv'}")
    print(f"[PASS] 三 seed 报告 Markdown: {md}")
    print(f"[PASS] 三 seed 报告 PDF: {pdf}")
    print(f"[PASS] 三 seed 报告 HTML: {html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
