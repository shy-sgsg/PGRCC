#!/usr/bin/env python3
"""Run P4 truth matching and metric generation for one GMTI output directory."""

import argparse
import os

from gmti_eval_io import read_csv_rows, read_json, write_csv_rows, load_case_inputs
from gmti_eval_match import one_to_one_match, truth_id
from gmti_eval_metrics import detection_metrics, tracking_metrics


MATCH_FIELDS = [
    "truth_index", "truth_id", "item_index", "item_id", "cost",
    "position_error_m", "range_error_m", "time_error_s", "velocity_error_mps",
]


def visible_truths(rows):
    out = []
    seen = set()
    for row in rows:
        if row.get("visible", "1") in ("0", "false", "False"):
            continue
        key = (
            row.get("target_id", ""),
            row.get("period_id", ""),
            row.get("beam_id", ""),
            row.get("range_bin", row.get("expected_bin", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def write_report(path, inputs, cfg, manifest, det_metrics, trk_metrics, notes):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# P3/P4 GMTI Evaluation Report\n\n")
        f.write("## Inputs\n\n")
        for k, v in inputs.items():
            f.write(f"- {k}: `{v}`\n")
        f.write("\n## Run\n\n")
        f.write(f"- case_id: `{manifest.get('case_id', '')}`\n")
        f.write(f"- run_id: `{manifest.get('run_id', '')}`\n")
        f.write(f"- git_commit: `{manifest.get('git_commit', '')}`\n")
        f.write("\n## Match Gates\n\n")
        for k, v in cfg.items():
            f.write(f"- {k}: {v}\n")
        f.write("\n## Detection Metrics\n\n")
        for row in det_metrics:
            for k, v in row.items():
                f.write(f"- {k}: {v}\n")
        f.write("\n## Tracking Metrics\n\n")
        for row in trk_metrics:
            for k, v in row.items():
                f.write(f"- {k}: {v}\n")
        f.write("\n## Notes\n\n")
        for note in notes:
            f.write(f"- {note}\n")


def main():
    parser = argparse.ArgumentParser(description="Run GMTI P4 truth evaluation.")
    parser.add_argument("--output-dir", required=True, help="Algorithm output directory")
    parser.add_argument("--truth-dir", default="", help="Truth directory; defaults to ../truth")
    parser.add_argument("--match-config", default="configs/eval/match_config.json")
    parser.add_argument("--report-dir", default="", help="Defaults to <output-dir>/reports")
    args = parser.parse_args()

    cfg = read_json(args.match_config)
    inputs = load_case_inputs(args.output_dir, args.truth_dir)
    detections = read_csv_rows(inputs["detection_results"])
    tracks = read_csv_rows(inputs["track_results"])
    truths = visible_truths(read_csv_rows(inputs["truth"]))
    manifest = read_json(inputs["run_manifest"])
    report_dir = args.report_dir or os.path.join(args.output_dir, "reports")
    os.makedirs(report_dir, exist_ok=True)

    det_use_velocity = bool(cfg.get("detection_use_velocity_gate", True))
    det_matches, used_det, used_truth = one_to_one_match(
        detections, truths, cfg, "det_id", use_beam=True, use_range=True,
        use_velocity=det_use_velocity)
    unmatched_truth = [
        {"truth_index": i, "truth_id": truth_id(row, i), **row}
        for i, row in enumerate(truths) if i not in used_truth
    ]
    false_alarms = [
        {"det_index": i, **row}
        for i, row in enumerate(detections) if i not in used_det
    ]
    det_metric_rows = detection_metrics(len(truths), len(detections), det_matches, false_alarms)

    # Current TrackManager writes every internal state (including Tentative and
    # Coasted) to track_results.csv and marks only protocol-eligible,
    # confirmed tracks with is_output=1.  Falling back to every state merely
    # because this frame has zero confirmed outputs turns tentative detections
    # into fake tracks and contradicts the pipe protocol.  Keep the fallback
    # only for legacy CSV files that predate the is_output column entirely.
    has_output_flag = bool(tracks) and "is_output" in tracks[0]
    track_pool = (
        [r for r in tracks
         if str(r.get("is_output", "0")).lower() in ("1", "true")]
        if has_output_flag else tracks
    )
    track_use_velocity = bool(cfg.get("track_use_velocity_gate", True))
    trk_matches, used_track, used_truth_track = one_to_one_match(
        track_pool, truths, cfg, "track_id", use_beam=False, use_range=False,
        use_velocity=track_use_velocity)
    trk_metric_rows = tracking_metrics(len(truths), len(track_pool), trk_matches)

    write_csv_rows(os.path.join(report_dir, "truth_match_results.csv"), MATCH_FIELDS, det_matches)
    write_csv_rows(os.path.join(report_dir, "unmatched_truth.csv"),
                   ["truth_index", "truth_id"] + (list(truths[0].keys()) if truths else []),
                   unmatched_truth)
    write_csv_rows(os.path.join(report_dir, "false_alarm_results.csv"),
                   ["det_index"] + (list(detections[0].keys()) if detections else []),
                   false_alarms)
    write_csv_rows(os.path.join(report_dir, "detection_metrics.csv"),
                   list(det_metric_rows[0].keys()), det_metric_rows)
    write_csv_rows(os.path.join(report_dir, "track_truth_match.csv"), MATCH_FIELDS, trk_matches)
    write_csv_rows(os.path.join(report_dir, "tracking_metrics.csv"),
                   list(trk_metric_rows[0].keys()), trk_metric_rows)

    notes = []
    if not truths:
        notes.append("No visible truth rows were found; all detections are counted as false alarms.")
    if detections and not det_matches:
        notes.append("Detections were present but none passed the configured truth gates.")
    if track_pool and not trk_matches:
        notes.append("Tracks were present but none passed the configured truth gates.")
    if not track_pool:
        notes.append(
            "No confirmed current-period output tracks were available for "
            "track-truth matching. Tentative/internal states are not protocol targets.")
    write_report(os.path.join(report_dir, "p3_p4_eval_report.md"),
                 inputs, cfg, manifest, det_metric_rows, trk_metric_rows, notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
