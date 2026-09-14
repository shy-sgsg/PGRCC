from __future__ import annotations

import unittest

import numpy as np

from scripts import audit_unknown_system_error_pfa as pfa


class PfaAuditTests(unittest.TestCase):
    def test_valid_cut_denominators_match_circular_dynamic_contract(self) -> None:
        masks = pfa._valid_masks(130, 4096, 4, 16, True, 46, 84)
        self.assertEqual(int(np.count_nonzero(masks["full"])), 527280)
        self.assertEqual(int(np.count_nonzero(masks["dynamic"])), 158184)
        self.assertEqual(int(np.count_nonzero(masks["outside_dynamic"])), 369096)

    def test_directional_go_means_match_circular_reference(self) -> None:
        rng = np.random.default_rng(17)
        power = rng.random((23, 31))
        left, right, top, bottom = pfa._directional_go_means(power, 2, 3)
        radius = 5
        row, col = 0, 5
        rows = [(row + offset) % power.shape[0] for offset in range(-radius, radius + 1)]
        self.assertAlmostEqual(
            left[row, col], power[np.ix_(rows, range(col - radius, col - 2))].mean()
        )
        self.assertAlmostEqual(
            right[row, col], power[np.ix_(rows, range(col + 3, col + radius + 1))].mean()
        )
        self.assertAlmostEqual(
            top[row, col],
            power[np.ix_([18, 19, 20], range(col - radius, col + radius + 1))].mean(),
        )
        self.assertAlmostEqual(
            bottom[row, col],
            power[np.ix_([3, 4, 5], range(col - radius, col + radius + 1))].mean(),
        )


if __name__ == "__main__":
    unittest.main()
