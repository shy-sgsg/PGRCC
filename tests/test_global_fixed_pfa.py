"""Leakage and held-out reuse tests for global fixed-Pfa evaluation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_global_fixed_pfa import (  # noqa: E402
    calibrate_thresholds,
    evaluate_causal_rows,
    evaluate_frozen_scores,
)


class GlobalFixedPfaTests(unittest.TestCase):
    def test_thresholds_are_frozen_from_calibration_only(self) -> None:
        calibration = calibrate_thresholds(
            [("J0_Current", np.arange(1.0, 101.0))],
            requested_pfas=(0.1, 0.01), top_k=200,
        )
        threshold_before = calibration["methods"]["J0_Current"]["thresholds"]["0.01"]
        held_out = [("J0_Current", np.asarray([1.0e12, 2.0e12]))]
        result = evaluate_frozen_scores(held_out, calibration)
        threshold_after = calibration["methods"]["J0_Current"]["thresholds"]["0.01"]
        self.assertEqual(threshold_before, threshold_after)
        self.assertEqual(result["J0_Current"]["threshold_source"],
                         "frozen_calibration_negative_controls")
        self.assertEqual(result["J0_Current"]["empirical_pfa"]["0.01"], 1.0)

    def test_unthresholded_oracle_cannot_leak_test_scores(self) -> None:
        calibration = calibrate_thresholds(
            [("J0_Current", np.arange(10.0))], requested_pfas=(0.5,), top_k=20)
        rows = [
            {"method": "J0_Current", "on_peak": 10.0, "off_peak": 1.0,
             "legacy_hit": True, "target_preservation_db": 0.0},
            {"method": "Oracle_Known_Joint", "on_peak": 100.0, "off_peak": 1.0,
             "legacy_hit": True, "target_preservation_db": 0.0},
        ]
        evaluated = evaluate_causal_rows(rows, calibration)
        self.assertIn("J0_Current", evaluated)
        self.assertNotIn("Oracle_Known_Joint", evaluated)
        self.assertEqual(evaluated["_unthresholded_methods"], ["Oracle_Known_Joint"])


if __name__ == "__main__":
    unittest.main()
