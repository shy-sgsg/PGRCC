#!/usr/bin/env python3
"""Compare file and shmdemo runs at truth/result semantics, not timing."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def matched_truth_keys(path: Path) -> set[tuple[int, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return {
            (int(float(row["period_id"])), row["target_id"])
            for row in csv.DictReader(stream)
        }


def normalized_detection_rows(report_dir: Path) -> Counter[tuple[tuple[str, str], ...]]:
    path = report_dir / "detection_rows_audit.csv"
    ignored = {"case_id", "run_id", "source_file"}
    with path.open("r", encoding="utf-8", newline="") as stream:
        return Counter(
            tuple(sorted((key, value) for key, value in row.items()
                         if key not in ignored))
            for row in csv.DictReader(stream)
        )


def physical_detection_keys(report_dir: Path) -> Counter[tuple[str, ...]]:
    fields = ("result_id", "period_id", "beam_id", "range_bin", "row", "col")
    with (report_dir / "detection_rows_audit.csv").open(
            "r", encoding="utf-8", newline="") as stream:
        return Counter(tuple(row[field] for field in fields)
                       for row in csv.DictReader(stream))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file-report-dir", required=True, type=Path)
    parser.add_argument("--shm-report-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-cycles", type=int, default=5)
    parser.add_argument("--min-truth-jaccard", type=float, default=0.80)
    parser.add_argument("--max-recall-delta", type=float, default=0.10)
    parser.add_argument("--max-false-relative-delta", type=float, default=0.10)
    parser.add_argument("--max-position-p95-delta-m", type=float, default=10.0)
    args = parser.parse_args()

    file_summary = load_json(args.file_report_dir / "fullflow_summary.json")
    shm_summary = load_json(args.shm_report_dir / "fullflow_summary.json")
    file_keys = matched_truth_keys(args.file_report_dir / "detection_truth_matches.csv")
    shm_keys = matched_truth_keys(args.shm_report_dir / "detection_truth_matches.csv")
    file_detection_rows = normalized_detection_rows(args.file_report_dir)
    shm_detection_rows = normalized_detection_rows(args.shm_report_dir)
    normalized_row_mismatches = sum(
        (file_detection_rows - shm_detection_rows).values()) + sum(
        (shm_detection_rows - file_detection_rows).values())
    file_physical_keys = physical_detection_keys(args.file_report_dir)
    shm_physical_keys = physical_detection_keys(args.shm_report_dir)
    physical_key_mismatches = sum(
        (file_physical_keys - shm_physical_keys).values()) + sum(
        (shm_physical_keys - file_physical_keys).values())
    union = file_keys | shm_keys
    intersection = file_keys & shm_keys
    jaccard = len(intersection) / len(union) if union else 1.0
    recall_delta = abs(file_summary["detection_recall"] -
                       shm_summary["detection_recall"])
    file_false = float(file_summary["false_alarms_per_scan"])
    shm_false = float(shm_summary["false_alarms_per_scan"])
    false_relative_delta = abs(file_false - shm_false) / max(1.0, file_false)
    p95_delta = abs(float(file_summary["position_p95_m"]) -
                    float(shm_summary["position_p95_m"]))
    shm_stream = shm_summary["shm"]
    checks = {
        "file_cycle_count": file_summary["shm"]["completed_cycles"] == args.expected_cycles,
        "shm_cycle_count": shm_stream["completed_cycles"] == args.expected_cycles,
        "shm_stream_integrity": all(shm_stream[name] == 0 for name in (
            "dropped_incomplete", "dropped_backpressure", "prt_gaps",
            "duplicate_prts", "ring_overruns")),
        "matched_truth_jaccard": jaccard >= args.min_truth_jaccard,
        "detection_recall_delta": recall_delta <= args.max_recall_delta,
        "false_alarm_relative_delta": (
            false_relative_delta <= args.max_false_relative_delta),
        "position_p95_delta": p95_delta <= args.max_position_p95_delta_m,
        "file_payload_provenance": bool(
            file_summary["track_debug"]["output_provenance_pass"]),
        "shm_payload_provenance": bool(
            shm_summary["track_debug"]["output_provenance_pass"]),
    }
    status = "passed" if all(checks.values()) else "failed"
    result = {
        "status": status,
        "checks": checks,
        "metrics": {
            "file_matched_truth_keys": len(file_keys),
            "shm_matched_truth_keys": len(shm_keys),
            "intersection": len(intersection),
            "union": len(union),
            "truth_match_jaccard": jaccard,
            "file_detection_rows": sum(file_detection_rows.values()),
            "shm_detection_rows": sum(shm_detection_rows.values()),
            "normalized_detection_row_mismatches": normalized_row_mismatches,
            "physical_detection_key_mismatches": physical_key_mismatches,
            "detection_recall_delta": recall_delta,
            "false_alarm_relative_delta": false_relative_delta,
            "position_p95_delta_m": p95_delta,
        },
        "diagnostics": {
            "normalized_detection_rows_exact": normalized_row_mismatches == 0,
            "note": (
                "Exact row equality is diagnostic only because parallel CUDA "
                "detection ordering/peak selection is not specified as bitwise deterministic."
            ),
        },
        "thresholds": {
            "expected_cycles": args.expected_cycles,
            "min_truth_jaccard": args.min_truth_jaccard,
            "max_recall_delta": args.max_recall_delta,
            "max_false_relative_delta": args.max_false_relative_delta,
            "max_position_p95_delta_m": args.max_position_p95_delta_m,
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "file_shm_equivalence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 文件/共享内存结果等价性",
        "",
        f"结论：**{status}**。本判定只比较结果语义和数据完整性，不使用实时性。",
        "",
        f"- truth 命中集合 Jaccard：{jaccard:.6f}",
        f"- 剔除运行身份字段后的检测行差异：{normalized_row_mismatches}",
        f"- 检测率绝对差：{recall_delta:.6f}",
        f"- 每扫描虚警相对差：{false_relative_delta:.6f}",
        f"- 定位 P95 绝对差：{p95_delta:.6f} m",
        "",
    ]
    lines.extend(f"- {'PASS' if value else 'FAIL'}: `{name}`"
                 for name, value in checks.items())
    (args.output_dir / "file_shm_equivalence.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "output_dir": str(args.output_dir)},
                     ensure_ascii=False))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
