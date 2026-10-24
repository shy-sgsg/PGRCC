#!/usr/bin/env python3
"""Re-evaluate all saved R7 three-period Production CFAR maps against float64 GO references."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, TextIO

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r.go_cfar_reference import (  # noqa: E402
    CfarGeometry,
    ThresholdMaps,
    _geometry_masks,
    thresholds_integral_f64,
)


R6_ROOT = ROOT / "outputs/phase_r6_stagewise_divergence_20261022"
R7_ROOT = ROOT / "outputs/phase_r7_cfar_correctness_20261022"
POST_MANIFEST_PATH = R7_ROOT / "post_fix_matrix_3period_final/experiment_manifest.json"
PRE_B_MANIFEST_PATH = R7_ROOT / "pre_fix_matrix_3period_id_only/experiment_manifest.json"
R6_NUMERIC_PATH = R7_ROOT / "numeric_analysis_r6/historical_r6_comparison.json"
DEFAULT_OUTPUT_DIR = (
    ROOT / "docs/research/phase_r/evidence/r7_cfar_correctness_20261022/analysis/r7_three_period"
)
CASE_IDS = (
    "A0_OFF", "A0_ON", "A0_TO",
    "B_OFF", "B_ON", "B_TO",
    "C_OFF", "C_ON", "C_TO",
)
BRANCHES = ("dynamic_in_band", "full_band")
ERROR_QUANTILES = ("p50", "p90", "p95", "p99", "p99_9")
HEIGHT, WIDTH = 130, 4096


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_map(path: Path, geometry: CfarGeometry) -> np.ndarray:
    values = np.fromfile(path, dtype="<f4")
    expected = geometry.height * geometry.width
    if values.size != expected:
        raise ValueError(f"{path}: expected {expected} float32 cells, got {values.size}")
    return values.reshape(geometry.height, geometry.width)


def _geometry(row: dict[str, str]) -> CfarGeometry:
    return CfarGeometry(
        height=int(row["height"]),
        width=int(row["width"]),
        guard_cells=int(row["guard_cells"]),
        background_cells=int(row["background_cells"]),
        doppler_circular=row["doppler_circular"].lower() == "true",
        exclude_row_start=int(row["exclude_row_start"]),
        exclude_row_end=int(row["exclude_row_end"]),
        cut_band_start=int(row["cut_band_start"]),
        cut_band_end=int(row["cut_band_end"]),
        cut_band_mode=int(row["cut_band_mode"]),
    )


def _geometry_identity(row: dict[str, str]) -> tuple[Any, ...]:
    return (
        int(row["height"]), int(row["width"]), int(row["guard_cells"]),
        int(row["background_cells"]), row["doppler_circular"].lower(),
        int(row["exclude_row_start"]), int(row["exclude_row_end"]),
        int(row["cut_band_start"]), int(row["cut_band_end"]),
        int(row["cut_band_mode"]), row["cfar_type"], float(row["alpha"]),
    )


def _geometry_row(
    rows: list[dict[str, str]], period_id: int, branch: str
) -> dict[str, str]:
    found = [
        row for row in rows
        if int(row["period_id"]) == period_id and branch in row["branch"]
    ]
    if len(found) != 1:
        raise ValueError(
            f"period {period_id}, branch {branch}: expected one geometry row, found {len(found)}"
        )
    return found[0]


def _period_input(record: dict[str, Any], period_id: int) -> dict[str, Any]:
    found = [item for item in record["input_periods"] if int(item["period_index"]) == period_id]
    if len(found) != 1:
        raise ValueError(f"expected one manifest input for period {period_id}, found {len(found)}")
    return found[0]


def _r6_history_rows(
    history: dict[str, Any], case_id: str, period_id: int
) -> list[dict[str, Any]]:
    rows = [
        row for row in history["branch_period_maps"]
        if row["case_id"] == case_id and int(row["period_id"]) == period_id
    ]
    by_branch = {row["branch"]: row for row in rows}
    if set(by_branch) != set(BRANCHES):
        raise ValueError(f"{case_id} period {period_id}: missing R6 branch-map provenance")
    if len(rows) != 2:
        raise ValueError(f"{case_id} period {period_id}: duplicate R6 branch-map provenance")
    return [by_branch[branch] for branch in BRANCHES]


def _find_r6_production_case(case_id: str, expected_xml_sha256: str) -> Path:
    candidates: list[Path] = []
    production_root = R6_ROOT / "production"
    for case_root in production_root.iterdir():
        runtime_xml = case_root / "runtime.xml"
        if case_root.is_dir() and runtime_xml.is_file() and _sha256(runtime_xml) == expected_xml_sha256:
            candidates.append(case_root)
    if len(candidates) != 1:
        raise ValueError(
            f"{case_id}: expected exactly one R6 Production run with paired runtime XML, "
            f"found {[path.name for path in candidates]}"
        )
    return candidates[0]


def _check_execution_record(record: dict[str, Any], case_id: str, label: str) -> None:
    if record.get("status") != "EXECUTED" or int(record.get("exit_code", -1)) != 0:
        raise ValueError(f"{label} {case_id}: Production execution is not successful")
    if record.get("period_sequence_status") != "evaluable":
        raise ValueError(f"{label} {case_id}: three-period sequence is incomplete")
    period_records = record.get("period_run_records", [])
    if len(period_records) != 3:
        raise ValueError(f"{label} {case_id}: expected three period run records")
    for period_id, period in enumerate(period_records):
        expected_id = period_id + 1
        if (
            int(period["period_index"]) != period_id
            or int(period["result_id"]) != expected_id
            or int(period["sequence"]) != expected_id
            or int(period["total"]) != 3
            or not period["cycle_success"]
        ):
            raise ValueError(f"{label} {case_id}: invalid period/result mapping {period}")


def _assert_same_shape(*arrays: np.ndarray) -> None:
    if len({np.asarray(array).shape for array in arrays}) != 1:
        raise ValueError("branch maps and masks must have identical shapes")


def select_threshold_from_high_power_branch(
    dynamic_power: np.ndarray,
    dynamic_threshold: np.ndarray,
    full_power: np.ndarray,
    full_threshold: np.ndarray,
) -> np.ndarray:
    """Match the Production union map: choose dynamic on a power tie."""
    _assert_same_shape(dynamic_power, dynamic_threshold, full_power, full_threshold)
    return np.where(
        np.asarray(dynamic_power) >= np.asarray(full_power),
        np.asarray(dynamic_threshold),
        np.asarray(full_threshold),
    )


def union_reference_hit_mask(
    dynamic_power: np.ndarray,
    dynamic_threshold: np.ndarray,
    dynamic_valid: np.ndarray,
    full_power: np.ndarray,
    full_threshold: np.ndarray,
    full_valid: np.ndarray,
) -> np.ndarray:
    """Return a unique OR of branch hits using strict Production CUT > threshold."""
    values = (
        dynamic_power, dynamic_threshold, dynamic_valid,
        full_power, full_threshold, full_valid,
    )
    _assert_same_shape(*values)
    dynamic_hit = (np.asarray(dynamic_power) > np.asarray(dynamic_threshold)) & np.asarray(
        dynamic_valid, dtype=bool
    )
    full_hit = (np.asarray(full_power) > np.asarray(full_threshold)) & np.asarray(
        full_valid, dtype=bool
    )
    return dynamic_hit | full_hit


def _threshold_metrics(
    actual: np.ndarray, reference: np.ndarray, valid: np.ndarray
) -> dict[str, float | int]:
    actual64 = np.asarray(actual, dtype=np.float64)[valid]
    reference64 = np.asarray(reference, dtype=np.float64)[valid]
    reference_f32 = reference64.astype(np.float32)
    ulp = np.abs(np.spacing(reference_f32).astype(np.float64))
    error = np.abs(actual64 - reference64)
    ulp_error = error / np.where(ulp > 0.0, ulp, np.inf)
    quantiles = (
        np.quantile(error, (0.5, 0.9, 0.95, 0.99, 0.999))
        if error.size
        else np.zeros(len(ERROR_QUANTILES), dtype=np.float64)
    )
    return {
        "max_abs_error": float(np.max(error)) if error.size else 0.0,
        **{
            f"{name}_abs_error": float(value)
            for name, value in zip(ERROR_QUANTILES, quantiles, strict=True)
        },
        "max_float32_ulp_error": float(np.max(ulp_error)) if error.size else 0.0,
        "cuts_over_2_float32_ulp": int(np.count_nonzero(ulp_error > 2.0)),
        "exact_float32_reference_cuts": int(
            np.count_nonzero(np.asarray(actual)[valid] == reference_f32)
        ),
        "valid_cut_count": int(error.size),
    }


def _threshold_counts(
    actual: np.ndarray, reference: np.ndarray, valid: np.ndarray
) -> dict[str, int]:
    actual_values = np.asarray(actual)
    reference_values = np.asarray(reference)
    mask = np.asarray(valid, dtype=bool)
    return {
        "negative": int(np.count_nonzero((actual_values < 0) & mask)),
        "zero": int(np.count_nonzero((actual_values == 0) & mask)),
        "reference_negative": int(np.count_nonzero((reference_values < 0) & mask)),
        "reference_zero": int(np.count_nonzero((reference_values == 0) & mask)),
        "nonpositive_with_positive_reference": int(
            np.count_nonzero((actual_values <= 0) & (reference_values > 0) & mask)
        ),
    }


def _hit_flip_counts(
    actual: np.ndarray, expected: np.ndarray, valid: np.ndarray
) -> dict[str, int]:
    actual_mask = np.asarray(actual, dtype=bool) & valid
    expected_mask = np.asarray(expected, dtype=bool) & valid
    return {
        "actual_hits": int(np.count_nonzero(actual_mask)),
        "reference_hits": int(np.count_nonzero(expected_mask)),
        "extra_hits": int(np.count_nonzero(actual_mask & ~expected_mask)),
        "missed_hits": int(np.count_nonzero(expected_mask & ~actual_mask)),
        "hit_flips": int(np.count_nonzero(actual_mask != expected_mask)),
    }


def _branch_map_path(root: Path, result_id: int, branch: str, kind: str) -> Path:
    return root / f"cfar_GMTI{result_id:02d}_beam050_{branch}_{kind}.f32"


def _union_map_path(root: Path, result_id: int, kind: str) -> Path:
    return root / f"cfar_GMTI{result_id:02d}_beam050_{kind}.f32"


def _analyze_case_period(
    case_id: str,
    period_id: int,
    pre_union_root: Path,
    post_root: Path,
    pre_branch_roots: dict[str, Path] | None,
    pre_geometry_rows: list[dict[str, str]],
    post_geometry_rows: list[dict[str, str]],
    input_sha256: str,
    pre_binary_sha256: str,
    post_binary_sha256: str,
    runtime_xml_sha256: str,
    result_id: int,
) -> dict[str, Any]:
    branch_inputs: dict[str, dict[str, Any]] = {}
    post_reference: dict[str, ThresholdMaps] = {}
    post_power: dict[str, np.ndarray] = {}
    post_threshold: dict[str, np.ndarray] = {}
    post_hits: dict[str, np.ndarray] = {}
    post_geometry_rows_by_branch: dict[str, dict[str, str]] = {}

    for branch in BRANCHES:
        post_geom_row = _geometry_row(post_geometry_rows, period_id, branch)
        pre_geom_row = _geometry_row(pre_geometry_rows, period_id, branch)
        if _geometry_identity(pre_geom_row) != _geometry_identity(post_geom_row):
            raise ValueError(f"{case_id} period {period_id} {branch}: pre/post geometry differs")
        geom = _geometry(post_geom_row)
        if (geom.height, geom.width) != (HEIGHT, WIDTH):
            raise ValueError(f"{case_id} period {period_id}: unexpected map dimensions")

        power = _read_map(_branch_map_path(post_root, result_id, branch, "power"), geom)
        threshold = _read_map(_branch_map_path(post_root, result_id, branch, "threshold"), geom)
        hits = _read_map(_branch_map_path(post_root, result_id, branch, "hits"), geom) > 0.0
        if int(np.count_nonzero(hits & geom_valid_mask(geom))) != int(
            post_geom_row["hit_cut_count"]
        ):
            raise ValueError(f"{case_id} period {period_id} {branch}: map/geometry hit count differs")

        reference = thresholds_integral_f64(power, geom, float(post_geom_row["alpha"]))
        reference_threshold_f32 = reference.threshold.astype(np.float32)
        if not np.array_equal(threshold[reference.valid_cut], reference_threshold_f32[reference.valid_cut]):
            raise ValueError(f"{case_id} period {period_id} {branch}: post threshold differs from float64 reference rounded to float32")
        expected_branch_hits = (power > reference_threshold_f32) & reference.valid_cut
        if not np.array_equal(hits[reference.valid_cut], expected_branch_hits[reference.valid_cut]):
            raise ValueError(f"{case_id} period {period_id} {branch}: post hits differ from float64 reference decisions")
        branch_inputs[branch] = {
            "geometry": geom,
            "pre_geometry_row": pre_geom_row,
            "post_geometry_row": post_geom_row,
        }
        post_reference[branch] = reference
        post_power[branch] = power
        post_threshold[branch] = threshold
        post_hits[branch] = hits
        post_geometry_rows_by_branch[branch] = post_geom_row

    dynamic_ref = post_reference["dynamic_in_band"]
    full_ref = post_reference["full_band"]
    dynamic_power = post_power["dynamic_in_band"]
    full_power = post_power["full_band"]
    dynamic_reference_f32 = dynamic_ref.threshold.astype(np.float32)
    full_reference_f32 = full_ref.threshold.astype(np.float32)
    expected_union_hits = union_reference_hit_mask(
        dynamic_power,
        dynamic_reference_f32,
        dynamic_ref.valid_cut,
        full_power,
        full_reference_f32,
        full_ref.valid_cut,
    )
    union_valid = dynamic_ref.valid_cut | full_ref.valid_cut
    select_dynamic = dynamic_power >= full_power
    selected_reference64 = np.where(
        select_dynamic, dynamic_ref.threshold, full_ref.threshold
    )
    selected_reference_f32 = selected_reference64.astype(np.float32)
    selected_valid = np.where(select_dynamic, dynamic_ref.valid_cut, full_ref.valid_cut)
    if not np.array_equal(union_valid, selected_valid):
        raise ValueError(f"{case_id} period {period_id}: branch union and selected-threshold valid-CUT domains differ")

    post_union_power = _read_map(_union_map_path(post_root, result_id, "power"), branch_inputs[BRANCHES[0]]["geometry"])
    post_union_threshold = _read_map(_union_map_path(post_root, result_id, "threshold"), branch_inputs[BRANCHES[0]]["geometry"])
    post_union_hits = _read_map(_union_map_path(post_root, result_id, "hits"), branch_inputs[BRANCHES[0]]["geometry"]) > 0.0
    expected_post_union_threshold = select_threshold_from_high_power_branch(
        dynamic_power, post_threshold["dynamic_in_band"],
        full_power, post_threshold["full_band"],
    )
    if not np.array_equal(post_union_power, np.maximum(dynamic_power, full_power)):
        raise ValueError(f"{case_id} period {period_id}: post union power is not branch maximum")
    if not np.array_equal(post_union_threshold, expected_post_union_threshold):
        raise ValueError(f"{case_id} period {period_id}: post union threshold does not select the higher-power branch")
    if not np.array_equal(post_union_hits, post_hits["dynamic_in_band"] | post_hits["full_band"]):
        raise ValueError(f"{case_id} period {period_id}: post union hit map differs from branch OR")
    if not np.array_equal(
        post_union_threshold[selected_valid], selected_reference_f32[selected_valid]
    ):
        raise ValueError(f"{case_id} period {period_id}: post union threshold differs from float64 reference rounded to float32")
    if not np.array_equal(post_union_hits[union_valid], expected_union_hits[union_valid]):
        raise ValueError(f"{case_id} period {period_id}: post union hits differ from reference branch OR")

    pre_union_power = _read_map(_union_map_path(pre_union_root, result_id, "power"), branch_inputs[BRANCHES[0]]["geometry"])
    pre_union_threshold = _read_map(_union_map_path(pre_union_root, result_id, "threshold"), branch_inputs[BRANCHES[0]]["geometry"])
    pre_union_hits = _read_map(_union_map_path(pre_union_root, result_id, "hits"), branch_inputs[BRANCHES[0]]["geometry"]) > 0.0
    if not np.array_equal(pre_union_power, post_union_power):
        raise ValueError(f"{case_id} period {period_id}: pre/post union power maps differ")

    pre_branch_results: dict[str, Any] | None = None
    pre_union_branch_or_matches: bool | None = None
    if pre_branch_roots is not None:
        pre_branch_results = {}
        pre_actual_branch_hits: dict[str, np.ndarray] = {}
        for branch in BRANCHES:
            geom = branch_inputs[branch]["geometry"]
            pre_root = pre_branch_roots[branch]
            pre_geom_row = branch_inputs[branch]["pre_geometry_row"]
            pre_power = _read_map(
                pre_root / f"cfar_GMTI{result_id:02d}_beam050_power.f32", geom
            )
            pre_threshold = _read_map(
                pre_root / f"cfar_GMTI{result_id:02d}_beam050_threshold.f32", geom
            )
            pre_hits = _read_map(
                pre_root / f"cfar_GMTI{result_id:02d}_beam050_hits.f32", geom
            ) > 0.0
            if not np.array_equal(pre_power, post_power[branch]):
                raise ValueError(f"{case_id} period {period_id} {branch}: pre/post branch power differs")
            if int(np.count_nonzero(pre_hits & post_reference[branch].valid_cut)) != int(
                pre_geom_row["hit_cut_count"]
            ):
                raise ValueError(f"{case_id} period {period_id} {branch}: pre branch map/geometry hit count differs")
            expected_branch_hits = (
                post_power[branch] > post_reference[branch].threshold.astype(np.float32)
            ) & post_reference[branch].valid_cut
            pre_branch_results[branch] = {
                "threshold_counts": _threshold_counts(
                    pre_threshold, post_reference[branch].threshold, post_reference[branch].valid_cut
                ),
                "threshold_error": _threshold_metrics(
                    pre_threshold, post_reference[branch].threshold, post_reference[branch].valid_cut
                ),
                "hit_flips": _hit_flip_counts(
                    pre_hits, expected_branch_hits, post_reference[branch].valid_cut
                ),
                "power_map_matches_post": True,
                "input_sha256": input_sha256,
                "branch_config_sha256": pre_geom_row.get("config_sha256"),
            }
            pre_actual_branch_hits[branch] = pre_hits

        pre_union_branch_or_matches = np.array_equal(
            pre_union_hits,
            pre_actual_branch_hits["dynamic_in_band"] | pre_actual_branch_hits["full_band"],
        )
        if not pre_union_branch_or_matches:
            raise ValueError(f"{case_id} period {period_id}: pre union hit map differs from pre branch OR")
        pre_union_from_branches_threshold = select_threshold_from_high_power_branch(
            post_power["dynamic_in_band"],
            _read_map(pre_branch_roots["dynamic_in_band"] / f"cfar_GMTI{result_id:02d}_beam050_threshold.f32", branch_inputs["dynamic_in_band"]["geometry"]),
            post_power["full_band"],
            _read_map(pre_branch_roots["full_band"] / f"cfar_GMTI{result_id:02d}_beam050_threshold.f32", branch_inputs["full_band"]["geometry"]),
        )
        if not np.array_equal(pre_union_threshold, pre_union_from_branches_threshold):
            raise ValueError(f"{case_id} period {period_id}: pre union threshold differs from selected pre branch map")

    return {
        "case_id": case_id,
        "condition": case_id.split("_")[0],
        "mode": case_id.split("_")[1],
        "period_id": period_id,
        "result_id": result_id,
        "input_sha256": input_sha256,
        "pre_binary_sha256": pre_binary_sha256,
        "post_binary_sha256": post_binary_sha256,
        "runtime_xml_sha256": runtime_xml_sha256,
        "valid_cut_count_per_branch": int(np.count_nonzero(dynamic_ref.valid_cut)),
        "pre_union_power_matches_post": True,
        "post_union_power_matches_branch_max": True,
        "post_union_hits_match_branch_or": True,
        "pre_union_hits_match_pre_branch_or": pre_union_branch_or_matches,
        "pre_branch_metrics": pre_branch_results,
        "post_branch_metrics": {
            branch: {
                "threshold_counts": _threshold_counts(
                    post_threshold[branch], post_reference[branch].threshold,
                    post_reference[branch].valid_cut,
                ),
                "threshold_error": _threshold_metrics(
                    post_threshold[branch], post_reference[branch].threshold,
                    post_reference[branch].valid_cut,
                ),
                "hit_flips": _hit_flip_counts(
                    post_hits[branch],
                    (post_power[branch] > post_reference[branch].threshold.astype(np.float32))
                    & post_reference[branch].valid_cut,
                    post_reference[branch].valid_cut,
                ),
            }
            for branch in BRANCHES
        },
        "pre_union_threshold_counts": _threshold_counts(
            pre_union_threshold, selected_reference64, selected_valid
        ),
        "post_union_threshold_counts": _threshold_counts(
            post_union_threshold, selected_reference64, selected_valid
        ),
        "pre_union_threshold_error": _threshold_metrics(
            pre_union_threshold, selected_reference64, selected_valid
        ),
        "post_union_threshold_error": _threshold_metrics(
            post_union_threshold, selected_reference64, selected_valid
        ),
        "pre_union_hit_flips": _hit_flip_counts(pre_union_hits, expected_union_hits, union_valid),
        "post_union_hit_flips": _hit_flip_counts(post_union_hits, expected_union_hits, union_valid),
        "pre_unique_union_hit_count": int(np.count_nonzero(pre_union_hits & union_valid)),
        "post_unique_union_hit_count": int(np.count_nonzero(post_union_hits & union_valid)),
        "reference_unique_union_hit_count": int(np.count_nonzero(expected_union_hits & union_valid)),
    }


def geom_valid_mask(geometry: CfarGeometry) -> np.ndarray:
    # Reuse the reference module's exact geometry definition without calculating
    # a redundant threshold map merely to validate the saved hit count.
    return _geometry_masks(geometry)[1]


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_mode: dict[str, Any] = {}
    for mode in ("OFF", "ON", "TO"):
        group = [row for row in rows if row["mode"] == mode]
        branch_totals: dict[str, Any] = {}
        for branch in BRANCHES:
            pre_branch_rows = [row["pre_branch_metrics"][branch] for row in group if row["pre_branch_metrics"]]
            post_branch_rows = [row["post_branch_metrics"][branch] for row in group]
            branch_totals[branch] = {
                "pre_branch_map_count": len(pre_branch_rows),
                "pre_extra_hit_flips": sum(r["hit_flips"]["extra_hits"] for r in pre_branch_rows),
                "pre_missed_hit_flips": sum(r["hit_flips"]["missed_hits"] for r in pre_branch_rows),
                "post_extra_hit_flips": sum(r["hit_flips"]["extra_hits"] for r in post_branch_rows),
                "post_missed_hit_flips": sum(r["hit_flips"]["missed_hits"] for r in post_branch_rows),
                "pre_nonpositive_with_positive_reference": sum(
                    r["threshold_counts"]["nonpositive_with_positive_reference"] for r in pre_branch_rows
                ),
                "post_nonpositive_with_positive_reference": sum(
                    r["threshold_counts"]["nonpositive_with_positive_reference"] for r in post_branch_rows
                ),
            }
        by_mode[mode] = {
            "case_period_count": len(group),
            "pre_branch_maps_evaluable": all(row["pre_branch_metrics"] is not None for row in group),
            "valid_cut_count_per_union_map": sum(row["valid_cut_count_per_branch"] for row in group),
            "pre_union_hits": sum(row["pre_unique_union_hit_count"] for row in group),
            "post_union_hits": sum(row["post_unique_union_hit_count"] for row in group),
            "reference_union_hits": sum(row["reference_unique_union_hit_count"] for row in group),
            "pre_union_extra_hit_flips": sum(row["pre_union_hit_flips"]["extra_hits"] for row in group),
            "pre_union_missed_hit_flips": sum(row["pre_union_hit_flips"]["missed_hits"] for row in group),
            "post_union_extra_hit_flips": sum(row["post_union_hit_flips"]["extra_hits"] for row in group),
            "post_union_missed_hit_flips": sum(row["post_union_hit_flips"]["missed_hits"] for row in group),
            "pre_union_negative_thresholds": sum(row["pre_union_threshold_counts"]["negative"] for row in group),
            "pre_union_zero_thresholds": sum(row["pre_union_threshold_counts"]["zero"] for row in group),
            "pre_union_reference_zero_thresholds": sum(row["pre_union_threshold_counts"]["reference_zero"] for row in group),
            "post_union_negative_thresholds": sum(row["post_union_threshold_counts"]["negative"] for row in group),
            "post_union_zero_thresholds": sum(row["post_union_threshold_counts"]["zero"] for row in group),
            "post_union_reference_zero_thresholds": sum(row["post_union_threshold_counts"]["reference_zero"] for row in group),
            "branch_metrics": branch_totals,
        }
    return {
        "condition_period_records": len(rows),
        "valid_cut_count_per_branch_total": sum(row["valid_cut_count_per_branch"] * 2 for row in rows),
        "by_mode": by_mode,
    }


def analyze() -> dict[str, Any]:
    post_manifest = _read_json(POST_MANIFEST_PATH)
    pre_b_manifest = _read_json(PRE_B_MANIFEST_PATH)
    r6_history = _read_json(R6_NUMERIC_PATH)
    results: list[dict[str, Any]] = []
    pre_binary_ids: set[str] = set()
    post_binary_ids: set[str] = set()
    runtime_xml_ids: set[str] = set()

    for case_id in CASE_IDS:
        post_record = post_manifest["runs"][case_id]
        _check_execution_record(post_record, case_id, "post-fix")
        post_root = Path(post_record["output_dir"]) / "result/debug"
        post_geometry_rows = _read_csv(post_root / "cfar_geometry_diagnostics.csv")
        post_xml_sha = str(post_record["config_sha256"])
        post_binary_sha = str(post_record["binary_sha256"])
        post_binary_ids.add(post_binary_sha)
        runtime_xml_ids.add(post_xml_sha)

        if case_id.startswith("B_"):
            pre_record = pre_b_manifest["runs"][case_id]
            _check_execution_record(pre_record, case_id, "pre-fix B replay")
            pre_root = Path(pre_record["output_dir"]) / "result/debug"
            pre_geometry_rows = _read_csv(pre_root / "cfar_geometry_diagnostics.csv")
            pre_binary_sha = str(pre_record["binary_sha256"])
            if str(pre_record["config_sha256"]) != post_xml_sha:
                raise ValueError(f"{case_id}: pre/post B runtime XML differs")
            pre_branch_roots = None
        else:
            pre_case_root = _find_r6_production_case(case_id, post_xml_sha)
            pre_root = pre_case_root / "result/debug"
            pre_geometry_rows = _read_csv(pre_root / "cfar_geometry_diagnostics.csv")
            pre_xml = pre_case_root / "runtime.xml"
            if _sha256(pre_xml) != post_xml_sha:
                raise ValueError(f"{case_id}: pre/post A0/C Production runtime XML differs")
            pre_binary_sha = str(r6_history["r6_binary_sha256"])
            pre_branch_roots = {
                "dynamic_in_band": R6_ROOT / "cfar_branch_diagnostics" / case_id / "forced_ids/debug/dynamic/result/debug",
                "full_band": R6_ROOT / "cfar_branch_diagnostics" / case_id / "forced_ids/debug/full/result/debug",
            }

        pre_binary_ids.add(pre_binary_sha)
        for period_id in range(3):
            post_input = _period_input(post_record, period_id)
            input_sha = str(post_input["sha256"])
            if case_id.startswith("B_"):
                pre_input = _period_input(pre_record, period_id)
                if str(pre_input["sha256"]) != input_sha:
                    raise ValueError(f"{case_id} period {period_id}: pre/post input hashes differ")
                period_pre_sha = pre_binary_sha
            else:
                old_rows = _r6_history_rows(r6_history, case_id, period_id)
                if any(str(row["input_sha256"]) != input_sha for row in old_rows):
                    raise ValueError(f"{case_id} period {period_id}: R6 branch input hash differs from post input")
                if any(str(row["binary_sha256"]) != pre_binary_sha for row in old_rows):
                    raise ValueError(f"{case_id} period {period_id}: R6 pre-fix branch binary identity differs")
                result_ids = {int(row["result_file_id"]) for row in old_rows}
                if result_ids != {period_id + 1}:
                    raise ValueError(f"{case_id} period {period_id}: R6 result-file ID does not match period")
                period_pre_sha = pre_binary_sha

            results.append(
                _analyze_case_period(
                    case_id=case_id,
                    period_id=period_id,
                    pre_union_root=pre_root,
                    post_root=post_root,
                    pre_branch_roots=pre_branch_roots,
                    pre_geometry_rows=pre_geometry_rows,
                    post_geometry_rows=post_geometry_rows,
                    input_sha256=input_sha,
                    pre_binary_sha256=period_pre_sha,
                    post_binary_sha256=post_binary_sha,
                    runtime_xml_sha256=post_xml_sha,
                    result_id=period_id + 1,
                )
            )

    return {
        "analysis": "R7 three-period Production GO-CFAR pre/post numerical revalidation",
        "status": "EVALUABLE",
        "period_mapping": "runtime result_id 1,2,3 maps to period_id 0,1,2; verified against execution manifests and per-period geometry hit counts",
        "pre_fix_branch_map_coverage": {
            "A0_and_C": "branch-specific pre-fix maps available from R6 branch diagnostics; power arrays are required to match the paired post-fix branch maps bitwise",
            "B": "old Production run retains selected-union maps, not separate dynamic/full pre-fix branch maps; unique union and selected threshold remain evaluable",
        },
        "pre_fix_binary_sha256s": sorted(pre_binary_ids),
        "post_fix_binary_sha256s": sorted(post_binary_ids),
        "runtime_xml_sha256s": sorted(runtime_xml_ids),
        "summary": _summarize(results),
        "case_period_results": results,
        "interpretation_boundary": "One seed with three paired periods is a feasibility/correctness result, not an independent-seed statistical performance claim.",
    }


def _flatten_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for row in results:
        item: dict[str, Any] = {
            "case_id": row["case_id"],
            "mode": row["mode"],
            "period_id": row["period_id"],
            "result_id": row["result_id"],
            "input_sha256": row["input_sha256"],
            "pre_binary_sha256": row["pre_binary_sha256"],
            "post_binary_sha256": row["post_binary_sha256"],
            "runtime_xml_sha256": row["runtime_xml_sha256"],
            "valid_cut_count_per_branch": row["valid_cut_count_per_branch"],
            "pre_unique_union_hit_count": row["pre_unique_union_hit_count"],
            "reference_unique_union_hit_count": row["reference_unique_union_hit_count"],
            "post_unique_union_hit_count": row["post_unique_union_hit_count"],
            "pre_union_extra_hits": row["pre_union_hit_flips"]["extra_hits"],
            "pre_union_missed_hits": row["pre_union_hit_flips"]["missed_hits"],
            "post_union_extra_hits": row["post_union_hit_flips"]["extra_hits"],
            "post_union_missed_hits": row["post_union_hit_flips"]["missed_hits"],
            "pre_union_negative_thresholds": row["pre_union_threshold_counts"]["negative"],
            "pre_union_zero_thresholds": row["pre_union_threshold_counts"]["zero"],
            "pre_union_reference_zero_thresholds": row["pre_union_threshold_counts"]["reference_zero"],
            "pre_union_max_abs_threshold_error": row["pre_union_threshold_error"]["max_abs_error"],
            "post_union_max_abs_threshold_error": row["post_union_threshold_error"]["max_abs_error"],
            "pre_branch_maps_available": row["pre_branch_metrics"] is not None,
        }
        for quantile in ERROR_QUANTILES:
            item[f"pre_union_{quantile}_abs_threshold_error"] = row[
                "pre_union_threshold_error"
            ][f"{quantile}_abs_error"]
            item[f"post_union_{quantile}_abs_threshold_error"] = row[
                "post_union_threshold_error"
            ][f"{quantile}_abs_error"]
        for branch in BRANCHES:
            key = "dynamic" if branch == "dynamic_in_band" else "full"
            post_metrics = row["post_branch_metrics"][branch]
            item[f"post_{key}_extra_hits"] = post_metrics["hit_flips"]["extra_hits"]
            item[f"post_{key}_missed_hits"] = post_metrics["hit_flips"]["missed_hits"]
            item[f"post_{key}_max_abs_threshold_error"] = post_metrics["threshold_error"]["max_abs_error"]
            for quantile in ERROR_QUANTILES:
                item[f"post_{key}_{quantile}_abs_threshold_error"] = post_metrics[
                    "threshold_error"
                ][f"{quantile}_abs_error"]
            if row["pre_branch_metrics"] is None:
                item[f"pre_{key}_extra_hits"] = ""
                item[f"pre_{key}_missed_hits"] = ""
                item[f"pre_{key}_max_abs_threshold_error"] = ""
                for quantile in ERROR_QUANTILES:
                    item[f"pre_{key}_{quantile}_abs_threshold_error"] = ""
            else:
                pre_metrics = row["pre_branch_metrics"][branch]
                item[f"pre_{key}_extra_hits"] = pre_metrics["hit_flips"]["extra_hits"]
                item[f"pre_{key}_missed_hits"] = pre_metrics["hit_flips"]["missed_hits"]
                item[f"pre_{key}_max_abs_threshold_error"] = pre_metrics["threshold_error"]["max_abs_error"]
                for quantile in ERROR_QUANTILES:
                    item[f"pre_{key}_{quantile}_abs_threshold_error"] = pre_metrics[
                        "threshold_error"
                    ][f"{quantile}_abs_error"]
        flat.append(item)
    return flat


def _write_csv_rows(stream: TextIO, rows: list[dict[str, Any]]) -> None:
    writer = csv.DictWriter(
        stream, fieldnames=list(rows[0]), lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    if output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output directory: {output_dir}")

    result = analyze()
    output_dir.mkdir(parents=True)
    json_path = output_dir / "three_period_numerical_revalidation.json"
    csv_path = output_dir / "three_period_numerical_revalidation.csv"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    flat_rows = _flatten_rows(result["case_period_results"])
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        _write_csv_rows(stream, flat_rows)
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
