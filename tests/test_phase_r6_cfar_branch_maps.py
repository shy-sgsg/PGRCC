"""R6 map analysis must honor the production CUT mask and strict CFAR test."""

from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r.analyze_r6_cfar_branch_maps import (
    build_valid_cut_mask,
    hit_index_hash,
    summarize_margin_map,
)


def test_valid_cut_mask_matches_circular_doppler_and_range_edges():
    geometry = {
        "height": 2,
        "width": 8,
        "guard_cells": 1,
        "background_cells": 1,
        "doppler_circular": "true",
        "exclude_row_start": -1,
        "exclude_row_end": -1,
        "cut_band_start": 0,
        "cut_band_end": 7,
        "cut_band_mode": 0,
    }

    mask = build_valid_cut_mask(geometry)

    assert mask.tolist() == [
        [False, False, True, True, True, True, False, False],
        [False, False, True, True, True, True, False, False],
    ]


def test_margin_uses_strict_greater_than_and_only_valid_cuts():
    geometry = {
        "height": 1,
        "width": 8,
        "guard_cells": 1,
        "background_cells": 1,
        "doppler_circular": "true",
        "exclude_row_start": -1,
        "exclude_row_end": -1,
        "cut_band_start": 0,
        "cut_band_end": 7,
        "cut_band_mode": 0,
    }
    mask = build_valid_cut_mask(geometry)
    power = np.array([[0, 0, 1, 2, 4, 8, 0, 0]], dtype=np.float32)
    threshold = np.array([[0, 0, 1, 2, 2, 16, 0, 0]], dtype=np.float32)
    hits = power > threshold

    summary = summarize_margin_map(power, threshold, hits, mask)

    assert summary["valid_cut_count"] == 4
    assert summary["hit_count"] == 1
    assert summary["hit_mask_matches_power_threshold"] is True
    assert summary["within_0p25_db_count"] == 2
    assert summary["margin_p50_finite_db"] == 0.0


def test_empty_hit_hash_uses_registered_fnv_offset_basis():
    assert hit_index_hash(np.zeros(5, dtype=np.float32)) == "fnv1a64:14650fb0739d0383"


def test_nonpositive_thresholds_are_counted_and_excluded_from_log_margins():
    geometry = {
        "height": 1,
        "width": 8,
        "guard_cells": 1,
        "background_cells": 1,
        "doppler_circular": "true",
        "exclude_row_start": -1,
        "exclude_row_end": -1,
        "cut_band_start": 0,
        "cut_band_end": 7,
        "cut_band_mode": 0,
    }
    mask = build_valid_cut_mask(geometry)
    power = np.array([[0, 0, 1, 2, 3, 4, 0, 0]], dtype=np.float32)
    threshold = np.array([[0, 0, 0, 1, 2, -1, 0, 0]], dtype=np.float32)
    hits = power > threshold

    summary = summarize_margin_map(power, threshold, hits, mask)

    assert summary["hit_mask_matches_power_threshold"] is True
    assert summary["valid_cut_count"] == 4
    assert summary["threshold_nonpositive_count"] == 2
    assert summary["threshold_negative_count"] == 1
    assert summary["threshold_zero_count"] == 1
    assert summary["threshold_nonpositive_hit_count"] == 2
    assert summary["evaluable_margin_count"] == 2
    assert summary["margin_status"] == "PARTIALLY_EVALUABLE_NONPOSITIVE_THRESHOLD"
