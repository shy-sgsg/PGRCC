#!/usr/bin/env python3
"""Aggregate multi-seed weak-target sweeps and derive measured boundaries."""

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


def read_csv(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def number(value, default=math.nan):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def integer(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def finite_median(values):
    values = [value for value in values if math.isfinite(value)]
    return statistics.median(values) if values else math.nan


def finite_mean(values):
    values = [value for value in values if math.isfinite(value)]
    return statistics.mean(values) if values else math.nan


def finite_std(values):
    values = [value for value in values if math.isfinite(value)]
    return statistics.stdev(values) if len(values) > 1 else (0.0 if values else math.nan)


def finite_quantile(values, probability):
    values = sorted(value for value in values if math.isfinite(value))
    if not values:
        return math.nan
    if len(values) == 1:
        return values[0]
    position = probability * (len(values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def latest_tap_summary(output_root, case_id):
    paths = sorted(
        (output_root / "algorithm_outputs" / case_id / "csi_metrics").glob(
            "*/csi_metric_tap_summary.csv"),
        key=lambda path: path.stat().st_mtime,
    )
    if not paths:
        return {}
    rows = read_csv(paths[-1])
    return rows[0] if rows else {}


def fmt(value, digits=4):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "not_resolved"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def json_safe(value):
    """Represent unavailable numeric evidence as JSON null, never NaN."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sustained_boundary(rows, pass_field):
    ordered = sorted(rows, key=lambda row: row["sweep_value"])
    first = next((row for row in ordered if row[pass_field]), None)
    sustained = None
    for index, row in enumerate(ordered):
        if row[pass_field] and all(later[pass_field] for later in ordered[index:]):
            sustained = row
            break
    previous_fail = None
    if first is not None:
        for row in ordered:
            if row["sweep_value"] >= first["sweep_value"]:
                break
            if not row[pass_field]:
                previous_fail = row
    if sustained is None:
        status = "not_reached"
    elif previous_fail is None:
        status = "below_or_equal_tested_floor"
    else:
        status = "bracketed"
    return {
        "first_pass": first,
        "sustained_pass": sustained,
        "previous_fail": previous_fail,
        "status": status,
    }


def make_plots(report_dir, aggregate):
    import os
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti-matplotlib")
    import matplotlib.pyplot as plt

    for axis, xlabel, stem in [
        ("injected_snr_db", "Injected SNR (dB)", "weak_target_snr_boundary"),
        ("radial_velocity_mps", "Truth radial velocity (m/s)", "weak_target_velocity_boundary"),
    ]:
        rows = sorted((row for row in aggregate if row["sweep_axis"] == axis),
                      key=lambda row: row["sweep_value"])
        if not rows:
            continue
        x = [row["sweep_value"] for row in rows]
        pd = [row["pd"] for row in rows]
        fa = [row["mean_incremental_false_alarms"] for row in rows]
        fig, left = plt.subplots(figsize=(8.5, 4.6))
        right = left.twinx()
        left.plot(x, pd, marker="o", color="#1565c0", label="Pd")
        right.plot(x, fa, marker="s", color="#c62828",
                   label="incremental false alarms")
        left.axhline(0.9, color="#1565c0", linestyle="--", alpha=0.5)
        right.axhline(1.0, color="#c62828", linestyle="--", alpha=0.5)
        left.set_xlabel(xlabel)
        left.set_ylabel("Detection probability", color="#1565c0")
        right.set_ylabel("Mean incremental false alarms / beam", color="#c62828")
        left.set_ylim(-0.03, 1.05)
        left.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig(report_dir / f"{stem}.png", dpi=160)
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--plots", action="store_true")
    args = parser.parse_args()

    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    output_root = args.output_root or Path(matrix["output_root"])
    report_dir = args.report_dir or output_root / "metrics" / "weak_target_limits"
    report_dir.mkdir(parents=True, exist_ok=True)
    design = matrix.get("weak_target_design", {})
    acceptance = design.get("coarse_acceptance", {})
    minimum_pd = float(acceptance.get("minimum_pd", 0.9))
    maximum_position_error = float(
        acceptance.get("maximum_median_position_error_m", 10.0))
    maximum_incremental_fa = float(
        acceptance.get("maximum_incremental_false_alarms_per_beam", 1.0))

    background_fa = {}
    background_quality = []
    for case in matrix["cases"]:
        if case.get("sweep_axis") != "background":
            continue
        rows = read_csv(output_root / "metrics" / case["case_id"] /
                        "p4_truth_eval" / "detection_metrics.csv")
        if rows:
            background_fa[int(case["random_seed"])] = integer(
                rows[0].get("false_alarm_count"), 0)
        cancellation = read_csv(
            output_root / "metrics" / case["case_id"] / "p5_csi" /
            "cancellation_metrics.csv")
        tap = latest_tap_summary(output_root, case["case_id"])
        background_quality.append({
            "case_id": case["case_id"],
            "random_seed": int(case["random_seed"]),
            "false_alarm_count": background_fa.get(
                int(case["random_seed"]), 0),
            "CA_ROI_dB": number(cancellation[0].get("CA_ROI_dB"))
                if cancellation else math.nan,
            "CA_full_support_dB": number(tap.get("CA_ROI_dB")),
            "phase_model_weighted_coherence": number(
                tap.get("phase_model_weighted_coherence")),
            "phase_model_weighted_circular_rmse_rad": number(
                tap.get("phase_model_weighted_circular_rmse_rad")),
        })

    case_rows = []
    for case in matrix["cases"]:
        axis = case.get("sweep_axis")
        if axis not in {"radial_velocity_mps", "injected_snr_db"}:
            continue
        case_id = case["case_id"]
        seed = int(case["random_seed"])
        detection_rows = read_csv(
            output_root / "metrics" / case_id / "p4_truth_eval" /
            "detection_metrics.csv")
        scnr_rows = read_csv(
            output_root / "metrics" / case_id / "p5_csi" /
            "scnr_improvement_metrics.csv")
        if not detection_rows:
            case_rows.append({
                "case_id": case_id,
                "random_seed": seed,
                "sweep_axis": axis,
                "sweep_value": float(case["sweep_value"]),
                "status": "missing_detection_metrics",
            })
            continue
        detection = detection_rows[0]
        scnr = scnr_rows[0] if scnr_rows else {}
        false_alarms = integer(detection.get("false_alarm_count"), 0)
        matched = integer(detection.get("matched_count"), 0)
        row = {
            "case_id": case_id,
            "random_seed": seed,
            "sweep_axis": axis,
            "sweep_value": float(case["sweep_value"]),
            "injected_snr_db": number(case.get("injected_snr_db")),
            "truth_radial_velocity_mps": number(
                case.get("truth_radial_velocity_mps")),
            "truth_count": integer(detection.get("truth_count"), 0),
            "detection_count": integer(detection.get("detection_count"), 0),
            "matched_count": matched,
            "detected": int(matched > 0),
            "missed_count": integer(detection.get("missed_truth_count"), 0),
            "false_alarm_count": false_alarms,
            "background_false_alarm_count": background_fa.get(seed, 0),
            "incremental_false_alarm_count": max(
                0, false_alarms - background_fa.get(seed, 0)),
            "position_error_m": number(detection.get("mean_position_error_m")),
            "SCNR_before_dB": number(scnr.get("SCNR_before_dB")),
            "SCNR_after_dB": number(scnr.get("SCNR_after_dB")),
            "SCNR_improvement_dB": number(scnr.get("SCNR_improvement_dB")),
            "target_loss_dB": number(scnr.get("target_loss_dB")),
            "scnr_estimation_method": scnr.get("estimation_method", "missing"),
            "status": "ok",
        }
        case_rows.append(row)

    groups = defaultdict(list)
    for row in case_rows:
        if row.get("status") == "ok":
            groups[(row["sweep_axis"], row["sweep_value"])].append(row)
    aggregate = []
    for (axis, value), rows in sorted(groups.items()):
        matched = sum(row["detected"] for row in rows)
        count = len(rows)
        position = finite_median(row["position_error_m"] for row in rows)
        incremental_fa = finite_mean(
            row["incremental_false_alarm_count"] for row in rows)
        pd = matched / count if count else math.nan
        pd_pass = count > 0 and pd >= minimum_pd
        localization_pass = (
            math.isfinite(position) and position <= maximum_position_error)
        # Retain the historical combined field for existing consumers, while
        # exposing the detection-only and localization checks separately.
        detection_pass = pd_pass and localization_pass
        false_alarm_pass = incremental_fa <= maximum_incremental_fa
        engineering_pass = detection_pass and false_alarm_pass
        position_values = [row["position_error_m"] for row in rows]
        aggregate.append({
            "sweep_axis": axis,
            "sweep_value": value,
            "seed_count": count,
            "matched_seed_count": matched,
            "pd": pd,
            "miss_rate": 1.0 - pd,
            "mean_false_alarms": finite_mean(row["false_alarm_count"] for row in rows),
            "mean_background_false_alarms": finite_mean(
                row["background_false_alarm_count"] for row in rows),
            "mean_incremental_false_alarms": incremental_fa,
            "median_position_error_m": position,
            "mean_position_error_m": finite_mean(position_values),
            "std_position_error_m": finite_std(position_values),
            "p90_position_error_m": finite_quantile(position_values, 0.90),
            "p95_position_error_m": finite_quantile(position_values, 0.95),
            "minimum_position_error_m": finite_quantile(position_values, 0.0),
            "maximum_position_error_m": finite_quantile(position_values, 1.0),
            "valid_position_count": sum(
                math.isfinite(value) for value in position_values),
            "failure_count": count - matched,
            "median_SCNR_before_dB": finite_median(
                row["SCNR_before_dB"] for row in rows),
            "median_SCNR_after_dB": finite_median(
                row["SCNR_after_dB"] for row in rows),
            "median_SCNR_improvement_dB": finite_median(
                row["SCNR_improvement_dB"] for row in rows),
            "median_target_loss_dB": finite_median(
                row["target_loss_dB"] for row in rows),
            "pd_pass": pd_pass,
            "localization_pass": localization_pass,
            "false_alarm_pass": false_alarm_pass,
            "detection_pass": detection_pass,
            "engineering_pass": engineering_pass,
        })

    snr_rows = [row for row in aggregate if row["sweep_axis"] == "injected_snr_db"]
    velocity_rows = [row for row in aggregate if row["sweep_axis"] == "radial_velocity_mps"]
    positive = [row for row in velocity_rows if row["sweep_value"] > 0.0]
    negative = [dict(row, sweep_value=abs(row["sweep_value"]))
                for row in velocity_rows if row["sweep_value"] < 0.0]
    snr_detection = sustained_boundary(snr_rows, "detection_pass")
    snr_engineering = sustained_boundary(snr_rows, "engineering_pass")
    snr_pd = sustained_boundary(snr_rows, "pd_pass")
    pos_detection = sustained_boundary(positive, "detection_pass")
    neg_detection = sustained_boundary(negative, "detection_pass")
    pos_engineering = sustained_boundary(positive, "engineering_pass")
    neg_engineering = sustained_boundary(negative, "engineering_pass")
    pos_pd = sustained_boundary(positive, "pd_pass")
    neg_pd = sustained_boundary(negative, "pd_pass")

    def tested_floor(boundary, key="sustained_pass"):
        row = boundary.get(key)
        return row["sweep_value"] if row else None

    def resolved_boundary_value(boundary):
        return tested_floor(boundary) if boundary.get("status") == "bracketed" else None

    def conservative(left, right):
        values = [value for value in [left, right] if value is not None]
        return max(values) if len(values) == 2 else None

    snr_boundary_row = snr_pd.get("sustained_pass")
    ca_roi_values = [row["CA_ROI_dB"] for row in background_quality]
    ca_full_values = [row["CA_full_support_dB"] for row in background_quality]
    coherence_values = [row["phase_model_weighted_coherence"]
                        for row in background_quality]
    phase_rmse_values = [row["phase_model_weighted_circular_rmse_rad"]
                         for row in background_quality]
    lowest_snr_row = snr_pd.get("sustained_pass")
    suppression_demonstrated = (
        bool(background_quality) and
        all(math.isfinite(value) and value > 0.0 for value in ca_roi_values) and
        lowest_snr_row is not None and
        lowest_snr_row["pd_pass"] and
        lowest_snr_row["false_alarm_pass"] and
        math.isfinite(lowest_snr_row["median_SCNR_improvement_dB"]) and
        lowest_snr_row["median_SCNR_improvement_dB"] >= 0.0)
    summary = {
        "experiment_id": matrix.get("experiment_id", ""),
        "matrix": str(args.matrix),
        "output_root": str(output_root),
        "criteria": {
            "minimum_pd": minimum_pd,
            "maximum_median_position_error_m": maximum_position_error,
            "maximum_incremental_false_alarms_per_beam": maximum_incremental_fa,
            "minimum_definition":
                "lowest grid point that passes and all higher tested points also pass",
        },
        "minimum_detectable_injected_snr_db": resolved_boundary_value(snr_pd),
        "minimum_detectable_injected_snr_status": snr_pd["status"],
        "lowest_stably_detected_tested_injected_snr_db": tested_floor(snr_pd),
        "minimum_detectable_and_localizable_injected_snr_db":
            resolved_boundary_value(snr_detection),
        "minimum_detectable_and_localizable_injected_snr_status":
            snr_detection["status"],
        "minimum_engineering_injected_snr_db":
            resolved_boundary_value(snr_engineering),
        "minimum_detectable_measured_SCNR_before_dB": (
            snr_boundary_row["median_SCNR_before_dB"]
            if snr_boundary_row and snr_pd["status"] == "bracketed" else None),
        "minimum_detectable_measured_SCNR_after_dB": (
            snr_boundary_row["median_SCNR_after_dB"]
            if snr_boundary_row and snr_pd["status"] == "bracketed" else None),
        "lowest_stably_detected_tested_measured_SCNR_before_dB": (
            snr_boundary_row["median_SCNR_before_dB"] if snr_boundary_row else None),
        "lowest_stably_detected_tested_measured_SCNR_after_dB": (
            snr_boundary_row["median_SCNR_after_dB"] if snr_boundary_row else None),
        "minimum_detectable_positive_radial_speed_mps": resolved_boundary_value(pos_pd),
        "minimum_detectable_negative_radial_speed_magnitude_mps": resolved_boundary_value(neg_pd),
        "minimum_detectable_radial_speed_magnitude_conservative_mps": conservative(
            resolved_boundary_value(pos_pd), resolved_boundary_value(neg_pd)),
        "minimum_localizable_positive_radial_speed_mps":
            resolved_boundary_value(pos_detection),
        "minimum_localizable_negative_radial_speed_magnitude_mps":
            resolved_boundary_value(neg_detection),
        "minimum_localizable_radial_speed_magnitude_conservative_mps": conservative(
            resolved_boundary_value(pos_detection),
            resolved_boundary_value(neg_detection)),
        "minimum_engineering_positive_radial_speed_mps": resolved_boundary_value(pos_engineering),
        "minimum_engineering_negative_radial_speed_magnitude_mps": resolved_boundary_value(neg_engineering),
        "minimum_engineering_radial_speed_magnitude_conservative_mps": conservative(
            resolved_boundary_value(pos_engineering), resolved_boundary_value(neg_engineering)),
        "static_zero_speed": next((row for row in velocity_rows
                                   if row["sweep_value"] == 0.0), None),
        "p5_background_suppression": {
            "seed_count": len(background_quality),
            "CA_ROI_dB_mean": finite_mean(ca_roi_values),
            "CA_ROI_dB_std": finite_std(ca_roi_values),
            "CA_ROI_dB_min": finite_quantile(ca_roi_values, 0.0),
            "CA_ROI_dB_max": finite_quantile(ca_roi_values, 1.0),
            "CA_full_support_dB_mean": finite_mean(ca_full_values),
            "CA_full_support_dB_min": finite_quantile(ca_full_values, 0.0),
            "CA_full_support_dB_max": finite_quantile(ca_full_values, 1.0),
            "phase_model_weighted_coherence_mean": finite_mean(coherence_values),
            "phase_model_weighted_circular_rmse_rad_mean": finite_mean(
                phase_rmse_values),
            "background_false_alarm_mean": finite_mean(
                row["false_alarm_count"] for row in background_quality),
            "assessment": (
                "effective_on_tested_stage2_area_clutter"
                if suppression_demonstrated else
                "not_demonstrated_on_tested_stage2_area_clutter"),
            "assessment_rule": (
                "all background seeds have positive ROI CA; the lowest stable "
                "weak-target point retains Pd, non-negative median SCNR "
                "improvement, and bounded incremental false alarms"),
        },
        "coarse_grid_only": True,
        "requires_boundary_refinement": True,
    }

    case_fields = [
        "case_id", "random_seed", "sweep_axis", "sweep_value",
        "injected_snr_db", "truth_radial_velocity_mps", "truth_count",
        "detection_count", "matched_count", "detected", "missed_count",
        "false_alarm_count", "background_false_alarm_count",
        "incremental_false_alarm_count", "position_error_m", "SCNR_before_dB",
        "SCNR_after_dB", "SCNR_improvement_dB", "target_loss_dB",
        "scnr_estimation_method", "status",
    ]
    aggregate_fields = [
        "sweep_axis", "sweep_value", "seed_count", "matched_seed_count", "pd",
        "miss_rate", "mean_false_alarms", "mean_background_false_alarms",
        "mean_incremental_false_alarms", "median_position_error_m",
        "mean_position_error_m", "std_position_error_m",
        "p90_position_error_m", "p95_position_error_m",
        "minimum_position_error_m", "maximum_position_error_m",
        "valid_position_count", "failure_count",
        "median_SCNR_before_dB", "median_SCNR_after_dB",
        "median_SCNR_improvement_dB", "median_target_loss_dB",
        "pd_pass", "localization_pass", "false_alarm_pass",
        "detection_pass", "engineering_pass",
    ]
    write_csv(report_dir / "weak_target_case_metrics.csv", case_rows, case_fields)
    write_csv(report_dir / "weak_target_detection_metrics.csv", aggregate,
              aggregate_fields)
    write_csv(
        report_dir / "p5_background_suppression_metrics.csv",
        background_quality,
        ["case_id", "random_seed", "false_alarm_count", "CA_ROI_dB",
         "CA_full_support_dB", "phase_model_weighted_coherence",
         "phase_model_weighted_circular_rmse_rad"],
    )
    safe_summary = json_safe(summary)
    (report_dir / "weak_target_limit_summary.json").write_text(
        json.dumps(safe_summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")

    lines = [
        "# 微弱目标检测边界评测",
        "",
        "本结果来自 Stage2 新协议回波和生产版 `GMTI_core` CUDA 链路，"
        "每个网格点使用多个固定随机种子。",
        "",
        "## 验收定义",
        "",
        f"- “可检”要求 Pd 不低于 {minimum_pd:g}。",
        f"- “可定位”另要求命中样本定位误差中位数不超过 {maximum_position_error:g} m。",
        f"- “工程可用”另要求每波位增量虚警不超过 {maximum_incremental_fa:g}。",
        "- 边界取“当前点及所有更高测试点均通过”的最低网格点，"
        "避免单次偶然命中。",
        "",
        "## 结果",
        "",
        f"- 最小可检注入 SNR：{fmt(summary['minimum_detectable_injected_snr_db'])} dB；"
        f"状态 {summary['minimum_detectable_injected_snr_status']}。",
        f"- 当前已验证的最低稳定命中点："
        f"{fmt(summary['lowest_stably_detected_tested_injected_snr_db'])} dB。",
        f"- 当前已验证最低稳定命中点的 CSI 前 SCNR："
        f"{fmt(summary['lowest_stably_detected_tested_measured_SCNR_before_dB'])} dB。",
        f"- 当前已验证最低稳定命中点的 CSI 后 SCNR："
        f"{fmt(summary['lowest_stably_detected_tested_measured_SCNR_after_dB'])} dB。",
        f"- 正径向最小可检速度：{fmt(summary['minimum_detectable_positive_radial_speed_mps'])} m/s。",
        f"- 负径向最小可检速度幅值：{fmt(summary['minimum_detectable_negative_radial_speed_magnitude_mps'])} m/s。",
        f"- 保守双向最小可检速度幅值：{fmt(summary['minimum_detectable_radial_speed_magnitude_conservative_mps'])} m/s。",
        f"- 保守双向最小可定位速度幅值："
        f"{fmt(summary['minimum_localizable_radial_speed_magnitude_conservative_mps'])} m/s。",
        "",
        "",
        "## P5 杂波抑制判读",
        "",
        f"- 背景 ROI CA："
        f"{fmt(summary['p5_background_suppression']['CA_ROI_dB_mean'])} ± "
        f"{fmt(summary['p5_background_suppression']['CA_ROI_dB_std'])} dB，"
        f"范围 {fmt(summary['p5_background_suppression']['CA_ROI_dB_min'])}–"
        f"{fmt(summary['p5_background_suppression']['CA_ROI_dB_max'])} dB。",
        f"- 全动态支撑域 CA 均值："
        f"{fmt(summary['p5_background_suppression']['CA_full_support_dB_mean'])} dB。",
        f"- 弱目标最低已测档位的 SCNR 改善中位数："
        f"{fmt(lowest_snr_row['median_SCNR_improvement_dB'] if lowest_snr_row else None)} dB。",
        f"- 综合判读："
        f"`{summary['p5_background_suppression']['assessment']}`。",
        "- 本判读不使用目标强度硬阈值删除 detection；"
        "虚警以 target-off 绝对值和同种子增量值同时保留。",
        "",
        "当前 SNR 扫描在最低测试点仍通过，因此真正最低 SCNR "
        "只能报告为低于或等于已测下界；必须在 CUDA 额度恢复后向下扩展网格才能给出失败/通过夹逼边界。",
        "",
        "## 产出",
        "",
        "- `weak_target_case_metrics.csv`：逐 case/种子结果。",
        "- `weak_target_detection_metrics.csv`：逐速度或 SNR 的多种子汇总。",
        "- `p5_background_suppression_metrics.csv`：逐背景种子 CA、相干性和绝对虚警。",
        "- `weak_target_limit_summary.json`：机器可读边界。",
    ]
    (report_dir / "weak_target_detection_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    if args.plots:
        make_plots(report_dir, aggregate)
    print(json.dumps(safe_summary, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
