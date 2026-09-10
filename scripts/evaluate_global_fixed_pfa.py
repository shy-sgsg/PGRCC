#!/usr/bin/env python3
"""Leakage-safe global fixed-Pfa calibration helpers.

Thresholds are learned only from the supplied calibration score iterator.  The
test iterator is evaluated with those frozen thresholds and is never passed to
the calibration accumulator.  This module has no production detector logic;
it consumes score maps produced by the production replay.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np


class TopKQuantileAccumulator:
    """Keep enough of the upper tail to resolve low-Pfa quantiles in bounded RAM."""

    def __init__(self, top_k: int = 500_000) -> None:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        self.top_k = int(top_k)
        self.total_count = 0
        self.finite_count = 0
        self._top = np.empty(0, dtype=np.float64)

    def add(self, scores: np.ndarray) -> None:
        values = np.asarray(scores, dtype=np.float64)
        values = values[np.isfinite(values) & (values >= 0.0)]
        if values.size == 0:
            return
        self.total_count += int(np.asarray(scores).size)
        self.finite_count += int(values.size)
        merged = np.concatenate((self._top, values))
        if merged.size > self.top_k:
            indices = np.argpartition(merged, merged.size - self.top_k)[-self.top_k:]
            merged = merged[indices]
        self._top = merged

    def threshold(self, requested_pfa: float) -> float:
        if not (0.0 < requested_pfa < 1.0):
            raise ValueError(f"requested_pfa must be in (0,1): {requested_pfa}")
        if self.finite_count == 0:
            raise ValueError("calibration population has no finite scores")
        rank_from_top = max(1, int(math.ceil(requested_pfa * self.finite_count)))
        if rank_from_top > self._top.size:
            raise ValueError(
                f"top_k={self.top_k} cannot resolve pfa={requested_pfa} "
                f"for finite_count={self.finite_count}")
        ordered = np.sort(self._top)
        return float(ordered[-rank_from_top])


def calibrate_thresholds(
    calibration_scores: Iterable[tuple[str, np.ndarray]],
    requested_pfas: Iterable[float] = (1.0e-2, 1.0e-3, 1.0e-4),
    top_k: int = 500_000,
) -> dict[str, Any]:
    requested = [float(value) for value in requested_pfas]
    accumulators: dict[str, TopKQuantileAccumulator] = {}
    for method, scores in calibration_scores:
        accumulators.setdefault(method, TopKQuantileAccumulator(top_k)).add(scores)
    rows: dict[str, Any] = {}
    for method, accumulator in sorted(accumulators.items()):
        thresholds = {
            str(pfa): accumulator.threshold(pfa)
            for pfa in requested
        }
        rows[method] = {
            "thresholds": thresholds,
            "calibration_cell_count": accumulator.finite_count,
            "calibration_input_cell_count": accumulator.total_count,
            "calibration_status": "frozen_from_calibration_negative_controls_only",
        }
    if not rows:
        raise ValueError("no calibration score maps supplied")
    return {
        "schema_version": 1,
        "requested_pfas": requested,
        "methods": rows,
        "leakage_guard": {
            "threshold_source": "calibration_negative_controls_only",
            "validation_or_test_scores_seen_during_calibration": False,
        },
    }


def evaluate_frozen_scores(
    test_scores: Iterable[tuple[str, np.ndarray]],
    frozen: dict[str, Any],
) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {}
    for method, scores in test_scores:
        if method not in frozen["methods"]:
            raise KeyError(f"no frozen calibration thresholds for method {method}")
        values = np.asarray(scores, dtype=np.float64)
        finite = values[np.isfinite(values) & (values >= 0.0)]
        row = counts.setdefault(method, {str(pfa): 0 for pfa in frozen["requested_pfas"]})
        row["__finite_count"] = row.get("__finite_count", 0) + int(finite.size)
        for pfa in frozen["requested_pfas"]:
            threshold = float(frozen["methods"][method]["thresholds"][str(pfa)])
            row[str(pfa)] += int(np.count_nonzero(finite >= threshold))
    output: dict[str, Any] = {}
    for method, row in counts.items():
        denominator = int(row.pop("__finite_count", 0))
        output[method] = {
            "test_cell_count": denominator,
            "empirical_pfa": {
                pfa: (float(count) / denominator if denominator else math.nan)
                for pfa, count in row.items()
            },
            "threshold_source": "frozen_calibration_negative_controls",
        }
    return output


def evaluate_causal_rows(
    rows: Iterable[dict[str, Any]], frozen: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate target-level held-out causal/legacy Pd at frozen thresholds."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["method"]), []).append(row)
    output: dict[str, Any] = {}
    unthresholded: list[str] = []
    for method, method_rows in sorted(grouped.items()):
        # Ablations and known-parameter oracles are evaluation-only methods in
        # the formal matrix.  They are not allowed to manufacture a threshold
        # from validation/test scores, so leave them in the raw target table
        # but omit them from fixed-Pfa Pd aggregation.
        if method not in frozen["methods"]:
            unthresholded.append(method)
            continue
        method_result: dict[str, Any] = {}
        for pfa in frozen["requested_pfas"]:
            key = str(pfa)
            threshold = float(frozen["methods"][method]["thresholds"][key])
            eligible = [item for item in method_rows
                        if math.isfinite(float(item["on_peak"])) and
                        math.isfinite(float(item["off_peak"]))]
            method_result[key] = {
                "threshold": threshold,
                "target_count": len(eligible),
                "causal_pd": (sum(float(item["on_peak"]) >= threshold for item in eligible) /
                              len(eligible) if eligible else math.nan),
                "paired_causal_pd": (sum(float(item["on_peak"]) >= threshold and
                                          float(item["off_peak"]) < threshold
                                          for item in eligible) /
                                     len(eligible) if eligible else math.nan),
                "legacy_pd": (sum(bool(item.get("legacy_hit", False)) for item in eligible) /
                              len(eligible) if eligible else math.nan),
                "target_preservation_db_median": _median_finite(
                    [item.get("target_preservation_db") for item in eligible]),
            }
        output[method] = method_result
    if unthresholded:
        output["_unthresholded_methods"] = unthresholded
    return output


def recovery_metrics(
    current_cancellation_db: Any,
    deterministic_cancellation_db: Any,
    oracle_cancellation_db: Any,
) -> dict[str, float | None]:
    """Compare a deterministic method with the known-parameter headroom.

    The oracle is an evaluation-only reference.  A non-positive or missing
    oracle gain is reported as an undefined recovery ratio rather than being
    turned into a misleading percentage.
    """
    current = finite_number(current_cancellation_db)
    deterministic = finite_number(deterministic_cancellation_db)
    oracle = finite_number(oracle_cancellation_db)
    if current is None or deterministic is None or oracle is None:
        return {
            "recoverable_headroom_db": None,
            "deterministic_gain_db": None,
            "remaining_gap_db": None,
            "recovery_ratio": None,
        }
    headroom = oracle - current
    gain = deterministic - current
    return {
        "recoverable_headroom_db": headroom,
        "deterministic_gain_db": gain,
        "remaining_gap_db": oracle - deterministic,
        "recovery_ratio": gain / headroom if headroom > 0.0 else None,
    }


def finite_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _median_finite(values: Iterable[Any]) -> float:
    finite_values: list[float] = []
    for value in values:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            finite_values.append(numeric)
    return float(np.median(finite_values)) if finite_values else math.nan
