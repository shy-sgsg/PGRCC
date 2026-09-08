#!/usr/bin/env python3
"""Audit the formal four-channel Stage2 file and create compact truth products."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import struct


HEADER_BYTES = 256
MAGIC_HEAD = 0x5A5A5A5A5A5A5A5A
MAGIC_TAIL = 0x5B5B5B5B5B5B5B5B


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as src:
        while chunk := src.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as dst:
        writer = csv.DictWriter(dst, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--truth", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    radar = scenario["waveform"]
    scan = scenario["scan"]
    random_cfg = scenario["random"]
    channels = int(radar["new_protocol_channel_count"])
    samples = int(radar["pulse_len"])
    pulses = int(radar["pulse_num"])
    beams = int(scan["beam_count"])
    periods = int(random_cfg["period_count"])
    packet_bytes = HEADER_BYTES + samples * channels * 2 * 4
    expected_prts = periods * beams * pulses
    expected_bytes = packet_bytes * expected_prts
    actual_bytes = args.input.stat().st_size
    if actual_bytes != expected_bytes:
        raise SystemExit(f"size mismatch: actual={actual_bytes}, expected={expected_bytes}")

    errors: list[str] = []
    first_header = None
    last_header = None
    previous_utc = None
    utc_step_min = math.inf
    utc_step_max = -math.inf
    previous_pos = None
    pos_change_indices: list[int] = []
    pos_change_utcs: list[float] = []
    theta_counts: dict[str, int] = {}
    header_platform_rows: list[dict] = []
    with args.input.open("rb", buffering=8 * 1024 * 1024) as src:
        for index in range(expected_prts):
            header = src.read(HEADER_BYTES)
            if len(header) != HEADER_BYTES:
                errors.append(f"short header at PRT {index}")
                break
            head, = struct.unpack_from("<Q", header, 0)
            version = header[8]
            prt_len, = struct.unpack_from("<I", header, 9)
            utc, = struct.unpack_from("<f", header, 16)
            counter, = struct.unpack_from("<I", header, 20)
            theta_x100, = struct.unpack_from("<h", header, 218)
            tail, = struct.unpack_from("<Q", header, 248)
            if head != MAGIC_HEAD or tail != MAGIC_TAIL:
                errors.append(f"magic mismatch at PRT {index}")
            if version != 9 or prt_len != packet_bytes:
                errors.append(f"layout mismatch at PRT {index}: version={version}, len={prt_len}")
            if counter != index or header[208] != (counter & 0xFF):
                errors.append(f"counter mismatch at PRT {index}: {counter}")
            if previous_utc is not None:
                step = utc - previous_utc
                utc_step_min = min(utc_step_min, step)
                utc_step_max = max(utc_step_max, step)
                if step <= 0.0:
                    errors.append(f"non-increasing UTC at PRT {index}")
            previous_utc = utc
            lat, lon, height = struct.unpack_from("<ddd", header, 104)
            position = (lat, lon, height)
            if previous_pos is not None and position != previous_pos:
                pos_change_indices.append(index)
                pos_change_utcs.append(utc)
            previous_pos = position
            theta_key = f"{theta_x100 / 100.0:.2f}"
            theta_counts[theta_key] = theta_counts.get(theta_key, 0) + 1
            decoded = {"index": index, "counter": counter, "utc": utc,
                       "theta_deg": theta_x100 / 100.0}
            if index % (beams * pulses) == 0:
                vn, ve, vd = struct.unpack_from("<fff", header, 128)
                header_platform_rows.append({
                    "period_id": index // (beams * pulses), "utc": utc,
                    "lat": lat, "lon": lon, "height_m": height,
                    "vn_mps": vn, "ve_mps": ve, "vd_mps": vd})
            first_header = first_header or decoded
            last_header = decoded
            src.seek(packet_bytes - HEADER_BYTES, 1)
            if len(errors) >= 20:
                break

    # The generated navigation solution is deliberately sampled at 25 Hz and
    # held between updates.  Verify that its packet-header positions change at
    # the expected number of PRIs; this catches an accidental per-PRT POS
    # write or an uninitialised/stationary POS stream before GMTI consumes it.
    prf_hz = float(radar.get("prf_hz", 0.0))
    expected_prts_per_pos = prf_hz / 25.0 if prf_hz > 0.0 else 0.0
    pos_update_intervals = [
        right - left for left, right in zip(pos_change_indices, pos_change_indices[1:])
    ]
    if expected_prts_per_pos > 0.0 and pos_update_intervals:
        min_allowed = max(1, math.floor(expected_prts_per_pos) - 1)
        max_allowed = math.ceil(expected_prts_per_pos) + 1
        bad_intervals = [interval for interval in pos_update_intervals
                         if interval < min_allowed or interval > max_allowed]
        if bad_intervals:
            errors.append(
                "POS update interval mismatch: "
                f"expected about {expected_prts_per_pos:.3f} PRT, "
                f"observed range {min(pos_update_intervals)}..{max(pos_update_intervals)}")
    elif expected_prts_per_pos > 0.0:
        errors.append("no 25 Hz POS position update observed")

    # Deterministic channel audit: 25 PRTs spread over the complete file and
    # 64 complex samples spread over each selected PRT.
    channel_energy = [0.0] * channels
    pair_diff_energy = {(1, 3): 0.0, (2, 4): 0.0}
    pair_ref_energy = {(1, 3): 0.0, (2, 4): 0.0}
    nonfinite = 0
    sample_count = 0
    prt_indices = sorted({round(i * (expected_prts - 1) / 24) for i in range(25)})
    sample_indices = sorted({round(i * (samples - 1) / 63) for i in range(64)})
    sample_stride = channels * 2 * 4
    with args.input.open("rb") as src:
        for prt_index in prt_indices:
            packet_base = prt_index * packet_bytes + HEADER_BYTES
            for sample_index in sample_indices:
                src.seek(packet_base + sample_index * sample_stride)
                raw = src.read(sample_stride)
                values = []
                for ch in range(channels):
                    i_val, q_val = struct.unpack_from("<ff", raw, ch * 8)
                    if not math.isfinite(i_val) or not math.isfinite(q_val):
                        nonfinite += 1
                    z = complex(i_val, q_val)
                    values.append(z)
                    channel_energy[ch] += abs(z) ** 2
                for pair in pair_diff_energy:
                    a, b = pair
                    pair_diff_energy[pair] += abs(values[a - 1] - values[b - 1]) ** 2
                    pair_ref_energy[pair] += abs(values[a - 1]) ** 2
                sample_count += 1

    moving_truth = args.truth / "moving_target_truth.csv"
    target_rows: list[dict] = []
    beam_rows: list[dict] = []
    target_seen: set[tuple[str, str]] = set()
    beam_seen: set[tuple[str, str]] = set()
    with moving_truth.open(newline="", encoding="utf-8") as src:
        for row in csv.DictReader(src):
            period = row["period_id"]
            target_key = (period, row["target_id"])
            if target_key not in target_seen:
                target_seen.add(target_key)
                target_rows.append({key: row[key] for key in (
                    "period_id", "target_id", "e", "n", "lat", "lon", "range_m",
                    "expected_bin", "ve_mps", "vn_mps", "snr_db")})
            beam_key = (period, row["beam_id"])
            if beam_key not in beam_seen and row["pulse_id"] == "0":
                beam_seen.add(beam_key)
                beam_rows.append({"period_id": period, "beam_id": row["beam_id"],
                                  "utc": row["utc"], "theta_cmd_deg": row["theta_cmd_deg"]})

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "truth_targets_by_period.csv", list(target_rows[0]), target_rows)
    write_csv(args.output_dir / "truth_platform.csv",
              ["period_id", "utc", "lat", "lon", "height_m", "vn_mps", "ve_mps", "vd_mps"],
              header_platform_rows)
    write_csv(args.output_dir / "truth_beams.csv",
              ["period_id", "beam_id", "utc", "theta_cmd_deg"], beam_rows)
    (args.output_dir / "simulation_config.json").write_text(
        json.dumps(scenario, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    digest = sha256(args.input)
    report = {
        "pass": not errors and nonfinite == 0,
        "input": str(args.input.resolve()),
        "sha256": digest,
        "actual_bytes": actual_bytes,
        "expected_bytes": expected_bytes,
        "packet_bytes": packet_bytes,
        "expected_prts": expected_prts,
        "periods": periods,
        "beams_per_period": beams,
        "pulses_per_beam": pulses,
        "channels": channels,
        "samples_per_prt": samples,
        "first_header": first_header,
        "last_header": last_header,
        "utc_step_min_s_float32": utc_step_min,
        "utc_step_max_s_float32": utc_step_max,
        "pos_update_hz_expected": 25.0,
        "pos_update_prt_expected": expected_prts_per_pos,
        "pos_update_count": len(pos_change_indices),
        "pos_update_interval_prt_min": min(pos_update_intervals) if pos_update_intervals else None,
        "pos_update_interval_prt_max": max(pos_update_intervals) if pos_update_intervals else None,
        "pos_update_first_utc": pos_change_utcs[0] if pos_change_utcs else None,
        "theta_counts": theta_counts,
        "sampled_complex_values_per_channel": sample_count,
        "channel_rms": [math.sqrt(value / sample_count) for value in channel_energy],
        "normalized_pair_difference": {
            f"ch{a}_ch{b}": math.sqrt(pair_diff_energy[(a, b)] /
                                      max(pair_ref_energy[(a, b)], 1e-30))
            for a, b in pair_diff_energy
        },
        "nonfinite_components": nonfinite,
        "truth_target_period_rows": len(target_rows),
        "truth_platform_rows": len(header_platform_rows),
        "truth_beam_rows": len(beam_rows),
        "errors": errors,
    }
    (args.output_dir / "data_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
