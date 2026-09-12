import unittest

from scripts.safe_expert_selector import (
    ComplexitySelectorThresholds,
    CURRENT,
    J5,
    J6,
    SELECTOR_PROFILES,
    select_complexity_aware_expert,
    select_complexity_aware_expert_with_reason,
    select_safe_expert,
    select_safe_expert_with_reason,
)


def good_features() -> dict[str, float]:
    return {
        "joint_confidence": 0.85,
        "joint_rmse_rad": 0.30,
        "residual_coherence": 0.95,
        "tau_ns": 0.0,
        "beta1_deg_per_pulse": 0.0,
        "beta2_deg_per_pulse2": 0.0,
        "d3_j6_delay_disagreement_ns": 0.05,
        "p1_j6_phase_disagreement_deg_per_pulse": 0.02,
        "raw_coherence": 0.90,
        "support_fraction": 0.50,
        "delay_confidence": 0.0,
        "phase_confidence": 0.0,
        "phase_signal": 0.0,
        "delay_signal": 0.0,
        "delay_extrapolation_ratio": 0.01,
        "abs_beta2_deg_per_pulse2": 0.0,
        "joint_vs_p1_residual_coherence_gain": 0.0,
    }


class SafeExpertSelectorTests(unittest.TestCase):
    def test_safe_expert_zero_identity(self) -> None:
        self.assertEqual(select_safe_expert(good_features(),
                                            SELECTOR_PROFILES["balanced"]),
                         CURRENT)

    def test_safe_expert_decorrelation_fallback(self) -> None:
        features = good_features()
        features.update({
            "residual_coherence": 0.40,
            "delay_confidence": 0.90,
            "delay_signal": 2.0,
        })
        method, reason = select_safe_expert_with_reason(
            features, SELECTOR_PROFILES["balanced"])
        self.assertEqual(method, CURRENT)
        self.assertEqual(reason, "decorrelation_veto_residual_coherence")

    def test_safe_expert_high_confidence_delay(self) -> None:
        features = good_features()
        features.update({"delay_confidence": 0.50, "delay_signal": 1.0})
        self.assertEqual(select_safe_expert(features,
                                             SELECTOR_PROFILES["balanced"]), J5)

    def test_safe_expert_phase_or_mixed_uses_j6(self) -> None:
        features = good_features()
        features.update({"phase_confidence": 0.50, "phase_signal": 0.20})
        self.assertEqual(select_safe_expert(features,
                                             SELECTOR_PROFILES["balanced"]), J6)

    def test_j71_linear_phase_prefers_j5(self) -> None:
        features = good_features()
        features.update({"phase_confidence": 0.50, "phase_signal": 0.20,
                         "abs_beta2_deg_per_pulse2": 0.001,
                         "joint_vs_p1_residual_coherence_gain": 0.30})
        thresholds = ComplexitySelectorThresholds(0.005, 0.02)
        self.assertEqual(select_complexity_aware_expert(
            features, SELECTOR_PROFILES["balanced"], thresholds), J5)

    def test_j71_nonlinear_surface_requires_both_evidence_fields(self) -> None:
        features = good_features()
        features.update({"phase_confidence": 0.50, "phase_signal": 0.20,
                         "abs_beta2_deg_per_pulse2": 0.01,
                         "joint_vs_p1_residual_coherence_gain": 0.05})
        thresholds = ComplexitySelectorThresholds(0.005, 0.02)
        method, reason = select_complexity_aware_expert_with_reason(
            features, SELECTOR_PROFILES["balanced"], thresholds)
        self.assertEqual(method, J6)
        self.assertIn("nonlinear", reason)

    def test_j71_insufficient_joint_gain_does_not_select_j6(self) -> None:
        features = good_features()
        features.update({"phase_confidence": 0.50, "phase_signal": 0.20,
                         "abs_beta2_deg_per_pulse2": 0.01,
                         "joint_vs_p1_residual_coherence_gain": 0.01})
        thresholds = ComplexitySelectorThresholds(0.005, 0.02)
        self.assertEqual(select_complexity_aware_expert(
            features, SELECTOR_PROFILES["balanced"], thresholds), J5)


if __name__ == "__main__":
    unittest.main()
