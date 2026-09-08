#!/usr/bin/env python3
"""Independent CPU audit of a new-protocol p38 phase slope.

The script intentionally follows the production chain: read interleaved IQ,
range-compress with the XML parameter-derived analytic matched filter, estimate
the Doppler centre, azimuth FFT/fftshift, apply the production DBS circular
shift, form F1*conj(F2), and robustly fit inter-channel phase versus Doppler
frequency.  The output records both the raw FFT row and the DBS-shifted row so
a near-zero Doppler centre cannot hide a row/axis mismatch.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np


HEADER_BYTES = 256


def wrap_pi(value: np.ndarray | float) -> np.ndarray | float:
    return (value + np.pi) % (2.0 * np.pi) - np.pi


def read_new_protocol(
    path: Path,
    pulse_count: int,
    ddc_len: int,
    channel_count: int,
) -> np.ndarray:
    packet_bytes = HEADER_BYTES + ddc_len * channel_count * 2 * 4
    expected = pulse_count * packet_bytes
    actual = path.stat().st_size
    if actual != expected:
        raise ValueError(
            f"file size mismatch: actual={actual}, expected={expected} "
            f"({pulse_count} packets of {packet_bytes} bytes)"
        )
    packets = np.memmap(path, dtype=np.uint8, mode="r").reshape(
        pulse_count, packet_bytes
    )
    # Packet headers make the payload rows non-contiguous, hence the explicit
    # copy before viewing as little-endian float32.
    payload = np.ascontiguousarray(packets[:, HEADER_BYTES:])
    iq = payload.view("<f4").reshape(pulse_count, ddc_len, channel_count, 2)
    return iq[..., 0].astype(np.float64) + 1j * iq[..., 1].astype(np.float64)


def range_compress(
    raw: np.ndarray,
    fs_hz: float,
    bandwidth_hz: float,
    pulse_width_s: float,
    fft_len: int,
    crop_start: int,
    crop_len: int,
) -> np.ndarray:
    if crop_start < 0 or crop_start + crop_len > fft_len:
        raise ValueError("invalid range-compression crop")
    kr = bandwidth_hz / pulse_width_s
    frequency = np.fft.fftfreq(fft_len, d=1.0 / fs_hz)
    matched_filter = np.exp(1j * np.pi * frequency * frequency / kr)
    spectrum = np.fft.fft(raw, n=fft_len, axis=1)
    compressed = np.fft.ifft(spectrum * matched_filter[None, :, None], axis=1)
    return compressed[:, crop_start : crop_start + crop_len, :]


def production_precompressed_extract(
    payload: np.ndarray,
    range_len: int,
) -> np.ndarray:
    """Mirror the production ``isPC=1`` range extraction path."""
    if payload.ndim != 3 or range_len <= 0:
        raise ValueError("invalid precompressed payload or range length")
    raw_len = int(payload.shape[1])
    stride = raw_len // range_len
    if stride <= 0 or (range_len - 1) * stride >= raw_len:
        raise ValueError("precompressed range extraction exceeds payload")
    indices = np.arange(range_len, dtype=np.int64) * stride
    return payload[:, indices, :]


def estimate_doppler_center(data: np.ndarray, prf_hz: float) -> float:
    correlation = np.sum(data[1:, :] * np.conj(data[:-1, :]))
    return float(prf_hz / (2.0 * np.pi) * np.angle(correlation))


def production_dbs_recenter(
    az_spectrum: np.ndarray,
    doppler_center_hz: float,
    prf_hz: float,
) -> tuple[np.ndarray, dict[str, object]]:
    """Apply the exact circular row shift used by cuda_stage_dbs_async().

    The input is already FFT-shifted, so raw row zero represents -PRF/2.  The
    production code computes a one-based start row, wraps it into [1, Na], and
    copies raw rows [k..Na) followed by [0..k).  Consequently shifted row i
    originates from raw FFT row (i + k) % Na.
    """
    if az_spectrum.ndim < 1 or az_spectrum.shape[0] <= 0:
        raise ValueError("empty azimuth spectrum")
    if not math.isfinite(doppler_center_hz) or not prf_hz > 0.0:
        raise ValueError("invalid Doppler centre or PRF")

    row_count = int(az_spectrum.shape[0])
    center_num_1based = (
        math.floor(
            (doppler_center_hz + 0.5 * prf_hz)
            / prf_hz
            * row_count
        )
        + 1
    )
    start_row_1based = center_num_1based - row_count // 2
    while start_row_1based < 1:
        start_row_1based += row_count
    while start_row_1based > row_count:
        start_row_1based -= row_count
    start_raw_row = start_row_1based - 1

    if start_raw_row == 0:
        shifted = az_spectrum
    else:
        shifted = np.concatenate(
            (az_spectrum[start_raw_row:], az_spectrum[:start_raw_row]),
            axis=0,
        )

    shifted_to_raw = (
        np.arange(row_count, dtype=np.int64) + start_raw_row
    ) % row_count
    raw_center_row = (center_num_1based - 1) % row_count
    shifted_center_row = (raw_center_row - start_raw_row) % row_count
    return shifted, {
        "mode": "production_dbs_circular_shift",
        "start_raw_fft_row": int(start_raw_row),
        "raw_fft_center_row": int(raw_center_row),
        "shifted_center_row": int(shifted_center_row),
        "shifted_to_raw_fft_row": shifted_to_raw,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def weighted_line(x: np.ndarray, y: np.ndarray, weight: np.ndarray) -> tuple[float, float]:
    sw = float(np.sum(weight))
    if not sw > 0.0:
        raise ValueError("zero fit weight")
    mx = float(np.sum(weight * x) / sw)
    my = float(np.sum(weight * y) / sw)
    dx = x - mx
    variance = float(np.sum(weight * dx * dx))
    if not variance > np.finfo(float).eps * sw:
        raise ValueError("degenerate Doppler support")
    k = float(np.sum(weight * dx * (y - my)) / variance)
    return k, my - k * mx


def robust_phase_fit(
    fa_hz: np.ndarray,
    cross: np.ndarray,
    energy: np.ndarray,
    min_coherence: float = 0.05,
    min_relative_energy: float = 1.0e-6,
    min_peak_energy_fraction: float = 0.0,
    huber_delta_rad: float = 0.20,
    inlier_threshold_rad: float = 0.35,
) -> dict[str, object]:
    magnitude = np.abs(cross)
    candidates = np.isfinite(energy) & (energy > 1.0e-12) & (magnitude > 1.0e-15)
    median_energy = float(np.median(energy[candidates]))
    coherence = np.divide(
        magnitude,
        energy,
        out=np.zeros_like(energy),
        where=energy > 0.0,
    )
    coherence = np.minimum(1.0, coherence)
    valid = (
        candidates
        & (energy > max(1.0e-12, min_relative_energy * median_energy))
        & (energy >= min_peak_energy_fraction * float(np.max(energy[candidates])))
        & (coherence >= min_coherence)
    )
    if np.count_nonzero(valid) < 2:
        raise ValueError("too few valid p38 rows")

    x = fa_hz[valid]
    raw_phase = np.angle(cross[valid])
    unwrapped = np.unwrap(raw_phase)
    base_weight = (
        np.sqrt(np.minimum(1.0, energy[valid] / median_energy))
        * coherence[valid]
        * coherence[valid]
    )
    k, b = weighted_line(x, unwrapped, base_weight)
    for _ in range(16):
        prediction = k * x + b
        residual = wrap_pi(raw_phase - prediction)
        huber = np.minimum(1.0, huber_delta_rad / np.maximum(np.abs(residual), 1.0e-30))
        new_k, new_b = weighted_line(x, prediction + residual, base_weight * huber)
        change = abs((new_k - k) * float(np.mean(x)) + new_b - b)
        change += 0.5 * abs(new_k - k) * float(np.ptp(x))
        k, b = new_k, new_b
        if change <= 1.0e-11:
            break
    for _ in range(4):
        prediction = k * x + b
        residual = wrap_pi(raw_phase - prediction)
        inlier = np.abs(residual) <= inlier_threshold_rad
        if np.count_nonzero(inlier) < 2:
            break
        new_k, new_b = weighted_line(
            x, prediction + residual, base_weight * inlier.astype(float)
        )
        change = abs((new_k - k) * float(np.mean(x)) + new_b - b)
        change += 0.5 * abs(new_k - k) * float(np.ptp(x))
        k, b = new_k, new_b
        if change <= 1.0e-11:
            break

    residual = wrap_pi(raw_phase - (k * x + b))
    inlier = np.abs(residual) <= inlier_threshold_rad
    rmse = math.sqrt(
        float(np.sum(base_weight[inlier] * residual[inlier] ** 2))
        / float(np.sum(base_weight[inlier]))
    )
    full_phase = np.full(fa_hz.shape, np.nan)
    full_residual = np.full(fa_hz.shape, np.nan)
    full_used = np.zeros(fa_hz.shape, dtype=bool)
    full_phase[valid] = unwrapped
    full_residual[valid] = residual
    full_used[np.flatnonzero(valid)[inlier]] = True
    return {
        "k": k,
        "b": b,
        "rmse_rad": rmse,
        "sample_count": int(np.count_nonzero(valid)),
        "inlier_ratio": float(np.mean(inlier)),
        "phase": full_phase,
        "residual": full_residual,
        "used": full_used,
        "coherence": coherence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("echo_bin", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pulse-count", type=int, default=130)
    parser.add_argument("--ddc-len", type=int, default=11820)
    parser.add_argument("--channel-count", type=int, default=2)
    parser.add_argument(
        "--input-domain",
        choices=("raw_lfm", "range_compressed"),
        default="raw_lfm",
    )
    parser.add_argument("--fs-hz", type=float, default=60.0e6)
    parser.add_argument("--bandwidth-hz", type=float, default=50.0e6)
    parser.add_argument("--pulse-width-s", type=float, default=130.0e-6)
    parser.add_argument("--range-fft-len", type=int, default=12288)
    parser.add_argument("--range-crop-start", type=int, default=3864)
    parser.add_argument("--range-len", type=int, default=4096)
    parser.add_argument("--prf-hz", type=float, default=1300.0)
    parser.add_argument("--fc-hz", type=float, default=16.0e9)
    parser.add_argument("--platform-speed-mps", type=float, default=60.0)
    parser.add_argument("--baseline-m", type=float, default=0.17)
    parser.add_argument("--rx-baseline-sign", type=int, default=1)
    parser.add_argument("--channel-phase-sign", type=int, default=-1)
    parser.add_argument("--support-half-angle-deg", type=float, default=2.0)
    parser.add_argument("--rg-start", type=int, default=1)
    parser.add_argument("--rg-end", type=int, default=4095)
    parser.add_argument(
        "--strong-range-mask-ratio",
        type=float,
        default=0.0,
        help="mask range cross-sums above ratio*median; 0 disables the fallback",
    )
    parser.add_argument("--strong-range-mask-dilation-bins", type=int, default=32)
    parser.add_argument("--min-peak-row-energy-fraction", type=float, default=0.05)
    parser.add_argument("--min-sample-count", type=int, default=8)
    parser.add_argument("--min-inlier-ratio", type=float, default=0.60)
    parser.add_argument("--max-rmse-rad", type=float, default=0.60)
    args = parser.parse_args()

    if args.rx_baseline_sign not in (-1, 1):
        raise ValueError("rx-baseline-sign must be -1 or 1")
    if args.channel_phase_sign not in (-1, 1):
        raise ValueError("channel-phase-sign must be -1 or 1")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw = read_new_protocol(
        args.echo_bin, args.pulse_count, args.ddc_len, args.channel_count
    )
    if args.input_domain == "raw_lfm":
        rc = range_compress(
            raw,
            args.fs_hz,
            args.bandwidth_hz,
            args.pulse_width_s,
            args.range_fft_len,
            args.range_crop_start,
            args.range_len,
        )
        range_processing_mode = "analytic_matched_filter_then_crop"
    else:
        rc = production_precompressed_extract(raw, args.range_len)
        range_processing_mode = "production_isPC_stride_extract"
    fa_center = estimate_doppler_center(rc[:, :, 0], args.prf_hz)
    doppler_bin_hz = args.prf_hz / args.pulse_count
    fa_hz = (
        -0.5 * args.prf_hz
        + np.arange(args.pulse_count) * doppler_bin_hz
        + fa_center
    )
    raw_fft_spectrum = np.fft.fftshift(np.fft.fft(rc, axis=0), axes=0)
    az_spectrum, dbs = production_dbs_recenter(
        raw_fft_spectrum, fa_center, args.prf_hz
    )
    shifted_to_raw = np.asarray(dbs["shifted_to_raw_fft_row"])
    raw_fft_fa_hz = -0.5 * args.prf_hz + shifted_to_raw * doppler_bin_hz
    rg_start = max(0, min(args.rg_start, args.range_len - 1))
    rg_end = max(0, min(args.rg_end, args.range_len - 1))
    if rg_start > rg_end:
        raise ValueError("invalid inclusive p38 range support")
    bw_hz = (
        2.0
        * args.platform_speed_mps
        * math.sin(math.radians(args.support_half_angle_deg))
        / (299792458.0 / args.fc_hz)
    )
    support = np.abs(fa_hz - fa_center) <= bw_hz
    range_mask = np.zeros(args.range_len, dtype=bool)
    mask_median = math.nan
    mask_threshold = math.nan
    if args.strong_range_mask_ratio > 0.0:
        range_cross_sum = np.sum(
            az_spectrum[support, :, 0]
            * np.conj(az_spectrum[support, :, 1]),
            axis=0,
        )
        support_magnitude = np.abs(range_cross_sum[rg_start : rg_end + 1])
        finite_positive = support_magnitude[
            np.isfinite(support_magnitude) & (support_magnitude > 0.0)
        ]
        if finite_positive.size < 8:
            raise ValueError("too few finite range statistics for strong-range mask")
        mask_median = float(np.median(finite_positive))
        mask_threshold = args.strong_range_mask_ratio * mask_median
        strong = np.zeros(args.range_len, dtype=bool)
        strong[rg_start : rg_end + 1] = (
            support_magnitude > mask_threshold
        )
        dilation = max(0, args.strong_range_mask_dilation_bins)
        if dilation:
            strong = np.convolve(
                strong.astype(np.int8),
                np.ones(2 * dilation + 1, dtype=np.int16),
                mode="same",
            ) > 0
        range_mask = strong
    selected_range = np.arange(rg_start, rg_end + 1, dtype=np.int64)
    selected_range = selected_range[~range_mask[selected_range]]
    if selected_range.size < 8:
        raise ValueError("too few unmasked p38 range bins")
    rg = selected_range
    row_cross = np.sum(
        az_spectrum[:, rg, 0] * np.conj(az_spectrum[:, rg, 1]), axis=1
    )
    e1 = np.sum(np.abs(az_spectrum[:, rg, 0]) ** 2, axis=1)
    e2 = np.sum(np.abs(az_spectrum[:, rg, 1]) ** 2, axis=1)
    row_energy = np.sqrt(e1 * e2)
    fit = robust_phase_fit(
        fa_hz[support], row_cross[support], row_energy[support],
        min_peak_energy_fraction=args.min_peak_row_energy_fraction,
    )

    theory_sign = args.rx_baseline_sign * args.channel_phase_sign
    theory_k = (
        theory_sign * math.pi * args.baseline_m / args.platform_speed_mps
    )
    relative_error = abs((float(fit["k"]) - theory_k) / theory_k)
    fit_valid = (
        int(fit["sample_count"]) >= max(2, args.min_sample_count)
        and float(fit["inlier_ratio"]) >= args.min_inlier_ratio
        and float(fit["rmse_rad"]) <= args.max_rmse_rad
    )
    support_indices = np.flatnonzero(support)
    range_power = np.sum(np.abs(rc[:, :, 0]) ** 2, axis=0)
    active_range = np.flatnonzero(range_power >= np.max(range_power) * 1.0e-6)
    summary = {
        "echo_bin": str(args.echo_bin.resolve()),
        "echo_size_bytes": args.echo_bin.stat().st_size,
        "echo_mtime_ns": args.echo_bin.stat().st_mtime_ns,
        "echo_sha256": sha256_file(args.echo_bin),
        "pulse_count": args.pulse_count,
        "input_domain": args.input_domain,
        "range_processing_mode": range_processing_mode,
        "range_fft_len": args.range_fft_len,
        "range_crop_start": args.range_crop_start,
        "range_len": args.range_len,
        "doppler_center_hz": fa_center,
        "doppler_bin_hz": doppler_bin_hz,
        "doppler_row_index_domain": "DBS-shifted rows",
        "doppler_shift_mode": dbs["mode"],
        "doppler_shift_start_raw_fft_row": dbs["start_raw_fft_row"],
        "doppler_shift_start_raw_fft_fa_wrapped_hz": float(
            -0.5 * args.prf_hz
            + int(dbs["start_raw_fft_row"]) * doppler_bin_hz
        ),
        "doppler_raw_fft_center_row": dbs["raw_fft_center_row"],
        "doppler_raw_fft_center_fa_wrapped_hz": float(
            -0.5 * args.prf_hz
            + int(dbs["raw_fft_center_row"]) * doppler_bin_hz
        ),
        "doppler_shifted_center_row": dbs["shifted_center_row"],
        "doppler_shifted_center_fa_hz": float(
            fa_hz[int(dbs["shifted_center_row"])]
        ),
        "support_bw_one_sided_hz": bw_hz,
        "support_first_row": int(support_indices[0]),
        "support_last_row": int(support_indices[-1]),
        "support_row_count": int(np.count_nonzero(support)),
        "support_raw_fft_rows": [
            int(value) for value in shifted_to_raw[support_indices]
        ],
        "configured_range_support_first_bin": rg_start,
        "configured_range_support_last_bin": rg_end,
        "configured_range_support_bin_count": rg_end - rg_start + 1,
        "strong_range_mask_ratio": args.strong_range_mask_ratio,
        "strong_range_mask_dilation_bins": args.strong_range_mask_dilation_bins,
        "strong_range_mask_median_cross": mask_median,
        "strong_range_mask_threshold": mask_threshold,
        "strong_range_masked_bin_count": int(np.count_nonzero(range_mask)),
        "p38_unmasked_range_bin_count": int(selected_range.size),
        "power_active_range_first_bin_at_minus60db": int(active_range[0]),
        "power_active_range_last_bin_at_minus60db": int(active_range[-1]),
        "fit_k_rad_per_hz": float(fit["k"]),
        "fit_b_rad": float(fit["b"]),
        "fit_rmse_rad": float(fit["rmse_rad"]),
        "fit_sample_count": int(fit["sample_count"]),
        "fit_inlier_ratio": float(fit["inlier_ratio"]),
        "fit_valid": fit_valid,
        "min_peak_row_energy_fraction": args.min_peak_row_energy_fraction,
        "min_sample_count": args.min_sample_count,
        "min_inlier_ratio": args.min_inlier_ratio,
        "max_rmse_rad": args.max_rmse_rad,
        "rx_baseline_sign": args.rx_baseline_sign,
        "channel_phase_sign": args.channel_phase_sign,
        "theory_sign": theory_sign,
        "theory_k_rad_per_hz": theory_k,
        "relative_slope_error": relative_error,
        "range_peak_bin": int(np.argmax(np.sum(np.abs(rc[:, :, 0]) ** 2, axis=0))),
    }
    (args.output_dir / "p38_audit_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    with (args.output_dir / "p38_phase_samples.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "shifted_row",
                "raw_fft_row",
                "raw_fft_fa_wrapped_hz",
                "fa_hz",
                "phase_unwrapped_rad",
                "residual_rad",
                "energy",
                "coherence",
                "used",
            ]
        )
        for local, row in enumerate(support_indices):
            writer.writerow(
                [
                    int(row),
                    int(shifted_to_raw[row]),
                    float(raw_fft_fa_hz[row]),
                    float(fa_hz[row]),
                    float(fit["phase"][local]),
                    float(fit["residual"][local]),
                    float(row_energy[row]),
                    float(fit["coherence"][local]),
                    int(fit["used"][local]),
                ]
            )

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = fa_hz[support]
    phase = np.asarray(fit["phase"])
    used = np.asarray(fit["used"])
    fig, axes = plt.subplots(2, 1, figsize=(9.0, 7.0), sharex=True)
    axes[0].plot(x, phase, ".", color="0.65", label="accepted input")
    axes[0].plot(x[used], phase[used], "o", ms=4, label="robust inlier")
    axes[0].plot(x, float(fit["k"]) * x + float(fit["b"]), "r-", label="robust fit")
    axes[0].set_ylabel("unwrapped phase (rad)")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend()
    axes[0].set_title(
        f"p38: k={float(fit['k']):.9f} rad/Hz, theory={theory_k:.9f}, "
        f"rel.err={relative_error:.3%}"
    )
    axes[1].plot(x, np.asarray(fit["residual"]), ".-")
    axes[1].axhline(0.0, color="k", lw=0.8)
    axes[1].set_xlabel("Doppler frequency (Hz)")
    axes[1].set_ylabel("circular residual (rad)")
    axes[1].grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(args.output_dir / "p38_phase_fit.png", dpi=160)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(9.0, 3.6))
    axis.plot(np.arange(args.range_len), 10.0 * np.log10(range_power + 1.0e-30))
    axis.set_xlabel("range-compressed bin")
    axis.set_ylabel("integrated power (dB)")
    axis.set_title("Stage3 range-compressed clutter support")
    axis.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(args.output_dir / "range_compressed_power.png", dpi=160)
    plt.close(fig)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
