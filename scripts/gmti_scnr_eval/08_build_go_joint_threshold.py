#!/usr/bin/env python3
"""由正式 GO-CFAR 目标/航迹 Pd 与测角统计反求联合输出 SCNR 门限。"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti_scnr_eval_matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from scnr_eval_lib import ensure_dir, read_csv, write_csv


def parse_angles(text: str) -> tuple[float, ...]:
    values = tuple(float(token.strip()) for token in text.split(",") if token.strip())
    if not values or len({round(value, 8) for value in values}) != len(values):
        raise ValueError("evaluation-angles 必须为不重复的数值列表")
    return values


def number(row: dict[str, str], key: str) -> float:
    try:
        return float(row.get(key, ""))
    except (TypeError, ValueError):
        return float("nan")


def first_satisfying(rows: list[dict[str, float]], field: str, target: float,
                     at_least: bool) -> float:
    candidates = [row["scnr"] for row in rows if math.isfinite(row.get(field, float("nan"))) and
                  (row[field] >= target if at_least else row[field] <= target)]
    return float(min(candidates)) if candidates else float("nan")


def max_required(values: list[float]) -> float:
    return float(max(values)) if values and all(math.isfinite(value) for value in values) else float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pd-summary", type=Path, required=True,
                        help="08_summarize_go_pd.py 的 go_target_and_track_pd_summary.csv")
    parser.add_argument("--angle-summary", type=Path, required=True,
                        help="08_summarize_go_pd.py 的 go_angle_error_summary.csv")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-pd", type=float, default=0.90)
    parser.add_argument("--track-pd", type=float, default=0.90)
    parser.add_argument("--angle-rmse-deg", type=float, default=0.20)
    parser.add_argument("--evaluation-angles", default="0,30,45",
                        help="参与联合门限的实际命令波位（度）")
    args = parser.parse_args()
    if not (0.0 < args.target_pd < 1.0 and 0.0 < args.track_pd < 1.0 and args.angle_rmse_deg > 0.0):
        raise SystemExit("Pd 门限必须在 (0,1)，测角 RMSE 门限必须为正")
    pd_rows = read_csv(args.pd_summary.resolve())
    angle_rows = read_csv(args.angle_summary.resolve())
    required_angles = parse_angles(args.evaluation_angles)
    if not pd_rows or not angle_rows:
        raise SystemExit("Pd 或测角汇总为空，不能反求联合门限")

    by_key: dict[tuple[float, float], dict[str, float]] = {}
    for row in pd_rows:
        angle = number(row, "truth_angle_deg")
        injection = number(row, "configured_target_snr_db")
        scnr = number(row, "paired_calibrated_output_scnr_db")
        if not (math.isfinite(angle) and math.isfinite(injection) and math.isfinite(scnr)):
            continue
        by_key[(round(angle, 8), round(injection, 8))] = {
            "scnr": scnr,
            "target_pd": number(row, "target_pd"),
            "target_lcb": min(number(row, "target_pd_wilson95_low"),
                              number(row, "target_pd_seed_bootstrap95_low")),
            "track_pd": number(row, "trackmanager_protocol_track_pd"),
            "track_lcb": min(number(row, "track_pd_wilson95_low"),
                             number(row, "track_pd_seed_bootstrap95_low")),
        }
    if not by_key:
        raise SystemExit("Pd 汇总不含有效的配对标定输出 SCNR；拒绝使用注入值替代")

    angle_by_angle: dict[float, list[dict[str, float]]] = defaultdict(list)
    for row in angle_rows:
        angle = number(row, "truth_angle_deg")
        injection = number(row, "configured_target_snr_db")
        metrics = by_key.get((round(angle, 8), round(injection, 8)))
        if metrics is not None:
            angle_by_angle[angle].append({
                "scnr": metrics["scnr"], "angle_rmse": number(row, "angle_rmse_deg"),
                "angle_ucb": number(row, "angle_rmse_seed_bootstrap95_high"),
            })
    angle_thresholds: dict[float, tuple[float, float]] = {}
    for requested in required_angles:
        candidates = [angle for angle in angle_by_angle if abs(angle - requested) < 1.0e-6]
        if len(candidates) != 1:
            raise SystemExit(f"测角汇总缺少正式 {requested:g}° 数据")
        rows = angle_by_angle[candidates[0]]
        angle_thresholds[requested] = (
            first_satisfying(rows, "angle_rmse", args.angle_rmse_deg, at_least=False),
            first_satisfying(rows, "angle_ucb", args.angle_rmse_deg, at_least=False),
        )

    all_pd_rows = list(by_key.values())
    target_point = first_satisfying(all_pd_rows, "target_pd", args.target_pd, at_least=True)
    target_conservative = first_satisfying(all_pd_rows, "target_lcb", args.target_pd, at_least=True)
    track_point = first_satisfying(all_pd_rows, "track_pd", args.track_pd, at_least=True)
    track_conservative = first_satisfying(all_pd_rows, "track_lcb", args.track_pd, at_least=True)
    point_joint = max_required([*(pair[0] for pair in angle_thresholds.values()), target_point, track_point])
    conservative_joint = max_required([*(pair[1] for pair in angle_thresholds.values()),
                                       target_conservative, track_conservative])

    output_rows: list[dict[str, object]] = []
    for angle in required_angles:
        point, conservative = angle_thresholds[angle]
        output_rows.append({
            "constraint": f"angle_rmse_{angle:g}deg", "point_estimate_scnr_db": point,
            "conservative_95_scnr_db": conservative,
            "criterion": f"RMSE <= {args.angle_rmse_deg:g} deg; conservative uses seed-bootstrap UCB",
        })
    output_rows.extend([
        {"constraint": "target_pd", "point_estimate_scnr_db": target_point,
         "conservative_95_scnr_db": target_conservative,
         "criterion": f"Pd >= {args.target_pd:g}; conservative uses min(Wilson LCB, seed-bootstrap LCB)"},
        {"constraint": "trackmanager_protocol_track_pd", "point_estimate_scnr_db": track_point,
         "conservative_95_scnr_db": track_conservative,
         "criterion": f"Pd >= {args.track_pd:g}; protocol-valid TrackManager output only; conservative uses min(Wilson LCB, seed-bootstrap LCB)"},
        {"constraint": "joint_max", "point_estimate_scnr_db": point_joint,
         "conservative_95_scnr_db": conservative_joint,
         "criterion": "max of all configured command-angle, target Pd, and TrackManager protocol Pd constraints"},
    ])
    out = ensure_dir(args.output_dir.resolve())
    write_csv(out / "go_joint_scnr_thresholds.csv", output_rows)
    payload = {
        "cfar_type": "GO", "angle_rmse_limit_deg": args.angle_rmse_deg,
        "target_pd_limit": args.target_pd, "track_pd_limit": args.track_pd,
        "point_estimate_joint_scnr_db": point_joint,
        "conservative_95_joint_scnr_db": conservative_joint,
        "method": "discrete minimum satisfying formal samples; no interpolation or extrapolation across absent refine points",
    }
    (out / "go_joint_scnr_thresholds.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    labels = [str(row["constraint"]) for row in output_rows]
    positions = np.arange(len(labels))
    fig, axis = plt.subplots(figsize=(9.0, 4.8))
    axis.bar(positions - 0.18, [row["point_estimate_scnr_db"] for row in output_rows],
             width=0.36, label="点估计")
    axis.bar(positions + 0.18, [row["conservative_95_scnr_db"] for row in output_rows],
             width=0.36, label="95% 保守")
    axis.set(xticks=positions, xticklabels=labels, ylabel="所需配对输出 SCNR (dB)",
             title="GO-CFAR 测角/目标/航迹联合门限")
    axis.tick_params(axis="x", rotation=25)
    axis.grid(axis="y", alpha=0.3); axis.legend(); fig.tight_layout()
    fig.savefig(out / "F12_go_joint_scnr_thresholds.png", dpi=180); plt.close(fig)
    print(f"[PASS] 已反求 GO 联合门限：point={point_joint:g} dB, conservative95={conservative_joint:g} dB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
