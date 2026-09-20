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

from scripts.analyze_equivalent_vs_physical_calibration import emit_evidence  # noqa: E402
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
    "M0": "Current",
    "M1": "ordinary subtraction",
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
    if shape[0] >= 12 and shape[1] >= 5:
        mask[3:6, 1] = True
        mask[10:13, 4] = True
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


def case_gamma(mechanism: str, rows: int, cols: int, range_band_size: int) -> np.ndarray:
    row = np.arange(rows, dtype=np.float64)[:, np.newaxis]
    col = np.arange(cols, dtype=np.float64)[np.newaxis, :]
    if mechanism == "pure_equivalent_mismatch":
        result = np.full((rows, cols), 1.12 - 0.28j, dtype=np.complex128)
    elif mechanism == "fast_time_channel_delay":
        phase = 0.045 * row
        result = np.exp(1j * phase) * (1.0 + 0.01 * col)
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


def build_case(case_cfg: dict[str, Any], rows: int, cols: int, range_band_size: int) -> dict[str, Any]:
    mechanism = case_cfg["mechanism"]
    f1 = deterministic_f1(rows, cols)
    gamma_truth = case_gamma(mechanism, rows, cols, range_band_size)
    support = np.ones((rows, cols), dtype=bool)
    # This mechanism pilot has no production target tap. Target contamination
    # is covered by the T4 sanity case, so the pilot cannot turn a target
    # proxy into physical-state evidence.
    target = np.zeros((rows, cols), dtype=bool)
    f2_off = gamma_truth * f1
    floor = 0.0
    coherence = 1.0
    if mechanism == "true_decorrelation":
        decorrelated = orthogonal_component(f1, range_band_size)
        f2_off = f2_off + decorrelated
        floor = residual_power(decorrelated, support)
        signal = residual_power(gamma_truth * f1, support)
        coherence = math.sqrt(signal / (signal + floor))
    f2_on = f2_off.copy()
    return {
        "case_id": case_cfg["case_id"],
        "label": case_cfg["label"],
        "mechanism": mechanism,
        "f1": f1,
        "f2_off": f2_off,
        "f2_on": f2_on,
        "support": support,
        "clutter_mask": support & ~target,
        "gamma_truth": gamma_truth,
        "decorrelation_floor": floor,
        "empirical_coherence": coherence,
        "physical_observable": case_cfg["physical_observable"],
        "observable_domain": case_cfg["observable_domain"],
        "physical_truth": float(case_cfg.get("physical_truth", 0.0)),
        "physical_blind_estimate": float(case_cfg.get("physical_blind_estimate", 0.0)),
        "physical_unit": case_cfg.get("physical_unit", ""),
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
        return case["f2_on"] - case["f1"]
    assert estimate is not None
    return apply_complex_calibration(case["f1"], case["f2_on"], estimate, allow_invalid=True)


def _physical_status(case: dict[str, Any], method_id: str) -> tuple[str, float, float]:
    if case["mechanism"] not in {"fast_time_channel_delay", "servo_pointing", "platform_velocity"}:
        return NOT_EVALUABLE, float("nan"), float("nan")
    truth = float(case["physical_truth"])
    if method_id in {"P1", "P2"}:
        estimate = float(case["physical_blind_estimate"])
    elif method_id in {"PK", "PK+R"}:
        estimate = truth
    else:
        return NOT_EVALUABLE, float("nan"), float("nan")
    return "OK", estimate, abs(estimate - truth)


def build_observations(config: dict[str, Any]) -> dict[str, Any]:
    rows = int(config["dimensions"]["range_bins"])
    cols = int(config["dimensions"]["doppler_bins"])
    range_band_size = int(config["dimensions"]["range_band_size"])
    min_support = int(config["support"]["min_support"])
    phase_threshold_rad = float(config["robust"]["phase_threshold_rad"])
    pilot_cfg = config["mechanism_pilot"]
    cases_cfg = pilot_cfg["cases"]
    modes = config["modes"]

    gamma_rows: list[dict[str, Any]] = []
    clutter_rows: list[dict[str, Any]] = []
    audit_entries: list[dict[str, Any]] = []
    cases_summary: list[dict[str, str]] = []

    for case_cfg in cases_cfg:
        case = build_case(case_cfg, rows, cols, range_band_size)
        cases_summary.append(
            {
                "case_id": case["case_id"],
                "label": case["label"],
                "mechanism": case["mechanism"],
                "physical_observable": case["physical_observable"],
                "observable_domain": case["observable_domain"],
            }
        )
        for mode_cfg in modes:
            mode = mode_cfg["mode_id"]
            estimator_f2 = case["f2_off"] if mode == "Mode-A" else case["f2_on"]
            for method_id in EQUIVALENT_METHODS:
                estimate = estimate_equivalent(
                    method_id,
                    case["f1"],
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
                            "OK" if case["mechanism"] == "true_decorrelation" and case["decorrelation_floor"] > 0.0 else ""
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
            physical_status, physical_estimate, physical_error = _physical_status(case, method_id)
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
                    "residual_power": "",
                    "physical_observable": case["physical_observable"],
                    "observable_domain": case["observable_domain"],
                    "calibration_scope": "physical_state_only",
                    "physical_status": physical_status,
                    "physical_estimate": _fmt(physical_estimate),
                    "physical_error": _fmt(physical_error),
                    "range_registration_error_samples": _fmt(range_error),
                    "doppler_only_claim": "false",
                    "truth_used_in_estimator": "false" if method_id in {"P1", "P2"} else "true",
                    "estimator_input_paths": "physical_observable" if method_id in {"P1", "P2"} else "known_error_state",
                    "support_count": "",
                    "excluded_count": "",
                }
            )
            if method_id in {"P1", "P2"} and physical_status == "OK":
                audit_entries.append(
                    {
                        "case_id": case["case_id"],
                        "mode": "Mode-A" if method_id == "P1" else "Mode-B",
                        "method_id": method_id,
                        "truth_used_in_estimator": False,
                        "estimator_input_paths": ["physical_observable"],
                    }
                )

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
        "physical_correction_status": config.get("downstream_status", NOT_EVALUABLE),
        "preserved_sanity_evidence": [],
    }


def preserve_existing_sanity_evidence(output_root: Path) -> list[str]:
    """Keep Task 3's same-named compact evidence before Task 4 writes its schema."""

    manifest = output_root / "sanity_manifest.json"
    if not manifest.is_file():
        return []
    preserve_root = output_root / "sanity"
    preserve_root.mkdir(parents=True, exist_ok=True)
    preserved: list[str] = []
    for filename in ("sanity_manifest.json", "gamma_recovery.csv", "clutter_metrics.csv", "target_transfer.csv"):
        source = output_root / filename
        if not source.is_file():
            continue
        destination = preserve_root / filename
        if not destination.exists():
            shutil.copy2(source, destination)
        preserved.append(str(destination.relative_to(output_root)))
    return preserved


def provenance(config_path: Path, command: list[str], preserved_sanity_evidence: list[str]) -> dict[str, Any]:
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
    print(json.dumps({"output_root": str(output_root), "pilot_status": manifest["pilot_status"], "decision_label": manifest["decision_label"]}, sort_keys=True))
    return 0 if manifest["pilot_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
