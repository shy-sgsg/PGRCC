#!/usr/bin/env python3
"""Audit why J6/J7 can lose target transfer on a small development set.

The audit compares target-only and clutter-only differential phase surfaces,
target-channel coherence before/after the frozen correction, beta2, and the
P1-vs-joint residual-coherence gain.  It uses no target metric as an estimator
or selector input; target-only quantities are post-hoc diagnostics only.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_target_transfer as transfer  # noqa: E402
import run_joint_physics_calibration as calibration  # noqa: E402
import run_joint_physics_formal_matrix as formal  # noqa: E402
import run_physics_ai_final_gate_v3 as v3  # noqa: E402
from experiment_provenance import (  # noqa: E402
    run_provenance_post,
    run_provenance_start,
)


METHOD_PLAN = {
    "J5_Selective_Physics_Calibration": "J5_Selective_Physics_Calibration",
    "J6_Joint_Phase_Surface": "J6_Joint_Phase_Surface",
}


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


def resource_snapshot() -> dict[str, str]:
    def run(command: list[str]) -> str:
        result = subprocess.run(command, cwd=ROOT, text=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, check=False)
        return result.stdout.strip()
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "nvidia_smi": run(["nvidia-smi"]),
        "free_h": run(["free", "-h"]),
        "df_h_workspace": run(["df", "-h", str(ROOT)]),
    }


def selected_specs(indices: list[int]) -> list[dict[str, Any]]:
    specs = v3.design_specs()
    output: list[dict[str, Any]] = []
    for index in indices:
        spec = dict(specs[index])
        spec["split"] = "target_safe_failure_mechanism"
        spec["scene_id"] = f"mechanism_{specs[index]['scene_id']}"
        spec["mechanism_source_scene_id"] = specs[index]["scene_id"]
        output.append(spec)
    return output


def corrected_target_observables(
    target_root: Path, plan: Mapping[str, Any],
) -> tuple[dict[str, float], float]:
    raw, xml, target_obs = calibration.case_observables(
        target_root, 1300.0, 80.0, include_selective_surface=True)
    x1, x2 = calibration.load_raw_channels(
        raw, calibration.xml_int(xml, "pulse_len", 11840),
        calibration.xml_int(xml, "new_protocol_channel_count", 4), 1, 2)
    fs_hz = calibration.xml_frequency_hz(xml, "fs", 60.0e6)
    spectrum1 = np.fft.fft(x1, axis=1)
    spectrum2 = np.fft.fft(x2, axis=1)
    frequency = np.fft.fftfreq(x1.shape[1], d=1.0 / fs_hz)
    positive = frequency > 0.0
    corrected = calibration._corrected_spectrum2(
        spectrum2,
        frequency,
        finite(plan.get("estimated_delay_ns")) if plan.get("apply_delay") else 0.0,
        np.asarray(plan["phase_values"], dtype=np.float64)
        if plan.get("apply_phase") and plan.get("phase_values") is not None else None,
    )
    before = calibration._coherence_metrics(spectrum1, spectrum2, positive)
    after = calibration._coherence_metrics(spectrum1, corrected, positive)
    target_power_before = float(np.sum(np.abs(spectrum2) ** 2))
    target_power_after = float(np.sum(np.abs(corrected) ** 2))
    attenuation = (10.0 * math.log10(max(target_power_after, 1.0e-30) /
                                      max(target_power_before, 1.0e-30)))
    return {
        "target_coherence_before": finite(before.get("global")),
        "target_coherence_after": finite(after.get("global")),
        "target_coherence_delta": (
            finite(after.get("global")) - finite(before.get("global"))),
        "target_theoretical_csi_attenuation_db": attenuation,
        "target_observed_pulse_coherence_before": finite(
            target_obs["summary"]["coherence"].get("pulse_median")),
    }, attenuation


def run_one_scene(
    spec: dict[str, Any], base: dict[str, Any], output: Path, build_dir: Path,
    gate: dict[str, Any], positive_gate: dict[str, Any],
    beta2_threshold: float, joint_gain_threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    scene = formal.run_scene(spec, base, output, build_dir, True)
    if scene.get("status") != "pass":
        raise RuntimeError(f"scene failed: {spec['scene_id']} {scene}")
    formal.enrich_scene(scene, output, spec)
    scene_root = Path(scene["scene_root"])
    roots = {role: Path(scene["roles"][role]["root"])
             for role in ("target_off", "target_on", "target_only")}
    _, _, off_obs = calibration.case_observables(
        roots["target_off"], 1300.0, 80.0, include_selective_surface=True)
    _, _, to_obs = calibration.case_observables(
        roots["target_only"], 1300.0, 80.0, include_selective_surface=True)
    plans = {
        method: transfer.make_frozen_plan(
            source_method, roots["target_off"], gate, positive_gate,
            scene_root / "_mechanism_plans")
        for method, source_method in METHOD_PLAN.items()
    }
    mechanism_row: dict[str, Any] = {
        "split": spec["split"],
        "scene_id": spec["scene_id"],
        "source_scene_id": spec["mechanism_source_scene_id"],
        "family": spec["label"],
        "snr_db": spec["snr_db"],
        "off_tau_ns": finite(off_obs["summary"].get("joint_surface", {}).get("tau_ns")),
        "off_beta1_deg_per_pulse": finite(off_obs["summary"].get(
            "joint_surface", {}).get("beta1_deg_per_pulse")),
        "off_beta2_deg_per_pulse2": finite(off_obs["summary"].get(
            "joint_surface", {}).get("beta2_deg_per_pulse2")),
        "target_only_tau_ns": finite(to_obs["summary"].get(
            "joint_surface", {}).get("tau_ns")),
        "target_only_beta1_deg_per_pulse": finite(to_obs["summary"].get(
            "joint_surface", {}).get("beta1_deg_per_pulse")),
        "target_only_beta2_deg_per_pulse2": finite(to_obs["summary"].get(
            "joint_surface", {}).get("beta2_deg_per_pulse2")),
        "target_only_minus_off_tau_ns": math.nan,
        "target_only_minus_off_beta1_deg_per_pulse": math.nan,
        "target_only_minus_off_beta2_deg_per_pulse2": math.nan,
        "off_residual_p1_global": finite(off_obs["summary"]["coherence"].get(
            "residual_p1_global")),
        "off_residual_joint_global": finite(off_obs["summary"]["coherence"].get(
            "residual_joint_global")),
        "off_joint_minus_p1_residual_coherence_gain": math.nan,
        "j6_beta2_abs": abs(finite(plans["J6_Joint_Phase_Surface"].get(
            "estimated_beta2_deg_per_pulse2"))),
    }
    mechanism_row["target_only_minus_off_tau_ns"] = (
        mechanism_row["target_only_tau_ns"] - mechanism_row["off_tau_ns"])
    mechanism_row["target_only_minus_off_beta1_deg_per_pulse"] = (
        mechanism_row["target_only_beta1_deg_per_pulse"] -
        mechanism_row["off_beta1_deg_per_pulse"])
    mechanism_row["target_only_minus_off_beta2_deg_per_pulse2"] = (
        mechanism_row["target_only_beta2_deg_per_pulse2"] -
        mechanism_row["off_beta2_deg_per_pulse2"])
    mechanism_row["off_joint_minus_p1_residual_coherence_gain"] = (
        mechanism_row["off_residual_joint_global"] -
        mechanism_row["off_residual_p1_global"])
    mechanism_row["nonlinear_surface_evidence"] = bool(
        mechanism_row["j6_beta2_abs"] >= beta2_threshold and
        mechanism_row["off_joint_minus_p1_residual_coherence_gain"] >=
        joint_gain_threshold)

    transfer_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    current_raw = transfer.transfer_rows_for_method(
        spec, "J0_Current", roots, roots["target_on"])
    current_rows = {row["target_id"]: row for row in current_raw}
    method_roots: dict[str, dict[str, Path]] = {"J0_Current": roots}
    for method, plan in plans.items():
        method_roots[method] = {}
        for role in ("target_off", "target_on", "target_only"):
            method_roots[method][role] = Path(transfer.run_frozen_method(
                roots[role], plan, scene_root / "mechanism" / method / role,
                build_dir)["root"])
    for method, method_root in method_roots.items():
        raw_rows = transfer.transfer_rows_for_method(
            spec, method, method_root, roots["target_on"])
        enriched = transfer.enrich_transfer_rows(raw_rows, current_rows)
        for row in enriched:
            row["method"] = method
        transfer_rows.extend(enriched)
        if method in plans:
            obs, _ = corrected_target_observables(
                roots["target_only"], plans[method])
            target_rows.append({
                "split": spec["split"], "scene_id": spec["scene_id"],
                "source_scene_id": spec["mechanism_source_scene_id"],
                "method": method,
                **obs,
                "L_target_only_mean_dB": float(np.mean([
                    finite(row.get("L_target_only_dB")) for row in enriched
                    if math.isfinite(finite(row.get("L_target_only_dB")))]))
                if any(math.isfinite(finite(row.get("L_target_only_dB")))
                       for row in enriched) else math.nan,
                "plan_beta2_deg_per_pulse2": finite(
                    plans[method].get("estimated_beta2_deg_per_pulse2")),
            })
    j5_target = next(row for row in target_rows if row["method"] == "J5_Selective_Physics_Calibration")
    j6_target = next(row for row in target_rows if row["method"] == "J6_Joint_Phase_Surface")
    mechanism_row["j6_target_coherence_minus_j5"] = (
        j6_target["target_coherence_after"] - j5_target["target_coherence_after"])
    mechanism_row["j6_target_theoretical_attenuation_db"] = j6_target[
        "target_theoretical_csi_attenuation_db"]
    mechanism_row["nearly_linear_phase"] = (
        mechanism_row["j6_beta2_abs"] < beta2_threshold)
    mechanism_row["j6_overcorrection_suspected"] = bool(
        mechanism_row["nearly_linear_phase"] and (
            mechanism_row["off_joint_minus_p1_residual_coherence_gain"] <
            joint_gain_threshold or
            mechanism_row["j6_target_coherence_minus_j5"] < 0.0))
    compact = {
        "spec": spec,
        "status": scene["status"],
        "plans": {method: transfer.plan_summary(plan)
                  for method, plan in plans.items()},
        "mechanism_row": mechanism_row,
    }
    if scene_root.exists():
        shutil.rmtree(scene_root)
    return transfer_rows, target_rows, compact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/target_safe_failure_mechanism_audit")
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs/research/target_safe_failure_mechanism_audit.json")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--formal-dir", type=Path,
                        default=ROOT / "outputs/physics_adaptive_selective_v2_formal")
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("ai_training") is not False:
        raise RuntimeError("mechanism audit requires ai_training=false")
    formal_dir = args.formal_dir.resolve()
    gate_path = formal_dir / "null_gate.json"
    positive_gate_path = formal_dir / "positive_quality_gate.json"
    start = run_provenance_start(ROOT, (config_path, gate_path, positive_gate_path))
    if start["source_worktree_dirty_before"] is not False:
        raise SystemExit("mechanism audit requires a clean source worktree at start")
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    before = resource_snapshot()
    if not before["nvidia_smi"] or "NVIDIA-SMI has failed" in before["nvidia_smi"]:
        raise SystemExit("mechanism audit requires a visible CUDA device")
    base = formal.base_scenario()
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    positive_gate = json.loads(positive_gate_path.read_text(encoding="utf-8"))
    specs = selected_specs([int(item) for item in config["scene_indices"]])
    transfer_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    scene_records: list[dict[str, Any]] = []
    mechanism_rows: list[dict[str, Any]] = []
    for index, spec in enumerate(specs, 1):
        print(f"[mechanism-audit] {index}/{len(specs)} {spec['scene_id']}", flush=True)
        tr, target, compact = run_one_scene(
            spec, base, output, args.build_dir.resolve(), gate, positive_gate,
            float(config["beta2_abs_min_deg_per_pulse2"]),
            float(config["joint_vs_p1_residual_coherence_gain_min"]))
        transfer_rows.extend(tr)
        target_rows.extend(target)
        mechanism_rows.append(compact["mechanism_row"])
        scene_records.append(compact)
    write_csv(output / "mechanism_transfer_rows.csv", transfer_rows)
    write_csv(output / "mechanism_target_rows.csv", target_rows)
    write_csv(output / "mechanism_surface_rows.csv", mechanism_rows)
    (output / "resource_snapshot_before.json").write_text(
        json.dumps(json_safe(before), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    after = resource_snapshot()
    (output / "resource_snapshot_after.json").write_text(
        json.dumps(json_safe(after), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    post = run_provenance_post(ROOT, start)
    (output / "provenance_start.json").write_text(
        json.dumps(json_safe(start), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (output / "provenance_post.json").write_text(
        json.dumps(json_safe(post), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "analysis": "Target-safe failure mechanism audit",
        "source_commit": start["source_commit"],
        "source_worktree_dirty_before": start["source_worktree_dirty_before"],
        "provenance_start": start,
        "provenance_post": post,
        "ai_training": False,
        "development_only": True,
        "test_v3_target_metrics_used_as_selector_input": False,
        "design": {
            "scene_indices": config["scene_indices"],
            "scene_count": len(specs),
            "comparison": "target-only differential phase surface minus OFF clutter correction surface",
            "coherence": "target channel global coherence before/after frozen correction",
            "theoretical_csi_attenuation": "spectral target-only power ratio under frozen correction",
            "overcorrection_rule": "nearly linear beta2 plus insufficient joint gain or J6 coherence below J5",
        },
        "counts": {"scene_count": len(scene_records),
                   "transfer_rows": len(transfer_rows),
                   "target_rows": len(target_rows),
                   "surface_rows": len(mechanism_rows)},
        "outputs": {
            "transfer": "mechanism_transfer_rows.csv",
            "target": "mechanism_target_rows.csv",
            "surface": "mechanism_surface_rows.csv",
        },
        "cleanup": {"raw_scene_cleanup": True,
                     "retained": "compact mechanism/surface/coherence/target-transfer rows and provenance"},
        "scene_records": scene_records,
    }
    (output / "target_safe_failure_mechanism_manifest.json").write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps(json_safe({"output_dir": str(output),
                                "scene_count": len(specs),
                                "ai_training": False}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
