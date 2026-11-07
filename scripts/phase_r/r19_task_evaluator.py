#!/usr/bin/env python3
"""Versioned, offline R19 task evaluator for retained Phase-R18 evidence."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.phase_r.evaluate_r7_task_metrics import _hungarian, expected_doppler_row


CONTRACT_VERSION = "r19-task-eval-v1"
RANGE_GATE_BINS = 2
RANGE_GATE_METERS = 150.0
POSITION_GATE_METERS = 1000.0
DOPPLER_GATE_ROWS = 2


def _finite(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    number = _finite(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "confirmed"}
    return bool(value)


def _wrapped_difference(estimate: float, truth: float, period: float) -> float:
    return (estimate - truth + period / 2.0) % period - period / 2.0


def _identity_value(row: Mapping[str, Any], key: str) -> Any:
    value = row.get(key)
    if value in (None, ""):
        return None
    return str(value).strip()


def validate_comparison_identity(identities: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Reject mixed scenes, seeds, configurations, or Production binaries."""

    if not identities:
        raise ValueError("comparison identity list is empty")
    required = ("seed", "config_sha256", "production_binary_sha256")
    normalized: list[dict[str, str]] = []
    for index, row in enumerate(identities):
        item = {key: str(row[key]).strip() for key in required if row.get(key) not in (None, "")}
        missing = [key for key in required if key not in item]
        if missing:
            raise ValueError(f"comparison identity row {index} missing {', '.join(missing)}")
        for key in (
            "case_id", "target_velocity_mps", "target_snr_db", "delay_error_ns", "background_id",
            "target_id", "production_identity", "input_identity",
        ):
            if row.get(key) not in (None, ""):
                item[key] = str(row[key]).strip()
        normalized.append(item)
    keys = sorted(set.intersection(*(set(row) for row in normalized)))
    mismatches = {
        key: sorted({row[key] for row in normalized})
        for key in keys
        if len({row[key] for row in normalized}) > 1
    }
    if mismatches:
        raise ValueError(f"comparison identity mismatch: {mismatches}")
    return {key: normalized[0][key] for key in keys}


def reference_role(*, mode: str, estimator_role: str, evaluation_role: str) -> str:
    mode = mode.strip().lower()
    estimator_role = estimator_role.strip().upper()
    evaluation_role = evaluation_role.strip().upper()
    if mode == "mode-a" and estimator_role == "OFF" and evaluation_role == "OFF":
        return "PAIRED_OFF_REFERENCE_SAMPLE_IN"
    if mode == "mode-b" and estimator_role == "ON" and evaluation_role == "OFF":
        return "ON_ESTIMATE_OFF_COUNTERFACTUAL"
    raise ValueError(
        f"incompatible estimator/evaluation role for {mode}: {estimator_role}/{evaluation_role}"
    )


def audit_h0(
    *,
    hit_count: Any,
    valid_cut_count: Any,
    target_free: bool,
    role: str,
    reference_role: str,
    independent_holdout: bool,
    independent_seed_count: int,
    uncertainty_assessed: bool = False,
) -> dict[str, Any]:
    hits = _integer(hit_count)
    valid = _integer(valid_cut_count)
    cut_fraction = None
    if hits is not None and valid is not None and valid > 0 and 0 <= hits <= valid:
        cut_fraction = hits / valid
    if not target_free:
        return {
            "status": "NOT_H0_TARGET_PRESENT",
            "role": role,
            "reference_role": reference_role,
            "hit_count": hits,
            "valid_cut_count": valid,
            "cut_hit_fraction": cut_fraction,
            "h0_observed_false_hit_fraction": None,
            "statistical_pfa_status": "NOT_EVALUABLE",
            "reason": "target_present_condition_cannot_be_labelled_H0",
        }
    if hits is None or valid is None or valid <= 0 or hits < 0 or hits > valid:
        return {
            "status": "NOT_EVALUABLE",
            "role": role,
            "reference_role": reference_role,
            "hit_count": hits,
            "valid_cut_count": valid,
            "cut_hit_fraction": cut_fraction,
            "h0_observed_false_hit_fraction": None,
            "statistical_pfa_status": "NOT_EVALUABLE",
            "reason": "valid_H0_CUT_denominator_and_legal_hit_count_required",
        }
    pfa_status = (
        "evaluable_statistical_estimate"
        if independent_holdout and independent_seed_count >= 2 and uncertainty_assessed
        else "NOT_EVALUABLE"
    )
    return {
        "status": "evaluable_descriptive_only",
        "role": role,
        "reference_role": reference_role,
        "hit_count": hits,
        "valid_cut_count": valid,
        "cut_hit_fraction": cut_fraction,
        "h0_observed_false_hit_fraction": cut_fraction,
        "statistical_pfa_status": pfa_status,
        "independent_holdout": bool(independent_holdout),
        "independent_seed_count": max(0, int(independent_seed_count)),
        "reason": None if pfa_status == "evaluable_statistical_estimate" else
        "independent H0 holdout, multiple independent seeds, and uncertainty assessment are all required",
    }


def _truth_key(row: Mapping[str, Any]) -> tuple[int, int, str]:
    period = _integer(row.get("period_id"))
    beam = _integer(row.get("beam_id"))
    target_id = str(row.get("target_id", "")).strip()
    if period is None or beam is None or not target_id:
        raise ValueError(f"truth row lacks a unique period/beam/target key: {row}")
    return period, beam, target_id


def _detection_key(row: Mapping[str, Any]) -> tuple[int, int, str]:
    period = _integer(row.get("period_id"))
    beam = _integer(row.get("beam_id"))
    det_id = str(row.get("det_id", "")).strip()
    if period is None or beam is None or not det_id:
        raise ValueError(f"detection row lacks a unique period/beam/detection key: {row}")
    return period, beam, det_id


def _position_error(truth: Mapping[str, Any], detection: Mapping[str, Any]) -> float | None:
    values = (
        _finite(truth.get("e_mid")), _finite(truth.get("n_mid")),
        _finite(detection.get("new_e")), _finite(detection.get("new_n")),
    )
    if any(value is None for value in values):
        return None
    te, tn, de, dn = (float(value) for value in values)
    return math.hypot(de - te, dn - tn)


def _candidate_edge(
    truth: Mapping[str, Any], detection: Mapping[str, Any], doppler_rows: int,
    doppler_axis: Mapping[str, Any] | None = None,
) -> tuple[bool, str, float | None, dict[str, Any]]:
    tr = _integer(truth.get("range_bin"))
    dr = _integer(detection.get("range_bin"))
    if dr is None:
        dr = _integer(detection.get("col"))
    tr_m, dr_m = _finite(truth.get("range_m")), _finite(detection.get("range_m"))
    pos_error = _position_error(truth, detection)
    truth_freq = _finite(truth.get("af_total_truth_hz"))
    expected_row = expected_doppler_row(truth, detection, doppler_rows, doppler_axis)
    det_row = _integer(detection.get("row"))
    details: dict[str, Any] = {
        "range_delta_bins": abs(dr - tr) if dr is not None and tr is not None else None,
        "range_delta_m": abs(dr_m - tr_m) if dr_m is not None and tr_m is not None else None,
        "position_error_m": pos_error,
        "expected_doppler_row": expected_row,
        "detection_doppler_row": det_row,
        "doppler_delta_rows": None,
    }
    required = (tr, dr, tr_m, dr_m, pos_error, truth_freq, expected_row, det_row)
    if any(value is None for value in required) or doppler_rows <= 0:
        return False, "invalid_required_match_field", None, details
    range_delta_bins = abs(int(dr) - int(tr))
    range_delta_m = abs(float(dr_m) - float(tr_m))
    if range_delta_bins > RANGE_GATE_BINS:
        return False, "range_bin_gate", None, details
    if range_delta_m > RANGE_GATE_METERS:
        return False, "range_m_gate", None, details
    if pos_error > POSITION_GATE_METERS:
        return False, "position_gate", None, details
    doppler_delta = min(
        (int(det_row) - int(expected_row)) % doppler_rows,
        (int(expected_row) - int(det_row)) % doppler_rows,
    )
    details["doppler_delta_rows"] = doppler_delta
    if doppler_delta > DOPPLER_GATE_ROWS:
        return False, "doppler_gate", None, details
    normalized = math.sqrt(
        ((range_delta_bins / RANGE_GATE_BINS) ** 2
         + (range_delta_m / RANGE_GATE_METERS) ** 2
         + (pos_error / POSITION_GATE_METERS) ** 2
         + (doppler_delta / DOPPLER_GATE_ROWS) ** 2) / 4.0
    )
    return True, "eligible", normalized, details


def _parameter_errors(
    truth: Mapping[str, Any], detection: Mapping[str, Any], sample_key: str,
) -> dict[str, dict[str, Any] | None]:
    output: dict[str, dict[str, Any] | None] = {name: None for name in (
        "angle_deg", "position_m", "range_m", "folded_doppler_hz", "radial_velocity_mps",
    )}
    pos_error = _position_error(truth, detection)
    if pos_error is not None:
        output["position_m"] = {"sample_key": sample_key, "error": pos_error}
    te_range, de_range = _finite(truth.get("range_m")), _finite(detection.get("range_m"))
    if te_range is not None and de_range is not None:
        output["range_m"] = {"sample_key": sample_key, "error": de_range - te_range}
    east, north = _finite(truth.get("e_mid")), _finite(truth.get("n_mid"))
    de, dn = _finite(detection.get("new_e")), _finite(detection.get("new_n"))
    pe, pn = _finite(detection.get("platform_e")), _finite(detection.get("platform_n"))
    if None not in (east, north, de, dn, pe, pn):
        truth_bearing = math.atan2(float(north) - float(pn), float(east) - float(pe))
        detection_bearing = math.atan2(float(dn) - float(pn), float(de) - float(pe))
        error = (detection_bearing - truth_bearing + math.pi) % (2 * math.pi) - math.pi
        output["angle_deg"] = {"sample_key": sample_key, "error": math.degrees(error)}
    prf = _finite(detection.get("prf_hz"))
    truth_fd = _finite(truth.get("af_total_truth_hz"))
    estimate_fd = _finite(detection.get("af_unwrapped_hz"))
    if estimate_fd is None:
        estimate_fd = _finite(detection.get("af_total_hz"))
    if prf is not None and prf > 0 and truth_fd is not None and estimate_fd is not None:
        output["folded_doppler_hz"] = {
            "sample_key": sample_key,
            "error": _wrapped_difference(estimate_fd, truth_fd, prf),
        }
    ambiguity = str(detection.get("ambiguity_status", "")).strip().lower()
    radial, radial_truth = _finite(detection.get("radial_velocity_mps")), _finite(truth.get("vr_self_mps"))
    if ambiguity == "resolved" and radial is not None and radial_truth is not None:
        output["radial_velocity_mps"] = {"sample_key": sample_key, "error": radial - radial_truth}
    return output


def rmse_from_samples(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    values: list[float] = []
    for row in samples:
        key = str(row.get("sample_key", "")).strip()
        error = _finite(row.get("error"))
        if not key or error is None:
            raise ValueError("RMSE sample requires a unique key and finite error")
        if key in seen:
            raise ValueError(f"duplicate RMSE opportunity key: {key}")
        seen.add(key)
        values.append(error)
    return {
        "status": "evaluable" if values else "NOT_EVALUABLE",
        "count": len(values),
        "rmse": math.sqrt(sum(value * value for value in values) / len(values)) if values else None,
    }


def common_success_keys(samples_by_method: Mapping[str, Sequence[Mapping[str, Any]]]) -> set[str]:
    if not samples_by_method:
        return set()
    keys_by_method: list[set[str]] = []
    for method, samples in samples_by_method.items():
        keys = [str(row.get("sample_key", "")).strip() for row in samples]
        if any(not key for key in keys):
            raise ValueError(f"{method}: common-success row lacks sample_key")
        if len(keys) != len(set(keys)):
            raise ValueError(f"{method}: duplicate common-success opportunity")
        keys_by_method.append(set(keys))
    return set.intersection(*keys_by_method)


def evaluate_targets(
    truths: Sequence[Mapping[str, Any]],
    detections: Sequence[Mapping[str, Any]],
    *,
    doppler_rows: int,
    doppler_axis_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Match visible truth opportunities to final Production detections."""

    visible_truths = [dict(row) for row in truths if row.get("visible") in (None, "", True) or _truthy(row.get("visible"))]
    ordered_truths = sorted(visible_truths, key=_truth_key)
    truth_keys = [_truth_key(row) for row in ordered_truths]
    if len(truth_keys) != len(set(truth_keys)):
        raise ValueError("duplicate truth opportunity key")
    ordered_detections = sorted((dict(row) for row in detections), key=_detection_key)
    detection_keys = [_detection_key(row) for row in ordered_detections]
    if len(detection_keys) != len(set(detection_keys)):
        raise ValueError("duplicate (period, beam, det_id) detection key")

    invalid_truth_fields = 0
    for row in ordered_truths:
        required = ("range_bin", "range_m", "e_mid", "n_mid", "af_total_truth_hz")
        if any(_finite(row.get(key)) is None for key in required):
            invalid_truth_fields += 1
    valid_detections: list[dict[str, Any]] = []
    invalid_detections = 0
    for row in ordered_detections:
        # The saved per-period Production Doppler axis is authoritative when
        # available; the Detection need not also carry its frequency center.
        required = ("range_m", "row", "new_e", "new_n", "prf_hz")
        if any(_finite(row.get(key)) is None for key in required) or _finite(row.get("prf_hz")) <= 0:
            invalid_detections += 1
        else:
            valid_detections.append(row)

    truth_groups: dict[tuple[int, int], list[int]] = {}
    detection_groups: dict[tuple[int, int], list[int]] = {}
    for index, row in enumerate(ordered_truths):
        period, beam, _ = _truth_key(row)
        truth_groups.setdefault((period, beam), []).append(index)
    for index, row in enumerate(valid_detections):
        period, beam, _ = _detection_key(row)
        detection_groups.setdefault((period, beam), []).append(index)
    doppler_axes: dict[tuple[int, int], Mapping[str, Any]] = {}
    for axis in doppler_axis_rows:
        period = _integer(axis.get("period_id"))
        beam = _integer(axis.get("beam_id"))
        if period is None or beam is None:
            continue
        key = (period, beam)
        if key in doppler_axes and dict(doppler_axes[key]) != dict(axis):
            raise ValueError(f"conflicting Production Doppler axis metadata for {key}")
        doppler_axes[key] = axis

    edge_info: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}
    pair_audit: list[dict[str, Any]] = []
    edge_counts = [0] * len(ordered_truths)
    legacy_edge_counts = [0] * len(ordered_truths)
    legacy_rejections = {
        "range_bin": 0,
        "doppler": 0,
        "other": 0,
    }
    gated_out = 0
    assignments: dict[int, int] = {}
    for group, truth_indices in sorted(truth_groups.items()):
        detection_indices = detection_groups.get(group, [])
        if not detection_indices:
            continue
        penalty = float(len(truth_indices) + 1)
        invalid_cost = 2.0 * penalty
        matrix: list[list[float]] = []
        for local_truth, ti in enumerate(truth_indices):
            row_costs: list[float] = []
            for local_detection, di in enumerate(detection_indices):
                eligible, reason, cost, details = _candidate_edge(
                    ordered_truths[ti], valid_detections[di], doppler_rows, doppler_axes.get(group),
                )
                legacy_range_delta = _finite(details.get("range_delta_m"))
                legacy_position_error = _finite(details.get("position_error_m"))
                legacy_gate_pass = (
                    legacy_range_delta is not None
                    and legacy_position_error is not None
                    and legacy_range_delta <= RANGE_GATE_METERS
                    and legacy_position_error <= POSITION_GATE_METERS
                )
                if legacy_gate_pass:
                    legacy_edge_counts[ti] += 1
                    if not eligible:
                        rejection_key = reason if reason in {"range_bin_gate", "doppler_gate"} else "other"
                        legacy_rejections[rejection_key.removesuffix("_gate")] += 1
                pair_audit.append({
                    "period_id": group[0], "beam_id": group[1],
                    "target_id": _truth_key(ordered_truths[ti])[2],
                    "det_id": _detection_key(valid_detections[di])[2],
                    "eligible": eligible, "reason": reason,
                    "legacy_range_position_gate_pass": legacy_gate_pass, **details,
                })
                if eligible and cost is not None:
                    edge_info[(ti, di)] = (cost, details)
                    edge_counts[ti] += 1
                    row_costs.append(cost)
                else:
                    gated_out += int(reason != "invalid_required_match_field")
                    row_costs.append(invalid_cost)
            row_costs.extend([penalty] * len(truth_indices))
            matrix.append(row_costs)
        for local_truth, assigned_col in enumerate(_hungarian(matrix)):
            if assigned_col < len(detection_indices):
                di = detection_indices[assigned_col]
                if (truth_indices[local_truth], di) in edge_info:
                    assignments[truth_indices[local_truth]] = di

    matched_detection_indices = set(assignments.values())
    associations: list[dict[str, Any]] = []
    parameter_samples: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ("angle_deg", "position_m", "range_m", "folded_doppler_hz", "radial_velocity_mps")
    }
    for ti, target in enumerate(ordered_truths):
        key = _truth_key(target)
        sample_key = "/".join((key[2], str(key[0]), str(key[1])))
        di = assignments.get(ti)
        row: dict[str, Any] = {
            "period_id": key[0], "beam_id": key[1], "target_id": key[2],
            "sample_key": sample_key, "matched": di is not None,
            "det_id": None, "candidate_count": edge_counts[ti],
            "legacy_range_position_candidate_count": legacy_edge_counts[ti],
            "position_error_m": None, "radial_velocity_status": "NOT_EVALUABLE",
            "radial_velocity_error_mps": None,
        }
        if di is not None:
            detection = valid_detections[di]
            row["det_id"] = _detection_key(detection)[2]
            row["position_error_m"] = _position_error(target, detection)
            ambiguity = str(detection.get("ambiguity_status", "")).strip().lower()
            row["radial_velocity_status"] = (
                "evaluable" if ambiguity == "resolved" and _finite(detection.get("radial_velocity_mps")) is not None
                and _finite(target.get("vr_self_mps")) is not None
                else "NOT_EVALUABLE_AMBIGUITY" if ambiguity != "resolved"
                else "NOT_EVALUABLE_MISSING_VALUE"
            )
            errors = _parameter_errors(target, detection, sample_key)
            for name, sample in errors.items():
                if sample is not None:
                    parameter_samples[name].append(sample)
                    if name == "radial_velocity_mps":
                        row["radial_velocity_error_mps"] = sample["error"]
        associations.append(row)

    hits = len(assignments)
    metrics = {
        "truth_opportunities": len(ordered_truths),
        "invalid_truth_opportunities": invalid_truth_fields,
        "raw_detection_rows": len(ordered_detections),
        "valid_detection_rows": len(valid_detections),
        "invalid_detection_rows": invalid_detections,
        "eligible_candidate_edges": sum(edge_counts),
        "legacy_range_position_candidate_edges": sum(legacy_edge_counts),
        "legacy_range_position_duplicate_candidate_count": sum(max(0, n - 1) for n in legacy_edge_counts),
        "legacy_candidates_rejected_by_range_bin_gate": legacy_rejections["range_bin"],
        "legacy_candidates_rejected_by_doppler_gate": legacy_rejections["doppler"],
        "legacy_candidates_rejected_by_other_strict_requirements": legacy_rejections["other"],
        "legacy_candidates_also_strict_eligible": sum(edge_counts),
        "gated_out_candidate_edges": gated_out,
        "unique_hits": hits,
        "misses": len(ordered_truths) - hits,
        "truth_unmatched_detection_rows": len(ordered_detections) - len(matched_detection_indices),
        "duplicate_candidate_count": sum(max(0, n - 1) for n in edge_counts),
        "truth_opportunity_hit_fraction": hits / len(ordered_truths) if ordered_truths else None,
        "detection_status": "evaluable" if ordered_truths else "NOT_EVALUABLE_NO_VISIBLE_TRUTH_OPPORTUNITIES",
    }
    parameter_metrics = {name: rmse_from_samples(samples) for name, samples in parameter_samples.items()}
    return {
        "schema_version": CONTRACT_VERSION,
        "association_gate": {
            "same_period_and_beam": True, "range_bin_tolerance": RANGE_GATE_BINS,
            "range_m_tolerance": RANGE_GATE_METERS, "position_tolerance_m": POSITION_GATE_METERS,
            "circular_doppler_row_tolerance": DOPPLER_GATE_ROWS,
            "assignment": "maximum_cardinality_then_minimum_normalized_rms_cost",
        },
        "metrics": metrics,
        "parameter_metrics_own_success": parameter_metrics,
        "parameter_samples": parameter_samples,
        "associations": associations,
        "candidate_pair_audit": pair_audit,
    }


def summarize_track_lineage(
    *,
    observations: Sequence[Mapping[str, Any]],
    target_matched_detection_keys: set[tuple[int, int, str]],
    association_audit_available: bool,
    truth_opportunity_count: int | None = None,
) -> dict[str, Any]:
    required = ("period_id", "beam_id", "track_id", "state", "matched_this_frame", "det_id", "output_payload")
    if not observations or any(any(key not in row for key in required) for row in observations):
        return {
            "status": "NOT_EVALUABLE",
            "reason": "missing_or_empty_production_track_association_fields",
            "id_switch_status": "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG",
        }
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for source in observations:
        row = dict(source)
        period = _integer(row.get("period_id"))
        beam = _integer(row.get("beam_id"))
        track_id = str(row.get("track_id", "")).strip()
        det_id = str(row.get("det_id", "")).strip()
        if period is None or beam is None or not track_id:
            return {"status": "NOT_EVALUABLE", "reason": "invalid_production_track_key",
                    "id_switch_status": "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"}
        key = (period, beam, track_id, det_id)
        if key in seen:
            raise ValueError(f"duplicate TrackManager observation: {key}")
        seen.add(key)
        row["period_id"] = period
        row["beam_id"] = beam
        row["track_id"] = track_id
        row["det_id"] = det_id
        row["confirmed"] = str(row.get("state", "")).strip().lower() == "confirmed"
        row["matched_this_frame"] = _truthy(row.get("matched_this_frame"))
        row["output_payload"] = _truthy(row.get("output_payload"))
        normalized.append(row)
    confirmed_outputs = [row for row in normalized if row["confirmed"] and row["output_payload"]]
    target_outputs = [
        row for row in confirmed_outputs
        if row["matched_this_frame"] and (row["period_id"], row["beam_id"], row["det_id"]) in target_matched_detection_keys
    ]
    target_track_ids = {row["track_id"] for row in target_outputs}
    all_confirmed_track_ids = {row["track_id"] for row in normalized if row["confirmed"]}
    false_track_ids = all_confirmed_track_ids - target_track_ids
    target_periods = {row["period_id"] for row in target_outputs}
    observed_periods = {row["period_id"] for row in normalized}
    denominator = truth_opportunity_count if truth_opportunity_count is not None else len(observed_periods)
    id_switch_observed = len(target_track_ids) > 1
    return {
        "status": "evaluable",
        "confirmed_track_periods": sum(row["confirmed"] for row in normalized),
        "target_matched_periods": len(target_periods),
        "unique_target_track_count": len(target_track_ids),
        "target_track_ids": sorted(target_track_ids),
        "false_confirmed_track_count": len(false_track_ids),
        "unmatched_confirmed_payload_count": sum(
            (row["period_id"], row["beam_id"], row["det_id"]) not in target_matched_detection_keys
            for row in confirmed_outputs
        ),
        "track_continuity": len(target_periods) / denominator if denominator else None,
        "id_switch_observed": id_switch_observed,
        "id_switch_status": "EVALUABLE_FROM_PRODUCTION_V2" if association_audit_available
        else "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG",
        "false_track_semantics": "unique confirmed track IDs with no target-matched output anywhere in this branch",
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", help="print the frozen evaluator schema")
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps({
            "schema_version": CONTRACT_VERSION,
            "range_bin_tolerance": RANGE_GATE_BINS,
            "range_m_tolerance": RANGE_GATE_METERS,
            "position_tolerance_m": POSITION_GATE_METERS,
            "doppler_row_tolerance": DOPPLER_GATE_ROWS,
        }, sort_keys=True))
        return 0
    parser.error("use the Python API or --self-check; snapshot CLI is added with the R19 analysis adapter")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
