#!/usr/bin/env python3
"""Compute P5 CSI metrics from production GMTI CSI taps.

The script only reduces matrices captured between the production CSI kernel and
production CFAR.  It does not implement an alternative cancellation pipeline.
"""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

import numpy as np

from gmti_eval_metrics import (
    estimate_target_power,
    linear_power_ratio_db,
    summarize_power,
)


COMMON_FIELDS = [
    "case_id", "experiment_id", "run_id", "result_id", "config_hash",
    "data_hash", "git_commit", "build_type", "random_seed",
    "algorithm_exe", "period_id", "beam_id", "target_id",
]


def read_csv(path):
    if path is None or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path):
    if path is None or not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value, default=math.nan):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result


def resolve_array_path(tap_dir, value):
    if value is None or not str(value).strip():
        raise RuntimeError(
            "CSI power-map path is empty; rerun GMTI_core with "
            "csi_metrics_dump_power_maps=1 for formal P5/P8 metrics")
    path = Path(value)
    if path.is_file():
        return path
    candidate = tap_dir / path
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(value)


def discover_tap_dir(output_dir):
    root = output_dir / "csi_metrics"
    manifests = list(root.glob("*/csi_roi_manifest.csv"))
    if not manifests:
        raise FileNotFoundError(f"no CSI manifest below {root}")
    return max(manifests, key=lambda item: item.stat().st_mtime).parent


def load_taps(tap_dir):
    rows = read_csv(tap_dir / "csi_roi_manifest.csv")
    if not rows:
        raise RuntimeError("empty csi_roi_manifest.csv")
    taps = {}
    for row in rows:
        beam = as_int(row.get("beam_id"), -1)
        before = np.load(resolve_array_path(tap_dir, row["before_power_path"]))
        after = np.load(resolve_array_path(tap_dir, row["after_power_path"]))
        fa_axis = np.load(resolve_array_path(tap_dir, row["fa_axis_path"]))
        range_axis = np.load(resolve_array_path(tap_dir, row["range_axis_path"]))
        if before.shape != after.shape:
            raise RuntimeError(f"beam {beam}: before/after shape mismatch")
        if before.shape != (as_int(row["rows"]), as_int(row["cols"])):
            raise RuntimeError(f"beam {beam}: manifest shape mismatch")
        taps[beam] = {
            "manifest": row,
            "before": before.astype(np.float64, copy=False),
            "after": after.astype(np.float64, copy=False),
            "fa_axis": np.ravel(fa_axis).astype(np.float64, copy=False),
            "range_axis": np.ravel(range_axis).astype(np.float64, copy=False),
        }
    return taps


def resample_rows_to_axis(power, source_axis, target_axis):
    """Resample a power map onto a physical Doppler axis, column by column."""
    source_axis = np.asarray(source_axis, dtype=np.float64)
    target_axis = np.asarray(target_axis, dtype=np.float64)
    if source_axis.ndim != 1 or target_axis.ndim != 1:
        raise RuntimeError("Doppler axes must be one-dimensional")
    if power.shape[0] != source_axis.size:
        raise RuntimeError("power/source Doppler axis shape mismatch")
    if source_axis.size < 2 or np.any(np.diff(source_axis) <= 0.0):
        raise RuntimeError("source Doppler axis must be strictly increasing")
    output = np.empty((target_axis.size, power.shape[1]), dtype=np.float64)
    for col in range(power.shape[1]):
        output[:, col] = np.interp(
            target_axis, source_axis, power[:, col], left=np.nan, right=np.nan)
    return output


def map_frequency_to_dft_axis(frequency_hz, axis_hz):
    """Map an unwrapped Doppler frequency to the equivalent row of one DFT period.

    Production ``fa_axis`` contains one PRF-wide FFT interval while Stage2/Stage3
    truth deliberately preserves the unwrapped total Doppler.  Comparing those
    values directly sends high-order truth to an edge row.  Infer PRF from the
    uniform DFT axis and add an integer multiple that is closest to its centre.
    """
    axis = np.asarray(axis_hz, dtype=np.float64)
    if not math.isfinite(frequency_hz) or axis.ndim != 1 or axis.size < 2:
        return frequency_hz, 0
    steps = np.diff(axis)
    step_hz = float(np.median(steps))
    if not (step_hz > 0.0) or not np.allclose(steps, step_hz, rtol=1e-5, atol=1e-6):
        raise RuntimeError("Doppler axis must be uniformly increasing")
    prf_hz = step_hz * axis.size
    axis_center_hz = 0.5 * (float(axis[0]) + float(axis[-1]))
    wrap_order = int(round((axis_center_hz - frequency_hz) / prf_hz))
    return frequency_hz + wrap_order * prf_hz, wrap_order


def load_truth(truth_dir):
    if truth_dir is None:
        return {}, {}
    summaries = read_csv(truth_dir / "truth_targets_by_beam.csv")
    pulses = read_csv(truth_dir / "truth_pulse.csv")
    truth_by_beam = {}
    for row in summaries:
        visible = str(row.get("visible", row.get("visible_by_beam", "1"))).lower()
        if visible not in {"1", "true", "yes"}:
            continue
        beam = as_int(row.get("beam_id"), -1)
        target_id = row.get("target_id", "")
        if beam < 0 or not target_id:
            continue
        truth_by_beam.setdefault(beam, []).append(row)
    af_values = {}
    for row in pulses:
        enabled = str(row.get("injection_enabled", "1")).lower()
        if enabled not in {"1", "true", "yes"}:
            continue
        beam = as_int(row.get("beam_id_1based", row.get("beam_id")), -1)
        target_id = row.get("target_name", row.get("target_id", ""))
        af = as_float(row.get("af_total_truth_hz"))
        if beam >= 0 and target_id and math.isfinite(af):
            af_values.setdefault((beam, target_id), []).append(af)
    af_truth = {key: statistics.median(values) for key, values in af_values.items()}
    return truth_by_beam, af_truth


def clipped_rect(shape, row, col, half_rows, half_cols):
    row0 = max(0, row - half_rows)
    row1 = min(shape[0] - 1, row + half_rows)
    col0 = max(0, col - half_cols)
    col1 = min(shape[1] - 1, col + half_cols)
    mask = np.zeros(shape, dtype=bool)
    if row0 <= row1 and col0 <= col1:
        mask[row0:row1 + 1, col0:col1 + 1] = True
    return mask


def target_centers(tap, truths, af_truth):
    centers = []
    beam = as_int(tap["manifest"]["beam_id"], -1)
    for truth in truths:
        target_id = truth.get("target_id", "")
        col = as_int(truth.get("expected_bin", truth.get("range_bin")), -1)
        af = af_truth.get((beam, target_id), math.nan)
        if math.isfinite(af):
            axis_af, wrap_order = map_frequency_to_dft_axis(af, tap["fa_axis"])
            row = int(np.argmin(np.abs(tap["fa_axis"] - axis_af)))
        else:
            axis_af = math.nan
            wrap_order = 0
            row = as_int(truth.get("row_truth"), -1)
        if 0 <= row < tap["before"].shape[0] and 0 <= col < tap["before"].shape[1]:
            centers.append((truth, row, col, af, axis_af, wrap_order))
    return centers


def active_support_mask(tap):
    row = tap["manifest"]
    mask = np.zeros(tap["before"].shape, dtype=bool)
    r0 = max(0, as_int(row["az_st"]))
    r1 = min(mask.shape[0] - 1, as_int(row["az_ed"]))
    c0 = max(0, as_int(row["rg_st"]))
    c1 = min(mask.shape[1] - 1, as_int(row["rg_ed"]))
    if r0 <= r1 and c0 <= c1:
        mask[r0:r1 + 1, c0:c1 + 1] = True
    return mask


def full_range_support_mask(tap):
    """All Doppler rows over the configured valid range support.

    CSI is produced for the full RD matrix and ``union``/``full`` CFAR may
    legitimately detect a moving target outside the dynamic clutter band.
    The dynamic band remains the background-CA ROI, but must not clip a truth
    target ROI to zero cells.
    """
    row = tap["manifest"]
    mask = np.zeros(tap["before"].shape, dtype=bool)
    c0 = max(0, as_int(row["rg_st"]))
    c1 = min(mask.shape[1] - 1, as_int(row["rg_ed"]))
    if c0 <= c1:
        mask[:, c0:c1 + 1] = True
    return mask


def remove_strong_peaks(mask, before, threshold_db):
    valid = before[mask & np.isfinite(before) & (before >= 0.0)]
    if valid.size == 0:
        return mask, math.nan
    median = float(np.median(valid))
    if not (median > 0.0):
        return mask, math.nan
    threshold = median * 10.0 ** (threshold_db / 10.0)
    return mask & (before <= threshold), threshold


def provenance(args, algorithm_output, manifest_row):
    manifest = {}
    run_manifest = algorithm_output / "run_manifest.json"
    if run_manifest.exists():
        manifest = json.loads(run_manifest.read_text(encoding="utf-8"))
    return {
        "case_id": manifest_row.get("case_id", manifest.get("case_id", "")),
        "experiment_id": args.experiment_id,
        "run_id": manifest_row.get("run_id", manifest.get("run_id", "")),
        "result_id": manifest_row.get("result_id", manifest.get("result_id", "")),
        "config_hash": sha256_file(args.config_path),
        "data_hash": sha256_file(args.data_path),
        "git_commit": manifest.get("git_commit", "unknown"),
        "build_type": manifest.get("build_type", "unknown"),
        "random_seed": args.random_seed,
        "algorithm_exe": manifest.get("executable", "GMTI_core"),
        "period_id": as_int(manifest_row.get("period_id"), 0),
        "beam_id": as_int(manifest_row.get("beam_id"), -1),
        "target_id": "",
    }


def add_power_fields(row, before_stats, after_stats):
    row.update({
        "valid_background_cells": min(before_stats["valid_count"], after_stats["valid_count"]),
        "before_mean_power": before_stats["mean_power"],
        "after_mean_power": after_stats["mean_power"],
        "before_median_power": before_stats["median_power"],
        "after_median_power": after_stats["median_power"],
        "before_rms": before_stats["rms"],
        "after_rms": after_stats["rms"],
        "before_p95": before_stats["p95"],
        "after_p95": after_stats["p95"],
        "before_max": before_stats["max"],
        "after_max": after_stats["max"],
        "CA_ROI_dB": linear_power_ratio_db(before_stats["mean_power"], after_stats["mean_power"]),
        "CSR_ROI_dB": linear_power_ratio_db(before_stats["mean_power"], after_stats["mean_power"]),
        "csr_method": "background_power_proxy",
    })


def make_plots(report_dir, taps):
    import os
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti-matplotlib")
    import matplotlib.pyplot as plt

    for beam, tap in taps.items():
        before = tap["before"]
        after = tap["after"]
        epsilon = max(float(np.nanmedian(before[before > 0])) * 1e-9, 1e-30)
        images = [
            (before, "CSI before power", "csi_before_power"),
            (after, "CSI after power", "csi_after_power"),
            (after - before, "CSI after - before linear power", "csi_difference_power"),
        ]
        for values, title, stem in images:
            display = 10.0 * np.log10(np.maximum(np.abs(values), epsilon))
            finite = display[np.isfinite(display)]
            if finite.size == 0:
                continue
            lo, hi = np.percentile(finite, [1.0, 99.5])
            fig, axis = plt.subplots(figsize=(10, 4))
            image = axis.imshow(display, aspect="auto", origin="lower", vmin=lo, vmax=hi)
            axis.set_title(f"{title} — beam {beam}")
            axis.set_xlabel("range bin")
            axis.set_ylabel("Doppler row")
            fig.colorbar(image, ax=axis, label="dB (display only)")
            fig.tight_layout()
            fig.savefig(report_dir / f"{stem}_beam{beam:03d}.png", dpi=140)
            plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--algorithm-output-dir", required=True, type=Path)
    parser.add_argument("--tap-dir", type=Path)
    parser.add_argument("--truth-dir", type=Path)
    parser.add_argument("--paired-off-output-dir", type=Path)
    parser.add_argument("--paired-off-tap-dir", type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--experiment-id", default="p5_csi")
    parser.add_argument("--random-seed", default="")
    parser.add_argument("--config-path", type=Path)
    parser.add_argument("--data-path", type=Path)
    parser.add_argument("--plots", action="store_true")
    args = parser.parse_args()

    tap_dir = args.tap_dir or discover_tap_dir(args.algorithm_output_dir)
    taps = load_taps(tap_dir)
    off_taps = {}
    if args.paired_off_tap_dir:
        off_taps = load_taps(args.paired_off_tap_dir)
    elif args.paired_off_output_dir:
        off_taps = load_taps(discover_tap_dir(args.paired_off_output_dir))
    truth_by_beam, af_truth = load_truth(args.truth_dir)
    args.report_dir.mkdir(parents=True, exist_ok=True)

    cancellation = []
    scnr = []
    residual = []
    roi_manifest = []
    for beam, tap in sorted(taps.items()):
        manifest = tap["manifest"]
        common = provenance(args, args.algorithm_output_dir, manifest)
        truths = truth_by_beam.get(beam, [])
        centers = target_centers(tap, truths, af_truth)
        active = active_support_mask(tap)
        target_support = full_range_support_mask(tap)
        guard_union = np.zeros(active.shape, dtype=bool)
        for _, row, col, _, _, _ in centers:
            guard_union |= clipped_rect(
                active.shape, row, col,
                as_int(manifest["guard_doppler_bins"]),
                as_int(manifest["guard_range_bins"]),
            )

        background_specs = []
        if centers:
            for truth, row, col, _, _, _ in centers:
                local = clipped_rect(
                    active.shape, row, col,
                    as_int(manifest["background_doppler_bins"]),
                    as_int(manifest["background_range_bins"]),
                )
                background_specs.append((truth.get("target_id", ""), local & active & ~guard_union))
        else:
            background_specs.append(("", active & ~guard_union))

        for target_id, background_mask in background_specs:
            filtered_mask, strong_threshold = remove_strong_peaks(
                background_mask, tap["before"],
                as_float(manifest["strong_peak_threshold_db"], 15.0),
            )
            before_stats = summarize_power(tap["before"][filtered_mask])
            after_stats = summarize_power(tap["after"][filtered_mask])
            row = dict(common)
            row["target_id"] = target_id
            row["roi_name"] = f"target_{target_id}_background" if target_id else "background_support"
            row["strong_peak_linear_threshold"] = strong_threshold
            add_power_fields(row, before_stats, after_stats)
            minimum = as_int(manifest["min_valid_background_cells"], 100)
            row["status"] = "ok" if row["valid_background_cells"] >= minimum else "insufficient_background_cells"
            cancellation.append(row)

        off_tap = off_taps.get(beam)
        for truth, center_row, center_col, af, axis_af, wrap_order in centers:
            target_id = truth.get("target_id", "")
            target_mask = clipped_rect(
                active.shape, center_row, center_col,
                as_int(manifest["target_half_doppler_bins"]),
                as_int(manifest["target_half_range_bins"]),
            ) & target_support
            local_background = next((r for r in cancellation
                                     if r["beam_id"] == beam and r["target_id"] == target_id), None)
            method = "local_guard_ring"
            if off_tap is not None:
                if off_tap["before"].shape[1] != tap["before"].shape[1] or not np.allclose(
                        off_tap["range_axis"], tap["range_axis"], rtol=0.0, atol=1e-3):
                    raise RuntimeError(f"beam {beam}: paired-off range axes do not match")
                if np.allclose(off_tap["fa_axis"], tap["fa_axis"], rtol=0.0, atol=1e-5):
                    off_before_aligned = off_tap["before"]
                    off_after_aligned = off_tap["after"]
                    paired_axis_alignment = "exact"
                else:
                    off_before_aligned = resample_rows_to_axis(
                        off_tap["before"], off_tap["fa_axis"], tap["fa_axis"])
                    off_after_aligned = resample_rows_to_axis(
                        off_tap["after"], off_tap["fa_axis"], tap["fa_axis"])
                    paired_axis_alignment = "linear_doppler_interpolation"
                before_off = off_before_aligned[target_mask]
                after_off = off_after_aligned[target_mask]
                before_on = tap["before"][target_mask]
                after_on = tap["after"][target_mask]
                finite = (np.isfinite(before_off) & np.isfinite(after_off) &
                          np.isfinite(before_on) & np.isfinite(after_on))
                before_off = before_off[finite]
                after_off = after_off[finite]
                before_on = before_on[finite]
                after_on = after_on[finite]
                s_before = max(0.0, float(np.sum(before_on) - np.sum(before_off)))
                s_after = max(0.0, float(np.sum(after_on) - np.sum(after_off)))
                c_before = float(np.sum(before_off))
                c_after = float(np.sum(after_off))
                method = "paired_target_off"
            elif local_background is not None:
                before_mean = as_float(local_background["before_mean_power"])
                after_mean = as_float(local_background["after_mean_power"])
                s_before = estimate_target_power(tap["before"][target_mask], before_mean)
                s_after = estimate_target_power(tap["after"][target_mask], after_mean)
                cells = int(np.count_nonzero(target_mask))
                c_before = before_mean * cells
                c_after = after_mean * cells
            else:
                s_before = s_after = c_before = c_after = math.nan

            row = dict(common)
            row.update({
                "target_id": target_id,
                "truth_row": center_row,
                "truth_range_bin": center_col,
                "truth_total_doppler_hz": af,
                "truth_axis_doppler_hz": axis_af,
                "truth_axis_wrap_order": wrap_order,
                "target_roi_cells": int(np.count_nonzero(target_mask)),
                "estimation_method": method,
                "paired_axis_alignment": paired_axis_alignment if off_tap is not None else "not_applicable",
                "S_before": s_before,
                "S_after": s_after,
                "C_before": c_before,
                "C_after": c_after,
                "N_before": "",
                "N_after": "",
                "SCNR_before_dB": linear_power_ratio_db(s_before, c_before),
                "SCNR_after_dB": linear_power_ratio_db(s_after, c_after),
                "target_gain_dB": linear_power_ratio_db(s_after, s_before),
                "CA_local_dB": linear_power_ratio_db(c_before, c_after),
            })
            row["SCNR_improvement_dB"] = (
                row["SCNR_after_dB"] - row["SCNR_before_dB"]
                if math.isfinite(row["SCNR_after_dB"]) and math.isfinite(row["SCNR_before_dB"])
                else math.nan
            )
            row["target_loss_dB"] = -row["target_gain_dB"] if math.isfinite(row["target_gain_dB"]) else math.nan
            row["status"] = "ok" if s_before > 0.0 and s_after > 0.0 and c_before > 0.0 and c_after > 0.0 else "non_positive_power_estimate"
            scnr.append(row)

        finite_after = tap["after"][active & np.isfinite(tap["after"]) & (tap["after"] >= 0.0)]
        if finite_after.size:
            median_after = float(np.median(finite_after))
            threshold = median_after * 10.0 ** (
                as_float(manifest["strong_peak_threshold_db"], 15.0) / 10.0)
            indexes = np.argwhere(active & (tap["after"] > threshold))
            powers = [float(tap["after"][tuple(index)]) for index in indexes]
            order = np.argsort(powers)[::-1][:200]
            for rank, index_in_list in enumerate(order, 1):
                row_index, col_index = indexes[index_in_list]
                protected = any(abs(row_index - r) <= as_int(manifest["guard_doppler_bins"]) and
                                abs(col_index - c) <= as_int(manifest["guard_range_bins"])
                                for _, r, c, _, _, _ in centers)
                row = dict(common)
                row.update({
                    "target_id": "",
                    "rank": rank,
                    "row": int(row_index),
                    "col": int(col_index),
                    "fa_hz": float(tap["fa_axis"][row_index]),
                    "range_m": float(tap["range_axis"][col_index]),
                    "after_power": powers[index_in_list],
                    "threshold_power": threshold,
                    "inside_truth_guard": int(protected),
                })
                residual.append(row)

        roi_row = dict(common)
        roi_row.update(manifest)
        roi_manifest.append(roi_row)

    cancellation_fields = COMMON_FIELDS + [
        "roi_name", "valid_background_cells", "before_mean_power", "after_mean_power",
        "before_median_power", "after_median_power", "before_rms", "after_rms",
        "before_p95", "after_p95", "before_max", "after_max", "CA_ROI_dB",
        "CSR_ROI_dB", "csr_method", "strong_peak_linear_threshold", "status",
    ]
    scnr_fields = COMMON_FIELDS + [
        "truth_row", "truth_range_bin", "truth_total_doppler_hz",
        "truth_axis_doppler_hz", "truth_axis_wrap_order", "target_roi_cells",
        "estimation_method", "S_before", "S_after", "C_before", "C_after",
        "paired_axis_alignment",
        "N_before", "N_after", "SCNR_before_dB", "SCNR_after_dB",
        "SCNR_improvement_dB", "target_gain_dB", "target_loss_dB", "CA_local_dB",
        "status",
    ]
    residual_fields = COMMON_FIELDS + [
        "rank", "row", "col", "fa_hz", "range_m", "after_power",
        "threshold_power", "inside_truth_guard",
    ]
    write_csv(args.report_dir / "cancellation_metrics.csv", cancellation, cancellation_fields)
    write_csv(args.report_dir / "scnr_improvement_metrics.csv", scnr, scnr_fields)
    write_csv(args.report_dir / "residual_peaks.csv", residual, residual_fields)
    manifest_fields = list(dict.fromkeys(COMMON_FIELDS + list(roi_manifest[0].keys())))
    write_csv(args.report_dir / "csi_roi_manifest.csv", roi_manifest, manifest_fields)

    valid_ca = [as_float(row["CA_ROI_dB"]) for row in cancellation
                if row["status"] == "ok" and math.isfinite(as_float(row["CA_ROI_dB"]))]
    valid_scnr = [as_float(row["SCNR_improvement_dB"]) for row in scnr
                  if row["status"] == "ok" and math.isfinite(as_float(row["SCNR_improvement_dB"]))]
    valid_loss = [as_float(row["target_loss_dB"]) for row in scnr
                  if row["status"] == "ok" and math.isfinite(as_float(row["target_loss_dB"]))]
    target_metric_count = len(scnr)
    summary = [{
        "experiment_id": args.experiment_id,
        "beam_count": len(taps),
        "background_roi_count": len(cancellation),
        "target_metric_count": len(scnr),
        "valid_background_roi_count": len(valid_ca),
        "valid_target_metric_count": len(valid_scnr),
        "mean_CA_ROI_dB": float(np.mean(valid_ca)) if valid_ca else math.nan,
        "mean_SCNR_improvement_dB": float(np.mean(valid_scnr)) if valid_scnr else math.nan,
        "mean_target_loss_dB": float(np.mean(valid_loss)) if valid_loss else math.nan,
        "residual_peak_count": len(residual),
        "status": "ok" if valid_ca and (target_metric_count == 0 or valid_scnr) else "incomplete",
    }]
    write_csv(args.report_dir / "csi_metric_summary.csv", summary, list(summary[0]))
    if args.plots:
        make_plots(args.report_dir, taps)

    evidence = {
        "tap_dir": str(tap_dir),
        "paired_off_tap_dir": str(args.paired_off_tap_dir or args.paired_off_output_dir or ""),
        "truth_dir": str(args.truth_dir or ""),
        "config_path": str(args.config_path or ""),
        "config_hash": sha256_file(args.config_path),
        "data_path": str(args.data_path or ""),
        "data_hash": sha256_file(args.data_path),
        "metric_definition": "all dB values are converted from linear power ratios",
        "summary": summary[0],
    }
    (args.report_dir / "p5_evaluation_manifest.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary[0], ensure_ascii=False))


if __name__ == "__main__":
    main()
