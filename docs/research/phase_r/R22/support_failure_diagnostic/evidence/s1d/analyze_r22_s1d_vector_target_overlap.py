#!/usr/bin/env python3
"""Summarize S1d CSI phase-vector shifts and posthoc target-bin overlap."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import struct
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
GROUP_START = 1065
GROUP_END = 1197
BEAM_ID = 50
AZ_INDEX = 52
PERIOD_INDEX = 4
PHASE_TRACE_STAGE = "csi_post_range_phase_pre_c4_adapter"
PHASE_VECTOR_FILE = "paired_range_phase_csi_period050_beam050.f32"

BRANCHES = {
    "SNR20_self": "C4_modeb_S1d_native_on_snr20",
    "SNR20_cross_SNR30": "C4_modeb_S1d_input_snr20_cross_csi_snr30",
    "SNR20_OFF_vector": "C4_modeb_S1d_input_snr20_shared_off_csi",
    "SNR30_self": "C4_modeb_S1d_native_on_snr30",
    "SNR30_cross_SNR20": "C4_modeb_S1d_input_snr30_cross_csi_snr20",
    "SNR30_OFF_vector": "C4_modeb_S1d_input_snr30_shared_off_csi",
    "OFF_self": "C4_modea_S1d_native_target_free_OFF",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def wrap_phase_delta(first: float, second: float) -> float:
    return math.atan2(math.sin(first - second), math.cos(first - second))


def read_phase_vector(branch_name: str) -> tuple[list[float], Path]:
    path = ROOT / "branches" / branch_name / "result" / "debug" / PHASE_VECTOR_FILE
    data = path.read_bytes()
    if len(data) != 4096 * 4:
        raise ValueError(f"expected 4096 float32 phase values: {path}")
    values = list(struct.unpack("<4096f", data))
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"non-finite phase vector values: {path}")
    return values, path


def validate_trace(branch_name: str, vector: list[float]) -> float:
    path = ROOT / "branches" / branch_name / "result" / "production_phase_boundary_trace.csv"
    rows = [
        row
        for row in read_csv(path)
        if row["stage"] == PHASE_TRACE_STAGE
        and int(row["azimuth_index"]) == AZ_INDEX
        and GROUP_START <= int(row["range_index"]) <= GROUP_END
    ]
    by_range = {int(row["range_index"]): float(row["range_phase_rad"]) for row in rows}
    expected = set(range(GROUP_START, GROUP_END + 1))
    if set(by_range) != expected or len(rows) != len(expected):
        raise ValueError(f"incomplete CSI phase trace for {branch_name}")
    max_abs_error = max(abs(by_range[index] - vector[index]) for index in expected)
    if max_abs_error > 1e-6:
        raise ValueError(f"phase sidecar/Production trace mismatch for {branch_name}: {max_abs_error}")
    return max_abs_error


def phase_stats(values: list[float], threshold: float) -> dict[str, Any]:
    absolute = sorted(abs(value) for value in values)
    if not absolute:
        raise ValueError("phase statistics require at least one value")
    p90_index = math.ceil(0.9 * len(absolute)) - 1
    return {
        "n": len(absolute),
        "mean_abs_wrapped_rad": sum(absolute) / len(absolute),
        "median_abs_wrapped_rad": statistics.median(absolute),
        "p90_abs_wrapped_rad": absolute[p90_index],
        "max_abs_wrapped_rad": absolute[-1],
        "count_abs_vector_delta_over_0_35_rad": sum(value > threshold for value in absolute),
    }


def main() -> int:
    target_audit = ROOT.parent / "s1c" / "v2" / "s1c_target_overlap_audit.csv"
    target_rows = [
        row
        for row in read_csv(target_audit)
        if int(row["period_index_zero_based"]) == PERIOD_INDEX
        and int(row["beam_id"]) == BEAM_ID
        and GROUP_START <= int(row["range_bin"]) <= GROUP_END
    ]
    target_ids_by_bin: dict[int, set[str]] = {}
    for row in target_rows:
        target_ids_by_bin.setdefault(int(row["range_bin"]), set()).add(row["target_id"])

    selected_path = ROOT / "s1d_selected_group_cells.csv"
    selected_rows = read_csv(selected_path)
    cells_by_branch: dict[str, dict[int, dict[str, str]]] = {name: {} for name in BRANCHES.values()}
    for row in selected_rows:
        branch = row["branch_name"]
        if branch not in cells_by_branch:
            raise ValueError(f"unexpected branch in selected-cell table: {branch}")
        range_index = int(row["range_index"])
        if not GROUP_START <= range_index <= GROUP_END or range_index in cells_by_branch[branch]:
            raise ValueError(f"duplicate or out-of-group selected cell: {branch}/{range_index}")
        cells_by_branch[branch][range_index] = row

    expected_ranges = set(range(GROUP_START, GROUP_END + 1))
    for branch, cells in cells_by_branch.items():
        if set(cells) != expected_ranges:
            raise ValueError(f"selected-cell table incomplete for {branch}")

    vectors: dict[str, list[float]] = {}
    vector_paths: dict[str, Path] = {}
    trace_errors: dict[str, float] = {}
    native_vector_branches = {
        "SNR20_ON": BRANCHES["SNR20_self"],
        "SNR30_ON": BRANCHES["SNR30_self"],
        "OFF": BRANCHES["OFF_self"],
    }
    for key, branch in native_vector_branches.items():
        vectors[key], vector_paths[key] = read_phase_vector(branch)
    for branch in BRANCHES.values():
        vector, _ = read_phase_vector(branch)
        trace_errors[branch] = validate_trace(branch, vector)

    threshold = 0.35
    comparisons = [
        ("SNR20_ON", "SNR30_ON"),
        ("SNR20_ON", "OFF"),
        ("SNR30_ON", "OFF"),
    ]
    phase_comparisons: dict[str, Any] = {}
    for first, second in comparisons:
        values = {
            index: wrap_phase_delta(vectors[first][index], vectors[second][index])
            for index in expected_ranges
        }
        target_values = [values[index] for index in target_ids_by_bin]
        non_target_values = [values[index] for index in expected_ranges if index not in target_ids_by_bin]
        label = f"{first}_vs_{second}"
        phase_comparisons[label] = {
            "all_group_bins": phase_stats(list(values.values()), threshold),
            "truth_target_range_bins": phase_stats(target_values, threshold),
            "other_group_bins": phase_stats(non_target_values, threshold),
        }

    membership_summary = []
    for label, branch in BRANCHES.items():
        cells = cells_by_branch[branch]
        kept = {
            index
            for index, row in cells.items()
            if row["retained_by_frozen_gate"].strip().lower() == "true"
        }
        expected_support_values = {int(row["adapter_support_count"]) for row in cells.values()}
        if len(expected_support_values) != 1 or next(iter(expected_support_values)) != len(kept):
            raise ValueError(f"support count mismatch for {branch}")
        target_kept = kept & set(target_ids_by_bin)
        membership_summary.append({
            "condition": label,
            "branch_name": branch,
            "support_count": len(kept),
            "target_truth_bins_retained": len(target_kept),
            "target_truth_bins": sorted(target_kept),
            "non_target_support_count": len(kept - set(target_ids_by_bin)),
        })

    columns = [
        "range_index", "is_target_truth_bin", "target_ids",
        "snr20_on_csi_phase_rad", "snr30_on_csi_phase_rad", "paired_off_csi_phase_rad",
        "abs_wrapped_delta_snr20_snr30_rad", "abs_wrapped_delta_snr20_off_rad",
        "abs_wrapped_delta_snr30_off_rad",
        "support_snr20_self", "support_snr20_cross_snr30", "support_snr20_off_vector",
        "support_snr30_self", "support_snr30_cross_snr20", "support_snr30_off_vector",
        "support_off_self",
    ]
    branch_column_map = [
        ("support_snr20_self", BRANCHES["SNR20_self"]),
        ("support_snr20_cross_snr30", BRANCHES["SNR20_cross_SNR30"]),
        ("support_snr20_off_vector", BRANCHES["SNR20_OFF_vector"]),
        ("support_snr30_self", BRANCHES["SNR30_self"]),
        ("support_snr30_cross_snr20", BRANCHES["SNR30_cross_SNR20"]),
        ("support_snr30_off_vector", BRANCHES["SNR30_OFF_vector"]),
        ("support_off_self", BRANCHES["OFF_self"]),
    ]
    csv_path = ROOT / "s1d_phase_vector_target_overlap.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for index in sorted(expected_ranges):
            row: dict[str, Any] = {
                "range_index": index,
                "is_target_truth_bin": index in target_ids_by_bin,
                "target_ids": ";".join(sorted(target_ids_by_bin.get(index, set()))),
                "snr20_on_csi_phase_rad": vectors["SNR20_ON"][index],
                "snr30_on_csi_phase_rad": vectors["SNR30_ON"][index],
                "paired_off_csi_phase_rad": vectors["OFF"][index],
                "abs_wrapped_delta_snr20_snr30_rad": abs(wrap_phase_delta(vectors["SNR20_ON"][index], vectors["SNR30_ON"][index])),
                "abs_wrapped_delta_snr20_off_rad": abs(wrap_phase_delta(vectors["SNR20_ON"][index], vectors["OFF"][index])),
                "abs_wrapped_delta_snr30_off_rad": abs(wrap_phase_delta(vectors["SNR30_ON"][index], vectors["OFF"][index])),
            }
            for column, branch in branch_column_map:
                row[column] = cells_by_branch[branch][index]["retained_by_frozen_gate"].strip().lower() == "true"
            writer.writerow(row)

    source_hashes = {
        "selected_group_cells_csv": sha256(selected_path),
        "target_overlap_audit_csv": sha256(target_audit),
        "phase_vectors": {key: sha256(path) for key, path in vector_paths.items()},
    }
    summary = {
        "analysis": "S1d posthoc phase-vector shift and target-truth-bin overlap",
        "period_index_zero_based": PERIOD_INDEX,
        "production_period_label": "GMTI05",
        "selected_group": {"beam_id": BEAM_ID, "az_index": AZ_INDEX, "range_start": GROUP_START, "range_end": GROUP_END},
        "target_truth_bins": sorted(target_ids_by_bin),
        "target_truth_bin_count": len(target_ids_by_bin),
        "phase_delta_definition": "abs(atan2(sin(phi_a - phi_b), cos(phi_a - phi_b)))",
        "reference_threshold_rad_for_descriptive_count_only": threshold,
        "threshold_note": "the robust gate applies to each cell's residual from the group phase center, not to this pairwise vector-difference statistic",
        "phase_sidecar_trace_validation": {"branches": len(trace_errors), "max_abs_error_rad_by_branch": trace_errors},
        "phase_vector_comparisons": phase_comparisons,
        "support_target_overlap": membership_summary,
        "truth_use": "posthoc evaluator join only; not used by the estimator",
        "source_sha256": source_hashes,
        "output_csv": csv_path.name,
    }
    summary_path = ROOT / "s1d_phase_vector_target_overlap_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output_csv": csv_path.name,
        "summary_json": summary_path.name,
        "target_truth_bin_count": len(target_ids_by_bin),
        "phase_vector_comparisons": phase_comparisons,
        "support_target_overlap": membership_summary,
        "trace_validation_max_error": max(trace_errors.values()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
