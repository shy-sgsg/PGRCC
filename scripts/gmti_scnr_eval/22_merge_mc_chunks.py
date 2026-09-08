#!/usr/bin/env python3
"""合并分块运行的 output-SCNR Monte Carlo 审计，不重新拟合或重跑算法。"""

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


def p_transition(groups: list[dict[str, int]], first: str, second: str, previous: int) -> float:
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
    parser.add_argument("--run-dir", action="append", required=True,
                        help="一个分块 17_run_center_angle_monte_carlo 输出目录；可重复")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    chunk_dirs = [Path(value).resolve() for value in args.run_dir]
    output_dir = ensure_dir(args.output_dir.resolve())
    manifests = []
    for path in chunk_dirs:
        manifest_path = path / "run_manifest.json"
        if not manifest_path.is_file():
            raise SystemExit(f"缺少分块 manifest：{manifest_path}")
        manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))
    desired_grid = [number(value) for value in manifests[0].get("desired_output_scnr_grid_db", [])]
    if not desired_grid:
        raise SystemExit("分块 manifest 没有 desired_output_scnr_grid_db")
    for document in manifests[1:]:
        candidate = [number(value) for value in document.get("desired_output_scnr_grid_db", [])]
        if candidate != desired_grid:
            raise SystemExit(f"分块 output-SCNR 网格不一致：{desired_grid} vs {candidate}")

    instance_rows: list[dict[str, object]] = []
    combined_angles: list[dict[str, object]] = []
    # group ids are local to each 17 invocation; offset them before calculating
    # three-screen events so chunks cannot accidentally form cross-chunk tracks.
    for chunk_index, (chunk_dir, manifest) in enumerate(zip(chunk_dirs, manifests)):
        group_offset = chunk_index * 1_000_000
        for angle_record in manifest.get("angles", []):
            angle = number(angle_record.get("angle_deg"))
            if not math.isfinite(angle) or angle_record.get("status") != "pass":
                continue
            angle_dir = Path(str(angle_record["angle_dir"]))
            period_path = angle_dir / "standard_mc_period_metrics.csv"
            if not period_path.is_file():
                raise SystemExit(f"分块角度 {angle:g}° 缺少逐周期审计：{period_path}")
            combined_record = dict(angle_record)
            combined_record["chunk_id"] = chunk_index
            combined_record["chunk_dir"] = str(chunk_dir)
            combined_angles.append(combined_record)
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
                local_group = period_id // 3
                group_id = local_group + group_offset
                screen_id = period_id % 3 + 1
                final_hit = bit(raw.get("target_event", raw.get("final_output")))
                target_select = bit(raw.get("target_select_hit", raw.get("final_output")))
                measured = number(raw.get("measured_scnr_out_db", raw.get("output_scnr_db")))
                row = {
                    "case_id": f"chunk{chunk_index:03d}:{raw.get('case_id', f'angle_{angle:g}_group_{local_group}')}",
                    "chunk_id": chunk_index,
                    "seed": angle_record.get("seed", raw.get("seed", "")),
                    "angle_deg": angle, "desired_scnr_out_db": desired,
                    "measured_scnr_out_db": measured,
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
                    # Explicit audit name required by the MC contract;
                    # ``truth_doppler_bin`` remains as a compatibility alias.
                    "reference_doppler_bin": integer(raw.get("truth_row", raw.get("truth_cell_doppler_row")), -1),
                    "cut_power": number(raw.get("unit_cut_power", raw.get("cut_power"))),
                    "unit_cut_power": number(raw.get("unit_cut_power")),
                    "go_mean_L": number(raw.get("unit_cfar_left_mean")),
                    "go_mean_R": number(raw.get("unit_cfar_right_mean")),
                    "go_mean_T": number(raw.get("unit_cfar_top_mean")),
                    "go_mean_B": number(raw.get("unit_cfar_bottom_mean")),
                    "go_M": max([number(raw.get(key)) for key in (
                        "unit_cfar_left_mean", "unit_cfar_right_mean",
                        "unit_cfar_top_mean", "unit_cfar_bottom_mean")
                        if math.isfinite(number(raw.get(key)))], default=float("nan")),
                    "go_threshold": number(raw.get("unit_cfar_threshold", raw.get("cfar_threshold"))),
                    "unit_hit": bit(raw.get("unit_exact")),
                    "cluster_hit": bit(raw.get("cluster_event")),
                    "target_select_hit": target_select,
                    "target_select_source": raw.get("target_select_source", "proxy_final_output_truth_match"),
                    "final_target_hit": final_hit, "target_event": final_hit,
                    "angle_truth_deg": number(raw.get("angle_truth_deg", raw.get("truth_angle_deg"))),
                    "angle_est_deg": number(raw.get("angle_est_deg")),
                    "angle_error_deg": number(raw.get("angle_error_deg")),
                    "scnr_error_db": measured - desired,
                    "trackmanager_audit_source": raw.get("extract_source", "production_track_debug_and_detection_csv"),
                }
                instance_rows.append(row)

    by_group: dict[tuple[float, int, int], list[dict[str, object]]] = defaultdict(list)
    for row in instance_rows:
        by_group[(float(row["angle_deg"]), integer(row["level_index"]), integer(row["track_group_id"]))].append(row)
    for rows in by_group.values():
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
            rows = [row for row in instance_rows if float(row["angle_deg"]) == angle and integer(row["level_index"]) == level_index]
            groups = [sorted(group, key=lambda row: integer(row["screen_id"]))
                      for key, group in by_group.items() if key[0] == angle and key[1] == level_index]
            group_bits = [{"screen1_hit": bit(group[0].get("final_target_hit")),
                           "screen2_hit": bit(group[1].get("final_target_hit")),
                           "screen3_hit": bit(group[2].get("final_target_hit"))}
                          for group in groups if len(group) == 3]
            unit_hits = sum(bit(row["unit_hit"]) for row in rows)
            cluster_hits = sum(bit(row["cluster_hit"]) for row in rows)
            select_hits = sum(bit(row["target_select_hit"]) for row in rows)
            target_hits = sum(bit(row["final_target_hit"]) for row in rows)
            track_hits = sum(bit(group[0].get("track_2of3_hit")) for group in groups if len(group) == 3)
            p12 = mean_or_nan([x["screen1_hit"] * x["screen2_hit"] for x in group_bits])
            p13 = mean_or_nan([x["screen1_hit"] * x["screen3_hit"] for x in group_bits])
            p23 = mean_or_nan([x["screen2_hit"] * x["screen3_hit"] for x in group_bits])
            p123 = mean_or_nan([x["screen1_hit"] * x["screen2_hit"] * x["screen3_hit"] for x in group_bits])
            track_ie = p12 + p13 + p23 - 2.0 * p123 if all(math.isfinite(x) for x in (p12, p13, p23, p123)) else float("nan")
            measured = [number(row["measured_scnr_out_db"]) for row in rows if math.isfinite(number(row["measured_scnr_out_db"]))]
            errors = [number(row["scnr_error_db"]) for row in rows if math.isfinite(number(row["scnr_error_db"]))]
            abs_errors = [abs(value) for value in errors]
            valid_errors = [number(row["angle_error_deg"]) for row in rows if bit(row["final_target_hit"]) and math.isfinite(number(row["angle_error_deg"]))]
            summary_rows.append({
                "angle_deg": angle, "desired_scnr_out_db": desired, "output_scnr_db": mean_or_nan(measured), "level_index": level_index,
                "unit_hits": unit_hits, "unit_trials": len(rows), "unit_pd": conditional(unit_hits, len(rows)),
                "unit_wilson_low": wilson_interval(unit_hits, len(rows))[0], "unit_wilson_high": wilson_interval(unit_hits, len(rows))[1],
                "cluster_hits": cluster_hits, "cluster_trials": len(rows), "cluster_pd": conditional(cluster_hits, len(rows)),
                "cluster_wilson_low": wilson_interval(cluster_hits, len(rows))[0], "cluster_wilson_high": wilson_interval(cluster_hits, len(rows))[1],
                "target_select_hits": select_hits, "target_select_trials": len(rows), "target_select_pd": conditional(select_hits, len(rows)),
                "target_hits": target_hits, "target_trials": len(rows), "target_pd": conditional(target_hits, len(rows)),
                "target_wilson_low": wilson_interval(target_hits, len(rows))[0], "target_wilson_high": wilson_interval(target_hits, len(rows))[1],
                "track_hits": track_hits, "track_trials": len(group_bits), "track_pd": conditional(track_hits, len(group_bits)),
                "track_wilson_low": wilson_interval(track_hits, len(group_bits))[0], "track_wilson_high": wilson_interval(track_hits, len(group_bits))[1],
                "angle_valid": len(valid_errors), "angle_total": target_hits, "angle_rmse_deg": rmse(valid_errors), "angle_bias_deg": mean_or_nan(valid_errors),
                "measured_scnr_out_db_mean": mean_or_nan(measured), "measured_scnr_out_db_std": float(np.std(measured, ddof=1)) if len(measured) > 1 else float("nan"),
                "scnr_error_db_mean": mean_or_nan(errors), "scnr_error_abs_mean_db": mean_or_nan(abs_errors), "scnr_error_max_abs_db": max(abs_errors, default=float("nan")),
                "scnr_transfer_pass": int(bool(abs_errors) and max(abs_errors) <= 0.5),
                "cluster_given_unit": conditional(cluster_hits, unit_hits), "select_given_cluster": conditional(select_hits, cluster_hits),
                "final_given_select": conditional(target_hits, select_hits), "track_pd_inclusion_exclusion": track_ie,
                "target_select_semantics": "production final-output proxy; no separate selector rejection flag",
            })
            transition_rows.append({
                "angle_deg": angle, "desired_scnr_out_db": desired, "output_scnr_db": mean_or_nan(measured), "screen_groups": len(group_bits),
                "p_D2_given_D1": p_transition(group_bits, "screen1_hit", "screen2_hit", 1), "p_D2_given_not_D1": p_transition(group_bits, "screen1_hit", "screen2_hit", 0),
                "p_D3_given_D2": p_transition(group_bits, "screen2_hit", "screen3_hit", 1), "p_D3_given_not_D2": p_transition(group_bits, "screen2_hit", "screen3_hit", 0),
                "correlation_D1_D2": correlation(group_bits, "screen1_hit", "screen2_hit"), "correlation_D2_D3": correlation(group_bits, "screen2_hit", "screen3_hit"),
                "p12": p12, "p13": p13, "p23": p23, "p123": p123, "track_pd_inclusion_exclusion": track_ie,
            })

    write_csv(output_dir / "monte_carlo_instances.csv", instance_rows)
    write_csv(output_dir / "mc_curve_summary.csv", summary_rows)
    write_csv(output_dir / "mc_transition_stats.csv", transition_rows)
    calibration_dirs = [str(m.get("calibration_dir")) for m in manifests
                        if m.get("calibration_dir")]
    calibration_dir = calibration_dirs[0] if calibration_dirs else ""
    if calibration_dirs and any(value != calibration_dir for value in calibration_dirs[1:]):
        raise SystemExit(f"分块 calibration_dir 不一致：{calibration_dirs}")
    (output_dir / "run_manifest.json").write_text(json.dumps({
        "chunks": [str(path) for path in chunk_dirs], "angles": combined_angles,
        "desired_output_scnr_grid_db": desired_grid, "trials_per_level": sum(int(m.get("trials_per_level", 0)) for m in manifests),
        "chunk_count": len(chunk_dirs), "status": "pass", "calibration_dir": calibration_dir,
        "axis_definition": "fixed S-only support power / C+N-only ensemble mean P0",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "metrics_manifest.json").write_text(json.dumps({
        "run_dirs": [str(path) for path in chunk_dirs], "instance_count": len(instance_rows),
        "summary_count": len(summary_rows), "transition_count": len(transition_rows), "chunk_count": len(chunk_dirs),
        "unit_definition": "production GO exact CUT at physical truth row/range cell",
        "target_definition": "final truth output after production matching; cluster and select retained as funnel",
        "track_definition": "truth three-screen 2-of-3; TrackManager audit is provenance only",
        "angle_definition": "RMSE over final_target_hit=1 rows only; angle_valid/angle_total retained",
        "scnr_error_definition": "measured_scnr_out_db - desired_scnr_out_db",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 分块 Monte Carlo 已合并：{output_dir}，chunk={len(chunk_dirs)}，实例数={len(instance_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
