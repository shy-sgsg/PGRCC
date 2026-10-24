#!/usr/bin/env python3
"""Validate R6 CUDA CFAR branch maps and quantify A0/C threshold margins."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = ROOT / "outputs/phase_r6_stagewise_divergence_20261022"
PRODUCTION_CASES = {
    "A0_ON": "A0_ON_complexdiag",
    "A0_OFF": "A0_OFF",
    "A0_TO": "A0_TO",
    "C_ON": "C_ON",
    "C_OFF": "C_OFF",
    "C_TO": "C_TO",
}
BRANCHES = {
    "dynamic": ("dynamic_in_band", "union_dynamic_in_band"),
    "full": ("full_band", "union_full_band"),
}
MARGIN_BANDS_DB = (0.25, 0.5, 1.0)
FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
UINT64_MASK = (1 << 64) - 1


def _integer(row: Mapping[str, Any], name: str) -> int:
    try:
        return int(row[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid geometry field {name}: {row.get(name)!r}") from exc


def _boolean(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    raise ValueError(f"invalid boolean geometry field {name}: {value!r}")


def build_valid_cut_mask(geometry: Mapping[str, Any]) -> np.ndarray:
    """Mirror include/cfar_geometry.hpp's data-independent CUT eligibility."""
    height = _integer(geometry, "height")
    width = _integer(geometry, "width")
    if height <= 0 or width <= 0:
        raise ValueError("CFAR geometry dimensions must be positive")
    radius = max(0, _integer(geometry, "guard_cells")) + max(
        1, _integer(geometry, "background_cells")
    )
    circular = _boolean(geometry["doppler_circular"], "doppler_circular")
    row = np.arange(height)[:, None]
    col = np.arange(width)[None, :]
    row_min = 0 if circular else radius
    row_max = height - 1 if circular else height - 1 - radius
    mask = (
        (row >= row_min)
        & (row <= row_max)
        & (col >= radius)
        & (col <= width - 1 - radius)
    )

    exclude_start = _integer(geometry, "exclude_row_start")
    exclude_end = _integer(geometry, "exclude_row_end")
    if exclude_start >= 0:
        mask &= ~((row >= exclude_start) & (row <= exclude_end))

    band_mode = _integer(geometry, "cut_band_mode")
    if band_mode not in (0, 1, 2):
        raise ValueError(f"invalid cut_band_mode: {band_mode}")
    if band_mode:
        band_start = _integer(geometry, "cut_band_start")
        band_end = _integer(geometry, "cut_band_end")
        in_band = (row >= band_start) & (row <= band_end)
        mask &= in_band if band_mode == 1 else ~in_band
    return mask


def hit_index_hash(hits: np.ndarray) -> str:
    """Return the C++ diagnostic's FNV-1a hash over positive flat indices."""
    value = FNV_OFFSET
    for index in np.flatnonzero(np.asarray(hits).reshape(-1) > 0):
        index_value = int(index)
        for shift in range(0, 64, 8):
            value ^= (index_value >> shift) & 0xFF
            value = (value * FNV_PRIME) & UINT64_MASK
    return f"fnv1a64:{value:x}"


def summarize_margin_map(
    power: np.ndarray,
    threshold: np.ndarray,
    hits: np.ndarray,
    valid_mask: np.ndarray,
) -> dict[str, Any]:
    """Summarize 10*log10(power/threshold) on valid CUTs only."""
    if not (power.shape == threshold.shape == hits.shape == valid_mask.shape):
        raise ValueError("power, threshold, hits, and valid mask shapes must match")
    valid_power = np.asarray(power[valid_mask], dtype=np.float64)
    valid_threshold = np.asarray(threshold[valid_mask], dtype=np.float64)
    valid_hits = np.asarray(hits[valid_mask]) > 0
    if not valid_power.size:
        raise ValueError("CFAR branch has no valid CUTs")
    if not np.isfinite(valid_power).all() or not np.isfinite(valid_threshold).all():
        raise ValueError("non-finite power/threshold value on a valid CUT")
    if np.any(valid_power < 0):
        raise ValueError("valid CUT power must be nonnegative")

    expected_hits = valid_power > valid_threshold
    matches = bool(np.array_equal(valid_hits, expected_hits))
    if not matches:
        mismatch_count = int(np.count_nonzero(valid_hits != expected_hits))
        raise ValueError(
            f"hit map disagrees with strict power > threshold on {mismatch_count} valid CUTs"
        )

    positive_threshold = valid_threshold > 0
    with np.errstate(divide="ignore", invalid="raise"):
        margins = 10.0 * np.log10(
            valid_power[positive_threshold] / valid_threshold[positive_threshold]
        )
    finite = margins[np.isfinite(margins)]
    nonpositive_count = int(np.count_nonzero(~positive_threshold))
    result: dict[str, Any] = {
        "valid_cut_count": int(valid_power.size),
        "hit_count": int(np.count_nonzero(valid_hits)),
        "hit_mask_matches_power_threshold": matches,
        "threshold_nonpositive_count": nonpositive_count,
        "threshold_negative_count": int(np.count_nonzero(valid_threshold < 0)),
        "threshold_zero_count": int(np.count_nonzero(valid_threshold == 0)),
        "threshold_nonpositive_hit_count": int(
            np.count_nonzero(valid_hits & ~positive_threshold)
        ),
        "evaluable_margin_count": int(np.count_nonzero(positive_threshold)),
        "margin_status": (
            "EVALUABLE" if nonpositive_count == 0 else
            "NOT_EVALUABLE_NO_POSITIVE_THRESHOLD" if not np.any(positive_threshold) else
            "PARTIALLY_EVALUABLE_NONPOSITIVE_THRESHOLD"
        ),
        "zero_power_count": int(np.count_nonzero(valid_power == 0)),
        "finite_margin_count": int(finite.size),
        "margin_min_finite_db": float(np.min(finite)) if finite.size else None,
        "margin_p01_finite_db": float(np.quantile(finite, 0.01)) if finite.size else None,
        "margin_p05_finite_db": float(np.quantile(finite, 0.05)) if finite.size else None,
        "margin_p50_finite_db": float(np.quantile(finite, 0.50)) if finite.size else None,
        "margin_p95_finite_db": float(np.quantile(finite, 0.95)) if finite.size else None,
        "margin_p99_finite_db": float(np.quantile(finite, 0.99)) if finite.size else None,
        "margin_max_finite_db": float(np.max(finite)) if finite.size else None,
    }
    for band in MARGIN_BANDS_DB:
        key = str(band).replace(".", "p")
        result[f"within_{key}_db_count"] = int(np.count_nonzero(np.abs(margins) <= band))
    return result


def _read_geometry(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"missing CFAR geometry CSV: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_f32(path: Path, height: int, width: int) -> np.ndarray:
    if not path.is_file():
        raise ValueError(f"missing CFAR map: {path}")
    values = np.fromfile(path, dtype="<f4")
    expected = height * width
    if values.size != expected:
        raise ValueError(f"wrong CFAR map length: {path} has {values.size}, expected {expected}")
    return values.reshape(height, width)


def _read_meta(path: Path, height: int, width: int) -> None:
    if not path.is_file():
        raise ValueError(f"missing CFAR map metadata: {path}")
    meta = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            meta[key.strip()] = value.strip()
    if meta.get("rows") != str(height) or meta.get("cols") != str(width):
        raise ValueError(f"CFAR map metadata shape mismatch: {path}")
    if meta.get("detect_input_complex_dump") != "true":
        raise ValueError(f"CFAR complex input tap is incomplete: {path}")


def _load_branch(
    root: Path,
    case: str,
    period: int,
    mode: str,
    union_rows: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    branch_name, union_branch_name = BRANCHES[mode]
    run_dir = root / "cfar_branch_diagnostics" / case / "forced_ids/debug" / mode
    geometry_path = run_dir / "result/debug/cfar_geometry_diagnostics.csv"
    rows = _read_geometry(geometry_path)
    matches = [
        row for row in rows
        if _integer(row, "period_id") == period and row.get("branch") == branch_name
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one {branch_name} geometry row for {case} period {period}")
    geometry = matches[0]
    height = _integer(geometry, "height")
    width = _integer(geometry, "width")
    valid = build_valid_cut_mask(geometry)
    valid_count = int(np.count_nonzero(valid))
    if valid_count != _integer(geometry, "valid_cut_count"):
        raise ValueError(f"valid CUT mask count mismatch for {case}/{mode}/period{period}")
    if _integer(geometry, "threshold_test_count") < valid_count:
        raise ValueError(f"threshold test count below valid CUT count for {case}/{mode}/period{period}")

    map_dir = run_dir / "result/debug"
    stem = f"cfar_GMTI{period + 1:02d}_beam050_"
    meta_path = map_dir / f"{stem}meta.txt"
    _read_meta(meta_path, height, width)
    power = _read_f32(map_dir / f"{stem}power.f32", height, width)
    threshold = _read_f32(map_dir / f"{stem}threshold.f32", height, width)
    hits = _read_f32(map_dir / f"{stem}hits.f32", height, width)
    real = _read_f32(map_dir / f"{stem}complex_real.f32", height, width)
    imag = _read_f32(map_dir / f"{stem}complex_imag.f32", height, width)
    if np.any(hits[~valid] > 0):
        raise ValueError(f"CFAR hit outside valid CUT geometry for {case}/{mode}/period{period}")
    actual_hash = hit_index_hash(hits)
    if actual_hash != geometry["hit_index_hash"]:
        raise ValueError(f"map/geometry hit-index hash mismatch for {case}/{mode}/period{period}")
    if int(np.count_nonzero(hits > 0)) != _integer(geometry, "hit_cut_count"):
        raise ValueError(f"map/geometry hit count mismatch for {case}/{mode}/period{period}")

    union_geometry = [
        row for row in union_rows
        if _integer(row, "period_id") == period and row.get("branch") == union_branch_name
    ]
    if len(union_geometry) != 1:
        raise ValueError(f"expected one union geometry row for {case}/{mode}/period{period}")
    union_row = union_geometry[0]
    if (geometry["hit_index_hash"] != union_row["hit_index_hash"]
            or geometry["hit_cut_count"] != union_row["hit_cut_count"]):
        raise ValueError(f"isolated branch differs from union branch record for {case}/{mode}/period{period}")

    summary = summarize_margin_map(power, threshold, hits, valid)
    actual_period = _integer(geometry, "period_id")
    complex_power = real[valid].astype(np.float64) ** 2 + imag[valid].astype(np.float64) ** 2
    valid_power = power[valid].astype(np.float64)
    denom = float(np.mean(complex_power ** 2)) if complex_power.size else 0.0
    rel_rms = (
        math.sqrt(float(np.mean((complex_power - valid_power) ** 2)) / denom)
        if denom > 0 else None
    )
    summary.update({
        "case": case,
        "period_id": actual_period,
        "branch": mode,
        "geometry_branch": branch_name,
        "geometry_hit_index_hash": actual_hash,
        "union_branch_hash_match": True,
        "complex_power_rel_rms": rel_rms,
        "complex_power_max_abs_error": float(np.max(np.abs(complex_power - valid_power))),
    })
    margin_db = np.full(power.shape, np.nan, dtype=np.float64)
    margin_evaluable = valid & (threshold > 0)
    with np.errstate(divide="ignore", invalid="raise"):
        margin_db[margin_evaluable] = 10.0 * np.log10(
            power[margin_evaluable].astype(np.float64)
            / threshold[margin_evaluable].astype(np.float64)
        )
    compact = {
        "geometry": geometry,
        "valid": valid,
        "hits": hits > 0,
        "margin_db": margin_db,
        "threshold_nonpositive": valid & (threshold <= 0),
        "shape": (height, width),
    }
    return summary, compact


def _load_union_hits(root: Path, case: str, period: int, shape: tuple[int, int]) -> np.ndarray:
    prod_case = PRODUCTION_CASES[case]
    path = root / "production" / prod_case / "result/debug" / (
        f"cfar_GMTI{period + 1:02d}_beam050_hits.f32"
    )
    return _read_f32(path, *shape) > 0


def _csv_write(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def analyze(root: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError(f"refusing to overwrite existing analysis directory: {output_dir}")
    margins: list[dict[str, Any]] = []
    runs: dict[tuple[str, int, str], dict[str, Any]] = {}
    union_checks: list[dict[str, Any]] = []
    exact_indices: dict[str, np.ndarray] = {}

    for case, production_case in PRODUCTION_CASES.items():
        union_path = root / "production" / production_case / "result/debug/cfar_geometry_diagnostics.csv"
        union_rows = _read_geometry(union_path)
        for period in range(3):
            branch_hits: dict[str, np.ndarray] = {}
            branch_valid: np.ndarray | None = None
            branch_shapes: set[tuple[int, int]] = set()
            for mode in BRANCHES:
                summary, compact = _load_branch(root, case, period, mode, union_rows)
                margins.append(summary)
                runs[(case, period, mode)] = compact
                exact_indices[f"{case}_p{period}_{mode}_nonpositive_threshold"] = (
                    np.flatnonzero(compact["threshold_nonpositive"].reshape(-1)).astype(np.uint32)
                )
                branch_hits[mode] = compact["hits"]
                branch_shapes.add(compact["shape"])
                if branch_valid is None:
                    branch_valid = compact["valid"]
                elif not np.array_equal(branch_valid, compact["valid"]):
                    raise ValueError(f"dynamic/full valid CUT masks differ for {case} period {period}")
            if len(branch_shapes) != 1:
                raise ValueError(f"dynamic/full map shapes differ for {case} period {period}")
            union_hit = branch_hits["dynamic"] | branch_hits["full"]
            actual_union_hit = _load_union_hits(root, case, period, next(iter(branch_shapes)))
            if not np.array_equal(union_hit, actual_union_hit):
                raise ValueError(f"branch OR differs from Production union hit map for {case} period {period}")
            union_checks.append({
                "case": case,
                "period_id": period,
                "branch_or_matches_production_union": True,
                "union_hit_count": int(np.count_nonzero(union_hit)),
                "valid_cut_count": int(np.count_nonzero(branch_valid)),
            })

    flips: list[dict[str, Any]] = []
    for role in ("ON", "OFF", "TO"):
        a0_case, c_case = f"A0_{role}", f"C_{role}"
        for period in range(3):
            a0_dynamic = runs[(a0_case, period, "dynamic")]
            c_dynamic = runs[(c_case, period, "dynamic")]
            a0_full = runs[(a0_case, period, "full")]
            c_full = runs[(c_case, period, "full")]
            for mode in ("dynamic", "full", "union"):
                if mode == "dynamic":
                    a0_hit, c_hit = a0_dynamic["hits"], c_dynamic["hits"]
                    a0_distance = np.abs(a0_dynamic["margin_db"])
                    c_distance = np.abs(c_dynamic["margin_db"])
                    valid = a0_dynamic["valid"]
                elif mode == "full":
                    a0_hit, c_hit = a0_full["hits"], c_full["hits"]
                    a0_distance = np.abs(a0_full["margin_db"])
                    c_distance = np.abs(c_full["margin_db"])
                    valid = a0_full["valid"]
                else:
                    a0_hit = a0_dynamic["hits"] | a0_full["hits"]
                    c_hit = c_dynamic["hits"] | c_full["hits"]
                    a0_distance = np.fmin(
                        np.abs(a0_dynamic["margin_db"]), np.abs(a0_full["margin_db"])
                    )
                    c_distance = np.fmin(
                        np.abs(c_dynamic["margin_db"]), np.abs(c_full["margin_db"])
                    )
                    valid = a0_dynamic["valid"]
                if not np.array_equal(valid, c_dynamic["valid"]):
                    raise ValueError(f"A0/C valid CUT geometry differs for {role} period {period}")
                a0_flat = a0_hit.reshape(-1)
                c_flat = c_hit.reshape(-1)
                v_flat = valid.reshape(-1)
                shared = np.flatnonzero(v_flat & a0_flat & c_flat).astype(np.uint32)
                a0_only = np.flatnonzero(v_flat & a0_flat & ~c_flat).astype(np.uint32)
                c_only = np.flatnonzero(v_flat & c_flat & ~a0_flat).astype(np.uint32)
                prefix = f"{role}_p{period}_{mode}"
                exact_indices[f"{prefix}_shared"] = shared
                exact_indices[f"{prefix}_a0_only"] = a0_only
                exact_indices[f"{prefix}_c_only"] = c_only
                changed = a0_only.size + c_only.size
                row: dict[str, Any] = {
                    "role": role,
                    "period_id": period,
                    "branch": mode,
                    "valid_cut_count": int(np.count_nonzero(v_flat)),
                    "a0_hit_count": int(np.count_nonzero(v_flat & a0_flat)),
                    "c_hit_count": int(np.count_nonzero(v_flat & c_flat)),
                    "shared_hit_count": int(shared.size),
                    "a0_only_hit_count": int(a0_only.size),
                    "c_only_hit_count": int(c_only.size),
                    "hit_flip_count": int(changed),
                }
                if mode == "dynamic":
                    a0_nonpositive = a0_dynamic["threshold_nonpositive"]
                    c_nonpositive = c_dynamic["threshold_nonpositive"]
                elif mode == "full":
                    a0_nonpositive = a0_full["threshold_nonpositive"]
                    c_nonpositive = c_full["threshold_nonpositive"]
                else:
                    a0_nonpositive = (
                        a0_dynamic["threshold_nonpositive"]
                        | a0_full["threshold_nonpositive"]
                    )
                    c_nonpositive = (
                        c_dynamic["threshold_nonpositive"]
                        | c_full["threshold_nonpositive"]
                    )
                flip_mask = (a0_hit != c_hit) & valid
                row["hit_flips_with_nonpositive_a0_threshold"] = int(
                    np.count_nonzero(flip_mask & a0_nonpositive)
                )
                row["hit_flips_with_nonpositive_c_threshold"] = int(
                    np.count_nonzero(flip_mask & c_nonpositive)
                )
                row["hit_flips_with_nonpositive_threshold_both"] = int(
                    np.count_nonzero(flip_mask & a0_nonpositive & c_nonpositive)
                )
                row["hit_flips_with_positive_thresholds_both"] = int(
                    np.count_nonzero(flip_mask & ~a0_nonpositive & ~c_nonpositive)
                )
                for band in MARGIN_BANDS_DB:
                    suffix = str(band).replace(".", "p")
                    row[f"flips_within_{suffix}_db_of_a0_threshold"] = int(
                        np.count_nonzero((a0_distance <= band) & (a0_hit != c_hit) & valid)
                    )
                    row[f"flips_within_{suffix}_db_of_c_threshold"] = int(
                        np.count_nonzero((c_distance <= band) & (a0_hit != c_hit) & valid)
                    )
                    row[f"flips_near_either_{suffix}_db"] = int(
                        np.count_nonzero(
                            ((a0_distance <= band) | (c_distance <= band))
                            & (a0_hit != c_hit) & valid
                        )
                    )
                flips.append(row)

    binary = ROOT / "build/GMTI_pipe_core"
    resolved_root = root.resolve()
    try:
        root_identity = resolved_root.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        root_identity = str(resolved_root)
    manifest = {
        "analysis": "R6 exact branch CFAR margin and A0/C hit-index comparison",
        "root": root_identity,
        "production_executable": binary.relative_to(ROOT).as_posix(),
        "production_executable_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "margin_definition": "10*log10(power/threshold); detector hit is strict power > threshold",
        "valid_cut_mask": "include/cfar_geometry.hpp count_geometry contract reconstructed from each geometry row",
        "near_threshold_bands_db": list(MARGIN_BANDS_DB),
        "period_mapping": "forced result IDs 1,2,3 map to period IDs 0,1,2",
        "checks": {
            "branch_count_and_hit_hash_match_union_geometry": len(margins) == 36,
            "branch_or_matches_production_union": len(union_checks) == 18,
        },
        "nonpositive_threshold_valid_cuts_total": sum(
            int(row["threshold_nonpositive_count"]) for row in margins
        ),
        "nonpositive_threshold_hits_total": sum(
            int(row["threshold_nonpositive_hit_count"]) for row in margins
        ),
        "branch_map_rows": len(margins),
        "union_map_rows": len(union_checks),
        "a0_c_hit_comparison_rows": len(flips),
    }
    if not all(manifest["checks"].values()):
        raise ValueError("analysis acceptance checks incomplete")

    output_dir.mkdir(parents=True, exist_ok=False)
    _csv_write(output_dir / "cfar_branch_margin_summary.csv", margins)
    _csv_write(output_dir / "cfar_union_map_validation.csv", union_checks)
    _csv_write(output_dir / "cfar_a0_c_hit_flip_summary.csv", flips)
    np.savez_compressed(output_dir / "cfar_a0_c_exact_hit_indices.npz", **exact_indices)
    (output_dir / "analysis_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    output = args.output or args.root / "analysis/cfar_branch_metrics"
    try:
        result = analyze(args.root, output)
    except (OSError, ValueError, KeyError) as exc:
        print(f"R6 CFAR analysis failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
