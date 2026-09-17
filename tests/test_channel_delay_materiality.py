"""Schema contract for pre-registered channel-delay materiality."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs/research/channel_delay_stage1_materiality.json"
ALLOWED_SOURCES = {
    "system_requirement",
    "engineering_judgment",
    "historical_production_variability",
    "literature-derived",
    "not_available",
}
REQUIRED_METRICS = {
    "delay_rmse_ns",
    "target_detection_pd",
    "track_pd",
    "position_rmse_m",
    "angle_rmse_deg",
    "velocity_rmse_mps",
    "false_clusters",
    "false_detections",
    "false_tracks",
    "id_switches",
}


def test_materiality_config_preregisters_all_requested_metrics() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert config["ai_training"] is False
    assert config["router_enabled"] is False
    assert config["native_four_channel_stap"] is False
    metrics = {row["name"]: row for row in config["metrics"]}
    assert set(metrics) == REQUIRED_METRICS
    for row in metrics.values():
        assert {"unit", "direction", "threshold", "scope", "source_classification", "status"} <= set(row)
        assert row["direction"] in {"higher_is_better", "lower_is_better"}
        assert row["source_classification"] in ALLOWED_SOURCES
        if row["threshold"] is None:
            assert row["exploratory_pending"] is True
            assert row["status"] == "exploratory_pending"


def test_materiality_config_declares_recovery_and_not_evaluable_rules() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    rules = config["evaluation_rules"]
    assert rules["zero_recoverable_space_status"] == "NOT_EVALUABLE"
    assert rules["missing_valid_cut_denominator_status"] == "NOT_EVALUABLE"
    assert rules["recovery_space_formula"] == "A2_minus_A1_direction_normalized"
    assert rules["actual_recovered_formula"] == "A3_minus_A1_direction_normalized"
    assert rules["recovery_ratio_formula"] == "actual_recovered_over_recoverable_space"
    assert config["final_pass_requires_non_pending_thresholds"] is True
