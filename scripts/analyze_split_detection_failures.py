#!/usr/bin/env python3
"""Explain misses/false alarms in the fixed five-period split-CFAR result."""

from __future__ import annotations

import argparse
import csv
import math
import re
from pathlib import Path


def read_csv(path: Path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def circular_distance(a: int, b: int, size: int = 128) -> int:
    delta = abs(a - b) % size
    return min(delta, size - delta)


def fmt_float(value, digits=3):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "NA"
    return f"{number:.{digits}f}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--truth", "--truth-dir", dest="truth", required=True, type=Path)
    args = parser.parse_args()
    reports = args.run_root / "reports"
    details = read_csv(reports / "all_50_target_localization_details.csv")
    truth = [row for row in read_csv(args.truth)
             if row.get("visible", "1") not in ("0", "false", "False")]
    detections = []
    for path in sorted((args.run_root / "algorithm_result").glob("period_*/detection_results.csv")):
        detections.extend(read_csv(path))
    matched_detections = {
        (row["period_id"], row["matched_detection_id"])
        for row in details if row["localization_status"] == "MATCHED"
    }
    support = {}
    pattern = re.compile(
        r"\[CSI\]\[CANCELLATION\] period=(\d+) beam=(\d+) support_rows=\[(\d+),(\d+)\]")
    for path in sorted(args.run_root.glob("period_*.log")):
        for period, beam, start, end in pattern.findall(path.read_text(errors="ignore")):
            support[(period, beam)] = (int(start), int(end))

    def compare(detection, truth_row):
        return {
            "beam_difference": abs(int(detection["beam_id"]) - int(truth_row["beam_id"])),
            "range_difference_m": abs(float(detection["range_m"]) - float(truth_row["range_m"])),
            "position_difference_m": math.hypot(
                float(detection["new_e"]) - float(truth_row["e_mid"]),
                float(detection["new_n"]) - float(truth_row["n_mid"])),
        }

    misses = []
    for detail in details:
        if detail["localization_status"] == "MATCHED":
            continue
        truth_row = next(row for row in truth if row["period_id"] == detail["period_id"]
                         and row["target_id"] == detail["target_id"])
        candidates = [row for row in detections if row["period_id"] == truth_row["period_id"]]
        nearest = min(candidates, key=lambda row: (
            compare(row, truth_row)["beam_difference"],
            compare(row, truth_row)["range_difference_m"],
            compare(row, truth_row)["position_difference_m"]))
        delta = compare(nearest, truth_row)
        same_beam_candidates = [row for row in candidates
                                if abs(int(row["beam_id"]) - int(truth_row["beam_id"])) <= 1]
        close = [row for row in same_beam_candidates
                 if abs(float(row["range_m"]) - float(truth_row["range_m"])) <= 100.0]
        if close:
            cause = "近真值候选存在，但二维定位门未通过"
        elif any(abs(float(row["range_m"]) - float(truth_row["range_m"])) <= 1000.0
                 for row in same_beam_candidates):
            cause = "同波束有候选，但距离偏差超过 100 m 匹配门"
        else:
            cause = "CFAR/聚类未在真值波束与距离附近形成候选"
        bounds = support.get((truth_row["period_id"], truth_row["beam_id"]))
        boundary_distance = (min(circular_distance(int(truth_row["row_truth"]), bounds[0]),
                                 circular_distance(int(truth_row["row_truth"]), bounds[1]))
                             if bounds else -1)
        misses.append({
            "period_id": truth_row["period_id"], "target_id": truth_row["target_id"],
            "truth_beam_id": truth_row["beam_id"], "truth_row": truth_row["row_truth"],
            "measured_roi_to_background_db": detail["measured_csi_roi_to_background_db"],
            "nearest_detection_id": nearest["det_id"], "nearest_detection_beam": nearest["beam_id"],
            **delta, "distance_to_csi_boundary_rows": boundary_distance, "diagnosis": cause,
        })

    false_alarms = []
    for detection in detections:
        if (detection["period_id"], detection["det_id"]) in matched_detections:
            continue
        candidates = [row for row in truth if row["period_id"] == detection["period_id"]]
        nearest = min(candidates, key=lambda row: (
            compare(detection, row)["beam_difference"],
            compare(detection, row)["range_difference_m"],
            compare(detection, row)["position_difference_m"]))
        delta = compare(detection, nearest)
        if delta["beam_difference"] <= 1 and delta["range_difference_m"] <= 1000.0:
            cause = "真值附近偏移候选，但距离偏差超过 100 m 匹配门"
        else:
            cause = "与任一真值不近的残余杂波/噪声聚类"
        bounds = support.get((detection["period_id"], detection["beam_id"]))
        boundary_distance = (min(circular_distance(int(detection["row"]), bounds[0]),
                                 circular_distance(int(detection["row"]), bounds[1]))
                             if bounds else -1)
        false_alarms.append({
            "period_id": detection["period_id"], "detection_id": detection["det_id"],
            "beam_id": detection["beam_id"], "row": detection["row"],
            "range_m": detection["range_m"], "power": detection["power"],
            "nearest_target_id": nearest["target_id"], **delta,
            "distance_to_csi_boundary_rows": boundary_distance, "diagnosis": cause,
        })

    for name, rows in (("missed_target_diagnosis.csv", misses),
                       ("false_alarm_diagnosis.csv", false_alarms)):
        with (reports / name).open("w", encoding="utf-8", newline="") as stream:
            fieldnames = list(rows[0]) if rows else (
                ["period_id", "target_id", "diagnosis"] if name.startswith("miss")
                else ["period_id", "detection_id", "diagnosis"])
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader(); writer.writerows(rows)

    lines = [
        "# 分路 CFAR 漏检与假警逐项诊断", "",
        "匹配门与正式评价一致：周期相同、波束差不超过 1、距离差不超过 100 m、"
        "二维位置差不超过 1000 m。边界距离按 128 行环形多普勒轴计算。", "",
        f"## {len(misses)} 个漏检", "",
        "| 周期 | 目标 | 真值波束/行 | ROI/背景 dB | 最近检测波束 | 波束差 | 距离差 m | 位置差 m | 距边界行数 | 诊断 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in misses:
        lines.append(f"| {int(row['period_id']) + 1} | {row['target_id']} | {row['truth_beam_id']}/{row['truth_row']} | "
                     f"{fmt_float(row['measured_roi_to_background_db'])} | {row['nearest_detection_beam'] or 'NA'} | "
                     f"{fmt_float(row['beam_difference'], 0)} | {fmt_float(row['range_difference_m'], 1)} | {fmt_float(row['position_difference_m'], 1)} | "
                     f"{row['distance_to_csi_boundary_rows']} | {row['diagnosis']} |")
    lines.extend(["", f"## {len(false_alarms)} 个假警", "",
                  "| 周期 | 检测 | 波束/行 | 功率 | 最近目标 | 波束差 | 距离差 m | 位置差 m | 距边界行数 | 诊断 |",
                  "|---:|---:|---:|---:|---|---:|---:|---:|---:|---|"])
    for row in false_alarms:
        lines.append(f"| {int(row['period_id']) + 1} | {row['detection_id']} | {row['beam_id']}/{row['row']} | "
                     f"{fmt_float(row['power'], 3)} | {row['nearest_target_id']} | {fmt_float(row['beam_difference'], 0)} | "
                     f"{fmt_float(row['range_difference_m'], 1)} | {fmt_float(row['position_difference_m'], 1)} | "
                     f"{row['distance_to_csi_boundary_rows']} | {row['diagnosis']} |")
    min_miss_boundary = min((x['distance_to_csi_boundary_rows'] for x in misses), default="NA")
    min_fa_boundary = min((x['distance_to_csi_boundary_rows'] for x in false_alarms), default="NA")
    lines.extend(["", "## 结论", "",
                  f"- 漏检的最小边界距离为 {min_miss_boundary} 行；"
                  f"假警的最小边界距离为 {min_fa_boundary} 行。"
                  "边界距离仅作证据记录，不把边界保护误判为根因。",
                  "- 漏检按真值波束/距离邻域区分为 CUT 未命中、命中但小簇未成形、以及同波束偏移候选；"
                  "假警按真值邻近偏移和远离真值的残余簇区分。"])
    (reports / "split_detection_failure_analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(reports / "split_detection_failure_analysis.md")


if __name__ == "__main__":
    main()
