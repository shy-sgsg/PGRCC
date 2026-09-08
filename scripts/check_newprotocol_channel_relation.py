#!/usr/bin/env python3
"""Measure raw NewProtocol complex-channel pairing without altering the data."""

import argparse
import cmath
import struct
from pathlib import Path


HEADER_BYTES = 256
OFF_PRT_LEN = 9


def load_complex(packet: bytes, sample: int, channel_1based: int, channels: int,
                 iq_type: str) -> complex:
    component_bytes = 2 if iq_type == "int16" else 4
    complex_bytes = 2 * component_bytes
    offset = HEADER_BYTES + (sample * channels + channel_1based - 1) * complex_bytes
    i, q = struct.unpack_from("<hh" if iq_type == "int16" else "<ff", packet, offset)
    return complex(i, q)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data")
    parser.add_argument("--channels", type=int, required=True, choices=(2, 4))
    parser.add_argument("--iq-type", choices=("float32", "int16"), default="float32")
    parser.add_argument("--left", type=int, default=1)
    parser.add_argument("--right", type=int, default=3)
    parser.add_argument("--max-samples", type=int, default=20000)
    args = parser.parse_args()
    if not (1 <= args.left <= args.channels and 1 <= args.right <= args.channels):
        parser.error("channel indices must be within --channels")
    if args.left == args.right:
        parser.error("--left and --right must differ")

    raw = Path(args.data).read_bytes()
    packet_bytes = struct.unpack_from("<I", raw, OFF_PRT_LEN)[0]
    payload = packet_bytes - HEADER_BYTES
    bytes_per_sample = args.channels * (4 if args.iq_type == "int16" else 8)
    if packet_bytes < HEADER_BYTES or payload % bytes_per_sample or len(raw) % packet_bytes:
        raise SystemExit("invalid float32 NewProtocol packet layout")
    packets = len(raw) // packet_bytes
    samples_per_packet = payload // bytes_per_sample
    stride = max(1, (packets * samples_per_packet) // args.max_samples)
    xy = 0j
    yy = 0.0
    xx = 0.0
    selected = []
    index = 0
    for packet_id in range(packets):
        packet = raw[packet_id * packet_bytes:(packet_id + 1) * packet_bytes]
        for sample in range(samples_per_packet):
            if index % stride == 0:
                x = load_complex(packet, sample, args.left, args.channels, args.iq_type)
                y = load_complex(packet, sample, args.right, args.channels, args.iq_type)
                selected.append((x, y))
                xy += x * y.conjugate()
                xx += abs(x) ** 2
                yy += abs(y) ** 2
            index += 1
    gain = xy / yy if yy else 0j
    residual = sum(abs(x - gain * y) ** 2 for x, y in selected)
    corr = abs(xy) / ((xx * yy) ** 0.5) if xx and yy else 0.0
    rms = (residual / xx) ** 0.5 if xx else float("nan")
    print("samples,channels,left,right,estimated_gain_real,estimated_gain_imag,corr_abs,normalized_residual_rms")
    print(f"{len(selected)},{args.channels},{args.left},{args.right},{gain.real:.12g},{gain.imag:.12g},{corr:.12g},{rms:.12g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
