#!/usr/bin/env python3
"""Fail-closed, method-level case classification for the R22 V3 screening."""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from zipfile import ZipFile


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _truth(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _text(value).lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _number(value: Any) -> float | None:
    try:
        if isinstance(value, bool) or value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _identity_value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _row_method_mode(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        _text(_identity_value(row, "method_id", "method")),
        _text(_identity_value(row, "mode", "calibration_mode")),
    )


def _gate_failure(
    run_manifest: Mapping[str, Any], pipeline_manifest: Mapping[str, Any]
) -> tuple[str | None, str | None]:
    """Return (case state, reason); absent identity is unknown, failed gate invalid."""
    area = run_manifest.get("area_clutter_gate")
    if not isinstance(area, Mapping):
        return "CASE_UNKNOWN_STOP", "area_clutter_gate_missing"
    if _text(area.get("status")).lower() not in {"passed", "pass"}:
        return "CASE_INVALID", "area_clutter_gate_not_passed"
    packets = _number(area.get("continuous_area_packets"))
    samples = _number(area.get("continuous_area_samples"))
    if packets is None or samples is None:
        return "CASE_UNKNOWN_STOP", "area_injection_counts_missing_or_invalid"
    if packets <= 0 or samples <= 0:
        return "CASE_INVALID", "continuous_area_clutter_not_injected"
    if _number(_identity_value(area, "mean_power", "area_mean_power_measured")) is None:
        return "CASE_UNKNOWN_STOP", "area_clutter_power_missing"
    if _number(area.get("ground_range_min_m")) is None or _number(area.get("ground_range_max_m")) is None:
        return "CASE_UNKNOWN_STOP", "area_clutter_ground_range_missing"

    run_evidence = run_manifest.get("evidence_gate")
    pipe_evidence = pipeline_manifest.get("evidence_gate")
    if not isinstance(run_evidence, Mapping) or not isinstance(pipe_evidence, Mapping):
        return "CASE_UNKNOWN_STOP", "causal_evidence_gate_missing"
    if (run_evidence.get("causal_triplets") != "passed"
            or pipe_evidence.get("causal_triplets") != "passed"):
        return "CASE_INVALID", "causal_triplet_gate_not_passed"
    truth_sources = (
        (run_evidence, run_manifest),
        (pipe_evidence, pipeline_manifest),
    )
    for evidence, parent in truth_sources:
        if _truth(evidence.get("fallback_to_current")) is not False:
            return "CASE_INVALID", "fallback_to_current_true_or_unverified"
        truth_used = _truth(
            evidence.get("truth_used_in_estimator", parent.get("truth_used_in_estimator"))
        )
        if truth_used is not False:
            return "CASE_INVALID", "truth_used_or_unverified_in_estimator"

    design = run_manifest.get("design")
    if not isinstance(design, Mapping):
        return "CASE_UNKNOWN_STOP", "design_identity_missing"
    for field in ("seed", "delay_error_ns", "target_count", "period_count"):
        if design.get(field) is None:
            return "CASE_UNKNOWN_STOP", f"design_{field}_missing"

    source = run_manifest.get("source_identity")
    stage2 = run_manifest.get("stage2_binary")
    production = run_manifest.get("production_binary")
    if not isinstance(source, Mapping) or not _text(source.get("commit")):
        return "CASE_UNKNOWN_STOP", "source_identity_missing"
    for name, identity in (("stage2", stage2), ("production", production)):
        if not isinstance(identity, Mapping) or not _text(identity.get("sha256")):
            return "CASE_UNKNOWN_STOP", f"{name}_binary_identity_missing"
    if _text(production.get("backend")).lower() not in {"local native cuda", "native cuda"}:
        return "CASE_INVALID", "production_backend_not_native_cuda"
    if _truth(production.get("use_corex")) is not False:
        return "CASE_INVALID", "production_use_corex_true_or_unverified"

    simulation = pipeline_manifest.get("simulation")
    if not isinstance(simulation, Mapping):
        return "CASE_UNKNOWN_STOP", "paired_simulation_status_missing"
    for role in ("A0_OFF", "A0_ON", "A0_TO"):
        entry = simulation.get(role)
        if not isinstance(entry, Mapping):
            return "CASE_UNKNOWN_STOP", f"paired_simulation_{role}_missing"
        if _text(entry.get("status")).lower() not in {"passed", "pass"}:
            return "CASE_INVALID", f"paired_simulation_{role}_failed"
        if _number(entry.get("returncode")) != 0:
            return "CASE_INVALID", f"paired_simulation_{role}_returncode_nonzero"

    pair = pipeline_manifest.get("pair_identity")
    if not isinstance(pair, Mapping):
        return "CASE_UNKNOWN_STOP", "pair_identity_missing"
    for field in ("target_id", "background_id", "production_identity"):
        if not _text(pair.get(field)):
            return "CASE_UNKNOWN_STOP", f"pair_{field}_missing"
    seed = _number(design.get("seed"))
    pair_seed = _number(pair.get("seed"))
    delay = _number(design.get("delay_error_ns"))
    pair_delay = _number(pair.get("delay_error_ns"))
    if seed is None or pair_seed is None or seed != pair_seed:
        return "CASE_INVALID", "pair_seed_mismatch_or_invalid"
    if delay is None or pair_delay is None or delay != pair_delay:
        return "CASE_INVALID", "pair_delay_mismatch_or_invalid"

    return None, None


def _branch_ok(branch: Mapping[str, Any], *, min_support: int) -> tuple[bool, str]:
    if _text(branch.get("status")).lower() not in {"passed", "pass"}:
        return False, "branch_status_not_passed"
    if _text(branch.get("production_status")).lower() not in {"passed", "pass"}:
        return False, "production_status_not_passed"
    if _number(branch.get("production_returncode")) != 0:
        return False, "production_returncode_nonzero_or_missing"
    if _truth(branch.get("truth_used_in_estimator")) is not False:
        return False, "truth_used_or_unverified_in_method_estimator"
    if _text(branch.get("method_id")) == "C4":
        overrides = branch.get("xml_overrides")
        runtime = branch.get("runtime_calibration_config")
        if not isinstance(overrides, Mapping) or not isinstance(runtime, Mapping):
            return False, "c4_runtime_xml_or_override_evidence_missing"
        expected_mode = _text(branch.get("mode"))
        for config in (overrides, runtime):
            if _text(config.get("research_calibration_enable")).lower() != "true":
                return False, "c4_calibration_not_enabled"
            if _text(config.get("research_calibration_method")) != "robust_ddc_rb":
                return False, "c4_calibration_method_mismatch"
            if _text(config.get("research_calibration_mode")) != expected_mode:
                return False, "c4_calibration_mode_mismatch"
            if _number(config.get("research_calibration_min_support")) != min_support:
                return False, "c4_min_support_mismatch"
            if _number(config.get("research_calibration_range_band_bins")) != 133:
                return False, "c4_range_band_bins_mismatch"
            threshold = _number(config.get("research_calibration_robust_phase_threshold_rad"))
            if threshold is None or abs(threshold - 0.35) > 1e-12:
                return False, "c4_robust_phase_threshold_mismatch"
    geometry = branch.get("cfar_geometry")
    summary = branch.get("cfar_summary")
    if not isinstance(geometry, Mapping) or not isinstance(summary, Mapping):
        return False, "cfar_geometry_or_summary_missing"
    if _text(geometry.get("branch_geometry_status")).lower() not in {"evaluable", "passed"}:
        return False, "cfar_geometry_not_evaluable"
    if _text(summary.get("status")).lower() not in {"passed", "evaluable"}:
        return False, "cfar_summary_not_passed"
    if _text(summary.get("valid_cut_denominator_status")).lower() != "passed":
        return False, "valid_cut_denominator_not_passed"
    expected_periods = int(_number(summary.get("expected_period_count")) or 0)
    row_count = int(_number(summary.get("valid_cut_denominator_row_count")) or 0)
    summaries = summary.get("summaries")
    if expected_periods <= 0 or row_count <= 0 or not isinstance(summaries, list):
        return False, "valid_cut_or_period_summary_missing"
    if int(_number(summary.get("summary_count")) or -1) != expected_periods:
        return False, "cfar_summary_period_count_mismatch"
    if len(summaries) != expected_periods:
        return False, "cfar_summary_rows_incomplete"
    for payload_name, payload in (("geometry", geometry), ("summary", summary)):
        branch_summaries = payload.get("branch_summaries")
        if not isinstance(branch_summaries, Mapping):
            return False, f"cfar_{payload_name}_branch_summaries_missing"
        for branch_name in ("union_dynamic_in_band", "union_full_band"):
            branch_summary = branch_summaries.get(branch_name)
            if not isinstance(branch_summary, Mapping):
                return False, f"cfar_{payload_name}_{branch_name}_missing"
            period_ids = branch_summary.get("period_ids")
            valid_cuts = _number(branch_summary.get("valid_cut_count"))
            if (_text(branch_summary.get("status")).lower() not in {"evaluable", "passed"}
                    or int(_number(branch_summary.get("period_count")) or 0) != expected_periods
                    or not isinstance(period_ids, list) or len(period_ids) != expected_periods
                    or valid_cuts is None or valid_cuts <= 0):
                return False, f"cfar_{payload_name}_{branch_name}_invalid"
    return True, ""


def _reference_gap_rows(
    rows: Sequence[Mapping[str, Any]], method_id: str, mode: str, min_support: int
) -> tuple[bool, dict[str, Any] | None]:
    matching = [row for row in rows if _row_method_mode(row) == (method_id, mode)]
    for row in matching:
        support = _number(row.get("support_count"))
        reason = _text(row.get("reason"))
        status = _text(row.get("status")).upper()
        truth_used = _truth(_identity_value(row, "truth_used_in_estimator", "truth_used"))
        if (status == "NOT_EVALUABLE" and reason == "insufficient_robust_support"
                and support is not None and support < min_support and truth_used is False):
            return True, {
                "reason": reason,
                "support_count": int(support) if support.is_integer() else support,
                "min_support": min_support,
                "truth_used_in_estimator": False,
                "reference_row": dict(row),
            }
    return False, None


def classify_method_level_case(
    run_manifest: Mapping[str, Any],
    pipeline_manifest: Mapping[str, Any],
    task_rows: Sequence[Mapping[str, Any]],
    branch_manifests: Sequence[Mapping[str, Any]],
    reference_rows: Sequence[Mapping[str, Any]],
    expected_units: Sequence[Mapping[str, Any]],
    *,
    min_support: int = 8,
    recorded: bool = False,
    original_matrix_status: str | None = None,
) -> dict[str, Any]:
    """Classify one case without converting a local reference gap into a miss.

    ``expected_units`` is the frozen applicability denominator. Missing or
    duplicate observations always remain in that denominator and stop the case.
    Historical archive inspection is explicitly not a V3 execution.
    """
    if min_support <= 0:
        raise ValueError("min_support must be positive")

    units = [(str(item.get("method_id", "")), str(item.get("mode", "")))
             for item in expected_units]
    duplicate_expected = len(units) != len(set(units))
    if not units or any(not method_id or not mode for method_id, mode in units):
        return {
            "scenario_status": "CASE_UNKNOWN_STOP",
            "scenario_reason": "frozen_expected_method_units_missing_or_invalid",
            "original_matrix_status": original_matrix_status,
            "method_units": [],
            "applicable_count": len(units),
            "evaluable_count": 0,
            "reference_gap_count": 0,
            "unknown_count": len(units),
            "continue_matrix": False,
        }
    shared_state, shared_reason = _gate_failure(run_manifest, pipeline_manifest)
    if shared_state is not None:
        method_results = [{
            "method_id": method_id, "mode": mode, "applicable": True,
            "status": "NOT_RUN_GLOBAL_GATE", "reason": shared_reason,
        } for method_id, mode in units]
        if recorded and shared_state == "CASE_INVALID":
            scenario_status = "CASE_RECORDED_SHARED_GATE_FAILED_NOT_REPLAYED"
        else:
            scenario_status = shared_state
        return {
            "scenario_status": scenario_status,
            "scenario_reason": shared_reason,
            "original_matrix_status": original_matrix_status,
            "method_units": method_results,
            "applicable_count": len(units),
            "evaluable_count": 0,
            "reference_gap_count": 0,
            "unknown_count": 0,
            "continue_matrix": False,
        }

    method_results: list[dict[str, Any]] = []
    for method_id, mode in units:
        rows = [row for row in task_rows if _row_method_mode(row) == (method_id, mode)]
        counts = Counter(_text(row.get("role")).upper() for row in rows)
        if duplicate_expected or any(counts[role] != 1 for role in ("OFF", "ON", "TO")):
            method_results.append({
                "method_id": method_id, "mode": mode, "applicable": True,
                "status": "UNKNOWN_STOP", "reason": "missing_or_duplicate_expected_task_rows",
            })
            continue

        by_role = {_text(row.get("role")).upper(): row for row in rows}
        task_states = {role: _text(by_role[role].get("status")).upper()
                       for role in ("OFF", "ON", "TO")}
        task_reasons = {role: _text(by_role[role].get("reason"))
                        for role in ("OFF", "ON", "TO")}

        branch_rows = [branch for branch in branch_manifests
                       if _row_method_mode(branch) == (method_id, mode)]
        branch_counts = Counter(_text(branch.get("role")).upper() for branch in branch_rows)
        branches_by_role = {_text(branch.get("role")).upper(): branch for branch in branch_rows}
        if any(count > 1 for count in branch_counts.values()):
            method_results.append({
                "method_id": method_id, "mode": mode, "applicable": True,
                "status": "UNKNOWN_STOP", "reason": "duplicate_production_branch_manifest",
            })
            continue
        branch_checks = {
            role: _branch_ok(branch, min_support=min_support)
            for role, branch in branches_by_role.items()
        }
        if any(not ok for ok, _ in branch_checks.values()):
            failed_role = next(role for role, (ok, _) in branch_checks.items() if not ok)
            method_results.append({
                "method_id": method_id, "mode": mode, "applicable": True,
                "status": "UNKNOWN_STOP", "reason": f"{failed_role.lower()}_{branch_checks[failed_role][1]}",
            })
            continue

        reference_match = [row for row in reference_rows
                           if _row_method_mode(row) == (method_id, mode)]
        bad_reference = [row for row in reference_match
                         if _text(row.get("status")).upper() not in {"OK", "PASSED", "EVALUABLE"}]
        if task_states["ON"] == "EVALUABLE" and task_states["TO"] == "EVALUABLE":
            if task_states["OFF"] not in {"EVALUABLE", "NOT_APPLICABLE_H0_ROLE", "NOT_APPLICABLE"}:
                method_results.append({
                    "method_id": method_id, "mode": mode, "applicable": True,
                    "status": "UNKNOWN_STOP", "reason": "off_task_state_unexpected",
                })
            elif bad_reference:
                method_results.append({
                    "method_id": method_id, "mode": mode, "applicable": True,
                    "status": "UNKNOWN_STOP", "reason": "task_evaluable_with_invalid_reference_rows",
                })
            elif any(branch_counts[role] != 1 for role in ("OFF", "ON", "TO")):
                method_results.append({
                    "method_id": method_id, "mode": mode, "applicable": True,
                    "status": "UNKNOWN_STOP", "reason": "required_production_branch_missing",
                })
            else:
                method_results.append({
                    "method_id": method_id, "mode": mode, "applicable": True,
                    "status": "EVALUABLE", "reason": "all_method_task_and_production_gates_passed",
                })
            continue

        local_gap, gap_evidence = _reference_gap_rows(reference_rows, method_id, mode, min_support)
        local_reasons = (
            all(
                task_states[role] == "NOT_EVALUABLE"
                and task_reasons[role] == "reference_status_not_ok"
                for role in ("ON", "TO")
            )
            and (
                task_states["OFF"] in {"NOT_APPLICABLE_H0_ROLE", "NOT_APPLICABLE"}
                or (
                    task_states["OFF"] == "NOT_EVALUABLE"
                    and task_reasons["OFF"] == "reference_status_not_ok"
                )
            )
        )
        on_branch_ok = branch_counts["ON"] == 1 and branch_checks.get("ON", (False, ""))[0]
        allowed_roles = {"ON", "OFF", "TO"}
        only_expected_branches = set(branch_counts) <= allowed_roles
        no_fallback = (
            _truth(run_manifest.get("evidence_gate", {}).get("fallback_to_current")) is False
            and _truth(pipeline_manifest.get("evidence_gate", {}).get("fallback_to_current")) is False
        )
        if local_gap and local_reasons and on_branch_ok and only_expected_branches and no_fallback:
            method_results.append({
                "method_id": method_id, "mode": mode, "applicable": True,
                "status": "REFERENCE_NOT_EVALUABLE", "reason": "insufficient_robust_support",
                "reference_gap": gap_evidence,
            })
        else:
            reason = "unexplained_method_task_failure"
            if not reference_match:
                reason = "task_failure_without_reference_evidence"
            elif bad_reference:
                reason = "reference_failure_not_proven_as_frozen_support_gap"
            method_results.append({
                "method_id": method_id, "mode": mode, "applicable": True,
                "status": "UNKNOWN_STOP", "reason": reason,
            })

    evaluable_count = sum(item["status"] == "EVALUABLE" for item in method_results)
    local_gap_count = sum(item["status"] == "REFERENCE_NOT_EVALUABLE" for item in method_results)
    unknown_count = sum(item["status"] == "UNKNOWN_STOP" for item in method_results)

    if duplicate_expected:
        state, reason = "CASE_UNKNOWN_STOP", "duplicate_expected_method_unit"
    else:
        if unknown_count:
            state, reason = "CASE_UNKNOWN_STOP", "one_or_more_method_units_unknown"
        elif local_gap_count:
            state, reason = "CASE_VALID_WITH_METHOD_GAPS", "verified_method_reference_support_gap"
        else:
            state, reason = "CASE_VALID", "all_expected_method_units_evaluable"

    if recorded and state in {"CASE_VALID", "CASE_VALID_WITH_METHOD_GAPS"}:
        state = "CASE_RECORDED_GATE_PASS_NOT_REPLAYED"
        reason = "historical_archive_gate_evidence_only"

    return {
        "scenario_status": state,
        "scenario_reason": reason,
        "original_matrix_status": original_matrix_status,
        "method_units": method_results,
        "applicable_count": len(units),
        "evaluable_count": evaluable_count,
        "reference_gap_count": local_gap_count,
        "unknown_count": unknown_count,
        "continue_matrix": not recorded and state in {
            "CASE_VALID", "CASE_VALID_WITH_METHOD_GAPS"
        },
    }


def _csv_rows(data: bytes) -> list[dict[str, Any]]:
    return [dict(row) for row in csv.DictReader(io.StringIO(data.decode("utf-8-sig")))]


def _c4_runtime_config(data: bytes) -> dict[str, str]:
    root = ET.fromstring(data)
    names = (
        "research_calibration_enable", "research_calibration_method",
        "research_calibration_mode", "research_calibration_min_support",
        "research_calibration_range_band_bins",
        "research_calibration_robust_phase_threshold_rad",
    )
    result: dict[str, str] = {}
    for name in names:
        element = root.find(f".//{name}")
        if element is not None:
            result[name] = _text(element.text)
    return result


def _condition_key(value: Any) -> str:
    return "".join(ch.lower() for ch in _text(value) if ch.isalnum())


def _annotate_method_identity(
    rows: Sequence[Mapping[str, Any]], task_rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    condition_methods: dict[str, set[tuple[str, str]]] = {}
    for task in task_rows:
        condition_key = _condition_key(task.get("condition"))
        identity = _row_method_mode(task)
        if condition_key and all(identity):
            condition_methods.setdefault(condition_key, set()).add(identity)
        if identity[1].lower() == "not_applicable" and all(identity):
            condition_methods.setdefault(_condition_key(identity[0]), set()).add(identity)

    annotated: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        condition_key = _condition_key(row.get("condition"))
        candidates = condition_methods.get(condition_key, set())
        if len(candidates) == 1:
            row["method_id"], row["mode"] = next(iter(candidates))
        annotated.append(row)
    return annotated


def _case_inputs_from_members(members: Mapping[str, bytes]) -> tuple[
    dict[str, Any], dict[str, Any], list[dict[str, Any]],
    list[dict[str, Any]], list[dict[str, Any]],
]:
    required = {"r22_run_manifest.json", "case_manifest_review_extract.json", "evaluation/task_metrics.csv"}
    missing = required - set(members)
    if missing:
        raise ValueError(f"archive missing required evidence members: {sorted(missing)}")
    run_manifest = json.loads(members["r22_run_manifest.json"])
    pipeline_manifest = json.loads(members["case_manifest_review_extract.json"])
    task_rows = _csv_rows(members["evaluation/task_metrics.csv"])

    branch_manifests: list[dict[str, Any]] = []
    reference_rows: list[dict[str, Any]] = []
    for name, content in members.items():
        if not name.endswith("/branch_manifest.json"):
            continue
        branch = json.loads(content)
        if _text(branch.get("role")).upper() == "ALL":
            continue
        branch["condition"] = branch.get("condition", "")
        branch_dir = str(PurePosixPath(name).parent)
        runtime_xml_name = f"{branch_dir}/production.xml"
        if runtime_xml_name in members:
            branch["runtime_calibration_config"] = _c4_runtime_config(members[runtime_xml_name])
        branch_manifests.append(branch)
        gamma_name = f"{branch_dir}/result/production_calibration_reference_gamma.csv"
        if gamma_name in members:
            for gamma in _csv_rows(members[gamma_name]):
                gamma["condition"] = branch.get("condition", "")
                reference_rows.append(gamma)

    branch_manifests = _annotate_method_identity(branch_manifests, task_rows)
    reference_rows = _annotate_method_identity(reference_rows, task_rows)
    return run_manifest, pipeline_manifest, task_rows, branch_manifests, reference_rows


def classify_recorded_v2_archive(
    archive_path: str | Path,
    expected_units: Sequence[Mapping[str, Any]],
    *,
    original_matrix_status: str,
    min_support: int = 8,
) -> dict[str, Any]:
    """Read a compact V2 ZIP and classify its saved evidence as historical only."""
    archive = Path(archive_path)
    with ZipFile(archive) as stream:
        bad_member = stream.testzip()
        if bad_member is not None:
            raise ValueError(f"archive CRC check failed at member: {bad_member}")
        names = stream.namelist()
        if len(names) != len(set(names)):
            raise ValueError("archive contains duplicate member names")
        selected_names = {
            name for name in names
            if name in {
                "r22_run_manifest.json", "case_manifest_review_extract.json",
                "evaluation/task_metrics.csv",
            }
            or name.endswith("/branch_manifest.json")
            or name.endswith("/production.xml")
            or name.endswith("/result/production_calibration_reference_gamma.csv")
        }
        members = {name: stream.read(name) for name in selected_names}
    run_manifest, pipeline_manifest, task_rows, branch_manifests, reference_rows = (
        _case_inputs_from_members(members)
    )
    result = classify_method_level_case(
        run_manifest, pipeline_manifest, task_rows, branch_manifests,
        reference_rows, expected_units, min_support=min_support, recorded=True,
        original_matrix_status=original_matrix_status,
    )
    result["archive_path"] = str(archive)
    result["archive_status"] = "CRC_VERIFIED_READ_ONLY"
    result["v3_execution"] = False
    result["recorded_task_rows"] = len(task_rows)
    result["recorded_on_task_rows"] = sum(
        _text(row.get("role")).upper() == "ON" for row in task_rows
    )
    result["recorded_task_role_counts"] = dict(
        Counter(_text(row.get("role")).upper() for row in task_rows)
    )
    result["recorded_delay_error_ns"] = run_manifest.get("design", {}).get("delay_error_ns")
    result["recorded_task_status_counts"] = dict(
        Counter(_text(row.get("status")).upper() for row in task_rows)
    )
    result["recorded_aggregate_gates"] = {
        key: run_manifest.get("evidence_gate", {}).get(key)
        for key in (
            "valid_cut_denominators", "unique_final_union_hits", "empirical_pfa",
        )
    }
    return result


def classify_v3_case(
    case_root: str | Path,
    run_manifest: Mapping[str, Any] | None = None,
    *,
    expected_units: Sequence[Mapping[str, Any]] | None = None,
    min_support: int = 8,
) -> dict[str, Any]:
    """Load one live V3 case directory and classify its explicit expected units."""
    root = Path(case_root)
    run_path = root / "r22_run_manifest.json"
    run = dict(run_manifest) if run_manifest is not None else json.loads(run_path.read_text())
    units = expected_units
    if units is None:
        declared = run.get("expected_method_units")
        if not isinstance(declared, list):
            raise ValueError("V3 run manifest must freeze expected_method_units")
        units = declared

    case_manifests = list((root / "pipeline" / "cases").glob("*/case_manifest.json"))
    if len(case_manifests) != 1:
        raise ValueError(f"expected one pipeline case manifest, found {len(case_manifests)}")
    pipeline_manifest = json.loads(case_manifests[0].read_text(encoding="utf-8"))
    task_path = root / "evaluation" / "task_metrics.csv"
    task_rows = _csv_rows(task_path.read_bytes())
    case_root = case_manifests[0].parent
    branches: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    for branch_path in sorted((case_root / "production").glob("*/branch_manifest.json")):
        branch = json.loads(branch_path.read_text(encoding="utf-8"))
        if _text(branch.get("role")).upper() == "ALL":
            continue
        runtime_xml = branch_path.parent / "production.xml"
        if runtime_xml.is_file():
            branch["runtime_calibration_config"] = _c4_runtime_config(runtime_xml.read_bytes())
        branches.append(branch)
        gamma_path = branch_path.parent / "result" / "production_calibration_reference_gamma.csv"
        if gamma_path.is_file():
            for row in _csv_rows(gamma_path.read_bytes()):
                row["condition"] = branch.get("condition", "")
                references.append(row)
    branches = _annotate_method_identity(branches, task_rows)
    references = _annotate_method_identity(references, task_rows)
    return classify_method_level_case(
        run, pipeline_manifest, task_rows, branches, references, units,
        min_support=min_support,
    )
