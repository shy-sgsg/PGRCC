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

    def test_apply_nuisance_config_changes_only_requested_profile(self) -> None:
        template = {
            "channel_impairments": {
                "enabled": False,
                "channel_amp_mismatch_db": 0.35,
                "channel_fixed_phase_mismatch_deg": 8.0,
            },
            "scene": {
                "area_clutter": {"texture_sigma": 0.2},
                "thermal_noise": {"noise_power": 0.001},
                "range_min_m": 8250.0,
                "range_max_m": 9750.0,
            },
            "scan": {
                "scan_min_deg": -20.0,
                "scan_step_deg": 5.0,
                "beam_count": 9,
            },
            "random": {"beam_count": 9},
        }
        result = MATRIX.apply_nuisance_config(
            template,
            {"fixed_channel_phase_mismatch_deg": 16.0},
        )
        self.assertEqual(result["channel_impairments"]["channel_fixed_phase_mismatch_deg"], 16.0)
        self.assertEqual(result["channel_impairments"]["channel_amp_mismatch_db"], 0.35)
        self.assertEqual(result["scene"]["thermal_noise"]["noise_power"], 0.001)
        self.assertEqual(template["channel_impairments"]["enabled"], False)

    def test_apply_nuisance_config_updates_angle_span_and_calibration_window(self) -> None:
        template = {
            "channel_impairments": {},
            "scene": {
                "area_clutter": {},
                "thermal_noise": {},
                "range_min_m": 8250.0,
                "range_max_m": 9750.0,
            },
            "scan": {"scan_min_deg": -20.0, "scan_step_deg": 5.0, "beam_count": 9},
            "random": {"beam_count": 9},
        }
        result = MATRIX.apply_nuisance_config(
            template,
            {"angle_span_deg": 10.0, "calibration_range_m": 9600.0},
        )
        self.assertEqual(result["scan"]["scan_min_deg"], -10.0)
        self.assertEqual(result["scan"]["beam_count"], 5)
        self.assertEqual(result["random"]["beam_count"], 5)
        self.assertEqual(result["scene"]["range_min_m"], 8850.0)
        self.assertEqual(result["scene"]["range_max_m"], 10350.0)
        self.assertEqual(result["scene"]["area_clutter"]["calibration_range_m"], 9600.0)

    def test_summarize_case_preserves_v2_uncertainty_and_dynamic_deadband(self) -> None:
        six = {
            "fit_status": "fit",
            "status": "APPLY_ESTIMATED_CORRECTION",
            "estimated_baseline_error_m": 0.0025,
            "fit_rmse_rad": 0.01,
            "uncertainty_m": 0.0001,
            "deadband_m": 0.0002,
            "sensitivity_rad_per_m": 42.0,
            "model_disagreement_mean_abs_rad": 0.03,
            "observation_count": 18,
        }
        single = {
            "fit_status": "fit",
            "status": "NO_CORRECTION_NEEDED",
            "estimated_baseline_error_m": 0.0001,
            "uncertainty_m": 0.0002,
            "deadband_m": 0.0003,
        }
        row = MATRIX.summarize_case(0.0025, six, single)
        self.assertAlmostEqual(row["six_uncertainty_m"], 0.0001)
        self.assertAlmostEqual(row["six_deadband_m"], 0.0002)
        self.assertAlmostEqual(row["six_sensitivity_rad_per_m"], 42.0)
        self.assertAlmostEqual(row["six_model_disagreement_mean_abs_rad"], 0.03)
        self.assertFalse(row["six_fallback"])


if __name__ == "__main__":
    unittest.main()
