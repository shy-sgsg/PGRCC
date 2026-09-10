"""Deterministic formula tests for the M1 observable delay estimator."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from estimate_channel_delay import (  # noqa: E402
    correct_fractional_delay_frequency_domain,
    estimate_from_arrays,
    estimate_from_raw_time_arrays,
)
from estimate_temporal_phase import estimate_from_slow_time  # noqa: E402


class ChannelDelayEstimatorTests(unittest.TestCase):
    def test_raw_fast_time_delay_recovery(self) -> None:
        rng = np.random.default_rng(7)
        pulses, samples = 8, 256
        fs_hz = 60.0e6
        delay_sec = 8.0e-9
        channel1 = rng.normal(size=(pulses, samples)) + 1j * rng.normal(size=(pulses, samples))
        frequency = np.fft.fftfreq(samples, d=1.0 / fs_hz)
        channel2 = np.fft.ifft(
            np.fft.fft(channel1, axis=1) * np.exp(-1j * 2.0 * np.pi * frequency[None, :] * delay_sec),
            axis=1,
        )
        result = estimate_from_raw_time_arrays(channel1, channel2, fs_hz)
        for method in ("D1_ordinary_LS", "D2_weighted_LS", "D3_Huber_weighted_LS"):
            self.assertAlmostEqual(result["aggregate"][method]["delta_tau_ns"], 8.0, places=2)

    def test_known_delay_and_frequency_domain_correction(self) -> None:
        rows, cols = 6, 64
        range_axis = np.linspace(-1.0, 1.0, cols)
        bandwidth_hz = 50.0e6
        chirp_duration_sec = 130.0e-6
        frequency = (range_axis - np.mean(range_axis)) * 2.0 * bandwidth_hz / chirp_duration_sec / 299792458.0
        delay_sec = 8.0e-9
        intercept = 0.37
        f2 = np.ones((rows, cols), dtype=np.complex128)
        f1 = np.exp(1j * (intercept + 2.0 * np.pi * frequency * delay_sec))[None, :]
        f1 = np.repeat(f1, rows, axis=0)
        result = estimate_from_arrays(
            f1, f2, np.linspace(-100.0, 100.0, rows), range_axis,
            np.ones((rows, cols), dtype=bool), 0, rows - 1, 0, cols - 1,
            bandwidth_hz, chirp_duration_sec,
        )
        for method in ("D1_ordinary_LS", "D2_weighted_LS", "D3_Huber_weighted_LS"):
            self.assertAlmostEqual(result["aggregate"][method]["delta_tau_ns"], 8.0, places=6)
        corrected = correct_fractional_delay_frequency_domain(
            f2, frequency, delay_sec,
            np.full(rows, intercept, dtype=np.float64),
        )
        np.testing.assert_allclose(corrected, f1, atol=1.0e-12)

    def test_known_temporal_phase_slope_and_unwrapping(self) -> None:
        pulses, ranges = 64, 12
        slope = 0.08
        phase = 0.4 + slope * np.arange(pulses, dtype=np.float64)
        slow_f2 = np.ones((pulses, ranges), dtype=np.complex128)
        slow_f1 = np.exp(1j * phase)[:, None] * slow_f2
        result = estimate_from_slow_time(
            slow_f1, slow_f2, np.ones(ranges, dtype=bool), 1300.0,
        )
        for name in ("P1_robust_constant_linear", "P2_robust_quadratic",
                     "P3_smooth_spline_Kalman_like"):
            self.assertAlmostEqual(result["fits"][name]["slope_rad_per_pulse"], slope, places=3)

    def test_raw_cross_spectrum_phase_uses_channel_two_sign(self) -> None:
        pulses, samples = 48, 128
        slope = 0.08
        rng = np.random.default_rng(11)
        channel1 = rng.normal(size=(pulses, samples)) + 1j * rng.normal(size=(pulses, samples))
        spectrum1 = np.fft.fft(channel1, axis=1)
        phase = slope * np.arange(pulses, dtype=np.float64)
        spectrum2 = spectrum1 * np.exp(1j * phase[:, None])
        result = estimate_from_slow_time(
            spectrum1, spectrum2,
            np.fft.fftfreq(samples, d=1.0 / 60.0e6) > 0.0,
            1300.0,
        )
        self.assertAlmostEqual(
            result["fits"]["P1_robust_constant_linear"]["slope_rad_per_pulse"],
            -slope,
            places=3,
        )


if __name__ == "__main__":
    unittest.main()
