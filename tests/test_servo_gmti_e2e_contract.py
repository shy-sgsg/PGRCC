from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_servo_gmti_e2e.py"
SPEC = importlib.util.spec_from_file_location("run_servo_gmti_e2e", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
E2E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E2E)


class ServoGmtiE2EContractTests(unittest.TestCase):
    def test_scene_contract_has_target_free_and_three_moving_classes(self) -> None:
        contract = E2E.scene_contract()
        self.assertEqual(
            set(contract),
            {"target_free", "slow_near_ridge", "medium", "fast"},
        )
        self.assertFalse(contract["target_free"]["target_enabled"])
        self.assertTrue(contract["slow_near_ridge"]["target_enabled"])
        self.assertTrue(contract["medium"]["target_enabled"])
        self.assertTrue(contract["fast"]["target_enabled"])
        self.assertEqual(contract["slow_near_ridge"]["evaluation_scope"], "moving_target")

    def test_estimator_input_audit_accepts_only_off_for_s1(self) -> None:
        audit = E2E.estimator_input_audit(
            branch="S1",
            input_paths=["case/off/data/period_0000.bin"],
            contains_target_truth=False,
            contains_nominal_target=False,
            contains_known_servo_error=False,
            contains_servo_truth=False,
        )
        self.assertTrue(audit["off_only"])
        self.assertFalse(audit["contains_on_or_to"])
        for forbidden in (
            "contains_target_truth",
            "contains_nominal_target",
            "contains_known_servo_error",
            "contains_servo_truth",
        ):
            self.assertFalse(audit[forbidden])

    def test_estimator_input_audit_rejects_on_to_and_forbidden_metadata(self) -> None:
        cases = [
            ("case/on/data/period_0000.bin", False, False, False, False),
            ("case/to/data/period_0000.bin", False, False, False, False),
            ("case/off/data/period_0000.bin", True, False, False, False),
            ("case/off/data/period_0000.bin", False, True, False, False),
            ("case/off/data/period_0000.bin", False, False, True, False),
            ("case/off/data/period_0000.bin", False, False, False, True),
        ]
        for path, target_truth, nominal_target, known_servo, servo_truth in cases:
            with self.subTest(path=path, flags=(target_truth, nominal_target, known_servo, servo_truth)):
                with self.assertRaises(ValueError):
                    E2E.estimator_input_audit(
                        branch="S1",
                        input_paths=[path],
                        contains_target_truth=target_truth,
                        contains_nominal_target=nominal_target,
                        contains_known_servo_error=known_servo,
                        contains_servo_truth=servo_truth,
                    )

    def test_on_and_to_are_evaluation_only(self) -> None:
        roles = E2E.role_contract()
        self.assertEqual(roles["OFF"]["use"], "calibration_estimator_input")
        self.assertEqual(roles["ON"]["use"], "target_bearing_evaluation_only")
        self.assertEqual(roles["TO"]["use"], "causal_target_transfer_evaluation_only")
        self.assertTrue(roles["ON"]["target_bearing"])
        self.assertTrue(roles["TO"]["target_only"])

    def test_production_current_cfar_signature_is_stable_across_branches(self) -> None:
        settings = {
            "cfar": {"pf": 1.0e-6, "guard_cells": 4, "background_cells": 16},
            "csi": {"mode": "production_current", "pair_fusion": "(1,3),(2,4)"},
            "roi": {"name": "active_support_unmasked"},
        }
        signatures = [E2E.production_cfar_signature(settings) for _ in ("S0", "S1", "S1K", "Sassist")]
        self.assertEqual(len(set(signatures)), 1)

    def test_internal_core_quality_failure_is_not_reported_as_completed(self) -> None:
        self.assertEqual(E2E.pilot_status(True, []), "completed")
        self.assertEqual(
            E2E.pilot_status(False, [{"status": "completed"}]),
            "completed",
        )
        self.assertEqual(
            E2E.pilot_status(False, [{"status": "completed_with_internal_quality_failure"}]),
            "completed_with_failed_core",
        )


if __name__ == "__main__":
    unittest.main()
