#!/usr/bin/env python3
"""Plot the retained high-speed/high-SNR R18 pipeline counts by independent seed."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results/formal_matrix"
OUTPUT = HERE / "r19_mode_a_mode_b_pipeline_seed606_707_808_v6p7_snr30.png"
CONDITIONS = (
    ("A0", "A0/C0"),
    ("C1", "C1"),
    ("C2_Mode-A", "C2 Mode-A"),
    ("C2_Mode-B", "C2 Mode-B"),
    ("C3_Mode-A", "C3 Mode-A"),
    ("C3_Mode-B", "C3 Mode-B"),
)
SEEDS = ("606", "707", "808")
SEED_MARKERS = ("o", "s", "^")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def main() -> None:
    task_rows = [
        row for row in read_csv(RESULTS / "baseline_task_metrics.csv")
        if row["role"] == "ON" and row["target_velocity_mps"] == "6.7"
        and row["target_snr_db"] == "30.0" and row["seed"] in SEEDS
    ]
    track_rows = [
        row for row in read_csv(RESULTS / "track_metrics.csv")
        if row["target_velocity_mps"] == "6.7" and row["target_snr_db"] == "30.0"
        and row["seed"] in SEEDS
    ]
    by_key = {(row["case_key"], row["condition"]): row for row in task_rows}
    track_by_key = {(row["case_key"], row["condition"]): row for row in track_rows}
    for seed in SEEDS:
        rows = [row for row in task_rows if row["seed"] == seed]
        a0 = next(row for row in rows if row["condition"] == "A0")
        c0 = next(row for row in rows if row["condition"] == "C0")
        assert all(a0[key] == c0[key] for key in (
            "cfar_full_hit_count", "final_detection_rows", "strict_truth_hits",
        )), f"A0 and C0 differ for seed {seed}; do not combine them in this figure"

    titles = (
        "Final-union CFAR hits",
        "Final Production Detection records",
        "False confirmed TrackManager IDs",
    )
    fields = (
        ("cfar_full_hit_count", None),
        ("final_detection_rows", None),
        (None, "false_confirmed_track_count"),
    )
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.6), constrained_layout=False)
    colors = ("#44546A", "#4472C4", "#C55A11", "#ED7D31", "#7030A0", "#A64D79")
    x = np.arange(len(CONDITIONS))
    jitter = (-0.09, 0.0, 0.09)
    for axis, title, (task_field, track_field) in zip(axes, titles, fields):
        for index, ((condition, label), color) in enumerate(zip(CONDITIONS, colors)):
            values = []
            for seed in SEEDS:
                case = next(row["case_key"] for row in task_rows
                            if row["seed"] == seed and row["condition"] == condition)
                source = track_by_key[(case, condition)] if track_field else by_key[(case, condition)]
                field = track_field or task_field
                values.append(float(source[field]))
            axis.vlines(index, min(values), max(values), color=color, linewidth=2.2, alpha=0.8)
            for seed_index, value in enumerate(values):
                axis.scatter(index + jitter[seed_index], value, s=48, marker=SEED_MARKERS[seed_index], color=color,
                             edgecolor="white", linewidth=0.7, zorder=3,
                             label=f"seed {SEEDS[seed_index]}" if index == 0 else None)
            axis.scatter(index, float(np.median(values)), marker="D", s=58, color="black", zorder=4)
        axis.set_title(title, fontsize=11)
        axis.set_xticks(x, [label for _, label in CONDITIONS], rotation=32, ha="right")
        axis.grid(axis="y", alpha=0.22)
        axis.set_axisbelow(True)
        axis.set_ylabel("count")
    fig.suptitle("R18 retained data: high-speed, 30 dB pipeline counts",
                 fontsize=14, fontweight="bold", y=0.98)
    fig.text(
        0.5, 0.015,
        "Marker shape identifies seed; black diamond = median; vertical range = min–max. "
        "Mode-A uses privileged paired OFF reference. Counts are descriptive, not Pfa or significance tests.",
        ha="center", fontsize=9,
    )
    fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center",
               bbox_to_anchor=(0.5, 0.925), ncol=3, frameon=False, fontsize=8)
    fig.subplots_adjust(top=0.82, bottom=0.24, wspace=0.28)
    fig.savefig(OUTPUT, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"figure={OUTPUT}")


if __name__ == "__main__":
    main()
