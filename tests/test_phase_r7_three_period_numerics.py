from __future__ import annotations

import io
import unittest

import numpy as np

from scripts.phase_r.analyze_r7_three_period_numerics import (
    _threshold_metrics,
    _write_csv_rows,
    select_threshold_from_high_power_branch,
    union_reference_hit_mask,
)


class TestR7ThreePeriodNumericalHelpers(unittest.TestCase):
    def test_threshold_error_quantiles_use_only_valid_cuts(self) -> None:
        actual = np.asarray([1.0, 2.0, 4.0, 101.0], dtype=np.float32)
        reference = np.ones(4, dtype=np.float64)
        valid = np.asarray([True, True, True, False])

        metrics = _threshold_metrics(actual, reference, valid)

        self.assertEqual(metrics["valid_cut_count"], 3)
        self.assertEqual(metrics["p50_abs_error"], 1.0)
        self.assertAlmostEqual(metrics["p90_abs_error"], 2.6)
        self.assertAlmostEqual(metrics["p95_abs_error"], 2.8)
        self.assertEqual(metrics["p99_abs_error"], 2.96)
        self.assertAlmostEqual(metrics["p99_9_abs_error"], 2.996)

    def test_csv_rows_use_lf_line_endings(self) -> None:
        stream = io.StringIO(newline="")

        _write_csv_rows(stream, [{"value": "checked"}])

        self.assertEqual(stream.getvalue(), "value\nchecked\n")

    def test_union_reference_counts_overlaps_once_and_uses_strict_cut(self) -> None:
        dynamic_power = np.asarray([6.0, 6.0, 4.0, 9.0], dtype=np.float32)
        dynamic_threshold = np.asarray([5.0, 5.0, 4.0, 5.0], dtype=np.float32)
        dynamic_valid = np.asarray([True, True, True, True])
        full_power = np.asarray([7.0, 8.0, 9.0, 4.0], dtype=np.float32)
        full_threshold = np.asarray([6.0, 8.0, 5.0, 4.0], dtype=np.float32)
        full_valid = np.asarray([True, True, True, True])

        union = union_reference_hit_mask(
            dynamic_power,
            dynamic_threshold,
            dynamic_valid,
            full_power,
            full_threshold,
            full_valid,
        )

        np.testing.assert_array_equal(union, np.asarray([True, True, True, True]))
        self.assertEqual(int(union.sum()), 4)  # branch hit counts sum to 5; row 0 overlaps

    def test_union_threshold_comes_from_higher_power_branch_with_dynamic_tie_break(self) -> None:
        dynamic_power = np.asarray([7.0, 9.0, 8.0], dtype=np.float32)
        dynamic_threshold = np.asarray([1.0, 2.0, 3.0], dtype=np.float32)
        full_power = np.asarray([8.0, 8.0, 8.0], dtype=np.float32)
        full_threshold = np.asarray([4.0, 5.0, 6.0], dtype=np.float32)

        selected = select_threshold_from_high_power_branch(
            dynamic_power, dynamic_threshold, full_power, full_threshold
        )

        np.testing.assert_array_equal(selected, np.asarray([4.0, 2.0, 3.0], dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
