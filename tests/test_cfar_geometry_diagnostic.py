"""Contracts for the production GO-CFAR geometry diagnostic schema."""

from __future__ import annotations

import pytest

from scripts.cfar_geometry_audit import (
    REQUIRED_GEOMETRY_FIELDS,
    aggregate_geometry_rows,
    validate_geometry_row,
)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": 1,
        "period_id": 7,
        "beam_id": 3,
        "branch": "dynamic_in_band",
        "total_cells": 80,
        "edge_invalid_cells": 56,
        "excluded_cells": 0,
        "cut_band_filtered_cells": 0,
        "valid_cut_count": 24,
        "threshold_test_count": 24,
        "hit_cut_count": 2,
        "hit_index_hash": "fnv1a64:1234",
        "configured_pfa": 1.0e-3,
        "cfar_type": "GO",
        "alpha": 12.5,
        "guard_cells": 1,
        "background_cells": 1,
        "doppler_circular": False,
        "exclude_row_start": -1,
        "exclude_row_end": -1,
        "cut_band_start": 0,
        "cut_band_end": 7,
        "cut_band_mode": 0,
        "hit_rate": 2.0 / 24.0,
        "hit_rate_status": "evaluable",
        "false_cut_hit_count": 2,
        "false_cluster_count": 1,
        "protocol_detection_row_count": 1,
        "false_track_count": 0,
    }
    row.update(overrides)
    return row


def test_required_schema_keeps_geometry_and_downstream_layers_separate() -> None:
    row = _row()
    assert validate_geometry_row(row) == row
    assert REQUIRED_GEOMETRY_FIELDS <= set(row)
    aggregate = aggregate_geometry_rows([row])
    assert aggregate[0]["hit_rate"] == pytest.approx(2.0 / 24.0)
    assert aggregate[0]["false_cut_hit_count"] == 2
    assert aggregate[0]["false_cluster_count"] == 1
    assert aggregate[0]["protocol_detection_row_count"] == 1
    assert aggregate[0]["false_track_count"] == 0


def test_branch_identity_is_not_collapsed() -> None:
    rows = [
        _row(branch="dynamic_in_band"),
        _row(branch="full_band"),
        _row(branch="union_deduplicated"),
        _row(branch="split_in_band"),
        _row(branch="split_out_of_band"),
    ]
    aggregate = aggregate_geometry_rows(rows)
    assert [item["branch"] for item in aggregate] == [
        "dynamic_in_band",
        "full_band",
        "union_deduplicated",
        "split_in_band",
        "split_out_of_band",
    ]


def test_zero_valid_cut_is_not_evaluable() -> None:
    row = _row(
        valid_cut_count=0,
        threshold_test_count=24,
        cut_band_filtered_cells=24,
        hit_cut_count=0,
    )
    aggregate = aggregate_geometry_rows([row])
    assert aggregate[0]["hit_rate"] is None
    assert aggregate[0]["hit_rate_status"] == "NOT_EVALUABLE"


def test_missing_geometry_field_is_rejected() -> None:
    row = _row()
    del row["valid_cut_count"]
    with pytest.raises(ValueError, match="valid_cut_count"):
        validate_geometry_row(row)


def test_downstream_false_counts_keep_unavailable_markers() -> None:
    row = _row(
        false_cut_hit_count="NOT_EVALUABLE",
        false_cluster_count="NOT_EVALUABLE",
        false_track_count="NOT_EVALUABLE",
    )
    aggregate = aggregate_geometry_rows([row])
    assert aggregate[0]["false_cut_hit_count"] is None
    assert aggregate[0]["false_cluster_count"] is None
    assert aggregate[0]["false_track_count"] is None
