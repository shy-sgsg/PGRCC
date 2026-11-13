#!/usr/bin/env python3
"""Hash, archive, verify, then remove one completed R22 case output tree."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any


_PER_CASE_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_RAW_ARRAY_SUFFIXES = {".bin", ".npy", ".npz"}
_CASE_MANIFEST_EXTRACT_KEYS = (
    "case_id", "status", "simulation", "area_clutter_gate", "causal_triplets",
    "evidence_gate", "input_contract", "inputs", "pair_identity", "protocol_layout",
    "delay_estimator", "r22_blind_method_qualification", "r22_delay_estimator",
    "r22_evaluation_summary", "r22_status_labels", "raw_cleanup", "residual_closure",
    "scene_identity", "selected_calibration_modes", "selected_methods",
    "source_xml_sha256", "truth_used_in_estimator",
)


def _exclusion_reason(relative_path: str) -> str | None:
    path = Path(relative_path)
    parts = path.parts
    if path.suffix.lower() in _PER_CASE_IMAGE_SUFFIXES:
        return "reproducible_per_case_plot; retain aggregate plots"
    if path.suffix.lower() == ".f32":
        return "regenerable_dense_map; retain geometry and tap summaries"
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


def archive_case_directory(
    case_root: Path,
    archive_path: Path,
    *,
    raw_root: Path,
    remove_source: bool = True,
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
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("archive path must be outside the case root")
    files = sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix())
    if any(item.is_symlink() for item in files):
        raise ValueError("case tree contains a symlink; refusing to archive an ambiguous path")
    regular_files = [item for item in files if item.is_file()]
    raw_files = [item for item in regular_files if item.suffix.lower() in _RAW_ARRAY_SUFFIXES]
    if raw_files:
        names = ", ".join(item.relative_to(source).as_posix() for item in raw_files[:5])
        raise ValueError(f"raw array remains after runner cleanup: {names}")

    generated_extract = _compact_case_manifest_extract(source, regular_files)
    if generated_extract is not None and (source / "case_manifest_review_extract.json").exists():
        raise ValueError("case tree already contains a generated manifest extract")
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for item in regular_files:
        relative = item.relative_to(source).as_posix()
        size = item.stat().st_size
        digest = _sha256_file(item)
        reason = _exclusion_reason(relative)
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

    manifest = {
        "schema_version": 1,
        "archive_policy": "all_case_files_except_reproducible_raster_plots",
        "included_files": included,
        "excluded_files": excluded,
        "included_bytes": sum(item["bytes"] for item in included),
        "excluded_bytes": sum(item["bytes"] for item in excluded),
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
            archive.writestr("ARCHIVE_MANIFEST.json", manifest_bytes)

        with zipfile.ZipFile(temporary, mode="r") as archive:
            if archive.testzip() is not None:
                raise OSError("case archive failed ZIP CRC verification")
            archived_names = set(archive.namelist())
            expected_names = {item["path"] for item in included} | {"ARCHIVE_MANIFEST.json"}
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
                expected_names = {item["path"] for item in included} | {"ARCHIVE_MANIFEST.json"}
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
        "archive_bytes": destination.stat().st_size,
        "included_file_count": len(included),
        "included_bytes": manifest["included_bytes"],
        "excluded_file_count": len(excluded),
        "excluded_bytes": manifest["excluded_bytes"],
        "archive_manifest_sha256": _sha256_bytes(manifest_bytes),
        "raw_case_removed": remove_source,
        "reused_existing_archive": reused_existing_archive,
    }
