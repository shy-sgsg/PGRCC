#!/usr/bin/env python3
"""Frozen Phase-R7 task metrics for Production detections and cluster lineage."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


RANGE_GATE_BINS = 2
DOPPLER_GATE_BINS = 2


def _get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    value = row.get(key, default)
    return default if value is None or value == "" else value


def _int(row: Mapping[str, Any], key: str, default: int | None = None) -> int | None:
    value = _get(row, key)
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _float(row: Mapping[str, Any], key: str, default: float | None = None) -> float | None:
    value = _get(row, key)
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def _flag(row: Mapping[str, Any], key: str) -> bool:
    value = _get(row, key, False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _key(row: Mapping[str, Any], *, lineage: bool) -> tuple[int, int, int, int]:
    period = _int(row, "period_id")
    beam = _int(row, "beam_id")
    row_field = "cluster_row" if lineage else "row"
    col_field = "cluster_col" if lineage else "col"
    row_index = _int(row, row_field)
    col_index = _int(row, col_field)
    if None in (period, beam, row_index, col_index):
        raise ValueError(f"lineage/detection row lacks period, beam, row, or column: {row}")
    return int(period), int(beam), int(row_index), int(col_index)


def join_cluster_lineage(
    lineage_rows: Sequence[Mapping[str, Any]],
    final_detections: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Join post-filter clusters to final detections only by exact physical indices."""

    clusters: list[dict[str, Any]] = []
    beam_summaries: list[dict[str, Any]] = []
    cluster_by_key: dict[tuple[int, int, int, int], dict[str, Any]] = {}
    for source in lineage_rows:
        row = dict(source)
        record_type = str(_get(row, "record_type", "cluster")).strip().lower()
        if record_type in {"beam_summary", "summary"}:
            beam_summaries.append(row)
            continue
        if record_type not in {"", "cluster"}:
            raise ValueError(f"unknown cluster lineage record_type {record_type!r}")
        key = _key(row, lineage=True)
        if key in cluster_by_key:
            raise ValueError(f"duplicate cluster lineage key {key}")
        row["period_id"] = key[0]
        row["beam_id"] = key[1]
        row["cluster_row"] = key[2]
        row["cluster_col"] = key[3]
        row["selected_flag"] = _flag(row, "selected")
        row["raw_detection_flag"] = _flag(row, "raw_detection")
        row["final_detection_ids"] = []
        cluster_by_key[key] = row
        clusters.append(row)

    unjoined_detection_ids: list[str] = []
    detection_by_key: dict[tuple[int, int, int, int], list[str]] = {}
    for index, source in enumerate(final_detections):
        detection = dict(source)
        key = _key(detection, lineage=False)
        det_id = str(_get(detection, "det_id", index))
        detection_by_key.setdefault(key, []).append(det_id)
        cluster = cluster_by_key.get(key)
        if cluster is None:
            unjoined_detection_ids.append(det_id)
        else:
            cluster["final_detection_ids"].append(det_id)

    for row in clusters:
        selected = bool(row["selected_flag"])
        raw = bool(row["raw_detection_flag"])
        has_final = bool(row["final_detection_ids"])
        if has_final and not (selected and raw):
            stage = "inconsistent_final_without_selected_raw"
        elif not selected:
            stage = "rejected_before_selection"
        elif not raw:
            stage = "selected_no_raw_detection"
        elif not has_final:
            stage = "raw_detection_removed_before_final"
        else:
            stage = "final_detection"
        row["lineage_stage"] = stage

    return {
        "clusters": clusters,
        "beam_summaries": beam_summaries,
        "detection_to_cluster": {
            det_id: cluster_by_key[key]
            for key, det_ids in detection_by_key.items()
            for det_id in det_ids
            if key in cluster_by_key
        },
        "unjoined_detection_ids": unjoined_detection_ids,
    }


def _round_half_away_from_zero(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0.0 else math.ceil(value - 0.5)


def expected_doppler_row(
    truth: Mapping[str, Any],
    detection: Mapping[str, Any],
    doppler_rows: int,
    doppler_axis: Mapping[str, Any] | None = None,
) -> int | None:
    """Map truth frequency onto the exact Production Doppler axis before wrapping."""

    if doppler_rows <= 0:
        return None
    total_hz = _float(truth, "af_total_truth_hz")
    if total_hz is None:
        return None
    if doppler_axis is not None:
        if (_get(doppler_axis, "doppler_axis_uniform") is not None
                and not _flag(doppler_axis, "doppler_axis_uniform")):
            return None
        first_hz = _float(doppler_axis, "doppler_axis_first_hz")
        spacing_hz = _float(doppler_axis, "doppler_bin_spacing_hz")
        row_count = _int(doppler_axis, "doppler_row_count")
        if (first_hz is None or spacing_hz is None or spacing_hz == 0.0
                or row_count != doppler_rows):
            return None
        return _round_half_away_from_zero((total_hz - first_hz) / spacing_hz) % row_count

    fd_center = _float(detection, "fd_ctr_unwrapped")
    prf_hz = _float(detection, "prf_hz")
    if fd_center is not None and prf_hz is not None and prf_hz > 0:
        fd_res = prf_hz / doppler_rows
        first = fd_center - 0.5 * prf_hz
        return _round_half_away_from_zero((total_hz - first) / fd_res) % doppler_rows
    return None


def _doppler_axis_map(
    lineage_rows: Sequence[Mapping[str, Any]] | None,
    explicit_rows: Sequence[Mapping[str, Any]],
    doppler_rows: int,
) -> dict[tuple[int, int], dict[str, Any]]:
    axes: dict[tuple[int, int], dict[str, Any]] = {}
    sources = list(lineage_rows or ()) + list(explicit_rows)
    for source in sources:
        row = dict(source)
        period, beam = _int(row, "period_id"), _int(row, "beam_id")
        if _get(row, "doppler_axis_uniform") is not None and not _flag(
            row, "doppler_axis_uniform",
        ):
            if period is None or beam is None:
                raise ValueError(f"nonuniform Doppler-axis metadata lacks period/beam: {row}")
            key = (period, beam)
            invalid_axis = {"doppler_axis_uniform": 0}
            if key in axes and axes[key] != invalid_axis:
                raise ValueError(f"conflicting Production Doppler-axis metadata for {key}")
            axes[key] = invalid_axis
            continue
        required = (
            "doppler_axis_first_hz",
            "doppler_bin_spacing_hz",
            "doppler_row_count",
        )
        values = [_get(row, key) for key in required]
        if all(value is None for value in values):
            continue
        if any(value is None for value in values):
            raise ValueError(f"incomplete Production Doppler-axis metadata: {row}")
        first_hz = _float(row, "doppler_axis_first_hz")
        spacing_hz = _float(row, "doppler_bin_spacing_hz")
        row_count = _int(row, "doppler_row_count")
        if (period is None or beam is None or first_hz is None or spacing_hz is None
                or spacing_hz == 0.0 or row_count is None or row_count <= 0
                or (doppler_rows > 0 and row_count != doppler_rows)):
            raise ValueError(f"invalid Production Doppler-axis metadata: {row}")
        key = (period, beam)
        axis = {
            "doppler_axis_first_hz": first_hz,
            "doppler_bin_spacing_hz": spacing_hz,
            "doppler_row_count": row_count,
            "doppler_axis_uniform": 1,
        }
        if key in axes and axes[key] != axis:
            raise ValueError(f"conflicting Production Doppler-axis metadata for {key}")
        axes[key] = axis
    return axes


def circular_doppler_distance(lhs: int, rhs: int, row_count: int) -> int:
    if row_count <= 0:
        return abs(lhs - rhs)
    difference = abs((lhs % row_count) - (rhs % row_count))
    return min(difference, row_count - difference)


def _hungarian(costs: Sequence[Sequence[float]]) -> list[int]:
    """Minimum-cost column assignment for each row; rows must not exceed columns."""

    n = len(costs)
    if n == 0:
        return []
    m = len(costs[0])
    if m < n or any(len(row) != m for row in costs):
        raise ValueError("assignment cost matrix must be rectangular with rows <= columns")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [math.inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = math.inf
            j1 = 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                current = costs[i0 - 1][j - 1] - u[i0] - v[j]
                if current < minv[j]:
                    minv[j] = current
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assignment = [-1] * n
    for j in range(1, m + 1):
        if p[j] != 0:
            assignment[p[j] - 1] = j - 1
    return assignment


def _identity(row: Mapping[str, Any], fallback: int = 0) -> tuple[int, int, str]:
    return (
        int(_int(row, "period_id", -1)),
        int(_int(row, "beam_id", -1)),
        str(_get(row, "target_id", _get(row, "det_id", fallback))),
    )


def _cluster_summary(
    lineage_rows: Sequence[Mapping[str, Any]] | None,
    cfar_rows: Sequence[Mapping[str, Any]],
    final_detections: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if lineage_rows is None:
        return {
            "status": "NOT_AVAILABLE",
            "cluster_count_source": None,
            "postfilter_clusters": None,
            "selected_candidates": None,
            "raw_detections": None,
            "final_detections_joined": None,
            "unjoined_final_detections": None,
            "rejected_before_selection": None,
            "selected_without_raw_detection": None,
            "raw_removed_before_final": None,
        }

    joined = join_cluster_lineage(lineage_rows, final_detections)
    clusters = joined["clusters"]
    summaries: dict[tuple[int, int], dict[str, int]] = {}
    for summary in joined["beam_summaries"]:
        period = _int(summary, "period_id")
        beam = _int(summary, "beam_id")
        if period is None or beam is None:
            raise ValueError(f"beam lineage summary lacks period/beam: {summary}")
        key = (period, beam)
        if key in summaries:
            raise ValueError(f"duplicate beam lineage summary {key}")
        summaries[key] = {
            "postfilter_clusters": _int(summary, "postfilter_cluster_count", 0) or 0,
            "selected_candidates": _int(summary, "selected_candidate_count", 0) or 0,
            "raw_detections": _int(summary, "raw_detection_count", 0) or 0,
        }
    actual_by_beam: dict[tuple[int, int], dict[str, int]] = {}
    for row in clusters:
        key = (int(row["period_id"]), int(row["beam_id"]))
        counts = actual_by_beam.setdefault(key, {
            "postfilter_clusters": 0, "selected_candidates": 0, "raw_detections": 0,
        })
        counts["postfilter_clusters"] += 1
        counts["selected_candidates"] += int(bool(row["selected_flag"]))
        counts["raw_detections"] += int(bool(row["raw_detection_flag"]))
    summary_mismatches = [
        {"period_id": key[0], "beam_id": key[1], "declared": declared,
         "observed": actual_by_beam.get(key, {"postfilter_clusters": 0,
                                               "selected_candidates": 0,
                                               "raw_detections": 0})}
        for key, declared in summaries.items()
        if declared != actual_by_beam.get(key, {"postfilter_clusters": 0,
                                                "selected_candidates": 0,
                                                "raw_detections": 0})
    ]
    for key, actual in actual_by_beam.items():
        if key not in summaries:
            summary_mismatches.append({
                "period_id": key[0], "beam_id": key[1],
                "declared": None, "observed": actual,
            })
    joined_count = sum(bool(row["final_detection_ids"]) for row in clusters)
    complete = not joined["unjoined_detection_ids"] and all(
        row["lineage_stage"] != "inconsistent_final_without_selected_raw" for row in clusters
    ) and not summary_mismatches
    totals = {
        metric: sum(row[metric] for row in summaries.values()) if summaries else sum(
            counts[metric] for counts in actual_by_beam.values()
        )
        for metric in ("postfilter_clusters", "selected_candidates", "raw_detections")
    }
    return {
        "status": "complete" if complete else "INCOMPLETE",
        "cluster_count_source": "cluster_lineage",
        "beam_summary_count": len(summaries),
        "summary_mismatches": summary_mismatches,
        **totals,
        "final_detections_joined": joined_count,
        "unjoined_final_detections": len(joined["unjoined_detection_ids"]),
        "rejected_before_selection": sum(row["lineage_stage"] == "rejected_before_selection" for row in clusters),
        "selected_without_raw_detection": sum(row["lineage_stage"] == "selected_no_raw_detection" for row in clusters),
        "raw_removed_before_final": sum(row["lineage_stage"] == "raw_detection_removed_before_final" for row in clusters),
        "lineage_evidence": clusters,
        "unjoined_detection_ids": joined["unjoined_detection_ids"],
    }


def _cfar_counts(cfar_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    branch_hits: dict[tuple[int, int, str], int] = {}
    unique_union_values: list[int] = []
    for row in cfar_rows:
        period = _int(row, "period_id")
        beam = _int(row, "beam_id")
        if period is None or beam is None:
            continue
        branch = str(_get(row, "branch", "unknown"))
        hits = _int(row, "hit_cut_count")
        if hits is not None:
            key = (period, beam, branch)
            if key in branch_hits and branch_hits[key] != hits:
                raise ValueError(f"conflicting CFAR hit counts for {key}")
            branch_hits[key] = hits
        union_count = _int(row, "unique_union_hit_cells")
        if union_count is not None:
            unique_union_values.append(union_count)
    return {
        "cfar_branch_hit_cells": sum(branch_hits.values()),
        "unique_union_hit_cells": (
            {"status": "evaluable", "count": max(unique_union_values)}
            if unique_union_values
            else {"status": "NOT_EVALUABLE", "count": None}
        ),
        "status": "evaluable" if cfar_rows else "NOT_EVALUABLE",
    }


def _track_counts(
    track_associations: Sequence[Mapping[str, Any]],
    track_frames: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    frame_tracks = sum(_int(row, "num_tracks", 0) or 0 for row in track_frames)
    frame_confirmed = sum(_int(row, "num_confirmed", 0) or 0 for row in track_frames)
    confirmed_ids = {
        str(_get(row, "track_id"))
        for row in track_associations
        if _flag(row, "confirmed") and _flag(row, "matched_this_frame")
        and _get(row, "track_id") is not None
    }
    return {
        "tracks": frame_tracks if track_frames else len(confirmed_ids),
        "confirmed_tracks": frame_confirmed if track_frames else len(confirmed_ids),
        "confirmed_track_ids": confirmed_ids,
        "status": "evaluable" if track_frames or track_associations else "NOT_EVALUABLE",
    }


def _result_number(value: Any) -> int | None:
    text = str(value or "").strip()
    digits = "".join(character for character in text if character.isdigit())
    return int(digits) if digits else None


def track_associations_from_debug(
    *,
    track_detections: Sequence[Mapping[str, Any]],
    track_payloads: Sequence[Mapping[str, Any]],
    final_detections: Sequence[Mapping[str, Any]],
    result_to_period: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Keep only confirmed output tracks matched to a current Production detection."""

    period_by_result: dict[int, int] = {}
    for result_id, period in result_to_period.items():
        number = _result_number(result_id)
        if number is not None:
            period_by_result[number] = int(period)

    confirmed_matches: set[tuple[int, int, str]] = set()
    for row in track_detections:
        result_number = _result_number(_get(row, "result_id"))
        det_index = _int(row, "det_index")
        if result_number is None or det_index is None or not _flag(row, "matched"):
            continue
        track_id = _get(row, "matched_track_id")
        if track_id is not None:
            confirmed_matches.add((result_number, det_index, str(track_id)))

    detection_by_result_and_index: dict[tuple[int, int], Mapping[str, Any]] = {}
    for index, row in enumerate(final_detections):
        result_number = _result_number(_get(row, "result_id"))
        det_index = _int(row, "det_id", index)
        if result_number is None or det_index is None:
            continue
        detection_by_result_and_index[(result_number, det_index)] = row

    associations: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for payload in track_payloads:
        result_number = _result_number(_get(payload, "result_id"))
        det_index = _int(payload, "matched_det_index")
        track_id = _get(payload, "track_id")
        if result_number is None or det_index is None or track_id is None:
            continue
        match_key = (result_number, det_index, str(track_id))
        if match_key not in confirmed_matches or match_key in seen:
            continue
        period = period_by_result.get(result_number)
        detection = detection_by_result_and_index.get((result_number, det_index))
        if period is None or detection is None:
            continue
        if _int(detection, "period_id") != period:
            continue
        beam = _int(detection, "beam_id")
        if beam is None:
            continue
        seen.add(match_key)
        association: dict[str, Any] = {
            "period_id": period,
            "beam_id": beam,
            "det_id": str(det_index),
            "track_id": str(track_id),
            "confirmed": 1,
            "matched_this_frame": 1,
        }
        speed = _float(payload, "output_speed")
        if speed is not None:
            association["output_speed"] = speed
        associations.append(association)
    return sorted(associations, key=lambda row: (row["period_id"], row["beam_id"], int(row["det_id"]), row["track_id"]))


def _pair_cost(
    truth: Mapping[str, Any],
    detection: Mapping[str, Any],
    doppler_rows: int,
    doppler_axes: Mapping[tuple[int, int], Mapping[str, Any]],
) -> tuple[bool, int | None, int | None, int | None, float | None, str]:
    truth_period, truth_beam = _int(truth, "period_id"), _int(truth, "beam_id")
    det_period, det_beam = _int(detection, "period_id"), _int(detection, "beam_id")
    if truth_period != det_period:
        return False, None, None, None, None, "period_mismatch"
    if truth_beam != det_beam:
        return False, None, None, None, None, "beam_mismatch"
    truth_range, detection_range = _int(truth, "range_bin"), _int(detection, "range_bin")
    detection_row = _int(detection, "row")
    axis = doppler_axes.get((int(truth_period), int(truth_beam)))
    expected_row = expected_doppler_row(truth, detection, doppler_rows, axis)
    if None in (truth_range, detection_range, detection_row, expected_row):
        return False, None, expected_row, None, None, "missing_bin_or_doppler_axis"
    range_delta = abs(int(detection_range) - int(truth_range))
    doppler_delta = circular_doppler_distance(int(detection_row), int(expected_row), doppler_rows)
    if range_delta > RANGE_GATE_BINS:
        return False, range_delta, expected_row, doppler_delta, None, "range_gate"
    if doppler_delta > DOPPLER_GATE_BINS:
        return False, range_delta, expected_row, doppler_delta, None, "doppler_gate"
    cost = math.hypot(range_delta / RANGE_GATE_BINS, doppler_delta / DOPPLER_GATE_BINS)
    return True, range_delta, expected_row, doppler_delta, cost, "candidate"


def _angle_error(estimate: float, truth: float) -> float:
    delta = (estimate - truth + 180.0) % 360.0 - 180.0
    return delta


def _bearing_deg(
    east: float | None,
    north: float | None,
    platform_east: float | None,
    platform_north: float | None,
) -> float | None:
    if None in (east, north, platform_east, platform_north):
        return None
    delta_east = float(east) - float(platform_east)
    delta_north = float(north) - float(platform_north)
    if math.hypot(delta_east, delta_north) <= 1.0e-9:
        return None
    return math.degrees(math.atan2(delta_north, delta_east))


def _rmse(values: Sequence[float]) -> float | None:
    return math.sqrt(sum(value * value for value in values) / len(values)) if values else None


def _on_metrics(
    truths: Sequence[Mapping[str, Any]],
    detections: Sequence[Mapping[str, Any]],
    track_associations: Sequence[Mapping[str, Any]],
    doppler_rows: int,
    doppler_axes: Mapping[tuple[int, int], Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ordered_truths = sorted(
        (dict(row) for row in truths if _flag(row, "visible") or _get(row, "visible") is None),
        key=lambda row: (_int(row, "period_id", -1), _int(row, "beam_id", -1), str(_get(row, "target_id", ""))),
    )
    ordered_detections = sorted(
        (dict(row) for row in detections),
        key=lambda row: (_int(row, "period_id", -1), _int(row, "beam_id", -1),
                         _int(row, "det_id", 0) or 0, _int(row, "row", -1), _int(row, "col", -1)),
    )
    edge_by_pair: dict[tuple[int, int], tuple[int | None, int | None, int | None, float | None, str]] = {}
    pair_audit: list[dict[str, Any]] = []
    groups: dict[tuple[int, int], tuple[list[int], list[int]]] = {}
    for ti, truth in enumerate(ordered_truths):
        period = _int(truth, "period_id", -1)
        beam = _int(truth, "beam_id", -1)
        group = (int(period if period is not None else -1), int(beam if beam is not None else -1))
        groups.setdefault(group, ([], []))[0].append(ti)
    for di, detection in enumerate(ordered_detections):
        period = _int(detection, "period_id", -1)
        beam = _int(detection, "beam_id", -1)
        group = (int(period if period is not None else -1), int(beam if beam is not None else -1))
        groups.setdefault(group, ([], []))[1].append(di)

    assignments: dict[int, int] = {}
    for (period, beam), (truth_indices, detection_indices) in groups.items():
        if not truth_indices:
            continue
        matrix: list[list[float]] = []
        valid_costs: dict[tuple[int, int], float] = {}
        for local_t, ti in enumerate(truth_indices):
            truth = ordered_truths[ti]
            costs = []
            for local_d, di in enumerate(detection_indices):
                detection = ordered_detections[di]
                eligible, dr, expected_row, dd, cost, reason = _pair_cost(
                    truth, detection, doppler_rows, doppler_axes,
                )
                detection_index = _int(detection, "det_id", di)
                pair_audit.append({
                    "period_id": period,
                    "beam_id": beam,
                    "target_id": _get(truth, "target_id", ti),
                    "det_id": detection_index,
                    "range_delta_bins": dr,
                    "truth_row": expected_row,
                    "detection_row": _int(detection, "row"),
                    "doppler_delta_bins": dd,
                    "normalized_residual": cost,
                    "eligible": int(eligible),
                    "gate_result": reason,
                    "candidate_source": "final_production_detection",
                })
                edge_by_pair[(ti, di)] = (dr, expected_row, dd, cost, reason)
                if eligible:
                    valid_costs[(local_t, local_d)] = float(cost)
                    costs.append(float(cost))
                else:
                    costs.append(1000.0)
            # A valid normalized Euclidean residual is at most sqrt(2); dummy
            # cost 2 first maximizes association cardinality, then minimizes
            # the sum of residuals.
            costs.extend([2.0] * len(truth_indices))
            matrix.append(costs)
        for local_t, assigned_col in enumerate(_hungarian(matrix)):
            if assigned_col < len(detection_indices) and (local_t, assigned_col) in valid_costs:
                assignments[truth_indices[local_t]] = detection_indices[assigned_col]

    tracks_by_detection: dict[tuple[int, int, str], list[Mapping[str, Any]]] = {}
    for track in track_associations:
        if not (_flag(track, "confirmed") and _flag(track, "matched_this_frame")):
            continue
        key = (
            int(_int(track, "period_id", -1)),
            int(_int(track, "beam_id", -1)),
            str(_get(track, "det_id", "")),
        )
        tracks_by_detection.setdefault(key, []).append(track)

    association_evidence: list[dict[str, Any]] = []
    matched_detection_indices: set[int] = set()
    associated_track_keys: set[tuple[int, str]] = set()
    position_errors: list[float] = []
    angle_errors: list[float] = []
    speed_errors: list[float] = []
    radial_errors: list[float] = []
    radial_unresolved = 0
    correct_tracks = 0
    for ti, truth in enumerate(ordered_truths):
        di = assignments.get(ti)
        row: dict[str, Any] = {
            "period_id": _int(truth, "period_id"),
            "beam_id": _int(truth, "beam_id"),
            "target_id": _get(truth, "target_id", ti),
            "matched": int(di is not None),
            "det_id": None,
            "range_delta_bins": None,
            "truth_row": None,
            "detection_row": None,
            "doppler_delta_bins": None,
            "normalized_residual": None,
            "track_id": None,
            "position_error_m": None,
            "angle_error_deg": None,
            "ground_speed_error_mps": None,
            "radial_velocity_error_mps": None,
            "radial_velocity_status": "NOT_EVALUABLE",
            "candidate_source": "final_production_detection",
        }
        if di is not None:
            detection = ordered_detections[di]
            matched_detection_indices.add(di)
            dr, expected_row, dd, cost, _ = edge_by_pair[(ti, di)]
            row.update({
                "det_id": _get(detection, "det_id", di),
                "range_delta_bins": dr,
                "truth_row": expected_row,
                "detection_row": _int(detection, "row"),
                "doppler_delta_bins": dd,
                "normalized_residual": cost,
            })
            track_key = (
                int(_int(truth, "period_id", -1)),
                int(_int(truth, "beam_id", -1)),
                str(_get(detection, "det_id", di)),
            )
            linked_tracks = tracks_by_detection.get(track_key, [])
            if len(linked_tracks) == 1:
                track = linked_tracks[0]
                track_id = str(_get(track, "track_id", ""))
                row["track_id"] = track_id
                associated_track_keys.add((track_key[0], track_id))
                correct_tracks += 1
                truth_speed = _float(truth, "ve_mps")
                truth_vn = _float(truth, "vn_mps")
                output_speed = _float(track, "output_speed")
                if truth_speed is not None and truth_vn is not None and output_speed is not None:
                    speed_error = output_speed - math.hypot(truth_speed, truth_vn)
                    speed_errors.append(speed_error)
                    row["ground_speed_error_mps"] = speed_error
            elif len(linked_tracks) > 1:
                row["track_association_status"] = "ambiguous_multiple_tracks_for_detection"

            east, north = _float(detection, "new_e"), _float(detection, "new_n")
            truth_e, truth_n = _float(truth, "e_mid"), _float(truth, "n_mid")
            if None not in (east, north, truth_e, truth_n):
                position_error = math.hypot(east - truth_e, north - truth_n)
                position_errors.append(position_error)
                row["position_error_m"] = position_error
            platform_e = _float(detection, "platform_e")
            platform_n = _float(detection, "platform_n")
            estimated_angle = _bearing_deg(
                east, north, platform_e, platform_n,
            )
            truth_angle = _bearing_deg(
                truth_e, truth_n, platform_e, platform_n,
            )
            if estimated_angle is not None and truth_angle is not None:
                angle_error = _angle_error(estimated_angle, truth_angle)
                angle_errors.append(angle_error)
                row["angle_error_deg"] = angle_error
            status = str(_get(detection, "ambiguity_status", "")).strip().lower()
            radial, truth_radial = _float(detection, "radial_velocity_mps"), _float(truth, "vr_self_mps")
            if status in {"unresolved", "ambiguous", "low_confidence"} or radial is None or truth_radial is None:
                radial_unresolved += 1
                row["radial_velocity_status"] = "unresolved"
            else:
                radial_error = radial - truth_radial
                radial_errors.append(radial_error)
                row["radial_velocity_error_mps"] = radial_error
                row["radial_velocity_status"] = "evaluable"
        else:
            row["miss_reason"] = "no_one_to_one_production_detection_within_frozen_gate"
        association_evidence.append(row)

    all_eligible_track_keys = {
        (int(_int(track, "period_id", -1)), str(_get(track, "track_id", "")))
        for track in track_associations
        if _flag(track, "confirmed") and _flag(track, "matched_this_frame")
    }
    false_tracks = all_eligible_track_keys - associated_track_keys
    radial_status = "evaluable" if radial_errors else "NOT_EVALUABLE"
    parameter_errors = {
        "position_2d_rmse_m": _rmse(position_errors),
        "position_error_count": len(position_errors),
        "angle_rmse_deg": _rmse(angle_errors),
        "angle_error_count": len(angle_errors),
        "ground_speed_rmse_mps": _rmse(speed_errors),
        "ground_speed_error_count": len(speed_errors),
        "radial_velocity": {
            "status": radial_status,
            "rmse_mps": _rmse(radial_errors),
            "count": len(radial_errors),
            "unresolved_count": radial_unresolved,
        },
    }
    metrics = {
        "truth_opportunities": len(ordered_truths),
        "unique_correct_detections": len(assignments),
        "detection_rate": len(assignments) / len(ordered_truths) if ordered_truths else None,
        "false_associations": len(ordered_detections) - len(matched_detection_indices),
        "misses": len(ordered_truths) - len(assignments),
        "correct_track_associations": correct_tracks,
        "false_track_associations": len(false_tracks),
        "track_association_status": "evaluable" if track_associations else "NOT_EVALUABLE",
        "parameter_errors": parameter_errors,
    }
    return metrics, association_evidence, pair_audit


def summarize_target_only_response(
    before: Sequence[complex], after: Sequence[complex]
) -> dict[str, Any]:
    """Measure complex transfer on caller-selected, truth-centered TO samples."""

    if len(before) != len(after) or len(before) == 0:
        return {
            "status": "NOT_EVALUABLE",
            "reason": "empty_or_mismatched_target_support",
            "sample_count": min(len(before), len(after)),
            "observable": False,
            "input_energy": None,
            "output_energy": None,
            "energy_ratio": None,
            "amplitude_ratio": None,
            "phase_error_rad": None,
        }
    input_energy = float(sum(abs(value) ** 2 for value in before))
    output_energy = float(sum(abs(value) ** 2 for value in after))
    if not math.isfinite(input_energy) or input_energy <= 0.0:
        return {
            "status": "NOT_EVALUABLE",
            "reason": "zero_or_nonfinite_target_reference_energy",
            "sample_count": len(before),
            "observable": False,
            "input_energy": input_energy if math.isfinite(input_energy) else None,
            "output_energy": output_energy if math.isfinite(output_energy) else None,
            "energy_ratio": None,
            "amplitude_ratio": None,
            "phase_error_rad": None,
        }
    transfer = sum(value_after * value_before.conjugate() for value_before, value_after in zip(before, after)) / input_energy
    amplitude = abs(transfer)
    return {
        "status": "evaluable",
        "reason": "truth_centered_target_only_complex_transfer",
        "sample_count": len(before),
        "observable": output_energy > 0.0 and math.isfinite(output_energy),
        "input_energy": input_energy,
        "output_energy": output_energy,
        "energy_ratio": output_energy / input_energy,
        "amplitude_ratio": amplitude,
        "phase_error_rad": math.atan2(transfer.imag, transfer.real),
    }


def evaluate_case(
    *,
    mode: str,
    truths: Sequence[Mapping[str, Any]] = (),
    final_detections: Sequence[Mapping[str, Any]] = (),
    lineage_rows: Sequence[Mapping[str, Any]] | None = None,
    track_associations: Sequence[Mapping[str, Any]] = (),
    track_frames: Sequence[Mapping[str, Any]] = (),
    cfar_rows: Sequence[Mapping[str, Any]] = (),
    doppler_axis_rows: Sequence[Mapping[str, Any]] = (),
    doppler_rows: int = 0,
    target_only_response: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    mode = mode.upper()
    if mode not in {"ON", "OFF", "TO"}:
        raise ValueError(f"mode must be ON, OFF, or TO; got {mode!r}")
    detections = [dict(row) for row in final_detections]
    cfar = _cfar_counts(cfar_rows)
    track_counts = _track_counts(track_associations, track_frames)
    lineage = _cluster_summary(lineage_rows, cfar_rows, detections)
    doppler_axes = _doppler_axis_map(lineage_rows, doppler_axis_rows, doppler_rows)
    metrics: dict[str, Any]
    association_evidence: list[dict[str, Any]] = []
    candidate_pair_evidence: list[dict[str, Any]] = []

    if mode == "ON":
        candidate_rows = detections
        if lineage_rows is not None:
            by_key = {
                _key(row, lineage=True): row
                for row in lineage.get("lineage_evidence", [])
            }
            candidate_rows = []
            for detection in detections:
                cluster = by_key.get(_key(detection, lineage=False))
                if cluster is not None and cluster["selected_flag"] and cluster["raw_detection_flag"]:
                    candidate_rows.append(detection)
        metrics, association_evidence, candidate_pair_evidence = _on_metrics(
            truths, candidate_rows, track_associations, doppler_rows, doppler_axes,
        )
        metrics["association_status"] = (
            "NOT_EVALUABLE_NO_VISIBLE_TRUTH_OPPORTUNITIES" if metrics["truth_opportunities"] == 0
            else "evaluable" if lineage_rows is None or lineage["status"] == "complete"
            else "NOT_EVALUABLE_INCOMPLETE_CLUSTER_LINEAGE"
        )
        metrics["candidate_final_detections"] = len(candidate_rows)
        metrics["cfar_branch_hit_cells"] = cfar["cfar_branch_hit_cells"]
        metrics["unique_union_hit_cells"] = cfar["unique_union_hit_cells"]
        metrics["cfar_status"] = cfar["status"]
        metrics["postfilter_clusters"] = lineage.get("postfilter_clusters")
        metrics["selected_candidates"] = lineage.get("selected_candidates")
        metrics["raw_detections"] = lineage.get("raw_detections")
        metrics["final_detections"] = len(detections)
        metrics["tracks"] = track_counts["tracks"]
        metrics["confirmed_tracks"] = track_counts["confirmed_tracks"]
        metrics["track_status"] = track_counts["status"]
    elif mode == "OFF":
        lineage_complete = lineage["status"] == "complete"
        cluster_count = lineage.get("postfilter_clusters") if lineage_complete else None
        cluster_status = (
            "evaluable" if lineage_complete
            else "NOT_EVALUABLE_LEGACY_PRE_CLUSTER_SUMMARY" if lineage_rows is None
            else "NOT_EVALUABLE_INCOMPLETE_CLUSTER_LINEAGE"
        )
        metrics = {
            "cfar_branch_hit_cells": cfar["cfar_branch_hit_cells"],
            "unique_union_hit_cells": cfar["unique_union_hit_cells"],
            "postfilter_clusters": cluster_count,
            "false_clusters": cluster_count,
            "final_detections": len(detections),
            "false_detections": len(detections),
            "tracks": track_counts["tracks"],
            "false_confirmed_tracks": track_counts["confirmed_tracks"],
            "cluster_status": cluster_status,
            "cfar_status": cfar["status"],
            "track_status": track_counts["status"],
            "lineage_status": lineage["status"],
        }
    else:
        metrics = {
            "cfar_branch_hit_cells": cfar["cfar_branch_hit_cells"],
            "unique_union_hit_cells": cfar["unique_union_hit_cells"],
            "postfilter_clusters": lineage.get("postfilter_clusters"),
            "postfilter_cluster_status": (
                "evaluable" if lineage.get("postfilter_clusters") is not None
                else "NOT_EVALUABLE_LEGACY_PRE_CLUSTER_SUMMARY" if lineage_rows is None
                else "NOT_EVALUABLE_INCOMPLETE_CLUSTER_LINEAGE"
            ),
            "final_detections": len(detections),
            "target_only_response": dict(target_only_response or {
                "status": "NOT_EVALUABLE",
                "reason": "target_only_complex_support_not_supplied",
                "observable": False,
            }),
            "parameter_observability": (
                "evaluable" if target_only_response and target_only_response.get("status") == "evaluable"
                else "NOT_EVALUABLE"
            ),
            "lineage_status": lineage["status"],
            "cfar_status": cfar["status"],
            "track_status": track_counts["status"],
        }

    return {
        "mode": mode,
        "association_gate": {
            "same_period": True,
            "same_beam": True,
            "range_tolerance_bins": RANGE_GATE_BINS,
            "doppler_tolerance_bins": DOPPLER_GATE_BINS,
            "doppler_distance": "circular",
            "assignment": "maximum-cardinality then minimum-sum normalized Euclidean residual",
            "normalized_residual": "hypot(range_delta/2, circular_doppler_delta/2)",
            "doppler_axis_source": "period/beam Production axis metadata, else detection center/PRF",
        },
        "metrics": metrics,
        "lineage": {key: value for key, value in lineage.items() if key != "lineage_evidence"},
        "lineage_evidence": lineage.get("lineage_evidence", []),
        "association_evidence": association_evidence,
        "candidate_pair_evidence": candidate_pair_evidence,
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _read_csv_many(manifest: Mapping[str, Any], key: str, base: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    value = manifest.get(key, [])
    paths = value if isinstance(value, list) else [value]
    for raw in paths:
        if not raw:
            continue
        path = Path(str(raw))
        if not path.is_absolute():
            path = base / path
        rows.extend(_read_csv(path))
    return rows


def evaluate_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("case manifest must be a JSON object")
    base = manifest_path.parent
    truths = _read_csv_many(manifest, "truth_csv", base)
    detections = _read_csv_many(manifest, "detection_csvs", base)
    lineages = _read_csv_many(manifest, "lineage_csvs", base) if manifest.get("lineage_csvs") is not None else None
    tracks = _read_csv_many(manifest, "track_associations_csv", base)
    frames = _read_csv_many(manifest, "track_frames_csv", base)
    cfar = _read_csv_many(manifest, "cfar_geometry_csv", base)
    if not tracks and (manifest.get("track_detections_csvs") or manifest.get("track_payload_csvs")):
        tracks = track_associations_from_debug(
            track_detections=_read_csv_many(manifest, "track_detections_csvs", base),
            track_payloads=_read_csv_many(manifest, "track_payload_csvs", base),
            final_detections=detections,
            result_to_period=manifest.get("result_to_period", {}),
        )
    response = None
    if manifest.get("target_only_response_json"):
        path = Path(str(manifest["target_only_response_json"]))
        if not path.is_absolute():
            path = base / path
        response = json.loads(path.read_text(encoding="utf-8"))
    return evaluate_case(
        mode=str(manifest.get("mode", "")),
        truths=truths,
        final_detections=detections,
        lineage_rows=lineages,
        track_associations=tracks,
        track_frames=frames,
        cfar_rows=cfar,
        doppler_axis_rows=manifest.get("doppler_axis_rows", []),
        doppler_rows=int(manifest.get("doppler_rows", 0)),
        target_only_response=response,
    )


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["status"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--association-csv", required=True, type=Path)
    parser.add_argument("--lineage-csv", required=True, type=Path)
    args = parser.parse_args()
    result = evaluate_manifest(args.manifest.resolve())
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_csv(args.association_csv, result["association_evidence"] + result["candidate_pair_evidence"])
    _write_csv(args.lineage_csv, result["lineage_evidence"])
    print(json.dumps({"mode": result["mode"], "metrics": result["metrics"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
