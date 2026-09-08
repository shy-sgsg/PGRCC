#!/usr/bin/env python3
"""Parse GMTIxx_track.bin current-track packets into CSV.

The current track packet writer stores repeated 28-byte records without a
target-count header:
  uint16 id
  int32 lon_quantized, LSB = 8.38191e-8 deg
  int32 lat_quantized, LSB = 8.38191e-8 deg
  uint16 speed, scale = 0.01 m/s
  float64 direction_deg
  float64 range_m

An empty file is a valid "no confirmed output tracks" packet.
"""

import argparse
import csv
import json
import os
import struct
import sys

LSB_DEG = 8.38191e-8
REC_BYTES = 28

FIELDS = [
    "case_id", "run_id", "result_id", "track_id", "lat", "lon",
    "speed", "direction", "range", "source_file",
]


def read_runtime_context(path):
    context = {"case_id": "", "run_id": "", "result_id": ""}
    if not path:
        return context
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    run_info = data.get("run_info", data)
    context["case_id"] = str(run_info.get("case_id", ""))
    context["run_id"] = str(run_info.get("run_id", ""))
    context["result_id"] = str(run_info.get("result_id", ""))
    return context


def infer_result_id(path):
    name = os.path.basename(path)
    stem = os.path.splitext(name)[0]
    if stem.endswith("_track"):
        stem = stem[:-6]
    return stem if stem.startswith("GMTI") else ""


def parse_track_bin(path):
    with open(path, "rb") as f:
        data = f.read()
    if not data:
        return []
    if len(data) % REC_BYTES != 0:
        raise ValueError(
            f"unsupported track binary size: {len(data)} is not a multiple of {REC_BYTES}"
        )
    rows = []
    for idx, off in enumerate(range(0, len(data), REC_BYTES)):
        track_id, lon_q, lat_q, speed_q, direction, range_m = struct.unpack_from(
            "<HiiHdd", data, off
        )
        rows.append({
            "track_id": track_id,
            "lat": lat_q * LSB_DEG,
            "lon": lon_q * LSB_DEG,
            "speed": speed_q * 0.01,
            "direction": direction,
            "range": range_m,
            "source_file": path,
        })
    return rows


def write_csv(path, rows, context, source_file):
    result_id = context.get("result_id") or infer_result_id(source_file)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            out = {k: "" for k in FIELDS}
            out.update({
                "case_id": context.get("case_id", ""),
                "run_id": context.get("run_id", ""),
                "result_id": result_id,
            })
            out.update(row)
            out["source_file"] = source_file
            writer.writerow(out)


def main():
    parser = argparse.ArgumentParser(description="Parse GMTIxx_track.bin into CSV.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default="", help="Optional runtime_config_dump.json")
    parser.add_argument(
        "--source-label", default="",
        help="Optional logical source name written to source_file instead of the input path",
    )
    args = parser.parse_args()
    try:
        rows = parse_track_bin(args.input)
        context = read_runtime_context(args.config)
        write_csv(args.output, rows, context, args.source_label or args.input)
    except Exception as exc:
        print(f"[parse_gmti_track_bin][ERR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
