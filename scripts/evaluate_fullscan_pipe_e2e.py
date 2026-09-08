#!/usr/bin/env python3
"""Evaluate a multi-scan GMTI_pipe production run against simulator truth.

The script consumes per-result detection snapshots written by GMTI_pipe,
TrackManager's existing ``track_debug``/payload CSVs, and the FIFO/SHM logs.
It does not implement an alternative detector or tracker.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import struct
from collections import defaultdict
from pathlib import Path
from statistics import median

from gmti_eval_match import one_to_one_match


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        if not fields:
            stream.write("")
            return
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def number(row: dict, key: str, default: float = math.nan) -> float:
    try:
        return float(row.get(key, default))
    except (TypeError, ValueError):
        return default


def integer(row: dict, key: str, default: int = -1) -> int:
    try:
        return int(float(row.get(key, default)))
    except (TypeError, ValueError):
        return default


def percentile(values: list[float], fraction: float) -> float:
    finite = sorted(value for value in values if math.isfinite(value))
    if not finite:
        return math.nan
    position = fraction * (len(finite) - 1)
    lo = int(math.floor(position))
    hi = int(math.ceil(position))
    if lo == hi:
        return finite[lo]
    weight = position - lo
    return finite[lo] * (1.0 - weight) + finite[hi] * weight


def png_dimensions(path: Path) -> tuple[int, int]:
    try:
        with path.open("rb") as stream:
            header = stream.read(24)
        if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
            return 0, 0
        return struct.unpack(">II", header[16:24])
    except OSError:
        return 0, 0


def visible_truth(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row for row in rows
        if str(row.get("visible", "1")).strip().lower()
        not in ("0", "false", "no")
    ]


def result_number(path: Path) -> int:
    match = re.search(r"GMTI(\d+)", path.name)
    return int(match.group(1)) if match else -1


def result_id_number(value: object) -> int:
    match = re.search(r"(\d+)", str(value))
    return int(match.group(1)) if match else -1


def parse_log_metrics(log_dir: Path, run_dir: Path) -> dict:
    server_path = next((path for path in (
        log_dir / "gmti_pipe_core.log",
        log_dir / "gmti_file_mode.log",
        run_dir / "gmti_file_mode.log",
    ) if path.is_file()), log_dir / "gmti_pipe_core.log")
    server = server_path.read_text(
        encoding="utf-8", errors="replace") if server_path.is_file() else ""
    client = (log_dir / "pipe_client.log").read_text(
        encoding="utf-8", errors="replace") if (log_dir / "pipe_client.log").is_file() else ""
    cycle_lines = re.findall(r"\[SHM\]\[METRICS\][^\n]*", server)
    last_cycle = cycle_lines[-1] if cycle_lines else ""
    file_cycles = re.findall(r"\[FILE\]\[CYCLE\][^\n]*success=true", server)

    def field(name: str, default: int = 0) -> int:
        match = re.search(rf"\b{name}=(\d+)", last_cycle)
        return int(match.group(1)) if match else default

    protocol_rows = []
    for match in re.finditer(
            r"result_index=(\d+).*?targetNum=(\d+).*?image=(\d+)x(\d+).*?availFlag=0x([0-9a-fA-F]+)",
            client):
        protocol_rows.append({
            "result_index": int(match.group(1)),
            "target_count": int(match.group(2)),
            "image_rows": int(match.group(3)),
            "image_cols": int(match.group(4)),
            "image_available": int(int(match.group(5), 16) != 0),
        })
    startup = {}
    for key in ("scan_beam_count", "processing_beam_count", "prts_per_scan",
                "scan_bytes", "double_buffer_bytes"):
        match = re.search(rf"\[STARTUP\] {key}: (\d+)", server)
        startup[key] = int(match.group(1)) if match else None
    return {
        "completed_cycles": field("completed_cycles", len(file_cycles)),
        "dropped_incomplete": field("dropped_incomplete"),
        "dropped_backpressure": field("dropped_backpressure"),
        "prt_gaps": field("gaps"),
        "duplicate_prts": field("duplicates"),
        "ring_overruns": field("ring_overrun"),
        "protocol_rows": protocol_rows,
        "startup": startup,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-dir", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--case-label", required=True)
    parser.add_argument("--expected-cycles", type=int, default=3)
    parser.add_argument("--input-mode", choices=("auto", "file", "shm"),
                        default="auto")
    parser.add_argument("--match-config", type=Path,
                        default=Path("configs/eval/localization_match_config.json"))
    parser.add_argument("--min-detection-recall", type=float, default=0.90)
    parser.add_argument("--max-false-alarms-per-scan", type=float, default=10.0)
    parser.add_argument("--max-position-p95-m", type=float, default=50.0)
    parser.add_argument("--min-track-recall-after-confirm", type=float, default=0.80)
    parser.add_argument("--max-id-switches", type=int, default=1)
    args = parser.parse_args()

    result_dir = args.run_dir / "result"
    if not result_dir.is_dir() and (args.run_dir / "results").is_dir():
        result_dir = args.run_dir / "results"
    log_dir = args.run_dir / "logs"
    input_mode = args.input_mode
    if input_mode == "auto":
        input_mode = "shm" if (log_dir / "pipe_client.log").is_file() else "file"
    truth_path = args.scenario_dir / "truth" / "truth_targets_by_beam.csv"
    truths = visible_truth(read_csv(truth_path))
    periods = sorted({integer(row, "period_id", 0) for row in truths})
    match_cfg = json.loads(args.match_config.read_text(encoding="utf-8"))

    snapshot_paths = sorted(
        result_dir.glob("detection_results_GMTI*.csv"), key=result_number)
    detection_rows: list[dict] = []
    cycle_rows: list[dict] = []
    match_rows: list[dict] = []
    result_to_period: dict[int, int] = {}
    raw_detection_ranges: dict[tuple[int, int], str] = {}
    all_position_errors: list[float] = []
    total_truth = total_det = total_match = total_false = 0
    for cycle_index, path in enumerate(snapshot_paths):
        detections = read_csv(path)
        observed_periods = sorted({integer(row, "period_id", -1)
                                   for row in detections
                                   if integer(row, "period_id", -1) >= 0})
        expected_period = periods[cycle_index] if cycle_index < len(periods) else cycle_index
        period_id = observed_periods[0] if len(observed_periods) == 1 else expected_period
        result_id = result_number(path)
        result_to_period[result_id] = period_id
        for det_index, detection in enumerate(detections):
            raw_range = detection.get("range_m", "")
            try:
                valid_range = math.isfinite(float(raw_range))
            except (TypeError, ValueError):
                valid_range = False
            if not valid_range:
                raise SystemExit(
                    "原始检测快照的range_m不是有限数值："
                    f"result_id={result_id}, det_index={det_index}, value={raw_range!r}"
                )
            raw_detection_ranges[(result_id, det_index)] = raw_range
        cycle_truth = [row for row in truths if integer(row, "period_id", 0) == period_id]
        matches, used_det, used_truth = one_to_one_match(
            detections, cycle_truth, match_cfg, "det_id",
            use_beam=True, use_range=True, use_velocity=False)
        position_errors = [float(row["position_error_m"]) for row in matches
                           if math.isfinite(float(row["position_error_m"]))]
        all_position_errors.extend(position_errors)
        for match in matches:
            truth = cycle_truth[match["truth_index"]]
            det = detections[match["item_index"]]
            match_rows.append({
                "case_label": args.case_label,
                "result_id": result_id,
                "period_id": period_id,
                "target_id": truth.get("target_id", ""),
                "det_id": det.get("det_id", ""),
                "beam_id": truth.get("beam_id", ""),
                "range_bin_truth": truth.get("range_bin", ""),
                "range_bin_detection": det.get("range_bin", ""),
                "position_error_m": match["position_error_m"],
                "range_error_m": match["range_error_m"],
                "velocity_error_mps": match["velocity_error_mps"],
            })
        truth_count = len(cycle_truth)
        det_count = len(detections)
        matched_count = len(matches)
        false_count = det_count - matched_count
        cycle_rows.append({
            "case_label": args.case_label,
            "result_id": result_id,
            "period_id": period_id,
            "truth_count": truth_count,
            "detection_count": det_count,
            "matched_count": matched_count,
            "missed_count": truth_count - matched_count,
            "false_alarm_count": false_count,
            "recall": matched_count / truth_count if truth_count else 1.0,
            "precision": matched_count / det_count if det_count else (1.0 if not truth_count else 0.0),
            "position_median_m": median(position_errors) if position_errors else math.nan,
            "position_p95_m": percentile(position_errors, 0.95),
            "snapshot_path": str(path),
        })
        total_truth += truth_count
        total_det += det_count
        total_match += matched_count
        total_false += false_count
        for index, row in enumerate(detections):
            copied = dict(row)
            copied["snapshot_result_id"] = result_id
            copied["matched"] = int(index in used_det)
            detection_rows.append(copied)

    debug_roots = (result_dir / "track_debug", args.run_dir / "track_debug")
    debug_runs = sorted(
        path for root in debug_roots if root.is_dir()
        for path in root.rglob("track_frames.csv"))
    if not debug_runs:
        debug_runs = sorted(
            path / "track_frames.csv"
            for path in (result_dir / "track_debug_runs").glob("*")
            if path.is_dir() and (path / "track_frames.csv").is_file())
    debug_dir = debug_runs[-1].parent if debug_runs else result_dir / "track_debug"
    payloads = read_csv(result_dir / "track_output_payloads.csv")
    if not payloads:
        payloads = read_csv(debug_dir / "track_output_payloads.csv")
    debug_frames = read_csv(debug_dir / "track_frames.csv")
    debug_detections = read_csv(debug_dir / "track_detections.csv")
    debug_states = read_csv(debug_dir / "track_states.csv")
    debug_detection_by_key = {
        (integer(row, "result_id"), integer(row, "det_index")): row
        for row in debug_detections
    }
    debug_output_state_by_key = {
        (integer(row, "result_id"), integer(row, "track_id")): row
        for row in debug_states if integer(row, "is_output", 0) == 1
    }
    track_debug_payload_rows: list[dict] = []
    for payload in payloads:
        result_id = result_id_number(payload.get("result_id", ""))
        track_id = integer(payload, "track_id")
        det_index = integer(payload, "matched_det_index")
        debug_detection = debug_detection_by_key.get((result_id, det_index))
        debug_state = debug_output_state_by_key.get((result_id, track_id))
        measurement_position_error = math.nan
        output_measurement_position_error = math.nan
        measurement_time_error = math.nan
        measurement_range_error = math.nan
        detection_track_match = False
        if debug_detection is not None:
            measurement_position_error = math.hypot(
                number(payload, "measurement_e") - number(debug_detection, "e"),
                number(payload, "measurement_n") - number(debug_detection, "n"))
            output_measurement_position_error = math.hypot(
                number(payload, "output_e") - number(debug_detection, "e"),
                number(payload, "output_n") - number(debug_detection, "n"))
            measurement_time_error = abs(
                number(payload, "measurement_utc") - number(debug_detection, "utc"))
            measurement_range_error = abs(
                number(payload, "measurement_range") - number(debug_detection, "range"))
            detection_track_match = (
                integer(debug_detection, "matched", 0) == 1 and
                integer(debug_detection, "matched_track_id") == track_id)
        state_output_match = bool(debug_state) and (
            str(debug_state.get("state", "")) == "Confirmed" and
            integer(debug_state, "matched_this_frame", 0) == 1 and
            integer(debug_state, "matched_det_index") == det_index)
        source_is_measurement = (
            payload.get("position_source") == "matched_detection" and
            payload.get("time_source") == "matched_detection")
        provenance_pass = (
            debug_detection is not None and state_output_match and
            detection_track_match and source_is_measurement and
            math.isfinite(measurement_position_error) and
            measurement_position_error <= 1.0e-3 and
            math.isfinite(output_measurement_position_error) and
            output_measurement_position_error <= 1.0e-3 and
            math.isfinite(measurement_time_error) and
            measurement_time_error <= 1.0e-6 and
            math.isfinite(measurement_range_error) and
            measurement_range_error <= 1.0e-6)
        track_debug_payload_rows.append({
            "result_id": result_id,
            "track_id": track_id,
            "matched_det_index": det_index,
            "debug_detection_found": int(debug_detection is not None),
            "debug_confirmed_output_state_found": int(state_output_match),
            "debug_detection_track_match": int(detection_track_match),
            "position_source": payload.get("position_source", ""),
            "time_source": payload.get("time_source", ""),
            "speed_source": payload.get("speed_source", ""),
            "measurement_position_error_m": measurement_position_error,
            "output_vs_detection_position_error_m": output_measurement_position_error,
            "measurement_time_error_s": measurement_time_error,
            "measurement_range_error_m": measurement_range_error,
            "provenance_pass": int(provenance_pass),
        })
    track_match_rows: list[dict] = []
    truth_track_ids: dict[str, list[int]] = defaultdict(list)
    warm_periods = periods[1:] if len(periods) > 1 else periods
    track_truth_total = track_match_total = 0
    track_speed_errors: list[float] = []
    for period_id in warm_periods:
        cycle_truth = [row for row in truths if integer(row, "period_id", 0) == period_id]
        cycle_payloads = []
        for row in payloads:
            rid = result_id_number(row.get("result_id", ""))
            if result_to_period.get(rid) != period_id:
                continue
            item = dict(row)
            item["e"] = row.get("output_e", "")
            item["n"] = row.get("output_n", "")
            item["utc"] = row.get("output_utc", "")
            det_index = integer(row, "matched_det_index")
            raw_range = raw_detection_ranges.get((rid, det_index))
            if raw_range is None:
                raise SystemExit(
                    "协议输出关联检测不在原始检测快照中："
                    f"result_id={rid}, det_index={det_index}"
                )
            item["range_m"] = raw_range
            item["track_range_m"] = row.get("output_range", "")
            item["range_source"] = "associated_raw_detection.range_m"
            item["speed"] = row.get("output_speed", "")
            item["period_id"] = period_id
            cycle_payloads.append(item)
        matches, _, _ = one_to_one_match(
            cycle_payloads, cycle_truth, match_cfg, "track_id",
            # TrackManager output_range is horizontal distance, while Stage2
            # truth range_m is slant range. Use the same raw range_m from the
            # associated detection snapshot so the 100 m gate is comparable
            # with raw-detection evaluation.
            use_beam=False, use_range=True, use_velocity=False)
        track_truth_total += len(cycle_truth)
        track_match_total += len(matches)
        for match in matches:
            truth = cycle_truth[match["truth_index"]]
            payload = cycle_payloads[match["item_index"]]
            track_id = integer(payload, "track_id", -1)
            target_id = truth.get("target_id", "")
            truth_track_ids[target_id].append(track_id)
            truth_speed = math.hypot(number(truth, "ve_mps"), number(truth, "vn_mps"))
            output_speed = number(payload, "output_speed")
            speed_error = abs(output_speed - truth_speed) if (
                math.isfinite(output_speed) and math.isfinite(truth_speed)) else math.nan
            if math.isfinite(speed_error):
                track_speed_errors.append(speed_error)
            track_match_rows.append({
                "case_label": args.case_label,
                "period_id": period_id,
                "target_id": target_id,
                "track_id": track_id,
                "position_error_m": match["position_error_m"],
                "range_error_m": match["range_error_m"],
                "speed_truth_mps": truth_speed,
                "speed_output_mps": output_speed,
                "speed_error_mps": speed_error,
                "configured_source": payload.get("configured_source", ""),
                "position_source": payload.get("position_source", ""),
                "speed_source": payload.get("speed_source", ""),
            })
    id_switch_count = sum(
        sum(left != right for left, right in zip(ids, ids[1:]))
        for ids in truth_track_ids.values())

    png_rows = []
    for path in sorted(result_dir.glob("GMTI*.png")):
        if "_" in path.stem:
            continue
        width, height = png_dimensions(path)
        png_rows.append({
            "path": str(path), "bytes": path.stat().st_size,
            "width": width, "height": height,
            "valid": int(path.stat().st_size > 0 and width > 0 and height > 0),
        })
    logs = parse_log_metrics(log_dir, args.run_dir)
    detection_recall = total_match / total_truth if total_truth else 0.0
    false_per_scan = total_false / max(1, len(snapshot_paths))
    track_recall = track_match_total / track_truth_total if track_truth_total else 0.0
    position_p95 = percentile(all_position_errors, 0.95)
    protocol_rows = logs["protocol_rows"]
    debug_frame_result_ids = {
        integer(row, "result_id") for row in debug_frames
        if integer(row, "result_id") >= 0
    }
    debug_provenance_pass = bool(track_debug_payload_rows) and all(
        integer(row, "provenance_pass", 0) == 1
        for row in track_debug_payload_rows)
    checks = {
        "complete_scan_layout": (
            logs["startup"].get("scan_beam_count") == 61 and
            logs["startup"].get("prts_per_scan") == 61 * 130),
        "all_cycles_completed": logs["completed_cycles"] == args.expected_cycles,
        "no_stream_drop_or_gap": all(logs[key] == 0 for key in (
            "dropped_incomplete", "dropped_backpressure", "prt_gaps",
            "duplicate_prts", "ring_overruns")),
        "detection_snapshot_count": len(snapshot_paths) == args.expected_cycles,
        "continuous_image_count": len(png_rows) == args.expected_cycles,
        "all_images_valid": len(png_rows) == args.expected_cycles and
                            all(row["valid"] for row in png_rows),
        "track_debug_frame_count": len(debug_frame_result_ids) == args.expected_cycles,
        "track_debug_output_provenance": debug_provenance_pass,
        "detection_recall": detection_recall >= args.min_detection_recall,
        "false_alarm_guardrail": false_per_scan <= args.max_false_alarms_per_scan,
        "localization_p95": math.isfinite(position_p95) and
                            position_p95 <= args.max_position_p95_m,
        "track_recall_after_confirmation": track_recall >= args.min_track_recall_after_confirm,
        "track_id_continuity": id_switch_count <= args.max_id_switches,
        "track_output_measurement_position": bool(track_match_rows) and all(
            row["position_source"] == "matched_detection" for row in track_match_rows),
        "track_speed_from_multi_period_displacement": bool(track_match_rows) and all(
            row["speed_source"] == "track_displacement_over_elapsed_time"
            for row in track_match_rows),
    }
    not_applicable_checks: list[str] = []
    if input_mode == "shm":
        checks["protocol_result_count"] = len(protocol_rows) == args.expected_cycles
        checks["protocol_images_available"] = (
            len(protocol_rows) == args.expected_cycles and
            all(row["image_available"] for row in protocol_rows))
    else:
        not_applicable_checks.extend((
            "protocol_result_count", "protocol_images_available"))
    overall = "passed" if all(checks.values()) else "failed"
    summary = {
        "case_label": args.case_label,
        "input_mode": input_mode,
        "status": overall,
        "expected_cycles": args.expected_cycles,
        "truth_count": total_truth,
        "detection_count": total_det,
        "matched_count": total_match,
        "missed_count": total_truth - total_match,
        "false_alarm_count": total_false,
        "detection_recall": detection_recall,
        "detection_precision": total_match / total_det if total_det else 0.0,
        "false_alarms_per_scan": false_per_scan,
        "position_median_m": median(all_position_errors) if all_position_errors else math.nan,
        "position_p95_m": position_p95,
        "track_recall_after_confirmation": track_recall,
        "track_range_gate_enabled": True,
        "track_range_gate_m": float(match_cfg["max_range_error_m"]),
        "track_range_source": "associated_raw_detection.range_m",
        "track_id_switch_count": id_switch_count,
        "track_speed_error_median_mps": median(track_speed_errors) if track_speed_errors else math.nan,
        "track_speed_error_p95_mps": percentile(track_speed_errors, 0.95),
        "shm": {key: value for key, value in logs.items() if key != "protocol_rows"},
        "protocol_rows": protocol_rows,
        "track_debug": {
            "frame_rows": len(debug_frames),
            "unique_frame_result_ids": len(debug_frame_result_ids),
            "detection_rows": len(debug_detections),
            "state_rows": len(debug_states),
            "output_payload_rows": len(payloads),
            "output_provenance_pass": debug_provenance_pass,
        },
        "checks": checks,
        "not_applicable_checks": not_applicable_checks,
        "acceptance_thresholds": {
            "min_detection_recall": args.min_detection_recall,
            "max_false_alarms_per_scan": args.max_false_alarms_per_scan,
            "max_position_p95_m": args.max_position_p95_m,
            "min_track_recall_after_confirm": args.min_track_recall_after_confirm,
            "max_id_switches": args.max_id_switches,
        },
    }

    args.report_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.report_dir / "detection_cycle_metrics.csv", cycle_rows)
    write_csv(args.report_dir / "detection_truth_matches.csv", match_rows)
    write_csv(args.report_dir / "detection_rows_audit.csv", detection_rows)
    write_csv(args.report_dir / "track_truth_matches.csv", track_match_rows)
    write_csv(args.report_dir / "track_debug_payload_audit.csv",
              track_debug_payload_rows)
    write_csv(args.report_dir / "continuous_image_audit.csv", png_rows)
    write_csv(args.report_dir / "protocol_result_audit.csv", protocol_rows)
    (args.report_dir / "fullflow_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=True) + "\n",
        encoding="utf-8")

    check_lines = [f"- {'PASS' if passed else 'FAIL'}: `{name}`"
                   for name, passed in checks.items()]
    check_lines.extend(f"- N/A: `{name}`" for name in not_applicable_checks)
    report = f"""# {args.case_label} GMTI_pipe 全流程验收报告

## 结论

当前自动验收状态：**{overall}**。该结论来自生产 `GMTI_pipe_core`、共享内存完整扫描双缓冲、原始检测快照、现有 `TrackManager track_debug`、PIPE 返回包和仿真 truth，不是独立简化算法。

## 核心指标

- 完整扫描：{args.expected_cycles} 周期 × 61 波位 × 130 PRT。
- truth / 命中 / 漏检 / 虚警：{total_truth} / {total_match} / {total_truth - total_match} / {total_false}。
- 检测率：{detection_recall:.6f}；精确率：{summary['detection_precision']:.6f}；平均每扫描虚警：{false_per_scan:.3f}。
- 定位误差中位数 / P95：{summary['position_median_m']:.3f} m / {position_p95:.3f} m。
- 确认窗口后航迹覆盖率：{track_recall:.6f}；ID switch：{id_switch_count}。
- 输入模式：{input_mode}；连续 DBS 图像：{len(png_rows)}/{args.expected_cycles} 张有效；PIPE 回包：{len(protocol_rows)}/{args.expected_cycles if input_mode == 'shm' else 'N/A'}。

## 逐项验收

{chr(10).join(check_lines)}

## 产物与审查

- `detection_cycle_metrics.csv`：每个完整扫描的检测/漏检/虚警/定位指标。
- `detection_truth_matches.csv`：一对一 truth-detection 匹配。
- `track_truth_matches.csv`：确认航迹当周期输出与 truth 匹配，包括位置源和速度源审计。
- `track_debug_payload_audit.csv`：逐条交叉核对 `track_debug` 的确认状态、关联 detection 与 PIPE 航迹载荷，证明返回坐标/距离/时间确实来自本周期关联检测。
- `continuous_image_audit.csv`：连续 `GMTIxx.png` 尺寸与完整性。
- `protocol_result_audit.csv`：PIPE 目标数、图像尺寸和 availFlag。
- `fullflow_summary.json`：机器可读总结和验收门限。
- `track_visualization/`：由现有 `scripts/visualize_track_manager.py` 基于 `track_debug` 生成。

## 口径说明

虚警使用实际未匹配 detection 计数，未为了做到零虚警而删除检测或开启强度保护阈值。航迹协议只统计已确认且当周期命中的输出；位置默认取当周期关联检测，最终速度取多周期位移/实际时间。
航迹 truth 匹配的距离门限与原始检测统一使用同一关联检测的 `range_m`，并启用
`max_range_error_m`（当前配置为 100 m）；TrackManager 的水平 `output_range` 仅作为审计字段。
"""
    (args.report_dir / "fullflow_report.md").write_text(report, encoding="utf-8")
    print(json.dumps({
        "status": overall,
        "summary": str(args.report_dir / "fullflow_summary.json"),
        "report": str(args.report_dir / "fullflow_report.md"),
    }, ensure_ascii=False))
    return 0 if overall == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
