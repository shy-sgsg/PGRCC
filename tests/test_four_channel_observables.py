from __future__ import annotations

import importlib.util
import math
import struct
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

    def test_exact_pair_predictor_uses_two_way_receive_geometry(self) -> None:
        positions = np.array(
            [[-0.1, 0.0, 0.0], [0.1, 0.0, 0.0],
             [-0.1, 0.0, 0.2], [0.1, 0.0, 0.2]],
            dtype=np.float64,
        )
        target = np.array([0.0, 100.0, 10.0], dtype=np.float64)
        platform = np.array([0.0, 0.0, 5.0], dtype=np.float64)
        path_left = OBS.exact_receive_channel_path_length(target, platform, positions[0])
        path_right = OBS.exact_receive_channel_path_length(target, platform, positions[1])
        self.assertGreater(path_left, 0.0)
        self.assertGreater(path_right, 0.0)
        phase = OBS.exact_pair_phase_rad(
            target, platform, positions, 0, 1, fc_hz=16.0e9, carrier_phase_sign=-1.0
        )
        expected = OBS._wrap_phase(
            -2.0 * math.pi * (path_left - path_right) / (OBS.C / 16.0e9)
        )
        self.assertAlmostEqual(phase, expected, places=12)

    def test_single_angle_is_explicitly_underdetermined(self) -> None:
        fit = OBS.fit_angle_phase_model(np.array([30.0]), np.array([0.5]), np.array([1.0]))
        self.assertEqual(fit["fit_status"], "underdetermined")
        self.assertIsNone(fit["slope_rad_per_sin_theta"])

    def test_pooled_beam_coherence_uses_pooled_channel_powers(self) -> None:
        accumulator = {
            (0, "C12"): {
                "theta_cmd_deg": 0.0,
                "sample_count": 2,
                "cross": 20.0 + 0.0j,
                "power_i": 101.0,
                "power_j": 101.0,
            }
        }
        rows = OBS._beam_rows_from_accumulator(
            accumulator, OBS.DEFAULT_CHANNEL_X_M
        )
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["coherence"], 20.0 / 101.0)

    def test_truncated_packet_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "truncated.bin"
            path.write_bytes(b"\0" * 32)
            with self.assertRaises(ValueError):
                list(OBS.iter_raw_packets(path, pulse_len=2, channel_count=4, iq_data_type="float32"))

    def test_unknown_only_six_pair_estimator_uses_nominal_model(self) -> None:
        pulse_len = 32
        fc_hz = 16.0e9
        wavelength = OBS.C / fc_hz
        nominal = np.array(
            [[-0.1, 0.0, 0.1], [0.1, 0.0, 0.1],
             [-0.1, 0.0, -0.1], [0.1, 0.0, -0.1]],
            dtype=np.float64,
        )
        delta_d = 0.0025
        truth = nominal.copy()
        truth[[1, 3], 0] += delta_d
        metadata = {
            "carrier_phase_sign": -1,
            "waveform": {"fs_mhz": 60.0},
            "platform": {"height_m": 0.0, "speed_mps": 60.0, "squint_side": 1},
            "scene": {"ground_z_m": 0.0},
            "simulation_geometry": {
                "local_x_axis": "north",
                "local_y_axis": "east",
                "platform_heading_source": "fixed_angle",
                "platform_heading_deg": 90.0,
                "beam_theta_offset_deg": 0.0,
                "range_geometry": "slant",
                "use_ground_range_for_position": False,
            },
        }
        angles = (-20.0, -10.0, 0.0, 10.0, 20.0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unknown_off.bin"
            with path.open("wb") as stream:
                for packet_index, theta in enumerate(angles):
                    header = bytearray(OBS.HEADER_BYTES)
                    packet_bytes = OBS.HEADER_BYTES + pulse_len * 4 * 2 * 4
                    struct.pack_into("<I", header, 9, packet_bytes)
                    struct.pack_into("<I", header, 20, packet_index)
                    struct.pack_into("<h", header, 218, int(round(theta * 100.0)))
                    los = OBS._nominal_los_unit(theta, 10.0, metadata)
                    channel_phase = -metadata["carrier_phase_sign"] * 2.0 * math.pi * (
                        truth @ los
                    ) / wavelength
                    values = np.empty((pulse_len, 4, 2), dtype="<f4")
                    for channel in range(4):
                        value = complex(math.cos(channel_phase[channel]),
                                        math.sin(channel_phase[channel]))
                        values[:, channel, 0] = value.real
                        values[:, channel, 1] = value.imag
                    stream.write(header)
                    stream.write(values.tobytes())
            result = OBS.estimate_unknown_baseline(
                path,
                pulse_len=pulse_len,
                channel_count=4,
                iq_data_type="float32",
                fc_hz=fc_hz,
                nominal_channel_positions_m=nominal,
                metadata=metadata,
                pair_mode="six_pair",
                range_block_size=pulse_len,
            )
        self.assertEqual(result["fit_status"], "fit")
        self.assertAlmostEqual(result["estimated_baseline_error_m"], delta_d, places=5)
        self.assertEqual(set(result["pair_fits"]), {item[0] for item in OBS.PAIR_DEFINITIONS})
        self.assertTrue(result["model"]["uses_ideal_reference"] is False)

    def test_unknown_only_estimator_falls_back_without_angle_span(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unknown_off.bin"
            pulse_len = 8
            packet_bytes = OBS.HEADER_BYTES + pulse_len * 4 * 2 * 4
            header = bytearray(OBS.HEADER_BYTES)
            struct.pack_into("<I", header, 9, packet_bytes)
            struct.pack_into("<h", header, 218, 0)
            values = np.ones((pulse_len, 4, 2), dtype="<f4")
            with path.open("wb") as stream:
                stream.write(header)
                stream.write(values.tobytes())
            result = OBS.estimate_unknown_baseline(
                path,
                pulse_len=pulse_len,
                channel_count=4,
                iq_data_type="float32",
                fc_hz=16.0e9,
                nominal_channel_positions_m=np.array(
                    [[-0.1, 0.0, 0.1], [0.1, 0.0, 0.1],
                     [-0.1, 0.0, -0.1], [0.1, 0.0, -0.1]]
                ),
                metadata={
                    "waveform": {"fs_mhz": 60.0},
                    "platform": {"height_m": 0.0, "squint_side": 1},
                    "simulation_geometry": {
                        "local_x_axis": "north",
                        "local_y_axis": "east",
                        "platform_heading_source": "fixed_angle",
                        "platform_heading_deg": 90.0,
                        "range_geometry": "slant",
                        "use_ground_range_for_position": False,
                    },
                },
                range_block_size=pulse_len,
            )
        self.assertEqual(result["fit_status"], "fallback_unidentifiable")
        self.assertIsNone(result["estimated_baseline_error_m"])


if __name__ == "__main__":
    unittest.main()
