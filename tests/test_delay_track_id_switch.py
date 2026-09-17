"""Tests for evidence-first TrackManager ID-switch classification."""

from __future__ import annotations

from pathlib import Path

from scripts.audit_delay_track_id_switch import classify_switch, write_audit


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
