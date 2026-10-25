"""Contract tests for the channel-delay Stage-1 configuration."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/research/channel_delay_stage1_formal.json"
EXPECTED_TOP_LEVEL_KEYS = {
    "schema_version",
    "scientific_input",
    "delay_sweep_ns",
    "delay_ranges_ns",
    "working_points",
    "pilot",
    "formal",
    "monte_carlo",
    "paired_control",
    "cfar",
    "statistics",
    "ai_training",
    "router_enabled",
    "native_four_channel_stap",
}
EXPECTED_WORKING_POINT_KEYS = {
    "name",
    "beam_id",
    "expected_bin",
    "range_m",
    "texture_sigma",
    "clutter_rho",
    "target_velocity_mps",
    "target_snr_db",
}
EXPECTED_WORKING_POINT_NAMES = {
    "base",
    "low_snr",
    "high_velocity",
    "alternate_texture_geometry",
}
EXPECTED_FORMAL_CASE_GROUPS = {
    "base": {
        "point": "base",
        "seeds": [101, 202, 303],
        "target_velocities_mps": [6.7, 12.0],
        "target_snr_db": [30.0, 35.0],
    },
    "low_snr": {
        "point": "low_snr",
        "seeds": [101],
        "target_velocities_mps": [6.7],
        "target_snr_db": [20.0],
    },
    "high_velocity": {
        "point": "high_velocity",
        "seeds": [101],
        "target_velocities_mps": [12.0],
        "target_snr_db": [30.0],
    },
    "alternate_texture_geometry": {
        "point": "alternate_texture_geometry",
        "seeds": [101],
        "target_velocities_mps": [6.7],
        "target_snr_db": [30.0],
    },
}


def load_stage1_config() -> dict[str, object]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_stage1_config_declares_delay_sweep_and_disabled_ai() -> None:
    config = load_stage1_config()
    assert config["delay_sweep_ns"] == [0, 1, -1, 2, -2, 4, -4, 8, -8]
    assert config["ai_training"] is False
    assert config["router_enabled"] is False
    assert config["native_four_channel_stap"] is False
    scientific_input = config["scientific_input"]
    assert isinstance(scientific_input, dict)
    assert scientific_input["fusion_pairs"] == [[1, 3], [2, 4]]


def test_stage1_config_has_exact_schema_and_working_point_registry() -> None:
    config = load_stage1_config()
    assert set(config) == EXPECTED_TOP_LEVEL_KEYS

    points = config["working_points"]
    assert isinstance(points, list)
    assert all(isinstance(point, dict) for point in points)
    names = [point["name"] for point in points]
    assert len(names) == len(set(names))
    assert set(names) == EXPECTED_WORKING_POINT_NAMES
    assert all(set(point) == EXPECTED_WORKING_POINT_KEYS for point in points)


def test_stage1_config_declares_four_channel_input_and_fusion_formulas() -> None:
    config = load_stage1_config()
    scientific_input = config["scientific_input"]
    assert isinstance(scientific_input, dict)
    assert scientific_input["source"] == "four_channel_protocol_iq"
    assert scientific_input["protocol_channel_count"] == 4
    assert scientific_input["f1_formula"] == "(channel_1 + channel_3) / 2"
    assert scientific_input["f2_formula"] == "(channel_2 + channel_4) / 2"


def test_stage1_config_has_paired_control_and_registered_working_points() -> None:
    config = load_stage1_config()
    assert config["paired_control"] == {"formula": "ON-OFF-TO", "dtype": "float32"}
    points = config["working_points"]
    assert isinstance(points, list)
    names = {point["name"] for point in points}
    assert EXPECTED_WORKING_POINT_NAMES <= names
    formal = config["formal"]
    assert isinstance(formal, dict)
    assert set(formal["seeds"]) == {101, 202, 303}
    assert set(formal["target_velocities_mps"]) == {6.7, 12.0}
    assert min(formal["target_snr_db"]) < 30.0


def test_stage1_branch_contract_has_four_conditions_and_truth_blind_a3() -> None:
    from scripts.run_delay_stage1_formal import build_stage1_branch_contract

    contract = build_stage1_branch_contract()
    assert set(contract["conditions"]) == {
        "A0_Ideal",
        "A1_Current_unknown_error",
        "A2_Known_error_correction_upper_bound",
        "A3_Blind_target_free_estimated_correction",
    }
    assert contract["requirements"]["A3_Blind_target_free_estimated_correction"]["truth_used_in_estimator"] is False


def test_stage1_condition_subset_is_fail_closed_and_can_skip_blind_estimation() -> None:
    from scripts.run_delay_stage1_formal import (
        _normalize_stage1_conditions,
        _stage1_delay_estimation_requested,
    )

    assert _normalize_stage1_conditions(None) == (
        "A0_Ideal",
        "A1_Current_unknown_error",
        "A2_Known_error_correction_upper_bound",
        "A3_Blind_target_free_estimated_correction",
    )
    abc = _normalize_stage1_conditions((
        "A0_Ideal",
        "A1_Current_unknown_error",
        "A2_Known_error_correction_upper_bound",
    ))
    assert abc == (
        "A0_Ideal",
        "A1_Current_unknown_error",
        "A2_Known_error_correction_upper_bound",
    )
    assert _stage1_delay_estimation_requested(abc) is False
    assert _stage1_delay_estimation_requested(_normalize_stage1_conditions(None)) is True

    with pytest.raises(ValueError, match="requires A0 and A1"):
        _normalize_stage1_conditions(("A2_Known_error_correction_upper_bound",))
    with pytest.raises(ValueError, match="duplicate"):
        _normalize_stage1_conditions(("A0_Ideal", "A0_Ideal"))


def test_additive_triplet_reports_pass_and_not_evaluable_failure() -> None:
    import numpy as np

    from scripts.run_delay_stage1_formal import audit_additive_triplet

    off = np.array([1 + 2j, 2 - 1j], dtype=np.complex64)
    target = np.array([3 - 1j, -2 + 4j], dtype=np.complex64)
    passed = audit_additive_triplet(off + target, off, target, tolerance=1e-5)
    assert passed["status"] == "passed"

    failed = audit_additive_triplet(np.ones(2, complex), np.zeros(2, complex), np.zeros(2, complex), tolerance=1e-6)
    assert failed["status"] == "NOT_EVALUABLE"
    assert failed["reason"] == "on_minus_off_minus_target_exceeds_tolerance"


def test_stage2_area_clutter_gate_uses_observed_injection_and_reachable_geometry() -> None:
    from scripts.run_delay_stage1_formal import audit_stage2_area_clutter_report

    scenario = {
        "platform": {"height_m": 6000.0, "origin_alt_m": 6000.0},
        "scene": {
            "ground_z_m": 0.0,
            "range_min_m": 84_500.0,
            "range_max_m": 85_500.0,
            "area_clutter": {"enabled": True, "model": "continuous_texture"},
        },
    }
    report = "\n".join((
        "- continuous_area_packets: 390",
        "- continuous_area_samples: 1952655",
        "- area_mean_power_measured: 0.0223",
    ))
    passed = audit_stage2_area_clutter_report(report, scenario)
    assert passed["status"] == "passed"
    assert passed["continuous_area_packets"] == 390
    assert passed["continuous_area_samples"] == 1952655
    assert passed["minimum_slant_range_m"] == 6000.0
    assert passed["ground_range_min_m"] > 0.0

    zero_injection = audit_stage2_area_clutter_report(
        "\n".join((
            "- continuous_area_packets: 0",
            "- continuous_area_samples: 0",
            "- area_mean_power_measured: 0",
        )),
        scenario,
    )
    assert zero_injection["status"] == "NOT_EVALUABLE"
    assert "continuous_area_packets_not_positive" in zero_injection["reasons"]

    unreachable = dict(scenario)
    unreachable["scene"] = {
        **scenario["scene"],
        "range_min_m": 2500.0,
        "range_max_m": 5000.0,
    }
    failed_geometry = audit_stage2_area_clutter_report(report, unreachable)
    assert failed_geometry["status"] == "NOT_EVALUABLE"
    assert "range_window_below_ground_slant_horizon" in failed_geometry["reasons"]


def test_production_smoke_gate_requires_current_runtime_csi_and_valid_cfar_branch(tmp_path: Path) -> None:
    from scripts.run_delay_stage1_formal import audit_production_path_evidence

    branch = tmp_path / "A_ON"
    result_dir = branch / "result"
    metrics_dir = result_dir / "csi_metrics" / "run1"
    metrics_dir.mkdir(parents=True)
    (branch / "production.xml").write_text("<root/>", encoding="utf-8")
    (branch / "gmt_pipe_core.log").write_text(
        "[TIMING-SCOPE] pulse_compression: 1 ms\n"
        "[CSI][CANCELLATION] period=0 beam=50\n[CFAR][SUMMARY] beam=50\n",
        encoding="utf-8",
    )
    (result_dir / "runtime_config_dump.json").write_text("{}\n", encoding="utf-8")
    (metrics_dir / "csi_metric_tap_summary.csv").write_text(
        "period_id,roi_name,status\n0,active_support_unmasked,diagnostic\n",
        encoding="utf-8",
    )
    record = {
        "status": "passed",
        "production_returncode": 0,
        "production_xml": str(branch / "production.xml"),
        "production_log": str(branch / "gmt_pipe_core.log"),
        "result_dir": str(result_dir),
        "cfar_geometry": {
            "status": "NOT_EVALUABLE",
            "branch_summaries": {
                "union_dynamic_in_band": {"status": "NOT_EVALUABLE", "valid_cut_count": 0},
                "union_full_band": {"status": "evaluable", "valid_cut_count": 100},
            },
        },
    }
    passed = audit_production_path_evidence(record)
    assert passed["status"] == "passed"
    assert passed["valid_cfar_branches"] == ["union_full_band"]
    assert passed["csi_tap_row_count"] == 1

    record["cfar_geometry"]["branch_summaries"]["union_full_band"]["valid_cut_count"] = 0
    failed = audit_production_path_evidence(record)
    assert failed["status"] == "NOT_EVALUABLE"
    assert "valid_cfar_cut_branch_missing" in failed["reasons"]


def test_known_correction_closure_is_measured_on_fused_production_inputs(monkeypatch) -> None:
    import numpy as np

    from scripts import run_delay_stage1_formal as stage1

    reference_path = Path("reference.bin")
    candidate_path = Path("candidate.bin")
    source = np.array([[1 + 2j, 3 - 1j]], dtype=np.complex128)

    def load_fused(paths, layout):
        if paths[0] == reference_path:
            return source.copy(), source.copy() * 2.0
        return source.copy(), source.copy() * 2.0

    monkeypatch.setattr(stage1, "_load_fused_from_periods", load_fused)
    passed = stage1.audit_fused_input_closure(
        [reference_path], [candidate_path], {"pulse_len": 2}
    )
    assert passed["status"] == "passed"
    assert passed["channels"]["F1"]["relative_rms_error"] == 0.0

    def load_mismatched(paths, layout):
        if paths[0] == reference_path:
            return source.copy(), source.copy()
        return source.copy(), source.copy() * 1.1

    monkeypatch.setattr(stage1, "_load_fused_from_periods", load_mismatched)
    failed = stage1.audit_fused_input_closure(
        [reference_path], [candidate_path], {"pulse_len": 2}
    )
    assert failed["status"] == "failed"
    assert failed["channels"]["F2"]["relative_rms_error"] > 1.0e-3


def test_target_off_waterfall_keeps_missing_denominators_not_evaluable(tmp_path: Path) -> None:
    from scripts.run_delay_stage1_formal import evaluate_target_off_waterfall

    result = evaluate_target_off_waterfall(
        tmp_path,
        None,
        {"status": "passed", "hit_cells": 12, "clusters": 3, "selected": 2},
        1e-6,
    )
    assert result["theoretical_go_cfar_cell_pfa"] == 1e-6
    assert result["cell_false_hit_fraction"]["status"] == "NOT_EVALUABLE"
    assert result["protocol_false_detections"]["status"] == "NOT_EVALUABLE"
    assert "empirical_structured_clutter_false_hit_fraction" in result


def test_target_off_waterfall_reports_separate_explicit_layers(tmp_path: Path) -> None:
    result_dir = tmp_path / "result"
    debug_dir = tmp_path / "debug"
    result_dir.mkdir()
    debug_dir.mkdir()
    (result_dir / "detection_results_GMTI01.csv").write_text(
        "det_index,range_m\n0,1\n1,2\n2,3\n", encoding="utf-8"
    )
    (debug_dir / "track_states.csv").write_text(
        "result_id,track_id,state\n1,7,Confirmed\n", encoding="utf-8"
    )
    from scripts.run_delay_stage1_formal import evaluate_target_off_waterfall

    result = evaluate_target_off_waterfall(
        result_dir,
        debug_dir,
        {
            "status": "passed",
            "hit_cells": 4,
            "valid_cut_count": 100,
            "clusters": 2,
            "cluster_ids": ["c1", "c2"],
            "selected": 2,
        },
        1e-6,
    )
    assert result["cell_false_hit_fraction"]["value"] == 0.04
    assert result["false_clusters"]["value"] == 2
    assert result["protocol_false_detections"]["value"] == 3
    assert result["false_tracks"]["value"] == 1


def test_cfar_geometry_summary_keeps_branch_hits_separate_when_cut_sets_overlap() -> None:
    from scripts.run_delay_stage1_formal import summarize_cfar_geometry_rows

    rows = [
        {
            "period_id": 0,
            "beam_id": 50,
            "branch": "union_dynamic_in_band",
            "valid_cut_count": 100,
            "hit_cut_count": 4,
        },
        {
            "period_id": 0,
            "beam_id": 50,
            "branch": "union_full_band",
            "valid_cut_count": 100,
            "hit_cut_count": 5,
        },
    ]

    result = summarize_cfar_geometry_rows(rows)

    assert result["status"] == "NOT_EVALUABLE"
    assert result["cell_pfa"] is None
    assert result["cell_pfa_status"] == "NOT_EVALUABLE"
    assert result["tested_cut_hit_fraction"] is None
    assert result["tested_cut_hit_fraction_status"] == "NOT_EVALUABLE"
    assert result["reason"] == "multiple_detector_branches_require_unique_hit_union"
    assert result["branch_summaries"]["union_dynamic_in_band"]["hit_fraction"] == 0.04
    assert result["branch_summaries"]["union_full_band"]["hit_fraction"] == 0.05
    assert result.get("valid_cut_denominator_status") == "passed"
    assert result.get("valid_cut_denominator_row_count") == len(rows)

    invalid = summarize_cfar_geometry_rows([
        {
            "period_id": 0,
            "beam_id": 50,
            "branch": "union_full_band",
            "valid_cut_count": 0,
            "hit_cut_count": 0,
        }
    ])
    assert invalid.get("valid_cut_denominator_status") == "NOT_EVALUABLE"

    duplicated = summarize_cfar_geometry_rows([rows[0], rows[0]])
    assert duplicated.get("valid_cut_denominator_status") == "NOT_EVALUABLE"


def test_cfar_geometry_summary_pools_one_branch_and_never_labels_it_pfa() -> None:
    from scripts.run_delay_stage1_formal import summarize_cfar_geometry_rows

    rows = [
        {
            "period_id": period_id,
            "beam_id": 50,
            "branch": "full_band",
            "threshold_test_count": 120,
            "valid_cut_count": 100,
            "hit_cut_count": hits,
        }
        for period_id, hits in ((0, 2), (1, 3))
    ]

    result = summarize_cfar_geometry_rows(rows)

    assert result["status"] == "evaluable"
    assert result["threshold_test_count"] == 240
    assert result["valid_cut_count"] == 200
    assert result["hit_cut_count"] == 5
    assert result["tested_cut_hit_fraction"] == 0.025
    assert result["tested_cut_hit_fraction_status"] == "evaluable"
    assert result["cell_pfa"] is None
    assert result["cell_pfa_status"] == "NOT_EVALUABLE"


def test_target_off_waterfall_rejects_ambiguous_multibranch_geometry(tmp_path: Path) -> None:
    from scripts.run_delay_stage1_formal import evaluate_target_off_waterfall

    summary = {
        "status": "passed",
        "hit_cells": None,
        "valid_cut_count": None,
        "aggregate_hit_fraction_status": "NOT_EVALUABLE",
        "aggregate_hit_fraction_reason": (
            "multiple_detector_branches_require_unique_hit_union"
        ),
        "valid_cut_denominator_status": "passed",
        "valid_cut_denominator_row_count": 4,
        "valid_cut_denominator_reason": None,
        "branch_summaries": {
            "union_dynamic_in_band": {
                "status": "evaluable", "period_count": 2, "period_ids": [0, 1]
            },
            "union_full_band": {
                "status": "evaluable", "period_count": 2, "period_ids": [0, 1]
            },
        },
        "expected_period_count": 2,
        "summary_count": 2,
        "summaries": [
            {"hit_cells": 7, "clusters": 2, "selected": 1},
            {"hit_cells": 9, "clusters": 3, "selected": 2},
        ],
    }
    result = evaluate_target_off_waterfall(
        tmp_path,
        None,
        summary,
        1e-6,
    )

    assert result["cell_false_hit_fraction"]["status"] == "NOT_EVALUABLE"
    assert result["cell_false_hit_fraction"]["reason"] == (
        "multiple_detector_branches_require_unique_hit_union"
    )
    assert result.get("valid_cut_denominators") == {
        "status": "passed",
        "row_count": 4,
        "reason": None,
        "definition": (
            "each recorded period/beam/branch has a positive valid CUT denominator; "
            "overlapping branch denominators are not summed"
        ),
    }
    incomplete = evaluate_target_off_waterfall(
        tmp_path,
        None,
        {**summary, "valid_cut_denominator_row_count": 3},
        1e-6,
    )
    assert incomplete["valid_cut_denominators"]["status"] == "NOT_EVALUABLE"
    assert incomplete["unique_final_union_hits"]["status"] == "passed"
    incomplete_union = evaluate_target_off_waterfall(
        tmp_path,
        None,
        {**summary, "summary_count": 1},
        1e-6,
    )
    assert incomplete_union["unique_final_union_hits"]["status"] == "NOT_EVALUABLE"
    assert result.get("unique_final_union_hits") == {
        "status": "passed",
        "expected_period_count": 2,
        "summary_count": 2,
        "hit_cells_by_production_order": [7, 9],
        "total_hit_cells": 16,
        "source": "production_cfar_summary",
        "definition": (
            "positive CUT cells in the merged final CFAR hit map, in production period order; "
            "descriptive counts, not Pfa"
        ),
    }


def test_target_off_waterfall_rejects_missing_union_geometry_branch(tmp_path: Path) -> None:
    from scripts.run_delay_stage1_formal import evaluate_target_off_waterfall

    result = evaluate_target_off_waterfall(
        tmp_path,
        None,
        {
            "status": "passed",
            "valid_cut_denominator_status": "passed",
            "valid_cut_denominator_row_count": 3,
            "valid_cut_denominator_reason": None,
            "expected_period_count": 3,
            "branch_summaries": {
                "union_dynamic_in_band": {
                    "status": "evaluable",
                    "period_count": 3,
                    "period_ids": [0, 1, 2],
                },
            },
            "summary_count": 3,
            "summaries": [
                {"hit_cells": 4},
                {"hit_cells": 5},
                {"hit_cells": 6},
            ],
        },
        1e-6,
    )

    assert result["valid_cut_denominators"]["status"] == "NOT_EVALUABLE"
    assert result["valid_cut_denominators"]["reason"] == "required_geometry_branch_missing"


def test_target_off_waterfall_rejects_noncontiguous_geometry_period_ids(tmp_path: Path) -> None:
    from scripts.run_delay_stage1_formal import evaluate_target_off_waterfall

    branch_summaries = {
        "union_dynamic_in_band": {
            "status": "evaluable",
            "period_count": 3,
            "period_ids": [0, 1, 3],
        },
        "union_full_band": {
            "status": "evaluable",
            "period_count": 3,
            "period_ids": [0, 1, 2],
        },
    }
    result = evaluate_target_off_waterfall(
        tmp_path,
        None,
        {
            "status": "passed",
            "valid_cut_denominator_status": "passed",
            "valid_cut_denominator_row_count": 6,
            "valid_cut_denominator_reason": None,
            "expected_period_count": 3,
            "branch_summaries": branch_summaries,
            "summary_count": 3,
            "summaries": [
                {"hit_cells": 4},
                {"hit_cells": 5},
                {"hit_cells": 6},
            ],
        },
        1e-6,
    )

    assert result["valid_cut_denominators"]["status"] == "NOT_EVALUABLE"
    assert result["valid_cut_denominators"]["reason"] == (
        "valid_cut_denominator_period_coverage_missing_or_incomplete"
    )


def test_scene_variants_preserve_scene_identity_and_pair_backgrounds(tmp_path: Path) -> None:
    import numpy as np

    from scripts.run_delay_stage1_formal import build_scene_variants

    config = {
        "case_id": "template",
        "waveform": {"new_protocol_channel_count": 4, "iq_data_type": "float32"},
        "random": {"random_seed": 999, "beam_start": 1, "period_count": 2},
        "scene": {"mode": "full", "output_signal_domain": "raw_lfm"},
        "targets": [{"target_id": "old", "enabled": True}],
        "channel_impairments": {"enabled": False, "channel_time_delay_ns": 0.0},
        "production": {"gate": "same"},
    }
    scene_identity = {
        "seed": 101,
        "target": {"target_id": "T1", "enabled": True},
        "clutter": {"mean_power": 0.02, "texture_sigma": 0.25},
        "noise": {"noise_power": 0.001},
        "beam_id": 7,
        "range_m": 85000.0,
        "velocity_mps": 6.7,
        "snr_db": 30.0,
        "production": {"gate": "same"},
    }
    paths = build_scene_variants(config, scene_identity, -4.0, tmp_path / "case")
    assert set(paths) == {"A0_ON", "A0_OFF", "A0_TO", "A1_ON", "A1_OFF", "A1_TO"}
    payloads = {role: json.loads(path.read_text(encoding="utf-8")) for role, path in paths.items()}
    for path in paths.values():
        assert path.is_relative_to(tmp_path / "case")
    assert payloads["A0_ON"]["random"]["random_seed"] == 101
    assert payloads["A1_ON"]["random"]["random_seed"] == 101
    assert payloads["A0_ON"]["channel_impairments"]["channel_time_delay_ns"] == 0.0
    assert payloads["A1_ON"]["channel_impairments"]["channel_time_delay_ns"] == -4.0
    # The simulator rejects paired_background_output_dir when a non-zero
    # impairment is enabled.  A0 ON owns the zero-error background write;
    # A1 ON reuses that exact run root through background_input_dir and applies
    # the delay after target injection.
    assert payloads["A0_ON"]["paired_background_output_dir"]
    assert "background_input_dir" not in payloads["A0_ON"]["stage1_pairing"]
    assert payloads["A1_ON"]["background_input_dir"]
    assert "paired_background_output_dir" not in payloads["A1_ON"]
    assert payloads["A1_OFF"]["background_input_dir"] == payloads["A1_TO"]["background_input_dir"]
    assert payloads["A1_TO"]["scene"]["signal_only"] is True
    assert payloads["A1_TO"]["scene"]["output_signal_domain"] == "raw_lfm"
    assert payloads["A1_ON"]["pairing_provenance"]["seed"] == 101
    assert np.isfinite(payloads["A1_ON"]["channel_impairments"]["channel_time_delay_ns"])


def test_prepare_condition_inputs_records_truth_and_blind_provenance(tmp_path: Path, monkeypatch) -> None:
    from scripts.run_delay_stage1_formal import prepare_condition_inputs

    period = tmp_path / "period_0000.bin"
    period.write_bytes(b"raw")
    calls = []

    def fake_rewrite(source, destination, **kwargs):
        calls.append((source, destination, kwargs))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"corrected")
        return {"status": "passed", "channel_indices": list(kwargs["channel_indices"]), "packets_rewritten": 1}

    monkeypatch.setattr("scripts.run_delay_stage1_formal.rewrite_float32_protocol_delay", fake_rewrite)
    layout = {
        "pulse_len": 1,
        "channel_count": 4,
        "fs_hz": 60.0e6,
        "correction_channel_indices": [1],
    }
    raw = prepare_condition_inputs(
        tmp_path,
        "A1_Current_unknown_error",
        [period],
        tmp_path / "missing_calibration.bin",
        delay_truth_ns=4.0,
        delay_estimate_ns=None,
        layout=layout,
    )
    assert raw["raw_paths"] == [period]
    assert raw["corrected_paths"] == []
    assert raw["correction_applied"] is False
    assert raw["truth_used_in_estimator"] is False

    known = prepare_condition_inputs(
        tmp_path,
        "A2_Known_error_correction_upper_bound",
        [period],
        tmp_path / "missing_calibration.bin",
        delay_truth_ns=4.0,
        delay_estimate_ns=None,
        layout=layout,
    )
    assert known["estimate"] == 4.0
    assert known["source"] == "truth_evaluation_only"
    assert known["truth_used_in_estimator"] is False
    assert known["correction_applied"] is True
    assert known["residual_ns"] == 0.0

    blind = prepare_condition_inputs(
        tmp_path,
        "A3_Blind_target_free_estimated_correction",
        [period],
        tmp_path / "missing_calibration.bin",
        delay_truth_ns=4.0,
        delay_estimate_ns=3.5,
        layout=layout,
    )
    assert blind["estimate"] == 3.5
    assert blind["source"] == "A1_OFF_target_free_estimate"
    assert blind["truth_used_in_estimator"] is False
    assert blind["residual_ns"] == 0.5
    assert blind["correction_applied"] is True
    assert all(item["channel_indices"] == [1] for item in blind["correction_audit"])
    assert len(calls) == 2


def test_prepare_a3_rejects_missing_or_nonfinite_estimate(tmp_path: Path) -> None:
    from scripts.run_delay_stage1_formal import prepare_condition_inputs

    layout = {"pulse_len": 1, "channel_count": 4, "fs_hz": 60.0e6, "correction_channel_indices": [1]}
    with pytest.raises(ValueError, match="finite target-free delay estimate"):
        prepare_condition_inputs(
            tmp_path,
            "A3_Blind_target_free_estimated_correction",
            [],
            tmp_path / "unused.bin",
            delay_truth_ns=4.0,
            delay_estimate_ns=None,
            layout=layout,
        )


def test_stage1_config_makes_formal_selection_and_statistics_explicit() -> None:
    config = load_stage1_config()
    formal = config["formal"]
    assert isinstance(formal, dict)
    assert formal["selection_mode"] == "case_groups_only"
    assert formal["array_semantics"] == "supported_values_not_cartesian"
    assert formal["delay_errors_ns"] == [0, 1, -1, 4, -4, 8, -8]
    assert formal["case_groups"] == EXPECTED_FORMAL_CASE_GROUPS
    assert formal["base_combination_count"] == 12

    monte_carlo = config["monte_carlo"]
    assert isinstance(monte_carlo, dict)
    assert monte_carlo["trials"] == 100

    cfar = config["cfar"]
    assert isinstance(cfar, dict)
    theoretical = cfar["theoretical"]
    assert isinstance(theoretical, dict)
    assert theoretical["pfa"] == 1e-6

    statistics = config["statistics"]
    assert isinstance(statistics, dict)
    assert statistics["bootstrap_repetitions"] == 2000


def test_cli_parser_exposes_required_stage1_arguments() -> None:
    from scripts.run_delay_stage1_formal import build_arg_parser

    parser = build_arg_parser()
    options = set(parser._option_string_actions)
    assert {
        "--mode",
        "--delay-errors-ns",
        "--seeds",
        "--target-velocities-mps",
        "--snr-db",
        "--working-point",
        "--mc-trials",
        "--output-root",
        "--input-mode",
        "--period-count",
        "--skip-cuda",
        "--cleanup-raw",
        "--resume",
        "--max-cases",
    } <= options


def test_cleanup_stage1_raw_removes_only_case_binaries_and_keeps_audit(tmp_path: Path) -> None:
    from scripts.run_delay_stage1_formal import cleanup_stage1_raw

    case_root = tmp_path / "case"
    (case_root / "scenes" / "data").mkdir(parents=True)
    (case_root / "production").mkdir()
    (case_root / "case_manifest.json").write_text(
        json.dumps({"status": "completed", "scene_identity": {"case_id": "test"}}),
        encoding="utf-8",
    )
    raw = case_root / "scenes" / "data" / "period_0000.bin"
    raw.write_bytes(b"raw")
    (case_root / "audit.csv").write_text("ok\n", encoding="utf-8")
    external = tmp_path / "external.bin"
    external.write_bytes(b"keep")
    link = case_root / "production" / "external.bin"
    link.symlink_to(external)

    report = cleanup_stage1_raw(case_root)

    assert report["status"] == "completed"
    assert report["removed_file_count"] == 1
    assert report["remaining_bin_count"] == 1
    assert not raw.exists()
    assert link.is_symlink()
    assert (case_root / "audit.csv").is_file()
    assert (case_root / "raw_cleanup.json").is_file()


def test_cleanup_stage1_raw_accepts_completed_production_comparison_status(tmp_path: Path) -> None:
    from scripts.run_delay_stage1_formal import cleanup_stage1_raw

    case_root = tmp_path / "case"
    (case_root / "data").mkdir(parents=True)
    (case_root / "case_manifest.json").write_text(
        json.dumps({"status": "production_comparisons_executed"}),
        encoding="utf-8",
    )
    raw = case_root / "data" / "derived.bin"
    raw.write_bytes(b"reproducible")
    (case_root / "runtime.xml").write_text("<root/>\n", encoding="utf-8")

    report = cleanup_stage1_raw(case_root)

    assert report["status"] == "production_comparisons_executed"
    assert report["removed_file_count"] == 1
    assert not raw.exists()
    assert (case_root / "runtime.xml").is_file()


def test_cleanup_stage1_raw_can_reclaim_binaries_after_preserving_production_failure(tmp_path: Path) -> None:
    from scripts.run_delay_stage1_formal import cleanup_stage1_raw

    case_root = tmp_path / "failed_case"
    (case_root / "data").mkdir(parents=True)
    (case_root / "case_manifest.json").write_text(
        json.dumps({"status": "production_path_failed"}),
        encoding="utf-8",
    )
    raw = case_root / "data" / "reproducible_input.bin"
    raw.write_bytes(b"reproducible")
    (case_root / "failure.log").write_text("no CUDA-capable device is detected\n", encoding="utf-8")

    report = cleanup_stage1_raw(case_root, allow_incomplete=True)

    assert report["status"] == "production_path_failed"
    assert report["removed_file_count"] == 1
    assert not raw.exists()
    assert (case_root / "failure.log").is_file()


def test_formal_selection_uses_registered_groups_without_cross_group_cartesian_product() -> None:
    from scripts.run_delay_stage1_formal import build_arg_parser, load_stage1_config, resolve_stage1_selection

    parser = build_arg_parser()
    args = parser.parse_args(["--mode", "formal", "--output-root", "/tmp/unused-stage1-selection"])
    selection = resolve_stage1_selection(load_stage1_config(), args)
    assert len(selection["cases"]) == 15
    base_cases = [case for case in selection["cases"] if case["working_point"] == "base"]
    assert len(base_cases) == 12
    assert selection["level1_mc_delay_errors_ns"] == [0.0, 1.0, -1.0, 2.0, -2.0, 4.0, -4.0, 8.0, -8.0]


def test_registered_formal_override_keeps_group_scoped_case_count() -> None:
    from scripts.run_delay_stage1_formal import build_arg_parser, load_stage1_config, resolve_stage1_selection

    parser = build_arg_parser()
    args = parser.parse_args([
        "--mode", "formal",
        "--output-root", "/tmp/unused-stage1-selection",
        "--working-point", "registered",
        "--seeds", "101,202,303",
        "--target-velocities-mps", "6.7,12.0",
        "--snr-db", "20,30,35",
    ])
    selection = resolve_stage1_selection(load_stage1_config(), args)
    assert len(selection["cases"]) == 15


def test_bounded_run_is_not_marked_completed_before_expected_matrix(tmp_path: Path) -> None:
    from scripts.run_delay_stage1_formal import build_arg_parser, run_stage1_cli

    args = build_arg_parser().parse_args(
        [
            "--mode", "formal",
            "--output-root", str(tmp_path / "bounded"),
            "--input-mode", "local",
            "--working-point", "registered",
            "--delay-errors-ns", "0",
            "--mc-trials", "100",
            "--skip-cuda",
            "--max-cases", "1",
        ]
    )
    manifest = run_stage1_cli(args, command_args=[])
    assert manifest["status"] == "completed_with_gaps"
    assert manifest["progress"]["processed_this_invocation"] == 1
    assert manifest["progress"]["expected_case_count"] == 15


def test_cli_rejects_nonempty_output_root(tmp_path: Path) -> None:
    output_root = tmp_path / "nonempty"
    output_root.mkdir()
    (output_root / "existing.txt").write_text("keep", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_delay_stage1_formal.py"),
            "--mode",
            "pilot",
            "--output-root",
            str(output_root),
            "--skip-cuda",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "refuse to overwrite non-empty output root" in result.stderr


def test_compact_evidence_has_exact_required_files(tmp_path: Path) -> None:
    from scripts.analyze_delay_stage1_formal import build_compact_evidence

    run_root = tmp_path / "run"
    run_root.mkdir()
    manifest = run_root / "manifest.json"
    manifest.write_text(
        json.dumps({
            "schema_version": 1,
            "status": "completed_with_gaps",
            "mode": "pilot",
            "command": {"shell": "pilot"},
            "config": {},
            "template": {},
            "resolved": {"e2e_delay_errors_ns": [0.0]},
            "cases": [],
            "mc_summary": {"rows": []},
            "ai_training": False,
            "router_enabled": False,
        }),
        encoding="utf-8",
    )
    outputs = build_compact_evidence(manifest, tmp_path / "evidence")
    expected = {
        "manifest.json",
        "delay_estimation_summary.csv",
        "delay_baseline_comparison.csv",
        "A0_A1_A2_A3_summary.csv",
        "target_off_false_alarm_summary.csv",
        "target_on_detection_summary.csv",
        "track_summary.csv",
        "id_switch_audit.csv",
        "statistics_summary.csv",
    }
    assert set(outputs) == expected
    assert {path.name for path in (tmp_path / "evidence").iterdir()} == expected


def test_summary_preserves_not_evaluable_zero_denominator() -> None:
    from scripts.analyze_delay_stage1_formal import compute_condition_summary

    rows = compute_condition_summary(
        [{"condition": "A1", "metric": "cfar_pfa", "value": None, "status": "NOT_EVALUABLE"}],
        ["A0", "A1", "A2", "A3"],
    )
    assert rows[0]["status"] == "NOT_EVALUABLE"
    assert rows[0]["value"] is None


def test_stage1_report_and_paper_outline_are_navigable() -> None:
    report = ROOT / "docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md"
    paper = ROOT / "docs/papers/Paper1_ChannelDelay_SelfCalibration_Outline.md"
    readme = ROOT / "README.md"
    assert report.is_file()
    assert paper.is_file()
    assert "A0/A1/A2/A3" in report.read_text(encoding="utf-8")
    assert "NOT_EVALUABLE" in report.read_text(encoding="utf-8")
    assert "potential contributions" in paper.read_text(encoding="utf-8")
    readme_text = readme.read_text(encoding="utf-8")
    assert "AI_CSI_36_单一系统误差确定性自校准阶段报告.md" in readme_text
    assert "Paper1_ChannelDelay_SelfCalibration_Outline.md" in readme_text
