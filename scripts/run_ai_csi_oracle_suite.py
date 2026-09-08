#!/usr/bin/env python3
"""运行第一阶段 Current/Oracle CSI 的典型与非理想场景并汇总结果。

逐案例目录只用于本地复核；仓库交付的是根目录下的小型 CSV/PNG/JSON 汇总。
每个案例的 Oracle 权重由对应的 C+N 背景 BIN 单独估计，然后固定用于 S+C+N
BIN，目标真值只用于评价 ROI 和检测指标，不参与权重求解。
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Dict, Iterable, List

os.environ.setdefault("MPLCONFIGDIR", "/tmp/ai_csi_mpl_cache")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/ai_csi_oracle"
CHILD = ROOT / "scripts/run_ai_csi_oracle.py"

CASES = [
    {
        "condition": "typical_uniform_clutter",
        "condition_label": "Typical: uniform texture",
        "case_id": "ai_csi_stage2_typical_1beam_20260908",
        "target": OUT / "stage2_target/data/stage2_statistical_newprotocol_period_0000.bin",
        "background": OUT / "stage2_background/data/stage2_statistical_newprotocol_period_0000.bin",
        "xml": OUT / "stage2_target/config/temp_config_stage2_period_0000.xml",
        "truth": OUT / "stage2_target/truth/truth_targets_by_beam.csv",
        "child_out": OUT / "per_case/typical_uniform_clutter",
    },
    {
        "condition": "nonideal_nonuniform_amp_phase_drop",
        "condition_label": "Non-ideal: nonuniform + amp/phase + drops",
        "case_id": "ai_csi_stage2_nonideal_1beam_20260908",
        "target": OUT / "nonideal_target/data/stage2_statistical_newprotocol_period_0000.bin",
        "background": OUT / "nonideal_background/data/stage2_statistical_newprotocol_period_0000.bin",
        "xml": OUT / "nonideal_target/config/temp_config_stage2_period_0000.xml",
        "truth": OUT / "nonideal_target/truth/truth_targets_by_beam.csv",
        "child_out": OUT / "per_case/nonideal_nonuniform_amp_phase_drop",
    },
]


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: Iterable[Dict[str, object]]) -> None:
    rows = list(rows)
    if not rows:
        return
    fields: List[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run_case(case: Dict[str, object]) -> None:
    case["child_out"].mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(CHILD),
        "--target",
        str(case["target"]),
        "--background",
        str(case["background"]),
        "--xml",
        str(case["xml"]),
        "--truth",
        str(case["truth"]),
        "--out",
        str(case["child_out"]),
        "--case-id",
        str(case["case_id"]),
    ]
    subprocess.run(command, cwd=ROOT, env=os.environ.copy(), check=True)


def annotated(rows: Iterable[Dict[str, str]], case: Dict[str, object]) -> List[Dict[str, object]]:
    result: List[Dict[str, object]] = []
    for row in rows:
        item: Dict[str, object] = dict(row)
        item["condition"] = case["condition"]
        item["condition_label"] = case["condition_label"]
        result.append(item)
    return result


def grouped_values(rows: List[Dict[str, object]], key: str, variants: List[str], conditions: List[str]) -> np.ndarray:
    result = np.full((len(conditions), len(variants)), np.nan, dtype=float)
    for row in rows:
        if str(row.get("variant")) not in variants:
            continue
        if str(row.get("condition")) not in conditions:
            continue
        i = conditions.index(str(row["condition"]))
        j = variants.index(str(row["variant"]))
        result[i, j] = float(row[key])
    return result


def plot_grouped_scnr(rows: List[Dict[str, object]]) -> None:
    variants = [
        "Current",
        "Oracle phase",
        "Oracle amplitude",
        "Oracle delay",
        "Oracle support",
        "Oracle weight",
        "Oracle All",
    ]
    conditions = [str(case["condition"]) for case in CASES]
    labels = [str(case["condition_label"]) for case in CASES]
    values = grouped_values(rows, "SCNR_improvement_dB", variants, conditions)
    x = np.arange(len(variants))
    width = 0.36
    fig, ax = plt.subplots(figsize=(12, 5.2))
    for i, label in enumerate(labels):
        ax.bar(x + (i - 0.5) * width, values[i], width, label=label)
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_xticks(x, variants, rotation=25, ha="right")
    ax.set_ylabel("SCNR improvement (dB)")
    ax.set_title("Current vs Oracle CSI cancellation by condition")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "current_vs_oracle_scnr_improvement.png", dpi=150)
    plt.close(fig)


def plot_mismatch(loo: List[Dict[str, object]], overall: List[Dict[str, object]]) -> None:
    labels = ["Phase", "Amplitude", "Delay", "Support", "Signed Oracle−Current"]
    conditions = [str(case["condition"]) for case in CASES]
    condition_labels = [str(case["condition_label"]) for case in CASES]
    values = []
    for condition in conditions:
        all_row = next(row for row in overall if row["condition"] == condition)
        all_scnr = float(all_row["oracle_all_scnr_improvement_dB"])
        current_scnr = float(all_row["current_scnr_improvement_dB"])
        loo_map = {
            str(row["variant"]): float(row["SCNR_improvement_dB"])
            for row in loo
            if row["condition"] == condition
        }
        values.append(
            [
                all_scnr - loo_map["LOO_phase"],
                all_scnr - loo_map["LOO_amplitude"],
                all_scnr - loo_map["LOO_delay"],
                all_scnr - loo_map["LOO_support"],
                all_scnr - current_scnr,
            ]
        )
    values_array = np.asarray(values, dtype=float)
    x = np.arange(len(labels))
    width = 0.36
    fig, ax = plt.subplots(figsize=(10, 5.0))
    for i, label in enumerate(condition_labels):
        ax.bar(x + (i - 0.5) * width, values_array[i], width, label=label)
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_xticks(x, labels, rotation=20, ha="right")
    ax.set_ylabel("Signed SCNR difference (dB)")
    ax.set_title("Leave-one-error-out mismatch attribution")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "mismatch_contribution.png", dpi=150)
    plt.close(fig)


def plot_quality(rows: List[Dict[str, object]]) -> None:
    variants = [
        "Current",
        "Oracle phase",
        "Oracle amplitude",
        "Oracle delay",
        "Oracle support",
        "Oracle weight",
        "Oracle All",
    ]
    conditions = [str(case["condition"]) for case in CASES]
    labels = [str(case["condition_label"]) for case in CASES]
    keys = ["CA_ROI_dB", "target_loss_dB", "Pd"]
    titles = ["CA ROI (dB)", "Target loss (dB)", "Pd proxy"]
    x = np.arange(len(variants))
    width = 0.36
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.0))
    for axis, key, title in zip(axes, keys, titles):
        values = grouped_values(rows, key, variants, conditions)
        for i, label in enumerate(labels):
            axis.bar(x + (i - 0.5) * width, values[i], width, label=label)
        axis.set_xticks(x, variants, rotation=35, ha="right")
        axis.set_title(title)
        if key == "Pd":
            axis.set_ylim(0.0, 1.1)
    axes[0].legend(fontsize=7)
    fig.suptitle("Cancellation quality, target preservation and detection proxy")
    fig.tight_layout()
    fig.savefig(OUT / "ca_target_loss_pd.png", dpi=150)
    plt.close(fig)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)
    for case in CASES:
        run_case(case)

    single: List[Dict[str, object]] = []
    loo: List[Dict[str, object]] = []
    overall: List[Dict[str, object]] = []
    for case in CASES:
        child_out = case["child_out"]
        single.extend(annotated(read_csv(child_out / "single_factor_summary.csv"), case))
        loo.extend(annotated(read_csv(child_out / "leave_one_out_summary.csv"), case))
        overall.extend(annotated(read_csv(child_out / "oracle_overall_summary.csv"), case))

    write_csv(OUT / "single_factor_summary.csv", single)
    write_csv(OUT / "leave_one_out_summary.csv", loo)
    write_csv(OUT / "oracle_overall_summary.csv", overall)
    plot_grouped_scnr(single)
    plot_mismatch(loo, overall)
    plot_quality(single)

    manifest = {
        "script": "scripts/run_ai_csi_oracle_suite.py",
        "cases": [
            {
                "condition": case["condition"],
                "condition_label": case["condition_label"],
                "case_id": case["case_id"],
                "target_bin": str(case["target"]),
                "background_bin": str(case["background"]),
                "xml": str(case["xml"]),
                "truth": str(case["truth"]),
                "per_case_output": str(case["child_out"]),
            }
            for case in CASES
        ],
        "oracle_weight_constraint": "每个案例的 Oracle weight/alpha 只由配对 clutter+noise truth 估计，再固定应用到 clutter+target+noise；目标 truth 不参与权重求解。",
        "summary_files": [
            "outputs/ai_csi_oracle/single_factor_summary.csv",
            "outputs/ai_csi_oracle/leave_one_out_summary.csv",
            "outputs/ai_csi_oracle/oracle_overall_summary.csv",
        ],
        "gpu_production_run": False,
        "evidence_boundary": "CPU offline replay of current source operators; CUDA production execution not claimed because no NVIDIA driver is available",
        "raw_output_policy": "generated BINs and per-case replay artifacts stay outside Git tracking; only compact research deliverables are tracked",
    }
    with (OUT / "oracle_analysis_manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    print(json.dumps({"cases": [case["case_id"] for case in CASES], "output": str(OUT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
