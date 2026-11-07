import csv

import json
import pytest

from scripts.phase_r.r20_harmonized_task_review import (
    _off_h0_rows,
    _metric_evaluability_rows,
    _find_estimator_details,
    _parameter_records,
    _paired_r8_causal_opportunity_rows,
    _r8_seed_summary_rows,
    _range_rate_doppler_audit,
    _write_manifest,
)
from scripts.phase_r.r20_task_evaluator import causal_effects_from_hit_fractions


def test_causal_effects_use_a0_minus_b_and_gate_recovery_ratio_on_positive_loss():
    result = causal_effects_from_hit_fractions(a0=2 / 3, current=0.0, known=2 / 3)
    assert result == {
        "loss_L_A0_minus_B": 2 / 3,
        "gain_G_Known_minus_B": 2 / 3,
        "residual_E_Known_minus_A0": 0.0,
        "recovery_ratio_G_over_L": 1.0,
        "status": "EVALUABLE",
    }
    no_loss = causal_effects_from_hit_fractions(a0=0.0, current=1 / 3, known=2 / 3)
    assert no_loss["loss_L_A0_minus_B"] < 0
    assert no_loss["recovery_ratio_G_over_L"] is None
    assert no_loss["status"] == "NOT_DEFINED_NO_POSITIVE_A0_MINUS_B_LOSS"


def test_h0_geometry_reports_cut_counts_as_descriptive_and_keeps_statistical_pfa_unavailable(tmp_path):
    diagnostic = tmp_path / "result/debug/cfar_geometry_diagnostics.csv"
    diagnostic.parent.mkdir(parents=True)
    with diagnostic.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "period_id", "beam_id", "branch", "valid_cut_count", "hit_cut_count",
        ])
        writer.writeheader()
        writer.writerows([
            {"period_id": 0, "beam_id": 50, "branch": "union_full_band", "valid_cut_count": 100, "hit_cut_count": 2},
            {"period_id": 1, "beam_id": 50, "branch": "union_full_band", "valid_cut_count": 100, "hit_cut_count": 3},
            {"period_id": 0, "beam_id": 50, "branch": "union_dynamic_in_band", "valid_cut_count": 40, "hit_cut_count": 1},
        ])
    bundle = {
        "dataset": "R8", "dataset_key": "seed-test", "role": "OFF",
        "branch_dir": tmp_path, "case": {"case_manifest": {"conditions": {}}},
    }
    rows = _off_h0_rows(bundle)
    by_mode = {row["cut_band_mode"]: row for row in rows}
    full = by_mode["union_full_band"]
    assert full["observed_cut_hit_count"] == 5
    assert full["valid_cut_count"] == 200
    assert full["observed_cut_hit_fraction"] == 0.025
    assert full["status"] == "DESCRIPTIVE_ONLY_NOT_STATISTICAL_PFA"
    assert full["empirical_pfa_status"].startswith("NOT_EVALUABLE")
    assert by_mode["union_dynamic_in_band"]["valid_cut_count"] == 40


def test_h0_missing_geometry_is_explicitly_not_evaluable(tmp_path):
    bundle = {
        "dataset": "R12", "dataset_key": "seed-test", "role": "OFF",
        "branch_dir": tmp_path, "case": {"case_manifest": {"conditions": {}}},
    }
    rows = _off_h0_rows(bundle)
    assert len(rows) == 1
    assert rows[0]["status"] == "NOT_EVALUABLE_MISSING_H0_COUNTER"
    assert rows[0]["empirical_pfa_status"] == "NOT_EVALUABLE"


def test_doppler_audit_closes_truth_against_independent_saved_slant_range_history(tmp_path):
    scene = tmp_path / "scenes/A1_ON"
    (scene / "truth").mkdir(parents=True)
    (scene / "scenario_resolved.json").write_text(json.dumps({
        "waveform": {"fc_hz": 16e9}, "carrier_phase_sign": -1,
    }), encoding="utf-8")
    center_utc = 4.95
    rate = 43.4052588868
    lines = ["target_id,period_id,pulse_id,utc,range_m"]
    for pulse in range(60, 71):
        utc = center_utc + (pulse - 65) / 1300.0
        distance = 87_919.0 + rate * (pulse - 65) / 1300.0
        lines.append(f"T0,0,{pulse},{utc:.15f},{distance:.12f}")
    (scene / "truth/moving_target_truth.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    truth = {
        "target_id": "T0", "period_id": 0, "utc_mid": center_utc,
        "af_total_truth_hz": -2 * rate / (299_792_458 / 16e9),
    }
    result = _range_rate_doppler_audit(
        {"case": {"case_root": tmp_path}, "role": "ON"}, truth,
    )
    assert result["status"] == "VERIFIED_RANGE_RATE_DOPPLER"
    assert result["closure_verified"] is True
    assert abs(result["frequency_closure_error_hz"]) < result["closure_tolerance_hz"]
    assert result["source_path"].endswith("moving_target_truth.csv")
    assert result["scenario_source_path"].endswith("scenario_resolved.json")


def test_r8_seed_summary_keeps_seed101_unverified_and_summarizes_independent_seeds():
    rows = []
    for seed, status, hits in [
        (101, "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED", None),
        (202, "EVALUABLE", {"A0": 2, "B": 0, "Known": 2}),
        (303, "EVALUABLE", {"A0": 3, "B": 0, "Known": 3}),
    ]:
        for condition in ("A0", "B", "Known"):
            rows.append({
                "dataset": "R8", "dataset_key": f"seed{seed}", "seed": seed,
                "canonical_condition": condition, "evaluator_status": status,
                "identity_status": "IDENTITY_VALID", "unique_hits": hits[condition] if hits else None,
                "truth_opportunities": 3 if hits else None,
                "truth_opportunity_hit_fraction": hits[condition] / 3 if hits else None,
            })
    result = _r8_seed_summary_rows(rows)
    by_seed = {str(row["seed"]): row for row in result if row["row_type"] == "SEED"}
    assert len(by_seed) == 3
    assert by_seed["101"]["status"] == "NOT_EVALUABLE"
    assert by_seed["101"]["B_evaluator_status"] == "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED"
    assert by_seed["202"]["loss_L_A0_minus_B"] == 2 / 3
    assert by_seed["202"]["recovery_ratio_G_over_L"] == 1.0
    aggregate = next(row for row in result if row["row_type"] == "EVALUABLE_SEED_SUMMARY")
    assert aggregate["independent_seed_count"] == 2
    assert aggregate["loss_L_median"] == pytest.approx(5 / 6)
    assert aggregate["loss_L_min"] == pytest.approx(2 / 3)
    assert aggregate["loss_L_max"] == 1.0


def test_paired_r8_opportunities_show_detection_recovery_parameters_and_track_lineage():
    truth_row = {
        "target_id": "T0", "period_id": 0, "beam_id": 50,
        "visible": "1", "range_bin": 100, "range_m": 10_000.0,
        "e_mid": 0.0, "n_mid": 0.0, "af_total_truth_hz": 10.0,
    }
    conditions = ("A0", "B", "Known")
    bundles = []
    evaluations = {}
    for condition in conditions:
        detection_id = None if condition == "B" else f"D_{condition}"
        association = {
            "period_id": 0, "beam_id": 50, "target_id": "T0", "sample_key": "T0/0/50",
            "matched": detection_id is not None, "det_id": detection_id, "candidate_count": 1,
        }
        observations = [] if detection_id is None else [{
            "period_id": 0, "beam_id": 50, "track_id": f"track_{condition}", "det_id": detection_id,
            "state": "Confirmed", "matched_this_frame": True, "output_payload": True,
        }]
        bundle = {
            "dataset": "R8", "dataset_key": "seed202", "case_id": "case202", "seed": 202,
            "condition": condition, "canonical_condition": condition, "role": "ON",
            "truths": [truth_row], "track": {"observations": observations},
        }
        bundles.append(bundle)
        samples = {name: [] for name in ("angle_deg", "position_m", "range_m", "folded_doppler_hz", "radial_velocity_mps")}
        if detection_id is not None:
            samples["angle_deg"] = [{"sample_key": "T0/0/50", "error": 0.1 if condition == "A0" else 0.2}]
        evaluations[("R8", "seed202", condition, "ON")] = {
            "status": "EVALUABLE", "target_result": {
                "associations": [association], "parameter_samples": samples, "candidate_pair_audit": [],
            },
        }
    row = _paired_r8_causal_opportunity_rows(bundles, evaluations)[0]
    assert row["transition_type"] == "B_DETECTION_LOSS_KNOWN_RECOVERED"
    assert row["A0_det_id"] == "D_A0"
    assert row["B_det_id"] is None
    assert row["Known_det_id"] == "D_Known"
    assert row["A0_angle_deg_error"] == 0.1
    assert row["Known_angle_deg_error"] == 0.2
    assert row["A0_track_status"] == "CONFIRMED_MATCHED_OUTPUT_PAYLOAD"
    assert row["Known_track_id"] == "track_Known"


def test_metric_evaluability_distinguishes_observed_cut_fraction_from_statistical_pfa(tmp_path):
    diagnostic = tmp_path / "result/debug/cfar_geometry_diagnostics.csv"
    diagnostic.parent.mkdir(parents=True)
    diagnostic.write_text(
        "period_id,beam_id,branch,valid_cut_count,hit_cut_count\n"
        "0,50,union_full_band,100,2\n",
        encoding="utf-8",
    )
    bundle = {
        "dataset": "R8", "dataset_key": "seed202", "case_id": "case202", "seed": 202,
        "condition": "A0_Ideal", "canonical_condition": "A0", "role": "OFF",
        "branch_dir": tmp_path,
    }
    h0_rows = _off_h0_rows(bundle)
    rows = _metric_evaluability_rows(
        [bundle], {}, [], [], h0_rows, [],
    )
    by_metric = {row["metric"]: row for row in rows}
    observed = by_metric["cfar_observed_cut_fraction:union_full_band"]
    assert observed["value"] == 0.02
    assert observed["status"] == "EVALUABLE_DESCRIPTIVE_ONLY"
    assert by_metric["statistical_pfa"]["status"] == "NOT_EVALUABLE"


@pytest.mark.parametrize("role", ["ON", "TO"])
def test_metric_evaluability_records_target_scoring_and_raw_iq_dependent_metrics(tmp_path, role):
    diagnostic = tmp_path / "result/debug/cfar_geometry_diagnostics.csv"
    diagnostic.parent.mkdir(parents=True)
    diagnostic.write_text(
        "period_id,beam_id,branch,valid_cut_count,hit_cut_count\n"
        "0,50,union_full_band,100,2\n",
        encoding="utf-8",
    )
    bundle = {
        "dataset": "R12", "dataset_key": "seed404", "case_id": "case404", "seed": 404,
        "condition": "R12_M1_Physics_Matched", "canonical_condition": "M1", "role": role,
        "branch_dir": tmp_path,
    }
    evaluation = {
        "status": "EVALUABLE", "target_result": {
            "metrics": {"truth_opportunities": 3, "unique_hits": 2, "misses": 1,
                        "truth_unmatched_detection_rows": 4, "raw_detection_rows": 6,
                        "truth_opportunity_hit_fraction": 2 / 3},
        },
    }
    key = ("R12", "seed404", "R12_M1_Physics_Matched", role)
    rows = _metric_evaluability_rows(
        [bundle], {key: evaluation}, [], [], [], [],
    )
    by_metric = {row["metric"]: row for row in rows}
    assert by_metric["target_detection_hits"]["value"] == 2
    assert by_metric["target_detection_misses"]["denominator"] == 3
    assert by_metric["cfar_valid_cut_count:union_full_band"]["value"] == 100
    assert by_metric["cfar_valid_cut_count:union_full_band"]["status"] == "EVALUABLE"
    assert "statistical_pfa" not in by_metric
    if role == "ON":
        assert by_metric["target_complex_retention"]["status"] == "NOT_EVALUABLE"
        assert "raw iq" in by_metric["target_complex_retention"]["reason"].lower()


def test_input_manifest_hashes_independent_physics_truth_and_waveform_sources(tmp_path):
    case_root = tmp_path / "case"
    scene = case_root / "scenes/A1_ON"
    (scene / "truth").mkdir(parents=True)
    history = scene / "truth/moving_target_truth.csv"
    history.write_text("target_id,period_id,pulse_id,utc,range_m\nT0,0,0,0,100\n", encoding="utf-8")
    scenario = scene / "scenario_resolved.json"
    scenario.write_text('{"waveform":{"fc_hz":16000000000},"carrier_phase_sign":-1}\n', encoding="utf-8")
    truth_path = scene / "truth/truth_targets_by_beam.csv"
    truth_path.write_text("target_id,period_id,beam_id\nT0,0,50\n", encoding="utf-8")
    case_manifest = case_root / "case_manifest.json"
    case_manifest.write_text("{}\n", encoding="utf-8")
    branch_dir = case_root / "production/A0_Ideal_ON"
    (branch_dir / "result").mkdir(parents=True)
    (branch_dir / "branch_manifest.json").write_text("{}\n", encoding="utf-8")
    (branch_dir / "production.xml").write_text("<config/>\n", encoding="utf-8")
    (branch_dir / "result/run_manifest.json").write_text("{}\n", encoding="utf-8")
    (branch_dir / "result/runtime_config_dump.json").write_text("{}\n", encoding="utf-8")
    bundle = {
        "dataset": "R8", "dataset_key": "seed-test", "case_id": "case-test",
        "condition": "A0_Ideal", "role": "ON", "branch_dir": branch_dir,
        "identity": {}, "truth_path": truth_path,
        "axes": {"status": "DOPPLER_AXIS_NOT_AVAILABLE", "source_path": None},
        "detections": {"files": []}, "track": None,
        "case": {"case_root": case_root, "case_manifest_path": case_manifest},
    }
    _write_manifest(tmp_path / "output", [bundle], {}, {})
    manifest = json.loads((tmp_path / "output/manifests/r20_input_identity_manifest.json").read_text())
    entry = manifest["branches"][0]
    source_paths = {record["path"] for record in entry["input_files"]}
    assert any(path.endswith("moving_target_truth.csv") for path in source_paths)
    assert any(path.endswith("scenario_resolved.json") for path in source_paths)
    assert entry["physical_doppler_truth_sources"]["moving_truth_sha256"] is not None
    assert entry["physical_doppler_truth_sources"]["scenario_resolved_sha256"] is not None


def test_ground_speed_skips_duplicate_payload_key_even_when_one_track_row_links(tmp_path):
    truth = {
        "target_id": "T0", "period_id": 0, "beam_id": 50, "visible": "1",
        "range_bin": 100, "range_m": 10_000, "e_mid": 0, "n_mid": 0,
        "af_total_truth_hz": 10, "ve_mps": 3, "vn_mps": 4,
    }
    bundle = {
        "dataset": "R12", "dataset_key": "seed404", "case_id": "case404", "seed": 404,
        "condition": "R12_M1_Physics_Matched", "canonical_condition": "M1", "role": "ON",
        "truths": [truth],
        "track": {"observations": [{
            "period_id": 0, "beam_id": 50, "track_id": "7", "state": "Confirmed",
            "matched_this_frame": True, "output_payload": True, "det_id": "D0",
            "output_speed": 5, "payload_rows_for_key": 2,
        }]},
    }
    target_result = {"associations": [{
        "sample_key": "T0/0/50", "matched": True, "det_id": "D0",
    }]}
    rows = _parameter_records(bundle, target_result, None)
    ground_speed = next(row for row in rows if row["metric"] == "ground_speed_mps")
    assert ground_speed["valid_parameter_opportunities"] == 0
    assert ground_speed["missing_reasons"] == {"duplicate_output_payloads": 1}


def test_ground_speed_requires_confirmed_current_frame_payload(tmp_path):
    truth = {
        "target_id": "T0", "period_id": 0, "beam_id": 50, "visible": "1",
        "range_bin": 100, "range_m": 10_000, "e_mid": 0, "n_mid": 0,
        "af_total_truth_hz": 10, "ve_mps": 3, "vn_mps": 4,
    }
    bundle = {
        "dataset": "R12", "dataset_key": "seed404", "case_id": "case404", "seed": 404,
        "condition": "R12_M1_Physics_Matched", "canonical_condition": "M1", "role": "ON",
        "truths": [truth],
        "track": {"observations": [{
            "period_id": 0, "beam_id": 50, "track_id": "7", "state": "Tentative",
            "matched_this_frame": True, "output_payload": True, "det_id": "D0",
            "output_speed": 5, "payload_rows_for_key": 1,
        }]},
    }
    target_result = {"associations": [{
        "sample_key": "T0/0/50", "matched": True, "det_id": "D0",
    }]}
    rows = _parameter_records(bundle, target_result, None)
    ground_speed = next(row for row in rows if row["metric"] == "ground_speed_mps")
    assert ground_speed["valid_parameter_opportunities"] == 0
    assert ground_speed["missing_reasons"] == {"no_confirmed_output_payload": 1}


def test_r12_estimator_input_role_is_explicitly_qualified_when_inferred_from_source():
    details = _find_estimator_details({
        "correction_source": "M1_A1_ON_raw_C1_C2_blind_estimate",
        "truth_used_in_estimator": False,
        "input_preparation": {"ON": {
            "correction_source": "M1_A1_ON_raw_C1_C2_blind_estimate",
            "truth_used_in_estimator": False,
            "truth_used_to_apply_correction": False,
            "correction_audit": [{"status": "passed"}],
        }},
    })
    assert details["input_role"] == "A1_ON_RAW_C1_C2"
    assert details["input_role_evidence_status"] == "INFERRED_FROM_CORRECTION_SOURCE_LABEL"
    assert details["truth_used_to_apply_correction"] is False
