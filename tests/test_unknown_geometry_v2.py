from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analyze_four_channel_observables.py"
SPEC = importlib.util.spec_from_file_location("four_channel_observables_v2", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
OBS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OBS)
MODEL = OBS.GEOMETRY_MODEL


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
    "carrier_phase_sign": -1.0,
    "platform": {"height_m": 6000.0, "speed_mps": 60.0, "squint_side": 1},
    "waveform": {"prf_hz": 1300.0, "fc_ghz": 16.0},
    "simulation_geometry": {
        "local_x_axis": "north",
        "local_y_axis": "east",
        "platform_heading_deg": 90.0,
        "range_geometry": "algorithm",
        "use_ground_range_for_position": True,
        "squint_side": 1,
    },
    "scene": {"ground_z_m": 0.0},
}


def _observations(
    delta_m: float,
    *,
    ranges: tuple[float, ...] = (8800.0, 9000.0, 9600.0),
    angles: tuple[float, ...] = (-10.0, 0.0, 10.0),
    noise_sigma_rad: float = 0.0,
    seed: int = 7,
) -> list[dict[str, object]]:
    truth_positions = CHANNELS.copy()
    truth_positions[[1, 3], 0] += delta_m
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for slant_range_m in ranges:
        for theta_deg in angles:
            for i, j in ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)):
                phase = MODEL.finite_range_pair_phase_rad(
                    theta_deg,
                    slant_range_m,
                    [0.0, 0.0, 6000.0],
                    truth_positions,
                    i,
                    j,
                    16.0e9,
                    METADATA,
                )
                rows.append(
                    {
                        "theta_deg": theta_deg,
                        "slant_range_m": slant_range_m,
                        "platform_position_m": [0.0, 0.0, 6000.0],
                        "pair_i": i,
                        "pair_j": j,
                        "observed_phase_rad": phase
                        + rng.normal(0.0, noise_sigma_rad),
                        "noise_sigma_rad": noise_sigma_rad,
                        "coherence": 0.99,
                        "weight": 1.0,
                        "source_id": "synthetic_observed_phase",
                    }
                )
    return rows


def test_v2_recovers_signed_baseline_error_from_reported_context() -> None:
    result = OBS.estimate_unknown_geometry_from_observations(
        _observations(0.0025),
        CHANNELS,
        16.0e9,
        METADATA,
        model="v2",
        max_abs_delta_m=0.01,
    )
    assert result["fit_status"] == "fit"
    assert result["status"] == "APPLY_ESTIMATED_CORRECTION"
    assert result["estimated_baseline_error_m"] == pytest.approx(0.0025, abs=2.0e-5)
    assert result["uncertainty_m"] > 0.0
    assert result["deadband_m"] > 0.0


def test_v2_zero_error_floor_is_finite_and_comparable_to_v1() -> None:
    observations = _observations(0.0)
    v1 = OBS.estimate_unknown_geometry_from_observations(
        observations,
        CHANNELS,
        16.0e9,
        METADATA,
        model="v1",
        max_abs_delta_m=0.01,
    )
    v2 = OBS.estimate_unknown_geometry_from_observations(
        observations,
        CHANNELS,
        16.0e9,
        METADATA,
        model="v2",
        max_abs_delta_m=0.01,
    )
    assert np.isfinite(float(v1["estimated_baseline_error_m"]))
    assert np.isfinite(float(v2["estimated_baseline_error_m"]))
    assert float(v2["estimated_baseline_error_m"]) == pytest.approx(0.0, abs=2.0e-5)
    assert np.isfinite(float(v2["model_disagreement_mean_abs_rad"]))


def test_deadband_uses_local_uncertainty_and_sensitivity() -> None:
    near = OBS.estimate_unknown_geometry_from_observations(
        _observations(
            0.0,
            ranges=(8800.0,),
            angles=(-5.0, 0.0, 5.0),
            noise_sigma_rad=1.0e-3,
        ),
        CHANNELS,
        16.0e9,
        METADATA,
        model="v2",
        max_abs_delta_m=0.01,
    )
    far = OBS.estimate_unknown_geometry_from_observations(
        _observations(
            0.0,
            ranges=(9600.0,),
            angles=(-10.0, 0.0, 10.0),
            noise_sigma_rad=1.0e-3,
        ),
        CHANNELS,
        16.0e9,
        METADATA,
        model="v2",
        max_abs_delta_m=0.01,
    )
    assert near["deadband_m"] > 0.0
    assert far["deadband_m"] > 0.0
    assert near["sensitivity_rad_per_m"] != pytest.approx(
        far["sensitivity_rad_per_m"]
    )
    assert near["deadband_m"] != pytest.approx(far["deadband_m"])


def test_observed_range_sample_uses_energy_centroid_inside_range_block() -> None:
    channels = np.zeros((8, 4), dtype=np.complex128)
    channels[2, :] = 1.0 + 0.0j
    channels[3, :] = 2.0 + 0.0j
    assert OBS.observed_range_sample(channels, 0, 8) == pytest.approx(2.8)
