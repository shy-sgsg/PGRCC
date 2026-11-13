#!/usr/bin/env python3
"""Evidence-first audit of production TrackManager ID switches.

This module never associates a detection itself and never changes tracker
thresholds.  It only joins the production debug CSVs, the production
detection snapshots, the existing truth file, and the output payloads so a
switch can be inspected after the fact.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


_CLASSIFICATIONS = (
    "miss_to_reacquisition",
    "multiple_candidate_competition",
    "false_track_takeover",
    "gate_boundary_crossing",
    "position_velocity_jump",
    "lifecycle_transition",
    "track_confirmation_or_reset",
    "unclassifiable_production_fields",
    "other",
)

_V2_REQUIRED_ATTRIBUTION_FIELDS = (
    "candidate_count",
    "candidate_detection_ids",
    "candidate_track_ids",
    "euclidean_dist_m",
    "euclidean_gate_m",
    "assignment_outcome",
    "lifecycle_event",
)

# Keep the output schema stable even when a particular production version did
# not export one of the optional association fields.
AUDIT_COLUMNS = (
    "scene_id",
    "case_id",
    "branch",
    "previous_period_id",
    "period_id",
    "truth_target_id",
    "previous_truth_target_id",
    "previous_detection_id",
    "detection_id",
    "previous_track_id",
    "current_track_id",
    "association_schema_version",
    "candidate_ids",
    "candidate_track_ids",
    "candidate_rank",
    "candidate_count",
    "innovation_m",
    "residual_m",
    "association_distance_m",
    "gate_threshold_m",
    "mahalanobis_d2",
    "mahalanobis_gate_d2",
    "assignment_mode",
    "assignment_outcome",
    "reject_reason",
    "lifecycle_event",
    "unavailable_fields",
    "association_source_file",
    "association_source_row",
    "missing_production_fields",
    "previous_confirmation_state",
    "confirmation_state",
    "lost",
    "reacquired",
    "merge_evidence",
    "split_evidence",
    "classification",
    "reason",
    "source_file",
    "source_row",
    "previous_source_row",
    "current_source_row",
    "detection_source_file",
    "previous_detection_source_file",
    "truth_source_file",
    "evidence_json",
)


def _finite(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: object) -> int | None:
    parsed = _finite(value)
    return int(parsed) if parsed is not None else None


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "confirmed"}


def _period_id(row: Mapping[str, object], *, default: int = -1) -> int:
    direct = _integer(row.get("period_id"))
    if direct is not None:
        return direct
    match = re.search(r"(\d+)$", str(row.get("result_id", "")))
    if match is None:
        return default
    return max(0, int(match.group(1)) - 1)


def _csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _csv_row_number(index: int) -> int:
    # CSV line one is the header.
    return index + 2


def _first_file(root: Path, filename: str) -> Path | None:
    direct = root / filename
    if direct.is_file():
        return direct
    matches = sorted(root.rglob(filename)) if root.is_dir() else []
    return matches[0] if matches else None


def _event_flags(events: Sequence[Mapping[str, object]], period: int, track_id: str) -> dict[str, object]:
    selected = [
        row for row in events
        if _period_id(row) == period and str(row.get("track_id", "")).strip() == track_id
    ]
    event_names = {str(row.get("event", "")).strip().lower() for row in selected}
    states = {str(row.get("state", "")).strip() for row in selected if row.get("state")}
    return {
        "lost": int(bool(event_names & {"miss", "lost", "delete", "deleted", "coast"})),
        "reacquired": int(bool(event_names & {"reacquire", "reacquired", "reacquisition"})),
        "event": "|".join(sorted(event_names)),
        "states": "|".join(sorted(states)),
        "rows": selected,
    }


def _return_classification(
    classification: str,
    reason: str,
    *,
    evidence: Mapping[str, object] | None = None,
    missing_production_fields: Sequence[str] | None = None,
) -> dict[str, object]:
    if classification not in _CLASSIFICATIONS:
        raise ValueError(f"unknown switch classification: {classification}")
    missing = sorted({str(value) for value in (missing_production_fields or ()) if str(value).strip()})
    return {
        "classification": classification,
        "reason": reason,
        "evidence": dict(evidence or {}),
        "missing_production_fields": missing,
    }


def _is_blank(value: object) -> bool:
    return value is None or not str(value).strip() or str(value).strip().lower() in {
        "na", "n/a", "not_evaluable", "none",
    }


def _missing_production_fields(row: Mapping[str, object], context: Mapping[str, object]) -> list[str]:
    """Return explicit gaps in the versioned production association evidence."""

    schema = str(row.get("_association_schema", context.get("association_schema", ""))).strip().lower()
    if schema == "v2":
        missing = [field for field in _V2_REQUIRED_ATTRIBUTION_FIELDS if _is_blank(row.get(field))]
        candidate_count = _integer(row.get("candidate_count"))
        if candidate_count is not None and candidate_count > 0 and _is_blank(row.get("candidate_detection_ids")):
            if "candidate_detection_ids" not in missing:
                missing.append("candidate_detection_ids")
        return sorted(set(missing))
    if schema == "legacy":
        explicit = row.get("_missing_production_fields", context.get("missing_production_fields"))
        if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes, bytearray)):
            return sorted({str(value) for value in explicit if str(value).strip()})
        if explicit is not None and str(explicit).strip():
            return sorted({value for value in str(explicit).split("|") if value.strip()})
        return ["track_association_audit_v2.csv"]
    return []


def _candidate_values(value: object) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        raw = [str(item) for item in value]
    else:
        raw = re.split(r"[|;]", str(value or ""))
    return [item.strip() for item in raw if item.strip()]


def classify_switch(
    previous: Mapping[str, object],
    current: Mapping[str, object],
    context: Mapping[str, object],
) -> dict[str, object]:
    """Classify only mechanisms explicitly represented by recorded fields."""

    missing_fields = _missing_production_fields(current, context)
    if missing_fields and str(current.get("_association_schema", context.get("association_schema", ""))).strip().lower() == "v2":
        return _return_classification(
            "unclassifiable_production_fields",
            "production association evidence is missing fields required for mechanism attribution",
            evidence={
                "missing_production_fields": missing_fields,
                "association_schema": current.get("_association_schema", context.get("association_schema")),
            },
            missing_production_fields=missing_fields,
        )

    previous_missed = (
        previous.get("matched_this_frame") is not None
        and not _truthy(previous.get("matched_this_frame"))
    ) or _truthy(previous.get("lost"))
    current_reacquired = _truthy(current.get("reacquired"))
    if previous_missed and current_reacquired:
        return _return_classification(
            "miss_to_reacquisition",
            "previous target association was recorded as missed/lost and the current record was explicitly reacquired",
            evidence={"previous_lost": previous.get("lost"), "current_reacquired": current.get("reacquired")},
        )

    candidate_ids = current.get("candidate_ids")
    candidate_count = _integer(current.get("candidate_count"))
    if candidate_count is not None and candidate_count > 1:
        return _return_classification(
            "multiple_candidate_competition",
            "the current association record exports more than one candidate",
            evidence={"candidate_count": candidate_count, "candidate_ids": candidate_ids},
        )
    candidate_values = _candidate_values(candidate_ids)
    if len(set(candidate_values)) > 1:
        return _return_classification(
            "multiple_candidate_competition",
            "the current association record exports multiple candidate IDs",
            evidence={"candidate_count": len(set(candidate_values)), "candidate_ids": candidate_values},
        )

    if _truthy(context.get("false_track_takeover")) or (
        current.get("truth_associated") is not None
        and not _truthy(current.get("truth_associated"))
        and _truthy(previous.get("truth_associated"))
    ):
        return _return_classification(
            "false_track_takeover",
            "the current track was explicitly marked as not truth-associated while the prior target slot was associated",
            evidence={
                "previous_truth_associated": previous.get("truth_associated"),
                "current_truth_associated": current.get("truth_associated"),
                "context_false_track_takeover": context.get("false_track_takeover"),
            },
        )

    distance = _finite(current.get("association_distance_m"))
    gate = _finite(current.get("gate_threshold_m"))
    gate_passed = current.get("gate_passed")
    reject_reason = str(current.get("reject_reason", "")).strip().lower()
    assignment_outcome = str(current.get("assignment_outcome", "")).strip().lower()
    gate_rejected = (
        not _is_blank(gate_passed)
        and not _truthy(gate_passed)
    ) or assignment_outcome == "gate_rejected" or any(
        marker in reject_reason for marker in ("gate", "euclidean", "mahalanobis", "chi2")
    )
    near_gate = (
        distance is not None
        and gate is not None
        and gate > 0.0
        and abs(distance - gate) <= max(1.0, 0.01 * gate)
    )
    if (distance is not None and gate is not None and distance > gate) or gate_rejected or near_gate:
        return _return_classification(
            "gate_boundary_crossing",
            "recorded association distance or production gate outcome is at the gate boundary",
            evidence={
                "association_distance_m": distance,
                "gate_threshold_m": gate,
                "gate_passed": current.get("gate_passed"),
                "reject_reason": current.get("reject_reason"),
                "assignment_outcome": current.get("assignment_outcome"),
            },
        )

    position_jump = _finite(current.get("position_jump_m"))
    velocity_jump = _finite(current.get("velocity_jump_mps"))
    jump_threshold = _finite(current.get("recorded_jump_threshold_m"))
    velocity_threshold = _finite(current.get("recorded_velocity_jump_threshold_mps"))
    if (
        position_jump is not None
        and jump_threshold is not None
        and position_jump > jump_threshold
    ) or (
        velocity_jump is not None
        and velocity_threshold is not None
        and velocity_jump > velocity_threshold
    ):
        return _return_classification(
            "position_velocity_jump",
            "recorded position/velocity jump exceeds its recorded comparison threshold",
            evidence={
                "position_jump_m": position_jump,
                "recorded_jump_threshold_m": jump_threshold,
                "velocity_jump_mps": velocity_jump,
                "recorded_velocity_jump_threshold_mps": velocity_threshold,
            },
        )

    previous_state = str(previous.get("confirmation_state", "")).strip().lower()
    current_state = str(current.get("confirmation_state", "")).strip().lower()
    event = str(current.get("event", "")).strip().lower()
    lifecycle_event = str(current.get("lifecycle_event", "")).strip().lower()
    if lifecycle_event in {
        "reacquired",
        "confirmed",
        "coasted",
        "lost_deleted",
        "matched_other_detection",
        "candidate_rejected",
    } or (
        current.get("_association_schema") == "v2"
        and current_state
        and str(current.get("track_state_before", "")).strip().lower() != current_state
    ):
        return _return_classification(
            "lifecycle_transition",
            "versioned production association audit records an explicit lifecycle or state transition",
            evidence={
                "track_state_before": current.get("track_state_before"),
                "track_state_after": current.get("track_state_after"),
                "lifecycle_event": current.get("lifecycle_event"),
            },
        )
    if (
        event in {"reset", "delete", "deleted", "confirm", "confirmation", "reacquire", "reacquired"}
        or (previous_state and current_state and previous_state != current_state)
    ):
        return _return_classification(
            "track_confirmation_or_reset",
            "recorded confirmation state or lifecycle event changed across the switch",
            evidence={
                "previous_confirmation_state": previous.get("confirmation_state"),
                "confirmation_state": current.get("confirmation_state"),
                "event": current.get("event"),
            },
        )

    if missing_fields:
        return _return_classification(
            "unclassifiable_production_fields",
            "production association evidence is missing fields required for mechanism attribution",
            evidence={
                "missing_production_fields": missing_fields,
                "association_schema": current.get("_association_schema", context.get("association_schema")),
            },
            missing_production_fields=missing_fields,
        )

    return _return_classification(
        "other",
        "insufficient_debug_fields_for_mechanism_classification",
        evidence={
            "available_previous_fields": sorted(str(key) for key in previous),
            "available_current_fields": sorted(str(key) for key in current),
            "available_context_fields": sorted(str(key) for key in context),
        },
    )


def _truth_rows(truth_path: Path | None) -> tuple[dict[int, list[dict[str, str]]], Path | None]:
    if truth_path is None:
        return {}, None
    path = Path(truth_path)
    if path.is_dir():
        for name in ("truth_targets_by_beam.csv", "truth_targets_by_period.csv"):
            candidate = path / name
            if candidate.is_file():
                path = candidate
                break
    rows = _csv_rows(path)
    by_period: dict[int, list[dict[str, str]]] = {}
    for row in rows:
        if "visible" in row and not _truthy(row.get("visible")):
            continue
        period = _period_id(row)
        if period >= 0:
            by_period.setdefault(period, []).append(row)
    return by_period, path if path.is_file() else None


def _target_match(
    detection: Mapping[str, object],
    truth: Mapping[str, object],
) -> tuple[bool, float]:
    """Use the existing production evaluator's recorded truth-match contract."""

    from scripts import run_track_manager_e2e as e2e

    de = _finite(detection.get("e", detection.get("new_e")))
    dn = _finite(detection.get("n", detection.get("new_n")))
    te = _finite(truth.get("e_mid"))
    tn = _finite(truth.get("n_mid"))
    if None in {de, dn, te, tn}:
        return False, math.inf
    distance = math.hypot(float(de) - float(te), float(dn) - float(tn))
    detection_range = _finite(detection.get("range", detection.get("range_m")))
    truth_range = _finite(truth.get("range_m"))
    range_error = (
        abs(float(detection_range) - float(truth_range))
        if detection_range is not None and truth_range is not None
        else math.nan
    )
    matched = distance <= e2e.MAX_POSITION_MATCH_M and (
        not math.isfinite(range_error) or range_error <= e2e.MAX_RANGE_MATCH_M
    )
    return bool(matched), distance


def _association_rows(track_debug_dir: Path) -> dict[tuple[int, str, int], dict[str, str]]:
    """Read v2 production association evidence, with an explicit legacy fallback."""

    v2_path = _first_file(track_debug_dir, "track_association_audit_v2.csv")
    v2_rows = _csv_rows(v2_path) if v2_path else []
    result: dict[tuple[int, str, int], dict[str, str]] = {}
    if v2_rows:
        for index, source_row in enumerate(v2_rows):
            row = dict(source_row)
            row["_association_schema"] = "v2"
            row["_association_source_file"] = str(v2_path)
            row["_association_source_row"] = str(_csv_row_number(index))
            row["association_schema_version"] = row.get("schema_version", "")
            row["candidate_ids"] = row.get("candidate_detection_ids", "")
            row["association_distance_m"] = row.get("euclidean_dist_m", "")
            row["gate_threshold_m"] = row.get("euclidean_gate_m", "")
            innovation_e = _finite(row.get("innovation_e"))
            innovation_n = _finite(row.get("innovation_n"))
            if innovation_e is not None and innovation_n is not None:
                row["innovation_m"] = str(math.hypot(innovation_e, innovation_n))
            row["residual_m"] = row.get("euclidean_dist_m", "")
            row["confirmation_state"] = row.get("track_state_after", "")
            row["event"] = row.get("lifecycle_event", "")
            row["reacquired"] = int(str(row.get("lifecycle_event", "")).strip().lower() == "reacquired")
            row["lost"] = int(str(row.get("lifecycle_event", "")).strip().lower() in {"coasted", "lost_deleted"})
            det_index = _integer(row.get("det_index"))
            period = _period_id(row)
            track_id = str(row.get("track_id", "")).strip()
            if period >= 0 and track_id and det_index is not None and det_index >= 0:
                result[(period, track_id, det_index)] = row
        if result:
            return result

    path = _first_file(track_debug_dir, "track_association_accepts.csv")
    for index, source_row in enumerate(_csv_rows(path) if path else []):
        row = dict(source_row)
        row["_association_schema"] = "legacy"
        row["_association_source_file"] = str(path) if path else ""
        row["_association_source_row"] = str(_csv_row_number(index))
        row["_missing_production_fields"] = "track_association_audit_v2.csv"
        row["association_schema_version"] = ""
        row["candidate_ids"] = ""
        row["candidate_count"] = ""
        row["association_distance_m"] = row.get("euclidean_dist_m", "")
        row["gate_threshold_m"] = row.get("euclidean_gate_m", "")
        row["residual_m"] = row.get("euclidean_dist_m", "")
        row["assignment_outcome"] = "assigned" if _truthy(row.get("post_assignment_accepted")) else ""
        row["reject_reason"] = ""
        row["lifecycle_event"] = ""
        row["confirmation_state"] = row.get("track_state_before", "")
        row["event"] = ""
        det_index = _integer(row.get("det_index"))
        period = _period_id(row)
        track_id = str(row.get("track_id", "")).strip()
        if period >= 0 and track_id and det_index is not None:
            result[(period, track_id, det_index)] = row
    return result


def _state_rows(track_debug_dir: Path) -> dict[tuple[int, str], dict[str, str]]:
    path = _first_file(track_debug_dir, "track_states.csv")
    result: dict[tuple[int, str], dict[str, str]] = {}
    for row in _csv_rows(path) if path else []:
        period = _period_id(row)
        track_id = str(row.get("track_id", "")).strip()
        if period < 0 or not track_id:
            continue
        # Prefer an output/matched row when the debug stream contains several
        # lifecycle records for the same track and period.
        key = (period, track_id)
        old = result.get(key)
        if old is None or (_truthy(row.get("is_output")), _truthy(row.get("matched_this_frame"))) > (
            _truthy(old.get("is_output")), _truthy(old.get("matched_this_frame"))
        ):
            result[key] = row
    return result


def audit_track_id_switches(
    track_debug_dir: Path,
    detection_dir: Path,
    payload_path: Path,
    truth_path: Path | None,
) -> list[dict[str, object]]:
    """Return one evidence row for each adjacent-period target ID switch."""

    debug_root = Path(track_debug_dir)
    payload_file = Path(payload_path)
    payload_rows = _csv_rows(payload_file)
    truth_by_period, truth_file = _truth_rows(Path(truth_path) if truth_path is not None else None)
    if not payload_rows or not truth_by_period:
        return []

    detection_files = sorted(Path(detection_dir).glob("detection_results_GMTI*.csv"))
    detections_by_period: dict[int, list[dict[str, str]]] = {}
    detection_sources: dict[tuple[int, int], tuple[Path, int]] = {}
    for detection_file in detection_files:
        match = re.search(r"GMTI(\d+)", detection_file.name)
        if match is None:
            continue
        period = max(0, int(match.group(1)) - 1)
        rows = _csv_rows(detection_file)
        detections_by_period[period] = rows
        for index in range(len(rows)):
            detection_sources[(period, index)] = (detection_file, _csv_row_number(index))

    states = _state_rows(debug_root)
    associations = _association_rows(debug_root)
    events_path = _first_file(debug_root, "track_events.csv")
    events = _csv_rows(events_path) if events_path else []
    event_by_track_period: dict[tuple[int, str], dict[str, object]] = {}
    for period in { _period_id(row) for row in events }:
        for track_id in {str(row.get("track_id", "")).strip() for row in events if _period_id(row) == period}:
            if track_id:
                event_by_track_period[(period, track_id)] = _event_flags(events, period, track_id)

    # Group output payloads by period and retain their original source rows.
    target_candidates: dict[int, list[dict[str, object]]] = {}
    for index, payload in enumerate(payload_rows):
        period = _period_id(payload)
        if period not in truth_by_period:
            continue
        track_id = str(payload.get("track_id", "")).strip()
        if not track_id:
            continue
        det_index = _integer(payload.get("matched_det_index"))
        detection = (
            detections_by_period.get(period, [])[det_index]
            if det_index is not None
            and 0 <= det_index < len(detections_by_period.get(period, []))
            else None
        )
        best_truth: Mapping[str, object] | None = None
        best_distance = math.inf
        if detection is not None:
            for truth in truth_by_period[period]:
                matched, distance = _target_match(detection, truth)
                if matched and distance < best_distance:
                    best_truth = truth
                    best_distance = distance
        if best_truth is None:
            continue
        state = states.get((period, track_id), {})
        association = associations.get((period, track_id, det_index), {}) if det_index is not None else {}
        if not association:
            association = {
                "_association_schema": "legacy",
                "_missing_production_fields": "track_association_audit_v2.csv",
            }
        flags = event_by_track_period.get((period, track_id), {"lost": 0, "reacquired": 0, "event": ""})
        association_event = str(association.get("lifecycle_event", "")).strip()
        event = "|".join(
            value for value in (
                str(flags.get("event", "")).strip(),
                association_event,
            ) if value
        )
        lifecycle_reacquired = association_event.lower() == "reacquired"
        lifecycle_lost = association_event.lower() in {"coasted", "lost_deleted"}
        detection_source = detection_sources.get((period, det_index)) if det_index is not None else None
        target_candidates.setdefault(period, []).append({
            "period_id": period,
            "truth_target_id": str(best_truth.get("target_id", "")),
            "truth_match_distance_m": best_distance,
            "track_id": track_id,
            "det_index": det_index,
            "matched_this_frame": state.get("matched_this_frame", 1),
            "truth_associated": 1,
            "confirmation_state": state.get("state", payload.get("resolved_source", "")),
            "lost": int(bool(flags.get("lost", 0)) or lifecycle_lost),
            "reacquired": int(bool(flags.get("reacquired", 0)) or lifecycle_reacquired),
            "event": event,
            "association_schema": association.get("_association_schema", ""),
            "_association_schema": association.get("_association_schema", ""),
            "association_schema_version": association.get("association_schema_version", ""),
            "candidate_ids": association.get("candidate_ids", ""),
            "candidate_track_ids": association.get("candidate_track_ids", ""),
            "candidate_rank": association.get("candidate_rank", ""),
            "candidate_count": association.get("candidate_count", ""),
            "innovation_m": association.get("innovation_m", ""),
            "residual_m": association.get("residual_m", ""),
            "association_distance_m": association.get("euclidean_dist_m"),
            "gate_threshold_m": association.get("euclidean_gate_m"),
            "mahalanobis_d2": association.get("mahalanobis_d2"),
            "mahalanobis_gate_d2": association.get("mahalanobis_gate_d2"),
            "assignment_mode": association.get("assignment_mode", ""),
            "assignment_outcome": association.get("assignment_outcome", ""),
            "reject_reason": association.get("reject_reason", ""),
            "lifecycle_event": association_event,
            "unavailable_fields": association.get("unavailable_fields", ""),
            "association_source_file": association.get("_association_source_file", ""),
            "association_source_row": association.get("_association_source_row", ""),
            "missing_production_fields": association.get("_missing_production_fields", ""),
            "_missing_production_fields": association.get("_missing_production_fields", ""),
            "source_row": _csv_row_number(index),
            "detection_source": detection_source,
            "truth_source_file": str(truth_file) if truth_file else "",
            "payload": payload,
        })

    # One truth target may be within the existing evaluator's broad match
    # radius of many clutter detections.  The production evaluator resolves
    # that ambiguity by retaining the smallest-distance target match; mirror
    # that established rule before looking for an ID switch.  Do not emit a
    # Cartesian product of every target-compatible payload row.
    best_by_period_target: dict[tuple[int, str], dict[str, object]] = {}
    for period, candidates in target_candidates.items():
        for candidate in candidates:
            key = (period, str(candidate["truth_target_id"]))
            old = best_by_period_target.get(key)
            distance = float(candidate.get("truth_match_distance_m", math.inf))
            old_distance = float(old.get("truth_match_distance_m", math.inf)) if old else math.inf
            if old is None or distance < old_distance:
                best_by_period_target[key] = candidate
    target_candidates = {}
    for (period, _target_id), candidate in best_by_period_target.items():
        target_candidates.setdefault(period, []).append(candidate)

    branch = debug_root.parents[1].name if len(debug_root.parents) > 1 else debug_root.name
    scene_id = branch
    output: list[dict[str, object]] = []
    for period in sorted(target_candidates):
        previous_period = period - 1
        if previous_period not in target_candidates:
            continue
        for current in target_candidates[period]:
            previous_matches = [
                item for item in target_candidates[previous_period]
                if item["truth_target_id"] == current["truth_target_id"]
            ]
            for previous in previous_matches:
                if previous["track_id"] == current["track_id"]:
                    continue
                previous_record = dict(previous)
                current_record = dict(current)
                previous_record["truth_associated"] = 1
                current_record["truth_associated"] = 1
                # Lifecycle evidence can mention a previous miss even if the
                # target payload itself was emitted on both adjacent periods.
                context: dict[str, object] = {
                    "scene_id": scene_id,
                    "branch": branch,
                    "false_track_takeover": False,
                }
                classified = classify_switch(previous_record, current_record, context)
                detection_source = current.get("detection_source")
                previous_detection_source = previous.get("detection_source")
                row: dict[str, object] = {
                    "scene_id": scene_id,
                    "case_id": str(current.get("payload", {}).get("case_id", "")),
                    "branch": branch,
                    "previous_period_id": previous_period,
                    "period_id": period,
                    "truth_target_id": current["truth_target_id"],
                    "previous_truth_target_id": previous["truth_target_id"],
                    "previous_detection_id": previous.get("det_index"),
                    "detection_id": current.get("det_index"),
                    "previous_track_id": previous["track_id"],
                    "current_track_id": current["track_id"],
                    "association_schema_version": current_record.get("association_schema_version"),
                    "candidate_ids": current_record.get("candidate_ids"),
                    "candidate_track_ids": current_record.get("candidate_track_ids"),
                    "candidate_rank": current_record.get("candidate_rank"),
                    "candidate_count": current_record.get("candidate_count"),
                    "innovation_m": current_record.get("innovation_m"),
                    "residual_m": current_record.get("residual_m"),
                    "association_distance_m": current.get("association_distance_m"),
                    "gate_threshold_m": current.get("gate_threshold_m"),
                    "mahalanobis_d2": current.get("mahalanobis_d2"),
                    "mahalanobis_gate_d2": current.get("mahalanobis_gate_d2"),
                    "assignment_mode": current_record.get("assignment_mode"),
                    "assignment_outcome": current_record.get("assignment_outcome"),
                    "reject_reason": current_record.get("reject_reason"),
                    "lifecycle_event": current_record.get("lifecycle_event"),
                    "unavailable_fields": current_record.get("unavailable_fields"),
                    "association_source_file": current_record.get("association_source_file"),
                    "association_source_row": current_record.get("association_source_row"),
                    "missing_production_fields": classified.get("missing_production_fields", []),
                    "previous_confirmation_state": previous.get("confirmation_state"),
                    "confirmation_state": current.get("confirmation_state"),
                    "lost": current.get("lost", 0),
                    "reacquired": current.get("reacquired", 0),
                    "merge_evidence": current_record.get("merge_evidence"),
                    "split_evidence": current_record.get("split_evidence"),
                    "classification": classified["classification"],
                    "reason": classified["reason"],
                    "source_file": str(payload_file),
                    "source_row": current.get("source_row"),
                    "previous_source_row": previous.get("source_row"),
                    "current_source_row": current.get("source_row"),
                    "truth_source_file": current.get("truth_source_file", ""),
                    "evidence_json": json.dumps(classified.get("evidence", {}), sort_keys=True),
                }
                if detection_source is not None:
                    row["detection_source_file"] = str(detection_source[0])
                if previous_detection_source is not None:
                    row["previous_detection_source_file"] = str(previous_detection_source[0])
                output.append(row)
    return output


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def missing_production_field_counts(rows: Sequence[Mapping[str, object]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        values = row.get("missing_production_fields")
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
            fields = [str(value).strip() for value in values]
        else:
            text = str(values or "").strip()
            if not text:
                fields = []
            elif text.startswith("["):
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError:
                    decoded = []
                fields = [str(value).strip() for value in decoded] if isinstance(decoded, list) else []
            else:
                fields = [value.strip() for value in text.replace(";", "|").split("|")]
        for field in fields:
            if field:
                counts[field] += 1
    return {field: int(counts[field]) for field in sorted(counts)}


def reanalyze_retained_audit(
    source_path: Path,
    output_path: Path,
) -> dict[str, object]:
    """Reclassify retained v1 switch rows without rebuilding any association.

    The retained compact v1 audit is the only source of switch identities after
    raw TrackManager CSV cleanup.  This function therefore never joins a new
    detection, never creates a candidate, and never mutates the v1 file.  It
    records the missing v2 association stream explicitly in the new output.
    """

    source = Path(source_path)
    destination = Path(output_path)
    source_rows = _csv_rows(source)
    reanalyzed: list[dict[str, object]] = []
    for index, source_row in enumerate(source_rows):
        current = dict(source_row)
        current["_association_schema"] = "legacy"
        current["_missing_production_fields"] = "track_association_audit_v2.csv"
        previous = {
            "track_id": source_row.get("previous_track_id"),
            "truth_target_id": source_row.get("previous_truth_target_id"),
            "truth_associated": 1,
            "matched_this_frame": 1,
            "confirmation_state": source_row.get("previous_confirmation_state"),
        }
        classified = classify_switch(
            previous,
            current,
            {
                "association_schema": "legacy",
                "missing_production_fields": ["track_association_audit_v2.csv"],
            },
        )
        row = dict(source_row)
        row["association_schema_version"] = ""
        row["association_source_file"] = ""
        row["association_source_row"] = ""
        row["missing_production_fields"] = classified.get("missing_production_fields", [])
        row["classification"] = classified["classification"]
        row["reason"] = classified["reason"]
        row["evidence_json"] = json.dumps(classified.get("evidence", {}), sort_keys=True)
        row["reanalysis_source_row"] = _csv_row_number(index)
        reanalyzed.append(row)

    destination.parent.mkdir(parents=True, exist_ok=True)
    extra_columns = (
        "condition",
        "working_point",
        "seed",
        "delay_error_ns",
        "reanalysis_source_row",
    )
    fields = list(dict.fromkeys([*AUDIT_COLUMNS, *extra_columns]))
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in reanalyzed:
            writer.writerow({field: _csv_value(row.get(field)) for field in fields})

    import hashlib

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    summary = {
        "schema_version": 1,
        "ai_training": False,
        "router_enabled": False,
        "native_four_channel_stap": False,
        "source_file": str(source),
        "source_sha256": digest,
        "source_row_count": len(source_rows),
        "output_file": str(destination),
        "read_only_v1": True,
        "truth_used_to_create_switches": False,
        "production_v2_association_available": False,
        "classification_counts": classification_counts(reanalyzed),
        "missing_production_field_counts": missing_production_field_counts(reanalyzed),
        "limitations": [
            "reclassification consumes retained v1 switch rows only",
            "candidate competition, lifecycle and gate attribution require the removed v2 production association stream",
            "no truth or raw detection row is used to create a new switch",
        ],
    }
    manifest = destination.with_name("reanalysis_manifest.json")
    manifest.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def write_audit(path: Path, rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Write a stable, gap-preserving ID-switch audit CSV."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(AUDIT_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in AUDIT_COLUMNS})
    return {
        "row_count": len(rows),
        "classification_counts": classification_counts(rows),
        "missing_production_field_counts": missing_production_field_counts(rows),
    }


def classification_counts(rows: Sequence[Mapping[str, object]]) -> dict[str, int]:
    counts = Counter(str(row.get("classification", "other")) for row in rows)
    return {label: int(counts.get(label, 0)) for label in _CLASSIFICATIONS}


__all__ = [
    "AUDIT_COLUMNS",
    "audit_track_id_switches",
    "classify_switch",
    "classification_counts",
    "missing_production_field_counts",
    "reanalyze_retained_audit",
    "write_audit",
]
