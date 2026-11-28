#!/usr/bin/env python3
"""Resumable, integrity-checked R22 V4 archives with per-shard source cleanup."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shutil
import stat
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from scripts.phase_r import r22_compact_archive as compact


ARCHIVE_SET_FORMAT = "r22_v4_output_complete_zip_shard_set_v1"
COMPACT_PROFILE = "r22_v4_size_pilot_compact"
SHARD_MANIFEST = "SHARD_MANIFEST.json"
_COMPACT_EVALUATOR_MEMBERS = (
    "evaluation/doppler_opportunity_audit.csv",
    "evaluation/h0_false_alarm_metrics.csv",
    "evaluation/h0_task_metrics.csv",
    "evaluation/parameter_metrics.csv",
    "evaluation/target_associations.csv",
    "evaluation/target_level_metrics.csv",
    "evaluation/task_metrics.csv",
    "evaluation/track_level_metrics.csv",
)
_CHUNK_BYTES = 1024 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
    ) + "\n").encode("utf-8")


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _hash_stream(stream: Any) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for block in iter(lambda: stream.read(_CHUNK_BYTES), b""):
        digest.update(block)
        count += len(block)
    return digest.hexdigest(), count


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_member_path(value: str) -> None:
    member = Path(value)
    if (not value or member.is_absolute() or ".." in member.parts
            or "\\" in value):
        raise ValueError(f"unsafe R22 archive member path: {value!r}")


def _validate_roots(case_root: Path, archive_set_path: Path, raw_root: Path) -> tuple[Path, Path, Path]:
    source = Path(case_root).expanduser().resolve()
    destination = Path(archive_set_path).expanduser().resolve()
    raw_base = Path(raw_root).expanduser().resolve()
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"case root must be an existing non-symlink directory: {source}")
    try:
        source.relative_to(raw_base)
    except ValueError as exc:
        raise ValueError(f"case root is outside raw root: {source}") from exc
    if source == raw_base:
        raise ValueError("refusing to archive or remove the raw root itself")
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("archive-set manifest must be outside the case root")
    return source, destination, raw_base


def _make_archive_manifest(
    source: Path,
    *,
    archive_profile: str,
    require_complete_output: bool,
    case_schema_identity: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    if archive_profile != "r22_v4_output_complete":
        raise ValueError("sharded archive is restricted to the R22 V4 complete-output profile")
    if case_schema_identity is not None and not require_complete_output:
        raise ValueError("case-schema identity requires a complete V4 output archive")

    files = sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix())
    symlink_paths = [item for item in files if item.is_symlink()]
    regular_files = [item for item in files if item.is_file()]
    symlink_target_identity_cache: dict[str, tuple[int, str]] = {}
    symlink_entries = [
        compact._archive_symlink_record(item, source, symlink_target_identity_cache)
        for item in symlink_paths
    ]
    required_members = (
        compact._v4_required_members(source, regular_files)
        if require_complete_output else []
    )
    dense_exclusions = compact._v4_verified_dense_csi_exclusions(
        source, regular_files,
        require_complete_output=require_complete_output,
    )
    generated_extract = compact._compact_case_manifest_extract(source, regular_files)
    if generated_extract is not None and (source / "case_manifest_review_extract.json").exists():
        raise ValueError("case tree already contains a generated manifest extract")

    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    source_count = 0
    source_bytes = 0
    for item in regular_files:
        if item.is_symlink():
            continue
        relative = item.relative_to(source).as_posix()
        size = item.stat().st_size
        digest = compact._sha256_file(item)
        source_count += 1
        source_bytes += size
        reason = dense_exclusions.get(relative) or compact._exclusion_reason(
            relative, archive_profile,
        )
        record = {"path": relative, "bytes": size, "sha256": digest}
        if reason is None:
            included.append(record)
        else:
            excluded.append({**record, "reason": reason})
    if generated_extract is not None:
        included.append({
            "path": "case_manifest_review_extract.json",
            "bytes": len(generated_extract),
            "sha256": _hash_bytes(generated_extract),
            "generated_from": "pipeline/cases/*/case_manifest.json",
        })
    included.sort(key=lambda item: item["path"])
    excluded.sort(key=lambda item: item["path"])
    symlink_entries.sort(key=lambda item: item["path"])
    source_count += len(symlink_entries)
    source_bytes += sum(int(item["bytes"]) for item in symlink_entries)

    included_paths = {item["path"] for item in included}
    symlink_paths_in_archive = {item["path"] for item in symlink_entries}
    schema_identity: dict[str, Any] | None = None
    case_schema: dict[str, Any] | None = None
    if case_schema_identity is not None:
        schema_identity = {
            key: case_schema_identity[key]
            for key in ("case_id", "phase", "target_count", "delay_error_ns")
            if key in case_schema_identity
        }
        case_schema = compact._confirmatory_case_schema(
            source, sorted(included_paths), schema_identity,
        )
        required_members = sorted(
            set(required_members) | set(case_schema["required_members"])
        )
    if not set(required_members) <= included_paths | symlink_paths_in_archive:
        raise ValueError("V4 archive policy excluded required evidence members")
    for link in symlink_entries:
        if link["target_case_path"] not in included_paths:
            raise ValueError(
                f"V4 archive symlink target is not retained as a regular member: {link['path']}"
            )

    run_path = source / "r22_run_manifest.json"
    run_manifest = json.loads(run_path.read_text(encoding="utf-8")) if run_path.is_file() else {}
    manifest = {
        "schema_version": 1,
        "archive_profile": archive_profile,
        "case_phase": run_manifest.get("phase"),
        "case_schema_identity": schema_identity,
        "case_schema_identity_sha256": (
            hashlib.sha256(json.dumps(
                schema_identity, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")).hexdigest()
            if schema_identity is not None else None
        ),
        "case_schema": case_schema,
        "archive_policy": "all_case_files_except_reproducible_rasters_and_hash-bound_dense_csi_arrays",
        "required_v4_members": required_members,
        "included_files": included,
        "excluded_files": excluded,
        "symlink_entries": symlink_entries,
        "included_bytes": sum(int(item["bytes"]) for item in included),
        "excluded_bytes": sum(int(item["bytes"]) for item in excluded),
        "symlink_target_bytes": sum(int(item["bytes"]) for item in symlink_entries),
    }
    generated_members = (
        {"case_manifest_review_extract.json": generated_extract.decode("utf-8")}
        if generated_extract is not None else {}
    )
    return {
        "archive_manifest": manifest,
        "source_file_count": source_count,
        "source_file_bytes": source_bytes,
        "source_excluded_file_count": len(excluded),
        "source_excluded_bytes": sum(int(item["bytes"]) for item in excluded),
    }, generated_members


def _member_descriptors(manifest: Mapping[str, Any], generated: Mapping[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = [{
        "path": "ARCHIVE_MANIFEST.json",
        "kind": "archive_manifest",
        "bytes": len(_json_bytes(manifest["archive_manifest"])),
        "sha256": _hash_bytes(_json_bytes(manifest["archive_manifest"])),
    }]
    for record in manifest["archive_manifest"]["included_files"]:
        descriptor = {
            "path": str(record["path"]),
            "kind": "generated" if record.get("generated_from") else "file",
            "bytes": int(record["bytes"]),
            "sha256": str(record["sha256"]),
        }
        if descriptor["kind"] == "generated":
            if descriptor["path"] not in generated:
                raise ValueError(f"generated archive member payload is missing: {descriptor['path']}")
        result.append(descriptor)
    for record in manifest["archive_manifest"]["symlink_entries"]:
        result.append({
            "path": str(record["path"]),
            "kind": "symlink",
            "bytes": len(str(record["archive_link_target"]).encode("utf-8")),
            "sha256": _hash_bytes(str(record["archive_link_target"]).encode("utf-8")),
            "target_case_path": str(record["target_case_path"]),
            "archive_link_target": str(record["archive_link_target"]),
        })
    names = [item["path"] for item in result]
    if len(names) != len(set(names)):
        raise ValueError("sharded archive inventory contains duplicate paths")
    for name in names:
        _safe_member_path(name)
    result[0:1] = sorted(result[0:1], key=lambda item: item["path"])
    return result


def _make_shard_plan(descriptors: Sequence[Mapping[str, Any]], max_bytes: int) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 0
    for item in descriptors:
        descriptor = dict(item)
        item_bytes = max(1, int(descriptor["bytes"]))
        if current and current_bytes + item_bytes > max_bytes:
            groups.append(current)
            current = []
            current_bytes = 0
        current.append(descriptor)
        current_bytes += item_bytes
    if current:
        groups.append(current)
    return [
        {"index": index, "members": members,
         "uncompressed_member_bytes": sum(int(item["bytes"]) for item in members)}
        for index, members in enumerate(groups)
    ]


def _set_plan_sha256(value: Mapping[str, Any]) -> str:
    identity = {
        "archive_manifest_sha256": value["archive_manifest_sha256"],
        "shard_plan": value["shard_plan"],
        "archive_profile": value["archive_profile"],
    }
    return _hash_bytes(json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8"))


def _shard_directory(archive_set_path: Path) -> Path:
    return Path(str(archive_set_path) + ".shards")


def _shard_path(archive_set_path: Path, index: int) -> Path:
    return _shard_directory(archive_set_path) / f"part_{index:04d}.zip"


def _owned_shard_temporaries(
    archive_set_path: Path, shard_plan: Iterable[Mapping[str, Any]],
) -> list[Path]:
    shard_dir = _shard_directory(archive_set_path)
    if shard_dir.is_symlink():
        raise ValueError(f"refusing to clean temporary files through a shard-directory symlink: {shard_dir}")
    found: list[Path] = []
    for plan in shard_plan:
        destination = _shard_path(archive_set_path, int(plan["index"]))
        for candidate in shard_dir.glob(f".{destination.name}.*.tmp"):
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"unexpected owned shard temporary type: {candidate}")
            found.append(candidate)
    return found


def _shard_record_for_archive(
    archive_set_path: Path,
    index: int,
    verified: Mapping[str, Any],
) -> dict[str, Any]:
    value = dict(verified)
    value["index"] = index
    value["path"] = _shard_path(archive_set_path, index).relative_to(
        archive_set_path.parent,
    ).as_posix()
    return value


def _shard_manifest_bytes(plan_sha256: str, shard_plan: Mapping[str, Any]) -> bytes:
    return _json_bytes({
        "schema_version": 1,
        "archive_format": ARCHIVE_SET_FORMAT,
        "plan_sha256": plan_sha256,
        "shard_index": int(shard_plan["index"]),
        "members": shard_plan["members"],
    })


def _verify_sharded_archive_part(
    path: Path,
    *,
    archive_set: Mapping[str, Any],
    shard_plan: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {str(item["path"]): item for item in shard_plan["members"]}
    shard_manifest_bytes = _shard_manifest_bytes(
        str(archive_set["plan_sha256"]), shard_plan,
    )
    expected_names = set(expected) | {SHARD_MANIFEST}
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != expected_names:
            raise ValueError(f"shard member inventory mismatch: {path}")
        if archive.testzip() is not None:
            raise ValueError(f"shard CRC check failed: {path}")
        if archive.read(SHARD_MANIFEST) != shard_manifest_bytes:
            raise ValueError(f"shard manifest mismatch: {path}")
        for name, descriptor in expected.items():
            info = archive.getinfo(name)
            if descriptor["kind"] == "symlink":
                if not stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError(f"archive member lost symlink mode: {name}")
                payload = archive.read(name)
                if (payload.decode("utf-8") != str(descriptor["archive_link_target"])
                        or _hash_bytes(payload) != descriptor["sha256"]):
                    raise ValueError(f"archive symlink payload mismatch: {name}")
                continue
            with archive.open(name, "r") as stream:
                digest, count = _hash_stream(stream)
            if count != int(descriptor["bytes"]) or digest != descriptor["sha256"]:
                raise ValueError(f"archive member content mismatch: {name}")
    return {
        "path": str(path),
        "sha256": compact._sha256_file(path),
        "bytes": path.stat().st_size,
        "members": sorted(expected),
        "member_count": len(expected),
        "uncompressed_member_bytes": int(shard_plan["uncompressed_member_bytes"]),
        "crc_verified": True,
        "member_manifest_verified": True,
        "status": "VERIFIED",
    }


def _write_sharded_archive_part(
    source: Path,
    archive_set_path: Path,
    archive_set: Mapping[str, Any],
    shard_plan: Mapping[str, Any],
) -> dict[str, Any]:
    destination = _shard_path(archive_set_path, int(shard_plan["index"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    manifest_bytes = _json_bytes(archive_set["archive_manifest"])
    generated = archive_set.get("generated_members", {})
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED,
            compresslevel=6, allowZip64=True,
        ) as archive:
            for descriptor in shard_plan["members"]:
                member = str(descriptor["path"])
                kind = descriptor["kind"]
                if kind == "symlink":
                    info = zipfile.ZipInfo(member)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    info.compress_type = zipfile.ZIP_STORED
                    archive.writestr(info, str(descriptor["archive_link_target"]).encode("utf-8"))
                elif kind == "archive_manifest":
                    archive.writestr(member, manifest_bytes)
                elif kind == "generated":
                    archive.writestr(member, str(generated[member]).encode("utf-8"))
                elif kind == "file":
                    source_path = source / member
                    if (not source_path.is_file() or source_path.is_symlink()
                            or source_path.stat().st_size != int(descriptor["bytes"])
                            or compact._sha256_file(source_path) != descriptor["sha256"]):
                        raise ValueError(f"source file changed before sharding: {source_path}")
                    archive.write(source_path, arcname=member)
                else:
                    raise ValueError(f"unknown sharded archive member kind: {kind}")
            archive.writestr(
                SHARD_MANIFEST,
                _shard_manifest_bytes(str(archive_set["plan_sha256"]), shard_plan),
            )
        result = _verify_sharded_archive_part(
            temporary, archive_set=archive_set, shard_plan=shard_plan,
        )
        try:
            os.link(temporary, destination)
        except FileExistsError:
            existing = _verify_sharded_archive_part(
                destination, archive_set=archive_set, shard_plan=shard_plan,
            )
            if existing["sha256"] != result["sha256"]:
                raise ValueError(f"existing archive shard differs from retry output: {destination}")
        temporary.unlink()
        return _verify_sharded_archive_part(
            destination, archive_set=archive_set, shard_plan=shard_plan,
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _remove_verified_shard_sources(
    source: Path,
    archive_set: Mapping[str, Any],
    shard_plan: Mapping[str, Any],
) -> int:
    removed = 0
    for descriptor in shard_plan["members"]:
        kind = str(descriptor["kind"])
        if kind == "symlink":
            _safe_member_path(str(descriptor["path"]))
            path = source / str(descriptor["path"])
            if not path.is_symlink():
                if path.exists():
                    raise ValueError(f"source symlink identity changed before cleanup: {path}")
                continue
            target_case_path = str(descriptor.get("target_case_path", ""))
            _safe_member_path(target_case_path)
            expected_target = (source / target_case_path).resolve(strict=False)
            actual_target = path.resolve(strict=False)
            included_target = next((item for item in archive_set["archive_manifest"].get(
                "included_files", [],
            ) if item.get("path") == target_case_path), None)
            symlink_identity = next((item for item in archive_set["archive_manifest"].get(
                "symlink_entries", [],
            ) if item.get("path") == descriptor["path"]), None)
            verified_members = {
                str(member)
                for shard in archive_set.get("shards", [])
                if shard.get("status") == "VERIFIED"
                for member in shard.get("members", [])
            }
            if (actual_target != expected_target
                    or not isinstance(included_target, Mapping)
                    or not isinstance(symlink_identity, Mapping)
                    or symlink_identity.get("target_case_path") != target_case_path
                    or int(included_target.get("bytes", -1)) != int(symlink_identity.get("bytes", -2))
                    or included_target.get("sha256") != symlink_identity.get("sha256")
                    or target_case_path not in verified_members):
                raise ValueError(f"source symlink target changed before cleanup: {path}")
            if actual_target.exists() and (
                    not actual_target.is_file()
                    or actual_target.stat().st_size != int(symlink_identity["bytes"])
                    or compact._sha256_file(actual_target) != symlink_identity["sha256"]):
                raise ValueError(f"source symlink target identity changed before cleanup: {path}")
            path.unlink()
            removed += 1
            continue
        if kind != "file":
            continue
        _safe_member_path(str(descriptor["path"]))
        path = source / str(descriptor["path"])
        if path.is_symlink():
            raise ValueError(f"source file became a symlink before cleanup: {path}")
        if not path.exists():
            continue
        if (not path.is_file() or path.stat().st_size != int(descriptor["bytes"])
                or compact._sha256_file(path) != descriptor["sha256"]):
            raise ValueError(f"source file identity changed before cleanup: {path}")
        path.unlink()
        removed += 1
    return removed


_REGENERABLE_EXCLUSION_REASONS = {
    "reproducible_per_case_plot; retain aggregate plots",
    "regenerable_dense_map; retain geometry and tap summaries",
    "dense_csi_array_exact_hash_retained_in_v4_response_manifest",
}


def _remove_verified_regenerable_exclusions(
    source: Path,
    archive_set: Mapping[str, Any],
) -> dict[str, int]:
    removed_count = 0
    removed_bytes = 0
    preserved_count = 0
    manifest = archive_set["archive_manifest"]
    for descriptor in manifest.get("excluded_files", []):
        reason = str(descriptor.get("reason", ""))
        if reason not in _REGENERABLE_EXCLUSION_REASONS:
            preserved_count += 1
            continue
        relative = str(descriptor.get("path", ""))
        _safe_member_path(relative)
        path = source / relative
        if path.is_symlink():
            raise ValueError(f"excluded source file became a symlink before cleanup: {path}")
        if not path.exists():
            continue
        if (not path.is_file()
                or path.stat().st_size != int(descriptor["bytes"])
                or compact._sha256_file(path) != descriptor["sha256"]):
            raise ValueError(f"excluded source file identity changed before cleanup: {path}")
        path.unlink()
        removed_count += 1
        removed_bytes += int(descriptor["bytes"])
    return {
        "regenerable_excluded_file_count_removed": removed_count,
        "regenerable_excluded_bytes_removed": removed_bytes,
        "unrecognized_exclusion_count_preserved": preserved_count,
    }


def _remove_empty_case_directories(source: Path) -> bool:
    if not source.exists():
        return True
    for directory in sorted(source.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if directory.is_dir() and not directory.is_symlink():
            try:
                directory.rmdir()
            except OSError:
                pass
    try:
        source.rmdir()
    except OSError:
        pass
    return not source.exists()


def _remaining_source_inventory(source: Path) -> dict[str, int]:
    if not source.exists():
        return {"remaining_file_count": 0, "remaining_file_bytes": 0,
                "remaining_symlink_count": 0}
    files = [item for item in source.rglob("*") if item.is_file() and not item.is_symlink()]
    links = [item for item in source.rglob("*") if item.is_symlink()]
    return {
        "remaining_file_count": len(files),
        "remaining_file_bytes": sum(item.stat().st_size for item in files),
        "remaining_symlink_count": len(links),
    }


def _cleanup_source_after_full_verification(
    source: Path,
    archive_set: Mapping[str, Any],
) -> dict[str, Any]:
    removed_included = sum(
        _remove_verified_shard_sources(source, archive_set, plan)
        for plan in archive_set.get("shard_plan", [])
    ) if source.exists() else 0
    excluded = (
        _remove_verified_regenerable_exclusions(source, archive_set)
        if source.exists() else {
            "regenerable_excluded_file_count_removed": 0,
            "regenerable_excluded_bytes_removed": 0,
            "unrecognized_exclusion_count_preserved": 0,
        }
    )
    case_directory_removed = _remove_empty_case_directories(source)
    remaining = _remaining_source_inventory(source)
    return {
        "status": (
            "CASE_DIRECTORY_REMOVED_AFTER_FULL_ARCHIVE_VERIFICATION"
            if case_directory_removed else "UNINVENTORIED_OR_UNRECOGNIZED_FILES_PRESERVED"
        ),
        "case_path": str(source),
        "case_directory_removed": case_directory_removed,
        "verified_shard_source_entries_removed": removed_included,
        **excluded,
        **remaining,
        "finished_at_utc": _utc_now(),
    }


def _archive_set_identity_sha256(archive_set: Mapping[str, Any]) -> str:
    shards = sorted(archive_set.get("shards", []), key=lambda item: int(item["index"]))
    identity = {
        "archive_profile": archive_set["archive_profile"],
        "archive_manifest_sha256": archive_set["archive_manifest_sha256"],
        "shards": [
            {"index": item["index"], "sha256": item["sha256"], "bytes": item["bytes"]}
            for item in shards
        ],
    }
    return _hash_bytes(json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8"))


def archive_case_directory_sharded(
    case_root: Path,
    archive_set_path: Path,
    *,
    raw_root: Path,
    archive_profile: str = "r22_v4_output_complete",
    require_complete_output: bool = True,
    max_shard_uncompressed_bytes: int = 512 * 1024 * 1024,
    remove_source: bool = True,
    case_schema_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write and verify ZIP shards, checkpoint each, then remove only their source files.

    The archive-set JSON retains a complete member inventory and each shard digest.
    A failed shard leaves its source data in place; a retry verifies earlier shards
    and resumes at the first unfinished one.
    """
    if (isinstance(max_shard_uncompressed_bytes, bool)
            or not isinstance(max_shard_uncompressed_bytes, int)
            or max_shard_uncompressed_bytes <= 0):
        raise ValueError("max_shard_uncompressed_bytes must be a positive integer")
    source = Path(case_root).expanduser().resolve()
    destination = Path(archive_set_path).expanduser().resolve()
    raw_base = Path(raw_root).expanduser().resolve()
    try:
        source.relative_to(raw_base)
    except ValueError as exc:
        raise ValueError(f"case root is outside raw root: {source}") from exc
    if source == raw_base:
        raise ValueError("refusing to archive or remove the raw root itself")
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("archive-set manifest must be outside the case root")
    if destination.exists():
        archive_set = json.loads(destination.read_text(encoding="utf-8"))
        if (archive_set.get("archive_format") != ARCHIVE_SET_FORMAT
                or archive_set.get("archive_profile") != archive_profile
                or archive_set.get("case_root") != str(source)
                or archive_set.get("max_shard_uncompressed_bytes") != max_shard_uncompressed_bytes
                or archive_set.get("require_complete_output") is not require_complete_output):
            raise ValueError(f"existing sharded archive identity differs: {destination}")
        if archive_set.get("status") == "FULL_VERIFIED":
            verified = verify_sharded_case_archive(
                destination,
                expected_sha256=compact._sha256_file(destination),
                expected_manifest_sha256=str(archive_set["archive_manifest_sha256"]),
                archive_profile=archive_profile,
                require_complete_output=require_complete_output,
            )
            if remove_source and source.exists():
                cleanup = _cleanup_source_after_full_verification(source, archive_set)
                cleanup["verified_shard_source_entries_removed"] = (
                    int(archive_set.get("source_cleanup", {}).get(
                        "verified_shard_source_entries_removed", 0,
                    )) + int(cleanup["verified_shard_source_entries_removed"])
                )
                archive_set["source_cleanup"] = cleanup
                _write_json_atomic(destination, archive_set)
            return _archive_result(
                destination, archive_set, verified,
                resumed_shard_count=len(archive_set.get("shards", [])),
            )
        if archive_set.get("status") in {"COMPACT_VERIFIED", "COMPACTED_CLEANED"}:
            raise ValueError(
                "archive set has advanced to compact cleanup; resume with compact_sharded_case_archive"
            )
    else:
        if source.is_symlink() or not source.is_dir():
            raise ValueError(f"case root must be an existing non-symlink directory: {source}")
        prepared, generated = _make_archive_manifest(
            source,
            archive_profile=archive_profile,
            require_complete_output=require_complete_output,
            case_schema_identity=case_schema_identity,
        )
        inner_bytes = _json_bytes(prepared["archive_manifest"])
        prepared["archive_manifest_sha256"] = _hash_bytes(inner_bytes)
        prepared["generated_members"] = generated
        descriptors = _member_descriptors(prepared, generated)
        shard_plan = _make_shard_plan(descriptors, max_shard_uncompressed_bytes)
        archive_set = {
            "schema_version": 1,
            "archive_format": ARCHIVE_SET_FORMAT,
            "archive_profile": archive_profile,
            "case_root": str(source),
            "raw_root": str(raw_base),
            "require_complete_output": require_complete_output,
            "max_shard_uncompressed_bytes": max_shard_uncompressed_bytes,
            "archive_manifest": prepared["archive_manifest"],
            "archive_manifest_sha256": prepared["archive_manifest_sha256"],
            "generated_members": generated,
            "source_file_count": prepared["source_file_count"],
            "source_file_bytes": prepared["source_file_bytes"],
            "source_excluded_file_count": prepared["source_excluded_file_count"],
            "source_excluded_bytes": prepared["source_excluded_bytes"],
            "shard_plan": shard_plan,
            "shards": [],
            "status": "ARCHIVING",
            "started_at_utc": _utc_now(),
        }
        archive_set["plan_sha256"] = _set_plan_sha256(archive_set)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"refusing to overwrite archive-set manifest: {destination}")
        _write_json_atomic(destination, archive_set)

    if not source.exists() and archive_set.get("status") != "FULL_VERIFIED":
        raise FileNotFoundError(f"raw case is missing before full archive verification: {source}")
    if not archive_set.get("plan_sha256") or _set_plan_sha256(archive_set) != archive_set["plan_sha256"]:
        raise ValueError("sharded archive plan digest is invalid")
    existing_shards = {
        int(item["index"]): item for item in archive_set.get("shards", [])
    }
    resumed_count = len(existing_shards)
    for shard_plan in archive_set["shard_plan"]:
        index = int(shard_plan["index"])
        destination_shard = _shard_path(destination, index)
        prior = existing_shards.get(index)
        if prior is not None:
            verified_part = _verify_sharded_archive_part(
                destination_shard, archive_set=archive_set, shard_plan=shard_plan,
            )
            if (prior.get("sha256") != verified_part["sha256"]
                    or prior.get("bytes") != verified_part["bytes"]):
                raise ValueError(f"checkpointed archive shard identity differs: {destination_shard}")
        elif destination_shard.exists():
            verified_part = _verify_sharded_archive_part(
                destination_shard, archive_set=archive_set, shard_plan=shard_plan,
            )
            existing_shards[index] = _shard_record_for_archive(
                destination, index, verified_part,
            )
            archive_set["shards"] = [existing_shards[key] for key in sorted(existing_shards)]
            _write_json_atomic(destination, archive_set)
        else:
            verified_part = _write_sharded_archive_part(
                source, destination, archive_set, shard_plan,
            )
            existing_shards[index] = _shard_record_for_archive(
                destination, index, verified_part,
            )
            archive_set["shards"] = [existing_shards[key] for key in sorted(existing_shards)]
            _write_json_atomic(destination, archive_set)
        if remove_source:
            removed = _remove_verified_shard_sources(source, archive_set, shard_plan)
            if removed:
                archive_set.setdefault("source_cleanup", {}).setdefault(
                    "verified_shard_source_entries_removed", 0,
                )
                archive_set["source_cleanup"]["verified_shard_source_entries_removed"] += removed
                archive_set["source_cleanup"]["last_shard_removed_at_utc"] = _utc_now()
                _write_json_atomic(destination, archive_set)

    archive_set["shards"] = [existing_shards[key] for key in sorted(existing_shards)]
    archive_set["status"] = "FULL_VERIFIED"
    archive_set["full_archive_verified_at_utc"] = _utc_now()
    archive_set["archive_set_identity_sha256"] = _archive_set_identity_sha256(archive_set)
    archive_set["archive_bytes"] = sum(int(item["bytes"]) for item in archive_set["shards"])
    archive_set["included_file_count"] = len(archive_set["archive_manifest"]["included_files"])
    archive_set["included_bytes"] = int(archive_set["archive_manifest"]["included_bytes"])
    archive_set["excluded_file_count"] = len(archive_set["archive_manifest"]["excluded_files"])
    archive_set["excluded_bytes"] = int(archive_set["archive_manifest"]["excluded_bytes"])
    archive_set["symlink_count"] = len(archive_set["archive_manifest"]["symlink_entries"])
    _write_json_atomic(destination, archive_set)
    verified = verify_sharded_case_archive(
        destination,
        expected_sha256=compact._sha256_file(destination),
        expected_manifest_sha256=str(archive_set["archive_manifest_sha256"]),
        archive_profile=archive_profile,
        require_complete_output=require_complete_output,
    )
    if remove_source and source.exists():
        cleanup = _cleanup_source_after_full_verification(source, archive_set)
        cleanup["verified_shard_source_entries_removed"] = (
            int(archive_set.get("source_cleanup", {}).get("verified_shard_source_entries_removed", 0))
            + int(cleanup["verified_shard_source_entries_removed"])
        )
        archive_set["source_cleanup"] = cleanup
        _write_json_atomic(destination, archive_set)
    return _archive_result(
        destination, archive_set, verified, resumed_shard_count=resumed_count,
    )


def verify_sharded_case_archive(
    archive_set_path: Path,
    *,
    expected_sha256: str,
    expected_manifest_sha256: str,
    archive_profile: str,
    require_complete_output: bool = False,
) -> dict[str, Any]:
    """Verify every ZIP shard and the union of its complete member inventory."""
    path = Path(archive_set_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"sharded archive-set manifest is missing: {path}")
    set_sha = compact._sha256_file(path)
    if set_sha != expected_sha256:
        raise ValueError(f"archive-set manifest SHA-256 mismatch: {path}")
    archive_set = json.loads(path.read_text(encoding="utf-8"))
    if (archive_set.get("archive_format") != ARCHIVE_SET_FORMAT
            or archive_set.get("archive_profile") != archive_profile
            or archive_set.get("status") != "FULL_VERIFIED"):
        raise ValueError(f"sharded archive set is not a verified full archive: {path}")
    manifest = archive_set.get("archive_manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError(f"sharded archive set has no member inventory: {path}")
    manifest_sha = _hash_bytes(_json_bytes(manifest))
    if manifest_sha != expected_manifest_sha256 or manifest_sha != archive_set.get("archive_manifest_sha256"):
        raise ValueError(f"sharded archive member-manifest SHA-256 mismatch: {path}")
    if require_complete_output and (archive_profile != "r22_v4_output_complete"
                                    or not manifest.get("required_v4_members")):
        raise ValueError(f"sharded archive has no V4 complete-output member set: {path}")
    plan_sha = _set_plan_sha256(archive_set)
    if plan_sha != archive_set.get("plan_sha256"):
        raise ValueError(f"sharded archive plan SHA-256 mismatch: {path}")
    shard_records = {int(item["index"]): item for item in archive_set.get("shards", [])}
    if set(shard_records) != {int(item["index"]) for item in archive_set["shard_plan"]}:
        raise ValueError(f"sharded archive does not contain every planned part: {path}")
    archived_members: list[str] = []
    shard_bytes = 0
    for shard_plan in archive_set["shard_plan"]:
        index = int(shard_plan["index"])
        record = shard_records[index]
        shard_path = _shard_path(path, index)
        actual = _verify_sharded_archive_part(
            shard_path, archive_set=archive_set, shard_plan=shard_plan,
        )
        if actual["sha256"] != record.get("sha256") or actual["bytes"] != record.get("bytes"):
            raise ValueError(f"sharded archive part identity mismatch: {shard_path}")
        archived_members.extend(actual["members"])
        shard_bytes += actual["bytes"]
    if len(archived_members) != len(set(archived_members)):
        raise ValueError(f"sharded archive contains duplicate member paths: {path}")
    expected_members = (
        {str(item["path"]) for item in manifest.get("included_files", [])}
        | {str(item["path"]) for item in manifest.get("symlink_entries", [])}
        | {"ARCHIVE_MANIFEST.json"}
    )
    if set(archived_members) != expected_members:
        raise ValueError(f"shard union differs from full archive member inventory: {path}")
    included_by_path = {str(item["path"]): item for item in manifest.get("included_files", [])}
    for record in manifest.get("symlink_entries", []):
        target = str(record["target_case_path"])
        if target not in included_by_path:
            raise ValueError(f"archive symlink target is absent from inventory: {record['path']}")
        target_record = included_by_path[target]
        if (int(record["bytes"]) != int(target_record["bytes"])
                or str(record["sha256"]) != str(target_record["sha256"])):
            raise ValueError(f"archive symlink target identity mismatch: {record['path']}")
        link_part = next(
            int(plan["index"]) for plan in archive_set["shard_plan"]
            if str(record["path"]) in {str(item["path"]) for item in plan["members"]}
        )
        with zipfile.ZipFile(_shard_path(path, link_part), "r") as archive:
            _verify_archive_link_record(archive, record)
    required = set(str(item) for item in manifest.get("required_v4_members", []))
    if require_complete_output and not required <= set(included_by_path):
        raise ValueError(f"sharded archive omits required V4 members: {path}")
    if _archive_set_identity_sha256(archive_set) != archive_set.get("archive_set_identity_sha256"):
        raise ValueError(f"sharded archive stable identity digest mismatch: {path}")
    return {
        "archive_path": str(path),
        "archive_sha256": set_sha,
        "archive_manifest_sha256": manifest_sha,
        "archive_profile": archive_profile,
        "archive_format": ARCHIVE_SET_FORMAT,
        "archive_bytes": shard_bytes,
        "source_file_bytes": archive_set["source_file_bytes"],
        "included_file_count": len(manifest.get("included_files", [])),
        "included_bytes": manifest.get("included_bytes", 0),
        "excluded_file_count": len(manifest.get("excluded_files", [])),
        "excluded_bytes": manifest.get("excluded_bytes", 0),
        "symlink_count": len(manifest.get("symlink_entries", [])),
        "shard_count": len(archive_set["shard_plan"]),
        "archive_set_identity_sha256": archive_set["archive_set_identity_sha256"],
        "crc_verified": True,
        "member_manifest_verified": True,
        "full_archive_present": True,
    }


def _verify_archive_link_record(archive: zipfile.ZipFile, record: Mapping[str, Any]) -> None:
    member = str(record["path"])
    info = archive.getinfo(member)
    if not stat.S_ISLNK(info.external_attr >> 16):
        raise ValueError(f"archive member is not encoded as a symlink: {member}")
    if archive.read(member).decode("utf-8") != str(record["archive_link_target"]):
        raise ValueError(f"archive symlink target text differs: {member}")
    target = posixpath.normpath(posixpath.join(
        posixpath.dirname(member), str(record["archive_link_target"]),
    ))
    if target != str(record["target_case_path"]):
        raise ValueError(f"archive symlink resolves to an unexpected member: {member}")


def _verify_compact_archive_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    from scripts.phase_r.r22_compact_archive import verify_case_archive

    path = Path(str(record["archive_path"])).expanduser().resolve()
    return verify_case_archive(
        path,
        expected_sha256=str(record["archive_sha256"]),
        expected_manifest_sha256=str(record["archive_manifest_sha256"]),
        archive_profile=COMPACT_PROFILE,
    )


def compact_sharded_case_archive(
    archive_set_path: Path,
    compact_archive_path: Path,
    *,
    cleanup_full_archive: bool = True,
) -> dict[str, Any]:
    """Create verified compact evidence from a verified shard set, then release shards."""
    set_path = Path(archive_set_path).expanduser().resolve()
    compact_path = Path(compact_archive_path).expanduser().resolve()
    if set_path == compact_path:
        raise ValueError("compact archive must be separate from its archive-set receipt")
    if not set_path.is_file():
        raise FileNotFoundError(f"archive-set receipt is missing: {set_path}")
    archive_set = json.loads(set_path.read_text(encoding="utf-8"))
    if archive_set.get("archive_format") != ARCHIVE_SET_FORMAT:
        raise ValueError("compact source is not a sharded V4 output archive")
    if archive_set.get("status") == "COMPACTED_CLEANED":
        compact_record = archive_set.get("compact_archive")
        if not isinstance(compact_record, Mapping):
            raise ValueError("cleaned archive receipt has no compact archive identity")
        verification = _verify_compact_archive_from_record(compact_record)
        return {
            **dict(compact_record),
            "status": "VERIFIED",
            "archive_set_path": str(set_path),
            "archive_set_sha256": compact._sha256_file(set_path),
            "full_archive_identity_sha256": archive_set["archive_set_identity_sha256"],
            "full_archive_manifest_sha256": archive_set["archive_manifest_sha256"],
            "full_archive_bytes": archive_set["archive_bytes"],
            "full_archive_cleanup": archive_set.get("full_archive_cleanup"),
            "verification": verification,
        }

    if archive_set.get("status") not in {"FULL_VERIFIED", "COMPACT_VERIFIED"}:
        raise ValueError("compact evidence requires a fully verified output archive set")
    cleanup_in_progress = (
        archive_set.get("status") == "COMPACT_VERIFIED"
        and archive_set.get("full_archive_cleanup", {}).get("status") == "IN_PROGRESS"
    )
    if cleanup_in_progress:
        compact_record = archive_set.get("compact_archive")
        if not isinstance(compact_record, Mapping):
            raise ValueError("cleanup checkpoint has no compact archive identity")
        _verify_compact_archive_from_record(compact_record)
    else:
        verify_sharded_case_archive(
            set_path,
            expected_sha256=compact._sha256_file(set_path),
            expected_manifest_sha256=str(archive_set["archive_manifest_sha256"]),
            archive_profile=str(archive_set["archive_profile"]),
            require_complete_output=bool(archive_set.get("require_complete_output")),
        )
    if archive_set.get("compact_archive"):
        compact_record = dict(archive_set["compact_archive"])
        _verify_compact_archive_from_record(compact_record)
    else:
        included = archive_set["archive_manifest"]["included_files"]
        included_by_path = {str(item["path"]): item for item in included}
        evaluator = [name for name in _COMPACT_EVALUATOR_MEMBERS if name in included_by_path]
        required = [str(item) for item in archive_set["archive_manifest"].get("required_v4_members", [])]
        small_metadata = [
            name for name in included_by_path
            if (name == "r22_run_manifest.json"
                or name.endswith("/case_manifest.json")
                or name.endswith("/scenario_resolved.json")
                or name.endswith("/stage2_simulation_report.md")
                or name.endswith("/stage2_simulation_report.json"))
        ]
        selected = sorted(set(required) | set(evaluator) | set(small_metadata))
        if not selected:
            raise ValueError("compact evidence selection is empty")
        missing = sorted(set(selected) - set(included_by_path))
        if missing:
            raise ValueError(f"compact evidence references absent full-archive members: {missing[:10]}")
        member_shard: dict[str, int] = {}
        for plan in archive_set["shard_plan"]:
            for member in plan["members"]:
                member_shard[str(member["path"])] = int(plan["index"])
        compact_manifest = {
            "schema_version": 1,
            "archive_profile": COMPACT_PROFILE,
            "source_archive_format": ARCHIVE_SET_FORMAT,
            "source_archive_profile": archive_set["archive_profile"],
            "source_archive_set_identity_sha256": archive_set["archive_set_identity_sha256"],
            "source_archive_manifest_sha256": archive_set["archive_manifest_sha256"],
            "required_v4_members": required,
            "included_files": [included_by_path[name] for name in selected],
            "excluded_files": [],
            "symlink_entries": [],
            "included_bytes": sum(int(included_by_path[name]["bytes"]) for name in selected),
            "excluded_bytes": 0,
            "symlink_target_bytes": 0,
        }
        compact_manifest_bytes = _json_bytes(compact_manifest)
        compact_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{compact_path.name}.", suffix=".tmp", dir=compact_path.parent,
        )
        os.close(fd)
        temporary = Path(temporary_name)
        shard_archives: dict[int, zipfile.ZipFile] = {}
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED,
                compresslevel=6, allowZip64=True,
            ) as target:
                for member in selected:
                    source_index = member_shard[member]
                    archive = shard_archives.get(source_index)
                    if archive is None:
                        archive = zipfile.ZipFile(_shard_path(set_path, source_index), "r")
                        shard_archives[source_index] = archive
                    with archive.open(member, "r") as src, target.open(member, "w") as dst:
                        shutil.copyfileobj(src, dst, length=_CHUNK_BYTES)
                target.writestr("ARCHIVE_MANIFEST.json", compact_manifest_bytes)
            for archive in shard_archives.values():
                archive.close()
            shard_archives.clear()
            _verify_compact_temporary(temporary, compact_manifest, compact_manifest_bytes)
            try:
                os.link(temporary, compact_path)
            except FileExistsError:
                _verify_compact_temporary(
                    compact_path, compact_manifest, compact_manifest_bytes,
                )
            temporary.unlink()
            compact_record = {
                "archive_path": str(compact_path),
                "archive_sha256": compact._sha256_file(compact_path),
                "archive_manifest_sha256": _hash_bytes(compact_manifest_bytes),
                "archive_profile": COMPACT_PROFILE,
                "archive_bytes": compact_path.stat().st_size,
                "included_file_count": len(selected),
                "included_bytes": compact_manifest["included_bytes"],
                "crc_verified": True,
                "member_manifest_verified": True,
            }
        except Exception:
            for archive in shard_archives.values():
                archive.close()
            temporary.unlink(missing_ok=True)
            raise

    _verify_compact_archive_from_record(compact_record)
    archive_set["compact_archive"] = compact_record
    archive_set["status"] = "COMPACT_VERIFIED"
    archive_set["compact_verified_at_utc"] = archive_set.get("compact_verified_at_utc", _utc_now())
    archive_set["full_archive_cleanup"] = archive_set.get("full_archive_cleanup") or {
        "status": "PENDING",
        "verified_compact_archive_sha256": compact_record["archive_sha256"],
        "shards": [
            {"index": item["index"], "path": item["path"],
             "sha256": item["sha256"], "bytes": item["bytes"], "status": "PENDING"}
            for item in archive_set["shards"]
        ],
        "started_at_utc": _utc_now(),
    }
    _write_json_atomic(set_path, archive_set)
    if cleanup_full_archive:
        ledger = archive_set["full_archive_cleanup"]
        ledger["status"] = "IN_PROGRESS"
        _write_json_atomic(set_path, archive_set)
        for shard in ledger["shards"]:
            shard_path = set_path.parent / str(shard["path"])
            if shard_path.exists():
                if compact._sha256_file(shard_path) != shard["sha256"]:
                    raise ValueError(f"full archive shard changed before authorized cleanup: {shard_path}")
                shard_path.unlink()
            shard["status"] = "REMOVED_AFTER_COMPACT_VERIFICATION"
            shard["removed_at_utc"] = _utc_now()
            _write_json_atomic(set_path, archive_set)
        shard_dir = _shard_directory(set_path)
        if shard_dir.exists():
            orphan_paths = _owned_shard_temporaries(set_path, archive_set["shard_plan"])
            ledger["orphan_temporary_files"] = sorted(set(
                ledger.get("orphan_temporary_files", [])
            ) | {item.name for item in orphan_paths})
            ledger["orphan_temporary_cleanup_status"] = "IN_PROGRESS"
            _write_json_atomic(set_path, archive_set)
            for orphan_path in orphan_paths:
                if orphan_path.is_symlink() or not orphan_path.is_file():
                    raise ValueError(f"unexpected owned shard temporary type: {orphan_path}")
                orphan_path.unlink()
            ledger["orphan_temporary_cleanup_status"] = "REMOVED"
            _write_json_atomic(set_path, archive_set)
            shard_dir.rmdir()
        ledger["status"] = "REMOVED_AFTER_COMPACT_VERIFICATION"
        ledger["finished_at_utc"] = _utc_now()
        archive_set["status"] = "COMPACTED_CLEANED"
        _write_json_atomic(set_path, archive_set)
    return {
        **compact_record,
        "status": "VERIFIED" if cleanup_full_archive else "COMPACT_VERIFIED",
        "archive_set_path": str(set_path),
        "archive_set_sha256": compact._sha256_file(set_path),
        "full_archive_identity_sha256": archive_set["archive_set_identity_sha256"],
        "full_archive_manifest_sha256": archive_set["archive_manifest_sha256"],
        "full_archive_bytes": archive_set["archive_bytes"],
        "full_archive_cleanup": archive_set.get("full_archive_cleanup"),
        "source_cleanup": archive_set.get("source_cleanup"),
    }


def _verify_compact_temporary(
    path: Path,
    manifest: Mapping[str, Any],
    manifest_bytes: bytes,
) -> None:
    included = manifest["included_files"]
    expected_names = {str(item["path"]) for item in included} | {"ARCHIVE_MANIFEST.json"}
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != expected_names:
            raise ValueError("compact archive member inventory mismatch")
        if archive.testzip() is not None:
            raise ValueError("compact archive failed ZIP CRC check")
        if archive.read("ARCHIVE_MANIFEST.json") != manifest_bytes:
            raise ValueError("compact archive manifest changed during write")
        for record in included:
            with archive.open(str(record["path"]), "r") as stream:
                digest, count = _hash_stream(stream)
            if count != int(record["bytes"]) or digest != record["sha256"]:
                raise ValueError(f"compact archive member failed SHA-256 check: {record['path']}")


def verify_sharded_archive_receipt(
    archive_set_path: Path,
    *,
    expected_sha256: str,
    expected_manifest_sha256: str,
    expected_compact_sha256: str,
) -> dict[str, Any]:
    """Verify the retained receipt after its full shards were compacted and deleted."""
    path = Path(archive_set_path).expanduser().resolve()
    if compact._sha256_file(path) != expected_sha256:
        raise ValueError(f"sharded archive receipt SHA-256 mismatch: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (value.get("status") != "COMPACTED_CLEANED"
            or value.get("archive_format") != ARCHIVE_SET_FORMAT
            or value.get("archive_manifest_sha256") != expected_manifest_sha256
            or not value.get("full_archive_verified_at_utc")):
        raise ValueError(f"sharded archive receipt lacks verified-and-cleaned state: {path}")
    if _hash_bytes(_json_bytes(value["archive_manifest"])) != expected_manifest_sha256:
        raise ValueError(f"retained full-output inventory digest mismatch: {path}")
    compact_record = value.get("compact_archive")
    if not isinstance(compact_record, Mapping) or compact_record.get("archive_sha256") != expected_compact_sha256:
        raise ValueError(f"retained compact archive identity mismatch: {path}")
    cleanup = value.get("full_archive_cleanup", {})
    if (cleanup.get("status") != "REMOVED_AFTER_COMPACT_VERIFICATION"
            or any(item.get("status") != "REMOVED_AFTER_COMPACT_VERIFICATION"
                   for item in cleanup.get("shards", []))):
        raise ValueError(f"full-output shard cleanup is incomplete: {path}")
    for item in cleanup.get("shards", []):
        shard_path = path.parent / str(item["path"])
        if shard_path.exists():
            raise ValueError(f"full-output shard remains after cleanup receipt: {shard_path}")
    compact_verification = _verify_compact_archive_from_record(compact_record)
    return {
        "archive_set_path": str(path),
        "archive_set_sha256": expected_sha256,
        "archive_manifest_sha256": expected_manifest_sha256,
        "archive_set_identity_sha256": value["archive_set_identity_sha256"],
        "full_archive_bytes": value["archive_bytes"],
        "full_archive_payload_present": False,
        "historically_verified_full_archive": True,
        "compact_archive_sha256": expected_compact_sha256,
        "compact_archive_verified": True,
        "compact_verification": compact_verification,
        "crc_verified_before_full_archive_cleanup": True,
        "member_manifest_verified_before_full_archive_cleanup": True,
    }


def _archive_result(
    path: Path,
    archive_set: Mapping[str, Any],
    verification: Mapping[str, Any],
    *,
    resumed_shard_count: int,
) -> dict[str, Any]:
    return {
        **verification,
        "status": "VERIFIED",
        "archive_sha256": compact._sha256_file(path),
        "raw_case_removed": archive_set.get("source_cleanup", {}).get("case_directory_removed") is True,
        "archive_set_path": str(path),
        "archive_set_sha256": compact._sha256_file(path),
        "resumed_shard_count": resumed_shard_count,
        "shards": archive_set.get("shards", []),
        "source_cleanup": archive_set.get("source_cleanup", {}),
    }
