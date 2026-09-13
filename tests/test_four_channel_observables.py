from __future__ import annotations

import importlib.util
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analyze_four_channel_observables.py"
SPEC = importlib.util.spec_from_file_location("four_channel_observables", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
OBS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OBS)


class FourChannelObservableTests(unittest.TestCase):
    def test_all_pairs_coherence_and_closure_phase(self) -> None:
        phases = np.array([0.0, 0.5, 0.2, 0.7], dtype=np.float64)
        samples = np.exp(1j * phases)[None, :].repeat(32, axis=0)
        result = OBS.compute_pair_observables(samples)

        self.assertEqual(list(result["pairs"]), ["C13", "C24", "C12", "C14", "C23", "C34"])
        self.assertAlmostEqual(result["pairs"]["C12"]["phase_rad"], -0.5, places=6)
        self.assertAlmostEqual(result["pairs"]["C23"]["phase_rad"], 0.3, places=6)
        self.assertAlmostEqual(result["closure_phase_C12_C23_minus_C13_rad"], 0.0, places=6)
        for pair in result["pairs"].values():
            self.assertGreaterEqual(pair["coherence"], 0.0)
            self.assertLessEqual(pair["coherence"], 1.0)
            self.assertEqual(pair["effective_sample_count"], 32)

    def test_multi_beam_fit_recovers_baseline_error(self) -> None:
        fc_hz = 16.0e9
        wavelength = OBS.C / fc_hz
        delta_d = 0.01
        intercept = 0.25
        theta_deg = np.array([-45.0, -15.0, 15.0, 45.0, 60.0])
        phase = 2.0 * math.pi * delta_d * np.sin(np.deg2rad(theta_deg)) / wavelength + intercept
        fit = OBS.fit_angle_phase_model(theta_deg, phase, np.ones(theta_deg.shape))

        self.assertEqual(fit["fit_status"], "fit")
        self.assertEqual(fit["n_beams"], 5)
        self.assertLess(fit["rmse_rad"], 1.0e-9)
        self.assertAlmostEqual(OBS.estimate_baseline_error(fit, fc_hz), delta_d, places=9)

    def test_single_angle_is_explicitly_underdetermined(self) -> None:
        fit = OBS.fit_angle_phase_model(np.array([30.0]), np.array([0.5]), np.array([1.0]))
        self.assertEqual(fit["fit_status"], "underdetermined")
        self.assertIsNone(fit["slope_rad_per_sin_theta"])

    def test_truncated_packet_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "truncated.bin"
            path.write_bytes(b"\0" * 32)
            with self.assertRaises(ValueError):
                list(OBS.iter_raw_packets(path, pulse_len=2, channel_count=4, iq_data_type="float32"))


if __name__ == "__main__":
    unittest.main()
