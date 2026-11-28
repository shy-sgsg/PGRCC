#!/usr/bin/env python3
"""Compare frozen R7 pre/post Production CFAR maps against float64 references."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r.go_cfar_reference import CfarGeometry, thresholds_integral_f64  # noqa: E402


R7 = ROOT / "outputs/phase_r7_cfar_correctness_20261022"
R6 = ROOT / "outputs/phase_r6_stagewise_divergence_20261022"
PRE_MANIFEST_PATH = R7 / "pre_fix_matrix/experiment_manifest.json"
POST_MANIFEST_PATH = R7 / "post_fix_matrix/experiment_manifest.json"
R6_CFAR_MANIFEST_PATH = R6 / "analysis/cfar_branch_metrics/analysis_manifest.json"
DEFAULT_OUTPUT = R7 / "numeric_analysis"
DEFAULT_R6_OUTPUT = R7 / "numeric_analysis_r6"
CASE_IDS = (
    "A0_OFF", "A0_ON", "A0_TO",
    "B_OFF", "B_ON", "B_TO",
    "C_OFF", "C_ON", "C_TO",
)
R6_CASES = ("A0_OFF", "A0_ON", "A0_TO", "C_OFF", "C_ON", "C_TO")


def union_reference_hit_mask(
    dynamic_power: np.ndarray,
    dynamic_threshold: np.ndarray,
    dynamic_valid: np.ndarray,
    full_power: np.ndarray,
    full_threshold: np.ndarray,
    full_valid: np.ndarray,
) -> np.ndarray:
    """Return the OR of the two branch decisions, preserving each branch mask."""
    values = (dynamic_power, dynamic_threshold, dynamic_valid,
              full_power, full_threshold, full_valid)
    shapes = {np.asarray(value).shape for value in values}
    if len(shapes) != 1:
        raise ValueError("both branch maps and masks must have the same shape")
    dynamic_hit = (dynamic_power > dynamic_threshold) & dynamic_valid
    full_hit = (full_power > full_threshold) & full_valid
    return dynamic_hit | full_hit


def summarize_nonpositive_thresholds(
    actual: np.ndarray, reference: np.ndarray, valid: np.ndarray
) -> dict[str, int]:
    """Count negative, zero, true-zero-reference, and erroneous nonpositive cuts."""
    actual_values = np.asarray(actual)
    reference_values = np.asarray(reference)
    valid_values = np.asarray(valid, dtype=bool)
    if actual_values.shape != reference_values.shape or actual_values.shape != valid_values.shape:
        raise ValueError("actual, reference, and valid masks must have the same shape")
    return {
        "negative": int(np.count_nonzero((actual_values < 0) & valid_values)),
        "zero": int(np.count_nonzero((actual_values == 0) & valid_values)),
        "reference_zero": int(np.count_nonzero((reference_values == 0) & valid_values)),
        "nonpositive_with_positive_reference": int(np.count_nonzero(
            (actual_values <= 0) & (reference_values > 0) & valid_values
        )),
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _read_map(path: Path, height: int, width: int) -> np.ndarray:
    values = np.fromfile(path, dtype="<f4")
    expected = height * width
    if values.size != expected:
        raise ValueError(f"{path}: expected {expected} float32 cells, got {values.size}")
    return values.reshape(height, width)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _branch_row(rows: list[dict[str, str]], branch: str) -> dict[str, str]:
    matches = [row for row in rows if branch in row["branch"]]
    if len(matches) != 1:
        raise ValueError(f"expected one {branch} geometry row, found {len(matches)}")
    return matches[0]


def _geometry_identity(row: dict[str, str]) -> tuple[Any, ...]:
    return (
        int(row["height"]), int(row["width"]), int(row["guard_cells"]),
        int(row["background_cells"]), row["doppler_circular"].lower(),
        int(row["exclude_row_start"]), int(row["exclude_row_end"]),
        int(row["cut_band_start"]), int(row["cut_band_end"]),
        int(row["cut_band_mode"]), row["cfar_type"], float(row["alpha"]),
    )


def _error_metrics(actual: np.ndarray, reference: np.ndarray, valid: np.ndarray) -> dict[str, float | int]:
    actual64 = np.asarray(actual, dtype=np.float64)[valid]
    reference64 = np.asarray(reference, dtype=np.float64)[valid]
    reference_f32 = np.asarray(reference64, dtype=np.float32)
    ulp_size = np.abs(np.spacing(reference_f32).astype(np.float64))
    difference = np.abs(actual64 - reference64)
    ulp_error = difference / np.where(ulp_size > 0.0, ulp_size, np.inf)
    return {
        "max_abs_error": float(np.max(difference)) if difference.size else 0.0,
        "p99_abs_error": float(np.quantile(difference, 0.99)) if difference.size else 0.0,
        "max_float32_ulp_error": float(np.max(ulp_error)) if ulp_error.size else 0.0,
        "cuts_over_2_float32_ulp": int(np.count_nonzero(ulp_error > 2.0)),
        "exact_float32_reference_cuts": int(np.count_nonzero(
            np.asarray(actual)[valid] == reference_f32
        )),
        "valid_cut_count": int(difference.size),
    }


def _load_pair_manifests() -> tuple[dict[str, Any], dict[str, Any]]:
    pre = json.loads(PRE_MANIFEST_PATH.read_text(encoding="utf-8"))
    post = json.loads(POST_MANIFEST_PATH.read_text(encoding="utf-8"))
    return pre, post


def analyze_case(case_id: str, pre_manifest: dict[str, Any], post_manifest: dict[str, Any]) -> dict[str, Any]:
    pre_record = pre_manifest["runs"][case_id]
    post_case_id = "A0_ON_repeat" if case_id == "A0_ON" else case_id
    post_record = post_manifest["runs"][post_case_id]
    if pre_record["status"] != "EXECUTED" or post_record["status"] != "EXECUTED":
        raise ValueError(f"{case_id}: both Production runs must be EXECUTED")
    if pre_record["exit_code"] != 0 or post_record["exit_code"] != 0:
        raise ValueError(f"{case_id}: a paired Production run did not exit cleanly")
    if pre_record["input_sha256"] != post_record["input_sha256"]:
        raise ValueError(f"{case_id}: pre/post Stage2 input hashes differ")
    if pre_record["config_sha256"] != post_record["config_sha256"]:
        raise ValueError(f"{case_id}: pre/post runtime XML hashes differ")

    pre_debug = Path(pre_record["output_dir"]) / "result/debug"
    post_debug = Path(post_record["output_dir"]) / "result/debug"
    pre_geometry_rows = _read_csv(pre_debug / "cfar_geometry_diagnostics.csv")
    post_geometry_rows = _read_csv(post_debug / "cfar_geometry_diagnostics.csv")
    pre_dynamic_row = _branch_row(pre_geometry_rows, "dynamic_in_band")
    pre_full_row = _branch_row(pre_geometry_rows, "full_band")
    post_dynamic_row = _branch_row(post_geometry_rows, "dynamic_in_band")
    post_full_row = _branch_row(post_geometry_rows, "full_band")
    for branch, old_row, new_row in (
        ("dynamic", pre_dynamic_row, post_dynamic_row),
        ("full", pre_full_row, post_full_row),
    ):
        if _geometry_identity(old_row) != _geometry_identity(new_row):
            raise ValueError(f"{case_id}: {branch} CFAR geometry/alpha changed pre to post")

    height, width = int(post_dynamic_row["height"]), int(post_dynamic_row["width"])
    dynamic_power = _read_map(post_debug / "cfar_GMTI01_beam050_dynamic_in_band_power.f32", height, width)
    full_power = _read_map(post_debug / "cfar_GMTI01_beam050_full_band_power.f32", height, width)
    dynamic_threshold = _read_map(post_debug / "cfar_GMTI01_beam050_dynamic_in_band_threshold.f32", height, width)
    full_threshold = _read_map(post_debug / "cfar_GMTI01_beam050_full_band_threshold.f32", height, width)
    dynamic_hits = _read_map(post_debug / "cfar_GMTI01_beam050_dynamic_in_band_hits.f32", height, width) > 0.0
    full_hits = _read_map(post_debug / "cfar_GMTI01_beam050_full_band_hits.f32", height, width) > 0.0

    dynamic_geometry = _geometry(post_dynamic_row)
    full_geometry = _geometry(post_full_row)
    alpha = float(post_dynamic_row["alpha"])
    dynamic_reference = thresholds_integral_f64(dynamic_power, dynamic_geometry, alpha)
    full_reference = thresholds_integral_f64(full_power, full_geometry, alpha)
    dynamic_reference_f32 = dynamic_reference.threshold.astype(np.float32)
    full_reference_f32 = full_reference.threshold.astype(np.float32)
    expected_dynamic_hits = (dynamic_power > dynamic_reference_f32) & dynamic_reference.valid_cut
    expected_full_hits = (full_power > full_reference_f32) & full_reference.valid_cut
    expected_union_hits = union_reference_hit_mask(
        dynamic_power, dynamic_reference_f32, dynamic_reference.valid_cut,
        full_power, full_reference_f32, full_reference.valid_cut,
    )

    pre_union_power = _read_map(pre_debug / "cfar_GMTI01_beam050_power.f32", height, width)
    post_union_power = _read_map(post_debug / "cfar_GMTI01_beam050_power.f32", height, width)
    pre_union_threshold = _read_map(pre_debug / "cfar_GMTI01_beam050_threshold.f32", height, width)
    post_union_threshold = _read_map(post_debug / "cfar_GMTI01_beam050_threshold.f32", height, width)
    pre_union_hits = _read_map(pre_debug / "cfar_GMTI01_beam050_hits.f32", height, width) > 0.0
    post_union_hits = _read_map(post_debug / "cfar_GMTI01_beam050_hits.f32", height, width) > 0.0
    union_valid = dynamic_reference.valid_cut | full_reference.valid_cut

    use_dynamic = dynamic_power >= full_power
    selected_reference_threshold64 = np.where(
        use_dynamic, dynamic_reference.threshold, full_reference.threshold
    )
    selected_reference_threshold = selected_reference_threshold64.astype(np.float32)
    selected_valid = np.where(
        use_dynamic, dynamic_reference.valid_cut, full_reference.valid_cut
    )
    pre_threshold_counts = summarize_nonpositive_thresholds(
        pre_union_threshold, selected_reference_threshold64, selected_valid
    )
    post_threshold_counts = summarize_nonpositive_thresholds(
        post_union_threshold, selected_reference_threshold64, selected_valid
    )
    pre_flip = pre_union_hits != expected_union_hits
    post_flip = post_union_hits != expected_union_hits
    old_false_hits = pre_union_hits & ~expected_union_hits & union_valid
    old_missed_hits = expected_union_hits & ~pre_union_hits & union_valid
    branch_hit_flips = int(np.count_nonzero(dynamic_hits != expected_dynamic_hits)) + int(
        np.count_nonzero(full_hits != expected_full_hits)
    )

    numerical_rows = _read_csv(post_debug / "cfar_numerical_diagnostics.csv")
    dynamic_fallback = _branch_row(numerical_rows, "dynamic_in_band")
    full_fallback = _branch_row(numerical_rows, "full_band")
    dynamic_threshold_metrics = _error_metrics(
        dynamic_threshold, dynamic_reference.threshold, dynamic_reference.valid_cut
    )
    full_threshold_metrics = _error_metrics(
        full_threshold, full_reference.threshold, full_reference.valid_cut
    )
    selected_pre_error = _error_metrics(
        pre_union_threshold, selected_reference_threshold64, selected_valid
    )
    selected_post_error = _error_metrics(
        post_union_threshold, selected_reference_threshold64, selected_valid
    )

    return {
        "case_id": case_id,
        "mode": case_id.split("_")[-1],
        "condition": case_id.rsplit("_", 1)[0],
        "input_sha256": pre_record["input_sha256"],
        "config_sha256": pre_record["config_sha256"],
        "pre_fix_binary_sha256": pre_record["binary_sha256"],
        "post_fix_binary_sha256": post_record["binary_sha256"],
        "valid_cut_count": int(union_valid.sum()),
        "union_power_pre_post_bitwise_equal": bool(np.array_equal(pre_union_power, post_union_power)),
        "post_union_power_is_branch_max": bool(np.array_equal(post_union_power, np.maximum(dynamic_power, full_power))),
        "post_union_threshold_matches_higher_power_branch": bool(
            np.array_equal(post_union_threshold[selected_valid], selected_reference_threshold[selected_valid])
        ),
        "post_union_hits_match_branch_or": bool(np.array_equal(
            post_union_hits[union_valid], (dynamic_hits | full_hits)[union_valid]
        )),
        "pre_threshold_nonpositive": pre_threshold_counts,
        "post_threshold_nonpositive": post_threshold_counts,
        "pre_union_hits": int(np.count_nonzero(pre_union_hits & union_valid)),
        "reference_union_hits": int(np.count_nonzero(expected_union_hits & union_valid)),
        "post_union_hits": int(np.count_nonzero(post_union_hits & union_valid)),
        "pre_union_hit_flips": int(np.count_nonzero(pre_flip & union_valid)),
        "pre_false_union_hits": int(np.count_nonzero(old_false_hits)),
        "pre_missed_union_hits": int(np.count_nonzero(old_missed_hits)),
        "post_union_hit_flips": int(np.count_nonzero(post_flip & union_valid)),
        "post_branch_hit_flips": branch_hit_flips,
        "pre_selected_threshold_error": selected_pre_error,
        "post_selected_threshold_error": selected_post_error,
        "post_dynamic_threshold_error": dynamic_threshold_metrics,
        "post_full_threshold_error": full_threshold_metrics,
        "cpu_direct_fallback_directions": {
            "dynamic": dynamic_reference.direct_fallback_count,
            "full": full_reference.direct_fallback_count,
        },
        "cuda_direct_fallback_directions": {
            "dynamic": int(dynamic_fallback["direct_fallback_direction_count"]),
            "full": int(full_fallback["direct_fallback_direction_count"]),
        },
    }


def analyze_all() -> dict[str, Any]:
    pre_manifest, post_manifest = _load_pair_manifests()
    results = [analyze_case(case_id, pre_manifest, post_manifest) for case_id in CASE_IDS]
    return {
        "schema_version": 1,
        "analysis": "R7 paired Production GO-CFAR numeric comparison",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_revision_pre_fix": pre_manifest["git_revision"],
        "git_revision_post_fix": post_manifest["git_revision"],
        "pre_fix_binary_sha256": pre_manifest["binary_sha256"],
        "post_fix_binary_sha256": post_manifest["binary_sha256"],
        "pre_fix_gpu_status": pre_manifest.get("gpu_status_before_runs"),
        "post_fix_gpu_status": post_manifest.get("gpu_status_before_matrix"),
        "paired_condition_results": results,
    }


def aggregate_legacy_r6_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, int | float]]:
    """Aggregate historical branch-map counts separately by ON/OFF/TO mode."""
    count_fields = (
        "negative_threshold_cuts", "zero_threshold_cuts",
        "reference_zero_threshold_cuts", "erroneous_nonpositive_cuts",
        "false_hit_flips", "missed_hit_flips", "actual_hit_count",
        "reference_hit_count", "valid_cut_count",
    )
    result: dict[str, dict[str, int | float]] = {}
    for row in rows:
        mode = str(row["mode"])
        summary = result.setdefault(
            mode,
            {"branch_map_count": 0, **{field: 0 for field in count_fields},
             "max_float32_ulp_error": 0.0},
        )
        summary["branch_map_count"] = int(summary["branch_map_count"]) + 1
        for field in count_fields:
            summary[field] = int(summary[field]) + int(row[field])
        summary["max_float32_ulp_error"] = max(
            float(summary["max_float32_ulp_error"]), float(row["max_float32_ulp_error"])
        )
    return result


def _r6_input_path(case_id: str, period_id: int) -> Path:
    mode = case_id.split("_")[-1]
    if case_id.startswith("A0_"):
        stage = "A0_ON" if mode == "ON" else f"A0_{mode}_bg_replay1"
        return R6 / "reconstruction/stages" / stage / "data" / (
            f"stage2_statistical_newprotocol_period_{period_id:04d}.bin"
        )
    return R6 / "reconstruction/corrected_C" / mode / "prepared_inputs" / (
        "A2_Known_error_correction_upper_bound" / Path(f"period_{period_id:04d}.bin")
    )


def analyze_legacy_r6() -> dict[str, Any]:
    """Recompute the saved R6 A0/C × ON/OFF/TO × period × branch maps."""
    manifest = json.loads(R6_CFAR_MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("branch_map_rows") != 36:
        raise ValueError("R6 manifest does not identify the expected 36 branch-period maps")
    if manifest.get("period_mapping") != "forced result IDs 1,2,3 map to period IDs 0,1,2":
        raise ValueError("R6 forced result-ID to period mapping is not the audited mapping")
    binary_hash = manifest["production_executable_sha256"]
    if binary_hash != "f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962":
        raise ValueError("R6 branch map executable differs from the audited legacy binary")

    rows: list[dict[str, Any]] = []
    for case_id in R6_CASES:
        for branch_dir, branch_name in (("dynamic", "dynamic_in_band"), ("full", "full_band")):
            debug_dir = R6 / "cfar_branch_diagnostics" / case_id / "forced_ids" / "debug" / branch_dir / "result/debug"
            geometry_rows = _read_csv(debug_dir / "cfar_geometry_diagnostics.csv")
            if len(geometry_rows) != 3:
                raise ValueError(f"{case_id}/{branch_dir}: expected three period geometry rows")
            by_period = {int(row["period_id"]): row for row in geometry_rows}
            if set(by_period) != {0, 1, 2}:
                raise ValueError(f"{case_id}/{branch_dir}: period IDs do not match 0,1,2")
            for period_id in (0, 1, 2):
                geometry_row = by_period[period_id]
                if branch_name not in geometry_row["branch"]:
                    raise ValueError(f"{case_id}/{branch_dir}/period{period_id}: branch mismatch")
                input_path = _r6_input_path(case_id, period_id)
                if not input_path.is_file() or input_path.stat().st_size == 0:
                    raise ValueError(f"Missing R6 historical Stage2 input: {input_path}")
                result_id = period_id + 1
                stem = f"cfar_GMTI{result_id:02d}_beam050"
                height, width = int(geometry_row["height"]), int(geometry_row["width"])
                power = _read_map(debug_dir / f"{stem}_power.f32", height, width)
                actual_threshold = _read_map(debug_dir / f"{stem}_threshold.f32", height, width)
                actual_hits = _read_map(debug_dir / f"{stem}_hits.f32", height, width) > 0.0
                reference = thresholds_integral_f64(
                    power, _geometry(geometry_row), float(geometry_row["alpha"])
                )
                reference_f32 = reference.threshold.astype(np.float32)
                expected_hits = (power > reference_f32) & reference.valid_cut
                valid = reference.valid_cut
                nonpositive = summarize_nonpositive_thresholds(
                    actual_threshold, reference.threshold, valid
                )
                error = _error_metrics(actual_threshold, reference.threshold, valid)
                false_hits = actual_hits & ~expected_hits & valid
                missed_hits = expected_hits & ~actual_hits & valid
                row = {
                    "case_id": case_id,
                    "condition": case_id.rsplit("_", 1)[0],
                    "mode": case_id.split("_")[-1],
                    "branch": branch_name,
                    "period_id": period_id,
                    "result_file_id": result_id,
                    "input_path": str(input_path),
                    "input_sha256": _sha256(input_path),
                    "binary_sha256": binary_hash,
                    "config_path": str(R6 / "cfar_branch_diagnostics" / case_id / "forced_ids" / "debug" / branch_dir / "runtime.xml"),
                    "config_sha256": _sha256(R6 / "cfar_branch_diagnostics" / case_id / "forced_ids" / "debug" / branch_dir / "runtime.xml"),
                    "alpha": float(geometry_row["alpha"]),
                    "valid_cut_count": int(valid.sum()),
                    "negative_threshold_cuts": nonpositive["negative"],
                    "zero_threshold_cuts": nonpositive["zero"],
                    "reference_zero_threshold_cuts": nonpositive["reference_zero"],
                    "erroneous_nonpositive_cuts": nonpositive["nonpositive_with_positive_reference"],
                    "actual_hit_count": int(np.count_nonzero(actual_hits & valid)),
                    "reference_hit_count": int(np.count_nonzero(expected_hits)),
                    "false_hit_flips": int(np.count_nonzero(false_hits)),
                    "missed_hit_flips": int(np.count_nonzero(missed_hits)),
                    "threshold_max_abs_error": error["max_abs_error"],
                    "max_float32_ulp_error": error["max_float32_ulp_error"],
                    "cuts_over_2_float32_ulp": error["cuts_over_2_float32_ulp"],
                    "cpu_direct_fallback_directions": reference.direct_fallback_count,
                }
                rows.append(row)
    if len(rows) != 36:
        raise ValueError(f"R6 numeric reanalysis produced {len(rows)} maps, expected 36")
    return {
        "schema_version": 1,
        "analysis": "R6 saved Production GO-CFAR maps versus independent float64 references",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "r6_manifest_path": str(R6_CFAR_MANIFEST_PATH),
        "r6_binary_sha256": binary_hash,
        "source_map_count": 36,
        "period_mapping": manifest["period_mapping"],
        "by_mode": aggregate_legacy_r6_rows(rows),
        "branch_period_maps": rows,
    }


def _write_outputs(output_dir: Path, summary: dict[str, Any]) -> None:
    if output_dir.exists():
        raise SystemExit(f"Refusing to overwrite existing numeric analysis directory: {output_dir}")
    output_dir.mkdir(parents=True)
    (output_dir / "numeric_comparison.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    columns = (
        "case_id", "condition", "mode", "valid_cut_count",
        "pre_threshold_negative", "pre_threshold_zero",
        "pre_nonpositive_with_positive_reference",
        "post_threshold_negative", "post_threshold_zero",
        "post_nonpositive_with_positive_reference", "reference_zero",
        "pre_union_hits", "reference_union_hits", "post_union_hits",
        "pre_union_hit_flips", "pre_false_union_hits", "pre_missed_union_hits",
        "post_union_hit_flips", "post_branch_hit_flips",
        "pre_threshold_max_abs_error", "post_threshold_max_abs_error",
        "post_threshold_max_ulp_error", "post_threshold_cuts_over_2_ulp",
        "cpu_fallback_dynamic", "cuda_fallback_dynamic",
        "cpu_fallback_full", "cuda_fallback_full",
    )
    with (output_dir / "numeric_comparison.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for result in summary["paired_condition_results"]:
            pre_counts = result["pre_threshold_nonpositive"]
            post_counts = result["post_threshold_nonpositive"]
            row = {
                "case_id": result["case_id"],
                "condition": result["condition"],
                "mode": result["mode"],
                "valid_cut_count": result["valid_cut_count"],
                "pre_threshold_negative": pre_counts["negative"],
                "pre_threshold_zero": pre_counts["zero"],
                "pre_nonpositive_with_positive_reference": pre_counts["nonpositive_with_positive_reference"],
                "post_threshold_negative": post_counts["negative"],
                "post_threshold_zero": post_counts["zero"],
                "post_nonpositive_with_positive_reference": post_counts["nonpositive_with_positive_reference"],
                "reference_zero": post_counts["reference_zero"],
                "pre_union_hits": result["pre_union_hits"],
                "reference_union_hits": result["reference_union_hits"],
                "post_union_hits": result["post_union_hits"],
                "pre_union_hit_flips": result["pre_union_hit_flips"],
                "pre_false_union_hits": result["pre_false_union_hits"],
                "pre_missed_union_hits": result["pre_missed_union_hits"],
                "post_union_hit_flips": result["post_union_hit_flips"],
                "post_branch_hit_flips": result["post_branch_hit_flips"],
                "pre_threshold_max_abs_error": result["pre_selected_threshold_error"]["max_abs_error"],
                "post_threshold_max_abs_error": result["post_selected_threshold_error"]["max_abs_error"],
                "post_threshold_max_ulp_error": result["post_selected_threshold_error"]["max_float32_ulp_error"],
                "post_threshold_cuts_over_2_ulp": result["post_selected_threshold_error"]["cuts_over_2_float32_ulp"],
                "cpu_fallback_dynamic": result["cpu_direct_fallback_directions"]["dynamic"],
                "cuda_fallback_dynamic": result["cuda_direct_fallback_directions"]["dynamic"],
                "cpu_fallback_full": result["cpu_direct_fallback_directions"]["full"],
                "cuda_fallback_full": result["cuda_direct_fallback_directions"]["full"],
            }
            writer.writerow(row)


def _write_legacy_r6_outputs(output_dir: Path, summary: dict[str, Any]) -> None:
    if output_dir.exists():
        raise SystemExit(f"Refusing to overwrite existing numeric analysis directory: {output_dir}")
    output_dir.mkdir(parents=True)
    (output_dir / "historical_r6_comparison.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    columns = (
        "case_id", "condition", "mode", "branch", "period_id", "result_file_id",
        "input_path", "input_sha256", "binary_sha256", "config_path", "config_sha256", "alpha",
        "valid_cut_count", "negative_threshold_cuts", "zero_threshold_cuts",
        "reference_zero_threshold_cuts", "erroneous_nonpositive_cuts",
        "actual_hit_count", "reference_hit_count", "false_hit_flips", "missed_hit_flips",
        "threshold_max_abs_error", "max_float32_ulp_error", "cuts_over_2_float32_ulp",
        "cpu_direct_fallback_directions",
    )
    with (output_dir / "historical_r6_branch_period_maps.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(summary["branch_period_maps"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--legacy-r6-only", action="store_true",
        help="reanalyze saved historical R6 branch maps without running the R7 paired analysis",
    )
    args = parser.parse_args(argv)
    default_output = DEFAULT_R6_OUTPUT if args.legacy_r6_only else DEFAULT_OUTPUT
    output_dir = (args.output_dir or default_output).resolve()
    if args.legacy_r6_only:
        summary = analyze_legacy_r6()
        _write_legacy_r6_outputs(output_dir, summary)
        for mode, result in sorted(summary["by_mode"].items()):
            print(
                f"R6 {mode}: maps={result['branch_map_count']} "
                f"false_hits={result['false_hit_flips']} "
                f"missed_hits={result['missed_hit_flips']} "
                f"erroneous_nonpositive={result['erroneous_nonpositive_cuts']}"
            )
        print(f"Wrote {output_dir / 'historical_r6_comparison.json'}")
    else:
        summary = analyze_all()
        _write_outputs(output_dir, summary)
        for result in summary["paired_condition_results"]:
            print(
                f"{result['case_id']}: valid={result['valid_cut_count']} "
                f"pre_flips={result['pre_union_hit_flips']} "
                f"post_flips={result['post_union_hit_flips']} "
                f"post_max_ulp={result['post_selected_threshold_error']['max_float32_ulp_error']}"
            )
        print(f"Wrote {output_dir / 'numeric_comparison.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
