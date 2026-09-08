#!/usr/bin/env python3
import math
from pathlib import Path
import sys
import unittest

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from gmti_eval_metrics import (  # noqa: E402
    estimate_target_power,
    linear_power_ratio_db,
    summarize_power,
)
from run_p5_csi_eval import map_frequency_to_dft_axis  # noqa: E402


class P5MetricTests(unittest.TestCase):
    def test_db_conversion_uses_linear_power_ratio(self):
        self.assertAlmostEqual(linear_power_ratio_db(100.0, 10.0), 10.0)
        self.assertTrue(math.isnan(linear_power_ratio_db(0.0, 10.0)))

    def test_target_power_subtracts_background_in_linear_domain(self):
        roi = np.asarray([11.0, 13.0, 17.0])
        self.assertAlmostEqual(estimate_target_power(roi, 1.0), 38.0)

    def test_power_summary_filters_invalid_values(self):
        summary = summarize_power(np.asarray([1.0, 4.0, np.nan, -1.0]))
        self.assertEqual(summary["valid_count"], 2)
        self.assertAlmostEqual(summary["mean_power"], 2.5)
        self.assertAlmostEqual(summary["rms"], math.sqrt(2.5))

    def test_unwrapped_truth_maps_to_one_prf_fft_axis(self):
        axis = np.arange(130, dtype=np.float64) * 10.0 - 733.3273
        mapped, order = map_frequency_to_dft_axis(-4092.78989917, axis)
        self.assertEqual(order, 3)
        self.assertAlmostEqual(mapped, -192.78989917, places=8)
        row = int(np.argmin(np.abs(axis - mapped)))
        self.assertEqual(row, 54)

    def test_on_axis_frequency_keeps_zero_wrap_order(self):
        axis = np.arange(130, dtype=np.float64) * 10.0 - 650.0
        mapped, order = map_frequency_to_dft_axis(20.0, axis)
        self.assertEqual(order, 0)
        self.assertEqual(mapped, 20.0)

    def test_nonuniform_fft_axis_is_rejected(self):
        with self.assertRaises(RuntimeError):
            map_frequency_to_dft_axis(1.0, np.asarray([0.0, 1.0, 2.5]))


if __name__ == "__main__":
    unittest.main()
