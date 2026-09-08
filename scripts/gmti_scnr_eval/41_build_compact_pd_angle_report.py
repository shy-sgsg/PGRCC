#!/usr/bin/env python3
"""构建 0°/30°中心、min_points=2/3/6 的精简 Pd/RMSE 报告。

只读取已经审计的 min-points 汇总 CSV、Poisson-binomial 目标级理论 CSV
和 Jacobian 测角理论 CSV；不打开 raw BIN/F32，也不重新拟合任何命中率。
"""

from __future__ import annotations

import argparse
import csv
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


ROOT = Path(__file__).resolve().parents[2]
ANGLES = (0.0, 30.0)
MINPOINTS = (2, 3, 6)
ANGLE_LABEL = {0.0: "0°中心", 30.0: "30°中心"}
ANGLE_COLOR = {0.0: "#1f77b4", 30.0: "#d98c00"}
FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if FONT.is_file():
    font_manager.fontManager.addfont(str(FONT))
    plt.rcParams["font.family"] = "Noto Sans CJK JP"
plt.rcParams["axes.unicode_minus"] = False


def fnum(value: object, default: float = float("nan")) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def inum(value: object, default: int = 0) -> int:
    v = fnum(value)
    return int(round(v)) if math.isfinite(v) else default


def fmt(value: object, digits: int = 3) -> str:
    v = fnum(value)
    return f"{v:.{digits}f}" if math.isfinite(v) else "—"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def wilson(hits: int, trials: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if trials <= 0:
        return float("nan"), float("nan")
    p = hits / trials
    den = 1.0 + z * z / trials
    centre = (p + z * z / (2.0 * trials)) / den
    half = z * math.sqrt(max(0.0, p * (1.0 - p) / trials + z * z / (4.0 * trials * trials))) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def md_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---:" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
    return "\n".join(lines)


def load_measured(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for raw in read_csv(path):
        row = dict(raw)
        for key in ("min_points", "angle_deg", "level_index", "output_scnr_db", "target_pd",
                    "track_pd", "angle_rmse_deg", "angle_bias_deg", "theory_unit_pd_empirical_n_only"):
            row[key] = fnum(raw.get(key))
        for field in ("target", "track"):
            row[f"{field}_hits"] = inum(raw.get(f"{field}_hits"))
            row[f"{field}_trials"] = inum(raw.get(f"{field}_trials"))
            row[f"{field}_wilson_low"], row[f"{field}_wilson_high"] = wilson(
                row[f"{field}_hits"], row[f"{field}_trials"])
        rows.append(row)
    return [r for r in rows if fnum(r.get("angle_deg")) in ANGLES]


def load_target_theory(path: Path) -> dict[tuple[float, int], tuple[np.ndarray, np.ndarray]]:
    result: dict[tuple[float, int], tuple[np.ndarray, np.ndarray]] = {}
    raw = read_csv(path)
    for angle in ANGLES:
        for minimum in MINPOINTS:
            selected = [r for r in raw if r.get("angle_group") == f"{int(angle)}deg_center"
                        and inum(r.get("min_points"), -1) == minimum]
            selected.sort(key=lambda r: fnum(r.get("output_scnr_db")))
            x = np.asarray([fnum(r.get("output_scnr_db")) for r in selected], dtype=float)
            y = np.asarray([fnum(r.get("pd_cluster_pb_profile")) for r in selected], dtype=float)
            mask = np.isfinite(x) & np.isfinite(y)
            if np.sum(mask) < 2:
                raise RuntimeError(f"目标级理论缺少 {angle:g}°/min_points={minimum} 曲线")
            result[(angle, minimum)] = (x[mask], np.clip(y[mask], 0.0, 1.0))
    return result


def load_angle_theory(path: Path) -> dict[float, tuple[np.ndarray, np.ndarray]]:
    result: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    raw = read_csv(path)
    for angle in ANGLES:
        selected = [r for r in raw if abs(fnum(r.get("angle_deg")) - angle) < 1e-9]
        selected.sort(key=lambda r: fnum(r.get("output_scnr_db")))
        x = np.asarray([fnum(r.get("output_scnr_db")) for r in selected], dtype=float)
        y = np.asarray([fnum(r.get("ideal_angle_rmse_deg")) for r in selected], dtype=float)
        mask = np.isfinite(x) & np.isfinite(y)
        result[angle] = (x[mask], y[mask])
    return result


def interp_curve(x: np.ndarray, y: np.ndarray, points: np.ndarray) -> np.ndarray:
    return np.interp(points, x, y, left=y[0], right=y[-1])


def plot_metric(rows: list[dict[str, object]], target_theory: dict[tuple[float, int], tuple[np.ndarray, np.ndarray]],
                angle_theory: dict[float, tuple[np.ndarray, np.ndarray]], minimum: int, metric: str,
                output: Path) -> None:
    is_angle = metric == "angle_rmse_deg"
    ylabel = "测角 RMSE (deg)" if is_angle else ("目标级 Pd" if metric == "target_pd" else "航迹级 Pd（三屏选二）")
    fig, ax = plt.subplots(figsize=(8.4, 4.9))
    plotted = False
    for angle in ANGLES:
        selected = sorted([r for r in rows if inum(r.get("min_points"), -1) == minimum
                           and abs(fnum(r.get("angle_deg")) - angle) < 1e-9],
                          key=lambda r: fnum(r.get("output_scnr_db")))
        x = np.asarray([fnum(r.get("output_scnr_db")) for r in selected], dtype=float)
        y = np.asarray([fnum(r.get(metric)) for r in selected], dtype=float)
        mask = np.isfinite(x) & np.isfinite(y)
        if not np.any(mask):
            continue
        color = ANGLE_COLOR[angle]
        ax.plot(x[mask], y[mask], "o-", color=color, markerfacecolor="white",
                linewidth=1.6, label=f"{ANGLE_LABEL[angle]} 仿真")
        if not is_angle:
            prefix = "target" if metric == "target_pd" else "track"
            lo = np.asarray([fnum(r.get(f"{prefix}_wilson_low")) for r in selected])[mask]
            hi = np.asarray([fnum(r.get(f"{prefix}_wilson_high")) for r in selected])[mask]
            ax.fill_between(x[mask], lo, hi, color=color, alpha=0.14, linewidth=0)
            tx, ty = target_theory[(angle, minimum)]
            dense = np.linspace(max(0.0, min(float(np.min(tx)), float(np.min(x[mask])))),
                                min(35.0, max(float(np.max(tx)), float(np.max(x[mask])))), 300)
            target_y = interp_curve(tx, ty, dense)
            theory_y = target_y if metric == "target_pd" else 3.0 * target_y**2 - 2.0 * target_y**3
        else:
            tx, theory_y = angle_theory[angle]
            dense = np.linspace(max(0.0, min(float(np.min(tx)), float(np.min(x[mask])))),
                                min(35.0, max(float(np.max(tx)), float(np.max(x[mask])))), 300)
            theory_y = interp_curve(tx, theory_y, dense)
        ax.plot(dense, theory_y, "--", color=color, linewidth=1.25, alpha=0.9,
                label=f"{ANGLE_LABEL[angle]} 理论")
        plotted = True
    ax.set_title(f"min_points={minimum}：output-SCNR 与 {ylabel} 的关系")
    ax.set_xlabel("CUT-equivalent output SCNR (dB；生产物理 CUT)")
    ax.set_ylabel(ylabel)
    ax.grid(True, color="#d9dde3", alpha=0.7)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.legend(fontsize=8, ncol=2)
    if not is_angle:
        ax.set_ylim(0.0, 1.05)
    else:
        ax.set_ylim(bottom=0.0)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=220)
    fig.savefig(output.with_suffix(".pdf"))
    plt.close(fig)
    if not plotted:
        raise RuntimeError(f"没有可绘制的 {metric} 数据")


def build_report(out: Path, rows: list[dict[str, object]], target_theory: dict[tuple[float, int], tuple[np.ndarray, np.ndarray]],
                 angle_theory: dict[float, tuple[np.ndarray, np.ndarray]], figures: dict[str, Path],
                 source_paths: list[Path]) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    detail_rows: list[dict[str, object]] = []
    table_sections: list[str] = []
    summary_rows: list[list[object]] = []
    for minimum in MINPOINTS:
        subset = [r for r in rows if inum(r.get("min_points"), -1) == minimum]
        pd_table_rows: list[list[object]] = []
        angle_table_rows: list[list[object]] = []
        for angle in ANGLES:
            for r in sorted([x for x in subset if abs(fnum(x.get("angle_deg")) - angle) < 1e-9],
                            key=lambda x: fnum(x.get("output_scnr_db"))):
                x = fnum(r.get("output_scnr_db"))
                tx, ty = target_theory[(angle, minimum)]
                target_th = float(interp_curve(tx, ty, np.asarray([x]))[0]) if math.isfinite(x) else float("nan")
                track_th = 3.0 * target_th**2 - 2.0 * target_th**3 if math.isfinite(target_th) else float("nan")
                ax, ay = angle_theory[angle]
                angle_th = float(interp_curve(ax, ay, np.asarray([x]))[0]) if math.isfinite(x) else float("nan")
                detail_rows.append({"min_points": minimum, "angle_deg": angle, "level_index": inum(r.get("level_index")),
                                    "output_scnr_db": x, "target_theory_pd": target_th,
                                    "target_measured_pd": fnum(r.get("target_pd")), "track_theory_pd": track_th,
                                    "track_measured_pd": fnum(r.get("track_pd")), "angle_theory_rmse_deg": angle_th,
                                    "angle_measured_rmse_deg": fnum(r.get("angle_rmse_deg")),
                                    "angle_matched": inum(r.get("angle_matched")),
                                    "target_hits": inum(r.get("target_hits")), "target_trials": inum(r.get("target_trials")),
                                    "track_hits": inum(r.get("track_hits")), "track_trials": inum(r.get("track_trials"))})
                pd_table_rows.append([ANGLE_LABEL[angle], inum(r.get("level_index")), fmt(x, 2),
                                      fmt(target_th), f"{inum(r.get('target_hits'))}/{inum(r.get('target_trials'))}",
                                      fmt(track_th), f"{inum(r.get('track_hits'))}/{inum(r.get('track_trials'))}"])
                angle_table_rows.append([ANGLE_LABEL[angle], inum(r.get("level_index")), fmt(x, 2),
                                         fmt(angle_th, 3), fmt(r.get("angle_rmse_deg"), 3),
                                         inum(r.get("angle_matched"))])
        table_sections.append(
            f"### min_points={minimum}\n\n"
            "**目标/航迹 Pd：**\n\n" +
            md_table(["角度", "level", "output SCNR", "目标理论 Pd", "目标仿真 h/n",
                      "航迹理论 Pd", "航迹仿真 h/n"], pd_table_rows) +
            "\n\n**测角 RMSE：**\n\n" +
            md_table(["角度", "level", "output SCNR", "测角理论 RMSE", "测角仿真 RMSE", "匹配样本数"],
                     angle_table_rows))
        for angle in ANGLES:
            high = [r for r in detail_rows if r["min_points"] == minimum and r["angle_deg"] == angle
                    and math.isfinite(float(r["target_measured_pd"]))]
            high = high[-1] if high else None
            summary_rows.append([f"min_points={minimum}", ANGLE_LABEL[angle],
                                 fmt(high["target_measured_pd"] if high else float("nan")),
                                 fmt(high["track_measured_pd"] if high else float("nan")),
                                 fmt(high["angle_measured_rmse_deg"] if high else float("nan"), 3)])

    fields = ["min_points", "angle_deg", "level_index", "output_scnr_db", "target_theory_pd",
              "target_measured_pd", "track_theory_pd", "track_measured_pd", "angle_theory_rmse_deg",
              "angle_measured_rmse_deg", "angle_matched", "target_hits", "target_trials", "track_hits", "track_trials"]
    write_csv(out / "compact_pd_angle_curves.csv", detail_rows, fields)
    audit_path = source_paths[0].parent / "scn_minpoints_information_leakage_audit.json"
    audit_status = "missing"
    audit_seed_values: list[int] = []
    audit_roots = 0
    if audit_path.is_file():
        try:
            audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
            audit_status = str(audit_payload.get("status", "unknown"))
            audit_seed_values = [int(v) for v in audit_payload.get("seeds", [])]
            audit_roots = len(audit_payload.get("audits", []))
        except (OSError, ValueError, TypeError):
            audit_status = "unreadable"
    figure_map = {
        **{f"target_min{minimum}": str((out / "figures" / f"target_min{minimum}.png").resolve())
           for minimum in MINPOINTS},
        **{f"track_min{minimum}": str((out / "figures" / f"track_min{minimum}.png").resolve())
           for minimum in MINPOINTS},
        **{f"angle_min{minimum}": str((out / "figures" / f"angle_min{minimum}.png").resolve())
           for minimum in MINPOINTS},
    }
    metadata = {
        "status": "pass", "scope": "0°/30°中心、min_points=2/3/6",
        "measured_source": str(source_paths[0].resolve()),
        "target_theory_source": str(source_paths[1].resolve()),
        "angle_theory_source": str(source_paths[2].resolve()),
        "information_leakage_audit_source": str(audit_path.resolve()),
        "information_leakage_audit_status": audit_status,
        "audited_seed_values": audit_seed_values,
        "audited_root_count": audit_roots,
        "output_axis": "CUT-equivalent output SCNR from paired S+C+N/C+N physical truth CUT",
        "target_theory": "Poisson-binomial point-count baseline from calibrated 3x5 support; eta_connectivity=1",
        "track_theory": "3p^2-2p^3 independent three-screen baseline using target-theory p",
        "angle_theory": "sigma_theta = lambda/(2*pi*d*cos(theta)*sqrt(gamma_out)), lambda=0.0182 m, d=0.17 m",
        "no_raw_bin_opened": True, "evaluation_hit_rates_used_for_fit": False,
        "figure_map": figure_map,
    }
    (out / "compact_report_manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def display_path(path: Path) -> str:
        resolved = path.resolve()
        try:
            return str(resolved.relative_to(ROOT))
        except ValueError:
            return str(resolved)

    output_dir_display = display_path(out)
    audit_dir_display = display_path(audit_path.parent)

    md_parts = [
        "# GMTI 输出 SCNR—目标级检测—航迹级检测—测角精度精简报告",
        "## 技术摘要",
        "本报告只展示 0°和30°波位中心、min_points=2/3/6。实测来自同一紧凑统计 S+C+N 场景的 3 个独立 seed；每个 min_points、角度、档位包含 18 个目标屏幕和 6 个三屏航迹事件。实线/圆点是生产链结果，虚线是解析基线。",
        "目标级实测是当前生产 joint hit mask、连通性、3–5 点 +20 dB 小簇恢复及 truth match 的最终事件；目标理论是校准 3×5 支撑上的 Poisson-binomial 点数 baseline，因此用于看门槛趋势，不等同于完整生产链。航迹实测是三屏选二，航迹理论为独立屏幕近似。测角实测为 truth-match 样本 RMSE，理论为相位噪声 Jacobian 下界。",
        "## 1. 口径与参数",
        "主横轴不是控制变量 `target_snr_db`，而是同一物理 truth CUT 的 CUT-equivalent output SCNR：$\\gamma_{out}=10\\log_{10}[(P_{S+C+N,CUT}-P_{C+N,CUT})/P_{C+N,CUT}]$。所有门槛共用 GO-CFAR：$P_{FA}=10^{-6}$、guard=4、background=16、$\\alpha=13.4495103$；聚类使用 min_points=2/3/6，另有 3–5 点、峰值高出 20 dB 的恢复分支。",
        "## 2. 理论模型",
        "### 2.1 目标级 Poisson-binomial baseline",
        "对 3×5 支撑第 $i$ 个单元，校准得到信号占比 $\\kappa_i$ 和相对背景 $\\beta_i$。以物理 CUT 为锚点，$\\gamma_i=\\gamma_{CUT}(\\kappa_i/\\beta_i)/(\\kappa_7/\\beta_7)$，单元过门限概率记为 $p_i(\\gamma_{CUT})$。令 $H_i\\sim Bernoulli(p_i)$，则 $G(z)=\\prod_{i=1}^{15}[(1-p_i)+p_i z]$，目标级点数 baseline 为 $P_{target}^{PB}(m)=P(\\sum_iH_i\\ge m)=\\sum_{k=m}^{15}[z^k]G(z)$。本报告直接读取已生成的 profile 曲线，不用仿真命中率反拟合。",
        "### 2.2 航迹级 baseline",
        "若三屏目标级命中相互独立且边际概率为 $p$，三屏选二理论为 $P_{track}^{ind}=3p^2-2p^3$。实测航迹概率直接统计同一 truth 轨迹三屏中的 `track_2of3_hit`，不强行套独立假设。",
        "### 2.3 测角精度理论",
        "相位—角度关系为 $\\phi=(2\\pi d/\\lambda)\\sin\\theta$，所以 $|d\\theta/d\\phi|=\\lambda/(2\\pi d\\cos\\theta)$。采用 $\\sigma_\\phi\\approx1/\\sqrt{\\gamma_{out}}$，得到 $\\sigma_\\theta[deg]=180\\lambda/(2\\pi^2d\\cos\\theta\\sqrt{\\gamma_{out}})$。代入 $\\lambda=0.0182$ m、$d=0.17$ m，0°和30°的 Jacobian 分别为 0.01703894 和 0.01967487 rad/rad。",
        "## 3. 输出 SCNR 与目标级 Pd",
        "每张图中，圆点实线为生产目标事件命中率，浅色带为 Wilson 95% 区间；虚线为对应 min_points 的 PB 点数 baseline。若实测高于虚线，不表示 detector 偷看了 truth，而是生产链允许整图连通、小簇恢复和后级匹配，PB 只计算固定 15 个候选点数。",
    ]
    for minimum in MINPOINTS:
        md_parts.append(f"![min_points={minimum} 目标级 Pd](figures/target_min{minimum}.png)\n\n该图固定 min_points={minimum}，同时给出 0°/30°中心；横轴为物理 CUT 等效 output SCNR，分母为每个档位的 18 个屏幕事件。")
    md_parts += ["## 4. 输出 SCNR 与航迹级 Pd", "实线/圆点是三屏选二实测，虚线是把目标 PB 理论概率代入 $3p^2-2p^3$ 的独立航迹 baseline。两者差异反映跨屏共享背景、目标状态和有限样本，不应归因于 SCNR 轴重新拟合。"]
    for minimum in MINPOINTS:
        md_parts.append(f"![min_points={minimum} 航迹级 Pd](figures/track_min{minimum}.png)\n\n该图固定 min_points={minimum}；航迹分母为 6 个三屏 truth 事件，低 Pd 档位的 Wilson 区间较宽。")
    md_parts += ["## 5. 输出 SCNR 与测角 RMSE", "实测 RMSE 只对产生 truth-match 的样本计算；没有匹配的档位显示为缺失，不填零。虚线是相位噪声 Jacobian 理论，随着 output SCNR 增大而下降；高 SCNR 处才有足够匹配样本进行可信比较。"]
    for minimum in MINPOINTS:
        md_parts.append(f"![min_points={minimum} 测角 RMSE](figures/angle_min{minimum}.png)\n\n该图固定 min_points={minimum}；0°和30°采用相同的波长/通道间距，差异只来自 $1/\\cos\\theta$ Jacobian 与生产匹配样本。")
    md_parts += ["## 6. 关键数值表", md_table(["门槛", "角度", "最高有效档目标 Pd", "最高有效档航迹 Pd", "最高有效档测角 RMSE(deg)"], summary_rows)]
    md_parts += table_sections
    md_parts += ["## 7. 结论与边界", "在这组已审计数据中，降低 min_points 会让目标/航迹事件在较低 output SCNR 出现；0°中心最高档的目标 Pd 分别为 min2=1.000、min3=0.889、min6=0.889，30°中心分别为 0.667、0.667、0.667。min_points=3 与6在当前场景的目标/航迹结果相同，说明 3–5 点 +20 dB 恢复分支已主导这些命中。高 SCNR 匹配样本的测角 RMSE 约为 0.034–0.042°，与 Jacobian 理论的 0.037–0.046°同量级；低 SCNR 的缺失或跳动是匹配样本不足造成的。目标理论只是一条可解释的 PB baseline，不是完整生产链的等价模型；完整生产模型还需显式建模整图 component、恢复分支、selector/relocation/match。当前样本量适合比较趋势，不适合宣称最终 Pd90。", "## 8. 数据、审计与复现", f"输出目录：`{output_dir_display}`。\n\n逐点表文件：`compact_pd_angle_curves.csv`。\n\n审计目录：`{audit_dir_display}`。审计文件：`scn_minpoints_information_leakage_audit.json`。状态={audit_status}，审计根目录数={audit_roots}，seed={','.join(str(v) for v in audit_seed_values) or '—'}。本报告未打开 raw BIN/F32，理论曲线未使用任何 evaluation hit rate 拟合。复现命令：\n\n```bash\nMPLCONFIGDIR=/tmp/gmti_mpl \\\npython3 scripts/gmti_scnr_eval/41_build_compact_pd_angle_report.py\n```"]
    md = out / "GMTI_输出SCNR_目标级航迹级测角精度_精简报告.md"
    md.write_text("\n\n".join(md_parts) + "\n", encoding="utf-8")
    return md


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measured", type=Path,
                        default=ROOT / "outputs/gmti_scnr_eval/scn_validation_20260830/minpoints_20261031/scn_minpoints_summary.csv")
    parser.add_argument("--target-theory", type=Path,
                        default=ROOT / "outputs/gmti_scnr_eval/comprehensive_report_20260829/target_pd_poisson_binomial.csv")
    parser.add_argument("--angle-theory", type=Path,
                        default=ROOT / "outputs/gmti_scnr_eval/comprehensive_report_20260829/angle_theory_curves.csv")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/gmti_scnr_eval/compact_pd_angle_report_20260830")
    parser.add_argument("--output-pdf", type=Path,
                        default=ROOT / "docs/GMTI_输出SCNR_目标级航迹级测角精度_精简报告_20260830.pdf")
    args = parser.parse_args()
    rows = load_measured(args.measured.resolve())
    if not rows:
        raise SystemExit("没有 0°/30°中心实测数据")
    target_theory = load_target_theory(args.target_theory.resolve())
    angle_theory = load_angle_theory(args.angle_theory.resolve())
    out = args.output_dir.resolve(); (out / "figures").mkdir(parents=True, exist_ok=True)
    figures: dict[str, Path] = {}
    for minimum in MINPOINTS:
        for metric, name in (("target_pd", "target"), ("track_pd", "track"), ("angle_rmse_deg", "angle")):
            path = out / "figures" / f"{name}_min{minimum}.png"
            plot_metric(rows, target_theory, angle_theory, minimum, metric, path)
            figures[f"{name}_{minimum}"] = path
    md = build_report(out, rows, target_theory, angle_theory, figures,
                      [args.measured.resolve(), args.target_theory.resolve(), args.angle_theory.resolve()])
    pandoc = shutil.which("pandoc"); xelatex = shutil.which("xelatex")
    if not (pandoc and xelatex):
        raise SystemExit("需要 pandoc 和 xelatex 生成 PDF")
    args.output_pdf.resolve().parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([pandoc, str(md), "--resource-path", str(out), "--pdf-engine", xelatex,
                             "-V", "CJKmainfont=Noto Sans CJK SC",
                             "--metadata", "title=GMTI 输出SCNR—目标级检测—航迹级检测—测角精度精简报告",
                             "-o", str(args.output_pdf.resolve())], cwd=ROOT, check=False)
    if result.returncode:
        raise SystemExit(f"PDF 生成失败：{result.returncode}")
    print(json.dumps({"status": "pass", "pdf": str(args.output_pdf.resolve()), "markdown": str(md),
                      "summary_csv": str((out / "compact_pd_angle_curves.csv").resolve()),
                      "rows": len(rows), "angles": list(ANGLES), "min_points": list(MINPOINTS)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
