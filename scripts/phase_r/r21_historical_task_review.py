"""Replay the retained R18/R8/R12 task evidence under the R21 v3 contract."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.phase_r import r20_harmonized_task_review as r20
from scripts.phase_r import r20_historical_evidence_adapter as adapter
from scripts.phase_r.r21_task_evaluator import (
    audit_doppler_opportunity_v3,
    audit_range_bin_axis_v3,
    evaluate_targets_v3,
    metric_status_v3,
    summarize_track_lineage_v3,
)

R20_RESULTS = REPO_ROOT / "docs/research/phase_r/R20/evidence/results"


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (dict, list, tuple, set)) else value
                for key, value in row.items()
            })


def _production_range_axis(branch_dir: Path) -> tuple[dict[str, Any], str | None]:
    path = branch_dir / "production.xml"
    if not path.is_file():
        return {}, None
    root = ET.parse(path).getroot()
    values: dict[str, Any] = {}
    for key in ("range_fft_len", "range_crop_start", "range_compress_len", "fs", "Rmin"):
        values[key] = root.findtext(f".//{key}")
    values["fs_mhz"] = values.pop("fs")
    values["range_origin_m"] = values.pop("Rmin")
    return values, str(path)


def _range_axis_audit(bundle: Mapping[str, Any]) -> dict[str, Any]:
    case_root = Path(bundle["case"]["case_root"])
    role = str(bundle.get("role", "ON"))
    _, scenario, _, scenario_path = r20._physical_truth_inputs(str(case_root), role)
    waveform = scenario.get("waveform", {}) if isinstance(scenario, Mapping) else {}
    production, production_path = _production_range_axis(Path(bundle["branch_dir"]))
    audit = audit_range_bin_axis_v3(
        {key: waveform.get(key) for key in (
            "range_fft_len", "range_crop_start", "range_crop_len", "fs_hz", "sample_delay_sec",
        )},
        production,
    )
    audit["scene_axis_source"] = adapter.audit_path(scenario_path)
    audit["production_axis_source"] = adapter.audit_path(production_path)
    return audit


def _doppler_audit_row_v3(
    bundle: Mapping[str, Any], truth: Mapping[str, Any],
) -> dict[str, Any]:
    period, beam = r20._int(truth.get("period_id")), r20._int(truth.get("beam_id"))
    axis = r20._axis_map(bundle["axes"]["rows"]).get((period, beam)) if period is not None and beam is not None else None
    detections = bundle["detections"]["rows"]
    prf_values = [
        r20._float(row.get("prf_hz")) for row in detections
        if r20._int(row.get("period_id")) == period and r20._int(row.get("beam_id")) == beam
        and r20._float(row.get("prf_hz")) is not None
    ]
    prf = prf_values[0] if prf_values else r20._float(truth.get("prf_hz"))
    axis_data = {**axis, "prf_hz": prf} if axis else {}
    range_rate = r20._range_rate_doppler_audit(bundle, truth)
    range_rate_error = range_rate.get("frequency_closure_error_hz") if range_rate.get("closure_verified") else None
    mapped = audit_doppler_opportunity_v3(
        truth, axis_data,
        range_rate_closure_error_hz=range_rate_error,
        range_rate_closure_tolerance_hz=range_rate.get("closure_tolerance_hz"),
    )
    candidates = r20._non_doppler_candidates(truth, detections)
    count = mapped.get("axis_row_count")
    expected_row = mapped.get("expected_cfar_row")
    candidate_deltas = [
        (r20._circular_row_distance(item["detection_row"], int(expected_row), int(count)), item)
        for item in candidates
    ] if expected_row is not None and count else []
    best_delta, best = min(candidate_deltas, key=lambda item: (item[0], item[1]["det_id"])) if candidate_deltas else (None, None)
    component_error = mapped.get("truth_component_closure_error_hz")
    positive_category = "TARGET_ONLY_POSITIVE_CONTROL" if bundle["role"] == "TO" else (
        "A0_NO_ERROR_ON_CONTROL" if bundle["canonical_condition"] == "A0" else "OTHER"
    )
    meta = {key: bundle.get(key) for key in (
        "dataset", "dataset_key", "case_id", "seed", "target_velocity_mps", "target_snr_db",
        "condition", "canonical_condition", "role",
    )}
    return {
        **meta,
        "period_id": period, "beam_id": beam, "target_id": truth.get("target_id"),
        "truth_total_hz": r20._float(truth.get("af_total_truth_hz")),
        "truth_motion_hz": r20._float(truth.get("af_motion_truth_hz")),
        "truth_geometry_hz": r20._float(truth.get("af_geometry_truth_hz")),
        "truth_component_closure_error_hz": component_error,
        "truth_row_raw": truth.get("row_truth"),
        "truth_row_raw_semantics": "standalone truth ambiguity row; not treated as Production CFAR row",
        "axis_source_status": bundle["axes"]["status"],
        "axis_source_path": adapter.audit_path(bundle["axes"]["source_path"]),
        "axis_first_hz": mapped.get("axis_first_hz"),
        "axis_spacing_hz": mapped.get("axis_spacing_hz"),
        "axis_row_count": count,
        "row_orientation": mapped.get("row_orientation"),
        "prf_hz": prf,
        "axis_prf_closure_hz": mapped.get("axis_prf_closure_hz"),
        "folded_truth_hz": mapped.get("folded_truth_hz"),
        "axis_alias_hz": mapped.get("axis_alias_hz"),
        "expected_cfar_row": expected_row,
        "mapping_status": mapped.get("mapping_status"),
        "mapping_reasons": mapped.get("reasons"),
        "moving_truth_source_path": adapter.audit_path(range_rate.get("source_path")),
        "scenario_resolved_source_path": adapter.audit_path(range_rate.get("scenario_source_path")),
        "carrier_frequency_hz": range_rate.get("carrier_frequency_hz"),
        "wavelength_m": range_rate.get("wavelength_m"),
        "carrier_phase_sign": range_rate.get("carrier_phase_sign"),
        "range_rate_fit_sample_count": range_rate.get("sample_count"),
        "range_rate_from_saved_history_mps": range_rate.get("range_rate_mps"),
        "doppler_from_range_rate_hz": range_rate.get("frequency_hz"),
        "range_rate_doppler_closure_error_hz": range_rate.get("frequency_closure_error_hz"),
        "range_rate_doppler_closure_tolerance_hz": range_rate.get("closure_tolerance_hz"),
        "range_rate_doppler_status": range_rate.get("status"),
        "non_doppler_gate_candidate_count": len(candidates),
        "best_non_doppler_candidate_det_id": best.get("det_id") if best else None,
        "best_candidate_row": best.get("detection_row") if best else None,
        "best_candidate_circular_row_error": best_delta,
        "best_candidate_within_frozen_doppler_gate": best_delta is not None and best_delta <= 2,
        "positive_control_category": positive_category,
        "candidate_doppler_gate_tolerance_rows": 2,
    }


def _evaluate_bundle_v3(
    bundle: dict[str, Any],
    range_axis_audit: Mapping[str, Any],
    doppler_audit_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if bundle["role"] == "OFF":
        return {"status": "NOT_APPLICABLE_H0_ROLE", "target_result": None,
                "track_metrics": None, "parameter_records": [], "range_axis_audit": range_axis_audit}
    truths = bundle["truths"]
    doppler_rows = max((r20._int(row.get("doppler_row_count")) or 0 for row in bundle["axes"]["rows"]), default=0)
    target_result = evaluate_targets_v3(
        truths,
        bundle["detections"]["rows"],
        doppler_rows=doppler_rows,
        doppler_axis_rows=bundle["axes"]["rows"],
        range_axis_audit=range_axis_audit,
        doppler_opportunity_audits=doppler_audit_rows,
    )
    valid_keys = {
        (int(float(row["period_id"])), int(float(row["beam_id"])), str(row.get("target_id", "")))
        for row in target_result["per_opportunity_status"] if row["status"] == "EVALUABLE"
    }
    valid_truths = [row for row in truths if (
        int(float(row["period_id"])), int(float(row["beam_id"])), str(row.get("target_id", ""))
    ) in valid_keys]
    track_metrics: dict[str, Any] | None = None
    if bundle.get("track") and bundle["track"].get("observations"):
        if target_result["invalid_truth_audit"]:
            track_metrics = {
                "status": "NOT_EVALUABLE_EXCLUDED_TRUTH_OPPORTUNITIES",
                "reason": "unverified truth opportunities prevent complete false/mixed track classification",
            }
        else:
            matched_keys = {
                (int(row["period_id"]), int(row["beam_id"]), str(row["det_id"]))
                for row in target_result["associations"] if row.get("matched")
            }
            target_id_by_detection = {
                (int(row["period_id"]), int(row["beam_id"]), str(row["det_id"])): str(row["target_id"])
                for row in target_result["associations"] if row.get("matched")
            }
            track_metrics = summarize_track_lineage_v3(
                observations=bundle["track"]["observations"],
                target_matched_detection_keys=matched_keys,
                target_id_by_detection=target_id_by_detection,
                visible_truth_periods=r20._visible_periods(valid_truths),
                payload_duplicate_key_count=bundle["track"].get("payload_duplicate_key_count", 0),
            )
    parameter_bundle = {**bundle, "truths": valid_truths}
    parameter_records = r20._parameter_records(parameter_bundle, target_result, track_metrics)
    return {
        "status": target_result["status"],
        "target_result": target_result,
        "track_metrics": track_metrics,
        "parameter_records": parameter_records,
        "range_axis_audit": range_axis_audit,
    }


def _task_metric_values(evaluated: Mapping[str, Any]) -> dict[str, Any]:
    target = evaluated.get("target_result") or {}
    metrics = target.get("metrics", {})
    track = evaluated.get("track_metrics") or {}
    return {
        "strict_truth_hits": metrics.get("unique_hits"),
        "truth_opportunities": metrics.get("truth_opportunities"),
        "misses": metrics.get("misses"),
        "hit_fraction": metrics.get("truth_opportunity_hit_fraction"),
        "confirmed_observations": track.get("confirmed_observation_count"),
        "confirmed_unique_periods": track.get("confirmed_unique_period_count"),
        "post_confirmation_match_rate": track.get("post_confirmation_match_rate"),
        "track_fragmentation_count": track.get("track_fragmentation_count_observed"),
    }


def _r21_regression_rows(
    bundles: Sequence[Mapping[str, Any]],
    evaluations: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    saved_path = R20_RESULTS / "r20_evaluator_regression.csv"
    with saved_path.open(newline="", encoding="utf-8") as stream:
        saved = list(csv.DictReader(stream))
    by_case_condition: dict[tuple[str, str], dict[str, Any]] = {}
    for bundle in bundles:
        if bundle["dataset"] != "R18" or bundle["role"] != "ON":
            continue
        key = ("R18", str(bundle["dataset_key"]), str(bundle["condition"]), "ON")
        by_case_condition[(str(bundle["dataset_key"]), str(bundle["condition"]))] = evaluations.get(key, {})
    output: list[dict[str, Any]] = []
    for row in saved:
        evaluated = by_case_condition.get((str(row["case_key"]), str(row["condition"])), {})
        value = _task_metric_values(evaluated).get(str(row["metric"]))
        old = r20._float(row.get("old_metric"))
        v2 = r20._float(row.get("new_metric"))
        numeric = r20._float(value)
        output.append({
            **row,
            "r21_metric": numeric,
            "r21_status": evaluated.get("status", "NOT_EVALUABLE"),
            "r21_evaluator_version": "r21-task-eval-v3",
            "r21_delta_from_r19": numeric - old if numeric is not None and old is not None else None,
            "r21_delta_from_r20": numeric - v2 if numeric is not None and v2 is not None else None,
        })
    return output


def _r8_r12_regression_rows(
    saved_metric_rows: Sequence[Mapping[str, Any]],
    task_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Compare saved R20 R8/R12 task metrics with the R21 v3 replay."""

    new_by_key = {
        (str(row.get("dataset")), str(row.get("dataset_key")), str(row.get("condition")), str(row.get("role"))): row
        for row in task_rows
    }
    value_field = {
        "target_detection_hits": "hits",
        "target_detection_misses": "misses",
        "target_detection_hit_fraction": "hit_fraction",
    }
    evaluable_statuses = {"EVALUABLE", "EVALUABLE_WITH_EXCLUDED_OPPORTUNITIES"}
    output: list[dict[str, Any]] = []
    for old in saved_metric_rows:
        dataset = str(old.get("dataset", ""))
        metric = str(old.get("metric", ""))
        if dataset not in {"R8", "R12"} or metric not in value_field:
            continue
        key = (dataset, str(old.get("dataset_key")), str(old.get("condition")), str(old.get("role")))
        new = new_by_key.get(key, {})
        old_value = r20._float(old.get("value"))
        new_value = r20._float(new.get(value_field[metric]))
        old_status = str(old.get("status", "NOT_EVALUABLE")).strip().upper()
        new_status = str(new.get("status", "NOT_EVALUABLE")).strip().upper()
        if old_status in evaluable_statuses and new_status in evaluable_statuses:
            unchanged = old_value is not None and new_value is not None and math.isclose(
                old_value, new_value, rel_tol=1.0e-12, abs_tol=1.0e-12,
            )
            comparison = "VALUE_UNCHANGED" if unchanged else "VALUE_CHANGED"
        elif old_status not in evaluable_statuses and new_status not in evaluable_statuses:
            comparison = "NON_EVALUABLE_BOTH"
        else:
            comparison = "EVALUABILITY_CHANGED"
        output.append({
            **{name: old.get(name) for name in (
                "dataset", "dataset_key", "case_id", "seed", "condition", "canonical_condition", "role",
            )},
            "metric": metric,
            "r20_value": old.get("value"),
            "r20_status": old_status,
            "r20_reason": old.get("reason"),
            "r20_evidence_source": old.get("evidence_source"),
            "r21_value": new_value,
            "r21_status": new_status,
            "r21_evaluator_version": "r21-task-eval-v3",
            "delta_r21_minus_r20": new_value - old_value if new_value is not None and old_value is not None else None,
            "comparison_status": comparison,
        })
    return output


def _normalize_metric_statuses(
    rows: Sequence[Mapping[str, Any]],
    track_rows: Sequence[Mapping[str, Any]],
    task_rows: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    track_index = {
        (str(row.get("dataset")), str(row.get("dataset_key")), str(row.get("condition")), str(row.get("role"))): row
        for row in track_rows
    }
    task_index = {
        (str(row.get("dataset")), str(row.get("dataset_key")), str(row.get("condition")), str(row.get("role"))): row
        for row in task_rows
    }
    output = []
    for source in rows:
        row = dict(source)
        key = (str(row.get("dataset")), str(row.get("dataset_key")),
               str(row.get("condition")), str(row.get("role")))
        track = track_index.get(key, {})
        task = task_index.get(key, {})
        if (str(row.get("metric", "")) in {
                "target_detection_hits", "target_detection_misses", "target_detection_hit_fraction",
            }
                and task.get("status") == "NOT_EVALUABLE_NO_VALID_PHYSICS_VERIFIED_OPPORTUNITIES"):
            row["value"] = None
            row["numerator"] = None
            row["status"] = "NOT_EVALUABLE_NO_VALID_PHYSICS_VERIFIED_OPPORTUNITIES"
            row["reason"] = (
                "No truth opportunity passed the Production range-axis and per-opportunity Doppler physics gates."
            )
            output.append(row)
            continue
        row["status"] = metric_status_v3(
            metric=str(row.get("metric", "")), value=row.get("value"),
            base_status=str(row.get("status", "NOT_EVALUABLE")),
            target_confirmation_period=track.get("first_target_confirmation_period"),
        )
        output.append(row)
    return output


def run_review(output_root: Path) -> dict[str, Any]:
    output_root = output_root.expanduser().resolve()
    if output_root.exists() and (not output_root.is_dir() or any(output_root.iterdir())):
        raise ValueError(f"refusing to overwrite non-empty R21 historical output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    cases = list(adapter.discover_r18_cases()) + list(adapter.discover_historical_cases())
    bundles: list[dict[str, Any]] = []
    for case in cases:
        for condition, role, branch_dir, _ in r20._branch_items(case):
            allowed_roles = {"ON", "TO"} if case["dataset"] == "R18" else {"ON", "TO", "OFF"}
            if role not in allowed_roles:
                continue
            bundle = r20._condition_role_bundle(case, condition, role, branch_dir)
            bundle["case"] = case
            bundles.append(bundle)

    range_audits: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    doppler_rows: list[dict[str, Any]] = []
    per_bundle_doppler: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for bundle in bundles:
        key = (bundle["dataset"], bundle["dataset_key"], bundle["condition"], bundle["role"])
        range_audits[key] = _range_axis_audit(bundle)
        if bundle["role"] not in {"ON", "TO"}:
            continue
        rows = [
            _doppler_audit_row_v3(bundle, truth)
            for truth in bundle["truths"] if r20._visible_truth(truth)
        ]
        per_bundle_doppler[key] = rows
        doppler_rows.extend(rows)

    control_rows = [row for row in doppler_rows if row["dataset"] == "R18"
                    and row["dataset_key"] == "seed606_v6p7_snr30"
                    and row["canonical_condition"] == "A0" and row["role"] in {"ON", "TO"}]
    to_controls = [row for row in control_rows if row["role"] == "TO"]
    a0_controls = [row for row in control_rows if row["role"] == "ON"]
    to_pass = bool(to_controls) and all(
        row["mapping_status"] == "MAPPING_VERIFIED"
        and row["best_candidate_within_frozen_doppler_gate"] for row in to_controls
    )
    a0_axis_valid = bool(a0_controls) and all(row["mapping_status"] == "MAPPING_VERIFIED" for row in a0_controls)
    global_gate = {
        "status": "DOPPLER_MAPPING_VERIFIED" if to_pass and a0_axis_valid else "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED",
        "positive_control_case": "R18 seed606, 6.7 m/s, 30 dB, delay 0 ns",
        "target_only_TO_opportunities": len(to_controls),
        "target_only_TO_in_frozen_gate": sum(bool(row["best_candidate_within_frozen_doppler_gate"]) for row in to_controls),
        "A0_ON_opportunities": len(a0_controls),
        "A0_ON_physical_mappings_verified": sum(row["mapping_status"] == "MAPPING_VERIFIED" for row in a0_controls),
        "frozen_doppler_gate_rows": 2,
    }

    evaluations: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for bundle in bundles:
        key = (bundle["dataset"], bundle["dataset_key"], bundle["condition"], bundle["role"])
        if bundle["role"] == "OFF":
            evaluations[key] = {"status": "NOT_APPLICABLE_H0_ROLE", "target_result": None,
                                "track_metrics": None, "parameter_records": []}
        elif global_gate["status"] != "DOPPLER_MAPPING_VERIFIED":
            evaluations[key] = {"status": "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED",
                                "reason": "global seed606 positive control failed", "target_result": None,
                                "track_metrics": None, "parameter_records": []}
        else:
            evaluations[key] = _evaluate_bundle_v3(
                bundle, range_audits[key], per_bundle_doppler.get(key, []),
            )

    task_rows: list[dict[str, Any]] = []
    truth_opportunity_rows: list[dict[str, Any]] = []
    track_rows: list[dict[str, Any]] = []
    parameter_rows: list[dict[str, Any]] = []
    for bundle in bundles:
        key = (bundle["dataset"], bundle["dataset_key"], bundle["condition"], bundle["role"])
        evaluated = evaluations[key]
        result = evaluated.get("target_result") or {}
        metrics = result.get("metrics", {})
        for opportunity in result.get("per_opportunity_status", []):
            truth_opportunity_rows.append({
                **{name: bundle.get(name) for name in (
                    "dataset", "dataset_key", "case_id", "seed", "condition", "canonical_condition", "role",
                )},
                "range_axis_status": range_audits[key].get("status"),
                **opportunity,
            })
        task_rows.append({
            **{name: bundle.get(name) for name in (
                "dataset", "dataset_key", "case_id", "seed", "condition", "canonical_condition", "role",
                "target_velocity_mps", "target_snr_db", "delay_error_ns",
            )},
            "evaluator_version": "r21-task-eval-v3",
            "status": evaluated.get("status"),
            "range_axis_status": range_audits[key].get("status"),
            "visible_truth_opportunities": metrics.get("visible_truth_opportunities_total"),
            "truth_opportunities": metrics.get("truth_opportunities"),
            "hits": metrics.get("unique_hits"), "misses": metrics.get("misses"),
            "hit_fraction": metrics.get("truth_opportunity_hit_fraction"),
            "invalid_truth_opportunities": metrics.get("invalid_truth_opportunities"),
        })
        if bundle["role"] == "ON":
            track_metrics = evaluated.get("track_metrics") or {}
            track_rows.extend(r20._track_rows(bundle, evaluated))
            if track_metrics:
                track_rows[-1].update({
                    "false_confirmed_track_ids_without_any_target_match": track_metrics.get("false_confirmed_track_ids_without_any_target_match"),
                    "false_confirmed_track_ids": track_metrics.get("false_confirmed_track_ids"),
                    "unique_track_ids_with_any_unmatched_payload": track_metrics.get("unique_track_ids_with_any_unmatched_payload"),
                    "unique_track_ids_with_any_unmatched_payload_ids": track_metrics.get("unique_track_ids_with_any_unmatched_payload_ids"),
                    "mixed_target_matched_and_unmatched_track_ids": track_metrics.get("mixed_target_matched_and_unmatched_track_ids"),
                    "mixed_target_matched_and_unmatched_track_count": track_metrics.get("mixed_target_matched_and_unmatched_track_count"),
                    "unmatched_confirmed_payload_count": track_metrics.get("unmatched_confirmed_payload_count"),
                    "unmatched_payload_after_target_match_count": track_metrics.get("unmatched_payload_after_target_match_count"),
                    "track_ids_associated_with_multiple_target_ids": track_metrics.get("track_ids_associated_with_multiple_target_ids"),
                    "cross_target_association_payload_count": track_metrics.get("cross_target_association_payload_count"),
                    "source_state_path": adapter.audit_path((bundle.get("track") or {}).get("source_paths", {}).get("states")),
                    "source_payload_path": adapter.audit_path((bundle.get("track") or {}).get("source_paths", {}).get("payloads")),
                })
            parameter_rows.extend(evaluated.get("parameter_records", []))

    v3_regression = _r21_regression_rows(bundles, evaluations)
    h0_rows = [row for bundle in bundles if bundle["role"] == "OFF" for row in r20._off_h0_rows(bundle)]
    known_closure_rows: list[dict[str, Any]] = []
    for case in cases:
        if case["dataset"] not in {"R8", "R12"}:
            continue
        closure = case["case_manifest"].get("known_correction_physical_closure", {})
        for role, role_data in closure.get("by_role", {}).items():
            for channel, values in role_data.get("channels", {}).items():
                known_closure_rows.append({
                    **r20._case_metadata(case), "dataset": case["dataset"], "role": role,
                    "channel": channel, **values,
                    "source": adapter.audit_path(case["case_manifest_path"]),
                })
    raw_metric_rows = r20._metric_evaluability_rows(
        bundles, evaluations, parameter_rows, track_rows, h0_rows, known_closure_rows,
    )
    metric_rows = _normalize_metric_statuses(raw_metric_rows, track_rows, task_rows)
    with (R20_RESULTS / "r20_metric_evaluability.csv").open(newline="", encoding="utf-8") as stream:
        saved_metric_rows = list(csv.DictReader(stream))
    r8_r12_regression = _r8_r12_regression_rows(saved_metric_rows, task_rows)

    output = output_root / "results"
    _write_csv(output / "r21_historical_task_metrics.csv", task_rows)
    _write_csv(output / "r21_truth_opportunity_audit.csv", truth_opportunity_rows)
    _write_csv(output / "r21_evaluator_regression.csv", v3_regression)
    _write_csv(output / "r21_historical_task_regression.csv", r8_r12_regression)
    _write_csv(output / "r21_doppler_opportunity_audit.csv", doppler_rows)
    _write_csv(output / "r21_track_semantics_audit.csv", track_rows)
    _write_csv(output / "r21_metric_evaluability_audit.csv", metric_rows)
    _write_csv(output / "r21_h0_descriptive_metrics.csv", h0_rows)
    manifest = {
        "schema_version": 1,
        "experiment": "R21 retained R18/R8/R12 historical evaluator v3 replay",
        "evaluator_version": "r21-task-eval-v3",
        "source_scope": "read-only R18/R8/R12 Production records; no CUDA or Stage2 run",
        "case_count": len(cases), "branch_count": len(bundles),
        "range_axis_status_counts": {
            status: sum(row.get("range_axis_status") == status for row in task_rows)
            for status in sorted({str(row.get("range_axis_status")) for row in task_rows})
        },
        "truth_range_bin_mapping": {
            "opportunity_count": len(truth_opportunity_rows),
            "status_counts": {
                status: sum(
                    (row.get("range_bin_audit") or {}).get("status") == status
                    for row in truth_opportunity_rows
                )
                for status in sorted({
                    str((row.get("range_bin_audit") or {}).get("status"))
                    for row in truth_opportunity_rows
                })
            },
            "physical_bin_disagreement_count": sum(
                "range_bin_disagrees_with_physical_distance_axis"
                in ((row.get("range_bin_audit") or {}).get("reasons") or [])
                for row in truth_opportunity_rows
            ),
        },
        "doppler_control_gate": global_gate,
        "r19_frozen_task_metrics": {
            "row_count": sum(row.get("metric") in {"strict_truth_hits", "truth_opportunities", "misses", "hit_fraction"}
                              for row in v3_regression),
            "rows_equal_to_r19_and_r20": sum(
                row.get("metric") in {"strict_truth_hits", "truth_opportunities", "misses", "hit_fraction"}
                and row.get("r21_delta_from_r19") == 0 and row.get("r21_delta_from_r20") == 0
                for row in v3_regression
            ),
        },
        "r8_r12_task_regression": {
            "row_count": len(r8_r12_regression),
            "value_unchanged_count": sum(row["comparison_status"] == "VALUE_UNCHANGED" for row in r8_r12_regression),
            "both_non_evaluable_count": sum(row["comparison_status"] == "NON_EVALUABLE_BOTH" for row in r8_r12_regression),
            "evaluability_changed_count": sum(row["comparison_status"] == "EVALUABILITY_CHANGED" for row in r8_r12_regression),
            "value_changed_count": sum(row["comparison_status"] == "VALUE_CHANGED" for row in r8_r12_regression),
        },
        "metric_rows_with_evaluable_status_but_blank_value": sum(
            str(row.get("status", "")).upper() in {"EVALUABLE", "EVALUABLE_WITH_EXCLUDED_OPPORTUNITIES"}
            and row.get("value") in (None, "") for row in metric_rows
        ),
        "r12_seed404_m1_track_semantics": next((row for row in track_rows
            if row.get("dataset") == "R12" and row.get("seed") == 404
            and row.get("canonical_condition") == "M1" and row.get("role") == "ON"), None),
        "generated_files": sorted(str(path.relative_to(output_root)) for path in output_root.rglob("*") if path.is_file()),
    }
    (output_root / "r21_historical_replay_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run_review(args.output_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["doppler_control_gate"]["status"] == "DOPPLER_MAPPING_VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
