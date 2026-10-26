#!/usr/bin/env python3
"""Re-evaluate retained R18 Production snapshots under the frozen R19 contract."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r.evaluate_r7_task_metrics import join_cluster_lineage  # noqa: E402
from scripts.phase_r.r19_task_evaluator import (
    CONTRACT_VERSION,
    audit_h0,
    common_success_keys,
    evaluate_targets,
    reference_role,
    rmse_from_samples,
    summarize_track_lineage,
    validate_comparison_identity,
)


CONDITIONS = (
    ("A0", "not_applicable"),
    ("C0", "not_applicable"),
    ("C1", "not_applicable"),
    ("C2", "Mode-A"),
    ("C2", "Mode-B"),
    ("C3", "Mode-A"),
    ("C3", "Mode-B"),
)
OPERATIONAL_GROUP = ("A0", "C0", "C1", "C2_Mode-B", "C3_Mode-B")
PRIVILEGED_GROUP = ("C2_Mode-A", "C3_Mode-A")
PARAMETER_NAMES = ("angle_deg", "position_m", "range_m", "folded_doppler_hz", "radial_velocity_mps", "ground_speed_mps")
R18_PRODUCTION_BINARY_SHA256 = "3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0"
R18_RELEASE_BUILD_COMMIT = "4c6d4d303621324f5e174db0ba1811c5691df2e2"
R18_RELEASE_BUILD_SOURCE = "docs/research/phase_r/R18/05_execution_and_quality_audit.md"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _load_final_detections(branch_dir: Path) -> list[dict[str, str]]:
    result_dir = branch_dir / "result"
    period_files = sorted(result_dir.glob("detection_results_GMTI*.csv"))
    if period_files:
        rows = [row for path in period_files for row in _csv(path)]
    else:
        aggregate = result_dir / "detection_results.csv"
        rows = _csv(aggregate) if aggregate.is_file() else []
    keys = [
        (str(row.get("period_id", "")).strip(), str(row.get("beam_id", "")).strip(),
         str(row.get("det_id", "")).strip())
        for row in rows
    ]
    if any(not all(key) for key in keys):
        raise ValueError(f"final Detection output has an incomplete period/beam/det_id key: {branch_dir}")
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate final Detection key across Production period files: {branch_dir}")
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: Any) -> int | None:
    parsed = _number(value)
    if parsed is None or not parsed.is_integer():
        return None
    return int(parsed)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _condition_label(method: str, mode: str) -> str:
    return method if mode == "not_applicable" else f"{method}_{mode}"


def _build_source_identity(snapshot_manifest: Mapping[str, Any]) -> dict[str, str | None]:
    build = snapshot_manifest.get("build", {})
    binary = build.get("GMTI_pipe_core", {})
    commit = binary.get("source_commit")
    source = "experiment_snapshot/manifest.json"
    if not commit and binary.get("sha256") == R18_PRODUCTION_BINARY_SHA256:
        commit = R18_RELEASE_BUILD_COMMIT
        source = R18_RELEASE_BUILD_SOURCE
    return {"commit": commit, "source": source if commit else None}


def _worktree_dirty(repo_root: Path) -> bool:
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo_root, text=True,
    )
    return bool(status.strip())


def _period_input_digest(branch_manifest: Mapping[str, Any]) -> str:
    paths = branch_manifest.get("period_paths", [])
    identity = [(str(row.get("bytes", "")), str(row.get("sha256", "")))
                for row in paths if isinstance(row, Mapping)]
    if not identity or any(not sha for _, sha in identity):
        raise ValueError("branch manifest lacks complete period input SHA identity")
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def _find_case(case_dir: Path) -> tuple[Path, dict[str, Any]]:
    manifests = sorted(case_dir.glob("experiment_snapshot/cases/*/case_manifest.json"))
    if len(manifests) != 1:
        raise ValueError(f"expected one case manifest under {case_dir}, found {len(manifests)}")
    return manifests[0].parent, _json(manifests[0])


def _load_condition_records(case_manifest: Mapping[str, Any], case_root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    roles = case_manifest.get("method_mode_roles", {})
    for logical_key, role_records in roles.items():
        for role in ("ON", "OFF", "TO"):
            record = role_records.get(role)
            if not isinstance(record, Mapping):
                continue
            method = str(record.get("method_id", ""))
            mode = str(record.get("mode", "not_applicable"))
            label = _condition_label(method, mode)
            branch_name = str(record.get("branch_name", ""))
            branch_dir = case_root / "production" / branch_name
            if not branch_name or not (branch_dir / "branch_manifest.json").is_file():
                raise ValueError(f"missing branch manifest for {logical_key}/{role}: {branch_name}")
            branch_manifest = _json(branch_dir / "branch_manifest.json")
            result[(label, role)] = {
                "condition": label,
                "method_id": method,
                "mode": mode,
                "role": role,
                "branch_name": branch_name,
                "branch_dir": branch_dir,
                "condition_record": dict(record),
                "branch_manifest": branch_manifest,
            }
    return result


def _validate_case_identity(
    *, case_manifest: Mapping[str, Any], snapshot_manifest: Mapping[str, Any],
    condition_records: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    case_info = case_manifest.get("case", {})
    scene = case_manifest.get("scene_identity", {})
    pair = case_manifest.get("pair_identity", {})
    source = snapshot_manifest.get("source", {})
    config = snapshot_manifest.get("config", {})
    build = snapshot_manifest.get("build", {})
    binary = build.get("GMTI_pipe_core", {}).get("sha256")
    if binary != R18_PRODUCTION_BINARY_SHA256:
        raise ValueError(f"snapshot Production binary differs from frozen R18 identity: {binary}")
    build_source = _build_source_identity(snapshot_manifest)
    reports: dict[str, Any] = {}
    for role in ("ON", "OFF", "TO"):
        identities = []
        for (condition, branch_role), branch in sorted(condition_records.items()):
            if branch_role != role:
                continue
            branch_manifest = branch["branch_manifest"]
            run_manifest_path = branch["branch_dir"] / "result/run_manifest.json"
            run_manifest = _json(run_manifest_path) if run_manifest_path.is_file() else {}
            branch_record = branch["condition_record"]
            input_key = branch_record.get("input_identity", {})
            identities.append({
                "case_id": case_manifest.get("case_id"),
                "seed": scene.get("seed", case_info.get("seed")),
                "target_velocity_mps": scene.get("target_velocity_mps", case_info.get("target_velocity_mps")),
                "target_snr_db": scene.get("target_snr_db", case_info.get("target_snr_db")),
                "delay_error_ns": case_info.get("delay_error_ns"),
                "config_sha256": config.get("sha256"),
                "production_binary_sha256": binary,
                "background_id": pair.get("background_id"),
                "target_id": pair.get("target_id"),
                "production_identity": pair.get("production_identity"),
                "input_identity": _period_input_digest(branch_manifest),
                "runner_commit": run_manifest.get("git_commit"),
                "condition": condition,
            })
        if len(identities) != len(CONDITIONS):
            reports[role] = {"status": "IDENTITY_MISMATCH", "branch_count": len(identities),
                             "expected_branch_count": len(CONDITIONS)}
            continue
        # run_manifest stores the short runner SHA; the snapshot manifest has the full source identity.
        for identity in identities:
            identity["runner_commit"] = source.get("commit")
        common = validate_comparison_identity(identities)
        reports[role] = {"status": "passed", "branch_count": len(identities), "common_identity": common}
    if any(row.get("status") != "passed" for row in reports.values()):
        raise ValueError(f"cross-condition execution identity validation failed: {reports}")
    return {
        "runner_source_commit": source.get("commit"),
        "runner_source_dirty": source.get("dirty"),
        "config_sha256": config.get("sha256"),
        "production_binary_sha256": binary,
        "build_source_commit": build_source["commit"],
        "build_source_commit_source": build_source["source"],
        "roles": reports,
    }


def _load_truth(case_root: Path, role: str) -> list[dict[str, str]]:
    variants = {"ON": ("A1_ON", "A0_ON"), "TO": ("A0_TO", "A1_TO")}
    for variant in variants.get(role, ()):
        path = case_root / "scenes" / variant / "truth/truth_targets_by_beam.csv"
        if path.is_file():
            return _csv(path)
    return []


def _target_free(case_root: Path) -> bool:
    scenario = case_root / "scenes/A0_OFF/scenario_resolved.json"
    if not scenario.is_file():
        return False
    targets = _json(scenario).get("targets", [])
    return bool(targets) and all(not _truthy(target.get("enabled")) for target in targets)


def _axis_rows(lineage_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in lineage_rows if str(row.get("record_type", "")).lower() in {"beam_summary", "summary"}]


def _lineage_status(lineage_rows: Sequence[Mapping[str, Any]], detections: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    try:
        joined = join_cluster_lineage(lineage_rows, detections)
    except (ValueError, KeyError) as exc:
        return {"status": "NOT_EVALUABLE", "reason": str(exc), "unjoined_final_detections": None}
    inconsistent = sum(row.get("lineage_stage") == "inconsistent_final_without_selected_raw"
                       for row in joined["clusters"])
    return {
        "status": "evaluable" if not joined["unjoined_detection_ids"] and not inconsistent else "INCOMPLETE",
        "cluster_rows": len(joined["clusters"]),
        "final_detections_joined": len(detections) - len(joined["unjoined_detection_ids"]),
        "unjoined_final_detections": len(joined["unjoined_detection_ids"]),
        "inconsistent_final_clusters": inconsistent,
    }


def _cfar_metrics(branch_dir: Path) -> dict[str, Any]:
    path = branch_dir / "result/debug/cfar_geometry_diagnostics.csv"
    rows = _csv(path) if path.is_file() else []
    by_branch: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_branch[str(row.get("branch", ""))].append(row)
    result: dict[str, Any] = {"status": "evaluable" if rows else "NOT_EVALUABLE"}
    for name, prefix in (("union_dynamic_in_band", "dynamic"), ("union_full_band", "full")):
        items = by_branch.get(name, [])
        hits = [_integer(row.get("hit_cut_count")) for row in items]
        valid = [_integer(row.get("valid_cut_count")) for row in items]
        periods = [_integer(row.get("period_id")) for row in items]
        legal = bool(items) and len(set(periods)) == len(periods) and all(
            h is not None and v is not None and v > 0 and 0 <= h <= v for h, v in zip(hits, valid)
        )
        result[f"{prefix}_status"] = "evaluable" if legal else "NOT_EVALUABLE"
        result[f"{prefix}_hit_count"] = sum(int(h) for h in hits) if legal else None
        result[f"{prefix}_valid_cut_count"] = sum(int(v) for v in valid) if legal else None
        result[f"{prefix}_cut_hit_fraction"] = (
            result[f"{prefix}_hit_count"] / result[f"{prefix}_valid_cut_count"] if legal else None
        )
        result[f"{prefix}_period_count"] = len(set(periods)) if items else 0
    result["alpha_values"] = sorted({float(row["alpha"]) for row in rows if _number(row.get("alpha")) is not None})
    result["configured_pfa_values"] = sorted({float(row["configured_pfa"]) for row in rows if _number(row.get("configured_pfa")) is not None})
    result["hit_index_hashes_available"] = bool(rows) and all(row.get("hit_index_hash") for row in rows)
    return result


def _cluster_counts(branch_dir: Path) -> dict[str, Any]:
    path = branch_dir / "result/debug/cluster_detection_lineage.csv"
    rows = _csv(path) if path.is_file() else []
    summaries = [row for row in rows if row.get("record_type") == "beam_summary"]
    return {
        "status": "evaluable" if summaries else "NOT_EVALUABLE",
        "period_count": len({row.get("period_id") for row in summaries}),
        "postfilter_clusters": sum(_integer(row.get("postfilter_cluster_count")) or 0 for row in summaries),
        "selected_candidates": sum(_integer(row.get("selected_candidate_count")) or 0 for row in summaries),
        "raw_detections": sum(_integer(row.get("raw_detection_count")) or 0 for row in summaries),
        "axis_rows": _axis_rows(rows),
    }


def _track_observations(branch_dir: Path) -> dict[str, Any]:
    debug_root = branch_dir / "track_debug_runs"
    run_dirs = sorted(path for path in debug_root.iterdir() if path.is_dir()) if debug_root.is_dir() else []
    if len(run_dirs) != 1:
        return {"status": "NOT_EVALUABLE", "reason": f"expected one TrackManager debug run, found {len(run_dirs)}"}
    run_dir = run_dirs[0]
    state_path = run_dir / "track_states.csv"
    payload_path = run_dir / "track_output_payloads.csv"
    if not state_path.is_file() or not payload_path.is_file():
        return {"status": "NOT_EVALUABLE", "reason": "missing track_states or track_output_payloads"}
    states, payloads = _csv(state_path), _csv(payload_path)
    per_result: dict[int, list[dict[str, str]]] = {}
    result_period: dict[int, int] = {}
    for result_number in (1, 2, 3):
        path = branch_dir / f"result/detection_results_GMTI{result_number:02d}.csv"
        if not path.is_file():
            continue
        rows = _csv(path)
        periods = {_integer(row.get("period_id")) for row in rows}
        periods.discard(None)
        if len(periods) != 1:
            continue
        per_result[result_number] = rows
        result_period[result_number] = next(iter(periods))
    payload_keys: dict[tuple[int, str, int], dict[str, str]] = {}
    for row in payloads:
        match = re.search(r"(\d+)", str(row.get("result_id", "")))
        result_number = int(match.group(1)) if match else None
        det_index = _integer(row.get("matched_det_index"))
        track_id = str(row.get("track_id", "")).strip()
        if result_number is None or det_index is None or not track_id:
            continue
        payload_keys[(result_number, track_id, det_index)] = row
    observations: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in states:
        result_number = _integer(row.get("result_id"))
        track_id = str(row.get("track_id", "")).strip()
        matched = _truthy(row.get("matched_this_frame"))
        det_index = _integer(row.get("matched_det_index")) if matched else None
        if result_number is None or result_number not in result_period or not track_id:
            errors.append("unmapped_track_state_result_or_id")
            continue
        detection_rows = per_result[result_number]
        detection: Mapping[str, Any] | None = None
        if matched and det_index is not None and 0 <= det_index < len(detection_rows):
            detection = detection_rows[det_index]
        elif matched:
            errors.append("matched_det_index_out_of_range")
        det_id = str(detection.get("det_id", "")) if detection else ""
        beam_id = _integer(detection.get("beam_id")) if detection else 50
        if beam_id is None:
            errors.append("missing_beam_id")
            continue
        key = (result_number, track_id, det_index) if det_index is not None else None
        payload = payload_keys.get(key) if key is not None else None
        observations.append({
            "period_id": result_period[result_number], "beam_id": beam_id,
            "track_id": track_id, "state": row.get("state", ""),
            "matched_this_frame": matched, "det_id": det_id,
            "output_payload": payload is not None,
            "output_speed": payload.get("output_speed") if payload else None,
            "result_number": result_number,
            "det_index": det_index,
            "utc": row.get("utc", ""),
        })
    v2 = any(run_dir.rglob("track_association_audit_v2.csv"))
    return {
        "status": "evaluable" if observations and not errors else "PARTIAL" if observations else "NOT_EVALUABLE",
        "reason": ";".join(sorted(set(errors))) if errors else None,
        "observations": observations,
        "payload_count": len(payloads),
        "association_audit_v2_available": v2,
        "track_events": _csv(run_dir / "track_events.csv") if (run_dir / "track_events.csv").is_file() else [],
        "track_frames": _csv(run_dir / "track_frames.csv") if (run_dir / "track_frames.csv").is_file() else [],
        "track_detections": _csv(run_dir / "track_detections.csv") if (run_dir / "track_detections.csv").is_file() else [],
    }


def _ground_speed_samples(
    truths: Sequence[Mapping[str, Any]],
    target_result: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    truth_by_key = {
        (int(float(row["period_id"])), int(float(row["beam_id"])), str(row["target_id"])): row
        for row in truths if _truthy(row.get("visible"))
    }
    association_by_detection = {
        (int(row["period_id"]), int(row["beam_id"]), str(row["det_id"])): row
        for row in target_result.get("associations", []) if row.get("matched")
    }
    payloads_by_key: dict[tuple[int, int, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in observations:
        if not row.get("output_payload") or not row.get("matched_this_frame"):
            continue
        key = (int(row["period_id"]), int(row["beam_id"]), str(row["det_id"]))
        if key in association_by_detection:
            payloads_by_key[key].append(row)
    output: list[dict[str, Any]] = []
    for key, payloads in payloads_by_key.items():
        if len(payloads) != 1:
            continue
        association = association_by_detection[key]
        target = truth_by_key.get((key[0], key[1], str(association["target_id"])))
        speed, ve, vn = (_number(payloads[0].get("output_speed")),
                         _number(target.get("ve_mps")) if target else None,
                         _number(target.get("vn_mps")) if target else None)
        if speed is None or ve is None or vn is None:
            continue
        output.append({"sample_key": association["sample_key"], "error": speed - math.hypot(ve, vn)})
    return output


def _track_metrics(
    track_data: Mapping[str, Any], truths: Sequence[Mapping[str, Any]], target_result: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    observations = track_data.get("observations", [])
    matched_keys = {
        (int(row["period_id"]), int(row["beam_id"]), str(row["det_id"]))
        for row in target_result.get("associations", []) if row.get("matched")
    }
    metrics = summarize_track_lineage(
        observations=observations,
        target_matched_detection_keys=matched_keys,
        association_audit_available=bool(track_data.get("association_audit_v2_available")),
        truth_opportunity_count=sum(_truthy(row.get("visible")) for row in truths),
    ) if observations else {"status": "NOT_EVALUABLE", "reason": track_data.get("reason")}
    ground_speed = _ground_speed_samples(truths, target_result, observations)
    metrics["production_debug_status"] = track_data.get("status")
    metrics["production_debug_reason"] = track_data.get("reason")
    metrics["track_output_payload_count"] = track_data.get("payload_count", 0)
    metrics["ground_speed_rmse_own"] = rmse_from_samples(ground_speed)
    # These lifecycle fields are calculated only from explicit retained states/events.
    target_ids = set(metrics.get("target_track_ids", []))
    events = track_data.get("track_events", [])
    states = observations
    truth_times = [_number(row.get("utc_mid")) for row in truths if _truthy(row.get("visible"))]
    first_truth_time = min((x for x in truth_times if x is not None), default=None)
    target_events = [row for row in events if str(row.get("track_id", "")) in target_ids]
    create_times = [_number(row.get("utc")) for row in target_events if str(row.get("event", "")).lower() in {"create", "created"}]
    confirm_times = [_number(row.get("utc")) for row in states
                     if str(row.get("track_id", "")) in target_ids and str(row.get("state", "")).lower() == "confirmed"]
    metrics["target_track_initiation_latency_s"] = (
        min(x for x in create_times if x is not None) - first_truth_time
        if first_truth_time is not None and any(x is not None for x in create_times) else None
    )
    first_confirm = min((x for x in confirm_times if x is not None), default=None)
    metrics["target_track_confirmation_latency_s"] = (
        first_confirm - first_truth_time if first_truth_time is not None and first_confirm is not None else None
    )
    confirmed_target_periods = {
        int(row["period_id"]) for row in observations
        if str(row.get("track_id", "")) in target_ids and str(row.get("state", "")).lower() == "confirmed"
        and row.get("output_payload") and row.get("matched_this_frame")
        and (int(row["period_id"]), int(row["beam_id"]), str(row["det_id"])) in matched_keys
    }
    post_confirmation_periods = {
        int(row["period_id"]) for row in truths if _truthy(row.get("visible"))
        and first_confirm is not None and (_number(row.get("utc_mid")) or math.inf) >= first_confirm
    }
    metrics["post_confirmation_match_rate"] = (
        len(confirmed_target_periods & post_confirmation_periods) / len(post_confirmation_periods)
        if post_confirmation_periods else None
    )
    metrics["track_fragmentation_count_observed"] = max(0, len(target_ids) - 1) if target_ids else None
    reacquisitions = sum(
        str(row.get("event", "")).strip().lower() in {"reacquire", "reacquired"}
        for row in target_events
    )
    metrics["explicit_target_reacquisition_events"] = reacquisitions if target_ids else None
    return metrics, ground_speed


def _gamma_pair_rows(case_meta: Mapping[str, Any], condition_records: Mapping[tuple[str, str], Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    details: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for method in ("C2", "C3"):
        mode_rows: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {}
        for mode in ("Mode-A", "Mode-B"):
            label = f"{method}_{mode}"
            branch = condition_records.get((label, "ON"))
            if not branch:
                continue
            path = branch["branch_dir"] / "result/production_calibration_reference_gamma.csv"
            if not path.is_file():
                continue
            data = _csv(path)
            indexed: dict[tuple[str, ...], dict[str, Any]] = {}
            for row in data:
                key = tuple(str(row.get(k, "")) for k in ("period_id", "beam_id", "group_id", "az_index", "range_start", "range_end"))
                indexed[key] = row
            mode_rows[mode] = indexed
        if not all(mode in mode_rows for mode in ("Mode-A", "Mode-B")):
            summaries.append({**case_meta, "method": method, "status": "NOT_EVALUABLE_MISSING_GAMMA"})
            continue
        keys_a, keys_b = set(mode_rows["Mode-A"]), set(mode_rows["Mode-B"])
        common = sorted(keys_a & keys_b)
        amp_diffs: list[float] = []; phase_diffs: list[float] = []; support_diffs: list[float] = []
        for key in common:
            a, b = mode_rows["Mode-A"][key], mode_rows["Mode-B"][key]
            ar, ai, br, bi = (_number(a.get("gamma_real")), _number(a.get("gamma_imag")),
                              _number(b.get("gamma_real")), _number(b.get("gamma_imag")))
            if None in (ar, ai, br, bi):
                continue
            amp_a, amp_b = math.hypot(ar, ai), math.hypot(br, bi)
            phase_a, phase_b = math.atan2(ai, ar), math.atan2(bi, br)
            phase_delta = (phase_b - phase_a + math.pi) % (2 * math.pi) - math.pi
            sa, sb = _integer(a.get("support_count")), _integer(b.get("support_count"))
            support_delta = sb - sa if sa is not None and sb is not None else None
            if support_delta is not None: support_diffs.append(float(support_delta))
            amp_diffs.append(amp_b - amp_a); phase_diffs.append(phase_delta)
            details.append({**case_meta, "method": method, "group_key": "/".join(key),
                "mode_a_status": a.get("status"), "mode_b_status": b.get("status"),
                "mode_a_truth_used": a.get("truth_used_in_estimator"), "mode_b_truth_used": b.get("truth_used_in_estimator"),
                "mode_a_support": sa, "mode_b_support": sb, "support_delta_mode_b_minus_a": support_delta,
                "mode_a_gamma_abs": amp_a, "mode_b_gamma_abs": amp_b, "gamma_abs_delta_mode_b_minus_a": amp_b-amp_a,
                "mode_a_gamma_phase_rad": phase_a, "mode_b_gamma_phase_rad": phase_b,
                "gamma_phase_delta_rad": phase_delta,
                "mode_a_phase_coherence": _number(a.get("phase_coherence")),
                "mode_b_phase_coherence": _number(b.get("phase_coherence")),
            })
        summaries.append({**case_meta, "method": method,
            "status": "evaluable" if common else "NOT_EVALUABLE_NO_COMMON_GROUPS",
            "mode_a_group_count": len(keys_a), "mode_b_group_count": len(keys_b),
            "common_group_count": len(common), "mode_a_only_groups": len(keys_a-keys_b), "mode_b_only_groups": len(keys_b-keys_a),
            "gamma_abs_delta_median": sorted(amp_diffs)[len(amp_diffs)//2] if amp_diffs else None,
            "gamma_phase_delta_median_rad": sorted(phase_diffs)[len(phase_diffs)//2] if phase_diffs else None,
            "support_delta_median": sorted(support_diffs)[len(support_diffs)//2] if support_diffs else None,
            "paired_estimates_truth_flag_false": all(
                str(mode_rows[mode][key].get("truth_used_in_estimator", "")).lower() in {"false", "0"}
                for mode in ("Mode-A", "Mode-B") for key in common
            ),
        })
    return summaries, details


def _csi_tap_rows(case_meta: Mapping[str, Any], condition_records: Mapping[tuple[str, str], Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for method in ("C2", "C3"):
        for mode in ("Mode-A", "Mode-B"):
            label = f"{method}_{mode}"
            branch = condition_records.get((label, "ON"))
            if not branch:
                continue
            for path in sorted((branch["branch_dir"] / "result/csi_metrics").glob("*/csi_metric_tap_summary.csv")):
                for row in _csv(path):
                    output.append({**case_meta, "method": method, "mode": mode,
                        "period_id": row.get("period_id"), "result_id": row.get("result_id"),
                        "roi_name": row.get("roi_name"), "valid_cells": row.get("valid_cells"),
                        "before_mean_power": row.get("before_mean_power"), "after_mean_power": row.get("after_mean_power"),
                        "CA_ROI_dB": row.get("CA_ROI_dB"),
                        "phase_model_weighted_coherence": row.get("phase_model_weighted_coherence"),
                        "status": row.get("status"), "interpretation": "mixed clutter/noise diagnostic; not target-only complex transfer",
                        "source_sha256": _sha256(path), "source_path": str(path),
                    })
    return output


def evaluate_case(case_dir: Path, snapshot_manifest: Mapping[str, Any]) -> dict[str, Any]:
    case_root, cm = _find_case(case_dir)
    case_info = cm.get("case", {})
    scene = cm.get("scene_identity", {})
    case_meta = {
        "case_key": case_dir.name,
        "case_id": cm.get("case_id"),
        "seed": scene.get("seed", case_info.get("seed")),
        "target_velocity_mps": scene.get("target_velocity_mps", case_info.get("target_velocity_mps")),
        "target_snr_db": scene.get("target_snr_db", case_info.get("target_snr_db")),
        "delay_error_ns": case_info.get("delay_error_ns"),
    }
    conditions = _load_condition_records(cm, case_root)
    identity = _validate_case_identity(case_manifest=cm, snapshot_manifest=snapshot_manifest, condition_records=conditions)
    on_truth = _load_truth(case_root, "ON")
    to_truth = _load_truth(case_root, "TO")
    target_free = _target_free(case_root)
    results: dict[str, Any] = {
        "case_meta": case_meta, "identity": identity, "conditions": {},
        "gamma_summaries": [], "gamma_details": [], "csi_taps": [],
        "h0_rows": [], "to_rows": [],
    }
    for (label, role), branch in sorted(conditions.items()):
        branch_dir = branch["branch_dir"]
        cf = _cfar_metrics(branch_dir)
        clusters = _cluster_counts(branch_dir)
        result_row: dict[str, Any] = {
            **case_meta, "condition": label, "method": branch["method_id"], "mode": branch["mode"], "role": role,
            "branch_name": branch["branch_name"], "production_status": branch["branch_manifest"].get("production_status"),
            "cfar_dynamic_hit_count": cf.get("dynamic_hit_count"),
            "cfar_dynamic_valid_cut_count": cf.get("dynamic_valid_cut_count"),
            "cfar_dynamic_cut_hit_fraction": cf.get("dynamic_cut_hit_fraction"),
            "cfar_full_hit_count": cf.get("full_hit_count"),
            "cfar_full_valid_cut_count": cf.get("full_valid_cut_count"),
            "cfar_full_cut_hit_fraction": cf.get("full_cut_hit_fraction"),
            "cfar_alpha_values": json.dumps(cf.get("alpha_values", [])),
            "postfilter_clusters": clusters.get("postfilter_clusters"),
            "selected_candidates": clusters.get("selected_candidates"),
            "lineage_status": None,
            "final_detection_rows": None,
            "strict_truth_hits": None,
            "strict_truth_opportunities": None,
            "strict_hit_fraction": None,
            "old_r18_truth_hit_count": None,
            "old_r18_truth_opportunity_count": None,
            "old_r18_detection_record_false_alarm_count": None,
            "evaluator_status": "NOT_EVALUABLE",
        }
        result_manifest = branch_dir / "result/run_manifest.json"
        result_row["execution_run_manifest_sha256"] = _sha256(result_manifest) if result_manifest.is_file() else None
        if role in {"ON", "TO"}:
            lineage_path = branch_dir / "result/debug/cluster_detection_lineage.csv"
            detections = _load_final_detections(branch_dir)
            lineage = _csv(lineage_path) if lineage_path.is_file() else []
            truths = on_truth if role == "ON" else to_truth
            result_row["final_detection_rows"] = len(detections)
            result_row["lineage_status"] = _lineage_status(lineage, detections).get("status") if lineage else "NOT_EVALUABLE"
            axes = _axis_rows(lineage)
            doppler_rows = max((_integer(row.get("doppler_row_count")) or 0 for row in axes), default=0)
            target_result = evaluate_targets(truths, detections, doppler_rows=doppler_rows, doppler_axis_rows=axes)
            result_row["strict_truth_hits"] = target_result["metrics"]["unique_hits"]
            result_row["strict_truth_opportunities"] = target_result["metrics"]["truth_opportunities"]
            result_row["strict_hit_fraction"] = target_result["metrics"]["truth_opportunity_hit_fraction"]
            result_row["invalid_detection_rows"] = target_result["metrics"]["invalid_detection_rows"]
            result_row["invalid_truth_opportunities"] = target_result["metrics"]["invalid_truth_opportunities"]
            result_row["eligible_candidate_edges"] = target_result["metrics"]["eligible_candidate_edges"]
            result_row["duplicate_candidate_count"] = target_result["metrics"]["duplicate_candidate_count"]
            result_row["truth_unmatched_detection_rows"] = target_result["metrics"]["truth_unmatched_detection_rows"]
            result_row["parameter_metrics_own_success"] = target_result["parameter_metrics_own_success"]
            result_row["evaluator_status"] = target_result["metrics"]["detection_status"]
            # Historical R18 counters are retained only to quantify definition changes.
            cond_record = branch["condition_record"]
            old = cond_record.get("metrics", {})
            result_row["old_r18_truth_hit_count"] = old.get("target_detection_hit_count")
            result_row["old_r18_truth_opportunity_count"] = old.get("target_detection_opportunity_count")
            result_row["old_r18_detection_record_false_alarm_count"] = old.get("detection_record_false_alarm_count")
            result_row["target_result"] = target_result
            if role == "ON":
                track_data = _track_observations(branch_dir)
                track_metrics, ground_speed_samples = _track_metrics(track_data, truths, target_result)
                result_row["track_metrics"] = track_metrics
                result_row["ground_speed_samples"] = ground_speed_samples
                results["conditions"][label] = result_row
                results["csi_taps"].extend(_csi_tap_rows(case_meta, {key: value for key, value in conditions.items() if key[0] == label})) if label in {"C2_Mode-A", "C2_Mode-B", "C3_Mode-A", "C3_Mode-B"} else None
                continue
            else:
                results["to_rows"].append({**{k: v for k, v in result_row.items() if k != "target_result"},
                    "scope": "target_only_control_not_operational_Pd"})
        elif role == "OFF":
            ref = "FIXED_OR_CURRENT_NO_FIT"
            if branch["mode"] in {"Mode-A", "Mode-B"}:
                fit_role = "OFF" if branch["mode"] == "Mode-A" else "ON"
                ref = reference_role(mode=branch["mode"], estimator_role=fit_role, evaluation_role="OFF")
            h0 = audit_h0(
                hit_count=cf.get("full_hit_count"), valid_cut_count=cf.get("full_valid_cut_count"),
                target_free=target_free, role="OFF", reference_role=ref,
                independent_holdout=False, independent_seed_count=3, uncertainty_assessed=False,
            )
            result_row["h0_status"] = h0["status"]
            result_row["h0_observed_false_hit_fraction"] = h0["h0_observed_false_hit_fraction"]
            result_row["empirical_pfa_status"] = h0["statistical_pfa_status"]
            result_row["reference_role"] = ref
            result_row["h0_reason"] = h0.get("reason")
            results["h0_rows"].append(result_row)
        results["conditions"].setdefault(label, result_row)

    gamma_summary, gamma_details = _gamma_pair_rows(case_meta, conditions)
    results["gamma_summaries"] = gamma_summary
    results["gamma_details"] = gamma_details
    if not results["csi_taps"]:
        results["csi_taps"] = _csi_tap_rows(case_meta, conditions)
    return results


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields and not isinstance(row[key], (dict, list, tuple, set)):
                fields.append(key)
    if not fields:
        fields = ["status"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: (json.dumps(value, sort_keys=True) if isinstance(value, (dict, list, tuple, set)) else value)
                             for key, value in row.items()})


def _baseline_differences(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    by_key = {(row["case_key"], row["condition"]): row for row in rows if row.get("role") == "ON"}
    comparisons = [("operational_vs_C0", "C0", label) for label in ("A0", "C1", "C2_Mode-B", "C3_Mode-B")]
    comparisons += [("Mode-A_C3_vs_C2", "C2_Mode-A", "C3_Mode-A")]
    metrics = ("strict_hit_fraction", "cfar_full_cut_hit_fraction", "cfar_full_hit_count",
               "final_detection_rows", "postfilter_clusters", "selected_candidates")
    for (case_key, condition), current in sorted(by_key.items()):
        for group, baseline_label, compared_label in comparisons:
            if condition != compared_label or (case_key, baseline_label) not in by_key:
                continue
            baseline = by_key[(case_key, baseline_label)]
            for metric in metrics:
                a, b = _number(current.get(metric)), _number(baseline.get(metric))
                output.append({
                    "case_key": case_key, "seed": current.get("seed"),
                    "target_velocity_mps": current.get("target_velocity_mps"),
                    "target_snr_db": current.get("target_snr_db"),
                    "comparison_group": group, "baseline_condition": baseline_label,
                    "condition": compared_label, "metric": metric,
                    "condition_value": a, "baseline_value": b,
                    "paired_difference_condition_minus_baseline": a-b if a is not None and b is not None else None,
                    "independent_unit": "seed within fixed speed/SNR; period/CUT/Detection not independent",
                })
    return output


def _common_and_parameter_tables(case_results: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    common_rows: list[dict[str, Any]] = []
    parameter_rows: list[dict[str, Any]] = []
    for case in case_results:
        meta = case["case_meta"]
        conditions = case["conditions"]
        for group_name, labels in (("operational", OPERATIONAL_GROUP), ("privileged_mode_a", PRIVILEGED_GROUP)):
            selected = {label: conditions[label] for label in labels if label in conditions}
            if len(selected) != len(labels):
                common_rows.append({**meta, "comparison_group": group_name, "status": "NOT_EVALUABLE_MISSING_CONDITION"})
                continue
            for metric in PARAMETER_NAMES:
                samples_by_method: dict[str, list[dict[str, Any]]] = {}
                for label, row in selected.items():
                    if metric == "ground_speed_mps":
                        samples = row.get("ground_speed_samples", [])
                    else:
                        samples = row.get("target_result", {}).get("parameter_samples", {}).get(metric, [])
                    samples_by_method[label] = list(samples)
                try:
                    keys = common_success_keys(samples_by_method)
                    status = "evaluable" if keys else "NOT_EVALUABLE_NO_COMMON_SUCCESS"
                except ValueError as exc:
                    keys = set(); status = f"NOT_EVALUABLE:{exc}"
                common_rows.append({**meta, "comparison_group": group_name, "metric": metric,
                    "common_success_count": len(keys), "common_success_keys": ";".join(sorted(keys)), "status": status})
                for label, samples in samples_by_method.items():
                    own = rmse_from_samples(samples)
                    common_samples = [row for row in samples if str(row.get("sample_key")) in keys]
                    common = rmse_from_samples(common_samples)
                    parameter_rows.append({**meta, "comparison_group": group_name, "condition": label,
                        "metric": metric, "own_success_count": own["count"], "own_success_rmse": own["rmse"],
                        "common_success_count": common["count"], "common_success_rmse": common["rmse"],
                        "unit": {"angle_deg":"degree", "position_m":"m", "range_m":"m",
                                 "folded_doppler_hz":"Hz", "radial_velocity_mps":"m/s", "ground_speed_mps":"m/s"}[metric],
                        "status": own["status"],
                    })
    return common_rows, parameter_rows


def _metric_evaluability_rows(
    baseline_rows: Sequence[Mapping[str, Any]], h0_rows: Sequence[Mapping[str, Any]],
    parameter_rows: Sequence[Mapping[str, Any]], common_rows: Sequence[Mapping[str, Any]],
    csi_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    context_keys = ("case_key", "case_id", "seed", "target_velocity_mps", "target_snr_db", "delay_error_ns")

    def emit(context: Mapping[str, Any], metric: str, status: str, *, value: Any = None,
             numerator: Any = None, denominator: Any = None, unit: str = "",
             reason: str = "", source_table: str = "") -> None:
        output.append({**{key: context.get(key) for key in context_keys},
                       "condition": context.get("condition"), "role": context.get("role"),
                       "comparison_group": context.get("comparison_group"), "metric": metric,
                       "status": status, "numerator": numerator, "denominator": denominator,
                       "value": value, "unit": unit, "reason": reason, "source_table": source_table})

    for row in baseline_rows:
        if row.get("role") != "ON":
            continue
        emit(row, "truth_opportunity_hit_fraction", "EVALUABLE" if row.get("evaluator_status") == "evaluable" else "NOT_EVALUABLE",
             value=row.get("strict_hit_fraction"), numerator=row.get("strict_truth_hits"),
             denominator=row.get("strict_truth_opportunities"), unit="target-period opportunities",
             source_table="baseline_task_metrics.csv")
        emit(row, "tested_CUT_hit_fraction", "EVALUABLE" if _integer(row.get("cfar_full_valid_cut_count")) else "NOT_EVALUABLE",
             value=row.get("cfar_full_cut_hit_fraction"), numerator=row.get("cfar_full_hit_count"),
             denominator=row.get("cfar_full_valid_cut_count"), unit="valid CUTs",
             source_table="baseline_task_metrics.csv")
        for metric, field, unit in (
            ("postfilter_cluster_count", "postfilter_clusters", "clusters"),
            ("selected_candidate_count", "selected_candidates", "candidates"),
            ("final_detection_record_count", "final_detection_rows", "Detection records"),
            ("truth_unmatched_detection_record_count", "truth_unmatched_detection_rows", "Detection records"),
        ):
            emit(row, metric, "EVALUABLE" if _integer(row.get(field)) is not None else "NOT_EVALUABLE",
                 value=row.get(field), unit=unit, source_table="baseline_task_metrics.csv")
        emit(row, "CFAR_hit_spatial_attribution", "REPLAY_REQUIRED",
             reason="R18 snapshot has counts and hit-index hashes but no hit coordinates, valid mask, power map, or threshold map",
             source_table="R18 snapshot inventory")
        track = row.get("track_metrics", {})
        for metric, field, unit in (
            ("target_matched_period_count", "target_matched_periods", "periods"),
            ("unique_target_track_count", "unique_target_track_count", "track IDs"),
            ("false_confirmed_track_count", "false_confirmed_track_count", "unique confirmed track IDs"),
            ("unmatched_confirmed_payload_count", "unmatched_confirmed_payload_count", "payloads"),
            ("track_continuity", "track_continuity", "fraction of visible truth periods"),
            ("target_track_initiation_latency", "target_track_initiation_latency_s", "s"),
            ("target_track_confirmation_latency", "target_track_confirmation_latency_s", "s"),
            ("post_confirmation_match_rate", "post_confirmation_match_rate", "fraction"),
            ("observed_track_fragmentation_count", "track_fragmentation_count_observed", "additional target track IDs"),
            ("explicit_target_reacquisition_events", "explicit_target_reacquisition_events", "events"),
        ):
            value = track.get(field)
            status = "EVALUABLE" if track.get("status") == "evaluable" and value is not None else "NOT_EVALUABLE"
            emit(row, metric, status, value=value, unit=unit,
                 reason=str(track.get("reason") or "") if status == "NOT_EVALUABLE" else "",
                 source_table="track_metrics.csv")
        emit(row, "ID_switch_root_cause", "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG",
             reason="track_association_audit_v2 candidate competition/rejection evidence is absent",
             source_table="track_metrics.csv")

    for row in h0_rows:
        emit(row, "H0_observed_false_hit_fraction",
             "EVALUABLE_DESCRIPTIVE_ONLY" if row.get("h0_status") == "evaluable_descriptive_only" else "NOT_EVALUABLE",
             value=row.get("h0_observed_false_hit_fraction"), numerator=row.get("cfar_full_hit_count"),
             denominator=row.get("cfar_full_valid_cut_count"), unit="valid H0 CUTs",
             reason=str(row.get("h0_reason") or "CUTs are correlated; H0 is not an independent holdout"),
             source_table="cfar_h0_descriptive_metrics.csv")
        emit(row, "empirical_Pfa", str(row.get("empirical_pfa_status") or "NOT_EVALUABLE"),
             unit="probability", reason=str(row.get("h0_reason") or "independent H0 holdout and uncertainty assessment unavailable"),
             source_table="cfar_h0_descriptive_metrics.csv")

    for row in parameter_rows:
        context = {**row, "role": "ON"}
        own_count = _integer(row.get("own_success_count")) or 0
        emit(context, f"own_success_RMSE:{row.get('metric')}",
             "EVALUABLE" if own_count > 0 and _number(row.get("own_success_rmse")) is not None else "NOT_EVALUABLE",
             value=row.get("own_success_rmse"), numerator=own_count,
             denominator=row.get("truth_opportunity_count"), unit=str(row.get("unit") or ""),
             source_table="parameter_estimation_metrics.csv")
        common_count = _integer(row.get("common_success_count")) or 0
        emit(context, f"common_success_RMSE:{row.get('metric')}",
             "EVALUABLE" if common_count > 0 and _number(row.get("common_success_rmse")) is not None else "NOT_EVALUABLE",
             value=row.get("common_success_rmse"), numerator=common_count,
             denominator=row.get("truth_opportunity_count"), unit=str(row.get("unit") or ""),
             source_table="parameter_estimation_metrics.csv")

    for row in csi_rows:
        emit(row, "CSI_mixed_clutter_noise_CA_ROI_dB",
             "EVALUABLE_DESCRIPTIVE_MIXED_CLUTTER_NOISE" if _number(row.get("CA_ROI_dB")) is not None else "NOT_EVALUABLE",
             value=row.get("CA_ROI_dB"), denominator=row.get("valid_cells"), unit="dB",
             reason="summary combines clutter and noise; not target-only complex transfer",
             source_table="csi_tap_descriptive.csv")
    seen_target_transfer: set[tuple[str, str, str]] = set()
    for row in csi_rows:
        key = (str(row.get("case_key")), str(row.get("method")), str(row.get("mode")))
        if key in seen_target_transfer:
            continue
        seen_target_transfer.add(key)
        emit({**row, "condition": f"{key[1]}_{key[2]}", "role": "ON"}, "target_only_complex_transfer", "REPLAY_REQUIRED",
             reason="paired complex CSI/input arrays and target-only support are absent from retained R18 snapshots",
             source_table="R18 snapshot inventory")
    for row in common_rows:
        if row.get("status") != "evaluable":
            emit(row, f"common_success_opportunities:{row.get('metric')}", "NOT_EVALUABLE",
                 numerator=row.get("common_success_count"), unit="truth opportunities",
                 reason=str(row.get("status")), source_table="baseline_common_opportunity.csv")
    return output


def run_evaluation(input_root: Path, output_dir: Path, case_key: str | None = None) -> dict[str, Any]:
    case_dirs = sorted(path for path in input_root.glob("seed*") if path.is_dir())
    if case_key:
        case_dirs = [path for path in case_dirs if path.name == case_key]
    if not case_dirs:
        raise ValueError(f"no R18 cases found under {input_root} for case={case_key!r}")
    all_case_results: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []
    h0_rows: list[dict[str, Any]] = []
    gamma_summaries: list[dict[str, Any]] = []
    gamma_details: list[dict[str, Any]] = []
    csi_rows: list[dict[str, Any]] = []
    to_rows: list[dict[str, Any]] = []
    for case_dir in case_dirs:
        snap_manifest = _json(case_dir / "experiment_snapshot/manifest.json")
        result = evaluate_case(case_dir, snap_manifest)
        all_case_results.append(result)
        for condition in result["conditions"].values():
            baseline_rows.append(condition)
        h0_rows.extend(result["h0_rows"])
        gamma_summaries.extend(result["gamma_summaries"])
        gamma_details.extend(result["gamma_details"])
        csi_rows.extend(result["csi_taps"])
        to_rows.extend(result["to_rows"])
    common_rows, parameter_rows = _common_and_parameter_tables(all_case_results)
    track_rows = []
    for result in all_case_results:
        for label, row in result["conditions"].items():
            if row.get("role") == "ON":
                track_rows.append({**{k:v for k,v in result["case_meta"].items()}, "condition": label,
                                   **row.get("track_metrics", {})})
    metric_rows = _metric_evaluability_rows(baseline_rows, h0_rows, parameter_rows, common_rows, csi_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "baseline_task_metrics.csv", baseline_rows)
    _write_csv(output_dir / "baseline_seed_paired_differences.csv", _baseline_differences(baseline_rows))
    _write_csv(output_dir / "baseline_common_opportunity.csv", common_rows)
    _write_csv(output_dir / "cfar_h0_descriptive_metrics.csv", h0_rows)
    _write_csv(output_dir / "detection_waterfall.csv", baseline_rows)
    _write_csv(output_dir / "track_metrics.csv", track_rows)
    _write_csv(output_dir / "parameter_estimation_metrics.csv", parameter_rows)
    _write_csv(output_dir / "metric_evaluability.csv", metric_rows)
    _write_csv(output_dir / "gamma_mode_comparison.csv", gamma_summaries)
    _write_csv(output_dir / "gamma_mode_pair_details.csv", gamma_details)
    _write_csv(output_dir / "csi_tap_descriptive.csv", csi_rows)
    _write_csv(output_dir / "target_only_control_metrics.csv", to_rows)
    script_path = Path(__file__).resolve()
    contract_path = Path(__file__).resolve().parents[2] / "docs/research/phase_r/R19/02_frozen_task_evaluation_contract.md"
    repo_root = script_path.parents[2]
    current_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()
    manifest = {
        "schema_version": CONTRACT_VERSION,
        "analysis_source_commit": current_commit,
        "analysis_worktree_dirty": _worktree_dirty(repo_root),
        "analysis_script_sha256": _sha256(script_path),
        "task_evaluator_sha256": _sha256(script_path.parents[0] / "r19_task_evaluator.py"),
        "contract_sha256": _sha256(contract_path),
        "input_root": str(input_root.resolve()),
        "case_count": len(all_case_results),
        "case_keys": [result["case_meta"]["case_key"] for result in all_case_results],
        "r18_runner_source_commits": sorted({str(result["identity"].get("runner_source_commit")) for result in all_case_results}),
        "r18_build_source_commits": sorted({str(result["identity"].get("build_source_commit")) for result in all_case_results}),
        "r18_production_binary_sha256": sorted({str(result["identity"].get("production_binary_sha256")) for result in all_case_results}),
        "r18_config_sha256": sorted({str(result["identity"].get("config_sha256")) for result in all_case_results}),
        "identity_by_case": {result["case_meta"]["case_key"]: result["identity"] for result in all_case_results},
        "outputs": sorted(path.name for path in output_dir.glob("*.csv")),
        "limitations": [
            "input IQ arrays absent; their hashes are only those recorded by the R18 manifests",
            "CFAR exact hit coordinates and power/threshold maps absent; only per-period counts and hashes retained",
            "CSI tap power summaries mix clutter and noise and cannot establish target-only complex transfer",
            "statistical empirical Pfa remains NOT_EVALUABLE under the frozen contract",
        ],
    }
    (output_dir / "evaluation_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=Path("docs/research/phase_r/R18/evidence/formal_matrix"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case-key", help="evaluate one existing R18 case first")
    args = parser.parse_args()
    manifest = run_evaluation(args.input_root.resolve(), args.output_dir.resolve(), args.case_key)
    print(json.dumps({"schema_version": manifest["schema_version"], "case_count": manifest["case_count"],
                      "output_dir": str(args.output_dir.resolve()),
                      "production_binary_sha256": manifest["r18_production_binary_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
