#!/usr/bin/env python3
"""Build the paired, compact Router Opportunity development dataset.

This runner is deliberately evaluation-only.  It generates fresh paired
OFF/ON/TO scenes, estimates J5/J6 correction parameters from OFF only, and
replays the frozen correction at the six actions frozen by
``physics_ai_router_gate_v1.json``.  It does not train a model and refuses to
start a long CUDA run when the resource preflight fails.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_target_transfer as transfer  # noqa: E402
import run_joint_physics_formal_matrix as formal  # noqa: E402
import run_joint_physics_selective_v2 as selective_v2  # noqa: E402
from evaluate_target_safe_oracle import scale_frozen_plan  # noqa: E402
from experiment_provenance import (  # noqa: E402
    run_provenance_post,
    run_provenance_start,
)
from resource_preflight import (  # noqa: E402
    assess_resource_preflight,
    collect_resource_snapshot,
)
from run_joint_physics_calibration import (  # noqa: E402
    case_observables,
)
from safe_expert_selector import extract_inference_features  # noqa: E402


FAMILIES = (
    "zero", "M1", "M2", "M3", "M1+M2", "M1+M3", "M2+M3",
    "M1+M2+M3",
)
CURRENT = "J0_Current"
EXPERT_METHODS = ("J5_Selective_Physics_Calibration", "J6_Joint_Phase_Surface")
EXPECTED_ACTIONS = (
    ("A0", CURRENT, 0.0),
    ("A1", "J5_Selective_Physics_Calibration", 0.5),
    ("A2", "J5_Selective_Physics_Calibration", 1.0),
    ("A3", "J6_Joint_Phase_Surface", 0.5),
    ("A4", "J6_Joint_Phase_Surface", 0.75),
    ("A5", "J6_Joint_Phase_Surface", 1.0),
)
C0 = 299_792_458.0


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
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_safe(value), ensure_ascii=False, indent=2,
                   allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def lhs(count: int, dimensions: int, seed: int) -> np.ndarray:
    """Return a deterministic Latin-hypercube design with independent axes."""

    if count < 1 or dimensions < 1:
        raise ValueError("LHS count and dimensions must be positive")
    base = (np.arange(count, dtype=np.float64) + 0.5) / float(count)
    values = np.empty((count, dimensions), dtype=np.float64)
    for dimension in range(dimensions):
        rng = np.random.default_rng(seed + 7919 * dimension)
        values[:, dimension] = base[rng.permutation(count)]
    return values


def physical_look(theta_deg: float) -> np.ndarray:
    theta = math.radians(theta_deg)
    return np.asarray([-math.sin(theta), -math.cos(theta)], dtype=np.float64)


def radial_geometry(spec: Mapping[str, Any]) -> dict[str, float]:
    theta = float(spec["look_angle_deg"])
    look = physical_look(theta)
    velocity = np.asarray([float(spec["ve_mps"]), float(spec["vn_mps"])])
    radial = float(np.dot(velocity, look))
    wavelength = C0 / float(spec["fc_hz"])
    ridge_hz = 2.0 * radial / wavelength
    return {
        "look_angle_deg": theta,
        "radial_velocity_mps": radial,
        "distance_to_clutter_ridge_mps": abs(radial),
        "distance_to_clutter_ridge_hz": abs(ridge_hz),
    }


def action_specs(gate_config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate and return the frozen six-action policy."""

    raw = gate_config.get("action_space", {}).get("actions", [])
    actual = [(str(row.get("id")), str(row.get("method")),
               float(row.get("lambda"))) for row in raw]
    if actual != list(EXPECTED_ACTIONS):
        raise ValueError(
            "router action space drifted; expected frozen A0/A1/A2/A3/A4/A5")
    return [dict(row) for row in raw]


def design_specs(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    design = config["design"]
    families = tuple(str(value) for value in design["families"])
    count = int(config["scene_count"])
    per_family = int(design["scenes_per_family"])
    if families != FAMILIES:
        raise ValueError("Router Opportunity family order is not frozen")
    if count != len(families) * per_family:
        raise ValueError("scene_count must equal family_count*scenes_per_family")
    axes = design["environment_axes"]
    axis_names = tuple(axes)
    expected_axes = (
        "scan_min_deg", "squint_deg", "range_bin", "snr_db",
        "radial_velocity_mps", "texture_sigma", "rho", "delay_ns",
        "phase_deg",
    )
    if axis_names != expected_axes:
        raise ValueError(f"unexpected independent LHS axes: {axis_names}")
    values = lhs(count, len(axis_names), int(design["lhs_seed"]))
    family_rng = np.random.default_rng(int(design["lhs_seed"]) + 104729)
    labels = np.asarray([family for family in families for _ in range(per_family)],
                        dtype=object)
    labels = labels[family_rng.permutation(count)]
    seed_base = int(config["seed_namespace"])
    specs: list[dict[str, Any]] = []
    for index, vector in enumerate(values):
        sampled = {
            name: float(low) + (float(high) - float(low)) * float(vector[pos])
            for pos, (name, bounds) in enumerate(axes.items())
            for low, high in [bounds]
        }
        label = str(labels[index])
        mechanisms = set() if label == "zero" else set(label.split("+"))
        look_angle = -60.0 + sampled["scan_min_deg"]
        radial = sampled["radial_velocity_mps"]
        look = physical_look(look_angle)
        orthogonal = np.asarray([-look[1], look[0]], dtype=np.float64)
        tangent = 12.0 * math.sin(math.radians(look_angle + index * 7.0))
        velocity = radial * look + tangent * orthogonal
        spec: dict[str, Any] = {
            "scene_id": f"router_opportunity_{index:03d}",
            "split": "router_opportunity_v1",
            "label": label,
            "seed": seed_base + index,
            "squint_deg": sampled["squint_deg"],
            "scan_min_deg": sampled["scan_min_deg"],
            "look_angle_deg": look_angle,
            "range_bin": int(round(sampled["range_bin"])),
            "snr_db": sampled["snr_db"],
            "ve_mps": float(velocity[0]),
            "vn_mps": float(velocity[1]),
            "radial_velocity_mps": radial,
            "texture_sigma": sampled["texture_sigma"],
            "rho": sampled["rho"] if "M3" in mechanisms else 1.0,
            "sampled_rho": sampled["rho"],
            "delay_ns": sampled["delay_ns"] if "M1" in mechanisms else 0.0,
            "sampled_delay_ns": sampled["delay_ns"],
            "phase_deg": sampled["phase_deg"] if "M2" in mechanisms else 0.0,
            "sampled_phase_deg": sampled["phase_deg"],
            "multi_target": False,
            "near_clutter_ridge": bool(abs(radial) < 8.0),
            "high_speed_target": bool(abs(radial) > 35.0),
            "include_target_only": True,
            "fc_hz": 16.472113076923076e9,
        }
        spec.update(radial_geometry(spec))
        specs.append(spec)
    return specs


def _mean_finite(rows: list[Mapping[str, Any]], key: str) -> float:
    values = np.asarray([finite(row.get(key)) for row in rows], dtype=float)
    values = values[np.isfinite(values)]
    return float(np.mean(values)) if values.size else math.nan


def _min_finite(rows: list[Mapping[str, Any]], key: str) -> float:
    values = np.asarray([finite(row.get(key)) for row in rows], dtype=float)
    values = values[np.isfinite(values)]
    return float(np.min(values)) if values.size else math.nan


def _feature_row(spec: Mapping[str, Any], off_root: Path) -> dict[str, Any]:
    """Create only the inference-visible OFF/Current feature row."""

    _, _, observables = case_observables(
        off_root, 1300.0, 80.0, include_selective_surface=True)
    features = extract_inference_features(observables["summary"])
    current_metrics = transfer.summary_metrics(transfer.single_manifest(off_root))
    geometry = radial_geometry(spec)
    row: dict[str, Any] = {
        "scene_id": spec["scene_id"],
        "joint_confidence": features["joint_confidence"],
        "joint_rmse_rad": features["joint_rmse_rad"],
        "raw_coherence": features["raw_coherence"],
        "p1_residual_coherence": finite(
            observables["summary"].get("coherence", {}).get("residual_p1_global")),
        "joint_residual_coherence": finite(
            observables["summary"].get("coherence", {}).get("residual_joint_global")),
        "joint_minus_p1_residual_coherence_gain": features[
            "joint_vs_p1_residual_coherence_gain"],
        "tau_ns": features["tau_ns"],
        "beta1_deg_per_pulse": features["beta1_deg_per_pulse"],
        "beta2_deg_per_pulse2": features["beta2_deg_per_pulse2"],
        "abs_beta2_deg_per_pulse2": features["abs_beta2_deg_per_pulse2"],
        "d3_j6_delay_disagreement_ns": features[
            "d3_j6_delay_disagreement_ns"],
        "p1_j6_phase_disagreement_deg_per_pulse": features[
            "p1_j6_phase_disagreement_deg_per_pulse"],
        "delay_confidence": features["delay_confidence"],
        "phase_confidence": features["phase_confidence"],
        "delay_signal_ns": features["delay_signal"],
        "phase_signal": features["phase_signal"],
        "support_fraction": features["support_fraction"],
        "delay_extrapolation_ratio": features["delay_extrapolation_ratio"],
        "current_residual_p95_rad": finite(current_metrics.get("phase_p95_rad")),
        "current_phase_rmse_rad": finite(current_metrics.get("phase_rmse_rad")),
        "current_coherence": finite(current_metrics.get("coherence")),
        "current_cancellation_db": finite(current_metrics.get("cancellation_db")),
        "distance_to_clutter_ridge": geometry["distance_to_clutter_ridge_mps"],
        "distance_to_clutter_ridge_mps": geometry["distance_to_clutter_ridge_mps"],
        "distance_to_clutter_ridge_hz": geometry["distance_to_clutter_ridge_hz"],
        "geometry_look_angle_deg": geometry["look_angle_deg"],
        "geometry_range_bin": spec["range_bin"],
        "geometry_scan_min_deg": spec["scan_min_deg"],
        "geometry_squint_deg": spec["squint_deg"],
    }
    return row


def _scale_action_plan(plan: Mapping[str, Any], lambda_value: float) -> dict[str, Any]:
    if lambda_value == 0.0:
        scaled = dict(plan)
        scaled["fallback"] = True
        scaled["apply_delay"] = False
        scaled["apply_phase"] = False
        scaled["phase_values"] = None
        scaled["lambda"] = 0.0
        return scaled
    return scale_frozen_plan(plan, lambda_value)


def _emit_action(
    spec: Mapping[str, Any],
    action: Mapping[str, Any],
    roots: Mapping[str, Path],
    original_roots: Mapping[str, Path],
    current_rows: Mapping[str, Mapping[str, Any]],
    transfer_rows: list[dict[str, Any]],
    detection_rows: list[dict[str, Any]],
    cfar_rows: list[dict[str, Any]],
) -> dict[str, float]:
    action_id = str(action["id"])
    method = str(action["method"])
    lambda_value = float(action["lambda"])
    raw = transfer.transfer_rows_for_method(
        dict(spec), method, dict(roots), original_roots["target_on"])
    enriched = transfer.enrich_transfer_rows(raw, dict(current_rows))
    for row in enriched:
        row["action_id"] = action_id
        row["lambda"] = lambda_value
        row["L_causal"] = row.get("L_causal_dB")
        row["L_target_only"] = row.get("L_target_only_dB")
    transfer_rows.extend(enriched)
    for row in enriched:
        detection_rows.append({
            "split": spec["split"],
            "scene_id": spec["scene_id"],
            "family": spec["label"],
            "action_id": action_id,
            "method": method,
            "lambda": lambda_value,
            "target_id": row["target_id"],
            "paired_on_hit": bool(row["on_hit"]),
            "paired_off_hit": bool(row["off_hit"]),
            "paired_causal_hit": bool(row["paired_causal_hit"]),
            "on_cfar_margin_db": row.get("on_cfar_margin_db"),
            "off_cfar_margin_db": row.get("off_cfar_margin_db"),
            "delta_cfar_margin_db": row.get("delta_cfar_margin_db"),
            "paired_hit_definition": "on_hit AND NOT off_hit",
        })
    for role in ("target_off", "target_on"):
        row = selective_v2.production_cfar_row(
            roots[role], str(spec["split"]), str(spec["scene_id"]),
            role, method)
        row["action_id"] = action_id
        row["lambda"] = lambda_value
        cfar_rows.append(row)
    metrics = transfer.summary_metrics(
        transfer.single_manifest(roots["target_off"]))
    return {
        "cancellation_db": finite(metrics.get("cancellation_db")),
        "residual_p95_rad": finite(metrics.get("phase_p95_rad")),
        "coherence": finite(metrics.get("coherence")),
        "phase_rmse_rad": finite(metrics.get("phase_rmse_rad")),
    }


def _candidate_row(
    spec: Mapping[str, Any],
    action: Mapping[str, Any],
    metrics: Mapping[str, float],
    transfer_rows: list[dict[str, Any]],
    detection_rows: list[dict[str, Any]],
    cfar_rows: list[dict[str, Any]],
    current_metrics: Mapping[str, float],
    current_detection: list[Mapping[str, Any]],
    current_off: list[Mapping[str, Any]],
    safety: Mapping[str, Any],
) -> dict[str, Any]:
    action_id = str(action["id"])
    method = str(action["method"])
    lambda_value = float(action["lambda"])
    transfer_selected = [row for row in transfer_rows
                         if row.get("scene_id") == spec["scene_id"]
                         and row.get("action_id") == action_id]
    detection_selected = [row for row in detection_rows
                          if row.get("scene_id") == spec["scene_id"]
                          and row.get("action_id") == action_id]
    off_selected = [row for row in cfar_rows
                    if row.get("scene_id") == spec["scene_id"]
                    and row.get("action_id") == action_id
                    and row.get("role") == "target_off"]
    current_by_target = {
        str(row.get("target_id")): bool(row.get("paired_on_hit"))
        for row in current_detection
    }
    candidate_by_target = {
        str(row.get("target_id")): bool(row.get("paired_on_hit"))
        for row in detection_selected
    }
    current_detected_lost = sum(
        bool(hit) and not candidate_by_target.get(target_id, False)
        for target_id, hit in current_by_target.items()
    )
    current_pfa = _mean_finite(current_off, "pfa")
    current_clusters = _mean_finite(current_off, "false_clusters")
    pfa = _mean_finite(off_selected, "pfa")
    clusters = _mean_finite(off_selected, "false_clusters")
    pfa_delta = pfa - current_pfa if math.isfinite(pfa) and math.isfinite(current_pfa) else math.nan
    clusters_delta = (clusters - current_clusters
                      if math.isfinite(clusters) and math.isfinite(current_clusters)
                      else math.nan)
    lcausal = _min_finite(transfer_selected, "L_causal")
    lcausal_mean = _mean_finite(transfer_selected, "L_causal")
    ltarget = _mean_finite(transfer_selected, "L_target_only")
    ltarget_min = _min_finite(transfer_selected, "L_target_only")
    cancellation = finite(metrics.get("cancellation_db"))
    current_cancellation = finite(current_metrics.get("cancellation_db"))
    gain = (cancellation - current_cancellation
            if math.isfinite(cancellation) and math.isfinite(current_cancellation)
            else math.nan)
    causal_ok = bool(math.isfinite(lcausal)
                     and lcausal >= float(safety["causal_transfer_floor_db"]))
    target_ok = (not bool(safety["require_current_detected_targets_preserved"])
                 or current_detected_lost == 0)
    pfa_ok = (action_id == "A0" or
              (math.isfinite(pfa_delta)
               and pfa_delta <= float(safety["target_off_pfa_delta_max"])))
    clusters_ok = (action_id == "A0" or
                   (math.isfinite(clusters_delta)
                    and clusters_delta <= float(
                        safety["target_off_false_clusters_delta_max"])))
    safe = True if action_id == "A0" else bool(
        causal_ok and target_ok and pfa_ok and clusters_ok)
    return {
        "split": spec["split"],
        "scene_id": spec["scene_id"],
        "family": spec["label"],
        "snr_db": spec["snr_db"],
        "action_id": action_id,
        "method": method,
        "lambda": lambda_value,
        "cancellation_db": cancellation,
        "gain_vs_Current": gain,
        "scnr_gain_db_vs_current": gain,
        "residual_p95_rad": finite(metrics.get("residual_p95_rad")),
        "coherence": finite(metrics.get("coherence")),
        "phase_rmse_rad": finite(metrics.get("phase_rmse_rad")),
        "L_causal": lcausal,
        "L_causal_min_dB": lcausal,
        "L_causal_mean_dB": lcausal_mean,
        "L_target_only": ltarget,
        "L_target_only_min_dB": ltarget_min,
        "L_target_only_mean_dB": ltarget,
        "paired_on_hit_count": sum(bool(row.get("paired_on_hit"))
                                   for row in detection_selected),
        "paired_off_hit_count": sum(bool(row.get("paired_off_hit"))
                                    for row in detection_selected),
        "paired_causal_hit_count": sum(bool(row.get("paired_causal_hit"))
                                      for row in detection_selected),
        "target_count": len(transfer_selected),
        "current_detected_targets_lost": current_detected_lost,
        "target_preservation_ok": target_ok,
        "target_off_Pfa": pfa,
        "target_off_false_clusters": clusters,
        "delta_Pfa": pfa_delta,
        "delta_false_clusters": clusters_delta,
        "causal_transfer_floor_db": float(safety["causal_transfer_floor_db"]),
        "causal_transfer_ok": causal_ok,
        "pfa_acceptable": pfa_ok,
        "false_clusters_acceptable": clusters_ok,
        "safe": safe,
        "safe_definition": (
            "L_causal >= -0.25 dB; Current-detected target not lost; "
            "delta_Pfa <= 0; delta_false_clusters <= 0"),
    }


def select_safe_action(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Select max cancellation among safe actions, preferring Current on ties."""

    safe = [row for row in rows if bool(row.get("safe"))]
    if not safe:
        raise ValueError("A0 Current must make at least one safe action")
    rank = {action_id: index for index, (action_id, _, _) in enumerate(EXPECTED_ACTIONS)}
    return dict(max(
        safe,
        key=lambda row: (finite(row.get("cancellation_db"), -math.inf),
                         -rank.get(str(row.get("action_id")), 10_000)),
    ))


def run_one_scene(
    spec: Mapping[str, Any],
    base: Mapping[str, Any],
    output: Path,
    build_dir: Path,
    gate: Mapping[str, Any],
    positive_gate: Mapping[str, Any],
    actions: list[Mapping[str, Any]],
    safety: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
           list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
           dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    scene = formal.run_scene(dict(spec), dict(base), output, build_dir, True)
    if scene.get("status") != "pass":
        raise RuntimeError(f"scene failed: {spec['scene_id']} {scene}")
    formal.enrich_scene(scene, output, dict(spec))
    scene_root = Path(scene["scene_root"]).resolve()
    original_roots = {
        role: Path(scene["roles"][role]["root"])
        for role in ("target_off", "target_on", "target_only")
    }
    plans = {
        method: transfer.make_frozen_plan(
            method, original_roots["target_off"], dict(gate),
            dict(positive_gate), scene_root / "_router_plans")
        for method in EXPERT_METHODS
    }
    feature_row = _feature_row(spec, original_roots["target_off"])
    current_raw = transfer.transfer_rows_for_method(
        dict(spec), CURRENT, original_roots, original_roots["target_on"])
    current_rows = {str(row["target_id"]): row for row in current_raw}
    transfer_rows: list[dict[str, Any]] = []
    detection_rows: list[dict[str, Any]] = []
    cfar_rows: list[dict[str, Any]] = []
    metrics_by_action: dict[str, dict[str, float]] = {}
    plan_rows: list[dict[str, Any]] = []
    for action in actions:
        action_id = str(action["id"])
        method = str(action["method"])
        lambda_value = float(action["lambda"])
        if method == CURRENT:
            roots = dict(original_roots)
        else:
            scaled = _scale_action_plan(plans[method], lambda_value)
            roots = {}
            if scaled.get("fallback"):
                roots = dict(original_roots)
            else:
                for role in ("target_off", "target_on", "target_only"):
                    roots[role] = Path(transfer.run_frozen_method(
                        original_roots[role], scaled,
                        scene_root / "router" / action_id / role,
                        build_dir)["root"])
        metrics_by_action[action_id] = _emit_action(
            spec, action, roots, original_roots, current_rows,
            transfer_rows, detection_rows, cfar_rows)
        plan = plans.get(method, {"method": CURRENT, "fallback": False})
        plan_rows.append({
            "scene_id": spec["scene_id"],
            "action_id": action_id,
            "method": method,
            "lambda": lambda_value,
            "source_role": "target_off" if method != CURRENT else "identity",
            "fallback": plan.get("fallback", False),
            "apply_delay": plan.get("apply_delay", False),
            "apply_phase": plan.get("apply_phase", False),
            "estimated_delay_ns": plan.get("estimated_delay_ns"),
            "estimated_phase_slope_deg_per_pulse": plan.get(
                "estimated_phase_slope_deg_per_pulse"),
            "estimated_beta2_deg_per_pulse2": plan.get(
                "estimated_beta2_deg_per_pulse2"),
            "truth_used_in_estimator": False,
        })
    current_detection = [row for row in detection_rows if row["action_id"] == "A0"]
    current_off = [row for row in cfar_rows
                   if row["action_id"] == "A0" and row["role"] == "target_off"]
    candidates = [
        _candidate_row(
            spec, action, metrics_by_action[str(action["id"])], transfer_rows,
            detection_rows, cfar_rows,
            metrics_by_action["A0"], current_detection, current_off, safety)
        for action in actions
    ]
    selected = select_safe_action(candidates)
    selection = {
        "split": spec["split"],
        "scene_id": spec["scene_id"],
        "family": spec["label"],
        "selected_action": selected["action_id"],
        "selected_method": selected["method"],
        "selected_lambda": selected["lambda"],
        "safe_headroom_db": max(0.0, finite(selected.get("gain_vs_Current"), 0.0)),
        "safe_candidate_count": sum(bool(row["safe"]) for row in candidates),
        "selection_definition": (
            "select max cancellation_db among safe actions; A0 Current is the "
            "explicit safe identity baseline"),
    }
    expected_root = (output / "scenes" / str(spec["split"]) /
                     str(spec["scene_id"])).resolve()
    if scene_root != expected_root:
        raise RuntimeError(f"refusing to clean unexpected scene root: {scene_root}")
    scene_manifest_rel = Path("scene_manifests") / f"{spec['scene_id']}.json"
    metadata = {
        "scene_id": spec["scene_id"],
        "split": spec["split"],
        "family": spec["label"],
        "status": scene["status"],
        "background_status": scene.get("background", {}).get("status"),
        "exact_pairing": "same deterministic background_input_dir for OFF/ON/TO",
        "primary_correction_source": "target_off",
        "raw_scene_cleanup": "pending_after_manifest",
        "seed": spec["seed"],
        "scan_min_deg": spec["scan_min_deg"],
        "squint_deg": spec["squint_deg"],
        "look_angle_deg": spec["look_angle_deg"],
        "range_bin": spec["range_bin"],
        "snr_db": spec["snr_db"],
        "radial_velocity_mps": spec["radial_velocity_mps"],
        "texture_sigma": spec["texture_sigma"],
        "rho": spec["rho"],
        "sampled_rho": spec["sampled_rho"],
        "delay_ns": spec["delay_ns"],
        "sampled_delay_ns": spec["sampled_delay_ns"],
        "phase_deg": spec["phase_deg"],
        "sampled_phase_deg": spec["sampled_phase_deg"],
        "distance_to_clutter_ridge_mps": spec["distance_to_clutter_ridge_mps"],
        "distance_to_clutter_ridge_hz": spec["distance_to_clutter_ridge_hz"],
        "raw_sha256": json.dumps(json_safe(scene.get("raw_sha256", {})),
                                  ensure_ascii=False, sort_keys=True),
        "scene_manifest": str(scene_manifest_rel),
    }
    scene_manifest_path = output / scene_manifest_rel
    compact_manifest = {
        "schema_version": 1,
        "analysis": "Router Opportunity per-scene compact manifest",
        "scene_id": spec["scene_id"],
        "split": spec["split"],
        "family": spec["label"],
        "status": scene["status"],
        "exact_pairing": metadata["exact_pairing"],
        "primary_correction_source": metadata["primary_correction_source"],
        "raw_sha256": json_safe(scene.get("raw_sha256", {})),
        "metadata": metadata,
        "selection": selection,
        "candidate_metrics": candidates,
        "transfer_rows": transfer_rows,
        "detection_rows": detection_rows,
        "cfar_rows": cfar_rows,
        "plan_rows": plan_rows,
        "raw_scene_cleanup": "pending",
    }
    # Persist the compact evidence before deleting the scene.  If this write
    # fails, the raw scene remains available for diagnosing the failed case.
    write_json(scene_manifest_path, compact_manifest)
    if scene_root.exists():
        shutil.rmtree(scene_root)
    metadata["raw_scene_cleanup"] = True
    compact_manifest["metadata"] = metadata
    compact_manifest["raw_scene_cleanup"] = True
    write_json(scene_manifest_path, compact_manifest)
    return (transfer_rows, detection_rows, cfar_rows, candidates, [feature_row],
            selection, metadata, plan_rows)


def validate_seed_namespace(config: Mapping[str, Any], specs: list[Mapping[str, Any]]) -> None:
    base = int(config["seed_namespace"])
    seeds = [int(spec["seed"]) for spec in specs]
    if seeds != list(range(base, base + len(seeds))):
        raise ValueError("scene seeds must be contiguous within the new namespace")
    forbidden = tuple(int(value) for value in config.get(
        "forbidden_seed_namespaces", (2026111000, 2026120000, 2026130000,
                                        2026700000)))
    if any(any(abs(seed - namespace) < 10000 for namespace in forbidden)
           for seed in seeds):
        raise ValueError("Router Opportunity seed overlaps a forbidden namespace")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs/research/router_opportunity_v1.json")
    parser.add_argument("--gate-config", type=Path,
                        default=ROOT / "configs/research/physics_ai_router_gate_v1.json")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/router_opportunity_v1")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--formal-dir", type=Path,
                        default=ROOT / "outputs/physics_adaptive_selective_v2_formal")
    args = parser.parse_args()
    config_path = args.config.resolve()
    gate_config_path = args.gate_config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    gate_config = json.loads(gate_config_path.read_text(encoding="utf-8"))
    if config.get("ai_training") is not False or gate_config.get("ai_training") is not False:
        raise RuntimeError("Router Opportunity builder requires ai_training=false")
    actions = action_specs(gate_config)
    if config.get("safety") != gate_config.get("safety"):
        raise RuntimeError(
            "Router Opportunity safety policy differs from frozen router gate")
    safety = gate_config["safety"]
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"output directory must be empty: {output}")
    formal_dir = args.formal_dir.resolve()
    gate_path = formal_dir / "null_gate.json"
    positive_gate_path = formal_dir / "positive_quality_gate.json"
    provenance_start = run_provenance_start(
        ROOT, (config_path, gate_config_path, gate_path, positive_gate_path))
    output.mkdir(parents=True, exist_ok=True)
    if provenance_start["source_worktree_dirty_before"] is not False:
        record = {
            "status": "reject",
            "reason": "Router Opportunity run requires a clean source worktree",
            "provenance_start": provenance_start,
        }
        write_json(output / "router_opportunity_preflight_rejection.json", record)
        raise SystemExit(record["reason"])
    snapshot_before = collect_resource_snapshot(ROOT)
    preflight = assess_resource_preflight(
        snapshot_before, config["resource_preflight"])
    write_json(output / "resource_preflight_before.json", preflight)
    if preflight["status"] != "pass":
        write_json(output / "router_opportunity_preflight_rejection.json", {
            "status": "reject",
            "reason": "resource preflight failed; long CUDA run was not started",
            "preflight": preflight,
            "provenance_start": provenance_start,
        })
        raise SystemExit("resource preflight rejected the long CUDA run")
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    positive_gate = json.loads(positive_gate_path.read_text(encoding="utf-8"))
    base = formal.base_scenario()
    specs = design_specs(config)
    validate_seed_namespace(config, specs)
    family_counts = Counter(str(spec["label"]) for spec in specs)
    if any(family_counts[family] != int(config["design"]["scenes_per_family"])
           for family in FAMILIES):
        raise RuntimeError(f"initial Router Opportunity design is not equal-family: {family_counts}")
    all_transfer: list[dict[str, Any]] = []
    all_detection: list[dict[str, Any]] = []
    all_cfar: list[dict[str, Any]] = []
    all_candidates: list[dict[str, Any]] = []
    all_selection: list[dict[str, Any]] = []
    all_metadata: list[dict[str, Any]] = []
    all_features: list[dict[str, Any]] = []
    all_plans: list[dict[str, Any]] = []
    scene_records: list[dict[str, Any]] = []
    try:
        for index, spec in enumerate(specs, 1):
            print(f"[router-opportunity] {index}/{len(specs)} {spec['scene_id']}",
                  flush=True)
            tr, det, cfar, candidates, features, selection, metadata, plans = run_one_scene(
                spec, base, output, args.build_dir.resolve(), gate, positive_gate,
                actions, safety)
            all_transfer.extend(tr)
            all_detection.extend(det)
            all_cfar.extend(cfar)
            all_candidates.extend(candidates)
            all_features.extend(features)
            all_selection.append(selection)
            all_metadata.append(metadata)
            all_plans.extend(plans)
            scene_records.append({
                "scene_id": spec["scene_id"],
                "family": spec["label"],
                "seed": spec["seed"],
                "selection": selection,
                "metadata": metadata,
            })
    except Exception as exc:  # noqa: BLE001 - preserve the exact failure point
        write_json(output / "router_opportunity_failure.json", {
            "status": "failed",
            "error": str(exc),
            "completed_scene_count": len(scene_records),
            "provenance_start": provenance_start,
        })
        raise

    write_csv(output / "router_transfer_rows.csv", all_transfer)
    write_csv(output / "router_detection_rows.csv", all_detection)
    write_csv(output / "router_cfar_rows.csv", all_cfar)
    write_csv(output / "router_candidate_metrics.csv", all_candidates)
    write_csv(output / "router_scene_selection.csv", all_selection)
    write_csv(output / "router_scene_metadata.csv", all_metadata)
    write_csv(output / "router_inference_visible_features.csv", all_features)
    write_csv(output / "router_plan_rows.csv", all_plans)
    snapshot_after = collect_resource_snapshot(ROOT)
    post = run_provenance_post(ROOT, provenance_start)
    write_json(output / "resource_preflight_after.json", {
        "status": "recorded",
        "snapshot": snapshot_after,
    })
    write_json(output / "provenance_start.json", provenance_start)
    write_json(output / "provenance_post.json", post)
    manifest = {
        "schema_version": 1,
        "analysis": "Router Opportunity Dataset Builder",
        "source_commit": provenance_start["source_commit"],
        "source_worktree_dirty_before": provenance_start[
            "source_worktree_dirty_before"],
        "source_worktree_dirty_after": post["source_worktree_dirty_after"],
        "provenance_start": provenance_start,
        "provenance_post": post,
        "config_path": str(config_path),
        "gate_config_path": str(gate_config_path),
        "ai_training": False,
        "router_state": "REOPEN_ROUTER_RESEARCH",
        "design": config["design"],
        "actions": actions,
        "safety": config["safety"],
        "roles": config["design"]["roles"],
        "primary_correction_source": "target_off",
        "feature_source": "OFF/Current only",
        "forbidden_feature_inputs": config["forbidden_feature_inputs"],
        "counts": {
            "scene_count": len(scene_records),
            "transfer_rows": len(all_transfer),
            "detection_rows": len(all_detection),
            "cfar_rows": len(all_cfar),
            "candidate_rows": len(all_candidates),
            "selection_rows": len(all_selection),
            "feature_rows": len(all_features),
            "scene_manifest_rows": len(scene_records),
        },
        "outputs": {
            "transfer": "router_transfer_rows.csv",
            "detection": "router_detection_rows.csv",
            "cfar": "router_cfar_rows.csv",
            "candidate_metrics": "router_candidate_metrics.csv",
            "selection": "router_scene_selection.csv",
            "metadata": "router_scene_metadata.csv",
            "features": "router_inference_visible_features.csv",
            "plans": "router_plan_rows.csv",
            "scene_manifests": "scene_manifests/<scene_id>.json",
        },
        "cleanup": {
            "raw_scene_cleanup": True,
            "policy": "one scene generated, evaluated, compacted, hashed, then deleted before next scene",
        },
        "scene_records": scene_records,
    }
    write_json(output / "router_opportunity_v1_manifest.json", manifest)
    print(json.dumps({
        "output_dir": str(output),
        "scene_count": len(scene_records),
        "candidate_rows": len(all_candidates),
        "ai_training": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
