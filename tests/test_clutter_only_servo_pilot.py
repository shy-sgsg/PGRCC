from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_clutter_only_servo_pilot.py"
SPEC = importlib.util.spec_from_file_location("run_clutter_only_servo_pilot", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
PILOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PILOT)


class ClutterOnlyServoPilotTests(unittest.TestCase):
    def test_json_safe_preserves_boolean_configuration_values(self) -> None:
        self.assertIs(PILOT._json_safe(True), True)
        self.assertIs(PILOT._json_safe(False), False)

    def test_skip_core_template_is_explicitly_compact(self) -> None:
        template = json.loads(PILOT.TEMPLATE.read_text(encoding="utf-8"))
        config = PILOT._prepare_template(template, compact_input=True)
        self.assertEqual(config["waveform"]["pulse_len"], 4096)
        self.assertEqual(config["waveform"]["pulse_num"], 8)
        self.assertEqual(config["range_processing"]["range_fft_len"], 4096)
        self.assertEqual(config["range_processing"]["range_crop_len"], 4096)
        self.assertFalse(config["truth_output"])

    def test_branch_contract_separates_estimation_and_evaluation_sources(self) -> None:
        contract = PILOT.branch_contract()
        self.assertEqual(
            set(contract),
            {"S0", "S1", "S1K", "Sassist"},
        )
        self.assertEqual(contract["S1"]["estimator_input_role"], "OFF_C_PLUS_N_TARGET_FREE_ONLY")
        self.assertFalse(contract["S1"]["on_off_difference_used"])
        self.assertFalse(contract["S1"]["target_truth_used"])
        self.assertEqual(contract["Sassist"]["calibration_mode"], "target_assisted")
        self.assertTrue(contract["Sassist"]["on_off_difference_used"])
        self.assertNotEqual(contract["S0"]["estimator_input_role"], "OFF_C_PLUS_N_TARGET_FREE_ONLY")

    def test_estimator_audit_rejects_target_data_for_clutter_only_branch(self) -> None:
        audit = PILOT.estimator_input_audit(
            branch="S1",
            input_paths=["case/off/data/stage2_statistical_newprotocol_period_0000.bin"],
            contains_on_off_difference=False,
            contains_target_truth=False,
            contains_known_error=False,
            contains_servo_truth=False,
        )
        self.assertTrue(audit["target_free_c_plus_n_only"])
        self.assertFalse(audit["contains_on_off_difference"])
        self.assertFalse(audit["contains_target_truth"])
        self.assertFalse(audit["contains_known_error"])
        self.assertFalse(audit["contains_servo_truth"])
        with self.assertRaises(ValueError):
            PILOT.estimator_input_audit(
                branch="S1",
                input_paths=["case/on/data/stage2_statistical_newprotocol_period_0000.bin"],
                contains_on_off_difference=True,
                contains_target_truth=False,
                contains_known_error=False,
                contains_servo_truth=False,
            )

    def test_evaluation_placeholders_are_null_when_not_run(self) -> None:
        row = PILOT.evaluation_only_fields(evaluated=False)
        self.assertEqual(row["evaluation_scope"], "not_evaluated")
        self.assertIsNone(row["target_pd_evaluation_only"])
        self.assertIsNone(row["false_hit_fraction_evaluation_only"])
        self.assertIsNone(row["causal_target_transfer_db_evaluation_only"])


if __name__ == "__main__":
    unittest.main()
