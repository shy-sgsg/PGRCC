#!/usr/bin/env python3
"""Run one frozen R21 seed through local Native CUDA Production."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_delay_stage1_formal as stage1  # noqa: E402
from scripts import run_track_manager_e2e as e2e  # noqa: E402
from scripts.phase_r import evaluate_r3_minimal_cuda_causal_pilot as r3_eval  # noqa: E402
from scripts.phase_r import run_r12_physics_matched_delay as r12  # noqa: E402
from scripts.phase_r import run_r3_minimal_cuda_causal_pilot as r3_runner  # noqa: E402
from scripts.phase_r import r20_harmonized_task_review as r20  # noqa: E402
from scripts.phase_r import r20_historical_evidence_adapter as adapter  # noqa: E402
from scripts.phase_r import r21_historical_task_review as r21_eval  # noqa: E402


SEEDS = (1001, 1002)
ROLES = ("ON", "OFF", "TO")
BASE_CONDITIONS = (
    "A0_Ideal",
    "A1_Current_unknown_error",
    "A2_Known_error_correction_upper_bound",
)
FIXED_D3_NS = 3.4099650318669545
METHOD_CONDITIONS = {
    "R21_D3_R9_Fixed": {
        "label": "D3_R9_fixed",
        "correction_source": "R9_frozen_calibration",
        "estimate_key": None,
    },
    "R21_D3_Huber_B_ON": {
        "label": "D3_Huber_weighted_LS",
        "correction_source": "same_seed_B_ON_raw_C1_C2_Huber_fit",
        "estimate_key": "D3_Huber_weighted_LS",
    },
    "R21_M1_Physics_Matched": {
        "label": "M1_physics_matched",
        "correction_source": "same_seed_B_ON_raw_C1_C2_M1_fit",
        "estimate_key": "M1",
    },
}
CANONICAL_CONDITIONS = {
    "A0_Ideal": "A0",
    "A1_Current_unknown_error": "B",
    "A2_Known_error_correction_upper_bound": "Known",
    "R21_D3_R9_Fixed": "D3_R9_fixed",
    "R21_D3_Huber_B_ON": "D3_Huber",
    "R21_M1_Physics_Matched": "M1",
}
XML_OVERRIDES = {
    "csi_metrics_enable": "true",
    "csi_metrics_dump_power_maps": "true",
    "csi_metrics_dump_intermediate_maps": "true",
    "csi_metrics_beam_id": 50,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "path": adapter.audit_path(resolved),
        "exists": resolved.is_file(),
        "bytes": resolved.stat().st_size if resolved.is_file() else None,
        "sha256": _sha256(resolved) if resolved.is_file() else None,
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (dict, list, tuple, set)) else value
                for key, value in row.items()
            })


def _git_identity() -> dict[str, Any]:
    def run(*argv: str) -> str:
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError(f"git command failed: {shlex.join(argv)}: {result.stderr.strip()}")
        return result.stdout.strip()

    untracked = subprocess.run(
        ("git", "ls-files", "--others", "--exclude-standard", "-z"),
        cwd=ROOT, capture_output=True, check=False,
    )
    if untracked.returncode:
        raise RuntimeError(f"git untracked-file count failed: {untracked.stderr.decode(errors='replace').strip()}")
    untracked_count = sum(bool(item) for item in untracked.stdout.split(b"\0"))
    return {
        "commit": run("git", "rev-parse", "HEAD"),
        "branch": run("git", "branch", "--show-current"),
        "tracked_status": run("git", "status", "--short", "--untracked-files=no"),
        "untracked_file_count": untracked_count,
    }


def _seed_block(config: dict[str, Any], seed: int) -> dict[str, Any]:
    if seed not in SEEDS:
        raise ValueError(f"seed is outside the frozen R21 candidates: {seed}")
    return r3_runner._pilot_block(config, seed)


def _native_cuda_preflight(binary: Path, output_root: Path) -> dict[str, Any]:
    binary = binary.expanduser().resolve()
    cache = binary.parent / "CMakeCache.txt"
    if not cache.is_file():
        raise FileNotFoundError(f"Native CUDA build CMakeCache missing: {cache}")
    cache_lines = cache.read_text(encoding="utf-8", errors="replace").splitlines()
    corex_lines = [line for line in cache_lines if line.startswith("USE_COREX:BOOL=")]
    if corex_lines != ["USE_COREX:BOOL=OFF"]:
        raise ValueError(f"R21 requires Native CUDA with USE_COREX=OFF, got {corex_lines}")
    if not any(line.startswith("CMAKE_CUDA_COMPILER:") and "nvcc" in line.lower() for line in cache_lines):
        raise ValueError("build cache does not identify nvcc as the CUDA compiler")
    if not binary.is_file():
        raise FileNotFoundError(f"Native CUDA Production binary missing: {binary}")
    gpu = stage1._gpu_status()
    if not gpu.get("available"):
        raise RuntimeError(f"CUDA device unavailable to this process: {gpu}")
    disk = stage1._disk_status(output_root)
    if disk.get("error") or int(disk.get("free_bytes", 0)) <= 0:
        raise RuntimeError(f"insufficient or unavailable output disk: {disk}")
    return {
        "binary": {
            "path": str(binary), "repo_relative_path": adapter.audit_path(binary), "sha256": _sha256(binary),
            "build_cache": _file_record(cache), "backend": "local Native CUDA", "use_corex": False,
        },
        "gpu": gpu,
        "disk": disk,
    }


def _has_production_execution(case: Mapping[str, Any] | None) -> bool:
    """Stage2 scene generation alone is not an executed CUDA Production run."""
    conditions = case.get("conditions", {}) if isinstance(case, Mapping) else {}
    if not isinstance(conditions, Mapping):
        return False
    return any(
        isinstance(record, Mapping) and "production_returncode" in record
        for condition in conditions.values() if isinstance(condition, Mapping)
        for records in (condition.get("production", {}),)
        if isinstance(records, Mapping)
        for record in records.values()
    )


def qualify_blind_methods(estimate: Mapping[str, Any]) -> dict[str, Any]:
    """Accept only same-seed B/ON C1/C2 estimates with no truth input."""

    input_role = str(estimate.get("input_role", ""))
    if input_role != "A1_Current_unknown_error_ON_raw_C1_C2_only":
        raise ValueError(f"blind estimator input role is not eligible: {input_role!r}")
    if estimate.get("truth_used_in_estimator") is not False:
        raise ValueError("blind estimator reports truth use or omits its false flag")
    baseline = estimate.get("baselines", {})
    d3_record = baseline.get("D3_Huber_weighted_LS") if isinstance(baseline, Mapping) else None
    d3_value = d3_record.get("delta_tau_ns") if isinstance(d3_record, Mapping) else None
    try:
        d3_ns = float(d3_value)
    except (TypeError, ValueError):
        d3_ns = math.nan
    try:
        m1_ns = float(estimate.get("delta_tau_ns"))
    except (TypeError, ValueError):
        m1_ns = math.nan
    try:
        support_count = int(estimate.get("support_count"))
        d3_support_count = int(d3_record.get("support_count")) if isinstance(d3_record, Mapping) else 0
        frequency_span_hz = float(estimate.get("frequency_span_hz"))
    except (TypeError, ValueError, OverflowError):
        support_count, d3_support_count, frequency_span_hz = 0, 0, math.nan
    input_support_qualified = (
        support_count >= 8
        and math.isfinite(frequency_span_hz)
        and frequency_span_hz >= 3.0e6
    )
    d3_support_qualified = input_support_qualified and d3_support_count == support_count
    m1_available = estimate.get("status") == "ok" and math.isfinite(m1_ns) and input_support_qualified
    d3_available = math.isfinite(d3_ns) and d3_support_qualified
    return {
        "input_role": input_role,
        "truth_used_in_estimator": False,
        "d3_huber": {"status": "AVAILABLE" if d3_available else "NOT_AVAILABLE", "correction_ns": d3_ns if d3_available else None,
                     "source_record": d3_record if d3_available else None,
                     "support_qualified": d3_support_qualified},
        "m1": {"status": "AVAILABLE" if m1_available else "NOT_AVAILABLE", "correction_ns": m1_ns if m1_available else None,
               "source_record": {key: estimate.get(key) for key in (
                   "method", "status", "delta_tau_ns", "support_count", "frequency_span_hz",
                   "normalized_model_residual", "relative_response_model", "fallback_reason",
               )} if m1_available else None,
               "support_qualified": input_support_qualified},
    }


def _raw_period_paths(case: Mapping[str, Any], condition: str, role: str) -> list[Path]:
    try:
        prep = case["conditions"][condition]["input_preparation"][role]
        values = prep.get("raw_paths", prep.get("period_paths", []))
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"raw Stage2 periods missing for {condition}/{role}") from exc
    paths = [Path(str(value)).resolve() for value in values] if isinstance(values, list) else []
    if not paths or any(not path.is_file() for path in paths):
        raise FileNotFoundError(f"raw Stage2 periods missing for {condition}/{role}: {paths}")
    return paths


def _run_corrected_method(
    case_root: Path,
    case: dict[str, Any],
    condition: str,
    correction_ns: float,
    correction_source: str,
    method_estimate: Mapping[str, Any] | None,
    binary: Path,
) -> dict[str, Any]:
    source_condition = "A1_Current_unknown_error"
    layout = case.get("protocol_layout")
    if not isinstance(layout, Mapping):
        raise ValueError("case protocol_layout missing")
    period_count = int(case["scene_identity"]["period_count"])
    scene_a0 = Path(str(case["simulation"]["A0_ON"]["output_dir"]))
    source_xml = scene_a0 / "config" / "temp_config_stage2_period_0000.xml"
    truth = e2e._truth_by_period(scene_a0, period_count)
    if len(truth) != period_count:
        raise ValueError(f"A0 truth period mismatch: expected {period_count}, got {len(truth)}")

    prepared_roles: dict[str, Any] = {}
    production_roles: dict[str, Any] = {}
    condition_record = {
        "input_preparation": prepared_roles,
        "production": production_roles,
        "truth_used_in_estimator": False,
        "correction_applied": True,
        "correction_source": correction_source,
        "applied_delay_ns": correction_ns,
        "estimator_record": dict(method_estimate or {}),
    }
    case.setdefault("conditions", {})[condition] = condition_record
    for role in ROLES:
        raw_paths = _raw_period_paths(case, source_condition, role)
        corrected_root = case_root / "inputs" / condition / role / "prepared_inputs"
        corrected_root.mkdir(parents=True, exist_ok=True)
        corrected_paths: list[Path] = []
        audits: list[dict[str, Any]] = []
        for index, raw_path in enumerate(raw_paths):
            destination = corrected_root / f"period_{index:04d}.bin"
            audit = r12.delay_io.rewrite_float32_protocol_delay(
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
            "raw_paths": [str(path) for path in raw_paths],
            "raw_input_identities": [_file_record(path) for path in raw_paths],
            "corrected_paths": [str(path.resolve()) for path in corrected_paths],
            "period_paths": [str(path.resolve()) for path in corrected_paths],
            "correction_applied": True,
            "applied_delay_ns": correction_ns,
            "correction_source": correction_source,
            "truth_used_in_estimator": False,
            "truth_used_to_apply_correction": False,
            "correction_operator": "estimate_channel_delay.rewrite_float32_protocol_delay",
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
        stage1.PIPE = binary
        e2e.PIPE = binary
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
        branch.update({
            "condition": condition,
            "role": role,
            "correction_source": correction_source,
            "correction_applied": True,
            "blind_estimate_used_for_correction_ns": correction_ns,
            "truth_used_in_estimator": False,
            "truth_used_to_apply_correction": False,
            "production_path_gate": gate,
        })
        stage1._write_json(case_root / "production" / branch_name / "branch_manifest.json", branch)
        production_roles[role] = branch
        stage1._write_json(case_root / "case_manifest.json", case)
        if gate.get("status") != "passed":
            raise RuntimeError(f"Production evidence gate failed for {branch_name}: {gate}")
    return condition_record


def _area_clutter_audit(case: Mapping[str, Any]) -> dict[str, Any]:
    expected_roles = {
        "A0_ON": "on", "A0_OFF": "off", "A0_TO": "target_only",
        "A1_ON": "on", "A1_OFF": "off", "A1_TO": "target_only",
    }
    simulation = case.get("simulation", {})
    declared = case.get("paired_background", {})
    rows: list[dict[str, Any]] = []
    records: dict[str, dict[str, Any]] = {}

    def log_fields(path: Path) -> dict[str, str]:
        if not path.is_file():
            return {}
        return {
            key.strip(): value.strip()
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if "=" in line
            for key, value in (line.split("=", 1),)
        }

    for name, role in expected_roles.items():
        run = simulation.get(name, {})
        scenario_path = Path(str(run.get("scenario_path", "")))
        output_dir = Path(str(run.get("output_dir", "")))
        report = output_dir / "reports/stage2_simulation_report.md"
        log = output_dir / "logs/simulate_stage2.log"
        reasons = []
        if run.get("returncode") != 0:
            reasons.append("scene_run_failed")
        if not scenario_path.is_file() or not report.is_file() or not log.is_file():
            reasons.append("scene_scenario_report_or_log_missing")
        row: dict[str, Any] = {
            "scene": name, "role": role, "status": "NOT_EVALUABLE", "reasons": reasons,
            "report_path": adapter.audit_path(report) if report.is_file() else None,
            "report_sha256": _sha256(report) if report.is_file() else None,
            "scenario_sha256": _sha256(scenario_path) if scenario_path.is_file() else None,
        }
        rows.append(row)
        if reasons:
            continue
        scenario = stage1._read_json(scenario_path)
        area = stage1.audit_stage2_area_clutter_report(report.read_text(encoding="utf-8"), scenario)
        records[name] = {
            "scenario": scenario, "area": area, "log": log_fields(log),
            "mapping": output_dir / "reports/background_reuse_mapping.csv", "row": row,
        }
        row.update({
            "continuous_area_packets_reported": area.get("continuous_area_packets"),
            "continuous_area_samples_reported": area.get("continuous_area_samples"),
            "area_mean_power_measured": area.get("area_mean_power_measured"),
            "runtime_log_sha256": _sha256(log),
        })

    source = records.get("A0_ON")
    background: Path | None = None
    source_periods: list[int] = []
    if source:
        scenario, area, log, row = (
            source["scenario"], source["area"], source["log"], source["row"]
        )
        raw_dir = scenario.get("paired_background_output_dir")
        background = Path(str(raw_dir)).expanduser().resolve() if raw_dir else None
        if (
            not background or not isinstance(declared, Mapping)
            or not declared.get("directory")
            or Path(str(declared["directory"])).expanduser().resolve() != background
            or log.get("paired_background_output_dir") != str(background)
            or log.get("background_input_dir", "")
        ):
            row["reasons"].append("generated_background_source_mismatch")
        if area.get("status") != "passed":
            row["reasons"].extend(area.get("reasons", []))

        random_cfg = scenario.get("random", {})
        try:
            start = int(random_cfg.get("period_start", 0))
            count = int(random_cfg["period_count"])
            expected_periods = list(range(start, start + count))
        except (AttributeError, KeyError, TypeError, ValueError):
            expected_periods = []
            row["reasons"].append("source_period_count_invalid")
        manifest = background / "data/period_files.csv" if background else Path()
        try:
            with manifest.open("r", encoding="utf-8", newline="") as stream:
                source_rows = list(csv.DictReader(stream))
            source_periods = [int(item["period_id"]) for item in source_rows]
            if source_periods != expected_periods:
                row["reasons"].append("paired_background_period_manifest_mismatch")
            for item in source_rows:
                data = Path(item["file"])
                if not data.is_file() or data.stat().st_size != int(item["file_bytes"]):
                    row["reasons"].append("paired_background_period_data_invalid")
                    break
        except (OSError, KeyError, TypeError, ValueError, csv.Error):
            row["reasons"].append("paired_background_manifest_invalid")
        row["background_manifest_sha256"] = _sha256(manifest) if manifest.is_file() else None
        row["background_period_ids"] = source_periods
        row["continuous_area_packets"] = area.get("continuous_area_packets")
        row["continuous_area_samples"] = area.get("continuous_area_samples")
    def signature(scenario: Mapping[str, Any]) -> str | None:
        scene, platform, random = (
            scenario.get("scene"), scenario.get("platform"), scenario.get("random")
        )
        if not all(isinstance(value, Mapping) for value in (scene, platform, random)):
            return None
        fields = {
            "area": scene.get("area_clutter"), "range": [scene.get("range_min_m"), scene.get("range_max_m")],
            "ground": scene.get("ground_z_m"), "amplitude": scene.get("clutter_amplitude_scale"),
            "height": platform.get("origin_alt_m", platform.get("height_m")),
            "seed": random.get("random_seed"), "period_start": random.get("period_start", 0),
            "period_count": random.get("period_count"),
        }
        return json.dumps(fields, sort_keys=True)

    source_signature = signature(source["scenario"]) if source else None
    for name, record in records.items():
        if name == "A0_ON":
            continue
        scenario, area, log, mapping, row = (
            record["scenario"], record["area"], record["log"], record["mapping"], record["row"]
        )
        role = expected_roles[name]
        configured = scenario.get("background_input_dir")
        if not background or not configured or Path(str(configured)).expanduser().resolve() != background:
            row["reasons"].append("background_source_mismatch")
        if log.get("background_input_dir") != str(background or "") or log.get("paired_background_output_dir", ""):
            row["reasons"].append("runtime_background_source_mismatch")
        try:
            if float(log.get("background_input_scale", "nan")) != 1.0:
                row["reasons"].append("runtime_background_scale_mismatch")
        except (TypeError, ValueError):
            row["reasons"].append("runtime_background_scale_invalid")
        scene = scenario.get("scene", {})
        if scenario.get("stage1_role") != role or not isinstance(scene, Mapping) or scene.get("signal_only") is not (role == "target_only"):
            row["reasons"].append("scene_role_mismatch")
        if source_signature is None or signature(scenario) != source_signature:
            row["reasons"].append("clutter_scene_configuration_mismatch")
        if area.get("continuous_area_packets") != 0 or area.get("continuous_area_samples") != 0:
            row["reasons"].append("unexpected_duplicate_area_generation")
        try:
            with mapping.open("r", encoding="utf-8", newline="") as stream:
                mapping_rows = list(csv.DictReader(stream))
            mapping_periods = [int(item["period_id"]) for item in mapping_rows]
            mapping_matched = mapping_periods == source_periods and all(
                item.get("mapping_status") == "matched" for item in mapping_rows
            )
            if not mapping_matched:
                row["reasons"].append("background_reuse_mapping_not_matched")
            row["background_reuse_mapping_status"] = "passed" if mapping_matched else "failed"
            row["background_reuse_period_ids"] = mapping_periods
            row["background_reuse_mapping_sha256"] = _sha256(mapping)
        except (OSError, KeyError, TypeError, ValueError, csv.Error):
            row["reasons"].append("background_reuse_mapping_invalid")
            row["background_reuse_mapping_status"] = "NOT_EVALUABLE"

    for row in rows:
        if not row["reasons"] and row["scene"] in records:
            row["status"] = "passed"
    reused = [row for row in rows if row["scene"] != "A0_ON"]
    all_mappings_matched = len(reused) == 5 and all(
        row.get("background_reuse_mapping_status") == "passed" for row in reused
    )
    return {
        "status": "passed" if len(rows) == 6 and all(row["status"] == "passed" for row in rows) else "NOT_EVALUABLE",
        "scene_count": len(rows),
        "generated_source_scene": "A0_ON",
        "continuous_area_packets": source["area"].get("continuous_area_packets") if source else None,
        "continuous_area_samples": source["area"].get("continuous_area_samples") if source else None,
        "area_mean_power_measured": source["area"].get("area_mean_power_measured") if source else None,
        "source_physical_geometry_passed": bool(source and source["area"].get("status") == "passed"),
        "shared_background_source": adapter.audit_path(background) if background else None,
        "all_reuse_mappings_matched": all_mappings_matched,
        "all_scene_reports_present": len(records) == 6,
        "scenes": rows,
    }


def _input_closure_rows(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    layout = case["protocol_layout"]
    output: list[dict[str, Any]] = []
    a0 = case["conditions"]["A0_Ideal"]["input_preparation"]
    for condition in ("A2_Known_error_correction_upper_bound", *METHOD_CONDITIONS):
        if condition not in case.get("conditions", {}):
            continue
        candidate = case["conditions"][condition].get("input_preparation")
        if not isinstance(candidate, Mapping) or any(role not in candidate for role in ROLES):
            continue
        for role in ROLES:
            left = [Path(str(path)) for path in a0[role]["period_paths"]]
            right = [Path(str(path)) for path in candidate[role]["period_paths"]]
            result = stage1.audit_fused_input_closure(left, right, layout)
            output.append({"condition": condition, "role": role, **result})
    return output


def _build_bundles(case_root: Path, case: dict[str, Any]) -> list[dict[str, Any]]:
    evaluation_case = dict(case)
    evaluation_case["dataset"] = "R21"
    evaluation_case["dataset_key"] = str(case["scene_identity"].get("case_id", case_root.name))
    evaluation_case["case_root"] = str(case_root)
    evaluation_case["case_manifest_path"] = case_root / "case_manifest.json"
    evaluation_case["snapshot_manifest"] = {"build": {"GMTI_pipe_core": case.get("production_binary", {})}}
    branches: dict[tuple[str, str], Path] = {}
    for condition, record in evaluation_case.get("conditions", {}).items():
        production = record.get("production", {}) if isinstance(record, Mapping) else {}
        for role in ROLES:
            if role in production:
                branches[(condition, role)] = case_root / "production" / f"{condition}_{role}"
    evaluation_case["branches"] = branches
    bundles: list[dict[str, Any]] = []
    for (condition, role), branch_dir in sorted(branches.items()):
        bundle = r20._condition_role_bundle(evaluation_case, condition, role, branch_dir)
        bundle["case"] = evaluation_case
        bundle["canonical_condition"] = CANONICAL_CONDITIONS.get(condition, condition)
        bundle["identity"]["production_binary_sha256"] = case["production_binary"].get("sha256")
        bundle["identity"]["build_source_commit"] = case["production_binary"].get("source_commit")
        bundles.append(bundle)
    return bundles


def _h0_csi_summary(branches: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    periods: list[dict[str, Any]] = []
    for period, tap in sorted(r3_eval._tap_rows(branches["OFF"]).items()):
        arrays = r3_eval._tap_arrays(tap)
        row = arrays["row"]
        result = r3_eval.active_support_csi_power(
            arrays["before_power"], arrays["csi_after"].real, arrays["csi_after"].imag,
            row_start=int(row["az_st"]), row_end=int(row["az_ed"]),
            col_start=int(row["rg_st"]), col_end=int(row["rg_ed"]),
        )
        periods.append({"period_id": period, **result})
    valid = [row for row in periods if row.get("status") == "evaluable"]
    cells = sum(int(row.get("valid_cells", 0)) for row in valid)
    before = sum(float(row.get("before_mean_power", 0.0)) * int(row.get("valid_cells", 0)) for row in valid)
    after = sum(float(row.get("csi_output_mean_power", 0.0)) * int(row.get("valid_cells", 0)) for row in valid)
    return {
        "status": "EVALUABLE_DESCRIPTIVE_ONLY" if cells and before > 0 and after > 0 else "NOT_EVALUABLE",
        "valid_cells": cells,
        "before_mean_power": before / cells if cells else None,
        "csi_output_mean_power": after / cells if cells else None,
        "cancellation_db": 10.0 * math.log10(before / after) if before > 0 and after > 0 else None,
        "per_period": periods,
    }


def _evaluate_case(case_root: Path, case: dict[str, Any], bundles: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ranges: dict[tuple[str, str], dict[str, Any]] = {}
    doppler: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for bundle in bundles:
        key = (bundle["condition"], bundle["role"])
        ranges[key] = r21_eval._range_axis_audit(bundle)
        if bundle["role"] in {"ON", "TO"}:
            doppler[key] = [
                r21_eval._doppler_audit_row_v3(bundle, truth)
                for truth in bundle["truths"] if r20._visible_truth(truth)
            ]

    task_rows: list[dict[str, Any]] = []
    opportunity_rows: list[dict[str, Any]] = []
    doppler_rows: list[dict[str, Any]] = []
    track_rows: list[dict[str, Any]] = []
    parameter_rows: list[dict[str, Any]] = []
    h0_rows: list[dict[str, Any]] = []
    evaluations: dict[tuple[str, str], dict[str, Any]] = {}
    for bundle in bundles:
        condition, role = bundle["condition"], bundle["role"]
        key = (condition, role)
        if role == "OFF":
            evaluated: dict[str, Any] = {
                "status": "NOT_APPLICABLE_H0_ROLE", "target_result": None,
                "track_metrics": None, "parameter_records": [],
            }
            h0_rows.extend(r20._off_h0_rows(bundle))
        else:
            evaluated = r21_eval._evaluate_bundle_v3(bundle, ranges[key], doppler.get(key, []))
            result = evaluated.get("target_result") or {}
            for row in result.get("per_opportunity_status", []):
                opportunity_rows.append({
                    "condition": condition, "canonical_condition": bundle["canonical_condition"],
                    "role": role, "seed": case["scene_identity"]["seed"], **row,
                })
        evaluations[key] = evaluated
        target = evaluated.get("target_result") or {}
        metrics = target.get("metrics", {})
        task_rows.append({
            "seed": case["scene_identity"]["seed"], "condition": condition,
            "canonical_condition": bundle["canonical_condition"], "role": role,
            "status": evaluated.get("status"),
            "range_axis_status": ranges[key].get("status"),
            "visible_truth_opportunities": metrics.get("visible_truth_opportunities_total"),
            "valid_physics_verified_opportunities": metrics.get("truth_opportunities"),
            "hits": metrics.get("unique_hits"), "misses": metrics.get("misses"),
            "hit_fraction": metrics.get("truth_opportunity_hit_fraction"),
            "invalid_truth_opportunities": metrics.get("invalid_truth_opportunities"),
            "production_xml": adapter.audit_path(bundle["branch_dir"] / "production.xml"),
        })
        if role in {"ON", "TO"}:
            doppler_rows.extend(doppler[key])
        if role == "ON":
            track = evaluated.get("track_metrics") or {}
            track_rows.extend(r20._track_rows(bundle, evaluated))
            if track_rows and track:
                track_rows[-1].update({
                    "false_confirmed_track_ids_without_any_target_match": track.get("false_confirmed_track_ids_without_any_target_match"),
                    "false_confirmed_track_ids": track.get("false_confirmed_track_ids"),
                    "unique_track_ids_with_any_unmatched_payload": track.get("unique_track_ids_with_any_unmatched_payload"),
                    "unique_track_ids_with_any_unmatched_payload_ids": track.get("unique_track_ids_with_any_unmatched_payload_ids"),
                    "mixed_target_matched_and_unmatched_track_ids": track.get("mixed_target_matched_and_unmatched_track_ids"),
                    "unmatched_confirmed_payload_count": track.get("unmatched_confirmed_payload_count"),
                    "unmatched_payload_after_target_match_count": track.get("unmatched_payload_after_target_match_count"),
                    "track_ids_associated_with_multiple_target_ids": track.get("track_ids_associated_with_multiple_target_ids"),
                    "cross_target_association_payload_count": track.get("cross_target_association_payload_count"),
                })
            parameter_rows.extend(evaluated.get("parameter_records", []))

    condition_rows: list[dict[str, Any]] = []
    condition_auxiliary: dict[str, Any] = {}
    for condition in CANONICAL_CONDITIONS:
        condition_record = case.get("conditions", {}).get(condition, {})
        production = condition_record.get("production", {}) if isinstance(condition_record, Mapping) else {}
        if not isinstance(production, Mapping) or set(production) != set(ROLES):
            continue
        branches = r12._condition_branches(case, condition)
        try:
            target_transfer = r3_eval._target_transfer(case_root, case, condition, branches)
        except (OSError, ValueError, KeyError, IndexError) as exc:
            target_transfer = {"status": "NOT_EVALUABLE", "reason": str(exc)}
        h0_csi = _h0_csi_summary(branches)
        condition_auxiliary[condition] = {
            "target_transfer": target_transfer,
            "h0_csi": h0_csi,
            "processing_chain_passed": all(r12._branch_passed(branches[role]) for role in ROLES),
        }
        for role in ROLES:
            evaluation = evaluations[(condition, role)]
            target = evaluation.get("target_result") or {}
            metrics = target.get("metrics", {})
            task_record = next(row for row in task_rows if row["condition"] == condition and row["role"] == role)
            condition_h0_rows = [row for row in h0_rows if row.get("condition") == condition]
            condition_rows.append({
                "seed": case["scene_identity"]["seed"], "condition": condition,
                "canonical_condition": CANONICAL_CONDITIONS[condition], "role": role,
                "task_status": evaluation.get("status"),
                "truth_opportunities": metrics.get("truth_opportunities"),
                "hits": metrics.get("unique_hits"), "misses": metrics.get("misses"),
                "hit_fraction": metrics.get("truth_opportunity_hit_fraction"),
                "false_confirmed_track_count": (evaluation.get("track_metrics") or {}).get("false_confirmed_track_count") if role == "ON" else None,
                "confirmed_payload_count": (evaluation.get("track_metrics") or {}).get("confirmed_payload_count") if role == "ON" else None,
                "h0_descriptive_status": "EVALUABLE_DESCRIPTIVE_ONLY" if role == "OFF" and condition_h0_rows else "NOT_EVALUABLE",
                "production_path_passed": r12._branch_passed(branches[role]),
                "range_axis_status": task_record.get("range_axis_status"),
            })

    return {
        "task_metrics": task_rows,
        "task_metrics_by_condition": condition_rows,
        "truth_opportunity_audit": opportunity_rows,
        "doppler_opportunity_audit": doppler_rows,
        "track_semantics": track_rows,
        "parameter_records": parameter_rows,
        "h0_descriptive_metrics": h0_rows,
        "condition_auxiliary": condition_auxiliary,
    }


def run_seed(output_root: Path, seed: int, binary: Path) -> dict[str, Any]:
    output_root = output_root.expanduser().resolve()
    if seed not in SEEDS:
        raise ValueError(f"seed must be one of {SEEDS}")
    if output_root.exists() and (not output_root.is_dir() or any(output_root.iterdir())):
        raise ValueError(f"refusing to overwrite non-empty R21 output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    preflight = _native_cuda_preflight(binary, output_root)
    config = stage1.load_stage1_config()
    config["scenario_template"] = str(stage1.TEMPLATE)
    identity = stage1._scene_identity_for_block(_seed_block(config, seed), 4.0, 3)
    case_root = output_root / "cases" / str(identity["case_id"])
    source_files = [
        Path(__file__), Path(__file__).with_name("r21_task_evaluator.py"),
        Path(__file__).with_name("r21_historical_task_review.py"),
        ROOT / "scripts/phase_r/run_r12_physics_matched_delay.py",
        ROOT / "scripts/phase_r/physics_matched_delay.py",
        ROOT / "scripts/run_delay_stage1_formal.py",
        ROOT / "scripts/estimate_channel_delay.py",
        ROOT / "scripts/phase_r/evaluate_r7_task_metrics.py",
        ROOT / "configs/research/channel_delay_stage1_formal.json",
        stage1.TEMPLATE,
    ]
    git = _git_identity()
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "PGRCC Phase R21 unknown static channel delay paired CUDA Production pilot",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "in_progress",
        "git": git,
        "entry_commit": git["commit"],
        "source_identity_before": r3_runner._source_identity(),
        "source_files": {path.name: _file_record(path) for path in source_files if path.is_file()},
        "production_binary": preflight["binary"],
        "native_cuda_preflight": preflight,
        "design": {
            "seed": seed, "period_count": 3, "target_velocity_mps": 6.7,
            "target_snr_db": 30.0, "injected_channel_delay_ns": 4.0,
            "conditions": [*BASE_CONDITIONS, *METHOD_CONDITIONS],
            "paired_roles": list(ROLES),
            "known_truth_use": "evaluator-only Known correction and post-run truth evaluation",
            "blind_estimator_inputs": "same-seed B/ON raw physical C1/C2 and B Production reported geometry only",
            "ai_training": False, "router_enabled": False, "corex": False,
            "statistical_pfa": "NOT_EVALUABLE_WITH_TWO_INDEPENDENT_SEEDS",
        },
        "scene_identity": identity,
        "config": _file_record(ROOT / "configs/research/channel_delay_stage1_formal.json"),
        "case_root": adapter.audit_path(case_root),
        "output_root": adapter.audit_path(output_root),
        "gpu_status_before": preflight["gpu"],
        "disk_status_before": preflight["disk"],
        "status_labels": {"IMPLEMENTED": True, "EXECUTED": False, "EVALUABLE": False, "PERFORMANCE_VERIFIED": False},
        "progress": [],
    }
    _write_json(output_root / "manifest.json", manifest)
    case: dict[str, Any] | None = None
    try:
        old_stage1_pipe, old_e2e_pipe = stage1.PIPE, e2e.PIPE
        stage1.PIPE = Path(str(preflight["binary"]["path"]))
        e2e.PIPE = Path(str(preflight["binary"]["path"]))
        try:
            case = stage1.run_stage1_case(config, identity, case_root, "local", conditions_to_run=BASE_CONDITIONS)
        finally:
            stage1.PIPE, e2e.PIPE = old_stage1_pipe, old_e2e_pipe
        _write_json(case_root / "case_manifest.json", case)
        production_started = _has_production_execution(case)
        manifest["status_labels"]["EXECUTED"] = production_started
        manifest["base_stage1_status"] = case.get("status")
        manifest["stage2_area_clutter_audit"] = _area_clutter_audit(case)
        manifest["known_correction_physical_closure"] = case.get("known_correction_physical_closure")
        manifest["base_processing_chain_comparability"] = r12._operator_comparability(case)
        if case.get("status") != "completed":
            raise RuntimeError(f"A0/B/Known Stage1 or Production did not complete: {case.get('status')}")
        case["production_binary"] = preflight["binary"]
        if manifest["stage2_area_clutter_audit"].get("status") != "passed":
            raise RuntimeError("continuous-area source injection or paired background reuse was not verified")
        if case.get("known_correction_physical_closure", {}).get("status") != "passed":
            raise RuntimeError("Known correction did not close to the A0 physical input")
        if manifest["base_processing_chain_comparability"].get("status") != "passed":
            raise RuntimeError("A0/B/Known Production operators are not comparable")
        if any(not r12._branch_passed(branch) for condition in BASE_CONDITIONS
               for branch in r12._condition_branches(case, condition).values()):
            raise RuntimeError("A0/B/Known Production branch evidence gate failed")
        manifest["progress"].append({"stage": "base_CUDA_smoke_and_frozen_conditions", "status": "passed"})
        _write_json(output_root / "manifest.json", manifest)

        b_on = r12._raw_period_paths(case, "A1_Current_unknown_error", "ON")
        b_branch = r12._condition_branches(case, "A1_Current_unknown_error")["ON"]
        blind_estimate = r12._blind_m1_from_b_on(b_on, Path(str(b_branch["production_xml"])))
        qualified = qualify_blind_methods(blind_estimate)
        manifest["blind_estimator"] = {
            **blind_estimate,
            "input_files": [_file_record(path) for path in b_on],
            "runtime_xml": _file_record(Path(str(b_branch["production_xml"]))),
            "qualification": qualified,
            "evaluator_only_truth_ns": 4.0,
        }
        if qualified["m1"]["status"] != "AVAILABLE":
            raise RuntimeError(f"M1 blind estimate is not available: {qualified['m1']}")
        _write_json(output_root / "manifest.json", manifest)

        corrections = {
            "R21_D3_R9_Fixed": (FIXED_D3_NS, "R9_frozen_calibration", None),
        }
        if qualified["d3_huber"]["status"] == "AVAILABLE":
            corrections["R21_D3_Huber_B_ON"] = (
                float(qualified["d3_huber"]["correction_ns"]),
                METHOD_CONDITIONS["R21_D3_Huber_B_ON"]["correction_source"],
                {"estimator": "D3_Huber_weighted_LS", "record": qualified["d3_huber"]["source_record"]},
            )
        else:
            case.setdefault("conditions", {})["R21_D3_Huber_B_ON"] = {
                "status": "NOT_AVAILABLE", "reason": "B/ON raw C1/C2 Huber estimate did not pass the frozen input/support gate",
                "truth_used_in_estimator": False,
            }
        corrections["R21_M1_Physics_Matched"] = (
            float(qualified["m1"]["correction_ns"]),
            METHOD_CONDITIONS["R21_M1_Physics_Matched"]["correction_source"],
            {"estimator": "M1_Physics_Matched_Delay", "record": qualified["m1"]["source_record"]},
        )
        for condition, (delay_ns, source, estimator_record) in corrections.items():
            _run_corrected_method(case_root, case, condition, delay_ns, source, estimator_record, Path(str(preflight["binary"]["path"])))
            manifest["progress"].append({"stage": condition, "status": "all_three_roles_passed", "applied_delay_ns": delay_ns})
            _write_json(case_root / "case_manifest.json", case)
            _write_json(output_root / "manifest.json", manifest)

        comparability = r12._operator_comparability(case)
        if comparability.get("status") != "passed":
            raise RuntimeError("A0/B/Known/D3/Huber/M1 Production operator configurations differ")
        case["all_condition_processing_chain_comparability"] = comparability
        closures = _input_closure_rows(case)
        case["corrected_input_closure_to_A0"] = closures
        _write_json(case_root / "case_manifest.json", case)

        bundles = _build_bundles(case_root, case)
        evaluation = _evaluate_case(case_root, case, bundles)
        output = output_root / "evaluation"
        _write_csv(output / "r21_task_metrics.csv", evaluation["task_metrics"])
        _write_csv(output / "r21_task_metrics_by_condition.csv", evaluation["task_metrics_by_condition"])
        _write_csv(output / "r21_truth_opportunity_audit.csv", evaluation["truth_opportunity_audit"])
        _write_csv(output / "r21_doppler_opportunity_audit.csv", evaluation["doppler_opportunity_audit"])
        _write_csv(output / "r21_track_semantics.csv", evaluation["track_semantics"])
        _write_csv(output / "r21_parameter_records.csv", evaluation["parameter_records"])
        _write_csv(output / "r21_h0_descriptive_metrics.csv", evaluation["h0_descriptive_metrics"])
        _write_csv(output / "r21_corrected_input_closure.csv", closures)
        _write_json(output / "r21_condition_auxiliary_metrics.json", evaluation["condition_auxiliary"])

        invalid = sum(row.get("status") == "NOT_EVALUABLE" for row in evaluation["truth_opportunity_audit"])
        blank_evaluable = sum(
            row.get("status") in {"EVALUABLE", "EVALUABLE_WITH_EXCLUDED_OPPORTUNITIES"}
            and row.get("hits") is None
            for row in evaluation["task_metrics"]
        )
        evaluated_conditions = [row for row in evaluation["task_metrics_by_condition"] if row["role"] == "ON"]
        all_mapping_passed = all(row.get("status") == "EVALUABLE" for row in evaluated_conditions)
        manifest["evaluation_summary"] = {
            "r21_evaluator_version": "r21-task-eval-v3",
            "condition_count": len(case.get("conditions", {})),
            "task_metric_rows": len(evaluation["task_metrics"]),
            "truth_opportunity_rows": len(evaluation["truth_opportunity_audit"]),
            "truth_opportunities_not_evaluable": invalid,
            "evaluable_task_rows_with_missing_hits": blank_evaluable,
            "all_ON_task_conditions_evaluable": all_mapping_passed,
            "statistical_pd_pfa_claim": False,
        }
        manifest["input_closure_to_A0"] = closures
        manifest["all_condition_processing_chain_comparability"] = comparability
        manifest["source_identity_after"] = r3_runner._source_identity()
        manifest["gpu_status_after"] = stage1._gpu_status()
        manifest["disk_status_after"] = stage1._disk_status(output_root)
        manifest["status_labels"] = {
            "IMPLEMENTED": True,
            "EXECUTED": True,
            "EVALUABLE": bool(all_mapping_passed and invalid == 0 and blank_evaluable == 0),
            "PERFORMANCE_VERIFIED": False,
            "note": "two independent seeds are feasibility evidence only; no statistical Pd/Pfa claim",
        }
        manifest["status"] = "completed" if manifest["status_labels"]["EVALUABLE"] else "completed_with_evaluation_gaps"
        _write_json(case_root / "case_manifest.json", case)
        _write_json(output_root / "manifest.json", manifest)
        return manifest
    except Exception as exc:
        manifest["status"] = "failed_or_incomplete"
        manifest["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        if case is not None:
            manifest["status_labels"]["EXECUTED"] = _has_production_execution(case)
            manifest["status_labels"]["EVALUABLE"] = False
            manifest["status_labels"]["PERFORMANCE_VERIFIED"] = False
        manifest["source_identity_after"] = r3_runner._source_identity()
        manifest["gpu_status_after"] = stage1._gpu_status()
        manifest["disk_status_after"] = stage1._disk_status(output_root)
        _write_json(output_root / "manifest.json", manifest)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--seed", type=int, choices=SEEDS, required=True)
    parser.add_argument("--production-binary", type=Path, default=ROOT / "build/GMTI_pipe_core")
    args = parser.parse_args(argv)
    try:
        manifest = run_seed(args.output_root, args.seed, args.production_binary)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"R21 seed {args.seed} failed: {exc}\n")
    print(json.dumps({
        "status": manifest.get("status"), "seed": args.seed,
        "output_root": adapter.audit_path(args.output_root.resolve()),
        "status_labels": manifest.get("status_labels"),
    }, ensure_ascii=False, indent=2))
    return 0 if manifest.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
