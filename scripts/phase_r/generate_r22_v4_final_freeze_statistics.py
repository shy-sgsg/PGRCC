#!/usr/bin/env python3
"""Generate V4 statistical, missingness, H0, and resource-planning evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORICAL = ROOT / (
    "docs/research/phase_r/R22/V3_3_residual_task_gap_audit/evidence/"
    "parameter_opportunity_audit.csv.gz"
)
DEFAULT_OUTPUT = ROOT / "docs/research/phase_r/R22/evidence/v4_final_freeze_readiness"
SAMPLE_SIZES = (20, 30, 40, 60, 100)
POSITION_MDES_M = (3.0, 6.0, 12.0)
COVERAGE_MARGINS_PP = (0.25, 0.5, 1.0)
TRUE_IMPROVEMENTS_M = (0.0, 3.0, 6.0, 10.0, 15.0)
TRUE_COVERAGE_DELTAS_PP = (0.0, -0.25, -0.5, -1.0)
VARIANCE_SCALES = (1.0, 1.5, 2.0)
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 22_029


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write an empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(rows[0]), extrasaction="ignore", lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _git_identity() -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "diff", "--quiet"], cwd=ROOT, check=False,
    ).returncode != 0
    return {"head_commit": commit, "tracked_worktree_dirty": dirty}


def _historical_rows(historical_path: Path) -> tuple[dict[str, Any], str]:
    sys.path.insert(0, str(ROOT))
    from scripts.phase_r import analyze_r22_v4_p0_freeze_readiness as analysis

    with tempfile.TemporaryDirectory(prefix="r22-v4-v33-history-") as temporary:
        historical = analysis.analyze_historical_v33(historical_path, Path(temporary))
    return historical, _sha256(historical_path)


def _historical_summary(historical: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(historical["per_seed"])
    position = np.asarray([float(row["position_delta_M1_minus_Fixed_m"]) for row in rows])
    coverage = np.asarray([float(row["coverage_delta_M1_minus_Fixed_pp"]) for row in rows])
    correlation = float(np.corrcoef(position, coverage)[0, 1])
    z = math.atanh(max(-0.999999, min(0.999999, correlation)))
    z_half = float(norm.ppf(0.975)) / math.sqrt(len(rows) - 3)
    return {
        "source": "V3.3 historical batch-screening seed-level sensitivity input",
        "independent_seed_count": len(rows),
        "position_delta_M1_minus_Fixed_mean_m": float(position.mean()),
        "position_delta_M1_minus_Fixed_sd_m": float(position.std(ddof=1)),
        "coverage_delta_M1_minus_Fixed_mean_pp": float(coverage.mean()),
        "coverage_delta_M1_minus_Fixed_sd_pp": float(coverage.std(ddof=1)),
        "position_coverage_seed_level_correlation": correlation,
        "position_coverage_correlation_fisher_z_95pct_sensitivity_interval": [
            math.tanh(z - z_half), math.tanh(z + z_half),
        ],
        "position_delta_M1_minus_Huber_sd_m": float(np.std([
            float(row["position_delta_M1_minus_Huber_m"]) for row in rows
        ], ddof=1)),
        "coverage_delta_M1_minus_Huber_sd_pp": float(np.std([
            float(row["coverage_delta_M1_minus_Huber_pp"]) for row in rows
        ], ddof=1)),
        "transport_limit": "V3.3 batch SD/correlation are planning sensitivities, not V4 causal-prefix variance estimates",
    }


def _position_power_rows(position_sd: float) -> list[dict[str, Any]]:
    from scripts.phase_r.r22_v4_confirmatory_statistics import position_power

    rows = []
    for n in SAMPLE_SIZES:
        for mde in POSITION_MDES_M:
            for improvement in TRUE_IMPROVEMENTS_M:
                for scheme in ("A", "B"):
                    result = position_power(
                        n=n, seed_sd=position_sd, true_improvement_m=improvement,
                        scheme=scheme, mde_m=mde, interval_confidence=0.95,
                    )
                    rows.append({
                        **result,
                        "planning_distribution": "normal independent seed contrasts; exact noncentral-t reference",
                        "interval_relation": "two-sided 95% interval upper endpoint uses t(0.975, n-1)",
                        "interpretation": (
                            "CI upper < 0; MDE point-estimate criterion separate"
                            if scheme == "A" else "CI upper < -MDE; strict effect-size superiority"
                        ),
                    })
    return rows


def _precision_rows(position_sd: float) -> list[dict[str, Any]]:
    from scripts.phase_r.r22_v4_confirmatory_statistics import position_power

    rows = []
    for n in SAMPLE_SIZES:
        for mde in POSITION_MDES_M:
            for scheme in ("A", "B"):
                def objective(improvement: float) -> float:
                    return position_power(
                        n=n, seed_sd=position_sd, true_improvement_m=improvement,
                        scheme=scheme, mde_m=mde,
                    )["power"] - 0.80
                lower = 0.0 if scheme == "A" else mde
                planning_improvement = float(brentq(objective, lower, mde + 100.0))
                result = position_power(
                    n=n, seed_sd=position_sd, true_improvement_m=planning_improvement,
                    scheme=scheme, mde_m=mde,
                )
                rows.append({
                    "n": n,
                    "scheme": scheme,
                    "engineering_mde_m": mde,
                    "seed_sd_m": position_sd,
                    "standard_error_m": position_sd / math.sqrt(n),
                    "two_sided_95pct_t_interval_half_width_m": (
                        float(result["critical_t"]) * position_sd / math.sqrt(n)
                    ),
                    "true_improvement_for_80pct_statistical_power_m": planning_improvement,
                    "statistical_power_at_planning_effect": result["power"],
                    "alpha_at_boundary": result["alpha_at_boundary"],
                    "planning_model": "noncentral-t; seed is independent unit",
                    "scheme_a_note": (
                        "80% is for CI upper < 0 only; joint CI + point-estimate-MDE probability is in joint simulation"
                        if scheme == "A" else
                        "80% is for CI upper < -MDE, proving improvement exceeds MDE"
                    ),
                })
    return rows


def _joint_rows(summary: Mapping[str, Any], replicates: int) -> list[dict[str, Any]]:
    from scripts.phase_r.r22_v4_confirmatory_statistics import joint_position_coverage_power_grid

    corr = summary["position_coverage_seed_level_correlation"]
    correlations = sorted({round(float(value), 6) for value in (-0.5, 0.0, corr, 0.5, 0.9)})
    return joint_position_coverage_power_grid(
        position_seed_sd_m=float(summary["position_delta_M1_minus_Fixed_sd_m"]),
        coverage_seed_sd_pp=float(summary["coverage_delta_M1_minus_Fixed_sd_pp"]),
        sample_sizes=SAMPLE_SIZES,
        position_mdes_m=POSITION_MDES_M,
        coverage_ni_margins_pp=COVERAGE_MARGINS_PP,
        true_position_improvements_m=TRUE_IMPROVEMENTS_M,
        true_coverage_deltas_pp=TRUE_COVERAGE_DELTAS_PP,
        variance_scales=VARIANCE_SCALES,
        correlations=correlations,
        replicates=replicates,
        seed=BOOTSTRAP_SEED,
    )


def _bootstrap_audit(
    summary: Mapping[str, Any], *, datasets: int = 5_000,
    bootstrap_replicates: int = 1_000,
) -> dict[str, Any]:
    from scripts.phase_r.r22_v4_confirmatory_statistics import (
        bootstrap_monte_carlo_audit,
        joint_bootstrap_monte_carlo_audit,
    )

    position_sd = float(summary["position_delta_M1_minus_Fixed_sd_m"])
    coverage_sd = float(summary["coverage_delta_M1_minus_Fixed_sd_pp"])
    correlation = float(summary["position_coverage_seed_level_correlation"])
    scenarios = [
        ("scheme_a_null_size", 0.0, 0.0),
        ("scheme_a_true_3m_improvement", -3.0, 0.0),
        ("scheme_b_at_3m_mde_boundary", -3.0, -3.0),
        ("scheme_b_true_6m_improvement_vs_3m_mde", -6.0, -3.0),
    ]
    position_results = []
    for interval_method in ("percentile", "bca", "bootstrap_t"):
        for index, (name, true_delta, boundary) in enumerate(scenarios):
            audit = bootstrap_monte_carlo_audit(
                n=30,
                seed_sd=position_sd,
                true_delta=true_delta,
                null_boundary=boundary,
                datasets=datasets,
                bootstrap_replicates=bootstrap_replicates,
                seed=BOOTSTRAP_SEED + index,
                confidence=0.95,
                interval_method=interval_method,
            )
            position_results.append({
                "scenario": name, "interval_method": interval_method, **audit,
            })
    joint_results = []
    joint_scenarios = [
        ("scheme_a_3m_point_target", -3.0, 0.0, "A"),
        ("scheme_b_6m_true_vs_3m_mde", -6.0, -3.0, "B"),
    ]
    for interval_method in ("percentile", "bca", "bootstrap_t"):
        for index, (name, delta, boundary, scheme) in enumerate(joint_scenarios):
            joint_results.append({
                "scenario": name,
                "interval_method": interval_method,
                **joint_bootstrap_monte_carlo_audit(
                    n=30,
                    position_seed_sd_m=position_sd,
                    coverage_seed_sd_pp=coverage_sd,
                    position_true_delta_m=delta,
                    coverage_true_delta_pp=0.0,
                    position_boundary_m=boundary,
                    coverage_ni_margin_pp=0.5,
                    correlation=correlation,
                    datasets=datasets,
                    bootstrap_replicates=bootstrap_replicates,
                    seed=BOOTSTRAP_SEED + 100 + index,
                    confidence=0.95,
                    position_scheme=scheme,
                    position_mde_m=3.0,
                    interval_method=interval_method,
                ),
            })
    return {
        "status": "EXECUTED_CPU_SYNTHETIC_SEED_MONTE_CARLO",
        "interval_methods_compared": [
            "two-sided percentile", "two-sided BCa", "studentized bootstrap-t",
        ],
        "position_test_rule": "upper endpoint q(0.975); alpha at boundary is 0.025",
        "planned_real_analysis_replicates": BOOTSTRAP_REPLICATES,
        "planned_real_analysis_rng": f"NumPy PCG64 seed {BOOTSTRAP_SEED}",
        "monte_carlo_audit_datasets": datasets,
        "monte_carlo_inner_bootstrap_replicates": bootstrap_replicates,
        "position_interval_and_test_audits": position_results,
        "paired_joint_bootstrap_audits": joint_results,
        "interpretation": (
            "Synthetic seed-level statistical-method calibration only. These draws are not Stage2, Production, "
            "CUDA, or GMTI algorithm results. The broad joint grid separately uses t-reference planning intervals."
        ),
    }


def _missing_policy() -> dict[str, Any]:
    return {
        "opportunity_identity_contract": {
            "planned_registry": "exact (speed_mps,target_id,period) identities from frozen truth mapping",
            "missing_record": "NOT_EVALUABLE_MISSING_OPPORTUNITY_RECORDS; do not infer it was a miss or drop it",
            "unexpected_identity": "hard error; do not add a post-hoc target-period opportunity",
            "algorithm_miss": "retain the explicit planned row with matched=false in the method-specific coverage denominator",
        },
        "primary_estimand": {
            "per_speed": "RMSE_M1_minus_RMSE_Fixed_D3 on the same correctly associated target-period opportunities",
            "per_seed": "equal-weight mean across 0.5, 2.5, 6.7 m/s strata",
            "inferential_unit": "independent seed; resample a complete seed cluster",
        },
        "algorithm_task_failure": {
            "examples": ["no correct detection", "estimator failure", "zero-correction fallback"],
            "coverage_rule": "retain every planned target-period opportunity in method-specific coverage denominator",
            "position_rule": "only common correct matches enter each conditional position RMSE; record status and counts",
            "fallback_rule": "zero-correction fallback remains in denominator and is counted by speed-period",
        },
        "data_or_engineering_failure": {
            "examples": ["Production crash", "corrupt output", "truth map identity failure"],
            "rule": "retain the registered seed/case identity; only a technical repair may retry the same seed/config; never replace or draw a favorable seed",
            "analysis_status": "not evaluable until a same-identity technical repair completes; full matrix cannot pass with unresolved technical loss",
        },
        "zero_common_success_in_speed_stratum": {
            "rule": "primary seed contrast is NOT_EVALUABLE_NO_COMMON_SUCCESS; preserve all opportunity and coverage records",
            "confirmatory_consequence": "overall position claim is INCONCLUSIVE if any planned seed lacks a required speed contrast; do not silently complete-case drop",
            "sensitivity": "report observed common-support subset descriptively; it cannot replace the registered primary result",
        },
        "one_common_success_in_speed_stratum": {
            "rule": "compute the mathematically defined RMSE contrast and flag LOW_COMMON_SUPPORT",
            "freeze_blocker": "minimum acceptable common support is an engineering/user decision; do not infer it from P0",
            "candidate_sensitivity_counts_per_speed": [1, 5, 10],
        },
        "alternative_policies_compared": [
            {"policy": "drop missing seed/speed", "decision": "reject as primary; can induce outcome-dependent selection"},
            {"policy": "impute miss as fixed large position error", "decision": "reject without an independently approved finite loss scale; changes estimand"},
            {"policy": "replace seed", "decision": "prohibited; breaks registered seed identity"},
            {"policy": "retain misses in coverage and make missing primary endpoint inconclusive", "decision": "recommended conservative rule"},
        ],
        "freeze_state": "USER_DECISION_REQUIRED_FOR_LOW_COMMON_SUPPORT_THRESHOLD",
    }


def _h0_contract() -> dict[str, Any]:
    from scripts.phase_r.r22_v4_confirmatory_statistics import h0_zero_event_sample_size, zero_event_upper_bound

    candidates = []
    for limit in (0.10, 0.05, 0.01, 0.001):
        candidates.append({
            "seed_any_event_probability_upper_limit": limit,
            "confidence": 0.95,
            "independent_seeds_required_if_zero_events": h0_zero_event_sample_size(
                probability_upper_limit=limit,
            ),
        })
    return {
        "primary_candidate": "seed-any-event probability with an exact one-sided seed-level binomial upper bound",
        "supporting_endpoints": [
            "per-seed fraction of valid periods with at least one false Detection; seed-cluster bootstrap",
            "unique false-confirmed track IDs per seed; TrackManager lineage retained",
        ],
        "period_is_independent_unit": False,
        "false_track_counting_unit": "unique confirmed track ID within seed; do not count repeated frames as independent events",
        "thirty_seed_zero_event_upper_bound_one_sided_95pct": zero_event_upper_bound(30),
        "zero_event_threshold_sample_size_candidates": candidates,
        "scope": "beam-50, ten-period task-level H0; not full 61-beam or system Pfa",
        "success_threshold_status": "UNAPPROVED_USER_DECISION_REQUIRED",
        "note": "30 seeds with zero events only bounds seed-any-event probability to about 9.5%; it does not establish extremely low system Pfa",
    }


def _draw_figures(output: Path, position_rows: Sequence[Mapping[str, Any]], joint_rows: Sequence[Mapping[str, Any]]) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl-pgrcc-r22v4")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure_dir = output / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8.5, 5.0), constrained_layout=True)
    for scheme, mde, effect, label in (
        ("A", 3.0, 3.0, "A: CI<0, true improvement 3 m"),
        ("B", 3.0, 3.0, "B: CI<−3 m, true improvement 3 m"),
        ("B", 3.0, 6.0, "B: CI<−3 m, true improvement 6 m"),
    ):
        selected = [row for row in position_rows if row["scheme"] == scheme
                    and float(row["mde_m"]) == mde
                    and float(row["true_improvement_m"]) == effect]
        ax.plot([row["n"] for row in selected], [row["power"] for row in selected], marker="o", label=label)
    ax.set_xlabel("Independent seeds")
    ax.set_ylabel("Statistical pass probability")
    ax.set_ylim(0, 1)
    ax.set_title("Position hypothesis choices; historical V3.3 SD sensitivity")
    ax.legend(fontsize=8)
    fig.savefig(figure_dir / "figure_v4_position_power_schemes.svg", format="svg")
    plt.close(fig)

    selected_joint = [row for row in joint_rows
                      if row["n"] == 30 and row["position_mde_m"] == 3.0
                      and row["coverage_ni_margin_pp"] == 0.5
                      and row["true_position_improvement_m"] == 6.0
                      and row["true_coverage_delta_pp"] == 0.0
                      and row["variance_scale"] == 1.0
                      and row["position_scheme"] == "B"]
    labels = [f"ρ={row['assumed_seed_correlation']:.2f}" for row in selected_joint]
    values = [row["joint_pass_probability"] for row in selected_joint]
    fig, ax = plt.subplots(figsize=(8.5, 4.8), constrained_layout=True)
    ax.bar(labels, values, color="#417a65")
    ax.set_ylim(0, 1)
    ax.set_ylabel("Joint position + coverage pass probability")
    ax.set_title("Joint pass sensitivity to seed-level correlation (n=30)")
    fig.savefig(figure_dir / "figure_v4_joint_power_sensitivity.svg", format="svg")
    plt.close(fig)


def generate(
    historical_path: Path,
    output: Path,
    *,
    joint_replicates: int = 10_000,
    bootstrap_audit_datasets: int = 5_000,
) -> dict[str, Any]:
    started = time.monotonic()
    historical, historical_sha = _historical_rows(historical_path)
    summary = _historical_summary(historical)
    position_rows = _position_power_rows(float(summary["position_delta_M1_minus_Fixed_sd_m"]))
    precision_rows = _precision_rows(float(summary["position_delta_M1_minus_Fixed_sd_m"]))
    joint_rows = _joint_rows(summary, joint_replicates)
    bootstrap = _bootstrap_audit(summary, datasets=bootstrap_audit_datasets)
    missing = _missing_policy()
    h0 = _h0_contract()

    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "statistical_hypothesis_comparison.json", {
        "primary_contrast": "Delta_s = RMSE_M1_CAUSAL_PREFIX,s − RMSE_Fixed_D3,s; negative favors M1",
        "scheme_a": {
            "null": "H0: mean(Delta) >= 0",
            "alternative": "H1: mean(Delta) < 0",
            "statistical_success": "upper endpoint of two-sided 95% seed-cluster studentized bootstrap-t CI < 0",
            "engineering_mde": "point estimate must reach the user-approved MDE; this is not proof that the true effect exceeds MDE",
            "combined_success": "statistical improvement AND point estimate reaches MDE AND coverage noninferiority passes",
            "boundary_alpha": 0.025,
        },
        "scheme_b": {
            "null": "H0: mean(Delta) >= −MDE",
            "alternative": "H1: mean(Delta) < −MDE",
            "statistical_success": "upper endpoint of two-sided 95% seed-cluster studentized bootstrap-t CI < −MDE",
            "combined_success": "strict position superiority AND coverage noninferiority passes",
            "boundary_alpha": 0.025,
        },
        "interval_and_test_alignment": {
            "primary_interval": "95% two-sided seed-cluster studentized bootstrap-t interval of independent seeds",
            "bootstrap_resampling_unit": "seed; keep each seed's three speed strata and all nested opportunities together",
            "upper_endpoint_quantile": 0.975,
            "one_sided_95pct_upper_endpoint_quantile": 0.95,
            "critical_value_warning": "a one-sided 95% upper bound and the upper end of a two-sided 95% CI do not use the same quantile",
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "rng": f"NumPy PCG64 seed {BOOTSTRAP_SEED}",
            "planning_power": "parametric noncentral-t planning reference under normal independent seed-level deltas; not exact power for the studentized bootstrap-t procedure",
            "bootstrap_calibration": "the primary studentized bootstrap-t rule is checked by separate synthetic seed-level Monte Carlo recorded in bootstrap_coverage_audit.json; percentile and BCa are sensitivity comparisons",
        },
        "historical_v33_seed_summary": summary,
        "historical_input_sha256": historical_sha,
        "research_scope": "V3.3 historical batch sensitivity; does not estimate V4 causal-prefix variance",
        "user_thresholds_not_approved": {
            "position_mde_m": True,
            "detection_coverage_ni_margin_pp": True,
            "track_safety_limits": True,
            "h0_event_limits": True,
        },
    })
    _write_csv(output / "position_power_corrected.csv", position_rows)
    _write_csv(output / "position_precision_by_n.csv", precision_rows)
    _write_csv(output / "joint_position_coverage_power.csv", joint_rows)
    _write_json(output / "bootstrap_coverage_audit.json", bootstrap)
    _write_json(output / "missing_opportunity_policy_candidates.json", missing)
    _write_json(output / "h0_statistical_contract.json", h0)
    _draw_figures(output, position_rows, joint_rows)

    current = _git_identity()
    record = {
        "status": "STATISTICAL_EVIDENCE_GENERATED",
        "generated_at_source_commit": current["head_commit"],
        "tracked_worktree_dirty_during_generation": current["tracked_worktree_dirty"],
        "historical_v33_input": historical_path.relative_to(ROOT).as_posix()
        if historical_path.is_relative_to(ROOT) else str(historical_path),
        "historical_v33_input_sha256": historical_sha,
        "source_file_sha256": {
            "generator": _sha256(Path(__file__).resolve()),
            "statistics_module": _sha256(ROOT / "scripts/phase_r/r22_v4_confirmatory_statistics.py"),
            "historical_analyzer": _sha256(ROOT / "scripts/phase_r/analyze_r22_v4_p0_freeze_readiness.py"),
        },
        "joint_grid_row_count": len(joint_rows),
        "joint_grid_monte_carlo_replicates_per_scenario": joint_replicates,
        "bootstrap_audit_datasets_per_scenario": bootstrap_audit_datasets,
        "bootstrap_audit_inner_replicates_per_dataset": 1_000,
        "position_power_row_count": len(position_rows),
        "position_precision_row_count": len(precision_rows),
        "bootstrap_audit_scenarios": len(bootstrap["position_interval_and_test_audits"])
        + len(bootstrap["paired_joint_bootstrap_audits"]),
        "execution_kind": "CPU-only historical aggregation and synthetic seed-level statistics",
        "stage2_started": False,
        "production_started": False,
        "cuda_started": False,
        "ai_training_started": False,
        "master_plan_modified": False,
        "elapsed_seconds": time.monotonic() - started,
    }
    _write_json(output / "statistics_generation.json", record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-audit", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--joint-replicates", type=int, default=10_000)
    parser.add_argument("--bootstrap-audit-datasets", type=int, default=5_000)
    args = parser.parse_args()
    historical = args.historical_audit.expanduser().resolve()
    output = args.output_dir.expanduser().resolve()
    if args.joint_replicates < 1_000:
        parser.error("joint-replicates must be >= 1000 for final evidence")
    if args.bootstrap_audit_datasets < 1_000:
        parser.error("bootstrap-audit-datasets must be >= 1000 for final evidence")
    record = generate(
        historical,
        output,
        joint_replicates=args.joint_replicates,
        bootstrap_audit_datasets=args.bootstrap_audit_datasets,
    )
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
