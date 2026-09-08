#!/usr/bin/env python3
"""Summarize Stage3 plugged-in compare/CUDA-only runs and dmon telemetry."""

import argparse
import csv
import json
import os
import re
import statistics


def read_perf(run_dir):
    path = os.path.join(run_dir, "logs", "stage3_performance.csv")
    with open(path, "r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(f"expected one performance row in {path}, got {len(rows)}")
    row = rows[0]
    numeric = {}
    for key, value in row.items():
        try:
            numeric[key] = float(value)
        except (TypeError, ValueError):
            numeric[key] = value
    return numeric


def read_wall(run_dir, fallback=""):
    path = os.path.join(run_dir, "wall_time.txt")
    if not os.path.exists(path) and fallback:
        path = fallback
    text = open(path, "r", encoding="utf-8").read()
    match = re.search(r"elapsed_s=([0-9.]+)", text)
    if not match:
        raise ValueError(f"elapsed_s missing in {path}")
    return float(match.group(1)), path


def median(rows, key):
    return statistics.median(float(row[key]) for row in rows)


def telemetry_summary(path):
    samples = []
    if not path:
        return {"sample_count": 0}
    with open(path, "r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            fields = line.split()
            if len(fields) < 14:
                continue
            try:
                samples.append(
                    {
                        "date": fields[0],
                        "time": fields[1],
                        "power_w": float(fields[3]),
                        "temperature_c": float(fields[4]),
                        "sm_percent": float(fields[6]),
                        "memory_percent": float(fields[7]),
                        "memory_clock_mhz": float(fields[12]),
                        "graphics_clock_mhz": float(fields[13]),
                    }
                )
            except ValueError:
                continue
    if not samples:
        return {"sample_count": 0}
    return {
        "sample_count": len(samples),
        "max_power_w": max(row["power_w"] for row in samples),
        "max_temperature_c": max(row["temperature_c"] for row in samples),
        "max_sm_percent": max(row["sm_percent"] for row in samples),
        "max_memory_clock_mhz": max(row["memory_clock_mhz"] for row in samples),
        "max_graphics_clock_mhz": max(row["graphics_clock_mhz"] for row in samples),
        "active_samples": [
            row for row in samples
            if row["sm_percent"] >= 90.0 or row["power_w"] >= 15.0
        ],
    }


def summarize(run_dirs, wall_fallbacks=None):
    wall_fallbacks = wall_fallbacks or [""] * len(run_dirs)
    rows = [read_perf(path) for path in run_dirs]
    walls = [read_wall(path, fallback)[0] for path, fallback in zip(run_dirs, wall_fallbacks)]
    return rows, walls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compare-dir", action="append", required=True)
    parser.add_argument("--compare-wall-fallback", action="append", default=[])
    parser.add_argument("--cuda-only-dir", action="append", required=True)
    parser.add_argument("--legacy-cuda-only-dir", action="append", default=[])
    parser.add_argument("--telemetry")
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    fallbacks = list(args.compare_wall_fallback)
    while len(fallbacks) < len(args.compare_dir):
        fallbacks.append("")
    compare, compare_walls = summarize(args.compare_dir, fallbacks)
    cuda, cuda_walls = summarize(args.cuda_only_dir)
    legacy, legacy_walls = (summarize(args.legacy_cuda_only_dir)
                            if args.legacy_cuda_only_dir else ([], []))

    result = {
        "compare": {
            "run_dirs": args.compare_dir,
            "runs": compare,
            "wall_s": compare_walls,
            "median_cpu_geometry_ms": median(compare, "cpu_geometry_ms"),
            "median_cuda_total_ms": median(compare, "cuda_total_ms"),
            "median_geometry_speedup": median(compare, "geometry_speedup"),
            "median_core_speedup": median(compare, "total_speedup"),
            "median_wall_s": statistics.median(compare_walls),
            "max_abs_error": max(float(row["max_abs_error"]) for row in compare),
            "max_relative_l2_error": max(float(row["relative_l2_error"]) for row in compare),
            "max_rmse": max(float(row["rmse"]) for row in compare),
            "max_phase_error_rad": max(float(row["max_phase_error_rad"]) for row in compare),
            "all_passed": all(int(row["comparison_pass"]) == 1 for row in compare),
        },
        "plugged_cuda_only": {
            "run_dirs": args.cuda_only_dir,
            "runs": cuda,
            "wall_s": cuda_walls,
            "median_cuda_total_ms": median(cuda, "cuda_total_ms"),
            "median_kernel_ms": median(cuda, "cuda_kernel_ms"),
            "median_lfm_ms": median(cuda, "lfm_ms"),
            "median_pack_write_ms": median(cuda, "pack_write_ms"),
            "median_wall_s": statistics.median(cuda_walls),
        },
        "legacy_unplugged_cuda_only": None,
        "telemetry": telemetry_summary(args.telemetry),
    }
    if legacy:
        result["legacy_unplugged_cuda_only"] = {
            "run_dirs": args.legacy_cuda_only_dir,
            "runs": legacy,
            "wall_s": legacy_walls,
            "median_cuda_total_ms": median(legacy, "cuda_total_ms"),
            "median_kernel_ms": median(legacy, "cuda_kernel_ms"),
            "median_wall_s": statistics.median(legacy_walls),
            "plugged_vs_legacy_cuda_total_speedup": (
                median(legacy, "cuda_total_ms") / median(cuda, "cuda_total_ms")
            ),
            "plugged_vs_legacy_wall_speedup": (
                statistics.median(legacy_walls) / statistics.median(cuda_walls)
            ),
        }

    os.makedirs(os.path.dirname(args.output_json) or ".", exist_ok=True)
    with open(args.output_json, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
