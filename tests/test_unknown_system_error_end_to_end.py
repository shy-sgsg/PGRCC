from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_unknown_system_error_end_to_end.py"
SPEC = importlib.util.spec_from_file_location("unknown_system_error_end_to_end", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
E2E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E2E)


class UnknownSystemErrorEndToEndTests(unittest.TestCase):
    def test_production_condition_contract_separates_interfaces(self) -> None:
        contract = E2E.condition_contract()
        self.assertEqual(
            contract["B0_Current"]["interface"],
            "four_channel_protocol_pair_fusion_to_two_channel_CSI",
        )
        self.assertEqual(
            contract["B2_Uncalibrated_4ch_STAP"]["interface"],
            "four_channel_offline_conventional_STAP_reference",
        )
        self.assertFalse(contract["B2_Uncalibrated_4ch_STAP"]["production_cuda"])
        self.assertTrue(contract["B3_Blind_4ch_STAP"]["blind_estimated"])
        self.assertEqual(contract["B3K_Known_4ch_STAP"]["correction_source"], "known_error_upper_bound")

    def test_static_target_velocity_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            E2E.validate_moving_velocities([(0.0, 0.0)])
        self.assertEqual(
            E2E.validate_moving_velocities([(-3.0, 2.0), (0.0, 4.0)]),
            [(-3.0, 2.0), (0.0, 4.0)],
        )

    def test_recovery_metrics_use_known_minus_current_direction(self) -> None:
        result = E2E.recovery_metrics(
            current_value=2.0,
            known_value=0.5,
            estimated_value=0.8,
            metric_direction="higher_is_better",
        )
        self.assertAlmostEqual(result["recoverable_space_known_minus_current"], -1.5)
        self.assertAlmostEqual(result["actual_recovered_estimated_minus_current"], -1.2)
        self.assertAlmostEqual(result["recovery_ratio"], 0.8)

    def test_target_only_is_an_evaluation_transfer_condition(self) -> None:
        conditions = E2E.target_transfer_conditions()
        self.assertEqual(conditions, ("target_only_current", "target_only_blind", "target_only_known"))
        self.assertTrue(all("production" not in item for item in conditions))

    def test_timing_prefers_end_to_end_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "timing_metrics.csv").write_text(
                "scope_name,elapsed_ms\nconfig_load,1\ngmti_processing,556\nmain_total,833\n",
                encoding="utf-8",
            )
            timing = E2E._read_first_timing(root)
        self.assertEqual(timing["selected_scope"], "main_total")
        self.assertEqual(timing["runtime_total_ms"], 833.0)
        self.assertEqual(timing["gmti_processing_ms"], 556.0)


if __name__ == "__main__":
    unittest.main()
