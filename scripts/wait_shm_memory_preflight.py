#!/usr/bin/env python3
"""Wait until a shmdemo run has memory for its derived scan buffers and ring."""

from __future__ import annotations

import argparse
import struct
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def mem_available_bytes() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable is absent from /proc/meminfo")


def xml_int(root: ET.Element, name: str, default: int = 0) -> int:
    node = root.find(f".//{name}")
    return int(node.text.strip()) if node is not None and node.text else default


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--ring-bytes", required=True, type=int)
    parser.add_argument("--reserve-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--stable-seconds", type=int, default=30)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    with args.input.open("rb") as stream:
        header = stream.read(256)
    if len(header) != 256:
        raise RuntimeError("input is shorter than the 256-byte new-protocol header")
    # NewProtocolLayout::kOffPrtLen is byte 9, little endian.
    prt_bytes = struct.unpack_from("<I", header, 9)[0]
    root = ET.parse(args.config).getroot()
    pulses = xml_int(root, "pulse_num")
    scan_beams = xml_int(root, "shm_scan_beam_count")
    if scan_beams <= 0:
        first = xml_int(root, "new_protocol_file_first_beam", 1)
        scan_beams = xml_int(root, "wavepos_ed") - first + 1
    if prt_bytes <= 256 or pulses <= 0 or scan_beams <= 0:
        raise RuntimeError("cannot derive shared-memory complete-scan size")

    scan_bytes = prt_bytes * pulses * scan_beams
    double_buffer_bytes = 2 * scan_bytes
    required = double_buffer_bytes + args.ring_bytes + args.reserve_bytes
    deadline = time.monotonic() + args.timeout_seconds
    available = mem_available_bytes()
    stable_since: float | None = None
    stable_elapsed = 0.0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if available >= required:
            if stable_since is None:
                stable_since = now
            stable_elapsed = now - stable_since
            if stable_elapsed >= args.stable_seconds:
                break
        else:
            stable_since = None
            stable_elapsed = 0.0
        time.sleep(1.0)
        available = mem_available_bytes()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join((
            f"prt_bytes={prt_bytes}",
            f"pulses_per_beam={pulses}",
            f"scan_beam_count={scan_beams}",
            f"scan_bytes={scan_bytes}",
            f"scan_double_buffer_bytes={double_buffer_bytes}",
            f"shmdemo_ring_bytes={args.ring_bytes}",
            f"reserve_bytes={args.reserve_bytes}",
            f"required_available_bytes={required}",
            f"actual_available_bytes={available}",
            f"required_stable_seconds={args.stable_seconds}",
            f"actual_stable_seconds={stable_elapsed:.3f}",
        )) + "\n",
        encoding="utf-8")
    if available < required or stable_elapsed < args.stable_seconds:
        print("memory preflight timeout: "
              f"need={required} available={available} "
              f"stable={stable_elapsed:.3f}/{args.stable_seconds}")
        return 1
    print("memory preflight pass: "
          f"need={required} available={available} "
          f"stable={stable_elapsed:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
