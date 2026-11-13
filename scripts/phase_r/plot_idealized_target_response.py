#!/usr/bin/env python3
"""Plot the ideal equal-amplitude single-pixel CSI response versus phase gap."""

import argparse
import csv
import math
import os
import tempfile
from pathlib import Path


SAMPLE_COUNT = 361


def response_curve():
    """Return |y|/(2A)=|sin(delta/2)| for delta in [0, pi]."""
    points = []
    for index in range(SAMPLE_COUNT):
        delta = math.pi * index / (SAMPLE_COUNT - 1)
        normalized_amplitude = abs(math.sin(delta / 2.0))
        points.append((delta, normalized_amplitude))
    return points


def write_curve_csv(path, points):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("phase_difference_rad", "normalized_output_amplitude"))
        writer.writerows((f"{delta:.17g}", f"{amplitude:.17g}") for delta, amplitude in points)


def write_plot(path, points):
    with tempfile.TemporaryDirectory(prefix="pgrcc_phase_r_mpl_") as mpl_config_dir:
        os.environ["MPLCONFIGDIR"] = mpl_config_dir
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        deltas = [point[0] for point in points]
        amplitudes = [point[1] for point in points]

        fig, ax = plt.subplots(figsize=(7.2, 4.4), constrained_layout=True)
        ax.plot(deltas, amplitudes, color="#1f5a85", linewidth=2.6)
        ax.scatter((0.0, math.pi), (0.0, 1.0), color="#bf4b36", zorder=3)
        ax.annotate(
            "zero response",
            xy=(0.0, 0.0),
            xytext=(0.28, 0.16),
            arrowprops={"arrowstyle": "->", "color": "#555555"},
        )
        ax.annotate(
            "maximum ideal response",
            xy=(math.pi, 1.0),
            xytext=(1.36, 0.79),
            arrowprops={"arrowstyle": "->", "color": "#555555"},
        )
        ax.set_xlim(0.0, math.pi)
        ax.set_ylim(0.0, 1.05)
        ax.set_xticks((0.0, math.pi / 2.0, math.pi), ("0", "π/2", "π"))
        ax.set_yticks((0.0, 0.25, 0.5, 0.75, 1.0))
        ax.set_xlabel("Target-to-clutter phase separation |δ| (rad)")
        ax.set_ylabel("Normalized ideal CSI amplitude |y|/(2A)")
        ax.set_title("Idealized equal-amplitude single-pixel response")
        ax.grid(True, color="#d9dee5", linewidth=0.8)
        ax.text(
            0.02,
            0.98,
            "Analytical model only — not Pd or measured target preservation",
            transform=ax.transAxes,
            va="top",
            fontsize=8.5,
            color="#444444",
        )
        fig.savefig(path, dpi=180, bbox_inches="tight")
        plt.close(fig)


def main():
    repo_root = Path(__file__).resolve().parents[2]
    default_output_dir = repo_root / "docs/research/phase_r/figures"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=default_output_dir)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    points = response_curve()
    csv_path = args.output_dir / "idealized_target_response.csv"
    png_path = args.output_dir / "idealized_target_response.png"
    write_curve_csv(csv_path, points)
    write_plot(png_path, points)
    print(f"curve_csv={csv_path}")
    print(f"figure_png={png_path}")


if __name__ == "__main__":
    main()
