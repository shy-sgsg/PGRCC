"""Tests for the TrackManager branch correction gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.run_track_manager_e2e import (
    _audit_runtime_xml_layout,
    _find_debug_dir,
    _run_branch,
    branch_contract,
    branch_evaluation_gate,
)


ROOT = Path(__file__).resolve().parents[1]


def test_track_manager_contract_requires_real_corrections() -> None:
    contract = branch_contract()
    assert contract["branches"] == ["Current", "blind_calibrated", "known_error_calibrated"]
    assert contract["ai_training"] is False
    assert contract["router_enabled"] is False
    assert contract["protocol_channel_count"] == 4
    assert contract["fusion_pairs"] == [[1, 3], [2, 4]]
    assert contract["correction_requirements"]["blind_calibrated"]["correction_applied"] is True
    assert contract["correction_requirements"]["known_error_calibrated"]["correction_applied"] is True
    assert contract["correction_requirements"]["known_error_calibrated"]["truth_used_in_estimator"] is False
    assert contract["correction_requirements"]["known_error_calibrated"]["evaluation_only"] is True


def test_track_manager_gate_marks_missing_correction_not_evaluable() -> None:
    assert branch_evaluation_gate(
        "Current", correction_applied=False, correction_source="none",
        delay_estimate_ns=None, delay_reference_ns=None,
    )["status"] == "evaluable"
    result = branch_evaluation_gate(
        "blind_calibrated", correction_applied=False, correction_source="fallback",
        delay_estimate_ns=None, delay_reference_ns=None,
    )
    assert result["status"] == "NOT_EVALUABLE"
    assert "correction_applied" in result["reason"]


def test_current_gate_rejects_declared_correction() -> None:
    result = branch_evaluation_gate(
        "Current", correction_applied=True, correction_source="bad_fallback",
        delay_estimate_ns=1.0, delay_reference_ns=None,
    )
    assert result["status"] == "NOT_EVALUABLE"
    assert "Current" in result["reason"]


def test_runner_marks_failed_estimator_before_validating_missing_inputs(tmp_path: Path) -> None:
    branch_root = tmp_path / "case"
    source_xml = tmp_path / "source.xml"
    source_xml.write_text("<xml />\n", encoding="utf-8")
    record = _run_branch(
        branch_root,
        "blind_calibrated",
        "local",
        {
            "period_paths": [],
            "merged_input": None,
            "correction_applied": False,
            "correction_source": "target_free_phase_vs_frequency",
            "delay_estimate_ns": None,
            "delay_reference_ns": 4.0,
        },
        source_xml,
        {},
        1,
        1000,
        1,
    )
    assert record["status"] == "NOT_EVALUABLE"
    assert (branch_root / "branches/blind_calibrated/branch_manifest.json").is_file()


def test_runner_direct_entrypoint_has_import_compatible_help() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_track_manager_e2e.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "delay-error-ns" in completed.stdout
    assert "--period-count {3,4,5,6,7}" in completed.stdout
    assert "requested" in completed.stdout and "5-7 range" in completed.stdout


def test_runtime_xml_audit_requires_four_channel_fusion_contract(tmp_path: Path) -> None:
    source_xml = tmp_path / "runtime.xml"
    source_xml.write_text(
        """<root>
  <pulse_len>4</pulse_len>
  <new_protocol_channel_count>4</new_protocol_channel_count>
  <new_protocol_read_channel_1>1</new_protocol_read_channel_1>
  <new_protocol_read_channel_2>2</new_protocol_read_channel_2>
  <iq_data_type>float32</iq_data_type>
  <fs>60</fs>
  <enable_four_channel_fusion>1</enable_four_channel_fusion>
  <four_channel_phase_compensation_enable>true</four_channel_phase_compensation_enable>
  <four_channel_fusion_channel_3>3</four_channel_fusion_channel_3>
  <four_channel_fusion_channel_4>4</four_channel_fusion_channel_4>
</root>
""",
        encoding="utf-8",
    )
    layout = {
        "pulse_len": 4,
        "channel_count": 4,
        "read_channel_1": 1,
        "read_channel_2": 2,
        "iq_data_type": "float32",
        "fs_hz": 60_000_000.0,
        "enable_four_channel_fusion": True,
        "four_channel_phase_compensation_enable": True,
        "fusion_channel_3": 3,
        "fusion_channel_4": 4,
    }
    assert _audit_runtime_xml_layout(source_xml, layout)["status"] == "passed"

    source_xml.write_text(source_xml.read_text(encoding="utf-8").replace(
        "<enable_four_channel_fusion>1</enable_four_channel_fusion>",
        "<enable_four_channel_fusion>0</enable_four_channel_fusion>",
    ), encoding="utf-8")
    failed = _audit_runtime_xml_layout(source_xml, layout)
    assert failed["status"] == "failed"
    assert any("enable_four_channel_fusion" in error for error in failed["errors"])


def test_runner_finds_local_test_debug_run_next_to_result_dir(tmp_path: Path) -> None:
    result_dir = tmp_path / "branch" / "result"
    configured_debug_dir = tmp_path / "branch" / "track_debug"
    actual_run = tmp_path / "branch" / "track_debug_runs" / "local_test_001"
    result_dir.mkdir(parents=True)
    actual_run.mkdir(parents=True)
    (actual_run / "track_frames.csv").write_text("result_id\n1\n", encoding="utf-8")

    assert _find_debug_dir(result_dir, configured_debug_dir) == actual_run
