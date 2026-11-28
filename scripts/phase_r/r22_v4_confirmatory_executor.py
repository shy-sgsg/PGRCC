#!/usr/bin/env python3
"""Default-deny registry, authorization, checkpoint, and resume controls for R22-V4.

The dry-run path is CPU-only.  The execution path requires a separately approved,
hash-bound authorization record and delegates each case to the Native CUDA runner.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r import r22_v4_execution_plan as execution_plan
from scripts.phase_r import r22_sharded_archive as sharded
from scripts.phase_r import run_r22_cuda_pilot as runner
from scripts.phase_r.r22_v4_confirmatory_statistics import H0_PRIMARY_ENDPOINT
from scripts.phase_r.r22_compact_archive import (
    verify_case_archive,
)


DEFAULT_DESIGN = ROOT / (
    "docs/research/phase_r/R22/config/"
    "r22_v4_causal_output_complete_confirmatory_draft_20261113.json"
)
DEFAULT_TEMPLATE = ROOT / "simulator/scenarios/beam50_one_targets_continuous.run.json"
DEFAULT_METHOD_CONFIG = ROOT / (
    "docs/research/phase_r/R22/config/r22_method_config_v2_range_roi.json"
)
AUTHORIZATION_TYPE = "PGRCC_R22_V4_CONFIRMATORY_NATIVE_CUDA"
ARCHIVE_PROFILE = "r22_v4_output_complete"
COMPACT_PROFILE = sharded.COMPACT_PROFILE
PROJECT_FINAL_CAP_BYTES = 20_000_000_000
RESOURCE_RESERVE_BYTES = 10_737_418_240
CASE_STATES = {
    "PLANNED", "RUNNING", "ARCHIVED", "VERIFIED", "EVALUATED",
    "FAILED", "NOT_EVALUABLE",
}
MAX_TECHNICAL_ATTEMPTS = 3
REQUIRED_THRESHOLD_KEYS = (
    "position_mde_m",
    "detection_coverage_noninferiority_margin_pp",
    "h0_seed_event_rate_upper_limit",
    "h0_false_confirmed_track_rate_upper_limit",
)
_STATISTICAL_POLICY_FIELDS = {
    "position_success_rule": (
        "Scheme A: two-sided bootstrap-t interval upper endpoint < 0 and seed-level point estimate "
        "<= -MDE; Scheme B: two-sided bootstrap-t interval upper endpoint < -MDE"
    ),
    "studentized_zero_variance_policy": (
        "zero observed seed variance or any zero-SE bootstrap replicate makes the interval "
        "NOT_EVALUABLE; report point estimate and counts, return null interval endpoints, "
        "never claim zero uncertainty"
    ),
    "coverage_interval_zero_variance_policy": (
        "zero observed seed-level coverage-difference variance makes the interval NOT_EVALUABLE; "
        "report point estimate and counts, return null interval endpoints, never claim zero uncertainty"
    ),
    "missing_seed_policy": (
        "retain planned seed registry; missing or invalid seed makes the confirmatory endpoint "
        "incomplete; do not replace seeds or silently reduce the denominator"
    ),
    "missing_opportunity_policy": (
        "retain all planned target-period opportunities in detection coverage; a missing required "
        "opportunity record makes that seed position endpoint NOT_EVALUABLE"
    ),
    "common_success_policy": (
        "position RMSE uses only the frozen common correctly-associated opportunity set; every "
        "speed must meet the separately approved minimum support; report coverage alongside "
        "conditional position error"
    ),
    "tie_policy": (
        "strict superiority uses upper endpoint < boundary; detection noninferiority uses lower "
        "endpoint > -margin; equality does not pass"
    ),
    "detection_coverage_noninferiority_rule": (
        "two-sided paired seed-level t 95% interval lower endpoint for M1-minus-FixedD3 percentage-point "
        "coverage difference must be strictly greater than the negative approved margin"
    ),
    "joint_success_rule": (
        "approved position rule AND detection-coverage noninferiority; add a track-coverage gate "
        "only if explicitly selected and its margin is approved"
    ),
    "method_failure_policy": (
        "M1 or Huber prefix estimate failure uses current-period zero correction; every planned "
        "opportunity remains in coverage denominators; never substitute another method or reference"
    ),
    "h0_rate_interval_rule": (
        "seed-any-event and false-confirmed-track-event rates use two-sided exact Clopper-Pearson "
        "intervals and one-sided exact 95% upper bounds for the complete planned seed set; "
        "also report the closed-form zero-event upper bound when applicable; do not infer full-system Pfa"
    ),
    "h0_gate_rule": (
        "the seed-any-event and false-confirmed-track-event one-sided exact 95% upper bounds must "
        "each be strictly below their separately approved limits; require the complete planned H0 "
        "seed set"
    ),
    "h0_incomplete_policy": (
        "all planned H0 seeds and all ten valid beam-50 periods are required for the confirmatory "
        "H0 gate; incomplete seeds do not count as zero-event seeds"
    ),
    "h0_false_track_unit": "unique false confirmed TrackManager track ID per independent seed",
}
RUNTIME_SOURCE_PATHS = (
    "scripts/phase_r/r22_v4_confirmatory_executor.py",
    "scripts/phase_r/run_r22_cuda_pilot.py",
    "scripts/phase_r/r22_experiment_matrix.py",
    "scripts/phase_r/r22_sharded_archive.py",
    "scripts/phase_r/r22_scenario_factory.py",
    "scripts/phase_r/r22_v4_execution_plan.py",
    "scripts/phase_r/r22_v4_evidence.py",
    "scripts/phase_r/r22_v4_case_schema.py",
    "scripts/phase_r/r22_v4_identity.py",
    "scripts/phase_r/r22_compact_archive.py",
    "scripts/phase_r/r22_v4_confirmatory_statistics.py",
    "scripts/phase_r/run_r12_physics_matched_delay.py",
    "scripts/phase_r/run_r21_cuda_pilot.py",
    "scripts/phase_r/physics_matched_delay.py",
    "scripts/phase_r/r21_task_evaluator.py",
    "scripts/phase_r/r21_historical_task_review.py",
    "scripts/run_delay_stage1_formal.py",
    "scripts/estimate_channel_delay.py",
    "scripts/run_production_hierarchical_calibration_pilot.py",
    "scripts/run_track_manager_e2e.py",
)


def canonical_sha256(value: Mapping[str, Any], *, omit: str | None = None) -> str:
    payload = {key: item for key, item in value.items() if key != omit}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def registry_digest(registry: Mapping[str, Any]) -> str:
    return canonical_sha256(registry, omit="registry_sha256")


def authorization_digest(authorization: Mapping[str, Any]) -> str:
    return canonical_sha256(authorization, omit="authorization_sha256")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
    ) + "\n").encode("utf-8")


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_canonical_json_bytes(value))
    os.replace(temporary, path)


def _resolve_path(value: Path, *, base: Path = ROOT) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _frozen_thresholds(design: Mapping[str, Any]) -> dict[str, Any]:
    primary = design.get("primary_comparison", {})
    statistics = design.get("statistics", {})
    h0 = design.get("h0", {})
    return {
        "position_mde_m": primary.get("position_mde_m"),
        "detection_coverage_noninferiority_margin_pp": primary.get(
            "detection_coverage_noninferiority_margin_pp"
        ),
        "h0_seed_event_rate_upper_limit": h0.get("seed_event_rate_upper_limit"),
        "h0_false_confirmed_track_rate_upper_limit": h0.get(
            "false_confirmed_track_rate_upper_limit"
        ),
        "confirmatory_statistical_contract": {
            "position_scheme": primary.get("position_scheme"),
            "minimum_common_support_per_speed": primary.get(
                "minimum_common_support_per_speed"
            ),
            "position_interval_method": statistics.get("position_interval_method"),
            "position_interval_confidence": statistics.get("position_interval_confidence"),
            "coverage_interval_method": statistics.get("coverage_interval_method"),
            "coverage_interval_confidence": statistics.get("coverage_interval_confidence"),
            "track_coverage_interval_method": statistics.get("track_coverage_interval_method"),
            "track_coverage_interval_confidence": statistics.get("track_coverage_interval_confidence"),
            "coverage_interval_method_selection_status": statistics.get(
                "coverage_interval_method_selection_status"
            ),
            "bootstrap_replicates": statistics.get("bootstrap_replicates"),
            "bootstrap_rng_algorithm": statistics.get("bootstrap_rng_algorithm"),
            "bootstrap_seed": statistics.get("bootstrap_seed"),
            "track_protection_policy": primary.get("track_protection_policy"),
            "track_coverage_noninferiority_margin_pp": primary.get(
                "track_coverage_noninferiority_margin_pp"
            ),
            "h0_primary_endpoint": h0.get("primary_false_alarm_endpoint"),
            "h0_false_track_event_endpoint": h0.get("false_confirmed_track_event_endpoint"),
            "h0_supporting_endpoints": h0.get("supporting_endpoints"),
            "h0_interval_confidence": h0.get("interval_confidence"),
            "h0_seed_event_rate_upper_limit": h0.get("seed_event_rate_upper_limit"),
            "h0_false_confirmed_track_rate_upper_limit": h0.get(
                "false_confirmed_track_rate_upper_limit"
            ),
            "secondary_speed_tests": statistics.get("secondary_speed_tests"),
            **{key: statistics.get(key) for key in _STATISTICAL_POLICY_FIELDS},
        },
    }


def _planning_copy(design: Mapping[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(design))
    # The planner is deliberately non-executing.  It may expand a separately
    # frozen design for audit without changing the source design's authorization.
    copied["execution_authorized"] = False
    copied["cuda_authorized"] = False
    return copied


def _batch_key(case: Mapping[str, Any]) -> tuple[str, int, float]:
    return (
        str(case["phase"]), int(case["seed"]),
        float(case["speed_mps"]) if case["phase"] == "target_present" else 0.0,
    )


def _batch_id(key: tuple[str, int, float]) -> str:
    phase, seed, speed = key
    if phase == "target_present":
        speed_label = f"{speed:g}".replace(".", "p")
        return f"TP_seed{seed}_v{speed_label}"
    return f"H0_seed{seed}"


def _validate_seed_registry_binding(
    design: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, set[int]]]:
    references = design.get("references")
    if not isinstance(references, Mapping):
        raise ValueError("confirmatory design has no seed-registry reference")
    relative_path = references.get("seed_registry")
    expected_digest = references.get("seed_registry_sha256")
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("confirmatory design seed-registry path is missing")
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise ValueError("confirmatory design seed-registry digest is missing")
    path = _resolve_path(Path(relative_path))
    if not path.is_file():
        raise FileNotFoundError(f"confirmatory seed registry is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("confirmatory seed registry must be a JSON object")
    actual_digest = canonical_sha256(value, omit="registry_sha256")
    if (value.get("registry_sha256") != expected_digest
            or actual_digest != expected_digest):
        raise ValueError("confirmatory seed registry digest differs from the approved design")

    cohorts = value.get("cohorts")
    if not isinstance(cohorts, Mapping):
        raise ValueError("confirmatory seed registry has no cohorts")
    cohort_specs = {
        "target_present": ("target_present_confirmatory", "target_present"),
        "h0": ("h0_confirmatory", "h0"),
    }
    seed_sets: dict[str, set[int]] = {}
    for phase, (cohort_key, design_key) in cohort_specs.items():
        cohort = cohorts.get(cohort_key)
        seeds = cohort.get("seeds") if isinstance(cohort, Mapping) else None
        if (not isinstance(seeds, list) or not seeds
                or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds)
                or len(set(seeds)) != len(seeds)):
            raise ValueError(f"confirmatory seed registry cohort is invalid: {cohort_key}")
        phase_design = design.get(design_key)
        if not isinstance(phase_design, Mapping):
            raise ValueError(f"confirmatory design cohort is missing: {design_key}")
        count = phase_design.get("seed_count")
        if isinstance(count, bool) or count != len(seeds):
            raise ValueError(f"confirmatory seed count differs from registry: {phase}")
        range_key = (
            "seed_range_inclusive" if phase == "target_present"
            else "candidate_seed_range_inclusive"
        )
        seed_range = phase_design.get(range_key)
        if (not isinstance(seed_range, list) or len(seed_range) != 2
                or any(isinstance(item, bool) or not isinstance(item, int) for item in seed_range)
                or seeds != list(range(seed_range[0], seed_range[1] + 1))):
            raise ValueError(f"confirmatory design seed range differs from registry: {phase}")
        seed_sets[phase] = set(seeds)
    return {
        "path": str(path),
        "registry_id": value.get("registry_id"),
        "registry_sha256": expected_digest,
    }, seed_sets


def build_confirmatory_registry(
    *, design_path: Path = DEFAULT_DESIGN,
    scenario_template: Path = DEFAULT_TEMPLATE,
    method_config: Path = DEFAULT_METHOD_CONFIG,
    output_root: Path,
) -> dict[str, Any]:
    design_path = _resolve_path(design_path)
    template_path = _resolve_path(scenario_template)
    method_config_path = _resolve_path(method_config)
    for path, label in (
        (design_path, "design"), (template_path, "scenario template"),
        (method_config_path, "method config"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"R22-V4 {label} is missing: {path}")
    design = json.loads(design_path.read_text(encoding="utf-8"))
    if not isinstance(design, dict):
        raise ValueError("R22-V4 design must be a JSON object")
    seed_registry_binding, approved_seed_sets = _validate_seed_registry_binding(design)
    expanded = execution_plan.build_v4_execution_plan(_planning_copy(design))
    output_path = _resolve_path(output_root)
    source_identity = {
        source: sha256_file(ROOT / source)
        for source in RUNTIME_SOURCE_PATHS
    }
    runtime_profile = {
        "backend": "Native CUDA",
        "use_corex": False,
        "archive_profile": ARCHIVE_PROFILE,
        "v4_output_complete": True,
        "checkpoint_states": sorted(CASE_STATES),
        "technical_retry_limit": MAX_TECHNICAL_ATTEMPTS,
        "ai_training": False,
    }

    cases: list[dict[str, Any]] = []
    batches_by_key: dict[tuple[str, int, float], list[dict[str, Any]]] = {}
    for source_case in expanded["case_identities"]:
        case = dict(source_case)
        case["first_beam"] = int(case["beam"])
        case["beam_count"] = 1
        key = _batch_key(case)
        batch_id = _batch_id(key)
        case["batch_id"] = batch_id
        case["identity_sha256"] = canonical_sha256(case)
        cases.append(case)
        batches_by_key.setdefault(key, []).append(case)

    batches: list[dict[str, Any]] = []
    for key in sorted(batches_by_key, key=lambda item: (item[0], item[1], item[2])):
        rows = sorted(
            batches_by_key[key], key=lambda item: float(item["delay_error_ns"]),
        )
        delays = [float(item["delay_error_ns"]) for item in rows]
        if delays != [0.0, 4.0]:
            raise ValueError(f"confirmatory paired batch must contain 0/+4 ns: {_batch_id(key)}")
        batches.append({
            "batch_id": _batch_id(key),
            "phase": key[0], "seed": key[1],
            "speed_mps": key[2] if key[0] == "target_present" else None,
            "delay_error_ns": delays,
            "case_ids": [str(item["case_id"]) for item in rows],
        })
    if len(cases) != 240 or len(batches) != 120:
        raise ValueError(
            f"V4 confirmatory registry expected 240 cases/120 paired batches, "
            f"found {len(cases)}/{len(batches)}"
        )
    expanded_seed_sets = {
        phase: {int(case["seed"]) for case in cases if case.get("phase") == phase}
        for phase in ("target_present", "h0")
    }
    if expanded_seed_sets != approved_seed_sets:
        raise ValueError("expanded confirmatory cases differ from the approved seed registry")

    registry: dict[str, Any] = {
        "schema_version": 1,
        "registry_type": "R22_V4_CONFIRMATORY_CASE_REGISTRY",
        "design_id": design.get("design_id"),
        "design_status": design.get("status"),
        "design_execution_authorized": design.get("execution_authorized"),
        "design_cuda_authorized": design.get("cuda_authorized"),
        "ai_training": design.get("ai_training"),
        "execution_authorized": False,
        "cuda_authorized": False,
        "design_path": str(design_path),
        "design_sha256": sha256_file(design_path),
        "seed_registry_path": seed_registry_binding["path"],
        "seed_registry_id": seed_registry_binding["registry_id"],
        "seed_registry_sha256": seed_registry_binding["registry_sha256"],
        "scenario_template_path": str(template_path),
        "scenario_template_sha256": sha256_file(template_path),
        "method_config_path": str(method_config_path),
        "method_config_sha256": sha256_file(method_config_path),
        "runtime_source_identity": source_identity,
        "runtime_source_identity_sha256": canonical_sha256(source_identity),
        "runtime_profile": runtime_profile,
        "runtime_profile_sha256": canonical_sha256(runtime_profile),
        "method_inventory": {
            "target_present": [dict(row) for row in design.get("target_present", {}).get("methods", [])],
            "h0": [dict(row) for row in design.get("h0", {}).get("methods", [])],
        },
        "output_root": str(output_path),
        "archive_profile": ARCHIVE_PROFILE,
        "production_backend": "Native CUDA",
        "use_corex": False,
        "case_count": len(cases),
        "batch_count": len(batches),
        "case_counts": dict(expanded["case_counts"]),
        "cases": cases,
        "batches": batches,
        "frozen_thresholds": _frozen_thresholds(design),
        "execution_plan_counts": {
            "registered_science_branches": expanded["registered_branch_counts"],
            "production_calls": expanded["production_branch_call_counts"],
            "stage2_calls": expanded["stage2_call_counts"],
        },
        "not_executed": ["Stage2", "Native CUDA Production", "confirmatory matrix", "AI training"],
    }
    registry["registry_sha256"] = registry_digest(registry)
    return registry


def authorization_template(registry: Mapping[str, Any]) -> dict[str, Any]:
    """Return a visibly invalid template; generating it grants no permission."""
    return {
        "schema_version": 1,
        "authorization_type": AUTHORIZATION_TYPE,
        "authorization_id": None,
        "status": "NOT_AUTHORIZED",
        "user_approved": False,
        "revoked": False,
        "approved_at_utc": None,
        "expires_at_utc": None,
        "design_sha256": registry.get("design_sha256"),
        "registry_sha256": registry.get("registry_sha256"),
        "selected_batch_ids": [],
        "selected_case_ids": [],
        "max_case_count": 0,
        "output_root": registry.get("output_root"),
        "archive_profile": ARCHIVE_PROFILE,
        "production_backend": "Native CUDA",
        "use_corex": False,
        "gpu_uuid": None,
        "stage2_binary_sha256": None,
        "production_binary_sha256": None,
        "scenario_template_sha256": registry.get("scenario_template_sha256"),
        "method_config_sha256": registry.get("method_config_sha256"),
        "resource_evidence_path": None,
        "resource_evidence_sha256": None,
        "resource_reserve_bytes": None,
        "runtime_source_identity": dict(registry.get("runtime_source_identity", {})),
        "runtime_source_identity_sha256": registry.get("runtime_source_identity_sha256"),
        "runtime_profile_sha256": registry.get("runtime_profile_sha256"),
        "minimum_free_bytes": None,
        "frozen_thresholds": dict(registry.get("frozen_thresholds", {})),
        "authorization_sha256": None,
        "note": "Invalid until a separate user approval, frozen design, selected case scope, and runtime identities are recorded.",
    }


def _parse_utc(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"authorization {field} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"authorization {field} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"authorization {field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_thresholds(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("frozen threshold record is missing")
    result: dict[str, Any] = {}
    for key in REQUIRED_THRESHOLD_KEYS:
        item = value.get(key)
        if isinstance(item, bool):
            raise ValueError(f"frozen threshold {key} is invalid")
        try:
            number = float(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"frozen threshold {key} is missing") from exc
        if not (number >= 0.0 and number < float("inf")):
            raise ValueError(f"frozen threshold {key} is invalid")
        if key.endswith("upper_limit") and number > 1.0:
            raise ValueError(f"frozen threshold {key} must be in [0,1]")
        result[key] = number
    contract = value.get("confirmatory_statistical_contract")
    if not isinstance(contract, Mapping):
        raise ValueError("confirmatory statistical contract is missing")
    if contract.get("position_scheme") not in {"A", "B"}:
        raise ValueError("confirmatory statistical contract position scheme must be A or B")
    support = contract.get("minimum_common_support_per_speed")
    if isinstance(support, bool) or not isinstance(support, int) or support < 1:
        raise ValueError("confirmatory statistical contract minimum common support must be positive")
    if contract.get("position_interval_method") != "bootstrap_t":
        raise ValueError("confirmatory statistical contract interval method must be bootstrap_t")
    try:
        confidence = float(contract.get("position_interval_confidence"))
    except (TypeError, ValueError) as exc:
        raise ValueError("confirmatory statistical contract interval confidence is missing") from exc
    if isinstance(contract.get("position_interval_confidence"), bool) or not 0.5 < confidence < 1.0:
        raise ValueError("confirmatory statistical contract interval confidence is invalid")
    coverage_method = contract.get("coverage_interval_method")
    track_coverage_method = contract.get("track_coverage_interval_method")
    if coverage_method not in {"paired_t", "bootstrap_percentile", "bootstrap_bca"}:
        raise ValueError("confirmatory statistical contract coverage interval method is unresolved")
    if track_coverage_method not in {"paired_t", "bootstrap_percentile", "bootstrap_bca"}:
        raise ValueError("confirmatory statistical contract track-coverage interval method is unresolved")
    for field in ("coverage_interval_confidence", "track_coverage_interval_confidence"):
        try:
            interval_confidence = float(contract.get(field))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"confirmatory statistical contract {field} is missing") from exc
        if isinstance(contract.get(field), bool) or not 0.5 < interval_confidence < 1.0:
            raise ValueError(f"confirmatory statistical contract {field} is invalid")
    if contract.get("coverage_interval_method_selection_status") != "USER_APPROVED_AND_FROZEN":
        raise ValueError("confirmatory statistical contract coverage interval method lacks user approval")
    replicates = contract.get("bootstrap_replicates")
    bootstrap_seed = contract.get("bootstrap_seed")
    if (isinstance(replicates, bool) or not isinstance(replicates, int) or replicates < 1
            or isinstance(bootstrap_seed, bool) or not isinstance(bootstrap_seed, int)
            or bootstrap_seed < 0 or contract.get("bootstrap_rng_algorithm") != "PCG64"):
        raise ValueError("confirmatory statistical contract bootstrap identity is invalid")
    if contract.get("track_protection_policy") not in {"DESCRIPTIVE_ONLY", "NONINFERIORITY_GATE"}:
        raise ValueError("confirmatory statistical contract track protection policy is unresolved")
    track_margin = contract.get("track_coverage_noninferiority_margin_pp")
    if contract["track_protection_policy"] == "NONINFERIORITY_GATE":
        try:
            track_margin = float(track_margin)
        except (TypeError, ValueError) as exc:
            raise ValueError("confirmatory statistical contract track margin is missing") from exc
        if (isinstance(contract.get("track_coverage_noninferiority_margin_pp"), bool)
                or not math.isfinite(track_margin) or track_margin < 0):
            raise ValueError("confirmatory statistical contract track margin is invalid")
    elif track_margin is not None:
        raise ValueError("confirmatory statistical contract has an unused track margin")
    try:
        h0_confidence = float(contract.get("h0_interval_confidence"))
    except (TypeError, ValueError) as exc:
        raise ValueError("confirmatory statistical contract H0 confidence is missing") from exc
    if (contract.get("h0_primary_endpoint") != H0_PRIMARY_ENDPOINT
            or not 0.5 < h0_confidence < 1.0):
        raise ValueError("confirmatory statistical contract H0 endpoint or confidence is invalid")
    if contract.get("h0_false_track_event_endpoint") != (
        "proportion of independent H0 seeds with at least one unique false confirmed TrackManager track ID"
    ):
        raise ValueError("confirmatory statistical contract false-track event unit is invalid")
    if contract.get("h0_supporting_endpoints") != [
        "false_unique_cfar_cut_count / valid_cut_count per seed",
        "false_cluster_count per seed",
        "false_confirmed_track_count per seed",
    ]:
        raise ValueError("confirmatory statistical contract H0 supporting metrics differ")
    if contract.get("secondary_speed_tests") != "Holm correction across three speeds if inferential p-values are reported":
        raise ValueError("confirmatory statistical contract speed-test multiplicity rule differs")
    for key, expected in _STATISTICAL_POLICY_FIELDS.items():
        if contract.get(key) != expected:
            raise ValueError(f"confirmatory statistical contract policy differs: {key}")
    for key in ("h0_seed_event_rate_upper_limit", "h0_false_confirmed_track_rate_upper_limit"):
        if contract.get(key) != result[key]:
            raise ValueError(f"confirmatory statistical contract threshold differs: {key}")
    result["confirmatory_statistical_contract"] = dict(contract)
    return result


def _case_schema_key(case: Mapping[str, Any]) -> str:
    family = "h0" if int(case.get("target_count", -1)) == 0 else "target_present"
    return f"{family}_{int(float(case.get('delay_error_ns', -1)))}ns"


def _case_schema_identity_sha256(case: Mapping[str, Any]) -> str:
    identity = {
        key: case[key]
        for key in ("case_id", "phase", "target_count", "delay_error_ns")
        if key in case
    }
    if set(identity) != {"case_id", "phase", "target_count", "delay_error_ns"}:
        raise ValueError("registered V4 case lacks a complete archive schema identity")
    return hashlib.sha256(json.dumps(
        identity, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _allocated_tree_bytes(root: Path, *, exclude_subtrees: Sequence[Path] = ()) -> int:
    input_root = Path(root).expanduser().absolute()
    if input_root.is_symlink() or not input_root.is_dir():
        raise ValueError(f"project storage root must be an existing non-symlink directory: {input_root}")
    base = input_root.resolve()
    if not base.is_dir():
        raise ValueError(f"project storage root must be an existing non-symlink directory: {base}")
    excluded: list[Path] = []
    for item in exclude_subtrees:
        candidate = Path(item).expanduser().absolute()
        try:
            candidate.relative_to(base)
        except ValueError:
            continue
        excluded.append(candidate)
    pending = [base]
    seen_inodes: set[tuple[int, int]] = set()
    allocated = 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                if any(path == item or item in path.parents for item in excluded):
                    continue
                metadata = entry.stat(follow_symlinks=False)
                inode = (int(metadata.st_dev), int(metadata.st_ino))
                if inode not in seen_inodes:
                    allocated += int(metadata.st_blocks) * 512
                    seen_inodes.add(inode)
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(path)
    return allocated


def _resource_projection(
    registry: Mapping[str, Any],
    case_ids: Sequence[str],
    resource_evidence: Mapping[str, Any],
    *,
    reserve_bytes: int,
) -> dict[str, int]:
    per_schema = resource_evidence.get("max_retained_case_bytes_by_schema")
    required_schemas = {"target_present_0ns", "target_present_4ns", "h0_0ns", "h0_4ns"}
    if not isinstance(per_schema, Mapping) or set(per_schema) != required_schemas:
        raise ValueError("resource evidence must measure retained compact-plus-receipt size for all four V4 schemas")
    if (isinstance(reserve_bytes, bool) or not isinstance(reserve_bytes, int)
            or reserve_bytes != RESOURCE_RESERVE_BYTES
            or resource_evidence.get("storage_reserve_bytes") != RESOURCE_RESERVE_BYTES):
        raise ValueError("resource_reserve_bytes must match the frozen 10 GiB storage reserve")
    case_map = {str(row["case_id"]): row for row in registry.get("cases", [])}
    measured_archive_total = 0
    for case_id in case_ids:
        schema = _case_schema_key(case_map[case_id])
        measured_size = per_schema.get(schema)
        if isinstance(measured_size, bool) or not isinstance(measured_size, int) or measured_size <= 0:
            raise ValueError(f"resource evidence has invalid archive size for {schema}")
        measured_archive_total += measured_size
    peak = resource_evidence.get("peak_temporary_bytes")
    if isinstance(peak, bool) or not isinstance(peak, int) or peak <= 0:
        raise ValueError("resource evidence requires a measured positive full-case peak_temporary_bytes")
    project_root = resource_evidence.get("project_root")
    project_allocated = resource_evidence.get("project_allocated_bytes_at_measurement")
    project_cap = resource_evidence.get("project_final_cap_bytes")
    if not isinstance(project_root, str) or not project_root:
        raise ValueError("resource evidence must identify the final project storage root")
    if (isinstance(project_allocated, bool) or not isinstance(project_allocated, int)
            or project_allocated < 0):
        raise ValueError("resource evidence must record project allocated bytes at measurement")
    if project_cap != PROJECT_FINAL_CAP_BYTES:
        raise ValueError("resource evidence final project cap differs from the authorized 20 GB limit")
    archive_with_margin = (measured_archive_total * 5 + 3) // 4
    projected_project_bytes = project_allocated + archive_with_margin
    if projected_project_bytes > project_cap:
        raise ValueError(
            "retained V4 compact archives exceed the final project storage cap with the frozen margin"
        )
    return {
        "selected_case_count": len(case_ids),
        "measured_retained_case_bytes_total": measured_archive_total,
        "retained_case_bytes_with_25pct_margin": archive_with_margin,
        "measured_peak_temporary_bytes": peak,
        "storage_reserve_bytes": reserve_bytes,
        "minimum_free_bytes": archive_with_margin + peak + reserve_bytes,
        "project_root": project_root,
        "project_allocated_bytes_at_measurement": project_allocated,
        "project_retained_archive_projection_bytes": archive_with_margin,
        "project_final_allocated_projection_bytes": projected_project_bytes,
        "project_final_cap_bytes": project_cap,
        "project_cap_margin_bytes": project_cap - projected_project_bytes,
    }


def _authorized_case_ids(
    registry: Mapping[str, Any], authorization: Mapping[str, Any],
) -> list[str]:
    """Return the full signed authorization inventory, including unrequested batches."""
    selected_batches = authorization.get("selected_batch_ids")
    selected_cases = authorization.get("selected_case_ids")
    if (not isinstance(selected_batches, list) or not selected_batches
            or not isinstance(selected_cases, list)):
        raise ValueError("authorization lacks selected batch/case inventories")
    batch_map = {str(row["batch_id"]): row for row in registry.get("batches", [])}
    if any(str(batch_id) not in batch_map for batch_id in selected_batches):
        raise ValueError("authorization references an unregistered batch")
    expected = [
        str(case_id)
        for batch_id in selected_batches
        for case_id in batch_map[str(batch_id)].get("case_ids", [])
    ]
    supplied = [str(case_id) for case_id in selected_cases]
    if len(expected) != len(set(expected)) or set(expected) != set(supplied) or len(expected) != len(supplied):
        raise ValueError("authorization case inventory does not match all selected batches")
    return expected


def validate_confirmatory_authorization(
    authorization: Mapping[str, Any] | None,
    registry: Mapping[str, Any],
    *,
    requested_batch_ids: Sequence[str],
    output_root: Path,
    now: datetime | None = None,
    observed_gpu_uuid: str | None = None,
    stage2_binary_sha256: str | None = None,
    production_binary_sha256: str | None = None,
    archive_profile: str = ARCHIVE_PROFILE,
) -> dict[str, Any]:
    if not isinstance(authorization, Mapping):
        raise ValueError("separate confirmatory authorization record is required")
    if authorization.get("authorization_type") != AUTHORIZATION_TYPE:
        raise ValueError("authorization type does not identify R22-V4 confirmatory CUDA")
    if authorization.get("schema_version") != 1:
        raise ValueError("unsupported confirmatory authorization schema")
    if authorization.get("status") != "AUTHORIZED" or authorization.get("user_approved") is not True:
        raise ValueError("explicit user approval is required")
    if authorization.get("revoked") is not False:
        raise ValueError("authorization is revoked or lacks explicit revoked=false")
    if not str(authorization.get("authorization_id") or "").strip():
        raise ValueError("authorization id is required")
    supplied_digest = str(authorization.get("authorization_sha256") or "")
    if supplied_digest != authorization_digest(authorization):
        raise ValueError("authorization digest is missing or invalid")
    if registry_digest(registry) != registry.get("registry_sha256"):
        raise ValueError("case registry digest is missing or invalid")
    if authorization.get("design_sha256") != registry.get("design_sha256"):
        raise ValueError("authorization design identity differs from the registry")
    if authorization.get("registry_sha256") != registry.get("registry_sha256"):
        raise ValueError("authorization registry identity differs")
    if registry.get("design_status") != "FROZEN":
        raise ValueError("confirmatory design is not frozen")
    if registry.get("design_execution_authorized") is not False:
        raise ValueError("frozen design must defer execution approval to the separate authorization")
    if registry.get("design_cuda_authorized") is not False:
        raise ValueError("frozen design must defer CUDA approval to the separate authorization")
    if registry.get("ai_training") is not False:
        raise ValueError("V4 confirmatory design must keep AI training disabled")
    if registry.get("execution_authorized") is not False or registry.get("cuda_authorized") is not False:
        raise ValueError("registry expansion itself must remain non-executing")
    thresholds = _validate_thresholds(registry.get("frozen_thresholds"))
    design_path = Path(str(registry.get("design_path") or "")).expanduser().resolve()
    if not design_path.is_file() or sha256_file(design_path) != registry.get("design_sha256"):
        raise ValueError("frozen design file changed or is missing after registry creation")
    current_design = _load_json_object(design_path)
    if (current_design.get("status") != "FROZEN"
            or current_design.get("execution_authorized") is not False
            or current_design.get("cuda_authorized") is not False
            or current_design.get("ai_training") is not False
            or _frozen_thresholds(current_design) != registry.get("frozen_thresholds")):
        raise ValueError("frozen design state or thresholds differ from the registry")
    authorized_thresholds = _validate_thresholds(authorization.get("frozen_thresholds"))
    if authorized_thresholds != thresholds:
        raise ValueError("authorization thresholds differ from the frozen design")

    approved_at = _parse_utc(authorization.get("approved_at_utc"), field="approved_at_utc")
    expires_at = _parse_utc(authorization.get("expires_at_utc"), field="expires_at_utc")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires_at <= current:
        raise ValueError("authorization is expired")
    if approved_at > current:
        raise ValueError("authorization approval time is in the future")

    if authorization.get("archive_profile") != ARCHIVE_PROFILE or archive_profile != ARCHIVE_PROFILE:
        raise ValueError("authorization archive profile must be r22_v4_output_complete")
    if authorization.get("production_backend") != "Native CUDA":
        raise ValueError("authorization must specify Native CUDA")
    if authorization.get("use_corex") is not False:
        raise ValueError("CoreX is forbidden for this local Native CUDA executor")
    if authorization.get("scenario_template_sha256") != registry.get("scenario_template_sha256"):
        raise ValueError("scenario template SHA-256 differs from the registry")
    if authorization.get("method_config_sha256") != registry.get("method_config_sha256"):
        raise ValueError("method configuration SHA-256 differs from the registry")
    evidence_path_value = authorization.get("resource_evidence_path")
    evidence_sha_value = authorization.get("resource_evidence_sha256")
    if not evidence_path_value or not evidence_sha_value:
        raise ValueError("measured output-complete resource evidence path and SHA-256 are required")
    evidence_path = Path(str(evidence_path_value)).expanduser().resolve()
    if not evidence_path.is_file() or sha256_file(evidence_path) != evidence_sha_value:
        raise ValueError("measured resource evidence is missing or its SHA-256 changed")
    resource_evidence = _load_json_object(evidence_path)
    if (resource_evidence.get("status") not in {
                "PASS", "PASS_WITH_CONSERVATIVE_RECEIPT_MODEL",
            }
            or resource_evidence.get("archive_profile") != ARCHIVE_PROFILE
            or resource_evidence.get("stage2_production_cuda_started") is not True):
        raise ValueError("resource evidence lacks completed V4 pilots and a bounded output profile")
    if (resource_evidence.get("retained_archive_profile") != COMPACT_PROFILE
            or resource_evidence.get("peak_temporary_includes_full_archive") is not True):
        raise ValueError("resource evidence does not bound compact retention and full-archive temporary peak")
    if authorization.get("runtime_source_identity") != registry.get("runtime_source_identity"):
        raise ValueError("runtime source identity differs from the registry")
    if authorization.get("runtime_source_identity_sha256") != registry.get("runtime_source_identity_sha256"):
        raise ValueError("runtime source identity digest differs")
    if authorization.get("runtime_profile_sha256") != registry.get("runtime_profile_sha256"):
        raise ValueError("runtime profile differs from authorization")
    current_source_identity = {
        source: sha256_file(ROOT / source)
        for source in RUNTIME_SOURCE_PATHS
    }
    if current_source_identity != registry.get("runtime_source_identity"):
        raise ValueError("runtime source changed after registry creation")

    selected_batches = authorization.get("selected_batch_ids")
    selected_cases = authorization.get("selected_case_ids")
    if (not isinstance(selected_batches, list) or not selected_batches
            or len(selected_batches) != len(set(selected_batches))):
        raise ValueError("authorization requires a unique selected batch allowlist")
    if not isinstance(selected_cases, list) or len(selected_cases) != len(set(selected_cases)):
        raise ValueError("authorization requires a unique selected case allowlist")
    batch_map = {str(row["batch_id"]): row for row in registry.get("batches", [])}
    case_map = {str(row["case_id"]): row for row in registry.get("cases", [])}
    if any(batch not in batch_map for batch in selected_batches):
        raise ValueError("authorization contains an unregistered batch")
    expected_cases = [
        case_id for batch_id in selected_batches for case_id in batch_map[batch_id]["case_ids"]
    ]
    if set(expected_cases) != set(selected_cases) or len(expected_cases) != len(selected_cases):
        raise ValueError("authorization case allowlist does not exactly match selected batches")
    requested = list(requested_batch_ids)
    if not requested or len(requested) != len(set(requested)):
        raise ValueError("requested batch list must be non-empty and unique")
    if any(batch not in selected_batches for batch in requested):
        raise ValueError("requested batch is outside the approved batch allowlist")
    requested_cases = [case_id for batch in requested for case_id in batch_map[batch]["case_ids"]]
    max_cases = authorization.get("max_case_count")
    if isinstance(max_cases, bool) or not isinstance(max_cases, int):
        raise ValueError("authorization max case count is invalid")
    if max_cases != len(selected_cases) or len(requested_cases) > max_cases:
        raise ValueError("authorization case count exceeds or differs from its exact allowlist")
    projection = _resource_projection(
        registry,
        selected_cases,
        resource_evidence,
        reserve_bytes=authorization.get("resource_reserve_bytes"),
    )

    approved_output = Path(str(authorization.get("output_root") or "")).expanduser().resolve()
    registry_output = Path(str(registry.get("output_root") or "")).expanduser().resolve()
    requested_output = Path(output_root).expanduser().resolve()
    if approved_output != registry_output:
        raise ValueError("approved output root differs from registry output root")
    try:
        requested_output.relative_to(approved_output)
    except ValueError as exc:
        raise ValueError("requested output is outside the approved output root") from exc
    if requested_output == approved_output:
        pass
    for path in (requested_output, approved_output):
        if path.is_symlink():
            raise ValueError("authorized output path cannot be a symlink")

    gpu_uuid = str(authorization.get("gpu_uuid") or "")
    if not gpu_uuid:
        raise ValueError("authorization GPU UUID is missing")
    if observed_gpu_uuid is not None and observed_gpu_uuid != gpu_uuid:
        raise ValueError("observed GPU UUID differs from authorization")
    for field, supplied, expected in (
        ("Stage2 binary SHA-256", stage2_binary_sha256, authorization.get("stage2_binary_sha256")),
        ("Production binary SHA-256", production_binary_sha256, authorization.get("production_binary_sha256")),
    ):
        if supplied is not None and supplied != expected:
            raise ValueError(f"{field} differs from authorization")
    for key in ("stage2_binary_sha256", "production_binary_sha256"):
        value = str(authorization.get(key) or "")
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
            raise ValueError(f"authorization {key} is missing or malformed")
    min_free = authorization.get("minimum_free_bytes")
    if isinstance(min_free, bool) or not isinstance(min_free, int) or min_free <= 0:
        raise ValueError("authorization minimum_free_bytes must be a positive integer")
    if min_free < projection["minimum_free_bytes"]:
        raise ValueError("authorized free-space floor is below the measured resource projection")
    return {
        "status": "AUTHORIZED_FOR_SELECTED_BATCHES",
        "authorization_id": str(authorization["authorization_id"]),
        "authorization_sha256": supplied_digest,
        "registry_sha256": str(registry["registry_sha256"]),
        "batch_ids": requested,
        "case_count": len(requested_cases),
        "case_ids": requested_cases,
        "output_root": str(approved_output),
        "gpu_uuid": gpu_uuid,
        "minimum_free_bytes": min_free,
        "resource_projection": projection,
        "resource_evidence_sha256": str(evidence_sha_value),
        "archive_profile": ARCHIVE_PROFILE,
        "frozen_thresholds": thresholds,
    }


def resource_gate(
    *, authorization: Mapping[str, Any] | None,
    free_bytes: int,
    required_free_bytes: int,
    observed_gpu_uuid: str | None,
    enforce_authorized_floor: bool = True,
) -> dict[str, Any]:
    reasons: list[str] = []
    if not isinstance(authorization, Mapping):
        reasons.append("no separate authorization record")
    else:
        if authorization.get("status") != "AUTHORIZED" or authorization.get("user_approved") is not True:
            reasons.append("authorization is not explicitly approved")
        if authorization.get("archive_profile") != ARCHIVE_PROFILE:
            reasons.append("complete-output archive profile is not authorized")
        if authorization.get("production_backend") != "Native CUDA" or authorization.get("use_corex") is not False:
            reasons.append("runtime backend is not local Native CUDA")
        expected_gpu = str(authorization.get("gpu_uuid") or "")
        if not expected_gpu or observed_gpu_uuid != expected_gpu:
            reasons.append("GPU UUID is absent or differs from authorization")
        authorized_minimum = authorization.get("minimum_free_bytes")
        if (enforce_authorized_floor and isinstance(authorized_minimum, int)
                and not isinstance(authorized_minimum, bool)):
            required_free_bytes = max(required_free_bytes, authorized_minimum)
    if isinstance(free_bytes, bool) or free_bytes < 0:
        reasons.append("free disk measurement is invalid")
    elif free_bytes < required_free_bytes:
        reasons.append("free disk space is below the approved resource floor")
    return {
        "status": "DENIED" if reasons else "READY",
        "reasons": reasons,
        "free_bytes": free_bytes,
        "required_free_bytes": required_free_bytes,
        "observed_gpu_uuid": observed_gpu_uuid,
        "execution_started": False,
    }


def create_checkpoint(registry: Mapping[str, Any]) -> dict[str, Any]:
    if registry_digest(registry) != registry.get("registry_sha256"):
        raise ValueError("cannot checkpoint an invalid case registry")
    cases = {}
    for case in registry.get("cases", []):
        identity = dict(case)
        cases[str(case["case_id"])] = {
            "case_identity_sha256": canonical_sha256(identity),
            "status": "PLANNED",
            "attempt_count": 0,
            "failure_kind": None,
            "reason": None,
            "archive": None,
            "evaluation": None,
        }
    return {
        "schema_version": 1,
        "registry_sha256": registry["registry_sha256"],
        "execution_status": "NOT_AUTHORIZED",
        "execution_authorized": False,
        "cuda_authorized": False,
        "cases": cases,
        "history": [],
    }


def write_checkpoint(path: Path, checkpoint: Mapping[str, Any]) -> None:
    _write_json_atomic(Path(path), checkpoint)


def transition_case(
    checkpoint: dict[str, Any],
    case_id: str,
    new_status: str,
    *,
    failure_kind: str | None = None,
    reason: str | None = None,
    archive: Mapping[str, Any] | None = None,
    evaluation: Mapping[str, Any] | None = None,
    retry_same_case: bool = False,
) -> dict[str, Any]:
    if new_status not in CASE_STATES:
        raise ValueError(f"unknown V4 case status: {new_status}")
    cases = checkpoint.get("cases")
    if not isinstance(cases, dict) or case_id not in cases:
        raise ValueError(f"case is not present in checkpoint registry: {case_id}")
    record = cases[case_id]
    old = record.get("status")
    if new_status == "RUNNING" and old == "FAILED":
        if not retry_same_case:
            raise ValueError("retry requires explicit same-case retry flag")
        if record.get("failure_kind") != "technical":
            raise ValueError("algorithm failure is terminal and cannot be retried")
        if int(record.get("attempt_count", 0)) >= MAX_TECHNICAL_ATTEMPTS:
            raise ValueError("technical retry limit reached for the same case")
    else:
        allowed = {
            "PLANNED": {"RUNNING"},
            "RUNNING": {"ARCHIVED", "FAILED", "NOT_EVALUABLE"},
            "ARCHIVED": {"VERIFIED", "FAILED"},
            "VERIFIED": {"EVALUATED", "NOT_EVALUABLE"},
            "FAILED": set(), "NOT_EVALUABLE": set(), "EVALUATED": set(),
        }
        if new_status not in allowed.get(str(old), set()):
            raise ValueError(f"invalid V4 case transition: {old} -> {new_status}")
    if new_status == "ARCHIVED":
        if not isinstance(archive, Mapping) or archive.get("archive_profile") != ARCHIVE_PROFILE:
            raise ValueError("ARCHIVED status requires a complete-output archive receipt")
        for key in ("archive_path", "archive_sha256", "archive_manifest_sha256"):
            if not archive.get(key):
                raise ValueError(f"ARCHIVED status lacks {key}")
        if (archive.get("case_id") != case_id
                or not archive.get("case_schema_identity_sha256")
                or archive.get("receipt_compression") != "gzip-9"
                or archive.get("full_archive_payload_present") is not False
                or archive.get("historically_verified_full_archive") is not True):
            raise ValueError("ARCHIVED status lacks a case-bound compressed full-output receipt")
        if archive.get("compact_archive_profile") != COMPACT_PROFILE:
            raise ValueError("ARCHIVED status requires a verified compact-retention archive")
        for key in (
            "compact_archive_path", "compact_archive_sha256",
            "compact_archive_manifest_sha256",
        ):
            if not archive.get(key):
                raise ValueError(f"ARCHIVED status lacks {key}")
    if new_status == "FAILED" and failure_kind not in {"technical", "algorithm", "data"}:
        raise ValueError("FAILED status requires technical, algorithm, or data failure kind")
    if new_status == "EVALUATED" and not isinstance(evaluation, Mapping):
        raise ValueError("EVALUATED status requires evaluator summary")
    if new_status == "RUNNING":
        record["attempt_count"] = int(record.get("attempt_count", 0)) + 1
        record["failure_kind"] = None
        record["reason"] = None
    if new_status == "FAILED":
        record["failure_kind"] = failure_kind
        record["reason"] = reason
    if new_status == "NOT_EVALUABLE":
        record["reason"] = reason
        if evaluation is not None:
            record["evaluation"] = dict(evaluation)
    if new_status == "ARCHIVED":
        record["archive"] = dict(archive or {})
    if new_status == "EVALUATED":
        record["evaluation"] = dict(evaluation or {})
    record["status"] = new_status
    checkpoint.setdefault("history", []).append({
        "case_id": case_id,
        "from": old,
        "to": new_status,
        "attempt_count": record.get("attempt_count"),
        "reason": reason,
    })
    return record


def _archive_set_bytes(path: Path) -> bytes:
    if path.name.endswith(".json.gz"):
        with gzip.open(path, "rb") as stream:
            return stream.read()
    return path.read_bytes()


def _load_archive_set(path: Path) -> dict[str, Any]:
    value = json.loads(_archive_set_bytes(path))
    if not isinstance(value, dict):
        raise ValueError(f"V4 archive-set receipt is not a JSON object: {path}")
    return value


def _verify_archive_set_and_compact(
    archive_set_path: Path,
    archive_set: Mapping[str, Any],
    *,
    expected_manifest_sha256: str,
    expected_case_id: str | None = None,
) -> dict[str, Any]:
    if (archive_set.get("status") != "COMPACTED_CLEANED"
            or archive_set.get("archive_profile") != ARCHIVE_PROFILE
            or archive_set.get("archive_manifest_sha256") != expected_manifest_sha256
            or not archive_set.get("full_archive_verified_at_utc")):
        raise ValueError(f"V4 archive-set receipt is not verified and compacted: {archive_set_path}")
    manifest = archive_set.get("archive_manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("V4 full archive manifest is missing from its retained receipt")
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
    ).encode("utf-8") + b"\n"
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        raise ValueError("retained V4 full archive manifest SHA-256 differs")
    identity = manifest.get("case_schema_identity")
    schema = manifest.get("case_schema")
    if (not isinstance(identity, Mapping) or not isinstance(schema, Mapping)
            or schema.get("status") != "PASS"):
        raise ValueError("retained V4 archive lacks its passing registered case schema")
    if expected_case_id is not None and identity.get("case_id") != expected_case_id:
        raise ValueError("retained V4 archive case identity differs from the checkpoint")
    identity_sha = hashlib.sha256(json.dumps(
        identity, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    if identity_sha != manifest.get("case_schema_identity_sha256"):
        raise ValueError("retained V4 case-schema identity digest differs")

    cleanup = archive_set.get("full_archive_cleanup", {})
    if (cleanup.get("status") != "REMOVED_AFTER_COMPACT_VERIFICATION"
            or any(item.get("status") != "REMOVED_AFTER_COMPACT_VERIFICATION"
                   for item in cleanup.get("shards", []))):
        raise ValueError("full V4 archive shards were not all removed after compact verification")
    compact_record = archive_set.get("compact_archive")
    if not isinstance(compact_record, Mapping):
        raise ValueError("retained V4 archive-set receipt has no compact identity")
    compact_path = Path(str(compact_record.get("archive_path") or "")).expanduser().resolve()
    if (compact_record.get("archive_profile") != COMPACT_PROFILE
            or not compact_path.is_file()):
        raise ValueError("retained V4 compact archive is missing or has the wrong profile")

    temporary_receipt: Path | None = None
    plain_receipt = archive_set_path
    try:
        if archive_set_path.name.endswith(".json.gz"):
            fd, name = tempfile.mkstemp(
                prefix=f".{archive_set_path.name}.", suffix=".verify.json",
                dir=archive_set_path.parent,
            )
            temporary_receipt = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(_archive_set_bytes(archive_set_path))
                stream.flush()
                os.fsync(stream.fileno())
            plain_receipt = temporary_receipt
        receipt_check = sharded.verify_sharded_archive_receipt(
            plain_receipt,
            expected_sha256=sha256_file(plain_receipt),
            expected_manifest_sha256=expected_manifest_sha256,
            expected_compact_sha256=str(compact_record.get("archive_sha256") or ""),
        )
    finally:
        if temporary_receipt is not None:
            temporary_receipt.unlink(missing_ok=True)

    compact_manifest_sha256 = str(compact_record.get("archive_manifest_sha256") or "")
    compact_check = verify_case_archive(
        compact_path,
        expected_sha256=str(compact_record.get("archive_sha256") or ""),
        expected_manifest_sha256=compact_manifest_sha256,
        archive_profile=COMPACT_PROFILE,
    )
    with zipfile.ZipFile(compact_path, "r") as compact_zip:
        compact_manifest = json.loads(compact_zip.read("ARCHIVE_MANIFEST.json"))
    if (compact_manifest.get("source_archive_manifest_sha256") != expected_manifest_sha256
            or compact_manifest.get("source_archive_set_identity_sha256")
            != archive_set.get("archive_set_identity_sha256")):
        raise ValueError("compact archive is not bound to the retained full-output receipt")
    compact_names = {str(row.get("path")) for row in compact_manifest.get("included_files", [])}
    if not set(manifest.get("required_v4_members", [])) <= compact_names:
        raise ValueError("compact archive omits a required V4 evidence member")
    return {
        "case_id": identity.get("case_id"),
        "archive_set_identity_sha256": archive_set.get("archive_set_identity_sha256"),
        "case_schema_identity_sha256": manifest.get("case_schema_identity_sha256"),
        "full_archive_bytes": archive_set.get("archive_bytes"),
        "full_archive_payload_present": False,
        "historically_verified_full_archive": True,
        "compact_archive_path": str(compact_path),
        "compact_archive_sha256": compact_record.get("archive_sha256"),
        "compact_archive_manifest_sha256": compact_record.get("archive_manifest_sha256"),
        "compact_archive_verified": True,
        "compact_verification": compact_check,
        "receipt_verification": receipt_check,
    }


def _default_archive_verifier(path: Path, sha: str, manifest_sha: str, profile: str) -> Any:
    if profile != ARCHIVE_PROFILE:
        raise ValueError("checkpoint archive profile differs from the V4 full-output receipt")
    if not path.is_file() or sha256_file(path) != sha:
        raise ValueError(f"compressed V4 archive-set receipt SHA-256 mismatch: {path}")
    archive_set = _load_archive_set(path)
    return _verify_archive_set_and_compact(
        path, archive_set, expected_manifest_sha256=manifest_sha,
    )


def validate_checkpoint_resume(
    checkpoint: Mapping[str, Any],
    registry: Mapping[str, Any],
    *,
    authorization_sha256: str | None = None,
    archive_verifier: Callable[..., Any] = _default_archive_verifier,
) -> dict[str, Any]:
    if registry_digest(registry) != registry.get("registry_sha256"):
        raise ValueError("resume registry digest is invalid")
    if checkpoint.get("registry_sha256") != registry.get("registry_sha256"):
        raise ValueError("resume case registry identity changed")
    if authorization_sha256 is not None and checkpoint.get("authorization_sha256") != authorization_sha256:
        raise ValueError("resume authorization identity changed")
    records = checkpoint.get("cases")
    if not isinstance(records, Mapping):
        raise ValueError("resume checkpoint has no case state map")
    expected = {str(row["case_id"]): row for row in registry.get("cases", [])}
    if set(records) != set(expected):
        raise ValueError("resume case set differs from the frozen registry")
    verified_archives = 0
    for case_id, row in expected.items():
        state = records[case_id]
        if state.get("case_identity_sha256") != canonical_sha256(row):
            raise ValueError(f"resume case identity changed: {case_id}")
        if state.get("status") not in CASE_STATES:
            raise ValueError(f"resume case status is invalid: {case_id}")
        archive = state.get("archive")
        if state.get("status") in {"ARCHIVED", "VERIFIED", "EVALUATED", "NOT_EVALUABLE"}:
            if not isinstance(archive, Mapping):
                raise ValueError(f"resume prior result has no archive identity: {case_id}")
        if isinstance(archive, Mapping):
            profile = archive.get("archive_profile")
            if profile != ARCHIVE_PROFILE:
                raise ValueError(f"resume archive profile changed: {case_id}")
            if archive.get("compact_archive_profile") != COMPACT_PROFILE:
                raise ValueError(f"resume compact-retention profile changed: {case_id}")
            verification = archive_verifier(
                Path(str(archive.get("archive_path") or "")),
                str(archive.get("archive_sha256") or ""),
                str(archive.get("archive_manifest_sha256") or ""),
                str(profile),
            )
            if archive_verifier is _default_archive_verifier:
                if (archive.get("case_id") != case_id
                        or verification.get("case_id") != case_id
                        or verification.get("case_schema_identity_sha256")
                        != archive.get("case_schema_identity_sha256")
                        or archive.get("case_schema_identity_sha256")
                        != _case_schema_identity_sha256(row)):
                    raise ValueError(f"resume archive case-schema identity changed: {case_id}")
                for checkpoint_key, verified_key in (
                    ("compact_archive_path", "compact_archive_path"),
                    ("compact_archive_sha256", "compact_archive_sha256"),
                    ("compact_archive_manifest_sha256", "compact_archive_manifest_sha256"),
                ):
                    if str(archive.get(checkpoint_key)) != str(verification.get(verified_key)):
                        raise ValueError(f"resume compact archive identity changed: {case_id}")
            verified_archives += 1
    return {
        "status": "RESUME_VALID",
        "registry_sha256": registry["registry_sha256"],
        "case_count": len(expected),
        "verified_archive_count": verified_archives,
    }


def dry_run(
    *,
    design_path: Path = DEFAULT_DESIGN,
    scenario_template: Path = DEFAULT_TEMPLATE,
    method_config: Path = DEFAULT_METHOD_CONFIG,
    output_root: Path,
) -> dict[str, Any]:
    registry = build_confirmatory_registry(
        design_path=design_path,
        scenario_template=scenario_template,
        method_config=method_config,
        output_root=output_root,
    )
    output_path = Path(registry["output_root"])
    checkpoint = create_checkpoint(registry)
    plan = execution_plan.build_v4_execution_plan(_planning_copy(
        json.loads(Path(registry["design_path"]).read_text(encoding="utf-8"))
    ))
    _write_json_atomic(output_path / "confirmatory_execution_plan.json", plan)
    _write_json_atomic(output_path / "confirmatory_case_registry.json", registry)
    _write_json_atomic(output_path / "confirmatory_authorization_template.json", authorization_template(registry))
    write_checkpoint(output_path / "confirmatory_dry_run_checkpoint.json", checkpoint)
    summary = {
        "status": "NOT_AUTHORIZED",
        "registry_sha256": registry["registry_sha256"],
        "case_count": registry["case_count"],
        "batch_count": registry["batch_count"],
        "execution_started": False,
        "stage2_executed": False,
        "production_executed": False,
        "cuda_executed": False,
        "ai_training": False,
        "output_root": str(output_path),
    }
    _write_json_atomic(output_path / "confirmatory_dry_run.json", summary)
    return summary


def _probe_gpu_uuid() -> str:
    completed = subprocess.run(
        ["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"],
        check=True, capture_output=True, text=True, timeout=15,
    )
    values = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(values) != 1:
        raise RuntimeError(f"expected one local CUDA device, found {len(values)}")
    return values[0]


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def validate_case_authorization(
    authorization: Mapping[str, Any] | None,
    registry: Mapping[str, Any] | None,
    *,
    batch_id: str | None,
    output_root: Path,
    phase: str,
    seed: int,
    speed_mps: float,
    snr_db: float,
    delay_ns: float,
    target_count: int,
    period_count: int,
    first_beam: int,
    beam_count: int,
    template: Path,
    method_config: Path,
    stage2_binary: Path,
    production_binary: Path,
) -> dict[str, Any]:
    if not isinstance(authorization, Mapping):
        raise ValueError("separate confirmatory authorization record is required")
    if not isinstance(registry, Mapping):
        raise ValueError("V4 confirmatory case requires the frozen case registry")
    if not batch_id:
        raise ValueError("V4 confirmatory case requires its registered paired batch id")
    runner_phase = "confirmatory" if phase == "confirmatory" else phase
    case_id = runner.case_identifier(
        runner_phase, seed, speed_mps, snr_db, delay_ns, target_count, period_count,
    )
    matches = [
        row for row in registry.get("cases", [])
        if row.get("case_id") == case_id and row.get("batch_id") == batch_id
    ]
    if len(matches) != 1:
        raise ValueError("runner case identity is outside the registered batch")
    expected = matches[0]
    actual_fields = {
        "phase": "target_present" if phase == "confirmatory" else "h0",
        "seed": seed,
        "speed_mps": float(speed_mps),
        "snr_db": float(snr_db),
        "delay_error_ns": float(delay_ns),
        "target_count": target_count,
        "period_count": period_count,
        "beam": first_beam,
        "beam_count": beam_count,
    }
    for key, value in actual_fields.items():
        if expected.get(key) != value:
            raise ValueError(f"runner case differs from registered identity: {key}")
    if expected.get("area_clutter") != runner.DEFAULT_AREA:
        raise ValueError("runner area-clutter configuration differs from registered V4 design")
    template_path = _resolve_path(template)
    config_path = _resolve_path(method_config)
    if template_path != Path(str(registry.get("scenario_template_path"))).resolve():
        raise ValueError("runner scenario template path differs from registry")
    if config_path != Path(str(registry.get("method_config_path"))).resolve():
        raise ValueError("runner method configuration path differs from registry")
    if sha256_file(template_path) != registry.get("scenario_template_sha256"):
        raise ValueError("runner scenario template changed after authorization")
    if sha256_file(config_path) != registry.get("method_config_sha256"):
        raise ValueError("runner method configuration changed after authorization")
    return validate_confirmatory_authorization(
        authorization,
        registry,
        requested_batch_ids=[batch_id],
        output_root=output_root,
        observed_gpu_uuid=_probe_gpu_uuid(),
        stage2_binary_sha256=sha256_file(stage2_binary),
        production_binary_sha256=sha256_file(production_binary),
    )


def _materialize_archive_set(archive_set_path: Path) -> Path:
    """Restore the small gzip receipt to its working JSON path for resume."""
    compressed = Path(str(archive_set_path) + ".gz")
    if not compressed.is_file():
        return archive_set_path
    payload = _archive_set_bytes(compressed)
    if archive_set_path.exists():
        if archive_set_path.read_bytes() != payload:
            raise ValueError("plain and compressed V4 archive-set receipts disagree")
        return archive_set_path
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{archive_set_path.name}.", suffix=".resume.tmp",
        dir=archive_set_path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, archive_set_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return archive_set_path


def _compress_archive_set_receipt(archive_set_path: Path) -> tuple[Path, str, str]:
    payload = archive_set_path.read_bytes()
    compressed_path = Path(str(archive_set_path) + ".gz")
    if compressed_path.exists():
        if _archive_set_bytes(compressed_path) != payload:
            raise ValueError("existing compressed V4 archive-set receipt has different content")
    else:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{compressed_path.name}.", suffix=".tmp",
            dir=compressed_path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as raw_stream:
                with gzip.GzipFile(
                    fileobj=raw_stream, mode="wb", compresslevel=9, mtime=0,
                ) as zipped:
                    zipped.write(payload)
                raw_stream.flush()
                os.fsync(raw_stream.fileno())
            with gzip.open(temporary, "rb") as compressed_stream:
                round_trip = compressed_stream.read()
            if round_trip != payload:
                raise ValueError("compressed V4 archive-set receipt failed read-back")
            os.link(temporary, compressed_path)
            temporary.unlink()
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    compressed_sha = sha256_file(compressed_path)
    uncompressed_sha = hashlib.sha256(payload).hexdigest()
    if _archive_set_bytes(compressed_path) != payload:
        raise ValueError("compressed V4 archive-set receipt changed during publication")
    archive_set_path.unlink()
    return compressed_path, compressed_sha, uncompressed_sha


def _archive_confirmatory_case(
    case_root: Path,
    *,
    raw_root: Path,
    archive_root: Path,
    case_identity: Mapping[str, Any],
    attempt: int,
) -> dict[str, Any]:
    case_id = str(case_identity["case_id"])
    stem = f"{case_id}__attempt_{attempt:03d}"
    archive_set_path = archive_root / f"{stem}.archive-set.json"
    compact_path = archive_root / f"{stem}.compact.zip"
    archive_root.mkdir(parents=True, exist_ok=True)
    _materialize_archive_set(archive_set_path)

    if not archive_set_path.exists() and compact_path.exists():
        raise FileExistsError(f"compact archive has no resumable full-output receipt: {compact_path}")
    raw_inventory = _raw_attempt_inventory(case_root)
    if archive_set_path.exists():
        archive_set = _load_archive_set(archive_set_path)
        if (archive_set.get("archive_format") != sharded.ARCHIVE_SET_FORMAT
                or archive_set.get("archive_profile") != ARCHIVE_PROFILE
                or archive_set.get("case_root") != str(case_root.resolve())):
            raise ValueError(f"existing archive-set identity differs for {case_id}")
        manifest = archive_set.get("archive_manifest", {})
        if manifest.get("case_schema_identity") != {
            key: case_identity[key]
            for key in ("case_id", "phase", "target_count", "delay_error_ns")
            if key in case_identity
        }:
            raise ValueError(f"existing archive-set case identity differs for {case_id}")
        if archive_set.get("status") not in {"FULL_VERIFIED", "COMPACT_VERIFIED", "COMPACTED_CLEANED"}:
            sharded.archive_case_directory_sharded(
                case_root,
                archive_set_path,
                raw_root=raw_root,
                archive_profile=ARCHIVE_PROFILE,
                require_complete_output=True,
                remove_source=False,
                case_schema_identity=case_identity,
            )
    else:
        sharded.archive_case_directory_sharded(
            case_root,
            archive_set_path,
            raw_root=raw_root,
            archive_profile=ARCHIVE_PROFILE,
            require_complete_output=True,
            remove_source=False,
            case_schema_identity=case_identity,
        )

    compact_result = sharded.compact_sharded_case_archive(
        archive_set_path, compact_path, cleanup_full_archive=True,
    )
    archive_set = _load_archive_set(archive_set_path)
    manifest_sha = str(archive_set.get("archive_manifest_sha256") or "")
    _verify_archive_set_and_compact(
        archive_set_path, archive_set,
        expected_manifest_sha256=manifest_sha,
        expected_case_id=case_id,
    )
    compressed_path, compressed_sha, uncompressed_sha = _compress_archive_set_receipt(
        archive_set_path,
    )
    archive_record = {
        "case_id": case_id,
        "archive_path": str(compressed_path.resolve()),
        "archive_sha256": compressed_sha,
        "archive_set_uncompressed_sha256": uncompressed_sha,
        "archive_manifest_sha256": manifest_sha,
        "archive_profile": ARCHIVE_PROFILE,
        "archive_bytes": int(archive_set.get("archive_bytes", 0)),
        "archive_set_identity_sha256": archive_set.get("archive_set_identity_sha256"),
        "case_schema_identity_sha256": archive_set.get("archive_manifest", {}).get(
            "case_schema_identity_sha256"
        ),
        "archive_shard_count": len(archive_set.get("shards", [])),
        "full_archive_payload_present": False,
        "historically_verified_full_archive": True,
        "compact_archive_path": str(compact_path.resolve()),
        "compact_archive_sha256": str(compact_result["archive_sha256"]),
        "compact_archive_manifest_sha256": str(compact_result["archive_manifest_sha256"]),
        "compact_archive_profile": COMPACT_PROFILE,
        "compact_archive_bytes": int(compact_result["archive_bytes"]),
        "compact_archive_included_file_count": int(compact_result["included_file_count"]),
        "compact_archive_included_bytes": int(compact_result["included_bytes"]),
        "receipt_compression": "gzip-9",
        "receipt_bytes": compressed_path.stat().st_size,
        "raw_inventory_before_removal": raw_inventory,
        "raw_case_path": str(case_root.resolve()),
    }
    verification = _default_archive_verifier(
        compressed_path, compressed_sha, manifest_sha, ARCHIVE_PROFILE,
    )
    for key in ("compact_archive_path", "compact_archive_sha256", "compact_archive_manifest_sha256"):
        if str(archive_record[key]) != str(verification.get(key)):
            raise ValueError(f"published compact receipt identity differs: {key}")
    return archive_record


def _archive_evaluation(archive_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(archive_path, "r") as archive:
        matches = [name for name in archive.namelist() if name.endswith("r22_run_manifest.json")]
        if len(matches) != 1:
            raise ValueError(f"V4 archive must contain one R22 run manifest: {archive_path}")
        manifest = json.loads(archive.read(matches[0]))
    return {
        "implementation_result": manifest.get("implementation_result"),
        "status_labels": manifest.get("status_labels", {}),
        "evaluation_summary": manifest.get("evaluation_summary", {}),
    }


def _finalize_verified_case(
    checkpoint: dict[str, Any],
    case_id: str,
    *,
    archive_path: Path,
    checkpoint_path: Path,
) -> None:
    record = checkpoint["cases"][case_id]
    if record.get("status") != "VERIFIED":
        return
    evaluation = record.get("pending_evaluation")
    if not isinstance(evaluation, Mapping):
        evaluation = _archive_evaluation(archive_path)
        record["pending_evaluation"] = dict(evaluation)
        write_checkpoint(checkpoint_path, checkpoint)
    labels = evaluation.get("status_labels", {})
    if isinstance(labels, Mapping) and labels.get("EVALUABLE") is True:
        transition_case(checkpoint, case_id, "EVALUATED", evaluation=evaluation)
    else:
        transition_case(
            checkpoint,
            case_id,
            "NOT_EVALUABLE",
            evaluation=evaluation,
            reason="verified archive retained; case evaluator did not mark it EVALUABLE",
        )
    write_checkpoint(checkpoint_path, checkpoint)


def _raw_attempt_inventory(path: Path) -> dict[str, int]:
    file_count = 0
    total_bytes = 0
    if path.is_dir():
        for item in path.rglob("*"):
            if item.is_file() and not item.is_symlink():
                file_count += 1
                total_bytes += item.stat().st_size
    return {"file_count": file_count, "bytes": total_bytes}


def _case_manifest_result(raw_case: Path) -> dict[str, Any] | None:
    manifest_path = raw_case / "r22_run_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        value = _load_json_object(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    labels = value.get("status_labels")
    summary = value.get("evaluation_summary")
    if not isinstance(labels, Mapping) or not isinstance(summary, Mapping):
        return None
    return {
        "implementation_result": value.get("implementation_result"),
        "status_labels": dict(labels),
        "evaluation_summary": dict(summary),
    }


def execute_authorized_batches(
    *,
    registry_path: Path,
    authorization_path: Path,
    batch_ids: Sequence[str],
    stage2_binary: Path,
    production_binary: Path,
    resume: bool = False,
) -> dict[str, Any]:
    registry = _load_json_object(registry_path)
    authorization = _load_json_object(authorization_path)
    output_root = Path(str(registry["output_root"])).resolve()
    stage2_binary = _resolve_path(stage2_binary)
    production_binary = _resolve_path(production_binary)
    if not stage2_binary.is_file() or not production_binary.is_file():
        raise FileNotFoundError("authorized Stage2 and Production binaries must exist")
    stage2_sha = sha256_file(stage2_binary)
    production_sha = sha256_file(production_binary)
    gpu_uuid = _probe_gpu_uuid()
    auth = validate_confirmatory_authorization(
        authorization,
        registry,
        requested_batch_ids=batch_ids,
        output_root=output_root,
        observed_gpu_uuid=gpu_uuid,
        stage2_binary_sha256=stage2_sha,
        production_binary_sha256=production_sha,
    )
    resource_evidence = _load_json_object(
        Path(str(authorization["resource_evidence_path"]))
    )
    output_root.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_root / "confirmatory_checkpoint.json"
    if checkpoint_path.exists():
        if not resume:
            raise FileExistsError("checkpoint exists; pass --resume after archive verification")
        checkpoint = _load_json_object(checkpoint_path)
        validate_checkpoint_resume(
            checkpoint, registry,
            authorization_sha256=auth["authorization_sha256"],
        )
    else:
        if resume:
            raise FileNotFoundError("--resume requested but no checkpoint exists")
        checkpoint = create_checkpoint(registry)

    disk = shutil.disk_usage(output_root.parent)
    resources = resource_gate(
        authorization=authorization,
        free_bytes=int(disk.free),
        required_free_bytes=0 if resume else auth["minimum_free_bytes"],
        observed_gpu_uuid=gpu_uuid,
        enforce_authorized_floor=not resume,
    )
    resources["gate_scope"] = "RESUME_RECOVERY_AND_PER_CASE" if resume else "FRESH_AUTHORIZED_EXECUTION"
    _write_json_atomic(output_root / "confirmatory_resource_gate.json", resources)
    if resources["status"] != "READY":
        raise RuntimeError(f"R22-V4 resource gate denied execution: {resources['reasons']}")
    checkpoint["execution_status"] = "AUTHORIZED_RUNNING"
    checkpoint["execution_authorized"] = True
    checkpoint["cuda_authorized"] = True
    checkpoint["authorization_id"] = auth["authorization_id"]
    checkpoint["authorization_sha256"] = auth["authorization_sha256"]
    checkpoint["resource_gate"] = resources
    write_checkpoint(checkpoint_path, checkpoint)

    case_by_id = {str(row["case_id"]): row for row in registry["cases"]}
    batch_by_id = {str(row["batch_id"]): row for row in registry["batches"]}
    authorized_case_ids = _authorized_case_ids(registry, authorization)
    archive_root = output_root / "archives"
    raw_root = output_root / "raw"
    archive_root.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)
    completed_ids: list[str] = []

    project_root = Path(str(resource_evidence["project_root"])).expanduser().resolve()

    def verify_actual_project_cap(case_id: str | None) -> None:
        allocated_bytes = _allocated_tree_bytes(project_root)
        check = {
            "status": "PASS" if allocated_bytes <= PROJECT_FINAL_CAP_BYTES else "PAUSED_PROJECT_FINAL_CAP",
            "project_root": str(project_root),
            "allocated_bytes": allocated_bytes,
            "project_final_cap_bytes": PROJECT_FINAL_CAP_BYTES,
            "case_id": case_id,
        }
        checkpoint["actual_project_final_storage_check"] = check
        if allocated_bytes > PROJECT_FINAL_CAP_BYTES:
            checkpoint["execution_status"] = "PAUSED_PROJECT_FINAL_CAP"
            checkpoint["last_project_storage_gate"] = check
            write_checkpoint(checkpoint_path, checkpoint)
            raise RuntimeError(
                "actual project allocation exceeds the final cap after verified case cleanup: "
                f"allocated={allocated_bytes} cap={PROJECT_FINAL_CAP_BYTES} case={case_id}"
            )
        write_checkpoint(checkpoint_path, checkpoint)

    def verified_cleanup_and_finalize(case_id: str, archive_record: Mapping[str, Any]) -> None:
        archive_set_path = Path(str(archive_record["archive_path"])).resolve()
        verification = _default_archive_verifier(
            archive_set_path,
            str(archive_record["archive_sha256"]),
            str(archive_record["archive_manifest_sha256"]),
            ARCHIVE_PROFILE,
        )
        if (archive_record.get("case_id") != case_id
                or verification.get("case_id") != case_id
                or verification.get("case_schema_identity_sha256")
                != archive_record.get("case_schema_identity_sha256")
                or archive_record.get("case_schema_identity_sha256")
                != _case_schema_identity_sha256(case_by_id[case_id])):
            raise ValueError(f"verified full-output receipt identity differs for {case_id}")
        if str(verification.get("compact_archive_path")) != str(
            Path(str(archive_record["compact_archive_path"])).resolve()
        ):
            raise ValueError(f"verified compact archive path differs for {case_id}")
        record = checkpoint["cases"][case_id]
        if record["status"] == "ARCHIVED":
            transition_case(checkpoint, case_id, "VERIFIED")
            write_checkpoint(checkpoint_path, checkpoint)
        cleanup = record.get("cleanup")
        if not isinstance(cleanup, Mapping):
            cleanup = {
                "status": "PENDING_VERIFIED_ARCHIVE_CLEANUP",
                "archive_path": str(archive_set_path),
                "archive_sha256": archive_record["archive_sha256"],
                "raw_case_path": record.get("raw_case_path"),
            }
            record["cleanup"] = dict(cleanup)
            write_checkpoint(checkpoint_path, checkpoint)
        raw_case_value = cleanup.get("raw_case_path")
        if raw_case_value:
            raw_case_input = Path(str(raw_case_value)).expanduser()
            if raw_case_input.is_symlink():
                raise ValueError(f"unsafe symlink V4 attempt cleanup path: {raw_case_input}")
            raw_case_path = raw_case_input.resolve()
            try:
                relative = raw_case_path.relative_to(raw_root.resolve())
            except ValueError as exc:
                raise ValueError(f"unsafe raw cleanup path outside the V4 output root: {raw_case_path}") from exc
            if raw_case_path.is_symlink() or len(relative.parts) != 2 or relative.parts[0] != case_id:
                raise ValueError(f"unsafe V4 attempt cleanup path: {raw_case_path}")
            if raw_case_path.exists():
                record["cleanup"]["raw_inventory_before_removal"] = _raw_attempt_inventory(raw_case_path)
                write_checkpoint(checkpoint_path, checkpoint)
                shutil.rmtree(raw_case_path)
        record["cleanup"]["status"] = "REMOVED_AFTER_ARCHIVE_VERIFICATION"
        record["cleanup"]["raw_case_present_after"] = bool(
            raw_case_value and Path(str(raw_case_value)).exists()
        )
        write_checkpoint(checkpoint_path, checkpoint)
        _finalize_verified_case(
            checkpoint,
            case_id,
            archive_path=Path(str(archive_record["compact_archive_path"])).resolve(),
            checkpoint_path=checkpoint_path,
        )

    for batch_id in batch_ids:
        batch = batch_by_id.get(batch_id)
        if batch is None:
            raise ValueError(f"unregistered confirmatory batch: {batch_id}")
        for case_id in batch["case_ids"]:
            case = case_by_id[case_id]
            state = checkpoint["cases"][case_id]
            if state["status"] in {"EVALUATED", "NOT_EVALUABLE"}:
                completed_ids.append(case_id)
                continue
            if state["status"] == "ARCHIVED":
                archive_record = state["archive"]
                verified_cleanup_and_finalize(case_id, archive_record)
                verify_actual_project_cap(case_id)
                completed_ids.append(case_id)
                continue
            if state["status"] == "VERIFIED":
                verified_cleanup_and_finalize(case_id, state["archive"])
                verify_actual_project_cap(case_id)
                completed_ids.append(case_id)
                continue

            if state["status"] == "RUNNING":
                attempt = int(state.get("attempt_count", 0))
                interrupted_raw = raw_root / case_id / f"attempt_{attempt:03d}"
                archive_stem = f"{case_id}__attempt_{attempt:03d}"
                interrupted_archive_set = archive_root / f"{archive_stem}.archive-set.json"
                if (interrupted_archive_set.is_file()
                        or Path(str(interrupted_archive_set) + ".gz").is_file()):
                    archive_record = _archive_confirmatory_case(
                        interrupted_raw,
                        raw_root=raw_root,
                        archive_root=archive_root,
                        case_identity=case,
                        attempt=attempt,
                    )
                    recovered = _case_manifest_result(interrupted_raw)
                    if recovered is None:
                        recovered = _archive_evaluation(
                            Path(str(archive_record["compact_archive_path"])),
                        )
                    state["pending_evaluation"] = recovered
                    state["raw_case_path"] = str(interrupted_raw)
                    transition_case(checkpoint, case_id, "ARCHIVED", archive=archive_record)
                    write_checkpoint(checkpoint_path, checkpoint)
                    verified_cleanup_and_finalize(case_id, archive_record)
                    verify_actual_project_cap(case_id)
                    completed_ids.append(case_id)
                    continue
                recovered = _case_manifest_result(interrupted_raw)
                if recovered is not None and interrupted_raw.is_dir():
                    state["pending_evaluation"] = recovered
                    state["raw_case_path"] = str(interrupted_raw)
                    write_checkpoint(checkpoint_path, checkpoint)
                    recovered_archive = _archive_confirmatory_case(
                        interrupted_raw,
                        raw_root=raw_root,
                        archive_root=archive_root,
                        case_identity=case,
                        attempt=attempt,
                    )
                    recovered_archive["case_id"] = case_id
                    transition_case(checkpoint, case_id, "ARCHIVED", archive=recovered_archive)
                    write_checkpoint(checkpoint_path, checkpoint)
                    verified_cleanup_and_finalize(case_id, recovered_archive)
                    verify_actual_project_cap(case_id)
                    completed_ids.append(case_id)
                    continue
                state["raw_case_path"] = str(interrupted_raw)
                transition_case(
                    checkpoint, case_id, "FAILED", failure_kind="technical",
                    reason="process interruption before complete V4 case manifest; raw attempt preserved",
                )
                write_checkpoint(checkpoint_path, checkpoint)
                raise RuntimeError(
                    f"case {case_id} was interrupted; attempt {attempt:03d} is preserved. "
                    "Resume again to retry this same registered case in a new attempt directory."
                )

            if state["status"] == "FAILED" and state.get("failure_kind") != "technical":
                raise RuntimeError(f"case {case_id} has a terminal {state.get('failure_kind')} failure")
            attempt = int(state.get("attempt_count", 0)) + 1
            raw_case = raw_root / case_id / f"attempt_{attempt:03d}"
            archive_stem = f"{case_id}__attempt_{attempt:03d}"
            archive_set_path = archive_root / f"{archive_stem}.archive-set.json"
            archive_artifacts = (
                archive_set_path,
                Path(str(archive_set_path) + ".gz"),
                Path(str(archive_set_path) + ".shards"),
                archive_root / f"{archive_stem}.compact.zip",
            )
            if raw_case.exists() or raw_case.is_symlink() or any(
                path.exists() for path in archive_artifacts
            ):
                raise FileExistsError(
                    f"attempt output already exists; refusing overwrite: {raw_case} / {archive_artifacts}"
                )
            pending_authorized_cases = [
                authorized_case_id
                for authorized_case_id in authorized_case_ids
                if checkpoint["cases"][authorized_case_id]["status"]
                not in {"EVALUATED", "NOT_EVALUABLE", "ARCHIVED", "VERIFIED"}
            ]
            remaining_projection = _resource_projection(
                registry,
                pending_authorized_cases,
                resource_evidence,
                reserve_bytes=int(authorization["resource_reserve_bytes"]),
            )
            project_root = Path(str(resource_evidence["project_root"])).expanduser().resolve()
            project_persistent_bytes = _allocated_tree_bytes(
                project_root, exclude_subtrees=(raw_case,),
            )
            projected_final_project_bytes = (
                project_persistent_bytes
                + remaining_projection["retained_case_bytes_with_25pct_margin"]
            )
            project_gate = {
                "status": "READY" if projected_final_project_bytes <= PROJECT_FINAL_CAP_BYTES else "PAUSED_PROJECT_FINAL_CAP",
                "project_root": str(project_root),
                "project_persistent_bytes_excluding_active_raw_case": project_persistent_bytes,
                "remaining_retained_archives_with_25pct_margin_bytes": remaining_projection[
                    "retained_case_bytes_with_25pct_margin"
                ],
                "projected_final_allocated_bytes": projected_final_project_bytes,
                "project_final_cap_bytes": PROJECT_FINAL_CAP_BYTES,
                "case_id": case_id,
            }
            if projected_final_project_bytes > PROJECT_FINAL_CAP_BYTES:
                checkpoint["execution_status"] = "PAUSED_PROJECT_FINAL_CAP"
                checkpoint["last_project_storage_gate"] = project_gate
                write_checkpoint(checkpoint_path, checkpoint)
                raise RuntimeError(
                    "project final-storage gate paused before case "
                    f"{case_id}: projected={projected_final_project_bytes} "
                    f"cap={PROJECT_FINAL_CAP_BYTES}"
                )
            checkpoint["last_project_storage_gate"] = project_gate
            write_checkpoint(checkpoint_path, checkpoint)
            disk_now = shutil.disk_usage(output_root.parent)
            case_resource_gate = resource_gate(
                authorization=authorization,
                free_bytes=int(disk_now.free),
                required_free_bytes=remaining_projection["minimum_free_bytes"],
                observed_gpu_uuid=gpu_uuid,
                enforce_authorized_floor=False,
            )
            if case_resource_gate["status"] != "READY":
                checkpoint["execution_status"] = "PAUSED_RESOURCE_GATE"
                checkpoint["last_resource_gate"] = case_resource_gate
                write_checkpoint(checkpoint_path, checkpoint)
                raise RuntimeError(
                    f"resource gate paused before case {case_id}: {case_resource_gate['reasons']}"
                )

            transition_case(
                checkpoint, case_id, "RUNNING",
                retry_same_case=state["status"] == "FAILED",
            )
            checkpoint["cases"][case_id]["raw_case_path"] = str(raw_case)
            write_checkpoint(checkpoint_path, checkpoint)
            try:
                result = runner.run_case(
                    raw_case,
                    phase=str(case["runner_phase"]),
                    seed=int(case["seed"]),
                    speed_mps=float(case["speed_mps"]),
                    snr_db=float(case["snr_db"]),
                    delay_ns=float(case["delay_error_ns"]),
                    target_count=int(case["target_count"]),
                    period_count=int(case["period_count"]),
                    first_beam=int(case["first_beam"]), beam_count=int(case["beam_count"]),
                    stage2_binary=stage2_binary,
                    production_binary=production_binary,
                    template=Path(str(registry["scenario_template_path"])),
                    method_config_path=Path(str(registry["method_config_path"])),
                    area=case["area_clutter"],
                    v4_output_complete=True,
                    v4_confirmatory_authorization=authorization,
                    v4_confirmatory_registry=registry,
                    v4_confirmatory_batch_id=batch_id,
                )
            except Exception as exc:
                transient = isinstance(
                    exc,
                    (OSError, TimeoutError, subprocess.SubprocessError, RuntimeError),
                )
                transition_case(
                    checkpoint, case_id, "FAILED",
                    failure_kind="technical" if transient else "data",
                    reason=f"{type(exc).__name__}: {exc}",
                )
                write_checkpoint(checkpoint_path, checkpoint)
                raise

            labels = result.get("status_labels", {})
            evaluation = {
                "implementation_result": result.get("implementation_result"),
                "status_labels": labels,
                "evaluation_summary": result.get("evaluation_summary", {}),
            }
            checkpoint["cases"][case_id]["pending_evaluation"] = evaluation
            write_checkpoint(checkpoint_path, checkpoint)

            archive_record = _archive_confirmatory_case(
                raw_case,
                raw_root=raw_root,
                archive_root=archive_root,
                case_identity=case,
                attempt=attempt,
            )
            archive_record["case_id"] = case_id
            transition_case(checkpoint, case_id, "ARCHIVED", archive=archive_record)
            write_checkpoint(checkpoint_path, checkpoint)
            checkpoint["cases"][case_id]["raw_case_path"] = str(raw_case)
            checkpoint["cases"][case_id]["cleanup"] = {
                "status": "PENDING_VERIFIED_ARCHIVE_CLEANUP",
                "raw_case_path": str(raw_case),
                "raw_inventory_before_removal": _raw_attempt_inventory(raw_case),
                "archive_path": archive_record["archive_path"],
                "archive_sha256": archive_record["archive_sha256"],
                "compact_archive_path": archive_record["compact_archive_path"],
                "compact_archive_sha256": archive_record["compact_archive_sha256"],
                "reason": "remove raw attempt only after full shard, retained receipt, and compact SHA/member/CRC verification",
            }
            write_checkpoint(checkpoint_path, checkpoint)
            verified_cleanup_and_finalize(case_id, archive_record)
            verify_actual_project_cap(case_id)
            completed_ids.append(case_id)
    verify_actual_project_cap(None)
    checkpoint["execution_status"] = "AUTHORIZED_BATCHES_COMPLETE"
    write_checkpoint(checkpoint_path, checkpoint)
    return {
        "status": checkpoint["execution_status"],
        "batch_ids": list(batch_ids),
        "case_ids": completed_ids,
        "execution_started": True,
        "output_root": str(output_root),
        "checkpoint_path": str(checkpoint_path),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--scenario-template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--method-config", type=Path, default=DEFAULT_METHOD_CONFIG)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--batches", default="")
    parser.add_argument("--stage2-binary", type=Path, default=runner.DEFAULT_STAGE2)
    parser.add_argument("--production-binary", type=Path, default=runner.DEFAULT_PRODUCTION)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run == args.execute:
        parser.error("choose exactly one of --dry-run or --execute")
    if args.dry_run:
        result = dry_run(
            design_path=args.design,
            scenario_template=args.scenario_template,
            method_config=args.method_config,
            output_root=args.output_root,
        )
    else:
        if not args.authorization or not args.registry or not args.batches.strip():
            parser.error("--execute requires --authorization, --registry, and --batches")
        registry_path = _resolve_path(args.registry)
        if _resolve_path(args.output_root) != Path(
            _load_json_object(registry_path)["output_root"]
        ).resolve():
            parser.error("--output-root must equal the registry-approved output root")
        result = execute_authorized_batches(
            registry_path=registry_path,
            authorization_path=_resolve_path(args.authorization),
            batch_ids=[value.strip() for value in args.batches.split(",") if value.strip()],
            stage2_binary=args.stage2_binary,
            production_binary=args.production_binary,
            resume=args.resume,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
