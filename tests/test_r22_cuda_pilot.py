import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.phase_r.physics_matched_delay import (
    estimate_physics_matched_delay,
    stage2_linear_fractional_delay,
)
from scripts.phase_r import run_r22_cuda_pilot as r22


def test_r22_case_identifier_encodes_physical_factors_and_sample_scale():
    value = r22.case_identifier("confirmatory", 222001, 0.5, 20.0, 4.0, 20, 10)

    assert value == (
        "confirmatory__seed_222001__vr_0d5__snr_20__delay_4ns__targets_20__periods_10"
    )


def test_v4_driver_identity_records_nvidia_smi_versions(monkeypatch):
    observed = []
    monkeypatch.setattr(
        r22.subprocess,
        "run",
        lambda command, **_kwargs: (
            observed.append(command)
            or SimpleNamespace(returncode=0, stdout="570.86.16\n", stderr="")
        ),
    )

    identity = r22._cuda_driver_identity()

    assert observed == [[
        "nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader,nounits",
    ]]
    assert identity["driver_versions"] == ["570.86.16"]


def test_v4_driver_identity_fails_closed_when_nvidia_smi_has_no_version(monkeypatch):
    monkeypatch.setattr(
        r22.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="unavailable"),
    )

    with pytest.raises(RuntimeError, match="no driver version"):
        r22._cuda_driver_identity()


def test_r22_zero_delay_reference_does_not_schedule_physical_blind_correction():
    methods = ("C0", "P1", "P2", "PK", "PKR")

    assert r22._method_ids_for_delay(methods, 0.0) == ("C0", "PK", "PKR")
    assert r22._method_ids_for_delay(methods, 4.0) == methods


def test_v4_runner_uses_only_registered_calibration_methods_for_each_delay(tmp_path: Path):
    assert r22._v4_calibration_method_ids(0.0) == ()
    assert r22._v4_calibration_method_ids(4.0) == ("C0", "PK")
    with pytest.raises(ValueError, match="no registered delay condition"):
        r22._v4_calibration_method_ids(2.0)

    config_path = r22.DEFAULT_METHOD_CONFIG
    scenario_path = tmp_path / "scenario.json"
    _, empty_contract = r22._load_method_configuration(
        config_path, scenario_path, (), allow_empty=True,
    )
    assert empty_contract == []
    with pytest.raises(ValueError, match="at least one method"):
        r22._load_method_configuration(config_path, scenario_path, ())


def test_v4_runner_labels_scientific_and_internal_production_roles_separately():
    branches = [
        {"method_id": "A0", "role": "ON"},
        {"method_id": "A0", "role": "OFF"},
        {"method_id": "C0", "role": "TO"},
        {"method_id": "C0", "role": "OFF"},
        {"method_id": "PK", "role": "ON"},
        {"method_id": "M1_CAUSAL_PREFIX", "role": "TO"},
        {"method_id": "M1_BATCH10_DIAGNOSTIC", "role": "ALL"},
    ]
    labeled = r22._annotate_v4_branch_classes(
        branches, phase="confirmatory", delay_ns=4.0,
    )
    by_identity = {(row["method_id"], row["role"]): row for row in labeled}
    assert by_identity[("A0", "ON")]["v4_execution_class"] == "internal_auxiliary_branch"
    assert by_identity[("C0", "TO")]["v4_registered_method_id"] == "C0"
    assert by_identity[("C0", "OFF")]["v4_execution_class"] == "internal_auxiliary_branch"
    assert by_identity[("PK", "ON")]["v4_registered_method_id"] == "PK_KNOWN"
    assert by_identity[("M1_CAUSAL_PREFIX", "TO")]["v4_registered_method_id"] == "M1_CAUSAL_PREFIX"
    assert by_identity[("M1_BATCH10_DIAGNOSTIC", "ALL")]["v4_execution_class"] == "non_branch_diagnostic"

    h0_labeled = r22._annotate_v4_branch_classes(
        [{"method_id": "M1_CAUSAL_PREFIX", "role": "TO"},
         {"method_id": "M1_CAUSAL_PREFIX", "role": "ON"}],
        phase="h0", delay_ns=4.0,
    )
    assert [row["v4_execution_class"] for row in h0_labeled] == [
        "internal_auxiliary_branch", "registered_scientific_branch",
    ]


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


def test_v4_output_complete_run_case_requires_matrix_authorization(tmp_path: Path):
    with pytest.raises(ValueError, match="matrix size-pilot authorization"):
        r22.run_case(
            tmp_path / "case", phase="engineering", seed=1937001,
            speed_mps=2.5, snr_db=20.0, delay_ns=4.0,
            target_count=20, period_count=10, first_beam=50, beam_count=1,
            stage2_binary=tmp_path / "missing_stage2",
            production_binary=tmp_path / "missing_production",
            v4_output_complete=True,
        )


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

    independent = r22._h0_false_alarm_rows(bundle, phase="h0", role="ON")
    paired = r22._h0_false_alarm_rows(bundle, phase="screening", role="OFF")
    internal_off = r22._h0_false_alarm_rows(bundle, phase="h0", role="OFF")

    assert independent[0]["h0_scope"] == "independent_target_free_seed"
    assert independent[0]["empirical_pfa_status"] == "SEED_CLUSTER_AGGREGATION_REQUIRED"
    assert paired[0]["h0_scope"] == "paired_OFF_role_not_an_independent_H0_seed"
    assert internal_off[0]["h0_scope"] == "internal_auxiliary_OFF_not_registered_h0"


def test_r22_h0_false_alarm_rows_keep_method_and_mode_identity(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        r22.r20, "_condition_role_bundle",
        lambda *args: {"truths": [], "branch_dir": str(tmp_path / "branch")},
    )
    monkeypatch.setattr(r22.r21_eval, "_range_axis_audit", lambda bundle: {"status": "evaluable"})
    monkeypatch.setattr(
        r22, "_h0_task_metric_row",
        lambda bundle, expected_period_count: {
            "status": "EVALUABLE_DESCRIPTIVE_H0_SEED",
            "h0_event_count": 1, "valid_period_count": expected_period_count,
            "h0_event_rate": 0.1, "false_confirmed_track_count": 0,
        },
    )
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
        pipeline_manifest={}, binary_identity={}, period_count=10,
        branch_records=[{
            "branch_name": "A0_on", "method_id": "A0", "mode": "not_applicable",
            "role": "ON", "status": "passed",
        }],
    )

    row = tables["h0_false_alarm_metrics"][0]
    assert row["method_id"] == "A0"
    assert row["mode"] == "not_applicable"
    assert row["condition"] == "A0_not_applicable"
    assert row["role"] == "ON"
    assert row["h0_scope"] == "independent_target_free_seed"
    assert tables["h0_task_metrics"][0]["status"] == "EVALUABLE_DESCRIPTIVE_H0_SEED"
    assert tables["h0_task_metrics"][0]["role"] == "ON"


def test_r22_h0_task_metrics_use_valid_periods_and_final_detections(tmp_path: Path):
    branch = tmp_path / "branch"
    debug = branch / "result" / "debug"
    debug.mkdir(parents=True)
    (debug / "cfar_geometry_diagnostics.csv").write_text(
        "period_id,beam_id,branch,valid_cut_count,hit_cut_count\n"
        "0,50,union_full_band,100,1\n"
        "1,50,union_full_band,100,0\n",
        encoding="utf-8",
    )
    (debug / "cluster_detection_lineage.csv").write_text(
        "record_type,period_id,beam_id,cluster_index,cluster_row,cluster_col,selected,raw_detection,postfilter_cluster_count,selected_candidate_count,raw_detection_count\n"
        "beam_summary,0,50,,,,,,0,0,0\n"
        "beam_summary,1,50,,,,,,1,1,1\n"
        "cluster,1,50,0,10,20,1,1,,,,\n",
        encoding="utf-8",
    )
    detections = [{
        "period_id": 1, "beam_id": 50, "det_id": "d1", "row": 10, "col": 20,
    }]
    bundle = {
        "branch_dir": branch,
        "case_id": "h0_case", "seed": 1937001, "condition": "C0_not_applicable",
        "role": "ON",
        "detections": {
            "rows": detections, "files": [Path("GMTI01.csv"), Path("GMTI02.csv")],
            "result_period_map": {1: 0, 2: 1},
            "source_status": "PER_RESULT_PRODUCTION_CSV",
        },
        "track": {
            "status": "evaluable",
            "observations": [{
                "period_id": 1, "beam_id": 50, "track_id": "t1", "state": "confirmed",
                "matched_this_frame": True, "det_id": "d1", "output_payload": True,
            }],
            "payload_duplicate_key_count": 0,
        },
    }

    row = r22._h0_task_metric_row(bundle, expected_period_count=2)

    assert row["status"] == "EVALUABLE_DESCRIPTIVE_H0_SEED"
    assert row["valid_period_count"] == 2
    assert row["detection_count"] == 1
    assert row["cluster_count"] == 1
    assert row["false_confirmed_track_count"] == 1
    assert row["h0_event_count"] == 1
    assert row["h0_event_rate"] == 0.5
    assert row["valid_cut_count"] == 200
    assert row["empirical_pfa_status"] == "NOT_EVALUABLE_NO_INDEPENDENT_H0_SEED_AGGREGATION"


def test_r22_h0_task_metrics_fail_closed_when_period_geometry_is_incomplete(tmp_path: Path):
    branch = tmp_path / "branch"
    debug = branch / "result" / "debug"
    debug.mkdir(parents=True)
    (debug / "cfar_geometry_diagnostics.csv").write_text(
        "period_id,beam_id,branch,valid_cut_count,hit_cut_count\n"
        "0,50,union_full_band,100,0\n",
        encoding="utf-8",
    )
    row = r22._h0_task_metric_row({"branch_dir": branch, "role": "ON",
                                  "detections": {}, "track": {}},
                                  expected_period_count=2)
    assert row["status"] == "NOT_EVALUABLE"
    assert "period_geometry_incomplete" in row["reason"]


def test_r22_h0_zero_detections_are_evaluable_when_all_output_audits_are_present(tmp_path: Path):
    branch = tmp_path / "branch"
    debug = branch / "result" / "debug"
    track_run = branch / "track_debug_runs" / "run1"
    debug.mkdir(parents=True)
    track_run.mkdir(parents=True)
    (debug / "cfar_geometry_diagnostics.csv").write_text(
        "period_id,beam_id,branch,valid_cut_count,hit_cut_count\n"
        "0,50,union_full_band,100,0\n"
        "1,50,union_full_band,100,0\n",
        encoding="utf-8",
    )
    (debug / "cluster_detection_lineage.csv").write_text(
        "record_type,period_id,beam_id,cluster_index,cluster_row,cluster_col,selected,raw_detection,postfilter_cluster_count,selected_candidate_count,raw_detection_count\n"
        "beam_summary,0,50,,,,,,0,0,0\n"
        "beam_summary,1,50,,,,,,0,0,0\n",
        encoding="utf-8",
    )
    (track_run / "track_states.csv").write_text("track_id\n", encoding="utf-8")
    (track_run / "track_events.csv").write_text("event_id\n", encoding="utf-8")
    (track_run / "track_output_payloads.csv").write_text("track_id\n", encoding="utf-8")
    bundle = {
        "branch_dir": branch, "case_id": "h0_empty", "seed": 1,
        "condition": "C0_not_applicable", "role": "ON",
        "detections": {
            "rows": [], "files": [Path("GMTI01.csv"), Path("GMTI02.csv")],
            "result_period_map": {}, "empty_result_numbers": [1, 2],
            "source_status": "PER_RESULT_PRODUCTION_CSV",
        },
        "track": {"status": "NOT_EVALUABLE", "reason": "no track observations"},
    }

    row = r22._h0_task_metric_row(bundle, expected_period_count=2)

    assert row["status"] == "EVALUABLE_DESCRIPTIVE_H0_SEED"
    assert row["detection_count"] == 0
    assert row["cluster_count"] == 0
    assert row["false_confirmed_track_count"] == 0
    assert row["h0_event_count"] == 0
    assert row["h0_event_rate"] == 0.0


def test_r22_detection_loader_retains_header_only_per_period_outputs(tmp_path: Path):
    result = tmp_path / "result"
    result.mkdir()
    header = "case_id,seed,result_id,period_id,beam_id,det_id,row,col\n"
    (result / "detection_results_GMTI01.csv").write_text(header, encoding="utf-8")
    (result / "detection_results_GMTI02.csv").write_text(
        header + "case,1,GMTI02,1,50,d1,10,20\n", encoding="utf-8",
    )

    bundle = r22.r20.load_detections(tmp_path)

    assert bundle["source_status"] == "PER_RESULT_PRODUCTION_CSV"
    assert bundle["empty_result_numbers"] == [1]
    assert bundle["result_period_map"] == {2: 1}
    assert bundle["per_result_rows"][1] == []
    assert len(bundle["rows"]) == 1


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


def test_causal_prefix_estimators_only_receive_periods_through_current_period(
    monkeypatch, tmp_path: Path,
):
    paths = []
    for period in range(3):
        path = tmp_path / f"period_{period:04d}.bin"
        path.write_bytes(f"period-{period}".encode())
        paths.append(path)

    estimator_inputs = []

    def fake_estimator(prefix, _xml):
        estimator_inputs.append([Path(item).name for item in prefix])
        prefix_bytes = sum(len(Path(item).read_bytes()) for item in prefix)
        return {
            "marker": len(prefix),
            "status": "ok",
            "delta_tau_ns": float(prefix_bytes),
            "truth_used_in_estimator": False,
        }

    def fake_qualify(estimate):
        correction = estimate["delta_tau_ns"]
        return {
            "m1": {"status": "AVAILABLE", "correction_ns": correction},
            "d3_huber": {"status": "AVAILABLE", "correction_ns": correction + 0.25},
        }

    monkeypatch.setattr(r22.r12, "_blind_m1_from_b_on", fake_estimator)
    monkeypatch.setattr(r22.r21, "qualify_blind_methods", fake_qualify)

    records = r22._causal_prefix_estimates(paths, tmp_path / "runtime.xml")

    assert estimator_inputs == [
        ["period_0000.bin"],
        ["period_0000.bin", "period_0001.bin"],
        ["period_0000.bin", "period_0001.bin", "period_0002.bin"],
    ]
    assert all(row["estimator_elapsed_sec"] >= 0.0 for row in records["M1_CAUSAL_PREFIX"])
    assert records["M1_CAUSAL_PREFIX"][1]["prefix_period_ids"] == [0, 1]
    assert records["M1_CAUSAL_PREFIX"][1]["applied_correction_ns"] == 16.0
    assert records["HUBER_D3_CAUSAL_PREFIX"][1]["applied_correction_ns"] == 16.25
    assert records["M1_CAUSAL_PREFIX"][0]["prefix_input_identities"] == [
        {"period_id": 0, "path": str(paths[0].resolve()), "exists": True,
         "bytes": paths[0].stat().st_size,
         "sha256": r22.sha256_file(paths[0])}
    ]
    prefix_zero_before = records["M1_CAUSAL_PREFIX"][0]
    paths[2].write_bytes(b"changed future period")
    future_mutated = r22._causal_prefix_estimates(paths, tmp_path / "runtime.xml")
    prefix_zero_after = future_mutated["M1_CAUSAL_PREFIX"][0]
    assert prefix_zero_after["estimate_ns"] == prefix_zero_before["estimate_ns"]
    assert prefix_zero_after["prefix_input_identities"] == prefix_zero_before["prefix_input_identities"]


def test_real_m1_huber_prefix_estimates_ignore_mutated_future_periods(tmp_path: Path, monkeypatch):
    sample_rate_hz = 60.0e6
    rng = np.random.default_rng(20261123)
    paths = [tmp_path / f"period_{period:04d}.npz" for period in range(3)]
    source_arrays = []
    for period in range(3):
        first = (rng.standard_normal((8, 2048)) + 1j * rng.standard_normal((8, 2048)))
        second = stage2_linear_fractional_delay(first, 0.24)
        source_arrays.append((first, second))

    def save_periods():
        for path, (first, second) in zip(paths, source_arrays):
            np.savez(path, first=first, second=second)

    def real_estimator(prefix_paths, _runtime_xml):
        first_rows, second_rows = [], []
        for path in prefix_paths:
            with np.load(path) as data:
                first_rows.append(data["first"])
                second_rows.append(data["second"])
        result = estimate_physics_matched_delay(
            np.concatenate(first_rows, axis=0),
            np.concatenate(second_rows, axis=0),
            fs_hz=sample_rate_hz,
            carrier_hz=16.0e9,
            geometry_delay_sec=0.0,
            carrier_phase_sign=-1,
            relative_response_model="constant_complex_gain",
        )
        result.update({
            "input_role": "A1_Current_unknown_error_ON_raw_C1_C2_only",
            "sample_rate_hz": sample_rate_hz,
            "delay_bounds_samples": [-0.49, 0.49],
            "truth_used_in_estimator": False,
        })
        return result

    monkeypatch.setattr(r22.r12, "_blind_m1_from_b_on", real_estimator)
    save_periods()
    before = r22._causal_prefix_estimates(paths, tmp_path / "runtime.xml")
    for method_id in ("M1_CAUSAL_PREFIX", "HUBER_D3_CAUSAL_PREFIX"):
        assert before[method_id][0]["estimator_status"] == "AVAILABLE"
        assert before[method_id][1]["estimator_status"] == "AVAILABLE"

    future_first, _ = source_arrays[2]
    future_second = rng.standard_normal(future_first.shape) + 1j * rng.standard_normal(future_first.shape)
    source_arrays[2] = (future_first, future_second)
    save_periods()
    after = r22._causal_prefix_estimates(paths, tmp_path / "runtime.xml")

    for method_id in ("M1_CAUSAL_PREFIX", "HUBER_D3_CAUSAL_PREFIX"):
        for period_id in (0, 1):
            prior = before[method_id][period_id]
            changed = after[method_id][period_id]
            assert changed["estimate_ns"] == pytest.approx(prior["estimate_ns"], abs=1.0e-12)
            assert changed["applied_correction_ns"] == pytest.approx(
                prior["applied_correction_ns"], abs=1.0e-12,
            )
            assert changed["estimator_status"] == prior["estimator_status"]
            assert changed["qualification"] == prior["qualification"]
            assert changed["prefix_input_identities"] == prior["prefix_input_identities"]
            assert changed["estimate_record"]["support_count"] == prior["estimate_record"]["support_count"]


def test_causal_prefix_failure_uses_zero_for_that_period_without_prior_fallback(
    monkeypatch, tmp_path: Path,
):
    paths = []
    for period in range(2):
        path = tmp_path / f"period_{period:04d}.bin"
        path.write_bytes(bytes([period + 1]))
        paths.append(path)

    monkeypatch.setattr(
        r22.r12, "_blind_m1_from_b_on",
        lambda prefix, _xml: {"period": len(prefix), "truth_used_in_estimator": False},
    )

    def qualify(estimate):
        status = "AVAILABLE" if estimate["period"] == 2 else "NOT_AVAILABLE"
        return {
            "m1": {"status": status, "correction_ns": 3.25 if status == "AVAILABLE" else None},
            "d3_huber": {"status": "NOT_AVAILABLE", "correction_ns": None},
        }

    monkeypatch.setattr(r22.r21, "qualify_blind_methods", qualify)
    records = r22._causal_prefix_estimates(paths, tmp_path / "runtime.xml")

    m1 = records["M1_CAUSAL_PREFIX"]
    assert [row["applied_correction_ns"] for row in m1] == [0.0, 3.25]
    assert m1[0]["fallback_status"] == "ESTIMATOR_UNAVAILABLE_ZERO_CORRECTION"
    assert m1[1]["fallback_status"] is None
    assert records["HUBER_D3_CAUSAL_PREFIX"][0]["applied_correction_ns"] == 0.0


def test_periodwise_delay_correction_uses_same_period_estimate(monkeypatch, tmp_path: Path):
    source_paths = []
    for period in range(3):
        path = tmp_path / f"raw_{period}.bin"
        path.write_bytes(b"raw")
        source_paths.append(path)
    observed = []

    def rewrite(_source, destination, **kwargs):
        observed.append((Path(destination).name, kwargs["delta_tau_sec"]))
        Path(destination).write_bytes(b"corrected")
        return {"status": "written"}

    monkeypatch.setattr(r22.stage1, "rewrite_float32_protocol_delay", rewrite)
    corrections = [
        {"period_id": period, "applied_correction_ns": value}
        for period, value in enumerate((0.0, 1.5, 3.25))
    ]

    outputs, audits = r22._delay_corrected_inputs_by_period(
        tmp_path, "M1_CAUSAL_PREFIX", "ON", source_paths, corrections,
        {"pulse_len": 1, "channel_count": 4, "fs_hz": 60.0e6,
         "correction_channel_indices": [1, 2]},
    )

    assert len(outputs) == len(source_paths)
    assert [item[0] for item in observed] == [f"period_{period:04d}.bin" for period in range(3)]
    assert [item[1] for item in observed] == pytest.approx([0.0, 1.5e-9, 3.25e-9])
    assert len(audits) == 3


def test_causal_method_runs_one_continuous_production_branch_per_role(
    monkeypatch, tmp_path: Path,
):
    raw_roles = {}
    for role in ("ON", "OFF", "TO"):
        role_paths = []
        for period in range(3):
            path = tmp_path / "raw" / role / f"period_{period:04d}.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{role}-{period}".encode())
            role_paths.append(path)
        raw_roles[role] = role_paths

    def fake_correct(_root, _method, role, sources, estimates, _layout):
        paths = []
        for period, _source in enumerate(sources):
            path = tmp_path / "corrected" / role / f"period_{period:04d}.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"corrected")
            paths.append(path)
        return paths, [{"period_id": i, "applied_correction_ns": row["applied_correction_ns"]}
                       for i, row in enumerate(estimates)]

    calls = []
    monkeypatch.setattr(r22, "_delay_corrected_inputs_by_period", fake_correct)
    monkeypatch.setattr(
        r22.stage1, "run_production_branch",
        lambda *args, **kwargs: (calls.append((args, kwargs)) or {
            "status": "passed", "production_returncode": 0,
        }),
    )
    monkeypatch.setattr(r22.stage1, "audit_production_path_evidence", lambda _row: {"status": "passed"})
    monkeypatch.setattr(r22.stage1, "_write_json", lambda *args, **kwargs: None)
    estimates = [
        {"period_id": i, "applied_correction_ns": float(i), "fallback_status": None}
        for i in range(3)
    ]

    branches = r22._run_causal_prefix_delay_method(
        tmp_path / "case", case_id="case-1", method_id="M1_CAUSAL_PREFIX",
        period_estimates=estimates, raw_roles=raw_roles,
        source_xml=tmp_path / "source.xml", truth_root=tmp_path / "truth",
        layout={"beam_id": 50, "pulse_len": 1, "channel_count": 4, "fs_hz": 60.0e6},
        production_config={"processing_range_start": 666, "processing_range_end": 1197},
    )

    assert [row["role"] for row in branches] == ["ON", "OFF", "TO"]
    assert len(calls) == 3
    for args, kwargs in calls:
        assert args[5] == 3
        input_dir = Path(args[2])
        assert len(list(input_dir.glob("period_*.bin"))) == 3
        assert args[6] == "local"
    assert all(row["continuous_track_manager_run"] is True for row in branches)


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


def test_v4_cleanup_does_not_delete_raw_input_before_response_verification(tmp_path: Path):
    raw_input = tmp_path / "period_0000.bin"
    raw_input.write_bytes(b"raw input")

    with pytest.raises(RuntimeError, match="matrix after complete archive verification"):
        r22._compact_case_artifacts(tmp_path, require_v4_response=True)

    assert raw_input.read_bytes() == b"raw input"
