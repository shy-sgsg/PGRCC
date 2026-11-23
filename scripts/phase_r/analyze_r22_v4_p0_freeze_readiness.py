#!/usr/bin/env python3
"""Re-analyze the archived R22-V4 P0 evidence for a freeze-readiness review.

This is an offline analysis only. It never runs Stage2, CUDA, Production, or
the V4 matrix, and it does not extract the large ZIP to disk.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE = Path(
    "outputs/r22_v4_p0_seed1937003_20261123/archives/engineering/"
    "engineering__seed_1937003__vr_2d5__snr_20__delay_4ns__targets_20__periods_10__attempt_001.zip"
)
DEFAULT_PILOT_EVIDENCE = Path(
    "docs/research/phase_r/R22/evidence/v4_native_cuda_pilot_20261123"
)
DEFAULT_HISTORICAL = Path(
    "docs/research/phase_r/R22/V3_3_residual_task_gap_audit/evidence/"
    "parameter_opportunity_audit.csv.gz"
)
DEFAULT_OUTPUT = Path(
    "docs/research/phase_r/R22/evidence/v4_confirmatory_freeze_readiness"
)

EXPECTED_ARCHIVE_BYTES = 13_037_254_578
EXPECTED_ARCHIVE_SHA256 = "0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02"
EXPECTED_ARCHIVE_MANIFEST_SHA256 = "4f96e4f1fb1cf7db1ebe2d1d07c79a8537b47fccac1efa3f4fbd7c7a0e4036a0"
EXPECTED_MASTER_BLOB = "e637e3769bde7814298ff012d0f42301884e2985"

METHODS = (
    "A0",
    "C0",
    "PK",
    "R22_R9_Fixed_D3",
    "M1_CAUSAL_PREFIX",
    "HUBER_D3_CAUSAL_PREFIX",
)
METHOD_LABELS = {
    "A0": "A0 / no-error reference",
    "C0": "C0 / Current +4 ns",
    "PK": "PK / Known correction",
    "R22_R9_Fixed_D3": "Fixed D3",
    "M1_CAUSAL_PREFIX": "M1 causal prefix",
    "HUBER_D3_CAUSAL_PREFIX": "Huber D3 causal prefix",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def csv_write(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def zip_csv(archive: zipfile.ZipFile, member: str) -> list[dict[str, str]]:
    with archive.open(member) as stream:
        text = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
        return list(csv.DictReader(text))


def finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def rmse(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return math.sqrt(sum(value * value for value in values) / len(values))


def pairwise_common_rmse(
    values_a: Mapping[str, float | None], values_b: Mapping[str, float | None],
) -> tuple[list[str], float | None, float | None]:
    common = sorted(
        key for key in set(values_a) & set(values_b)
        if values_a[key] is not None and values_b[key] is not None
    )
    return (
        common,
        rmse([values_a[key] for key in common]),
        rmse([values_b[key] for key in common]),
    )


def quantile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def archive_identity(archive_path: Path, pilot_evidence: Path) -> dict[str, Any]:
    validation_path = pilot_evidence / "archive_validation.json"
    usage_path = pilot_evidence / "resource_usage.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    observed_size = archive_path.stat().st_size
    observed_sha = sha256_file(archive_path)
    if observed_size != EXPECTED_ARCHIVE_BYTES or observed_sha != EXPECTED_ARCHIVE_SHA256:
        raise ValueError(
            "P0 archive identity mismatch: "
            f"size={observed_size}, sha256={observed_sha}"
        )
    if validation.get("archive_size_bytes") != observed_size:
        raise ValueError("archive_validation.json size does not match the P0 ZIP")
    if validation.get("archive_sha256") != observed_sha:
        raise ValueError("archive_validation.json SHA-256 does not match the P0 ZIP")
    if validation.get("archive_crc_verified") is not True:
        raise ValueError("the existing P0 archive CRC record is not verified")
    if validation.get("archive_profile") != "r22_v4_output_complete":
        raise ValueError("unexpected P0 archive profile")
    with zipfile.ZipFile(archive_path) as archive:
        infos = {info.filename: info for info in archive.infolist()}
        manifest_bytes = archive.read("ARCHIVE_MANIFEST.json")
        manifest_sha = sha256_bytes(manifest_bytes)
        if manifest_sha != EXPECTED_ARCHIVE_MANIFEST_SHA256:
            raise ValueError("embedded archive manifest SHA-256 mismatch")
        manifest = json.loads(manifest_bytes)
        required = set(manifest["required_v4_members"])
        missing = sorted(required - set(infos))
        if missing:
            raise ValueError(f"required V4 archive members are missing: {missing[:5]}")
        if len(infos) != 4642 or len(required) != 422:
            raise ValueError(
                f"unexpected archive inventory: entries={len(infos)}, required={len(required)}"
            )
        required_compressed = sum(infos[name].compress_size for name in required)
        required_uncompressed = sum(infos[name].file_size for name in required)
        payload_compressed = sum(
            info.compress_size for name, info in infos.items()
            if name != "ARCHIVE_MANIFEST.json"
        )
        payload_uncompressed = sum(
            info.file_size for name, info in infos.items()
            if name != "ARCHIVE_MANIFEST.json"
        )
    inventory = usage["archive_inventory"]
    archive_relpath = archive_path.resolve().relative_to(ROOT.resolve())
    validation_relpath = validation_path.resolve().relative_to(ROOT.resolve())
    return {
        "archive_path": archive_relpath.as_posix(),
        "archive_size_bytes": observed_size,
        "archive_size_gib": observed_size / 1024**3,
        "archive_sha256": observed_sha,
        "embedded_manifest_sha256": manifest_sha,
        "archive_crc_verified_by_prior_pilot_record": True,
        "archive_crc_record_path": validation_relpath.as_posix(),
        "zip_central_directory_entry_count": 4642,
        "manifest_included_file_count": manifest["included_files"] and len(manifest["included_files"]),
        "manifest_symlink_entry_count": len(manifest["symlink_entries"]),
        "required_v4_member_count": len(required),
        "required_v4_members_missing": 0,
        "required_v4_compressed_bytes": required_compressed,
        "required_v4_uncompressed_bytes": required_uncompressed,
        "all_zip_payload_compressed_bytes": payload_compressed,
        "all_zip_payload_uncompressed_bytes": payload_uncompressed,
        "full_zip_to_required_member_compression_ratio": (
            observed_size / required_compressed if required_compressed else None
        ),
        "inventory_categories": inventory["member_categories"],
        "interpretation": (
            "The current complete P0 ZIP is retained as the sole full run archive. "
            "The 422-member pilot allowlist provides a measured compact evidence-size "
            "reference; it is not yet a per-case guarantee for every H0/target case."
        ),
    }


def current_repo_identity() -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            text=True, capture_output=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "NOT_AVAILABLE"
    try:
        logical_size = int(subprocess.run(
            ["du", "-sb", str(ROOT)], check=True, text=True, capture_output=True,
        ).stdout.split()[0])
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
        logical_size = None
    try:
        allocated_size = int(subprocess.run(
            ["du", "-s", "-B1", str(ROOT)], check=True, text=True, capture_output=True,
        ).stdout.split()[0])
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
        allocated_size = None
    disk = shutil.disk_usage(ROOT)
    return {
        "analysis_base_commit": commit,
        "protected_research_master_expected_blob": EXPECTED_MASTER_BLOB,
        "project_tree_logical_bytes_at_analysis": logical_size,
        "project_tree_logical_decimal_gb_at_analysis": logical_size / 1e9 if logical_size else None,
        "project_tree_allocated_bytes_at_analysis": allocated_size,
        "project_tree_allocated_decimal_gb_at_analysis": allocated_size / 1e9 if allocated_size else None,
        "filesystem_total_bytes": disk.total,
        "filesystem_free_bytes": disk.free,
        "filesystem_free_gib": disk.free / 1024**3,
    }


def analyze_task_metrics(
    archive: zipfile.ZipFile, case_root: str, output: Path,
) -> dict[str, Any]:
    associations = zip_csv(archive, "evaluation/target_associations.csv")
    parameter_rows = zip_csv(archive, "evaluation/parameter_metrics.csv")
    task_rows = zip_csv(archive, "evaluation/task_metrics.csv")
    track_rows = zip_csv(archive, "evaluation/track_level_metrics.csv")

    on_rows = [row for row in associations if row["role"] == "ON"]
    by_method: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in on_rows:
        by_method[row["method_id"]].append(row)

    task_by_method = {
        row["method_id"]: row for row in task_rows
        if row["role"] == "ON" and row["status"] == "EVALUABLE"
    }
    parameter_by_method = {
        (row["method_id"], row["metric"]): row
        for row in parameter_rows if row["role"] == "ON"
    }
    summary_rows: list[dict[str, Any]] = []
    method_data: dict[str, dict[str, Any]] = {}
    parity_rows: list[dict[str, Any]] = []
    for method in METHODS:
        rows = by_method[method]
        opportunities = len(rows)
        matched = [row for row in rows if as_bool(row["matched"])]
        errors = [value for row in matched
                  if (value := finite_float(row.get("position_error_m"))) is not None]
        position_rmse = rmse(errors)
        task = task_by_method[method]
        saved_position = parameter_by_method[(method, "position_m")]
        saved_count = int(saved_position["own_success_count"])
        saved_rmse = finite_float(saved_position["own_success_rmse"])
        parity_rows.append({
            "method_id": method,
            "recomputed_opportunities": opportunities,
            "saved_evaluable_truth_opportunities": int(task["evaluable_truth_opportunities"]),
            "recomputed_matched": len(matched),
            "saved_task_hits": int(task["hits"]),
            "recomputed_valid_position_errors": len(errors),
            "saved_position_count": saved_count,
            "recomputed_position_rmse_m": position_rmse,
            "saved_position_rmse_m": saved_rmse,
            "absolute_rmse_delta_m": abs(position_rmse - saved_rmse)
            if position_rmse is not None and saved_rmse is not None else None,
            "parity_pass": (
                opportunities == int(task["evaluable_truth_opportunities"])
                and len(matched) == int(task["hits"])
                and len(errors) == saved_count
                and saved_rmse is not None and abs(position_rmse - saved_rmse) <= 1e-8
            ),
        })
        method_data[method] = {
            "rows": rows,
            "opportunities": opportunities,
            "matched_rows": matched,
            "coverage": len(matched) / opportunities if opportunities else None,
            "errors": errors,
            "position_rmse_m": position_rmse,
        }
        summary_rows.append({
            "method_id": method,
            "method_label": METHOD_LABELS[method],
            "truth_opportunities": opportunities,
            "matched_hits": len(matched),
            "misses": opportunities - len(matched),
            "coverage_fraction": len(matched) / opportunities if opportunities else None,
            "coverage_percent": 100.0 * len(matched) / opportunities if opportunities else None,
            "own_success_position_count": len(errors),
            "own_success_position_rmse_m": position_rmse,
            "interpretation": "single seed, descriptive only; own-success sets differ",
        })

    if not all(row["parity_pass"] for row in parity_rows):
        raise ValueError("independent target-association recomputation failed saved metric parity")

    pair_rows: list[dict[str, Any]] = []
    for index, method_a in enumerate(METHODS):
        map_a = {
            row["sample_key"]: finite_float(row.get("position_error_m"))
            for row in method_data[method_a]["rows"] if as_bool(row["matched"])
        }
        for method_b in METHODS[index + 1:]:
            map_b = {
                row["sample_key"]: finite_float(row.get("position_error_m"))
                for row in method_data[method_b]["rows"] if as_bool(row["matched"])
            }
            common, rmse_a, rmse_b = pairwise_common_rmse(map_a, map_b)
            pair_rows.append({
                "method_a": method_a,
                "method_b": method_b,
                "common_success_count": len(common),
                "method_a_common_rmse_m": rmse_a,
                "method_b_common_rmse_m": rmse_b,
                "delta_a_minus_b_m": rmse_a - rmse_b if rmse_a is not None and rmse_b is not None else None,
                "method_a_own_coverage_percent": 100 * method_data[method_a]["coverage"],
                "method_b_own_coverage_percent": 100 * method_data[method_b]["coverage"],
                "scope": "same successful target-period opportunities; one seed only",
            })

    coverage_rows = []
    current_coverage = method_data["C0"]["coverage"]
    known_coverage = method_data["PK"]["coverage"]
    recoverable_pp = 100 * (known_coverage - current_coverage)
    for method in METHODS:
        coverage = method_data[method]["coverage"]
        actual_pp = 100 * (coverage - current_coverage)
        coverage_rows.append({
            "method_id": method,
            "coverage_percent": 100 * coverage,
            "current_coverage_percent": 100 * current_coverage,
            "known_coverage_percent": 100 * known_coverage,
            "known_minus_current_recoverable_pp": recoverable_pp,
            "method_minus_current_actual_recovered_pp": actual_pp,
            "descriptive_recovery_ratio": actual_pp / recoverable_pp if recoverable_pp else None,
            "single_seed_only": True,
        })

    period_groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    target_groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in on_rows:
        period_groups[(row["method_id"], row["period_id"])].append(row)
        target_groups[(row["method_id"], row["target_id"])].append(row)
    period_metrics = []
    for method in METHODS:
        for period in sorted({key[1] for key in period_groups if key[0] == method}, key=int):
            rows = period_groups[(method, period)]
            hits = [row for row in rows if as_bool(row["matched"])]
            errors = [value for row in hits
                      if (value := finite_float(row.get("position_error_m"))) is not None]
            period_metrics.append({
                "method_id": method, "period_id": int(period),
                "truth_opportunities": len(rows), "matched_hits": len(hits),
                "coverage_fraction": len(hits) / len(rows) if rows else None,
                "own_success_position_count": len(errors),
                "own_success_position_rmse_m": rmse(errors),
            })
    target_metrics = []
    for method in METHODS:
        for target in sorted({key[1] for key in target_groups if key[0] == method}):
            rows = target_groups[(method, target)]
            hits = [row for row in rows if as_bool(row["matched"])]
            errors = [value for row in hits
                      if (value := finite_float(row.get("position_error_m"))) is not None]
            target_metrics.append({
                "method_id": method, "target_id": target,
                "period_opportunities": len(rows), "matched_periods": len(hits),
                "coverage_fraction": len(hits) / len(rows) if rows else None,
                "own_success_position_count": len(errors),
                "own_success_position_rmse_m": rmse(errors),
            })

    track_summary = [row for row in track_rows if row["role"] == "ON"]
    for item in track_summary:
        item["interpretation"] = (
            "track evaluator diagnostic only; false_confirmed_track_count is not Pfa"
        )

    csv_write(output / "p0_method_summary.csv", list(summary_rows[0]), summary_rows)
    csv_write(output / "p0_recomputed_parity.csv", list(parity_rows[0]), parity_rows)
    csv_write(output / "p0_pairwise_common_position_rmse.csv", list(pair_rows[0]), pair_rows)
    csv_write(output / "p0_coverage_recovery.csv", list(coverage_rows[0]), coverage_rows)
    csv_write(output / "p0_per_period_metrics.csv", list(period_metrics[0]), period_metrics)
    csv_write(output / "p0_per_target_metrics.csv", list(target_metrics[0]), target_metrics)
    csv_write(output / "p0_track_summary.csv", list(track_summary[0]), track_summary)

    return {
        "method_summary": summary_rows,
        "recomputed_parity": parity_rows,
        "pairwise_common_position_rmse": pair_rows,
        "coverage_recovery": coverage_rows,
        "per_period_row_count": len(period_metrics),
        "per_target_row_count": len(target_metrics),
        "track_summary": track_summary,
    }


def result_period(result_id: str) -> int:
    digits = "".join(character for character in str(result_id) if character.isdigit())
    if not digits:
        raise ValueError(f"invalid TrackManager result_id: {result_id!r}")
    return int(digits) - 1


def observed_period_handoffs(period_track_ids: Mapping[int, set[str]]) -> int:
    """Count disjoint track transitions across consecutive observed periods."""
    periods = sorted(period_track_ids)
    return sum(
        period_track_ids[left].isdisjoint(period_track_ids[right])
        for left, right in zip(periods, periods[1:])
    )


def _numeric_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return str(int(text))


def audit_track_payloads(
    archive: zipfile.ZipFile,
    associations: Sequence[Mapping[str, str]],
    output: Path,
) -> list[dict[str, Any]]:
    names = archive.namelist()
    branch_dirs = sorted({
        name.split("/production/", 1)[1].split("/", 1)[0]
        for name in names if "/production/" in name and name.endswith("/branch_manifest.json")
    })
    on_associations: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in associations:
        if row["role"] == "ON":
            on_associations[row["method_id"]].append(row)
    metric_members = [name for name in names if name == "evaluation/track_level_metrics.csv"]
    if len(metric_members) != 1:
        raise ValueError("expected exactly one evaluation/track_level_metrics.csv")
    metric_rows = zip_csv(archive, metric_members[0])
    metrics_by_key = {(row["method_id"], row["role"]): row for row in metric_rows}
    audit_rows: list[dict[str, Any]] = []
    lineage_rows: list[dict[str, Any]] = []
    for branch in branch_dirs:
        prefix = next(
            name.split("/production/", 1)[0] + "/production/" + branch + "/"
            for name in names if "/production/" in name
            and name.endswith("/production/" + branch + "/branch_manifest.json")
        )
        state_matches = [name for name in names if name.startswith(prefix + "track_debug_runs/")
                         and name.endswith("/track_states.csv")]
        payload_matches = [name for name in names if name.startswith(prefix + "track_debug_runs/")
                           and name.endswith("/track_output_payloads.csv")]
        detection_debug_matches = [name for name in names if name.startswith(prefix + "track_debug_runs/")
                                   and name.endswith("/track_detections.csv")]
        production_detection_members = sorted(
            name for name in names
            if name.startswith(prefix + "result/")
            and "/detection_results_GMTI" in name
            and name.endswith(".csv")
        )
        if (len(state_matches) != 1 or len(payload_matches) != 1
                or len(detection_debug_matches) != 1 or len(production_detection_members) != 10):
            raise ValueError(f"expected one state/payload/debug-detection CSV and ten Production detections for {branch}")
        states = zip_csv(archive, state_matches[0])
        payloads = zip_csv(archive, payload_matches[0])
        debug_detections = zip_csv(archive, detection_debug_matches[0])
        production_detections: dict[tuple[int, str], Mapping[str, str]] = {}
        for member in production_detection_members:
            for row in zip_csv(archive, member):
                result_number = result_period(row["result_id"]) + 1
                det_id = _numeric_key(row.get("det_id", ""))
                key = (result_number, det_id)
                if not det_id or key in production_detections:
                    raise ValueError(f"duplicate or missing Production detection key in {branch}: {key}")
                if int(row["period_id"]) != result_number - 1:
                    raise ValueError(f"Production result/period mismatch in {branch}: {key}")
                production_detections[key] = row
        debug_detection_by_key: dict[tuple[int, str], Mapping[str, str]] = {}
        for row in debug_detections:
            key = (result_period(row["result_id"]) + 1, _numeric_key(row.get("det_index", "")))
            if not key[1] or key in debug_detection_by_key:
                raise ValueError(f"duplicate or missing TrackManager detection key in {branch}: {key}")
            debug_detection_by_key[key] = row
        for key, row in debug_detection_by_key.items():
            production_row = production_detections.get(key)
            if production_row is None:
                raise ValueError(f"TrackManager detection has no Production detection in {branch}: {key}")
            if int(production_row["period_id"]) != result_period(row["result_id"]):
                raise ValueError(f"TrackManager/Production detection period mismatch in {branch}: {key}")

        state_by_key: dict[tuple[int, str, str], list[Mapping[str, str]]] = defaultdict(list)
        states_by_result: dict[int, list[Mapping[str, str]]] = defaultdict(list)
        confirmed_observations = 0
        for row in states:
            result_number = result_period(row["result_id"]) + 1
            track_id = row["track_id"]
            det_id = _numeric_key(row.get("matched_det_index", ""))
            matched = as_bool(row.get("matched_this_frame", ""))
            confirmed = row.get("state") == "Confirmed"
            state_by_key[(result_number, track_id, det_id)].append(row)
            states_by_result[result_number].append(row)
            if confirmed:
                confirmed_observations += 1

        violations = 0
        unmatched_payload_rows = 0
        missing_debug_detection = 0
        missing_production_detection = 0
        source_violations = 0
        measurement_provenance_violations = 0
        valid_payload_keys: set[tuple[int, str, str]] = set()
        payload_detection_keys: dict[tuple[int, str, str], tuple[int, int, str]] = {}
        for row in payloads:
            result_number = result_period(row["result_id"]) + 1
            det_id = _numeric_key(row.get("matched_det_index", ""))
            key = (result_number, row["track_id"], det_id)
            state_rows = state_by_key.get(key, [])
            valid_state = any(
                state.get("state") == "Confirmed"
                and as_bool(state.get("matched_this_frame", ""))
                and as_bool(state.get("is_output", ""))
                for state in state_rows
            )
            if not det_id:
                unmatched_payload_rows += 1
            debug_detection = debug_detection_by_key.get((result_number, det_id)) if det_id else None
            production_detection = production_detections.get((result_number, det_id)) if det_id else None
            if debug_detection is None:
                missing_debug_detection += 1
            if production_detection is None:
                missing_production_detection += 1
            debug_track_match = bool(debug_detection) and as_bool(debug_detection.get("matched", "")) \
                and debug_detection.get("matched_track_id") == row["track_id"]
            result_period_consistent = bool(production_detection) and \
                int(production_detection["period_id"]) == result_number - 1
            valid = valid_state and debug_track_match and result_period_consistent
            if not valid:
                violations += 1
            if row.get("position_source") != "matched_detection" or row.get("time_source") != "matched_detection":
                source_violations += 1
            if debug_detection is not None:
                values = (
                    (row.get("measurement_e"), debug_detection.get("e"), 1.0e-3),
                    (row.get("measurement_n"), debug_detection.get("n"), 1.0e-3),
                    (row.get("output_e"), debug_detection.get("e"), 1.0e-3),
                    (row.get("output_n"), debug_detection.get("n"), 1.0e-3),
                    (row.get("measurement_utc"), debug_detection.get("utc"), 1.0e-6),
                    (row.get("measurement_range"), debug_detection.get("range"), 1.0e-6),
                )
                if any(
                    finite_float(actual) is None or finite_float(expected) is None
                    or abs(finite_float(actual) - finite_float(expected)) > tolerance
                    for actual, expected, tolerance in values
                ):
                    measurement_provenance_violations += 1
            else:
                measurement_provenance_violations += 1
            if valid:
                valid_payload_keys.add(key)
                payload_detection_keys[key] = (
                    result_number - 1,
                    int(production_detection["beam_id"]),
                    det_id,
                )

        method = branch.rsplit("_", 1)[0]
        role = branch.rsplit("_", 1)[-1]
        if method.endswith("_not_applicable"):
            method = method.removesuffix("_not_applicable")
        truth_matched_associations = [
            row for row in on_associations.get(method, [])
            if as_bool(row["matched"])
        ] if role == "ON" else []
        target_id_by_detection: dict[tuple[int, int, str], str] = {}
        target_matched_detection_keys: set[tuple[int, int, str]] = set()
        for row in truth_matched_associations:
            key = (int(row["period_id"]), int(row["beam_id"]), _numeric_key(row.get("det_id", "")))
            target_matched_detection_keys.add(key)
            target_id_by_detection[key] = row["target_id"]
        target_tracks_by_period: dict[int, set[str]] = defaultdict(set)
        target_tracks_by_target_period: dict[str, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
        confirmed_detection_to_tracks: dict[tuple[int, int, str], set[str]] = defaultdict(set)
        for key in valid_payload_keys:
            result_number, track_id, det_id = key
            state_rows = state_by_key[key]
            state = next((candidate for candidate in state_rows
                          if candidate.get("state") == "Confirmed"
                          and as_bool(candidate.get("matched_this_frame", ""))
                          and as_bool(candidate.get("is_output", ""))), None)
            detection_key = payload_detection_keys[key]
            if state is not None:
                confirmed_detection_to_tracks[detection_key].add(track_id)
        for detection_key, target_id in target_id_by_detection.items():
            for track_id in confirmed_detection_to_tracks.get(detection_key, set()):
                period = detection_key[0]
                target_tracks_by_period[period].add(track_id)
                target_tracks_by_target_period[target_id][period].add(track_id)

        unmapped_target_matches = 0
        for row in truth_matched_associations:
            detection_key = (int(row["period_id"]), int(row["beam_id"]), _numeric_key(row.get("det_id", "")))
            tracks = confirmed_detection_to_tracks.get(detection_key, set())
            if not tracks:
                unmapped_target_matches += 1
        target_period_ids = sorted(target_tracks_by_period)
        target_track_ids = {
            target: {track for tracks in periods.values() for track in tracks}
            for target, periods in target_tracks_by_target_period.items()
        }
        per_target_handoffs = {
            target: observed_period_handoffs(periods)
            for target, periods in target_tracks_by_target_period.items()
        }
        global_handoffs = observed_period_handoffs(target_tracks_by_period)
        target_track_union = {track for tracks in target_tracks_by_period.values() for track in tracks}
        confirmed_payloads = [
            row for row in payloads
            if (result_period(row["result_id"]) + 1,
                row["track_id"], _numeric_key(row.get("matched_det_index", ""))) in valid_payload_keys
        ]
        unmatched_confirmed_payloads = [
            row for row in confirmed_payloads
            if payload_detection_keys[(result_period(row["result_id"]) + 1,
                                       row["track_id"], _numeric_key(row.get("matched_det_index", "")))]
            not in target_matched_detection_keys
        ]
        unmatched_payload_track_ids = {
            row["track_id"] for row in unmatched_confirmed_payloads
        }
        false_confirmed_track_ids = {
            row["track_id"] for row in confirmed_payloads
        } - target_track_union
        first_target_match_period = {
            track: min(period for period, tracks in target_tracks_by_period.items() if track in tracks)
            for track in target_track_union
        }
        unmatched_after_target_match = sum(
            row["track_id"] in first_target_match_period
            and result_period(row["result_id"]) >= first_target_match_period[row["track_id"]]
            for row in unmatched_confirmed_payloads
        )
        mixed_target_matched_and_unmatched = unmatched_payload_track_ids & target_track_union
        target_confirmed_observations = [
            row for row in states if row.get("state") == "Confirmed" and row["track_id"] in target_track_union
        ]
        period_track_counts = [len(tracks) for tracks in target_tracks_by_period.values()]

        expected = metrics_by_key.get((method, role))
        if expected is None and role == "ON":
            raise ValueError(f"missing archived track summary for {method}/{role}")
        expected = expected or {}
        computed_lineage = []
        for target, periods in sorted(target_tracks_by_target_period.items()):
            track_ids = sorted({track for tracks in periods.values() for track in tracks})
            first_confirmation = min(
                (result_period(state["result_id"])
                 for state in states if state.get("state") == "Confirmed"
                 and state.get("track_id") in track_ids), default=None,
            )
            computed_lineage.append({
                "target_id": target,
                "first_confirmation_period": first_confirmation,
                "matched_period_ids": sorted(periods),
                "track_ids": track_ids,
                "sequential_track_handoffs": per_target_handoffs[target],
            })
        expected_lineage = json.loads(expected.get("target_lineage_by_target") or "[]")
        normalize_lineage = lambda rows: sorted(
            ({
                "target_id": row["target_id"],
                "first_confirmation_period": row.get("first_confirmation_period"),
                "matched_period_ids": sorted(row.get("matched_period_ids", [])),
                "track_ids": sorted(row.get("track_ids", [])),
                "sequential_track_handoffs": int(row.get("sequential_track_handoffs", 0)),
            } for row in rows),
            key=lambda row: row["target_id"],
        )
        parity_fields = {
            "confirmed_observation_count": confirmed_observations,
            "confirmed_unique_period_count": len({result_period(row["result_id"]) for row in states
                                                   if row.get("state") == "Confirmed"}),
            "target_confirmed_observation_count": len(target_confirmed_observations),
            "target_confirmed_unique_period_count": len({result_period(row["result_id"])
                                                          for row in target_confirmed_observations}),
            "target_matched_periods": len(target_period_ids),
            "unique_target_track_count": len(target_track_union),
            "observed_sequential_target_track_handoffs": global_handoffs,
            "track_fragmentation_count_observed": global_handoffs,
            "false_confirmed_track_count": len(false_confirmed_track_ids),
            "false_confirmed_track_ids_without_any_target_match": len(false_confirmed_track_ids),
            "unique_track_ids_with_any_unmatched_payload": len(unmatched_payload_track_ids),
            "legacy_v2_any_unmatched_track_count": len(unmatched_payload_track_ids),
            "unmatched_payload_after_target_match_count": unmatched_after_target_match,
            "mixed_target_matched_and_unmatched_track_count": len(mixed_target_matched_and_unmatched),
            "confirmed_payload_count": len(confirmed_payloads),
            "unmatched_confirmed_payload_count": len(unmatched_confirmed_payloads),
            "maximum_simultaneous_target_track_count": max(period_track_counts, default=0),
            "periods_with_simultaneous_target_tracks": sum(count > 1 for count in period_track_counts),
        }
        parity_failures = {
            field: {"computed": value, "archived": int(expected[field])}
            for field, value in parity_fields.items()
            if field in expected and value != int(expected[field])
        }
        expected_target_periods = json.loads(expected.get("target_matched_period_ids") or "[]")
        if target_period_ids != expected_target_periods:
            parity_failures["target_matched_period_ids"] = {
                "computed": target_period_ids, "archived": expected_target_periods,
            }
        if expected and normalize_lineage(computed_lineage) != normalize_lineage(expected_lineage):
            parity_failures["target_lineage_by_target"] = {
                "computed": normalize_lineage(computed_lineage),
                "archived": normalize_lineage(expected_lineage),
            }

        audit_rows.append({
            "branch_name": branch,
            "method_id": method,
            "role": role,
            "track_state_rows": len(states),
            "confirmed_observation_count_recomputed": confirmed_observations,
            "payload_rows": len(payloads),
            "payloads_trace_to_same_period_confirmed_matched_output": len(payloads) - violations,
            "payload_lineage_violations": violations,
            "payloads_without_matched_det_index": unmatched_payload_rows,
            "payloads_missing_track_detection": missing_debug_detection,
            "payloads_missing_production_detection": missing_production_detection,
            "payload_source_violations": source_violations,
            "payload_measurement_provenance_violations": measurement_provenance_violations,
            "target_matched_associations": len(truth_matched_associations),
            "target_matches_without_confirmed_output_track": unmapped_target_matches,
            "targets_with_confirmed_track_lineage": len(target_track_ids),
            "targets_with_multiple_track_ids": sum(len(v) > 1 for v in target_track_ids.values()),
            "false_confirmed_track_count_recomputed": len(false_confirmed_track_ids),
            "per_target_handoffs_recomputed": sum(per_target_handoffs.values()),
            "global_observed_period_handoffs_recomputed": global_handoffs,
            "archived_global_handoffs": expected.get("observed_sequential_target_track_handoffs", ""),
            "track_ids_with_any_unmatched_payload": len(unmatched_payload_track_ids),
            "unmatched_payload_after_target_match_count": unmatched_after_target_match,
            "mixed_target_matched_and_unmatched_track_count": len(mixed_target_matched_and_unmatched),
            "track_summary_parity": not parity_failures if expected else "NOT_AVAILABLE",
            "track_summary_parity_failures": json.dumps(parity_failures, ensure_ascii=False, sort_keys=True),
            "interpretation": "track lineage diagnostic; not a false-alarm probability",
        })
        if (violations or source_violations or measurement_provenance_violations
                or (role == "ON" and parity_failures)):
            raise ValueError(
                f"TrackManager provenance/parity failed in {branch}: payload={violations}, "
                f"source={source_violations}, measurement={measurement_provenance_violations}, "
                f"summary={parity_failures}"
            )
        if role == "ON":
            for target, track_ids in target_track_ids.items():
                periods = target_tracks_by_target_period[target]
                lineage_rows.append({
                    "branch_name": branch,
                    "method_id": method,
                    "target_id": target,
                    "matched_period_count_with_confirmed_lineage": len(periods),
                    "unique_confirmed_track_ids": len(track_ids),
                    "sequential_observed_period_handoffs": per_target_handoffs[target],
                    "track_ids": ";".join(sorted(track_ids)),
                })

    csv_write(output / "p0_track_payload_lineage_audit.csv", list(audit_rows[0]), audit_rows)
    if lineage_rows:
        csv_write(output / "p0_target_track_lineage.csv", list(lineage_rows[0]), lineage_rows)
    return audit_rows


def analyze_cfar_geometry(archive: zipfile.ZipFile, output: Path) -> list[dict[str, Any]]:
    names = archive.namelist()
    members = sorted(name for name in names if name.endswith("/result/debug/cfar_geometry_diagnostics.csv"))
    rows_out: list[dict[str, Any]] = []
    for member in members:
        rows = zip_csv(archive, member)
        branch = member.split("/production/", 1)[1].split("/", 1)[0]
        full_band = [row for row in rows if row["branch"] == "union_full_band"]
        dynamic = [row for row in rows if row["branch"] == "union_dynamic_in_band"]
        if len(full_band) != 10 or len(dynamic) != 10:
            raise ValueError(f"CFAR geometry rows are incomplete in {branch}")
        valid_counts = {int(row["valid_cut_count"]) for row in full_band}
        threshold_counts = {int(row["threshold_test_count"]) for row in full_band}
        if valid_counts != {527_280} or threshold_counts != {527_280}:
            raise ValueError(f"CFAR valid CUT denominator differs in {branch}: {valid_counts}")
        rows_out.append({
            "branch_name": branch,
            "geometry_rows": len(rows),
            "full_band_periods": len(full_band),
            "full_band_valid_cut_per_period": 527_280,
            "full_band_valid_cut_all_periods": sum(int(row["valid_cut_count"]) for row in full_band),
            "dynamic_band_periods": len(dynamic),
            "all_hit_cut_count_full_band": sum(int(row["hit_cut_count"]) for row in full_band),
            "configured_pfa": full_band[0]["configured_pfa"],
            "empirical_pfa_status": "NOT_EVALUABLE_WITHOUT_INDEPENDENT_H0_TRUTH_JOIN",
            "interpretation": "valid CUTs and threshold tests are denominator counts, not independent replicates",
        })
    csv_write(output / "p0_cfar_geometry_summary.csv", list(rows_out[0]), rows_out)
    return rows_out


def analyze_target_response(archive: zipfile.ZipFile, case_root: str, output: Path) -> list[dict[str, Any]]:
    suffix = "/r22_v4_output_evidence/target_complex_response.csv"
    candidates = [name for name in archive.namelist() if name.endswith(suffix)]
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one target complex response CSV, got {len(candidates)}")
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    with archive.open(candidates[0]) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        for row in csv.DictReader(text):
            if row["role"] == "TO":
                grouped[(row["method_id"], row["target_id"], row["period_id"])].append(row)
    baseline = {
        (target, period): rows for (method, target, period), rows in grouped.items()
        if method == "A0"
    }
    metrics: dict[str, dict[str, list[float]]] = {
        method: defaultdict(list) for method in METHODS
    }
    paired_cells = Counter()
    for method in METHODS:
        for (candidate_method, target, period), rows in grouped.items():
            if candidate_method != method or (target, period) not in baseline:
                continue
            reference_rows = baseline[(target, period)]
            key = lambda item: (item["doppler_offset"], item["range_offset"])
            candidate_map = {key(row): row for row in rows}
            reference_map = {key(row): row for row in reference_rows}
            common = sorted(set(candidate_map) & set(reference_map))
            if len(common) != 25:
                raise ValueError(f"TO ROI cell mismatch for {method}/{target}/{period}: {len(common)}")
            candidate_power = 0.0
            reference_power = 0.0
            dot_real = 0.0
            dot_imag = 0.0
            for cell in common:
                candidate = candidate_map[cell]
                reference = reference_map[cell]
                cr = finite_float(candidate["csi_after_real"])
                ci = finite_float(candidate["csi_after_imag"])
                rr = finite_float(reference["csi_after_real"])
                ri = finite_float(reference["csi_after_imag"])
                cp = finite_float(candidate["csi_after_power"])
                rp = finite_float(reference["csi_after_power"])
                if None in (cr, ci, rr, ri, cp, rp):
                    raise ValueError("non-finite target response value")
                candidate_power += cp
                reference_power += rp
                dot_real += cr * rr + ci * ri
                dot_imag += ci * rr - cr * ri
            if reference_power <= 0 or candidate_power < 0:
                raise ValueError("target response ROI has invalid power")
            gain_real = dot_real / reference_power
            gain_imag = dot_imag / reference_power
            gain_magnitude = math.hypot(gain_real, gain_imag)
            gain_phase_deg = math.degrees(math.atan2(gain_imag, gain_real))
            coherence = math.hypot(dot_real, dot_imag) / math.sqrt(candidate_power * reference_power) if candidate_power else 0.0
            ratio = candidate_power / reference_power
            metrics[method]["power_ratio"].append(ratio)
            metrics[method]["gain_magnitude"].append(gain_magnitude)
            metrics[method]["gain_phase_deg"].append(gain_phase_deg)
            metrics[method]["abs_gain_phase_deg"].append(abs(gain_phase_deg))
            metrics[method]["complex_coherence"].append(coherence)
            paired_cells[method] += 25
    result = []
    for method in METHODS:
        value = metrics[method]
        result.append({
            "method_id": method,
            "role": "TO",
            "processing_tap": "CSI_AFTER",
            "paired_target_period_roi_count": len(value["power_ratio"]),
            "paired_roi_cell_count": paired_cells[method],
            "power_ratio_mean": statistics.fmean(value["power_ratio"]) if value["power_ratio"] else None,
            "power_ratio_median": statistics.median(value["power_ratio"]) if value["power_ratio"] else None,
            "power_ratio_p10": quantile(value["power_ratio"], 0.10),
            "power_ratio_p90": quantile(value["power_ratio"], 0.90),
            "complex_gain_magnitude_mean": statistics.fmean(value["gain_magnitude"]) if value["gain_magnitude"] else None,
            "complex_gain_magnitude_median": statistics.median(value["gain_magnitude"]) if value["gain_magnitude"] else None,
            "wrapped_phase_difference_median_deg": statistics.median(value["gain_phase_deg"]) if value["gain_phase_deg"] else None,
            "wrapped_phase_difference_abs_median_deg": statistics.median(value["abs_gain_phase_deg"]) if value["abs_gain_phase_deg"] else None,
            "wrapped_phase_difference_abs_p90_deg": quantile(value["abs_gain_phase_deg"], 0.90),
            "complex_coherence_median": statistics.median(value["complex_coherence"]) if value["complex_coherence"] else None,
            "interpretation": "single-seed fixed-ROI response diagnostic; not Pd or a population-level preservation claim",
        })
    csv_write(output / "p0_target_response_summary.csv", list(result[0]), result)
    return result


def analyze_historical_v33(historical_path: Path, output: Path) -> dict[str, Any]:
    wanted_speeds = {0.5, 2.5, 6.7}
    method_ids = {
        "M1": "R22_M1_Physics_Matched",
        "Fixed": "R22_R9_Fixed_D3",
        "Huber": "R22_D3_Huber_B_ON",
        "Known": "PK",
    }
    data: dict[tuple[str, float, str], dict[str, float]] = defaultdict(dict)
    coverage: dict[tuple[str, float, str], list[int]] = defaultdict(list)
    with gzip.open(historical_path, "rt", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if (float(row["delay_ns"]) != 4.0 or float(row["snr_db"]) != 20.0
                    or float(row["speed_mps"]) not in wanted_speeds
                    or row["method_id"] not in method_ids.values()):
                continue
            method = next(key for key, value in method_ids.items() if value == row["method_id"])
            key = (row["seed"], float(row["speed_mps"]), method)
            coverage[key].append(1 if as_bool(row["matched"]) else 0)
            if as_bool(row["matched"]):
                error = finite_float(row.get("position_m_all_periods_association"))
                if error is not None:
                    sample = row["sample_key"]
                    if sample in data[key]:
                        raise ValueError(f"duplicate historical sample key: {key}/{sample}")
                    data[key][sample] = error
    seeds = sorted({key[0] for key in coverage}, key=int)
    if len(seeds) != 12:
        raise ValueError(f"expected 12 V3.3 historical seeds, found {len(seeds)}")
    pairings = (("M1", "Fixed"), ("M1", "Huber"), ("M1", "Known"))
    per_seed: list[dict[str, Any]] = []
    for seed in seeds:
        row: dict[str, Any] = {"seed": seed}
        speed_deltas: dict[tuple[str, str], list[float]] = defaultdict(list)
        coverage_deltas: dict[tuple[str, str], list[float]] = defaultdict(list)
        for speed in sorted(wanted_speeds):
            for method in method_ids:
                denom = len(coverage[(seed, speed, method)])
                if denom != 200:
                    raise ValueError(f"historical opportunity count is not 200: {seed}/{speed}/{method}/{denom}")
            for method_a, method_b in pairings:
                a = data[(seed, speed, method_a)]
                b = data[(seed, speed, method_b)]
                common = sorted(set(a) & set(b))
                if not common:
                    raise ValueError(f"no historical common successes for {seed}/{speed}/{method_a}/{method_b}")
                delta = rmse([a[key] for key in common]) - rmse([b[key] for key in common])
                speed_deltas[(method_a, method_b)].append(delta)
                hits_a = sum(coverage[(seed, speed, method_a)])
                hits_b = sum(coverage[(seed, speed, method_b)])
                coverage_deltas[(method_a, method_b)].append((hits_a - hits_b) / 200.0 * 100.0)
        for pair in pairings:
            suffix = f"{pair[0]}_minus_{pair[1]}"
            row[f"position_delta_{suffix}_m"] = statistics.fmean(speed_deltas[pair])
            row[f"coverage_delta_{suffix}_pp"] = statistics.fmean(coverage_deltas[pair])
        per_seed.append(row)
    csv_write(output / "historical_v33_seed_level_sensitivity.csv", list(per_seed[0]), per_seed)

    summary = []
    for a, b in pairings:
        suffix = f"{a}_minus_{b}"
        deltas = [row[f"position_delta_{suffix}_m"] for row in per_seed]
        cover = [row[f"coverage_delta_{suffix}_pp"] for row in per_seed]
        summary.append({
            "comparison": suffix,
            "independent_seed_count": len(deltas),
            "position_delta_mean_m": statistics.fmean(deltas),
            "position_delta_seed_sd_m": statistics.stdev(deltas),
            "position_delta_same_direction_seed_count": sum(value < 0 for value in deltas),
            "coverage_delta_mean_pp": statistics.fmean(cover),
            "coverage_delta_seed_sd_pp": statistics.stdev(cover),
            "evidence_scope": "V3.3 full-case batch screening; sensitivity input only, not V4 causal-prefix evidence",
        })
    csv_write(output / "historical_v33_sensitivity_summary.csv", list(summary[0]), summary)
    return {"per_seed": per_seed, "summary": summary}


def power_sensitivity(historical: Mapping[str, Any], output: Path) -> dict[str, Any]:
    from scipy.stats import nct, t

    by_comparison = {row["comparison"]: row for row in historical["summary"]}
    scenarios = (
        ("M1_minus_Fixed", by_comparison["M1_minus_Fixed"]["position_delta_seed_sd_m"], "position_m"),
        ("M1_minus_Huber", by_comparison["M1_minus_Huber"]["position_delta_seed_sd_m"], "position_m"),
        ("M1_minus_Known", by_comparison["M1_minus_Known"]["position_delta_seed_sd_m"], "position_m"),
        ("coverage_M1_minus_Fixed", by_comparison["M1_minus_Fixed"]["coverage_delta_seed_sd_pp"], "coverage_pp"),
        ("coverage_M1_minus_Huber", by_comparison["M1_minus_Huber"]["coverage_delta_seed_sd_pp"], "coverage_pp"),
    )
    output_rows: list[dict[str, Any]] = []
    mdes = (0.25, 0.5, 1.0, 2.5, 3.0, 5.0, 6.0, 10.0, 12.5)
    sample_sizes = (12, 20, 30, 40, 60)
    for comparison, sd, unit in scenarios:
        if sd <= 0:
            continue
        for n in sample_sizes:
            df = n - 1
            if unit == "position_m":
                critical = float(t.ppf(0.975, df))
                for mde in mdes:
                    nc = mde * math.sqrt(n) / sd
                    power = float(nct.cdf(-critical, df, -nc) + nct.sf(critical, df, -nc))
                    output_rows.append({
                        "comparison": comparison, "unit": unit, "independent_seeds": n,
                        "assumed_true_effect_abs": mde, "assumed_seed_sd": sd,
                        "alpha": 0.05, "test": "two-sided paired t noncentral-t sensitivity",
                        "power": power,
                    })
            else:
                critical = float(t.ppf(0.975, df))
                for margin in mdes:
                    nc = margin * math.sqrt(n) / sd
                    power = float(nct.sf(critical, df, nc))
                    output_rows.append({
                        "comparison": comparison, "unit": unit, "independent_seeds": n,
                        "assumed_true_effect_abs": 0.0, "assumed_seed_sd": sd,
                        "noninferiority_margin": margin, "alpha": 0.025,
                        "test": "one-sided paired t noncentral-t sensitivity, true difference zero",
                        "power": power,
                    })
    csv_write(output / "power_precision_sensitivity.csv", list(output_rows[0]), output_rows)

    def mde_for_80(sd: float, n: int) -> float:
        from scipy.optimize import brentq

        critical = float(t.ppf(0.975, n - 1))
        def power_at(effect: float) -> float:
            nc = effect * math.sqrt(n) / sd
            return float(nct.cdf(-critical, n - 1, -nc) + nct.sf(critical, n - 1, -nc))
        return float(brentq(lambda effect: power_at(effect) - 0.80, 1e-9, max(100.0, 100 * sd)))

    def ni_margin_for_80(sd: float, n: int) -> float:
        from scipy.optimize import brentq

        critical = float(t.ppf(0.975, n - 1))
        def power_at(margin: float) -> float:
            nc = margin * math.sqrt(n) / sd
            return float(nct.sf(critical, n - 1, nc))
        return float(brentq(lambda margin: power_at(margin) - 0.80, 1e-9, max(100.0, 100 * sd)))

    mde_rows = []
    for comparison, sd, unit in scenarios[:3]:
        for n in sample_sizes:
            mde_rows.append({
                "comparison": comparison,
                "independent_seeds": n,
                "historical_seed_sd_m": sd,
                "two_sided_alpha": 0.05,
                "80_percent_power_mde_m": mde_for_80(sd, n),
                "assumption": "normal paired seed-level differences; V3.3 batch SD transports to V4 only as sensitivity",
            })
    csv_write(output / "position_mde_precision_by_n.csv", list(mde_rows[0]), mde_rows)
    by_comp = {row["comparison"]: row for row in historical["summary"]}
    n = 30
    position_sd = by_comp["M1_minus_Fixed"]["position_delta_seed_sd_m"]
    coverage_sd = by_comp["M1_minus_Fixed"]["coverage_delta_seed_sd_pp"]
    p0_any_event = 1.0 - 0.05 ** (1.0 / 30.0)
    return {
        "method": "paired t noncentral-t sensitivity; not a power guarantee",
        "independent_unit": "seed",
        "seed_count": n,
        "primary_m1_minus_fixed": {
            "historical_seed_sd_m": position_sd,
            "standard_error_m": position_sd / math.sqrt(n),
            "two_sided_95pct_t_interval_half_width_m": float(t.ppf(0.975, n - 1)) * position_sd / math.sqrt(n),
            "80pct_power_mde_m": mde_for_80(position_sd, n),
            "power_if_true_improvement_3m": float(
                nct.cdf(-t.ppf(0.975, n - 1), n - 1, -3.0 * math.sqrt(n) / position_sd)
                + nct.sf(t.ppf(0.975, n - 1), n - 1, -3.0 * math.sqrt(n) / position_sd)
            ),
        },
        "coverage_m1_minus_fixed": {
            "historical_seed_sd_pp": coverage_sd,
            "standard_error_pp": coverage_sd / math.sqrt(n),
            "two_sided_95pct_t_interval_half_width_pp": float(t.ppf(0.975, n - 1)) * coverage_sd / math.sqrt(n),
            "80pct_one_sided_alpha_0_025_noninferiority_margin_pp_when_true_difference_zero": ni_margin_for_80(coverage_sd, n),
        },
        "zero_events_across_30_independent_seeds_one_sided_95pct_upper_bound_on_seed_any_event_probability": p0_any_event,
        "limitations": [
            "V3.3 estimates used full-case batch estimators; V4 uses causal prefixes.",
            "Only 12 historical seeds estimate each SD; variance transport is uncertain.",
            "The MDE and noninferiority margin remain engineering/user decisions.",
            "No targets, periods, CUTs, branches, or detections are treated as independent seeds.",
        ],
    }


def resource_model(
    archive_record: Mapping[str, Any], pilot_evidence: Path, output: Path,
) -> dict[str, Any]:
    usage = json.loads((pilot_evidence / "resource_usage.json").read_text(encoding="utf-8"))
    archive_size = int(archive_record["archive_size_bytes"])
    required_bytes = int(archive_record["required_v4_compressed_bytes"])
    compact_240 = required_bytes * 240
    compact_with_25pct = math.ceil(compact_240 * 1.25)
    production_calls = 2520
    stage2_min = 1200
    stage2_max = 1440
    stage2 = usage["archive_inventory"]["member_categories"]["stage2_scene_bins"]
    stage2_per_call = stage2["compressed_bytes"] / 6.0
    production_seconds_per_call = usage["production_elapsed_seconds_sum"] / usage["production_branch_count"]
    stage2_seconds_per_call = usage["stage2_elapsed_seconds"] / usage["stage2_run_count"]
    residual_case_seconds = max(
        0.0,
        usage["case_wall_seconds"]
        - usage["stage2_elapsed_seconds"]
        - usage["production_elapsed_seconds_sum"],
    )
    free_start = int(usage["disk_start_free_bytes"])
    free_min = int(usage["disk_minimum_available_bytes"])
    peak_consumption = max(0, free_start - free_min)
    ten_gib = 10 * 1024**3
    full_pilot_archives_240 = archive_size * 240
    full_plus4_archives_120 = archive_size * 120
    current_identity = current_repo_identity()
    current_tree = current_identity["project_tree_logical_bytes_at_analysis"]
    current_allocated = current_identity["project_tree_allocated_bytes_at_analysis"]
    projected_final_tree = (
        current_tree - archive_size + compact_with_25pct
        if current_tree is not None and current_tree >= archive_size else None
    )
    payload_categories = usage["archive_inventory"]["member_categories"]
    compact_rows = [
        {"category": category,
         "pilot_compressed_bytes": values["compressed_bytes"],
         "pilot_uncompressed_bytes": values["uncompressed_bytes"],
         "pilot_entries": values["entries"],
         "role_in_freeze_model": (
             "retained in compact V4 archive" if category in {
                 "production_detection_csv", "production_track_debug", "production_other", "evaluation"
             } else "raw/reconstructable diagnostic volume; do not linearly retain in every final archive"
         )}
        for category, values in payload_categories.items()
    ]
    csv_write(output / "p0_resource_categories.csv", list(compact_rows[0]), compact_rows)
    record = {
        "source_design_status": "DRAFT_NOT_FROZEN",
        "case_plan": {
            "target_present_cases": 180,
            "h0_cases": 60,
            "total_case_identities": 240,
            "registered_science_slots": 1470,
            "registered_production_slots": 1260,
            "offline_estimator_diagnostic_slots": 210,
            "internal_auxiliary_production_calls": 1260,
            "actual_production_calls": production_calls,
            "stage2_calls_minimum": stage2_min,
            "stage2_calls_maximum": stage2_max,
            "source": "scripts/phase_r/r22_v4_execution_plan.py and its CPU tests",
            "execution_authorized": False,
            "cuda_authorized": False,
        },
        "measured_p0": {
            "full_complete_archive_bytes": archive_size,
            "full_complete_archive_decimal_gb": archive_size / 1e9,
            "required_422_member_compressed_bytes": required_bytes,
            "required_422_member_uncompressed_bytes": archive_record["required_v4_uncompressed_bytes"],
            "pilot_case_wall_seconds": usage["case_wall_seconds"],
            "stage2_seconds_total": usage["stage2_elapsed_seconds"],
            "production_seconds_total": usage["production_elapsed_seconds_sum"],
            "production_branches": usage["production_branch_count"],
            "stage2_calls": usage["stage2_run_count"],
            "observed_peak_free_space_consumption_bytes": peak_consumption,
            "observed_minimum_free_space_bytes": free_min,
            "raw_files_remaining_after_cleanup": usage["raw_files_remaining_after_archive_cleanup"],
        },
        "compact_archive_scenarios": {
            "conservative_all_240_case_ids_at_full_p0_allowlist_bytes": compact_240,
            "conservative_all_240_case_ids_at_full_p0_allowlist_decimal_gb": compact_240 / 1e9,
            "with_25_percent_growth_reserve_bytes": compact_with_25pct,
            "with_25_percent_growth_reserve_decimal_gb": compact_with_25pct / 1e9,
            "interpretation": (
                "Conservative retention-size scenario: every case is charged the entire P0 422-member "
                "allowlist. Actual H0 and 0-ns archives should be smaller, but this is not a guarantee "
                "until their schemas are dry-run against representative archived data."
            ),
            "raw_and_full_csi_arrays_policy": (
                "Do not include full CSI NPY arrays or duplicate prepared IQ in every final case archive. "
                "Keep hashes, manifests, source identities, evaluation outputs, full-period Detection/TrackManager "
                "lineage, CFAR geometry, and target-only fixed-ROI complex response. Remove rebuildable raw only "
                "after that case archive passes SHA/CRC/schema/lineage checks and the review retention window ends."
            ),
        },
        "full_archive_scenarios": {
            "one_full_p0_archive_for_each_of_240_case_ids_bytes": full_pilot_archives_240,
            "one_full_p0_archive_for_each_of_240_case_ids_decimal_tb": full_pilot_archives_240 / 1e12,
            "full_p0_archives_for_120_plus4_case_identities_bytes": full_plus4_archives_120,
            "stage2_call_normalized_bytes_minimum": math.ceil(stage2_per_call * stage2_min),
            "stage2_call_normalized_bytes_maximum": math.ceil(stage2_per_call * stage2_max),
            "production_call_normalized_bytes": None,
            "note": (
                "The full P0 ZIP repeats raw Stage2/prepared inputs and all CSI arrays. Its direct scaling is a "
                "storage stress scenario, not the recommended retention plan. Full raw archive scaling for the "
                "zero-delay and H0 case types is not determined by this one target-present pilot."
            ),
        },
        "time_projection_under_p0_contention": {
            "p0_stage2_seconds_per_call": stage2_seconds_per_call,
            "p0_production_seconds_per_call": production_seconds_per_call,
            "stage2_wall_hours_from_observed_call_mean_min": stage2_seconds_per_call * stage2_min / 3600,
            "stage2_wall_hours_from_observed_call_mean_max": stage2_seconds_per_call * stage2_max / 3600,
            "production_wall_hours_from_observed_call_mean": production_seconds_per_call * production_calls / 3600,
            "p0_residual_case_seconds_after_stage2_and_production": residual_case_seconds,
            "p0_residual_case_linear_hours_for_240_cases": residual_case_seconds * 240 / 3600,
            "pilot_component_model_total_hours_min": (
                stage2_seconds_per_call * stage2_min
                + production_seconds_per_call * production_calls
                + residual_case_seconds * 240
            ) / 3600,
            "pilot_component_model_total_hours_max": (
                stage2_seconds_per_call * stage2_max
                + production_seconds_per_call * production_calls
                + residual_case_seconds * 240
            ) / 3600,
            "pilot_case_wall_linear_hours": usage["case_wall_seconds"] * 240 / 3600,
            "limitations": [
                "The P0 GPU was concurrently occupied by an external training process; these are not benchmark timings.",
                "M1/Huber estimator and correction time was not independently timed.",
                "The residual case term includes input preparation, archive work, orchestration, and uninstrumented estimator time.",
                "Queueing, failed attempts, retries, and H0/0-ns case differences are not modeled exactly.",
            ],
        },
        "space_gate": {
            "user_final_project_cap_decimal_gb": 20.0,
            "observed_p0_peak_disk_consumption_bytes": peak_consumption,
            "minimum_free_reserve_bytes": ten_gib,
            "minimum_start_free_bytes_using_observed_peak_plus_reserve": peak_consumption + ten_gib,
            "current_project_tree_bytes": current_tree,
            "current_project_tree_decimal_gb": current_tree / 1e9 if current_tree else None,
            "current_project_tree_allocated_bytes": current_allocated,
            "current_project_tree_allocated_decimal_gb": current_allocated / 1e9 if current_allocated else None,
            "modeled_final_tree_bytes_after_review_replaces_p0_zip_with_compact_matrix_and_25pct_reserve": projected_final_tree,
            "modeled_final_tree_decimal_gb_after_replacement": projected_final_tree / 1e9 if projected_final_tree else None,
            "project_tree_if_p0_zip_and_conservative_compact_matrix_both_retained_bytes": (
                current_tree + compact_with_25pct if current_tree else None
            ),
            "deletion_policy": (
                "Retain the unique 13-GB P0 ZIP through independent review. Only after review may the full ZIP be "
                "removed if the high-value report/manifest/compact evidence is verified and the user retention "
                "window is complete. No existing user data was removed for this analysis."
            ),
        },
        "archive_categories": compact_rows,
    }
    json_write(output / "resource_model.json", record)
    return record


def build_case_plan(output: Path) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from scripts.phase_r.r22_v4_execution_plan import build_v4_execution_plan, load_v4_design

    plan = build_v4_execution_plan(load_v4_design())
    keep = {
        "design_id": plan["design_id"],
        "design_status": plan["design_status"],
        "execution_authorized": plan["execution_authorized"],
        "cuda_authorized": plan["cuda_authorized"],
        "case_counts": plan["case_counts"],
        "registered_branch_counts": plan["registered_branch_counts"],
        "registered_production_branch_counts": plan["registered_production_branch_counts"],
        "internal_auxiliary_production_call_counts": plan["internal_auxiliary_production_call_counts"],
        "production_branch_call_counts": plan["production_branch_call_counts"],
        "offline_diagnostic_slot_count": plan["offline_diagnostic_slot_count"],
        "stage2_call_counts": plan["stage2_call_counts"],
        "case_identity_count": len(plan["case_identities"]),
        "registered_science_branch_count": len(plan["registered_science_branches"]),
        "source_derived": True,
        "dry_run_only": True,
    }
    if (keep["case_identity_count"] != 240
            or keep["registered_science_branch_count"] != 1470
            or keep["production_branch_call_counts"]["total"] != 2520):
        raise ValueError("source-derived V4 case plan differs from the documented draft")
    json_write(output / "case_plan_counts.json", keep)
    return keep


def draw_figures(
    output: Path, task_result: Mapping[str, Any], response_rows: Sequence[Mapping[str, Any]],
    power_summary: Mapping[str, Any],
) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    methods = task_result["method_summary"]
    labels = [row["method_id"] for row in methods]
    coverages = [row["coverage_percent"] for row in methods]
    rmse_values = [row["own_success_position_rmse_m"] for row in methods]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
    axes[0].barh(labels, coverages, color="#376996")
    axes[0].invert_yaxis()
    axes[0].set_xlim(0, 100)
    axes[0].set_xlabel("Target-period hit coverage (%)")
    axes[0].set_title("P0 single-seed coverage (n=200 opportunities)")
    axes[1].barh(labels, rmse_values, color="#b06d44")
    axes[1].invert_yaxis()
    axes[1].set_xlabel("Own-success position RMSE (m)")
    axes[1].set_title("Conditional position error (different hit sets)")
    fig.savefig(output / "figure_p0_coverage_and_position.svg", format="svg")
    plt.close(fig)

    response = [row for row in response_rows if row["role"] == "TO"]
    fig, ax = plt.subplots(figsize=(9, 4.8), constrained_layout=True)
    x = range(len(response))
    medians = [row["power_ratio_median"] for row in response]
    lows = [row["power_ratio_p10"] for row in response]
    highs = [row["power_ratio_p90"] for row in response]
    ax.errorbar(
        list(x), medians,
        yerr=[[median - low for median, low in zip(medians, lows)],
              [high - median for median, high in zip(medians, highs)]],
        fmt="o", capsize=4, color="#4d7d57",
    )
    ax.axhline(1.0, color="#555555", linewidth=1, linestyle="--")
    ax.set_xticks(list(x), [row["method_id"] for row in response], rotation=25, ha="right")
    ax.set_ylabel("TO CSI_AFTER fixed-ROI power / A0_TO")
    ax.set_title("Single-seed target-only response ratio (median, P10–P90)")
    fig.savefig(output / "figure_p0_target_response.svg", format="svg")
    plt.close(fig)

    primary = power_summary["primary_m1_minus_fixed"]
    coverage = power_summary["coverage_m1_minus_fixed"]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5), constrained_layout=True)
    axes[0].bar(["M1 − Fixed"], [primary["80pct_power_mde_m"]], color="#b06d44")
    axes[0].set_ylabel("80% power MDE (m)")
    axes[0].set_title("Position planning sensitivity, n=30 seeds")
    axes[1].bar(["M1 − Fixed"], [coverage["80pct_one_sided_alpha_0_025_noninferiority_margin_pp_when_true_difference_zero"]], color="#376996")
    axes[1].set_ylabel("80% power NI margin (percentage points)")
    axes[1].set_title("Coverage planning sensitivity, true Δ=0")
    fig.savefig(output / "figure_v33_precision_sensitivity.svg", format="svg")
    plt.close(fig)
    for svg_path in output.glob("figure_*.svg"):
        lines = svg_path.read_text(encoding="utf-8").splitlines()
        svg_path.write_text(
            "\n".join(line.rstrip(" \t\r") for line in lines) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--pilot-evidence", type=Path, default=DEFAULT_PILOT_EVIDENCE)
    parser.add_argument("--historical-audit", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    archive_path = args.archive if args.archive.is_absolute() else ROOT / args.archive
    pilot_evidence = args.pilot_evidence if args.pilot_evidence.is_absolute() else ROOT / args.pilot_evidence
    historical_path = args.historical_audit if args.historical_audit.is_absolute() else ROOT / args.historical_audit
    output = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    output.mkdir(parents=True, exist_ok=True)

    identity = archive_identity(archive_path, pilot_evidence)
    with zipfile.ZipFile(archive_path) as archive:
        case_manifests = [name for name in archive.namelist() if name.endswith("/case_manifest.json")]
        if len(case_manifests) != 1:
            raise ValueError("expected exactly one P0 case manifest")
        case_root = case_manifests[0].rsplit("/case_manifest.json", 1)[0]
        task_result = analyze_task_metrics(archive, case_root, output)
        association_rows = zip_csv(archive, "evaluation/target_associations.csv")
        track_audit = audit_track_payloads(archive, association_rows, output)
        cfar_rows = analyze_cfar_geometry(archive, output)
        response_rows = analyze_target_response(archive, case_root, output)

    historical = analyze_historical_v33(historical_path, output)
    power = power_sensitivity(historical, output)
    plan = build_case_plan(output)
    resources = resource_model(identity, pilot_evidence, output)
    repo_identity = current_repo_identity()
    source_paths = [
        Path(__file__),
        ROOT / "scripts/phase_r/r22_v4_execution_plan.py",
        ROOT / "scripts/phase_r/r21_task_evaluator.py",
        ROOT / "scripts/phase_r/r20_task_evaluator.py",
        ROOT / "scripts/phase_r/evaluate_r7_task_metrics.py",
        ROOT / "docs/research/phase_r/R22/config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json",
        ROOT / "docs/research/phase_r/R22/31_v4_causal_output_complete_confirmatory_contract_draft_20261113.md",
        historical_path,
    ]
    source_hashes = {
        str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path): sha256_file(path)
        for path in source_paths
    }
    p0_build_identity = json.loads((pilot_evidence / "build_identity.json").read_text(encoding="utf-8"))
    json_write(output / "archive_validation.json", identity)
    json_write(output / "power_precision_sensitivity.json", power)
    json_write(output / "source_identity.json", {
        **repo_identity,
        "p0_execution_source_commit": p0_build_identity.get("execution_source_commit"),
        "p0_execution_source_worktree_dirty": p0_build_identity.get("experiment_source_worktree_dirty_at_run"),
        "analysis_script_sha256": source_hashes[str(Path(__file__).relative_to(ROOT))],
        "source_sha256": source_hashes,
        "p0_archive_sha256": identity["archive_sha256"],
        "embedded_archive_manifest_sha256": identity["embedded_manifest_sha256"],
        "historical_audit_sha256": source_hashes[str(historical_path.relative_to(ROOT))],
        "research_master_blob": EXPECTED_MASTER_BLOB,
        "no_research_master_edit": True,
    })
    draw_figures(output, task_result, response_rows, power)
    json_write(output / "analysis_summary.json", {
        "analysis_type": "offline_p0_evidence_reanalysis",
        "native_cuda_rerun": False,
        "stage2_rerun": False,
        "production_rerun": False,
        "confirmatory_matrix_started": False,
        "ai_training_started": False,
        "archive_identity": identity,
        "p0_method_summary": task_result["method_summary"],
        "all_p0_parameter_parity_pass": all(row["parity_pass"] for row in task_result["recomputed_parity"]),
        "track_payload_lineage_violations": sum(int(row["payload_lineage_violations"]) for row in track_audit),
        "track_payload_source_violations": sum(int(row["payload_source_violations"]) for row in track_audit),
        "track_payload_measurement_provenance_violations": sum(
            int(row["payload_measurement_provenance_violations"]) for row in track_audit
        ),
        "on_track_summary_parity_methods": sum(
            row["role"] == "ON" and row["track_summary_parity"] is True for row in track_audit
        ),
        "on_track_summary_methods": sum(row["role"] == "ON" for row in track_audit),
        "cfar_branch_count": len(cfar_rows),
        "target_response_rows_reanalyzed": 90000,
        "target_response_summary": response_rows,
        "historical_v33_summary": historical["summary"],
        "power_precision_sensitivity": power,
        "source_derived_v4_plan": plan,
        "resource_model": resources,
        "status": "ANALYSIS_COMPLETE; CONFIRMATORY_DESIGN_NOT_FROZEN",
    })
    print(json.dumps({
        "output_dir": str(output.relative_to(ROOT)),
        "archive_sha256": identity["archive_sha256"],
        "archive_size_bytes": identity["archive_size_bytes"],
        "required_member_bytes": identity["required_v4_compressed_bytes"],
        "method_hits": {row["method_id"]: row["matched_hits"] for row in task_result["method_summary"]},
        "position_parity": all(row["parity_pass"] for row in task_result["recomputed_parity"]),
        "track_lineage_violations": sum(int(row["payload_lineage_violations"]) for row in track_audit),
        "track_summary_parity": {
            row["method_id"]: row["track_summary_parity"]
            for row in track_audit if row["role"] == "ON"
        },
        "historical_m1_fixed_sd_m": historical["summary"][0]["position_delta_seed_sd_m"],
        "case_plan": plan["case_counts"],
        "compact_matrix_estimate_decimal_gb": resources["compact_archive_scenarios"]["conservative_all_240_case_ids_at_full_p0_allowlist_decimal_gb"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
