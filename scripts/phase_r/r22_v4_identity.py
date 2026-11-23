"""Shared identity and runtime-configuration checks for R22 V4 branches."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping


_VALID_MODES = {"Mode-A", "Mode-B", "not_applicable"}
_VALID_ROLES = {"ON", "OFF", "TO"}


def normalize_mode(value: object) -> str:
    if value is None or str(value).strip().lower() in {"", "na", "n/a", "not_applicable"}:
        return "not_applicable"
    raw = str(value).strip()
    canonical = {item.lower(): item for item in _VALID_MODES}.get(raw.lower())
    if canonical is None:
        raise ValueError(f"unknown R22 V4 production mode: {raw}")
    return canonical


def branch_identity(
    record: Mapping[str, Any], *, case_id: str | None = None,
) -> dict[str, Any]:
    resolved_case_id = str(record.get("case_id") or case_id or "").strip()
    if not resolved_case_id:
        raise ValueError("R22 V4 branch lacks case_id")
    if case_id is not None and str(case_id) != resolved_case_id:
        raise ValueError(
            f"R22 V4 branch case_id mismatch: expected {case_id}, found {resolved_case_id}"
        )
    method_id = str(record.get("method_id") or "").strip()
    condition = str(record.get("condition") or "").strip()
    role = str(record.get("role") or "").strip()
    branch_name = str(record.get("branch_name") or "").strip()
    if not method_id or not condition or not branch_name:
        raise ValueError(
            "R22 V4 branch identity requires distinct explicit method_id, condition, and branch_name"
        )
    if role not in _VALID_ROLES:
        raise ValueError(f"R22 V4 branch has invalid role: {role or '<missing>'}")
    source_config = record.get("source_configuration_identity")
    if not isinstance(source_config, Mapping):
        raise ValueError(f"R22 V4 branch lacks source configuration identity: {branch_name}")
    return {
        "case_id": resolved_case_id,
        "method_id": method_id,
        "condition": condition,
        "mode": normalize_mode(record.get("mode")),
        "role": role,
        "branch_name": branch_name,
        "source_configuration_identity": dict(source_config),
    }


def branch_identity_key(identity: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    return tuple(str(identity[key]) for key in (
        "case_id", "condition", "method_id", "mode", "role", "branch_name",
    ))  # type: ignore[return-value]


def branch_group_key(identity: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(identity[key]) for key in (
        "case_id", "condition", "method_id", "mode",
    ))  # type: ignore[return-value]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_source_configuration_identity(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("R22 V4 source configuration identity is missing")
    method_config = value.get("method_config")
    source_xml = value.get("source_xml")
    overrides_sha = str(value.get("branch_overrides_sha256") or "")
    if (not isinstance(method_config, Mapping)
            or len(str(method_config.get("sha256") or "")) != 64
            or not str(method_config.get("path") or "")):
        raise ValueError("R22 V4 method configuration SHA-256 is missing")
    if (not isinstance(source_xml, Mapping)
            or len(str(source_xml.get("sha256") or "")) != 64
            or not str(source_xml.get("path") or "")):
        raise ValueError("R22 V4 source XML SHA-256 is missing")
    if len(overrides_sha) != 64:
        raise ValueError("R22 V4 branch override SHA-256 is missing")
    return dict(value)


def validate_branch_manifest_identity(
    manifest: Mapping[str, Any],
    *,
    identity: Mapping[str, Any],
    branch_root: Path,
    case_root: Path,
) -> dict[str, Any]:
    """Validate branch-manifest identity, config source, and materialized XML."""
    branch_root = Path(branch_root).resolve()
    case_root = Path(case_root).resolve()
    for key in ("case_id", "method_id", "condition", "role", "branch_name"):
        if str(manifest.get(key) or "") != str(identity.get(key) or ""):
            raise ValueError(
                f"R22 V4 branch manifest identity mismatch for {key}: {branch_root}"
            )
    try:
        manifest_mode = normalize_mode(manifest.get("mode"))
    except ValueError as exc:
        raise ValueError(f"R22 V4 branch manifest mode is invalid: {branch_root}") from exc
    if manifest_mode != identity.get("mode") or manifest.get("mode") is None:
        raise ValueError(f"R22 V4 branch manifest mode mismatch: {branch_root}")
    source_config = validate_source_configuration_identity(
        manifest.get("source_configuration_identity")
    )
    if source_config != identity.get("source_configuration_identity"):
        raise ValueError(f"R22 V4 source configuration identity mismatch: {branch_root}")

    source_xml = source_config["source_xml"]
    source_xml_path = Path(str(source_xml["path"])).expanduser()
    source_xml_path = (
        source_xml_path.resolve() if source_xml_path.is_absolute()
        else (case_root / source_xml_path).resolve()
    )
    try:
        source_xml_path.relative_to(case_root)
    except ValueError as exc:
        raise ValueError(f"R22 V4 source XML is outside case root: {source_xml_path}") from exc
    if (not source_xml_path.is_file()
            or source_xml_path.stat().st_size != int(source_xml.get("bytes", -1))
            or _sha256(source_xml_path) != str(source_xml["sha256"])):
        raise ValueError(f"R22 V4 source XML content identity mismatch: {source_xml_path}")

    method_config = source_config["method_config"]
    method_config_path = Path(str(method_config["path"])).expanduser()
    method_config_path = (
        method_config_path.resolve() if method_config_path.is_absolute()
        else (case_root / method_config_path).resolve()
    )
    try:
        method_config_path.relative_to(case_root)
    except ValueError as exc:
        raise ValueError(f"R22 V4 method config is outside case root: {method_config_path}") from exc
    if (not method_config_path.is_file()
            or method_config_path.stat().st_size != int(method_config.get("bytes", -1))
            or _sha256(method_config_path) != str(method_config["sha256"])):
        raise ValueError(f"R22 V4 method config content identity mismatch: {method_config_path}")

    xml_path = branch_root / "production.xml"
    declared_xml = manifest.get("production_xml")
    if not declared_xml:
        raise ValueError(f"R22 V4 branch manifest lacks runtime XML path: {branch_root}")
    declared_path = Path(str(declared_xml)).expanduser()
    declared_path = declared_path.resolve() if declared_path.is_absolute() else (branch_root / declared_path).resolve()
    if declared_path != xml_path.resolve() or not xml_path.is_file():
        raise ValueError(f"R22 V4 runtime XML identity path mismatch: {branch_root}")

    xml_identity = manifest.get("runtime_xml_identity")
    if not isinstance(xml_identity, Mapping):
        raise ValueError(f"R22 V4 runtime XML identity is missing: {branch_root}")
    expected_relative = xml_path.relative_to(case_root).as_posix()
    if (str(xml_identity.get("path") or "") != expected_relative
            or int(xml_identity.get("bytes", -1)) != xml_path.stat().st_size
            or str(xml_identity.get("sha256") or "") != _sha256(xml_path)):
        raise ValueError(f"R22 V4 runtime XML content identity mismatch: {branch_root}")

    overrides = manifest.get("xml_overrides")
    if not isinstance(overrides, Mapping) or not overrides:
        raise ValueError(f"R22 V4 runtime XML overrides are missing: {branch_root}")
    actual_override_sha = hashlib.sha256(json.dumps(
        dict(overrides), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    if actual_override_sha != source_config["branch_overrides_sha256"]:
        raise ValueError(f"R22 V4 branch override identity mismatch: {branch_root}")
    try:
        xml_root = ET.parse(xml_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"R22 V4 runtime XML is invalid: {branch_root}: {exc}") from exc
    actual_fields: dict[str, str] = {}
    for node in xml_root.iter():
        tag = str(node.tag).rsplit("}", 1)[-1]
        if tag in overrides:
            actual_fields[tag] = (node.text or "").strip()
    mismatches = {
        str(key): (str(value).strip(), actual_fields.get(str(key)))
        for key, value in overrides.items()
        if actual_fields.get(str(key)) != str(value).strip()
    }
    if mismatches:
        raise ValueError(f"R22 V4 runtime XML fields mismatch {branch_root}: {mismatches}")
    actual_mode = actual_fields.get("research_calibration_mode")
    expected_mode = str(identity.get("mode"))
    mode_mismatch = (
        actual_mode is not None and normalize_mode(actual_mode) != "not_applicable"
        if expected_mode == "not_applicable"
        else actual_mode != expected_mode
    )
    if mode_mismatch:
        raise ValueError(f"R22 V4 runtime XML mode mismatch: {branch_root}")
    return {
        "runtime_xml": {
            "path": expected_relative,
            "bytes": xml_path.stat().st_size,
            "sha256": _sha256(xml_path),
        },
        "source_configuration_identity": source_config,
    }
