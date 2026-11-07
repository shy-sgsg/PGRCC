#!/usr/bin/env python3
"""Audit the existing R21 blind M1 estimate offline; this script never runs CUDA."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.phase_r import physics_matched_delay as physics  # noqa: E402
from scripts.phase_r import run_r12_physics_matched_delay as r12  # noqa: E402
from scripts import run_delay_stage1_formal as stage1  # noqa: E402


SEED_CASES = {
    1001: "seed1001_gatefixed_6f261b2",
    1002: "seed1002_0957f49",
}
RETAINED_MANIFEST = (
    ROOT / "docs/research/phase_r/R21/evidence/inputs/"
    "r21_retained_binary_manifest.json"
)
EXPERIMENT_MANIFEST = ROOT / "docs/research/phase_r/R21/evidence/results/r21_experiment_manifest.json"
DEFAULT_OUTPUT = ROOT / "docs/research/phase_r/R21/evidence/results"
ANALYSIS_SOURCE_PATHS = (
    Path(__file__).resolve(),
    ROOT / "scripts/phase_r/run_r12_physics_matched_delay.py",
    ROOT / "scripts/phase_r/physics_matched_delay.py",
    ROOT / "scripts/estimate_channel_delay.py",
    ROOT / "scripts/run_delay_stage1_formal.py",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _case(seed: int) -> tuple[Path, dict[str, Any]]:
    folder = SEED_CASES[seed]
    case_root = (
        ROOT / "docs/research/phase_r/R21/evidence/cuda_pilot" / folder
        / f"cases/base__seed_{seed}__v_p6d7__snr_p30__delay_p4ns"
    )
    manifest_path = case_root / "case_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing retained R21 case manifest: {manifest_path}")
    return case_root, json.loads(manifest_path.read_text(encoding="utf-8"))


def _verify_inputs(raw_paths: list[Path]) -> list[dict[str, Any]]:
    manifest = json.loads(RETAINED_MANIFEST.read_text(encoding="utf-8"))
    records = {
        (ROOT / item["path"]).resolve(): item
        for item in manifest["files"]
    }
    verified = []
    for path in raw_paths:
        resolved = path.resolve()
        record = records.get(resolved)
        if record is None:
            raise ValueError(f"raw input is absent from retained-binary manifest: {path}")
        if not resolved.is_file():
            raise FileNotFoundError(f"retained raw input is missing: {resolved}")
        actual_hash = _sha256(resolved)
        if actual_hash != record["sha256"]:
            raise ValueError(f"retained raw input hash mismatch: {resolved}")
        if resolved.stat().st_size != int(record["bytes"]):
            raise ValueError(f"retained raw input size mismatch: {resolved}")
        verified.append({
            "path": resolved.relative_to(ROOT).as_posix(),
            "size_bytes": resolved.stat().st_size,
            "sha256": actual_hash,
        })
    return verified


def _load_protocol_fusions(path: Path, layout: dict[str, Any]) -> tuple[np.ndarray, ...]:
    f1_rows: list[np.ndarray] = []
    f2_rows: list[np.ndarray] = []
    c2_rows: list[np.ndarray] = []
    c4_rows: list[np.ndarray] = []
    for packet in stage1.iter_raw_packets(
        path,
        int(layout["pulse_len"]),
        int(layout["channel_count"]),
        str(layout["iq_data_type"]),
    ):
        channels = np.asarray(packet["channels"], dtype=np.complex128)
        if channels.shape != (int(layout["pulse_len"]), 4):
            raise ValueError(f"unexpected protocol channel shape in {path}: {channels.shape}")
        c2, c4 = channels[:, 1], channels[:, 3]
        f1_rows.append(0.5 * (channels[:, 0] + channels[:, 2]))
        f2_rows.append(0.5 * (c2 + c4))
        c2_rows.append(c2)
        c4_rows.append(c4)
    if not f1_rows:
        raise ValueError(f"no valid protocol packets in {path}")
    return tuple(np.stack(values, axis=0) for values in (f1_rows, f2_rows, c2_rows, c4_rows))


def _spectral_phase_summary(candidate: np.ndarray, reference: np.ndarray,
                            sample_rate_hz: float) -> dict[str, Any]:
    reference_spectrum = np.fft.fft(reference, axis=1)
    candidate_spectrum = np.fft.fft(candidate, axis=1)
    cross = np.sum(candidate_spectrum * np.conj(reference_spectrum), axis=0)
    magnitude = np.abs(cross)
    valid = np.isfinite(magnitude) & (magnitude > np.finfo(float).tiny)
    threshold = float(np.percentile(magnitude[valid], 80.0)) if np.any(valid) else math.inf
    support = valid & (magnitude >= threshold)
    frequency = np.fft.fftfreq(reference.shape[1], d=1.0 / sample_rate_hz)
    result = {}
    for label, sign_mask in (("negative", frequency < 0.0), ("positive", frequency > 0.0)):
        indices = np.flatnonzero(support & sign_mask)
        order = indices[np.argsort(frequency[indices])]
        if order.size < 3:
            result[label] = {"status": "NOT_EVALUABLE_INSUFFICIENT_SUPPORT", "support_bins": int(order.size)}
            continue
        phase = np.unwrap(np.angle(cross[order]))
        x = frequency[order]
        weights = magnitude[order]
        x_mean = float(np.average(x, weights=weights))
        phase_mean = float(np.average(phase, weights=weights))
        denominator = float(np.sum(weights * (x - x_mean) ** 2))
        slope = float(np.sum(weights * (x - x_mean) * (phase - phase_mean)) / denominator)
        phase_residual = phase - (phase_mean + slope * (x - x_mean))
        result[label] = {
            "status": "EVALUABLE_DESCRIPTIVE",
            "support_bins": int(order.size),
            "frequency_span_hz": float(np.ptp(x)),
            "cross_spectral_phase_rmse_rad_after_linear_fit": float(
                np.sqrt(np.average(phase_residual**2, weights=weights))
            ),
            "estimated_relative_delay_ns_evaluator_audit_only": float(
                -slope / (2.0 * math.pi) * 1.0e9
            ),
        }
    return result


def _f2_spectral_audit(seed: int, case_root: Path,
                       layout: dict[str, Any], true_delay_ns: float) -> list[dict[str, Any]]:
    conditions = (
        ("A0", "A0_Ideal"),
        ("B", "A1_Current_unknown_error"),
        ("Known", "A2_Known_error_correction_upper_bound"),
        ("D3_R9_fixed", "R21_D3_R9_Fixed"),
        ("D3_Huber", "R21_D3_Huber_B_ON"),
        ("M1", "R21_M1_Physics_Matched"),
    )
    delay_path = ROOT / "docs/research/phase_r/R21/evidence/results/r21_delay_estimates.csv"
    with delay_path.open(newline="", encoding="utf-8") as stream:
        delay_records = {
            row["method"]: row
            for row in csv.DictReader(stream)
            if row["seed"] == str(seed)
        }
    closure_path = ROOT / "docs/research/phase_r/R21/evidence/results/r21_f2_physical_closure.csv"
    with closure_path.open(newline="", encoding="utf-8") as stream:
        stored_closure = {
            row["canonical_condition"]: row
            for row in csv.DictReader(stream)
            if row["seed"] == str(seed) and row["role"] == "ON"
        }
    reference_path = case_root / "production/A0_Ideal_ON/input_merged.bin"
    reference_identity = _verify_inputs([reference_path])[0]
    reference_f1, reference_f2, reference_c2, reference_c4 = _load_protocol_fusions(reference_path, layout)
    reference_f1_power = float(np.mean(np.abs(reference_f1) ** 2))
    reference_f2_power = float(np.mean(np.abs(reference_f2) ** 2))
    reference_c2_power = float(np.mean(np.abs(reference_c2) ** 2))
    reference_c4_power = float(np.mean(np.abs(reference_c4) ** 2))
    rows = []
    for method, condition in conditions:
        candidate_path = case_root / "production" / f"{condition}_ON/input_merged.bin"
        candidate_identity = _verify_inputs([candidate_path])[0]
        candidate_f1, candidate_f2, candidate_c2, candidate_c4 = _load_protocol_fusions(candidate_path, layout)
        if candidate_f1.shape != reference_f1.shape or candidate_f2.shape != reference_f2.shape:
            raise ValueError(f"F1/F2 shape mismatch for {seed}/{method}")
        delta_c2 = candidate_c2 - reference_c2
        delta_c4 = candidate_c4 - reference_c4
        delta_f2 = 0.5 * (delta_c2 + delta_c4)
        f1_relative_rms = float(
            np.sqrt(np.mean(np.abs(candidate_f1 - reference_f1) ** 2) / reference_f1_power)
        )
        f2_relative_rms = float(np.sqrt(np.mean(np.abs(delta_f2) ** 2) / reference_f2_power))
        c2_relative_rms = float(np.sqrt(np.mean(np.abs(delta_c2) ** 2) / reference_c2_power))
        c4_relative_rms = float(np.sqrt(np.mean(np.abs(delta_c4) ** 2) / reference_c4_power))
        c2_error_power = float(np.mean(np.abs(delta_c2) ** 2))
        c4_error_power = float(np.mean(np.abs(delta_c4) ** 2))
        channel_cross_power = float(np.mean(np.real(delta_c2 * np.conj(delta_c4))))
        power_parts = {
            "channel2_quarter_error_power_over_f2_reference": 0.25 * c2_error_power / reference_f2_power,
            "channel4_quarter_error_power_over_f2_reference": 0.25 * c4_error_power / reference_f2_power,
            "channel2_channel4_cross_term_over_f2_reference": 0.5 * channel_cross_power / reference_f2_power,
            "direct_f2_error_power_over_reference": f2_relative_rms**2,
        }
        power_parts["identity_abs_error"] = abs(
            sum(power_parts[key] for key in (
                "channel2_quarter_error_power_over_f2_reference",
                "channel4_quarter_error_power_over_f2_reference",
                "channel2_channel4_cross_term_over_f2_reference",
            )) - power_parts["direct_f2_error_power_over_reference"]
        )
        closure_crosscheck = None
        if method != "A0":
            saved = stored_closure.get(method)
            if saved is None:
                raise ValueError(f"stored ON F2 closure row missing for {seed}/{method}")
            saved_f2 = float(saved["f2_relative_rms"])
            closure_crosscheck = abs(f2_relative_rms - saved_f2)
            if closure_crosscheck > 1.0e-10:
                raise ValueError(
                    f"recomputed F2 closure differs from saved result for {seed}/{method}: "
                    f"{f2_relative_rms} vs {saved_f2}"
                )
        applied_raw = delay_records.get(method, {}).get("applied_delay_ns")
        expected_residual = (
            0.0 if method == "A0" else
            true_delay_ns - float(applied_raw)
            if applied_raw not in (None, "") else None
        )
        rows.append({
            "seed": seed,
            "method": method,
            "condition": condition,
            "reference": "paired A0 fused F1/F2 and protocol channel 2/channel 4 inputs",
            "reference_input": reference_identity,
            "candidate_input": candidate_identity,
            "f1_relative_rms": f1_relative_rms,
            "f2_relative_rms": f2_relative_rms,
            "channel2_relative_rms": c2_relative_rms,
            "channel4_relative_rms": c4_relative_rms,
            "channel4_bitwise_unchanged": bool(np.array_equal(candidate_c4, reference_c4)),
            "f2_error_power_decomposition": power_parts,
            "saved_f2_relative_rms": (
                float(stored_closure[method]["f2_relative_rms"]) if method in stored_closure else 0.0
            ),
            "f2_closure_crosscheck_abs_difference": closure_crosscheck,
            "f2_closure_crosscheck_status": (
                "PASSED_MATCHED_SAVED_RESULT" if method != "A0" else "SELF_REFERENCE_ZERO"
            ),
            "frequency_support_percentile": 80.0,
            "fused_f2_phase_by_frequency_sign": _spectral_phase_summary(
                candidate_f2, reference_f2, float(layout["fs_hz"])
            ),
            "channel2_phase_by_frequency_sign": _spectral_phase_summary(
                candidate_c2, reference_c2, float(layout["fs_hz"])
            ),
            "expected_residual_delay_ns_evaluator_only": expected_residual,
            "truth_used_for_estimation": False,
            "truth_used_only_for_expected_residual_label": True,
        })
        del candidate_f1, candidate_f2, candidate_c2, candidate_c4, delta_c2, delta_c4, delta_f2
    return rows


def _profile(raw_paths: list[Path], runtime_xml: Path,
            evaluator_true_delay_ns: float, sample_rate_hz: float) -> dict[str, Any]:
    """Capture the existing coarse scan, then independently sample its local bracket."""
    original_predict = physics._predicted_cross_spectrum
    original_profile = physics._profile_complex_gain
    original_golden = physics._golden_section_minimum
    current: dict[str, float] = {}
    trace: list[tuple[float, float]] = []
    last: dict[str, np.ndarray] = {}
    dense_check: list[dict[str, Any]] = []

    def predict(delay: float, *args: Any) -> np.ndarray:
        current["delay"] = float(delay)
        result = original_predict(delay, *args)
        last["full_prediction"] = result
        last["frequency_hz"] = np.asarray(args[0])
        return result

    def profile(observed: np.ndarray, predicted: np.ndarray,
                weights: np.ndarray) -> tuple[float, complex, float, float]:
        result = original_profile(observed, predicted, weights)
        trace.append((current["delay"], float(result[0])))
        last["observed"] = observed
        last["selected_prediction"] = predicted
        last["weights"] = weights
        last["fit"] = np.asarray([result[0], result[2], result[3]], dtype=np.float64)
        last["gain"] = np.asarray([result[1].real, result[1].imag], dtype=np.float64)
        return result

    def dense_then_golden(function: Any, left: float, right: float) -> float:
        delays = np.linspace(left, right, 1001)
        scores = np.asarray([function(float(value)) for value in delays])
        best = int(np.argmin(scores))
        evaluator_true_samples = evaluator_true_delay_ns * 1.0e-9 * sample_rate_hz
        evaluator_true_score = float(function(evaluator_true_samples))
        refined = original_golden(function, left, right)
        dense_check.append({
            "bracket_samples": [float(left), float(right)],
            "dense_grid_points": int(delays.size),
            "dense_best_samples": float(delays[best]),
            "dense_best_score": float(scores[best]),
            "original_golden_samples": float(refined),
            "golden_minus_dense_samples": float(refined - delays[best]),
            "evaluator_only_true_delay_ns": float(evaluator_true_delay_ns),
            "evaluator_only_true_delay_samples": float(evaluator_true_samples),
            "evaluator_only_true_delay_score": evaluator_true_score,
            "true_score_minus_best_score": float(evaluator_true_score - scores[best]),
            "truth_used_for_estimation": False,
        })
        return refined

    physics._predicted_cross_spectrum = predict
    physics._profile_complex_gain = profile
    physics._golden_section_minimum = dense_then_golden
    try:
        profiled = r12._blind_m1_from_b_on(raw_paths, runtime_xml)
    finally:
        physics._predicted_cross_spectrum = original_predict
        physics._profile_complex_gain = original_profile
        physics._golden_section_minimum = original_golden

    if profiled.get("status") != "ok" or not dense_check:
        raise RuntimeError(f"M1 objective profile did not complete for seed {seed}")
    coarse = trace[:401]
    if len(coarse) != 401:
        raise RuntimeError(f"expected 401 coarse candidates; observed {len(coarse)}")
    coarse_delays = np.asarray([item[0] for item in coarse])
    coarse_scores = np.asarray([item[1] for item in coarse])
    minima = np.flatnonzero(
        (coarse_scores[1:-1] <= coarse_scores[:-2])
        & (coarse_scores[1:-1] <= coarse_scores[2:])
    ) + 1

    selected = np.isin(last["full_prediction"], last["selected_prediction"])
    selected_indices = np.flatnonzero(selected)
    mapping_verified = (
        selected_indices.size == last["selected_prediction"].size
        and np.array_equal(
            last["full_prediction"][selected_indices], last["selected_prediction"]
        )
    )
    residual_phase: dict[str, Any] = {"status": "NOT_EVALUABLE_FREQUENCY_MAP"}
    if mapping_verified:
        frequency = last["frequency_hz"][selected_indices]
        gain = complex(*last["gain"])
        phase_error = np.angle(
            last["observed"]
            * np.conj(gain * last["selected_prediction"])
        )
        residual_phase = {"status": "EVALUABLE_DESCRIPTIVE"}
        for label, sign_mask in (("negative", frequency < 0), ("positive", frequency > 0)):
            indices = np.flatnonzero(sign_mask)
            order = indices[np.argsort(frequency[indices])]
            phase = np.unwrap(phase_error[order])
            x = frequency[order]
            weights = last["weights"][order]
            x_mean = float(np.average(x, weights=weights))
            phase_mean = float(np.average(phase, weights=weights))
            denominator = float(np.sum(weights * (x - x_mean) ** 2))
            slope = float(np.sum(weights * (x - x_mean) * (phase - phase_mean)) / denominator)
            residual_phase[label] = {
                "support_bins": int(indices.size),
                "weighted_phase_rmse_rad": float(np.sqrt(np.average(phase**2, weights=weights))),
                "descriptive_linear_residual_delay_ns": float(slope / (2.0 * math.pi) * 1.0e9),
            }

    return {
        "profile_status": "EVALUABLE_DESCRIPTIVE",
        "coarse_grid_points": int(len(coarse)),
        "coarse_grid_step_samples": float(coarse_delays[1] - coarse_delays[0]),
        "coarse_interior_local_minima": int(minima.size),
        "coarse_global_best_samples": float(coarse_delays[int(np.argmin(coarse_scores))]),
        "coarse_global_best_score": float(np.min(coarse_scores)),
        "coarse_profile": [
            {"delay_samples": float(delay), "normalized_residual_squared": float(score)}
            for delay, score in coarse
        ],
        "dense_optimizer_check": dense_check[0],
        "frequency_mapping_verified": bool(mapping_verified),
        "residual_phase_by_frequency_sign": residual_phase,
        "diagnostic_estimate_ns": float(profiled["delta_tau_ns"]),
        "diagnostic_normalized_model_residual": float(profiled["normalized_model_residual"]),
    }


def run(seed: int, repeats: int) -> dict[str, Any]:
    case_root, case = _case(seed)
    raw_paths = r12._raw_period_paths(case, "A1_Current_unknown_error", "ON")
    input_records = _verify_inputs(raw_paths)
    branches = r12._condition_branches(case, "A1_Current_unknown_error")
    runtime_xml = Path(str(branches["ON"]["production_xml"]))
    def timed_estimate() -> tuple[dict[str, Any], float, list[dict[str, Any]]]:
        fit_calls: list[dict[str, Any]] = []
        original_fit = physics.fit_weighted_line

        def measured_fit(*fit_args: Any, **fit_kwargs: Any) -> dict[str, Any]:
            start = time.perf_counter()
            result = original_fit(*fit_args, **fit_kwargs)
            fit_calls.append({
                "robust": bool(fit_kwargs.get("robust", False)),
                "elapsed_seconds": time.perf_counter() - start,
                "support_count": int(result["n"]),
            })
            return result

        physics.fit_weighted_line = measured_fit
        start = time.perf_counter()
        try:
            value = r12._blind_m1_from_b_on(raw_paths, runtime_xml)
        finally:
            physics.fit_weighted_line = original_fit
        return value, time.perf_counter() - start, fit_calls

    warmup_estimate, warmup_elapsed, warmup_fit_calls = timed_estimate()
    estimate_peak_rss_kib = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if warmup_estimate.get("truth_used_in_estimator") is not False:
        raise ValueError("M1 estimator did not explicitly report truth_used_in_estimator=false")
    measured_estimates = [timed_estimate() for _ in range(repeats)]
    estimate = measured_estimates[-1][0]
    measured_times = [elapsed for _, elapsed, _calls in measured_estimates]
    robust_fit_times = [
        sum(call["elapsed_seconds"] for call in calls if call["robust"])
        for _value, _elapsed, calls in measured_estimates
    ]
    robust_fit_support = [
        [call["support_count"] for call in calls if call["robust"]]
        for _value, _elapsed, calls in measured_estimates
    ]
    if any(len(support) != 1 for support in robust_fit_support):
        raise RuntimeError("expected one same-input Huber robust fit per M1 estimate")
    for measured, _elapsed, _calls in measured_estimates:
        if not math.isclose(
            float(warmup_estimate["delta_tau_ns"]),
            float(measured["delta_tau_ns"]),
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ):
            raise ValueError("repeated M1 estimates are not deterministic")
    experiment_manifest_path = EXPERIMENT_MANIFEST
    experiment_manifest = json.loads(experiment_manifest_path.read_text(encoding="utf-8"))
    evaluator_true_delay_ns = float(experiment_manifest["frozen_conditions"]["delay_error_ns"])
    sample_rate_hz = r12._xml_float(ET.parse(runtime_xml).getroot(), "fs") * 1.0e6
    profile = _profile(raw_paths, runtime_xml, evaluator_true_delay_ns, sample_rate_hz)
    f2_audit = _f2_spectral_audit(seed, case_root, case["protocol_layout"], evaluator_true_delay_ns)
    if not math.isclose(
        float(estimate["delta_tau_ns"]),
        float(profile["diagnostic_estimate_ns"]),
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise ValueError("instrumented and uninstrumented M1 estimates differ")
    return {
        "seed": seed,
        "case_root": case_root.relative_to(ROOT).as_posix(),
        "method": "M1_Physics_Matched_Delay",
        "input_role": "A1_Current_unknown_error_ON_raw_C1_C2_only",
        "truth_used_in_estimator": False,
        "runtime_xml": {
            "path": runtime_xml.resolve().relative_to(ROOT).as_posix(),
            "sha256": _sha256(runtime_xml),
        },
        "inputs": input_records,
        "input_size_bytes_total": sum(item["size_bytes"] for item in input_records),
        "input_pulse_count": int(estimate["input_pulse_count"]),
        "input_sample_count_per_pulse": 11820,
        "channel_pair": "C1/C2",
        "compute_time_scope": "one complete CPU M1 estimate including raw input read, protocol decode, geometry, FFT and delay fit; excludes Python startup and SHA audit",
        "warmup_compute_time_seconds": warmup_elapsed,
        "measured_compute_time_seconds": measured_times,
        "measured_compute_time_median_seconds": float(np.median(measured_times)),
        "measured_compute_time_min_seconds": float(np.min(measured_times)),
        "measured_compute_time_max_seconds": float(np.max(measured_times)),
        "measured_repeat_count": repeats,
        "same_input_huber_fit_incremental_seconds": robust_fit_times,
        "same_input_huber_fit_incremental_median_seconds": float(np.median(robust_fit_times)),
        "same_input_huber_fit_support_bins": robust_fit_support[-1][0],
        "same_input_huber_timing_scope": "robust weighted phase-slope regression only; shared raw loading, geometry and FFT preprocessing excluded",
        "warmup_huber_fit_incremental_seconds": sum(
            call["elapsed_seconds"] for call in warmup_fit_calls if call["robust"]
        ),
        "process_peak_rss_kib_after_estimate": estimate_peak_rss_kib,
        "process_peak_rss_kib_after_diagnostic": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "compute_environment": {
            "backend": "CPU offline analysis; no CUDA invoked",
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "platform": platform.platform(),
            "logical_cpu_count": __import__("os").cpu_count(),
        },
        "estimate": {
            key: estimate.get(key) for key in (
                "status", "delta_tau_ns", "delay_samples", "support_count",
                "frequency_span_hz", "normalized_model_residual", "phase_rmse_rad",
                "relative_response_model", "delay_bounds_samples", "fallback_reason",
            )
        },
        "optimizer_and_residual_audit": profile,
        "f2_complex_frequency_residual_audit": f2_audit,
        "failure_behavior": "run_r21_cuda_pilot rejects unavailable M1 and aborts corrected branches; no silent fallback",
        "measurement_limit": "single offline invocation; host-sensitive and not a production throughput benchmark",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, choices=sorted(SEED_CASES), required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    output = args.output or (
        DEFAULT_OUTPUT / f"r21_m1_offline_diagnostic_seed{args.seed}.json"
    )
    result = {
        "schema_version": "r21-m1-offline-diagnostic-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "retained_binary_manifest": RETAINED_MANIFEST.relative_to(ROOT).as_posix(),
        "retained_binary_manifest_sha256": _sha256(RETAINED_MANIFEST),
        "experiment_manifest": EXPERIMENT_MANIFEST.relative_to(ROOT).as_posix(),
        "experiment_manifest_sha256": _sha256(EXPERIMENT_MANIFEST),
        "analysis_sources": [
            {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": _sha256(path),
            }
            for path in ANALYSIS_SOURCE_PATHS
        ],
        "result": run(args.seed, args.repeats),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = result["result"]
    print(json.dumps({
        "output": str(output),
        "seed": summary["seed"],
        "measured_compute_time_seconds": summary["measured_compute_time_seconds"],
        "measured_compute_time_median_seconds": summary["measured_compute_time_median_seconds"],
        "same_input_huber_fit_incremental_seconds": summary["same_input_huber_fit_incremental_seconds"],
        "process_peak_rss_kib_after_estimate": summary["process_peak_rss_kib_after_estimate"],
        "input_size_bytes_total": summary["input_size_bytes_total"],
        "estimate": summary["estimate"],
        "optimizer_check": summary["optimizer_and_residual_audit"]["dense_optimizer_check"],
        "coarse_interior_local_minima": summary["optimizer_and_residual_audit"]["coarse_interior_local_minima"],
        "residual_phase_by_frequency_sign": summary["optimizer_and_residual_audit"]["residual_phase_by_frequency_sign"],
        "f2_frequency_residual_audit": [
            {
                "method": row["method"],
                "f1_relative_rms": row["f1_relative_rms"],
                "f2_relative_rms": row["f2_relative_rms"],
                "channel2_relative_rms": row["channel2_relative_rms"],
                "channel4_relative_rms": row["channel4_relative_rms"],
                "channel4_bitwise_unchanged": row["channel4_bitwise_unchanged"],
                "f2_error_power_decomposition": row["f2_error_power_decomposition"],
                "fused_f2_phase_by_frequency_sign": row["fused_f2_phase_by_frequency_sign"],
                "channel2_phase_by_frequency_sign": row["channel2_phase_by_frequency_sign"],
            }
            for row in summary["f2_complex_frequency_residual_audit"]
        ],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
