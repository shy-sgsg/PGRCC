#!/usr/bin/env python3
"""Render the compact, single-seed R14 task trade-off figure."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent
ANALYSIS = ROOT / "analysis"
METHODS = ("C0", "C2", "C3")
COLORS = {"C0": "#64748b", "C2": "#2563eb", "C3": "#ea580c"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


summary = read_csv(ANALYSIS / "summary.csv")
csi = {row["method_id"]: row for row in read_csv(ANALYSIS / "csi_results.csv")}
selected = {
    method: {
        role: next(
            row for row in summary
            if row["method_id"] == method and row["role"] == role
        )
        for role in ("OFF", "ON", "TO")
    }
    for method in METHODS
}

metrics = [
    (
        "TO target-only CSI retention (dB)",
        [float(csi[m]["target_only_csi_retention_db"]) for m in METHODS],
        "Higher retains more target-only output power",
    ),
    (
        "ON non-target detection rows",
        [int(selected[m]["ON"]["false_detections_or_associations"]) for m in METHODS],
        "Raw unmatched rows; lower is fewer candidates",
    ),
    (
        "OFF unique union hit fraction (%)",
        [
            100.0 * int(selected[m]["OFF"]["cfar_unique_union_hit_cells"])
            / int(selected[m]["OFF"]["valid_cut_count"])
            for m in METHODS
        ],
        "Observed hit fraction, not Pfa",
    ),
    (
        "ON TrackManager ground-speed RMSE (m/s)",
        [float(selected[m]["ON"]["ground_speed_rmse_mps"]) for m in METHODS],
        "Two confirmed target track-period associations per method",
    ),
]

fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
for ax, (title, values, note) in zip(axes.flat, metrics):
    bars = ax.bar(
        METHODS,
        values,
        color=[COLORS[method] for method in METHODS],
        width=0.62,
    )
    ax.set_title(title, fontsize=11, pad=12)
    ylabel = (
        "dB" if "(dB)" in title else
        "%" if "(%)" in title else
        "m/s" if "RMSE (m/s)" in title else
        "rows"
    )
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", alpha=0.25)
    ax.set_axisbelow(True)
    for bar, value in zip(bars, values):
        ax.annotate(
            f"{value:.2f}" if isinstance(value, float) else str(value),
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    ax.text(
        0.5,
        -0.20,
        note,
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=8,
        color="#475569",
    )

fig.suptitle(
    "R14 seed505: Robust DDC / SCC task trade-offs",
    fontsize=14,
    fontweight="bold",
)
fig.text(
    0.5,
    -0.01,
    "Single independent seed; descriptive only. ON correct detections were 3/3 for all methods; no statistical Pfa or significance claim.",
    ha="center",
    fontsize=9,
    color="#334155",
)
output = ROOT / "figures" / "r14_seed505_task_tradeoffs.png"
output.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(output, dpi=170, bbox_inches="tight")
print(output)
