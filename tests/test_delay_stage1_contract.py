"""Contract tests for the channel-delay Stage-1 configuration."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/research/channel_delay_stage1_formal.json"


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


def test_stage1_config_has_paired_control_and_registered_working_points() -> None:
    config = load_stage1_config()
    assert config["paired_control"] == {"formula": "ON-OFF-TO", "dtype": "float32"}
    points = config["working_points"]
    assert isinstance(points, list)
    names = {point["name"] for point in points if isinstance(point, dict)}
    assert {"base", "low_snr", "high_velocity", "alternate_texture_geometry"} <= names
    formal = config["formal"]
    assert isinstance(formal, dict)
    assert set(formal["seeds"]) == {101, 202, 303}
    assert set(formal["target_velocities_mps"]) == {6.7, 12.0}
    assert min(formal["target_snr_db"]) < 30.0
