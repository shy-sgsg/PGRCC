#!/usr/bin/env python3
"""Execute a frozen R22 case matrix sequentially with resumable audit state."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r import run_r22_cuda_pilot as r22
from scripts.phase_r import r22_sharded_archive as sharded
from scripts.phase_r.r22_compact_archive import archive_case_directory, verify_case_archive


DEFAULT_DESIGN = ROOT / "docs/research/phase_r/R22/config/r22_extension_experiment_design.json"
DEFAULT_V4_SIZE_PILOT_DESIGN = (
    ROOT / "docs/research/phase_r/R22/config/r22_v4_size_pilot_draft_20261123.json"
)
DEFAULT_V4_THREE_SCHEMA_SIZE_PILOT_DESIGN = (
    ROOT / "docs/research/phase_r/R22/config/"
    "r22_v4_three_schema_size_pilot_draft_20261126.json"
)
V4_SIZE_PILOT_RESOURCE_EVIDENCE = (
    ROOT / "docs/research/phase_r/R22/evidence/"
    "v4_native_cuda_pilot_20261123/resource_usage.json"
)
V4_SIZE_PILOT_ARCHIVE_COMPARISON = (
    ROOT / "docs/research/phase_r/R22/evidence/v4_phase_h/archive_profile_comparison.json"
)
V4_SHARD_MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
V4_PILOT_FILESYSTEM_RESERVE_BYTES = 1024 * 1024 * 1024
V4_PILOT_RESOURCE_UNCERTAINTY_FACTOR = 1.20
V4_SIZE_PILOT_APPROVED_SHA256 = r22.V4_SIZE_PILOT_APPROVED_SHA256
V4_THREE_SCHEMA_SIZE_PILOT_APPROVED_SHA256 = r22.V4_THREE_SCHEMA_SIZE_PILOT_APPROVED_SHA256


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _design_digest(design: Mapping[str, Any]) -> str:
    payload = json.dumps(design, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_v4_size_pilot_authorization(
    design: Mapping[str, Any],
    *, phase: str, archive_profile: str,
    design_source_path: Path | None,
    authorize_v4_size_pilot: bool,
    approved_design_sha256: str | None,
    max_cases: int | None,
) -> dict[str, Any]:
    if not authorize_v4_size_pilot:
        raise ValueError("V4 output-complete execution requires explicit authorization flag")
    if archive_profile != "r22_v4_output_complete":
        raise ValueError("V4 size pilot requires archive_profile=r22_v4_output_complete")
    if phase != "engineering":
        raise ValueError("V4 size-pilot authorization is limited to phase=engineering")
    if max_cases not in (None, 1):
        raise ValueError("V4 size-pilot authorization allows exactly one case")
    if design_source_path is None or not Path(design_source_path).expanduser().is_file():
        raise ValueError("V4 size-pilot authorization requires the frozen design source file")

    source_path = Path(design_source_path).expanduser().resolve()
    source_sha256 = _file_sha256(source_path)
    supplied_sha256 = str(approved_design_sha256 or "").lower()
    if (len(supplied_sha256) != 64 or supplied_sha256 != source_sha256
            or source_sha256 != V4_SIZE_PILOT_APPROVED_SHA256):
        raise ValueError("approved design SHA-256 does not match the frozen V4 size-pilot design")

    frozen_design = json.loads(DEFAULT_V4_SIZE_PILOT_DESIGN.read_text(encoding="utf-8"))
    source_design = json.loads(source_path.read_text(encoding="utf-8"))
    if (_design_digest(design) != _design_digest(source_design)
            or _design_digest(design) != _design_digest(frozen_design)):
        raise ValueError("V4 size pilot does not match the frozen size-pilot design identity")
    if (design.get("design_id") != r22.V4_SIZE_PILOT_DESIGN_ID
            or design.get("state") != "DRAFT_NOT_FROZEN"
            or design.get("execution_authorized") is not False
            or design.get("cuda_authorized") is not False
            or design.get("ai_training") is not False):
        raise ValueError("V4 size-pilot draft authorization flags or identity changed")

    specs = build_case_specs(design, phase)
    if len(specs) != 1:
        raise ValueError("V4 size-pilot authorization requires exactly one planned case")
    spec = specs[0]
    expected_spec = {
        "phase": "engineering", "seed": 1937003, "speed_mps": 2.5,
        "snr_db": 20.0, "delay_ns": 4.0, "target_count": 20,
        "period_count": 10, "first_beam": 50, "beam_count": 1,
        "method_ids": ["C0", "PK"],
        "calibration_modes": ["Mode-A", "Mode-B"],
        "area": r22.DEFAULT_AREA,
        "scenario_template": "simulator/scenarios/beam50_one_targets_continuous.run.json",
        "method_config": "docs/research/phase_r/R22/config/r22_method_config_v2_range_roi.json",
    }
    for key, expected in expected_spec.items():
        if spec.get(key) != expected:
            raise ValueError(f"V4 size-pilot case differs from frozen identity: {key}")
    common = design.get("common", {})
    if common.get("production_backend") != "Native CUDA" or common.get("use_corex") is not False:
        raise ValueError("V4 size pilot requires Native CUDA with USE_COREX=false")

    def resolve_design_path(value: str) -> str:
        path = Path(value).expanduser()
        return str(path.resolve() if path.is_absolute() else (ROOT / path).resolve())

    case_id = r22.case_identifier(
        phase=phase, seed=spec["seed"], speed_mps=spec["speed_mps"],
        snr_db=spec["snr_db"], delay_ns=spec["delay_ns"],
        target_count=spec["target_count"], period_count=spec["period_count"],
    )
    return {
        "status": "AUTHORIZED_SINGLE_ENGINEERING_CASE",
        "design_id": str(design["design_id"]),
        "design_sha256": _design_digest(design),
        "approved_design_sha256": source_sha256,
        "design_source_path": str(source_path),
        "phase": phase,
        "case_id": case_id,
        "condition_id": spec["condition_id"],
        "seed": spec["seed"], "speed_mps": spec["speed_mps"],
        "snr_db": spec["snr_db"], "delay_ns": spec["delay_ns"],
        "target_count": spec["target_count"], "period_count": spec["period_count"],
        "first_beam": spec["first_beam"], "beam_count": spec["beam_count"],
        "method_ids": list(spec["method_ids"]),
        "calibration_modes": list(spec["calibration_modes"]),
        "area": dict(spec["area"]),
        "production_backend": "Native CUDA", "use_corex": False,
        "archive_profile": archive_profile,
        "scenario_template": resolve_design_path(spec["scenario_template"]),
        "method_config": resolve_design_path(spec["method_config"]),
        "scope_note": (
            "user-authorized single R22-V4-P0 output-size feasibility case; "
            "no confirmatory inference or performance claim"
        ),
    }


def _validate_v4_three_schema_size_pilot_authorization(
    design: Mapping[str, Any],
    *, phase: str, archive_profile: str,
    design_source_path: Path | None,
    authorize_v4_size_pilot: bool,
    approved_design_sha256: str | None,
    max_cases: int | None,
) -> dict[str, Any]:
    if not authorize_v4_size_pilot:
        raise ValueError("V4 output-complete execution requires explicit authorization flag")
    if archive_profile != "r22_v4_output_complete":
        raise ValueError("V4 size pilot requires archive_profile=r22_v4_output_complete")
    if phase not in {"engineering", "h0"}:
        raise ValueError("three-schema size pilot is limited to engineering and h0 phases")
    if design_source_path is None or not Path(design_source_path).expanduser().is_file():
        raise ValueError("three-schema size pilot requires the frozen design source file")

    source_path = Path(design_source_path).expanduser().resolve()
    source_sha256 = _file_sha256(source_path)
    supplied_sha256 = str(approved_design_sha256 or "").lower()
    if (len(supplied_sha256) != 64 or supplied_sha256 != source_sha256
            or source_sha256 != V4_THREE_SCHEMA_SIZE_PILOT_APPROVED_SHA256):
        raise ValueError("approved design SHA-256 does not match the frozen three-schema size-pilot design")

    frozen_design = json.loads(
        DEFAULT_V4_THREE_SCHEMA_SIZE_PILOT_DESIGN.read_text(encoding="utf-8")
    )
    source_design = json.loads(source_path.read_text(encoding="utf-8"))
    if (_design_digest(design) != _design_digest(source_design)
            or _design_digest(design) != _design_digest(frozen_design)):
        raise ValueError("three-schema size pilot does not match the frozen design identity")
    if (design.get("design_id") != r22.V4_THREE_SCHEMA_SIZE_PILOT_DESIGN_ID
            or design.get("state") != "DRAFT_NOT_FROZEN"
            or design.get("execution_authorized") is not False
            or design.get("cuda_authorized") is not False
            or design.get("ai_training") is not False):
        raise ValueError("three-schema size-pilot identity or execution flags changed")
    user_auth = design.get("user_pilot_authorization", {})
    if (user_auth.get("status") != "APPROVED_BY_USER"
            or user_auth.get("date") != "2026-11-26"
            or user_auth.get("formal_confirmatory_matrix_authorized") is not False
            or set(user_auth.get("scope", [])) != {
                "target_present_0ns", "h0_0ns", "h0_4ns",
            }):
        raise ValueError("three-schema pilot exceeds or lacks the user's approved scope")

    all_specs: dict[str, dict[str, Any]] = {}
    for pilot_phase in ("engineering", "h0"):
        for spec in build_case_specs(design, pilot_phase):
            key = r22.case_identifier(
                phase=pilot_phase, seed=spec["seed"], speed_mps=spec["speed_mps"],
                snr_db=spec["snr_db"], delay_ns=spec["delay_ns"],
                target_count=spec["target_count"], period_count=spec["period_count"],
            )
            all_specs[key] = spec
    if len(all_specs) != 3:
        raise ValueError("three-schema pilot must contain exactly three unique cases")
    schemas = {
        ("target_present" if spec["target_count"] > 0 else "h0", spec["delay_ns"])
        for spec in all_specs.values()
    }
    if schemas != {("target_present", 0.0), ("h0", 0.0), ("h0", 4.0)}:
        raise ValueError("three-schema pilot must cover target-present 0 ns and H0 0/+4 ns only")
    phase_specs = build_case_specs(design, phase)
    if max_cases not in (None, len(phase_specs)):
        raise ValueError("three-schema size-pilot authorization does not allow partial phase execution")

    common = design.get("common", {})
    expected_common = {
        "first_beam": 50,
        "beam_count": 1,
        "scenario_template": "simulator/scenarios/beam50_one_targets_continuous.run.json",
        "method_config": "docs/research/phase_r/R22/config/r22_method_config_v2_range_roi.json",
        "area": r22.DEFAULT_AREA,
        "method_ids": ["C0", "PK"],
        "calibration_modes": ["Mode-A", "Mode-B"],
        "production_backend": "Native CUDA",
        "use_corex": False,
        "archive_profile": "r22_v4_output_complete",
    }
    for key, expected in expected_common.items():
        if common.get(key) != expected:
            raise ValueError(f"three-schema size-pilot common configuration changed: {key}")

    def resolve_design_path(value: str) -> str:
        path = Path(value).expanduser()
        return str(path.resolve() if path.is_absolute() else (ROOT / path).resolve())

    case_authorizations: dict[str, dict[str, Any]] = {}
    for case_id, spec in all_specs.items():
        schema = "target_present_0ns" if spec["target_count"] else f"h0_{int(spec['delay_ns'])}ns"
        case_authorizations[case_id] = {
            "status": "AUTHORIZED_USER_SIZE_PILOT_CASE",
            "design_id": str(design["design_id"]),
            "approved_design_sha256": source_sha256,
            "phase": spec["phase"],
            "case_id": case_id,
            "schema": schema,
            "seed": spec["seed"],
            "speed_mps": spec["speed_mps"],
            "snr_db": spec["snr_db"],
            "delay_ns": spec["delay_ns"],
            "target_count": spec["target_count"],
            "period_count": spec["period_count"],
            "first_beam": spec["first_beam"],
            "beam_count": spec["beam_count"],
            "method_ids": list(spec["method_ids"]),
            "calibration_modes": list(spec["calibration_modes"]),
            "area": dict(spec["area"]),
            "production_backend": "Native CUDA",
            "use_corex": False,
            "archive_profile": archive_profile,
            "scenario_template": resolve_design_path(spec["scenario_template"]),
            "method_config": resolve_design_path(spec["method_config"]),
            "scope_note": "user-authorized three-schema resource pilot only; no confirmatory inference or performance claim",
        }
    return {
        "status": "AUTHORIZED_THREE_SCHEMA_SIZE_PILOT",
        "design_id": str(design["design_id"]),
        "design_sha256": _design_digest(design),
        "approved_design_sha256": source_sha256,
        "design_source_path": str(source_path),
        "phase": phase,
        "phase_case_ids": [
            r22.case_identifier(
                phase=phase, seed=spec["seed"], speed_mps=spec["speed_mps"],
                snr_db=spec["snr_db"], delay_ns=spec["delay_ns"],
                target_count=spec["target_count"], period_count=spec["period_count"],
            ) for spec in phase_specs
        ],
        "case_count_total": len(case_authorizations),
        "cases_by_case_id": case_authorizations,
        "production_backend": "Native CUDA",
        "use_corex": False,
        "archive_profile": archive_profile,
        "scenario_template": resolve_design_path(common["scenario_template"]),
        "method_config": resolve_design_path(common["method_config"]),
        "scope_note": "user-approved three missing-schema resource pilot only; formal confirmatory matrix remains unauthorized",
    }


def _v4_three_schema_size_pilot_resource_budget(
    design: Mapping[str, Any],
    *,
    resource_evidence_path: Path = V4_SIZE_PILOT_RESOURCE_EVIDENCE,
    archive_comparison_path: Path = V4_SIZE_PILOT_ARCHIVE_COMPARISON,
    max_shard_uncompressed_bytes: int = V4_SHARD_MAX_UNCOMPRESSED_BYTES,
    filesystem_reserve_bytes: int = V4_PILOT_FILESYSTEM_RESERVE_BYTES,
    uncertainty_factor: float = V4_PILOT_RESOURCE_UNCERTAINTY_FACTOR,
) -> dict[str, Any]:
    """Scale the measured P0 case by fixed scene and per-method/mode file groups."""
    resource_path = Path(resource_evidence_path).expanduser().resolve()
    comparison_path = Path(archive_comparison_path).expanduser().resolve()
    prior = json.loads(resource_path.read_text(encoding="utf-8"))
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    source_record = comparison.get("source_archive", {})
    source_path = Path(str(source_record.get("path", ""))).expanduser()
    if not source_path.is_absolute():
        source_path = (ROOT / source_path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"measured P0 complete archive is unavailable: {source_path}")
    if source_path.stat().st_size != source_record.get("size_bytes"):
        raise ValueError("measured P0 archive size differs from its resource evidence")
    expected_profile = source_record.get("archive_profile")
    expected_manifest_sha = source_record.get("embedded_archive_manifest_sha256")
    with zipfile.ZipFile(source_path, "r") as archive:
        try:
            manifest_bytes = archive.read("ARCHIVE_MANIFEST.json")
        except KeyError as exc:
            raise ValueError("measured P0 archive has no embedded member manifest") from exc
    actual_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    if expected_manifest_sha and actual_manifest_sha != expected_manifest_sha:
        raise ValueError("measured P0 embedded manifest differs from archived resource evidence")
    archive_manifest = json.loads(manifest_bytes)
    if archive_manifest.get("archive_profile") != "r22_v4_output_complete" or expected_profile != "r22_v4_output_complete":
        raise ValueError("resource baseline is not the R22 V4 output-complete profile")

    start_free = prior.get("disk_start_free_bytes")
    minimum_free = prior.get("disk_minimum_available_bytes")
    archive_bytes = prior.get("archive_bytes")
    reference_branches = prior.get("production_branch_count")
    if any(isinstance(value, bool) or not isinstance(value, int)
           for value in (start_free, minimum_free, archive_bytes, reference_branches)):
        raise ValueError("prior output-complete P0 resource evidence is incomplete")
    raw_peak = start_free - minimum_free - archive_bytes
    if raw_peak <= 0 or reference_branches <= 0 or reference_branches % 3:
        raise ValueError("prior P0 raw peak or Production branch count is invalid")

    included = archive_manifest.get("included_files")
    excluded = archive_manifest.get("excluded_files")
    if not isinstance(included, list) or not isinstance(excluded, list):
        raise ValueError("prior P0 archive member inventory is incomplete")
    group_bytes: dict[str, int] = {}
    payload_bytes = 0
    for group in (included, excluded):
        for item in group:
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                raise ValueError("prior P0 archive member inventory contains an invalid row")
            size = item.get("bytes")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ValueError("prior P0 archive member inventory contains an invalid size")
            parts = Path(item["path"]).parts
            category = (
                parts[3] if len(parts) > 3 and parts[:2] == ("pipeline", "cases")
                else parts[0]
            )
            group_bytes[category] = group_bytes.get(category, 0) + size
            payload_bytes += size
    if raw_peak < payload_bytes:
        raise ValueError("measured P0 raw peak is smaller than its uncompressed file inventory")
    overhead = raw_peak - payload_bytes

    common = design.get("common", {})
    method_ids = common.get("method_ids", [])
    modes = common.get("calibration_modes", [])
    if not method_ids or not modes:
        raise ValueError("pilot resource model requires non-empty method and calibration-mode sets")
    active_groups = len(set(method_ids)) * len(set(modes))
    reference_groups = reference_branches // 3
    if active_groups <= 0 or active_groups > reference_groups:
        raise ValueError("pilot method/mode scope exceeds the measured P0 branch coverage")
    branch_scale = active_groups / reference_groups
    branch_bytes = group_bytes.get("inputs", 0) + group_bytes.get("production", 0)
    shared_bytes = sum(
        value for name, value in group_bytes.items()
        if name not in {"inputs", "production"}
    )
    estimate = math.ceil(shared_bytes + branch_bytes * branch_scale + overhead)
    if (max_shard_uncompressed_bytes <= 0 or filesystem_reserve_bytes < 0
            or not math.isfinite(uncertainty_factor) or uncertainty_factor < 1.0):
        raise ValueError("pilot resource safety margin inputs are invalid")
    required = (
        math.ceil(estimate * uncertainty_factor)
        + max_shard_uncompressed_bytes
        + filesystem_reserve_bytes
    )
    return {
        "resource_evidence_path": str(resource_path),
        "archive_comparison_path": str(comparison_path),
        "source_archive_path": str(source_path),
        "source_archive_bytes": archive_bytes,
        "source_archive_manifest_sha256": actual_manifest_sha,
        "prior_p0_peak_consumption_bytes": start_free - minimum_free,
        "prior_p0_archive_bytes": archive_bytes,
        "prior_p0_raw_case_peak_bytes": raw_peak,
        "prior_p0_uncompressed_inventory_bytes": payload_bytes,
        "measured_inventory_overhead_bytes": overhead,
        "p0_bytes_by_storage_group": group_bytes,
        "active_method_mode_groups": active_groups,
        "reference_method_mode_groups": reference_groups,
        "branch_data_scale": branch_scale,
        "unscaled_shared_scene_bytes": group_bytes.get("scenes", 0),
        "scaled_branch_data_bytes": math.ceil(branch_bytes * branch_scale),
        "fixed_other_bytes": shared_bytes - group_bytes.get("scenes", 0),
        "estimated_raw_case_peak_bytes": estimate,
        "uncertainty_factor": uncertainty_factor,
        "max_shard_uncompressed_bytes": max_shard_uncompressed_bytes,
        "filesystem_reserve_bytes": filesystem_reserve_bytes,
        "required_free_bytes": required,
        "required_free_bytes_per_output_filesystem": required,
        "model_note": (
            "Measured P0 uncompressed inventory is split into shared scene/other files and "
            "inputs+Production files; the latter scale by active method×mode groups / P0 groups. "
            "The measured P0 residual overhead is retained, then a 20% uncertainty margin, "
            "one shard of temporary headroom, and a 1 GiB filesystem reserve are added."
        ),
    }


def _v4_three_schema_size_pilot_resource_gate(
    paths: Mapping[str, Path],
    design: Mapping[str, Any],
) -> dict[str, Any]:
    budget = _v4_three_schema_size_pilot_resource_budget(design)
    required_free = int(budget["required_free_bytes"])
    filesystems: dict[int, dict[str, Any]] = {}
    path_devices: dict[str, int] = {}
    for label, supplied_path in paths.items():
        path = Path(supplied_path).expanduser().resolve()
        write_probe_error = None
        try:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix=".pgrcc-size-pilot-write-probe-", dir=path,
            ) as probe_file:
                probe_file.write(b"pgrcc size-pilot storage preflight\n")
                probe_file.flush()
        except OSError as exc:
            write_probe_error = f"{type(exc).__name__}: {exc}"
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if not probe.exists():
            raise FileNotFoundError(f"cannot resolve storage path for pilot preflight: {path}")
        device_id = probe.stat().st_dev
        path_devices[label] = device_id
        record = filesystems.get(device_id)
        if record is None:
            usage = shutil.disk_usage(probe)
            free_bytes = int(usage.free)
            record = {
                "device_id": device_id,
                "probe_path": str(probe),
                "free_bytes": free_bytes,
                "required_free_bytes": required_free,
                "status": "READY" if free_bytes >= required_free else "PAUSED_RESOURCE_GATE",
                "paths": [],
            }
            filesystems[device_id] = record
        path_record = {
            "label": label,
            "path": str(path),
            "writable": write_probe_error is None,
        }
        if write_probe_error is not None:
            path_record["write_probe_error"] = write_probe_error
            record["status"] = "PAUSED_RESOURCE_GATE"
        record["paths"].append(path_record)

    reasons = [
        f"{record['probe_path']}: free {record['free_bytes']} < required {required_free} bytes"
        for record in filesystems.values() if record["status"] != "READY"
    ]
    raw_device = path_devices.get("raw_case_outputs")
    archive_device = path_devices.get("complete_archive")
    if raw_device is not None and archive_device is not None and raw_device != archive_device:
        reasons.append("raw case outputs and temporary complete archive must share a filesystem for per-shard cleanup")
        for record in filesystems.values():
            record["status"] = "PAUSED_RESOURCE_GATE"
    reasons.extend(
        f"{path['path']}: output write probe failed: {path['write_probe_error']}"
        for record in filesystems.values() for path in record["paths"]
        if not path["writable"]
    )
    blocked = any(record["status"] != "READY" for record in filesystems.values())
    return {
        "status": "PAUSED_RESOURCE_GATE" if blocked else "READY",
        "basis": budget,
        "filesystems": list(filesystems.values()),
        "reasons": reasons,
    }


class _DiskSpaceMonitor:
    """Sample free bytes during one case and append crash-survivable measurements."""

    def __init__(
        self,
        paths: Mapping[str, Path],
        *,
        sample_log: Path,
        sample_interval_seconds: float = 2.0,
    ) -> None:
        if sample_interval_seconds <= 0:
            raise ValueError("disk sample interval must be positive")
        self.sample_log = Path(sample_log).expanduser().resolve()
        self.sample_interval_seconds = float(sample_interval_seconds)
        self._devices: dict[int, dict[str, Any]] = {}
        for label, supplied in paths.items():
            path = Path(supplied).expanduser().resolve()
            probe = path
            while not probe.exists() and probe != probe.parent:
                probe = probe.parent
            if not probe.exists():
                raise FileNotFoundError(f"cannot resolve resource-monitor path: {path}")
            device_id = probe.stat().st_dev
            device = self._devices.setdefault(device_id, {
                "device_id": device_id,
                "probe_path": str(probe),
                "paths": [],
                "free_start_bytes": None,
                "minimum_free_bytes": None,
                "free_end_bytes": None,
            })
            device["paths"].append({"label": label, "path": str(path)})
        self._stream = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._sample_count = 0
        self._started_monotonic: float | None = None
        self._started_at_utc: str | None = None

    def start(self) -> None:
        if self._stream is not None:
            raise RuntimeError("disk monitor has already started")
        self.sample_log.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.sample_log.open("x", encoding="utf-8")
        self._started_monotonic = time.monotonic()
        self._started_at_utc = _utc_now()
        self.sample_once()
        self._thread = threading.Thread(target=self._run, name="r22-disk-monitor", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.wait(self.sample_interval_seconds):
            self.sample_once()

    def sample_once(self) -> None:
        timestamp = _utc_now()
        with self._lock:
            for device in self._devices.values():
                try:
                    free = int(shutil.disk_usage(device["probe_path"]).free)
                except OSError as exc:
                    device["last_sample_error"] = f"{type(exc).__name__}: {exc}"
                    continue
                if device["free_start_bytes"] is None:
                    device["free_start_bytes"] = free
                device["minimum_free_bytes"] = (
                    free if device["minimum_free_bytes"] is None
                    else min(int(device["minimum_free_bytes"]), free)
                )
                device["free_end_bytes"] = free
            self._sample_count += 1
            if self._stream is not None:
                self._stream.write(json.dumps({
                    "sample_index": self._sample_count,
                    "timestamp_utc": timestamp,
                    "filesystems": [
                        {key: device[key] for key in (
                            "device_id", "probe_path", "free_start_bytes",
                            "minimum_free_bytes", "free_end_bytes", "last_sample_error",
                        ) if key in device}
                        for device in self._devices.values()
                    ],
                }, sort_keys=True) + "\n")
                self._stream.flush()

    def stop(self) -> dict[str, Any]:
        if self._stream is None:
            raise RuntimeError("disk monitor was not started")
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
        self.sample_once()
        self._stream.close()
        self._stream = None
        elapsed = (
            time.monotonic() - self._started_monotonic
            if self._started_monotonic is not None else 0.0
        )
        filesystems = []
        for device in self._devices.values():
            start = device["free_start_bytes"]
            minimum = device["minimum_free_bytes"]
            row = dict(device)
            row["observed_peak_consumption_bytes"] = (
                max(0, int(start) - int(minimum))
                if start is not None and minimum is not None else None
            )
            filesystems.append(row)
        return {
            "status": "MEASURED",
            "started_at_utc": self._started_at_utc,
            "finished_at_utc": _utc_now(),
            "elapsed_seconds": elapsed,
            "sample_interval_seconds": self.sample_interval_seconds,
            "sample_count": self._sample_count,
            "sample_log": str(self.sample_log),
            "filesystems": filesystems,
            "scope_note": "Host filesystem free-space samples include concurrent external activity; they are observed peak usage, not exclusive process attribution.",
        }


def _recover_disk_space_samples(sample_log: str | Path) -> dict[str, Any] | None:
    path = Path(sample_log).expanduser().resolve()
    if not path.is_file():
        return None
    valid: list[dict[str, Any]] = []
    invalid_count = 0
    for raw_line in path.read_bytes().splitlines():
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            invalid_count += 1
            continue
        if (not isinstance(item, Mapping)
                or not isinstance(item.get("filesystems"), list)
                or not item["filesystems"]):
            invalid_count += 1
            continue
        valid.append(dict(item))
    if not valid:
        return None

    devices: dict[int, dict[str, Any]] = {}
    for sample in valid:
        for filesystem in sample["filesystems"]:
            if not isinstance(filesystem, Mapping) or "device_id" not in filesystem:
                continue
            device_id = int(filesystem["device_id"])
            row = devices.setdefault(device_id, {
                "device_id": device_id,
                "probe_path": filesystem.get("probe_path"),
                "free_start_bytes": int(filesystem["free_start_bytes"]),
                "minimum_free_bytes": int(filesystem["minimum_free_bytes"]),
                "free_end_bytes": int(filesystem["free_end_bytes"]),
            })
            row["minimum_free_bytes"] = min(
                int(row["minimum_free_bytes"]),
                int(filesystem["minimum_free_bytes"]),
                int(filesystem["free_end_bytes"]),
            )
            row["free_end_bytes"] = int(filesystem["free_end_bytes"])
            if filesystem.get("last_sample_error"):
                row["last_sample_error"] = filesystem["last_sample_error"]
    if not devices:
        return None
    filesystems = []
    for row in devices.values():
        row["observed_peak_consumption_bytes"] = max(
            0, int(row["free_start_bytes"]) - int(row["minimum_free_bytes"]),
        )
        filesystems.append(row)
    return {
        "status": "RECOVERED_PARTIAL",
        "started_at_utc": valid[0].get("timestamp_utc"),
        "last_sample_at_utc": valid[-1].get("timestamp_utc"),
        "sample_count": len(valid),
        "invalid_sample_line_count": invalid_count,
        "sample_log": str(path),
        "filesystems": filesystems,
        "scope_note": (
            "Recovered from a possibly truncated sample log after interruption; values reflect "
            "host filesystem free-space samples, including concurrent activity."
        ),
    }


def load_design(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("R22 design must be a JSON object")
    validate_design(value)
    return value


def validate_design(design: Mapping[str, Any]) -> None:
    if design.get("schema_version") != 1 or not design.get("design_id"):
        raise ValueError("R22 design requires schema_version=1 and design_id")
    seed_sets = design.get("seed_sets")
    if not isinstance(seed_sets, Mapping):
        raise ValueError("R22 design seed_sets must be an object")
    independent_names = ("engineering", "screening", "confirmatory", "h0")
    normalized: dict[str, set[int]] = {}
    for name in independent_names:
        values = seed_sets.get(name)
        if not isinstance(values, list) or not values:
            raise ValueError(f"independent seed set {name} must be a non-empty list")
        parsed: list[int] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"seed set {name} contains a non-integer seed")
            parsed.append(value)
        if len(parsed) != len(set(parsed)):
            raise ValueError(f"seed set {name} contains duplicate seeds")
        normalized[name] = set(parsed)
    for index, left in enumerate(independent_names):
        for right in independent_names[index + 1:]:
            overlap = normalized[left] & normalized[right]
            if overlap:
                raise ValueError(
                    f"independent seed sets overlap: {left} and {right}: {sorted(overlap)}"
                )

    methods = design.get("common", {}).get("method_ids", design.get("method_ids"))
    modes = design.get("common", {}).get("calibration_modes", design.get("calibration_modes"))
    if not isinstance(methods, list) or not methods or not all(isinstance(x, str) and x for x in methods):
        raise ValueError("R22 design requires non-empty method_ids")
    if not isinstance(modes, list) or not modes or not all(isinstance(x, str) and x for x in modes):
        raise ValueError("R22 design requires non-empty calibration_modes")

    phases = design.get("phases")
    if not isinstance(phases, Mapping) or not phases:
        raise ValueError("R22 design phases must be a non-empty object")
    target_counts = design.get("target_counts", {})
    common_period_count = design.get("period_count")
    for phase, phase_spec in phases.items():
        if not isinstance(phase_spec, Mapping):
            raise ValueError(f"phase {phase} must be an object")
        seed_set = phase_spec.get("seed_set")
        if seed_set not in seed_sets:
            raise ValueError(f"phase {phase} references unknown seed set {seed_set}")
        target_count = phase_spec.get("target_count", target_counts.get(phase))
        if isinstance(target_count, bool) or not isinstance(target_count, int):
            raise ValueError(f"phase {phase} requires integer target_count")
        if (phase == "h0" and target_count != 0) or (phase != "h0" and target_count <= 0):
            raise ValueError(f"phase {phase} has invalid target_count={target_count}")
        period_count = phase_spec.get("period_count", common_period_count)
        if isinstance(period_count, bool) or not isinstance(period_count, int) or period_count <= 0:
            raise ValueError(f"phase {phase} requires positive period_count")
        if "conditions" not in phase_spec and "factorial" not in phase_spec:
            raise ValueError(f"phase {phase} requires conditions or factorial factors")


def _conditions(phase_spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    direct = phase_spec.get("conditions")
    if direct is not None:
        if not isinstance(direct, list) or not direct:
            raise ValueError("conditions must be a non-empty list")
        result = [dict(item) for item in direct]
    else:
        factorial = phase_spec.get("factorial")
        if not isinstance(factorial, Mapping) or not factorial:
            raise ValueError("factorial must be a non-empty object")
        keys = list(factorial)
        values = [factorial[key] for key in keys]
        if any(not isinstance(items, list) or not items for items in values):
            raise ValueError("every factorial factor must be a non-empty list")
        result = []
        for combination in itertools.product(*values):
            item = dict(zip(keys, combination))
            item["condition_id"] = (
                f"v{item['radial_speed_mps']:g}_snr{item['target_snr_db']:g}_"
                f"d{item['delay_error_ns']:g}"
            )
            result.append(item)
    required = ("radial_speed_mps", "target_snr_db", "delay_error_ns")
    ids: set[str] = set()
    for item in result:
        if any(key not in item for key in required):
            raise ValueError(f"condition missing one of {required}")
        condition_id = str(item.get("condition_id", ""))
        if not condition_id or condition_id in ids:
            raise ValueError("each condition requires a unique non-empty condition_id")
        ids.add(condition_id)
        for key in required:
            if isinstance(item[key], bool) or not isinstance(item[key], (int, float)):
                raise ValueError(f"condition {condition_id} has non-numeric {key}")
    return result


def build_case_specs(design: Mapping[str, Any], phase: str) -> list[dict[str, Any]]:
    validate_design(design)
    phases = design["phases"]
    if phase not in phases:
        raise ValueError(f"phase is not in frozen design: {phase}")
    phase_spec = phases[phase]
    common = design.get("common", {})
    target_count = phase_spec.get("target_count", design.get("target_counts", {}).get(phase))
    period_count = phase_spec.get("period_count", design.get("period_count"))
    cases: list[dict[str, Any]] = []
    for seed in design["seed_sets"][phase_spec["seed_set"]]:
        for condition in _conditions(phase_spec):
            cases.append({
                "phase": phase,
                "seed": seed,
                "condition_id": condition["condition_id"],
                "speed_mps": float(condition["radial_speed_mps"]),
                "snr_db": float(condition["target_snr_db"]),
                "delay_ns": float(condition["delay_error_ns"]),
                "target_count": target_count,
                "period_count": period_count,
                "first_beam": common.get("first_beam", 50),
                "beam_count": common.get("beam_count", 1),
                "area": dict(common.get("area", r22.DEFAULT_AREA)),
                "method_ids": list(common.get("method_ids", design.get("method_ids", []))),
                "calibration_modes": list(common.get("calibration_modes", design.get("calibration_modes", []))),
                "scenario_template": common.get("scenario_template"),
                "method_config": common.get("method_config"),
            })
    identifiers = [
        r22.case_identifier(
            phase=item["phase"], seed=item["seed"], speed_mps=item["speed_mps"],
            snr_db=item["snr_db"], delay_ns=item["delay_ns"],
            target_count=item["target_count"], period_count=item["period_count"],
        ) for item in cases
    ]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"phase {phase} produces duplicate case identifiers")
    return cases


def _case_state(phase: str, result: Mapping[str, Any]) -> str:
    labels = result.get("status_labels", {})
    if phase == "h0":
        summary = result.get("evaluation_summary", {})
        if labels.get("EVALUABLE") and int(summary.get("independent_h0_seed_rows", 0)) > 0:
            return "PASSED" if result.get("implementation_result") == "passed" else "H0_EVALUABLE_WITH_GAPS"
        return "GATE_FAILED"
    if (result.get("implementation_result") == "passed"
            and labels.get("EVALUABLE")
            and result.get("all_production_branches_passed") is True):
        return "PASSED"
    return "GATE_FAILED"


def _relative_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def _case_output_path(raw_base: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"case output path is a symlink: {candidate}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(raw_base)
    except ValueError as exc:
        raise ValueError(f"case output is outside raw output root: {resolved}") from exc
    if resolved == raw_base:
        raise ValueError("refusing to archive or remove the raw output root")
    return resolved


def _raw_tree_inventory(case_root: Path) -> dict[str, int]:
    file_count = 0
    total_bytes = 0
    for path in case_root.rglob("*"):
        if path.is_file():
            file_count += 1
            total_bytes += path.stat().st_size
    return {"file_count": file_count, "bytes": total_bytes}


def _three_schema_archive_paths(
    record: Mapping[str, Any], *, phase: str, archive_base: Path,
) -> tuple[Path, Path]:
    stem = f"{record['case_id']}__attempt_{int(record['attempt']):03d}"
    archive_dir = archive_base / phase
    return (
        archive_dir / f"{stem}.archive-set.json",
        archive_dir / f"{stem}.compact.zip",
    )


def _verify_three_schema_size_pilot_archives(
    record: Mapping[str, Any], *, phase: str, archive_base: Path,
) -> dict[str, Any]:
    full = record.get("full_output_archive")
    compact = record.get("compact_archive")
    if not isinstance(full, Mapping) or not isinstance(compact, Mapping):
        raise ValueError(f"three-schema archive receipt is incomplete for {record['case_id']}")
    archive_set_path, compact_path = _three_schema_archive_paths(
        record, phase=phase, archive_base=archive_base,
    )
    if Path(str(full.get("archive_set_path", ""))).expanduser().resolve() != archive_set_path.resolve():
        raise ValueError(f"full-output archive-set path changed for {record['case_id']}")
    if Path(str(compact.get("archive_path", ""))).expanduser().resolve() != compact_path.resolve():
        raise ValueError(f"compact archive path changed for {record['case_id']}")
    return sharded.verify_sharded_archive_receipt(
        archive_set_path,
        expected_sha256=str(full.get("archive_set_sha256", "")),
        expected_manifest_sha256=str(full.get("archive_manifest_sha256", "")),
        expected_compact_sha256=str(compact.get("archive_sha256", "")),
    )


def _ensure_three_schema_size_pilot_archives(
    record: dict[str, Any], *, phase: str, raw_base: Path,
    archive_base: Path, manifest: dict[str, Any], manifest_path: Path,
    require_complete_output: bool,
) -> None:
    if not require_complete_output:
        raise ValueError("three-schema size pilot requires complete V4 output verification")
    case_root = _case_output_path(raw_base, str(record["output_root"]))
    archive_set_path, compact_path = _three_schema_archive_paths(
        record, phase=phase, archive_base=archive_base,
    )
    archive_set_path.parent.mkdir(parents=True, exist_ok=True)

    if archive_set_path.is_file():
        archive_set_state = json.loads(archive_set_path.read_text(encoding="utf-8"))
        archive_status = archive_set_state.get("status")
    else:
        archive_status = None

    if archive_status not in {"COMPACT_VERIFIED", "COMPACTED_CLEANED"}:
        record["full_output_archive_status"] = "PENDING"
        _write_json_atomic(manifest_path, manifest)
        full_archive_result = sharded.archive_case_directory_sharded(
            case_root,
            archive_set_path,
            raw_root=raw_base,
            archive_profile="r22_v4_output_complete",
            require_complete_output=True,
            max_shard_uncompressed_bytes=V4_SHARD_MAX_UNCOMPRESSED_BYTES,
            remove_source=True,
        )
        verified_full_set = json.loads(archive_set_path.read_text(encoding="utf-8"))
        record["full_output_archive_status"] = "VERIFIED"
        record["full_output_archive"] = {
            "archive_set_path": str(archive_set_path),
            "archive_set_sha256_before_compaction": full_archive_result["archive_set_sha256"],
            "archive_manifest_sha256": full_archive_result["archive_manifest_sha256"],
            "archive_set_identity_sha256": full_archive_result["archive_set_identity_sha256"],
            "archive_bytes": full_archive_result["archive_bytes"],
            "shard_count": full_archive_result["shard_count"],
            "shards": full_archive_result["shards"],
            "source_file_count": verified_full_set["source_file_count"],
            "source_file_bytes": full_archive_result["source_file_bytes"],
            "included_file_count": full_archive_result["included_file_count"],
            "included_bytes": full_archive_result["included_bytes"],
            "excluded_file_count": full_archive_result["excluded_file_count"],
            "excluded_bytes": full_archive_result["excluded_bytes"],
            "crc_verified": full_archive_result["crc_verified"],
            "member_manifest_verified": full_archive_result["member_manifest_verified"],
            "raw_case_removed": full_archive_result["raw_case_removed"],
            "full_archive_payload_present": True,
        }
        record["raw_cleanup_ledger"] = {
            "status": "REMOVED_AFTER_FULL_ARCHIVE_VERIFICATION",
            "case_path": str(case_root),
            "file_count": verified_full_set["source_file_count"],
            "bytes": full_archive_result["source_file_bytes"],
            "included_bytes": full_archive_result["included_bytes"],
            "excluded_bytes": full_archive_result["excluded_bytes"],
            "archive_set_path": str(archive_set_path),
            "archive_manifest_sha256": full_archive_result["archive_manifest_sha256"],
            "archive_crc_verified": full_archive_result["crc_verified"],
            "removed_at_utc": _utc_now(),
            "raw_case_present_after": case_root.exists(),
        }
        _write_json_atomic(manifest_path, manifest)
    else:
        full_archive_state = json.loads(archive_set_path.read_text(encoding="utf-8"))
        if full_archive_state.get("archive_profile") != "r22_v4_output_complete":
            raise ValueError(f"sharded archive profile changed for {record['case_id']}")

    record["compact_archive_status"] = "PENDING"
    _write_json_atomic(manifest_path, manifest)
    compact_result = sharded.compact_sharded_case_archive(
        archive_set_path, compact_path, cleanup_full_archive=True,
    )
    receipt = sharded.verify_sharded_archive_receipt(
        archive_set_path,
        expected_sha256=compact_result["archive_set_sha256"],
        expected_manifest_sha256=compact_result["full_archive_manifest_sha256"],
        expected_compact_sha256=compact_result["archive_sha256"],
    )
    final_set = json.loads(archive_set_path.read_text(encoding="utf-8"))
    record["full_output_archive"] = {
        "archive_set_path": str(archive_set_path),
        "archive_set_sha256": compact_result["archive_set_sha256"],
        "archive_manifest_sha256": compact_result["full_archive_manifest_sha256"],
        "archive_set_identity_sha256": compact_result["full_archive_identity_sha256"],
        "archive_bytes": compact_result["full_archive_bytes"],
        "shard_count": len(final_set.get("shards", [])),
        "shards": final_set.get("shards", []),
        "crc_verified": receipt["crc_verified_before_full_archive_cleanup"],
        "member_manifest_verified": receipt["member_manifest_verified_before_full_archive_cleanup"],
        "historically_verified_full_archive": receipt["historically_verified_full_archive"],
        "full_archive_payload_present": receipt["full_archive_payload_present"],
        "full_archive_cleanup": final_set.get("full_archive_cleanup"),
    }
    record["full_output_archive_status"] = "VERIFIED_COMPACTED_AND_CLEANED"
    record["compact_archive"] = {
        "archive_path": compact_result["archive_path"],
        "archive_sha256": compact_result["archive_sha256"],
        "archive_manifest_sha256": compact_result["archive_manifest_sha256"],
        "archive_profile": compact_result["archive_profile"],
        "archive_bytes": compact_result["archive_bytes"],
        "included_file_count": compact_result["included_file_count"],
        "included_bytes": compact_result["included_bytes"],
        "crc_verified": compact_result["crc_verified"],
        "member_manifest_verified": compact_result["member_manifest_verified"],
    }
    record["compact_archive_status"] = "VERIFIED_AND_RAW_REMOVED"
    ledger = dict(record.get("raw_cleanup_ledger", {}))
    ledger.update({
        "status": "REMOVED_AFTER_FULL_ARCHIVE_VERIFICATION",
        "archive_set_path": str(archive_set_path),
        "archive_set_sha256": compact_result["archive_set_sha256"],
        "archive_manifest_sha256": compact_result["full_archive_manifest_sha256"],
        "compact_archive_sha256": compact_result["archive_sha256"],
        "full_archive_payload_present": False,
        "raw_case_present_after": case_root.exists(),
        "full_archive_cleanup_status": final_set.get("full_archive_cleanup", {}).get("status"),
    })
    record["raw_cleanup_ledger"] = ledger
    record["raw_output_removed_after_archive_verification"] = True
    record.pop("compact_archive_error", None)
    record.pop("full_output_archive_error", None)
    _write_json_atomic(manifest_path, manifest)


def _ensure_compact_archive(
    record: dict[str, Any],
    *,
    phase: str,
    raw_base: Path,
    archive_base: Path,
    manifest: dict[str, Any],
    manifest_path: Path,
    failure_archive_root: Path | None = None,
    archive_profile: str = "legacy",
    require_complete_output: bool = False,
    prior_archive_verified: bool = False,
) -> None:
    if record.get("archive_strategy") == "v4_sharded_full_then_compact":
        selected_archive_base = (
            failure_archive_root
            if failure_archive_root is not None
            and record.get("status") in {"GATE_FAILED", "UNKNOWN_STOP"}
            else archive_base
        )
        _ensure_three_schema_size_pilot_archives(
            record,
            phase=phase,
            raw_base=raw_base,
            archive_base=selected_archive_base,
            manifest=manifest,
            manifest_path=manifest_path,
            require_complete_output=require_complete_output,
        )
        return
    case_root = _case_output_path(raw_base, str(record["output_root"]))
    selected_archive_base = (
        failure_archive_root
        if failure_archive_root is not None
        and record.get("status") in {"GATE_FAILED", "UNKNOWN_STOP"}
        else archive_base
    )
    archive_path = selected_archive_base / phase / (
        f"{record['case_id']}__attempt_{int(record['attempt']):03d}.zip"
    )
    prior = record.get("compact_archive")
    if prior is not None:
        if not isinstance(prior, Mapping):
            raise ValueError(f"invalid compact archive record for {record['case_id']}")
        recorded_path = Path(str(prior.get("archive_path", ""))).expanduser().resolve()
        if recorded_path != archive_path.resolve():
            raise ValueError(f"compact archive path changed for {record['case_id']}")
        if prior.get("archive_profile", "legacy") != archive_profile:
            raise ValueError(f"compact archive profile changed for {record['case_id']}")
        if not prior_archive_verified:
            verify_case_archive(
                recorded_path,
                expected_sha256=str(prior.get("archive_sha256", "")),
                expected_manifest_sha256=str(prior.get("archive_manifest_sha256", "")),
                archive_profile=archive_profile,
                require_complete_output=require_complete_output,
            )
        compact_archive = dict(prior)
    else:
        if not case_root.is_dir():
            raise FileNotFoundError(
                f"case output and compact archive record are both missing: {case_root}"
            )
        record["compact_archive_status"] = "PENDING"
        _write_json_atomic(manifest_path, manifest)
        compact_archive = archive_case_directory(
            case_root, archive_path, raw_root=raw_base, remove_source=False,
            archive_profile=archive_profile,
            require_complete_output=require_complete_output,
        )
        record["compact_archive"] = compact_archive
        record["compact_archive_status"] = "ARCHIVE_VERIFIED"
        _write_json_atomic(manifest_path, manifest)

    cleanup_ledger: dict[str, Any] | None = None
    if archive_profile == "r22_v4_output_complete":
        prior_cleanup = record.get("raw_cleanup_ledger")
        if isinstance(prior_cleanup, Mapping):
            cleanup_ledger = dict(prior_cleanup)
            if cleanup_ledger.get("archive_sha256") != compact_archive.get("archive_sha256"):
                raise ValueError(f"V4 cleanup ledger archive identity changed for {record['case_id']}")
            if (cleanup_ledger.get("status") in {
                    "REMOVED_AFTER_ARCHIVE_VERIFICATION",
                    "RAW_ALREADY_ABSENT_AFTER_VERIFIED_ARCHIVE",
                }
                    and case_root.exists()):
                raise ValueError(
                    f"V4 raw case reappeared after recorded cleanup; refusing deletion: {case_root}"
                )
        else:
            inventory = _raw_tree_inventory(case_root) if case_root.is_dir() else {
                "file_count": 0, "bytes": 0,
            }
            cleanup_ledger = {
                "status": "PENDING",
                "case_path": str(case_root),
                **inventory,
                "archive_path": compact_archive.get("archive_path"),
                "archive_sha256": compact_archive.get("archive_sha256"),
                "archive_manifest_sha256": compact_archive.get("archive_manifest_sha256"),
                "archive_crc_verified": True,
                "reason": "raw case is removable only after complete V4 archive verification",
                "started_at_utc": _utc_now(),
            }
        record["raw_cleanup_ledger"] = cleanup_ledger
        _write_json_atomic(manifest_path, manifest)

    raw_case_present = case_root.exists()
    if raw_case_present:
        if not case_root.is_dir() or case_root.is_symlink():
            raise ValueError(f"case output is not a regular directory: {case_root}")
        shutil.rmtree(case_root)
    compact_archive["raw_case_removed"] = True
    record["compact_archive"] = compact_archive
    record["compact_archive_status"] = "VERIFIED_AND_RAW_REMOVED"
    record["raw_output_removed_after_archive_verification"] = True
    record.pop("compact_archive_error", None)
    if cleanup_ledger is not None:
        cleanup_ledger["status"] = (
            "REMOVED_AFTER_ARCHIVE_VERIFICATION" if raw_case_present
            else "RAW_ALREADY_ABSENT_AFTER_VERIFIED_ARCHIVE"
        )
        cleanup_ledger["removed_at_utc"] = _utc_now()
        cleanup_ledger["raw_case_present_after"] = case_root.exists()
        record["raw_cleanup_ledger"] = cleanup_ledger
    _write_json_atomic(manifest_path, manifest)


def run_matrix(
    design: Mapping[str, Any],
    *,
    phase: str,
    output_root: Path,
    stage2_binary: Path | None = None,
    production_binary: Path | None = None,
    template: Path | None = None,
    method_config: Path | None = None,
    raw_output_root: Path | None = None,
    compact_archive_root: Path | None = None,
    max_cases: int | None = None,
    resume: bool = False,
    retry_interrupted: bool = False,
    retry_failed_case: bool = False,
    run_case_fn: Callable[..., Mapping[str, Any]] | None = None,
    case_evaluator_fn: Callable[[Path, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    failure_archive_root: Path | None = None,
    manifest_metadata: Mapping[str, Any] | None = None,
    archive_profile: str = "legacy",
    design_source_path: Path | None = None,
    authorize_v4_size_pilot: bool = False,
    approved_design_sha256: str | None = None,
) -> dict[str, Any]:
    validate_design(design)
    if archive_profile not in {"legacy", "r22_v4_output_complete"}:
        raise ValueError(f"unknown R22 archive profile: {archive_profile}")
    size_pilot_design_id = design.get("design_id")
    archive_strategy = (
        "v4_sharded_full_then_compact"
        if size_pilot_design_id == r22.V4_THREE_SCHEMA_SIZE_PILOT_DESIGN_ID
        else None
    )
    is_size_pilot_design = size_pilot_design_id in {
        r22.V4_SIZE_PILOT_DESIGN_ID,
        r22.V4_THREE_SCHEMA_SIZE_PILOT_DESIGN_ID,
    }
    v4_size_pilot_authorization = None
    if archive_profile == "r22_v4_output_complete" or authorize_v4_size_pilot:
        validator = (
            _validate_v4_three_schema_size_pilot_authorization
            if size_pilot_design_id == r22.V4_THREE_SCHEMA_SIZE_PILOT_DESIGN_ID
            else _validate_v4_size_pilot_authorization
        )
        v4_size_pilot_authorization = validator(
            design,
            phase=phase,
            archive_profile=archive_profile,
            design_source_path=design_source_path,
            authorize_v4_size_pilot=authorize_v4_size_pilot,
            approved_design_sha256=approved_design_sha256,
            max_cases=max_cases,
        )
    elif is_size_pilot_design:
        raise ValueError("V4 size-pilot design requires archive_profile=r22_v4_output_complete")
    if retry_interrupted and not resume:
        raise ValueError("retry_interrupted requires resume=True")
    if retry_failed_case and not resume:
        raise ValueError("retry_failed_case requires resume=True")
    if (retry_failed_case
            and size_pilot_design_id != r22.V4_THREE_SCHEMA_SIZE_PILOT_DESIGN_ID):
        raise ValueError("retry_failed_case is limited to the approved three-schema size pilot")
    specs = build_case_specs(design, phase)
    if max_cases is not None:
        if isinstance(max_cases, bool) or max_cases <= 0:
            raise ValueError("max_cases must be positive")
        specs = specs[:max_cases]
    output_root = Path(output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    raw_base = Path(raw_output_root or output_root).expanduser().resolve()
    raw_base.mkdir(parents=True, exist_ok=True)
    archive_base = (
        Path(compact_archive_root).expanduser().resolve()
        if compact_archive_root is not None else None
    )
    if (size_pilot_design_id == r22.V4_THREE_SCHEMA_SIZE_PILOT_DESIGN_ID
            and archive_base is None):
        raise ValueError("three-schema size pilot requires a complete archive output root")
    failure_archive_base = (
        Path(failure_archive_root).expanduser().resolve()
        if failure_archive_root is not None else None
    )
    if case_evaluator_fn is not None and archive_base is not None and failure_archive_base is None:
        raise ValueError("V3 case classification with compaction requires failure_archive_root")
    manifest_path = output_root / "matrix_manifest.json"
    digest = _design_digest(design)
    failed_case_to_retry: str | None = None
    metadata = dict(manifest_metadata or {})
    execution_identity = metadata.get("execution_identity", metadata)
    preflight_snapshot = metadata.get("preflight_snapshot")
    if manifest_path.is_file():
        if not resume:
            raise FileExistsError(f"matrix output already exists; pass --resume: {output_root}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("design_id") != design.get("design_id") or manifest.get("design_sha256") != digest:
            raise ValueError("cannot resume with a different frozen design")
        if manifest.get("status") == "GATE_FAILED":
            if not retry_failed_case:
                raise RuntimeError("phase previously failed its gate; use a new evidence root after review")
            latest_by_case: dict[str, Mapping[str, Any]] = {}
            for row in manifest.get("cases", []):
                if isinstance(row, Mapping) and row.get("case_id"):
                    latest_by_case[str(row["case_id"])] = row
            failed_case_ids = [
                case_id for case_id, row in latest_by_case.items()
                if row.get("status") == "GATE_FAILED"
            ]
            if (len(failed_case_ids) != 1
                    or any(row.get("status") == "UNKNOWN_STOP" for row in latest_by_case.values())):
                raise RuntimeError(
                    "retry_failed_case requires exactly one latest GATE_FAILED case and no UNKNOWN_STOP cases"
                )
            failed_case_to_retry = failed_case_ids[0]
            phase_case_ids = {
                r22.case_identifier(
                    phase=phase, seed=item["seed"], speed_mps=item["speed_mps"],
                    snr_db=item["snr_db"], delay_ns=item["delay_ns"],
                    target_count=item["target_count"], period_count=item["period_count"],
                ) for item in specs
            }
            if failed_case_to_retry not in phase_case_ids:
                raise ValueError("failed case is outside the explicitly authorized phase scope")
            failed_attempt = int(latest_by_case[failed_case_to_retry].get("attempt", 0))
            manifest.setdefault("recovery_events", []).append({
                "type": "EXPLICIT_RETRY_OF_GATE_FAILED_CASE",
                "status": "RETRY_AUTHORIZED",
                "case_id": failed_case_to_retry,
                "failed_attempt": failed_attempt,
                "prior_case_status": "GATE_FAILED",
                "requested_at_utc": _utc_now(),
                "authorization_design_id": str(design.get("design_id")),
                "approved_design_sha256": str(approved_design_sha256),
            })
            manifest["status"] = "RUNNING"
        elif retry_failed_case:
            raise RuntimeError("retry_failed_case requires a matrix currently stopped at GATE_FAILED")
        if case_evaluator_fn is not None and manifest.get("status") == "UNKNOWN_STOP":
            raise RuntimeError("phase previously stopped on unknown evidence; use a new evidence root after review")
        if metadata:
            if manifest.get("execution_identity") != execution_identity:
                raise ValueError("cannot resume with changed V3 source, configuration, or binary identity")
            if isinstance(preflight_snapshot, Mapping):
                manifest.setdefault("preflight_snapshots", []).append(dict(preflight_snapshot))
                _write_json_atomic(manifest_path, manifest)
        prior_archive_profile = manifest.get("archive_profile", "legacy")
        if prior_archive_profile != archive_profile:
            raise ValueError("cannot resume with a different compact-archive profile")
        if manifest.get("archive_strategy") != archive_strategy:
            raise ValueError("cannot resume with a different V4 archive strategy")
        if manifest.get("v4_size_pilot_authorization") != v4_size_pilot_authorization:
            raise ValueError("cannot resume with a different V4 size-pilot authorization identity")
    else:
        if retry_failed_case:
            raise RuntimeError("retry_failed_case requires an existing GATE_FAILED matrix manifest")
        if any(output_root.iterdir()):
            raise FileExistsError(f"refuse to use non-empty matrix output root: {output_root}")
        manifest = {
            "schema_version": 1,
            "design_id": design["design_id"],
            "design_sha256": digest,
            "phase": phase,
            "archive_profile": archive_profile,
            "archive_strategy": archive_strategy,
            "v4_size_pilot_authorization": v4_size_pilot_authorization,
            "status": "RUNNING",
            "planned_case_count": len(build_case_specs(design, phase)),
            "cases": [],
            "started_at_utc": _utc_now(),
        }
        if metadata:
            manifest["execution_identity"] = execution_identity
            if isinstance(preflight_snapshot, Mapping):
                manifest["preflight_snapshots"] = [dict(preflight_snapshot)]
    if manifest.get("phase") != phase:
        raise ValueError("matrix output root belongs to a different phase")

    default_passed_states = {"PASSED", "H0_EVALUABLE_WITH_GAPS"}
    passed_states = set(default_passed_states)
    if case_evaluator_fn is not None:
        passed_states.add("PASSED_WITH_METHOD_GAPS")
    failure_states = {"GATE_FAILED", "UNKNOWN_STOP"} if case_evaluator_fn is not None else set()

    if manifest.get("status") == "ARCHIVE_PENDING":
        pending = next((row for row in manifest.get("cases", [])
                        if row.get("compact_archive_status") == "PENDING"
                        and row.get("status") in passed_states | failure_states), None)
        if pending is not None and archive_base is not None:
            try:
                _ensure_compact_archive(
                    pending, phase=phase, raw_base=raw_base,
                    archive_base=archive_base, manifest=manifest,
                    manifest_path=manifest_path,
                    failure_archive_root=failure_archive_base,
                    archive_profile=archive_profile,
                    require_complete_output=(
                        archive_profile == "r22_v4_output_complete"
                        and pending.get("status") in passed_states
                    ),
                )
            except Exception as exc:
                pending["compact_archive_status"] = "PENDING"
                pending["compact_archive_error"] = {
                    "type": type(exc).__name__, "message": str(exc),
                }
                manifest["status"] = "ARCHIVE_PENDING"
                manifest["finished_at_utc"] = _utc_now()
                _write_json_atomic(manifest_path, manifest)
                raise
            if pending.get("status") in failure_states:
                manifest["status"] = pending["status"]
                manifest["finished_at_utc"] = _utc_now()
                _write_json_atomic(manifest_path, manifest)
                return {**manifest, "completed_cases": 0, "skipped_cases": 0}
            manifest["status"] = "RUNNING"
            _write_json_atomic(manifest_path, manifest)

    existing = {row["case_id"]: row for row in manifest.get("cases", []) if "case_id" in row}
    completed_now = skipped_now = 0
    case_runner = run_case_fn or r22.run_case
    common = design.get("common", {})
    for spec in specs:
        case_id = r22.case_identifier(
            phase=phase, seed=spec["seed"], speed_mps=spec["speed_mps"],
            snr_db=spec["snr_db"], delay_ns=spec["delay_ns"],
            target_count=spec["target_count"], period_count=spec["period_count"],
        )
        previous = existing.get(case_id)
        if previous and previous.get("status") in passed_states:
            if archive_base is not None:
                prior_archive = previous.get("compact_archive")
                prior_archive_verified = False
                if prior_archive is not None:
                    if not isinstance(prior_archive, Mapping):
                        raise ValueError(f"invalid compact archive record for {case_id}")
                    expected_compact_profile = (
                        sharded.COMPACT_PROFILE
                        if archive_strategy == "v4_sharded_full_then_compact"
                        else archive_profile
                    )
                    if prior_archive.get("archive_profile", "legacy") != expected_compact_profile:
                        raise ValueError(f"compact archive profile changed for {case_id}")
                    if archive_strategy == "v4_sharded_full_then_compact":
                        _verify_three_schema_size_pilot_archives(
                            previous, phase=phase, archive_base=archive_base,
                        )
                    else:
                        verify_case_archive(
                            Path(str(prior_archive.get("archive_path", ""))),
                            expected_sha256=str(prior_archive.get("archive_sha256", "")),
                            expected_manifest_sha256=str(prior_archive.get("archive_manifest_sha256", "")),
                            archive_profile=archive_profile,
                            require_complete_output=(archive_profile == "r22_v4_output_complete"),
                        )
                    prior_archive_verified = True
                try:
                    _ensure_compact_archive(
                        previous, phase=phase, raw_base=raw_base,
                        archive_base=archive_base, manifest=manifest,
                        manifest_path=manifest_path,
                        failure_archive_root=failure_archive_base,
                        archive_profile=archive_profile,
                        require_complete_output=(
                            archive_profile == "r22_v4_output_complete"
                            and previous.get("status") in passed_states
                        ),
                        prior_archive_verified=prior_archive_verified,
                    )
                    if manifest.get("status") == "ARCHIVE_PENDING":
                        manifest["status"] = "RUNNING"
                        _write_json_atomic(manifest_path, manifest)
                except Exception as exc:
                    previous["compact_archive_status"] = "PENDING"
                    previous["compact_archive_error"] = {
                        "type": type(exc).__name__, "message": str(exc),
                    }
                    manifest["status"] = "ARCHIVE_PENDING"
                    manifest["finished_at_utc"] = _utc_now()
                    _write_json_atomic(manifest_path, manifest)
                    break
            skipped_now += 1
            continue
        if previous and previous.get("status") in {"RUNNING", "INTERRUPTED"}:
            if previous.get("status") == "RUNNING":
                previous["status"] = "INTERRUPTED"
                previous["finished_at_utc"] = _utc_now()
                previous["interruption_reason"] = "runner ended before a terminal case checkpoint"
                sample_log = previous.get("resource_usage_sample_log")
                if sample_log and "resource_usage" not in previous:
                    recovered_usage = _recover_disk_space_samples(str(sample_log))
                    if recovered_usage is not None:
                        previous["resource_usage"] = recovered_usage
            if not retry_interrupted:
                manifest["status"] = "INTERRUPTED_REVIEW_REQUIRED"
                manifest["interrupted_case_id"] = case_id
                manifest["finished_at_utc"] = _utc_now()
                _write_json_atomic(manifest_path, manifest)
                break
            _write_json_atomic(manifest_path, manifest)
        elif previous:
            if not (
                retry_failed_case
                and case_id == failed_case_to_retry
                and previous.get("status") == "GATE_FAILED"
            ):
                manifest["status"] = "GATE_FAILED"
                _write_json_atomic(manifest_path, manifest)
                break
        if size_pilot_design_id == r22.V4_THREE_SCHEMA_SIZE_PILOT_DESIGN_ID:
            storage_paths = {
                "matrix_manifest": output_root,
                "raw_case_outputs": raw_base,
                "complete_archive": archive_base,
            }
            if failure_archive_base is not None:
                storage_paths["failure_archive"] = failure_archive_base
            resource_gate = _v4_three_schema_size_pilot_resource_gate(storage_paths, design)
            manifest.setdefault("resource_gate_history", []).append(resource_gate)
            manifest["resource_gate"] = resource_gate
            if resource_gate["status"] != "READY":
                manifest["status"] = "PAUSED_RESOURCE_GATE"
                manifest["paused_case_id"] = case_id
                manifest["finished_at_utc"] = _utc_now()
                _write_json_atomic(manifest_path, manifest)
                break
            manifest.pop("paused_case_id", None)
        attempts_root = raw_base / phase / case_id
        attempts_root.mkdir(parents=True, exist_ok=True)
        prior_attempts = [
            int(row.get("attempt", 0))
            for row in manifest.get("cases", [])
            if row.get("case_id") == case_id
        ]
        attempt = max(prior_attempts, default=0) + 1
        while (attempts_root / f"attempt_{attempt:03d}").exists():
            attempt += 1
        case_root = attempts_root / f"attempt_{attempt:03d}"
        print(f"[{phase}] START {case_id} seed={spec['seed']} output={case_root}", flush=True)
        started = _utc_now()
        record: dict[str, Any] = {
            "case_id": case_id,
            "condition_id": spec["condition_id"],
            "seed": spec["seed"],
            "attempt": attempt,
            "status": "RUNNING",
            "output_root": str(case_root),
            "started_at_utc": started,
        }
        if archive_strategy is not None:
            record["archive_strategy"] = archive_strategy
        if failed_case_to_retry == case_id:
            recovery_event = manifest["recovery_events"][-1]
            recovery_event.update({
                "status": "RETRY_STARTED",
                "retry_attempt": attempt,
                "retry_started_at_utc": started,
            })
        manifest.setdefault("cases", []).append(record)
        existing[case_id] = record
        manifest["status"] = "RUNNING"
        manifest["active_case_id"] = case_id
        manifest["active_case_output_root"] = str(case_root)
        _write_json_atomic(manifest_path, manifest)
        resource_monitor: _DiskSpaceMonitor | None = None
        resource_monitor_started = False
        try:
            if archive_strategy == "v4_sharded_full_then_compact":
                sample_log = (
                    output_root / "resource_usage"
                    / f"{case_id}__attempt_{attempt:03d}.jsonl"
                )
                resource_monitor = _DiskSpaceMonitor(
                    {
                        "matrix_manifest": output_root,
                        "raw_case_outputs": raw_base,
                        "complete_archive": archive_base,
                    },
                    sample_log=sample_log,
                )
                record["resource_usage_sample_log"] = str(sample_log)
                _write_json_atomic(manifest_path, manifest)
                resource_monitor.start()
                resource_monitor_started = True
            case_kwargs = {
                "phase": phase,
                "seed": spec["seed"],
                "speed_mps": spec["speed_mps"],
                "snr_db": spec["snr_db"],
                "delay_ns": spec["delay_ns"],
                "target_count": spec["target_count"],
                "period_count": spec["period_count"],
                "first_beam": spec["first_beam"],
                "beam_count": spec["beam_count"],
                "stage2_binary": Path(stage2_binary or r22.DEFAULT_STAGE2),
                "production_binary": Path(production_binary or r22.DEFAULT_PRODUCTION),
                "template": _relative_path(str(template) if template else spec.get("scenario_template")) or r22.DEFAULT_TEMPLATE,
                "method_config_path": _relative_path(str(method_config) if method_config else spec.get("method_config")) or r22.DEFAULT_METHOD_CONFIG,
                "method_ids": tuple(spec["method_ids"]),
                "calibration_modes": tuple(spec["calibration_modes"]),
                "area": spec["area"],
            }
            if archive_profile == "r22_v4_output_complete":
                case_kwargs["v4_output_complete"] = True
                case_kwargs["keep_raw"] = True
                case_authorization = v4_size_pilot_authorization
                if (isinstance(v4_size_pilot_authorization, Mapping)
                        and v4_size_pilot_authorization.get("status") == "AUTHORIZED_THREE_SCHEMA_SIZE_PILOT"):
                    case_authorization = v4_size_pilot_authorization.get(
                        "cases_by_case_id", {},
                    ).get(case_id)
                    if not isinstance(case_authorization, Mapping):
                        raise ValueError(f"size-pilot authorization missing case identity: {case_id}")
                case_kwargs["v4_size_pilot_authorization"] = case_authorization
            result = dict(case_runner(
                case_root,
                **case_kwargs,
            ))
            if case_evaluator_fn is None:
                state = _case_state(phase, result)
            else:
                classification = dict(case_evaluator_fn(case_root, result))
                record["case_evaluation"] = classification
                scenario_status = classification.get("scenario_status")
                can_continue = classification.get("continue_matrix") is True
                if scenario_status == "CASE_VALID" and can_continue:
                    state = "PASSED"
                elif scenario_status == "CASE_VALID_WITH_METHOD_GAPS" and can_continue:
                    state = "PASSED_WITH_METHOD_GAPS"
                elif scenario_status == "CASE_INVALID":
                    state = "GATE_FAILED"
                else:
                    state = "UNKNOWN_STOP"
            record.update({
                "implementation_result": result.get("implementation_result"),
                "status_labels": result.get("status_labels"),
                "evaluation_summary": result.get("evaluation_summary"),
                "all_production_branches_passed": result.get("all_production_branches_passed"),
            })
        except Exception as exc:
            state = "GATE_FAILED" if case_evaluator_fn is None else "UNKNOWN_STOP"
            record["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        record["status"] = state
        record["finished_at_utc"] = _utc_now()
        if failed_case_to_retry == case_id:
            recovery_event = manifest["recovery_events"][-1]
            recovery_event.update({
                "status": "RETRY_FINISHED",
                "retry_case_status": state,
                "retry_finished_at_utc": record["finished_at_utc"],
            })
        completed_now += 1
        manifest["status"] = "RUNNING" if state in passed_states else "GATE_FAILED"
        if state == "UNKNOWN_STOP":
            manifest["status"] = "UNKNOWN_STOP"
        manifest.pop("active_case_id", None)
        manifest.pop("active_case_output_root", None)
        manifest["finished_at_utc"] = _utc_now()
        _write_json_atomic(manifest_path, manifest)
        should_archive = state in passed_states or (
            state in failure_states and failure_archive_base is not None
        )
        archive_failed = False
        if should_archive and archive_base is not None:
            try:
                record["compact_archive_status"] = "PENDING"
                _write_json_atomic(manifest_path, manifest)
                _ensure_compact_archive(
                    record, phase=phase, raw_base=raw_base,
                    archive_base=archive_base, manifest=manifest,
                    manifest_path=manifest_path,
                    failure_archive_root=failure_archive_base,
                    archive_profile=archive_profile,
                    require_complete_output=(
                        archive_profile == "r22_v4_output_complete"
                        and record.get("status") in passed_states
                    ),
                )
            except Exception as exc:
                record["compact_archive_status"] = "PENDING"
                record["compact_archive_error"] = {
                    "type": type(exc).__name__, "message": str(exc),
                }
                manifest["status"] = "ARCHIVE_PENDING"
                manifest["finished_at_utc"] = _utc_now()
                _write_json_atomic(manifest_path, manifest)
                print(f"[{phase}] ARCHIVE_PENDING {case_id}: {exc}", flush=True)
                archive_failed = True
        if resource_monitor_started and resource_monitor is not None:
            try:
                record["resource_usage"] = resource_monitor.stop()
            except Exception as exc:
                record["resource_usage_error"] = {
                    "type": type(exc).__name__, "message": str(exc),
                    "sample_log": record.get("resource_usage_sample_log"),
                }
            _write_json_atomic(manifest_path, manifest)
        print(f"[{phase}] {state} {case_id}", flush=True)
        if archive_failed:
            break
        if state in failure_states or state == "GATE_FAILED":
            break

    terminal_ids = {
        row["case_id"] for row in manifest.get("cases", [])
        if row.get("status") in passed_states
    }
    if manifest.get("status") not in {
        "GATE_FAILED", "UNKNOWN_STOP", "ARCHIVE_PENDING", "INTERRUPTED_REVIEW_REQUIRED",
        "PAUSED_RESOURCE_GATE",
    }:
        full_case_count = len(build_case_specs(design, phase))
        manifest["status"] = "COMPLETED" if len(terminal_ids) == full_case_count else "PARTIAL_BOUNDED_RUN"
        manifest["finished_at_utc"] = _utc_now()
        _write_json_atomic(manifest_path, manifest)
    return {
        **manifest,
        "completed_cases": completed_now,
        "skipped_cases": skipped_now,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--phase", required=True,
                        choices=("engineering", "screening", "confirmatory", "dense", "bridge", "h0"))
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--stage2-binary", type=Path, default=r22.DEFAULT_STAGE2)
    parser.add_argument("--production-binary", type=Path, default=r22.DEFAULT_PRODUCTION)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--method-config", type=Path)
    parser.add_argument(
        "--raw-output-root", type=Path,
        help="temporary case output root; can be /tmp to keep the project tree compact",
    )
    parser.add_argument(
        "--compact-archive-root", type=Path,
        help="archive successfully evaluated cases here after verification",
    )
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--archive-profile", choices=("legacy", "r22_v4_output_complete"),
        default="legacy",
        help="V4 preserves all per-period Detection and TrackManager/PIPE evidence",
    )
    parser.add_argument(
        "--retry-interrupted", action="store_true",
        help="start a new attempt after preserving an interrupted case record",
    )
    parser.add_argument(
        "--retry-failed-case", action="store_true",
        help="retry exactly one gate-failed case in the approved three-schema size pilot",
    )
    parser.add_argument(
        "--authorize-v4-size-pilot", action="store_true",
        help="authorize only a hash-bound user-approved R22-V4 size-pilot design",
    )
    parser.add_argument(
        "--approved-design-sha256",
        help="must equal the approved SHA-256 of the selected V4 size-pilot JSON",
    )
    args = parser.parse_args(argv)
    try:
        design = load_design(args.design)
        result = run_matrix(
            design,
            phase=args.phase,
            output_root=args.output_root,
            stage2_binary=args.stage2_binary,
            production_binary=args.production_binary,
            template=args.template,
            method_config=args.method_config,
            raw_output_root=args.raw_output_root,
            compact_archive_root=args.compact_archive_root,
            max_cases=args.max_cases,
            resume=args.resume,
            retry_interrupted=args.retry_interrupted,
            retry_failed_case=args.retry_failed_case,
            archive_profile=args.archive_profile,
            design_source_path=args.design,
            authorize_v4_size_pilot=args.authorize_v4_size_pilot,
            approved_design_sha256=args.approved_design_sha256,
        )
    except Exception as exc:
        print(f"[r22_experiment_matrix][ERROR] {type(exc).__name__}: {exc}")
        return 2
    print(json.dumps({
        "phase": args.phase,
        "status": result.get("status"),
        "completed_cases": result.get("completed_cases"),
        "skipped_cases": result.get("skipped_cases"),
        "planned_case_count": result.get("planned_case_count"),
        "manifest": str(args.output_root.expanduser().resolve() / "matrix_manifest.json"),
    }, ensure_ascii=False, sort_keys=True))
    return 2 if result.get("status") in {
        "GATE_FAILED", "ARCHIVE_PENDING", "INTERRUPTED_REVIEW_REQUIRED",
    } else 0


if __name__ == "__main__":
    raise SystemExit(main())
