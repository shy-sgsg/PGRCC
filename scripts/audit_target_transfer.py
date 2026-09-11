#!/usr/bin/env python3
"""Audit target transfer with one frozen OFF-derived correction per scene.

This is a targeted CUDA audit, not a replacement V2 formal matrix. For every
scene it generates the exact paired roles OFF=C+N, ON=S+C+N, and TO=S-only.
J5/J6 parameters are estimated from OFF only, frozen, and replayed on all
three roles. An ON-derived parameter estimate is retained as a deployment
diagnostic and is never used by the primary target-transfer metrics.

Only compact ROI complex statistics, paired detection rows, CFAR summaries,
manifests, and resource snapshots survive scene cleanup.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_joint_physics_formal_matrix as formal  # noqa: E402
import run_joint_physics_calibration as calibration  # noqa: E402
import run_joint_physics_selective_v2 as selective_v2  # noqa: E402
from evaluate_causal_detection import (  # noqa: E402
    detections_in_roi,
    load_manifest_row,
    target_centers,
)
from run_mechanism_aware_oracle import (  # noqa: E402
    patch_xml,
    run_logged,
    single_manifest,
    summary_metrics,
)
from experiment_provenance import git_provenance  # noqa: E402


FAMILIES = ("M1", "M2", "M1+M2", "M1+M3", "M2+M3", "M1+M2+M3")
METHODS = ("J0_Current", "J5_Selective_Physics_Calibration",
           "J6_Joint_Phase_Surface")
DEFAULT_SNR_SCREEN = (-2.0, 2.0)
DEFAULT_SNR_AUDIT = (-2.0, 0.0, 2.0, 4.0)
SEED_NAMESPACE = {
    "snr-screen": 2026110000,
    "audit": 2026111000,
}
EPS = 1.0e-12


def finite(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.floating, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def resource_snapshot() -> dict[str, str]:
    def run(command: list[str]) -> str:
        result = subprocess.run(command, cwd=ROOT, text=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, check=False)
        return result.stdout.strip()
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "nvidia_smi": run(["nvidia-smi"]),
        "free_h": run(["free", "-h"]),
        "df_h_workspace": run(["df", "-h", str(ROOT)]),
    }


def parse_snr_values(text: str | None, default: tuple[float, ...]) -> tuple[float, ...]:
    if not text:
        return default
    values = tuple(float(item.strip()) for item in text.split(",") if item.strip())
    if not values:
        raise ValueError("snr-values must contain at least one number")
    return values


def design_specs(mode: str, snr_values: tuple[float, ...]) -> list[dict[str, Any]]:
    if mode == "snr-screen":
        if len(snr_values) != 2:
            raise ValueError("snr-screen requires exactly two SNR values")
        count = 12
        split = "target_transfer_snr_screen"
    elif mode == "audit":
        if len(snr_values) != 4:
            raise ValueError("audit requires exactly four SNR values")
        count = 24
        split = "target_transfer"
    else:
        raise ValueError(f"unknown mode: {mode}")
    seed_base = SEED_NAMESPACE[mode]
    specs: list[dict[str, Any]] = []
    for index in range(count):
        family = FAMILIES[index % len(FAMILIES)]
        level = index // len(FAMILIES)
        snr_db = float(snr_values[level % len(snr_values)])
        mechanisms = set(family.split("+"))
        look_angle = (-20.0, -10.0, 0.0, 10.0, 20.0)[index % 5]
        desired_radial = (-18.0, -8.0, 0.0, 8.0, 18.0)[index % 5]
        tangent = (-12.0, -4.0, 5.0, 11.0)[level % 4]
        theta = math.radians(-60.0 + look_angle)
        look = np.asarray([-math.sin(theta), -math.cos(theta)])
        orthogonal = np.asarray([-look[1], look[0]])
        velocity = desired_radial * look + tangent * orthogonal
        spec: dict[str, Any] = {
            "scene_id": f"{split}_{index:03d}",
            "split": split,
            "label": family,
            "seed": seed_base + index,
            "squint_deg": float((-0.25, 0.0, 0.25)[index % 3]),
            "scan_min_deg": look_angle,
            "look_angle_deg": -60.0 + look_angle,
            "range_bin": int(350 + (index * 137) % 1250),
            "snr_db": snr_db,
            "ve_mps": float(velocity[0]),
            "vn_mps": float(velocity[1]),
            "texture_sigma": float(0.25 + 0.10 * (index % 4)),
            "rho": float(0.52 + 0.08 * (index % 4))
            if "M3" in mechanisms else float(0.82 + 0.04 * (index % 4)),
            "delay_ns": float(6.0 + 1.2 * (index % 4))
            if "M1" in mechanisms else 0.0,
            "phase_deg": float(0.35 + 0.12 * (index % 4))
            if "M2" in mechanisms else 0.0,
            "multi_target": False,
            "near_clutter_ridge": bool(index % 5 == 0),
            "high_speed_target": bool(abs(desired_radial) > 15.0),
        }
        specs.append(spec)
    return specs


def resolve_array(manifest: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = manifest.parent / path
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def load_complex_map(manifest: Path, real_key: str, imag_key: str) -> np.ndarray:
    real = np.asarray(np.load(resolve_array(manifest, real_key)), dtype=np.float64)
    imag = np.asarray(np.load(resolve_array(manifest, imag_key)), dtype=np.float64)
    if real.shape != imag.shape:
        raise RuntimeError(f"complex map shape mismatch: {real.shape} vs {imag.shape}")
    return real + 1j * imag


def db_ratio(numerator: float, denominator: float) -> float:
    if not (math.isfinite(numerator) and math.isfinite(denominator)):
        return math.nan
    return 10.0 * math.log10(max(numerator, EPS) / max(denominator, EPS))


def roi_slices(shape: tuple[int, int], center: dict[str, Any],
               half_rows: int, half_cols: int) -> tuple[slice, slice]:
    row, col = int(center["row"]), int(center["col"])
    return (
        slice(max(0, row - half_rows), min(shape[0], row + half_rows + 1)),
        slice(max(0, col - half_cols), min(shape[1], col + half_cols + 1)),
    )


def complex_stats(values: np.ndarray, row0: int, col0: int) -> dict[str, Any]:
    original = np.asarray(values, dtype=np.complex128)
    if original.ndim != 2:
        raise ValueError("complex ROI must be a 2-D array")
    finite_mask = np.isfinite(original.real) & np.isfinite(original.imag)
    if not np.any(finite_mask):
        return {
            "cell_count": 0, "sum_real": math.nan, "sum_imag": math.nan,
            "energy": math.nan, "peak_abs": math.nan, "peak_real": math.nan,
            "peak_imag": math.nan, "peak_row": None, "peak_col": None,
            "centroid_row": math.nan, "centroid_col": math.nan,
            "spread_rms_cells": math.nan,
        }
    values = original[finite_mask]
    weights = np.abs(values) ** 2
    row_grid, col_grid = np.indices(original.shape)
    rows = row_grid[finite_mask].astype(np.float64)
    cols = col_grid[finite_mask].astype(np.float64)
    total = float(np.sum(weights))
    centroid_row = float(np.sum(weights * rows) / max(total, EPS))
    centroid_col = float(np.sum(weights * cols) / max(total, EPS))
    spread = math.sqrt(float(np.sum(weights * (
        (rows - centroid_row) ** 2 + (cols - centroid_col) ** 2
    )) / max(total, EPS)))
    peak_position = np.unravel_index(
        int(np.nanargmax(np.where(finite_mask, np.abs(original) ** 2, -np.inf))),
        original.shape)
    peak_row = row0 + int(peak_position[0])
    peak_col = col0 + int(peak_position[1])
    peak = values[int(np.argmax(weights))]
    return {
        "cell_count": int(values.size),
        "sum_real": float(np.sum(values.real)),
        "sum_imag": float(np.sum(values.imag)),
        "energy": total,
        "peak_abs": float(abs(peak)),
        "peak_real": float(peak.real),
        "peak_imag": float(peak.imag),
        "peak_row": peak_row,
        "peak_col": peak_col,
        "centroid_row": row0 + centroid_row,
        "centroid_col": col0 + centroid_col,
        "spread_rms_cells": spread,
    }


def detection_margin(rows: list[dict[str, str]]) -> float:
    values = [finite(row.get("cfar_margin_db")) for row in rows]
    values = [value for value in values if math.isfinite(value)]
    return max(values) if values else math.nan


def paired_causal_hit(on_hit: bool, off_hit: bool) -> bool:
    return bool(on_hit and not off_hit)


def plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if key != "phase_values"}


def make_frozen_plan(method: str, role_root: Path, gate: dict[str, Any],
                     positive_gate: dict[str, Any], temp_root: Path) -> dict[str, Any]:
    raw, xml, initial = calibration.case_observables(
        role_root, 1300.0, 80.0, include_selective_surface=True)
    decision = calibration.selective_correction_decision(
        method, initial, gate, positive_gate)
    if method == "J6_Joint_Phase_Surface":
        surface = initial["summary"].get("joint_surface", {})
        delay_ns = finite(surface.get("tau_ns"))
        phase_slope = finite(surface.get("beta1_deg_per_pulse"))
        beta2 = finite(surface.get("beta2_deg_per_pulse2"))
        phase_method = "JOINT"
    else:
        delay_ns = finite(initial["summary"]["delay"].get("delta_tau_ns"))
        phase_slope = finite(initial["summary"]["phase_p1"].get(
            "slope_deg_per_pulse"))
        beta2 = math.nan
        phase_method = "P1"
    plan: dict[str, Any] = {
        "method": method,
        "source_role": role_root.name,
        "apply_delay": bool(decision.get("apply_delay")),
        "apply_phase": bool(decision.get("apply_phase")),
        "fallback": bool(decision.get("fallback")),
        "fallback_reason": decision.get("fallback_reason"),
        "branch": decision.get("branch", "D0P0"),
        "state": decision.get("state"),
        "global_veto": decision.get("global_veto", False),
        "delay_state": decision.get("delay_state"),
        "phase_state": decision.get("phase_state"),
        "estimated_delay_ns": delay_ns,
        "estimated_phase_slope_deg_per_pulse": phase_slope,
        "estimated_beta2_deg_per_pulse2": beta2,
        "phase_method": phase_method,
        "raw_coherence": finite(initial["summary"]["coherence"].get("pulse_median")),
        "joint_confidence": finite(initial["summary"].get(
            "joint_surface", {}).get("confidence")),
        "joint_rmse_rad": finite(initial["summary"].get(
            "joint_surface", {}).get("rmse_rad")),
        "truth_used_in_estimator": False,
        "phase_values": None,
    }
    if plan["fallback"] or (not plan["apply_delay"] and not plan["apply_phase"]):
        plan["apply_delay"] = False
        plan["apply_phase"] = False
        return plan

    phase_values: np.ndarray | None = None
    if method == "J5_Selective_Physics_Calibration" and plan["apply_delay"]:
        temp_root.mkdir(parents=True, exist_ok=True)
        delay_path = temp_root / f"{method}_delay.bin"
        calibration.apply_raw_correction(raw, delay_path, xml, delay_ns, None)
        x1, x2 = calibration.load_raw_channels(
            delay_path,
            calibration.xml_int(xml, "pulse_len", 11840),
            calibration.xml_int(xml, "new_protocol_channel_count", 4), 1, 2)
        after_delay = calibration.estimate_observables_from_arrays(
            x1, x2, calibration.xml_frequency_hz(xml, "fs", 60.0e6),
            1300.0, 80.0, include_selective_surface=True)
        after_decision = calibration.selective_correction_decision(
            method, after_delay, gate, positive_gate)
        plan["delay_state_after_delay"] = after_decision.get("delay_state")
        plan["phase_state_after_delay"] = after_decision.get("phase_state")
        plan["apply_phase"] = bool(after_decision.get("phase_active"))
        if plan["apply_phase"]:
            phase_values = calibration._phase_for_method(after_delay, "P1")
        if delay_path.is_file():
            delay_path.unlink()
    elif plan["apply_phase"]:
        phase_values = calibration._phase_for_method(initial, phase_method)
    plan["phase_values"] = phase_values
    return plan


def run_frozen_method(role_root: Path, plan: dict[str, Any],
                      output_root: Path, build_dir: Path) -> dict[str, Any]:
    if plan["fallback"] or (not plan["apply_delay"] and not plan["apply_phase"]):
        manifest = single_manifest(role_root)
        return {
            "status": "alias_current",
            "root": str(role_root.resolve()),
            "manifest": str(manifest.resolve()),
            "metrics": summary_metrics(manifest),
        }
    raw = calibration.raw_input(role_root)
    xml = calibration.xml_path(role_root)
    corrected = output_root / "stage2/data/frozen_corrected_period_0000.bin"
    steps = calibration.apply_raw_correction(
        raw, corrected, xml,
        plan["estimated_delay_ns"] if plan["apply_delay"] else None,
        plan["phase_values"] if plan["apply_phase"] else None,
    )
    result_dir = output_root / "stage2/algorithm_result/period_0000"
    output_xml = output_root / "stage2/config/frozen_calibration_config.xml"
    patch_xml(xml, corrected, result_dir, output_xml)
    log_path = output_root / "gmticore.log"
    rc = run_logged(
        [str(build_dir.resolve() / "GMTI_core"), str(output_xml),
         "--runtime-mode=debug", "--runtime-diagnostics=on"], log_path)
    row: dict[str, Any] = {
        "status": "pass" if rc == 0 else "gmticore_failed",
        "root": str(output_root.resolve()),
        "gmticore_exit_code": rc,
        "correction_steps": steps,
    }
    if rc == 0:
        manifest = single_manifest(output_root)
        row["manifest"] = str(manifest.resolve())
        row["metrics"] = summary_metrics(manifest)
    if corrected.is_file():
        corrected.unlink()
    if rc != 0:
        raise RuntimeError(f"frozen {plan['method']} replay failed for {role_root}: {row}")
    return row


def role_complex_stats(root: Path, center: dict[str, Any],
                       half_rows: int, half_cols: int) -> tuple[dict[str, Any], np.ndarray]:
    manifest_path, manifest = load_manifest_row(root)
    complex_map = load_complex_map(
        manifest_path, manifest["after_real_path"], manifest["after_imag_path"])
    rows, cols = roi_slices(complex_map.shape, center, half_rows, half_cols)
    return complex_stats(complex_map[rows, cols], rows.start, cols.start), complex_map


def transfer_rows_for_method(
    spec: dict[str, Any], method: str, roots: dict[str, Path],
    reference_on_root: Path,
) -> list[dict[str, Any]]:
    on_manifest_path, on_manifest = load_manifest_row(roots["target_on"])
    on_map = load_complex_map(
        on_manifest_path, on_manifest["after_real_path"], on_manifest["after_imag_path"])
    centers = target_centers(reference_on_root, on_manifest_path,
                             on_manifest, on_map.shape)
    half_rows = int(on_manifest.get("target_half_doppler_bins", 2))
    half_cols = int(on_manifest.get("target_half_range_bins", 2))
    maps: dict[str, np.ndarray] = {}
    for role in ("target_off", "target_on", "target_only"):
        manifest_path, manifest = load_manifest_row(roots[role])
        maps[role] = load_complex_map(
            manifest_path, manifest["after_real_path"], manifest["after_imag_path"])
    output: list[dict[str, Any]] = []
    for center in centers:
        row_slice, col_slice = roi_slices(maps["target_on"].shape, center,
                                          half_rows, half_cols)
        off = maps["target_off"][row_slice, col_slice]
        on = maps["target_on"][row_slice, col_slice]
        target_only = maps["target_only"][row_slice, col_slice]
        delta = on - off
        row0, col0 = row_slice.start, col_slice.start
        current = complex_stats(delta, row0, col0)
        target_stats = complex_stats(target_only, row0, col0)
        on_detections = detections_in_roi(
            roots["target_on"], center, half_rows, half_cols)
        off_detections = detections_in_roi(
            roots["target_off"], center, half_rows, half_cols)
        current_on_hit = bool(on_detections)
        current_off_hit = bool(off_detections)
        output.append({
            "split": spec["split"],
            "scene_id": spec["scene_id"],
            "family": spec["label"],
            "snr_db": spec["snr_db"],
            "method": method,
            "target_id": center["target_id"],
            "target_row": center["row"],
            "target_col": center["col"],
            "on_hit": current_on_hit,
            "off_hit": current_off_hit,
                "paired_causal_hit": paired_causal_hit(current_on_hit, current_off_hit),
            "on_cfar_margin_db": detection_margin(on_detections),
            "off_cfar_margin_db": detection_margin(off_detections),
            "delta_cfar_margin_db": (
                detection_margin(on_detections) - detection_margin(off_detections)
                if math.isfinite(detection_margin(on_detections))
                and math.isfinite(detection_margin(off_detections)) else math.nan
            ),
            "delta_complex": current,
            "target_only_fixedcal": target_stats,
            "on_roi_cells": int(on.size),
            "off_roi_cells": int(off.size),
            "target_only_roi_cells": int(target_only.size),
        })
    return output


def enrich_transfer_rows(
    rows: list[dict[str, Any]], current_rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        key = row["target_id"]
        baseline = current_rows[key]
        delta = row["delta_complex"]
        target_only = row["target_only_fixedcal"]
        base_delta = baseline["delta_complex"]
        base_target = baseline["target_only_fixedcal"]
        row = dict(row)
        row["L_causal_dB"] = db_ratio(delta["energy"], base_delta["energy"])
        row["L_target_only_dB"] = db_ratio(
            target_only["energy"], base_target["energy"])
        row["peak_transfer_dB"] = db_ratio(delta["peak_abs"] ** 2,
                                            base_delta["peak_abs"] ** 2)
        row["integrated_roi_energy_candidate"] = delta["energy"]
        row["integrated_roi_energy_current"] = base_delta["energy"]
        row["range_centroid_shift_cells"] = (
            delta["centroid_col"] - base_delta["centroid_col"])
        row["doppler_centroid_shift_cells"] = (
            delta["centroid_row"] - base_delta["centroid_row"])
        row["peak_bin_shift_cells"] = (
            (delta["peak_row"] - base_delta["peak_row"])
            if delta["peak_row"] is not None and base_delta["peak_row"] is not None
            else math.nan,
            (delta["peak_col"] - base_delta["peak_col"])
            if delta["peak_col"] is not None and base_delta["peak_col"] is not None
            else math.nan,
        )
        row["roi_spreading_delta_cells"] = (
            delta["spread_rms_cells"] - base_delta["spread_rms_cells"])
        row["delta_definition"] = "Y_ON_fixedcal - Y_OFF_fixedcal"
        row["target_only_definition"] = "Y_TO_fixedcal with correction frozen from OFF"
        for prefix, stats in (("delta", delta), ("target_only", target_only)):
            for key_name, value in stats.items():
                row[f"{prefix}_{key_name}"] = value
        row.pop("delta_complex", None)
        row.pop("target_only_fixedcal", None)
        output.append(row)
    return output


def deployment_rows(spec: dict[str, Any], method: str,
                    off_plan: dict[str, Any], on_plan: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "estimated_delay_ns", "estimated_phase_slope_deg_per_pulse",
        "estimated_beta2_deg_per_pulse2", "raw_coherence", "joint_confidence",
        "joint_rmse_rad", "branch", "state", "delay_state", "phase_state",
    )
    row: dict[str, Any] = {
        "split": spec["split"], "scene_id": spec["scene_id"],
        "family": spec["label"], "snr_db": spec["snr_db"], "method": method,
        "off_calibration_role": "target_off",
        "on_calibration_role": "target_on",
        "same_correction_primary": True,
    }
    for key in fields:
        row[f"off_{key}"] = off_plan.get(key)
        row[f"on_{key}"] = on_plan.get(key)
        if isinstance(off_plan.get(key), (int, float)) and isinstance(on_plan.get(key), (int, float)):
            row[f"on_minus_off_{key}"] = finite(on_plan.get(key)) - finite(off_plan.get(key))
        else:
            row[f"on_minus_off_{key}"] = (
                "same" if off_plan.get(key) == on_plan.get(key) else "different")
    row["deployment_check_definition"] = (
        "ON-derived estimates are diagnostic only; primary metrics use OFF-derived frozen correction"
    )
    return row


def paired_scene_bootstrap(rows: list[dict[str, Any]], metric: str,
                           iterations: int = 4000) -> dict[str, Any]:
    by_scene: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = finite(row.get(metric))
        if math.isfinite(value):
            by_scene[str(row["scene_id"])].append(value)
    scene_values = np.asarray([np.mean(values) for values in by_scene.values()],
                              dtype=np.float64)
    if scene_values.size == 0:
        return {"metric": metric, "scene_count": 0, "mean": None,
                "bootstrap_ci95": [None, None], "bootstrap_unit": "scene"}
    rng = np.random.default_rng(2026110901 + len(metric))
    draws = np.empty(iterations, dtype=np.float64)
    for index in range(iterations):
        draws[index] = np.mean(rng.choice(scene_values, size=scene_values.size,
                                           replace=True))
    return {
        "metric": metric,
        "scene_count": int(scene_values.size),
        "mean": float(np.mean(scene_values)),
        "bootstrap_ci95": [float(np.quantile(draws, 0.025)),
                           float(np.quantile(draws, 0.975))],
        "bootstrap_unit": "scene",
    }


def run_one_scene(spec: dict[str, Any], base: dict[str, Any], output: Path,
                  build_dir: Path, gate: dict[str, Any],
                  positive_gate: dict[str, Any]) -> tuple[
                      list[dict[str, Any]], list[dict[str, Any]],
                      list[dict[str, Any]], dict[str, Any]]:
    scene = formal.run_scene(spec, base, output, build_dir, True)
    if scene.get("status") != "pass":
        raise RuntimeError(f"scene failed: {spec['scene_id']} {scene}")
    formal.enrich_scene(scene, output, spec)
    scene_root = Path(scene["scene_root"])
    original_roots = {
        role: Path(scene["roles"][role]["root"])
        for role in ("target_off", "target_on", "target_only")
    }
    plans: dict[str, dict[str, Any]] = {}
    deployment: list[dict[str, Any]] = []
    fixed_roots: dict[str, dict[str, Path]] = {
        "J0_Current": dict(original_roots),
        "J5_Selective_Physics_Calibration": {},
        "J6_Joint_Phase_Surface": {},
    }
    plan_records: list[dict[str, Any]] = []
    for method in METHODS[1:]:
        plans[f"{method}_off"] = make_frozen_plan(
            method, original_roots["target_off"], gate, positive_gate,
            scene_root / "_plans")
        plans[f"{method}_on"] = make_frozen_plan(
            method, original_roots["target_on"], gate, positive_gate,
            scene_root / "_plans")
        plan_records.extend([
            {**plan_summary(plans[f"{method}_off"]), "calibration_role": "target_off",
             "split": spec["split"], "scene_id": spec["scene_id"],
             "family": spec["label"], "snr_db": spec["snr_db"]},
            {**plan_summary(plans[f"{method}_on"]), "calibration_role": "target_on",
             "split": spec["split"], "scene_id": spec["scene_id"],
             "family": spec["label"], "snr_db": spec["snr_db"]},
        ])
        deployment.append(deployment_rows(
            spec, method, plans[f"{method}_off"], plans[f"{method}_on"]))
        for role in ("target_off", "target_on", "target_only"):
            fixed_roots[method][role] = Path(run_frozen_method(
                original_roots[role], plans[f"{method}_off"],
                scene_root / "frozen_off" / method / role, build_dir)["root"])
    raw_current_rows = transfer_rows_for_method(
        spec, "J0_Current", fixed_roots["J0_Current"],
        original_roots["target_on"])
    current_rows = {row["target_id"]: row for row in raw_current_rows}
    transfer_rows: list[dict[str, Any]] = []
    for method in METHODS:
        raw_rows = (
            raw_current_rows
            if method == "J0_Current"
            else transfer_rows_for_method(
                spec, method, fixed_roots[method], original_roots["target_on"])
        )
        transfer_rows.extend(enrich_transfer_rows(raw_rows, current_rows))
    # Build exact paired hit rows from the production detection CSVs. These
    # rows are independent of the complex increment statistics above.
    detection_rows: list[dict[str, Any]] = []
    on_manifest_path, on_manifest = load_manifest_row(fixed_roots["J0_Current"]["target_on"])
    on_map = load_complex_map(on_manifest_path, on_manifest["after_real_path"],
                              on_manifest["after_imag_path"])
    centers = target_centers(fixed_roots["J0_Current"]["target_on"],
                             on_manifest_path, on_manifest, on_map.shape)
    half_rows = int(on_manifest.get("target_half_doppler_bins", 2))
    half_cols = int(on_manifest.get("target_half_range_bins", 2))
    for method in METHODS:
        for center in centers:
            on_det = detections_in_roi(fixed_roots[method]["target_on"],
                                       center, half_rows, half_cols)
            off_det = detections_in_roi(fixed_roots[method]["target_off"],
                                        center, half_rows, half_cols)
            on_hit = bool(on_det)
            off_hit = bool(off_det)
            on_margin = detection_margin(on_det)
            off_margin = detection_margin(off_det)
            detection_rows.append({
                "split": spec["split"], "scene_id": spec["scene_id"],
                "family": spec["label"], "snr_db": spec["snr_db"],
                "method": method, "target_id": center["target_id"],
                "on_hit": on_hit, "off_hit": off_hit,
                "paired_causal_hit": paired_causal_hit(on_hit, off_hit),
                "on_cfar_margin_db": on_margin,
                "off_cfar_margin_db": off_margin,
                "delta_cfar_margin_db": (
                    on_margin - off_margin
                    if math.isfinite(on_margin) and math.isfinite(off_margin)
                    else math.nan
                ),
                "paired_hit_definition": "on_hit AND NOT off_hit",
            })
    cfar_rows: list[dict[str, Any]] = []
    for method in METHODS:
        for role in ("target_off", "target_on"):
            cfar_rows.append(selective_v2.production_cfar_row(
                fixed_roots[method][role], spec["split"], spec["scene_id"],
                role, method))
    compact_scene = {
        "spec": spec,
        "status": scene["status"],
        "background_status": scene.get("background", {}).get("status"),
        "exact_pairing": "same deterministic background_input_dir for OFF/ON/TO",
        "raw_sha256": scene.get("raw_sha256", {}),
        "plans": plan_records,
    }
    if scene_root.exists():
        shutil.rmtree(scene_root)
    return transfer_rows, detection_rows, cfar_rows, {
        "scene": compact_scene, "deployment": deployment,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("snr-screen", "audit"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--formal-dir", type=Path,
                        default=ROOT / "outputs/physics_adaptive_selective_v2_formal")
    parser.add_argument("--snr-values", default=None,
                        help="comma-separated SNR values; screen=2, audit=4")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    snr_values = parse_snr_values(
        args.snr_values,
        DEFAULT_SNR_SCREEN if args.mode == "snr-screen" else DEFAULT_SNR_AUDIT)
    specs = design_specs(args.mode, snr_values)
    formal_dir = args.formal_dir.resolve()
    gate = json.loads((formal_dir / "null_gate.json").read_text(encoding="utf-8"))
    positive_gate = json.loads(
        (formal_dir / "positive_quality_gate.json").read_text(encoding="utf-8"))
    base = formal.base_scenario()
    before = resource_snapshot()
    transfer_rows: list[dict[str, Any]] = []
    detection_rows: list[dict[str, Any]] = []
    cfar_rows: list[dict[str, Any]] = []
    deployment_rows_all: list[dict[str, Any]] = []
    scene_rows: list[dict[str, Any]] = []
    for index, spec in enumerate(specs, 1):
        print(f"[target-transfer] {index}/{len(specs)} {spec['scene_id']}", flush=True)
        transfer, detection, cfar, extra = run_one_scene(
            spec, base, output, args.build_dir.resolve(), gate, positive_gate)
        transfer_rows.extend(transfer)
        detection_rows.extend(detection)
        cfar_rows.extend(cfar)
        deployment_rows_all.extend(extra["deployment"])
        scene_rows.append(extra["scene"])

    paired_pd = []
    for method in METHODS:
        selected = [row for row in detection_rows if row["method"] == method]
        paired_pd.append({
            "split": args.mode,
            "method": method,
            "target_count": len(selected),
            "paired_causal_hit_count": sum(bool(row["paired_causal_hit"])
                                           for row in selected),
            "paired_causal_pd": (
                float(np.mean([bool(row["paired_causal_hit"]) for row in selected]))
                if selected else math.nan
            ),
            "on_hit_count": sum(bool(row["on_hit"]) for row in selected),
            "off_hit_count": sum(bool(row["off_hit"]) for row in selected),
            "definition": "paired_causal_hit = on_hit AND NOT off_hit",
        })
    bootstrap = [
        paired_scene_bootstrap(
            [row for row in detection_rows if row["method"] == method],
            "paired_causal_hit")
        | {"method": method, "split": args.mode}
        for method in METHODS
    ]
    cfar_bootstrap = []
    for method in METHODS:
        for metric in ("pfa", "false_clusters"):
            cfar_bootstrap.append(
                selective_v2.bootstrap_scene(cfar_rows, metric, method)
                | {"split": args.mode})
    write_csv(output / "target_transfer_complex_roi.csv", transfer_rows)
    write_csv(output / "paired_off_on_detection.csv", detection_rows)
    write_csv(output / "paired_causal_pd.csv", paired_pd)
    write_csv(output / "paired_causal_pd_bootstrap.csv", bootstrap)
    write_csv(output / "production_cfar_rows.csv", cfar_rows)
    write_csv(output / "production_cfar_bootstrap.csv", cfar_bootstrap)
    write_csv(output / "deployment_calibration_check.csv", deployment_rows_all)
    write_csv(output / "scene_summary.csv", [
        {
            "split": item["spec"]["split"],
            "scene_id": item["spec"]["scene_id"],
            "family": item["spec"]["label"],
            "snr_db": item["spec"]["snr_db"],
            "status": item["status"],
        }
        for item in scene_rows
    ])
    provenance = git_provenance(ROOT)
    manifest = {
        "schema_version": 1,
        "analysis": "Target Transfer / Preservation Audit",
        "mode": args.mode,
        **provenance,
        "ai_training": False,
        "formal_source_commit": json.loads(
            (formal_dir / "formal_matrix_manifest.json").read_text(
                encoding="utf-8")).get("source_commit"),
        "design": {
            "scene_count": len(specs),
            "families": list(FAMILIES),
            "snr_values_db": list(snr_values),
            "fresh_seed_namespace": str(min(spec["seed"] for spec in specs)),
            "roles": {
                "OFF": "C+N",
                "ON": "S+C+N",
                "TO": "S-only",
            },
            "same_seed_geometry_mismatch": True,
            "correction_source": "target_off",
            "on_derived_deployment_check": True,
        },
        "definitions": {
            "delta_y": "Y_ON_fixedcal - Y_OFF_fixedcal",
            "L_causal": "10log10(sum_ROI(|DeltaY_candidate|^2) / sum_ROI(|DeltaY_Current|^2))",
            "L_target_only": "10log10(sum_ROI(|Y_TO_candidate_fixedcal|^2) / sum_ROI(|Y_TO_Current_fixedcal|^2))",
            "paired_causal_hit": "on_hit AND NOT off_hit",
            "primary_calibration": "OFF-derived estimator and frozen correction applied identically to OFF/ON/TO",
            "deployment_check": "ON-derived estimator parameters are compared with OFF-derived parameters and excluded from primary metrics",
            "truth_used_in_estimator": False,
        },
        "outputs": {
            "target_transfer_complex_roi": "target_transfer_complex_roi.csv",
            "paired_off_on_detection": "paired_off_on_detection.csv",
            "paired_causal_pd": "paired_causal_pd.csv",
            "paired_causal_pd_bootstrap": "paired_causal_pd_bootstrap.csv",
            "production_cfar_rows": "production_cfar_rows.csv",
            "production_cfar_bootstrap": "production_cfar_bootstrap.csv",
            "deployment_calibration_check": "deployment_calibration_check.csv",
        },
        "cleanup": {
            "raw_scene_cleanup": True,
            "removed": ["*.bin", "*.npy", "*.f32", "*.log", "*.xml", "scene runtime directories"],
            "retained": "compact ROI complex statistics, paired detections, CFAR rows/bootstrap, manifests, resource snapshots",
        },
        "counts": {
            "scene_count": len(scene_rows),
            "target_transfer_rows": len(transfer_rows),
            "detection_rows": len(detection_rows),
            "production_cfar_rows": len(cfar_rows),
            "deployment_rows": len(deployment_rows_all),
        },
        "scene_records": scene_rows,
    }
    (output / "resource_snapshot_before.json").write_text(
        json.dumps(json_safe(before), ensure_ascii=False, indent=2,
                   allow_nan=False) + "\n", encoding="utf-8")
    (output / "resource_snapshot_after.json").write_text(
        json.dumps(json_safe(resource_snapshot()), ensure_ascii=False, indent=2,
                   allow_nan=False) + "\n", encoding="utf-8")
    (output / "target_transfer_manifest.json").write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2,
                   allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "output_dir": str(output),
        "mode": args.mode,
        "scene_count": len(scene_rows),
        "target_transfer_rows": len(transfer_rows),
        "paired_detection_rows": len(detection_rows),
        "ai_training": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
