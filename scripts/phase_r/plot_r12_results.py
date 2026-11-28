#!/usr/bin/env python3
"""Render a compact, single-seed summary figure for the R12 pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


LABELS = ["A0", "B", "Known", "D3 fixed", "M1"]
CONDITION_KEYS = ["A", "B", "C", "D3", "M1"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    estimator = manifest["blind_estimator"]
    conditions = manifest["evaluation"]["conditions"]

    delay_methods = [
        ("D1 ordinary LS", estimator["baselines"]["D1_ordinary_LS"]["delta_tau_ns"]),
        ("D2 weighted LS", estimator["baselines"]["D2_weighted_LS"]["delta_tau_ns"]),
        ("D3 Huber LS", estimator["baselines"]["D3_Huber_weighted_LS"]["delta_tau_ns"]),
        ("M1 physics matched", estimator["delta_tau_ns"]),
        ("R9 fixed D3", manifest["design"]["d3_fixed_calibration_ns"]),
    ]

    detection_counts = []
    track_counts = []
    cancellation_db = []
    scnr_db = []
    transfer_db = []
    transfer_phase = []
    for key in CONDITION_KEYS:
        condition = conditions[key]
        on = condition["roles"]["ON"]["metrics"]
        transfer = condition["target_transfer"]
        detection_counts.append(on["unique_correct_detections"])
        track_counts.append(on["correct_track_associations"])
        cancellation_db.append(condition["h0_csi_active_support"]["cancellation_db"])
        scnr_db.append(transfer["output_scnr_proxy_db"])
        paired = transfer["paired_complex_transfer"]
        transfer_db.append(paired["amplitude_ratio_db"])
        transfer_phase.append(paired["phase_error_deg"])

    plt.rcParams.update({"font.size": 9, "axes.titlesize": 10})
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.2), constrained_layout=True)
    fig.suptitle("R12 Native CUDA pilot · seed 404 · descriptive only", fontsize=13)

    ax = axes[0, 0]
    x = np.arange(len(delay_methods))
    vals = [value for _, value in delay_methods]
    ax.scatter(x, vals, s=36, color="#1769aa", zorder=3)
    ax.axhline(4.0, color="#b23a48", linestyle="--", linewidth=1.2)
    ax.set_ylim(3.35, 4.12)
    ax.text(0.03, 4.045, "Injected delay = 4 ns", color="#b23a48", fontsize=8)
    ax.set_xticks(x, [name.replace(" ", "\n", 1) for name, _ in delay_methods])
    ax.set_ylabel("Estimated delay (ns)")
    ax.set_title("Blind estimates and fixed calibration")
    ax.grid(axis="y", alpha=0.25)
    for xi, value in zip(x, vals):
        ax.annotate(f"{value:.3f}", (xi, value), xytext=(0, 6), textcoords="offset points", ha="center", fontsize=8)

    ax = axes[0, 1]
    x = np.arange(len(LABELS))
    width = 0.36
    ax.bar(x - width / 2, detection_counts, width, label="Correct detections / 3", color="#2b8a3e")
    ax.bar(x + width / 2, track_counts, width, label="Correct track associations / 3", color="#f08c00")
    ax.set_xticks(x, LABELS)
    ax.set_ylim(0, 3.5)
    ax.set_ylabel("Count")
    ax.set_title("Frozen task matcher")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False, fontsize=8)

    ax = axes[1, 0]
    x = np.arange(len(LABELS))
    ax.bar(x, cancellation_db, color="#5c7cfa", alpha=0.88)
    ax.set_xticks(x, LABELS)
    ax.set_ylabel("H0 CSI active-support cancellation (dB)")
    ax.set_title("Clutter + noise residual summary")
    ax.grid(axis="y", alpha=0.25)
    ax2 = ax.twinx()
    ax2.plot(x, scnr_db, color="#c92a2a", marker="o", linewidth=1.3, label="Output SCNR proxy")
    ax2.set_ylabel("Output SCNR proxy (dB)", color="#c92a2a")
    ax2.tick_params(axis="y", labelcolor="#c92a2a")
    ax2.legend(frameon=False, loc="lower right", fontsize=8)

    ax = axes[1, 1]
    x = np.arange(len(LABELS))
    ax.bar(x - 0.18, transfer_db, 0.36, color="#845ef7", label="Amplitude ratio (dB)")
    ax.set_xticks(x, LABELS)
    ax.set_ylabel("Paired target-transfer amplitude (dB)")
    ax.set_title("Paired CSI target transfer")
    ax.grid(axis="y", alpha=0.25)
    ax2 = ax.twinx()
    ax2.scatter(x + 0.18, transfer_phase, color="#e8590c", marker="D", s=25, label="Phase error")
    ax2.set_ylabel("Phase error (degrees)", color="#e8590c")
    ax2.tick_params(axis="y", labelcolor="#e8590c")
    ax2.legend(frameon=False, loc="lower right", fontsize=8)

    fig.text(
        0.5,
        -0.01,
        "One seed, three target opportunities. H0 counts are descriptive; unique union false-hit/Pfa is not evaluable.",
        ha="center",
        fontsize=8,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
