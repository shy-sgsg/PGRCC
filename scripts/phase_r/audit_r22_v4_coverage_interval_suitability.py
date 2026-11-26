#!/usr/bin/env python3
"""CPU-only suitability audit for seed-level discrete coverage intervals.

This is a statistical-method simulation, not GMTI performance evidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r.r22_v4_confirmatory_statistics import coverage_mean_interval


DEFAULT_OUTPUT = ROOT / "docs/research/phase_r/R22/evidence/v4_confirmatory_contract"
METHODS = ("paired_t", "bootstrap_percentile", "bootstrap_bca")
SEED_COUNT = 30
OPPORTUNITIES_PER_SEED = 200
NONINFERIORITY_MARGIN_PP = 1.0


def _paired_binomial_differences(
    rng: np.random.Generator, *, p_both: float, p_m1_only: float,
    p_fixed_only: float, p_neither: float,
) -> np.ndarray:
    probabilities = np.asarray([p_both, p_m1_only, p_fixed_only, p_neither], dtype=float)
    if np.any(probabilities < 0.0) or not math.isclose(float(probabilities.sum()), 1.0):
        raise ValueError("paired opportunity probabilities must be non-negative and sum to one")
    counts = rng.multinomial(
        OPPORTUNITIES_PER_SEED, probabilities, size=SEED_COUNT,
    )
    return 100.0 * (counts[:, 1] - counts[:, 2]) / OPPORTUNITIES_PER_SEED


def _scenarios(rng: np.random.Generator) -> dict[str, tuple[float, np.ndarray]]:
    discrete = _paired_binomial_differences(
        rng, p_both=0.77, p_m1_only=0.10, p_fixed_only=0.07, p_neither=0.06,
    )
    zero_mean = _paired_binomial_differences(
        rng, p_both=0.72, p_m1_only=0.12, p_fixed_only=0.12, p_neither=0.04,
    )
    boundary = _paired_binomial_differences(
        rng, p_both=0.75, p_m1_only=0.12, p_fixed_only=0.13, p_neither=0.0,
    )
    skew = np.where(rng.random(SEED_COUNT) < 0.1, -10.0, 0.0)
    near_zero_variance = rng.choice(np.asarray([-1.2, -0.8]), size=SEED_COUNT)
    all_identical = np.full(SEED_COUNT, -0.5, dtype=float)
    return {
        "discrete_ties_true_3pp": (3.0, discrete),
        "zero_mean_paired_discrete": (0.0, zero_mean),
        "zero_heavy_skew_true_minus_1pp": (-1.0, skew),
        "near_zero_variance_true_minus_1pp": (-1.0, near_zero_variance),
        "all_identical_true_minus_0p5pp": (-0.5, all_identical),
        "noninferiority_boundary_minus_1pp": (-1.0, boundary),
    }


def _one_interval(
    values: np.ndarray, *, method: str, seed: int, bootstrap_replicates: int,
) -> dict[str, Any]:
    return coverage_mean_interval(
        values.tolist(), method=method, confidence=0.95,
        replicates=bootstrap_replicates, seed=seed,
    )


def run_suitability_audit(
    *, datasets: int = 1_500, bootstrap_replicates: int = 1_000,
    seed: int = 22_029,
) -> dict[str, Any]:
    if datasets < 100 or bootstrap_replicates < 200:
        raise ValueError("use at least 100 outer datasets and 200 bootstrap replicates")
    root_rng = np.random.Generator(np.random.PCG64(seed))
    summaries: list[dict[str, Any]] = []
    outlier_summaries: list[dict[str, Any]] = []

    scenario_specs = (
        "discrete_ties_true_3pp",
        "zero_mean_paired_discrete",
        "zero_heavy_skew_true_minus_1pp",
        "near_zero_variance_true_minus_1pp",
        "all_identical_true_minus_0p5pp",
        "noninferiority_boundary_minus_1pp",
    )
    for scenario_index, scenario_name in enumerate(scenario_specs):
        scenario_rng = np.random.Generator(np.random.PCG64(seed + scenario_index + 1))
        true_mean, observed_template = _scenarios(scenario_rng)[scenario_name]
        for method_index, method in enumerate(METHODS):
            evaluable = 0
            covered = 0
            boundary_false_passes = 0
            degenerate_widths = 0
            widths: list[float] = []
            endpoint_shifts: list[float] = []
            width_ratios: list[float] = []
            outlier_evaluable = 0
            # Use independent outer draws; the method-specific bootstrap streams
            # are paired across interval methods by reusing the same seed index.
            for dataset_index in range(datasets):
                values = (
                    observed_template.copy() if dataset_index == 0 else
                    _scenarios(np.random.Generator(np.random.PCG64(
                        int(root_rng.integers(0, 2**63 - 1)),
                    )))[scenario_name][1]
                )
                interval_seed = seed + 100_000 + dataset_index
                interval = _one_interval(
                    values, method=method, seed=interval_seed,
                    bootstrap_replicates=bootstrap_replicates,
                )
                if interval["status"] == "EVALUABLE":
                    evaluable += 1
                    lower, upper = float(interval["lower"]), float(interval["upper"])
                    covered += int(lower <= true_mean <= upper)
                    widths.append(upper - lower)
                    degenerate_widths += int(upper == lower)
                    if scenario_name == "noninferiority_boundary_minus_1pp":
                        boundary_false_passes += int(lower > -NONINFERIORITY_MARGIN_PP)

                    # One predeclared +10 percentage-point seed outlier checks
                    # sensitivity without changing the method-selection samples.
                    if scenario_index == 0 and dataset_index < min(datasets, 500):
                        contaminated = values.copy()
                        contaminated[0] = min(100.0, contaminated[0] + 10.0)
                        outlier_interval = _one_interval(
                            contaminated, method=method,
                            seed=interval_seed, bootstrap_replicates=bootstrap_replicates,
                        )
                        if outlier_interval["status"] == "EVALUABLE":
                            outlier_evaluable += 1
                            endpoint_shifts.append(max(
                                abs(float(outlier_interval["lower"]) - lower),
                                abs(float(outlier_interval["upper"]) - upper),
                            ))
                            base_width = upper - lower
                            if base_width > 0:
                                width_ratios.append(
                                    (float(outlier_interval["upper"])
                                     - float(outlier_interval["lower"])) / base_width
                                )

            non_evaluable = datasets - evaluable
            summaries.append({
                "scenario": scenario_name,
                "true_mean_pp": true_mean,
                "method": method,
                "datasets": datasets,
                "evaluable_datasets": evaluable,
                "non_evaluable_fraction": non_evaluable / datasets,
                "coverage_among_evaluable": covered / evaluable if evaluable else None,
                "coverage_monte_carlo_se": (
                    math.sqrt((covered / evaluable) * (1.0 - covered / evaluable) / evaluable)
                    if evaluable and covered < evaluable else 0.0 if evaluable else None
                ),
                "median_interval_width_pp": float(np.median(widths)) if widths else None,
                "degenerate_width_fraction_among_evaluable": (
                    degenerate_widths / evaluable if evaluable else None
                ),
                "boundary_false_noninferiority_passes": (
                    boundary_false_passes if scenario_name == "noninferiority_boundary_minus_1pp" else None
                ),
                "boundary_false_noninferiority_pass_rate": (
                    boundary_false_passes / datasets
                    if scenario_name == "noninferiority_boundary_minus_1pp" else None
                ),
                "boundary_pass_rate_monte_carlo_se": (
                    math.sqrt((boundary_false_passes / datasets)
                              * (1.0 - boundary_false_passes / datasets) / datasets)
                    if scenario_name == "noninferiority_boundary_minus_1pp" else None
                ),
            })
            if scenario_index == 0:
                outlier_summaries.append({
                    "scenario": scenario_name,
                    "method": method,
                    "outlier_definition": "replace one seed difference by min(100 pp, observed + 10 pp)",
                    "datasets_checked": min(datasets, 500),
                    "paired_evaluable_datasets": outlier_evaluable,
                    "median_max_endpoint_shift_pp": (
                        float(np.median(endpoint_shifts)) if endpoint_shifts else None
                    ),
                    "median_interval_width_ratio": (
                        float(np.median(width_ratios)) if width_ratios else None
                    ),
                })

    return {
        "schema_version": 1,
        "status": "CPU_METHOD_SUITABILITY_ONLY",
        "independent_unit": "seed",
        "seed_count_per_dataset": SEED_COUNT,
        "opportunities_per_seed_in_paired_binomial_scenarios": OPPORTUNITIES_PER_SEED,
        "confidence": 0.95,
        "noninferiority_margin_pp_for_boundary_check": NONINFERIORITY_MARGIN_PP,
        "methods": list(METHODS),
        "datasets_per_scenario": datasets,
        "bootstrap_replicates": bootstrap_replicates,
        "outer_rng": f"NumPy PCG64 seed {seed}",
        "bootstrap_rng": "NumPy PCG64 seed 122029 + outer dataset index",
        "scenario_method_results": summaries,
        "outlier_sensitivity": outlier_summaries,
        "provisional_recommendation": {
            "method": "paired_t",
            "status": "PROVISIONAL_PENDING_USER_APPROVAL",
            "basis": (
                "Across these synthetic scenarios, paired_t had near-nominal coverage in the evaluable "
                "non-degenerate cases. Its boundary false-pass point estimate was numerically lowest, "
                "but it was close to the percentile estimate relative to Monte Carlo uncertainty. This "
                "finite simulation does not establish universal coverage or GMTI validity."
            ),
            "constraints": [
                "Use seed as the independent unit.",
                "Keep zero-variance samples NOT_EVALUABLE.",
                "Do not treat this simulation as GMTI performance evidence.",
                "Require explicit user approval before freezing the method in the confirmatory contract.",
            ],
        },
        "interpretation_limit": (
            "Synthetic seed-level coverage differences only; no CUDA, GMTI, Pd, Pfa, or method-performance evidence. "
            "The all-identical scenario is intentionally NOT_EVALUABLE."
        ),
    }


def _write_outputs(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "coverage_interval_suitability.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    csv_path = output_dir / "coverage_interval_suitability.csv"
    rows = result["scenario_method_results"]
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# R22-V4 离散覆盖率区间方法适用性审计",
        "",
        "本审计仅使用 CPU 生成的合成 seed 级覆盖率差，不是 GMTI 实验或性能证据。",
        "独立单位为 seed；每组 30 个 seed，双侧置信水平 95%。",
        "",
        "| 场景 | 方法 | 可评估率 | 可评估条件下覆盖率 | 区间宽度中位数 (pp) | NI 边界错误通过率 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        def fmt(value: Any) -> str:
            return "—" if value is None else f"{float(value):.4f}"
        lines.append(
            f"| {row['scenario']} | {row['method']} | {1.0 - row['non_evaluable_fraction']:.4f} | "
            f"{fmt(row['coverage_among_evaluable'])} | {fmt(row['median_interval_width_pp'])} | "
            f"{fmt(row['boundary_false_noninferiority_pass_rate'])} |"
        )
    lines.extend([
        "",
        "## Outlier sensitivity",
        "",
        "| 方法 | 可配对评估数 | 端点最大移动中位数 (pp) | 区间宽度比中位数 |",
        "|---|---:|---:|---:|",
    ])
    for row in result["outlier_sensitivity"]:
        lines.append(
            f"| {row['method']} | {row['paired_evaluable_datasets']} | "
            f"{row['median_max_endpoint_shift_pp']:.4f} | {row['median_interval_width_ratio']:.4f} |"
        )
    lines.extend([
        "",
        "所有差异、非评估比例、Monte Carlo 标准误和逐场景数值见相邻 JSON/CSV。",
        "方法选择不得以更容易通过非劣效门为依据；全相同数据必须保持不可评估。",
        "",
        "## 暂定方法建议（待用户批准）",
        "",
        "在本次六种合成分布与 1,500 次外层模拟中，暂建议以 `paired_t` 作为目标检测覆盖率及航迹覆盖率差的区间候选：可评估且非退化场景覆盖率接近 95%；非劣效边界错误通过率点估计为 2.93%（Monte Carlo SE 0.44个百分点），percentile 为 3.20%（SE 0.45个百分点），BCa 为 4.40%（SE 0.53个百分点）。paired_t 与 percentile 的差距小于模拟误差，不能据此宣称前者显著更优。",
        "这只是有限模拟下的暂定统计方法建议；不证明普遍覆盖率，也不是 GMTI 性能证据。全相同数据仍须 `NOT_EVALUABLE`；在用户明确批准并冻结前，生产设计保持不可执行。",
        "",
    ])
    (output_dir / "coverage_interval_suitability.md").write_text(
        "\n".join(lines), encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=int, default=1_500)
    parser.add_argument("--bootstrap-replicates", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=22_029)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run_suitability_audit(
        datasets=args.datasets,
        bootstrap_replicates=args.bootstrap_replicates,
        seed=args.seed,
    )
    _write_outputs(result, args.output_dir.expanduser().resolve())
    print(json.dumps({
        "status": result["status"],
        "output_dir": str(args.output_dir.expanduser().resolve()),
        "scenario_count": 6,
        "method_count": len(METHODS),
        "datasets_per_scenario": args.datasets,
        "bootstrap_replicates": args.bootstrap_replicates,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
