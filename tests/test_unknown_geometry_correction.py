from __future__ import annotations

import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "unknown_geometry_correction.py"
SPEC = importlib.util.spec_from_file_location("unknown_geometry_correction", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
CORRECTION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORRECTION)


class UnknownGeometryCorrectionTests(unittest.TestCase):
    def test_correction_uses_nominal_los_and_preserves_other_channels(self) -> None:
        pulse_len = 4
        channel_count = 4
        packet_bytes = 256 + pulse_len * channel_count * 2 * 4
        theta_deg = 10.0
        metadata = {
            "waveform": {
                "fc_hz": 16.0e9,
                "fs_hz": 60.0e6,
                "sample_delay_sec": 50.0e-6,
                "pulse_len": pulse_len,
                "new_protocol_channel_count": channel_count,
                "iq_data_type": "float32",
            },
            "platform": {"height_m": 6000.0, "squint_side": 1},
            "simulation_geometry": {
                "local_x_axis": "north",
                "local_y_axis": "east",
                "platform_heading_source": "fixed_angle",
                "platform_heading_deg": 90.0,
                "beam_theta_offset_deg": 0.0,
                "range_geometry": "algorithm",
                "use_ground_range_for_position": True,
                "squint_side": 1,
            },
            "scene": {"ground_z_m": 0.0},
            "carrier_phase_sign": -1,
        }
        delta_m = 0.0025
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.bin"
            destination = Path(tmp) / "corrected.bin"
            header = bytearray(256)
            struct.pack_into("<I", header, 9, packet_bytes)
            struct.pack_into("<h", header, 218, int(round(theta_deg * 100.0)))
            channels = np.ones((pulse_len, channel_count), dtype=np.complex64)
            phases = np.asarray(
                [
                    CORRECTION.geometry_phase_error_rad(
                        theta_deg, float(sample), metadata, delta_m
                    )
                    for sample in range(pulse_len)
                ]
            )
            channels[:, 1] *= np.exp(1j * phases)
            channels[:, 3] *= np.exp(1j * phases)
            payload = np.empty((pulse_len, channel_count, 2), dtype="<f4")
            payload[..., 0] = channels.real
            payload[..., 1] = channels.imag
            source.write_bytes(bytes(header) + payload.tobytes())

            result = CORRECTION.apply_unknown_geometry_correction(
                source, destination, metadata, delta_m
            )
            self.assertEqual(result["packet_count"], 1)
            raw = destination.read_bytes()
            corrected = np.frombuffer(raw[256:], dtype="<f4").reshape(
                pulse_len, channel_count, 2
            )
            values = corrected[..., 0] + 1j * corrected[..., 1]
            np.testing.assert_allclose(values[:, 0], 1.0 + 0.0j, atol=1.0e-6)
            np.testing.assert_allclose(values[:, 2], 1.0 + 0.0j, atol=1.0e-6)
            np.testing.assert_allclose(values[:, 1], 1.0 + 0.0j, atol=1.0e-6)
            np.testing.assert_allclose(values[:, 3], 1.0 + 0.0j, atol=1.0e-6)

    def test_invalid_delta_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CORRECTION.geometry_phase_error_rad(
                0.0,
                1.0,
                {"waveform": {"fc_hz": 16.0e9, "fs_hz": 60.0e6}},
                float("nan"),
            )

    def test_no_correction_deadband_falls_back_to_current(self) -> None:
        decision = CORRECTION.decide_unknown_geometry_correction(
            estimated_delta_m=0.00016,
            uncertainty_m=0.00005,
            deadband_m=0.00020,
        )
        self.assertEqual(decision["status"], "NO_CORRECTION_NEEDED")
        self.assertEqual(decision["action"], "fallback_current")
        self.assertEqual(decision["apply_delta_m"], 0.0)

    def test_correction_decision_applies_only_above_threshold(self) -> None:
        decision = CORRECTION.decide_unknown_geometry_correction(
            estimated_delta_m=-0.0025,
            uncertainty_m=0.0002,
            deadband_m=0.0002,
        )
        self.assertEqual(decision["status"], "APPLY_ESTIMATED_CORRECTION")
        self.assertEqual(decision["action"], "apply_estimate")
        self.assertAlmostEqual(decision["apply_delta_m"], -0.0025)

    def test_unidentifiable_estimate_falls_back(self) -> None:
        decision = CORRECTION.decide_unknown_geometry_correction(
            estimated_delta_m=None,
            uncertainty_m=0.0002,
            deadband_m=0.0002,
        )
        self.assertEqual(decision["status"], "FALLBACK_UNIDENTIFIABLE")
        self.assertEqual(decision["action"], "fallback_current")

    def test_deadband_is_derived_from_zero_sweep_floor_and_symmetric_sensitivity(self) -> None:
        summary = CORRECTION.derive_deadband_from_sweep(
            [
                {"truth_delta_m": 0.0, "six_estimate_m": 0.00015},
                {"truth_delta_m": 0.0, "six_estimate_m": 0.00016},
                {"truth_delta_m": 0.0025, "six_estimate_m": 0.00265},
                {"truth_delta_m": -0.0025, "six_estimate_m": -0.00235},
                {"truth_delta_m": "bad", "six_estimate_m": 0.0},
            ]
        )
        self.assertEqual(summary["zero_level_case_count"], 2)
        self.assertAlmostEqual(summary["deadband_m"], 0.00016)
        self.assertAlmostEqual(summary["symmetric_sensitivity_m_per_m"], 1.0)
        self.assertTrue(summary["uses_truth"])
        self.assertTrue(summary["evaluator_only"])


if __name__ == "__main__":
    unittest.main()
