#!/usr/bin/env python3
"""Run the blind true-geometry baseline-error observability matrix.

Each generated case contains a true-vs-reported four-channel geometry.  The
estimator is called once with the ``unknown_off`` IQ file, reported positions,
and a deliberately filtered nominal metadata object.  Truth is retained only
by this outer evaluator for error statistics; it is never passed to the
estimator.  Stage2 raw files are temporary by default so the committed
artifact remains a compact, auditable matrix rather than a raw-data bundle.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import importlib.util
import json
import math
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "configs/research/unknown_system_error_true_geometry_pilot.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
ANALYZER_PATH = ROOT / "scripts/analyze_four_channel_observables.py"
DEFAULT_LEVELS_M = (0.0, 0.001, -0.001, 0.0025, -0.0025,
                    0.005, -0.005, 0.01, -0.01)
DEFAULT_SEEDS = (101, 202, 303)
REQUIRED_ANGLES_DEG = (-20.0, -10.0, -5.0, 0.0, 5.0, 10.0, 20.0)
FIXED_PHASE_MISMATCH_DEG = 8.0
AMP_MISMATCH_DB = 0.35


def _load_analyzer():
    spec = importlib.util.spec_from_file_location("four_channel_observables", ANALYZER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ANALYZER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OBS = _load_analyzer()


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "--git-dir=.git-real", "--work-tree=.", *args],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


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
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in materialized:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(materialized)


def _finite_float(value: object) -> Optional[float]:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def bootstrap_mean_ci(
    values: Sequence[float] | np.ndarray,
    seed: int = 20260914,
    resamples: int = 4000,
) -> tuple[Optional[float], Optional[float]]:
    """Return a deterministic percentile-bootstrap 95% CI for a sample mean."""

    sample = np.asarray(values, dtype=np.float64).reshape(-1)
    sample = sample[np.isfinite(sample)]
    if sample.size == 0:
        return None, None
    if resamples <= 0:
        raise ValueError("resamples must be positive")
    rng = np.random.default_rng(seed)
    means = np.mean(
        sample[rng.integers(0, sample.size, size=(resamples, sample.size))],
        axis=1,
    )
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def estimator_metadata(resolved: Mapping[str, object]) -> dict[str, object]:
    """Select only nominal metadata allowed by the unknown-only contract."""

    allowed: dict[str, object] = {}
    for key in ("waveform", "range_processing", "platform", "simulation_geometry"):
        if key in resolved:
            allowed[key] = copy.deepcopy(resolved[key])
    scene = resolved.get("scene")
    if isinstance(scene, Mapping) and "ground_z_m" in scene:
        allowed["scene"] = {"ground_z_m": copy.deepcopy(scene["ground_z_m"])}
    for key in ("carrier_phase_sign", "sample_delay_us", "range_geometry"):
        if key in resolved:
            allowed[key] = copy.deepcopy(resolved[key])
    return allowed


def _delta_tag(delta_m: float) -> str:
    if abs(delta_m) < 1.0e-12:
        return "zero"
    sign = "p" if delta_m > 0.0 else "m"
    return f"{sign}{abs(delta_m) * 1000.0:.3f}mm".replace(".", "p")


def _nuisance_float(
    nuisance: Mapping[str, object], key: str, default: float
) -> float:
    value = _finite_float(nuisance.get(key, default))
    if value is None:
        raise ValueError(f"nuisance.{key} must be finite")
    return value


def apply_nuisance_config(
    config: Mapping[str, object], nuisance: Mapping[str, object] | None = None
) -> dict[str, object]:
    """Apply an explicit one-factor nuisance profile to a matrix template."""

    result = copy.deepcopy(config)
    profile = dict(nuisance or {})
    impairments = result.setdefault("channel_impairments", {})
    if not isinstance(impairments, dict):
        raise ValueError("channel_impairments must be an object")
    impairments.update({
        "enabled": True,
        "baseline_error_m": 0.0,
        "baseline_error_mode": "group_baseline_error_legacy_pilot",
        "channel_amp_mismatch_db": _nuisance_float(
            profile, "fixed_channel_amp_mismatch_db", AMP_MISMATCH_DB
        ),
        "channel_fixed_phase_mismatch_deg": _nuisance_float(
            profile, "fixed_channel_phase_mismatch_deg", FIXED_PHASE_MISMATCH_DEG
        ),
        "channel_phase_jitter_std_deg": 0.0,
        "per_pulse_phase_drift_deg": 0.0,
    })

    scene = result.setdefault("scene", {})
    if not isinstance(scene, dict):
        raise ValueError("scene must be an object")
    area = scene.setdefault("area_clutter", {})
    if not isinstance(area, dict):
        raise ValueError("scene.area_clutter must be an object")
    if "texture_sigma" in profile:
        texture_sigma = _nuisance_float(profile, "texture_sigma", 0.0)
        if texture_sigma < 0.0:
            raise ValueError("nuisance.texture_sigma must be non-negative")
        area["texture_sigma"] = texture_sigma
    if "calibration_range_m" in profile:
        calibration_range = _nuisance_float(profile, "calibration_range_m", 9000.0)
        if calibration_range <= 0.0:
            raise ValueError("nuisance.calibration_range_m must be positive")
        area["calibration_range_m"] = calibration_range
        old_min = _finite_float(scene.get("range_min_m"))
        old_max = _finite_float(scene.get("range_max_m"))
        half_width = 750.0
        if old_min is not None and old_max is not None and old_max > old_min:
            half_width = max(500.0, 0.5 * (old_max - old_min))
        scene["range_min_m"] = calibration_range - half_width
        scene["range_max_m"] = calibration_range + half_width
    noise = scene.setdefault("thermal_noise", {})
    if not isinstance(noise, dict):
        raise ValueError("scene.thermal_noise must be an object")
    if "noise_power" in profile:
        noise_power = _nuisance_float(profile, "noise_power", 0.0)
        if noise_power < 0.0:
            raise ValueError("nuisance.noise_power must be non-negative")
        noise["noise_power"] = noise_power

    if "angle_span_deg" in profile:
        span = _nuisance_float(profile, "angle_span_deg", 20.0)
        if span <= 0.0:
            raise ValueError("nuisance.angle_span_deg must be positive")
        scan = result.setdefault("scan", {})
        if not isinstance(scan, dict):
            raise ValueError("scan must be an object")
        step = abs(float(scan.get("scan_step_deg", 5.0)))
        if step <= 0.0:
            raise ValueError("scan_step_deg must be positive")
        beam_count = int(round(2.0 * span / step)) + 1
        if beam_count < 3:
            raise ValueError("nuisance.angle_span_deg must produce at least 3 beams")
        scan["scan_min_deg"] = -span
        scan["beam_count"] = beam_count
        random_cfg = result.setdefault("random", {})
        if not isinstance(random_cfg, dict):
            raise ValueError("random must be an object")
        random_cfg["beam_count"] = beam_count
        scene["azimuth_min_deg"] = -span - step
        scene["azimuth_max_deg"] = span + step
    return result


def _make_case_config(
    template: Mapping[str, object],
    delta_m: float,
    seed: int,
    case_id: str,
    output_dir: Path,
) -> dict[str, object]:
    config = copy.deepcopy(template)
    config["case_id"] = case_id
    config["output_dir"] = str(output_dir)
    config["truth_output"] = False
    random_cfg = config.setdefault("random", {})
    if not isinstance(random_cfg, dict):
        raise ValueError("random config must be an object")
    random_cfg["random_seed"] = int(seed)
    scene = config.setdefault("scene", {})
    if not isinstance(scene, dict):
        raise ValueError("scene config must be an object")
    scene["signal_only"] = False
    for target in config.get("targets", []):
        if isinstance(target, dict):
            target["enabled"] = False

    geometry = config.get("channel_geometry")
    if not isinstance(geometry, dict):
        raise ValueError("matrix requires channel_geometry")
    if geometry.get("mode") != "true_channel_positions":
        raise ValueError("matrix requires channel_geometry.mode=true_channel_positions")
    for channel_index in (2, 4):
        channel = geometry.get(f"channel_{channel_index}")
        if not isinstance(channel, dict):
            raise ValueError(f"missing channel_{channel_index} geometry")
        reported = channel.get("reported")
        true = channel.get("true")
        if not isinstance(reported, dict) or not isinstance(true, dict):
            raise ValueError(f"channel_{channel_index} needs reported and true geometry")
        if "x_m" not in reported:
            raise ValueError(f"channel_{channel_index}.reported.x_m is missing")
        true["x_m"] = float(reported["x_m"]) + float(delta_m)
        for key in ("y_m", "z_m"):
            true[key] = float(reported.get(key, true.get(key, 0.0)))

    impairments = config.setdefault("channel_impairments", {})
    if not isinstance(impairments, dict):
        raise ValueError("channel_impairments must be an object")
    # These nuisance terms are fixed across the matrix and are absorbed by
    # per-pair intercepts; only the geometry offset changes with the case.
    impairments.update({
        "enabled": True,
        "baseline_error_m": 0.0,
        "baseline_error_mode": "group_baseline_error_legacy_pilot",
        "channel_amp_mismatch_db": float(
            impairments.get("channel_amp_mismatch_db", AMP_MISMATCH_DB)
        ),
        "channel_fixed_phase_mismatch_deg": float(
            impairments.get("channel_fixed_phase_mismatch_deg", FIXED_PHASE_MISMATCH_DEG)
        ),
        "channel_phase_jitter_std_deg": 0.0,
        "per_pulse_phase_drift_deg": 0.0,
    })
    return config


def _find_period_file(case_dir: Path) -> Path:
    manifest = case_dir / "data/period_files.csv"
    if not manifest.is_file():
        raise RuntimeError(f"period manifest is missing: {manifest}")
    with manifest.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise RuntimeError(f"matrix expects one period, got {len(rows)} in {manifest}")
    path = Path(rows[0]["file"])
    if not path.is_absolute():
        candidates = ((case_dir / path).resolve(), (ROOT / path).resolve())
        path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    if not path.is_file():
        raise RuntimeError(f"period file is missing: {path}")
    return path


def _run_stage2(simulator: Path, config_path: Path, log_path: Path) -> tuple[int, float]:
    if not simulator.is_file():
        raise RuntimeError(f"missing simulator: {simulator}")
    command = [str(simulator), "--config", str(config_path)]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("command=" + " ".join(command) + "\n")
        log.flush()
        completed = subprocess.run(
            command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False
        )
        log.write(f"exit_code={completed.returncode}\n")
    return completed.returncode, time.perf_counter() - start


def _fit_field(result: Mapping[str, object], field: str) -> Optional[float]:
    return _finite_float(result.get(field))


def summarize_case(
    truth_delta_m: float,
    six_pair_result: Mapping[str, object],
    single_pair_result: Mapping[str, object],
) -> dict[str, object]:
    """Flatten one case while keeping truth as evaluator-only data."""

    six_estimate = _fit_field(six_pair_result, "estimated_baseline_error_m")
    single_estimate = _fit_field(single_pair_result, "estimated_baseline_error_m")
    six_error = None if six_estimate is None else six_estimate - truth_delta_m
    single_error = None if single_estimate is None else single_estimate - truth_delta_m
    global_fit = six_pair_result.get("global_fit", {})
    if not isinstance(global_fit, Mapping):
        global_fit = {}
    consistency = six_pair_result.get("cross_pair_consistency", {})
    if not isinstance(consistency, Mapping):
        consistency = {}
    pair_fits = six_pair_result.get("pair_fits", {})
    if not isinstance(pair_fits, Mapping):
        pair_fits = {}
    row: dict[str, object] = {
        "truth_delta_m": float(truth_delta_m),
        "six_fit_status": six_pair_result.get("fit_status"),
        "six_estimate_m": six_estimate,
        "six_error_m": six_error,
        "six_abs_error_m": None if six_error is None else abs(six_error),
        "single_fit_status": single_pair_result.get("fit_status"),
        "single_estimate_m": single_estimate,
        "single_error_m": single_error,
        "single_abs_error_m": None if single_error is None else abs(single_error),
        "six_pair_residual_rmse_rad": _finite_float(global_fit.get("rmse_rad")),
        "closure_residual_mean_abs_rad": _fit_field(
            six_pair_result, "closure_phase_residual_rad"
        ),
        "closure_residual_p95_rad": _fit_field(
            six_pair_result, "closure_phase_p95_rad"
        ),
        "cross_pair_consistency_m": _finite_float(
            consistency.get("max_minus_min_delta_d_m")
        ),
        "six_observation_count": six_pair_result.get("observation_counts"),
        "six_fallback": six_pair_result.get("fit_status") == "fallback_unidentifiable",
        "six_failure": six_pair_result.get("fit_status") != "fit",
        "single_fallback": single_pair_result.get("fit_status") == "fallback_unidentifiable",
        "single_failure": single_pair_result.get("fit_status") != "fit",
    }
    for pair_name in (item[0] for item in OBS.PAIR_DEFINITIONS):
        pair_fit = pair_fits.get(pair_name, {})
        if not isinstance(pair_fit, Mapping):
            pair_fit = {}
        row[f"{pair_name}_estimate_m"] = _finite_float(
            pair_fit.get("estimated_delta_d_m")
        )
        row[f"{pair_name}_residual_rmse_rad"] = _finite_float(pair_fit.get("rmse_rad"))
        row[f"{pair_name}_observation_count"] = pair_fit.get("observation_count")
    return row


def _aggregate_level(
    truth_delta_m: float,
    rows: Sequence[Mapping[str, object]],
    level_index: int,
    bootstrap_resamples: int,
) -> dict[str, object]:
    def values(field: str) -> np.ndarray:
        return np.asarray(
            [float(row[field]) for row in rows
             if _finite_float(row.get(field)) is not None],
            dtype=np.float64,
        )

    six_estimates = values("six_estimate_m")
    single_estimates = values("single_estimate_m")
    six_errors = values("six_error_m")
    single_errors = values("single_error_m")
    six_ci = bootstrap_mean_ci(
        six_estimates, seed=20260914 + level_index, resamples=bootstrap_resamples
    )
    single_ci = bootstrap_mean_ci(
        single_estimates, seed=20261914 + level_index, resamples=bootstrap_resamples
    )
    attempted = len(rows)
    six_fit_count = int(six_estimates.size)
    single_fit_count = int(single_estimates.size)

    def mean_or_none(sample: np.ndarray) -> Optional[float]:
        return None if sample.size == 0 else float(np.mean(sample))

    def rmse_or_none(sample: np.ndarray) -> Optional[float]:
        return None if sample.size == 0 else float(math.sqrt(np.mean(sample * sample)))

    return {
        "truth_delta_m": float(truth_delta_m),
        "case_count": attempted,
        "six_fit_count": six_fit_count,
        "six_fit_failure_rate": None if attempted == 0 else 1.0 - six_fit_count / attempted,
        "six_fallback_rate": None if attempted == 0 else sum(
            bool(row.get("six_fallback")) for row in rows
        ) / attempted,
        "six_estimate_mean_m": mean_or_none(six_estimates),
        "six_estimate_ci95_low_m": six_ci[0],
        "six_estimate_ci95_high_m": six_ci[1],
        "six_bias_mean_m": mean_or_none(six_errors),
        "six_rmse_m": rmse_or_none(six_errors),
        "single_fit_count": single_fit_count,
        "single_fit_failure_rate": None if attempted == 0 else 1.0 - single_fit_count / attempted,
        "single_fallback_rate": None if attempted == 0 else sum(
            bool(row.get("single_fallback")) for row in rows
        ) / attempted,
        "single_estimate_mean_m": mean_or_none(single_estimates),
        "single_estimate_ci95_low_m": single_ci[0],
        "single_estimate_ci95_high_m": single_ci[1],
        "single_bias_mean_m": mean_or_none(single_errors),
        "single_rmse_m": rmse_or_none(single_errors),
        "six_minus_single_rmse_m": (
            None
            if six_errors.size == 0 or single_errors.size == 0
            else rmse_or_none(six_errors) - rmse_or_none(single_errors)  # type: ignore[operator]
        ),
        "mean_six_pair_residual_rmse_rad": mean_or_none(values("six_pair_residual_rmse_rad")),
        "mean_closure_residual_rad": mean_or_none(values("closure_residual_mean_abs_rad")),
        "mean_closure_p95_rad": mean_or_none(values("closure_residual_p95_rad")),
        "mean_cross_pair_consistency_m": mean_or_none(values("cross_pair_consistency_m")),
    }


def _layout(template: Mapping[str, object]) -> dict[str, object]:
    waveform = template.get("waveform")
    if not isinstance(waveform, Mapping):
        raise ValueError("waveform config is missing")
    return {
        "pulse_len": int(waveform["pulse_len"]),
        "channel_count": int(waveform["new_protocol_channel_count"]),
        "iq_data_type": str(waveform["iq_data_type"]),
        "fc_hz": float(waveform["fc_ghz"]) * 1.0e9,
        "pulses_per_beam": int(waveform["pulse_num"]),
    }


def _estimator_range_window(
    template: Mapping[str, object],
    half_width_samples: int,
    range_block_size: int = 32,
) -> tuple[int, int]:
    """Derive the calibration window from the configured scene geometry.

    The window is an explicit runner control; its centre comes from the
    fixture's calibration range and waveform metadata rather than from a
    sample-number literal tied to one prior run.
    """

    if half_width_samples <= 0:
        raise ValueError("estimator_range_half_width_samples must be positive")
    if range_block_size <= 0:
        raise ValueError("range_block_size must be positive")
    waveform = template.get("waveform")
    scene = template.get("scene")
    if not isinstance(waveform, Mapping) or not isinstance(scene, Mapping):
        raise ValueError("template must contain waveform and scene objects")
    area = scene.get("area_clutter", {})
    if not isinstance(area, Mapping):
        raise ValueError("scene.area_clutter must be an object")
    calibration_range_m = _finite_float(area.get("calibration_range_m"))
    if calibration_range_m is None or calibration_range_m <= 0.0:
        raise ValueError("scene.area_clutter.calibration_range_m must be positive")
    fs_hz = _finite_float(waveform.get("fs_hz"))
    if fs_hz is None:
        fs_mhz = _finite_float(waveform.get("fs_mhz"))
        fs_hz = None if fs_mhz is None else fs_mhz * 1.0e6
    if fs_hz is None or fs_hz <= 0.0:
        raise ValueError("waveform.fs_hz or waveform.fs_mhz must be positive")
    sample_delay_sec = _finite_float(waveform.get("sample_delay_sec"))
    if sample_delay_sec is None:
        range_processing = template.get("range_processing", {})
        if isinstance(range_processing, Mapping):
            delay_us = _finite_float(range_processing.get("sample_delay_us"))
            sample_delay_sec = None if delay_us is None else delay_us * 1.0e-6
    if sample_delay_sec is None:
        sample_delay_sec = 0.0
    center = int(round((2.0 * calibration_range_m / OBS.C - sample_delay_sec) * fs_hz))
    pulse_len = int(waveform.get("pulse_len", 0))
    if pulse_len <= 0:
        raise ValueError("waveform.pulse_len must be positive")
    start_unaligned = max(0, center - int(half_width_samples))
    end_unaligned = min(pulse_len, center + int(half_width_samples) + 1)
    # The estimator consumes complete range blocks.  Aligning the derived
    # bounds prevents a window-origin change from moving a strong echo across
    # two blocks and changing the observed circular phase statistic.
    start = (start_unaligned // range_block_size) * range_block_size
    end = min(
        pulse_len,
        ((end_unaligned + range_block_size - 1) // range_block_size)
        * range_block_size,
    )
    if end <= start:
        raise ValueError(
            f"derived estimator range window is empty: center={center}, pulse_len={pulse_len}"
        )
    return start, end


def _angles(template: Mapping[str, object]) -> list[float]:
    scan = template.get("scan")
    if not isinstance(scan, Mapping):
        raise ValueError("scan config is missing")
    start = float(scan["scan_min_deg"])
    step = float(scan["scan_step_deg"])
    count = int(scan["beam_count"])
    return [start + step * index for index in range(count)]


def run_matrix(
    output_root: Path,
    template_path: Path = TEMPLATE_PATH,
    simulator: Path = SIMULATOR,
    levels_m: Sequence[float] = DEFAULT_LEVELS_M,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    min_coherence: float = 0.6,
    range_block_size: int = 32,
    bootstrap_resamples: int = 4000,
    estimator_range_half_width_samples: int = 1024,
    nuisance: Mapping[str, object] | None = None,
    required_angles: Sequence[float] = REQUIRED_ANGLES_DEG,
    source_commit_override: Optional[str] = None,
    worktree_dirty_override: Optional[bool] = None,
) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty matrix output: {output_root}")
    if not simulator.is_file():
        raise RuntimeError(f"missing simulator: {simulator}")
    if not (0.0 <= min_coherence <= 1.0):
        raise ValueError("min_coherence must be in [0,1]")
    if range_block_size <= 0:
        raise ValueError("range_block_size must be positive")
    output_root.mkdir(parents=True, exist_ok=True)
    with template_path.open("r", encoding="utf-8") as stream:
        template = json.load(stream)
    effective_template = apply_nuisance_config(template, nuisance)
    layout = _layout(effective_template)
    range_start, range_end = _estimator_range_window(
        effective_template, estimator_range_half_width_samples, range_block_size
    )
    positions = OBS.load_channel_positions(template_path, source="reported")
    angles = _angles(effective_template)
    missing_angles = [angle for angle in required_angles
                      if not any(abs(angle - actual) <= 1.0e-9 for actual in angles)]
    if missing_angles:
        raise ValueError(f"template is missing required beam angles: {missing_angles}")
    source_status = _git("status", "--short", "--untracked-files=all")
    source_commit = source_commit_override or _git("rev-parse", "HEAD")
    source_dirty = (
        bool(source_status)
        if worktree_dirty_override is None
        else bool(worktree_dirty_override)
    )
    rows: list[dict[str, object]] = []
    commands: list[list[str]] = []
    config_records: list[dict[str, object]] = []
    config_dir = output_root / "configs"
    log_dir = output_root / "logs"
    estimator_dir = output_root / "estimators"
    temp_parent = Path(tempfile.mkdtemp(prefix="unknown_geometry_matrix_", dir="/tmp"))
    try:
        total = len(tuple(levels_m)) * len(tuple(seeds))
        case_index = 0
        for delta_m in levels_m:
            for seed in seeds:
                case_index += 1
                delta_m = float(delta_m)
                seed = int(seed)
                case_id = f"baseline_{_delta_tag(delta_m)}_seed_{seed}"
                case_dir = temp_parent / case_id
                config_path = config_dir / f"{case_id}.json"
                config = _make_case_config(
                    effective_template, delta_m, seed, case_id, case_dir
                )
                _write_json(config_path, config)
                command = [str(simulator), "--config", str(config_path)]
                commands.append(command)
                config_records.append({
                    "case_id": case_id,
                    "truth_delta_m": delta_m,
                    "seed": seed,
                    "config": str(config_path.resolve()),
                    "config_sha256": _sha256(config_path),
                })
                print(f"[matrix] {case_index}/{total} {case_id} start", flush=True)
                rc, runtime_sec = _run_stage2(
                    simulator, config_path, log_dir / f"{case_id}.log"
                )
                base_row: dict[str, object] = {
                    "case_id": case_id,
                    "seed": seed,
                    "truth_delta_m": delta_m,
                    "stage2_exit_code": rc,
                    "stage2_runtime_sec": runtime_sec,
                    "raw_retained": False,
                }
                if rc != 0:
                    base_row.update({
                        "case_status": "stage2_failed",
                        "six_fit_status": None,
                        "single_fit_status": None,
                        "six_failure": True,
                        "single_failure": True,
                    })
                    rows.append(base_row)
                    print(f"[matrix] {case_index}/{total} {case_id} stage2_failed", flush=True)
                    continue
                try:
                    raw_path = _find_period_file(case_dir)
                    resolved_path = case_dir / "scenario_resolved.json"
                    if not resolved_path.is_file():
                        raise RuntimeError(f"resolved scenario is missing: {resolved_path}")
                    with resolved_path.open("r", encoding="utf-8") as stream:
                        resolved = json.load(stream)
                    allowed_metadata = estimator_metadata(resolved)
                    # This is the only call boundary used for estimation.  The
                    # evaluator's delta_m and true geometry never enter it.
                    six_result = OBS.estimate_unknown_baseline(
                        raw_path,
                        pulse_len=int(layout["pulse_len"]),
                        channel_count=int(layout["channel_count"]),
                        iq_data_type=str(layout["iq_data_type"]),
                        fc_hz=float(layout["fc_hz"]),
                        nominal_channel_positions_m=positions,
                        metadata=allowed_metadata,
                        pulses_per_beam=int(layout["pulses_per_beam"]),
                        pair_mode="six_pair",
                        range_block_size=range_block_size,
                        range_start=range_start,
                        range_end=range_end,
                        max_abs_delta_m=0.02,
                        min_coherence=min_coherence,
                    )
                    single_result = OBS.estimate_unknown_baseline(
                        raw_path,
                        pulse_len=int(layout["pulse_len"]),
                        channel_count=int(layout["channel_count"]),
                        iq_data_type=str(layout["iq_data_type"]),
                        fc_hz=float(layout["fc_hz"]),
                        nominal_channel_positions_m=positions,
                        metadata=allowed_metadata,
                        pulses_per_beam=int(layout["pulses_per_beam"]),
                        pair_mode="single_pair",
                        single_pair="C12",
                        range_block_size=range_block_size,
                        range_start=range_start,
                        range_end=range_end,
                        max_abs_delta_m=0.02,
                        min_coherence=min_coherence,
                    )
                    compact_six = copy.deepcopy(six_result)
                    compact_single = copy.deepcopy(single_result)
                    for result in (compact_six, compact_single):
                        result.pop("input_path", None)
                    _write_json(estimator_dir / f"{case_id}.six_pair.json", compact_six)
                    _write_json(estimator_dir / f"{case_id}.single_pair.json", compact_single)
                    row = summarize_case(delta_m, six_result, single_result)
                    row.update(base_row)
                    row.update({
                        "case_status": "completed",
                        "raw_size_bytes": raw_path.stat().st_size,
                        "raw_sha256": _sha256(raw_path),
                        "estimator_metadata_keys": sorted(allowed_metadata),
                    })
                    rows.append(row)
                    estimate = row.get("six_estimate_m")
                    print(f"[matrix] {case_index}/{total} {case_id} six={estimate}", flush=True)
                except Exception as exc:  # retain a machine-readable failure row
                    base_row.update({
                        "case_status": "estimator_failed",
                        "estimator_error": f"{type(exc).__name__}: {exc}",
                        "six_fit_status": None,
                        "single_fit_status": None,
                        "six_failure": True,
                        "single_failure": True,
                    })
                    rows.append(base_row)
                    print(f"[matrix] {case_index}/{total} {case_id} estimator_failed", flush=True)
    finally:
        # temp_parent contains only raw Stage2 output created by this runner.
        # TemporaryDirectory-style cleanup is intentional; compact hashes and
        # estimator JSONs above are the retained evidence.
        import shutil
        shutil.rmtree(temp_parent)

    aggregate_rows: list[dict[str, object]] = []
    for level_index, delta_m in enumerate(levels_m):
        selected = [row for row in rows if abs(float(row["truth_delta_m"]) - float(delta_m)) <= 1.0e-12]
        aggregate_rows.append(
            _aggregate_level(
                float(delta_m), selected, level_index, bootstrap_resamples
            )
        )
    _write_rows(output_root / "matrix_rows.csv", rows)
    _write_rows(output_root / "matrix_summary.csv", aggregate_rows)
    _write_rows(
        output_root / "single_pair_vs_six_pair.csv",
        [
            {
                "case_id": row.get("case_id"),
                "seed": row.get("seed"),
                "truth_delta_m": row.get("truth_delta_m"),
                "six_fit_status": row.get("six_fit_status"),
                "six_estimate_m": row.get("six_estimate_m"),
                "six_abs_error_m": row.get("six_abs_error_m"),
                "single_fit_status": row.get("single_fit_status"),
                "single_estimate_m": row.get("single_estimate_m"),
                "single_abs_error_m": row.get("single_abs_error_m"),
                "six_minus_single_error_m": (
                    None
                    if _finite_float(row.get("six_abs_error_m")) is None
                    or _finite_float(row.get("single_abs_error_m")) is None
                    else float(row["six_abs_error_m"]) - float(row["single_abs_error_m"])
                ),
            }
            for row in rows
        ],
    )
    completed = [row for row in rows if row.get("case_status") == "completed"]
    manifest: dict[str, object] = {
        "schema": "unknown_system_error_geometry_matrix_v1",
        "status": "completed" if len(completed) == len(rows) else "completed_with_failures",
        "ai_training": False,
        "source": {
            "source_commit_before_run": source_commit,
            "worktree_dirty_before": source_dirty,
            "worktree_status_before": source_status,
            "template": str(template_path.resolve()),
            "template_sha256": _sha256(template_path),
            "simulator": str(simulator.resolve()),
        },
        "execution": {
            "working_directory": str(ROOT),
            "python": sys.version,
            "platform": platform.platform(),
            "gpu_probe": "nvidia-smi was checked separately; this matrix is Stage2 CPU raw-IQ generation plus Python estimation",
            "commands": commands,
        },
        "matrix": {
            "baseline_levels_m": [float(value) for value in levels_m],
            "seeds": [int(value) for value in seeds],
            "angles_deg": angles,
            "required_angles_deg": [float(value) for value in required_angles],
            "min_coherence": min_coherence,
            "range_block_size": range_block_size,
            "estimator_range_start": range_start,
            "estimator_range_end": range_end,
            "estimator_range_half_width_samples": estimator_range_half_width_samples,
            "bootstrap_resamples": bootstrap_resamples,
            "nuisance": {
                "fixed_channel_phase_mismatch_deg": float(
                    effective_template["channel_impairments"]["channel_fixed_phase_mismatch_deg"]  # type: ignore[index]
                ),
                "channel_amp_mismatch_db": float(
                    effective_template["channel_impairments"]["channel_amp_mismatch_db"]  # type: ignore[index]
                ),
                "noise_power": float(
                    effective_template["scene"]["thermal_noise"]["noise_power"]  # type: ignore[index]
                ),
                "texture_sigma": float(
                    effective_template["scene"]["area_clutter"]["texture_sigma"]  # type: ignore[index]
                ),
                "overrides": copy.deepcopy(dict(nuisance or {})),
                "thermal_noise": True,
                "clutter_texture_variation": True,
                "phase_jitter_and_drift": 0.0,
            },
        },
        "estimator_input_contract": {
            "unknown_input": "one unknown_off four-channel IQ file per case",
            "allowed_nominal_inputs": ["reported channel positions", "waveform", "range processing", "platform", "simulation geometry", "ground height"],
            "forbidden_inputs": ["ideal_off", "ideal_on", "truth", "known-error parameters", "future metrics", "true channel positions"],
            "pair_modes": ["single_pair:C12", "six_pair:C13,C24,C12,C14,C23,C34"],
        },
        "layout": layout,
        "config_records": config_records,
        "case_count": len(rows),
        "completed_case_count": len(completed),
        "failure_case_count": len(rows) - len(completed),
        "raw_cleanup": {
            "raw_retained": False,
            "temporary_raw_deleted_after_hashing": True,
            "retained_artifacts": "configs, logs, compact estimator JSON, matrix CSVs, manifest",
        },
        "summary_csv": str((output_root / "matrix_summary.csv").resolve()),
        "interpretation": {
            "known_error_correction": "not run in Phase 3; no known/truth correction is fed to the estimator",
            "ci_definition": "deterministic percentile bootstrap 95% CI of the per-seed estimate mean",
            "failure_definition": "fit_status != fit; fallback is reported separately",
        },
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path,
                        default=ROOT / "outputs/unknown_system_error_geometry_matrix_20260914")
    parser.add_argument("--template", type=Path, default=TEMPLATE_PATH)
    parser.add_argument("--simulator", type=Path, default=SIMULATOR)
    parser.add_argument("--levels-m", type=float, nargs="+", default=list(DEFAULT_LEVELS_M))
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    parser.add_argument("--min-coherence", type=float, default=0.6)
    parser.add_argument("--range-block-size", type=int, default=32)
    parser.add_argument("--bootstrap-resamples", type=int, default=4000)
    parser.add_argument("--estimator-range-half-width-samples", type=int, default=1024)
    args = parser.parse_args(argv)
    manifest = run_matrix(
        args.output_root.resolve(),
        template_path=args.template.resolve(),
        simulator=args.simulator.resolve(),
        levels_m=tuple(args.levels_m),
        seeds=tuple(args.seeds),
        min_coherence=args.min_coherence,
        range_block_size=args.range_block_size,
        bootstrap_resamples=args.bootstrap_resamples,
        estimator_range_half_width_samples=args.estimator_range_half_width_samples,
    )
    print(json.dumps({
        "status": manifest["status"],
        "output_root": str(args.output_root.resolve()),
        "case_count": manifest["case_count"],
        "completed_case_count": manifest["completed_case_count"],
        "failure_case_count": manifest["failure_case_count"],
    }, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
