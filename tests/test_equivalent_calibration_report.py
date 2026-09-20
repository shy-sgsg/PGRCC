"""Contract tests for the current-stage equivalent calibration report."""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT = REPO_ROOT / "docs" / "AI_CSI_40_等效复校准与物理校准定向Pilot报告.md"
PILOT_MANIFEST = REPO_ROOT / "outputs" / "formal_evidence" / "equivalent_vs_physical_pilot" / "manifest.json"


def test_report_is_source_backed_and_keeps_evidence_status_boundaries() -> None:
    assert REPORT.is_file()
    report = REPORT.read_text(encoding="utf-8")

    for token in (
        "sanity_status",
        "pilot_status",
        "passed",
        "manifest.json",
        "method_contract.csv",
        "gamma_recovery.csv",
        "clutter_metrics.csv",
        "target_transfer.csv",
        "detection_metrics.csv",
        "track_metrics.csv",
        "decision_matrix.csv",
        "NOT_EVALUABLE",
        "ai_training=false",
        "router_enabled=false",
        "GO_COUPLED_PHYSICAL_STATE_STUDY",
    ):
        assert token in report, token

    for mechanism in (
        "pure equivalent mismatch",
        "fast-time channel delay",
        "servo pointing",
        "platform velocity",
        "true decorrelation",
    ):
        assert mechanism in report, mechanism

    assert "DDC" in report and "DDC-RB" in report and "Robust DDC" in report
    assert "clutter residual" in report.lower()
    assert "physical-state" in report.lower() or "physical state" in report.lower()
    assert "TrackManager" in report and "PIPE" in report
    assert "AI_CSI_36" in report and "AI_CSI_37" in report and "AI_CSI_38" in report
    assert "GO_PHYSICS_AI" not in report


def test_report_decision_matches_current_compact_manifest_when_present() -> None:
    if not PILOT_MANIFEST.is_file():
        return
    manifest = json.loads(PILOT_MANIFEST.read_text(encoding="utf-8"))
    report = REPORT.read_text(encoding="utf-8")
    assert f"`{manifest['pilot_status']}`" in report
    assert f"`{manifest['decision_label']}`" in report
    assert manifest["ai_training"] is False
    assert manifest["router_enabled"] is False
