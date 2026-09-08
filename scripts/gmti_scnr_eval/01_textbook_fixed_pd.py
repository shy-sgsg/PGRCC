#!/usr/bin/env python3
"""T2：确定复幅度目标的教材固定门限单元 Pd 理论与 Monte Carlo。"""

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

from scnr_eval_lib import db_to_linear, ensure_dir, fixed_pd, parse_grid, wilson_interval, write_csv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("outputs/gmti_scnr_eval/textbook_cell"))
    parser.add_argument("--pfa", type=float, default=1e-7)
    parser.add_argument("--scnr-grid", default="0,2,4,6,8,10,12,14,16,18,20,22,24")
    parser.add_argument("--samples", type=int, default=200_000)
    parser.add_argument("--seed", type=int, default=20260825)
    args = parser.parse_args()
    if not 0 < args.pfa < 1 or args.samples < 1:
        raise SystemExit("--pfa 必须在 (0,1)，--samples 必须为正")
    out = ensure_dir(args.output_dir.resolve())
    scnr_db = np.asarray(parse_grid(args.scnr_grid))
    gamma = np.asarray([db_to_linear(value) for value in scnr_db])
    theory = fixed_pd(gamma, args.pfa)
    rng = np.random.default_rng(args.seed)
    noise = (rng.standard_normal((args.samples, gamma.size)) +
             1j * rng.standard_normal((args.samples, gamma.size))) / math.sqrt(2.0)
    eta = -math.log(args.pfa)
    hits = np.abs(noise + np.sqrt(gamma)[None, :]) ** 2 > eta
    counts = hits.sum(axis=0, dtype=np.int64)
    mc = counts / args.samples
    theory_rows = [{"scnr_db": float(x), "scnr_linear": float(g), "pfa": args.pfa,
                    "threshold_over_p0": eta, "pd_fixed_theory": float(p)}
                   for x, g, p in zip(scnr_db, gamma, theory)]
    mc_rows = []
    for x, g, p, count in zip(scnr_db, gamma, mc, counts):
        low, high = wilson_interval(int(count), args.samples)
        mc_rows.append({"scnr_db": float(x), "scnr_linear": float(g), "samples": args.samples,
                        "detections": int(count), "pd_fixed_mc": float(p),
                        "pd_ci95_low": low, "pd_ci95_high": high, "seed": args.seed})
    # 直接 PFA MC 的置信上界用于说明：pfa=1e-7 下小样本不能“零命中即通过”。
    pfa_noise = (rng.standard_normal(args.samples) + 1j * rng.standard_normal(args.samples)) / math.sqrt(2.0)
    pfa_count = int(np.count_nonzero(np.abs(pfa_noise) ** 2 > eta))
    pfa_low, pfa_high = wilson_interval(pfa_count, args.samples)
    pfa_rows = [{"configured_pfa": args.pfa, "threshold_over_p0": eta, "samples": args.samples,
                 "false_alarms": pfa_count, "pfa_direct_mc": pfa_count / args.samples,
                 "pfa_ci95_low": pfa_low, "pfa_ci95_high": pfa_high,
                 "interpretation": "低 PFA 的直接 MC 需远大于 1/PFA 的样本；本表只保留可审计估计"}]
    write_csv(out / "textbook_fixed_threshold_theory.csv", theory_rows)
    write_csv(out / "textbook_fixed_threshold_mc.csv", mc_rows)
    write_csv(out / "textbook_pfa_validation.csv", pfa_rows)
    fig, axis = plt.subplots(figsize=(7.4, 4.6))
    axis.plot(scnr_db, theory, label="固定门限理论（Marcum-Q）", linewidth=2)
    axis.errorbar(scnr_db, mc,
                  yerr=np.asarray([[row["pd_fixed_mc"] - row["pd_ci95_low"] for row in mc_rows],
                                   [row["pd_ci95_high"] - row["pd_fixed_mc"] for row in mc_rows]]),
                  fmt="o", label="直接 Monte Carlo（95% Wilson CI）")
    axis.axhline(0.9, color="tab:red", linestyle="--", label="Pd=0.90")
    axis.set(xlabel="单元 SCNR (dB)", ylabel="检测概率", ylim=(-0.02, 1.02), title="F01/F02 固定门限单元级检测概率")
    axis.grid(True, alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(out / "F01_F02_textbook_fixed_pd.png", dpi=180)
    plt.close(fig)
    print(f"[PASS] 固定门限理论/MC 已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
