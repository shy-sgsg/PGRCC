#!/usr/bin/env python3
"""Hash, archive, verify, then remove one completed R22 case output tree."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping

from scripts.phase_r.r22_v4_identity import (
    branch_group_key,
    branch_identity,
    branch_identity_key,
    validate_branch_manifest_identity,
)


_PER_CASE_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_RAW_ARRAY_SUFFIXES = {".bin", ".npy", ".npz"}
_CASE_MANIFEST_EXTRACT_KEYS = (
    "case_id", "status", "simulation", "area_clutter_gate", "causal_triplets",
    "evidence_gate", "input_contract", "inputs", "pair_identity", "protocol_layout",
    "delay_estimator", "r22_blind_method_qualification", "r22_delay_estimator",
    "r22_causal_prefix_estimators", "r22_batch_m1_diagnostic", "r22_v4_target_response",
    "method_branches",
    "r22_evaluation_summary", "r22_status_labels", "raw_cleanup", "residual_closure",
    "scene_identity", "selected_calibration_modes", "selected_methods",
    "source_xml_sha256", "truth_used_in_estimator",
)


def _exclusion_reason(relative_path: str, archive_profile: str = "legacy") -> str | None:
    path = Path(relative_path)
    parts = path.parts
    if archive_profile not in {"legacy", "r22_v4_output_complete"}:
        raise ValueError(f"unknown R22 archive profile: {archive_profile}")
    if path.suffix.lower() in _PER_CASE_IMAGE_SUFFIXES:
        return "reproducible_per_case_plot; retain aggregate plots"
    if path.suffix.lower() == ".f32":
        return "regenerable_dense_map; retain geometry and tap summaries"
    if archive_profile == "r22_v4_output_complete":
        return None
    if path.name == "doppler_opportunity_audit.csv":
        return "reconstructible_doppler_detail; retain parameter_metrics"
    if "track_debug_runs" in parts:
        return "raw_track_debug_detail; retain track_level_metrics and target_associations"
    if path.name == "case_manifest.json":
        return "expanded_case_manifest; compact case manifest extract retained"
    if path.name == "raw_cleanup.json":
        return "cleanup identity retained in compact case manifest extract"
    if path.name in {"truth_pulse.csv", "moving_target_truth.csv", "target_injection_amplitudes.csv"}:
        return "duplicated_pulse_level_truth; retain target metrics and truth manifest"
    if "references" in parts and "production" not in parts:
        return "duplicated_estimator_reference; retain executed ON branch reference"
    if "production" in parts:
        production_index = parts.index("production")
        branch = parts[production_index + 1] if len(parts) > production_index + 1 else ""
        is_on_role = branch.endswith("_ON")
        in_result = "result" in parts[production_index + 2:]
        if path.name == "gmt_pipe_core.log" and not is_on_role:
            return "repeated_non_on_production_log; retain branch manifest and ON logs"
        if path.name in {"production_calibration_reference_gamma.csv", "production_calibration_adapter.csv"} and not is_on_role:
            return "repeated_non_on_calibration_detail; retain executed ON branch records"
        if in_result and not is_on_role:
            if path.name.startswith("detection_results") or path.name == "azimuth_localization_debug.csv":
                return "repeated_non_on_detection_dump; retain evaluator metrics and ON diagnostics"
            if re.fullmatch(r"GMTI\d+\.txt", path.name):
                return "repeated_non_on_stage_dump; retain production logs and ON diagnostics"
        if in_result and is_on_role:
            if (path.name.startswith("detection_results")
                    and path.name != "detection_results_GMTI09.csv"):
                return "repeated_intermediate_detection_dump; retain final ON detection and evaluator tables"
            if path.name == "azimuth_localization_debug.csv":
                return "duplicated_parameter_debug; retain parameter_metrics"
    return None


def _relative_to_case(path: Path, case_root: Path) -> str:
    resolved = Path(path).expanduser().resolve()
    try:
        return resolved.relative_to(case_root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"V4 required artifact is outside case root: {resolved}") from exc


def _v4_branch_group_roles(
    records: list[Mapping[str, Any]], *, case_id: str, target_count: int,
) -> dict[tuple[str, str, str, str], set[str]]:
    groups: dict[tuple[str, str, str, str], set[str]] = {}
    identities: set[tuple[str, str, str, str, str, str]] = set()
    for record in records:
        identity = branch_identity(record, case_id=case_id)
        key = branch_identity_key(identity)
        if key in identities:
            raise ValueError(f"duplicate V4 Production branch identity: {key}")
        identities.add(key)
        group_key = branch_group_key(identity)
        roles = groups.setdefault(group_key, set())
        if identity["role"] in roles:
            raise ValueError(f"duplicate V4 Production role for method/mode: {group_key}/{identity['role']}")
        roles.add(str(identity["role"]))
    if not groups:
        raise ValueError("V4 complete archive has no Production role manifests")
    for group_key, roles in groups.items():
        required_roles = {"ON"} if target_count == 0 else {"ON", "TO"}
        if group_key[3] in {"Mode-A", "Mode-B"}:
            required_roles = {"ON", "OFF", "TO"}
        if not required_roles <= roles:
            raise ValueError(
                f"V4 method/mode lacks required roles {sorted(required_roles)}: {group_key}"
            )
    return groups


def _v4_required_members(case_root: Path, files: list[Path]) -> list[str]:
    """Validate V4 output completeness and return members that must be retained."""
    regular_files = {item.resolve() for item in files if item.is_file()}
    required: set[str] = set()
    response_manifests = [
        item for item in files
        if item.name == "target_complex_response_manifest.json"
        and "r22_v4_output_evidence" in item.parts
    ]
    if len(response_manifests) != 1:
        raise ValueError(
            "V4 complete archive requires one target_complex_response_manifest.json"
        )
    response_manifest_path = response_manifests[0]
    response_manifest = json.loads(response_manifest_path.read_text(encoding="utf-8"))
    labels = response_manifest.get("status_labels", {})
    if (response_manifest.get("status") not in {"EVALUABLE", "NOT_APPLICABLE_NO_TARGETS"}
            or labels.get("IMPLEMENTED") is not True
            or labels.get("EXECUTED") is not True
            or labels.get("EVALUABLE") is not True
            or labels.get("PERFORMANCE_VERIFIED") is not False):
        raise ValueError("V4 target-response evidence is incomplete or has invalid status labels")
    extractor_identity = response_manifest.get("extractor_identity")
    extractor_sha = (
        extractor_identity.get("sha256")
        if isinstance(extractor_identity, Mapping) else None
    )
    if not isinstance(extractor_sha, str) or len(extractor_sha) != 64:
        raise ValueError("V4 target-response extractor identity is missing")
    required.add(_relative_to_case(response_manifest_path, case_root))
    for name, identity in response_manifest.get("files", {}).items():
        record_path = response_manifest_path.parent / str(name)
        if not record_path.is_file():
            raise ValueError(f"V4 target-response member is missing: {record_path}")
        if not isinstance(identity, Mapping):
            raise ValueError(f"V4 target-response file identity is invalid: {name}")
        if (record_path.stat().st_size != identity.get("bytes")
                or _sha256_file(record_path) != identity.get("sha256")):
            raise ValueError(f"V4 target-response file identity mismatch: {record_path}")
        required.add(_relative_to_case(record_path, case_root))
    truth_audit_path = response_manifest_path.parent / "truth_mapping_audit.json"
    truth_audit = json.loads(truth_audit_path.read_text(encoding="utf-8"))
    pipeline_root = response_manifest_path.parent.parent
    target_count = int(response_manifest.get("target_count_design", -1))
    if target_count > 0:
        if (truth_audit.get("status") != "passed"
                or set(truth_audit.get("roles_compared", [])) !=
                {"A0_ON", "A1_ON", "A0_TO", "A1_TO"}):
            raise ValueError("V4 archive target truth mapping audit is incomplete")
        truth_sources = truth_audit.get("sources")
        required_truth_roles = {"A0_ON", "A1_ON", "A0_TO", "A1_TO"}
        case_seed = truth_audit.get("case_seed")
        seeds_by_role = truth_audit.get("seeds_by_role")
        if (isinstance(case_seed, bool) or not isinstance(case_seed, int)
                or not isinstance(seeds_by_role, Mapping)
                or set(seeds_by_role) != required_truth_roles
                or any(value != case_seed for value in seeds_by_role.values())):
            raise ValueError("V4 archive target truth seed identity is incomplete or inconsistent")
        scenario_sources = truth_audit.get("scenario_sources")
        for source_key, inventory in (
            ("sources", truth_sources), ("scenario_sources", scenario_sources),
        ):
            if not isinstance(inventory, Mapping) or set(inventory) != required_truth_roles:
                raise ValueError(f"V4 archive {source_key} inventory is incomplete")
            for role, identity in inventory.items():
                if not isinstance(identity, Mapping) or identity.get("exists") is not True:
                    raise ValueError(f"V4 archive truth source identity is invalid: {source_key}/{role}")
                source_path = Path(str(identity.get("path") or ""))
                if source_path.is_absolute() or ".." in source_path.parts:
                    raise ValueError(f"V4 archive truth source path is unsafe: {source_key}/{role}")
                source = (pipeline_root / source_path).resolve()
                if (not source.is_file()
                        or source.stat().st_size != int(identity.get("bytes", -1))
                        or _sha256_file(source) != str(identity.get("sha256") or "")):
                    raise ValueError(
                        f"V4 archive truth source content identity mismatch: {source_key}/{role}"
                    )
                if source_key == "scenario_sources" and identity.get("seed") != case_seed:
                    raise ValueError(f"V4 archive scenario seed identity mismatch: {role}")
                required.add(_relative_to_case(source, case_root))
    elif target_count != 0 or truth_audit.get("status") != "NOT_APPLICABLE_NO_TARGETS":
        raise ValueError("V4 archive target-free truth state is invalid")

    run_manifests = [item for item in files if item.name == "r22_run_manifest.json"]
    if len(run_manifests) != 1:
        raise ValueError("V4 archive requires one R22 execution manifest")
    run_manifest_path = run_manifests[0]
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    case_id = str(run_manifest.get("case_id") or "")
    if not case_id:
        raise ValueError("V4 execution manifest lacks case_id")
    run_design = run_manifest.get("design")
    run_seed = run_design.get("seed") if isinstance(run_design, Mapping) else None
    if isinstance(run_seed, bool) or not isinstance(run_seed, int):
        raise ValueError("V4 execution manifest lacks an integer case seed")
    design = run_manifest.get("design")
    if not isinstance(design, Mapping) or int(design.get("target_count", -1)) != target_count:
        raise ValueError("V4 run and target-response design counts differ")
    source_identity = run_manifest.get("source_identity")
    source_files = source_identity.get("files", {}) if isinstance(source_identity, Mapping) else {}
    required_sources = (
        "scripts/phase_r/run_r22_cuda_pilot.py",
        "scripts/phase_r/r22_scenario_factory.py",
        "scripts/phase_r/r22_v4_evidence.py",
        "scripts/phase_r/r22_compact_archive.py",
        "scripts/phase_r/r22_experiment_matrix.py",
        "scripts/phase_r/run_r12_physics_matched_delay.py",
        "scripts/phase_r/run_r21_cuda_pilot.py",
        "scripts/phase_r/r21_task_evaluator.py",
        "scripts/phase_r/r21_historical_task_review.py",
        "scripts/run_delay_stage1_formal.py",
        "scripts/run_production_hierarchical_calibration_pilot.py",
        "scripts/run_track_manager_e2e.py",
    )
    missing_sources = [
        name for name in required_sources
        if not isinstance(source_files.get(name), Mapping)
        or len(str(source_files.get(name, {}).get("sha256", ""))) != 64
    ]
    if missing_sources:
        raise ValueError(f"V4 execution manifest lacks source identities: {missing_sources}")
    extractor_record = source_files.get("scripts/phase_r/r22_v4_evidence.py")
    if not isinstance(extractor_record, Mapping) or extractor_record.get("sha256") != extractor_sha:
        raise ValueError("V4 extractor hash differs from the R22 execution source snapshot")
    native_preflight = run_manifest.get("native_cuda_preflight")
    gpu_identity = native_preflight.get("gpu") if isinstance(native_preflight, Mapping) else None
    driver_identity = gpu_identity.get("driver_identity") if isinstance(gpu_identity, Mapping) else None
    if (not isinstance(run_manifest.get("stage2_binary"), Mapping)
            or not run_manifest["stage2_binary"].get("sha256")
            or not isinstance(run_manifest.get("production_binary"), Mapping)
            or not run_manifest["production_binary"].get("sha256")
            or not isinstance(native_preflight, Mapping)
            or not isinstance(gpu_identity, Mapping)
            or gpu_identity.get("available") is not True
            or not isinstance(driver_identity, Mapping)
            or not driver_identity.get("driver_versions")):
        raise ValueError("V4 R22 execution manifest lacks binary or CUDA preflight identities")
    required.add(_relative_to_case(run_manifest_path, case_root))

    pipeline_case_manifests = [
        item for item in files
        if item.name == "case_manifest.json" and {"pipeline", "cases"} <= set(item.parts)
    ]
    if len(pipeline_case_manifests) != 1:
        raise ValueError("V4 archive requires one pipeline case manifest")
    pipeline_case_manifest_path = pipeline_case_manifests[0]
    pipeline_case_root = pipeline_case_manifest_path.parent
    pipeline_manifest = json.loads(pipeline_case_manifest_path.read_text(encoding="utf-8"))
    if str(pipeline_manifest.get("case_id") or "") != case_id:
        raise ValueError("V4 pipeline case manifest identity differs from run manifest")
    pipeline_scene_identity = pipeline_manifest.get("scene_identity")
    pipeline_case_identity = pipeline_manifest.get("case")
    pipeline_seed = (
        pipeline_scene_identity.get("seed") if isinstance(pipeline_scene_identity, Mapping) else None
    )
    pipeline_case_seed = (
        pipeline_case_identity.get("seed") if isinstance(pipeline_case_identity, Mapping) else None
    )
    if (pipeline_seed != run_seed or pipeline_case_seed != run_seed):
        raise ValueError("V4 pipeline, run, and scene seed identities differ")
    expected_records = pipeline_manifest.get("method_branches")
    if not isinstance(expected_records, list):
        raise ValueError("V4 pipeline manifest lacks Production branch inventory")
    expected_branch_keys: set[tuple[str, str, str, str, str, str]] = set()
    for record in expected_records:
        if not isinstance(record, Mapping) or str(record.get("role", "")) not in {"ON", "OFF", "TO"}:
            continue
        if record.get("status") != "passed" or not record.get("branch_name"):
            raise ValueError("V4 pipeline manifest contains an incomplete Production branch")
        expected_branch_keys.add(branch_identity_key(branch_identity(record, case_id=case_id)))
    required.add(_relative_to_case(pipeline_case_manifest_path, case_root))

    branch_manifests = [
        item for item in files
        if item.name == "branch_manifest.json" and "production" in item.parts
    ]
    actual_branch_keys: set[tuple[str, str, str, str, str, str]] = set()
    actual_branch_records: list[Mapping[str, Any]] = []
    for manifest_path in branch_manifests:
        record = json.loads(manifest_path.read_text(encoding="utf-8"))
        role = str(record.get("role", ""))
        if role not in {"ON", "OFF", "TO"}:
            continue
        identity = branch_identity(record, case_id=case_id)
        branch_key = branch_identity_key(identity)
        if branch_key in actual_branch_keys:
            raise ValueError(f"duplicate V4 Production branch identity: {branch_key}")
        actual_branch_keys.add(branch_key)
        actual_branch_records.append(record)
        branch_name = str(identity["branch_name"])
        method_id = str(identity["method_id"])
        required.add(_relative_to_case(manifest_path, case_root))
        branch_root = manifest_path.parent
        identity_check = validate_branch_manifest_identity(
            record, identity=identity, branch_root=branch_root, case_root=pipeline_case_root,
        )
        required.add(_relative_to_case(branch_root / "production.xml", case_root))
        for item in (identity["source_configuration_identity"]["method_config"],
                     identity["source_configuration_identity"]["source_xml"]):
            identity_path = Path(str(item["path"]))
            if identity_path.is_absolute():
                identity_path = identity_path.resolve()
            else:
                identity_path = (pipeline_case_root / identity_path).resolve()
            required.add(_relative_to_case(identity_path, case_root))
        if identity_check["runtime_xml"]["sha256"] != record["runtime_xml_identity"]["sha256"]:
            raise ValueError(f"V4 branch runtime XML identity is inconsistent: {branch_name}")
        for fixed_name in ("production.xml", "gmt_pipe_core.log"):
            artifact = branch_root / fixed_name
            if not artifact.is_file():
                raise ValueError(f"V4 branch execution evidence missing: {artifact}")
            required.add(_relative_to_case(artifact, case_root))
        result_raw = Path(str(record.get("result_dir", branch_root / "result")))
        result_dir = result_raw if result_raw.is_absolute() else branch_root / result_raw
        if not result_dir.is_dir():
            raise ValueError(f"V4 Production result directory is missing: {result_dir}")
        detection_files = sorted(result_dir.rglob("detection_results_GMTI*.csv"))
        expected_period_count = int(record.get("period_count_expected", 0))
        if expected_period_count <= 0 or len(detection_files) != expected_period_count:
            raise ValueError(
                f"V4 detection period count mismatch for {branch_name}: "
                f"expected {expected_period_count}, found {len(detection_files)}"
            )
        period_numbers = {
            int(match.group(1))
            for path in detection_files
            if (match := re.fullmatch(r"detection_results_GMTI(\d+)\.csv", path.name))
        }
        zero_based = set(range(expected_period_count))
        one_based = set(range(1, expected_period_count + 1))
        if frozenset(period_numbers) not in {frozenset(zero_based), frozenset(one_based)}:
            raise ValueError(f"V4 Detection files do not cover every period for {branch_name}")
        required.update(_relative_to_case(path, case_root) for path in detection_files)

        debug_raw = record.get("track_debug_dir")
        debug_dir = Path(str(debug_raw)) if debug_raw else branch_root / "track_debug_runs"
        if not debug_dir.is_absolute():
            debug_dir = branch_root / debug_dir
        if not debug_dir.is_dir():
            raise ValueError(f"V4 TrackManager debug directory is missing: {debug_dir}")
        payloads = sorted(debug_dir.rglob("track_output_payloads.csv"))
        if not payloads:
            raise ValueError(f"V4 TrackManager PIPE payload audit missing: {debug_dir}")
        debug_files = [item for item in debug_dir.rglob("*") if item.is_file()]
        required.update(_relative_to_case(path, case_root) for path in debug_files)

        geometry_files = sorted({
            *branch_root.rglob("cfar_geometry_diagnostics.csv"),
            *branch_root.rglob("cfar_geometry_diagnostics.json"),
        })
        if not geometry_files:
            raise ValueError(f"V4 CFAR geometry diagnostics are missing: {branch_name}")
        required.update(_relative_to_case(path, case_root) for path in geometry_files)

    if actual_branch_keys != expected_branch_keys:
        raise ValueError("V4 archived branch identities differ from pipeline manifest")
    _v4_branch_group_roles(actual_branch_records, case_id=case_id, target_count=target_count)

    missing = sorted(path for path in required
                     if not (case_root / path).is_file() or (case_root / path).resolve() not in regular_files)
    if missing:
        raise ValueError(f"V4 required archive members are missing: {missing[:5]}")
    return sorted(required)


def _v4_verified_dense_csi_exclusions(
    case_root: Path, files: list[Path], *, require_complete_output: bool,
) -> dict[str, str]:
    """Exclude only CSI arrays whose exact hashes are bound by V4 response evidence."""
    if not require_complete_output:
        return {}
    manifests = [item for item in files if item.name == "target_complex_response_manifest.json"]
    if len(manifests) != 1:
        raise ValueError("V4 dense CSI cleanup requires one response evidence manifest")
    manifest_path = manifests[0]
    response = json.loads(manifest_path.read_text(encoding="utf-8"))
    pipeline_root = manifest_path.parent.parent
    exclusions: dict[str, str] = {}
    for state in response.get("period_processing_states", []):
        if not isinstance(state, Mapping):
            raise ValueError("V4 period processing state is invalid")
        arrays = state.get("tap_arrays")
        if not isinstance(arrays, Mapping):
            raise ValueError("V4 tap array identity inventory is missing")
        for record in arrays.values():
            if not isinstance(record, Mapping) or record.get("exists") is not True:
                raise ValueError("V4 tap array source identity is incomplete")
            relative = Path(str(record.get("path") or ""))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("V4 tap array path is unsafe")
            source = (pipeline_root / relative).resolve()
            try:
                source.relative_to(case_root.resolve())
            except ValueError as exc:
                raise ValueError(f"V4 tap array is outside case root: {source}") from exc
            if (not source.is_file()
                    or source.stat().st_size != int(record.get("bytes", -1))
                    or _sha256_file(source) != str(record.get("sha256") or "")):
                raise ValueError(f"V4 tap array identity mismatch: {source}")
            if source.suffix.lower() not in {".npy", ".npz"} or "csi_metrics" not in source.parts:
                continue
            exclusions[_relative_to_case(source, case_root)] = (
                "dense_csi_array_exact_hash_retained_in_v4_response_manifest"
            )
    return exclusions


def _compact_case_manifest_extract(case_root: Path, files: list[Path]) -> bytes | None:
    candidates = [
        item for item in files
        if item.name == "case_manifest.json" and {"pipeline", "cases"} <= set(item.parts)
    ]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise ValueError(f"expected one pipeline case_manifest.json, found {len(candidates)}")
    value = json.loads(candidates[0].read_text(encoding="utf-8"))
    compact = {key: value[key] for key in _CASE_MANIFEST_EXTRACT_KEYS if key in value}
    return (json.dumps(
        compact, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
    ) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _archive_symlink_record(
    path: Path,
    case_root: Path,
    target_identity_cache: dict[str, tuple[int, str]],
) -> dict[str, Any]:
    relative = path.relative_to(case_root).as_posix()
    if path.is_dir():
        raise ValueError(f"case archive refuses directory symlink: {relative}")
    try:
        target = path.resolve(strict=True)
        target_relative = target.relative_to(case_root).as_posix()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"case archive symlink target is missing or outside the case root: {relative}") from exc
    if not target.is_file() or target == path:
        raise ValueError(f"case archive symlink target is not a regular case file: {relative}")
    link_target = posixpath.relpath(target_relative, posixpath.dirname(relative) or ".")
    target_key = os.fspath(target)
    identity = target_identity_cache.get(target_key)
    if identity is None:
        identity = (target.stat().st_size, _sha256_file(target))
        target_identity_cache[target_key] = identity
    return {
        "path": relative,
        "target_case_path": target_relative,
        "archive_link_target": link_target,
        "bytes": identity[0],
        "sha256": identity[1],
    }


def _verify_archive_symlinks(
    archive: zipfile.ZipFile,
    symlink_entries: Any,
    included_files: Any,
) -> list[str]:
    if not isinstance(symlink_entries, list) or not isinstance(included_files, list):
        raise ValueError("case archive symlink/member manifest is invalid")
    included_by_path = {
        str(record.get("path")): record
        for record in included_files if isinstance(record, Mapping)
    }
    seen: set[str] = set()
    paths: list[str] = []
    for record in symlink_entries:
        if not isinstance(record, Mapping):
            raise ValueError("case archive symlink identity is invalid")
        member = str(record.get("path") or "")
        target_case_path = str(record.get("target_case_path") or "")
        link_target = str(record.get("archive_link_target") or "")
        member_path, target_path = Path(member), Path(target_case_path)
        if (not member or member_path.is_absolute() or ".." in member_path.parts
                or not target_case_path or target_path.is_absolute() or ".." in target_path.parts
                or member in seen or member in included_by_path):
            raise ValueError(f"case archive symlink path is unsafe or duplicated: {member}")
        seen.add(member)
        paths.append(member)
        resolved_link_target = posixpath.normpath(
            posixpath.join(posixpath.dirname(member), link_target)
        )
        if (not link_target or link_target.startswith("/")
                or resolved_link_target != target_case_path
                or target_case_path not in included_by_path):
            raise ValueError(f"case archive symlink target is unsafe or absent: {member}")
        target_record = included_by_path[target_case_path]
        if (int(record.get("bytes", -1)) != int(target_record.get("bytes", -2))
                or str(record.get("sha256") or "") != str(target_record.get("sha256") or "")):
            raise ValueError(f"case archive symlink target identity differs: {member}")
        try:
            info = archive.getinfo(member)
        except KeyError as exc:
            raise ValueError(f"case archive symlink member is missing: {member}") from exc
        if not stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError(f"case archive link is not encoded as a symlink: {member}")
        if archive.read(member) != link_target.encode("utf-8"):
            raise ValueError(f"case archive symlink target text differs: {member}")
    return paths


def verify_case_archive(
    archive_path: Path,
    *,
    expected_sha256: str,
    expected_manifest_sha256: str,
    archive_profile: str,
    require_complete_output: bool = False,
) -> dict[str, Any]:
    """Verify a previously published archive before resume can skip its case.

    The archive digest binds the exact ZIP that passed member SHA and CRC
    verification when it was published. Resume rechecks that digest, the
    archive-manifest digest, member inventory, profile, required V4 members,
    and current ZIP CRCs before trusting the prior case state.
    """
    path = Path(archive_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"case archive is missing: {path}")
    archive_sha = _sha256_file(path)
    if archive_sha != expected_sha256:
        raise ValueError(f"case archive SHA-256 mismatch: {path}")
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError(f"case archive contains duplicate member names: {path}")
        if "ARCHIVE_MANIFEST.json" not in names:
            raise ValueError(f"case archive manifest is missing: {path}")
        manifest_bytes = archive.read("ARCHIVE_MANIFEST.json")
        manifest_sha = _sha256_bytes(manifest_bytes)
        if manifest_sha != expected_manifest_sha256:
            raise ValueError(f"case archive manifest SHA-256 mismatch: {path}")
        manifest = json.loads(manifest_bytes)
        if manifest.get("archive_profile") != archive_profile:
            raise ValueError(f"case archive profile differs from matrix manifest: {path}")
        included = manifest.get("included_files")
        if not isinstance(included, list):
            raise ValueError(f"case archive member manifest is invalid: {path}")
        included_names: list[str] = []
        for record in included:
            if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
                raise ValueError(f"case archive member identity is invalid: {path}")
            member = Path(record["path"])
            if member.is_absolute() or ".." in member.parts:
                raise ValueError(f"case archive member path is unsafe: {record['path']}")
            included_names.append(record["path"])
        if len(included_names) != len(set(included_names)):
            raise ValueError(f"case archive manifest contains duplicate paths: {path}")
        symlink_entries = manifest.get("symlink_entries", [])
        symlink_names = _verify_archive_symlinks(archive, symlink_entries, included)
        if set(included_names) & set(symlink_names):
            raise ValueError(f"case archive duplicates regular and symlink member paths: {path}")
        expected_names = set(included_names) | set(symlink_names) | {"ARCHIVE_MANIFEST.json"}
        if set(names) != expected_names:
            raise ValueError(f"case archive member list differs from its manifest: {path}")
        required_v4 = manifest.get("required_v4_members", [])
        if require_complete_output:
            if archive_profile != "r22_v4_output_complete" or not isinstance(required_v4, list):
                raise ValueError(f"case archive has no verified V4 completeness record: {path}")
            if not set(required_v4) <= set(included_names):
                raise ValueError(f"case archive omits a required V4 member: {path}")
        corrupt = archive.testzip()
        if corrupt is not None:
            raise ValueError(f"case archive CRC check failed at {corrupt}: {path}")
    return {
        "archive_path": str(path),
        "archive_sha256": archive_sha,
        "archive_manifest_sha256": manifest_sha,
        "archive_bytes": path.stat().st_size,
        "archive_profile": archive_profile,
        "crc_verified": True,
        "member_manifest_verified": True,
    }


def archive_case_directory(
    case_root: Path,
    archive_path: Path,
    *,
    raw_root: Path,
    remove_source: bool = True,
    archive_profile: str = "legacy",
    require_complete_output: bool = False,
) -> dict[str, Any]:
    """Create and verify a compact archive before deleting a case directory.

    Numeric input/diagnostic arrays must already have been handled by the case
    runner. Repeated raster plots are omitted with hashes and reasons; aggregate
    plots are retained separately. Any error leaves the source directory intact.
    """
    raw_base = Path(raw_root).expanduser().resolve()
    source = Path(case_root).expanduser().resolve()
    destination = Path(archive_path).expanduser().resolve()
    if not source.is_dir() or source.is_symlink():
        raise ValueError(f"case root must be an existing non-symlink directory: {source}")
    try:
        source.relative_to(raw_base)
    except ValueError as exc:
        raise ValueError(f"case root is outside raw root: {source}") from exc
    if source == raw_base:
        raise ValueError("refusing to archive or remove the raw root itself")
    if archive_profile not in {"legacy", "r22_v4_output_complete"}:
        raise ValueError(f"unknown R22 archive profile: {archive_profile}")
    if require_complete_output and archive_profile != "r22_v4_output_complete":
        raise ValueError("complete-output validation requires the R22 V4 archive profile")
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("archive path must be outside the case root")
    files = sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix())
    symlink_paths = [item for item in files if item.is_symlink()]
    if symlink_paths and archive_profile != "r22_v4_output_complete":
        raise ValueError("case tree contains a symlink; refusing to archive an ambiguous path")
    regular_files = [item for item in files if item.is_file()]
    symlink_target_identity_cache: dict[str, tuple[int, str]] = {}
    symlink_entries = [
        _archive_symlink_record(item, source, symlink_target_identity_cache)
        for item in symlink_paths
    ]
    raw_files = [item for item in regular_files if item.suffix.lower() in _RAW_ARRAY_SUFFIXES]
    if raw_files and archive_profile != "r22_v4_output_complete":
        names = ", ".join(item.relative_to(source).as_posix() for item in raw_files[:5])
        raise ValueError(f"raw array remains after runner cleanup: {names}")

    required_members = (
        _v4_required_members(source, regular_files)
        if require_complete_output else []
    )
    verified_dense_exclusions = _v4_verified_dense_csi_exclusions(
        source, regular_files,
        require_complete_output=(archive_profile == "r22_v4_output_complete" and require_complete_output),
    )

    generated_extract = _compact_case_manifest_extract(source, regular_files)
    if generated_extract is not None and (source / "case_manifest_review_extract.json").exists():
        raise ValueError("case tree already contains a generated manifest extract")
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for item in regular_files:
        if item.is_symlink():
            continue
        relative = item.relative_to(source).as_posix()
        size = item.stat().st_size
        digest = _sha256_file(item)
        reason = verified_dense_exclusions.get(relative) or _exclusion_reason(relative, archive_profile)
        if reason is not None:
            excluded.append({
                "path": relative,
                "reason": reason,
                "bytes": size,
                "sha256": digest,
            })
        else:
            included.append({"path": relative, "bytes": size, "sha256": digest})
    if generated_extract is not None:
        included.append({
            "path": "case_manifest_review_extract.json",
            "bytes": len(generated_extract),
            "sha256": _sha256_bytes(generated_extract),
            "generated_from": "pipeline/cases/*/case_manifest.json",
        })
        included.sort(key=lambda record: record["path"])

    included_paths = {item["path"] for item in included}
    symlink_paths_in_archive = {item["path"] for item in symlink_entries}
    if not set(required_members) <= included_paths | symlink_paths_in_archive:
        raise ValueError("V4 archive policy excluded required evidence members")
    for link in symlink_entries:
        if link["target_case_path"] not in included_paths:
            raise ValueError(
                f"V4 archive symlink target is not retained as a regular member: {link['path']}"
            )
    manifest = {
        "schema_version": 1,
        "archive_profile": archive_profile,
        "archive_policy": (
            "all_case_files_except_reproducible_rasters_and_hash-bound_dense_csi_arrays"
            if archive_profile == "r22_v4_output_complete"
            else "all_case_files_except_reproducible_raster_plots"
        ),
        "required_v4_members": required_members,
        "included_files": included,
        "excluded_files": excluded,
        "symlink_entries": symlink_entries,
        "included_bytes": sum(item["bytes"] for item in included),
        "excluded_bytes": sum(item["bytes"] for item in excluded),
        "symlink_target_bytes": sum(item["bytes"] for item in symlink_entries),
    }
    manifest_bytes = (json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
    ) + "\n").encode("utf-8")

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
            allowZip64=True,
        ) as archive:
            for record in included:
                if record["path"] == "case_manifest_review_extract.json":
                    archive.writestr(record["path"], generated_extract)
                else:
                    item = source / record["path"]
                    archive.write(item, arcname=record["path"])
            for record in symlink_entries:
                info = zipfile.ZipInfo(record["path"])
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, record["archive_link_target"].encode("utf-8"))
            archive.writestr("ARCHIVE_MANIFEST.json", manifest_bytes)

        with zipfile.ZipFile(temporary, mode="r") as archive:
            if archive.testzip() is not None:
                raise OSError("case archive failed ZIP CRC verification")
            archived_names = set(archive.namelist())
            _verify_archive_symlinks(archive, symlink_entries, included)
            expected_names = (
                {item["path"] for item in included}
                | {item["path"] for item in symlink_entries}
                | {"ARCHIVE_MANIFEST.json"}
            )
            if archived_names != expected_names:
                raise OSError("case archive file list differs from its manifest")
            if archive.read("ARCHIVE_MANIFEST.json") != manifest_bytes:
                raise OSError("case archive manifest changed during packaging")
            for record in included:
                payload = archive.read(record["path"])
                if len(payload) != record["bytes"] or _sha256_bytes(payload) != record["sha256"]:
                    raise OSError(f"case archive content verification failed: {record['path']}")

        # Hard-link publication is atomic. If a prior interrupted attempt already
        # published this exact archive, verify it and reuse it without overwriting.
        try:
            os.link(temporary, destination)
            reused_existing_archive = False
        except FileExistsError:
            reused_existing_archive = True
        if reused_existing_archive:
            with zipfile.ZipFile(destination, mode="r") as archive:
                if archive.testzip() is not None:
                    raise OSError("existing case archive failed ZIP CRC verification")
                archived_names = set(archive.namelist())
                _verify_archive_symlinks(archive, symlink_entries, included)
                expected_names = (
                    {item["path"] for item in included}
                    | {item["path"] for item in symlink_entries}
                    | {"ARCHIVE_MANIFEST.json"}
                )
                if archived_names != expected_names:
                    raise OSError("existing case archive file list differs from its manifest")
                if archive.read("ARCHIVE_MANIFEST.json") != manifest_bytes:
                    raise OSError("existing case archive manifest differs from current case")
                for record in included:
                    payload = archive.read(record["path"])
                    if len(payload) != record["bytes"] or _sha256_bytes(payload) != record["sha256"]:
                        raise OSError(f"existing case archive content mismatch: {record['path']}")
        temporary.unlink()
        archive_sha = _sha256_file(destination)
        if remove_source:
            shutil.rmtree(source)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return {
        "archive_path": str(destination),
        "archive_sha256": archive_sha,
        "archive_profile": archive_profile,
        "archive_bytes": destination.stat().st_size,
        "included_file_count": len(included),
        "included_bytes": manifest["included_bytes"],
        "excluded_file_count": len(excluded),
        "excluded_bytes": manifest["excluded_bytes"],
        "symlink_count": len(symlink_entries),
        "symlink_target_bytes": manifest["symlink_target_bytes"],
        "archive_manifest_sha256": _sha256_bytes(manifest_bytes),
        "raw_case_removed": remove_source,
        "reused_existing_archive": reused_existing_archive,
    }
