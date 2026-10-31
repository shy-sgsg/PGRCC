import json
import math
from pathlib import Path

import pytest

from scripts.phase_r import run_r22_cuda_pilot as r22


def test_r22_case_identifier_encodes_physical_factors_and_sample_scale():
    value = r22.case_identifier("confirmatory", 222001, 0.5, 20.0, 4.0, 20, 10)

    assert value == (
        "confirmatory__seed_222001__vr_0d5__snr_20__delay_4ns__targets_20__periods_10"
    )


def test_r22_zero_delay_reference_does_not_schedule_physical_blind_correction():
    methods = ("C0", "P1", "P2", "PK", "PKR")

    assert r22._method_ids_for_delay(methods, 0.0) == ("C0", "PK", "PKR")
    assert r22._method_ids_for_delay(methods, 4.0) == methods


def test_r22_zero_delay_reference_records_estimates_without_applying_correction():
    qualified = {
        "d3_huber": {"status": "AVAILABLE", "correction_ns": 0.19},
        "m1": {"status": "AVAILABLE", "correction_ns": 0.17},
    }
    estimate = {"status": "ok", "truth_used_in_estimator": False}

    specs, not_applicable = r22._extra_delay_correction_plan(
        delay_ns=0.0,
        qualified=qualified,
        estimate_record=estimate,
        zero_error_target_free_estimate={"status": "estimated", "selected_delay_ns": 0.05},
    )

    assert specs == []
    assert {row["method_id"] for row in not_applicable} == {
        "R22_R9_Fixed_D3", "R22_D3_Huber_B_ON", "R22_M1_Physics_Matched", "P1", "P2",
    }
    assert all(row["role"] == "ALL" for row in not_applicable)
    assert all(row["status"] == "NOT_APPLICABLE_ZERO_DELAY_REFERENCE" for row in not_applicable)
    by_method = {row["method_id"]: row for row in not_applicable}
    assert by_method["R22_D3_Huber_B_ON"]["estimate_record"] == estimate
    assert by_method["R22_M1_Physics_Matched"]["estimate_record"] == estimate
    assert by_method["P1"]["estimate_record"]["selected_delay_ns"] == 0.05
    assert by_method["P2"]["estimate_record"]["selected_delay_ns"] == 0.05


def test_r22_nonzero_delay_keeps_frozen_estimated_correction_methods():
    qualified = {
        "d3_huber": {"status": "AVAILABLE", "correction_ns": 3.4},
        "m1": {"status": "AVAILABLE", "correction_ns": 3.7},
    }
    estimate = {"status": "ok", "truth_used_in_estimator": False}

    specs, not_applicable = r22._extra_delay_correction_plan(
        delay_ns=4.0,
        qualified=qualified,
        estimate_record=estimate,
    )

    assert [row[0] for row in specs] == [
        "R22_R9_Fixed_D3", "R22_D3_Huber_B_ON", "R22_M1_Physics_Matched",
    ]
    assert not_applicable == []


def test_run_case_refuses_to_overwrite_existing_output(tmp_path: Path):
    root = tmp_path / "existing"
    root.mkdir()
    (root / "keep.txt").write_text("existing", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        r22.run_case(
            root,
            phase="engineering",
            seed=220001,
            speed_mps=2.5,
            snr_db=20.0,
            delay_ns=4.0,
            target_count=20,
            period_count=5,
            first_beam=50,
            beam_count=1,
            stage2_binary=tmp_path / "missing_stage2",
            production_binary=tmp_path / "missing_production",
        )
    assert (root / "keep.txt").read_text(encoding="utf-8") == "existing"


def test_r22_json_manifest_normalizes_nonfinite_values_and_paths(tmp_path: Path):
    destination = tmp_path / "manifest.json"

    r22.write_json(destination, {"missing": math.nan, "path": tmp_path})
    loaded = json.loads(destination.read_text(encoding="utf-8"))

    assert loaded == {"missing": None, "path": str(tmp_path)}


def test_r22_h0_scale_validation_only_allows_zero_targets_for_h0():
    assert r22.validate_case_scale(phase="h0", target_count=0, period_count=5)

    with pytest.raises(ValueError, match="target_count > 0"):
        r22.validate_case_scale(phase="screening", target_count=0, period_count=5)
    with pytest.raises(ValueError, match="target-free"):
        r22.validate_case_scale(phase="h0", target_count=1, period_count=5)


def test_r22_h0_false_alarm_rows_are_scoped_to_independent_target_free_seeds(monkeypatch):
    monkeypatch.setattr(
        r22.r20, "_off_h0_rows",
        lambda bundle: [{"status": "DESCRIPTIVE_ONLY_NOT_STATISTICAL_PFA",
                         "valid_cut_count": 100, "observed_cut_hit_count": 2}],
    )
    bundle = {"seed": 910001}

    independent = r22._h0_false_alarm_rows(bundle, phase="h0", role="OFF")
    paired = r22._h0_false_alarm_rows(bundle, phase="screening", role="OFF")
    ignored_role = r22._h0_false_alarm_rows(bundle, phase="h0", role="ON")

    assert independent[0]["h0_scope"] == "independent_target_free_seed"
    assert independent[0]["empirical_pfa_status"] == "SEED_CLUSTER_AGGREGATION_REQUIRED"
    assert paired[0]["h0_scope"] == "paired_OFF_role_not_an_independent_H0_seed"
    assert ignored_role == []


def test_r22_h0_false_alarm_rows_keep_method_and_mode_identity(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        r22.r20, "_condition_role_bundle",
        lambda *args: {"truths": [], "branch_dir": str(tmp_path / "branch")},
    )
    monkeypatch.setattr(r22.r21_eval, "_range_axis_audit", lambda bundle: {"status": "evaluable"})
    monkeypatch.setattr(
        r22.r20, "_off_h0_rows",
        lambda bundle: [{
            "condition": "A0_not_applicable", "role": "OFF", "cut_band_mode": "union_full_band",
            "status": "DESCRIPTIVE_ONLY_NOT_STATISTICAL_PFA", "valid_cut_count": 100,
            "observed_cut_hit_count": 2, "observed_cut_hit_fraction": 0.02,
        }],
    )

    tables = r22._evaluate_branches(
        phase="h0", case_id="h0_seed1", seed=1, case_root=tmp_path,
        pipeline_manifest={}, binary_identity={},
        branch_records=[{
            "branch_name": "A0_off", "method_id": "A0", "mode": "not_applicable",
            "role": "OFF", "status": "passed",
        }],
    )

    row = tables["h0_false_alarm_metrics"][0]
    assert row["method_id"] == "A0"
    assert row["mode"] == "not_applicable"
    assert row["condition"] == "A0_not_applicable"
    assert row["h0_scope"] == "independent_target_free_seed"


def test_r22_case_compaction_removes_only_regenerable_binary_files(tmp_path: Path):
    case_root = tmp_path / "case"
    case_root.mkdir()
    (case_root / "case_manifest.json").write_text(
        '{"status":"completed_with_gaps"}\n', encoding="utf-8"
    )
    (case_root / "period.bin").write_bytes(b"regenerable input")
    (case_root / "production.log").write_text("runtime evidence\n", encoding="utf-8")
    (case_root / "result.csv").write_text("metric,value\nvalid,1\n", encoding="utf-8")

    cleanup = r22._compact_case_artifacts(case_root)

    assert not (case_root / "period.bin").exists()
    assert (case_root / "production.log").is_file()
    assert (case_root / "result.csv").is_file()
    assert cleanup["removed_file_count"] == 1
    assert cleanup["removed_bytes"] == len(b"regenerable input")
    assert (case_root / "raw_cleanup.json").is_file()
    manifest = json.loads((case_root / "case_manifest.json").read_text(encoding="utf-8"))
    assert manifest["raw_cleanup"]["remaining_bin_count"] == 0


def test_r22_case_compaction_removes_csi_maps_but_keeps_summary_csv(tmp_path: Path):
    case_root = tmp_path / "case"
    csi_dir = case_root / "production" / "A0_OFF" / "result" / "csi_metrics" / "run1"
    csi_dir.mkdir(parents=True)
    (case_root / "case_manifest.json").write_text(
        '{"status":"completed_with_gaps"}\n', encoding="utf-8"
    )
    (csi_dir / "period000_power.npy").write_bytes(b"reproducible csi map")
    (csi_dir / "csi_metric_tap_summary.csv").write_text(
        "beam_id,before_mean_power,after_mean_power\n50,2,1\n", encoding="utf-8"
    )

    cleanup = r22._compact_case_artifacts(case_root)

    assert not (csi_dir / "period000_power.npy").exists()
    assert (csi_dir / "csi_metric_tap_summary.csv").is_file()
    assert cleanup["removed_numpy_diagnostic_file_count"] == 1
    assert cleanup["removed_numpy_diagnostic_bytes"] == len(b"reproducible csi map")


def test_r22_method_config_has_feasible_pkr_support_without_lowering_the_gate():
    from scripts import run_production_hierarchical_calibration_pilot as calibration

    config = calibration.load_pilot_config(
        Path(__file__).resolve().parents[1]
        / "docs/research/phase_r/R22/config/r22_method_config.json"
    )
    production = config["production"]

    assert production["min_support"] == 8
    assert production["range_band_bins"] == 133
    assert production["range_band_bins"] >= production["min_support"]
    assert 4095 % production["range_band_bins"] >= production["min_support"]
    assert production["csi_metrics_dump_power_maps"] is False
    assert production["csi_metrics_dump_intermediate_maps"] is False


def test_r22_extra_correction_branches_share_configured_production_range(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"paired input")
    calls = []

    monkeypatch.setattr(
        r22, "_delay_corrected_inputs", lambda *args, **kwargs: ([source], [])
    )
    monkeypatch.setattr(
        r22.stage1, "run_production_branch",
        lambda *args, **kwargs: (calls.append(kwargs) or {
            "status": "passed", "production_returncode": 0,
        }),
    )
    monkeypatch.setattr(
        r22.stage1, "audit_production_path_evidence",
        lambda record: {"status": "passed"},
    )
    monkeypatch.setattr(r22.stage1, "_write_json", lambda *args, **kwargs: None)

    r22._run_extra_delay_method(
        tmp_path,
        "case",
        "blind",
        3.5,
        "target_free_OFF_estimator",
        None,
        {role: [source] for role in ("ON", "OFF", "TO")},
        tmp_path / "source.xml",
        tmp_path,
        {"beam_id": 50, "pulse_len": 1, "channel_count": 4, "fs_hz": 1.0},
        1,
        {"processing_range_start": 666, "processing_range_end": 1197},
    )

    assert len(calls) == 3
    assert all(call["xml_overrides"]["rg_st"] == 666 for call in calls)
    assert all(call["xml_overrides"]["rg_ed"] == 1197 for call in calls)


def test_r22_runner_loads_and_records_an_explicit_method_configuration(tmp_path: Path):
    config_path = r22.DEFAULT_METHOD_CONFIG
    scenario_path = tmp_path / "scenario.json"

    config, methods = r22._load_method_configuration(
        config_path, scenario_path, ("PKR",)
    )

    assert config["scenario_template"] == str(scenario_path.resolve())
    assert config["production"]["min_support"] == 8
    assert config["production"]["range_band_bins"] == 133
    assert [method["method_id"] for method in methods] == ["PKR"]


def test_r22_v2_range_roi_design_preserves_v1a_scope_and_quality_gates():
    root = Path(__file__).resolve().parents[1]
    v1a = json.loads(
        (root / "docs/research/phase_r/R22/config/r22_extension_experiment_design.json")
        .read_text(encoding="utf-8")
    )
    v2_path = root / "docs/research/phase_r/R22/config/r22_extension_experiment_design_v2_range_roi.json"
    v2 = json.loads(v2_path.read_text(encoding="utf-8"))
    v2_method = json.loads(
        (root / v2["common"]["method_config"]).read_text(encoding="utf-8")
    )

    assert v2["design_id"] == "R22_EXTENSION_20261028_V2_RANGE_ROI"
    assert v2["supersedes_design_id"] == v1a["design_id"]
    assert v2["seed_sets"] == v1a["seed_sets"]
    assert v2["expected_case_counts"] == v1a["expected_case_counts"]
    assert v2["target_counts"] == v1a["target_counts"]
    assert v2["period_count"] == v1a["period_count"]
    assert v2_method["production"]["min_support"] == 8
    assert v2_method["production"]["robust_phase_threshold_rad"] == 0.35
    assert v2_method["production"]["processing_range_start"] == 666
    assert v2_method["production"]["processing_range_end"] == 1197


def test_r22_attaches_production_evidence_gates_before_aggregating_branch_status(monkeypatch):
    def audit(record):
        return {"status": "passed", "branch": record["method_id"]}

    monkeypatch.setattr(r22.stage1, "audit_production_path_evidence", audit)
    branches = [
        {"method_id": "C0", "role": "ON", "status": "passed", "production_returncode": 0},
        {"method_id": "Known", "role": "ALL", "status": "NOT_AVAILABLE"},
    ]

    audited = r22._audit_production_branches(branches)

    assert audited[0]["production_path_gate"] == {"status": "passed", "branch": "C0"}
    assert audited[1] == branches[1]
    assert r22._all_production_branches_passed(audited)


def test_r22_rejects_any_production_branch_with_failed_evidence_gate(monkeypatch):
    def audit(record):
        return {"status": "failed", "reasons": ["runtime_xml_missing"]}

    monkeypatch.setattr(r22.stage1, "audit_production_path_evidence", audit)
    branches = [{"method_id": "C0", "role": "ON", "status": "passed", "production_returncode": 0}]

    audited = r22._audit_production_branches(branches)

    assert not r22._all_production_branches_passed(audited)
