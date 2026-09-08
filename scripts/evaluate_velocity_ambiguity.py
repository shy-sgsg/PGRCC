#!/usr/bin/env python3
"""Evaluate P6 ambiguity diagnostics from production detection output.

This script only matches truth to ``GMTI_core`` detections and reduces the
diagnostic fields emitted by the production solver.  It does not estimate or
replace Doppler/phase ambiguity itself.
"""

from __future__ import annotations

import argparse
import math
import os

from gmti_eval_io import (
    load_case_inputs,
    read_csv_rows,
    read_json,
    to_float,
    to_int,
    write_csv_rows,
)
from gmti_eval_match import one_to_one_match, truth_id


def visible_truths(rows):
    return [row for row in rows
            if str(row.get("visible", "1")).lower() not in ("0", "false")]


def signed_truth_velocity(row):
    for key in ("vr_self_mps", "target_vr_self_mps", "v_truth_mps"):
        value = to_float(row, key)
        if math.isfinite(value):
            return value
    return math.nan


def signed_detection_velocity(row):
    for key in ("radial_velocity_mps", "v_radial_mps"):
        value = to_float(row, key)
        if math.isfinite(value):
            return value
    return math.nan


def percentile(values, fraction):
    values = sorted(v for v in values if math.isfinite(v))
    if not values:
        return math.nan
    index = fraction * (len(values) - 1)
    lo = int(math.floor(index))
    hi = int(math.ceil(index))
    if lo == hi:
        return values[lo]
    weight = index - lo
    return values[lo] * (1.0 - weight) + values[hi] * weight


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--algorithm-output-dir", required=True)
    parser.add_argument("--truth-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--match-config", default="configs/eval/match_config.json")
    parser.add_argument("--wrong-velocity-error-mps", type=float, default=0.5)
    args = parser.parse_args()

    inputs = load_case_inputs(args.algorithm_output_dir, args.truth_dir)
    detections = read_csv_rows(inputs["detection_results"])
    truths = visible_truths(read_csv_rows(inputs["truth"]))
    cfg = read_json(args.match_config)
    manifest = read_json(inputs["run_manifest"])
    matches, used_detection, used_truth = one_to_one_match(
        detections, truths, cfg, "det_id",
        use_beam=True, use_range=True, use_velocity=False)

    rows = []
    for match in matches:
        det = detections[match["item_index"]]
        truth = truths[match["truth_index"]]
        truth_v = signed_truth_velocity(truth)
        estimated_v = signed_detection_velocity(det)
        velocity_error = (abs(estimated_v - truth_v)
                          if math.isfinite(estimated_v) and math.isfinite(truth_v)
                          else math.nan)
        prf = to_float(det, "prf_hz")
        af_alias = to_float(det, "af_alias_hz")
        af_total_truth = to_float(truth, "af_total_truth_hz")
        truth_order = (int(round((af_total_truth - af_alias) / prf))
                       if all(math.isfinite(v) for v in (af_total_truth, af_alias, prf))
                       and prf > 0.0 else None)
        estimated_order = to_int(det, "doppler_ambiguity_order", 0)
        truth_phase_order = to_int(truth, "phase_ambiguity_order_truth", 0)
        estimated_phase_order = to_int(det, "phase_ambiguity_order", 0)
        ambiguity_status = det.get("ambiguity_status", "")
        resolved = ambiguity_status == "resolved"
        order_correct = truth_order is not None and estimated_order == truth_order
        phase_order_correct = estimated_phase_order == truth_phase_order
        wrong_confident = resolved and (
            not order_correct or
            (math.isfinite(velocity_error) and
             velocity_error > args.wrong_velocity_error_mps))
        rows.append({
            "period_id": truth.get("period_id", ""),
            "beam_id": truth.get("beam_id", ""),
            "target_id": truth_id(truth, match["truth_index"]),
            "det_id": det.get("det_id", match["item_index"]),
            "matched": 1,
            "missed": 0,
            "false_alarm": 0,
            "truth_velocity_mps": truth_v,
            "estimated_radial_velocity_mps": estimated_v,
            "velocity_error_mps": velocity_error,
            "position_error_m": match.get("position_error_m", math.nan),
            "lambda_m": det.get("lambda_m", ""),
            "prf_hz": det.get("prf_hz", ""),
            "velocity_ambiguity_interval_mps": det.get("velocity_ambiguity_interval_mps", ""),
            "observed_doppler_hz": det.get("af_alias_hz", ""),
            "unwrapped_doppler_hz": det.get("af_unwrapped_hz", ""),
            "doppler_ambiguity_order_truth": "" if truth_order is None else truth_order,
            "doppler_ambiguity_order": estimated_order,
            "phase_wrapped_rad": det.get("phase_wrapped_rad", ""),
            "phase_unwrapped_rad": det.get("phase_unwrapped_rad", ""),
            "phase_ambiguity_order_truth": truth_phase_order,
            "phase_ambiguity_order": estimated_phase_order,
            "velocity_solver": det.get("velocity_solver", ""),
            "velocity_candidate_count": det.get("velocity_candidate_count", ""),
            "velocity_best_cost": det.get("velocity_best_cost", ""),
            "velocity_second_cost": det.get("velocity_second_cost", ""),
            "velocity_cost_margin": det.get("velocity_cost_margin", ""),
            "velocity_confidence": det.get("velocity_confidence", ""),
            "ambiguity_status": ambiguity_status,
            "order_correct": int(order_correct),
            "phase_order_correct": int(phase_order_correct),
            "resolved": int(resolved),
            "wrong_confident": int(wrong_confident),
        })

    for index, truth in enumerate(truths):
        if index in used_truth:
            continue
        rows.append({
            "period_id": truth.get("period_id", ""),
            "beam_id": truth.get("beam_id", ""),
            "target_id": truth_id(truth, index),
            "matched": 0, "missed": 1, "false_alarm": 0,
            "truth_velocity_mps": signed_truth_velocity(truth),
            "ambiguity_status": "missed",
            "resolved": 0, "wrong_confident": 0,
        })
    for index, det in enumerate(detections):
        if index in used_detection:
            continue
        rows.append({
            "period_id": det.get("period_id", ""),
            "beam_id": det.get("beam_id", ""),
            "det_id": det.get("det_id", index),
            "matched": 0, "missed": 0, "false_alarm": 1,
            "estimated_radial_velocity_mps": signed_detection_velocity(det),
            "ambiguity_status": det.get("ambiguity_status", "false_alarm"),
            "resolved": int(det.get("ambiguity_status", "") == "resolved"),
            "wrong_confident": 0,
        })

    matched = [row for row in rows if row.get("matched") == 1]
    resolved_rows = [row for row in matched if row.get("resolved") == 1]
    velocity_errors = [row.get("velocity_error_mps", math.nan)
                       for row in resolved_rows]
    position_errors = [row.get("position_error_m", math.nan) for row in matched]
    wrong_count = sum(row.get("wrong_confident", 0) for row in resolved_rows)
    summary = [{
        "case_id": manifest.get("case_id", ""),
        "run_id": manifest.get("run_id", ""),
        "truth_count": len(truths),
        "detection_count": len(detections),
        "matched_count": len(matched),
        "missed_count": len(truths) - len(matched),
        "false_alarm_count": len(detections) - len(matched),
        "resolved_count": len(resolved_rows),
        "resolved_rate": len(resolved_rows) / len(matched) if matched else 0.0,
        "unresolved_rate": (len(matched) - len(resolved_rows)) / len(matched) if matched else 0.0,
        "ambiguity_order_accuracy": (
            sum(row.get("order_correct", 0) for row in resolved_rows) / len(resolved_rows)
            if resolved_rows else math.nan),
        "phase_order_accuracy": (
            sum(row.get("phase_order_correct", 0) for row in resolved_rows) / len(resolved_rows)
            if resolved_rows else math.nan),
        "wrong_confident_count": wrong_count,
        "wrong_confident_rate": wrong_count / len(resolved_rows) if resolved_rows else 0.0,
        "velocity_rmse_mps": (
            math.sqrt(sum(v * v for v in velocity_errors if math.isfinite(v)) /
                      sum(math.isfinite(v) for v in velocity_errors))
            if any(math.isfinite(v) for v in velocity_errors) else math.nan),
        "velocity_p95_error_mps": percentile(velocity_errors, 0.95),
        "position_rmse_m": (
            math.sqrt(sum(v * v for v in position_errors if math.isfinite(v)) /
                      sum(math.isfinite(v) for v in position_errors))
            if any(math.isfinite(v) for v in position_errors) else math.nan),
        "position_p95_error_m": percentile(position_errors, 0.95),
        "status": "ok",
    }]

    os.makedirs(args.report_dir, exist_ok=True)
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    write_csv_rows(os.path.join(args.report_dir, "velocity_ambiguity_metrics.csv"),
                   fields, rows)
    write_csv_rows(os.path.join(args.report_dir, "velocity_ambiguity_summary.csv"),
                   list(summary[0]), summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
