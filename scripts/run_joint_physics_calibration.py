#!/usr/bin/env python3
"""Run the deterministic Physics-Adaptive Joint CSI Calibration V1 replay.

The module keeps the production Current result as J0 and adds independent
raw-observable replays:

* J1: D3 delay-only;
* J2: P1 phase-only;
* J3: D3 correction, then P1 re-estimation and correction;
* J4: D3 correction, then P2 re-estimation and correction.

All estimates are formed from measured channel-1/channel-2 fast-time samples.
Scenario truth is deliberately not read here.  A null-derived gate may block
correction and returns the production Current result instead.  The script is
also importable: the pure estimator/gate/correction-decision functions are
used by formula-level tests and by the formal matrix runner.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from estimate_channel_delay import (  # noqa: E402
    estimate_from_raw_time_arrays,
    load_raw_channels,
)
from estimate_temporal_phase import estimate_from_slow_time  # noqa: E402
from experiment_provenance import git_provenance  # noqa: E402
from run_mechanism_aware_oracle import (  # noqa: E402
    inverse_channel_impairment_frequency_domain,
    inverse_channel_phase_trajectory_frequency_domain,
    patch_xml,
    run_logged,
    single_manifest,
    summary_metrics,
    xml_float,
    xml_int,
)


METHODS = (
    "J0_Current",
    "J1_D3_delay_only",
    "J2_P1_phase_only",
    "J3_D3_P1_joint",
    "J4_D3_P2_joint",
)
ABLATION_METHODS = (
    "A_P1_then_D3",
    "A_D3_original_P1",
)
ALL_METHODS = METHODS + ABLATION_METHODS
CALIBRATABLE = "CALIBRATABLE"
UNCERTAIN = "UNCERTAIN"
DECORRELATED = "DECORRELATED"
EPS = 1.0e-12


def finite(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.floating, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    return value


def db_ratio(numerator: float, denominator: float) -> float:
    if not (math.isfinite(numerator) and math.isfinite(denominator)):
        return math.nan
    return 10.0 * math.log10(max(numerator, np.finfo(float).tiny) /
                              max(denominator, np.finfo(float).tiny))


def _fit_summary(fit: dict[str, Any]) -> dict[str, Any]:
    """Drop arrays from an estimator fit while preserving auditable scalars."""
    fields = (
        "delta_tau_ns", "slope_rad_per_hz", "intercept_rad", "r2",
        "rmse_rad", "confidence", "row_count", "frequency_bin_count",
        "pulse_count", "slope_rad_per_pulse", "slope_deg_per_pulse",
        "trajectory_rmse_rad", "n",
    )
    result: dict[str, Any] = {}
    for key, value in fit.items():
        if key not in fields or value is None:
            continue
        if isinstance(value, (np.floating, float)):
            result[key] = float(value) if math.isfinite(float(value)) else None
        elif isinstance(value, (np.integer, int)):
            result[key] = int(value)
        else:
            result[key] = value
    return result


def _trajectory(fit_result: dict[str, Any], name: str) -> np.ndarray:
    values = fit_result.get("trajectories", {}).get(name)
    if values is None:
        return np.empty(0, dtype=np.float64)
    return np.asarray(values, dtype=np.float64)


def estimate_observables_from_arrays(
    channel1: np.ndarray,
    channel2: np.ndarray,
    fs_hz: float,
    prf_hz: float,
    raw_support_percentile: float = 80.0,
) -> dict[str, Any]:
    """Estimate D3/P1/P2 and inference-visible support statistics.

    The returned mapping contains private NumPy trajectories for replay and a
    JSON-safe ``summary`` mapping for manifests.  No truth or scenario fields
    are accepted by this function.
    """
    x1 = np.asarray(channel1, dtype=np.complex128)
    x2 = np.asarray(channel2, dtype=np.complex128)
    if x1.shape != x2.shape or x1.ndim != 2:
        raise ValueError("channel arrays must be equal-shape 2-D [pulse, sample]")
    if fs_hz <= 0.0 or prf_hz <= 0.0:
        raise ValueError("fs_hz and prf_hz must be positive")

    delay_result = estimate_from_raw_time_arrays(
        x1, x2, fs_hz, raw_support_percentile)
    spectrum1 = np.fft.fft(x1, axis=1)
    spectrum2 = np.fft.fft(x2, axis=1)
    frequency = np.fft.fftfreq(x1.shape[1], d=1.0 / fs_hz)
    positive = frequency > 0.0
    phase_result = estimate_from_slow_time(
        spectrum1, spectrum2, positive, prf_hz)

    cross = spectrum1[:, positive] * np.conj(spectrum2[:, positive])
    denom1 = np.sum(np.abs(spectrum1[:, positive]) ** 2)
    denom2 = np.sum(np.abs(spectrum2[:, positive]) ** 2)
    global_coherence = (abs(np.sum(cross)) / math.sqrt(float(denom1 * denom2))
                        if denom1 > 0.0 and denom2 > 0.0 else math.nan)
    pulse_denom = np.sqrt(
        np.sum(np.abs(spectrum1[:, positive]) ** 2, axis=1) *
        np.sum(np.abs(spectrum2[:, positive]) ** 2, axis=1))
    pulse_cross = np.abs(np.sum(cross, axis=1))
    pulse_coherence = np.divide(
        pulse_cross, pulse_denom, out=np.zeros_like(pulse_cross), where=pulse_denom > 0.0)
    p1_name = "P1_robust_constant_linear"
    p2_name = "P2_robust_quadratic"
    d3_name = "D3_Huber_weighted_LS"
    d3 = delay_result["aggregate"][d3_name]
    p1 = phase_result["fits"][p1_name]
    p2 = phase_result["fits"][p2_name]
    summary = {
        "delay": _fit_summary(d3),
        "phase_p1": _fit_summary(p1),
        "phase_p2": _fit_summary(p2),
        "coherence": {
            "global": finite(global_coherence),
            "pulse_median": finite(np.median(pulse_coherence)),
            "pulse_p05": finite(np.percentile(pulse_coherence, 5.0)),
            "pulse_p95": finite(np.percentile(pulse_coherence, 95.0)),
            "frequency_bin_count": int(np.count_nonzero(positive)),
            "pulse_count": int(x1.shape[0]),
        },
        "input": {
            "shape": [int(x1.shape[0]), int(x1.shape[1])],
            "fs_hz": float(fs_hz),
            "prf_hz": float(prf_hz),
            "support_percentile": float(raw_support_percentile),
            "cross_spectrum": "sum_pulses(FFT(channel1)*conj(FFT(channel2)))",
            "truth_used_in_estimator": False,
        },
    }
    return {
        "summary": summary,
        "delay_ns": finite(d3.get("delta_tau_ns")),
        "delay_confidence": finite(d3.get("confidence"), 0.0),
        "phase_p1_observed": _trajectory(phase_result, p1_name),
        "phase_p2_observed": _trajectory(phase_result, p2_name),
        "phase_p1_correction": -_trajectory(phase_result, p1_name),
        "phase_p2_correction": -_trajectory(phase_result, p2_name),
        "phase_p1_slope_deg_per_pulse": finite(p1.get("slope_deg_per_pulse")),
        "phase_p2_slope_deg_per_pulse": finite(p2.get("slope_deg_per_pulse")),
        "phase_p1_confidence": finite(p1.get("confidence"), 0.0),
        "phase_p2_confidence": finite(p2.get("confidence"), 0.0),
        "coherence": finite(global_coherence),
        "pulse_coherence_median": finite(np.median(pulse_coherence)),
    }


def compact_observables(observables: dict[str, Any]) -> dict[str, Any]:
    """Return only JSON-safe inference-visible estimator evidence."""
    return json.loads(json.dumps(json_safe(observables["summary"]), allow_nan=False))


def calibrate_null_gate(
    null_observations: list[dict[str, Any]],
    calibration_set_id: str,
    deadband_quantile: float = 0.99,
    confidence_quantile: float = 0.05,
    decorrelation_quantile: float = 0.01,
) -> dict[str, Any]:
    """Freeze deadbands and confidence/fallback thresholds from null only."""
    if len(null_observations) < 3:
        raise ValueError("at least three null observations are required")

    def values(path: tuple[str, ...]) -> np.ndarray:
        data = []
        for row in null_observations:
            item: Any = row
            for key in path:
                item = item[key]
            value = finite(item)
            if math.isfinite(value):
                data.append(value)
        if len(data) < 3:
            raise ValueError(f"null population lacks finite values: {path}")
        return np.asarray(data, dtype=np.float64)

    delay_abs = np.abs(values(("delay", "delta_tau_ns")))
    phase_abs = np.abs(values(("phase_p1", "slope_deg_per_pulse")))
    phase_p2_abs = np.abs(values(("phase_p2", "slope_deg_per_pulse")))
    delay_conf = values(("delay", "confidence"))
    phase_conf = values(("phase_p1", "confidence"))
    phase_p2_conf = values(("phase_p2", "confidence"))
    delay_rmse = values(("delay", "rmse_rad"))
    phase_rmse = values(("phase_p1", "rmse_rad"))
    phase_p2_rmse = values(("phase_p2", "rmse_rad"))
    pulse_coherence = values(("coherence", "pulse_median"))
    return {
        "schema_version": 1,
        "calibration_set_id": calibration_set_id,
        "source": "zero-mismatch C+N raw-observable null population only",
        "ai_training": False,
        "quantiles": {
            "deadband": deadband_quantile,
            "confidence": confidence_quantile,
            "decorrelation": decorrelation_quantile,
        },
        "sample_count": len(null_observations),
        "thresholds": {
            "epsilon_tau_ns": float(np.quantile(delay_abs, deadband_quantile)),
            "epsilon_phase_deg_per_pulse": float(np.quantile(phase_abs, deadband_quantile)),
            "epsilon_phase_p2_deg_per_pulse": float(np.quantile(
                phase_p2_abs, deadband_quantile)),
            "confidence_tau": float(np.quantile(delay_conf, confidence_quantile)),
            "confidence_phase": float(np.quantile(phase_conf, confidence_quantile)),
            "confidence_phase_p2": float(np.quantile(phase_p2_conf, confidence_quantile)),
            "max_rmse_tau_rad": float(np.quantile(delay_rmse, deadband_quantile)),
            "max_rmse_phase_rad": float(np.quantile(phase_rmse, deadband_quantile)),
            "max_rmse_phase_p2_rad": float(np.quantile(phase_p2_rmse, deadband_quantile)),
            "decorrelation_pulse_coherence": float(np.quantile(
                pulse_coherence, decorrelation_quantile)),
        },
        "null_summary": {
            "delay_abs_min_ns": float(np.min(delay_abs)),
            "delay_abs_max_ns": float(np.max(delay_abs)),
            "phase_abs_min_deg_per_pulse": float(np.min(phase_abs)),
            "phase_abs_max_deg_per_pulse": float(np.max(phase_abs)),
            "pulse_coherence_min": float(np.min(pulse_coherence)),
            "pulse_coherence_max": float(np.max(pulse_coherence)),
        },
    }


def classify_state(
    observables: dict[str, Any], gate: dict[str, Any], phase_method: str = "P1",
) -> dict[str, Any]:
    """Classify from observable coherence/fit quality without truth rho."""
    thresholds = gate["thresholds"]
    delay = observables["summary"]["delay"]
    if phase_method not in {"P1", "P2"}:
        raise ValueError(f"unknown phase method: {phase_method}")
    phase_key = "phase_p1" if phase_method == "P1" else "phase_p2"
    phase = observables["summary"][phase_key]
    coherence = observables["summary"]["coherence"]
    pulse_coherence = finite(coherence.get("pulse_median"))
    reasons: list[str] = []
    if (not math.isfinite(pulse_coherence) or
            pulse_coherence < float(thresholds["decorrelation_pulse_coherence"])):
        return {
            "state": DECORRELATED,
            "reasons": ["pulse_coherence_below_null_floor"],
            "delay_active": False,
            "phase_active": False,
        }

    delay_conf = finite(delay.get("confidence"), 0.0)
    phase_conf = finite(phase.get("confidence"), 0.0)
    delay_rmse = finite(delay.get("rmse_rad"))
    phase_rmse = finite(phase.get("rmse_rad"))
    phase_conf_threshold = float(thresholds.get(
        "confidence_phase_p2" if phase_method == "P2" else "confidence_phase",
        thresholds["confidence_phase"]))
    phase_rmse_threshold = float(thresholds.get(
        "max_rmse_phase_p2_rad" if phase_method == "P2" else "max_rmse_phase_rad",
        thresholds["max_rmse_phase_rad"]))
    phase_deadband = float(thresholds.get(
        "epsilon_phase_p2_deg_per_pulse" if phase_method == "P2" else "epsilon_phase_deg_per_pulse",
        thresholds["epsilon_phase_deg_per_pulse"]))
    if delay_conf < float(thresholds["confidence_tau"]):
        reasons.append("delay_confidence_below_null_floor")
    if phase_conf < phase_conf_threshold:
        reasons.append("phase_confidence_below_null_floor")
    if not math.isfinite(delay_rmse) or delay_rmse > float(thresholds["max_rmse_tau_rad"]):
        reasons.append("delay_fit_residual_above_null_envelope")
    if not math.isfinite(phase_rmse) or phase_rmse > phase_rmse_threshold:
        reasons.append("phase_fit_residual_above_null_envelope")
    state = CALIBRATABLE if not reasons else UNCERTAIN
    delay_active = (state == CALIBRATABLE and
                    abs(finite(delay.get("delta_tau_ns"))) > float(thresholds["epsilon_tau_ns"]) and
                    delay_conf >= float(thresholds["confidence_tau"]))
    phase_active = (state == CALIBRATABLE and
                    abs(finite(phase.get("slope_deg_per_pulse"))) > phase_deadband and
                    phase_conf >= phase_conf_threshold)
    if not delay_active:
        reasons.append("delay_inside_null_deadband")
    if not phase_active:
        reasons.append("phase_inside_null_deadband")
    return {
        "state": state,
        "reasons": reasons,
        "delay_active": delay_active,
        "phase_active": phase_active,
    }


def correction_decision(
    method: str, initial: dict[str, Any], gate: dict[str, Any],
) -> dict[str, Any]:
    """Return the pre-correction decision for one method."""
    phase_method = "P2" if method == "J4_D3_P2_joint" else "P1"
    state = classify_state(initial, gate, phase_method)
    if method == "J0_Current":
        return {**state, "apply_delay": False, "apply_phase": False,
                "phase_method": None, "fallback": True, "fallback_reason": "current_baseline"}
    if state["state"] != CALIBRATABLE:
        return {**state, "apply_delay": False, "apply_phase": False,
                "phase_method": None, "fallback": True,
                "fallback_reason": f"state_{state['state'].lower()}"}
    if method == "J1_D3_delay_only":
        apply_delay, apply_phase, phase_method = state["delay_active"], False, None
    elif method == "J2_P1_phase_only":
        apply_delay, apply_phase, phase_method = False, state["phase_active"], "P1"
    elif method in {"J3_D3_P1_joint", "A_D3_original_P1"}:
        apply_delay, apply_phase, phase_method = state["delay_active"], state["phase_active"], "P1"
    elif method == "J4_D3_P2_joint":
        apply_delay, apply_phase, phase_method = state["delay_active"], state["phase_active"], "P2"
    elif method == "A_P1_then_D3":
        apply_delay, apply_phase, phase_method = state["delay_active"], state["phase_active"], "P1"
    else:
        raise ValueError(f"unknown joint method: {method}")
    if not apply_delay and not apply_phase:
        return {**state, "apply_delay": False, "apply_phase": False,
                "phase_method": phase_method, "fallback": True,
                "fallback_reason": "both_components_inside_null_deadband"}
    return {**state, "apply_delay": apply_delay, "apply_phase": apply_phase,
            "phase_method": phase_method, "fallback": False, "fallback_reason": None}


def raw_input(case: Path) -> Path:
    paths = sorted((case / "stage2/data").glob("*period_0000.bin"))
    if len(paths) != 1:
        raise RuntimeError(f"expected one raw period under {case}: {paths}")
    return paths[0]


def xml_path(case: Path) -> Path:
    path = case / "stage2/config/temp_config_stage2_period_0000.xml"
    if not path.is_file():
        raise RuntimeError(f"missing production XML: {path}")
    return path


def case_observables(case: Path, prf_hz: float, support_percentile: float) -> tuple[Path, Path, dict[str, Any]]:
    case = case.resolve()
    xml = xml_path(case)
    raw = raw_input(case)
    pulse_len = xml_int(xml, "pulse_len", 11840)
    channel_count = xml_int(xml, "new_protocol_channel_count", 4)
    fs_hz = xml_float(xml, "fs", 60.0e6)
    x1, x2 = load_raw_channels(raw, pulse_len, channel_count, 1, 2)
    return raw, xml, estimate_observables_from_arrays(
        x1, x2, fs_hz, prf_hz, support_percentile)


def _phase_for_method(observables: dict[str, Any], phase_method: str | None) -> np.ndarray:
    if phase_method == "P2":
        return observables["phase_p2_correction"]
    return observables["phase_p1_correction"]


def apply_raw_correction(
    source: Path,
    target: Path,
    xml: Path,
    delay_ns: float | None,
    phase_deg_by_pulse: np.ndarray | None,
    phase_first: bool = False,
) -> list[dict[str, Any]]:
    """Apply independent frequency-domain delay/phase operations in order."""
    steps: list[dict[str, Any]] = []
    current = source
    temp_paths: list[Path] = []
    try:
        operations: list[tuple[str, Any]] = []
        if phase_first and phase_deg_by_pulse is not None:
            operations.append(("phase", phase_deg_by_pulse))
        if delay_ns is not None:
            operations.append(("delay", float(delay_ns)))
        if not phase_first and phase_deg_by_pulse is not None:
            operations.append(("phase", phase_deg_by_pulse))
        if not operations:
            raise ValueError("at least one correction operation is required")
        for index, (kind, value) in enumerate(operations):
            destination = target if index == len(operations) - 1 else target.with_name(
                target.name + f".step{index}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if kind == "delay":
                transform = inverse_channel_impairment_frequency_domain(
                    current, destination, xml, float(value), 0.0)
            else:
                transform = inverse_channel_phase_trajectory_frequency_domain(
                    current, destination, xml, np.asarray(value, dtype=np.float64))
            steps.append({"kind": kind, **transform})
            if current != source:
                temp_paths.append(current)
            current = destination
        return steps
    finally:
        for path in temp_paths:
            if path != target and path.is_file():
                path.unlink()


def current_metrics(case: Path) -> tuple[Path, dict[str, float]]:
    manifest = single_manifest(case.resolve())
    return manifest, summary_metrics(manifest)


def replay_method(
    case: Path,
    method: str,
    output_dir: Path,
    gate: dict[str, Any],
    initial: dict[str, Any],
    source_raw: Path,
    source_xml: Path,
    current_manifest_path: Path,
    current: dict[str, float],
    build_dir: Path,
    prf_hz: float,
    support_percentile: float,
) -> dict[str, Any]:
    decision = correction_decision(method, initial, gate)
    base_row: dict[str, Any] = {
        "method": method,
        "case": str(case.resolve()),
        "state": decision["state"],
        "state_reasons": decision["reasons"],
        "delay_active": decision["delay_active"],
        "phase_active": decision["phase_active"],
        "estimated_delay_ns": finite(initial["summary"]["delay"].get("delta_tau_ns")),
        "estimated_phase_slope_deg_per_pulse": finite(
            initial["summary"]["phase_p1"].get("slope_deg_per_pulse")),
        "current_cancellation_db": current.get("cancellation_db"),
        "current_coherence": current.get("coherence"),
        "current_phase_rmse_rad": current.get("phase_rmse_rad"),
        "current_manifest": str(current_manifest_path.resolve()),
        "fallback": bool(decision["fallback"]),
        "fallback_reason": decision["fallback_reason"],
        "status": "fallback_current" if decision["fallback"] else "pending",
        "truth_used_in_estimator": False,
    }
    if decision["fallback"]:
        base_row.update({
            "manifest": str(current_manifest_path.resolve()),
            "estimated_cancellation_db": current.get("cancellation_db"),
            "headroom_gain_db_vs_current": 0.0,
            "estimated_coherence": current.get("coherence"),
            "estimated_phase_rmse_rad": current.get("phase_rmse_rad"),
        })
        return base_row

    method_dir = output_dir / method
    method_dir.mkdir(parents=True, exist_ok=True)
    corrected_raw = method_dir / "stage2/data/joint_corrected_period_0000.bin"
    delay_ns = finite(initial["summary"]["delay"].get("delta_tau_ns")) if decision["apply_delay"] else None
    phase_method = decision["phase_method"]
    phase_values = (_phase_for_method(initial, phase_method)
                    if decision["apply_phase"] and method != "J3_D3_P1_joint" else None)
    intermediate: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []

    if method == "A_P1_then_D3":
        phase_values = _phase_for_method(initial, "P1") if decision["apply_phase"] else None
        steps = apply_raw_correction(source_raw, corrected_raw, source_xml,
                                     delay_ns, phase_values, phase_first=True)
    elif method == "A_D3_original_P1":
        phase_values = _phase_for_method(initial, "P1") if decision["apply_phase"] else None
        steps = apply_raw_correction(source_raw, corrected_raw, source_xml,
                                     delay_ns, phase_values, phase_first=False)
    elif method in {"J3_D3_P1_joint", "J4_D3_P2_joint"}:
        # The phase fit is deliberately rerun on the delay-corrected raw data.
        if decision["apply_delay"]:
            delay_path = method_dir / "stage2/data/.delay_corrected.bin"
            delay_steps = apply_raw_correction(source_raw, delay_path, source_xml,
                                               delay_ns, None)
            corrected_x1, corrected_x2 = load_raw_channels(
                delay_path, xml_int(source_xml, "pulse_len", 11840),
                xml_int(source_xml, "new_protocol_channel_count", 4), 1, 2)
            corrected_obs = estimate_observables_from_arrays(
                corrected_x1, corrected_x2, xml_float(source_xml, "fs", 60.0e6),
                prf_hz, support_percentile)
            intermediate["after_delay"] = compact_observables(corrected_obs)
            phase_method = "P2" if method == "J4_D3_P2_joint" else "P1"
            corrected_decision = classify_state(corrected_obs, gate, phase_method)
            if corrected_decision["state"] == DECORRELATED:
                if delay_path.is_file():
                    delay_path.unlink()
                base_row.update({"status": "fallback_current",
                                 "fallback": True,
                                 "fallback_reason": "decorrelated_after_delay_correction"})
                return base_row
            phase_active = bool(corrected_decision["phase_active"])
            phase_values = _phase_for_method(corrected_obs, phase_method) if phase_active else None
            steps = delay_steps
            if phase_values is not None:
                phase_steps = apply_raw_correction(delay_path, corrected_raw, source_xml,
                                                   None, phase_values)
                steps += phase_steps
            else:
                shutil.copyfile(delay_path, corrected_raw)
            if delay_path.is_file():
                delay_path.unlink()
        else:
            phase_method = "P2" if method == "J4_D3_P2_joint" else "P1"
            phase_values = (_phase_for_method(initial, phase_method)
                            if decision["apply_phase"] else None)
            steps = apply_raw_correction(source_raw, corrected_raw, source_xml,
                                         None, phase_values)
    else:
        steps = apply_raw_correction(source_raw, corrected_raw, source_xml,
                                     delay_ns, phase_values)

    result_dir = method_dir / "stage2/algorithm_result/period_0000"
    output_xml = method_dir / "stage2/config/joint_calibration_config.xml"
    patch_xml(source_xml, corrected_raw, result_dir, output_xml)
    log_path = method_dir / "gmticore.log"
    return_code = run_logged(
        [str(build_dir.resolve() / "GMTI_core"), str(output_xml),
         "--runtime-mode=debug", "--runtime-diagnostics=on"], log_path)
    base_row.update({
        "correction_steps": steps,
        "reestimated_phase": method in {"J3_D3_P1_joint", "J4_D3_P2_joint"},
        "intermediate_observables": intermediate,
        "phase_method": phase_method,
        "gmticore_exit_code": return_code,
    })
    if return_code != 0:
        base_row["status"] = "gmticore_failed"
        if corrected_raw.is_file():
            corrected_raw.unlink()
        return base_row
    manifest = single_manifest(method_dir)
    estimated = summary_metrics(manifest)
    base_row.update({
        "status": "pass",
        "manifest": str(manifest.resolve()),
        "estimated_cancellation_db": estimated.get("cancellation_db"),
        "headroom_gain_db_vs_current": estimated.get("cancellation_db", math.nan) - current.get("cancellation_db", math.nan),
        "estimated_coherence": estimated.get("coherence"),
        "estimated_phase_rmse_rad": estimated.get("phase_rmse_rad"),
        "correction_activation": {
            "delay": delay_ns is not None,
            "phase": phase_values is not None,
        },
    })
    if corrected_raw.is_file():
        corrected_raw.unlink()
    return base_row


def write_rows(output: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "method", "status", "state", "fallback", "fallback_reason",
        "delay_active", "phase_active", "estimated_delay_ns",
        "estimated_phase_slope_deg_per_pulse", "current_cancellation_db",
        "estimated_cancellation_db", "headroom_gain_db_vs_current",
        "current_coherence", "estimated_coherence", "current_phase_rmse_rad",
        "estimated_phase_rmse_rad", "reestimated_phase", "phase_method",
        "gmticore_exit_code", "manifest", "current_manifest",
    ]
    with (output / "joint_calibration_summary.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True,
                        help="one production target-on/off case directory")
    parser.add_argument("--gate-json", type=Path, required=True,
                        help="null-calibrated gate JSON")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--prf-hz", type=float, default=1300.0)
    parser.add_argument("--raw-support-percentile", type=float, default=80.0)
    parser.add_argument("--method", dest="methods", action="append",
                        choices=ALL_METHODS,
                        help="repeat to select methods; default is J0-J4")
    args = parser.parse_args()
    case = args.case.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    gate = json.loads(args.gate_json.resolve().read_text(encoding="utf-8"))
    methods = args.methods or list(METHODS)
    source_raw, source_xml, initial = case_observables(
        case, args.prf_hz, args.raw_support_percentile)
    current_manifest_path, current = current_metrics(case)
    rows: list[dict[str, Any]] = []
    for method in methods:
        rows.append(replay_method(
            case, method, output, gate, initial, source_raw, source_xml,
            current_manifest_path, current, args.build_dir.resolve(),
            args.prf_hz, args.raw_support_percentile))
    write_rows(output, rows)
    (output / "joint_observables.json").write_text(
        json.dumps(json_safe({
            "schema_version": 1,
            "case": str(case),
            "truth_used_in_estimator": False,
            "observables": compact_observables(initial),
            "gate": gate,
        }), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "method_version": "Physics-Adaptive Joint Calibration V1",
        "ai_training": False,
        "truth_used_in_estimator": False,
        **git_provenance(ROOT),
        "case": str(case),
        "gate": str(args.gate_json.resolve()),
        "methods": methods,
        "rows": rows,
        "definitions": {
            "J0_Current": "production Current with no correction",
            "J1_D3_delay_only": "raw D3 delay estimate followed by independent frequency-domain correction",
            "J2_P1_phase_only": "raw P1 phase trajectory followed by per-pulse frequency-domain correction",
            "J3_D3_P1_joint": "D3 correction, corrected-raw P1 re-estimation, then phase correction",
            "J4_D3_P2_joint": "D3 correction, corrected-raw P2 re-estimation, then phase correction",
            "fallback": "UNCERTAIN/DECORRELATED/null-deadband returns Current metrics",
        },
    }
    (output / "joint_calibration_manifest.json").write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    failed = sum(row.get("status") == "gmticore_failed" for row in rows)
    print(json.dumps({"output_dir": str(output), "case": str(case),
                      "methods": methods, "failed": failed}, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
