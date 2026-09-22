#!/usr/bin/env python3
"""Targeted equivalent-vs-physical calibration mechanism pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shlex
import shutil
import sys
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analyze_equivalent_vs_physical_calibration import (  # noqa: E402
    PHYSICAL_STATUS_VOCABULARY,
    emit_evidence,
)
from scripts.two_channel_complex_calibration import (  # noqa: E402
    CalibrationEstimate,
    apply_complex_calibration,
    estimate_ddc,
    estimate_ddc_rb,
    estimate_robust_ddc,
    estimate_robust_ddc_rb,
    estimate_scc,
)


ESTIMATOR_INPUT_PATHS = ["F1", "F2", "clutter_support"]
NOT_EVALUABLE = "NOT_EVALUABLE"
EQUIVALENT_METHODS = ("M0", "M1", "M2", "M3", "M4", "M5", "M6")
PHYSICAL_METHODS = ("P1", "P2", "PK", "PK+R")


METHOD_NAMES = {
    "M0": "uncalibrated_subtraction_proxy",
    "M1": "ordinary_complex_subtraction",
    "M2": "SCC",
    "M3": "DDC",
    "M4": "Robust DDC",
    "M5": "DDC-RB",
    "M6": "Robust DDC-RB",
    "P1": "blind physical",
    "P2": "physical+robust",
    "PK": "known physical",
    "PK+R": "known physical+robust",
}


def _finite(value: float) -> float:
    return float(value) if math.isfinite(float(value)) else float("nan")


def _fmt(value: float) -> str:
    return f"{_finite(value):.12g}"


def coherence_metadata(coherence: float, threshold: float) -> dict[str, Any]:
    """Classify coherence metadata without changing any estimator calculation."""

    value = float(coherence)
    limit = float(threshold)
    if not math.isfinite(value):
        return {
            "status": NOT_EVALUABLE,
            "reason": "invalid_coherence",
            "coherence": None,
            "threshold": limit,
        }
    if value < limit:
        return {
            "status": NOT_EVALUABLE,
            "reason": "low_coherence",
            "coherence": value,
            "threshold": limit,
        }
    return {
        "status": "OK",
        "reason": "",
        "coherence": value,
        "threshold": limit,
    }


def residual_power(residual: np.ndarray, mask: np.ndarray) -> float:
    selected = residual[mask]
    if selected.size == 0:
        return float("nan")
    if not np.all(np.isfinite(selected)):
        return float("nan")
    return float(np.mean(np.abs(selected) ** 2))


def deterministic_f1(rows: int, cols: int) -> np.ndarray:
    row = np.arange(rows, dtype=np.float64)[:, np.newaxis]
    col = np.arange(cols, dtype=np.float64)[np.newaxis, :]
    real = 1.0 + 0.07 * row + 0.11 * col
    imag = 0.2 + 0.05 * row - 0.03 * col
    return real + 1j * imag


def target_mask(shape: tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape, dtype=bool)
    if shape[0] >= 3 and shape[1] >= 3:
        mask[shape[0] // 2, shape[1] // 2] = True
    return mask


def expand_gamma(gamma: complex | np.ndarray, shape: tuple[int, int], range_band_size: int) -> np.ndarray:
    values = np.asarray(gamma, dtype=np.complex128)
    if values.ndim == 0:
        return np.full(shape, complex(values), dtype=np.complex128)
    if values.ndim == 1:
        return np.broadcast_to(values[np.newaxis, :], shape).astype(np.complex128)
    if values.ndim == 2:
        if values.shape[1] != shape[1]:
            raise ValueError("Gamma Doppler axis does not match the requested shape")
        expanded = np.empty(shape, dtype=np.complex128)
        for band_index in range(values.shape[0]):
            start = band_index * range_band_size
            stop = min(shape[0], start + range_band_size)
            expanded[start:stop, :] = values[band_index][np.newaxis, :]
        return expanded
    raise ValueError("cannot expand Gamma with ndim > 2")


def gamma_abs_error(
    estimate: CalibrationEstimate | None,
    gamma_truth: np.ndarray,
    support: np.ndarray,
    range_band_size: int,
) -> float:
    if estimate is None or estimate.status == NOT_EVALUABLE:
        return float("nan")
    target_shape = support.shape
    truth = np.broadcast_to(np.asarray(gamma_truth, dtype=np.complex128), target_shape)
    expanded = expand_gamma(estimate.gamma, target_shape, range_band_size)
    finite = np.isfinite(expanded.real) & np.isfinite(expanded.imag) & support
    if not bool(np.any(finite)):
        return float("nan")
    return float(np.max(np.abs(expanded[finite] - truth[finite])))


def orthogonal_component(f1: np.ndarray, range_band_size: int) -> np.ndarray:
    row = np.arange(f1.shape[0], dtype=np.float64)[:, np.newaxis]
    col = np.arange(f1.shape[1], dtype=np.float64)[np.newaxis, :]
    raw = np.sin(0.31 * row + 0.77 * col) + 1j * np.cos(0.23 * row - 0.41 * col)
    result = np.empty_like(f1)
    for start in range(0, f1.shape[0], range_band_size):
        stop = min(f1.shape[0], start + range_band_size)
        for doppler in range(f1.shape[1]):
            base = f1[start:stop, doppler]
            candidate = raw[start:stop, doppler]
            denominator = np.sum(np.abs(base) ** 2)
            projection = np.sum(candidate * np.conj(base)) / denominator
            residual = candidate - projection * base
            scale = math.sqrt(float(np.mean(np.abs(residual) ** 2)))
            result[start:stop, doppler] = 0.28 * residual / scale
    return result


def empirical_coherence(first: np.ndarray, second: np.ndarray, mask: np.ndarray) -> float:
    first_selected = first[mask]
    second_selected = second[mask]
    denominator = math.sqrt(
        float(np.sum(np.abs(first_selected) ** 2) * np.sum(np.abs(second_selected) ** 2))
    )
    if denominator <= np.finfo(np.float64).eps:
        return float("nan")
    return float(abs(np.sum(second_selected * np.conj(first_selected))) / denominator)


def case_gamma(
    mechanism: str,
    rows: int,
    cols: int,
    range_band_size: int,
    fast_time_frequency_cycles_per_sample: np.ndarray,
    delay_samples: float,
) -> np.ndarray:
    row = np.arange(rows, dtype=np.float64)[:, np.newaxis]
    col = np.arange(cols, dtype=np.float64)[np.newaxis, :]
    if mechanism == "pure_equivalent_mismatch":
        result = np.full((rows, cols), 1.12 - 0.28j, dtype=np.complex128)
    elif mechanism == "fast_time_channel_delay":
        phase = -2.0 * np.pi * fast_time_frequency_cycles_per_sample[:, np.newaxis] * delay_samples
        result = np.exp(1j * phase)
    elif mechanism == "servo_pointing":
        band = (row // range_band_size).astype(np.float64)
        result = (0.94 + 0.035 * band) * np.exp(1j * (0.09 * band + 0.015 * col))
    elif mechanism == "platform_velocity":
        result = np.exp(1j * (0.16 * col)) * (1.0 + 0.012 * col)
    elif mechanism == "true_decorrelation":
        result = np.full((rows, cols), 0.98 + 0.14j, dtype=np.complex128)
    else:
        raise ValueError(f"unknown mechanism: {mechanism}")
    return np.broadcast_to(result, (rows, cols)).astype(np.complex128).copy()


def build_case(
    case_cfg: dict[str, Any],
    rows: int,
    cols: int,
    range_band_size: int,
    fast_time_frequency_cycles_per_sample: np.ndarray,
) -> dict[str, Any]:
    mechanism = case_cfg["mechanism"]
    f1_off = deterministic_f1(rows, cols)
    physical_truth = float(case_cfg.get("physical_truth", 0.0))
    gamma_truth = case_gamma(
        mechanism,
        rows,
        cols,
        range_band_size,
        fast_time_frequency_cycles_per_sample,
        physical_truth,
    )
    support = np.ones((rows, cols), dtype=bool)
    target = target_mask((rows, cols))
    clutter_mask = support & ~target
    f2_off = gamma_truth * f1_off
    floor = 0.0
    coherence = 1.0
    if mechanism == "true_decorrelation":
        decorrelated = orthogonal_component(f1_off, range_band_size)
        f2_off = f2_off + decorrelated
        floor = residual_power(decorrelated, clutter_mask)
    coherence = empirical_coherence(f1_off, f2_off, clutter_mask)
    f1_on = f1_off.copy()
    f2_on = f2_off.copy()
    target_signal = (9.0 + 0.25 * np.arange(np.count_nonzero(target))) * np.exp(1j * 0.35)
    f1_on[target] += target_signal
    f2_on[target] += 0.78 * np.exp(1j * 1.85) * target_signal
    sensor_prior_value = case_cfg.get("sensor_prior_value")
    sensor_observable_values = (
        np.full(rows, float(sensor_prior_value), dtype=np.float64)
        if sensor_prior_value is not None
        else None
    )
    return {
        "case_id": case_cfg["case_id"],
        "label": case_cfg["label"],
        "mechanism": mechanism,
        "f1": f1_off,
        "f1_off": f1_off,
        "f1_on": f1_on,
        "f2_off": f2_off,
        "f2_on": f2_on,
        "support": support,
        "target_mask": target,
        "clutter_mask": clutter_mask,
        "gamma_truth": gamma_truth,
        "decorrelation_floor": floor,
        "empirical_coherence": coherence,
        "physical_observable": case_cfg["physical_observable"],
        "observable_domain": case_cfg["observable_domain"],
        "physical_truth": physical_truth,
        "physical_unit": case_cfg.get("physical_unit", ""),
        "fast_time_frequency_cycles_per_sample": fast_time_frequency_cycles_per_sample,
        "sensor_observable_values": sensor_observable_values,
    }


def estimate_equivalent(
    method_id: str,
    f1: np.ndarray,
    f2: np.ndarray,
    support: np.ndarray,
    *,
    min_support: int,
    range_band_size: int,
    phase_threshold_rad: float,
) -> CalibrationEstimate | None:
    if method_id in {"M0", "M1"}:
        return None
    if method_id == "M2":
        return estimate_scc(f1, f2, support, min_support=min_support)
    if method_id == "M3":
        return estimate_ddc(f1, f2, support, min_support=min_support)
    if method_id == "M4":
        return estimate_robust_ddc(
            f1,
            f2,
            support,
            min_support=min_support,
            phase_threshold_rad=phase_threshold_rad,
        )
    if method_id == "M5":
        return estimate_ddc_rb(
            f1,
            f2,
            support,
            min_support=min_support,
            range_band_size=range_band_size,
        )
    if method_id == "M6":
        return estimate_robust_ddc_rb(
            f1,
            f2,
            support,
            min_support=min_support,
            range_band_size=range_band_size,
            phase_threshold_rad=phase_threshold_rad,
        )
    raise ValueError(f"unsupported equivalent method_id: {method_id}")


def residual_for_method(case: dict[str, Any], method_id: str, estimate: CalibrationEstimate | None) -> np.ndarray:
    if method_id in {"M0", "M1"}:
        return case["f2_on"] - case["f1_on"]
    assert estimate is not None
    return apply_complex_calibration(case["f1_on"], case["f2_on"], estimate, allow_invalid=True)


def _estimate_delay_samples(case: dict[str, Any]) -> float:
    frequency = case["fast_time_frequency_cycles_per_sample"]
    cross = case["f2_off"] * np.conj(case["f1_off"])
    phase = np.unwrap(np.angle(np.sum(cross, axis=1)))
    slope, _ = np.polyfit(frequency, phase, 1)
    estimate = -float(slope) / (2.0 * np.pi)
    return estimate if np.isfinite(estimate) else float("nan")


def _estimate_physical_state(case: dict[str, Any]) -> float:
    if case["mechanism"] == "fast_time_channel_delay":
        return _estimate_delay_samples(case)
    if case["sensor_observable_values"] is not None:
        return float(np.mean(case["sensor_observable_values"]))
    return float("nan")


def _physical_correction_f2(case: dict[str, Any], f2: np.ndarray, estimate: float) -> np.ndarray:
    if case["mechanism"] != "fast_time_channel_delay":
        raise ValueError("no physical correction model is registered for this mechanism")
    frequency = case["fast_time_frequency_cycles_per_sample"]
    compensating_phase = np.exp(1j * 2.0 * np.pi * frequency[:, np.newaxis] * estimate)
    return f2 * compensating_phase


def _physical_method_result(
    case: dict[str, Any],
    method_id: str,
    *,
    min_support: int,
    range_band_size: int,
    phase_threshold_rad: float,
) -> dict[str, Any]:
    physical_mechanisms = {"fast_time_channel_delay", "servo_pointing", "platform_velocity"}
    if case["mechanism"] not in physical_mechanisms:
        return {
            "status": NOT_EVALUABLE,
            "estimate": float("nan"),
            "error": float("nan"),
            "residual_power": float("nan"),
            "physical_correction_applied": False,
            "estimator_input_paths": ["physical_observable_values"],
            "truth_used_in_estimator": False,
        }

    truth = float(case["physical_truth"])
    known = method_id in {"PK", "PK+R"}
    blind = method_id in {"P1", "P2"}
    if not (known or blind):
        return {
            "status": NOT_EVALUABLE,
            "estimate": float("nan"),
            "error": float("nan"),
            "residual_power": float("nan"),
            "physical_correction_applied": False,
            "estimator_input_paths": [],
            "truth_used_in_estimator": False,
        }

    input_paths = (
        ["F1", "F2", "fast_time_frequency_cycles_per_sample"]
        if case["mechanism"] == "fast_time_channel_delay" and blind
        else ["physical_observable_values"] if blind else ["known_error_state"]
    )
    estimate = truth if known else _estimate_physical_state(case)
    if not np.isfinite(estimate):
        return {
            "status": NOT_EVALUABLE,
            "estimate": float("nan"),
            "error": float("nan"),
            "residual_power": float("nan"),
            "physical_correction_applied": False,
            "estimator_input_paths": input_paths,
            "truth_used_in_estimator": not blind,
        }

    error = abs(float(estimate) - truth)
    needs_correction = method_id in {"P2", "PK+R"}
    if needs_correction and case["mechanism"] != "fast_time_channel_delay":
        return {
            "status": NOT_EVALUABLE,
            "estimate": float(estimate),
            "error": error,
            "residual_power": float("nan"),
            "physical_correction_applied": False,
            "estimator_input_paths": input_paths,
            "truth_used_in_estimator": not blind,
        }

    if not needs_correction:
        status = (
            "KNOWN_TRUTH"
            if known
            else "RADAR_ESTIMATED"
            if case["mechanism"] == "fast_time_channel_delay"
            else "SENSOR_PRIOR_ONLY"
        )
        return {
            "status": status,
            "estimate": float(estimate),
            "error": error,
            "residual_power": float("nan"),
            "physical_correction_applied": False,
            "estimator_input_paths": input_paths,
            "truth_used_in_estimator": not blind,
        }

    corrected_f2 = _physical_correction_f2(case, case["f2_on"], float(estimate))
    robust = estimate_robust_ddc_rb(
        case["f1_on"],
        corrected_f2,
        case["support"],
        min_support=min_support,
        range_band_size=range_band_size,
        phase_threshold_rad=phase_threshold_rad,
    )
    residual = apply_complex_calibration(case["f1_on"], corrected_f2, robust, allow_invalid=True)
    status = (
        "KNOWN_TRUTH"
        if known and robust.status in {"OK", "PARTIAL"}
        else "RADAR_ESTIMATED"
        if not known and robust.status in {"OK", "PARTIAL"}
        else NOT_EVALUABLE
    )
    return {
        "status": status,
        "estimate": float(estimate),
        "error": error,
        "residual_power": residual_power(residual, case["clutter_mask"]),
        "physical_correction_applied": status != NOT_EVALUABLE,
        "estimator_input_paths": input_paths + ["F1", "F2", "clutter_support"],
        "truth_used_in_estimator": not blind,
    }


def build_observations(config: dict[str, Any]) -> dict[str, Any]:
    rows = int(config["dimensions"]["range_bins"])
    cols = int(config["dimensions"]["doppler_bins"])
    range_band_size = int(config["dimensions"]["range_band_size"])
    min_support = int(config["support"]["min_support"])
    phase_threshold_rad = float(config["robust"]["phase_threshold_rad"])
    pilot_cfg = config["mechanism_pilot"]
    cases_cfg = pilot_cfg["cases"]
    modes = config["modes"]
    frequency_cfg = pilot_cfg["fast_time_frequency_cycles_per_sample"]
    fast_time_frequency_cycles_per_sample = np.linspace(
        float(frequency_cfg["start"]),
        float(frequency_cfg["stop"]),
        rows,
        endpoint=bool(frequency_cfg.get("endpoint", False)),
        dtype=np.float64,
    )

    gamma_rows: list[dict[str, Any]] = []
    clutter_rows: list[dict[str, Any]] = []
    audit_entries: list[dict[str, Any]] = []
    cases_summary: list[dict[str, str]] = []
    mode_separation_audit: dict[str, dict[str, Any]] = {}
    mechanism_observable_audit: dict[str, dict[str, Any]] = {}
    coherence_metadata_by_mechanism: dict[str, dict[str, Any]] = {}

    for case_cfg in cases_cfg:
        case = build_case(
            case_cfg,
            rows,
            cols,
            range_band_size,
            fast_time_frequency_cycles_per_sample,
        )
        case["coherence_metadata"] = coherence_metadata(
            case["empirical_coherence"],
            float(config["thresholds"]["coherence_floor_min"]),
        )
        coherence_metadata_by_mechanism[case["mechanism"]] = case["coherence_metadata"]
        cases_summary.append(
            {
                "case_id": case["case_id"],
                "label": case["label"],
                "mechanism": case["mechanism"],
                "physical_observable": case["physical_observable"],
                "observable_domain": case["observable_domain"],
                "target_cell_count": int(np.count_nonzero(case["target_mask"])),
                "coherence_metadata": case["coherence_metadata"],
            }
        )
        mechanism_observable_audit[case["mechanism"]] = {
            "observable_domain": case["observable_domain"],
            "physical_estimator_input_paths": (
                ["F1", "F2", "fast_time_frequency_cycles_per_sample"]
                if case["mechanism"] == "fast_time_channel_delay"
                else ["physical_observable_values"]
            ),
        }
        for mode_cfg in modes:
            mode = mode_cfg["mode_id"]
            estimator_f1 = case["f1_off"] if mode == "Mode-A" else case["f1_on"]
            estimator_f2 = case["f2_off"] if mode == "Mode-A" else case["f2_on"]
            mode_separation_audit[mode] = {
                "estimator_source": mode_cfg["estimator_source"],
                "apply_source": mode_cfg["apply_source"],
                "off_on_observables_distinct": bool(
                    not np.array_equal(case["f1_off"], case["f1_on"])
                    or not np.array_equal(case["f2_off"], case["f2_on"])
                ),
            }
            for method_id in EQUIVALENT_METHODS:
                estimate = estimate_equivalent(
                    method_id,
                    estimator_f1,
                    estimator_f2,
                    case["support"],
                    min_support=min_support,
                    range_band_size=range_band_size,
                    phase_threshold_rad=phase_threshold_rad,
                )
                residual = residual_for_method(case, method_id, estimate)
                power = residual_power(residual, case["clutter_mask"])
                gamma_error = gamma_abs_error(
                    estimate,
                    case["gamma_truth"],
                    case["support"],
                    range_band_size,
                )
                status = estimate.status if estimate is not None else "OK"
                truth_blind = False if estimate is None else bool(
                    estimate.metadata.get("truth_used_in_estimator", True)
                )
                gamma_rows.append(
                    {
                        "case_id": case["case_id"],
                        "mechanism": case["mechanism"],
                        "mode": mode,
                        "method_id": method_id,
                        "method_name": METHOD_NAMES[method_id],
                        "status": status,
                        "gamma_error_abs_max": _fmt(gamma_error),
                        "residual_power": _fmt(power),
                        "physical_observable": case["physical_observable"],
                        "observable_domain": case["observable_domain"],
                        "calibration_scope": "equivalent_residual_only",
                        "physical_status": NOT_EVALUABLE,
                        "physical_estimate": "",
                        "physical_error": "",
                        "range_registration_error_samples": "",
                        "doppler_only_claim": "false",
                        "physical_correction_applied": "false",
                        "physical_estimator_input_paths": "",
                        "truth_used_in_estimator": str(truth_blind).lower(),
                        "estimator_input_paths": ";".join(ESTIMATOR_INPUT_PATHS if estimate is not None else []),
                        "support_count": estimate.support_count if estimate is not None else int(np.count_nonzero(case["support"])),
                        "excluded_count": int(estimate.metadata.get("excluded_count", 0)) if estimate is not None else 0,
                    }
                )
                clutter_rows.append(
                    {
                        "case_id": case["case_id"],
                        "mechanism": case["mechanism"],
                        "mode": mode,
                        "method_id": method_id,
                        "method_name": METHOD_NAMES[method_id],
                        "status": status,
                        "clutter_residual_power": _fmt(power),
                        "single_coefficient_residual_floor": _fmt(case["decorrelation_floor"]),
                        "empirical_coherence": _fmt(case["empirical_coherence"]),
                        "irreducible_decorrelation_status": (
                            "OK"
                            if case["mechanism"] == "true_decorrelation"
                            and math.isfinite(power)
                            and abs(power - case["decorrelation_floor"])
                            <= max(1e-12, case["decorrelation_floor"] * 1e-8)
                            else "NOT_EVALUABLE"
                            if case["mechanism"] == "true_decorrelation"
                            else ""
                        ),
                        "physical_state_claim": "false",
                        "observable_domain": case["observable_domain"],
                        "reason": "equivalent calibration row; physical-state recovery is not claimed from clutter residual alone",
                    }
                )
                if estimate is not None:
                    audit_entries.append(
                        {
                            "case_id": case["case_id"],
                            "mode": mode,
                            "method_id": method_id,
                            "truth_used_in_estimator": truth_blind,
                            "estimator_input_paths": ESTIMATOR_INPUT_PATHS,
                        }
                    )
        for method_id in PHYSICAL_METHODS:
            physical = _physical_method_result(
                case,
                method_id,
                min_support=min_support,
                range_band_size=range_band_size,
                phase_threshold_rad=phase_threshold_rad,
            )
            physical_status = physical["status"]
            physical_estimate = physical["estimate"]
            physical_error = physical["error"]
            range_error = (
                physical_error
                if case["physical_observable"] == "fast_time_range_registration" and math.isfinite(physical_error)
                else float("nan")
            )
            gamma_rows.append(
                {
                    "case_id": case["case_id"],
                    "mechanism": case["mechanism"],
                    "mode": "Mode-A" if method_id == "P1" else "Mode-B" if method_id == "P2" else "evaluator",
                    "method_id": method_id,
                    "method_name": METHOD_NAMES[method_id],
                    "status": physical_status,
                    "gamma_error_abs_max": "",
                    "residual_power": _fmt(physical["residual_power"]),
                    "physical_observable": case["physical_observable"],
                    "observable_domain": case["observable_domain"],
                    "calibration_scope": "physical_state_only",
                    "physical_status": physical_status,
                    "physical_estimate": _fmt(physical_estimate),
                    "physical_error": _fmt(physical_error),
                    "range_registration_error_samples": _fmt(range_error),
                    "doppler_only_claim": "false",
                    "physical_correction_applied": str(physical["physical_correction_applied"]).lower(),
                    "physical_estimator_input_paths": ";".join(physical["estimator_input_paths"]),
                    "truth_used_in_estimator": str(physical["truth_used_in_estimator"]).lower(),
                    "estimator_input_paths": ";".join(physical["estimator_input_paths"]),
                    "support_count": "",
                    "excluded_count": "",
                }
            )
            if (
                method_id in {"P1", "P2"}
                and physical_status in PHYSICAL_STATUS_VOCABULARY
                and physical_status != NOT_EVALUABLE
            ):
                audit_entries.append(
                    {
                        "case_id": case["case_id"],
                        "mode": "Mode-A" if method_id == "P1" else "Mode-B",
                        "method_id": method_id,
                        "truth_used_in_estimator": physical["truth_used_in_estimator"],
                        "estimator_input_paths": physical["estimator_input_paths"],
                    }
                )
            if method_id in {"P2", "PK+R"} and physical_status in {"OK", "PARTIAL"}:
                clutter_rows.append(
                    {
                        "case_id": case["case_id"],
                        "mechanism": case["mechanism"],
                        "mode": "Mode-B" if method_id == "P2" else "evaluator",
                        "method_id": method_id,
                        "method_name": METHOD_NAMES[method_id],
                        "status": physical_status,
                        "clutter_residual_power": _fmt(physical["residual_power"]),
                        "single_coefficient_residual_floor": "",
                        "empirical_coherence": _fmt(case["empirical_coherence"]),
                        "irreducible_decorrelation_status": "",
                        "physical_state_claim": "true",
                        "observable_domain": case["observable_domain"],
                        "reason": "physical correction followed by robust residual calibration; downstream state/track safety remains separate",
                    }
                )

    decorrelation_checks = [
        row
        for row in clutter_rows
        if row["mechanism"] == "true_decorrelation"
        and row["mode"] == "Mode-A"
        and row["method_id"] in {"M2", "M6"}
    ]
    floor_check_status = "passed" if decorrelation_checks and all(
        row["irreducible_decorrelation_status"] == "OK" for row in decorrelation_checks
    ) else "failed"
    return {
        "seed": config["seed"],
        "dimensions": config["dimensions"],
        "cases": cases_summary,
        "modes": modes,
        "gamma_rows": gamma_rows,
        "clutter_rows": clutter_rows,
        "truth_blind_entries": audit_entries,
        "historical_references": pilot_cfg["historical_references"],
        "thresholds": config["thresholds"],
        "parameters": {
            "min_support": min_support,
            "range_band_size": range_band_size,
            "phase_threshold_rad": phase_threshold_rad,
            "thresholds": config["thresholds"],
            "fast_time_frequency_cycles_per_sample": {
                "start": float(frequency_cfg["start"]),
                "stop": float(frequency_cfg["stop"]),
                "endpoint": bool(frequency_cfg.get("endpoint", False)),
            },
        },
        "cleanup_policy": (
            "raw arrays are never written; retain compact JSON/CSV/Markdown evidence; "
            "copy pre-existing Task 3 same-named evidence under sanity/ before Task 4 output"
        ),
        "physical_correction_status": config.get("downstream_status", NOT_EVALUABLE),
        "coherence_metadata": coherence_metadata_by_mechanism,
        "mode_separation_audit": mode_separation_audit,
        "mechanism_observable_audit": {
            **mechanism_observable_audit,
            "fast_time_channel_delay": {
                **mechanism_observable_audit["fast_time_channel_delay"],
                "phase_model": "exp(-j*2*pi*f*delay_samples)",
                "frequency_axis_count": int(fast_time_frequency_cycles_per_sample.size),
            },
        },
        "physical_estimator_audit": {
            "blind_rows_use_observable_values": True,
            "blind_rows_read_evaluator_truth": False,
        },
        "decorrelation_audit": {
            "empirical_coherence_source": "cross_correlation(F1,F2,clutter_support)",
            "floor_check_status": floor_check_status,
            "checked_methods": ["M2", "M6"],
        },
        "preserved_sanity_evidence": [],
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preserve_existing_sanity_evidence(output_root: Path) -> list[dict[str, Any]]:
    """Keep Task 3's same-named compact evidence before Task 4 writes its schema."""

    manifest = output_root / "sanity_manifest.json"
    if not manifest.is_file():
        return []
    preserve_root = output_root / "sanity"
    preserve_root.mkdir(parents=True, exist_ok=True)
    preserved: list[dict[str, Any]] = []
    for filename in ("sanity_manifest.json", "gamma_recovery.csv", "clutter_metrics.csv", "target_transfer.csv"):
        source = output_root / filename
        if not source.is_file():
            continue
        destination = preserve_root / filename
        if not destination.exists():
            shutil.copy2(source, destination)
        preserved.append(
            {
                "source": filename,
                "destination": str(destination.relative_to(output_root)),
                "sha256": _sha256_file(destination),
                "size_bytes": destination.stat().st_size,
            }
        )
    return preserved


def provenance(
    config_path: Path,
    command: list[str],
    preserved_sanity_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    config_bytes = config_path.read_bytes()
    commit_result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, capture_output=True, check=False
    )
    status_result = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": shlex.join(command),
        "exit_code": 0,
        "source_commit": commit_result.stdout.strip() if commit_result.returncode == 0 else "UNKNOWN",
        "dirty_worktree": bool(status_result.stdout.strip()) if status_result.returncode == 0 else None,
        "config_path": str(config_path),
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "input_identity": {
            "kind": "deterministic synthetic F1/F2 mechanism arrays held in memory",
            "raw_arrays_written": False,
            "seed": None,
        },
        "preserved_sanity_evidence": preserved_sanity_evidence,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    output_root = args.output_root if args.output_root is not None else Path(config["evidence_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    preserved_sanity_evidence = preserve_existing_sanity_evidence(output_root)
    observations = build_observations(config)
    observations["preserved_sanity_evidence"] = preserved_sanity_evidence
    run_provenance = provenance(args.config, [sys.executable, *sys.argv], preserved_sanity_evidence)
    run_provenance["input_identity"]["seed"] = config["seed"]
    observations["provenance"] = run_provenance
    (output_root / "observations.json").write_text(
        json.dumps(observations, indent=2, sort_keys=True), encoding="utf-8"
    )
    manifest = emit_evidence(observations, output_root)
    manifest["provenance"]["exit_code"] = 0 if manifest["pilot_status"] == "passed" else 1
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps({"output_root": str(output_root), "pilot_status": manifest["pilot_status"], "decision_label": manifest["decision_label"]}, sort_keys=True))
    return 0 if manifest["pilot_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
