#!/usr/bin/env python3
"""Aggregate P5-P10 production evidence without upgrading stale runs to passes.

The script only reshapes files emitted by the production evaluation workflows.
It never recomputes signal-processing outputs and labels missing or matrix-stale
evidence explicitly so a dry-run or an old result directory cannot satisfy an
acceptance gate by accident.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], preferred: Iterable[str] = ()) -> None:
    fields = []
    for name in preferred:
        if name not in fields:
            fields.append(name)
    for row in rows:
        for name in row:
            if name not in fields:
                fields.append(name)
    if not fields:
        fields = ["evidence_status"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def child_output_root(matrix_path: Path, matrix: dict) -> Path:
    configured = matrix.get("output_root", f"outputs/eval/{matrix.get('experiment_id', matrix_path.stem)}")
    return (REPO_ROOT / configured).resolve()


def evidence_status(matrix_path: Path, matrix: dict, root: Path) -> tuple[str, dict]:
    summary_path = root / "experiment_summary.json"
    if not summary_path.exists():
        return "pending", {}
    try:
        summary = read_json(summary_path)
    except (OSError, ValueError, TypeError):
        return "invalid_summary", {}
    snapshot = root / "matrix" / matrix_path.name
    if not snapshot.exists():
        return "unverified_matrix", summary
    if sha256(snapshot) != sha256(matrix_path):
        return "stale_matrix", summary
    failed = int(summary.get("failed_case_count", 0) or 0)
    selected = int(summary.get("selected_case_count", 0) or 0)
    complete = int(summary.get("complete_case_count", 0) or 0)
    expected = len(matrix.get("cases", []))
    if failed:
        return "failed", summary
    if selected == expected and complete == expected and expected > 0:
        return "current_complete", summary
    return "partial", summary


def metric_rows(root: Path, pattern: str, experiment_id: str, status: str) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(root.glob(pattern)):
        relative = path.relative_to(root)
        case_id = (relative.parts[1] if len(relative.parts) > 1
                   and relative.parts[0] in {"metrics", "algorithm_outputs"} else "")
        for row in read_csv(path):
            enriched = {
                "evidence_status": status,
                "source_experiment_id": experiment_id,
                "source_case_id": case_id,
                "source_file": str(path.relative_to(REPO_ROOT)),
            }
            enriched.update(row)
            rows.append(enriched)
    return rows


def localization_rows(root: Path, experiment_id: str, status: str) -> list[dict]:
    rows = []
    manifest = {row.get("case_id", ""): row for row in read_csv(root / "case_manifest.csv")}
    for case_id, source in sorted(manifest.items()):
        if not case_id:
            continue
        row = {
            "evidence_status": status,
            "experiment_id": experiment_id,
            "case_id": case_id,
            "truth_count": source.get("truth_count", ""),
            "detection_count": source.get("detection_count", ""),
            "matched_count": source.get("matched_count", ""),
            "missed": _difference(source.get("truth_count"), source.get("matched_count")),
            "false_alarm": source.get("false_alarm_count", ""),
            "position_error_m": source.get("mean_position_error_m", ""),
            "velocity_error_mps": source.get("mean_velocity_error_mps", ""),
            "config_hash": source.get("config_hash", ""),
            "data_hash": source.get("data_hash", ""),
            "git_commit": source.get("git_commit", ""),
        }
        detailed = read_csv(
            root / "metrics" / case_id / "p4_truth_eval" / "truth_match_results.csv")
        if detailed:
            row.update({
                "range_error_m": _mean(detailed, "range_error_m"),
                "matched": len(detailed),
            })
        else:
            row["matched"] = source.get("matched_count", "")
        rows.append(row)
    return rows


def impairment_rows(root: Path, experiment_id: str, status: str) -> list[dict]:
    rows = []
    for base in localization_rows(root, experiment_id, status):
        case_id = base["case_id"]
        scenario_path = root / "configs" / case_id / "expanded_scenario.json"
        impairments = {}
        if scenario_path.exists():
            impairments = read_json(scenario_path).get("channel_impairments", {}) or {}
        calibration_files = sorted(
            (root / "algorithm_outputs" / case_id).glob("channel_calibration_beam*.json"))
        calibration = read_json(calibration_files[0]) if calibration_files else {}
        p5 = _first(root / "metrics" / case_id / "p5_csi" / "csi_metric_summary.csv")
        row = dict(base)
        for key in (
                "enabled", "channel_amp_mismatch_db", "channel_fixed_phase_mismatch_deg",
                "channel_phase_jitter_std_deg", "channel_range_shift_samples",
                "channel_time_delay_ns", "channel_noise_power_ratio_db", "baseline_error_m",
                "per_pulse_phase_drift_deg", "per_beam_phase_bias_deg"):
            row[key] = impairments.get(key, 0 if key != "enabled" else False)
        for key in (
                "valid", "applied", "status", "phase_bias_deg", "applied_phase_bias_deg",
                "relative_gain_abs", "coherence", "residual_rmse_rad", "sample_count",
                "inlier_count", "range_shift_gate_enabled", "range_alignment_valid",
                "estimated_range_shift_bins", "range_profile_correlation",
                "range_profile_peak_margin"):
            row[f"calibration_{key}"] = calibration.get(key, "")
        row["CA_ROI_dB"] = p5.get("mean_CA_ROI_dB", "")
        row["SCNR_improvement_dB"] = p5.get("mean_SCNR_improvement_dB", "")
        row["target_loss_dB"] = p5.get("mean_target_loss_dB", "")
        rows.append(row)
    return rows


def _first(path: Path) -> dict:
    values = read_csv(path)
    return values[0] if values else {}


def _number(value) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def _mean(rows: list[dict], key: str):
    values = [_number(row.get(key)) for row in rows]
    values = [value for value in values if math.isfinite(value)]
    return sum(values) / len(values) if values else ""


def _difference(left, right):
    a, b = _number(left), _number(right)
    return max(0, int(a - b)) if math.isfinite(a) and math.isfinite(b) else ""


def generate_plots(output_root: Path, datasets: dict[str, list[dict]]) -> list[str]:
    plot_dir = output_root / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    known_names = {
        "csi_scnr_target_loss.png", "velocity_truth_vs_estimate.png",
        "localization_error_by_case.png", "tracking_completeness_id_switch.png",
    }
    for name in known_names:
        path = plot_dir / name
        if path.exists():
            path.unlink()
    os.environ.setdefault("MPLCONFIGDIR", str(output_root / ".matplotlib"))
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return []

    generated = []
    blue, gold, orange, ink = "#2E5B88", "#C69332", "#D97735", "#30343B"

    def finish(fig, name: str):
        path = plot_dir / name
        fig.savefig(path, dpi=160, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        generated.append(str(path.relative_to(REPO_ROOT)))

    p5 = [row for row in datasets["scnr"] if math.isfinite(_number(row.get("SCNR_improvement_dB")))]
    if p5:
        labels = [row.get("source_case_id") or row.get("case_id", "") for row in p5]
        x = np.arange(len(labels))
        fig, ax = plt.subplots(figsize=(max(8, len(labels) * .65), 4.8))
        ax.bar(x - .2, [_number(row.get("SCNR_improvement_dB")) for row in p5], .4,
               label="SCNR improvement", color=blue, edgecolor=ink)
        ax.bar(x + .2, [_number(row.get("target_loss_dB")) for row in p5], .4,
               label="Target loss", color=gold, edgecolor=ink, hatch="//")
        ax.axhline(0, color=ink, linewidth=.8)
        ax.set(title="CSI target-region metrics", ylabel="Power ratio (dB)",
               xlabel="Evaluation case")
        ax.set_xticks(x, labels, rotation=35, ha="right")
        ax.grid(axis="y", color="#D9DEE5", linewidth=.6)
        ax.legend(frameon=False)
        finish(fig, "csi_scnr_target_loss.png")

    velocity = [row for row in datasets["velocity"]
                if math.isfinite(_number(row.get("truth_velocity_mps")))
                and math.isfinite(_number(row.get("estimated_radial_velocity_mps")))]
    if velocity:
        truth = [_number(row["truth_velocity_mps"]) for row in velocity]
        estimate = [_number(row["estimated_radial_velocity_mps"]) for row in velocity]
        low, high = min(truth + estimate), max(truth + estimate)
        pad = max(1.0, .05 * (high - low or 1.0))
        fig, ax = plt.subplots(figsize=(6.4, 5.2))
        ax.scatter(truth, estimate, color=blue, edgecolor=ink, s=46, label="Detection")
        ax.plot([low-pad, high+pad], [low-pad, high+pad], color=ink,
                linestyle="--", label="Ideal")
        ax.set(xlabel="Truth velocity (m/s)", ylabel="Estimated single-period velocity (m/s)",
               title="Single-period velocity diagnostic")
        ax.grid(color="#D9DEE5", linewidth=.6)
        ax.legend(frameon=False)
        finish(fig, "velocity_truth_vs_estimate.png")

    localization = [row for row in datasets["localization"]
                    if math.isfinite(_number(row.get("position_error_m")))]
    if localization:
        localization = sorted(localization, key=lambda row: _number(row["position_error_m"]))
        labels = [row["case_id"] for row in localization]
        values = [_number(row["position_error_m"]) for row in localization]
        fig, ax = plt.subplots(figsize=(8.5, max(4.2, .38 * len(labels))))
        ax.barh(labels, values, color=blue, edgecolor=ink)
        ax.set(title="Localization error by case", xlabel="Mean 2-D position error (m)")
        ax.grid(axis="x", color="#D9DEE5", linewidth=.6)
        ax.invert_yaxis()
        finish(fig, "localization_error_by_case.png")

    tracking = datasets["tracking"]
    if tracking:
        labels = [row.get("case_id", "") for row in tracking]
        completeness = [_number(row.get("track_completeness")) for row in tracking]
        switches = [_number(row.get("id_switch_count")) for row in tracking]
        fig, axes = plt.subplots(1, 2, figsize=(12, max(5, .28 * len(labels))), sharey=True)
        axes[0].barh(labels, completeness, color=blue, edgecolor=ink)
        axes[0].set(xlim=(0, 1.05), xlabel="Track completeness", title="Completeness")
        axes[1].barh(labels, switches, color=orange, edgecolor=ink, hatch="//")
        axes[1].set(xlabel="ID switches", title="Identity stability")
        for ax in axes:
            ax.grid(axis="x", color="#D9DEE5", linewidth=.6)
            ax.invert_yaxis()
        fig.suptitle("Tracking robustness by scenario")
        finish(fig, "tracking_completeness_id_switch.png")
    return generated


def build_markdown(output_root: Path, inventory: list[dict], datasets: dict, plots: list[str]) -> Path:
    lines = [
        "# GMTI P5–P10 自动证据摘要", "",
        "## 结论", "",
        "本页只汇总与当前实验矩阵一致的生产链路产物；`pending`、`stale_matrix` 和 "
        "`partial` 均不计为通过。CUDA 正式矩阵未运行时，报告会保留缺口而不会引用旧目录充数。", "",
        "## 阶段证据状态", "",
        "| 阶段 | 实验 | 状态 | 完成/计划 case | 失败 |", "|---|---|---|---:|---:|",
    ]
    for item in inventory:
        lines.append(
            f"| {item['stage']} | {item['experiment_id']} | {item['evidence_status']} | "
            f"{item.get('complete_case_count', 0)}/{item.get('expected_case_count', 0)} | "
            f"{item.get('failed_case_count', 0)} |")
    lines.extend(["", "## 标准汇总文件", ""])
    for name, rows in datasets.items():
        lines.append(f"- `{name}`：{len(rows)} 行")
    if plots:
        lines.extend(["", "## 图表", ""])
        for path in plots:
            lines.append(f"- `{path}`")
    lines.extend([
        "", "## 判读约束", "",
        "- P6 的单周期径向速度仅是诊断量，不是定位放行条件；最终速度来自多周期航迹位移除以真实时间间隔。",
        "- P8 的 `estimated_range_shift_bins` 是相关峰错位指示量，用于拒绝不适用的标量幅相校准，不是物理时延标定值。",
        "- 任何旧矩阵、缺少 manifest 或配置 hash 不一致的结果都必须重跑后才能用于最终验收。",
        "",
    ])
    path = output_root / "reports" / "experiment_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--strict", action="store_true",
                        help="return non-zero unless every child matrix is current_complete")
    args = parser.parse_args()
    matrix_path = args.matrix.resolve()
    matrix = read_json(matrix_path)
    if matrix.get("workflow") != "composite":
        parser.error("--matrix must describe workflow=composite")
    output_root = (args.output_root.resolve() if args.output_root else
                   child_output_root(matrix_path, matrix))
    metrics_dir = output_root / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)

    inventory = []
    datasets = {
        "cancellation_metrics.csv": [],
        "scnr_improvement_metrics.csv": [],
        "velocity_ambiguity_metrics.csv": [],
        "localization_metrics.csv": [],
        "impairment_metrics.csv": [],
        "tracking_metrics.csv": [],
        "timing_metrics.csv": [],
    }
    stage_names = {"p5": "P5", "p6": "P6", "p7": "P7", "p8": "P8", "p9": "P9"}
    for child_name in matrix.get("matrices", []):
        child_path = (REPO_ROOT / str(child_name)).resolve()
        child = read_json(child_path)
        root = child_output_root(child_path, child)
        status, summary = evidence_status(child_path, child, root)
        experiment_id = child.get("experiment_id", child_path.stem)
        stage = next((label for token, label in stage_names.items()
                      if token in child_path.stem.lower()), child_path.stem)
        inventory.append({
            "stage": stage,
            "matrix": str(child_path.relative_to(REPO_ROOT)),
            "matrix_hash": sha256(child_path),
            "experiment_id": experiment_id,
            "output_root": str(root.relative_to(REPO_ROOT)),
            "evidence_status": status,
            "expected_case_count": len(child.get("cases", [])),
            "selected_case_count": summary.get("selected_case_count", 0),
            "complete_case_count": summary.get("complete_case_count", 0),
            "failed_case_count": summary.get("failed_case_count", 0),
            "git_commit": summary.get("git_commit", ""),
            "worktree_fingerprint": summary.get("worktree_fingerprint", ""),
        })
        datasets["cancellation_metrics.csv"].extend(metric_rows(
            root, "metrics/*/p5_csi/cancellation_metrics.csv", experiment_id, status))
        datasets["scnr_improvement_metrics.csv"].extend(metric_rows(
            root, "metrics/*/p5_csi/scnr_improvement_metrics.csv", experiment_id, status))
        datasets["velocity_ambiguity_metrics.csv"].extend(metric_rows(
            root, "metrics/*/p6_velocity/velocity_ambiguity_metrics.csv", experiment_id, status))
        datasets["timing_metrics.csv"].extend(metric_rows(
            root, "algorithm_outputs/*/timing_metrics.csv", experiment_id, status))
        if stage == "P7":
            datasets["localization_metrics.csv"].extend(
                localization_rows(root, experiment_id, status))
        if stage == "P8":
            datasets["impairment_metrics.csv"].extend(
                impairment_rows(root, experiment_id, status))
        if stage == "P9":
            for row in read_csv(root / "tracking_metrics.csv"):
                datasets["tracking_metrics.csv"].append({
                    "evidence_status": status,
                    "source_file": str((root / "tracking_metrics.csv").relative_to(REPO_ROOT)),
                    **row,
                })

    for name, rows in datasets.items():
        write_csv(metrics_dir / name, rows, ("evidence_status", "experiment_id", "case_id"))
    write_csv(output_root / "full_regression_summary.csv", inventory,
              ("stage", "experiment_id", "evidence_status"))
    write_json(output_root / "evidence_inventory.json", {
        "matrix": str(matrix_path.relative_to(REPO_ROOT)),
        "matrix_hash": sha256(matrix_path),
        "children": inventory,
        "all_current_complete": all(
            item["evidence_status"] == "current_complete" for item in inventory),
    })
    # Reader-facing charts only use current evidence.  Stale rows remain in the
    # audit CSVs with their status, but are never promoted into a visual result.
    current = lambda rows: [row for row in rows
                            if row.get("evidence_status") == "current_complete"]
    chart_data = {
        "scnr": current(datasets["scnr_improvement_metrics.csv"]),
        "velocity": current(datasets["velocity_ambiguity_metrics.csv"]),
        "localization": current(datasets["localization_metrics.csv"]),
        "tracking": current(datasets["tracking_metrics.csv"]),
    }
    plots = generate_plots(output_root, chart_data)
    report = build_markdown(output_root, inventory, datasets, plots)
    print(json.dumps({
        "output_root": str(output_root),
        "report": str(report),
        "all_current_complete": all(
            item["evidence_status"] == "current_complete" for item in inventory),
        "plots": plots,
    }, ensure_ascii=False))
    incomplete = any(item["evidence_status"] != "current_complete" for item in inventory)
    return 1 if args.strict and incomplete else 0


if __name__ == "__main__":
    raise SystemExit(main())
