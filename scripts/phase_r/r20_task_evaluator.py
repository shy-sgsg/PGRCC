#!/usr/bin/env python3
"""Versioned offline task metrics for R20; R19 v1 remains frozen."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from scripts.phase_r.r19_task_evaluator import (
    _parameter_errors,
    _truthy,
    _integer,
    _finite,
    evaluate_targets,
)


CONTRACT_VERSION = "r20-task-eval-v2"


def validate_comparison_identity_v2(
    identities: Sequence[Mapping[str, Any]],
    schema: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """Validate a complete identity schema without assuming intervention values match."""

    if not identities:
        return {"status": "IDENTITY_INCOMPLETE", "reason": "empty_identity_set"}
    required = tuple(schema.get("required_equal", ()))
    role_variable = tuple(schema.get("role_variable", ()))
    optional = tuple(schema.get("optional_reported", ()))
    missing: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(identities):
        item = dict(row)
        row_missing = [key for key in (*required, *role_variable) if row.get(key) in (None, "")]
        if row_missing:
            missing.append({"row_index": index, "fields": row_missing})
        normalized.append(item)
    if missing:
        return {
            "status": "IDENTITY_INCOMPLETE",
            "missing_fields": missing,
            "schema": {"required_equal": list(required), "role_variable": list(role_variable),
                       "optional_reported": list(optional)},
        }
    mismatches: dict[str, list[str]] = {}
    for key in required:
        values = sorted({str(row[key]).strip() for row in normalized})
        if len(values) > 1:
            mismatches[key] = values
    role_values = {
        key: [str(row[key]).strip() for row in normalized]
        for key in role_variable
    }
    optional_values = {
        key: [None if row.get(key) in (None, "") else str(row[key]).strip() for row in normalized]
        for key in optional
    }
    return {
        "status": "IDENTITY_MISMATCH" if mismatches else "IDENTITY_VALID",
        "mismatches": mismatches,
        "invariant_values": {
            key: str(normalized[0][key]).strip() for key in required if key not in mismatches
        },
        "role_variable_values": role_values,
        "optional_reported_values": optional_values,
        "row_count": len(normalized),
        "schema": {"required_equal": list(required), "role_variable": list(role_variable),
                   "optional_reported": list(optional)},
    }


def _truth_validity(row: Mapping[str, Any]) -> tuple[bool, list[str]]:
    required = ("range_bin", "range_m", "e_mid", "n_mid", "af_total_truth_hz")
    invalid = [key for key in required if _finite(row.get(key)) is None]
    return not invalid, invalid


def _valid_index(value: Any) -> int | None:
    number = _finite(value)
    if number is None or not float(number).is_integer():
        return None
    return int(number)


def _parameter_evaluability(
    truths: Sequence[Mapping[str, Any]],
    detections: Sequence[Mapping[str, Any]],
    associations: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    detection_by_key = {
        (str(row.get("period_id")), str(row.get("beam_id")), str(row.get("det_id"))): row
        for row in detections
    }
    truth_by_key = {
        (str(row.get("period_id")), str(row.get("beam_id")), str(row.get("target_id"))): row
        for row in truths
    }
    names = ("angle_deg", "position_m", "range_m", "folded_doppler_hz", "radial_velocity_mps")
    counters = {
        name: {"matched": 0, "valid": 0, "missing_reasons": Counter()}
        for name in names
    }
    for association in associations:
        key = (str(association["period_id"]), str(association["beam_id"]), str(association["target_id"]))
        truth = truth_by_key.get(key)
        if truth is None:
            continue
        detection = None
        if association.get("matched"):
            detection = detection_by_key.get((key[0], key[1], str(association.get("det_id"))))
        sample_key = str(association.get("sample_key", "/".join(key)))
        errors = _parameter_errors(truth, detection, sample_key) if detection is not None else {name: None for name in names}
        for name in names:
            state = counters[name]
            if detection is not None:
                state["matched"] += 1
            sample = errors.get(name)
            if sample is not None:
                state["valid"] += 1
                continue
            if detection is None:
                reason = "no_matched_detection"
            elif name == "radial_velocity_mps" and str(detection.get("ambiguity_status", "")).strip().lower() != "resolved":
                reason = "ambiguity_unresolved"
            elif name == "angle_deg" and any(_finite(detection.get(key)) is None for key in ("platform_e", "platform_n")):
                reason = "missing_platform_position"
            elif name in {"position_m", "angle_deg"} and any(_finite(detection.get(key)) is None for key in ("new_e", "new_n")):
                reason = "missing_detection_position"
            elif name == "range_m" and (_finite(truth.get("range_m")) is None or _finite(detection.get("range_m")) is None):
                reason = "missing_range_value"
            elif name == "folded_doppler_hz" and (
                _finite(truth.get("af_total_truth_hz")) is None
                or _finite(detection.get("prf_hz")) is None
                or (_finite(detection.get("af_unwrapped_hz")) is None and _finite(detection.get("af_total_hz")) is None)
            ):
                reason = "missing_doppler_value_or_prf"
            elif name == "radial_velocity_mps":
                reason = "missing_radial_velocity_value"
            else:
                reason = "required_parameter_field_invalid"
            state["missing_reasons"][reason] += 1
    total = len(truths)
    return {
        name: {
            "truth_opportunities": total,
            "matched_detection_opportunities": value["matched"],
            "valid_parameter_opportunities": value["valid"],
            "missing_parameter_opportunities": total - value["valid"],
            "missing_reasons": dict(sorted(value["missing_reasons"].items())),
        }
        for name, value in counters.items()
    }


def evaluate_targets_v2(
    truths: Sequence[Mapping[str, Any]],
    detections: Sequence[Mapping[str, Any]],
    *,
    doppler_rows: int,
    doppler_axis_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Apply R19's frozen gates with corrected opportunity and field semantics."""

    visible_truths = [dict(row) for row in truths if row.get("visible") in (None, "", True) or _truthy(row.get("visible"))]
    valid_truths: list[dict[str, Any]] = []
    invalid_truth_audit: list[dict[str, Any]] = []
    for row in visible_truths:
        valid, invalid_fields = _truth_validity(row)
        if valid:
            valid_truths.append(row)
        else:
            invalid_truth_audit.append({
                "period_id": row.get("period_id"), "beam_id": row.get("beam_id"),
                "target_id": row.get("target_id"), "invalid_fields": ";".join(invalid_fields),
                "excluded_from_detection_opportunity_denominator": True,
            })

    normalized_detections: list[dict[str, Any]] = []
    range_bin_fallback_count = 0
    present_invalid_detection_ids: set[tuple[str, str, str]] = set()
    for source in detections:
        row = dict(source)
        raw_range_bin = row.get("range_bin")
        if raw_range_bin in (None, ""):
            fallback = _valid_index(row.get("col"))
            if fallback is not None:
                row["range_bin"] = fallback
                range_bin_fallback_count += 1
            else:
                row["range_bin"] = None
        elif _valid_index(raw_range_bin) is None:
            row["range_bin"] = None
            row.pop("col", None)
            row["r20_range_bin_status"] = "PRESENT_INVALID"
            present_invalid_detection_ids.add((str(row.get("period_id")), str(row.get("beam_id")), str(row.get("det_id"))))
        normalized_detections.append(row)

    evaluated = evaluate_targets(
        valid_truths, normalized_detections, doppler_rows=doppler_rows,
        doppler_axis_rows=doppler_axis_rows,
    )
    for pair in evaluated["candidate_pair_audit"]:
        key = (str(pair.get("period_id")), str(pair.get("beam_id")), str(pair.get("det_id")))
        if key in present_invalid_detection_ids:
            pair["reason"] = "invalid_present_range_bin"
            pair["eligible"] = False
            pair["range_bin_status"] = "PRESENT_INVALID"
    metrics = evaluated["metrics"]
    invalid_range_bin_rows = len(present_invalid_detection_ids)
    metrics["valid_detection_rows"] -= invalid_range_bin_rows
    metrics["invalid_detection_rows"] += invalid_range_bin_rows
    metrics.update({
        "visible_truth_opportunities_total": len(visible_truths),
        "truth_opportunities": len(valid_truths),
        "invalid_truth_opportunities": len(invalid_truth_audit),
        "valid_truth_opportunities": len(valid_truths),
        "misses": len(valid_truths) - metrics["unique_hits"],
        "truth_opportunity_hit_fraction": (
            metrics["unique_hits"] / len(valid_truths) if valid_truths else None
        ),
        "range_bin_fallback_count": range_bin_fallback_count,
        "present_invalid_range_bin_count": len(present_invalid_detection_ids),
        "evaluator_version": CONTRACT_VERSION,
    })
    evaluated["invalid_truth_audit"] = invalid_truth_audit
    evaluated["parameter_evaluability"] = _parameter_evaluability(
        valid_truths, normalized_detections, evaluated["associations"],
    )
    evaluated["schema_version"] = CONTRACT_VERSION
    evaluated["evaluator_version"] = CONTRACT_VERSION
    return evaluated


def _result_number(result_id: Any) -> int | None:
    match = re.search(r"(\d+)", str(result_id or ""))
    return int(match.group(1)) if match else None


def map_track_observation_periods(
    states: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    result_period_map: Mapping[int, int],
) -> dict[str, Any]:
    """Map TrackManager rows by result ID; UTC is retained only for audit."""

    normalized_states: list[dict[str, Any]] = []
    normalized_events: list[dict[str, Any]] = []
    event_by_result_track: dict[tuple[int, str], list[Mapping[str, Any]]] = defaultdict(list)
    event_period_mismatches = 0
    for event in events:
        result_number = _result_number(event.get("result_id"))
        if result_number is None or result_number not in result_period_map:
            raise ValueError(f"unmapped TrackManager event result_id: {event.get('result_id')}")
        mapped_period = int(result_period_map[result_number])
        event_period = _integer(event.get("period_id"))
        event_period_mismatches += int(event_period is not None and event_period != mapped_period)
        item = dict(event)
        item["event_reported_period_id"] = event_period
        item["period_id"] = mapped_period
        normalized_events.append(item)
        event_by_result_track[(result_number, str(event.get("track_id", "")))].append(event)
    precision_conflicts = 0
    for state in states:
        result_number = _result_number(state.get("result_id"))
        if result_number is None or result_number not in result_period_map:
            raise ValueError(f"unmapped TrackManager state result_id: {state.get('result_id')}")
        item = dict(state)
        item["period_id"] = int(result_period_map[result_number])
        related = event_by_result_track.get((result_number, str(state.get("track_id", ""))), ())
        state_utc = _finite(state.get("utc"))
        if state_utc is not None and any(
            (event_utc := _finite(event.get("utc"))) is not None and event_utc != state_utc
            for event in related
        ):
            precision_conflicts += 1
        normalized_states.append(item)
    return {
        "observations": normalized_states,
        "events": normalized_events,
        "utc_period_conflicts_avoided": precision_conflicts,
        "event_reported_period_mismatch_count": event_period_mismatches,
        "result_period_map": {str(key): value for key, value in sorted(result_period_map.items())},
        "mapping_source": "Production per-result Detection period_id; event ordinal/UTC retained for audit only",
    }


def summarize_track_lineage_v2(
    *,
    observations: Sequence[Mapping[str, Any]],
    target_matched_detection_keys: set[tuple[int, int, str]],
    visible_truth_periods: Sequence[int],
    payload_duplicate_key_count: int = 0,
) -> dict[str, Any]:
    """Report TrackManager observations, unique periods, parallel tracks and handoffs separately."""

    required = ("period_id", "beam_id", "track_id", "state", "matched_this_frame", "det_id", "output_payload")
    if not observations or any(any(key not in row for key in required) for row in observations):
        return {"status": "NOT_EVALUABLE", "reason": "missing_or_empty_production_track_association_fields"}
    seen: set[tuple[int, int, str, str]] = set()
    confirmed_rows: list[dict[str, Any]] = []
    target_tracks_by_period: dict[int, set[str]] = defaultdict(set)
    for source in observations:
        row = dict(source)
        period = _integer(row.get("period_id"))
        beam = _integer(row.get("beam_id"))
        track_id = str(row.get("track_id", "")).strip()
        det_id = str(row.get("det_id", "")).strip()
        if period is None or beam is None or not track_id:
            return {"status": "NOT_EVALUABLE", "reason": "invalid_production_track_key"}
        key = (period, beam, track_id, det_id)
        if key in seen:
            raise ValueError(f"duplicate TrackManager observation: {key}")
        seen.add(key)
        confirmed = str(row.get("state", "")).strip().lower() == "confirmed"
        matched = _truthy(row.get("matched_this_frame"))
        output = _truthy(row.get("output_payload"))
        if confirmed:
            confirmed_rows.append(row)
        if (confirmed and matched and output and (period, beam, det_id) in target_matched_detection_keys):
            target_tracks_by_period[period].add(track_id)
    target_periods = sorted(target_tracks_by_period)
    target_track_ids = sorted({track for tracks in target_tracks_by_period.values() for track in tracks})
    simultaneous_counts = [len(target_tracks_by_period[period]) for period in target_periods]
    transitions = sum(
        not bool(target_tracks_by_period[left] & target_tracks_by_period[right])
        for left, right in zip(target_periods, target_periods[1:])
    )
    first_confirmation_period = min(
        (int(row["period_id"]) for row in confirmed_rows
         if str(row.get("track_id", "")) in target_track_ids),
        default=None,
    )
    truth_periods = sorted({_integer(period) for period in visible_truth_periods if _integer(period) is not None})
    post_confirmation_truth_periods = [
        period for period in truth_periods
        if first_confirmation_period is not None and period >= first_confirmation_period
    ]
    post_confirmation_matched_periods = sorted(set(target_periods) & set(post_confirmation_truth_periods))
    confirmed_target_rows = [row for row in confirmed_rows if str(row.get("track_id", "")) in target_track_ids]
    confirmed_payload_rows = [row for row in confirmed_rows if _truthy(row.get("output_payload"))]
    unmatched_confirmed_payload_count = sum(
        (int(row["period_id"]), int(row["beam_id"]), str(row.get("det_id", "")))
        not in target_matched_detection_keys
        for row in confirmed_payload_rows
    )
    false_confirmed_track_ids = {
        str(row.get("track_id", "")) for row in confirmed_rows
    } - set(target_track_ids)
    return {
        "status": "evaluable",
        "confirmed_observation_count": len(confirmed_rows),
        "confirmed_unique_period_count": len({int(row["period_id"]) for row in confirmed_rows}),
        "confirmed_observation_counts_by_period": dict(sorted(Counter(int(row["period_id"]) for row in confirmed_rows).items())),
        "target_confirmed_observation_count": len(confirmed_target_rows),
        "target_confirmed_unique_period_count": len({int(row["period_id"]) for row in confirmed_target_rows}),
        "first_target_confirmation_period": first_confirmation_period,
        "target_matched_periods": len(target_periods),
        "target_matched_period_ids": target_periods,
        "post_confirmation_truth_periods": post_confirmation_truth_periods,
        "post_confirmation_matched_periods": len(post_confirmation_matched_periods),
        "post_confirmation_miss_periods": sorted(set(post_confirmation_truth_periods) - set(post_confirmation_matched_periods)),
        "post_confirmation_match_rate": (
            len(post_confirmation_matched_periods) / len(post_confirmation_truth_periods)
            if post_confirmation_truth_periods else None
        ),
        "unique_target_track_count": len(target_track_ids),
        "target_track_ids": target_track_ids,
        "false_confirmed_track_count": len(false_confirmed_track_ids),
        "confirmed_payload_count": len(confirmed_payload_rows),
        "unmatched_confirmed_payload_count": unmatched_confirmed_payload_count,
        "maximum_simultaneous_target_track_count": max(simultaneous_counts, default=0),
        "periods_with_simultaneous_target_tracks": sum(count > 1 for count in simultaneous_counts),
        "observed_sequential_target_track_handoffs": transitions,
        "track_fragmentation_count_observed": transitions,
        "track_fragmentation_semantics": "adjacent matched target periods with disjoint confirmed output track IDs; simultaneous IDs are reported separately",
        "payload_duplicate_key_count": int(payload_duplicate_key_count),
    }


def expected_doppler_row_from_physics(
    truth: Mapping[str, Any],
    axis: Mapping[str, Any],
) -> dict[str, Any]:
    """Independently map physical truth Doppler components onto a saved Production axis."""

    motion = _finite(truth.get("af_motion_truth_hz"))
    geometry = _finite(truth.get("af_geometry_truth_hz"))
    first = _finite(axis.get("doppler_axis_first_hz"))
    spacing = _finite(axis.get("doppler_bin_spacing_hz"))
    row_count = _integer(axis.get("doppler_row_count"))
    prf = _finite(axis.get("prf_hz", truth.get("prf_hz")))
    if None in (motion, geometry, first, spacing, row_count, prf) or row_count <= 0 or spacing == 0 or prf <= 0:
        return {"status": "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED", "reason": "missing_or_invalid_physical_axis_field"}
    if str(axis.get("doppler_axis_uniform", "1")).strip().lower() not in {"1", "true", "yes"}:
        return {"status": "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED", "reason": "nonuniform_production_axis"}
    total = float(motion) + float(geometry)
    folded = (total + float(prf) / 2.0) % float(prf) - float(prf) / 2.0
    midpoint = float(first) + (int(row_count) - 1) * float(spacing) / 2.0
    alias_order = math.floor((midpoint - folded) / float(prf) + 0.5)
    axis_alias = folded + alias_order * float(prf)
    unwrapped_index = (axis_alias - float(first)) / float(spacing)
    row = int(math.floor(unwrapped_index + 0.5)) % int(row_count)
    error_hz = axis_alias - (float(first) + row * float(spacing))
    return {
        "status": "VERIFIED_MAPPING",
        "af_motion_truth_hz": float(motion),
        "af_geometry_truth_hz": float(geometry),
        "af_total_from_components_hz": total,
        "folded_truth_hz": folded,
        "prf_hz": float(prf),
        "axis_alias_hz": axis_alias,
        "axis_alias_order": alias_order,
        "axis_first_hz": float(first),
        "axis_spacing_hz": float(spacing),
        "axis_row_count": int(row_count),
        "row_orientation": "increasing_frequency" if float(spacing) > 0 else "decreasing_frequency",
        "continuous_row_index": unwrapped_index,
        "expected_row": row,
        "nearest_axis_frequency_hz": float(first) + row * float(spacing),
        "quantization_residual_hz": error_hz,
    }
