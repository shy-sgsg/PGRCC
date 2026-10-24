#!/usr/bin/env python3
"""Run the frozen R12 seed-404 Native CUDA channel-delay comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shlex
import struct
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
import xml.etree.ElementTree as ET

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import estimate_channel_delay as delay_io  # noqa: E402
from scripts import run_delay_stage1_formal as stage1  # noqa: E402
from scripts import run_track_manager_e2e as e2e  # noqa: E402
from scripts.phase_r import evaluate_r3_minimal_cuda_causal_pilot as r3_eval  # noqa: E402
from scripts.phase_r import evaluate_r7_task_metrics as task_eval  # noqa: E402
from scripts.phase_r import evaluate_r8_multiseed_causal_pilot as r8_eval  # noqa: E402
from scripts.phase_r import run_r3_minimal_cuda_causal_pilot as r3_runner  # noqa: E402
from scripts.phase_r.physics_matched_delay import (  # noqa: E402
    compute_electronic_pair_group_delay_sec,
    estimate_physics_matched_delay,
)

DEFAULT_OUTPUT = ROOT / "outputs/phase_r_r12_physics_matched_delay_20261024"
DEFAULT_BINARY = ROOT / "build_r7/GMTI_pipe_core"
EXPECTED_BINARY_SHA256 = "de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00"
SEED = 404
DELAY_TRUTH_NS = 4.0
R9_FIXED_D3_NS = 3.4099650318669545
BASE_CONDITIONS = (
    "A0_Ideal",
    "A1_Current_unknown_error",
    "A2_Known_error_correction_upper_bound",
)
R12_CONDITIONS = {
    "R12_D3_R9_Fixed": R9_FIXED_D3_NS,
    "R12_M1_Physics_Matched": None,
}
ROLES = ("ON", "OFF", "TO")
XML_OVERRIDES = {
    "csi_metrics_enable": "true",
    "csi_metrics_dump_power_maps": "true",
    "csi_metrics_dump_intermediate_maps": "true",
    "csi_metrics_beam_id": 50,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    return {
        "path": str(resolved),
        "exists": resolved.is_file(),
        "bytes": resolved.stat().st_size if resolved.is_file() else None,
        "sha256": _sha256(resolved) if resolved.is_file() else None,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _json_default(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _ensure_native_binary(binary: Path) -> dict[str, Any]:
    cache = binary.parent / "CMakeCache.txt"
    if not cache.is_file():
        raise FileNotFoundError(f"native build CMakeCache missing: {cache}")
    cache_lines = cache.read_text(encoding="utf-8", errors="replace").splitlines()
    corex_lines = [line for line in cache_lines if line.startswith("USE_COREX:BOOL=")]
    if corex_lines != ["USE_COREX:BOOL=OFF"]:
        raise ValueError(f"expected Native CUDA build with USE_COREX=OFF, got {corex_lines}")
    binary = binary.expanduser().resolve()
    if not binary.is_file():
        raise FileNotFoundError(f"Native CUDA Production binary missing: {binary}")
    digest = _sha256(binary)
    if digest != EXPECTED_BINARY_SHA256:
        raise ValueError(
            "Production binary identity mismatch: "
            f"expected={EXPECTED_BINARY_SHA256} actual={digest}"
        )
    return {
        "path": str(binary),
        "sha256": digest,
        "build_cache": _file_record(cache),
        "use_corex": False,
        "backend": "Native CUDA",
    }


def _git_identity() -> dict[str, Any]:
    def run(*argv: str) -> str:
        result = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, check=False)
        if result.returncode:
            raise RuntimeError(f"git command failed: {shlex.join(argv)}: {result.stderr.strip()}")
        return result.stdout.strip()

    return {
        "commit": run("git", "rev-parse", "HEAD"),
        "branch": run("git", "branch", "--show-current"),
        "tracked_status": run("git", "status", "--short", "--untracked-files=no"),
    }


def _native_preflight(binary: Path, output_root: Path) -> dict[str, Any]:
    identity = _ensure_native_binary(binary)
    gpu = stage1._gpu_status()
    if not gpu.get("available"):
        raise RuntimeError(f"CUDA device unavailable to this process: {gpu}")
    disk = stage1._disk_status(output_root)
    if disk.get("error") or int(disk.get("free_bytes", 0)) <= 0:
        raise RuntimeError(f"insufficient or unavailable output disk: {disk}")
    return {"binary": identity, "gpu": gpu, "disk": disk}


def _raw_period_paths(case: Mapping[str, Any], condition: str, role: str) -> list[Path]:
    try:
        prep = case["conditions"][condition]["input_preparation"][role]
        values = prep.get("raw_paths", prep.get("period_paths", []))
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"raw period paths missing for {condition}/{role}") from exc
    paths = [Path(str(value)).resolve() for value in values] if isinstance(values, list) else []
    if not paths or any(not path.is_file() for path in paths):
        raise FileNotFoundError(f"raw Stage2 periods missing for {condition}/{role}: {paths}")
    return paths


def _xml_float(root: ET.Element, tag: str) -> float:
    raw = root.findtext(f".//{tag}")
    if raw is None:
        raise ValueError(f"runtime XML lacks {tag}")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"runtime XML has non-finite {tag}")
    return value


def _blind_m1_from_b_on(
    raw_paths: Sequence[Path],
    runtime_xml: Path,
) -> dict[str, Any]:
    """Fit ON raw physical C1/C2 and reported geometry; no evaluator inputs."""

    runtime = ET.parse(runtime_xml).getroot()
    pulse_len = int(_xml_float(runtime, "pulse_len"))
    channel_count = int(_xml_float(runtime, "new_protocol_channel_count"))
    fs_hz = _xml_float(runtime, "fs") * 1.0e6
    carrier_hz = _xml_float(runtime, "fc") * 1.0e9
    period_pulses = int(_xml_float(runtime, "pulse_num"))
    range_m = _xml_float(runtime, "Rmin")
    height = _xml_float(runtime, "platform_origin_alt_m")
    heading = _xml_float(runtime, "platform_heading_deg")
    squint_side = int(_xml_float(runtime, "four_channel_fusion_squint_side"))
    if pulse_len != 11820 or channel_count != 4 or fs_hz != 60.0e6 or carrier_hz != 16.0e9:
        raise ValueError("R12 M1 input is outside the frozen R8 protocol/waveform")
    scan_mode = (runtime.findtext(".//scan_mode") or "").strip().lower()
    sign = int(_xml_float(runtime, "four_channel_carrier_phase_sign"))
    if period_pulses != 130:
        raise ValueError("R12 M1 protocol pulse count is outside frozen R8")
    if scan_mode != "electronic" or sign not in (-1, 1) or squint_side != 1 or heading != 0.0:
        raise ValueError("M1 rejects undeclared non-R8 scan/heading geometry")

    positions = {
        channel: tuple(_xml_float(runtime, f"four_channel_ch{channel}_{axis}_m") for axis in "xyz")
        for channel in (1, 2)
    }
    if positions[1] != (-0.085, 0.0, 0.085) or positions[2] != (0.085, 0.0, 0.085):
        raise ValueError(f"M1 rejects undeclared R8 reported geometry: {positions}")
    packet_bytes = 256 + pulse_len * channel_count * 2 * 4
    first_parts: list[np.ndarray] = []
    second_parts: list[np.ndarray] = []
    angles: list[float] = []
    for path in raw_paths:
        size = path.stat().st_size
        if size <= 0 or size % packet_bytes:
            raise ValueError(f"raw protocol file has incomplete packets: {path}")
        count = size // packet_bytes
        if count != period_pulses:
            raise ValueError(f"expected {period_pulses} pulses in {path}, found {count}")
        first, second = delay_io.load_raw_channels(path, pulse_len, channel_count, 1, 2)
        first_parts.append(first)
        second_parts.append(second)
        with path.open("rb") as stream:
            for packet_id in range(count):
                header = stream.read(256)
                if len(header) != 256 or int.from_bytes(header[9:13], "little") != packet_bytes:
                    raise ValueError(f"invalid protocol header in {path}, packet {packet_id}")
                angles.append(struct.unpack_from("<h", header, 218)[0] / 100.0)
                stream.seek(packet_bytes - 256, 1)
    if not angles or any(abs(angle - 38.0) > 0.01 for angle in angles):
        raise ValueError("M1 is frozen to the R8 single 38-degree beam")
    first = np.concatenate(first_parts, axis=0)
    second = np.concatenate(second_parts, axis=0)
    geometry_delay = np.asarray([
        compute_electronic_pair_group_delay_sec(
            theta_deg=angle,
            range_m=range_m,
            height_m=height,
            reference_offset_m=positions[1],
            source_offset_m=positions[2],
            squint_side=squint_side,
        )
        for angle in angles
    ], dtype=np.float64)
    estimate = estimate_physics_matched_delay(
        first,
        second,
        fs_hz=fs_hz,
        carrier_hz=carrier_hz,
        geometry_delay_sec=geometry_delay,
        carrier_phase_sign=sign,
        relative_response_model="constant_complex_gain",
    )
    estimate["input_role"] = "A1_Current_unknown_error_ON_raw_C1_C2_only"
    estimate["input_pulse_count"] = int(first.shape[0])
    estimate["geometry_reference_range_m"] = range_m
    estimate["beam_angle_deg_min"] = float(min(angles))
    estimate["beam_angle_deg_max"] = float(max(angles))
    estimate["reported_geometry_delay_ns"] = float(geometry_delay[0] * 1.0e9)
    estimate["truth_used_in_estimator"] = False
    return estimate


def _branch_passed(record: Mapping[str, Any]) -> bool:
    return (
        record.get("status") == "passed"
        and record.get("production_status") == "passed"
        and record.get("production_returncode") == 0
        and isinstance(record.get("production_path_gate"), Mapping)
        and record["production_path_gate"].get("status") == "passed"
    )


def _condition_branches(case: Mapping[str, Any], condition: str) -> dict[str, Mapping[str, Any]]:
    values = case.get("conditions", {}).get(condition, {}).get("production", {})
    if not isinstance(values, Mapping):
        raise ValueError(f"production records missing for {condition}")
    result = {role: values[role] for role in ROLES if isinstance(values.get(role), Mapping)}
    if set(result) != set(ROLES):
        raise ValueError(f"one or more production roles missing for {condition}")
    return result


def _operator_comparability(case: Mapping[str, Any]) -> dict[str, Any]:
    fingerprints: dict[str, str] = {}
    for condition, roles in case.get("conditions", {}).items():
        production = roles.get("production", {}) if isinstance(roles, Mapping) else {}
        if not isinstance(production, Mapping):
            continue
        for role, branch in production.items():
            if not isinstance(branch, Mapping) or not branch.get("production_xml"):
                continue
            xml_path = Path(str(branch["production_xml"]))
            normalized = r8_eval.normalized_runtime_xml(xml_path.read_text(encoding="utf-8"))
            fingerprints[f"{condition}/{role}"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return {
        "status": "passed" if fingerprints and len(set(fingerprints.values())) == 1 else "failed",
        "fingerprints": fingerprints,
        "normalization": "R8 normalized runtime XML; result_add and track_debug_dir output paths ignored",
    }


def _run_corrected_condition(
    case_root: Path,
    case: dict[str, Any],
    condition: str,
    correction_ns: float,
    production_binary: Path,
) -> dict[str, Any]:
    source_condition = "A1_Current_unknown_error"
    layout = case.get("protocol_layout")
    if not isinstance(layout, Mapping):
        raise ValueError("case protocol_layout missing")
    period_count = int(case["scene_identity"]["period_count"])
    simulation = case.get("simulation", {})
    scene_a0 = Path(str(simulation["A0_ON"]["output_dir"]))
    source_xml = scene_a0 / "config/temp_config_stage2_period_0000.xml"
    truth = e2e._truth_by_period(scene_a0, period_count)
    if len(truth) != period_count:
        raise ValueError(f"A0 truth period mismatch: expected {period_count}, got {len(truth)}")
    prepared_roles: dict[str, Any] = {}
    production_roles: dict[str, Any] = {}
    case.setdefault("conditions", {})[condition] = {
        "input_preparation": prepared_roles,
        "production": production_roles,
        "truth_used_in_estimator": False,
        "correction_applied": True,
        "correction_source": "R9_fixed_D3_calibration" if condition.endswith("Fixed") else "M1_A1_ON_raw_C1_C2_blind_estimate",
        "applied_delay_ns": correction_ns,
    }
    for role in ROLES:
        raw_paths = _raw_period_paths(case, source_condition, role)
        corrected_root = case_root / "inputs" / condition / role / "prepared_inputs"
        corrected_root.mkdir(parents=True, exist_ok=True)
        corrected_paths: list[Path] = []
        audits: list[dict[str, Any]] = []
        for index, raw_path in enumerate(raw_paths):
            destination = corrected_root / f"period_{index:04d}.bin"
            audit = delay_io.rewrite_float32_protocol_delay(
                raw_path,
                destination,
                pulse_len=int(layout["pulse_len"]),
                channel_count=int(layout["channel_count"]),
                fs_hz=float(layout["fs_hz"]),
                delta_tau_sec=correction_ns * 1.0e-9,
                channel_indices=tuple(int(value) for value in layout["correction_channel_indices"]),
                simulator_linear_fractional_delay_inverse=True,
            )
            audits.append(audit)
            corrected_paths.append(destination)
        prepared_roles[role] = {
            "status": "prepared",
            "raw_paths": [str(path.resolve()) for path in raw_paths],
            "corrected_paths": [str(path.resolve()) for path in corrected_paths],
            "period_paths": [str(path.resolve()) for path in corrected_paths],
            "correction_applied": True,
            "applied_delay_ns": correction_ns,
            "correction_source": case["conditions"][condition]["correction_source"],
            "truth_used_in_estimator": False,
            "truth_used_to_apply_correction": False,
            "correction_audit": audits,
        }
        input_dir = case_root / "inputs" / "production" / condition / role
        input_dir.mkdir(parents=True, exist_ok=True)
        for index, path in enumerate(corrected_paths):
            link = input_dir / f"period_{index:04d}.bin"
            if link.exists() or link.is_symlink():
                raise FileExistsError(f"refusing to overwrite corrected input link: {link}")
            link.symlink_to(path.resolve())
        branch_name = f"{condition}_{role}"
        old_stage1_pipe, old_e2e_pipe = stage1.PIPE, e2e.PIPE
        stage1.PIPE = production_binary
        e2e.PIPE = production_binary
        try:
            branch = stage1.run_production_branch(
                case_root,
                branch_name,
                input_dir,
                source_xml,
                truth,
                period_count,
                "local",
                layout,
                truth_path=scene_a0 / "truth",
                xml_overrides=XML_OVERRIDES,
            )
        finally:
            stage1.PIPE, e2e.PIPE = old_stage1_pipe, old_e2e_pipe
        gate = stage1.audit_production_path_evidence(branch)
        branch["production_path_gate"] = gate
        branch["condition"] = condition
        branch["role"] = role
        branch["blind_estimate_used_for_correction_ns"] = correction_ns
        stage1._write_json(case_root / "production" / branch_name / "branch_manifest.json", branch)
        production_roles[role] = branch
        _write_json(case_root / "case_manifest.json", case)
        if gate.get("status") != "passed":
            raise RuntimeError(f"production evidence gate failed for {branch_name}: {gate}")
    return case["conditions"][condition]


def _evaluate(case_root: Path, case: dict[str, Any], output_root: Path) -> dict[str, Any]:
    labels = {
        "A": "A0_Ideal",
        "B": "A1_Current_unknown_error",
        "C": "A2_Known_error_correction_upper_bound",
        "D3": "R12_D3_R9_Fixed",
        "M1": "R12_M1_Physics_Matched",
    }
    result: dict[str, Any] = {
        "single_seed_population_pd_pfa": "NOT_EVALUABLE",
        "false_alarm_scope": "descriptive H0 OFF counts only; no pooled or statistical Pfa claim",
        "conditions": {},
    }
    metrics_rows: list[dict[str, Any]] = []
    for label, condition in labels.items():
        branches = _condition_branches(case, condition)
        task_roles: dict[str, Any] = {}
        for role in ROLES:
            output_dir = output_root / "evaluation" / f"seed{SEED}" / f"{label}_{role}"
            manifest, provenance = r8_eval.build_task_manifest(
                case_root=case_root,
                case=case,
                condition=condition,
                role=role,
                union_rows=[],
                output_dir=output_dir,
            )
            task_result = task_eval.evaluate_manifest(output_dir / "evaluation_manifest.json")
            _write_json(output_dir / "task_metrics.json", task_result)
            task_eval._write_csv(
                output_dir / "associations.csv",
                task_result["association_evidence"] + task_result["candidate_pair_evidence"],
            )
            task_eval._write_csv(output_dir / "lineage.csv", task_result["lineage_evidence"])
            metrics = task_result.get("metrics", {})
            errors = metrics.get("parameter_errors", {}) if isinstance(metrics, Mapping) else {}
            radial_errors = errors.get("radial_velocity", {}) if isinstance(errors, Mapping) else {}
            metrics_rows.append({
                "seed": SEED,
                "condition": label,
                "role": role,
                "task_metrics": str((output_dir / "task_metrics.json").resolve()),
                "truth_opportunities": metrics.get("truth_opportunities"),
                "unique_correct_detections": metrics.get("unique_correct_detections"),
                "detection_rate": metrics.get("detection_rate"),
                "misses": metrics.get("misses"),
                "false_detections": metrics.get("false_detections"),
                "false_associations": metrics.get("false_associations"),
                "false_confirmed_tracks": metrics.get("false_confirmed_tracks"),
                "tracks": metrics.get("tracks"),
                "confirmed_tracks": metrics.get("confirmed_tracks"),
                "angle_rmse_deg": errors.get("angle_rmse_deg") if isinstance(errors, Mapping) else None,
                "ground_speed_rmse_mps": errors.get("ground_speed_rmse_mps") if isinstance(errors, Mapping) else None,
                "position_2d_rmse_m": errors.get("position_2d_rmse_m") if isinstance(errors, Mapping) else None,
                "radial_velocity_status": radial_errors.get("status") if isinstance(radial_errors, Mapping) else None,
                "radial_velocity_rmse_mps": radial_errors.get("rmse_mps") if isinstance(radial_errors, Mapping) else None,
                "cfar_branch_hit_cells": metrics.get("cfar_branch_hit_cells"),
                "unique_union_hit_cells_status": "NOT_EVALUABLE_NO_NUMERIC_CUT_UNION_AUDIT",
            })
            task_roles[role] = {
                "task_metrics": str((output_dir / "task_metrics.json").resolve()),
                "metrics": task_result.get("metrics", {}),
                "lineage_status": task_result.get("lineage", {}).get("status"),
                "provenance": provenance,
                "branch_gate": branches[role].get("production_path_gate"),
            }

        off_taps = r3_eval._tap_rows(branches["OFF"])
        h0_periods: list[dict[str, Any]] = []
        for period, tap in sorted(off_taps.items()):
            arrays = r3_eval._tap_arrays(tap)
            row = arrays["row"]
            h0_periods.append(r3_eval.active_support_csi_power(
                arrays["before_power"],
                arrays["csi_after"].real,
                arrays["csi_after"].imag,
                row_start=int(row["az_st"]),
                row_end=int(row["az_ed"]),
                col_start=int(row["rg_st"]),
                col_end=int(row["rg_ed"]),
            ))
        valid_h0 = [row for row in h0_periods if row.get("status") == "evaluable"]
        valid_cells = sum(int(row.get("valid_cells", 0)) for row in valid_h0)
        before_power = sum(float(row.get("before_mean_power", 0.0)) * int(row.get("valid_cells", 0)) for row in valid_h0)
        after_power = sum(float(row.get("csi_output_mean_power", 0.0)) * int(row.get("valid_cells", 0)) for row in valid_h0)
        h0_csi = {
            "status": "evaluable" if valid_cells > 0 and before_power > 0 and after_power > 0 else "NOT_EVALUABLE",
            "valid_cells": valid_cells,
            "before_mean_power": before_power / valid_cells if valid_cells else None,
            "csi_output_mean_power": after_power / valid_cells if valid_cells else None,
            "cancellation_db": 10.0 * math.log10(before_power / after_power) if before_power > 0 and after_power > 0 else None,
            "per_period": h0_periods,
        }
        try:
            target_transfer = r3_eval._target_transfer(case_root, case, condition, branches)
        except (OSError, ValueError, KeyError, IndexError) as exc:
            target_transfer = {"status": "NOT_EVALUABLE", "reason": str(exc)}
        radial = r3_eval._radial_velocity_audit(case, case_root, branches["ON"])
        h0_branch = branches["OFF"].get("target_off_waterfall", {})
        result["conditions"][label] = {
            "condition_name": condition,
            "production_path_all_roles_passed": all(_branch_passed(branches[role]) for role in ROLES),
            "roles": task_roles,
            "h0_false_clusters": h0_branch.get("false_clusters"),
            "h0_false_confirmed_tracks": h0_branch.get("false_tracks"),
            "h0_false_detection_rows": h0_branch.get("protocol_false_detections"),
            "h0_csi_active_support": h0_csi,
            "target_transfer": target_transfer,
            "radial_velocity_audit": radial,
        }

    a0_paths = case["conditions"]["A0_Ideal"]["input_preparation"]
    for label, condition in labels.items():
        condition_record = case["conditions"][condition]
        closures = {}
        for role in ROLES:
            ref = [Path(str(value)) for value in a0_paths[role]["period_paths"]]
            candidate = [Path(str(value)) for value in condition_record["input_preparation"][role]["period_paths"]]
            closures[role] = stage1.audit_fused_input_closure(ref, candidate, case["protocol_layout"])
        result["conditions"][label]["input_fusion_closure_to_A0"] = closures
    result["task_metrics_csv"] = str((output_root / "evaluation" / "r12_task_metrics.csv").resolve())
    r8_eval.write_csv(output_root / "evaluation" / "r12_task_metrics.csv", metrics_rows)
    return result


def run_r12(output_root: Path, binary: Path) -> dict[str, Any]:
    output_root = output_root.expanduser().resolve()
    if output_root.exists() and (not output_root.is_dir() or any(output_root.iterdir())):
        raise ValueError(f"refusing to overwrite non-empty R12 output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    preflight = _native_preflight(binary, output_root)
    config = stage1.load_stage1_config()
    config["scenario_template"] = str(stage1.TEMPLATE)
    identity = stage1._scene_identity_for_block(r3_runner._pilot_block(config, SEED), DELAY_TRUTH_NS, 3)
    case_root = output_root / "cases" / str(identity["case_id"])
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--output-root",
        str(output_root),
        "--seed",
        str(SEED),
        "--production-binary",
        str(binary.resolve()),
    ]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "PGRCC Phase R12 independent seed-404 physics-matched delay production pilot",
        "created_at_utc": _now(),
        "status": "in_progress",
        "command": {"argv": command, "shell": shlex.join(command)},
        "baseline_commit": "3e23235180f59fbb09b890af000544ae7a7c9fd0",
        "git": _git_identity(),
        "production_binary": preflight["binary"],
        "native_cuda_preflight": preflight,
        "design": {
            "seed": SEED,
            "period_count": 3,
            "delay_error_ns": DELAY_TRUTH_NS,
            "conditions": ["A0_Ideal", "A1_Current_unknown_error", "A2_Known_error_correction_upper_bound", *R12_CONDITIONS],
            "roles": list(ROLES),
            "blind_estimator_input": "A1_Current_unknown_error ON raw physical C1/C2 only",
            "known_truth_used_only_for_A2_correction_and_evaluation": True,
            "d3_fixed_calibration_ns": R9_FIXED_D3_NS,
            "ai_training": False,
            "router_enabled": False,
            "six_parameter_estimation": False,
            "corex": False,
        },
        "scene_identity": identity,
        "source_identity_before": r3_runner._source_identity(),
        "gpu_status_before": preflight["gpu"],
        "disk_status_before": preflight["disk"],
        "status_labels": {
            "IMPLEMENTED": True,
            "EXECUTED": False,
            "EVALUABLE": False,
            "PERFORMANCE_VERIFIED": False,
        },
        "case_root": str(case_root),
        "progress": [],
    }
    manifest_path = output_root / "manifest.json"
    manifest["source_files"] = {
        path.name: _file_record(path)
        for path in (
            Path(__file__),
            Path(__file__).with_name("physics_matched_delay.py"),
            ROOT / "scripts/run_delay_stage1_formal.py",
            ROOT / "scripts/run_track_manager_e2e.py",
            ROOT / "scripts/estimate_channel_delay.py",
            ROOT / "scripts/phase_r/evaluate_r7_task_metrics.py",
            ROOT / "configs/research/channel_delay_stage1_formal.json",
            ROOT / "simulator/scenarios/beam50_one_targets_continuous.run.json",
        )
    }
    _write_json(manifest_path, manifest)
    try:
        old_stage1_pipe, old_e2e_pipe = stage1.PIPE, e2e.PIPE
        stage1.PIPE = Path(str(preflight["binary"]["path"]))
        e2e.PIPE = Path(str(preflight["binary"]["path"]))
        try:
            case = stage1.run_stage1_case(
                config,
                identity,
                case_root,
                "local",
                conditions_to_run=BASE_CONDITIONS,
            )
        finally:
            stage1.PIPE, e2e.PIPE = old_stage1_pipe, old_e2e_pipe
        _write_json(case_root / "case_manifest.json", case)
        manifest["stage1_case_status"] = case.get("status")
        manifest["stage1_case_manifest"] = str((case_root / "case_manifest.json").resolve())
        manifest["progress"].append({"stage": "A0_B_Known_native_cuda", "status": case.get("status")})
        production_started = any(
            isinstance(condition, Mapping)
            and isinstance(condition.get("production"), Mapping)
            and any("production_returncode" in record for record in condition["production"].values() if isinstance(record, Mapping))
            for condition in case.get("conditions", {}).values()
        )
        manifest["status_labels"]["EXECUTED"] = bool(case.get("simulation")) or production_started
        if case.get("status") != "completed":
            raise RuntimeError(f"base A0/B/Known Stage1/Production path failed: {case.get('status')}")
        if case.get("area_clutter_gate", {}).get("status") != "passed":
            raise RuntimeError("Stage2 area-clutter evidence gate failed")
        if case.get("known_correction_physical_closure", {}).get("status") != "passed":
            raise RuntimeError("Known Physical Correction failed A0 input closure")
        for condition in BASE_CONDITIONS:
            branches = _condition_branches(case, condition)
            if not all(_branch_passed(branches[role]) for role in ROLES):
                raise RuntimeError(f"Native CUDA production gate failed for {condition}")

        baseline_comparability = _operator_comparability(case)
        if baseline_comparability.get("status") != "passed":
            raise RuntimeError("A0/B/Known Production operator configurations are not comparable")
        manifest["base_processing_chain_comparability"] = baseline_comparability
        _write_json(manifest_path, manifest)

        b_on = _raw_period_paths(case, "A1_Current_unknown_error", "ON")
        b_prod = _condition_branches(case, "A1_Current_unknown_error")["ON"]
        m1 = _blind_m1_from_b_on(
            b_on,
            Path(str(b_prod["production_xml"])),
        )
        manifest["blind_estimator"] = {
            **m1,
            "evaluator_only_truth_ns": DELAY_TRUTH_NS,
            "evaluator_only_error_ns": float(m1["delta_tau_ns"]) - DELAY_TRUTH_NS,
            "input_files": [_file_record(path) for path in b_on],
            "runtime_xml": _file_record(Path(str(b_prod["production_xml"]))),
        }
        if m1.get("status") != "ok" or not isinstance(m1.get("delta_tau_ns"), (int, float)):
            raise RuntimeError(f"M1 failed its frozen blind estimate gate: {m1}")
        for index, (name, delay_ns) in enumerate(R12_CONDITIONS.items()):
            chosen_delay = float(m1["delta_tau_ns"]) if delay_ns is None else float(delay_ns)
            _run_corrected_condition(case_root, case, name, chosen_delay, Path(str(preflight["binary"]["path"])))
            manifest["progress"].append({"stage": name, "status": "all_three_roles_passed", "delay_ns": chosen_delay})
            manifest["gpu_status_after_last_condition"] = stage1._gpu_status()
            manifest["disk_status_after_last_condition"] = stage1._disk_status(output_root)
            _write_json(case_root / "case_manifest.json", case)
            _write_json(manifest_path, manifest)

        comparability = _operator_comparability(case)
        manifest["all_condition_processing_chain_comparability"] = comparability
        if comparability.get("status") != "passed":
            raise RuntimeError("R12 conditions changed Production operator configuration")
        evaluation = _evaluate(case_root, case, output_root)
        manifest["evaluation"] = evaluation
        manifest["source_identity_after"] = r3_runner._source_identity()
        manifest["gpu_status_after"] = stage1._gpu_status()
        manifest["disk_status_after"] = stage1._disk_status(output_root)
        condition_evaluable = all(
            evaluation.get("conditions", {}).get(label, {}).get("production_path_all_roles_passed") is True
            and evaluation.get("conditions", {}).get(label, {}).get("target_transfer", {}).get("status") == "evaluable"
            and evaluation.get("conditions", {}).get(label, {}).get("roles", {}).get("ON", {}).get("metrics", {}).get("truth_opportunities", 0) > 0
            for label in ("A", "B", "C", "D3", "M1")
        )
        manifest["status_labels"] = {
            "IMPLEMENTED": True,
            "EXECUTED": True,
            "EVALUABLE": condition_evaluable and all(
                _branch_passed(branch)
                for condition in [*BASE_CONDITIONS, *R12_CONDITIONS]
                for branch in _condition_branches(case, condition).values()
            ),
            "PERFORMANCE_VERIFIED": False,
            "note": "single independent seed is feasibility evidence only; population Pd/Pfa is not verified",
        }
        manifest["status"] = "completed" if manifest["status_labels"]["EVALUABLE"] else "completed_with_evaluation_gaps"
        _write_json(case_root / "case_manifest.json", case)
        _write_json(manifest_path, manifest)
        return manifest
    except Exception as exc:
        manifest["status"] = "failed_or_incomplete"
        manifest["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        manifest["source_identity_after"] = r3_runner._source_identity()
        manifest["gpu_status_after"] = stage1._gpu_status()
        manifest["disk_status_after"] = stage1._disk_status(output_root)
        _write_json(manifest_path, manifest)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, choices=(SEED,), default=SEED)
    parser.add_argument("--production-binary", type=Path, default=DEFAULT_BINARY)
    args = parser.parse_args(argv)
    try:
        manifest = run_r12(args.output_root, args.production_binary)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"R12 failed: {exc}\n")
    print(json.dumps({
        "status": manifest.get("status"),
        "seed": SEED,
        "output_root": str(args.output_root.expanduser().resolve()),
        "status_labels": manifest.get("status_labels"),
        "blind_estimate_ns": manifest.get("blind_estimator", {}).get("delta_tau_ns"),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if manifest.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
