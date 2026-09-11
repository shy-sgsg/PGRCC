import unittest

import numpy as np

from scripts.evaluate_target_safe_oracle import (
    LAMBDAS,
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


if __name__ == "__main__":
    unittest.main()
