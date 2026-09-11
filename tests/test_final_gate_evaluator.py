import unittest

from scripts.evaluate_physics_ai_final_gate import evaluate_method


class FinalGateEvaluatorTests(unittest.TestCase):
    def test_method_fails_causal_floor_and_background_regression(self) -> None:
        transfer = [
            {"method": "J0_Current", "L_causal_dB": "0", "L_target_only_dB": "0"},
            {"method": "J7_Deterministic_Safe_Expert_Selector",
             "L_causal_dB": "-0.5", "L_target_only_dB": "-0.1"},
        ]
        detection = [
            {"method": "J0_Current", "paired_causal_hit": "True", "snr_db": "10"},
            {"method": "J7_Deterministic_Safe_Expert_Selector",
             "paired_causal_hit": "True", "snr_db": "10"},
        ]
        cfar = [
            {"method": "J0_Current", "scene_id": "s", "role": "target_off",
             "pfa": "0.1", "false_clusters": "1"},
            {"method": "J7_Deterministic_Safe_Expert_Selector", "scene_id": "s",
             "role": "target_off", "pfa": "0.2", "false_clusters": "2"},
        ]
        result = evaluate_method(transfer, detection, cfar,
                                 "J7_Deterministic_Safe_Expert_Selector", -0.25)
        self.assertFalse(result["method_passes_physics_gate"])
        self.assertFalse(result["causal_transfer_ok"])
        self.assertFalse(result["pfa_acceptable"])
        self.assertFalse(result["false_clusters_acceptable"])


if __name__ == "__main__":
    unittest.main()
