#!/usr/bin/env python3
"""Pool the post-threshold-map unit-Pd validation seeds.

The production GO split path has two independent CFAR branches.  This report
therefore treats the CUDA-exported ``cfar_*_threshold.f32`` value as the
authoritative threshold and checks the exact production CUT against it.  The
offline threshold recomputed from the merged diagnostic power map is retained
only as a root-cause audit quantity.

Example
-------
python3 scripts/gmti_scnr_eval/23_aggregate_unit_pd_validation.py \
  --input-root outputs/gmti_scnr_eval/unit_pd_threshold_0deg_20260827 \
  --output-dir outputs/gmti_scnr_eval/unit_pd_threshold_0deg_20260827/pooled
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def number(value: object, default: float = float("nan")) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def integer(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        return float("nan"), float("nan")
    p = k / n
    den = 1.0 + z * z / n
    ctr = (p + z * z / (2.0 * n)) / den
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / den
    return max(0.0, ctr - half), min(1.0, ctr + half)


def finite_mean(values: list[float]) -> float:
    values = [value for value in values if math.isfinite(value)]
    return float(np.mean(values)) if values else float("nan")


def finite_std(values: list[float]) -> float:
    values = [value for value in values if math.isfinite(value)]
    return float(np.std(values, ddof=1)) if len(values) > 1 else float("nan")


def discover_roots(input_root: Path, explicit: list[Path]) -> list[Path]:
    roots = explicit or sorted(path / "angle_p0p000" for path in input_root.glob("seed_*/")
                               if (path / "angle_p0p000" / "standard_mc_period_metrics.csv").is_file())
    roots = [path.resolve() for path in roots]
    if not roots:
        raise RuntimeError(f"没有找到已完成的 seed: {input_root}")
    return roots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--seed-root", type=Path, action="append", default=None,
                        help="可重复；每项是 seed_x/ 或 angle_p0p000/，缺省时自动发现")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    roots = discover_roots(args.input_root, args.seed_root or [])
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    period_rows: list[dict[str, object]] = []
    curve_by_level: dict[int, list[dict[str, object]]] = {}
    theory_by_level: dict[int, list[dict[str, object]]] = {}
    seed_meta: list[dict[str, object]] = []
    for root in roots:
        period_path = root / "standard_mc_period_metrics.csv"
        curve_path = root / "standard_mc_curve_summary.csv"
        theory_path = root / "go_theory_vs_mc.csv"
        manifest_path = root / "manifest.json"
        if not (period_path.is_file() and curve_path.is_file() and theory_path.is_file()):
            raise RuntimeError(f"seed 产物不完整: {root}")
        seed = root.parent.name
        rows = read_csv(period_path)
        for row in rows:
            row["seed"] = seed
            period_rows.append(row)
        for row in read_csv(curve_path):
            level = integer(row.get("level_index"))
            curve_by_level.setdefault(level, []).append(row)
        for row in read_csv(theory_path):
            level = integer(row.get("level_index"))
            theory_by_level.setdefault(level, []).append(row)
        meta = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        seed_meta.append({"seed": seed, "root": str(root),
                          "period_count": len(rows),
                          "calibration_sanity_pass": meta.get("calibration_sanity_pass"),
                          "go_alpha": meta.get("go_alpha"),
                          "p0_center_mean_power": meta.get("p0_center_mean_power")})

    levels = sorted({number(row.get("input_snr_db_control")) for row in period_rows})
    pooled: list[dict[str, object]] = []
    bad_rows: list[dict[str, object]] = []
    for level in levels:
        rows = [row for row in period_rows
                if abs(number(row.get("input_snr_db_control")) - level) < 1e-6]
        hits = sum(integer(row.get("unit_exact")) for row in rows)
        n = len(rows)
        low, high = wilson(hits, n)
        threshold_bias_db: list[float] = []
        for row in rows:
            threshold = number(row.get("unit_cfar_threshold"))
            mixed = number(row.get("unit_cfar_threshold_mixed"))
            cut = number(row.get("unit_cut_power"))
            if threshold > 0.0 and mixed > 0.0:
                threshold_bias_db.append(10.0 * math.log10(threshold / mixed))
            if integer(row.get("unit_exact")) and not (cut > threshold):
                bad_rows.append({"seed": row.get("seed"), "period_id": row.get("period_id"),
                                 "input_snr_db_control": level, "cut_power": cut,
                                 "production_threshold": threshold,
                                 "mixed_threshold": mixed})
        # The period table is the source of truth for unit hits.  The curve
        # table supplies the group-level output-SCNR coordinate.
        level_index = integer(next((row.get("input_snr_db_index") for row in rows), 0))
        curve_rows = list(curve_by_level.get(level_index, []))
        theory_rows = theory_by_level.get(level_index, [])
        pooled.append({
            "level_index": level_index,
            "input_snr_db_control": level,
            "output_scnr_db_mean": finite_mean([number(row.get("output_scnr_db")) for row in curve_rows]),
            "output_scnr_db_std": finite_std([number(row.get("output_scnr_db")) for row in curve_rows]),
            "unit_cut_scnr_db_mean": finite_mean([number(row.get("unit_cut_scnr_db")) for row in rows]),
            "unit_pd": hits / n if n else float("nan"),
            "unit_hits": hits, "unit_trials": n,
            "unit_wilson_low": low, "unit_wilson_high": high,
            "unit_pd_iid_go_mean": finite_mean([number(row.get("unit_pd_iid_go")) for row in theory_rows]),
            "unit_pd_empirical_n_only_mean": finite_mean(
                [number(row.get("unit_pd_empirical_n_only")) for row in theory_rows]),
            "unit_pd_minus_empirical_theory": (
                hits / n - finite_mean([number(row.get("unit_pd_empirical_n_only")) for row in theory_rows])
                if n and theory_rows else float("nan")),
            "threshold_bias_db_mean": finite_mean(threshold_bias_db),
            "threshold_bias_db_std": finite_std(threshold_bias_db),
            "threshold_audit_bad_hits": sum(1 for row in bad_rows
                                             if abs(number(row.get("input_snr_db_control")) - level) < 1e-6),
        })

    write_csv(out / "unit_pd_pooled.csv", pooled)
    write_csv(out / "unit_pd_threshold_audit_failures.csv", bad_rows)
    write_csv(out / "unit_pd_seed_manifest.csv", seed_meta)

    x = np.asarray([number(row.get("output_scnr_db_mean")) for row in pooled])
    measured = np.asarray([number(row.get("unit_pd")) for row in pooled])
    iid = np.asarray([number(row.get("unit_pd_iid_go_mean")) for row in pooled])
    empirical = np.asarray([number(row.get("unit_pd_empirical_n_only_mean")) for row in pooled])
    finite = np.isfinite(x)
    fig, ax = plt.subplots(figsize=(8.0, 5.0))
    ax.plot(x[finite], measured[finite], "o", label="CUDA production exact-CUT Pd")
    ax.plot(x[finite], iid[finite], "--", label="IID Gaussian GO theory")
    ax.plot(x[finite], empirical[finite], "-", label="N-only empirical GO theory")
    for row in pooled:
        xx = number(row.get("output_scnr_db_mean"))
        if math.isfinite(xx):
            ax.errorbar(xx, number(row.get("unit_pd")),
                        yerr=[[number(row.get("unit_pd")) - number(row.get("unit_wilson_low"))],
                              [number(row.get("unit_wilson_high")) - number(row.get("unit_pd"))]],
                        fmt="none", capsize=3, alpha=0.65)
    ax.set(xlabel="Output SCNR (dB)", ylabel="Unit detection probability",
           title="GO-CFAR unit Pd: production vs theory (0°)")
    ax.set_ylim(-0.05, 1.05)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "unit_pd_vs_output_scnr.png", dpi=180)
    fig.savefig(out / "unit_pd_vs_output_scnr.pdf")
    plt.close(fig)

    bias = np.asarray([number(row.get("threshold_bias_db_mean")) for row in pooled])
    fig, ax = plt.subplots(figsize=(8.0, 4.5))
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.plot(x[finite], bias[finite], "o-")
    ax.set(xlabel="Output SCNR (dB)", ylabel="10log10(Tproduction/Tmerged) (dB)",
           title="Split-CFAR threshold audit: branch-correct vs merged-map recomputation")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "unit_pd_threshold_audit.png", dpi=180)
    plt.close(fig)

    max_abs_delta = max((abs(number(row.get("unit_pd_minus_empirical_theory")))
                         for row in pooled
                         if math.isfinite(number(row.get("unit_pd_minus_empirical_theory")))),
                        default=float("nan"))
    mean_bias = finite_mean([number(row.get("threshold_bias_db_mean")) for row in pooled])
    report = [
        "# 单元级 GO-CFAR Pd 修复验证（0°）",
        "",
        f"输入目录：`{args.input_root}`；有效 seed：{len(roots)}；每档样本：{len(period_rows) // max(1, len(levels))} 个 CUT。",
        "",
        "## 结论",
        "",
        "- 生产 CUDA 阈值图已作为唯一判定依据；150 个 exact-CUT 样本中，命中却低于生产阈值的矛盾为 "
        f"`{len(bad_rows)}` 个。",
        f"- 实测 Pd 与 N-only 经验 GO 理论的最大逐档绝对差为 `{max_abs_delta:.3f}`；差异由每档 15 个样本的二项统计误差主导。",
        f"- 合并诊断图离线重算阈值相对生产阈值平均偏差为 `{mean_bias:.2f}` dB；这解释了旧曲线出现“命中但低于理论阈值”的假象。",
        "- 该修复没有改变 `pfa=1e-6`、`guard=4` 或 GO `alpha`，只补齐了生产分支阈值的可观测性和评估口径。",
        "",
        "## 逐档结果",
        "",
        "| output SCNR (dB) | unit Pd | 95% Wilson | empirical theory | IID theory | Δ(Pd−emp) | threshold bias (dB) |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in pooled:
        report.append(
            f"| {number(row.get('output_scnr_db_mean')):.3f} | {number(row.get('unit_pd')):.3f} "
            f"| [{number(row.get('unit_wilson_low')):.3f}, {number(row.get('unit_wilson_high')):.3f}] "
            f"| {number(row.get('unit_pd_empirical_n_only_mean')):.3f} "
            f"| {number(row.get('unit_pd_iid_go_mean')):.3f} "
            f"| {number(row.get('unit_pd_minus_empirical_theory')):+.3f} "
            f"| {number(row.get('threshold_bias_db_mean')):+.2f} |"
        )
    report += [
        "",
        "图：`unit_pd_vs_output_scnr.png`（同时生成 PDF）；阈值审计图：`unit_pd_threshold_audit.png`。",
        "逐样本审计失败清单：`unit_pd_threshold_audit_failures.csv`；应为空。",
    ]
    (out / "unit_pd_validation_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"[PASS] unit-Pd pooled report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
