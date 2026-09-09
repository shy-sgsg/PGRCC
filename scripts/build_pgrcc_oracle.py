#!/usr/bin/env python3
"""Build the bounded-complex-residual Oracle Headroom Audit for PGRCC-v1.

The audit is deliberately upstream of any neural-network training.  It creates
new, non-frozen Stage2 scenes, estimates the physical F1/F2 experts from paired
C+N background only, and searches bounded residuals plus a confidence blend.
Target truth is used only for labels/evaluation; it is never included in the
feature vector used by the exploratory predictor.

The output is region based: one scene realization/seed, one local Doppler row,
and one local range block.  Large generated BINs are removed after each case;
only compact CSV/JSON/PNG evidence remains in the requested output directory.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

os.environ.setdefault("MPLCONFIGDIR", "/tmp/pgrcc_oracle_matplotlib")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_ai_csi_oracle as oracle
import run_baseline_benchmark as v1
import run_baseline_v2 as v2


TAU = 2.0 * math.pi
FEATURE_NAMES = [
    "p38_slope_rad_per_hz",
    "p38_intercept_rad",
    "p38_rmse_rad",
    "p38_inlier_count",
    "coherence_mean",
    "F1_F2_energy_ratio_dB",
    "phase_residual_mean_rad",
    "phase_variance_rad2",
    "valid_sample_fraction",
    "texture_heterogeneity_cv",
    "distance_to_clutter_ridge_hz",
    "distance_to_support_edge_rows",
    "local_residual_tail_p95_dB",
    "robust_lsq_condition_number",
    "robust_lsq_confidence",
]

OBJECTIVE_SPECS = (
    ("delta_scnr_dB", True),
    ("delta_target_preservation_dB", True),
    ("delta_residual_p95_dB", False),
    ("delta_residual_cvar95_dB", False),
    ("delta_background_pfa", False),
    ("delta_detection_margin_dB", True),
)


def db(value: float) -> float:
    return float(10.0 * math.log10(max(float(value), 1.0e-300)))


def finite(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def wrap_phase(value: np.ndarray | float) -> np.ndarray | float:
    result = (np.asarray(value) + math.pi) % TAU - math.pi
    if np.ndim(result) == 0:
        return float(result)
    return result


def write_csv(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    materialized = list(rows)
    if not materialized:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: List[str] = []
    for row in materialized:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(materialized)


def json_default(value: object) -> object:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value)!r}")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def repo_path(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace(os.sep, "/")


def git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", "--git-dir=.git-real", "--work-tree=.", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return result.stdout.strip()


def source_provenance(command: Sequence[str]) -> Dict[str, object]:
    status = git_output("status", "--short", "--untracked-files=all")
    return {
        "source_commit": git_output("rev-parse", "HEAD"),
        "worktree_dirty_at_run": bool(status),
        "worktree_status_at_run": status.splitlines(),
        "command": list(command),
        "working_directory": str(ROOT),
    }


def set_audit_value(case: Dict[str, object], name: str, value: object) -> None:
    impairments = case.setdefault("impairments", {})
    if name == "texture_sigma":
        case[name] = float(value)
    elif name == "azimuth_subcell_count":
        case[name] = int(round(float(value)))
    elif name in {
        "channel_amp_mismatch_db",
        "channel_fixed_phase_mismatch_deg",
        "channel_phase_jitter_std_deg",
    }:
        impairments["enabled"] = True
        impairments[name] = float(value)
    elif name == "valid_sample_fraction":
        impairments["enabled"] = True
        impairments["channel_drop_probability"] = max(0.0, min(1.0, 1.0 - float(value)))
    elif name == "target_snr_db":
        case[name] = float(value)
    elif name == "target_radial_speed_mps":
        case["velocity_mode"] = "radial"
        case["radial_speed_mps"] = float(value)
    elif name == "target_azimuth_offset_deg":
        case[name] = float(value)
    elif name == "target_expected_bin":
        case[name] = int(round(float(value)))
    else:
        raise ValueError(f"unsupported Oracle audit dimension: {name}")


def endpoint(bounds: Sequence[object], high: bool) -> object:
    if len(bounds) != 2:
        raise ValueError(f"audit dimension must have two bounds, got {bounds!r}")
    value = bounds[1] if high else bounds[0]
    return float(value)


def build_cases(config: Dict[str, object], limit: int | None) -> List[Dict[str, object]]:
    audit = config["audit"]
    seeds = [int(v) for v in audit["seeds"]]
    count = min(int(audit["case_count"]), len(seeds))
    if limit is not None:
        count = min(count, int(limit))
    base = copy.deepcopy(config["base_case"])
    dimensions = dict(audit["dimensions"])
    names = list(dimensions)
    families = list(audit.get("factor_families", []))
    cases: List[Dict[str, object]] = []
    base_id = str(base.get("case_id", "pgrcc_oracle"))

    # Dedicated endpoint cases make factor-family holdouts meaningful.  They
    # are separate from all V2.1 frozen seeds and are never reused as training
    # data by the later dataset builder.
    dedicated = [
        ("nominal", {}),
        ("texture", {"texture_sigma": True}),
        ("channel_amp", {"channel_amp_mismatch_db": True}),
        ("channel_phase", {"channel_fixed_phase_mismatch_deg": True}),
        ("phase_jitter", {"channel_phase_jitter_std_deg": True}),
        ("valid_fraction", {"valid_sample_fraction": False}),
        ("target_snr", {"target_snr_db": False}),
        ("target_velocity", {"target_radial_speed_mps": True}),
        ("geometry", {"target_azimuth_offset_deg": True, "target_expected_bin": True}),
    ]
    dedicated_count = min(len(dedicated), count)
    for index in range(dedicated_count):
        family, selected = dedicated[index]
        case = copy.deepcopy(base)
        seed = seeds[index]
        for name, high in selected.items():
            set_audit_value(case, name, endpoint(dimensions[name], bool(high)))
        case["case_id"] = f"{base_id}_{family}_s{seed}"
        case["target_id"] = f"{base.get('target_id', 'PGRCC_TARGET')}_{family}_{seed}"
        case["seed"] = seed
        case["factor"] = family
        case["factor_family"] = family
        case["audit_values"] = {name: case.get(name, case.get("impairments", {}).get(name)) for name in names}
        # Stage2 only persists a paired C+N packet when channel impairments
        # are disabled.  With impairments, regenerate the background-only
        # packet independently from the same seed/config so it carries the
        # same impairment law without violating the simulator contract.
        case["use_paired_background"] = not bool(case.get("impairments", {}).get("enabled", False))
        cases.append(case)

    remaining = count - len(cases)
    if remaining <= 0:
        return cases
    rng = np.random.default_rng(int(audit.get("lhs_seed", 20261200)))
    permutations = {name: rng.permutation(remaining) for name in names}
    for offset in range(remaining):
        case = copy.deepcopy(base)
        seed = seeds[len(cases)]
        values: Dict[str, object] = {}
        for name, bounds in dimensions.items():
            low, high = float(bounds[0]), float(bounds[1])
            unit = (float(permutations[name][offset]) + float(rng.random())) / float(remaining)
            value = low + (high - low) * unit
            if name == "azimuth_subcell_count" or name == "target_expected_bin":
                value = int(round(value))
            set_audit_value(case, name, value)
            values[name] = value
        case["case_id"] = f"{base_id}_mixed_lhs{offset + 1:02d}_s{seed}"
        case["target_id"] = f"{base.get('target_id', 'PGRCC_TARGET')}_mixed_{offset + 1}_{seed}"
        case["seed"] = seed
        case["factor"] = "mixed_lhs"
        case["factor_family"] = "mixed_lhs"
        case["factor_level"] = f"lhs_{offset + 1:02d}"
        case["audit_values"] = values
        case["use_paired_background"] = not bool(case.get("impairments", {}).get("enabled", False))
        cases.append(case)
    return cases


def robust_alpha_rows(
    f1_bg: np.ndarray,
    f2_bg: np.ndarray,
    p38_phase: np.ndarray,
    support: np.ndarray,
    p: oracle.Params,
    huber_delta: float,
    physics_regularization: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return background-only robust alpha, condition and confidence per row."""

    alpha_rows = np.exp(1j * np.asarray(p38_phase, dtype=float)).astype(np.complex128)
    condition = np.full(f1_bg.shape[0], np.nan, dtype=float)
    confidence = np.zeros(f1_bg.shape[0], dtype=float)
    columns = np.arange(max(0, p.rg_st), min(f1_bg.shape[1], p.rg_ed + 1), dtype=int)
    for row in np.flatnonzero(support):
        x = np.asarray(f2_bg[row, columns], dtype=np.complex128)
        y = np.asarray(f1_bg[row, columns], dtype=np.complex128)
        energy = float(np.mean(np.abs(x) ** 2))
        if not math.isfinite(energy) or energy <= 1.0e-24:
            continue
        prior = np.exp(1j * float(p38_phase[row]))
        alpha = complex(prior)
        weighted_energy = energy
        for _ in range(5):
            residual = y - alpha * x
            center = float(np.median(np.abs(residual)))
            scale = max(1.4826 * center, 1.0e-12)
            threshold = max(float(huber_delta), 1.0e-6) * scale
            weights = np.minimum(1.0, threshold / np.maximum(np.abs(residual), 1.0e-12))
            weighted_energy = float(np.sum(weights * np.abs(x) ** 2))
            denominator = weighted_energy + float(physics_regularization) * energy
            numerator = np.sum(weights * np.conj(x) * y) + float(physics_regularization) * energy * prior
            if denominator <= 1.0e-24:
                break
            updated = numerator / denominator
            previous = alpha
            alpha = complex(updated)
            if abs(updated - previous) <= 1.0e-8 * max(1.0, abs(alpha)):
                break
        fitted = y - alpha * x
        residual_power = float(np.mean(np.abs(fitted) ** 2))
        alpha_rows[row] = alpha
        confidence[row] = energy / max(energy + residual_power, 1.0e-24)
        condition[row] = (weighted_energy + float(physics_regularization) * energy) / max(
            float(physics_regularization) * energy, 1.0e-24
        )
    return alpha_rows, condition, confidence


def cfar_threshold_map(detect: np.ndarray, p: oracle.Params, threshold_scale: float = 1.0) -> np.ndarray:
    """Return the GO-CFAR threshold map using the production geometry."""

    power = np.abs(np.asarray(detect)) ** 2
    h, w = power.shape
    radius = int(p.cfar_guard + p.cfar_background)
    result = np.full((h, w), np.nan, dtype=float)
    if w <= 2 * radius:
        return result
    rows = np.arange(h, dtype=np.int64)[:, None]
    cols = np.arange(radius, w - radius, dtype=np.int64)[None, :]
    rowpad = np.concatenate((power[-radius:], power, power[:radius]), axis=0)
    padded = np.pad(rowpad, ((0, 0), (radius, radius)), mode="constant")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(axis=0).cumsum(axis=1)

    def rect(r0: np.ndarray, r1: np.ndarray, c0: np.ndarray, c1: np.ndarray) -> np.ndarray:
        return (
            integral[r1 + 1, c1 + 1]
            - integral[r0, c1 + 1]
            - integral[r1 + 1, c0]
            + integral[r0, c0]
        )

    g, b = int(p.cfar_guard), int(p.cfar_background)
    left = rect(rows, rows + 2 * radius, cols, cols + radius - g - 1) / ((2 * radius + 1) * b)
    right = rect(rows, rows + 2 * radius, cols + radius + g + 1, cols + 2 * radius) / ((2 * radius + 1) * b)
    top = rect(rows, rows + radius - g - 1, cols, cols + 2 * radius) / ((2 * radius + 1) * b)
    bottom = rect(rows + radius + g + 1, rows + 2 * radius, cols, cols + 2 * radius) / ((2 * radius + 1) * b)
    noise = np.maximum.reduce((left, right, top, bottom))
    result[:, radius : w - radius] = float(threshold_scale) * oracle.go_alpha(
        p.cfar_pfa, g, b
    ) * noise
    return result


def current_and_expert_arrays(data: v1.CaseData, method_config: Mapping[str, object]) -> Dict[str, object]:
    alignment = data.background_alignment
    p38_phase = np.asarray(alignment.p38["phase_rows"], dtype=float)
    current_bg = oracle.current_cancel(alignment.f1_bg, alignment.f2_bg, p38_phase, data.current_support)
    current_target = oracle.current_cancel(alignment.f1_target, alignment.f2_target, p38_phase, data.current_support)
    row_ls = np.asarray(alignment.alpha, dtype=np.complex128)
    robust, robust_condition, robust_confidence = robust_alpha_rows(
        alignment.f1_bg,
        alignment.f2_bg,
        p38_phase,
        data.current_support,
        data.p,
        float(method_config.get("robust_huber_delta", 1.5)),
        float(method_config.get("robust_physics_regularization", 0.05)),
    )
    return {
        "alignment": alignment,
        "p38_phase": p38_phase,
        "current_bg": current_bg,
        "current_target": current_target,
        "row_ls_alpha": row_ls,
        "robust_alpha": robust,
        "robust_condition": robust_condition,
        "robust_confidence": robust_confidence,
        "current_detector_bg": v1.dynamic_detector(current_bg, alignment.f2_bg, data.current_support),
        "current_detector_target": v1.dynamic_detector(current_target, alignment.f2_target, data.current_support),
    }


def region_rows(data: v1.CaseData, region_config: Mapping[str, object]) -> List[Tuple[int, int, int]]:
    p = data.p
    support_rows = np.flatnonzero(data.current_support)
    if not support_rows.size:
        return []
    stride = max(1, int(region_config.get("doppler_row_stride", 4)))
    half = max(0, int(region_config.get("local_doppler_half_width", 2)))
    start = max(int(p.rg_st), 0)
    stop = min(int(p.rg_ed) + 1, data.alignment.f1_bg.shape[1])
    block = max(1, int(region_config.get("range_block_size", 256)))
    block_stride = max(1, int(region_config.get("range_block_stride", block)))
    rows = set(range(int(support_rows[0]), int(support_rows[-1]) + 1, stride))
    rows.update({int(support_rows[0]), int(support_rows[-1]), int(data.truth_row)})
    rows = {row for row in rows if int(support_rows[0]) <= row <= int(support_rows[-1])}
    output: List[Tuple[int, int, int]] = []
    for row in sorted(rows):
        for lo in range(start, stop, block_stride):
            output.append((row, lo, min(stop, lo + block)))
    if half < 0:
        raise ValueError("local_doppler_half_width must be non-negative")
    return output


def region_features(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    row: int,
    lo: int,
    hi: int,
) -> Dict[str, object]:
    alignment = arrays["alignment"]
    assert isinstance(alignment, oracle.Alignment)
    p = data.p
    half = 2
    row0 = max(0, row - half)
    row1 = min(p.pulse_num, row + half + 1)
    block = slice(lo, hi)
    f1 = alignment.f1_bg[row0:row1, block]
    f2 = alignment.f2_bg[row0:row1, block]
    local_power = np.abs(f1) ** 2
    row_power = np.mean(local_power, axis=1) if local_power.size else np.empty(0)
    mean_power = float(np.mean(row_power)) if row_power.size else math.nan
    texture_cv = float(np.std(row_power) / max(mean_power, 1.0e-300)) if row_power.size else math.nan
    phase_samples = np.angle(f1 * np.conj(f2)).reshape(-1)
    p38_phase = float(arrays["p38_phase"][row])
    phase_residual = wrap_phase(phase_samples - p38_phase)
    phase_residual = np.asarray(phase_residual, dtype=float)
    phase_mean = float(np.angle(np.mean(np.exp(1j * phase_residual)))) if phase_residual.size else math.nan
    phase_variance = float(1.0 - abs(np.mean(np.exp(1j * phase_residual)))) if phase_residual.size else math.nan
    input_power = np.abs(alignment.f1_bg[row, block]) ** 2
    current_power = np.abs(arrays["current_bg"][row, block]) ** 2
    input_median = float(np.median(input_power)) if input_power.size else math.nan
    residual_p95 = float(np.percentile(current_power, 95.0)) if current_power.size else math.nan
    support = np.flatnonzero(data.current_support)
    edge_distance = min(row - int(support[0]), int(support[-1]) - row)
    p38 = alignment.p38
    return {
        "p38_slope_rad_per_hz": finite(p38.get("k")),
        "p38_intercept_rad": finite(p38.get("b")),
        "p38_rmse_rad": finite(p38.get("rmse")),
        "p38_inlier_count": finite(p38.get("sample_count")),
        "coherence_mean": float(np.nanmean(alignment.coherence[row0:row1])),
        "F1_F2_energy_ratio_dB": db(float(np.mean(np.abs(f1) ** 2)) / max(float(np.mean(np.abs(f2) ** 2)), 1.0e-300)),
        "phase_residual_mean_rad": phase_mean,
        "phase_variance_rad2": phase_variance,
        "valid_sample_fraction": float(data.valid_sample_fraction),
        "texture_heterogeneity_cv": texture_cv,
        "distance_to_clutter_ridge_hz": abs(float(data.axis[row] - data.fa_ctr)),
        "distance_to_support_edge_rows": float(edge_distance),
        "local_residual_tail_p95_dB": db(residual_p95 / max(input_median, 1.0e-300)),
        "robust_lsq_condition_number": finite(arrays["robust_condition"][row]),
        "robust_lsq_confidence": finite(arrays["robust_confidence"][row]),
    }


def replace_target_roi(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    row: int,
    lo: int,
    hi: int,
    candidate_bg: np.ndarray,
    candidate_target: np.ndarray,
    threshold_bg: np.ndarray,
    threshold_target: np.ndarray,
) -> Dict[str, float]:
    """Evaluate a candidate with current-map CFAR thresholds fixed.

    Fixing the thresholds makes the region search cheap and prevents the local
    candidate from changing its own operating point.  The selected diagnostic
    representatives are checked again with full-map GO-CFAR below.
    """

    alignment = arrays["alignment"]
    assert isinstance(alignment, oracle.Alignment)
    current_bg = np.asarray(arrays["current_bg"])
    current_target = np.asarray(arrays["current_target"])
    current_detector_bg = np.asarray(arrays["current_detector_bg"])
    current_detector_target = np.asarray(arrays["current_detector_target"])
    cols = np.arange(lo, hi, dtype=int)
    current_region_bg = current_bg[row, lo:hi]
    current_region_target = current_target[row, lo:hi]
    input_region = alignment.f1_bg[row, lo:hi]
    candidate_power = np.abs(candidate_bg) ** 2
    current_power = np.abs(current_region_bg) ** 2
    input_median = float(np.median(np.abs(input_region) ** 2)) if input_region.size else math.nan
    residual_p95 = float(np.percentile(candidate_power, 95.0)) if candidate_power.size else math.nan
    residual_p99 = float(np.percentile(candidate_power, 99.0)) if candidate_power.size else math.nan
    tail_threshold = input_median * 10.0 ** (15.0 / 10.0)
    tail_points = candidate_power[candidate_power >= residual_p95] if candidate_power.size else np.empty(0)
    cvar95 = float(np.mean(tail_points)) if tail_points.size else residual_p95
    current_p95 = float(np.percentile(current_power, 95.0)) if current_power.size else math.nan
    current_p99 = float(np.percentile(current_power, 99.0)) if current_power.size else math.nan
    current_tail = current_power[current_power >= current_p95] if current_power.size else np.empty(0)
    current_cvar = float(np.mean(current_tail)) if current_tail.size else current_p95
    valid_bg_threshold = threshold_bg[row, lo:hi]
    valid_bg_threshold = np.isfinite(valid_bg_threshold) & (valid_bg_threshold > 0.0)
    if np.any(valid_bg_threshold):
        background_pfa = float(np.mean(candidate_power[valid_bg_threshold] > threshold_bg[row, lo:hi][valid_bg_threshold]))
        current_pfa = float(np.mean(current_power[valid_bg_threshold] > threshold_bg[row, lo:hi][valid_bg_threshold]))
    else:
        background_pfa = math.nan
        current_pfa = math.nan

    r0 = max(0, int(data.truth_row) - 2)
    r1 = min(current_bg.shape[0], int(data.truth_row) + 3)
    c0 = max(0, int(data.truth_col) - 2)
    c1 = min(current_bg.shape[1], int(data.truth_col) + 3)
    before_signal = alignment.f1_target[r0:r1, c0:c1] - alignment.f1_bg[r0:r1, c0:c1]
    current_signal = current_target[r0:r1, c0:c1] - current_bg[r0:r1, c0:c1]
    candidate_signal = current_signal.copy()
    candidate_clutter = np.abs(current_bg[r0:r1, c0:c1]) ** 2
    if r0 <= row < r1:
        overlap_lo = max(lo, c0)
        overlap_hi = min(hi, c1)
        if overlap_hi > overlap_lo:
            source = slice(overlap_lo - lo, overlap_hi - lo)
            target = slice(overlap_lo - c0, overlap_hi - c0)
            candidate_signal[row - r0, target] = candidate_target[source] - candidate_bg[source]
            candidate_clutter[row - r0, target] = np.abs(candidate_bg[source]) ** 2
    before_power = float(np.mean(np.abs(before_signal) ** 2))
    current_signal_power = float(np.mean(np.abs(current_signal) ** 2))
    candidate_signal_power = float(np.mean(np.abs(candidate_signal) ** 2))
    before_clutter = float(np.mean(np.abs(alignment.f1_bg[r0:r1, c0:c1]) ** 2))
    current_clutter = float(np.mean(np.abs(current_bg[r0:r1, c0:c1]) ** 2))
    candidate_clutter_power = float(np.mean(candidate_clutter))
    current_scnr = db(current_signal_power / max(current_clutter, 1.0e-300)) - db(before_power / max(before_clutter, 1.0e-300))
    candidate_scnr = db(candidate_signal_power / max(candidate_clutter_power, 1.0e-300)) - db(before_power / max(before_clutter, 1.0e-300))
    current_target_loss = db(current_signal_power / max(before_power, 1.0e-300))
    candidate_target_loss = db(candidate_signal_power / max(before_power, 1.0e-300))

    candidate_detector_target = current_detector_target[r0:r1, c0:c1].copy()
    current_detector_target_roi = current_detector_target[r0:r1, c0:c1]
    if r0 <= row < r1:
        overlap_lo = max(lo, c0)
        overlap_hi = min(hi, c1)
        if overlap_hi > overlap_lo:
            candidate_detector_target[row - r0, overlap_lo - c0 : overlap_hi - c0] = candidate_target[
                overlap_lo - lo : overlap_hi - lo
            ]
    target_threshold = threshold_target[r0:r1, c0:c1]
    valid_target = np.isfinite(target_threshold) & (target_threshold > 0.0)
    if np.any(valid_target):
        current_ratio = np.abs(current_detector_target_roi[valid_target]) ** 2 / target_threshold[valid_target]
        candidate_ratio = np.abs(candidate_detector_target[valid_target]) ** 2 / target_threshold[valid_target]
        current_margin = db(float(np.max(current_ratio)))
        candidate_margin = db(float(np.max(candidate_ratio)))
        candidate_detected = float(np.any(candidate_ratio > 1.0))
    else:
        current_margin = math.nan
        candidate_margin = math.nan
        candidate_detected = math.nan
    return {
        "scnr_improvement_dB": candidate_scnr,
        "target_loss_dB": candidate_target_loss,
        "residual_p95_dB": db(residual_p95 / max(input_median, 1.0e-300)),
        "residual_p99_dB": db(residual_p99 / max(input_median, 1.0e-300)),
        "residual_cvar95_dB": db(cvar95 / max(input_median, 1.0e-300)),
        "high_tail_fraction": float(np.mean(candidate_power > tail_threshold)) if candidate_power.size else math.nan,
        "background_pfa": background_pfa,
        "target_cfar_margin_dB": candidate_margin,
        "target_detected_fixed_threshold": candidate_detected,
        "current_scnr_improvement_dB": current_scnr,
        "current_target_loss_dB": current_target_loss,
        "current_residual_p95_dB": db(current_p95 / max(input_median, 1.0e-300)),
        "current_residual_p99_dB": db(current_p99 / max(input_median, 1.0e-300)),
        "current_residual_cvar95_dB": db(current_cvar / max(input_median, 1.0e-300)),
        "current_high_tail_fraction": float(np.mean(current_power > tail_threshold)) if current_power.size else math.nan,
        "current_background_pfa": current_pfa,
        "current_target_cfar_margin_dB": current_margin,
        "current_target_detected_fixed_threshold": float(np.any(np.abs(current_detector_target_roi[valid_target]) ** 2 > target_threshold[valid_target])) if np.any(valid_target) else math.nan,
        "delta_scnr_dB": candidate_scnr - current_scnr,
        "delta_target_preservation_dB": candidate_target_loss - current_target_loss,
        "delta_residual_p95_dB": db(residual_p95 / max(current_p95, 1.0e-300)),
        "delta_residual_p99_dB": db(residual_p99 / max(current_p99, 1.0e-300)),
        "delta_residual_cvar95_dB": db(cvar95 / max(current_cvar, 1.0e-300)),
        "delta_high_tail_fraction": float(np.mean(candidate_power > tail_threshold) - np.mean(current_power > tail_threshold)) if current_power.size else math.nan,
        "delta_background_pfa": background_pfa - current_pfa if math.isfinite(background_pfa) and math.isfinite(current_pfa) else math.nan,
        "delta_detection_margin_dB": candidate_margin - current_margin if math.isfinite(candidate_margin) and math.isfinite(current_margin) else math.nan,
    }


def materialize_candidate(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    candidate: Mapping[str, object],
) -> Tuple[np.ndarray, np.ndarray]:
    """Materialize one candidate on the full current map for exact checking."""

    alignment = arrays["alignment"]
    assert isinstance(alignment, oracle.Alignment)
    current_bg = np.asarray(arrays["current_bg"])
    current_target = np.asarray(arrays["current_target"])
    candidate_bg = current_bg.copy()
    candidate_target = current_target.copy()
    if str(candidate.get("candidate_type")) == "current":
        return candidate_bg, candidate_target
    expert = str(candidate.get("expert"))
    row = int(candidate["doppler_row"])
    lo = int(candidate["range_block_start"])
    hi = int(candidate["range_block_stop"])
    if expert == "current_p38":
        alpha_phy = complex(np.exp(1j * arrays["p38_phase"][row]))
    elif expert == "row_ls":
        alpha_phy = complex(arrays["row_ls_alpha"][row])
    elif expert == "robust_row_ls":
        alpha_phy = complex(arrays["robust_alpha"][row])
    else:
        raise ValueError(f"unknown exact-check expert {expert!r}")
    delta_a = float(candidate.get("delta_log_amplitude", 0.0))
    delta_phi = float(candidate.get("delta_phase", 0.0))
    gate = float(candidate.get("gate", 0.0))
    alpha = alpha_phy * math.exp(delta_a) * np.exp(1j * delta_phi)
    corrected_bg = alignment.f1_bg[row, lo:hi] - alpha * alignment.f2_bg[row, lo:hi]
    corrected_target = alignment.f1_target[row, lo:hi] - alpha * alignment.f2_target[row, lo:hi]
    if gate == 0.0:
        # Preserve the exact identity contract, including bitwise equality of
        # the selected region with Current before any metric computation.
        return candidate_bg, candidate_target
    candidate_bg[row, lo:hi] = (1.0 - gate) * current_bg[row, lo:hi] + gate * corrected_bg
    candidate_target[row, lo:hi] = (1.0 - gate) * current_target[row, lo:hi] + gate * corrected_target
    return candidate_bg, candidate_target


def exact_global_metrics(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    bg: np.ndarray,
    target: np.ndarray,
) -> Dict[str, float]:
    """Evaluate a full-map candidate with freshly recomputed GO-CFAR."""

    alignment = arrays["alignment"]
    assert isinstance(alignment, oracle.Alignment)
    detector_bg = v1.dynamic_detector(bg, alignment.f2_bg, data.current_support)
    detector_target = v1.dynamic_detector(target, alignment.f2_target, data.current_support)
    col_start = max(0, min(bg.shape[1], int(data.p.rg_st)))
    col_stop = max(col_start + 1, min(bg.shape[1], int(data.p.rg_ed) + 1))
    residual_power = np.abs(bg[:, col_start:col_stop]) ** 2
    input_power = np.abs(alignment.f1_bg[:, col_start:col_stop]) ** 2
    input_median = float(np.median(input_power))
    p95 = float(np.percentile(residual_power, 95.0))
    p99 = float(np.percentile(residual_power, 99.0))
    cvar = float(np.mean(residual_power[residual_power >= p95]))
    r0 = max(0, data.truth_row - 2)
    r1 = min(bg.shape[0], data.truth_row + 3)
    c0 = max(0, data.truth_col - 2)
    c1 = min(bg.shape[1], data.truth_col + 3)
    before_signal = alignment.f1_target[r0:r1, c0:c1] - alignment.f1_bg[r0:r1, c0:c1]
    after_signal = target[r0:r1, c0:c1] - bg[r0:r1, c0:c1]
    before_signal_power = float(np.mean(np.abs(before_signal) ** 2))
    after_signal_power = float(np.mean(np.abs(after_signal) ** 2))
    before_clutter = float(np.mean(np.abs(alignment.f1_bg[r0:r1, c0:c1]) ** 2))
    after_clutter = float(np.mean(np.abs(bg[r0:r1, c0:c1]) ** 2))
    input_scnr = db(before_signal_power / max(before_clutter, 1.0e-300))
    output_scnr = db(after_signal_power / max(after_clutter, 1.0e-300))
    target_result = oracle.go_cfar(detector_target, data.truth_row, data.truth_col, data.p)
    background_result = oracle.go_cfar(detector_bg, bg.shape[0] + 100, bg.shape[1] + 100, data.p)
    radius = int(data.p.cfar_guard + data.p.cfar_background)
    test_cells = float(bg.shape[0] * max(bg.shape[1] - 2 * radius, 0))
    threshold = cfar_threshold_map(detector_target, data.p)
    target_threshold = threshold[r0:r1, c0:c1]
    valid = np.isfinite(target_threshold) & (target_threshold > 0.0)
    if np.any(valid):
        margin = db(float(np.max(np.abs(detector_target[r0:r1, c0:c1][valid]) ** 2 / target_threshold[valid])))
    else:
        margin = math.nan
    return {
        "exact_SCNR_improvement_dB": output_scnr - input_scnr,
        "exact_target_loss_dB": db(after_signal_power / max(before_signal_power, 1.0e-300)),
        "exact_residual_p95_dB": db(p95 / max(input_median, 1.0e-300)),
        "exact_residual_p99_dB": db(p99 / max(input_median, 1.0e-300)),
        "exact_residual_cvar95_dB": db(cvar / max(input_median, 1.0e-300)),
        "exact_background_Pfa": float(background_result["false_alarm_count"] / max(test_cells, 1.0)),
        "exact_target_Pd": float(target_result["Pd"]),
        "exact_target_cfar_margin_dB": margin,
        "exact_target_peak_power": float(target_result["target_peak_power"]),
        "exact_background_false_alarm_count": float(background_result["false_alarm_count"]),
    }


def exact_representative_rows(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    pareto_rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    candidates = [row for row in pareto_rows if str(row.get("candidate_type")) != "current"]
    current = next((row for row in pareto_rows if str(row.get("candidate_type")) == "current"), None)
    if current is None:
        raise RuntimeError("exact representative check requires Current")
    selected: Dict[Tuple[str, str], Mapping[str, object]] = {("current_reference", str(current.get("region_id"))): current}
    if candidates:
        selectors = {
            "max_scnr": max(candidates, key=lambda row: finite(row.get("delta_scnr_dB"))),
            "min_tail_p95": min(candidates, key=lambda row: finite(row.get("delta_residual_p95_dB"))),
            "min_background_pfa": min(candidates, key=lambda row: finite(row.get("delta_background_pfa"))),
            "max_detection_margin": max(candidates, key=lambda row: finite(row.get("delta_detection_margin_dB"))),
        }
        for reason, row in selectors.items():
            key = (reason, str(row.get("region_id")) + str(row.get("expert")) + str(row.get("delta_phase")))
            selected[key] = row
    current_bg, current_target = materialize_candidate(data, arrays, current)
    current_metrics = exact_global_metrics(data, arrays, current_bg, current_target)
    output: List[Dict[str, object]] = []
    for key, candidate in selected.items():
        reason = key[0]
        bg, target = materialize_candidate(data, arrays, candidate)
        metrics = exact_global_metrics(data, arrays, bg, target)
        row: Dict[str, object] = {
            "case_id": str(data.case["case_id"]),
            "seed": int(data.case["seed"]),
            "factor_family": str(data.case.get("factor_family", "unknown")),
            "selection_reason": reason,
            "region_id": str(candidate.get("region_id")),
            "expert": str(candidate.get("expert")),
            "candidate_type": str(candidate.get("candidate_type")),
            "doppler_row": int(candidate.get("doppler_row")),
            "range_block_start": int(candidate.get("range_block_start")),
            "range_block_stop": int(candidate.get("range_block_stop")),
            "delta_log_amplitude": finite(candidate.get("delta_log_amplitude")),
            "delta_phase": finite(candidate.get("delta_phase")),
            "gate": finite(candidate.get("gate")),
            "screen_delta_scnr_dB": finite(candidate.get("delta_scnr_dB")),
            "screen_delta_target_preservation_dB": finite(candidate.get("delta_target_preservation_dB")),
            "screen_delta_residual_p95_dB": finite(candidate.get("delta_residual_p95_dB")),
            "screen_delta_background_pfa": finite(candidate.get("delta_background_pfa")),
        }
        row.update(metrics)
        for metric, current_value in current_metrics.items():
            candidate_value = metrics.get(metric, math.nan)
            if metric == "exact_target_Pd":
                row[f"delta_{metric}"] = float(candidate_value) - float(current_value)
            elif metric.startswith("exact_") and metric not in {"exact_background_false_alarm_count", "exact_target_peak_power"}:
                row[f"delta_{metric}"] = float(candidate_value) - float(current_value) if math.isfinite(float(candidate_value)) and math.isfinite(float(current_value)) else math.nan
        output.append(row)
    return output


def candidate_row(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    feature_values: Mapping[str, object],
    row: int,
    lo: int,
    hi: int,
    expert: str,
    candidate_type: str,
    delta_a: float,
    delta_phi: float,
    gate: float,
    alpha_phy: complex,
    bg_region: np.ndarray,
    target_region: np.ndarray,
    threshold_bg: np.ndarray,
    threshold_target: np.ndarray,
) -> Dict[str, object]:
    metrics = replace_target_roi(
        data, arrays, row, lo, hi, bg_region, target_region, threshold_bg, threshold_target
    )
    result: Dict[str, object] = {
        "case_id": str(data.case["case_id"]),
        "seed": int(data.case["seed"]),
        "factor_family": str(data.case.get("factor_family", "unknown")),
        "factor": str(data.case.get("factor", "unknown")),
        "factor_level": data.case.get("factor_level", "audit"),
        "region_id": f"{data.case['case_id']}:r{row}:c{lo}-{hi}",
        "doppler_row": int(row),
        "range_block_start": int(lo),
        "range_block_stop": int(hi),
        "expert": expert,
        "candidate_type": candidate_type,
        "delta_log_amplitude": float(delta_a),
        "delta_phase": float(delta_phi),
        "gate": float(gate),
        "alpha_phy_abs": float(abs(alpha_phy)),
        "alpha_phy_phase_rad": float(np.angle(alpha_phy)),
        "status": "ok",
    }
    result.update(feature_values)
    result.update(metrics)
    return result


def dominates(a: Mapping[str, object], b: Mapping[str, object], tolerance: float = 1.0e-12) -> bool:
    strict = False
    for key, maximize in OBJECTIVE_SPECS:
        av, bv = finite(a.get(key)), finite(b.get(key))
        if not math.isfinite(av) or not math.isfinite(bv):
            return False
        if maximize:
            if av < bv - tolerance:
                return False
            strict |= av > bv + tolerance
        else:
            if av > bv + tolerance:
                return False
            strict |= av < bv - tolerance
    return strict


def pareto_front(candidates: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    front: List[Dict[str, object]] = []
    for index, candidate in enumerate(candidates):
        if any(dominates(other, candidate) for j, other in enumerate(candidates) if j != index):
            continue
        row = dict(candidate)
        row["pareto_rank"] = 0
        front.append(row)
    # Keep the exact Current fallback in the audit table even when a bounded
    # candidate dominates it.  This makes identity and relative-to-Current
    # checks explicit rather than vacuous.
    current = next((dict(row) for row in candidates if str(row.get("candidate_type")) == "current"), None)
    if current is not None and not any(str(row.get("candidate_type")) == "current" for row in front):
        current["pareto_rank"] = 0
        front.insert(0, current)
    return front


def meets_policy(candidate: Mapping[str, object], policy: Mapping[str, object]) -> bool:
    checks = (
        ("delta_scnr_dB", float(policy["min_delta_scnr_db"]), lambda x, y: x >= y),
        ("delta_target_preservation_dB", float(policy["min_delta_target_preservation_db"]), lambda x, y: x >= y),
        ("delta_residual_p95_dB", float(policy["max_delta_tail_p95_db"]), lambda x, y: x <= y),
        ("delta_residual_cvar95_dB", float(policy["max_delta_tail_cvar95_db"]), lambda x, y: x <= y),
        ("delta_background_pfa", float(policy["max_delta_background_pfa"]), lambda x, y: x <= y),
        ("delta_detection_margin_dB", float(policy["min_delta_detection_margin_db"]), lambda x, y: x >= y),
    )
    for key, bound, predicate in checks:
        value = finite(candidate.get(key))
        if not math.isfinite(value) or not predicate(value, bound):
            return False
    return True


def select_primary(front: Sequence[Dict[str, object]], policy: Mapping[str, object]) -> Dict[str, object]:
    current = next((dict(row) for row in front if str(row.get("candidate_type")) == "current"), None)
    feasible = [dict(row) for row in front if meets_policy(row, policy) and str(row.get("candidate_type")) != "current"]
    if not feasible:
        if current is None:
            raise RuntimeError("Pareto front lost the Current fallback")
        current["primary_reason"] = "current_hold_no_feasible_bounded_candidate"
        return current
    # Lexicographic tie-break after Pareto filtering.  No weighted scalar
    # objective is used: detection margin, SCNR, preservation, tail, and Pfa
    # remain separately visible in the audit files.
    feasible.sort(
        key=lambda row: (
            -finite(row.get("delta_detection_margin_dB")),
            -finite(row.get("delta_scnr_dB")),
            -finite(row.get("delta_target_preservation_dB")),
            finite(row.get("delta_residual_p95_dB")),
            finite(row.get("delta_residual_cvar95_dB")),
            finite(row.get("delta_background_pfa")),
        )
    )
    feasible[0]["primary_reason"] = "pareto_feasible_lexicographic_tiebreak"
    return feasible[0]


def make_candidate_set(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    feature_values: Mapping[str, object],
    row: int,
    lo: int,
    hi: int,
    threshold_bg: np.ndarray,
    threshold_target: np.ndarray,
    region_config: Mapping[str, object],
    expanded: bool,
) -> List[Dict[str, object]]:
    alignment = arrays["alignment"]
    assert isinstance(alignment, oracle.Alignment)
    f1_bg = alignment.f1_bg[row, lo:hi]
    f2_bg = alignment.f2_bg[row, lo:hi]
    f1_target = alignment.f1_target[row, lo:hi]
    f2_target = alignment.f2_target[row, lo:hi]
    current_bg = np.asarray(arrays["current_bg"])[row, lo:hi]
    current_target = np.asarray(arrays["current_target"])[row, lo:hi]
    current_row = candidate_row(
        data, arrays, feature_values, row, lo, hi, "Current", "current", 0.0, 0.0, 0.0,
        complex(np.exp(1j * arrays["p38_phase"][row])), current_bg, current_target,
        threshold_bg, threshold_target,
    )
    # Current is an exact fallback.  Keep its deltas exactly zero rather than
    # exposing harmless floating-point noise as a learned signal.
    for key in (
        "delta_scnr_dB", "delta_target_preservation_dB", "delta_residual_p95_dB",
        "delta_residual_p99_dB", "delta_residual_cvar95_dB", "delta_high_tail_fraction",
        "delta_background_pfa", "delta_detection_margin_dB",
    ):
        current_row[key] = 0.0

    experts = {
        "current_p38": complex(np.exp(1j * arrays["p38_phase"][row])),
        "row_ls": complex(arrays["row_ls_alpha"][row]),
        "robust_row_ls": complex(arrays["robust_alpha"][row]),
    }
    rows = [current_row]
    delta_a_values = region_config["expanded_delta_log_amplitude"] if expanded else region_config["initial_delta_log_amplitude"]
    delta_phi_values = region_config["expanded_delta_phase_rad"] if expanded else region_config["initial_delta_phase_rad"]
    gate_values = region_config["expanded_gate"] if expanded else region_config["initial_gate"]
    for expert, alpha_phy in experts.items():
        if abs(alpha_phy) <= 1.0e-12 or not (math.isfinite(alpha_phy.real) and math.isfinite(alpha_phy.imag)):
            continue
        for delta_a in delta_a_values:
            for delta_phi in delta_phi_values:
                corrected_bg = f1_bg - alpha_phy * math.exp(float(delta_a)) * np.exp(1j * float(delta_phi)) * f2_bg
                corrected_target = f1_target - alpha_phy * math.exp(float(delta_a)) * np.exp(1j * float(delta_phi)) * f2_target
                for gate in gate_values:
                    g = float(gate)
                    blended_bg = (1.0 - g) * current_bg + g * corrected_bg
                    blended_target = (1.0 - g) * current_target + g * corrected_target
                    rows.append(
                        candidate_row(
                            data, arrays, feature_values, row, lo, hi, expert, "bounded_corrected",
                            float(delta_a), float(delta_phi), g, alpha_phy, blended_bg, blended_target,
                            threshold_bg, threshold_target,
                        )
                    )
    return rows


def process_regions(
    data: v1.CaseData,
    method_config: Mapping[str, object],
    region_config: Mapping[str, object],
    policy: Mapping[str, object],
    expanded: bool,
) -> Dict[str, object]:
    arrays = current_and_expert_arrays(data, method_config)
    current_detector_bg = np.asarray(arrays["current_detector_bg"])
    current_detector_target = np.asarray(arrays["current_detector_target"])
    threshold_bg = cfar_threshold_map(current_detector_bg, data.p)
    threshold_target = cfar_threshold_map(current_detector_target, data.p)
    all_pareto: List[Dict[str, object]] = []
    region_map: List[Dict[str, object]] = []
    saturation: Dict[str, Dict[str, int]] = {
        name: {"regions": 0, "delta_a_saturated": 0, "delta_phase_saturated": 0, "gate_saturated": 0}
        for name in ["current_p38", "row_ls", "robust_row_ls", "overall"]
    }
    regions = region_rows(data, region_config)
    tolerance = float(region_config.get("bound_saturation_tolerance", 0.05))
    a_values = region_config["expanded_delta_log_amplitude"] if expanded else region_config["initial_delta_log_amplitude"]
    phi_values = region_config["expanded_delta_phase_rad"] if expanded else region_config["initial_delta_phase_rad"]
    a_bound = max(abs(float(value)) for value in a_values)
    phi_bound = max(abs(float(value)) for value in phi_values)
    start_time = time.perf_counter()
    for region_index, (row, lo, hi) in enumerate(regions):
        features = region_features(data, arrays, row, lo, hi)
        candidates = make_candidate_set(
            data, arrays, features, row, lo, hi, threshold_bg, threshold_target, region_config, expanded
        )
        front = pareto_front(candidates)
        current_candidate = next(item for item in candidates if str(item.get("candidate_type")) == "current")
        current_is_dominated = any(
            dominates(candidate, current_candidate)
            for candidate in candidates
            if str(candidate.get("candidate_type")) != "current"
        )
        diagnostic_candidates = [
            item for item in front if str(item.get("candidate_type")) != "current"
        ]
        if diagnostic_candidates:
            diagnostic_best_scnr = max(
                diagnostic_candidates,
                key=lambda item: (
                    finite(item.get("delta_scnr_dB")),
                    finite(item.get("delta_target_preservation_dB")),
                    -finite(item.get("delta_residual_p95_dB")),
                ),
            )
            diagnostic_best_target = max(
                diagnostic_candidates,
                key=lambda item: (
                    finite(item.get("delta_target_preservation_dB")),
                    finite(item.get("delta_detection_margin_dB")),
                    -finite(item.get("delta_residual_p95_dB")),
                ),
            )
            diagnostic_best_tail = min(
                diagnostic_candidates,
                key=lambda item: (
                    finite(item.get("delta_residual_p95_dB")),
                    finite(item.get("delta_residual_cvar95_dB")),
                    -finite(item.get("delta_scnr_dB")),
                ),
            )
            diagnostic_best_cvar = min(
                diagnostic_candidates,
                key=lambda item: (
                    finite(item.get("delta_residual_cvar95_dB")),
                    finite(item.get("delta_residual_p95_dB")),
                    -finite(item.get("delta_scnr_dB")),
                ),
            )
            diagnostic_best_pfa = min(
                diagnostic_candidates,
                key=lambda item: (
                    finite(item.get("delta_background_pfa")),
                    finite(item.get("delta_residual_p95_dB")),
                    -finite(item.get("delta_scnr_dB")),
                ),
            )
            diagnostic_best_margin = max(
                diagnostic_candidates,
                key=lambda item: (
                    finite(item.get("delta_detection_margin_dB")),
                    finite(item.get("delta_target_preservation_dB")),
                    -finite(item.get("delta_residual_p95_dB")),
                ),
            )
        else:
            diagnostic_best_scnr = current_candidate
            diagnostic_best_target = current_candidate
            diagnostic_best_tail = current_candidate
            diagnostic_best_cvar = current_candidate
            diagnostic_best_pfa = current_candidate
            diagnostic_best_margin = current_candidate
        primary = select_primary(front, policy)
        for item in front:
            item["expanded_search"] = bool(expanded)
            item["region_index"] = region_index
            all_pareto.append(item)
        for candidate in candidates:
            expert = str(candidate["expert"])
            if expert not in saturation:
                continue
            if candidate["candidate_type"] != "current":
                continue
            # This branch is intentionally unused; best expert candidates are
            # recorded below from their expert-specific Pareto fronts.
        for expert in ("current_p38", "row_ls", "robust_row_ls"):
            expert_front = [item for item in front if str(item.get("expert")) == expert]
            if not expert_front:
                continue
            expert_current = next((dict(item) for item in front if str(item.get("candidate_type")) == "current"), None)
            if expert_current is None:
                raise RuntimeError("missing Current fallback while selecting expert saturation")
            expert_current["expert"] = expert
            expert_primary = select_primary([expert_current] + expert_front, policy)
            stats = saturation[expert]
            stats["regions"] += 1
            if abs(finite(expert_primary.get("delta_log_amplitude"))) >= a_bound * (1.0 - tolerance):
                stats["delta_a_saturated"] += 1
            if abs(finite(expert_primary.get("delta_phase"))) >= phi_bound * (1.0 - tolerance):
                stats["delta_phase_saturated"] += 1
            gate = finite(expert_primary.get("gate"))
            if gate <= tolerance or gate >= 1.0 - tolerance:
                stats["gate_saturated"] += 1
        if str(primary.get("expert")) in saturation:
            stats = saturation["overall"]
            stats["regions"] += 1
            if abs(finite(primary.get("delta_log_amplitude"))) >= a_bound * (1.0 - tolerance):
                stats["delta_a_saturated"] += 1
            if abs(finite(primary.get("delta_phase"))) >= phi_bound * (1.0 - tolerance):
                stats["delta_phase_saturated"] += 1
            gate = finite(primary.get("gate"))
            if gate <= tolerance or gate >= 1.0 - tolerance:
                stats["gate_saturated"] += 1
        region_row = {
            "case_id": str(data.case["case_id"]),
            "seed": int(data.case["seed"]),
            "factor_family": str(data.case.get("factor_family", "unknown")),
            "factor": str(data.case.get("factor", "unknown")),
            "factor_level": data.case.get("factor_level", "audit"),
            "region_id": f"{data.case['case_id']}:r{row}:c{lo}-{hi}",
            "doppler_row": int(row),
            "range_block_start": int(lo),
            "range_block_stop": int(hi),
            "candidate_count": len(candidates),
            "pareto_count": len(front),
            "adaptive_worthwhile": bool(str(primary.get("candidate_type")) != "current" and meets_policy(primary, policy)),
            "primary_expert": str(primary.get("expert", "")),
            "primary_candidate_type": str(primary.get("candidate_type", "")),
            "primary_reason": str(primary.get("primary_reason", "")),
            "current_pareto_optimal": bool(not current_is_dominated),
            "oracle_delta_log_amplitude": finite(primary.get("delta_log_amplitude")),
            "oracle_delta_phase": finite(primary.get("delta_phase")),
            "oracle_gate": finite(primary.get("gate")),
            "oracle_gain_over_current_dB": finite(primary.get("delta_scnr_dB")),
            "oracle_delta_target_preservation_dB": finite(primary.get("delta_target_preservation_dB")),
            "oracle_delta_residual_p95_dB": finite(primary.get("delta_residual_p95_dB")),
            "oracle_delta_residual_p99_dB": finite(primary.get("delta_residual_p99_dB")),
            "oracle_delta_residual_cvar95_dB": finite(primary.get("delta_residual_cvar95_dB")),
            "oracle_delta_background_pfa": finite(primary.get("delta_background_pfa")),
            "oracle_delta_detection_margin_dB": finite(primary.get("delta_detection_margin_dB")),
            "diagnostic_best_scnr_expert": str(diagnostic_best_scnr.get("expert", "")),
            "diagnostic_best_scnr_delta_log_amplitude": finite(diagnostic_best_scnr.get("delta_log_amplitude")),
            "diagnostic_best_scnr_delta_phase": finite(diagnostic_best_scnr.get("delta_phase")),
            "diagnostic_best_scnr_gate": finite(diagnostic_best_scnr.get("gate")),
            "diagnostic_best_scnr_gain_dB": finite(diagnostic_best_scnr.get("delta_scnr_dB")),
            "diagnostic_best_scnr_target_preservation_dB": finite(diagnostic_best_scnr.get("delta_target_preservation_dB")),
            "diagnostic_best_scnr_delta_residual_p95_dB": finite(diagnostic_best_scnr.get("delta_residual_p95_dB")),
            "diagnostic_best_scnr_delta_residual_cvar95_dB": finite(diagnostic_best_scnr.get("delta_residual_cvar95_dB")),
            "diagnostic_best_scnr_delta_background_pfa": finite(diagnostic_best_scnr.get("delta_background_pfa")),
            "diagnostic_best_scnr_delta_detection_margin_dB": finite(diagnostic_best_scnr.get("delta_detection_margin_dB")),
            "diagnostic_max_target_preservation_dB": finite(diagnostic_best_target.get("delta_target_preservation_dB")),
            "diagnostic_min_residual_p95_dB": finite(diagnostic_best_tail.get("delta_residual_p95_dB")),
            "diagnostic_min_residual_cvar95_dB": finite(diagnostic_best_cvar.get("delta_residual_cvar95_dB")),
            "diagnostic_min_background_pfa": finite(diagnostic_best_pfa.get("delta_background_pfa")),
            "diagnostic_max_detection_margin_dB": finite(diagnostic_best_margin.get("delta_detection_margin_dB")),
            "current_scnr_improvement_dB": finite(primary.get("current_scnr_improvement_dB")),
            "current_target_loss_dB": finite(primary.get("current_target_loss_dB")),
            "current_residual_p95_dB": finite(primary.get("current_residual_p95_dB")),
            "current_residual_p99_dB": finite(primary.get("current_residual_p99_dB")),
            "current_residual_cvar95_dB": finite(primary.get("current_residual_cvar95_dB")),
            "current_background_pfa": finite(primary.get("current_background_pfa")),
            "current_target_cfar_margin_dB": finite(primary.get("current_target_cfar_margin_dB")),
            "target_row_truth": int(data.truth_row),
            "target_range_bin_truth": int(data.truth_col),
            "target_fd_truth_hz": float(data.target_fd),
            "status": "ok",
        }
        region_row.update(features)
        region_map.append(region_row)
        if (region_index + 1) % 64 == 0:
            print(
                f"[pgrcc-oracle] {data.case['case_id']}: regions {region_index + 1}/{len(regions)} "
                f"elapsed={time.perf_counter() - start_time:.1f}s",
                flush=True,
            )
    return {
        "region_map": region_map,
        "pareto": all_pareto,
        "exact_representatives": exact_representative_rows(data, arrays, all_pareto),
        "saturation": saturation,
        "region_count": len(regions),
        "threshold_bg": threshold_bg,
        "threshold_target": threshold_target,
    }


def saturation_rows(
    saturation: Mapping[str, Mapping[str, int]],
    expanded: bool,
    region_config: Mapping[str, object],
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for expert, stats in saturation.items():
        regions = int(stats.get("regions", 0))
        row = {
            "expert": expert,
            "search_stage": "expanded" if expanded else "initial",
            "regions": regions,
            "delta_a_saturation_count": int(stats.get("delta_a_saturated", 0)),
            "delta_phase_saturation_count": int(stats.get("delta_phase_saturated", 0)),
            "gate_saturation_count": int(stats.get("gate_saturated", 0)),
            "delta_a_saturation_fraction": float(stats.get("delta_a_saturated", 0) / max(regions, 1)),
            "delta_phase_saturation_fraction": float(stats.get("delta_phase_saturated", 0) / max(regions, 1)),
            "gate_saturation_fraction": float(stats.get("gate_saturated", 0) / max(regions, 1)),
            "delta_a_bound": max(abs(float(v)) for v in (region_config["expanded_delta_log_amplitude"] if expanded else region_config["initial_delta_log_amplitude"])),
            "delta_phase_bound_rad": max(abs(float(v)) for v in (region_config["expanded_delta_phase_rad"] if expanded else region_config["initial_delta_phase_rad"])),
        }
        rows.append(row)
    return rows


def ci95(values: Sequence[float]) -> Tuple[float, float]:
    values = [float(v) for v in values if math.isfinite(float(v))]
    if not values:
        return math.nan, math.nan
    mean = float(np.mean(values))
    if len(values) < 2:
        return mean, math.nan
    critical = v2.student_t_critical_975(len(values) - 1)
    half = critical * float(np.std(values, ddof=1)) / math.sqrt(len(values))
    return mean - half, mean + half


def summary_rows(region_map: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    groups: Dict[Tuple[str, str], List[Mapping[str, object]]] = {("overall", "overall"): list(region_map)}
    for row in region_map:
        groups.setdefault(("factor_family", str(row.get("factor_family", "unknown"))), []).append(row)
        groups.setdefault(("primary_expert", str(row.get("primary_expert", "unknown"))), []).append(row)
    output: List[Dict[str, object]] = []
    metrics = (
        "oracle_gain_over_current_dB",
        "oracle_delta_target_preservation_dB",
        "oracle_delta_residual_p95_dB",
        "oracle_delta_residual_p99_dB",
        "oracle_delta_residual_cvar95_dB",
        "oracle_delta_background_pfa",
        "oracle_delta_detection_margin_dB",
        "diagnostic_best_scnr_gain_dB",
        "diagnostic_best_scnr_target_preservation_dB",
        "diagnostic_best_scnr_delta_residual_p95_dB",
        "diagnostic_best_scnr_delta_residual_cvar95_dB",
        "diagnostic_best_scnr_delta_background_pfa",
        "diagnostic_best_scnr_delta_detection_margin_dB",
        "diagnostic_max_target_preservation_dB",
        "diagnostic_min_residual_p95_dB",
        "diagnostic_min_residual_cvar95_dB",
        "diagnostic_min_background_pfa",
        "diagnostic_max_detection_margin_dB",
    )
    for (scope, group), rows in sorted(groups.items()):
        worthwhile = [bool(row.get("adaptive_worthwhile", False)) for row in rows]
        result: Dict[str, object] = {
            "scope": scope,
            "group": group,
            "region_count": len(rows),
            "case_count": len({str(row.get("case_id")) for row in rows}),
            "seed_count": len({int(row.get("seed")) for row in rows}),
            "adaptive_worthwhile_count": int(sum(worthwhile)),
            "adaptive_worthwhile_fraction": float(np.mean(worthwhile)) if worthwhile else math.nan,
            "current_pareto_optimal_fraction": float(np.mean([bool(row.get("current_pareto_optimal", False)) for row in rows])) if rows else math.nan,
            "diagnostic_best_scnr_positive_fraction": float(np.mean([finite(row.get("diagnostic_best_scnr_gain_dB")) > 0.0 for row in rows])) if rows else math.nan,
            "diagnostic_best_scnr_gt_025db_fraction": float(np.mean([finite(row.get("diagnostic_best_scnr_gain_dB")) >= 0.25 for row in rows])) if rows else math.nan,
            "primary_expert_mode": max(
                (str(row.get("primary_expert", "")) for row in rows),
                key=lambda value: sum(str(row.get("primary_expert", "")) == value for row in rows),
                default="",
            ),
        }
        for metric in metrics:
            values = [finite(row.get(metric)) for row in rows]
            values = [value for value in values if math.isfinite(value)]
            low, high = ci95(values)
            result[f"{metric}_mean"] = float(np.mean(values)) if values else math.nan
            result[f"{metric}_median"] = float(np.median(values)) if values else math.nan
            result[f"{metric}_p95"] = float(np.percentile(values, 95.0)) if values else math.nan
            result[f"{metric}_ci95_low"] = low
            result[f"{metric}_ci95_high"] = high
        output.append(result)
    return output


def rankdata(values: Sequence[float]) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size, dtype=float)
    start = 0
    while start < values.size:
        stop = start + 1
        while stop < values.size and values[order[stop]] == values[order[start]]:
            stop += 1
        ranks[order[start:stop]] = 0.5 * (start + 1 + stop)
        start = stop
    return ranks


def correlation_rows(region_map: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    targets = (
        "oracle_gain_over_current_dB",
        "diagnostic_best_scnr_gain_dB",
    )
    for target_name in targets:
        target = np.asarray([finite(row.get(target_name)) for row in region_map], dtype=float)
        for name in FEATURE_NAMES:
            values = np.asarray([finite(row.get(name)) for row in region_map], dtype=float)
            valid = np.isfinite(values) & np.isfinite(target)
            n = int(np.count_nonzero(valid))
            if n >= 3 and np.std(values[valid]) > 1.0e-12 and np.std(target[valid]) > 1.0e-12:
                pearson = float(np.corrcoef(values[valid], target[valid])[0, 1])
                spearman = float(np.corrcoef(rankdata(values[valid]), rankdata(target[valid]))[0, 1])
            else:
                pearson = math.nan
                spearman = math.nan
            output.append({"feature": name, "outcome": target_name, "n": n, "pearson": pearson, "spearman": spearman})
    return output


def fit_ridge_fold(train_x: np.ndarray, train_y: np.ndarray, test_x: np.ndarray, ridge: float = 1.0) -> np.ndarray:
    medians = np.nanmedian(train_x, axis=0)
    train = np.where(np.isfinite(train_x), train_x, medians[None, :])
    test = np.where(np.isfinite(test_x), test_x, medians[None, :])
    mean = np.mean(train, axis=0)
    scale = np.std(train, axis=0)
    scale = np.where(scale > 1.0e-12, scale, 1.0)
    train = (train - mean) / scale
    test = (test - mean) / scale
    train_aug = np.column_stack((np.ones(train.shape[0]), train))
    test_aug = np.column_stack((np.ones(test.shape[0]), test))
    penalty = np.eye(train_aug.shape[1], dtype=float) * float(ridge)
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(train_aug.T @ train_aug + penalty, train_aug.T @ train_y)
    return test_aug @ beta


def predictor_rows(region_map: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    if not region_map:
        return []
    x = np.asarray([[finite(row.get(name)) for name in FEATURE_NAMES] for row in region_map], dtype=float)
    groups = {
        "leave_one_seed_out": [str(row.get("seed")) for row in region_map],
        "leave_one_factor_family_out": [str(row.get("factor_family")) for row in region_map],
    }
    output: List[Dict[str, object]] = []
    for target_name in ("oracle_gain_over_current_dB", "diagnostic_best_scnr_gain_dB"):
        y = np.asarray([finite(row.get(target_name)) for row in region_map], dtype=float)
        for mode, group_values in groups.items():
            unique = sorted(set(group_values))
            if len(unique) < 2:
                output.append({"target": target_name, "validation_mode": mode, "status": "not_applicable", "n_folds": len(unique)})
                continue
            predictions = np.full(y.shape, np.nan, dtype=float)
            fold_rows: List[Dict[str, object]] = []
            for group in unique:
                test = np.asarray([value == group for value in group_values], dtype=bool)
                train = ~test
                valid_train = train & np.isfinite(y)
                valid_test = test & np.isfinite(y)
                if np.count_nonzero(valid_train) < len(FEATURE_NAMES) + 2 or not np.any(valid_test):
                    continue
                predictions[valid_test] = fit_ridge_fold(x[valid_train], y[valid_train], x[valid_test])
                residual = predictions[valid_test] - y[valid_test]
                fold_rows.append({
                    "target": target_name,
                    "validation_mode": mode,
                    "holdout_group": group,
                    "n": int(np.count_nonzero(valid_test)),
                    "rmse": float(math.sqrt(np.mean(residual ** 2))),
                    "mae": float(np.mean(np.abs(residual))),
                })
            valid = np.isfinite(predictions) & np.isfinite(y)
            if np.any(valid):
                residual = predictions[valid] - y[valid]
                ss_tot = float(np.sum((y[valid] - np.mean(y[valid])) ** 2))
                output.append({
                    "target": target_name,
                    "validation_mode": mode,
                    "status": "ok",
                    "holdout_group": "aggregate",
                    "n": int(np.count_nonzero(valid)),
                    "n_folds": len(fold_rows),
                    "ridge_lambda": 1.0,
                    "rmse": float(math.sqrt(np.mean(residual ** 2))),
                    "mae": float(np.mean(np.abs(residual))),
                    "r2": float(1.0 - np.sum(residual ** 2) / ss_tot) if ss_tot > 1.0e-12 else math.nan,
                    "scope": "exploratory only; foldwise imputation/normalization; no production model",
                })
                output.extend(fold_rows)
            else:
                output.append({"target": target_name, "validation_mode": mode, "status": "insufficient_training_rows", "n_folds": len(fold_rows)})
    return output


def plot_outputs(region_map: Sequence[Mapping[str, object]], correlations: Sequence[Mapping[str, object]], out: Path) -> None:
    if not region_map:
        return
    gain = np.asarray([finite(row.get("diagnostic_best_scnr_gain_dB")) for row in region_map])
    preservation = np.asarray([finite(row.get("diagnostic_best_scnr_target_preservation_dB")) for row in region_map])
    valid = np.isfinite(gain) & np.isfinite(preservation)
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    families = sorted({str(row.get("factor_family", "unknown")) for row in region_map})
    colors = plt.cm.tab10(np.linspace(0, 1, max(1, len(families))))
    for color, family in zip(colors, families):
        mask = valid & np.asarray([str(row.get("factor_family", "unknown")) == family for row in region_map])
        ax.scatter(gain[mask], preservation[mask], s=10, alpha=0.45, label=family, color=color)
    ax.axvline(0.0, color="#666666", linewidth=0.7)
    ax.axhline(0.0, color="#666666", linewidth=0.7)
    ax.set_xlabel("Diagnostic max-SCNR Δ over Current (dB)")
    ax.set_ylabel("Target preservation at max-SCNR candidate (dB)")
    ax.set_title("Bounded complex residual diagnostic headroom")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(out / "oracle_headroom_scatter.png", dpi=160)
    plt.close(fig)

    family_values: Dict[str, List[float]] = {}
    for row in region_map:
        value = finite(row.get("diagnostic_best_scnr_gain_dB"))
        if math.isfinite(value):
            family_values.setdefault(str(row.get("factor_family", "unknown")), []).append(value)
    fig, ax = plt.subplots(figsize=(9.0, 5.5))
    labels = sorted(family_values)
    ax.boxplot([family_values[label] for label in labels], tick_labels=labels, showfliers=False)
    ax.axhline(0.0, color="#666666", linewidth=0.7)
    ax.set_ylabel("Oracle gain over Current (dB)")
    ax.set_title("Oracle gain by audit factor family")
    ax.tick_params(axis="x", rotation=35)
    ax.grid(True, axis="y", color="#dddddd", linewidth=0.6)
    fig.tight_layout()
    fig.savefig(out / "oracle_gain_by_family.png", dpi=160)
    plt.close(fig)

    corr = [row for row in correlations if math.isfinite(finite(row.get("spearman")))]
    corr.sort(key=lambda row: abs(float(row["spearman"])), reverse=True)
    if corr:
        top = corr[: min(12, len(corr))]
        fig, ax = plt.subplots(figsize=(9.0, 5.5))
        values = [float(row["spearman"]) for row in top][::-1]
        labels = [str(row["feature"]) for row in top][::-1]
        ax.barh(labels, values, color="#3182bd")
        ax.axvline(0.0, color="#666666", linewidth=0.7)
        ax.set_xlabel("Tie-aware Spearman correlation")
        ax.set_title("Physical feature association with Oracle gain")
        ax.grid(True, axis="x", color="#dddddd", linewidth=0.6)
        fig.tight_layout()
        fig.savefig(out / "oracle_feature_correlations.png", dpi=160)
        plt.close(fig)


def case_manifest_row(data: v1.CaseData) -> Dict[str, object]:
    return {
        "case_id": str(data.case["case_id"]),
        "seed": int(data.case["seed"]),
        "factor_family": str(data.case.get("factor_family", "unknown")),
        "factor": str(data.case.get("factor", "unknown")),
        "target_truth_row": int(data.truth_row),
        "target_truth_range_bin": int(data.truth_col),
        "target_bin_bytes": int(data.target_bin_bytes),
        "background_bin_bytes": int(data.background_bin_bytes),
        "target_input_sha256": data.target_sha256,
        "background_input_sha256": data.background_sha256,
        "valid_sample_fraction": float(data.valid_sample_fraction),
        "audit_values": data.case.get("audit_values", {}),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/research/pgrcc_oracle_audit.json"))
    parser.add_argument("--out", type=Path, default=Path("outputs/pgrcc_oracle"))
    parser.add_argument("--case-limit", type=int, default=None, help="run only the first N audit cases")
    parser.add_argument("--keep-data", action="store_true", help="retain generated case BINs for debugging")
    parser.add_argument("--allow-existing", action="store_true", help="allow writing into a non-empty output directory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = (ROOT / args.config).resolve() if not args.config.is_absolute() else args.config.resolve()
    out = (ROOT / args.out).resolve() if not args.out.is_absolute() else args.out.resolve()
    if not config_path.exists():
        raise FileNotFoundError(config_path)
    if out.exists() and any(out.iterdir()) and not args.allow_existing:
        raise RuntimeError(f"refusing to overwrite non-empty Oracle output: {out}; use a new --out or --allow-existing")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config = copy.deepcopy(config)
    config["output_root"] = repo_path(out / "cases")
    cases = build_cases(config, args.case_limit)
    if not cases:
        raise RuntimeError("Oracle audit produced no cases")
    out.mkdir(parents=True, exist_ok=True)
    command = ["python3", repo_path(Path(__file__)), "--config", repo_path(config_path), "--out", repo_path(out)]
    if args.case_limit is not None:
        command += ["--case-limit", str(args.case_limit)]
    if args.keep_data:
        command += ["--keep-data"]
    provenance = source_provenance(command)
    method_config = dict(config.get("method_config", {}))
    region_config = dict(config.get("regions", {}))
    policy = dict(config.get("worthwhile_policy", {}))
    all_regions: List[Dict[str, object]] = []
    all_pareto: List[Dict[str, object]] = []
    all_exact: List[Dict[str, object]] = []
    all_saturation: List[Dict[str, object]] = []
    case_rows: List[Dict[str, object]] = []
    failures: List[Dict[str, object]] = []
    expansion_records: List[Dict[str, object]] = []
    started = time.perf_counter()
    base_config_path = (ROOT / str(config["base_config"])).resolve()

    for case_index, case in enumerate(cases, start=1):
        print(f"[pgrcc-oracle] case {case_index}/{len(cases)}: {case['case_id']}", flush=True)
        paths: Dict[str, object] | None = None
        try:
            paths = v1.prepare_case_data(base_config_path, config, case, args.keep_data)
            data = v1.load_case_data(case, paths)
            initial = process_regions(data, method_config, region_config, policy, expanded=False)
            initial_sat = saturation_rows(initial["saturation"], False, region_config)
            expand_threshold = float(region_config.get("bound_saturation_expand_threshold", 0.20))
            expand = any(
                str(row.get("expert")) != "overall"
                and max(float(row.get("delta_a_saturation_fraction", 0.0)), float(row.get("delta_phase_saturation_fraction", 0.0))) > expand_threshold
                for row in initial_sat
            )
            if expand:
                print(f"[pgrcc-oracle] expanding bounds for {case['case_id']}", flush=True)
                final = process_regions(data, method_config, region_config, policy, expanded=True)
                final_sat = saturation_rows(final["saturation"], True, region_config)
                all_saturation.extend(initial_sat)
                all_saturation.extend(final_sat)
                selected = final
                expansion_records.append({"case_id": str(case["case_id"]), "expanded": True, "initial": initial_sat, "final": final_sat})
            else:
                all_saturation.extend(initial_sat)
                selected = initial
                expansion_records.append({"case_id": str(case["case_id"]), "expanded": False, "initial": initial_sat, "final": initial_sat})
            all_regions.extend(selected["region_map"])
            all_pareto.extend(selected["pareto"])
            all_exact.extend(selected["exact_representatives"])
            case_rows.append(case_manifest_row(data))
            print(
                f"[pgrcc-oracle] completed {case['case_id']}: regions={selected['region_count']} "
                f"elapsed={time.perf_counter() - started:.1f}s",
                flush=True,
            )
        except Exception as exc:
            failures.append({"case_id": str(case.get("case_id")), "error": repr(exc)})
            print(f"[pgrcc-oracle][ERR] {case.get('case_id')}: {exc}", file=sys.stderr, flush=True)
        finally:
            if paths is not None and not args.keep_data:
                v1.cleanup_case(paths)

    if not all_regions:
        raise RuntimeError(f"Oracle audit produced no region rows; failures={failures}")

    correlations = correlation_rows(all_regions)
    predictors = predictor_rows(all_regions)
    headroom = summary_rows(all_regions)
    write_csv(out / "oracle_region_map.csv", all_regions)
    write_csv(out / "oracle_pareto_summary.csv", all_pareto)
    write_csv(out / "oracle_exact_representatives.csv", all_exact)
    write_csv(out / "oracle_headroom_summary.csv", headroom)
    write_csv(out / "oracle_bound_saturation.csv", all_saturation)
    write_csv(out / "oracle_feature_correlations.csv", correlations)
    write_csv(out / "oracle_feature_predictor.csv", predictors)
    plot_outputs(all_regions, correlations, out)

    worthwhile = [bool(row.get("adaptive_worthwhile", False)) for row in all_regions]
    gains = [finite(row.get("oracle_gain_over_current_dB")) for row in all_regions]
    gains = [value for value in gains if math.isfinite(value)]
    stable_case_fractions = []
    for case_id in sorted({str(row.get("case_id")) for row in all_regions}):
        case_values = [row for row in all_regions if str(row.get("case_id")) == case_id]
        stable_case_fractions.append(float(np.mean([bool(row.get("adaptive_worthwhile", False)) for row in case_values])))
    overall_fraction = float(np.mean(worthwhile)) if worthwhile else 0.0
    worthwhile_gains = [
        finite(row.get("oracle_gain_over_current_dB"))
        for row in all_regions
        if bool(row.get("adaptive_worthwhile", False))
    ]
    worthwhile_gains = [value for value in worthwhile_gains if math.isfinite(value)]
    median_gain = float(np.median(gains)) if gains else math.nan
    median_worthwhile_gain = float(np.median(worthwhile_gains)) if worthwhile_gains else math.nan
    go_threshold_fraction = 0.10
    go_threshold_gain_db = 0.25
    predictor_ok = any(
        str(row.get("target")) == "oracle_gain_over_current_dB"
        and
        str(row.get("validation_mode")) == "leave_one_seed_out"
        and str(row.get("status")) == "ok"
        and math.isfinite(finite(row.get("r2")))
        for row in predictors
    )
    go = bool(
        not failures
        and overall_fraction >= go_threshold_fraction
        and math.isfinite(median_worthwhile_gain)
        and median_worthwhile_gain >= go_threshold_gain_db
        and predictor_ok
    )
    identity_test = bool(
        all(abs(finite(row.get("delta_scnr_dB"))) <= 1.0e-12 for row in all_pareto if str(row.get("candidate_type")) == "current")
        and all(str(row.get("primary_candidate_type")) != "current" or abs(finite(row.get("oracle_gate"))) <= 1.0e-12 for row in all_regions if not bool(row.get("adaptive_worthwhile", False)))
    )
    manifest = {
        "schema_id": "pgrcc_oracle_headroom_audit_v1",
        "mode": "oracle_headroom_audit",
        "config": repo_path(config_path),
        "output_dir": repo_path(out),
        "provenance": provenance,
        "cases": case_rows,
        "case_count_requested": len(cases),
        "case_count_completed": len(case_rows),
        "region_count": len(all_regions),
        "pareto_candidate_count": len(all_pareto),
        "exact_representative_count": len(all_exact),
        "failures": failures,
        "expansion_records": expansion_records,
        "method_config": method_config,
        "region_config": region_config,
        "worthwhile_policy": policy,
        "inference_feature_names": FEATURE_NAMES,
        "forbidden_inference_features": config.get("provenance", {}).get("forbidden_inference_features", []),
        "target_truth_policy": "target truth and paired C+N are evaluation/label only; never inference feature",
        "frozen_benchmark_outputs": config.get("provenance", {}).get("frozen_benchmark_outputs", []),
        "identity_test": identity_test,
        "go_no_go": {
            "decision": "GO_PGRCC_V1" if go else "NO_GO_ORACLE_NOT_SUFFICIENT",
            "adaptive_worthwhile_fraction": overall_fraction,
            "median_oracle_gain_over_current_dB": median_gain,
            "median_worthwhile_oracle_gain_over_current_dB": median_worthwhile_gain,
            "minimum_worthwhile_fraction": go_threshold_fraction,
            "minimum_median_gain_dB": go_threshold_gain_db,
            "predictor_leave_one_seed_out_available": predictor_ok,
            "rule": "GO only if worthwhile regions, median gain, grouped predictor evidence, and all cases pass; thresholds were fixed in this audit run",
        },
        "runtime_seconds": time.perf_counter() - started,
        "status": "ok" if not failures else "failed_cases_present",
    }
    write_json(out / "oracle_audit_manifest.json", manifest)
    print(
        f"[pgrcc-oracle] decision={manifest['go_no_go']['decision']} "
        f"regions={len(all_regions)} worthwhile_fraction={overall_fraction:.4f} "
        f"median_gain_dB={median_gain:.4f} failures={len(failures)}",
        flush=True,
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[pgrcc-oracle][FATAL] {exc}", file=sys.stderr)
        raise
