#!/usr/bin/env python3
"""Run the paired production hierarchical calibration delay pilot.

The runner is an orchestration boundary.  Stage-2 generates the paired raw
inputs, ``estimate_channel_delay`` performs the existing fractional-delay
rewrite, and ``run_delay_stage1_formal.run_production_branch`` invokes the
real ``GMTI_pipe_core``/TrackManager path.  This module does not implement a
detector or a replacement tracker.

``--skip-cuda`` (also exposed as ``--contract``) writes only the frozen
contract and provenance.  It intentionally cannot produce algorithmic
results or a GO decision.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.runtime_binary_identity import (
    BinaryIdentityMismatch,
    assert_artifact_sha256 as _assert_artifact_sha256,
    configured_executable_path as _configured_executable_path,
)

CONFIG = ROOT / "configs/research/production_hierarchical_calibration_delay_pilot.json"
TEMPLATE = ROOT / "simulator/scenarios/beam50_one_targets_continuous.run.json"
SIMULATOR = _configured_executable_path(
    "PGRCC_STAGE2_SIMULATOR", ROOT / "build/simulate_stage2_statistical"
)
PIPE = _configured_executable_path("PGRCC_GMTI_PIPE_CORE", ROOT / "build/GMTI_pipe_core")
CONFIGURE_XML = ROOT / "scripts/configure_gmti_xml.py"

METHOD_IDS = ("C0", "C1", "C2", "C3", "C4", "P1", "P2", "PK", "PKR")
COMPACT_EVIDENCE_FILES = (
    "manifest.json",
    "method_contract.csv",
    "gamma_recovery.csv",
    "clutter_metrics.csv",
    "decision_matrix.csv",
    "report.md",
)
ALLOWED_DECISION_LABELS = {
    "GO_EQUIVALENT_CALIBRATION_ONLY",
    "GO_PHYSICAL_CALIBRATION_ONLY",
    "GO_HIERARCHICAL_CALIBRATION",
    "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE",
}
DEFAULT_ADDITIVE_TOLERANCE = 5.0e-5
DEFAULT_COHERENCE_THRESHOLD = 0.0
REFERENCE_FIT_METHOD_IDS = frozenset({"C2", "C3", "C4", "P2", "PKR"})


def method_requires_research_adapter_tap(method: Mapping[str, object]) -> bool:
    """Return whether the configured method enables the research CSI adapter."""

    return bool(method.get("research_calibration_enable", False))


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_rows(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    materialized = [dict(row) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in materialized:
        for key in row:
            if key not in fields:
                fields.append(str(key))
    with path.open("w", encoding="utf-8", newline="") as stream:
        if not fields:
            return
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in materialized:
            writer.writerow(_json_safe(row))


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_identity(path: Path) -> dict[str, object]:
    candidate = Path(path).resolve()
    if not candidate.is_file():
        return {"path": str(candidate), "exists": False, "sha256": None}
    return {
        "path": str(candidate),
        "exists": True,
        "bytes": candidate.stat().st_size,
        "sha256": _sha256(candidate),
    }


def _finite_float(value: object, name: str) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _production_range_overrides(production: Mapping[str, object]) -> dict[str, int]:
    """Translate an optional inclusive processing window into Production XML fields."""

    start_key = "processing_range_start"
    end_key = "processing_range_end"
    has_start = start_key in production
    has_end = end_key in production
    if not has_start and not has_end:
        return {}
    if has_start != has_end:
        raise ValueError("production processing range requires both start and end")
    start = production[start_key]
    end = production[end_key]
    if (
        isinstance(start, bool) or not isinstance(start, int)
        or isinstance(end, bool) or not isinstance(end, int)
    ):
        raise ValueError("production processing range bounds must be integers")
    if start < 0 or end <= start:
        raise ValueError("production processing range must satisfy 0 <= start < end")
    return {"rg_st": start, "rg_ed": end}


def load_pilot_config(path: Path = CONFIG) -> dict[str, object]:
    """Load and validate the frozen delay-pilot configuration."""

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    candidate = candidate.resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f"pilot config does not exist: {candidate}")
    config = _read_json(candidate)
    if config.get("stage") != "Production Hierarchical Calibration Pilot":
        raise ValueError("pilot config stage is not frozen to the production hierarchical pilot")
    production = config.get("production")
    if not isinstance(production, Mapping):
        raise ValueError("pilot config production section must be an object")
    _production_range_overrides(production)
    for key in ("ai_training", "router_enabled", "native_4ch_stap"):
        if config.get(key) is not False:
            raise ValueError(f"{key} must be false for this pilot")
    methods = config.get("methods")
    if not isinstance(methods, list):
        raise ValueError("pilot config methods must be a list")
    ids = tuple(str(item.get("method_id")) for item in methods if isinstance(item, Mapping))
    if ids != METHOD_IDS:
        raise ValueError(f"pilot method order must be {METHOD_IDS}, got {ids}")
    allowlist = config.get("evidence_allowlist")
    if tuple(allowlist or ()) != COMPACT_EVIDENCE_FILES:
        raise ValueError("pilot compact evidence allow-list is not frozen")
    labels = config.get("decision_labels")
    if set(labels or ()) != ALLOWED_DECISION_LABELS:
        raise ValueError("pilot decision labels are not the four allowed labels")
    return config


def build_method_contract(
    config: Mapping[str, object] | None = None,
    *,
    method_ids: Sequence[str] | None = None,
) -> list[dict[str, object]]:
    """Return the frozen method contract, optionally restricted to a subset."""

    source = config if config is not None else load_pilot_config()
    methods = source.get("methods")
    if not isinstance(methods, list):
        raise ValueError("methods must be a list")
    configured: dict[str, dict[str, object]] = {}
    for expected, item in zip(METHOD_IDS, methods):
        if not isinstance(item, Mapping) or str(item.get("method_id")) != expected:
            raise ValueError(f"method contract is missing {expected}")
        copied = copy.deepcopy(dict(item))
        copied.setdefault("truth_used_in_estimator", False)
        if copied["truth_used_in_estimator"] is not False:
            raise ValueError(f"{expected} truth leakage is declared in method contract")
        configured[expected] = copied
    if len(configured) != len(METHOD_IDS):
        raise ValueError("method contract must contain exactly nine methods")
    selected_ids = tuple(METHOD_IDS if method_ids is None else (str(value) for value in method_ids))
    if not selected_ids:
        raise ValueError("at least one method must be selected")
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("duplicate method id in method selection")
    unknown = [method for method in selected_ids if method not in configured]
    if unknown:
        raise ValueError(f"unknown method id in selection: {unknown}")
    selected = set(selected_ids)
    return [configured[method] for method in METHOD_IDS if method in selected]


def resolve_calibration_modes(
    method_contract: Sequence[Mapping[str, object]],
    mode_ids: Sequence[str] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Resolve selected Mode-A/Mode-B paths without changing method defaults."""

    modes = tuple(("Mode-A", "Mode-B") if mode_ids is None else (str(value) for value in mode_ids))
    if not modes:
        raise ValueError("at least one calibration mode must be selected")
    if len(modes) != len(set(modes)):
        raise ValueError("duplicate calibration mode in selection")
    unknown = [mode for mode in modes if mode not in {"Mode-A", "Mode-B"}]
    if unknown:
        raise ValueError(f"unknown calibration mode in selection: {unknown}")
    return {
        str(method["method_id"]): (
            modes if str(method["method_id"]) in REFERENCE_FIT_METHOD_IDS else ("not_applicable",)
        )
        for method in method_contract
    }


def build_paired_input_contract() -> dict[str, object]:
    """Return the OFF/ON/TO and Mode-A/Mode-B information boundary."""

    return {
        "roles": {"OFF": "C+N", "ON": "S+C+N", "TO": "S"},
        "shared_identity_fields": [
            "seed",
            "background_id",
            "target_id",
            "delay_error_ns",
            "production_identity",
        ],
        "identity_rule": "same seed/background/target/production settings",
        "causal_target_rule": "ON-OFF with TO=S as the independent target control",
        "modes": {
            "Mode-A": {
                "estimator_input_roles": ["OFF"],
                "apply_input_roles": ["ON", "TO"],
                "description": "estimate from target-free OFF=C+N and apply the fixed reference to ON=S+C+N and TO=S",
                "truth_used_in_estimator": False,
                "target_position_allowed": False,
                "injected_error_label_allowed": False,
                "known_error_allowed": False,
            },
            "Mode-B": {
                "estimator_input_roles": ["ON"],
                "apply_input_roles": ["ON", "OFF", "TO"],
                "description": "blind online ON=S+C+N estimate; apply its fixed reference to ON/OFF/TO without target truth or error labels",
                "truth_used_in_estimator": False,
                "target_position_allowed": False,
                "injected_error_label_allowed": False,
                "known_error_allowed": False,
            },
        },
    }


def build_branch_execution_contract(
    method_id: str,
    mode: str,
    role: str,
    reference_path: str | Path | None = None,
) -> dict[str, object]:
    """Describe exactly which role may estimate or apply a Gamma reference.

    This is deliberately a data contract, not a declaration that a production
    run succeeded.  The runner creates the reference artifact after the sole
    estimator role has produced auditable adapter rows; consumers must still
    validate the artifact and the production tap status before accepting a
    branch.
    """

    method = str(method_id)
    selected_mode = str(mode)
    selected_role = str(role)
    if method not in METHOD_IDS and method != "A0":
        raise ValueError(f"unknown production method: {method}")
    if selected_mode not in {"Mode-A", "Mode-B", "not_applicable"}:
        raise ValueError(f"unknown calibration mode: {selected_mode}")
    if method == "A0" and selected_mode != "not_applicable":
        raise ValueError("A0 only supports not_applicable mode")
    if selected_role not in {"OFF", "ON", "TO"}:
        raise ValueError(f"unknown paired-input role: {selected_role}")

    reference_fit_enabled = method in REFERENCE_FIT_METHOD_IDS
    if method == "C1" and selected_mode != "not_applicable":
        raise ValueError("C1 Gamma=1 ordinary subtraction only supports not_applicable mode")
    if selected_mode == "Mode-A":
        estimator_roles = ["OFF"] if reference_fit_enabled else []
        reference_source = "OFF_estimator_output" if reference_fit_enabled else "not_applicable"
        estimator_called = reference_fit_enabled and selected_role == "OFF"
        fixed_reference_required = reference_fit_enabled and selected_role in {"ON", "TO"}
    elif selected_mode == "Mode-B":
        estimator_roles = ["ON"] if reference_fit_enabled else []
        reference_source = "ON_estimator_output" if reference_fit_enabled else "not_applicable"
        estimator_called = reference_fit_enabled and selected_role == "ON"
        fixed_reference_required = reference_fit_enabled and selected_role in {"OFF", "TO"}
    else:
        estimator_roles = []
        reference_source = "not_applicable"
        estimator_called = False
        fixed_reference_required = False

    reference_value = (
        str(Path(reference_path).resolve())
        if reference_fit_enabled and reference_path is not None else None
    )
    return {
        "method_id": method,
        "mode": selected_mode,
        "role": selected_role,
        "estimator_input_roles": estimator_roles,
        "reference_source": reference_source,
        "reference_artifact": reference_value,
        "estimator_called": estimator_called,
        "evaluator_only": not estimator_called,
        "fixed_reference_required": fixed_reference_required,
        "reference_applied": fixed_reference_required and reference_value is not None,
        "truth_used_in_estimator": False,
        "target_position_allowed": False,
        "injected_error_label_allowed": False,
        "known_error_allowed": False,
        "fallback_to_current": False,
    }


def _parse_bool_marker(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_truth_marker(value: object) -> bool | None:
    """Parse the persisted estimator-truth marker without defaulting unknowns."""

    if isinstance(value, bool):
        return value
    marker = str(value).strip().lower()
    if marker in {"0", "false", "no", "off"}:
        return False
    if marker in {"1", "true", "yes", "on"}:
        return True
    return None


def validate_adapter_rows(
    rows: Sequence[Mapping[str, object]], *, min_support: int
) -> dict[str, object]:
    """Validate adapter provenance without replacing failures by Current.

    The returned fields intentionally retain the tap's actual status, truth
    marker, support and reason.  A missing/failed row is a hard evidence gap.
    """

    try:
        minimum = int(min_support)
    except (TypeError, ValueError):
        minimum = 0
    copied = [dict(row) for row in rows]
    if not copied:
        return {
            "status": "NOT_EVALUABLE",
            "truth_used_in_estimator": False,
            "support_count": 0,
            "min_support": minimum,
            "reason": "adapter_rows_missing",
            "fallback_to_current": False,
            "rows": [],
        }

    statuses = [str(row.get("status", "")).strip() for row in copied]
    support_counts: list[int] = []
    for row in copied:
        try:
            support_counts.append(int(row.get("support_count", 0)))
        except (TypeError, ValueError):
            support_counts.append(-1)
    reasons = [str(row.get("reason", "")).strip() for row in copied if str(row.get("reason", "")).strip()]
    first_reason = reasons[0] if reasons else None
    truth_values: list[bool] = []
    truth_marker_error: str | None = None
    for row in copied:
        if "truth_used_in_estimator" not in row:
            truth_marker_error = truth_marker_error or "truth_marker_missing"
            continue
        parsed_truth = _parse_truth_marker(row["truth_used_in_estimator"])
        if parsed_truth is None:
            truth_marker_error = truth_marker_error or "truth_marker_invalid"
            continue
        truth_values.append(parsed_truth)
    truth_used = any(truth_values)
    unsupported_status = next(
        (status for status in statuses if status != "OK"),
        None,
    )
    if truth_used:
        status = "NOT_EVALUABLE"
        reason = first_reason or "truth_used_in_estimator"
    elif truth_marker_error is not None:
        status = "NOT_EVALUABLE"
        reason = first_reason or truth_marker_error
    elif unsupported_status is not None:
        status = "NOT_EVALUABLE"
        reason = first_reason or f"adapter_status_{unsupported_status or 'missing'}"
    elif minimum <= 0:
        status = "NOT_EVALUABLE"
        reason = first_reason or "invalid_min_support"
    elif any(value < minimum for value in support_counts):
        status = "NOT_EVALUABLE"
        reason = first_reason or "support_below_minimum"
    else:
        status = "evaluable"
        reason = first_reason

    return {
        "status": status,
        "truth_used_in_estimator": truth_used,
        "support_count": sum(max(0, value) for value in support_counts),
        "support_counts": support_counts,
        "min_support": minimum,
        "reason": reason,
        "fallback_to_current": False,
        "statuses": statuses,
        "rows": copied,
    }


def aggregate_branch_cfar_geometry(paths: Sequence[str | Path]) -> dict[str, object]:
    """Aggregate exported valid/hit CUT geometry, never configured Pfa."""

    candidates = [Path(path).resolve() for path in paths if Path(path).is_file()]
    if not candidates:
        return {
            "status": "NOT_EVALUABLE",
            "valid_cut_count": 0,
            "hit_cut_count": 0,
            "tested_cut_hit_fraction": None,
            "cell_pfa": None,
            "pfa_status": "NOT_EVALUABLE",
            "pfa_reason": "cfar_geometry_diagnostics_missing",
            "reason": "cfar_geometry_diagnostics_missing",
            "definition": "false_cut_hit_count / valid_h0_cut_count",
            "source_paths": [],
            "rows": [],
        }
    try:
        from scripts.cfar_geometry_audit import load_geometry_rows

        rows = load_geometry_rows(candidates)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "NOT_EVALUABLE",
            "valid_cut_count": 0,
            "hit_cut_count": 0,
            "tested_cut_hit_fraction": None,
            "cell_pfa": None,
            "pfa_status": "NOT_EVALUABLE",
            "pfa_reason": "cfar_geometry_invalid",
            "reason": f"cfar_geometry_invalid:{exc}",
            "definition": "false_cut_hit_count / valid_h0_cut_count",
            "source_paths": [str(path) for path in candidates],
            "rows": [],
        }
    valid = sum(int(row["valid_cut_count"]) for row in rows)
    hits = sum(int(row["hit_cut_count"]) for row in rows)
    threshold = sum(int(row["threshold_test_count"]) for row in rows)
    status = "evaluable" if valid > 0 else "NOT_EVALUABLE"
    return {
        "status": status,
        "valid_cut_count": valid,
        "hit_cut_count": hits,
        "threshold_test_count": threshold,
        "tested_cut_hit_fraction": float(hits) / float(valid) if valid > 0 else None,
        "cell_pfa": None,
        "pfa_status": "NOT_EVALUABLE",
        "pfa_reason": "false_hits_and_target_free_cut_denominator_required",
        "reason": None if valid > 0 else "valid_cut_count_missing_or_non_positive",
        "definition": (
            "tested_cut_hit_fraction = sum(hit_cut_count) / sum(valid_cut_count); "
            "descriptive only, not empirical Pfa"
        ),
        "source_paths": [str(path) for path in candidates],
        "rows": rows,
    }


def _normalize_xml_value(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value).strip()


def audit_xml_fields(path: Path, expected: Mapping[str, object]) -> dict[str, object]:
    """Check method-specific XML values after the final XML is materialized."""

    candidate = Path(path).resolve()
    try:
        root = ET.parse(candidate).getroot()
    except (OSError, ET.ParseError) as exc:
        return {"status": "failed", "path": str(candidate), "reason": str(exc), "fields": {}}
    actual: dict[str, str] = {}
    for node in root.iter():
        tag = str(node.tag).rsplit("}", 1)[-1]
        if tag in expected:
            actual[tag] = (node.text or "").strip()
    missing = [key for key in expected if key not in actual]
    mismatches = {
        key: {"expected": _normalize_xml_value(value), "actual": actual.get(key)}
        for key, value in expected.items()
        if key in actual and actual[key] != _normalize_xml_value(value)
    }
    return {
        "status": "passed" if not missing and not mismatches else "failed",
        "path": str(candidate),
        "fields": actual,
        "missing": missing,
        "mismatches": mismatches,
    }


def build_downstream_compact_contract() -> dict[str, object]:
    """Freeze the causal and production-debug evidence layers."""

    return {
        "ON_minus_OFF": {
            "definition": "target causal transfer; compare ON against paired OFF",
            "required": True,
        },
        "TO": {
            "definition": "target-only evaluator; never an estimator input",
            "required": True,
        },
        "CFAR": {
            "definition": "GO-CFAR valid_cut_count/hit_cut_count geometry",
            "required": True,
        },
        "cluster": {
            "definition": "production cluster count/association layer",
            "required": True,
        },
        "protocol_detection": {
            "definition": "production detection CSV and protocol eligibility",
            "required": True,
        },
        "track": {
            "definition": "production TrackManager confirmed/matched output",
            "required": True,
        },
        "target_protection_rule": "causal_ON_minus_OFF_and_TO; never_ON_power_alone",
        "track_evidence": [
            "track_association_audit_v2",
            "track_states",
            "track_output_payloads",
            "id_switch_classification_or_NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG",
        ],
        "counterfactual_processing_rule": (
            "Mode-A uses the OFF-estimated reference for ON/TO; Mode-B uses the ON-estimated "
            "reference for OFF/TO; ON-minus-OFF requires the shared fixed-reference provenance"
        ),
    }


def _unique_values(values: object, name: str, *, positive: bool = False) -> list[float]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a list")
    result: list[float] = []
    for item in values:
        value = _finite_float(item, name)
        if positive and value <= 0.0:
            raise ValueError(f"{name} must contain positive values")
        if value not in result:
            result.append(value)
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


def _unique_ints(values: object, name: str) -> list[int]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a list")
    result: list[int] = []
    for item in values:
        try:
            value = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} contains an invalid seed") from exc
        if value not in result:
            result.append(value)
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


def _normalize_scene_parameters(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("scene_parameters must be an object")
    allowed = {
        "beam_id",
        "expected_bin",
        "range_m",
        "texture_sigma",
        "clutter_rho",
        "area_mean_power",
        "noise_power",
    }
    unknown = sorted(str(key) for key in value if str(key) not in allowed)
    if unknown:
        raise ValueError(f"unknown scene_parameters: {unknown}")
    result: dict[str, object] = {}
    for key, raw in value.items():
        name = str(key)
        if name in {"beam_id", "expected_bin"}:
            number = _finite_float(raw, f"scene_parameters.{name}")
            if not number.is_integer():
                raise ValueError(f"scene_parameters.{name} must be an integer")
            integer = int(number)
            if integer < (1 if name == "beam_id" else 0):
                raise ValueError(f"scene_parameters.{name} is out of range")
            result[name] = integer
            continue
        number = _finite_float(raw, f"scene_parameters.{name}")
        if name in {"range_m", "area_mean_power", "noise_power"} and number <= 0.0:
            raise ValueError(f"scene_parameters.{name} must be positive")
        if name == "texture_sigma" and number < 0.0:
            raise ValueError("scene_parameters.texture_sigma must be non-negative")
        if name == "clutter_rho" and not -1.0 <= number <= 1.0:
            raise ValueError("scene_parameters.clutter_rho must be in [-1, 1]")
        result[name] = number
    return result


def _case_matches_overrides(
    case: Mapping[str, object], overrides: Mapping[str, object] | None,
) -> bool:
    if not overrides:
        return True
    checks = {
        "delay_errors_ns": float(case["delay_error_ns"]),
        "seeds": int(case["seed"]),
        "target_velocities_mps": float(case["target_velocity_mps"]),
        "target_snr_db": float(case["target_snr_db"]),
    }
    for key, actual in checks.items():
        if key not in overrides or overrides[key] is None:
            continue
        values = overrides[key]
        if isinstance(values, (str, int, float)):
            values = [values]
        if actual not in {float(item) for item in values}:  # type: ignore[union-attr]
            return False
    return True


def resolve_selection(
    config: Mapping[str, object],
    mode: str,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Resolve explicit pilot/formal groups without manufacturing a matrix."""

    section = config.get(mode)
    if not isinstance(section, Mapping):
        raise ValueError(f"config is missing selection {mode!r}")
    selection_mode = str(section.get("selection_mode", ""))
    if not selection_mode or selection_mode == "cartesian":
        raise ValueError("selection must be an explicit targeted selection, not Cartesian")
    delays = _unique_values(section.get("delay_errors_ns"), f"{mode}.delay_errors_ns")
    seeds = _unique_ints(section.get("seeds"), f"{mode}.seeds")
    velocities = _unique_values(
        section.get("target_velocities_mps"), f"{mode}.target_velocities_mps", positive=True
    )
    snrs = _unique_values(section.get("target_snr_db"), f"{mode}.target_snr_db")
    period_count = int(section.get("period_count", 0))
    if period_count <= 0:
        raise ValueError(f"{mode}.period_count must be positive")
    groups = section.get("case_groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError(f"{mode}.case_groups must be a non-empty list")
    cases: list[dict[str, object]] = []
    for group in groups:
        if not isinstance(group, Mapping):
            raise ValueError(f"{mode}.case_groups contains a non-object")
        group_name = str(group.get("name", "")).strip()
        group_cases = group.get("cases")
        if not isinstance(group_cases, list) or not group_cases:
            raise ValueError(f"{mode} group {group_name!r} must have explicit cases")
        for raw_case in group_cases:
            if not isinstance(raw_case, Mapping):
                raise ValueError(f"{mode} group {group_name!r} contains a non-object case")
            case = {
                "group": group_name,
                "delay_error_ns": _finite_float(raw_case.get("delay_error_ns"), "delay_error_ns"),
                "seed": int(raw_case.get("seed")),
                "target_velocity_mps": _finite_float(
                    raw_case.get("target_velocity_mps"), "target_velocity_mps"
                ),
                "target_snr_db": _finite_float(raw_case.get("target_snr_db"), "target_snr_db"),
                "working_point": str(raw_case.get("working_point", "base")),
                "scene_parameters": _normalize_scene_parameters(raw_case.get("scene_parameters")),
            }
            if case["target_velocity_mps"] <= 0.0:
                raise ValueError("target_velocity_mps must be positive")
            if case["delay_error_ns"] not in delays:
                raise ValueError(f"case delay is not in {mode}.delay_errors_ns")
            if case["seed"] not in seeds:
                raise ValueError(f"case seed is not in {mode}.seeds")
            if case["target_velocity_mps"] not in velocities:
                raise ValueError(f"case velocity is not in {mode}.target_velocities_mps")
            if case["target_snr_db"] not in snrs:
                raise ValueError(f"case SNR is not in {mode}.target_snr_db")
            case["period_count"] = period_count
            if _case_matches_overrides(case, overrides):
                cases.append(case)
    if not cases:
        raise ValueError("selection overrides removed every explicit case")
    configured_product = len(delays) * len(seeds) * len(velocities) * len(snrs)
    return {
        "mode": mode,
        "selection_mode": selection_mode,
        "cartesian_full_matrix": False,
        "delay_errors_ns": delays,
        "seeds": seeds,
        "target_velocities_mps": velocities,
        "target_snr_db": snrs,
        "working_points": list(section.get("working_points", [])),
        "period_count": period_count,
        "cases": cases,
        "case_count": len(cases),
        "configured_cartesian_case_count": configured_product,
        "overrides": dict(overrides or {}),
    }


def evaluate_support(
    *,
    support_count: object,
    min_support: object,
    denominator: object,
    phase_coherence: object,
    coherence_threshold: float = DEFAULT_COHERENCE_THRESHOLD,
) -> dict[str, object]:
    """Apply the fail-closed support contract used by compact summaries."""

    try:
        support = int(support_count)
        minimum = int(min_support)
        denom = float(denominator)
        coherence = float(phase_coherence)
    except (TypeError, ValueError):
        support, minimum, denom, coherence = -1, 1, math.nan, math.nan
    reason = None
    if support < 0 or minimum <= 0:
        reason = "support_metadata_invalid"
    elif support < minimum:
        reason = "support_below_minimum"
    elif not math.isfinite(denom) or denom <= 0.0:
        reason = "support_denominator_invalid"
    elif not math.isfinite(coherence) or coherence < float(coherence_threshold):
        reason = "phase_coherence_below_threshold"
    if reason is not None:
        return {
            "status": "NOT_EVALUABLE",
            "support_count": max(0, support),
            "min_support": minimum,
            "denominator": denom if math.isfinite(denom) else None,
            "phase_coherence": coherence if math.isfinite(coherence) else None,
            "reason": reason,
            "fallback_method": None,
            "fallback_to_current": False,
        }
    return {
        "status": "evaluable",
        "support_count": support,
        "min_support": minimum,
        "denominator": denom,
        "phase_coherence": coherence,
        "reason": None,
        "fallback_method": None,
        "fallback_to_current": False,
    }


def validate_compact_evidence_root(root: Path) -> dict[str, object]:
    """Require exactly the six tracked evidence files and nothing else."""

    candidate = Path(root)
    if not candidate.is_dir():
        raise ValueError(f"compact evidence root does not exist: {candidate}")
    names = sorted(path.name for path in candidate.iterdir())
    expected = sorted(COMPACT_EVIDENCE_FILES)
    if names != expected:
        raise ValueError(
            f"compact evidence allow-list violation: actual={names}, expected={expected}"
        )
    return {"status": "passed", "files": list(COMPACT_EVIDENCE_FILES)}


def source_snapshot() -> dict[str, object]:
    """Record tracked source identity without enumerating user output roots."""

    commit_proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    status_proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    commit = commit_proc.stdout.strip() or None
    tracked_status = status_proc.stdout.strip()
    return {
        "commit": commit,
        "dirty": bool(tracked_status),
        "dirty_tracked": bool(tracked_status),
        "tracked_status": tracked_status,
        "scope": "tracked_source_only; existing untracked output roots are not scanned",
    }


def require_clean_tracked_source(snapshot: Mapping[str, object], *, mode: str) -> dict[str, object]:
    if mode in {"formal", "e1_pilot"} and bool(
        snapshot.get("dirty_tracked", snapshot.get("dirty", False))
    ):
        raise RuntimeError(f"tracked source is dirty; {mode} production run refuses to start")
    return {"status": "passed", "mode": mode, "commit": snapshot.get("commit")}


def _run_logged(
    command: Sequence[object],
    log_path: Path,
    *,
    timeout_s: float = 1800.0,
    env: Mapping[str, str] | None = None,
) -> tuple[int, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    returncode = 127
    with log_path.open("w", encoding="utf-8") as stream:
        stream.write(f"$ {shlex.join(str(item) for item in command)}\n")
        stream.flush()
        binary_pin: tuple[Path, str, str] | None = None
        try:
            if command:
                executable = Path(str(command[0])).resolve()
                if executable == SIMULATOR.resolve():
                    expected = os.environ.get("PGRCC_EXPECTED_STAGE2_SIMULATOR_SHA256")
                    before = _sha256(executable)
                    _assert_artifact_sha256(executable, expected or before, "Stage2 simulator")
                    binary_pin = (executable, expected or before, "Stage2 simulator")
                    stream.write(f"[binary_sha256_before]={before}\n")
            completed = subprocess.run(
                [str(item) for item in command],
                cwd=ROOT,
                stdout=stream,
                stderr=subprocess.STDOUT,
                env=dict(env) if env is not None else None,
                timeout=float(timeout_s),
                check=False,
            )
            returncode = int(completed.returncode)
        except FileNotFoundError as exc:
            stream.write(f"\n[file_not_found] {exc}\n")
        except subprocess.TimeoutExpired:
            returncode = 124
            stream.write("\n[timeout_expired]=true\n")
        finally:
            if binary_pin is not None:
                after = _assert_artifact_sha256(*binary_pin)
                stream.write(f"[binary_sha256_after]={after}\n")
        elapsed = time.monotonic() - start
        stream.write(f"\n[exit_code]={returncode}\n[elapsed_sec]={elapsed:.3f}\n")
    return returncode, time.monotonic() - start


def _probe_nvidia_smi() -> dict[str, object]:
    try:
        result = subprocess.run(
            ["nvidia-smi"], capture_output=True, text=True, timeout=15.0, check=False
        )
        return {
            "command": "nvidia-smi",
            "returncode": int(result.returncode),
            "available": result.returncode == 0,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "command": "nvidia-smi",
            "returncode": 127,
            "available": False,
            "stdout": "",
            "stderr": str(exc),
        }


def _disk_status(path: Path) -> dict[str, object]:
    usage = shutil.disk_usage(Path(path).resolve().parent)
    return {
        "path": str(Path(path).resolve().parent),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }


def _build_identity() -> dict[str, object]:
    return {
        "simulate_stage2_statistical": _artifact_identity(SIMULATOR),
        "GMTI_pipe_core": _artifact_identity(PIPE),
    }


def _pin_build_identity() -> dict[str, object]:
    """Require runnable build artifacts and pin their identities for the run."""

    identity = _build_identity()
    expected_env = {
        "simulate_stage2_statistical": "PGRCC_EXPECTED_STAGE2_SIMULATOR_SHA256",
        "GMTI_pipe_core": "PGRCC_EXPECTED_GMTI_PIPE_CORE_SHA256",
    }
    for name, env_name in expected_env.items():
        item = identity[name]
        if not isinstance(item, Mapping) or item.get("exists") is not True:
            raise BinaryIdentityMismatch(f"{name} identity mismatch: executable is missing")
        path = Path(str(item["path"]))
        digest = str(item.get("sha256") or "")
        if not os.access(path, os.X_OK):
            raise BinaryIdentityMismatch(f"{name} identity mismatch: not executable: {path}")
        configured_expected = os.environ.get(env_name)
        _assert_artifact_sha256(path, configured_expected or digest, name)
        os.environ[env_name] = digest
    return identity


def _scene_template(config: Mapping[str, object]) -> dict[str, object]:
    raw = config.get("scenario_template")
    path = Path(str(raw)) if isinstance(raw, str) else TEMPLATE
    if not path.is_absolute():
        path = ROOT / path
    return _read_json(path.resolve())


def _point_registry(config: Mapping[str, object]) -> dict[str, dict[str, object]]:
    registry: dict[str, dict[str, object]] = {}
    for section in config.values():
        if not isinstance(section, Mapping):
            continue
        for group in section.get("case_groups", []):
            if not isinstance(group, Mapping):
                continue
            for case in group.get("cases", []):
                if isinstance(case, Mapping):
                    name = str(case.get("working_point", "base"))
                    registry.setdefault(name, {
                        "name": name,
                        "beam_id": 50,
                        "expected_bin": 2048,
                        "range_m": 85000.0,
                        "texture_sigma": 0.25,
                        "clutter_rho": 0.98,
                    })
    return registry


def _scene_identity(config: Mapping[str, object], case: Mapping[str, object], period_count: int) -> dict[str, object]:
    override = case.get("scene_identity_override")
    if isinstance(override, Mapping):
        identity = copy.deepcopy(dict(override))
        try:
            override_seed = int(identity["seed"])
            override_period_count = int(identity["period_count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("scene_identity_override must declare integer seed and period_count") from exc
        if override_seed != int(case["seed"]):
            raise ValueError("scene_identity_override seed does not match the case seed")
        if override_period_count != int(period_count):
            raise ValueError("scene_identity_override period_count does not match the case")
        return identity
    points = _point_registry(config)
    point = copy.deepcopy(points.get(str(case["working_point"]), points.get("base", {})))
    point.update(_normalize_scene_parameters(case.get("scene_parameters")))
    return {
        "seed": int(case["seed"]),
        "period_count": int(period_count),
        "beam_id": int(point.get("beam_id", 50)),
        "expected_bin": int(point.get("expected_bin", 2048)),
        "range_m": float(point.get("range_m", 85000.0)),
        "texture_sigma": float(point.get("texture_sigma", 0.25)),
        "clutter_rho": float(point.get("clutter_rho", 0.98)),
        "target_velocity_mps": float(case["target_velocity_mps"]),
        "target_snr_db": float(case["target_snr_db"]),
        "target": {"target_id": "PGRCC_DELAY_PILOT_TARGET", "enabled": True},
        "clutter": {"mean_power": float(point.get("area_mean_power", 0.02))},
        "noise": {"noise_power": float(point.get("noise_power", 0.001))},
        "production": {"identity": "stage2_production_delay_pilot_v1"},
    }


def _missing_branches(
    reason: str,
    method_contract: Sequence[Mapping[str, object]],
    calibration_modes: Sequence[str] = ("Mode-A", "Mode-B"),
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for method in method_contract:
        method_id = str(method["method_id"])
        modes = tuple(calibration_modes) if method_id in REFERENCE_FIT_METHOD_IDS else ("not_applicable",)
        for mode in modes:
            for role in ("OFF", "ON", "TO"):
                contract = build_branch_execution_contract(method_id, mode, role)
                rows.append({
                    "method_id": method_id,
                    "mode": mode,
                    "role": role,
                    "status": "NOT_EVALUABLE",
                    "reason": reason,
                    "truth_used_in_estimator": False,
                    "fallback_to_current": False,
                    "execution_contract": contract,
                })
    return rows


def _required_prepared_families(
    method_contract: Sequence[Mapping[str, object]],
) -> set[str]:
    """Resolve input families actually consumed by the selected methods.

    The ideal A0 baseline is always executed. Other condition families are
    prepared only when their corresponding method is selected; in particular,
    equivalent-calibration pilots must not run blind or known-delay correction.
    """

    required = {"ideal"}
    for method in method_contract:
        method_id = str(method.get("method_id", ""))
        if method_id in {"P1", "P2"}:
            required.add("blind")
        elif method_id in {"PK", "PKR"}:
            required.add("known")
        else:
            required.add("none")
    return required


def _zero_delay_stage2_reuse_aliases(
    scenario_paths: Mapping[str, Path], delay_error_ns: float
) -> dict[str, str]:
    """Reuse duplicate A1 OFF/TO Stage2 inputs only for a zero-delay pair.

    The generated role documents must match after removing only output and
    pairing labels.  Any physical/configuration difference keeps the Stage2
    invocation, and non-zero delay cases never use these aliases.
    """

    if float(delay_error_ns) != 0.0:
        return {}

    audit_fields = {
        "case_id",
        "output_dir",
        "stage1_condition",
        "stage1_pair_id",
        "stage1_background_source_condition",
        "stage1_pairing",
        "pairing_provenance",
    }

    def physical_payload(key: str) -> dict[str, object] | None:
        path = scenario_paths.get(key)
        if path is None or not Path(path).is_file():
            return None
        payload = _read_json(Path(path))
        return {name: value for name, value in payload.items() if name not in audit_fields}

    aliases: dict[str, str] = {}
    for alias, source in (("A1_OFF", "A0_OFF"), ("A1_TO", "A0_TO")):
        alias_payload = physical_payload(alias)
        source_payload = physical_payload(source)
        if alias_payload is not None and alias_payload == source_payload:
            aliases[alias] = source
    return aliases


def _prepare_raw_input_record(
    role_paths: Mapping[str, Sequence[Path]],
    *,
    correction: str,
    correction_source: str,
    delay_truth_ns: float,
) -> dict[str, object]:
    return {
        "condition": "A1_Current_unknown_error",
        "correction": correction,
        "correction_source": correction_source,
        "delay_truth_ns": float(delay_truth_ns),
        "delay_estimate_ns": None,
        "correction_applied": False,
        "truth_used_in_estimator": False,
        "truth_used_to_apply_correction": False,
        "period_paths_by_role": {role: list(paths) for role, paths in role_paths.items()},
        "status": "evaluable",
    }


def _collect_branch_artifacts(record: Mapping[str, object]) -> dict[str, object]:
    result_dir = Path(str(record.get("result_dir", ""))) if record.get("result_dir") else None
    debug_dir = Path(str(record.get("track_debug_dir", ""))) if record.get("track_debug_dir") else None
    patterns = {
        "adapter_csv": "production_calibration_adapter.csv",
        "reference_gamma_csv": "production_calibration_reference_gamma.csv",
        "csi_metric_tap_summary": "csi_metric_tap_summary.csv",
        "cfar_geometry": "*cfar*geometry*.csv",
        "detection": "detection_results_GMTI*.csv",
        "track_frames": "track_frames.csv",
        "track_association": "track_association_audit_v2.csv",
        "track_states": "track_states.csv",
        "track_payloads": "track_output_payloads.csv",
    }
    paths: dict[str, list[str]] = {key: [] for key in patterns}
    for key, pattern in patterns.items():
        roots = [path for path in (result_dir, debug_dir) if path is not None and path.is_dir()]
        for root in roots:
            for candidate in root.rglob(pattern):
                if candidate.is_file():
                    paths[key].append(str(candidate.resolve()))
        paths[key] = sorted(set(paths[key]))
    return paths


def _adapter_rows(paths: Sequence[str], method_id: str, role: str) -> list[dict[str, object]]:
    return _read_csv_rows(paths, method_id, role)


def _read_csv_rows(paths: Sequence[str], method_id: str, role: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    copied: dict[str, object] = dict(row)
                    copied.update({"method_id": method_id, "role": role, "source_path": str(path)})
                    rows.append(copied)
        except OSError:
            continue
    return rows


def _reference_gamma_rows(
    paths: Sequence[str], method_id: str, role: str
) -> list[dict[str, object]]:
    """Read raw per-GammaSummary rows from the dedicated reference artifact."""

    return _read_csv_rows(paths, method_id, role)


REFERENCE_GAMMA_REQUIRED_FIELDS = (
    "period_id",
    "result_id",
    "beam_id",
    "method",
    "group_id",
    "az_index",
    "range_start",
    "range_end",
    "status",
    "support_count",
    "gamma_real",
    "gamma_imag",
    "truth_used_in_estimator",
)


def validate_reference_gamma_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    method_id: str,
    min_support: int,
    expected_method: str | None = None,
) -> dict[str, object]:
    """Validate strict raw per-group Gamma rows for fixed-reference replay.

    ``method_id`` identifies the pilot branch (for example ``C3``), while the
    production CSV ``method`` column records the method family (for example
    ``robust_ddc``).  Callers that have both identities must pass the latter as
    ``expected_method`` so the two namespaces are not conflated.
    """

    try:
        minimum = int(min_support)
    except (TypeError, ValueError):
        minimum = 0
    expected_method_value = str(expected_method or method_id).strip()
    copied = [dict(row) for row in rows]
    statuses = [str(row.get("status", "")).strip() for row in copied]
    support_counts: list[int] = []
    for row in copied:
        try:
            support_counts.append(int(str(row.get("support_count", "")).strip()))
        except (TypeError, ValueError):
            support_counts.append(-1)
    base = {
        "truth_used_in_estimator": False,
        "support_count": sum(max(0, value) for value in support_counts),
        "support_counts": support_counts,
        "min_support": minimum,
        "statuses": statuses,
        "rows": copied,
        "fallback_to_current": False,
    }
    if not copied:
        return {**base, "status": "NOT_EVALUABLE", "reason": "reference_groups_missing"}
    if minimum <= 0:
        return {**base, "status": "NOT_EVALUABLE", "reason": "invalid_min_support"}

    for row in copied:
        for field in REFERENCE_GAMMA_REQUIRED_FIELDS:
            value = row.get(field)
            if value is None or not str(value).strip():
                return {
                    **base,
                    "status": "NOT_EVALUABLE",
                    "reason": f"reference_field_missing:{field}",
                }
        parsed_ints: dict[str, int] = {}
        for field in (
            "period_id",
            "beam_id",
            "az_index",
            "range_start",
            "range_end",
            "support_count",
        ):
            try:
                parsed = int(str(row[field]).strip())
            except (TypeError, ValueError):
                return {
                    **base,
                    "status": "NOT_EVALUABLE",
                    "reason": f"reference_field_invalid:{field}",
                }
            if parsed < -(2**31) or parsed > 2**31 - 1:
                return {
                    **base,
                    "status": "NOT_EVALUABLE",
                    "reason": f"reference_field_invalid:{field}",
                }
            parsed_ints[field] = parsed
        if parsed_ints["period_id"] <= 0:
            return {
                **base,
                "status": "NOT_EVALUABLE",
                "reason": "reference_field_invalid:period_id",
            }
        expected_result_id = f"GMTI{parsed_ints['period_id']:02d}"
        if str(row["result_id"]).strip() != expected_result_id:
            return {
                **base,
                "status": "NOT_EVALUABLE",
                "reason": "reference_result_id_mismatch",
            }
        expected_group_id = (
            f"az_{parsed_ints['az_index']}_range_"
            f"{parsed_ints['range_start']}_{parsed_ints['range_end']}"
        )
        if str(row["group_id"]).strip() != expected_group_id:
            return {
                **base,
                "status": "NOT_EVALUABLE",
                "reason": "reference_group_id_mismatch",
            }
        if str(row["method"]).strip() != expected_method_value:
            return {
                **base,
                "status": "NOT_EVALUABLE",
                "reason": "reference_method_mismatch",
            }
        if str(row["status"]).strip() != "OK":
            return {
                **base,
                "status": "NOT_EVALUABLE",
                "reason": "reference_status_not_ok",
            }
        truth = row["truth_used_in_estimator"]
        truth_is_false = (
            isinstance(truth, bool) and not truth
        ) or str(truth).strip().lower() == "false"
        if not truth_is_false:
            return {
                **base,
                "status": "NOT_EVALUABLE",
                "reason": "truth_used_in_estimator",
            }
        if int(str(row["support_count"]).strip()) < minimum:
            return {
                **base,
                "status": "NOT_EVALUABLE",
                "reason": "support_below_minimum",
            }
        for field in ("gamma_real", "gamma_imag"):
            try:
                value = float(str(row[field]).strip())
            except (TypeError, ValueError):
                return {
                    **base,
                    "status": "NOT_EVALUABLE",
                    "reason": f"reference_field_invalid:{field}",
                }
            if not math.isfinite(value):
                return {
                    **base,
                    "status": "NOT_EVALUABLE",
                    "reason": f"reference_field_invalid:{field}",
                }
        phase = row.get("phase_coherence")
        if phase is not None and str(phase).strip():
            phase_text = str(phase).strip().lower()
            if phase_text != "nan":
                try:
                    phase_value = float(phase_text)
                except (TypeError, ValueError):
                    return {
                        **base,
                        "status": "NOT_EVALUABLE",
                        "reason": "reference_field_invalid:phase_coherence",
                    }
                if not math.isfinite(phase_value):
                    return {
                        **base,
                        "status": "NOT_EVALUABLE",
                        "reason": "reference_field_invalid:phase_coherence",
                    }
    return {**base, "status": "evaluable", "reason": None}


def _compact_reference_validation(validation: Mapping[str, object]) -> dict[str, object]:
    """Keep validation status/counts without embedding raw per-group rows."""

    return {
        str(key): value
        for key, value in validation.items()
        if key not in {"rows", "support_counts", "statuses"}
    }


def compact_reference_gamma_summary(
    rows: Sequence[Mapping[str, object]],
    validation: Mapping[str, object],
    *,
    source_paths: Sequence[str],
) -> dict[str, object]:
    """Summarize raw Gamma rows while retaining their external provenance."""

    real_values: list[float] = []
    imag_values: list[float] = []
    abs_values: list[float] = []
    coherence_values: list[float] = []
    for row in rows:
        try:
            real = float(row.get("gamma_real"))
            imag = float(row.get("gamma_imag"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(real) and math.isfinite(imag):
            real_values.append(real)
            imag_values.append(imag)
            abs_values.append(math.hypot(real, imag))
        try:
            coherence = float(row.get("phase_coherence"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(coherence):
            coherence_values.append(coherence)

    def _mean(values: Sequence[float]) -> float | None:
        return sum(values) / len(values) if values else None

    return {
        "status": validation.get("status", "NOT_EVALUABLE"),
        "reason": validation.get("reason"),
        "truth_used_in_estimator": validation.get("truth_used_in_estimator", False),
        "fallback_to_current": validation.get("fallback_to_current", False),
        "row_count": len(rows),
        "support_count": validation.get("support_count", 0),
        "min_support": validation.get("min_support"),
        "gamma_real_mean": _mean(real_values),
        "gamma_imag_mean": _mean(imag_values),
        "gamma_abs_mean": _mean(abs_values),
        "phase_coherence_mean": _mean(coherence_values),
        "source_paths": sorted({str(path) for path in source_paths}),
    }


def _result_period_id(value: object) -> int:
    text = str(value or "")
    digits = "".join(char for char in text if char.isdigit())
    try:
        return int(digits) if digits else -1
    except ValueError:
        return -1


def write_reference_gamma_artifact(
    rows: Sequence[Mapping[str, object]],
    destination: Path,
    *,
    method_id: str,
    min_support: int,
    expected_method: str | None = None,
) -> dict[str, object]:
    """Persist raw per-GammaSummary rows for a later fixed-reference run."""

    validation = validate_reference_gamma_rows(
        rows,
        method_id=method_id,
        min_support=min_support,
        expected_method=expected_method,
    )
    if validation["status"] != "evaluable":
        return {
            "status": "NOT_EVALUABLE",
            "reason": validation.get("reason", "reference_gamma_rows_invalid"),
            "fallback_to_current": False,
            "validation": validation,
            "path": str(Path(destination).resolve()),
        }
    fields = [
        "period_id",
        "result_id",
        "beam_id",
        "method",
        "group_id",
        "az_index",
        "range_start",
        "range_end",
        "status",
        "support_count",
        "phase_coherence",
        "gamma_real",
        "gamma_imag",
        "truth_used_in_estimator",
        "reason",
    ]
    output_rows = [
        {field: row.get(field, "") for field in fields}
        for row in rows
    ]
    if not output_rows:
        return {
            "status": "NOT_EVALUABLE",
            "reason": "reference_groups_missing",
            "fallback_to_current": False,
            "validation": validation,
            "path": str(Path(destination).resolve()),
        }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output_rows)
    return {
        "status": "passed",
        "reason": None,
        "fallback_to_current": False,
        "validation": validation,
        "path": str(destination.resolve()),
        "row_count": len(output_rows),
        "sha256": _sha256(destination),
        "grouping": "period_id/beam_id/group_id",
    }


def _method_xml(
    case_root: Path,
    source_xml: Path,
    method: Mapping[str, object],
    production: Mapping[str, object],
    *,
    layout: Mapping[str, object],
    mode: str = "not_applicable",
    role: str = "ALL",
    reference_path: Path | None = None,
) -> dict[str, object]:
    method_id = str(method["method_id"])
    destination = case_root / "method_xml" / f"{method_id}_{mode}_{role}.xml"
    enable = bool(method["research_calibration_enable"])
    method_name = str(method["research_calibration_method"])
    band = int(production.get("range_band_bins", 6)) if method_name == "robust_ddc_rb" else 0
    overrides = {
        **_production_range_overrides(production),
        "research_calibration_enable": "true" if enable else "false",
        "research_calibration_method": method_name,
        "research_calibration_min_support": int(production.get("min_support", 8)),
        "research_calibration_range_band_bins": band,
        "research_calibration_robust_phase_threshold_rad": float(
            production.get("robust_phase_threshold_rad", 0.35)
        ),
        "research_calibration_mode": str(mode),
        "research_calibration_reference_gamma_csv": (
            str(Path(reference_path).resolve()) if reference_path is not None else ""
        ),
        "csi_metrics_enable": "true",
        "csi_metrics_dump_power_maps": (
            "true" if bool(production.get("csi_metrics_dump_power_maps", True)) else "false"
        ),
        "csi_metrics_dump_intermediate_maps": (
            "true" if bool(production.get("csi_metrics_dump_intermediate_maps", True)) else "false"
        ),
        "csi_metrics_beam_id": int(layout["beam_id"]),
    }
    command: list[object] = [
        sys.executable,
        CONFIGURE_XML,
        "--input",
        source_xml,
        "--output",
        destination,
    ]
    for key, value in overrides.items():
        command.extend(["--set", f"{key}={value}"])
    rc, elapsed = _run_logged(command, destination.with_suffix(".configure.log"), timeout_s=60.0)
    return {
        "method_id": method_id,
        "path": str(destination),
        "sha256": _sha256(destination) if destination.is_file() else None,
        "overrides": overrides,
        "returncode": rc,
        "elapsed_sec": elapsed,
        "status": "passed" if rc == 0 else "failed",
        "log": str(destination.with_suffix(".configure.log")),
    }


def _materialize_input_dir(root: Path, paths: Sequence[Path]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(paths):
        destination = root / f"period_{index:04d}.bin"
        if destination.exists() or destination.is_symlink():
            raise RuntimeError(f"refuse to overwrite production input link: {destination}")
        destination.symlink_to(Path(source).resolve())
    return root


def _case_manifest_base(config: Mapping[str, object], case: Mapping[str, object], output_root: Path) -> dict[str, object]:
    case_id = (
        f"{case.get('working_point', 'base')}"
        f"__seed_{case.get('seed')}"
        f"__v_{case.get('target_velocity_mps')}"
        f"__snr_{case.get('target_snr_db')}"
        f"__delay_{case.get('delay_error_ns')}ns"
    )
    return {
        "schema_version": 1,
        "stage": config["stage"],
        "case": dict(case),
        "case_id": case_id,
        "output_root": str(output_root),
        "input_contract": build_paired_input_contract(),
        "truth_used_in_estimator": False,
        "algorithmic_results_claimed": False,
        "status": "not_started",
    }


def estimate_blind_delay(
    delay_runner: object, periods: Sequence[Path], layout: Mapping[str, object]
) -> dict[str, object]:
    """Use the truth-blind Stage1 estimator without materializing a full scan."""

    chunks = delay_runner._iter_fused_packets_from_periods(periods, layout)  # type: ignore[attr-defined]
    from scripts.delay_stage1_core import delay_method_suite_streaming

    rows = delay_method_suite_streaming(chunks, float(layout["fs_hz"]))
    selected_delay, selected_method = delay_runner._select_delay_estimate(rows)  # type: ignore[attr-defined]
    return {
        "status": "estimated" if selected_delay is not None else "failed",
        "method_rows": rows,
        "selected_method": selected_method,
        "selected_delay_ns": selected_delay,
        "input_role": "OFF=C+N",
        "input_streaming": True,
        "truth_used_in_estimator": False,
    }


def prepare_blind_delay_correction(
    estimator: Mapping[str, object], *, delay_truth_ns: float
) -> dict[str, object]:
    """Translate the public blind-estimator result into correction metadata."""

    selected_delay = estimator.get("selected_delay_ns")
    try:
        estimate = float(selected_delay)
    except (TypeError, ValueError):
        estimate = math.nan
    if estimator.get("status") == "estimated" and math.isfinite(estimate):
        return {
            "condition": "A3_Blind_target_free_estimated_correction",
            "correction": "raw_channel_2_fractional_delay",
            "correction_source": "target_free_OFF_phase_vs_fast_frequency",
            "delay_truth_ns": float(delay_truth_ns),
            "delay_estimate_ns": estimate,
            "correction_applied": True,
            "truth_used_in_estimator": False,
            "truth_used_to_apply_correction": False,
            "status": "evaluable",
        }
    return {
        "condition": "A3_Blind_target_free_estimated_correction",
        "correction": "raw_channel_2_fractional_delay",
        "correction_source": "target_free_OFF_phase_vs_fast_frequency",
        "delay_truth_ns": float(delay_truth_ns),
        "delay_estimate_ns": None,
        "correction_applied": False,
        "truth_used_in_estimator": False,
        "truth_used_to_apply_correction": False,
        "status": "NOT_EVALUABLE",
        "reason": "target_free_OFF_delay_estimate_missing; refusing Current fallback",
    }


def aggregate_case_residual_closures(
    cases: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Require every executed case to carry auditable A0/PK/PKR CSI closure."""

    required = ("A0", "PK", "PKR")
    source_paths: dict[str, list[str]] = {condition: [] for condition in required}
    per_case: dict[str, dict[str, object]] = {}
    for case in cases:
        case_id = str(case.get("case_id", ""))
        closure = case.get("residual_closure")
        if not isinstance(closure, Mapping):
            return {
                "status": "NOT_EVALUABLE",
                "reason": f"case_residual_closure_missing:{case_id}",
                "actual_production_artifacts": False,
                "source_paths": source_paths,
                "per_case": per_case,
            }
        per_case[case_id] = dict(closure)
        paths = closure.get("source_paths")
        if (
            closure.get("status") != "passed"
            or closure.get("actual_production_artifacts") is not True
            or not isinstance(paths, Mapping)
            or any(not paths.get(condition) for condition in required)
        ):
            return {
                "status": "NOT_EVALUABLE",
                "reason": f"case_residual_closure_not_evaluable:{case_id}",
                "actual_production_artifacts": False,
                "source_paths": source_paths,
                "per_case": per_case,
            }
        for condition in required:
            source_paths[condition].extend(str(path) for path in paths[condition])
    if not cases:
        return {
            "status": "NOT_EVALUABLE",
            "reason": "case_residual_closure_missing:no_cases",
            "actual_production_artifacts": False,
            "source_paths": source_paths,
            "per_case": per_case,
        }
    return {
        "status": "passed",
        "actual_production_artifacts": True,
        "source_paths": source_paths,
        "per_case": per_case,
    }


def _run_case(
    config: Mapping[str, object],
    case: Mapping[str, object],
    output_root: Path,
    method_contract: Sequence[Mapping[str, object]],
    *,
    input_mode: str,
    skip_cuda: bool,
    calibration_modes: Sequence[str] = ("Mode-A", "Mode-B"),
) -> dict[str, object]:
    """Run one explicit case; all raw outputs stay below ``output_root``."""

    from scripts import run_delay_stage1_formal as delay_runner
    from scripts import run_track_manager_e2e as e2e

    case_name = (
        f"{str(case['working_point'])}__seed_{int(case['seed'])}"
        f"__v_{float(case['target_velocity_mps']):g}"
        f"__snr_{float(case['target_snr_db']):g}"
        f"__delay_{float(case['delay_error_ns']):g}ns"
    ).replace("-", "m").replace(".", "d")
    case_root = output_root / "cases" / case_name
    case_root.mkdir(parents=True, exist_ok=False)
    manifest = _case_manifest_base(config, case, case_root)
    production_config = config.get("production")
    if not isinstance(production_config, Mapping):
        raise ValueError("pilot production section must be an object")
    period_count = int(case.get("period_count", production_config.get("period_count", 5)))
    manifest["scene_identity"] = _scene_identity(config, case, period_count)
    manifest["pair_identity"] = {
        "seed": int(case["seed"]),
        "background_id": str((case_root / "scenes" / "A0_background").resolve()),
        "target_id": "PGRCC_DELAY_PILOT_TARGET",
        "delay_error_ns": float(case["delay_error_ns"]),
        "production_identity": "stage2_production_delay_pilot_v1",
    }
    manifest["selected_methods"] = [str(method["method_id"]) for method in method_contract]
    manifest["selected_calibration_modes"] = list(calibration_modes)
    if skip_cuda:
        manifest.update({
            "status": "skip_cuda",
            "reason": "contract_only_no_simulator_or_production_execution",
            "algorithmic_results_claimed": False,
            "method_branches": _missing_branches(
                "skip_cuda_contract_only", method_contract, calibration_modes
            ),
        })
        _write_json(case_root / "case_manifest.json", manifest)
        return manifest

    scenario_base = _scene_template(config)
    scene_variants = delay_runner.build_scene_variants(
        scenario_base,
        manifest["scene_identity"],
        float(case["delay_error_ns"]),
        case_root / "scenes",
        allow_target_free=case.get("allow_target_free_h0") is True,
    )
    manifest["allow_target_free_h0"] = case.get("allow_target_free_h0") is True
    manifest["scene_variants"] = {key: str(path) for key, path in scene_variants.items()}
    simulation: dict[str, dict[str, object]] = {}
    simulation_order = ["A0_ON", "A0_OFF", "A0_TO", "A1_ON", "A1_OFF", "A1_TO"]
    simulation_aliases = _zero_delay_stage2_reuse_aliases(
        scene_variants, float(case["delay_error_ns"])
    )
    for key in simulation_order:
        scenario_path = scene_variants[key]
        output_dir = delay_runner._scenario_output_dir(scenario_path)
        if key in simulation_aliases:
            source_key = simulation_aliases[key]
            source_run = simulation[source_key]
            simulation[key] = {
                "scenario": str(scenario_path),
                "scenario_sha256": _sha256(scenario_path),
                "output_dir": source_run["output_dir"],
                "returncode": 0,
                "elapsed_sec": 0.0,
                "status": "reused",
                "command": [],
                "reused_from": source_key,
                "executed_scenario": source_run["scenario"],
                "executed_scenario_sha256": source_run["scenario_sha256"],
                "reuse_basis": "zero_delay_normalized_scenario_equivalence",
            }
            manifest["simulation"] = simulation
            continue
        rc, elapsed = _run_logged(
            [SIMULATOR, "--config", scenario_path],
            case_root / "scenes" / "logs" / f"{key}.simulate.log",
            timeout_s=1800.0,
        )
        simulation[key] = {
            "scenario": str(scenario_path),
            "scenario_sha256": _sha256(scenario_path),
            "output_dir": str(output_dir),
            "returncode": rc,
            "elapsed_sec": elapsed,
            "status": "passed" if rc == 0 else "failed",
            "command": [str(SIMULATOR), "--config", str(scenario_path)],
        }
        manifest["simulation"] = simulation
        if rc != 0:
            manifest.update({
                "status": "stage2_failed",
                "reason": f"stage2_simulation_failed:{key}",
                "method_branches": _missing_branches(
                    f"stage2_simulation_failed:{key}", method_contract, calibration_modes
                ),
            })
            _write_json(case_root / "case_manifest.json", manifest)
            return manifest
        if key == "A0_ON":
            report_path = output_dir / "reports" / "stage2_simulation_report.md"
            try:
                report_text = report_path.read_text(encoding="utf-8")
                scenario = _read_json(scenario_path)
                clutter_gate = delay_runner.audit_stage2_area_clutter_report(report_text, scenario)
            except (OSError, ValueError) as exc:
                clutter_gate = {
                    "status": "NOT_EVALUABLE",
                    "reasons": [f"stage2_area_report_unavailable:{exc}"],
                }
            clutter_gate["report_path"] = str(report_path)
            clutter_gate["report_sha256"] = _sha256(report_path) if report_path.is_file() else None
            clutter_gate["scenario_path"] = str(scenario_path.resolve())
            clutter_gate["scenario_sha256"] = _sha256(scenario_path)
            manifest["area_clutter_gate"] = clutter_gate
            if clutter_gate.get("status") != "passed":
                manifest.update({
                    "status": "clutter_gate_failed",
                    "reason": "stage2_area_clutter_not_proven",
                    "method_branches": _missing_branches(
                        "stage2_area_clutter_not_proven", method_contract, calibration_modes
                    ),
                })
                _write_json(case_root / "case_manifest.json", manifest)
                return manifest

    scenario = _read_json(scene_variants["A0_ON"])
    layout = e2e._protocol_layout_from_scenario(scenario)
    manifest["protocol_layout"] = layout
    scene_periods: dict[str, list[Path]] = {}
    for key in simulation_order:
        output_dir = Path(str(simulation[key]["output_dir"]))
        scene_periods[key] = delay_runner._period_paths_from_output(output_dir, period_count)
    raw_by_role = {
        "OFF": scene_periods["A1_OFF"],
        "ON": scene_periods["A1_ON"],
        "TO": scene_periods["A1_TO"],
    }
    ideal_by_role = {
        "OFF": scene_periods["A0_OFF"],
        "ON": scene_periods["A0_ON"],
        "TO": scene_periods["A0_TO"],
    }
    calibration_input = delay_runner._merge_binary_files(
        raw_by_role["OFF"], case_root / "inputs" / "target_free_OFF_C_plus_N.bin"
    )
    manifest["inputs"] = {
        role: [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in paths
        ]
        for role, paths in raw_by_role.items()
    }
    manifest["calibration_input"] = {
        "path": str(calibration_input),
        "bytes": calibration_input.stat().st_size,
        "sha256": _sha256(calibration_input),
        "role": "OFF=C+N",
        "truth_used_in_estimator": False,
    }
    source_xml = Path(str(simulation["A0_ON"]["output_dir"])) / "config" / "temp_config_stage2_period_0000.xml"
    truth_root = Path(str(simulation["A0_ON"]["output_dir"])) / "truth"
    try:
        truth = e2e._truth_by_period(Path(str(simulation["A0_ON"]["output_dir"])), period_count)
    except RuntimeError as exc:
        if not str(exc).startswith("more than one visible target row in period "):
            raise
        # The legacy branch summary accepts one target per period. R22 uses
        # the frozen multi-target R21-v3 evaluator after Production; passing an
        # empty mapping here prevents the legacy summary from choosing one
        # target opportunistically or treating many targets as independent.
        truth = {}
        manifest["legacy_single_target_task_summary"] = {
            "status": "NOT_EVALUABLE_MULTI_TARGET",
            "reason": str(exc),
            "replacement_evaluator": "r21-task-eval-v3 via R22 evaluator",
        }
    manifest["source_xml"] = str(source_xml)
    manifest["source_xml_sha256"] = _sha256(source_xml) if source_xml.is_file() else None

    delay_truth = float(case["delay_error_ns"])
    required_families = _required_prepared_families(method_contract)
    manifest["required_prepared_families"] = sorted(required_families)
    prepared: dict[str, dict[str, object]] = {}
    if "ideal" in required_families:
        prepared["ideal"] = {
            **_prepare_raw_input_record(
                ideal_by_role,
                correction="none",
                correction_source="none_ideal_no_error",
                delay_truth_ns=0.0,
            ),
            "condition": "A0_Ideal_no_error",
        }
    if "none" in required_families:
        prepared["none"] = _prepare_raw_input_record(
            raw_by_role,
            correction="none",
            correction_source="none",
            delay_truth_ns=delay_truth,
        )
    if "blind" in required_families:
        try:
            estimator = estimate_blind_delay(delay_runner, raw_by_role["OFF"], layout)
        except (OSError, ValueError, RuntimeError, SystemExit) as exc:
            estimator = {
                "status": "NOT_EVALUABLE",
                "input_role": "OFF=C+N",
                "truth_used_in_estimator": False,
                "reason": str(exc),
            }
        manifest["delay_estimator"] = estimator
        blind_record = prepare_blind_delay_correction(estimator, delay_truth_ns=delay_truth)
        if blind_record["correction_applied"]:
            blind: dict[str, dict[str, object]] = {}
            for role, paths in raw_by_role.items():
                blind[role] = delay_runner.prepare_condition_inputs(
                    case_root / "inputs" / "blind" / role,
                    "A3_Blind_target_free_estimated_correction",
                    paths,
                    calibration_input,
                    delay_truth,
                    float(blind_record["delay_estimate_ns"]),
                    layout,
                )
            prepared["blind"] = {
                **blind_record,
                "period_paths_by_role": {role: list(item["period_paths"]) for role, item in blind.items()},
                "correction_audit_by_role": {role: item.get("correction_audit", []) for role, item in blind.items()},
            }
        else:
            prepared["blind"] = {
                **blind_record,
                "period_paths_by_role": {role: [] for role in raw_by_role},
            }
    else:
        manifest["delay_estimator"] = {
            "status": "not_applicable",
            "truth_used_in_estimator": False,
            "reason": "selected_method_subset_excludes_blind_correction",
        }
    if "known" in required_families:
        known: dict[str, dict[str, object]] = {}
        for role, paths in raw_by_role.items():
            known[role] = delay_runner.prepare_condition_inputs(
                case_root / "inputs" / "known" / role,
                "A2_Known_error_correction_upper_bound",
                paths,
                calibration_input,
                delay_truth,
                None,
                layout,
            )
        prepared["known"] = {
            "condition": "A2_Known_error_correction_upper_bound",
            "correction": "raw_channel_2_fractional_delay",
            "correction_source": "truth_evaluation_only",
            "delay_truth_ns": delay_truth,
            "delay_estimate_ns": delay_truth,
            "correction_applied": True,
            "truth_used_in_estimator": False,
            "truth_used_to_apply_correction": True,
            "known_error_use": "evaluator_only",
            "period_paths_by_role": {role: list(item["period_paths"]) for role, item in known.items()},
            "correction_audit_by_role": {role: item.get("correction_audit", []) for role, item in known.items()},
            "status": "evaluable",
        }
    manifest["prepared_inputs"] = {
        kind: {
            key: value if key not in {"period_paths_by_role"} else {
                role: [str(path) for path in paths]
                for role, paths in value.items()
            }
            for key, value in record.items()
            if key != "period_paths_by_role" or isinstance(value, Mapping)
        }
        for kind, record in prepared.items()
    }

    # The paired causal identity is checked on every input family.  The
    # evaluator is read-only and does not enter any estimator call.
    manifest["causal_triplets"] = {}
    for kind, record in prepared.items():
        paths_by_role = record.get("period_paths_by_role")
        if not isinstance(paths_by_role, Mapping) or not all(paths_by_role.get(role) for role in ("OFF", "ON", "TO")):
            manifest["causal_triplets"][kind] = {
                "status": "NOT_EVALUABLE",
                "reason": record.get("reason", "missing_OFF_ON_TO_inputs"),
            }
            continue
        try:
            manifest["causal_triplets"][kind] = delay_runner.audit_additive_triplet_periods(
                paths_by_role, layout, DEFAULT_ADDITIVE_TOLERANCE
            )
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            manifest["causal_triplets"][kind] = {
                "status": "NOT_EVALUABLE",
                "reason": f"causal_triplet_load_failed:{exc}",
            }

    production = production_config
    xml_records: dict[str, dict[str, object]] = {}
    method_branches: list[dict[str, object]] = []
    mode_contracts: list[dict[str, object]] = []
    execution_methods = [
        {
            "method_id": "A0",
            "family": "ideal_reference",
            "research_calibration_enable": False,
            "research_calibration_method": "production_current",
            "input_correction": "none",
            "known_error": False,
            "known_error_use": "none",
            "_prepared_family": "ideal",
        },
        *method_contract,
    ]
    for method in execution_methods:
        method_id = str(method["method_id"])
        if method_id == "A0":
            family = "ideal"
        elif method_id in {"P1", "P2"}:
            family = "blind"
        elif method_id in {"PK", "PKR"}:
            family = "known"
        else:
            family = "none"
        prepared_family = prepared[family]
        modes = tuple(calibration_modes) if method_id in REFERENCE_FIT_METHOD_IDS else ("not_applicable",)
        for mode in modes:
            mode_root = case_root / "references" / mode / method_id
            reference_path = mode_root / "reference_gamma.csv"
            role_order = ("OFF", "ON", "TO")
            if mode == "Mode-B":
                # The estimator must finish before its fixed reference can be
                # used by the target-only evaluator.
                role_order = ("ON", "OFF", "TO")
            role_records: dict[str, dict[str, object]] = {}
            reference_info: dict[str, object] | None = None
            for role in role_order:
                execution_contract = build_branch_execution_contract(
                    method_id, mode, role, reference_path if mode != "not_applicable" else None
                )
                mode_contracts.append(dict(execution_contract))
                base_record: dict[str, object] = {
                    "method_id": method_id,
                    "mode": mode,
                    "role": role,
                    "method_family": method["family"],
                    "research_calibration_enable": method["research_calibration_enable"],
                    "research_calibration_method": method["research_calibration_method"],
                    "input_correction": method["input_correction"],
                    "known_error": method["known_error"],
                    "known_error_use": method["known_error_use"],
                    "truth_used_in_estimator": False,
                    "estimator_input_roles": execution_contract["estimator_input_roles"],
                    "execution_contract": execution_contract,
                    "mode_contract": build_paired_input_contract()["modes"],
                    "fallback_to_current": False,
                    "input_identity": manifest["pair_identity"],
                }
                if prepared_family.get("status") != "evaluable":
                    base_record.update({
                        "status": "NOT_EVALUABLE",
                        "reason": prepared_family.get("reason", "input_preparation_failed"),
                    })
                    method_branches.append(base_record)
                    role_records[role] = base_record
                    continue
                paths_by_role = prepared_family.get("period_paths_by_role")
                if not isinstance(paths_by_role, Mapping) or not isinstance(paths_by_role.get(role), Sequence):
                    base_record.update({"status": "NOT_EVALUABLE", "reason": "missing_role_input"})
                    method_branches.append(base_record)
                    role_records[role] = base_record
                    continue

                if bool(execution_contract.get("fixed_reference_required")):
                    if reference_info is None or reference_info.get("status") != "passed":
                        base_record.update({
                            "status": "NOT_EVALUABLE",
                            "reason": (
                                "reference_gamma_artifact_missing_or_invalid"
                                if reference_info is None
                                else reference_info.get("reason", "reference_gamma_artifact_invalid")
                            ),
                            "reference_artifact": str(reference_path.resolve()),
                        })
                        method_branches.append(base_record)
                        role_records[role] = base_record
                        continue

                input_dir = _materialize_input_dir(
                    case_root / "production_inputs" / method_id / mode / role,
                    [Path(path) for path in paths_by_role[role]],
                )
                branch_name = f"{method_id}_{mode.replace('-', '').lower()}_{role}"
                xml_record = _method_xml(
                    case_root,
                    source_xml,
                    method,
                    production,
                    layout=layout,
                    mode=mode,
                    role=role,
                    reference_path=(
                        Path(str(reference_info["path"]))
                        if reference_info is not None and reference_info.get("status") == "passed"
                        else None
                    ),
                )
                xml_key = f"{method_id}_{mode}_{role}"
                xml_records[xml_key] = xml_record
                base_record["xml_key"] = xml_key
                try:
                    record = delay_runner.run_production_branch(
                        case_root,
                        branch_name,
                        input_dir,
                        Path(str(xml_record["path"])),
                        truth,
                        period_count,
                        input_mode,
                        layout,
                        truth_path=truth_root,
                        xml_overrides=xml_record["overrides"],
                    )
                except BinaryIdentityMismatch:
                    raise
                except (OSError, RuntimeError, ValueError, KeyError) as exc:
                    record = {
                        "status": "NOT_EVALUABLE",
                        "reason": f"production_branch_exception:{exc}",
                        "production_returncode": None,
                        "xml_overrides": xml_record["overrides"],
                    }
                base_record.update(record)
                expected_pipe_sha256 = os.environ.get("PGRCC_EXPECTED_GMTI_PIPE_CORE_SHA256")
                if expected_pipe_sha256:
                    _assert_artifact_sha256(PIPE, expected_pipe_sha256, "GMTI_pipe_core")
                    base_record["production_binary_identity"] = _artifact_identity(PIPE)
                base_record["input_dir"] = str(input_dir)
                base_record["input_paths"] = [str(path) for path in paths_by_role[role]]
                base_record["input_sha256"] = [_sha256(Path(path)) for path in paths_by_role[role]]
                actual_xml = Path(str(record.get("production_xml", ""))) if record.get("production_xml") else None
                if actual_xml is not None and actual_xml.is_file():
                    xml_audit = audit_xml_fields(actual_xml, xml_record["overrides"])
                else:
                    xml_audit = {
                        "status": "failed",
                        "path": str(actual_xml) if actual_xml else None,
                        "reason": "production_xml_missing",
                        "fields": {},
                    }
                base_record["xml_audit"] = xml_audit
                if xml_audit.get("status") != "passed":
                    base_record.update({
                        "status": "NOT_EVALUABLE",
                        "reason": "production_xml_method_fields_not_auditable",
                        "fallback_to_current": False,
                    })
                base_record["artifacts"] = _collect_branch_artifacts(record)
                if not isinstance(base_record.get("cfar_geometry"), Mapping):
                    base_record["cfar_geometry"] = aggregate_branch_cfar_geometry(
                        base_record["artifacts"].get("cfar_geometry", [])  # type: ignore[union-attr]
                    )
                reference_gamma_paths = base_record["artifacts"].get("reference_gamma_csv", [])
                reference_gamma_rows = _reference_gamma_rows(
                    reference_gamma_paths, method_id, role  # type: ignore[arg-type]
                )
                expects_tap = method_requires_research_adapter_tap(method)
                if expects_tap:
                    adapter_validation = validate_reference_gamma_rows(
                        reference_gamma_rows,
                        method_id=method_id,
                        min_support=int(production.get("min_support", 8)),
                        expected_method=str(method["research_calibration_method"]),
                    )
                    base_record["reference_gamma_summary"] = compact_reference_gamma_summary(
                        reference_gamma_rows,
                        adapter_validation,
                        source_paths=reference_gamma_paths,  # type: ignore[arg-type]
                    )
                    compact_validation = _compact_reference_validation(adapter_validation)
                    base_record["adapter_validation"] = compact_validation
                    base_record["reference_gamma_validation"] = compact_validation
                    base_record["truth_used_in_estimator"] = adapter_validation.get(
                        "truth_used_in_estimator"
                    )
                    if adapter_validation.get("status") != "evaluable":
                        base_record.update({
                            "status": "NOT_EVALUABLE",
                            "reason": adapter_validation.get("reason", "adapter_not_evaluable"),
                            "fallback_to_current": False,
                        })
                    if bool(execution_contract.get("estimator_called")):
                        reference_info = write_reference_gamma_artifact(
                            reference_gamma_rows,
                            reference_path,
                            method_id=method_id,
                            min_support=int(production.get("min_support", 8)),
                            expected_method=str(method["research_calibration_method"]),
                        )
                        compact_reference_info = dict(reference_info)
                        if isinstance(compact_reference_info.get("validation"), Mapping):
                            compact_reference_info["validation"] = _compact_reference_validation(
                                compact_reference_info["validation"]  # type: ignore[arg-type]
                            )
                        base_record["reference_artifact"] = compact_reference_info
                else:
                    base_record["adapter_validation"] = {
                        "status": "not_applicable",
                        "truth_used_in_estimator": False,
                        "fallback_to_current": False,
                    }
                if bool(execution_contract.get("fixed_reference_required")):
                    base_record["reference_applied"] = True
                    base_record["reference_path"] = str(reference_path.resolve())
                method_branches.append(base_record)
                role_records[role] = base_record
            manifest.setdefault("method_mode_roles", {})[f"{method_id}_{mode}"] = role_records
    manifest["method_xml"] = xml_records
    manifest["mode_contracts"] = mode_contracts
    manifest["method_branches"] = method_branches
    from scripts.analyze_production_hierarchical_calibration_pilot import (
        residual_closure_from_branch_artifacts,
    )

    manifest["residual_closure"] = residual_closure_from_branch_artifacts(method_branches)
    causal_ok = all(
        isinstance(value, Mapping) and value.get("status") == "passed"
        for value in manifest.get("causal_triplets", {}).values()
    )
    off_branches = [
        branch
        for branch in method_branches
        if isinstance(branch, Mapping) and str(branch.get("role")) == "OFF"
    ]
    denominator_ok = bool(off_branches) and all(
        isinstance(branch.get("target_off_waterfall"), Mapping)
        and isinstance(
            branch["target_off_waterfall"].get("valid_cut_denominators"), Mapping
        )
        and branch["target_off_waterfall"]["valid_cut_denominators"].get("status")
        == "passed"
        for branch in off_branches
    )
    final_union_hits_ok = bool(off_branches) and all(
        isinstance(branch.get("target_off_waterfall"), Mapping)
        and isinstance(
            branch["target_off_waterfall"].get("unique_final_union_hits"), Mapping
        )
        and branch["target_off_waterfall"]["unique_final_union_hits"].get("status")
        == "passed"
        for branch in off_branches
    )
    manifest["evidence_gate"] = {
        "causal_triplets": "passed" if causal_ok else "NOT_EVALUABLE",
        "valid_cut_denominators": "passed" if denominator_ok else "NOT_EVALUABLE",
        "unique_final_union_hits": (
            "passed" if final_union_hits_ok else "NOT_EVALUABLE"
        ),
        "empirical_pfa": "NOT_EVALUABLE",
        "truth_used_in_estimator": False,
        "fallback_to_current": False,
    }
    manifest["status"] = (
        "completed"
        if all(str(item.get("status")) == "passed" for item in method_branches)
        and causal_ok
        and denominator_ok
        and final_union_hits_ok
        else "completed_with_gaps"
    )
    manifest["algorithmic_results_claimed"] = False
    _write_json(case_root / "case_manifest.json", manifest)
    return manifest


def _counterfactual_processing_comparability(
    method_id: str,
    mode: str,
    roles: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    if mode not in {"Mode-A", "Mode-B"}:
        return {"status": "not_required", "reason": None}

    estimator_role = "OFF" if mode == "Mode-A" else "ON"
    apply_roles = {"ON", "TO"} if mode == "Mode-A" else {"OFF", "TO"}
    expected_source = f"{estimator_role}_estimator_output"
    for role in ("OFF", "ON", "TO"):
        branch = roles.get(role)
        contract = branch.get("execution_contract") if isinstance(branch, Mapping) else None
        if not isinstance(contract, Mapping):
            return {"status": "NOT_EVALUABLE", "reason": f"execution_contract_missing:{role}"}
        if (
            str(branch.get("method_id")) != method_id
            or str(branch.get("mode")) != mode
            or str(branch.get("role")) != role
            or str(contract.get("method_id")) != method_id
            or str(contract.get("mode")) != mode
            or str(contract.get("role")) != role
            or contract.get("reference_source") != expected_source
        ):
            return {"status": "NOT_EVALUABLE", "reason": f"execution_contract_mismatch:{role}"}
        if branch.get("fallback_to_current") is True or contract.get("fallback_to_current") is True:
            return {"status": "NOT_EVALUABLE", "reason": f"fallback_to_current:{role}"}
        if role == estimator_role:
            if contract.get("estimator_called") is not True or contract.get("fixed_reference_required") is True:
                return {"status": "NOT_EVALUABLE", "reason": f"estimator_contract_invalid:{role}"}
        elif role in apply_roles:
            if (
                contract.get("estimator_called") is not False
                or contract.get("fixed_reference_required") is not True
                or contract.get("reference_applied") is not True
                or branch.get("reference_applied") is not True
            ):
                return {"status": "NOT_EVALUABLE", "reason": f"fixed_reference_contract_invalid:{role}"}

    estimator = roles[estimator_role]
    artifact = estimator.get("reference_artifact")
    reference_path = artifact.get("path") if isinstance(artifact, Mapping) else None
    if not isinstance(artifact, Mapping) or artifact.get("status") != "passed" or not reference_path:
        return {"status": "NOT_EVALUABLE", "reason": "estimator_reference_artifact_invalid"}
    for role in apply_roles:
        if str(roles[role].get("reference_path", "")) != str(reference_path):
            return {"status": "NOT_EVALUABLE", "reason": f"fixed_reference_path_mismatch:{role}"}
    return {"status": "passed", "reason": None}


def _flatten_rows(cases: Sequence[Mapping[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    method_rows: list[dict[str, object]] = []
    gamma_rows: list[dict[str, object]] = []
    clutter_rows: list[dict[str, object]] = []
    for case in cases:
        case_values = case.get("case", {})
        case_id = str(case.get("case_id", case_values.get("group", ""))) if isinstance(case_values, Mapping) else str(case.get("case_id", ""))
        grouped: dict[tuple[str, str], dict[str, dict[str, object]]] = {}
        for branch in case.get("method_branches", []):
            if not isinstance(branch, Mapping):
                continue
            method_id = str(branch.get("method_id", ""))
            mode = str(branch.get("mode", "not_applicable"))
            common = {
                "case": case_id,
                "method_id": method_id,
                "mode": mode,
                "role": branch.get("role"),
                "status": branch.get("status"),
                "reason": branch.get("reason"),
                "truth_used_in_estimator": branch.get("truth_used_in_estimator"),
                "fallback_to_current": branch.get("fallback_to_current", False),
                "reference_source": (
                    branch.get("execution_contract", {}).get("reference_source")
                    if isinstance(branch.get("execution_contract"), Mapping) else None
                ),
            }
            method_rows.append(common)
            raw_reference_rows = branch.get("reference_gamma_rows", [])
            if (
                isinstance(raw_reference_rows, Sequence)
                and not isinstance(raw_reference_rows, (str, bytes))
                and raw_reference_rows
            ):
                for reference_row in raw_reference_rows:
                    if isinstance(reference_row, Mapping):
                        gamma = dict(reference_row)
                        gamma.update({
                            "case": case_id,
                            "method_id": method_id,
                            "mode": mode,
                            "role": branch.get("role"),
                            "reference_source": common["reference_source"],
                        })
                        gamma_rows.append(gamma)
            else:
                summary = branch.get("reference_gamma_summary")
                if isinstance(summary, Mapping):
                    gamma_rows.append({
                        "case": case_id,
                        "method_id": method_id,
                        "mode": mode,
                        "role": branch.get("role"),
                        "method": branch.get("research_calibration_method"),
                        "status": summary.get("status"),
                        "reason": summary.get("reason"),
                        "support_count": summary.get("support_count"),
                        "row_count": summary.get("row_count"),
                        "gamma_real": summary.get("gamma_real_mean"),
                        "gamma_imag": summary.get("gamma_imag_mean"),
                        "gamma_abs": summary.get("gamma_abs_mean"),
                        "phase_coherence": summary.get("phase_coherence_mean"),
                        "truth_used_in_estimator": summary.get(
                            "truth_used_in_estimator", False
                        ),
                        "reference_source": common["reference_source"],
                        "source_paths": ";".join(
                            str(path) for path in summary.get("source_paths", [])
                        ),
                        "group_id": "__branch_summary__",
                    })
            grouped.setdefault((method_id, mode), {})[str(branch.get("role", ""))] = dict(branch)

            metrics = branch.get("metrics")
            metrics_map = metrics if isinstance(metrics, Mapping) else {}
            geometry = branch.get("cfar_geometry")
            geometry_map = geometry if isinstance(geometry, Mapping) else {}
            artifacts = branch.get("artifacts")
            artifact_map = artifacts if isinstance(artifacts, Mapping) else {}
            id_switch = branch.get("id_switch_classification_counts")
            id_switch_marker = (
                id_switch
                if branch.get("id_switch_audit_status") == "passed"
                else "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"
            )
            layer_rows = [
                {
                    "layer": "CFAR",
                    "status": geometry_map.get("status", "NOT_EVALUABLE"),
                    "valid_cut_count": geometry_map.get("valid_cut_count"),
                    "hit_cut_count": geometry_map.get("hit_cut_count"),
                    "tested_cut_hit_fraction": geometry_map.get(
                        "tested_cut_hit_fraction"
                    ),
                    "cell_pfa": geometry_map.get("cell_pfa"),
                    "pfa_status": geometry_map.get("pfa_status", "NOT_EVALUABLE"),
                    "pfa_reason": geometry_map.get("pfa_reason"),
                    "false_cut_hit_count": geometry_map.get("false_cut_hit_count"),
                    "valid_h0_cut_count": geometry_map.get("valid_h0_cut_count"),
                    "cfar_layer": geometry_map,
                },
                {
                    "layer": "cluster",
                    "status": metrics_map.get("cluster_false_alarm_status", "NOT_EVALUABLE"),
                    "cluster_layer": {
                        "cfar_cluster_count": metrics_map.get("cfar_cluster_count"),
                        "selected_cluster_count": metrics_map.get("cfar_selected_cluster_count"),
                        "false_alarm_count": metrics_map.get("cluster_false_alarm_count"),
                    },
                },
                {
                    "layer": "protocol_detection",
                    "status": metrics_map.get("protocol_detection_false_alarm_status", "NOT_EVALUABLE"),
                    "protocol_detection_layer": {
                        "raw_detection_count": metrics_map.get("raw_detection_count"),
                        "target_detection_hit_count": metrics_map.get("target_detection_hit_count"),
                        "false_alarm_count": metrics_map.get("protocol_payload_false_alarm_count"),
                    },
                },
                {
                    "layer": "track",
                    "status": "evaluable" if artifact_map.get("track_states") else "NOT_EVALUABLE",
                    "track_layer": {
                        "track_pd_all_visible": metrics_map.get("track_pd_all_visible"),
                        "track_continuity": metrics_map.get("track_continuity"),
                        "association": artifact_map.get("track_association", []),
                        "states": artifact_map.get("track_states", []),
                        "payloads": artifact_map.get("track_payloads", []),
                        "id_switch_classification": id_switch_marker,
                    },
                    "track_association_audit_v2": artifact_map.get("track_association", []),
                    "track_states": artifact_map.get("track_states", []),
                    "track_output_payloads": artifact_map.get("track_payloads", []),
                    "id_switch_classification": id_switch_marker,
                },
            ]
            for layer in layer_rows:
                clutter_rows.append({
                    "case": case_id,
                    "method_id": method_id,
                    "mode": mode,
                    "role": branch.get("role"),
                    **layer,
                })
            if str(branch.get("role")) == "OFF":
                waterfall = branch.get("target_off_waterfall")
                if isinstance(waterfall, Mapping):
                    cell = waterfall.get("cell_false_hit_fraction")
                    cell_map = cell if isinstance(cell, Mapping) else {}
                    clutter_rows.append({
                        "case": case_id,
                        "method_id": method_id,
                        "mode": mode,
                        "role": "OFF",
                        "layer": "OFF_waterfall",
                        "status": cell_map.get("status", "NOT_EVALUABLE"),
                        "cell_false_hit_status": cell_map.get("status", "NOT_EVALUABLE"),
                        "cell_false_hit_fraction": cell_map.get("value"),
                        "hit_cut_count": cell_map.get("hit_cell_count"),
                        "valid_cut_count": cell_map.get("valid_cut_count"),
                        "valid_cut_denominators": waterfall.get(
                            "valid_cut_denominators"
                        ),
                        "unique_final_union_hits": waterfall.get(
                            "unique_final_union_hits"
                        ),
                        "cluster_layer": waterfall.get("false_clusters"),
                        "protocol_detection_layer": waterfall.get("protocol_false_detections"),
                        "track_layer": waterfall.get("false_tracks"),
                    })

        # One causal row per method/mode keeps ON-OFF and TO evidence together
        # without promoting target power alone to a protection metric.
        for (method_id, mode), roles in grouped.items():
            off = roles.get("OFF", {})
            on = roles.get("ON", {})
            target_only = roles.get("TO", {})
            processing_comparability = _counterfactual_processing_comparability(
                method_id, mode, roles
            )
            on_metrics = on.get("metrics") if isinstance(on.get("metrics"), Mapping) else {}
            off_metrics = off.get("metrics") if isinstance(off.get("metrics"), Mapping) else {}
            to_metrics = target_only.get("metrics") if isinstance(target_only.get("metrics"), Mapping) else {}

            def _delta(key: str) -> float | None:
                try:
                    on_value = float(on_metrics[key])
                    off_value = float(off_metrics[key])
                except (KeyError, TypeError, ValueError):
                    return None
                return on_value - off_value if math.isfinite(on_value) and math.isfinite(off_value) else None

            causal_status = (
                "evaluable"
                if on_metrics and off_metrics and to_metrics
                and processing_comparability.get("status") in {"passed", "not_required"}
                and all(item.get("status") in {"passed", "completed", "completed_with_gaps"} for item in (on, off, target_only))
                else "NOT_EVALUABLE"
            )
            on_artifacts = on.get("artifacts") if isinstance(on.get("artifacts"), Mapping) else {}
            id_switch_status = on.get("id_switch_audit_status")
            clutter_rows.append({
                "case": case_id,
                "method_id": method_id,
                "mode": mode,
                "role": "ON_MINUS_OFF",
                "layer": "ON_minus_OFF",
                "status": causal_status,
                "processing_comparability_status": processing_comparability.get("status"),
                "processing_comparability_reason": processing_comparability.get("reason"),
                "on_minus_off_target_detection_pd": _delta("target_detection_pd"),
                "on_minus_off_track_pd": _delta("track_pd_all_visible"),
                "to_target_detection_pd": to_metrics.get("target_detection_pd"),
                "to_track_pd": to_metrics.get("track_pd_all_visible"),
                "target_protection_rule": "causal_ON_minus_OFF_and_TO; never_ON_power_alone",
                "track_association_audit_v2": on_artifacts.get("track_association", []),
                "track_states": on_artifacts.get("track_states", []),
                "track_output_payloads": on_artifacts.get("track_payloads", []),
                "id_switch_classification": (
                    on.get("id_switch_classification_counts")
                    if id_switch_status == "passed"
                    else "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"
                ),
            })
    return method_rows, gamma_rows, clutter_rows


def _initial_manifest(
    config_path: Path,
    config: Mapping[str, object],
    selection: Mapping[str, object],
    source: Mapping[str, object],
    output_root: Path,
    args: argparse.Namespace,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "stage": config["stage"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "not_started",
        "mode": selection["mode"],
        "skip_cuda": bool(args.skip_cuda),
        "algorithmic_results_claimed": False,
        "decision": "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE",
        "method_contract": build_method_contract(config),
        "input_contract": build_paired_input_contract(),
        "downstream_compact_contract": build_downstream_compact_contract(),
        "production_contract": config.get("production"),
        "selection": selection,
        "ai_training": False,
        "router_enabled": False,
        "native_4ch_stap": False,
        "source": dict(source),
        "config": {
            "path": str(config_path.resolve()),
            "sha256": _sha256(config_path),
        },
        "runner": {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__))},
        "analyzer": {
            "path": str((ROOT / "scripts/analyze_production_hierarchical_calibration_pilot.py").resolve()),
            "sha256": _sha256(ROOT / "scripts/analyze_production_hierarchical_calibration_pilot.py")
            if (ROOT / "scripts/analyze_production_hierarchical_calibration_pilot.py").is_file()
            else None,
        },
        "platform": platform.platform(),
        "python": sys.version,
        "gpu_probe": _probe_nvidia_smi(),
        "disk": _disk_status(output_root),
        "build": _build_identity(),
        "command": [str(item) for item in args._command_args],
        "output_root": str(output_root),
        "external_commands": [],
        "cases": [],
    }


def _write_skip_compact_evidence(manifest_path: Path, evidence_root: Path) -> dict[str, object]:
    from scripts.analyze_production_hierarchical_calibration_pilot import build_compact_evidence

    outputs = build_compact_evidence(manifest_path, evidence_root)
    return {name: str(path) for name, path in outputs.items()}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--mode", choices=("pilot", "formal", "e1_pilot"), default=None)
    parser.add_argument("--pilot", action="store_true", help="select the targeted pilot group")
    parser.add_argument("--formal", action="store_true", help="select explicit formal targeted groups")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, default=None)
    parser.add_argument("--input-mode", choices=("local", "shm"), default=None)
    parser.add_argument("--skip-cuda", "--contract", dest="skip_cuda", action="store_true")
    parser.add_argument("--delay-errors-ns", action="append", default=None)
    parser.add_argument("--seeds", action="append", default=None)
    parser.add_argument("--target-velocities-mps", action="append", default=None)
    parser.add_argument("--target-snr-db", action="append", default=None)
    parser.add_argument(
        "--method-ids", "--methods", dest="method_ids", action="append", default=None,
        help="comma-separated/repeatable method subset; defaults to the full frozen suite",
    )
    parser.add_argument(
        "--calibration-modes", action="append", default=None,
        help="comma-separated/repeatable Mode-A and/or Mode-B subset; defaults to both",
    )
    parser.add_argument("--max-cases", type=int, default=None)
    return parser


def _parse_override_values(raw: object, converter: type[int] | type[float]) -> list[object] | None:
    if raw is None:
        return None
    values = raw if isinstance(raw, list) else [raw]
    result: list[object] = []
    for item in values:
        for token in str(item).split(","):
            if token.strip():
                result.append(converter(token.strip()))
    return result


def _parse_string_values(raw: object, name: str) -> tuple[str, ...] | None:
    if raw is None:
        return None
    values = raw if isinstance(raw, list) else [raw]
    result = tuple(
        token.strip()
        for item in values
        for token in str(item).split(",")
        if token.strip()
    )
    if not result:
        raise ValueError(f"{name} selection must not be empty")
    return result


def run_cli(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    args._command_args = list(argv) if argv is not None else sys.argv[1:]
    if args.pilot and args.formal:
        parser.error("--pilot and --formal are mutually exclusive")
    mode = args.mode or ("formal" if args.formal else "pilot")
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    config_path = config_path.resolve()
    try:
        config = load_pilot_config(config_path)
        overrides = {
            "delay_errors_ns": _parse_override_values(args.delay_errors_ns, float),
            "seeds": _parse_override_values(args.seeds, int),
            "target_velocities_mps": _parse_override_values(args.target_velocities_mps, float),
            "target_snr_db": _parse_override_values(args.target_snr_db, float),
        }
        overrides = {key: value for key, value in overrides.items() if value is not None}
        selection = resolve_selection(config, mode, overrides)
        method_ids = _parse_string_values(args.method_ids, "method id")
        method_contract = build_method_contract(config, method_ids=method_ids)
        calibration_modes = _parse_string_values(args.calibration_modes, "calibration mode")
        calibration_mode_map = resolve_calibration_modes(method_contract, calibration_modes)
        if args.max_cases is not None:
            if args.max_cases <= 0:
                raise ValueError("--max-cases must be positive")
            selection["cases"] = selection["cases"][: args.max_cases]
            selection["case_count"] = len(selection["cases"])
            selection["truncated_by_max_cases"] = True
        pinned_build_identity = None if args.skip_cuda else _pin_build_identity()
        source = source_snapshot()
        require_clean_tracked_source(source, mode=mode)
        output_root = Path(args.output_root).resolve()
        if output_root.exists() and any(output_root.iterdir()):
            raise RuntimeError(f"refuse to overwrite non-empty output root: {output_root}")
        output_root.mkdir(parents=True, exist_ok=True)
        production = config.get("production")
        if not isinstance(production, Mapping):
            raise ValueError("pilot config production section is missing")
        input_mode = args.input_mode or str(production.get("input_mode", "local"))
        if input_mode not in {"local", "shm"}:
            raise ValueError("input mode must be local or shm")
        manifest = _initial_manifest(config_path, config, selection, source, output_root, args)
        if pinned_build_identity is not None:
            manifest["build_identity_pinned"] = pinned_build_identity
        manifest["input_mode"] = input_mode
        manifest["method_contract"] = method_contract
        manifest["selected_methods"] = [str(item["method_id"]) for item in method_contract]
        manifest["selected_calibration_modes"] = list(
            calibration_modes if calibration_modes is not None else ("Mode-A", "Mode-B")
        )
        manifest["method_mode_selection"] = {
            method: list(modes) for method, modes in calibration_mode_map.items()
        }
        manifest_path = output_root / "manifest.json"
        _write_json(manifest_path, manifest)
        if args.skip_cuda:
            cases = [
                _run_case(
                    config,
                    case,
                    output_root,
                    method_contract,
                    input_mode=input_mode,
                    skip_cuda=True,
                    calibration_modes=(
                        calibration_modes if calibration_modes is not None else ("Mode-A", "Mode-B")
                    ),
                )
                for case in selection["cases"]
            ]
            manifest["cases"] = cases
            manifest.update({
                "status": "skip_cuda",
                "algorithmic_results_claimed": False,
                "decision": "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE",
                "skip_reason": "no simulator/GMTI_pipe_core execution; contract only",
            })
        else:
            cases: list[dict[str, object]] = []
            for case in selection["cases"]:
                try:
                    result = _run_case(
                        config,
                        case,
                        output_root,
                        method_contract,
                        input_mode=input_mode,
                        skip_cuda=False,
                        calibration_modes=(
                            calibration_modes
                            if calibration_modes is not None
                            else ("Mode-A", "Mode-B")
                        ),
                    )
                except BinaryIdentityMismatch:
                    raise
                except (OSError, RuntimeError, ValueError, KeyError) as exc:
                    result = _case_manifest_base(config, case, output_root / "cases")
                    result.update({
                        "status": "NOT_EVALUABLE",
                        "reason": f"case_exception:{exc}",
                        "method_branches": _missing_branches(
                            f"case_exception:{exc}", method_contract,
                            calibration_modes
                            if calibration_modes is not None
                            else ("Mode-A", "Mode-B"),
                        ),
                    })
                cases.append(result)
                manifest["cases"] = cases
                _write_json(manifest_path, manifest)
            if pinned_build_identity is not None:
                final_build_identity = _build_identity()
                for name, pinned in pinned_build_identity.items():
                    if not isinstance(pinned, Mapping):
                        raise BinaryIdentityMismatch(f"{name} identity mismatch: missing pinned identity")
                    _assert_artifact_sha256(
                        Path(str(pinned["path"])), str(pinned.get("sha256") or ""), name
                    )
                manifest["build_identity_after_run"] = final_build_identity
            manifest["status"] = (
                "completed"
                if all(case.get("status") == "completed" for case in cases)
                else "completed_with_gaps"
            )
            manifest["algorithmic_results_claimed"] = False
            method_rows, gamma_rows, clutter_rows = _flatten_rows(cases)
            manifest["compact_rows"] = {
                "method_contract": method_rows,
                "gamma_recovery": gamma_rows,
                "clutter_metrics": clutter_rows,
            }
            manifest["residual_closure"] = aggregate_case_residual_closures(cases)
        _write_json(manifest_path, manifest)
        evidence_root = (
            Path(args.evidence_root).resolve()
            if args.evidence_root is not None
            else output_root / "compact_evidence"
        )
        manifest["compact_evidence_root"] = str(evidence_root)
        _write_json(manifest_path, manifest)
        manifest["compact_evidence"] = _write_skip_compact_evidence(manifest_path, evidence_root)
        compact_manifest = _read_json(evidence_root / "manifest.json")
        analysis = compact_manifest.get("analysis")
        if isinstance(analysis, Mapping):
            manifest["analysis"] = dict(analysis)
            manifest["algorithmic_results_claimed"] = bool(
                analysis.get("algorithmic_results_claimed")
            )
            manifest["decision"] = analysis.get(
                "decision", "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE"
            )
        _write_json(manifest_path, manifest)
        return 0
    except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
        print(f"[production_hierarchical_calibration_pilot][ERROR] {exc}", file=sys.stderr)
        return 2


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
