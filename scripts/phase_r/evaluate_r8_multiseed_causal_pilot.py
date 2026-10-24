#!/usr/bin/env python3
"""Evaluate the frozen R8 Native CUDA A/B/C pilot and reused R7 seed101."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence
import xml.etree.ElementTree as ET

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r import audit_r8_cfar_numeric_reference as cfar_audit
from scripts.phase_r import evaluate_r3_minimal_cuda_causal_pilot as r3_eval
from scripts.phase_r import evaluate_r7_production_matrix as r7_production_eval
from scripts.phase_r import evaluate_r7_task_metrics as task_eval


CONDITIONS = {
    "A": "A0_Ideal",
    "B": "A1_Current_unknown_error",
    "C": "A2_Known_error_correction_upper_bound",
}
R7_CONDITIONS = {"A": "A0", "B": "B", "C": "C"}
ROLES = ("ON", "OFF", "TO")
EXPECTED_BINARY_SHA256 = "de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00"


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["status"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _normalize_csv_line_endings(directory: Path) -> None:
    for path in directory.rglob("*.csv"):
        content = path.read_bytes()
        normalized = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        if normalized != content:
            path.write_bytes(normalized)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    path = path.resolve()
    try:
        display_path = str(path.relative_to(ROOT))
    except ValueError:
        display_path = str(path)
    if not path.is_file():
        return {"path": display_path, "exists": False}
    return {
        "path": display_path,
        "absolute_path": str(path),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _case_execution_evidence(seed_root: Path, case_root: Path) -> dict[str, Any]:
    outer_path = seed_root / "manifest.json"
    outer = read_json(outer_path)
    case = read_json(case_root / "case_manifest.json")
    branches = []
    for condition in CONDITIONS.values():
        for role in ROLES:
            branch = _branch(case, condition, role)
            result_dir = Path(str(branch.get("result_dir", "")))
            track_dir = Path(str(branch.get("track_debug_dir", "")))
            debug = result_dir / "debug"
            csi_manifests = sorted(result_dir.rglob("csi_roi_manifest.csv"))
            csi_summaries = sorted(result_dir.rglob("csi_metric_tap_summary.csv"))
            beam_text = sorted(result_dir.glob("GMTI*.txt"))
            branch_manifest_path = case_root / "production" / f"{condition}_{role}" / "branch_manifest.json"
            branch_record = {
                "condition": condition,
                "role": role,
                "status": branch.get("status"),
                "production_status": branch.get("production_status"),
                "production_returncode": branch.get("production_returncode"),
                "production_path_gate": branch.get("production_path_gate"),
                "production_xml": _file_record(Path(str(branch.get("production_xml", "")))),
                "configuration_log": _file_record(Path(str(branch.get("configuration_log", "")))),
                "production_log": _file_record(Path(str(branch.get("production_log", "")))),
                "runtime_config_dump": _file_record(result_dir / "runtime_config_dump.json"),
                "cycle_logs": [_file_record(path) for path in beam_text],
                "csi_roi_manifests": [_file_record(path) for path in csi_manifests],
                "csi_tap_summaries": [_file_record(path) for path in csi_summaries],
                "cfar_geometry": _file_record(debug / "cfar_geometry_diagnostics.csv"),
                "cfar_numeric": _file_record(debug / "cfar_numerical_diagnostics.csv"),
                "cluster_detection_lineage": _file_record(debug / "cluster_detection_lineage.csv"),
                "track_debug": {
                    filename: _file_record(track_dir / filename)
                    for filename in (
                        "track_frames.csv", "track_detections.csv",
                        "track_output_payloads.csv", "track_states.csv",
                    )
                },
                "detections": [_file_record(Path(str(row["path"]))) for row in branch.get("cycle_rows", [])],
                "period_inputs": branch.get("period_paths", []),
                "branch_manifest": _file_record(branch_manifest_path),
            }
            branches.append(branch_record)

    audits = []
    failed_attempts = []
    for path in sorted(seed_root.glob("cfar_numeric_audit*/manifest.json")):
        record = read_json(path)
        audit = {
            "manifest": _file_record(path),
            "condition": record.get("condition"),
            "role": record.get("role"),
            "status": record.get("status"),
            "exit_code": record.get("exit_code"),
            "production_binary": record.get("production_binary"),
            "source_runtime_xml_sha256": record.get("source_runtime_xml_sha256"),
            "runtime_xml_sha256": record.get("runtime_xml_sha256"),
            "input_periods": record.get("input_periods"),
            "cfar_numeric_audit": record.get("cfar_numeric_audit"),
            "log": _file_record(Path(str(record.get("log", "")))),
        }
        audits.append(audit)
        if record.get("status") != "passed":
            log_path = Path(str(record.get("log", "")))
            log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
            failure_lines = [
                line.strip() for line in log_text.splitlines()
                if "[ERR]" in line or "[TRIG-LUT]" in line
            ]
            failed_attempts.append({
                "manifest": audit["manifest"],
                "condition": record.get("condition"),
                "role": record.get("role"),
                "status": record.get("status"),
                "exit_code": record.get("exit_code"),
                "reason_lines": failure_lines,
                "superseded_by_passing_retry": any(
                    passed.get("condition") == record.get("condition")
                    and passed.get("role") == record.get("role")
                    and passed.get("status") == "passed"
                    for passed_path in seed_root.glob("cfar_numeric_audit*/manifest.json")
                    if passed_path != path
                    for passed in [read_json(passed_path)]
                ),
            })

    scene_records = {}
    for name, item in case.get("simulation", {}).items():
        output_dir = Path(str(item.get("output_dir", "")))
        scene_records[name] = {
            "status": item.get("status"),
            "returncode": item.get("returncode"),
            "log": _file_record(Path(str(item.get("log", "")))),
            "stage2_report": _file_record(output_dir / "reports/stage2_simulation_report.md"),
            "temporal_clutter_diagnostics": _file_record(output_dir / "reports/temporal_clutter_diagnostics.json"),
            "truth": _file_record(output_dir / "truth/truth_targets_by_beam.csv"),
        }
    reconciliation_path = seed_root / "runner_exit_code_reconciliation.json"
    return {
        "seed": int(case.get("scene_identity", {}).get("seed", -1)),
        "outer_manifest": _file_record(outer_path),
        "case_manifest": _file_record(case_root / "case_manifest.json"),
        "outer_status": outer.get("status"),
        "case_status": outer.get("case_status"),
        "binary": outer.get("production_binary"),
        "source_identity_before": outer.get("source_identity_before"),
        "source_identity_after": outer.get("source_identity_after"),
        "gpu_status_before": outer.get("gpu_status_before"),
        "gpu_status_after": outer.get("gpu_status_after"),
        "disk_status_before": outer.get("disk_status_before"),
        "disk_status_after": outer.get("disk_status_after"),
        "stage2_scenes": scene_records,
        "area_clutter_gate": case.get("area_clutter_gate"),
        "physical_closure": case.get("known_correction_physical_closure"),
        "additive_audits": case.get("additive_audits"),
        "processing_chain_consistency": _operator_config_gates(case),
        "production_branches": branches,
        "cfar_numeric_audits": audits,
        "failed_diagnostic_attempts": failed_attempts,
        "runner_exit_code_reconciliation": _file_record(reconciliation_path) if reconciliation_path.is_file() else None,
    }


def _build_experiment_manifest(
    *, seed_roots: Sequence[Path], seed101_case_root: Path,
    r7_task_manifest: Path, output_dir: Path,
    campaign: Mapping[str, Any],
) -> dict[str, Any]:
    cache = ROOT / "build_r7/CMakeCache.txt"
    cache_values: dict[str, str] = {}
    if cache.is_file():
        for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(("USE_COREX:", "CMAKE_BUILD_TYPE:")) and "=" in line:
                key, value = line.split("=", 1)
                cache_values[key.split(":", 1)[0]] = value
    outer_runs = [_case_execution_evidence(root.resolve(), _seed_case_root(root.resolve())) for root in seed_roots]
    source_files = (
        ROOT / "scripts/phase_r/run_r8_multiseed_causal_pilot.py",
        ROOT / "scripts/phase_r/audit_r8_cfar_numeric_reference.py",
        ROOT / "scripts/phase_r/evaluate_r8_multiseed_causal_pilot.py",
        ROOT / "scripts/phase_r/evaluate_r7_task_metrics.py",
        ROOT / "scripts/phase_r/evaluate_r3_minimal_cuda_causal_pilot.py",
        ROOT / "configs/research/channel_delay_stage1_formal.json",
        ROOT / "simulator/scenarios/beam50_one_targets_continuous.run.json",
        ROOT / "src/processOnePeriod.cpp",
        ROOT / "build_r7/GMTI_pipe_core",
        ROOT / "build/simulate_stage2_statistical",
    )
    failed_attempts = [
        failure for run in outer_runs for failure in run["failed_diagnostic_attempts"]
    ]
    return {
        "schema_version": 1,
        "experiment": "PGRCC Phase R8 multi-seed Native CUDA Production causal pilot",
        "created_by": _file_record(Path(__file__)),
        "base_commit": read_json(seed_roots[0].resolve() / "manifest.json").get("source_identity_after", {}).get("git", {}).get("commit"),
        "production_backend": "Native CUDA / local RTX 3050 Laptop GPU",
        "corex": {"used": False, "build_cache": cache_values},
        "frozen_build": {
            "build_directory": str((ROOT / "build_r7").resolve().relative_to(ROOT)),
            "production_binary": _file_record(ROOT / "build_r7/GMTI_pipe_core"),
            "expected_sha256": EXPECTED_BINARY_SHA256,
            "stage2_simulator": _file_record(ROOT / "build/simulate_stage2_statistical"),
        },
        "source_files": [_file_record(path) for path in source_files],
        "seed101_reused_r7": {
            "case_root": str(seed101_case_root.resolve()),
            "task_metrics_manifest": _file_record(r7_task_manifest),
            "binary_sha256": read_json(r7_task_manifest).get("post_fix_binary_sha256"),
            "new_cuda_matrix_run": False,
        },
        "new_cuda_runs": outer_runs,
        "failed_attempts": failed_attempts,
        "seed202_seed303_input_reconstruction": campaign.get("historical_input_comparisons"),
        "seed_comparability": [
            {
                "seed": seed.get("seed"),
                "area_clutter_gate": seed.get("area_clutter_gate"),
                "paired_background": seed.get("paired_background"),
                "input_fusion_power": seed.get("input_fusion_power"),
            }
            for seed in campaign.get("seeds", [])
        ],
        "evaluation": {
            "metrics": str((output_dir / "r8_seed_condition_metrics.csv").resolve()),
            "target_neighborhood_cfar": str((output_dir / "target_neighborhood_cfar.csv").resolve()),
            "figure": _file_record(output_dir / "r8_seed_condition_comparison.png"),
            "metrics_json": _file_record(output_dir / "r8_multiseed_causal_evaluation.json"),
        },
        "status_labels": campaign.get("status_labels"),
        "statistical_significance_claimed": False,
        "interpretation": "three-seed descriptive causal feasibility and correctness evidence; no population Pd/Pfa or significance claim",
    }


def target_neighborhood_slices(
    *, center_row: int, center_col: int, row_count: int, col_count: int,
    half_doppler: int = 2, half_range: int = 2,
) -> tuple[list[int], list[int]]:
    """Return circular Doppler rows and clipped range columns around a target."""
    if row_count <= 0 or col_count <= 0:
        raise ValueError("CFAR map dimensions must be positive")
    if not 0 <= center_row < row_count or not 0 <= center_col < col_count:
        raise ValueError("target center is outside the CFAR map")
    if half_doppler < 0 or half_range < 0:
        raise ValueError("neighborhood half-widths must be nonnegative")
    rows = sorted({(center_row + offset) % row_count for offset in range(-half_doppler, half_doppler + 1)})
    cols = list(range(max(0, center_col - half_range), min(col_count, center_col + half_range + 1)))
    return rows, cols


def target_neighborhood_metrics(
    power: np.ndarray, threshold: np.ndarray, hits: np.ndarray, *,
    center_row: int, center_col: int, half_doppler: int = 2,
    half_range: int = 2,
) -> dict[str, Any]:
    power = np.asarray(power)
    threshold = np.asarray(threshold)
    hits = np.asarray(hits)
    if power.ndim != 2 or power.shape != threshold.shape or power.shape != hits.shape:
        raise ValueError("power, threshold, and hit maps must have the same 2D shape")
    rows, cols = target_neighborhood_slices(
        center_row=center_row, center_col=center_col,
        row_count=power.shape[0], col_count=power.shape[1],
        half_doppler=half_doppler, half_range=half_range,
    )
    roi_power = power[np.ix_(rows, cols)].astype(np.float64, copy=False)
    roi_threshold = threshold[np.ix_(rows, cols)].astype(np.float64, copy=False)
    roi_hits = hits[np.ix_(rows, cols)]
    valid = (
        np.isfinite(roi_power) & np.isfinite(roi_threshold)
        & (roi_power >= 0.0) & (roi_threshold > 0.0)
    )
    ratio = roi_power[valid] / roi_threshold[valid]
    positive_ratio = ratio[ratio > 0.0]
    margins = 10.0 * np.log10(positive_ratio) if positive_ratio.size else np.asarray([])
    finite_hits = np.isfinite(roi_hits) & valid
    return {
        "center_row": int(center_row),
        "center_col": int(center_col),
        "valid_cut_count": int(valid.sum()),
        "hit_count": int(np.count_nonzero((roi_hits >= 0.5) & finite_hits)),
        "best_margin_db": float(np.max(margins)) if margins.size else None,
        "center_margin_db": (
            float(10.0 * math.log10(float(power[center_row, center_col]) / float(threshold[center_row, center_col])))
            if valid[rows.index(center_row), cols.index(center_col)] and float(power[center_row, center_col]) > 0.0 else None
        ),
        "zero_power_cut_count": int(np.count_nonzero((roi_power == 0.0) & valid)),
        "neighborhood": "Doppler circular +/-2 rows, range +/-2 bins, clipped at range edges",
    }


def classify_target_loss_stage(
    local_cfar_hits: int, local_clusters: int,
    detection_matched: bool, track_matched: bool,
    nearest_candidate_outside_gate: bool = False,
) -> str:
    if track_matched:
        return "confirmed_track_match"
    if detection_matched:
        return "detection_to_track_association"
    if nearest_candidate_outside_gate:
        return "nearest_cluster_or_detection_outside_frozen_target_gate"
    if local_clusters > 0:
        return "cluster_to_detection_or_target_association"
    if local_cfar_hits > 0:
        return "after_cfar_before_local_cluster"
    return "no_target_local_cfar_hit"


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _seed_case_root(seed_root: Path) -> Path:
    manifests = list((seed_root / "cases").glob("*/case_manifest.json"))
    if len(manifests) != 1:
        raise ValueError(f"expected one case_manifest.json under {seed_root}/cases; found {len(manifests)}")
    return manifests[0].parent.resolve()


def _branch(case: Mapping[str, Any], condition: str, role: str) -> Mapping[str, Any]:
    record = case.get("conditions", {}).get(condition, {})
    production = record.get("production", {}) if isinstance(record, Mapping) else {}
    value = production.get(role, {}) if isinstance(production, Mapping) else {}
    if not isinstance(value, Mapping):
        raise ValueError(f"missing R8 production branch: {condition}/{role}")
    return value


def _truth_path(case: Mapping[str, Any], condition: str) -> Path:
    # Known correction reuses the B/+4 ns scene and changes only the evaluator-side
    # physical correction, so its target truth is the A1 scene truth.
    scene = "A0" if condition == "A0_Ideal" else "A1"
    simulation = case.get("simulation", {})
    record = simulation.get(f"{scene}_ON", {}) if isinstance(simulation, Mapping) else {}
    output = Path(str(record.get("output_dir", "")))
    return output / "truth/truth_targets_by_beam.csv"


def normalized_runtime_xml(xml_text: str) -> str:
    """Normalize only per-run output paths before comparing operator settings."""
    root = ET.fromstring(xml_text)
    for node in root.iter():
        if node.tag in {"result_add", "track_debug_dir"}:
            node.text = "<PER_RUN_OUTPUT_PATH>"
    return ET.tostring(root, encoding="unicode")


def _operator_config_gates(case: Mapping[str, Any]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for condition in CONDITIONS.values():
        fingerprints = {}
        for role in ROLES:
            branch = _branch(case, condition, role)
            xml_path = Path(str(branch["production_xml"]))
            normalized = normalized_runtime_xml(xml_path.read_text(encoding="utf-8"))
            fingerprints[role] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        checks[condition] = {
            "status": "passed" if len(set(fingerprints.values())) == 1 else "failed",
            "role_operator_fingerprints": fingerprints,
            "ignored_run_specific_xml_fields": ["result_add", "track_debug_dir"],
        }
    return {
        "status": "passed" if all(item["status"] == "passed" for item in checks.values()) else "failed",
        "conditions": checks,
    }


def _audit_manifests(seed_root: Path) -> dict[tuple[str, str], tuple[Path, dict[str, Any]]]:
    matches: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}
    for path in sorted(seed_root.glob("cfar_numeric_audit*/manifest.json")):
        manifest = read_json(path)
        if manifest.get("status") != "passed":
            continue
        if manifest.get("production_binary", {}).get("sha256") != EXPECTED_BINARY_SHA256:
            continue
        key = (str(manifest.get("condition")), str(manifest.get("role")))
        if key not in matches or path.stat().st_mtime > matches[key][0].stat().st_mtime:
            matches[key] = (path, manifest)
    return matches


def _audit_union_rows(audit_manifest_path: Path, audit_manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    reference_path = audit_manifest_path.parent / "cfar_reference_audit.json"
    numeric = read_json(reference_path)
    if numeric.get("status") != "passed":
        raise ValueError(f"CFAR float64 numeric audit did not pass: {reference_path}")
    rows = []
    for item in numeric.get("union_period_rows", []):
        rows.append({
            "period_id": int(item["period_id"]),
            "result_id": int(item["result_id"]),
            "unique_union_hit_cells": int(item["production_union_hit_count"]),
            "valid_cut_count": int(item["valid_cut_count"]),
            "audit_status": item["status"],
            "audit_manifest": str(audit_manifest_path.resolve()),
            "audit_json": str(reference_path.resolve()),
            "branch_manifest": audit_manifest.get("branch_manifest"),
        })
    if len(rows) != 3 or any(row["audit_status"] != "passed" for row in rows):
        raise ValueError(f"expected three passing CFAR union rows: {reference_path}")
    return rows


def _cfar_eval_csv(
    source_path: Path, union_rows: Sequence[Mapping[str, Any]], output_path: Path,
) -> None:
    rows = _load_csv(source_path)
    for union in union_rows:
        rows.append({
            "schema_version": "1",
            "period_id": str(union["period_id"]),
            "result_id": str(union["result_id"]),
            "beam_id": "50",
            "branch": "unique_union_audit",
            "hit_cut_count": "",
            "unique_union_hit_cells": str(union["unique_union_hit_cells"]),
            "valid_cut_count": str(union["valid_cut_count"]),
            "hit_rate_status": "evaluable",
        })
    write_csv(output_path, rows)


def build_task_manifest(
    *, case_root: Path, case: Mapping[str, Any], condition: str,
    role: str, union_rows: Sequence[Mapping[str, Any]], output_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    branch = _branch(case, condition, role)
    result_dir = Path(str(branch["result_dir"])).resolve()
    track_dir = Path(str(branch["track_debug_dir"])).resolve()
    lineage_path = result_dir / "debug/cluster_detection_lineage.csv"
    cfar_path = result_dir / "debug/cfar_geometry_diagnostics.csv"
    detections = [Path(str(row["path"])).resolve() for row in branch.get("cycle_rows", [])]
    if len(detections) != 3 or any(not path.is_file() for path in detections):
        raise ValueError(f"three production detection CSVs are required: {condition}/{role}")
    required = [lineage_path, cfar_path, track_dir / "track_frames.csv", track_dir / "track_detections.csv", track_dir / "track_output_payloads.csv"]
    if any(not path.is_file() for path in required):
        raise FileNotFoundError(f"required Production evidence is missing for {condition}/{role}")
    # Stage2 truth is common to paired role runs and is stored under the ON scene.
    truth = _truth_path(case, condition)
    if not truth.is_file():
        raise FileNotFoundError(f"Stage2 truth is missing for {condition}: {truth}")
    cfar_eval_path = output_dir / "cfar_geometry_with_unique_union.csv"
    _cfar_eval_csv(cfar_path, union_rows, cfar_eval_path)
    result_to_period = {
        f"GMTI{int(row['result_id']):02d}": int(row["period_id"])
        for row in branch.get("cycle_rows", [])
    }
    manifest: dict[str, Any] = {
        "seed": int(case.get("scene_identity", {}).get("seed", -1)),
        "condition": condition,
        "mode": role,
        "truth_csv": [str(truth.resolve())] if role != "OFF" else [],
        "detection_csvs": [str(path) for path in detections],
        "lineage_csvs": [str(lineage_path)],
        "cfar_geometry_csv": [str(cfar_eval_path.resolve())],
        "track_detections_csvs": [str((track_dir / "track_detections.csv").resolve())],
        "track_payload_csvs": [str((track_dir / "track_output_payloads.csv").resolve())],
        "track_frames_csv": [str((track_dir / "track_frames.csv").resolve())],
        "result_to_period": result_to_period,
        "doppler_rows": 130,
        "production_xml": str(Path(str(branch["production_xml"])).resolve()),
        "production_log": str(Path(str(branch["production_log"])).resolve()),
        "source_case_manifest": str((case_root / "case_manifest.json").resolve()),
        "unique_union_hit_rows": list(union_rows),
    }
    response_path: Path | None = None
    if role == "TO":
        response = r7_production_eval._target_only_response(result_dir, truth)
        response_path = output_dir / "target_only_response.json"
        write_json(response_path, response)
        manifest["target_only_response_json"] = str(response_path.resolve())
    manifest_path = output_dir / "evaluation_manifest.json"
    write_json(manifest_path, manifest)
    provenance = {
        "manifest": str(manifest_path.resolve()),
        "result_dir": str(result_dir),
        "track_debug_dir": str(track_dir),
        "truth_csv": str(truth.resolve()) if role != "OFF" else None,
        "detection_csvs": [str(path) for path in detections],
        "lineage_csv": str(lineage_path.resolve()),
        "cfar_geometry_csv": str(cfar_eval_path.resolve()),
        "target_only_response_json": str(response_path.resolve()) if response_path else None,
    }
    return manifest, provenance


def _evaluate_new_seed(
    seed_root: Path, output_dir: Path,
) -> tuple[dict[str, Any], dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    case_root = _seed_case_root(seed_root)
    case = read_json(case_root / "case_manifest.json")
    if case.get("status") != "completed":
        raise ValueError(f"R8 seed case is not completed: {case_root}")
    audit_map = _audit_manifests(seed_root)
    branch_gates = {}
    for condition in CONDITIONS.values():
        for role in ROLES:
            branch = _branch(case, condition, role)
            gate = branch.get("production_path_gate", {})
            branch_gates[f"{condition}/{role}"] = (
                branch.get("status") == "passed"
                and branch.get("production_status") == "passed"
                and branch.get("production_returncode") == 0
                and isinstance(gate, Mapping)
                and gate.get("status") == "passed"
            )
    audit_gates = {
        f"{condition}/{role}": (condition, role) in audit_map
        for condition in CONDITIONS.values()
        for role in ("ON", "OFF")
    }
    processing_chain_consistency = _operator_config_gates(case)
    evaluated: dict[tuple[str, str], dict[str, Any]] = {}
    provenance: list[dict[str, Any]] = []
    for label, condition in CONDITIONS.items():
        for role in ROLES:
            audit_entry = audit_map.get((condition, role)) if role in {"ON", "OFF"} else None
            union_rows: list[dict[str, Any]] = []
            audit_status = "NOT_REQUIRED_FOR_TARGET_ONLY" if role == "TO" else "NOT_EVALUABLE"
            if audit_entry:
                audit_path, audit_manifest = audit_entry
                union_rows = _audit_union_rows(audit_path, audit_manifest)
                audit_status = "passed"
            elif role in {"ON", "OFF"}:
                raise FileNotFoundError(f"passing CUDA CFAR audit is missing: seed={case.get('scene_identity', {}).get('seed')} {condition}/{role}")

            case_output = output_dir / f"seed{case['scene_identity']['seed']}" / f"{label}_{role}"
            manifest, case_provenance = build_task_manifest(
                case_root=case_root, case=case, condition=condition, role=role,
                union_rows=union_rows, output_dir=case_output,
            )
            result = task_eval.evaluate_manifest(case_output / "evaluation_manifest.json")
            write_json(case_output / "task_metrics.json", result)
            task_eval._write_csv(
                case_output / "associations.csv",
                result["association_evidence"] + result["candidate_pair_evidence"],
            )
            task_eval._write_csv(case_output / "lineage.csv", result["lineage_evidence"])
            case_provenance.update({
                "audit_status": audit_status,
                "cfar_union_rows": union_rows,
                "task_metrics": str((case_output / "task_metrics.json").resolve()),
                "associations": str((case_output / "associations.csv").resolve()),
                "lineage_metrics": str((case_output / "lineage.csv").resolve()),
            })
            provenance.append({"seed": case["scene_identity"]["seed"], "condition": label, "role": role, **case_provenance})
            evaluated[(label, role)] = result
    csi = r3_eval.evaluate_case(case_root)
    csi_path = output_dir / f"seed{case['scene_identity']['seed']}" / "r3_csi_transfer_metrics.json"
    write_json(csi_path, csi)
    execution_gates = {
        "stage2_area_clutter": case.get("area_clutter_gate", {}).get("status") == "passed",
        "historical_input_identity": "NOT_EVALUATED_YET",
        "known_correction_physical_closure": case.get("known_correction_physical_closure", {}).get("status") == "passed",
        "additive_audits": all(item.get("status") == "passed" for item in case.get("additive_audits", {}).values()),
        "production_branches": all(branch_gates.values()),
        "production_branch_gates": branch_gates,
        "cfar_numeric_audits": all(audit_gates.values()),
        "cfar_audit_gates": audit_gates,
        "processing_chain_consistency": processing_chain_consistency["status"] == "passed",
        "processing_chain_details": processing_chain_consistency,
    }
    return {
        "seed": int(case["scene_identity"]["seed"]),
        "case_root": str(case_root),
        "case_manifest": str((case_root / "case_manifest.json").resolve()),
        "case_manifest_status": case.get("status"),
        "area_clutter_gate": case.get("area_clutter_gate"),
        "paired_background": case.get("paired_background"),
        "input_fusion_power": csi.get("input_fusion_power"),
        "known_correction_physical_closure": case.get("known_correction_physical_closure"),
        "additive_audits": case.get("additive_audits"),
        "processing_chain_consistency": processing_chain_consistency,
        "execution_gates": execution_gates,
        "conditions": {
            label: {
                "r3_csi": csi.get("conditions", {}).get(label, {}),
                "input_fusion_power": csi.get("input_fusion_power", {}).get(label, {}),
                "roles": {role: evaluated[(label, role)] for role in ROLES},
            }
            for label in CONDITIONS
        },
        "csi_summary": str(csi_path.resolve()),
    }, evaluated, provenance


def _r7_seed101(
    r7_task_manifest: Path, seed101_case_root: Path, output_dir: Path,
) -> tuple[dict[str, Any], dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    r7_doc = read_json(r7_task_manifest)
    original_case = read_json(seed101_case_root / "case_manifest.json")
    results: dict[tuple[str, str], dict[str, Any]] = {}
    provenance: list[dict[str, Any]] = []
    branch_paths: dict[str, dict[str, Any]] = {}
    for label, r7_condition in R7_CONDITIONS.items():
        for role in ROLES:
            key = f"{r7_condition}_{role}"
            post_fix = r7_doc["cases"][key]["post_fix"]
            source_manifest = Path(str(post_fix["provenance"]["evaluation_manifest"])).resolve()
            result = task_eval.evaluate_manifest(source_manifest)
            case_output = output_dir / "seed101" / f"{label}_{role}"
            case_output.mkdir(parents=True, exist_ok=True)
            write_json(case_output / "task_metrics.json", result)
            task_eval._write_csv(
                case_output / "associations.csv",
                result["association_evidence"] + result["candidate_pair_evidence"],
            )
            task_eval._write_csv(case_output / "lineage.csv", result["lineage_evidence"])
            branch_paths.setdefault(label, {})[role] = {
                "result_dir": post_fix["provenance"]["result_dir"],
            }
            provenance.append({
                "seed": 101, "condition": label, "role": role,
                "source_r7_evaluation_manifest": str(source_manifest),
                "source_r7_binary_sha256": r7_doc.get("post_fix_binary_sha256"),
                "unique_union_status": result["metrics"].get("unique_union_hit_cells"),
                "task_metrics": str((case_output / "task_metrics.json").resolve()),
            })
            results[(label, role)] = result

    r7_truth = Path(str(r7_doc["cases"]["A0_ON"]["post_fix"]["provenance"]["truth_csv"])).resolve()
    original_case = json.loads(json.dumps(original_case))
    original_case.setdefault("simulation", {}).setdefault("A0_ON", {})["output_dir"] = str(r7_truth.parent.parent)
    csi_conditions: dict[str, Any] = {}
    for label, condition in CONDITIONS.items():
        roles = branch_paths[label]
        transfer = r3_eval._target_transfer(seed101_case_root, original_case, condition, roles)
        off_taps = r3_eval._tap_rows(roles["OFF"])
        per_period = []
        for period in sorted(off_taps):
            arrays = r3_eval._tap_arrays(off_taps[period])
            row = arrays["row"]
            per_period.append(r3_eval.active_support_csi_power(
                arrays["before_power"], arrays["csi_after"].real,
                arrays["csi_after"].imag,
                row_start=int(row["az_st"]), row_end=int(row["az_ed"]),
                col_start=int(row["rg_st"]), col_end=int(row["rg_ed"]),
            ))
        before = sum(item.get("before_mean_power", 0.0) * item.get("valid_cells", 0) for item in per_period if item.get("status") == "evaluable")
        after = sum(item.get("csi_output_mean_power", 0.0) * item.get("valid_cells", 0) for item in per_period if item.get("status") == "evaluable")
        cells = sum(item.get("valid_cells", 0) for item in per_period if item.get("status") == "evaluable")
        csi_conditions[label] = {
            "csi_h0_active_support": {
                "status": "evaluable" if cells and before > 0 and after > 0 else "NOT_EVALUABLE",
                "period_count": len(per_period), "valid_cells": cells,
                "before_mean_power": before / cells if cells else None,
                "csi_output_mean_power": after / cells if cells else None,
                "cancellation_db": 10.0 * math.log10(before / after) if before > 0 and after > 0 else None,
                "per_period": per_period,
            },
            "target_transfer": transfer,
            "source": "R7 corrected Native CUDA Production CSI taps; R7 Stage2 truth",
        }
    csi_path = output_dir / "seed101" / "r7_csi_transfer_metrics.json"
    write_json(csi_path, csi_conditions)
    input_power_table = r3_eval._input_power_table(original_case, original_case.get("protocol_layout", {}))
    input_power_table = {label: input_power_table.get(label, {}) for label in CONDITIONS}
    r7_task_gates = {
        f"{label}/{role}": results[(label, role)].get("metrics", {}).get("association_status") == "evaluable"
        if role == "ON" else results[(label, role)].get("metrics", {}).get("lineage_status") == "complete"
        for label in CONDITIONS for role in ROLES
    }
    return {
        "seed": 101,
        "case_root": str(seed101_case_root.resolve()),
        "r7_task_metrics_manifest": str(r7_task_manifest.resolve()),
        "production_binary_sha256": r7_doc.get("post_fix_binary_sha256"),
        "area_clutter_gate": original_case.get("area_clutter_gate"),
        "paired_background": original_case.get("paired_background"),
        "input_fusion_power": input_power_table,
        "known_correction_physical_closure": original_case.get("known_correction_physical_closure"),
        "execution_gates": {
            "source_r7_binary_identity": r7_doc.get("post_fix_binary_sha256") == EXPECTED_BINARY_SHA256,
            "area_clutter_gate": original_case.get("area_clutter_gate", {}).get("status") == "passed",
            "known_correction_physical_closure": original_case.get("known_correction_physical_closure", {}).get("status") == "passed",
            "task_evaluation": all(r7_task_gates.values()),
            "task_gates": r7_task_gates,
            "unique_union_maps": "NOT_EVALUABLE_R7_MAPS_NOT_SAVED",
        },
        "conditions": {
            label: {
                "r3_csi": csi_conditions[label],
                "input_fusion_power": input_power_table.get(label, {}),
                "roles": {role: results[(label, role)] for role in ROLES},
            }
            for label in CONDITIONS
        },
        "csi_summary": str(csi_path.resolve()),
    }, results, provenance


def _historical_input_comparison(seed_root: Path, case_root: Path, r3_root: Path) -> dict[str, Any]:
    seed = int(read_json(case_root / "case_manifest.json")["scene_identity"]["seed"])
    historic_root = r3_root / f"replication_seed_{seed}_cuda" / "cases"
    historical_manifests = list(historic_root.glob("*/case_manifest.json"))
    if len(historical_manifests) != 1:
        return {"status": "NOT_EVALUABLE", "reason": f"historical R3 case manifest missing/ambiguous for seed {seed}"}
    current_case = read_json(case_root / "case_manifest.json")
    historical_case = read_json(historical_manifests[0])
    rows = []
    matched = 0
    for label, condition in CONDITIONS.items():
        for role in ROLES:
            current_branch = _branch(current_case, condition, role)
            historical_branch = _branch(historical_case, condition, role)
            current_paths = current_branch.get("period_paths", [])
            historical_paths = historical_branch.get("period_paths", [])
            current_by_period = {index: item for index, item in enumerate(current_paths)}
            historical_by_period = {index: item for index, item in enumerate(historical_paths)}
            if len(current_by_period) != 3 or len(historical_by_period) != 3:
                rows.append({"seed": seed, "condition": label, "role": role, "status": "missing_periods"})
                continue
            for period in range(3):
                current, historical = current_by_period[period], historical_by_period[period]
                same = current.get("sha256") == historical.get("sha256") and current.get("bytes") == historical.get("bytes")
                matched += int(same)
                rows.append({
                    "seed": seed, "condition": label, "role": role,
                    "period_id": period,
                    "current_path": current.get("path"),
                    "current_bytes": current.get("bytes"),
                    "current_sha256": current.get("sha256"),
                    "historical_path": historical.get("path"),
                    "historical_bytes": historical.get("bytes"),
                    "historical_sha256": historical.get("sha256"),
                    "matches": same,
                    "status": "matched" if same else "mismatch",
                })
    return {
        "status": "HISTORICAL_INPUT_RECONSTRUCTED_AND_VERIFIED" if matched == 27 and len(rows) == 27 else "NEW_PAIRED_SCENE_OR_MISMATCH",
        "seed": seed, "matched_period_hashes": matched, "expected_period_hashes": 27,
        "historical_case_manifest": str(historical_manifests[0].resolve()),
        "rows": rows,
    }


def _target_local_rows(
    *, seed: int, label: str, condition: str, role: str,
    case: Mapping[str, Any], branch: Mapping[str, Any],
    audit_manifest_path: Path | None, task_result: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if audit_manifest_path is None:
        return [{
            "seed": seed, "condition": label, "role": role,
            "status": "NOT_EVALUABLE_R7_DIAGNOSTIC_MAPS_NOT_SAVED",
        }]
    audit_manifest = read_json(audit_manifest_path)
    numeric = read_json(audit_manifest_path.parent / "cfar_reference_audit.json")
    if audit_manifest.get("status") != "passed" or numeric.get("status") != "passed":
        return [{"seed": seed, "condition": label, "role": role, "status": "NOT_EVALUABLE_AUDIT_FAILED"}]
    truth_path = _truth_path(case, condition)
    truths = [row for row in _load_csv(truth_path) if row.get("visible", "1").lower() not in {"0", "false"}]
    result_dir = Path(str(audit_manifest["result_dir"]))
    debug = result_dir / "debug"
    lineage_path = Path(str(branch["result_dir"])) / "debug/cluster_detection_lineage.csv"
    lineage = _load_csv(lineage_path)
    axes = task_eval._doppler_axis_map(lineage, [], 130)
    associations = (task_result or {}).get("association_evidence", [])
    union = {int(row["period_id"]): row for row in numeric.get("union_period_rows", [])}
    rows: list[dict[str, Any]] = []
    for truth in truths:
        period, beam = int(truth["period_id"]), int(truth["beam_id"])
        axis = axes.get((period, beam))
        expected_row = task_eval.expected_doppler_row(
            truth, {"period_id": period, "beam_id": beam}, 130, axis,
        )
        col = int(truth["range_bin"])
        if expected_row is None:
            rows.append({"seed": seed, "condition": label, "role": role, "period_id": period, "target_id": truth.get("target_id"), "status": "NOT_EVALUABLE_DOPPLER_AXIS"})
            continue
        maps = {
            name: cfar_audit.read_f32_map(
                cfar_audit._map_path(debug, period + 1, "", name), 130, 4096,
            )
            for name in ("power", "threshold", "hits")
        }
        local = target_neighborhood_metrics(
            maps["power"], maps["threshold"], maps["hits"],
            center_row=expected_row, center_col=col,
        )
        local_clusters = []
        candidate_clusters = []
        for cluster in lineage:
            if str(cluster.get("record_type", "cluster")).lower() in {"beam_summary", "summary"}:
                continue
            if int(float(cluster.get("period_id", -1))) != period or int(float(cluster.get("beam_id", -1))) != beam:
                continue
            cluster_row = int(float(cluster["cluster_row"]))
            cluster_col = int(float(cluster["cluster_col"]))
            signed_doppler_delta = ((cluster_row - expected_row + 65) % 130) - 65
            range_delta = cluster_col - col
            normalized_residual = math.hypot(range_delta / 2.0, signed_doppler_delta / 2.0)
            candidate = {
                "range_delta_bins": range_delta,
                "doppler_delta_bins_signed": signed_doppler_delta,
                "normalized_residual": normalized_residual,
                "row": cluster_row,
                "col": cluster_col,
                "selected": str(cluster.get("selected", "0")).lower() in {"1", "true"},
                "raw_detection": str(cluster.get("raw_detection", "0")).lower() in {"1", "true"},
                "within_frozen_gate": abs(range_delta) <= 2 and abs(signed_doppler_delta) <= 2,
            }
            candidate_clusters.append(candidate)
            if candidate["within_frozen_gate"]:
                local_clusters.append(cluster)
        nearest_cluster = min(candidate_clusters, key=lambda item: item["normalized_residual"]) if candidate_clusters else None

        detection_candidates = []
        for cycle in branch.get("cycle_rows", []):
            if int(cycle["period_id"]) != period:
                continue
            for detection in _load_csv(Path(str(cycle["path"]))):
                detection_row = int(float(detection["row"]))
                detection_col = int(float(detection["range_bin"]))
                signed_doppler_delta = ((detection_row - expected_row + 65) % 130) - 65
                range_delta = detection_col - col
                detection_candidates.append({
                    "det_id": detection.get("det_id"),
                    "row": detection_row,
                    "col": detection_col,
                    "cfar_margin_db": float(detection["cfar_margin_db"]) if detection.get("cfar_margin_db") else None,
                    "range_delta_bins": range_delta,
                    "doppler_delta_bins_signed": signed_doppler_delta,
                    "normalized_residual": math.hypot(range_delta / 2.0, signed_doppler_delta / 2.0),
                    "within_frozen_gate": abs(range_delta) <= 2 and abs(signed_doppler_delta) <= 2,
                })
        nearest_detection = min(detection_candidates, key=lambda item: item["normalized_residual"]) if detection_candidates else None
        association = next((
            item for item in associations
            if int(item.get("period_id", -1)) == period
            and str(item.get("target_id")) == str(truth.get("target_id"))
        ), None)
        detection_matched = bool(association and association.get("matched"))
        track_matched = bool(detection_matched and association.get("track_id"))
        rows.append({
            "seed": seed, "condition": label, "role": role,
            "period_id": period, "beam_id": beam,
            "target_id": truth.get("target_id"),
            "expected_doppler_row": expected_row,
            "truth_range_bin": col,
            **local,
            "unique_union_hit_count_full_map": union.get(period, {}).get("production_union_hit_count"),
            "local_cluster_count": len(local_clusters),
            "local_selected_cluster_count": sum(str(item.get("selected", "0")).lower() in {"1", "true"} for item in local_clusters),
            "local_raw_detection_cluster_count": sum(str(item.get("raw_detection", "0")).lower() in {"1", "true"} for item in local_clusters),
            "nearest_cluster_range_delta_bins": nearest_cluster.get("range_delta_bins") if nearest_cluster else None,
            "nearest_cluster_doppler_delta_bins": nearest_cluster.get("doppler_delta_bins_signed") if nearest_cluster else None,
            "nearest_cluster_normalized_residual": nearest_cluster.get("normalized_residual") if nearest_cluster else None,
            "nearest_cluster_selected": nearest_cluster.get("selected") if nearest_cluster else None,
            "nearest_cluster_raw_detection": nearest_cluster.get("raw_detection") if nearest_cluster else None,
            "nearest_cluster_within_frozen_gate": nearest_cluster.get("within_frozen_gate") if nearest_cluster else None,
            "nearest_detection_id": nearest_detection.get("det_id") if nearest_detection else None,
            "nearest_detection_range_delta_bins": nearest_detection.get("range_delta_bins") if nearest_detection else None,
            "nearest_detection_doppler_delta_bins": nearest_detection.get("doppler_delta_bins_signed") if nearest_detection else None,
            "nearest_detection_normalized_residual": nearest_detection.get("normalized_residual") if nearest_detection else None,
            "nearest_detection_cfar_margin_db": nearest_detection.get("cfar_margin_db") if nearest_detection else None,
            "nearest_detection_within_frozen_gate": nearest_detection.get("within_frozen_gate") if nearest_detection else None,
            "final_target_detection_matched": detection_matched,
            "confirmed_current_track_matched": track_matched,
            "target_loss_stage": classify_target_loss_stage(
                local["hit_count"], len(local_clusters), detection_matched, track_matched,
                nearest_candidate_outside_gate=bool(
                    (nearest_cluster and not nearest_cluster["within_frozen_gate"])
                    or (nearest_detection and not nearest_detection["within_frozen_gate"])
                ),
            ),
            "stage_attribution_scope": "truth-centered spatial neighborhood and frozen R7 target association; descriptive per scene/period",
            "status": "evaluable",
        })
    return rows


def _metrics_row(seed: int, label: str, csi: Mapping[str, Any], roles: Mapping[str, Any]) -> dict[str, Any]:
    on = roles["ON"]["metrics"]
    off = roles["OFF"]["metrics"]
    transfer = csi.get("target_transfer", {})
    coef = transfer.get("paired_complex_transfer", {})
    parameter = on.get("parameter_errors", {})
    radial = parameter.get("radial_velocity", {})
    union_rows = roles["OFF"].get("unique_union_hit_rows", [])
    row = {
        "seed": seed, "condition": label,
        "csi_h0_cancellation_db": csi.get("csi_h0_active_support", {}).get("cancellation_db"),
        "output_scnr_proxy_db": transfer.get("output_scnr_proxy_db"),
        "target_only_csi_retention_db": transfer.get("target_only_csi_retention_db"),
        "target_amplitude_ratio_db": coef.get("amplitude_ratio_db"),
        "target_phase_error_deg": coef.get("phase_error_deg"),
        "target_detections": on.get("unique_correct_detections"),
        "target_opportunities": on.get("truth_opportunities"),
        "target_detection_rate": on.get("detection_rate"),
        "target_misses": on.get("misses"),
        "on_final_detections": on.get("final_detections"),
        "on_non_target_detections": on.get("false_associations"),
        "correct_track_associations": on.get("correct_track_associations"),
        "unassociated_confirmed_tracks": on.get("false_track_associations"),
        "on_confirmed_tracks": on.get("confirmed_tracks"),
        "cfar_branch_hit_cells_on": on.get("cfar_branch_hit_cells"),
        "cfar_branch_hit_cells_off": off.get("cfar_branch_hit_cells"),
        "false_h0_clusters": off.get("false_clusters"),
        "false_h0_detections": off.get("false_detections"),
        "false_h0_confirmed_tracks": off.get("false_confirmed_tracks"),
        "h0_unique_union_hit_cells_total": sum(int(row["unique_union_hit_cells"]) for row in union_rows) if union_rows else None,
        "angle_rmse_deg": parameter.get("angle_rmse_deg"),
        "ground_speed_rmse_mps": parameter.get("ground_speed_rmse_mps"),
        "position_rmse_m": parameter.get("position_2d_rmse_m"),
        "radial_velocity_rmse_mps": radial.get("rmse_mps"),
        "radial_velocity_status": radial.get("status"),
    }
    for role in ("ON", "OFF", "TO"):
        role_power = csi.get("input_fusion_power", {}).get(role, {})
        role_delta = role_power.get("relative_to_A0_dB", {})
        suffix = role.lower()
        for channel in ("F1", "F2"):
            row[f"input_{channel.lower()}_mean_power_{suffix}"] = role_power.get(f"{channel}_mean_power")
            row[f"input_{channel.lower()}_delta_vs_A0_db_{suffix}"] = role_delta.get(channel)
    return row


def _write_comparison_figure(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    seeds = sorted({int(row["seed"]) for row in rows})
    panels = [
        ("target_detection_rate", "Target detection rate (per-seed opportunities)"),
        ("output_scnr_proxy_db", "Output SCNR proxy (dB)"),
        ("target_amplitude_ratio_db", "Paired target transfer amplitude (dB)"),
        ("target_phase_error_deg", "Paired target transfer phase error (deg)"),
        ("target_only_csi_retention_db", "TO target power retention through CSI (dB)"),
        ("angle_rmse_deg", "Correctly associated target angle RMSE (deg)"),
        ("ground_speed_rmse_mps", "Confirmed-track ground-speed RMSE (m/s)"),
        ("position_rmse_m", "Correctly associated target position RMSE (m)"),
    ]
    fig, axes = plt.subplots(2, 4, figsize=(16.0, 7.2))
    for axis, (key, title) in zip(axes.flat, panels):
        for seed in seeds:
            by_condition = {
                str(row["condition"]): row.get(key)
                for row in rows if int(row["seed"]) == seed
            }
            values = [by_condition.get(label) for label in ("A", "B", "C")]
            xs = list(range(3))
            ys = [
                float(value) if value is not None and math.isfinite(float(value)) else math.nan
                for value in values
            ]
            axis.plot(xs, ys, marker="o", label=f"seed {seed}")
        axis.set_xticks([0, 1, 2], ["A0", "B +4 ns", "C known correction"])
        axis.set_title(title, fontsize=9)
        axis.grid(True, alpha=0.3)
    axes[0, 0].legend(fontsize=8)
    fig.suptitle("R8 native CUDA production pilot — descriptive, not significance-tested")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def run_evaluation(
    *, seed_roots: Sequence[Path], seed101_case_root: Path,
    r7_task_manifest: Path, r3_root: Path, output_dir: Path,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    seed_records: dict[int, dict[str, Any]] = {}
    task_results: dict[int, dict[tuple[str, str], dict[str, Any]]] = {}
    provenance: list[dict[str, Any]] = []
    input_comparisons = []

    seed101, task101, provenance101 = _r7_seed101(r7_task_manifest.resolve(), seed101_case_root.resolve(), output_dir)
    seed_records[101] = seed101
    task_results[101] = task101
    provenance.extend(provenance101)

    for seed_root in seed_roots:
        seed_root = seed_root.resolve()
        case_root = _seed_case_root(seed_root)
        record, results, case_provenance = _evaluate_new_seed(seed_root, output_dir)
        seed = int(record["seed"])
        if seed not in {202, 303} or seed in seed_records:
            raise ValueError(f"R8 new seed must be unique and one of 202/303: {seed}")
        seed_records[seed] = record
        task_results[seed] = results
        provenance.extend(case_provenance)
        historical = _historical_input_comparison(seed_root, case_root, r3_root)
        record["historical_input_comparison"] = {
            key: value for key, value in historical.items() if key != "rows"
        }
        record["execution_gates"]["historical_input_identity"] = (
            historical.get("status") == "HISTORICAL_INPUT_RECONSTRUCTED_AND_VERIFIED"
        )
        input_comparisons.append(historical)
        write_json(output_dir / f"seed{seed}" / "historical_input_comparison.json", historical)
        write_csv(output_dir / f"seed{seed}" / "historical_input_comparison.csv", historical.get("rows", []))

    if set(seed_records) != {101, 202, 303}:
        raise ValueError(f"expected reused seed101 and new seeds202/303; found {sorted(seed_records)}")

    local_rows: list[dict[str, Any]] = []
    for seed in (202, 303):
        seed_root = next(path.resolve() for path in seed_roots if int(read_json(_seed_case_root(path.resolve()) / "case_manifest.json")["scene_identity"]["seed"]) == seed)
        case_root = _seed_case_root(seed_root)
        case = read_json(case_root / "case_manifest.json")
        audits = _audit_manifests(seed_root)
        for label, condition in CONDITIONS.items():
            for role in ("ON", "OFF"):
                entry = audits.get((condition, role))
                if entry is None:
                    local_rows.extend(_target_local_rows(
                        seed=seed, label=label, condition=condition, role=role,
                        case=case, branch=_branch(case, condition, role),
                        audit_manifest_path=None, task_result=task_results[seed][(label, role)],
                    ))
                else:
                    local_rows.extend(_target_local_rows(
                        seed=seed, label=label, condition=condition, role=role,
                        case=case, branch=_branch(case, condition, role),
                        audit_manifest_path=entry[0], task_result=task_results[seed][(label, role)],
                    ))
    for label, condition in CONDITIONS.items():
        for role in ("ON", "OFF"):
            local_rows.extend(_target_local_rows(
                seed=101, label=label, condition=condition, role=role,
                case=read_json(seed101_case_root / "case_manifest.json"),
                branch={}, audit_manifest_path=None,
                task_result=task_results[101][(label, role)],
            ))
    write_csv(output_dir / "target_neighborhood_cfar.csv", local_rows)

    metric_rows = []
    for seed in (101, 202, 303):
        record = seed_records[seed]
        for label in CONDITIONS:
            csi = dict(record["conditions"][label]["r3_csi"])
            csi["input_fusion_power"] = record["conditions"][label].get("input_fusion_power", {})
            roles = {role: task_results[seed][(label, role)] for role in ROLES}
            row = _metrics_row(seed, label, csi, roles)
            if seed in {202, 303}:
                off_audit = _audit_manifests(next(path.resolve() for path in seed_roots if int(read_json(_seed_case_root(path.resolve()) / "case_manifest.json")["scene_identity"]["seed"]) == seed)).get((CONDITIONS[label], "OFF"))
                if off_audit:
                    audit_path, audit_manifest = off_audit
                    union = _audit_union_rows(audit_path, audit_manifest)
                    row["h0_unique_union_hit_cells_total"] = sum(int(item["unique_union_hit_cells"]) for item in union)
                    row["h0_union_valid_cut_count_total"] = sum(int(item["valid_cut_count"]) for item in union)
                on_audit = _audit_manifests(next(path.resolve() for path in seed_roots if int(read_json(_seed_case_root(path.resolve()) / "case_manifest.json")["scene_identity"]["seed"]) == seed)).get((CONDITIONS[label], "ON"))
                if on_audit:
                    on_union = _audit_union_rows(*on_audit)
                    row["h1_unique_union_hit_cells_total"] = sum(int(item["unique_union_hit_cells"]) for item in on_union)
                    row["h1_union_valid_cut_count_total"] = sum(int(item["valid_cut_count"]) for item in on_union)
            else:
                row["h0_unique_union_hit_cells_total"] = None
                row["h0_union_valid_cut_count_total"] = None
                row["h1_unique_union_hit_cells_total"] = None
                row["h1_union_valid_cut_count_total"] = None
            metric_rows.append(row)
    write_csv(output_dir / "r8_seed_condition_metrics.csv", metric_rows)
    _write_comparison_figure(output_dir / "r8_seed_condition_comparison.png", metric_rows)

    # Make compact per-seed/per-condition branch summaries alongside detailed audits.
    for seed, record in seed_records.items():
        for label in CONDITIONS:
            for role in ROLES:
                task = task_results[seed][(label, role)]
                path = output_dir / f"seed{seed}" / f"{label}_{role}" / "task_metrics.json"
                if not path.exists():
                    write_json(path, task)

    seed101_gates = seed_records[101]["execution_gates"]
    new_seed_gates = [seed_records[seed]["execution_gates"] for seed in (202, 303)]
    executed = (
        seed101_gates["source_r7_binary_identity"]
        and all(gates["production_branches"] for gates in new_seed_gates)
    )
    evaluable = (
        executed
        and seed101_gates["area_clutter_gate"]
        and seed101_gates["known_correction_physical_closure"]
        and seed101_gates["task_evaluation"]
        and all(
            gates["stage2_area_clutter"]
            and gates["historical_input_identity"]
            and gates["known_correction_physical_closure"]
            and gates["additive_audits"]
            and gates["cfar_numeric_audits"]
            and gates["processing_chain_consistency"]
            for gates in new_seed_gates
        )
    )
    campaign = {
        "schema_version": 1,
        "experiment": "PGRCC Phase R8 multi-seed Native CUDA causal pilot",
        "created_by": str(Path(__file__).resolve()),
        "production_binary_sha256": EXPECTED_BINARY_SHA256,
        "corex": "not used; native CUDA/nvcc local production only",
        "seeds": [seed_records[seed] for seed in (101, 202, 303)],
        "seed_condition_metrics": metric_rows,
        "target_neighborhood_cfar": str((output_dir / "target_neighborhood_cfar.csv").resolve()),
        "evaluation_provenance": provenance,
        "historical_input_comparisons": input_comparisons,
        "statistical_significance_claimed": False,
        "periods_are_independent_samples": False,
        "status_labels": {
            "IMPLEMENTED": True,
            "EXECUTED": executed,
            "EVALUABLE": evaluable,
            "PERFORMANCE_VERIFIED": False,
            "PERFORMANCE_VERIFIED_reason": "three seeds are descriptive only; task changes are not declared statistically significant",
        },
    }
    experiment_manifest_path = output_dir / "r8_experiment_manifest.json"
    campaign["experiment_manifest"] = str(experiment_manifest_path.resolve())
    write_json(output_dir / "r8_multiseed_causal_evaluation.json", campaign)
    experiment_manifest = _build_experiment_manifest(
        seed_roots=seed_roots,
        seed101_case_root=seed101_case_root,
        r7_task_manifest=r7_task_manifest,
        output_dir=output_dir,
        campaign=campaign,
    )
    write_json(experiment_manifest_path, experiment_manifest)
    _normalize_csv_line_endings(output_dir)
    return campaign


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-root", action="append", required=True, type=Path)
    parser.add_argument("--seed101-case-root", type=Path, default=ROOT / "outputs/phase_r_minimal_cuda_causal_pilot_20261021/cases/base__seed_101__v_p6d7__snr_p30__delay_p4ns")
    parser.add_argument("--r7-task-manifest", type=Path, default=ROOT / "outputs/phase_r7_cfar_correctness_20261022/task_metrics_final_v4/evaluation_manifest.json")
    parser.add_argument("--r3-root", type=Path, default=ROOT / "outputs/phase_r_minimal_cuda_causal_pilot_20261021")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/phase_r8_unknown_delay_multiseed_20261023/evaluation")
    args = parser.parse_args()
    result = run_evaluation(
        seed_roots=args.seed_root,
        seed101_case_root=args.seed101_case_root,
        r7_task_manifest=args.r7_task_manifest,
        r3_root=args.r3_root,
        output_dir=args.output_dir,
    )
    print(json.dumps({
        "status_labels": result["status_labels"],
        "output_dir": str(args.output_dir.resolve()),
        "seed_condition_metric_rows": len(result["seed_condition_metrics"]),
        "target_neighborhood_rows": result["target_neighborhood_cfar"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
