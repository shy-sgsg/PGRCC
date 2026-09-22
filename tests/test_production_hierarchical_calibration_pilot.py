"""Contract tests for the production hierarchical calibration delay pilot.

These tests deliberately exercise the runner's provenance and selection
boundary without starting CUDA or replacing the production tracker.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/research/production_hierarchical_calibration_delay_pilot.json"


def test_method_contract_freezes_nine_production_ids_and_roles() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        METHOD_IDS,
        build_method_contract,
        load_pilot_config,
    )

    config = load_pilot_config(CONFIG)
    contract = build_method_contract(config)
    assert tuple(item["method_id"] for item in contract) == METHOD_IDS
    assert set(METHOD_IDS) == {"C0", "C1", "C2", "C3", "C4", "P1", "P2", "PK", "PKR"}

    by_id = {item["method_id"]: item for item in contract}
    assert by_id["C0"]["research_calibration_enable"] is False
    assert by_id["C0"]["research_calibration_method"] == "production_current"
    assert by_id["C1"]["research_calibration_method"] == "ordinary_subtraction"
    assert by_id["C2"]["research_calibration_method"] == "scc"
    assert by_id["C3"]["research_calibration_method"] == "robust_ddc"
    assert by_id["C4"]["research_calibration_method"] == "robust_ddc_rb"
    assert by_id["P1"]["input_correction"] == "raw_channel_2_fractional_delay"
    assert by_id["P1"]["research_calibration_enable"] is False
    assert by_id["P2"]["input_correction"] == "raw_channel_2_fractional_delay"
    assert by_id["P2"]["research_calibration_method"] == "robust_ddc_rb"
    assert by_id["PK"]["known_error"] is True
    assert by_id["PK"]["known_error_use"] == "evaluator_only"
    assert by_id["PKR"]["known_error_use"] == "evaluator_only"
    assert all(item["truth_used_in_estimator"] is False for item in contract)


def test_paired_input_contract_declares_shared_identity_and_two_modes() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        build_paired_input_contract,
    )

    contract = build_paired_input_contract()
    assert contract["roles"] == {"OFF": "C+N", "ON": "S+C+N", "TO": "S"}
    assert set(contract["shared_identity_fields"]) >= {
        "seed",
        "background_id",
        "target_id",
        "production_identity",
    }
    assert contract["modes"]["Mode-A"]["estimator_input_roles"] == ["OFF"]
    assert contract["modes"]["Mode-A"]["apply_input_roles"] == ["ON"]
    assert contract["modes"]["Mode-B"]["estimator_input_roles"] == ["ON"]
    assert contract["modes"]["Mode-B"]["apply_input_roles"] == ["ON"]
    for mode in contract["modes"].values():
        assert mode["truth_used_in_estimator"] is False
        assert mode["target_position_allowed"] is False
        assert mode["injected_error_label_allowed"] is False
        assert mode["known_error_allowed"] is False


def test_selection_requires_targeted_delay_sweep_and_formal_coverage() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        load_pilot_config,
        resolve_selection,
    )

    config = load_pilot_config(CONFIG)
    pilot = resolve_selection(config, "pilot")
    assert pilot["selection_mode"] == "targeted"
    assert pilot["cartesian_full_matrix"] is False
    assert set(pilot["delay_errors_ns"]) == {0.0, 2.0, -2.0}
    assert pilot["seeds"] == [101]
    assert pilot["target_velocities_mps"] == [6.7]
    assert pilot["target_snr_db"] == [30.0]
    assert pilot["period_count"] == 3
    assert {case["period_count"] for case in pilot["cases"]} == {3}
    assert len(pilot["cases"]) == 3

    formal = resolve_selection(config, "formal")
    assert formal["selection_mode"] == "targeted_groups"
    assert formal["cartesian_full_matrix"] is False
    assert {101, 202, 303} <= set(formal["seeds"])
    assert {6.7, 12.0} <= set(formal["target_velocities_mps"])
    assert {0.0, 2.0, -2.0, 4.0, -4.0} <= set(formal["delay_errors_ns"])
    assert formal["case_count"] < (
        len(formal["delay_errors_ns"])
        * len(formal["seeds"])
        * len(formal["target_velocities_mps"])
        * len(formal["target_snr_db"])
    )


def test_support_gate_is_not_evaluable_and_never_current_fallback() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import evaluate_support

    result = evaluate_support(
        support_count=2,
        min_support=8,
        denominator=0.0,
        phase_coherence=0.1,
    )
    assert result["status"] == "NOT_EVALUABLE"
    assert result["reason"] in {
        "support_below_minimum",
        "support_denominator_invalid",
        "phase_coherence_below_threshold",
    }
    assert result["fallback_method"] is None
    assert result["fallback_to_current"] is False


def test_compact_evidence_root_has_exact_six_file_allow_list(tmp_path: Path) -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        COMPACT_EVIDENCE_FILES,
        validate_compact_evidence_root,
    )

    root = tmp_path / "evidence"
    root.mkdir()
    for name in COMPACT_EVIDENCE_FILES:
        (root / name).write_text("placeholder\n", encoding="utf-8")
    assert validate_compact_evidence_root(root)["status"] == "passed"

    (root / "raw.bin").write_bytes(b"forbidden")
    with pytest.raises(ValueError, match="allow-list"):
        validate_compact_evidence_root(root)


def test_formal_source_guard_refuses_tracked_dirty_identity() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        require_clean_tracked_source,
    )

    with pytest.raises(RuntimeError, match="tracked source is dirty"):
        require_clean_tracked_source(
            {"commit": "abc", "dirty": True, "dirty_tracked": True},
            mode="formal",
        )
    assert require_clean_tracked_source(
        {"commit": "abc", "dirty": False, "dirty_tracked": False},
        mode="pilot",
    )["status"] == "passed"


def test_only_four_decision_labels_are_accepted() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import (
        ALLOWED_DECISION_LABELS,
        choose_decision,
        validate_decision_label,
    )

    assert ALLOWED_DECISION_LABELS == {
        "GO_EQUIVALENT_CALIBRATION_ONLY",
        "GO_PHYSICAL_CALIBRATION_ONLY",
        "GO_HIERARCHICAL_CALIBRATION",
        "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE",
    }
    assert choose_decision({"status": "skip_cuda"}) == "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE"
    for label in ALLOWED_DECISION_LABELS:
        assert validate_decision_label(label) == label
    with pytest.raises(ValueError, match="decision label"):
        validate_decision_label("GO_PHYSICS_AI")


def test_cell_pfa_uses_only_hit_and_valid_cut_denominator() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import empirical_cell_pfa

    passed = empirical_cell_pfa(5, 100)
    assert passed["status"] == "evaluable"
    assert passed["value"] == pytest.approx(0.05)
    assert passed["definition"] == "hit_cut_count / valid_cut_count"

    missing = empirical_cell_pfa(5, 0)
    assert missing["status"] == "NOT_EVALUABLE"
    assert missing["value"] is None


def test_causal_triplet_requires_on_minus_off_equals_target_only() -> None:
    import numpy as np

    from scripts.analyze_production_hierarchical_calibration_pilot import causal_triplet_status

    off = np.array([1 + 2j, 2 - 1j], dtype=np.complex64)
    target = np.array([3 - 1j, -2 + 4j], dtype=np.complex64)
    assert causal_triplet_status(off, off + target, target)["status"] == "passed"
    assert causal_triplet_status(off, off + target, np.zeros(1, dtype=np.complex64))["status"] == "NOT_EVALUABLE"


def test_skip_contract_runner_records_non_algorithmic_status_and_methods(tmp_path: Path) -> None:
    from scripts.run_production_hierarchical_calibration_pilot import run_cli

    output_root = tmp_path / "pilot"
    returncode = run_cli(
        [
            "--config",
            str(CONFIG),
            "--mode",
            "pilot",
            "--output-root",
            str(output_root),
            "--skip-cuda",
        ]
    )
    assert returncode == 0
    manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "skip_cuda"
    assert manifest["algorithmic_results_claimed"] is False
    assert manifest["decision"] == "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE"
    assert [row["method_id"] for row in manifest["method_contract"]] == [
        "C0", "C1", "C2", "C3", "C4", "P1", "P2", "PK", "PKR"
    ]
    assert manifest["selection"]["cartesian_full_matrix"] is False
