#!/usr/bin/env python3
"""Prepare oracle-free TrackManager replay inputs from frozen R14 detections."""
import argparse
import csv
from pathlib import Path

FIELDS = ["period_id", "utc", "valid", "det_id", "det_utc", "e", "n", "lat", "lon", "speed", "direction", "range"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--r14-root", type=Path, required=True,
                    help="R14 evidence root containing production/C2_modea_ON and C3_modea_ON")
    ap.add_argument("--output-root", type=Path, required=True)
    args = ap.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    for method in ("C2", "C3"):
        track_dir = args.r14_root / "production" / f"{method}_modea_ON" / "tracks"
        with (track_dir / "track_frames.csv").open(newline="") as f:
            frames = {int(r["result_id"]): float(r["frame_utc"])
                      for r in csv.DictReader(f)}
        src = track_dir / "track_detections.csv"
        dst = args.output_root / f"{method}_input.csv"
        with src.open(newline="") as fin, dst.open("w", newline="") as fout:
            reader = csv.DictReader(fin)
            writer = csv.DictWriter(fout, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            for row in reader:
                period = int(row["result_id"])
                utc = frames[period]
                writer.writerow({
                    "period_id": period, "utc": f"{utc:.12f}", "valid": 1,
                    "det_id": int(row["det_index"]), "det_utc": row["utc"],
                    "e": row["e"], "n": row["n"], "lat": row["lat"],
                    "lon": row["lon"], "speed": 0,
                    "direction": row["direction"], "range": row["range"],
                })
        print(f"{method}: {dst}")

if __name__ == "__main__":
    main()
