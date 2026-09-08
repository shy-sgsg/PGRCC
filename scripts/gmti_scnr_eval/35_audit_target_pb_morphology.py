#!/usr/bin/env python3
"""Audit the structural gap between the target PB baseline and production events.

This is a read-only audit of retained compact CSVs.  It deliberately does not
open raw BIN/maps and does not fit any curve to evaluation hit rates.  The
historical joint-mask file is labelled non-strict because its parent run did
not carry the later ``strict_online_no_prior`` manifest flag; it is used only
to explain morphology, not as a formal Pd result.
"""

from __future__ import annotations

import argparse
import json
import math
import re
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
from scnr_eval_lib import read_csv, write_csv  # noqa: E402


HISTORICAL_PATH = ROOT / "outputs/gmti_scnr_eval/target_track_analysis_0deg_20260827/target_track_period_audit.csv"
PB_PATH = ROOT / "outputs/gmti_scnr_eval/comprehensive_report_20260829/target_pd_poisson_binomial.csv"
CURRENT_ROOTS = {
    2: ROOT / "outputs/gmti_scnr_eval/target_minpoints_corrected_20260828/min2_formal",
    3: ROOT / "outputs/gmti_scnr_eval/target_minpoints_corrected_20260828/min3_formal",
    6: ROOT / "outputs/gmti_scnr_eval/target_minpoints_corrected_20260828/min6_formal",
}

FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if FONT.is_file():
    font_manager.fontManager.addfont(str(FONT))
    plt.rcParams["font.family"] = "Noto Sans CJK JP"
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams.update({"font.size": 9, "axes.grid": True, "grid.alpha": 0.25,
                     "savefig.dpi": 220, "figure.dpi": 150})


def fnum(value: object, default: float = float("nan")) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def inum(value: object, default: int = 0) -> int:
    value = fnum(value, float(default))
    return int(round(value)) if math.isfinite(value) else default


def frac(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else float("nan")


def interp_pb(pb_rows: list[dict[str, str]], minimum: int,
              output_db: float) -> float:
    selected = [r for r in pb_rows
                if r.get("angle_group") == "0deg_center"
                and inum(r.get("min_points")) == minimum]
    selected = sorted((r for r in selected
                       if math.isfinite(fnum(r.get("output_scnr_db")))
                       and math.isfinite(fnum(r.get("pd_cluster_pb_profile")))),
                      key=lambda r: fnum(r.get("output_scnr_db")))
    if not selected or not math.isfinite(output_db):
        return float("nan")
    return float(np.interp(output_db,
                           [fnum(r.get("output_scnr_db")) for r in selected],
                           [fnum(r.get("pd_cluster_pb_profile")) for r in selected]))


def build_historical(pb_rows: list[dict[str, str]]) -> tuple[list[dict[str, object]], dict[str, int]]:
    if not HISTORICAL_PATH.is_file():
        return [], {"rows": 0}
    raw = read_csv(HISTORICAL_PATH)
    mask_columns = [f"mask_{row}_{col}" for row in range(3) for col in range(5)]
    groups: dict[int, list[dict[str, object]]] = {}
    for source in raw:
        level = inum(source.get("level_index"), -1)
        if level < 0:
            continue
        mask_count = sum(1 for key in mask_columns if inum(source.get(key)) > 0)
        item = {
            "output_scnr_db": fnum(source.get("output_scnr_db")),
            "mask_hit_count": mask_count,
            "component_size": inum(source.get("component_size")),
            "cluster_min2": inum(source.get("cluster_min2")),
            "cluster_min3": inum(source.get("cluster_min3")),
            "cluster_min6": inum(source.get("cluster_min6")),
            "small_recovery": inum(source.get("cluster_small_recovery")),
            "production": inum(source.get("cluster_production")),
            "target": inum(source.get("target_event")),
        }
        groups.setdefault(level, []).append(item)

    output: list[dict[str, object]] = []
    for level in sorted(groups):
        values = groups[level]
        n = len(values)
        output_db = np.asarray([fnum(v["output_scnr_db"]) for v in values], dtype=float)
        output_db = output_db[np.isfinite(output_db)]
        mask = np.asarray([inum(v["mask_hit_count"]) for v in values], dtype=float)
        component = np.asarray([inum(v["component_size"]) for v in values], dtype=float)
        min2 = np.asarray([inum(v["cluster_min2"]) for v in values], dtype=float)
        min3 = np.asarray([inum(v["cluster_min3"]) for v in values], dtype=float)
        min6 = np.asarray([inum(v["cluster_min6"]) for v in values], dtype=float)
        small = np.asarray([inum(v["small_recovery"]) for v in values], dtype=float)
        production = np.asarray([inum(v["production"]) for v in values], dtype=float)
        target = np.asarray([inum(v["target"]) for v in values], dtype=float)
        prod_n = int(np.sum(production > 0))
        full_ge6_n = int(np.sum((production > 0) & (component >= 6)))
        small_prod_n = int(np.sum((production > 0) & (small > 0)))
        local_lt6_prod_n = int(np.sum((production > 0) & (mask < 6)))
        pb = {m: interp_pb(pb_rows, m, float(np.mean(output_db)) if output_db.size else float("nan"))
              for m in (2, 3, 6)}
        output.append({
            "source_scope": "historical_0deg_20260827_non_strict_morphology_only",
            "level_index": level,
            "output_scnr_db_mean": float(np.mean(output_db)) if output_db.size else float("nan"),
            "output_scnr_db_std": float(np.std(output_db, ddof=1)) if output_db.size > 1 else 0.0,
            "event_count": n,
            "local_3x5_hit_mean": float(np.mean(mask)),
            "local_3x5_ge6_rate": float(np.mean(mask >= 6)),
            "full_component_size_mean": float(np.mean(component)),
            "full_component_ge6_rate": float(np.mean(component >= 6)),
            "cluster_min2_rate": float(np.mean(min2 > 0)),
            "cluster_min3_rate": float(np.mean(min3 > 0)),
            "cluster_min6_rate": float(np.mean(min6 > 0)),
            "small_recovery_rate": float(np.mean(small > 0)),
            "production_cluster_rate": float(np.mean(production > 0)),
            "target_rate": float(np.mean(target > 0)),
            "production_events": prod_n,
            "production_small_branch_fraction": frac(small_prod_n, prod_n),
            "production_full_ge6_fraction": frac(full_ge6_n, prod_n),
            "production_local_lt6_fraction": frac(local_lt6_prod_n, prod_n),
            "production_target_mismatch_rate": float(np.mean((production > 0) != (target > 0))),
            "pb_min2_profile": pb[2],
            "pb_min3_profile": pb[3],
            "pb_min6_profile": pb[6],
            "pb_min6_minus_production": pb[6] - float(np.mean(production > 0)),
            "note": "仅解释形态；历史父运行未标记 strict_online_no_prior，不作为正式Pd",
        })
    total = len(raw)
    prod = sum(inum(x.get("cluster_production")) > 0 for x in raw)
    small_prod = sum(inum(x.get("cluster_production")) > 0 and inum(x.get("cluster_small_recovery")) > 0 for x in raw)
    full_prod = sum(inum(x.get("cluster_production")) > 0 and inum(x.get("component_size")) >= 6 for x in raw)
    mask_prod = []
    for x in raw:
        if inum(x.get("cluster_production")) <= 0:
            continue
        mask_prod.append(sum(inum(x.get(key)) > 0 for key in mask_columns) < 6)
    return output, {"rows": total, "production_events": prod,
                    "production_small_branch_events": small_prod,
                    "production_full_ge6_events": full_prod,
                    "production_local_lt6_events": int(sum(mask_prod))}


def parse_angle(path: Path) -> float:
    for part in path.parts:
        match = re.fullmatch(r"angle_p(\d+)p000", part)
        if match:
            return float(match.group(1))
    return float("nan")


def build_current() -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for minimum, root in CURRENT_ROOTS.items():
        for path in sorted(root.rglob("standard_mc_period_metrics.csv")):
            angle = parse_angle(path)
            rows = read_csv(path)
            groups: dict[int, list[dict[str, str]]] = {}
            for row in rows:
                groups.setdefault(inum(row.get("input_snr_db_index"), -1), []).append(row)
            for level, values in sorted(groups.items()):
                if level < 0 or not values:
                    continue
                component = np.asarray([inum(v.get("unit_component_size")) for v in values], dtype=float)
                cluster = np.asarray([inum(v.get("cluster_event")) > 0 for v in values])
                target = np.asarray([inum(v.get("target_event")) > 0 for v in values])
                unit = np.asarray([inum(v.get("unit_exact")) > 0 for v in values])
                out_db = np.asarray([fnum(v.get("output_scnr_db")) for v in values], dtype=float)
                small_proxy = cluster & (component >= 3) & (component < minimum)
                output.append({
                    "source_scope": "current_strict_formal_compact_period_metrics",
                    "min_points": minimum,
                    "angle_deg": angle,
                    "level_index": level,
                    "output_scnr_db_mean": float(np.nanmean(out_db)),
                    "output_scnr_db_std": float(np.nanstd(out_db, ddof=1)) if len(values) > 1 else 0.0,
                    "event_count": len(values),
                    "unit_exact_rate": float(np.mean(unit)),
                    "full_component_size_mean": float(np.mean(component)),
                    "full_component_ge6_rate": float(np.mean(component >= 6)),
                    "component_ge_min_rate": float(np.mean(component >= minimum)),
                    "cluster_rate": float(np.mean(cluster)),
                    "target_rate": float(np.mean(target)),
                    "small_branch_proxy_rate": float(np.mean(small_proxy)),
                    "cluster_target_mismatch_rate": float(np.mean(cluster != target)),
                    "note": "当前严格 one-seed正式记录；small_branch为由component/event推断的审计proxy",
                })
    return output


def write_markdown(out: Path, historical: list[dict[str, object]], totals: dict[str, int], current: list[dict[str, object]]) -> Path:
    prod = totals.get("production_events", 0)
    small = totals.get("production_small_branch_events", 0)
    full = totals.get("production_full_ge6_events", 0)
    local = totals.get("production_local_lt6_events", 0)
    lines = [
        "# 目标级 Poisson-binomial 与生产连通形态审计",
        "",
        "本文件只读取保留的紧凑 CSV；不打开 raw BIN/逐周期图，也不使用 evaluation 命中率拟合 PB 曲线。",
        "历史联合掩膜来源于 `target_track_analysis_0deg_20260827`，共 " + str(totals.get("rows", 0)) + " 条（5 seed、150 events）。该父运行没有后来的 strict_online_no_prior 标记，因此本审计只用于解释形态，不替代正式 Pd。",
        "",
        f"历史生产 cluster 命中 {prod} 条，其中小簇 +20 dB 分支 {small} 条（{small / prod:.1%}，若分母非零），全图 component≥6 仅 {full} 条，生产命中中局部 3×5 命中数<6 的有 {local} 条。",
        "",
        "这说明 min_points=6 的生产事件不是 15 个候选单元独立计数达到 6：实际 full-map 连通分量与局部 3×5 support 不同，而且 3–5 点小簇可以通过 +20 dB 峰值分支。PB η=1 曲线要求的随机事件与生产事件不相同，出现右移不是门限 bug，也不能用 evaluation 命中率重新拟合。",
        "",
        "`target_pb_morphology_audit.csv` 保存逐 SCNR 档统计，`target_pb_morphology_audit.png` 为四面板图；`current_formal_morphology_summary.csv` 是当前严格正式 period metrics 的 component/cluster 紧凑摘要。",
    ]
    path = out / "target_pb_morphology_audit.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def plot(out: Path, rows: list[dict[str, object]]) -> Path:
    figure_dir = out / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    x = np.asarray([fnum(r.get("output_scnr_db_mean")) for r in rows], dtype=float)
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7.4))
    ax = axes[0, 0]
    ax.plot(x, [fnum(r.get("local_3x5_hit_mean")) for r in rows], "o-", label="局部3×5命中数均值")
    ax.plot(x, [fnum(r.get("full_component_size_mean")) for r in rows], "s-", label="全图component大小均值")
    ax.axhline(6, color="#d62728", linestyle=":", label="min_points=6")
    ax.set(xlabel="output SCNR (dB)", ylabel="count", title="局部 support 与全图 component")
    ax.legend(fontsize=7)
    ax = axes[0, 1]
    for key, label, color in (("local_3x5_ge6_rate", "局部3×5≥6", "#9467bd"),
                              ("full_component_ge6_rate", "全图component≥6", "#1f77b4"),
                              ("cluster_min6_rate", "严格min6点数", "#777777"),
                              ("production_cluster_rate", "生产cluster", "#d62728"),
                              ("target_rate", "最终target", "#2ca02c")):
        ax.plot(x, [fnum(r.get(key)) for r in rows], "o-", label=label, color=color)
    ax.set(xlabel="output SCNR (dB)", ylabel="rate", title="不同事件定义的命中率")
    ax.set_ylim(-.05, 1.05); ax.legend(fontsize=7)
    ax = axes[1, 0]
    for key, label, color in (("production_small_branch_fraction", "生产命中中小簇+20 dB", "#d62728"),
                              ("production_full_ge6_fraction", "生产命中中full≥6", "#1f77b4"),
                              ("production_local_lt6_fraction", "生产命中中局部<6", "#9467bd")):
        ax.plot(x, [fnum(r.get(key)) for r in rows], "o-", label=label, color=color)
    ax.set(xlabel="output SCNR (dB)", ylabel="conditional fraction", title="生产命中的结构分解")
    ax.set_ylim(-.05, 1.05); ax.legend(fontsize=7)
    ax = axes[1, 1]
    ax.plot(x, [fnum(r.get("production_cluster_rate")) for r in rows], "o-", color="#d62728", label="历史生产cluster")
    ax.plot(x, [fnum(r.get("target_rate")) for r in rows], "s--", color="#2ca02c", label="历史target")
    ax.plot(x, [fnum(r.get("pb_min6_profile")) for r in rows], "^-", color="#111111", label="PB min6 η=1")
    ax.axhline(.9, color="#555", linestyle=":")
    ax.set(xlabel="output SCNR (dB)", ylabel="Pd / rate", title="PB min6 与生产事件（历史形态审计）")
    ax.set_ylim(-.05, 1.05); ax.legend(fontsize=7)
    fig.suptitle("Poisson-binomial 与生产连通形态的结构性差异（非正式Pd）")
    fig.tight_layout()
    path = figure_dir / "target_pb_morphology_audit.png"
    fig.savefig(path)
    fig.savefig(path.with_suffix(".pdf"))
    plt.close(fig)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/gmti_scnr_eval/comprehensive_report_20260829")
    parser.add_argument("--pb-path", type=Path, default=PB_PATH)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    pb_rows = read_csv(args.pb_path) if args.pb_path.is_file() else []
    historical, totals = build_historical(pb_rows)
    current = build_current()
    write_csv(out / "target_pb_morphology_audit.csv", historical)
    write_csv(out / "current_formal_morphology_summary.csv", current)
    figure = plot(out, historical) if historical else None
    markdown = write_markdown(out, historical, totals, current)
    manifest = {
        "status": "pass" if historical else "missing_historical_source",
        "historical_source": str(HISTORICAL_PATH.resolve()),
        "pb_source": str(args.pb_path.resolve()),
        "current_roots": {str(k): str(v.resolve()) for k, v in CURRENT_ROOTS.items()},
        "historical_scope": "morphology_only_non_strict_not_formal_pd",
        "evaluation_hit_rates_used_for_fit": False,
        "raw_bin_opened": False,
        "historical_totals": totals,
        "historical_rows": len(historical),
        "current_rows": len(current),
        "figure": str(figure.resolve()) if figure else "",
        "markdown": str(markdown.resolve()),
    }
    (out / "target_pb_morphology_audit_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0 if historical else 2


if __name__ == "__main__":
    raise SystemExit(main())
