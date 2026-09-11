#!/usr/bin/env python3
"""Run the selective Physics Calibration V2 matrix.

The runner is deliberately separate from the V1 formal runner.  It reuses
the production simulator, GMTI_core, paired-background design and compact
cleanup helpers, while adding independent decorrelation/delay/phase states,
J5/J6 replays, held-out zero-family coverage, production GO-CFAR accounting
and scene-block bootstrap statistics.  It never trains AI and never passes
mechanism truth to an estimator or gate.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_model_mismatch_metric_controls as cfar  # noqa: E402
import run_joint_physics_formal_matrix as v1  # noqa: E402
from experiment_provenance import git_provenance  # noqa: E402
from run_joint_physics_calibration import (  # noqa: E402
    METHODS,
    SELECTIVE_METHODS,
    calibrate_null_gate,
    case_observables,
    finite,
)


V2_METHODS = METHODS + SELECTIVE_METHODS
FAMILIES = ("zero", "M1", "M2", "M3", "M1+M2", "M1+M3", "M2+M3", "M1+M2+M3")
ANGLE_GRID_DEG = (-20.0, -10.0, 0.0, 10.0, 20.0)
REQUESTED_PFAS = (1.0e-2, 1.0e-3, 1.0e-4)
C0 = 299_792_458.0


def lhs(count: int, dimensions: int, seed: int) -> np.ndarray:
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


def radial_geometry(spec: dict[str, Any]) -> dict[str, float]:
    theta = float(spec["look_angle_deg"])
    look = physical_look(theta)
    velocity = np.asarray([float(spec["ve_mps"]), float(spec["vn_mps"])])
    radial = float(np.dot(velocity, look))
    wavelength = C0 / float(spec["fc_hz"])
    ridge_hz = 2.0 * radial / wavelength
    return {
        "look_angle_deg": theta,
        "true_radial_velocity_mps": radial,
        "true_clutter_ridge_doppler_hz": ridge_hz,
        "distance_to_clutter_ridge_hz": abs(ridge_hz),
        "distance_to_clutter_ridge_mps": abs(radial),
    }


def design_specs(mode: str) -> list[dict[str, Any]]:
    if mode == "screen":
        split_counts = (("null", 12), ("screen", 24))
    elif mode == "formal":
        split_counts = (("calibration", 48), ("validation", 16), ("test", 64))
    else:
        raise ValueError(f"unknown mode: {mode}")
    specs: list[dict[str, Any]] = []
    for split, count in split_counts:
        values = lhs(count, 12, 2026091000 + 101 * len(split))
        for index, vector in enumerate(values):
            if split in {"null", "calibration"}:
                label = "zero"
            elif split == "screen":
                label = FAMILIES[index // 3]
            else:
                label = FAMILIES[index // 8]
            mechanisms = set() if label == "zero" else set(label.split("+"))
            angle = ANGLE_GRID_DEG[index % len(ANGLE_GRID_DEG)]
            desired_radial = -45.0 + 90.0 * vector[4]
            tangent = -20.0 + 40.0 * vector[5]
            look = physical_look(-60.0 + angle)
            orthogonal = np.asarray([-look[1], look[0]])
            velocity = desired_radial * look + tangent * orthogonal
            spec: dict[str, Any] = {
                "scene_id": f"{split}_{index:03d}",
                "split": split,
                "label": label,
                "seed": 2026700000 + (0 if split in {"null", "calibration"}
                                      else 1000 if split == "validation" else 2000) + index,
                "squint_deg": 0.0,
                "scan_min_deg": angle,
                "look_angle_deg": -60.0 + angle,
                "range_bin": int(round(180 + 1400 * vector[2])),
                "snr_db": float(-5.0 + 13.0 * vector[3]),
                "ve_mps": float(velocity[0]),
                "vn_mps": float(velocity[1]),
                # These are intentionally independent LHS dimensions.
                "texture_sigma": float(0.15 + 0.90 * vector[6]),
                "rho": float((0.45 + 0.50 * vector[7]) if "M3" in mechanisms
                             else (0.76 + 0.22 * vector[7])),
                "delay_ns": float(1.0 + 10.0 * vector[8]) if "M1" in mechanisms else 0.0,
                "phase_deg": float(0.03 + 0.65 * vector[9]) if "M2" in mechanisms else 0.0,
                "multi_target": bool(split == "test" and index % 8 == 0),
                "near_clutter_ridge": bool(abs(desired_radial) < 8.0),
                "high_speed_target": bool(abs(desired_radial) > 35.0),
                "fc_hz": 16.472113076923076e9,
            }
            spec.update(radial_geometry(spec))
            if split == "screen":
                spec["strength"] = ("weak", "moderate", "strong")[index % 3]
            if split == "test":
                spec["family_index"] = FAMILIES.index(label)
            specs.append(spec)
    return specs


def resource_snapshot() -> dict[str, str]:
    def run(command: list[str]) -> str:
        result = subprocess.run(command, cwd=ROOT, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                check=False)
        return result.stdout.strip()

    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "nvidia_smi": run(["nvidia-smi"]),
        "free_h": run(["free", "-h"]),
        "df_h_workspace": run(["df", "-h", str(ROOT)]),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def json_safe(value: Any) -> Any:
    return v1.json_safe(value)


def run_joint_for_role_v2(
    role_root: Path, gate_path: Path, positive_path: Path | None,
    output_root: Path, build_dir: Path,
) -> dict[str, Any]:
    command = [sys.executable, str(ROOT / "scripts/run_joint_physics_calibration.py"),
               "--case", str(role_root), "--gate-json", str(gate_path),
               "--output-dir", str(output_root), "--build-dir", str(build_dir)]
    if positive_path is not None:
        command.extend(["--positive-gate-json", str(positive_path)])
    for method in V2_METHODS:
        command.extend(["--method", method])
    log = output_root / "joint_runner.log"
    rc = v1.run_logged(command, log)
    manifest = output_root / "joint_calibration_manifest.json"
    return {"status": "pass" if rc == 0 and manifest.is_file() else "failed",
            "exit_code": rc, "manifest": str(manifest.resolve())}


def enrich_scene_v2(scene: dict[str, Any], output: Path, spec: dict[str, Any]) -> dict[str, Any]:
    scene["scene_root"] = str((output / "scenes" / spec["split"] /
                                spec["scene_id"]).resolve())
    for role in scene["roles"]:
        scenario_path = Path(scene["roles"][role]["scenario"])
        scene["roles"][role]["root"] = str(scenario_path.parent.resolve())
    observables: dict[str, Any] = {}
    for role in scene["roles"]:
        _, _, result = case_observables(Path(scene["roles"][role]["root"]),
                                         1300.0, 80.0,
                                         include_selective_surface=True)
        observables[role] = result["summary"]
    scene["observables"] = observables
    scene["raw_sha256"] = {role: scene["roles"][role].get("raw_sha256")
                           for role in scene["roles"]}
    scene["geometry_audit"] = radial_geometry(spec)
    return scene


def method_rows_from_manifest(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["method"]): row for row in data.get("rows", [])}


def execute_joint_scene_v2(
    scene: dict[str, Any], gate_path: Path, positive_path: Path | None,
    build_dir: Path, include_oracle: bool,
) -> None:
    scene["methods"] = {}
    for role in scene["roles"]:
        root = Path(scene["roles"][role]["root"])
        joint_root = Path(scene["scene_root"]) / "joint" / role
        result = run_joint_for_role_v2(root, gate_path, positive_path, joint_root, build_dir)
        if result["status"] != "pass":
            raise RuntimeError(f"joint replay failed: {scene['spec']['scene_id']} {role}: {result}")
        scene["methods"][role] = method_rows_from_manifest(Path(result["manifest"]))
    if include_oracle:
        scene["oracle"] = {}
        for role in scene["roles"]:
            root = Path(scene["roles"][role]["root"])
            oracle_root = Path(scene["scene_root"]) / "oracle" / role
            scene["oracle"][role] = v1.run_known_oracle(
                root, scene["spec"], oracle_root, build_dir)
            scene["methods"][role]["Oracle_Known_Joint"] = scene["oracle"][role]
    scene["estimator_bias"] = v1.estimator_bias_rows(scene)


def compact_scene(scene: dict[str, Any]) -> dict[str, Any]:
    method_summary: dict[str, dict[str, Any]] = {}
    for role, rows in scene.get("methods", {}).items():
        method_summary[role] = {}
        for method, row in rows.items():
            keep = ("method", "status", "state", "fallback", "fallback_reason",
                    "decorrelation_state", "global_veto", "delay_state", "phase_state",
                    "branch", "delay_active", "phase_active", "estimated_delay_ns",
                    "estimated_phase_slope_deg_per_pulse", "estimated_cancellation_db",
                    "headroom_gain_db_vs_current", "reestimated_phase", "phase_method",
                    "truth_used_in_estimator")
            method_summary[role][method] = {key: row.get(key) for key in keep if key in row}
            if method == "Oracle_Known_Joint":
                method_summary[role][method]["metrics"] = row.get("metrics", {})
    roles = {role: {key: result.get(key) for key in
                    ("status", "simulate_exit_code", "gmticore_exit_code", "raw_sha256")
                    if key in result}
             for role, result in scene["roles"].items()}
    return {
        "spec": scene["spec"],
        "status": scene.get("status"),
        "background_status": scene.get("background", {}).get("status"),
        "exact_pairing": "same deterministic background_input_dir for target_off/target_on/target_only",
        "roles": roles,
        "raw_sha256": scene.get("raw_sha256", {}),
        "geometry_audit": scene.get("geometry_audit", {}),
        "observables": scene.get("observables", {}),
        "methods": method_summary,
        "estimator_bias": scene.get("estimator_bias", []),
    }


def method_root(row: dict[str, Any], method: str) -> Path | None:
    value = row.get("manifest")
    if not value:
        return None
    path = Path(value)
    if not path.is_file():
        return None
    if method != "J0_Current":
        for parent in path.parents:
            if parent.name == method and (parent / "stage2").is_dir():
                return parent
    return v1.run_root_from_manifest(path)


def production_cfar_row(root: Path, split: str, scene_id: str,
                        role: str, method: str) -> dict[str, Any]:
    try:
        manifest_path = cfar.find_manifest(root)
        rows = cfar.read_csv(manifest_path)
        if len(rows) != 1:
            raise RuntimeError(f"manifest rows={len(rows)}")
        config_path = cfar.latest_runtime_config(root)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        summary = cfar.parse_cfar_summary(root)
        geometry = cfar.count_cfar_test_cells(
            root, rows[0], config, Path(summary["log"]))
        denominator = int(geometry["valid_cfar_test_cells"])
        return {
            "split": split, "scene_id": scene_id, "role": role, "method": method,
            "status": "measured", "pfa": summary["hit_cells"] / denominator
            if denominator else math.nan,
            "hit_cells": summary["hit_cells"], "false_clusters": summary["clusters"],
            "selected": summary["selected"],
            "valid_cfar_test_cells": denominator,
            "configured_pf": summary["configured_pf"],
            "cfar_mode": geometry["csi_detection_band_mode"],
        }
    except Exception as exc:  # noqa: BLE001 - preserve per-case audit evidence
        return {
            "split": split, "scene_id": scene_id, "role": role, "method": method,
            "status": "unavailable", "pfa": math.nan, "hit_cells": math.nan,
            "false_clusters": math.nan, "selected": math.nan,
            "valid_cfar_test_cells": math.nan, "configured_pf": math.nan,
            "error": str(exc),
        }


def bootstrap_scene(rows: list[dict[str, Any]], value_key: str,
                    method: str, iterations: int = 2000) -> dict[str, Any]:
    selected = [row for row in rows if row.get("method") == method and
                row.get("status") == "measured" and finite(row.get(value_key)) is not None]
    by_scene: dict[str, list[float]] = defaultdict(list)
    for row in selected:
        value = finite(row.get(value_key))
        if math.isfinite(value):
            by_scene[str(row["scene_id"])].append(value)
    scene_values = np.asarray([float(np.mean(values)) for values in by_scene.values()], dtype=float)
    if scene_values.size == 0:
        return {"method": method, "metric": value_key, "scene_count": 0,
                "mean": None, "median": None, "bootstrap_ci95": [None, None],
                "bootstrap_unit": "scene"}
    rng = np.random.default_rng(2026091099 + len(value_key))
    samples = np.empty(iterations, dtype=float)
    for index in range(iterations):
        samples[index] = float(np.mean(rng.choice(scene_values, size=scene_values.size,
                                                  replace=True)))
    return {
        "method": method, "metric": value_key, "scene_count": int(scene_values.size),
        "mean": float(np.mean(scene_values)), "median": float(np.median(scene_values)),
        "bootstrap_ci95": [float(np.quantile(samples, 0.025)),
                            float(np.quantile(samples, 0.975))],
        "bootstrap_unit": "scene",
    }


def freeze_positive_gate(observations: list[dict[str, Any]]) -> dict[str, Any]:
    def collect(section: str, key: str) -> np.ndarray:
        values = [finite(row.get(section, {}).get(key)) for row in observations]
        return np.asarray([value for value in values if math.isfinite(value)], dtype=float)

    def threshold(section: str, key: str, quantile: float, fallback: float) -> float:
        values = collect(section, key)
        return float(np.quantile(values, quantile)) if values.size else fallback

    result = {
        "schema_version": 1,
        "source": "calibration plus validation observables only",
        "test_seen_during_freeze": False,
        "ai_training": False,
        "thresholds": {
            "delay": {
                "confidence_min": max(0.05, threshold("delay", "confidence", 0.05, 0.05)),
                "rmse_max_rad": threshold("delay", "rmse_rad", 0.99, math.inf),
            },
            "phase": {
                "confidence_min": max(0.05, threshold("phase_p1", "confidence", 0.05, 0.05)),
                "rmse_max_rad": threshold("phase_p1", "rmse_rad", 0.99, math.inf),
            },
            "joint": {
                "confidence_min": max(0.05, threshold("joint_surface", "confidence", 0.05, 0.05)),
                "rmse_max_rad": threshold("joint_surface", "rmse_rad", 0.99, math.inf),
            },
        },
        "positive_gate_not_null_rmse_envelope": True,
        "sample_count": len(observations),
    }
    return result


def cancellation(row: dict[str, Any] | None) -> float:
    if not row:
        return math.nan
    if row.get("method") == "Oracle_Known_Joint":
        return finite(row.get("metrics", {}).get("cancellation_db"))
    return finite(row.get("estimated_cancellation_db", row.get("current_cancellation_db")))


def recovery_rows(scene_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for scene in scene_records:
        if not set(scene["spec"]["label"].split("+")) & {"M1", "M2"}:
            continue
        for role in ("target_off", "target_on"):
            methods = scene["methods"].get(role, {})
            current = cancellation(methods.get("J0_Current"))
            oracle = cancellation(methods.get("Oracle_Known_Joint"))
            for method in V2_METHODS:
                row = methods.get(method, {})
                if method == "J1_D3_delay_only":
                    active = bool(row.get("delay_active"))
                elif method == "J2_P1_phase_only":
                    active = bool(row.get("phase_active"))
                elif method == "J0_Current":
                    active = False
                else:
                    active = bool(row.get("delay_active") or row.get("phase_active"))
                active = active and not bool(row.get("fallback"))
                output.append({
                    "split": scene["spec"]["split"], "scene_id": scene["spec"]["scene_id"],
                    "actual_mechanism": scene["spec"]["label"], "role": role,
                    "method": method, "branch": row.get("branch"),
                    "active": active,
                    "fallback": bool(row.get("fallback")),
                    "current_cancellation_db": current,
                    "deterministic_cancellation_db": cancellation(row),
                    "oracle_cancellation_db": oracle,
                    "fallback_opportunity_cost_db": (
                        v1.recovery_metrics(current, cancellation(row), oracle)
                        .get("recoverable_headroom_db")
                        if bool(row.get("fallback")) else 0.0),
                    **v1.recovery_metrics(current, cancellation(row), oracle),
                })
    return output


def recovery_summary(rows: list[dict[str, Any]], material_headroom_db: float = 0.5) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for method in V2_METHODS:
        items = [row for row in rows if row["method"] == method]
        finite_rows = [row for row in items if math.isfinite(finite(row.get("deterministic_gain_db")))]
        active = [row for row in finite_rows if row.get("active")]
        material = [row for row in finite_rows if finite(row.get("recoverable_headroom_db")) >= material_headroom_db]
        material_active = [row for row in material if row.get("active")]
        ratios = [finite(row.get("recovery_ratio")) for row in material_active]
        ratios = [value for value in ratios if math.isfinite(value)]
        all_fallback_costs = [finite(row.get("fallback_opportunity_cost_db"))
                              for row in items if row.get("fallback")]
        all_fallback_costs = [value for value in all_fallback_costs if math.isfinite(value)]
        material_costs = [finite(row.get("fallback_opportunity_cost_db"))
                          for row in material if row.get("fallback")]
        material_costs = [value for value in material_costs if math.isfinite(value)]
        gains = [finite(row.get("deterministic_gain_db")) for row in finite_rows]
        gains = [value for value in gains if math.isfinite(value)]
        output.append({
            "method": method,
            "row_count": len(items), "active_count": len(active),
            "all_scene_gain_db_mean": float(np.mean(gains)) if gains else math.nan,
            "material_headroom_db": material_headroom_db,
            "material_row_count": len(material),
            "material_active_count": len(material_active),
            "material_recovery_ratio_median": float(np.median(ratios)) if ratios else math.nan,
            "material_recovery_ratio_min": float(np.min(ratios)) if ratios else math.nan,
            "fallback_opportunity_cost_db_sum": float(np.sum(all_fallback_costs))
            if all_fallback_costs else 0.0,
            "fallback_count": len(all_fallback_costs),
            "material_fallback_opportunity_cost_db_sum": float(np.sum(material_costs))
            if material_costs else 0.0,
            "material_fallback_count": len(material_costs),
        })
    return output


def ai_gate(recovery: list[dict[str, Any]], cfar_rows: list[dict[str, Any]],
            target_rows: list[dict[str, Any]]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for method in SELECTIVE_METHODS:
        summary = next(row for row in recovery if row["method"] == method)
        active_material = summary["material_active_count"] > 0
        material_ratio = finite(summary["material_recovery_ratio_median"])
        current_pfa = [finite(row.get("pfa")) for row in cfar_rows
                       if row.get("split") == "test" and
                       row.get("method") == "J0_Current" and row.get("role") == "target_off"]
        candidate_pfa = [finite(row.get("pfa")) for row in cfar_rows
                         if row.get("split") == "test" and
                         row.get("method") == method and row.get("role") == "target_off"]
        current_pfa = [value for value in current_pfa if math.isfinite(value)]
        candidate_pfa = [value for value in candidate_pfa if math.isfinite(value)]
        pfa_no_regression = (bool(current_pfa) and bool(candidate_pfa) and
                             float(np.mean(candidate_pfa)) <= float(np.mean(current_pfa)))
        current_hits = [bool(row.get("legacy_hit")) for row in target_rows
                        if row.get("split") == "test" and row.get("method") == "J0_Current"]
        candidate_hits = [bool(row.get("legacy_hit")) for row in target_rows
                          if row.get("split") == "test" and row.get("method") == method]
        pd_no_regression = (bool(current_hits) and bool(candidate_hits) and
                            np.mean(candidate_hits) >= np.mean(current_hits))
        def preservation_median(method_name: str) -> float:
            values = [finite(row.get("target_preservation_db")) for row in target_rows
                      if row.get("split") == "test" and row.get("method") == method_name]
            values = [value for value in values if math.isfinite(value)]
            return float(np.median(values)) if values else math.nan
        current_preservation = preservation_median("J0_Current")
        candidate_preservation = preservation_median(method)
        preservation_no_regression = (
            math.isfinite(current_preservation) and
            math.isfinite(candidate_preservation) and
            candidate_preservation >= current_preservation)
        no_regression = pfa_no_regression and pd_no_regression and preservation_no_regression
        details[method] = {
            "material_active": active_material,
            "material_recovery_ratio_median": material_ratio,
            "production_cfar_no_pfa_regression": pfa_no_regression,
            "production_pd_no_regression": pd_no_regression,
            "target_preservation_db_current_median": current_preservation,
            "target_preservation_db_candidate_median": candidate_preservation,
            "target_preservation_no_regression": preservation_no_regression,
            "no_regression": no_regression,
            "training": False,
        }
    go = any(item["material_active"] and finite(item["material_recovery_ratio_median"]) >= 0.80
             and item["no_regression"] for item in details.values())
    active_but_insufficient = any(item["material_active"] and
                                  finite(item["material_recovery_ratio_median"]) < 0.80 and
                                  item["no_regression"] for item in details.values())
    return {
        "decision": "NO_GO_AI_M1_M2" if go else
        "REOPEN_PHYSICS_AI" if active_but_insufficient else "UNRESOLVED",
        "method_details": details,
        "reopen_does_not_authorize_training": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("screen", "formal"), default="screen")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--keep-raw", action="store_true")
    args = parser.parse_args()
    build_dir = args.build_dir.resolve()
    for binary in ("simulate_stage2_statistical", "GMTI_core"):
        if not (build_dir / binary).is_file():
            raise SystemExit(f"missing build product: {build_dir / binary}")
    provenance = git_provenance(ROOT)
    v1.validate_clean_provenance(provenance)
    before = resource_snapshot()
    if not before["nvidia_smi"] or "NVIDIA-SMI has failed" in before["nvidia_smi"]:
        raise SystemExit("V2 requires a visible CUDA device")
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    suite = json.loads((ROOT / "configs/research/ai_csi_model_mismatch_suite.json").read_text(
        encoding="utf-8"))
    base = suite["base_scenario"]
    specs = design_specs(args.mode)
    null_specs = [spec for spec in specs if spec["split"] in {"null", "calibration"}]
    quality_observations: list[dict[str, Any]] = []
    null_records: list[dict[str, Any]] = []

    # Pass 1: null gate and the observable population used for positive gate
    # freezing.  Each scene is cleaned immediately after compact extraction.
    for index, spec in enumerate(null_specs, 1):
        print(f"[v2] null {index}/{len(null_specs)} {spec['scene_id']}", flush=True)
        scene = v1.run_scene(spec, base, output, build_dir, False)
        if scene.get("status") != "pass":
            raise SystemExit(f"null scene failed: {scene}")
        enrich_scene_v2(scene, output, spec)
        quality_observations.append(scene["observables"]["target_off"])
        null_records.append(compact_scene(scene))
        v1.cleanup_scene(scene)
    gate = calibrate_null_gate(quality_observations,
                               f"physics_selective_v2_{args.mode}_null_{len(null_specs)}")

    validation_specs = [spec for spec in specs if spec["split"] in {"validation", "screen"}]
    validation_first_pass: list[dict[str, Any]] = []
    for index, spec in enumerate(validation_specs, 1):
        print(f"[v2] quality {index}/{len(validation_specs)} {spec['scene_id']}", flush=True)
        scene = v1.run_scene(spec, base, output, build_dir, False)
        if scene.get("status") != "pass":
            raise SystemExit(f"quality scene failed: {scene}")
        enrich_scene_v2(scene, output, spec)
        validation_first_pass.append(scene["observables"]["target_off"])
        v1.cleanup_scene(scene)
    positive = freeze_positive_gate(quality_observations + validation_first_pass)
    gate_path = output / "null_gate.json"
    positive_path = output / "positive_quality_gate.json"
    gate_path.write_text(json.dumps(json_safe(gate), ensure_ascii=False, indent=2,
                                    allow_nan=False) + "\n", encoding="utf-8")
    positive_path.write_text(json.dumps(json_safe(positive), ensure_ascii=False, indent=2,
                                        allow_nan=False) + "\n", encoding="utf-8")

    replay_specs = [spec for spec in specs if spec["split"] in
                    ({"validation", "screen", "test", "calibration"}
                     if args.mode == "formal" else {"screen"})]
    scene_records: list[dict[str, Any]] = []
    cfar_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    calibration_scores: list[tuple[str, np.ndarray]] = []
    validation_scores: list[tuple[str, np.ndarray]] = []
    test_scores: list[tuple[str, np.ndarray]] = []
    for index, spec in enumerate(replay_specs, 1):
        include_target_only = bool(spec["split"] in {"validation", "test"} and
                                   (spec["scene_id"].endswith("000") or
                                    spec["scene_id"].endswith("008") or
                                    "M1+M2" == spec["label"]))
        print(f"[v2] replay {index}/{len(replay_specs)} {spec['scene_id']}", flush=True)
        scene = v1.run_scene(spec, base, output, build_dir, include_target_only)
        if scene.get("status") != "pass":
            raise SystemExit(f"replay scene failed: {scene}")
        enrich_scene_v2(scene, output, spec)
        include_oracle = bool(set(spec["label"].split("+")) & {"M1", "M2"})
        execute_joint_scene_v2(scene, gate_path, positive_path, build_dir, include_oracle)
        names = list(V2_METHODS) + (["Oracle_Known_Joint"] if include_oracle else [])
        target_rows.extend(v1.evaluate_scene_targets(scene, names))
        for method in V2_METHODS:
            off_row = scene["methods"]["target_off"].get(method)
            on_row = scene["methods"]["target_on"].get(method)
            for role, row in (("target_off", off_row), ("target_on", on_row)):
                root = method_root(row or {}, method)
                if root is not None:
                    cfar_rows.append(production_cfar_row(
                        root, spec["split"], spec["scene_id"], role, method))
        if spec["split"] == "calibration":
            calibration_scores.extend(v1.score_pairs_for_scene(scene, V2_METHODS))
        elif spec["split"] in {"validation", "screen"}:
            validation_scores.extend(v1.score_pairs_for_scene(scene, V2_METHODS))
        else:
            test_scores.extend(v1.score_pairs_for_scene(scene, V2_METHODS))
        scene_records.append(compact_scene(scene))
        if not args.keep_raw:
            v1.cleanup_scene(scene)

    score_diag: dict[str, Any] = {"status": "not_run", "reason": "screen mode"}
    if calibration_scores:
        frozen = v1.calibrate_thresholds(calibration_scores, REQUESTED_PFAS)
        score_diag = {
            "status": "diagnostic_only",
            "calibration": frozen,
            "validation": v1.evaluate_frozen_scores(validation_scores, frozen),
            "test": v1.evaluate_frozen_scores(test_scores, frozen),
            "definition": "score-map all-cell diagnostic; production CFAR Pfa is the primary detector metric",
        }
    recovery = recovery_rows(scene_records)
    recovery_sum = recovery_summary(recovery, 0.5)
    recovery_sum_1db = recovery_summary(recovery, 1.0)
    bootstrap = [bootstrap_scene(cfar_rows, key, method)
                 for key in ("pfa", "false_clusters") for method in V2_METHODS]
    ai = ai_gate(recovery_sum, cfar_rows, target_rows)
    summary = {
        "schema_version": 2,
        "method_version": "Physics-Adaptive Selective Calibration V2",
        **provenance,
        "ai_training": False,
        "formal_command": " ".join([sys.executable, *sys.argv]),
        "mode": args.mode,
        "design": {
            "scene_count": len(scene_records),
            "null_count": len(null_specs),
            "validation_or_screen_count": len(validation_specs),
            "test_count": sum(spec["split"] == "test" for spec in specs),
            "test_family_counts": {label: sum(spec["split"] == "test" and
                                                spec["label"] == label for spec in specs)
                                   for label in FAMILIES},
            "angle_grid_deg": list(ANGLE_GRID_DEG),
            "independent_lhs_dimensions": ["texture_sigma", "rho"],
            "split_policy": "fresh seed/LHS/family; test zero is held out and never used for gate freeze",
        },
        "null_gate": gate,
        "positive_quality_gate": positive,
        "production_cfar": {
            "definition": "production GO-CFAR hit_cells / valid CUT cells, followed by production clustering",
            "target_metric": "target-on causal/legacy ROI detection from production detection CSV",
            "rows": cfar_rows,
            "scene_block_bootstrap": bootstrap,
        },
        "score_map_pfa_diagnostic": score_diag,
        "recovery_summary_0_5db": recovery_sum,
        "recovery_summary_1db_sensitivity": recovery_sum_1db,
        "ai_gate": ai,
        "cleanup": {
            "performed": not args.keep_raw,
            "removed": ["*.bin", "*.npy", "*.log", "*.xml", "*.f32", "*.png", "stage2 runtime directories"],
            "raw_sha256_recorded_before_cleanup": True,
        },
        "scene_records": scene_records,
    }
    write_csv(output / "scene_summary.csv", [
        {"split": item["spec"]["split"], "scene_id": item["spec"]["scene_id"],
         "family": item["spec"]["label"], "status": item["status"],
         **item.get("geometry_audit", {})}
        for item in scene_records])
    write_csv(output / "production_cfar_rows.csv", cfar_rows)
    write_csv(output / "production_cfar_bootstrap.csv", bootstrap)
    write_csv(output / "target_rows.csv", target_rows)
    write_csv(output / "recovery_metrics.csv", recovery)
    write_csv(output / "recovery_summary_0_5db.csv", recovery_sum)
    write_csv(output / "recovery_summary_1db.csv", recovery_sum_1db)
    (output / "formal_matrix_manifest.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    (output / "resource_snapshot_before.json").write_text(
        json.dumps(json_safe(before), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    (output / "resource_snapshot_after.json").write_text(
        json.dumps(json_safe(resource_snapshot()), ensure_ascii=False, indent=2,
                   allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "mode": args.mode,
                      "scene_count": len(scene_records), "ai_gate": ai["decision"],
                      "ai_training": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
