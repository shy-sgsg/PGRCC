#!/usr/bin/env python3
"""Compare M3 O1--O5 score maps at a matched empirical Pfa.

This is an explicit post-hoc diagnostic over the measured F1/F2 CSI tap.  O2--O5
fit their complex coefficient separately on each run's active-support tap, so
the result is an upper-bound diagnostic rather than a production detector
evaluation.  The same scorable support and negative-control quantiles are used
for every method; no target truth is used to fit a coefficient.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from run_mechanism_aware_oracle import (
    _row_or_block_complex_ls,
    _short_wiener_alpha,
    read_rows,
)


METHODS = (
    "O1_Current",
    "O2_row_phase_current_amplitude_oracle",
    "O3_row_complex_scalar_LS",
    "O4_range_block_complex_LS",
    "O5_short_Wiener_LMMSE",
)


def manifest_path(run_dir: Path) -> Path:
    paths = sorted((run_dir / "stage2/algorithm_result/period_0000/csi_metrics").glob(
        "*/csi_roi_manifest.csv"))
    if len(paths) != 1:
        raise RuntimeError(f"expected one CSI manifest under {run_dir}, found {paths}")
    return paths[0]


def resolve_array(manifest: Path, value: str) -> np.ndarray:
    path = Path(value)
    if not path.is_absolute():
        path = (manifest.parent / path).resolve()
    if not path.is_file():
        raise RuntimeError(f"missing array: {path}")
    return np.asarray(np.load(path), dtype=np.float64)


def load_run(run_dir: Path) -> dict[str, Any]:
    manifest = manifest_path(run_dir.resolve())
    rows = read_rows(manifest)
    if len(rows) != 1:
        raise RuntimeError(f"expected one CSI manifest row: {manifest}")
    row = rows[0]
    f1 = resolve_array(manifest, row["channel1_real_path"]) + 1j * resolve_array(
        manifest, row["channel1_imag_path"])
    f2 = resolve_array(manifest, row["channel2_real_path"]) + 1j * resolve_array(
        manifest, row["channel2_imag_path"])
    after = resolve_array(manifest, row["after_power_path"])
    before = resolve_array(manifest, row["before_power_path"])
    if not (f1.shape == f2.shape == after.shape == before.shape):
        raise RuntimeError(f"shape mismatch under {run_dir}: {f1.shape}, {f2.shape}, "
                           f"{after.shape}, {before.shape}")
    az_st, az_ed = int(row["az_st"]), int(row["az_ed"])
    rg_st, rg_ed = int(row["rg_st"]), int(row["rg_ed"])
    active = np.zeros(f1.shape, dtype=bool)
    active[az_st:az_ed + 1, rg_st:rg_ed + 1] = True
    pmin = np.minimum(np.abs(f1) ** 2, np.abs(f2) ** 2)
    support = active & np.isfinite(pmin) & np.isfinite(f1.real) & np.isfinite(f1.imag)
    support &= np.isfinite(f2.real) & np.isfinite(f2.imag)
    values = pmin[support]
    if values.size == 0:
        raise RuntimeError(f"no finite active F1/F2 support under {run_dir}")
    floor = float(np.percentile(values, 5.0))
    ceiling = float(np.percentile(values, 99.0))
    valid = support & (pmin >= max(floor, 1.0e-12)) & (pmin <= ceiling)
    return {
        "run_dir": str(run_dir.resolve()),
        "manifest": str(manifest.resolve()),
        "f1": f1, "f2": f2, "after": after, "before": before,
        "valid": valid, "active": active,
        "az_st": az_st, "az_ed": az_ed, "rg_st": rg_st, "rg_ed": rg_ed,
    }


def row_phase_alpha(f1: np.ndarray, f2: np.ndarray, valid: np.ndarray,
                    az_st: int, az_ed: int) -> np.ndarray:
    row_alpha = np.ones(az_ed - az_st + 1, dtype=np.complex128)
    for row in range(az_st, az_ed + 1):
        b = f2[row, valid[row]]
        denominator = float(np.sum(np.abs(b) ** 2))
        if denominator > 1.0e-12:
            alpha = np.sum(f1[row, valid[row]] * np.conj(b)) / denominator
            row_alpha[row - az_st] = alpha / abs(alpha) if abs(alpha) > 1.0e-12 else 1.0
    alpha = np.ones(f1.shape, dtype=np.complex128)
    alpha[az_st:az_ed + 1, :] = row_alpha[:, None]
    return alpha


def row_complex_alpha(f1: np.ndarray, f2: np.ndarray, valid: np.ndarray,
                      az_st: int, az_ed: int) -> np.ndarray:
    alpha = np.ones(f1.shape, dtype=np.complex128)
    for row in range(az_st, az_ed + 1):
        b = f2[row, valid[row]]
        denominator = float(np.sum(np.abs(b) ** 2))
        if denominator > 1.0e-12:
            alpha[row, :] = np.sum(f1[row, valid[row]] * np.conj(b)) / denominator
    return alpha


def score_maps(run: dict[str, Any], score_mask: np.ndarray | None = None) -> dict[str, np.ndarray]:
    f1, f2, valid = run["f1"], run["f2"], run["valid"]
    az_st, az_ed = run["az_st"], run["az_ed"]
    rg_st, rg_ed = run["rg_st"], run["rg_ed"]
    scores = {"O1_Current": run["after"].copy()}
    alpha_o2 = row_phase_alpha(f1, f2, valid, az_st, az_ed)
    alpha_o3 = row_complex_alpha(f1, f2, valid, az_st, az_ed)
    alpha_o4 = _row_or_block_complex_ls(
        f1, f2, valid, az_st, az_ed, rg_st, rg_ed, 64)
    alpha_o5 = _short_wiener_alpha(
        f1, f2, valid, az_st, az_ed, rg_st, rg_ed)
    for name, alpha, balanced in (
        ("O2_row_phase_current_amplitude_oracle", alpha_o2, True),
        ("O3_row_complex_scalar_LS", alpha_o3, False),
        ("O4_range_block_complex_LS", alpha_o4, False),
        ("O5_short_Wiener_LMMSE", alpha_o5, False),
    ):
        target = alpha * f2
        if balanced:
            a_abs, b_abs = np.abs(f1), np.abs(target)
            residual = np.where(a_abs > 0.0, np.minimum(a_abs, b_abs) / a_abs * f1, 0.0)
            residual -= np.where(b_abs > 0.0, np.minimum(a_abs, b_abs) / b_abs * target, 0.0)
        else:
            residual = f1 - target
        scores[name] = np.abs(residual) ** 2
    scorable = run["valid"] if score_mask is None else np.asarray(score_mask, dtype=bool)
    for name in METHODS:
        value = scores[name]
        scores[name] = np.where(scorable & np.isfinite(value) & (value >= 0.0), value, np.nan)
    return scores


def peak(score: np.ndarray, row: int, col: int, half_rows: int, half_cols: int) -> float:
    roi = score[max(0, row - half_rows):min(score.shape[0], row + half_rows + 1),
                max(0, col - half_cols):min(score.shape[1], col + half_cols + 1)]
    values = roi[np.isfinite(roi) & (roi >= 0.0)]
    return float(np.max(values)) if values.size else math.nan


def json_float(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--positive-run", type=Path, required=True)
    parser.add_argument("--negative-run", type=Path, required=True)
    parser.add_argument("--target-only-run", type=Path, required=True)
    parser.add_argument("--target-row", type=int, required=True)
    parser.add_argument("--target-col", type=int, required=True)
    parser.add_argument("--half-doppler-bins", type=int, default=2)
    parser.add_argument("--half-range-bins", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pfa", nargs="+", type=float,
                        default=[1.0e-2, 1.0e-3, 1.0e-4])
    args = parser.parse_args()
    positive, negative, target_only = (load_run(path) for path in (
        args.positive_run, args.negative_run, args.target_only_run))
    if positive["f1"].shape != negative["f1"].shape or positive["f1"].shape != target_only["f1"].shape:
        raise RuntimeError("positive/negative/target-only map shapes differ")
    rows: list[dict[str, Any]] = []
    common_valid = positive["valid"] & negative["valid"]
    positive_scores, negative_scores, target_scores = (
        score_maps(positive, common_valid), score_maps(negative, common_valid),
        score_maps(target_only, common_valid))
    for method in METHODS:
        negative_values = negative_scores[method]
        negative_values = negative_values[np.isfinite(negative_values) & (negative_values >= 0.0)]
        if negative_values.size == 0:
            raise RuntimeError(f"no negative score cells for {method}")
        positive_peak = peak(positive_scores[method], args.target_row, args.target_col,
                             args.half_doppler_bins, args.half_range_bins)
        negative_peak = peak(negative_scores[method], args.target_row, args.target_col,
                             args.half_doppler_bins, args.half_range_bins)
        target_peak = peak(target_scores[method], args.target_row, args.target_col,
                           args.half_doppler_bins, args.half_range_bins)
        positive_peak_json = json_float(positive_peak)
        negative_peak_json = json_float(negative_peak)
        target_peak_json = json_float(target_peak)
        target_loss = (10.0 * math.log10(
            max(positive_peak, np.finfo(float).tiny) /
            max(target_peak, np.finfo(float).tiny))
                       if math.isfinite(positive_peak) and math.isfinite(target_peak) else math.nan)
        for requested in args.pfa:
            if not (0.0 < requested < 1.0):
                raise SystemExit(f"Pfa must be in (0,1): {requested}")
            threshold = float(np.quantile(negative_values, 1.0 - requested, method="higher"))
            empirical = float(np.mean(negative_values >= threshold))
            rows.append({
                "method": method,
                "requested_pfa": requested,
                "threshold": threshold,
                "empirical_pfa": empirical,
                "positive_roi_peak_power": positive_peak_json,
                "negative_roi_peak_power": negative_peak_json,
                "target_only_roi_peak_power": target_peak_json,
                "causal_pd": bool(math.isfinite(positive_peak) and positive_peak >= threshold),
                "paired_causal_hit": bool(math.isfinite(positive_peak) and
                                           math.isfinite(negative_peak) and
                                           positive_peak >= threshold and negative_peak < threshold),
                "target_loss_db_vs_target_only_peak": json_float(target_loss),
                "negative_score_cell_count": int(negative_values.size),
                "pfa_calibration_status": "measured_from_negative_control_active_support",
            })
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["method", "requested_pfa"]
    with (output / "m3_oracle_fixed_pfa.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "schema_version": 1,
        "methods": list(METHODS),
        "positive_run": positive["run_dir"],
        "negative_run": negative["run_dir"],
        "target_only_run": target_only["run_dir"],
        "target_roi": {"row": args.target_row, "col": args.target_col,
                       "half_doppler_bins": args.half_doppler_bins,
                       "half_range_bins": args.half_range_bins},
        "score_domain": "same_paired_positive_negative_valid_active_support_for_all_O1_O5",
        "fit_scope": "each_run_F1_F2_only; target_truth_not_used_for_coefficient_fit",
        "production_status": "posthoc_upper_bound_not_production_detector",
        "pfa_definition": "empirical quantile of negative-control score cells in the active support",
        "rows": rows,
        "ai_training": False,
    }
    (output / "m3_oracle_fixed_pfa_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "row_count": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
