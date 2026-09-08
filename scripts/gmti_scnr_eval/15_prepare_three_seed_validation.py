#!/usr/bin/env python3
"""Merge the three small validation seeds and attach output-SCNR provenance.

The formal paired calibration currently covers the three beam-centre angles
and the 0.8-degree edge case.  The validation batch also contains 30/45-degree
edge cases.  Those rows keep the production ``scnr_det_out_db`` measurement as
the output-SCNR x coordinate and are explicitly marked unpaired; no input SNR
is substituted or fitted.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from pathlib import Path

from scnr_eval_lib import read_csv, write_csv


def num(value: object, default: float = float("nan")) -> float:
    try:
        text = str(value).strip()
        return float(text) if text else default
    except (TypeError, ValueError):
        return default


def case_info(name: str) -> tuple[float, str, float]:
    match = re.fullmatch(r"command_([pm])(\d+)p(\d+)deg_(center|right_edge)", name)
    if not match:
        raise ValueError(name)
    sign, integer, fraction, position = match.groups()
    command = float(f"{integer}.{fraction}") * (1 if sign == "p" else -1)
    physical = command + (0.8 if position == "right_edge" else 0.0)
    return command, position, physical


def calibration_map(root: Path) -> dict[tuple[float, float], dict[str, str]]:
    result: dict[tuple[float, float], dict[str, str]] = {}
    for path in root.glob("angle_*/go_paired_output_scnr_summary.csv"):
        for row in read_csv(path):
            angle = num(row.get("truth_angle_deg")); snr = num(row.get("configured_target_snr_db"))
            out = num(row.get("output_scnr_det_out_db_median"))
            if math.isfinite(angle) and math.isfinite(snr) and math.isfinite(out):
                result[(round(angle, 6), round(snr, 6))] = row
    return result


def add_scnr(row: dict[str, str], physical_angle: float, calibration: dict[tuple[float, float], dict[str, str]], fallback: float, fallback_bin_db: float = 2.0) -> None:
    snr = num(row.get("configured_target_snr_db"))
    cal = calibration.get((round(physical_angle, 6), round(snr, 6)))
    if cal is not None:
        value = num(cal.get("output_scnr_det_out_db_median"))
        row["scnr_out_db"] = f"{value:.12g}"
        row["scnr_out_p05_db"] = cal.get("output_scnr_det_out_db_p05", "")
        row["scnr_out_p95_db"] = cal.get("output_scnr_det_out_db_p95", "")
        row["scnr_calibration_samples"] = cal.get("sample_count", "")
        row["scnr_calibration_status"] = "paired_S-only_C+N"
        return
    if math.isfinite(fallback):
        # For unpaired edge cases, use a measured production output-SCNR
        # median per seed/case/input档 and bin it only to make the validation
        # denominator visible.  The bin centre is still output SCNR, never
        # the configured input SNR.
        binned = fallback_bin_db * round(fallback / fallback_bin_db)
        row["scnr_out_db"] = f"{binned:.12g}"
        row["scnr_out_p05_db"] = ""
        row["scnr_out_p95_db"] = ""
        row["scnr_calibration_samples"] = ""
        row["scnr_calibration_status"] = f"production_cfar_output_scnr_unpaired_binned_{fallback_bin_db:g}db"
    else:
        row["scnr_out_db"] = ""
        row["scnr_out_p05_db"] = ""
        row["scnr_out_p95_db"] = ""
        row["scnr_calibration_samples"] = ""
        row["scnr_calibration_status"] = "unavailable"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--calibration-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.validation_root.resolve(); calibration = calibration_map(args.calibration_root.resolve())
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=True)
    metrics: list[dict[str, str]] = []
    tracks: list[dict[str, str]] = []
    # Track fallback uses the median raw output SCNR of the same target across
    # its three periods.  It remains a measured production output quantity.
    raw_by_target: dict[tuple[str, str, str], list[float]] = {}
    metric_sources: list[str] = []
    track_sources: list[str] = []
    for seed_dir in sorted(root.glob("seed_*")):
        for case_dir in sorted(seed_dir.glob("command_*")):
            try:
                command, position, physical = case_info(case_dir.name)
            except ValueError:
                continue
            metric_path = case_dir / "metrics/target_diagnostics_all_periods.csv"
            track_path = case_dir / "metrics/track_sequence_audit.csv"
            if not metric_path.is_file() or not track_path.is_file():
                continue
            metric_sources.append(str(metric_path))
            local = read_csv(metric_path)
            fallback_by_snr: dict[str, list[float]] = {}
            for source in local:
                raw = num(source.get("scnr_det_out_db"))
                if math.isfinite(raw):
                    fallback_by_snr.setdefault(source.get("configured_target_snr_db", ""), []).append(raw)
            fallback_median = {snr: float(statistics.median(values)) for snr, values in fallback_by_snr.items()}
            for source in local:
                row = dict(source)
                row.update({"case": f"{command:g}", "label": position,
                            "case_dir": str(case_dir),
                            "truth_angle_case_deg": f"{physical:g}",
                            "validation_seed": seed_dir.name})
                raw = num(row.get("scnr_det_out_db"))
                key = (seed_dir.name, row.get("target_id", ""), row.get("configured_target_snr_db", ""))
                if math.isfinite(raw): raw_by_target.setdefault(key, []).append(raw)
                add_scnr(row, physical, calibration,
                         fallback_median.get(row.get("configured_target_snr_db", ""), float("nan")))
                metrics.append(row)
            track_sources.append(str(track_path))
            for source in read_csv(track_path):
                row = dict(source)
                row.update({"case": f"{command:g}", "label": position,
                            "case_dir": str(case_dir),
                            "truth_angle_case_deg": f"{physical:g}",
                            "validation_seed": seed_dir.name})
                key = (seed_dir.name, row.get("target_id", ""), row.get("configured_target_snr_db", ""))
                fallback_values = raw_by_target.get(key, [])
                add_scnr(row, physical, calibration,
                         statistics.median(fallback_values) if fallback_values else float("nan"))
                tracks.append(row)
    write_csv(out / "validation_3seed_metrics_with_scnr.csv", metrics)
    write_csv(out / "validation_3seed_track_with_scnr.csv", tracks)
    manifest = {
        "validation_root": str(root), "calibration_root": str(args.calibration_root.resolve()),
        "seed_count": len({row.get("validation_seed") for row in metrics}),
        "metric_rows": len(metrics), "track_rows": len(tracks),
        "calibration_rows": len(calibration), "metric_sources": metric_sources,
        "track_sources": track_sources,
        "output_scnr_policy": "paired calibration when physical angle/input档 exists; otherwise measured production scnr_det_out_db median binned to 2 dB, explicitly unpaired",
        "no_input_snr_as_axis": True,
    }
    (out / "validation_3seed_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(out), "metric_rows": len(metrics), "track_rows": len(tracks),
                      "seed_count": manifest["seed_count"], "calibration_rows": len(calibration)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
