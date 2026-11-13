#!/usr/bin/env python3
"""Run a compact, reproducible GO-CFAR H0--H5 closure.

The production detector uses four directional training means and a frozen
alpha.  This closure samples the same eight-block geometry with independent
CUT/training realizations, so H0 is an empirical IID cell-Pfa check.  H1--H5
add controlled structured-clutter/ridge mismatch terms and are deliberately
reported as structured-clutter false-hit fractions, not as theoretical or
operational Pfa.

No raw maps are written.  The output is limited to CSV/JSON evidence suitable
for ``outputs/formal_evidence`` collection.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
HYPOTHESES = ("H0", "H1", "H2", "H3", "H4", "H5")
DEFAULT_ALPHA = 13.44951031977817
CONFIGURED_PFA = 1.0e-6
DEFAULT_GUARD = 4
DEFAULT_BACKGROUND = 16
DEFAULT_CUT_COUNT = 1_000_000
DEFAULT_CHUNK_SIZE = 100_000
DEFAULT_SEEDS = (101, 202, 303)


_HYPOTHESIS_CONTRACT: dict[str, dict[str, object]] = {
    "H0": {
        "name": "pure_noise_iid",
        "metric": "empirical_cell_pfa",
        "scene": "IID exponential noise with independent training blocks",
        "correction": "none",
        "structured_clutter": False,
    },
    "H1": {
        "name": "noise_stationary_clutter",
        "metric": "structured_clutter_false_hit_fraction",
        "scene": "noise plus stationary local clutter ridge",
        "correction": "none",
        "structured_clutter": True,
    },
    "H2": {
        "name": "geometry_error_clutter",
        "metric": "structured_clutter_false_hit_fraction",
        "scene": "stationary clutter with geometry-induced CUT/training ridge mismatch",
        "correction": "geometry_error_present",
        "structured_clutter": True,
    },
    "H3": {
        "name": "corrected_geometry_clutter",
        "metric": "structured_clutter_false_hit_fraction",
        "scene": "same geometry control with known geometry mismatch removed for evaluation",
        "correction": "known_geometry_correction_evaluation_only",
        "structured_clutter": True,
    },
    "H4": {
        "name": "servo_error_clutter",
        "metric": "structured_clutter_false_hit_fraction",
        "scene": "stationary clutter with servo-induced CUT/training ridge mismatch",
        "correction": "servo_error_present",
        "structured_clutter": True,
    },
    "H5": {
        "name": "corrected_servo_clutter",
        "metric": "structured_clutter_false_hit_fraction",
        "scene": "same servo control with known servo mismatch removed for evaluation",
        "correction": "known_servo_correction_evaluation_only",
        "structured_clutter": True,
    },
}


def metric_definition(hypothesis: str) -> str:
    """Return the metric name required by the H0 versus structured-clutter split."""

    try:
        return str(_HYPOTHESIS_CONTRACT[hypothesis]["metric"])
    except KeyError as exc:
        raise ValueError(f"unknown hypothesis: {hypothesis}") from exc


def configured_pfa_is_claim(metric: str) -> bool:
    """Identify forbidden wording that would turn a configured value into a claim."""

    return metric in {"configured_pfa_achieved", "production_pfa_achieved"}


def training_cell_counts(guard: int, background: int) -> dict[str, int]:
    """Return the exact block counts used by ``include/go_cfar_alpha.hpp``."""

    if guard < 0 or background < 1:
        raise ValueError("guard must be non-negative and background must be positive")
    radius = guard + background
    return {
        "corner_block_cells": background * background,
        "center_block_cells": background * (2 * guard + 1),
        "directional_strip_cells": background * (2 * radius + 1),
        "unique_training_cells": 4 * background * background
        + 4 * background * (2 * guard + 1),
    }


def summarize_cut_events(
    cut_power: np.ndarray,
    threshold: np.ndarray,
    valid: np.ndarray,
    cut_ids: np.ndarray,
) -> dict[str, int | float | bool]:
    """Count only finite, valid, unique CUT events.

    Duplicate IDs are an audit failure rather than extra denominator mass.  IDs
    that are already excluded by ``valid`` or a non-finite value do not count as
    duplicate independent CUTs because they never enter the estimate.
    """

    cut = np.asarray(cut_power)
    limit = np.asarray(threshold)
    mask = np.asarray(valid, dtype=bool)
    ids = np.asarray(cut_ids)
    if not (cut.shape == limit.shape == mask.shape == ids.shape):
        raise ValueError("cut_power, threshold, valid and cut_ids must have the same shape")
    finite = mask & np.isfinite(cut) & np.isfinite(limit) & (limit >= 0.0)
    candidate_ids = ids[finite]
    unique_count = np.unique(candidate_ids).size
    duplicate_count = int(candidate_ids.size - unique_count)
    if duplicate_count:
        raise ValueError(f"duplicate independent CUT ids: {duplicate_count}")
    hit_count = int(np.count_nonzero(cut[finite] > limit[finite]))
    valid_count = int(candidate_ids.size)
    total_count = int(cut.size)
    return {
        "hit_count": hit_count,
        "valid_cut_count": valid_count,
        "excluded_cut_count": total_count - valid_count,
        "invalid_cut_count": int(np.count_nonzero(~finite)),
        "duplicate_cut_count": duplicate_count,
        "empirical_rate": float(hit_count / valid_count) if valid_count else math.nan,
        "independent_cut_set": True,
    }


def wilson_interval(hit_count: int, trial_count: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    """Return a two-sided Wilson interval for a binomial event rate."""

    if trial_count <= 0:
        return None, None
    n = float(trial_count)
    p = float(hit_count) / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denominator
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def _scenario_parameters(hypothesis: str) -> dict[str, float]:
    if hypothesis not in HYPOTHESES:
        raise ValueError(f"unknown hypothesis: {hypothesis}")
    if hypothesis == "H0":
        return {
            "noise_power": 1.0,
            "clutter_power": 0.0,
            "texture_sigma": 0.0,
            "ridge_width": 0.2,
            "ridge_slope": 0.25,
            "mismatch": 0.0,
        }
    # These are fixed before a run and are not fit to its hit counts.  The
    # mismatch is a local-coordinate displacement between the CUT and the
    # directional training support; H3/H5 remove only the named known term.
    mismatch = {
        "H1": 0.0,
        "H2": 0.35,
        "H3": 0.0,
        "H4": -0.35,
        "H5": 0.0,
    }[hypothesis]
    return {
        "noise_power": 1.0,
        "clutter_power": 18.0,
        "texture_sigma": 0.55,
        "ridge_width": 0.16,
        "ridge_slope": 0.25,
        "mismatch": mismatch,
    }


def _ridge_scale(
    x: np.ndarray,
    y: np.ndarray,
    center: np.ndarray,
    slope: float,
    width: float,
    texture: np.ndarray,
    clutter_power: float,
    noise_power: float,
) -> np.ndarray:
    distance = (x - slope * y) - center
    ridge = np.exp(-0.5 * np.square(distance / width))
    return noise_power + clutter_power * texture * ridge


def _draw_chunk(
    rng: np.random.Generator,
    hypothesis: str,
    count: int,
    guard: int,
    background: int,
) -> tuple[np.ndarray, np.ndarray]:
    params = _scenario_parameters(hypothesis)
    counts = training_cell_counts(guard, background)
    corner_cells = counts["corner_block_cells"]
    center_cells = counts["center_block_cells"]
    strip_cells = float(counts["directional_strip_cells"])

    if hypothesis == "H0":
        # This follows the eight gamma blocks used to calibrate the production
        # GO alpha: four corner blocks and four center/edge blocks.  CUTs are
        # separate exponential draws, so no CUT reuses a training sample.
        corner_scale = np.ones((4, count), dtype=np.float64)
        center_scale = np.ones((4, count), dtype=np.float64)
        cut_scale = np.ones(count, dtype=np.float64)
    else:
        center = rng.uniform(-0.65, 0.65, count)
        texture = np.exp(rng.normal(0.0, params["texture_sigma"], count))
        # The eight block centers mirror the four directional strips and their
        # shared corners.  Coordinate units are abstract local Doppler/range
        # cells; only the frozen production training topology is normative.
        points = np.asarray(
            [(-1.0, -1.0), (0.0, -1.0), (1.0, -1.0),
             (-1.0, 0.0), (1.0, 0.0),
             (-1.0, 1.0), (0.0, 1.0), (1.0, 1.0)],
            dtype=np.float64,
        )
        block_scales = np.vstack([
            _ridge_scale(
                np.full(count, x + params["mismatch"]), np.full(count, y), center,
                params["ridge_slope"], params["ridge_width"], texture,
                params["clutter_power"], params["noise_power"],
            )
            for x, y in points
        ])
        corner_scale = block_scales[[0, 2, 5, 7]]
        center_scale = block_scales[[1, 3, 4, 6]]
        cut_scale = _ridge_scale(
            np.full(count, params["mismatch"]), np.zeros(count), center,
            params["ridge_slope"], params["ridge_width"], texture,
            params["clutter_power"], params["noise_power"],
        )

    corner = rng.gamma(corner_cells, corner_scale)
    middle = rng.gamma(center_cells, center_scale)
    tl, tr, bl, br = corner
    tc, lc, rc, bc = middle
    left = (tl + lc + bl) / strip_cells
    right = (tr + rc + br) / strip_cells
    top = (tl + tc + tr) / strip_cells
    bottom = (bl + bc + br) / strip_cells
    maximum = np.maximum.reduce((left, right, top, bottom))
    cut = rng.exponential(cut_scale)
    return cut, maximum


def simulate_hypothesis(
    hypothesis: str,
    seed: int,
    cut_count: int = DEFAULT_CUT_COUNT,
    alpha: float = DEFAULT_ALPHA,
    guard: int = DEFAULT_GUARD,
    background: int = DEFAULT_BACKGROUND,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, object]:
    """Simulate one independent CUT set for one H hypothesis."""

    if hypothesis not in HYPOTHESES:
        raise ValueError(f"unknown hypothesis: {hypothesis}")
    if cut_count < 1 or chunk_size < 1:
        raise ValueError("cut_count and chunk_size must be positive")
    if not math.isfinite(alpha) or alpha <= 0.0:
        raise ValueError("alpha must be finite and positive")
    rng = np.random.default_rng(int(seed))
    hits = 0
    valid_count = 0
    excluded_count = 0
    for start in range(0, cut_count, chunk_size):
        size = min(chunk_size, cut_count - start)
        cut, training_max = _draw_chunk(rng, hypothesis, size, guard, background)
        valid = np.ones(size, dtype=bool)
        ids = np.arange(start, start + size, dtype=np.int64)
        counted = summarize_cut_events(cut, alpha * training_max, valid, ids)
        hits += int(counted["hit_count"])
        valid_count += int(counted["valid_cut_count"])
        excluded_count += int(counted["excluded_cut_count"])
    rate = float(hits / valid_count) if valid_count else math.nan
    low, high = wilson_interval(hits, valid_count)
    contract = _HYPOTHESIS_CONTRACT[hypothesis]
    return {
        "hypothesis": hypothesis,
        "hypothesis_name": contract["name"],
        "seed": int(seed),
        "cut_count_requested": int(cut_count),
        "valid_cut_count": int(valid_count),
        "hit_count": int(hits),
        "excluded_cut_count": int(excluded_count),
        "invalid_cut_count": int(excluded_count),
        "duplicate_cut_count": 0,
        "empirical_rate": rate,
        "wilson95_low": low,
        "wilson95_high": high,
        "metric_definition": metric_definition(hypothesis),
        "configured_pfa": CONFIGURED_PFA,
        "threshold_status": "frozen_production_alpha",
        "independent_cut_set": True,
        "training_reuse": False,
        "cut_id_namespace": f"seed={int(seed)}:local_index=0..{int(cut_count) - 1}",
        "structured_clutter": bool(contract["structured_clutter"]),
        "correction": contract["correction"],
        "status": "measured",
    }


def _aggregate(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["hypothesis"]), []).append(row)
    result: list[dict[str, object]] = []
    for hypothesis in HYPOTHESES:
        members = grouped.get(hypothesis, [])
        if not members:
            continue
        hit_count = sum(int(row["hit_count"]) for row in members)
        valid_count = sum(int(row["valid_cut_count"]) for row in members)
        low, high = wilson_interval(hit_count, valid_count)
        result.append({
            "hypothesis": hypothesis,
            "hypothesis_name": _HYPOTHESIS_CONTRACT[hypothesis]["name"],
            "seed_count": len(members),
            "cut_count_requested": sum(int(row["cut_count_requested"]) for row in members),
            "valid_cut_count": valid_count,
            "hit_count": hit_count,
            "excluded_cut_count": sum(int(row["excluded_cut_count"]) for row in members),
            "empirical_rate": float(hit_count / valid_count) if valid_count else math.nan,
            "wilson95_low": low,
            "wilson95_high": high,
            "metric_definition": metric_definition(hypothesis),
            "configured_pfa": CONFIGURED_PFA,
            "threshold_status": "frozen_production_alpha",
            "independent_cut_set": all(bool(row["independent_cut_set"]) for row in members),
            "training_reuse": any(bool(row["training_reuse"]) for row in members),
            "status": "measured",
        })
    return result


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run_closure(
    output_root: Path,
    hypotheses: Sequence[str] = HYPOTHESES,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    cut_count: int = DEFAULT_CUT_COUNT,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, object]:
    """Run the matrix and write only compact evidence files."""

    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty output: {output_root}")
    selected = tuple(hypotheses)
    if not selected or any(h not in HYPOTHESES for h in selected):
        raise ValueError(f"hypotheses must be a non-empty subset of {HYPOTHESES}")
    if not seeds:
        raise ValueError("at least one seed is required")
    output_root.mkdir(parents=True, exist_ok=True)

    rows = [
        simulate_hypothesis(hypothesis, int(seed), cut_count=cut_count, chunk_size=chunk_size)
        for hypothesis in selected
        for seed in seeds
    ]
    aggregate = _aggregate(rows)
    manifest = {
        "schema": "unknown_system_error_pfa_closure_v1",
        "status": "completed",
        "ai_training": False,
        "ai_router": False,
        "threshold_tuning": False,
        "hypotheses": list(selected),
        "hypothesis_contract": {h: _HYPOTHESIS_CONTRACT[h] for h in selected},
        "levels": {
            "seeds": [int(seed) for seed in seeds],
            "cut_count_per_hypothesis_seed": int(cut_count),
            "chunk_size": int(chunk_size),
            "large_independent_cut_set": bool(cut_count >= 1_000_000),
        },
        "go_cfar": {
            "cfar_type": "GO",
            "guard_cells": DEFAULT_GUARD,
            "background_cells": DEFAULT_BACKGROUND,
            "alpha": DEFAULT_ALPHA,
            "configured_pfa": CONFIGURED_PFA,
            "alpha_source": "frozen exact production value from include/go_cfar_alpha.hpp for pfa=1e-6,g=4,b=16",
            "training_cell_counts": training_cell_counts(DEFAULT_GUARD, DEFAULT_BACKGROUND),
            "training_definition": "four directional strip means with shared corner blocks; threshold=alpha*max(four means)",
        },
        "independence_contract": {
            "cut_set": "one unique local CUT id per independent sampled trial",
            "training_reuse": False,
            "cut_training_overlap": False,
            "multi_seed": len(seeds) > 1,
        },
        "metric_contract": {
            "H0": "empirical_cell_pfa = independent CUT hits / valid independent CUTs",
            "H1_H5": "structured_clutter_false_hit_fraction = structured-clutter CUT hits / valid independent CUTs",
            "configured_pfa_is_not_empirical_result": True,
            "forbidden_claim": "configured Pfa=1e-6 achieved",
        },
        "counts": {
            "row_count": len(rows),
            "aggregate_count": len(aggregate),
            "total_valid_cuts": sum(int(row["valid_cut_count"]) for row in rows),
            "total_hits": sum(int(row["hit_count"]) for row in rows),
        },
        "source": {
            "working_directory": str(ROOT),
            "source_commit": _git_commit(),
            "python": sys.version,
            "platform": platform.platform(),
        },
        "artifacts": {
            "summary": str((output_root / "pfa_summary.csv").resolve()),
            "aggregate": str((output_root / "pfa_aggregate.csv").resolve()),
            "manifest": str((output_root / "manifest.json").resolve()),
        },
        "limitations": [
            "H0 is a production-geometry GO-CFAR IID closure, not a full CUDA/raw-IQ run.",
            "H1-H5 use fixed synthetic local ridge controls to isolate denominator and correction semantics; their rates are structured-clutter false-hit fractions.",
            "Known geometry/servo correction in H3/H5 is evaluator-only and does not use a blind estimator input.",
            "No claim is made that the configured 1e-6 Pfa is achieved in structured clutter or in production deployment.",
        ],
    }
    _write_csv(output_root / "pfa_summary.csv", rows)
    _write_csv(output_root / "pfa_aggregate.csv", aggregate)
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def _parse_int_list(values: str) -> tuple[int, ...]:
    try:
        parsed = tuple(int(item.strip()) for item in values.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer list: {values}") from exc
    if not parsed:
        raise argparse.ArgumentTypeError("integer list must not be empty")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root", type=Path,
        default=ROOT / "outputs/unknown_system_error_pfa_closure_20260914",
    )
    parser.add_argument("--hypotheses", default=",".join(HYPOTHESES))
    parser.add_argument("--seeds", type=_parse_int_list, default=DEFAULT_SEEDS)
    parser.add_argument("--cut-count", type=int, default=DEFAULT_CUT_COUNT)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    args = parser.parse_args(argv)
    hypotheses = tuple(item.strip() for item in args.hypotheses.split(",") if item.strip())
    manifest = run_closure(
        args.output_root.resolve(), hypotheses=hypotheses, seeds=args.seeds,
        cut_count=args.cut_count, chunk_size=args.chunk_size,
    )
    print(json.dumps({
        "status": manifest["status"],
        "output_root": str(args.output_root.resolve()),
        "row_count": manifest["counts"]["row_count"],
        "aggregate_count": manifest["counts"]["aggregate_count"],
        "total_valid_cuts": manifest["counts"]["total_valid_cuts"],
        "total_hits": manifest["counts"]["total_hits"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
