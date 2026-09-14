#!/usr/bin/env python3
"""Re-evaluate the historical formal C+N Pfa with the corrected CUT mask.

Formal v4 did not retain dense CFAR maps, but it did retain the production
detector-input ``after_real/imag`` maps.  This evaluator reconstructs the GO
threshold from those maps, reports the full and dynamic-band denominators, and
keeps the production log count for comparison.  It never changes a threshold
or a detection result.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

import audit_unknown_system_error_pfa as pfa


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FORMAL_ROOT = ROOT / "outputs/unknown_system_error_end_to_end_20260914_formal_v4"
DEFAULT_ALPHA = 13.44951031977817


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _read_metrics(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _resolve(value: str, root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _log_counts(log_path: Path) -> dict[str, int | None]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(
        r"\[CFAR\]\[SUMMARY\].*?hit_cells=(\d+).*?clusters=(\d+)", text
    )
    if not matches:
        return {"logged_hit_cells": None, "logged_clusters": None}
    hit, clusters = matches[-1]
    return {"logged_hit_cells": int(hit), "logged_clusters": int(clusters)}


def _band(log_path: Path, metrics_row: Mapping[str, str]) -> tuple[int, int]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(r"\[CFAR\]\[CSI-BAND\].*?dynamic=\[(\d+),(\d+)\]", text)
    if matches:
        return int(matches[-1][0]), int(matches[-1][1])
    return int(float(metrics_row["csi_az_st"])), int(float(metrics_row["csi_az_ed"]))


def _outliers(
    power: np.ndarray,
    threshold: np.ndarray,
    mask: np.ndarray,
    count: int = 12,
) -> list[dict[str, float | int]]:
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        ratio = power / np.maximum(threshold, np.finfo(np.float64).tiny)
    selected = np.flatnonzero(mask & np.isfinite(ratio))
    if selected.size == 0:
        return []
    selected = selected[np.argsort(ratio.ravel()[selected])[-count:][::-1]]
    result: list[dict[str, float | int]] = []
    for flat in selected:
        row, col = np.unravel_index(int(flat), power.shape)
        result.append({
            "row": int(row),
            "col": int(col),
            "power": float(power[row, col]),
            "threshold": float(threshold[row, col]),
            "power_over_threshold": float(ratio[row, col]),
        })
    return result


def audit(formal_root: Path, output_dir: Path) -> dict[str, object]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = formal_root / "production_metrics.csv"
    metrics_rows = [
        row for row in _read_metrics(metrics_path)
        if row.get("condition_id") == "B0_Current" and row.get("split") == "off"
    ]
    if not metrics_rows:
        raise RuntimeError(f"no B0 off rows in {metrics_path}")

    summary_rows: list[dict[str, object]] = []
    outlier_records: dict[str, object] = {}
    for metrics_row in metrics_rows:
        production_root = Path(metrics_row["production_root"])
        period_root = production_root / "stage2/algorithm_result/period_0000"
        manifest_path = next(period_root.rglob("csi_roi_manifest.csv"))
        with manifest_path.open(newline="", encoding="utf-8") as stream:
            manifest_row = next(csv.DictReader(stream))
        real_path = _resolve(manifest_row["after_real_path"], formal_root)
        imag_path = _resolve(manifest_row["after_imag_path"], formal_root)
        real = np.load(real_path)
        imag = np.load(imag_path)
        power = np.asarray(real * real + imag * imag, dtype=np.float64)
        if power.ndim != 2:
            raise RuntimeError(f"detector map is not 2-D: {real_path}")
        runtime_path = next(period_root.rglob("runtime_config_dump.json"))
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        channel = runtime.get("channel_and_gmti", {})
        detection = runtime.get("detection", {})
        if not isinstance(channel, Mapping) or not isinstance(detection, Mapping):
            raise RuntimeError(f"runtime config sections are missing: {runtime_path}")
        guard = int(float(detection.get("cfar_guard_cells", 4)))
        background = int(float(detection.get("cfar_background_cells", 16)))
        circular = bool(channel.get("cfar_doppler_circular", True))
        az_st, az_ed = _band(production_root / "gmticore.log", metrics_row)
        masks = pfa._valid_masks(
            power.shape[0], power.shape[1], guard, background, circular, az_st, az_ed
        )
        left, right, top, bottom = pfa._directional_go_means(power, guard, background)
        go_max = np.maximum.reduce([left, right, top, bottom])
        threshold = DEFAULT_ALPHA * go_max
        hit_map = power > threshold
        log = _log_counts(production_root / "gmticore.log")
        map_stats = pfa._map_stats(
            hit_map.astype(np.float32), power, threshold, masks,
            DEFAULT_ALPHA, guard, background,
        )
        case_id = metrics_row.get("case_id", production_root.name)
        outlier_records[case_id] = {
            "source_detector_real": str(real_path),
            "source_detector_imag": str(imag_path),
            "full_top_outliers": _outliers(power, threshold, masks["full"]),
            "dynamic_top_outliers": _outliers(power, threshold, masks["dynamic"]),
        }
        full = map_stats["full"]
        dynamic = map_stats["dynamic"]
        if not isinstance(full, Mapping) or not isinstance(dynamic, Mapping):
            raise RuntimeError("invalid map statistics")
        summary_rows.append({
            "case_id": case_id,
            "velocity_ve_mps": metrics_row.get("ve_mps"),
            "velocity_vn_mps": metrics_row.get("vn_mps"),
            "production_root": str(production_root),
            "raw_path": metrics_row.get("raw_path"),
            "raw_sha256": metrics_row.get("raw_sha256"),
            "logged_hit_cells": log["logged_hit_cells"],
            "logged_clusters": log["logged_clusters"],
            "reconstructed_full_hit_cells": full["hit_cells_from_map"],
            "reconstructed_dynamic_hit_cells": dynamic["hit_cells_from_map"],
            "full_valid_CUTs": full["valid_cut_cells"],
            "dynamic_band_valid_CUTs": dynamic["valid_cut_cells"],
            "reconstructed_full_pfa": full["empirical_pfa"],
            "reconstructed_dynamic_band_pfa": dynamic["empirical_pfa"],
            "historical_reported_test_cells": metrics_row.get("cfar_test_cells"),
            "historical_reported_pfa": metrics_row.get("cfar_pfa"),
            "configured_pfa": metrics_row.get("cfar_configured_pfa"),
            "cfar_alpha": DEFAULT_ALPHA,
            "cfar_guard_cells": guard,
            "cfar_background_cells": background,
            "doppler_circular": circular,
            "dynamic_band_az_st": az_st,
            "dynamic_band_az_ed": az_ed,
            "power_quantiles_full": json.dumps(map_stats["full"]["power_quantiles"]),
            "threshold_quantiles_full": json.dumps(map_stats["full"]["threshold_quantiles"]),
            "go_max_to_min_ratio_quantiles": json.dumps(
                map_stats["go_training"]["go_max_to_min_ratio_quantiles"]
            ),
            "reconstruction_hit_count_delta_vs_log": (
                full["hit_cells_from_map"] - log["logged_hit_cells"]
                if log["logged_hit_cells"] is not None else None
            ),
        })

    fields = list(summary_rows[0])
    with (output_dir / "formal_pfa_reference.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary_rows)
    _write_json(output_dir / "top_outliers.json", outlier_records)
    manifest = {
        "schema": "unknown_system_error_pfa_formal_reference_v1",
        "formal_root": str(formal_root),
        "formal_production_metrics": str(metrics_path),
        "formal_production_metrics_sha256": _sha256(metrics_path),
        "source_commit": "recorded_in_formal_manifest",
        "evaluator_only": True,
        "threshold_tuning": False,
        "alpha_source": "fixed exact value evaluated from include/go_cfar_alpha.hpp for pf=1e-6,g=4,b=16",
        "denominator_conclusion": "historical dynamic denominator mixed all-row hits with dynamic-band CUT count; use reconstructed full valid CUTs for full-map Pfa and dynamic subset hits for dynamic-band Pfa",
        "artifacts": {
            "summary": str(output_dir / "formal_pfa_reference.csv"),
            "top_outliers": str(output_dir / "top_outliers.json"),
        },
        "rows": len(summary_rows),
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-root", type=Path, default=DEFAULT_FORMAL_ROOT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "outputs/unknown_system_error_pfa_formal_reference_20260914_v1",
    )
    args = parser.parse_args(argv)
    manifest = audit(args.formal_root.resolve(), args.output_dir.resolve())
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
