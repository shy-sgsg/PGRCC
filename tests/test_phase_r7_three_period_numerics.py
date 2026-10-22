from __future__ import annotations

import unittest

import numpy as np

from scripts.phase_r.analyze_r7_three_period_numerics import (
    select_threshold_from_high_power_branch,
    union_reference_hit_mask,
)


class TestR7ThreePeriodNumericalHelpers(unittest.TestCase):
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
