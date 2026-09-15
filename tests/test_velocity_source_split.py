from __future__ import annotations

import unittest

from scripts import run_velocity_error_study as study


class VelocitySourceSplitTests(unittest.TestCase):
    def test_first_delta_velocity_levels_are_symmetric_and_include_zero(self) -> None:
        self.assertEqual(
            study.delta_velocity_levels(),
            (0.0, -0.05, 0.05, -0.1, 0.1, -0.2, 0.2, -0.5, 0.5),
        )

    def test_velocity_pair_requires_both_explicit_fields(self) -> None:
        self.assertEqual(
            study.require_velocity_pair(
                {"velocity_true_mps": 60.0, "velocity_reported_mps": 59.8}
            ),
            (60.0, 59.8),
        )
        with self.assertRaises(ValueError):
            study.require_velocity_pair({"velocity_true_mps": 60.0})
        with self.assertRaises(ValueError):
            study.require_velocity_pair({"velocity_reported_mps": 60.0})
        with self.assertRaises(ValueError):
            study.require_velocity_pair({"speed_mps": 60.0})

    def test_velocity_contract_assigns_physical_and_processing_sources(self) -> None:
        contract = study.velocity_source_contract()
        self.assertEqual(
            contract["true_velocity_mps"]["consumers"],
            [
                "true_platform_trajectory",
                "echo_phase",
                "clutter_doppler",
                "target_relative_geometry",
            ],
        )
        self.assertEqual(
            contract["reported_velocity_mps"]["consumers"],
            [
                "ins_header",
                "ctdr",
                "p38",
                "clutter_ridge_model",
                "steering",
                "velocity_conversion",
            ],
        )

    def test_branch_contract_keeps_known_error_out_of_blind_estimator(self) -> None:
        branches = study.branch_contract()
        self.assertEqual(branches["V0_Current"]["information_condition"], "unknown_velocity_error")
        self.assertEqual(branches["V1K_Known_Correction"]["information_condition"], "known_velocity_error")
        self.assertEqual(branches["V1_Blind_Deterministic"]["information_condition"], "unknown_velocity_error")
        self.assertTrue(branches["V1K_Known_Correction"]["known_error_evaluation_only"])
        self.assertFalse(branches["V1_Blind_Deterministic"]["target_truth_used"])

    def test_estimator_input_audit_rejects_truth_and_known_error(self) -> None:
        audit = study.estimator_input_audit(
            ["off/data/stage2_period_0000.bin"],
            {"velocity_reported_mps": 60.0},
            contains_target_truth=False,
            contains_known_velocity_error=False,
            contains_true_velocity=False,
        )
        self.assertTrue(audit["off_only"])
        self.assertFalse(audit["contains_true_velocity"])
        with self.assertRaises(ValueError):
            study.estimator_input_audit(
                ["off/data/stage2_period_0000.bin"],
                {"velocity_reported_mps": 60.0, "velocity_true_mps": 60.0},
                contains_target_truth=False,
                contains_known_velocity_error=False,
                contains_true_velocity=True,
            )

    def test_deterministic_estimator_combines_target_free_observables(self) -> None:
        result = study.combine_velocity_observables(
            reported_velocity_mps=60.0,
            ridge_velocity_mps=60.2,
            p38_velocity_mps=59.8,
            ctdr_velocity_mps=60.1,
            slow_time_velocity_mps=60.0,
            residuals={"ridge": 0.2, "p38": -0.2, "ctdr": 0.1, "slow_time": 0.0},
        )
        self.assertEqual(result["status"], "VALID")
        self.assertAlmostEqual(result["estimate_delta_v_mps"], 0.025)
        self.assertEqual(
            result["causal_data_sources"],
            [
                "clutter_doppler_ridge_displacement",
                "p38_phase_slope",
                "ctdr_residual",
                "four_channel_slow_time_phase",
            ],
        )
        self.assertFalse(result["target_truth_used"])
        with self.assertRaises(ValueError):
            study.combine_velocity_observables(
                reported_velocity_mps=60.0,
                ridge_velocity_mps=None,
                p38_velocity_mps=None,
                ctdr_velocity_mps=None,
                slow_time_velocity_mps=None,
                residuals={},
            )

    def test_deterministic_estimator_falls_back_when_candidates_exceed_supported_error(self) -> None:
        result = study.combine_velocity_observables(
            reported_velocity_mps=60.0,
            ridge_velocity_mps=63.5,
            p38_velocity_mps=64.0,
            ctdr_velocity_mps=None,
            slow_time_velocity_mps=63.8,
            residuals={"ridge": 3.5, "p38": 4.0, "slow_time": 3.8},
            max_supported_error_mps=1.0,
        )
        self.assertEqual(result["status"], "FALLBACK_MODEL_MISMATCH")
        self.assertEqual(result["fallback_reason"], "velocity_candidate_outside_supported_error_bound")
        self.assertTrue(result["model_mismatch"])


if __name__ == "__main__":
    unittest.main()
