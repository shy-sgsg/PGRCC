#!/usr/bin/env python3
"""合并同一方位的多批 GO 配对标定原始样本，避免重复 SCNR 键覆盖。"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from scnr_eval_lib import ensure_dir, read_csv, write_csv


def number(row: dict[str, str], field: str) -> float:
    try:
        return float(row[field])
    except (KeyError, TypeError, ValueError):
        return float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, action="append", required=True,
                        help="可重复给出含 go_paired_output_scnr_samples.csv 的 calibration batch 目录")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    all_rows: list[dict[str, str]] = []
    resolved_inputs: list[str] = []
    for directory in args.input_dir:
        samples = directory.resolve() / "go_paired_output_scnr_samples.csv"
        if not samples.is_file():
            raise SystemExit(f"缺少标定原始样本：{samples}")
        all_rows.extend(read_csv(samples))
        resolved_inputs.append(str(samples))
    if not all_rows:
        raise SystemExit("没有可合并的标定样本")
    out = ensure_dir(args.output_dir.resolve())
    write_csv(out / "go_paired_output_scnr_samples.csv", all_rows)
    grouped: dict[tuple[float, float], list[dict[str, str]]] = defaultdict(list)
    for row in all_rows:
        grouped[(round(number(row, "truth_angle_deg"), 6),
                 number(row, "configured_target_snr_db"))].append(row)
    metrics = (
        "output_scnr_det_out_db", "contextual_incremental_output_scnr_db", "effective_output_scnr_det_out_db",
        "output_scnr_phase_ch1_db", "output_scnr_phase_ch2_db", "output_scnr_phase_eff_db",
        "phase_sigma_ideal_rad", "s_only_to_contextual_incremental_csi_power_ratio",
    )
    summary: list[dict[str, object]] = []
    for (angle, injection), rows in sorted(grouped.items()):
        result: dict[str, object] = {
            "truth_angle_deg": angle, "configured_target_snr_db": injection,
            "sample_count": len(rows),
            "independent_seed_count": len({row.get("calibration_seed", "") for row in rows}),
            "scnr_definition": "see raw paired samples: S-only and nonlinear effective output-SCNR use C+N GO directional-max training reference",
        }
        for metric in metrics:
            values = [number(row, metric) for row in rows]
            values = [value for value in values if math.isfinite(value)]
            result[metric + "_median"] = float(np.median(values)) if values else float("nan")
            result[metric + "_mean"] = float(np.mean(values)) if values else float("nan")
            result[metric + "_p05"] = float(np.quantile(values, 0.05)) if values else float("nan")
            result[metric + "_p95"] = float(np.quantile(values, 0.95)) if values else float("nan")
        summary.append(result)
    write_csv(out / "go_paired_output_scnr_summary.csv", summary)
    (out / "calibration_merge_provenance.json").write_text(json.dumps({
        "input_samples": resolved_inputs, "row_count": len(all_rows),
        "merge_method": "raw fixed-cell samples regrouped by truth_angle_deg/configured_target_snr_db",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 已合并 {len(all_rows)} 条 GO 配对标定样本：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
