"""Schema and lightweight summaries for TrackManager association audit rows."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


REQUIRED_ASSOCIATION_FIELDS = frozenset(
    {
        "schema_version",
        "case_id",
        "run_id",
        "result_id",
        "period_id",
        "frame_utc",
        "track_id",
        "track_state_before",
        "track_state_after",
        "det_index",
        "detection_id",
        "candidate_rank",
        "candidate_count",
        "candidate_track_count",
        "candidate_detection_ids",
        "candidate_track_ids",
        "innovation_e",
        "innovation_n",
        "euclidean_dist_m",
        "euclidean_gate_m",
        "mahalanobis_d2",
        "mahalanobis_gate_d2",
        "speed_innovation_mps",
        "heading_innovation_deg",
        "distance_mode",
        "assignment_mode",
        "assignment_column",
        "assigned",
        "gate_passed",
        "cost_beats_dummy",
        "total_cost",
        "dummy_cost",
        "reject_reason",
        "assignment_outcome",
        "lifecycle_event",
        "angle_innovation_available",
        "velocity_innovation_available",
        "unavailable_fields",
    }
)

_INT_FIELDS = {
    "schema_version",
    "period_id",
    "track_id",
    "det_index",
    "detection_id",
    "candidate_rank",
    "candidate_count",
    "candidate_track_count",
    "assignment_column",
    "assigned",
    "gate_passed",
    "cost_beats_dummy",
    "angle_innovation_available",
    "velocity_innovation_available",
}
_FLOAT_FIELDS = {
    "frame_utc",
    "innovation_e",
    "innovation_n",
    "euclidean_dist_m",
    "euclidean_gate_m",
    "mahalanobis_d2",
    "mahalanobis_gate_d2",
    "speed_innovation_mps",
    "heading_innovation_deg",
    "total_cost",
    "dummy_cost",
}


def validate_association_row(row: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(REQUIRED_ASSOCIATION_FIELDS - set(row))
    if missing:
        raise ValueError(f"missing association fields: {', '.join(missing)}")
    if int(row["schema_version"]) != 2:
        raise ValueError(f"unsupported association schema_version={row['schema_version']}")
    for field in ("track_state_before", "track_state_after", "assignment_outcome", "lifecycle_event"):
        if not str(row[field]).strip():
            raise ValueError(f"{field} must be non-empty")
    normalized = dict(row)
    for field in _INT_FIELDS:
        normalized[field] = int(row[field])
    for field in _FLOAT_FIELDS:
        normalized[field] = float(row[field])
    if normalized["candidate_count"] < 0 or normalized["candidate_track_count"] < 0:
        raise ValueError("candidate counts must be non-negative")
    if normalized["candidate_count"] == 0 and normalized["det_index"] >= 0:
        raise ValueError("det_index cannot be present when candidate_count is zero")
    if normalized["candidate_count"] > 0 and not str(row["candidate_detection_ids"]).strip():
        raise ValueError("candidate_detection_ids must be explicit")
    return normalized


def summarize_candidate_competition(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = [validate_association_row(row) for row in rows]
    if not normalized:
        return {"candidate_count": 0, "assigned_det_index": None, "reject_reasons": []}
    candidate_count = max(int(row["candidate_count"]) for row in normalized)
    assigned = [row for row in normalized if int(row["assigned"]) == 1]
    reasons: list[str] = []
    for row in normalized:
        reason = str(row["reject_reason"])
        if reason not in {"None", "", "NOT_EVALUABLE"} and reason not in reasons:
            reasons.append(reason)
    return {
        "candidate_count": candidate_count,
        "assigned_det_index": int(assigned[0]["det_index"]) if assigned else None,
        "reject_reasons": reasons,
    }
