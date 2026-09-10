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
import run_joint_physics_selective_v2 as selective_v2  # noqa: E402
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
            "input": {"max_supported_delay_ns": 100.0},
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

        out_of_window = summary_observables(delay_ns=1000.0)
        state = joint.classify_state(out_of_window, gate)
        self.assertEqual(state["state"], joint.UNCERTAIN)
        self.assertIn("delay_outside_unambiguous_raw_window", state["reasons"])

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

    def test_xml_frequency_units_are_converted_at_the_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xml = Path(tmp) / "config.xml"
            xml.write_text("<root><fs>60</fs></root>", encoding="utf-8")
            self.assertEqual(joint.xml_frequency_hz(xml, "fs", 60.0e6), 60.0e6)

    def test_selective_delay_and_phase_gates_are_independent(self) -> None:
        null = [summary_observables(delay_ns=0.01 * index,
                                    phase_slope=0.001 * index)["summary"]
                for index in range(1, 8)]
        gate = joint.calibrate_null_gate(null, "unit-selective-null")
        bad_phase = summary_observables(
            delay_ns=2.0, phase_slope=0.2, phase_confidence=0.0)
        state = joint.classify_selective_state(bad_phase, gate, "P1")
        self.assertFalse(state["global_veto"])
        self.assertTrue(state["delay_active"])
        self.assertFalse(state["phase_active"])
        self.assertEqual(state["delay_state"], joint.ACTIVE)
        self.assertEqual(state["phase_state"], joint.UNCERTAIN)
        self.assertEqual(
            joint.selective_correction_decision(
                "J5_Selective_Physics_Calibration", bad_phase, gate)["branch"],
            "D1P0")

        bad_delay = summary_observables(
            delay_ns=2.0, phase_slope=0.2, delay_confidence=0.0)
        state = joint.classify_selective_state(bad_delay, gate, "P1")
        self.assertFalse(state["global_veto"])
        self.assertFalse(state["delay_active"])
        self.assertTrue(state["phase_active"])
        self.assertEqual(
            joint.selective_correction_decision(
                "J5_Selective_Physics_Calibration", bad_delay, gate)["branch"],
            "D0P1")

        no_mismatch = summary_observables()
        self.assertEqual(
            joint.selective_correction_decision(
                "J5_Selective_Physics_Calibration", no_mismatch, gate)["branch"],
            "D0P0")
        veto = summary_observables(coherence=0.0, delay_ns=2.0, phase_slope=0.2)
        veto_state = joint.classify_selective_state(veto, gate, "P1")
        self.assertTrue(veto_state["global_veto"])
        self.assertFalse(veto_state["delay_active"])
        self.assertFalse(veto_state["phase_active"])

    def test_j6_joint_surface_identity_and_mixed_surface(self) -> None:
        rng = np.random.default_rng(77)
        pulses, samples = 32, 128
        fs_hz = 60.0e6
        frequency = np.fft.fftfreq(samples, d=1.0 / fs_hz)
        positive = frequency > 0.0
        spectrum1 = (rng.normal(size=(pulses, samples)) +
                     1j * rng.normal(size=(pulses, samples)))
        identity = joint.estimate_joint_phase_surface_from_spectra(
            spectrum1, spectrum1, frequency, positive, 1300.0)
        self.assertAlmostEqual(identity["tau_ns"], 0.0, places=8)
        self.assertAlmostEqual(identity["beta1_deg_per_pulse"], 0.0, places=8)
        self.assertAlmostEqual(identity["beta2_deg_per_pulse2"], 0.0, places=8)
        self.assertGreater(identity["residual_coherence_global"], 0.999)

        pulse = np.arange(pulses, dtype=np.float64)[:, None]
        model = (0.4 + 2.0 * np.pi * 6.0e-9 * frequency[None, :] +
                 np.deg2rad(0.12) * pulse + np.deg2rad(0.003) * pulse ** 2)
        spectrum2 = spectrum1 * np.exp(-1j * model)
        mixed = joint.estimate_joint_phase_surface_from_spectra(
            spectrum1, spectrum2, frequency, positive, 1300.0)
        self.assertAlmostEqual(mixed["tau_ns"], 6.0, places=5)
        self.assertAlmostEqual(mixed["beta1_deg_per_pulse"], 0.12, places=5)
        self.assertAlmostEqual(mixed["beta2_deg_per_pulse2"], 0.003, places=5)
        self.assertLess(mixed["rmse_rad"], 1.0e-8)
        self.assertGreater(mixed["residual_coherence_global"], 0.999)

    def test_v2_design_is_balanced_and_bootstrap_is_scene_blocked(self) -> None:
        specs = selective_v2.design_specs("formal")
        self.assertEqual(len(specs), 128)
        test_specs = [item for item in specs if item["split"] == "test"]
        self.assertEqual(len(test_specs), 64)
        for family in selective_v2.FAMILIES:
            self.assertEqual(sum(item["label"] == family for item in test_specs), 8)
        self.assertEqual(
            {item["scan_min_deg"] for item in test_specs},
            set(selective_v2.ANGLE_GRID_DEG),
        )
        self.assertGreater(len({(item["texture_sigma"], item["rho"])
                                for item in test_specs}), 32)
        rows = [
            {"scene_id": "s0", "method": "J5", "status": "measured", "pfa": 0.1},
            {"scene_id": "s1", "method": "J5", "status": "measured", "pfa": 0.2},
            {"scene_id": "s2", "method": "J5", "status": "measured", "pfa": 0.3},
        ]
        summary = selective_v2.bootstrap_scene(rows, "pfa", "J5", iterations=100)
        self.assertEqual(summary["bootstrap_unit"], "scene")
        self.assertEqual(summary["scene_count"], 3)
        self.assertEqual(len(summary["bootstrap_ci95"]), 2)


if __name__ == "__main__":
    unittest.main()
