from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "finite_range_geometry_model.py"
SPEC = importlib.util.spec_from_file_location("finite_range_geometry_model", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
MODEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODEL)
ANALYZER_SCRIPT = ROOT / "scripts" / "analyze_four_channel_observables.py"
ANALYZER_SPEC = importlib.util.spec_from_file_location(
    "analyze_four_channel_observables_for_geometry_contract",
    ANALYZER_SCRIPT,
)
if ANALYZER_SPEC is None or ANALYZER_SPEC.loader is None:
    raise RuntimeError(f"cannot load {ANALYZER_SCRIPT}")
ANALYZER = importlib.util.module_from_spec(ANALYZER_SPEC)
ANALYZER_SPEC.loader.exec_module(ANALYZER)


CHANNELS = np.asarray(
    [
        [-0.085, 0.0, 0.085],
        [0.085, 0.0, 0.085],
        [-0.085, 0.0, -0.085],
        [0.085, 0.0, -0.085],
    ],
    dtype=np.float64,
)
METADATA = {
    "platform": {"height_m": 6000.0, "squint_side": 1},
    "simulation_geometry": {
        "local_x_axis": "north",
        "local_y_axis": "east",
        "platform_heading_deg": 90.0,
        "range_geometry": "algorithm",
        "use_ground_range_for_position": True,
        "squint_side": 1,
    },
    "scene": {"ground_z_m": 0.0},
    "carrier_phase_sign": -1.0,
}


def test_exact_channel_path_matches_explicit_euclidean_path() -> None:
    platform = np.asarray([13.0, -4.0, 2.0])
    channel = np.asarray([0.23, -0.17, 0.08])
    target = np.asarray([8800.0, 1400.0, 50.0])
    expected = np.linalg.norm(target - platform) + np.linalg.norm(
        target - (platform + channel)
    )
    actual = MODEL.finite_range_channel_path_m(target, platform, channel)
    assert actual == pytest.approx(expected)


def test_pair_phase_is_wrapped_and_uses_channel_specific_receive_paths() -> None:
    platform = np.zeros(3)
    theta = 7.0
    radius = 9000.0
    target = MODEL.beam_target_position_m(theta, radius, platform, METADATA)
    paths = [
        np.linalg.norm(target - platform) + np.linalg.norm(target - (platform + p))
        for p in CHANNELS
    ]
    raw = -2.0 * np.pi * (paths[0] - paths[1]) / (MODEL.C / 16.0e9)
    expected = np.arctan2(np.sin(raw), np.cos(raw))
    actual = MODEL.finite_range_pair_phase_rad(
        theta,
        radius,
        platform,
        CHANNELS,
        0,
        1,
        16.0e9,
        METADATA,
    )
    assert actual == pytest.approx(expected)
    assert -np.pi <= actual <= np.pi


def test_reported_context_changes_target_position_without_hidden_global_state() -> None:
    near = MODEL.beam_target_position_m(0.0, 8800.0, np.zeros(3), METADATA)
    far = MODEL.beam_target_position_m(0.0, 9600.0, np.zeros(3), METADATA)
    assert not np.allclose(near, far)


def test_stage2_reference_platform_context_is_used_for_fixed_surface_targets() -> None:
    metadata = {
        **METADATA,
        "platform": {**METADATA["platform"], "speed_mps": 60.0},
        "waveform": {"prf_hz": 1300.0, "pulse_num": 4},
        "scan": {"beam_count": 9},
        "random": {"period_start": 0},
    }
    packet_platform = np.asarray([0.0, 0.0, 6000.0])
    target = MODEL.beam_target_position_m(0.0, 9000.0, packet_platform, metadata)
    reference_index = (9 // 2) * 4 + (4 // 2)
    expected = packet_platform.copy()
    expected[0] = 60.0 * reference_index / 1300.0
    ground = np.sqrt(9000.0**2 - 6000.0**2)
    expected[1] = -ground
    expected[2] = 0.0
    assert target == pytest.approx(expected)


def test_exact_and_linear_comparison_reports_finite_sensitivity() -> None:
    comparison = MODEL.compare_exact_linear_phase(
        theta_deg=10.0,
        slant_range_m=9000.0,
        platform_position_m=np.zeros(3),
        channel_positions_m=CHANNELS,
        i=0,
        j=1,
        fc_hz=16.0e9,
        metadata=METADATA,
    )
    assert np.isfinite(comparison["exact_phase_rad"])
    assert np.isfinite(comparison["linear_phase_rad"])
    assert np.isfinite(comparison["disagreement_rad"])
    assert comparison["sensitivity_rad_per_m"] != 0.0


def test_observable_evaluator_delegates_exact_path_to_shared_model() -> None:
    platform = np.asarray([0.0, 0.0, 6000.0])
    target = MODEL.beam_target_position_m(10.0, 9000.0, platform, METADATA)
    expected = MODEL.finite_range_channel_path_m(target, platform, CHANNELS[1])
    actual = ANALYZER.exact_receive_channel_path_length(
        target, platform, CHANNELS[1]
    )
    assert actual == pytest.approx(expected)
    expected_phase = MODEL.wrap_phase(
        -2.0
        * np.pi
        * (
            MODEL.finite_range_channel_path_m(target, platform, CHANNELS[0])
            - MODEL.finite_range_channel_path_m(target, platform, CHANNELS[1])
        )
        / (MODEL.C / 16.0e9)
    )
    assert ANALYZER.exact_pair_phase_rad(
        target, platform, CHANNELS, 0, 1, 16.0e9
    ) == pytest.approx(expected_phase)
