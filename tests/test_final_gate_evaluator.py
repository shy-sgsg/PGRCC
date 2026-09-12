import unittest

from scripts.evaluate_physics_ai_final_gate import (
    J5,
    J6,
    J7,
    classify_final_gate,
    evaluate_method,
)


GUARDRAILS = {
    "causal_transfer_floor_db": -0.25,
    "paired_pd_no_loss": True,
    "target_off_pfa_delta_max": 0.0,
    "target_off_false_clusters_delta_max": 0.0,
}


CONFIG = {
    "target_safe_oracle": {
        "safe_oracle_headroom_min_db": 0.0,
        "deterministic_solved_min_gain_db": 0.0,
    }
}


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
                                 "J7_Deterministic_Safe_Expert_Selector",
                                 GUARDRAILS)
        self.assertFalse(result["method_passes_physics_gate"])
        self.assertFalse(result["causal_transfer_ok"])
        self.assertFalse(result["pfa_acceptable"])
        self.assertFalse(result["false_clusters_acceptable"])

    def test_j7_safety_alone_does_not_reopen_without_oracle_headroom(self) -> None:
        methods = [{
            "method": J7,
            "method_passes_physics_gate": True,
            "L_causal_mean_dB": 0.0,
        }]
        result = classify_final_gate(
            methods, True,
            {"status": "proven", "safe_oracle_headroom_db": 0.0}, CONFIG)
        self.assertEqual(result["decision"], "FINAL_NO_GO_AI")

    def test_positive_oracle_headroom_reopens_when_deterministic_fails(self) -> None:
        methods = [{
            "method": J6,
            "method_passes_physics_gate": False,
            "L_causal_mean_dB": 0.5,
        }]
        result = classify_final_gate(
            methods, True,
            {"status": "proven", "safe_oracle_headroom_db": 0.5}, CONFIG)
        self.assertEqual(result["decision"], "REOPEN_AI_ROUTER")

    def test_deterministic_positive_safe_recovery_is_no_go_ai(self) -> None:
        methods = [{
            "method": J5,
            "method_passes_physics_gate": True,
            "L_causal_mean_dB": 0.5,
        }]
        result = classify_final_gate(
            methods, True,
            {"status": "proven", "safe_oracle_headroom_db": 0.5}, CONFIG)
        self.assertEqual(result["decision"], "NO_GO_AI")


if __name__ == "__main__":
    unittest.main()
