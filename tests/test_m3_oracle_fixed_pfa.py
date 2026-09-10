"""Formula regressions for the post-hoc M3 O1--O5 score evaluator."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_m3_oracle_fixed_pfa import METHODS, score_maps  # noqa: E402


class M3OracleFixedPfaTests(unittest.TestCase):
    def test_score_maps_are_finite_on_common_support(self) -> None:
        rows, cols = 7, 32
        f2 = np.ones((rows, cols), dtype=np.complex128)
        alpha = np.asarray([0.8 + 0.1j, 1.1 - 0.2j, 0.6 + 0.4j,
                           1.3 + 0.0j, 0.9 - 0.3j, 1.0 + 0.2j, 0.7 - 0.1j])
        f1 = alpha[:, None] * f2
        valid = np.ones((rows, cols), dtype=bool)
        run = {
            "f1": f1, "f2": f2,
            "after": np.ones((rows, cols), dtype=np.float64),
            "before": np.ones((rows, cols), dtype=np.float64),
            "valid": valid, "active": valid,
            "az_st": 0, "az_ed": rows - 1, "rg_st": 0, "rg_ed": cols - 1,
        }
        scores = score_maps(run, valid)
        self.assertEqual(set(scores), set(METHODS))
        for value in scores.values():
            self.assertTrue(np.all(np.isfinite(value)))
        self.assertLess(float(np.max(scores["O3_row_complex_scalar_LS"])), 1.0e-20)


if __name__ == "__main__":
    unittest.main()
