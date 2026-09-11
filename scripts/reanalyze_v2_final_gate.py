#!/usr/bin/env python3
"""Recompute the V2 final-gate statistics without rerunning CUDA.

The published V2 formal run mixed roles and splits in its historical recovery
aggregate. This audit reads only the retained compact CSV/JSON evidence and
recomputes the quantities with the stricter exploratory protocol:

* recovery: split=test and role=target_off only;
* production CFAR: split=test and role=target_off only;
* candidate-vs-Current differences: paired by scene and bootstrapped by scene;
* legacy_hit is relabeled matched_target_on_pd because it is a target-on ROI
  matched detection, not a causal Pd measurement.

No estimator, gate threshold, raw runtime file, or CUDA result is modified.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FORMAL = ROOT / "outputs/physics_adaptive_selective_v2_formal"
DEFAULT_OUTPUT = ROOT / "outputs/v2_final_gate_reanalysis"
METHODS = (
    "J0_Current",
    "J1_D3_delay_only",
    "J2_P1_phase_only",
    "J3_D3_P1_joint",
    "J4_D3_P2_joint",
    "J5_Selective_Physics_Calibration",
    "J6_Joint_Phase_Surface",
)
SELECTIVE_METHODS = (
    "J5_Selective_Physics_Calibration",
    "J6_Joint_Phase_Surface",
)
HEADROOM_THRESHOLDS_DB = (0.5, 1.0)


def finite(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.floating, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]],
              fields: list[str] | None = None) -> None:
    materialized = list(rows)
    if fields is None:
        fields = []
        for row in materialized:
            for key in row:
                if key not in fields:
                    fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(materialized)


def bootstrap_stat(values: np.ndarray, statistic: Callable[[np.ndarray], float],
                   iterations: int = 4000, seed: int = 2026091101) -> list[float | None]:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return [None, None]
    rng = np.random.default_rng(seed)
    draws = np.empty(iterations, dtype=np.float64)
    for index in range(iterations):
        draws[index] = statistic(rng.choice(values, size=values.size, replace=True))
    return [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]


def cvar_lower(values: np.ndarray, fraction: float = 0.05) -> float:
    values = np.sort(np.asarray(values, dtype=np.float64))
    values = values[np.isfinite(values)]
    if values.size == 0:
        return math.nan
    count = max(1, int(math.ceil(values.size * fraction)))
    return float(np.mean(values[:count]))


def recovery_filter(rows: list[dict[str, str]], method: str,
                    threshold_db: float) -> list[dict[str, str]]:
    """Return only primary recovery rows for one method and threshold."""
    return [
        row for row in rows
        if row.get("method") == method
        and row.get("split") == "test"
        and row.get("role") == "target_off"
        and finite(row.get("recoverable_headroom_db")) >= threshold_db
    ]


def recovery_summary(rows: list[dict[str, str]], method: str,
                     threshold_db: float) -> dict[str, Any]:
    material = recovery_filter(rows, method, threshold_db)
    active = [row for row in material if as_bool(row.get("active"))]
    values = np.asarray([finite(row.get("recovery_ratio")) for row in active],
                        dtype=np.float64)
    values = values[np.isfinite(values)]
    scene_ids = sorted({row.get("scene_id", "") for row in material})
    summary: dict[str, Any] = {
        "method": method,
        "split": "test",
        "role": "target_off",
        "headroom_threshold_db": threshold_db,
        "scene_count": len(scene_ids),
        "material_row_count": len(material),
        "active_count": len(active),
        "finite_recovery_count": int(values.size),
        "bootstrap_unit": "scene",
        "primary_definition": (
            "recovery statistics use split=test, role=target_off, "
            "recoverable_headroom_db>=threshold, and active finite recovery_ratio"
        ),
    }
    if values.size == 0:
        summary.update({
            "median": None, "mean": None, "p05": None, "p10": None,
            "p90": None, "minimum": None, "cvar5": None,
            "median_bootstrap_ci95": [None, None],
            "mean_bootstrap_ci95": [None, None],
        })
        return summary
    summary.update({
        "median": float(np.median(values)),
        "mean": float(np.mean(values)),
        "p05": float(np.quantile(values, 0.05)),
        "p10": float(np.quantile(values, 0.10)),
        "p90": float(np.quantile(values, 0.90)),
        "minimum": float(np.min(values)),
        "cvar5": cvar_lower(values),
        "median_bootstrap_ci95": bootstrap_stat(
            values, np.median, seed=2026091101 + int(threshold_db * 10)),
        "mean_bootstrap_ci95": bootstrap_stat(
            values, np.mean, seed=2026091201 + int(threshold_db * 10)),
    })
    return summary


def stratified_recovery(rows: list[dict[str, str]], method: str,
                        threshold_db: float) -> list[dict[str, Any]]:
    material = recovery_filter(rows, method, threshold_db)
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in material:
        key = (row.get("actual_mechanism", ""), row.get("branch", "") or "NONE")
        groups.setdefault(key, []).append(row)
    output: list[dict[str, Any]] = []
    for (family, branch), group in sorted(groups.items()):
        active = [row for row in group if as_bool(row.get("active"))]
        values = np.asarray([finite(row.get("recovery_ratio")) for row in active],
                            dtype=np.float64)
        values = values[np.isfinite(values)]
        item: dict[str, Any] = {
            "method": method,
            "family": family,
            "branch": branch,
            "headroom_threshold_db": threshold_db,
            "scene_count": len({row.get("scene_id", "") for row in group}),
            "material_row_count": len(group),
            "active_count": len(active),
            "finite_recovery_count": int(values.size),
        }
        if values.size:
            item.update({
                "median": float(np.median(values)),
                "mean": float(np.mean(values)),
                "p05": float(np.quantile(values, 0.05)),
                "p10": float(np.quantile(values, 0.10)),
                "p90": float(np.quantile(values, 0.90)),
                "minimum": float(np.min(values)),
                "cvar5": cvar_lower(values),
            })
        else:
            item.update({key: None for key in (
                "median", "mean", "p05", "p10", "p90", "minimum", "cvar5")})
        output.append(item)
    return output


def cfar_test_off_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows
            if row.get("split") == "test" and row.get("role") == "target_off"]


def paired_cfar_summary(rows: list[dict[str, str]], candidate: str,
                        metric: str, iterations: int = 4000) -> dict[str, Any]:
    current = {
        row["scene_id"]: finite(row.get(metric))
        for row in rows if row.get("method") == "J0_Current"
    }
    proposed = {
        row["scene_id"]: finite(row.get(metric))
        for row in rows if row.get("method") == candidate
    }
    scene_ids = sorted(set(current) & set(proposed))
    pairs = np.asarray([(proposed[scene] - current[scene]) for scene in scene_ids],
                       dtype=np.float64)
    pairs = pairs[np.isfinite(pairs)]
    result: dict[str, Any] = {
        "candidate": candidate,
        "metric": metric,
        "split": "test",
        "role": "target_off",
        "bootstrap_unit": "scene",
        "scene_count": int(pairs.size),
        "current_mean": None,
        "candidate_mean": None,
        "delta_mean_candidate_minus_current": None,
        "delta_median_candidate_minus_current": None,
        "delta_bootstrap_ci95": [None, None],
        "p_candidate_le_current": None,
        "interpretation": "no resolved difference",
    }
    if not pairs.size:
        return result
    current_values = np.asarray([current[scene] for scene in scene_ids], dtype=np.float64)
    candidate_values = np.asarray([proposed[scene] for scene in scene_ids], dtype=np.float64)
    result.update({
        "current_mean": float(np.mean(current_values)),
        "candidate_mean": float(np.mean(candidate_values)),
        "delta_mean_candidate_minus_current": float(np.mean(pairs)),
        "delta_median_candidate_minus_current": float(np.median(pairs)),
        "delta_bootstrap_ci95": bootstrap_stat(
            pairs, np.mean, iterations=iterations,
            seed=2026091301 + (
                METHODS.index(candidate) if candidate in METHODS else 0)),
        "p_candidate_le_current": float(np.mean(candidate_values <= current_values)),
    })
    low, high = result["delta_bootstrap_ci95"]
    if low is not None and low > 0.0:
        result["interpretation"] = "significant regression"
    elif high is not None and high < 0.0:
        result["interpretation"] = "significant improvement"
    return result


def rename_target_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["matched_target_on_pd"] = as_bool(item.pop("legacy_hit", False))
        item["pd_definition"] = "target-on ROI matched detection; not causal Pd"
        item["causal_pd_status"] = "not_available_from_V2_target_rows"
        output.append(item)
    return output


def run_reanalysis(formal_dir: Path, output_dir: Path) -> dict[str, Any]:
    formal_dir = formal_dir.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = formal_dir / "formal_matrix_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recovery = read_csv(formal_dir / "recovery_metrics.csv")
    cfar = read_csv(formal_dir / "production_cfar_rows.csv")
    target = read_csv(formal_dir / "target_rows.csv")
    scene_summary = read_csv(formal_dir / "scene_summary.csv")

    recovery_rows: list[dict[str, Any]] = []
    recovery_summaries: list[dict[str, Any]] = []
    stratified: list[dict[str, Any]] = []
    for method in SELECTIVE_METHODS:
        for threshold in HEADROOM_THRESHOLDS_DB:
            selected = recovery_filter(recovery, method, threshold)
            for row in selected:
                item = dict(row)
                item["primary_recovery"] = bool(
                    as_bool(row.get("active")) and
                    math.isfinite(finite(row.get("recovery_ratio"))))
                item["headroom_threshold_db"] = threshold
                recovery_rows.append(item)
            recovery_summaries.append(recovery_summary(recovery, method, threshold))
            stratified.extend(stratified_recovery(recovery, method, threshold))

    cfar_test = cfar_test_off_rows(cfar)
    paired = [
        paired_cfar_summary(cfar_test, method, metric)
        for method in METHODS[1:]
        for metric in ("pfa", "false_clusters")
    ]
    renamed_target = rename_target_rows(target)

    write_csv(output_dir / "recovery_test_only_target_off.csv", recovery_rows)
    write_csv(output_dir / "recovery_summary_test_only.csv", recovery_summaries)
    write_csv(output_dir / "recovery_stratified_test_only.csv", stratified)
    write_csv(output_dir / "production_pfa_target_off_only.csv", cfar_test)
    write_csv(output_dir / "paired_scene_delta_bootstrap.csv", paired)
    write_csv(output_dir / "target_rows_matched_target_on_pd.csv", renamed_target)
    (output_dir / "target_metric_relabeling.json").write_text(
        json.dumps({
            "legacy_hit": "matched_target_on_pd",
            "definition": "target-on ROI matched detection; not causal Pd",
            "causal_pd": "not available from V2 target_rows because V2 did not retain paired off-hit per target",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = {
        "schema_version": 1,
        "analysis": "V2 Final-Gate exploratory reanalysis",
        "formal_dir": str(formal_dir),
        "source_commit": manifest.get("source_commit"),
        "source_worktree_dirty": manifest.get("worktree_dirty"),
        "ai_training": False,
        "final_gate_status": "UNRESOLVED",
        "gate_interpretation": (
            "V2 is development evidence because target-preservation was added/changed after Test-V2; "
            "this reanalysis does not authorize AI training or make a confirmatory claim"
        ),
        "posthoc_metric_gate_fixes": ["6a8f05d", "4d777b3", "1445f3f"],
        "inputs": {
            "recovery_metrics": str((formal_dir / "recovery_metrics.csv").resolve()),
            "production_cfar_rows": str((formal_dir / "production_cfar_rows.csv").resolve()),
            "target_rows": str((formal_dir / "target_rows.csv").resolve()),
            "scene_summary": str((formal_dir / "scene_summary.csv").resolve()),
            "formal_manifest": str(manifest_path.resolve()),
        },
        "recovery_protocol": {
            "primary_filter": "split=test AND role=target_off",
            "excluded_from_primary": ["validation", "target_on"],
            "headroom_thresholds_db": list(HEADROOM_THRESHOLDS_DB),
            "summary_file": "recovery_summary_test_only.csv",
            "stratified_file": "recovery_stratified_test_only.csv",
        },
        "production_cfar_protocol": {
            "primary_filter": "split=test AND role=target_off",
            "bootstrap_unit": "scene",
            "paired_difference_file": "paired_scene_delta_bootstrap.csv",
            "candidate_methods": list(METHODS[1:]),
        },
        "target_metric_protocol": {
            "renamed_file": "target_rows_matched_target_on_pd.csv",
            "matched_target_on_pd": "former legacy_hit; target-on ROI matched detection",
            "causal_pd": "not available from V2 target_rows because Phase B paired off-hit is not present",
        },
        "counts": {
            "recovery_input_rows": len(recovery),
            "recovery_primary_rows": len(recovery_rows),
            "cfar_input_rows": len(cfar),
            "cfar_test_target_off_rows": len(cfar_test),
            "target_input_rows": len(target),
            "scene_summary_rows": len(scene_summary),
        },
        "recovery_summary": recovery_summaries,
        "paired_cfar_summary": paired,
    }
    (output_dir / "reanalysis_manifest.json").write_text(
        json.dumps(json_safe(result), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-dir", type=Path, default=DEFAULT_FORMAL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run_reanalysis(args.formal_dir, args.output_dir)
    print(json.dumps({
        "output_dir": str(Path(args.output_dir).resolve()),
        "cfar_test_target_off_rows": result["counts"]["cfar_test_target_off_rows"],
        "recovery_primary_rows": result["counts"]["recovery_primary_rows"],
        "ai_training": False,
        "final_gate_status": "UNRESOLVED",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
