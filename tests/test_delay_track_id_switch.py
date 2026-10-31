"""Tests for evidence-first TrackManager ID-switch classification."""

from __future__ import annotations

import csv
from pathlib import Path

from scripts.audit_delay_track_id_switch import (
    _association_rows,
    classify_switch,
    missing_production_field_counts,
    reanalyze_retained_audit,
    write_audit,
)


def synthetic_switch_fixtures() -> list[tuple[str, dict[str, object], dict[str, object], dict[str, object]]]:
    base_previous = {"track_id": 1, "truth_target_id": "T0", "matched_this_frame": 1}
    base_current = {"track_id": 2, "truth_target_id": "T0", "matched_this_frame": 1}
    return [
        (
            "miss_to_reacquisition",
            {**base_previous, "matched_this_frame": 0, "lost": 1},
            {**base_current, "reacquired": 1},
            {},
        ),
        (
            "multiple_candidate_competition",
            base_previous,
            {**base_current, "candidate_count": 3, "candidate_ids": "2|8|9"},
            {},
        ),
        (
            "false_track_takeover",
            base_previous,
            {**base_current, "truth_associated": 0},
            {"false_track_takeover": True},
        ),
        (
            "gate_boundary_crossing",
            base_previous,
            {**base_current, "association_distance_m": 301.0, "gate_threshold_m": 300.0},
            {},
        ),
        (
            "position_velocity_jump",
            base_previous,
            {**base_current, "position_jump_m": 500.0, "recorded_jump_threshold_m": 300.0},
            {},
        ),
        (
            "track_confirmation_or_reset",
            {**base_previous, "confirmation_state": "Confirmed"},
            {**base_current, "confirmation_state": "Tentative", "event": "reset"},
            {},
        ),
    ]


def test_classify_switch_covers_six_requested_mechanisms() -> None:
    for label, previous, current, context in synthetic_switch_fixtures():
        result = classify_switch(previous, current, context)
        assert result["classification"] == label


def test_unavailable_evidence_is_other_and_keeps_reason() -> None:
    result = classify_switch({"track_id": 1}, {"track_id": 2}, {})
    assert result["classification"] == "other"
    assert result["reason"] == "insufficient_debug_fields_for_mechanism_classification"


def test_write_audit_preserves_required_provenance_columns(tmp_path: Path) -> None:
    output = tmp_path / "id_switch_audit.csv"
    write_audit(
        output,
        [{
            "scene_id": "scene-1",
            "period_id": 2,
            "truth_target_id": "T0",
            "previous_track_id": 1,
            "current_track_id": 2,
            "classification": "other",
            "reason": "missing_candidate_fields",
            "source_file": "track_states.csv",
            "source_row": 8,
        }],
    )
    header = output.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert {
        "truth_target_id", "previous_track_id", "current_track_id", "candidate_ids",
        "candidate_count", "innovation_m", "residual_m", "association_distance_m",
        "gate_threshold_m", "confirmation_state", "lost", "reacquired", "merge_evidence",
        "split_evidence", "classification", "reason", "source_file", "source_row",
    } <= set(header)


def _v2_association_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": 2,
        "case_id": "case-1",
        "run_id": "run-1",
        "result_id": "GMTI2",
        "period_id": 1,
        "frame_utc": 2.0,
        "track_id": 2,
        "track_state_before": "Confirmed",
        "track_state_after": "Confirmed",
        "det_index": 3,
        "detection_id": 3,
        "candidate_rank": 1,
        "candidate_count": 1,
        "candidate_track_count": 1,
        "candidate_detection_ids": "3",
        "candidate_track_ids": "2",
        "innovation_e": 1.0,
        "innovation_n": 2.0,
        "euclidean_dist_m": 10.0,
        "euclidean_gate_m": 300.0,
        "mahalanobis_d2": 1.0,
        "mahalanobis_gate_d2": 9.21,
        "speed_innovation_mps": 0.5,
        "heading_innovation_deg": 1.0,
        "distance_mode": "euclidean",
        "assignment_mode": "hungarian",
        "assignment_column": 3,
        "assigned": 1,
        "gate_passed": 1,
        "cost_beats_dummy": 1,
        "total_cost": 0.1,
        "dummy_cost": 1.0,
        "reject_reason": "None",
        "assignment_outcome": "assigned",
        "lifecycle_event": "matched",
        "angle_innovation_available": 0,
        "velocity_innovation_available": 0,
        "unavailable_fields": "merge_split_lifecycle;heading_innovation_deg",
    }
    row.update(overrides)
    return row


def _write_v2_association(path: Path, row: dict[str, object]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def test_v2_association_audit_attributes_candidate_competition(tmp_path: Path) -> None:
    _write_v2_association(
        tmp_path / "track_association_audit_v2.csv",
        _v2_association_row(
            candidate_count=2,
            candidate_detection_ids="3;4",
            candidate_track_ids="2",
            candidate_rank=2,
        ),
    )
    row = _association_rows(tmp_path)[(1, "2", 3)]
    result = classify_switch({}, row, {"association_schema": "v2"})
    assert result["classification"] == "multiple_candidate_competition"
    assert result["evidence"]["candidate_count"] == 2


def test_v2_association_audit_attributes_gate_boundary(tmp_path: Path) -> None:
    _write_v2_association(
        tmp_path / "track_association_audit_v2.csv",
        _v2_association_row(
            euclidean_dist_m=300.0,
            euclidean_gate_m=300.0,
            gate_passed=0,
            assigned=0,
            cost_beats_dummy=0,
            assignment_outcome="gate_rejected",
            reject_reason="EuclideanGate",
        ),
    )
    row = _association_rows(tmp_path)[(1, "2", 3)]
    result = classify_switch({}, row, {"association_schema": "v2"})
    assert result["classification"] == "gate_boundary_crossing"


def test_v2_association_audit_attributes_lifecycle_transition(tmp_path: Path) -> None:
    _write_v2_association(
        tmp_path / "track_association_audit_v2.csv",
        _v2_association_row(
            track_state_before="Confirmed",
            track_state_after="Coasted",
            lifecycle_event="coasted",
            assigned=0,
            gate_passed=1,
            cost_beats_dummy=0,
            assignment_outcome="dummy_assigned",
        ),
    )
    row = _association_rows(tmp_path)[(1, "2", 3)]
    result = classify_switch({}, row, {"association_schema": "v2"})
    assert result["classification"] == "lifecycle_transition"


def test_v2_association_audit_reports_missing_production_field(tmp_path: Path) -> None:
    _write_v2_association(
        tmp_path / "track_association_audit_v2.csv",
        _v2_association_row(
            candidate_count=2,
            candidate_detection_ids="",
            euclidean_dist_m=10.0,
            euclidean_gate_m=300.0,
            lifecycle_event="matched",
        ),
    )
    row = _association_rows(tmp_path)[(1, "2", 3)]
    result = classify_switch({}, row, {"association_schema": "v2"})
    assert result["classification"] == "unclassifiable_production_fields"
    assert "candidate_detection_ids" in result["missing_production_fields"]
    assert missing_production_field_counts(
        [{"missing_production_fields": result["missing_production_fields"]}]
    ) == {"candidate_detection_ids": 1}


def test_write_audit_returns_classification_and_missing_field_summary(tmp_path: Path) -> None:
    summary = write_audit(
        tmp_path / "id_switch_audit.csv",
        [{
            "classification": "unclassifiable_production_fields",
            "missing_production_fields": ["candidate_detection_ids"],
        }],
    )
    assert summary["classification_counts"]["unclassifiable_production_fields"] == 1
    assert summary["missing_production_field_counts"] == {"candidate_detection_ids": 1}


def test_retained_v1_reanalysis_is_read_only_and_explicit_about_missing_v2(tmp_path: Path) -> None:
    source = tmp_path / "v1_id_switch_audit.csv"
    source.write_text(
        "scene_id,previous_period_id,period_id,previous_truth_target_id,truth_target_id,"
        "previous_track_id,current_track_id,association_distance_m,gate_threshold_m,classification,reason\n"
        "scene-1,1,2,T0,T0,1,2,8.0,300.0,other,old\n",
        encoding="utf-8",
    )
    original = source.read_bytes()
    summary = reanalyze_retained_audit(source, tmp_path / "v2" / "id_switch_audit.csv")
    assert source.read_bytes() == original
    assert summary["read_only_v1"] is True
    assert summary["production_v2_association_available"] is False
    assert summary["classification_counts"]["unclassifiable_production_fields"] == 1
    assert summary["missing_production_field_counts"] == {"track_association_audit_v2.csv": 1}
