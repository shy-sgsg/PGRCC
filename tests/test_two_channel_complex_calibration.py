"""Unit tests for truth-blind two-channel complex calibration baselines."""

from __future__ import annotations

import numpy as np
import pytest

from scripts.two_channel_complex_calibration import (
    CalibrationEstimate,
    apply_complex_calibration,
    estimate_ddc,
    estimate_ddc_rb,
    estimate_robust_ddc,
    estimate_robust_ddc_rb,
    estimate_scc,
)


def deterministic_f1(shape: tuple[int, int]) -> np.ndarray:
    rows, cols = shape
    real = np.arange(1, rows * cols + 1, dtype=np.float64).reshape(shape)
    imag = 0.25 + np.arange(rows * cols, dtype=np.float64).reshape(shape) / 7.0
    return real + 1j * imag


def residual_power(residual: np.ndarray) -> float:
    return float(np.mean(np.abs(residual) ** 2))


def test_scc_recovers_constant_gamma_and_applies_residual_with_support_only_inputs() -> None:
    f1 = deterministic_f1((3, 4))
    gamma = 1.25 - 0.5j
    support = np.ones(f1.shape, dtype=bool)
    estimate = estimate_scc(f1, gamma * f1, support, min_support=3)

    assert estimate.method == "SCC"
    assert estimate.status == "OK"
    assert estimate.support_count == 12
    assert estimate.gamma == pytest.approx(gamma)
    np.testing.assert_allclose(
        apply_complex_calibration(f1, gamma * f1, estimate),
        np.zeros_like(f1),
        atol=1e-12,
    )
    assert estimate.metadata["truth_used_in_estimator"] is False


def test_conjugation_sign_regression_recovers_positive_phase_rotation() -> None:
    f1 = deterministic_f1((2, 5))
    positive_phase_gamma = np.exp(1j * 0.7)
    estimate = estimate_scc(
        f1,
        positive_phase_gamma * f1,
        np.ones(f1.shape, dtype=bool),
        min_support=2,
    )

    assert estimate.status == "OK"
    assert estimate.gamma == pytest.approx(positive_phase_gamma)
    assert np.angle(estimate.gamma) == pytest.approx(0.7)


def test_support_mask_excludes_unsupported_cells_from_scc_fit() -> None:
    f1 = deterministic_f1((3, 3))
    support = np.ones(f1.shape, dtype=bool)
    support[0, 0] = False
    f2 = (0.8 + 0.2j) * f1
    f2[0, 0] = (-9.0 + 4.0j) * f1[0, 0]

    estimate = estimate_scc(f1, f2, support, min_support=4)

    assert estimate.status == "OK"
    assert estimate.support_count == 8
    assert estimate.gamma == pytest.approx(0.8 + 0.2j)


def test_ddc_beats_scc_when_gamma_varies_by_doppler_bin() -> None:
    f1 = deterministic_f1((5, 4))
    gamma_by_doppler = np.asarray([1.0 + 0.0j, 0.7 + 0.4j, -0.2 + 1.1j, 1.4 - 0.3j])
    f2 = f1 * gamma_by_doppler[np.newaxis, :]
    support = np.ones(f1.shape, dtype=bool)

    scc = estimate_scc(f1, f2, support, min_support=3)
    ddc = estimate_ddc(f1, f2, support, min_support=3)

    assert ddc.status == "OK"
    np.testing.assert_allclose(ddc.gamma, gamma_by_doppler, atol=1e-12)
    assert residual_power(apply_complex_calibration(f1, f2, ddc)) < 1e-24
    assert residual_power(apply_complex_calibration(f1, f2, scc)) > 0.1


def test_ddc_rb_beats_ddc_when_gamma_varies_by_range_band_and_doppler() -> None:
    f1 = deterministic_f1((4, 3))
    gamma_by_band_doppler = np.asarray(
        [
            [1.0 + 0.0j, 0.5 + 0.5j, 1.1 - 0.2j],
            [-0.3 + 0.9j, 1.6 - 0.1j, 0.8 - 0.7j],
        ]
    )
    f2 = f1.copy()
    f2[:2, :] *= gamma_by_band_doppler[0][np.newaxis, :]
    f2[2:, :] *= gamma_by_band_doppler[1][np.newaxis, :]
    support = np.ones(f1.shape, dtype=bool)

    ddc = estimate_ddc(f1, f2, support, min_support=2)
    ddc_rb = estimate_ddc_rb(f1, f2, support, min_support=2, range_band_size=2)

    assert ddc_rb.status == "OK"
    assert ddc_rb.gamma.shape == (2, 3)
    np.testing.assert_allclose(ddc_rb.gamma, gamma_by_band_doppler, atol=1e-12)
    assert ddc_rb.metadata["range_band_size"] == 2
    assert residual_power(apply_complex_calibration(f1, f2, ddc_rb)) < 1e-24
    assert residual_power(apply_complex_calibration(f1, f2, ddc)) > 0.1


def test_ddc_rb_apply_handles_final_short_range_band_boundary() -> None:
    f1 = deterministic_f1((5, 2))
    gamma_by_band_doppler = np.asarray(
        [
            [1.0 + 0.0j, 0.9 + 0.1j],
            [0.5 + 0.5j, 1.1 - 0.2j],
            [-0.2 + 1.0j, 0.7 - 0.4j],
        ]
    )
    f2 = f1.copy()
    f2[:2, :] *= gamma_by_band_doppler[0][np.newaxis, :]
    f2[2:4, :] *= gamma_by_band_doppler[1][np.newaxis, :]
    f2[4:, :] *= gamma_by_band_doppler[2][np.newaxis, :]

    estimate = estimate_ddc_rb(
        f1,
        f2,
        np.ones(f1.shape, dtype=bool),
        min_support=1,
        range_band_size=2,
    )

    assert estimate.status == "OK"
    np.testing.assert_allclose(estimate.gamma, gamma_by_band_doppler, atol=1e-12)
    np.testing.assert_allclose(
        apply_complex_calibration(f1, f2, estimate),
        np.zeros_like(f1),
        atol=1e-12,
    )


def test_robust_ddc_uses_circular_phase_threshold_to_exclude_phase_outlier() -> None:
    f1 = np.ones((5, 2), dtype=np.complex128)
    inlier_gamma = np.exp(1j * 0.4)
    f2 = np.tile(np.asarray([inlier_gamma, 1.0 + 0.0j]), (5, 1))
    f2[4, 0] = np.exp(1j * 2.8)
    support = np.ones(f1.shape, dtype=bool)

    plain = estimate_ddc(f1, f2, support, min_support=3)
    robust = estimate_robust_ddc(f1, f2, support, min_support=3, phase_threshold_rad=0.35)

    assert abs(np.angle(plain.gamma[0]) - 0.4) > 0.1
    assert robust.status == "OK"
    assert robust.gamma[0] == pytest.approx(inlier_gamma)
    assert robust.metadata["excluded_count"] == 1
    assert robust.metadata["local_support_count"].tolist() == [4, 5]


def test_robust_ddc_rb_reports_local_not_evaluable_for_low_support_band() -> None:
    f1 = deterministic_f1((4, 2))
    f2 = f1 * (1.2 - 0.1j)
    support = np.ones(f1.shape, dtype=bool)
    support[2:, 1] = False

    estimate = estimate_robust_ddc_rb(
        f1,
        f2,
        support,
        min_support=2,
        range_band_size=2,
        phase_threshold_rad=0.25,
    )

    assert estimate.status == "PARTIAL"
    assert estimate.metadata["local_status"].tolist() == [
        ["OK", "OK"],
        ["OK", "NOT_EVALUABLE"],
    ]
    assert np.isnan(estimate.gamma[1, 1])


def test_zero_denominator_is_not_evaluable_and_apply_rejects_invalid_estimate() -> None:
    f1 = np.zeros((2, 3), dtype=np.complex128)
    f2 = np.ones((2, 3), dtype=np.complex128)
    support = np.ones(f1.shape, dtype=bool)

    estimate = estimate_scc(f1, f2, support, min_support=2)

    assert estimate.status == "NOT_EVALUABLE"
    assert estimate.support_count == 6
    assert np.isnan(estimate.gamma)
    assert estimate.metadata["reason"] == "zero_denominator"
    with pytest.raises(ValueError, match="NOT_EVALUABLE"):
        apply_complex_calibration(f1, f2, estimate)


def test_invalid_shape_and_support_inputs_are_rejected() -> None:
    f1 = deterministic_f1((2, 3))
    f2 = deterministic_f1((2, 3))
    with pytest.raises(ValueError, match="equal shape"):
        estimate_scc(f1, f2[:, :2], np.ones(f1.shape, dtype=bool))
    with pytest.raises(ValueError, match="boolean"):
        estimate_scc(f1, f2, np.ones(f1.shape, dtype=np.int64))
    with pytest.raises(ValueError, match="finite"):
        estimate_scc(f1, f2 + np.nan, np.ones(f1.shape, dtype=bool))


def test_apply_rejects_unknown_estimate_shape() -> None:
    f1 = deterministic_f1((2, 3))
    f2 = f1.copy()
    bad = CalibrationEstimate(
        gamma=np.ones((2, 2), dtype=np.complex128),
        status="OK",
        support_count=4,
        method="BAD",
        metadata={},
    )

    with pytest.raises(ValueError, match="broadcast"):
        apply_complex_calibration(f1, f2, bad)
