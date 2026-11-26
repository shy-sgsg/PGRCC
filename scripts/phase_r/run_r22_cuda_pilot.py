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
import time
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
V4_SIZE_PILOT_DESIGN_ID = "R22_V4_OUTPUT_SIZE_PILOT_DRAFT_20261123"
V4_SIZE_PILOT_APPROVED_SHA256 = "f9de67eab44c10e7f076e3852f76a216991fba29dbb7a9e1113f1021e4559954"
V4_THREE_SCHEMA_SIZE_PILOT_DESIGN_ID = "R22_V4_THREE_SCHEMA_SIZE_PILOT_20261126"
V4_THREE_SCHEMA_SIZE_PILOT_APPROVED_SHA256 = "5fba267143196125f6fe7e4281b95a070611d283d9b0d5313127777bbe5c243a"
R22_XML_OVERRIDES = {
    "csi_metrics_enable": "true",
    "csi_metrics_dump_power_maps": "false",
    "csi_metrics_dump_intermediate_maps": "false",
}


def _r22_xml_overrides(*, v4_output_complete: bool = False) -> dict[str, str]:
    overrides = dict(R22_XML_OVERRIDES)
    if v4_output_complete:
        overrides["csi_metrics_dump_power_maps"] = "true"
        overrides["csi_metrics_dump_intermediate_maps"] = "true"
    return overrides


def _apply_v4_output_capture(
    method_config: Mapping[str, Any], *, v4_output_complete: bool,
) -> dict[str, Any]:
    if not v4_output_complete:
        return {}
    production = method_config.get("production")
    if not isinstance(production, dict):
        raise ValueError("V4 complete-output capture requires a mutable production config")
    production["csi_metrics_dump_power_maps"] = True
    production["csi_metrics_dump_intermediate_maps"] = True
    return {
        "csi_metrics_dump_power_maps": True,
        "csi_metrics_dump_intermediate_maps": True,
        "scope": "v4_output_complete_diagnostic_capture_only",
        "algorithm_parameters_changed": False,
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


def _cuda_driver_identity() -> dict[str, Any]:
    command = ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader,nounits"]
    try:
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=15.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"CUDA driver identity query failed: {exc}") from exc
    versions = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not versions:
        raise RuntimeError(
            "CUDA driver identity query returned no driver version "
            f"(returncode={result.returncode}, stderr={result.stderr.strip()})"
        )
    return {
        "command": command,
        "returncode": int(result.returncode),
        "driver_versions": versions,
        "stderr": result.stderr.strip(),
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
        Path(__file__).with_name("r22_v4_evidence.py"),
        Path(__file__).with_name("r22_compact_archive.py"),
        Path(__file__).with_name("r22_experiment_matrix.py"),
        ROOT / "scripts/run_delay_stage1_formal.py",
        ROOT / "scripts/run_production_hierarchical_calibration_pilot.py",
        ROOT / "scripts/run_track_manager_e2e.py",
        ROOT / "scripts/phase_r/run_r12_physics_matched_delay.py",
        ROOT / "scripts/phase_r/run_r21_cuda_pilot.py",
        ROOT / "scripts/phase_r/physics_matched_delay.py",
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
    *,
    allow_empty: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config_path = Path(config_path).expanduser().resolve()
    method_config = calibration.load_pilot_config(config_path)
    method_config["scenario_template"] = str(Path(scenario_path).expanduser().resolve())
    if not method_ids and allow_empty:
        method_contract: list[dict[str, Any]] = []
    else:
        method_contract = calibration.build_method_contract(method_config, method_ids=method_ids)
    return method_config, method_contract


def _v4_calibration_method_ids(delay_ns: float) -> tuple[str, ...]:
    """Methods the V4 draft actually sends through the shared calibration runner."""
    if float(delay_ns) == 0.0:
        return ()
    if float(delay_ns) == 4.0:
        # C0 supplies the uncorrected current baseline and runtime XML used by
        # the causal estimators; PK is the evaluator-only known-error upper bound.
        return ("C0", "PK")
    raise ValueError(f"V4 draft has no registered delay condition for {delay_ns} ns")


def _annotate_v4_branch_classes(
    branch_records: Sequence[Mapping[str, Any]], *, phase: str, delay_ns: float,
) -> list[dict[str, Any]]:
    """Mark registered scientific branches separately from runner support calls."""
    runner_to_v4 = {
        "A0": "A0", "C0": "C0", "PK": "PK_KNOWN",
        "R22_R9_Fixed_D3": "FIXED_D3",
        "HUBER_D3_CAUSAL_PREFIX": "HUBER_D3_CAUSAL_PREFIX",
        "M1_CAUSAL_PREFIX": "M1_CAUSAL_PREFIX",
    }
    registered_methods = (
        {"A0"} if float(delay_ns) == 0.0 else {
            "C0", "FIXED_D3", "HUBER_D3_CAUSAL_PREFIX", "M1_CAUSAL_PREFIX", "PK_KNOWN",
        }
    )
    registered_roles = {"ON", "TO"} if phase != "h0" else {"ON"}
    annotated: list[dict[str, Any]] = []
    for branch in branch_records:
        record = dict(branch)
        role = str(record.get("role") or "")
        canonical = runner_to_v4.get(str(record.get("method_id") or ""))
        is_registered = canonical in registered_methods and role in registered_roles
        record["v4_registered_method_id"] = canonical if is_registered else None
        record["v4_execution_class"] = (
            "registered_scientific_branch" if is_registered
            else "internal_auxiliary_branch" if role in {"ON", "OFF", "TO"}
            else "non_branch_diagnostic"
        )
        annotated.append(record)
    return annotated


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
    *,
    case_root: Path | None = None,
    case_id: str | None = None,
    method_config_identity: Mapping[str, Any] | None = None,
    default_source_xml: Path | None = None,
) -> list[dict[str, Any]]:
    audited: list[dict[str, Any]] = []
    for branch in branch_records:
        record = dict(branch)
        if str(record.get("role", "")) != "ALL":
            record["production_path_gate"] = stage1.audit_production_path_evidence(record)
        if case_root is not None and case_id is not None and str(record.get("role", "")) in {"ON", "OFF", "TO"}:
            from scripts.phase_r.r22_v4_identity import normalize_mode

            root = Path(case_root).expanduser().resolve()
            branch_name = str(record.get("branch_name") or "")
            if not branch_name:
                raise ValueError("V4 Production result lacks branch_name")
            record["case_id"] = case_id
            record["method_id"] = str(record.get("method_id") or "").strip()
            if not record["method_id"]:
                raise ValueError(f"V4 Production branch lacks explicit method_id: {branch_name}")
            # Keep method_id and condition as separate identity fields. The
            # calibration runner has no condition column on its base branches,
            # so those branches use the method label as their condition value.
            record["condition"] = str(record.get("condition") or record["method_id"])
            record["mode"] = normalize_mode(record.get("mode"))
            branch_root = root / "production" / branch_name
            xml_path = Path(str(record.get("production_xml") or branch_root / "production.xml")).resolve()
            source_xml_value = record.get("source_xml") or default_source_xml
            source_xml_path = Path(str(source_xml_value or "")).expanduser().resolve()
            overrides = record.get("xml_overrides")
            if not xml_path.is_file() or not source_xml_path.is_file():
                raise FileNotFoundError(f"V4 branch XML identity input is missing: {branch_name}")
            try:
                source_xml_relative = source_xml_path.relative_to(root).as_posix()
                xml_relative = xml_path.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError(f"V4 branch XML identity input is outside case root: {branch_name}") from exc
            if not isinstance(overrides, Mapping) or not overrides:
                raise ValueError(f"V4 branch XML overrides are missing: {branch_name}")
            if not isinstance(method_config_identity, Mapping):
                raise ValueError("V4 method configuration identity is missing")
            xml_identity = {
                "path": xml_relative,
                "bytes": xml_path.stat().st_size,
                "sha256": sha256_file(xml_path),
            }
            source_configuration_identity = {
                "method_config": dict(method_config_identity),
                "source_xml": {
                    "path": source_xml_relative,
                    "bytes": source_xml_path.stat().st_size,
                    "sha256": sha256_file(source_xml_path),
                },
                "branch_overrides_sha256": hashlib.sha256(json.dumps(
                    dict(overrides), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                ).encode("utf-8")).hexdigest(),
            }
            record["runtime_xml_identity"] = xml_identity
            record["source_configuration_identity"] = source_configuration_identity
            write_json(branch_root / "branch_manifest.json", {
                **record, "branch_name": branch_name, "production_xml": "production.xml",
                "xml_overrides": dict(overrides),
            })
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


def _method_ids_for_delay(method_ids: Sequence[str], delay_ns: float) -> tuple[str, ...]:
    """Do not run physical blind corrections on the no-error reference case.

    Blind estimates are recorded separately as estimate-only diagnostics at
    zero delay. The Stage2 inverse operator is defined for an actually delayed,
    zero-extended input; it must not be applied to a no-error packet.
    """

    requested = tuple(str(method_id) for method_id in method_ids)
    if float(delay_ns) != 0.0:
        return requested
    return tuple(method_id for method_id in requested if method_id not in {"P1", "P2"})


def _not_applicable_correction_record(
    method_id: str,
    *,
    reason: str,
    correction_source: str,
    estimate_record: Mapping[str, Any] | None = None,
    correction_ns: float | None = None,
) -> dict[str, Any]:
    return {
        "method_id": method_id,
        "mode": "not_applicable",
        "role": "ALL",
        "status": "NOT_APPLICABLE_ZERO_DELAY_REFERENCE",
        "reason": reason,
        "correction_source": correction_source,
        "correction_ns": correction_ns,
        "correction_applied": False,
        "truth_used_in_estimator": False,
        "truth_used_to_apply_correction": False,
        "estimate_record": dict(estimate_record or {}),
    }


def _extra_delay_correction_plan(
    *,
    delay_ns: float,
    qualified: Mapping[str, Any],
    estimate_record: Mapping[str, Any],
    zero_error_target_free_estimate: Mapping[str, Any] | None = None,
) -> tuple[list[tuple[str, float, str, Mapping[str, Any] | None]], list[dict[str, Any]]]:
    """Build correction branches without applying a nonzero inverse at 0 ns."""

    if float(delay_ns) == 0.0:
        reason = "zero_delay_reference_has_no_physical_channel_error_to_correct"
        records = [
            _not_applicable_correction_record(
                "R22_R9_Fixed_D3", reason=reason,
                correction_source="R9_frozen_calibration",
                correction_ns=r21.FIXED_D3_NS,
            ),
            _not_applicable_correction_record(
                "R22_D3_Huber_B_ON", reason=reason,
                correction_source="same_seed_B_ON_raw_C1_C2_Huber_fit",
                estimate_record=estimate_record,
                correction_ns=(qualified.get("d3_huber", {}).get("correction_ns")
                               if isinstance(qualified.get("d3_huber"), Mapping) else None),
            ),
            _not_applicable_correction_record(
                "R22_M1_Physics_Matched", reason=reason,
                correction_source="same_seed_B_ON_raw_C1_C2_M1_fit",
                estimate_record=estimate_record,
                correction_ns=(qualified.get("m1", {}).get("correction_ns")
                               if isinstance(qualified.get("m1"), Mapping) else None),
            ),
            _not_applicable_correction_record(
                "P1", reason=reason,
                correction_source="target_free_OFF_phase_vs_fast_frequency",
                estimate_record=zero_error_target_free_estimate,
                correction_ns=(zero_error_target_free_estimate.get("selected_delay_ns")
                               if isinstance(zero_error_target_free_estimate, Mapping) else None),
            ),
            _not_applicable_correction_record(
                "P2", reason=reason,
                correction_source="target_free_OFF_phase_vs_fast_frequency",
                estimate_record=zero_error_target_free_estimate,
                correction_ns=(zero_error_target_free_estimate.get("selected_delay_ns")
                               if isinstance(zero_error_target_free_estimate, Mapping) else None),
            ),
        ]
        return [], records

    correction_specs: list[tuple[str, float, str, Mapping[str, Any] | None]] = [
        ("R22_R9_Fixed_D3", r21.FIXED_D3_NS, "R9_frozen_calibration", None),
    ]
    not_applicable: list[dict[str, Any]] = []
    for key, method_id, correction_source in (
        ("d3_huber", "R22_D3_Huber_B_ON", "same_seed_B_ON_raw_C1_C2_Huber_fit"),
        ("m1", "R22_M1_Physics_Matched", "same_seed_B_ON_raw_C1_C2_M1_fit"),
    ):
        record = qualified.get(key, {})
        if isinstance(record, Mapping) and record.get("status") == "AVAILABLE":
            correction_specs.append((
                method_id, float(record["correction_ns"]), correction_source, record,
            ))
        else:
            not_applicable.append({
                "method_id": method_id,
                "mode": "not_applicable",
                "role": "ALL",
                "status": "NOT_AVAILABLE",
                "reason": (record.get("reason", "support_gate_failed")
                           if isinstance(record, Mapping) else "support_gate_failed"),
                "truth_used_in_estimator": False,
            })
    return correction_specs, not_applicable


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


def _causal_prefix_estimates(
    raw_paths: Sequence[Path], runtime_xml: Path,
) -> dict[str, list[dict[str, Any]]]:
    """Estimate M1 and Huber from ON raw C1/C2 prefixes ending at each period."""
    paths = [Path(path).expanduser().resolve() for path in raw_paths]
    if not paths:
        raise FileNotFoundError("causal estimator requires ordered ON period inputs")
    if len(set(paths)) != len(paths):
        raise ValueError("causal estimator period inputs must be unique and ordered")

    identities: list[dict[str, Any]] = []
    records: dict[str, list[dict[str, Any]]] = {
        "M1_CAUSAL_PREFIX": [],
        "HUBER_D3_CAUSAL_PREFIX": [],
    }
    qualified_keys = {
        "M1_CAUSAL_PREFIX": "m1",
        "HUBER_D3_CAUSAL_PREFIX": "d3_huber",
    }
    for period_id in range(len(paths)):
        current_path = paths[period_id]
        if not current_path.is_file():
            raise FileNotFoundError(f"causal estimator period input is missing: {current_path}")
        identities.append({"period_id": period_id, **file_identity(current_path)})
        prefix_paths = paths[:period_id + 1]
        prefix_ids = list(range(period_id + 1))
        prefix_identities = identities[:period_id + 1]
        estimate_started = time.perf_counter()
        try:
            estimate = r12._blind_m1_from_b_on(prefix_paths, Path(runtime_xml))
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            estimate = {
                "status": "NOT_AVAILABLE",
                "reason": f"prefix_estimator_failed:{type(exc).__name__}:{exc}",
                "input_role": "A1_Current_unknown_error_ON_raw_C1_C2_only",
                "truth_used_in_estimator": False,
            }
            qualification = {
                key: {"status": "NOT_AVAILABLE", "correction_ns": None,
                      "reason": estimate["reason"]}
                for key in ("m1", "d3_huber")
            }
        else:
            # Qualification errors (wrong input role or truth use) are contract
            # violations and deliberately propagate instead of becoming fallback.
            qualification = r21.qualify_blind_methods(estimate)
        estimator_elapsed_sec = time.perf_counter() - estimate_started

        for method_id, qualification_key in qualified_keys.items():
            method_qualification = qualification.get(qualification_key, {})
            try:
                correction_ns = float(method_qualification.get("correction_ns"))
            except (TypeError, ValueError):
                correction_ns = math.nan
            available = (
                method_qualification.get("status") == "AVAILABLE"
                and math.isfinite(correction_ns)
            )
            records[method_id].append({
                "method_id": method_id,
                "period_id": period_id,
                "prefix_period_ids": prefix_ids,
                "prefix_input_identities": prefix_identities,
                "estimator_input_role": "A1_Current_unknown_error_ON_raw_C1_C2_only",
                "estimator_status": method_qualification.get("status", "NOT_AVAILABLE"),
                "estimator_elapsed_sec": estimator_elapsed_sec,
                "estimate_ns": correction_ns if available else None,
                "applied_correction_ns": correction_ns if available else 0.0,
                "fallback_status": None if available else "ESTIMATOR_UNAVAILABLE_ZERO_CORRECTION",
                "fallback_reason": method_qualification.get("reason") or estimate.get("reason"),
                "qualification": dict(method_qualification),
                "estimate_record": dict(estimate),
                "truth_used_in_estimator": estimate.get("truth_used_in_estimator") is True,
            })
    return records


def _delay_corrected_inputs_by_period(
    case_root: Path,
    condition: str,
    role: str,
    source_paths: Sequence[Path],
    period_estimates: Sequence[Mapping[str, Any]],
    layout: Mapping[str, Any],
) -> tuple[list[Path], list[dict[str, Any]]]:
    if len(source_paths) != len(period_estimates) or not source_paths:
        raise ValueError("period-wise corrections must match the ordered production inputs")
    output_dir = case_root / "inputs" / "r22_v4_corrections" / condition / role
    output_dir.mkdir(parents=True, exist_ok=True)
    corrected: list[Path] = []
    audits: list[dict[str, Any]] = []
    for period_id, (source, estimate) in enumerate(zip(source_paths, period_estimates)):
        if int(estimate.get("period_id", -1)) != period_id:
            raise ValueError("period-wise correction identity is out of order")
        try:
            correction_ns = float(estimate["applied_correction_ns"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"period {period_id} lacks a finite correction policy") from exc
        if not math.isfinite(correction_ns):
            raise ValueError(f"period {period_id} correction is not finite")
        destination = output_dir / f"period_{period_id:04d}.bin"
        if destination.exists():
            raise FileExistsError(f"refusing to overwrite V4 correction input: {destination}")
        audit = stage1.rewrite_float32_protocol_delay(
            Path(source), destination,
            pulse_len=int(layout["pulse_len"]),
            channel_count=int(layout["channel_count"]),
            fs_hz=float(layout["fs_hz"]),
            delta_tau_sec=correction_ns * 1.0e-9,
            channel_indices=tuple(int(value) for value in layout["correction_channel_indices"]),
            simulator_linear_fractional_delay_inverse=True,
        )
        corrected.append(destination)
        audits.append({
            "period_id": period_id,
            "source_path": str(Path(source).resolve()),
            "destination_path": str(destination.resolve()),
            "estimator_status": estimate.get("estimator_status"),
            "estimate_ns": estimate.get("estimate_ns"),
            "applied_correction_ns": correction_ns,
            "fallback_status": estimate.get("fallback_status"),
            "operator_audit": audit,
        })
    return corrected, audits


def _run_causal_prefix_delay_method(
    case_root: Path,
    *,
    case_id: str,
    method_id: str,
    period_estimates: Sequence[Mapping[str, Any]],
    raw_roles: Mapping[str, Sequence[Path]],
    source_xml: Path,
    truth_root: Path,
    layout: Mapping[str, Any],
    production_config: Mapping[str, Any],
    v4_output_complete: bool = False,
) -> list[dict[str, Any]]:
    period_count = len(period_estimates)
    if period_count == 0 or any(len(raw_roles.get(role, ())) != period_count
                                for role in ("ON", "OFF", "TO")):
        raise ValueError("causal Production roles must share the estimator period count")
    branch_records: list[dict[str, Any]] = []
    for role in ("ON", "OFF", "TO"):
        corrected, audits = _delay_corrected_inputs_by_period(
            case_root, method_id, role, raw_roles[role], period_estimates, layout,
        )
        input_dir = case_root / "inputs" / "production" / method_id / role
        input_dir.mkdir(parents=True, exist_ok=True)
        for period_id, source in enumerate(corrected):
            link = input_dir / f"period_{period_id:04d}.bin"
            if link.exists() or link.is_symlink():
                raise FileExistsError(f"refusing to overwrite V4 Production input link: {link}")
            link.symlink_to(source.resolve())
        branch_name = f"{method_id}_{role}"
        branch = stage1.run_production_branch(
            case_root, branch_name, input_dir, source_xml, {}, period_count,
            "local", layout, truth_path=truth_root,
            xml_overrides={
                **_r22_xml_overrides(v4_output_complete=v4_output_complete),
                "csi_metrics_beam_id": int(layout["beam_id"]),
                "new_protocol_file_first_beam": int(layout["beam_id"]),
                "new_protocol_file_scan_beam_count": int(layout.get("scan_beam_count", 1)),
                "shm_scan_beam_count": int(layout.get("scan_beam_count", 1)),
                "wavepos_st": int(layout["beam_id"]),
                "wavepos_ed": int(layout.get("last_beam_id", layout["beam_id"])),
                "wavepos_skip": 1,
                **calibration._production_range_overrides(production_config),
            },
        )
        gate = stage1.audit_production_path_evidence(branch)
        branch.update({
            "method_id": method_id,
            "condition": method_id,
            "mode": "not_applicable",
            "role": role,
            "case_id": case_id,
            "correction_source": "same_case_ON_raw_C1_C2_causal_prefix",
            "applied_delay_ns": None,
            "period_corrections": audits,
            "period_count_expected": period_count,
            "continuous_track_manager_run": True,
            "estimator_method_id": method_id,
            "estimator_input_role": "A1_Current_unknown_error_ON_raw_C1_C2_only",
            "truth_used_in_estimator": False,
            "truth_used_to_apply_correction": False,
            "correction_operator": "estimate_channel_delay.rewrite_float32_protocol_delay",
            "input_role": "same_seed_A1_Current_unknown_error_raw_role",
            "production_path_gate": gate,
        })
        stage1._write_json(case_root / "production" / branch_name / "branch_manifest.json", branch)
        branch_records.append(branch)
        if gate.get("status") != "passed":
            break
    return branch_records


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
    production_config: Mapping[str, Any] | None = None,
    v4_output_complete: bool = False,
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
                **_r22_xml_overrides(v4_output_complete=v4_output_complete),
                "csi_metrics_beam_id": int(layout["beam_id"]),
                "new_protocol_file_first_beam": int(layout["beam_id"]),
                "new_protocol_file_scan_beam_count": int(layout.get("scan_beam_count", 1)),
                "shm_scan_beam_count": int(layout.get("scan_beam_count", 1)),
                "wavepos_st": int(layout["beam_id"]),
                "wavepos_ed": int(layout.get("last_beam_id", layout["beam_id"])),
                "wavepos_skip": 1,
                **calibration._production_range_overrides(production_config or {}),
            },
        )
        gate = stage1.audit_production_path_evidence(branch)
        branch.update({
            "method_id": condition,
            "condition": condition,
            "mode": "not_applicable",
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
    period_count: int | None = None,
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
        "h0_task_metrics": [],
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
            if phase == "h0" and role == "ON":
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
                tables["h0_task_metrics"].append({
                    **row_base,
                    "status": "NOT_EVALUABLE",
                    "reason": record.get("reason", "production_branch_not_passed"),
                    "empirical_pfa_status": "NOT_EVALUABLE",
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
            if role == "ON":
                tables["h0_task_metrics"].append({
                    **row_base,
                    **_h0_task_metric_row(bundle, expected_period_count=period_count),
                })
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
    """Use registered H0 ON as the independent seed; retain paired OFF as diagnostic."""
    if role not in {"ON", "OFF"}:
        return []
    if phase == "h0" and role == "OFF":
        return [{
            **item,
            "h0_scope": "internal_auxiliary_OFF_not_registered_h0",
            "empirical_pfa_status": "NOT_EVALUABLE_INTERNAL_AUXILIARY_BRANCH",
        } for item in r20._off_h0_rows(bundle)]
    if role != "OFF" and phase != "h0":
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


def _h0_task_metric_row(
    bundle: Mapping[str, Any], *, expected_period_count: int | None,
) -> dict[str, Any]:
    """Evaluate one target-free Production ON branch from its saved outputs."""
    base = {
        "case_id": bundle.get("case_id"), "seed": bundle.get("seed"),
        "condition": bundle.get("canonical_condition", bundle.get("condition")),
        "role": bundle.get("role"),
        "h0_scope": "independent_target_free_seed",
        "empirical_pfa_status": "NOT_EVALUABLE_NO_INDEPENDENT_H0_SEED_AGGREGATION",
    }

    def not_evaluable(reason: str) -> dict[str, Any]:
        return {
            **base, "status": "NOT_EVALUABLE", "reason": reason,
            "track_status": "NOT_EVALUABLE_PRIMARY_ENDPOINT",
            "track_reason": reason,
            "valid_period_count": None, "detection_count": None,
            "cluster_count": None, "false_confirmed_track_count": None,
            "track_output_complete": False,
            "h0_event_count": None, "h0_event_rate": None,
            "valid_cut_count": None, "observed_cut_hit_count": None,
            "observed_cut_hit_fraction": None,
        }

    if bundle.get("role") != "ON":
        return not_evaluable("registered_h0_role_must_be_ON")
    if expected_period_count is None or expected_period_count <= 0:
        return not_evaluable("expected_period_count_missing_or_invalid")

    branch_dir = Path(str(bundle.get("branch_dir", "")))
    cfar_path = branch_dir / "result/debug/cfar_geometry_diagnostics.csv"
    if not cfar_path.is_file():
        return not_evaluable("missing_cfar_geometry_diagnostics")
    cfar_rows = r20.read_csv(cfar_path)
    full_band_rows = [row for row in cfar_rows if row.get("branch") == "union_full_band"]
    period_geometry: dict[tuple[int, int], tuple[int, int]] = {}
    for row in full_band_rows:
        period, beam = r20._int(row.get("period_id")), r20._int(row.get("beam_id"))
        valid, hits = r20._int(row.get("valid_cut_count")), r20._int(row.get("hit_cut_count"))
        if None in (period, beam, valid, hits) or valid <= 0 or hits < 0 or hits > valid:
            return not_evaluable("invalid_cfar_geometry_counter")
        key = (period, beam)
        if key in period_geometry:
            return not_evaluable("duplicate_cfar_period_beam_geometry")
        period_geometry[key] = (valid, hits)
    expected_periods = set(range(expected_period_count))
    observed_periods = {period for period, _ in period_geometry}
    if observed_periods != expected_periods:
        return not_evaluable("period_geometry_incomplete")

    detection_bundle = bundle.get("detections")
    if not isinstance(detection_bundle, Mapping):
        return not_evaluable("final_detection_bundle_missing")
    if detection_bundle.get("source_status") != "PER_RESULT_PRODUCTION_CSV":
        return not_evaluable("final_per_period_detection_csv_missing")
    result_period_map = detection_bundle.get("result_period_map")
    detection_files = detection_bundle.get("files")
    empty_result_numbers = detection_bundle.get("empty_result_numbers", [])
    if not isinstance(result_period_map, Mapping) or not isinstance(empty_result_numbers, Sequence):
        return not_evaluable("final_detection_period_mapping_incomplete")
    mapped_periods = [r20._int(value) for value in result_period_map.values()]
    mapped_result_numbers = {r20._int(value) for value in result_period_map.keys()}
    empty_numbers = [r20._int(value) for value in empty_result_numbers]
    if (any(value is None for value in mapped_periods + list(mapped_result_numbers) + empty_numbers)
            or len(mapped_periods) != len(set(mapped_periods))
            or set(mapped_periods) - expected_periods
            or len(empty_numbers) != len(set(empty_numbers))
            or set(mapped_result_numbers) & set(empty_numbers)
            or len(result_period_map) + len(empty_numbers) != expected_period_count
            or not isinstance(detection_files, Sequence)
            or len(detection_files) != expected_period_count):
        return not_evaluable("final_detection_period_mapping_incomplete")
    detections = detection_bundle.get("rows")
    if not isinstance(detections, Sequence):
        return not_evaluable("final_detection_rows_missing")
    detection_keys: set[tuple[int, int, str]] = set()
    detection_periods: set[int] = set()
    for detection in detections:
        period = r20._int(detection.get("period_id"))
        beam = r20._int(detection.get("beam_id"))
        det_id = str(detection.get("det_id", "")).strip()
        if period not in expected_periods or beam is None or not det_id:
            return not_evaluable("invalid_final_detection_identity")
        key = (period, beam, det_id)
        if key in detection_keys:
            return not_evaluable("duplicate_final_detection_identity")
        detection_keys.add(key)
        detection_periods.add(period)

    lineage_path = next((candidate for candidate in (
        branch_dir / "result/debug/cluster_detection_lineage.csv",
        branch_dir / "result/cluster_detection_lineage.csv",
    ) if candidate.is_file()), None)
    if lineage_path is None:
        return not_evaluable("cluster_detection_lineage_missing")
    lineage_rows = r20.read_csv(lineage_path)
    from scripts.phase_r import evaluate_r7_task_metrics as r7_metrics

    cluster_summary = r7_metrics._cluster_summary(lineage_rows, cfar_rows, detections)
    if (cluster_summary.get("status") != "complete"
            or cluster_summary.get("final_detections_joined") != len(detections)):
        return not_evaluable("cluster_final_detection_join_incomplete")

    track = bundle.get("track")
    track_status = "EVALUABLE"
    track_reason: str | None = None
    false_tracks: int | None = None
    track_source: str | None = None
    if isinstance(track, Mapping) and track.get("status") == "evaluable":
        track_summary = r21_eval.summarize_track_lineage_v3(
            observations=track.get("observations", []),
            target_matched_detection_keys=set(),
            visible_truth_periods=[],
            payload_duplicate_key_count=int(track.get("payload_duplicate_key_count", 0)),
        )
        if track_summary.get("status") != "EVALUABLE":
            track_status = "NOT_EVALUABLE_INVALID_OUTPUT"
            track_reason = "track_manager_lineage_not_evaluable"
        else:
            false_tracks = int(track_summary.get("false_confirmed_track_count", 0))
            track_source = "TrackManager debug confirmed matched outputs"
    else:
        # Empty, header-valid TrackManager outputs are a real zero event. Missing files,
        # blank files, or non-empty data that the adapter cannot map remain not evaluable.
        track_root = branch_dir / "track_debug_runs"
        run_dirs = sorted(path for path in track_root.iterdir() if path.is_dir()) if track_root.is_dir() else []
        if len(run_dirs) != 1:
            track_status = "NOT_EVALUABLE_MISSING_OUTPUT"
            track_reason = "track_manager_debug_not_evaluable"
        else:
            empty_output_files = [run_dirs[0] / name for name in (
                "track_states.csv", "track_events.csv", "track_output_payloads.csv",
            )]
            if any(not path.is_file() or path.stat().st_size == 0 for path in empty_output_files):
                track_status = "NOT_EVALUABLE_MISSING_OUTPUT"
                track_reason = "track_manager_empty_output_evidence_missing"
            else:
                try:
                    headers_valid = True
                    for path in empty_output_files:
                        with path.open("r", newline="", encoding="utf-8-sig") as stream:
                            header = next(csv.reader(stream), None)
                        headers_valid &= bool(header) and all(field.strip() for field in header)
                    track_rows = [r20.read_csv(path) for path in empty_output_files]
                except (OSError, ValueError, csv.Error):
                    headers_valid = False
                    track_rows = []
                if not headers_valid:
                    track_status = "NOT_EVALUABLE_INVALID_OUTPUT"
                    track_reason = "track_manager_empty_output_evidence_invalid"
                elif any(track_rows):
                    track_status = "NOT_EVALUABLE_INVALID_OUTPUT"
                    track_reason = "track_manager_lineage_not_evaluable"
                else:
                    false_tracks = 0
                    track_source = "header-valid empty TrackManager output files"

    valid_cut_count = sum(value[0] for value in period_geometry.values())
    observed_cut_hit_count = sum(value[1] for value in period_geometry.values())
    event_count = len(detection_periods)
    detection_count = len(detections)
    cluster_count = int(cluster_summary.get("postfilter_clusters", 0))
    return {
        **base,
        "status": "EVALUABLE_DESCRIPTIVE_H0_SEED",
        "reason": None,
        "valid_period_count": len(expected_periods),
        "detection_count": detection_count,
        "false_detection_count": detection_count,
        "cluster_count": cluster_count,
        "false_cluster_count": cluster_count,
        "false_confirmed_track_count": false_tracks,
        "track_status": track_status,
        "track_reason": track_reason,
        "track_output_complete": track_status == "EVALUABLE",
        "h0_event_count": event_count,
        "h0_event_rate": event_count / len(expected_periods),
        "valid_cut_count": valid_cut_count,
        "observed_cut_hit_count": observed_cut_hit_count,
        "observed_cut_hit_fraction": (
            observed_cut_hit_count / valid_cut_count if valid_cut_count > 0 else None
        ),
        "event_definition": "at least one final postfilter Detection in a valid Production period",
        "event_denominator_source": "complete per-period CFAR geometry and Detection mapping",
        "cut_fraction_interpretation": "descriptive only; spatially correlated CUTs are not independent Pfa trials",
        "track_source": track_source,
    }


def _write_tables(root: Path, tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    for name, rows in tables.items():
        write_csv(root / "evaluation" / f"{name}.csv", rows)


def _require_v4_size_pilot_authorization(
    authorization: Mapping[str, Any] | None,
    *, phase: str, case_id: str, seed: int, speed_mps: float, snr_db: float,
    delay_ns: float, target_count: int, period_count: int, first_beam: int,
    beam_count: int, method_ids: Sequence[str], calibration_modes: Sequence[str],
    area: Mapping[str, Any], template: Path, method_config_path: Path,
) -> dict[str, Any]:
    if not isinstance(authorization, Mapping):
        raise ValueError("V4 output-complete execution requires matrix size-pilot authorization")
    if authorization.get("design_id") == V4_THREE_SCHEMA_SIZE_PILOT_DESIGN_ID:
        supplied = dict(authorization)
        actual_schema = (
            "target_present_0ns" if target_count > 0 else f"h0_{int(delay_ns)}ns"
        )
        expected = {
            "status": "AUTHORIZED_USER_SIZE_PILOT_CASE",
            "design_id": V4_THREE_SCHEMA_SIZE_PILOT_DESIGN_ID,
            "approved_design_sha256": V4_THREE_SCHEMA_SIZE_PILOT_APPROVED_SHA256,
            "phase": phase,
            "case_id": case_id,
            "schema": actual_schema,
            "production_backend": "Native CUDA",
            "use_corex": False,
            "archive_profile": "r22_v4_output_complete",
            "scope_note": (
                "user-authorized three-schema resource pilot only; "
                "no confirmatory inference or performance claim"
            ),
        }
        for key, value in expected.items():
            if supplied.get(key) != value:
                raise ValueError(f"V4 three-schema size-pilot authorization differs: {key}")
        actual = {
            "phase": phase, "case_id": case_id, "seed": seed,
            "speed_mps": float(speed_mps), "snr_db": float(snr_db),
            "delay_ns": float(delay_ns), "target_count": target_count,
            "period_count": period_count, "first_beam": first_beam,
            "beam_count": beam_count, "method_ids": list(method_ids),
            "calibration_modes": list(calibration_modes), "area": dict(area),
            "scenario_template": str(Path(template).expanduser().resolve()),
            "method_config": str(Path(method_config_path).expanduser().resolve()),
        }
        for key, value in actual.items():
            if supplied.get(key) != value:
                raise ValueError(f"V4 three-schema size-pilot case identity differs: {key}")
        return supplied

    expected = {
        "status": "AUTHORIZED_SINGLE_ENGINEERING_CASE",
        "design_id": V4_SIZE_PILOT_DESIGN_ID,
        "approved_design_sha256": V4_SIZE_PILOT_APPROVED_SHA256,
        "phase": "engineering",
        "case_id": case_identifier("engineering", 1937003, 2.5, 20.0, 4.0, 20, 10),
        "seed": 1937003, "speed_mps": 2.5, "snr_db": 20.0,
        "delay_ns": 4.0, "target_count": 20, "period_count": 10,
        "first_beam": 50, "beam_count": 1,
        "method_ids": ["C0", "PK"],
        "calibration_modes": ["Mode-A", "Mode-B"],
        "area": DEFAULT_AREA,
        "production_backend": "Native CUDA", "use_corex": False,
        "archive_profile": "r22_v4_output_complete",
        "scenario_template": str(DEFAULT_TEMPLATE.resolve()),
        "method_config": str((ROOT / "docs/research/phase_r/R22/config/r22_method_config_v2_range_roi.json").resolve()),
    }
    supplied = dict(authorization)
    for key, value in expected.items():
        if supplied.get(key) != value:
            raise ValueError(f"V4 size-pilot authorization does not match frozen identity: {key}")
    actual = {
        "phase": phase, "case_id": case_id, "seed": seed, "speed_mps": float(speed_mps),
        "snr_db": float(snr_db), "delay_ns": float(delay_ns),
        "target_count": target_count, "period_count": period_count,
        "first_beam": first_beam, "beam_count": beam_count,
        "method_ids": list(method_ids), "calibration_modes": list(calibration_modes),
        "area": dict(area), "scenario_template": str(Path(template).expanduser().resolve()),
        "method_config": str(Path(method_config_path).expanduser().resolve()),
    }
    for key, value in actual.items():
        if supplied.get(key) != value:
            raise ValueError(f"V4 size-pilot request differs from authorized identity: {key}")
    return supplied


def _compact_case_artifacts(
    case_root: Path, *, require_v4_response: bool = False,
) -> dict[str, Any]:
    """Remove regenerable binaries and full CSI maps after scalar evidence exists."""
    if require_v4_response:
        raise RuntimeError(
            "V4 raw cleanup is owned by the matrix after complete archive verification"
        )
    case_root = Path(case_root).expanduser().resolve()
    v4_response_audit: dict[str, Any] | None = None
    if require_v4_response:
        from scripts.phase_r.r22_v4_evidence import validate_target_complex_response

        v4_response_audit = validate_target_complex_response(case_root)
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
        "policy": (
            "verify_per_cell_v4_response_then_remove_dense_csi_maps_and_generated_bin"
            if require_v4_response else
            "remove_generated_bin_and_reproducible_csi_npy_keep_compact_audit_files"
        ),
        "removed_file_count": prior_count + int(cleanup.get("removed_file_count", 0)) + len(numpy_records),
        "removed_bytes": prior_bytes + int(cleanup.get("removed_bytes", 0)) + numpy_removed_bytes,
        "removed_numpy_diagnostic_file_count": prior_npy_count + len(numpy_records),
        "removed_numpy_diagnostic_bytes": prior_npy_bytes + numpy_removed_bytes,
        "records": prior_records + fresh_records + numpy_records,
    })
    if v4_response_audit is not None:
        cleanup["v4_response_verification_before_cleanup"] = v4_response_audit
        for record in numpy_records:
            record["reason"] = (
                "fixed per-cell target complex response and source array hashes were verified "
                "before dense CSI map removal"
            )
        cleanup["records"] = prior_records + fresh_records + numpy_records
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
    v4_output_complete: bool = False,
    v4_size_pilot_authorization: Mapping[str, Any] | None = None,
    v4_confirmatory_authorization: Mapping[str, Any] | None = None,
    v4_confirmatory_registry: Mapping[str, Any] | None = None,
    v4_confirmatory_batch_id: str | None = None,
) -> dict[str, Any]:
    case_id = case_identifier(phase, seed, speed_mps, snr_db, delay_ns, target_count, period_count)
    authorization_record = None
    if v4_output_complete:
        if v4_confirmatory_authorization is not None:
            if v4_size_pilot_authorization is not None:
                raise ValueError("V4 pilot and confirmatory authorization cannot be combined")
            from scripts.phase_r.r22_v4_confirmatory_executor import (
                validate_case_authorization,
            )

            authorization_record = validate_case_authorization(
                v4_confirmatory_authorization,
                v4_confirmatory_registry,
                batch_id=v4_confirmatory_batch_id,
                output_root=output_root,
                phase=phase,
                seed=seed,
                speed_mps=speed_mps,
                snr_db=snr_db,
                delay_ns=delay_ns,
                target_count=target_count,
                period_count=period_count,
                first_beam=first_beam,
                beam_count=beam_count,
                template=template,
                method_config=method_config_path,
                stage2_binary=stage2_binary,
                production_binary=production_binary,
            )
        else:
            authorization_record = _require_v4_size_pilot_authorization(
                v4_size_pilot_authorization,
                phase=phase, case_id=case_id, seed=seed, speed_mps=speed_mps,
                snr_db=snr_db, delay_ns=delay_ns, target_count=target_count,
                period_count=period_count, first_beam=first_beam, beam_count=beam_count,
                method_ids=method_ids, calibration_modes=calibration_modes, area=area,
                template=template, method_config_path=method_config_path,
            )
    elif (v4_size_pilot_authorization is not None
            or v4_confirmatory_authorization is not None
            or v4_confirmatory_registry is not None
            or v4_confirmatory_batch_id is not None):
        raise ValueError("V4 authorization records require v4_output_complete=True")
    output_root = Path(output_root).expanduser().resolve()
    if v4_output_complete:
        # Preserve the complete source tree until the outer matrix transaction
        # has published and verified its compact archive.
        keep_raw = True
    if output_root.exists() and (not output_root.is_dir() or any(output_root.iterdir())):
        raise ValueError(f"refusing to overwrite non-empty R22 output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    validate_case_scale(phase=phase, target_count=target_count, period_count=period_count)
    source_snapshot = _source_identity()
    stage2_binary = Path(stage2_binary).expanduser().resolve()
    production_binary = Path(production_binary).expanduser().resolve()
    preflight = r21._native_cuda_preflight(production_binary, output_root)
    if v4_output_complete:
        preflight["gpu"]["driver_identity"] = _cuda_driver_identity()
    if not stage2_binary.is_file():
        raise FileNotFoundError(f"Stage2 simulator missing: {stage2_binary}")
    stage2_identity = file_identity(stage2_binary)
    scenario_path, generator_manifest = _make_scenario(
        Path(template), output_root, seed=seed, target_count=target_count,
        speed_mps=speed_mps, snr_db=snr_db, period_count=period_count,
        first_beam=first_beam, beam_count=beam_count, area=area,
    )
    method_config_path = Path(method_config_path).expanduser().resolve()
    execution_method_ids = (
        _v4_calibration_method_ids(delay_ns)
        if v4_output_complete else _method_ids_for_delay(method_ids, delay_ns)
    )
    method_config, method_contract = _load_method_configuration(
        method_config_path, scenario_path, execution_method_ids,
        allow_empty=v4_output_complete and not execution_method_ids,
    )
    v4_output_capture = _apply_v4_output_capture(
        method_config, v4_output_complete=v4_output_complete,
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
            "method_ids": (
                ["A0", *execution_method_ids] if v4_output_complete else list(method_ids)
            ),
            "caller_method_ids": list(method_ids),
            "v4_registered_methods_applicable": (
                ["A0"] if float(delay_ns) == 0.0 else [
                    "C0", "FIXED_D3", "HUBER_D3_CAUSAL_PREFIX",
                    "M1_CAUSAL_PREFIX", "M1_BATCH10_DIAGNOSTIC", "PK_KNOWN",
                ] if v4_output_complete else None
            ),
            "calibration_modes": list(calibration_modes),
            "execution_method_ids": list(execution_method_ids),
            "production_processing_range": calibration._production_range_overrides(
                method_config["production"]
            ),
            "zero_delay_blind_correction_policy": (
                "estimate_only_no_physical_correction" if float(delay_ns) == 0.0
                else "execute_frozen_blind_and_fixed_corrections"
            ),
            "target_truth_nested_across_density": True,
            "known_error_use": "physical correction upper bound only; blind estimator does not receive it",
            "ai_training": False, "router_enabled": False,
            "raw_artifact_policy": (
                "keep_generated_binary_artifacts" if keep_raw else
                "remove_generated_bin_after_evaluation_keep_audit_files"
            ),
            "v4_output_complete": bool(v4_output_complete),
            "v4_causal_estimator_policy": (
                "period_prefix_current_period_zero_fallback" if v4_output_complete else None
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
        "v4_output_capture_overrides": v4_output_capture,
        "v4_size_pilot_authorization": authorization_record,
        "v4_confirmatory_authorization": (
            authorization_record
            if isinstance(authorization_record, Mapping)
            and authorization_record.get("status") == "AUTHORIZED_FOR_SELECTED_BATCHES"
            else None
        ),
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
        method_config_snapshot_identity: dict[str, Any] | None = None
        method_config_effective_snapshot_identity: dict[str, Any] | None = None
        source_xml_snapshot_path: Path | None = None
        if v4_output_complete:
            snapshot_path = pipeline_case_root / "method_config_snapshot.json"
            snapshot_path.write_bytes(method_config_path.read_bytes())
            method_config_snapshot_identity = {
                "path": snapshot_path.name,
                "bytes": snapshot_path.stat().st_size,
                "sha256": sha256_file(snapshot_path),
            }
            effective_config_snapshot = pipeline_case_root / "method_config_effective_snapshot.json"
            write_json(effective_config_snapshot, method_config)
            method_config_effective_snapshot_identity = file_identity(effective_config_snapshot)
            pipeline_manifest["v4_output_capture_overrides"] = v4_output_capture
            pipeline_manifest["method_config_source_snapshot"] = method_config_snapshot_identity
            pipeline_manifest["method_config_effective_snapshot"] = method_config_effective_snapshot_identity
            source_xml_value = pipeline_manifest.get("source_xml")
            if not source_xml_value:
                raise ValueError("V4 pipeline manifest lacks the effective Stage2 source XML")
            source_xml_snapshot_path = Path(str(source_xml_value)).expanduser().resolve()
            try:
                source_xml_snapshot_path.relative_to(pipeline_case_root)
            except ValueError as exc:
                raise ValueError("V4 effective Stage2 source XML is outside the pipeline case") from exc
        write_json(pipeline_case_root / "case_manifest.json", pipeline_manifest)
        raw = _raw_role_periods(pipeline_manifest, period_count)
        layout = pipeline_manifest.get("protocol_layout")
        if not isinstance(layout, Mapping):
            raise ValueError("Production protocol layout missing from pipeline manifest")
        c0_on = next((row for row in pipeline_manifest.get("method_branches", [])
                      if row.get("method_id") == "C0" and row.get("role") == "ON"
                      and row.get("mode") == "not_applicable" and row.get("status") == "passed"), None)
        estimate_record: dict[str, Any]
        zero_error_target_free_estimate: dict[str, Any] | None = None
        qualified: dict[str, Any]
        causal_prefix_records: dict[str, list[dict[str, Any]]] = {}
        if v4_output_complete and float(delay_ns) == 0.0:
            estimate_record = {
                "status": "NOT_APPLICABLE",
                "reason": "zero_delay_reference_has_no_registered_delay_estimator",
                "input_role": None,
                "truth_used_in_estimator": False,
            }
            qualified = {
                "d3_huber": {"status": "NOT_APPLICABLE", "reason": estimate_record["reason"]},
                "m1": {"status": "NOT_APPLICABLE", "reason": estimate_record["reason"]},
            }
        elif c0_on is None or not c0_on.get("production_xml"):
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
        if v4_output_complete and float(delay_ns) != 0.0:
            runtime_xml = (
                Path(str(c0_on["production_xml"]))
                if c0_on is not None and c0_on.get("production_xml")
                else output_root / "missing_causal_estimator_runtime.xml"
            )
            causal_prefix_records = _causal_prefix_estimates(
                raw["A1_Current_unknown_error"]["ON"], runtime_xml,
            )
        if float(delay_ns) == 0.0 and not v4_output_complete:
            try:
                zero_error_target_free_estimate = calibration.estimate_blind_delay(
                    stage1, raw["A1_Current_unknown_error"]["OFF"], layout,
                )
            except (OSError, RuntimeError, ValueError, KeyError, SystemExit) as exc:
                zero_error_target_free_estimate = {
                    "status": "NOT_AVAILABLE",
                    "input_role": "same_seed_A1_OFF_raw_C1_C2_only",
                    "truth_used_in_estimator": False,
                    "reason": f"zero_error_estimate_only_failed:{exc}",
                }
        method_records = list(pipeline_manifest.get("method_branches", []))
        correction_specs, extra_methods = _extra_delay_correction_plan(
            delay_ns=delay_ns,
            qualified=qualified,
            estimate_record=estimate_record,
            zero_error_target_free_estimate=zero_error_target_free_estimate,
        )
        if v4_output_complete:
            scalar_blind_ids = {"R22_D3_Huber_B_ON", "R22_M1_Physics_Matched"}
            correction_specs = [row for row in correction_specs if row[0] not in scalar_blind_ids]
            extra_methods = [row for row in extra_methods if row.get("method_id") not in scalar_blind_ids]
            if float(delay_ns) == 0.0:
                extra_methods = []
            else:
                registered_extra_ids = {
                    "R22_R9_Fixed_D3", "HUBER_D3_CAUSAL_PREFIX", "M1_CAUSAL_PREFIX",
                }
                extra_methods = [
                    row for row in extra_methods
                    if row.get("method_id") in registered_extra_ids
                ]
        if (not any(row.get("method_id") == "PK" for row in method_records)
                and not (v4_output_complete and float(delay_ns) == 0.0)):
            extra_methods.append({"method_id": "Known_error_correction_upper_bound", "mode": "not_applicable",
                                  "role": "ALL", "status": "NOT_AVAILABLE", "reason": "PK_method_not_selected"})

        source_xml = Path(str(pipeline_manifest.get("source_xml", "")))
        truth_root = Path(str(pipeline_manifest["simulation"]["A0_ON"]["output_dir"])) / "truth"
        for condition, correction_ns, source, estimate in correction_specs:
            extra_methods.extend(_run_extra_delay_method(
                pipeline_case_root, case_id, condition, correction_ns, source, estimate,
                raw["A1_Current_unknown_error"], source_xml, truth_root, layout, period_count,
                method_config["production"],
                v4_output_complete=v4_output_complete,
            ))
        if v4_output_complete and float(delay_ns) != 0.0:
            for method_id in ("M1_CAUSAL_PREFIX", "HUBER_D3_CAUSAL_PREFIX"):
                extra_methods.extend(_run_causal_prefix_delay_method(
                    pipeline_case_root,
                    case_id=case_id,
                    method_id=method_id,
                    period_estimates=causal_prefix_records[method_id],
                    raw_roles=raw["A1_Current_unknown_error"],
                    source_xml=source_xml,
                    truth_root=truth_root,
                    layout=layout,
                    production_config=method_config["production"],
                    v4_output_complete=v4_output_complete,
                ))
        method_records.extend(extra_methods)
        if v4_output_complete:
            method_records = _annotate_v4_branch_classes(
                method_records, phase=phase, delay_ns=delay_ns,
            )
        method_records = _audit_production_branches(
            method_records,
            case_root=pipeline_case_root if v4_output_complete else None,
            case_id=case_id if v4_output_complete else None,
            method_config_identity=(
                method_config_effective_snapshot_identity if v4_output_complete else None
            ),
            default_source_xml=source_xml_snapshot_path,
        )
        pipeline_manifest["method_branches"] = method_records
        pipeline_manifest["r22_delay_estimator"] = estimate_record
        pipeline_manifest["r22_blind_method_qualification"] = qualified
        pipeline_manifest["r22_zero_error_target_free_estimate_only"] = zero_error_target_free_estimate
        if v4_output_complete:
            pipeline_manifest["r22_batch_m1_diagnostic"] = {
                "status": (
                    "NOT_APPLICABLE_ZERO_DELAY_REFERENCE"
                    if float(delay_ns) == 0.0 else estimate_record.get("status", "NOT_AVAILABLE")
                ),
                "online_ranking": False,
                "used_to_apply_correction": False,
                "estimate_record": estimate_record,
            }
            pipeline_manifest["r22_causal_prefix_estimators"] = causal_prefix_records
        if v4_output_complete:
            from scripts.phase_r.r22_v4_evidence import extract_target_complex_response

            v4_response_manifest = extract_target_complex_response(
                pipeline_case_root,
                simulation=pipeline_manifest.get("simulation", {}),
                branch_records=method_records,
                target_count=target_count,
                period_count=period_count,
                case_id=case_id,
            )
            pipeline_manifest["r22_v4_target_response"] = v4_response_manifest
        write_json(pipeline_case_root / "case_manifest.json", pipeline_manifest)
        tables = _evaluate_branches(
            phase=phase,
            case_id=case_id,
            seed=seed,
            case_root=pipeline_case_root,
            pipeline_manifest=pipeline_manifest,
            binary_identity=preflight["binary"],
            branch_records=method_records,
            period_count=period_count,
        )
        _write_tables(output_root, tables)
        stage2_runs = pipeline_manifest.get("simulation", {})
        production_elapsed = [float(row.get("production_elapsed_sec", 0.0))
                              for row in method_records if row.get("production_elapsed_sec") is not None]
        on_task_rows = [row for row in tables["task_metrics"] if row.get("role") == "ON"]
        evaluated_on = [row for row in on_task_rows if row.get("status") == "EVALUABLE"]
        evaluable_h0_task_rows = [
            row for row in tables["h0_task_metrics"]
            if row.get("h0_scope") == "independent_target_free_seed"
            and row.get("role") == "ON"
            and row.get("status") == "EVALUABLE_DESCRIPTIVE_H0_SEED"
        ]
        evaluable_h0_seed_count = len({row.get("seed") for row in evaluable_h0_task_rows})
        invalid_truth = sum(int(row.get("invalid_truth_opportunities") or 0) for row in evaluated_on)
        all_branches_passed = _all_production_branches_passed(method_records)
        manifest.update({
            "status_labels": {
                "IMPLEMENTED": True,
                "EXECUTED": any(row.get("production_returncode") == 0 for row in method_records),
                "EVALUABLE": (
                    bool(evaluable_h0_task_rows) if phase == "h0" else
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
                "independent_h0_seed_rows": evaluable_h0_seed_count,
                "evaluable_h0_method_rows": len(evaluable_h0_task_rows),
                "h0_task_rows": len(tables["h0_task_metrics"]),
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
            cleanup = _compact_case_artifacts(
                pipeline_case_root, require_v4_response=v4_output_complete,
            )
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
    parser.add_argument("--v4-output-complete", action="store_true",
                        help="use period-causal estimators and retain V4 complex target-response evidence")
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
        v4_output_complete=args.v4_output_complete,
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
