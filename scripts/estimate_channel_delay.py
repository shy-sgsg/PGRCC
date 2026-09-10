#!/usr/bin/env python3
"""Estimate an observable M1 channel delay from production CSI F1/F2.

The estimator never reads an injected delay.  It forms
``C12 = X1 * conj(X2)``, unwraps phase along the measured range-frequency
axis, and fits ``phase = b + 2*pi*f*delta_tau``.  D1 is ordinary LS, D2 uses
power/coherence weights, and D3 adds an iterative Huber reweighting.  A truth
delay may be supplied only for post-hoc evaluation.
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
ROOT = Path(__file__).resolve().parents[1]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def select_row(manifest: Path, period_id: int | None, beam_id: int | None) -> dict[str, str]:
    rows = read_rows(manifest)
    selected = [row for row in rows
                if (period_id is None or int(row["period_id"]) == period_id)
                and (beam_id is None or int(row["beam_id"]) == beam_id)]
    if len(selected) != 1:
        raise SystemExit(
            f"manifest selection is not unique: period={period_id}, beam={beam_id}, "
            f"matches={len(selected)}")
    return selected[0]


def resolve_array(manifest: Path, value: str) -> np.ndarray:
    path = Path(value)
    if not path.is_absolute():
        path = (manifest.parent / path).resolve()
    if not path.is_file():
        raise SystemExit(f"missing production tap array: {path}")
    return np.asarray(np.load(path))


def fit_weighted_line(x: np.ndarray, y: np.ndarray, weights: np.ndarray,
                      robust: bool = False) -> dict[str, float | int | None]:
    good = np.isfinite(x) & np.isfinite(y) & np.isfinite(weights) & (weights > 0.0)
    x = np.asarray(x[good], dtype=np.float64)
    y = np.asarray(y[good], dtype=np.float64)
    weights = np.asarray(weights[good], dtype=np.float64)
    if x.size < 3 or float(np.ptp(x)) <= EPS:
        return {"n": int(x.size), "slope_rad_per_hz": None,
                "intercept_rad": None, "r2": None, "rmse_rad": None,
                "confidence": 0.0}
    weights = weights / max(float(np.mean(weights)), EPS)
    robust_weights = np.ones_like(weights)
    slope = 0.0
    intercept = float(np.average(y, weights=weights))
    for _ in range(12 if robust else 1):
        effective = weights * robust_weights
        x_bar = float(np.average(x, weights=effective))
        y_bar = float(np.average(y, weights=effective))
        dx = x - x_bar
        denominator = float(np.sum(effective * dx * dx))
        if denominator <= EPS:
            break
        slope = float(np.sum(effective * dx * (y - y_bar)) / denominator)
        intercept = y_bar - slope * x_bar
        residual = y - (slope * x + intercept)
        if not robust:
            break
        scale = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
        scale = max(scale, 1.0e-6)
        standardized = np.abs(residual) / scale
        robust_weights = np.where(standardized <= 1.345, 1.0,
                                  1.345 / np.maximum(standardized, EPS))
    residual = y - (slope * x + intercept)
    effective = weights * robust_weights
    weighted_sse = float(np.sum(effective * residual * residual))
    y_bar = float(np.average(y, weights=effective))
    total = float(np.sum(effective * (y - y_bar) ** 2))
    r2 = 1.0 - weighted_sse / total if total > EPS else 1.0
    rmse = math.sqrt(weighted_sse / max(float(np.sum(effective)), EPS))
    confidence = float(np.clip(max(r2, 0.0), 0.0, 1.0) *
                      min(1.0, x.size / 24.0))
    return {"n": int(x.size), "slope_rad_per_hz": slope,
            "intercept_rad": intercept, "r2": float(r2),
            "rmse_rad": float(rmse), "confidence": confidence}


def _db(value: float) -> float:
    return 10.0 * math.log10(max(value, np.finfo(float).tiny))


def estimate_from_arrays(
    f1: np.ndarray,
    f2: np.ndarray,
    fa: np.ndarray,
    range_axis: np.ndarray,
    valid: np.ndarray,
    row_start: int,
    row_end: int,
    range_start: int,
    range_end: int,
    bandwidth_hz: float,
    chirp_duration_sec: float,
) -> dict[str, Any]:
    """Core estimator exposed for deterministic formula-level tests."""
    if f1.shape != f2.shape or f1.ndim != 2:
        raise ValueError("F1/F2 must be equal-shape 2-D arrays")
    rows, cols = f1.shape
    if fa.size != rows or range_axis.size != cols:
        raise ValueError("axis lengths do not match F1/F2")
    range_frequency = (range_axis - float(np.mean(range_axis))) * 2.0 * bandwidth_hz / chirp_duration_sec / 299792458.0
    cross = f1 * np.conj(f2)
    power1 = np.abs(f1) ** 2
    power2 = np.abs(f2) ** 2
    row_results: dict[str, list[dict[str, Any]]] = {"D1_ordinary_LS": [],
                                                    "D2_weighted_LS": [],
                                                    "D3_Huber_weighted_LS": []}
    for row in range(max(0, row_start), min(rows - 1, row_end) + 1):
        mask = np.asarray(valid[row, max(0, range_start):min(cols - 1, range_end) + 1], dtype=bool)
        start = max(0, range_start)
        end = min(cols - 1, range_end) + 1
        if not np.any(mask):
            continue
        phase_all = np.unwrap(np.angle(cross[row, start:end]))
        x = range_frequency[start:end][mask]
        y = phase_all[mask]
        magnitude_weights = np.sqrt(power1[row, start:end] * power2[row, start:end])
        for name, weights, robust in (
            ("D1_ordinary_LS", np.ones_like(magnitude_weights), False),
            ("D2_weighted_LS", magnitude_weights, False),
            ("D3_Huber_weighted_LS", magnitude_weights, True),
        ):
            fit = fit_weighted_line(x, y, weights[mask], robust=robust)
            fit.update({"row": row, "doppler_hz": float(fa[row]),
                        "delta_tau_ns": (float(fit["slope_rad_per_hz"]) / (2.0 * math.pi) * 1.0e9
                                         if fit["slope_rad_per_hz"] is not None else None)})
            row_results[name].append(fit)

    aggregate: dict[str, dict[str, Any]] = {}
    for name, items in row_results.items():
        finite_items = [item for item in items
                        if item["delta_tau_ns"] is not None and math.isfinite(float(item["delta_tau_ns"]))]
        if not finite_items:
            aggregate[name] = {"delta_tau_ns": None, "phase_slope_rad_per_hz": None,
                               "r2": None, "rmse_rad": None, "confidence": 0.0,
                               "row_count": 0}
            continue
        weights = np.asarray([max(float(item["confidence"]), EPS) for item in finite_items])
        delays = np.asarray([float(item["delta_tau_ns"]) for item in finite_items])
        aggregate[name] = {
            "delta_tau_ns": float(np.average(delays, weights=weights)),
            "delta_tau_ns_std": float(np.std(delays)),
            "phase_slope_rad_per_hz": float(np.average(
                [float(item["slope_rad_per_hz"]) for item in finite_items], weights=weights)),
            "r2": float(np.average([float(item["r2"]) for item in finite_items], weights=weights)),
            "rmse_rad": float(np.average([float(item["rmse_rad"]) for item in finite_items], weights=weights)),
            "confidence": float(np.average([float(item["confidence"]) for item in finite_items], weights=weights)),
            "row_count": len(finite_items),
        }
    return {"frequency_axis_hz": range_frequency, "rows": row_results,
            "aggregate": aggregate, "cross_spectrum_definition": "X1*conj(X2)"}


def estimate_from_raw_time_arrays(
    channel1_time: np.ndarray,
    channel2_time: np.ndarray,
    fs_hz: float,
    support_percentile: float = 80.0,
) -> dict[str, Any]:
    """Estimate delay from observable raw channel samples before range FFT.

    This is the direct ``C12(f) = X1(f) * conj(X2(f))`` estimator requested by
    the M1 challenge.  It aggregates the cross spectrum over pulses, while
    the injected delay is never consulted.  The pulse aggregation only
    improves the observable SNR; it does not provide any truth-derived mask.
    """
    x1 = np.asarray(channel1_time)
    x2 = np.asarray(channel2_time)
    if x1.shape != x2.shape or x1.ndim != 2:
        raise ValueError("raw channel arrays must be equal-shape 2-D [pulse, sample]")
    if fs_hz <= 0.0:
        raise ValueError("fs_hz must be positive")
    spectrum1 = np.fft.fft(x1, axis=1)
    spectrum2 = np.fft.fft(x2, axis=1)
    cross = np.sum(spectrum1 * np.conj(spectrum2), axis=0)
    frequency = np.fft.fftfreq(x1.shape[1], d=1.0 / fs_hz)
    positive = frequency > 0.0
    magnitude = np.abs(cross)
    if not np.any(positive):
        raise ValueError("raw FFT has no positive-frequency support")
    threshold = float(np.percentile(magnitude[positive], support_percentile))
    mask = positive & np.isfinite(magnitude) & (magnitude >= max(threshold, EPS))
    if int(np.sum(mask)) < 3:
        raise ValueError("raw cross-spectrum has insufficient positive-frequency support")
    order = np.argsort(frequency[mask])
    x = frequency[mask][order]
    y = np.unwrap(np.angle(cross[mask][order]))
    magnitude = magnitude[mask][order]
    fits: dict[str, dict[str, Any]] = {}
    for name, weights, robust in (
        ("D1_ordinary_LS", np.ones_like(magnitude), False),
        ("D2_weighted_LS", magnitude, False),
        ("D3_Huber_weighted_LS", magnitude, True),
    ):
        fit = fit_weighted_line(x, y, weights, robust=robust)
        fit.update({
            "delta_tau_ns": (float(fit["slope_rad_per_hz"]) / (2.0 * math.pi) * 1.0e9
                             if fit["slope_rad_per_hz"] is not None else None),
            "pulse_count": int(x1.shape[0]),
            "frequency_bin_count": int(x.size),
        })
        fits[name] = fit
    return {
        "aggregate": fits,
        "frequency_axis_hz": x,
        "cross_spectrum": cross[mask][order],
        "cross_spectrum_definition": "sum_pulses(FFT(x1)*conj(FFT(x2)))",
        "support_percentile": support_percentile,
    }


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


def correct_fractional_delay_frequency_domain(
    f2: np.ndarray, frequency_axis_hz: np.ndarray,
    delta_tau_sec: float, intercept_by_row: np.ndarray | None = None,
) -> np.ndarray:
    """Apply an independent frequency-domain phase ramp to channel 2."""
    if intercept_by_row is None:
        intercept_by_row = np.zeros(f2.shape[0], dtype=np.float64)
    phase = intercept_by_row[:, None] + 2.0 * math.pi * delta_tau_sec * frequency_axis_hz[None, :]
    return f2 * np.exp(1j * phase)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=None,
                        help="production CSI tap manifest (post-Doppler diagnostic mode)")
    parser.add_argument("--raw-bin", type=Path, default=None,
                        help="production raw float32 BIN (direct fast-time mode)")
    parser.add_argument("--pulse-len", type=int, default=11840)
    parser.add_argument("--channel-count", type=int, default=4)
    parser.add_argument("--channel-1", type=int, default=1)
    parser.add_argument("--channel-2", type=int, default=2)
    parser.add_argument("--fs-hz", type=float, default=60.0e6)
    parser.add_argument("--raw-support-percentile", type=float, default=80.0)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--period-id", type=int, default=None)
    parser.add_argument("--beam-id", type=int, default=None)
    parser.add_argument("--bandwidth-mhz", type=float, default=50.0)
    parser.add_argument("--chirp-duration-us", type=float, default=130.0)
    parser.add_argument("--min-coherence", type=float, default=0.5)
    parser.add_argument("--truth-delay-ns", type=float, default=None,
                        help="evaluation-only truth; never used in the fit")
    args = parser.parse_args()
    if (args.manifest is None) == (args.raw_bin is None):
        raise SystemExit("provide exactly one of --manifest or --raw-bin")
    if args.raw_bin is not None:
        channel1, channel2 = load_raw_channels(
            args.raw_bin.resolve(), args.pulse_len, args.channel_count,
            args.channel_1, args.channel_2)
        result = estimate_from_raw_time_arrays(
            channel1, channel2, args.fs_hz, args.raw_support_percentile)
        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        summary: dict[str, Any] = {
            "schema_version": 1,
            "estimator_input": "measured production raw channel-1/channel-2 samples only",
            "input_mode": "raw_fast_time_cross_spectrum",
            "truth_used_in_estimator": False,
            "raw_bin": str(args.raw_bin.resolve()),
            "fit_model": "angle(sum_pulses(FFT(x1)*conj(FFT(x2)))) = b + 2*pi*f*delta_tau + epsilon",
            "raw_layout": {"pulse_len": args.pulse_len, "channel_count": args.channel_count,
                           "channel_1": args.channel_1, "channel_2": args.channel_2,
                           "fs_hz": args.fs_hz, "pulse_count": int(channel1.shape[0])},
            "support_percentile": args.raw_support_percentile,
            "methods": result["aggregate"],
        }
        if args.truth_delay_ns is not None and math.isfinite(args.truth_delay_ns):
            summary["truth_evaluation_only"] = {
                "truth_delay_ns": args.truth_delay_ns,
                "errors_ns": {
                    name: (float(values["delta_tau_ns"]) - args.truth_delay_ns
                           if values["delta_tau_ns"] is not None else None)
                    for name, values in result["aggregate"].items()
                },
            }
        (output_dir / "channel_delay_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8")
        print(json.dumps({"output_dir": str(output_dir), "input_mode": summary["input_mode"],
                          "methods": result["aggregate"], "truth_used_in_estimator": False},
                         ensure_ascii=False))
        return 0

    manifest = args.manifest.resolve()
    row = select_row(manifest, args.period_id, args.beam_id)
    f1 = resolve_array(manifest, row["channel1_real_path"]) + 1j * resolve_array(manifest, row["channel1_imag_path"])
    f2 = resolve_array(manifest, row["channel2_real_path"]) + 1j * resolve_array(manifest, row["channel2_imag_path"])
    fa = resolve_array(manifest, row["fa_axis_path"]).reshape(-1).astype(np.float64)
    range_axis = resolve_array(manifest, row["range_axis_path"]).reshape(-1).astype(np.float64)
    if f1.shape != f2.shape or f1.shape != (fa.size, range_axis.size):
        raise SystemExit(f"shape mismatch: F1={f1.shape}, F2={f2.shape}, fa={fa.shape}, range={range_axis.shape}")
    rows, cols = f1.shape
    az_st, az_ed = int(row["az_st"]), int(row["az_ed"])
    rg_st, rg_ed = int(row["rg_st"]), int(row["rg_ed"])
    active = np.zeros((rows, cols), dtype=bool)
    active[max(0, az_st):min(rows - 1, az_ed) + 1,
           max(0, rg_st):min(cols - 1, rg_ed) + 1] = True
    pmin = np.minimum(np.abs(f1) ** 2, np.abs(f2) ** 2)
    finite = active & np.isfinite(f1.real) & np.isfinite(f2.real) & np.isfinite(pmin)
    values = pmin[finite]
    if values.size == 0:
        raise SystemExit("active support has no finite cells")
    floor = max(float(np.percentile(values, 5.0)), EPS)
    ceiling = float(np.percentile(values, 99.0))
    valid = finite & (pmin >= floor) & (pmin <= ceiling)
    cross = f1 * np.conj(f2)
    for index in range(rows):
        mask = valid[index]
        denominator = math.sqrt(float(np.sum(np.abs(f1[index, mask]) ** 2) *
                                      np.sum(np.abs(f2[index, mask]) ** 2))) if np.any(mask) else 0.0
        rho = abs(np.sum(cross[index, mask])) / denominator if denominator > 0.0 else 0.0
        if az_st <= index <= az_ed and rho < args.min_coherence:
            valid[index] = False
    result = estimate_from_arrays(
        f1, f2, fa, range_axis, valid, az_st, az_ed, rg_st, rg_ed,
        args.bandwidth_mhz * 1.0e6, args.chirp_duration_us * 1.0e-6)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "channel_delay_by_row.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["method", "row", "doppler_hz", "delta_tau_ns", "slope_rad_per_hz",
                  "intercept_rad", "r2", "rmse_rad", "confidence", "n"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for method, items in result["rows"].items():
            for item in items:
                writer.writerow({"method": method, **item})
    summary: dict[str, Any] = {
        "schema_version": 1,
        "estimator_input": "measured production F1/F2 only",
        "truth_used_in_estimator": False,
        "manifest": str(manifest),
        "case": {"period_id": int(row["period_id"]), "beam_id": int(row["beam_id"]),
                 "shape": [rows, cols], "valid_cell_count": int(np.sum(valid))},
        "fit_model": "angle(X1*conj(X2)) = b + 2*pi*f*delta_tau + epsilon",
        "methods": result["aggregate"],
        "artifacts": {"channel_delay_by_row": str(output_dir / "channel_delay_by_row.csv")},
    }
    if args.truth_delay_ns is not None and math.isfinite(args.truth_delay_ns):
        summary["truth_evaluation_only"] = {
            "truth_delay_ns": args.truth_delay_ns,
            "errors_ns": {
                name: (float(values["delta_tau_ns"]) - args.truth_delay_ns
                       if values["delta_tau_ns"] is not None else None)
                for name, values in result["aggregate"].items()
            },
        }
    (output_dir / "channel_delay_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "methods": result["aggregate"],
                      "truth_used_in_estimator": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
