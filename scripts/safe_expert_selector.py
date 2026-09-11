#!/usr/bin/env python3
"""Inference-only deterministic selector for the safe physics expert.

The selector consumes only target-off observables that are available before
the target-preservation evaluation.  It never accepts scene truth, target
labels, or post-hoc recovery metrics as input.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping


CURRENT = "J0_Current"
J5 = "J5_Selective_Physics_Calibration"
J6 = "J6_Joint_Phase_Surface"

FEATURE_NAMES = (
    "joint_confidence",
    "joint_rmse_rad",
    "residual_coherence",
    "tau_ns",
    "beta1_deg_per_pulse",
    "beta2_deg_per_pulse2",
    "d3_j6_delay_disagreement_ns",
    "p1_j6_phase_disagreement_deg_per_pulse",
    "raw_coherence",
    "support_fraction",
    "delay_confidence",
    "phase_confidence",
    "phase_signal",
    "delay_signal",
    "delay_extrapolation_ratio",
)

QUALITY_FEATURES = (
    "joint_confidence",
    "joint_rmse_rad",
    "residual_coherence",
    "raw_coherence",
    "support_fraction",
    "d3_j6_delay_disagreement_ns",
    "p1_j6_phase_disagreement_deg_per_pulse",
    "delay_extrapolation_ratio",
)


@dataclass(frozen=True)
class SelectorThresholds:
    """Frozen thresholds for one deterministic selector profile."""

    name: str
    joint_confidence_min: float
    joint_rmse_max: float
    residual_coherence_min: float
    raw_coherence_min: float
    support_fraction_min: float
    delay_disagreement_max_ns: float
    phase_disagreement_max_deg_per_pulse: float
    delay_confidence_min: float
    phase_confidence_min: float
    delay_signal_min_ns: float
    phase_signal_min: float
    delay_extrapolation_ratio_max: float


# These profiles are fixed before looking at Test-V2.  Profile selection is
# allowed to use only calibration+validation outcomes in the audit script.
SELECTOR_PROFILES = {
    "identity": SelectorThresholds(
        "identity", 1.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0,
        1.0, 1.0, math.inf, math.inf, 0.0),
    "loose": SelectorThresholds(
        "loose", 0.70, 0.60, 0.85, 0.75, 0.45, 2.0, 0.30,
        0.03, 0.03, 0.20, 0.03, 0.80),
    "balanced": SelectorThresholds(
        "balanced", 0.75, 0.50, 0.88, 0.78, 0.45, 1.0, 0.25,
        0.05, 0.05, 0.25, 0.04, 0.80),
    "strict": SelectorThresholds(
        "strict", 0.80, 0.45, 0.90, 0.82, 0.49, 0.50, 0.15,
        0.10, 0.10, 0.50, 0.06, 0.50),
    "very_strict": SelectorThresholds(
        "very_strict", 0.82, 0.40, 0.92, 0.85, 0.49, 0.30, 0.10,
        0.20, 0.15, 0.75, 0.10, 0.30),
}


def _finite(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def _abs_difference(left: Any, right: Any) -> float:
    left_value = _finite(left)
    right_value = _finite(right)
    if not math.isfinite(left_value) or not math.isfinite(right_value):
        return math.nan
    return abs(left_value - right_value)


def extract_inference_features(observables: Mapping[str, Any]) -> dict[str, float]:
    """Derive selector inputs from inference-visible observable summaries."""

    delay = observables.get("delay", {})
    phase_p1 = observables.get("phase_p1", {})
    coherence = observables.get("coherence", {})
    joint = observables.get("joint_surface", {})
    input_summary = observables.get("input", {})
    shape = input_summary.get("shape", ())
    total_support = math.nan
    if isinstance(shape, (list, tuple)) and len(shape) == 2:
        try:
            total_support = float(shape[0]) * float(shape[1])
        except (TypeError, ValueError):
            total_support = math.nan
    support = _finite(joint.get("support"))
    support_fraction = (support / total_support
                        if math.isfinite(support) and total_support > 0
                        else math.nan)
    tau_ns = _finite(joint.get("tau_ns"))
    max_delay = _finite(input_summary.get("max_supported_delay_ns"))
    extrapolation = (abs(tau_ns) / max_delay
                     if math.isfinite(tau_ns) and math.isfinite(max_delay)
                     and max_delay > 0 else math.nan)
    beta1 = _finite(joint.get("beta1_deg_per_pulse"))
    beta2 = _finite(joint.get("beta2_deg_per_pulse2"))
    phase_signal = (abs(beta1) + 10.0 * abs(beta2)
                    if math.isfinite(beta1) and math.isfinite(beta2)
                    else math.nan)
    delay_signal = abs(_finite(delay.get("delta_tau_ns")))
    return {
        "joint_confidence": _finite(joint.get("confidence")),
        "joint_rmse_rad": _finite(joint.get("rmse_rad")),
        "residual_coherence": _finite(
            joint.get("residual_coherence_global")),
        "tau_ns": tau_ns,
        "beta1_deg_per_pulse": beta1,
        "beta2_deg_per_pulse2": beta2,
        "d3_j6_delay_disagreement_ns": _abs_difference(
            delay.get("delta_tau_ns"), joint.get("tau_ns")),
        "p1_j6_phase_disagreement_deg_per_pulse": _abs_difference(
            phase_p1.get("slope_deg_per_pulse"),
            joint.get("beta1_deg_per_pulse")),
        "raw_coherence": _finite(coherence.get("global")),
        "support_fraction": support_fraction,
        "delay_confidence": _finite(delay.get("confidence")),
        "phase_confidence": _finite(phase_p1.get("confidence")),
        "phase_signal": phase_signal,
        "delay_signal": delay_signal,
        "delay_extrapolation_ratio": extrapolation,
    }


def thresholds_to_dict(thresholds: SelectorThresholds) -> dict[str, Any]:
    return asdict(thresholds)


def _quality_veto(features: Mapping[str, Any], thresholds: SelectorThresholds) -> str | None:
    if any(not math.isfinite(_finite(features.get(name)))
           for name in FEATURE_NAMES):
        return "missing_or_nonfinite_inference_feature"
    if features["raw_coherence"] < thresholds.raw_coherence_min:
        return "decorrelation_veto_raw_coherence"
    if features["residual_coherence"] < thresholds.residual_coherence_min:
        return "decorrelation_veto_residual_coherence"
    if features["joint_confidence"] < thresholds.joint_confidence_min:
        return "joint_confidence_veto"
    if features["joint_rmse_rad"] > thresholds.joint_rmse_max:
        return "joint_rmse_veto"
    if features["support_fraction"] < thresholds.support_fraction_min:
        return "support_veto"
    if (features["d3_j6_delay_disagreement_ns"]
            > thresholds.delay_disagreement_max_ns):
        return "delay_disagreement_veto"
    if (features["p1_j6_phase_disagreement_deg_per_pulse"]
            > thresholds.phase_disagreement_max_deg_per_pulse):
        return "phase_disagreement_veto"
    if features["delay_extrapolation_ratio"] > thresholds.delay_extrapolation_ratio_max:
        return "delay_extrapolation_veto"
    return None


def select_safe_expert_with_reason(
        features: Mapping[str, Any],
        thresholds: SelectorThresholds) -> tuple[str, str]:
    """Return `(method, reason)` using only inference-visible features."""

    veto = _quality_veto(features, thresholds)
    if veto is not None:
        return CURRENT, veto
    delay_signal = (
        features["delay_confidence"] >= thresholds.delay_confidence_min
        and features["delay_signal"] >= thresholds.delay_signal_min_ns)
    phase_signal = (
        features["phase_confidence"] >= thresholds.phase_confidence_min
        and features["phase_signal"] >= thresholds.phase_signal_min)
    if phase_signal:
        return J6, "high_confidence_phase_or_mixed"
    if delay_signal:
        return J5, "high_confidence_delay"
    return CURRENT, "zero_or_weak_signal"


def select_safe_expert(features: Mapping[str, Any],
                       thresholds: SelectorThresholds) -> str:
    return select_safe_expert_with_reason(features, thresholds)[0]
