#!/usr/bin/env python3
"""Run a compact one-factor-at-a-time nuisance sweep for geometry estimation.

The existing geometry formal matrix freezes nuisance terms to isolate the
baseline-position parameter.  This companion audit varies one nuisance at a
time, keeps the true geometry levels and seeds fixed, and reuses the same
unknown-only six-pair/single-pair estimator.  Stage2 raw files are temporary;
the nested matrix manifests retain the commands, config hashes, estimates,
and failure/fallback accounting.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import platform
import sys
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "scripts/run_unknown_system_error_matrix.py"
TEMPLATE_PATH = ROOT / "configs/research/unknown_system_error_true_geometry_pilot.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
DEFAULT_LEVELS_M = (0.0, 0.0025, -0.0025)
DEFAULT_SEEDS = (101, 202, 303)
DEFAULT_BOOTSTRAP = 4000
DEFAULT_RANGE_HALF_WIDTH = 1600


def _load_matrix_module():
    spec = importlib.util.spec_from_file_location("unknown_geometry_matrix", MATRIX_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MATRIX_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MATRIX = _load_matrix_module()


PROFILE_DEFINITIONS = (
    ("fixed_phase", "fixed_channel_phase_mismatch_deg", (0.0, 8.0, 16.0)),
    ("gain_mismatch", "fixed_channel_amp_mismatch_db", (0.0, 0.35, 0.70)),
    ("noise_power", "noise_power", (0.0001, 0.001, 0.01)),
    ("texture_sigma", "texture_sigma", (0.0, 0.1, 0.3)),
    ("angle_span", "angle_span_deg", (10.0, 15.0, 20.0)),
    ("calibration_range", "calibration_range_m", (8800.0, 9000.0, 9600.0)),
)


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
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(materialized)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _value_tag(value: float, key: str) -> str:
    if key.endswith("_deg"):
        unit = "deg"
    elif key.endswith("_m"):
        unit = "m"
    else:
        unit = ""
    return (f"{value:g}{unit}").replace("-", "m").replace(".", "p")


def run_nuisance_sweep(
    output_root: Path,
    template_path: Path = TEMPLATE_PATH,
    simulator: Path = SIMULATOR,
    levels_m: Sequence[float] = DEFAULT_LEVELS_M,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    bootstrap_resamples: int = DEFAULT_BOOTSTRAP,
    estimator_range_half_width_samples: int = DEFAULT_RANGE_HALF_WIDTH,
    source_commit: Optional[str] = None,
    worktree_dirty_before: Optional[bool] = None,
) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty output: {output_root}")
    if not template_path.is_file():
        raise RuntimeError(f"missing template: {template_path}")
    if not simulator.is_file():
        raise RuntimeError(f"missing simulator: {simulator}")
    output_root.mkdir(parents=True, exist_ok=True)
    nested: list[dict[str, object]] = []
    all_rows: list[dict[str, object]] = []
    all_summaries: list[dict[str, object]] = []
    for profile_id, nuisance_key, values in PROFILE_DEFINITIONS:
        for value in values:
            nuisance = {nuisance_key: float(value)}
            profile_name = f"{profile_id}_{_value_tag(float(value), nuisance_key)}"
            profile_root = output_root / profile_name
            required_angles: Sequence[float] = () if nuisance_key == "angle_span_deg" else MATRIX.REQUIRED_ANGLES_DEG
            print(f"[nuisance] {profile_name} start", flush=True)
            manifest = MATRIX.run_matrix(
                profile_root,
                template_path=template_path,
                simulator=simulator,
                levels_m=tuple(float(item) for item in levels_m),
                seeds=tuple(int(item) for item in seeds),
                bootstrap_resamples=bootstrap_resamples,
                estimator_range_half_width_samples=estimator_range_half_width_samples,
                nuisance=nuisance,
                required_angles=required_angles,
                source_commit_override=source_commit,
                worktree_dirty_override=worktree_dirty_before,
            )
            nested.append({
                "profile_id": profile_id,
                "nuisance_key": nuisance_key,
                "nuisance_value": float(value),
                "profile_root": str(profile_root.resolve()),
                "manifest": str((profile_root / "manifest.json").resolve()),
                "status": manifest["status"],
                "case_count": manifest["case_count"],
                "completed_case_count": manifest["completed_case_count"],
            })
            for row in _read_rows(profile_root / "matrix_rows.csv"):
                row.update({
                    "profile_id": profile_id,
                    "nuisance_key": nuisance_key,
                    "nuisance_value": float(value),
                })
                all_rows.append(row)
            for row in _read_rows(profile_root / "matrix_summary.csv"):
                row.update({
                    "profile_id": profile_id,
                    "nuisance_key": nuisance_key,
                    "nuisance_value": float(value),
                })
                all_summaries.append(row)

    source_status = MATRIX._git("status", "--short", "--untracked-files=all")
    recorded_commit = source_commit or MATRIX._git("rev-parse", "HEAD")
    recorded_dirty = (
        bool(source_status)
        if worktree_dirty_before is None
        else bool(worktree_dirty_before)
    )
    completed = [item for item in nested if item["status"] == "completed"]
    manifest = {
        "schema": "unknown_system_error_geometry_nuisance_sweep_v1",
        "status": "completed" if len(completed) == len(nested) else "completed_with_failures",
        "ai_training": False,
        "one_factor_at_a_time": True,
        "source": {
            "source_commit_before_run": recorded_commit,
            "worktree_dirty_before": recorded_dirty,
            "worktree_status_before": source_status,
            "template": str(template_path.resolve()),
            "template_sha256": _sha256(template_path),
            "simulator": str(simulator.resolve()),
            "simulator_sha256": _sha256(simulator),
        },
        "execution": {
            "working_directory": str(ROOT),
            "python": sys.version,
            "platform": platform.platform(),
            "stage2_and_estimator": "CPU simulator raw-IQ generation plus unknown-only Python estimator; no production CUDA claim",
        },
        "matrix": {
            "geometry_levels_m": [float(item) for item in levels_m],
            "seeds": [int(item) for item in seeds],
            "bootstrap_resamples": bootstrap_resamples,
            "estimator_range_half_width_samples": estimator_range_half_width_samples,
            "profiles": [
                {
                    "profile_id": profile_id,
                    "nuisance_key": nuisance_key,
                    "values": [float(item) for item in values],
                }
                for profile_id, nuisance_key, values in PROFILE_DEFINITIONS
            ],
        },
        "nested_profiles": nested,
        "case_count": len(all_rows),
        "completed_profile_count": len(completed),
        "profile_count": len(nested),
        "raw_policy": "nested run_matrix outputs delete temporary raw files after hashing",
        "artifacts": {
            "rows": str((output_root / "nuisance_matrix_rows.csv").resolve()),
            "summary": str((output_root / "nuisance_matrix_summary.csv").resolve()),
            "profile_index": str((output_root / "profile_index.csv").resolve()),
        },
    }
    _write_rows(output_root / "nuisance_matrix_rows.csv", all_rows)
    _write_rows(output_root / "nuisance_matrix_summary.csv", all_summaries)
    _write_rows(output_root / "profile_index.csv", nested)
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root", type=Path,
        default=ROOT / "outputs/unknown_system_error_geometry_nuisance_sweep_20260914",
    )
    parser.add_argument("--template", type=Path, default=TEMPLATE_PATH)
    parser.add_argument("--simulator", type=Path, default=SIMULATOR)
    parser.add_argument("--levels-m", type=float, nargs="+", default=list(DEFAULT_LEVELS_M))
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    parser.add_argument("--bootstrap-resamples", type=int, default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--estimator-range-half-width-samples", type=int, default=DEFAULT_RANGE_HALF_WIDTH)
    parser.add_argument("--source-commit", default=None)
    parser.add_argument("--worktree-dirty-before", choices=("true", "false"), default=None)
    args = parser.parse_args(argv)
    dirty = None if args.worktree_dirty_before is None else args.worktree_dirty_before == "true"
    manifest = run_nuisance_sweep(
        args.output_root.resolve(),
        template_path=args.template.resolve(),
        simulator=args.simulator.resolve(),
        levels_m=tuple(args.levels_m),
        seeds=tuple(args.seeds),
        bootstrap_resamples=args.bootstrap_resamples,
        estimator_range_half_width_samples=args.estimator_range_half_width_samples,
        source_commit=args.source_commit,
        worktree_dirty_before=dirty,
    )
    print(json.dumps({
        "status": manifest["status"],
        "output_root": str(args.output_root.resolve()),
        "profile_count": manifest["profile_count"],
        "case_count": manifest["case_count"],
    }, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
