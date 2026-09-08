#!/usr/bin/env python3
"""P9 detection-level fault injection around the production TrackManager.

This script synthesizes only detector outputs and truth trajectories.  Every
association, Kalman update, confirmation, coast/delete decision and protocol
payload is produced by ``build/track_detection_eval``, which directly invokes
the production ``TrackManager`` implementation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time

from gmti_eval_match import min_cost_assignment


REPO_ROOT = Path(__file__).resolve().parents[1]

TRUTH_FIELDS = [
    "period_id", "utc", "target_id", "active", "e", "n", "ve", "vn",
    "speed", "heading",
]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"], cwd=REPO_ROOT,
        text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def git_provenance() -> dict:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=REPO_ROOT, capture_output=True, check=False)
    source_roots = [
        "CMakeLists.txt", "configs", "experiments", "include", "scripts",
        "simulator", "src", "tests", "tools",
    ]
    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD", "--", *source_roots], cwd=REPO_ROOT,
        capture_output=True, check=False)
    digest = hashlib.sha256(diff.stdout)
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT, capture_output=True, check=False)
    included_roots = {
        "configs", "experiments", "include", "scripts", "simulator",
        "src", "tests", "tools",
    }
    for raw_name in untracked.stdout.split(b"\0"):
        if not raw_name:
            continue
        relative = Path(raw_name.decode(errors="surrogateescape"))
        path = REPO_ROOT / relative
        if (not relative.parts or relative.parts[0] not in included_roots
                or not path.is_file()):
            continue
        digest.update(raw_name)
        digest.update(bytes.fromhex(sha256(path)))
    source_dirty = bool(diff.stdout) or any(
        raw_name and Path(raw_name.decode(errors="surrogateescape")).parts
        and Path(raw_name.decode(errors="surrogateescape")).parts[0] in included_roots
        for raw_name in untracked.stdout.split(b"\0"))
    return {
        "git_commit": git_commit(),
        "git_dirty": bool(status.stdout),
        "source_dirty": source_dirty,
        "worktree_fingerprint": digest.hexdigest() if source_dirty else "clean",
    }


def merged(*values: dict) -> dict:
    result = {}
    for value in values:
        result.update(value or {})
    return result


def period_times(periods: int, dt: float, scenario: dict) -> list[float]:
    explicit = scenario.get("period_dt_s")
    times = [0.0]
    for index in range(1, periods):
        step = float(explicit[(index - 1) % len(explicit)]) if explicit else dt
        times.append(times[-1] + step)
    return times


def active_window(target_index: int, periods: int, scenario: dict) -> tuple[int, int]:
    windows = scenario.get("active_windows")
    if windows and target_index < len(windows):
        return int(windows[target_index][0]), int(windows[target_index][1])
    return 1, periods


def truth_for_case(case: dict, defaults: dict) -> list[dict]:
    scenario = merged(defaults, case.get("scenario", {}))
    kind = scenario.get("type", "straight")
    periods = int(scenario.get("periods", 20))
    dt = float(scenario.get("dt_s", 1.0))
    times = period_times(periods, dt, scenario)
    count = int(scenario.get("target_count", 1))
    if kind in {"crossing", "merge_split"}:
        count = max(2, count)
    speed = float(scenario.get("speed_mps", 20.0))
    spacing = float(scenario.get("spacing_m", 250.0))
    rows = []
    integrated: dict[int, tuple[float, float]] = {}

    for pidx, elapsed in enumerate(times, start=1):
        for target_index in range(count):
            start, end = active_window(target_index, periods, scenario)
            active = start <= pidx <= end
            target_id = f"T{target_index + 1:02d}"
            lane = target_index - 0.5 * (count - 1)
            e0 = lane * spacing
            n0 = lane * float(scenario.get("cross_spacing_m", 80.0))
            ve = speed
            vn = 0.0
            e = e0 + ve * elapsed
            n = n0

            if kind == "accelerating":
                acceleration = float(scenario.get("acceleration_mps2", 3.0))
                ve = speed + acceleration * elapsed
                e = e0 + speed * elapsed + 0.5 * acceleration * elapsed * elapsed
            elif kind == "turn":
                turn_rate = math.radians(float(scenario.get("turn_rate_deg_s", 6.0)))
                heading0 = math.radians(float(scenario.get("heading_deg", 0.0)))
                if abs(turn_rate) < 1e-12:
                    ve = speed * math.cos(heading0)
                    vn = speed * math.sin(heading0)
                    e = e0 + ve * elapsed
                    n = n0 + vn * elapsed
                else:
                    heading = heading0 + turn_rate * elapsed
                    ve = speed * math.cos(heading)
                    vn = speed * math.sin(heading)
                    e = e0 + speed / turn_rate * (math.sin(heading) - math.sin(heading0))
                    n = n0 - speed / turn_rate * (math.cos(heading) - math.cos(heading0))
            elif kind == "crossing":
                midpoint = times[-1] * 0.5
                if target_index % 2 == 0:
                    e = -speed * midpoint + speed * elapsed
                    n = -float(scenario.get("cross_offset_m", 10.0))
                    ve, vn = speed, 0.0
                else:
                    e = float(scenario.get("cross_offset_m", 10.0))
                    n = -speed * midpoint + speed * elapsed
                    ve, vn = 0.0, speed
            elif kind == "merge_split":
                midpoint = times[-1] * 0.5
                sign = -1.0 if target_index % 2 == 0 else 1.0
                e = speed * elapsed
                n = sign * abs(elapsed - midpoint) * float(
                    scenario.get("merge_cross_speed_mps", 8.0))
                ve = speed
                vn = (-sign if elapsed < midpoint else sign) * float(
                    scenario.get("merge_cross_speed_mps", 8.0))
            elif kind == "parallel":
                ve = speed + lane * float(scenario.get("speed_step_mps", 0.0))
                e = e0 + ve * elapsed
                n = n0
            elif kind == "arbitrary_heading":
                heading = math.radians(float(scenario.get("heading_deg", 35.0)) + lane * 3.0)
                ve = speed * math.cos(heading)
                vn = speed * math.sin(heading)
                e = e0 + ve * elapsed
                n = n0 + vn * elapsed

            # For variable frame intervals, truth is still evaluated at the
            # actual elapsed time.  ``integrated`` only protects custom future
            # scenario extensions from accidentally resetting a trajectory.
            integrated[target_index] = (e, n)
            rows.append({
                "period_id": pidx,
                "utc": 100000.0 + elapsed,
                "target_id": target_id,
                "active": 1 if active else 0,
                "e": e,
                "n": n,
                "ve": ve,
                "vn": vn,
                "speed": math.hypot(ve, vn),
                "heading": math.degrees(math.atan2(vn, ve)),
            })
    return rows


def expected_false_alarm_count(rate: float, rng: random.Random) -> int:
    base = int(math.floor(max(0.0, rate)))
    return base + (1 if rng.random() < max(0.0, rate) - base else 0)


def detections_from_truth(truth: list[dict], injection: dict,
                          seed: int, frame_times: list[float] | None = None
                          ) -> list[dict]:
    rng = random.Random(seed)
    by_period: dict[int, list[dict]] = {}
    for row in truth:
        by_period.setdefault(int(row["period_id"]), []).append(row)
    forced_misses = {int(value) for value in injection.get("miss_periods", [])}
    speed_jump_periods = {int(value) for value in injection.get("speed_jump_periods", [])}
    outlier_periods = {int(value) for value in injection.get("outlier_periods", [])}
    miss_probability = float(injection.get("random_miss_probability", 0.0))
    position_noise = float(injection.get("position_noise_std_m", 0.0))
    velocity_noise = float(injection.get("velocity_noise_std_mps", 0.0))
    timestamp_jitter = float(injection.get("timestamp_jitter_s", 0.0))
    duplicate_probability = float(injection.get("duplicate_detection_probability", 0.0))
    outlier_probability = float(injection.get("outlier_probability", 0.0))
    outlier_distance = float(injection.get("outlier_distance_m", 600.0))
    false_alarm_rate = float(injection.get("false_alarm_rate", 0.0))
    speed_jump = float(injection.get("speed_jump_mps", 35.0))
    rows = []
    next_det_id = 1

    period_ids = set(by_period)
    if frame_times is not None:
        period_ids.update(range(1, len(frame_times) + 1))
    for period_id in sorted(period_ids):
        truths = by_period.get(period_id, [])
        utc = (float(truths[0]["utc"]) if truths else
               100000.0 + float(frame_times[period_id - 1]))
        frame_rows = []
        for item in truths:
            if not int(item["active"]):
                continue
            if period_id in forced_misses or rng.random() < miss_probability:
                continue
            e = float(item["e"]) + rng.gauss(0.0, position_noise)
            n = float(item["n"]) + rng.gauss(0.0, position_noise)
            if period_id in outlier_periods or rng.random() < outlier_probability:
                angle = rng.uniform(-math.pi, math.pi)
                e += outlier_distance * math.cos(angle)
                n += outlier_distance * math.sin(angle)
            measured_speed = float(item["speed"]) + rng.gauss(0.0, velocity_noise)
            if period_id in speed_jump_periods:
                measured_speed += speed_jump
            det = {
                "period_id": period_id,
                "utc": utc,
                "valid": 1,
                "det_id": next_det_id,
                "truth_hint": item["target_id"],
                "e": e,
                "n": n,
                "lon": 0.0,
                "lat": 0.0,
                "speed": measured_speed,
                "direction": float(item["heading"]),
                "range": 20000.0 + 0.1 * math.hypot(e, n),
                "det_utc": utc + rng.gauss(0.0, timestamp_jitter),
            }
            next_det_id += 1
            frame_rows.append(det)
            if rng.random() < duplicate_probability:
                duplicate = dict(det)
                duplicate["det_id"] = next_det_id
                duplicate["truth_hint"] = str(item["target_id"]) + "_duplicate"
                duplicate["e"] += rng.gauss(0.0, max(0.1, position_noise * 0.25))
                duplicate["n"] += rng.gauss(0.0, max(0.1, position_noise * 0.25))
                next_det_id += 1
                frame_rows.append(duplicate)

        for _ in range(expected_false_alarm_count(false_alarm_rate, rng)):
            frame_rows.append({
                "period_id": period_id,
                "utc": utc,
                "valid": 1,
                "det_id": next_det_id,
                "truth_hint": "false_alarm",
                "e": rng.uniform(-1500.0, 1500.0),
                "n": rng.uniform(-1500.0, 1500.0),
                "lon": 0.0,
                "lat": 0.0,
                "speed": rng.uniform(0.0, 50.0),
                "direction": rng.uniform(-180.0, 180.0),
                "range": rng.uniform(18000.0, 24000.0),
                "det_utc": utc + rng.gauss(0.0, timestamp_jitter),
            })
            next_det_id += 1
        if not frame_rows:
            frame_rows.append({
                "period_id": period_id, "utc": utc, "valid": 0,
                "det_id": -1, "truth_hint": "", "e": 0.0, "n": 0.0,
                "lon": 0.0, "lat": 0.0, "speed": 0.0,
                "direction": 0.0, "range": 0.0, "det_utc": utc,
            })
        rows.extend(frame_rows)
    return rows


def tracker_command(executable: Path, detection_path: Path, output_dir: Path,
                    tracker: dict) -> list[str]:
    options = {
        "distance_mode": "--distance-mode",
        "assignment_mode": "--assignment-mode",
        "output_source": "--output-source",
        "confirm_window": "--confirm-window",
        "confirm_hits": "--confirm-hits",
        "max_missed": "--max-missed",
        "tentative_max_missed": "--tentative-max-missed",
        "gate_m": "--gate-m",
        "chi2_gate": "--chi2-gate",
        "dummy_cost": "--dummy-cost",
        "measurement_noise_m": "--measurement-noise-m",
        "process_noise_pos": "--process-noise-pos",
        "process_noise_vel": "--process-noise-vel",
        "use_speed_cost": "--use-speed-cost",
        "speed_weight": "--speed-weight",
        "use_heading_cost": "--use-heading-cost",
        "heading_weight": "--heading-weight",
        "heading_min_displacement_m": "--heading-min-displacement-m",
        "min_linearity": "--min-linearity",
    }
    cmd = [str(executable), "--input", str(detection_path),
           "--output-dir", str(output_dir)]
    for key, flag in options.items():
        if key in tracker:
            cmd.extend([flag, str(tracker[key]).lower() if isinstance(
                tracker[key], bool) else str(tracker[key])])
    return cmd


def f(row: dict, key: str, fallback: float = math.nan) -> float:
    try:
        return float(row.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


def i(row: dict, key: str, fallback: int = -1) -> int:
    try:
        return int(float(row.get(key, fallback)))
    except (TypeError, ValueError):
        return fallback


def evaluate_tracks(truth: list[dict], outputs: list[dict], events: list[dict],
                    gate_m: float, miss_periods: list[int]) -> tuple[dict, list[dict]]:
    truth_by_period: dict[int, list[dict]] = {}
    output_by_period: dict[int, list[dict]] = {}
    for row in truth:
        if int(row["active"]):
            truth_by_period.setdefault(int(row["period_id"]), []).append(row)
    for row in outputs:
        output_by_period.setdefault(i(row, "period_id"), []).append(row)

    timeseries = []
    mappings: dict[str, list[tuple[int, int | None]]] = {}
    false_output_rows = 0
    squared_position_errors = []
    squared_velocity_errors = []
    total_truth_frames = 0
    matched_truth_frames = 0
    all_track_ids = set()
    matched_track_ids = set()

    for period_id in sorted(set(truth_by_period) | set(output_by_period)):
        truths = truth_by_period.get(period_id, [])
        tracks = output_by_period.get(period_id, [])
        total_truth_frames += len(truths)
        all_track_ids.update(i(row, "track_id") for row in tracks)
        costs = []
        for item in truths:
            costs.append([
                math.hypot(f(track, "e") - float(item["e"]),
                           f(track, "n") - float(item["n"]))
                for track in tracks
            ])
        assignment = min_cost_assignment(costs)
        used_tracks = set()
        for truth_index, item in enumerate(truths):
            track_index = assignment.get(truth_index)
            distance = (costs[truth_index][track_index]
                        if track_index is not None and track_index < len(tracks)
                        else math.inf)
            matched = track_index is not None and distance <= gate_m
            track_id = i(tracks[track_index], "track_id") if matched else None
            if matched:
                used_tracks.add(track_index)
                matched_truth_frames += 1
                matched_track_ids.add(track_id)
                squared_position_errors.append(distance * distance)
                speed_error = f(tracks[track_index], "speed") - float(item["speed"])
                if math.isfinite(speed_error):
                    squared_velocity_errors.append(speed_error * speed_error)
            mappings.setdefault(str(item["target_id"]), []).append((period_id, track_id))
            timeseries.append({
                "period_id": period_id,
                "truth_target_id": item["target_id"],
                "track_id": "" if track_id is None else track_id,
                "association_status": "matched" if matched else "missed",
                "position_error_m": distance if matched else "",
                "velocity_error_mps": (
                    f(tracks[track_index], "speed") - float(item["speed"])
                    if matched else ""),
                "id_switch_event": 0,
                "fragment_event": 0,
                "confirm_event": 0,
                "delete_event": 0,
            })
        false_output_rows += max(0, len(tracks) - len(used_tracks))

    event_keys = {
        (i(row, "period_id"), i(row, "track_id"), row.get("event", ""))
        for row in events
    }
    timeseries_index = {
        (int(row["period_id"]), str(row["truth_target_id"])): row
        for row in timeseries
    }
    id_switches = 0
    fragments = 0
    confirmation_delays = []
    covered_truths = 0
    for target_id, sequence in mappings.items():
        first_period = sequence[0][0]
        first_match = next((period for period, tid in sequence if tid is not None), None)
        if first_match is not None:
            covered_truths += 1
            confirmation_delays.append(first_match - first_period)
        previous_id = None
        ever_matched = False
        in_segment = False
        segments = 0
        for period, track_id in sequence:
            if track_id is None:
                in_segment = False
                continue
            ts_row = timeseries_index[(period, target_id)]
            if previous_id is not None and track_id != previous_id:
                id_switches += 1
                ts_row["id_switch_event"] = 1
            if not in_segment:
                segments += 1
                if ever_matched:
                    ts_row["fragment_event"] = 1
                in_segment = True
            if (period, track_id, "confirm") in event_keys:
                ts_row["confirm_event"] = 1
            ever_matched = True
            previous_id = track_id
        fragments += max(0, segments - 1)

    # Reacquisition delay is measured after explicitly requested consecutive
    # miss blocks; random misses remain represented in completeness/fragmentation.
    reacquisition_delays = []
    misses = sorted(set(int(value) for value in miss_periods))
    blocks = []
    for period in misses:
        if not blocks or period > blocks[-1][-1] + 1:
            blocks.append([period])
        else:
            blocks[-1].append(period)
    for sequence in mappings.values():
        for block in blocks:
            end = block[-1]
            next_match = next((period for period, tid in sequence
                               if period > end and tid is not None), None)
            if next_match is not None:
                reacquisition_delays.append(next_match - end)

    deletion_delays = []
    last_output_period: dict[int, int] = {}
    for row in sorted(events, key=lambda value: i(value, "period_id")):
        track_id = i(row, "track_id")
        period_id = i(row, "period_id")
        event = row.get("event", "")
        if event == "output":
            last_output_period[track_id] = period_id
        elif event == "delete" and track_id in last_output_period:
            deletion_delays.append(period_id - last_output_period[track_id])
            timeseries.append({
                "period_id": period_id,
                "truth_target_id": "",
                "track_id": track_id,
                "association_status": "delete_event",
                "position_error_m": "",
                "velocity_error_mps": "",
                "id_switch_event": 0,
                "fragment_event": 0,
                "confirm_event": 0,
                "delete_event": 1,
            })

    truth_ids = set(str(row["target_id"]) for row in truth if int(row["active"]))
    output_count = len(outputs)
    rmse_pos = math.sqrt(sum(squared_position_errors) / len(squared_position_errors)) \
        if squared_position_errors else math.nan
    rmse_vel = math.sqrt(sum(squared_velocity_errors) / len(squared_velocity_errors)) \
        if squared_velocity_errors else math.nan
    metrics = {
        "track_confirmation_delay_periods": (
            sum(confirmation_delays) / len(confirmation_delays)
            if confirmation_delays else ""),
        "track_completeness": matched_truth_frames / total_truth_frames
        if total_truth_frames else 1.0,
        "track_continuity_rate": 1.0 - fragments / max(1, matched_truth_frames),
        "track_fragment_count": fragments,
        "id_switch_count": id_switches,
        "false_track_count": len(all_track_ids - matched_track_ids),
        "false_track_rate": false_output_rows / output_count if output_count else 0.0,
        "track_position_rmse_m": rmse_pos,
        "track_velocity_rmse_mps": rmse_vel,
        "reacquisition_delay_periods": (
            sum(reacquisition_delays) / len(reacquisition_delays)
            if reacquisition_delays else ""),
        "deletion_delay_periods": (
            sum(deletion_delays) / len(deletion_delays)
            if deletion_delays else ""),
        "confirmed_track_count": len(all_track_ids),
        "truth_track_coverage": covered_truths / len(truth_ids) if truth_ids else 1.0,
        "truth_frame_count": total_truth_frames,
        "matched_truth_frame_count": matched_truth_frames,
        "protocol_output_count": output_count,
    }
    return metrics, timeseries


def nearly_equal(left, right, tolerance=1.0e-8):
    a, b = f(left, "value", left) if isinstance(left, dict) else f(
        {"value": left}, "value"), f(right, "value", right) if isinstance(
            right, dict) else f({"value": right}, "value")
    return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= tolerance


def evaluate_payload_audit(rows: list[dict], expected_source: str) -> dict:
    source_mismatches = 0
    coordinate_mismatches = 0
    speed_source_mismatches = 0
    for row in rows:
        if row.get("resolved_source") != expected_source:
            source_mismatches += 1
        expected_speed_source = {
            "measurement": "track_displacement_over_elapsed_time",
            "prediction": "kalman_prior_velocity",
            "kalman_filtered": "kalman_posterior_velocity",
        }.get(expected_source, "")
        if row.get("speed_source") != expected_speed_source:
            speed_source_mismatches += 1
        if expected_source == "measurement":
            pairs = [
                ("output_utc", "measurement_utc"),
                ("output_e", "measurement_e"),
                ("output_n", "measurement_n"),
                ("output_lat", "measurement_lat"),
                ("output_lon", "measurement_lon"),
                ("output_range", "measurement_range"),
                ("output_direction", "measurement_direction"),
            ]
        elif expected_source == "prediction":
            pairs = [
                ("output_utc", "prediction_utc"),
                ("output_e", "prediction_e"),
                ("output_n", "prediction_n"),
                ("output_range", "prediction_range"),
                ("output_direction", "prediction_direction"),
            ]
            expected_speed = math.hypot(
                f(row, "prediction_ve"), f(row, "prediction_vn"))
        else:
            pairs = [
                ("output_utc", "filtered_utc"),
                ("output_e", "filtered_e"),
                ("output_n", "filtered_n"),
                ("output_range", "filtered_range"),
                ("output_direction", "filtered_direction"),
            ]
            expected_speed = math.hypot(
                f(row, "filtered_ve"), f(row, "filtered_vn"))
        if any(not nearly_equal(row.get(output_key), row.get(source_key))
               for output_key, source_key in pairs):
            coordinate_mismatches += 1
        if expected_source != "measurement" and not nearly_equal(
                row.get("output_speed"), expected_speed):
            coordinate_mismatches += 1
    return {
        "output_source": expected_source,
        "payload_audit_count": len(rows),
        "payload_source_mismatch_count": source_mismatches,
        "payload_coordinate_mismatch_count": coordinate_mismatches,
        "payload_speed_source_mismatch_count": speed_source_mismatches,
        "payload_audit_pass": int(
            bool(rows) and source_mismatches == 0 and
            coordinate_mismatches == 0 and speed_source_mismatches == 0),
    }


def run_case(matrix: dict, case: dict, root: Path, args, provenance: dict) -> dict:
    case_id = str(case["case_id"])
    case_dir = root / "cases" / case_id
    if args.force and case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = case_dir / "metrics" / "tracking_metrics.csv"
    if args.resume and metrics_path.exists():
        return read_csv(metrics_path)[0]

    scenario = merged(matrix.get("default_scenario", {}), case.get("scenario", {}))
    injection = merged(matrix.get("default_injection", {}), case.get("injection", {}))
    tracker = merged(matrix.get("default_tracker", {}), case.get("tracker", {}))
    seed = int(case.get("random_seed", matrix.get("random_seed", 20260714)))
    truth = truth_for_case({"scenario": scenario}, {})
    frame_times = period_times(
        int(scenario.get("periods", 20)), float(scenario.get("dt_s", 1.0)),
        scenario)
    detections = detections_from_truth(truth, injection, seed, frame_times)
    truth_path = case_dir / "truth" / "track_truth.csv"
    detection_path = case_dir / "input" / "detections.csv"
    write_csv(truth_path, truth, list(truth[0]) if truth else TRUTH_FIELDS)
    write_csv(detection_path, detections, list(detections[0]))
    expanded = {
        "case_id": case_id, "scenario": scenario, "injection": injection,
        "tracker": tracker, "random_seed": seed,
    }
    write_json(case_dir / "expanded_config.json", expanded)

    algorithm_dir = case_dir / "algorithm_outputs"
    algorithm_dir.mkdir(parents=True, exist_ok=True)
    executable = (REPO_ROOT / matrix.get(
        "algorithm_exe", "build/track_detection_eval")).resolve()
    cmd = tracker_command(executable, detection_path, algorithm_dir, tracker)
    started = time.time()
    result = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True,
                            timeout=args.timeout, check=False)
    elapsed = time.time() - started
    logs = case_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "stdout.log").write_text(result.stdout, encoding="utf-8")
    (logs / "stderr.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"{case_id}: TrackManager returned {result.returncode}")

    outputs = read_csv(algorithm_dir / "protocol_tracks.csv")
    events = read_csv(algorithm_dir / "track_events.csv")
    payloads = read_csv(algorithm_dir / "track_output_payloads.csv")
    metrics, timeseries = evaluate_tracks(
        truth, outputs, events, float(matrix.get("truth_match_gate_m", 150.0)),
        injection.get("miss_periods", []))
    metrics.update(evaluate_payload_audit(
        payloads, str(tracker.get("output_source", "measurement"))))
    metrics["protocol_payload_count_matches_audit"] = int(
        len(outputs) == len(payloads))
    metrics.update({
        "experiment_id": matrix["experiment_id"],
        "case_id": case_id,
        "status": "complete",
        "random_seed": seed,
        "assignment_mode": tracker.get("assignment_mode", "hungarian"),
        "distance_mode": tracker.get("distance_mode", "mahalanobis"),
        "input_hash": sha256(detection_path),
        "truth_hash": sha256(truth_path),
        "git_commit": provenance["git_commit"],
        "git_dirty": provenance["git_dirty"],
        "source_dirty": provenance["source_dirty"],
        "worktree_fingerprint": provenance["worktree_fingerprint"],
        "elapsed_sec": elapsed,
    })
    ordered = [
        "experiment_id", "case_id", "status", "random_seed",
        "assignment_mode", "distance_mode", "input_hash", "truth_hash",
        "git_commit", "git_dirty", "source_dirty", "worktree_fingerprint",
        "track_confirmation_delay_periods", "track_completeness",
        "track_continuity_rate", "track_fragment_count", "id_switch_count",
        "false_track_count", "false_track_rate", "track_position_rmse_m",
        "track_velocity_rmse_mps", "reacquisition_delay_periods",
        "deletion_delay_periods", "confirmed_track_count", "truth_track_coverage",
        "truth_frame_count", "matched_truth_frame_count", "protocol_output_count",
        "output_source", "payload_audit_count",
        "payload_source_mismatch_count", "payload_coordinate_mismatch_count",
        "payload_speed_source_mismatch_count", "payload_audit_pass",
        "protocol_payload_count_matches_audit",
        "elapsed_sec",
    ]
    write_csv(metrics_path, [metrics], ordered)
    write_csv(case_dir / "metrics" / "track_truth_timeseries.csv", timeseries,
              list(timeseries[0]) if timeseries else ["period_id"])
    write_json(case_dir / "run_manifest.json", {
        "case_id": case_id,
        "normal_exit": True,
        "exit_code": result.returncode,
        "command": cmd,
        "elapsed_sec": elapsed,
        "config_hash": hashlib.sha256(json.dumps(
            expanded, sort_keys=True).encode()).hexdigest(),
        "data_hash": metrics["input_hash"],
        "truth_hash": metrics["truth_hash"],
        "git_commit": metrics["git_commit"],
        "git_dirty": metrics["git_dirty"],
        "source_dirty": metrics["source_dirty"],
        "worktree_fingerprint": metrics["worktree_fingerprint"],
        "algorithm_exe": str(executable),
    })
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--dump-failed-case", action="store_true")
    args = parser.parse_args()
    if args.force and args.resume:
        parser.error("--force and --resume are mutually exclusive")
    matrix_path = args.matrix.resolve()
    matrix = read_json(matrix_path)
    if not matrix.get("experiment_id") or not matrix.get("cases"):
        parser.error("matrix requires experiment_id and non-empty cases")
    configured_root = matrix.get(
        "output_root", f"outputs/eval/{matrix['experiment_id']}")
    root = (REPO_ROOT / configured_root).resolve()
    selected = set(value for group in args.case for value in group.split(",") if value)
    cases = [case for case in matrix["cases"]
             if not selected or case["case_id"] in selected]
    if selected - {case["case_id"] for case in cases}:
        parser.error(f"unknown cases: {sorted(selected - {case['case_id'] for case in cases})}")

    if args.dry_run:
        print(json.dumps({
            "workflow": "tracking",
            "experiment_id": matrix["experiment_id"],
            "output_root": str(root),
            "algorithm_exe": matrix.get("algorithm_exe", "build/track_detection_eval"),
            "cases": [case["case_id"] for case in cases],
        }, ensure_ascii=False, indent=2))
        return 0

    provenance = git_provenance()
    root.mkdir(parents=True, exist_ok=True)
    (root / "matrix").mkdir(exist_ok=True)
    shutil.copy2(matrix_path, root / "matrix" / matrix_path.name)

    rows = []
    failures = []
    for case in cases:
        try:
            row = run_case(matrix, case, root, args, provenance)
            rows.append(row)
            print(f"[P9][{case['case_id']}] complete", flush=True)
        except Exception as exc:
            failures.append({"case_id": case["case_id"], "error": str(exc)})
            if args.dump_failed_case:
                failure_dir = root / "cases" / case["case_id"] / "failed"
                failure_dir.mkdir(parents=True, exist_ok=True)
                write_json(failure_dir / "failure.json", failures[-1])
            print(f"[P9][{case['case_id']}][FAIL] {exc}", file=sys.stderr, flush=True)
            if args.stop_on_failure:
                break
    if rows:
        write_csv(root / "tracking_metrics.csv", rows, list(rows[0]))
    write_json(root / "experiment_summary.json", {
        "experiment_id": matrix["experiment_id"],
        "selected_case_count": len(cases),
        "complete_case_count": len(rows),
        "failed_case_count": len(failures),
        "matrix_hash": sha256(matrix_path),
        **provenance,
        "failures": failures,
    })
    if args.report:
        lines = [
            f"# {matrix['experiment_id']} 航迹鲁棒性摘要", "",
            "| case | 关联/距离 | 完整率 | ID switch | 断轨 | 虚假航迹率 | 位置 RMSE (m) |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for row in rows:
            lines.append(
                f"| {row['case_id']} | {row['assignment_mode']}/{row['distance_mode']} | "
                f"{float(row['track_completeness']):.3f} | {row['id_switch_count']} | "
                f"{row['track_fragment_count']} | {float(row['false_track_rate']):.3f} | "
                f"{float(row['track_position_rmse_m']):.3f} |")
        (root / "reports").mkdir(exist_ok=True)
        (root / "reports" / "tracking_report.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(read_json(root / "experiment_summary.json"), ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
