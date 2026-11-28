from __future__ import annotations

import unittest

from scripts import run_yaw_error_study as study


class AttitudeSourceSplitTests(unittest.TestCase):
    def test_yaw_levels_keep_pitch_and_roll_out_of_first_matrix(self) -> None:
        self.assertEqual(study.yaw_error_levels(), (0.0, -0.5, 0.5, -1.0, 1.0))
        contract = study.attitude_source_contract()
        self.assertEqual(contract["true_yaw_deg"]["consumers"], [
            "true_platform_heading",
            "clutter_look_geometry",
            "target_relative_geometry",
            "echo_phase",
        ])
        self.assertEqual(contract["reported_yaw_deg"]["consumers"], [
            "reported_geometry",
            "beam_steering_reference",
            "angle_conversion",
        ])
        self.assertEqual(contract["pitch_deg"], 0.0)
        self.assertEqual(contract["roll_deg"], 0.0)

    def test_condition_contract_separates_yaw_servo_and_baseline(self) -> None:
        conditions = study.condition_contract()
        self.assertEqual(conditions["baseline"]["yaw_true_minus_reported_deg"], 0.0)
        self.assertEqual(conditions["baseline"]["servo_true_minus_reported_deg"], 0.0)
        self.assertEqual(conditions["yaw"]["yaw_true_minus_reported_deg"], "case_value")
        self.assertEqual(conditions["yaw"]["servo_true_minus_reported_deg"], 0.0)
        self.assertEqual(conditions["servo"]["yaw_true_minus_reported_deg"], 0.0)
        self.assertEqual(conditions["servo"]["servo_true_minus_reported_deg"], "case_value")

    def test_yaw_first_validation_rejects_pitch_roll_and_joint_fit(self) -> None:
        self.assertEqual(
            study.require_yaw_first_state(
                {"yaw_true_deg": 1.0, "yaw_reported_deg": 0.0, "pitch_deg": 0.0, "roll_deg": 0.0}
            ),
            (1.0, 0.0),
        )
        with self.assertRaises(ValueError):
            study.require_yaw_first_state(
                {"yaw_true_deg": 1.0, "yaw_reported_deg": 0.0, "pitch_deg": 0.1, "roll_deg": 0.0}
            )
        with self.assertRaises(ValueError):
            study.require_yaw_first_state(
                {"yaw_true_deg": 1.0, "yaw_reported_deg": 0.0, "pitch_deg": 0.0, "roll_deg": 0.0,
                 "joint_attitude_fit": True}
            )

    def test_blind_yaw_input_is_off_only_and_reported_geometry_only(self) -> None:
        audit = study.estimator_input_audit(
            ["case/off/data/stage2_period_0000.bin"],
            {"reported_yaw_deg": 0.0, "reported_geometry": {"platform_heading_deg": 0.0}},
            contains_target_truth=False,
            contains_known_yaw_error=False,
            contains_true_yaw=False,
        )
        self.assertTrue(audit["off_only"])
        self.assertFalse(audit["contains_true_yaw"])
        with self.assertRaises(ValueError):
            study.estimator_input_audit(
                ["case/on/data/stage2_period_0000.bin"],
                {"reported_yaw_deg": 0.0},
                contains_target_truth=False,
                contains_known_yaw_error=False,
                contains_true_yaw=False,
            )


if __name__ == "__main__":
    unittest.main()
