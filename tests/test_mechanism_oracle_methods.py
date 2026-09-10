"""Formula-level tests for the measured-F1/F2 M3 oracle methods."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_mechanism_aware_oracle import m3_oracle_methods  # noqa: E402


class MechanismOracleMethodTests(unittest.TestCase):
    def test_complex_ls_recovers_row_and_range_block_scalar(self) -> None:
        rows, cols = 5, 24
        f2 = np.ones((rows, cols), dtype=np.complex128)
        row_alpha = np.asarray([
            0.8 + 0.1j, 1.1 - 0.2j, 0.6 + 0.4j, 1.3 + 0.0j, 0.9 - 0.3j,
        ])
        f1 = row_alpha[:, None] * f2
        before = np.ones((rows, cols), dtype=np.float64)
        current_after = np.ones((rows, cols), dtype=np.float64)
        valid = np.ones((rows, cols), dtype=bool)

        methods = m3_oracle_methods(
            f1, f2, before, current_after, valid,
            0, rows - 1, 0, cols - 1, range_block_bins=8,
        )
        by_name = {item["method"]: item for item in methods}
        self.assertGreater(by_name["O3_row_complex_scalar_LS"]["cancellation_db"], 200.0)
        self.assertGreater(by_name["O4_range_block_complex_LS"]["cancellation_db"], 200.0)
        self.assertGreater(by_name["O2_row_phase_current_amplitude_oracle"]["cancellation_db"], 200.0)
        self.assertEqual(by_name["O3_row_complex_scalar_LS"]["target_preservation_status"],
                         "not_evaluable_from_F1_F2_clutter_tap")


if __name__ == "__main__":
    unittest.main()
