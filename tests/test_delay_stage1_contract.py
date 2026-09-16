"""Contract tests for the channel-delay Stage-1 configuration."""

from __future__ import annotations

import json
from pathlib import Path


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
