#!/usr/bin/env python3
"""汇总同一 seed、同一 S+C+N 场景下 min_points=2/3/6 的生产结果。

本脚本只读取 ``16_run_standard_output_snr_mc.py`` 已保留的 CSV/JSON/XML 审计
结果，不打开 raw BIN/F32，也不重新拟合 output-SCNR 轴。三种 min_points 使用同一
seed、同一 0/30/45 度、同一 6 档×2 三屏组；因此差异可以归因于聚类门槛，而不是
背景 realization 或目标运动。主横轴是同档 `go_theory_vs_mc.csv` 提供的生产物理 CUT-equivalent
output SCNR；旧固定 3×3 支撑总 SCNR 保留为诊断列。输出独立图、逐档 CSV、审计 JSON 和 PDF/HTML。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
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
ANGLES = (0.0, 30.0, 45.0)
MINPOINTS = (2, 3, 6)
LABELS = {2: "min_points=2", 3: "min_points=3", 6: "min_points=6"}
ANGLE_LABELS = {0.0: "0°", 30.0: "30°", 45.0: "45°"}
COLORS = {0.0: "#1f77b4", 30.0: "#d98c00", 45.0: "#8c564b"}
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


def token(angle: float) -> str:
    return f"angle_{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def read_json(path: Path) -> dict[str, object]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def wilson(hits: int, trials: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if trials <= 0:
        return float("nan"), float("nan")
    p = hits / trials
    den = 1.0 + z * z / trials
    centre = (p + z * z / (2.0 * trials)) / den
    half = z * math.sqrt(max(0.0, p * (1.0 - p) / trials + z * z / (4.0 * trials * trials))) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def audit_root(root: Path, expected_min_points: int) -> dict[str, object]:
    root = root.resolve()
    manifests = sorted(root.rglob("pipeline_manifest.json"))
    angle_manifests = []
    active_truth: list[str] = []
    raw_remaining = sorted(str(p) for p in root.rglob("*.bin") if p.is_file())
    f32_remaining = sorted(str(p) for p in root.rglob("*.f32") if p.is_file())
    flags_ok = True
    for p in manifests:
        item = read_json(p)
        flags_ok = flags_ok and bool(item.get("raw_removed")) and bool(item.get("algorithm_output_bin_removed"))
        xml = Path(str(item.get("pipe_xml", "")))
        if xml.is_file():
            text = xml.read_text(encoding="utf-8", errors="replace")
            for tag in ("pc_peak_scene_truth", "debug_pc_peak"):
                match = re.search(fr"<{tag}>(.*?)</{tag}>", text, flags=re.S)
                value = match.group(1).strip() if match else ""
                if value and value not in {"0", "0.0", "false", "False"}:
                    active_truth.append(f"{xml}:{tag}")
        else:
            flags_ok = False
    for angle in ANGLES:
        path = root / token(angle) / "manifest.json"
        item = read_json(path)
        angle_manifests.append(item)
    strict_ok = bool(angle_manifests) and all(bool(x.get("strict_online_no_prior")) for x in angle_manifests)
    bg_ok = bool(angle_manifests) and all(x.get("background_profile") == "compact_statistical" for x in angle_manifests)
    pfa_ok = bool(angle_manifests) and all(abs(fnum(x.get("pfa")) - 1e-6) < 1e-12 for x in angle_manifests)
    min_ok = bool(angle_manifests) and all(inum(x.get("min_points"), -1) == expected_min_points for x in angle_manifests)
    status = "pass" if (len(manifests) == 12 and flags_ok and not raw_remaining and not f32_remaining
                         and not active_truth and strict_ok and bg_ok and pfa_ok and min_ok) else "fail"
    seeds = sorted({inum(x.get("seed"), -1) for x in angle_manifests})
    return {
        "root": str(root), "expected_min_points": expected_min_points, "status": status,
        "pipeline_count": len(manifests), "strict_online_no_prior": strict_ok,
        "background_profile_compact_statistical": bg_ok, "pfa_1e-6": pfa_ok,
        "min_points_expected": min_ok, "pipeline_raw_cleanup_flags": flags_ok,
        "truth_active_detector_count": len(active_truth), "diagnostic_truth_paths": active_truth,
        "raw_bin_remaining": raw_remaining, "f32_remaining": f32_remaining, "seed_values": seeds,
        "angle_manifest_count": len(angle_manifests),
    }


def load_root(root: Path, minimum: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    root_manifest = read_json(root / "run_manifest.json")
    seed_value = inum(root_manifest.get("seed"), -1)
    if seed_value < 0:
        manifests = [read_json(root / token(angle) / "manifest.json") for angle in ANGLES]
        seeds = [inum(x.get("seed"), -1) for x in manifests if inum(x.get("seed"), -1) >= 0]
        seed_value = seeds[0] if seeds else -1
    for angle in ANGLES:
        angle_dir = root / token(angle)
        curve = read_csv(angle_dir / "standard_mc_curve_summary.csv")
        theory = {inum(x.get("level_index")): x for x in read_csv(angle_dir / "go_theory_vs_mc.csv")}
        if len(curve) != 6:
            raise RuntimeError(f"{angle_dir} 曲线应有 6 行，实际 {len(curve)}")
        for item in curve:
            level = inum(item.get("level_index"), -1)
            t = theory.get(level, {})
            out: dict[str, object] = {"min_points": minimum, "angle_deg": angle, "level_index": level,
                                      "seed": seed_value}
            out.update(item)
            support_axis = fnum(item.get("support_output_scnr_db"), fnum(item.get("output_scnr_db")))
            cut_axis = fnum(item.get("output_scnr_cut_equiv_db"), fnum(t.get("unit_cut_scnr_db")))
            out["support_output_scnr_db"] = support_axis
            out["output_scnr_cut_equiv_db"] = cut_axis
            out["output_scnr_cut_equiv_db_std"] = fnum(item.get("output_scnr_cut_equiv_db_std"), 0.0)
            out["output_scnr_db"] = cut_axis if math.isfinite(cut_axis) else float("nan")
            for key in ("requested_output_scnr_db", "output_scnr_db", "output_scnr_db_std", "angle_rmse_deg", "angle_bias_deg",
                        "unit_exact_pd", "unit_resolution_pd", "cluster_pd", "target_pd", "track_pd",
                        "unit_exact_hits", "unit_exact_trials", "unit_resolution_hits", "unit_resolution_trials",
                        "cluster_hits", "cluster_trials", "target_hits", "target_trials", "track_hits", "track_trials"):
                out[key] = fnum(item.get(key))
            out["output_scnr_db"] = cut_axis if math.isfinite(cut_axis) else float("nan")
            out["theory_unit_pd_iid_go"] = fnum(t.get("unit_pd_iid_go"))
            out["theory_unit_pd_empirical_n_only"] = fnum(t.get("unit_pd_empirical_n_only"))
            out["theory_unit_cut_scnr_db"] = fnum(t.get("unit_cut_scnr_db"))
            out["go_alpha"] = fnum(t.get("alpha"))
            for field in ("unit_exact", "unit_resolution", "cluster", "target", "track"):
                h = inum(item.get(f"{field}_hits")); n = inum(item.get(f"{field}_trials"))
                lo, hi = wilson(h, n)
                out[f"{field}_wilson_low"] = lo; out[f"{field}_wilson_high"] = hi
            ch = inum(item.get("cluster_hits")); th = inum(item.get("target_hits"))
            out["postcluster_combined_retention"] = th / ch if ch else float("nan")
            rows.append(out)
    return rows


def aggregate_rows(seed_rows: list[list[dict[str, object]]], minimum: int) -> list[dict[str, object]]:
    """Pool equal angle/level rows across independent seeds.

    Hit/trial fields are summed (the estimand is the pooled event probability).
    The output-SCNR abscissa is pooled in the linear power domain, matching the
    multi-seed validator: ``10 log10(mean(10**(SCNR_dB/10)))``.  No hit rate is
    used to fit or repair the abscissa.
    """
    grouped: dict[tuple[float, int], list[dict[str, object]]] = {}
    for rows in seed_rows:
        for row in rows:
            key = (fnum(row.get("angle_deg")), inum(row.get("level_index"), -1))
            grouped.setdefault(key, []).append(row)
    hit_fields = ("unit_exact", "unit_resolution", "cluster", "target", "track")
    mean_fields = ("input_snr_db_control", "requested_output_scnr_db", "theory_unit_pd_iid_go",
                   "theory_unit_pd_empirical_n_only", "theory_unit_cut_scnr_db", "go_alpha", "p0_mean_linear")
    out_rows: list[dict[str, object]] = []
    for (angle, level), items in sorted(grouped.items()):
        out: dict[str, object] = {"min_points": minimum, "angle_deg": angle,
                                  "level_index": level, "seed_count": len(items),
                                  "seed_values": ",".join(str(inum(x.get("seed"), -1)) for x in items
                                                             if math.isfinite(fnum(x.get("seed"))))}
        # The per-seed ``load_root`` rows carry the seed only in their parent
        # audit, so leave an explicit count even when the optional values are
        # absent.  Values below are all deterministic reductions of retained
        # summary CSVs.
        for field in mean_fields:
            values = np.asarray([fnum(x.get(field)) for x in items], dtype=float)
            values = values[np.isfinite(values)]
            out[field] = float(np.mean(values)) if values.size else float("nan")
        axis_db = np.asarray([fnum(x.get("output_scnr_db")) for x in items], dtype=float)
        axis_db = axis_db[np.isfinite(axis_db)]
        if axis_db.size:
            out["output_scnr_db"] = float(10.0 * np.log10(np.mean(np.power(10.0, axis_db / 10.0))))
            within = np.asarray([fnum(x.get("output_scnr_cut_equiv_db_std"), 0.0) for x in items], dtype=float)
            within = within[np.isfinite(within)]
            out["output_scnr_db_std"] = float(np.sqrt(np.mean(np.square(within)) + np.var(axis_db))) if within.size else float(np.std(axis_db))
        else:
            out["output_scnr_db"] = float("nan"); out["output_scnr_db_std"] = float("nan")
        out["output_scnr_cut_equiv_db"] = out["output_scnr_db"]
        support_axis_db = np.asarray([fnum(x.get("support_output_scnr_db")) for x in items], dtype=float)
        support_axis_db = support_axis_db[np.isfinite(support_axis_db)]
        out["support_output_scnr_db"] = (float(10.0 * np.log10(np.mean(np.power(10.0, support_axis_db / 10.0))))
                                         if support_axis_db.size else float("nan"))
        out["support_output_scnr_db_std"] = float(np.std(support_axis_db, ddof=1)) if support_axis_db.size > 1 else 0.0
        for field in hit_fields:
            hits = sum(inum(x.get(f"{field}_hits")) for x in items)
            trials = sum(inum(x.get(f"{field}_trials")) for x in items)
            out[f"{field}_hits"] = hits; out[f"{field}_trials"] = trials
            out[f"{field}_pd"] = hits / trials if trials else float("nan")
            lo, hi = wilson(hits, trials)
            out[f"{field}_wilson_low"] = lo; out[f"{field}_wilson_high"] = hi
        # Angle RMSE is pooled from the underlying matched counts, not averaged
        # over only the seeds that happened to produce a match.
        matched = sum(inum(x.get("angle_matched")) for x in items)
        out["angle_matched"] = matched
        sq = sum((fnum(x.get("angle_rmse_deg")) ** 2) * inum(x.get("angle_matched"))
                 for x in items if math.isfinite(fnum(x.get("angle_rmse_deg"))))
        bias_num = sum(fnum(x.get("angle_bias_deg")) * inum(x.get("angle_matched"))
                       for x in items if math.isfinite(fnum(x.get("angle_bias_deg"))))
        out["angle_rmse_deg"] = math.sqrt(sq / matched) if matched and sq >= 0 else float("nan")
        out["angle_bias_deg"] = bias_num / matched if matched else float("nan")
        out["postcluster_combined_retention"] = (out["target_hits"] / out["cluster_hits"]
                                                   if out["cluster_hits"] else float("nan"))
        out_rows.append(out)
    return out_rows


def table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---:" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
    return "\n".join(lines)


def plot_metric(rows: list[dict[str, object]], minimum: int, field: str, ylabel: str,
                out: Path, theory: bool = False, ylim: tuple[float, float] | None = (0.0, 1.05)) -> None:
    fig, ax = plt.subplots(figsize=(8.4, 5.0))
    selected_min = [x for x in rows if inum(x.get("min_points"), -1) == minimum]
    for angle in ANGLES:
        selected = sorted([x for x in selected_min if abs(fnum(x.get("angle_deg")) - angle) < 1e-6],
                          key=lambda x: fnum(x.get("output_scnr_db")))
        if not selected:
            continue
        x = np.asarray([fnum(z.get("output_scnr_db")) for z in selected])
        y = np.asarray([fnum(z.get(field)) for z in selected])
        good = np.isfinite(x) & np.isfinite(y)
        if not np.any(good):
            continue
        color = COLORS[angle]
        ax.plot(x[good], y[good], "o-", color=color, markerfacecolor="white", label=ANGLE_LABELS[angle])
        if field.endswith("_pd"):
            prefix = field[:-3]
            lo = np.asarray([fnum(z.get(f"{prefix}_wilson_low")) for z in selected])[good]
            hi = np.asarray([fnum(z.get(f"{prefix}_wilson_high")) for z in selected])[good]
            ax.fill_between(x[good], np.where(np.isfinite(lo), lo, y[good]), np.where(np.isfinite(hi), hi, y[good]), color=color, alpha=.13, linewidth=0)
        if theory and field in {"unit_exact_pd", "unit_resolution_pd"}:
            ty = np.asarray([fnum(z.get("theory_unit_pd_empirical_n_only")) for z in selected])
            mask = np.isfinite(x) & np.isfinite(ty)
            if np.any(mask):
                ax.plot(x[mask], ty[mask], "--", color=color, alpha=.65, linewidth=1.2, label=f"{ANGLE_LABELS[angle]} C+N理论")
    ax.set_xlabel("CUT-equivalent 输出 SCNR（dB；生产物理 CUT）")
    ax.set_ylabel(ylabel)
    ax.set_title(f"S+C+N：{LABELS[minimum]} 的 {ylabel}")
    ax.grid(True, color="#d9dde3", alpha=.7); ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    if ylim is not None: ax.set_ylim(*ylim)
    ax.legend(fontsize=8, ncol=2); fig.tight_layout(); fig.savefig(out, dpi=220); fig.savefig(out.with_suffix(".pdf")); plt.close(fig)


def build_markdown(out: Path, rows: list[dict[str, object]], audits: list[dict[str, object]], figures: dict[str, Path], roots: dict[int, list[Path]]) -> Path:
    def row_at(minimum: int, angle: float, level: int) -> dict[str, object] | None:
        for item in rows:
            if (inum(item.get("min_points"), -1) == minimum
                    and abs(fnum(item.get("angle_deg")) - angle) < 1e-6
                    and inum(item.get("level_index"), -1) == level):
                return item
        return None

    seed_values = sorted({inum(s, -1) for a in audits for s in a.get("seed_values", []) if inum(s, -1) >= 0})
    seed_text = ", ".join(str(x) for x in seed_values) if seed_values else "未记录"
    detail: list[list[object]] = []
    for r in rows:
        detail.append([LABELS[inum(r.get("min_points"))], ANGLE_LABELS[fnum(r.get("angle_deg"))], inum(r.get("level_index")),
                       inum(r.get("seed_count"), 1), fmt(r.get("output_scnr_db"), 2), f"{inum(r.get('unit_exact_hits'))}/{inum(r.get('unit_exact_trials'))}",
                       f"{inum(r.get('unit_resolution_hits'))}/{inum(r.get('unit_resolution_trials'))}",
                       f"{inum(r.get('cluster_hits'))}/{inum(r.get('cluster_trials'))}",
                       f"{inum(r.get('target_hits'))}/{inum(r.get('target_trials'))}",
                       f"{inum(r.get('track_hits'))}/{inum(r.get('track_trials'))}", fmt(r.get("angle_rmse_deg"), 3)])
    sections = [
        "# GMTI 真实 S+C+N：min_points=2/3/6 同场景验证",
        f"本补充报告汇总 {len(seed_values)} 个独立 seed（{seed_text}）的同一紧凑统计 S+C+N 场景、同一 0°/30°/45°波位和同一 6 档×2 三屏组；每个 seed 内仅改变生产 XML 的 min_points。这样把聚类门槛影响与背景、目标运动和 output-SCNR 轴隔离开。主横轴是配对 S+C+N/C+N 得到的物理 CUT-equivalent output SCNR，固定 3×3 支撑总量只作诊断列，并按线性功率域跨 seed 聚合；`target_snr_db` 只控制输入档位。",
        "## 1. 参数、样本与审计",
        table(["项目", "取值", "含义"], [
            ["背景", "Rayleigh×lognormal 面杂波 + 热噪声 + 目标", "240 个 area scatterers；强散射/线散射关闭"],
            ["GO-CFAR", "Pfa=1e-6，GO，guard=4，background=16", "alpha约13.4495103；三种 min_points 共用"],
            ["聚类", "2、3、6 分别运行；small branch=2/3 点，峰值+20 dB", "生产 joint hit mask、连通性、truth match"],
            ["每根运行", "3 角度×6 档×2 三屏组", "每角度 36 periods、12 screen events、4 track events"],
            ["严格规则", "strict_online_no_prior=true", "truth 只用于离线支撑/评分；GO+TrackManager 审计后清理 raw"],
        ]) + "\n\n" + table(["min_points", "seed", "root", "status", "pipeline", "在线 truth", "raw BIN", "F32"],
              [[a["expected_min_points"], ",".join(str(x) for x in a.get("seed_values", [])), Path(str(a["root"])).name,
                a["status"], a["pipeline_count"], a["truth_active_detector_count"], len(a["raw_bin_remaining"]), len(a["f32_remaining"])] for a in audits]),
        f"本次共审计 {len(audits)} 个 root；每个 root 要求 12 条 pipeline、在线 truth=0、raw BIN/F32=0；任一项不满足就不进入汇总。"
        f"每个 seed 的每个 min_points 在每个角度有 36 periods、12 个 screen event、4 个三屏选二 track event；"
        f"{len(seed_values)} 个 seed pooled 后，每个角度为 {36 * len(seed_values)} periods、{12 * len(seed_values)} 个 screen event、{4 * len(seed_values)} 个 track event。",
        "## 2. 事件定义与理论对照",
        r"单元 exact 事件是生产物理 truth CUT 是否满足 $P_{CUT}>\alpha M$；resolution 事件是在 truth 周围 ±2 个 range/Doppler bin 内是否至少有 GO hit。cluster 事件由整幅 joint hit mask 的连通 component、min_points 和 3–5 点/小簇峰值条件决定；target 事件使用同帧、有界 Doppler/range 的 production truth match，并再经过 cluster gate（因此理论链路满足 $P_{target}\le P_{cluster}$）；被 gate 拒绝的 raw match 保留在逐周期审计字段中。航迹主指标是同一 truth 轨迹三屏至少两屏命中：$P_{track}=P_{12}+P_{13}+P_{23}-2P_{123}$，独立式 $3p^2-2p^3$ 只做参考。",
        r"理论虚线来自同一根运行的 `go_theory_vs_mc.csv`：对 C+N-only 训练窗逐窗平均 $Q_1(\sqrt{2\gamma_{CUT}},\sqrt{2\alpha m_r})$；没有读 S+C+N 命中率，也没有反向拟合。",
    ]
    repair_rows = [[Path(str(a["root"])).name, a.get("target_gate_repair_changed", 0),
                    a.get("target_gate_repair_before", 0), a.get("target_gate_repair_after", 0)] for a in audits]
    sections.append("## 2.0 target gate 一致性修复\n旧的 local-test writeresults 可能携带跨周期 target_id；仅按 range/ID 过滤会把 Doppler 远端峰误算为 target match。现行规则要求同 beam、range<=2 且 Doppler 环形距离<=2，并且 target 还必须通过本周期 cluster gate。对仍保留 period CSV 的 root，下表给出离线修复前后 target 事件数；已按运行时规则完成门控且随后清理逐周期 CSV 的 root，target 前值标为“—”，target 后取最终汇总总数，避免把“没有可回溯的前值”误写成 0。所有修复只读取 CSV/JSON，未打开 raw BIN/F32。\n\n" + table(["root", "改动行数", "target前", "target后"], repair_rows))
    # 这一段专门解释曲线中最容易被误读的“非单调”点。理论的输入和主横轴
    # 都是物理 truth CUT 的 gamma；旧固定 3x3 支撑积分只在审计列中保留。
    r0 = row_at(6, 0.0, 5)
    if r0 is not None:
        sections.append(
            "## 2.1 逐档差异的数值解释（不是把偏差藏进平滑）\n"
            f"0°第 5 档是一个有意保留的诊断例子：横轴是 CUT-equivalent output SCNR "
            f"{fmt(r0.get('output_scnr_db'), 2)} dB；同一档 `unit_cut_scnr_db` 与主横轴相同，因此 C+N 经验 GO 理论给 "
            f"Pd={fmt(r0.get('theory_unit_pd_empirical_n_only'), 4)}；实测 physical-CUT exact 为 "
            f"{inum(r0.get('unit_exact_hits'))}/{inum(r0.get('unit_exact_trials'))}，而 ±2-bin "
            f"resolution 为 {inum(r0.get('unit_resolution_hits'))}/{inum(r0.get('unit_resolution_trials'))}。"
            "这不是把 CFAR Pfa 或 alpha 改坏了，而是固定物理 CUT 与 3×3 主瓣支撑积分在该档落到了不同的 range/Doppler bin："
            "目标能量仍在邻近支撑单元内，所以 resolution 可以为 1，而 exact CUT 仍可能为 0。"
        )
    gap_rows: list[list[object]] = []
    for angle in ANGLES:
        subset = [x for x in rows if inum(x.get('min_points'), -1) == 6
                  and abs(fnum(x.get('angle_deg')) - angle) < 1e-6]
        exact_gap = [abs(fnum(x.get('unit_exact_pd')) - fnum(x.get('theory_unit_pd_empirical_n_only'))) for x in subset]
        resolution_gap = [abs(fnum(x.get('unit_resolution_pd')) - fnum(x.get('theory_unit_pd_empirical_n_only'))) for x in subset]
        gap_rows.append([ANGLE_LABELS[angle], fmt(np.mean(exact_gap), 3), fmt(np.mean(resolution_gap), 3),
                         fmt(np.max(exact_gap), 3), fmt(np.max(resolution_gap), 3)])
    sections.append(
        "为避免只凭图形下结论，表中给出 min_points=6、同一 seed 下每个角度六档的绝对差："
        "exact 与理论比较的是同一个 physical CUT 事件；resolution 则允许 ±2 个 bin，"
        "所以后者更接近目标主瓣支撑的生产检测定义。0° 的 resolution 平均差被上面的第 5 档拉大；"
        "30° 两种定义都较接近；45° 的 exact 差较大而 resolution 差较小，说明主要是波位边缘的能量落 bin/偏移，"
        "而不是 GO 门限本身失效。\n\n" +
        table(["角度", "exact 平均|差|", "resolution 平均|差|", "exact 最大|差|", "resolution 最大|差|"], gap_rows)
    )
    for minimum in MINPOINTS:
        sections.append(f"## 3.{minimum} min_points={minimum}：独立曲线与逐档表")
        sections.append(f"该组图只显示 min_points={minimum}，不与另外两个门槛叠画；彩色线是三角度实测，浅色带为 Wilson 95% 区间，单元图虚线是对应 C+N 经验 GO 理论。")
        for metric, label in (("unit_exact", "单元 exact Pd"), ("unit_resolution", "单元 ±2-bin Pd"), ("cluster", "cluster Pd"), ("target", "target Pd"), ("track", "三屏选二 track Pd"), ("angle", "测角 RMSE")):
            key = f"{minimum}_{metric}"
            if key in figures:
                sections.append(f"![{LABELS[minimum]} {label}。](figures/{figures[key].name})")
                sections.append(f"图中每个点的分子/分母来自 `standard_mc_curve_summary.csv`；{label} 不经过平滑。对 unit 曲线，虚线使用同档 `unit_cut_scnr_db` 代入 GO 的 Marcum-$Q$ 经验积分；对 cluster/target/track/RMSE，图只展示生产事件或 truth-match 统计，不把单元独立性假设当成实测。")
        sub = [r for r in rows if inum(r.get("min_points"), -1) == minimum]
        sections.append(table(["角度", "level", "seed数", "output SCNR", "exact", "resolution", "cluster", "target", "track", "RMSE(deg)"],
                              [[ANGLE_LABELS[fnum(r.get("angle_deg"))], inum(r.get("level_index")), inum(r.get("seed_count"), 1), fmt(r.get("output_scnr_db"), 2),
                                f"{inum(r.get('unit_exact_hits'))}/{inum(r.get('unit_exact_trials'))}", f"{inum(r.get('unit_resolution_hits'))}/{inum(r.get('unit_resolution_trials'))}",
                                f"{inum(r.get('cluster_hits'))}/{inum(r.get('cluster_trials'))}", f"{inum(r.get('target_hits'))}/{inum(r.get('target_trials'))}",
                                f"{inum(r.get('track_hits'))}/{inum(r.get('track_trials'))}", fmt(r.get("angle_rmse_deg"), 3)] for r in sub]))
    sections.append("## 4. 门槛差异的直接判读")
    comparison: list[list[object]] = []
    for angle in ANGLES:
        for level in range(6):
            vals = [(inum(r.get("min_points")), r) for r in rows if abs(fnum(r.get("angle_deg")) - angle) < 1e-6 and inum(r.get("level_index")) == level]
            by = {m: r for m, r in vals}
            if len(by) == 3:
                comparison.append([ANGLE_LABELS[angle], level, fmt(by[2].get("output_scnr_db"), 2), fmt(by[2].get("target_pd"), 3), fmt(by[3].get("target_pd"), 3), fmt(by[6].get("target_pd"), 3), fmt(fnum(by[2].get("target_pd")) - fnum(by[6].get("target_pd")), 3)])
    sections.append("目标级差异沿用同一 SCNR 档、同一背景 realization 解释；表中分子/分母已跨 seed 求和，横坐标是线性功率域 pooled output SCNR。若 min_points=2/3 在较低 SCNR 先出现 cluster/target 命中而 min_points=6 仍为 0，说明窄主瓣支撑被高门槛截断；这支持‘min_points=6 可能过于苛刻’的诊断，但不等于建议直接上线 min2：假警和误聚类必须在独立无目标背景批次中复核。")
    sections.append(table(["角度", "level", "output SCNR", "target Pd m2", "target Pd m3", "target Pd m6", "m2−m6"], comparison))
    sections.append("## 5. 信息边界与复现")
    cmd_lines = ["python3 scripts/gmti_scnr_eval/38_build_scn_minpoints_validation.py \\"]
    for minimum in MINPOINTS:
        paths = roots.get(minimum, []) if isinstance(roots, dict) else []
        for idx, path in enumerate(paths):
            is_last = minimum == MINPOINTS[-1] and idx == len(paths) - 1
            suffix = "" if is_last else " " + "\\"
            cmd_lines.append(f"  --root-{minimum} {path}" + suffix)
    cmd_lines.extend([
        "  --output-dir outputs/gmti_scnr_eval/scn_validation_20260830/minpoints_20261031",
        "  --output-pdf docs/GMTI_S+C+N_minpoints_2_3_6_验证报告_20260830.pdf",
        "  --output-html docs/GMTI_S+C+N_minpoints_2_3_6_验证报告_20260830.html",
    ])
    sections.append("每个 root 只保留 `standard_mc_curve_summary.csv`、`standard_mc_screen_metrics.csv`、`go_theory_vs_mc.csv`、manifest 和必要的配置审计；逐周期 debug/F32 与 raw BIN 已清理。汇总脚本只读取这些保留文件。最短复现入口：\n\n```bash\n" + "\n".join(cmd_lines) + "\n```\n\n本批使用有限独立 seed，Wilson 区间仍较宽；它用于补齐 S+C+N 下 min_points 门槛的方向性证据，不宣称最终 Pd90。")
    path = out / "GMTI_S+C+N_minpoints_2_3_6_验证报告.md"
    path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-2", type=Path, action="append", required=True,
                        help="min_points=2 root；可重复传入多个独立 seed")
    parser.add_argument("--root-3", type=Path, action="append", required=True,
                        help="min_points=3 root；可重复传入多个独立 seed")
    parser.add_argument("--root-6", type=Path, action="append", required=True,
                        help="min_points=6 root；可重复传入多个独立 seed")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-pdf", type=Path, required=True)
    parser.add_argument("--output-html", type=Path, required=True)
    args = parser.parse_args()
    roots = {2: [p.resolve() for p in args.root_2], 3: [p.resolve() for p in args.root_3],
             6: [p.resolve() for p in args.root_6]}
    audits: list[dict[str, object]] = []
    for minimum in MINPOINTS:
        for root in roots[minimum]:
            audit = audit_root(root, minimum)
            audit["minimum"] = minimum
            repair = read_json(root / "target_gate_repair.json")
            if repair and "target_events_before" in repair and "target_events_after" in repair:
                audit["target_gate_repair_changed"] = inum(repair.get("period_rows_changed"), 0)
                audit["target_gate_repair_before"] = inum(repair.get("target_events_before"), 0)
                audit["target_gate_repair_after"] = inum(repair.get("target_events_after"), 0)
                audit["target_gate_repair_source"] = "retained period CSV offline repair"
            else:
                # New formal roots apply the bounded-Doppler/range + cluster
                # gate inside the production extraction loop.  Their
                # per-period CSV is intentionally removed after audit, so the
                # pre-gate count is not observable and must not be reported as
                # zero.  Use the retained six-row curve summaries for the
                # post-gate total only.
                final_events = 0
                for angle in ANGLES:
                    curve_path = root / token(angle) / "standard_mc_curve_summary.csv"
                    if curve_path.is_file():
                        final_events += sum(inum(row.get("target_hits"), 0) for row in read_csv(curve_path))
                audit["target_gate_repair_changed"] = 0
                audit["target_gate_repair_before"] = "—"
                audit["target_gate_repair_after"] = final_events
                audit["target_gate_repair_source"] = "runtime bounded+cluster gate (period CSV cleaned)"
            audits.append(audit)
    if any(a["status"] != "pass" for a in audits):
        raise SystemExit("至少一根 S+C+N min_points 运行未通过严格审计")
    seed_sets = {minimum: sorted({s for root in roots[minimum]
                                  for a in [audit_root(root, minimum)] for s in a["seed_values"]})
                 for minimum in MINPOINTS}
    if any(not values for values in seed_sets.values()) or len({tuple(values) for values in seed_sets.values()}) != 1:
        raise SystemExit(f"min_points 三组必须使用相同 seed 集合，实际为 {seed_sets}")
    rows: list[dict[str, object]] = []
    for m in MINPOINTS:
        rows.extend(aggregate_rows([load_root(root, m) for root in roots[m]], m))
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=True); (out / "figures").mkdir(exist_ok=True)
    fields = ["min_points", "angle_deg", "level_index", "requested_output_scnr_db", "output_scnr_db", "output_scnr_cut_equiv_db", "support_output_scnr_db", "output_scnr_db_std", "output_scnr_cut_equiv_db_std",
              "unit_exact_hits", "unit_exact_trials", "unit_exact_pd", "unit_resolution_hits", "unit_resolution_trials", "unit_resolution_pd",
              "cluster_hits", "cluster_trials", "cluster_pd", "target_hits", "target_trials", "target_pd", "postcluster_combined_retention",
              "track_hits", "track_trials", "track_pd", "angle_matched", "angle_rmse_deg", "angle_bias_deg", "theory_unit_pd_iid_go",
              "theory_unit_pd_empirical_n_only", "theory_unit_cut_scnr_db", "go_alpha", "seed_count", "seed_values"]
    with (out / "scn_minpoints_summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for row in rows: writer.writerow({k: row.get(k, "") for k in fields})
    (out / "scn_minpoints_information_leakage_audit.json").write_text(json.dumps({"status": "pass", "audits": audits, "roots": {str(k): [str(v) for v in values] for k, values in roots.items()}, "seeds": next(iter(seed_sets.values())), "output_axis": "measured S+C+N physical CUT increment over C+N CUT P0 (CUT-equivalent output SCNR); fixed 3x3 support retained as diagnostic; pooled across seeds in linear power", "evaluation_hit_rates_used_for_fit": False, "raw_bin_opened": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    figures: dict[str, Path] = {}
    metrics = [("unit_exact", "单元 exact Pd", True, (0.0, 1.05)), ("unit_resolution", "单元 ±2-bin Pd", True, (0.0, 1.05)),
               ("cluster", "cluster Pd", False, (0.0, 1.05)), ("target", "target Pd", False, (0.0, 1.05)),
               ("track", "三屏选二 track Pd", False, (0.0, 1.05)), ("angle", "测角 RMSE（deg）", False, None)]
    for m in MINPOINTS:
        for metric, label, theory, ylim in metrics:
            field = "angle_rmse_deg" if metric == "angle" else f"{metric}_pd"
            path = out / "figures" / f"scn_minpoints_{m}_{metric}.png"
            plot_metric(rows, m, field, label, path, theory=theory, ylim=ylim); figures[f"{m}_{metric}"] = path
    md = build_markdown(out, rows, audits, figures, roots)
    pandoc = shutil.which("pandoc"); xelatex = shutil.which("xelatex")
    args.output_pdf.parent.mkdir(parents=True, exist_ok=True); args.output_html.parent.mkdir(parents=True, exist_ok=True)
    if pandoc and xelatex:
        cmd = [pandoc, str(md), "--resource-path", str(out), "--pdf-engine", xelatex,
               "-V", "CJKmainfont=Noto Sans CJK SC", "--metadata", "title=GMTI 真实 S+C+N：min_points=2/3/6 同场景验证",
               "-o", str(args.output_pdf.resolve())]
        result = subprocess.run(cmd, cwd=ROOT, check=False)
        if result.returncode:
            raise SystemExit(f"PDF 生成失败：{result.returncode}")
    if pandoc:
        result = subprocess.run([pandoc, str(md), "--standalone", "--resource-path", str(out), "--self-contained", "--mathml",
                                 "--metadata", "title=GMTI 真实 S+C+N：min_points=2/3/6 同场景验证", "-o", str(args.output_html.resolve())], cwd=ROOT, check=False)
        if result.returncode:
            raise SystemExit(f"HTML 生成失败：{result.returncode}")
    manifest = {"status": "pass", "seeds": next(iter(seed_sets.values())), "min_points": list(MINPOINTS), "angles": list(ANGLES), "rows": len(rows), "audits": audits, "summary_csv": str((out / "scn_minpoints_summary.csv").resolve()), "information_leakage_audit": str((out / "scn_minpoints_information_leakage_audit.json").resolve()), "output_axis": "measured S+C+N physical CUT increment over C+N CUT P0 (CUT-equivalent output SCNR); fixed 3x3 support retained as diagnostic; pooled across seeds in linear power", "reproduction": "python3 scripts/gmti_scnr_eval/38_build_scn_minpoints_validation.py"}
    (out / "scn_minpoints_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "pass", "output_dir": str(out), "pdf": str(args.output_pdf.resolve()), "html": str(args.output_html.resolve()), "rows": len(rows), "seeds": next(iter(seed_sets.values()))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
