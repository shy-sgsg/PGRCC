#!/usr/bin/env python3
"""Evaluate paired causal target detection from production on/off taps.

The three inputs are A=S+C+N (target on), B=C+N (target off), and optional
C=S-only.  Scenario identity is checked after removing only output paths and
target enable flags.  Continuous ROI deltas are always reported first.  A
causal hit requires an on-run ROI detection and rejects a target-off ROI that
is already equal to or stronger than the on-run ROI.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


EPS = 1.0e-12


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def find_manifest(run_dir: Path) -> Path:
    manifests = sorted((run_dir / "stage2/algorithm_result/period_0000/csi_metrics").glob(
        "*/csi_roi_manifest.csv"))
    if len(manifests) != 1:
        raise RuntimeError(f"expected one CSI manifest under {run_dir}, found {manifests}")
    return manifests[0]


def load_manifest_row(run_dir: Path) -> tuple[Path, dict[str, str]]:
    manifest = find_manifest(run_dir)
    rows = read_csv(manifest)
    if len(rows) != 1:
        raise RuntimeError(f"expected one manifest row: {manifest}")
    return manifest, rows[0]


def load_array(manifest: Path, key: str) -> np.ndarray:
    path = Path(key)
    if not path.is_absolute():
        path = (manifest.parent / path).resolve()
    if not path.is_file():
        raise RuntimeError(f"missing {key}: {path}")
    return np.asarray(np.load(path), dtype=np.float64)


def truth_rows(run_dir: Path, beam_id: int) -> list[dict[str, str]]:
    path = run_dir / "stage2/truth/truth_targets_by_beam.csv"
    return [row for row in read_csv(path)
            if str(row.get("visible", "1")).lower() not in {"0", "false", "no"}
            and int(float(row.get("beam_id", -1))) == beam_id]


def target_centers(run_dir: Path, manifest_path: Path,
                   manifest: dict[str, str], shape: tuple[int, int]) -> list[dict[str, Any]]:
    truth = truth_rows(run_dir, int(manifest["beam_id"]))
    axis_path = Path(manifest["fa_axis_path"])
    if not axis_path.is_absolute():
        axis_path = manifest_path.parent / axis_path
    axis = np.asarray(np.load(axis_path), dtype=np.float64).reshape(-1)
    if axis.size < 2:
        raise RuntimeError("Doppler axis is too short")
    step = float(np.median(np.diff(axis)))
    prf = step * axis.size
    center = 0.5 * (float(axis[0]) + float(axis[-1]))
    output = []
    for row in truth:
        af = float(row.get("af_total_truth_hz", "nan"))
        col = int(float(row.get("expected_bin", row.get("range_bin", -1))))
        if not math.isfinite(af) or not (0 <= col < shape[1]):
            continue
        wrapped = af + round((center - af) / prf) * prf
        doppler_row = int(np.argmin(np.abs(axis - wrapped)))
        if 0 <= doppler_row < shape[0]:
            output.append({"target_id": row.get("target_id", ""),
                           "row": doppler_row, "col": col,
                           "truth_af_hz": af})
    if not output:
        raise RuntimeError(f"no visible target center could be mapped from {run_dir}")
    return output


def normalize_scenario(value: Any) -> Any:
    if isinstance(value, dict):
        ignored = {"output_dir", "paired_background_output_dir", "case_id", "truth_output"}
        return {key: normalize_scenario(item) for key, item in value.items()
                if key not in ignored}
    if isinstance(value, list):
        return [normalize_scenario(item) for item in value]
    return value


def scenario_identity(run_dir: Path) -> Any:
    candidates = [run_dir / "scenario.json", run_dir / "scenario_resolved.json"]
    for path in candidates:
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if "targets" in value:
                for target in value["targets"]:
                    target["enabled"] = False
            if "scene" in value and isinstance(value["scene"], dict):
                value["scene"]["signal_only"] = False
            return normalize_scenario(value)
    raise RuntimeError(f"missing scenario identity under {run_dir}")


def roi_stats(power: np.ndarray, center: dict[str, Any], half_rows: int,
              half_cols: int) -> dict[str, Any]:
    row, col = int(center["row"]), int(center["col"])
    r0, r1 = max(0, row - half_rows), min(power.shape[0], row + half_rows + 1)
    c0, c1 = max(0, col - half_cols), min(power.shape[1], col + half_cols + 1)
    roi = power[r0:r1, c0:c1]
    finite = roi[np.isfinite(roi) & (roi >= 0.0)]
    if finite.size == 0:
        return {"peak_power": math.nan, "mean_power": math.nan, "roi_cells": 0,
                "row_start": r0, "row_end": r1 - 1, "col_start": c0,
                "col_end": c1 - 1}
    return {"peak_power": float(np.max(finite)), "mean_power": float(np.mean(finite)),
            "roi_cells": int(finite.size), "row_start": r0, "row_end": r1 - 1,
            "col_start": c0, "col_end": c1 - 1}


def detections_in_roi(run_dir: Path, center: dict[str, Any], half_rows: int,
                      half_cols: int) -> list[dict[str, str]]:
    path = run_dir / "stage2/algorithm_result/period_0000/detection_results_GMTI01.csv"
    rows = read_csv(path)
    output = []
    for row in rows:
        try:
            detected_row = int(float(row.get("row", "nan")))
            detected_col = int(float(row.get("col", row.get("range_bin", "nan"))))
        except ValueError:
            continue
        if abs(detected_row - center["row"]) <= half_rows and abs(detected_col - center["col"]) <= half_cols:
            output.append(row)
    return output


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    on_dir = args.target_on.resolve()
    off_dir = args.target_off.resolve()
    if scenario_identity(on_dir) != scenario_identity(off_dir):
        raise RuntimeError("target-on and target-off scenarios differ beyond target presence")
    on_manifest_path, on_manifest = load_manifest_row(on_dir)
    off_manifest_path, off_manifest = load_manifest_row(off_dir)
    on_after = load_array(on_manifest_path, on_manifest["after_power_path"])
    off_after = load_array(off_manifest_path, off_manifest["after_power_path"])
    on_before = load_array(on_manifest_path, on_manifest["before_power_path"])
    off_before = load_array(off_manifest_path, off_manifest["before_power_path"])
    if on_after.shape != off_after.shape or on_before.shape != on_after.shape or off_before.shape != on_after.shape:
        raise RuntimeError("paired on/off power-map shapes differ")
    centers = target_centers(on_dir, on_manifest_path, on_manifest, on_after.shape)
    half_rows = int(on_manifest.get("target_half_doppler_bins", 2))
    half_cols = int(on_manifest.get("target_half_range_bins", 2))
    target_only_after = target_only_before = None
    if args.target_only:
        target_manifest_path, target_manifest = load_manifest_row(args.target_only.resolve())
        target_only_after = load_array(target_manifest_path, target_manifest["after_power_path"])
        target_only_before = load_array(target_manifest_path, target_manifest["before_power_path"])
    records: list[dict[str, Any]] = []
    for center in centers:
        on_a = roi_stats(on_after, center, half_rows, half_cols)
        off_a = roi_stats(off_after, center, half_rows, half_cols)
        on_b = roi_stats(on_before, center, half_rows, half_cols)
        off_b = roi_stats(off_before, center, half_rows, half_cols)
        on_detections = detections_in_roi(on_dir, center, half_rows, half_cols)
        off_detections = detections_in_roi(off_dir, center, half_rows, half_cols)
        target_stats = None
        if target_only_after is not None and target_only_before is not None:
            target_stats = {
                "after": roi_stats(target_only_after, center, half_rows, half_cols),
                "before": roi_stats(target_only_before, center, half_rows, half_cols),
            }
        delta_after = on_a["peak_power"] - off_a["peak_power"]
        delta_before = on_b["peak_power"] - off_b["peak_power"]
        off_not_stronger = (math.isfinite(off_a["peak_power"]) and
                            math.isfinite(on_a["peak_power"]) and
                            off_a["peak_power"] < on_a["peak_power"])
        legacy_hit = bool(on_detections)
        causal_hit = legacy_hit and off_not_stronger
        record = {
            "target_id": center["target_id"],
            "target_row": center["row"], "target_col": center["col"],
            "on_peak_power": on_a["peak_power"], "off_peak_power": off_a["peak_power"],
            "delta_peak_power_after": delta_after,
            "on_before_peak_power": on_b["peak_power"], "off_before_peak_power": off_b["peak_power"],
            "delta_peak_power_before": delta_before,
            "delta_peak_power_after_db": (10.0 * math.log10(max(on_a["peak_power"], EPS) /
                                                          max(off_a["peak_power"], EPS))
                                          if off_a["peak_power"] >= 0.0 else math.nan),
            "target_increment_after_status": "continuous_delta_not_linear_S_only_claim",
            "target_increment_before_status": "continuous_delta_not_linear_S_only_claim",
            "legacy_hit": legacy_hit,
            "causal_hit": causal_hit,
            "on_detection_count_in_roi": len(on_detections),
            "off_detection_count_in_roi": len(off_detections),
            "off_roi_not_equal_or_stronger": off_not_stronger,
            "on_roi": on_a, "off_roi": off_a,
            "target_only_roi": target_stats,
        }
        records.append(record)
    result = {
        "schema_version": 1,
        "paired_design": "A=S+C+N, B=C+N, optional C=S-only",
        "truth_used_only_for_evaluation": True,
        "target_on": str(on_dir), "target_off": str(off_dir),
        "target_only": str(args.target_only.resolve()) if args.target_only else None,
        "target_count": len(records),
        "legacy_hit_count": sum(bool(item["legacy_hit"]) for item in records),
        "causal_hit_count": sum(bool(item["causal_hit"]) for item in records),
        "records": records,
        "definitions": {
            "causal_hit": "target-on ROI has a production detection and target-off ROI peak is strictly weaker",
            "target_increment": "continuous on-minus-off ROI power delta; not interpreted as linear S-only power",
        },
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-on", type=Path, required=True)
    parser.add_argument("--target-off", type=Path, required=True)
    parser.add_argument("--target-only", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "causal_detection_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    fields = ["target_id", "target_row", "target_col", "on_peak_power", "off_peak_power",
              "delta_peak_power_after", "delta_peak_power_before", "delta_peak_power_after_db",
              "legacy_hit", "causal_hit", "on_detection_count_in_roi",
              "off_detection_count_in_roi", "off_roi_not_equal_or_stronger",
              "target_increment_after_status", "target_increment_before_status"]
    with (output / "causal_detection_by_target.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result["records"])
    print(json.dumps({"output_dir": str(output), "target_count": result["target_count"],
                      "legacy_hit_count": result["legacy_hit_count"],
                      "causal_hit_count": result["causal_hit_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
