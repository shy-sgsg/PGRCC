#!/usr/bin/env python3
"""Run one fail-closed Phase-R3 seed through the existing Stage1/production path."""

from __future__ import annotations

import argparse
import copy
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_delay_stage1_formal as stage1  # noqa: E402


def _source_identity() -> dict[str, object]:
    files = {
        "pilot_runner": Path(__file__).resolve(),
        "stage1_runner": Path(stage1.__file__).resolve(),
        "production_cfar_period_identity_source": ROOT / "src/processOnePeriod.cpp",
        "stage2_delay_correction_operator": ROOT / "scripts/estimate_channel_delay.py",
        "stage1_config": stage1.CONFIG,
        "scene_template": stage1.TEMPLATE,
        "design": ROOT / "docs/research/phase_r/05_experiment_design.md",
    }
    identities: dict[str, object] = {}
    for name, path in files.items():
        identities[name] = {
            "path": str(path),
            "exists": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else None,
            "sha256": stage1._sha256(path) if path.is_file() else None,
        }
    return {
        "git": stage1._git_snapshot(),
        "tracked_status": subprocess.run(
            ["git", "status", "--short", "--untracked-files=no"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines(),
        "files": identities,
        "build": stage1._build_identity(),
    }


def _pilot_block(config: dict[str, object], seed: int) -> dict[str, object]:
    points = stage1._working_point_registry(config)
    point = points["base"]
    return {
        "group": "base",
        "working_point": "base",
        "seed": int(seed),
        "target_velocity_mps": 6.7,
        "target_snr_db": 30.0,
        "point": copy.deepcopy(point),
    }


def run_pilot(output_root: Path, seed: int) -> dict[str, object]:
    output_root = Path(output_root).expanduser().resolve()
    if output_root.exists() and (not output_root.is_dir() or any(output_root.iterdir())):
        raise ValueError(f"refusing to overwrite non-empty output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    config = stage1.load_stage1_config()
    config["scenario_template"] = str(stage1.TEMPLATE)
    block = _pilot_block(config, seed)
    identity = stage1._scene_identity_for_block(block, 4.0, 3)
    case_root = output_root / "cases" / str(identity["case_id"])
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--output-root",
        str(output_root),
        "--seed",
        str(seed),
    ]
    manifest: dict[str, object] = {
        "schema_version": 1,
        "experiment": "PGRCC Phase R3 minimal four-condition CUDA production causal pilot",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "in_progress",
        "command": {"argv": command, "shell": shlex.join(command)},
        "design": {
            "conditions": ["A0_Ideal", "A1_Current_unknown_error", "A2_Known_error_correction_upper_bound", "A3_Blind_target_free_estimated_correction"],
            "paired_roles": ["ON", "OFF", "TO"],
            "delay_error_ns": 4.0,
            "seed": int(seed),
            "period_count": 3,
            "ai_training": False,
            "router_enabled": False,
        },
        "scene_identity": identity,
        "source_identity_before": _source_identity(),
        "gpu_status_before": stage1._gpu_status(),
        "disk_status_before": stage1._disk_status(output_root),
        "status_labels": {
            "IMPLEMENTED": "source/config inspection; not a runtime claim",
            "EXECUTED": False,
            "EVALUABLE": False,
            "PERFORMANCE_VERIFIED": False,
        },
    }
    stage1._write_json(output_root / "manifest.json", manifest)
    case_manifest = stage1.run_stage1_case(
        config,
        identity,
        case_root,
        "local",
    )
    manifest["case_root"] = str(case_root)
    manifest["case_manifest"] = str(case_root / "case_manifest.json")
    manifest["case_status"] = case_manifest.get("status")
    manifest["case_result"] = case_manifest
    manifest["source_identity_after"] = _source_identity()
    manifest["gpu_status_after"] = stage1._gpu_status()
    manifest["disk_status_after"] = stage1._disk_status(output_root)
    simulation = case_manifest.get("simulation", {})
    conditions = case_manifest.get("conditions", {})
    production_executed = any(
        isinstance(roles, dict)
        and any(
            isinstance(record, dict) and "production_returncode" in record
            for record in roles.get("production", {}).values()
        )
        for roles in conditions.values()
    ) if isinstance(conditions, dict) else False
    manifest["status_labels"] = {
        "IMPLEMENTED": True,
        "EXECUTED": bool(simulation) or production_executed,
        "EVALUABLE": bool(
            case_manifest.get("area_clutter_gate", {}).get("status") == "passed"
            and case_manifest.get("production_smoke_gate", {}).get("status") == "passed"
        ),
        "PERFORMANCE_VERIFIED": False,
        "note": "single-seed evidence cannot establish statistical performance verification",
    }
    manifest["status"] = str(case_manifest.get("status", "unknown"))
    stage1._write_json(output_root / "manifest.json", manifest)
    return manifest


def run_abc_extension(output_root: Path, seed: int) -> dict[str, object]:
    """Run only A/B/C for an independent seed after the seed-101 gate passes."""

    output_root = Path(output_root).expanduser().resolve()
    if output_root.exists() and (not output_root.is_dir() or any(output_root.iterdir())):
        raise ValueError(f"refusing to overwrite non-empty output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    config = stage1.load_stage1_config()
    config["scenario_template"] = str(stage1.TEMPLATE)
    block = _pilot_block(config, seed)
    identity = stage1._scene_identity_for_block(block, 4.0, 3)
    case_root = output_root / "cases" / str(identity["case_id"])
    selected_conditions = [
        "A0_Ideal",
        "A1_Current_unknown_error",
        "A2_Known_error_correction_upper_bound",
    ]
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--abc-extension-output-root",
        str(output_root),
        "--seed",
        str(seed),
    ]
    manifest: dict[str, object] = {
        "schema_version": 1,
        "experiment": "PGRCC Phase R3 independent-seed A/B/C CUDA production extension",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "in_progress",
        "command": {"argv": command, "shell": shlex.join(command)},
        "design": {
            "conditions": selected_conditions,
            "paired_roles": ["ON", "OFF", "TO"],
            "delay_error_ns": 4.0,
            "seed": int(seed),
            "period_count": 3,
            "blind_estimator_run": False,
            "ai_training": False,
            "router_enabled": False,
        },
        "scene_identity": identity,
        "source_identity_before": _source_identity(),
        "gpu_status_before": stage1._gpu_status(),
        "disk_status_before": stage1._disk_status(output_root),
        "status_labels": {
            "IMPLEMENTED": True,
            "EXECUTED": False,
            "EVALUABLE": False,
            "PERFORMANCE_VERIFIED": False,
        },
    }
    stage1._write_json(output_root / "manifest.json", manifest)
    case_manifest = stage1.run_stage1_case(
        config,
        identity,
        case_root,
        "local",
        conditions_to_run=selected_conditions,
    )
    manifest["case_root"] = str(case_root)
    manifest["case_manifest"] = str(case_root / "case_manifest.json")
    manifest["case_status"] = case_manifest.get("status")
    manifest["case_result"] = case_manifest
    manifest["source_identity_after"] = _source_identity()
    manifest["gpu_status_after"] = stage1._gpu_status()
    manifest["disk_status_after"] = stage1._disk_status(output_root)
    simulation = case_manifest.get("simulation", {})
    conditions = case_manifest.get("conditions", {})
    production_executed = any(
        isinstance(roles, dict)
        and any(
            isinstance(record, dict) and "production_returncode" in record
            for record in roles.get("production", {}).values()
        )
        for roles in conditions.values()
    ) if isinstance(conditions, dict) else False
    manifest["status_labels"] = {
        "IMPLEMENTED": True,
        "EXECUTED": bool(simulation) or production_executed,
        "EVALUABLE": bool(
            case_manifest.get("area_clutter_gate", {}).get("status") == "passed"
            and case_manifest.get("known_correction_physical_closure", {}).get("status") == "passed"
            and case_manifest.get("status") == "completed"
        ),
        "PERFORMANCE_VERIFIED": False,
        "note": "independent-seed execution is pending metric-level cross-seed evaluation",
    }
    manifest["status"] = str(case_manifest.get("status", "unknown"))
    stage1._write_json(output_root / "manifest.json", manifest)
    return manifest


def resume_a0_production_smoke(case_root: Path) -> dict[str, object]:
    """Rerun only the A0 CUDA production smoke from preserved Stage2 inputs."""

    case_root = Path(case_root).expanduser().resolve()
    case_manifest_path = case_root / "case_manifest.json"
    if not case_manifest_path.is_file():
        raise ValueError(f"case manifest missing: {case_manifest_path}")
    case_manifest = stage1._read_json(case_manifest_path)
    area_gate = case_manifest.get("area_clutter_gate")
    if not isinstance(area_gate, dict) or area_gate.get("status") != "passed":
        raise ValueError("resume requires a passed observed Stage2 area-clutter gate")
    conditions = case_manifest.get("conditions")
    simulation = case_manifest.get("simulation")
    if not isinstance(conditions, dict) or not isinstance(simulation, dict):
        raise ValueError("resume case is missing conditions or Stage2 simulation records")
    a0 = conditions.get("A0_Ideal")
    if not isinstance(a0, dict):
        raise ValueError("resume case is missing A0_Ideal records")
    production = a0.get("production")
    if not isinstance(production, dict) or not isinstance(production.get("ON"), dict):
        raise ValueError("resume case is missing the original A0_Ideal_ON production record")
    original = production["ON"]
    if original.get("production_path_gate", {}).get("status") == "passed":
        raise ValueError("A0 production smoke gate already passed; refusing duplicate resume")
    branch_name = "A0_Ideal_ON_retry_cfar_period_identity"
    retry_branch_root = case_root / "production" / branch_name
    if retry_branch_root.exists():
        raise ValueError(f"refusing to overwrite existing retry branch: {retry_branch_root}")

    try:
        period_count = int(original["period_count_expected"])
        input_path = Path(str(original["input_path"]))
        source_xml = Path(str(original["source_xml"]))
        scene_a0 = simulation["A0_ON"]
        scene_output = Path(str(scene_a0["output_dir"]))
        layout = case_manifest["protocol_layout"]
        input_mode = str(case_manifest["input_mode"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"resume case metadata is incomplete: {exc}") from exc
    if not isinstance(layout, dict):
        raise ValueError("resume case protocol_layout is invalid")
    from scripts import run_track_manager_e2e as e2e

    truth = e2e._truth_by_period(scene_output, period_count)
    if len(truth) != period_count:
        raise ValueError(
            f"A0 truth period count mismatch: expected={period_count} actual={len(truth)}"
        )

    output_root = case_root.parent.parent
    outer_manifest_path = output_root / "manifest.json"
    outer_manifest = (
        stage1._read_json(outer_manifest_path)
        if outer_manifest_path.is_file()
        else {}
    )
    source_identity_before = _source_identity()
    command = [sys.executable, str(Path(__file__).resolve()), "--resume-case-root", str(case_root)]
    xml_overrides = original.get("xml_overrides", {})
    if not isinstance(xml_overrides, dict):
        raise ValueError("original A0 XML overrides are invalid")
    branch_record = stage1.run_production_branch(
        case_root,
        branch_name,
        input_path,
        source_xml,
        truth,
        period_count,
        input_mode,
        layout,
        truth_path=scene_output / "truth",
        xml_overrides=xml_overrides,
    )
    evidence_gate = stage1.audit_production_path_evidence(branch_record)
    branch_record["production_path_gate"] = evidence_gate
    retry_record: dict[str, object] = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": {"argv": command, "shell": shlex.join(command)},
        "reason": "repair CFAR diagnostic period identity; preserve Stage2 and original failed smoke",
        "source_identity_before": source_identity_before,
        "branch_record": branch_record,
        "evidence_gate": evidence_gate,
        "source_identity_after": _source_identity(),
        "gpu_status_after": stage1._gpu_status(),
        "disk_status_after": stage1._disk_status(output_root),
    }
    case_manifest.setdefault("production_smoke_retries", []).append(retry_record)
    case_manifest["production_smoke_gate_before_retry"] = case_manifest.get("production_smoke_gate")
    case_manifest["production_smoke_gate"] = evidence_gate
    case_manifest["status"] = (
        "production_smoke_passed"
        if evidence_gate.get("status") == "passed"
        else "production_path_failed"
    )
    stage1._write_json(case_manifest_path, case_manifest)

    if outer_manifest:
        outer_manifest["status"] = case_manifest["status"]
        outer_manifest["resumed_production_smoke"] = retry_record
        outer_manifest["case_result"] = case_manifest
        outer_manifest["source_identity_after"] = _source_identity()
        outer_manifest["gpu_status_after"] = stage1._gpu_status()
        outer_manifest["disk_status_after"] = stage1._disk_status(output_root)
        outer_manifest["status_labels"] = {
            "IMPLEMENTED": True,
            "EXECUTED": True,
            "EVALUABLE": evidence_gate.get("status") == "passed",
            "PERFORMANCE_VERIFIED": False,
            "note": "single-seed smoke gate only; no causal algorithm comparison or statistical claim",
        }
        stage1._write_json(outer_manifest_path, outer_manifest)
    return {
        "status": case_manifest["status"],
        "case_root": str(case_root),
        "retry_branch": str(retry_branch_root),
        "evidence_gate": evidence_gate,
    }


def continue_production_comparisons(case_root: Path) -> dict[str, object]:
    """Check A0/known closure, then run the frozen paired CUDA conditions."""

    case_root = Path(case_root).expanduser().resolve()
    case_manifest_path = case_root / "case_manifest.json"
    if not case_manifest_path.is_file():
        raise ValueError(f"case manifest missing: {case_manifest_path}")
    case_manifest = stage1._read_json(case_manifest_path)
    if case_manifest.get("status") not in {"production_smoke_passed", "correction_model_updated"}:
        raise ValueError("continuation requires a passed A0 production smoke")
    if case_manifest.get("known_correction_physical_closure") is not None:
        raise ValueError("known-correction closure already evaluated; refusing duplicate continuation")
    area_gate = case_manifest.get("area_clutter_gate")
    if not isinstance(area_gate, dict) or area_gate.get("status") != "passed":
        raise ValueError("continuation requires a passed observed Stage2 area-clutter gate")
    conditions = case_manifest.get("conditions")
    if not isinstance(conditions, dict):
        raise ValueError("continuation case is missing condition records")
    layout = case_manifest.get("protocol_layout")
    if not isinstance(layout, dict):
        raise ValueError("continuation case protocol_layout is invalid")
    period_count = len(case_manifest.get("simulation", {}).get("A0_ON", {}).get("period_paths", []))
    if period_count <= 0:
        period_count = int(case_manifest.get("scene_identity", {}).get("period_count", 0))
    if period_count <= 0:
        period_count = int(case_manifest.get("conditions", {}).get("A0_Ideal", {}).get("production", {}).get("ON", {}).get("period_count_expected", 0))
    if period_count <= 0:
        raise ValueError("continuation cannot determine the registered period count")

    def role_periods(condition: str, role: str) -> list[Path]:
        prep = conditions[condition].get("input_preparation", {}).get(role, {})
        values = prep.get("period_paths", []) if isinstance(prep, dict) else []
        paths = [Path(str(value)).expanduser().resolve() for value in values] if isinstance(values, list) else []
        if len(paths) != period_count or any(not path.is_file() for path in paths):
            raise ValueError(
                f"{condition}/{role} prepared inputs incomplete: expected={period_count} actual={len(paths)}"
            )
        return paths

    closure_by_role: dict[str, object] = {}
    for role in ("ON", "OFF", "TO"):
        closure_by_role[role] = stage1.audit_fused_input_closure(
            role_periods("A0_Ideal", role),
            role_periods("A2_Known_error_correction_upper_bound", role),
            layout,
        )
    closure_passed = all(
        isinstance(value, dict) and value.get("status") == "passed"
        for value in closure_by_role.values()
    )
    case_manifest["known_correction_physical_closure"] = {
        "status": "passed" if closure_passed else "failed",
        "by_role": closure_by_role,
        "definition": "A0 versus evaluator-only known-correction F1/F2, relative complex RMS <= 1e-3",
    }
    if not closure_passed:
        case_manifest["status"] = "known_correction_closure_failed"
        stage1._write_json(case_manifest_path, case_manifest)
        return {
            "status": case_manifest["status"],
            "case_root": str(case_root),
            "closure": case_manifest["known_correction_physical_closure"],
            "production_comparisons_started": False,
        }

    simulation = case_manifest.get("simulation")
    if not isinstance(simulation, dict) or not isinstance(simulation.get("A0_ON"), dict):
        raise ValueError("continuation is missing A0 Stage2 truth provenance")
    scene_output = Path(str(simulation["A0_ON"]["output_dir"]))
    source_xml = Path(str(case_manifest.get("source_xml", "")))
    if not source_xml.is_file():
        raise ValueError(f"production source XML missing: {source_xml}")
    from scripts import run_track_manager_e2e as e2e

    truth = e2e._truth_by_period(scene_output, period_count)
    if len(truth) != period_count:
        raise ValueError(f"A0 truth period count mismatch: expected={period_count} actual={len(truth)}")
    input_mode = str(case_manifest.get("input_mode", "local"))
    xml_overrides = {
        "csi_metrics_enable": "true",
        "csi_metrics_dump_power_maps": "true",
        "csi_metrics_dump_intermediate_maps": "true",
        "csi_metrics_beam_id": int(layout["beam_id"]),
    }
    retry_entries = case_manifest.get("production_smoke_retries", [])
    if not isinstance(retry_entries, list) or not retry_entries:
        raise ValueError("continuation requires preserved A0 smoke retry evidence")
    smoke_branch_record = retry_entries[-1].get("branch_record")
    if not isinstance(smoke_branch_record, dict):
        raise ValueError("A0 smoke retry branch record is invalid")
    comparison_records: dict[str, dict[str, object]] = {
        "A0_Ideal": {"ON": smoke_branch_record}
    }
    case_manifest["status"] = "production_comparison_in_progress"
    case_manifest["production_comparison_records"] = comparison_records
    stage1._write_json(case_manifest_path, case_manifest)

    branch_failure: dict[str, object] | None = None
    for condition in (
        "A0_Ideal",
        "A1_Current_unknown_error",
        "A2_Known_error_correction_upper_bound",
        "A3_Blind_target_free_estimated_correction",
    ):
        roles = comparison_records.setdefault(condition, {})
        for role in ("ON", "OFF", "TO"):
            if condition == "A0_Ideal" and role == "ON":
                continue
            preparation = conditions[condition].get("input_preparation", {}).get(role, {})
            if not isinstance(preparation, dict):
                raise ValueError(f"missing input preparation for {condition}/{role}")
            if preparation.get("status") == "NOT_AVAILABLE":
                roles[role] = {
                    "status": "NOT_AVAILABLE",
                    "reason": preparation.get("reason", "blind_estimator_unavailable"),
                    "condition": condition,
                    "role": role,
                }
                continue
            periods = role_periods(condition, role)
            input_dir = case_root / "inputs" / "production" / condition / role
            input_dir.mkdir(parents=True, exist_ok=True)
            for index, period_path in enumerate(periods):
                link = input_dir / f"period_{index:04d}.bin"
                if link.exists() or link.is_symlink():
                    if link.resolve() != period_path.resolve():
                        raise ValueError(f"refusing to replace mismatched prepared link: {link}")
                else:
                    link.symlink_to(period_path.resolve())
            branch_name = f"{condition}_{role}"
            branch_root = case_root / "production" / branch_name
            if branch_root.exists():
                raise ValueError(f"refusing to overwrite existing production branch: {branch_root}")
            branch_record = stage1.run_production_branch(
                case_root,
                branch_name,
                input_dir,
                source_xml,
                truth,
                period_count,
                input_mode,
                layout,
                truth_path=scene_output / "truth",
                xml_overrides=xml_overrides,
            )
            evidence_gate = stage1.audit_production_path_evidence(branch_record)
            branch_record["production_path_gate"] = evidence_gate
            roles[role] = branch_record
            stage1._write_json(case_root / "production" / branch_name / "branch_manifest.json", branch_record)
            if evidence_gate.get("status") != "passed":
                branch_failure = {
                    "branch": branch_name,
                    "reason": "production_path_evidence_gate_failed",
                    "evidence_gate": evidence_gate,
                }
                break
        if branch_failure is not None:
            break
        case_manifest["production_comparison_records"] = comparison_records
        case_manifest["production_comparison_last_completed_condition"] = condition
        stage1._write_json(case_manifest_path, case_manifest)

    if branch_failure is not None:
        for condition in (
            "A0_Ideal",
            "A1_Current_unknown_error",
            "A2_Known_error_correction_upper_bound",
            "A3_Blind_target_free_estimated_correction",
        ):
            roles = comparison_records.setdefault(condition, {})
            for role in ("ON", "OFF", "TO"):
                roles.setdefault(role, {
                    "status": "NOT_RUN",
                    "reason": "earlier_production_comparison_failed",
                    "condition": condition,
                    "role": role,
                })
        case_manifest["status"] = "production_comparison_failed"
        case_manifest["production_comparison_failure"] = branch_failure
    else:
        case_manifest["status"] = "production_comparisons_executed"
    case_manifest["production_comparison_records"] = comparison_records
    case_manifest["source_identity_after_production"] = _source_identity()
    stage1._write_json(case_manifest_path, case_manifest)

    outer_manifest_path = case_root.parent.parent / "manifest.json"
    if outer_manifest_path.is_file():
        outer_manifest = stage1._read_json(outer_manifest_path)
        outer_manifest["status"] = case_manifest["status"]
        outer_manifest["case_result"] = case_manifest
        outer_manifest["source_identity_after"] = _source_identity()
        outer_manifest["gpu_status_after"] = stage1._gpu_status()
        outer_manifest["disk_status_after"] = stage1._disk_status(outer_manifest_path.parent)
        outer_manifest["status_labels"] = {
            "IMPLEMENTED": True,
            "EXECUTED": True,
            "EVALUABLE": False,
            "PERFORMANCE_VERIFIED": False,
            "note": "production branches executed; metric-level evaluation is still pending",
        }
        stage1._write_json(outer_manifest_path, outer_manifest)
    return {
        "status": case_manifest["status"],
        "case_root": str(case_root),
        "known_correction_physical_closure": case_manifest["known_correction_physical_closure"],
        "production_comparison_failure": branch_failure,
        "completed_branches": {
            condition: sorted(roles)
            for condition, roles in comparison_records.items()
        },
    }


def repair_correction_inputs(case_root: Path) -> dict[str, object]:
    """Prepare isolated A2/A3 inputs using the measured Stage2 delay operator."""

    case_root = Path(case_root).expanduser().resolve()
    case_manifest_path = case_root / "case_manifest.json"
    if not case_manifest_path.is_file():
        raise ValueError(f"case manifest missing: {case_manifest_path}")
    case_manifest = stage1._read_json(case_manifest_path)
    if case_manifest.get("status") not in {
        "known_correction_closure_failed",
        "correction_model_updated",
    }:
        raise ValueError("correction-model repair requires a recorded A0/known closure failure")
    if case_manifest.get("production_smoke_gate", {}).get("status") != "passed":
        raise ValueError("correction-model repair requires a passed A0 production smoke")
    conditions = case_manifest.get("conditions")
    layout = case_manifest.get("protocol_layout")
    estimator = case_manifest.get("delay_estimator")
    if not isinstance(conditions, dict) or not isinstance(layout, dict) or not isinstance(estimator, dict):
        raise ValueError("repair case is missing prepared conditions, layout or estimator provenance")
    try:
        delay_truth_ns = float(case_manifest["delay_error_ns"])
        delay_estimate_ns = float(estimator["selected_delay_ns"])
        calibration_input = Path(str(case_manifest["calibration_input"]["path"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"repair correction metadata is incomplete: {exc}") from exc
    if estimator.get("truth_used_in_estimator") is not False or not calibration_input.is_file():
        raise ValueError("blind correction provenance is invalid or its target-free input is missing")
    repair_attempts = case_manifest.get("correction_model_repair_attempts", [])
    if not isinstance(repair_attempts, list):
        repair_attempts = []
    prior_repair = case_manifest.get("correction_model_repair")
    if isinstance(prior_repair, dict):
        repair_attempts.append({
            **prior_repair,
            "status": "invalidated_before_closure",
            "reason_invalidated": "ON/OFF/TO shared one derived destination; none of those files were used for a closure or production run",
        })
    generation = len(repair_attempts) + 1
    generation_name = f"correction_model_matched_inverse_v{generation}"
    repair_root = case_root / generation_name
    if repair_root.exists():
        raise ValueError(f"refusing to overwrite correction repair directory: {repair_root}")

    previous: dict[str, object] = {}
    repaired: dict[str, dict[str, object]] = {}
    preparation_history = case_manifest.get("correction_input_preparation_history", [])
    if not isinstance(preparation_history, list):
        preparation_history = []
    for condition in (
        "A2_Known_error_correction_upper_bound",
        "A3_Blind_target_free_estimated_correction",
    ):
        condition_record = conditions.get(condition)
        if not isinstance(condition_record, dict):
            raise ValueError(f"repair case is missing {condition}")
        old_preparation = condition_record.get("input_preparation")
        if not isinstance(old_preparation, dict):
            raise ValueError(f"repair case is missing {condition} input preparation")
        previous[condition] = old_preparation
        preparation_history.append({
            "generation": case_manifest.get("correction_model_repair", {}).get("directory", "original_phase_ramp")
            if isinstance(case_manifest.get("correction_model_repair"), dict)
            else "original_phase_ramp",
            "condition": condition,
            "roles": {
                role: [str(path) for path in item.get("period_paths", [])]
                for role, item in old_preparation.items()
                if isinstance(item, dict)
            },
        })
        repaired[condition] = {}
        for role in ("ON", "OFF", "TO"):
            old_role = old_preparation.get(role)
            if not isinstance(old_role, dict):
                raise ValueError(f"repair case is missing {condition}/{role} input preparation")
            raw_values = old_role.get("raw_paths")
            raw_paths = [Path(str(value)).expanduser().resolve() for value in raw_values] if isinstance(raw_values, list) else []
            if not raw_paths or any(not path.is_file() for path in raw_paths):
                raise ValueError(f"raw Stage2 input missing for {condition}/{role}")
            prepared = stage1.prepare_condition_inputs(
                repair_root / condition / role,
                condition,
                raw_paths,
                calibration_input,
                delay_truth_ns,
                delay_estimate_ns,
                layout,
            )
            prepared["repair_generation"] = generation_name
            repaired[condition][role] = prepared

    old_closure = case_manifest.get("known_correction_physical_closure")
    attempts = case_manifest.get("known_correction_physical_closure_attempts", [])
    if not isinstance(attempts, list):
        attempts = []
    if old_closure is not None:
        attempts.append(old_closure)
    case_manifest["known_correction_physical_closure_attempts"] = attempts
    case_manifest["known_correction_physical_closure"] = None
    case_manifest["correction_input_preparation_history"] = preparation_history
    for condition, role_map in repaired.items():
        conditions[condition].setdefault(
            "previous_input_preparation_phase_ramp", previous[condition]
        )
        conditions[condition]["input_preparation"] = role_map
    repair_record = {
        "status": "prepared",
        "generation": generation_name,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "directory": str(repair_root),
        "correction_model": "inverse_stage2_zero_extended_linear_interpolator",
        "reason": "the simulator injects x[n]=(1-d)x0[n]+d*x0[n-1] for d=delay*fs; a pure phase ramp cannot undo its amplitude response",
        "delay_truth_ns_used_only_for_A2": delay_truth_ns,
        "delay_estimate_ns_used_for_A3": delay_estimate_ns,
        "truth_used_in_estimator": False,
        "source_identity": _source_identity(),
        "previous_preparation": {
            condition: {
                role: [str(path) for path in item.get("period_paths", [])]
                for role, item in role_map.items()
                if isinstance(item, dict)
            }
            for condition, role_map in previous.items()
            if isinstance(role_map, dict)
        },
        "new_preparation": {
            condition: {
                role: [str(path) for path in item.get("period_paths", [])]
                for role, item in role_map.items()
                if isinstance(item, dict)
            }
            for condition, role_map in repaired.items()
        },
    }
    case_manifest["correction_model_repair_attempts"] = repair_attempts
    case_manifest["correction_model_repair"] = repair_record
    case_manifest["status"] = "correction_model_updated"
    stage1._write_json(case_manifest_path, case_manifest)
    outer_manifest_path = case_root.parent.parent / "manifest.json"
    if outer_manifest_path.is_file():
        outer_manifest = stage1._read_json(outer_manifest_path)
        outer_manifest["status"] = case_manifest["status"]
        outer_manifest["case_result"] = case_manifest
        outer_manifest["source_identity_after"] = _source_identity()
        outer_manifest["disk_status_after"] = stage1._disk_status(outer_manifest_path.parent)
        stage1._write_json(outer_manifest_path, outer_manifest)
    return {
        "status": case_manifest["status"],
        "case_root": str(case_root),
        "repair": repair_record,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    run_mode = parser.add_mutually_exclusive_group(required=True)
    run_mode.add_argument("--output-root", type=Path)
    run_mode.add_argument("--abc-extension-output-root", type=Path)
    run_mode.add_argument("--resume-case-root", type=Path)
    run_mode.add_argument("--continue-case-root", type=Path)
    run_mode.add_argument("--repair-correction-model-case-root", type=Path)
    parser.add_argument("--seed", type=int, default=101)
    args = parser.parse_args(argv)
    try:
        if args.repair_correction_model_case_root is not None:
            result = repair_correction_inputs(args.repair_correction_model_case_root)
        elif args.abc_extension_output_root is not None:
            manifest = run_abc_extension(args.abc_extension_output_root, args.seed)
            result = {
                "status": manifest.get("status"),
                "output_root": str(Path(args.abc_extension_output_root).expanduser().resolve()),
                "case_status": manifest.get("case_status"),
                "status_labels": manifest.get("status_labels"),
            }
        elif args.continue_case_root is not None:
            result = continue_production_comparisons(args.continue_case_root)
        elif args.resume_case_root is not None:
            result = resume_a0_production_smoke(args.resume_case_root)
        else:
            manifest = run_pilot(args.output_root, args.seed)
            result = {
                "status": manifest.get("status"),
                "output_root": str(Path(args.output_root).expanduser().resolve()),
                "case_status": manifest.get("case_status"),
                "status_labels": manifest.get("status_labels"),
            }
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") in {
        "completed", "completed_with_gaps", "production_smoke_passed", "correction_model_updated",
        "production_comparisons_executed"
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
