#!/usr/bin/env python3
"""R9: audit deterministic delay estimates and replay qualified corrections."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_delay_stage1_formal as stage1  # noqa: E402
from scripts import run_track_manager_e2e as e2e  # noqa: E402
from scripts.delay_stage1_core import estimate_delay_d1_d2_d3  # noqa: E402
from scripts.phase_r import audit_r8_cfar_numeric_reference as cfar_audit  # noqa: E402
from scripts.phase_r import evaluate_r3_minimal_cuda_causal_pilot as r3_eval  # noqa: E402
from scripts.phase_r import evaluate_r8_multiseed_causal_pilot as r8_eval  # noqa: E402
from scripts.phase_r import evaluate_r7_task_metrics as task_eval  # noqa: E402

BASELINE_COMMIT = "22cefb8e947a8a3565a88445fe2c1d3616408c47"
EXPECTED_R7_BINARY_SHA256 = "de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00"
DEFAULT_OUTPUT = ROOT / "outputs/phase_r_r9_deterministic_delay_20261023"
R3_CASE_MANIFEST = ROOT / "docs/research/phase_r/evidence/r3_minimal_cuda_causal_pilot_20261021/manifests/seed101_case_manifest.json"
R8_ROOT = ROOT / "outputs/phase_r8_unknown_delay_multiseed_20261023"
R8_EVALUATION = ROOT / "docs/research/phase_r/evidence/r8_unknown_delay_multiseed_20261023/evaluation"
R7_BINARY = ROOT / "build_r7/GMTI_pipe_core"
SEEDS = (202, 303)
ROLES = ("ON", "OFF", "TO")
STATIC_CONDITION = "R9_Static_R3_D3"
ONLINE_CONDITION = "R9_Online_D3"
SELECTION_ORDER = (
    "D3_Huber_weighted_LS",
    "D2_weighted_LS",
    "D1_ordinary_LS",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    path = path.resolve()
    return {
        "path": str(path),
        "exists": path.is_file(),
        "bytes": path.stat().st_size if path.is_file() else None,
        "sha256": _sha256(path) if path.is_file() else None,
    }


def _git_record() -> dict[str, Any]:
    def run(*argv: str) -> str:
        result = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"git command failed: {shlex.join(argv)}: {result.stderr.strip()}")
        return result.stdout.strip()

    return {
        "commit": run("git", "rev-parse", "HEAD"),
        "branch": run("git", "branch", "--show-current"),
        "tracked_status": run("git", "status", "--short", "--untracked-files=no"),
    }


def _seed_case(seed: int) -> tuple[Path, dict[str, Any]]:
    matches = list((R8_ROOT / f"seed{seed}" / "cases").glob("*/case_manifest.json"))
    if len(matches) != 1:
        raise ValueError(f"expected one R8 case manifest for seed {seed}, found {len(matches)}")
    path = matches[0].resolve()
    case = _read_json(path)
    if case.get("status") != "completed":
        raise ValueError(f"R8 input case is not completed: {path}")
    return path.parent, case


def _period_paths(case: Mapping[str, Any], condition: str, role: str) -> list[Path]:
    prepared = case.get("conditions", {}).get(condition, {}).get("input_preparation", {}).get(role, {})
    values = prepared.get("raw_paths", prepared.get("period_paths", []))
    paths = [Path(str(value)).resolve() for value in values] if isinstance(values, list) else []
    if not paths or any(not path.is_file() for path in paths):
        raise FileNotFoundError(f"missing R8 raw periods: {condition}/{role}")
    return paths


def _estimate(paths: Sequence[Path], layout: Mapping[str, Any]) -> dict[str, Any]:
    """Only accepts protocol IQ paths and the acquisition layout; it has no truth input."""
    f1, f2 = stage1._load_fused_from_periods(paths, layout)
    rows = estimate_delay_d1_d2_d3(f1, f2, float(layout["fs_hz"]))
    return {
        "shape": list(f1.shape),
        "methods": rows,
        "cross_spectrum_definition": "sum_pulses(FFT(F1)*conj(FFT(F2)))",
    }


def _estimator_summary(paths: Sequence[Path], layout: Mapping[str, Any]) -> dict[str, Any]:
    full = _estimate(paths, layout)
    period_rows = []
    for period_id, path in enumerate(paths):
        estimate = _estimate([path], layout)
        period_rows.append({
            "period_id": period_id,
            "input": _file_record(path),
            "pulse_count": estimate["shape"][0],
            "methods": estimate["methods"],
        })
    return {
        "inputs": [_file_record(path) for path in paths],
        "input_role": None,
        "pulse_count": full["shape"][0],
        "sample_count": full["shape"][1],
        "methods": full["methods"],
        "per_period": period_rows,
        "cross_spectrum_definition": full["cross_spectrum_definition"],
        "truth_used_in_estimator": False,
    }


def _selected_delay(methods: Mapping[str, Mapping[str, Any]]) -> tuple[float | None, str | None]:
    return stage1._select_delay_estimate(list(methods.values()))


def _closure_channels(value: Mapping[str, Any]) -> dict[str, Any]:
    channels = value.get("channels", {})
    return {
        name: {
            key: row.get(key)
            for key in ("relative_rms_error", "phase_rms_rad", "reference_mean_power", "error_mean_power", "status")
        }
        for name, row in channels.items()
        if isinstance(row, Mapping)
    }


def _source_records() -> dict[str, Any]:
    paths = (
        Path(__file__),
        ROOT / "scripts/delay_stage1_core.py",
        ROOT / "scripts/run_delay_stage1_formal.py",
        ROOT / "scripts/estimate_channel_delay.py",
        ROOT / "scripts/run_track_manager_e2e.py",
        ROOT / "scripts/phase_r/evaluate_r8_multiseed_causal_pilot.py",
        ROOT / "scripts/phase_r/evaluate_r7_task_metrics.py",
    )
    return {path.name: _file_record(path) for path in paths}


def run_preflight(output_root: Path) -> None:
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite R9 output directory: {output_root}")
    output_root.mkdir(parents=True)
    if not R7_BINARY.is_file() or _sha256(R7_BINARY) != EXPECTED_R7_BINARY_SHA256:
        raise ValueError("R7 Production binary identity does not match the frozen R8/R7 binary")
    git = _git_record()
    if git["commit"] != BASELINE_COMMIT:
        raise ValueError(f"expected baseline {BASELINE_COMMIT}, got {git['commit']}")

    r3 = _read_json(R3_CASE_MANIFEST)
    estimator_record = r3.get("delay_estimator", {})
    static_value = float(estimator_record["selected_delay_ns"])
    if estimator_record.get("selected_method") != "D3_Huber_weighted_LS":
        raise ValueError("R3 fixed calibration is not the recorded D3 selection")
    calibration_input = r3.get("calibration_input", {})
    static_calibration = {
        "status": "PREVIOUSLY_EXECUTED",
        "delay_ns": static_value,
        "method": estimator_record["selected_method"],
        "source_role": estimator_record.get("input_role"),
        "source_scientific_input": estimator_record.get("scientific_input"),
        "truth_used_in_estimator": estimator_record.get("truth_used_in_estimator"),
        "input_path": estimator_record.get("input_path"),
        "input_sha256": calibration_input.get("sha256"),
        "source_case_manifest": str(R3_CASE_MANIFEST.resolve()),
        "current_production_qualification": "PENDING_R9_R7_REPLAY",
        "online_deployable": False,
    }
    if static_calibration["truth_used_in_estimator"] is not False:
        raise ValueError("R3 fixed calibration lacks a truth-blind provenance record")

    seeds: dict[str, Any] = {}
    for seed in SEEDS:
        case_root, case = _seed_case(seed)
        layout = case["protocol_layout"]
        # Estimator calls are isolated from case truth and receive only B/ON protocol IQ.
        b_on_paths = _period_paths(case, "A1_Current_unknown_error", "ON")
        a0_on_paths = _period_paths(case, "A0_Ideal", "ON")
        online = _estimator_summary(b_on_paths, layout)
        online["input_role"] = "A1_Current_unknown_error_ON_target_present"
        zero = _estimator_summary(a0_on_paths, layout)
        zero["input_role"] = "A0_Ideal_ON_zero_injection_bias_diagnostic"
        selected_value, selected_method = _selected_delay(online["methods"])
        online["selection_rule"] = list(SELECTION_ORDER)
        online["selected_delay_ns"] = selected_value
        online["selected_method"] = selected_method
        online["selection_is_finite"] = selected_value is not None and math.isfinite(selected_value)
        if not online["selection_is_finite"]:
            online["status"] = "NOT_AVAILABLE"
        else:
            online["status"] = "ESTIMATED"
        seeds[str(seed)] = {
            "case_root": str(case_root),
            "case_manifest": _file_record(case_root / "case_manifest.json"),
            "layout": layout,
            "online_estimation": online,
            "zero_injection_diagnostic": zero,
            "r8_known_correction_closure": case.get("known_correction_physical_closure"),
            "current_vs_ideal_closure": {},
            "corrections": {},
        }

    # Freeze the estimator output and selection before creating or running any production branch.
    frozen = {
        "created_at_utc": _now(),
        "selection_rule": list(SELECTION_ORDER),
        "online_input_policy": "A1_Current_unknown_error/ON only; no A0, OFF, TO, truth, or paired reference is passed to the estimator",
        "fixed_calibration": static_calibration,
        "seeds": {
            seed: {
                "online_estimation": row["online_estimation"],
                "zero_injection_diagnostic": row["zero_injection_diagnostic"],
            }
            for seed, row in seeds.items()
        },
    }
    _write_json(output_root / "preproduction_estimation.json", frozen)

    for seed in SEEDS:
        seed_key = str(seed)
        case_root, case = _seed_case(seed)
        layout = seeds[seed_key]["layout"]
        choices = {
            STATIC_CONDITION: (static_value, "R3_seed101_A1_OFF_target_free_D3"),
            ONLINE_CONDITION: (
                seeds[seed_key]["online_estimation"]["selected_delay_ns"],
                "this_seed_A1_ON_D3_frozen_selection",
            ),
        }
        for condition, (delay_ns, source) in choices.items():
            if delay_ns is None:
                seeds[seed_key]["corrections"][condition] = {"status": "NOT_AVAILABLE", "source": source}
                continue
            correction_record: dict[str, Any] = {
                "status": "PREPARED",
                "applied_delay_ns": float(delay_ns),
                "source": source,
                "correction_operator": "inverse_stage2_zero_extended_linear_interpolator",
                "correction_channel_indices_zero_based": list(layout["correction_channel_indices"]),
                "roles": {},
            }
            for role in ROLES:
                raw = _period_paths(case, "A1_Current_unknown_error", role)
                ideal = _period_paths(case, "A0_Ideal", role)
                baseline = stage1.audit_fused_input_closure(ideal, raw, layout)
                seeds[seed_key]["current_vs_ideal_closure"][role] = {
                    "status": baseline.get("status"),
                    "channels": _closure_channels(baseline),
                }
                input_dir = output_root / "cases" / f"seed{seed}" / condition / "prepared_inputs" / role
                input_dir.mkdir(parents=True, exist_ok=False)
                corrected: list[Path] = []
                rewrites = []
                for index, source_path in enumerate(raw):
                    destination = input_dir / f"period_{index:04d}.bin"
                    audit = stage1.rewrite_float32_protocol_delay(
                        source_path,
                        destination,
                        pulse_len=int(layout["pulse_len"]),
                        channel_count=int(layout["channel_count"]),
                        fs_hz=float(layout["fs_hz"]),
                        delta_tau_sec=float(delay_ns) * 1.0e-9,
                        channel_indices=layout["correction_channel_indices"],
                        simulator_linear_fractional_delay_inverse=True,
                    )
                    corrected.append(destination)
                    rewrites.append({**audit, "output": _file_record(destination)})
                to_ideal = stage1.audit_fused_input_closure(ideal, corrected, layout)
                to_current = stage1.audit_fused_input_closure(raw, corrected, layout)
                current_f2 = baseline.get("channels", {}).get("F2", {}).get("relative_rms_error")
                corrected_f2 = to_ideal.get("channels", {}).get("F2", {}).get("relative_rms_error")
                f1_effect = to_current.get("channels", {}).get("F1", {}).get("relative_rms_error")
                role_gate = {
                    "f1_unchanged_by_channel2_correction": (
                        isinstance(f1_effect, (int, float)) and f1_effect <= 1.0e-12
                    ),
                    "f2_complex_rms_closer_to_A0": (
                        isinstance(current_f2, (int, float))
                        and isinstance(corrected_f2, (int, float))
                        and corrected_f2 < current_f2
                    ),
                    "current_vs_A0": _closure_channels(baseline),
                    "corrected_vs_A0": _closure_channels(to_ideal),
                    "corrected_vs_current": _closure_channels(to_current),
                    "f2_relative_rms_reduction_fraction": (
                        1.0 - corrected_f2 / current_f2
                        if isinstance(current_f2, (int, float)) and current_f2 > 0.0
                        and isinstance(corrected_f2, (int, float)) else None
                    ),
                }
                role_gate["physics_gate"] = (
                    role_gate["f1_unchanged_by_channel2_correction"]
                    and role_gate["f2_complex_rms_closer_to_A0"]
                )
                correction_record["roles"][role] = {
                    "raw_paths": [str(path) for path in raw],
                    "corrected_paths": [str(path.resolve()) for path in corrected],
                    "rewrite_records": rewrites,
                    "closure": role_gate,
                }
            correction_record["physics_gate"] = (
                "passed"
                if all(item["closure"]["physics_gate"] for item in correction_record["roles"].values())
                else "failed"
            )
            seeds[seed_key]["corrections"][condition] = correction_record

    gates = {
        "baseline_known_correction_closure_passed": all(
            isinstance(seeds[str(seed)]["r8_known_correction_closure"], Mapping)
            and seeds[str(seed)]["r8_known_correction_closure"].get("status") == "passed"
            for seed in SEEDS
        ),
        "online_estimates_available": all(
            seeds[str(seed)]["online_estimation"]["status"] == "ESTIMATED" for seed in SEEDS
        ),
        "static_calibration_physics_gate_passed": all(
            seeds[str(seed)]["corrections"].get(STATIC_CONDITION, {}).get("physics_gate") == "passed"
            for seed in SEEDS
        ),
        "online_d3_physics_gate_passed": all(
            seeds[str(seed)]["corrections"].get(ONLINE_CONDITION, {}).get("physics_gate") == "passed"
            for seed in SEEDS
        ),
    }
    gate_status = "passed" if all(gates.values()) else "failed"
    preflight = {
        "schema_version": 1,
        "created_at_utc": _now(),
        "status": gate_status,
        "gates": gates,
        "selection_rule_frozen_before_production": True,
        "seeds": seeds,
    }
    _write_json(output_root / "preflight_manifest.json", preflight)
    manifest = {
        "schema_version": 1,
        "task": "R9 deterministic channel-delay estimator audit and R7 Native CUDA correction replay",
        "status": "preflight_passed" if gate_status == "passed" else "preflight_failed",
        "created_at_utc": _now(),
        "baseline_commit_required": BASELINE_COMMIT,
        "git": git,
        "source_files": _source_records(),
        "r3_static_calibration": static_calibration,
        "r8_seed_cases": {str(seed): seeds[str(seed)]["case_manifest"] for seed in SEEDS},
        "preproduction_estimation": str((output_root / "preproduction_estimation.json").resolve()),
        "preflight_manifest": str((output_root / "preflight_manifest.json").resolve()),
        "preflight_gates": gates,
        "gpu_execution": "NOT_RUN_IN_PREFLIGHT",
        "native_cuda": {"use_corex": "OFF", "production_binary": _file_record(R7_BINARY)},
        "output_root": str(output_root),
    }
    _write_json(output_root / "manifest.json", manifest)
    print(json.dumps({"status": manifest["status"], "output_root": str(output_root), "gates": gates}, sort_keys=True))


def _gpu_snapshot() -> dict[str, Any]:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,driver_version,pstate,power.draw,power.limit,temperature.gpu,utilization.gpu,memory.used,memory.total", "--format=csv,noheader"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    return {"returncode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}


def _r9_case_path(output_root: Path, seed: int) -> Path:
    return output_root / "cases" / f"seed{seed}" / "case_manifest.json"


def _load_or_create_r9_case(output_root: Path, seed: int) -> tuple[Path, dict[str, Any]]:
    target = _r9_case_path(output_root, seed)
    if target.is_file():
        return target.parent, _read_json(target)
    _, source_case = _seed_case(seed)
    case = copy.deepcopy(source_case)
    case["r9"] = {"created_at_utc": _now(), "r8_source_case_manifest": str(_seed_case(seed)[0] / "case_manifest.json")}
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_json(target, case)
    return target.parent, case


def run_production(output_root: Path) -> None:
    output_root = output_root.resolve()
    manifest_path = output_root / "manifest.json"
    manifest = _read_json(manifest_path)
    preflight = _read_json(output_root / "preflight_manifest.json")
    if manifest.get("status") not in {"preflight_passed", "production_in_progress"} or preflight.get("status") != "passed":
        raise ValueError("R9 CUDA Production requires every preflight physics gate to pass")
    if not R7_BINARY.is_file() or _sha256(R7_BINARY) != EXPECTED_R7_BINARY_SHA256:
        raise ValueError("R7 Production binary identity does not match the frozen R8/R7 binary")
    os.environ["USE_COREX"] = "OFF"
    e2e.PIPE = R7_BINARY
    gpu_before = _gpu_snapshot()
    if gpu_before["returncode"] != 0:
        raise RuntimeError(f"nvidia-smi failed in elevated CUDA run: {gpu_before['stderr']}")
    manifest["status"] = "production_in_progress"
    manifest["gpu_before"] = gpu_before
    manifest["native_cuda"] = {"use_corex": "OFF", "production_binary": _file_record(R7_BINARY), "backend": "local Native CUDA"}
    manifest["production_branches"] = manifest.get("production_branches", [])
    _write_json(manifest_path, manifest)

    for seed in SEEDS:
        seed_root, case = _load_or_create_r9_case(output_root, seed)
        case_root, source_case = _seed_case(seed)
        layout = source_case["protocol_layout"]
        scene = source_case["simulation"]["A1_ON"]
        scene_output = Path(str(scene["output_dir"]))
        period_count = len(_period_paths(source_case, "A1_Current_unknown_error", "ON"))
        truth = e2e._truth_by_period(scene_output, period_count)
        if len(truth) != period_count:
            raise ValueError(f"Stage2 truth period count mismatch for seed {seed}")
        for condition in (STATIC_CONDITION, ONLINE_CONDITION):
            frozen_candidate = preflight["seeds"][str(seed)]["corrections"][condition]
            delay_ns = float(frozen_candidate["applied_delay_ns"])
            case_condition = case.setdefault("conditions", {}).setdefault(condition, {})
            case_condition.update({
                "input_preparation": {},
                "truth_used_in_estimator": False,
                "correction_applied": True,
                "delay_estimate_ns": delay_ns,
                "production": case_condition.get("production", {}),
            })
            for role in ROLES:
                branch_name = f"{condition}_{role}"
                branch_root = seed_root / "production" / branch_name
                production_map = case_condition["production"]
                previous = production_map.get(role)
                if isinstance(previous, Mapping) and previous.get("production_path_gate", {}).get("status") == "passed":
                    continue
                if branch_root.exists():
                    raise FileExistsError(f"production branch exists without a passing resumable record: {branch_root}")
                corrected_record = frozen_candidate["roles"][role]
                input_dir = Path(corrected_record["corrected_paths"][0]).parent
                source_branch = source_case["conditions"]["A1_Current_unknown_error"]["production"][role]
                source_xml = Path(str(source_branch["source_xml"]))
                xml_overrides = source_branch.get("xml_overrides", {})
                record = stage1.run_production_branch(
                    seed_root,
                    branch_name,
                    input_dir,
                    source_xml,
                    truth,
                    period_count,
                    str(source_case.get("input_mode", "local")),
                    layout,
                    truth_path=scene_output / "truth",
                    xml_overrides=xml_overrides,
                )
                gate = stage1.audit_production_path_evidence(record)
                record["production_path_gate"] = gate
                record["production_binary"] = _file_record(R7_BINARY)
                record["input_generation"] = {
                    "applied_delay_ns": delay_ns,
                    "source": frozen_candidate["source"],
                    "period_files": corrected_record["rewrite_records"],
                }
                record["runtime_xml_sha256"] = _sha256(Path(str(record["production_xml"]))) if Path(str(record.get("production_xml", ""))).is_file() else None
                record["production_log_sha256"] = _sha256(Path(str(record["production_log"]))) if Path(str(record.get("production_log", ""))).is_file() else None
                if record.get("input_mode") == "local":
                    command = [
                        str(R7_BINARY), "--config", str(record["production_xml"]),
                        "--result-dir", str(record["result_dir"]),
                        "--track-debug-dir", str(branch_root / "track_debug_runs"),
                        "--track-debug-dump", "on", "--runtime-mode=debug",
                        "--runtime-diagnostics=on", "--local-test",
                    ] + [f"{index + 1}={path}" for index, path in enumerate(corrected_record["corrected_paths"])]
                    record["command"] = {"argv": command, "shell": shlex.join(command), "USE_COREX": "OFF"}
                _write_json(branch_root / "branch_manifest.json", record)
                production_map[role] = record
                prep_entry = {
                    "status": "prepared",
                    "raw_paths": corrected_record["raw_paths"],
                    "period_paths": corrected_record["corrected_paths"],
                    "corrected_paths": corrected_record["corrected_paths"],
                    "applied_delay_ns": delay_ns,
                    "correction_source": frozen_candidate["source"],
                    "correction_model": "inverse_stage2_zero_extended_linear_interpolator",
                    "truth_used_in_estimator": False,
                }
                case_condition["input_preparation"][role] = prep_entry
                _write_json(_r9_case_path(output_root, seed), case)
                branch_progress = {
                    "seed": seed,
                    "condition": condition,
                    "role": role,
                    "status": record.get("status"),
                    "production_path_gate": gate.get("status"),
                    "branch_manifest": str((branch_root / "branch_manifest.json").resolve()),
                }
                manifest["production_branches"].append(branch_progress)
                manifest["gpu_after_last_branch"] = _gpu_snapshot()
                manifest["status"] = "production_in_progress" if gate.get("status") == "passed" else "production_failed"
                _write_json(manifest_path, manifest)
                print(json.dumps(branch_progress, sort_keys=True), flush=True)
                if gate.get("status") != "passed":
                    return

            # The three paired roles must use the same processing operators.
            fingerprints = {}
            for role in ROLES:
                branch = case_condition["production"][role]
                xml = Path(str(branch["production_xml"])).read_text(encoding="utf-8")
                normalized = r8_eval.normalized_runtime_xml(xml)
                fingerprints[role] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            case_condition["operator_consistency"] = {
                "status": "passed" if len(set(fingerprints.values())) == 1 else "failed",
                "role_fingerprints": fingerprints,
                "ignored_run_specific_xml_fields": ["result_add", "track_debug_dir"],
            }
            if case_condition["operator_consistency"]["status"] != "passed":
                manifest["status"] = "production_failed"
                _write_json(manifest_path, manifest)
                return
            _write_json(_r9_case_path(output_root, seed), case)

    manifest["status"] = "production_passed"
    manifest["gpu_after"] = _gpu_snapshot()
    _write_json(manifest_path, manifest)


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    r8_eval.write_csv(path, rows)


def _csi_evaluation(case_root: Path, case: Mapping[str, Any], condition: str) -> dict[str, Any]:
    roles = case["conditions"][condition]["production"]
    off_taps = r3_eval._tap_rows(roles["OFF"])
    per_period = []
    for period in sorted(off_taps):
        arrays = r3_eval._tap_arrays(off_taps[period])
        row = arrays["row"]
        per_period.append(r3_eval.active_support_csi_power(
            arrays["before_power"], arrays["csi_after"].real, arrays["csi_after"].imag,
            row_start=int(row["az_st"]), row_end=int(row["az_ed"]),
            col_start=int(row["rg_st"]), col_end=int(row["rg_ed"]),
        ))
    before = sum(item.get("before_mean_power", 0.0) * item.get("valid_cells", 0) for item in per_period if item.get("status") == "evaluable")
    after = sum(item.get("csi_output_mean_power", 0.0) * item.get("valid_cells", 0) for item in per_period if item.get("status") == "evaluable")
    cells = sum(item.get("valid_cells", 0) for item in per_period if item.get("status") == "evaluable")
    transfer = r3_eval._target_transfer(case_root, case, condition, roles)
    return {
        "csi_h0_active_support": {
            "status": "evaluable" if cells and before > 0 and after > 0 else "NOT_EVALUABLE",
            "valid_cells": cells,
            "before_mean_power": before / cells if cells else None,
            "csi_output_mean_power": after / cells if cells else None,
            "cancellation_db": 10.0 * math.log10(before / after) if before > 0 and after > 0 else None,
            "per_period": per_period,
        },
        "target_transfer": transfer,
    }


def _task_metric_file(seed: int, label: str, role: str, output_root: Path) -> Path:
    if label in {"A", "B", "C"}:
        return R8_EVALUATION / f"seed{seed}" / f"{label}_{role}" / "task_metrics.json"
    return output_root / "evaluation" / f"seed{seed}" / f"{label}_{role}" / "task_metrics.json"


def _task_comparison_row(seed: int, label: str, role: str, path: Path) -> dict[str, Any]:
    result = _read_json(path)
    metrics = result.get("metrics", {})
    errors = metrics.get("parameter_errors", {})
    target = metrics.get("target_only_response", {})
    return {
        "seed": seed,
        "condition": label,
        "role": role,
        "task_metrics_path": str(path.resolve()),
        "truth_opportunities": metrics.get("truth_opportunities"),
        "unique_correct_detections": metrics.get("unique_correct_detections"),
        "detection_rate": metrics.get("detection_rate"),
        "misses": metrics.get("misses"),
        "false_associations": metrics.get("false_associations"),
        "false_detections": metrics.get("false_detections"),
        "false_clusters": metrics.get("false_clusters"),
        "false_confirmed_tracks": metrics.get("false_confirmed_tracks"),
        "unique_union_hit_cells": metrics.get("unique_union_hit_cells", {}).get("count"),
        "cfar_branch_hit_cells": metrics.get("cfar_branch_hit_cells"),
        "cfar_status": metrics.get("cfar_status"),
        "lineage_status": metrics.get("lineage_status"),
        "angle_rmse_deg": errors.get("angle_rmse_deg"),
        "ground_speed_rmse_mps": errors.get("ground_speed_rmse_mps"),
        "position_2d_rmse_m": errors.get("position_2d_rmse_m"),
        "radial_velocity_status": errors.get("radial_velocity", {}).get("status"),
        "radial_velocity_rmse_mps": errors.get("radial_velocity", {}).get("rmse_mps"),
        "target_only_amplitude_ratio": target.get("amplitude_ratio"),
        "target_only_energy_ratio": target.get("energy_ratio"),
        "association_status": metrics.get("association_status"),
        "track_status": metrics.get("track_status"),
    }


def _lineage_rows(seed: int, label: str, path: Path) -> list[dict[str, Any]]:
    result = _read_json(path)
    associations = result.get("association_evidence", [])
    pairs = result.get("candidate_pair_evidence", [])
    lineage = result.get("lineage_evidence", [])
    periods = sorted({
        int(row["period_id"])
        for row in associations
        if row.get("target_id") == "stage1_target_1" and row.get("period_id") is not None
    })
    output = []
    for period in periods:
        association = next(
            row for row in associations
            if row.get("target_id") == "stage1_target_1" and int(row["period_id"]) == period
        )
        candidates = [
            row for row in pairs
            if row.get("target_id") == "stage1_target_1" and int(row.get("period_id", -1)) == period
        ]
        if association.get("matched"):
            candidate = next(
                (row for row in candidates if str(row.get("det_id")) == str(association.get("det_id"))),
                {},
            )
        else:
            candidates.sort(key=lambda row: (
                abs(int(row["range_delta_bins"])) if row.get("range_delta_bins") is not None else 10**9,
                abs(int(row["doppler_delta_bins"])) if row.get("doppler_delta_bins") is not None else 10**9,
                int(row.get("det_id", 10**9)) if str(row.get("det_id", "")).isdigit() else 10**9,
            ))
            candidate = candidates[0] if candidates else {}
        candidate_id = candidate.get("det_id", association.get("det_id"))
        linked_clusters = []
        for row in lineage:
            if int(row.get("period_id", -1)) != period:
                continue
            ids = row.get("final_detection_ids", [])
            if isinstance(ids, str):
                try:
                    ids = ast.literal_eval(ids)
                except (ValueError, SyntaxError):
                    ids = []
            if candidate_id is not None and str(candidate_id) in {str(value) for value in (ids or [])}:
                linked_clusters.append(row)
        linked = bool(linked_clusters)
        cluster = linked_clusters[0] if linked_clusters else {}
        output.append({
            "seed": seed,
            "condition": label,
            "period_id": period,
            "target_id": "stage1_target_1",
            "target_matched_by_frozen_evaluator": bool(association.get("matched")),
            "accepted_detection_id": association.get("det_id"),
            "accepted_detection_row": association.get("detection_row"),
            "candidate_detection_id": candidate_id,
            "candidate_detection_row": candidate.get("detection_row"),
            "range_delta_bins": candidate.get("range_delta_bins"),
            "doppler_delta_bins": candidate.get("doppler_delta_bins"),
            "gate_result": candidate.get("gate_result"),
            "eligible": candidate.get("eligible"),
            "cluster_final_detection_linked": linked,
            "cluster_index": cluster.get("cluster_index"),
            "cluster_row": cluster.get("cluster_row"),
            "cluster_col": cluster.get("cluster_col"),
            "lineage_stage": cluster.get("lineage_stage"),
            "interpretation": (
                "truth-matched final detection with cluster lineage"
                if association.get("matched") and linked
                else "production candidate only; not truth matched, so do not interpret its Doppler offset as target motion"
                if linked
                else "no linked final-detection cluster evidence"
            ),
        })
    return output


def run_summary(output_root: Path) -> None:
    output_root = output_root.resolve()
    manifest_path = output_root / "manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("status") != "evaluation_complete":
        raise ValueError("R9 summary requires completed production and frozen task evaluation")
    evaluation_root = output_root / "evaluation"
    r3 = _read_json(R3_CASE_MANIFEST)
    r3_truth = float(r3["delay_error_ns"])
    r3_estimator = r3["delay_estimator"]
    r3_rows = {str(row["method"]): row for row in r3_estimator["method_rows"]}
    frozen = _read_json(output_root / "preproduction_estimation.json")
    fixed = float(frozen["fixed_calibration"]["delay_ns"])
    delay_rows = []
    for method in SELECTION_ORDER:
        row = r3_rows.get(method)
        if not isinstance(row, Mapping) or row.get("delta_tau_ns") is None:
            continue
        value = float(row["delta_tau_ns"])
        delay_rows.append({
            "source": "R3_seed101_target_free_previously_executed",
            "seed": 101,
            "condition": "R3_static_calibration_source",
            "input_role": r3_estimator.get("input_role"),
            "method": method,
            "estimate_ns": value,
            "truth_ns_evaluator_only": r3_truth,
            "estimate_error_ns_estimate_minus_truth": value - r3_truth,
            "residual_ns_truth_minus_estimate": r3_truth - value,
            "fit_residual_phase_rms_rad": row.get("residual_phase_rms_rad"),
            "support_count": row.get("support_count"),
            "r2_or_confidence": row.get("r2", row.get("confidence")),
            "formal_uncertainty_ns": row.get("delay_std_ns"),
            "truth_used_in_estimator": False,
        })

    online_d3_by_seed = []
    for seed in SEEDS:
        item = frozen["seeds"][str(seed)]
        online = item["online_estimation"]
        zero = item["zero_injection_diagnostic"]
        case_root, case = _seed_case(seed)
        truth = float(case["delay_error_ns"])
        for method in SELECTION_ORDER:
            row = online["methods"][method]
            value = float(row["delta_tau_ns"])
            delay_rows.append({
                "source": "R9_online_target_present_A1_ON",
                "seed": seed,
                "condition": "R9_online_candidate",
                "input_role": online["input_role"],
                "method": method,
                "estimate_ns": value,
                "truth_ns_evaluator_only": truth,
                "estimate_error_ns_estimate_minus_truth": value - truth,
                "residual_ns_truth_minus_estimate": truth - value,
                "fit_residual_phase_rms_rad": row.get("residual_phase_rms_rad"),
                "support_count": row.get("support_count"),
                "r2_or_confidence": row.get("r2", row.get("confidence")),
                "formal_uncertainty_ns": row.get("delay_std_ns"),
                "truth_used_in_estimator": False,
                "selected_by_frozen_rule": method == online.get("selected_method"),
            })
            zero_row = zero["methods"][method]
            zero_value = float(zero_row["delta_tau_ns"])
            delay_rows.append({
                "source": "R9_A0_zero_injection_diagnostic_evaluator_side",
                "seed": seed,
                "condition": "R9_zero_injection_bias",
                "input_role": zero["input_role"],
                "method": method,
                "estimate_ns": zero_value,
                "truth_ns_evaluator_only": 0.0,
                "estimate_error_ns_estimate_minus_truth": zero_value,
                "residual_ns_truth_minus_estimate": -zero_value,
                "fit_residual_phase_rms_rad": zero_row.get("residual_phase_rms_rad"),
                "support_count": zero_row.get("support_count"),
                "r2_or_confidence": zero_row.get("r2", zero_row.get("confidence")),
                "formal_uncertainty_ns": zero_row.get("delay_std_ns"),
                "truth_used_in_estimator": False,
            })
            if method == "D3_Huber_weighted_LS":
                per_period = [float(period["methods"][method]["delta_tau_ns"]) for period in online["per_period"]]
                online_d3_by_seed.append({
                    "seed": seed,
                    "estimate_ns": value,
                    "per_period_estimates_ns": per_period,
                    "per_period_mean_ns": float(np.mean(per_period)),
                    "per_period_sample_std_ns": float(np.std(per_period, ddof=1)) if len(per_period) > 1 else None,
                    "per_period_min_ns": min(per_period),
                    "per_period_max_ns": max(per_period),
                    "formal_confidence_interval": None,
                })

    static_error = fixed - r3_truth
    delay_evaluation = {
        "schema_version": 1,
        "created_at_utc": _now(),
        "truth_source": "R8/R3 evaluator-side configuration, read only after preproduction_estimation.json was frozen",
        "sign_convention": {
            "estimate_error_ns_estimate_minus_truth": "negative means under-estimated positive channel delay",
            "residual_ns_truth_minus_estimate": "positive means residual delay remains after correction",
        },
        "static_calibration": {
            "estimate_ns": fixed,
            "truth_ns": r3_truth,
            "estimate_error_ns": static_error,
            "residual_ns_after_correction": -static_error,
            "source_seed": 101,
            "test_seed_count": len(SEEDS),
            "one_frozen_value_applied_to_both_seeds": True,
        },
        "online_d3_seed_consistency": {
            "seed_records": online_d3_by_seed,
            "mean_estimate_ns": float(np.mean([row["estimate_ns"] for row in online_d3_by_seed])),
            "sample_std_between_seeds_ns": float(np.std([row["estimate_ns"] for row in online_d3_by_seed], ddof=1)),
            "seed_count": len(online_d3_by_seed),
        },
        "uncertainty_note": "D1/D2/D3 do not report a calibrated delay standard error or confidence interval. Per-period spread is descriptive consistency across three packets groups, not a confidence interval.",
        "method_records": delay_rows,
        "statuses": {
            "IMPLEMENTED": True,
            "PREVIOUSLY_EXECUTED": True,
            "QUALIFIED_FOR_CURRENT_PRODUCTION": "controlled R7 replay for these R8 scene inputs only",
            "ONLINE_DEPLOYABLE": False,
        },
    }
    _write_json(evaluation_root / "delay_estimator_evaluation.json", delay_evaluation)

    labels = (("A", "A"), ("B", "B"), ("C", "C"), ("Static", STATIC_CONDITION), ("Online", ONLINE_CONDITION))
    metric_rows = []
    lineage_rows = []
    for seed in SEEDS:
        for label, lookup in labels:
            for role in ROLES:
                path = _task_metric_file(seed, lookup, role, output_root)
                if path.is_file():
                    metric_rows.append(_task_comparison_row(seed, label, role, path))
            if label in {"B", "C", "Static", "Online"}:
                on_path = _task_metric_file(seed, lookup, "ON", output_root)
                lineage_rows.extend(_lineage_rows(seed, label, on_path))
    _write_csv(evaluation_root / "task_metrics_comparison.csv", metric_rows)
    _write_csv(evaluation_root / "target_lineage_audit.csv", lineage_rows)
    summary_path = evaluation_root / "r9_evaluation_summary.json"
    summary = _read_json(summary_path)
    summary["delay_estimator_evaluation"] = str((evaluation_root / "delay_estimator_evaluation.json").resolve())
    summary["task_metrics_comparison"] = str((evaluation_root / "task_metrics_comparison.csv").resolve())
    summary["target_lineage_audit"] = str((evaluation_root / "target_lineage_audit.csv").resolve())
    _write_json(summary_path, summary)
    manifest["status"] = "summary_complete"
    manifest["summary_artifacts"] = {
        "delay_estimator_evaluation": str((evaluation_root / "delay_estimator_evaluation.json").resolve()),
        "task_metrics_comparison": str((evaluation_root / "task_metrics_comparison.csv").resolve()),
        "target_lineage_audit": str((evaluation_root / "target_lineage_audit.csv").resolve()),
    }
    _write_json(manifest_path, manifest)
    print(json.dumps({"status": manifest["status"], "summary_artifacts": manifest["summary_artifacts"]}, sort_keys=True))


def run_evaluation(output_root: Path) -> None:
    output_root = output_root.resolve()
    manifest_path = output_root / "manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("status") != "production_passed":
        raise ValueError("R9 evaluation requires all 12 CUDA Production branches to pass")
    evaluation_root = output_root / "evaluation"
    evaluation_root.mkdir(parents=True, exist_ok=True)
    if (evaluation_root / "r9_evaluation_summary.json").exists():
        raise FileExistsError(f"R9 evaluation summary already exists: {evaluation_root}")
    summary: dict[str, Any] = {"schema_version": 1, "created_at_utc": _now(), "seeds": {}}
    for seed in SEEDS:
        case_root = _r9_case_path(output_root, seed).parent
        case = _read_json(_r9_case_path(output_root, seed))
        seed_summary: dict[str, Any] = {"conditions": {}}
        for condition in (STATIC_CONDITION, ONLINE_CONDITION):
            role_results: dict[str, Any] = {}
            for role in ROLES:
                branch = case["conditions"][condition]["production"][role]
                union_rows: list[dict[str, Any]] = []
                audit_status = "NOT_REQUIRED_FOR_TARGET_ONLY"
                if role in {"ON", "OFF"}:
                    # R8 exports raw CFAR maps in a separate Native CUDA diagnostic replay.
                    # The ordinary Production branch intentionally retains geometry and lineage only.
                    cfar_audit.R8_CONDITIONS = tuple(dict.fromkeys((*cfar_audit.R8_CONDITIONS, condition)))
                    audit_root = case_root / f"cfar_numeric_audit_{condition}_{role}"
                    if audit_root.exists() and any(audit_root.iterdir()):
                        previous = audit_root / "manifest.json"
                        if previous.is_file() and _read_json(previous).get("status") == "passed":
                            audit_manifest = _read_json(previous)
                        else:
                            raise FileExistsError(f"preserving existing non-passing CFAR audit attempt: {audit_root}")
                    else:
                        os.environ["USE_COREX"] = "OFF"
                        print(json.dumps({"stage": "cfar_numeric_replay_start", "seed": seed, "condition": condition, "role": role}, sort_keys=True), flush=True)
                        audit_manifest = cfar_audit.run_audit(
                            case_root, audit_root, R7_BINARY, condition, role,
                        )
                    numeric = audit_manifest.get("cfar_numeric_audit", {})
                    if audit_manifest.get("status") != "passed" or numeric.get("status") != "passed":
                        manifest["status"] = "evaluation_failed"
                        manifest["evaluation_failure"] = {"seed": seed, "condition": condition, "role": role, "reason": "CFAR numeric audit failed", "audit": str((audit_root / "manifest.json").resolve())}
                        _write_json(manifest_path, manifest)
                        return
                    union_rows = r8_eval._audit_union_rows(audit_root / "manifest.json", audit_manifest)
                    audit_status = "passed"
                    print(json.dumps({"stage": "cfar_numeric_replay_complete", "seed": seed, "condition": condition, "role": role, "status": audit_status}, sort_keys=True), flush=True)

                case_output = evaluation_root / f"seed{seed}" / f"{condition}_{role}"
                task_manifest, provenance = r8_eval.build_task_manifest(
                    case_root=case_root,
                    case=case,
                    condition=condition,
                    role=role,
                    union_rows=union_rows,
                    output_dir=case_output,
                )
                result = task_eval.evaluate_manifest(case_output / "evaluation_manifest.json")
                _write_json(case_output / "task_metrics.json", result)
                task_eval._write_csv(case_output / "associations.csv", result["association_evidence"] + result["candidate_pair_evidence"])
                task_eval._write_csv(case_output / "lineage.csv", result["lineage_evidence"])
                role_results[role] = {
                    "audit_status": audit_status,
                    "task_metrics": str((case_output / "task_metrics.json").resolve()),
                    "metrics": result.get("metrics"),
                    "provenance": provenance,
                }
                print(json.dumps({"stage": "task_evaluation_complete", "seed": seed, "condition": condition, "role": role}, sort_keys=True), flush=True)
            csi = _csi_evaluation(case_root, case, condition)
            csi_path = evaluation_root / f"seed{seed}" / f"{condition}_csi_metrics.json"
            _write_json(csi_path, csi)
            seed_summary["conditions"][condition] = {"roles": role_results, "csi": csi, "csi_metrics_path": str(csi_path.resolve())}
        summary["seeds"][str(seed)] = seed_summary

    # Attach the frozen R8 A/B/C evaluations for direct same-seed comparison.
    baseline = {}
    for seed in SEEDS:
        baseline[str(seed)] = {}
        for label in ("A", "B", "C"):
            baseline[str(seed)][label] = {}
            for role in ROLES:
                path = R8_EVALUATION / f"seed{seed}" / f"{label}_{role}" / "task_metrics.json"
                if path.is_file():
                    baseline[str(seed)][label][role] = {"path": str(path.resolve()), "metrics": _read_json(path).get("metrics")}
                else:
                    baseline[str(seed)][label][role] = {"status": "NOT_AVAILABLE", "path": str(path)}
    summary["frozen_r8_baseline_task_metrics"] = baseline
    _write_json(evaluation_root / "r9_evaluation_summary.json", summary)
    manifest["status"] = "evaluation_complete"
    manifest["evaluation_summary"] = str((evaluation_root / "r9_evaluation_summary.json").resolve())
    manifest["evaluation_completed_at_utc"] = _now()
    _write_json(manifest_path, manifest)
    print(json.dumps({"status": manifest["status"], "summary": manifest["evaluation_summary"]}, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("preflight", "production", "evaluate", "summarize"), default="preflight")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        if args.phase == "preflight":
            run_preflight(args.output_root)
        elif args.phase == "production":
            run_production(args.output_root)
        elif args.phase == "summarize":
            run_summary(args.output_root)
        else:
            run_evaluation(args.output_root)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"R9 {args.phase} failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
