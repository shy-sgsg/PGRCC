#!/usr/bin/env python3
"""Calibrate and compare detection thresholds at matched empirical Pfa.

This compact evaluator consumes score maps, preferably production
``after_power`` maps.  The negative-control map supplies the empirical
threshold at each requested Pfa; target-on ROI peak scores then give causal Pd
at that same threshold.  It does not use a configured Pfa as if it were an
empirical measurement.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


def load_score(path: Path) -> np.ndarray:
    if not path.is_file():
        raise SystemExit(f"missing score map: {path}")
    value = np.asarray(np.load(path), dtype=np.float64)
    if value.ndim != 2:
        raise SystemExit(f"score map must be 2-D: {path} shape={value.shape}")
    return value


def finite_scores(value: np.ndarray) -> np.ndarray:
    result = value[np.isfinite(value) & (value >= 0.0)]
    if result.size == 0:
        raise SystemExit("score map has no finite non-negative cells")
    return result


def roi_peak(value: np.ndarray, row: int, col: int, half_rows: int,
             half_cols: int) -> float:
    roi = value[max(0, row - half_rows):min(value.shape[0], row + half_rows + 1),
                max(0, col - half_cols):min(value.shape[1], col + half_cols + 1)]
    scores = finite_scores(roi)
    return float(np.max(scores))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--negative-score", type=Path, required=True,
                        help="negative-control score map (.npy)")
    parser.add_argument("--positive-score", type=Path, required=True,
                        help="target-on score map (.npy)")
    parser.add_argument("--target-row", type=int, required=True)
    parser.add_argument("--target-col", type=int, required=True)
    parser.add_argument("--target-only-score", type=Path, default=None)
    parser.add_argument("--half-doppler-bins", type=int, default=2)
    parser.add_argument("--half-range-bins", type=int, default=2)
    parser.add_argument("--method", default="Current")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pfa", nargs="+", type=float, default=[1.0e-2, 1.0e-3, 1.0e-4])
    args = parser.parse_args()
    negative = load_score(args.negative_score)
    positive = load_score(args.positive_score)
    if negative.shape != positive.shape:
        raise SystemExit(f"negative/positive shapes differ: {negative.shape} vs {positive.shape}")
    target_only = load_score(args.target_only_score) if args.target_only_score else None
    if target_only is not None and target_only.shape != positive.shape:
        raise SystemExit("target-only score shape differs from positive score")
    negative_values = finite_scores(negative)
    positive_roi_peak = roi_peak(positive, args.target_row, args.target_col,
                                 args.half_doppler_bins, args.half_range_bins)
    negative_roi_peak = roi_peak(negative, args.target_row, args.target_col,
                                 args.half_doppler_bins, args.half_range_bins)
    target_only_peak = (roi_peak(target_only, args.target_row, args.target_col,
                                 args.half_doppler_bins, args.half_range_bins)
                        if target_only is not None else None)
    rows: list[dict[str, Any]] = []
    for requested in args.pfa:
        if not (0.0 < requested < 1.0):
            raise SystemExit(f"Pfa must be in (0,1): {requested}")
        threshold = float(np.quantile(negative_values, 1.0 - requested,
                                      method="higher"))
        empirical = float(np.mean(negative_values >= threshold))
        causal_pd = float(positive_roi_peak >= threshold)
        paired_causal_hit = bool(causal_pd and negative_roi_peak < threshold)
        target_loss = (10.0 * math.log10(max(positive_roi_peak, np.finfo(float).tiny) /
                                           max(float(target_only_peak), np.finfo(float).tiny))
                       if target_only_peak is not None else None)
        rows.append({
            "method": args.method,
            "requested_pfa": requested,
            "threshold": threshold,
            "empirical_pfa": empirical,
            "causal_pd": causal_pd,
            "paired_causal_hit": paired_causal_hit,
            "positive_roi_peak_power": positive_roi_peak,
            "negative_roi_peak_power": negative_roi_peak,
            "negative_score_cell_count": int(negative_values.size),
            "target_only_roi_peak_power": target_only_peak,
            "target_loss_db_vs_target_only_peak": target_loss,
            "scnr_db_vs_negative_peak": 10.0 * math.log10(
                max(positive_roi_peak, np.finfo(float).tiny) /
                max(float(np.max(negative_values)), np.finfo(float).tiny)),
            "pfa_calibration_status": "measured_from_negative_control_map",
        })
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "fixed_pfa_roc.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = list(rows[0]) if rows else ["method", "requested_pfa"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "schema_version": 1,
        "method": args.method,
        "negative_score_source": str(args.negative_score.resolve()),
        "positive_score_source": str(args.positive_score.resolve()),
        "target_only_score_source": str(args.target_only_score.resolve()) if args.target_only_score else None,
        "target_roi": {"row": args.target_row, "col": args.target_col,
                       "half_doppler_bins": args.half_doppler_bins,
                       "half_range_bins": args.half_range_bins},
        "paired_causal_definition": "positive target ROI reaches the calibrated threshold while the same target-off ROI remains below it",
        "rows": rows,
        "ai_training": False,
    }
    (output / "fixed_pfa_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "method": args.method,
                      "rows": rows}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
