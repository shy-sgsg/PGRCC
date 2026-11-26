#!/usr/bin/env python3
"""Analyze a frozen R22-V4 registry and its SHA/CRC-verified case archives.

CPU-only analysis entrypoint. It never runs Stage2, Production, CUDA, or AI.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r import r22_v4_confirmatory_executor as executor
from scripts.phase_r.r22_v4_confirmatory_statistics import (
    H0_PRIMARY_ENDPOINT,
    aggregate_h0_seed_results,
    aggregate_seed_level_primary,
    bootstrap_mean_interval,
    coverage_mean_interval,
    h0_seed_statistics,
)


ARCHIVE_PROFILE = "r22_v4_output_complete"
PRIMARY_METHODS = ("M1_CAUSAL_PREFIX", "FIXED_D3")
EVALUATOR_METHOD_IDS = {
    "FIXED_D3": "R22_R9_Fixed_D3",
    "PK_KNOWN": "PK",
}


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _csv_member(archive: zipfile.ZipFile, name: str, *, required: bool = True) -> list[dict[str, str]]:
    try:
        payload = archive.read(name)
    except KeyError:
        if required:
            raise ValueError(f"verified case archive lacks required evaluation table: {name}") from None
        return []
    try:
        return [dict(row) for row in csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))]
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError(f"case archive CSV is unreadable: {name}") from exc


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number


def _finite(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _truthy(value: Any, *, default: bool | None = None) -> bool:
    if value is None or str(value).strip() == "":
        if default is not None:
            return default
        raise ValueError("required boolean evaluation field is missing")
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"invalid boolean evaluation value: {value!r}")


def _validate_evaluation_row_identity(
    tables: Mapping[str, Sequence[Mapping[str, Any]]], *, case_id: str, seed: int,
) -> None:
    for table_name, rows in tables.items():
        for index, row in enumerate(rows, start=2):
            if str(row.get("case_id", "")).strip() != case_id:
                raise ValueError(f"{case_id}/{table_name}:{index}: case_id does not match archive")
            if _integer(row.get("seed")) != seed:
                raise ValueError(f"{case_id}/{table_name}:{index}: seed does not match registry")


def _method_id(method_id: str) -> str:
    return EVALUATOR_METHOD_IDS.get(method_id, method_id)


def _rows_for_method(
    rows: Sequence[Mapping[str, Any]], *, method_id: str,
    role: str = "ON",
) -> list[Mapping[str, Any]]:
    evaluator_id = _method_id(method_id)
    return [
        row for row in rows
        if str(row.get("method_id", "")).strip() == evaluator_id
        and str(row.get("role", "")).strip().upper() == role
    ]


def _read_verified_case(
    case: Mapping[str, Any], state: Mapping[str, Any], *, archive_root: Path | None,
) -> dict[str, Any]:
    case_id = str(case["case_id"])
    archive_record = state.get("archive")
    if not isinstance(archive_record, Mapping):
        raise ValueError(f"{case_id}: checkpoint has no archive record")
    if archive_record.get("archive_profile") != ARCHIVE_PROFILE:
        raise ValueError(f"{case_id}: archive profile is not {ARCHIVE_PROFILE}")
    archive_path = Path(str(archive_record.get("archive_path", ""))).expanduser()
    if not archive_path.is_absolute():
        if archive_root is None:
            raise ValueError(f"{case_id}: relative archive path requires --archive-root")
        archive_path = archive_root / archive_path
    archive_path = archive_path.resolve()
    verification = executor.verify_case_archive(
        archive_path,
        expected_sha256=str(archive_record.get("archive_sha256", "")),
        expected_manifest_sha256=str(archive_record.get("archive_manifest_sha256", "")),
        archive_profile=ARCHIVE_PROFILE,
        require_complete_output=True,
        require_case_schema=True,
    )
    if archive_record.get("case_id") not in (None, case_id):
        raise ValueError(f"{case_id}: checkpoint archive case ID disagrees")
    with zipfile.ZipFile(archive_path, "r") as stream:
        manifest = json.loads(stream.read("ARCHIVE_MANIFEST.json"))
        case_identity = manifest.get("case_schema_identity")
        if not isinstance(case_identity, Mapping):
            raise ValueError(f"{case_id}: archive lacks registered case-schema identity")
        for key in ("case_id", "phase", "target_count", "delay_error_ns"):
            if case_identity.get(key) != case.get(key):
                raise ValueError(f"{case_id}: archive case-schema identity mismatch for {key}")
        case_manifests = [name for name in stream.namelist() if name.endswith("/case_manifest.json")]
        if len(case_manifests) != 1:
            raise ValueError(f"{case_id}: archive must contain exactly one case manifest")
        case_root = case_manifests[0].rsplit("/case_manifest.json", 1)[0]
        run_manifest = json.loads(stream.read("r22_run_manifest.json"))
        if run_manifest.get("case_id") not in (None, case_id):
            raise ValueError(f"{case_id}: R22 run manifest identity disagrees")
        expected_runner_phase = "confirmatory" if case.get("phase") == "target_present" else "h0"
        if run_manifest.get("phase") not in (None, expected_runner_phase):
            raise ValueError(f"{case_id}: R22 run manifest phase disagrees")
        table = {
            "task": _csv_member(stream, "evaluation/task_metrics.csv"),
            "associations": _csv_member(stream, "evaluation/target_associations.csv"),
            "target_level": _csv_member(stream, "evaluation/target_level_metrics.csv"),
            "tracks": _csv_member(stream, "evaluation/track_level_metrics.csv"),
            "h0_task": _csv_member(stream, "evaluation/h0_task_metrics.csv", required=case.get("phase") == "h0"),
            "h0_false_alarm": _csv_member(
                stream, "evaluation/h0_false_alarm_metrics.csv", required=case.get("phase") == "h0",
            ),
        }
        _validate_evaluation_row_identity(
            table, case_id=case_id, seed=int(case["seed"]),
        )
        branch_manifests: list[dict[str, Any]] = []
        for name in stream.namelist():
            if name.startswith(f"{case_root}/production/") and name.endswith("/branch_manifest.json"):
                branch = json.loads(stream.read(name))
                if str(branch.get("role", "")).upper() != "ALL":
                    branch_manifests.append(branch)
        truth_sources: list[list[dict[str, str]]] = []
        if case.get("phase") == "target_present":
            names = sorted(
                name for name in stream.namelist()
                if name.startswith(f"{case_root}/scenes/")
                and name.endswith("/truth/truth_targets_by_beam.csv")
            )
            if not names:
                raise ValueError(f"{case_id}: target-present archive lacks planned truth")
            truth_sources = [_csv_member(stream, name) for name in names]
    truth_keys_by_source: list[set[tuple[int, str]]] = []
    beam = int(case["beam"])
    for rows in truth_sources:
        keys: set[tuple[int, str]] = set()
        for row in rows:
            row_beam = _integer(row.get("beam_id"))
            period = _integer(row.get("period_id"))
            target = str(row.get("target_id", "")).strip()
            if row_beam != beam or not target or period is None:
                continue
            if not _truthy(row.get("visible"), default=True):
                continue
            key = (period, target)
            if key in keys:
                raise ValueError(f"{case_id}: duplicate planned truth opportunity {key}")
            keys.add(key)
        truth_keys_by_source.append(keys)
    if truth_keys_by_source and any(keys != truth_keys_by_source[0] for keys in truth_keys_by_source[1:]):
        raise ValueError(f"{case_id}: role-specific truth opportunity identities disagree")
    truth_keys = truth_keys_by_source[0] if truth_keys_by_source else set()
    return {
        "case": dict(case),
        "archive": verification,
        "archive_path": str(archive_path),
        "archive_manifest_sha256": verification["archive_manifest_sha256"],
        "case_root": case_root,
        "tables": table,
        "branch_manifests": branch_manifests,
        "truth_keys": truth_keys,
    }


def _single_row(rows: Sequence[Mapping[str, Any]], *, context: str) -> Mapping[str, Any] | None:
    if len(rows) > 1:
        raise ValueError(f"{context}: duplicate evaluator rows")
    return rows[0] if rows else None


def _branch_status(case_data: Mapping[str, Any], method_id: str) -> str:
    rows = [
        row for row in case_data["branch_manifests"]
        if row.get("method_id") == _method_id(method_id)
        and str(row.get("role", "")).upper() == "ON"
    ]
    row = _single_row(rows, context=f"{case_data['case']['case_id']}/{method_id}/branch")
    return str(row.get("status", "MISSING_BRANCH")) if row else "MISSING_BRANCH"


def _evaluator_status(case_data: Mapping[str, Any], table_name: str, method_id: str) -> str:
    rows = _rows_for_method(case_data["tables"][table_name], method_id=method_id)
    row = _single_row(rows, context=f"{case_data['case']['case_id']}/{method_id}/{table_name}")
    return str(row.get("status", "MISSING_EVALUATOR_ROW")) if row else "MISSING_EVALUATOR_ROW"


def _target_seed_rows(
    plus_four_cases: Sequence[Mapping[str, Any]],
    *, primary_methods: Sequence[str],
    expected_speeds: Sequence[float],
) -> tuple[list[dict[str, Any]], list[tuple[float, str, int]], list[dict[str, Any]]]:
    opportunity_rows: list[dict[str, Any]] = []
    planned_keys_by_speed: dict[float, set[tuple[str, int]]] = {speed: set() for speed in expected_speeds}
    track_seed_values: dict[int, dict[float, dict[str, float | None]]] = {}
    for case_data in plus_four_cases:
        case = case_data["case"]
        seed, speed = int(case["seed"]), float(case["speed_mps"])
        truth_keys = case_data["truth_keys"]
        key_set = {(target_id, period) for period, target_id in truth_keys}
        if speed not in planned_keys_by_speed:
            raise ValueError(f"{case['case_id']}: unregistered speed stratum")
        if planned_keys_by_speed[speed] and planned_keys_by_speed[speed] != key_set:
            raise ValueError(f"{case['case_id']}: planned target-period registry differs across seeds")
        planned_keys_by_speed[speed] = key_set

        associations_by_method: dict[str, dict[tuple[str, int], Mapping[str, Any]]] = {}
        truth_status_by_method: dict[str, dict[tuple[str, int], str]] = {}
        for method_id in primary_methods:
            assoc = _rows_for_method(case_data["tables"]["associations"], method_id=method_id)
            target_level = _rows_for_method(case_data["tables"]["target_level"], method_id=method_id)
            assoc_map: dict[tuple[str, int], Mapping[str, Any]] = {}
            for row in assoc:
                target_id = str(row.get("target_id", "")).strip()
                period = _integer(row.get("period_id"))
                row_beam = _integer(row.get("beam_id"))
                if not target_id or period is None or row_beam != int(case["beam"]):
                    raise ValueError(f"{case['case_id']}/{method_id}: invalid target association identity")
                key = (target_id, period)
                if key in assoc_map:
                    raise ValueError(f"{case['case_id']}/{method_id}: duplicate target association {key}")
                assoc_map[key] = row
            status_map: dict[tuple[str, int], str] = {}
            for row in target_level:
                target_id = str(row.get("target_id", "")).strip()
                period = _integer(row.get("period_id"))
                if not target_id or period is None:
                    raise ValueError(f"{case['case_id']}/{method_id}: invalid target-level identity")
                key = (target_id, period)
                if key in status_map:
                    raise ValueError(f"{case['case_id']}/{method_id}: duplicate target-level row {key}")
                status_map[key] = str(row.get("status", "NOT_EVALUABLE"))
            associations_by_method[method_id] = assoc_map
            truth_status_by_method[method_id] = status_map

        branch_states = {method: _branch_status(case_data, method) for method in primary_methods}
        task_states = {method: _evaluator_status(case_data, "task", method) for method in primary_methods}
        for period, target_id in sorted(truth_keys):
            key = (target_id, period)
            method_rows = [associations_by_method[method].get(key) for method in primary_methods]
            # Missing an association row is a missing required evaluator record,
            # never an inferred miss.
            if any(row is None for row in method_rows):
                continue
            first_status = "VALID"
            for method in primary_methods:
                status = truth_status_by_method[method].get(key)
                if status is None:
                    first_status = "NOT_EVALUABLE_MISSING_TRUTH_STATUS"
                    break
                if status.strip().upper() != "EVALUABLE":
                    first_status = status
                    break
            first_row, second_row = method_rows
            m1_match = _truthy(first_row.get("matched"))
            fixed_match = _truthy(second_row.get("matched"))
            opportunity_rows.append({
                "seed": seed,
                "speed_mps": speed,
                "target_id": target_id,
                "period": period,
                "m1_matched": m1_match,
                "fixed_matched": fixed_match,
                "m1_position_error_m": _finite(first_row.get("position_error_m")) if m1_match else None,
                "fixed_position_error_m": _finite(second_row.get("position_error_m")) if fixed_match else None,
                "truth_status": first_status,
                "production_status": (
                    "PASSED" if all(value.lower() in {"passed", "ok", "succeeded", "success"}
                                    for value in branch_states.values()) else
                    "BRANCH_FAILURE:" + ",".join(f"{key}={value}" for key, value in branch_states.items())
                ),
                "m1_estimator_status": task_states[primary_methods[0]],
                "fixed_estimator_status": task_states[primary_methods[1]],
            })

        seed_tracks: dict[str, float | None] = {}
        for method_id in primary_methods:
            rows = _rows_for_method(case_data["tables"]["tracks"], method_id=method_id)
            row = _single_row(rows, context=f"{case['case_id']}/{method_id}/track")
            count = _integer(row.get("target_confirmed_unique_period_count")) if row else None
            status = str(row.get("status", "")) if row else "MISSING_TRACK_ROW"
            period_count = int(case["period_count"])
            seed_tracks[method_id] = (
                count / period_count
                if status.upper() in {"EVALUABLE", "OK", "PASSED"}
                and count is not None and 0 <= count <= period_count else None
            )
        track_seed_values.setdefault(seed, {})[speed] = seed_tracks

    planned_keys = [
        (speed, target_id, period)
        for speed in expected_speeds
        for target_id, period in sorted(planned_keys_by_speed[speed])
    ]
    track_rows: list[dict[str, Any]] = []
    for seed, by_speed in sorted(track_seed_values.items()):
        values: dict[str, float | None] = {}
        for method_id in primary_methods:
            strata = [by_speed.get(speed, {}).get(method_id) for speed in expected_speeds]
            values[method_id] = (
                sum(float(value) for value in strata) / len(strata)
                if strata and all(value is not None for value in strata) else None
            )
        track_rows.append({"seed": seed, **values})
    return opportunity_rows, planned_keys, track_rows


def _coverage_endpoint(
    differences_pp: Sequence[float], *, planned_seed_count: int,
    method: Any, confidence: float, replicates: int, seed: int,
) -> dict[str, Any]:
    if not isinstance(method, str) or method not in {"paired_t", "bootstrap_percentile", "bootstrap_bca"}:
        return {
            "status": "NOT_EVALUABLE_METHOD_UNRESOLVED",
            "planned_seed_count": planned_seed_count,
            "evaluable_seed_count": len(differences_pp),
            "lower": None, "upper": None,
        }
    if len(differences_pp) < planned_seed_count:
        return {
            "status": "NOT_EVALUABLE_INCOMPLETE_SEED_SET",
            "planned_seed_count": planned_seed_count,
            "evaluable_seed_count": len(differences_pp),
            "lower": None, "upper": None,
        }
    if len(differences_pp) < 2:
        return {
            "status": "NOT_EVALUABLE_INSUFFICIENT_SEEDS",
            "planned_seed_count": planned_seed_count,
            "evaluable_seed_count": len(differences_pp),
            "lower": None, "upper": None,
        }
    result = coverage_mean_interval(
        differences_pp, method=method, confidence=confidence,
        replicates=replicates, seed=seed,
    )
    result.update({
        "planned_seed_count": planned_seed_count,
        "evaluable_seed_count": len(differences_pp),
    })
    return result


def _h0_analysis(
    cases: Sequence[Mapping[str, Any]], *, method_inventory: Sequence[Mapping[str, Any]],
    planned_seeds: Sequence[int],
    period_count: int, seed_event_limit: float, track_event_limit: float,
    bootstrap_replicates: int, bootstrap_seed: int,
) -> dict[str, Any]:
    seed_ids = sorted(int(seed) for seed in planned_seeds)
    endpoints: list[dict[str, Any]] = []
    for method in method_inventory:
        method_id = str(method.get("id", "")).strip()
        if not method_id or method.get("online_ranking") is False:
            continue
        delay = float(method.get("delay_ns", 0.0))
        expected_by_seed = {
            int(item["case"]["seed"]): item for item in cases
            if math.isclose(float(item["case"]["delay_error_ns"]), delay, abs_tol=1e-12)
        }
        selected_cases = [item for item in cases if math.isclose(
            float(item["case"]["delay_error_ns"]), delay, abs_tol=1e-12,
        )]
        if len(expected_by_seed) != len(selected_cases):
            raise ValueError(f"H0 registry has duplicate {delay:g} ns cases for one seed")
        rows: list[dict[str, Any]] = []
        for seed in seed_ids:
            case_data = expected_by_seed.get(seed)
            task_row = None
            false_alarm_row = None
            if case_data is not None:
                task_row = _single_row(
                    _rows_for_method(case_data["tables"]["h0_task"], method_id=method_id),
                    context=f"{case_data['case']['case_id']}/{method_id}/h0_task",
                )
                full_band_rows = [
                    row for row in _rows_for_method(
                        case_data["tables"]["h0_false_alarm"], method_id=method_id,
                    )
                    if str(row.get("cut_band_mode", "")).strip() == "union_full_band"
                ]
                false_alarm_row = _single_row(
                    full_band_rows,
                    context=f"{case_data['case']['case_id']}/{method_id}/union_full_band",
                )
            if task_row is None:
                events: list[bool | None] = [None] * period_count
                valid = [False] * period_count
                track_count = None
                track_complete = False
            else:
                valid_count = _integer(task_row.get("valid_period_count"))
                event_count = _integer(task_row.get("h0_event_count"))
                evaluable = (
                    str(task_row.get("status", "")).upper() == "EVALUABLE_DESCRIPTIVE_H0_SEED"
                    and valid_count == period_count
                    and event_count is not None and 0 <= event_count <= period_count
                )
                events = ([True] * event_count + [False] * (period_count - event_count)
                          if evaluable else [None] * period_count)
                valid = [True] * period_count if evaluable else [False] * period_count
                track_count = _integer(task_row.get("false_confirmed_track_count"))
                track_complete = (
                    str(task_row.get("track_status", "")).upper() == "EVALUABLE"
                    and track_count is not None and track_count >= 0
                )
            summary = h0_seed_statistics(
                period_events=events,
                valid_periods=valid,
                false_confirmed_track_count=track_count if track_complete else None,
                track_output_complete=track_complete,
                expected_period_count=period_count,
            ) | {"seed": seed}
            false_detections = _integer(task_row.get("false_detection_count")) if task_row else None
            false_clusters = _integer(task_row.get("false_cluster_count")) if task_row else None
            cut_hits = _integer(false_alarm_row.get("observed_cut_hit_count")) if false_alarm_row else None
            valid_cuts = _integer(false_alarm_row.get("valid_cut_count")) if false_alarm_row else None
            cut_fraction = (
                cut_hits / valid_cuts
                if cut_hits is not None and valid_cuts is not None
                and valid_cuts > 0 and 0 <= cut_hits <= valid_cuts else None
            )
            summary.update({
                "false_detection_count": false_detections,
                "false_cluster_count": false_clusters,
                "full_band_false_cut_count": cut_hits,
                "full_band_valid_cut_count": valid_cuts,
                "full_band_cut_hit_fraction": cut_fraction,
                "full_band_cut_status": (
                    "DESCRIPTIVE_ONLY" if cut_fraction is not None else "NOT_EVALUABLE"
                ),
            })
            rows.append(summary)
        aggregate = aggregate_h0_seed_results(
            rows, planned_seeds=seed_ids,
            bootstrap_replicates=bootstrap_replicates,
            bootstrap_seed=bootstrap_seed,
        )
        seed_bound = aggregate["seed_any_event"]["one_sided_95pct_upper_bound"]
        track_bound = aggregate["false_confirmed_track_event_rate"]["one_sided_95pct_upper_bound"]
        aggregate["seed_any_event"]["approved_limit"] = seed_event_limit
        aggregate["seed_any_event"]["gate_status"] = (
            "PASS" if seed_bound is not None and seed_bound < seed_event_limit else
            "FAIL" if seed_bound is not None else "NOT_EVALUABLE"
        )
        aggregate["false_confirmed_track_event_rate"]["approved_limit"] = track_event_limit
        aggregate["false_confirmed_track_event_rate"]["gate_status"] = (
            "PASS" if track_bound is not None and track_bound < track_event_limit else
            "FAIL" if track_bound is not None else "NOT_EVALUABLE"
        )
        aggregate["method_id"] = method_id
        aggregate["delay_error_ns"] = delay
        aggregate["seed_rows"] = rows
        evaluable_cut_rows = [row for row in rows if row.get("full_band_cut_hit_fraction") is not None]
        evaluable_detection_rows = [row for row in rows if row.get("false_detection_count") is not None]
        evaluable_cluster_rows = [row for row in rows if row.get("false_cluster_count") is not None]
        aggregate["supporting_endpoints"] = {
            "seed_mean_full_band_cut_hit_fraction": (
                sum(float(row["full_band_cut_hit_fraction"]) for row in evaluable_cut_rows)
                / len(evaluable_cut_rows) if evaluable_cut_rows else None
            ),
            "full_band_cut_evaluable_seed_count": len(evaluable_cut_rows),
            "seed_mean_false_detection_count": (
                sum(int(row["false_detection_count"]) for row in evaluable_detection_rows)
                / len(evaluable_detection_rows) if evaluable_detection_rows else None
            ),
            "false_detection_evaluable_seed_count": len(evaluable_detection_rows),
            "seed_mean_false_cluster_count": (
                sum(int(row["false_cluster_count"]) for row in evaluable_cluster_rows)
                / len(evaluable_cluster_rows) if evaluable_cluster_rows else None
            ),
            "false_cluster_evaluable_seed_count": len(evaluable_cluster_rows),
            "cut_fraction_interpretation": (
                "equal-seed descriptive supporting endpoint; not statistical Pfa and not pooled-CUT inference"
            ),
        }
        endpoints.append(aggregate)
    return {
        "status": "EVALUABLE" if endpoints else "NOT_EVALUABLE_NO_REGISTERED_METHODS",
        "primary_endpoint": H0_PRIMARY_ENDPOINT,
        "independent_unit": "seed",
        "methods": endpoints,
        "period_fraction_is_supporting_only": True,
        "cut_hit_fraction_is_descriptive_only": True,
    }


def analyze_confirmatory_matrix(
    *, registry_path: Path, checkpoint_path: Path, output_dir: Path,
    archive_root: Path | None = None,
) -> dict[str, Any]:
    """Verify all registered archives, then build fail-closed seed analyses."""
    registry_path = Path(registry_path).expanduser().resolve()
    checkpoint_path = Path(checkpoint_path).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    registry = _json_object(registry_path)
    checkpoint = _json_object(checkpoint_path)
    if executor.registry_digest(registry) != registry.get("registry_sha256"):
        raise ValueError("registry SHA-256 is invalid")
    if checkpoint.get("registry_sha256") != registry.get("registry_sha256"):
        raise ValueError("checkpoint belongs to a different registry")
    if registry.get("design_status") != "FROZEN":
        raise ValueError("confirmatory analysis requires a frozen registry")
    thresholds = executor._validate_thresholds(registry.get("frozen_thresholds"))
    contract = thresholds["confirmatory_statistical_contract"]
    cases = registry.get("cases")
    states = checkpoint.get("cases")
    if not isinstance(cases, list) or not isinstance(states, Mapping):
        raise ValueError("registry/checkpoint case inventory is malformed")
    if len(cases) != int(registry.get("case_count", -1)):
        raise ValueError("registry case count is inconsistent")
    registered_case_ids = [str(case.get("case_id", "")) for case in cases if isinstance(case, Mapping)]
    if len(registered_case_ids) != len(cases) or len(set(registered_case_ids)) != len(registered_case_ids):
        raise ValueError("registry case identities are malformed or duplicated")
    unexpected_checkpoint_cases = set(states) - set(registered_case_ids)
    if unexpected_checkpoint_cases:
        raise ValueError(
            f"checkpoint contains unregistered cases: {sorted(unexpected_checkpoint_cases)}"
        )
    verified: list[dict[str, Any]] = []
    case_failures: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("registry contains a malformed case identity")
        case_id = str(case.get("case_id", ""))
        state = states.get(case_id)
        if not isinstance(state, Mapping):
            case_failures.append({"case_id": case_id, "status": "MISSING_CHECKPOINT_STATE"})
            continue
        if state.get("case_identity_sha256") != executor.canonical_sha256(case):
            raise ValueError(f"{case_id}: checkpoint case identity digest differs from registry")
        if state.get("status") not in {"ARCHIVED", "VERIFIED", "EVALUATED"}:
            case_failures.append({
                "case_id": case_id, "status": "NOT_ARCHIVED_OR_VERIFIED",
                "checkpoint_status": state.get("status"),
            })
            continue
        try:
            verified.append(_read_verified_case(case, state, archive_root=archive_root))
        except (OSError, ValueError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
            case_failures.append({"case_id": case_id, "status": "ARCHIVE_NOT_EVALUABLE", "reason": str(exc)})

    target_cases = [item for item in verified if item["case"].get("phase") == "target_present"]
    h0_cases = [item for item in verified if item["case"].get("phase") == "h0"]
    target_speeds = sorted({float(item["speed_mps"]) for item in cases
                            if item.get("phase") == "target_present"})
    target_seeds = sorted({int(item["seed"]) for item in cases
                           if item.get("phase") == "target_present"})
    h0_seeds = sorted({int(item["seed"]) for item in cases if item.get("phase") == "h0"})
    pair_issues: list[dict[str, Any]] = []
    for phase, phase_cases in (("target_present", cases), ("h0", cases)):
        grouped: dict[tuple[int, float], set[float]] = {}
        for case in phase_cases:
            if case.get("phase") != phase:
                continue
            key = (int(case["seed"]), float(case.get("speed_mps", 0.0)))
            grouped.setdefault(key, set()).add(float(case["delay_error_ns"]))
        for key, delays in grouped.items():
            if delays != {0.0, 4.0}:
                pair_issues.append({"phase": phase, "seed_speed": key, "delays": sorted(delays)})

    target_plus_four = [
        item for item in target_cases
        if math.isclose(float(item["case"]["delay_error_ns"]), 4.0, abs_tol=1e-12)
    ]
    opportunity_rows: list[dict[str, Any]] = []
    planned_opportunity_keys: list[tuple[float, str, int]] = []
    track_seed_rows: list[dict[str, Any]] = []
    primary = {"status": "NOT_EVALUABLE_MISSING_ARCHIVES"}
    if target_plus_four and not case_failures:
        opportunity_rows, planned_opportunity_keys, track_seed_rows = _target_seed_rows(
            target_plus_four, primary_methods=PRIMARY_METHODS,
            expected_speeds=target_speeds,
        )
        primary = aggregate_seed_level_primary(
            opportunity_rows,
            expected_speeds=target_speeds,
            planned_seeds=target_seeds,
            planned_opportunity_keys=planned_opportunity_keys,
            minimum_common_support_per_speed=contract["minimum_common_support_per_speed"],
        )

    expected_target_case_count = sum(1 for case in cases if case.get("phase") == "target_present")
    expected_h0_case_count = sum(1 for case in cases if case.get("phase") == "h0")
    full_target_archives = len(target_cases) == expected_target_case_count
    full_h0_archives = len(h0_cases) == expected_h0_case_count
    complete_archive_set = full_target_archives and full_h0_archives and not case_failures and not pair_issues

    seed_results = primary.get("seed_results", [])
    evaluable_seed_results = [row for row in seed_results if row.get("status") == "EVALUABLE"]
    position_values = [float(row["primary_delta_m"]) for row in evaluable_seed_results
                       if row.get("primary_delta_m") is not None]
    replicates = int(contract["bootstrap_replicates"])
    bootstrap_seed = int(contract["bootstrap_seed"])
    if len(position_values) == len(target_seeds) and len(position_values) >= 2:
        position_interval = bootstrap_mean_interval(
            position_values, replicates=replicates, seed=bootstrap_seed,
            confidence=float(contract["position_interval_confidence"]),
            method=str(contract["position_interval_method"]),
        )
    else:
        position_interval = {
            "status": "NOT_EVALUABLE_INCOMPLETE_OR_INSUFFICIENT_SEED_SET",
            "n": len(position_values), "planned_seed_count": len(target_seeds),
            "lower": None, "upper": None,
        }
    position_mean = (
        float(position_interval["mean"])
        if position_interval.get("status") == "EVALUABLE" else
        (sum(position_values) / len(position_values) if position_values else None)
    )
    position_upper = position_interval.get("upper")
    if position_interval.get("status") != "EVALUABLE" or len(position_values) != len(target_seeds):
        position_gate_status = "NOT_EVALUABLE"
    elif contract["position_scheme"] == "A":
        position_gate_status = (
            "PASS" if float(position_upper) < 0.0
            and position_mean <= -thresholds["position_mde_m"] else "FAIL"
        )
    else:
        position_gate_status = (
            "PASS" if float(position_upper) < -thresholds["position_mde_m"] else "FAIL"
        )
    coverage_differences = [
        100.0 * (float(row["m1_coverage"]) - float(row["fixed_coverage"]))
        for row in evaluable_seed_results
        if row.get("m1_coverage") is not None and row.get("fixed_coverage") is not None
    ]
    coverage_interval = _coverage_endpoint(
        coverage_differences,
        planned_seed_count=len(target_seeds),
        method=contract.get("coverage_interval_method"),
        confidence=float(contract.get("coverage_interval_confidence", 0.95)),
        replicates=replicates, seed=bootstrap_seed + 2,
    )
    coverage_margin = thresholds["detection_coverage_noninferiority_margin_pp"]
    coverage_gate_status = (
        "PASS" if coverage_interval.get("lower") is not None
        and float(coverage_interval["lower"]) > -coverage_margin else
        "FAIL" if coverage_interval.get("lower") is not None else "NOT_EVALUABLE"
    )

    track_differences: list[float] = []
    for row in track_seed_rows:
        m1_value = row.get(PRIMARY_METHODS[0])
        fixed_value = row.get(PRIMARY_METHODS[1])
        if m1_value is not None and fixed_value is not None:
            track_differences.append(100.0 * (float(m1_value) - float(fixed_value)))
    track_interval = _coverage_endpoint(
        track_differences,
        planned_seed_count=len(target_seeds),
        method=contract.get("track_coverage_interval_method"),
        confidence=float(contract.get("track_coverage_interval_confidence", 0.95)),
        replicates=replicates, seed=bootstrap_seed + 3,
    )
    track_interval["estimand"] = "difference in fraction of periods with any confirmed target track, percentage points"
    track_interval["interpretation"] = "descriptive only unless a track noninferiority gate was approved"

    track_policy = contract.get("track_protection_policy")
    track_gate_status = "DESCRIPTIVE_ONLY"
    if track_policy == "NONINFERIORITY_GATE":
        margin = float(contract["track_coverage_noninferiority_margin_pp"])
        lower = track_interval.get("lower")
        track_gate_status = (
            "PASS" if lower is not None and float(lower) > -margin else
            "FAIL" if lower is not None else "NOT_EVALUABLE"
        )
    track_interval["gate_status"] = track_gate_status

    methods = registry.get("method_inventory", {}).get("h0", [])
    expected_h0_period_count = next((int(case["period_count"]) for case in cases
                                     if case.get("phase") == "h0"), 10)
    h0 = _h0_analysis(
        h0_cases, method_inventory=methods,
        planned_seeds=h0_seeds,
        period_count=expected_h0_period_count,
        seed_event_limit=thresholds["h0_seed_event_rate_upper_limit"],
        track_event_limit=thresholds["h0_false_confirmed_track_rate_upper_limit"],
        bootstrap_replicates=replicates, bootstrap_seed=bootstrap_seed + 4,
    ) if h0_cases else {"status": "NOT_EVALUABLE_MISSING_H0_ARCHIVES", "methods": []}

    target_primary_complete = (
        primary.get("seed_count_planned", 0) > 0
        and primary.get("seed_count_evaluable") == primary.get("seed_count_planned")
    )
    h0_endpoints_complete = bool(h0.get("methods")) and all(
        method.get("status") == "EVALUABLE_COMPLETE_H0_SEED_SET"
        for method in h0.get("methods", [])
    )
    if not complete_archive_set:
        analysis_status = "INCONCLUSIVE_MISSING_OR_UNVERIFIED_CASE_DATA"
    elif not target_primary_complete or not h0_endpoints_complete:
        analysis_status = "INCONCLUSIVE_REQUIRED_EVALUATION_DATA"
    else:
        analysis_status = "EVALUABLE_ARCHIVE_SET"

    result = {
        "schema_version": 1,
        "analysis_status": analysis_status,
        "archive_set_status": "VERIFIED_COMPLETE" if complete_archive_set else "INCOMPLETE_OR_UNVERIFIED",
        "performance_verified": False,
        "cuda_executed_by_analyzer": False,
        "ai_training": False,
        "registry_sha256": registry["registry_sha256"],
        "design_sha256": registry.get("design_sha256"),
        "archive_profile": ARCHIVE_PROFILE,
        "case_count_planned": len(cases),
        "case_count_archive_verified": len(verified),
        "archive_failures": case_failures,
        "pair_issues": pair_issues,
        "target_present": {
            "status": (
                "EVALUABLE_SEED_ROWS" if target_primary_complete else
                "PARTIAL_SEED_ROWS" if primary.get("seed_count_evaluable", 0) > 0 else
                primary.get("status", "NOT_EVALUABLE")
            ),
            "primary_methods": list(PRIMARY_METHODS),
            "planned_seed_count": len(target_seeds),
            "planned_speeds_mps": target_speeds,
            "planned_opportunity_keys": len(planned_opportunity_keys),
            "seed_level": primary,
            "position_interval": position_interval,
            "position_gate_status": position_gate_status,
            "detection_coverage_m1_minus_fixed_d3_pp": {
                **coverage_interval,
                "noninferiority_margin_pp": coverage_margin,
                "gate_status": coverage_gate_status,
            },
            "confirmed_target_period_coverage_m1_minus_fixed_d3_pp": track_interval,
        },
        "h0": h0,
        "gate_policy": {
            "position_scheme": contract["position_scheme"],
            "position_mde_m": thresholds["position_mde_m"],
            "detection_coverage_noninferiority_margin_pp": (
                thresholds["detection_coverage_noninferiority_margin_pp"]
            ),
            "h0_seed_any_event_upper_limit": thresholds["h0_seed_event_rate_upper_limit"],
            "h0_false_track_event_upper_limit": thresholds["h0_false_confirmed_track_rate_upper_limit"],
            "joint_target_present_status": (
                "PASS" if position_gate_status == "PASS" and coverage_gate_status == "PASS" else
                "FAIL" if position_gate_status == "FAIL" or coverage_gate_status == "FAIL" else
                "NOT_EVALUABLE"
            ),
        },
        "evidence_identity": [
            {"case_id": item["case"]["case_id"],
             "archive_sha256": item["archive"]["archive_sha256"],
             "archive_manifest_sha256": item["archive_manifest_sha256"]}
            for item in verified
        ],
        "limitations": [
            "Archive verification and CPU analysis do not independently reproduce Native CUDA execution.",
            "Single or incomplete seed sets are descriptive and cannot establish confirmatory performance.",
            "CUT fractions remain descriptive; they are not a full-system Pfa estimate.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "r22_v4_confirmatory_analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _write_csv(output_dir / "r22_v4_seed_level_primary.csv", seed_results)
    _write_h0_csv(output_dir / "r22_v4_h0_seed_results.csv", h0)
    return result


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    keys = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys or ["status"], lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict))
                             else value for key, value in row.items()})


def _write_h0_csv(path: Path, h0: Mapping[str, Any]) -> None:
    rows: list[dict[str, Any]] = []
    for method in h0.get("methods", []):
        for seed in method.get("seed_rows", []):
            rows.append({"method_id": method.get("method_id"), "delay_error_ns": method.get("delay_error_ns"), **seed})
    _write_csv(path, rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path)
    args = parser.parse_args()
    result = analyze_confirmatory_matrix(
        registry_path=args.registry,
        checkpoint_path=args.checkpoint,
        output_dir=args.output_dir,
        archive_root=args.archive_root,
    )
    print(json.dumps({
        "analysis_status": result["analysis_status"],
        "case_count_planned": result["case_count_planned"],
        "case_count_archive_verified": result["case_count_archive_verified"],
        "output_dir": str(args.output_dir.expanduser().resolve()),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
