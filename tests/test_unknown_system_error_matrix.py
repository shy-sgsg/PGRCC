from __future__ import annotations

import importlib.util
import unittest

import numpy as np


ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_unknown_system_error_matrix.py"
SPEC = importlib.util.spec_from_file_location("unknown_system_error_matrix", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
MATRIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MATRIX)


class UnknownSystemErrorMatrixTests(unittest.TestCase):
    def test_bootstrap_ci_is_deterministic_and_contains_constant_sample(self) -> None:
        values = np.asarray([0.001, 0.001, 0.001], dtype=np.float64)
        first = MATRIX.bootstrap_mean_ci(values, seed=7, resamples=200)
        second = MATRIX.bootstrap_mean_ci(values, seed=7, resamples=200)
        self.assertEqual(first, second)
        self.assertAlmostEqual(first[0], 0.001, places=12)
        self.assertAlmostEqual(first[1], 0.001, places=12)

    def test_estimator_summary_keeps_truth_outside_unknown_only_result(self) -> None:
        six = {
            "fit_status": "fit",
            "estimated_baseline_error_m": 0.0024,
            "global_fit": {"rmse_rad": 0.08},
            "closure_phase_residual_rad": 0.03,
            "closure_phase_p95_rad": 0.09,
            "cross_pair_consistency": {"max_minus_min_delta_d_m": 0.0002},
            "pair_fits": {"C12": {"rmse_rad": 0.07}},
        }
        single = {"fit_status": "fit", "estimated_baseline_error_m": 0.0028}
        row = MATRIX.summarize_case(0.0025, six, single)
        self.assertAlmostEqual(row["truth_delta_m"], 0.0025)
        self.assertAlmostEqual(row["six_error_m"], -0.0001)
        self.assertAlmostEqual(row["six_pair_residual_rmse_rad"], 0.08)
        self.assertAlmostEqual(row["closure_residual_mean_abs_rad"], 0.03)
        self.assertNotIn("truth", six)

    def test_estimator_metadata_contract_excludes_truth_and_impairments(self) -> None:
        resolved = {
            "waveform": {"pulse_len": 8},
            "range_processing": {"sample_delay_us": 0.0},
            "platform": {"height_m": 6000.0},
            "simulation_geometry": {"local_x_axis": "north"},
            "scene": {"ground_z_m": 0.0},
            "channel_geometry": {"true_channel_positions": [1]},
            "targets": [{"truth": 1}],
            "channel_impairments": {"baseline_error_m": 0.01},
        }
        metadata = MATRIX.estimator_metadata(resolved)
        self.assertIn("waveform", metadata)
        self.assertIn("simulation_geometry", metadata)
        self.assertNotIn("channel_geometry", metadata)
        self.assertNotIn("targets", metadata)
        self.assertNotIn("channel_impairments", metadata)


if __name__ == "__main__":
    unittest.main()
