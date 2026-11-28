"""Truth-blind two-channel complex calibration baselines.

The estimators consume only observable F1/F2 arrays and a clutter-support
mask.  The fitted coefficient uses the fixed convention
``Gamma = sum(F2 * conj(F1)) / sum(|F1|^2)``, and correction applies the
matching residual ``F2 - Gamma * F1``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


_OK = "OK"
_PARTIAL = "PARTIAL"
_NOT_EVALUABLE = "NOT_EVALUABLE"
_EPS = np.finfo(np.float64).eps


@dataclass(frozen=True)
class CalibrationEstimate:
    """Complex calibration estimate plus structured status metadata."""

    gamma: complex | np.ndarray
    status: str
    support_count: int
    method: str
    metadata: dict[str, Any]


def _prepare_inputs(
    f1: np.ndarray,
    f2: np.ndarray,
    clutter_support: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    first = np.asarray(f1, dtype=np.complex128)
    second = np.asarray(f2, dtype=np.complex128)
    support = np.asarray(clutter_support)
    if first.ndim != 2 or second.ndim != 2:
        raise ValueError("F1/F2 must be 2-D arrays with equal shape")
    if first.shape != second.shape or first.shape != support.shape:
        raise ValueError("F1/F2/support must have equal shape")
    if support.dtype != np.bool_:
        raise ValueError("clutter_support must be a boolean array")
    if not np.all(np.isfinite(first)) or not np.all(np.isfinite(second)):
        raise ValueError("F1/F2 must contain finite complex values")
    return first, second, support


def _validate_min_support(min_support: int) -> int:
    value = int(min_support)
    if value <= 0:
        raise ValueError("min_support must be positive")
    return value


def _fit_gamma(
    first: np.ndarray,
    second: np.ndarray,
    mask: np.ndarray,
    min_support: int,
) -> tuple[complex, str, int, str | None]:
    support_count = int(np.count_nonzero(mask))
    if support_count < min_support:
        return complex(np.nan, np.nan), _NOT_EVALUABLE, support_count, "insufficient_support"
    selected_first = first[mask]
    denominator = float(np.sum(np.abs(selected_first) ** 2))
    if not np.isfinite(denominator) or denominator <= _EPS:
        return complex(np.nan, np.nan), _NOT_EVALUABLE, support_count, "zero_denominator"
    numerator = np.sum(second[mask] * np.conj(selected_first))
    gamma = complex(numerator / denominator)
    if not np.isfinite(gamma.real) or not np.isfinite(gamma.imag):
        return complex(np.nan, np.nan), _NOT_EVALUABLE, support_count, "nonfinite_gamma"
    return gamma, _OK, support_count, None


def _robust_mask(
    first: np.ndarray,
    second: np.ndarray,
    mask: np.ndarray,
    phase_threshold_rad: float,
    min_support: int,
) -> tuple[np.ndarray, str | None, float | None]:
    cross = second * np.conj(first)
    selected = mask & (np.abs(cross) > _EPS)
    if int(np.count_nonzero(selected)) < min_support:
        return np.zeros(first.shape, dtype=bool), "insufficient_phase_support", None
    unit_phase = cross[selected] / np.abs(cross[selected])
    mean_phase = np.mean(unit_phase)
    coherence = float(np.abs(mean_phase))
    if not np.isfinite(coherence) or coherence <= _EPS:
        return np.zeros(first.shape, dtype=bool), "low_phase_coherence", coherence
    phase_center = complex(mean_phase / coherence)
    residual_phase = np.angle(unit_phase * np.conj(phase_center))
    keep_selected = np.abs(residual_phase) <= phase_threshold_rad
    robust = np.zeros(first.shape, dtype=bool)
    robust[selected] = keep_selected
    return robust, None, coherence


def _overall_status(local_status: np.ndarray | list[str]) -> str:
    statuses = np.asarray(local_status, dtype=object)
    ok = statuses == _OK
    if bool(np.all(ok)):
        return _OK
    if bool(np.any(ok)):
        return _PARTIAL
    return _NOT_EVALUABLE


def _base_metadata(
    *,
    local_status: np.ndarray | None = None,
    local_support_count: np.ndarray | None = None,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "truth_used_in_estimator": False,
        "formula": "sum(F2*conj(F1))/sum(|F1|^2)",
        "residual": "F2-Gamma*F1",
    }
    if local_status is not None:
        metadata["local_status"] = local_status
    if local_support_count is not None:
        metadata["local_support_count"] = local_support_count
    if reason is not None:
        metadata["reason"] = reason
    if extra:
        metadata.update(extra)
    return metadata


def estimate_scc(
    f1: np.ndarray,
    f2: np.ndarray,
    clutter_support: np.ndarray,
    *,
    min_support: int = 8,
) -> CalibrationEstimate:
    """Estimate one scene-constant complex coefficient."""

    first, second, support = _prepare_inputs(f1, f2, clutter_support)
    min_count = _validate_min_support(min_support)
    gamma, status, support_count, reason = _fit_gamma(first, second, support, min_count)
    return CalibrationEstimate(
        gamma=gamma,
        status=status,
        support_count=support_count,
        method="SCC",
        metadata=_base_metadata(reason=reason),
    )


def estimate_ddc(
    f1: np.ndarray,
    f2: np.ndarray,
    clutter_support: np.ndarray,
    *,
    min_support: int = 8,
) -> CalibrationEstimate:
    """Estimate one complex coefficient per Doppler bin."""

    return _estimate_by_groups(
        f1,
        f2,
        clutter_support,
        min_support=min_support,
        method="DDC",
        range_band_size=None,
        robust=False,
        phase_threshold_rad=None,
    )


def estimate_ddc_rb(
    f1: np.ndarray,
    f2: np.ndarray,
    clutter_support: np.ndarray,
    *,
    min_support: int = 8,
    range_band_size: int = 8,
) -> CalibrationEstimate:
    """Estimate one complex coefficient per range band and Doppler bin."""

    return _estimate_by_groups(
        f1,
        f2,
        clutter_support,
        min_support=min_support,
        method="DDC-RB",
        range_band_size=range_band_size,
        robust=False,
        phase_threshold_rad=None,
    )


def estimate_robust_ddc(
    f1: np.ndarray,
    f2: np.ndarray,
    clutter_support: np.ndarray,
    *,
    min_support: int = 8,
    phase_threshold_rad: float = 0.5,
) -> CalibrationEstimate:
    """Estimate DDC after excluding circular phase outliers."""

    return _estimate_by_groups(
        f1,
        f2,
        clutter_support,
        min_support=min_support,
        method="robust_DDC",
        range_band_size=None,
        robust=True,
        phase_threshold_rad=phase_threshold_rad,
    )


def estimate_robust_ddc_rb(
    f1: np.ndarray,
    f2: np.ndarray,
    clutter_support: np.ndarray,
    *,
    min_support: int = 8,
    range_band_size: int = 8,
    phase_threshold_rad: float = 0.5,
) -> CalibrationEstimate:
    """Estimate DDC-RB after excluding circular phase outliers per local fit."""

    return _estimate_by_groups(
        f1,
        f2,
        clutter_support,
        min_support=min_support,
        method="robust_DDC-RB",
        range_band_size=range_band_size,
        robust=True,
        phase_threshold_rad=phase_threshold_rad,
    )


def _estimate_by_groups(
    f1: np.ndarray,
    f2: np.ndarray,
    clutter_support: np.ndarray,
    *,
    min_support: int,
    method: str,
    range_band_size: int | None,
    robust: bool,
    phase_threshold_rad: float | None,
) -> CalibrationEstimate:
    first, second, support = _prepare_inputs(f1, f2, clutter_support)
    min_count = _validate_min_support(min_support)
    threshold = None
    if robust:
        threshold = float(phase_threshold_rad)
        if not np.isfinite(threshold) or threshold <= 0.0:
            raise ValueError("phase_threshold_rad must be positive and finite")

    rows, cols = first.shape
    if range_band_size is None:
        band_size = rows
    else:
        band_size = int(range_band_size)
        if band_size <= 0:
            raise ValueError("range_band_size must be positive")
    band_count = (rows + band_size - 1) // band_size

    gamma = np.full((band_count, cols), np.nan + 1j * np.nan, dtype=np.complex128)
    local_status = np.full((band_count, cols), _NOT_EVALUABLE, dtype=object)
    local_support = np.zeros((band_count, cols), dtype=np.int64)
    local_phase_coherence = np.full((band_count, cols), np.nan, dtype=np.float64)
    reasons: list[str] = []
    excluded_count = 0

    for band_index in range(band_count):
        start = band_index * band_size
        stop = min(rows, start + band_size)
        for col in range(cols):
            local_mask = np.zeros(first.shape, dtype=bool)
            local_mask[start:stop, col] = support[start:stop, col]
            initial_gamma, status, count, reason = _fit_gamma(
                first, second, local_mask, min_count
            )
            if robust and status == _OK:
                assert threshold is not None
                filtered_mask, robust_reason, coherence = _robust_mask(
                    first, second, local_mask, threshold, min_count
                )
                if coherence is not None:
                    local_phase_coherence[band_index, col] = coherence
                excluded_count += int(np.count_nonzero(local_mask) - np.count_nonzero(filtered_mask))
                initial_gamma, status, count, reason = _fit_gamma(
                    first, second, filtered_mask, min_count
                )
                if robust_reason is not None:
                    reason = robust_reason
                if status != _OK and reason == "insufficient_support":
                    reason = "insufficient_robust_support"
            gamma[band_index, col] = initial_gamma
            local_status[band_index, col] = status
            local_support[band_index, col] = count
            if reason is not None:
                reasons.append(reason)

    shaped_gamma: np.ndarray = gamma[0] if range_band_size is None else gamma
    shaped_status: np.ndarray = local_status[0] if range_band_size is None else local_status
    shaped_support: np.ndarray = local_support[0] if range_band_size is None else local_support
    metadata_extra: dict[str, Any] = {}
    if range_band_size is not None:
        metadata_extra["range_band_size"] = band_size
    if robust:
        metadata_extra["phase_threshold_rad"] = threshold
        metadata_extra["excluded_count"] = excluded_count
        metadata_extra["local_phase_coherence"] = (
            local_phase_coherence[0] if range_band_size is None else local_phase_coherence
        )
    if reasons:
        metadata_extra["reasons"] = sorted(set(reasons))

    return CalibrationEstimate(
        gamma=shaped_gamma,
        status=_overall_status(shaped_status),
        support_count=int(np.count_nonzero(support)),
        method=method,
        metadata=_base_metadata(
            local_status=shaped_status,
            local_support_count=shaped_support,
            extra=metadata_extra,
        ),
    )


def apply_complex_calibration(
    f1: np.ndarray,
    f2: np.ndarray,
    estimate: CalibrationEstimate,
    *,
    allow_invalid: bool = False,
) -> np.ndarray:
    """Return the residual ``F2 - Gamma * F1`` for a valid estimate."""

    first = np.asarray(f1, dtype=np.complex128)
    second = np.asarray(f2, dtype=np.complex128)
    if first.shape != second.shape:
        raise ValueError("F1/F2 must have equal shape")
    if first.ndim != 2:
        raise ValueError("F1/F2 must be 2-D arrays")
    if estimate.status != _OK and not allow_invalid:
        raise ValueError(f"cannot apply calibration estimate with status {estimate.status}")

    gamma = np.asarray(estimate.gamma, dtype=np.complex128)
    if gamma.ndim == 0:
        expanded = gamma
    elif gamma.ndim == 1 and gamma.shape[0] == first.shape[1]:
        expanded = gamma[np.newaxis, :]
    elif gamma.ndim == 2 and gamma.shape[1] == first.shape[1]:
        band_count = gamma.shape[0]
        metadata_band_size = estimate.metadata.get("range_band_size")
        if metadata_band_size is None:
            if band_count <= 0 or first.shape[0] % band_count != 0:
                raise ValueError("Gamma range-band shape cannot broadcast to F1/F2")
            band_size = first.shape[0] // band_count
        else:
            band_size = int(metadata_band_size)
        if band_count <= 0 or band_size <= 0:
            raise ValueError("Gamma range-band shape cannot broadcast to F1/F2")
        expected_band_count = (first.shape[0] + band_size - 1) // band_size
        if expected_band_count != band_count:
            raise ValueError("Gamma range-band shape cannot broadcast to F1/F2")
        expanded = np.empty(first.shape, dtype=np.complex128)
        for band_index in range(band_count):
            start = band_index * band_size
            stop = min(first.shape[0], start + band_size)
            expanded[start:stop, :] = gamma[band_index][np.newaxis, :]
    else:
        raise ValueError("Gamma shape cannot broadcast to F1/F2")
    return second - expanded * first
