#!/usr/bin/env python3
"""Metric reducers shared by GMTI P4 and P5 evaluation."""

import math

import numpy as np


def mean_finite(rows, key):
    vals = []
    for row in rows:
        try:
            v = float(row.get(key, "nan"))
        except (TypeError, ValueError):
            v = math.nan
        if math.isfinite(v):
            vals.append(v)
    return sum(vals) / len(vals) if vals else ""


def detection_metrics(num_truth, num_det, matches, false_alarms):
    tp = len(matches)
    fp = len(false_alarms)
    fn = max(0, num_truth - tp)
    return [{
        "truth_count": num_truth,
        "detection_count": num_det,
        "matched_count": tp,
        "false_alarm_count": fp,
        "missed_truth_count": fn,
        "precision": tp / num_det if num_det else (1.0 if num_truth == 0 else 0.0),
        "recall": tp / num_truth if num_truth else 1.0,
        "mean_position_error_m": mean_finite(matches, "position_error_m"),
        "mean_range_error_m": mean_finite(matches, "range_error_m"),
        "mean_velocity_error_mps": mean_finite(matches, "velocity_error_mps"),
    }]


def tracking_metrics(num_truth, num_tracks, matches):
    tp = len(matches)
    fn = max(0, num_truth - tp)
    fp = max(0, num_tracks - tp)
    return [{
        "truth_count": num_truth,
        "track_count": num_tracks,
        "matched_track_count": tp,
        "false_track_count": fp,
        "missed_truth_count": fn,
        "track_recall": tp / num_truth if num_truth else 1.0,
        "track_precision": tp / num_tracks if num_tracks else (1.0 if num_truth == 0 else 0.0),
        "mean_position_error_m": mean_finite(matches, "position_error_m"),
        "mean_velocity_error_mps": mean_finite(matches, "velocity_error_mps"),
    }]


def linear_power_ratio_db(numerator, denominator):
    """Convert a ratio of linear powers to dB; never divide dB values."""
    numerator = float(numerator)
    denominator = float(denominator)
    if not (math.isfinite(numerator) and math.isfinite(denominator)):
        return math.nan
    if numerator <= 0.0 or denominator <= 0.0:
        return math.nan
    return 10.0 * math.log10(numerator / denominator)


def summarize_power(values):
    """Return finite, non-negative linear-power statistics."""
    array = np.asarray(values, dtype=np.float64)
    array = array[np.isfinite(array) & (array >= 0.0)]
    if array.size == 0:
        return {
            "valid_count": 0,
            "mean_power": math.nan,
            "median_power": math.nan,
            "rms": math.nan,
            "p95": math.nan,
            "max": math.nan,
        }
    mean_power = float(np.mean(array, dtype=np.float64))
    return {
        "valid_count": int(array.size),
        "mean_power": mean_power,
        "median_power": float(np.median(array)),
        "rms": math.sqrt(max(0.0, mean_power)),
        "p95": float(np.percentile(array, 95.0)),
        "max": float(np.max(array)),
    }


def estimate_target_power(target_values, background_mean_power):
    """Estimate integrated target power after subtracting a linear background."""
    array = np.asarray(target_values, dtype=np.float64)
    array = array[np.isfinite(array) & (array >= 0.0)]
    background_mean_power = float(background_mean_power)
    if array.size == 0 or not math.isfinite(background_mean_power):
        return math.nan
    return max(0.0, float(np.sum(array, dtype=np.float64)) -
               background_mean_power * int(array.size))
