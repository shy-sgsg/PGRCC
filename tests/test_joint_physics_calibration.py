"""Formula and policy regressions for Physics-Adaptive Joint Calibration V1."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_joint_physics_calibration as joint  # noqa: E402
from run_joint_physics_formal_matrix import validate_clean_provenance  # noqa: E402
from estimate_channel_delay import estimate_from_raw_time_arrays  # noqa: E402
from estimate_temporal_phase import estimate_from_slow_time  # noqa: E402


def summary_observables(
    delay_ns: float = 0.0,
    phase_slope: float = 0.0,
    coherence: float = 1.0,
    delay_confidence: float = 1.0,
    phase_confidence: float = 1.0,
    delay_rmse: float = 0.01,
    phase_rmse: float = 0.01,
) -> dict:
    phase = {
        "slope_deg_per_pulse": phase_slope,
        "confidence": phase_confidence,
        "rmse_rad": phase_rmse,
    }
    return {
        "summary": {
            "delay": {
                "delta_tau_ns": delay_ns,
                "confidence": delay_confidence,
                "rmse_rad": delay_rmse,
            },
            "phase_p1": phase,
            "phase_p2": phase,
            "coherence": {"pulse_median": coherence},
        },
        "phase_p1_correction": np.zeros(8),
        "phase_p2_correction": np.zeros(8),
    }


class JointPhysicsCalibrationTests(unittest.TestCase):
    def test_delay_and_phase_signs_are_observable_only(self) -> None:
        rng = np.random.default_rng(2026)
        pulses, samples = 48, 256
        fs_hz = 60.0e6
        delay_sec = 8.0e-9
        channel1 = rng.normal(size=(pulses, samples)) + 1j * rng.normal(size=(pulses, samples))
        frequency = np.fft.fftfreq(samples, d=1.0 / fs_hz)
        channel2 = np.fft.ifft(
            np.fft.fft(channel1, axis=1) *
            np.exp(-1j * 2.0 * np.pi * frequency[None, :] * delay_sec), axis=1)
        delay = estimate_from_raw_time_arrays(channel1, channel2, fs_hz)
        self.assertAlmostEqual(
            delay["aggregate"]["D3_Huber_weighted_LS"]["delta_tau_ns"], 8.0, places=2)

        phase = 0.08 * np.arange(pulses, dtype=np.float64)
        spectrum1 = np.fft.fft(channel1, axis=1)
        spectrum2 = spectrum1 * np.exp(1j * phase[:, None])
        phase_fit = estimate_from_slow_time(
            spectrum1, spectrum2, frequency > 0.0, 1300.0)
        self.assertAlmostEqual(
            phase_fit["fits"]["P1_robust_constant_linear"]["slope_rad_per_pulse"],
            -0.08, places=3)

    def test_null_deadband_and_decorrelation_fallback(self) -> None:
        null = [summary_observables(delay_ns=0.01 * index,
                                    phase_slope=0.001 * index)["summary"]
                for index in range(1, 8)]
        gate = joint.calibrate_null_gate(null, "unit-null")
        inside = summary_observables(delay_ns=0.0, phase_slope=0.0)
        decision = joint.correction_decision("J3_D3_P1_joint", inside, gate)
        self.assertTrue(decision["fallback"])
        self.assertFalse(decision["apply_delay"])
        self.assertFalse(decision["apply_phase"])

        decorrelated = summary_observables(coherence=0.0)
        state = joint.classify_state(decorrelated, gate)
        self.assertEqual(state["state"], joint.DECORRELATED)
        decision = joint.correction_decision("J4_D3_P2_joint", decorrelated, gate)
        self.assertTrue(decision["fallback"])
        self.assertEqual(decision["fallback_reason"], "state_decorrelated")

    def test_phase_order_is_explicit_and_no_truth_is_needed(self) -> None:
        calls: list[str] = []

        def fake_delay(source, target, xml, delay, phase):
            calls.append("delay")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"delay")
            return {"kind": "delay"}

        def fake_phase(source, target, xml, trajectory):
            calls.append("phase")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"phase")
            return {"kind": "phase"}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.bin"
            source.write_bytes(b"source")
            xml = root / "case.xml"
            xml.write_text("<root/>", encoding="utf-8")
            with patch.object(joint, "inverse_channel_impairment_frequency_domain",
                              side_effect=fake_delay), \
                 patch.object(joint, "inverse_channel_phase_trajectory_frequency_domain",
                              side_effect=fake_phase):
                joint.apply_raw_correction(source, root / "delay_first.bin", xml,
                                            3.0, np.zeros(2), phase_first=False)
                self.assertEqual(calls, ["delay", "phase"])
                calls.clear()
                joint.apply_raw_correction(source, root / "phase_first.bin", xml,
                                            3.0, np.zeros(2), phase_first=True)
                self.assertEqual(calls, ["phase", "delay"])

    def test_target_bias_and_recovery_ratio_are_paired_metrics(self) -> None:
        from evaluate_global_fixed_pfa import recovery_metrics  # noqa: E402

        metrics = recovery_metrics(10.0, 16.0, 20.0)
        self.assertEqual(metrics["recoverable_headroom_db"], 10.0)
        self.assertEqual(metrics["deterministic_gain_db"], 6.0)
        self.assertAlmostEqual(metrics["recovery_ratio"], 0.6)
        undefined = recovery_metrics(10.0, 12.0, 9.0)
        self.assertIsNone(undefined["recovery_ratio"])

    def test_formal_provenance_rejects_dirty_source(self) -> None:
        validate_clean_provenance({"source_commit": "abc123", "worktree_dirty": False})
        with self.assertRaises(ValueError):
            validate_clean_provenance({"source_commit": "abc123", "worktree_dirty": True})


if __name__ == "__main__":
    unittest.main()
