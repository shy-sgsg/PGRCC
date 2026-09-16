"""Pure-function tests for the channel-delay Stage-1 core."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from scripts.delay_stage1_core import (
    approximate_two_channel_cancellation_loss,
    delay_method_suite,
    estimate_delay_gcc_phat,
    paired_bootstrap_ci,
    paired_mcnemar_exact,
    recovery_ratio,
    residual_phase_from_delay_error,
    run_parameter_monte_carlo,
    weighted_slope_variance,
)
from scripts.delay_stage1_core import _analytic_lfm_spectrum


def make_fractionally_delayed_lfm(
    delay_ns: float,
    snr_db: float,
    *,
    n_samples: int = 2048,
    fs_hz: float = 60.0e6,
    seed: int = 20260916,
) -> tuple[np.ndarray, np.ndarray, float]:
    rng = np.random.default_rng(seed)
    frequency = np.fft.fftfreq(n_samples, d=1.0 / fs_hz)
    spectrum = np.zeros(n_samples, dtype=np.complex128)
    support = (np.abs(frequency) >= 5.0e6) & (np.abs(frequency) <= 25.0e6)
    spectrum[support] = np.exp(1j * rng.uniform(-math.pi, math.pi, int(np.sum(support))))
    reference = np.fft.ifft(spectrum)
    delayed = np.fft.ifft(
        np.fft.fft(reference)
        * np.exp(-1j * 2.0 * np.pi * frequency * delay_ns * 1.0e-9)
    )
    signal_power = float(np.mean(np.abs(delayed) ** 2))
    noise_power = signal_power / (10.0 ** (snr_db / 10.0))
    noise_scale = math.sqrt(noise_power / 2.0)
    noise1 = noise_scale * (
        rng.standard_normal(n_samples) + 1j * rng.standard_normal(n_samples)
    )
    noise2 = noise_scale * (
        rng.standard_normal(n_samples) + 1j * rng.standard_normal(n_samples)
    )
    return reference + noise1, delayed + noise2, fs_hz


def rows_by_method(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["method"]): row for row in rows}


def test_cross_spectrum_sign_convention_recovers_positive_and_negative_fractional_delay() -> None:
    f1, f2, fs = make_fractionally_delayed_lfm(delay_ns=2.25, snr_db=60.0)
    rows = rows_by_method(delay_method_suite(f1, f2, fs))
    assert rows["D1_ordinary_LS"]["delta_tau_ns"] == pytest.approx(2.25, abs=0.08)

    f1, f2, fs = make_fractionally_delayed_lfm(delay_ns=-2.25, snr_db=60.0)
    rows = rows_by_method(delay_method_suite(f1, f2, fs))
    assert rows["D2_weighted_LS"]["delta_tau_ns"] == pytest.approx(-2.25, abs=0.08)


def test_traditional_baselines_are_present_and_use_same_input() -> None:
    f1, f2, fs = make_fractionally_delayed_lfm(delay_ns=1.5, snr_db=50.0)
    rows = rows_by_method(delay_method_suite(f1, f2, fs))
    assert {"cross_correlation", "gcc_phat"} <= set(rows)
    assert rows["cross_correlation"]["support_count"] == len(f1)
    assert rows["gcc_phat"]["runtime_sec"] >= 0.0


@pytest.mark.parametrize("delay_ns", [16.6666667, -16.6666667])
def test_baselines_recover_signed_delay_under_c12_convention(delay_ns: float) -> None:
    f1, f2, fs = make_fractionally_delayed_lfm(delay_ns=delay_ns, snr_db=60.0)
    rows = rows_by_method(delay_method_suite(f1, f2, fs))
    assert rows["cross_correlation"]["delta_tau_ns"] == pytest.approx(delay_ns, abs=0.08)
    assert rows["gcc_phat"]["delta_tau_ns"] == pytest.approx(delay_ns, abs=0.08)
    assert rows["cross_correlation"]["cross_spectrum_definition"] == "X1*conj(X2)"
    assert "reported_delay_ns=-lag_samples/fs" in rows["gcc_phat"]["internal_lag_convention"]


def test_delay_core_accepts_one_pulse_and_pulse_by_sample_inputs() -> None:
    f1, f2, fs = make_fractionally_delayed_lfm(delay_ns=2.25, snr_db=60.0)
    one_pulse = rows_by_method(delay_method_suite(f1, f2, fs))
    pulse_by_sample = rows_by_method(delay_method_suite(f1[None, :], f2[None, :], fs))

    assert one_pulse["D1_ordinary_LS"]["delta_tau_ns"] == pytest.approx(
        pulse_by_sample["D1_ordinary_LS"]["delta_tau_ns"]
    )
    assert one_pulse["cross_correlation"]["support_count"] == f1.size
    assert pulse_by_sample["cross_correlation"]["support_count"] == f1.size


def test_baseline_fallback_is_structured_for_insufficient_support() -> None:
    row = estimate_delay_gcc_phat(np.ones(4, complex), np.ones(4, complex), 60e6)
    assert row["status"] == "fallback"
    assert row["fallback_reason"] == "insufficient_calibration_support"
    assert row["delta_tau_ns"] is None


def test_weighted_slope_variance_reports_theory_fields() -> None:
    frequency = np.linspace(-20.0e6, 20.0e6, 101)
    phase = 0.1 + 2.0 * np.pi * frequency * 3.0e-9
    result = weighted_slope_variance(frequency, phase, np.ones_like(frequency))
    assert result["slope_rad_per_hz"] == pytest.approx(2.0 * np.pi * 3.0e-9)
    assert result["delay_std_ns"] >= 0.0
    assert result["ci95_low_ns"] <= result["ci95_high_ns"]


def test_weighted_slope_variance_uses_exact_weighted_sxx_and_residual_variance() -> None:
    frequency = np.array([-2.0, 1.0, 4.0])
    phase = np.array([0.2, 0.8, 2.0])
    weights = np.array([1.0, 2.0, 3.0])
    result = weighted_slope_variance(frequency, phase, weights)
    fbar = np.sum(weights * frequency) / np.sum(weights)
    sxx = float(np.sum(weights * (frequency - fbar) ** 2))
    slope = float(np.sum(weights * (frequency - fbar) * (phase - np.average(phase, weights=weights))) / sxx)
    residual = phase - (np.average(phase, weights=weights) + slope * (frequency - fbar))
    expected_variance = float(np.sum(weights * residual**2) / (3.0 - 2.0) / sxx)
    assert result["sxx_w"] == pytest.approx(sxx)
    assert result["slope_variance"] == pytest.approx(expected_variance)


def test_residual_phase_and_cancellation_loss_are_monotonic_in_delay_error() -> None:
    frequency = np.linspace(-25.0e6, 25.0e6, 101)
    small_phase = residual_phase_from_delay_error(frequency, 1.0)
    large_phase = residual_phase_from_delay_error(frequency, 8.0)
    assert np.max(np.abs(large_phase)) > np.max(np.abs(small_phase))
    small = approximate_two_channel_cancellation_loss(frequency, 1.0)
    large = approximate_two_channel_cancellation_loss(frequency, 8.0)
    assert large["mean_residual_phase_abs_rad"] > small["mean_residual_phase_abs_rad"]
    assert large["mean_approximate_loss"] > small["mean_approximate_loss"]


def test_zero_recovery_denominator_is_not_evaluable() -> None:
    result = recovery_ratio(
        ideal_or_upper=0.8, current=0.8, blind=0.8, direction="higher_is_better"
    )
    assert result["status"] == "NOT_EVALUABLE"
    assert result["recovery_ratio"] is None


def test_mcnemar_and_block_bootstrap_are_paired() -> None:
    result = paired_mcnemar_exact(
        [False, True, True, True, True], [True, False, False, False, False]
    )
    assert result["discordant_current_miss_comparison_hit"] == 1
    assert result["discordant_current_hit_comparison_miss"] == 4
    assert result["p_value_two_sided"] == pytest.approx(0.375)

    bootstrap = paired_bootstrap_ci(
        [1.0, 2.0, 10.0, 11.0],
        [2.0, 4.0, 13.0, 15.0],
        ["scene-a", "scene-a", "scene-b", "scene-b"],
        trials=200,
        seed=7,
    )
    assert bootstrap["n_blocks"] == 2
    assert bootstrap["observed_difference"] == pytest.approx(2.5)
    assert bootstrap["ci95_low"] <= bootstrap["ci95_high"]


def test_parameter_monte_carlo_returns_method_level_quality_fields() -> None:
    rows = run_parameter_monte_carlo(
        delay_errors_ns=[0.0, 2.0],
        snr_db_values=[20.0],
        trials=3,
        seed=11,
        fs_hz=60.0e6,
        bandwidth_hz=40.0e6,
        pulse_samples=512,
    )
    assert len(rows) == 2 * 1 * 5
    required = {
        "delay_error_ns", "snr_db", "method", "bias_ns", "rmse_ns", "std_ns",
        "ci95_low_ns", "ci95_high_ns", "outlier_rate", "fallback_rate",
        "fallback_count", "finite_estimate_count", "trials", "mean_runtime_sec",
    }
    assert all(required <= set(row) for row in rows)
    assert all(row["trials"] == 3 for row in rows)
    assert all(row["finite_estimate_count"] + row["fallback_count"] == 3 for row in rows)
    assert all(math.isfinite(float(row["mean_runtime_sec"])) for row in rows)


def test_monte_carlo_lfm_support_is_true_one_sided_analytic() -> None:
    frequency, spectrum = _analytic_lfm_spectrum(60.0e6, 20.0e6, 512)
    assert np.all(spectrum[frequency <= 0.0] == 0.0j)
    assert np.count_nonzero(spectrum[frequency > 0.0]) >= 8
