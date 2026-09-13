from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_unknown_system_error_pilot.py"
SPEC = importlib.util.spec_from_file_location("unknown_system_error_pilot", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
PILOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PILOT)


class UnknownSystemErrorPilotTests(unittest.TestCase):
    def test_estimator_rejects_truth_paths(self) -> None:
        with self.assertRaises(ValueError):
            PILOT.validate_estimator_inputs(["outputs/truth/truth_pulse.csv"])
        PILOT.validate_estimator_inputs(["outputs/raw/unknown_off.bin"])

    def test_required_condition_set_is_explicit(self) -> None:
        PILOT.validate_condition_set(PILOT.REQUIRED_CONDITIONS)
        with self.assertRaises(ValueError):
            PILOT.validate_condition_set(["ideal_on", "current_unknown"])

    def test_zero_recoverable_space_has_null_ratio(self) -> None:
        result = PILOT.compute_recovery_metrics(1.0, 1.0, 0.5)
        self.assertIsNone(result["recovery_ratio"])

    def test_lower_is_better_metrics_are_sign_normalized(self) -> None:
        result = PILOT.compute_recovery_metrics(
            current_value=1.0,
            known_value=0.2,
            estimated_value=0.4,
            metric_direction="lower_is_better",
        )
        self.assertAlmostEqual(result["recoverable_space_known_minus_current"], 0.8)
        self.assertAlmostEqual(result["actual_recovered_estimated_minus_current"], 0.6)
        self.assertAlmostEqual(result["recovery_ratio"], 0.75)


if __name__ == "__main__":
    unittest.main()
