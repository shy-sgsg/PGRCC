"""Schema contracts for the read-only TrackManager association audit."""

from __future__ import annotations

import pytest

from scripts.track_manager_association_audit import (
    REQUIRED_ASSOCIATION_FIELDS,
    summarize_candidate_competition,
    validate_association_row,
)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": 2,
        "case_id": "case",
        "run_id": "run",
        "result_id": "GMTI07",
        "period_id": 7,
        "frame_utc": 100.5,
        "track_id": 3,
        "track_state_before": "Confirmed",
        "track_state_after": "Confirmed",
        "det_index": 1,
        "detection_id": 0,
        "candidate_rank": 1,
        "candidate_count": 2,
        "candidate_track_count": 1,
        "candidate_detection_ids": "0;1",
        "candidate_track_ids": "3",
        "innovation_e": 2.0,
        "innovation_n": -1.0,
        "euclidean_dist_m": 2.236,
        "euclidean_gate_m": 300.0,
        "mahalanobis_d2": 0.5,
        "mahalanobis_gate_d2": 9.21,
        "speed_innovation_mps": 0.0,
        "heading_innovation_deg": 0.0,
        "distance_mode": "MahalanobisSquared",
        "assignment_mode": "Hungarian",
        "assignment_column": 1,
        "assigned": 1,
        "gate_passed": 1,
        "cost_beats_dummy": 1,
        "total_cost": 0.5,
        "dummy_cost": 1.5,
        "reject_reason": "None",
        "assignment_outcome": "assigned",
        "lifecycle_event": "matched",
        "angle_innovation_available": 0,
        "velocity_innovation_available": 0,
        "unavailable_fields": "heading_innovation_deg;detection_velocity_innovation_mps",
    }
    row.update(overrides)
    return row


def test_required_fields_cover_candidate_and_lifecycle_evidence() -> None:
    row = validate_association_row(_row())
    assert REQUIRED_ASSOCIATION_FIELDS <= set(row)
    assert row["track_state_before"] == "Confirmed"
    assert row["track_state_after"] == "Confirmed"
    assert row["candidate_count"] == 2
    assert row["candidate_detection_ids"] == "0;1"
    assert row["unavailable_fields"]


def test_candidate_summary_preserves_competition_and_reject_reasons() -> None:
    rows = [
        _row(det_index=0, candidate_rank=2, assigned=0, assignment_outcome="not_selected"),
        _row(det_index=1, candidate_rank=1),
        _row(
            det_index=2,
            candidate_rank=3,
            candidate_count=3,
            assigned=0,
            gate_passed=0,
            cost_beats_dummy=0,
            reject_reason="EuclideanGate",
            assignment_outcome="gate_rejected",
        ),
    ]
    summary = summarize_candidate_competition(rows)
    assert summary["candidate_count"] == 3
    assert summary["assigned_det_index"] == 1
    assert summary["reject_reasons"] == ["EuclideanGate"]


def test_missing_required_state_or_candidate_id_is_rejected() -> None:
    row = _row()
    del row["track_state_after"]
    with pytest.raises(ValueError, match="track_state_after"):
        validate_association_row(row)
