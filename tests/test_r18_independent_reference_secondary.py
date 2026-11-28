from __future__ import annotations

import copy
import csv
import importlib.util


def test_independent_reference_scenario_builder_is_available() -> None:
    spec = importlib.util.find_spec("scripts.run_r18_independent_reference_secondary")
    assert spec is not None, "independent-reference runner must provide a scenario builder"


def test_reference_scenario_is_target_free_and_does_not_reuse_paired_inputs(tmp_path) -> None:
    from scripts.run_r18_independent_reference_secondary import (
        build_target_free_reference_scenario,
    )

    source = {
        "output_dir": "/old/scene",
        "scene_mode": "full",
        "truth_output": True,
        "random": {"random_seed": 606, "period_count": 3},
        "scene": {
            "mode": "full",
            "area_clutter": {
                "enabled": True,
                "model": "continuous_texture",
                "mean_power": 0.02,
                "texture_sigma": 0.25,
                "temporal_correlation_rho": 0.98,
            },
            "thermal_noise": {"enabled": True, "noise_power": 0.001},
            "strong_scatterers": {"enabled": True, "count": 2},
            "line_scatterers": {"enabled": True, "line_count": 1},
        },
        "targets": [{"target_id": "target", "enabled": True}],
        "background_input_dir": "/paired/background",
        "paired_background_output_dir": "/paired/output",
        "stage1_scene_identity": {"seed": 606},
        "stage1_role": "off",
        "stage1_pairing": {"background_source_dir": "/paired/background"},
        "pairing_provenance": {"background_source": "/paired/background"},
    }
    original = copy.deepcopy(source)

    result = build_target_free_reference_scenario(
        source,
        output_dir=tmp_path / "reference",
        seed=909,
    )

    assert source == original
    assert result["random"]["random_seed"] == 909
    assert result["scene_mode"] == result["scene"]["mode"] == "area_clutter_only"
    assert result["truth_output"] is False
    assert result["output_dir"] == str(tmp_path / "reference")
    assert result["scene"]["area_clutter"] == source["scene"]["area_clutter"]
    assert result["scene"]["thermal_noise"] == source["scene"]["thermal_noise"]
    assert result["scene"]["strong_scatterers"]["enabled"] is False
    assert result["scene"]["line_scatterers"]["enabled"] is False
    assert all(target["enabled"] is False for target in result["targets"])
    assert not any(key.startswith("stage1_") for key in result)
    assert "pairing_provenance" not in result
    assert "background_input_dir" not in result
    assert "paired_background_output_dir" not in result


def test_reference_scenario_rejects_missing_continuous_area_clutter(tmp_path) -> None:
    from scripts.run_r18_independent_reference_secondary import (
        build_target_free_reference_scenario,
    )

    source = {
        "random": {"random_seed": 606, "period_count": 3},
        "scene": {"area_clutter": {"enabled": False, "model": "none"}},
        "targets": [],
    }

    try:
        build_target_free_reference_scenario(
            source,
            output_dir=tmp_path / "reference",
            seed=909,
        )
    except ValueError as exc:
        assert "continuous area clutter" in str(exc)
    else:
        raise AssertionError("reference scenario without area clutter must be rejected")


def test_area_statistics_use_frozen_tolerances() -> None:
    from scripts.run_r18_independent_reference_secondary import compare_area_statistics

    primary = {
        "area_mean_power_measured": 0.0227,
        "report_values": {
            "area_power_cv_measured": "2.0",
            "area_rho_lag1_measured": "0.98",
            "area_rho_at_CTDR_lag": "0.96",
            "mean_noise_power_per_complex_sample": "0.001",
        },
        "continuous_area_packets": 390,
        "continuous_area_samples": 1_952_655,
    }
    reference = {
        "area_mean_power_measured": 0.0234,
        "report_values": {
            "area_power_cv_measured": "2.1",
            "area_rho_lag1_measured": "0.985",
            "area_rho_at_CTDR_lag": "0.965",
            "mean_noise_power_per_complex_sample": "0.00102",
        },
        "continuous_area_packets": 390,
        "continuous_area_samples": 1_952_655,
    }
    assert compare_area_statistics(primary, reference)["status"] == "passed"

    reference["area_mean_power_measured"] = 0.03
    assert compare_area_statistics(primary, reference)["status"] == "NOT_EVALUABLE"


def test_fixed_reference_application_requires_same_gamma_groups_and_values(tmp_path) -> None:
    from scripts.run_r18_independent_reference_secondary import _gamma_application_check

    reference_path = tmp_path / "reference_gamma.csv"
    with reference_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("period_id", "beam_id", "group_id", "status", "gamma_real", "gamma_imag", "truth_used_in_estimator"),
        )
        writer.writeheader()
        writer.writerow({
            "period_id": 1,
            "beam_id": 50,
            "group_id": "az_-1_range_0_9",
            "status": "OK",
            "gamma_real": 0.97,
            "gamma_imag": -0.01,
            "truth_used_in_estimator": "false",
        })

    applied = [{
        "period_id": "1",
        "beam_id": "50",
        "group_id": "az_-1_range_0_9",
        "status": "OK",
        "gamma_real": "0.97",
        "gamma_imag": "-0.01",
        "truth_used_in_estimator": "false",
    }]
    assert _gamma_application_check(reference_path, applied)["status"] == "passed"
    applied[0]["gamma_real"] = "0.9"
    assert _gamma_application_check(reference_path, applied)["status"] == "NOT_EVALUABLE"


def test_primary_area_gate_resolves_the_recorded_injection_owner(tmp_path) -> None:
    from scripts.run_r18_independent_reference_secondary import (
        _sha256,
        load_primary_area_evidence,
    )

    report = tmp_path / "A0_ON" / "stage2_simulation_report.md"
    scenario = tmp_path / "A0_ON.json"
    report.parent.mkdir(parents=True)
    report.write_text("area owner report", encoding="utf-8")
    scenario.write_text("{}\n", encoding="utf-8")
    gate = {
        "status": "passed",
        "report_path": str(report),
        "report_sha256": _sha256(report),
        "scenario_path": str(scenario),
        "scenario_sha256": _sha256(scenario),
    }
    case = {
        "area_clutter_gate": gate,
        "simulation": {"A0_OFF": {"output_dir": str(tmp_path / "A0_OFF")}},
    }

    _, selected_report, selected_scenario = load_primary_area_evidence(case)

    assert selected_report == report.resolve()
    assert selected_scenario == {}
