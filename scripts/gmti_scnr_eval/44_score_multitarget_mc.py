#!/usr/bin/env python3
"""由生产检测和 TrackManager 审计计算125目标三周期统计。"""
from __future__ import annotations
import argparse
import csv
import json
import math
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import linear_sum_assignment

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_track_manager_run import audit_debug_dir


def read(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write(path, rows):
    if not rows:
        raise ValueError("cannot save an empty result table")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def angle_stats(errors):
    errors = np.asarray(errors, dtype=float)
    if not np.all(np.isfinite(errors)):
        raise ValueError("matched angle must be finite")
    return {"angle_count": len(errors),
            "angle_bias": float(errors.mean()) if len(errors) else math.nan,
            "angle_MAE": float(np.abs(errors).mean()) if len(errors) else math.nan,
            "angle_RMSE": float(np.sqrt(np.mean(errors**2))) if len(errors) else math.nan,
            "angle_sum": float(errors.sum()), "angle_abs_sum": float(np.abs(errors).sum()),
            "angle_square_sum": float(np.square(errors).sum())}


def score(case: Path, result: Path, min_points: int, mc_id: int):
    doc = json.loads((case / "scenario.json").read_text())
    nrows = doc["waveform"]["pulse_num"]
    design = {r["target_id"]: r for r in json.loads((case / "design.json").read_text())["targets"]}
    truth = read(case / "stage2/truth/truth_targets_by_beam.csv")
    debug_dirs = list((result / "track_debug").glob("*/track_states.csv"))
    if len(debug_dirs) != 1:
        raise ValueError("must have exactly one authoritative TrackManager run")
    debug = debug_dirs[0].parent
    audit = audit_debug_dir(debug)
    if audit["status"] != "pass":
        raise ValueError(f"production TrackManager audit failed: {audit}")
    track_detections = read(debug / "track_detections.csv")
    states = read(debug / "track_states.csv")
    rows, detection_truth, associations = [], {}, {}
    extra_detections, max_link_error = 0, 0.0
    for period in range(3):
        rid = period + 1
        current_truth = [r for r in truth if int(r["period_id"]) == period]
        if len(current_truth) != 125:
            raise ValueError("wrong truth denominator")
        probes = {r["label"]: r for r in read(result / f"debug/cfar_GMTI{rid:02d}_beam001_probes.csv")}
        detections = read(result / f"detection_results_GMTI{rid:02d}.csv")
        # Every RD gate is disjoint by design; no truth can steal a neighbour.
        candidates = {}
        for j, d in enumerate(detections):
            matches = []
            for t in current_truth:
                probe = probes[t["target_id"]]
                dr = abs(float(d["row"]) - float(probe["row"]))
                dr = min(dr, nrows - dr)
                dc = abs(float(d["col"]) - float(probe["col"]))
                if dr <= 3 and dc <= 3:
                    matches.append((t["target_id"], dr*dr + dc*dc))
            if len(matches) > 1:
                raise ValueError("truth matching gates overlap")
            if matches:
                tid, cost = matches[0]
                candidates.setdefault(tid, []).append((cost, j))
        selected = {tid: min(items)[1] for tid, items in candidates.items()}
        extra_detections += len(detections) - len(selected)
        # Link CSV records to actual TrackManager inputs by production position
        # and ground range, independently of truth. The raw detection CSV stores
        # slant range, whereas the production GMTI packet stores ground range.
        # Packet latitude/longitude quantization contributes less than 1 cm here.
        td = [r for r in track_detections if int(r["result_id"]) == rid]
        if len(td) != len(detections):
            raise ValueError(f"detection/TrackManager count differs in period {period}")
        links = {}
        if td:
            costs = np.full((len(detections), len(td)), 1e12)
            for j, d in enumerate(detections):
                for k, tr in enumerate(td):
                    distance = math.hypot(float(d["new_e"])-float(tr["e"]),
                                          float(d["new_n"])-float(tr["n"]))
                    ground_range = math.hypot(float(d["new_e"])-float(d["platform_e"]),
                                              float(d["new_n"])-float(d["platform_n"]))
                    drange = abs(ground_range-float(tr["range"]))
                    if distance <= 0.02 and drange <= 0.02:
                        costs[j, k] = distance + drange
            jj, kk = linear_sum_assignment(costs)
            if np.any(costs[jj, kk] >= 1e12):
                raise ValueError(f"cannot trace detection CSV to production inputs, period={period}")
            max_link_error = max(max_link_error, float(costs[jj, kk].max()))
            links = {int(j): td[int(k)] for j, k in zip(jj, kk)}
        for t in current_truth:
            tid = t["target_id"]
            probe = probes[tid]
            detected = tid in selected
            # Truth azimuth uses the command convention; use an explicit EN
            # bearing relative to the known northward platform heading (west=180).
            true_angle = ((180 + float(t["azimuth_deg"]) + 180) % 360) - 180
            estimate, error = math.nan, math.nan
            det_index = track_id = -1
            if detected:
                j = selected[tid]
                d, tr = detections[j], links[j]
                estimate = math.degrees(math.atan2(float(d["new_n"])-float(d["platform_n"]),
                                                    float(d["new_e"])-float(d["platform_e"])))
                error = (estimate - true_angle + 180) % 360 - 180
                det_index, track_id = int(tr["det_index"]), int(tr["matched_track_id"])
                detection_truth[rid, det_index] = tid
                if track_id >= 0:
                    associations.setdefault(track_id, []).append((rid, tid))
            power, background = float(probe["power"]), float(probe["background_mean"])
            if not (math.isfinite(power) and math.isfinite(background) and background > 0):
                raise ValueError("invalid SCNR probe")
            rows.append({"min_points": min_points, "mc_id": mc_id,
                "SCNR": design[tid]["SCNR_target"], "period": period, "target_id": tid,
                "detected": int(detected), "true_angle": true_angle, "estimated_angle": estimate,
                "angle_error": error, "track_confirmed": 0,
                "production_track_confirmed": 0, "track_two_of_three": 0,
                "det_index": det_index,
                "track_id": track_id, "output_cut_power": power, "output_noise_power": background,
                "measured_output_SCNR_linear": power / background - 1})
    # Keep the production TrackManager result auditable, but make the primary
    # track metric truth based when the packed scene causes cross-target
    # association. The user's accepted definition is one real target counted
    # once if it has detections in at least two of its three periods.
    production_confirmed = set()
    false_confirmed = set()
    for s in states:
        if s["state"] != "Confirmed" or s["matched_this_frame"] != "1":
            continue
        rid, track_id, det_index = int(s["result_id"]), int(s["track_id"]), int(s["matched_det_index"])
        tid = detection_truth.get((rid, det_index))
        history = associations.get(track_id, [])
        identity = {t for p, t in history if p <= rid}
        periods = {p for p, t in history if p <= rid and t == tid}
        if tid is not None and identity == {tid} and len(periods) >= 2:
            production_confirmed.add(tid)
            for r in rows:
                if r["period"] == rid-1 and r["target_id"] == tid:
                    r["production_track_confirmed"] = 1
        else:
            false_confirmed.add(track_id)
    detection_counts = {}
    for row in rows:
        detection_counts[row["target_id"]] = detection_counts.get(row["target_id"], 0) + int(row["detected"])
    truth_two_of_three = {
        target_id for target_id, count in detection_counts.items() if count >= 2
    }
    for row in rows:
        row["track_two_of_three"] = int(row["target_id"] in truth_two_of_three)
        # ``track_confirmed`` is the primary metric column from this version
        # onward. ``production_track_confirmed`` preserves the old association
        # result for comparison and audit.
        row["track_confirmed"] = row["track_two_of_three"]
    trials = []
    for scnr in sorted({d["SCNR_target"] for d in design.values()}):
        group = [r for r in rows if r["SCNR"] == scnr]
        ids = {r["target_id"] for r in group}
        assert len(group) == 15 and len(ids) == 5
        measured = float(np.mean([r["measured_output_SCNR_linear"] for r in group]))
        production_group = {target_id for target_id in ids if target_id in production_confirmed}
        truth_group = {target_id for target_id in ids if target_id in truth_two_of_three}
        trials.append({"min_points": min_points, "mc_id": mc_id, "SCNR_target": scnr,
            "measured_output_SCNR": 10*math.log10(measured) if measured > 0 else math.nan,
            "measured_output_SCNR_linear": measured,
            "target_total": 15, "target_detected": sum(r["detected"] for r in group),
            "Pd_target": sum(r["detected"] for r in group)/15,
            "track_total": 5, "track_detected": len(truth_group), "Pd_track": len(truth_group)/5,
            "production_track_detected": len(production_group),
            "production_Pd_track": len(production_group)/5,
            "track_metric": "truth_target_detected_at_least_2_of_3_periods",
            **angle_stats([r["angle_error"] for r in group if r["detected"]])})
    destination = result / "metrics"
    write(destination / "trial_results.csv", trials)
    write(destination / "target_results.csv", rows)
    audit.update(extra_detections=extra_detections, false_confirmed_track_count=len(false_confirmed),
                 maximum_csv_track_link_error_m=max_link_error,
                 target_total=len(rows), target_detected=sum(r["detected"] for r in rows),
                 track_total=125, track_detected=len(truth_two_of_three),
                 production_track_detected=len(production_confirmed),
                 track_metric="truth_target_detected_at_least_2_of_3_periods",
                 angle_convention="ENU counterclockwise bearing degrees; wrap errors to [-180,180)",
                 truth_match="disjoint +/-3 circular Doppler cells and +/-3 range cells; nearest RD, one-to-one")
    (destination / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2)+"\n")
    return trials, rows, audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--min-points", type=int, required=True)
    parser.add_argument("--mc-id", type=int, default=-1)
    args = parser.parse_args()
    trials, rows, audit = score(args.case, args.result, args.min_points, args.mc_id)
    print(json.dumps({k:audit[k] for k in ["target_total", "target_detected", "track_total", "track_detected", "extra_detections"]}))
