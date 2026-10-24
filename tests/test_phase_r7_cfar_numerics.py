"""Tests for the read-only R7 CFAR comparison helpers."""

from __future__ import annotations

import unittest

import numpy as np

from scripts.phase_r.analyze_r7_cfar_numerics import (
    aggregate_legacy_r6_rows,
    summarize_nonpositive_thresholds,
    union_reference_hit_mask,
)


class TestR7CfarNumerics(unittest.TestCase):
    def test_union_hit_mask_preserves_hits_from_either_production_branch(self) -> None:
        dynamic_power = np.array([[2.0, 2.0, 0.0]], dtype=np.float32)
        dynamic_threshold = np.array([[1.0, 3.0, 1.0]], dtype=np.float32)
        dynamic_valid = np.array([[True, True, False]])
        full_power = np.array([[10.0, 0.0, 100.0]], dtype=np.float32)
        full_threshold = np.array([[11.0, 1.0, 0.0]], dtype=np.float32)
        full_valid = np.array([[True, True, True]])

        actual = union_reference_hit_mask(
            dynamic_power,
            dynamic_threshold,
            dynamic_valid,
            full_power,
            full_threshold,
            full_valid,
        )

        np.testing.assert_array_equal(actual, np.array([[True, False, True]]))

    def test_nonpositive_counts_distinguish_true_zero_reference_from_error(self) -> None:
        actual = np.array([[-1.0, 0.0, 0.0, 2.0, 0.0]], dtype=np.float32)
        reference = np.array([[1.0, 1.0, 0.0, 2.0, 0.0]], dtype=np.float64)
        valid = np.ones(actual.shape, dtype=bool)

        summary = summarize_nonpositive_thresholds(actual, reference, valid)

        self.assertEqual(summary["negative"], 1)
        self.assertEqual(summary["zero"], 3)
        self.assertEqual(summary["reference_zero"], 2)
        self.assertEqual(summary["nonpositive_with_positive_reference"], 2)

    def test_legacy_r6_summary_sums_each_condition_mode_without_mixing_branches(self) -> None:
        rows = [
            {"mode": "ON", "branch": "dynamic", "negative_threshold_cuts": 2,
             "zero_threshold_cuts": 3, "reference_zero_threshold_cuts": 5,
             "erroneous_nonpositive_cuts": 2, "false_hit_flips": 8, "missed_hit_flips": 1,
             "actual_hit_count": 9, "reference_hit_count": 2, "valid_cut_count": 100,
             "max_float32_ulp_error": 1.0},
            {"mode": "ON", "branch": "full", "negative_threshold_cuts": 1,
             "zero_threshold_cuts": 4, "reference_zero_threshold_cuts": 6,
             "erroneous_nonpositive_cuts": 3, "false_hit_flips": 10, "missed_hit_flips": 0,
             "actual_hit_count": 11, "reference_hit_count": 1, "valid_cut_count": 100,
             "max_float32_ulp_error": 2.0},
            {"mode": "OFF", "branch": "dynamic", "negative_threshold_cuts": 7,
             "zero_threshold_cuts": 0, "reference_zero_threshold_cuts": 0,
             "erroneous_nonpositive_cuts": 7, "false_hit_flips": 11, "missed_hit_flips": 2,
             "actual_hit_count": 13, "reference_hit_count": 2, "valid_cut_count": 100,
             "max_float32_ulp_error": 3.0},
        ]

        summary = aggregate_legacy_r6_rows(rows)

        self.assertEqual(summary["ON"]["branch_map_count"], 2)
        self.assertEqual(summary["ON"]["negative_threshold_cuts"], 3)
        self.assertEqual(summary["ON"]["zero_threshold_cuts"], 7)
        self.assertEqual(summary["ON"]["reference_zero_threshold_cuts"], 11)
        self.assertEqual(summary["ON"]["erroneous_nonpositive_cuts"], 5)
        self.assertEqual(summary["ON"]["false_hit_flips"], 18)
        self.assertEqual(summary["ON"]["missed_hit_flips"], 1)
        self.assertEqual(summary["ON"]["valid_cut_count"], 200)
        self.assertEqual(summary["ON"]["max_float32_ulp_error"], 2.0)
        self.assertEqual(summary["OFF"]["branch_map_count"], 1)
        self.assertEqual(summary["OFF"]["false_hit_flips"], 11)


if __name__ == "__main__":
    unittest.main()
