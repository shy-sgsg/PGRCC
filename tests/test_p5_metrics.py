#!/usr/bin/env python3
import math
import csv
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from gmti_eval_metrics import (  # noqa: E402
    estimate_target_power,
    linear_power_ratio_db,
    summarize_power,
)
from run_p5_csi_eval import (  # noqa: E402
    load_truth,
    map_frequency_to_dft_axis,
    resolve_period_id,
)


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

    def test_truth_is_filtered_to_tap_period(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            truth_dir = Path(temp_dir)
            with (truth_dir / "truth_targets_by_beam.csv").open(
                    "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "period_id", "beam_id", "target_id", "visible", "expected_bin",
                ])
                writer.writeheader()
                writer.writerows([
                    {"period_id": 0, "beam_id": 50, "target_id": "T", "visible": 1,
                     "expected_bin": 100},
                    {"period_id": 1, "beam_id": 50, "target_id": "T", "visible": 1,
                     "expected_bin": 101},
                ])
            with (truth_dir / "truth_pulse.csv").open(
                    "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "period_id", "beam_id_1based", "target_name", "injection_enabled",
                    "af_total_truth_hz",
                ])
                writer.writeheader()
                writer.writerows([
                    {"period_id": 0, "beam_id_1based": 50, "target_name": "T",
                     "injection_enabled": 1, "af_total_truth_hz": -10.0},
                    {"period_id": 1, "beam_id_1based": 50, "target_name": "T",
                     "injection_enabled": 1, "af_total_truth_hz": -20.0},
                ])

            truths, af_truth = load_truth(truth_dir, period_id=1)

        self.assertEqual(len(truths[50]), 1)
        self.assertEqual(truths[50][0]["expected_bin"], "101")
        self.assertEqual(af_truth[(50, "T")], -20.0)

    def test_tap_period_is_inferred_and_mismatch_rejected(self):
        taps = {50: {"manifest": {"period_id": "2"}}}
        self.assertEqual(resolve_period_id(taps), 2)
        self.assertEqual(resolve_period_id(taps, requested_period_id=2), 2)
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            resolve_period_id(taps, requested_period_id=1)


if __name__ == "__main__":
    unittest.main()
