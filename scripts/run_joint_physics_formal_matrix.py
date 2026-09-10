#!/usr/bin/env python3
"""Run the clean-source formal matrix for Physics-Adaptive Joint Calibration V1.

The matrix is deliberately split before any score threshold is calibrated:

* 24 zero-mismatch calibration scenes;
* 8 validation scenes;
* 32 held-out test scenes (four scenes for each of eight mechanism families).

Every scene has an exact paired target-on/target-off background.  A
representative subset also has target-only output.  The runner uses the
production Stage2 simulator and GMTI_core, then evaluates frozen global Pfa
thresholds.  It is an experiment orchestrator, not a training pipeline.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_causal_detection import (  # noqa: E402
    detections_in_roi,
    load_manifest_row,
    roi_stats,
    target_centers,
)
from evaluate_global_fixed_pfa import (  # noqa: E402
    calibrate_thresholds,
    evaluate_causal_rows,
    evaluate_frozen_scores,
    recovery_metrics,
)
from experiment_provenance import git_provenance  # noqa: E402
from run_joint_physics_calibration import (  # noqa: E402
    METHODS,
    apply_raw_correction,
    case_observables,
    finite,
)
from run_mechanism_aware_oracle import (  # noqa: E402
    patch_xml,
    run_logged,
    single_manifest,
    summary_metrics,
)
from run_model_mismatch_audit import patch_production_xml  # noqa: E402


FORMAL_METHODS = METHODS + ("Oracle_Known_Joint",)
ABLATION_METHODS = ("A_P1_then_D3", "A_D3_original_P1")
REQUESTED_PFAS = (1.0e-2, 1.0e-3, 1.0e-4)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_safe(value: Any) -> Any:
    """Convert NumPy scalars and non-finite floats to strict JSON values."""
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.floating, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    return value


def run_output(command: list[str]) -> str:
    completed = subprocess.run(command, cwd=ROOT, text=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, check=False)
    return completed.stdout.strip()


def resource_snapshot() -> dict[str, Any]:
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "nvidia_smi": run_output(["nvidia-smi"]),
        "free_h": run_output(["free", "-h"]),
        "df_h_workspace": run_output(["df", "-h", str(ROOT)]),
    }


def validate_clean_provenance(provenance: dict[str, Any]) -> None:
    """Reject a formal run unless source identity is explicit and clean."""
    if not provenance.get("source_commit"):
        raise ValueError("formal run requires a source_commit")
    if bool(provenance.get("worktree_dirty")):
        raise ValueError("formal run requires worktree_dirty=false")


def lhs(count: int, dimensions: int, seed: int) -> np.ndarray:
    if count < 1 or dimensions < 1:
        raise ValueError("LHS count and dimensions must be positive")
    base = (np.arange(count, dtype=np.float64) + 0.5) / float(count)
    values = np.empty((count, dimensions), dtype=np.float64)
    for dimension in range(dimensions):
        rng = np.random.default_rng(seed + 7919 * dimension)
        values[:, dimension] = base[rng.permutation(count)]
    return values


MECHANISM_LABELS = (
    "zero", "M1", "M2", "M3", "M1+M2", "M1+M3", "M2+M3", "M1+M2+M3",
)


def design_specs() -> list[dict[str, Any]]:
    """Return the fixed 24/8/32 stratified design."""
    specs: list[dict[str, Any]] = []
    split_counts = (("calibration", 24), ("validation", 8), ("test", 32))
    for split, count in split_counts:
        values = lhs(count, 9, 2026091000 + len(split) * 101)
        for index, vector in enumerate(values):
            if split == "calibration":
                label = "zero"
            elif split == "validation":
                label = ("zero", "M1", "M2", "M3")[index % 4]
            else:
                label = MECHANISM_LABELS[1 + (index % 7)]
            mechanisms = set() if label == "zero" else set(label.split("+"))
            seed_offset = {"calibration": 0, "validation": 1000, "test": 2000}[split]
            scene_index = len([item for item in specs if item["split"] == split])
            multi_target = split == "test" and scene_index % 4 == 0
            spec: dict[str, Any] = {
                "scene_id": f"{split}_{scene_index:03d}",
                "split": split,
                "label": label,
                "seed": 2026100000 + seed_offset + scene_index,
                "squint_deg": float(-0.62 + 1.24 * vector[0]),
                "scan_min_deg": float(2.0 * (int(vector[1] * 4.0) % 4)),
                "range_bin": int(round(160 + 1530 * vector[2])),
                "snr_db": float(-6.0 + 12.0 * vector[3]),
                "ve_mps": float(-60.0 + 100.0 * vector[4]),
                "vn_mps": float(-20.0 + 55.0 * vector[5]),
                "texture_sigma": float(0.25 + 0.60 * vector[6]),
                "delay_ns": float(1.2 + 8.0 * vector[7]) if "M1" in mechanisms else 0.0,
                "phase_deg": float(0.04 + 0.56 * vector[8]) if "M2" in mechanisms else 0.0,
                "rho": float(0.80 + 0.19 * vector[6]) if "M3" in mechanisms else 1.0,
                "multi_target": multi_target,
                "near_clutter_ridge": bool(scene_index % 5 == 0),
                "high_speed_target": bool(abs(-60.0 + 100.0 * vector[4]) > 45.0),
            }
            specs.append(spec)
    return specs


def base_scenario() -> dict[str, Any]:
    suite_path = ROOT / "configs/research/ai_csi_model_mismatch_suite.json"
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    return copy.deepcopy(suite["base_scenario"])


def scenario_for_spec(base: dict[str, Any], spec: dict[str, Any], output_dir: Path,
                      background_input: Path | None, role: str,
                      target_only: bool = False) -> dict[str, Any]:
    scenario = copy.deepcopy(base)
    scenario["case_id"] = f"{spec['scene_id']}_{role}"
    scenario["output_dir"] = str((output_dir / "stage2").resolve())
    scenario.pop("paired_background_output_dir", None)
    if background_input is not None:
        # As with paired_background_output_dir, this is the simulator run
        # root, not its nested ``stage2`` output directory.
        scenario["background_input_dir"] = str(background_input.resolve())
    else:
        scenario.pop("background_input_dir", None)
    scenario["truth_output"] = role in {"target_on", "target_only"}
    scenario.setdefault("scene", {})["signal_only"] = bool(target_only)
    scenario["scene"]["output_signal_domain"] = "raw_lfm"
    scenario["scene"]["signal_only"] = bool(target_only)
    scenario["scan"]["scan_min_deg"] = spec["scan_min_deg"]
    scenario["random"]["random_seed"] = spec["seed"]
    scenario["targets"][0]["init"]["azimuth_offset_deg"] = spec["squint_deg"]
    scenario["targets"][0]["init"]["expected_bin"] = spec["range_bin"]
    scenario["targets"][0]["motion"]["ve_mps"] = spec["ve_mps"]
    scenario["targets"][0]["motion"]["vn_mps"] = spec["vn_mps"]
    scenario["targets"][0]["amplitude"]["snr_db"] = spec["snr_db"]
    if spec["multi_target"]:
        second = copy.deepcopy(scenario["targets"][0])
        second["target_id"] = f"{spec['scene_id']}_T1"
        second["init"]["expected_bin"] = min(1800, spec["range_bin"] + 180)
        second["init"]["azimuth_offset_deg"] = spec["squint_deg"] + 0.12
        second["motion"]["ve_mps"] = -spec["ve_mps"] * 0.35
        second["motion"]["vn_mps"] = spec["vn_mps"] * 0.4
        second["amplitude"]["snr_db"] = spec["snr_db"] - 2.0
        scenario["targets"].append(second)
    if role == "target_off":
        for target in scenario["targets"]:
            target["enabled"] = False
    else:
        for target in scenario["targets"]:
            target["enabled"] = True
    scenario["scene"]["area_clutter"]["texture_sigma"] = spec["texture_sigma"]
    scenario["scene"]["area_clutter"]["temporal_correlation_rho"] = spec["rho"]
    scenario["channel_impairments"]["channel_time_delay_ns"] = spec["delay_ns"]
    scenario["channel_impairments"]["per_pulse_phase_drift_deg"] = spec["phase_deg"]
    scenario["channel_impairments"]["enabled"] = bool(spec["delay_ns"] or spec["phase_deg"])
    return scenario


def background_scenario(base: dict[str, Any], spec: dict[str, Any], full_dir: Path,
                        paired_dir: Path) -> dict[str, Any]:
    scenario = scenario_for_spec(base, spec, full_dir, None, "background")
    scenario["case_id"] = f"{spec['scene_id']}_background"
    # Stage2's paired/background paths are run roots; the simulator appends
    # config/data/truth/... itself.  Passing ``.../stage2`` would require a
    # non-existent parent and fails during truth-directory creation.
    scenario["paired_background_output_dir"] = str(paired_dir.resolve())
    scenario["channel_impairments"] = copy.deepcopy(base["channel_impairments"])
    scenario["channel_impairments"]["enabled"] = False
    scenario["channel_impairments"]["channel_time_delay_ns"] = 0.0
    scenario["channel_impairments"]["per_pulse_phase_drift_deg"] = 0.0
    scenario["scene"]["signal_only"] = False
    return scenario


def run_simulator(scenario: dict[str, Any], root: Path, build_dir: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "scenario.json"
    path.write_text(json.dumps(scenario, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log = root / "simulate_stage2.log"
    rc = run_logged([str(build_dir / "simulate_stage2_statistical"), "--config", str(path)], log)
    return {"scenario": str(path.resolve()), "simulate_exit_code": rc,
            "status": "pass" if rc == 0 else "simulate_failed"}


def run_production(scenario: dict[str, Any], root: Path, build_dir: Path) -> dict[str, Any]:
    result = run_simulator(scenario, root, build_dir)
    if result["status"] != "pass":
        return result
    xml = root / "stage2/config/temp_config_stage2_period_0000.xml"
    if not xml.is_file():
        result["status"] = "missing_generated_xml"
        return result
    patch_production_xml(xml)
    log = root / "gmticore.log"
    rc = run_logged([str(build_dir / "GMTI_core"), str(xml),
                     "--runtime-mode=debug", "--runtime-diagnostics=on"], log)
    result["gmticore_exit_code"] = rc
    if rc != 0:
        result["status"] = "gmticore_failed"
        return result
    manifest = single_manifest(root)
    raw = sorted((root / "stage2/data").glob("*period_0000.bin"))
    result.update({
        "status": "pass",
        "manifest": str(manifest.resolve()),
        "raw": str(raw[0].resolve()) if len(raw) == 1 else None,
        "raw_sha256": sha256_file(raw[0]) if len(raw) == 1 else None,
        "metrics": summary_metrics(manifest),
    })
    return result


def run_scene(spec: dict[str, Any], base: dict[str, Any], root: Path,
              build_dir: Path, include_target_only: bool) -> dict[str, Any]:
    scene = root / "scenes" / spec["split"] / spec["scene_id"]
    # Calibration is intentionally regenerated after the null gate is frozen.
    # Remove only this exact scene directory so a deterministic seed can be
    # replayed without retaining the first pass' raw packets.
    if scene.exists():
        shutil.rmtree(scene)
    background_full = scene / "_background_generator"
    background = scene / "background"
    bg_result = run_simulator(
        background_scenario(base, spec, background_full, background),
        background_full, build_dir)
    result: dict[str, Any] = {"spec": spec, "background": bg_result, "roles": {}}
    if bg_result["status"] != "pass":
        return result
    background_input = background
    for role, target_only in (("target_off", False), ("target_on", False)):
        role_root = scene / role
        role_scenario = scenario_for_spec(
            base, spec, role_root, background_input, role, target_only)
        result["roles"][role] = run_production(role_scenario, role_root, build_dir)
    if include_target_only:
        role_root = scene / "target_only"
        role_scenario = scenario_for_spec(
            base, spec, role_root, background_input, "target_only", True)
        result["roles"]["target_only"] = run_production(role_scenario, role_root, build_dir)
    result["status"] = "pass" if all(
        item.get("status") == "pass" for item in result["roles"].values()) else "failed"
    return result


def load_power(manifest_path: Path) -> tuple[np.ndarray, dict[str, str]]:
    manifest_path = manifest_path.resolve()
    with manifest_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise RuntimeError(f"expected one CSI manifest row: {manifest_path}")
    manifest, row = manifest_path, rows[0]
    path = Path(row["after_power_path"])
    if not path.is_absolute():
        path = (manifest.parent / path).resolve()
    return np.asarray(np.load(path), dtype=np.float64), row


def manifest_for_method(method_row: dict[str, Any]) -> Path | None:
    value = method_row.get("manifest")
    if not value:
        return None
    path = Path(value)
    return path if path.is_file() else None


def run_root_from_manifest(manifest: Path) -> Path:
    for parent in [manifest.parent, *manifest.parents]:
        if (parent / "scenario.json").is_file():
            return parent
    return manifest.parents[5]


def target_peaks(on_root: Path, off_root: Path, target_root: Path | None,
                 on_manifest: Path, off_manifest: Path, target_manifest: Path | None,
                 on_power: np.ndarray, off_power: np.ndarray,
                 target_power: np.ndarray | None) -> list[dict[str, Any]]:
    on_manifest_path, on_row = load_manifest_row(on_root)
    _, off_row = load_manifest_row(off_root)
    centers = target_centers(on_root, on_manifest_path, on_row, on_power.shape)
    half_rows = int(on_row.get("target_half_doppler_bins", 2))
    half_cols = int(on_row.get("target_half_range_bins", 2))
    output: list[dict[str, Any]] = []
    for center in centers:
        on_stats = roi_stats(on_power, center, half_rows, half_cols)
        off_stats = roi_stats(off_power, center, half_rows, half_cols)
        target_stats = roi_stats(target_power, center, half_rows, half_cols) if target_power is not None else None
        on_detections = detections_in_roi(on_root, center, half_rows, half_cols)
        output.append({
            "target_id": center["target_id"],
            "row": center["row"], "col": center["col"],
            "on_peak": on_stats["peak_power"], "off_peak": off_stats["peak_power"],
            "target_only_peak": target_stats["peak_power"] if target_stats else math.nan,
            "target_preservation_db": (
                10.0 * math.log10(max(on_stats["peak_power"], np.finfo(float).tiny) /
                                  max(target_stats["peak_power"], np.finfo(float).tiny))
                if target_stats and math.isfinite(target_stats["peak_power"]) else math.nan),
            "legacy_hit": bool(on_detections),
            "on_manifest": str(on_manifest.resolve()),
            "off_manifest": str(off_manifest.resolve()),
            "target_only_manifest": str(target_manifest.resolve()) if target_manifest else None,
            "off_row": off_row,
        })
    return output


def method_rows_from_manifest(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["method"]): row for row in data.get("rows", [])}


def run_joint_for_role(role_root: Path, gate_path: Path, output_root: Path,
                       build_dir: Path, methods: Iterable[str]) -> dict[str, Any]:
    command = [sys.executable, str(ROOT / "scripts/run_joint_physics_calibration.py"),
               "--case", str(role_root), "--gate-json", str(gate_path),
               "--output-dir", str(output_root), "--build-dir", str(build_dir)]
    for method in methods:
        command.extend(["--method", method])
    log = output_root / "joint_runner.log"
    rc = run_logged(command, log)
    manifest = output_root / "joint_calibration_manifest.json"
    return {"status": "pass" if rc == 0 and manifest.is_file() else "failed",
            "exit_code": rc, "manifest": str(manifest.resolve())}


def compact_observable_from_joint(joint_root: Path) -> dict[str, Any]:
    path = joint_root / "joint_observables.json"
    return json.loads(path.read_text(encoding="utf-8"))["observables"]


def enrich_scene(scene: dict[str, Any], output: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """Attach raw-observable evidence and stable roots before replay."""
    scene["scene_root"] = str((output / "scenes" / spec["split"] /
                                spec["scene_id"]).resolve())
    for role in scene["roles"]:
        scenario_path = Path(scene["roles"][role]["scenario"])
        role_root = scenario_path.parent
        scene["roles"][role]["root"] = str(role_root.resolve())
    off_root = Path(scene["roles"]["target_off"]["root"])
    on_root = Path(scene["roles"]["target_on"]["root"])
    _, _, off_obs = case_observables(off_root, 1300.0, 80.0)
    _, _, on_obs = case_observables(on_root, 1300.0, 80.0)
    scene["observables"] = {
        "target_on": on_obs["summary"],
        "target_off": off_obs["summary"],
    }
    if "target_only" in scene["roles"]:
        target_root = Path(scene["roles"]["target_only"]["root"])
        scene["observables"]["target_only_raw"] = case_observables(
            target_root, 1300.0, 80.0)[2]["summary"]
    scene["raw_sha256"] = {
        role: scene["roles"][role].get("raw_sha256")
        for role in scene["roles"]
    }
    return scene


def execute_joint_scene(
    scene: dict[str, Any], gate_path: Path, build_dir: Path,
    methods: list[str], include_oracle: bool,
) -> None:
    """Run proposed methods and isolated known-parameter oracle for one scene."""
    scene["methods"] = {}
    for role in scene["roles"]:
        role_root = Path(scene["roles"][role]["root"])
        joint_root = Path(scene["scene_root"]) / "joint" / role
        joint = run_joint_for_role(role_root, gate_path, joint_root, build_dir, methods)
        if joint["status"] != "pass":
            raise RuntimeError(f"joint replay failed: {scene['spec']['scene_id']} {role} {joint}")
        scene["methods"][role] = method_rows_from_manifest(Path(joint["manifest"]))
    if include_oracle:
        scene["oracle"] = {}
        for role in scene["roles"]:
            role_root = Path(scene["roles"][role]["root"])
            oracle_root = Path(scene["scene_root"]) / "oracle" / role
            scene["oracle"][role] = run_known_oracle(
                role_root, scene["spec"], oracle_root, build_dir)
            scene["methods"][role]["Oracle_Known_Joint"] = scene["oracle"][role]
    if "target_only" in scene["roles"]:
        scene["observables"]["target_only"] = compact_observable_from_joint(
            Path(scene["scene_root"]) / "joint" / "target_only")
    scene["estimator_bias"] = estimator_bias_rows(scene)


def score_pairs_for_scene(
    scene: dict[str, Any], method_names: Iterable[str],
) -> list[tuple[str, np.ndarray]]:
    """Load target-off score maps while the scene is still on disk."""
    return list(raw_score_iter([scene], scene["spec"]["split"], method_names))


def method_cancellation(row: dict[str, Any] | None) -> float:
    if not row:
        return math.nan
    if row.get("method") == "Oracle_Known_Joint":
        return finite(row.get("metrics", {}).get("cancellation_db"))
    return finite(row.get("estimated_cancellation_db", row.get("current_cancellation_db")))


def recovery_rows_for_scene(scene: dict[str, Any]) -> list[dict[str, Any]]:
    """Build Oracle headroom rows from target-off clutter cancellation."""
    if not (set(scene["spec"]["label"].split("+")) & {"M1", "M2"}):
        return []
    rows: list[dict[str, Any]] = []
    for role in ("target_off", "target_on"):
        method_rows = scene["methods"].get(role, {})
        current = method_cancellation(method_rows.get("J0_Current"))
        oracle = method_cancellation(method_rows.get("Oracle_Known_Joint"))
        for method in ("J1_D3_delay_only", "J2_P1_phase_only",
                       "J3_D3_P1_joint", "J4_D3_P2_joint"):
            metrics = recovery_metrics(
                current, method_cancellation(method_rows.get(method)), oracle)
            rows.append({
                "split": scene["spec"]["split"],
                "scene_id": scene["spec"]["scene_id"],
                "actual_mechanism": scene["spec"]["label"],
                "role": role,
                "method": method,
                "current_cancellation_db": current,
                "deterministic_cancellation_db": method_cancellation(method_rows.get(method)),
                "oracle_cancellation_db": oracle,
                **metrics,
            })
    return rows


def compact_scene_record(scene: dict[str, Any]) -> dict[str, Any]:
    """Keep audit-relevant scene facts without paths to deleted raw files."""
    method_summary: dict[str, dict[str, dict[str, Any]]] = {}
    for role, rows in scene.get("methods", {}).items():
        method_summary[role] = {}
        for method, row in rows.items():
            method_summary[role][method] = {
                key: row.get(key) for key in (
                    "method", "status", "state", "fallback", "fallback_reason",
                    "estimated_delay_ns", "estimated_phase_slope_deg_per_pulse",
                    "estimated_cancellation_db", "headroom_gain_db_vs_current",
                    "reestimated_phase", "phase_method", "truth_used_in_estimator",
                ) if key in row
            }
            if method == "Oracle_Known_Joint":
                method_summary[role][method]["metrics"] = row.get("metrics", {})
    roles = {
        role: {
            key: result.get(key) for key in (
                "status", "simulate_exit_code", "gmticore_exit_code",
                "raw_sha256", "manifest",
            ) if key in result
        }
        for role, result in scene["roles"].items()
    }
    for role in roles:
        roles[role].pop("manifest", None)
    return {
        "spec": scene["spec"],
        "status": scene.get("status"),
        "background_status": scene.get("background", {}).get("status"),
        "exact_pairing": "same deterministic background_input_dir for target_off/target_on/target_only",
        "roles": roles,
        "raw_sha256": scene.get("raw_sha256", {}),
        "observables": scene.get("observables", {}),
        "methods": method_summary,
        "estimator_bias": scene.get("estimator_bias", []),
    }


def summarize_recovery(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return deterministic bootstrap median CIs for the pre-registered gate."""
    rng = np.random.default_rng(2026091001)
    output: dict[str, Any] = {}
    for method in ("J1_D3_delay_only", "J2_P1_phase_only",
                   "J3_D3_P1_joint", "J4_D3_P2_joint"):
        values = np.asarray([
            finite(row.get("recovery_ratio")) for row in rows
            if row.get("method") == method and row.get("role") == "target_off"
        ], dtype=np.float64)
        values = values[np.isfinite(values)]
        if values.size == 0:
            output[method] = {"count": 0, "median": None,
                              "bootstrap_ci95": [None, None]}
            continue
        medians = np.empty(2000, dtype=np.float64)
        for index in range(medians.size):
            medians[index] = np.median(rng.choice(values, size=values.size, replace=True))
        output[method] = {
            "count": int(values.size),
            "median": float(np.median(values)),
            "bootstrap_ci95": [float(np.quantile(medians, 0.025)),
                                float(np.quantile(medians, 0.975))],
        }
    return output


def ai_gate_summary(recovery: dict[str, Any], test_pfa: dict[str, Any]) -> dict[str, Any]:
    """Apply fixed, pre-registered M1/M2 AI gate wording to measured rows."""
    details: dict[str, Any] = {}
    for method in ("J3_D3_P1_joint", "J4_D3_P2_joint"):
        row = recovery.get(method, {})
        no_pfa_regression: dict[str, bool] = {}
        current = test_pfa.get("J0_Current", {}).get("empirical_pfa", {})
        candidate = test_pfa.get(method, {}).get("empirical_pfa", {})
        for key in (str(pfa) for pfa in REQUESTED_PFAS):
            current_value = finite(current.get(key))
            candidate_value = finite(candidate.get(key))
            no_pfa_regression[key] = bool(
                math.isfinite(current_value) and math.isfinite(candidate_value) and
                candidate_value <= current_value)
        no_regression = bool(no_pfa_regression) and all(no_pfa_regression.values())
        median = row.get("median")
        ci = row.get("bootstrap_ci95", [None, None])
        stable_high = (finite(median) >= 0.80 and finite(ci[0]) >= 0.80 and no_regression)
        stable_gap = (finite(median) < 0.80 and finite(ci[1]) < 0.80)
        details[method] = {
            "no_pfa_regression_at_all_requested_pfas": no_regression,
            "pfa_comparison": no_pfa_regression,
            "median_recovery_ge_80pct_with_ci": stable_high,
            "stable_reproducible_residual_gap_candidate": stable_gap,
        }
    no_go = any(item["median_recovery_ge_80pct_with_ci"] for item in details.values())
    reopen = any(item["stable_reproducible_residual_gap_candidate"]
                 for item in details.values())
    return {
        "pre_registered_rule": (
            "M1/M2 NO_GO when median Oracle recovery ratio >=0.80, the 95% "
            "bootstrap CI lower bound is >=0.80, and no held-out fixed-Pfa "
            "regression; reopen only for a stable CI-bounded residual gap."),
        "method_details": details,
        "decision": "NO_GO_M1_M2" if no_go else ("REOPEN_CANDIDATE" if reopen else "UNRESOLVED"),
    }


def run_known_oracle(role_root: Path, spec: dict[str, Any], output_root: Path,
                     build_dir: Path) -> dict[str, Any]:
    """Known-parameter evaluation-only joint oracle for M1/M2 scenes."""
    raw, xml, observables = case_observables(role_root, 1300.0, 80.0)
    if not (spec["delay_ns"] or spec["phase_deg"]):
        manifest = single_manifest(role_root)
        return {"method": "Oracle_Known_Joint", "status": "alias_current",
                "manifest": str(manifest.resolve()), "truth_used_only_for_oracle": True,
                "metrics": summary_metrics(manifest)}
    output_root.mkdir(parents=True, exist_ok=True)
    corrected = output_root / "stage2/data/oracle_corrected_period_0000.bin"
    packet_count = observables["summary"]["input"]["shape"][0]
    phase = (np.arange(packet_count, dtype=np.float64) * float(spec["phase_deg"])
             if spec["phase_deg"] else None)
    steps = apply_raw_correction(raw, corrected, xml,
                                 float(spec["delay_ns"]) if spec["delay_ns"] else None,
                                 phase)
    result_dir = output_root / "stage2/algorithm_result/period_0000"
    out_xml = output_root / "stage2/config/oracle_joint_config.xml"
    patch_xml(xml, corrected, result_dir, out_xml)
    log = output_root / "gmticore.log"
    rc = run_logged([str(build_dir / "GMTI_core"), str(out_xml),
                     "--runtime-mode=debug", "--runtime-diagnostics=on"], log)
    row: dict[str, Any] = {
        "method": "Oracle_Known_Joint", "status": "pass" if rc == 0 else "gmticore_failed",
        "gmticore_exit_code": rc, "truth_used_only_for_oracle": True,
        "oracle_parameters": {"delay_ns": spec["delay_ns"], "phase_deg": spec["phase_deg"]},
        "correction_steps": steps,
    }
    if rc == 0:
        manifest = single_manifest(output_root)
        row.update({"manifest": str(manifest.resolve()), "metrics": summary_metrics(manifest)})
    if corrected.is_file():
        corrected.unlink()
    return row


def raw_score_iter(records: list[dict[str, Any]], split: str,
                   method_names: Iterable[str]) -> Iterable[tuple[str, np.ndarray]]:
    for scene in records:
        if scene["spec"]["split"] != split:
            continue
        off_methods = scene["methods"]["target_off"]
        for method in method_names:
            row = off_methods.get(method)
            if not row:
                continue
            manifest = manifest_for_method(row)
            if manifest is None:
                continue
            power, _ = load_power(manifest)
            yield method, power


def evaluate_scene_targets(scene: dict[str, Any], method_names: Iterable[str]) -> list[dict[str, Any]]:
    on_root = Path(scene["roles"]["target_on"]["root"])
    off_root = Path(scene["roles"]["target_off"]["root"])
    target_root = Path(scene["roles"]["target_only"]["root"]) if "target_only" in scene["roles"] else None
    rows: list[dict[str, Any]] = []
    for method in method_names:
        on_row = scene["methods"]["target_on"].get(method)
        off_row = scene["methods"]["target_off"].get(method)
        if not on_row or not off_row:
            continue
        on_manifest = manifest_for_method(on_row)
        off_manifest = manifest_for_method(off_row)
        if on_manifest is None or off_manifest is None:
            continue
        on_power, _ = load_power(on_manifest)
        off_power, _ = load_power(off_manifest)
        target_manifest = None
        target_power = None
        if target_root is not None:
            target_row = scene["methods"]["target_only"].get(method)
            target_manifest = manifest_for_method(target_row) if target_row else None
            if target_manifest is not None:
                target_power, _ = load_power(target_manifest)
        for target in target_peaks(
                on_root, off_root, target_root, on_manifest, off_manifest,
                target_manifest, on_power, off_power, target_power):
            rows.append({
                "split": scene["spec"]["split"], "scene_id": scene["spec"]["scene_id"],
                "actual_mechanism": scene["spec"]["label"], "method": method,
                **target,
            })
    return rows


def estimator_bias_rows(scene: dict[str, Any]) -> list[dict[str, Any]]:
    on = scene["observables"]["target_on"]
    off = scene["observables"]["target_off"]
    def value(doc: dict[str, Any], section: str, key: str) -> float:
        return finite(doc.get(section, {}).get(key))
    return [{
        "split": scene["spec"]["split"],
        "scene_id": scene["spec"]["scene_id"],
        "actual_mechanism": scene["spec"]["label"],
        "multi_target": scene["spec"]["multi_target"],
        "delay_tau_off_ns": value(off, "delay", "delta_tau_ns"),
        "delay_tau_on_ns": value(on, "delay", "delta_tau_ns"),
        "delay_target_bias_ns": value(on, "delay", "delta_tau_ns") - value(off, "delay", "delta_tau_ns"),
        "phase_slope_off_deg_per_pulse": value(off, "phase_p1", "slope_deg_per_pulse"),
        "phase_slope_on_deg_per_pulse": value(on, "phase_p1", "slope_deg_per_pulse"),
        "phase_target_bias_deg_per_pulse": value(on, "phase_p1", "slope_deg_per_pulse") - value(off, "phase_p1", "slope_deg_per_pulse"),
    }]


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


def cleanup_scene(scene: dict[str, Any]) -> None:
    root = Path(scene["scene_root"])
    for name in ("_background_generator", "background", "target_on/stage2",
                 "target_off/stage2", "target_only/stage2", "oracle"):
        target = root / name
        if target.exists():
            shutil.rmtree(target)
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".bin", ".npy", ".log", ".xml"}:
            path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="temporary run root; use /tmp for clean provenance")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--suite", type=Path,
                        default=ROOT / "configs/research/ai_csi_model_mismatch_suite.json")
    parser.add_argument("--keep-raw", action="store_true",
                        help="debug only; formal delivery should omit this")
    args = parser.parse_args()
    build_dir = args.build_dir.resolve()
    for binary in ("simulate_stage2_statistical", "GMTI_core"):
        if not (build_dir / binary).is_file():
            raise SystemExit(f"missing build product: {build_dir / binary}")
    provenance = git_provenance(ROOT)
    try:
        validate_clean_provenance(provenance)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    before = resource_snapshot()
    if not before["nvidia_smi"] or "NVIDIA-SMI has failed" in before["nvidia_smi"]:
        raise SystemExit("formal run requires a visible CUDA device; nvidia-smi failed")
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"formal output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    base = json.loads(args.suite.resolve().read_text(encoding="utf-8"))["base_scenario"]
    base = copy.deepcopy(base)
    specs = design_specs()
    for spec in specs:
        spec["include_target_only"] = spec["split"] != "calibration" and (
            spec["scene_id"].endswith("000") or spec["scene_id"].endswith("002") or
            spec["label"] == "M1+M2")

    split_specs = {
        "calibration": [spec for spec in specs if spec["split"] == "calibration"],
        "validation": [spec for spec in specs if spec["split"] == "validation"],
        "test": [spec for spec in specs if spec["split"] == "test"],
    }
    scene_records: list[dict[str, Any]] = []
    null_observations: list[dict[str, Any]] = []

    # First pass: calibration scenes only estimate the null population.  Each
    # scene is deleted immediately; the deterministic seed is replayed after
    # the gate is frozen, so raw packets never accumulate across the matrix.
    for index, spec in enumerate(split_specs["calibration"], 1):
        print(f"[formal] calibration-null {index}/{len(split_specs['calibration'])} "
              f"{spec['scene_id']}", flush=True)
        scene = run_scene(spec, base, output, build_dir, False)
        if scene.get("status") != "pass":
            raise SystemExit(f"scene generation failed: {scene}")
        enrich_scene(scene, output, spec)
        null_observations.append(scene["observables"]["target_off"])
        cleanup_scene(scene)

    from run_joint_physics_calibration import calibrate_null_gate  # noqa: E402
    gate = calibrate_null_gate(
        null_observations, "formal_v1_zero_mismatch_calibration_24")
    gate_path = output / "null_gate.json"
    gate_path.write_text(
        json.dumps(json_safe(gate), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")

    calibration_scores: list[tuple[str, np.ndarray]] = []
    validation_scores: list[tuple[str, np.ndarray]] = []
    test_scores: list[tuple[str, np.ndarray]] = []
    calibration_targets: list[dict[str, Any]] = []
    validation_targets: list[dict[str, Any]] = []
    test_targets: list[dict[str, Any]] = []
    bias_rows: list[dict[str, Any]] = []
    recovery_rows: list[dict[str, Any]] = []
    processed = 0

    # Second pass: replay each scene, aggregate compact evidence, then remove
    # raw/derived arrays before moving to the next scene.
    for split in ("calibration", "validation", "test"):
        for spec in split_specs[split]:
            processed += 1
            print(f"[formal] {processed}/{len(specs)} {spec['scene_id']} replay",
                  flush=True)
            scene = run_scene(spec, base, output, build_dir,
                              bool(spec["include_target_only"]))
            if scene.get("status") != "pass":
                raise SystemExit(f"scene generation failed: {scene}")
            enrich_scene(scene, output, spec)
            labels = set(spec["label"].split("+"))
            include_oracle = bool(labels & {"M1", "M2"})
            methods = list(METHODS)
            if split == "test" and spec["label"] == "M1+M2":
                methods += list(ABLATION_METHODS)
            execute_joint_scene(scene, gate_path, build_dir, methods, include_oracle)
            target_method_names = list(methods)
            if include_oracle:
                target_method_names.append("Oracle_Known_Joint")
            target_rows = evaluate_scene_targets(scene, target_method_names)
            if split == "calibration":
                calibration_targets.extend(target_rows)
                calibration_scores.extend(score_pairs_for_scene(scene, METHODS))
            elif split == "validation":
                validation_targets.extend(target_rows)
                validation_scores.extend(score_pairs_for_scene(scene, METHODS))
            else:
                test_targets.extend(target_rows)
                test_scores.extend(score_pairs_for_scene(scene, METHODS))
            bias_rows.extend(scene["estimator_bias"])
            recovery_rows.extend(recovery_rows_for_scene(scene))
            scene_records.append(compact_scene_record(scene))
            if not args.keep_raw:
                cleanup_scene(scene)

    calibration = calibrate_thresholds(calibration_scores, REQUESTED_PFAS)
    validation_pfa = evaluate_frozen_scores(validation_scores, calibration)
    test_pfa = evaluate_frozen_scores(test_scores, calibration)
    validation_pd = evaluate_causal_rows(validation_targets, calibration)
    test_pd = evaluate_causal_rows(test_targets, calibration)
    recovery_summary = summarize_recovery(recovery_rows)
    gate_summary = ai_gate_summary(recovery_summary, test_pfa)

    write_csv(output / "calibration_target_rows.csv", calibration_targets)
    write_csv(output / "estimator_target_bias.csv", bias_rows)
    write_csv(output / "heldout_target_rows.csv", test_targets)
    write_csv(output / "validation_target_rows.csv", validation_targets)
    write_csv(output / "recovery_summary.csv", recovery_rows)
    summary = {
        "schema_version": 1,
        "method_version": "Physics-Adaptive Joint Calibration V1",
        **provenance,
        "ai_training": False,
        "formal_command": " ".join([sys.executable, *sys.argv]),
        "source_commit_required": provenance["source_commit"],
        "design": {
            "scene_count": len(scene_records),
            "calibration_count": len(split_specs["calibration"]),
            "validation_count": len(split_specs["validation"]),
            "test_count": len(split_specs["test"]),
            "target_only_count": sum(
                bool(item["spec"].get("include_target_only")) for item in scene_records),
            "family_counts": {
                label: sum(item["spec"]["label"] == label and
                           item["spec"]["split"] == "test" for item in scene_records)
                for label in MECHANISM_LABELS
            },
            "sampling": "deterministic stratified Latin hypercube; no random row split",
            "split_policy": (
                "fixed calibration/validation/test IDs with held-out seed, scan, range, "
                "mismatch-strength and mechanism-family controls"),
            "paired_background": (
                "zero-impairment C+N generated once per scene and reused by target-off, "
                "target-on and representative target-only roles"),
        },
        "null_gate": gate,
        "global_fixed_pfa": {
            "calibration": calibration,
            "validation_negative_controls": validation_pfa,
            "held_out_test_negative_controls": test_pfa,
            "validation_target_metrics": validation_pd,
            "held_out_test_target_metrics": test_pd,
            "threshold_calibration_methods": list(METHODS),
            "threshold_leakage_guard": True,
        },
        "target_contamination": {
            "definition": "target-on estimator minus exact paired target-off estimator from raw observables",
            "rows": bias_rows,
            "multi_target_rows_present": any(bool(row["multi_target"]) for row in bias_rows),
        },
        "oracle_recovery": {
            "definition": (
                "known-parameter M1/M2 evaluation oracle headroom, deterministic gain, "
                "remaining gap and gain/headroom ratio"),
            "rows": recovery_rows,
            "summary": recovery_summary,
        },
        "ai_gate": gate_summary,
        "scene_records": scene_records,
        "formal_questions": {
            "Q1_joint_m1_m2": "answered by J3/J4 vs Oracle_Known_Joint in held-out test rows",
            "Q2_m3_protection": "answered by DECORRELATED state/fallback and M3 test rows",
            "Q3_target_contamination": "answered by estimator_target_bias.csv and paired target rows",
            "Q4_global_pfa": "answered by frozen calibration thresholds and held-out test metrics",
            "Q5_oracle_gap": "answered by recovery_summary.csv and oracle_recovery.summary",
            "Q6_ai_gate": "answered by the pre-registered ai_gate decision",
        },
        "resource_snapshot_before": before,
        "resource_snapshot_after": resource_snapshot(),
        "cleanup": {
            "performed": not args.keep_raw,
            "streaming_scene_cleanup": True,
            "removed": ["*.bin", "*.npy", "*.log", "*.xml", "stage2 runtime directories"],
            "raw_sha256_recorded_before_cleanup": True,
        },
    }
    (output / "formal_matrix_manifest.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps(json_safe({
        "output_dir": str(output), "scene_count": len(scene_records),
        "test_target_rows": len(test_targets), "ai_training": False,
        "cleanup": summary["cleanup"], "ai_gate": gate_summary["decision"],
    }), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
