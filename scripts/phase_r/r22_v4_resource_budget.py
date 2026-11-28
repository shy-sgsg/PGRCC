#!/usr/bin/env python3
"""Build an auditable retained-storage and per-case peak budget for R22 V4."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import shutil
import zipfile
from pathlib import Path
from typing import Any, Mapping

from scripts.phase_r import r22_sharded_archive as sharded
from scripts.phase_r import r22_v4_confirmatory_executor as executor
from scripts.phase_r.r22_compact_archive import verify_case_archive


PROJECT_CAP_BYTES = 20_000_000_000
RESOURCE_RESERVE_BYTES = executor.RESOURCE_RESERVE_BYTES
PEAK_TEMPORARY_BYTES = 29_821_104_128
P0_FULL_ARCHIVE_SHA256 = "0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02"
P0_COMPACT_SHA256 = "71b50b6110e04403b2152f8e292bb3e7df9ae4201fd2790d9d97595e7b12ae5a"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _case_record(path: Path, case_id: str) -> dict[str, Any]:
    manifest = _load(path)
    matches = [row for row in manifest.get("cases", []) if row.get("case_id") == case_id]
    if len(matches) != 1:
        raise ValueError(f"expected one pilot case record {case_id}: {path}")
    return matches[0]


def _model_target_plus4_receipt(
    *, pilot_receipt: Path, p0_archive: Path, p0_compact: Path,
) -> dict[str, Any]:
    """Model the receipt envelope from the actual P0 member inventory and shard plan."""
    model = _load(pilot_receipt)
    with zipfile.ZipFile(p0_archive, "r") as archive:
        manifest_bytes = archive.read("ARCHIVE_MANIFEST.json")
        manifest = json.loads(manifest_bytes)
        generated: dict[str, str] = {}
        for row in manifest.get("included_files", []):
            if row.get("generated_from"):
                generated[str(row["path"])] = archive.read(str(row["path"])).decode("utf-8")

    case_identity = {
        "case_id": "modeled_target_present_seed1937003_delay4",
        "phase": "target_present",
        "target_count": 20,
        "delay_error_ns": 4.0,
    }
    manifest["case_phase"] = "confirmatory"
    manifest["case_schema_identity"] = case_identity
    manifest["case_schema_identity_sha256"] = hashlib.sha256(json.dumps(
        case_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    manifest["case_schema"] = {
        "status": "PASS",
        "phase_family": "target_present",
        "required_members": ["r22_run_manifest.json"],
    }
    manifest["required_v4_members"] = sorted(
        set(manifest.get("required_v4_members", [])) | {"r22_run_manifest.json"}
    )

    descriptors = sharded._member_descriptors({"archive_manifest": manifest}, generated)
    shard_plan = sharded._make_shard_plan(descriptors, 512 * 1024 * 1024)
    case_root = "/project/outputs/r22_v4_confirmatory/raw/modeled_case/attempt_001"
    archive_name = "modeled_case__attempt_001.archive-set.json"
    model.update({
        "case_root": case_root,
        "raw_root": "/project/outputs/r22_v4_confirmatory/raw",
        "archive_manifest": manifest,
        "archive_manifest_sha256": hashlib.sha256(
            sharded._json_bytes(manifest)
        ).hexdigest(),
        "generated_members": generated,
        "shard_plan": shard_plan,
        "plan_sha256": "",
        "shards": [],
    })
    model["plan_sha256"] = sharded._set_plan_sha256(model)

    cleanup = copy.deepcopy(model.get("full_archive_cleanup", {}))
    cleanup["status"] = "REMOVED_AFTER_COMPACT_VERIFICATION"
    cleanup["shards"] = []
    model["full_archive_cleanup"] = cleanup
    shard_records = []
    for index, plan in enumerate(shard_plan):
        digest = hashlib.sha256(f"modeled-shard-digest-{index}".encode()).hexdigest()
        shard_relative = (
            f"{archive_name}.shards/part_{index:04d}.zip"
        )
        estimated_shard_bytes = max(
            1, int(plan["uncompressed_member_bytes"]) // 3,
        )
        members = [str(row["path"]) for row in plan["members"]]
        shard_records.append({
            "index": index,
            "path": shard_relative,
            "sha256": digest,
            "bytes": estimated_shard_bytes,
            "uncompressed_member_bytes": int(plan["uncompressed_member_bytes"]),
            "member_count": len(members),
            "members": members,
            "status": "VERIFIED",
            "crc_verified": True,
            "member_manifest_verified": True,
        })
        cleanup["shards"].append({
            "index": index,
            "path": shard_relative,
            "sha256": digest,
            "bytes": estimated_shard_bytes,
            "status": "REMOVED_AFTER_COMPACT_VERIFICATION",
            "removed_at_utc": "2026-11-28T00:00:00+00:00",
        })
    model["shards"] = shard_records
    model["full_archive_cleanup"] = cleanup
    if _sha256(p0_compact) != P0_COMPACT_SHA256:
        raise ValueError("P0 compact candidate SHA-256 differs from its prior audit identity")
    with zipfile.ZipFile(p0_compact, "r") as compact_zip:
        if compact_zip.testzip() is not None:
            raise ValueError("P0 compact candidate failed ZIP CRC verification")
        p0_compact_member_count = len(compact_zip.namelist())
    model["compact_archive"] = {
        "archive_path": "/project/outputs/r22_v4_confirmatory/archives/modeled_case.compact.zip",
        "archive_sha256": P0_COMPACT_SHA256,
        "archive_manifest_sha256": model["compact_archive"]["archive_manifest_sha256"],
        "archive_profile": sharded.COMPACT_PROFILE,
        "archive_bytes": p0_compact.stat().st_size,
        "included_file_count": p0_compact_member_count,
        "included_bytes": int(manifest.get("included_bytes", 0)),
        "crc_verified": True,
        "member_integrity_basis": "previously audited exact P0 compact schema-v2 artifact",
    }
    model.update({
        "source_file_count": (
            len(manifest.get("included_files", []))
            + len(manifest.get("excluded_files", []))
            + len(manifest.get("symlink_entries", []))
        ),
        "source_file_bytes": (
            int(manifest.get("included_bytes", 0))
            + int(manifest.get("excluded_bytes", 0))
            + int(manifest.get("symlink_target_bytes", 0))
        ),
        "source_excluded_file_count": len(manifest.get("excluded_files", [])),
        "source_excluded_bytes": int(manifest.get("excluded_bytes", 0)),
        "included_file_count": len(manifest.get("included_files", [])),
        "included_bytes": int(manifest.get("included_bytes", 0)),
        "excluded_file_count": len(manifest.get("excluded_files", [])),
        "excluded_bytes": int(manifest.get("excluded_bytes", 0)),
        "symlink_count": len(manifest.get("symlink_entries", [])),
        "archive_bytes": sum(int(row["bytes"]) for row in shard_records),
        "status": "COMPACTED_CLEANED",
        "case_schema_model_identity": case_identity,
    })
    model["archive_set_identity_sha256"] = sharded._archive_set_identity_sha256(model)
    receipt_bytes = sharded._json_bytes(model)
    return {
        "modeled_shard_count": len(shard_plan),
        "modeled_manifest_member_count": len(manifest.get("included_files", [])),
        "modeled_receipt_uncompressed_bytes": len(receipt_bytes),
        "modeled_receipt_gzip9_bytes": len(gzip.compress(
            receipt_bytes, compresslevel=9, mtime=0,
        )),
        "source_p0_archive_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "model_policy": "actual P0 full-output member inventory and extracted manifest data; current 512 MiB sharding plan; deterministic fixed-width shard identities; observed cleanup receipt structure; not a new CUDA execution",
    }


def _compact_manifest_sha256(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as archive:
        return hashlib.sha256(archive.read("ARCHIVE_MANIFEST.json")).hexdigest()


def _retained_record(
    *, compact_path: Path, receipt_path: Path, compact_size: int | None = None,
    receipt_gzip_bound: int | None = None, basis: str,
) -> dict[str, Any]:
    compact_sha = _sha256(compact_path)
    with zipfile.ZipFile(compact_path, "r") as compact_zip:
        if compact_zip.testzip() is not None:
            raise ValueError(f"compact archive failed ZIP CRC verification: {compact_path}")
        has_archive_manifest = "ARCHIVE_MANIFEST.json" in compact_zip.namelist()
    compact_manifest_sha = (
        _compact_manifest_sha256(compact_path) if has_archive_manifest else None
    )
    if has_archive_manifest:
        verify_case_archive(
            compact_path,
            expected_sha256=compact_sha,
            expected_manifest_sha256=str(compact_manifest_sha),
            archive_profile=sharded.COMPACT_PROFILE,
        )
    raw_receipt = receipt_path.read_bytes()
    receipt_gzip = len(gzip.compress(raw_receipt, compresslevel=9, mtime=0))
    stored_size = int(compact_size if compact_size is not None else compact_path.stat().st_size)
    receipt_bound = int(receipt_gzip_bound if receipt_gzip_bound is not None else receipt_gzip)
    return {
        "compact_archive": {
            "path": str(compact_path),
            "bytes": stored_size,
            "sha256": compact_sha,
            "manifest_sha256": compact_manifest_sha,
            "crc_verified": True,
            "member_sha_basis": (
                "current compact-profile manifest verifier"
                if has_archive_manifest else "previously audited exact P0 compact schema-v2 SHA-256"
            ),
        },
        "archive_set_receipt_source": {
            "path": str(receipt_path),
            "bytes": len(raw_receipt),
            "sha256": hashlib.sha256(raw_receipt).hexdigest(),
            "gzip9_observed_bytes": receipt_gzip,
        },
        "receipt_gzip9_retention_bound_bytes": receipt_bound,
        "retained_case_bound_bytes": stored_size + receipt_bound,
        "basis": basis,
    }


def build_resource_evidence(project_root: Path, output_path: Path) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    pilot_root = repo_root / "docs/research/phase_r/R22/evidence/v4_three_schema_size_pilot_20261126"
    p0_root = repo_root / "outputs/r22_v4_p0_seed1937003_20261123"
    p0_archive = p0_root / (
        "archives/engineering/engineering__seed_1937003__vr_2d5__snr_20__delay_4ns__"
        "targets_20__periods_10__attempt_001.zip"
    )
    p0_compact = p0_root / "archives_compact_candidate/p0_target_present_plus4ns_compact_schema_v2.zip"
    engineering_archive_root = pilot_root / "archives/engineering"
    h0_archive_root = pilot_root / "archives/h0"
    tp0_id = "engineering__seed_1937004__vr_2d5__snr_20__delay_0ns__targets_20__periods_10__attempt_001"
    h00_id = "h0__seed_1937005__vr_0__snr_20__delay_0ns__targets_0__periods_10__attempt_003"
    h04_id = "h0__seed_1937005__vr_0__snr_20__delay_4ns__targets_0__periods_10__attempt_001"
    tp0_receipt = engineering_archive_root / f"{tp0_id}.archive-set.json"
    h00_receipt = h0_archive_root / f"{h00_id}.archive-set.json"
    h04_receipt = h0_archive_root / f"{h04_id}.archive-set.json"
    tp0_compact = engineering_archive_root / f"{tp0_id}.compact.zip"
    h00_compact = h0_archive_root / f"{h00_id}.compact.zip"
    h04_compact = h0_archive_root / f"{h04_id}.compact.zip"

    with zipfile.ZipFile(p0_archive) as p0_zip:
        p0_manifest = json.loads(p0_zip.read("ARCHIVE_MANIFEST.json"))
    p0_case_schema_model = _model_target_plus4_receipt(
        pilot_receipt=h04_receipt, p0_archive=p0_archive, p0_compact=p0_compact,
    )
    conservative_receipt_bound = int(math.ceil(
        p0_case_schema_model["modeled_receipt_gzip9_bytes"] * 1.1 / 100_000
    ) * 100_000)

    retained = {
        "target_present_0ns": _retained_record(
            compact_path=tp0_compact, receipt_path=tp0_receipt,
            basis="actual 2026-11-26 Native CUDA size pilot; observed compact plus gzip-9 full receipt",
        ),
        "target_present_4ns": _retained_record(
            compact_path=p0_compact, receipt_path=h04_receipt,
            receipt_gzip_bound=conservative_receipt_bound,
            basis="actual P0 compact plus conservative gzip receipt model from the P0 full-output member inventory; modeled receipt size is not an executed V4 archive",
        ),
        "h0_0ns": _retained_record(
            compact_path=h00_compact, receipt_path=h00_receipt,
            basis="actual 2026-11-26 Native CUDA size pilot attempt 003; observed compact plus gzip-9 full receipt",
        ),
        "h0_4ns": _retained_record(
            compact_path=h04_compact, receipt_path=h04_receipt,
            basis="actual 2026-11-26 Native CUDA size pilot; observed compact plus gzip-9 full receipt",
        ),
    }
    retained["target_present_4ns"]["archive_set_receipt_source"]["role"] = (
        "observed H0+4 ns receipt used as a compression comparator; this is not the target-present receipt"
    )
    counts = {
        "target_present_0ns": 90,
        "target_present_4ns": 90,
        "h0_0ns": 30,
        "h0_4ns": 30,
    }
    per_schema = {
        schema: row["retained_case_bound_bytes"] for schema, row in retained.items()
    }
    retained_total = sum(per_schema[key] * counts[key] for key in counts)
    retained_with_margin = (retained_total * 5 + 3) // 4
    peak_plus_reserve = PEAK_TEMPORARY_BYTES + RESOURCE_RESERVE_BYTES
    project_root = Path(project_root).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()
    pilot_manifests = {
        "target_present_0ns": pilot_root / "engineering_matrix/matrix_manifest.json",
        "h0_0ns": pilot_root / "h0_matrix/matrix_manifest.json",
        "h0_4ns": pilot_root / "h0_matrix/matrix_manifest.json",
    }
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "created_at_utc": None,
        "status": "PASS_WITH_CONSERVATIVE_RECEIPT_MODEL",
        "archive_profile": "r22_v4_output_complete",
        "retained_archive_profile": sharded.COMPACT_PROFILE,
        "stage2_production_cuda_started": True,
        "peak_temporary_includes_full_archive": True,
        "formal_confirmatory_matrix_started": False,
        "evidence_scope_note": "Three missing case schemas have completed the approved Native CUDA pilots. The target-present +4 ns compact size comes from P0; only its new compressed full-output receipt is modeled conservatively and marked as such.",
        "pilot_matrix_manifests": {
            key: {"path": str(path), "sha256": _sha256(path)}
            for key, path in pilot_manifests.items()
        },
        "case_schema_retained_evidence": retained,
        "target_present_plus4_receipt_model": {
            **p0_case_schema_model,
            "receipt_bound_bytes": conservative_receipt_bound,
            "receipt_bound_rule": "round modeled gzip-9 size upward to the next 100,000 bytes after a 10% allowance",
        },
        "max_retained_case_bytes_by_schema": per_schema,
        "matrix_case_count": sum(counts.values()),
        "matrix_case_count_by_schema": counts,
        "retained_case_bytes_total": retained_total,
        "retained_case_bytes_with_25pct_margin": retained_with_margin,
        "peak_temporary_bytes": PEAK_TEMPORARY_BYTES,
        "peak_temporary_source": {
            "path": "docs/research/phase_r/R22/56_v4_phase_h_continuous_execution_20261126.md",
            "value_bytes": PEAK_TEMPORARY_BYTES,
            "meaning": "observed P0 start-free to minimum-free consumption while raw inputs and full archive coexisted; host activity may contribute",
        },
        "storage_reserve_bytes": RESOURCE_RESERVE_BYTES,
        "minimum_filesystem_free_bytes": retained_with_margin + peak_plus_reserve,
        "project_root": str(project_root),
        "project_allocated_bytes_at_measurement": 0,
        "project_final_cap_bytes": PROJECT_CAP_BYTES,
        "project_final_retained_projection_bytes": retained_with_margin,
        "project_final_allocated_projection_bytes": 0,
        "project_cap_margin_bytes": 0,
        "p0_full_archive": {
            "path": str(p0_archive),
            "bytes": p0_archive.stat().st_size,
            "recorded_sha256": P0_FULL_ARCHIVE_SHA256,
            "sha_recomputed_this_run": False,
            "embedded_manifest_sha256": hashlib.sha256(
                zipfile.ZipFile(p0_archive).read("ARCHIVE_MANIFEST.json")
            ).hexdigest(),
            "included_member_count": len(p0_manifest.get("included_files", [])),
            "excluded_member_count": len(p0_manifest.get("excluded_files", [])),
        },
        "project_measurement_method": "sum st_blocks*512 once per device/inode under project_root; excludes only the active case raw subtree for per-case final-state gating",
        "resource_gate_model": "retained matrix compact+gzip receipt with 25% margin + measured full-case peak temporary bytes + 10 GiB preserved free-space reserve",
        "final_project_gate_model": "actual project allocated bytes at each case boundary excluding active raw case + 25%-margined retained archives for remaining cases must be <=20,000,000,000 bytes",
        "notes": [
            "The resource calculation is a feasibility and execution-safety budget, not a confirmatory scientific result.",
            "The P0 full ZIP remains in its original path and is not modified or relocated.",
            "GPU and Stage2 pilots are already complete; this script only reads their manifests and archive metadata.",
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(4):
        allocated = executor._allocated_tree_bytes(project_root)
        free = int(shutil.disk_usage(project_root).free)
        evidence["project_allocated_bytes_at_measurement"] = allocated
        evidence["project_final_allocated_projection_bytes"] = allocated + retained_with_margin
        evidence["project_cap_margin_bytes"] = PROJECT_CAP_BYTES - allocated - retained_with_margin
        evidence["current_filesystem_available_bytes"] = free
        evidence["free_bytes_above_minimum_resource_gate"] = free - evidence[
            "minimum_filesystem_free_bytes"
        ]
        evidence["status"] = (
            "PASS_WITH_CONSERVATIVE_RECEIPT_MODEL"
            if evidence["project_final_allocated_projection_bytes"] <= PROJECT_CAP_BYTES
            and free >= evidence["minimum_filesystem_free_bytes"]
            else "FAIL_RESOURCE_PROJECTION"
        )
        evidence["created_at_utc"] = datetime.now(timezone.utc).isoformat()
        output_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    final_allocated = executor._allocated_tree_bytes(project_root)
    if final_allocated != evidence["project_allocated_bytes_at_measurement"]:
        evidence["project_allocated_bytes_at_measurement"] = final_allocated
        evidence["project_final_allocated_projection_bytes"] = final_allocated + retained_with_margin
        evidence["project_cap_margin_bytes"] = PROJECT_CAP_BYTES - final_allocated - retained_with_margin
        evidence["status"] = (
            "PASS_WITH_CONSERVATIVE_RECEIPT_MODEL"
            if evidence["project_final_allocated_projection_bytes"] <= PROJECT_CAP_BYTES
            and evidence["current_filesystem_available_bytes"] >= evidence["minimum_filesystem_free_bytes"]
            else "FAIL_RESOURCE_PROJECTION"
        )
        output_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return evidence


def main() -> int:
    project_root = executor.ROOT
    if project_root.parent.name == ".worktrees":
        project_root = project_root.parent.parent
    output = executor.ROOT / "docs/research/phase_r/R22/evidence/v4_phase_h/resource_budget_20261128.json"
    evidence = build_resource_evidence(project_root, output)
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if evidence["status"] == "PASS_WITH_CONSERVATIVE_RECEIPT_MODEL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
