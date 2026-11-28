#!/usr/bin/env python3
"""Evaluate the frozen Native CUDA baseline contract from case manifests.

The evaluator combines verified Production final-union CFAR maps, the frozen
R7 task association contract, confirmed/current-frame TrackManager joins, and
the retained CSI tap maps. It accepts one normal-scene manifest or the original
normal-plus-challenge pair. It does not estimate empirical Pfa from H0 hit rate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r import evaluate_r3_minimal_cuda_causal_pilot as r3  # noqa: E402
from scripts.phase_r.evaluate_r7_task_metrics import evaluate_case  # noqa: E402

METHODS = ("C0", "C2", "C3")
SUPPORTED_METHODS = frozenset({"C0", "C1", "C2", "C3"})
CONTROL = "A0"
ALL_CONDITIONS = (CONTROL, *METHODS)
ROLES = ("OFF", "ON", "TO")
UNION_BRANCHES = {"union_dynamic_in_band", "union_full_band"}
LOG_SUMMARY_RE = re.compile(
    r"\[CFAR\]\[SUMMARY\].*?beam=(\d+).*?hit_cells=(\d+).*?"
    r"clusters=(\d+).*?selected=(\d+)"
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _read_csv_paths(value: Any) -> list[dict[str, str]]:
    paths = value if isinstance(value, list) else [value]
    rows: list[dict[str, str]] = []
    for raw in paths:
        if raw:
            rows.extend(_read_csv(Path(str(raw))))
    return rows


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
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _as_int(value: Any, default: int | None = None) -> int | None:
    try:
        text = str(value).strip()
        return int(float(text)) if text else default
    except (TypeError, ValueError, OverflowError):
        return default


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _flag(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _result_number(value: Any) -> int | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits) if digits else None


def strict_track_associations(
    *,
    track_states: Sequence[Mapping[str, Any]],
    track_detections: Sequence[Mapping[str, Any]],
    track_payloads: Sequence[Mapping[str, Any]],
    final_detections: Sequence[Mapping[str, Any]],
    result_to_period: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Join only Confirmed + current-frame matches through the audited payload."""

    period_by_result = {
        number: int(period)
        for result_id, period in result_to_period.items()
        if (number := _result_number(result_id)) is not None
    }
    confirmed_keys: set[tuple[int, int, str]] = set()
    for row in track_states:
        result = _result_number(row.get("result_id"))
        det_index = _as_int(row.get("matched_det_index"))
        track_id = row.get("track_id")
        if (
            result is not None and det_index is not None and track_id is not None
            and str(row.get("state", "")).strip().lower() == "confirmed"
            and _flag(row.get("matched_this_frame"))
        ):
            confirmed_keys.add((result, det_index, str(track_id)))

    matched_keys = {
        (result, det_index, str(row.get("matched_track_id")))
        for row in track_detections
        if (result := _result_number(row.get("result_id"))) is not None
        and (det_index := _as_int(row.get("det_index"))) is not None
        and row.get("matched_track_id") is not None
        and _flag(row.get("matched"))
    }
    payload_by_key: dict[tuple[int, int, str], Mapping[str, Any]] = {}
    for row in track_payloads:
        result = _result_number(row.get("result_id"))
        det_index = _as_int(row.get("matched_det_index"))
        track_id = row.get("track_id")
        if result is None or det_index is None or track_id is None:
            continue
        key = (result, det_index, str(track_id))
        if key in payload_by_key:
            raise ValueError(f"duplicate TrackManager payload identity: {key}")
        payload_by_key[key] = row

    detection_by_key: dict[tuple[int, int, int], Mapping[str, Any]] = {}
    for index, row in enumerate(final_detections):
        result = _result_number(row.get("result_id"))
        period = _as_int(row.get("period_id"))
        det_index = _as_int(row.get("det_id"), index)
        beam = _as_int(row.get("beam_id"))
        if None in (result, period, det_index, beam):
            continue
        if period_by_result.get(result) != period:
            continue
        detection_by_key[(result, det_index, period)] = row

    associations: list[dict[str, Any]] = []
    for key in sorted(confirmed_keys & matched_keys & set(payload_by_key)):
        result, det_index, track_id = key
        period = period_by_result.get(result)
        detection = detection_by_key.get((result, det_index, period)) if period is not None else None
        if detection is None:
            continue
        beam = _as_int(detection.get("beam_id"))
        if beam is None:
            continue
        row: dict[str, Any] = {
            "period_id": period,
            "beam_id": beam,
            "det_id": str(det_index),
            "track_id": track_id,
            "confirmed": 1,
            "matched_this_frame": 1,
            "track_result_number": result,
        }
        speed = _as_float(payload_by_key[key].get("output_speed"))
        if speed is not None:
            row["output_speed"] = speed
        associations.append(row)
    return associations


def _geometry_valid_cut_mask(
    geometry: Mapping[str, Any], rows: int, cols: int,
) -> tuple[np.ndarray | None, list[str]]:
    """Rebuild the supported GO-CFAR valid-CUT mask; reject unknown filters."""

    reasons: list[str] = []
    height = _as_int(geometry.get("height"))
    width = _as_int(geometry.get("width"))
    guard = _as_int(geometry.get("guard_cells"))
    background = _as_int(geometry.get("background_cells"))
    total = _as_int(geometry.get("total_cells"))
    edge = _as_int(geometry.get("edge_invalid_cells"))
    excluded = _as_int(geometry.get("excluded_cells"))
    cut_filtered = _as_int(geometry.get("cut_band_filtered_cells"))
    valid = _as_int(geometry.get("valid_cut_count"))
    tested = _as_int(geometry.get("threshold_test_count"))
    if None in (height, width, guard, background, total, edge, excluded,
                cut_filtered, valid, tested):
        return None, ["incomplete_geometry"]
    if (height, width) != (rows, cols) or total != rows * cols:
        reasons.append("geometry_map_dimension_mismatch")
    edge_width = int(guard) + int(background)
    expected_edge = rows * 2 * edge_width
    if int(edge) != expected_edge:
        reasons.append("unsupported_edge_geometry")
    if (
        int(excluded) != 0 or int(cut_filtered) != 0
        or _as_int(geometry.get("cut_band_mode"), 0) != 0
        or _as_int(geometry.get("exclude_row_start"), -1) != -1
        or _as_int(geometry.get("exclude_row_end"), -1) != -1
    ):
        reasons.append("unsupported_cut_exclusion")
    expected_valid = int(total) - int(edge) - int(excluded) - int(cut_filtered)
    if int(valid) != expected_valid or int(tested) != int(valid):
        reasons.append("geometry_valid_cut_mismatch")
    if edge_width < 0 or cols < 2 * edge_width:
        reasons.append("invalid_range_edge_width")
    if reasons:
        return None, reasons
    mask = np.zeros((rows, cols), dtype=bool)
    mask[:, edge_width : cols - edge_width] = True
    return mask, []


def audit_final_union_counts(
    geometry_rows: Sequence[Mapping[str, Any]],
    *,
    map_rows: int,
    map_cols: int,
    positive_map_cells: int,
    positive_valid_cut_cells: int,
    log_beam_id: int | None,
    log_hit_cells: int | None,
    map_all_finite: bool = True,
) -> dict[str, Any]:
    """Fail-closed map/log/geometry audit for a Production final union map."""

    reasons: list[str] = []
    by_branch: dict[str, Mapping[str, Any]] = {}
    duplicate_branches: set[str] = set()
    for row in geometry_rows:
        branch = str(row.get("branch", ""))
        if branch not in UNION_BRANCHES:
            continue
        if branch in by_branch:
            duplicate_branches.add(branch)
        by_branch[branch] = row
    if duplicate_branches:
        reasons.append("duplicate_geometry_branch")
    if set(by_branch) != UNION_BRANCHES:
        reasons.append("missing_geometry_branch")
    periods = {_as_int(row.get("period_id")) for row in by_branch.values()}
    beams = {_as_int(row.get("beam_id")) for row in by_branch.values()}
    if len(periods) != 1 or None in periods:
        reasons.append("geometry_period_mismatch")
    if len(beams) != 1 or None in beams:
        reasons.append("geometry_beam_mismatch")
    if log_beam_id is None or (beams and log_beam_id not in beams):
        reasons.append("production_log_beam_mismatch")
    if not map_all_finite:
        reasons.append("nonfinite_final_union_map")

    common_rows = list(by_branch.values())
    if common_rows:
        reference = common_rows[0]
        common_fields = (
            "height", "width", "guard_cells", "background_cells", "total_cells",
            "edge_invalid_cells", "excluded_cells", "cut_band_filtered_cells",
            "cut_band_mode", "exclude_row_start", "exclude_row_end",
            "valid_cut_count", "threshold_test_count",
        )
        for row in common_rows[1:]:
            if any(str(row.get(key)) != str(reference.get(key)) for key in common_fields):
                reasons.append("geometry_branch_definition_mismatch")
                break
        _, geometry_reasons = _geometry_valid_cut_mask(reference, map_rows, map_cols)
        reasons.extend(reason for reason in geometry_reasons if reason not in reasons)
        valid_cut_count = _as_int(reference.get("valid_cut_count"))
    else:
        valid_cut_count = None

    branch_hits = {
        name: _as_int(row.get("hit_cut_count"))
        for name, row in by_branch.items()
    }
    if any(value is None or value < 0 for value in branch_hits.values()):
        reasons.append("invalid_component_hit_count")
    if positive_map_cells != positive_valid_cut_cells:
        reasons.append("map_hits_outside_valid_cuts")
    if valid_cut_count is None or valid_cut_count <= 0 or positive_map_cells > valid_cut_count:
        reasons.append("invalid_unique_union_count_or_denominator")
    if branch_hits and all(value is not None for value in branch_hits.values()):
        low = max(int(value) for value in branch_hits.values())
        high = sum(int(value) for value in branch_hits.values())
        if not low <= positive_map_cells <= high:
            reasons.append("union_count_outside_component_bounds")
    if log_hit_cells is None or log_hit_cells != positive_map_cells:
        reasons.append("production_log_map_count_mismatch")

    verified = not reasons
    return {
        "audit_verified": verified,
        "audit_status": "evaluable" if verified else "NOT_EVALUABLE",
        "audit_reasons": reasons,
        "unique_union_hit_cells": positive_map_cells,
        "valid_cut_count": valid_cut_count,
        "observed_hit_fraction": (
            positive_map_cells / valid_cut_count
            if valid_cut_count and valid_cut_count > 0 else None
        ),
        "positive_valid_cut_cells": positive_valid_cut_cells,
        "production_log_hit_cells": log_hit_cells,
        "production_log_beam_id": log_beam_id,
        "component_hit_cells": branch_hits,
        "map_rows": map_rows,
        "map_cols": map_cols,
    }


def _read_key_value_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def _read_log_summaries(path: Path) -> list[dict[str, int]]:
    summaries: list[dict[str, int]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = LOG_SUMMARY_RE.search(line)
        if match:
            summaries.append({
                "beam_id": int(match.group(1)),
                "hit_cells": int(match.group(2)),
                "clusters": int(match.group(3)),
                "selected": int(match.group(4)),
            })
    return summaries


def _hash_record(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def _case_relative(path: Path, case_root: Path) -> str:
    try:
        return path.resolve().relative_to(case_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _method_role_key(case: Mapping[str, Any], method: str) -> str:
    matches = [key for key in case.get("method_mode_roles", {}) if key.startswith(f"{method}_")]
    if len(matches) != 1:
        raise ValueError(f"expected one method/mode role group for {method}, found {matches}")
    return matches[0]


def _branch_map(case: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {
        (str(branch.get("method_id")), str(branch.get("role"))): branch
        for branch in case.get("method_branches", [])
    }


def _selected_methods(case: Mapping[str, Any]) -> tuple[str, ...]:
    raw = case.get("selected_methods")
    if not isinstance(raw, list) or not raw:
        raise ValueError("case manifest must declare a non-empty selected_methods list")
    methods = tuple(str(value) for value in raw)
    if len(methods) != len(set(methods)):
        raise ValueError(f"duplicate selected methods: {methods}")
    unknown = [method for method in methods if method not in SUPPORTED_METHODS]
    if unknown:
        raise ValueError(f"unsupported selected methods: {unknown}")
    if "C0" not in methods:
        raise ValueError("selected methods must include C0 Production Current as the operational anchor")
    return methods


def _load_detection_rows(branch: Mapping[str, Any]) -> tuple[list[dict[str, str]], dict[str, int]]:
    artifacts = branch.get("artifacts", {})
    paths = artifacts.get("detection", [])
    paths = paths if isinstance(paths, list) else [paths]
    rows: list[dict[str, str]] = []
    result_to_period: dict[str, int] = {}
    for raw in paths:
        if not raw:
            continue
        path = Path(str(raw))
        match = re.search(r"GMTI(\d+)", path.name)
        if not match:
            raise ValueError(f"cannot infer Production result period from detection file {path}")
        result_id = f"GMTI{match.group(1)}"
        period_from_name = int(match.group(1)) - 1
        if result_id in result_to_period:
            raise ValueError(f"duplicate Production result file {result_id}: {path}")
        result_to_period[result_id] = period_from_name
        for row in _read_csv(path):
            actual_period = _as_int(row.get("period_id"))
            if actual_period is not None and actual_period != period_from_name:
                raise ValueError(f"detection period disagrees with result file {path}: {row}")
            rows.append(row)
    return rows, result_to_period


def _audit_branch_cfar(
    branch: Mapping[str, Any],
    *,
    scene: str,
    beam_id: int,
    expected_periods: Sequence[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    artifacts = branch.get("artifacts", {})
    geometry_rows = _read_csv_paths(artifacts.get("cfar_geometry", []))
    result_dir = Path(str(branch.get("result_dir", "")))
    log_path = Path(str(branch.get("production_log", "")))
    if not result_dir.is_dir() or not log_path.is_file():
        raise FileNotFoundError(f"Production result/log missing for {branch.get('branch_name')}")
    summaries = _read_log_summaries(log_path)
    if len(summaries) != len(expected_periods):
        raise ValueError(
            f"CFAR summary count {len(summaries)} does not match periods {list(expected_periods)} "
            f"for {branch.get('branch_name')}"
        )
    detection_rows, result_to_period = _load_detection_rows(branch)
    if set(result_to_period.values()) != set(expected_periods):
        raise ValueError(f"detection result periods incomplete for {branch.get('branch_name')}")

    audits: list[dict[str, Any]] = []
    axes: list[dict[str, Any]] = []
    union_rows: list[dict[str, Any]] = []
    for position, period in enumerate(expected_periods):
        result_ids = [key for key, value in result_to_period.items() if value == period]
        if len(result_ids) != 1:
            raise ValueError(f"expected one Production result ID for period {period}: {result_ids}")
        result_id = result_ids[0]
        debug_dir = result_dir / "debug"
        map_path = debug_dir / f"cfar_{result_id}_beam{beam_id:03d}_hits.f32"
        meta_path = debug_dir / f"cfar_{result_id}_beam{beam_id:03d}_meta.txt"
        meta = _read_key_value_file(meta_path)
        rows, cols = _as_int(meta.get("rows")), _as_int(meta.get("cols"))
        if rows is None or cols is None or not map_path.is_file():
            raise FileNotFoundError(f"CFAR map or dimensions missing: {map_path}, {meta_path}")
        values = np.fromfile(map_path, dtype="<f4")
        map_all_finite = bool(np.all(np.isfinite(values)))
        geometry = [
            row for row in geometry_rows
            if _as_int(row.get("period_id")) == period
            and _as_int(row.get("beam_id")) == beam_id
        ]
        if geometry:
            valid_mask, _ = _geometry_valid_cut_mask(geometry[0], rows, cols)
        else:
            valid_mask = None
        expected_size = rows * cols
        shape_valid = values.size == expected_size
        if shape_valid:
            positive = values > 0.0
            positive_count = int(np.count_nonzero(positive))
            if valid_mask is not None and valid_mask.shape == (rows, cols):
                valid_positive_count = int(np.count_nonzero(positive.reshape(rows, cols) & valid_mask))
            else:
                valid_positive_count = -1
        else:
            positive_count = -1
            valid_positive_count = -1
        if not shape_valid:
            map_all_finite = False
        log_summary = summaries[position]
        audit = audit_final_union_counts(
            geometry,
            map_rows=rows,
            map_cols=cols,
            positive_map_cells=positive_count,
            positive_valid_cut_cells=valid_positive_count,
            log_beam_id=log_summary["beam_id"],
            log_hit_cells=log_summary["hit_cells"],
            map_all_finite=map_all_finite,
        )
        if not shape_valid:
            audit["audit_verified"] = False
            audit["audit_status"] = "NOT_EVALUABLE"
            audit["audit_reasons"].append("map_value_count_mismatch")
        if not geometry:
            audit["audit_verified"] = False
            audit["audit_status"] = "NOT_EVALUABLE"
            audit["audit_reasons"].append("geometry_period_beam_missing")
        audit.update({
            "scene": scene,
            "method_id": branch.get("method_id"),
            "role": branch.get("role"),
            "branch_name": branch.get("branch_name"),
            "period_id": period,
            "beam_id": beam_id,
            "result_id": result_id,
            "map_sha256": _hash_record(map_path)["sha256"],
            "map_bytes": map_path.stat().st_size,
            "meta_path": str(meta_path),
            "log_summary_order": position,
            "log_clusters": log_summary["clusters"],
            "log_selected": log_summary["selected"],
            "log_period_join": "ordered production loop; result file GMTIxx ordinal",
            "doppler_axis_first_hz": _as_float(meta.get("doppler_axis_row0_hz")),
            "doppler_bin_spacing_hz": _as_float(meta.get("doppler_axis_row_step_hz")),
            "doppler_row_count": rows,
            "doppler_axis_uniform": 1,
            "configured_pfa": _as_float(geometry[0].get("configured_pfa")) if geometry else None,
            "cfar_type": geometry[0].get("cfar_type") if geometry else None,
        })
        audits.append(audit)
        union_rows.append({
            "period_id": period,
            "beam_id": beam_id,
            "branch": "unique_union_audit",
            "unique_union_hit_cells": audit["unique_union_hit_cells"],
            "valid_cut_count": audit["valid_cut_count"],
            "audit_verified": audit["audit_verified"],
        })
        axes.append({
            "period_id": period,
            "beam_id": beam_id,
            "doppler_axis_first_hz": audit["doppler_axis_first_hz"],
            "doppler_bin_spacing_hz": audit["doppler_bin_spacing_hz"],
            "doppler_row_count": rows,
            "doppler_axis_uniform": 1,
        })
    return geometry_rows + union_rows, audits, axes


def _strict_tracks(branch: Mapping[str, Any], detections: Sequence[Mapping[str, Any]], result_to_period: Mapping[str, int]) -> list[dict[str, Any]]:
    artifacts = branch.get("artifacts", {})
    debug_dir = Path(str(branch.get("track_debug_dir", "")))
    states_paths = artifacts.get("track_states", [])
    payload_paths = artifacts.get("track_payloads", [])
    states = _read_csv_paths(states_paths)
    payloads = _read_csv_paths(payload_paths)
    track_detection_path = debug_dir / "track_detections.csv"
    if not track_detection_path.is_file():
        return []
    return strict_track_associations(
        track_states=states,
        track_detections=_read_csv(track_detection_path),
        track_payloads=payloads,
        final_detections=detections,
        result_to_period=result_to_period,
    )


def _audit_adapter(branch: Mapping[str, Any], expected_period_count: int) -> tuple[str, list[dict[str, Any]]]:
    method = str(branch.get("method_id"))
    role = str(branch.get("role"))
    artifacts = branch.get("artifacts", {})
    rows = _read_csv_paths(artifacts.get("adapter_csv", []))
    contract = branch.get("execution_contract", {})
    if method == "C1":
        if not rows:
            raise ValueError(f"Gamma=1 adapter evidence missing for {method}/{role}")
        if (
            str(branch.get("mode")) != "not_applicable"
            or contract.get("estimator_called") is not False
            or contract.get("fixed_reference_required") is not False
            or contract.get("reference_applied") is not False
            or contract.get("reference_source") != "not_applicable"
            or contract.get("reference_artifact") is not None
            or branch.get("reference_applied") is True
            or branch.get("fallback_to_current") is not False
        ):
            raise ValueError(f"Gamma=1 no-reference contract failed for {method}/{role}")
        if len(rows) != expected_period_count:
            raise ValueError(f"adapter row count mismatch for {method}/{role}: {len(rows)}")
        period_ids: set[int] = set()
        enriched_rows: list[dict[str, Any]] = []
        for row in rows:
            try:
                result_id = str(row.get("result_id", "")).strip()
                result_match = re.fullmatch(r"GMTI(\d+)", result_id)
                if result_match is None:
                    raise ValueError("invalid Production result_id")
                result_number = int(result_match.group(1))
                period_id = result_number - 1
                gamma_real = float(str(row.get("gamma_real", "")).strip())
                gamma_imag = float(str(row.get("gamma_imag", "")).strip())
                support_count = int(str(row.get("support_count", "")).strip())
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid Gamma=1 adapter numeric field for {method}/{role}: {row}") from exc
            explicit_period_id = str(row.get("period_id", "")).strip()
            if explicit_period_id:
                try:
                    if int(explicit_period_id) != period_id:
                        raise ValueError("period_id disagrees with Production result_id")
                except ValueError as exc:
                    raise ValueError(f"Gamma=1 adapter period identity failed for {method}/{role}: {row}") from exc
            if (
                result_number < 1
                or result_number > expected_period_count
                or period_id in period_ids
                or result_id != f"GMTI{result_number:02d}"
                or str(row.get("method", "")).strip() != "ordinary_subtraction"
                or str(row.get("status", "")).strip().upper() != "OK"
                or _flag(row.get("truth_used_in_estimator"))
                or str(row.get("source", "")) != "production_final_f1_f2_after_p38_range_phase"
                or support_count <= 0
                or not math.isfinite(gamma_real)
                or not math.isfinite(gamma_imag)
                or not math.isclose(gamma_real, 1.0, rel_tol=0.0, abs_tol=1.0e-12)
                or not math.isclose(gamma_imag, 0.0, rel_tol=0.0, abs_tol=1.0e-12)
            ):
                raise ValueError(f"Gamma=1 adapter evidence failed for {method}/{role}: {row}")
            period_ids.add(period_id)
            enriched_rows.append({
                "method_id": method,
                "role": role,
                "branch_name": branch.get("branch_name"),
                **row,
                "period_id": period_id,
            })
        if period_ids != set(range(expected_period_count)):
            raise ValueError(f"adapter period identity mismatch for {method}/{role}: {sorted(period_ids)}")
        return "passed", enriched_rows
    if method not in {"C2", "C3"}:
        if rows:
            raise ValueError(f"unexpected calibration adapter rows for non-adaptive {method}/{role}")
        if contract.get("fallback_to_current") is True:
            raise ValueError(f"unexpected Current fallback declaration in {method}/{role}")
        return "not_applicable", []
    expected_source = (
        "production_final_f1_f2_after_p38_range_phase"
        if role == "OFF" else "reference_gamma_artifact_fixed_apply"
    )
    if len(rows) != expected_period_count:
        raise ValueError(f"adapter row count mismatch for {method}/{role}: {len(rows)}")
    if contract.get("truth_used_in_estimator") is not False or contract.get("fallback_to_current") is not False:
        raise ValueError(f"truth/fallback contract failed for {method}/{role}")
    if role == "OFF" and contract.get("estimator_called") is not True:
        raise ValueError(f"Mode-A reference was not fitted on target-free OFF for {method}")
    if role in {"ON", "TO"} and (
        contract.get("estimator_called") is not False
        or contract.get("reference_applied") is not True
    ):
        raise ValueError(f"fixed OFF reference not applied as required for {method}/{role}")
    for row in rows:
        if (
            str(row.get("status", "")).upper() != "OK"
            or _flag(row.get("truth_used_in_estimator"))
            or str(row.get("source", "")) != expected_source
        ):
            raise ValueError(f"Mode-A adapter evidence failed for {method}/{role}: {row}")
    enriched = [
        {"method_id": method, "role": role, "branch_name": branch.get("branch_name"), **row}
        for row in rows
    ]
    return "passed", enriched


def _hashes_for_method_roles(
    case: Mapping[str, Any], method: str,
) -> dict[str, list[dict[str, Any]]]:
    method_key = _method_role_key(case, method)
    roles = case["method_mode_roles"][method_key]
    return {
        role: [dict(row) for row in roles[role].get("period_paths", [])]
        for role in ROLES
    }


def _verify_shared_inputs(
    case: Mapping[str, Any], case_root: Path, methods: Sequence[str] = METHODS,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_role: dict[str, list[str]] = {}
    verified_hashes: dict[Path, str] = {}
    conditions = (CONTROL, *methods)
    for method in conditions:
        method_hashes = _hashes_for_method_roles(case, method)
        for role in ROLES:
            refs = method_hashes[role]
            hashes = [str(ref.get("sha256", "")) for ref in refs]
            if not hashes or any(not value for value in hashes):
                raise ValueError(f"input hash inventory missing for {method}/{role}")
            if len(hashes) != int(case.get("scene_identity", {}).get("period_count", 0)):
                raise ValueError(f"input period count mismatch for {method}/{role}")
            key = f"{method}/{role}"
            by_role[key] = hashes
            for ref in refs:
                path = Path(str(ref["path"]))
                if not path.is_file() or path.stat().st_size != int(ref.get("bytes", -1)):
                    raise ValueError(f"input file missing or size changed since production: {path}")
                resolved = path.resolve()
                actual_sha256 = verified_hashes.get(resolved)
                if actual_sha256 is None:
                    actual_sha256 = str(_hash_record(path)["sha256"])
                    verified_hashes[resolved] = actual_sha256
                if actual_sha256 != str(ref.get("sha256", "")):
                    raise ValueError(f"input SHA-256 changed since production: {path}")
                rows.append({
                    "method_id": method,
                    "role": role,
                    "period_input": path.name,
                    "path": _case_relative(path, case_root),
                    "bytes": ref.get("bytes"),
                    "sha256": actual_sha256,
                    "manifest_sha256_verified": True,
                })
    for role in ROLES:
        baseline = by_role[f"C0/{role}"]
        for method in conditions:
            if by_role[f"{method}/{role}"] != baseline:
                raise ValueError(f"physical input hashes differ for {method}/{role}")
    return rows


def _read_truth(case: Mapping[str, Any]) -> list[dict[str, str]]:
    simulation = case.get("simulation", {})
    source = simulation.get("A0_ON", {}) if isinstance(simulation, Mapping) else {}
    output = Path(str(source.get("output_dir", "")))
    path = output / "truth" / "truth_targets_by_beam.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Stage2 target truth missing: {path}")
    rows = [row for row in _read_csv(path) if _flag(row.get("visible", "1"))]
    return sorted(rows, key=lambda row: (_as_int(row.get("period_id"), -1), _as_int(row.get("beam_id"), -1)))


def audit_paired_scene_inputs(
    *, case: Mapping[str, Any], scene: str,
) -> list[dict[str, Any]]:
    """Close OFF=C+N against the generated background and ON against its writer."""

    variants = case.get("scene_variants", {})
    simulation = case.get("simulation", {})
    if not isinstance(variants, Mapping) or not isinstance(simulation, Mapping):
        raise ValueError(f"Stage2 pairing manifest missing for {scene}")
    on_scenario_path = Path(str(variants.get("A0_ON", "")))
    if not on_scenario_path.is_file():
        raise FileNotFoundError(f"A0_ON Stage2 scenario missing: {on_scenario_path}")
    on_scenario = json.loads(on_scenario_path.read_text(encoding="utf-8"))
    background_dir = Path(str(on_scenario.get("paired_background_output_dir", "")))
    background_data = background_dir / "data"
    owner_output = Path(str(simulation.get("A0_ON", {}).get("output_dir", "")))
    owner_data = owner_output / "data"
    owner_report = owner_output / "reports" / "stage2_simulation_report.md"
    if not background_data.is_dir() or not owner_data.is_dir() or not owner_report.is_file():
        raise FileNotFoundError(f"Stage2 background writer artifacts missing for {scene}")

    def report_values(path: Path) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^-\s+([A-Za-z0-9_]+):\s*(.*)$", line.strip())
            if match:
                result[match.group(1)] = match.group(2)
        return result

    owner_report_values = report_values(owner_report)
    gate = case.get("area_clutter_gate", {})
    if (
        _as_int(owner_report_values.get("continuous_area_packets"), 0) <= 0
        or _as_int(owner_report_values.get("continuous_area_samples"), 0) <= 0
        or _as_float(owner_report_values.get("area_mean_power_measured"), 0.0) <= 0.0
        or gate.get("status") != "passed"
    ):
        raise ValueError(f"area clutter writer report did not prove injection for {scene}")

    branch_map = _branch_map(case)
    off_paths = branch_map[("C0", "OFF")].get("period_paths", [])
    on_paths = branch_map[("C0", "ON")].get("period_paths", [])
    if len(off_paths) != len(on_paths) or not off_paths:
        raise ValueError(f"paired OFF/ON period path inventory mismatch for {scene}")
    rows: list[dict[str, Any]] = []
    for period, (off, on) in enumerate(zip(off_paths, on_paths)):
        off_path = Path(str(off.get("path", "")))
        on_path = Path(str(on.get("path", "")))
        filename = off_path.name
        background_path = background_data / filename
        owner_path = owner_data / on_path.name
        if not all(path.is_file() for path in (off_path, on_path, background_path, owner_path)):
            raise FileNotFoundError(f"paired Stage2 IQ file missing for {scene}/period={period}")
        off_hash = str(_hash_record(off_path)["sha256"])
        background_hash = str(_hash_record(background_path)["sha256"])
        on_hash = str(_hash_record(on_path)["sha256"])
        owner_hash = str(_hash_record(owner_path)["sha256"])
        off_matches = off_hash == background_hash == str(off.get("sha256", ""))
        on_matches = on_hash == owner_hash == str(on.get("sha256", ""))
        rows.append({
            "scene": scene,
            "period_id": period,
            "off_role": "C+N",
            "on_role": "S+C+N",
            "background_writer": "A0_ON",
            "off_input_path": _case_relative(off_path, off_path.parents[3]),
            "background_path": str(background_path),
            "on_input_path": _case_relative(on_path, on_path.parents[3]),
            "writer_on_path": str(owner_path),
            "off_input_sha256": off_hash,
            "background_sha256": background_hash,
            "on_input_sha256": on_hash,
            "writer_on_sha256": owner_hash,
            "off_matches_area_background": off_matches,
            "on_matches_area_writer_output": on_matches,
            "area_owner_continuous_packets": owner_report_values.get("continuous_area_packets"),
            "area_owner_continuous_samples": owner_report_values.get("continuous_area_samples"),
            "area_owner_mean_power": owner_report_values.get("area_mean_power_measured"),
            "area_owner_lag1_rho": owner_report_values.get("area_rho_lag1_measured"),
            "area_owner_ctdr_lag_rho": owner_report_values.get("area_rho_at_CTDR_lag"),
            "area_owner_report_sha256": _hash_record(owner_report)["sha256"],
            "audit_verified": off_matches and on_matches,
        })
    if len(rows) != int(case.get("scene_identity", {}).get("period_count", 0)) or not all(row["audit_verified"] for row in rows):
        raise ValueError(f"paired area input hash closure failed for {scene}")
    return rows


def _evaluate_branch(
    *,
    scene: str,
    case: Mapping[str, Any],
    case_root: Path,
    branch: Mapping[str, Any],
    truths: Sequence[Mapping[str, Any]],
    beam_id: int,
) -> dict[str, Any]:
    if branch.get("status") != "passed" or _as_int(branch.get("production_returncode")) != 0:
        raise ValueError(f"Production branch did not pass: {branch.get('branch_name')}")
    adapter_status, adapter_rows = _audit_adapter(
        branch, expected_period_count=int(case["scene_identity"]["period_count"]),
    )
    detection_rows, result_to_period = _load_detection_rows(branch)
    expected_periods = sorted({_as_int(row.get("period_id"), -1) for row in truths})
    cfar_rows, cfar_audits, axis_rows = _audit_branch_cfar(
        branch, scene=scene, beam_id=beam_id, expected_periods=expected_periods,
    )
    if not all(row["audit_verified"] for row in cfar_audits):
        raise ValueError(
            f"CFAR union audit failed for {branch.get('branch_name')}: "
            f"{[row['audit_reasons'] for row in cfar_audits]}"
        )
    track_rows = _strict_tracks(branch, detection_rows, result_to_period)
    frames = _read_csv_paths(branch.get("artifacts", {}).get("track_frames", []))
    role = str(branch.get("role"))
    evaluator_mode = "OFF" if role == "OFF" else "ON"
    task = evaluate_case(
        mode=evaluator_mode,
        truths=truths if role != "OFF" else (),
        final_detections=detection_rows,
        track_associations=track_rows,
        track_frames=frames,
        cfar_rows=cfar_rows,
        expected_cfar_period_beam_keys={
            (_as_int(row.get("period_id"), -1), _as_int(row.get("beam_id"), -1))
            for row in truths
        },
        doppler_axis_rows=axis_rows,
        doppler_rows=axis_rows[0]["doppler_row_count"] if axis_rows else 0,
    )
    evaluation = {
        "scene": scene,
        "method_id": branch.get("method_id"),
        "role": role,
        "branch_name": branch.get("branch_name"),
        "evaluator_mode": evaluator_mode,
        "target_only_control": role == "TO",
        "status": branch.get("status"),
        "adapter_status": adapter_status,
        "adapter_rows": adapter_rows,
        "production_returncode": branch.get("production_returncode"),
        "production_elapsed_sec": branch.get("production_elapsed_sec"),
        "result_dir": branch.get("result_dir"),
        "production_log": branch.get("production_log"),
        "production_xml": branch.get("production_xml"),
        "track_debug_dir": branch.get("track_debug_dir"),
        "detection_rows": len(detection_rows),
        "strict_track_associations": track_rows,
        "task": task,
        "cfar_audits": cfar_audits,
        "frames": frames,
        "detections": detection_rows,
        "result_to_period": result_to_period,
    }
    return evaluation


def _csi_manifest_rows(branch: Mapping[str, Any]) -> dict[int, tuple[Path, dict[str, str]]]:
    result_dir = Path(str(branch.get("result_dir", "")))
    manifests = sorted(result_dir.rglob("csi_roi_manifest.csv"))
    rows_by_period: dict[int, tuple[Path, dict[str, str]]] = {}
    for manifest in manifests:
        for row in _read_csv(manifest):
            period = _as_int(row.get("period_id"))
            if period is None or not row.get("csi_after_real_path"):
                continue
            if period in rows_by_period:
                raise ValueError(f"duplicate CSI ROI manifest for period {period}: {result_dir}")
            rows_by_period[period] = (manifest, row)
    return rows_by_period


def _load_tap_array(manifest: Path, row: Mapping[str, Any], key: str) -> np.ndarray:
    path = _tap_array_path(manifest, row, key)
    return np.asarray(np.load(path), dtype=np.float64)


def _tap_array_path(manifest: Path, row: Mapping[str, Any], key: str) -> Path:
    raw = str(row.get(key, ""))
    if not raw:
        raise ValueError(f"CSI tap path missing: {key}")
    path = Path(raw)
    if not path.is_absolute():
        path = (manifest.parent / path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"CSI tap array missing: {path}")
    return path


def audit_shared_f1f2_inputs(
    rows: Sequence[Mapping[str, Any]],
    conditions: Sequence[str] = ALL_CONDITIONS,
) -> list[dict[str, Any]]:
    """Verify each selected method receives identical production F1/F2 taps."""

    grouped: dict[tuple[str, str, str], dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("scene", "")), str(row.get("role", "")), str(row.get("period_id", "")))
        method = str(row.get("method_id", ""))
        if method in conditions:
            if method in grouped.setdefault(key, {}):
                raise ValueError(f"duplicate F1/F2 tap identity: {key}/{method}")
            grouped[key][method] = row

    audits: list[dict[str, Any]] = []
    for (scene, role, period), method_rows in sorted(grouped.items()):
        missing = sorted(set(conditions) - set(method_rows))
        f1_hashes = {str(row.get("f1_input_sha256", "")) for row in method_rows.values()}
        f2_hashes = {str(row.get("f2_input_sha256", "")) for row in method_rows.values()}
        f1_powers = [_as_float(row.get("f1_input_active_support_mean_power")) for row in method_rows.values()]
        f2_powers = [_as_float(row.get("f2_input_active_support_mean_power")) for row in method_rows.values()]
        missing_hash = "" in f1_hashes or "" in f2_hashes
        verified = not missing and not missing_hash and len(f1_hashes) == 1 and len(f2_hashes) == 1
        audits.append({
            "scene": scene,
            "role": role,
            "period_id": period,
            "method_count": len(method_rows),
            "methods_present": ",".join(sorted(method_rows)),
            "f1_hash_unique_count": len(f1_hashes - {""}),
            "f2_hash_unique_count": len(f2_hashes - {""}),
            "f1_active_support_mean_power": f1_powers[0] if f1_powers and all(v == f1_powers[0] for v in f1_powers) else None,
            "f2_active_support_mean_power": f2_powers[0] if f2_powers and all(v == f2_powers[0] for v in f2_powers) else None,
            "audit_verified": verified,
            "audit_status": "verified_identical_f1_f2_inputs" if verified else "NOT_EVALUABLE_OR_INPUT_MISMATCH",
            "audit_reasons": ";".join(
                ([f"missing_methods:{','.join(missing)}"] if missing else [])
                + (["missing_f1_f2_hash"] if missing_hash else [])
                + (["f1_hash_mismatch"] if len(f1_hashes) > 1 else [])
                + (["f2_hash_mismatch"] if len(f2_hashes) > 1 else [])
            ),
        })
    return audits


def _csi_metrics(
    *,
    scene: str,
    case: Mapping[str, Any],
    case_root: Path,
    branch_records: Mapping[tuple[str, str], Mapping[str, Any]],
    conditions: Sequence[str] = ALL_CONDITIONS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    overall: list[dict[str, Any]] = []
    per_period: list[dict[str, Any]] = []
    roi_samples: list[dict[str, Any]] = []
    for method in conditions:
        roles = {role: branch_records[(method, role)] for role in ROLES}
        target = r3._target_transfer(case_root, case, f"{method}_R13", roles)
        off_rows = _csi_manifest_rows(roles["OFF"])
        if not off_rows:
            raise FileNotFoundError(f"CSI tap manifest missing for {scene}/{method}/OFF")
        clutter_periods: list[dict[str, Any]] = []
        for period, (manifest_path, row) in sorted(off_rows.items()):
            before = _load_tap_array(manifest_path, row, "before_power_path")
            real = _load_tap_array(manifest_path, row, "csi_after_real_path")
            imag = _load_tap_array(manifest_path, row, "csi_after_imag_path")
            shape_ok = before.shape == real.shape == imag.shape
            if not shape_ok or before.ndim != 2:
                raise ValueError(f"CSI tap array dimensions differ for {scene}/{method}/OFF/{period}")
            residual = r3.active_support_csi_power(
                before,
                real,
                imag,
                row_start=int(row["az_st"]),
                row_end=int(row["az_ed"]),
                col_start=int(row["rg_st"]),
                col_end=int(row["rg_ed"]),
            )
            clutter_periods.append({
                "period_id": period,
                **residual,
                "source_manifest": _case_relative(manifest_path, case_root),
                "before_power_sha256": _hash_record(_tap_array_path(manifest_path, row, "before_power_path"))["sha256"],
                "csi_after_real_sha256": _hash_record(_tap_array_path(manifest_path, row, "csi_after_real_path"))["sha256"],
                "csi_after_imag_sha256": _hash_record(_tap_array_path(manifest_path, row, "csi_after_imag_path"))["sha256"],
            })

        transfer_by_period = {
            int(row["period_id"]): row for row in target.get("periods", [])
        }
        target_rows = r3._truth_by_period(case_root, case.get("simulation", {}))
        for role in ROLES:
            role_taps = _csi_manifest_rows(roles[role])
            for period, (manifest_path, row) in sorted(role_taps.items()):
                truth = target_rows.get(period)
                if truth is None:
                    continue
                arrays = {
                    "before": _load_tap_array(manifest_path, row, "before_power_path"),
                    "real": _load_tap_array(manifest_path, row, "csi_after_real_path"),
                    "imag": _load_tap_array(manifest_path, row, "csi_after_imag_path"),
                    "f1_power": _load_tap_array(manifest_path, row, "channel1_power_path"),
                    "f2_power": _load_tap_array(manifest_path, row, "channel2_power_path"),
                    "fa_axis": _load_tap_array(manifest_path, row, "fa_axis_path").reshape(-1),
                    "range_axis": _load_tap_array(manifest_path, row, "range_axis_path").reshape(-1),
                }
                if arrays["f1_power"].shape != arrays["f2_power"].shape:
                    raise ValueError(f"F1/F2 CSI tap dimensions differ for {scene}/{method}/{role}/{period}")
                active = (
                    slice(int(row["az_st"]), int(row["az_ed"]) + 1),
                    slice(int(row["rg_st"]), int(row["rg_ed"]) + 1),
                )
                f1_input_mean = float(np.mean(arrays["f1_power"][active]))
                f2_input_mean = float(np.mean(arrays["f2_power"][active]))
                complex_map = arrays["real"] + 1j * arrays["imag"]
                center_row, center_col = r3._target_center(arrays, truth)
                roi = r3._roi(complex_map, center_row, center_col)
                before_roi = r3._roi(arrays["before"], center_row, center_col)
                if roi.shape != before_roi.shape:
                    raise ValueError(f"target CSI ROI dimensions differ for {scene}/{method}/{role}/{period}")
                half_r, half_c = roi.shape[0] // 2, roi.shape[1] // 2
                for row_index in range(roi.shape[0]):
                    for col_index in range(roi.shape[1]):
                        value = roi[row_index, col_index]
                        roi_samples.append({
                            "scene": scene,
                            "method_id": method,
                            "role": role,
                            "period_id": period,
                            "target_row_center": center_row,
                            "target_col_center": center_col,
                            "doppler_row": center_row + row_index - half_r,
                            "range_col": center_col + col_index - half_c,
                            "doppler_offset": row_index - half_r,
                            "range_offset": col_index - half_c,
                            "before_power": float(before_roi[row_index, col_index]),
                            "csi_after_real": float(value.real),
                            "csi_after_imag": float(value.imag),
                            "truth_range_m": truth.get("range_m"),
                            "truth_af_total_hz": truth.get("af_total_truth_hz"),
                        })
                target_period = transfer_by_period.get(period, {})
                clutter_period = next((item for item in clutter_periods if item["period_id"] == period), {})
                array_hashes = {
                    key: _hash_record(_tap_array_path(manifest_path, row, key))["sha256"]
                    for key in (
                        "before_power_path", "csi_after_real_path", "csi_after_imag_path",
                        "channel1_power_path", "channel2_power_path",
                    )
                }
                per_period.append({
                    "scene": scene,
                    "method_id": method,
                    "role": role,
                    "period_id": period,
                    "target_row_center": center_row,
                    "target_col_center": center_col,
                    "target_only_output_power": target_period.get("target_only_csi_power") if role == "TO" else None,
                    "h0_target_roi_output_power": target_period.get("h0_csi_power") if role == "OFF" else None,
                    "target_only_input_power": target_period.get("target_only_before_power") if role == "TO" else None,
                    "h0_active_support_valid_cells": clutter_period.get("valid_cells") if role == "OFF" else None,
                    "h0_active_support_before_mean_power": clutter_period.get("before_mean_power") if role == "OFF" else None,
                    "h0_active_support_csi_output_mean_power": clutter_period.get("csi_output_mean_power") if role == "OFF" else None,
                    "h0_active_support_cancellation_db": clutter_period.get("cancellation_db") if role == "OFF" else None,
                    "before_power_sha256": array_hashes["before_power_path"],
                    "csi_after_real_sha256": array_hashes["csi_after_real_path"],
                    "csi_after_imag_sha256": array_hashes["csi_after_imag_path"],
                    "f1_input_active_support_mean_power": f1_input_mean,
                    "f2_input_active_support_mean_power": f2_input_mean,
                    "f2_to_f1_input_power_ratio_db": (
                        10.0 * math.log10(f2_input_mean / f1_input_mean)
                        if f1_input_mean > 0.0 and f2_input_mean > 0.0 else None
                    ),
                    "f1_input_sha256": array_hashes["channel1_power_path"],
                    "f2_input_sha256": array_hashes["channel2_power_path"],
                    "csi_roi_manifest": _case_relative(manifest_path, case_root),
                })
        clutter_cancellation_db = [
            row["cancellation_db"] for row in clutter_periods
            if row.get("status") == "evaluable" and row.get("cancellation_db") is not None
        ]
        overall.append({
            "scene": scene,
            "method_id": method,
            "h0_active_support_period_count": len(clutter_cancellation_db),
            "h0_active_support_cancellation_db_mean_over_periods": (
                float(np.mean(clutter_cancellation_db)) if clutter_cancellation_db else None
            ),
            "h0_active_support_periods": json.dumps(clutter_periods, sort_keys=True),
            "target_transfer_status": target.get("status"),
            "target_transfer_amplitude_ratio": target.get("paired_complex_transfer", {}).get("amplitude_ratio"),
            "target_transfer_amplitude_ratio_db": target.get("paired_complex_transfer", {}).get("amplitude_ratio_db"),
            "target_transfer_phase_error_rad": target.get("paired_complex_transfer", {}).get("phase_error_rad"),
            "target_transfer_sample_count": target.get("paired_complex_transfer", {}).get("sample_count"),
            "target_only_csi_retention_db": target.get("target_only_csi_retention_db"),
            "output_scnr_proxy_db": target.get("output_scnr_proxy_db"),
            "target_only_output_power_mean": target.get("target_only_output_power_mean"),
            "h0_output_power_mean_target_roi": target.get("h0_output_power_mean"),
            "target_transfer_definitions": json.dumps(target.get("definitions", {}), sort_keys=True),
            "single_seed_descriptive_only": True,
        })
    return overall, per_period, roi_samples


def _rmse(values: Sequence[float]) -> float | None:
    return math.sqrt(sum(value * value for value in values) / len(values)) if values else None


def _common_opportunity_rows(
    evaluations: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    scene: str,
    methods: Sequence[str] = METHODS,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    metrics = (
        "position_error_m", "angle_error_deg", "ground_speed_error_mps",
        "folded_doppler_frequency_error_hz",
    )
    for role in ("ON", "TO"):
        for left_index, left in enumerate(methods):
            left_eval = evaluations[(left, role)]
            left_associations = {
                int(row["period_id"]): row
                for row in left_eval["task"]["association_evidence"]
                if _flag(row.get("matched"))
            }
            for right in methods[left_index + 1 :]:
                right_eval = evaluations[(right, role)]
                right_associations = {
                    int(row["period_id"]): row
                    for row in right_eval["task"]["association_evidence"]
                    if _flag(row.get("matched"))
                }
                common_periods = sorted(set(left_associations) & set(right_associations))
                if not common_periods:
                    rows.append({
                        "scene": scene,
                        "role": role,
                        "method_left": left,
                        "method_right": right,
                        "metric": "all",
                        "period_id": "",
                        "common_correct_target_opportunities": 0,
                        "rmse_left": None,
                        "rmse_right": None,
                        "delta_right_minus_left": None,
                        "status": "NOT_EVALUABLE_NO_COMMON_HITS",
                    })
                    continue
                for metric in metrics:
                    left_values: list[float] = []
                    right_values: list[float] = []
                    included_periods: list[int] = []
                    for period in common_periods:
                        lv = _as_float(left_associations[period].get(metric))
                        rv = _as_float(right_associations[period].get(metric))
                        if lv is None or rv is None:
                            continue
                        left_values.append(lv)
                        right_values.append(rv)
                        included_periods.append(period)
                    left_rmse, right_rmse = _rmse(left_values), _rmse(right_values)
                    rows.append({
                        "scene": scene,
                        "role": role,
                        "method_left": left,
                        "method_right": right,
                        "metric": metric,
                        "period_id": ";".join(map(str, included_periods)),
                        "common_correct_target_opportunities": len(included_periods),
                        "rmse_left": left_rmse,
                        "rmse_right": right_rmse,
                        "delta_right_minus_left": (
                            right_rmse - left_rmse
                            if left_rmse is not None and right_rmse is not None else None
                        ),
                        "status": "evaluable" if included_periods else "NOT_EVALUABLE_METRIC_MISSING_ON_COMMON_HITS",
                        "statistical_claim": "none_single_seed_descriptive",
                    })
    return rows


def _manifest_metadata(case: Mapping[str, Any], case_root: Path, scene: str) -> dict[str, Any]:
    compact_manifest_path = case_root.parents[1] / "compact_evidence" / "manifest.json"
    compact = json.loads(compact_manifest_path.read_text(encoding="utf-8")) if compact_manifest_path.is_file() else {}
    execution_manifest_path = case_root.parents[1] / "manifest.json"
    execution = json.loads(execution_manifest_path.read_text(encoding="utf-8")) if execution_manifest_path.is_file() else {}
    return {
        "scene": scene,
        "case_id": case.get("case_id"),
        "case_manifest": str(case_root / "case_manifest.json"),
        "source_commit": compact.get("source", {}).get("commit"),
        "source_dirty_at_execution": compact.get("source", {}).get("dirty"),
        "runner_sha256": compact.get("runner", {}).get("sha256"),
        "config_sha256": compact.get("config", {}).get("sha256"),
        "case_parameters": case.get("case"),
        "area_clutter_gate": case.get("area_clutter_gate"),
        "causal_triplets": case.get("causal_triplets"),
        "selected_methods": case.get("selected_methods"),
        "selected_calibration_modes": case.get("selected_calibration_modes"),
        "input_contract": case.get("input_contract"),
        "execution_provenance": {
            "execution_manifest": str(execution_manifest_path),
            "created_at_utc": execution.get("created_at_utc"),
            "mode": execution.get("mode"),
            "input_mode": execution.get("input_mode"),
            "command": execution.get("command"),
            "gpu_probe": execution.get("gpu_probe"),
            "build": execution.get("build"),
            "python": execution.get("python"),
            "platform": execution.get("platform"),
            "source": execution.get("source"),
        },
    }


def evaluate_case_manifest(case_manifest_path: Path, scene: str) -> dict[str, Any]:
    case_manifest_path = case_manifest_path.resolve()
    case_root = case_manifest_path.parent
    case = json.loads(case_manifest_path.read_text(encoding="utf-8"))
    if case.get("area_clutter_gate", {}).get("status") != "passed":
        raise ValueError(f"area clutter gate did not pass: {case_manifest_path}")
    methods = _selected_methods(case)
    conditions = (CONTROL, *methods)
    beam_id = int(case["scene_identity"]["beam_id"])
    truths = _read_truth(case)
    truth_periods = sorted({_as_int(row.get("period_id"), -1) for row in truths})
    expected_period_count = int(case["scene_identity"]["period_count"])
    if len(truths) != expected_period_count or len(truth_periods) != expected_period_count:
        raise ValueError(f"truth opportunities mismatch in {case_manifest_path}")
    branch_records = _branch_map(case)
    for condition in conditions:
        for role in ROLES:
            if (condition, role) not in branch_records:
                raise ValueError(f"missing Production branch {condition}/{role}")
    input_hashes = _verify_shared_inputs(case, case_root, methods)
    paired_input_audit = audit_paired_scene_inputs(case=case, scene=scene)
    evaluations: dict[str, dict[str, Any]] = {}
    for method in conditions:
        for role in ROLES:
            result = _evaluate_branch(
                scene=scene,
                case=case,
                case_root=case_root,
                branch=branch_records[(method, role)],
                truths=truths,
                beam_id=beam_id,
            )
            evaluations[f"{method}/{role}"] = result
    csi_overall, csi_periods, roi_samples = _csi_metrics(
        scene=scene,
        case=case,
        case_root=case_root,
        branch_records=branch_records,
        conditions=conditions,
    )
    return {
        "metadata": _manifest_metadata(case, case_root, scene),
        "truths": truths,
        "evaluations": evaluations,
        "input_hashes": input_hashes,
        "paired_input_audit": paired_input_audit,
        "csi_overall": csi_overall,
        "csi_periods": csi_periods,
        "csi_target_roi_samples": roi_samples,
    }


def _summary_rows(scene_results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scene_result in scene_results:
        scene = scene_result["metadata"]["scene"]
        for key, evaluation in scene_result["evaluations"].items():
            method, role = key.split("/", 1)
            metrics = evaluation["task"]["metrics"]
            cfar = metrics.get("unique_union_hit_cells", {})
            errors = metrics.get("parameter_errors", {})
            radial = errors.get("radial_velocity", {})
            rows.append({
                "scene": scene,
                "method_id": method,
                "comparison_class": "evaluator_only_current_control" if method == CONTROL else "selected_baseline",
                "role": role,
                "evaluator_mode": evaluation["evaluator_mode"],
                "production_status": evaluation["status"],
                "production_returncode": evaluation["production_returncode"],
                "production_elapsed_sec_single_run": evaluation["production_elapsed_sec"],
                "detections": metrics.get("final_detections", evaluation["detection_rows"]),
                "truth_opportunities": metrics.get("truth_opportunities"),
                "correct_detections": metrics.get("unique_correct_detections"),
                "misses": metrics.get("misses"),
                "false_detections_or_associations": metrics.get("false_detections", metrics.get("false_associations")),
                "correct_confirmed_track_associations": metrics.get("correct_track_associations"),
                "h0_false_confirmed_track_period_associations": metrics.get("false_confirmed_tracks"),
                "cfar_unique_union_audit_status": cfar.get("status"),
                "cfar_unique_union_hit_cells": cfar.get("count"),
                "valid_cut_count": cfar.get("valid_cut_count"),
                "observed_hit_fraction_not_pfa": cfar.get("observed_hit_fraction"),
                "empirical_pfa": None,
                "empirical_pfa_status": "NOT_EVALUABLE_NO_TRUTH_FILTERED_FALSE_CUT_ESTIMATOR",
                "configured_cfar_pf": (
                    evaluation["cfar_audits"][0].get("configured_pfa")
                    if evaluation["cfar_audits"] else None
                ),
                "position_2d_rmse_m": errors.get("position_2d_rmse_m"),
                "angle_rmse_deg": errors.get("angle_rmse_deg"),
                "ground_speed_rmse_mps": errors.get("ground_speed_rmse_mps"),
                "folded_doppler_frequency_rmse_hz": errors.get("folded_doppler_frequency_rmse_hz"),
                "radial_velocity_rmse_mps": radial.get("rmse_mps"),
                "radial_velocity_status": radial.get("status"),
                "radial_velocity_unresolved_count": radial.get("unresolved_count"),
                "single_seed_performance_verified": False,
                "target_only_detection_control": role == "TO",
            })
    return rows


def _period_rows(scene_results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scene_result in scene_results:
        scene = scene_result["metadata"]["scene"]
        for key, evaluation in scene_result["evaluations"].items():
            method, role = key.split("/", 1)
            metrics = evaluation["task"]["metrics"]
            cfar_by_period = {
                (int(audit["period_id"]), int(audit["beam_id"])): audit
                for audit in evaluation["cfar_audits"]
            }
            association_by_period = {
                int(row["period_id"]): row
                for row in evaluation["task"]["association_evidence"]
            }
            detection_counts: dict[int, int] = {}
            for detection in evaluation["detections"]:
                period = _as_int(detection.get("period_id"))
                if period is not None:
                    detection_counts[period] = detection_counts.get(period, 0) + 1
            for truth in scene_result["truths"]:
                period = int(truth["period_id"])
                assoc = association_by_period.get(period, {})
                audit = cfar_by_period.get((period, int(truth["beam_id"])), {})
                rows.append({
                    "scene": scene,
                    "method_id": method,
                    "role": role,
                    "period_id": period,
                    "beam_id": truth["beam_id"],
                    "target_velocity_mps": scene_result["metadata"]["case_parameters"].get("target_velocity_mps"),
                    "target_range_bin": truth.get("range_bin"),
                    "target_truth_doppler_row": assoc.get("truth_row"),
                    "target_detected": assoc.get("matched"),
                    "target_detection_id": assoc.get("det_id"),
                    "miss_reason": assoc.get("miss_reason"),
                    "detection_rows": detection_counts.get(period, 0),
                    "cfar_dynamic_branch_hits": audit.get("component_hit_cells", {}).get("union_dynamic_in_band"),
                    "cfar_full_branch_hits": audit.get("component_hit_cells", {}).get("union_full_band"),
                    "cfar_final_unique_union_hits": audit.get("unique_union_hit_cells"),
                    "cfar_valid_cuts": audit.get("valid_cut_count"),
                    "cfar_h0_observed_hit_fraction_not_pfa": audit.get("observed_hit_fraction") if role == "OFF" else None,
                    "cfar_map_log_geometry_verified": audit.get("audit_verified"),
                    "range_error_bins": assoc.get("range_delta_bins"),
                    "circular_doppler_error_bins": assoc.get("doppler_delta_bins"),
                    "signed_circular_doppler_error_bins": assoc.get("signed_doppler_delta_bins"),
                    "folded_doppler_frequency_error_hz": assoc.get("folded_doppler_frequency_error_hz"),
                    "radial_velocity_error_mps": assoc.get("radial_velocity_error_mps"),
                    "radial_velocity_status": assoc.get("radial_velocity_status"),
                    "angle_error_deg": assoc.get("angle_error_deg"),
                    "position_error_m": assoc.get("position_error_m"),
                    "ground_speed_error_mps": assoc.get("ground_speed_error_mps"),
                    "confirmed_matched_track_id": assoc.get("track_id"),
                })
    return rows


def _associations_rows(scene_results: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    task_rows: list[dict[str, Any]] = []
    track_rows: list[dict[str, Any]] = []
    for scene_result in scene_results:
        scene = scene_result["metadata"]["scene"]
        for key, evaluation in scene_result["evaluations"].items():
            method, role = key.split("/", 1)
            for row in evaluation["task"]["association_evidence"]:
                task_rows.append({"scene": scene, "method_id": method, "role": role, **row})
            for row in evaluation["strict_track_associations"]:
                track_rows.append({"scene": scene, "method_id": method, "role": role, **row})
    return task_rows, track_rows


def _branch_inventory_rows(scene_results: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inventory: list[dict[str, Any]] = []
    adapter_rows: list[dict[str, Any]] = []
    for scene_result in scene_results:
        scene = scene_result["metadata"]["scene"]
        for evaluation in scene_result["evaluations"].values():
            production_xml = Path(str(evaluation["production_xml"]))
            production_log = Path(str(evaluation["production_log"]))
            configure_log = production_xml.with_suffix(".configure.log")
            inventory.append({
                "scene": scene,
                "method_id": evaluation["method_id"],
                "role": evaluation["role"],
                "branch_name": evaluation["branch_name"],
                "production_status": evaluation["status"],
                "production_returncode": evaluation["production_returncode"],
                "production_elapsed_sec_single_run": evaluation["production_elapsed_sec"],
                "adapter_status": evaluation["adapter_status"],
                "adapter_row_count": len(evaluation["adapter_rows"]),
                "production_xml_relative_to_case": _case_relative(production_xml, Path(evaluation["result_dir"]).parents[2]),
                "production_xml_sha256": _hash_record(production_xml)["sha256"],
                "production_log_relative_to_case": _case_relative(production_log, Path(evaluation["result_dir"]).parents[2]),
                "production_log_sha256": _hash_record(production_log)["sha256"],
                "configure_log_sha256": _hash_record(configure_log)["sha256"] if configure_log.is_file() else None,
                "track_debug_dir": evaluation["track_debug_dir"],
            })
            adapter_rows.extend({"scene": scene, **row} for row in evaluation["adapter_rows"])
    return inventory, adapter_rows


def _pairwise_rows(scene_results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for scene_result in scene_results:
        scene = scene_result["metadata"]["scene"]
        methods = tuple(scene_result["metadata"].get("selected_methods") or METHODS)
        evaluations = {
            tuple(key.split("/", 1)): value
            for key, value in scene_result["evaluations"].items()
        }
        result.extend(_common_opportunity_rows(evaluations, scene=scene, methods=methods))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normal-case-manifest", required=True, type=Path)
    parser.add_argument("--challenge-case-manifest", type=Path)
    parser.add_argument("--normal-scene-label", default="normal_seed101_v6p7")
    parser.add_argument("--challenge-scene-label", default="low_speed_seed101_v0p5")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    scene_results = [evaluate_case_manifest(
        args.normal_case_manifest, args.normal_scene_label
    )]
    if args.challenge_case_manifest is not None:
        scene_results.append(evaluate_case_manifest(
            args.challenge_case_manifest, args.challenge_scene_label
        ))
    methods = tuple(scene_results[0]["metadata"].get("selected_methods") or METHODS)
    if any(
        tuple(scene["metadata"].get("selected_methods") or METHODS) != methods
        for scene in scene_results[1:]
    ):
        raise ValueError("all selected scenes must use the same Production method subset")
    conditions = (CONTROL, *methods)
    _write_csv(output / "summary.csv", _summary_rows(scene_results))
    _write_csv(output / "period_metrics.csv", _period_rows(scene_results))
    _write_csv(output / "pairwise_common_opportunities.csv", _pairwise_rows(scene_results))
    _write_csv(output / "target_associations.csv", _associations_rows(scene_results)[0])
    _write_csv(output / "strict_track_associations.csv", _associations_rows(scene_results)[1])
    csi_period_rows = [row for scene in scene_results for row in scene["csi_periods"]]
    f1f2_audits = audit_shared_f1f2_inputs(csi_period_rows, conditions)
    _write_csv(output / "csi_results.csv", [row for scene in scene_results for row in scene["csi_overall"]])
    _write_csv(output / "csi_period_metrics.csv", csi_period_rows)
    _write_csv(output / "f1f2_input_audit.csv", f1f2_audits)
    _write_csv(output / "csi_target_roi_samples.csv", [row for scene in scene_results for row in scene["csi_target_roi_samples"]])
    _write_csv(output / "input_hashes.csv", [row for scene in scene_results for row in scene["input_hashes"]])
    paired_input_audits = [row for scene in scene_results for row in scene["paired_input_audit"]]
    _write_csv(output / "paired_input_audit.csv", paired_input_audits)
    inventory_rows, adapter_rows = _branch_inventory_rows(scene_results)
    _write_csv(output / "branch_inventory.csv", inventory_rows)
    _write_csv(output / "adapter_records.csv", adapter_rows)
    _write_csv(
        output / "cfar_union_audit.csv",
        [
            row
            for scene in scene_results
            for evaluation in scene["evaluations"].values()
            for row in evaluation["cfar_audits"]
        ],
    )
    metadata = {
        "schema_version": 1,
        "stage": (
            "PGRCC Phase R16 minimal C1 Gamma=1 Native CUDA baseline comparison"
            if "C1" in methods else "PGRCC selected-method Native CUDA baseline comparison"
        ),
        "evaluator_status": "evaluated",
        "scenes": [scene["metadata"] for scene in scene_results],
        "method_ids": list(methods),
        "control_id": CONTROL,
        "performance_verified": False,
        "single_seed_limit": "3 periods are within one seed and are not independent repetitions",
        "pfa_status": "NOT_EVALUABLE; H0 observed hit fraction is descriptive and is not empirical Pfa",
        "f1f2_input_audit_status": (
            f"verified_identical_across_{'_'.join(conditions)}"
            if f1f2_audits and all(row["audit_verified"] for row in f1f2_audits)
            else "NOT_EVALUABLE_OR_INPUT_MISMATCH"
        ),
        "f1f2_input_audit_rows": len(f1f2_audits),
        "paired_area_input_audit_status": (
            "verified_off_matches_area_background_and_on_matches_writer"
            if paired_input_audits and all(row["audit_verified"] for row in paired_input_audits)
            else "NOT_EVALUABLE_OR_INPUT_MISMATCH"
        ),
        "paired_area_input_audit_rows": len(paired_input_audits),
        "evaluator": {
            "path": str(Path(__file__).resolve()),
            "sha256": _hash_record(Path(__file__))["sha256"],
        },
        "method_results": [
            {
                "scene": scene["metadata"]["scene"],
                "method_role": key,
                "production_status": evaluation["status"],
                "production_returncode": evaluation["production_returncode"],
                "production_elapsed_sec_single_run": evaluation["production_elapsed_sec"],
                "cfar_audit_verified": all(row["audit_verified"] for row in evaluation["cfar_audits"]),
                "task_metrics": evaluation["task"]["metrics"],
                "strict_track_association_count": len(evaluation["strict_track_associations"]),
            }
            for scene in scene_results
            for key, evaluation in scene["evaluations"].items()
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True, default=list) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(output), "branch_evaluations": len(metadata["method_results"]), "status": "evaluated"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
