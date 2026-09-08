#!/usr/bin/env python3
"""T3：当前生产二维 GO-CFAR 单元级检测概率的条件 MC 理论与直接 MC。"""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti_scnr_eval_matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from scnr_eval_lib import (GoCfarGeometry, db_to_linear, ensure_dir, fixed_pd,
                           go_conditional_pd, go_configured_pfa, go_direct_pd,
                           go_training_noise_samples, parse_grid, write_csv)


def plot_curve(path: Path, title: str, rows: list[dict], y_key: str,
               label: str, reference: list[dict] | None = None) -> None:
    fig, axis = plt.subplots(figsize=(7.4, 4.6))
    x = [row["scnr_db"] for row in rows]
    axis.plot(x, [row[y_key] for row in rows], marker="o", label=label)
    if reference:
        axis.plot([row["scnr_db"] for row in reference], [row["pd_fixed_theory"] for row in reference],
                  linestyle="--", label="教材固定门限（仅基准）")
    axis.axhline(0.9, color="tab:red", linestyle="--", label="Pd=0.90")
    axis.set(xlabel="单元 SCNR (dB)", ylabel="检测概率", ylim=(-0.02, 1.02), title=title)
    axis.grid(True, alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("outputs/gmti_scnr_eval/go_cfar_cell"))
    parser.add_argument("--pfa", type=float, default=1e-7)
    parser.add_argument("--alpha-override", type=float,
                        help="生产诊断 sidecar 记录的实际 alpha；提供时理论与该生产运行逐项一致")
    parser.add_argument("--guard", type=int, default=4)
    parser.add_argument("--background", type=int, default=16)
    parser.add_argument("--scnr-grid", default="0,2,4,6,8,10,12,14,16,18,20,22,24")
    parser.add_argument("--theory-samples", type=int, default=1_000_000)
    parser.add_argument("--direct-samples", type=int, default=200_000)
    parser.add_argument("--seed", type=int, default=20260825)
    args = parser.parse_args()
    if not 0 < args.pfa < 1 or min(args.guard, args.background) < 0:
        raise SystemExit("PFA 或 GO 窗口参数非法")
    out = ensure_dir(args.output_dir.resolve())
    geometry = GoCfarGeometry(args.guard, args.background)
    if args.alpha_override is not None and args.alpha_override <= 0.0:
        raise SystemExit("alpha-override 必须为正")
    alpha = args.alpha_override if args.alpha_override is not None else geometry.alpha(args.pfa)
    scnr_db = np.asarray(parse_grid(args.scnr_grid))
    gamma = np.asarray([db_to_linear(value) for value in scnr_db])
    training = go_training_noise_samples(geometry, args.theory_samples, args.seed)
    theory_pd = go_conditional_pd(gamma, alpha, training)
    pfa_estimate, pfa_se = go_configured_pfa(alpha, training)
    direct_training = training[:min(args.direct_samples, training.size)]
    direct_pd, ci_low, ci_high = go_direct_pd(gamma, alpha, direct_training, args.seed + 1)
    fixed = fixed_pd(gamma, args.pfa)
    common = {
        "cfar_type": "GO", "configured_pfa": args.pfa, "guard_half_width_cells": args.guard,
        "background_thickness_cells": args.background, "alpha": alpha,
        "alpha_ring_cells": geometry.code_total_background_cells,
        "directional_strip_cells": geometry.directional_strip_cells,
        "noise_estimator": "max(mean(left),mean(right),mean(top),mean(bottom))",
        "alpha_source": ("production_alpha_override" if args.alpha_override is not None
                         else "legacy_ca_ring_formula"),
    }
    theory_rows = [{**common, "scnr_db": float(x), "scnr_linear": float(g),
                    "pd_go_conditional_mc": float(p), "theory_training_samples": int(training.size),
                    "pd_fixed_theory": float(f)}
                   for x, g, p, f in zip(scnr_db, gamma, theory_pd, fixed)]
    direct_rows = [{**common, "scnr_db": float(x), "scnr_linear": float(g),
                    "pd_go_direct_mc": float(p), "pd_ci95_low": float(low), "pd_ci95_high": float(high),
                    "direct_samples": int(direct_training.size), "seed": args.seed + 1}
                   for x, g, p, low, high in zip(scnr_db, gamma, direct_pd, ci_low, ci_high)]
    write_csv(out / "go_cfar_cell_theory.csv", theory_rows)
    write_csv(out / "go_cfar_cell_mc_iid.csv", direct_rows)
    write_csv(out / "go_cfar_pfa_validation.csv", [{**common, "training_samples": int(training.size),
        "pfa_conditional_mc": pfa_estimate, "pfa_conditional_mc_standard_error": pfa_se,
        "method": "E[exp(-alpha*max(direction_mean))]; 无需用稀有 CUT 命中估计 1e-7"}])

    # 训练单元敏感性：改变生产同构背景带厚度，不将其当作正式配置。
    sweep_rows: list[dict] = []
    pfa_rows: list[dict] = []
    for index, background in enumerate((4, 8, 12, 16, 20, 24)):
        geom = GoCfarGeometry(args.guard, background)
        samples = go_training_noise_samples(geom, min(args.theory_samples, 300_000), args.seed + 10 + index)
        a = geom.alpha(args.pfa)
        values = go_conditional_pd(gamma, a, samples)
        for x, g, value in zip(scnr_db, gamma, values):
            sweep_rows.append({"scnr_db": float(x), "scnr_linear": float(g), "background_thickness_cells": background,
                               "alpha_ring_cells": geom.code_total_background_cells, "alpha": a,
                               "pd_go_conditional_mc": float(value), "samples": int(samples.size)})
    for index, pfa in enumerate((1e-5, 1e-6, 1e-7, 1e-8)):
        samples = go_training_noise_samples(geometry, min(args.theory_samples, 300_000), args.seed + 30 + index)
        a = geometry.alpha(pfa)
        values = go_conditional_pd(gamma, a, samples)
        pfa_actual, pfa_actual_se = go_configured_pfa(a, samples)
        for x, g, value in zip(scnr_db, gamma, values):
            pfa_rows.append({"scnr_db": float(x), "scnr_linear": float(g), "configured_pfa": pfa,
                             "actual_go_pfa_conditional_mc": pfa_actual,
                             "actual_go_pfa_standard_error": pfa_actual_se, "alpha": a,
                             "pd_go_conditional_mc": float(value), "samples": int(samples.size)})
    write_csv(out / "go_cfar_train_count_sweep.csv", sweep_rows)
    write_csv(out / "go_cfar_pfa_sweep.csv", pfa_rows)

    # 此图只验证 guard 几何/训练计数对同构 IID 背景的影响；真实脉压泄漏将在全链路输出后写入同名实测表。
    guard_rows: list[dict] = []
    for index, guard in enumerate((1, 2, 4, 6, 8)):
        geom = GoCfarGeometry(guard, args.background)
        samples = go_training_noise_samples(geom, min(args.theory_samples, 300_000), args.seed + 50 + index)
        a = geom.alpha(args.pfa)
        values = go_conditional_pd(gamma, a, samples)
        for x, g, value in zip(scnr_db, gamma, values):
            guard_rows.append({"scnr_db": float(x), "scnr_linear": float(g), "guard_half_width_cells": guard,
                               "alpha_ring_cells": geom.code_total_background_cells, "alpha": a,
                               "pd_go_iid_no_leakage": float(value), "samples": int(samples.size),
                               "scope": "IID 无目标泄漏；非正式全链路泄漏结论"})
    write_csv(out / "go_cfar_guard_sweep.csv", guard_rows)
    (out / "go_cfar_real_background_sweep.csv").write_text(
        "status,reason\npending,由 09_run_go_formal_batch.sh 与 07_extract_go_track_metrics.sh 的正式 CSI/GO-CFAR 诊断生成；不能以 IID 背景替代\n",
        encoding="utf-8")

    plot_curve(out / "F04_fixed_vs_go_cell_pd.png", "F04 固定门限与正式 GO-CFAR 单元 Pd",
               theory_rows, "pd_go_conditional_mc", "GO-CFAR 条件 MC 理论", theory_rows)
    fig, axis = plt.subplots(figsize=(7.4, 4.6))
    for background in sorted({row["background_thickness_cells"] for row in sweep_rows}):
        rows = [row for row in sweep_rows if row["background_thickness_cells"] == background]
        axis.plot([row["scnr_db"] for row in rows], [row["pd_go_conditional_mc"] for row in rows],
                  label=f"背景带 b={background}（N={rows[0]['alpha_ring_cells']}）")
    axis.axhline(0.9, color="tab:red", linestyle="--")
    axis.set(xlabel="SCNR (dB)", ylabel="GO-CFAR 单元 Pd", ylim=(-0.02, 1.02), title="F05 GO-CFAR 训练窗敏感性")
    axis.grid(True, alpha=0.3); axis.legend(fontsize=8); fig.tight_layout(); fig.savefig(out / "F05_go_train_sweep.png", dpi=180); plt.close(fig)
    fig, axis = plt.subplots(figsize=(7.4, 4.6))
    for pfa in sorted({row["configured_pfa"] for row in pfa_rows}, reverse=True):
        rows = [row for row in pfa_rows if row["configured_pfa"] == pfa]
        axis.plot([row["scnr_db"] for row in rows], [row["pd_go_conditional_mc"] for row in rows], label=f"代码 pf={pfa:g}")
    axis.axhline(0.9, color="tab:red", linestyle="--")
    axis.set(xlabel="SCNR (dB)", ylabel="GO-CFAR 单元 Pd", ylim=(-0.02, 1.02), title="F06 GO-CFAR PFA 参数敏感性")
    axis.grid(True, alpha=0.3); axis.legend(); fig.tight_layout(); fig.savefig(out / "F06_go_pfa_sweep.png", dpi=180); plt.close(fig)
    print(f"[PASS] 正式 GO-CFAR 单元理论/MC 已写入 {out}；条件 MC PFA={pfa_estimate:.3e}±{pfa_se:.1e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
