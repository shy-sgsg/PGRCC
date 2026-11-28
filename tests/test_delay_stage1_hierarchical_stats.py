"""Tests for scene-block hierarchical delay statistics."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.analyze_delay_stage1_hierarchical import (
    binary_cluster_paired,
    block_bootstrap_effect,
    make_physical_block_id,
    paired_delay_effects,
    write_hierarchical_evidence,
)


def _row(*, scene: str, delay: float, metric: str = "position_rmse_m", a1: float = 10.0,
         a2: float = 8.0, a3: float = 9.0) -> dict[str, object]:
    return {
        "scene_id": f"{scene}__delay_{'p' if delay >= 0 else 'm'}{abs(delay):g}ns",
        "working_point": "base",
        "seed": 101,
        "target_velocity_mps": 6.7,
        "target_snr_db": 30.0,
        "texture_sigma": 0.25,
        "clutter_rho": 0.98,
        "delay_error_ns": delay,
        "metric": metric,
        "metric_direction": "lower_is_better",
        "A1_value": a1,
        "A1_status": "passed",
        "A2_value": a2,
        "A2_status": "passed",
        "A3_value": a3,
        "A3_status": "passed",
    }


def test_physical_block_id_ignores_delay_but_keeps_scene_identity() -> None:
    first = make_physical_block_id(_row(scene="scene-1", delay=1))
    second = make_physical_block_id(_row(scene="scene-1", delay=-8))
    other = make_physical_block_id(_row(scene="scene-2", delay=1))
    assert first == second
    assert first != other
    assert "delay" not in first.lower()


def test_paired_effects_direction_recovery_and_zero_recoverable_space() -> None:
    rows = [
        _row(scene="scene-1", delay=1),
        _row(scene="scene-1", delay=-1, a1=10.0, a2=10.0, a3=9.0),
    ]
    effects = paired_delay_effects(
        rows,
        comparisons=("A1_to_A2", "A1_to_A3"),
        delays_ns=(1, -1),
    )
    plus_a2 = next(row for row in effects if row["delay_error_ns"] == 1 and row["comparison"] == "A1_to_A2")
    plus_a3 = next(row for row in effects if row["delay_error_ns"] == 1 and row["comparison"] == "A1_to_A3")
    zero = next(row for row in effects if row["delay_error_ns"] == -1 and row["comparison"] == "A1_to_A2")
    assert plus_a2["effect"] == 2.0
    assert plus_a3["effect"] == 1.0
    assert plus_a2["recovery_ratio"] == 0.5
    assert zero["status"] == "NOT_EVALUABLE"
    assert zero["reason"] == "zero_recoverable_space"


def test_paired_effects_reports_duplicate_and_missing_block_delay_pairs() -> None:
    rows = [
        _row(scene="scene-1", delay=1),
        _row(scene="scene-1", delay=1, a2=7.0),
        _row(scene="scene-2", delay=-1),
    ]
    effects = paired_delay_effects(rows, comparisons=("A1_to_A2",), delays_ns=(1, -1))
    plus = next(row for row in effects if row["delay_error_ns"] == 1)
    minus = next(row for row in effects if row["delay_error_ns"] == -1)
    assert plus["duplicate_block_delay_count"] == 1
    assert minus["missing_block_count"] == 1
    assert plus["status"] == "NOT_EVALUABLE"
    assert "duplicate" in plus["reason"]


def test_block_bootstrap_resamples_physical_blocks() -> None:
    rows = [
        {"block": "b1", "delay": 1, "effect": 1.0},
        {"block": "b2", "delay": 1, "effect": 3.0},
        {"block": "b3", "delay": 1, "effect": 5.0},
    ]
    result = block_bootstrap_effect(
        rows,
        block_key="block",
        treatment_key="delay",
        value_key="effect",
        comparison="A1_to_A2",
        trials=200,
        seed=7,
    )
    assert result["status"] == "passed"
    assert result["block_count"] == 3
    assert result["effective_block_count"] == 3
    assert result["effect"] == 3.0
    assert result["ci_low"] <= result["effect"] <= result["ci_high"]


def test_binary_cluster_paired_uses_block_pairs() -> None:
    rows = [
        {"block": "b1", "delay": 1, "current": 0, "comparison": 1},
        {"block": "b2", "delay": 1, "current": 1, "comparison": 0},
        {"block": "b3", "delay": 1, "current": 1, "comparison": 1},
    ]
    result = binary_cluster_paired(
        rows,
        block_key="block",
        treatment_key="delay",
        current_key="current",
        comparison_key="comparison",
    )
    assert result["status"] == "passed"
    assert result["block_count"] == 3
    assert result["discordant_current_miss_comparison_hit"] == 1
    assert result["discordant_current_hit_comparison_miss"] == 1
    assert result["effect"] == 0.0


def test_write_hierarchical_evidence_records_v1_exploratory_provenance(tmp_path: Path) -> None:
    input_path = tmp_path / "A0_A1_A2_A3_summary.csv"
    rows = [_row(scene="scene-1", delay=1)]
    with input_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = write_hierarchical_evidence([input_path], tmp_path / "out", seed=7, trials=20)
    assert summary["status"] == "completed"
    assert summary["source_classification"] == "Formal-v1 exploratory"
    assert (tmp_path / "out" / "hierarchical_manifest.json").is_file()
    assert (tmp_path / "out" / "hierarchical_effects.csv").is_file()


def test_write_hierarchical_evidence_inherits_v2_provenance(tmp_path: Path) -> None:
    input_dir = tmp_path / "compact"
    input_dir.mkdir()
    input_path = input_dir / "A0_A1_A2_A3_summary.csv"
    rows = [_row(scene="scene-1", delay=1)]
    with input_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (input_dir / "manifest.json").write_text(
        json.dumps({"evidence_version": "formal-v2"}) + "\n",
        encoding="utf-8",
    )

    summary = write_hierarchical_evidence([input_dir], tmp_path / "out", seed=7, trials=20)

    assert summary["source_classification"] == "Formal-v2 confirmatory"
    effect_rows = list(csv.DictReader((tmp_path / "out" / "hierarchical_effects.csv").open()))
    assert effect_rows
    assert {row["source_classification"] for row in effect_rows} == {"Formal-v2 confirmatory"}
