#!/usr/bin/env python3
"""Audit a Stage2 reused-background phase-input mapping.

The audit compares a target file with the source background packet group that
should have been copied for the requested beam.  It deliberately stops at the
new-protocol payload and does not implement a second GMTI detector.  The
reported channel-pair phase is

    angle(sum(x1 * conj(x2)))

which is the same cross-product convention as the production CSI path.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import struct
from pathlib import Path

import numpy as np


HEADER_BYTES = 256
MAGIC_HEAD = 0x5A5A5A5A5A5A5A5A
MAGIC_TAIL = 0x5B5B5B5B5B5B5B5B
C = 299_792_458.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def wrap_pi(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def header(path: Path, packet_index: int, packet_bytes: int) -> dict[str, float | int]:
    with path.open("rb") as stream:
        stream.seek(packet_index * packet_bytes)
        raw = stream.read(HEADER_BYTES)
    if len(raw) != HEADER_BYTES:
        raise ValueError(f"short header: {path} packet={packet_index}")
    return {
        "packet_index": packet_index,
        "magic_head": struct.unpack_from("<Q", raw, 0)[0],
        "version": raw[8],
        "prt_len": struct.unpack_from("<I", raw, 9)[0],
        "utc": struct.unpack_from("<f", raw, 16)[0],
        "prt_counter": struct.unpack_from("<I", raw, 20)[0],
        "theta_deg": struct.unpack_from("<h", raw, 218)[0] / 100.0,
        "magic_tail": struct.unpack_from("<Q", raw, 248)[0],
    }


def read_payload(
    path: Path,
    packet_index: int,
    packet_bytes: int,
    samples: int,
    channels: int,
    iq_type: str,
) -> np.ndarray:
    if iq_type not in {"int16", "float32"}:
        raise ValueError(f"unsupported audit IQ type: {iq_type}")
    dtype = np.dtype("<i2" if iq_type == "int16" else "<f4")
    with path.open("rb") as stream:
        stream.seek(packet_index * packet_bytes + HEADER_BYTES)
        values = np.fromfile(stream, dtype=dtype, count=samples * channels * 2)
    if values.size != samples * channels * 2:
        raise ValueError(f"short payload: {path} packet={packet_index}")
    values = values.reshape(samples, channels, 2).astype(np.float64)
    return values[..., 0] + 1j * values[..., 1]


def cross_metric(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    a = np.asarray(a).reshape(-1)
    b = np.asarray(b).reshape(-1)
    cross = np.sum(a * np.conj(b))
    denom = math.sqrt(float(np.sum(np.abs(a) ** 2) * np.sum(np.abs(b) ** 2)))
    return {
        "coherence": float(abs(cross) / denom) if denom > 0.0 else 0.0,
        "phase_deg": math.degrees(float(np.angle(cross))) if abs(cross) > 0.0 else 0.0,
    }


def payload_metric(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    rows: dict[str, float] = {}
    for channel in range(a.shape[1]):
        metric = cross_metric(a[:, channel], b[:, channel])
        rows[f"ch{channel + 1}_coherence"] = metric["coherence"]
        rows[f"ch{channel + 1}_phase_deg"] = metric["phase_deg"]
        rows[f"ch{channel + 1}_rmse"] = float(
            math.sqrt(float(np.mean(np.abs(a[:, channel] - b[:, channel]) ** 2)))
        )
    return rows


def pair_metric(path: Path, packet_indices: list[int], packet_bytes: int,
                samples: int, channels: int, iq_type: str, lo: int, hi: int) -> dict[str, float]:
    ch1: list[np.ndarray] = []
    ch2: list[np.ndarray] = []
    for packet_index in packet_indices:
        payload = read_payload(path, packet_index, packet_bytes, samples, channels, iq_type)
        ch1.append(payload[lo:hi, 0])
        ch2.append(payload[lo:hi, 1])
    return cross_metric(np.concatenate(ch1), np.concatenate(ch2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--source-background", required=True, type=Path)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--requested-beam", required=True, type=int,
                        help="1-based physical beam in the source scan")
    parser.add_argument("--source-beam-count", required=True, type=int,
                        help="number of electronic beams in the source period")
    parser.add_argument("--output-first-packet", type=int, default=0,
                        help="first packet of the requested beam in the output file (default: 0)")
    parser.add_argument("--range-bin", type=int, default=1500)
    parser.add_argument("--range-half-width", type=int, default=250)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    waveform = scenario["waveform"]
    scan = scenario["scan"]
    samples = int(waveform["pulse_len"])
    pulses = int(waveform["pulse_num"])
    channels = int(waveform["new_protocol_channel_count"])
    iq_type = str(waveform["iq_data_type"])
    iq_bytes = 2 if iq_type == "int16" else 4
    packet_bytes = HEADER_BYTES + samples * channels * 2 * iq_bytes
    requested_theta = float(scan["scan_min_deg"]) + float(scan["scan_step_deg"]) * (args.requested_beam - 1)
    source_first_packet = (args.requested_beam - 1) * pulses
    if args.requested_beam < 1 or args.requested_beam > args.source_beam_count:
        raise SystemExit("requested beam is outside the source scan")
    if args.output_first_packet < 0:
        raise SystemExit("output first packet must be non-negative")
    if args.range_bin < 0 or args.range_bin >= samples:
        raise SystemExit("range bin is outside the raw packet")
    lo = max(0, args.range_bin - args.range_half_width)
    hi = min(samples, args.range_bin + args.range_half_width)
    packet_indices = [args.output_first_packet + pulse for pulse in range(pulses)]
    source_indices = [source_first_packet + pulse for pulse in range(pulses)]

    before_first = header(args.before, args.output_first_packet, packet_bytes)
    after_first = header(args.after, args.output_first_packet, packet_bytes)
    source_first = header(args.source_background, source_first_packet, packet_bytes)
    source_beam1 = header(args.source_background, 0, packet_bytes)
    payload_before = read_payload(
        args.before, args.output_first_packet, packet_bytes, samples, channels, iq_type
    )
    payload_after = read_payload(
        args.after, args.output_first_packet, packet_bytes, samples, channels, iq_type
    )
    payload_source = read_payload(
        args.source_background, source_first_packet, packet_bytes, samples, channels, iq_type
    )
    rows: list[dict[str, object]] = []
    for label, path, source_index in (
        ("source_requested_beam", args.source_background, source_first_packet),
        ("before_output", args.before, args.output_first_packet),
        ("after_output", args.after, args.output_first_packet),
    ):
        rows.append({
            "kind": label,
            "first_theta_deg": header(path, source_index, packet_bytes)["theta_deg"],
            "first_utc": header(path, source_index, packet_bytes)["utc"],
            "first_prt_counter": header(path, source_index, packet_bytes)["prt_counter"],
            "raw_pair_coherence": pair_metric(path,
                source_indices if label == "source_requested_beam" else packet_indices,
                packet_bytes, samples, channels, iq_type, 0, samples)["coherence"],
            "raw_pair_phase_deg": pair_metric(path,
                source_indices if label == "source_requested_beam" else packet_indices,
                packet_bytes, samples, channels, iq_type, 0, samples)["phase_deg"],
            "range_pair_coherence": pair_metric(path,
                source_indices if label == "source_requested_beam" else packet_indices,
                packet_bytes, samples, channels, iq_type, lo, hi)["coherence"],
            "range_pair_phase_deg": pair_metric(path,
                source_indices if label == "source_requested_beam" else packet_indices,
                packet_bytes, samples, channels, iq_type, lo, hi)["phase_deg"],
        })

    before_source_beam1 = read_payload(args.source_background, 0, packet_bytes, samples, channels, iq_type)
    after_source_diff = payload_after - payload_source
    before_source_requested_diff = payload_before - payload_source
    rows.extend([
        {
            "kind": "before_vs_source_beam1_payload",
            **payload_metric(payload_before, before_source_beam1),
        },
        {
            "kind": "after_vs_source_requested_beam_payload",
            **payload_metric(payload_after, payload_source),
        },
        {
            "kind": "before_minus_source_requested_target_window",
            **cross_metric(before_source_requested_diff[lo:hi, 0], before_source_requested_diff[lo:hi, 1]),
        },
        {
            "kind": "after_minus_source_requested_target_window",
            **cross_metric(after_source_diff[lo:hi, 0], after_source_diff[lo:hi, 1]),
        },
    ])

    wavelength = C / (float(waveform["fc_ghz"]) * 1.0e9)
    ideal_requested = wrap_pi(2.0 * math.pi * float(waveform["d_chan_m"]) *
                              math.sin(math.radians(requested_theta)) / wavelength)
    ideal_source_beam1 = wrap_pi(2.0 * math.pi * float(waveform["d_chan_m"]) *
                                 math.sin(math.radians(float(scan["scan_min_deg"]))) / wavelength)
    report = {
        "scenario": str(args.scenario.resolve()),
        "source_background": str(args.source_background.resolve()),
        "before": str(args.before.resolve()),
        "after": str(args.after.resolve()),
        "sha256": {name: sha256(path) for name, path in (
            ("source_background", args.source_background),
            ("before", args.before),
            ("after", args.after),
        )},
        "packet_bytes": packet_bytes,
        "samples_per_prt": samples,
        "pulses_per_beam": pulses,
        "channels": channels,
        "requested_beam_1based": args.requested_beam,
        "requested_theta_deg": requested_theta,
        "source_requested_beam_first_packet": source_first_packet,
        "output_requested_beam_first_packet": args.output_first_packet,
        "source_beam1_header": source_beam1,
        "source_requested_beam_header": source_first,
        "before_first_header": before_first,
        "after_first_header": after_first,
        "range_window": [lo, hi],
        "ideal_phase_model": {
            "formula": "wrap(2*pi*d_chan*sin(theta)/lambda) for current left-looking geometry",
            "wavelength_m": wavelength,
            "requested_beam_phase_deg": math.degrees(ideal_requested),
            "source_beam1_phase_deg": math.degrees(ideal_source_beam1),
        },
        "rows": rows,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "phase_input_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fields = sorted({key for row in rows for key in row})
    with (args.output_dir / "phase_input_audit.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
