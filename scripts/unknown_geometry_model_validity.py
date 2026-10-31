#!/usr/bin/env python3
"""Trust gate for the deterministic unknown-geometry estimator.

The optimizer is deliberately not treated as a correctness oracle.  This
module accepts its fitted value together with explicit per-observation
residual context and checks whether the reported geometry model is stable
across range, beam, pair, and slow-time subsets.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Mapping, Sequence

import numpy as np


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _wrap(value: float) -> float:
    return math.atan2(math.sin(value), math.cos(value))


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    array = np.asarray(values, dtype=np.float64)
    weight = np.asarray(weights, dtype=np.float64)
    return float(np.sum(array * weight) / max(1.0e-12, float(np.sum(weight))))


def _weighted_slope(
    x_values: Sequence[float],
    y_values: Sequence[float],
    weights: Sequence[float],
) -> float:
    x = np.asarray(x_values, dtype=np.float64)
    y = np.asarray(y_values, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    x_mean = _weighted_mean(x.tolist(), w.tolist())
    y_mean = _weighted_mean(y.tolist(), w.tolist())
    denominator = float(np.sum(w * (x - x_mean) ** 2))
    if denominator <= 1.0e-18:
        return 0.0
    return float(np.sum(w * (x - x_mean) * (y - y_mean)) / denominator)


def _spread_by(
    rows: Sequence[Mapping[str, object]],
    key_name: str,
) -> float:
    grouped: dict[object, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        key = (
            (row.get("pair_i"), row.get("pair_j"))
            if key_name == "pair"
            else row.get(key_name)
        )
        residual = _finite(row.get("residual_rad"))
        weight = _finite(row.get("weight", 1.0))
        if key is None or residual is None or weight is None or weight <= 0.0:
            continue
        grouped[key].append((residual, weight))
    means = [
        _weighted_mean([item[0] for item in values], [item[1] for item in values])
        for values in grouped.values()
        if values
    ]
    return float(max(means) - min(means)) if len(means) >= 2 else 0.0


def _subset_spread(
    rows: Sequence[Mapping[str, object]],
    selector,
) -> float:
    grouped: dict[object, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        key = selector(row)
        residual = _finite(row.get("residual_rad"))
        weight = _finite(row.get("weight", 1.0))
        if key is None or residual is None or weight is None or weight <= 0.0:
            continue
        grouped[key].append((residual, weight))
    means = [
        _weighted_mean([item[0] for item in values], [item[1] for item in values])
        for values in grouped.values()
        if values
    ]
    return float(max(means) - min(means)) if len(means) >= 2 else 0.0


def _closure_max_abs(rows: Sequence[Mapping[str, object]]) -> float:
    explicit = [
        abs(float(row["closure_residual_rad"]))
        for row in rows
        if _finite(row.get("closure_residual_rad")) is not None
    ]
    if explicit:
        return float(max(explicit))

    grouped: dict[tuple[object, ...], dict[str, float]] = defaultdict(dict)
    for index, row in enumerate(rows):
        pair_i = row.get("pair_i")
        pair_j = row.get("pair_j")
        phase = _finite(row.get("observed_phase_rad"))
        if pair_i is None or pair_j is None or phase is None:
            continue
        try:
            pair_name = f"C{int(pair_i) + 1}{int(pair_j) + 1}"
        except (TypeError, ValueError):
            continue
        key = (
            row.get("beam_id", index),
            row.get("pulse_index", index),
            row.get("range_block", row.get("slant_range_m", index)),
        )
        grouped[key][pair_name] = phase
    closure = []
    for pair_phases in grouped.values():
        if all(name in pair_phases for name in ("C12", "C23", "C13")):
            closure.append(
                abs(_wrap(pair_phases["C12"] + pair_phases["C23"] - pair_phases["C13"]))
            )
    return float(max(closure)) if closure else 0.0


def _valid_rows(observations: Sequence[Mapping[str, object]]) -> tuple[list[dict[str, object]], int]:
    rows: list[dict[str, object]] = []
    invalid = 0
    for source in observations:
        if not isinstance(source, Mapping):
            invalid += 1
            continue
        theta = _finite(source.get("theta_deg"))
        slant = _finite(source.get("slant_range_m"))
        residual = _finite(source.get("residual_rad"))
        weight = _finite(source.get("weight", 1.0))
        if (
            theta is None
            or slant is None
            or slant <= 0.0
            or residual is None
            or weight is None
            or weight <= 0.0
        ):
            invalid += 1
            continue
        row = dict(source)
        row.update({
            "theta_deg": theta,
            "slant_range_m": slant,
            "residual_rad": _wrap(residual),
            "weight": weight,
        })
        rows.append(row)
    return rows, invalid


def validate_geometry_model(
    result: Mapping[str, object],
    observations: Sequence[Mapping[str, object]],
    thresholds: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Return a stable trust decision for a converged geometry fit.

    Thresholds default to quantities derived from the fit's observed phase
    noise, local physical sensitivity, and uncertainty.  Explicit threshold
    overrides are accepted for audit fixtures and are persisted verbatim in
    the returned record.
    """

    configured = dict(thresholds or {})
    rows, invalid_count = _valid_rows(observations)
    phase_noise = _finite(result.get("phase_noise_rad"))
    if phase_noise is None or phase_noise <= 0.0:
        phase_noise = _finite(result.get("fit_rmse_rad"))
    if phase_noise is None or phase_noise <= 0.0:
        phase_noise = 1.0e-6
    sensitivity = _finite(result.get("sensitivity_rad_per_m"))
    if sensitivity is None or abs(sensitivity) <= 1.0e-12:
        sensitivity = _finite(result.get("physical_sensitivity_rad_per_m"))
    uncertainty = _finite(result.get("uncertainty_m"))
    estimate = _finite(result.get("estimated_baseline_error_m"))
    if sensitivity is None:
        sensitivity = 0.0
    if uncertainty is None or uncertainty <= 0.0:
        uncertainty = 0.0

    derived_budget = max(
        3.0 * phase_noise,
        abs(sensitivity) * uncertainty,
        1.0e-6,
    )

    def configured_positive(name: str, default: float) -> float:
        value = _finite(configured.get(name))
        if value is None or value <= 0.0:
            return float(default)
        return float(value)

    theta_values = [float(row["theta_deg"]) for row in rows]
    range_values = [float(row["slant_range_m"]) for row in rows]
    theta_span = float(np.ptp(np.asarray(theta_values, dtype=np.float64))) if theta_values else 0.0
    range_span = float(np.ptp(np.asarray(range_values, dtype=np.float64))) if range_values else 0.0
    residual_budget = configured_positive("residual_budget_rad", derived_budget)
    closure_budget = configured_positive("closure_budget_rad", 2.0 * residual_budget)
    disagreement_budget = configured_positive(
        "model_disagreement_budget_rad", derived_budget
    )
    min_observations = int(configured.get("min_observations", max(6, 2 * max(2, len(set(theta_values))))) or 0)
    thresholds_out = {
        "phase_noise_rad": float(phase_noise),
        "sensitivity_rad_per_m": float(sensitivity),
        "uncertainty_m": float(uncertainty),
        "residual_budget_rad": float(residual_budget),
        "closure_budget_rad": float(closure_budget),
        "model_disagreement_budget_rad": float(disagreement_budget),
        "cross_range_drift_budget_rad": float(residual_budget),
        "range_drift_budget_rad": float(residual_budget),
        "subset_spread_budget_rad": float(residual_budget),
        "min_observations": min_observations,
    }
    for key, value in configured.items():
        if key not in thresholds_out:
            thresholds_out[key] = value

    reasons: list[str] = []
    fit_status = str(result.get("fit_status", ""))
    if fit_status != "fit":
        reasons.append("fit did not converge")
    if estimate is None or sensitivity == 0.0 or uncertainty <= 0.0:
        reasons.append("fit lacks finite estimate, sensitivity, or uncertainty")
    if len(rows) < min_observations:
        reasons.append(
            f"valid observations {len(rows)} are below minimum {min_observations}"
        )
    if invalid_count:
        reasons.append(f"{invalid_count} observations contain non-finite or invalid values")
    theta_count = len({round(value, 9) for value in theta_values})
    range_count = len({round(value, 6) for value in range_values})
    pair_count = len({(row.get("pair_i"), row.get("pair_j")) for row in rows})
    if theta_count < 2:
        reasons.append("reported beam-angle rank is insufficient")
    if pair_count < 1:
        reasons.append("pair rank is insufficient")

    residuals = [float(row["residual_rad"]) for row in rows]
    weights = [float(row["weight"]) for row in rows]
    cross_slope = _weighted_slope(theta_values, residuals, weights) if rows else 0.0
    cross_drift = abs(cross_slope) * theta_span
    if cross_drift > residual_budget:
        reasons.append(
            f"cross-range residual trend {cross_drift:.6g} rad exceeds "
            f"budget {residual_budget:.6g} rad"
        )

    range_slope = _weighted_slope(range_values, residuals, weights) if rows else 0.0
    range_drift = abs(range_slope) * range_span
    if range_count >= 2 and range_drift > residual_budget:
        reasons.append(
            f"range-block residual trend {range_drift:.6g} rad exceeds "
            f"budget {residual_budget:.6g} rad"
        )

    pair_consistency = _spread_by(rows, "pair")
    if pair_consistency > residual_budget:
        reasons.append(
            f"pair residual inconsistency {pair_consistency:.6g} rad exceeds "
            f"budget {residual_budget:.6g} rad"
        )

    closure_max = _closure_max_abs(rows)
    if closure_max > closure_budget:
        reasons.append(
            f"pair closure residual {closure_max:.6g} rad exceeds "
            f"budget {closure_budget:.6g} rad"
        )

    beam_spread = _spread_by(rows, "beam_id")
    if beam_spread > residual_budget:
        reasons.append(
            f"beam subset spread {beam_spread:.6g} rad exceeds budget {residual_budget:.6g} rad"
        )

    def pulse_parity(row: Mapping[str, object]) -> object:
        pulse = _finite(row.get("pulse_index"))
        return None if pulse is None else int(pulse) % 2

    odd_even_spread = _subset_spread(rows, pulse_parity)
    if odd_even_spread > residual_budget:
        reasons.append(
            f"odd/even subset spread {odd_even_spread:.6g} rad exceeds "
            f"budget {residual_budget:.6g} rad"
        )

    row_disagreements = [
        abs(float(row["exact_linear_disagreement_rad"]))
        for row in rows
        if _finite(row.get("exact_linear_disagreement_rad")) is not None
    ]
    disagreement_mean = (
        float(np.mean(np.asarray(row_disagreements, dtype=np.float64)))
        if row_disagreements
        else _finite(result.get("model_disagreement_mean_abs_rad")) or 0.0
    )
    disagreement_p95 = (
        float(np.percentile(np.asarray(row_disagreements, dtype=np.float64), 95.0))
        if row_disagreements
        else _finite(result.get("model_disagreement_p95_rad")) or disagreement_mean
    )
    if disagreement_p95 > disagreement_budget:
        reasons.append(
            f"exact-versus-linear disagreement {disagreement_p95:.6g} rad exceeds "
            f"budget {disagreement_budget:.6g} rad"
        )

    diagnostics = {
        "observation_count": len(observations),
        "valid_observation_count": len(rows),
        "invalid_observation_count": invalid_count,
        "theta_count": theta_count,
        "range_count": range_count,
        "pair_count": pair_count,
        "cross_range_slope_rad_per_deg": float(cross_slope),
        "cross_range_drift_rad": float(cross_drift),
        "range_slope_rad_per_m": float(range_slope),
        "range_drift_rad": float(range_drift),
        "pair_consistency_spread_rad": float(pair_consistency),
        "closure_max_abs_rad": float(closure_max),
        "beam_subset_spread_rad": float(beam_spread),
        "odd_even_subset_spread_rad": float(odd_even_spread),
        "exact_linear_disagreement_mean_abs_rad": float(disagreement_mean),
        "exact_linear_disagreement_p95_rad": float(disagreement_p95),
        "rank_ok": bool(theta_count >= 2 and pair_count >= 1 and abs(sensitivity) > 1.0e-12),
        "estimate_m": estimate,
        "uncertainty_m": uncertainty,
    }
    status = "VALID" if not reasons else (
        "FALLBACK_UNIDENTIFIABLE"
        if any(
            phrase in reason
            for reason in reasons
            for phrase in ("did not converge", "below minimum", "rank", "lacks finite")
        )
        else "FALLBACK_MODEL_MISMATCH"
    )
    return {
        "schema": "unknown_geometry_model_validity_v1",
        "status": status,
        "reasons": reasons,
        "thresholds": thresholds_out,
        "diagnostics": diagnostics,
    }
