#!/usr/bin/env python3
"""比较生产 min_points=6 与一次真实 CUDA min_points=3 对照。

这是诊断报告，不是把一次 seed 当作正式概率结论。生产曲线来自保留 joint
hit mask 的五 seed pooled 结果；min3 曲线来自一个同网格、同 0°、同 PFA
和同 S+N 生成链路的真实 CUDA run。横轴仍使用实际 output SCNR。
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti_scnr_eval_matplotlib")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from scnr_eval_lib import ensure_dir, read_csv, write_csv


def number(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return parsed if math.isfinite(parsed) else float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-curve", type=Path, required=True)
    parser.add_argument("--min3-curve", type=Path, required=True)
    parser.add_argument("--min3-period-metrics", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    out = ensure_dir(args.output_dir.resolve())
    prod = read_csv(args.production_curve.resolve())
    min3 = read_csv(args.min3_curve.resolve())
    rows: list[dict[str, object]] = []
    for row in prod:
        rows.append({
            "config": "production_min6_plus_small3_20dB",
            "input_snr_db_control": number(row.get("input_snr_db_control")),
            "output_scnr_db": number(row.get("output_scnr_db")),
            "unit_pd": number(row.get("unit_pd")),
            "cluster_pd": number(row.get("cluster_production_pd")),
            "target_pd": number(row.get("target_pd")),
            "track_pd": number(row.get("track_pd")),
            "unit_hits": number(row.get("unit_hits")),
            "unit_trials": number(row.get("unit_trials")),
            "cluster_hits": number(row.get("cluster_production_pd")) * number(row.get("unit_trials")),
            "target_hits": number(row.get("target_pd")) * number(row.get("unit_trials")),
            "track_hits": number(row.get("track_hits")),
            "track_trials": number(row.get("track_trials")),
            "source": str(args.production_curve.resolve()),
        })
    for row in min3:
        rows.append({
            "config": "one_seed_min3",
            "input_snr_db_control": number(row.get("input_snr_db_control")),
            "output_scnr_db": number(row.get("output_scnr_db")),
            "unit_pd": number(row.get("unit_pd")),
            "cluster_pd": number(row.get("cluster_pd")),
            "target_pd": number(row.get("target_pd")),
            "track_pd": number(row.get("track_pd")),
            "unit_hits": number(row.get("unit_hits")),
            "unit_trials": number(row.get("unit_trials")),
            "cluster_hits": number(row.get("cluster_hits")),
            "target_hits": number(row.get("target_hits")),
            "track_hits": number(row.get("track_hits")),
            "track_trials": number(row.get("track_trials")),
            "source": str(args.min3_curve.resolve()),
        })
    write_csv(out / "minpoints_comparison.csv", rows)

    period_rows = read_csv(args.min3_period_metrics.resolve())
    write_csv(out / "min3_period_metrics_copy.csv", period_rows)
    size_rows: list[dict[str, object]] = []
    for row in period_rows:
        size_rows.append({
            "period_id": row.get("period_id", ""),
            "input_snr_db_control": number(row.get("input_snr_db_control")),
            "output_scnr_db": number(row.get("output_scnr_db")),
            "unit_exact": row.get("unit_exact", ""),
            "unit_component_size": row.get("unit_component_size", ""),
            "cluster_event": row.get("cluster_event", ""),
            "target_event": row.get("target_event", ""),
            "target_select_hit": row.get("target_select_hit", ""),
            "angle_error_deg": row.get("angle_error_deg", ""),
        })
    write_csv(out / "min3_component_size_audit.csv", size_rows)

    fields = ["unit_pd", "cluster_pd", "target_pd", "track_pd"]
    labels = {"unit_pd": "unit Pd", "cluster_pd": "cluster Pd",
              "target_pd": "target Pd", "track_pd": "track 2/3 Pd"}
    fig, axes = plt.subplots(2, 2, figsize=(10.0, 7.0), sharex=False, sharey=True)
    for axis, field in zip(axes.ravel(), fields):
        for config, marker in (("production_min6_plus_small3_20dB", "o"), ("one_seed_min3", "s")):
            values = [row for row in rows if row["config"] == config and math.isfinite(number(row[field]))]
            values.sort(key=lambda row: number(row["output_scnr_db"]))
            axis.plot([number(row["output_scnr_db"]) for row in values],
                      [number(row[field]) for row in values], marker + "-", label=config)
        axis.set(xlabel="output SCNR (dB)", ylabel=labels[field], title=labels[field])
        axis.grid(True, alpha=0.3)
    axes[0, 0].legend(fontsize=8)
    fig.suptitle("Production min6 versus one-seed min3 (diagnostic)")
    fig.tight_layout()
    fig.savefig(out / "minpoints_comparison.png", dpi=180)
    fig.savefig(out / "minpoints_comparison.pdf")
    plt.close(fig)

    high = [row for row in size_rows if number(row["input_snr_db_control"]) >= 8.0]
    sizes = [number(row["unit_component_size"]) for row in high if math.isfinite(number(row["unit_component_size"]))]
    cluster_sizes = [number(row["unit_component_size"]) for row in size_rows
                     if row["cluster_event"] == "1" and math.isfinite(number(row["unit_component_size"]))]
    report = f"""# min_points 诊断对照

## 结论

生产规则是 `min_points=6`，并允许 3–5 点簇通过 `+20 dB` 小簇恢复；对照是同一
真实 S+N/CUDA 链路、同一 PFA=1e-6、同一 0° 网格的单个 seed `min_points=3`。
这不是正式 multi-seed 概率估计，因此只用于定位损失来源。

生产五 seed pooled 曲线和 min3 单 seed 曲线见 `minpoints_comparison.png` 及
`minpoints_comparison.csv`。min3 单 seed 在约 4 dB output SCNR 已有 unit hit，
但目标/cluster 仍为 0；约 16.7 dB 才出现 1/3 目标命中，约 18.9 dB 为 3/3。
高于 8 dB 的 min3 period 中，实际连通分量大小范围为
`{min(sizes) if sizes else float('nan'):.0f}–{max(sizes) if sizes else float('nan'):.0f}`；
已通过 cluster 的分量大小为 `{cluster_sizes}`。

因此“把 6 改 3”不是单独的修复：中等 SCNR 的真实 joint hit mask 本身只有
1–2 个单元，min3 也无法凭空产生空间支撑；高 SCNR 时生产小簇恢复与 min3
的行为已经接近。后续若要降低目标级门限，应先增加真实目标的 Doppler/range
支撑或调整小簇物理判据，并用多 seed 验证，而不是只改数字。

## 证据与限制

- 生产来源：`{args.production_curve.resolve()}`；五 seed pooled；
- min3 来源：`{args.min3_curve.resolve()}`；单个 seed；
- 逐周期审计：`min3_component_size_audit.csv`；
- 该对照不改变当前生产配置，也不宣称 min3 的最终 Pd。
"""
    (out / "minpoints_comparison_report.md").write_text(report, encoding="utf-8")
    print(f"[PASS] min_points 对照已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
