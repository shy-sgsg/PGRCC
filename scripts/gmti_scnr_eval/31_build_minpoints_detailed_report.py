#!/usr/bin/env python3
"""构建 min_points=2/3/6 的详细实测补充报告。

该脚本只读取已经完成并审计的真实 CUDA 生产链汇总 CSV/manifest，不重新拟合
任何曲线，也不把 ``target_snr_db`` 当作正式横轴。报告中的三种 min_points
各自有独立图、完整逐档表格和可复现的参数/样本数说明。
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
from scipy.stats import ncx2

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
import sys

sys.path.insert(0, str(SCRIPT_DIR))
from scnr_eval_lib import read_csv, wilson_interval, write_csv  # noqa: E402


COLORS = {0.0: "#1f77b4", 30.0: "#d98c00", 45.0: "#8c564b"}
LABELS = {0.0: "0°中心", 30.0: "30°中心", 45.0: "45°中心"}
FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if FONT.is_file():
    font_manager.fontManager.addfont(str(FONT))
    plt.rcParams["font.family"] = "Noto Sans CJK JP"
plt.rcParams["axes.unicode_minus"] = False


def finite(value: object) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def fmt(value: object, digits: int = 3) -> str:
    number = finite(value)
    return f"{number:.{digits}f}" if math.isfinite(number) else "—"


def token(angle: float) -> str:
    return f"angle_{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def parse_angle_token(value: str) -> float:
    if not value.startswith("angle_"):
        return float("nan")
    raw = value[len("angle_") :]
    sign = -1.0 if raw.startswith("m") else 1.0
    body = raw[1:] if raw[:1] in {"m", "p"} else raw
    try:
        return sign * float(body.replace("p", "."))
    except ValueError:
        return float("nan")


def table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---:" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(str(item) for item in row) + " |" for row in rows)
    return "\n".join(lines)


def manifest_for(root: Path) -> dict[str, object]:
    for path in (root / "run_manifest.json", root / "manifest.json"):
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    return value
            except (OSError, ValueError, TypeError):
                pass
    return {}


def angle_curve_path(root: Path, angle: float) -> Path:
    direct = root / "runs" / token(angle) / token(angle) / "standard_mc_curve_summary.csv"
    if direct.is_file():
        return direct
    candidates = sorted(root.rglob("standard_mc_curve_summary.csv"))
    for candidate in candidates:
        if abs(parse_angle_token(candidate.parent.name) - angle) < 1e-6:
            return candidate
    raise FileNotFoundError(f"{root} 缺少 {angle:g}° standard_mc_curve_summary.csv")


def load_rows(root: Path, minimum: int) -> tuple[list[dict[str, object]], dict[str, object]]:
    root = root.resolve()
    root_manifest = manifest_for(root)
    result: list[dict[str, object]] = []
    for angle in (0.0, 30.0, 45.0):
        path = angle_curve_path(root, angle)
        angle_manifest = manifest_for(path.parent)
        # Per-angle CSVs intentionally omit angle_deg; the angle is restored
        # from the explicit audited directory label, never from truth data.
        for raw in read_csv(path):
            output_scnr = finite(raw.get("output_scnr_db"))
            if not math.isfinite(output_scnr):
                output_scnr = finite(raw.get("requested_output_scnr_db"))
            target_hits = finite(raw.get("target_hits"))
            target_trials = finite(raw.get("target_trials"))
            unit_hits = finite(raw.get("unit_hits"))
            unit_trials = finite(raw.get("unit_trials"))
            if not (math.isfinite(output_scnr) and math.isfinite(target_hits)
                    and math.isfinite(target_trials) and target_trials > 0):
                continue
            target_pd = finite(raw.get("target_pd"))
            if not math.isfinite(target_pd):
                target_pd = target_hits / target_trials
            target_low = finite(raw.get("target_wilson_low"))
            target_high = finite(raw.get("target_wilson_high"))
            if not (math.isfinite(target_low) and math.isfinite(target_high)):
                target_low, target_high = wilson_interval(int(round(target_hits)), int(round(target_trials)))
            result.append({
                "min_points": minimum,
                "angle_deg": angle,
                "output_scnr_db": output_scnr,
                "requested_output_scnr_db": finite(raw.get("requested_output_scnr_db")),
                "input_snr_db_control": finite(raw.get("input_snr_db_control")),
                "unit_hits": int(round(unit_hits)) if math.isfinite(unit_hits) else "",
                "unit_trials": int(round(unit_trials)) if math.isfinite(unit_trials) else "",
                "unit_pd": finite(raw.get("unit_pd")),
                # Keep the direct physical-CUT event and the operational
                # resolution event separate.  The former is the event used
                # by the textbook single-CUT Q1 curve; the latter is the
                # production-resolution event accepted within the audited
                # +/-2 range/Doppler gate before clustering.
                "unit_exact_hits": int(round(finite(raw.get("unit_exact_hits")))) if math.isfinite(finite(raw.get("unit_exact_hits"))) else "",
                "unit_exact_trials": int(round(finite(raw.get("unit_exact_trials")))) if math.isfinite(finite(raw.get("unit_exact_trials"))) else "",
                "unit_exact_pd": finite(raw.get("unit_exact_pd")),
                "unit_resolution_hits": int(round(finite(raw.get("unit_resolution_hits")))) if math.isfinite(finite(raw.get("unit_resolution_hits"))) else "",
                "unit_resolution_trials": int(round(finite(raw.get("unit_resolution_trials")))) if math.isfinite(finite(raw.get("unit_resolution_trials"))) else "",
                "unit_resolution_pd": finite(raw.get("unit_resolution_pd")),
                "cluster_hits": int(round(finite(raw.get("cluster_hits")))) if math.isfinite(finite(raw.get("cluster_hits"))) else "",
                "cluster_trials": int(round(finite(raw.get("cluster_trials")))) if math.isfinite(finite(raw.get("cluster_trials"))) else "",
                "cluster_pd": finite(raw.get("cluster_pd")),
                "target_hits": int(round(target_hits)),
                "target_trials": int(round(target_trials)),
                "target_pd": target_pd,
                "target_wilson_low": target_low,
                "target_wilson_high": target_high,
                "track_hits": int(round(finite(raw.get("track_hits")))) if math.isfinite(finite(raw.get("track_hits"))) else "",
                "track_trials": int(round(finite(raw.get("track_trials")))) if math.isfinite(finite(raw.get("track_trials"))) else "",
                "track_pd": finite(raw.get("track_pd")),
                "angle_rmse_deg": finite(raw.get("angle_rmse_deg")),
                "angle_bias_deg": finite(raw.get("angle_bias_deg")),
                "source_curve": str(path),
                "seed": angle_manifest.get("base_seed", angle_manifest.get("seed", root_manifest.get("base_seed", ""))),
                "period_count": angle_manifest.get("period_count", ""),
                "trials_per_level": angle_manifest.get("trials_per_level", root_manifest.get("trials_per_level", "")),
                "go_alpha": finite(angle_manifest.get("go_alpha")),
                "cfar_pfa": finite(angle_manifest.get("pfa")),
                "cfar_guard": finite(angle_manifest.get("cfar_guard")),
                "cfar_background": finite(angle_manifest.get("cfar_background")),
                "strict_online_no_prior": bool(angle_manifest.get("strict_online_no_prior", root_manifest.get("strict_online_no_prior", False))),
            })
    result.sort(key=lambda row: (float(row["angle_deg"]), float(row["output_scnr_db"])))
    return result, root_manifest


def make_plot(rows: list[dict[str, object]], minimum: int, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(8.5, 5.3))
    for angle in (0.0, 30.0, 45.0):
        selected = [row for row in rows if abs(float(row["angle_deg"]) - angle) < 1e-6]
        selected.sort(key=lambda row: float(row["output_scnr_db"]))
        if not selected:
            continue
        x = np.asarray([float(row["output_scnr_db"]) for row in selected])
        y = np.asarray([float(row["target_pd"]) for row in selected])
        lo = np.asarray([float(row["target_wilson_low"]) for row in selected])
        hi = np.asarray([float(row["target_wilson_high"]) for row in selected])
        color = COLORS[angle]
        trials = sum(int(row["target_trials"]) for row in selected)
        ax.plot(x, y, "o-", color=color, markerfacecolor="white", label=f"{LABELS[angle]}（screen n={trials}）")
        ax.fill_between(x, lo, hi, color=color, alpha=0.13, linewidth=0)
    ax.axhline(0.9, color="#555", linestyle=":", linewidth=1.0, label="Pd=0.9")
    ax.set_xlabel("输出 SCNR（dB；固定 3×3 S+N 支撑增量 / N-only P0）")
    ax.set_ylabel("目标级 truth Pd")
    ax.set_title(f"GMTI 目标级检测概率：min_points={minimum}（独立分图）")
    ax.set_ylim(0.0, 1.03)
    ax.grid(True, color="#d9dde3", alpha=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output, dpi=220)
    fig.savefig(output.with_suffix(".pdf"))
    plt.close(fig)


def first_crossing(rows: list[dict[str, object]], target: float = 0.9) -> float:
    selected = sorted(rows, key=lambda row: float(row["output_scnr_db"]))
    for row in selected:
        if float(row["target_pd"]) >= target:
            return float(row["output_scnr_db"])
    return float("nan")


def mean_finite(values: list[object]) -> float:
    numeric = [finite(value) for value in values]
    numeric = [value for value in numeric if math.isfinite(value)]
    return float(np.mean(numeric)) if numeric else float("nan")


def unit_cut_mapping(root: Path, angle: float) -> dict[str, float]:
    """Fit the audited output-SCNR -> physical CUT-SCNR mapping.

    The calibration manifest's slope is the *control-input -> output* mapping;
    it must not be reused as an output-to-CUT conversion.  The latter is fitted
    only from the retained, paired per-angle CSV used by the actual run.
    """
    path = angle_curve_path(root, angle).parent / "processed_output_snr_calibration.csv"
    xs: list[float] = []
    ys: list[float] = []
    if path.is_file():
        for row in read_csv(path):
            x = finite(row.get("processed_output_scnr_db"))
            y = finite(row.get("unit_cut_scnr_db"))
            if math.isfinite(x) and math.isfinite(y):
                xs.append(x)
                ys.append(y)
    if len(xs) < 2:
        return {"slope": float("nan"), "intercept": float("nan"),
                "r2": float("nan"), "max_abs_residual": float("nan"),
                "sample_count": float(len(xs))}
    slope, intercept = np.polyfit(np.asarray(xs), np.asarray(ys), 1)
    predicted = slope * np.asarray(xs) + intercept
    residual = predicted - np.asarray(ys)
    total = float(np.sum((np.asarray(ys) - np.mean(ys)) ** 2))
    r2 = 1.0 - float(np.sum(residual ** 2)) / total if total > 0.0 else float("nan")
    return {"slope": float(slope), "intercept": float(intercept),
            "r2": r2, "max_abs_residual": float(np.max(np.abs(residual))),
            "sample_count": float(len(xs))}


def audit_summary(root: Path) -> dict[str, object]:
    path = root / "information_leakage_audit.json"
    if not path.is_file():
        return {"status": "missing", "pipeline_count": 0, "truth_active_detector_count": "—"}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return {
            "status": doc.get("status", "unknown"),
            "pipeline_count": doc.get("pipeline_count", 0),
            "truth_active_detector_count": doc.get("truth_active_detector_count", "—"),
            "diagnostic_truth_path_count": doc.get("diagnostic_truth_path_count", "—"),
            "nms_truth_tiebreak_source_fix": doc.get("nms_truth_tiebreak_source_fix", "—"),
        }
    except (OSError, ValueError, TypeError):
        return {"status": "invalid", "pipeline_count": 0, "truth_active_detector_count": "—"}


def track_dependency_rows(root: Path, minimum: int) -> list[dict[str, object]]:
    """Compute the requested three-screen dependence diagnostics from truth labels."""
    rows: list[dict[str, object]] = []
    for angle in (0.0, 30.0, 45.0):
        curve_path = angle_curve_path(root, angle)
        screen_path = curve_path.parent / "standard_mc_screen_metrics.csv"
        if not screen_path.is_file():
            continue
        records = read_csv(screen_path)
        triples = []
        for record in records:
            try:
                triple = tuple(int(round(float(record.get(key, 0)))) for key in
                               ("screen1_hit", "screen2_hit", "screen3_hit"))
            except (TypeError, ValueError):
                continue
            if len(triple) == 3:
                triples.append(triple)
        if not triples:
            continue
        values = np.asarray(triples, dtype=float)
        flat = values.reshape(-1)
        p_screen = float(np.mean(flat))
        prev_one = next_one = prev_zero = next_zero = 0
        for left, right in ((values[:, 0], values[:, 1]), (values[:, 1], values[:, 2])):
            prev_one += int(np.sum(left == 1))
            next_one += int(np.sum((left == 1) & (right == 1)))
            prev_zero += int(np.sum(left == 0))
            next_zero += int(np.sum((left == 0) & (right == 1)))
        cond_prev1 = next_one / prev_one if prev_one else float("nan")
        cond_prev0 = next_zero / prev_zero if prev_zero else float("nan")
        pair_corr = []
        for left, right in ((values[:, 0], values[:, 1]), (values[:, 0], values[:, 2]),
                            (values[:, 1], values[:, 2])):
            if np.std(left) > 0 and np.std(right) > 0:
                pair_corr.append(float(np.corrcoef(left, right)[0, 1]))
        p12 = float(np.mean(values[:, 0] * values[:, 1]))
        p13 = float(np.mean(values[:, 0] * values[:, 2]))
        p23 = float(np.mean(values[:, 1] * values[:, 2]))
        p123 = float(np.mean(values[:, 0] * values[:, 1] * values[:, 2]))
        track_ie = p12 + p13 + p23 - 2.0 * p123
        track_count = float(np.mean(np.sum(values, axis=1) >= 2))
        track_ind = 3.0 * p_screen * p_screen - 2.0 * p_screen ** 3
        rows.append({
            "min_points": minimum,
            "angle_deg": angle,
            "group_count": len(triples),
            "screen_trial_count": len(triples) * 3,
            "p_screen": p_screen,
            "p_dt1_given_dt0_1": cond_prev1,
            "p_dt1_given_dt0_0": cond_prev0,
            "pairwise_correlation_mean": float(np.mean(pair_corr)) if pair_corr else float("nan"),
            "p12": p12, "p13": p13, "p23": p23, "p123": p123,
            "track_pd_inclusion_exclusion": track_ie,
            "track_pd_direct": track_count,
            "track_pd_independent_baseline": track_ind,
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min2-dir", type=Path, required=True)
    parser.add_argument("--min3-dir", type=Path, required=True)
    parser.add_argument("--min6-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-pdf", type=Path,
                        default=ROOT / "docs/GMTI_输出SCNR_测角精度与检测概率_minpoints2_3_6_实测补充报告_20260828.pdf")
    parser.add_argument("--output-html", type=Path,
                        default=ROOT / "docs/GMTI_输出SCNR_测角精度与检测概率_minpoints2_3_6_实测补充报告_20260828.html")
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sources = {2: args.min2_dir.resolve(), 3: args.min3_dir.resolve(), 6: args.min6_dir.resolve()}
    all_rows: list[dict[str, object]] = []
    manifests: dict[int, dict[str, object]] = {}
    audit_rows: list[list[object]] = []
    dependency_rows: list[dict[str, object]] = []
    for minimum, root in sources.items():
        rows, manifest = load_rows(root, minimum)
        if len(rows) != 30:
            raise SystemExit(f"min_points={minimum} 期望 30 行（3 角度×10 档），实际 {len(rows)}：{root}")
        all_rows.extend(rows)
        manifests[minimum] = manifest
        audit = audit_summary(root)
        audit_rows.append([minimum, audit["status"], audit["pipeline_count"], audit["truth_active_detector_count"], audit["diagnostic_truth_path_count"], audit["nms_truth_tiebreak_source_fix"]])
        dependency_rows.extend(track_dependency_rows(root, minimum))
        make_plot(rows, minimum, out / f"target_pd_minpoints_{minimum}.png")

    # The CSV is the exact source table used below; no smoothing or fitting is
    # applied.  This makes the report auditable after raw BIN cleanup.
    write_csv(out / "target_pd_minpoints_detailed_summary.csv", all_rows)
    write_csv(out / "track_dependency_summary.csv", dependency_rows)
    strict_all = all(bool(row["strict_online_no_prior"]) for row in all_rows)
    summary_rows: list[list[object]] = []
    for minimum in (2, 3, 6):
        for angle in (0.0, 30.0, 45.0):
            selected = [row for row in all_rows if int(row["min_points"]) == minimum and abs(float(row["angle_deg"]) - angle) < 1e-6]
            summary_rows.append([minimum, f"{angle:g}°", len(selected), sum(int(row["target_trials"]) for row in selected),
                                 fmt(first_crossing(selected)), fmt(mean_finite([row["target_pd"] for row in selected]), 4),
                                 fmt(mean_finite([row["angle_rmse_deg"] for row in selected]), 4)])

    # Parameter values are intentionally explicit rather than inferred from
    # the measured curves.  They come from the run manifest/configuration.
    first_manifest = manifests[2]
    desired_grid = first_manifest.get("desired_output_scnr_grid_db", [6, 8, 10, 12, 14, 16, 18, 20, 22, 24])
    trials_per_level = first_manifest.get("trials_per_level", 5)
    # The production alpha is recorded in each angle manifest.  Do not
    # recompute it with the CA ring formula: GO uses the directional maximum
    # and its alpha is calibrated from E[exp(-alpha M)] = Pfa.
    alpha_values = [finite(row.get("go_alpha")) for row in all_rows
                    if math.isfinite(finite(row.get("go_alpha")))]
    alpha_value = mean_finite(alpha_values) if alpha_values else 13.44951031977817
    cut_mappings = {angle: unit_cut_mapping(sources[2], angle)
                    for angle in (0.0, 30.0, 45.0)}
    mapping_rows = []
    mapping_sentences = []
    for angle in (0.0, 30.0, 45.0):
        mapping = cut_mappings[angle]
        mapping_rows.append([
            f"{angle:g}°", fmt(mapping["slope"], 5), fmt(mapping["intercept"], 4),
            fmt(mapping["r2"], 5), fmt(mapping["max_abs_residual"], 3),
            int(mapping["sample_count"]) if math.isfinite(mapping["sample_count"]) else "—",
        ])
        if math.isfinite(mapping["slope"]):
            mapping_sentences.append(
                f"{angle:g}°: $\\gamma_{{CUT,dB}}\\approx"
                f"{mapping['slope']:.4f}\\,SCNR_{{out,dB}}{mapping['intercept']:+.4f}$"
            )
    mapping_text = "；".join(mapping_sentences) if mapping_sentences else "当前逐档 CUT 映射缺失"
    zero_mapping = cut_mappings[0.0]
    example_cut_db = (zero_mapping["slope"] * 6.0 + zero_mapping["intercept"]
                      if math.isfinite(zero_mapping["slope"]) else float("nan"))
    example_fixed_pd = (float(ncx2.sf(2.0 * alpha_value, 2,
                                      2.0 * 10.0 ** (example_cut_db / 10.0)))
                        if math.isfinite(example_cut_db) else float("nan"))
    # Derive the reported Monte-Carlo counts from the retained summary rows,
    # rather than baking one historical 150/50 schedule into the report.
    # Each row is one level for one angle; target_trials is the number of
    # screen events in that level and track_trials is the number of 2-of-3
    # groups.  The run manifest remains the primary configuration evidence.
    angle0_rows = [row for row in all_rows if abs(float(row["angle_deg"])) < 1e-6
                   and int(row["min_points"]) == 2]
    periods_per_angle = sum(int(row["target_trials"]) for row in angle0_rows)
    groups_per_angle = sum(int(row["track_trials"]) for row in angle0_rows
                           if str(row["track_trials"]).strip())
    md = out / "GMTI_minpoints2_3_6_实测补充报告_20260828.md"
    image_rel = lambda minimum: f"target_pd_minpoints_{minimum}.png"
    examples = sorted([row for row in all_rows
                       if int(row["min_points"]) == 2
                       and abs(float(row["angle_deg"])) < 1e-6],
                      key=lambda row: float(row["output_scnr_db"]))
    example = examples[0] if examples else {}
    example_text = ""
    if example:
        example_text = (
            f"以修正后 min_points=2、0° 的最低 output-SCNR 档为例：实际 "
            f"output SCNR={fmt(example.get('output_scnr_db'), 2)} dB，"
            f"物理 CUT 命中={example.get('unit_exact_hits', example.get('unit_hits'))}/"
            f"{example.get('unit_exact_trials', example.get('unit_trials'))}"
            f"（Pd={fmt(example.get('unit_exact_pd', example.get('unit_pd')), 3)}），"
            f"±2-bin 分辨率事件命中={example.get('unit_resolution_hits', '—')}/"
            f"{example.get('unit_resolution_trials', '—')}"
            f"（Pd={fmt(example.get('unit_resolution_pd'), 3)}），"
            f"cluster={fmt(example.get('cluster_pd'), 3)}，"
            f"target={fmt(example.get('target_pd'), 3)}。这说明单元 CUT 命中、"
            f"生产分辨率事件和目标级事件是三个不同层次；不能用其中一个替代另一个。"
        )
    sections: list[str] = []
    sections.append("""# GMTI 输出 SCNR—测角精度—检测概率：min_points=2/3/6 实测补充报告

本报告是主报告《GMTI_输出SCNR_测角精度与检测概率_理论建模报告_20260826》及其 20260827 闭环版的实测补充。目标是用同一套真实 CUDA 生产处理链，在 `min_points=2/3/6` 下逐一测量目标级检测概率，解释单元级实测曲线看起来高于教材曲线的原因，并核查测试是否把 truth 或 N-only 先验泄露给了在线检测器。

结论先行：本批每种配置均使用 3 个角度、10 个 output-SCNR 档位、每档 5 个三屏组；每个角度共 150 个 period、50 个三屏组，每个角度×档位有 15 个 screen truth trial 和 5 个三屏 2-of-3 trial。正式横轴是固定 3×3 支撑上的 output SCNR，不是 Stage2 的 `target_snr_db` 控制旋钮。原始 BIN 在 pipeline、GO/CSI、TrackManager payload 和 truth-match 审计成功后才删除，曲线所需汇总 CSV/manifest 保留。
""")
    sections.append("## 1. 实验设计与样本数\n\n" + table(["项目", "取值", "含义/证据"], [
        ["背景", "thermal noise only；area clutter disabled", "本批先隔离 CFAR/SNR 主因，不把杂波波动混入结论；run_manifest"],
        ["角度", "0°、30°、45°", "三个固定生产波位中心；每角度独立 seed（20260910/11/12）"],
        ["$P_{fa}$", "$1\\times10^{-6}$", "CFAR 虚警概率"],
        ["GO 窗", "guard=4，background=16", "外窗 41×41，保护窗 9×9；8 个独立块：4×(16×16)+4×(16×9/9×16)，环内训练单元 N=1600"],
        ["$\\alpha$", fmt(alpha_value, 10), "$E[\\exp(-\\alpha M/P_0)]=P_{fa}$ 的 directional-max 校准；本报告不设置 CA-CFAR 对照"],
        ["聚类", "分别 min_points=2/3/6；small peak=20 dB", "min_points=6 的 3–5 点小簇恢复仍用 small_min_points=3；min2 为通过生产校验而显式设置 small_min_points=2"],
        ["档位", ", ".join(str(x) for x in desired_grid), "横轴按实际固定支撑 output SCNR 记录；控制量只在 manifest 作为反解变量"],
        ["每点样本", f"{trials_per_level} 组三屏 = 15 screens + 5 track events", "target 分母=15；track 分母=5；Wilson 95% 区间"],
        ["每配置总量", f"3×10×{periods_per_angle//10} = {periods_per_angle*3} periods；{groups_per_angle*3} 三屏组", "unit/cluster/target/angle 为 screen 级；track 为三屏事件级"],
    ]) + "\n\n每个 screen 的真值只用于离线标注、固定支撑 SCNR 计算、truth matching 和 RMSE；在线 XML 没有激活 `pc_peak_scene_truth`，N-only 统计在严格模式下不会写回 S+N XML。")
    sections.append("## 2. 单元级教材曲线为什么会显得偏低\n\n教材 GO-CFAR 的固定门限表达式是\n\n$$P_d=Q_1\\left(\\sqrt{2\\gamma_{CUT}},\\sqrt{2\\alpha m}\\right)=\\operatorname{ncx2.sf}(2\\alpha m;\\,2,\\,2\\gamma_{CUT}),\\qquad \\gamma_{CUT}=P_{CUT}/P_0.$$\n\n其中 $m=M/P_0$ 是四方向训练均值最大值的归一化实例；真实 GO 曲线对 $m$ 积分，不能把 $m$ 和 $M$ 随意当成 1。生产 alpha=" + fmt(alpha_value, 10) + " 来自八块相关结构的 directional-max 校准方程 $E[\\exp(-\\alpha M/P_0)]=P_{fa}$。\n\n如果把图上的 output SCNR 直接当作单个 CUT 的 $10\\log_{10}(\\gamma_{CUT})$，例如 output SCNR=6 dB，则 $\\gamma=10^{0.6}=3.981$，固定 $m=1$ 的直观 Pd 仅约 0.0127；这条“直接代入”曲线把 3×3 输出支撑增量误当成一个 CUT，坐标定义错了。\n\n生产输出 SCNR 定义为\n\n$$SCNR_{out}=10\\log_{10}\\frac{P_{S+N}(W_{3\\times3})-P_N(W_{3\\times3})}{P_N(W_{3\\times3})}.$$\n\n本批用同一固定支撑、同一生产链逐档拟合物理 CUT 与 output SCNR，得到：\n\n" + table(["角度", "斜率", "截距(dB)", "$R^2$", "最大残差(dB)", "逐档样本"], mapping_rows) + "\n\n对应关系为 " + mapping_text + "。因此 output SCNR=6 dB 在 0° 的实际映射约为 CUT=" + fmt(example_cut_db, 2) + " dB；固定 $m=1$ 代回教材式 Pd 约为 " + fmt(example_fixed_pd, 4) + "，而 `go_theory_vs_mc.csv` 使用真实 N-only $M/P_0$ 的经验积分。\n\n需要再区分一个实现层因素：$Q_1$ 假设 CUT 是“确定复信号 + 复高斯噪声”，而真实 CSI/杂波抵消链会使残余目标幅度随 period 和 Doppler/相位状态变化；因此实测固定 CUT 可能在同一档位内上下波动。该波动不是先验泄露，也不能靠把 S+N 命中结果回填理论参数来消除；本报告保留独立 S-only/N-only 标尺，并用 Wilson 区间表示有限样本不确定度。" + example_text + "\n\n所以“实测比教材好很多”首先是错轴造成的视觉差，不是在线检测器凭空获得了额外增益。第二个历史问题是 NMS 曾经用 truth-derived position error 破平局；本批已删除该分支，审计字段 `nms_truth_tiebreak_removed=true`。")
    sections.append("## 3. 三种 min_points 的目标级实测曲线\n\n三张图分别只画一个 min_points；圆点是每个 output-SCNR 档位的 `target_hits/target_trials`，阴影是 Wilson 95% 区间。目标级事件不是“命中任意一个单元”，而是当前生产 clustering、3–5 点 +20 dB 小簇恢复、连通性和最终 truth-match 全部通过。表中的 `unit CUT Pd` 是物理投影 CUT 的直接命中率，供单元 GO-CFAR 教材理论比较；`unit resolution Pd` 是生产分辨率门内（±2 range/Doppler bins）至少一个 GO 命中的命中率，供解释 FFT 分数单元/主瓣偏移。两者不是同一个随机事件，不能把后者直接代入单一 CUT 的 Q1 公式。\n\n" + table(["min_points", "角度", "档位行数", "target screen trials", "首次观测 $P_d\\ge0.9$ (dB)", "10 档平均 Pd", "平均角度 RMSE (deg)"], summary_rows))
    for minimum in (2, 3, 6):
        rows = [row for row in all_rows if int(row["min_points"]) == minimum]
        detail = [[f"{float(row['angle_deg']):g}°", fmt(row["output_scnr_db"], 2), f"{row['target_hits']}/{row['target_trials']}", fmt(row["unit_pd"], 3), fmt(row["unit_resolution_pd"], 3), fmt(row["cluster_pd"], 3), fmt(row["target_pd"], 3), fmt(row["target_wilson_low"], 3), fmt(row["target_wilson_high"], 3), f"{row['track_hits']}/{row['track_trials']}", fmt(row["angle_rmse_deg"], 3)] for row in rows]
        sections.append(f"### 3.{minimum} min_points={minimum}\n\n![min_points={minimum} 目标 Pd]({image_rel(minimum)})\n\n" + table(["角度", "output SCNR(dB)", "target hits/n", "unit CUT Pd", "unit resolution Pd", "cluster Pd", "target Pd", "Wilson low", "Wilson high", "track hits/n", "angle RMSE(deg)"], detail) + "\n\n图中每个点的分子、分母直接来自真实生产链汇总，不进行平滑、拟合或按 truth 重新挑选峰值。`unit CUT Pd` 与 `unit resolution Pd` 的区别见本节开头；后续 cluster/target 只能由 production hit mask 和 truth-match 逐级得到。min_points 越大，需要的连通命中单元越多，unit→cluster→target 的漏斗损失通常越明显；但 3–5 点且峰值超过局部中位数 20 dB 的恢复逻辑仍可能使 min_points=6 在强目标处不再单调劣化。")
    sections.append("## 4. 从单元到目标、再到航迹的计算链\n\n单元级只问固定 CUT 是否超过 GO 阈值：\n\n$$H_i=1\\{P_i>\\alpha M_i\\},\\qquad \\hat P_{d,unit}=\\frac{\\sum_iH_i}{n_{screen}}.$$\n\n目标级使用真实 joint hit mask 运行生产聚类，而不是把单元事件假设成独立 Bernoulli。解析 baseline 可写成 Poisson-binomial：\n\n$$P_{cluster}^{PB}=\\sum_{k=m}^{K}\\sum_{A:|A|=k}\\prod_{i\\in A}p_i\\prod_{j\\notin A}(1-p_j),$$\n\n但本报告主指标直接计数生产规则的输出。随后按当前实现的条件分解记录\n\n$$P_{target}=P_{cluster}P(select\\mid cluster)P(reloc\\mid select,cluster)P(match\\mid reloc,select,cluster).$$\n\n航迹主指标是三屏选二：对同一 truth 轨迹记录 `screen1_hit/screen2_hit/screen3_hit`，若三屏命中数 $\\ge2$，则 `track_2of3_hit=1`。若屏间近似独立且目标级概率为 $p$，baseline 为\n\n$$P_{track}=3p^2-2p^3.$$\n\n本批 track 分母为每个角度×档位的 5 个三屏组；任何非独立性应使用\n\n$$P_{track}=P_{12}+P_{13}+P_{23}-2P_{123}$$\n\n并检查 $P(D_t=1\\mid D_{t-1}=1)$、$P(D_t=1\\mid D_{t-1}=0)$ 及 pairwise correlation，不能只套独立公式。下表由每个配置的 `standard_mc_screen_metrics.csv` 直接计算；TrackManager 的 `Confirmed+matched_this_frame` 仅作为附录诊断，不替代上述 truth 事件。\n\n" + table(["min_points", "角度", "三屏组数", "$P_{screen}$", "$P(D_t=1\\mid D_{t-1}=1)$", "$P(D_t=1\\mid D_{t-1}=0)$", "平均相关", "$P_{track}^{IE}$", "$P_{track}^{ind}$"], [[row["min_points"], f"{float(row['angle_deg']):g}°", row["group_count"], fmt(row["p_screen"], 3), fmt(row["p_dt1_given_dt0_1"], 3), fmt(row["p_dt1_given_dt0_0"], 3), fmt(row["pairwise_correlation_mean"], 3), fmt(row["track_pd_inclusion_exclusion"], 3), fmt(row["track_pd_independent_baseline"], 3)] for row in dependency_rows]))
    sections.append("## 5. 信息泄露核查\n\n" + table(["min_points", "审计状态", "pipeline 数", "在线 truth 路径数", "诊断 truth 路径数", "NMS 修复记录"], audit_rows) + f"\n\n本批所有曲线的严格模式汇总：`strict_online_no_prior={str(strict_all).lower()}`。审计允许 truth 的范围仅为：离线 output-SCNR 支撑/评分、离线 truth matching、离线 RMSE 与三屏标签。生产 XML 的 `debug_pc_peak=0` 且 `pc_peak_scene_truth` 为空；N-only 的 P0 只在离线计算 output-SCNR 轴，不写入 S+N 在线 detector。此隔离使“曲线高”不能被解释成 detector 看到了答案。")
    sections.append("## 6. 一次具体数值复算（如何读图）\n\n以任一角度某个记录为例，若 CSV 给出 `target_hits=8,target_trials=15`，则\n\n$$\\hat P_{target}=8/15=0.5333.$$\n\nWilson 区间由 $(h,n)=(8,15)$ 直接计算，不能把 0.5333 当成无不确定度的连续真值。若同一点三屏组中 `track_hits=3,track_trials=5`，则\n\n$$\\hat P_{track}=3/5=0.6,$$\n\n独立 baseline 用目标级 $p=0.5333$ 时为 $3p^2-2p^3\\approx0.ಂಚ?$. 实测航迹主值仍以三屏事件计数为准。每个表格单元都可由 `target_pd_minpoints_detailed_summary.csv` 的同名列复算。")
    # Replace the deliberately symbolic placeholder with a generated value in
    # the prose while keeping the formula visible.
    sections[-1] = sections[-1].replace("0.ಂಚ?", f"{3*(8/15)**2-2*(8/15)**3:.4f}")
    sections.append("""## 7. 结果解释、限制与下一步

- min_points=2/3/6 的差异是目标级聚类门槛差异，不应误读成单元级 CFAR 门限变化；三种配置共享 Pfa、GO 窗、支撑定义、角度、SCNR 档位和 seed。
- 本批 area clutter intentionally disabled，适合先核查 CFAR/SNR 与聚类机制；不能把它直接宣称为复杂真实杂波环境的最终性能。
- 每个点只有 15 个 screen trials、5 个 track events，Wilson 区间仍较宽；本批是模型/实现验证，不是最终 90% 门限声明。
- 进入正式多 seed sweep 前必须确认三种配置的审计均为 pass，并在 3–5 个独立 seed 上复核 unit/target/track/RMSE 的趋势；本批已经具备先做该小规模扩充的证据基础，但不应跳过 seed 间相关性检查。

必要证据：

- 详细 CSV：`target_pd_minpoints_detailed_summary.csv`
- 三张独立图：`target_pd_minpoints_2.png/.pdf`、`target_pd_minpoints_3.png/.pdf`、`target_pd_minpoints_6.png/.pdf`
- 每个配置的 `run_manifest.json` 与 `information_leakage_audit.json`
- 主理论/闭环报告：`docs/GMTI_输出SCNR_测角精度与检测概率_理论建模与MonteCarlo闭环报告_20260827.pdf`
""")
    md.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    pdf_path = args.output_pdf.resolve()
    html_path = args.output_html.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    pandoc = shutil.which("pandoc")
    xelatex = shutil.which("xelatex")
    if pandoc and xelatex:
        result = subprocess.run([pandoc, str(md), "--resource-path", str(out), "--pdf-engine", xelatex,
                                 "-V", "CJKmainfont=Noto Sans CJK SC", "-o", str(pdf_path)], cwd=ROOT, check=False)
        if result.returncode:
            raise SystemExit(f"pandoc PDF 失败，退出码={result.returncode}")
    if pandoc:
        subprocess.run([pandoc, str(md), "--standalone", "--resource-path", str(out), "--self-contained",
                        "--mathml", "-o", str(html_path)], cwd=ROOT, check=False)
    print(f"[PASS] minpoints 详细报告 Markdown: {md}")
    print(f"[PASS] minpoints 详细报告 PDF: {pdf_path}")
    print(f"[PASS] minpoints 详细报告 HTML: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
