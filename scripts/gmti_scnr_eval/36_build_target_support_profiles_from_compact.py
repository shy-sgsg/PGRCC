#!/usr/bin/env python3
"""Aggregate calibration-only target support shapes from formal run roots.

The input files are the compact ``target_support_measurements.csv`` written by
``16_run_standard_output_snr_mc.py`` before its S-only/C+N-only maps are
removed.  This script never opens raw BIN, power maps, S+N hit masks, or
evaluation hit rates.  It therefore cannot tune the target model to its own
evaluation result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
import sys

sys.path.insert(0, str(SCRIPT_DIR))
from scnr_eval_lib import read_csv, write_csv  # noqa: E402


def fnum(value: object, default: float = float("nan")) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compact_files(run_root: Path) -> list[Path]:
    """Return only the per-angle compact calibration tables."""
    return sorted(path for path in run_root.glob("angle_*/target_support_measurements.csv")
                  if path.is_file())


def aggregate(rows: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    materialized = list(rows)
    groups = sorted({str(row.get("angle_group", "")) for row in materialized if row.get("angle_group")})
    profiles: list[dict[str, object]] = []
    for group in groups:
        all_rows = [row for row in materialized if str(row.get("angle_group")) == group]
        qualified = [row for row in all_rows if fnum(row.get("support_scnr_db")) >= 1.0]
        # A low-SNR calibration realization cannot identify a footprint after
        # subtracting the local S-only floor.  If a group has no qualified
        # rows, keep all rows but record that fallback explicitly.
        selected = qualified or all_rows
        kappa = np.asarray([[max(fnum(row.get(f"kappa_{index:02d}"), 0.0), 0.0)
                             for index in range(15)] for row in selected], dtype=float)
        beta = np.asarray([[max(fnum(row.get(f"beta_{index:02d}"), 0.0), 0.0)
                            for index in range(15)] for row in selected], dtype=float)
        kappa_median = np.nanmedian(kappa, axis=0)
        beta_median = np.nanmedian(beta, axis=0)
        kappa_median = np.maximum(np.nan_to_num(kappa_median, nan=0.0), 0.0)
        beta_median = np.maximum(np.nan_to_num(beta_median, nan=1.0), 1e-6)
        kappa_median /= max(float(np.sum(kappa_median)), 1e-30)
        beta_median /= max(float(np.median(beta_median)), 1e-30)
        first = all_rows[0]
        profiles.append({
            "angle_group": group,
            "angle_deg": fnum(first.get("angle_deg")),
            "position": str(first.get("position", "center")),
            "sample_count": len(all_rows),
            "qualified_scnr_db_median": float(np.nanmedian(
                [fnum(row.get("support_scnr_db")) for row in selected])),
            "qualified_count_cut_gamma_ref_ge_1": len(qualified),
            "selection_rule": "support_scnr_db>=1dB; fallback=all rows if none",
            "profile_source": "compact S-only/C+N-only calibration; evaluation hits unused",
            **{f"kappa_{index:02d}": float(kappa_median[index]) for index in range(15)},
            **{f"beta_{index:02d}": float(beta_median[index]) for index in range(15)},
        })
    return profiles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, action="append", required=True,
                        help="独立 seed 的 16_run_standard_output_snr_mc 输出根目录；可重复")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_files: list[Path] = []
    rows: list[dict[str, object]] = []
    for run_root_arg in args.run_root:
        run_root = run_root_arg.resolve()
        if not run_root.is_dir():
            raise RuntimeError(f"run root 不存在：{run_root}")
        files = compact_files(run_root)
        if not files:
            raise RuntimeError(f"run root 没有 compact target support：{run_root}")
        for path in files:
            source_files.append(path)
            for row in read_csv(path):
                enriched = dict(row)
                enriched["source_run_root"] = str(run_root)
                enriched["source_compact_file"] = str(path)
                rows.append(enriched)
    if not rows:
        raise RuntimeError("compact target support 表为空")
    profiles = aggregate(rows)
    if not profiles:
        raise RuntimeError("无法从 compact target support 生成 profile")
    write_csv(output / "target_support_measurements.csv", rows)
    write_csv(output / "target_support_profiles.csv", profiles)
    manifest = {
        "status": "pass",
        "script": str(Path(__file__).resolve()),
        "output_dir": str(output),
        "source_run_roots": [str(path.resolve()) for path in args.run_root],
        "source_compact_files": [
            {"path": str(path), "sha256": sha256(path), "size_bytes": path.stat().st_size}
            for path in source_files
        ],
        "measurement_rows": len(rows),
        "profile_rows": len(profiles),
        "groups": [str(row["angle_group"]) for row in profiles],
        "qualified_cutoff_db": 1.0,
        "evaluation_hit_rates_used_for_fit": False,
        "raw_bin_opened": False,
        "maps_opened": False,
        "profile_definition": "median kappa/beta over compact calibration rows; kappa normalized to sum 1, beta normalized to median 1",
    }
    (output / "target_support_profile_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "pass", "output_dir": str(output),
                      "measurement_rows": len(rows), "profile_rows": len(profiles),
                      "groups": manifest["groups"], "raw_bin_opened": False,
                      "evaluation_hit_rates_used_for_fit": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
