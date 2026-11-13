from __future__ import annotations

import csv
import importlib.util
import json
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_unknown_system_error_servo_pilot.py"
SPEC = importlib.util.spec_from_file_location("unknown_system_error_servo_pilot", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
PILOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PILOT)


def _historical_v3_root() -> Path:
    candidates = (
        ROOT / "outputs/unknown_system_error_servo_pilot_20260914_v3",
        ROOT.parent.parent / "outputs/unknown_system_error_servo_pilot_20260914_v3",
    )
    return next(
        (candidate for candidate in candidates if candidate.is_dir()), candidates[0]
    )


class UnknownSystemErrorServoPilotTests(unittest.TestCase):
    def test_manifest_contract_is_target_assisted(self) -> None:
        contract = PILOT.servo_manifest_contract()

        self.assertEqual(contract["schema"], "target_assisted_servo_calibration_pilot_v1")
        self.assertEqual(contract["calibration_mode"], "target_assisted")
        estimator = contract["unknown_estimator"]
        self.assertIsInstance(estimator, dict)
        self.assertTrue(estimator["target_assisted"])
        self.assertFalse(estimator["operational"])
        self.assertTrue(estimator["target_assistance_used"])
        self.assertFalse(estimator["servo_truth_used_in_estimator"])
        self.assertIn("paired ON-OFF target residual", estimator["causal_data_sources"])
        self.assertIn(
            "configured nominal target/range hypothesis",
            estimator["causal_data_sources"],
        )
        serialized = json.dumps(contract, ensure_ascii=False).lower()
        self.assertNotIn("blind scene-wide", serialized)
        self.assertNotIn("target-free calibration", serialized)

    def test_stable_estimate_and_decision_fields_are_declared(self) -> None:
        contract = PILOT.servo_estimator_contract()
        self.assertEqual(contract["method"], contract["estimator_name"])
        self.assertEqual(contract["calibration_mode"], "target_assisted")
        self.assertTrue(contract["target_assisted"])
        self.assertIn("paired ON-OFF target residual", contract["causal_data_sources"])

        required_estimate_fields = {
            "method",
            "estimate_deg",
            "uncertainty_deg",
            "fit_rmse",
            "phase_rmse",
            "ridge_rmse",
            "clutter_cancellation_db",
            "model_mismatch",
            "fallback_reason",
            "status",
            "causal_data_sources",
        }
        self.assertTrue(required_estimate_fields.issubset(PILOT.SERVO_ESTIMATE_FIELDS))
        self.assertTrue(PILOT.SERVO_DECISION_FIELDS.issuperset({
            "calibration_mode",
            "target_assisted",
            "model_mismatch",
            "fallback_reason",
            "causal_data_sources",
        }))

    def test_historical_v3_18_case_numeric_baseline_is_readable(self) -> None:
        csv_path = _historical_v3_root() / "servo_estimates.csv"
        if not csv_path.is_file():
            self.skipTest(f"historical v3 evidence is not available: {csv_path}")
        with csv_path.open("r", encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))

        self.assertEqual(len(rows), 18)
        self.assertTrue({"fit_status", "estimated_true_minus_reported_deg"}.issubset(rows[0]))
        errors = [
            float(row["estimated_true_minus_reported_deg"]) - float(row["error_deg"])
            for row in rows
        ]
        bias = sum(errors) / len(errors)
        rmse = math.sqrt(sum(value * value for value in errors) / len(errors))
        self.assertAlmostEqual(bias, -0.0108, delta=0.0005)
        self.assertAlmostEqual(rmse, 0.0138, delta=0.0005)


if __name__ == "__main__":
    unittest.main()
