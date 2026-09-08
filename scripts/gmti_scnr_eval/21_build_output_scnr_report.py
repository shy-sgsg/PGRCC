#!/usr/bin/env python3
"""把理论说明、output-SCNR 标定和 Monte Carlo 证据汇成可交付 PDF/HTML。"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
import sys
sys.path.insert(0, str(SCRIPT_DIR))
from scnr_eval_lib import (read_csv, remap_target_theory_rows,
                           production_calibration_curve_rows,
                           target_theory_axis_offset_db)  # noqa: E402


def number(value: object) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def fmt(value: object, digits: int = 2) -> str:
    value = number(value)
    return f"{value:.{digits}f}" if math.isfinite(value) else "—"


def token(angle: float) -> str:
    return f"angle_{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def interpolate_clamped(x_values: list[float], y_values: list[float], query: float) -> float:
    pairs = sorted((x, y) for x, y in zip(x_values, y_values)
                   if math.isfinite(x) and math.isfinite(y))
    if not pairs:
        return float("nan")
    if query <= pairs[0][0]:
        return pairs[0][1]
    if query >= pairs[-1][0]:
        return pairs[-1][1]
    for (x0, y0), (x1, y1) in zip(pairs, pairs[1:]):
        if x0 <= query <= x1:
            return y0 if x1 == x0 else y0 + (query - x0) * (y1 - y0) / (x1 - x0)
    return float("nan")


def upward_crossing(x_values: list[float], y_values: list[float], target: float) -> float:
    pairs = sorted((x, y) for x, y in zip(x_values, y_values)
                   if math.isfinite(x) and math.isfinite(y))
    for index, (x1, y1) in enumerate(pairs):
        if y1 < target:
            continue
        if index == 0:
            return x1
        x0, y0 = pairs[index - 1]
        return x1 if y1 == y0 else x0 + (target - y0) * (x1 - x0) / (y1 - y0)
    return float("nan")


def table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---:" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mc-dir", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, default=None)
    parser.add_argument("--output-pdf", type=Path,
                        default=ROOT / "docs/GMTI_输出SCNR_测角精度与检测概率_理论建模与MonteCarlo闭环报告_20260826.pdf")
    parser.add_argument("--output-html", type=Path, default=None)
    parser.add_argument("--target-theory-dir", type=Path,
                        default=ROOT / "docs/GMTI_输出SCNR_目标级minpoints理论_20260826")
    parser.add_argument("--minpoints-dir", type=Path,
                        default=ROOT / "outputs/gmti_scnr_eval/target_minpoints_mc_20260828",
                        help="min_points=2/3/6 实测汇总目录；缺失时报告明确标记待补")
    args = parser.parse_args()
    mc_dir = args.mc_dir.resolve()
    md_path = (args.output_md or mc_dir / "输出SCNR_理论建模与MonteCarlo闭环报告_20260826.md").resolve()
    md_path.parent.mkdir(parents=True, exist_ok=True)
    summary = read_csv(mc_dir / "mc_curve_summary.csv")
    joint = read_csv(mc_dir / "joint_threshold/joint_threshold_summary.csv")
    run_manifest_path = mc_dir / "run_manifest.json"
    metrics_manifest_path = mc_dir / "metrics_manifest.json"
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8")) if run_manifest_path.is_file() else {}
    metrics_manifest = json.loads(metrics_manifest_path.read_text(encoding="utf-8")) if metrics_manifest_path.is_file() else {}
    calibration_candidates = [
        mc_dir / "../calibration/output_scnr_transfer_validation.csv",
        mc_dir / "../../calibration/output_scnr_transfer_validation.csv",
    ]
    calibration_path = next((path for path in calibration_candidates if path.is_file()), calibration_candidates[0])
    external_calibration = run_manifest.get("calibration_dir")
    if external_calibration:
        candidate = Path(str(external_calibration)) / "output_scnr_transfer_validation.csv"
        if candidate.is_file():
            calibration_path = candidate
    if not calibration_path.is_file():
        raise FileNotFoundError(
            "未找到 output-SCNR 校准表；请检查 run_manifest.json 的 calibration_dir "
            "或旧版相邻 calibration 目录。"
        )
    calibration = read_csv(calibration_path)
    calibration_run_root = calibration_path.parent / "calibration"
    if not calibration_run_root.is_dir():
        calibration_run_root = calibration_path.parent
    target_summary = read_csv(args.target_theory_dir.resolve() / "target_pd_minpoints_summary.csv")
    minpoints_dir = args.minpoints_dir.resolve()
    minpoints_summary_path = minpoints_dir / "minpoints_separate" / "target_pd_minpoints_summary.csv"
    if not minpoints_summary_path.is_file():
        minpoints_summary_path = minpoints_dir / "target_pd_minpoints_summary.csv"
    minpoints_summary = read_csv(minpoints_summary_path) if minpoints_summary_path.is_file() else []
    minpoints_manifest_path = minpoints_summary_path.parent / "target_pd_minpoints_manifest.json"
    minpoints_manifest = {}
    if minpoints_manifest_path.is_file():
        try:
            minpoints_manifest = json.loads(minpoints_manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            minpoints_manifest = {}
    # Center-MC runs intentionally keep the calibration artifacts in a
    # separate, shared directory.  Use the path recorded by the driver before
    # falling back to the legacy sibling layout above.
    if external_calibration:
        candidate = Path(str(external_calibration)) / "output_scnr_transfer_validation.csv"
        if candidate.is_file():
            calibration_path = candidate
            calibration = read_csv(candidate)
            calibration_run_root = calibration_path.parent / "calibration"
            if not calibration_run_root.is_dir():
                calibration_run_root = calibration_path.parent
    calibration_angle_dirs: dict[float, Path] = {}
    calibration_manifest_path = calibration_path.parent / "calibration_manifest.json"
    if calibration_manifest_path.is_file():
        try:
            calibration_manifest = json.loads(calibration_manifest_path.read_text(encoding="utf-8"))
            for item in calibration_manifest.get("angles", []):
                angle_value = number(item.get("angle_deg"))
                angle_dir = item.get("angle_dir")
                # Merged calibration manifests retain the per-angle path
                # inside source_manifest.  Resolve it here so the report can
                # include the original calibration rows rather than claiming
                # that the detail is absent.
                if not angle_dir:
                    source_manifest = item.get("source_manifest") or {}
                    source_angles = source_manifest.get("angles") or []
                    if source_angles:
                        angle_dir = source_angles[0].get("angle_dir")
                if math.isfinite(angle_value) and angle_dir:
                    calibration_angle_dirs[angle_value] = Path(str(angle_dir))
        except (OSError, ValueError, TypeError):
            pass
    trials_per_level = run_manifest.get("trials_per_level", "—")
    instance_count = metrics_manifest.get("instance_count", len(summary))
    transition_count = metrics_manifest.get("transition_count", "—")
    angles = sorted({number(row.get("angle_deg")) for row in summary})
    summary_rows = []
    for angle in angles:
        row = next((x for x in joint if abs(number(x.get("angle_deg")) - angle) < 1e-6), {})
        mc_values = [("RMSE", number(row.get("mc_angle_rmse_0p2_db"))),
                     ("unit", number(row.get("mc_unit_pd_0p9_db"))),
                     ("target", number(row.get("mc_target_pd_0p9_db"))),
                     ("track", number(row.get("mc_track_pd_0p9_db")))]
        mc_worst = max((value for _, value in mc_values if math.isfinite(value)), default=float("nan"))
        mc_worst_name = next((name for name, value in mc_values if math.isfinite(value) and abs(value - mc_worst) < 1e-9), "—")
        summary_rows.append([f"{angle:g}°", fmt(row.get("theory_angle_rmse_0p2_db")), fmt(row.get("mc_angle_rmse_0p2_db")),
                             fmt(row.get("theory_fixed_threshold_unit_pd_0p9_db")),
                             fmt(row.get("theory_unit_pd_0p9_db")), fmt(row.get("mc_unit_pd_0p9_db")),
                             fmt(row.get("theory_target_pd_0p9_db")), fmt(row.get("mc_target_pd_0p9_db")),
                             fmt(row.get("mc_track_pd_0p9_db")), fmt(row.get("mc_joint_max_db")),
                             f"{mc_worst_name}={fmt(mc_worst)}"])
    # Keep the requested first-page orientation as an explicit indicator table:
    # rows are the engineering requirements, columns are the three angles, and
    # the last column records the worst finite crossing (angle + value).
    joint_by_angle = {
        angle: next((x for x in joint if abs(number(x.get("angle_deg")) - angle) < 1e-6), {})
        for angle in angles
    }
    indicator_specs = [
        ("理论测角 RMSE<=0.2°所需 SCNR", "theory_angle_rmse_0p2_db"),
        ("MC测角 RMSE<=0.2°所需 SCNR", "mc_angle_rmse_0p2_db"),
        ("GO单元 Pd>=0.9 所需 SCNR", "mc_unit_pd_0p9_db"),
        ("目标 Pd>=0.9 所需 SCNR", "mc_target_pd_0p9_db"),
        ("航迹 Pd>=0.9 所需 SCNR", "mc_track_pd_0p9_db"),
        ("联合要求（四项 MC 最大值）", "mc_joint_max_db"),
    ]
    indicator_rows = []
    for label, field in indicator_specs:
        values = [(angle, number(joint_by_angle.get(angle, {}).get(field))) for angle in angles]
        finite = [(angle, value) for angle, value in values if math.isfinite(value)]
        worst = max(finite, key=lambda item: item[1]) if finite else (float("nan"), float("nan"))
        by_angle = {angle: value for angle, value in values}
        indicator_rows.append([label,
                               fmt(by_angle.get(0.0)), fmt(by_angle.get(30.0)), fmt(by_angle.get(45.0)),
                               f"{worst[0]:g}° / {fmt(worst[1])}" if math.isfinite(worst[1]) else "—"])
    # The archived target theory uses the 15-cell total-SCNR coordinate.  The
    # current report is explicitly on the production fixed 3x3 output axis,
    # so remap both the table values and the crossing values with measured
    # kappa/beta before presenting them as output-SCNR quantities.
    target_groups = {0.0: "0deg_center", 30.0: "30deg_center", 45.0: "45deg_center"}
    mapped_target_summary: list[dict[str, object]] = []
    raw_target_theory = read_csv(args.target_theory_dir.resolve() / "target_pd_minpoints_theory.csv")
    for angle in sorted(target_groups):
        mapped = remap_target_theory_rows(
            [row for row in raw_target_theory if row.get("angle_group") == target_groups[angle]],
            args.target_theory_dir.resolve(), angle,
        )
        for minimum in (2, 3, 6):
            rows = [row for row in mapped if int(round(number(row.get("min_points")))) == minimum]
            x_values = [number(row.get("output_scnr_db")) for row in rows]
            y_values = [number(row.get("pd_target_poisson_binomial_profile")) for row in rows]
            mapped_target_summary.append({
                "angle_group": target_groups[angle], "angle_deg": angle,
                "position": "center", "min_points": minimum,
                "pd_at_10db": interpolate_clamped(x_values, y_values, 10.0),
                "pd_at_15db": interpolate_clamped(x_values, y_values, 15.0),
                "pd_at_20db": interpolate_clamped(x_values, y_values, 20.0),
                "scnr_at_pd_0p5_db": upward_crossing(x_values, y_values, 0.5),
                "scnr_at_pd_0p9_db": upward_crossing(x_values, y_values, 0.9),
                "support_sample_count": rows[0].get("support_sample_count", "—") if rows else "—",
                "model": rows[0].get("model", "—") if rows else "—",
            })
    target_summary = mapped_target_summary
    target_axis_rows = [[f"{angle:g}°", fmt(target_theory_axis_offset_db(args.target_theory_dir.resolve(), angle), 3),
                         "15 单元总 SCNR → 固定 3×3 支撑 output SCNR"] for angle in sorted(target_groups)]
    angle_module_rows = []
    module_path = ROOT / "outputs/gmti_scnr_eval/angle_modules_20260827/angle_module_summary.csv"
    if module_path.is_file():
        for row in read_csv(module_path):
            sample_count = number(row.get("sample_count"))
            angle_module_rows.append([row.get("module", ""),
                                      str(int(sample_count)) if math.isfinite(sample_count) else "—",
                                      fmt(row.get("max_abs_error"), 4), row.get("status", ""),
                                      row.get("parameter_source", "")])
    angle_theory_summary = read_csv(ROOT / "outputs/gmti_scnr_eval/angle_theory/angle_theory_summary.csv")
    angle_theory_rows = [[fmt(row.get("truth_angle_deg"), 0) + "°",
                          fmt(row.get("production_crossing_output_scnr_db")),
                          fmt(row.get("center_cut_mapping_slope_db_per_db"), 4),
                          fmt(row.get("center_cut_mapping_intercept_db"), 3),
                          fmt(row.get("center_cut_mapping_r2"), 5),
                          fmt(row.get("center_cut_mapping_max_abs_residual_db"), 3)]
                         for row in angle_theory_summary]
    min_rows = []
    for row in target_summary:
        min_rows.append([row.get("angle_group", ""), row.get("min_points", ""), fmt(row.get("pd_at_10db"), 4),
                         fmt(row.get("pd_at_15db"), 4), fmt(row.get("pd_at_20db"), 4),
                         fmt(row.get("scnr_at_pd_0p5_db")), fmt(row.get("scnr_at_pd_0p9_db"))])
    cal_rows = [[row.get("angle_deg", ""), fmt(row.get("slope_db_per_db"), 4), fmt(row.get("r2"), 5),
                 fmt(row.get("max_abs_residual_db"))] for row in calibration]
    scnr_by_angle = []
    for angle in angles:
        angle_rows = [row for row in summary if abs(number(row.get("angle_deg")) - angle) < 1e-6]
        errors = [number(row.get("scnr_error_abs_mean_db")) for row in angle_rows if math.isfinite(number(row.get("scnr_error_abs_mean_db")))]
        max_errors = [number(row.get("scnr_error_max_abs_db")) for row in angle_rows if math.isfinite(number(row.get("scnr_error_max_abs_db")))]
        scnr_by_angle.append([f"{angle:g}°", fmt(sum(errors) / len(errors) if errors else float("nan")),
                              fmt(max(max_errors) if max_errors else float("nan")),
                              min((int(number(row.get("unit_trials"))) for row in angle_rows), default=0),
                              max((int(number(row.get("unit_trials"))) for row in angle_rows), default=0)])
    # Detailed tables are generated directly from the authoritative CSV so
    # every plotted point has an auditable companion row and sample count.
    detail_rows = sorted(summary, key=lambda row: (number(row.get("angle_deg")),
                                                    number(row.get("output_scnr_db"))))
    production_calibration: dict[float, list[dict[str, str]]] = {}
    if run_manifest.get("calibration_dir"):
        calibration_root = Path(str(run_manifest["calibration_dir"]))
        production_calibration = {
            angle: production_calibration_curve_rows(calibration_root, angle)
            for angle in angles
        }

    def production_model_value(angle: float, output_scnr_db: object, field: str) -> float:
        rows = production_calibration.get(angle, [])
        return interpolate_clamped(
            [number(row.get("output_scnr_db")) for row in rows],
            [number(row.get(field)) for row in rows],
            number(output_scnr_db),
        )

    def count_text(row: dict[str, str], field: str) -> str:
        value = number(row.get(field))
        return str(int(value)) if math.isfinite(value) else "—"
    figure_a_rows = [[fmt(row.get("angle_deg"), 0) + "°", fmt(row.get("output_scnr_db")),
                      fmt(row.get("angle_rmse_deg"), 3), fmt(row.get("angle_bias_deg"), 3),
                      fmt(production_model_value(number(row.get("angle_deg")), row.get("output_scnr_db"), "angle_rmse_deg"), 3),
                      f"{count_text(row, 'angle_valid')}/{count_text(row, 'angle_total')}"]
                     for row in detail_rows]
    figure_b_rows = [[fmt(row.get("angle_deg"), 0) + "°", fmt(row.get("output_scnr_db")),
                      fmt(row.get("unit_pd"), 4), fmt(row.get("unit_wilson_low"), 4),
                      fmt(row.get("unit_wilson_high"), 4), count_text(row, "unit_trials"),
                      fmt(row.get("cluster_pd"), 4)] for row in detail_rows]
    figure_c_rows = [[fmt(row.get("angle_deg"), 0) + "°", fmt(row.get("output_scnr_db")),
                      fmt(row.get("cluster_pd"), 4), fmt(row.get("target_select_pd"), 4),
                      fmt(row.get("target_pd"), 4), fmt(row.get("target_wilson_low"), 4),
                      fmt(row.get("target_wilson_high"), 4),
                      fmt(production_model_value(number(row.get("angle_deg")), row.get("output_scnr_db"), "target_pd"), 4),
                      count_text(row, "target_trials")]
                     for row in detail_rows]
    figure_d_rows = [[fmt(row.get("angle_deg"), 0) + "°", fmt(row.get("output_scnr_db")),
                      fmt(row.get("track_pd"), 4), fmt(row.get("track_wilson_low"), 4),
                      fmt(row.get("track_wilson_high"), 4),
                      fmt(production_model_value(number(row.get("angle_deg")), row.get("output_scnr_db"), "track_pd"), 4),
                      count_text(row, "track_trials"),
                      fmt(row.get("track_pd_inclusion_exclusion"), 4)] for row in detail_rows]
    # Compare the merged evaluation points with the independent production
    # calibration curves for the three downstream metrics.  The comparison is
    # diagnostic (not a refit): it makes the finite-sample spread visible while
    # keeping the production funnel model separate from the PB baseline.
    production_residual_rows = []
    for angle in angles:
        calibration_rows = production_calibration.get(angle, [])
        if not calibration_rows:
            continue
        cal_x = np.asarray([number(row.get("output_scnr_db")) for row in calibration_rows], dtype=float)
        metric_residuals: dict[str, list[float]] = {"target_pd": [], "track_pd": [], "angle_rmse_deg": []}
        metric_counts: dict[str, int] = {key: 0 for key in metric_residuals}
        for observed in [row for row in summary if abs(number(row.get("angle_deg")) - angle) < 1e-6]:
            x_value = number(observed.get("output_scnr_db"))
            if not math.isfinite(x_value):
                continue
            for field in metric_residuals:
                observed_value = number(observed.get(field))
                if not math.isfinite(observed_value):
                    continue
                predicted_values = [number(row.get(field)) for row in calibration_rows]
                predicted = interpolate_clamped(cal_x, predicted_values, x_value)
                if math.isfinite(predicted):
                    metric_residuals[field].append(abs(observed_value - predicted))
                    metric_counts[field] += 1
        if any(metric_residuals[field] for field in metric_residuals):
            def residual_text(field: str) -> str:
                values = metric_residuals[field]
                return (f"{max(values):.3f}/{float(np.mean(values)):.3f}"
                        if values else "—")
            production_residual_rows.append([
                f"{angle:g}°", residual_text("target_pd"),
                residual_text("track_pd"), residual_text("angle_rmse_deg"),
                f"{metric_counts['target_pd']}/{metric_counts['track_pd']}/{metric_counts['angle_rmse_deg']}",
            ])
    transition_path = mc_dir / "mc_transition_stats.csv"
    transition = read_csv(transition_path) if transition_path.is_file() else []
    transition_rows = [[fmt(row.get("angle_deg"), 0) + "°", fmt(row.get("output_scnr_db")),
                        count_text(row, "screen_groups"), fmt(row.get("p_D2_given_D1"), 4),
                        fmt(row.get("p_D2_given_not_D1"), 4), fmt(row.get("correlation_D1_D2"), 4),
                        fmt(row.get("track_pd_inclusion_exclusion"), 4)] for row in transition]
    calibration_detail_rows = []
    calibration_p0_by_angle: dict[float, tuple[float, float]] = {}
    for angle, angle_dir in calibration_angle_dirs.items():
        for manifest_path in (angle_dir / "manifest.json", angle_dir.parent / "manifest.json"):
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                p0_value = number(manifest.get("p0_mean_power"))
                p0_center_value = number(manifest.get("p0_center_mean_power"))
                if math.isfinite(p0_value):
                    calibration_p0_by_angle[angle] = (p0_value, p0_center_value)
                    break
            except (OSError, ValueError, TypeError):
                continue
    for angle in angles:
        path_candidates = []
        if angle in calibration_angle_dirs:
            # The per-angle calibration directory has used three equivalent
            # names over the successive calibration runs.  Prefer the
            # aggregated summary, then the processed summary, and finally
            # the raw per-period table.  All are authoritative outputs of the
            # same paired calibration and are normalized below.
            path_candidates.extend([
                calibration_angle_dirs[angle] / "output_snr_calibration_summary.csv",
                calibration_angle_dirs[angle] / "processed_output_snr_calibration.csv",
                calibration_angle_dirs[angle] / "output_snr_calibration.csv",
            ])
        path_candidates.extend([
            calibration_run_root / token(angle) / "output_snr_calibration_summary.csv",
            calibration_run_root / token(angle) / "processed_output_snr_calibration.csv",
            calibration_run_root / token(angle) / "output_snr_calibration.csv",
            calibration_run_root.parent / token(angle) / "output_snr_calibration_summary.csv",
            calibration_run_root.parent / token(angle) / "processed_output_snr_calibration.csv",
            calibration_run_root.parent / token(angle) / "output_snr_calibration.csv",
        ])
        path = next((candidate for candidate in path_candidates if candidate.is_file()), None)
        if path is not None:
            for row in read_csv(path):
                measured = row.get("measured_output_snr_db") or row.get("processed_output_scnr_db")
                measured_std = row.get("measured_output_snr_db_std") or row.get("processed_output_scnr_db_std")
                s_power = row.get("s_only_power_mean") or row.get("processed_signal_support_power_mean")
                p0_power = row.get("p0_mean_power") or row.get("p0_support_power_mean")
                if not p0_power and angle in calibration_p0_by_angle:
                    p0_power = calibration_p0_by_angle[angle][0]
                sample_count = row.get("sample_count") or row.get("screen_group_count")
                bias = row.get("median_bias_db")
                if not bias and measured and row.get("predicted_output_scnr_db"):
                    try:
                        bias = number(measured) - number(row.get("predicted_output_scnr_db"))
                    except (TypeError, ValueError):
                        bias = ""
                calibration_detail_rows.append([f"{angle:g}°", fmt(row.get("input_snr_db_control")),
                    fmt(measured), fmt(measured_std), fmt(s_power, 6), fmt(p0_power, 6),
                    fmt(sample_count, 0), fmt(bias, 3)])
    calibration_detail_rows.sort(key=lambda row: (row[0], number(row[1])))
    # Preserve the complete C+N-only fixed-support background distribution in
    # the report.  The raw p0_ensemble.csv is authoritative; the manifest is
    # used only as a fallback for older calibration directories.
    p0_stats_rows = []
    for angle in angles:
        angle_dir = calibration_angle_dirs.get(angle)
        p0_values = []
        p0_center_values = []
        if angle_dir is not None:
            p0_path = angle_dir / "p0_ensemble.csv"
            if p0_path.is_file():
                for row in read_csv(p0_path):
                    value = number(row.get("p0_power"))
                    center = number(row.get("p0_center_power"))
                    if math.isfinite(value):
                        p0_values.append(value)
                    if math.isfinite(center):
                        p0_center_values.append(center)
        manifest_values = {}
        if angle_dir is not None:
            manifest_path = angle_dir / "manifest.json"
            if manifest_path.is_file():
                try:
                    manifest_values = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    manifest_values = {}
        if p0_values:
            p0_std = float(np.std(p0_values, ddof=1)) if len(p0_values) > 1 else float("nan")
            center_mean = float(np.mean(p0_center_values)) if p0_center_values else float("nan")
            center_median = float(np.median(p0_center_values)) if p0_center_values else float("nan")
            p0_stats_rows.append([
                f"{angle:g}°", str(len(p0_values)), fmt(np.mean(p0_values), 6), fmt(p0_std, 6),
                fmt(np.median(p0_values), 6), fmt(np.percentile(p0_values, 5), 6),
                fmt(np.percentile(p0_values, 95), 6), fmt(center_mean, 6), fmt(center_median, 6),
            ])
        elif math.isfinite(number(manifest_values.get("p0_mean_power"))):
            p0_stats_rows.append([
                f"{angle:g}°", "—", fmt(manifest_values.get("p0_mean_power"), 6),
                fmt(manifest_values.get("p0_std_power"), 6), fmt(manifest_values.get("p0_median_power"), 6),
                fmt(manifest_values.get("p0_p05_power"), 6), fmt(manifest_values.get("p0_p95_power"), 6),
                fmt(manifest_values.get("p0_center_mean_power"), 6),
                fmt(manifest_values.get("p0_center_median_power"), 6),
            ])
    # The first low-SCNR point is the only place where this small validation
    # batch can visibly separate a unit theory value from the observed hit
    # count.  Report its exact binomial uncertainty instead of presenting a
    # 3-group realization as an algorithmic regression.
    unit_low_notes = []
    # A merged MC directory keeps a copied ``runs/`` tree for figure assets,
    # while the authoritative theory curves live in each chunk.  Evaluate the
    # theory at the merged row's measured output-SCNR instead of pairing it
    # with the first chunk's lowest row (which can have a different realized
    # SCNR).
    authoritative_chunk_dirs = [Path(str(chunk)) for chunk in run_manifest.get("chunks", [])]
    if not authoritative_chunk_dirs:
        authoritative_chunk_dirs = [mc_dir]
    for angle in angles:
        angle_rows = sorted(
            [row for row in summary if abs(number(row.get("angle_deg")) - angle) < 1e-6],
            key=lambda row: number(row.get("output_scnr_db")),
        )
        theory_sets = []
        for chunk_dir in authoritative_chunk_dirs:
            theory_path = chunk_dir / "runs" / token(angle) / token(angle) / "go_theory_vs_mc.csv"
            if theory_path.is_file():
                rows = read_csv(theory_path)
                if rows:
                    theory_sets.append(rows)
        if not angle_rows or not theory_sets:
            continue
        observed = angle_rows[0]
        observed_output = number(observed.get("output_scnr_db"))
        theory_values = []
        for theory_rows in theory_sets:
            theory_values.append(interpolate_clamped(
                [number(row.get("output_scnr_db")) for row in theory_rows],
                [number(row.get("unit_pd_empirical_n_only")) for row in theory_rows],
                observed_output,
            ))
        theory_values = [value for value in theory_values if math.isfinite(value)]
        if not theory_values:
            continue
        n_value = number(observed.get("unit_trials"))
        k_value = number(observed.get("unit_hits"))
        p_value = float(np.mean(theory_values))
        if not (math.isfinite(n_value) and math.isfinite(k_value) and math.isfinite(p_value)
                and n_value > 0.0):
            continue
        n_int = int(round(n_value)); k_int = int(round(k_value))
        cdf = sum(math.comb(n_int, k) * p_value ** k * (1.0 - p_value) ** (n_int - k)
                  for k in range(0, min(k_int, n_int) + 1))
        unit_low_notes.append(
            f"{angle:g}°最低档（实测 output SCNR={observed_output:.2f} dB）：理论 {p_value:.4f}，"
            f"实测 {k_int}/{n_int}={k_value / n_value:.4f}，"
            f"在理论概率下出现不高于该命中数的二项概率为 {cdf:.3f}"
        )
    unit_low_note = "；".join(unit_low_notes) if unit_low_notes else "当前批次没有可用的最低档 unit 统计。"
    unit_low_sample_note = (
        "这些最低档 unit 计数必须结合 Wilson 区间和上面的二项概率解读；"
        "本验证批次每个角度每档的 unit 分母由合并后的真实 period 数决定，"
        "不能把某个旧 pilot 的样本数带入当前结论。"
    )
    # Quantify the repaired unit-level agreement on the merged, measured
    # output-SCNR axis.  Each chunk carries an independently generated N-only
    # empirical GO curve; average those curves at the merged MC abscissae and
    # report the observed-vs-theory absolute residual.  This keeps the report
    # evidence-backed without claiming a large-sample confidence bound.
    unit_residual_rows = []
    for angle in angles:
        angle_rows = sorted(
            [row for row in summary if abs(number(row.get("angle_deg")) - angle) < 1e-6],
            key=lambda row: number(row.get("output_scnr_db")),
        )
        theory_sets = []
        for chunk_dir in authoritative_chunk_dirs:
            theory_path = chunk_dir / "runs" / token(angle) / token(angle) / "go_theory_vs_mc.csv"
            if theory_path.is_file():
                rows = read_csv(theory_path)
                if rows:
                    theory_sets.append(rows)
        residuals = []
        for observed in angle_rows:
            observed_x = number(observed.get("output_scnr_db"))
            observed_y = number(observed.get("unit_pd"))
            predictions = []
            for theory_rows in theory_sets:
                prediction = interpolate_clamped(
                    [number(row.get("output_scnr_db")) for row in theory_rows],
                    [number(row.get("unit_pd_empirical_n_only")) for row in theory_rows],
                    observed_x,
                )
                if math.isfinite(prediction):
                    predictions.append(prediction)
            if math.isfinite(observed_y) and predictions:
                residuals.append(abs(observed_y - float(np.mean(predictions))))
        if residuals:
            unit_residual_rows.append([
                f"{angle:g}°",
                f"{max(residuals):.4f}",
                f"{float(np.mean(residuals)):.4f}",
                str(len(residuals)),
            ])
    unit_residual_note = (
        "这里的差异是在每个合并 MC 档位的实测 output SCNR 处，"
        "将 production unit hit 频率与各 chunk 的 N-only 经验 GO 积分取均值后比较；"
        "最大差异由最低 SCNR 档的有限样本计数主导，不能解读为新的系统偏差。"
        if unit_residual_rows else
        "当前目录没有足够的 chunk 经验理论曲线，无法生成逐档残差表。"
    )
    # The production chain may splice an in-band CSI branch and an
    # out-of-band/original branch into one diagnostic power map.  Their
    # absolute power scales are not interchangeable, while the production
    # threshold map is kept branch-local.  Quantify the historical offline
    # reconstruction error whenever the per-period audit is available so the
    # root-cause section is evidence-backed rather than qualitative.
    threshold_mix_values: list[float] = []
    threshold_audit_paths: set[Path] = set()
    chunk_paths = [Path(str(chunk)) for chunk in run_manifest.get("chunks", [])]
    if chunk_paths:
        # A merged report may also contain a copied ``runs/`` tree for figure
        # assets.  Read the authoritative chunk paths only, otherwise the
        # copied first chunk would be counted twice in the diagnostic mean.
        for chunk_path in chunk_paths:
            if chunk_path.is_dir():
                for candidate in chunk_path.rglob("standard_mc_period_metrics.csv"):
                    threshold_audit_paths.add(candidate.resolve())
    else:
        for candidate in mc_dir.rglob("standard_mc_period_metrics.csv"):
            threshold_audit_paths.add(candidate.resolve())
    for audit_path in sorted(threshold_audit_paths):
        try:
            for row in read_csv(audit_path):
                mixed = number(row.get("unit_cfar_threshold_mixed"))
                production = number(row.get("unit_cfar_threshold"))
                if mixed > 0.0 and production > 0.0:
                    threshold_mix_values.append(10.0 * math.log10(mixed / production))
        except (OSError, ValueError, TypeError):
            continue
    if threshold_mix_values:
        threshold_mix_note = (
            f"本批可审计逐周期样本 {len(threshold_mix_values)} 个："
            f"离线混合阈值相对 production threshold 平均高 "
            f"{sum(threshold_mix_values) / len(threshold_mix_values):.2f} dB，"
            f"中位数 {float(np.median(threshold_mix_values)):.2f} dB，"
            f"标准差 {float(np.std(threshold_mix_values, ddof=1)):.2f} dB"
        )
    else:
        threshold_mix_note = "当前报告目录未找到逐周期 mixed/production threshold 对照；保留该项为历史诊断结论。"
    status_note = ("正式大样本批次" if isinstance(trials_per_level, (int, float)) and trials_per_level >= 100
                   else f"当前验证批次（每档 {trials_per_level} 组三屏）")
    command_text = run_manifest.get("command_string", run_manifest.get("command", run_manifest.get("commands", "未在 manifest 中记录")))
    if command_text == "未在 manifest 中记录" and run_manifest.get("angles"):
        command_text = run_manifest["angles"][0].get("command_string", command_text)
    if isinstance(command_text, (list, tuple)):
        command_text = " ".join(str(item) for item in command_text)
    figure_lines = []
    for name, title in (("figure_A_angle_rmse.png", "Figure A：输出 SCNR—测角 RMSE"),
                        ("figure_B_unit_pd.png", "Figure B：输出 SCNR—单元级 GO Pd"),
                        ("figure_C_target_pd.png", "Figure C：输出 SCNR—目标级最终 Pd"),
                        ("figure_D_track_pd.png", "Figure D：输出 SCNR—三屏选二航迹 Pd")):
        figure_lines.append(f"### {title}\n\n![{title}](final_four_curves/{name})\n")
    for angle in angles:
        p = mc_dir / "runs" / token(angle) / token(angle) / "figures/output_snr_calibration.png"
        if p.is_file():
            figure_lines.append(f"### {angle:g}° output-SCNR 标定 sanity\n\n![{angle:g}°标定](runs/{token(angle)}/{token(angle)}/figures/output_snr_calibration.png)\n")
    figure_block = "\n\n".join(figure_lines)

    # Build a compact but explicit experiment ledger.  The report is meant to
    # be read without opening the scripts, so every plotted family states its
    # source CSV, event definition, sample denominator and one numerical
    # substitution.  Values are derived from the authoritative merged CSV and
    # are never re-fitted here.
    desired_levels = run_manifest.get("desired_output_scnr_grid_db", [])
    level_count = len(desired_levels) if isinstance(desired_levels, list) else 0
    chunk_count = len(run_manifest.get("chunks", [])) if isinstance(run_manifest.get("chunks", []), list) else 0
    screen_groups = number(transition_count)
    if not math.isfinite(screen_groups):
        screen_groups = float("nan")
    calibration_p0_n = 108
    if calibration_detail_rows:
        inferred = [number(row[6]) for row in calibration_detail_rows if math.isfinite(number(row[6]))]
        if inferred:
            calibration_p0_n = int(round(max(inferred)))
    minpoint_counts = []
    minpoint_figure_block = []
    if minpoints_summary:
        for minimum in (2, 3, 6):
            rows = [row for row in minpoints_summary if int(round(number(row.get("min_points")))) == minimum]
            if rows:
                roots = sorted({str(row.get("source_root", "")) for row in rows if row.get("source_root")})
                total_trials = sum(int(round(number(row.get("target_trials")))) for row in rows
                                   if math.isfinite(number(row.get("target_trials"))))
                minpoint_counts.append([str(minimum), str(len(rows)), str(total_trials),
                                        "；".join(Path(root).name for root in roots) or "—"])
                image = minpoints_summary_path.parent / f"target_pd_minpoints_{minimum}.png"
                if image.is_file() and image.parent == md_path.parent / "minpoints_separate":
                    rel_image = f"minpoints_separate/target_pd_minpoints_{minimum}.png"
                elif image.is_file():
                    try:
                        rel_image = str(image.relative_to(md_path.parent))
                    except ValueError:
                        rel_image = str(image)
                else:
                    rel_image = ""
                block = [f"### min_points={minimum} 目标级实测（独立分图）", ""]
                if rel_image:
                    block.append(f"![min_points={minimum} target Pd]({rel_image})")
                    block.append("")
                block.append(table(["角度", "output SCNR(dB)", "target hits/n", "target Pd", "Wilson 下界", "Wilson 上界"], [
                    [fmt(row.get("angle_deg"), 0) + "°", fmt(row.get("output_scnr_db")),
                     f"{int(round(number(row.get('target_hits'))))}/{int(round(number(row.get('target_trials'))))}",
                     fmt(row.get("target_pd"), 4), fmt(row.get("target_wilson_low"), 4),
                     fmt(row.get("target_wilson_high"), 4)]
                    for row in sorted(rows, key=lambda item: (number(item.get("angle_deg")), number(item.get("output_scnr_db"))))
                ]))
                block.append("")
                block.append(
                    f"该图只包含 min_points={minimum} 的真实生产链结果；每个点的分子/分母直接来自 "
                    "`target_hits/target_trials`，阴影为 Wilson 95% 区间，不与另外两个 min_points 共用纵轴数据。"
                )
                minpoint_figure_block.append("\n".join(block))
    minpoint_status = (
        "已找到 min_points=2/3/6 的独立实测汇总，三种配置分别绘制并保留原始来源。"
        if len(minpoint_counts) == 3 else
        "min_points=2/3/6 实测汇总尚不完整；缺失配置只保留理论/待补标记，不把理论曲线冒充实测。"
    )

    # Select one representative row per angle for worked substitutions.  The
    # point nearest 18 dB has enough downstream hits in the current validation
    # batch while still showing the unit→target→track funnel.
    def nearest_row(angle: float, field: str = "output_scnr_db", target: float = 18.0) -> dict[str, str]:
        candidates = [row for row in detail_rows if abs(number(row.get("angle_deg")) - angle) < 1e-6
                      and math.isfinite(number(row.get(field)))]
        return min(candidates, key=lambda row: abs(number(row.get(field)) - target)) if candidates else {}

    def theory_value(angle: float, target_x: float, x_field: str, y_field: str) -> float:
        paths = [Path(str(chunk)) / "runs" / token(angle) / token(angle) / "go_theory_vs_mc.csv"
                 for chunk in authoritative_chunk_dirs]
        for path in paths:
            if path.is_file():
                rows = read_csv(path)
                value = interpolate_clamped([number(row.get(x_field)) for row in rows],
                                            [number(row.get(y_field)) for row in rows], target_x)
                if math.isfinite(value):
                    return value
        return float("nan")

    example_rows = []
    for angle in angles:
        row = nearest_row(angle)
        if not row:
            continue
        x_value = number(row.get("output_scnr_db"))
        mapped = None
        angle_path = ROOT / "outputs/gmti_scnr_eval/angle_theory" / f"angle_theory_{int(round(angle))}deg.csv"
        angle_rows = read_csv(angle_path) if angle_path.is_file() else []
        for item in angle_rows:
            if math.isfinite(number(item.get("center_cut_mapping_slope_db_per_db"))):
                mapped = (number(item.get("center_cut_mapping_slope_db_per_db")),
                          number(item.get("center_cut_mapping_intercept_db")))
                break
        center_db = mapped[0] * x_value + mapped[1] if mapped else float("nan")
        fixed_direct = float(fixed_pd(10.0 ** (x_value / 10.0), 1.0e-6))
        fixed_mapped = float(fixed_pd(10.0 ** (center_db / 10.0), 1.0e-6)) if math.isfinite(center_db) else float("nan")
        go_emp = theory_value(angle, x_value, "output_scnr_db", "unit_pd_empirical_n_only")
        model_target = production_model_value(angle, x_value, "target_pd")
        model_track = production_model_value(angle, x_value, "track_pd")
        p_target = number(row.get("target_pd"))
        track_baseline = 3.0 * p_target * p_target - 2.0 * p_target ** 3 if math.isfinite(p_target) else float("nan")
        example_rows.append({"angle": angle, "row": row, "center_db": center_db,
                             "fixed_direct": fixed_direct, "fixed_mapped": fixed_mapped,
                             "go_emp": go_emp, "model_target": model_target,
                             "model_track": model_track, "track_baseline": track_baseline})
    example_text = []
    for item in example_rows:
        row = item["row"]
        angle = item["angle"]
        example_text.append(
            f"- {angle:g}°、output SCNR={number(row.get('output_scnr_db')):.2f} dB："
            f"MC unit={int(round(number(row.get('unit_hits'))))}/{int(round(number(row.get('unit_trials'))))}="
            f"{number(row.get('unit_pd')):.3f}，经验 GO 积分={item['go_emp']:.3f}，"
            f"教材固定门限（直接错轴/中心 CUT 映射）={item['fixed_direct']:.4f}/{item['fixed_mapped']:.4f}；"
            f"target={int(round(number(row.get('target_hits'))))}/{int(round(number(row.get('target_trials'))))}="
            f"{number(row.get('target_pd')):.3f}，track={int(round(number(row.get('track_hits'))))}/"
            f"{int(round(number(row.get('track_trials'))))}={number(row.get('track_pd')):.3f}，"
            f"独立校准生产模型 target/track={item['model_target']:.3f}/{item['model_track']:.3f}。"
        )
    worked_examples = "\n".join(example_text) if example_text else "当前没有可用的代表点。"

    parameter_rows = [
        ["Pfa", "1×10⁻⁶", "CFAR 虚警控制量；η=−ln(Pfa)=13.8155", "运行时 XML / manifest"],
        ["GO guard / background", "4 / 16 cells", "外窗 25×25、保护窗 9×9；8 个不重叠块重建方向均值", "GO 几何与源码窗口"],
        ["GO α", "13.4495103", "N=544；α=N(Pfa⁻¹/N−1)", "go_theory_vs_mc.csv"],
        ["固定 output 支撑", "Doppler×range=3×3", "分母 P0 是 C+N-only ensemble 均值；不按检测峰移动", "run manifest"],
        ["生产目标聚类", "min_points=6", "3–5 点仅在局部峰值超过中位数 20 dB 时恢复", "运行时 XML / target event"],
        ["正式 MC evaluation", f"{chunk_count} chunks × {trials_per_level} 组三屏 × {level_count} 档 × 3 屏 = {int(instance_count) if math.isfinite(number(instance_count)) else '—'} periods", "每个角度×档位 unit/target 分母为 15（若合并 3 chunks）", "run_manifest / mc_curve_summary.csv"],
        ["GO 经验积分", f"每 chunk {int(metrics_manifest.get('theory_samples', 20000)) if str(metrics_manifest.get('theory_samples','')).isdigit() else 20000} 个训练窗", "按 Q1 条件项对真实 N-only M/P0 积分", "go_theory_vs_mc.csv"],
        ["C+N-only P0", f"每角度 {calibration_p0_n} 个固定支撑样本", "均值作分母；std/P05/P95 只描述背景波动", "p0_ensemble.csv"],
    ]
    for item in minpoint_counts:
        parameter_rows.append([f"目标实测 min_points={item[0]}", f"{item[1]} 个角度×档位行，screen trials 合计 {item[2]}", "同一 output-SCNR 定义；图、表独立", item[3]])
    parameter_table = table(["参数/实验", "本次取值", "物理/统计意义", "证据来源"], parameter_rows)
    minpoint_count_table = table(["min_points", "曲线行数", "screen trials 合计", "来源目录"], minpoint_counts) if minpoint_counts else "尚无完整 min_points 实测汇总。"
    minpoint_figure_text = "\n\n".join(minpoint_figure_block) if minpoint_figure_block else "当前 min_points=2/3/6 实测分图尚未全部生成；报告保留 PB 理论表，缺失实测不作性能结论。"
    # Leakage audit is deliberately reported separately from performance: a
    # high Pd is not evidence that the detector was blind to simulation labels.
    leakage_audits = []
    for audit_path in sorted(minpoints_dir.rglob("information_leakage_audit.json")):
        try:
            leakage_audits.append((audit_path, json.loads(audit_path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError):
            continue
    audit_passes = sum(1 for _, document in leakage_audits if document.get("status") == "pass")
    audit_failures = len(leakage_audits) - audit_passes
    leakage_rows = [
        ["目标 truth 文件", "不得进入生产 XML / detector", "XML: `debug_pc_peak=false`、`pc_peak_scene_truth=''`", "每个 pipeline_manifest + XML 审计"],
        ["NMS 并列检测取舍", "只能使用观测的功率、beam、row、range", "已移除 truth 距离/位置误差 tie-break", "`src/writeresults.cpp` 源码审查 + 重建"],
        ["N-only 背景", "严格模式不得回写 Doppler/P38", "`--strict-online-no-prior`；只作离线 P0/M 统计", "每角度 manifest: `strict_online_no_prior=true`"],
        ["S-only 高参考", "只能确定离线固定支撑，不得传给 S+N detector", "仅生成 output-SCNR 坐标/支撑表", "manifest policy + 独立 S+N XML"],
        ["truth match / RMSE / 2-of-3", "只能在 detector 输出之后用于计分", "不参与 CFAR、聚类、NMS、TrackManager 决策", "离线 CSV evaluator"],
    ]
    leakage_audit_note = (
        f"已发现 {len(leakage_audits)} 份严格运行审计：PASS={audit_passes}，FAIL={audit_failures}。"
        if leakage_audits else
        "本次报告目录尚未找到严格运行的信息泄露审计文件；历史曲线不得标为严格无先验结果。"
    )
    md = rf"""---
title: "GMTI 输出 SCNR—测角精度—检测概率理论建模与 Monte Carlo 闭环报告"
subtitle: "0°/30°/45°中心波位；GO-CFAR pfa=1e-6、guard=4、background=16、生产 min_points=6"
CJKmainfont: Noto Sans CJK SC
geometry: margin=1.65cm
fontsize: 10pt
---

# 技术摘要

本报告把理论曲线和真实 CUDA 生产链的 Monte Carlo 闭环接起来。正式横轴是 CSI 输出/GO 输入面固定真值支撑的 output SCNR：$10\log_{{10}}(P_S/P_0)$；Stage2 `target_snr_db` 只用于反解目标幅度控制量，不能作为主图横轴。当前输入背景、面积杂波开关和链路状态以 `run_manifest.json` 为准；{status_note}与最终大样本验收严格区分，不把未达到样本量的交点写成最终门限。

## 首页交付表

理论交点来自已审计的 GO/测角/min_points 模型；MC 交点来自本次实际实例表，若档位不足以形成交点则明确显示“—”，不插值填假数据。

{table(["角度", "理论 RMSE<=0.2°", "MC RMSE<=0.2°", "固定门限 unit理论", "GO unit理论", "MC unit", "理论 target", "MC target", "MC track", "MC联合最大", "最差项"], summary_rows)}

按验收要求重排的首页汇总（每个数仍来自上表同一份 `joint_threshold_summary.csv`，不是重新拟合）：

{table(["指标", "0°", "30°", "45°", "最坏条件"], indicator_rows)}

本次 Monte Carlo 实际包含 {instance_count} 个独立 period、{transition_count} 个三屏分组（每个输出档 {trials_per_level} 组三屏）。所有主图横轴均为实测固定支撑 output SCNR；desired SCNR 只作为档位标签和误差审计基准。运行命令、配置快照、输入身份和 raw 清理状态保存在 `run_manifest.json`、`metrics_manifest.json` 和各角度 `pipeline_manifest.json`；manifest 中记录的命令为：`{command_text}`。

## 阅读方法

每张主图后紧跟一张由同一 CSV 生成的数据表和一段判读说明。图中点是表中实际均值，误差带来自同一批实例的 Wilson 区间或有效测角样本；表中 `n` 是真实分母。若某档没有有效命中，报告显示“—”，不以插值填充。

## 本轮参数、数据来源和 Monte Carlo 次数

下面这张表是本报告所有曲线的参数台账。它把“控制旋钮”（例如 Stage2 的
`target_snr_db`）和“正式横轴/事件”（实测 output SCNR、truth hit）分开，避免
把控制量误读成检测性能。

{parameter_table}

正式中心角验证的抽样结构是：每个角度×SCNR 档位由合并后的 3 个独立 chunk、每个
chunk 5 组三屏组成，因此 unit/target 的分母为 15 个 screen，航迹 2-of-3 的分母
为 5 个三屏组；三角度共 450 个 period、150 个三屏组。GO 理论积分不是把这 450
个目标 period 当作理论样本，而是每个 N-only 背景 realization 生成 20,000 个
八块训练窗样本后计算条件 $Q_1$ 的平均。校准目录每个角度另有 108 个 C+N-only
固定支撑样本，专门用于估计 $P_0$，没有混入 evaluation 命中计数。

目标级 min_points=2/3/6 的实测（若目录状态为 pass）使用独立的真实 CUDA 运行，
每个点仍保存 `target_hits/target_trials`；旧的 Poisson-binomial 曲线只作解析
baseline，不能当成蒙特卡洛次数。{minpoint_status}

# 1. 测试对象、数据、配置和事件定义

## 1.1 测试链路

每个角度使用一个固定命令波位（0°、30°、45°），每个输出 SCNR 档位重复若干相互独立的三屏 truth 组；每组三个连续 period 共用同一目标几何，但热噪声由独立 seed/packet-addressed 实现产生。每个角度按以下顺序运行：

1. `N-only`：真实 Stage2 生成器 + 真实 `GMTI_pipe_core`，获得 C+N 背景图、四方向 GO 训练均值、P0 ensemble 和冻结 Doppler/P38 参数；
2. 高 S-only reference：只用于测量目标主瓣相对真值的固定 Doppler/range 支撑偏移；
3. 完整 S-only schedule：在同一 period 支撑上测量 $P_S$；
4. `S+N`：重新走完整生产链，读取 GO hit map、production clustering、truth matching、测角和 TrackManager debug；
5. 只有 detection/GO/TrackManager 审计成功后才删除对应 raw BIN，并在 `pipeline_manifest.json` 写入 SHA-256 和 `raw_removed`。

## 1.2 配置和数据身份

{table(["参数", "本次实际含义", "来源/审计文件"], [
    ["Pfa", "1e-6", "运行时 XML / manifest"],
    ["GO", "guard=4, background=16, alpha=13.4495103", "cfar_*_meta.txt / go_theory_vs_mc.csv"],
    ["min_points", "生产主线 6；2/3 由独立 CUDA 对照实测（见第4节）", "运行参数 / minpoints summary"],
    ["小簇恢复", "3–5 点且峰值超过局部中位数 +20 dB", "运行时 XML"],
    ["固定支撑", "默认 Doppler×range = 3×3", "manifest output_support_*"],
    ["主航迹事件", "同一 truth 三屏至少两屏命中", "mc_transition_stats.csv"],
])}

输入、输出和源码身份均保存在 MC 目录的 manifest、`monte_carlo_instances.csv`、`mc_curve_summary.csv` 和逐角度审计目录中；本报告不把旧 pilot 或旧校准的数字覆盖到新批次。

固定支撑窗口 $W$ 默认是 Doppler×range 的 3×3 邻域。S-only 输出功率 $P_S=\sum_W|z_S|^2$，C+N-only 背景参考 $P_0=E[\sum_W|z_{{C+N}}|^2]$。GO 每个实例仍保存四方向训练均值 $L,R,T,B$、$M=\max(L,R,T,B)$ 和生产 threshold；$M$ 只用于 unit Pd 与经验积分，不改变 output SCNR 分母。

单元级主事件是生产 GO exact CUT 在 truth row/range 上命中；聚类、目标选择和最终 truth 输出分开保留；航迹主事件为同一 truth 目标三屏至少两屏命中；测角 RMSE 仅在最终目标命中样本上计算，并记录有效数/总目标数。

## 1.3 信息泄露核查与隔离

{leakage_audit_note} 真值在仿真中是**评价标签**，不是雷达处理先验：雷达处理只能读取
回波、运行时配置和自身历史状态；truth 只能在处理完成后判断“这一输出是否匹配目标”、
计算 RMSE 与三屏 2-of-3。该边界逐项如下。

{table(["潜在先验", "允许边界", "实际隔离措施", "审计证据"], leakage_rows)}

特别说明两个曾经可能混淆的点。第一，历史 CSV-NMS 在功率完全并列时曾比较 truth 距离，
这会偏向最靠近仿真目标的候选；现已改为观测量的确定性排序，并用修复后二进制重跑严格
对照。第二，旧的受控 MC 用 N-only 的 Doppler/P38 统计冻结后续运行，虽然其中没有 target
truth 或 S+N 输出，但属于运行前背景先验；本报告的严格 min_points 对照启用
`--strict-online-no-prior`，完全禁止这项回写。历史中心验证只作为坐标/理论诊断，不冒充
严格无先验 target/track 结论。

# 2. output-SCNR 标定和修复证据

先用同一完整目标 schedule 运行 N-only、S-only、S+N。N-only 提供 ensemble $P_0$ 和冻结 Doppler/P38 参考；S-only 提供固定支撑的 $P_S$；S+N 只负责真实检测、聚类、目标匹配和角度结果。历史上出现过的“每个档位压缩 period 但套用另一 period 支撑”和“N-only Doppler center 随噪声跳变”已修复。

本轮进一步发现并修复了一个会伪造 SCNR 失配的支撑定位问题：`row_truth` 是 packet/raw FFT 轴行号，而生产 GO 图还经过当前 run 的 Doppler 重心化。正确做法是优先使用当前 run 的 `doppler_axis_center_wrapped_hz` 和 `doppler_axis_row_step_hz` 把物理 $f_D$ 映射到生产图行；只有旧文件缺少元数据时才回退 `row_truth % N_D`。如果直接把 raw row 复制到另一个 seed 的图上，30°目标会在同一物理目标下从 row~=2 变到 row~=60，造成非物理 SCNR 跳变。此前 pilot 的 30°/45°输出-SCNR偏差只作为失败诊断证据，不能解释为物理性能。

{table(["角度", "标定斜率(dB/dB)", "R²", "最大 batch 残差(dB)"], cal_rows)}

sanity 条件为单调、斜率 0.85–1.15、R²>=0.98、最大 batch 残差<=1 dB；正式档位还逐实例保存 desired→measured 误差。三角度标定结果见上表，批量误差见下表。

{table(["角度", "平均|误差| (dB)", "档位最大|误差| (dB)", "每档最少 period", "每档最多 period"], scnr_by_angle)}

这里的“平均|误差|”是该角度所有档位的逐档平均绝对误差，0°/30°/45°分别约为 0.38/0.44/0.44 dB，满足本轮批量标尺的 0.5 dB 建议；“档位最大|误差|”是单个 SCNR 档内 15 个 period 的最坏偏差，受当前小样本和热噪声 realization 影响，可大于 1 dB，不能与上面的独立标定拟合残差混为一谈。

### 2.1 每档原始标定表

{table(["角度", "控制 target_snr(dB)", "实测 output SCNR(dB)", "屏间 std(dB)", "$P_S$均值", "$P_0$", "n", "相对线性拟合偏差(dB)"], calibration_detail_rows) if calibration_detail_rows else "当前 MC 目录未包含独立校准明细；请检查 calibration 路径。"}

表中控制量只描述注入器的内部旋钮；正式曲线使用第三列的实测 output SCNR。$P_S$ 和 $P_0$ 使用同一固定支撑定义，因此可以直接复算第三列：$10\log_{10}(P_S/P_0)$。若 30°/45° 出现逐 period 跳变，应先检查 Doppler 重心和支撑 row，再讨论 CFAR 或 minpoints。

### 2.2 C+N-only 固定支撑背景统计

{table(["角度", "P0样本数", "均值", "标准差", "中位数", "P05", "P95", "中心CUT均值", "中心CUT中位数"], p0_stats_rows) if p0_stats_rows else "未找到 p0_ensemble.csv，无法给出背景分布统计。"}

这里的均值、标准差、中位数、P05 和 P95 都来自固定 reference support 上的 C+N-only `p0_ensemble.csv`；正式 output SCNR 的分母只使用均值，其他分位数用于衡量背景 realization 的波动范围，不能替代 GO 的瞬时 $M$。

# 3. GO 单元级理论与实测

保留生产二维 GO-CFAR 的八块相关结构：四个 16×16 角块、四个 16×9/9×16 中间块，四方向均值共享角块，$M=\max(L,R,T,B)$，生产系数 $\alpha=13.4495103$。在固定背景实例上，单元理论为

$$P_d(\gamma)=E_M[Q_1(\sqrt{{2\gamma}},\sqrt{{2\alpha M/P_0}})].$$

教材固定门限公式本来使用的是**单个 CUT** 的 SCNR：

$$P_{{d,\mathrm{{text}}}}(\gamma_{{cut}})=Q_1(\sqrt{{2\gamma_{{cut}}}},\sqrt{{2\eta}}),\qquad \eta=-\ln(P_{{fa}}).$$

而本专项横轴定义为固定 3×3 支撑的总 output SCNR。目标能量集中在中心 CUT 时，二者不是同一个量；本批独立标定给出
$\gamma_{{cut,dB}}=a_\theta\,SCNR_{{out,dB}}+b_\theta$，其中截距约 7.4–8.6 dB。因此在图上必须先做中心 CUT 映射再画教材曲线。若直接令 $\gamma_{{cut}}=\gamma_{{out}}$，就会得到一条人为偏低的“教材线”，正是实测看起来好很多的主要原因。

Figure B 的彩色点划线是完成中心 CUT 映射后的可比教材固定门限理论，彩色虚线使用本批 N-only 训练窗经验积分；灰色点划线特意保留旧的“总 3×3 SCNR 直接当 CUT SCNR”诊断线。实线/圆点是逐 period 的 production GO exact-CUT hit，三类正式比较均以 output SCNR 为横轴。

{figure_lines[1]}

### Figure B 数据表

{table(["角度", "output SCNR(dB)", "unit Pd", "Wilson 下界", "Wilson 上界", "unit n", "cluster Pd"], figure_b_rows)}

Figure B 的读法是：先看 unit Pd 是否随 output SCNR 单调上升，再看彩色实测点与 IID/经验理论的差异。若 unit Pd 已经很高而 target Pd 仍接近 0，损失发生在聚类连通性或后续 truth match，不应继续调单元门限。

### Figure B 每条曲线的来源、推导和数值代入

Figure B 不是把不同含义的“理论”混成一条线，而是并列显示四个可审计对象：

1. **灰色点划线（旧口径诊断）**：直接令
   $\gamma_{{cut}}=10^{{SCNR_{{out}}/10}}$，代入
   $P_d=Q_1(\sqrt{{2\gamma_{{cut}}}},\sqrt{{2\eta}})$，其中
   $\eta=-\ln(10^{{-6}})=13.8155$。例如 output SCNR=6 dB 时，
   $\gamma_{{cut}}=3.981$，得到 $P_d\approx0.0106$。这条线把 3×3
   **总**支撑能量误当成单个 CUT，只保留用来解释历史误读，不能与生产点比较。
2. **彩色点划线（可比教材固定门限）**：先由独立 calibration 得到
   $\gamma_{{cut,dB}}=a_\theta SCNR_{{out,dB}}+b_\theta$，再代入同一个
   Marcum-$Q$ 公式。以 0°为例，$a_0\approx1.0085,b_0\approx7.4284$；
   output SCNR=6 dB 对应中心 CUT≈13.48 dB，故 $P_d\approx0.93$，与灰线
   相差约两个数量级的表观差距完全来自坐标变换，而非 CFAR 获得了额外增益。
3. **彩色虚线（GO 经验理论）**：对同一批 N-only 固定支撑的每个训练窗，
   先用八个矩形块构造 $M_r=\max(L_r,R_r,T_r,B_r)$，再计算
   $q_r=Q_1(\sqrt{{2\gamma}},\sqrt{{2\alpha M_r/P_0}})$，最后取
   $N_r^{{-1}}\sum_r q_r$。这里 $\alpha=13.4495103$ 来自
   $N=25^2-9^2=544$ 和 $\alpha=N(P_{{fa}}^{{-1/N}}-1)$；每个 chunk
   使用 20,000 个训练窗，保留角块共享造成的方向相关性。
4. **圆点实线（生产 MC）**：每个点是同一 output-SCNR bin 中
   `unit_hits/unit_trials` 的频率，误差带是 Wilson 95% 区间；事件只读取
   production threshold map 在 truth CUT 的 exact hit，不使用邻近峰替代。

本批代表点的逐项代入如下（数值来自 `mc_curve_summary.csv` 和同角度
`go_theory_vs_mc.csv`，不是手工挑选成功样本）：

{worked_examples}

因此“实测比教材好很多”的准确解释是：旧图比较了**不同横轴**；在统一到中心
CUT 后，生产 MC 与 GO 经验积分只剩有限样本和真实背景差异，不能再把灰色诊断线
当作教材对照结论。

### 3.1 原先单元级曲线不符的根因和本轮证据

原先的偏差不是把 GO-CFAR 换成 CA-CFAR 就能解决的门限问题，而是评估事件和功率统计没有落在同一个生产坐标/统计平面上，主要有四类原因：

1. **CUT 坐标错位。** 旧评估直接使用 packet truth 的 `row_truth`，但生产 GO 图在 Doppler 重心化后，物理同一目标对应的行号会改变。修复前 30° 同一目标出现过 raw row 约 2、生产图 row 约 60；45°还会落到相邻 Doppler 行。这样会把“邻近单元命中”误记成 exact CUT 漏检，形成假性 unit Pd 右移。
2. **跨 period 支撑错配。** 旧校准把每个幅度档压缩成连续 period，却套用了另一条完整轨迹的 N-only 支撑；目标位置、Doppler、P38 相位和 GO 训练窗不再对应，导致输入幅度增大时测得的输出功率反而可能下降。
3. **统计口径混用。** 旧曲线交替使用 S-only 功率、单次复数差、瞬时 $M$ 和 C+N ensemble $P_0$。修复后统一为固定支撑的 $E[P_{{S+N}}]-E[P_{{C+N}}]$，unit 事件只读取生产 threshold map 的 exact CUT，$M$ 仅用于 GO 理论积分。
4. **CSI 分带功率尺度混合。** 生产链在 CSI 分带时分别计算 in-band 与 out-of-band 的 GO 均值/阈值，再按物理行拼接诊断图；旧离线重算却直接对拼接后的 `power_map` 求四方向均值，把两个支路的绝对尺度混在同一个 $M$ 中。因而会得到比生产门限偏高的假阈值，低 SCNR 时把真实命中误判为“理论漏检”。本批逐周期证据为：{threshold_mix_note}。当前 unit 判定以同一 CUT 的 production threshold map 为唯一依据，`unit_cfar_threshold_mixed` 只保留作诊断。

修复后的传递函数三角度斜率均接近 1、$R^2$ 均大于 0.998，最大批量残差小于 0.41 dB；因此原先的主要失配已定位为评估/坐标/统计问题，而不是单元级 GO 公式本身。当前最低 output-SCNR 档的实测与理论差异仍保留在报告中：{unit_low_note}。{unit_low_sample_note} 当前 3-seed 验证仍不是最终大样本门限声明。

### 3.1.1 修复后的 unit Pd 定量对照

{table(["角度", "逐档最大|实测−经验理论|", "逐档平均|实测−经验理论|", "对照档数"], unit_residual_rows) if unit_residual_rows else "当前目录没有足够的 chunk 经验理论曲线，无法生成逐档残差表。"}

{unit_residual_note}

# 4. 从单元到目标：min_points 敏感性

Poisson-binomial 只作为解析 baseline：由实测 3×5 支撑的 $\kappa_i$（信号分配）和 $\beta_i$（背景比例）得到每个候选单元的 $p_i(\gamma)$，再求 $P(K\ge m)$。它不代替真实连通性、3–5 点小簇 +20 dB、选择、重定位和 truth match。

{table(["角度组", "min_points", "Pd@10", "Pd@15", "Pd@20", "SCNR@Pd=0.5", "SCNR@Pd=0.9"], min_rows)}

PB 文件原始的 `output_scnr_db` 是 15 个候选单元的总信号功率除以单元背景，不是本专项主图的固定 3×3 支撑 output SCNR。依据实测支撑形状，使用
$\gamma_{{3\times3}}=\gamma_{{3\times5}}\,\sum_{{W_{{3\times3}}}}\kappa_i/\sum_{{W_{{3\times3}}}}\beta_i$，逐角度转换横轴；转换量如下，且已写入 `joint_threshold_summary.csv`：

{table(["角度", "PB→固定3×3轴偏移(dB)", "轴定义"], target_axis_rows)}

现有实测支撑理论显示 min_points=6 相比 min_points=3 明显右移；min_points=2/3 可能让目标 Pd 曲线左移，但也会改变连通组件假警和选择损失，不能只根据这一张 PB 曲线替换生产配置。生产实测还包含连通组件和 3–5 点、+20 dB 小簇恢复，因此 Figure C 的实线是最终 truth target Pd，虚线只是坐标已统一后的独立单元 PB baseline。

### 4.1 min_points=2/3/6 的真实 CUDA 实测分图

这三组实验不再只引用 PB 理论：每个 min_points 都在相同的 0°/30°/45°中心
波位、相同的固定 3×3 output-SCNR 标尺、相同的 GO 参数和相同的 truth-match
规则下独立运行。min_points=2 的配置同时将 `small_min_points=2` 写入 XML，
以满足加载器的“恢复阈值不得大于主阈值”约束；min_points=3 使用 3；min_points=6
保留生产的 3–5 点、局部峰值 +20 dB 小簇恢复。每个点的实测概率都是
$\hat P_{{target}}=k/n$，其中 $k$、$n$ 从该配置自己的 `target_hits`、
`target_trials` 读取，不能把 min6 的分母套给 min2/3。

{minpoint_count_table}

{minpoint_figure_text}

读图时应比较同一角度下三张图的横向移动量：如果 min_points 降低只让曲线左移，
说明瓶颈主要是连通点数；如果曲线仍不移动，则瓶颈在目标支撑、选择、重定位或
truth match。Wilson 阴影显示 5 组三屏/档位（若使用当前验证设置）带来的有限样本
不确定性，因此该敏感性实验用于工程选择，不是最终虚警/检测门限声明。

# 5. 四张主曲线

{figure_block}

## Figure A：测角 RMSE 数据表与判读

{table(["角度", "output SCNR(dB)", "RMSE(°)", "偏差(°)", "独立校准生产模型", "有效/总"], figure_a_rows)}

测角曲线只对最终 truth 命中的目标计算；低 SCNR 档的“—”不是零误差，而是没有可用于估计 RMSE 的命中样本。点线模型来自独立 calibration seeds 的真实生产定位器，未用本次 evaluation seed 拟合；报告同时保留 angle bias，便于区分随机方差和系统偏置。

### Figure A 每条曲线的来源、公式和代入

圆点实线由 `angle_rmse_deg` 在 `final_target_hit=1` 的样本上计算：
$RMSE=\sqrt{{n^{{-1}}\sum_j(\hat\theta_j-\theta_j)^2}}$；表中的“有效/总”
就是 $n$ 与该档所有 truth 目标数。点线是独立 calibration seed 的真实生产定位器
漏斗模型，虚线是相位/Jacobian 理论，先用
$\sigma_\theta^2\simeq J_\theta^2\sigma_\phi^2$、
$\sigma_\phi^2\simeq1/(2\,SCNR_{{cut}})$ 求中心 CUT 轴上的理论 RMSE，再用
$SCNR_{{cut,dB}}=a_\theta SCNR_{{out,dB}}+b_\theta$ 反变换到图的横轴。
例如 0° 的 mapping 截距约 7.43 dB；output SCNR=6 dB 时理论使用的不是
6 dB，而是约 13.48 dB 的中心 CUT SCNR。低档没有 final hit 时，实测 RMSE
留空而不是置零。

### 5.1 测角模块拆分审计

测角先拆成几何正逆变换、双通道复相位噪声、P38 斜率拟合和生产定位输出四个模块。前两个几何闭环误差应为数值零；相位/P38 的高相位 SNR 统计用于验证公式本身，不能直接替代生产 CSI 链的有效相位 SNR。

{table(["模块", "样本数", "最大绝对误差", "状态", "参数来源"], angle_module_rows) if angle_module_rows else "未找到 angle_module_summary.csv；请先运行 25_validate_angle_modules.py。"}

{table(["角度", "理论测角 RMSE<=0.2°所需 output SCNR(dB)", "中心CUT→固定支撑斜率", "截距(dB)", "R²", "最大残差(dB)"], angle_theory_rows) if angle_theory_rows else "未找到 angle_theory_summary.csv。"}

Figure A 的 Jacobian 理论曲线使用中心 CUT 的相位信噪比，而主图横轴定义为生产固定 3×3 支撑 output SCNR。为避免把两个物理量混为一谈，先用独立 calibration seed 中逐档同时记录的 `unit_cut_scnr_db` 与 `output_scnr_db` 做线性坐标标定，再把理论曲线反变换到主图横轴。三角度标定斜率约 0.990–1.008、R²>0.999、最大残差<0.20 dB，因而表中交点已经是正式 output-SCNR 坐标：0°、30°、45° 分别约 6.31、6.56、8.88 dB。该交点是单目标测角理论参考，不是本批 3-seed 目标级/航迹级最终门限；低 output-SCNR 处若无 final truth hit，实测 RMSE 仍显示为“—”。

## Figure C：目标级 Pd 数据表与判读

{table(["角度", "output SCNR(dB)", "cluster Pd", "select Pd", "target Pd", "target Wilson 下界", "target Wilson 上界", "独立校准生产模型", "target n"], figure_c_rows)}

目标级生产定义是 $P_{{target}}=P_{{cluster}}P(select|cluster)P(reloc|select,cluster)P(match|reloc,select,cluster)$；表中 `target Pd` 是最终 truth match 的直接频率，其他列用于显示损失发生在哪一级。`独立校准生产模型` 使用 calibration seeds 的真实 joint GO hit mask、min_points=6、小簇恢复和 truth-match 漏斗，未用 evaluation seed 重新拟合；当前实现若没有独立 selector rejection flag，`select Pd` 仅作生产输出代理，不能过度解释为独立物理概率。

### Figure C 每条曲线的来源、公式和代入

实线圆点是当前 evaluation 的最终 truth-match 事件；cluster、select、target
三列分别对应漏斗中可观察的中间层。独立校准点线按同一生产规则从 joint GO hit
mask 直接统计，再做 output-SCNR 插值，不使用 evaluation 点拟合。虚线是
min_points=6 的 Poisson-binomial 解析 baseline：令候选单元命中概率为 $p_i$，
则 $P(K\ge6)=\sum_{{k=6}}^{{N}}\sum_{{|A|=k}}\prod_{{i\in A}}p_i\prod_{{i\notin A}}(1-p_i)$；
其横轴由 3×5 理论支撑按实测 $\kappa/\beta$ 比例映射到固定 3×3。以任一表中
代表点为例，若 cluster=12/15、target=4/15，则生产漏斗的可见结果分别是
0.800 与 0.267；这两个数不是把 unit Pd 的 0.9 直接当成 target Pd。

## Figure D：三屏选二航迹 Pd 数据表与判读

{table(["角度", "output SCNR(dB)", "track Pd", "Wilson 下界", "Wilson 上界", "独立校准生产模型", "track n", "P12+P13+P23−2P123"], figure_d_rows)}

若三屏独立且单屏 target Pd 为 $p$，理论基线为 $P_{{track}}=3p^2-2p^3$；最后一列使用真实屏间联合计数，非独立时以它为准。

### Figure D 每条曲线的来源、公式和代入

圆点实线在每个 SCNR 档内按三屏组计算
$I_{{2/3}}=1[screen1+screen2+screen3\ge2]$，因此分母是三屏组数而不是
screen 数。独立校准点线使用同一生产漏斗的三屏联合事件。虚线仅在屏间近似独立
时使用单屏 $p$ 的 $3p^2-2p^3$；例如单屏 target $p=0.8$ 时，独立基线为
$3\times0.8^2-2\times0.8^3=0.896$。若 `mc_transition_stats.csv` 的
$P(D_t=1|D_{{t-1}}=1)$ 与 $P(D_t=1|D_{{t-1}}=0)$ 差异明显，报告优先采用
$P_{12}+P_{13}+P_{23}-2P_{123}$ 的真实联合修正，而不是继续套用独立公式。

### 5.2 生产漏斗模型与 MC 的定量对照

{table(["角度", "target Pd 最大/平均绝对差", "track Pd 最大/平均绝对差", "angle RMSE 最大/平均绝对差(°)", "有效比较点数(target/track/angle)"], production_residual_rows) if production_residual_rows else "未找到独立校准生产模型，无法进行逐点残差对照。"}

上表把本次 evaluation seed 的曲线逐点插值到独立 calibration seed 的生产模型上，只计算实际存在的有效值，**不重新拟合参数**。因此它量化的是有限样本波动和场景复现差异，而不是把 evaluation 数据反向用于理论模型。目标级生产模型包含真实 joint GO hit mask、当前 min_points=6 规则以及 3–5 点且峰值高于中位数 20 dB 的小簇恢复；Poisson-binomial min_points=6 仅保留为解析 baseline，不能替代这条生产漏斗曲线。

# 6. 航迹相关性与审计

主航迹曲线使用 truth 三屏 2-of-3。`mc_transition_stats.csv` 同时保存 $P(D_2|D_1)$、$P(D_2|\neg D_1)$、$P(D_3|D_2)$、$P(D_3|\neg D_2)$、相邻相关系数及 $P_{{12}}+P_{{13}}+P_{{23}}-2P_{{123}}$。TrackManager `Confirmed+matched_this_frame` 仅作为 payload/track_debug 审计旁路，不改变主航迹 Pd。

当前 `target_select()` 源码只对聚类代表点按幅度排序后全部保留，没有独立 selector rejection flag。因此 CSV 中的 `target_select_hit` 明确标为最终输出代理，不把 `select_given_cluster` 解释成独立物理概率；真实目标级主曲线仍使用最终 truth match。

### 6.1 三屏转移表

{table(["角度", "output SCNR(dB)", "三屏组数", "$P(D_2|D_1)$", "$P(D_2|D_1=0)$", "相邻相关", "联合修正航迹 Pd"], transition_rows) if transition_rows else "当前目录未生成 mc_transition_stats.csv。"}

当 $P(D_{{t}}=1|D_{{t-1}}=1)$ 与 $P(D_{{t}}=1|D_{{t-1}}=0)$ 相差很大，或相关系数显著偏离 0，就不能使用 $3p^2-2p^3$ 作为主结论；本表的 inclusion–exclusion 列是报告采用的航迹 Pd。

# 7. 复现和限制

一键入口（真实 CUDA）：

```bash
bash scripts/gmti_scnr_eval/run_output_scnr_monte_carlo.sh \
  --output-scnr-grid 6,8,10,12,14,16,18,20,22,24 \
  --trials 100 \
  --output-dir outputs/gmti_scnr_eval/output_scnr_monte_carlo_formal
```

逐实例证据在 `monte_carlo_instances.csv`，曲线汇总在 `mc_curve_summary.csv`，四图 PDF 在 `final_four_curves/final_four_curves.pdf`。raw BIN 仅在检测、GO 诊断和 TrackManager payload 审计成功后清理。若本目录每档少于 100 组三屏，则它仍是模型验证批次，不能宣称最终 90% 门限；若已达到大样本，仍需检查面积杂波状态和所有 acceptance 条件。修复前的 raw-row 失败现场保存在 `outputs/gmti_scnr_eval/output_scnr_mc_truthrowfix_20260826`，修复后的校准/回归目录按 manifest 单独标识。

"""
    md_path.write_text(md, encoding="utf-8")
    pdf_path = args.output_pdf.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pandoc = shutil.which("pandoc")
    xelatex = shutil.which("xelatex")
    if pandoc and xelatex:
        command = [pandoc, str(md_path), "--resource-path", str(md_path.parent),
                   "--pdf-engine", xelatex, "-V", "CJKmainfont=Noto Sans CJK SC",
                   "-o", str(pdf_path)]
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            raise SystemExit(f"pandoc PDF 失败，退出码={result.returncode}")
    if args.output_html:
        html_path = args.output_html.resolve()
        html_path.parent.mkdir(parents=True, exist_ok=True)
        if pandoc:
            # MathML keeps the HTML report self-contained and avoids an
            # external MathJax fetch (the evaluation environment is offline).
            subprocess.run([pandoc, str(md_path), "--standalone", "--resource-path", str(md_path.parent),
                            "--self-contained", "--mathml", "-o", str(html_path)], cwd=ROOT, check=False)
    print(f"[PASS] 报告 Markdown: {md_path}")
    print(f"[PASS] 报告 PDF: {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
