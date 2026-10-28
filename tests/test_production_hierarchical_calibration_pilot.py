"""Contract tests for the production hierarchical calibration delay pilot.

These tests deliberately exercise the runner's provenance and selection
boundary without starting CUDA or replacing the production tracker.
"""

from __future__ import annotations

import json
import inspect
import xml.etree.ElementTree as ET
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


def test_adapter_tap_audit_follows_the_method_enable_contract() -> None:
    from scripts import run_production_hierarchical_calibration_pilot as runner

    audit_policy = getattr(runner, "method_requires_research_adapter_tap", None)
    assert callable(audit_policy), "runner must expose its adapter-tap audit policy"

    methods = runner.build_method_contract(
        runner.load_pilot_config(CONFIG),
        method_ids=("C0", "C1", "C2", "C3", "C4", "P1", "P2", "PK", "PKR"),
    )
    assert {
        str(method["method_id"]): audit_policy(method)
        for method in methods
    } == {
        "C0": False,
        "C1": True,
        "C2": True,
        "C3": True,
        "C4": True,
        "P1": False,
        "P2": True,
        "PK": False,
        "PKR": True,
    }


def test_method_and_mode_subset_keeps_default_contract_but_allows_bounded_pilot() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        build_arg_parser,
        build_method_contract,
        resolve_calibration_modes,
        load_pilot_config,
    )

    config = load_pilot_config(CONFIG)
    selected = build_method_contract(config, method_ids=("C0", "C2", "C3"))
    assert [item["method_id"] for item in selected] == ["C0", "C2", "C3"]
    assert [item["method_id"] for item in build_method_contract(config)] == [
        "C0", "C1", "C2", "C3", "C4", "P1", "P2", "PK", "PKR"
    ]

    mode_map = resolve_calibration_modes(selected, ("Mode-A",))
    assert mode_map == {
        "C0": ("not_applicable",),
        "C2": ("Mode-A",),
        "C3": ("Mode-A",),
    }
    with pytest.raises(ValueError, match="unknown method"):
        build_method_contract(config, method_ids=("C0", "NOT_A_METHOD"))
    with pytest.raises(ValueError, match="duplicate"):
        build_method_contract(config, method_ids=("C0", "C0"))
    with pytest.raises(ValueError, match="calibration mode"):
        resolve_calibration_modes(selected, ("Mode-C",))

    args = build_arg_parser().parse_args([
        "--mode", "e1_pilot", "--output-root", "run",
        "--methods", "C0,C2,C3", "--calibration-modes", "Mode-A",
    ])
    assert args.mode == "e1_pilot"
    assert args.method_ids == ["C0,C2,C3"]
    assert args.calibration_modes == ["Mode-A"]


def test_c1_roles_do_not_select_or_require_a_fitted_reference() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        build_branch_execution_contract,
        build_method_contract,
        load_pilot_config,
        resolve_calibration_modes,
    )

    methods = build_method_contract(
        load_pilot_config(CONFIG), method_ids=("C0", "C1", "C2", "C3")
    )
    modes = resolve_calibration_modes(methods, ("Mode-A",))

    assert modes == {
        "C0": ("not_applicable",),
        "C1": ("not_applicable",),
        "C2": ("Mode-A",),
        "C3": ("Mode-A",),
    }
    for role in ("OFF", "ON", "TO"):
        contract = build_branch_execution_contract("C1", "not_applicable", role)
        assert contract["estimator_input_roles"] == []
        assert contract["reference_source"] == "not_applicable"
        assert contract["reference_artifact"] is None
        assert contract["estimator_called"] is False
        assert contract["fixed_reference_required"] is False
        assert contract["reference_applied"] is False
        assert contract["fallback_to_current"] is False
    with pytest.raises(ValueError, match="only supports not_applicable mode"):
        build_branch_execution_contract("C1", "Mode-A", "ON", "/tmp/unused_gamma.csv")

    # Mode-A's OFF-fit and fixed-reference application remain frozen for C2/C3.
    c2_off = build_branch_execution_contract("C2", "Mode-A", "OFF", "/tmp/c2_gamma.csv")
    c2_on = build_branch_execution_contract("C2", "Mode-A", "ON", "/tmp/c2_gamma.csv")
    assert c2_off["estimator_called"] is True
    assert c2_off["fixed_reference_required"] is False
    assert c2_on["estimator_called"] is False
    assert c2_on["fixed_reference_required"] is True


def test_r13_evaluator_accepts_direct_c1_gamma_one_taps_without_off_reference(
    tmp_path: Path,
) -> None:
    import csv

    from scripts.phase_r.evaluate_r13_minimal_baseline_pilot import _audit_adapter
    from scripts.run_production_hierarchical_calibration_pilot import (
        build_branch_execution_contract,
    )

    adapter_path = tmp_path / "c1_adapter.csv"
    fields = [
        "result_id", "beam_id", "method", "group_id", "az_index",
        "range_start", "range_end", "status", "support_count", "gamma_real",
        "gamma_imag", "truth_used_in_estimator", "source", "reason",
    ]
    with adapter_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for period in (1, 2, 3):
            writer.writerow({
                "result_id": f"GMTI{period:02d}",
                "beam_id": 50,
                "method": "ordinary_subtraction",
                "group_id": "az_-1_range_0_9",
                "az_index": -1,
                "range_start": 0,
                "range_end": 9,
                "status": "OK",
                "support_count": 10,
                "gamma_real": 1.0,
                "gamma_imag": 0.0,
                "truth_used_in_estimator": "false",
                "source": "production_final_f1_f2_after_p38_range_phase",
                "reason": "",
            })

    branch = {
        "method_id": "C1",
        "mode": "not_applicable",
        "role": "ON",
        "branch_name": "C1_not_applicable_ON",
        "fallback_to_current": False,
        "execution_contract": build_branch_execution_contract(
            "C1", "not_applicable", "ON"
        ),
        "artifacts": {"adapter_csv": [str(adapter_path)]},
    }

    status, rows = _audit_adapter(branch, expected_period_count=3)

    assert status == "passed"
    assert len(rows) == 3
    assert [int(row["period_id"]) for row in rows] == [0, 1, 2]
    assert {float(row["gamma_real"]) for row in rows} == {1.0}
    assert {float(row["gamma_imag"]) for row in rows} == {0.0}

    with adapter_path.open(newline="", encoding="utf-8") as stream:
        corrupted_rows = list(csv.DictReader(stream))
    corrupted_rows[-1]["gamma_real"] = "0.9"
    with adapter_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(corrupted_rows)
    with pytest.raises(ValueError, match="Gamma=1 adapter evidence failed"):
        _audit_adapter(branch, expected_period_count=3)


def test_selected_methods_only_prepare_their_required_input_families() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        _required_prepared_families,
        build_method_contract,
        load_pilot_config,
    )

    config = load_pilot_config(CONFIG)
    e1_methods = build_method_contract(config, method_ids=("C0", "C1", "C2", "C3"))
    assert _required_prepared_families(e1_methods) == {"ideal", "none"}

    blind_methods = build_method_contract(config, method_ids=("P1",))
    assert _required_prepared_families(blind_methods) == {"ideal", "blind"}

    known_methods = build_method_contract(config, method_ids=("PK",))
    assert _required_prepared_families(known_methods) == {"ideal", "known"}


def test_zero_delay_stage2_reuses_only_physically_identical_off_and_to_roles(
    tmp_path: Path,
) -> None:
    from scripts.run_delay_stage1_formal import build_scene_variants
    from scripts.run_production_hierarchical_calibration_pilot import (
        _zero_delay_stage2_reuse_aliases,
    )

    config = {
        "case_id": "template",
        "output_dir": str(tmp_path / "template_output"),
        "waveform": {"new_protocol_channel_count": 4, "iq_data_type": "float32"},
        "random": {"random_seed": 999, "beam_start": 1, "period_count": 2},
        "scene": {"mode": "full", "output_signal_domain": "raw_lfm"},
        "targets": [{"target_id": "target", "enabled": True}],
        "channel_impairments": {"enabled": False, "channel_time_delay_ns": 0.0},
        "production": {"identity": "same"},
    }
    identity = {
        "seed": 101,
        "period_count": 3,
        "beam_id": 50,
        "target_velocity_mps": 6.7,
        "target_snr_db": 30.0,
        "target": {"target_id": "target", "enabled": True},
        "production": {"identity": "same"},
    }

    zero_delay = build_scene_variants(config, identity, 0.0, tmp_path / "zero")
    assert _zero_delay_stage2_reuse_aliases(zero_delay, 0.0) == {
        "A1_OFF": "A0_OFF",
        "A1_TO": "A0_TO",
    }
    assert _zero_delay_stage2_reuse_aliases(zero_delay, 4.0) == {}

    changed_target_only = json.loads(zero_delay["A1_TO"].read_text(encoding="utf-8"))
    changed_target_only["targets"][0]["motion"]["ve_mps"] += 0.1
    zero_delay["A1_TO"].write_text(
        json.dumps(changed_target_only), encoding="utf-8"
    )
    assert _zero_delay_stage2_reuse_aliases(zero_delay, 0.0) == {
        "A1_OFF": "A0_OFF",
    }


def test_cli_passes_selected_methods_into_case_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import run_production_hierarchical_calibration_pilot as runner

    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        runner,
        "source_snapshot",
        lambda: {"commit": "abc", "dirty": False, "dirty_tracked": False},
    )
    monkeypatch.setattr(runner, "require_clean_tracked_source", lambda *args, **kwargs: None)

    def fake_run_case(config, case, output_root, method_contract, **kwargs):
        seen.append(tuple(str(item["method_id"]) for item in method_contract))
        return {"case": dict(case), "case_id": "test-case", "status": "completed", "method_branches": []}

    monkeypatch.setattr(runner, "_run_case", fake_run_case)
    monkeypatch.setattr(runner, "_flatten_rows", lambda cases: ([], [], []))
    monkeypatch.setattr(runner, "aggregate_case_residual_closures", lambda cases: {})

    def fake_compact(manifest_path, evidence_root):
        evidence_root.mkdir(parents=True, exist_ok=True)
        compact_path = evidence_root / "manifest.json"
        runner._write_json(
            compact_path,
            {"analysis": {"decision": "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE"}},
        )
        return {"manifest.json": str(compact_path)}

    monkeypatch.setattr(runner, "_write_skip_compact_evidence", fake_compact)
    code = runner.run_cli([
        "--mode", "e1_pilot",
        "--seeds", "101",
        "--methods", "C0,C2,C3",
        "--calibration-modes", "Mode-A",
        "--max-cases", "1",
        "--output-root", str(tmp_path / "run"),
    ])

    assert code == 0
    assert seen == [("C0", "C2", "C3")]


def test_scene_identity_accepts_explicit_case_scene_parameters() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        _scene_identity,
        load_pilot_config,
        resolve_selection,
    )

    config = load_pilot_config(CONFIG)
    case = dict(resolve_selection(config, "pilot")["cases"][0])
    case["scene_parameters"] = {
        "beam_id": 51,
        "expected_bin": 2049,
        "range_m": 86000.0,
        "texture_sigma": 0.65,
        "clutter_rho": 0.95,
        "area_mean_power": 0.0097,
        "noise_power": 0.002,
    }

    identity = _scene_identity(config, case, period_count=3)
    assert identity["beam_id"] == 51
    assert identity["expected_bin"] == 2049
    assert identity["range_m"] == pytest.approx(86000.0)
    assert identity["texture_sigma"] == pytest.approx(0.65)
    assert identity["clutter_rho"] == pytest.approx(0.95)
    assert identity["clutter"]["mean_power"] == pytest.approx(0.0097)
    assert identity["noise"]["noise_power"] == pytest.approx(0.002)


def test_invalid_area_injection_stops_after_a0_on_before_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import run_production_hierarchical_calibration_pilot as runner
    from scripts import run_delay_stage1_formal as delay_runner

    config = runner.load_pilot_config(CONFIG)
    case = dict(runner.resolve_selection(config, "pilot")["cases"][0])
    case["delay_error_ns"] = 0.0
    stage2_calls: list[str] = []

    def fake_run_logged(command, log_path, *, timeout_s=1800.0, env=None):
        scenario_path = Path(str(command[-1]))
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        output_dir = Path(scenario["output_dir"])
        report = output_dir / "reports" / "stage2_simulation_report.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            "- platform_height_m: 6000\n"
            "- continuous_area_packets: 0\n"
            "- continuous_area_samples: 0\n"
            "- area_mean_power_measured: 0\n",
            encoding="utf-8",
        )
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("fake stage2 invocation\n", encoding="utf-8")
        stage2_calls.append(scenario_path.stem)
        return 0, 0.01

    monkeypatch.setattr(runner, "_run_logged", fake_run_logged)
    monkeypatch.setattr(
        delay_runner,
        "run_production_branch",
        lambda *args, **kwargs: pytest.fail("production must not start after failed area gate"),
    )

    result = runner._run_case(
        config,
        case,
        tmp_path / "run",
        runner.build_method_contract(config, method_ids=("C0",)),
        input_mode="local",
        skip_cuda=False,
        calibration_modes=("Mode-A",),
    )

    assert result["status"] == "clutter_gate_failed"
    assert result["area_clutter_gate"]["status"] == "NOT_EVALUABLE"
    assert stage2_calls == ["A0_ON"]


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
    assert contract["modes"]["Mode-A"]["apply_input_roles"] == ["ON", "TO"]
    assert contract["modes"]["Mode-B"]["estimator_input_roles"] == ["ON"]
    assert contract["modes"]["Mode-B"]["apply_input_roles"] == ["ON", "OFF", "TO"]
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


def test_e1_pilot_is_explicitly_paired_and_does_not_expand_other_selections() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        load_pilot_config,
        resolve_selection,
    )

    config = load_pilot_config(CONFIG)
    e1 = resolve_selection(config, "e1_pilot")
    assert e1["case_count"] == 24
    assert {case["target_velocity_mps"] for case in e1["cases"]} == {0.5, 1.0, 2.0, 4.0}
    assert {case["target_snr_db"] for case in e1["cases"]} == {10.0, 30.0}
    assert {case["delay_error_ns"] for case in e1["cases"]} == {0.0}
    assert {case["seed"] for case in e1["cases"]} == {101, 202, 303}
    assert all(case["period_count"] == 3 for case in e1["cases"])
    assert all(case["scene_parameters"]["range_m"] == 85000.0 for case in e1["cases"])
    seed101 = resolve_selection(config, "e1_pilot", overrides={"seeds": [101]})
    assert seed101["case_count"] == 8
    assert {case["seed"] for case in seed101["cases"]} == {101}
    assert resolve_selection(config, "pilot")["case_count"] == 3
    assert resolve_selection(config, "formal")["case_count"] == 17


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


def test_compact_decision_matrix_retains_target_and_track_audit_contract(tmp_path: Path) -> None:
    import csv

    from scripts.analyze_production_hierarchical_calibration_pilot import build_compact_evidence

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "status": "skip_cuda",
                "algorithmic_results_claimed": False,
                "method_contract": [],
                "compact_rows": {"gamma_recovery": [], "clutter_metrics": []},
            }
        ),
        encoding="utf-8",
    )
    outputs = build_compact_evidence(manifest_path, tmp_path / "compact")
    with outputs["decision_matrix.csv"].open(newline="", encoding="utf-8") as stream:
        row = next(csv.DictReader(stream))
    assert row["target_protection_rule"] == "causal_ON_minus_OFF_and_TO; never_ON_power_alone"
    assert row["track_evidence_required"] == (
        "track_association_audit_v2,track_states,track_output_payloads,"
        "id_switch_classification_or_NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"
    )


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
    with pytest.raises(RuntimeError, match="tracked source is dirty"):
        require_clean_tracked_source(
            {"commit": "abc", "dirty": True, "dirty_tracked": True},
            mode="e1_pilot",
        )


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


def test_cell_pfa_requires_false_hits_and_target_free_cut_denominator() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import empirical_cell_pfa

    observed = empirical_cell_pfa(5, 100)
    assert observed["status"] == "NOT_EVALUABLE"
    assert observed["value"] is None
    assert observed["tested_cut_hit_fraction"] == pytest.approx(0.05)

    passed = empirical_cell_pfa(
        5,
        100,
        false_cut_hit_count=3,
        valid_h0_cut_count=60,
    )
    assert passed["status"] == "evaluable"
    assert passed["value"] == pytest.approx(0.05)
    assert passed["definition"] == "false_cut_hit_count / valid_h0_cut_count"

    missing = empirical_cell_pfa(
        5,
        100,
        false_cut_hit_count=3,
        valid_h0_cut_count=0,
    )
    assert missing["status"] == "NOT_EVALUABLE"
    assert missing["value"] is None


def test_causal_triplet_requires_on_minus_off_equals_target_only() -> None:
    import numpy as np

    from scripts.analyze_production_hierarchical_calibration_pilot import causal_triplet_status

    off = np.array([1 + 2j, 2 - 1j], dtype=np.complex64)
    target = np.array([3 - 1j, -2 + 4j], dtype=np.complex64)
    assert causal_triplet_status(off, off + target, target)["status"] == "passed"
    assert causal_triplet_status(off, off + target, np.zeros(1, dtype=np.complex64))["status"] == "NOT_EVALUABLE"


def test_mode_a_reference_handoff_and_mode_b_truth_blind_roles_are_explicit() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        build_paired_input_contract,
        build_branch_execution_contract,
    )

    input_contract = build_paired_input_contract()
    mode_a_off = build_branch_execution_contract("C4", "Mode-A", "OFF", "/tmp/reference.csv")
    mode_a_on = build_branch_execution_contract("C4", "Mode-A", "ON", "/tmp/reference.csv")
    mode_a_to = build_branch_execution_contract("C4", "Mode-A", "TO", "/tmp/reference.csv")
    mode_b_on = build_branch_execution_contract("C4", "Mode-B", "ON", "/tmp/reference.csv")
    mode_b_off = build_branch_execution_contract("C4", "Mode-B", "OFF", "/tmp/reference.csv")
    mode_b_to = build_branch_execution_contract("C4", "Mode-B", "TO", "/tmp/reference.csv")

    assert input_contract["modes"]["Mode-A"]["apply_input_roles"] == ["ON", "TO"]
    assert input_contract["modes"]["Mode-B"]["apply_input_roles"] == ["ON", "OFF", "TO"]
    assert mode_a_off["estimator_input_roles"] == ["OFF"]
    assert mode_a_off["reference_source"] == "OFF_estimator_output"
    assert mode_a_off["estimator_called"] is True
    assert mode_a_on["reference_source"] == "OFF_estimator_output"
    assert mode_a_on["estimator_called"] is False
    assert mode_a_to["evaluator_only"] is True
    assert mode_a_to["estimator_called"] is False
    assert mode_b_on["estimator_input_roles"] == ["ON"]
    assert mode_b_on["reference_source"] == "ON_estimator_output"
    assert mode_b_on["truth_used_in_estimator"] is False
    assert mode_b_off["reference_source"] == "ON_estimator_output"
    assert mode_b_off["estimator_called"] is False
    assert mode_b_off["fixed_reference_required"] is True
    assert mode_b_off["reference_applied"] is True
    assert mode_b_off["evaluator_only"] is True
    assert mode_b_to["evaluator_only"] is True
    assert mode_b_to["fixed_reference_required"] is True
    assert mode_b_to["estimator_called"] is False


def test_mode_b_off_fixed_reference_generates_research_operator_xml(tmp_path: Path) -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        _method_xml,
        build_branch_execution_contract,
    )

    reference = tmp_path / "mode_b_on_reference.csv"
    contract = build_branch_execution_contract("C4", "Mode-B", "OFF", reference)
    method = {
        "method_id": "C4",
        "research_calibration_enable": True,
        "research_calibration_method": "robust_ddc_rb",
    }
    xml_record = _method_xml(
        tmp_path,
        ROOT / "config/gmti.xml",
        method,
        {},
        layout={"beam_id": 50},
        mode="Mode-B",
        role="OFF",
        reference_path=Path(str(contract["reference_artifact"])),
    )

    assert xml_record["status"] == "passed"
    assert xml_record["overrides"]["research_calibration_enable"] == "true"
    assert xml_record["overrides"]["research_calibration_method"] == "robust_ddc_rb"
    assert xml_record["overrides"]["research_calibration_reference_gamma_csv"] == str(reference.resolve())
    assert xml_record["overrides"]["csi_metrics_enable"] == "true"
    assert xml_record["overrides"]["csi_metrics_dump_power_maps"] == "true"
    assert xml_record["overrides"]["csi_metrics_dump_intermediate_maps"] == "true"
    assert xml_record["overrides"]["csi_metrics_beam_id"] == 50
    generated_xml = ET.parse(xml_record["path"])
    assert generated_xml.findtext(".//csi_metrics_enable") == "true"
    assert generated_xml.findtext(".//csi_metrics_dump_power_maps") == "true"
    assert generated_xml.findtext(".//csi_metrics_dump_intermediate_maps") == "true"
    assert generated_xml.findtext(".//csi_metrics_beam_id") == "50"


def test_method_xml_can_keep_scalar_csi_tap_without_dumping_full_maps(tmp_path: Path) -> None:
    from scripts.run_production_hierarchical_calibration_pilot import _method_xml

    method = {
        "method_id": "PKR",
        "research_calibration_enable": True,
        "research_calibration_method": "robust_ddc_rb",
    }
    xml_record = _method_xml(
        tmp_path,
        ROOT / "config/gmti.xml",
        method,
        {
            "range_band_bins": 133,
            "min_support": 8,
            "csi_metrics_dump_power_maps": False,
            "csi_metrics_dump_intermediate_maps": False,
        },
        layout={"beam_id": 50},
        mode="Mode-A",
        role="OFF",
    )

    assert xml_record["status"] == "passed"
    assert xml_record["overrides"]["csi_metrics_enable"] == "true"
    assert xml_record["overrides"]["csi_metrics_dump_power_maps"] == "false"
    assert xml_record["overrides"]["csi_metrics_dump_intermediate_maps"] == "false"
    generated_xml = ET.parse(xml_record["path"])
    assert generated_xml.findtext(".//csi_metrics_enable") == "true"
    assert generated_xml.findtext(".//csi_metrics_dump_power_maps") == "false"
    assert generated_xml.findtext(".//csi_metrics_dump_intermediate_maps") == "false"


def test_production_range_overrides_are_optional_and_fail_closed() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        _production_range_overrides,
    )

    assert _production_range_overrides({}) == {}
    assert _production_range_overrides({
        "processing_range_start": 666,
        "processing_range_end": 1197,
    }) == {"rg_st": 666, "rg_ed": 1197}

    for invalid in (
        {"processing_range_start": 666},
        {"processing_range_end": 1197},
        {"processing_range_start": -1, "processing_range_end": 1197},
        {"processing_range_start": 1197, "processing_range_end": 666},
        {"processing_range_start": 1.5, "processing_range_end": 1197},
    ):
        with pytest.raises(ValueError):
            _production_range_overrides(invalid)


def test_method_xml_applies_explicit_production_processing_range(tmp_path: Path) -> None:
    from scripts.run_production_hierarchical_calibration_pilot import _method_xml

    method = {
        "method_id": "PKR",
        "research_calibration_enable": True,
        "research_calibration_method": "robust_ddc_rb",
    }
    xml_record = _method_xml(
        tmp_path,
        ROOT / "config/gmti.xml",
        method,
        {
            "range_band_bins": 133,
            "min_support": 8,
            "processing_range_start": 666,
            "processing_range_end": 1197,
        },
        layout={"beam_id": 50},
        mode="Mode-A",
        role="OFF",
    )

    assert xml_record["overrides"]["rg_st"] == 666
    assert xml_record["overrides"]["rg_ed"] == 1197
    generated_xml = ET.parse(xml_record["path"])
    assert generated_xml.findtext(".//rg_st") == "666"
    assert generated_xml.findtext(".//rg_ed") == "1197"


def test_flatten_rows_rejects_mode_b_current_off_as_noncomparable() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        _flatten_rows,
        build_branch_execution_contract,
    )

    reference = "/tmp/shared_mode_b_reference.csv"
    branches = []
    for role in ("ON", "OFF", "TO"):
        contract = build_branch_execution_contract("C4", "Mode-B", role, reference)
        branch = {
            "method_id": "C4",
            "mode": "Mode-B",
            "role": role,
            "status": "passed",
            "execution_contract": contract,
            "metrics": {
                "target_detection_pd": 0.8,
                "track_pd_all_visible": 0.7,
            },
            "artifacts": {},
        }
        if role == "ON":
            branch["reference_artifact"] = {"status": "passed", "path": reference}
        else:
            branch["reference_applied"] = True
            branch["reference_path"] = reference
        branches.append(branch)

    off = next(branch for branch in branches if branch["role"] == "OFF")
    off_contract = dict(off["execution_contract"])
    off_contract.update({
        "fixed_reference_required": False,
        "reference_applied": False,
        "control_path": "production_current_control",
    })
    off["execution_contract"] = off_contract
    off["reference_applied"] = False
    off.pop("reference_path")

    _, _, clutter_rows = _flatten_rows([{
        "case_id": "mode-b-counterfactual",
        "method_branches": branches,
    }])
    causal = next(row for row in clutter_rows if row.get("layer") == "ON_minus_OFF")

    assert causal["status"] == "NOT_EVALUABLE"
    assert causal["processing_comparability_status"] == "NOT_EVALUABLE"
    assert causal["processing_comparability_reason"] == "fixed_reference_contract_invalid:OFF"


@pytest.mark.parametrize(("mode", "estimator_role"), [("Mode-A", "OFF"), ("Mode-B", "ON")])
def test_flatten_rows_accepts_causal_pair_with_shared_fixed_reference(
    mode: str, estimator_role: str
) -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        _flatten_rows,
        build_branch_execution_contract,
    )

    reference = "/tmp/shared_counterfactual_reference.csv"
    branches = []
    for role in ("OFF", "ON", "TO"):
        contract = build_branch_execution_contract("C4", mode, role, reference)
        branch = {
            "method_id": "C4",
            "mode": mode,
            "role": role,
            "status": "passed",
            "execution_contract": contract,
            "metrics": {
                "target_detection_pd": 0.8,
                "track_pd_all_visible": 0.7,
            },
            "artifacts": {},
        }
        if role == estimator_role:
            branch["reference_artifact"] = {"status": "passed", "path": reference}
        else:
            branch["reference_applied"] = True
            branch["reference_path"] = reference
        branches.append(branch)

    _, _, clutter_rows = _flatten_rows([{
        "case_id": f"{mode}-counterfactual",
        "method_branches": branches,
    }])
    causal = next(row for row in clutter_rows if row.get("layer") == "ON_minus_OFF")

    assert causal["status"] == "evaluable"
    assert causal["processing_comparability_status"] == "passed"


def test_ideal_a0_evaluator_contract_is_supported_without_expanding_method_contract() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        build_branch_execution_contract,
    )

    a0 = build_branch_execution_contract("A0", "not_applicable", "ON")

    assert a0["method_id"] == "A0"
    assert a0["mode"] == "not_applicable"
    assert a0["estimator_input_roles"] == []
    assert a0["estimator_called"] is False
    assert a0["evaluator_only"] is True
    assert a0["fixed_reference_required"] is False
    assert a0["truth_used_in_estimator"] is False


@pytest.mark.parametrize("mode", ["Mode-A", "Mode-B"])
def test_ideal_a0_rejects_calibration_modes(mode: str) -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        build_branch_execution_contract,
    )

    with pytest.raises(ValueError, match="A0 only supports not_applicable"):
        build_branch_execution_contract("A0", mode, "ON")


def test_adapter_rows_preserve_provenance_and_fail_closed() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import validate_adapter_rows

    passed = validate_adapter_rows(
        [{"status": "OK", "truth_used_in_estimator": "false", "support_count": "12", "reason": ""}],
        min_support=8,
    )
    assert passed["status"] == "evaluable"
    assert passed["truth_used_in_estimator"] is False
    assert passed["support_count"] == 12

    failed = validate_adapter_rows(
        [{"status": "NOT_EVALUABLE", "truth_used_in_estimator": "true", "support_count": "0", "reason": "low_phase_coherence"}],
        min_support=8,
    )
    assert failed["status"] == "NOT_EVALUABLE"
    assert failed["truth_used_in_estimator"] is True
    assert failed["reason"] == "low_phase_coherence"
    assert failed["fallback_to_current"] is False

    partial = validate_adapter_rows(
        [{"status": "PARTIAL", "truth_used_in_estimator": "false", "support_count": "12"}],
        min_support=8,
    )
    assert partial["status"] == "NOT_EVALUABLE"
    assert partial["reason"] == "adapter_status_PARTIAL"
    assert partial["fallback_to_current"] is False

    missing_truth_marker = validate_adapter_rows(
        [{"status": "OK", "support_count": "12"}],
        min_support=8,
    )
    assert missing_truth_marker["status"] == "NOT_EVALUABLE"
    assert missing_truth_marker["reason"] == "truth_marker_missing"
    assert missing_truth_marker["fallback_to_current"] is False


def test_reference_group_rows_reject_empty_identity_and_gamma(tmp_path: Path) -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        validate_reference_gamma_rows,
    )

    row = {
        "period_id": "1",
        "result_id": "GMTI01",
        "beam_id": "3",
        "method": "robust_ddc_rb",
        "group_id": "az_1_range_4_7",
        "az_index": "1",
        "range_start": "4",
        "range_end": "7",
        "status": "OK",
        "support_count": "4",
        "phase_coherence": "nan",
        "gamma_real": "0.8",
        "gamma_imag": "0.1",
        "truth_used_in_estimator": "false",
        "reason": "",
    }
    for field in (
        "period_id",
        "beam_id",
        "az_index",
        "range_start",
        "range_end",
        "support_count",
        "gamma_real",
        "gamma_imag",
    ):
        malformed = dict(row)
        malformed[field] = ""
        result = validate_reference_gamma_rows(
            [malformed], method_id="robust_ddc_rb", min_support=4
        )
        assert result["status"] == "NOT_EVALUABLE"
        assert result["reason"] == f"reference_field_missing:{field}"

    malformed_gamma = dict(row)
    malformed_gamma["gamma_real"] = "not-a-number"
    result = validate_reference_gamma_rows(
        [malformed_gamma], method_id="robust_ddc_rb", min_support=4
    )
    assert result["status"] == "NOT_EVALUABLE"
    assert result["reason"] == "reference_field_invalid:gamma_real"

    malformed_result_id = dict(row)
    malformed_result_id["result_id"] = "bogus"
    result = validate_reference_gamma_rows(
        [malformed_result_id], method_id="robust_ddc_rb", min_support=4
    )
    assert result["status"] == "NOT_EVALUABLE"
    assert result["reason"] == "reference_result_id_mismatch"

    malformed_group_id = dict(row)
    malformed_group_id["group_id"] = "bogus"
    result = validate_reference_gamma_rows(
        [malformed_group_id], method_id="robust_ddc_rb", min_support=4
    )
    assert result["status"] == "NOT_EVALUABLE"
    assert result["reason"] == "reference_group_id_mismatch"


def test_reference_group_rows_match_production_method_family_separately_from_id() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        validate_reference_gamma_rows,
    )

    row = {
        "period_id": "1",
        "result_id": "GMTI01",
        "beam_id": "3",
        "method": "robust_ddc",
        "group_id": "az_1_range_4_7",
        "az_index": "1",
        "range_start": "4",
        "range_end": "7",
        "status": "OK",
        "support_count": "4",
        "phase_coherence": "nan",
        "gamma_real": "0.8",
        "gamma_imag": "0.1",
        "truth_used_in_estimator": "false",
    }

    result = validate_reference_gamma_rows(
        [row], method_id="C3", expected_method="robust_ddc", min_support=4
    )

    assert result["status"] == "evaluable"
    assert result["reason"] is None


def test_reference_artifact_uses_group_rows_not_aggregate_rows(tmp_path: Path) -> None:
    import csv

    from scripts.run_production_hierarchical_calibration_pilot import (
        write_reference_gamma_artifact,
    )

    rows = []
    for group_id, az_index, range_start, range_end, gamma_real in (
        ("az_1_range_1_3", "1", "1", "3", "0.8"),
        ("az_1_range_4_6", "1", "4", "6", "0.6"),
    ):
        rows.append(
            {
                "period_id": "1",
                "result_id": "GMTI01",
                "beam_id": "3",
                "method": "robust_ddc_rb",
                "group_id": group_id,
                "az_index": az_index,
                "range_start": range_start,
                "range_end": range_end,
                "status": "OK",
                "support_count": "3",
                "phase_coherence": "nan",
                "gamma_real": gamma_real,
                "gamma_imag": "0.0",
                "truth_used_in_estimator": "false",
                "reason": "",
            }
        )
    destination = tmp_path / "reference_gamma.csv"
    result = write_reference_gamma_artifact(
        rows, destination, method_id="robust_ddc_rb", min_support=3
    )
    assert result["status"] == "passed"
    with destination.open(newline="", encoding="utf-8") as stream:
        written = list(csv.DictReader(stream))
    assert [item["group_id"] for item in written] == [
        "az_1_range_1_3",
        "az_1_range_4_6",
    ]
    assert [item["gamma_real"] for item in written] == ["0.8", "0.6"]


def test_reference_gamma_summary_drops_raw_rows_but_keeps_recovery_statistics() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        compact_reference_gamma_summary,
    )

    rows = [
        {
            "gamma_real": "0.8",
            "gamma_imag": "0.1",
            "phase_coherence": "0.9",
            "support_count": "4",
        },
        {
            "gamma_real": "0.6",
            "gamma_imag": "-0.1",
            "phase_coherence": "0.7",
            "support_count": "5",
        },
    ]
    validation = {
        "status": "evaluable",
        "reason": None,
        "truth_used_in_estimator": False,
        "fallback_to_current": False,
        "support_count": 9,
        "min_support": 4,
        "rows": rows,
    }

    summary = compact_reference_gamma_summary(
        rows, validation, source_paths=["/scratch/reference_gamma.csv"]
    )

    assert summary["status"] == "evaluable"
    assert summary["row_count"] == 2
    assert summary["support_count"] == 9
    assert summary["gamma_real_mean"] == pytest.approx(0.7)
    assert summary["gamma_imag_mean"] == pytest.approx(0.0)
    assert summary["phase_coherence_mean"] == pytest.approx(0.8)
    assert summary["source_paths"] == ["/scratch/reference_gamma.csv"]
    assert "rows" not in summary


def test_flatten_rows_emits_compact_gamma_summary_row() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import _flatten_rows

    _, gamma_rows, _ = _flatten_rows(
        [
            {
                "case_id": "case-1",
                "method_branches": [
                    {
                        "method_id": "C4",
                        "mode": "Mode-A",
                        "role": "OFF",
                        "status": "passed",
                        "reference_source": "OFF_estimator_output",
                        "execution_contract": {"reference_source": "OFF_estimator_output"},
                        "reference_gamma_summary": {
                            "status": "evaluable",
                            "reason": None,
                            "row_count": 2,
                            "support_count": 9,
                            "gamma_real_mean": 0.7,
                            "gamma_imag_mean": 0.0,
                            "gamma_abs_mean": 0.71,
                            "phase_coherence_mean": 0.8,
                            "source_paths": ["/scratch/reference_gamma.csv"],
                        },
                        "metrics": {},
                        "artifacts": {},
                        "cfar_geometry": {},
                    }
                ],
            }
        ]
    )

    assert len(gamma_rows) == 1
    assert gamma_rows[0]["row_count"] == 2
    assert gamma_rows[0]["gamma_real"] == pytest.approx(0.7)
    assert gamma_rows[0]["source_paths"] == "/scratch/reference_gamma.csv"


def test_flatten_rows_preserves_off_cfar_denominator_and_final_union_evidence() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import _flatten_rows

    denominator_audit = {
        "status": "passed",
        "row_count": 6,
        "reason": None,
        "definition": "per branch, not pooled",
    }
    union_audit = {
        "status": "passed",
        "expected_period_count": 3,
        "summary_count": 3,
        "hit_cells_by_production_order": [12, 18, 9],
        "total_hit_cells": 39,
        "source": "production_cfar_summary",
        "definition": "final merged CFAR hit counts; descriptive, not Pfa",
    }
    _, _, clutter_rows = _flatten_rows(
        [
            {
                "case_id": "case-1",
                "method_branches": [
                    {
                        "method_id": "C0",
                        "mode": "not_applicable",
                        "role": "OFF",
                        "status": "passed",
                        "metrics": {},
                        "artifacts": {},
                        "cfar_geometry": {},
                        "target_off_waterfall": {
                            "cell_false_hit_fraction": {
                                "status": "NOT_EVALUABLE",
                                "value": None,
                            },
                            "valid_cut_denominators": denominator_audit,
                            "unique_final_union_hits": union_audit,
                            "false_clusters": {},
                            "protocol_false_detections": {},
                            "false_tracks": {},
                        },
                    }
                ],
            }
        ]
    )

    off = next(row for row in clutter_rows if row.get("layer") == "OFF_waterfall")
    assert off.get("valid_cut_denominators") == denominator_audit
    assert off.get("unique_final_union_hits") == union_audit


def test_geometry_aggregation_keeps_hit_fraction_distinct_from_pfa(tmp_path: Path) -> None:
    from scripts.run_production_hierarchical_calibration_pilot import aggregate_branch_cfar_geometry

    path = tmp_path / "cfar_geometry_diagnostics.csv"
    fields = [
        "schema_version", "period_id", "beam_id", "branch", "total_cells",
        "edge_invalid_cells", "excluded_cells", "cut_band_filtered_cells",
        "valid_cut_count", "threshold_test_count", "hit_cut_count", "hit_index_hash",
        "configured_pfa", "cfar_type", "alpha", "guard_cells", "background_cells",
        "doppler_circular", "exclude_row_start", "exclude_row_end", "cut_band_start",
        "cut_band_end", "cut_band_mode",
    ]
    values = {
        key: "0" for key in fields
    }
    values.update({
        "schema_version": "1", "period_id": "0", "beam_id": "1", "branch": "C4",
        "total_cells": "10", "threshold_test_count": "10", "valid_cut_count": "8",
        "hit_cut_count": "2", "hit_index_hash": "abc", "configured_pfa": "1e-6",
        "cfar_type": "GO", "doppler_circular": "0", "cut_band_mode": "none",
    })
    path.write_text(",".join(fields) + "\n" + ",".join(values[field] for field in fields) + "\n", encoding="utf-8")
    result = aggregate_branch_cfar_geometry([path])
    assert result["status"] == "evaluable"
    assert result["valid_cut_count"] == 8
    assert result["hit_cut_count"] == 2
    assert result["tested_cut_hit_fraction"] == pytest.approx(0.25)
    assert result["cell_pfa"] is None
    assert result["pfa_status"] == "NOT_EVALUABLE"


def test_formal_selection_declares_two_snr_values_without_cartesian_expansion() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import load_pilot_config, resolve_selection

    config = load_pilot_config(CONFIG)
    formal = resolve_selection(config, "formal")
    assert {30.0, 35.0} <= set(formal["target_snr_db"])
    assert formal["selection_mode"] == "targeted_groups"
    assert formal["cartesian_full_matrix"] is False
    assert formal["case_count"] < formal["configured_cartesian_case_count"]


def test_analyzer_closes_a0_pk_pkr_residual_before_assigning_estimator_class() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import residual_closure

    closed = residual_closure(
        {
            "A0": {"residual_power_db": -20.0},
            "PK": {"residual_power_db": -21.0},
            "PKR": {"residual_power_db": -22.0},
        },
        tolerance_db=3.0,
    )
    assert closed["status"] == "passed"
    assert closed["required_conditions"] == ["A0", "PK", "PKR"]
    assert closed["estimator_class_allowed"] is True

    missing = residual_closure({"A0": {"residual_power_db": -20.0}})
    assert missing["status"] == "NOT_EVALUABLE"
    assert missing["estimator_class_allowed"] is False
    assert missing["decision"] == "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE"


def test_residual_closure_allows_pkr_extra_suppression_and_reports_it_separately() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import residual_closure

    result = residual_closure(
        {
            "A0": {"residual_power_db": -20.0},
            "PK": {"residual_power_db": -21.0},
            "PKR": {"residual_power_db": -35.0},
        },
        tolerance_db=3.0,
    )

    assert result["status"] == "passed"
    assert result["closure_conditions"] == ["A0", "PK"]
    assert result["spread_db"] == pytest.approx(15.0)
    assert result["a0_pk_spread_db"] == pytest.approx(1.0)
    assert result["additional_residual_suppression_db"] == pytest.approx(14.0)
    assert result["estimator_class_allowed"] is True


def test_residual_closure_keeps_a0_pk_physical_check_as_gate() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import residual_closure

    result = residual_closure(
        {
            "A0": {"residual_power_db": -20.0},
            "PK": {"residual_power_db": -25.0},
            "PKR": {"residual_power_db": -21.0},
        },
        tolerance_db=3.0,
    )

    assert result["status"] == "NOT_EVALUABLE"
    assert result["reason"] == "a0_pk_residual_not_closed"
    assert result["spread_db"] == pytest.approx(5.0)
    assert result["estimator_class_allowed"] is False


def test_residual_closure_requires_pkr_for_the_separate_comparison() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import residual_closure

    result = residual_closure(
        {
            "A0": {"residual_power_db": -20.0},
            "PK": {"residual_power_db": -21.0},
        },
        tolerance_db=3.0,
    )

    assert result["status"] == "NOT_EVALUABLE"
    assert result["reason"] == "missing_condition:PKR"
    assert result["estimator_class_allowed"] is False


def test_analyzer_reports_causal_off_on_to_metrics_without_using_on_power() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import causal_target_metrics

    result = causal_target_metrics(
        {"target_detection_pd": 0.10, "track_pd": 0.05},
        {"target_detection_pd": 0.80, "track_pd": 0.70},
        {"target_detection_pd": 0.90, "track_pd": 0.75},
    )
    assert result["status"] == "evaluable"
    assert result["on_minus_off"]["target_detection_pd"] == pytest.approx(0.70)
    assert result["on_minus_off"]["track_pd"] == pytest.approx(0.65)
    assert result["to"]["target_detection_pd"] == pytest.approx(0.90)
    assert "on_power" not in result
    assert result["target_protection_rule"] == "causal_ON_minus_OFF_and_TO; never_ON_power_alone"


def test_analyzer_keeps_cfar_cluster_protocol_and_track_layers_separate() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import waterfall_layers

    result = waterfall_layers(
        {
            "cfar_geometry": {"status": "evaluable", "valid_cut_count": 100, "hit_cut_count": 4},
            "metrics": {"cfar_cluster_count": 3, "cfar_selected_cluster_count": 2},
            "artifacts": {
                "detection": ["detections.csv"],
                "track_association": ["track_association_audit_v2.csv"],
                "track_states": ["track_states.csv"],
                "track_payloads": ["track_payloads.csv"],
            },
        }
    )
    assert result["status"] == "evaluable"
    assert [item["layer"] for item in result["layers"]] == [
        "CFAR", "cluster", "protocol_detection", "track"
    ]
    assert result["layers"][0]["tested_cut_hit_fraction"] == pytest.approx(0.04)
    assert result["layers"][0]["cell_pfa"] is None
    assert result["layers"][0]["pfa_status"] == "NOT_EVALUABLE"
    assert result["layers"][1]["count"] == 3
    assert result["layers"][2]["count"] == 1
    assert result["layers"][3]["status"] == "evaluable"


def test_analyzer_classifies_id_switches_or_marks_debug_limit() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import classify_id_switches

    classified = classify_id_switches(
        [
            {"frame": 1, "track_id": 7, "truth_id": "T1"},
            {"frame": 2, "track_id": 7, "truth_id": "T2"},
        ]
    )
    assert classified["status"] == "evaluable"
    assert classified["id_switch_count"] == 1
    assert classified["classification"] == "ID_SWITCH"

    unavailable = classify_id_switches([{"track_id": 7, "truth_id": "T1"}])
    assert unavailable["status"] == "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"
    assert unavailable["classification"] == "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"


def test_blind_delay_estimator_uses_existing_stage1_fused_api(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import run_delay_stage1_formal as stage1
    from scripts import delay_stage1_core
    from scripts.run_production_hierarchical_calibration_pilot import estimate_blind_delay

    monkeypatch.setattr(
        stage1,
        "_load_fused_from_periods",
        lambda periods, layout: (object(), object()),
    )
    monkeypatch.setattr(
        delay_stage1_core,
        "delay_method_suite",
        lambda f1, f2, fs_hz: [{"method": "D1_ordinary_LS", "delta_tau_ns": 1.25}],
    )
    monkeypatch.setattr(
        stage1,
        "_select_delay_estimate",
        lambda rows: (1.25, "D1_ordinary_LS"),
    )
    result = estimate_blind_delay(
        stage1,
        [Path("off_0000.bin")],
        {"fs_hz": 1.0},
    )
    assert result["status"] == "estimated"
    assert result["selected_delay_ns"] == pytest.approx(1.25)
    assert result["truth_used_in_estimator"] is False


def test_production_branch_exposes_xml_override_handoff_and_fields_are_auditable(tmp_path: Path) -> None:
    from scripts import run_delay_stage1_formal as stage1
    from scripts.run_production_hierarchical_calibration_pilot import audit_xml_fields

    assert "xml_overrides" in inspect.signature(stage1.run_production_branch).parameters
    xml_path = tmp_path / "production.xml"
    root = ET.Element("config")
    params = ET.SubElement(root, "GMTI_parameter")
    for key, value in {"research_calibration_enable": "1", "research_calibration_method": "robust_ddc_rb"}.items():
        ET.SubElement(params, key).text = value
    ET.ElementTree(root).write(xml_path, encoding="utf-8")
    audit = audit_xml_fields(xml_path, {
        "research_calibration_enable": "1",
        "research_calibration_method": "robust_ddc_rb",
    })
    assert audit["status"] == "passed"


def test_compact_contract_includes_causal_waterfall_and_track_audit_layers() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import build_downstream_compact_contract

    contract = build_downstream_compact_contract()
    assert {"ON_minus_OFF", "TO", "CFAR", "cluster", "protocol_detection", "track"} <= set(contract)
    assert contract["target_protection_rule"] == "causal_ON_minus_OFF_and_TO; never_ON_power_alone"
    assert contract["track_evidence"] == [
        "track_association_audit_v2",
        "track_states",
        "track_output_payloads",
        "id_switch_classification_or_NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG",
    ]


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


def test_blind_delay_preparation_uses_selected_delay_without_truth() -> None:
    """P1/P2 must consume estimate_blind_delay's public result key."""

    from scripts.run_production_hierarchical_calibration_pilot import (
        prepare_blind_delay_correction,
    )

    prepared = prepare_blind_delay_correction(
        {
            "status": "estimated",
            "selected_delay_ns": 1.25,
            "input_role": "OFF=C+N",
            "truth_used_in_estimator": False,
        },
        delay_truth_ns=4.0,
    )

    assert prepared["status"] == "evaluable"
    assert prepared["delay_estimate_ns"] == pytest.approx(1.25)
    assert prepared["correction_applied"] is True
    assert prepared["truth_used_in_estimator"] is False
    assert prepared["truth_used_to_apply_correction"] is False


def test_residual_closure_reads_actual_csi_after_power_by_period_and_beam(tmp_path: Path) -> None:
    import csv

    from scripts.analyze_production_hierarchical_calibration_pilot import (
        residual_closure_from_branch_artifacts,
    )

    fields = ["period_id", "beam_id", "roi_name", "after_mean_power", "status"]
    branches = []
    for method_id, powers in {"A0": [100.0, 100.0], "PK": [110.0, 110.0], "PKR": [90.0, 90.0]}.items():
        summary = tmp_path / f"{method_id}_csi_metric_tap_summary.csv"
        with summary.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for period_id, power in enumerate(powers):
                writer.writerow({
                    "period_id": period_id,
                    "beam_id": 50,
                    "roi_name": "clutter_band_strong_power_top10pct",
                    "after_mean_power": power,
                    "status": "formal_clutter_band_strong_roi_before_power_selected",
                })
        branches.append({
            "method_id": method_id,
            "mode": "Mode-A" if method_id == "PKR" else "not_applicable",
            "role": "OFF",
            "status": "passed",
            "artifacts": {"csi_metric_tap_summary": [str(summary)]},
        })

    closure = residual_closure_from_branch_artifacts(branches, tolerance_db=1.0)

    assert closure["status"] == "passed"
    assert closure["period_aggregation"] == "arithmetic_mean_over_period_beam"
    assert closure["residual_power_definition"] == (
        "10*log10(mean(after_mean_power over clutter_band_strong_power_top10pct period-beam rows))"
    )
    assert closure["values_db"]["A0"] == pytest.approx(20.0)
    assert closure["source_paths"]["PK"] == [str(tmp_path / "PK_csi_metric_tap_summary.csv")]


def _formal_manifest_with_complete_rows() -> dict[str, object]:
    return {
        "status": "completed",
        "mode": "formal",
        "skip_cuda": False,
        "algorithmic_results_claimed": False,
        "decision_candidate": "GO_HIERARCHICAL_CALIBRATION",
        "source": {"commit": "frozen", "dirty": False, "dirty_tracked": False},
        "residual_closure": {
            "status": "passed",
            "actual_production_artifacts": True,
            "source_paths": {
                "A0": ["a0/csi_metric_tap_summary.csv"],
                "PK": ["pk/csi_metric_tap_summary.csv"],
                "PKR": ["pkr/csi_metric_tap_summary.csv"],
            },
        },
        "compact_rows": {
            "clutter_metrics": [
                {
                    "layer": "CFAR",
                    "status": "evaluable",
                    "valid_cut_count": 10,
                    "hit_cut_count": 1,
                    "tested_cut_hit_fraction": 0.1,
                    "false_cut_hit_count": 1,
                    "valid_h0_cut_count": 10,
                    "cell_pfa": 0.1,
                    "pfa_status": "evaluable",
                },
                {"layer": "cluster", "status": "evaluable"},
                {"layer": "protocol_detection", "status": "evaluable"},
                {
                    "layer": "track",
                    "status": "evaluable",
                    "track_association_audit_v2": ["association.csv"],
                    "track_states": ["states.csv"],
                    "track_output_payloads": ["payloads.csv"],
                    "id_switch_classification": {"classification": "NO_ID_SWITCH"},
                },
                {
                    "layer": "ON_minus_OFF",
                    "status": "evaluable",
                    "on_minus_off_target_detection_pd": 0.7,
                    "on_minus_off_track_pd": 0.6,
                    "to_target_detection_pd": 0.8,
                    "to_track_pd": 0.7,
                },
            ]
        },
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest["compact_rows"]["clutter_metrics"][0].update({  # type: ignore[index]
            "status": "NOT_EVALUABLE", "valid_cut_count": 0, "cell_pfa": 0.0
        }),
        lambda manifest: manifest["compact_rows"]["clutter_metrics"][-1].update({  # type: ignore[index]
            "on_minus_off_track_pd": None
        }),
        lambda manifest: manifest["compact_rows"]["clutter_metrics"].pop(2),  # type: ignore[index]
        lambda manifest: manifest["compact_rows"]["clutter_metrics"][3].update({  # type: ignore[index]
            "track_states": [], "id_switch_classification": "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"
        }),
        lambda manifest: manifest.update({"source": {"commit": "dirty", "dirty": True, "dirty_tracked": True}}),
    ],
    ids=["cell-pfa", "causal", "four-layers", "track-provenance", "dirty-source"],
)
def test_analyzer_refuses_algorithmic_claim_when_any_formal_gate_is_incomplete(mutate) -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import analyze_delay_evidence

    manifest = _formal_manifest_with_complete_rows()
    mutate(manifest)
    result = analyze_delay_evidence(manifest)

    assert result["status"] == "NOT_EVALUABLE"
    assert result["algorithmic_results_claimed"] is False
    assert result["decision"] == "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE"


def test_analyzer_rejects_mode_b_causal_row_without_comparability_evidence() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import analyze_delay_evidence

    manifest = _formal_manifest_with_complete_rows()
    causal = manifest["compact_rows"]["clutter_metrics"][-1]  # type: ignore[index]
    causal["mode"] = "Mode-B"

    result = analyze_delay_evidence(manifest)

    assert result["causal_metrics"]["status"] == "NOT_EVALUABLE"
    assert result["status"] == "NOT_EVALUABLE"
    assert result["algorithmic_results_claimed"] is False


def test_analyzer_accepts_mode_b_causal_row_with_comparability_evidence() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import analyze_delay_evidence

    manifest = _formal_manifest_with_complete_rows()
    causal = manifest["compact_rows"]["clutter_metrics"][-1]  # type: ignore[index]
    causal.update({
        "mode": "Mode-B",
        "processing_comparability_status": "passed",
    })

    result = analyze_delay_evidence(manifest)

    assert result["causal_metrics"]["status"] == "evaluable"
    assert result["causal_metrics"]["processing_comparability"] == "passed"
    assert result["status"] == "passed"


def test_analyzer_claims_only_clean_formal_actual_evidence_after_all_gates_pass() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import analyze_delay_evidence

    result = analyze_delay_evidence(_formal_manifest_with_complete_rows())

    assert result["status"] == "passed"
    assert result["algorithmic_results_claimed"] is True
    assert result["decision"] == "GO_HIERARCHICAL_CALIBRATION"


def test_analyzer_allows_not_identifiable_id_switch_with_complete_track_provenance() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import analyze_delay_evidence

    manifest = _formal_manifest_with_complete_rows()
    track = manifest["compact_rows"]["clutter_metrics"][3]  # type: ignore[index]
    track["id_switch_classification"] = "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"

    result = analyze_delay_evidence(manifest)

    assert result["status"] == "passed"
    assert result["algorithmic_results_claimed"] is True
    assert result["id_switch"]["classification"] == "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"
    assert result["id_switch"]["limitation"] == "current_debug_cannot_classify_id_switch"


def test_analyzer_refuses_not_identifiable_id_switch_when_track_provenance_is_missing() -> None:
    from scripts.analyze_production_hierarchical_calibration_pilot import analyze_delay_evidence

    manifest = _formal_manifest_with_complete_rows()
    track = manifest["compact_rows"]["clutter_metrics"][3]  # type: ignore[index]
    track["id_switch_classification"] = "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"
    track["track_output_payloads"] = []

    result = analyze_delay_evidence(manifest)

    assert result["status"] == "NOT_EVALUABLE"
    assert result["algorithmic_results_claimed"] is False


def test_compact_evidence_retains_not_identifiable_id_switch_marker(tmp_path: Path) -> None:
    import csv

    from scripts.analyze_production_hierarchical_calibration_pilot import build_compact_evidence

    manifest = _formal_manifest_with_complete_rows()
    track = manifest["compact_rows"]["clutter_metrics"][3]  # type: ignore[index]
    track["id_switch_classification"] = "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    outputs = build_compact_evidence(manifest_path, tmp_path / "compact")

    with outputs["decision_matrix.csv"].open(newline="", encoding="utf-8") as stream:
        row = next(csv.DictReader(stream))
    report = outputs["report.md"].read_text(encoding="utf-8")
    assert row["id_switch_classification"] == "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"
    assert row["id_switch_limitation"] == "current_debug_cannot_classify_id_switch"
    assert "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG" in report


def test_runner_aggregates_case_closures_into_manifest_evidence() -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        aggregate_case_residual_closures,
    )

    closure = aggregate_case_residual_closures([
        {
            "case_id": "case-a",
            "residual_closure": {
                "status": "passed",
                "actual_production_artifacts": True,
                "source_paths": {"A0": ["a0.csv"], "PK": ["pk.csv"], "PKR": ["pkr.csv"]},
            },
        }
    ])

    assert closure["status"] == "passed"
    assert closure["actual_production_artifacts"] is True
    assert closure["source_paths"]["A0"] == ["a0.csv"]


def test_isolated_runtime_executable_path_can_be_selected_without_touching_default_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        _configured_executable_path,
    )

    executable = tmp_path / "native-release" / "GMTI_pipe_core"
    executable.parent.mkdir()
    executable.write_bytes(b"isolated native CUDA executable")
    monkeypatch.setenv("PGRCC_GMTI_PIPE_CORE", str(executable))

    selected = _configured_executable_path(
        "PGRCC_GMTI_PIPE_CORE", ROOT / "build/GMTI_pipe_core"
    )

    assert selected == executable.resolve()
    assert selected != (ROOT / "build/GMTI_pipe_core").resolve()


def test_pinned_executable_identity_fails_closed_after_binary_changes(tmp_path: Path) -> None:
    from scripts.run_production_hierarchical_calibration_pilot import (
        _artifact_identity,
        _assert_artifact_sha256,
    )

    executable = tmp_path / "GMTI_pipe_core"
    executable.write_bytes(b"release A")
    pinned = _artifact_identity(executable)
    assert pinned["exists"] is True

    _assert_artifact_sha256(executable, str(pinned["sha256"]), "GMTI_pipe_core")
    executable.write_bytes(b"release B")
    with pytest.raises(RuntimeError, match="GMTI_pipe_core.*identity mismatch"):
        _assert_artifact_sha256(executable, str(pinned["sha256"]), "GMTI_pipe_core")
