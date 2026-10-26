#!/usr/bin/env python3
"""Offline R20 regression and harmonized review of saved Phase R evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.phase_r.r19_r18_snapshot_evaluation import _cfar_metrics
from scripts.phase_r.r19_task_evaluator import common_success_keys, rmse_from_samples
from scripts.phase_r.r20_historical_evidence_adapter import (
    REPO_ROOT,
    HISTORICAL_CONDITIONS,
    audit_path,
    branch_identity,
    discover_historical_cases,
    discover_r18_cases,
    file_manifest,
    load_detections,
    load_doppler_axes,
    load_track_debug,
    load_truth,
    read_csv,
    read_json,
    sha256_file,
)
from scripts.phase_r.r20_task_evaluator import (
    CONTRACT_VERSION,
    evaluate_targets_v2,
    expected_doppler_row_from_physics,
    summarize_track_lineage_v2,
    validate_comparison_identity_v2,
)


R19_BASELINE = REPO_ROOT / "docs/research/phase_r/R19/evidence/results/formal_matrix"
R19_SCRIPT = REPO_ROOT / "scripts/phase_r/r19_r18_snapshot_evaluation.py"
R19_EVALUATOR = REPO_ROOT / "scripts/phase_r/r19_task_evaluator.py"
R20_SCRIPT = Path(__file__).resolve()
R20_ADAPTER = REPO_ROOT / "scripts/phase_r/r20_historical_evidence_adapter.py"
R20_EVALUATOR = REPO_ROOT / "scripts/phase_r/r20_task_evaluator.py"
R20_TESTS = REPO_ROOT / "tests/test_r20_task_evaluator.py"


def _int(value: Any) -> int | None:
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number.is_integer() else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _visible_truth(row: Mapping[str, Any]) -> bool:
    value = str(row.get("visible", "1")).strip().lower()
    return value in {"", "1", "true", "yes", "y"}


def _branch_items(case: Mapping[str, Any]):
    for key, record in sorted(case["branches"].items()):
        condition, role = key
        if case["dataset"] == "R18":
            branch_dir = Path(record["branch_dir"])
            branch_record = record
        else:
            branch_dir = Path(record)
            branch_record = None
        yield str(condition), str(role), branch_dir, branch_record


def _case_metadata(case: Mapping[str, Any]) -> dict[str, Any]:
    cm = case["case_manifest"]
    scene = cm.get("scene_identity", {})
    record = cm.get("case", {})
    return {
        "dataset": case["dataset"],
        "dataset_key": case["dataset_key"],
        "case_id": cm.get("case_id", scene.get("case_id", record.get("case_id"))),
        "seed": scene.get("seed", record.get("seed")),
        "target_velocity_mps": scene.get("target_velocity_mps", record.get("target_velocity_mps")),
        "target_snr_db": scene.get("target_snr_db", record.get("target_snr_db")),
        "delay_error_ns": cm.get("delay_error_ns", record.get("delay_error_ns", scene.get("delay_error_ns"))),
    }


def _condition_role_bundle(case: Mapping[str, Any], condition: str, role: str, branch_dir: Path) -> dict[str, Any]:
    truths, truth_path = load_truth(case, role) if role in {"ON", "TO"} else ([], None)
    detections = load_detections(branch_dir)
    axes = load_doppler_axes(
        branch_dir, dataset_key=case["dataset_key"], condition=condition, role=role,
    )
    identity = branch_identity(case, branch_dir, condition, role, truth_path, detections)
    if identity.get("input_period_sha256_declared"):
        encoded = json.dumps(identity["input_period_sha256_declared"], separators=(",", ":")).encode("utf-8")
        identity["input_period_sha256_digest"] = hashlib.sha256(encoded).hexdigest()
    else:
        identity["input_period_sha256_digest"] = None
    track = load_track_debug(branch_dir, detections) if role == "ON" else None
    return {
        **_case_metadata(case), "condition": condition,
        "canonical_condition": HISTORICAL_CONDITIONS.get(condition, condition),
        "role": role, "branch_dir": branch_dir, "branch_record": branch_dir.name,
        "truths": truths, "truth_path": truth_path,
        "detections": detections, "axes": axes, "identity": identity, "track": track,
    }


def _axis_map(axis_rows: Sequence[Mapping[str, Any]]) -> dict[tuple[int, int], dict[str, Any]]:
    result: dict[tuple[int, int], dict[str, Any]] = {}
    for row in axis_rows:
        period, beam = _int(row.get("period_id")), _int(row.get("beam_id"))
        if period is not None and beam is not None:
            result[(period, beam)] = dict(row)
    return result


def _circular_row_distance(lhs: int, rhs: int, count: int) -> int:
    left, right = lhs % count, rhs % count
    return min((left - right) % count, (right - left) % count)


def _non_doppler_candidates(
    truth: Mapping[str, Any], detections: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    period, beam = _int(truth.get("period_id")), _int(truth.get("beam_id"))
    tr_bin, tr_range = _int(truth.get("range_bin")), _float(truth.get("range_m"))
    east, north = _float(truth.get("e_mid")), _float(truth.get("n_mid"))
    if None in (period, beam, tr_bin, tr_range, east, north):
        return []
    output: list[dict[str, Any]] = []
    for detection in detections:
        if _int(detection.get("period_id")) != period or _int(detection.get("beam_id")) != beam:
            continue
        raw = detection.get("range_bin")
        d_bin = _int(raw) if raw not in (None, "") else _int(detection.get("col"))
        d_range, d_e, d_n = _float(detection.get("range_m")), _float(detection.get("new_e")), _float(detection.get("new_n"))
        row = _int(detection.get("row"))
        if None in (d_bin, d_range, d_e, d_n, row):
            continue
        delta_bin, delta_range = abs(d_bin - tr_bin), abs(d_range - tr_range)
        position_error = math.hypot(d_e - east, d_n - north)
        if delta_bin <= 2 and delta_range <= 150.0 and position_error <= 1000.0:
            output.append({
                "det_id": str(detection.get("det_id", "")), "detection_row": row,
                "range_delta_bins": delta_bin, "range_delta_m": delta_range,
                "position_error_m": position_error,
            })
    return sorted(output, key=lambda row: (
        row["range_delta_bins"] / 2.0 + row["range_delta_m"] / 150.0 + row["position_error_m"] / 1000.0,
        row["det_id"],
    ))


def _doppler_audit_row(bundle: Mapping[str, Any], truth: Mapping[str, Any]) -> dict[str, Any]:
    meta = {key: bundle.get(key) for key in (
        "dataset", "dataset_key", "case_id", "seed", "target_velocity_mps", "target_snr_db",
        "condition", "canonical_condition", "role",
    )}
    period, beam = _int(truth.get("period_id")), _int(truth.get("beam_id"))
    axis = _axis_map(bundle["axes"]["rows"]).get((period, beam)) if period is not None and beam is not None else None
    detections = bundle["detections"]["rows"]
    prf_values = [
        _float(row.get("prf_hz")) for row in detections
        if _int(row.get("period_id")) == period and _int(row.get("beam_id")) == beam
        and _float(row.get("prf_hz")) is not None
    ]
    prf = prf_values[0] if prf_values else _float(truth.get("prf_hz"))
    axis_data = {**axis, "prf_hz": prf} if axis else {}
    mapped = expected_doppler_row_from_physics(truth, axis_data)
    total = _float(truth.get("af_total_truth_hz"))
    component_sum = None
    if _float(truth.get("af_motion_truth_hz")) is not None and _float(truth.get("af_geometry_truth_hz")) is not None:
        component_sum = float(truth["af_motion_truth_hz"]) + float(truth["af_geometry_truth_hz"])
    total_delta = total - component_sum if total is not None and component_sum is not None else None
    count = mapped.get("axis_row_count")
    spacing = mapped.get("axis_spacing_hz")
    prf_closure = count * spacing - prf if count is not None and spacing is not None and prf is not None else None
    candidates = _non_doppler_candidates(truth, detections)
    candidate_deltas: list[tuple[int, dict[str, Any]]] = []
    if mapped.get("expected_row") is not None and count:
        candidate_deltas = [
            (_circular_row_distance(item["detection_row"], mapped["expected_row"], int(count)), item)
            for item in candidates
        ]
    best_delta, best = min(candidate_deltas, key=lambda item: (item[0], item[1]["det_id"])) if candidate_deltas else (None, None)
    component_ok = total_delta is not None and abs(total_delta) <= 1.0e-6
    axis_prf_ok = prf_closure is not None and abs(prf_closure) <= max(1.0e-3, abs(float(spacing or 0.0)) * 1.0e-3)
    positive_category = "TARGET_ONLY_POSITIVE_CONTROL" if bundle["role"] == "TO" else (
        "A0_NO_ERROR_ON_CONTROL" if bundle["canonical_condition"] == "A0" else "OTHER"
    )
    if mapped.get("status") != "VERIFIED_MAPPING" or not component_ok or not axis_prf_ok:
        status = "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED"
    else:
        status = "MAPPING_VERIFIED"
    return {
        **meta,
        "period_id": period, "beam_id": beam, "target_id": truth.get("target_id"),
        "truth_total_hz": total, "truth_motion_hz": _float(truth.get("af_motion_truth_hz")),
        "truth_geometry_hz": _float(truth.get("af_geometry_truth_hz")),
        "component_sum_hz": component_sum, "component_closure_error_hz": total_delta,
        "truth_row_raw": truth.get("row_truth"),
        "truth_row_raw_semantics": "standalone truth raw/ambiguity row; not assumed to be saved CFAR row",
        "axis_source_status": bundle["axes"]["status"],
        "axis_source_path": audit_path(bundle["axes"]["source_path"]),
        "axis_first_hz": mapped.get("axis_first_hz"), "axis_spacing_hz": spacing,
        "axis_row_count": count, "row_orientation": mapped.get("row_orientation"),
        "prf_hz": prf, "axis_prf_closure_hz": prf_closure,
        "folded_truth_hz": mapped.get("folded_truth_hz"), "axis_alias_hz": mapped.get("axis_alias_hz"),
        "expected_cfar_row": mapped.get("expected_row"), "mapping_status": status,
        "non_doppler_gate_candidate_count": len(candidates),
        "best_non_doppler_candidate_det_id": best.get("det_id") if best else None,
        "best_candidate_row": best.get("detection_row") if best else None,
        "best_candidate_circular_row_error": best_delta,
        "best_candidate_within_frozen_doppler_gate": best_delta is not None and best_delta <= 2,
        "positive_control_category": positive_category,
        "candidate_doppler_gate_tolerance_rows": 2,
    }


def _parameter_records(bundle: Mapping[str, Any], target_result: Mapping[str, Any], track_metrics: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    metadata = {key: bundle.get(key) for key in (
        "dataset", "dataset_key", "case_id", "seed", "condition", "canonical_condition", "role",
    )}
    output: list[dict[str, Any]] = []
    for metric, values in target_result.get("parameter_evaluability", {}).items():
        samples = target_result.get("parameter_samples", {}).get(metric, [])
        rmse = rmse_from_samples(samples)
        output.append({
            **metadata, "metric": metric,
            **values,
            "own_success_rmse": rmse.get("rmse"),
            "own_success_count": rmse.get("count", 0),
            "evaluator_status": "EVALUABLE" if values["truth_opportunities"] else "NOT_EVALUABLE",
        })
    truths = [row for row in bundle["truths"] if _visible_truth(row)]
    observations = (bundle.get("track") or {}).get("observations", [])
    associations = {row["sample_key"]: row for row in target_result.get("associations", [])}
    ground_samples: list[dict[str, Any]] = []
    ground_reasons: defaultdict[str, int] = defaultdict(int)
    for truth in truths:
        sample_key = f"{truth.get('target_id')}/{truth.get('period_id')}/{truth.get('beam_id')}"
        association = associations.get(sample_key)
        if association is None or not association.get("matched"):
            ground_reasons["no_matched_detection"] += 1
            continue
        linked = [row for row in observations if row.get("matched_this_frame") and row.get("output_payload")
                  and int(row["period_id"]) == int(float(truth["period_id"]))
                  and int(row["beam_id"]) == int(float(truth["beam_id"]))
                  and str(row.get("det_id")) == str(association.get("det_id"))]
        if len(linked) == 0:
            ground_reasons["no_confirmed_output_payload"] += 1
            continue
        if len(linked) > 1:
            ground_reasons["duplicate_output_payloads"] += 1
            continue
        speed = _float(linked[0].get("output_speed"))
        ve, vn = _float(truth.get("ve_mps")), _float(truth.get("vn_mps"))
        if speed is None or ve is None or vn is None:
            ground_reasons["missing_speed_value"] += 1
            continue
        ground_samples.append({"sample_key": sample_key, "error": speed - math.hypot(ve, vn)})
    ground_rmse = rmse_from_samples(ground_samples)
    output.append({
        **metadata, "metric": "ground_speed_mps", "truth_opportunities": len(truths),
        "matched_detection_opportunities": sum(bool(row.get("matched")) for row in associations.values()),
        "valid_parameter_opportunities": len(ground_samples),
        "missing_parameter_opportunities": len(truths) - len(ground_samples),
        "missing_reasons": dict(sorted(ground_reasons.items())),
        "own_success_rmse": ground_rmse.get("rmse"), "own_success_count": ground_rmse.get("count", 0),
        "evaluator_status": "EVALUABLE" if truths else "NOT_EVALUABLE",
    })
    return output


def _visible_periods(truths: Sequence[Mapping[str, Any]]) -> list[int]:
    return sorted({_int(row.get("period_id")) for row in truths if _visible_truth(row) and _int(row.get("period_id")) is not None})


def _evaluate_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    truths = bundle["truths"]
    valid_truths = [row for row in truths if _visible_truth(row)]
    axis_by_key = _axis_map(bundle["axes"]["rows"])
    missing_axes = sorted({
        (_int(row.get("period_id")), _int(row.get("beam_id"))) for row in valid_truths
        if (_int(row.get("period_id")), _int(row.get("beam_id"))) not in axis_by_key
    })
    if not valid_truths:
        return {"status": "NOT_EVALUABLE_NO_VISIBLE_STANDALONE_TRUTH", "target_result": None,
                "track_metrics": None, "parameter_records": []}
    if missing_axes:
        return {"status": "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED", "reason": f"missing_saved_production_axis:{missing_axes}",
                "target_result": None, "track_metrics": None, "parameter_records": []}
    doppler_rows = max((_int(row.get("doppler_row_count")) or 0 for row in bundle["axes"]["rows"]), default=0)
    if doppler_rows <= 0:
        return {"status": "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED", "reason": "invalid_saved_production_axis_row_count",
                "target_result": None, "track_metrics": None, "parameter_records": []}
    target_result = evaluate_targets_v2(
        truths, bundle["detections"]["rows"], doppler_rows=doppler_rows,
        doppler_axis_rows=bundle["axes"]["rows"],
    )
    track_metrics = None
    if bundle.get("track") and bundle["track"].get("observations"):
        matched_keys = {
            (int(row["period_id"]), int(row["beam_id"]), str(row["det_id"]))
            for row in target_result["associations"] if row.get("matched")
        }
        track_metrics = summarize_track_lineage_v2(
            observations=bundle["track"]["observations"],
            target_matched_detection_keys=matched_keys,
            visible_truth_periods=_visible_periods(valid_truths),
            payload_duplicate_key_count=bundle["track"].get("payload_duplicate_key_count", 0),
        )
    return {
        "status": "EVALUABLE", "target_result": target_result, "track_metrics": track_metrics,
        "parameter_records": _parameter_records(bundle, target_result, track_metrics),
    }


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
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list, tuple)) else value
                             for key, value in row.items()})


def _identity_group_status(bundles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    required = [
        "case_id", "seed", "target_velocity_mps", "target_snr_db", "target_id",
        "truth_sha256", "production_binary_sha256", "background_id",
    ]
    if bundles and bundles[0]["dataset"] in {"R8", "R12"}:
        required.append("semantic_config_sha256")
        role_variable = ["condition", "delay_error_ns"]
        scope = "within-case causal condition comparison; Production binary and normalized config must match"
    else:
        role_variable = ["condition", "delay_error_ns", "semantic_config_sha256"]
        scope = "R18 method regression; method/mode config is an intervention and is reported per condition"
    schema = {
        "required_equal": required,
        "role_variable": role_variable,
        "optional_reported": [
            "runner_commit_case_manifest", "runner_commit_run_manifest", "snapshot_runner_commit",
            "input_manifest_status", "input_period_sha256_digest", "applied_delay_ns", "correction_source",
        ],
    }
    result = validate_comparison_identity_v2([bundle["identity"] for bundle in bundles], schema)
    result["comparison_scope"] = scope
    if bundles and bundles[0]["dataset"] == "R18":
        result["per_condition_semantic_config_sha256"] = {
            str(bundle["condition"]): bundle["identity"].get("semantic_config_sha256") for bundle in bundles
        }
    return result


def _v2_opportunity_rows(bundle: Mapping[str, Any], evaluated: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = evaluated.get("target_result")
    if result is None:
        return [{**{key: bundle.get(key) for key in (
            "dataset", "dataset_key", "seed", "condition", "canonical_condition", "role",
        )}, "evaluation_status": evaluated.get("status"), "reason": evaluated.get("reason")}]
    audit_by_edge = {
        (str(row.get("period_id")), str(row.get("beam_id")), str(row.get("target_id")), str(row.get("det_id"))): row
        for row in result.get("candidate_pair_audit", [])
    }
    output = []
    for association in result["associations"]:
        key = (str(association["period_id"]), str(association["beam_id"]), str(association["target_id"]), str(association.get("det_id")))
        edge = audit_by_edge.get(key, {})
        output.append({
            **{field: bundle.get(field) for field in (
                "dataset", "dataset_key", "case_id", "seed", "target_velocity_mps", "target_snr_db",
                "condition", "canonical_condition", "role",
            )},
            "period_id": association.get("period_id"), "beam_id": association.get("beam_id"),
            "target_id": association.get("target_id"), "sample_key": association.get("sample_key"),
            "matched": association.get("matched"), "det_id": association.get("det_id"),
            "candidate_count": association.get("candidate_count"),
            "doppler_delta_rows": edge.get("doppler_delta_rows"),
            "range_delta_bins": edge.get("range_delta_bins"), "range_delta_m": edge.get("range_delta_m"),
            "position_error_m": association.get("position_error_m"),
            "radial_velocity_status": association.get("radial_velocity_status"),
            "radial_velocity_error_mps": association.get("radial_velocity_error_mps"),
            "evaluation_status": "EVALUABLE",
        })
    return output


def _r18_regression_rows(bundles: Sequence[Mapping[str, Any]], evaluations: Mapping[tuple[str, str, str, str], Mapping[str, Any]]) -> list[dict[str, Any]]:
    old_task = { (r["case_key"], r["condition"]): r for r in read_csv(R19_BASELINE / "baseline_task_metrics.csv") }
    old_track = { (r["case_key"], r["condition"]): r for r in read_csv(R19_BASELINE / "track_metrics.csv") }
    output: list[dict[str, Any]] = []
    for bundle in bundles:
        if bundle["dataset"] != "R18" or bundle["role"] != "ON":
            continue
        key = (bundle["dataset_key"], bundle["condition"])
        prior, prior_track = old_task.get(key, {}), old_track.get(key, {})
        evaluated = evaluations.get(("R18", bundle["dataset_key"], bundle["condition"], "ON"), {})
        result = evaluated.get("target_result")
        current = result.get("metrics", {}) if result else {}
        current_track = evaluated.get("track_metrics") or {}
        comparisons = [
            ("strict_truth_hits", prior.get("strict_truth_hits"), current.get("unique_hits"), "same frozen gate; valid-truth denominator correction only affects invalid truth rows"),
            ("truth_opportunities", prior.get("strict_truth_opportunities"), current.get("truth_opportunities"), "invalid visible truth rows are now audited outside the valid opportunity denominator"),
            ("misses", _minus(prior.get("strict_truth_opportunities"), prior.get("strict_truth_hits")), current.get("misses"), "miss denominator excludes invalid truth rows"),
            ("hit_fraction", prior.get("strict_hit_fraction"), current.get("truth_opportunity_hit_fraction"), "valid truth opportunity denominator"),
            ("confirmed_observations", prior_track.get("confirmed_track_periods"), current_track.get("confirmed_observation_count"), "R19 field counted confirmed state observations, despite its period label"),
            ("confirmed_unique_periods", None, current_track.get("confirmed_unique_period_count"), "new explicit unique-period count"),
            ("post_confirmation_match_rate", prior_track.get("post_confirmation_match_rate"), current_track.get("post_confirmation_match_rate"), "R19 UTC float comparison replaced by result ID to Production period mapping"),
            ("track_fragmentation_count", prior_track.get("track_fragmentation_count_observed"), current_track.get("track_fragmentation_count_observed"), "R20 counts sequential disjoint-ID handoffs; simultaneous target tracks are reported separately"),
        ]
        for metric, old, new, reason in comparisons:
            old_number, new_number = _float(old), _float(new)
            output.append({
                "case_key": bundle["dataset_key"], "condition": bundle["condition"], "metric": metric,
                "old_metric": old, "new_metric": new,
                "difference": new_number - old_number if old_number is not None and new_number is not None else None,
                "difference_reason": reason if old_number != new_number else "no numeric change or no prior value",
                "source_evidence": "R19 saved formal_matrix CSV vs R20 reevaluation from the same R18 saved branch data",
                "evaluator_version": CONTRACT_VERSION,
                "status": evaluated.get("status", "NOT_EVALUABLE"),
            })
    return output


def _minus(lhs: Any, rhs: Any) -> float | None:
    left, right = _float(lhs), _float(rhs)
    return left - right if left is not None and right is not None else None


def _track_rows(bundle: Mapping[str, Any], evaluated: Mapping[str, Any]) -> list[dict[str, Any]]:
    if bundle["role"] != "ON":
        return []
    metrics = evaluated.get("track_metrics")
    track = bundle.get("track") or {}
    return [{
        **{key: bundle.get(key) for key in (
            "dataset", "dataset_key", "case_id", "seed", "target_velocity_mps", "target_snr_db",
            "condition", "canonical_condition", "role",
        )},
        "status": metrics.get("status") if metrics else evaluated.get("status", "NOT_EVALUABLE"),
        "reason": metrics.get("reason") if metrics else evaluated.get("reason"),
        "track_debug_status": track.get("status"),
        "track_debug_reason": track.get("reason"),
        **(metrics or {}),
        "track_period_mapping_audit": track.get("period_mapping_audit"),
        "payload_count": track.get("payload_count"),
        "payload_duplicate_row_count": track.get("payload_duplicate_row_count"),
        "payload_duplicate_key_count": track.get("payload_duplicate_key_count"),
    }]


def _common_success_parameter_updates(
    parameter_rows: list[dict[str, Any]],
    bundles: Sequence[Mapping[str, Any]],
    evaluations: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
) -> None:
    by_case: defaultdict[tuple[str, str], list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = defaultdict(list)
    for bundle in bundles:
        if bundle["role"] == "ON":
            evaluated = evaluations.get((bundle["dataset"], bundle["dataset_key"], bundle["condition"], "ON"), {})
            if evaluated.get("target_result"):
                by_case[(bundle["dataset"], bundle["dataset_key"])].append((bundle, evaluated))
    for (dataset, dataset_key), group in by_case.items():
        metric_names = set.intersection(*[
            set(row.get("parameter_samples", {})) for _, evaluated in group
            for row in [evaluated["target_result"]]
        ]) if group else set()
        for metric in metric_names:
            samples_by_condition = {
                bundle["condition"]: evaluated["target_result"].get("parameter_samples", {}).get(metric, [])
                for bundle, evaluated in group
            }
            common = common_success_keys(samples_by_condition)
            common_rmse = {
                condition: rmse_from_samples([sample for sample in samples if sample.get("sample_key") in common])
                for condition, samples in samples_by_condition.items()
            }
            for row in parameter_rows:
                if row.get("dataset") == dataset and row.get("dataset_key") == dataset_key and row.get("metric") == metric:
                    summary = common_rmse.get(str(row.get("condition")), {})
                    row["common_success_opportunities"] = len(common)
                    row["common_success_rmse"] = summary.get("rmse")


def _pairwise_common_success_rows(
    bundles: Sequence[Mapping[str, Any]],
    evaluations: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
    identity_groups: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Compare each available estimate to Known on the same matched truths."""
    grouped: defaultdict[tuple[str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for bundle in bundles:
        if bundle["role"] == "ON":
            grouped[(bundle["dataset"], bundle["dataset_key"])][bundle["canonical_condition"]] = bundle
    output: list[dict[str, Any]] = []
    for case_key, conditions in sorted(grouped.items()):
        known_bundle = conditions.get("Known")
        known_result = evaluations.get((case_key[0], case_key[1], known_bundle["condition"], "ON"), {}).get("target_result") if known_bundle else None
        for condition, bundle in sorted(conditions.items()):
            if condition == "Known":
                continue
            result = evaluations.get((case_key[0], case_key[1], bundle["condition"], "ON"), {})
            target_result = result.get("target_result")
            names = set((target_result or {}).get("parameter_samples", {})) | set((known_result or {}).get("parameter_samples", {}))
            if not names:
                names = {"angle_deg", "position_m", "range_m", "folded_doppler_hz", "radial_velocity_mps"}
            for metric in sorted(names):
                left = (target_result or {}).get("parameter_samples", {}).get(metric, [])
                right = (known_result or {}).get("parameter_samples", {}).get(metric, [])
                common = common_success_keys({condition: left, "Known": right})
                left_rmse = rmse_from_samples([row for row in left if row.get("sample_key") in common])
                right_rmse = rmse_from_samples([row for row in right if row.get("sample_key") in common])
                left_metrics = (target_result or {}).get("metrics", {})
                known_metrics = (known_result or {}).get("metrics", {})
                status = "EVALUABLE" if common else (
                    result.get("status") if result.get("status") != "EVALUABLE" else
                    "NOT_EVALUABLE_NO_COMMON_SUCCESS"
                )
                output.append({
                    "dataset": case_key[0], "dataset_key": case_key[1],
                    "seed": bundle.get("seed"), "metric": metric,
                    "condition": condition, "reference_condition": "Known",
                    "status": status,
                    "truth_opportunities": left_metrics.get("truth_opportunities", known_metrics.get("truth_opportunities")),
                    "condition_hits": left_metrics.get("unique_hits"),
                    "known_hits": known_metrics.get("unique_hits"),
                    "condition_success_count": len(left), "known_success_count": len(right),
                    "common_success_opportunities": len(common),
                    "condition_rmse_common": left_rmse.get("rmse"),
                    "known_rmse_common": right_rmse.get("rmse"),
                    "rmse_difference_condition_minus_known": _minus(left_rmse.get("rmse"), right_rmse.get("rmse")),
                    "identity_status": identity_groups.get(case_key, {}).get("status"),
                    "comparison_scope": "same truth opportunities detected by both conditions; descriptive only",
                })
    return output


def _write_manifest(outdir: Path, bundles: Sequence[Mapping[str, Any]], identity_groups: Mapping[tuple[str, str], Mapping[str, Any]], gate: Mapping[str, Any]) -> None:
    entries = []
    for bundle in bundles:
        entries.append({
            "dataset": bundle["dataset"], "dataset_key": bundle["dataset_key"],
            "case_id": bundle["case_id"], "condition": bundle["condition"], "role": bundle["role"],
            "branch_dir": audit_path(bundle["branch_dir"]),
            "identity": bundle["identity"],
            "truth_path": audit_path(bundle["truth_path"]),
            "truth_sha256": sha256_file(bundle["truth_path"]) if bundle["truth_path"] else None,
            "axis_source_status": bundle["axes"]["status"],
            "axis_source_path": audit_path(bundle["axes"]["source_path"]),
            "input_files": file_manifest(
                bundle["case"], bundle["branch_dir"], bundle["truth_path"], bundle["detections"],
                bundle["axes"], bundle.get("track"),
            ),
        })
    manifest = {
        "schema_version": "r20-historical-input-manifest-v1",
        "evaluator_version": CONTRACT_VERSION,
        "read_only_inputs": True,
        "cuda_executed": False,
        "r19_baseline_csv_sha256": {
            name: sha256_file(R19_BASELINE / name)
            for name in ("baseline_task_metrics.csv", "track_metrics.csv", "parameter_estimation_metrics.csv")
        },
        "r19_script_sha256": sha256_file(R19_SCRIPT),
        "r19_evaluator_sha256": sha256_file(R19_EVALUATOR),
        "r20_script_sha256": sha256_file(R20_SCRIPT),
        "r20_adapter_sha256": sha256_file(R20_ADAPTER),
        "r20_evaluator_sha256": sha256_file(R20_EVALUATOR),
        "r20_test_sha256": sha256_file(R20_TESTS),
        "identity_groups": {f"{dataset}/{case_key}": value for (dataset, case_key), value in identity_groups.items()},
        "doppler_positive_control_gate": gate,
        "branches": entries,
    }
    path = outdir / "manifests/r20_input_identity_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_review(outdir: Path) -> dict[str, Any]:
    cases = list(discover_r18_cases()) + list(discover_historical_cases())
    bundles: list[dict[str, Any]] = []
    case_lookup: dict[tuple[str, str], Mapping[str, Any]] = {}
    for case in cases:
        case_lookup[(case["dataset"], case["dataset_key"])] = case
        for condition, role, branch_dir, record in _branch_items(case):
            if case["dataset"] == "R18":
                allowed_roles = {"ON", "TO"}
            else:
                allowed_roles = {"ON", "TO", "OFF"}
            if role not in allowed_roles:
                continue
            bundle = _condition_role_bundle(case, condition, role, branch_dir)
            bundle["case"] = case
            bundles.append(bundle)

    identity_groups: dict[tuple[str, str], dict[str, Any]] = {}
    for key in sorted({(bundle["dataset"], bundle["dataset_key"]) for bundle in bundles}):
        group = [bundle for bundle in bundles if (bundle["dataset"], bundle["dataset_key"]) == key and bundle["role"] == "ON"]
        identity_groups[key] = _identity_group_status(group) if group else {"status": "IDENTITY_INCOMPLETE", "reason": "no_ON_conditions"}

    doppler_rows: list[dict[str, Any]] = []
    for bundle in bundles:
        if bundle["role"] not in {"ON", "TO"}:
            continue
        for truth in bundle["truths"]:
            if _visible_truth(truth):
                doppler_rows.append(_doppler_audit_row(bundle, truth))
    control_rows = [row for row in doppler_rows if row["dataset"] == "R18" and
                    row["dataset_key"] == "seed606_v6p7_snr30" and
                    row["canonical_condition"] == "A0" and
                    row["role"] in {"TO", "ON"}]
    to_controls = [row for row in control_rows if row["role"] == "TO"]
    a0_controls = [row for row in control_rows if row["role"] == "ON"]
    to_pass = bool(to_controls) and all(
        row["mapping_status"] == "MAPPING_VERIFIED" and row["component_closure_error_hz"] is not None
        and abs(float(row["component_closure_error_hz"])) <= 1.0e-6
        and row["best_candidate_within_frozen_doppler_gate"]
        for row in to_controls
    )
    a0_axis_valid = bool(a0_controls) and all(row["mapping_status"] == "MAPPING_VERIFIED" for row in a0_controls)
    gate = {
        "status": "DOPPLER_MAPPING_VERIFIED" if to_pass and a0_axis_valid else "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED",
        "positive_control_case": "R18 seed606, 6.7 m/s, 30 dB, delay 0 ns",
        "target_only_TO_opportunities": len(to_controls),
        "target_only_TO_in_frozen_gate": sum(bool(row["best_candidate_within_frozen_doppler_gate"]) for row in to_controls),
        "A0_ON_opportunities": len(a0_controls),
        "A0_ON_in_frozen_gate": sum(bool(row["best_candidate_within_frozen_doppler_gate"]) for row in a0_controls),
        "A0_ON_axis_and_truth_closure_valid": a0_axis_valid,
        "frozen_doppler_gate_rows": 2,
        "gate_reason": "TO must align under the unchanged ±2-row gate; A0 ON is an operational control and its misses remain task outcomes.",
    }

    evaluations: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for bundle in bundles:
        key = (bundle["dataset"], bundle["dataset_key"], bundle["condition"], bundle["role"])
        if bundle["role"] == "OFF":
            continue
        if gate["status"] != "DOPPLER_MAPPING_VERIFIED":
            evaluations[key] = {"status": "DOPPLER_TRUTH_MAPPING_NOT_VERIFIED", "reason": "global positive control gate failed",
                                "target_result": None, "track_metrics": None, "parameter_records": []}
            continue
        evaluations[key] = _evaluate_bundle(bundle)

    # Saved R19 v1 tables are the immutable comparison baseline.
    regression_rows = _r18_regression_rows(bundles, evaluations)
    opportunity_rows: dict[str, list[dict[str, Any]]] = {"R8": [], "R12": []}
    seed_metrics: list[dict[str, Any]] = []
    r12_blind_rows: list[dict[str, Any]] = []
    track_rows: list[dict[str, Any]] = []
    parameter_rows: list[dict[str, Any]] = []
    known_closure_rows: list[dict[str, Any]] = []
    h0_rows: list[dict[str, Any]] = []

    for bundle in bundles:
        evaluated = evaluations.get((bundle["dataset"], bundle["dataset_key"], bundle["condition"], bundle["role"]), {})
        if bundle["role"] == "ON":
            track_rows.extend(_track_rows(bundle, evaluated))
            parameter_rows.extend(evaluated.get("parameter_records", []))
        if bundle["dataset"] in {"R8", "R12"} and bundle["role"] == "ON":
            opportunity_rows[bundle["dataset"]].extend(_v2_opportunity_rows(bundle, evaluated))
            result = evaluated.get("target_result")
            identity_status = identity_groups[(bundle["dataset"], bundle["dataset_key"])]["status"]
            metrics = result.get("metrics", {}) if result else {}
            track = evaluated.get("track_metrics") or {}
            task_status = evaluated.get("status", "NOT_EVALUABLE")
            seed_metrics.append({
                **{key: bundle.get(key) for key in (
                    "dataset", "dataset_key", "case_id", "seed", "target_velocity_mps", "target_snr_db",
                    "delay_error_ns", "condition", "canonical_condition",
                )},
                "identity_status": identity_status, "evaluator_status": task_status,
                "truth_opportunities": metrics.get("truth_opportunities"),
                "unique_hits": metrics.get("unique_hits"), "misses": metrics.get("misses"),
                "truth_opportunity_hit_fraction": metrics.get("truth_opportunity_hit_fraction"),
                "final_detection_rows": metrics.get("raw_detection_rows"),
                "truth_unmatched_detection_rows": metrics.get("truth_unmatched_detection_rows"),
                "track_metrics_status": track.get("status"),
                "post_confirmation_match_rate": track.get("post_confirmation_match_rate"),
                "track_fragmentation_count_observed": track.get("track_fragmentation_count_observed"),
                "maximum_simultaneous_target_track_count": track.get("maximum_simultaneous_target_track_count"),
                "evidence_scope": "one seed; descriptive feasibility evidence; no significance claim",
            })
            if bundle["dataset"] == "R12" and bundle["canonical_condition"] in {"D3", "M1"}:
                estimator = bundle["case"]["case_manifest"].get("conditions", {}).get(bundle["condition"], {})
                details = _find_estimator_details(estimator)
                applied_delay = _float(details.get("applied_delay_ns"))
                truth_delay = _float(bundle.get("delay_error_ns"))
                baseline = next((row for row in seed_metrics if row.get("dataset_key") == bundle["dataset_key"]
                                 and row.get("canonical_condition") == "B"), {})
                known = next((row for row in seed_metrics if row.get("dataset_key") == bundle["dataset_key"]
                              and row.get("canonical_condition") == "Known"), {})
                r12_blind_rows.append({
                    "seed": bundle.get("seed"), "condition": bundle["condition"],
                    "estimator": bundle["canonical_condition"],
                    "delay_truth_ns": bundle.get("delay_error_ns"),
                    **details,
                    "applied_delay_error_ns": applied_delay - truth_delay if applied_delay is not None and truth_delay is not None else None,
                    "evaluator_status": task_status,
                    "hits": metrics.get("unique_hits"), "truth_opportunities": metrics.get("truth_opportunities"),
                    "hit_fraction": metrics.get("truth_opportunity_hit_fraction"),
                    "delta_hits_vs_B": _minus(metrics.get("unique_hits"), baseline.get("unique_hits")),
                    "delta_hits_vs_Known": _minus(metrics.get("unique_hits"), known.get("unique_hits")),
                    "R12_is_single_seed": True,
                })
        if bundle["role"] == "OFF":
            h0 = _off_h0_row(bundle)
            h0_rows.append(h0)

    for case in cases:
        if case["dataset"] not in {"R8", "R12"}:
            continue
        closure = case["case_manifest"].get("known_correction_physical_closure", {})
        for role, role_data in closure.get("by_role", {}).items():
            for channel, values in role_data.get("channels", {}).items():
                known_closure_rows.append({
                    **_case_metadata(case), "dataset": case["dataset"], "role": role, "channel": channel,
                    **values, "source": audit_path(case["case_manifest_path"]),
                    "evidence_status": "RETAINED_MANIFEST_RESULT_RAW_IQ_RECOMPUTE_NOT_AVAILABLE",
                })

    _common_success_parameter_updates(parameter_rows, bundles, evaluations)
    pairwise_parameter_rows = _pairwise_common_success_rows(bundles, evaluations, identity_groups)
    report_root = outdir / "results"
    _write_csv(report_root / "r20_evaluator_regression.csv", regression_rows)
    _write_csv(report_root / "r20_doppler_truth_mapping_audit.csv", doppler_rows)
    _write_csv(report_root / "r8_opportunity_level_comparison.csv", opportunity_rows["R8"])
    _write_csv(report_root / "r8_seed_level_causal_metrics.csv", seed_metrics_for(seed_metrics, "R8"))
    _write_csv(report_root / "r8_known_closure_audit.csv", [row for row in known_closure_rows if row["dataset"] == "R8"])
    _write_csv(report_root / "r12_opportunity_level_comparison.csv", opportunity_rows["R12"])
    _write_csv(report_root / "r12_blind_estimator_comparison.csv", r12_blind_rows)
    _write_csv(report_root / "r12_known_closure_audit.csv", [row for row in known_closure_rows if row["dataset"] == "R12"])
    _write_csv(report_root / "r20_parameter_evaluability.csv", parameter_rows)
    _write_csv(report_root / "r20_parameter_pairwise_common_success.csv", pairwise_parameter_rows)
    _write_csv(report_root / "r20_track_metrics.csv", track_rows)
    _write_csv(report_root / "r20_h0_descriptive_metrics.csv", h0_rows)
    identity_rows = [{"dataset": dataset, "dataset_key": case_key, **result}
                     for (dataset, case_key), result in identity_groups.items()]
    _write_csv(report_root / "r20_identity_validation.csv", identity_rows)
    _write_manifest(outdir, bundles, identity_groups, gate)
    return {
        "case_count": len(cases), "branch_count": len(bundles), "evaluation_count": len(evaluations),
        "doppler_control_gate": gate,
        "identity_status_counts": dict(defaultdict_count(row["status"] for row in identity_groups.values())),
        "output_root": str(outdir),
        "generated_files": sorted(str(path.relative_to(outdir)) for path in outdir.rglob("*") if path.is_file()),
    }


def defaultdict_count(values: Sequence[str]) -> dict[str, int]:
    counts: defaultdict[str, int] = defaultdict(int)
    for value in values:
        counts[str(value)] += 1
    return counts


def seed_metrics_for(rows: Sequence[Mapping[str, Any]], dataset: str) -> list[dict[str, Any]]:
    selected = [dict(row) for row in rows if row.get("dataset") == dataset]
    by_seed = {(str(row.get("dataset_key")), str(row.get("canonical_condition"))): row for row in selected}
    for row in selected:
        if row.get("evaluator_status") != "EVALUABLE" or row.get("identity_status") != "IDENTITY_VALID":
            continue
        for comparator, base_key in (("B_minus_A0", "A0"), ("Known_minus_B", "B"), ("Known_minus_A0", "A0")):
            condition = str(row.get("canonical_condition"))
            if (comparator == "B_minus_A0" and condition != "B") or (comparator.startswith("Known") and condition != "Known"):
                continue
            baseline = by_seed.get((str(row.get("dataset_key")), base_key), {})
            delta = _minus(row.get("truth_opportunity_hit_fraction"), baseline.get("truth_opportunity_hit_fraction"))
            row[f"{comparator}_hit_fraction"] = delta
    return selected


def _off_h0_row(bundle: Mapping[str, Any]) -> dict[str, Any]:
    if bundle["dataset"] == "R18":
        cf = _cfar_metrics(bundle["branch_dir"])
        observed = cf.get("full_cut_hit_fraction")
        hits, valid = cf.get("full_hit_count"), cf.get("full_valid_cut_count")
        source_status = cf.get("status")
    else:
        cm = bundle["case"]["case_manifest"]
        condition = cm.get("conditions", {}).get(bundle["condition"], {})
        metrics = condition.get("metrics", {})
        observed = metrics.get("raw_empirical_false_hit_fraction", metrics.get("cell_false_hit_fraction"))
        hits, valid = metrics.get("cell_false_hit_count"), metrics.get("cell_false_hit_valid_cut_count")
        source_status = condition.get("production_status", condition.get("status"))
    return {
        **{key: bundle.get(key) for key in (
            "dataset", "dataset_key", "case_id", "seed", "condition", "canonical_condition", "role",
        )},
        "observed_cut_hit_count": hits,
        "valid_cut_count": valid,
        "observed_cut_hit_fraction": observed,
        "status": "DESCRIPTIVE_ONLY_NOT_STATISTICAL_PFA" if observed is not None else "NOT_EVALUABLE_MISSING_H0_COUNTER",
        "source_status": source_status,
        "empirical_pfa_status": "NOT_EVALUABLE",
        "interpretation": "saved OFF CUT/detection counter is descriptive only; correlated CUTs and no independent H0 holdout",
    }


def _find_estimator_details(value: Any) -> dict[str, Any]:
    on = value.get("input_preparation", {}).get("ON", {}) if isinstance(value, Mapping) else {}
    correction_audit = on.get("correction_audit", []) if isinstance(on, Mapping) else []
    return {
        "applied_delay_ns": value.get("applied_delay_ns", on.get("applied_delay_ns")),
        "correction_source": value.get("correction_source", on.get("correction_source")),
        "correction_applied": value.get("correction_applied"),
        "truth_used_in_estimator": value.get("truth_used_in_estimator", on.get("truth_used_in_estimator")),
        "input_role": value.get("estimator_input_role", on.get("estimator_input_role")),
        "correction_audit_statuses": [item.get("status") for item in correction_audit if isinstance(item, Mapping)],
        "identity_source": "R12 case_manifest condition input_preparation.ON",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=REPO_ROOT / "docs/research/phase_r/R20/evidence")
    args = parser.parse_args()
    result = run_review(args.output_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["doppler_control_gate"]["status"] == "DOPPLER_MAPPING_VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
