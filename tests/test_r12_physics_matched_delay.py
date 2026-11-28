from __future__ import annotations

import numpy as np
import pytest

from scripts.phase_r.physics_matched_delay import (
    compute_electronic_pair_group_delay_sec,
    estimate_physics_matched_delay,
    stage2_linear_delay_response,
    stage2_linear_fractional_delay,
)


FS_HZ = 60.0e6
FC_HZ = 16.0e9
N_SAMPLES = 4096
PULSE_COUNT = 8


def _reference_pulses(seed: int = 41) -> np.ndarray:
    rng = np.random.default_rng(seed)
    active = N_SAMPLES - 512
    time = (np.arange(active) - (active - 1) / 2.0) / FS_HZ
    chirp_slope = 48.0e6 / (active / FS_HZ)
    taper = np.hanning(active)
    waveform = taper * np.exp(1j * np.pi * chirp_slope * time**2)
    pulses = np.zeros((PULSE_COUNT, N_SAMPLES), dtype=np.complex128)
    for pulse in range(PULSE_COUNT):
        gain = (0.7 + 0.08 * pulse) * np.exp(1j * rng.uniform(-np.pi, np.pi))
        start = 256 + (pulse % 3) * 4
        pulses[pulse, start:start + active] = gain * waveform
    return pulses


def _bandlimited_shift(samples: np.ndarray, delay_samples: float) -> np.ndarray:
    rows, cols = samples.shape
    padding = cols
    padded = np.pad(samples, ((0, 0), (padding, padding)))
    frequency = np.fft.fftfreq(padded.shape[1])
    shifted = np.fft.ifft(
        np.fft.fft(padded, axis=1)
        * np.exp(-2j * np.pi * delay_samples * frequency)[None, :],
        axis=1,
    )
    return shifted[:, padding:padding + cols]


def _make_observation(
    delay_samples: float,
    *,
    gain: complex = 0.73 * np.exp(1.1j),
    actual_geometry_s: np.ndarray | float = 0.31e-9,
    noise_rms: float = 0.0,
    seed: int = 92,
) -> tuple[np.ndarray, np.ndarray]:
    reference = _reference_pulses()
    geometry = np.broadcast_to(np.asarray(actual_geometry_s, dtype=float), (PULSE_COUNT,))
    observed = np.empty_like(reference)
    for pulse, delay_sec in enumerate(geometry):
        nominal = _bandlimited_shift(reference[pulse:pulse + 1], delay_sec * FS_HZ)
        nominal *= np.exp(-2j * np.pi * FC_HZ * delay_sec)
        observed[pulse:pulse + 1] = stage2_linear_fractional_delay(
            nominal, delay_samples
        ) * gain
    if noise_rms:
        rng = np.random.default_rng(seed)
        observed += noise_rms * (
            rng.standard_normal(observed.shape)
            + 1j * rng.standard_normal(observed.shape)
        ) / np.sqrt(2.0)
    return reference, observed


@pytest.mark.parametrize("delay_samples", [0.0, 0.24, -0.24])
def test_m1_recovers_signed_fractional_delay_with_unknown_complex_gain(delay_samples: float) -> None:
    reference, observed = _make_observation(delay_samples)

    result = estimate_physics_matched_delay(
        reference,
        observed,
        fs_hz=FS_HZ,
        carrier_hz=FC_HZ,
        geometry_delay_sec=0.31e-9,
        carrier_phase_sign=-1,
        relative_response_model="constant_complex_gain",
    )

    assert result["status"] == "ok"
    assert result["delta_tau_ns"] == pytest.approx(delay_samples / FS_HZ * 1.0e9, abs=0.025)
    assert result["truth_used_in_estimator"] is False
    assert set(result["baselines"]) == {
        "D1_ordinary_LS", "D2_weighted_LS", "D3_Huber_weighted_LS"
    }
    assert result["support_count"] >= 8


def test_stage2_frequency_response_matches_the_signed_linear_interpolator() -> None:
    frequency = np.linspace(-25.0e6, 25.0e6, 1001)

    positive = stage2_linear_delay_response(0.24, frequency, FS_HZ)
    negative = stage2_linear_delay_response(-0.24, frequency, FS_HZ)

    assert positive[0] == pytest.approx(np.conj(positive[-1]))
    assert negative[0] == pytest.approx(np.conj(negative[-1]))
    assert negative == pytest.approx(np.conj(positive), abs=1e-12)


def test_electronic_geometry_delay_uses_the_production_los_projection() -> None:
    from math import cos, pi, sin, sqrt

    range_m = 88_000.0
    height_m = 6_000.0
    theta = 38.0 * pi / 180.0
    horizontal = sqrt(range_m**2 - height_m**2)
    los = np.asarray((
        -horizontal * sin(theta) / range_m,
        -horizontal * cos(theta) / range_m,
        -height_m / range_m,
    ))
    reference = np.asarray((-0.085, 0.0, 0.085))
    source = np.asarray((0.085, 0.0, 0.085))
    exact_path_difference = (
        sqrt(range_m**2 - 2.0 * range_m * np.dot(los, source) + np.dot(source, source))
        - sqrt(range_m**2 - 2.0 * range_m * np.dot(los, reference) + np.dot(reference, reference))
    )
    result = compute_electronic_pair_group_delay_sec(
        theta_deg=38.0,
        range_m=range_m,
        height_m=height_m,
        reference_offset_m=tuple(reference),
        source_offset_m=tuple(source),
        squint_side=1,
    )

    assert result == pytest.approx(exact_path_difference / 299_792_458.0, abs=2.0e-15)


def test_m1_remains_evaluable_with_observation_noise() -> None:
    reference, observed = _make_observation(0.24, noise_rms=0.01)

    result = estimate_physics_matched_delay(
        reference,
        observed,
        fs_hz=FS_HZ,
        carrier_hz=FC_HZ,
        geometry_delay_sec=0.31e-9,
        carrier_phase_sign=-1,
        relative_response_model="constant_complex_gain",
    )

    assert result["status"] == "ok"
    assert result["delta_tau_ns"] == pytest.approx(4.0, abs=0.08)


def test_m1_rejects_insufficient_spectral_support() -> None:
    reference = np.ones((PULSE_COUNT, N_SAMPLES), dtype=np.complex128)
    observed = reference.copy()

    result = estimate_physics_matched_delay(
        reference,
        observed,
        fs_hz=FS_HZ,
        carrier_hz=FC_HZ,
        geometry_delay_sec=0.0,
        carrier_phase_sign=-1,
        relative_response_model="constant_complex_gain",
    )

    assert result["status"] == "insufficient_support"
    assert result["delta_tau_ns"] is None


def test_m1_rejects_an_arbitrary_unknown_frequency_response_as_nonidentifiable() -> None:
    reference, observed = _make_observation(0.24)

    result = estimate_physics_matched_delay(
        reference,
        observed,
        fs_hz=FS_HZ,
        carrier_hz=FC_HZ,
        geometry_delay_sec=0.31e-9,
        carrier_phase_sign=-1,
        relative_response_model="arbitrary_complex_frequency_response",
    )

    assert result["status"] == "not_identifiable"
    assert result["delta_tau_ns"] is None
    assert result["fallback_reason"] == "unknown_frequency_response_can_absorb_delay"


def test_geometry_delay_error_is_confounded_with_channel_delay() -> None:
    actual_geometry = 0.31e-9
    reported_geometry = 0.46e-9
    reference, observed = _make_observation(0.24, actual_geometry_s=actual_geometry)

    result = estimate_physics_matched_delay(
        reference,
        observed,
        fs_hz=FS_HZ,
        carrier_hz=FC_HZ,
        geometry_delay_sec=reported_geometry,
        carrier_phase_sign=-1,
        relative_response_model="constant_complex_gain",
    )

    assert result["status"] == "ok"
    expected = (0.24 / FS_HZ + actual_geometry - reported_geometry) * 1.0e9
    assert result["delta_tau_ns"] == pytest.approx(expected, abs=0.035)


def test_unmodelled_geometry_phase_can_bias_m1_without_a_large_residual() -> None:
    actual_geometry = np.linspace(-0.7e-9, 0.7e-9, PULSE_COUNT)
    reference, observed = _make_observation(0.24, actual_geometry_s=actual_geometry)

    result = estimate_physics_matched_delay(
        reference,
        observed,
        fs_hz=FS_HZ,
        carrier_hz=FC_HZ,
        geometry_delay_sec=0.0,
        carrier_phase_sign=-1,
        relative_response_model="constant_complex_gain",
    )

    assert result["status"] == "ok"
    assert abs(result["delta_tau_ns"] - 4.0) > 0.1
    assert result["normalized_model_residual"] < 0.05
