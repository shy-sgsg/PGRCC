#!/usr/bin/env python3
"""Estimate observable M2 slow-time differential phase from measured F1/F2.

The production CSI tap is transformed back to slow time only as an explicit
diagnostic because it does not export the pre-Doppler pulse matrix.  For each
pulse the estimator forms ``c[p] = sum_r F1[p,r] * conj(F2[p,r])``, unwraps its
phase, and compares robust constant+linear, robust quadratic, and a short
smooth (Kalman-like) trajectory.  Truth is evaluation-only.
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


def _db(value: float) -> float:
    """Return a finite power ratio in dB for the estimator summary."""
    return 10.0 * math.log10(max(float(value), np.finfo(float).tiny))


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def select_row(manifest: Path, period_id: int | None, beam_id: int | None) -> dict[str, str]:
    selected = [row for row in read_rows(manifest)
                if (period_id is None or int(row["period_id"]) == period_id)
                and (beam_id is None or int(row["beam_id"]) == beam_id)]
    if len(selected) != 1:
        raise SystemExit(f"manifest selection is not unique: matches={len(selected)}")
    return selected[0]


def load_array(manifest: Path, value: str) -> np.ndarray:
    path = Path(value)
    if not path.is_absolute():
        path = (manifest.parent / path).resolve()
    if not path.is_file():
        raise SystemExit(f"missing production tap array: {path}")
    return np.asarray(np.load(path))


def robust_polyfit(x: np.ndarray, y: np.ndarray, weights: np.ndarray,
                   degree: int) -> dict[str, Any]:
    good = np.isfinite(x) & np.isfinite(y) & np.isfinite(weights) & (weights > 0.0)
    x = np.asarray(x[good], dtype=np.float64)
    y = np.asarray(y[good], dtype=np.float64)
    weights = np.asarray(weights[good], dtype=np.float64)
    if x.size < degree + 2:
        return {"coefficients": None, "rmse_rad": None, "r2": None, "confidence": 0.0,
                "n": int(x.size)}
    x0 = float(np.mean(x))
    scale = max(float(np.std(x)), 1.0)
    z = (x - x0) / scale
    design = np.column_stack([z ** power for power in range(degree + 1)])
    robust_weights = np.ones_like(weights)
    coefficients = np.zeros(degree + 1, dtype=np.float64)
    for _ in range(12):
        effective = weights * robust_weights
        normal = design.T @ (effective[:, None] * design)
        rhs = design.T @ (effective * y)
        try:
            coefficients = np.linalg.solve(normal, rhs)
        except np.linalg.LinAlgError:
            coefficients = np.linalg.lstsq(normal, rhs, rcond=None)[0]
        residual = y - design @ coefficients
        scale_residual = max(1.4826 * float(np.median(np.abs(residual - np.median(residual)))), 1.0e-6)
        standardized = np.abs(residual) / scale_residual
        robust_weights = np.where(standardized <= 1.345, 1.0,
                                  1.345 / np.maximum(standardized, EPS))
    residual = y - design @ coefficients
    effective = weights * robust_weights
    sse = float(np.sum(effective * residual ** 2))
    mean = float(np.average(y, weights=effective))
    total = float(np.sum(effective * (y - mean) ** 2))
    r2 = 1.0 - sse / total if total > EPS else 1.0
    # Convert the normalized polynomial back to a callable coefficient set.
    trajectory = design @ coefficients
    confidence = float(np.clip(max(r2, 0.0), 0.0, 1.0) * min(1.0, x.size / 16.0))
    return {"coefficients": coefficients, "x_center": x0, "x_scale": scale,
            "rmse_rad": math.sqrt(sse / max(float(np.sum(effective)), EPS)),
            "r2": float(r2), "confidence": confidence, "n": int(x.size),
            "fitted": trajectory, "fit_x": x}


def moving_average(values: np.ndarray, window: int = 5, passes: int = 2) -> np.ndarray:
    result = np.asarray(values, dtype=np.float64).copy()
    kernel = np.ones(window, dtype=np.float64) / float(window)
    half = window // 2
    for _ in range(passes):
        padded = np.pad(result, (half, half), mode="edge")
        result = np.convolve(padded, kernel, mode="valid")
    return result


def estimate_from_slow_time(
    slow_f1: np.ndarray,
    slow_f2: np.ndarray,
    valid_range: np.ndarray,
    prf_hz: float,
) -> dict[str, Any]:
    if slow_f1.shape != slow_f2.shape or slow_f1.ndim != 2:
        raise ValueError("slow-time F1/F2 must be equal-shape 2-D arrays")
    if valid_range.size != slow_f1.shape[1]:
        raise ValueError("range mask length mismatch")
    cross = slow_f1[:, valid_range] * np.conj(slow_f2[:, valid_range])
    c = np.sum(cross, axis=1)
    observed = np.unwrap(np.angle(c))
    weights = np.abs(c)
    pulses = np.arange(slow_f1.shape[0], dtype=np.float64)
    p1 = robust_polyfit(pulses, observed, weights, 1)
    p2 = robust_polyfit(pulses, observed, weights, 2)
    p3_trajectory = moving_average(observed, window=5, passes=2)
    p3_fit = robust_polyfit(pulses, p3_trajectory, np.maximum(weights, EPS), 1)
    trajectories: dict[str, np.ndarray] = {
        "P1_robust_constant_linear": (
            np.column_stack([np.ones_like(pulses), (pulses - p1["x_center"]) / p1["x_scale"]]) @ p1["coefficients"]
            if p1["coefficients"] is not None else np.full_like(pulses, np.nan)),
        "P2_robust_quadratic": (
            np.column_stack([np.ones_like(pulses), (pulses - p2["x_center"]) / p2["x_scale"],
                             ((pulses - p2["x_center"]) / p2["x_scale"]) ** 2]) @ p2["coefficients"]
            if p2["coefficients"] is not None else np.full_like(pulses, np.nan)),
        "P3_smooth_spline_Kalman_like": p3_trajectory,
    }
    fits = {
        "P1_robust_constant_linear": p1,
        "P2_robust_quadratic": p2,
        "P3_smooth_spline_Kalman_like": p3_fit,
    }
    for name, trajectory in trajectories.items():
        finite = np.isfinite(trajectory) & np.isfinite(observed)
        diff = trajectory[finite] - observed[finite]
        fits[name]["trajectory_rmse_rad"] = float(np.sqrt(np.mean(diff ** 2))) if np.any(finite) else None
        slope = np.polyfit(pulses[finite], trajectory[finite], 1)[0] if np.count_nonzero(finite) >= 2 else math.nan
        fits[name]["slope_rad_per_pulse"] = float(slope)
        fits[name]["slope_deg_per_pulse"] = float(math.degrees(slope))
        fits[name].pop("fitted", None)
        fits[name].pop("fit_x", None)
        if isinstance(fits[name].get("coefficients"), np.ndarray):
            fits[name]["coefficients"] = fits[name]["coefficients"].tolist()
    return {"cross_spectrum": c, "observed_phase_rad": observed,
            "pulse_index": pulses, "trajectories": trajectories, "fits": fits}


def load_raw_channels(path: Path, pulse_len: int, channel_count: int,
                      channel_1: int, channel_2: int) -> tuple[np.ndarray, np.ndarray]:
    if pulse_len <= 0 or channel_count < max(channel_1, channel_2) or min(channel_1, channel_2) < 1:
        raise SystemExit("invalid raw pulse/channel layout")
    bytes_per_packet = 256 + pulse_len * channel_count * 2 * 4
    raw = np.fromfile(path, dtype=np.uint8)
    if raw.size == 0 or raw.size % bytes_per_packet != 0:
        raise SystemExit(f"raw file is not complete float32 packets: {path} bytes={raw.size}")
    payload = raw.reshape((-1, bytes_per_packet))[:, 256:]
    values = payload.view("<f4").reshape((-1, pulse_len, channel_count, 2))
    first = values[:, :, channel_1 - 1, 0].astype(np.float64) + 1j * values[:, :, channel_1 - 1, 1].astype(np.float64)
    second = values[:, :, channel_2 - 1, 0].astype(np.float64) + 1j * values[:, :, channel_2 - 1, 1].astype(np.float64)
    return first, second


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=None,
                        help="production CSI tap manifest (inverse-Doppler diagnostic mode)")
    parser.add_argument("--raw-bin", type=Path, default=None,
                        help="production raw float32 BIN (direct pulse-domain mode)")
    parser.add_argument("--pulse-len", type=int, default=11840)
    parser.add_argument("--channel-count", type=int, default=4)
    parser.add_argument("--channel-1", type=int, default=1)
    parser.add_argument("--channel-2", type=int, default=2)
    parser.add_argument("--fs-hz", type=float, default=60.0e6)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--period-id", type=int, default=None)
    parser.add_argument("--beam-id", type=int, default=None)
    parser.add_argument("--prf-hz", type=float, default=1300.0)
    parser.add_argument("--truth-slope-deg-per-pulse", type=float, default=None,
                        help="evaluation-only truth slope")
    args = parser.parse_args()
    if (args.manifest is None) == (args.raw_bin is None):
        raise SystemExit("provide exactly one of --manifest or --raw-bin")
    if args.raw_bin is not None:
        channel1, channel2 = load_raw_channels(
            args.raw_bin.resolve(), args.pulse_len, args.channel_count,
            args.channel_1, args.channel_2)
        spectra1 = np.fft.fft(channel1, axis=1)
        spectra2 = np.fft.fft(channel2, axis=1)
        frequency = np.fft.fftfreq(args.pulse_len, d=1.0 / args.fs_hz)
        valid_range = frequency > 0.0
        result = estimate_from_slow_time(spectra1, spectra2, valid_range, args.prf_hz)
        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        with (output_dir / "temporal_phase_trajectory.csv").open("w", newline="", encoding="utf-8") as handle:
            fields = ["pulse", "time_s", "observed_phase_rad"] + list(result["trajectories"])
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for index, pulse in enumerate(result["pulse_index"]):
                writer.writerow({"pulse": int(pulse), "time_s": float(pulse / args.prf_hz),
                                 "observed_phase_rad": float(result["observed_phase_rad"][index]),
                                 **{name: float(values[index]) for name, values in result["trajectories"].items()}})
        summary: dict[str, Any] = {
            "schema_version": 1,
            "estimator_input": "measured production raw channel-1/channel-2 samples only",
            "input_mode": "raw_fast_time_pulse_phase",
            "truth_used_in_estimator": False,
            "raw_bin": str(args.raw_bin.resolve()),
            "cross_spectrum_definition": "c[p]=sum_positive_f FFT(x1[p])*conj(FFT(x2[p]))",
            "raw_layout": {"pulse_len": args.pulse_len, "channel_count": args.channel_count,
                           "channel_1": args.channel_1, "channel_2": args.channel_2,
                           "fs_hz": args.fs_hz, "pulse_count": int(channel1.shape[0])},
            "methods": result["fits"],
            "artifacts": {"temporal_phase_trajectory": str(output_dir / "temporal_phase_trajectory.csv")},
        }
        if args.truth_slope_deg_per_pulse is not None and math.isfinite(args.truth_slope_deg_per_pulse):
            summary["truth_evaluation_only"] = {
                "truth_slope_deg_per_pulse": args.truth_slope_deg_per_pulse,
                "slope_errors_deg_per_pulse": {
                    name: float(values["slope_deg_per_pulse"] - args.truth_slope_deg_per_pulse)
                    for name, values in result["fits"].items()
                },
            }
        (output_dir / "temporal_phase_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8")
        print(json.dumps({"output_dir": str(output_dir), "input_mode": summary["input_mode"],
                          "methods": result["fits"], "truth_used_in_estimator": False},
                         ensure_ascii=False))
        return 0

    manifest = args.manifest.resolve()
    row = select_row(manifest, args.period_id, args.beam_id)
    f1 = load_array(manifest, row["channel1_real_path"]) + 1j * load_array(manifest, row["channel1_imag_path"])
    f2 = load_array(manifest, row["channel2_real_path"]) + 1j * load_array(manifest, row["channel2_imag_path"])
    before = load_array(manifest, row["before_power_path"]).astype(np.float64)
    after = load_array(manifest, row["after_power_path"]).astype(np.float64)
    if f1.shape != f2.shape or before.shape != f1.shape or after.shape != f1.shape:
        raise SystemExit("F1/F2/before/after shapes do not match")
    rows, cols = f1.shape
    az_st, az_ed = int(row["az_st"]), int(row["az_ed"])
    rg_st, rg_ed = int(row["rg_st"]), int(row["rg_ed"])
    active = np.zeros((rows, cols), dtype=bool)
    active[max(0, az_st):min(rows - 1, az_ed) + 1,
           max(0, rg_st):min(cols - 1, rg_ed) + 1] = True
    pmin = np.minimum(np.abs(f1) ** 2, np.abs(f2) ** 2)
    values = pmin[active & np.isfinite(pmin)]
    floor = max(float(np.percentile(values, 5.0)), EPS) if values.size else EPS
    ceiling = float(np.percentile(values, 99.0)) if values.size else float("inf")
    valid = active & np.isfinite(f1.real) & np.isfinite(f2.real) & (pmin >= floor) & (pmin <= ceiling)
    current_k = float(row.get("p38_k_rad_per_hz", 0.0) or 0.0)
    current_b = float(row.get("p38_b_rad", 0.0) or 0.0)
    fa = load_array(manifest, row["fa_axis_path"]).reshape(-1).astype(np.float64)
    f2_current = np.exp(1j * (current_k * fa + current_b))[:, None] * f2
    slow_f1 = np.fft.ifft(np.fft.ifftshift(f1, axes=0), axis=0)
    slow_f2 = np.fft.ifft(np.fft.ifftshift(f2_current, axes=0), axis=0)
    slow_valid_range = np.any(valid, axis=0)
    result = estimate_from_slow_time(slow_f1, slow_f2, slow_valid_range, args.prf_hz)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "temporal_phase_trajectory.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["pulse", "time_s", "observed_phase_rad"] + list(result["trajectories"])
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, pulse in enumerate(result["pulse_index"]):
            writer.writerow({"pulse": int(pulse), "time_s": float(pulse / args.prf_hz),
                             "observed_phase_rad": float(result["observed_phase_rad"][index]),
                             **{name: float(values[index]) for name, values in result["trajectories"].items()}})
    current_power = float(np.mean(after[valid])) if np.any(valid) else math.nan
    before_power = float(np.mean(before[valid])) if np.any(valid) else math.nan
    summary: dict[str, Any] = {
        "schema_version": 1,
        "estimator_input": "measured production F1/F2, inverse-Doppler diagnostic",
        "truth_used_in_estimator": False,
        "manifest": str(manifest),
        "cross_spectrum_definition": "c[p]=sum_r F1_slow[p,r]*conj(F2_current_slow[p,r])",
        "current_cancellation_db": _db(before_power / current_power) if current_power > 0.0 else None,
        "methods": result["fits"],
        "artifacts": {"temporal_phase_trajectory": str(output_dir / "temporal_phase_trajectory.csv")},
    }
    if args.truth_slope_deg_per_pulse is not None and math.isfinite(args.truth_slope_deg_per_pulse):
        summary["truth_evaluation_only"] = {
            "truth_slope_deg_per_pulse": args.truth_slope_deg_per_pulse,
            "slope_errors_deg_per_pulse": {
                name: float(values["slope_deg_per_pulse"] - args.truth_slope_deg_per_pulse)
                for name, values in result["fits"].items()
            },
        }
    (output_dir / "temporal_phase_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "methods": result["fits"],
                      "truth_used_in_estimator": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
