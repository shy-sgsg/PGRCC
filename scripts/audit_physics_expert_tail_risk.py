#!/usr/bin/env python3
"""Audit J6 tail risk and evaluate a deterministic inference-only J7 selector.

This script is offline: it consumes the compact V2 manifest and recovery CSV,
does not train a model, and never passes truth-derived scene fields to the
selector.  Truth/spec fields are used only for post-hoc stratification.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from safe_expert_selector import (
    CURRENT,
    FEATURE_NAMES,
    J5,
    J6,
    SELECTOR_PROFILES,
    SelectorThresholds,
    extract_inference_features,
    select_safe_expert_with_reason,
    thresholds_to_dict,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FORMAL_DIR = ROOT / "outputs/physics_adaptive_selective_v2_formal"
DEFAULT_OUTPUT_DIR = ROOT / "outputs/physics_expert_tail_risk"
METHODS = (CURRENT, J5, J6)
EXPERT_METHODS = (J5, J6)
NEGATIVE_THRESHOLDS = (-0.1, -0.5, -1.0)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def finite(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
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


def stats(values: Iterable[Any]) -> dict[str, Any]:
    array = np.asarray([finite(value) for value in values], dtype=float)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return {
            "count": 0, "mean": None, "median": None, "p05": None,
            "p10": None, "p90": None, "min": None, "cvar5": None,
            "fraction_lt_neg_0_1": None, "fraction_lt_neg_0_5": None,
            "fraction_lt_neg_1_0": None,
        }
    tail_count = max(1, int(math.ceil(0.05 * array.size)))
    return {
        "count": int(array.size),
        "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "p05": float(np.percentile(array, 5)),
        "p10": float(np.percentile(array, 10)),
        "p90": float(np.percentile(array, 90)),
        "min": float(np.min(array)),
        "cvar5": float(np.mean(np.sort(array)[:tail_count])),
        "fraction_lt_neg_0_1": float(np.mean(array < -0.1)),
        "fraction_lt_neg_0_5": float(np.mean(array < -0.5)),
        "fraction_lt_neg_1_0": float(np.mean(array < -1.0)),
    }


def git_commit() -> str:
    result = subprocess.run(
        ["git", f"--git-dir={ROOT / '.git-real'}", f"--work-tree={ROOT}",
         "rev-parse", "HEAD"],
        cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False)
    return result.stdout.strip()


def load_formal(formal_dir: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    manifest = json.loads(
        (formal_dir / "formal_matrix_manifest.json").read_text(encoding="utf-8"))
    recovery = read_csv(formal_dir / "recovery_metrics.csv")
    return manifest, recovery


def scene_maps(manifest: Mapping[str, Any]) -> tuple[
        dict[str, dict[str, Any]], dict[str, dict[str, float]]]:
    records = {
        record["spec"]["scene_id"]: record
        for record in manifest["scene_records"]
    }
    features = {
        scene_id: extract_inference_features(record["observables"]["target_off"])
        for scene_id, record in records.items()
    }
    return records, features


def strict_tail_rows(recovery: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Strict held-out tail rows: test + target_off only."""

    rows: list[dict[str, Any]] = []
    for row in recovery:
        if (row.get("split") != "test"
                or row.get("role") != "target_off"
                or row.get("method") not in EXPERT_METHODS):
            continue
        rows.append({**row, "gain_db": finite(row.get("deterministic_gain_db"))})
    return rows


def spec_strata(spec: Mapping[str, Any]) -> dict[str, str]:
    range_bin = finite(spec.get("range_bin"))
    radial = finite(spec.get("true_radial_velocity_mps"))
    texture = finite(spec.get("texture_sigma"))
    rho = finite(spec.get("rho"))
    ridge_distance = finite(spec.get("distance_to_clutter_ridge_hz"))

    def text(value: Any) -> str:
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, float):
            return f"{value:g}"
        return str(value)

    return {
        "family": text(spec.get("label")),
        "angle": text(finite(spec.get("look_angle_deg"))),
        "range_band": text(int(range_bin // 500) * 500
                            if math.isfinite(range_bin) else "unknown"),
        "snr": text(finite(spec.get("snr_db"))),
        "radial_velocity_band": (
            "negative_fast" if radial <= -15.0 else
            "negative_slow" if radial < 0.0 else
            "positive_slow" if radial < 15.0 else "positive_fast"),
        "clutter_ridge": text(spec.get("near_clutter_ridge")),
        "clutter_ridge_distance_band": (
            "near" if abs(ridge_distance) < 500.0 else "far"),
        "texture_band": text(round(texture, 2)),
        "rho_band": text(round(rho, 2)),
        "branch": text(spec.get("_branch", "unknown")),
    }


def add_strata(row: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, str]:
    spec = dict(record["spec"])
    spec["_branch"] = row.get("branch", "unknown")
    return spec_strata(spec)


def build_tail_rows(
        tail_rows: list[dict[str, Any]],
        records: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in tail_rows:
        record = records.get(row["scene_id"])
        if record is None:
            continue
        output.append({
            "split": row["split"],
            "role": row["role"],
            "scene_id": row["scene_id"],
            "method": row["method"],
            "actual_mechanism": row["actual_mechanism"],
            "branch": row.get("branch", ""),
            "gain_db": row["gain_db"],
            **add_strata(row, record),
        })
    return output


def build_stratified_summary(
        tail_rows: list[dict[str, Any]],
        records: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    expanded = build_tail_rows(tail_rows, records)
    dimensions = (
        ("overall", None),
        ("family", "family"),
        ("branch", "branch"),
        ("angle", "angle"),
        ("range", "range_band"),
        ("snr", "snr"),
        ("radial_velocity", "radial_velocity_band"),
        ("clutter_ridge", "clutter_ridge"),
        ("clutter_ridge_distance", "clutter_ridge_distance_band"),
        ("texture", "texture_band"),
        ("rho", "rho_band"),
    )
    output: list[dict[str, Any]] = []
    for method in EXPERT_METHODS:
        method_rows = [row for row in expanded if row["method"] == method]
        for dimension, field in dimensions:
            levels = [("all", method_rows)] if field is None else [
                (level, [row for row in method_rows if row[field] == level])
                for level in sorted({row[field] for row in method_rows})]
            for level, level_rows in levels:
                output.append({
                    "method": method,
                    "dimension": dimension,
                    "level": level,
                    **stats(row["gain_db"] for row in level_rows),
                })
    return output


def feature_csv_rows(records: Mapping[str, Mapping[str, Any]],
                     features: Mapping[str, Mapping[str, float]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scene_id, record in records.items():
        row: dict[str, Any] = {
            "split": record["spec"]["split"],
            "scene_id": scene_id,
        }
        row.update(features[scene_id])
        rows.append(row)
    return rows


def method_gain(record: Mapping[str, Any], method: str) -> float:
    if method == CURRENT:
        return 0.0
    return finite(record["methods"]["target_off"][method].get(
        "headroom_gain_db_vs_current"), 0.0)


def evaluate_profile(
        records: Iterable[Mapping[str, Any]],
        features: Mapping[str, Mapping[str, float]],
        thresholds: SelectorThresholds,
        include_rows: bool = False,
) -> dict[str, Any]:
    choices: list[dict[str, Any]] = []
    for record in records:
        scene_id = record["spec"]["scene_id"]
        method, reason = select_safe_expert_with_reason(
            features[scene_id], thresholds)
        gain = method_gain(record, method)
        choices.append({
            "split": record["spec"]["split"],
            "scene_id": scene_id,
            "selected_method": method,
            "selection_reason": reason,
            "selected_gain_db": gain,
            "j5_gain_db": method_gain(record, J5),
            "j6_gain_db": method_gain(record, J6),
        })
    selected = [row for row in choices if row["selected_method"] != CURRENT]
    all_stats = stats(row["selected_gain_db"] for row in choices)
    selected_stats = stats(row["selected_gain_db"] for row in selected)
    result: dict[str, Any] = {
        "profile": thresholds.name,
        "scene_count": len(choices),
        "selected_count": len(selected),
        "selection_rate": (len(selected) / len(choices) if choices else None),
        "method_counts": dict(Counter(row["selected_method"] for row in choices)),
        "all_scene_stats": all_stats,
        "selected_scene_stats": selected_stats,
        "safe_for_fit": (
            all_stats["fraction_lt_neg_0_5"] is not None
            and all_stats["fraction_lt_neg_0_5"] <= 0.05
            and all_stats["fraction_lt_neg_1_0"] <= 0.0
        ),
    }
    if include_rows:
        result["rows"] = choices
    return result


def choose_profile(
        records: list[Mapping[str, Any]],
        features: Mapping[str, Mapping[str, float]],
        profile_map: Mapping[str, SelectorThresholds],
) -> tuple[SelectorThresholds, list[dict[str, Any]]]:
    evaluations = []
    for name, thresholds in profile_map.items():
        result = evaluate_profile(records, features, thresholds)
        selected_stats = result["selected_scene_stats"]
        selected_mean = selected_stats["mean"]
        evaluations.append({
            "profile": name,
            "safe_for_fit": result["safe_for_fit"],
            "scene_count": result["scene_count"],
            "selected_count": result["selected_count"],
            "selection_rate": result["selection_rate"],
            "selected_mean_gain_db": selected_mean,
            "selected_p05_gain_db": selected_stats["p05"],
            "selected_min_gain_db": selected_stats["min"],
            "selected_fraction_lt_neg_0_1": selected_stats[
                "fraction_lt_neg_0_1"],
            "selected_fraction_lt_neg_0_5": selected_stats[
                "fraction_lt_neg_0_5"],
            "selected_fraction_lt_neg_1_0": selected_stats[
                "fraction_lt_neg_1_0"],
        })
    safe = [row for row in evaluations if row["safe_for_fit"]]
    candidates = safe or evaluations
    chosen_row = max(candidates, key=lambda row: (
        finite(row["selected_mean_gain_db"], -math.inf),
        int(row["selected_count"]),
        -list(profile_map).index(row["profile"]),
    ))
    return profile_map[chosen_row["profile"]], evaluations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-dir", type=Path, default=DEFAULT_FORMAL_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    formal_dir = args.formal_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    manifest, recovery = load_formal(formal_dir)
    records, features = scene_maps(manifest)
    tail = strict_tail_rows(recovery)
    tail_rows = build_tail_rows(tail, records)
    write_csv(output / "j6_tail_risk_rows.csv", tail_rows)
    write_csv(output / "j6_tail_risk_stratified.csv",
              build_stratified_summary(tail, records))
    write_csv(output / "inference_visible_features.csv",
              feature_csv_rows(records, features))

    fit_records = [record for record in records.values()
                   if record["spec"]["split"] in ("calibration", "validation")]
    selected_thresholds, profile_evaluations = choose_profile(
        fit_records, features, SELECTOR_PROFILES)
    write_csv(output / "j7_profile_selection.csv", profile_evaluations)
    test_records = [record for record in records.values()
                    if record["spec"]["split"] == "test"]
    selector_result = evaluate_profile(
        test_records, features, selected_thresholds, include_rows=True)
    selector_rows = selector_result.pop("rows")
    for row in selector_rows:
        record = records[row["scene_id"]]
        row.update({
            "family_audit_only": record["spec"].get("label"),
            "look_angle_audit_only": record["spec"].get("look_angle_deg"),
            "snr_audit_only": record["spec"].get("snr_db"),
            "branch_j5_audit_only": record["methods"]["target_off"][J5].get("branch"),
            "branch_j6_audit_only": record["methods"]["target_off"][J6].get("branch"),
        })
    write_csv(output / "j7_selector_test_eval.csv", selector_rows)

    tail_by_scene_method = {
        (row["scene_id"], row["method"]): row["gain_db"]
        for row in tail_rows
    }
    strict_selector_rows = []
    for row in selector_rows:
        values = [tail_by_scene_method[(row["scene_id"], method)]
                  for method in EXPERT_METHODS
                  if (row["scene_id"], method) in tail_by_scene_method]
        if not values:
            continue
        strict_selector_rows.append({
            **row,
            "strict_recovery_coverage": True,
            "selected_gain_db_strict_source": row["selected_gain_db"]
            if row["selected_method"] == CURRENT
            else (tail_by_scene_method.get(
                (row["scene_id"], row["selected_method"]), math.nan)),
        })
    write_csv(output / "j7_selector_test_strict_recovery_eval.csv",
              strict_selector_rows)

    tail_stats = {}
    for method in EXPERT_METHODS:
        method_values = [row["gain_db"] for row in tail_rows
                         if row["method"] == method]
        tail_stats[method] = stats(method_values)
    result = {
        "schema_version": 1,
        "analysis": "J6 tail risk and deterministic J7 safe expert selector",
        "source_commit": git_commit(),
        "formal_source_commit": manifest.get("source_commit"),
        "formal_dir": str(formal_dir),
        "ai_training": False,
        "strict_filter": "split=test AND role=target_off",
        "strict_tail_row_count": len(tail_rows),
        "tail_stats": tail_stats,
        "selector": {
            "selected_profile": selected_thresholds.name,
            "thresholds": thresholds_to_dict(selected_thresholds),
            "fit_splits": ["calibration", "validation"],
            "evaluation_split": "test",
            "feature_names": list(FEATURE_NAMES),
            "truth_fields_in_selector_input": False,
            "selection_rule": (
                "quality/decorrelation/inconsistency/extrapolation veto -> Current; "
                "high-confidence phase or mixed -> J6; high-confidence delay -> J5; "
                "weak/zero signal -> Current"),
            "fit_result": profile_evaluations,
            "test_result": selector_result,
        },
        "stratification": [
            "family", "branch", "angle", "range", "snr",
            "radial_velocity", "clutter_ridge", "texture", "rho",
        ],
        "outputs": {
            "j6_tail_risk_rows": "j6_tail_risk_rows.csv",
            "j6_tail_risk_stratified": "j6_tail_risk_stratified.csv",
            "inference_visible_features": "inference_visible_features.csv",
            "j7_profile_selection": "j7_profile_selection.csv",
            "j7_selector_test_eval": "j7_selector_test_eval.csv",
            "j7_selector_test_strict_recovery_eval": (
                "j7_selector_test_strict_recovery_eval.csv"),
        },
    }
    (output / "physics_expert_tail_risk_manifest.json").write_text(
        json.dumps(json_safe(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps(json_safe({
        "output_dir": str(output),
        "strict_tail_row_count": len(tail_rows),
        "selected_profile": selected_thresholds.name,
        "test_selection_counts": selector_result["method_counts"],
        "ai_training": False,
    }), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
