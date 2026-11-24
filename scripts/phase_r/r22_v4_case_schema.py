#!/usr/bin/env python3
"""Case-type-specific CPU schema contract for R22-V4 compact archives."""

from __future__ import annotations

import math
import re
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping


METHOD_PREFIXES = (
    ("HUBER_D3_CAUSAL_PREFIX", "HUBER_D3_CAUSAL_PREFIX"),
    ("M1_CAUSAL_PREFIX", "M1_CAUSAL_PREFIX"),
    ("R22_R9_Fixed_D3", "R22_R9_Fixed_D3"),
    ("PK", "PK"),
    ("C0", "C0"),
    ("A0", "A0"),
)
PLUS_FOUR_METHODS = {
    "A0", "C0", "PK", "R22_R9_Fixed_D3",
    "HUBER_D3_CAUSAL_PREFIX", "M1_CAUSAL_PREFIX",
}
CORRECTION_METHODS = PLUS_FOUR_METHODS - {"A0"}
TRACK_DEBUG_FILES = (
    "track_association_accepts.csv", "track_detections.csv", "track_events.csv",
    "track_frames.csv", "track_output_payloads.csv", "track_points.csv",
    "track_results.csv", "track_states.csv",
)
COMMON_EVALUATION_MEMBERS = {
    "evaluation/parameter_metrics.csv",
    "evaluation/target_associations.csv",
    "evaluation/task_metrics.csv",
    "evaluation/track_level_metrics.csv",
}
H0_EVALUATION_MEMBERS = {
    "evaluation/h0_false_alarm_metrics.csv",
    "evaluation/h0_task_metrics.csv",
}
TARGET_RESPONSE_SUFFIXES = (
    "/r22_v4_output_evidence/target_complex_response.csv",
    "/r22_v4_output_evidence/target_complex_response_manifest.json",
    "/r22_v4_output_evidence/target_roi_manifest.csv",
    "/r22_v4_output_evidence/adaptive_role_difference_diagnostic.csv",
    "/r22_v4_output_evidence/truth_mapping_audit.json",
)
H0_FORBIDDEN_MEMBER_SUFFIXES = (
    "/truth/truth_targets_by_beam.csv",
    "/truth/moving_target_truth.csv",
    "/truth/target_injection_amplitudes.csv",
)


def _method_role(branch: str) -> tuple[str, str] | None:
    for prefix, method in METHOD_PREFIXES:
        for role in ("ON", "OFF", "TO"):
            if branch == f"{prefix}_{role}" or branch.startswith(f"{prefix}_") and branch.endswith(f"_{role}"):
                return method, role
    return None


def _branch_inventory(members: set[str], case_root: str) -> dict[str, set[str]]:
    prefix = f"{case_root}/production/"
    branches = sorted({
        name[len(prefix):].split("/", 1)[0]
        for name in members if name.startswith(prefix) and "/" in name[len(prefix):]
    })
    result: dict[str, set[str]] = {}
    for branch in branches:
        identity = _method_role(branch)
        if identity is None:
            raise ValueError(f"unsupported V4 Production branch identity: {branch}")
        method, role = identity
        result.setdefault(method, set()).add(role)
    return result


def _schema_name(phase: str, delay: float) -> str:
    family = "h0" if phase == "h0" else "target_present"
    return f"{family}_{int(delay)}ns"


def _safe_names(member_names: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for raw in member_names:
        name = str(raw)
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or ".." in path.parts or "\\" in name):
            raise ValueError(f"unsafe archive member path: {name!r}")
        if name in names:
            raise ValueError(f"duplicate archive member path: {name}")
        names.add(name)
    return names


def validate_v4_case_schema(
    case_identity: Mapping[str, Any],
    member_names: Iterable[str],
    *,
    payload_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one of the four target/H0 × zero/+4 ns archive contracts."""
    if not isinstance(case_identity, Mapping):
        raise ValueError("case identity must be an object")
    try:
        target_count = int(case_identity["target_count"])
        delay = float(case_identity.get("delay_error_ns", case_identity.get("delay_ns")))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("case identity requires target_count and delay_error_ns") from exc
    if target_count < 0 or not math.isfinite(delay) or delay not in (0.0, 4.0):
        raise ValueError("V4 case requires target_count >= 0 and delay 0 or 4 ns")
    phase_value = str(case_identity.get("phase", "")).strip().lower()
    if target_count == 0:
        if phase_value in {"target_present", "engineering", "screening", "confirmatory"}:
            raise ValueError("case phase and zero target_count disagree")
        phase = "h0"
    else:
        if phase_value == "h0":
            raise ValueError("H0 phase and positive target_count disagree")
        phase = "target_present"
    schema_name = _schema_name(phase, delay)
    members = _safe_names(member_names)
    case_manifests = [name for name in members if name.endswith("/case_manifest.json")]
    if len(case_manifests) != 1 or "r22_run_manifest.json" not in members:
        raise ValueError("case schema requires one case_manifest.json and r22_run_manifest.json")
    case_root = case_manifests[0].rsplit("/case_manifest.json", 1)[0]

    allowlist_patterns = [
        "r22_run_manifest.json", "case_manifest_review_extract.json",
        f"{case_root}/**", "evaluation/**",
    ]
    allowlist_patterns.extend([
        f"{case_root}/scenes/**/truth/**",
        f"{case_root}/r22_v4_output_evidence/**",
    ])
    forbidden: list[str] = []
    outside_allowlist = [
        name for name in members
        if name != "r22_run_manifest.json"
        and name != "case_manifest_review_extract.json"
        and not name.startswith(case_root + "/")
        and not name.startswith("evaluation/")
    ]
    if outside_allowlist:
        raise ValueError(f"case contains members outside its schema allowlist: {outside_allowlist[:5]}")

    required = {
        "r22_run_manifest.json", case_manifests[0], *COMMON_EVALUATION_MEMBERS,
        *(f"{case_root}{suffix}" for suffix in TARGET_RESPONSE_SUFFIXES),
    }
    if phase == "h0":
        required |= H0_EVALUATION_MEMBERS
        forbidden = [name for name in members if any(
            name.endswith(suffix) for suffix in H0_FORBIDDEN_MEMBER_SUFFIXES
        )]
        if forbidden:
            raise ValueError(f"H0 case contains target-present truth rows: {forbidden[:5]}")
        metadata = payload_metadata or {}
        association_count = metadata.get("target_truth_association_count")
        response_count = metadata.get("target_response_target_count")
        if association_count is None or int(association_count) != 0:
            raise ValueError("H0 target truth association count must be zero")
        if response_count is None or int(response_count) != 0:
            raise ValueError("H0 target response target count must be zero")
        if (metadata.get("target_response_status") != "NOT_APPLICABLE_NO_TARGETS"
                or int(metadata.get("target_response_row_count", -1)) != 0
                or int(metadata.get("target_roi_row_count", -1)) != 0):
            raise ValueError("H0 target response must be explicitly empty and not applicable")
        if metadata.get("truth_mapping_status") != "NOT_APPLICABLE_NO_TARGETS":
            raise ValueError("H0 truth mapping must be not applicable")
    else:
        truth_files = [
            name for name in members
            if name.startswith(f"{case_root}/scenes/") and name.endswith("/truth/truth_targets_by_beam.csv")
        ]
        if not truth_files:
            raise ValueError("target-present case lacks target truth files")
        metadata = payload_metadata or {}
        association_count = metadata.get("target_truth_association_count")
        response_count = metadata.get("target_response_target_count")
        if association_count is None or int(association_count) <= 0:
            raise ValueError("target-present case requires non-empty target truth associations")
        if response_count is None or int(response_count) <= 0:
            raise ValueError("target-present case requires a non-empty target complex ROI")
        if (metadata.get("target_response_status") != "EVALUABLE"
                or int(metadata.get("target_response_row_count", 0)) <= 0
                or int(metadata.get("target_roi_row_count", 0)) <= 0):
            raise ValueError("target-present response and ROI must contain evaluated target samples")
        if metadata.get("truth_mapping_status") != "passed":
            raise ValueError("target-present truth mapping audit must pass")

    missing = sorted(required - members)
    if missing:
        raise ValueError(f"case schema is missing required members: {missing[:10]}")

    branches = _branch_inventory(members, case_root)
    expected_methods = {"A0"} if delay == 0 else set(PLUS_FOUR_METHODS)
    unexpected_methods = set(branches) - expected_methods
    if unexpected_methods:
        if delay == 0 and unexpected_methods & CORRECTION_METHODS:
            raise ValueError(
                "zero-delay case contains correction method branches: "
                f"{sorted(unexpected_methods & CORRECTION_METHODS)}"
            )
        raise ValueError(f"case contains unregistered V4 methods: {sorted(unexpected_methods)}")
    missing_methods = expected_methods - set(branches)
    if missing_methods:
        raise ValueError(f"case lacks required method branches: {sorted(missing_methods)}")

    supplied_roles = case_identity.get("branch_roles")
    actual_roles: list[dict[str, str]] = []
    production_branches: list[str] = []
    for name in sorted(members):
        marker = f"{case_root}/production/"
        if name.startswith(marker) and "/" in name[len(marker):]:
            branch = name[len(marker):].split("/", 1)[0]
            identity = _method_role(branch)
            if identity and {"method_id": identity[0], "role": identity[1]} not in actual_roles:
                actual_roles.append({"method_id": identity[0], "role": identity[1]})
            if name.endswith("/branch_manifest.json"):
                production_branches.append(branch)
    if supplied_roles is not None:
        supplied_set = {
            (str(row.get("method_id")), str(row.get("role")))
            for row in supplied_roles if isinstance(row, Mapping)
        }
        actual_set = {(row["method_id"], row["role"]) for row in actual_roles}
        if supplied_set != actual_set:
            raise ValueError("case identity branch_roles do not match archive member branches")

    required_methods_roles = {
        method: ({"ON"} if phase == "h0" else {"ON", "TO"})
        for method in expected_methods
    }
    missing_roles = {
        method: sorted(roles - branches.get(method, set()))
        for method, roles in required_methods_roles.items()
        if roles - branches.get(method, set())
    }
    if missing_roles:
        raise ValueError(f"target-present method lacks ON/TO role or H0 lacks ON: {missing_roles}")

    branch_fixed = (
        "branch_manifest.json", "production.xml", "gmt_pipe_core.log",
        "result/debug/cfar_geometry_diagnostics.csv",
    )
    missing_production: list[str] = []
    for branch in production_branches:
        base = f"{case_root}/production/{branch}/"
        missing_production.extend(base + name for name in branch_fixed if base + name not in members)
        missing_production.extend(
            base + f"result/detection_results_GMTI{period:02d}.csv"
            for period in range(1, 11)
            if base + f"result/detection_results_GMTI{period:02d}.csv" not in members
        )
        track_prefix = base + "track_debug_runs/"
        missing_production.extend(
            track_prefix + "*/" + filename for filename in TRACK_DEBUG_FILES
            if not any(name.startswith(track_prefix) and name.endswith("/" + filename) for name in members)
        )
    if missing_production:
        raise ValueError(f"missing required Production member or TrackManager lineage: {missing_production[:10]}")

    auxiliary_roles = sorted({
        row["role"] for row in actual_roles
        if row["role"] != "ON" and phase == "h0"
    })
    optional = sorted(name for name in members if "/production/" in name and (
        name.split("/production/", 1)[1].split("/", 1)[0].endswith("_OFF")
        or phase == "h0" and name.split("/production/", 1)[1].split("/", 1)[0].endswith("_TO")
    ))
    return {
        "status": "PASS",
        "schema_name": schema_name,
        "phase_family": phase,
        "delay_ns": delay,
        "target_count": target_count,
        "case_root": case_root,
        "allowlist_patterns": allowlist_patterns,
        "forbidden_member_patterns": (
            [f"{case_root}/*{suffix}" for suffix in H0_FORBIDDEN_MEMBER_SUFFIXES]
            if phase == "h0" else []
        ),
        "required_members": sorted(required),
        "optional_role_names": sorted({"OFF", "TO"} if phase == "h0" else {"OFF"}),
        "forbidden_member_count": 0,
        "missing_required_member_count": 0,
        "forbidden_members_checked": True,
        "expected_method_ids": sorted(expected_methods),
        "branch_roles": sorted(actual_roles, key=lambda row: (row["method_id"], row["role"])),
        "production_branch_count": len(production_branches),
        "production_branches": sorted(production_branches),
        "auxiliary_roles": auxiliary_roles,
        "target_response_status": metadata.get("target_response_status"),
        "target_response_target_count": int(metadata.get("target_response_target_count", 0)),
        "target_response_row_count": int(metadata.get("target_response_row_count", 0)),
        "target_roi_row_count": int(metadata.get("target_roi_row_count", 0)),
        "truth_mapping_status": metadata.get("truth_mapping_status"),
        "optional_member_count": len(optional),
        "optional_members": optional,
        "detection_csvs_per_branch": 10,
        "track_debug_files_required_per_branch": list(TRACK_DEBUG_FILES),
        "evaluator_policy": "H0 false alarm tables are separate from target truth association",
    }
