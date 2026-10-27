import csv
import json
from pathlib import Path

import pytest

from scripts.phase_r.run_r21_cuda_pilot import (
    _build_bundles,
    _area_clutter_audit,
    _has_production_execution,
    _input_closure_rows,
    qualify_blind_methods,
)


def _estimate(**overrides):
    value = {
        "status": "ok",
        "method": "M1_Physics_Matched_Delay",
        "delta_tau_ns": 3.75,
        "support_count": 2364,
        "frequency_span_hz": 50.0e6,
        "input_role": "A1_Current_unknown_error_ON_raw_C1_C2_only",
        "truth_used_in_estimator": False,
        "baselines": {
            "D3_Huber_weighted_LS": {"delta_tau_ns": 3.58, "support_count": 2364},
        },
    }
    value.update(overrides)
    return value


def test_blind_method_gate_accepts_only_declared_b_on_raw_inputs():
    result = qualify_blind_methods(_estimate())
    assert result["input_role"] == "A1_Current_unknown_error_ON_raw_C1_C2_only"
    assert result["truth_used_in_estimator"] is False
    assert result["d3_huber"] == {
        "status": "AVAILABLE", "correction_ns": 3.58,
        "source_record": {"delta_tau_ns": 3.58, "support_count": 2364},
        "support_qualified": True,
    }
    assert result["m1"]["correction_ns"] == 3.75


def test_blind_method_gate_rejects_truth_or_other_role_inputs():
    with pytest.raises(ValueError, match="input role"):
        qualify_blind_methods(_estimate(input_role="A0_ON"))
    with pytest.raises(ValueError, match="truth use"):
        qualify_blind_methods(_estimate(truth_used_in_estimator=True))


def test_missing_huber_estimate_is_explicitly_not_available():
    result = qualify_blind_methods(_estimate(baselines={}))
    assert result["d3_huber"] == {
        "status": "NOT_AVAILABLE", "correction_ns": None,
        "source_record": None, "support_qualified": False,
    }
    assert result["m1"]["status"] == "AVAILABLE"


def test_non_finite_blind_estimates_are_not_qualified():
    result = qualify_blind_methods(_estimate(
        status="insufficient_support", delta_tau_ns=float("nan"),
        baselines={"D3_Huber_weighted_LS": {"delta_tau_ns": float("inf")}},
    ))
    assert result["m1"]["status"] == "NOT_AVAILABLE"
    assert result["d3_huber"]["status"] == "NOT_AVAILABLE"


def test_finite_huber_estimate_without_frozen_support_is_not_available():
    result = qualify_blind_methods(_estimate(
        status="insufficient_support", support_count=7, frequency_span_hz=1.0e6,
        baselines={"D3_Huber_weighted_LS": {"delta_tau_ns": 3.58, "support_count": 7}},
    ))
    assert result["d3_huber"]["status"] == "NOT_AVAILABLE"
    assert result["m1"]["status"] == "NOT_AVAILABLE"


def test_huber_support_must_match_the_estimator_input_support():
    result = qualify_blind_methods(_estimate(
        baselines={"D3_Huber_weighted_LS": {"delta_tau_ns": 3.58, "support_count": 2000}},
    ))
    assert result["d3_huber"]["status"] == "NOT_AVAILABLE"


def test_executed_requires_a_production_return_record_not_stage2_simulation():
    assert not _has_production_execution({"simulation": {"A0_ON": {"status": "completed"}}})
    assert _has_production_execution({
        "simulation": {"A0_ON": {"status": "completed"}},
        "conditions": {"B": {"production": {"ON": {"production_returncode": 1}}}},
    })


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _area_case(tmp_path: Path):
    background = tmp_path / "scenes" / "A0_background"
    data_dir = background / "data"
    data_dir.mkdir(parents=True)
    manifest_path = data_dir / "period_files.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("period_id", "file", "packet_count", "packet_bytes", "file_bytes"),
        )
        writer.writeheader()
        for period in range(3):
            data = data_dir / f"period_{period:04d}.bin"
            data.write_bytes(b"background-packets")
            writer.writerow({
                "period_id": period,
                "file": data,
                "packet_count": 1,
                "packet_bytes": data.stat().st_size,
                "file_bytes": data.stat().st_size,
            })

    simulation = {}
    roles = {
        "A0_ON": "on", "A0_OFF": "off", "A0_TO": "target_only",
        "A1_ON": "on", "A1_OFF": "off", "A1_TO": "target_only",
    }
    for name, role in roles.items():
        is_source = name == "A0_ON"
        output = tmp_path / "scenes" / name
        (output / "reports").mkdir(parents=True)
        (output / "logs").mkdir()
        scenario_path = tmp_path / "scenarios" / f"{name}.json"
        scenario = {
            "scene": {
                "area_clutter": {
                    "enabled": True,
                    "model": "continuous_texture",
                    "mean_power": 0.1,
                    "texture_sigma": 0.25,
                    "temporal_correlation_rho": 0.98,
                },
                "ground_z_m": 0,
                "range_min_m": 84500,
                "range_max_m": 85500,
                "clutter_amplitude_scale": 1,
                "signal_only": role == "target_only",
            },
            "platform": {"origin_alt_m": 6000},
            "random": {"random_seed": 1001, "period_count": 3},
            "stage1_role": role,
            "stage1_pairing": {"background_source_dir": str(background)},
            "pairing_provenance": {"background_source": str(background)},
            "paired_background_output_dir": str(background) if is_source else "",
            "background_input_dir": "" if is_source else str(background),
        }
        _write_json(scenario_path, scenario)

        area_values = (
            "- platform_height_m: 6000\n"
            f"- continuous_area_packets: {390 if is_source else 0}\n"
            f"- continuous_area_samples: {1952655 if is_source else 0}\n"
            f"- area_mean_power_measured: {0.111666783 if is_source else 'null'}\n"
        )
        (output / "reports" / "stage2_simulation_report.md").write_text(
            area_values, encoding="utf-8"
        )
        if is_source:
            runtime_log = (
                "period_count=3\n"
                f"paired_background_output_dir={background}\n"
                "background_input_dir=\nbackground_input_scale=1\n"
            )
        else:
            mapping = output / "reports" / "background_reuse_mapping.csv"
            with mapping.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("period_id", "mapping_status"))
                writer.writeheader()
                writer.writerows({"period_id": period, "mapping_status": "matched"}
                                 for period in range(3))
            runtime_log = (
                "period_count=3\n"
                "paired_background_output_dir=\n"
                f"background_input_dir={background}\nbackground_input_scale=1\n"
            )
        (output / "logs" / "simulate_stage2.log").write_text(runtime_log, encoding="utf-8")
        simulation[name] = {
            "scenario_path": str(scenario_path),
            "output_dir": str(output),
            "returncode": 0,
        }
    return {
        "simulation": simulation,
        "paired_background": {"directory": str(background)},
    }


def test_area_clutter_gate_accepts_generated_source_and_verified_reuse(tmp_path):
    result = _area_clutter_audit(_area_case(tmp_path))

    assert result["status"] == "passed"
    assert result["generated_source_scene"] == "A0_ON"
    assert result["continuous_area_packets"] == 390
    assert result["continuous_area_samples"] == 1952655
    assert result["all_reuse_mappings_matched"] is True
    assert len(result["scenes"]) == 6


def test_area_clutter_gate_rejects_reuse_from_a_different_background(tmp_path):
    case = _area_case(tmp_path)
    scenario_path = Path(case["simulation"]["A1_OFF"]["scenario_path"])
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    scenario["background_input_dir"] = str(tmp_path / "other_background")
    _write_json(scenario_path, scenario)

    result = _area_clutter_audit(case)

    assert result["status"] == "NOT_EVALUABLE"
    row = next(row for row in result["scenes"] if row["scene"] == "A1_OFF")
    assert "background_source_mismatch" in row["reasons"]


def test_area_clutter_gate_rejects_unmatched_reuse_mapping(tmp_path):
    case = _area_case(tmp_path)
    mapping = Path(case["simulation"]["A1_ON"]["output_dir"]) / "reports/background_reuse_mapping.csv"
    mapping.write_text(
        "period_id,mapping_status\n0,matched\n1,failed\n2,matched\n",
        encoding="utf-8",
    )

    result = _area_clutter_audit(case)

    assert result["status"] == "NOT_EVALUABLE"
    assert result["all_reuse_mappings_matched"] is False
    row = next(row for row in result["scenes"] if row["scene"] == "A1_ON")
    assert "background_reuse_mapping_not_matched" in row["reasons"]


def test_area_clutter_gate_still_requires_positive_source_injection(tmp_path):
    case = _area_case(tmp_path)
    report = Path(case["simulation"]["A0_ON"]["output_dir"]) / "reports/stage2_simulation_report.md"
    report.write_text(
        "- platform_height_m: 6000\n"
        "- continuous_area_packets: 0\n"
        "- continuous_area_samples: 0\n"
        "- area_mean_power_measured: 0\n",
        encoding="utf-8",
    )

    result = _area_clutter_audit(case)

    assert result["status"] == "NOT_EVALUABLE"
    assert result["source_physical_geometry_passed"] is False
    source = next(row for row in result["scenes"] if row["scene"] == "A0_ON")
    assert "continuous_area_packets_not_positive" in source["reasons"]


def test_bundle_builder_supplies_case_manifest_to_historical_adapter(tmp_path, monkeypatch):
    from scripts.phase_r import run_r21_cuda_pilot as r21_runner

    case = {
        "scene_identity": {"case_id": "seed1001"},
        "production_binary": {"sha256": "binary-sha", "source_commit": "source-commit"},
        "git": {"commit": "case-commit"},
        "conditions": {
            "A0_Ideal": {"production": {role: {} for role in ("ON", "OFF", "TO")}}
        },
    }

    def adapter_contract(bundle_case, branch_dir, condition, role):
        return {
            "identity": {"case_commit": bundle_case["case_manifest"]["git"]["commit"]}
        }

    monkeypatch.setattr(r21_runner.r20, "_condition_role_bundle", adapter_contract)

    bundles = _build_bundles(tmp_path, case)

    assert len(bundles) == 3
    assert {bundle["identity"]["case_commit"] for bundle in bundles} == {"case-commit"}


def test_estimated_correction_is_reported_as_distance_not_failed_known_closure(monkeypatch):
    from scripts.phase_r import run_r21_cuda_pilot as r21_runner

    roles = {role: {"period_paths": [f"{role}.bin"]} for role in ("ON", "OFF", "TO")}
    case = {
        "protocol_layout": {},
        "conditions": {
            "A0_Ideal": {"input_preparation": roles},
            "A2_Known_error_correction_upper_bound": {"input_preparation": roles},
            "R21_D3_R9_Fixed": {"input_preparation": roles},
        },
    }
    monkeypatch.setattr(
        r21_runner.stage1,
        "audit_fused_input_closure",
        lambda left, right, layout: {"status": "failed", "relative_rms_error": 0.02},
    )

    rows = _input_closure_rows(case)
    known = next(row for row in rows if row["condition"] == "A2_Known_error_correction_upper_bound")
    estimated = next(row for row in rows if row["condition"] == "R21_D3_R9_Fixed")

    assert known["status"] == "failed"
    assert estimated["status"] == "DISTANCE_RECORDED"
    assert estimated["exact_closure_threshold_status"] == "failed"
    assert "exact closure is not required" in estimated["definition"]
