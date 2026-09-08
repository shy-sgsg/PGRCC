#!/usr/bin/env python3
"""把单周期校准报告合并回五周期注入计划，保持其余周期不变。"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--period-report", action="append", default=[],
                        help="PERIOD=CSV，单周期报告，可重复")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    merged = read(args.base)
    index = {(row["target_id"], int(row["period_id"])): row for row in merged}
    for spec in args.period_report:
        try:
            raw_period, raw_path = spec.split("=", 1)
            period = int(raw_period)
        except ValueError as exc:
            raise SystemExit(f"--period-report格式必须为PERIOD=CSV：{spec}") from exc
        report_rows = read(Path(raw_path))
        seen = set()
        for report in report_rows:
            key = (report.get("target_id", ""), int(report["period_id"]))
            if key[1] != period or key not in index or key in seen:
                raise SystemExit(f"单周期报告目标/周期不匹配或重复：{key}")
            if not report.get("recommended_injected_snr_db"):
                raise SystemExit(f"缺少推荐注入值：{key}")
            index[key]["recommended_injected_snr_db"] = report[
                "recommended_injected_snr_db"]
            seen.add(key)
        expected = {key for key in index if key[1] == period}
        if seen != expected:
            raise SystemExit(
                f"周期{period}报告不完整：缺少={sorted(expected-seen)}，多余={sorted(seen-expected)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(merged[0])
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(merged)
    print(f"已合并逐周期校准计划：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
