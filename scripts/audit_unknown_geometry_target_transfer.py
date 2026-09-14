#!/usr/bin/env python3
"""Audit target transfer through the production B0/B1/B1K path.

This is a read-only evaluator for a completed end-to-end run.  It consumes
the paired OFF=C+N, ON=S+C+N, and target-only=S raw inputs plus the CSI tap
from the same production run.  The causal target response is defined as
``DeltaY = ON - OFF`` at a common target ROI; no raw detection list is used
to redefine protocol targets.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import struct
import sys
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
ANALYZER_PATH = ROOT / "scripts/analyze_four_channel_observables.py"


def _load_analyzer():
    import importlib.util

    spec = importlib.util.spec_from_file_location("four_channel_target_transfer", ANALYZER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ANALYZER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OBS = _load_analyzer()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def _write_rows(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not materialized:
        path.write_text("\n", encoding="utf-8")
        return
    fields: list[str] = []
    for row in materialized:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(materialized)


def _float_or_none(value: object) -> Optional[float]:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _read_single_manifest(production_split: Path) -> tuple[Path, dict[str, str]]:
    manifests = sorted(production_split.rglob("csi_roi_manifest.csv"))
    if len(manifests) != 1:
        raise RuntimeError(f"expected one CSI manifest under {production_split}, got {len(manifests)}")
    rows = _read_rows(manifests[0])
    if not rows:
        raise RuntimeError(f"CSI manifest is empty: {manifests[0]}")
    row = rows[0]
    for key in ("channel1_real_path", "channel1_imag_path", "channel2_real_path", "channel2_imag_path", "after_real_path", "after_imag_path", "csi_after_real_path", "csi_after_imag_path"):
        if not row.get(key):
            raise RuntimeError(f"CSI manifest lacks {key}: {manifests[0]}")
    return manifests[0], row


def _complex_array(row: Mapping[str, str], real_key: str, imag_key: str) -> np.ndarray:
    real = np.load(row[real_key]).astype(np.float64, copy=False)
    imag = np.load(row[imag_key]).astype(np.float64, copy=False)
    if real.shape != imag.shape:
        raise RuntimeError(f"complex tap shape mismatch for {real_key}/{imag_key}")
    return real + 1j * imag


def _complex_scalar(value: complex) -> tuple[float, float]:
    return float(value.real), float(value.imag)


def _wrap_phase(value: float) -> float:
    return math.atan2(math.sin(value), math.cos(value))


def _roi(array: np.ndarray, row: int, col: int, half_row: int, half_col: int) -> np.ndarray:
    r0 = max(0, row - half_row)
    r1 = min(array.shape[0], row + half_row + 1)
    c0 = max(0, col - half_col)
    c1 = min(array.shape[1], col + half_col + 1)
    return array[r0:r1, c0:c1]


def _power_stats(value: np.ndarray, row_axis: np.ndarray, range_axis: np.ndarray) -> dict[str, object]:
    power = np.abs(value) ** 2
    if power.size == 0 or not np.any(np.isfinite(power)):
        return {
            "power_sum": None,
            "power_peak": None,
            "peak_row": None,
            "peak_col": None,
            "row_centroid": None,
            "range_centroid_m": None,
            "phase_coherent_sum_rad": None,
        }
    finite = np.isfinite(power)
    safe_power = np.where(finite, power, 0.0)
    peak = np.unravel_index(int(np.argmax(safe_power)), safe_power.shape)
    row_indices = np.arange(value.shape[0], dtype=np.float64)
    col_indices = np.arange(value.shape[1], dtype=np.float64)
    total = float(np.sum(safe_power))
    row_centroid = float(np.sum(safe_power * row_indices[:, None]) / total) if total > 0.0 else None
    range_centroid = None
    if total > 0.0 and range_axis.size >= value.shape[1]:
        range_centroid = float(np.sum(safe_power * range_axis[:value.shape[1]][None, :]) / total)
    coherent = complex(np.sum(value))
    return {
        "power_sum": total,
        "power_peak": float(safe_power[peak]),
        "peak_row": int(peak[0]),
        "peak_col": int(peak[1]),
        "row_centroid": row_centroid,
        "range_centroid_m": range_centroid,
        "phase_coherent_sum_rad": _wrap_phase(float(np.angle(coherent))) if abs(coherent) > 0.0 else None,
    }


def _go_alpha_from_meta(production_split: Path) -> tuple[Optional[float], str]:
    metas = sorted(production_split.rglob("*_meta.txt"))
    for path in metas:
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        alpha = _float_or_none(values.get("cfar_alpha"))
        if alpha is not None:
            return alpha, f"{path}:cfar_alpha"
    # The formal v4 run predates optional dense CFAR diagnostics.  This value
    # was independently evaluated from the exact C++ go_cfar_alpha.hpp
    # implementation for (pf=1e-6, guard=4, background=16).
    return 13.44951031977817, "C++ go_cfar_alpha.hpp evaluated for pf=1e-6,g=4,b=16"


def _go_threshold_at(power: np.ndarray, row: int, col: int, guard: int, background: int, alpha: float, circular: bool) -> tuple[Optional[float], Optional[float], Optional[int]]:
    height, width = power.shape
    outer = max(guard, 0) + max(background, 1)
    if row < 0 or row >= height or col < outer or col + outer >= width:
        return None, None, None

    def rect(row_start: int, row_end: int, col_start: int, col_end: int) -> float:
        if col_start > col_end:
            return 0.0
        rows = np.arange(row_start, row_end + 1, dtype=np.int64)
        if circular:
            rows %= height
        elif rows[0] < 0 or rows[-1] >= height:
            return 0.0
        return float(np.sum(power[rows, col_start:col_end + 1], dtype=np.float64))

    r0, r1 = row - outer, row + outer
    c0, c1 = col - outer, col + outer
    rg0, rg1 = row - guard, row + guard
    cg0, cg1 = col - guard, col + guard
    left = rect(r0, r1, c0, cg0 - 1) / float((r1 - r0 + 1) * background)
    right = rect(r0, r1, cg1 + 1, c1) / float((r1 - r0 + 1) * background)
    top = rect(r0, rg0 - 1, c0, c1) / float(background * (c1 - c0 + 1))
    bottom = rect(rg1 + 1, r1, c0, c1) / float(background * (c1 - c0 + 1))
    background_mean = max(left, right, top, bottom)
    threshold = float(alpha * background_mean)
    return threshold, background_mean, 4 * background * (2 * guard + 1) + 0


def _raw_center_samples(path: Path, pulse_len: int, sample_by_packet: Mapping[int, int]) -> dict[int, np.ndarray]:
    values: dict[int, np.ndarray] = {}
    packets = OBS.iter_raw_packets(path, pulse_len, 4, "float32")
    for packet in packets:
        packet_index = int(packet["packet_index"])
        sample = int(sample_by_packet.get(packet_index, -1))
        if sample < 0 or sample >= pulse_len:
            continue
        values[packet_index] = np.asarray(packet["channels"])[sample, :].astype(np.complex128)
    if len(values) != len(sample_by_packet):
        raise RuntimeError(f"raw target tap packet/sample coverage incomplete for {path}: {len(values)}/{len(sample_by_packet)}")
    return values


def _find_detection_csv(split_root: Path) -> Path:
    candidates = sorted(split_root.rglob("detection_results.csv"))
    if not candidates:
        candidates = sorted(split_root.rglob("detection_results_*.csv"))
    if not candidates:
        raise RuntimeError(f"detection CSV is missing under {split_root}")
    return candidates[0]


def _candidate(rows: Sequence[Mapping[str, str]], truth_col: int, half_col: int) -> Optional[dict[str, str]]:
    selected = []
    for row in rows:
        try:
            col = int(float(row.get("col", "-1")))
            power = float(row.get("power", "nan"))
        except (TypeError, ValueError):
            continue
        if truth_col - half_col <= col <= truth_col + half_col and math.isfinite(power):
            selected.append((power, abs(col - truth_col), row))
    if not selected:
        return None
    selected.sort(key=lambda item: (-item[0], item[1]))
    return dict(selected[0][2])


def _p4_metrics(split_root: Path) -> dict[str, Optional[float]]:
    path = split_root / "p4_eval/detection_metrics.csv"
    rows = _read_rows(path) if path.is_file() else []
    return rows[0] if rows else {}


def _tap_record(
    root: Path,
    velocity_dir: Path,
    condition: str,
    split: str,
    reference_row: int,
    reference_col: int,
    half_row: int,
    half_col: int,
) -> tuple[dict[str, object], dict[str, object]]:
    velocity_tag = velocity_dir.name.split("_", 2)
    if len(velocity_tag) < 2 or not velocity_tag[1].isdigit():
        raise RuntimeError(f"cannot parse velocity index from {velocity_dir.name}")
    production_split = root / "production" / f"velocity_{int(velocity_tag[1]):02d}" / condition / split
    manifest_path, manifest = _read_single_manifest(production_split)
    csi_before = _complex_array(manifest, "channel1_real_path", "channel1_imag_path")
    f2 = _complex_array(manifest, "channel2_real_path", "channel2_imag_path")
    csi_after = _complex_array(manifest, "csi_after_real_path", "csi_after_imag_path")
    detector = _complex_array(manifest, "after_real_path", "after_imag_path")
    if not (csi_before.shape == f2.shape == csi_after.shape == detector.shape):
        raise RuntimeError(f"tap matrix shape mismatch for {manifest_path}")
    row = max(0, min(reference_row, detector.shape[0] - 1))
    col = max(0, min(reference_col, detector.shape[1] - 1))
    half_row = max(0, half_row)
    half_col = max(0, half_col)
    axis_fa = np.load(manifest["fa_axis_path"]).reshape(-1).astype(np.float64)
    axis_range = np.load(manifest["range_axis_path"]).reshape(-1).astype(np.float64)
    detector_roi = _roi(detector, row, col, half_row, half_col)
    csi_roi = _roi(csi_after, row, col, half_row, half_col)
    f1_roi = _roi(csi_before, row, col, half_row, half_col)
    f2_roi = _roi(f2, row, col, half_row, half_col)
    guard = int(float(manifest.get("guard_doppler_bins", 4)))
    background = int(float(manifest.get("background_doppler_bins", 16)))
    alpha, alpha_source = _go_alpha_from_meta(production_split)
    power = np.abs(detector) ** 2
    threshold, bg_mean, bg_count = _go_threshold_at(
        power, row, col, guard, background, float(alpha), True
    ) if alpha is not None else (None, None, None)
    detection_path = _find_detection_csv(production_split)
    detection_rows = _read_rows(detection_path)
    candidate = _candidate(
        detection_rows,
        reference_col,
        int(float(manifest.get("target_half_range_bins", 2))),
    ) if split == "on" else None
    metrics = _p4_metrics(production_split) if split == "on" else {}
    candidate_row = None if candidate is None else int(float(candidate.get("row", "-1")))
    candidate_col = None if candidate is None else int(float(candidate.get("col", "-1")))
    row_result: dict[str, object] = {
        "condition": condition,
        "split": split,
        "tap_manifest": str(manifest_path),
        "beam_id": manifest.get("beam_id"),
        "roi_reference_row": row,
        "roi_reference_col": col,
        "roi_half_doppler_bins": half_row,
        "roi_half_range_bins": half_col,
        "detector_branch": "csi_after" if int(manifest["az_st"]) <= row <= int(manifest["az_ed"]) else "channel2_outside_dynamic_band",
        "csi_before_cut_real": _complex_scalar(csi_before[row, col])[0],
        "csi_before_cut_imag": _complex_scalar(csi_before[row, col])[1],
        "f2_cut_real": _complex_scalar(f2[row, col])[0],
        "f2_cut_imag": _complex_scalar(f2[row, col])[1],
        "csi_after_cut_real": _complex_scalar(csi_after[row, col])[0],
        "csi_after_cut_imag": _complex_scalar(csi_after[row, col])[1],
        "detector_cut_real": _complex_scalar(detector[row, col])[0],
        "detector_cut_imag": _complex_scalar(detector[row, col])[1],
        "csi_before_cut_power": float(abs(csi_before[row, col]) ** 2),
        "f2_cut_power": float(abs(f2[row, col]) ** 2),
        "csi_after_cut_power": float(abs(csi_after[row, col]) ** 2),
        "detector_cut_power": float(abs(detector[row, col]) ** 2),
        "cfar_threshold_computed": threshold,
        "cfar_background_mean_max_direction": bg_mean,
        "cfar_background_cell_count": bg_count,
        "cfar_alpha": alpha,
        "cfar_alpha_source": alpha_source,
        "cfar_margin_db_computed": None if threshold is None or threshold <= 0.0 else 10.0 * math.log10(abs(detector[row, col]) ** 2 / threshold),
        "cfar_margin_db_from_detection": None if candidate is None else _float_or_none(candidate.get("cfar_margin_db")),
        "candidate_detected_by_p4": _float_or_none(metrics.get("recall")) if metrics else None,
        "candidate_row": candidate_row,
        "candidate_col": candidate_col,
        "candidate_power": None if candidate is None else _float_or_none(candidate.get("power")),
        "candidate_radial_velocity_mps": None if candidate is None else _float_or_none(candidate.get("radial_velocity_mps")),
        "candidate_position_e": None if candidate is None else _float_or_none(candidate.get("new_e")),
        "candidate_position_n": None if candidate is None else _float_or_none(candidate.get("new_n")),
        "p4_position_error_m": _float_or_none(metrics.get("mean_position_error_m")) if metrics else None,
        "p4_velocity_error_mps": _float_or_none(metrics.get("mean_velocity_error_mps")) if metrics else None,
        "cfar_clusters": None,
        "cfar_selected": None,
        "csi_after_roi": csi_roi,
        "detector_roi": detector_roi,
        "f1_roi": f1_roi,
        "f2_roi": f2_roi,
        "axis_fa": axis_fa,
        "axis_range": axis_range,
    }
    return row_result, {
        "csi_before": csi_before,
        "f2": f2,
        "csi_after": csi_after,
        "detector": detector,
        "axis_fa": axis_fa,
        "axis_range": axis_range,
        "manifest": manifest,
    }


def _production_summary(root: Path, velocity_index: int, condition: str, split: str) -> Mapping[str, str]:
    path = root / "production_metrics.csv"
    for row in _read_rows(path):
        if row.get("case_id") == f"velocity_{velocity_index:02d}" and row.get("condition_id") == condition and row.get("split") == split:
            return row
    return {}


def _case_dirs(root: Path) -> list[Path]:
    return sorted((root / "cases").glob("velocity_*"))


def _raw_paths(case_dir: Path, condition: str) -> dict[str, Path]:
    source = {
        "B0_Current": case_dir,
        "B1_Blind_Estimated_Current": case_dir / "corrections/blind",
        "B1K_Known_Current": case_dir / "corrections/known",
    }[condition]
    if source == case_dir:
        paths = {}
        for role in ("on", "off", "target_only"):
            manifest = _read_rows(case_dir / role / "data/period_files.csv")
            if len(manifest) != 1:
                raise RuntimeError(f"invalid raw manifest: {case_dir / role}")
            path = Path(manifest[0]["file"])
            if not path.is_absolute():
                path = (case_dir / role / path).resolve()
            paths[role] = path
        return paths
    return {role: source / f"{role}.bin" for role in ("on", "off", "target_only")}


def run_audit(input_root: Path, output_root: Path) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty target audit output: {output_root}")
    production_metrics = input_root / "production_metrics.csv"
    if not production_metrics.is_file():
        raise RuntimeError(f"missing formal production metrics: {production_metrics}")
    output_root.mkdir(parents=True, exist_ok=True)
    tap_rows: list[dict[str, object]] = []
    transfer_rows: list[dict[str, object]] = []
    raw_records: list[dict[str, object]] = []
    conditions = ("B0_Current", "B1_Blind_Estimated_Current", "B1K_Known_Current")
    for velocity_index, case_dir in enumerate(_case_dirs(input_root)):
        truth_rows = _read_rows(case_dir / "on/truth/truth_targets_by_beam.csv")
        if not truth_rows:
            raise RuntimeError(f"target truth is empty: {case_dir}")
        truth = truth_rows[0]
        truth_col = int(float(truth["range_bin"]))
        # Detection rows are in the processed, centered Doppler indexing.  A
        # target-only detector peak is the evaluator's fallback if B0 has no
        # candidate in a future case; current formal cases have an on candidate.
        candidate_rows: dict[str, int] = {}
        candidate_cols: dict[str, int] = {}
        for condition in conditions:
            on_root = input_root / "production" / f"velocity_{velocity_index:02d}" / condition / "on"
            detections = _read_rows(_find_detection_csv(on_root))
            candidate = _candidate(detections, truth_col, 2)
            if candidate is not None:
                candidate_rows[condition] = int(float(candidate["row"]))
                candidate_cols[condition] = int(float(candidate["col"]))
        reference_row = candidate_rows.get("B0_Current")
        reference_col = candidate_cols.get("B0_Current")
        if reference_row is None or reference_col is None:
            raise RuntimeError(f"no B0 target-range candidate for velocity_{velocity_index:02d}")
        tap_data: dict[str, dict[str, object]] = {}
        for condition in conditions:
            condition_taps: dict[str, dict[str, object]] = {}
            for split in ("off", "on", "target_only"):
                record, arrays = _tap_record(
                    input_root,
                    case_dir,
                    condition,
                    split,
                    reference_row,
                    reference_col,
                    2,
                    2,
                )
                summary = _production_summary(input_root, velocity_index, condition, split)
                record.update({
                    "velocity_index": velocity_index,
                    "velocity_case": case_dir.name,
                    "truth_range_bin": truth_col,
                    "truth_row_reported": _float_or_none(truth.get("row_truth")),
                    "cfar_hit_cells": _float_or_none(summary.get("cfar_hit_cells")),
                    "cfar_clusters": _float_or_none(summary.get("cfar_clusters")),
                    "cfar_selected": _float_or_none(summary.get("cfar_selected")),
                    "cfar_empirical_pfa": _float_or_none(summary.get("cfar_pfa")),
                })
                for key in ("csi_after_roi", "detector_roi", "f1_roi", "f2_roi", "axis_fa", "axis_range"):
                    record.pop(key, None)
                tap_rows.append(record)
                condition_taps[split] = {"record": record, "arrays": arrays}
            tap_data[condition] = condition_taps

        # Raw central target sample: ON-OFF and target-only are retained for
        # every corrected condition.  The sample identity comes from truth;
        # no fixed raw sample literal is used.
        pulse_rows = _read_rows(case_dir / "on/truth/truth_pulse.csv")
        sample_by_packet = {
            int(row["beam_id_0based"]) * 130 + int(row["pulse_id"]): int(row["range_sample_int"])
            for row in pulse_rows
        }
        for condition in conditions:
            paths = _raw_paths(case_dir, condition)
            raw_on = _raw_center_samples(paths["on"], 11820, sample_by_packet)
            raw_off = _raw_center_samples(paths["off"], 11820, sample_by_packet)
            raw_to = _raw_center_samples(paths["target_only"], 11820, sample_by_packet)
            delta = np.stack([raw_on[k] - raw_off[k] for k in sorted(sample_by_packet)], axis=0)
            target_only = np.stack([raw_to[k] for k in sorted(sample_by_packet)], axis=0)
            raw_row: dict[str, object] = {
                "velocity_index": velocity_index,
                "velocity_case": case_dir.name,
                "condition": condition,
                "raw_target_sample_definition": "central truth_pulse.range_sample_int; ON-OFF and target_only=S",
                "raw_target_sample_count": int(delta.shape[0]),
                "raw_on_off_sha256": _sha256(paths["on"]),
                "raw_off_sha256": _sha256(paths["off"]),
                "raw_target_only_sha256": _sha256(paths["target_only"]),
            }
            for channel in range(4):
                raw_row[f"raw_delta_ch{channel + 1}_real"] = float(np.mean(delta[:, channel].real))
                raw_row[f"raw_delta_ch{channel + 1}_imag"] = float(np.mean(delta[:, channel].imag))
                raw_row[f"raw_target_only_ch{channel + 1}_real"] = float(np.mean(target_only[:, channel].real))
                raw_row[f"raw_target_only_ch{channel + 1}_imag"] = float(np.mean(target_only[:, channel].imag))
            raw_records.append(raw_row)

        # The same B0 ROI is used for every condition so transfer is not
        # inflated by a condition-specific candidate relocation.
        base = tap_data["B0_Current"]
        base_on = base["on"]["arrays"]
        base_off = base["off"]["arrays"]
        base_detector_delta = base_on["detector"] - base_off["detector"]
        base_csi_delta = base_on["csi_after"] - base_off["csi_after"]
        base_f1_delta = base_on["csi_before"] - base_off["csi_before"]
        base_f2_delta = base_on["f2"] - base_off["f2"]
        base_row = int(reference_row)
        base_col = int(reference_col)
        base_det_roi = _roi(base_detector_delta, base_row, base_col, 2, 2)
        base_csi_roi = _roi(base_csi_delta, base_row, base_col, 2, 2)
        base_f1_roi = _roi(base_f1_delta, base_row, base_col, 2, 2)
        base_f2_roi = _roi(base_f2_delta, base_row, base_col, 2, 2)
        base_axis_fa = base_on["axis_fa"]
        base_axis_range = base_on["axis_range"]
        base_stats = _power_stats(base_det_roi, base_axis_fa[max(0, base_row - 2):base_row + 3], base_axis_range[max(0, base_col - 2):base_col + 3])
        base_den = float(base_stats["power_sum"] or 0.0)
        for condition in conditions:
            on = tap_data[condition]["on"]["arrays"]
            off = tap_data[condition]["off"]["arrays"]
            target_only = tap_data[condition]["target_only"]["arrays"]
            detector_delta = on["detector"] - off["detector"]
            csi_delta = on["csi_after"] - off["csi_after"]
            f1_delta = on["csi_before"] - off["csi_before"]
            f2_delta = on["f2"] - off["f2"]
            det_roi = _roi(detector_delta, base_row, base_col, 2, 2)
            csi_roi = _roi(csi_delta, base_row, base_col, 2, 2)
            f1_roi = _roi(f1_delta, base_row, base_col, 2, 2)
            f2_roi = _roi(f2_delta, base_row, base_col, 2, 2)
            target_roi = _roi(target_only["detector"], base_row, base_col, 2, 2)
            stats = _power_stats(det_roi, base_axis_fa[max(0, base_row - 2):base_row + 3], base_axis_range[max(0, base_col - 2):base_col + 3])
            csi_stats = _power_stats(csi_roi, base_axis_fa[max(0, base_row - 2):base_row + 3], base_axis_range[max(0, base_col - 2):base_col + 3])
            f1_stats = _power_stats(f1_roi, base_axis_fa[max(0, base_row - 2):base_row + 3], base_axis_range[max(0, base_col - 2):base_col + 3])
            f2_stats = _power_stats(f2_roi, base_axis_fa[max(0, base_row - 2):base_row + 3], base_axis_range[max(0, base_col - 2):base_col + 3])
            target_stats = _power_stats(target_roi, base_axis_fa[max(0, base_row - 2):base_row + 3], base_axis_range[max(0, base_col - 2):base_col + 3])
            def transfer(value: object) -> Optional[float]:
                number = _float_or_none(value)
                return None if number is None or number <= 0.0 or base_den <= 0.0 else 10.0 * math.log10(number / base_den)
            def phase_shift(stats_value: object, base_value: object) -> Optional[float]:
                a, b = _float_or_none(stats_value), _float_or_none(base_value)
                return None if a is None or b is None else _wrap_phase(a - b)
            record = tap_data[condition]["on"]["record"]
            transfer_rows.append({
                "velocity_index": velocity_index,
                "velocity_case": case_dir.name,
                "condition": condition,
                "truth_range_bin": truth_col,
                "reference_candidate_row": base_row,
                "reference_candidate_col": base_col,
                "reference_candidate_source": "B0 on detection range-neighbourhood maximum",
                "delta_y_definition": "ON_detector_input - OFF_detector_input",
                "delta_y_csi_definition": "ON_csi_after - OFF_csi_after",
                "causal_transfer_db_detector": transfer(stats["power_sum"]),
                "causal_transfer_db_csi_after": transfer(csi_stats["power_sum"]),
                "causal_transfer_db_f1_pre": transfer(f1_stats["power_sum"]),
                "causal_transfer_db_f2_pre": transfer(f2_stats["power_sum"]),
                "delta_y_detector_power_sum": stats["power_sum"],
                "delta_y_csi_power_sum": csi_stats["power_sum"],
                "delta_y_f1_power_sum": f1_stats["power_sum"],
                "delta_y_f2_power_sum": f2_stats["power_sum"],
                "delta_y_b0_detector_power_sum": base_den,
                "detector_peak_row": stats["peak_row"],
                "detector_peak_col": stats["peak_col"],
                "detector_peak_row_global": None if stats["peak_row"] is None else int(stats["peak_row"]) + max(0, base_row - 2),
                "detector_peak_col_global": None if stats["peak_col"] is None else int(stats["peak_col"]) + max(0, base_col - 2),
                "detector_row_centroid_local": stats["row_centroid"],
                "detector_row_centroid_global": None if stats["row_centroid"] is None else float(stats["row_centroid"]) + max(0, base_row - 2),
                "detector_range_centroid_m": stats["range_centroid_m"],
                "detector_phase_coherent_sum_rad": stats["phase_coherent_sum_rad"],
                "csi_peak_row": csi_stats["peak_row"],
                "csi_peak_col": csi_stats["peak_col"],
                "csi_peak_row_global": None if csi_stats["peak_row"] is None else int(csi_stats["peak_row"]) + max(0, base_row - 2),
                "csi_peak_col_global": None if csi_stats["peak_col"] is None else int(csi_stats["peak_col"]) + max(0, base_col - 2),
                "csi_row_centroid_local": csi_stats["row_centroid"],
                "csi_row_centroid_global": None if csi_stats["row_centroid"] is None else float(csi_stats["row_centroid"]) + max(0, base_row - 2),
                "csi_range_centroid_m": csi_stats["range_centroid_m"],
                "csi_phase_coherent_sum_rad": csi_stats["phase_coherent_sum_rad"],
                "phase_shift_vs_b0_detector_rad": phase_shift(stats["phase_coherent_sum_rad"], base_stats["phase_coherent_sum_rad"]),
                "phase_shift_vs_b0_csi_after_rad": phase_shift(csi_stats["phase_coherent_sum_rad"], _power_stats(base_csi_roi, base_axis_fa[max(0, base_row - 2):base_row + 3], base_axis_range[max(0, base_col - 2):base_col + 3])["phase_coherent_sum_rad"]),
                "target_only_detector_power_sum": target_stats["power_sum"],
                "target_only_detector_peak_row": target_stats["peak_row"],
                "target_only_detector_peak_col": target_stats["peak_col"],
                "detector_branch_at_reference": record.get("detector_branch"),
                "candidate_row_for_condition": record.get("candidate_row"),
                "candidate_col_for_condition": record.get("candidate_col"),
                "candidate_detected_by_p4": record.get("candidate_detected_by_p4"),
                "candidate_radial_velocity_mps": record.get("candidate_radial_velocity_mps"),
                "p4_position_error_m": record.get("p4_position_error_m"),
                "p4_velocity_error_mps": record.get("p4_velocity_error_mps"),
                "cfar_cut_power": record.get("detector_cut_power"),
                "cfar_threshold_computed": record.get("cfar_threshold_computed"),
                "cfar_margin_db_computed": record.get("cfar_margin_db_computed"),
                "cfar_hit_cells": record.get("cfar_hit_cells"),
                "cfar_clusters": record.get("cfar_clusters"),
                "cfar_selected": record.get("cfar_selected"),
            })
    _write_rows(output_root / "target_taps.csv", tap_rows)
    _write_rows(output_root / "target_transfer.csv", transfer_rows)
    _write_rows(output_root / "raw_target_complex.csv", raw_records)
    manifest = {
        "schema": "unknown_system_error_target_transfer_audit_v1",
        "status": "completed",
        "input_root": str(input_root.resolve()),
        "input_manifest_sha256": _sha256(input_root / "manifest.json") if (input_root / "manifest.json").is_file() else None,
        "execution": {"working_directory": str(ROOT), "python": sys.version, "platform": platform.platform()},
        "conditions": list(conditions),
        "paired_protocol": {
            "off": "C+N",
            "on": "S+C+N",
            "target_only": "S",
            "delta_y": "ON-OFF, same condition and same target ROI",
            "causal_transfer": "10log10(sum ROI |DeltaY_candidate|^2 / sum ROI |DeltaY_B0|^2)",
        },
        "roi": {"half_doppler_bins": 2, "half_range_bins": 2, "center": "B0 on detection candidate near truth range bin"},
        "tap_semantics": {
            "csi_before": "production aligned F1",
            "f2": "production aligned F2",
            "csi_after": "actual CSI output before detector branch",
            "detector": "actual detector input, CSI inside configured dynamic band and channel2 outside",
            "raw_target": "central truth_pulse.range_sample_int complex sample; mean over pulses is a compact tap",
        },
        "limitations": [
            "The B0 reference ROI is fixed across conditions; condition-specific candidates are reported but do not redefine the transfer denominator.",
            "CFAR threshold is reconstructed from the exact GO directional-window definition and alpha; formal v4 did not request dense threshold-map diagnostics.",
            "Target-only detector Pfa-like ratios are not Pfa measurements; target-only is a signal transfer control.",
        ],
        "artifacts": {
            "target_taps": str((output_root / "target_taps.csv").resolve()),
            "target_transfer": str((output_root / "target_transfer.csv").resolve()),
            "raw_target_complex": str((output_root / "raw_target_complex.csv").resolve()),
        },
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = run_audit(args.input_root.resolve(), args.output_root.resolve())
    print(json.dumps({"status": manifest["status"], "output_root": str(args.output_root.resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
