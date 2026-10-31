#!/usr/bin/env python3
"""Read-only adapters for the saved R18, R8 and R12 Production records."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterator, Mapping

from scripts.phase_r.r19_r18_snapshot_evaluation import (
    _axis_rows,
    _load_condition_records,
    _load_final_detections,
    _load_truth as _load_r18_truth,
    _csv,
)
from scripts.phase_r.r20_task_evaluator import map_track_observation_periods


REPO_ROOT = Path(__file__).resolve().parents[2]
R18_ROOT = REPO_ROOT / "docs/research/phase_r/R18/evidence/formal_matrix"
R20_INPUT_ROOT = REPO_ROOT / "docs/research/phase_r/R20/evidence/inputs"
R8_ROOT = R20_INPUT_ROOT / "r8"
R8_SEED101_ROOT = R8_ROOT / "seed101"
R12_ROOT = R20_INPUT_ROOT / "r12"
R8_BUILD_MANIFEST = REPO_ROOT / "docs/research/phase_r/evidence/r8_unknown_delay_multiseed_20261023/evaluation/r8_experiment_manifest.json"
R12_RUN_MANIFEST = REPO_ROOT / "docs/research/phase_r/evidence/r12_physics_matched_delay_20261024/source_manifests/run_manifest.json"
R8_BINARY_SHA256 = "de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00"
R18_BINARY_SHA256 = "3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0"

HISTORICAL_CONDITIONS = {
    "A0_Ideal": "A0",
    "A1_Current_unknown_error": "B",
    "A2_Known_error_correction_upper_bound": "Known",
    "R12_D3_R9_Fixed": "D3",
    "R12_M1_Physics_Matched": "M1",
}
R8_SEED101_COMPACT_NAMES = {"A0_Ideal": "A", "A1_Current_unknown_error": "B",
                            "A2_Known_error_correction_upper_bound": "C"}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    return _csv(path) if path.is_file() else []


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit_path(value: str | Path | None) -> str | None:
    """Use repository-relative paths in committed evidence, not workstation roots."""
    if value in (None, ""):
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve(strict=False).relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return f"<external>/{path.name}"


def _canonical_xml_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    root = ET.parse(path).getroot()
    output_path_tags = {"result_add", "track_debug_dir"}
    values: list[tuple[str, str]] = []

    def visit(node: ET.Element, prefix: str) -> None:
        key = f"{prefix}/{node.tag}"
        value = (node.text or "").strip()
        if value:
            if node.tag in output_path_tags:
                value = "<RUN_OUTPUT_PATH>"
            values.append((key, value))
        for name, item in sorted(node.attrib.items()):
            values.append((f"{key}@{name}", item))
        for child in node:
            visit(child, key)

    visit(root, "")
    canonical = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def discover_r18_cases() -> Iterator[dict[str, Any]]:
    for manifest_path in sorted(R18_ROOT.glob("seed*/experiment_snapshot/manifest.json")):
        snapshot_root = manifest_path.parent
        snapshot = read_json(manifest_path)
        for case_root in sorted((snapshot_root / "cases").glob("*")):
            case_manifest_path = case_root / "case_manifest.json"
            if not case_manifest_path.is_file():
                continue
            case_manifest = read_json(case_manifest_path)
            records = _load_condition_records(case_manifest, case_root)
            yield {
                "dataset": "R18",
                "dataset_key": manifest_path.parents[1].name,
                "case_root": case_root,
                "case_manifest": case_manifest,
                "case_manifest_path": case_manifest_path,
                "snapshot_manifest": snapshot,
                "snapshot_manifest_path": manifest_path,
                "branches": {
                    (str(label), str(role)): record
                    for (label, role), record in records.items()
                },
            }


def discover_historical_cases() -> Iterator[dict[str, Any]]:
    cases: list[tuple[str, Path]] = []
    cases.append(("R8", R8_SEED101_ROOT / "cases/base__seed_101__v_p6d7__snr_p30__delay_p4ns"))
    for seed in (202, 303):
        seed_root = R8_ROOT / f"seed{seed}"
        cases.extend(("R8", path) for path in sorted((seed_root / "cases").glob("*")))
    cases.extend(("R12", path) for path in sorted((R12_ROOT / "cases").glob("*")))
    for dataset, case_root in cases:
        case_manifest_path = case_root / "case_manifest.json"
        if not case_manifest_path.is_file():
            continue
        case_manifest = read_json(case_manifest_path)
        branch_dirs: dict[tuple[str, str], Path] = {}
        for branch_dir in sorted((case_root / "production").glob("*")):
            manifest_path = branch_dir / "branch_manifest.json"
            if not branch_dir.is_dir() or not manifest_path.is_file():
                continue
            branch_manifest = read_json(manifest_path)
            condition, role = str(branch_manifest.get("condition", "")), str(branch_manifest.get("role", ""))
            allowed = set(HISTORICAL_CONDITIONS) if dataset == "R12" else {
                "A0_Ideal", "A1_Current_unknown_error", "A2_Known_error_correction_upper_bound",
            }
            if condition not in allowed:
                continue
            key = (condition, role)
            current = branch_dirs.get(key)
            expected_name = f"{condition}_{role}"
            if current is None or (branch_dir.name == expected_name and current.name != expected_name):
                branch_dirs[key] = branch_dir
        yield {
            "dataset": dataset,
            "dataset_key": case_root.name,
            "case_root": case_root,
            "case_manifest": case_manifest,
            "case_manifest_path": case_manifest_path,
            "snapshot_manifest": None,
            "snapshot_manifest_path": None,
            "branches": branch_dirs,
        }


def load_truth(case: Mapping[str, Any], role: str) -> tuple[list[dict[str, str]], Path | None]:
    case_root = Path(case["case_root"])
    if case["dataset"] == "R18":
        truths = _load_r18_truth(case_root, role)
        path = case_root / "scenes" / ("A1_ON" if role == "ON" else "A0_TO") / "truth/truth_targets_by_beam.csv"
        if not path.is_file():
            # R18 truth discovery is role-aware and may use A0_ON/A1_ON aliases.
            for variant in (("A1_ON", "A0_ON") if role == "ON" else ("A0_TO", "A1_TO")):
                candidate = case_root / "scenes" / variant / "truth/truth_targets_by_beam.csv"
                if candidate.is_file():
                    path = candidate
                    break
        return truths, path if path.is_file() else None
    variants = ("A1_TO", "A0_TO") if role == "TO" else ("A1_ON", "A0_ON")
    for variant in variants:
        path = case_root / "scenes" / variant / "truth/truth_targets_by_beam.csv"
        if path.is_file():
            return read_csv(path), path
    return [], None


def _result_files(branch_dir: Path) -> list[Path]:
    return sorted(
        branch_dir.glob("result/detection_results_GMTI*.csv"),
        key=lambda path: int(re.search(r"GMTI(\d+)", path.name).group(1)),
    )


def load_detections(branch_dir: Path) -> dict[str, Any]:
    result_files = _result_files(branch_dir)
    rows: list[dict[str, str]] = []
    result_period_map: dict[int, int] = {}
    per_result_rows: dict[int, list[dict[str, str]]] = {}
    for path in result_files:
        match = re.search(r"GMTI(\d+)", path.name)
        if not match:
            continue
        result_number = int(match.group(1))
        current = read_csv(path)
        periods = {int(float(row["period_id"])) for row in current if row.get("period_id") not in (None, "")}
        if len(periods) != 1:
            raise ValueError(f"expected one Production period in {path}, found {sorted(periods)}")
        result_period_map[result_number] = next(iter(periods))
        per_result_rows[result_number] = current
        rows.extend(current)
    if not result_files:
        aggregate = branch_dir / "result/detection_results.csv"
        rows = read_csv(aggregate)
    return {
        "rows": rows,
        "files": result_files,
        "file_sha256": {str(path): sha256_file(path) for path in result_files},
        "result_period_map": result_period_map,
        "per_result_rows": per_result_rows,
        "source_status": "PER_RESULT_PRODUCTION_CSV" if result_files else "AGGREGATE_OR_MISSING",
    }


def load_doppler_axes(branch_dir: Path, *, dataset_key: str, condition: str, role: str) -> dict[str, Any]:
    candidates = (
        branch_dir / "result/debug/cluster_detection_lineage.csv",
        branch_dir / "result/cluster_detection_lineage.csv",
    )
    for path in candidates:
        if path.is_file():
            rows = read_csv(path)
            axes = _axis_rows(rows)
            return {"rows": axes, "source_path": path, "source_sha256": sha256_file(path),
                    "status": "PRODUCTION_LINEAGE_AXIS" if axes else "LINEAGE_WITHOUT_AXIS_ROWS"}
    if dataset_key.startswith("base__seed_101") and condition in R8_SEED101_COMPACT_NAMES:
        alias = R8_SEED101_COMPACT_NAMES[condition]
        compact = REPO_ROOT / "docs/research/phase_r/evidence/r8_unknown_delay_multiseed_20261023" / "evaluation/seed101" / f"{alias}_{role}/lineage.csv"
        if compact.is_file():
            rows = read_csv(compact)
            axes = _axis_rows(rows)
            return {"rows": axes, "source_path": compact, "source_sha256": sha256_file(compact),
                    "status": "COMPACT_LINEAGE_AXIS" if axes else "COMPACT_LINEAGE_NO_AXIS_ROWS"}
    return {"rows": [], "source_path": None, "source_sha256": None, "status": "DOPPLER_AXIS_NOT_AVAILABLE"}


def load_track_debug(branch_dir: Path, detections: Mapping[str, Any]) -> dict[str, Any]:
    root = branch_dir / "track_debug_runs"
    run_dirs = sorted(path for path in root.iterdir() if path.is_dir()) if root.is_dir() else []
    if len(run_dirs) != 1:
        return {"status": "NOT_EVALUABLE", "reason": f"expected one TrackManager debug run, found {len(run_dirs)}"}
    run_dir = run_dirs[0]
    state_path, event_path, payload_path = (run_dir / name for name in (
        "track_states.csv", "track_events.csv", "track_output_payloads.csv",
    ))
    if not state_path.is_file() or not payload_path.is_file():
        return {"status": "NOT_EVALUABLE", "reason": "missing track_states or track_output_payloads"}
    states, events, payloads = read_csv(state_path), read_csv(event_path), read_csv(payload_path)
    mapped = map_track_observation_periods(states, events, detections["result_period_map"])
    payload_groups: dict[tuple[int, str, int], list[dict[str, str]]] = {}
    for payload in payloads:
        result_number = _result_number(payload.get("result_id"))
        det_index = _int(payload.get("matched_det_index"))
        track_id = str(payload.get("track_id", "")).strip()
        if result_number is None or det_index is None or not track_id:
            continue
        payload_groups.setdefault((result_number, track_id, det_index), []).append(payload)
    observations: list[dict[str, Any]] = []
    errors: list[str] = []
    for source in mapped["observations"]:
        result_number = _result_number(source.get("result_id"))
        track_id = str(source.get("track_id", "")).strip()
        matched = _truthy(source.get("matched_this_frame"))
        det_index = _int(source.get("matched_det_index")) if matched else None
        detections_for_result = detections["per_result_rows"].get(result_number, [])
        detection = None
        if matched and det_index is not None and 0 <= det_index < len(detections_for_result):
            detection = detections_for_result[det_index]
        elif matched:
            errors.append("matched_det_index_out_of_range")
        beam_id = _int(detection.get("beam_id")) if detection else 50
        if beam_id is None:
            errors.append("missing_beam_id")
            continue
        det_id = str(detection.get("det_id", "")) if detection else ""
        payload_group = payload_groups.get((result_number, track_id, det_index), []) if det_index is not None else []
        unique_payload = payload_group[0] if len(payload_group) == 1 else None
        observations.append({
            "period_id": int(source["period_id"]), "beam_id": beam_id, "track_id": track_id,
            "state": source.get("state", ""), "matched_this_frame": matched, "det_id": det_id,
            "output_payload": bool(payload_group), "output_speed": unique_payload.get("output_speed") if unique_payload else None,
            "result_number": result_number, "det_index": det_index, "utc": source.get("utc", ""),
            "payload_rows_for_key": len(payload_group),
        })
    duplicate_keys = sum(len(rows) > 1 for rows in payload_groups.values())
    duplicate_rows = sum(max(0, len(rows) - 1) for rows in payload_groups.values())
    return {
        "status": "evaluable" if observations and not errors else "PARTIAL" if observations else "NOT_EVALUABLE",
        "reason": ";".join(sorted(set(errors))) if errors else None,
        "observations": observations,
        "events": mapped["events"],
        "payload_count": len(payloads),
        "payload_duplicate_key_count": duplicate_keys,
        "payload_duplicate_row_count": duplicate_rows,
        "period_mapping_audit": {key: mapped[key] for key in (
            "result_period_map", "mapping_source", "utc_period_conflicts_avoided",
            "event_reported_period_mismatch_count",
        )},
        "association_audit_v2_available": any(run_dir.rglob("track_association_audit_v2.csv")),
        "source_paths": {"states": state_path, "events": event_path, "payloads": payload_path},
        "source_sha256": {"states": sha256_file(state_path), "events": sha256_file(event_path),
                          "payloads": sha256_file(payload_path)},
    }


def _result_number(value: Any) -> int | None:
    match = re.search(r"(\d+)", str(value or ""))
    return int(match.group(1)) if match else None


def _int(value: Any) -> int | None:
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number.is_integer() else None
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def branch_identity(
    case: Mapping[str, Any], branch_dir: Path, condition: str, role: str,
    truth_path: Path | None, detection_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    cm = case["case_manifest"]
    branch_manifest_path = branch_dir / "branch_manifest.json"
    branch_manifest = read_json(branch_manifest_path) if branch_manifest_path.is_file() else {}
    run_path = branch_dir / "result/run_manifest.json"
    run_manifest = read_json(run_path) if run_path.is_file() else {}
    snapshot = case.get("snapshot_manifest") or {}
    scene = cm.get("scene_identity", {})
    case_record = cm.get("case", {})
    build = snapshot.get("build", {}).get("GMTI_pipe_core", {})
    build_audit_path: Path | None = None
    build_audit: dict[str, Any] = {}
    if case["dataset"] == "R8":
        build_audit_path = R8_BUILD_MANIFEST
        if build_audit_path.is_file():
            build_audit = read_json(build_audit_path)
        binary_record = build_audit.get("frozen_build", {}).get("production_binary", {})
        experiment_runner_commit = build_audit.get("base_commit")
        binary_sha_source = "frozen_build.production_binary.sha256"
    elif case["dataset"] == "R12":
        build_audit_path = R12_RUN_MANIFEST
        if build_audit_path.is_file():
            build_audit = read_json(build_audit_path)
        binary_record = build_audit.get("production_binary", {})
        experiment_runner_commit = build_audit.get("baseline_commit")
        binary_sha_source = "production_binary.sha256"
    else:
        binary_record = build
        experiment_runner_commit = None
        binary_sha_source = "snapshot build manifest"
    frozen_binary = binary_record.get("sha256") or build.get("sha256") or (
        R18_BINARY_SHA256 if case["dataset"] == "R18" else None
    )
    source_identity = snapshot.get("source", {})
    input_paths = branch_manifest.get("period_paths", [])
    input_hashes = [item.get("sha256") for item in input_paths if item.get("sha256")]
    input_availability = [Path(item.get("path", "")).is_file() for item in input_paths]
    pair = cm.get("pair_identity", {})
    paired_background = cm.get("paired_background", {})
    config_file = branch_dir / "production.xml"
    runtime_config = branch_dir / "result/runtime_config_dump.json"
    truth_hash = sha256_file(truth_path) if truth_path else None
    semantic_config_hash = _canonical_xml_sha256(config_file) or snapshot.get("config", {}).get("sha256")
    correction = cm.get("conditions", {}).get(condition, {})
    input_preparation = correction.get("input_preparation", {}).get(role, {})
    return {
        "dataset": case["dataset"], "dataset_key": case["dataset_key"],
        "case_id": cm.get("case_id", scene.get("case_id", case_record.get("case_id"))),
        "seed": scene.get("seed", case_record.get("seed")),
        "target_velocity_mps": scene.get("target_velocity_mps", case_record.get("target_velocity_mps")),
        "target_snr_db": scene.get("target_snr_db", case_record.get("target_snr_db")),
        "delay_error_ns": cm.get("delay_error_ns", case_record.get("delay_error_ns", scene.get("delay_error_ns"))),
        "condition": condition, "canonical_condition": HISTORICAL_CONDITIONS.get(condition, condition), "role": role,
        "branch_name": branch_manifest.get("branch_name", branch_dir.name),
        "runner_commit_case_manifest": cm.get("git", {}).get("commit"),
        "runner_commit_run_manifest": run_manifest.get("git_commit"),
        "snapshot_runner_commit": source_identity.get("commit"),
        "snapshot_runner_dirty": source_identity.get("dirty"),
        "experiment_base_commit_from_build_audit": experiment_runner_commit,
        "build_source_commit": binary_record.get("source_commit") or build.get("source_commit") or cm.get("frozen_build", {}).get("source_commit"),
        "production_binary_sha256": frozen_binary,
        "production_binary_identity_source": (
            f"{build_audit_path.relative_to(REPO_ROOT)}:{binary_sha_source}"
            if build_audit_path and build_audit_path.is_file() else
            "snapshot build manifest" if build.get("sha256") else "identity unavailable"
        ),
        "binary_build_audit_manifest_sha256": sha256_file(build_audit_path) if build_audit_path else None,
        "production_xml_sha256": sha256_file(config_file),
        "semantic_config_sha256": semantic_config_hash,
        "semantic_config_identity_source": "normalized Production XML" if _canonical_xml_sha256(config_file) else "snapshot config manifest",
        "runtime_config_dump_sha256": sha256_file(runtime_config),
        "truth_path": audit_path(truth_path),
        "truth_sha256": truth_hash,
        "background_id": audit_path(pair.get("background_id")) if Path(str(pair.get("background_id", ""))).is_absolute()
                         else pair.get("background_id") or paired_background.get("source_condition"),
        "target_id": pair.get("target_id") or scene.get("target", {}).get("target_id"),
        "production_identity": pair.get("production_identity"),
        "input_period_sha256_declared": input_hashes,
        "input_period_sha256_verified": [
            sha256_file(Path(item["path"])) if item.get("path") and Path(item["path"]).is_file() else None
            for item in input_paths
        ],
        "input_period_paths_available": input_availability,
        "input_manifest_status": "DECLARED_AND_VERIFIED" if input_hashes and all(input_availability) else
                                 "DECLARED_RAW_INPUT_MISSING" if input_hashes else "INPUT_IDENTITY_INCOMPLETE",
        "correction_applied": correction.get("correction_applied", input_preparation.get("correction_applied")),
        "applied_delay_ns": correction.get("applied_delay_ns", input_preparation.get("applied_delay_ns")),
        "correction_source": correction.get("correction_source", input_preparation.get("correction_source")),
        "branch_manifest_sha256": sha256_file(branch_manifest_path),
        "run_manifest_sha256": sha256_file(run_path),
        "period_detection_files": [
            [audit_path(path), digest]
            for path, digest in detection_bundle.get("file_sha256", {}).items()
        ],
    }


def file_manifest(case: Mapping[str, Any], branch_dir: Path, truth_path: Path | None,
                  detection_bundle: Mapping[str, Any], axis_bundle: Mapping[str, Any],
                  track_bundle: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    candidates = [case["case_manifest_path"], branch_dir / "branch_manifest.json",
                  branch_dir / "result/run_manifest.json", branch_dir / "production.xml",
                  branch_dir / "result/runtime_config_dump.json"]
    if truth_path:
        candidates.append(truth_path)
    candidates.extend(detection_bundle.get("files", []))
    if axis_bundle.get("source_path"):
        candidates.append(axis_bundle["source_path"])
    if track_bundle:
        candidates.extend(Path(path) for path in track_bundle.get("source_paths", {}).values())
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        records.append({"path": audit_path(path),
                        "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return records
