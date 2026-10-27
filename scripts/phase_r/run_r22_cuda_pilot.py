#!/usr/bin/env python3
"""Run one R22 multi-target scene through audited Native CUDA methods."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_delay_stage1_formal as stage1  # noqa: E402
from scripts import run_production_hierarchical_calibration_pilot as calibration  # noqa: E402
from scripts import run_track_manager_e2e as e2e  # noqa: E402
from scripts.phase_r import r20_harmonized_task_review as r20  # noqa: E402
from scripts.phase_r import r21_historical_task_review as r21_eval  # noqa: E402
from scripts.phase_r import run_r12_physics_matched_delay as r12  # noqa: E402
from scripts.phase_r import run_r21_cuda_pilot as r21  # noqa: E402
from scripts.phase_r.r22_scenario_factory import build_seed_scenario  # noqa: E402

DEFAULT_STAGE2 = ROOT / "build/simulate_stage2_statistical"
DEFAULT_PRODUCTION = ROOT / "build/GMTI_pipe_core"
DEFAULT_TEMPLATE = ROOT / "simulator/scenarios/beam50_one_targets_continuous.run.json"
DEFAULT_METHOD_CONFIG = ROOT / "docs/research/phase_r/R22/config/r22_method_config.json"
DEFAULT_AREA = {
    "range_window_m": [84500.0, 85500.0],
    "mean_power": 0.02,
    "texture_sigma": 0.25,
    "temporal_correlation_rho": 0.98,
    "thermal_noise_power": 0.001,
}
R22_XML_OVERRIDES = {
    "csi_metrics_enable": "true",
    "csi_metrics_dump_power_maps": "false",
    "csi_metrics_dump_intermediate_maps": "false",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_identity(path: Path) -> dict[str, Any]:
    candidate = Path(path).expanduser().resolve()
    return {
        "path": str(candidate),
        "exists": candidate.is_file(),
        "bytes": candidate.stat().st_size if candidate.is_file() else None,
        "sha256": sha256_file(candidate) if candidate.is_file() else None,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    item = getattr(value, "item", None)
    if callable(item):
        return _json_safe(item())
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(value), indent=2, ensure_ascii=False, sort_keys=True,
                   allow_nan=False, default=str) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(str(key))
    with path.open("w", encoding="utf-8", newline="") as stream:
        if not fields:
            stream.write("\n")
            return
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, allow_nan=False)
                if isinstance(value, (dict, list, tuple, set)) else _json_safe(value)
                for key, value in row.items()
            })


def case_identifier(
    phase: str, seed: int, speed_mps: float, snr_db: float, delay_ns: float,
    target_count: int, period_count: int,
) -> str:
    def token(value: float) -> str:
        return f"{value:g}".replace("-", "m").replace(".", "d")
    return (
        f"{phase}__seed_{seed}__vr_{token(speed_mps)}__snr_{token(snr_db)}"
        f"__delay_{token(delay_ns)}ns__targets_{target_count}__periods_{period_count}"
    )


def validate_case_scale(*, phase: str, target_count: int, period_count: int) -> bool:
    """Keep target-free H0 scenes distinct from target-bearing experiment phases."""
    phases = {"engineering", "screening", "confirmatory", "dense", "bridge", "h0"}
    if phase not in phases:
        raise ValueError(f"unsupported R22 phase: {phase}")
    if isinstance(target_count, bool) or not isinstance(target_count, int):
        raise ValueError("target_count must be an integer")
    if isinstance(period_count, bool) or not isinstance(period_count, int) or period_count <= 0:
        raise ValueError("period_count must be a positive integer")
    if phase == "h0":
        if target_count != 0:
            raise ValueError("H0 must be a target-free scene with target_count=0")
    elif target_count <= 0:
        raise ValueError("target-bearing R22 phases require target_count > 0")
    return True


def _source_identity() -> dict[str, Any]:
    sources = (
        Path(__file__), Path(__file__).with_name("r22_scenario_factory.py"),
        ROOT / "scripts/run_delay_stage1_formal.py",
        ROOT / "scripts/run_production_hierarchical_calibration_pilot.py",
        ROOT / "scripts/run_track_manager_e2e.py",
        ROOT / "scripts/phase_r/r21_task_evaluator.py",
        ROOT / "scripts/phase_r/r21_historical_task_review.py",
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True,
    )
    tracked = subprocess.run(
        ["git", "status", "--short", "--untracked-files=no"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return {
        "commit": result.stdout.strip(),
        "branch": subprocess.run(
            ["git", "branch", "--show-current"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip(),
        "tracked_dirty_status": tracked.stdout.splitlines(),
        "files": {str(path.relative_to(ROOT)): file_identity(path) for path in sources if path.is_file()},
    }


def _load_method_configuration(
    config_path: Path,
    scenario_path: Path,
    method_ids: Sequence[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config_path = Path(config_path).expanduser().resolve()
    method_config = calibration.load_pilot_config(config_path)
    method_config["scenario_template"] = str(Path(scenario_path).expanduser().resolve())
    method_contract = calibration.build_method_contract(method_config, method_ids=method_ids)
    return method_config, method_contract


def _make_scenario(
    template: Path,
    output_root: Path,
    *, seed: int, target_count: int, speed_mps: float, snr_db: float,
    period_count: int, first_beam: int, beam_count: int,
    area: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    base = stage1._load_stage1_template({"scenario_template": str(template.resolve())})
    scene = base["scene"]
    window = tuple(float(value) for value in area["range_window_m"])
    scene["range_min_m"], scene["range_max_m"] = window
    clutter = scene["area_clutter"]
    clutter.update({
        "enabled": True,
        "model": "continuous_texture",
        "mean_power": float(area["mean_power"]),
        "texture_sigma": float(area["texture_sigma"]),
        "temporal_correlation_rho": float(area["temporal_correlation_rho"]),
    })
    scene["thermal_noise"].update({"enabled": True, "noise_power": float(area["thermal_noise_power"])})
    scenario, generator_manifest = build_seed_scenario(
        base,
        seed=seed,
        target_count=target_count,
        radial_speed_mps=speed_mps,
        target_snr_db=snr_db,
        period_count=period_count,
        first_beam=first_beam,
        beam_count=beam_count,
        clutter_range_window_m=window,
    )
    case_id = case_identifier("scene", seed, speed_mps, snr_db, 0.0, target_count, period_count)
    scenario["case_id"] = f"R22_{case_id}"
    scenario["output_dir"] = str((output_root / "unused_stage2_template_output").resolve())
    path = output_root / "scenario_template.json"
    write_json(path, scenario)
    write_json(output_root / "target_generation_manifest.json", generator_manifest)
    return path, generator_manifest


def _raw_role_periods(pilot_manifest: Mapping[str, Any], period_count: int) -> dict[str, dict[str, list[Path]]]:
    simulation = pilot_manifest.get("simulation", {})
    result: dict[str, dict[str, list[Path]]] = {}
    for condition, short in (("A0_Ideal", "A0"), ("A1_Current_unknown_error", "A1")):
        roles: dict[str, list[Path]] = {}
        for role in ("ON", "OFF", "TO"):
            item = simulation.get(f"{short}_{role}", {})
            output = Path(str(item.get("output_dir", "")))
            roles[role] = stage1._period_paths_from_output(output, period_count)
        result[condition] = roles
    return result


def _audit_production_branches(
    branch_records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    audited: list[dict[str, Any]] = []
    for branch in branch_records:
        record = dict(branch)
        if str(record.get("role", "")) != "ALL":
            record["production_path_gate"] = stage1.audit_production_path_evidence(record)
        audited.append(record)
    return audited


def _all_production_branches_passed(branch_records: Sequence[Mapping[str, Any]]) -> bool:
    production_branches = [record for record in branch_records if str(record.get("role", "")) != "ALL"]
    return bool(production_branches) and all(
        record.get("status") == "passed"
        and record.get("production_returncode") == 0
        and isinstance(record.get("production_path_gate"), Mapping)
        and record["production_path_gate"].get("status") == "passed"
        for record in production_branches
    )


def _delay_corrected_inputs(
    case_root: Path, condition: str, role: str, source_paths: Sequence[Path],
    delay_ns: float, layout: Mapping[str, Any],
) -> tuple[list[Path], list[dict[str, Any]]]:
    output_dir = case_root / "inputs" / "r22_corrections" / condition / role
    output_dir.mkdir(parents=True, exist_ok=True)
    corrected: list[Path] = []
    audits: list[dict[str, Any]] = []
    for index, source in enumerate(source_paths):
        destination = output_dir / f"period_{index:04d}.bin"
        audit = stage1.rewrite_float32_protocol_delay(
            source,
            destination,
            pulse_len=int(layout["pulse_len"]),
            channel_count=int(layout["channel_count"]),
            fs_hz=float(layout["fs_hz"]),
            delta_tau_sec=float(delay_ns) * 1.0e-9,
            channel_indices=tuple(int(value) for value in layout["correction_channel_indices"]),
            simulator_linear_fractional_delay_inverse=True,
        )
        corrected.append(destination)
        audits.append(audit)
    return corrected, audits


def _run_extra_delay_method(
    case_root: Path,
    case_id: str,
    condition: str,
    estimate_ns: float,
    correction_source: str,
    estimate_record: Mapping[str, Any] | None,
    raw_b_roles: Mapping[str, Sequence[Path]],
    source_xml: Path,
    truth_root: Path,
    layout: Mapping[str, Any],
    period_count: int,
) -> list[dict[str, Any]]:
    branch_records: list[dict[str, Any]] = []
    for role in ("ON", "OFF", "TO"):
        corrected, audits = _delay_corrected_inputs(
            case_root, condition, role, raw_b_roles[role], estimate_ns, layout,
        )
        input_dir = case_root / "inputs" / "production" / condition / role
        input_dir.mkdir(parents=True, exist_ok=True)
        for index, source in enumerate(corrected):
            link = input_dir / f"period_{index:04d}.bin"
            if link.exists() or link.is_symlink():
                raise FileExistsError(f"refusing to overwrite R22 correction input link: {link}")
            link.symlink_to(source.resolve())
        branch_name = f"{condition}_{role}"
        branch = stage1.run_production_branch(
            case_root,
            branch_name,
            input_dir,
            source_xml,
            {},
            period_count,
            "local",
            layout,
            truth_path=truth_root,
            xml_overrides={
                **R22_XML_OVERRIDES,
                "csi_metrics_beam_id": int(layout["beam_id"]),
                "new_protocol_file_first_beam": int(layout["beam_id"]),
                "new_protocol_file_scan_beam_count": int(layout.get("scan_beam_count", 1)),
                "shm_scan_beam_count": int(layout.get("scan_beam_count", 1)),
                "wavepos_st": int(layout["beam_id"]),
                "wavepos_ed": int(layout.get("last_beam_id", layout["beam_id"])),
                "wavepos_skip": 1,
            },
        )
        gate = stage1.audit_production_path_evidence(branch)
        branch.update({
            "condition": condition,
            "role": role,
            "case_id": case_id,
            "correction_source": correction_source,
            "applied_delay_ns": float(estimate_ns),
            "estimate_record": dict(estimate_record or {}),
            "truth_used_in_estimator": False,
            "truth_used_to_apply_correction": False,
            "correction_operator": "estimate_channel_delay.rewrite_float32_protocol_delay",
            "correction_audits": audits,
            "input_role": "same_seed_A1_Current_unknown_error_raw_role",
            "production_path_gate": gate,
        })
        stage1._write_json(case_root / "production" / branch_name / "branch_manifest.json", branch)
        branch_records.append(branch)
        if gate.get("status") != "passed":
            break
    return branch_records


def _evaluate_branches(
    *, phase: str, case_id: str, seed: int, case_root: Path, pipeline_manifest: Mapping[str, Any],
    binary_identity: Mapping[str, Any], branch_records: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    eval_case = {
        "dataset": "R22",
        "dataset_key": case_id,
        "case_id": case_id,
        "case_root": str(case_root),
        "case_manifest": pipeline_manifest,
        "snapshot_manifest": {"build": {"GMTI_pipe_core": dict(binary_identity)}},
    }
    tables: dict[str, list[dict[str, Any]]] = {
        "task_metrics": [], "target_level_metrics": [], "track_level_metrics": [],
        "parameter_metrics": [], "h0_false_alarm_metrics": [],
        "doppler_opportunity_audit": [], "target_associations": [],
    }
    for record in branch_records:
        branch_name = str(record.get("branch_name", ""))
        method_id = str(record.get("method_id", record.get("condition", "UNKNOWN")))
        mode = str(record.get("mode", "not_applicable"))
        role = str(record.get("role", "UNKNOWN"))
        condition = f"{method_id}_{mode}"
        row_base = {"case_id": case_id, "seed": seed, "condition": condition,
                    "method_id": method_id, "mode": mode, "role": role}
        if not branch_name or record.get("status") != "passed":
            tables["task_metrics"].append({**row_base, "status": record.get("status", "NOT_AVAILABLE"),
                                           "reason": record.get("reason", "production_branch_not_passed")})
            if phase == "h0" and role == "OFF":
                tables["h0_false_alarm_metrics"].append({
                    **row_base,
                    "cut_band_mode": None,
                    "observed_cut_hit_count": None,
                    "valid_cut_count": None,
                    "observed_cut_hit_fraction": None,
                    "status": "NOT_EVALUABLE_PRODUCTION_BRANCH",
                    "empirical_pfa_status": "NOT_EVALUABLE",
                    "h0_scope": "independent_target_free_seed",
                    "reason": record.get("reason", "production_branch_not_passed"),
                })
            continue
        branch_dir = case_root / "production" / branch_name
        bundle = r20._condition_role_bundle(eval_case, condition, role, branch_dir)
        bundle["case"] = eval_case
        bundle["canonical_condition"] = condition
        range_audit = r21_eval._range_axis_audit(bundle)
        doppler_rows = (
            [r21_eval._doppler_audit_row_v3(bundle, truth) for truth in bundle["truths"] if r20._visible_truth(truth)]
            if role in {"ON", "TO"} else []
        )
        if phase == "h0":
            evaluated = {
                "status": "NOT_APPLICABLE_H0_ROLE", "target_result": None,
                "track_metrics": None, "parameter_records": [],
            }
            tables["h0_false_alarm_metrics"].extend(
                {**item, **row_base}
                for item in _h0_false_alarm_rows(bundle, phase=phase, role=role)
            )
        elif role == "OFF":
            evaluated: dict[str, Any] = {
                "status": "NOT_APPLICABLE_H0_ROLE", "target_result": None,
                "track_metrics": None, "parameter_records": [],
            }
            tables["h0_false_alarm_metrics"].extend(
                {**item, **row_base}
                for item in _h0_false_alarm_rows(bundle, phase=phase, role=role)
            )
        else:
            evaluated = r21_eval._evaluate_bundle_v3(bundle, range_audit, doppler_rows)
            target_result = evaluated.get("target_result") or {}
            tables["target_level_metrics"].extend({**row_base, **item} for item in target_result.get("per_opportunity_status", []))
            tables["target_associations"].extend({**row_base, **item} for item in target_result.get("associations", []))
            tables["doppler_opportunity_audit"].extend({**row_base, **item} for item in doppler_rows)
            tables["parameter_metrics"].extend({**row_base, **item} for item in evaluated.get("parameter_records", []))
        target = evaluated.get("target_result") or {}
        metrics = target.get("metrics", {})
        track = evaluated.get("track_metrics") or {}
        tables["task_metrics"].append({
            **row_base,
            "status": evaluated.get("status"),
            "range_axis_status": range_audit.get("status"),
            "visible_truth_opportunities": metrics.get("visible_truth_opportunities_total"),
            "evaluable_truth_opportunities": metrics.get("truth_opportunities"),
            "hits": metrics.get("unique_hits"),
            "misses": metrics.get("misses"),
            "hit_fraction": metrics.get("truth_opportunity_hit_fraction"),
            "invalid_truth_opportunities": metrics.get("invalid_truth_opportunities"),
            "confirmed_observations": track.get("confirmed_observation_count"),
            "confirmed_unique_periods": track.get("confirmed_unique_period_count"),
            "post_confirmation_match_rate": track.get("post_confirmation_match_rate"),
            "track_fragmentation_count": track.get("track_fragmentation_count_observed"),
            "track_status": track.get("status"),
        })
        if role == "ON":
            tables["track_level_metrics"].append({**row_base, **track})
    return tables


def _h0_false_alarm_rows(
    bundle: Mapping[str, Any], *, phase: str, role: str,
) -> list[dict[str, Any]]:
    """Preserve paired OFF diagnostics while identifying independent H0 seeds."""
    if role != "OFF":
        return []
    rows = r20._off_h0_rows(bundle)
    if phase != "h0":
        return [
            {**item, "h0_scope": "paired_OFF_role_not_an_independent_H0_seed"}
            for item in rows
        ]

    output: list[dict[str, Any]] = []
    for item in rows:
        row = {**item, "h0_scope": "independent_target_free_seed"}
        if (row.get("valid_cut_count") is not None
                and int(row["valid_cut_count"]) > 0
                and row.get("observed_cut_hit_count") is not None):
            row.update({
                "status": "EVALUABLE_DESCRIPTIVE_H0_SEED",
                "empirical_pfa_status": "SEED_CLUSTER_AGGREGATION_REQUIRED",
                "interpretation": (
                    "per-seed target-free CUT fraction; inferential Pfa requires aggregation "
                    "and uncertainty estimation across independent H0 seeds"
                ),
            })
        output.append(row)
    return output


def _write_tables(root: Path, tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    for name, rows in tables.items():
        write_csv(root / "evaluation" / f"{name}.csv", rows)


def _compact_case_artifacts(case_root: Path) -> dict[str, Any]:
    """Remove regenerable binaries and full CSI maps after scalar evidence exists."""
    case_root = Path(case_root).expanduser().resolve()
    ledger_path = case_root / "raw_cleanup.json"
    previous = (
        json.loads(ledger_path.read_text(encoding="utf-8"))
        if ledger_path.is_file() else {}
    )
    cleanup = stage1.cleanup_stage1_raw(case_root, allow_incomplete=True)
    numpy_records: list[dict[str, Any]] = []
    numpy_removed_bytes = 0
    production_root = case_root / "production"
    if production_root.is_dir():
        for path in sorted(production_root.rglob("*.npy")):
            if "csi_metrics" not in path.parts or not path.is_file() or path.is_symlink():
                continue
            size = int(path.stat().st_size)
            relative = str(path.relative_to(case_root))
            path.unlink()
            numpy_removed_bytes += size
            numpy_records.append({
                "path": relative,
                "kind": "reproducible_csi_diagnostic_map",
                "bytes": size,
                "status": "removed",
                "reason": "scalar CSI tap summary retained; map is reproducible from the scenario and runtime config",
            })
    prior_records = list(previous.get("records", [])) if isinstance(previous, Mapping) else []
    fresh_records = list(cleanup.get("records", []))
    prior_count = int(previous.get("removed_file_count", 0)) if isinstance(previous, Mapping) else 0
    prior_bytes = int(previous.get("removed_bytes", 0)) if isinstance(previous, Mapping) else 0
    prior_npy_count = int(previous.get("removed_numpy_diagnostic_file_count", 0)) if isinstance(previous, Mapping) else 0
    prior_npy_bytes = int(previous.get("removed_numpy_diagnostic_bytes", 0)) if isinstance(previous, Mapping) else 0
    cleanup.update({
        "policy": "remove_generated_bin_and_reproducible_csi_npy_keep_compact_audit_files",
        "removed_file_count": prior_count + int(cleanup.get("removed_file_count", 0)) + len(numpy_records),
        "removed_bytes": prior_bytes + int(cleanup.get("removed_bytes", 0)) + numpy_removed_bytes,
        "removed_numpy_diagnostic_file_count": prior_npy_count + len(numpy_records),
        "removed_numpy_diagnostic_bytes": prior_npy_bytes + numpy_removed_bytes,
        "records": prior_records + fresh_records + numpy_records,
    })
    write_json(ledger_path, cleanup)
    case_manifest_path = case_root / "case_manifest.json"
    case_manifest = json.loads(case_manifest_path.read_text(encoding="utf-8"))
    case_manifest["raw_cleanup"] = cleanup
    write_json(case_manifest_path, case_manifest)
    return cleanup


def run_case(
    output_root: Path,
    *, phase: str, seed: int, speed_mps: float, snr_db: float, delay_ns: float,
    target_count: int, period_count: int, first_beam: int, beam_count: int,
    stage2_binary: Path, production_binary: Path, template: Path = DEFAULT_TEMPLATE,
    method_config_path: Path = DEFAULT_METHOD_CONFIG,
    method_ids: Sequence[str] = calibration.METHOD_IDS,
    calibration_modes: Sequence[str] = ("Mode-A", "Mode-B"),
    area: Mapping[str, Any] = DEFAULT_AREA,
    keep_raw: bool = False,
) -> dict[str, Any]:
    output_root = Path(output_root).expanduser().resolve()
    if output_root.exists() and (not output_root.is_dir() or any(output_root.iterdir())):
        raise ValueError(f"refusing to overwrite non-empty R22 output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    validate_case_scale(phase=phase, target_count=target_count, period_count=period_count)
    case_id = case_identifier(phase, seed, speed_mps, snr_db, delay_ns, target_count, period_count)
    source_snapshot = _source_identity()
    stage2_binary = Path(stage2_binary).expanduser().resolve()
    production_binary = Path(production_binary).expanduser().resolve()
    preflight = r21._native_cuda_preflight(production_binary, output_root)
    if not stage2_binary.is_file():
        raise FileNotFoundError(f"Stage2 simulator missing: {stage2_binary}")
    stage2_identity = file_identity(stage2_binary)
    scenario_path, generator_manifest = _make_scenario(
        Path(template), output_root, seed=seed, target_count=target_count,
        speed_mps=speed_mps, snr_db=snr_db, period_count=period_count,
        first_beam=first_beam, beam_count=beam_count, area=area,
    )
    method_config_path = Path(method_config_path).expanduser().resolve()
    method_config, method_contract = _load_method_configuration(
        method_config_path, scenario_path, method_ids
    )
    scene_identity = {
        "seed": seed,
        "period_count": period_count,
        "delay_error_ns": float(delay_ns),
        "case_id": case_id,
        "target_count": target_count,
        "radial_speed_mps": float(speed_mps),
        "target_snr_db": float(snr_db),
    }
    pipeline_case = {
        "working_point": "r22_multi_target",
        "seed": seed,
        "target_count": target_count,
        "allow_target_free_h0": phase == "h0",
        "target_velocity_mps": float(speed_mps),
        "target_snr_db": float(snr_db),
        "delay_error_ns": float(delay_ns),
        "period_count": period_count,
        "scene_identity_override": scene_identity,
    }
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "PGRCC R22 multi-target Native CUDA Production case",
        "case_id": case_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "design": {
            "seed": seed, "radial_speed_mps": speed_mps, "target_snr_db": snr_db,
            "delay_error_ns": delay_ns, "target_count": target_count,
            "period_count": period_count, "first_beam": first_beam, "beam_count": beam_count,
            "method_ids": list(method_ids), "calibration_modes": list(calibration_modes),
            "target_truth_nested_across_density": True,
            "known_error_use": "physical correction upper bound only; blind estimator does not receive it",
            "ai_training": False, "router_enabled": False,
            "raw_artifact_policy": (
                "keep_generated_binary_artifacts" if keep_raw else
                "remove_generated_bin_after_evaluation_keep_audit_files"
            ),
        },
        "status_labels": {"IMPLEMENTED": True, "EXECUTED": False, "EVALUABLE": False,
                          "PERFORMANCE_VERIFIED": False},
        "source_identity": source_snapshot,
        "stage2_binary": stage2_identity,
        "production_binary": preflight["binary"],
        "native_cuda_preflight": preflight,
        "generator_manifest": generator_manifest,
        "scenario_template": file_identity(scenario_path),
        "method_config": file_identity(method_config_path),
        "output_root": str(output_root),
        "implementation_result": None,
    }
    write_json(output_root / "r22_run_manifest.json", manifest)

    modules = (stage1, e2e, calibration)
    old_values = [(module, getattr(module, "SIMULATOR", None), getattr(module, "PIPE", None)) for module in modules]
    stage1.SIMULATOR = stage2_binary
    stage1.PIPE = production_binary
    e2e.PIPE = production_binary
    calibration.SIMULATOR = stage2_binary
    calibration.PIPE = production_binary
    try:
        pipeline_manifest = calibration._run_case(
            method_config,
            pipeline_case,
            output_root / "pipeline",
            method_contract,
            input_mode="local",
            skip_cuda=False,
            calibration_modes=calibration_modes,
        )
        pipeline_case_root = Path(str(pipeline_manifest["output_root"])).resolve()
        write_json(pipeline_case_root / "case_manifest.json", pipeline_manifest)
        raw = _raw_role_periods(pipeline_manifest, period_count)
        c0_on = next((row for row in pipeline_manifest.get("method_branches", [])
                      if row.get("method_id") == "C0" and row.get("role") == "ON"
                      and row.get("mode") == "not_applicable" and row.get("status") == "passed"), None)
        estimate_record: dict[str, Any]
        qualified: dict[str, Any]
        if c0_on is None or not c0_on.get("production_xml"):
            estimate_record = {"status": "NOT_AVAILABLE", "reason": "C0_B_ON_Production_XML_missing",
                               "truth_used_in_estimator": False}
            qualified = {
                "d3_huber": {"status": "NOT_AVAILABLE", "reason": estimate_record["reason"]},
                "m1": {"status": "NOT_AVAILABLE", "reason": estimate_record["reason"]},
            }
        else:
            try:
                estimate_record = r12._blind_m1_from_b_on(raw["A1_Current_unknown_error"]["ON"],
                                                            Path(str(c0_on["production_xml"])))
                qualified = r21.qualify_blind_methods(estimate_record)
            except (OSError, RuntimeError, ValueError, KeyError) as exc:
                estimate_record = {"status": "NOT_AVAILABLE", "reason": str(exc),
                                   "input_role": "same_seed_A1_ON_raw_C1_C2", "truth_used_in_estimator": False}
                qualified = {
                    "d3_huber": {"status": "NOT_AVAILABLE", "reason": str(exc)},
                    "m1": {"status": "NOT_AVAILABLE", "reason": str(exc)},
                }
        method_records = list(pipeline_manifest.get("method_branches", []))
        extra_methods: list[dict[str, Any]] = []
        correction_specs: list[tuple[str, float, str, Mapping[str, Any] | None]] = [
            ("R22_R9_Fixed_D3", r21.FIXED_D3_NS, "R9_frozen_calibration", None),
        ]
        if qualified.get("d3_huber", {}).get("status") == "AVAILABLE":
            correction_specs.append((
                "R22_D3_Huber_B_ON", float(qualified["d3_huber"]["correction_ns"]),
                "same_seed_B_ON_raw_C1_C2_Huber_fit", qualified["d3_huber"],
            ))
        else:
            extra_methods.append({"method_id": "R22_D3_Huber_B_ON", "mode": "not_applicable",
                                  "role": "ALL", "status": "NOT_AVAILABLE",
                                  "reason": qualified.get("d3_huber", {}).get("reason", "support_gate_failed")})
        if qualified.get("m1", {}).get("status") == "AVAILABLE":
            correction_specs.append((
                "R22_M1_Physics_Matched", float(qualified["m1"]["correction_ns"]),
                "same_seed_B_ON_raw_C1_C2_M1_fit", qualified["m1"],
            ))
        else:
            extra_methods.append({"method_id": "R22_M1_Physics_Matched", "mode": "not_applicable",
                                  "role": "ALL", "status": "NOT_AVAILABLE",
                                  "reason": qualified.get("m1", {}).get("reason", estimate_record.get("reason", "support_gate_failed"))})
        if not any(row.get("method_id") == "PK" for row in method_records):
            extra_methods.append({"method_id": "Known_error_correction_upper_bound", "mode": "not_applicable",
                                  "role": "ALL", "status": "NOT_AVAILABLE", "reason": "PK_method_not_selected"})

        layout = pipeline_manifest.get("protocol_layout")
        source_xml = Path(str(pipeline_manifest.get("source_xml", "")))
        truth_root = Path(str(pipeline_manifest["simulation"]["A0_ON"]["output_dir"])) / "truth"
        if not isinstance(layout, Mapping):
            raise ValueError("Production protocol layout missing from pipeline manifest")
        for condition, correction_ns, source, estimate in correction_specs:
            extra_methods.extend(_run_extra_delay_method(
                pipeline_case_root, case_id, condition, correction_ns, source, estimate,
                raw["A1_Current_unknown_error"], source_xml, truth_root, layout, period_count,
            ))
        method_records.extend(extra_methods)
        method_records = _audit_production_branches(method_records)
        pipeline_manifest["method_branches"] = method_records
        pipeline_manifest["r22_delay_estimator"] = estimate_record
        pipeline_manifest["r22_blind_method_qualification"] = qualified
        write_json(pipeline_case_root / "case_manifest.json", pipeline_manifest)
        tables = _evaluate_branches(
            phase=phase,
            case_id=case_id,
            seed=seed,
            case_root=pipeline_case_root,
            pipeline_manifest=pipeline_manifest,
            binary_identity=preflight["binary"],
            branch_records=method_records,
        )
        _write_tables(output_root, tables)
        stage2_runs = pipeline_manifest.get("simulation", {})
        production_elapsed = [float(row.get("production_elapsed_sec", 0.0))
                              for row in method_records if row.get("production_elapsed_sec") is not None]
        on_task_rows = [row for row in tables["task_metrics"] if row.get("role") == "ON"]
        evaluated_on = [row for row in on_task_rows if row.get("status") == "EVALUABLE"]
        evaluable_h0_rows = [
            row for row in tables["h0_false_alarm_metrics"]
            if row.get("h0_scope") == "independent_target_free_seed"
            and row.get("status") == "EVALUABLE_DESCRIPTIVE_H0_SEED"
        ]
        invalid_truth = sum(int(row.get("invalid_truth_opportunities") or 0) for row in evaluated_on)
        all_branches_passed = _all_production_branches_passed(method_records)
        manifest.update({
            "status_labels": {
                "IMPLEMENTED": True,
                "EXECUTED": any(row.get("production_returncode") == 0 for row in method_records),
                "EVALUABLE": (
                    bool(evaluable_h0_rows) if phase == "h0" else
                    bool(evaluated_on) and len(evaluated_on) == len(on_task_rows) and invalid_truth == 0
                ),
                "PERFORMANCE_VERIFIED": False,
                "performance_note": "single case is feasibility evidence; no inferential claim",
            },
            "stage2_run_count": len(stage2_runs),
            "stage2_elapsed_sec": sum(float(row.get("elapsed_sec", 0.0)) for row in stage2_runs.values()),
            "production_branch_count": len([row for row in method_records if row.get("role") != "ALL"]),
            "production_elapsed_sec_sum": sum(production_elapsed),
            "area_clutter_gate": pipeline_manifest.get("area_clutter_gate"),
            "evidence_gate": pipeline_manifest.get("evidence_gate"),
            "production_methods_status": pipeline_manifest.get("status"),
            "all_production_branches_passed": all_branches_passed,
            "legacy_single_target_task_summary": pipeline_manifest.get("legacy_single_target_task_summary"),
            "evaluation_summary": {
                "evaluator": "r21-task-eval-v3",
                "task_rows": len(tables["task_metrics"]),
                "evaluated_on_rows": len(evaluated_on),
                "target_opportunity_rows": len(tables["target_level_metrics"]),
                "independent_h0_seed_rows": len(evaluable_h0_rows),
                "invalid_truth_opportunities": invalid_truth,
                "target_period_opportunities_are_not_independent_seeds": True,
            },
            "r22_delay_estimator": estimate_record,
            "r22_blind_method_qualification": qualified,
            "pipeline_case_manifest": file_identity(pipeline_case_root / "case_manifest.json"),
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        manifest["implementation_result"] = "passed" if (
            pipeline_manifest.get("status") == "completed" and all_branches_passed
        ) else "completed_with_gaps"
        if not keep_raw:
            pipeline_manifest["r22_status_labels"] = dict(manifest["status_labels"])
            pipeline_manifest["r22_evaluation_summary"] = dict(manifest["evaluation_summary"])
            write_json(pipeline_case_root / "case_manifest.json", pipeline_manifest)
            cleanup = _compact_case_artifacts(pipeline_case_root)
            manifest["raw_cleanup"] = {
                "status": "passed",
                "removed_file_count": cleanup["removed_file_count"],
                "removed_bytes": cleanup["removed_bytes"],
                "remaining_bin_count": cleanup["remaining_bin_count"],
                "ledger": str(pipeline_case_root / "raw_cleanup.json"),
            }
            manifest["pipeline_case_manifest"] = file_identity(
                pipeline_case_root / "case_manifest.json"
            )
    except Exception as exc:
        manifest.update({
            "implementation_result": "failed_or_incomplete",
            "failure": {"type": type(exc).__name__, "message": str(exc)},
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        write_json(output_root / "r22_run_manifest.json", manifest)
        raise
    finally:
        for module, old_simulator, old_pipe in old_values:
            if old_simulator is not None:
                module.SIMULATOR = old_simulator
            if old_pipe is not None:
                module.PIPE = old_pipe
        e2e.PIPE = old_values[1][2]

    write_json(output_root / "r22_run_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--phase", choices=("engineering", "screening", "confirmatory", "dense", "bridge", "h0"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--speed-mps", type=float, required=True)
    parser.add_argument("--snr-db", type=float, required=True)
    parser.add_argument("--delay-ns", type=float, required=True)
    parser.add_argument("--target-count", type=int, default=20)
    parser.add_argument("--period-count", type=int, default=5)
    parser.add_argument("--first-beam", type=int, default=50)
    parser.add_argument("--beam-count", type=int, default=1)
    parser.add_argument("--stage2-binary", type=Path, default=DEFAULT_STAGE2)
    parser.add_argument("--production-binary", type=Path, default=DEFAULT_PRODUCTION)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--method-config", type=Path, default=DEFAULT_METHOD_CONFIG)
    parser.add_argument("--method-ids", default=",".join(calibration.METHOD_IDS))
    parser.add_argument("--calibration-modes", default="Mode-A,Mode-B")
    parser.add_argument("--keep-raw", action="store_true",
                        help="retain regenerable .bin files after evaluation (default: compact them)")
    args = parser.parse_args()
    method_ids = tuple(item.strip() for item in args.method_ids.split(",") if item.strip())
    modes = tuple(item.strip() for item in args.calibration_modes.split(",") if item.strip())
    result = run_case(
        args.output_root,
        phase=args.phase,
        seed=args.seed,
        speed_mps=args.speed_mps,
        snr_db=args.snr_db,
        delay_ns=args.delay_ns,
        target_count=args.target_count,
        period_count=args.period_count,
        first_beam=args.first_beam,
        beam_count=args.beam_count,
        stage2_binary=args.stage2_binary,
        production_binary=args.production_binary,
        template=args.template,
        method_config_path=args.method_config,
        method_ids=method_ids,
        calibration_modes=modes,
        keep_raw=args.keep_raw,
    )
    print(json.dumps({
        "case_id": result.get("case_id"),
        "implementation_result": result.get("implementation_result"),
        "status_labels": result.get("status_labels"),
        "production_branch_count": result.get("production_branch_count"),
        "evaluation_summary": result.get("evaluation_summary"),
        "output_root": result.get("output_root"),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("implementation_result") == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
