#!/usr/bin/env python3
"""Create and verify an independent compact ZIP for the local R22-V4 P0 case."""

from __future__ import annotations

import hashlib
import csv
import io
import json
import os
import shutil
import stat
import tempfile
import time
import zlib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


COMPACT_MANIFEST = "P0_COMPACT_MANIFEST.json"
ARCHIVE_PROFILE = "r22_v4_p0_independent_compact"
_CHUNK_BYTES = 8 * 1024 * 1024
_RESERVE_BYTES = 64 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_member_path(name: str) -> None:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"unsafe archive member path: {name!r}")


def _sha_crc_stream(stream: Any) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    crc = 0
    count = 0
    for block in iter(lambda: stream.read(_CHUNK_BYTES), b""):
        digest.update(block)
        crc = zlib.crc32(block, crc)
        count += len(block)
    return digest.hexdigest(), crc & 0xFFFFFFFF, count


def _case_identity(archive: zipfile.ZipFile, members: Sequence[str]) -> tuple[str, dict[str, Any]]:
    manifests = [name for name in members if name.endswith("/case_manifest.json")]
    run_manifests = [name for name in members if name == "r22_run_manifest.json"]
    if len(manifests) != 1 or len(run_manifests) != 1:
        raise ValueError("P0 compact requires exactly one case manifest and one R22 run manifest")
    case_root = manifests[0].rsplit("/case_manifest.json", 1)[0]
    run = json.loads(archive.read(run_manifests[0]))
    design = run.get("design")
    if not isinstance(design, Mapping):
        raise ValueError("P0 run manifest lacks design identity")
    return case_root, {
        "case_id": run.get("case_id"),
        "phase": run.get("phase"),
        "seed": design.get("seed"),
        "speed_mps": design.get("radial_speed_mps"),
        "snr_db": design.get("target_snr_db"),
        "delay_ns": design.get("delay_error_ns"),
        "target_count": design.get("target_count"),
        "period_count": design.get("period_count"),
        "case_root_member_prefix": case_root,
    }


def _csv_unique_count(archive: zipfile.ZipFile, member: str, key: str) -> int:
    with archive.open(member, "r") as binary_stream:
        text = io.TextIOWrapper(binary_stream, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        if not reader.fieldnames or key not in reader.fieldnames:
            raise ValueError(f"CSV schema is missing {key!r}: {member}")
        return len({
            str(row.get(key, "")).strip() for row in reader
            if str(row.get(key, "")).strip()
        })


def _csv_row_count(archive: zipfile.ZipFile, member: str) -> int:
    with archive.open(member, "r") as binary_stream:
        text = io.TextIOWrapper(binary_stream, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        if not reader.fieldnames:
            raise ValueError(f"CSV schema is missing its header: {member}")
        return sum(1 for _ in reader)


def _validate_p0_inventory(members: Sequence[str], case_root: str) -> dict[str, Any]:
    names = set(members)
    production_prefix = f"{case_root}/production/"
    branches: set[str] = set()
    for name in names:
        if name.startswith(production_prefix) and name.endswith("/branch_manifest.json"):
            branch = name[len(production_prefix):].split("/", 1)[0]
            branches.add(branch)
    if len(branches) != 18:
        raise ValueError(f"P0 compact requires 18 Production branches; found {len(branches)}")
    required_track_files = (
        "track_association_accepts.csv", "track_detections.csv", "track_events.csv",
        "track_frames.csv", "track_output_payloads.csv", "track_points.csv",
        "track_results.csv", "track_states.csv",
    )
    for branch in sorted(branches):
        base = f"{production_prefix}{branch}/"
        fixed = (
            "branch_manifest.json", "production.xml", "gmt_pipe_core.log",
            "result/debug/cfar_geometry_diagnostics.csv",
        )
        missing = [base + name for name in fixed if base + name not in names]
        missing.extend(
            base + f"result/detection_results_GMTI{period:02d}.csv"
            for period in range(1, 11)
            if base + f"result/detection_results_GMTI{period:02d}.csv" not in names
        )
        track_prefix = base + "track_debug_runs/"
        for required in required_track_files:
            if not any(name.startswith(track_prefix) and name.endswith("/" + required) for name in names):
                missing.append(track_prefix + "*/" + required)
        if missing:
            raise ValueError(f"P0 Production branch is incomplete ({branch}): {missing[:5]}")
    required_evaluation = {
        "evaluation/doppler_opportunity_audit.csv",
        "evaluation/h0_false_alarm_metrics.csv",
        "evaluation/h0_task_metrics.csv",
        "evaluation/parameter_metrics.csv",
        "evaluation/target_associations.csv",
        "evaluation/target_level_metrics.csv",
        "evaluation/task_metrics.csv",
        "evaluation/track_level_metrics.csv",
    }
    missing_evaluation = sorted(required_evaluation - names)
    if missing_evaluation:
        raise ValueError(f"P0 compact is missing evaluator outputs: {missing_evaluation}")
    required_response = {
        f"{case_root}/r22_v4_output_evidence/target_complex_response.csv",
        f"{case_root}/r22_v4_output_evidence/target_complex_response_manifest.json",
        f"{case_root}/r22_v4_output_evidence/target_roi_manifest.csv",
        f"{case_root}/r22_v4_output_evidence/truth_mapping_audit.json",
    }
    missing_response = sorted(required_response - names)
    if missing_response:
        raise ValueError(f"P0 compact is missing target-response/truth evidence: {missing_response}")
    return {
        "production_branch_count": len(branches),
        "production_branches": sorted(branches),
        "detection_periods_per_branch": 10,
        "track_debug_required_files_per_branch": list(required_track_files),
        "evaluation_output_count": len(required_evaluation),
        "evaluation_outputs": sorted(required_evaluation),
        "target_response_present": any(name.endswith("/target_complex_response.csv") for name in names),
    }


def build_p0_compact_archive(
    source_archive: Path,
    destination: Path,
    *,
    expected_source_sha256: str,
    expected_source_manifest_sha256: str,
    additional_reanalysis_members: Sequence[str],
) -> dict[str, Any]:
    """Stream V4-required and evaluator members into a new ZIP without extraction."""
    source = Path(source_archive).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    if source == target:
        raise ValueError("compact output must be separate from the source archive")
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"source archive must be an existing regular file: {source}")
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing compact archive: {target}")
    if len(expected_source_sha256) != 64 or len(expected_source_manifest_sha256) != 64:
        raise ValueError("expected source SHA-256 identities must be 64 hex characters")

    source_stat = source.stat()
    started = time.monotonic()
    observed_source_sha = sha256_file(source)
    if observed_source_sha != expected_source_sha256:
        raise ValueError(
            f"source archive SHA-256 mismatch: expected {expected_source_sha256}, got {observed_source_sha}"
        )
    source_hash_seconds = time.monotonic() - started

    with zipfile.ZipFile(source, "r") as src:
        infos_list = src.infolist()
        names_list = [info.filename for info in infos_list]
        if len(names_list) != len(set(names_list)):
            raise ValueError("source archive contains duplicate member names")
        infos = {info.filename: info for info in infos_list}
        if "ARCHIVE_MANIFEST.json" not in infos:
            raise ValueError("source archive has no ARCHIVE_MANIFEST.json")
        source_manifest_bytes = src.read("ARCHIVE_MANIFEST.json")
        source_manifest_sha = _sha256_bytes(source_manifest_bytes)
        if source_manifest_sha != expected_source_manifest_sha256:
            raise ValueError("source embedded archive manifest SHA-256 mismatch")
        source_manifest = json.loads(source_manifest_bytes)
        if source_manifest.get("archive_profile") != "r22_v4_output_complete":
            raise ValueError("source archive is not the V4 output-complete profile")
        required = source_manifest.get("required_v4_members")
        if not isinstance(required, list) or not required:
            raise ValueError("source archive has no V4 required-member allowlist")
        required_names = [str(name) for name in required]
        additions = [str(name) for name in additional_reanalysis_members]
        if len(additions) != len(set(additions)):
            raise ValueError("additional reanalysis member list contains duplicates")
        for name in required_names + additions:
            _safe_member_path(name)
        missing = sorted((set(required_names) | set(additions)) - set(infos))
        if missing:
            raise ValueError(f"required source members are missing: {missing[:10]}")
        overlap = set(required_names) & set(additions)
        if overlap:
            raise ValueError(f"additional evaluator members overlap V4 allowlist: {sorted(overlap)}")
        selected = sorted(set(required_names) | set(additions))
        for name in selected:
            info = infos[name]
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"required source member is a symbolic link: {name}")
            if info.is_dir():
                raise ValueError(f"required source member is a directory: {name}")
        uncompressed_total = sum(infos[name].file_size for name in selected)
        target.parent.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(target.parent).free
        minimum_free = uncompressed_total + _RESERVE_BYTES
        if free_bytes < minimum_free:
            raise OSError(
                f"insufficient free space for atomic compact ZIP: free={free_bytes}, required={minimum_free}"
            )
        case_root, case_identity = _case_identity(src, selected)
        from scripts.phase_r.r22_v4_case_schema import validate_v4_case_schema

        association_member = "evaluation/target_associations.csv"
        response_root = f"{case_root}/r22_v4_output_evidence/"
        response_manifest = json.loads(src.read(response_root + "target_complex_response_manifest.json"))
        truth_audit = json.loads(src.read(response_root + "truth_mapping_audit.json"))
        roi_member = f"{case_root}/r22_v4_output_evidence/target_roi_manifest.csv"
        schema_result = validate_v4_case_schema(
            case_identity,
            selected,
            payload_metadata={
                "target_truth_association_count": _csv_unique_count(
                    src, association_member, "target_id",
                ),
                "target_response_target_count": _csv_unique_count(
                    src, roi_member, "target_id",
                ),
                "target_response_status": response_manifest.get("status"),
                "target_response_row_count": _csv_row_count(
                    src, response_root + "target_complex_response.csv",
                ),
                "target_roi_row_count": _csv_row_count(src, roi_member),
                "truth_mapping_status": truth_audit.get("status"),
            },
        )
        schema_inventory = _validate_p0_inventory(selected, case_root)

        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        member_records: list[dict[str, Any]] = []
        write_started = time.monotonic()
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED,
                compresslevel=6, allowZip64=True,
            ) as dst:
                for name in selected:
                    source_info = infos[name]
                    digest = hashlib.sha256()
                    crc = 0
                    count = 0
                    info = zipfile.ZipInfo(name, date_time=source_info.date_time)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    info.create_system = 3
                    with src.open(name, "r") as input_stream, dst.open(info, "w") as output_stream:
                        for block in iter(lambda: input_stream.read(_CHUNK_BYTES), b""):
                            output_stream.write(block)
                            digest.update(block)
                            crc = zlib.crc32(block, crc)
                            count += len(block)
                    computed_crc = crc & 0xFFFFFFFF
                    if count != source_info.file_size or computed_crc != source_info.CRC:
                        raise ValueError(f"source member CRC/size mismatch while streaming: {name}")
                    member_records.append({
                        "path": name,
                        "bytes": count,
                        "sha256": digest.hexdigest(),
                        "crc32": f"{computed_crc:08x}",
                        "source_crc32": f"{source_info.CRC:08x}",
                        "source_compressed_bytes": source_info.compress_size,
                        "source_compression": source_info.compress_type,
                        "required_v4_member": name in set(required_names),
                        "required_for_compact_reanalysis": name in set(additions),
                        "evidence_type": (
                            "v4_required_science_or_audit" if name in set(required_names)
                            else "evaluator_output_for_independent_reanalysis"
                        ),
                    })

            with zipfile.ZipFile(temporary, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as dst:
                compact_infos = {info.filename: info for info in dst.infolist()}
                for record in member_records:
                    out_info = compact_infos[record["path"]]
                    record["compact_compressed_bytes"] = out_info.compress_size
                    record["compact_compression"] = out_info.compress_type

                manifest = {
                    "schema_version": 1,
                    "archive_profile": ARCHIVE_PROFILE,
                    "source_archive_identity": {
                        "sha256": observed_source_sha,
                        "bytes": source_stat.st_size,
                        "embedded_archive_manifest_sha256": source_manifest_sha,
                        "archive_profile": source_manifest["archive_profile"],
                        "zip_central_directory_entry_count": len(infos_list),
                        "required_v4_member_count": len(required_names),
                        "required_v4_member_uncompressed_bytes": sum(infos[name].file_size for name in required_names),
                        "required_v4_member_source_compressed_bytes": sum(infos[name].compress_size for name in required_names),
                        "source_archive_path_recorded": False,
                    },
                    "case_identity": case_identity,
                    "case_root_member_prefix": case_root,
                    "case_schema": schema_result,
                    "schema_inventory": schema_inventory,
                    "archive_policy": (
                        "all V4 required members plus evaluator tables needed for offline P0 reanalysis; "
                        "no raw IQ, full CSI maps, source ZIP manifest, or symbolic links"
                    ),
                    "additional_reanalysis_members": additions,
                    "member_count": len(member_records),
                    "included_uncompressed_bytes": sum(row["bytes"] for row in member_records),
                    "included_source_compressed_bytes": sum(row["source_compressed_bytes"] for row in member_records),
                    "members": member_records,
                    "independence": {
                        "standalone_member_inventory": True,
                        "source_archive_path_recorded": False,
                        "references_source_archive_for_reanalysis": False,
                    },
                }
                manifest_bytes = (json.dumps(
                    manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
                ) + "\n").encode("utf-8")
                dst.writestr(COMPACT_MANIFEST, manifest_bytes)
            write_seconds = time.monotonic() - write_started

            verify_started = time.monotonic()
            verification = verify_p0_compact_archive(temporary)
            verification_seconds = time.monotonic() - verify_started
            try:
                os.link(temporary, target)
            except FileExistsError:
                raise FileExistsError(f"refusing to overwrite existing compact archive: {target}")
            temporary.unlink()
            final_sha = sha256_file(target)
            final_stat = source.stat()
            if (final_stat.st_ino, final_stat.st_size, final_stat.st_mtime_ns) != (
                source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns,
            ):
                raise RuntimeError("source archive changed during compact generation")
            return {
                "status": "PASS",
                "archive_path": str(target),
                "archive_sha256": final_sha,
                "archive_bytes": target.stat().st_size,
                "member_count": len(member_records),
                "required_v4_member_count": len(required_names),
                "additional_reanalysis_member_count": len(additions),
                "uncompressed_bytes": sum(row["bytes"] for row in member_records),
                "compressed_member_bytes": sum(row["compact_compressed_bytes"] for row in member_records),
                "source_hash_seconds": source_hash_seconds,
                "write_seconds": write_seconds,
                "verification_seconds": verification_seconds,
                "source_unchanged": True,
                "verification": verification,
            }
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def verify_p0_compact_archive(
    archive_path: Path, *, expected_archive_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify compact ZIP CRC, path safety, exact inventory and member hashes."""
    path = Path(archive_path).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"compact archive must be an existing regular file: {path}")
    archive_sha = sha256_file(path)
    if expected_archive_sha256 is not None and archive_sha != expected_archive_sha256:
        raise ValueError("compact archive SHA-256 mismatch")
    try:
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"compact ZIP is unreadable or truncated: {path}") from exc
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("compact ZIP contains duplicate member names")
        if COMPACT_MANIFEST not in names:
            raise ValueError("compact archive manifest is missing")
        for info in infos:
            _safe_member_path(info.filename)
            if info.filename != COMPACT_MANIFEST and stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"compact archive contains a symbolic link: {info.filename}")
        try:
            manifest_bytes = archive.read(COMPACT_MANIFEST)
            manifest = json.loads(manifest_bytes)
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("compact archive manifest is invalid") from exc
        if manifest.get("archive_profile") != ARCHIVE_PROFILE:
            raise ValueError("unexpected compact archive profile")
        source_identity = manifest.get("source_archive_identity")
        if not isinstance(source_identity, Mapping) or source_identity.get("source_archive_path_recorded") is not False:
            raise ValueError("compact archive source identity is incomplete or records an external path")
        if manifest.get("independence", {}).get("references_source_archive_for_reanalysis") is not False:
            raise ValueError("compact manifest still depends on the source archive")
        members = manifest.get("members")
        if not isinstance(members, list):
            raise ValueError("compact member manifest is invalid")
        member_names: list[str] = []
        for record in members:
            if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
                raise ValueError("compact member record is malformed")
            name = record["path"]
            _safe_member_path(name)
            member_names.append(name)
            if name == COMPACT_MANIFEST:
                raise ValueError("compact manifest must not list itself as a payload")
        if len(member_names) != len(set(member_names)):
            raise ValueError("compact member manifest contains duplicates")
        expected_names = set(member_names) | {COMPACT_MANIFEST}
        if set(names) != expected_names:
            missing = sorted(expected_names - set(names))
            extra = sorted(set(names) - expected_names)
            raise ValueError(f"compact member inventory differs from manifest; missing={missing[:5]}, extra={extra[:5]}")
        try:
            bad_member = archive.testzip()
        except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zlib.error) as exc:
            raise ValueError("compact ZIP CRC/decompression verification failed") from exc
        if bad_member is not None:
            raise ValueError(f"compact ZIP CRC verification failed: {bad_member}")
        verified = 0
        for record in members:
            name = str(record["path"])
            info = archive.getinfo(name)
            if info.file_size != int(record.get("bytes", -1)):
                raise ValueError(f"compact member size mismatch: {name}")
            if f"{info.CRC:08x}" != str(record.get("crc32", "")).lower():
                raise ValueError(f"compact member CRC manifest mismatch: {name}")
            try:
                with archive.open(name, "r") as stream:
                    digest, crc, count = _sha_crc_stream(stream)
            except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zlib.error) as exc:
                raise ValueError(f"compact member could not be decoded: {name}") from exc
            if count != info.file_size or digest != record.get("sha256") or crc != info.CRC:
                raise ValueError(f"compact member SHA/CRC verification failed: {name}")
            verified += 1

        case_root = manifest.get("case_root_member_prefix")
        case_schema = manifest.get("case_schema")
        if not isinstance(case_schema, Mapping) or case_schema.get("status") != "PASS":
            raise ValueError("compact case-type schema validation is missing or failed")
        response_root = f"{case_root}/r22_v4_output_evidence/"
        response_manifest = json.loads(archive.read(response_root + "target_complex_response_manifest.json"))
        truth_audit = json.loads(archive.read(response_root + "truth_mapping_audit.json"))
        from scripts.phase_r.r22_v4_case_schema import validate_v4_case_schema

        recomputed_case_schema = validate_v4_case_schema(
            manifest.get("case_identity", {}),
            member_names,
            payload_metadata={
                "target_truth_association_count": _csv_unique_count(
                    archive, "evaluation/target_associations.csv", "target_id",
                ),
                "target_response_target_count": _csv_unique_count(
                    archive, response_root + "target_roi_manifest.csv", "target_id",
                ),
                "target_response_status": response_manifest.get("status"),
                "target_response_row_count": _csv_row_count(
                    archive, response_root + "target_complex_response.csv",
                ),
                "target_roi_row_count": _csv_row_count(
                    archive, response_root + "target_roi_manifest.csv",
                ),
                "truth_mapping_status": truth_audit.get("status"),
            },
        )
        if recomputed_case_schema != case_schema:
            raise ValueError("compact case-type schema does not match its archived payload")
        schema = _validate_p0_inventory(member_names, str(case_root))
        if schema != manifest.get("schema_inventory"):
            raise ValueError("compact schema inventory does not match member payloads")
        return {
            "status": "PASS",
            "archive_path": str(path),
            "archive_sha256": archive_sha,
            "archive_bytes": path.stat().st_size,
            "archive_manifest_sha256": _sha256_bytes(manifest_bytes),
            "member_count": len(members),
            "member_sha256_verified": verified,
            "zip_crc_verified": True,
            "duplicate_member_count": 0,
            "unsafe_path_count": 0,
            "symlink_count": 0,
            "source_archive_path_recorded": False,
            "references_source_archive_for_reanalysis": False,
            "schema_inventory": schema,
            "uncompressed_bytes": sum(int(row["bytes"]) for row in members),
            "compressed_member_bytes": sum(int(row.get("compact_compressed_bytes", 0)) for row in members),
        }
