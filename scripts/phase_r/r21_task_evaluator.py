"""R21 task evaluator v3, preserving the historical R19/R20 evaluators."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
from typing import Any, Mapping, Sequence

from scripts.phase_r.r20_task_evaluator import (
    _truth_validity,
    evaluate_targets_v2,
    expected_doppler_row_from_physics,
    summarize_track_lineage_v2,
)

CONTRACT_VERSION = "r21-task-eval-v3"


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return int(number)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _truth_key(row: Mapping[str, Any]) -> tuple[int | None, int | None, str]:
    return _integer(row.get("period_id")), _integer(row.get("beam_id")), str(row.get("target_id", ""))


def _visible_truth(row: Mapping[str, Any]) -> bool:
    value = str(row.get("visible", "1")).strip().lower()
    return value in {"", "1", "true", "yes", "y"}


def audit_range_bin_axis_v3(
    scene_axis: Mapping[str, Any],
    production_axis: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify Stage2's cropped range axis against the Production output axis."""

    scene_values = {
        "range_fft_len": _integer(scene_axis.get("range_fft_len")),
        "range_crop_start": _integer(scene_axis.get("range_crop_start")),
        "range_crop_len": _integer(scene_axis.get("range_crop_len")),
    }
    production_values = {
        "range_fft_len": _integer(production_axis.get("range_fft_len")),
        "range_crop_start": _integer(production_axis.get("range_crop_start")),
        "range_crop_len": _integer(production_axis.get("range_compress_len")),
    }
    reasons: list[str] = []
    if any(value is None for value in scene_values.values()):
        reasons.append("missing_or_invalid_scene_range_axis")
    if any(value is None for value in production_values.values()):
        reasons.append("missing_or_invalid_production_range_axis")
    if not reasons:
        if scene_values["range_fft_len"] <= 0 or scene_values["range_crop_len"] <= 0:
            reasons.append("non_positive_scene_range_axis")
        if production_values["range_fft_len"] <= 0 or production_values["range_crop_len"] <= 0:
            reasons.append("non_positive_production_range_axis")
        if scene_values["range_crop_start"] < 0 or production_values["range_crop_start"] < 0:
            reasons.append("negative_range_crop_start")
        if scene_values["range_crop_start"] + scene_values["range_crop_len"] > scene_values["range_fft_len"]:
            reasons.append("scene_crop_exceeds_fft_axis")
        if production_values["range_crop_start"] + production_values["range_crop_len"] > production_values["range_fft_len"]:
            reasons.append("production_crop_exceeds_fft_axis")
        if scene_values != production_values:
            reasons.append("scene_and_production_range_axes_disagree")
    status = "RANGE_AXIS_VERIFIED" if not reasons else "RANGE_AXIS_MISMATCH"
    return {
        "status": status,
        "range_bin_count": scene_values["range_crop_len"] if status == "RANGE_AXIS_VERIFIED" else None,
        "scene_axis": scene_values,
        "production_axis": production_values,
        "reasons": reasons,
    }


def validate_truth_range_bin_v3(
    truth: Mapping[str, Any],
    range_axis_audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Require an exact, in-range cropped-bin index consistent with scene truth."""

    reasons: list[str] = []
    count = _integer(range_axis_audit.get("range_bin_count"))
    if range_axis_audit.get("status") != "RANGE_AXIS_VERIFIED" or count is None or count <= 0:
        reasons.append("range_axis_not_verified")
    raw = truth.get("range_bin")
    if raw in (None, ""):
        reasons.append("missing_range_bin")
        value = None
    else:
        value = _integer(raw)
        if value is None:
            reasons.append("invalid_range_bin" if _finite(raw) is None else "non_integer_range_bin")
        elif count is not None and not 0 <= value < count:
            reasons.append("range_bin_outside_scene_axis")

    expected_raw = truth.get("expected_bin")
    if expected_raw not in (None, ""):
        expected = _integer(expected_raw)
        if expected is None:
            reasons.append("invalid_expected_bin")
        elif expected >= 0 and value is not None and expected != value:
            reasons.append("range_bin_disagrees_with_expected_bin")
        elif expected >= 0 and count is not None and expected >= count:
            reasons.append("expected_bin_outside_scene_axis")

    status = "TRUTH_RANGE_BIN_VERIFIED" if not reasons else "INVALID_TRUTH_RANGE_BIN"
    return {
        "status": status,
        "range_bin": value if status == "TRUTH_RANGE_BIN_VERIFIED" else None,
        "range_bin_count": count,
        "reasons": reasons,
    }


def audit_doppler_opportunity_v3(
    truth: Mapping[str, Any],
    production_axis: Mapping[str, Any],
    *,
    range_rate_closure_error_hz: Any,
    range_rate_closure_tolerance_hz: Any,
) -> dict[str, Any]:
    """Audit the signed Doppler row mapping and physical closure for one truth."""

    mapped = expected_doppler_row_from_physics(truth, production_axis)
    reasons: list[str] = []
    if mapped.get("status") != "VERIFIED_MAPPING":
        reasons.append(str(mapped.get("reason") or "production_axis_mapping_failed"))
    motion = _finite(truth.get("af_motion_truth_hz"))
    geometry = _finite(truth.get("af_geometry_truth_hz"))
    total = _finite(truth.get("af_total_truth_hz"))
    component_error = total - (motion + geometry) if None not in (motion, geometry, total) else None
    component_tolerance = 1.0e-6
    if component_error is None or abs(component_error) > component_tolerance:
        reasons.append("truth_component_closure_failed")

    range_rate_error = _finite(range_rate_closure_error_hz)
    range_rate_tolerance = _finite(range_rate_closure_tolerance_hz)
    if (range_rate_error is None or range_rate_tolerance is None or range_rate_tolerance < 0.0
            or abs(range_rate_error) > range_rate_tolerance):
        reasons.append("range_rate_doppler_closure_failed")

    spacing = _finite(production_axis.get("doppler_bin_spacing_hz"))
    row_count = _integer(production_axis.get("doppler_row_count"))
    prf = _finite(production_axis.get("prf_hz", truth.get("prf_hz")))
    axis_closure = (
        row_count * abs(spacing) - prf
        if row_count is not None and spacing is not None and prf is not None else None
    )
    axis_tolerance = max(1.0e-3, abs(spacing) * 1.0e-3) if spacing is not None else None
    if (axis_closure is None or axis_tolerance is None or abs(axis_closure) > axis_tolerance):
        reasons.append("production_axis_prf_closure_failed")

    status = "MAPPING_VERIFIED" if not reasons else "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED"
    return {
        **mapped,
        "status": status,
        "mapping_status": status,
        "truth_component_closure_error_hz": component_error,
        "truth_component_closure_tolerance_hz": component_tolerance,
        "range_rate_doppler_closure_error_hz": range_rate_error,
        "range_rate_doppler_closure_tolerance_hz": range_rate_tolerance,
        "axis_prf_closure_hz": axis_closure,
        "axis_prf_closure_tolerance_hz": axis_tolerance,
        "row_orientation": mapped.get("row_orientation"),
        "expected_cfar_row": mapped.get("expected_row"),
        "reasons": reasons,
    }


def summarize_track_lineage_v3(
    *,
    observations: Sequence[Mapping[str, Any]],
    target_matched_detection_keys: set[tuple[int, int, str]],
    target_id_by_detection: Mapping[tuple[int, int, str], str] | None = None,
    visible_truth_periods: Sequence[int],
    payload_duplicate_key_count: int = 0,
) -> dict[str, Any]:
    """Separate unmatched payload rows, never-matched IDs and mixed lineage IDs."""

    base = summarize_track_lineage_v2(
        observations=observations,
        target_matched_detection_keys=target_matched_detection_keys,
        target_id_by_detection=target_id_by_detection,
        visible_truth_periods=visible_truth_periods,
        payload_duplicate_key_count=payload_duplicate_key_count,
    )
    if base.get("status") != "evaluable":
        return {**base, "status": str(base.get("status", "NOT_EVALUABLE")).upper()}

    matched_by_track: dict[str, list[tuple[int, str]]] = {}
    target_ids_by_track: dict[str, set[str]] = {}
    payload_rows: list[tuple[Mapping[str, Any], tuple[int, int, str]]] = []
    for row in observations:
        if (str(row.get("state", "")).strip().lower() != "confirmed"
                or not _truth_value(row.get("output_payload"))):
            continue
        period, beam = _integer(row.get("period_id")), _integer(row.get("beam_id"))
        if period is None or beam is None:
            continue
        key = (period, beam, str(row.get("det_id", "")).strip())
        payload_rows.append((row, key))
        track_id = str(row.get("track_id", "")).strip()
        if _truth_value(row.get("matched_this_frame")) and key in target_matched_detection_keys:
            matched_by_track.setdefault(track_id, []).append((period, key[2]))
            target_id = (target_id_by_detection or {}).get(key)
            if target_id is not None:
                target_ids_by_track.setdefault(track_id, set()).add(str(target_id))

    target_track_ids = set(matched_by_track)
    unmatched_rows = [(row, key) for row, key in payload_rows
                      if not (_truth_value(row.get("matched_this_frame")) and key in target_matched_detection_keys)]
    unmatched_track_ids = {str(row.get("track_id", "")).strip() for row, _ in unmatched_rows}
    all_payload_track_ids = {str(row.get("track_id", "")).strip() for row, _ in payload_rows}
    never_matched = all_payload_track_ids - target_track_ids
    mixed = unmatched_track_ids & target_track_ids
    first_match_period = {track: min(period for period, _ in values) for track, values in matched_by_track.items()}
    unmatched_after_target_match = sum(
        str(row.get("track_id", "")).strip() in first_match_period
        and int(row["period_id"]) >= first_match_period[str(row.get("track_id", "")).strip()]
        for row, _ in unmatched_rows
    )
    multi_target_ids = sorted(track for track, values in target_ids_by_track.items() if len(values) > 1)
    cross_target_payload_count = sum(
        str(row.get("track_id", "")).strip() in multi_target_ids
        and _truth_value(row.get("matched_this_frame"))
        and key in target_matched_detection_keys
        for row, key in payload_rows
    )
    return {
        **base,
        "status": "EVALUABLE",
        "legacy_v2_any_unmatched_track_count": len(unmatched_track_ids),
        "unique_track_ids_with_any_unmatched_payload": len(unmatched_track_ids),
        "unique_track_ids_with_any_unmatched_payload_ids": sorted(unmatched_track_ids),
        "false_confirmed_track_count": len(never_matched),
        "false_confirmed_track_ids_without_any_target_match": len(never_matched),
        "false_confirmed_track_ids": sorted(never_matched),
        "mixed_target_matched_and_unmatched_track_ids": sorted(mixed),
        "mixed_target_matched_and_unmatched_track_count": len(mixed),
        "unmatched_confirmed_payload_count": len(unmatched_rows),
        "unmatched_payload_after_target_match_count": unmatched_after_target_match,
        "track_ids_associated_with_multiple_target_ids": multi_target_ids,
        "multi_target_track_id_count": len(multi_target_ids),
        "cross_target_association_payload_count": cross_target_payload_count,
        "target_ids_by_track": {key: sorted(value) for key, value in sorted(target_ids_by_track.items())},
    }


def _truth_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def metric_status_v3(
    *,
    metric: str,
    value: Any,
    base_status: str,
    target_confirmation_period: Any = None,
) -> str:
    """Return a canonical status; an empty target-conditioned metric is not evaluable."""

    status = str(base_status or "NOT_EVALUABLE").strip().upper()
    has_value = value not in (None, "")
    if status not in {"EVALUABLE", "EVALUABLE_WITH_EXCLUDED_OPPORTUNITIES"}:
        return status
    if has_value:
        return status
    if metric in {"post_confirmation_match_rate", "target_confirmation_delay_periods"}:
        if target_confirmation_period in (None, ""):
            return "NOT_EVALUABLE_NO_TARGET_CONFIRMATION"
    return "NOT_EVALUABLE_MISSING_VALUE"


def evaluate_targets_v3(
    truths: Sequence[Mapping[str, Any]],
    detections: Sequence[Mapping[str, Any]],
    *,
    doppler_rows: int,
    doppler_axis_rows: Sequence[Mapping[str, Any]],
    range_axis_audit: Mapping[str, Any],
    doppler_opportunity_audits: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate only truths with valid range geometry and a verified physical Doppler mapping."""

    audits = {
        _truth_key(row): row for row in doppler_opportunity_audits
        if _truth_key(row)[0] is not None and _truth_key(row)[1] is not None
    }
    visible_truths = [dict(row) for row in truths if _visible_truth(row)]
    valid_truths: list[dict[str, Any]] = []
    invalid_audit: list[dict[str, Any]] = []
    opportunity_status_rows: list[dict[str, Any]] = []
    for truth in visible_truths:
        key = _truth_key(truth)
        reasons: list[str] = []
        valid_fields, invalid_fields = _truth_validity(truth)
        if not valid_fields:
            reasons.extend(f"invalid_truth_field:{name}" for name in invalid_fields)
        range_audit = validate_truth_range_bin_v3(truth, range_axis_audit)
        reasons.extend(range_audit["reasons"])
        doppler_audit = audits.get(key)
        if doppler_audit is None:
            reasons.append("missing_per_opportunity_doppler_audit")
        elif doppler_audit.get("mapping_status", doppler_audit.get("status")) != "MAPPING_VERIFIED":
            reasons.extend(doppler_audit.get("reasons") or ["doppler_truth_mapping_not_verified"])
        if reasons:
            invalid_row = {
                "period_id": truth.get("period_id"), "beam_id": truth.get("beam_id"),
                "target_id": truth.get("target_id"), "invalid_reasons": sorted(set(reasons)),
                "excluded_from_detection_opportunity_denominator": True,
            }
            invalid_audit.append(invalid_row)
            opportunity_status_rows.append({
                "period_id": truth.get("period_id"), "beam_id": truth.get("beam_id"),
                "target_id": truth.get("target_id"), "status": "NOT_EVALUABLE",
                "reasons": invalid_row["invalid_reasons"],
            })
        else:
            valid_truths.append(truth)
            opportunity_status_rows.append({
                "period_id": truth.get("period_id"), "beam_id": truth.get("beam_id"),
                "target_id": truth.get("target_id"), "status": "EVALUABLE", "reasons": [],
            })

    result = evaluate_targets_v2(
        valid_truths, detections, doppler_rows=doppler_rows,
        doppler_axis_rows=doppler_axis_rows,
    )
    metrics = result["metrics"]
    metrics.update({
        "visible_truth_opportunities_total": len(visible_truths),
        "invalid_truth_opportunities": len(invalid_audit),
        "valid_physics_verified_truth_opportunities": len(valid_truths),
        "truth_opportunities": len(valid_truths),
        "unique_hits": int(metrics.get("unique_hits", 0)) if valid_truths else None,
        "misses": len(valid_truths) - int(metrics.get("unique_hits", 0)) if valid_truths else None,
        "truth_opportunity_hit_fraction": (
            int(metrics.get("unique_hits", 0)) / len(valid_truths) if valid_truths else None
        ),
        "evaluator_version": CONTRACT_VERSION,
    })
    result["invalid_truth_audit"] = invalid_audit
    result["schema_version"] = CONTRACT_VERSION
    result["evaluator_version"] = CONTRACT_VERSION
    if not valid_truths:
        status = "NOT_EVALUABLE_NO_VALID_PHYSICS_VERIFIED_OPPORTUNITIES"
    elif invalid_audit:
        status = "EVALUABLE_WITH_EXCLUDED_OPPORTUNITIES"
    else:
        status = "EVALUABLE"
    result["status"] = status
    result["per_opportunity_status"] = opportunity_status_rows
    return result
