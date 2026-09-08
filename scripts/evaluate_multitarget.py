#!/usr/bin/env python3
"""Aggregate dense multi-target production detections and one-to-one truth matches."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import statistics
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from gmti_eval_io import read_csv_rows, read_json, to_float  # noqa: E402
from gmti_eval_match import candidate_errors, gate_pass  # noqa: E402
from run_p4_truth_eval import visible_truths  # noqa: E402


def write_csv(path, rows, fields=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0]) if rows else ["case_id"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def finite(values):
    return [float(value) for value in values
            if value not in (None, "") and math.isfinite(float(value))]


def percentile(values, q):
    values = sorted(finite(values))
    if not values:
        return math.nan
    index = (len(values) - 1) * q
    lo = int(math.floor(index))
    hi = int(math.ceil(index))
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - index) + values[hi] * (index - lo)


def value_stats(values, prefix):
    values = finite(values)
    if not values:
        return {
            f"{prefix}_mean": math.nan,
            f"{prefix}_std": math.nan,
            f"{prefix}_median": math.nan,
            f"{prefix}_p90": math.nan,
            f"{prefix}_p95": math.nan,
            f"{prefix}_min": math.nan,
            f"{prefix}_max": math.nan,
        }
    return {
        f"{prefix}_mean": statistics.fmean(values),
        f"{prefix}_std": statistics.stdev(values) if len(values) > 1 else 0.0,
        f"{prefix}_median": statistics.median(values),
        f"{prefix}_p90": percentile(values, 0.90),
        f"{prefix}_p95": percentile(values, 0.95),
        f"{prefix}_min": min(values),
        f"{prefix}_max": max(values),
    }


def aggregate_group(rows, target_rows, dimension, value):
    case_ids = {row["case_id"] for row in rows}
    positions = [row.get("position_error_m", math.nan)
                 for row in target_rows
                 if row["case_id"] in case_ids and int(row["matched"])]
    truth_total = sum(int(row["visible_truth_count"]) for row in rows)
    detection_total = sum(int(row["detection_count"]) for row in rows)
    matched_total = sum(int(row["matched_count"]) for row in rows)
    false_total = sum(int(row["false_alarm_count"]) for row in rows)
    incremental_total = sum(
        int(row["incremental_false_alarm_count"]) for row in rows)
    positive_incremental_total = sum(
        int(row["positive_incremental_false_alarm_count"]) for row in rows)
    result = {
        "aggregation_dimension": dimension,
        "aggregation_value": value,
        "case_count": len(rows),
        "seed_count": len({str(row["random_seed"]) for row in rows}),
        "truth_total": truth_total,
        "detection_total": detection_total,
        "matched_total": matched_total,
        "missed_total": sum(int(row["missed_count"]) for row in rows),
        "false_alarm_total": false_total,
        "background_false_alarm_total": sum(
            int(row["background_false_alarm_count"]) for row in rows),
        "incremental_false_alarm_total": incremental_total,
        "positive_incremental_false_alarm_total": positive_incremental_total,
        "duplicate_detection_total": sum(
            int(row["duplicate_detection_count"]) for row in rows),
        "micro_pd": matched_total / truth_total if truth_total else 1.0,
        "micro_precision": (matched_total / detection_total
                            if detection_total else
                            (1.0 if truth_total == 0 else 0.0)),
        "failed_case_count": sum(int(row["matched_count"]) <
                                 int(row["visible_truth_count"])
                                 for row in rows),
    }
    result.update(value_stats((row["pd"] for row in rows), "case_pd"))
    result.update(value_stats(
        (row["false_alarm_count"] for row in rows),
        "case_false_alarm_count"))
    result.update(value_stats(
        (row["incremental_false_alarm_count"] for row in rows),
        "case_incremental_false_alarm_count"))
    result.update(value_stats(positions, "position_error_m"))
    return result


def p38_summary(algorithm_dir):
    paths = sorted(algorithm_dir.glob("p38_phase_fit_beam*.json"))
    if not paths:
        return {}
    value = read_json(paths[0])
    stage = value.get("stages", {}).get("refit", {})
    return {
        "p38_source": value.get("used_source", ""),
        "p38_model_source": value.get("used_model_source", ""),
        "p38_k_rad_per_hz": stage.get("k_rad_per_hz", math.nan),
        "p38_relative_theory_error": stage.get(
            "relative_theory_error", math.nan),
        "p38_valid": stage.get("valid", False),
        "p38_refit_sample_count": stage.get("sample_count", 0),
        "p38_strong_range_masked_bins": stage.get(
            "strong_range_masked_bins", 0),
    }


def classify_duplicate(false_alarm, truths, match_cfg):
    best = None
    for truth in truths:
        ok, _ = gate_pass(false_alarm, truth, match_cfg,
                          use_beam=True, use_range=True,
                          use_velocity=False)
        if not ok:
            continue
        errors = candidate_errors(false_alarm, truth)
        score = errors["position_error_m"]
        if not math.isfinite(score):
            score = errors["range_error_m"]
        if best is None or score < best:
            best = score
    return best is not None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--plots", action="store_true")
    args = parser.parse_args()

    matrix = read_json(args.matrix)
    output_root = REPO_ROOT / matrix["output_root"]
    report_dir = output_root / "metrics" / "multi_target"
    match_cfg = read_json(REPO_ROOT / matrix.get(
        "match_config", "configs/eval/localization_match_config.json"))
    target_rows = []
    case_rows = []

    for case in matrix.get("cases", []):
        case_id = case["case_id"]
        p4_dir = output_root / "metrics" / case_id / "p4_truth_eval"
        truth_dir = output_root / "case_work" / case_id / "truth"
        algorithm_dir = output_root / "algorithm_outputs" / case_id
        truths = visible_truths(read_csv_rows(
            truth_dir / "truth_targets_by_beam.csv"))
        matches = read_csv_rows(p4_dir / "truth_match_results.csv")
        false_alarms = read_csv_rows(p4_dir / "false_alarm_results.csv")
        detections = read_csv_rows(algorithm_dir / "detection_results.csv")
        match_by_truth = {str(row.get("truth_id", "")): row
                          for row in matches}
        p38 = p38_summary(algorithm_dir)
        duplicate_count = sum(
            classify_duplicate(row, truths, match_cfg) for row in false_alarms)

        position_errors = []
        for truth in truths:
            target_id = str(truth.get("target_id", ""))
            match = match_by_truth.get(target_id)
            matched = match is not None
            position_error = (to_float(match, "position_error_m")
                              if matched else math.nan)
            if math.isfinite(position_error):
                position_errors.append(position_error)
            target_rows.append({
                "experiment_id": matrix["experiment_id"],
                "case_id": case_id,
                "random_seed": case.get("random_seed", ""),
                "scenario_class": case.get("scenario_class", ""),
                "configured_target_count": case.get("target_count", ""),
                "expected_observability": case.get(
                    "expected_observability", ""),
                "target_id": target_id,
                "period_id": truth.get("period_id", ""),
                "beam_id": truth.get("beam_id", ""),
                "truth_range_bin": truth.get(
                    "range_bin", truth.get("expected_bin", "")),
                "truth_doppler_row": truth.get("row_truth", ""),
                "truth_snr_db": truth.get("snr_db", ""),
                "truth_radial_velocity_mps": truth.get(
                    "vr_self_mps", ""),
                "matched": int(matched),
                "missed": int(not matched),
                "det_id": match.get("item_id", "") if matched else "",
                "position_error_m": position_error,
                "range_error_m": (to_float(match, "range_error_m")
                                  if matched else math.nan),
                "velocity_error_mps": (to_float(match, "velocity_error_mps")
                                       if matched else math.nan),
                **p38,
            })

        truth_count = len(truths)
        matched_count = len(matches)
        case_rows.append({
            "experiment_id": matrix["experiment_id"],
            "case_id": case_id,
            "random_seed": case.get("random_seed", ""),
            "scenario_class": case.get("scenario_class", ""),
            "expected_observability": case.get(
                "expected_observability", ""),
            "configured_target_count": case.get("target_count", ""),
            "visible_truth_count": truth_count,
            "detection_count": len(detections),
            "matched_count": matched_count,
            "missed_count": max(0, truth_count - matched_count),
            "false_alarm_count": len(false_alarms),
            "duplicate_detection_count": duplicate_count,
            "pd": matched_count / truth_count if truth_count else 1.0,
            "precision": (matched_count / len(detections)
                          if detections else (1.0 if not truths else 0.0)),
            "mean_position_error_m": (statistics.fmean(position_errors)
                                      if position_errors else math.nan),
            "median_position_error_m": (statistics.median(position_errors)
                                        if position_errors else math.nan),
            "p95_position_error_m": percentile(position_errors, 0.95),
            "maximum_position_error_m": (max(position_errors)
                                         if position_errors else math.nan),
            **p38,
        })

    # Compare each nested scaling case with the one-target result from the
    # same clutter seed.  This isolates degradation caused by added targets.
    baseline = {
        str(row["random_seed"]): row for row in case_rows
        if row["scenario_class"] == "separated_dense_scaling"
        and int(row["configured_target_count"]) == 1
    }
    for row in case_rows:
        reference = baseline.get(str(row["random_seed"]))
        row["pd_delta_vs_single"] = (
            row["pd"] - reference["pd"] if reference else math.nan)
        ref_error = (reference.get("median_position_error_m", math.nan)
                     if reference else math.nan)
        row["median_position_error_ratio_vs_single"] = (
            row["median_position_error_m"] / ref_error
            if (math.isfinite(float(row["median_position_error_m"])) and
                math.isfinite(float(ref_error)) and float(ref_error) > 0.0)
            else math.nan)

    # A false alarm already present in the paired target-off packet is a
    # background candidate, not evidence that another injected target caused
    # mutual interference.  Preserve both the absolute count and the signed
    # paired difference; never delete either detection class here.
    background_by_seed = {
        str(row["random_seed"]): int(row["false_alarm_count"])
        for row in case_rows
        if row["scenario_class"] == "background_target_off"
    }
    for row in case_rows:
        background_count = background_by_seed.get(
            str(row["random_seed"]), 0)
        row["background_false_alarm_count"] = background_count
        row["incremental_false_alarm_count"] = (
            int(row["false_alarm_count"]) - background_count)
        row["positive_incremental_false_alarm_count"] = max(
            0, int(row["incremental_false_alarm_count"]))

    aggregate_rows = []
    scaling_counts = sorted({
        int(row["configured_target_count"]) for row in case_rows
        if row["scenario_class"] == "separated_dense_scaling"
    })
    for count in scaling_counts:
        selected = [
            row for row in case_rows
            if row["scenario_class"] == "separated_dense_scaling"
            and int(row["configured_target_count"]) == count
        ]
        aggregate_rows.append(aggregate_group(
            selected, target_rows, "target_count", str(count)))
    scenario_classes = sorted({
        str(row["scenario_class"]) for row in case_rows
        if row["scenario_class"] != "background_target_off"
    })
    for scenario_class in scenario_classes:
        selected = [row for row in case_rows
                    if row["scenario_class"] == scenario_class]
        aggregate_rows.append(aggregate_group(
            selected, target_rows, "scenario_class", scenario_class))

    write_csv(report_dir / "localization_metrics.csv", target_rows)
    write_csv(report_dir / "multi_target_summary.csv", case_rows)
    write_csv(report_dir / "multi_target_aggregate.csv", aggregate_rows)

    if args.plots and case_rows:
        import matplotlib.pyplot as plt

        scaling = [row for row in case_rows
                   if row["scenario_class"] == "separated_dense_scaling"]
        counts = sorted(set(int(row["configured_target_count"])
                            for row in scaling))
        pd_median = []
        fa_median = []
        for count in counts:
            selected = [row for row in scaling
                        if int(row["configured_target_count"]) == count]
            pd_median.append(statistics.median(row["pd"] for row in selected))
            fa_median.append(statistics.median(
                row["false_alarm_count"] for row in selected))
        fig, left = plt.subplots(figsize=(7.2, 4.5))
        right = left.twinx()
        left.plot(counts, pd_median, "o-", color="#1f77b4", label="Pd")
        right.plot(counts, fa_median, "s--", color="#d62728",
                   label="false alarms")
        left.set_xlabel("targets in the same beam/period")
        left.set_ylabel("median Pd", color="#1f77b4")
        right.set_ylabel("median false alarms", color="#d62728")
        left.set_ylim(-0.02, 1.02)
        left.grid(True, alpha=0.25)
        fig.tight_layout()
        fig.savefig(report_dir / "multi_target_scaling.png", dpi=180)
        plt.close(fig)

    report = [
        "# 多目标相互影响评测",
        "",
        "所有数字来自 Stage2 新协议回波进入生产版 `GMTI_core` 的结果，",
        "truth 与 detection 使用矩形 Hungarian 全局一对一分配。",
        "",
    ]
    strong_filter = bool(matrix.get("default_xml_overrides", {}).get(
        "cluster_strong_small_enable", False))
    if strong_filter:
        threshold = matrix.get("default_xml_overrides", {}).get(
            "cluster_strong_small_peak_over_median_db", "")
        report.extend([
            "> 注：该批次启用了小强聚类强度诊断筛选"
            f"（{threshold} dB），只用于 A/B 定位虚警来源，"
            "不作为生产推荐参数或弱目标验收依据。",
            "",
        ])
    report.extend([
        "## 跨种子聚合",
        "",
        "`absolute FA` 保留当前结果中的全部未匹配检测；"
        "`incremental FA` 扣除同种子 target-off 背景已有候选，"
        "用于判断多目标是否额外制造虚警，两者均不会被评测脚本删除。",
        "",
        "| 分组 | cases/seeds | truth/matched | micro Pd | absolute/incremental FA | 位置误差中位/P95 (m) |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for row in aggregate_rows:
        group = f"{row['aggregation_dimension']}={row['aggregation_value']}"
        report.append(
            f"| {group} | {row['case_count']}/{row['seed_count']} | "
            f"{row['truth_total']}/{row['matched_total']} | "
            f"{row['micro_pd']:.4f} | {row['false_alarm_total']}/"
            f"{row['incremental_false_alarm_total']} | "
            f"{row['position_error_m_median']:.4g}/"
            f"{row['position_error_m_p95']:.4g} |")
    report.extend([
        "",
        "## 单 case 明细",
        "",
        "| case | 场景 | truth/detection/matched | Pd | false/duplicate | 位置误差中位/P95 (m) |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for row in case_rows:
        counts = (f"{row['visible_truth_count']}/"
                  f"{row['detection_count']}/{row['matched_count']}")
        report.append(
            f"| {row['case_id']} | {row['scenario_class']} | {counts} | "
            f"{row['pd']:.4f} | {row['false_alarm_count']}/"
            f"{row['duplicate_detection_count']} | "
            f"{row['median_position_error_m']:.4g}/"
            f"{row['p95_position_error_m']:.4g} |")
    report.extend([
        "",
        "`co_located_same_doppler` 的两个目标在单波位、单周期观测中物理不可分，",
        "该 case 用来验证系统会保留漏检/不可观测事实，而不是复制一个 detection",
        "去匹配两个 truth。",
        "",
    ])
    (report_dir / "multi_target_report.md").write_text(
        "\n".join(report), encoding="utf-8")
    print(json.dumps({
        "experiment_id": matrix["experiment_id"],
        "case_count": len(case_rows),
        "maximum_visible_truth_count": max(
            (row["visible_truth_count"] for row in case_rows), default=0),
        "summary": str(report_dir / "multi_target_summary.csv"),
        "aggregate": str(report_dir / "multi_target_aggregate.csv"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
