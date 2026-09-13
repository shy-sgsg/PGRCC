#!/usr/bin/env python3
"""Evaluate Router Opportunity materiality with equal-family weighting.

The evaluator is offline: it consumes compact Builder CSVs and never opens
raw scene/runtime files.  It does not authorize AI training.  Its only gate
state is ``REOPEN_ROUTER_RESEARCH`` until the later observable-only
learnability and training-authorization gates are independently proven.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def finite(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.floating, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    return value


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(json_safe(value), ensure_ascii=False, indent=2,
                   allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields,
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def weighted_quantile(values: list[float], weights: list[float], quantile: float) -> float:
    if len(values) != len(weights) or not values:
        return math.nan
    data = [(float(value), float(weight)) for value, weight in zip(values, weights)
            if math.isfinite(float(value)) and math.isfinite(float(weight))
            and float(weight) > 0.0]
    if not data:
        return math.nan
    data.sort(key=lambda item: item[0])
    x = np.asarray([item[0] for item in data], dtype=float)
    w = np.asarray([item[1] for item in data], dtype=float)
    cumulative = np.cumsum(w)
    target = min(max(float(quantile), 0.0), 1.0) * float(cumulative[-1])
    index = int(np.searchsorted(cumulative, target, side="left"))
    return float(x[min(index, x.size - 1)])


def _family_records(records: list[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in records:
        family = str(row.get("family", ""))
        if not family:
            raise ValueError("every headroom row requires a family label")
        groups[family].append(row)
    if not groups:
        raise ValueError("no finite family records")
    return dict(groups)


def equal_family_weights(records: list[Mapping[str, Any]]) -> list[float]:
    """Give every family total weight 1/N, regardless of oversampling."""

    groups = _family_records(records)
    family_weight = 1.0 / len(groups)
    return [family_weight / len(groups[str(row["family"])]) for row in records]


def equal_family_stats(records: list[Mapping[str, Any]], field: str,
                       threshold: float = 0.0,
                       threshold_inclusive: bool = True) -> dict[str, Any]:
    values = [finite(row.get(field)) for row in records]
    weights = equal_family_weights(records)
    finite_pairs = [(value, weight) for value, weight in zip(values, weights)
                    if math.isfinite(value)]
    if not finite_pairs:
        return {
            "count": 0, "family_count": len(_family_records(records)),
            "equal_family_weighted_mean": None, "median": None,
            "p10": None, "p90": None, "positive_rate": None,
        }
    valid_values = [item[0] for item in finite_pairs]
    valid_weights = [item[1] for item in finite_pairs]
    weight_sum = float(sum(valid_weights))
    if threshold_inclusive:
        positive_weight = sum(weight for value, weight in finite_pairs
                               if value >= threshold)
    else:
        positive_weight = sum(weight for value, weight in finite_pairs
                              if value > threshold)
    return {
        "count": len(valid_values),
        "family_count": len(_family_records(records)),
        "equal_family_weighted_mean": float(
            np.dot(np.asarray(valid_values), np.asarray(valid_weights)) / weight_sum),
        "median": weighted_quantile(valid_values, valid_weights, 0.50),
        "p10": weighted_quantile(valid_values, valid_weights, 0.10),
        "p90": weighted_quantile(valid_values, valid_weights, 0.90),
        "positive_rate": float(positive_weight / weight_sum),
    }


def bootstrap_equal_family_mean(records: list[Mapping[str, Any]], field: str,
                                iterations: int, seed: int) -> dict[str, Any]:
    groups = _family_records(records)
    rng = np.random.default_rng(seed)
    draws: list[float] = []
    for _ in range(iterations):
        family_means: list[float] = []
        for family in sorted(groups):
            values = np.asarray([finite(row.get(field)) for row in groups[family]], dtype=float)
            values = values[np.isfinite(values)]
            if values.size:
                sampled = rng.choice(values, size=values.size, replace=True)
                family_means.append(float(np.mean(sampled)))
        if family_means:
            draws.append(float(np.mean(family_means)))
    if not draws:
        return {"iterations": 0, "mean": None, "ci95": [None, None],
                "bootstrap_unit": "equal-family scene resampling"}
    data = np.asarray(draws, dtype=float)
    return {
        "iterations": int(data.size),
        "mean": float(np.mean(data)),
        "ci95": [float(np.quantile(data, 0.025)),
                 float(np.quantile(data, 0.975))],
        "bootstrap_unit": "sample scenes within each family, then weight families equally",
    }


def _action_rows_from_config(gate_config: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions = gate_config.get("action_space", {}).get("actions", [])
    if len(actions) != 6:
        raise ValueError("Router gate must contain exactly six frozen actions")
    expected = {
        "A0": ("J0_Current", 0.0),
        "A1": ("J5_Selective_Physics_Calibration", 0.5),
        "A2": ("J5_Selective_Physics_Calibration", 1.0),
        "A3": ("J6_Joint_Phase_Surface", 0.5),
        "A4": ("J6_Joint_Phase_Surface", 0.75),
        "A5": ("J6_Joint_Phase_Surface", 1.0),
    }
    for row in actions:
        action_id = str(row.get("id"))
        method, lambda_value = expected.get(action_id, (None, None))
        if method is None or str(row.get("method")) != method \
                or not math.isclose(float(row.get("lambda")), lambda_value):
            raise ValueError("router action space is not the frozen A0-A5 policy")
    if {str(row.get("id")) for row in actions} != set(expected):
        raise ValueError("router action IDs are incomplete")
    return [dict(row) for row in actions]


def build_headroom_rows(candidate_rows: list[Mapping[str, Any]],
                        selection_rows: list[Mapping[str, Any]],
                        material_threshold_db: float) -> list[dict[str, Any]]:
    """Derive one label row per scene and verify the stored selection."""

    material_threshold_db = float(material_threshold_db)
    if not math.isfinite(material_threshold_db):
        raise ValueError("material threshold must be finite")

    by_scene: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        by_scene[str(row.get("scene_id"))].append(row)
    selected_by_scene: dict[str, Mapping[str, Any]] = {}
    for row in selection_rows:
        scene_id = str(row.get("scene_id"))
        if scene_id in selected_by_scene:
            raise ValueError(f"duplicate selection row for {scene_id}")
        selected_by_scene[scene_id] = row
    action_rank = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4, "A5": 5}
    output: list[dict[str, Any]] = []
    for scene_id, rows in sorted(by_scene.items()):
        action_ids = [str(row.get("action_id")) for row in rows]
        if len(action_ids) != 6 or set(action_ids) != set(action_rank):
            raise ValueError(
                f"scene {scene_id} must contain exactly one frozen A0-A5 row")
        if len(set(action_ids)) != len(action_ids):
            raise ValueError(f"scene {scene_id} has duplicate action rows")
        current = [row for row in rows if str(row.get("action_id")) == "A0"]
        if len(current) != 1:
            raise ValueError(f"scene {scene_id} must contain one A0 row")
        current_cancellation = finite(current[0].get("cancellation_db"))
        if not math.isfinite(current_cancellation):
            raise ValueError(f"scene {scene_id} has non-finite Current cancellation")
        if not bool_value(current[0].get("safe")):
            raise ValueError(f"scene {scene_id} must mark A0 Current safe")
        safe = [row for row in rows if bool_value(row.get("safe"))]
        if not safe:
            raise ValueError(f"scene {scene_id} has no safe action including A0")
        best = max(
            safe,
            key=lambda row: (
                finite(row.get("cancellation_db"), -math.inf),
                -action_rank[str(row.get("action_id"))],
            ),
        )
        best_cancellation = finite(best.get("cancellation_db"))
        if not math.isfinite(best_cancellation):
            raise ValueError(f"scene {scene_id} has non-finite best safe cancellation")
        headroom = max(0.0, best_cancellation - current_cancellation)
        stored = selected_by_scene.get(scene_id)
        if stored is None:
            raise ValueError(f"missing selection row for {scene_id}")
        if str(stored.get("selected_action")) != str(best.get("action_id")):
            raise ValueError(
                f"selection mismatch for {scene_id}: stored={stored.get('selected_action')} "
                f"derived={best.get('action_id')}")
        stored_headroom = finite(stored.get("safe_headroom_db"))
        if math.isfinite(stored_headroom) and not math.isclose(
                stored_headroom, headroom, rel_tol=1.0e-9, abs_tol=1.0e-9):
            raise ValueError(
                f"headroom mismatch for {scene_id}: stored={stored_headroom} "
                f"derived={headroom}")
        output.append({
            "scene_id": scene_id,
            "family": str(best.get("family")),
            "current_cancellation_db": current_cancellation,
            "best_safe_cancellation_db": best_cancellation,
            "safe_headroom_db": headroom,
            "material_safe_opportunity": headroom >= material_threshold_db,
            "best_safe_action": str(best.get("action_id")),
            "best_safe_method": str(best.get("method")),
            "best_safe_lambda": finite(best.get("lambda")),
            "safe_action_count": sum(bool_value(row.get("safe")) for row in rows),
        })
    extra = set(selected_by_scene) - set(by_scene)
    if extra:
        raise ValueError(f"selection rows have unknown scenes: {sorted(extra)}")
    return output


def _conditional(records: list[Mapping[str, Any]], predicate: Any) -> list[dict[str, Any]]:
    selected = [dict(row) for row in records if predicate(str(row.get("family", "")))]
    if not selected:
        raise ValueError("conditional breakdown has no records")
    return selected


def family_breakdown(records: list[Mapping[str, Any]], thresholds: list[float],
                     positive_threshold: float, scope: str = "all") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for family, group in sorted(_family_records(records).items()):
        values = np.asarray([finite(row.get("safe_headroom_db")) for row in group], dtype=float)
        values = values[np.isfinite(values)]
        row: dict[str, Any] = {
            "scope": scope,
            "family": family,
            "scene_count": int(values.size),
            "safe_headroom_mean_db": float(np.mean(values)) if values.size else math.nan,
            "safe_headroom_median_db": float(np.median(values)) if values.size else math.nan,
            "safe_headroom_p10_db": float(np.percentile(values, 10)) if values.size else math.nan,
            "safe_headroom_p90_db": float(np.percentile(values, 90)) if values.size else math.nan,
            "positive_opportunity_rate": float(np.mean(values > positive_threshold))
            if values.size else math.nan,
        }
        for threshold in thresholds:
            row[f"p_headroom_ge_{threshold:g}_db"] = (
                float(np.mean(values >= threshold)) if values.size else math.nan)
        rows.append(row)
    return rows


def action_distribution(records: list[Mapping[str, Any]],
                        actions: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(str(row.get("best_safe_action")) for row in records)
    weights = equal_family_weights(records)
    weighted: Counter[str] = Counter()
    for row, weight in zip(records, weights):
        weighted[str(row.get("best_safe_action"))] += weight
    total = sum(weighted.values())
    by_id = {str(row["id"]): row for row in actions}
    output: list[dict[str, Any]] = []
    for action_id in by_id:
        row = by_id[action_id]
        output.append({
            "action_id": action_id,
            "method": row["method"],
            "lambda": row["lambda"],
            "selected_count": counts.get(action_id, 0),
            "selected_fraction_raw": counts.get(action_id, 0) / len(records),
            "selected_weight_equal_family": weighted.get(action_id, 0.0),
            "selected_fraction_equal_family": weighted.get(action_id, 0.0) / total
            if total else math.nan,
        })
    return output


def lambda_distribution(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Summarize the selected correction strength independently of method."""

    counts: Counter[float] = Counter()
    weights_by_lambda: Counter[float] = Counter()
    weights = equal_family_weights(records)
    for row, weight in zip(records, weights):
        value = finite(row.get("best_safe_lambda"))
        if not math.isfinite(value):
            raise ValueError(
                f"selected action has non-finite lambda: {row.get('scene_id')}")
        counts[value] += 1
        weights_by_lambda[value] += weight
    total = float(sum(weights_by_lambda.values()))
    return [
        {
            "lambda": value,
            "selected_count": counts[value],
            "selected_fraction_raw": counts[value] / len(records),
            "selected_weight_equal_family": weights_by_lambda[value],
            "selected_fraction_equal_family": weights_by_lambda[value] / total
            if total else math.nan,
        }
        for value in sorted(counts)
    ]


def _reference_development(path: Path, thresholds: list[float],
                           positive_threshold: float) -> dict[str, Any] | None:
    selection = path / "oracle_scene_selection.csv"
    if not selection.is_file():
        return None
    rows = read_csv(selection)
    manifest_path = path / "target_safe_oracle_v2_manifest.json"
    manifest_family_by_scene: dict[str, str] = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest.get("scene_records", []):
            spec = item.get("spec", {})
            scene_id = spec.get("scene_id", item.get("scene_id"))
            family = spec.get("label", item.get("family"))
            if scene_id is not None and family:
                manifest_family_by_scene[str(scene_id)] = str(family)
    records = [{
        "scene_id": row.get("scene_id"),
        "family": (row.get("family") or manifest_family_by_scene.get(
            str(row.get("scene_id")), "unknown")),
        "safe_headroom_db": finite(row.get("safe_oracle_headroom_db"), 0.0),
        "selected_method": row.get("selected_method", ""),
        "selected_lambda": finite(row.get("selected_lambda")),
    } for row in rows]
    stats = equal_family_stats(records, "safe_headroom_db", positive_threshold,
                               threshold_inclusive=False)
    classified = [row for row in records
                  if str(row["family"]).strip().lower() not in {"", "unknown"}]
    m2_records = [row for row in classified if "M2" in str(row["family"])]
    non_m2_records = [row for row in classified if "M2" not in str(row["family"])]
    total_headroom = sum(finite(row.get("safe_headroom_db"), 0.0)
                         for row in records)
    m2_headroom = sum(finite(row.get("safe_headroom_db"), 0.0)
                      for row in m2_records)
    return {
        "source": str(selection.resolve()),
        "scene_count": len(records),
        "stats": stats,
        "family_breakdown": family_breakdown(
            records, thresholds, positive_threshold, scope="development_reference"),
        "m2_containing_breakdown": family_breakdown(
            m2_records, thresholds, positive_threshold,
            scope="development_reference_M2-containing") if m2_records else [],
        "m2_concentration": {
            "classification_status": (
                "complete" if len(classified) == len(records)
                else "partial" if classified else "unavailable"),
            "classified_scene_count": len(classified),
            "m2_scene_fraction_raw": len(m2_records) / len(records)
            if classified and records else None,
            "m2_headroom_share_raw": m2_headroom / total_headroom
            if classified and total_headroom > 0.0 else None,
            "opportunity_definition": "safe_headroom_db > 0 dB",
            "opportunity_scene_count": sum(
                finite(row.get("safe_headroom_db"), 0.0) > positive_threshold
                for row in records),
            "m2_opportunity_scene_fraction_raw": (
                sum("M2" in str(row["family"])
                    and finite(row.get("safe_headroom_db"), 0.0) > positive_threshold
                    for row in m2_records) / sum(
                        finite(row.get("safe_headroom_db"), 0.0) > positive_threshold
                        for row in classified)
                if classified and any(
                    finite(row.get("safe_headroom_db"), 0.0) > positive_threshold
                    for row in classified) else None),
            "phase_method_opportunity_scene_fraction_raw": (
                sum(str(row.get("selected_method")) == "J6_Joint_Phase_Surface"
                    and finite(row.get("safe_headroom_db"), 0.0) > positive_threshold
                    for row in records) / sum(
                        finite(row.get("safe_headroom_db"), 0.0) > positive_threshold
                        for row in records)
                if any(finite(row.get("safe_headroom_db"), 0.0) > positive_threshold
                       for row in records) else None),
            "m2_equal_family_mean_db": (
                equal_family_stats(m2_records, "safe_headroom_db", positive_threshold)
                .get("equal_family_weighted_mean") if m2_records else None),
            "non_m2_equal_family_mean_db": (
                equal_family_stats(non_m2_records, "safe_headroom_db", positive_threshold)
                .get("equal_family_weighted_mean") if non_m2_records else None),
        },
        "selected_method_distribution": dict(Counter(
            str(row.get("selected_method")) for row in records
            if row.get("selected_method"))),
        "threshold_probabilities": {
            str(threshold): equal_family_stats(
                [{"family": row["family"], "value": row["safe_headroom_db"]}
                 for row in records], "value", threshold)["positive_rate"]
            for threshold in thresholds
        },
    }


def evaluate_materiality(candidate_rows: list[Mapping[str, Any]],
                         selection_rows: list[Mapping[str, Any]],
                         config: Mapping[str, Any],
                         gate_config: Mapping[str, Any],
                         development_reference: Path | None = None) -> dict[str, Any]:
    actions = _action_rows_from_config(gate_config)
    if config.get("safety") != gate_config.get("safety"):
        raise ValueError(
            "Router Opportunity safety policy differs from frozen router gate")
    materiality = config["materiality"]
    thresholds = [float(value) for value in materiality["sensitivity_db"]]
    primary = float(materiality["primary_threshold_db"])
    label_threshold = float(config["learnability"]["material_label_threshold_db"])
    if not math.isclose(primary, label_threshold, rel_tol=0.0, abs_tol=1.0e-12):
        raise ValueError(
            "materiality primary threshold and Y1 label threshold must match")
    records = build_headroom_rows(candidate_rows, selection_rows, primary)
    positive_threshold = float(materiality["positive_threshold_db"])
    stats = equal_family_stats(records, "safe_headroom_db", positive_threshold,
                               threshold_inclusive=False)
    probabilities = {
        str(threshold): equal_family_stats(
            [{"family": row["family"], "value": row["safe_headroom_db"]}
                 for row in records], "value", threshold,
            threshold_inclusive=True)["positive_rate"]
        for threshold in thresholds
    }
    positive = [row for row in records
                if finite(row.get("safe_headroom_db")) > positive_threshold]
    positive_stats = equal_family_stats(positive, "safe_headroom_db", positive_threshold) \
        if positive else {"count": 0, "equal_family_weighted_mean": None, "median": None}
    bootstrap = bootstrap_equal_family_mean(
        records, "safe_headroom_db", int(materiality["bootstrap_iterations"]),
        int(materiality["bootstrap_seed"]))
    early = materiality["early_stop"]
    early_stop = bool(
        stats["equal_family_weighted_mean"] is not None
        and stats["equal_family_weighted_mean"] < float(early["mean_safe_headroom_lt_db"])
        and probabilities.get("0.1", probabilities.get("0.10", 0.0))
        < float(early["p_headroom_ge_0_10_max"]))
    primary_mean = finite(stats.get("equal_family_weighted_mean"))
    materiality_passed = bool(math.isfinite(primary_mean) and primary_mean >= primary)
    if early_stop:
        decision = str(early["decision"])
    elif materiality_passed:
        decision = "MATERIALITY_GATE_PASS_CONTINUE_LEARNABILITY_AUDIT"
    elif len(records) < int(config["max_scene_count"]):
        decision = "CONTINUE_ROUTER_OPPORTUNITY_EXPANSION"
    else:
        decision = "NO_GO_AI_ROUTER_VALUE"
    return {
        "schema_version": 1,
        "analysis": "Router Opportunity Materiality Gate",
        "router_state": "REOPEN_ROUTER_RESEARCH",
        "training_state": "GO_AI_ROUTER_TRAINING",
        "ai_training": False,
        "training_authorized": False,
        "decision": decision,
        "materiality_gate_passed": materiality_passed,
        "early_stop": early_stop,
        "scene_count": len(records),
        "family_count": len(_family_records(records)),
        "overall_equal_family_weighted": stats,
        "bootstrap": bootstrap,
        "positive_subset": {
            "threshold_db": positive_threshold,
            "mean_db": positive_stats.get("equal_family_weighted_mean"),
            "median_db": positive_stats.get("median"),
            "scene_count": positive_stats.get("count", 0),
        },
        "threshold_probabilities": probabilities,
        "selected_action_distribution": action_distribution(records, actions),
        "selected_lambda_distribution": lambda_distribution(records),
        "family_breakdown": family_breakdown(records, thresholds, positive_threshold),
        "m2_containing_breakdown": family_breakdown(
            _conditional(records, lambda family: "M2" in family),
            thresholds, positive_threshold, scope="M2-containing"),
        "development_reference": (
            _reference_development(development_reference, thresholds, positive_threshold)
            if development_reference is not None else None),
        "policy": {
            "material_safe_headroom_primary_db": primary,
            "sensitivity_db": thresholds,
            "equal_family_weighting": True,
            "selection": "safe candidate first, then maximum cancellation; A0 Current is identity baseline",
        },
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/router_opportunity_v1")
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs/research/router_opportunity_v1.json")
    parser.add_argument("--gate-config", type=Path,
                        default=ROOT / "configs/research/physics_ai_router_gate_v1.json")
    parser.add_argument("--development-reference", type=Path,
                        default=ROOT / "outputs/target_safe_oracle_v2")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    config_path = args.config.resolve()
    gate_path = args.gate_config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    gate_config = json.loads(gate_path.read_text(encoding="utf-8"))
    candidate_path = output / "router_candidate_metrics.csv"
    selection_path = output / "router_scene_selection.csv"
    if not candidate_path.is_file() or not selection_path.is_file():
        raise SystemExit("Builder candidate and selection CSVs are required")
    candidate_rows = read_csv(candidate_path)
    selection_rows = read_csv(selection_path)
    result = evaluate_materiality(
        candidate_rows, selection_rows, config, gate_config,
        args.development_reference.resolve()
        if args.development_reference else None)
    write_json(output / "router_materiality_summary.json", result)
    write_csv(output / "router_headroom_rows.csv", result["records"])
    write_csv(output / "router_family_breakdown.csv",
              result["family_breakdown"] + result["m2_containing_breakdown"])
    write_csv(output / "router_action_distribution.csv",
              result["selected_action_distribution"])
    write_csv(output / "router_lambda_distribution.csv",
              result["selected_lambda_distribution"])
    write_csv(output / "router_materiality_sensitivity.csv", [
        {
            "threshold_db": threshold,
            "probability_headroom_ge_threshold": result["threshold_probabilities"][str(threshold)],
        }
        for threshold in result["policy"]["sensitivity_db"]
    ])
    write_json(output / "router_materiality_manifest.json", {
        "schema_version": 1,
        "analysis": "Router Opportunity Materiality Gate",
        "config_path": str(config_path),
        "gate_config_path": str(gate_path),
        "candidate_source": str(candidate_path.resolve()),
        "selection_source": str(selection_path.resolve()),
        "ai_training": False,
        "router_state": "REOPEN_ROUTER_RESEARCH",
        "decision": result["decision"],
        "training_authorized": False,
        "equal_family_weighting": True,
    })
    print(json.dumps({
        "output_dir": str(output),
        "decision": result["decision"],
        "scene_count": result["scene_count"],
        "equal_family_mean_safe_headroom_db": result[
            "overall_equal_family_weighted"]["equal_family_weighted_mean"],
        "ai_training": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
