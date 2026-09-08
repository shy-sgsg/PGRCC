#!/usr/bin/env python3
"""CSV/JSON helpers for GMTI P4 truth evaluation."""

import csv
import json
import math
import os


def read_json(path, default=None):
    if not path or not os.path.exists(path):
        return {} if default is None else default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_csv_rows(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv_rows(path, fieldnames, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def to_float(row, key, default=math.nan):
    try:
        value = row.get(key, "")
        if value == "" or value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(row, key, default=-1):
    try:
        value = row.get(key, "")
        if value == "" or value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def first_existing(*paths):
    for path in paths:
        if path and os.path.exists(path):
            return path
    return ""


def load_case_inputs(output_dir, truth_dir=""):
    truth_dir = truth_dir or os.path.join(os.path.dirname(output_dir), "truth")
    return {
        "detection_results": os.path.join(output_dir, "detection_results.csv"),
        "track_results": os.path.join(output_dir, "track_results.csv"),
        "truth": first_existing(
            os.path.join(truth_dir, "target_truth_beam_summary.csv"),
            os.path.join(truth_dir, "truth_targets_by_beam.csv"),
        ),
        "run_manifest": os.path.join(output_dir, "run_manifest.json"),
    }


def hypot2(dx, dy):
    if not math.isfinite(dx) or not math.isfinite(dy):
        return math.nan
    return math.hypot(dx, dy)
