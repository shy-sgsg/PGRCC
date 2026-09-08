#!/usr/bin/env python3
"""从五周期场景和校准表生成一个保持全局周期ID的单周期场景。"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-scenario", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--period-id", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--override", action="append", default=[],
                        help="覆盖单个目标推荐注入值，格式TARGET=DB，可重复")
    args = parser.parse_args()
    if args.period_id < 0:
        raise SystemExit("--period-id必须非负")

    scenario = json.loads(args.base_scenario.read_text(encoding="utf-8"))
    rows = list(csv.DictReader(args.calibration.open(encoding="utf-8-sig", newline="")))
    values: dict[str, float] = {}
    for row in rows:
        try:
            period_id = int(row["period_id"])
        except (KeyError, ValueError) as exc:
            raise SystemExit(f"无效校准周期：{row}") from exc
        if period_id != args.period_id:
            continue
        target_id = row.get("target_id", "")
        if target_id in values:
            raise SystemExit(f"重复校准项：{target_id}/{period_id}")
        try:
            value = float(row["recommended_injected_snr_db"])
        except (KeyError, ValueError) as exc:
            raise SystemExit(f"缺少有效推荐注入值：{target_id}/{period_id}") from exc
        if not math.isfinite(value):
            raise SystemExit(f"推荐注入值不是有限数：{target_id}/{period_id}")
        values[target_id] = value

    expected = {str(target["target_id"]) for target in scenario["targets"]}
    if set(values) != expected:
        raise SystemExit(
            f"周期{args.period_id}校准目标不完整：缺少={sorted(expected-set(values))}，"
            f"多余={sorted(set(values)-expected)}")
    for override in args.override:
        try:
            target_id, raw_value = override.split("=", 1)
            value = float(raw_value)
        except (ValueError, TypeError) as exc:
            raise SystemExit(f"无效--override：{override}") from exc
        if target_id not in expected or not math.isfinite(value):
            raise SystemExit(f"--override目标或数值无效：{override}")
        values[target_id] = value
    scenario["output_dir"] = str(args.output_dir.resolve())
    scenario["random"]["period_start"] = args.period_id
    scenario["random"]["period_count"] = 1
    scenario["random"]["target_reference_period"] = 0
    for target in scenario["targets"]:
        value = values[str(target["target_id"])]
        target["amplitude"]["snr_db"] = value
        target["amplitude"]["snr_db_by_period"] = [value]
    contract = scenario.setdefault("design_contract", {})
    contract["calibration_period_id"] = args.period_id
    contract["calibration_source"] = str(args.calibration.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(scenario, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"周期{args.period_id}单周期校准场景：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
