#!/usr/bin/env python3
"""Baseline V2 for the physics-controlled GMTI clutter-cancellation study.

The V2 runner keeps the original V1 replay available, but makes the scientific
comparison explicit and auditable:

* production replay keeps the target-observation P38 path used by Current;
* scientific controlled rows estimate every adaptive quantity from paired C+N;
* four-channel methods use geometry-derived spatial/space-time steering;
* covariance policy, sample counts, condition numbers, rank, loading and
  shrinkage are recorded with every method result;
* the velocity sweep regenerates Stage2 data instead of shifting an RD matrix.

The runner intentionally removes generated BINs after each case.  Use
``--keep-data`` only for a single debugging case; it is not the default
research path.
"""

from __future__ import annotations

import argparse
import copy
import csv
import concurrent.futures
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti_baseline_v2_matplotlib")
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


STRICT_METHODS = [
    "Current legacy CSI [production replay]",
    "Phase-only CSI [production replay]",
    "Current legacy CSI [scientific controlled]",
    "Phase-only CSI [scientific controlled]",
    "Row complex LS / Wiener [scientific controlled]",
    "Row complex LS / Wiener robust IRLS [scientific controlled]",
]
ADAPTIVE_METHODS = [
    "DL-SMI/MVDR global",
    "DL-SMI/MVDR local",
    "Shrinkage-SMI/MVDR global",
    "Shrinkage-SMI/MVDR local",
    "Corrected JDL physical",
    "MNEC physical",
    "SA-MNEC physical [approximate academic reference]",
]
METHODS = STRICT_METHODS + ADAPTIVE_METHODS
JDL_OFFSETS = (-1, 0, 1)
METHOD_PALETTE = {
    "Current legacy CSI [production replay]": "#1f77b4",
    "Phase-only CSI [production replay]": "#ff7f0e",
    "Current legacy CSI [scientific controlled]": "#5b8db8",
    "Phase-only CSI [scientific controlled]": "#e69f49",
    "Row complex LS / Wiener [scientific controlled]": "#2ca02c",
    "Row complex LS / Wiener robust IRLS [scientific controlled]": "#006d2c",
    "DL-SMI/MVDR global": "#9467bd",
    "DL-SMI/MVDR local": "#8c6bb1",
    "Shrinkage-SMI/MVDR global": "#d62728",
    "Shrinkage-SMI/MVDR local": "#b94e4e",
    "Corrected JDL physical": "#17becf",
    "MNEC physical": "#bcbd22",
    "SA-MNEC physical [approximate academic reference]": "#7f7f7f",
}
DEFAULT_ROC_SCALES = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0)


def db(value: float) -> float:
    return float(10.0 * math.log10(max(float(value), 1.0e-300)))


def db20(value: float) -> float:
    return float(20.0 * math.log10(max(float(abs(value)), 1.0e-300)))


def relative_repo_path(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace(os.sep, "/")


def write_csv(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    materialized = list(rows)
    if not materialized:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: List[str] = []
    for row in materialized:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(materialized)


def json_default(value: object) -> object:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (complex, np.complexfloating)):
        return {"real": float(np.real(value)), "imag": float(np.imag(value))}
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value)!r}")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def finite_or_nan(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def ci95(values: Sequence[float]) -> Tuple[float, float]:
    finite = np.asarray([float(v) for v in values if math.isfinite(float(v))], dtype=float)
    if finite.size == 0:
        return math.nan, math.nan
    mean = float(np.mean(finite))
    if finite.size < 2:
        return mean, math.nan
    # Continuous seed-level summaries use Student-t quantiles when n is
    # small.  Wilson intervals remain the Bernoulli-specific path below.
    critical = student_t_critical_975(int(finite.size - 1))
    half = critical * float(np.std(finite, ddof=1)) / math.sqrt(float(finite.size))
    return mean - half, mean + half


def student_t_critical_975(df: int) -> float:
    table = {
        1: 12.7062047364, 2: 4.3026527297, 3: 3.1824463053,
        4: 2.7764451052, 5: 2.5705818356, 6: 2.4469118511,
        7: 2.3646242510, 8: 2.3060041350, 9: 2.2621571629,
        10: 2.22813885196, 11: 2.2009851601, 12: 2.1788128297,
        13: 2.1603686565, 14: 2.1447866879, 15: 2.1314495456,
        16: 2.1199052992, 17: 2.1098155778, 18: 2.1009220402,
        19: 2.0930240544, 20: 2.0859634473, 21: 2.0796138447,
        22: 2.0738730679, 23: 2.0686576104, 24: 2.0638985616,
        25: 2.0595385528, 26: 2.0555294386, 27: 2.0518305165,
        28: 2.0484071418, 29: 2.0452296421, 30: 2.0422724563,
    }
    return float(table.get(int(df), 1.9599639845))


def wilson_ci95(successes: int, trials: int) -> Tuple[float, float]:
    """Bounded 95% interval for an empirical Bernoulli detection rate."""
    if trials <= 0:
        return math.nan, math.nan
    n = float(trials)
    z = 1.96
    phat = float(successes) / n
    denominator = 1.0 + z * z / n
    center = (phat + z * z / (2.0 * n)) / denominator
    half = z * math.sqrt(
        phat * (1.0 - phat) / n + z * z / (4.0 * n * n)
    ) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def rankdata(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    ranks = np.empty(values.size, dtype=float)
    start = 0
    while start < values.size:
        stop = start + 1
        while stop < values.size and sorted_values[stop] == sorted_values[start]:
            stop += 1
        ranks[order[start:stop]] = 0.5 * (start + 1 + stop)
        start = stop
    return ranks


def pearson(x: Sequence[float], y: Sequence[float]) -> float:
    xv = np.asarray(x, dtype=float)
    yv = np.asarray(y, dtype=float)
    valid = np.isfinite(xv) & np.isfinite(yv)
    if np.count_nonzero(valid) < 3:
        return math.nan
    xv = xv[valid]
    yv = yv[valid]
    if np.std(xv) <= 1.0e-12 or np.std(yv) <= 1.0e-12:
        return math.nan
    return float(np.corrcoef(xv, yv)[0, 1])


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    xv = np.asarray(x, dtype=float)
    yv = np.asarray(y, dtype=float)
    valid = np.isfinite(xv) & np.isfinite(yv)
    if np.count_nonzero(valid) < 3:
        return math.nan
    return pearson(rankdata(xv[valid]), rankdata(yv[valid]))


def load_suite(path: Path) -> Tuple[Path, Dict[str, object]]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    base = ROOT / str(suite["base_config"])
    if not base.exists():
        raise FileNotFoundError(f"base Stage2 config not found: {base}")
    if not suite.get("output_root"):
        raise ValueError("V2 suite must define output_root")
    return base, suite


def _base_case(suite: Dict[str, object]) -> Dict[str, object]:
    base = copy.deepcopy(suite.get("base_case", {}))
    if not base:
        base = copy.deepcopy(suite.get("screen", {}).get("base_case", {}))
    if not base:
        raise ValueError("V2 suite must define base_case or screen.base_case")
    return base


def _set_factor(case: Dict[str, object], factor: str, level: object) -> None:
    impairments = case.setdefault("impairments", {})
    motion = case.setdefault("motion", {})
    scene = case.setdefault("scene_overrides", {})
    if factor in {"texture_sigma", "clutter_texture_sigma"}:
        case["texture_sigma"] = float(level)
    elif factor == "azimuth_subcell_count":
        case["azimuth_subcell_count"] = int(level)
    elif factor == "channel_amp_mismatch_db":
        impairments["enabled"] = True
        impairments["channel_amp_mismatch_db"] = float(level)
    elif factor == "channel_fixed_phase_mismatch_deg":
        impairments["enabled"] = True
        impairments["channel_fixed_phase_mismatch_deg"] = float(level)
    elif factor == "channel_phase_jitter_std_deg":
        impairments["enabled"] = True
        impairments["channel_phase_jitter_std_deg"] = float(level)
    elif factor == "valid_sample_fraction":
        impairments["enabled"] = True
        impairments["channel_drop_probability"] = max(0.0, min(1.0, 1.0 - float(level)))
    elif factor == "target_snr_db":
        case["target_snr_db"] = float(level)
    elif factor == "target_radial_speed_mps":
        speed = float(level)
        case["velocity_mode"] = "radial"
        case["radial_speed_mps"] = speed
    elif factor == "support_edge_radial_speed_mps":
        speed = float(level)
        case["velocity_mode"] = "radial"
        case["radial_speed_mps"] = speed
    else:
        raise ValueError(f"unknown V2 sweep factor: {factor}")
    case["factor"] = factor
    case["factor_level"] = level
    case.setdefault("scene_overrides", scene)
    impairment_enabled = bool(impairments.get("enabled", False))
    case["use_paired_background"] = not impairment_enabled


def _case_id(base_id: str, factor: str, level: object, seed: int) -> str:
    safe_factor = factor.replace("_", "")
    safe_level = str(level).replace("-", "m").replace(".", "p")
    return f"{base_id}_{safe_factor}{safe_level}_s{seed}"


def expand_cases(suite: Dict[str, object], mode: str) -> List[Dict[str, object]]:
    if mode == "explicit":
        cases = copy.deepcopy(suite.get("cases", []))
        if not cases:
            raise ValueError("explicit mode requires suite.cases")
        return cases
    if mode == "screen":
        screen = suite.get("screen", {})
        seeds = [int(v) for v in screen.get("seeds", [])]
        base = _base_case(suite)
        factors = screen.get("single_factor_sweeps", {})
    elif mode == "formal":
        formal = suite.get("formal", {})
        seeds = [int(v) for v in formal.get("seeds", [])]
        base = _base_case(suite)
        factors = formal.get("single_factor_sweeps", {})
    elif mode == "velocity":
        velocity = suite.get("velocity_sweep", {})
        seeds = [int(v) for v in velocity.get("seeds", [])]
        base = _base_case(suite)
        factors = {"target_radial_speed_mps": velocity.get("levels", [])}
    elif mode == "roc":
        roc = suite.get("roc_sweep", {})
        seeds = [int(v) for v in roc.get("seeds", [])]
        base = _base_case(suite)
        factors = roc.get("single_factor_sweeps", {"target_snr_db": [10.0]})
    elif mode == "transition":
        transition = suite.get("transition_sweep", {})
        base = _base_case(suite)
        pilot_seeds = [int(v) for v in transition.get("pilot_seeds", [])]
        refined_seeds = [int(v) for v in transition.get("refined_seeds", [])]
        pilot_levels = [float(v) for v in transition.get("pilot_levels_db", [])]
        refined_levels = [float(v) for v in transition.get("refined_levels_db", [])]
        output: List[Dict[str, object]] = []
        base_id = str(base.get("case_id", "baseline_v21"))
        for label, levels, mode_seeds in (
            ("pilot", pilot_levels, pilot_seeds),
            ("refined", refined_levels, refined_seeds),
        ):
            for level in levels:
                for seed in mode_seeds:
                    case = copy.deepcopy(base)
                    case["seed"] = seed
                    case["case_id"] = _case_id(base_id, f"transition_{label}_target_snr_db", level, seed)
                    case["target_id"] = f"{base.get('target_id', 'V21_TARGET')}_transition_{label}_{level}_{seed}"
                    _set_factor(case, "target_snr_db", level)
                    case["factor"] = "target_snr_db"
                    case["factor_family"] = "transition"
                    case["transition_phase"] = label
                    output.append(case)
        if not output:
            raise ValueError("transition_sweep must define pilot/refined levels and seeds")
        return output
    elif mode == "mixed_stress":
        stress = suite.get("mixed_stress", {})
        base = _base_case(suite)
        count = int(stress.get("case_count", 0))
        seeds = [int(v) for v in stress.get("seeds", [])]
        dimensions = stress.get("dimensions", {})
        if count <= 0 or len(seeds) < count or not isinstance(dimensions, dict) or not dimensions:
            raise ValueError("mixed_stress requires case_count, at least that many seeds, and dimensions")
        rng = np.random.default_rng(int(stress.get("lhs_seed", 20261150)))
        permutations = {str(name): rng.permutation(count) for name in dimensions}
        output: List[Dict[str, object]] = []
        base_id = str(base.get("case_id", "baseline_v21"))
        for index in range(count):
            case = copy.deepcopy(base)
            case["seed"] = seeds[index]
            case["case_id"] = f"{base_id}_mixed_stress_lhs{index + 1:02d}_s{seeds[index]}"
            case["target_id"] = f"{base.get('target_id', 'V21_TARGET')}_mixed_stress_{index + 1}_{seeds[index]}"
            applied: Dict[str, float] = {}
            for name, bounds in dimensions.items():
                if not isinstance(bounds, list) or len(bounds) != 2:
                    raise ValueError(f"mixed_stress dimension {name} must be [low, high]")
                low, high = float(bounds[0]), float(bounds[1])
                unit = (float(permutations[str(name)][index]) + float(rng.random())) / float(count)
                level = low + (high - low) * unit
                _set_factor(case, str(name), level)
                applied[str(name)] = level
            case["factor"] = "mixed_stress"
            case["factor_family"] = "mixed_stress"
            case["factor_level"] = f"lhs_{index + 1:02d}"
            case["mixed_stress_values"] = applied
            output.append(case)
        return output
    else:
        raise ValueError(f"unsupported case expansion mode: {mode}")
    if not seeds or not factors:
        raise ValueError(f"{mode} suite has no seeds or factors")
    output: List[Dict[str, object]] = []
    base_id = str(base.get("case_id", "baseline_v2"))
    for factor, levels in factors.items():
        if not isinstance(levels, list) or not levels:
            raise ValueError(f"factor {factor} must have non-empty levels")
        for level in levels:
            for seed in seeds:
                case = copy.deepcopy(base)
                case["seed"] = seed
                case["case_id"] = _case_id(base_id, factor, level, seed)
                case["target_id"] = f"{base.get('target_id', 'V2_TARGET')}_{factor}_{level}_{seed}"
                _set_factor(case, factor, level)
                output.append(case)
    return output


def physical_look_vector(theta_deg: float, squint_side: int) -> np.ndarray:
    theta = math.radians(float(theta_deg))
    if int(squint_side) == 1:
        # Stage2's algorithm-axis mapping is side_dir - theta.  With the
        # default local x=north/y=east convention this gives north=-sin(theta)
        # on the left-looking side.
        along = -math.sin(theta)
        cross = -math.cos(theta)
    else:
        along = math.sin(theta)
        cross = math.cos(theta)
    return np.asarray([along, cross, 0.0], dtype=float)


def range_at_column(col: float, p: oracle.Params) -> float:
    sample = float(p.range_crop_start) + float(col)
    return 0.5 * oracle.C0 * (p.sample_delay_s + sample / p.fs_hz)


def path_offset(reference_range_m: float, los: np.ndarray, offset: np.ndarray) -> float:
    dot = float(np.dot(los, offset))
    offset_sq = float(np.dot(offset, offset))
    normalized = 1.0 - 2.0 * dot / reference_range_m + offset_sq / reference_range_m**2
    radial = math.sqrt(max(0.0, normalized))
    return (-2.0 * dot + offset_sq / reference_range_m) / max(radial + 1.0, 1.0e-12)


def spatial_steering(theta_deg: float, range_m: float, geometry: oracle.Params) -> np.ndarray:
    """Configured target spatial steering for one range block.

    Doppler is deliberately absent from this function.  The range-dependent
    receive path uses only the Stage2 geometry and the beam-center hypothesis.
    """

    range_m = float(range_m)
    if not math.isfinite(range_m) or range_m <= 0.0:
        raise ValueError(f"range_m must be finite and positive, got {range_m!r}")
    height = float(getattr(geometry, "platform_height_m", 6000.0))
    ground_sq = max(1.0, range_m * range_m - height * height)
    ground = math.sqrt(ground_sq)
    look = physical_look_vector(theta_deg, geometry.squint_side)
    los = np.asarray([ground * look[0], ground * look[1], -height], dtype=float) / range_m
    offsets = np.asarray(geometry.offsets_m, dtype=float)
    if offsets.shape != (4, 3):
        raise ValueError(f"expected four channel offsets, got {offsets.shape}")
    reference = path_offset(range_m, los, offsets[0])
    phase = np.asarray(
        [geometry.carrier_phase_sign * 2.0 * math.pi / (oracle.C0 / geometry.fc_hz) *
         (path_offset(range_m, los, offset) - reference) for offset in offsets],
        dtype=float,
    )
    steering = np.exp(1j * phase)
    return steering / max(np.linalg.norm(steering), 1.0e-12) * 2.0


def channel_steering(theta_deg: float, p: oracle.Params, range_m: float) -> np.ndarray:
    """Compatibility alias for callers outside the V2 runner."""

    return spatial_steering(theta_deg, range_m, p)


def clutter_angle_from_doppler(fd_rel_hz: float, p: oracle.Params) -> float:
    """Stationary-clutter ridge angle, for diagnostics/features only."""

    speed = max(abs(float(getattr(p, "platform_speed_mps", 60.0))), 1.0e-12)
    wavelength = oracle.C0 / p.fc_hz
    argument = float(fd_rel_hz) * wavelength / max(2.0 * speed, 1.0e-12)
    return math.degrees(math.asin(max(-1.0, min(1.0, argument))))


def temporal_response(fd_rel_hz: float, row_fd_rel_hz: float, p: oracle.Params) -> complex:
    n = np.arange(p.pulse_num, dtype=float)
    return complex(np.mean(np.exp(2j * math.pi * (fd_rel_hz - row_fd_rel_hz) * n / p.prf_hz)))


def space_time_steering(
    theta_deg: float,
    fd_rel_hz: float,
    row: int,
    axis: np.ndarray,
    fa_ctr: float,
    p: oracle.Params,
    range_m: float,
    offsets: Sequence[int] = JDL_OFFSETS,
) -> np.ndarray:
    spatial = spatial_steering(theta_deg, range_m, p)
    parts: List[np.ndarray] = []
    for offset in offsets:
        feature_row = (row + int(offset)) % p.pulse_num
        feature_fd = float(axis[feature_row] - fa_ctr)
        temporal = temporal_response(fd_rel_hz, feature_fd, p)
        parts.append(temporal * spatial)
    vector = np.concatenate(parts).astype(np.complex128)
    return vector / max(np.linalg.norm(vector), 1.0e-12) * math.sqrt(len(parts))


def loaded_covariance(
    samples: np.ndarray,
    shrink: bool,
    loading_fraction: float,
) -> Tuple[np.ndarray, Dict[str, float]]:
    samples = np.asarray(samples, dtype=np.complex128)
    if samples.ndim != 2:
        raise ValueError(f"covariance samples must be 2-D, got {samples.shape}")
    finite = np.all(np.isfinite(samples.real) & np.isfinite(samples.imag), axis=1)
    norms = np.linalg.norm(samples, axis=1)
    valid = finite & (norms > 1.0e-12)
    usable = samples[valid]
    if usable.shape[0] == 0:
        usable = samples[:1]
    n, dim = usable.shape
    covariance = usable.conj().T @ usable / max(n, 1)
    covariance = 0.5 * (covariance + covariance.conj().T)
    trace = max(float(np.trace(covariance).real), 1.0e-12)
    mu = trace / dim
    shrinkage = 0.0
    if shrink:
        target = mu * np.eye(dim, dtype=np.complex128)
        deviations = usable[:, :, None] * usable[:, None, :].conj() - covariance[None, :, :]
        phi = float(np.mean(np.sum(np.abs(deviations) ** 2, axis=(1, 2))))
        rho = float(np.sum(np.abs(covariance - target) ** 2))
        shrinkage = max(0.0, min(1.0, phi / max(float(n) * rho, 1.0e-12)))
        covariance = (1.0 - shrinkage) * covariance + shrinkage * target
    loading = max(float(loading_fraction) * trace / dim, 1.0e-12)
    loaded = covariance + loading * np.eye(dim, dtype=np.complex128)
    eigenvalues = np.linalg.eigvalsh(covariance).real
    positive = np.maximum(eigenvalues, 0.0)
    median = max(float(np.median(positive)), 1.0e-12)
    rank = int(np.count_nonzero(positive > 3.0 * median))
    if np.all(np.isfinite(covariance)) and eigenvalues.size:
        # The covariance is Hermitian PSD; reuse its eigenvalues instead of
        # performing a second SVD solely for the condition estimate.
        condition = float(
            np.max(np.abs(eigenvalues))
            / max(float(np.min(np.abs(eigenvalues))), 1.0e-12)
        )
    else:
        condition = math.inf
    return loaded, {
        "training_sample_count": float(n),
        "training_valid_fraction": float(np.count_nonzero(valid) / max(samples.shape[0], 1)),
        "covariance_condition_number": condition,
        "estimated_clutter_rank": float(max(0, min(dim, rank))),
        "loading_coefficient": float(loading),
        "shrinkage_coefficient": float(shrinkage),
    }


def mvdr_weight(covariance: np.ndarray, steering: np.ndarray) -> np.ndarray:
    try:
        solved = np.linalg.solve(covariance, steering)
    except np.linalg.LinAlgError:
        solved = np.linalg.pinv(covariance, rcond=1.0e-8) @ steering
    denominator = np.vdot(steering, solved)
    if abs(denominator) <= 1.0e-12:
        return steering / max(np.linalg.norm(steering), 1.0e-12)
    return solved / denominator


def mnec_weight(covariance: np.ndarray, steering: np.ndarray, rank: int) -> np.ndarray:
    values, vectors = np.linalg.eigh(0.5 * (covariance + covariance.conj().T))
    dim = covariance.shape[0]
    rank = max(1, min(dim - 1, int(rank))) if dim > 1 else 0
    clutter = vectors[:, -rank:] if rank else np.zeros((dim, 0), dtype=np.complex128)
    projector = np.eye(dim, dtype=np.complex128) - clutter @ clutter.conj().T
    projected = projector @ steering
    denominator = np.vdot(steering, projected)
    if abs(denominator) <= 1.0e-12:
        return mvdr_weight(covariance, steering)
    return projected / denominator


def block_edges(start: int, stop: int, block_size: int) -> List[Tuple[int, int]]:
    edges = []
    for lo in range(start, stop, max(1, int(block_size))):
        edges.append((lo, min(stop, lo + max(1, int(block_size)))))
    return edges


def training_columns(
    center: int,
    p: oracle.Params,
    mode: str,
    local_half_width: int,
    guard: int,
) -> np.ndarray:
    start = max(0, int(p.rg_st))
    stop = min(int(getattr(p, "range_compress_len", p.rg_ed + 1)), int(p.rg_ed) + 1)
    if mode == "local":
        lo = max(start, center - int(local_half_width))
        hi = min(stop, center + int(local_half_width) + 1)
    else:
        lo, hi = start, stop
    cols = np.arange(lo, hi, dtype=int)
    if guard > 0 and cols.size:
        cols = cols[(np.abs(cols - center) > int(guard))]
    return cols


@dataclass
class WeightModel:
    method: str
    kind: str
    training_mode: str
    weights: np.ndarray
    block_edges: List[Tuple[int, int]]
    scale: np.ndarray | None
    row_stats: Dict[str, float]
    steering_error_mean: float
    steering_error_p95: float
    details: Dict[str, object]


def _feature_tensor(rd: np.ndarray, offsets: Sequence[int]) -> np.ndarray:
    return np.concatenate([np.roll(rd, -int(offset), axis=0) for offset in offsets], axis=2)


def target_beam_angle_deg(data: v1.CaseData) -> float:
    """Return the configured beam-center target hypothesis, not target truth."""

    p = data.p
    beam_id = int(data.case.get("beam_id", 31))
    return float(
        p.scan_min_deg
        + (beam_id - 1) * p.scan_step_deg
        + p.beam_theta_offset_deg
    )


def _physical_steering_for_row(
    data: v1.CaseData, row: int, range_m: float
) -> np.ndarray:
    return spatial_steering(target_beam_angle_deg(data), range_m, data.p)


def _jdl_steering_for_row(data: v1.CaseData, row: int, range_m: float) -> np.ndarray:
    return space_time_steering(
        target_beam_angle_deg(data),
        float(data.axis[row] - data.fa_ctr),
        row,
        data.axis,
        data.fa_ctr,
        data.p,
        range_m,
        JDL_OFFSETS,
    )


def build_weight_model(data: v1.CaseData, method: str, config: Dict[str, object]) -> WeightModel:
    started = time.perf_counter()
    p = data.p
    block_size = int(config.get("range_block_size", 64))
    local_half_width = int(config.get("local_training_half_width", 128))
    guard = int(config.get("cut_guard_bins", 4))
    loading_fraction = float(config.get("diagonal_loading_fraction", 1.0e-2))
    edges = block_edges(
        max(0, p.rg_st),
        min(int(getattr(p, "range_compress_len", p.rg_ed + 1)), int(p.rg_ed) + 1),
        block_size,
    )
    if not edges:
        raise RuntimeError("no valid training range columns")
    method_lower = method.lower()
    is_jdl = method.startswith("Corrected JDL")
    is_sa = method.startswith("SA-MNEC")
    is_mnec = method.startswith("MNEC")
    is_shrink = method.startswith("Shrinkage")
    mode = "local" if " local" in method else "global"
    dim = 12 if is_jdl else 4
    weights = np.zeros((p.pulse_num, len(edges), dim), dtype=np.complex128)
    stat_values: Dict[str, List[float]] = {
        "training_sample_count": [],
        "training_valid_fraction": [],
        "covariance_condition_number": [],
        "estimated_clutter_rank": [],
        "loading_coefficient": [],
        "shrinkage_coefficient": [],
    }
    distortion: List[float] = []
    scale: np.ndarray | None = None

    feature_bg = _feature_tensor(data.raw_bg_rd, JDL_OFFSETS) if is_jdl else None
    if is_sa:
        train_cols = training_columns(
            int(0.5 * (p.rg_st + p.rg_ed)), p, mode, local_half_width, guard
        )
        scale = np.sqrt(np.mean(np.abs(data.raw_bg_rd[:, train_cols, :]) ** 2, axis=(0, 1)))
        scale = np.maximum(scale, 1.0e-12)
        normalized = data.raw_bg_compressed / scale[None, None, :]
        segments = [segment for segment in np.array_split(np.arange(p.pulse_num), 4) if len(segment)]
    else:
        normalized = None
        segments = []

    for row in range(p.pulse_num):
        if is_sa:
            frequency = data.axis[row] - data.fa_ctr
            snapshot_parts: List[np.ndarray] = []
            for segment in segments:
                phase = np.exp(-2j * math.pi * frequency * segment / p.prf_hz)
                snapshot_parts.append(
                    np.sum(normalized[segment] * phase[:, None, None], axis=0) / len(segment)
                )
        for block_index, (lo, hi) in enumerate(edges):
            center = (lo + hi - 1) // 2
            range_m = range_at_column(center, p)
            spatial = _physical_steering_for_row(data, row, range_m)
            steering = _jdl_steering_for_row(data, row, range_m) if is_jdl else spatial
            cols = training_columns(center, p, mode, local_half_width, guard)
            if is_sa:
                # Each subaperture contributes a covariance snapshot.  This is
                # the intentionally approximate SA-MNEC reference, not a paper
                # reproduction with additional calibration stages.
                samples = np.stack([part[cols] for part in snapshot_parts], axis=0).reshape(-1, 4)
                covariance, stats = loaded_covariance(samples, False, loading_fraction)
                rank = int(stats["estimated_clutter_rank"])
                weight = mnec_weight(covariance, spatial, rank)
            else:
                if is_jdl:
                    samples = feature_bg[row, cols, :]
                else:
                    samples = data.raw_bg_rd[row, cols, :]
                covariance, stats = loaded_covariance(samples, is_shrink, loading_fraction)
                if is_mnec:
                    weight = mnec_weight(covariance, steering, int(stats["estimated_clutter_rank"]))
                else:
                    weight = mvdr_weight(covariance, steering)
            weights[row, block_index, :] = weight
            for key in stat_values:
                stat_values[key].append(float(stats[key]))
            distortion.append(abs(np.vdot(weight, steering) - 1.0))

    def mean_stat(key: str) -> float:
        values = np.asarray(stat_values[key], dtype=float)
        values = values[np.isfinite(values)]
        return float(np.mean(values)) if values.size else math.nan

    condition = np.asarray(stat_values["covariance_condition_number"], dtype=float)
    condition = condition[np.isfinite(condition)]
    distortion_array = np.asarray(distortion, dtype=float)
    details = {
        "method_kind": "SA-MNEC" if is_sa else ("JDL" if is_jdl else ("MNEC" if is_mnec else "MVDR")),
        "training_mode": mode,
        "range_block_size": block_size,
        "local_training_half_width": local_half_width,
        "cut_guard_bins": guard,
        "diagonal_loading_fraction": loading_fraction,
        "weight_build_runtime_ms": 1000.0 * (time.perf_counter() - started),
        "steering_source": "configured beam-center spatial hypothesis; Doppler row used only for temporal/JDL frequency",
        "covariance_training_source": "paired C+N background only",
    }
    return WeightModel(
        method=method,
        kind=details["method_kind"],
        training_mode=mode,
        weights=weights,
        block_edges=edges,
        scale=scale,
        row_stats={key: mean_stat(key) for key in stat_values}
        | {
            "covariance_condition_number_p95": float(np.percentile(condition, 95.0)) if condition.size else math.nan,
            "steering_projection_error_max": float(np.max(distortion_array)) if distortion_array.size else math.nan,
        },
        steering_error_mean=float(np.mean(distortion_array)) if distortion_array.size else math.nan,
        steering_error_p95=float(np.percentile(distortion_array, 95.0)) if distortion_array.size else math.nan,
        details=details,
    )


def apply_weight_model(
    data: v1.CaseData,
    model: WeightModel,
    rd_bg: np.ndarray,
    rd_target: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    is_jdl = model.kind == "JDL"
    is_sa = model.kind == "SA-MNEC"
    if is_sa:
        assert model.scale is not None
        bg_input = rd_bg / model.scale[None, None, :]
        target_input = rd_target / model.scale[None, None, :]
    else:
        bg_input = rd_bg
        target_input = rd_target
    feature_bg = _feature_tensor(bg_input, JDL_OFFSETS) if is_jdl else None
    feature_target = _feature_tensor(target_input, JDL_OFFSETS) if is_jdl else None
    bg = np.zeros(bg_input.shape[:2], dtype=np.complex128)
    target = np.zeros(target_input.shape[:2], dtype=np.complex128)
    for block_index, (lo, hi) in enumerate(model.block_edges):
        weight = model.weights[:, block_index, :]
        if is_jdl:
            bg[:, lo:hi] = np.einsum("md,mrd->mr", weight.conj(), feature_bg[:, lo:hi, :], optimize=True)
            target[:, lo:hi] = np.einsum("md,mrd->mr", weight.conj(), feature_target[:, lo:hi, :], optimize=True)
        else:
            bg[:, lo:hi] = np.einsum("mc,mrc->mr", weight.conj(), bg_input[:, lo:hi, :], optimize=True)
            target[:, lo:hi] = np.einsum("mc,mrc->mr", weight.conj(), target_input[:, lo:hi, :], optimize=True)
    return bg, target, bg_input[:, :, 0], target_input[:, :, 0]


def background_cfar(
    output: np.ndarray,
    p: oracle.Params,
    threshold_scale: float = 1.0,
) -> Dict[str, float]:
    rows, cols = output.shape
    radius = p.cfar_guard + p.cfar_background
    if cols <= 2 * radius:
        return {
            "false_alarm_count": math.nan,
            "background_Pfa": math.nan,
            "cfar_test_cells": 0.0,
            "cfar_alpha": math.nan,
        }
    result = oracle.go_cfar(output, rows + 100, cols + 100, p, threshold_scale=threshold_scale)
    test_cells = float(rows * (cols - 2 * radius))
    count = float(result["false_alarm_count"])
    return {
        "false_alarm_count": count,
        "background_Pfa": count / max(test_cells, 1.0),
        "cfar_test_cells": test_cells,
        "cfar_alpha": float(result["cfar_alpha"]),
    }


def output_metrics(
    data: v1.CaseData,
    bg: np.ndarray,
    target: np.ndarray,
    detector_bg: np.ndarray,
    detector_target: np.ndarray,
    input_bg: np.ndarray,
    input_target: np.ndarray,
    roc_scales: Sequence[float] | None = None,
    detection_threshold_scale: float = 1.0,
) -> Dict[str, object]:
    rows, cols = bg.shape
    col_start = max(0, min(cols, int(data.p.rg_st)))
    col_stop = max(col_start + 1, min(cols, int(data.p.rg_ed) + 1))
    input_power = np.abs(input_bg[:, col_start:col_stop]) ** 2
    residual_power = np.abs(bg[:, col_start:col_stop]) ** 2
    input_median = float(np.median(input_power))
    residual_p95 = float(np.percentile(residual_power, 95.0))
    residual_p99 = float(np.percentile(residual_power, 99.0))
    cvar_threshold = residual_p95
    cvar_power = float(np.mean(residual_power[residual_power >= cvar_threshold]))
    r0 = max(0, data.truth_row - 2)
    r1 = min(rows, data.truth_row + 3)
    c0 = max(0, data.truth_col - 2)
    c1 = min(cols, data.truth_col + 3)
    roi = (slice(r0, r1), slice(c0, c1))
    before_clutter = float(np.mean(np.abs(input_bg[roi]) ** 2))
    after_clutter = float(np.mean(np.abs(bg[roi]) ** 2))
    signal_before = input_target[roi] - input_bg[roi]
    signal_after = target[roi] - bg[roi]
    signal_power_before = float(np.mean(np.abs(signal_before) ** 2))
    signal_power_after = float(np.mean(np.abs(signal_after) ** 2))
    input_scnr = db(signal_power_before / max(before_clutter, 1.0e-300))
    output_scnr = db(signal_power_after / max(after_clutter, 1.0e-300))
    if not math.isfinite(float(detection_threshold_scale)) or float(detection_threshold_scale) <= 0.0:
        raise ValueError(f"detection_threshold_scale must be finite and positive, got {detection_threshold_scale!r}")
    target_cfar = oracle.go_cfar(
        detector_target, data.truth_row, data.truth_col, data.p,
        threshold_scale=float(detection_threshold_scale),
    )
    cfar_bg = background_cfar(
        detector_bg, data.p, threshold_scale=float(detection_threshold_scale)
    )
    high_threshold = input_median * 10.0 ** (15.0 / 10.0)
    result: Dict[str, object] = {
        "residual_suppression_dB": db(np.mean(input_power) / max(np.mean(residual_power), 1.0e-300)),
        "input_SCNR_dB": input_scnr,
        "output_SCNR_dB": output_scnr,
        "SCNR_improvement_dB": output_scnr - input_scnr,
        "target_loss_dB": db(signal_power_after / max(signal_power_before, 1.0e-300)),
        "background_residual_p95_dB_over_input_median": db(residual_p95 / max(input_median, 1.0e-300)),
        "background_residual_p99_dB_over_input_median": db(residual_p99 / max(input_median, 1.0e-300)),
        "background_residual_cvar95_dB_over_input_median": db(cvar_power / max(input_median, 1.0e-300)),
        "high_tail_fraction_15dB": float(np.count_nonzero(residual_power > high_threshold) / max(residual_power.size, 1)),
        "target_signal_power_before": signal_power_before,
        "target_signal_power_after": signal_power_after,
        "background_power_before_roi": before_clutter,
        "background_power_after_roi": after_clutter,
        "target_detected": float(target_cfar["Pd"]),
        "detection_threshold_scale": float(detection_threshold_scale),
        "target_cfar_candidate_cells": float(target_cfar["cfar_candidate_cells"]),
        "target_peak_power": float(target_cfar["target_peak_power"]),
        **cfar_bg,
    }
    if roc_scales is not None:
        roc_points: List[Dict[str, float]] = []
        for scale in sorted({float(value) for value in roc_scales}):
            if not math.isfinite(scale) or scale <= 0.0:
                raise ValueError(f"ROC threshold scales must be finite and positive, got {scale!r}")
            roc_target = oracle.go_cfar(
                detector_target,
                data.truth_row,
                data.truth_col,
                data.p,
                threshold_scale=scale,
            )
            roc_background = background_cfar(detector_bg, data.p, threshold_scale=scale)
            roc_points.append(
                {
                    "threshold_scale": scale,
                    "target_detected": float(roc_target["Pd"]),
                    "background_Pfa": float(roc_background["background_Pfa"]),
                }
            )
        result["roc_points"] = roc_points
    return result


def robust_row_lsq_cancel(
    f1_bg: np.ndarray,
    f2_bg: np.ndarray,
    f1_apply: np.ndarray,
    f2_apply: np.ndarray,
    phase_rows: np.ndarray,
    support: np.ndarray,
    p: oracle.Params,
    huber_delta: float = 1.5,
    physics_regularization: float = 0.05,
) -> Tuple[np.ndarray, Dict[str, float]]:
    """Background-only robust complex row LS with a bounded physics prior."""

    out = f1_apply.copy()
    columns = np.arange(max(0, p.rg_st), min(f1_bg.shape[1], p.rg_ed + 1), dtype=int)
    if columns.size == 0:
        return out, {"condition_number": math.nan, "confidence": 0.0, "iterations": 0.0}
    alphas = np.zeros(f1_bg.shape[0], dtype=np.complex128)
    condition_values: List[float] = []
    confidence_values: List[float] = []
    iteration_values: List[float] = []
    prior_phase = np.asarray(phase_rows, dtype=float)
    for row in np.flatnonzero(support):
        x = np.asarray(f2_bg[row, columns], dtype=np.complex128)
        y = np.asarray(f1_bg[row, columns], dtype=np.complex128)
        energy = float(np.mean(np.abs(x) ** 2))
        if not math.isfinite(energy) or energy <= 1.0e-24:
            continue
        prior = np.exp(1j * prior_phase[row])
        alpha = prior
        iterations = 0
        for iterations in range(1, 6):
            residual = y - alpha * x
            center = float(np.median(np.abs(residual)))
            scale = max(1.4826 * center, 1.0e-12)
            threshold = max(float(huber_delta), 1.0e-6) * scale
            magnitude = np.abs(residual)
            weights = np.minimum(1.0, threshold / np.maximum(magnitude, 1.0e-12))
            weighted_energy = float(np.sum(weights * np.abs(x) ** 2))
            denom = weighted_energy + float(physics_regularization) * energy
            numerator = np.sum(weights * np.conj(x) * y) + float(physics_regularization) * energy * prior
            if denom <= 1.0e-24:
                break
            updated = numerator / denom
            if abs(updated - alpha) <= 1.0e-8 * max(1.0, abs(alpha)):
                alpha = updated
                break
            alpha = updated
        alphas[row] = alpha
        fitted = y - alpha * x
        residual_power = float(np.mean(np.abs(fitted) ** 2))
        confidence_values.append(float(energy / max(energy + residual_power, 1.0e-24)))
        # The row problem is scalar; report the regularized effective normal
        # equation conditioning rather than pretending to have a matrix rank.
        condition_values.append(float((weighted_energy + float(physics_regularization) * energy) / max(float(physics_regularization) * energy, 1.0e-24)))
        iteration_values.append(float(iterations))
        out[row] = f1_apply[row] - alpha * f2_apply[row]
    return out, {
        "condition_number": float(np.nanmean(condition_values)) if condition_values else math.nan,
        "confidence": float(np.nanmean(confidence_values)) if confidence_values else 0.0,
        "iterations": float(np.nanmean(iteration_values)) if iteration_values else 0.0,
    }


def run_strict_method(
    data: v1.CaseData,
    method: str,
    roc_scales: Sequence[float] | None = None,
    method_config: Mapping[str, object] | None = None,
    detection_threshold_scale: float = 1.0,
) -> Tuple[Dict[str, object], Dict[str, object]]:
    production = "production" in method
    alignment = data.alignment if production else data.background_alignment
    p38_phase = np.asarray(alignment.p38["phase_rows"])
    robust_details: Dict[str, float] = {}
    if method.startswith("Current"):
        bg = oracle.current_cancel(alignment.f1_bg, alignment.f2_bg, p38_phase, data.current_support)
        target = oracle.current_cancel(alignment.f1_target, alignment.f2_target, p38_phase, data.current_support)
    elif method.startswith("Phase-only"):
        bg = v1.phase_only_cancel(alignment.f1_bg, alignment.f2_bg, p38_phase, data.current_support)
        target = v1.phase_only_cancel(alignment.f1_target, alignment.f2_target, p38_phase, data.current_support)
    elif method.startswith("Row complex LS / Wiener robust"):
        config = method_config or {}
        robust_kwargs = {
            "huber_delta": float(config.get("robust_huber_delta", 1.5)),
            "physics_regularization": float(config.get("robust_physics_regularization", 0.05)),
        }
        bg, robust_details = robust_row_lsq_cancel(
            data.background_alignment.f1_bg,
            data.background_alignment.f2_bg,
            alignment.f1_bg,
            alignment.f2_bg,
            p38_phase,
            data.current_support,
            data.p,
            **robust_kwargs,
        )
        target, _ = robust_row_lsq_cancel(
            data.background_alignment.f1_bg,
            data.background_alignment.f2_bg,
            alignment.f1_target,
            alignment.f2_target,
            p38_phase,
            data.current_support,
            data.p,
            **robust_kwargs,
        )
    else:
        bg = oracle.direct_weight_cancel(alignment.f1_bg, alignment.f2_bg, alignment.alpha, data.current_support)
        target = oracle.direct_weight_cancel(alignment.f1_target, alignment.f2_target, alignment.alpha, data.current_support)
    detector_bg = v1.dynamic_detector(bg, alignment.f2_bg, data.current_support)
    detector_target = v1.dynamic_detector(target, alignment.f2_target, data.current_support)
    metrics = output_metrics(
        data,
        bg,
        target,
        detector_bg,
        detector_target,
        alignment.f1_bg,
        alignment.f1_target,
        roc_scales=roc_scales,
        detection_threshold_scale=detection_threshold_scale,
    )
    details = {
        "comparison_protocol": "production_replay" if production else "scientific_controlled",
        "method_family": "F1/F2 strict",
        "training_source": "target-observation P38" if production else "paired C+N background",
        "steering_source": "not applicable to two-channel CSI",
        "p38_fit_source": str(alignment.p38["source"]),
        "p38_rmse_rad": finite_or_nan(alignment.p38["rmse"]),
        "p38_inlier_ratio": finite_or_nan(alignment.p38["inlier_ratio"]),
        "p38_sample_count": finite_or_nan(alignment.p38["sample_count"]),
        "p38_k_rad_per_hz": finite_or_nan(alignment.p38["k"]),
        "p38_b_rad": finite_or_nan(alignment.p38["b"]),
        "covariance_condition_number": math.nan,
        "estimated_clutter_rank": math.nan,
        "loading_coefficient": math.nan,
        "shrinkage_coefficient": math.nan,
        "steering_projection_error_mean": math.nan,
        "steering_projection_error_p95": math.nan,
        "robust_lsq_condition_number": robust_details.get("condition_number", math.nan),
        "robust_lsq_confidence": robust_details.get("confidence", math.nan),
        "robust_lsq_iterations": robust_details.get("iterations", math.nan),
    }
    return metrics, details


def run_adaptive_method(
    data: v1.CaseData,
    method: str,
    config: Dict[str, object],
    roc_scales: Sequence[float] | None = None,
    detection_threshold_scale: float = 1.0,
) -> Tuple[Dict[str, object], Dict[str, object]]:
    model = build_weight_model(data, method, config)
    bg, target, input_bg, input_target = apply_weight_model(
        data, model, data.raw_bg_rd, data.raw_target_rd
    )
    metrics = output_metrics(
        data,
        bg,
        target,
        bg,
        target,
        input_bg,
        input_target,
        roc_scales=roc_scales,
        detection_threshold_scale=detection_threshold_scale,
    )
    details = {
        "comparison_protocol": "scientific_controlled",
        "method_family": "4-channel physical academic reference",
        "training_source": "paired C+N background only",
        "steering_source": "configured beam-center spatial hypothesis; Doppler row used only for temporal/JDL frequency",
        "academic_reference_status": "approximate academic reference" if method.startswith("SA-MNEC") else "corrected research baseline",
        "training_mode": model.training_mode,
        "training_sample_count": model.row_stats["training_sample_count"],
        "training_valid_fraction": model.row_stats["training_valid_fraction"],
        "training_channel_valid_fraction": data.valid_sample_fraction,
        "covariance_condition_number": model.row_stats["covariance_condition_number"],
        "covariance_condition_number_p95": model.row_stats["covariance_condition_number_p95"],
        "estimated_clutter_rank": model.row_stats["estimated_clutter_rank"],
        "loading_coefficient": model.row_stats["loading_coefficient"],
        "shrinkage_coefficient": model.row_stats["shrinkage_coefficient"],
        "steering_projection_error_mean": model.steering_error_mean,
        "steering_projection_error_p95": model.steering_error_p95,
        "weight_build_runtime_ms": model.details["weight_build_runtime_ms"],
        "cut_guard_bins": model.details["cut_guard_bins"],
        "range_block_size": model.details["range_block_size"],
    }
    return metrics, details


def feature_base(data: v1.CaseData) -> Dict[str, object]:
    alignment = data.background_alignment
    p38 = alignment.p38
    support_rows = np.flatnonzero(data.current_support)
    region = slice(max(0, data.p.rg_st), min(data.raw_bg_rd.shape[1], data.p.rg_ed + 1))
    f1_energy = float(np.mean(np.abs(alignment.f1_bg[:, region]) ** 2))
    f2_energy = float(np.mean(np.abs(alignment.f2_bg[:, region]) ** 2))
    texture_power = np.abs(alignment.f1_bg[support_rows, region]) ** 2 if support_rows.size else np.empty((0, 0))
    texture_row_power = np.mean(texture_power, axis=1) if texture_power.size else np.empty(0)
    texture_mean = float(np.mean(texture_row_power)) if texture_row_power.size else math.nan
    texture_nonuniformity_cv = (
        float(np.std(texture_row_power) / max(texture_mean, 1.0e-300))
        if texture_row_power.size else math.nan
    )
    coherence = np.asarray(alignment.coherence, dtype=float)
    phase = np.unwrap(np.asarray(alignment.row_phase, dtype=float)[support_rows]) if support_rows.size else np.empty(0)
    phase_variance = float(np.var(phase)) if phase.size else math.nan
    target_fd_offset = float(data.target_fd - data.fa_ctr)
    edge_distance = min(
        int(data.truth_row - support_rows[0]),
        int(support_rows[-1] - data.truth_row),
    ) if support_rows.size else math.nan
    return {
        "case_id": str(data.case["case_id"]),
        "factor": str(data.case.get("factor", "explicit")),
        "factor_family": str(data.case.get("factor_family", data.case.get("factor", "explicit"))),
        "factor_level": data.case.get("factor_level", "explicit"),
        "seed": int(data.case.get("seed", -1)),
        "scenario_label": str(data.case.get("label", data.case.get("case_id", ""))),
        "valid_pulse_fraction": float(data.valid_sample_fraction),
        "texture_sigma_config": finite_or_nan(data.case.get("texture_sigma")),
        "azimuth_subcell_count_config": finite_or_nan(data.case.get("azimuth_subcell_count")),
        "texture_nonuniformity_cv": texture_nonuniformity_cv,
        "p38_slope_rad_per_hz": finite_or_nan(p38["k"]),
        "p38_intercept_rad": finite_or_nan(p38["b"]),
        "p38_rmse_rad": finite_or_nan(p38["rmse"]),
        "p38_inlier_ratio": finite_or_nan(p38["inlier_ratio"]),
        "p38_accepted_sample_count": finite_or_nan(p38["sample_count"]),
        "coherence_mean": float(np.nanmean(coherence)) if coherence.size else math.nan,
        "coherence_p10": float(np.nanpercentile(coherence, 10.0)) if coherence.size else math.nan,
        "F1_F2_energy_ratio_dB": db(f1_energy / max(f2_energy, 1.0e-300)),
        "phase_variance_rad2": phase_variance,
        "distance_to_clutter_ridge_hz": abs(target_fd_offset),
        "distance_to_clutter_ridge_mps": abs(target_fd_offset) * (oracle.C0 / data.p.fc_hz) / 2.0,
        "clutter_ridge_angle_deg_diagnostic": clutter_angle_from_doppler(target_fd_offset, data.p),
        "target_beam_hypothesis_angle_deg": target_beam_angle_deg(data),
        "distance_to_support_edge_rows": edge_distance,
        "distance_to_support_edge_hz": float(edge_distance) * data.p.prf_hz / data.p.pulse_num if math.isfinite(float(edge_distance)) else math.nan,
        "target_fd_truth_hz": float(data.target_fd),
        "fa_ctr_hz": float(data.fa_ctr),
        "target_row_truth": int(data.truth_row),
        "target_range_bin_truth": int(data.truth_col),
        "dynamic_support_row_start": int(support_rows[0]) if support_rows.size else math.nan,
        "dynamic_support_row_end": int(support_rows[-1]) if support_rows.size else math.nan,
    }


def base_result_row(
    data: v1.CaseData,
    method: str,
    metrics: Dict[str, float],
    details: Dict[str, object],
    runtime_ms: float,
) -> Dict[str, object]:
    row: Dict[str, object] = {
        "case_id": str(data.case["case_id"]),
        "factor": str(data.case.get("factor", "explicit")),
        "factor_level": data.case.get("factor_level", "explicit"),
        "seed": int(data.case.get("seed", -1)),
        "scenario_label": str(data.case.get("label", data.case.get("case_id", ""))),
        "method": method,
        "comparison_protocol": details.get("comparison_protocol", ""),
        "method_family": details.get("method_family", ""),
        "target_id": data.target_id,
        "target_snr_config_dB": float(data.case.get("target_snr_db", math.nan)),
        "target_fd_truth_hz": float(data.target_fd),
        "fa_ctr_hz": float(data.fa_ctr),
        "target_clutter_center_offset_hz": float(data.target_fd - data.fa_ctr),
        "target_motion_ve_mps": float(data.case.get("motion", {}).get("ve_mps", math.nan)),
        "target_motion_vn_mps": float(data.case.get("motion", {}).get("vn_mps", math.nan)),
        "target_velocity_mode": str(data.case.get("velocity_mode", "enu")),
        "target_radial_speed_config_mps": float(data.case.get("radial_speed_mps", math.nan)),
        "target_azimuth_offset_deg": float(data.case.get("target_azimuth_offset_deg", 0.0)),
        "channel_valid_fraction": float(data.valid_sample_fraction),
        "target_row_truth": int(data.truth_row),
        "target_range_bin_truth": int(data.truth_col),
        "runtime_ms": float(runtime_ms),
        "status": "ok",
    }
    row.update(details)
    row.update({key: value for key, value in metrics.items() if key != "roc_points"})
    return row


def run_case(
    base_config_path: Path,
    suite: Dict[str, object],
    case: Dict[str, object],
    method_config: Dict[str, object],
    keep_data: bool,
    roc_scales: Sequence[float] | None = None,
    detection_threshold_scale: float = 1.0,
) -> Tuple[List[Dict[str, object]], Dict[str, object], List[Dict[str, object]]]:
    paths = v1.prepare_case_data(base_config_path, suite, case, keep_data)
    try:
        data = v1.load_case_data(case, paths)
        features = feature_base(data)
        rows: List[Dict[str, object]] = []
        roc_rows: List[Dict[str, object]] = []
        for method in METHODS:
            started = time.perf_counter()
            if method in STRICT_METHODS:
                metrics, details = run_strict_method(
                    data,
                    method,
                    roc_scales=roc_scales,
                    method_config=method_config,
                    detection_threshold_scale=detection_threshold_scale,
                )
            else:
                metrics, details = run_adaptive_method(
                    data,
                    method=method,
                    config=method_config,
                    roc_scales=roc_scales,
                    detection_threshold_scale=detection_threshold_scale,
                )
            runtime_ms = 1000.0 * (time.perf_counter() - started)
            rows.append(base_result_row(data, method, metrics, details, runtime_ms))
            for point in metrics.get("roc_points", []):
                roc_rows.append(
                    {
                        "case_id": str(data.case["case_id"]),
                        "factor": str(data.case.get("factor", "explicit")),
                        "factor_level": data.case.get("factor_level", "explicit"),
                        "seed": int(data.case.get("seed", -1)),
                        "method": method,
                        "comparison_protocol": details.get("comparison_protocol", ""),
                        "threshold_scale": point["threshold_scale"],
                        "target_detected": point["target_detected"],
                        "background_Pfa": point["background_Pfa"],
                    }
                )
        features.update(
            {
                "target_bin_bytes": int(data.target_bin_bytes),
                "background_bin_bytes": int(data.background_bin_bytes),
                "target_input_sha256": data.target_sha256,
                "background_input_sha256": data.background_sha256,
            }
        )
        return rows, features, roc_rows
    finally:
        v1.cleanup_case(paths)


def _complex_vector_comparison(predicted: np.ndarray, measured: np.ndarray) -> Dict[str, object]:
    predicted = np.asarray(predicted, dtype=np.complex128).reshape(-1)
    measured = np.asarray(measured, dtype=np.complex128).reshape(-1)
    valid = np.isfinite(predicted.real) & np.isfinite(predicted.imag)
    valid &= np.isfinite(measured.real) & np.isfinite(measured.imag)
    predicted = predicted[valid]
    measured = measured[valid]
    pred_norm = np.linalg.norm(predicted)
    measured_norm = np.linalg.norm(measured)
    if pred_norm <= 1.0e-12 or measured_norm <= 1.0e-12:
        return {
            "normalized_complex_correlation": math.nan,
            "phase_error_rms_rad": math.nan,
            "amplitude_normalized_error": math.nan,
            "phase_error_rad": [math.nan] * int(predicted.size),
        }
    predicted = predicted / pred_norm
    measured = measured / measured_norm
    correlation = abs(np.vdot(predicted, measured))
    global_phase = float(np.angle(np.vdot(predicted, measured)))
    aligned = predicted * np.exp(1j * global_phase)
    phase_error = np.angle(measured * np.conj(aligned))
    phase_weight = 0.5 * (np.abs(measured) ** 2 + np.abs(predicted) ** 2)
    phase_weight /= max(float(np.sum(phase_weight)), 1.0e-12)
    return {
        "normalized_complex_correlation": float(correlation),
        "phase_error_rms_rad": float(np.sqrt(np.sum(phase_weight * phase_error ** 2))),
        "amplitude_normalized_error": float(np.linalg.norm(np.abs(measured) - np.abs(predicted))),
        "phase_error_rad": phase_error.tolist(),
    }


def _empirical_sanity_cases(suite: Dict[str, object]) -> List[Dict[str, object]]:
    spec = suite.get("steering_sanity", {})
    base = _base_case(suite)
    cases: List[Dict[str, object]] = []
    for index, item in enumerate(spec.get("cases", [])):
        case = copy.deepcopy(base)
        case.update(copy.deepcopy(item))
        case["case_id"] = str(item.get("case_id", f"steering_sanity_{index + 1:02d}"))
        case["target_id"] = f"{case.get('target_id', 'V21_TARGET')}_{case['case_id']}"
        case["factor"] = "steering_sanity"
        case["factor_level"] = item.get("label", case["case_id"])
        case["label"] = str(item.get("label", case["case_id"]))
        case["target_snr_db"] = float(item.get("target_snr_db", spec.get("target_snr_db", 30.0)))
        if "radial_speed_mps" in item:
            case["velocity_mode"] = "radial"
            case["radial_speed_mps"] = float(item["radial_speed_mps"])
        case["use_paired_background"] = True
        cases.append(case)
    if not cases:
        raise ValueError("steering_sanity.cases must contain at least one real Stage2 case")
    return cases


def empirical_steering_sanity(
    out_dir: Path,
    base_config_path: Path,
    suite: Dict[str, object],
) -> Dict[str, object]:
    """Compare geometry predictions with same-seed S=(S+C+N)-(C+N) vectors."""

    sanity_spec = suite.get("steering_sanity", {})
    thresholds = {
        "spatial_correlation_min": float(sanity_spec.get("spatial_correlation_min", 0.90)),
        "jdl_correlation_min": float(sanity_spec.get("jdl_correlation_min", 0.80)),
        "spatial_phase_rms_max_rad": float(sanity_spec.get("spatial_phase_rms_max_rad", 0.40)),
        "jdl_phase_rms_max_rad": float(sanity_spec.get("jdl_phase_rms_max_rad", 0.60)),
        "amplitude_normalized_error_max": float(sanity_spec.get("amplitude_normalized_error_max", 0.25)),
        "velocity_invariance_correlation_min": float(sanity_spec.get("velocity_invariance_correlation_min", 0.90)),
    }
    run_suite = copy.deepcopy(suite)
    run_suite["output_root"] = relative_repo_path(out_dir / "cases")
    rows: List[Dict[str, object]] = []
    measured_by_group: Dict[str, List[Tuple[int, np.ndarray]]] = {}
    for case in _empirical_sanity_cases(suite):
        paths: Dict[str, Path | bool] | None = None
        try:
            paths = v1.prepare_case_data(base_config_path, run_suite, case, keep_data=False)
            data = v1.load_case_data(case, paths)
            signal = data.raw_target_rd - data.raw_bg_rd
            r0 = max(0, data.truth_row - 2)
            r1 = min(signal.shape[0], data.truth_row + 3)
            c0 = max(0, data.truth_col - 2)
            c1 = min(signal.shape[1], data.truth_col + 3)
            local = signal[r0:r1, c0:c1, :]
            measured_power = np.sum(np.abs(local) ** 2, axis=2)
            local_row, local_col = np.unravel_index(int(np.argmax(measured_power)), measured_power.shape)
            row = r0 + int(local_row)
            col = c0 + int(local_col)
            measured_spatial = signal[row, col, :]
            range_m = range_at_column(col, data.p)
            angle_deg = target_beam_angle_deg(data)
            actual_angle_deg = angle_deg + float(case.get("target_azimuth_offset_deg", 0.0))
            predicted_spatial = spatial_steering(actual_angle_deg, range_m, data.p)
            predicted_hypothesis_spatial = spatial_steering(angle_deg, range_m, data.p)
            spatial_metrics = _complex_vector_comparison(predicted_spatial, measured_spatial)
            hypothesis_spatial_metrics = _complex_vector_comparison(predicted_hypothesis_spatial, measured_spatial)
            measured_features = _feature_tensor(signal, JDL_OFFSETS)[row, col, :]
            predicted_features = space_time_steering(
                actual_angle_deg,
                float(data.axis[row] - data.fa_ctr),
                row,
                data.axis,
                data.fa_ctr,
                data.p,
                range_m,
            )
            jdl_metrics = _complex_vector_comparison(predicted_features, measured_features)
            angle_response = {
                f"angle_perturbation_{offset:+g}deg_gain_dB": db20(
                    abs(np.vdot(predicted_hypothesis_spatial, spatial_steering(angle_deg + offset, range_m, data.p)))
                )
                for offset in (-2.0, -1.0, 1.0, 2.0)
            }
            record: Dict[str, object] = {
                "case_id": case["case_id"],
                "label": case.get("label", case["case_id"]),
                "seed": int(case.get("seed", -1)),
                "sanity_group": case.get("sanity_group", "unspecified"),
                "beam_id": int(case.get("beam_id", 31)),
                "target_expected_bin": int(case.get("target_expected_bin", data.truth_col)),
                "target_azimuth_offset_deg": float(case.get("target_azimuth_offset_deg", 0.0)),
                "target_beam_hypothesis_angle_deg": angle_deg,
                "target_geometry_angle_deg_for_sanity": actual_angle_deg,
                "target_radial_speed_mps": finite_or_nan(case.get("radial_speed_mps")),
                "target_fd_truth_hz": float(data.target_fd),
                "target_fd_relative_hz": float(data.target_fd - data.fa_ctr),
                "jdl_row_fd_relative_hz_used": float(data.axis[row] - data.fa_ctr),
                "measured_peak_row": row,
                "measured_peak_col": col,
                "measured_range_m": range_m,
                "spatial_normalized_complex_correlation": spatial_metrics["normalized_complex_correlation"],
                "spatial_phase_error_rms_rad": spatial_metrics["phase_error_rms_rad"],
                "spatial_amplitude_normalized_error": spatial_metrics["amplitude_normalized_error"],
                "spatial_hypothesis_normalized_complex_correlation": hypothesis_spatial_metrics["normalized_complex_correlation"],
                "spatial_hypothesis_phase_error_rms_rad": hypothesis_spatial_metrics["phase_error_rms_rad"],
                "jdl_normalized_complex_correlation": jdl_metrics["normalized_complex_correlation"],
                "jdl_phase_error_rms_rad": jdl_metrics["phase_error_rms_rad"],
                "jdl_amplitude_normalized_error": jdl_metrics["amplitude_normalized_error"],
                "signal_definition": "raw_target_rd - raw_background_rd from same-seed paired Stage2 outputs",
                "status": "ok",
            }
            for index, value in enumerate(spatial_metrics["phase_error_rad"], start=1):
                record[f"spatial_phase_error_ch{index}_rad"] = value
            for index, value in enumerate(jdl_metrics["phase_error_rad"], start=1):
                record[f"jdl_phase_error_feature{index}_rad"] = value
            record.update(angle_response)
            rows.append(record)
            measured_by_group.setdefault(str(case.get("sanity_group", "unspecified")), []).append((len(rows) - 1, measured_spatial.copy()))
            del data
        except Exception as exc:
            rows.append({
                "case_id": case["case_id"],
                "label": case.get("label", case["case_id"]),
                "seed": int(case.get("seed", -1)),
                "sanity_group": case.get("sanity_group", "unspecified"),
                "status": "failed",
                "failure_reason": repr(exc),
            })
        finally:
            if paths is not None:
                v1.cleanup_case(paths)

    for group, indexed_vectors in measured_by_group.items():
        if len(indexed_vectors) < 2:
            continue
        reference = indexed_vectors[0][1]
        for index, vector in indexed_vectors:
            comparison = _complex_vector_comparison(reference, vector)
            rows[index]["velocity_spatial_invariance_corr"] = comparison["normalized_complex_correlation"]

    checks: List[Dict[str, object]] = []
    valid_rows = [row for row in rows if row.get("status") == "ok"]
    for row in valid_rows:
        checks.extend([
            {
                "case_id": row["case_id"],
                "test": "spatial_normalized_complex_correlation",
                "value": row.get("spatial_normalized_complex_correlation", math.nan),
                "threshold": thresholds["spatial_correlation_min"],
                "passed": bool(finite_or_nan(row.get("spatial_normalized_complex_correlation")) >= thresholds["spatial_correlation_min"]),
            },
            {
                "case_id": row["case_id"],
                "test": "jdl_normalized_complex_correlation",
                "value": row.get("jdl_normalized_complex_correlation", math.nan),
                "threshold": thresholds["jdl_correlation_min"],
                "passed": bool(finite_or_nan(row.get("jdl_normalized_complex_correlation")) >= thresholds["jdl_correlation_min"]),
            },
            {
                "case_id": row["case_id"],
                "test": "spatial_phase_error_rms_rad",
                "value": row.get("spatial_phase_error_rms_rad", math.nan),
                "threshold": thresholds["spatial_phase_rms_max_rad"],
                "passed": bool(finite_or_nan(row.get("spatial_phase_error_rms_rad")) <= thresholds["spatial_phase_rms_max_rad"]),
            },
            {
                "case_id": row["case_id"],
                "test": "jdl_phase_error_rms_rad",
                "value": row.get("jdl_phase_error_rms_rad", math.nan),
                "threshold": thresholds["jdl_phase_rms_max_rad"],
                "passed": bool(finite_or_nan(row.get("jdl_phase_error_rms_rad")) <= thresholds["jdl_phase_rms_max_rad"]),
            },
            {
                "case_id": row["case_id"],
                "test": "spatial_amplitude_normalized_error",
                "value": row.get("spatial_amplitude_normalized_error", math.nan),
                "threshold": thresholds["amplitude_normalized_error_max"],
                "passed": bool(finite_or_nan(row.get("spatial_amplitude_normalized_error")) <= thresholds["amplitude_normalized_error_max"]),
            },
        ])
        if str(row.get("sanity_group")) == "fixed_angle_velocity" and "velocity_spatial_invariance_corr" in row:
            checks.append({
                "case_id": row["case_id"],
                "test": "velocity_spatial_invariance_corr",
                "value": row["velocity_spatial_invariance_corr"],
                "threshold": thresholds["velocity_invariance_correlation_min"],
                "passed": bool(finite_or_nan(row.get("velocity_spatial_invariance_corr")) >= thresholds["velocity_invariance_correlation_min"]),
            })
    if len(valid_rows) != len(rows):
        checks.append({
            "case_id": "all",
            "test": "all_empirical_cases_completed",
            "value": len(valid_rows),
            "threshold": len(rows),
            "passed": False,
        })
    write_csv(out_dir / "baseline_v21_empirical_steering_sanity.csv", rows)
    write_csv(out_dir / "baseline_v21_empirical_steering_checks.csv", checks)
    if rows:
        labels = [str(row.get("case_id", "")) for row in rows]
        corr = [finite_or_nan(row.get("spatial_normalized_complex_correlation")) for row in rows]
        jdl_corr = [finite_or_nan(row.get("jdl_normalized_complex_correlation")) for row in rows]
        fig, ax = plt.subplots(figsize=(max(9.0, 0.55 * len(labels)), 5.0))
        x = np.arange(len(labels))
        ax.bar(x - 0.18, corr, width=0.36, label="spatial", color="#1f77b4")
        ax.bar(x + 0.18, jdl_corr, width=0.36, label="JDL 12-D", color="#17becf")
        ax.axhline(thresholds["spatial_correlation_min"], color="#1f77b4", linestyle="--", linewidth=0.8)
        ax.axhline(thresholds["jdl_correlation_min"], color="#17becf", linestyle=":", linewidth=0.8)
        ax.set_xticks(x, labels, rotation=35, ha="right")
        ax.set_ylim(0.0, 1.05)
        ax.set_ylabel("Absolute normalized complex correlation")
        ax.set_title("Empirical Stage2 steering sanity")
        ax.grid(True, axis="y", color="#dddddd", linewidth=0.6)
        ax.legend()
        fig.tight_layout()
        fig.savefig(out_dir / "baseline_v21_empirical_steering_sanity.png", dpi=160)
        plt.close(fig)
    result = {
        "case_count": len(rows),
        "completed_case_count": len(valid_rows),
        "thresholds": thresholds,
        "checks": checks,
        "all_pass": bool(rows) and bool(checks) and all(bool(check["passed"]) for check in checks),
        "signal_definition": "S=(S+C+N)-(C+N), same seed and same generated Stage2 scene configuration",
        "spatial_definition": "configured target beam-center angle and measured range column",
        "jdl_definition": "explicit target Doppler temporal response multiplied by the same configured spatial steering",
    }
    write_json(out_dir / "baseline_v21_empirical_steering_sanity.json", result)
    return result


def steering_sanity(
    out_dir: Path,
    base_config_path: Path,
    suite: Dict[str, object],
) -> Dict[str, object]:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = SimpleNamespace(
        fc_hz=16.0e9,
        pulse_num=130,
        prf_hz=1300.0,
        rg_st=1,
        rg_ed=4095,
        range_crop_start=3864,
        sample_delay_s=488.0e-6,
        fs_hz=60.0e6,
        platform_speed_mps=60.0,
        platform_height_m=6000.0,
        carrier_phase_sign=-1,
        squint_side=1,
        offsets_m=((-0.085, 0.0, 0.085), (0.085, 0.0, 0.085), (-0.085, 0.0, -0.085), (0.085, 0.0, -0.085)),
    )
    sanity_range_m = range_at_column(2200, p)
    target_angle = 3.0
    clutter_angle = 0.0
    target_fd = 2.0 * p.platform_speed_mps * math.sin(math.radians(target_angle)) / (oracle.C0 / p.fc_hz)
    clutter_fd = 2.0 * p.platform_speed_mps * math.sin(math.radians(clutter_angle)) / (oracle.C0 / p.fc_hz)
    target_s = spatial_steering(target_angle, sanity_range_m, p)
    clutter_s = spatial_steering(clutter_angle, sanity_range_m, p)
    covariance = 20.0 * np.outer(clutter_s, clutter_s.conj()) + np.eye(4, dtype=np.complex128)
    target_w = mvdr_weight(covariance, target_s)
    target_gain = np.vdot(target_w, target_s)
    clutter_gain = np.vdot(target_w, clutter_s)
    target_phase = 0.71
    target_amplitude = 1.7
    injected = target_amplitude * np.exp(1j * target_phase) * target_s
    recovered = np.vdot(target_w, injected)
    perturbations = [0.0, 0.5, 1.0, 2.0, 4.0]
    perturb_gain_db = [db20(np.vdot(target_w, spatial_steering(target_angle + d, sanity_range_m, p))) for d in perturbations]
    # Center the Doppler scan on the tested target so the response peak is
    # evaluated in a target-centered physical neighborhood, not on a distant
    # JDL ambiguity branch.
    axis = target_fd + np.linspace(-450.0, 450.0, 121)
    angles = np.linspace(-5.0, 5.0, 81)
    jdl_target_row = 65
    sanity_axis = np.linspace(-650.0, 650.0, p.pulse_num)
    jdl_target_s = space_time_steering(target_angle, target_fd, jdl_target_row, sanity_axis, 0.0, p, sanity_range_m)
    jdl_clutter_s = space_time_steering(clutter_angle, clutter_fd, jdl_target_row, sanity_axis, 0.0, p, sanity_range_m)
    jdl_covariance = 20.0 * np.outer(jdl_clutter_s, jdl_clutter_s.conj()) + np.eye(12, dtype=np.complex128)
    jdl_w = mvdr_weight(jdl_covariance, jdl_target_s)
    response = np.zeros((angles.size, axis.size), dtype=float)
    for i, angle in enumerate(angles):
        for j, fd in enumerate(axis):
            candidate = space_time_steering(
                float(angle),
                float(fd),
                jdl_target_row,
                sanity_axis,
                0.0,
                p,
                sanity_range_m,
            )
            response[i, j] = db20(np.vdot(jdl_w, candidate))
    scan_peak = np.unravel_index(int(np.nanargmax(response)), response.shape)
    checks = [
        {"test": "spatial_distortionless", "value": abs(target_gain - 1.0), "threshold": 1.0e-8, "passed": bool(abs(target_gain - 1.0) <= 1.0e-8)},
        {"test": "space_time_distortionless", "value": abs(np.vdot(jdl_w, jdl_target_s) - 1.0), "threshold": 1.0e-8, "passed": bool(abs(np.vdot(jdl_w, jdl_target_s) - 1.0) <= 1.0e-8)},
        {"test": "clutter_ridge_notch_dB", "value": db20(clutter_gain), "threshold": -3.0, "passed": bool(db20(clutter_gain) <= -3.0)},
        {"test": "single_target_complex_gain_error", "value": abs(recovered / (target_amplitude * np.exp(1j * target_phase)) - 1.0), "threshold": 1.0e-8, "passed": bool(abs(recovered / (target_amplitude * np.exp(1j * target_phase)) - 1.0) <= 1.0e-8)},
        {"test": "perturbation_4deg_gain_dB", "value": perturb_gain_db[-1], "threshold": -0.5, "passed": bool(perturb_gain_db[-1] <= -0.5)},
    ]
    checks_rows = []
    for check in checks:
        checks_rows.append(check)
    write_csv(out_dir / "steering_sanity.csv", checks_rows)
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    image = axes[0].imshow(
        response,
        aspect="auto",
        origin="lower",
        extent=[axis[0], axis[-1], angles[0], angles[-1]],
        cmap="magma",
        vmin=-40,
        vmax=3,
    )
    axes[0].set_title("Physical space-time steering response")
    axes[0].set_xlabel("Relative Doppler (Hz)")
    axes[0].set_ylabel("Angle (deg)")
    fig.colorbar(image, ax=axes[0], label="|wᴴs| (dB)")
    axes[1].plot(perturbations, perturb_gain_db, marker="o", color="#1f77b4")
    axes[1].axhline(0.0, color="#555555", linewidth=0.8)
    axes[1].set_title("Steering perturbation target-gain check")
    axes[1].set_xlabel("Angle perturbation (deg)")
    axes[1].set_ylabel("Target gain (dB)")
    axes[1].grid(True, color="#dddddd", linewidth=0.6)
    fig.tight_layout()
    fig.savefig(out_dir / "baseline_v21_steering_sanity.png", dpi=160)
    plt.close(fig)
    synthetic_result = {
        "target_angle_deg": target_angle,
        "target_relative_doppler_hz": target_fd,
        "clutter_angle_deg": clutter_angle,
        "clutter_relative_doppler_hz": clutter_fd,
        "target_gain_complex": target_gain,
        "clutter_gain_dB": db20(clutter_gain),
        "single_target_recovered_complex": recovered,
        "perturbations_deg": perturbations,
        "perturb_gain_dB": perturb_gain_db,
        "scan_peak_angle_deg": float(angles[scan_peak[0]]),
        "scan_peak_relative_doppler_hz": float(axis[scan_peak[1]]),
        "checks": checks,
        "all_pass": all(bool(check["passed"]) for check in checks),
        "steering_definition": "configured beam-center spatial path difference plus explicit temporal Doppler response; clutter ridge is diagnostic only",
    }
    empirical_result = empirical_steering_sanity(out_dir, base_config_path, suite)
    result = dict(synthetic_result)
    result["synthetic"] = synthetic_result
    result["empirical"] = empirical_result
    result["all_pass"] = bool(synthetic_result["all_pass"] and empirical_result["all_pass"])
    result["steering_definition"] = "spatial(theta, range, geometry) with explicit temporal(fd, row); clutter_ridge_from_doppler is diagnostic only"
    write_json(out_dir / "steering_sanity.json", result)
    return result


def aggregate_sweeps(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    if not rows:
        return []
    metrics = [
        "SCNR_improvement_dB",
        "target_loss_dB",
        "residual_suppression_dB",
        "background_residual_p95_dB_over_input_median",
        "background_residual_p99_dB_over_input_median",
        "background_residual_cvar95_dB_over_input_median",
        "high_tail_fraction_15dB",
        "background_Pfa",
        "Pd",
        "runtime_ms",
    ]
    groups: Dict[Tuple[str, str, str, str], List[Dict[str, object]]] = {}
    for row in rows:
        key = (str(row["factor"]), str(row["factor_level"]), str(row["method"]), str(row["comparison_protocol"]))
        groups.setdefault(key, []).append(row)
    output: List[Dict[str, object]] = []
    for (factor, level, method, protocol), grouped in sorted(groups.items()):
        base = {
            "factor": factor,
            "factor_level": level,
            "method": method,
            "comparison_protocol": protocol,
            "n_cases": len(grouped),
            "n_seeds": len({str(row.get("seed", "")) for row in grouped}),
            "failure_rate": float(np.mean([str(row.get("status", "ok")) != "ok" for row in grouped])),
        }
        for metric in metrics:
            source_metric = "target_detected" if metric == "Pd" else metric
            values = [finite_or_nan(row.get(source_metric)) for row in grouped]
            finite = [value for value in values if math.isfinite(value)]
            low, high = ci95(finite)
            base[f"{metric}__mean"] = float(np.mean(finite)) if finite else math.nan
            base[f"{metric}__std"] = float(np.std(finite, ddof=1)) if len(finite) >= 2 else math.nan
            if metric == "Pd":
                successes = int(sum(value >= 0.5 for value in finite))
                trials = len(finite)
                low, high = wilson_ci95(successes, trials)
                base["Pd__successes"] = successes
                base["Pd__trials"] = trials
            base[f"{metric}__ci95_low"] = low
            base[f"{metric}__ci95_high"] = high
            base[f"{metric}__p05"] = float(np.percentile(finite, 5.0)) if finite else math.nan
            base[f"{metric}__p95"] = float(np.percentile(finite, 95.0)) if finite else math.nan
        output.append(base)
    return output


def summarize_mdv(rows: List[Dict[str, object]], suite: Dict[str, object]) -> List[Dict[str, object]]:
    """Estimate operational MDV from regenerated physical velocity cases.

    MDV is defined here before looking at the results as the smallest absolute
    regenerated radial-speed grid value whose seed-mean empirical Pd reaches the
    configured threshold.  If the crossing is bracketed, a linear interpolation
    is reported only as a compact summary; the un-interpolated grid and Pd
    values remain in baseline_v2_sweep_summary.csv.
    """
    velocity = [row for row in rows if str(row.get("factor")) == "target_radial_speed_mps"]
    if not velocity:
        return []
    threshold = float(suite.get("velocity_sweep", {}).get("mdv_pd_threshold", 0.5))
    grouped: Dict[Tuple[str, str, str], Dict[float, List[float]]] = {}
    by_seed: Dict[Tuple[str, str, str, str], Dict[float, List[float]]] = {}
    for row in velocity:
        signed_speed = finite_or_nan(row.get("target_radial_speed_config_mps"))
        if not math.isfinite(signed_speed):
            signed_speed = finite_or_nan(row.get("target_motion_ve_mps"))
        direction = "approaching" if signed_speed < 0.0 else ("receding" if signed_speed > 0.0 else "zero")
        protocol_key = (str(row.get("method")), str(row.get("comparison_protocol")), direction)
        speed = abs(float(signed_speed))
        pd = finite_or_nan(row.get("target_detected"))
        if math.isfinite(speed) and math.isfinite(pd):
            grouped.setdefault(protocol_key, {}).setdefault(speed, []).append(pd)
            by_seed.setdefault((protocol_key[0], protocol_key[1], direction, str(row.get("seed"))), {}).setdefault(speed, []).append(pd)

    def crossing_value(curve: List[Tuple[float, float]]) -> float:
        for index, (speed, pd) in enumerate(curve):
            if pd >= threshold:
                if index == 0 or curve[index - 1][1] >= threshold:
                    return speed
                low_speed, low_pd = curve[index - 1]
                fraction = (threshold - low_pd) / max(pd - low_pd, 1.0e-12)
                return low_speed + fraction * (speed - low_speed)
        return math.nan

    output: List[Dict[str, object]] = []
    for (method, protocol, direction), by_speed in sorted(grouped.items()):
        curve = sorted((speed, float(np.mean(values)), len(values)) for speed, values in by_speed.items())
        group_crossing = math.nan
        low_speed = math.nan
        high_speed = math.nan
        for index, (speed, pd, _) in enumerate(curve):
            if pd >= threshold:
                if index == 0 or curve[index - 1][1] >= threshold:
                    group_crossing = speed
                else:
                    low_speed, low_pd, _ = curve[index - 1]
                    high_speed = speed
                    fraction = (threshold - low_pd) / max(pd - low_pd, 1.0e-12)
                    group_crossing = low_speed + fraction * (high_speed - low_speed)
                if not math.isfinite(low_speed):
                    low_speed = speed
                    high_speed = speed
                break
        seed_crossings: List[float] = []
        for (seed_method, seed_protocol, seed_direction, _seed), seed_by_speed in by_seed.items():
            if seed_method != method or seed_protocol != protocol or seed_direction != direction:
                continue
            seed_curve = sorted((speed, float(np.mean(values))) for speed, values in seed_by_speed.items())
            seed_value = crossing_value(seed_curve)
            if math.isfinite(seed_value):
                seed_crossings.append(seed_value)
        seed_low, seed_high = ci95(seed_crossings)
        output.append(
            {
                "method": method,
                "comparison_protocol": protocol,
                "velocity_direction": direction,
                "symmetry_verified": False,
                "pd_threshold": threshold,
                "mdv_mps": group_crossing,
                "crossing_low_mps": low_speed,
                "crossing_high_mps": high_speed,
                "velocity_grid_min_mps": curve[0][0],
                "velocity_grid_max_mps": curve[-1][0],
                "n_cases": sum(n for _, _, n in curve),
                "n_seeds": len({str(row.get("seed")) for row in velocity if row.get("method") == method and row.get("comparison_protocol") == protocol}),
                "mdv_seed_mean_mps": float(np.mean(seed_crossings)) if seed_crossings else math.nan,
                "mdv_seed_std_mps": float(np.std(seed_crossings, ddof=1)) if len(seed_crossings) >= 2 else math.nan,
                "mdv_seed_ci95_low_mps": max(0.0, seed_low) if math.isfinite(seed_low) else seed_low,
                "mdv_seed_ci95_high_mps": seed_high,
                "mdv_seed_p05_mps": float(np.percentile(seed_crossings, 5.0)) if seed_crossings else math.nan,
                "mdv_seed_p95_mps": float(np.percentile(seed_crossings, 95.0)) if seed_crossings else math.nan,
                "definition": "smallest absolute regenerated signed-radial-speed grid value with direction-specific seed-mean empirical Pd >= threshold; approaching/receding are not combined unless symmetry is verified",
            }
        )
    symmetry_tolerance = float(suite.get("velocity_sweep", {}).get("symmetry_pd_tolerance", 0.15))
    by_curve = {(row["method"], row["comparison_protocol"], row["velocity_direction"]): row for row in output}
    methods_protocols = sorted({(row["method"], row["comparison_protocol"]) for row in output})
    for method, protocol in methods_protocols:
        approaching = by_curve.get((method, protocol, "approaching"))
        receding = by_curve.get((method, protocol, "receding"))
        if approaching is None or receding is None:
            continue
        paired = []
        for speed in sorted(set(grouped[(method, protocol, "approaching")]) & set(grouped[(method, protocol, "receding")])):
            a = float(np.mean(grouped[(method, protocol, "approaching")][speed]))
            r = float(np.mean(grouped[(method, protocol, "receding")][speed]))
            paired.append(abs(a - r))
        verified = bool(paired) and max(paired) <= symmetry_tolerance
        approaching["symmetry_max_abs_pd_difference"] = max(paired) if paired else math.nan
        receding["symmetry_max_abs_pd_difference"] = max(paired) if paired else math.nan
        if verified:
            combined = dict(approaching)
            combined["velocity_direction"] = "abs_speed_combined"
            combined["symmetry_verified"] = True
            combined["definition"] = "approaching/receding curves combined only after per-speed mean Pd difference passed the configured symmetry tolerance"
            output.append(combined)
    return output


def enrich_feature_diagnostics(
    features: List[Dict[str, object]],
    rows: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    by_case: Dict[str, List[Dict[str, object]]] = {}
    for row in rows:
        by_case.setdefault(str(row["case_id"]), []).append(row)
    output: List[Dict[str, object]] = []
    for feature in features:
        case_rows = by_case.get(str(feature["case_id"]), [])
        strict_rows = [row for row in case_rows if row.get("method_family") == "F1/F2 strict"]
        adaptive_rows = [row for row in case_rows if row.get("method_family") == "4-channel physical academic reference"]
        for label, candidates in (("strict", strict_rows), ("adaptive", adaptive_rows), ("overall", case_rows)):
            if not candidates:
                continue
            best = max(candidates, key=lambda row: finite_or_nan(row.get("SCNR_improvement_dB")))
            feature[f"best_{label}_expert"] = best.get("method", "")
            feature[f"best_{label}_SCNR_improvement_dB"] = finite_or_nan(best.get("SCNR_improvement_dB"))
        for method_tag, method_name in (
            ("current", "Current legacy CSI [production replay]"),
            ("phase_only", "Phase-only CSI [production replay]"),
            ("row_ls", "Row complex LS / Wiener [scientific controlled]"),
            ("robust_row_ls", "Row complex LS / Wiener robust IRLS [scientific controlled]"),
            ("dl_mvdr", "DL-SMI/MVDR global"),
            ("shrinkage_mvdr", "Shrinkage-SMI/MVDR global"),
            ("jdl", "Corrected JDL physical"),
        ):
            match = next((row for row in case_rows if row.get("method") == method_name), None)
            if match is not None:
                feature[f"{method_tag}_SCNR_improvement_dB"] = finite_or_nan(match.get("SCNR_improvement_dB"))
                feature[f"{method_tag}_target_loss_dB"] = finite_or_nan(match.get("target_loss_dB"))
                feature[f"{method_tag}_Pfa"] = finite_or_nan(match.get("background_Pfa"))
                feature[f"{method_tag}_failure"] = int(
                    not math.isfinite(finite_or_nan(match.get("SCNR_improvement_dB"))) or
                    finite_or_nan(match.get("target_loss_dB")) < -10.0
                )
        current_match = next(
            (row for row in case_rows if row.get("method") == "Current legacy CSI [scientific controlled]"),
            next((row for row in case_rows if row.get("method") == "Current legacy CSI [production replay]"), None),
        )
        if current_match is not None:
            feature["current_residual_p95_dB_over_input_median"] = finite_or_nan(
                current_match.get("background_residual_p95_dB_over_input_median")
            )
            feature["current_residual_p99_dB_over_input_median"] = finite_or_nan(
                current_match.get("background_residual_p99_dB_over_input_median")
            )
            feature["current_residual_cvar95_dB_over_input_median"] = finite_or_nan(
                current_match.get("background_residual_cvar95_dB_over_input_median")
            )
        dl_global = next((row for row in case_rows if row.get("method") == "DL-SMI/MVDR global"), None)
        if dl_global is not None:
            feature["dl_global_covariance_condition_number"] = finite_or_nan(
                dl_global.get("covariance_condition_number")
            )
            feature["dl_global_covariance_condition_number_p95"] = finite_or_nan(
                dl_global.get("covariance_condition_number_p95")
            )
            feature["dl_global_estimated_clutter_rank"] = finite_or_nan(
                dl_global.get("estimated_clutter_rank")
            )
        jdl = next((row for row in case_rows if row.get("method") == "Corrected JDL physical"), None)
        if jdl is not None:
            feature["jdl_covariance_condition_number"] = finite_or_nan(
                jdl.get("covariance_condition_number")
            )
            feature["jdl_estimated_clutter_rank"] = finite_or_nan(jdl.get("estimated_clutter_rank"))
        feature["adaptive_failure"] = int(
            not math.isfinite(finite_or_nan(feature.get("best_adaptive_SCNR_improvement_dB")))
        )
        output.append(feature)
    return output


def feature_correlations(features: List[Dict[str, object]]) -> List[Dict[str, object]]:
    if not features:
        return []
    feature_names = [
        "valid_pulse_fraction",
        "texture_sigma_config",
        "azimuth_subcell_count_config",
        "texture_nonuniformity_cv",
        "p38_slope_rad_per_hz",
        "p38_rmse_rad",
        "p38_inlier_ratio",
        "p38_accepted_sample_count",
        "coherence_mean",
        "coherence_p10",
        "F1_F2_energy_ratio_dB",
        "phase_variance_rad2",
        "distance_to_clutter_ridge_hz",
        "distance_to_support_edge_rows",
        "dl_global_covariance_condition_number",
        "dl_global_estimated_clutter_rank",
        "current_residual_p95_dB_over_input_median",
        "current_residual_p99_dB_over_input_median",
    ]
    outcomes = [
        "current_SCNR_improvement_dB",
        "row_ls_SCNR_improvement_dB",
        "best_adaptive_SCNR_improvement_dB",
        "current_failure",
        "row_ls_failure",
        "jdl_failure",
        "adaptive_failure",
    ]
    output: List[Dict[str, object]] = []
    for feature_name in feature_names:
        for outcome in outcomes:
            output.append(
                {
                    "feature": feature_name,
                    "outcome": outcome,
                    "n": sum(
                        math.isfinite(finite_or_nan(row.get(feature_name))) and
                        math.isfinite(finite_or_nan(row.get(outcome))) for row in features
                    ),
                    "pearson": pearson(
                        [finite_or_nan(row.get(feature_name)) for row in features],
                        [finite_or_nan(row.get(outcome)) for row in features],
                    ),
                    "spearman": spearman(
                        [finite_or_nan(row.get(feature_name)) for row in features],
                        [finite_or_nan(row.get(outcome)) for row in features],
                    ),
                }
            )
    return output


def feature_light_predictor(features: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Run leakage-controlled exploratory ridge folds, never a production model."""
    predictor_features = [
        "valid_pulse_fraction",
        "texture_sigma_config",
        "azimuth_subcell_count_config",
        "texture_nonuniformity_cv",
        "p38_rmse_rad",
        "coherence_mean",
        "F1_F2_energy_ratio_dB",
        "phase_variance_rad2",
        "distance_to_clutter_ridge_hz",
        "distance_to_support_edge_rows",
        "dl_global_covariance_condition_number",
    ]
    targets = ["current_SCNR_improvement_dB", "best_adaptive_SCNR_improvement_dB"]
    output: List[Dict[str, object]] = []
    if len(features) < 5:
        return output
    ridge_lambda = 1.0

    def fit_predict(train_matrix: np.ndarray, train_y: np.ndarray, test_matrix: np.ndarray) -> np.ndarray:
        # Imputation and standardization are deliberately fitted on the
        # training fold only; test rows never influence either statistic.
        train_matrix = np.asarray(train_matrix, dtype=float).copy()
        test_matrix = np.asarray(test_matrix, dtype=float).copy()
        medians = np.zeros(train_matrix.shape[1], dtype=float)
        for column in range(train_matrix.shape[1]):
            finite = train_matrix[:, column][np.isfinite(train_matrix[:, column])]
            medians[column] = float(np.median(finite)) if finite.size else 0.0
            train_matrix[~np.isfinite(train_matrix[:, column]), column] = medians[column]
            test_matrix[~np.isfinite(test_matrix[:, column]), column] = medians[column]
        means = np.mean(train_matrix, axis=0)
        scales = np.std(train_matrix, axis=0)
        scales[scales <= 1.0e-12] = 1.0
        train_design = np.column_stack((np.ones(train_matrix.shape[0]), (train_matrix - means) / scales))
        test_design = np.column_stack((np.ones(test_matrix.shape[0]), (test_matrix - means) / scales))
        gram = train_design.T @ train_design
        gram[1:, 1:] += ridge_lambda * np.eye(train_design.shape[1] - 1)
        rhs = train_design.T @ train_y
        try:
            coefficients = np.linalg.solve(gram, rhs)
        except np.linalg.LinAlgError:
            coefficients = np.linalg.lstsq(gram, rhs, rcond=None)[0]
        return test_design @ coefficients

    for target in targets:
        target_values = np.asarray([finite_or_nan(row.get(target)) for row in features], dtype=float)
        valid_target = np.isfinite(target_values)
        indices = np.flatnonzero(valid_target)
        if indices.size < 5:
            continue
        selected = [features[index] for index in indices]
        y = target_values[valid_target]
        matrix = np.asarray(
            [[finite_or_nan(row.get(name)) for name in predictor_features] for row in selected],
            dtype=float,
        )
        modes = {
            "leave_one_case_out": [str(index) for index in range(len(selected))],
            "leave_one_seed_out": sorted({str(row.get("seed", "")) for row in selected}),
            "leave_one_factor_family_out": sorted({str(row.get("factor_family", row.get("factor", ""))) for row in selected}),
        }
        for mode, groups in modes.items():
            fold_rows: List[Dict[str, object]] = []
            all_y: List[float] = []
            all_pred: List[float] = []
            for group in groups:
                if mode == "leave_one_case_out":
                    test_mask = np.zeros(len(selected), dtype=bool)
                    test_mask[int(group)] = True
                elif mode == "leave_one_seed_out":
                    test_mask = np.asarray([str(row.get("seed", "")) == group for row in selected], dtype=bool)
                else:
                    test_mask = np.asarray([
                        str(row.get("factor_family", row.get("factor", ""))) == group for row in selected
                    ], dtype=bool)
                train_mask = ~test_mask
                if np.count_nonzero(train_mask) < 3 or not np.any(test_mask):
                    continue
                prediction = fit_predict(matrix[train_mask], y[train_mask], matrix[test_mask])
                actual = y[test_mask]
                residual = prediction - actual
                all_y.extend(actual.tolist())
                all_pred.extend(prediction.tolist())
                fold_rows.append({
                    "target": target,
                    "validation_mode": mode,
                    "holdout_group": group,
                    "n_train": int(np.count_nonzero(train_mask)),
                    "n_test": int(np.count_nonzero(test_mask)),
                    "fold_rmse": float(math.sqrt(np.mean(residual ** 2))),
                    "fold_mae": float(np.mean(np.abs(residual))),
                    "ridge_lambda": ridge_lambda,
                    "scope": "exploratory input-feature screen only; fold imputation/normalization fitted on training data; no production model",
                })
            if not all_y:
                continue
            y_array = np.asarray(all_y, dtype=float)
            pred_array = np.asarray(all_pred, dtype=float)
            residual = pred_array - y_array
            ss_tot = float(np.sum((y_array - np.mean(y_array)) ** 2))
            summary = {
                "target": target,
                "validation_mode": mode,
                "holdout_group": "aggregate",
                "n": int(y_array.size),
                "n_folds": len(fold_rows),
                "ridge_lambda": ridge_lambda,
                "rmse": float(math.sqrt(np.mean(residual ** 2))),
                "mae": float(np.mean(np.abs(residual))),
                "r2": float(1.0 - np.sum(residual ** 2) / ss_tot) if ss_tot > 1.0e-12 else math.nan,
                "scope": "exploratory input-feature screen only; fold imputation/normalization fitted on training data; no production model",
            }
            output.append(summary)
            output.extend(fold_rows)
    return output


def physics_expert_map_schema() -> Dict[str, object]:
    return {
        "schema_id": "physics_expert_map_v1",
        "grain": "one scene realization × seed × local Doppler row × local range block",
        "identity_fields": ["scene_id", "seed", "factor_family", "doppler_row", "range_block_start", "range_block_stop"],
        "input_fields": {
            "p38_slope_rad_per_hz": "float",
            "p38_intercept_rad": "float",
            "p38_rmse_rad": "float",
            "p38_inlier_count": "integer",
            "coherence_mean": "float",
            "F1_F2_energy_ratio_dB": "float",
            "phase_residual_mean_rad": "float",
            "phase_variance_rad2": "float",
            "valid_sample_fraction": "float",
            "texture_heterogeneity_cv": "float",
            "distance_to_clutter_ridge_hz": "float",
            "distance_to_support_edge_rows": "float",
            "local_residual_tail_p95_dB": "float",
            "robust_lsq_condition_number": "float",
            "robust_lsq_confidence": "float",
        },
        "expert_outputs": {
            "current_scnr_improvement_dB": "float",
            "row_ls_scnr_improvement_dB": "float",
            "robust_row_ls_scnr_improvement_dB": "float",
            "current_hold": "boolean",
            "adaptive_worthwhile": "boolean",
        },
        "oracle_label_fields": {
            "delta_log_amplitude": "float",
            "delta_phase": "float",
            "gate_target": "float in [0,1]",
            "oracle_gain_over_current_dB": "float",
        },
        "split_policy": {
            "required": True,
            "train_validation_test": "scene/seed grouped; leave-one-seed-out or whole-factor-family holdout",
            "forbidden": ["random local-row split", "same scene realization across train and test", "truth/oracle input to inference"],
        },
        "status": "export interface only; no row-level AI model is trained by V2.1",
    }


def pgrcc_v1_training_schema() -> Dict[str, object]:
    return {
        "schema_id": "pgrcc_v1_training_interface",
        "input": "physics_expert_map_v1 input_fields plus expert availability flags",
        "targets": ["delta_log_amplitude", "delta_phase", "gate_target"],
        "model_contract": {
            "architecture": "small MLP or light 1-D CNN over bounded local feature vector",
            "bounded_residual": "alpha_AI = alpha_phy * exp(clamp(delta_log_amplitude)) * exp(j*clamp(delta_phase))",
            "output": "Y = (1-gate_target)*Y_Current + gate_target*Y_corrected",
            "bounds": {"delta_log_amplitude": [-0.5, 0.5], "delta_phase": [-0.7853981634, 0.7853981634], "gate_target": [0.0, 1.0]},
            "identity_test": "delta_log_amplitude=0, delta_phase=0, gate_target=0 reproduces Current exactly",
            "fallback": "low confidence, invalid features, or failed physics gate returns Current",
        },
        "label_policy": "paired C+N/truth may define oracle labels and loss only; never a network inference input",
        "scope": "PGRCC-v1 interface; no training or RD-image model in V2.1",
    }


def aggregate_roc_points(roc_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    groups: Dict[Tuple[str, str, float], List[Dict[str, object]]] = {}
    for row in roc_rows:
        key = (
            str(row.get("method", "")),
            str(row.get("comparison_protocol", "")),
            float(row.get("threshold_scale", math.nan)),
        )
        groups.setdefault(key, []).append(row)
    output: List[Dict[str, object]] = []
    for (method, protocol, scale), grouped in sorted(groups.items()):
        pfa_values = [finite_or_nan(row.get("background_Pfa")) for row in grouped]
        detected_values = [finite_or_nan(row.get("target_detected")) for row in grouped]
        pfa_finite = [value for value in pfa_values if math.isfinite(value)]
        detected_finite = [value for value in detected_values if math.isfinite(value)]
        successes = int(sum(value >= 0.5 for value in detected_finite))
        trials = len(detected_finite)
        pd_low, pd_high = wilson_ci95(successes, trials)
        pfa_low, pfa_high = ci95(pfa_finite)
        output.append(
            {
                "method": method,
                "comparison_protocol": protocol,
                "threshold_scale": scale,
                "n_cases": len(grouped),
                "n_seeds": len({str(row.get("seed", "")) for row in grouped}),
                "background_Pfa_mean": float(np.mean(pfa_finite)) if pfa_finite else math.nan,
                "background_Pfa_std": float(np.std(pfa_finite, ddof=1)) if len(pfa_finite) >= 2 else math.nan,
                "background_Pfa_ci95_low": pfa_low,
                "background_Pfa_ci95_high": pfa_high,
                "Pd_mean": float(np.mean(detected_finite)) if detected_finite else math.nan,
                "Pd_std": float(np.std(detected_finite, ddof=1)) if len(detected_finite) >= 2 else math.nan,
                "Pd_ci95_low": pd_low,
                "Pd_ci95_high": pd_high,
                "Pd_successes": successes,
                "Pd_trials": trials,
            }
        )
    return output


def plot_roc(roc_summary: List[Dict[str, object]], out_dir: Path) -> None:
    if not roc_summary:
        return
    grouped: Dict[Tuple[str, str], List[Dict[str, object]]] = {}
    for row in roc_summary:
        grouped.setdefault((str(row["method"]), str(row["comparison_protocol"])), []).append(row)
    fig, ax = plt.subplots(figsize=(10.5, 6.0))
    for (method, protocol), points in grouped.items():
        points = sorted(points, key=lambda row: float(row["threshold_scale"]))
        x = np.asarray([max(finite_or_nan(row.get("background_Pfa_mean")), 1.0e-8) for row in points])
        y = np.asarray([finite_or_nan(row.get("Pd_mean")) for row in points])
        valid = np.isfinite(x) & np.isfinite(y)
        if not np.any(valid):
            continue
        label = method.replace(" [scientific controlled]", "").replace(" [production replay]", "")
        if protocol == "production_replay":
            label += " [prod]"
        ax.plot(x[valid], y[valid], marker="o", linewidth=1.2, markersize=3.5, label=label, color=METHOD_PALETTE.get(method, "#333333"))
    ax.set_xscale("log")
    ax.set_xlabel("Background Pfa (mean across ROC seeds)")
    ax.set_ylabel("Empirical Pd (target_detected aggregated across seeds)")
    ax.set_ylim(-0.05, 1.05)
    ax.set_title("CPU GO-CFAR operating-point sweep")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    ax.legend(fontsize=6.5, ncol=2)
    fig.tight_layout()
    fig.savefig(out_dir / "baseline_v2_pfa_roc.png", dpi=160)
    plt.close(fig)


def plot_pd_scnr(rows: List[Dict[str, object]], out_dir: Path) -> None:
    selected = [row for row in rows if str(row.get("factor")) == "target_snr_db"]
    if not selected:
        return
    grouped: Dict[Tuple[str, str, float], List[Dict[str, object]]] = {}
    for row in selected:
        grouped.setdefault(
            (str(row["method"]), str(row["comparison_protocol"]), float(row["factor_level"])),
            [],
        ).append(row)
    curves: Dict[Tuple[str, str], List[Dict[str, float]]] = {}
    for (method, protocol, level), values in grouped.items():
        detected = [finite_or_nan(row.get("target_detected")) for row in values]
        detected = [value for value in detected if math.isfinite(value)]
        measured_scnr = [finite_or_nan(row.get("input_SCNR_dB")) for row in values]
        measured_scnr = [value for value in measured_scnr if math.isfinite(value)]
        successes = int(sum(value >= 0.5 for value in detected))
        low, high = wilson_ci95(successes, len(detected))
        curves.setdefault((method, protocol), []).append(
            {
                "scnr": float(np.mean(measured_scnr)) if measured_scnr else level,
                "pd": float(np.mean(detected)) if detected else math.nan,
                "low": low,
                "high": high,
            }
        )
    fig, ax = plt.subplots(figsize=(10.5, 6.0))
    for (method, protocol), points in curves.items():
        points = sorted(points, key=lambda point: point["scnr"])
        x = np.asarray([point["scnr"] for point in points])
        y = np.asarray([point["pd"] for point in points])
        low = np.asarray([point["low"] for point in points])
        high = np.asarray([point["high"] for point in points])
        valid = np.isfinite(y)
        label = method.replace(" [scientific controlled]", "").replace(" [production replay]", "")
        if protocol == "production_replay":
            label += " [prod]"
        ax.errorbar(x[valid], y[valid], yerr=np.vstack((y[valid] - low[valid], high[valid] - y[valid])), marker="o", capsize=2, linewidth=1.1, label=label, color=METHOD_PALETTE.get(method, "#333333"))
    ax.set_xlabel("Measured input SCNR (dB)")
    ax.set_ylabel("Empirical Pd (5-seed aggregate)")
    ax.set_ylim(-0.05, 1.05)
    ax.set_title("Empirical Pd versus target SNR")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    ax.legend(fontsize=6.5, ncol=2)
    fig.tight_layout()
    fig.savefig(out_dir / "baseline_v2_pd_scnr.png", dpi=160)
    plt.close(fig)


def plot_pareto_tradeoffs(
    rows: List[Dict[str, object]],
    out_dir: Path,
    roc_summary: List[Dict[str, object]] | None = None,
) -> None:
    if not rows:
        return
    grouped: Dict[Tuple[str, str], List[Dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault((str(row.get("method", "")), str(row.get("comparison_protocol", ""))), []).append(row)
    labels = list(grouped)

    fig, ax = plt.subplots(figsize=(10.5, 6.0))
    for method, protocol in labels:
        values = grouped[(method, protocol)]
        x = np.asarray([finite_or_nan(row.get("target_loss_dB")) for row in values])
        y = np.asarray([finite_or_nan(row.get("residual_suppression_dB")) for row in values])
        valid = np.isfinite(x) & np.isfinite(y)
        if not np.any(valid):
            continue
        label = method.replace(" [scientific controlled]", "").replace(" [production replay]", "")
        if protocol == "production_replay":
            label += " [prod]"
        ax.scatter(float(np.mean(x[valid])), float(np.mean(y[valid])), s=65, label=label, color=METHOD_PALETTE.get(method, "#333333"))
    ax.axvline(0.0, color="#555555", linewidth=0.8)
    ax.set_xlabel("Target preservation: target loss (dB; 0 is no loss)")
    ax.set_ylabel("Residual suppression (dB)")
    ax.set_title("Pareto view: residual suppression versus target preservation")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    ax.legend(fontsize=6.5, ncol=2)
    fig.tight_layout()
    fig.savefig(out_dir / "baseline_v2_pareto_suppression_target.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10.5, 6.0))
    if roc_summary:
        for (method, protocol), points in sorted({
            (str(row["method"]), str(row["comparison_protocol"])): [] for row in roc_summary
        }.items()):
            points = [row for row in roc_summary if str(row["method"]) == method and str(row["comparison_protocol"]) == protocol]
            x = np.asarray([max(finite_or_nan(row.get("background_Pfa_mean")), 1.0e-8) for row in points])
            y = np.asarray([finite_or_nan(row.get("Pd_mean")) for row in points])
            valid = np.isfinite(x) & np.isfinite(y)
            if np.any(valid):
                ax.plot(x[valid], y[valid], marker="o", linewidth=1.0, markersize=3.0, label=method.replace(" [scientific controlled]", "").replace(" [production replay]", ""), color=METHOD_PALETTE.get(method, "#333333"))
    else:
        for method, protocol in labels:
            values = grouped[(method, protocol)]
            x = np.asarray([finite_or_nan(row.get("background_Pfa")) for row in values])
            y = np.asarray([finite_or_nan(row.get("target_detected")) for row in values])
            valid = np.isfinite(x) & np.isfinite(y)
            if np.any(valid):
                ax.scatter(max(float(np.mean(x[valid])), 1.0e-8), float(np.mean(y[valid])), s=65, label=method.replace(" [scientific controlled]", "").replace(" [production replay]", ""), color=METHOD_PALETTE.get(method, "#333333"))
    ax.set_xscale("log")
    ax.set_xlabel("Background Pfa")
    ax.set_ylabel("Empirical Pd")
    ax.set_ylim(-0.05, 1.05)
    ax.set_title("Pareto view: false alarms versus empirical detection")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    ax.legend(fontsize=6.5, ncol=2)
    fig.tight_layout()
    fig.savefig(out_dir / "baseline_v2_pareto_pfa_pd.png", dpi=160)
    plt.close(fig)


def plot_summary(rows: List[Dict[str, object]], out_dir: Path) -> None:
    if not rows:
        return
    grouped: Dict[str, List[Dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["method"]), []).append(row)
    methods = [method for method in METHODS if method in grouped]
    fig, ax = plt.subplots(figsize=(11, 6))
    for method in methods:
        values = [finite_or_nan(row.get("SCNR_improvement_dB")) for row in grouped[method]]
        target_loss = [finite_or_nan(row.get("target_loss_dB")) for row in grouped[method]]
        x = float(np.mean([finite_or_nan(row.get("runtime_ms")) for row in grouped[method]]))
        y = float(np.mean([value for value in values if math.isfinite(value)]))
        loss = float(np.mean([value for value in target_loss if math.isfinite(value)]))
        ax.scatter(x, y, s=70, color=METHOD_PALETTE.get(method, "#333333"), label=method)
        ax.annotate(method.replace(" [scientific controlled]", "").replace(" [production replay]", ""), (x, y), xytext=(4, 4), textcoords="offset points", fontsize=7)
        _ = loss
    ax.axhline(0.0, color="#555555", linewidth=0.8)
    ax.set_xlabel("Mean CPU method runtime (ms)")
    ax.set_ylabel("Mean SCNR improvement (dB)")
    ax.set_title("Baseline V2 performance versus computational cost")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    fig.tight_layout()
    fig.savefig(out_dir / "baseline_v2_pareto_runtime_scnr.png", dpi=160)
    plt.close(fig)

    cases = list(dict.fromkeys(str(row["case_id"]) for row in rows))
    matrix = np.full((len(cases), len(methods)), np.nan, dtype=float)
    for row in rows:
        i = cases.index(str(row["case_id"]))
        j = methods.index(str(row["method"]))
        matrix[i, j] = finite_or_nan(row.get("SCNR_improvement_dB"))
    fig, ax = plt.subplots(figsize=(15, max(5.5, 0.35 * len(cases))))
    image = ax.imshow(matrix, aspect="auto", cmap="viridis")
    ax.set_xticks(np.arange(len(methods)), methods, rotation=35, ha="right")
    ax.set_yticks(np.arange(len(cases)), cases)
    ax.set_title("Baseline V2 SCNR improvement by case and method")
    ax.set_xlabel("Method; production and scientific protocols remain separate")
    ax.set_ylabel("Case")
    fig.colorbar(image, ax=ax, label="SCNR improvement (dB)")
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            if math.isfinite(matrix[i, j]):
                ax.text(j, i, f"{matrix[i, j]:.1f}", ha="center", va="center", fontsize=6, color="white")
    fig.tight_layout()
    fig.savefig(out_dir / "baseline_v2_scnr_heatmap.png", dpi=160)
    plt.close(fig)


def plot_velocity(rows: List[Dict[str, object]], out_dir: Path) -> None:
    velocity_rows = [row for row in rows if str(row.get("factor")) == "target_radial_speed_mps"]
    if not velocity_rows:
        return
    grouped: Dict[Tuple[str, str], List[Dict[str, object]]] = {}
    for row in velocity_rows:
        signed_speed = finite_or_nan(row.get("target_radial_speed_config_mps"))
        direction = "approaching" if signed_speed < 0.0 else ("receding" if signed_speed > 0.0 else "zero")
        grouped.setdefault((str(row["method"]), direction), []).append(row)
    fig, ax = plt.subplots(figsize=(11, 6))
    for method in METHODS:
        for direction in ("approaching", "receding", "zero"):
            if (method, direction) not in grouped:
                continue
            method_rows = grouped[(method, direction)]
            by_speed: Dict[float, List[float]] = {}
            for row in method_rows:
                speed = abs(float(row.get("target_radial_speed_config_mps", math.nan)))
                by_speed.setdefault(speed, []).append(float(row.get("target_detected", math.nan)))
            xs = sorted(by_speed)
            ys = [float(np.nanmean(by_speed[x])) for x in xs]
            lows = []
            highs = []
            for x in xs:
                values = [value for value in by_speed[x] if math.isfinite(value)]
                low, high = wilson_ci95(int(sum(value >= 0.5 for value in values)), len(values))
                lows.append(low)
                highs.append(high)
            ax.errorbar(
                xs,
                ys,
                yerr=np.vstack((np.asarray(ys) - np.asarray(lows), np.asarray(highs) - np.asarray(ys))),
                marker="o",
                capsize=2,
                linewidth=1.1,
                label=f"{method} [{direction}]",
                color=METHOD_PALETTE.get(method, "#333333"),
                linestyle="--" if direction == "receding" else "-",
            )
    ax.set_xlabel("Regenerated target radial speed magnitude (m/s)")
    ax.set_ylabel("Empirical Pd (target_detected across seeds)")
    ax.set_ylim(-0.05, 1.05)
    ax.set_title("Physical velocity sweep: approaching and receding reported separately")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(out_dir / "baseline_v2_velocity_pd.png", dpi=160)
    plt.close(fig)


def plot_features(features: List[Dict[str, object]], out_dir: Path) -> None:
    if not features:
        return
    x = np.asarray([finite_or_nan(row.get("valid_pulse_fraction")) for row in features], dtype=float)
    y = np.asarray([finite_or_nan(row.get("current_SCNR_improvement_dB")) for row in features], dtype=float)
    z = np.asarray([finite_or_nan(row.get("best_adaptive_SCNR_improvement_dB")) for row in features], dtype=float)
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    valid_current = np.isfinite(x) & np.isfinite(y)
    valid_adaptive = np.isfinite(x) & np.isfinite(z)
    ax.scatter(x[valid_current], y[valid_current], color="#1f77b4", label="Current production")
    ax.scatter(x[valid_adaptive], z[valid_adaptive], color="#d62728", marker="x", label="Best adaptive")
    ax.set_xlabel("Valid pulse fraction")
    ax.set_ylabel("SCNR improvement (dB)")
    ax.set_title("Feature feasibility: sample validity versus baseline outcome")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "baseline_v2_feature_valid_fraction.png", dpi=160)
    plt.close(fig)

    condition = np.asarray([finite_or_nan(row.get("dl_global_covariance_condition_number")) for row in features], dtype=float)
    residual_p99 = np.asarray([finite_or_nan(row.get("current_residual_p99_dB_over_input_median")) for row in features], dtype=float)
    ridge = np.asarray([finite_or_nan(row.get("distance_to_clutter_ridge_hz")) for row in features], dtype=float)
    texture_cv = np.asarray([finite_or_nan(row.get("texture_nonuniformity_cv")) for row in features], dtype=float)
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8))
    valid_condition = np.isfinite(condition) & np.isfinite(z)
    axes[0].scatter(np.log10(np.maximum(condition[valid_condition], 1.0)), z[valid_condition], color="#9467bd")
    axes[0].set_xlabel("log10(global DL covariance condition number)")
    axes[0].set_ylabel("Best adaptive SCNR improvement (dB)")
    axes[0].set_title("Covariance conditioning versus adaptive outcome")
    valid_tail = np.isfinite(ridge) & np.isfinite(residual_p99)
    axes[1].scatter(ridge[valid_tail], residual_p99[valid_tail], c=texture_cv[valid_tail], cmap="viridis", s=28)
    axes[1].set_xlabel("Distance to clutter ridge (Hz)")
    axes[1].set_ylabel("Current residual p99 (dB over input median)")
    axes[1].set_title("Input geometry and Current residual tail")
    for axis in axes:
        axis.grid(True, color="#dddddd", linewidth=0.6)
    fig.tight_layout()
    fig.savefig(out_dir / "baseline_v2_feature_diagnostics.png", dpi=160)
    plt.close(fig)


def run_analysis(
    base_config_path: Path,
    suite: Dict[str, object],
    cases: List[Dict[str, object]],
    out_dir: Path,
    method_config: Dict[str, object],
    keep_data: bool,
    workers: int = 1,
    roc_scales: Sequence[float] | None = None,
    detection_threshold_scale: float = 1.0,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], List[Dict[str, object]], List[Dict[str, object]]]:
    rows: List[Dict[str, object]] = []
    features: List[Dict[str, object]] = []
    failures: List[Dict[str, object]] = []
    roc_rows: List[Dict[str, object]] = []

    def failure_rows(case: Dict[str, object], error: str) -> List[Dict[str, object]]:
        failed: List[Dict[str, object]] = []
        for method in METHODS:
            failed.append(
                {
                    "case_id": str(case.get("case_id", "")),
                    "factor": str(case.get("factor", "explicit")),
                    "factor_level": case.get("factor_level", "explicit"),
                    "seed": int(case.get("seed", -1)),
                    "scenario_label": str(case.get("label", case.get("case_id", ""))),
                    "method": method,
                    "comparison_protocol": "production_replay" if method in STRICT_METHODS[:2] else "scientific_controlled",
                    "method_family": "failed_case",
                    "status": "failed",
                    "failure_reason": error,
                }
            )
        return failed

    def execute(index: int, case: Dict[str, object]) -> Tuple[int, Dict[str, object], List[Dict[str, object]], Dict[str, object] | None, List[Dict[str, object]], str | None]:
        print(json.dumps({"stage": "case", "index": index + 1, "total": len(cases), "case_id": case["case_id"], "factor": case.get("factor"), "level": case.get("factor_level")}, ensure_ascii=False), flush=True)
        try:
            case_rows, case_features, case_roc_rows = run_case(
                base_config_path,
                suite,
                case,
                method_config,
                keep_data,
                roc_scales=roc_scales,
                detection_threshold_scale=detection_threshold_scale,
            )
            return index, case, case_rows, case_features, case_roc_rows, None
        except Exception as exc:
            return index, case, failure_rows(case, repr(exc)), None, [], repr(exc)

    ordered_results: List[Tuple[int, Dict[str, object], List[Dict[str, object]], Dict[str, object] | None, List[Dict[str, object]], str | None]] = []
    worker_count = max(1, int(workers))
    if worker_count == 1:
        ordered_results = [execute(index, case) for index, case in enumerate(cases)]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(execute, index, case) for index, case in enumerate(cases)]
            ordered_results = [future.result() for future in concurrent.futures.as_completed(futures)]
        ordered_results.sort(key=lambda result: result[0])

    for _, case, case_rows, case_features, case_roc_rows, error in ordered_results:
        rows.extend(case_rows)
        roc_rows.extend(case_roc_rows)
        if case_features is not None:
            features.append(case_features)
        if error is not None:
            failures.append({"case_id": case.get("case_id"), "error": error})
            print(f"[baseline-v2][ERR] case {case.get('case_id')}: {error}", file=sys.stderr, flush=True)
    features = enrich_feature_diagnostics(features, rows)
    write_csv(out_dir / "baseline_v2_summary.csv", rows)
    write_csv(out_dir / "baseline_v2_sweep_summary.csv", aggregate_sweeps(rows))
    write_csv(out_dir / "baseline_v2_feature_diagnostics.csv", features)
    write_csv(out_dir / "baseline_v2_feature_correlations.csv", feature_correlations(features))
    write_csv(out_dir / "baseline_v2_feature_light_predictor.csv", feature_light_predictor(features))
    plot_summary(rows, out_dir)
    plot_velocity(rows, out_dir)
    plot_pd_scnr(rows, out_dir)
    plot_pareto_tradeoffs(rows, out_dir)
    plot_features(features, out_dir)
    if roc_rows:
        roc_summary = aggregate_roc_points(roc_rows)
        write_csv(out_dir / "baseline_v2_roc_points.csv", roc_rows)
        write_csv(out_dir / "baseline_v2_roc_summary.csv", roc_summary)
        plot_roc(roc_summary, out_dir)
        plot_pareto_tradeoffs(rows, out_dir, roc_summary=roc_summary)
    return rows, features, failures, roc_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path("configs/research/ai_csi_baseline_v2_suite.json"))
    parser.add_argument("--mode", choices=("sanity", "screen", "formal", "velocity", "roc", "transition", "mixed_stress", "explicit"), default="screen")
    parser.add_argument("--out", type=Path, default=Path("outputs/ai_csi_baseline_v2"))
    parser.add_argument("--keep-data", action="store_true", help="retain generated BINs; only for one-case debugging")
    parser.add_argument("--max-cases", type=int, default=0, help="development limit; omit for the configured matrix")
    parser.add_argument("--workers", type=int, default=1, help="parallel case workers; default 1 for deterministic low-resource runs")
    args = parser.parse_args()
    out_dir = ROOT / args.out if not args.out.is_absolute() else args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "generated_configs").mkdir(parents=True, exist_ok=True)
    Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)
    suite_path = ROOT / args.suite if not args.suite.is_absolute() else args.suite
    base_config_path, suite = load_suite(suite_path)

    if args.mode == "sanity":
        result = steering_sanity(out_dir, base_config_path, suite)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=json_default))
        return 0 if result["all_pass"] else 2

    sanity_path = out_dir / "steering_sanity.json"
    if not sanity_path.exists():
        sanity = steering_sanity(out_dir, base_config_path, suite)
    else:
        sanity = json.loads(sanity_path.read_text(encoding="utf-8"))
    if not bool(sanity.get("all_pass", False)):
        raise RuntimeError("steering sanity failed; formal performance matrix is refused")
    cases = expand_cases(suite, args.mode)
    if args.max_cases > 0:
        cases = cases[: args.max_cases]
    method_config = dict(suite.get("method_config", {}))
    roc_scales = None
    detection_threshold_scale = 1.0
    if args.mode in {"roc", "transition"}:
        roc_scales = suite.get("roc_sweep", {}).get("threshold_scales", DEFAULT_ROC_SCALES)
    if args.mode == "transition":
        detection_threshold_scale = float(
            suite.get("transition_sweep", {}).get("target_detection_threshold_scale", 1.0)
        )
    rows, features, failures, roc_rows = run_analysis(
        base_config_path,
        suite,
        cases,
        out_dir,
        method_config,
        args.keep_data,
        args.workers,
        roc_scales=roc_scales,
        detection_threshold_scale=detection_threshold_scale,
    )
    mdv_rows = summarize_mdv(rows, suite)
    write_csv(out_dir / "baseline_v2_mdv_summary.csv", mdv_rows)
    write_json(out_dir / "physics_expert_map_schema_v1.json", physics_expert_map_schema())
    write_json(out_dir / "pgrcc_v1_training_schema.json", pgrcc_v1_training_schema())
    provenance = v1.reproducibility_provenance([sys.executable, *sys.argv])
    manifest = {
        "script": "scripts/run_baseline_v2.py",
        "suite": relative_repo_path(ROOT / args.suite if not args.suite.is_absolute() else args.suite),
        "mode": args.mode,
        "case_count_requested": len(cases),
        "case_count_completed": len(features),
        "workers": max(1, int(args.workers)),
        "row_count": len(rows),
        "failures": failures,
        "methods": METHODS,
        "strict_protocols": {
            "production_replay": "Current and Phase-only keep target-observation P38 exactly to describe current production behavior.",
            "scientific_controlled": "Current/Phase-only frozen background P38 and Row-LS use paired C+N; target truth is evaluation-only.",
        },
        "adaptive_policy": {
            "training_source": "paired C+N background only",
            "training_modes": ["global", "local"],
            "cut_guard_bins": method_config.get("cut_guard_bins", 4),
            "diagonal_loading_fraction": method_config.get("diagonal_loading_fraction", 1.0e-2),
            "range_block_size": method_config.get("range_block_size", 64),
            "steering": "physical receive path difference plus clutter-ridge Doppler; corrected JDL uses a 12-D temporal/spatial steering vector",
            "SA_MNEC": "approximate academic reference; not a full paper reproduction",
        },
        "metrics": {
            "formal_suppression": "residual_suppression_dB only; old CA_dB and CSR_dB duplicate formula was removed",
            "fixed_region": "all Doppler rows and configured rg_st..rg_ed range columns",
            "target_loss": "10log10(target signal ROI power after / before)",
            "target_detected": "single-case GO-CFAR ROI indicator; never interpreted as a probability at row level",
            "Pd": "empirical Bernoulli detection probability aggregated across independent seeds/trials with Wilson 95% intervals",
            "ROC": "dedicated roc mode sweeps the GO-CFAR threshold scale over regenerated target/background cases",
            "MDV": "only reported from regenerated physical velocity sweep, never from RD np.roll",
            "default_detection_threshold_scale": detection_threshold_scale,
        },
        "mdv_summary": relative_repo_path(out_dir / "baseline_v2_mdv_summary.csv") if mdv_rows else None,
        "roc_points": relative_repo_path(out_dir / "baseline_v2_roc_points.csv") if roc_rows else None,
        "roc_summary": relative_repo_path(out_dir / "baseline_v2_roc_summary.csv") if roc_rows else None,
        "raw_data_policy": "generated per-case BIN/config directories are removed after processing unless --keep-data is explicitly supplied",
        "sanity": sanity,
        "reproducibility": provenance,
        "evidence_boundary": "CPU offline baseline unless a real CUDA device and a separate production run are available",
    }
    velocity_dir = ROOT / "outputs/ai_csi_baseline_v21_velocity"
    roc_dir = ROOT / "outputs/ai_csi_baseline_v21_roc"
    transition_dir = ROOT / "outputs/ai_csi_baseline_v21_transition"
    if (velocity_dir / "baseline_v2_mdv_summary.csv").exists():
        manifest["velocity_evidence"] = {
            "summary": relative_repo_path(velocity_dir / "baseline_v2_summary.csv"),
            "sweep_summary": relative_repo_path(velocity_dir / "baseline_v2_sweep_summary.csv"),
            "mdv_summary": relative_repo_path(velocity_dir / "baseline_v2_mdv_summary.csv"),
            "pd_curve": relative_repo_path(velocity_dir / "baseline_v2_velocity_pd.png"),
            "definition": "full regenerated 7-point velocity sweep; root screen output is not used for MDV",
        }
    if (roc_dir / "baseline_v2_roc_summary.csv").exists():
        manifest["roc_evidence"] = {
            "points": relative_repo_path(roc_dir / "baseline_v2_roc_points.csv"),
            "summary": relative_repo_path(roc_dir / "baseline_v2_roc_summary.csv"),
            "plot": relative_repo_path(roc_dir / "baseline_v2_pfa_roc.png"),
            "definition": "5 regenerated seeds and configured threshold-scale operating points",
        }
    if (transition_dir / "baseline_v2_roc_summary.csv").exists():
        manifest["transition_evidence"] = {
            "points": relative_repo_path(transition_dir / "baseline_v2_roc_points.csv"),
            "summary": relative_repo_path(transition_dir / "baseline_v2_roc_summary.csv"),
            "pd_scnr": relative_repo_path(transition_dir / "baseline_v2_pd_scnr.png"),
            "definition": "pilot plus refined lower-SNR regenerated cases; refined seed/trial groups are explicit in the case manifest",
        }
    write_json(out_dir / "baseline_v2_manifest.json", manifest)
    print(json.dumps({"cases": len(features), "rows": len(rows), "failures": len(failures), "output": str(out_dir)}, ensure_ascii=False, indent=2))
    return 0 if not failures else 3


if __name__ == "__main__":
    raise SystemExit(main())
