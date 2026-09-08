#!/usr/bin/env python3
"""Validate an in-memory Stage3 raw-LFM target injection against a baseline."""

import argparse
import csv
import json
import math
import os

import numpy as np


HEADER_BYTES = 256


def load_packets(path, pulse_len, channel_count):
    sample_bytes = channel_count * 2 * 4
    packet_bytes = HEADER_BYTES + pulse_len * sample_bytes
    raw = np.fromfile(path, dtype=np.uint8)
    if raw.size == 0 or raw.size % packet_bytes:
        raise ValueError(f"invalid packet file size: {path} ({raw.size} bytes)")
    packets = raw.reshape((-1, packet_bytes))
    headers = packets[:, :HEADER_BYTES].copy()
    iq = packets[:, HEADER_BYTES:].copy().view("<f4")
    iq = iq.reshape((-1, pulse_len, channel_count, 2))
    return headers, iq


def load_truth(path):
    with open(path, "r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f"empty truth CSV: {path}")
    return rows


def as_complex(iq, channel):
    return iq[..., channel, 0].astype(np.float64) + 1j * iq[..., channel, 1].astype(np.float64)


def range_compress(x, fs_hz, bandwidth_hz, pulse_width_s, fft_len, crop_start, crop_len):
    kr = bandwidth_hz / pulse_width_s
    frequency = np.fft.fftfreq(fft_len, d=1.0 / fs_hz)
    hf = np.exp(1j * np.pi * frequency * frequency / kr)
    return np.fft.ifft(np.fft.fft(x, n=fft_len) * hf)[crop_start : crop_start + crop_len]


def range_compress_batch(x, fs_hz, bandwidth_hz, pulse_width_s, fft_len, crop_start, crop_len):
    kr = bandwidth_hz / pulse_width_s
    frequency = np.fft.fftfreq(fft_len, d=1.0 / fs_hz)
    hf = np.exp(1j * np.pi * frequency * frequency / kr)
    spectrum = np.fft.fft(x, n=fft_len, axis=1)
    return np.fft.ifft(spectrum * hf[None, :], axis=1)[:, crop_start : crop_start + crop_len]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-bin", required=True)
    parser.add_argument("--baseline-bin", required=True)
    parser.add_argument("--truth-pulse", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--pulse-len", type=int, default=11820)
    parser.add_argument("--channel-count", type=int, default=2)
    parser.add_argument("--fs-hz", type=float, default=60e6)
    parser.add_argument("--bandwidth-hz", type=float, default=50e6)
    parser.add_argument("--pulse-width-s", type=float, default=130e-6)
    parser.add_argument("--fft-len", type=int, default=12288)
    parser.add_argument("--crop-start", type=int, default=3864)
    parser.add_argument("--crop-len", type=int, default=4096)
    args = parser.parse_args()

    target_headers, target_iq = load_packets(
        args.target_bin, args.pulse_len, args.channel_count
    )
    baseline_headers, baseline_iq = load_packets(
        args.baseline_bin, args.pulse_len, args.channel_count
    )
    if target_iq.shape != baseline_iq.shape:
        raise ValueError(f"shape mismatch: {target_iq.shape} vs {baseline_iq.shape}")
    truth = load_truth(args.truth_pulse)
    if len(truth) != target_iq.shape[0]:
        raise ValueError(f"truth/packet count mismatch: {len(truth)} vs {target_iq.shape[0]}")

    diff = target_iq.astype(np.float64) - baseline_iq.astype(np.float64)
    complex_diff = [as_complex(diff, channel) for channel in range(args.channel_count)]
    total_power = sum(np.abs(channel) ** 2 for channel in complex_diff)
    nonzero = total_power > 1e-20

    chirp_samples = int(math.floor(args.pulse_width_s * args.fs_hz + 0.5))
    outside_power = 0.0
    all_power = float(np.sum(total_power))
    pulse_support = []
    for pulse_index, row in enumerate(truth):
        delay = float(row["range_sample_float"])
        lo = max(0, int(math.floor(delay)))
        hi = min(args.pulse_len, lo + chirp_samples)
        mask = np.ones(args.pulse_len, dtype=bool)
        mask[lo:hi] = False
        outside_power += float(np.sum(total_power[pulse_index, mask]))
        used = np.flatnonzero(nonzero[pulse_index])
        pulse_support.append(
            {
                "pulse": pulse_index,
                "truth_delay_sample": delay,
                "first_nonzero_sample": int(used[0]) if used.size else -1,
                "last_nonzero_sample": int(used[-1]) if used.size else -1,
            }
        )

    ref_index = len(truth) // 2
    ref_truth = truth[ref_index]
    compressed_all = [
        range_compress_batch(
            channel,
            args.fs_hz,
            args.bandwidth_hz,
            args.pulse_width_s,
            args.fft_len,
            args.crop_start,
            args.crop_len,
        )
        for channel in complex_diff
    ]
    compressed = [channel[ref_index] for channel in compressed_all]
    compressed_power = sum(np.abs(channel) ** 2 for channel in compressed)
    peak_bin = int(np.argmax(compressed_power))
    expected_bin = int(float(ref_truth["expected_range_bin"]))
    cross = compressed[0][peak_bin] * np.conj(compressed[1][peak_bin])
    doppler_maps = [
        np.fft.fftshift(np.fft.fft(channel, axis=0), axes=0)
        for channel in compressed_all
    ]
    doppler_power = sum(np.abs(channel) ** 2 for channel in doppler_maps)
    doppler_row, doppler_range_bin = np.unravel_index(
        int(np.argmax(doppler_power)), doppler_power.shape
    )
    expected_doppler_row = int(float(ref_truth["row_truth"]))

    result = {
        "target_bin": args.target_bin,
        "baseline_bin": args.baseline_bin,
        "packet_count": int(target_iq.shape[0]),
        "packet_shape": list(target_iq.shape),
        "headers_identical": bool(np.array_equal(target_headers, baseline_headers)),
        "header_different_bytes": int(np.count_nonzero(target_headers != baseline_headers)),
        "difference": {
            "nonzero_complex_samples": int(np.count_nonzero(nonzero)),
            "max_abs": float(max(np.max(np.abs(channel)) for channel in complex_diff)),
            "rms": float(math.sqrt(all_power / max(1, nonzero.size))),
            "total_power": all_power,
            "outside_truth_chirp_power": outside_power,
            "outside_truth_chirp_power_ratio": outside_power / all_power if all_power else 0.0,
        },
        "range_compression": {
            "reference_packet": ref_index,
            "truth_delay_sample": float(ref_truth["range_sample_float"]),
            "expected_range_bin": expected_bin,
            "measured_peak_bin": peak_bin,
            "peak_bin_error": peak_bin - expected_bin,
            "peak_power": float(compressed_power[peak_bin]),
            "channel_phase_rad": float(np.angle(cross)),
            "truth_channel_phase_rad": float(ref_truth["delta_phi_ch_rad"]),
        },
        "range_doppler": {
            "expected_doppler_row": expected_doppler_row,
            "measured_peak_row": int(doppler_row),
            "peak_row_error": int(doppler_row) - expected_doppler_row,
            "expected_range_bin": expected_bin,
            "measured_peak_range_bin": int(doppler_range_bin),
            "peak_range_bin_error": int(doppler_range_bin) - expected_bin,
            "peak_power": float(doppler_power[doppler_row, doppler_range_bin]),
        },
        "support_examples": [pulse_support[0], pulse_support[ref_index], pulse_support[-1]],
        "pass": bool(
            np.array_equal(target_headers, baseline_headers)
            and all_power > 0.0
            and outside_power <= max(1e-18, all_power * 1e-12)
            and abs(peak_bin - expected_bin) <= 1
            and abs(int(doppler_row) - expected_doppler_row) <= 1
            and abs(int(doppler_range_bin) - expected_bin) <= 1
        ),
    }
    os.makedirs(os.path.dirname(args.output_json) or ".", exist_ok=True)
    with open(args.output_json, "w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
