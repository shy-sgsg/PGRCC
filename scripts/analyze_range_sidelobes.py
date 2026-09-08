#!/usr/bin/env python3
"""Diagnose range sidelobes from paired target-on/off new-protocol data.

The ``production_phase_only`` branch reproduces the range-compression transfer
function used by production code.  The band-limited and Kaiser branches are
reference diagnostics only and are never reported as production detections.
"""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np


C = 299792458.0


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def xml_params(path):
    params = ET.parse(path).getroot().find("GMTI_parameter")
    if params is None:
        raise RuntimeError("missing GMTI_parameter")
    return {child.tag: (child.text or "").strip() for child in params}


def read_packets(path, packet_count, pulse_len, channel_count, info_len):
    packet_bytes = info_len + pulse_len * channel_count * 2 * 4
    expected = packet_count * packet_bytes
    if path.stat().st_size < expected:
        raise RuntimeError(f"{path}: expected at least {expected} bytes")
    raw = np.memmap(path, mode="r", dtype=np.uint8, shape=(packet_count, packet_bytes))
    values = np.empty((packet_count, pulse_len, channel_count), dtype=np.complex64)
    headers = np.asarray(raw[:, :info_len]).copy()
    for pulse in range(packet_count):
        iq = np.frombuffer(raw[pulse, info_len:].tobytes(), dtype="<f4")
        iq = iq.reshape(pulse_len, channel_count, 2)
        values[pulse] = iq[..., 0] + 1j * iq[..., 1]
    return values, headers


def make_transfer_functions(nfft, fs_hz, bandwidth_hz, tr_sec):
    frequency = np.fft.fftfreq(nfft, d=1.0 / fs_hz)
    kr = bandwidth_hz / tr_sec
    phase = np.exp(1j * np.pi * frequency * frequency / kr)
    passband = np.abs(frequency) <= bandwidth_hz / 2.0

    bandlimited = phase * passband
    kaiser_weight = np.zeros(nfft, dtype=np.float64)
    indexes = np.flatnonzero(passband)
    # Assign the taper in sorted physical-frequency order, then return to FFT order.
    ordered = indexes[np.argsort(frequency[indexes])]
    kaiser_weight[ordered] = np.kaiser(ordered.size, 6.0)
    kaiser = phase * kaiser_weight
    return {
        "production_phase_only": phase,
        "bandlimited_phase_reference": bandlimited,
        "kaiser6_bandlimited_reference": kaiser,
    }


def compress(target_echo, transfer, nfft, crop_start, crop_len):
    spectrum = np.fft.fft(target_echo, n=nfft, axis=1)
    compressed = np.fft.ifft(spectrum * transfer[None, :, None], axis=1)
    cropped = compressed[:, crop_start:crop_start + crop_len, :]
    return np.mean(np.abs(cropped) ** 2, axis=(0, 2))


def relative_db(power, reference):
    return 10.0 * np.log10(np.maximum(power, 1e-300) / max(reference, 1e-300))


def profile_summary(name, power, expected_bin, exclusion_half_width):
    peak_bin = int(np.argmax(power))
    peak_power = float(power[peak_bin])
    mask = np.ones(power.size, dtype=bool)
    lo = max(0, expected_bin - exclusion_half_width)
    hi = min(power.size, expected_bin + exclusion_half_width + 1)
    mask[lo:hi] = False
    side_bin = int(np.flatnonzero(mask)[np.argmax(power[mask])])
    side_power = float(power[side_bin])
    return {
        "method": name,
        "peak_bin": peak_bin,
        "peak_power": peak_power,
        "peak_offset_bin": peak_bin - expected_bin,
        "exclusion_half_width_bins": exclusion_half_width,
        "max_sidelobe_bin": side_bin,
        "max_sidelobe_power": side_power,
        "pslr_db": float(relative_db(side_power, peak_power)),
    }


def read_detection_bins(path):
    if path is None or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return sorted({int(float(row["range_bin"])) for row in csv.DictReader(handle)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-on", required=True, type=Path)
    parser.add_argument("--target-off", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-bin", required=True, type=int)
    parser.add_argument("--detection-csv", type=Path)
    parser.add_argument("--packet-count", type=int)
    parser.add_argument("--exclusion-half-width", type=int, default=8)
    args = parser.parse_args()

    p = xml_params(args.config)
    pulse_len = int(p["pulse_len"])
    pulse_count = args.packet_count or int(p.get("read_pulse_num", p["pulse_num"]))
    channel_count = int(p.get("new_protocol_channel_count", 2))
    info_len = int(p.get("info_len", 256))
    nfft = int(p.get("range_fft_len", 12288))
    crop_start = int(p.get("range_crop_start", 3864))
    crop_len = int(p.get("range_crop_len", p.get("rg_len", 4096)))
    fs_hz = float(p["fs"]) * 1.0e6
    bandwidth_hz = float(p["Br"]) * 1.0e6
    tr_sec = float(p["Tr"])
    if tr_sec > 1.0:
        tr_sec *= 1.0e-6

    on, on_headers = read_packets(
        args.target_on, pulse_count, pulse_len, channel_count, info_len)
    off, off_headers = read_packets(
        args.target_off, pulse_count, pulse_len, channel_count, info_len)
    if not np.array_equal(on_headers, off_headers):
        raise RuntimeError("paired target-on/off headers differ")
    target_echo = on - off
    transfers = make_transfer_functions(nfft, fs_hz, bandwidth_hz, tr_sec)
    profiles = {
        name: compress(target_echo, transfer, nfft, crop_start, crop_len)
        for name, transfer in transfers.items()
    }
    if not (0 <= args.expected_bin < crop_len):
        raise RuntimeError("expected bin is outside cropped range")

    summaries = [
        profile_summary(name, power, args.expected_bin, args.exclusion_half_width)
        for name, power in profiles.items()
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_fields = list(summaries[0])
    with (args.output_dir / "range_sidelobe_summary.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(summaries)

    sample_delay_us = float(p.get("sample_delay_us", 0.0))
    profile_fields = ["range_bin", "range_m"]
    for name in profiles:
        profile_fields.extend([f"{name}_power", f"{name}_relative_db"])
    with (args.output_dir / "range_sidelobe_profiles.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=profile_fields)
        writer.writeheader()
        for index in range(crop_len):
            row = {
                "range_bin": index,
                "range_m": (sample_delay_us * 1.0e-6 +
                            (crop_start + index) / fs_hz) * C / 2.0,
            }
            for name, power in profiles.items():
                reference = float(power[args.expected_bin])
                row[f"{name}_power"] = float(power[index])
                row[f"{name}_relative_db"] = float(relative_db(power[index], reference))
            writer.writerow(row)

    detection_rows = []
    for index in read_detection_bins(args.detection_csv):
        row = {"range_bin": index, "offset_from_truth_bin": index - args.expected_bin}
        for name, power in profiles.items():
            row[f"{name}_relative_db"] = float(
                relative_db(power[index], power[args.expected_bin]))
        detection_rows.append(row)
    detection_fields = ["range_bin", "offset_from_truth_bin"] + [
        f"{name}_relative_db" for name in profiles]
    with (args.output_dir / "detected_bin_sidelobes.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=detection_fields)
        writer.writeheader()
        writer.writerows(detection_rows)

    manifest = {
        "status": "ok",
        "scope": "paired raw target isolation and range-compression diagnostic",
        "production_metric_source": "production_phase_only reproduces current transfer function; reference branches are not production results",
        "target_on": str(args.target_on),
        "target_on_sha256": sha256_file(args.target_on),
        "target_off": str(args.target_off),
        "target_off_sha256": sha256_file(args.target_off),
        "config": str(args.config),
        "config_sha256": sha256_file(args.config),
        "pulse_count": pulse_count,
        "expected_bin": args.expected_bin,
        "header_equality": True,
        "summary": summaries,
    }
    (args.output_dir / "range_sidelobe_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
