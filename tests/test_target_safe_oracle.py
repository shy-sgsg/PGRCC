import unittest

import numpy as np

from scripts.evaluate_target_safe_oracle import (
    EXPERT_LAMBDAS,
    LAMBDAS,
    select_safe_candidate,
    scale_frozen_plan,
)


class TargetSafeOracleTests(unittest.TestCase):
    def test_lambda_zero_equals_current_identity(self) -> None:
        plan = {
            "fallback": False,
            "apply_delay": True,
            "apply_phase": True,
            "estimated_delay_ns": 8.0,
            "phase_values": np.asarray([1.0, -2.0]),
        }
        scaled = scale_frozen_plan(plan, 0.0)
        self.assertTrue(scaled["fallback"])
        self.assertFalse(scaled["apply_delay"])
        self.assertFalse(scaled["apply_phase"])
        self.assertIsNone(scaled["phase_values"])

    def test_lambda_one_preserves_full_frozen_correction(self) -> None:
        plan = {
            "fallback": False,
            "apply_delay": True,
            "apply_phase": True,
            "estimated_delay_ns": 8.0,
            "phase_values": np.asarray([1.0, -2.0]),
        }
        scaled = scale_frozen_plan(plan, 1.0)
        self.assertFalse(scaled["fallback"])
        self.assertTrue(scaled["apply_delay"])
        self.assertTrue(scaled["apply_phase"])
        self.assertEqual(scaled["estimated_delay_ns"], 8.0)
        np.testing.assert_allclose(scaled["phase_values"], plan["phase_values"])

    def test_lambda_grid_is_frozen(self) -> None:
        self.assertEqual(LAMBDAS, (0.0, 0.25, 0.5, 0.75, 1.0))
        self.assertEqual(EXPERT_LAMBDAS, (0.25, 0.5, 0.75, 1.0))

    def test_selection_is_per_scene_and_maximizes_safe_cancellation(self) -> None:
        rows = [
            {"method": "J0_Current", "lambda": 0.0,
             "cancellation_db": 1.0, "safe": True},
            {"method": "J5_Selective_Physics_Calibration", "lambda": 0.25,
             "cancellation_db": 1.2, "safe": True},
            {"method": "J6_Joint_Phase_Surface", "lambda": 0.5,
             "cancellation_db": 1.5, "safe": False},
            {"method": "J6_Joint_Phase_Surface", "lambda": 0.75,
             "cancellation_db": 1.1, "safe": True},
        ]
        selected = select_safe_candidate(rows)
        self.assertEqual(selected["method"], "J5_Selective_Physics_Calibration")
        self.assertEqual(selected["lambda"], 0.25)

    def test_selection_falls_back_to_current_when_all_experts_are_unsafe(self) -> None:
        rows = [
            {"method": "J0_Current", "lambda": 0.0,
             "cancellation_db": 1.0, "safe": True},
            {"method": "J5_Selective_Physics_Calibration", "lambda": 0.25,
             "cancellation_db": 2.0, "safe": False},
        ]
        self.assertEqual(select_safe_candidate(rows)["method"], "J0_Current")


if __name__ == "__main__":
    unittest.main()
