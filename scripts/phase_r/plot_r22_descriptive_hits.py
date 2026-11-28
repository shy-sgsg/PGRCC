#!/usr/bin/env python3
"""Plot one-period, descriptive R22 target-hit counts from the archived table."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path


def main() -> int:
    here = Path(__file__).resolve().parents[2]
    default_csv = here / "docs/research/phase_r/R22/evidence/results/r22_key_metrics.csv"
    default_png = here / "docs/research/phase_r/R22/evidence/results/r22_descriptive_target_hits.png"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=default_csv)
    parser.add_argument("--output", type=Path, default=default_png)
    args = parser.parse_args()

    os.environ.setdefault("MPLCONFIGDIR", "/tmp/pgrcc-r22-mpl")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    with args.input.open("r", newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))

    methods = ["A0", "C0", "PK", "R22_R9_Fixed_D3", "R22_D3_Huber_B_ON", "R22_M1_Physics_Matched"]
    seeds = [900202, 900203]
    counts: dict[tuple[int, str], float] = {}
    for row in rows:
        if row["role"] != "ON":
            continue
        if row["status"] != "EVALUABLE" or not row["hits"]:
            continue
        counts[(int(row["seed"]), row["method_id"])] = float(row["hits"])

    x = np.arange(len(methods), dtype=float)
    width = 0.36
    fig, ax = plt.subplots(figsize=(10.5, 5.4), constrained_layout=True)
    colors = ["#3569a8", "#d17a32"]
    for index, seed in enumerate(seeds):
        values = [counts.get((seed, method), np.nan) for method in methods]
        bars = ax.bar(x + (index - 0.5) * width, values, width, label=f"seed {seed}", color=colors[index])
        for bar, value in zip(bars, values):
            if np.isfinite(value):
                ax.text(bar.get_x() + bar.get_width() / 2, value + 0.25, f"{int(value)}", ha="center", va="bottom", fontsize=8)

    labels = ["A0", "C0", "PK\n(Known)", "R9\nFixed D3", "Huber\nD3", "M1\nPhysics Matched"]
    ax.set_xticks(x, labels)
    ax.set_ylabel("Correct target detections (of 20 opportunities)")
    ax.set_ylim(0, 21.5)
    ax.set_yticks(range(0, 21, 5))
    ax.set_title("R22 one-period Native CUDA pilot: descriptive target hits")
    ax.text(
        0.01,
        0.98,
        "2.5 m/s · 20 dB · +4 ns · 1 beam · 1 period per seed\n"
        "Target opportunities are not independent seed replicates; no statistical inference.",
        transform=ax.transAxes,
        va="top",
        fontsize=9,
        bbox={"facecolor": "white", "edgecolor": "#cccccc", "alpha": 0.9},
    )
    ax.grid(axis="y", alpha=0.25)
    ax.legend(loc="upper right")
    fig.savefig(args.output, dpi=170)
    plt.close(fig)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
