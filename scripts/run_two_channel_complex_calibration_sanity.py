#!/usr/bin/env python3
"""Synthetic Gamma recovery sanity runner for two-channel complex calibration."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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


def finite_float(value: float) -> float:
    return float(value) if math.isfinite(float(value)) else float("nan")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def method_estimate(
    method: str,
    f1: np.ndarray,
    f2: np.ndarray,
    support: np.ndarray,
    *,
    min_support: int,
    range_band_size: int,
    phase_threshold_rad: float,
) -> CalibrationEstimate:
    if method == "SCC":
        return estimate_scc(f1, f2, support, min_support=min_support)
    if method == "DDC":
        return estimate_ddc(f1, f2, support, min_support=min_support)
    if method == "DDC-RB":
        return estimate_ddc_rb(
            f1,
            f2,
            support,
            min_support=min_support,
            range_band_size=range_band_size,
        )
    if method == "robust_DDC":
        return estimate_robust_ddc(
            f1,
            f2,
            support,
            min_support=min_support,
            phase_threshold_rad=phase_threshold_rad,
        )
    if method == "robust_DDC-RB":
        return estimate_robust_ddc_rb(
            f1,
            f2,
            support,
            min_support=min_support,
            range_band_size=range_band_size,
            phase_threshold_rad=phase_threshold_rad,
        )
    raise ValueError(f"unsupported calibration method: {method}")


def expand_gamma(gamma: complex | np.ndarray, shape: tuple[int, int], range_band_size: int) -> np.ndarray:
    values = np.asarray(gamma, dtype=np.complex128)
    if values.ndim == 0:
        return np.full(shape, complex(values), dtype=np.complex128)
    if values.ndim == 1:
        return np.broadcast_to(values[np.newaxis, :], shape).astype(np.complex128)
    if values.ndim == 2:
        expanded = np.empty(shape, dtype=np.complex128)
        for band_index in range(values.shape[0]):
            start = band_index * range_band_size
            stop = min(shape[0], start + range_band_size)
            expanded[start:stop, :] = values[band_index][np.newaxis, :]
        return expanded
    raise ValueError("Gamma shape cannot be expanded")


def gamma_abs_error(
    estimate: CalibrationEstimate,
    gamma_truth: np.ndarray,
    support: np.ndarray,
    range_band_size: int,
) -> float:
    if estimate.status == NOT_EVALUABLE:
        return float("nan")
    expanded = expand_gamma(estimate.gamma, gamma_truth.shape, range_band_size)
    finite = np.isfinite(expanded.real) & np.isfinite(expanded.imag) & support
    if not np.any(finite):
        return float("nan")
    return float(np.max(np.abs(expanded[finite] - gamma_truth[finite])))


def residual_power(residual: np.ndarray, mask: np.ndarray) -> float:
    selected = residual[mask]
    if selected.size == 0:
        return float("nan")
    return float(np.mean(np.abs(selected) ** 2))


def deterministic_f1(rows: int, cols: int, rng: np.random.Generator) -> np.ndarray:
    real = rng.normal(loc=0.0, scale=1.0, size=(rows, cols))
    imag = rng.normal(loc=0.0, scale=1.0, size=(rows, cols))
    ramp = np.linspace(0.3, 1.4, rows, dtype=np.float64)[:, np.newaxis]
    return (real + ramp) + 1j * (imag + 0.2 * np.arange(cols, dtype=np.float64)[np.newaxis, :])


def gamma_for_case(case_id: str, rows: int, cols: int, range_band_size: int) -> np.ndarray:
    if case_id == "T1":
        return np.full((rows, cols), 1.15 - 0.35j, dtype=np.complex128)
    if case_id in {"T2", "T4"}:
        values = np.asarray([1.0 + 0.0j, 0.8 + 0.35j, 1.2 - 0.15j, 0.55 + 0.7j, -0.25 + 1.05j, 1.4 - 0.45j])
        return np.broadcast_to(values[np.newaxis, :cols], (rows, cols)).astype(np.complex128)
    if case_id in {"T3", "T5"}:
        band_count = (rows + range_band_size - 1) // range_band_size
        values = np.empty((band_count, cols), dtype=np.complex128)
        for band in range(band_count):
            for col in range(cols):
                values[band, col] = (0.8 + 0.17 * band + 0.05 * col) + 1j * (0.2 * col - 0.11 * band)
        return expand_gamma(values, (rows, cols), range_band_size)
    raise ValueError(f"unknown case_id: {case_id}")


def target_mask(shape: tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape, dtype=bool)
    mask[2:5, 1] = True
    mask[12:15, 4] = True
    return mask


def orthogonal_decorrelation(
    f1: np.ndarray,
    rng: np.random.Generator,
    range_band_size: int,
) -> np.ndarray:
    rows, cols = f1.shape
    raw = rng.normal(size=f1.shape) + 1j * rng.normal(size=f1.shape)
    out = np.empty_like(f1)
    for start in range(0, rows, range_band_size):
        stop = min(rows, start + range_band_size)
        for col in range(cols):
            base = f1[start:stop, col]
            candidate = raw[start:stop, col]
            denom = np.sum(np.abs(base) ** 2)
            projection = np.sum(candidate * np.conj(base)) / denom
            residual = candidate - projection * base
            scale = np.sqrt(np.mean(np.abs(residual) ** 2))
            out[start:stop, col] = residual / scale
    return 0.3 * out


def build_case(
    case_id: str,
    rows: int,
    cols: int,
    range_band_size: int,
    rng: np.random.Generator,
) -> dict[str, Any]:
    f1 = deterministic_f1(rows, cols, rng)
    gamma_truth = gamma_for_case(case_id, rows, cols, range_band_size)
    support = np.ones((rows, cols), dtype=bool)
    target = target_mask((rows, cols)) if case_id == "T4" else np.zeros((rows, cols), dtype=bool)
    f2_off = gamma_truth * f1
    floor_power = 0.0
    coherence_floor = 1.0
    if case_id == "T5":
        decorrelation = orthogonal_decorrelation(f1, rng, range_band_size)
        f2_off = f2_off + decorrelation
        floor_power = residual_power(decorrelation, support)
        signal_power = residual_power(gamma_truth * f1, support)
        coherence_floor = math.sqrt(signal_power / (signal_power + floor_power))
    f2_on = f2_off.copy()
    if case_id == "T4":
        moving_target = 18.0 * np.exp(1j * 2.6) * f1[target]
        f2_on[target] = f2_on[target] + moving_target
    return {
        "case_id": case_id,
        "f1": f1,
        "f2_off": f2_off,
        "f2_on": f2_on,
        "support": support,
        "clutter_mask": support & ~target,
        "target_mask": target,
        "gamma_truth": gamma_truth,
        "decorrelation_floor_power": floor_power,
        "coherence_floor": coherence_floor,
    }


def run(config: dict[str, Any], output_root: Path) -> dict[str, Any]:
    rows = int(config["dimensions"]["range_bins"])
    cols = int(config["dimensions"]["doppler_bins"])
    range_band_size = int(config["dimensions"]["range_band_size"])
    min_support = int(config["support"]["min_support"])
    thresholds = config["thresholds"]
    phase_threshold_rad = float(config["robust"]["phase_threshold_rad"])
    rng = np.random.default_rng(int(config["seed"]))

    gamma_rows: list[dict[str, Any]] = []
    clutter_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    input_audit: list[dict[str, Any]] = []
    case_statuses: list[dict[str, Any]] = []
    coherence_values: list[float] = []

    for case_cfg in config["cases"]:
        case_id = case_cfg["case_id"]
        case = build_case(case_id, rows, cols, range_band_size, rng)
        coherence_values.append(finite_float(case["coherence_floor"]))
        per_mode_assertions: list[bool] = []
        ordinary_on_residual = float("nan")
        if case_id == "T4":
            ordinary = estimate_ddc(
                case["f1"],
                case["f2_on"],
                case["support"],
                min_support=min_support,
            )
            ordinary_on_residual = residual_power(
                apply_complex_calibration(case["f1"], case["f2_on"], ordinary),
                case["clutter_mask"],
            )

        for mode_cfg in config["modes"]:
            mode = mode_cfg["mode_id"]
            method = config["methods"][mode][case_id]
            estimator_f2 = case["f2_off"] if mode == "Mode-A" else case["f2_on"]
            estimate = method_estimate(
                method,
                case["f1"],
                estimator_f2,
                case["support"],
                min_support=min_support,
                range_band_size=range_band_size,
                phase_threshold_rad=phase_threshold_rad,
            )
            apply_f2 = case["f2_on"]
            residual = apply_complex_calibration(case["f1"], apply_f2, estimate)
            power = residual_power(residual, case["clutter_mask"])
            gamma_error = gamma_abs_error(
                estimate,
                case["gamma_truth"],
                case["support"],
                range_band_size,
            )
            robust_excluded = int(estimate.metadata.get("excluded_count", 0))

            assertion = estimate.status == "OK"
            if case_id in {"T1", "T2", "T3", "T4"}:
                assertion = assertion and gamma_error <= float(thresholds["gamma_error_abs_max"])
            if case_id == "T5":
                floor_power = float(case["decorrelation_floor_power"])
                assertion = (
                    assertion
                    and power >= floor_power * float(thresholds["decorrelation_floor_ratio_min"])
                )
            if case_id == "T4" and mode == "Mode-B":
                assertion = (
                    assertion
                    and robust_excluded > 0
                    and power < ordinary_on_residual * float(thresholds["mode_b_improvement_ratio_max"])
                )
            per_mode_assertions.append(assertion)

            gamma_rows.append(
                {
                    "case_id": case_id,
                    "mode": mode,
                    "method": estimate.method,
                    "status": estimate.status,
                    "support_count": estimate.support_count,
                    "gamma_error_abs_max": f"{gamma_error:.12g}",
                    "assertion_status": "passed" if assertion else "failed",
                }
            )
            clutter_rows.append(
                {
                    "case_id": case_id,
                    "mode": mode,
                    "method": estimate.method,
                    "status": estimate.status,
                    "clutter_residual_power": f"{power:.12g}",
                    "decorrelation_floor_power": f"{float(case['decorrelation_floor_power']):.12g}",
                    "theory_coherence_floor": f"{float(case['coherence_floor']):.12g}",
                    "ordinary_on_ddc_residual_power": f"{ordinary_on_residual:.12g}",
                    "robust_excluded_count": robust_excluded,
                    "assertion_status": "passed" if assertion else "failed",
                }
            )
            target_rows.append(
                {
                    "case_id": case_id,
                    "mode": mode,
                    "method": estimate.method,
                    "status": NOT_EVALUABLE,
                    "target_pd": NOT_EVALUABLE,
                    "track_pd": NOT_EVALUABLE,
                    "target_localization_rmse": NOT_EVALUABLE,
                    "note": "synthetic Gamma sanity has no downstream detector or tracker",
                }
            )
            input_audit.append(
                {
                    "case_id": case_id,
                    "mode": mode,
                    "method": estimate.method,
                    "truth_used_in_estimator": bool(estimate.metadata.get("truth_used_in_estimator", True)),
                    "estimator_input_paths": ESTIMATOR_INPUT_PATHS,
                    "estimator_source": mode_cfg["estimator_source"],
                }
            )
        case_statuses.append(
            {
                "case_id": case_id,
                "status": "passed" if all(per_mode_assertions) else "failed",
            }
        )

    coherence_floor_value = min(coherence_values) if coherence_values else float("nan")
    floor_status = (
        "passed"
        if coherence_floor_value >= float(thresholds["coherence_floor_min"])
        else "failed"
    )
    sanity_status = (
        "passed"
        if floor_status == "passed" and all(item["status"] == "passed" for item in case_statuses)
        else "failed"
    )

    output_root.mkdir(parents=True, exist_ok=True)
    write_csv(
        output_root / "gamma_recovery.csv",
        gamma_rows,
        ["case_id", "mode", "method", "status", "support_count", "gamma_error_abs_max", "assertion_status"],
    )
    write_csv(
        output_root / "clutter_metrics.csv",
        clutter_rows,
        [
            "case_id",
            "mode",
            "method",
            "status",
            "clutter_residual_power",
            "decorrelation_floor_power",
            "theory_coherence_floor",
            "ordinary_on_ddc_residual_power",
            "robust_excluded_count",
            "assertion_status",
        ],
    )
    write_csv(
        output_root / "target_transfer.csv",
        target_rows,
        [
            "case_id",
            "mode",
            "method",
            "status",
            "target_pd",
            "track_pd",
            "target_localization_rmse",
            "note",
        ],
    )

    manifest = {
        "schema": "two_channel_complex_calibration_sanity.v1",
        "sanity_status": sanity_status,
        "seed": config["seed"],
        "dimensions": config["dimensions"],
        "thresholds": thresholds,
        "cases": [
            {"case_id": item["case_id"], "label": item["label"], "mechanism": item["mechanism"]}
            for item in config["cases"]
        ],
        "modes": config["modes"],
        "case_statuses": case_statuses,
        "theory_coherence_floor": {
            "value": coherence_floor_value,
            "status": floor_status,
            "definition": "minimum synthetic true F1/F2 coherence after evaluator-only decorrelation injection",
        },
        "estimator_input_audit": input_audit,
        "evidence_files": {
            "gamma_recovery": "gamma_recovery.csv",
            "clutter_metrics": "clutter_metrics.csv",
            "target_transfer": "target_transfer.csv",
        },
        "raw_arrays_written": False,
        "downstream_fields": NOT_EVALUABLE,
    }
    (output_root / "sanity_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    output_root = args.output_root if args.output_root is not None else Path(config["evidence_root"])
    manifest = run(config, output_root)
    print(json.dumps({"sanity_status": manifest["sanity_status"], "output_root": str(output_root)}, sort_keys=True))
    return 0 if manifest["sanity_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
