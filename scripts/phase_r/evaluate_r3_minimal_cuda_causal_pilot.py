#!/usr/bin/env python3
"""Evaluate the frozen Phase-R3 paired CUDA pilot from its preserved outputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONDITIONS = {
    "A": "A0_Ideal",
    "B": "A1_Current_unknown_error",
    "C": "A2_Known_error_correction_upper_bound",
    "D": "A3_Blind_target_free_estimated_correction",
}
REQUIRED_PRIMARY = ("A", "B", "C")


def matched_complex_transfer(reference: np.ndarray, observed: np.ndarray) -> dict[str, Any]:
    """Fit a complex scalar from the target-only CSI map to paired ON-OFF."""

    ref = np.asarray(reference, dtype=np.complex128).reshape(-1)
    obs = np.asarray(observed, dtype=np.complex128).reshape(-1)
    if ref.shape != obs.shape or ref.size == 0:
        return {"status": "NOT_EVALUABLE", "reason": "empty_or_mismatched_samples"}
    finite = np.isfinite(ref.real) & np.isfinite(ref.imag) & np.isfinite(obs.real) & np.isfinite(obs.imag)
    ref = ref[finite]
    obs = obs[finite]
    denominator = float(np.sum(np.abs(ref) ** 2, dtype=np.float64))
    if ref.size == 0 or not math.isfinite(denominator) or denominator <= 0.0:
        return {"status": "NOT_EVALUABLE", "reason": "zero_or_nonfinite_reference_power"}
    coefficient = np.sum(obs * np.conj(ref), dtype=np.complex128) / denominator
    residual = obs - coefficient * ref
    amplitude = float(abs(coefficient))
    phase = float(np.angle(coefficient))
    return {
        "status": "evaluable",
        "sample_count": int(ref.size),
        "coefficient_real": float(coefficient.real),
        "coefficient_imag": float(coefficient.imag),
        "amplitude_ratio": amplitude,
        "amplitude_ratio_db": 20.0 * math.log10(amplitude) if amplitude > 0.0 else None,
        "phase_error_rad": phase,
        "phase_error_deg": math.degrees(phase),
        "normalized_residual_rms": float(
            np.sqrt(np.mean(np.abs(residual) ** 2, dtype=np.float64) / (denominator / ref.size))
        ),
    }


def active_support_csi_power(
    before_power: np.ndarray,
    csi_after_real: np.ndarray,
    csi_after_imag: np.ndarray,
    *,
    row_start: int,
    row_end: int,
    col_start: int,
    col_end: int,
) -> dict[str, Any]:
    """Summarize pre-canceller power versus the actual CSI output, not detector union."""

    before = np.asarray(before_power, dtype=np.float64)
    real = np.asarray(csi_after_real, dtype=np.float64)
    imag = np.asarray(csi_after_imag, dtype=np.float64)
    if before.shape != real.shape or real.shape != imag.shape or before.ndim != 2:
        return {"status": "NOT_EVALUABLE", "reason": "tap_shape_mismatch"}
    r0, r1 = max(0, row_start), min(before.shape[0] - 1, row_end)
    c0, c1 = max(0, col_start), min(before.shape[1] - 1, col_end)
    if r0 > r1 or c0 > c1:
        return {"status": "NOT_EVALUABLE", "reason": "empty_active_support"}
    before_roi = before[r0 : r1 + 1, c0 : c1 + 1]
    after_power = real[r0 : r1 + 1, c0 : c1 + 1] ** 2 + imag[r0 : r1 + 1, c0 : c1 + 1] ** 2
    valid = np.isfinite(before_roi) & np.isfinite(after_power) & (before_roi >= 0.0) & (after_power >= 0.0)
    if not np.any(valid):
        return {"status": "NOT_EVALUABLE", "reason": "no_finite_active_support_cells"}
    p_before = float(np.mean(before_roi[valid], dtype=np.float64))
    p_after = float(np.mean(after_power[valid], dtype=np.float64))
    return {
        "status": "evaluable" if p_before > 0.0 and p_after > 0.0 else "NOT_EVALUABLE",
        "valid_cells": int(np.count_nonzero(valid)),
        "before_mean_power": p_before,
        "csi_output_mean_power": p_after,
        "cancellation_db": 10.0 * math.log10(p_before / p_after) if p_before > 0.0 and p_after > 0.0 else None,
        "definition": "10log10(mean(before_power)/mean(abs(csi_after)^2)) on H0 active support",
    }


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _find_case_manifest(case_root: Path) -> Path:
    candidate = case_root / "case_manifest.json"
    if candidate.is_file():
        return candidate
    if case_root.is_file() and case_root.name == "case_manifest.json":
        return case_root
    raise FileNotFoundError(f"case_manifest.json not found: {case_root}")


def _branch_records(case: Mapping[str, Any]) -> Mapping[str, Any]:
    comparison = case.get("production_comparison_records")
    if isinstance(comparison, Mapping) and comparison:
        return comparison
    conditions = case.get("conditions")
    if not isinstance(conditions, Mapping):
        raise ValueError("case manifest has no production condition records")
    return conditions


def _condition_record(case: Mapping[str, Any], condition: str) -> Mapping[str, Any]:
    records = _branch_records(case)
    value = records.get(condition)
    return value if isinstance(value, Mapping) else {}


def _role_record(case: Mapping[str, Any], condition: str, role: str) -> Mapping[str, Any]:
    condition_record = _condition_record(case, condition)
    nested = condition_record.get("production")
    records = nested if isinstance(nested, Mapping) else condition_record
    value = records.get(role)
    return value if isinstance(value, Mapping) else {}


def _input_role(case: Mapping[str, Any], condition: str, role: str) -> Mapping[str, Any]:
    all_conditions = case.get("conditions")
    condition_record = all_conditions.get(condition) if isinstance(all_conditions, Mapping) else None
    prep = condition_record.get("input_preparation") if isinstance(condition_record, Mapping) else None
    if not isinstance(prep, Mapping):
        return {}
    value = prep.get(role)
    return value if isinstance(value, Mapping) else {}


def _tap_rows(branch: Mapping[str, Any]) -> dict[int, tuple[Path, dict[str, str]]]:
    result_dir = Path(str(branch.get("result_dir", "")))
    if not result_dir.is_dir():
        raise FileNotFoundError(f"production result directory missing: {result_dir}")
    by_period: dict[int, tuple[Path, dict[str, str]]] = {}
    for manifest_path in sorted(result_dir.rglob("csi_roi_manifest.csv")):
        for row in _read_rows(manifest_path):
            if not row.get("period_id") or not row.get("csi_after_real_path"):
                continue
            period = int(row["period_id"])
            if period in by_period:
                raise ValueError(f"duplicate CSI tap for period {period}: {result_dir}")
            by_period[period] = (manifest_path, row)
    return by_period


def _array_path(manifest_path: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (manifest_path.parent / path).resolve()


def _tap_arrays(tap: tuple[Path, dict[str, str]]) -> dict[str, Any]:
    manifest_path, row = tap
    def load(key: str) -> np.ndarray:
        path = _array_path(manifest_path, row[key])
        if not path.is_file():
            raise FileNotFoundError(f"CSI tap array missing: {path}")
        return np.asarray(np.load(path), dtype=np.float64)

    csi = load("csi_after_real_path") + 1j * load("csi_after_imag_path")
    return {
        "row": row,
        "before_power": load("before_power_path"),
        "csi_after": csi,
        "fa_axis": load("fa_axis_path").reshape(-1),
        "range_axis": load("range_axis_path").reshape(-1),
    }


def _truth_by_period(case_root: Path, simulation: Mapping[str, Any]) -> dict[int, dict[str, str]]:
    a0 = simulation.get("A0_ON")
    if not isinstance(a0, Mapping):
        raise ValueError("case lacks A0_ON Stage2 simulation record")
    output = Path(str(a0.get("output_dir", "")))
    path = output / "truth" / "truth_targets_by_beam.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Stage2 target truth missing: {path}")
    truths: dict[int, dict[str, str]] = {}
    for row in _read_rows(path):
        if row.get("pulse_id") not in {None, "0"} or row.get("visible", "1") in {"0", "false", "False"}:
            continue
        period = int(row["period_id"])
        truths.setdefault(period, row)
    return truths


def _target_center(tap: Mapping[str, Any], truth: Mapping[str, str]) -> tuple[int, int]:
    fa_axis = np.asarray(tap["fa_axis"], dtype=np.float64)
    range_axis = np.asarray(tap["range_axis"], dtype=np.float64)
    if fa_axis.size < 2 or range_axis.size == 0:
        raise ValueError("CSI axes are invalid")
    fa = float(truth["af_total_truth_hz"])
    step = float(np.median(np.diff(fa_axis)))
    prf = step * fa_axis.size
    center = 0.5 * (float(fa_axis[0]) + float(fa_axis[-1]))
    wrapped = fa + round((center - fa) / prf) * prf
    row = int(np.argmin(np.abs(fa_axis - wrapped)))
    rng = float(truth["range_m"])
    col = int(np.argmin(np.abs(range_axis - rng)))
    return row, col


def _roi(value: np.ndarray, row: int, col: int, half_row: int = 2, half_col: int = 2) -> np.ndarray:
    return value[
        max(0, row - half_row) : min(value.shape[0], row + half_row + 1),
        max(0, col - half_col) : min(value.shape[1], col + half_col + 1),
    ]


def _safe_mean_power(value: np.ndarray) -> float | None:
    power = np.abs(value) ** 2
    finite = np.isfinite(power)
    return float(np.mean(power[finite], dtype=np.float64)) if np.any(finite) else None


def _target_transfer(
    case_root: Path,
    case: Mapping[str, Any],
    condition: str,
    branches: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    roles = {role: _tap_rows(branches[role]) for role in ("ON", "OFF", "TO")}
    if set(roles["ON"]) != set(roles["OFF"]) or set(roles["ON"]) != set(roles["TO"]):
        raise ValueError(f"paired role CSI periods differ for {condition}")
    simulation = case.get("simulation", {})
    if not isinstance(simulation, Mapping):
        raise ValueError("case simulation provenance is invalid")
    truths = _truth_by_period(case_root, simulation)
    transfer_ref: list[np.ndarray] = []
    transfer_obs: list[np.ndarray] = []
    to_powers: list[float] = []
    off_powers: list[float] = []
    to_before_powers: list[float] = []
    per_period: list[dict[str, Any]] = []
    for period in sorted(roles["ON"]):
        if period not in truths:
            raise ValueError(f"target truth missing for period {period}")
        on = _tap_arrays(roles["ON"][period])
        off = _tap_arrays(roles["OFF"][period])
        to = _tap_arrays(roles["TO"][period])
        if on["csi_after"].shape != off["csi_after"].shape or on["csi_after"].shape != to["csi_after"].shape:
            raise ValueError(f"paired CSI map shapes differ for {condition}, period {period}")
        row, col = _target_center(on, truths[period])
        on_roi = _roi(on["csi_after"], row, col)
        off_roi = _roi(off["csi_after"], row, col)
        to_roi = _roi(to["csi_after"], row, col)
        to_before_roi = _roi(to["before_power"], row, col)
        if on_roi.shape != off_roi.shape or on_roi.shape != to_roi.shape:
            raise ValueError(f"target ROI shapes differ for {condition}, period {period}")
        transfer_ref.append(to_roi.reshape(-1))
        transfer_obs.append((on_roi - off_roi).reshape(-1))
        p_to = _safe_mean_power(to_roi)
        p_off = _safe_mean_power(off_roi)
        p_before = float(np.mean(to_before_roi, dtype=np.float64))
        if p_to is not None:
            to_powers.append(p_to)
        if p_off is not None:
            off_powers.append(p_off)
        to_before_powers.append(p_before)
        per_period.append({
            "period_id": period,
            "target_row": row,
            "target_col": col,
            "target_range_m": float(truths[period]["range_m"]),
            "target_af_truth_hz": float(truths[period]["af_total_truth_hz"]),
            "target_only_csi_power": p_to,
            "h0_csi_power": p_off,
            "target_only_before_power": p_before,
        })
    reference = np.concatenate(transfer_ref) if transfer_ref else np.asarray([], dtype=np.complex128)
    observed = np.concatenate(transfer_obs) if transfer_obs else np.asarray([], dtype=np.complex128)
    transfer = matched_complex_transfer(reference, observed)
    signal_power = float(np.mean(to_powers, dtype=np.float64)) if to_powers else None
    background_power = float(np.mean(off_powers, dtype=np.float64)) if off_powers else None
    target_input_power = float(np.mean(to_before_powers, dtype=np.float64)) if to_before_powers else None
    return {
        "status": "evaluable" if transfer.get("status") == "evaluable" and signal_power is not None and background_power is not None and background_power > 0 else "NOT_EVALUABLE",
        "paired_complex_transfer": transfer,
        "target_only_output_power_mean": signal_power,
        "h0_output_power_mean": background_power,
        "output_scnr_proxy_db": 10.0 * math.log10(signal_power / background_power) if signal_power and background_power and background_power > 0 else None,
        "target_only_pre_csi_power_mean": target_input_power,
        "target_only_csi_retention_db": 10.0 * math.log10(signal_power / target_input_power) if signal_power and target_input_power and target_input_power > 0 else None,
        "periods": per_period,
        "definitions": {
            "paired_complex_transfer": "least-squares complex gain mapping CSI(TO) to CSI(ON)-CSI(OFF) in truth-centered 5x5 ROIs; CSI adapts per role, so this is a paired transfer diagnostic, not a fixed linear-system gain",
            "output_scnr_proxy_db": "10log10(mean CSI(TO) target-ROI power / mean CSI(OFF) same-ROI C+N power)",
            "target_only_csi_retention_db": "10log10(CSI output power / before-CSI equalized power) for TO target ROI; negative values indicate target attenuation",
        },
    }


def _input_power_table(case: Mapping[str, Any], layout: Mapping[str, Any]) -> dict[str, Any]:
    from scripts import run_delay_stage1_formal as stage1

    results: dict[str, Any] = {}
    for label in REQUIRED_PRIMARY + (("D",) if "D" in CONDITIONS and _condition_record(case, CONDITIONS["D"]) else ()):
        condition = CONDITIONS[label]
        results[label] = {}
        for role in ("ON", "OFF", "TO"):
            prepared = _input_role(case, condition, role)
            raw_paths = prepared.get("period_paths")
            if not isinstance(raw_paths, list) or not raw_paths:
                results[label][role] = {"status": "NOT_EVALUABLE", "reason": "prepared_input_paths_missing"}
                continue
            paths = [Path(str(value)) for value in raw_paths]
            if any(not path.is_file() for path in paths):
                results[label][role] = {"status": "NOT_EVALUABLE", "reason": "raw_input_removed_or_missing"}
                continue
            f1, f2 = stage1._load_fused_from_periods(paths, layout)
            p1 = float(np.mean(np.abs(f1) ** 2, dtype=np.float64))
            p2 = float(np.mean(np.abs(f2) ** 2, dtype=np.float64))
            results[label][role] = {
                "status": "evaluable",
                "period_count": len(paths),
                "F1_mean_power": p1,
                "F2_mean_power": p2,
                "input_paths": [str(path) for path in paths],
                "relative_to_A0_dB": None,
            }
    for role in ("ON", "OFF", "TO"):
        a = results.get("A", {}).get(role, {})
        if a.get("status") != "evaluable":
            continue
        for label in results:
            current = results[label].get(role, {})
            if current.get("status") != "evaluable":
                continue
            current["relative_to_A0_dB"] = {
                "F1": 10.0 * math.log10(current["F1_mean_power"] / a["F1_mean_power"]) if a["F1_mean_power"] > 0 else None,
                "F2": 10.0 * math.log10(current["F2_mean_power"] / a["F2_mean_power"]) if a["F2_mean_power"] > 0 else None,
            }
    return results


def _radial_velocity_audit(
    case: Mapping[str, Any],
    case_root: Path,
    on_branch: Mapping[str, Any],
) -> dict[str, Any]:
    simulation = case.get("simulation", {})
    a0 = simulation.get("A0_ON", {}) if isinstance(simulation, Mapping) else {}
    truth_root = Path(str(a0.get("output_dir", ""))) / "truth"
    target_path = truth_root / "truth_targets_by_beam.csv"
    pulse_path = truth_root / "truth_pulse.csv"
    if not target_path.is_file() or not pulse_path.is_file():
        return {"status": "NOT_EVALUABLE", "reason": "paired_radial_truth_missing"}
    target_rows = [r for r in _read_rows(target_path) if r.get("pulse_id") in {None, "0"}]
    pulse_rows = [r for r in _read_rows(pulse_path) if r.get("pulse_id") == "0"]
    truth = {int(r["period_id"]): r for r in target_rows}
    radial_truth = {int(r["period_id"]): r for r in pulse_rows}
    result_dir = Path(str(on_branch.get("result_dir", "")))
    detections: list[dict[str, str]] = []
    for path in sorted(result_dir.glob("detection_results_GMTI*.csv")):
        detections.extend(_read_rows(path))
    rows: list[dict[str, Any]] = []
    for period, target in sorted(truth.items()):
        try:
            east = float(target.get("e", target.get("e_mid", "nan")))
            north = float(target.get("n", target.get("n_mid", "nan")))
            target_range = float(target["range_m"])
            vr_truth = float(radial_truth[period]["radial_velocity_mps"])
        except (KeyError, ValueError, TypeError):
            continue
        candidates = []
        for detection in detections:
            if int(float(detection.get("period_id", -1))) != period:
                continue
            try:
                position_error = math.hypot(float(detection["new_e"]) - east, float(detection["new_n"]) - north)
                range_error = abs(float(detection["range_m"]) - target_range)
            except (KeyError, ValueError, TypeError):
                continue
            if position_error <= 1000.0 and range_error <= 150.0:
                candidates.append((position_error, range_error, detection))
        if not candidates:
            rows.append({"period_id": period, "status": "NOT_EVALUABLE", "reason": "target_detection_not_matched"})
            continue
        _, range_error, selected = min(candidates, key=lambda item: (item[0], item[1]))
        estimate = float(selected["radial_velocity_mps"])
        ambiguity = str(selected.get("ambiguity_status", ""))
        rows.append({
            "period_id": period,
            "status": "evaluable" if ambiguity == "resolved" else "NOT_EVALUABLE",
            "reason": None if ambiguity == "resolved" else f"ambiguity_status_{ambiguity or 'missing'}",
            "ambiguity_status": ambiguity,
            "radial_velocity_estimate_mps": estimate,
            "radial_velocity_truth_mps": vr_truth,
            "raw_error_mps_not_validated": estimate - vr_truth,
            "range_error_m": range_error,
            "velocity_solver": selected.get("velocity_solver"),
        })
    evaluable = [r for r in rows if r.get("status") == "evaluable"]
    rmse = math.sqrt(sum(float(r["raw_error_mps_not_validated"]) ** 2 for r in evaluable) / len(evaluable)) if evaluable else None
    return {
        "status": "evaluable" if evaluable else "NOT_EVALUABLE",
        "rmse_mps": rmse,
        "matched_rows": rows,
        "definition": "nearest production detection per truth period by estimated position, gated at 1 km and 150 m range; radial RMSE includes only velocity_ambiguity_status=resolved",
    }


def evaluate_case(case_root: Path) -> dict[str, Any]:
    case_manifest_path = _find_case_manifest(case_root)
    case_root = case_manifest_path.parent.resolve()
    case = _read_json(case_manifest_path)
    seed = int(case.get("scene_identity", {}).get("seed", -1))
    branch_records = _branch_records(case)
    layout = case.get("protocol_layout")
    if not isinstance(layout, Mapping):
        raise ValueError(f"protocol layout missing in {case_manifest_path}")
    conditions_available = [
        label for label, condition in CONDITIONS.items()
        if isinstance(_condition_record(case, condition), Mapping) and _condition_record(case, condition)
    ]
    result: dict[str, Any] = {
        "seed": seed,
        "case_root": str(case_root),
        "case_manifest": str(case_manifest_path),
        "case_status": case.get("status"),
        "conditions_available": conditions_available,
        "area_clutter_gate": case.get("area_clutter_gate"),
        "paired_background": case.get("paired_background"),
        "known_correction_physical_closure": case.get("known_correction_physical_closure"),
        "input_fusion_power": _input_power_table(case, layout),
        "conditions": {},
    }
    all_required_passed = True
    for label in conditions_available:
        condition = CONDITIONS[label]
        roles = {role: _role_record(case, condition, role) for role in ("ON", "OFF", "TO")}
        branch_passed = all(
            roles[role].get("production_path_gate", {}).get("status") == "passed"
            and roles[role].get("production_returncode") == 0
            for role in roles
        )
        if label in REQUIRED_PRIMARY and not branch_passed:
            all_required_passed = False
        off_geometry = roles["OFF"].get("cfar_geometry", {})
        on_geometry = roles["ON"].get("cfar_geometry", {})
        summary: dict[str, Any] = {
            "production_path_all_roles_passed": branch_passed,
            "roles": {
                role: {
                    "branch_name": roles[role].get("branch_name"),
                    "returncode": roles[role].get("production_returncode"),
                    "production_path_gate": roles[role].get("production_path_gate"),
                    "metrics": roles[role].get("metrics"),
                    "h0_false_cluster_count": roles[role].get("target_off_waterfall", {}).get("false_clusters", {}).get("value") if role == "OFF" else None,
                    "h0_protocol_false_detection_rows": roles[role].get("target_off_waterfall", {}).get("protocol_false_detections", {}).get("value") if role == "OFF" else None,
                    "h0_false_track_count": roles[role].get("target_off_waterfall", {}).get("false_tracks", {}).get("value") if role == "OFF" else None,
                    "cfar_geometry": roles[role].get("cfar_geometry"),
                }
                for role in roles
            },
            "cfar_h0_branch_hit_fractions": {
                name: {
                    "hit_cut_count": value.get("hit_cut_count"),
                    "valid_cut_count": value.get("valid_cut_count"),
                    "hit_fraction": value.get("hit_fraction"),
                    "interpretation": "H0 branch-specific structured-clutter false-hit fraction; not Pfa and not pooled across overlapping branches",
                }
                for name, value in off_geometry.get("branch_summaries", {}).items()
            } if isinstance(off_geometry, Mapping) else {},
            "csi_h0_active_support": None,
            "target_transfer": None,
            "radial_velocity_audit": None,
        }
        if branch_passed:
            off_taps = _tap_rows(roles["OFF"])
            per_period_csi = []
            for period in sorted(off_taps):
                arrays = _tap_arrays(off_taps[period])
                row = arrays["row"]
                per_period_csi.append(active_support_csi_power(
                    arrays["before_power"],
                    arrays["csi_after"].real,
                    arrays["csi_after"].imag,
                    row_start=int(row["az_st"]),
                    row_end=int(row["az_ed"]),
                    col_start=int(row["rg_st"]),
                    col_end=int(row["rg_ed"]),
                ))
            before_sum = sum(item.get("before_mean_power", 0.0) * item.get("valid_cells", 0) for item in per_period_csi if item.get("status") == "evaluable")
            after_sum = sum(item.get("csi_output_mean_power", 0.0) * item.get("valid_cells", 0) for item in per_period_csi if item.get("status") == "evaluable")
            cell_sum = sum(item.get("valid_cells", 0) for item in per_period_csi if item.get("status") == "evaluable")
            summary["csi_h0_active_support"] = {
                "status": "evaluable" if cell_sum and before_sum > 0 and after_sum > 0 else "NOT_EVALUABLE",
                "period_count": len(per_period_csi),
                "valid_cells": cell_sum,
                "before_mean_power": before_sum / cell_sum if cell_sum else None,
                "csi_output_mean_power": after_sum / cell_sum if cell_sum else None,
                "cancellation_db": 10.0 * math.log10(before_sum / after_sum) if before_sum > 0 and after_sum > 0 else None,
                "per_period": per_period_csi,
                "definition": "H0-only CSI residual on exported active support; uses csi_after complex map, not detector-input union after_power",
            }
            summary["target_transfer"] = _target_transfer(case_root, case, condition, roles)
            summary["radial_velocity_audit"] = _radial_velocity_audit(case, case_root, roles["ON"])
        result["conditions"][label] = summary
    result["status_labels"] = {
        "IMPLEMENTED": True,
        "EXECUTED": all_required_passed,
        "EVALUABLE": all_required_passed and all(
            result["conditions"].get(label, {}).get("target_transfer", {}).get("status") == "evaluable"
            for label in REQUIRED_PRIMARY
        ),
        "PERFORMANCE_VERIFIED": False,
        "note": "set only after explicit cross-seed directional gate; descriptive outcomes do not imply statistical significance",
    }
    outer_path = case_root.parent.parent / "manifest.json"
    if outer_path.is_file():
        outer = _read_json(outer_path)
        result["source_identity"] = outer.get("source_identity_after", outer.get("source_identity_before"))
        result["gpu_status_before"] = outer.get("gpu_status_before")
        result["gpu_status_after"] = outer.get("gpu_status_after")
    return result


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _cross_seed_table(cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        for label, condition in case["conditions"].items():
            on_metrics = condition.get("roles", {}).get("ON", {}).get("metrics", {})
            clutter = condition.get("csi_h0_active_support", {})
            transfer = condition.get("target_transfer", {})
            coef = transfer.get("paired_complex_transfer", {})
            rows.append({
                "seed": case["seed"],
                "condition": label,
                "condition_name": CONDITIONS[label],
                "csi_h0_cancellation_db": clutter.get("cancellation_db"),
                "output_scnr_proxy_db": transfer.get("output_scnr_proxy_db"),
                "target_amplitude_ratio_db": coef.get("amplitude_ratio_db"),
                "target_phase_error_deg": coef.get("phase_error_deg"),
                "target_detection_hits": on_metrics.get("target_detection_hit_count"),
                "target_detection_opportunities": on_metrics.get("target_detection_opportunity_count"),
                "angle_rmse_deg": on_metrics.get("angle_rmse_deg"),
                "ground_speed_rmse_mps": on_metrics.get("velocity_rmse_mps_ground_speed"),
                "position_rmse_m": on_metrics.get("position_rmse_m"),
                "radial_velocity_rmse_mps": condition.get("radial_velocity_audit", {}).get("rmse_mps"),
                "radial_velocity_status": condition.get("radial_velocity_audit", {}).get("status"),
            })
    return rows


def assess_primary_direction(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Gate descriptive performance verification on repeated A→B loss, C recovery.

    The primary task metrics here are lower-is-better tracking/localization RMSEs.
    This is a directional repeatability gate, not a significance test.
    """

    metrics = ("angle_rmse_deg", "ground_speed_rmse_mps", "position_rmse_m")
    grouped: dict[int, dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        try:
            seed = int(row["seed"])
        except (KeyError, TypeError, ValueError):
            continue
        condition = str(row.get("condition", ""))
        if condition in {"A", "B", "C"}:
            grouped.setdefault(seed, {})[condition] = row

    seed_results = []
    for seed in sorted(grouped):
        conditions = grouped[seed]
        if not all(label in conditions for label in ("A", "B", "C")):
            continue
        metric_results: dict[str, Any] = {}
        for metric in metrics:
            try:
                a, b, c = (float(conditions[label][metric]) for label in ("A", "B", "C"))
            except (KeyError, TypeError, ValueError):
                metric_results[metric] = {"status": "NOT_EVALUABLE"}
                continue
            if not all(math.isfinite(value) for value in (a, b, c)):
                metric_results[metric] = {"status": "NOT_EVALUABLE"}
                continue
            metric_results[metric] = {
                "status": "evaluable",
                "A": a,
                "B": b,
                "C": c,
                "B_worse_than_A": b > a,
                "C_better_than_B": c < b,
            }
        seed_results.append({"seed": seed, "metrics": metric_results})

    independent_seed_count = len(seed_results)
    all_directions_hold = independent_seed_count >= 3 and all(
        result["metrics"][metric].get("status") == "evaluable"
        and result["metrics"][metric]["B_worse_than_A"]
        and result["metrics"][metric]["C_better_than_B"]
        for result in seed_results
        for metric in metrics
    )
    return {
        "status": "directionally_repeatable" if all_directions_hold else "not_directionally_verified",
        "repeatable_tracking_degradation_and_recovery": all_directions_hold,
        "independent_seed_count": independent_seed_count,
        "required_independent_seed_count": 3,
        "lower_is_better_metrics": list(metrics),
        "seed_results": seed_results,
        "interpretation": "descriptive directional repeatability only; no statistical significance claim",
    }


def _write_figure(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    seeds = sorted({int(row["seed"]) for row in rows})
    labels = ["A", "B", "C"]
    metrics = [
        ("csi_h0_cancellation_db", "H0 CSI cancellation (dB; higher is better)"),
        ("angle_rmse_deg", "Target angle RMSE (degrees; lower is better)"),
        ("ground_speed_rmse_mps", "Tracked ground-speed RMSE (m/s; lower is better)"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.2))
    for axis, (key, title) in zip(axes, metrics):
        for seed in seeds:
            by_condition = {
                str(row["condition"]): row.get(key)
                for row in rows if int(row["seed"]) == seed
            }
            values = [by_condition.get(label) for label in labels]
            valid_x = [i for i, value in enumerate(values) if value is not None]
            valid_y = [float(values[i]) for i in valid_x]
            axis.plot(valid_x, valid_y, marker="o", label=f"seed {seed}")
        axis.set_xticks(range(len(labels)), labels)
        axis.set_title(title, fontsize=9)
        axis.grid(True, alpha=0.25)
    axes[0].legend(fontsize=8)
    fig.suptitle("Phase R3 paired CUDA A/B/C — descriptive, not significance-tested")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def evaluate(cases: Sequence[Path], output_dir: Path) -> dict[str, Any]:
    outputs = [evaluate_case(path) for path in cases]
    outputs.sort(key=lambda item: int(item["seed"]))
    rows = _cross_seed_table(outputs)
    grouped: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        grouped.setdefault(str(row["condition"]), {})
        for metric in ("csi_h0_cancellation_db", "output_scnr_proxy_db", "angle_rmse_deg", "ground_speed_rmse_mps", "position_rmse_m"):
            value = row.get(metric)
            if value is not None and math.isfinite(float(value)):
                grouped[str(row["condition"])].setdefault(metric, []).append(float(value))
    primary_direction = assess_primary_direction(rows)
    execution_and_evaluation_pass = all(
        case["status_labels"]["EXECUTED"] and case["status_labels"]["EVALUABLE"]
        for case in outputs
    )
    performance_verified = (
        execution_and_evaluation_pass
        and primary_direction["repeatable_tracking_degradation_and_recovery"]
    )
    summary = {
        "schema_version": 1,
        "experiment": "PGRCC Phase R3 minimal four-condition CUDA production causal pilot",
        "seed_count": len(outputs),
        "seed_results": outputs,
        "condition_seed_values": rows,
        "descriptive_medians": {
            condition: {metric: float(np.median(values)) for metric, values in metrics.items()}
            for condition, metrics in grouped.items()
        },
        "statistical_significance_claimed": False,
        "primary_direction_assessment": primary_direction,
        "status_labels": {
            "IMPLEMENTED": True,
            "EXECUTED": all(case["status_labels"]["EXECUTED"] for case in outputs),
            "EVALUABLE": all(case["status_labels"]["EVALUABLE"] for case in outputs),
            "PERFORMANCE_VERIFIED": performance_verified,
            "PERFORMANCE_VERIFIED_scope": "repeatable direction in target angle, tracked ground-speed, and position RMSE only; not Pd/Pfa or statistical significance",
        },
    }
    output_dir = Path(output_dir).expanduser().resolve()
    _write_json(output_dir / "r3_minimal_cuda_causal_evaluation.json", summary)
    _write_csv(output_dir / "r3_cross_seed_condition_metrics.csv", rows)
    _write_figure(output_dir / "r3_cross_seed_comparison.png", rows)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.case_root, args.output_dir)
    print(json.dumps({"output_dir": str(args.output_dir.resolve()), "seed_count": result["seed_count"], "status_labels": result["status_labels"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
