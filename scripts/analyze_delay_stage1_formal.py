#!/usr/bin/env python3
"""Build the small, reviewable evidence package for delay Stage-1 runs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _ROOT_FOR_IMPORT = Path(__file__).resolve().parents[1]
    if str(_ROOT_FOR_IMPORT) not in sys.path:
        sys.path.insert(0, str(_ROOT_FOR_IMPORT))

from scripts.audit_delay_track_id_switch import (
    AUDIT_COLUMNS,
    audit_track_id_switches,
    classification_counts,
    missing_production_field_counts,
)
from scripts.delay_stage1_core import (
    paired_bootstrap_ci,
    paired_mcnemar_exact,
)


ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = (
    "A0_Ideal",
    "A1_Current_unknown_error",
    "A2_Known_error_correction_upper_bound",
    "A3_Blind_target_free_estimated_correction",
)
REQUIRED_OUTPUTS = (
    "manifest.json",
    "delay_estimation_summary.csv",
    "delay_baseline_comparison.csv",
    "A0_A1_A2_A3_summary.csv",
    "target_off_false_alarm_summary.csv",
    "target_on_detection_summary.csv",
    "track_summary.csv",
    "id_switch_audit.csv",
    "statistics_summary.csv",
)
_HIGHER_IS_BETTER = {
    "target_detection_pd",
    "period_detection_pd",
    "track_pd_all_visible",
    "track_pd_after_confirmation",
    "track_continuity",
    "track_continuity_after_confirmation",
    "target_detection_hit_count",
}
_LOWER_IS_BETTER = {
    "position_rmse_m",
    "velocity_rmse_mps_ground_speed",
    "angle_rmse_deg",
    "false_track_count",
    "unique_false_track_count",
    "false_clusters",
    "protocol_false_detections",
    "detection_record_false_alarm_rate",
    "track_id_switch_count",
}
_ON_METRICS = (
    "target_detection_pd",
    "period_detection_pd",
    "track_pd_all_visible",
    "track_pd_after_confirmation",
    "track_continuity",
    "track_continuity_after_confirmation",
    "position_rmse_m",
    "velocity_rmse_mps_ground_speed",
    "angle_rmse_deg",
)
_TRACK_METRICS = (
    "track_pd_all_visible",
    "track_pd_after_confirmation",
    "track_continuity",
    "track_continuity_after_confirmation",
    "position_rmse_m",
    "velocity_rmse_mps_ground_speed",
    "angle_rmse_deg",
    "false_track_count",
    "unique_false_track_count",
    "track_id_switch_count",
)


def _safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON manifest must be an object: {path}")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _finite(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metric_direction(metric: str) -> str:
    if metric in _LOWER_IS_BETTER:
        return "lower_is_better"
    return "higher_is_better"


def _metric_definition(metric: str) -> str:
    if metric in _LOWER_IS_BETTER:
        return f"{metric}; lower values are better; transformed_value=-value for recovery"
    return f"{metric}; higher values are better"


def compute_condition_summary(
    rows: Sequence[Mapping[str, object]],
    condition_names: Sequence[str],
) -> list[dict[str, object]]:
    """Normalize values/status/direction without promoting missing values."""

    allowed = {str(name) for name in condition_names}
    output: list[dict[str, object]] = []
    for row in rows:
        condition = str(row.get("condition", ""))
        if allowed and condition not in allowed:
            continue
        metric = str(row.get("metric", ""))
        value = _finite(row.get("value"))
        status = str(row.get("status", "")) or ("passed" if value is not None else "NOT_EVALUABLE")
        denominator = row.get("denominator")
        denominator_value = _finite(denominator)
        if denominator is not None and denominator_value is not None and denominator_value <= 0.0:
            status = "NOT_EVALUABLE"
            value = None
        if value is None and status.lower() in {"ok", "passed", "pass", "complete"}:
            status = "NOT_EVALUABLE"
        direction = _metric_direction(metric)
        output.append({
            **{str(key): row[key] for key in row},
            "condition": condition,
            "metric": metric,
            "value": value,
            "status": status,
            "denominator": denominator,
            "definition": str(row.get("definition") or _metric_definition(metric)),
            "metric_direction": direction,
            "transformed_value": (
                value if direction == "higher_is_better" else -value
            ) if value is not None else None,
        })
    return output


def _materiality(
    status: str,
    ci_low: float | None,
    ci_high: float | None,
) -> str:
    if status == "NOT_EVALUABLE":
        return "NOT_EVALUABLE"
    if ci_low is not None and ci_high is not None and ci_low <= 0.0 <= ci_high:
        return "indeterminate_ci_crosses_zero"
    return "indeterminate_no_config_effect_threshold"


def compute_paired_statistics(scene_rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Compute paired binary/continuous statistics by scene block."""

    groups: dict[tuple[str, str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in scene_rows:
        metric = str(row.get("metric", ""))
        comparison = str(row.get("comparison", "A3"))
        kind = "binary" if "current_hit" in row and "comparison_hit" in row else "continuous"
        groups[(kind, metric, comparison)].append(row)
    output: list[dict[str, object]] = []
    for (kind, metric, comparison), rows in sorted(groups.items()):
        block_ids = [row.get("block_id", row.get("scene_id", index)) for index, row in enumerate(rows)]
        if kind == "binary":
            current_hits = [bool(row.get("current_hit")) for row in rows]
            comparison_hits = [bool(row.get("comparison_hit")) for row in rows]
            result = paired_mcnemar_exact(current_hits, comparison_hits)
            current_rate = sum(current_hits) / len(current_hits) if current_hits else None
            comparison_rate = sum(comparison_hits) / len(comparison_hits) if comparison_hits else None
            effect = (
                comparison_rate - current_rate
                if current_rate is not None and comparison_rate is not None else None
            )
            output.append({
                "metric": metric,
                "comparison": comparison,
                "test": "mcnemar_exact",
                "status": result["status"],
                "effect_size": effect,
                "ci_low": None,
                "ci_high": None,
                "p_value": result.get("p_value_two_sided"),
                "block_count": result.get("n_pairs", 0),
                "discordant_current_miss_comparison_hit": result.get("discordant_current_miss_comparison_hit"),
                "discordant_current_hit_comparison_miss": result.get("discordant_current_hit_comparison_miss"),
                "materiality": _materiality(str(result["status"]), None, None),
                "difference_definition": "comparison hit rate minus Current hit rate by paired scene block",
            })
            continue
        values_a: list[float] = []
        values_b: list[float] = []
        usable_blocks: list[object] = []
        direction = "higher_is_better"
        for row, block_id in zip(rows, block_ids):
            a = _finite(row.get("current_value"))
            b = _finite(row.get("comparison_value"))
            direction = str(row.get("metric_direction", direction))
            if a is None or b is None:
                continue
            if direction == "lower_is_better":
                a, b = -a, -b
            values_a.append(a)
            values_b.append(b)
            usable_blocks.append(block_id)
        if not values_a:
            output.append({
                "metric": metric,
                "comparison": comparison,
                "test": "paired_scene_block_bootstrap",
                "status": "NOT_EVALUABLE",
                "effect_size": None,
                "ci_low": None,
                "ci_high": None,
                "p_value": None,
                "block_count": 0,
                "materiality": "NOT_EVALUABLE",
                "difference_definition": "transformed comparison minus transformed Current by scene block",
            })
            continue
        result = paired_bootstrap_ci(values_a, values_b, usable_blocks, trials=2000, seed=20260916)
        ci_low = _finite(result.get("ci95_low"))
        ci_high = _finite(result.get("ci95_high"))
        status = str(result.get("status", "NOT_EVALUABLE"))
        output.append({
            "metric": metric,
            "comparison": comparison,
            "test": "paired_scene_block_bootstrap",
            "status": status,
            "effect_size": result.get("observed_difference"),
            "ci_low": ci_low,
            "ci_high": ci_high,
            "p_value": None,
            "block_count": result.get("n_blocks", 0),
            "materiality": _materiality(status, ci_low, ci_high),
            "metric_direction": direction,
            "difference_definition": "transformed comparison minus transformed Current by scene block",
        })
    return output


def _write_rows(path: Path, rows: Sequence[Mapping[str, object]], fieldnames: Sequence[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = [str(value) for value in (fieldnames or [])]
    for row in rows:
        for key in row:
            if str(key) not in fields:
                fields.append(str(key))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            materialized = {}
            for key in fields:
                value = row.get(key)
                if isinstance(value, (dict, list, tuple)):
                    value = json.dumps(_safe(value), ensure_ascii=False, sort_keys=True)
                elif value is None:
                    value = ""
                materialized[key] = value
            writer.writerow(materialized)


def _case_manifest_paths(run_manifest: Mapping[str, object]) -> list[Path]:
    result: list[Path] = []
    for item in run_manifest.get("cases", []):
        if not isinstance(item, Mapping):
            continue
        value = item.get("case_manifest")
        if isinstance(value, str) and Path(value).is_file():
            result.append(Path(value).resolve())
    return result


def _case_context(case_manifest: Mapping[str, object]) -> dict[str, object]:
    identity = case_manifest.get("scene_identity")
    return dict(identity) if isinstance(identity, Mapping) else {}


def _condition_branch(
    case_manifest: Mapping[str, object],
    condition: str,
    role: str,
) -> Mapping[str, object]:
    conditions = case_manifest.get("conditions")
    if not isinstance(conditions, Mapping):
        return {}
    info = conditions.get(condition)
    if not isinstance(info, Mapping):
        return {}
    production = info.get("production")
    if not isinstance(production, Mapping):
        return {}
    branch = production.get(role)
    return branch if isinstance(branch, Mapping) else {}


def _scene_id(case_manifest: Mapping[str, object], case_path: Path) -> str:
    identity = _case_context(case_manifest)
    return str(identity.get("case_id") or case_path.parent.name)


def _extract_case_rows(
    case_path: Path,
    case_manifest: Mapping[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    identity = _case_context(case_manifest)
    scene_id = _scene_id(case_manifest, case_path)
    common = {
        "scene_id": scene_id,
        "working_point": identity.get("working_point"),
        "seed": identity.get("seed"),
        "delay_error_ns": identity.get("delay_error_ns"),
        "target_velocity_mps": identity.get("target_velocity_mps"),
        "target_snr_db": identity.get("target_snr_db"),
    }
    estimates: list[dict[str, object]] = []
    estimator = case_manifest.get("delay_estimator")
    if isinstance(estimator, Mapping):
        for row in estimator.get("method_rows", []):
            if isinstance(row, Mapping):
                estimate = _finite(row.get("delta_tau_ns"))
                truth = _finite(identity.get("delay_error_ns"))
                estimates.append({
                    **common,
                    "level": "level2",
                    "method": row.get("method"),
                    "status": row.get("status"),
                    "estimate_ns": estimate,
                    "truth_ns": truth,
                    "error_ns": estimate - truth if estimate is not None and truth is not None else None,
                    "runtime_sec": row.get("runtime_sec"),
                    "fallback_reason": row.get("fallback_reason"),
                })

    a0_rows: list[dict[str, object]] = []
    off_rows: list[dict[str, object]] = []
    on_rows: list[dict[str, object]] = []
    track_rows: list[dict[str, object]] = []
    audit_rows: list[dict[str, object]] = []
    for condition in CONDITIONS:
        on_branch = _condition_branch(case_manifest, condition, "ON")
        off_branch = _condition_branch(case_manifest, condition, "OFF")
        on_metrics = on_branch.get("metrics") if isinstance(on_branch.get("metrics"), Mapping) else {}
        off_waterfall = off_branch.get("target_off_waterfall")
        for metric in _ON_METRICS:
            value = _finite(on_metrics.get(metric))
            status = "passed" if value is not None else str(on_metrics.get(f"{metric}_status", "NOT_EVALUABLE"))
            on_rows.append({
                **common,
                "condition": condition,
                "role": "ON",
                "metric": metric,
                "value": value,
                "status": status,
                "denominator": on_metrics.get("target_detection_opportunity_count") if metric in {"target_detection_pd", "period_detection_pd"} else None,
                "definition": on_metrics.get("target_detection_definition", _metric_definition(metric)),
            })
        for metric in _TRACK_METRICS:
            value = _finite(on_metrics.get(metric))
            status = "passed" if value is not None else str(on_metrics.get(f"{metric}_status", "NOT_EVALUABLE"))
            track_rows.append({
                **common,
                "condition": condition,
                "role": "ON",
                "metric": metric,
                "value": value,
                "status": status,
                "denominator": on_metrics.get("truth_visible_periods") if metric.startswith("track_pd") else None,
                "definition": _metric_definition(metric),
            })
        if isinstance(off_waterfall, Mapping):
            for layer in ("cell_false_hit_fraction", "false_clusters", "protocol_false_detections", "false_tracks"):
                payload = off_waterfall.get(layer)
                if not isinstance(payload, Mapping):
                    payload = {}
                off_rows.append({
                    **common,
                    "condition": condition,
                    "role": "OFF",
                    "layer": layer,
                    "value": payload.get("value"),
                    "status": payload.get("status", "NOT_EVALUABLE"),
                    "denominator": payload.get("denominator"),
                    "definition": payload.get("definition") or off_waterfall.get("layer_definitions", {}).get(layer, "") if isinstance(off_waterfall.get("layer_definitions"), Mapping) else "",
                    "reason": payload.get("reason"),
                    "theoretical_go_cfar_cell_pfa": off_waterfall.get("theoretical_go_cfar_cell_pfa"),
                })
        for metric in _ON_METRICS:
            values: dict[str, float | None] = {}
            statuses: dict[str, str] = {}
            for candidate in CONDITIONS:
                branch = _condition_branch(case_manifest, candidate, "ON")
                metrics = branch.get("metrics") if isinstance(branch.get("metrics"), Mapping) else {}
                values[candidate] = _finite(metrics.get(metric))
                statuses[candidate] = "passed" if values[candidate] is not None else str(metrics.get(f"{metric}_status", "NOT_EVALUABLE"))
            direction = _metric_direction(metric)
            transformed = {
                condition_name: (
                    values[condition_name]
                    if direction == "higher_is_better"
                    else -values[condition_name]
                ) if values[condition_name] is not None else None
                for condition_name in CONDITIONS
            }
            recoverable = (
                transformed["A2_Known_error_correction_upper_bound"]
                - transformed["A1_Current_unknown_error"]
                if transformed["A2_Known_error_correction_upper_bound"] is not None
                and transformed["A1_Current_unknown_error"] is not None else None
            )
            blind_recovery = (
                transformed["A3_Blind_target_free_estimated_correction"]
                - transformed["A1_Current_unknown_error"]
                if transformed["A3_Blind_target_free_estimated_correction"] is not None
                and transformed["A1_Current_unknown_error"] is not None else None
            )
            a0_gap = (
                transformed["A1_Current_unknown_error"] - transformed["A0_Ideal"]
                if transformed["A1_Current_unknown_error"] is not None
                and transformed["A0_Ideal"] is not None else None
            )
            recovery_ratio = (
                blind_recovery / recoverable
                if recoverable is not None and blind_recovery is not None and recoverable != 0.0 else None
            )
            status = "passed"
            if recoverable is None or blind_recovery is None:
                status = "NOT_EVALUABLE"
            elif recoverable == 0.0:
                status = "NOT_EVALUABLE"
            a0_rows.append({
                **common,
                "metric": metric,
                "metric_direction": direction,
                "A0_value": values["A0_Ideal"],
                "A0_status": statuses["A0_Ideal"],
                "A1_value": values["A1_Current_unknown_error"],
                "A1_status": statuses["A1_Current_unknown_error"],
                "A2_value": values["A2_Known_error_correction_upper_bound"],
                "A2_status": statuses["A2_Known_error_correction_upper_bound"],
                "A3_value": values["A3_Blind_target_free_estimated_correction"],
                "A3_status": statuses["A3_Blind_target_free_estimated_correction"],
                "A1_minus_A0_transformed": a0_gap,
                "recoverable_space_transformed": recoverable,
                "blind_recovery_transformed": blind_recovery,
                "recovery_ratio": recovery_ratio,
                "status": status,
                "definition": _metric_definition(metric) + "; recoverable=A2-A1 and blind=A3-A1 after direction transform",
            })

        # Reuse an already-recorded branch audit when available.  For older
        # manifests, derive it from the same production debug artifacts so the
        # compact package can audit the completed v3 pilot without rerunning it.
        audit_path = on_branch.get("id_switch_audit_path")
        branch_audits: list[dict[str, str]] = []
        if isinstance(audit_path, str) and Path(audit_path).is_file():
            branch_audits = _read_csv(Path(audit_path))
        else:
            debug = on_branch.get("track_debug_dir")
            result_dir = on_branch.get("result_dir")
            simulation = case_manifest.get("simulation")
            a0_sim = simulation.get("A0_ON") if isinstance(simulation, Mapping) else None
            a0_output = a0_sim.get("output_dir") if isinstance(a0_sim, Mapping) else None
            if isinstance(debug, str) and isinstance(result_dir, str) and isinstance(a0_output, str):
                payload = Path(debug) / "track_output_payloads.csv"
                try:
                    branch_audits = [
                        {str(key): str(value) for key, value in row.items()}
                        for row in audit_track_id_switches(
                            Path(debug),
                            Path(result_dir),
                            payload,
                            Path(a0_output) / "truth",
                        )
                    ]
                except (OSError, ValueError, RuntimeError, KeyError):
                    branch_audits = []
        for row in branch_audits:
            audit_rows.append({
                **row,
                "scene_id": scene_id,
                "condition": condition,
                "working_point": identity.get("working_point"),
                "seed": identity.get("seed"),
                "delay_error_ns": identity.get("delay_error_ns"),
            })
    return estimates, a0_rows, off_rows, on_rows, track_rows, audit_rows


def _source_gap_rows(
    a0_rows: Sequence[Mapping[str, object]],
    off_rows: Sequence[Mapping[str, object]],
    on_rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    gaps: list[dict[str, object]] = []
    for row in [*a0_rows, *off_rows, *on_rows]:
        if str(row.get("status", "")).upper() == "NOT_EVALUABLE":
            gaps.append({
                "scene_id": row.get("scene_id"),
                "condition": row.get("condition"),
                "metric": row.get("metric", row.get("layer")),
                "reason": row.get("reason") or row.get("definition"),
            })
    return gaps


def build_compact_evidence(run_manifest: Path, output_dir: Path) -> dict[str, Path]:
    """Write exactly the nine requested compact files and no raw inputs."""

    source_path = Path(run_manifest).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"run manifest does not exist: {source_path}")
    source = _read_json(source_path)
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        if not destination.is_dir():
            raise ValueError(f"compact evidence output is not a directory: {destination}")
        if any(destination.iterdir()):
            raise ValueError(f"refuse to overwrite non-empty compact evidence: {destination}")
    else:
        destination.mkdir(parents=True, exist_ok=False)

    estimates: list[dict[str, object]] = []
    a0_rows: list[dict[str, object]] = []
    off_rows: list[dict[str, object]] = []
    on_rows: list[dict[str, object]] = []
    track_rows: list[dict[str, object]] = []
    audit_rows: list[dict[str, object]] = []
    for case_path in _case_manifest_paths(source):
        case = _read_json(case_path)
        parts = _extract_case_rows(case_path, case)
        estimates.extend(parts[0])
        a0_rows.extend(parts[1])
        off_rows.extend(parts[2])
        on_rows.extend(parts[3])
        track_rows.extend(parts[4])
        audit_rows.extend(parts[5])

    mc_rows = []
    mc_summary = source.get("mc_summary")
    if isinstance(mc_summary, Mapping) and isinstance(mc_summary.get("rows"), list):
        mc_rows = [dict(row) for row in mc_summary["rows"] if isinstance(row, Mapping)]
        for row in mc_rows:
            row["level"] = "level1"
            row["estimate_ns"] = row.get("bias_ns")
            row["truth_ns"] = row.get("delay_error_ns")
            if _finite(row.get("bias_ns")) is not None and _finite(row.get("delay_error_ns")) is not None:
                # MC bias is already estimate minus truth; retain it as a
                # bias field and do not re-interpret it as a single estimate.
                row["error_ns"] = row.get("bias_ns")
    all_estimates = [*estimates, *mc_rows]

    binary_rows: list[dict[str, object]] = []
    continuous_rows: list[dict[str, object]] = []
    by_scene_condition: dict[tuple[str, str], dict[str, object]] = {}
    for row in on_rows:
        scene = str(row.get("scene_id", ""))
        condition = str(row.get("condition", ""))
        by_scene_condition.setdefault((scene, condition), {})[str(row.get("metric"))] = row
    scenes = sorted({scene for scene, _condition in by_scene_condition})
    for scene in scenes:
        current = by_scene_condition.get((scene, "A1_Current_unknown_error"), {})
        for comparison in ("A2_Known_error_correction_upper_bound", "A3_Blind_target_free_estimated_correction"):
            candidate = by_scene_condition.get((scene, comparison), {})
            current_pd = current.get("target_detection_pd", {})
            candidate_pd = candidate.get("target_detection_pd", {})
            current_value = _finite(current_pd.get("value")) if isinstance(current_pd, Mapping) else None
            candidate_value = _finite(candidate_pd.get("value")) if isinstance(candidate_pd, Mapping) else None
            if current_value is not None and candidate_value is not None:
                binary_rows.append({
                    "metric": "target_detection_full_scene_hit",
                    "comparison": comparison,
                    "block_id": scene,
                    "scene_id": scene,
                    "current_hit": current_value >= 1.0,
                    "comparison_hit": candidate_value >= 1.0,
                })
            for metric in ("position_rmse_m", "velocity_rmse_mps_ground_speed", "angle_rmse_deg"):
                a = current.get(metric, {})
                b = candidate.get(metric, {})
                continuous_rows.append({
                    "metric": metric,
                    "comparison": comparison,
                    "block_id": scene,
                    "scene_id": scene,
                    "current_value": a.get("value") if isinstance(a, Mapping) else None,
                    "comparison_value": b.get("value") if isinstance(b, Mapping) else None,
                    "metric_direction": _metric_direction(metric),
                })
    stats_rows = compute_paired_statistics([*binary_rows, *continuous_rows])

    paths = {name: destination / name for name in REQUIRED_OUTPUTS}
    _write_rows(paths["delay_estimation_summary.csv"], all_estimates)
    baseline_rows = [
        {
            "level": row.get("level"),
            "delay_error_ns": row.get("delay_error_ns", row.get("truth_ns")),
            "snr_db": row.get("snr_db"),
            "method": row.get("method"),
            "method_family": "spectral_D1_D2_D3" if str(row.get("method", "")).startswith("D") else "traditional_baseline",
            "bias_ns": row.get("bias_ns", row.get("error_ns")),
            "rmse_ns": row.get("rmse_ns"),
            "std_ns": row.get("std_ns"),
            "ci95_low_ns": row.get("ci95_low_ns"),
            "ci95_high_ns": row.get("ci95_high_ns"),
            "fallback_rate": row.get("fallback_rate"),
            "mean_runtime_sec": row.get("mean_runtime_sec", row.get("runtime_sec")),
            "trials": row.get("trials"),
        }
        for row in mc_rows
    ]
    _write_rows(paths["delay_baseline_comparison.csv"], baseline_rows)
    _write_rows(paths["A0_A1_A2_A3_summary.csv"], a0_rows)
    _write_rows(paths["target_off_false_alarm_summary.csv"], off_rows)
    _write_rows(paths["target_on_detection_summary.csv"], on_rows)
    _write_rows(paths["track_summary.csv"], track_rows)
    _write_rows(paths["id_switch_audit.csv"], audit_rows, [*AUDIT_COLUMNS, "condition", "working_point", "seed", "delay_error_ns"])
    _write_rows(paths["statistics_summary.csv"], stats_rows)

    gaps = _source_gap_rows(a0_rows, off_rows, on_rows)
    compact_manifest: dict[str, object] = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_run_manifest": str(source_path),
        "source_run_manifest_sha256": _sha256(source_path),
        "source_status": source.get("status"),
        "source_mode": source.get("mode"),
        "source_command": source.get("command"),
        "source_config": source.get("config"),
        "source_template": source.get("template"),
        "source_git": source.get("git"),
        "source_git_after": source.get("git_after"),
        "gpu_status_before": source.get("gpu_status_before"),
        "gpu_status_after": source.get("gpu_status_after"),
        "disk_status_before": source.get("disk_status_before"),
        "disk_status_after": source.get("disk_status_after"),
        "resolved": source.get("resolved"),
        "delay_range_labels": source.get("delay_range_labels"),
        "ai_training": source.get("ai_training", False),
        "router_enabled": source.get("router_enabled", False),
        "native_four_channel_stap": source.get("native_four_channel_stap", False),
        "raw_inputs_copied": False,
        "row_counts": {
            "delay_estimation": len(all_estimates),
            "baseline": len(baseline_rows),
            "A0_A1_A2_A3": len(a0_rows),
            "target_off": len(off_rows),
            "target_on": len(on_rows),
            "track": len(track_rows),
            "id_switch": len(audit_rows),
            "statistics": len(stats_rows),
        },
        "id_switch_classification_counts": classification_counts(audit_rows),
        "id_switch_missing_production_field_counts": missing_production_field_counts(audit_rows),
        "unresolved_not_evaluable": gaps,
        "output_file_hashes": {
            name: _sha256(path)
            for name, path in paths.items()
            if name != "manifest.json"
        },
    }
    paths["manifest.json"].write_text(
        json.dumps(_safe(compact_manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return paths


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build compact channel-delay Stage-1 evidence.")
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        outputs = build_compact_evidence(args.run_manifest, args.output_root)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": "completed", "files": sorted(outputs)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "build_compact_evidence",
    "compute_condition_summary",
    "compute_paired_statistics",
]
