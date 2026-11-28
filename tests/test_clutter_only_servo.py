from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "estimate_clutter_only_servo.py"
SPEC = importlib.util.spec_from_file_location("estimate_clutter_only_servo", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
SERVO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVO)


def _context() -> dict[str, object]:
    return {
        "reported_channel_positions_m": [
            [-0.085, 0.0, 0.085],
            [0.085, 0.0, 0.085],
            [-0.085, 0.0, -0.085],
            [0.085, 0.0, -0.085],
        ],
        "reported_platform": {
            "speed_mps": 60.0,
            "height_m": 6000.0,
            "heading_deg": 0.0,
            "squint_side": 1,
        },
        "reported_scan": {
            "beam_count": 7,
            "scan_min_deg": -6.0,
            "scan_step_deg": 2.0,
            "beam_width_deg": 4.0,
            "beam_index_base": 1,
        },
        "waveform": {
            "fc_ghz": 16.0,
            "prf_hz": 1300.0,
            "fs_mhz": 60.0,
        },
        "range_processing": {
            "sample_delay_us": 1.0,
        },
        "representative_range_sample": 3500.0,
        "source_id": "synthetic_target_free_c_plus_n",
    }


def _synthetic_features(delta_deg: float, inconsistent_ridge: bool = False) -> dict[str, object]:
    context = _context()
    rows: list[dict[str, object]] = []
    pair_definitions = SERVO.PAIR_DEFINITIONS
    for beam_id, theta_deg in enumerate(np.arange(-6.0, 8.0, 2.0)):
        for pair_id, i, j in pair_definitions:
            phase_theta = theta_deg + delta_deg
            ridge_delta = -delta_deg if inconsistent_ridge else delta_deg
            rows.append({
                "theta_deg": float(theta_deg),
                "slant_range_m": 8750.0,
                "beam_id": beam_id,
                "pair_id": pair_id,
                "clutter_phase_rad": SERVO.expected_clutter_phase_rad(
                    phase_theta, 8750.0, context, i, j
                ),
                "doppler_ridge_hz": SERVO.expected_doppler_ridge_hz(
                    theta_deg + ridge_delta, context
                ),
                "phase_slope_rad_per_rad": SERVO.expected_phase_slope_rad_per_rad(
                    phase_theta, 8750.0, context, i, j
                ),
                "power_db": float(20.0 * np.log10(max(
                    np.cos(0.5 * np.pi * np.clip(phase_theta / 4.0, -0.999, 0.999)),
                    1.0e-3,
                ))),
                "coherence": 0.95,
                "source_id": "synthetic_target_free_c_plus_n",
            })
    return {
        "schema": "clutter_only_servo_features_v1",
        "rows": rows,
        "reported_context": context,
        "causal_data_sources": list(SERVO.CAUSAL_DATA_SOURCES),
        "contains_target_truth": False,
        "contains_on_off_difference": False,
    }


class ClutterOnlyServoTests(unittest.TestCase):
    def test_target_free_feature_extraction_uses_only_allowed_inputs(self) -> None:
        rng = np.random.default_rng(101)
        packets = []
        for packet_index in range(21):
            base = rng.normal(size=64) + 1j * rng.normal(size=64)
            channels = np.column_stack([
                base,
                base * np.exp(1j * 0.03),
                base * np.exp(1j * -0.02),
                base * np.exp(1j * 0.05),
            ])
            packets.append({
                "packet_index": packet_index,
                "prt_counter": packet_index,
                "beam_id": packet_index // 3,
                "theta_cmd_deg": -6.0 + 2.0 * (packet_index // 3),
                "channels": channels,
            })

        features = SERVO.extract_clutter_features(packets, _context())
        self.assertEqual(
            set(features["causal_data_sources"]),
            {
                "six_pair_clutter_phase",
                "doppler_ridge_displacement",
                "p38_phase_slope",
                "multi_beam_power_secondary",
                "reported_angle_geometry_platform",
            },
        )
        self.assertFalse(features["contains_target_truth"])
        self.assertFalse(features["contains_on_off_difference"])
        self.assertEqual(len(features["rows"]), 7 * len(SERVO.PAIR_DEFINITIONS))

    def test_grid_fit_is_deterministic_and_reports_model_mismatch(self) -> None:
        features = _synthetic_features(0.22)
        first = SERVO.estimate_clutter_only_servo(features, {"seed": 101})
        second = SERVO.estimate_clutter_only_servo(features, {"seed": 101})
        self.assertEqual(first, second)
        self.assertIn(first["status"], {"VALID", "FALLBACK_MODEL_MISMATCH", "FALLBACK_UNIDENTIFIABLE"})
        self.assertEqual(first["status"], "VALID")
        self.assertAlmostEqual(float(first["estimate_deg"]), 0.22, delta=0.03)

    def test_inconsistent_ridge_and_phase_blocks_correction(self) -> None:
        features = _synthetic_features(0.22, inconsistent_ridge=True)
        result = SERVO.estimate_clutter_only_servo(features, {"seed": 101})
        self.assertEqual(result["status"], "FALLBACK_MODEL_MISMATCH")
        self.assertEqual(result["action"], "KEEP_CURRENT")
        self.assertTrue(result["model_mismatch"])

    def test_target_related_estimator_inputs_are_rejected(self) -> None:
        context = _context()
        context["targets"] = []
        with self.assertRaises(ValueError):
            SERVO.extract_clutter_features([], context)


if __name__ == "__main__":
    unittest.main()
