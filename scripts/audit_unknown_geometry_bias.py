#!/usr/bin/env python3
"""Audit the zero-error geometry bias with an exact evaluation predictor.

The estimator remains blind and is called only with reported geometry.  This
audit adds a separate, evaluator-only calculation that reconstructs the
beam-centre clutter cell and evaluates the simulator's common-TX,
channel-specific receive path.  It retains per-block observed, nominal, and
exact phases so a fitted ~0.16 mm offset can be attributed to model mismatch
instead of being removed empirically.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "configs/research/unknown_system_error_true_geometry_pilot.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
ANALYZER_PATH = ROOT / "scripts/analyze_four_channel_observables.py"
MATRIX_PATH = ROOT / "scripts/run_unknown_system_error_matrix.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OBS = _load_module(ANALYZER_PATH, "four_channel_observables_bias_audit")
MATRIX = _load_module(MATRIX_PATH, "unknown_geometry_matrix_bias_audit")


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
    seen: set[str] = set()
    for row in materialized:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(materialized)


def _finite(value: object) -> float:
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number):
        raise ValueError(f"non-finite value: {value}")
    return number


def _reference_platform(resolved: Mapping[str, object]) -> np.ndarray:
    waveform = resolved["waveform"]
    scan = resolved["scan"]
    platform = resolved["platform"]
    if not isinstance(waveform, Mapping) or not isinstance(scan, Mapping) or not isinstance(platform, Mapping):
        raise ValueError("resolved config has invalid waveform/scan/platform")
    prf_hz = _finite(waveform["prf_hz"])
    pulse_num = int(waveform["pulse_num"])
    beam_count = int(scan["beam_count"])
    ref_time = (max(0, beam_count // 2) * pulse_num + max(0, pulse_num // 2)) / prf_hz
    return np.array([
        _finite(platform["speed_mps"]) * ref_time,
        0.0,
        _finite(platform["height_m"]),
    ], dtype=np.float64)


def _packet_platform(resolved: Mapping[str, object], packet_index: int) -> np.ndarray:
    waveform = resolved["waveform"]
    platform = resolved["platform"]
    if not isinstance(waveform, Mapping) or not isinstance(platform, Mapping):
        raise ValueError("resolved config has invalid waveform/platform")
    return np.array([
        _finite(platform["speed_mps"]) * (float(packet_index) / _finite(waveform["prf_hz"])),
        0.0,
        _finite(platform["height_m"]),
    ], dtype=np.float64)


def _beam_centre_target(resolved: Mapping[str, object], theta_deg: float) -> np.ndarray:
    reference = _reference_platform(resolved)
    geometry = resolved.get("simulation_geometry", {})
    scene = resolved.get("scene", {})
    platform = resolved.get("platform", {})
    waveform = resolved.get("waveform", {})
    if not isinstance(geometry, Mapping) or not isinstance(scene, Mapping) or not isinstance(platform, Mapping) or not isinstance(waveform, Mapping):
        raise ValueError("resolved config has invalid geometry/scene/platform/waveform")
    area = scene.get("area_clutter", {})
    if not isinstance(area, Mapping):
        raise ValueError("scene.area_clutter is not an object")
    slant = _finite(area["calibration_range_m"])
    ground_z = _finite(scene.get("ground_z_m", 0.0))
    height = _finite(platform["height_m"])
    range_geometry = str(geometry.get("range_geometry", "algorithm"))
    use_ground = bool(geometry.get("use_ground_range_for_position", True))
    ground_range = slant
    if use_ground and range_geometry != "slant":
        ground_range = math.sqrt(max(0.0, slant * slant - (height - ground_z) ** 2))
    horizontal = OBS._local_look_vector(theta_deg, resolved)
    return np.array([
        reference[0] + float(horizontal[0]) * ground_range,
        reference[1] + float(horizontal[1]) * ground_range,
        ground_z,
    ], dtype=np.float64)


def _projection_rad_per_m(
    theta_deg: float,
    range_sample: float,
    metadata: Mapping[str, object],
    positions: np.ndarray,
    i: int,
    j: int,
    fc_hz: float,
) -> float:
    los = OBS._nominal_los_unit(theta_deg, range_sample, metadata)
    if not np.all(np.isfinite(los)):
        raise ValueError("nominal LOS is not finite")
    right = np.array([0.0, 1.0, 0.0, 1.0], dtype=np.float64)
    sign = _finite(metadata.get("carrier_phase_sign", -1.0))
    return sign * 2.0 * math.pi / (OBS.C / fc_hz) * float(
        los[0] * (right[j] - right[i])
    )


def _run_stage2(simulator: Path, config_path: Path, log_path: Path) -> tuple[int, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [str(simulator), "--config", str(config_path)]
    start = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as stream:
        stream.write("command=" + " ".join(command) + "\n")
        stream.flush()
        completed = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=False)
        stream.write(f"exit_code={completed.returncode}\n")
    return completed.returncode, time.perf_counter() - start


def _fit_value(fit: Mapping[str, object], key: str) -> Optional[float]:
    value = fit.get(key)
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _fit_summary(observations: Sequence[Mapping[str, float]], pair_name: str, max_abs_delta_m: float) -> dict[str, object]:
    fit = OBS._circular_pair_fit(observations, max_abs_delta_m, pair_name)
    return {
        "fit_status": fit.get("fit_status"),
        "estimate_m": _fit_value(fit, "estimated_delta_d_m"),
        "intercept_rad": _fit_value(fit, "intercept_rad"),
        "rmse_rad": _fit_value(fit, "rmse_rad"),
        "resultant_length": _fit_value(fit, "resultant_length"),
        "observation_count": fit.get("observation_count"),
        "inlier_count": fit.get("inlier_count"),
    }


def _audit_case(
    raw_path: Path,
    resolved: Mapping[str, object],
    reported_positions: np.ndarray,
    true_positions: np.ndarray,
    fc_hz: float,
    pulse_len: int,
    channel_count: int,
    iq_type: str,
    pulses_per_beam: int,
    min_coherence: float,
    range_start: int,
    range_end: int,
    range_block_size: int,
    max_abs_delta_m: float,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    metadata = MATRIX.estimator_metadata(resolved)
    pair_rows: list[dict[str, object]] = []
    fit_rows: dict[str, dict[str, list[dict[str, float]]]] = {}
    all_pairs = [(name, i, j) for name, i, j in OBS.PAIR_DEFINITIONS]
    scan = resolved["scan"]
    waveform = resolved["waveform"]
    if not isinstance(scan, Mapping) or not isinstance(waveform, Mapping):
        raise ValueError("resolved config has invalid scan/waveform")
    beam_count = int(scan["beam_count"])
    for packet in OBS.iter_raw_packets(raw_path, pulse_len, channel_count, iq_type):
        packet_index = int(packet["packet_index"])
        beam_index = packet_index // max(1, pulses_per_beam)
        pulse_index = packet_index % max(1, pulses_per_beam)
        if beam_index >= beam_count:
            continue
        theta = _finite(packet["theta_cmd_deg"])
        target = _beam_centre_target(resolved, theta)
        platform = _packet_platform(resolved, packet_index)
        channels = packet["channels"]
        for start in range(max(0, range_start), min(channels.shape[0], range_end), range_block_size):
            end = min(channels.shape[0], start + range_block_size)
            range_sample = 0.5 * (start + end - 1)
            for pair_name, i, j in all_pairs:
                observed = OBS._block_pair_observable(channels, start, end, i, j)
                if observed is None or float(observed["coherence"]) < min_coherence:
                    continue
                nominal = OBS.nominal_pair_phase_rad(
                    theta, range_sample, metadata, reported_positions, i, j, fc_hz
                )
                exact_reported = OBS.exact_pair_phase_rad(
                    target, platform, reported_positions, i, j, fc_hz,
                    carrier_phase_sign=_finite(metadata.get("carrier_phase_sign", -1.0)),
                )
                exact_true = OBS.exact_pair_phase_rad(
                    target, platform, true_positions, i, j, fc_hz,
                    carrier_phase_sign=_finite(metadata.get("carrier_phase_sign", -1.0)),
                )
                projection = _projection_rad_per_m(
                    theta, range_sample, metadata, reported_positions, i, j, fc_hz
                )
                observed_phase = _finite(observed["phase_rad"])
                row: dict[str, object] = {
                    "packet_index": packet_index,
                    "beam_index": beam_index,
                    "pulse_index": pulse_index,
                    "theta_cmd_deg": theta,
                    "block_start": start,
                    "block_end": end,
                    "range_sample": range_sample,
                    "pair": pair_name,
                    "coherence": _finite(observed["coherence"]),
                    "observed_phase_rad": observed_phase,
                    "nominal_phase_rad": nominal,
                    "exact_reported_phase_rad": exact_reported,
                    "exact_true_phase_rad": exact_true,
                    "observed_minus_nominal_rad": OBS._wrap_phase(observed_phase - nominal),
                    "exact_reported_minus_nominal_rad": OBS._wrap_phase(exact_reported - nominal),
                    "exact_true_minus_nominal_rad": OBS._wrap_phase(exact_true - nominal),
                    "exact_true_minus_reported_rad": OBS._wrap_phase(exact_true - exact_reported),
                    "projection_rad_per_m": projection,
                    "weight": _finite(observed["weight"]),
                }
                pair_rows.append(row)
                fit_rows.setdefault(pair_name, {"observed": [], "exact_reported": [], "exact_true": []})
                for label, residual in (
                    ("observed", row["observed_minus_nominal_rad"]),
                    ("exact_reported", row["exact_reported_minus_nominal_rad"]),
                    ("exact_true", row["exact_true_minus_nominal_rad"]),
                ):
                    fit_rows[pair_name][label].append({
                        "residual_rad": _finite(residual),
                        "projection_rad_per_m": projection,
                        "weight": _finite(observed["weight"]),
                    })
    fit_summary: dict[str, object] = {}
    for pair_name in (item[0] for item in OBS.PAIR_DEFINITIONS):
        groups = fit_rows.get(pair_name, {})
        fit_summary[pair_name] = {
            label: _fit_summary(rows, pair_name, max_abs_delta_m)
            for label, rows in groups.items()
        }
    estimator = OBS.estimate_unknown_baseline(
        raw_path,
        pulse_len=pulse_len,
        channel_count=channel_count,
        iq_data_type=iq_type,
        fc_hz=fc_hz,
        nominal_channel_positions_m=reported_positions,
        metadata=metadata,
        pulses_per_beam=pulses_per_beam,
        pair_mode="six_pair",
        range_block_size=range_block_size,
        range_start=range_start,
        range_end=range_end,
        max_abs_delta_m=max_abs_delta_m,
        min_coherence=min_coherence,
    )
    return pair_rows, {"exact_and_observed_fits": fit_summary, "blind_estimator": estimator}


def run_audit(
    output_root: Path,
    template_path: Path = TEMPLATE_PATH,
    simulator: Path = SIMULATOR,
    levels_m: Sequence[float] = (0.0, 0.0025),
    seeds: Sequence[int] = (101, 202, 303),
    min_coherence: float = 0.6,
    range_block_size: int = 32,
    range_half_width_samples: int = 1024,
    max_abs_delta_m: float = 0.02,
) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty audit output: {output_root}")
    if not simulator.is_file():
        raise RuntimeError(f"missing simulator: {simulator}")
    output_root.mkdir(parents=True, exist_ok=True)
    with template_path.open("r", encoding="utf-8") as stream:
        template = json.load(stream)
    layout = MATRIX._layout(template)
    range_start, range_end = MATRIX._estimator_range_window(
        template, range_half_width_samples, range_block_size
    )
    reported_positions = OBS.load_channel_positions(template_path, source="reported")
    source_commit = MATRIX._git("rev-parse", "HEAD")
    tracked_status = MATRIX._git("status", "--short", "--untracked-files=no")
    config_dir = output_root / "configs"
    log_dir = output_root / "logs"
    temp_parent = Path(tempfile.mkdtemp(prefix="unknown_geometry_bias_", dir="/tmp"))
    rows: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    commands: list[list[str]] = []
    case_records: list[dict[str, object]] = []
    try:
        cases = [(float(level), int(seed)) for level in levels_m for seed in seeds]
        for case_index, (delta_m, seed) in enumerate(cases, start=1):
            case_id = f"bias_{MATRIX._delta_tag(delta_m)}_seed_{seed}"
            case_dir = temp_parent / case_id
            config_path = config_dir / f"{case_id}.json"
            config = MATRIX._make_case_config(template, delta_m, seed, case_id, case_dir)
            MATRIX._write_json(config_path, config)
            command = [str(simulator), "--config", str(config_path)]
            commands.append(command)
            print(f"[bias] {case_index}/{len(cases)} {case_id} start", flush=True)
            rc, runtime_sec = _run_stage2(simulator, config_path, log_dir / f"{case_id}.log")
            if rc != 0:
                raise RuntimeError(f"Stage2 failed for {case_id}; see {log_dir / (case_id + '.log')}")
            raw_path = MATRIX._find_period_file(case_dir)
            resolved_path = case_dir / "scenario_resolved.json"
            with resolved_path.open("r", encoding="utf-8") as stream:
                resolved = json.load(stream)
            reported = OBS.load_channel_positions(resolved_path, source="reported")
            true = OBS.load_channel_positions(resolved_path, source="true")
            case_rows, case_summary = _audit_case(
                raw_path,
                resolved,
                reported,
                true,
                float(layout["fc_hz"]),
                int(layout["pulse_len"]),
                int(layout["channel_count"]),
                str(layout["iq_data_type"]),
                int(layout["pulses_per_beam"]),
                min_coherence,
                range_start,
                range_end,
                range_block_size,
                max_abs_delta_m,
            )
            for row in case_rows:
                row.update({"case_id": case_id, "truth_delta_m": delta_m, "seed": seed})
            rows.extend(case_rows)
            exact_fits = case_summary["exact_and_observed_fits"]
            for pair_name, values in exact_fits.items():
                for model_name, fit in values.items():
                    row = {
                        "case_id": case_id,
                        "truth_delta_m": delta_m,
                        "seed": seed,
                        "pair": pair_name,
                        "model": model_name,
                    }
                    row.update({
                        key: value for key, value in fit.items()
                        if key != "fit_status"
                    })
                    row["fit_status"] = fit.get("fit_status")
                    summaries.append(row)
            estimator = case_summary["blind_estimator"]
            if isinstance(estimator, Mapping):
                summaries.append({
                    "case_id": case_id,
                    "truth_delta_m": delta_m,
                    "seed": seed,
                    "pair": "six_pair_global",
                    "model": "blind_estimator",
                    "fit_status": estimator.get("fit_status"),
                    "estimate_m": estimator.get("estimated_baseline_error_m"),
                    "rmse_rad": (estimator.get("global_fit", {}) or {}).get("rmse_rad") if isinstance(estimator.get("global_fit", {}), Mapping) else None,
                    "resultant_length": (estimator.get("global_fit", {}) or {}).get("resultant_length") if isinstance(estimator.get("global_fit", {}), Mapping) else None,
                    "observation_count": estimator.get("packet_count"),
                })
            case_records.append({
                "case_id": case_id,
                "truth_delta_m": delta_m,
                "seed": seed,
                "stage2_runtime_sec": runtime_sec,
                "config": str(config_path.resolve()),
                "config_sha256": _sha256(config_path),
                "raw_size_bytes": raw_path.stat().st_size,
                "raw_sha256": _sha256(raw_path),
            })
            print(f"[bias] {case_index}/{len(cases)} {case_id} rows={len(case_rows)}", flush=True)
    finally:
        shutil.rmtree(temp_parent)
    _write_rows(output_root / "pair_phase_rows.csv", rows)
    _write_rows(output_root / "fit_summary.csv", summaries)
    manifest: dict[str, object] = {
        "schema": "unknown_system_error_geometry_bias_audit_v1",
        "status": "completed",
        "ai_training": False,
        "source": {
            "source_commit_before_run": source_commit,
            "worktree_dirty_before": bool(tracked_status),
            "worktree_status_before_tracked_only": tracked_status,
            "template": str(template_path.resolve()),
            "template_sha256": _sha256(template_path),
            "simulator": str(simulator.resolve()),
        },
        "execution": {
            "working_directory": str(ROOT),
            "python": sys.version,
            "platform": platform.platform(),
            "commands": commands,
            "raw_cleanup": "temporary raw inputs were hashed and removed after audit",
        },
        "model_contract": {
            "blind_estimator": "reported geometry + nominal LOS only; no true positions, target truth, or ideal reference",
            "evaluation_predictor": "common TX path plus channel-specific RX path, evaluated outside the estimator",
            "exact_path": "|target - TX| + |target - (platform + RX offset)|",
            "phase": "carrier_phase_sign*2*pi*(path_i-path_j)/wavelength",
            "target_reconstruction": "beam_center_clutter cell at calibration range and mid-aperture reference platform",
            "observed_phase": "block sum channel_i * conj(channel_j)",
        },
        "matrix": {
            "levels_m": [float(value) for value in levels_m],
            "seeds": [int(value) for value in seeds],
            "min_coherence": min_coherence,
            "range_block_size": range_block_size,
            "range_start": range_start,
            "range_end": range_end,
            "max_abs_delta_m": max_abs_delta_m,
            "pair_definitions": [list(item) for item in OBS.PAIR_DEFINITIONS],
        },
        "cases": case_records,
        "interpretation": {
            "zero_level_bias": "exact_reported and exact_true fits at truth_delta_m=0 quantify nominal-model curvature/reference-time bias; no empirical subtraction is applied",
            "nuisance": "fixed phase and amplitude mismatch are retained and represented by pair intercepts",
            "limitation": "the predictor uses the known beam-centre clutter construction only for evaluator attribution",
        },
        "artifacts": {
            "pair_phase_rows": str((output_root / "pair_phase_rows.csv").resolve()),
            "fit_summary": str((output_root / "fit_summary.csv").resolve()),
        },
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/unknown_system_error_geometry_bias_audit_20260914_v1")
    parser.add_argument("--template", type=Path, default=TEMPLATE_PATH)
    parser.add_argument("--simulator", type=Path, default=SIMULATOR)
    parser.add_argument("--levels-m", type=float, nargs="+", default=[0.0, 0.0025])
    parser.add_argument("--seeds", type=int, nargs="+", default=[101, 202, 303])
    parser.add_argument("--min-coherence", type=float, default=0.6)
    parser.add_argument("--range-block-size", type=int, default=32)
    parser.add_argument("--range-half-width-samples", type=int, default=1024)
    parser.add_argument("--max-abs-delta-m", type=float, default=0.02)
    args = parser.parse_args(argv)
    manifest = run_audit(
        args.output_root.resolve(),
        template_path=args.template.resolve(),
        simulator=args.simulator.resolve(),
        levels_m=tuple(args.levels_m),
        seeds=tuple(args.seeds),
        min_coherence=args.min_coherence,
        range_block_size=args.range_block_size,
        range_half_width_samples=args.range_half_width_samples,
        max_abs_delta_m=args.max_abs_delta_m,
    )
    print(json.dumps({
        "status": manifest["status"],
        "output_root": str(args.output_root.resolve()),
        "case_count": len(manifest["cases"]),
        "row_path": manifest["artifacts"]["pair_phase_rows"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
