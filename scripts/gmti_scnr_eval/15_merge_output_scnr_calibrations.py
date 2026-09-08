#!/usr/bin/env python3
"""合并已分别完成的 0/30/45° output-SCNR 标定目录。

长时间 CUDA 标定按角度拆分运行时使用本脚本重新组合审计表；它只读取
每个角度已经通过 ``16_validate_output_scnr_transfer.py`` 的产物，不重跑
算法，也不重新拟合每个角度的传递函数。
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from scnr_eval_lib import ensure_dir, read_csv, write_csv  # noqa: E402


def parse_input(text: str) -> tuple[float, Path]:
    angle_text, separator, path_text = text.partition(":")
    if not separator:
        raise argparse.ArgumentTypeError("输入格式必须是 angle:path")
    try:
        angle = float(angle_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"非法角度：{angle_text}") from exc
    path = Path(path_text).expanduser().resolve()
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"标定目录不存在：{path}")
    return angle, path


def finite(value: object) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True, type=parse_input,
                        help="已完成的单角度标定目录，格式 angle:path，可重复")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir = ensure_dir(args.output_dir.resolve())
    mapping_rows: list[dict[str, object]] = []
    audit_rows: list[dict[str, object]] = []
    angle_records: list[dict[str, object]] = []
    seen: set[float] = set()
    for angle, source in sorted(args.input, key=lambda item: item[0]):
        if angle in seen:
            raise SystemExit(f"角度重复：{angle:g}")
        seen.add(angle)
        validation_path = source / "output_scnr_transfer_validation.json"
        mapping_path = source / "calibration_mapping.csv"
        audit_path = source / "output_scnr_transfer_audit.csv"
        manifest_path = source / "calibration_manifest.json"
        missing = [str(path) for path in (validation_path, mapping_path, audit_path) if not path.is_file()]
        if missing:
            raise SystemExit("标定目录缺少必要审计文件：" + ", ".join(missing))
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        rows = [row for row in read_csv(mapping_path)
                if abs(finite(row.get("angle_deg")) - angle) < 1e-6]
        if not rows:
            raise SystemExit(f"{source} 没有 angle={angle:g}° mapping 行")
        if not bool(validation.get("all_pass")):
            raise SystemExit(f"{source} output-SCNR transfer 未通过 sanity")
        mapping_rows.extend(rows)
        audit_rows.extend({**row, "source_calibration_dir": str(source)}
                          for row in read_csv(audit_path))
        source_manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                           if manifest_path.is_file() else {})
        angle_records.append({
            "angle_deg": angle, "source_dir": str(source),
            "validation": validation, "source_manifest": source_manifest,
            "slope_db_per_db": finite(rows[0].get("slope_db_per_db")),
            "intercept_db": finite(rows[0].get("intercept_db")),
        })
    write_csv(output_dir / "calibration_mapping.csv", mapping_rows)
    write_csv(output_dir / "output_scnr_transfer_audit.csv", audit_rows)
    document = {
        "angles": angle_records,
        "axis_definition": "CSI output fixed-support paired E[P_S+N]-E[P_C+N] / C+N-only ensemble mean P0",
        "merge_script": str(Path(__file__).resolve()),
        "input_dirs": [str(source) for _, source in sorted(args.input, key=lambda item: item[0])],
    }
    (output_dir / "calibration_manifest.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 已合并 {len(angle_records)} 个角度的 output-SCNR 标定：{output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
