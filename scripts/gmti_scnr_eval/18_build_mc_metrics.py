#!/usr/bin/env python3
"""把中心角 Monte Carlo 的逐周期证据整理成实例表和四层曲线。

本脚本不重新拟合标定，也不重跑算法。它只读取 17 产生的逐周期审计，
把 unit exact CUT、cluster、target select、final truth match、三屏 2-of-3
以及只在 final hit 上计算的角度 RMSE 分开汇总。这样每条曲线都能反查到
一个独立 realization 和同周期 GO/TrackManager 审计。
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
import sys
sys.path.insert(0, str(SCRIPT_DIR))

from scnr_eval_lib import ensure_dir, read_csv, wilson_interval, write_csv  # noqa: E402


def number(value: object, default: float = float("nan")) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def integer(value: object, default: int = 0) -> int:
    value = number(value)
    return int(round(value)) if math.isfinite(value) else default


def bit(value: object) -> int:
    return int(integer(value, 0) != 0)


def mean_or_nan(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return float(np.mean(finite)) if finite else float("nan")


def rmse(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return float(math.sqrt(np.mean(np.square(finite)))) if finite else float("nan")


def conditional(success: int, denominator: int) -> float:
    return success / denominator if denominator > 0 else float("nan")


def p_transition(groups: list[dict[str, int]], first: str, second: str,
                 previous: int) -> float:
    values = [row[second] for row in groups if row[first] == previous]
    return conditional(sum(values), len(values))


def correlation(groups: list[dict[str, int]], first: str, second: str) -> float:
    if len(groups) < 2:
        return float("nan")
    x = np.asarray([row[first] for row in groups], dtype=float)
    y = np.asarray([row[second] for row in groups], dtype=float)
    return float(np.corrcoef(x, y)[0, 1]) if np.std(x) > 0 and np.std(y) > 0 else float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    output_dir = ensure_dir((args.output_dir or run_dir).resolve())
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"缺少 {manifest_path}")
    run_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    desired_grid = [number(value) for value in run_manifest.get("desired_output_scnr_grid_db", [])]
    if not desired_grid:
        raise SystemExit("run_manifest 没有 desired_output_scnr_grid_db")

    instance_rows: list[dict[str, object]] = []
    angle_groups: dict[tuple[float, int, int], list[dict[str, object]]] = defaultdict(list)
    for angle_record in run_manifest.get("angles", []):
        angle = number(angle_record.get("angle_deg"))
        if not math.isfinite(angle) or angle_record.get("status") != "pass":
            continue
        angle_dir = Path(str(angle_record["angle_dir"]))
        period_path = angle_dir / "standard_mc_period_metrics.csv"
        if not period_path.is_file():
            raise SystemExit(f"角度 {angle:g}° 缺少逐周期审计：{period_path}")
        rows = read_csv(period_path)
        for raw in rows:
            period_id = integer(raw.get("period_id"), integer(raw.get("result_id")) - 1)
            level_index = integer(raw.get("input_snr_db_index"), -1)
            if not (0 <= level_index < len(desired_grid)):
                control = number(raw.get("input_snr_db_control"))
                controls = [number(x) for x in angle_record.get("input_snr_db_control", [])]
                level_index = min(range(len(controls)), key=lambda i: abs(controls[i] - control)) if controls else -1
            if level_index < 0 or level_index >= len(desired_grid):
                raise SystemExit(f"无法识别 output-SCNR 档位：{raw}")
            desired = desired_grid[level_index]
            screen_id = period_id % 3 + 1
            group_id = period_id // 3
            final_hit = bit(raw.get("target_event", raw.get("final_output")))
            target_select = bit(raw.get("target_select_hit", raw.get("final_output")))
            row = {
                "case_id": raw.get("case_id", f"angle_{angle:g}_group_{group_id}"),
                "seed": angle_record.get("seed", raw.get("seed", "")),
                "angle_deg": angle, "desired_scnr_out_db": desired,
                "measured_scnr_out_db": number(raw.get("measured_scnr_out_db", raw.get("output_scnr_db"))),
                "measured_signal_power": number(raw.get("measured_signal_power")),
                "P_S_out": number(raw.get("measured_signal_power")),
                "P0_reference": number(raw.get("P0_reference", raw.get("p0_mean_power"))),
                "requested_output_scnr_db": number(raw.get("requested_output_scnr_db")),
                "input_snr_db_control": number(raw.get("input_snr_db_control")),
                "target_snr_config": number(raw.get("input_snr_db_control")),
                "level_index": level_index, "period_id": period_id,
                "track_group_id": group_id, "screen_id": screen_id,
                "reference_range_bin": integer(raw.get("truth_col", raw.get("truth_range_bin")), -1),
                "truth_doppler_bin": integer(raw.get("truth_row", raw.get("truth_cell_doppler_row")), -1),
                # Keep the explicit field requested by the experiment
                # contract while retaining ``truth_doppler_bin`` for older
                # consumers.
                "reference_doppler_bin": integer(raw.get("truth_row", raw.get("truth_cell_doppler_row")), -1),
                "cut_power": number(raw.get("unit_cut_power", raw.get("cut_power"))),
                "unit_cut_power": number(raw.get("unit_cut_power")),
                "go_mean_L": number(raw.get("unit_cfar_left_mean")),
                "go_mean_R": number(raw.get("unit_cfar_right_mean")),
                "go_mean_T": number(raw.get("unit_cfar_top_mean")),
                "go_mean_B": number(raw.get("unit_cfar_bottom_mean")),
                "go_M": max([number(raw.get(key)) for key in (
                    "unit_cfar_left_mean", "unit_cfar_right_mean",
                    "unit_cfar_top_mean", "unit_cfar_bottom_mean") if math.isfinite(number(raw.get(key)))], default=float("nan")),
                "go_threshold": number(raw.get("unit_cfar_threshold", raw.get("cfar_threshold"))),
                "unit_hit": bit(raw.get("unit_exact")),
                "cluster_hit": bit(raw.get("cluster_event")),
                "target_select_hit": target_select,
                "target_select_source": raw.get("target_select_source", "proxy_final_output_truth_match"),
                "final_target_hit": final_hit,
                "target_event": final_hit,
                "angle_truth_deg": number(raw.get("angle_truth_deg", raw.get("truth_angle_deg"))),
                "angle_est_deg": number(raw.get("angle_est_deg")),
                "angle_error_deg": number(raw.get("angle_error_deg")),
                "scnr_error_db": number(raw.get("measured_scnr_out_db", raw.get("output_scnr_db"))) - desired,
                "trackmanager_audit_source": raw.get("extract_source", "production_track_debug_and_detection_csv"),
            }
            instance_rows.append(row)
            angle_groups[(angle, level_index, group_id)].append(row)

    # Re-attach the three-screen event to every instance.  A group can never
    # cross an output-SCNR level because the producer schedules level blocks.
    by_group: dict[tuple[float, int, int], list[dict[str, object]]] = defaultdict(list)
    for row in instance_rows:
        by_group[(float(row["angle_deg"]), integer(row["level_index"]), integer(row["track_group_id"]))].append(row)
    for key, rows in by_group.items():
        rows.sort(key=lambda row: integer(row["screen_id"]))
        hits = [bit(row["final_target_hit"]) for row in rows]
        track_hit = int(sum(hits) >= 2 and len(hits) == 3)
        for row in rows:
            row["track_2of3_hit"] = track_hit
            row["screen_hit_sum"] = sum(hits)
            row["screen1_hit"] = hits[0] if len(hits) == 3 else 0
            row["screen2_hit"] = hits[1] if len(hits) == 3 else 0
            row["screen3_hit"] = hits[2] if len(hits) == 3 else 0

    summary_rows: list[dict[str, object]] = []
    transition_rows: list[dict[str, object]] = []
    for angle in sorted({float(row["angle_deg"]) for row in instance_rows}):
        for level_index, desired in enumerate(desired_grid):
            rows = [row for row in instance_rows if float(row["angle_deg"]) == angle
                    and integer(row["level_index"]) == level_index]
            groups = [rows for key, rows in sorted(by_group.items())
                      if key[0] == angle and key[1] == level_index]
            groups = [sorted(group, key=lambda row: integer(row["screen_id"])) for group in groups]
            group_bits = [{"screen1_hit": bit(next((r for r in group if integer(r["screen_id"]) == 1), {}).get("final_target_hit")),
                           "screen2_hit": bit(next((r for r in group if integer(r["screen_id"]) == 2), {}).get("final_target_hit")),
                           "screen3_hit": bit(next((r for r in group if integer(r["screen_id"]) == 3), {}).get("final_target_hit"))}
                          for group in groups if len(group) == 3]
            unit_hits = sum(bit(row["unit_hit"]) for row in rows)
            cluster_hits = sum(bit(row["cluster_hit"]) for row in rows)
            select_hits = sum(bit(row["target_select_hit"]) for row in rows)
            target_hits = sum(bit(row["final_target_hit"]) for row in rows)
            track_hits = sum(bit(group[0].get("track_2of3_hit")) for group in groups if len(group) == 3)
            unit_low, unit_high = wilson_interval(unit_hits, len(rows))
            cluster_low, cluster_high = wilson_interval(cluster_hits, len(rows))
            target_low, target_high = wilson_interval(target_hits, len(rows))
            track_low, track_high = wilson_interval(track_hits, len(group_bits))
            valid_errors = [number(row["angle_error_deg"]) for row in rows
                            if bit(row["final_target_hit"]) and math.isfinite(number(row["angle_error_deg"]))]
            p12 = mean_or_nan([x["screen1_hit"] * x["screen2_hit"] for x in group_bits])
            p13 = mean_or_nan([x["screen1_hit"] * x["screen3_hit"] for x in group_bits])
            p23 = mean_or_nan([x["screen2_hit"] * x["screen3_hit"] for x in group_bits])
            p123 = mean_or_nan([x["screen1_hit"] * x["screen2_hit"] * x["screen3_hit"] for x in group_bits])
            track_ie = (p12 + p13 + p23 - 2.0 * p123
                        if all(math.isfinite(x) for x in (p12, p13, p23, p123)) else float("nan"))
            measured_values = [number(row["measured_scnr_out_db"]) for row in rows
                               if math.isfinite(number(row["measured_scnr_out_db"]))]
            measured_mean = mean_or_nan(measured_values)
            scnr_errors = [number(row["scnr_error_db"]) for row in rows
                           if math.isfinite(number(row["scnr_error_db"]))]
            scnr_abs = [abs(value) for value in scnr_errors]
            summary_rows.append({
                "angle_deg": angle, "desired_scnr_out_db": desired, "level_index": level_index,
                "unit_hits": unit_hits, "unit_trials": len(rows), "unit_pd": conditional(unit_hits, len(rows)),
                "unit_wilson_low": unit_low, "unit_wilson_high": unit_high,
                "cluster_hits": cluster_hits, "cluster_trials": len(rows), "cluster_pd": conditional(cluster_hits, len(rows)),
                "cluster_wilson_low": cluster_low, "cluster_wilson_high": cluster_high,
                "target_select_hits": select_hits, "target_select_trials": len(rows),
                "target_select_pd": conditional(select_hits, len(rows)),
                "target_hits": target_hits, "target_trials": len(rows), "target_pd": conditional(target_hits, len(rows)),
                "target_wilson_low": target_low, "target_wilson_high": target_high,
                "track_hits": track_hits, "track_trials": len(group_bits), "track_pd": conditional(track_hits, len(group_bits)),
                "track_wilson_low": track_low, "track_wilson_high": track_high,
                "angle_valid": len(valid_errors), "angle_total": target_hits,
                "angle_rmse_deg": rmse(valid_errors),
                "angle_bias_deg": mean_or_nan(valid_errors),
                "output_scnr_db": measured_mean,
                "measured_scnr_out_db_mean": measured_mean,
                "measured_scnr_out_db_std": float(np.std(measured_values, ddof=1)) if len(measured_values) > 1 else float("nan"),
                "scnr_error_db_mean": mean_or_nan(scnr_errors),
                "scnr_error_abs_mean_db": mean_or_nan(scnr_abs),
                "scnr_error_max_abs_db": max(scnr_abs, default=float("nan")),
                "scnr_transfer_pass": int(bool(scnr_abs) and max(scnr_abs) <= 0.5),
                "cluster_given_unit": conditional(cluster_hits, unit_hits),
                "select_given_cluster": conditional(select_hits, cluster_hits),
                "final_given_select": conditional(target_hits, select_hits),
                "track_pd_inclusion_exclusion": track_ie,
                "target_select_semantics": "production final-output proxy; current local-test audit exposes truth match but not a separate selector flag",
            })
            transition_rows.append({
                "angle_deg": angle, "desired_scnr_out_db": desired, "output_scnr_db": measured_mean,
                "measured_scnr_out_db_mean": measured_mean, "level_index": level_index,
                "screen_groups": len(group_bits),
                "p_D2_given_D1": p_transition(group_bits, "screen1_hit", "screen2_hit", 1),
                "p_D2_given_not_D1": p_transition(group_bits, "screen1_hit", "screen2_hit", 0),
                "p_D3_given_D2": p_transition(group_bits, "screen2_hit", "screen3_hit", 1),
                "p_D3_given_not_D2": p_transition(group_bits, "screen2_hit", "screen3_hit", 0),
                "correlation_D1_D2": correlation(group_bits, "screen1_hit", "screen2_hit"),
                "correlation_D2_D3": correlation(group_bits, "screen2_hit", "screen3_hit"),
                "p12": p12, "p13": p13, "p23": p23, "p123": p123,
                "track_pd_inclusion_exclusion": track_ie,
            })

    write_csv(output_dir / "monte_carlo_instances.csv", instance_rows)
    write_csv(output_dir / "mc_curve_summary.csv", summary_rows)
    write_csv(output_dir / "mc_transition_stats.csv", transition_rows)
    (output_dir / "metrics_manifest.json").write_text(json.dumps({
        "run_manifest": str(manifest_path), "instance_count": len(instance_rows),
        "summary_count": len(summary_rows), "transition_count": len(transition_rows),
        "unit_definition": "production GO exact CUT at physical truth row/range cell",
        "target_definition": "final truth output after production matching; cluster and select retained as funnel",
        "track_definition": "truth three-screen 2-of-3; TrackManager audit is a provenance field only",
        "angle_definition": "RMSE over final_target_hit=1 rows only; angle_valid/angle_total retained",
        "scnr_error_definition": "measured_scnr_out_db - desired_scnr_out_db; per-level max absolute error is the formal transfer audit",
        "target_select_definition": "proxy only: production local-test audit does not expose a separate selector rejection flag; target_select_hit equals final truth output and is not an independent loss factor",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] Monte Carlo 指标已生成：{output_dir}，实例数={len(instance_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
