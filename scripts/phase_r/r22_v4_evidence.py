#!/usr/bin/env python3
"""Output-complete target-response evidence for the R22 V4 contract."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from scripts.phase_r import evaluate_r3_minimal_cuda_causal_pilot as r3
from scripts.phase_r.r22_v4_identity import (
    branch_group_key,
    branch_identity,
    branch_identity_key,
    validate_branch_manifest_identity,
)


_ROLES = ("ON", "OFF", "TO")
_ROI_HALF_DOPPLER = 2
_ROI_HALF_RANGE = 2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_record(path: Path, root: Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = str(resolved)
    return {
        "path": relative,
        "exists": resolved.is_file(),
        "bytes": resolved.stat().st_size if resolved.is_file() else None,
        "sha256": _sha256(resolved) if resolved.is_file() else None,
    }


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _truth_rows(
    simulation: Mapping[str, Any], period_count: int, case_root: Path,
) -> tuple[Path, dict[int, list[dict[str, str]]], dict[str, Any]]:
    roles = ("A0_ON", "A1_ON", "A0_TO", "A1_TO")
    by_role: dict[str, tuple[Path, dict[tuple[str, str, str, str], dict[str, str]]]] = {}
    scenario_sources: dict[str, dict[str, Any]] = {}
    seeds_by_role: dict[str, int] = {}
    case_manifest_path = Path(case_root) / "case_manifest.json"
    if not case_manifest_path.is_file():
        raise FileNotFoundError("V4 truth mapping requires the pipeline case_manifest.json seed identity")
    case_manifest = json.loads(case_manifest_path.read_text(encoding="utf-8"))
    scene_identity = case_manifest.get("scene_identity")
    case_record = case_manifest.get("case")
    raw_seed = (
        scene_identity.get("seed") if isinstance(scene_identity, Mapping) else
        case_record.get("seed") if isinstance(case_record, Mapping) else None
    )
    if isinstance(raw_seed, bool) or not isinstance(raw_seed, int):
        raise ValueError("V4 pipeline case manifest lacks an integer scene seed")
    case_seed = int(raw_seed)
    compared_fields = ("range_m", "af_total_truth_hz", "visible", "target_state")
    for role in roles:
        source = simulation.get(role)
        if not isinstance(source, Mapping) or not source.get("output_dir"):
            raise ValueError(f"truth mapping differs: missing simulation truth source {role}")
        scenario_value = source.get("scenario")
        if not scenario_value:
            raise ValueError(f"truth mapping differs: missing source scenario identity {role}")
        scenario_path = Path(str(scenario_value)).expanduser().resolve()
        try:
            scenario_path.relative_to(Path(case_root).resolve())
        except ValueError as exc:
            raise ValueError(f"truth mapping scenario is outside pipeline case: {role}") from exc
        if not scenario_path.is_file():
            raise FileNotFoundError(f"truth mapping scenario is missing for {role}: {scenario_path}")
        declared_scenario_sha = source.get("scenario_sha256")
        actual_scenario_sha = _sha256(scenario_path)
        if declared_scenario_sha and declared_scenario_sha != actual_scenario_sha:
            raise ValueError(f"truth mapping scenario SHA-256 mismatch for {role}")
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        random_config = scenario.get("random")
        stage1_identity = scenario.get("stage1_scene_identity")
        scenario_seed = (
            random_config.get("random_seed") if isinstance(random_config, Mapping) else None
        )
        identity_seed = (
            stage1_identity.get("seed") if isinstance(stage1_identity, Mapping) else None
        )
        if (isinstance(scenario_seed, bool) or not isinstance(scenario_seed, int)
                or isinstance(identity_seed, bool) or not isinstance(identity_seed, int)
                or scenario_seed != identity_seed or scenario_seed != case_seed):
            raise ValueError(f"truth mapping seed identity differs for {role}")
        seeds_by_role[role] = int(scenario_seed)
        scenario_sources[role] = {
            **_file_record(scenario_path, Path(case_root)),
            "seed": int(scenario_seed),
        }
        output = Path(str(source["output_dir"])).expanduser().resolve()
        path = output / "truth" / "truth_targets_by_beam.csv"
        if not path.is_file():
            raise FileNotFoundError(f"target truth file is missing for {role}: {path}")
        keyed: dict[tuple[str, str, str, str], dict[str, str]] = {}
        for row in r3._read_rows(path):
            try:
                period_id = int(row["period_id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"truth mapping differs: invalid period_id in {role}: {path}") from exc
            if period_id not in range(period_count):
                continue
            target_id = str(row.get("target_id", "")).strip()
            beam_id = str(row.get("beam_id", "50")).strip()
            pulse_id = str(row.get("pulse_id", "0") or "0").strip()
            if not target_id:
                raise ValueError(f"truth mapping differs: target_id is empty in {role}: {path}")
            key = (target_id, str(period_id), beam_id, pulse_id)
            if key in keyed:
                raise ValueError(f"truth mapping differs: duplicate target-period-beam-pulse key {key} in {role}")
            normalized = dict(row)
            normalized["target_state"] = str(
                row.get("target_state") or row.get("state") or row.get("visible", "")
            )
            keyed[key] = normalized
        by_role[role] = (path, keyed)

    reference_path, reference = by_role["A0_ON"]
    reference_keys = set(reference)
    for role in roles[1:]:
        path, candidate = by_role[role]
        if set(candidate) != reference_keys:
            raise ValueError(
                f"truth mapping differs: A0_ON vs {role} target-period-beam-pulse identities"
            )
        for key in sorted(reference_keys):
            for field in compared_fields:
                left = reference[key].get(field)
                right = candidate[key].get(field)
                if field in {"range_m", "af_total_truth_hz"}:
                    try:
                        equal = math.isclose(float(left), float(right), rel_tol=1e-10, abs_tol=1e-6)
                    except (TypeError, ValueError):
                        equal = False
                else:
                    equal = str(left) == str(right)
                if not equal:
                    raise ValueError(
                        f"truth mapping differs: {role} {field} for identity {key}: {left!r} != {right!r}"
                    )
    by_period: dict[int, list[dict[str, str]]] = {period: [] for period in range(period_count)}
    for key, row in reference.items():
        period_id = int(key[1])
        if key[2] not in {"50", "050"} or key[3] not in {"0", ""}:
            continue
        by_period[period_id].append(row)
    for period_id, rows in by_period.items():
        target_ids = [row.get("target_id", "") for row in rows]
        if len(set(target_ids)) != len(target_ids):
            raise ValueError(f"duplicate target truth rows in period {period_id}")
    audit = {
        "status": "passed",
        "case_seed": case_seed,
        "seeds_by_role": seeds_by_role,
        "scenario_sources": scenario_sources,
        "roles_compared": list(roles),
        "compared_identity_fields": ["target_id", "period_id", "beam_id", "pulse_id"],
        "compared_value_fields": list(compared_fields),
        "identity_row_count": len(reference_keys),
        "rows_by_role": {role: len(rows) for role, (_, rows) in by_role.items()},
        "sources": {
            role: _file_record(path, case_root)
            for role, (path, _) in by_role.items()
        },
    }
    return reference_path, by_period, audit


def _target_center(fa_axis: np.ndarray, range_axis: np.ndarray, truth: Mapping[str, str]) -> tuple[int, int]:
    if fa_axis.size < 2 or range_axis.size == 0:
        raise ValueError("CSI tap axes are invalid")
    fa = float(truth["af_total_truth_hz"])
    step = float(np.median(np.diff(fa_axis)))
    prf = step * fa_axis.size
    center = 0.5 * (float(fa_axis[0]) + float(fa_axis[-1]))
    wrapped = fa + round((center - fa) / prf) * prf
    return int(np.argmin(np.abs(fa_axis - wrapped))), int(
        np.argmin(np.abs(range_axis - float(truth["range_m"])))
    )


def _roi_bounds(shape: tuple[int, int], row: int, col: int) -> tuple[int, int, int, int]:
    return (
        max(0, row - _ROI_HALF_DOPPLER),
        min(shape[0], row + _ROI_HALF_DOPPLER + 1),
        max(0, col - _ROI_HALF_RANGE),
        min(shape[1], col + _ROI_HALF_RANGE + 1),
    )


def _branch_state(
    case_root: Path,
    record: Mapping[str, Any],
    identity: Mapping[str, Any],
    period_id: int,
    tap_row: Mapping[str, str],
) -> dict[str, Any]:
    branch_root_value = record.get("branch_root")
    branch_root = Path(str(branch_root_value)).expanduser() if branch_root_value else Path()
    if not branch_root_value or not branch_root.is_dir():
        branch_root = case_root / "production" / str(record.get("branch_name", ""))
    branch_root = branch_root.resolve()
    calibration_files = []
    result_raw = Path(str(record.get("result_dir", "")))
    result_dir = result_raw if result_raw.is_absolute() else branch_root / result_raw
    if result_dir.is_dir():
        for name in ("production_calibration_reference_gamma.csv", "production_calibration_adapter.csv"):
            calibration_files.extend(sorted(result_dir.rglob(name)))
    period_corrections = record.get("period_corrections", [])
    period_correction = next((item for item in period_corrections
                              if isinstance(item, Mapping)
                              and int(item.get("period_id", -1)) == period_id), None)
    production_xml_raw = Path(str(record.get("production_xml") or branch_root / "production.xml"))
    production_xml = production_xml_raw if production_xml_raw.is_absolute() else branch_root / production_xml_raw
    branch_manifest = branch_root / "branch_manifest.json"
    return {
        **dict(identity),
        "correction_source": record.get("correction_source"),
        "applied_delay_ns": record.get("applied_delay_ns"),
        "period_correction": dict(period_correction) if period_correction else None,
        "truth_used_in_estimator": record.get("truth_used_in_estimator"),
        "production_xml": _file_record(production_xml, case_root),
        "branch_manifest": _file_record(branch_manifest, case_root),
        "calibration_reference_files": [_file_record(path, case_root) for path in calibration_files],
        "tap_alignment_mode": tap_row.get("alignment_mode"),
        "tap_after_definition": tap_row.get("after_definition"),
        "tap_p38_k_rad_per_hz": tap_row.get("p38_k_rad_per_hz"),
        "tap_p38_b_rad": tap_row.get("p38_b_rad"),
    }


def extract_target_complex_response(
    case_root: Path,
    *,
    simulation: Mapping[str, Any],
    branch_records: Sequence[Mapping[str, Any]],
    target_count: int,
    period_count: int,
    case_id: str | None = None,
) -> dict[str, Any]:
    """Save truth-aligned CSI ROIs with mode- and role-complete identities."""
    root = Path(case_root).expanduser().resolve()
    output = root / "r22_v4_output_evidence"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite V4 response evidence: {output}")
    if target_count < 0 or period_count <= 0:
        raise ValueError("target_count and period_count are invalid")
    resolved_case_id = case_id
    case_manifest_path = root / "case_manifest.json"
    if resolved_case_id is None and case_manifest_path.is_file():
        case_manifest = json.loads(case_manifest_path.read_text(encoding="utf-8"))
        resolved_case_id = str(case_manifest.get("case_id") or "") or None
    if resolved_case_id is None:
        resolved_case_id = next((str(row.get("case_id")) for row in branch_records
                                 if row.get("case_id")), root.name)

    truth_audit: dict[str, Any]
    if target_count > 0:
        truth_path, truths, truth_audit = _truth_rows(simulation, period_count, root)
    else:
        truth_path = None
        truths = {period: [] for period in range(period_count)}
        truth_audit = {
            "status": "NOT_APPLICABLE_NO_TARGETS",
            "roles_compared": [],
            "identity_row_count": 0,
            "rows_by_role": {},
            "sources": {},
        }
    if target_count > 0:
        if any(not truths[period] for period in range(period_count)):
            raise ValueError("visible target truth is missing for a required period")
        expected_ids = {row.get("target_id", "") for row in truths[0]}
        if len(expected_ids) != target_count:
            raise ValueError(
                f"truth target count differs from design: expected {target_count}, found {len(expected_ids)}"
            )
        for period, rows in truths.items():
            if {row.get("target_id", "") for row in rows} != expected_ids:
                raise ValueError(f"target identity set differs in period {period}")
    elif any(truths.values()):
        raise ValueError("target-free case contains target truth rows")

    branch_groups: dict[tuple[str, str, str, str], dict[str, dict[str, Any]]] = {}
    seen_branch_keys: set[tuple[str, str, str, str, str, str]] = set()
    branch_identity_records: list[dict[str, Any]] = []
    for record in branch_records:
        role = str(record.get("role", ""))
        branch_name = str(record.get("branch_name", ""))
        if role not in _ROLES or not branch_name:
            continue
        if record.get("status") != "passed" or record.get("production_returncode") != 0:
            raise ValueError(f"V4 response source branch did not pass Production: {branch_name}")
        identity = branch_identity(record, case_id=resolved_case_id)
        identity_key = branch_identity_key(identity)
        if identity_key in seen_branch_keys:
            raise ValueError(f"duplicate V4 Production branch identity: {identity_key}")
        seen_branch_keys.add(identity_key)
        branch_root = root / "production" / identity["branch_name"]
        branch_manifest_path = branch_root / "branch_manifest.json"
        if not branch_manifest_path.is_file():
            raise FileNotFoundError(f"V4 branch manifest is missing: {branch_manifest_path}")
        branch_manifest = json.loads(branch_manifest_path.read_text(encoding="utf-8"))
        if not isinstance(branch_manifest, Mapping):
            raise ValueError(f"V4 branch manifest must be an object: {branch_manifest_path}")
        validate_branch_manifest_identity(
            branch_manifest, identity=identity, branch_root=branch_root, case_root=root,
        )
        record_xml_identity = record.get("runtime_xml_identity")
        if record_xml_identity != branch_manifest.get("runtime_xml_identity"):
            raise ValueError(f"V4 runtime XML identity differs from branch record: {branch_name}")
        group_key = branch_group_key(identity)
        group = branch_groups.setdefault(group_key, {})
        if role in group:
            raise ValueError(f"duplicate V4 branch role for mode-qualified identity: {group_key}/{role}")
        branch_entry = {
            "identity": identity,
            "record": record,
            "branch_manifest": branch_manifest_path,
        }
        group[role] = branch_entry
        branch_identity_records.append({
            **identity,
            "runtime_xml_identity": dict(branch_manifest["runtime_xml_identity"]),
            "response_included": target_count > 0,
            "branch_manifest": _file_record(branch_manifest_path, root),
            "production_xml": _file_record(branch_root / "production.xml", root),
            "correction_source": record.get("correction_source"),
            "applied_delay_ns": record.get("applied_delay_ns"),
            "period_corrections": record.get("period_corrections", []),
            "truth_used_in_estimator": record.get("truth_used_in_estimator"),
            "continuous_track_manager_run": record.get("continuous_track_manager_run"),
        })
    if not branch_groups:
        raise ValueError("V4 response extraction requires at least one Production branch")
    for group_key, group in branch_groups.items():
        required_roles = {"ON", "TO"} if target_count > 0 else {"ON"}
        if group_key[3] in {"Mode-A", "Mode-B"}:
            required_roles = {"ON", "OFF", "TO"}
        if not required_roles <= set(group):
            raise ValueError(
                f"V4 Production mode group lacks required roles {sorted(required_roles)}: {group_key}"
            )

    taps: dict[tuple[str, str, str, str, str], dict[int, tuple[Path, dict[str, str]]]] = {}
    evidence_branch_keys: list[tuple[str, str, str, str, str, str]] = []
    if target_count > 0:
        for group_key, role_entries in branch_groups.items():
            for role, entry in role_entries.items():
                identity = entry["identity"]
                if role not in {"ON", "OFF", "TO"}:
                    continue
                record = entry["record"]
                role_taps = r3._tap_rows(record)
                if set(role_taps) != set(range(period_count)):
                    raise ValueError(f"CSI tap period set is incomplete for {group_key}/{role}")
                taps[branch_identity_key(identity)] = role_taps
                evidence_branch_keys.append(branch_identity_key(identity))

    canonical_group = sorted(branch_groups)[0] if target_count > 0 else None
    canonical_identity = (
        branch_groups[canonical_group]["ON"]["identity"] if canonical_group is not None else None
    )
    roi_manifest_rows: list[dict[str, Any]] = []
    response_rows_path = output / "target_complex_response.csv"
    roi_fields = (
        "roi_id", "period_id", "target_id", "truth_visible", "truth_range_m",
        "truth_af_total_hz", "center_doppler_row", "center_range_col",
        "row_start", "row_stop_exclusive", "col_start", "col_stop_exclusive",
        "overlap_cell_count", "overlap_target_ids",
    )
    response_fields = (
        "branch_id", "case_id", "condition", "method_id", "mode", "role",
        "calibration_state_id", "tap_identity_id", "processing_tap", "roi_id",
        "period_id", "target_id", "truth_visible", "truth_range_m",
        "truth_af_total_hz", "center_doppler_row", "center_range_col",
        "doppler_row", "range_col", "doppler_offset", "range_offset",
        "doppler_hz", "range_m", "roi_overlap_count", "csi_after_real",
        "csi_after_imag", "csi_after_power", "before_csi_power",
    )
    closure_rows: list[dict[str, Any]] = []
    period_processing_states: list[dict[str, Any]] = []
    response_count = 0
    if target_count > 0:
        output.mkdir(parents=True)
        with response_rows_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=response_fields, extrasaction="ignore")
            writer.writeheader()
            for period_id in range(period_count):
                canonical_entry = branch_groups[canonical_group]["ON"]
                canonical_tap = r3._tap_arrays(
                    taps[branch_identity_key(canonical_entry["identity"])][period_id]
                )
                canonical_map = canonical_tap["csi_after"]
                fa_axis, range_axis = canonical_tap["fa_axis"], canonical_tap["range_axis"]
                if canonical_map.ndim != 2:
                    raise ValueError("CSI output map must be two-dimensional")
                rois: dict[str, dict[str, Any]] = {}
                overlap: dict[tuple[int, int], list[str]] = {}
                for truth in truths[period_id]:
                    target_id = str(truth.get("target_id", ""))
                    center_row, center_col = _target_center(fa_axis, range_axis, truth)
                    r0, r1, c0, c1 = _roi_bounds(canonical_map.shape, center_row, center_col)
                    cells = [(row, col) for row in range(r0, r1) for col in range(c0, c1)]
                    rois[target_id] = {"truth": truth, "center": (center_row, center_col),
                                       "bounds": (r0, r1, c0, c1), "cells": cells}
                    for cell in cells:
                        overlap.setdefault(cell, []).append(target_id)
                for target_id, spec in rois.items():
                    center_row, center_col = spec["center"]
                    r0, r1, c0, c1 = spec["bounds"]
                    shared = [cell for cell in spec["cells"] if len(overlap[cell]) > 1]
                    roi_manifest_rows.append({
                        "roi_id": f"period_{period_id:04d}:{target_id}", "period_id": period_id,
                        "target_id": target_id, "truth_visible": spec["truth"].get("visible", "1"),
                        "truth_range_m": spec["truth"].get("range_m"),
                        "truth_af_total_hz": spec["truth"].get("af_total_truth_hz"),
                        "center_doppler_row": center_row, "center_range_col": center_col,
                        "row_start": r0, "row_stop_exclusive": r1,
                        "col_start": c0, "col_stop_exclusive": c1,
                        "overlap_cell_count": len(shared),
                        "overlap_target_ids": json.dumps(sorted({
                            other for cell in shared for other in overlap[cell]
                        } - {target_id})),
                    })
                for group_key, role_entries in sorted(branch_groups.items()):
                    role_values: dict[str, dict[str, np.ndarray]] = {}
                    for role in ("ON", "OFF", "TO"):
                        if role not in role_entries:
                            continue
                        entry = role_entries[role]
                        identity = entry["identity"]
                        tap_manifest, tap_row = taps[branch_identity_key(identity)][period_id]
                        arrays = r3._tap_arrays((tap_manifest, tap_row))
                        value, before = arrays["csi_after"], arrays["before_power"]
                        if value.shape != before.shape or value.shape != canonical_map.shape:
                            raise ValueError(f"CSI tap shape differs for {group_key}/{role}/{period_id}")
                        if (not np.allclose(arrays["fa_axis"], fa_axis, rtol=0.0, atol=1e-9)
                                or not np.allclose(arrays["range_axis"], range_axis, rtol=0.0, atol=1e-6)):
                            raise ValueError(f"CSI axes differ from shared V4 ROI for {group_key}/{role}/{period_id}")
                        record = entry["record"]
                        branch_name = str(identity["branch_name"])
                        calibration_state_id = f"{branch_name}:period_{period_id:04d}"
                        tap_identity_id = (
                            f"{identity['case_id']}:{identity['condition']}:{identity['method_id']}"
                            f":{identity['mode']}:{role}:period_{period_id:04d}"
                        )
                        for target_id, spec in rois.items():
                            r0, r1, c0, c1 = spec["bounds"]
                            role_values.setdefault(target_id, {})[role] = value[r0:r1, c0:c1].copy()
                            truth, center = spec["truth"], spec["center"]
                            for row, col in spec["cells"]:
                                cell = value[row, col]
                                writer.writerow({
                                    "branch_id": branch_name, "case_id": identity["case_id"],
                                    "condition": identity["condition"], "method_id": identity["method_id"],
                                    "mode": identity["mode"], "role": role,
                                    "calibration_state_id": calibration_state_id,
                                    "tap_identity_id": tap_identity_id, "processing_tap": "CSI_AFTER",
                                    "roi_id": f"period_{period_id:04d}:{target_id}",
                                    "period_id": period_id, "target_id": target_id,
                                    "truth_visible": truth.get("visible", "1"),
                                    "truth_range_m": truth.get("range_m"),
                                    "truth_af_total_hz": truth.get("af_total_truth_hz"),
                                    "center_doppler_row": center[0], "center_range_col": center[1],
                                    "doppler_row": row, "range_col": col,
                                    "doppler_offset": row - center[0], "range_offset": col - center[1],
                                    "doppler_hz": float(fa_axis[row]), "range_m": float(range_axis[col]),
                                    "roi_overlap_count": len(overlap[(row, col)]),
                                    "csi_after_real": float(cell.real), "csi_after_imag": float(cell.imag),
                                    "csi_after_power": float(abs(cell) ** 2),
                                    "before_csi_power": float(before[row, col]),
                                })
                                response_count += 1
                        arrays_manifest = {
                            field: _file_record(r3._array_path(tap_manifest, tap_row[field]), root)
                            for field in ("csi_after_real_path", "csi_after_imag_path", "before_power_path",
                                          "fa_axis_path", "range_axis_path")
                        }
                        state = _branch_state(root, record, identity, period_id, tap_row)
                        state.update({
                            "calibration_state_id": calibration_state_id,
                            "tap_identity_id": tap_identity_id,
                            "tap_manifest": _file_record(tap_manifest, root),
                            "tap_arrays": arrays_manifest,
                        })
                        period_processing_states.append({**dict(identity), "period_id": period_id, **state})
                    if "OFF" in role_entries:
                        for target_id, spec in rois.items():
                            values = role_values.get(target_id, {})
                            on, off, to = values.get("ON"), values.get("OFF"), values.get("TO")
                            if on is None or off is None or to is None:
                                continue
                            denom = float(np.linalg.norm(on.reshape(-1)))
                            residual = float(np.linalg.norm((on - off - to).reshape(-1)))
                            closure_rows.append({
                                "case_id": group_key[0], "condition": group_key[1],
                                "method_id": group_key[2], "mode": group_key[3],
                                "period_id": period_id, "target_id": target_id,
                                "roi_id": f"period_{period_id:04d}:{target_id}",
                                "roi_cell_count": int(on.size),
                                "overlap_cell_count": sum(len(overlap[cell]) > 1 for cell in spec["cells"]),
                                "on_minus_off_minus_to_relative_l2": residual / denom if denom > 0 else None,
                                "interpretation": "diagnostic_only_independently_adaptive_CSI_roles_are_not_assumed_to_close",
                            })
    else:
        output.mkdir(parents=True)
        with response_rows_path.open("w", encoding="utf-8", newline="") as stream:
            csv.DictWriter(stream, fieldnames=response_fields).writeheader()

    roi_manifest_path = output / "target_roi_manifest.csv"
    _write_csv(roi_manifest_path, roi_fields, roi_manifest_rows)
    closure_path = output / "adaptive_role_difference_diagnostic.csv"
    closure_fields = (
        "case_id", "condition", "method_id", "mode", "period_id", "target_id",
        "roi_id", "roi_cell_count", "overlap_cell_count",
        "on_minus_off_minus_to_relative_l2", "interpretation",
    )
    _write_csv(closure_path, closure_fields, closure_rows)
    truth_audit_path = output / "truth_mapping_audit.json"
    truth_audit_path.write_text(
        json.dumps(truth_audit, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    evidence_keys = set(evidence_branch_keys)
    production_branch_records = []
    for item in branch_identity_records:
        record = dict(item)
        record["response_included"] = branch_identity_key(record) in evidence_keys
        production_branch_records.append(record)

    manifest = {
        "schema_version": 2, "case_id": resolved_case_id,
        "status": "NOT_APPLICABLE_NO_TARGETS" if target_count == 0 else "EVALUABLE",
        "status_labels": {
            "IMPLEMENTED": True, "EXECUTED": True, "EVALUABLE": True,
            "PERFORMANCE_VERIFIED": False,
        },
        "target_count_design": target_count, "period_count": period_count,
        "target_truth_expected": target_count > 0,
        "truth_source": _file_record(truth_path, root) if truth_path is not None else None,
        "truth_mapping_audit": truth_audit,
        "roi": {
            "definition": "shared truth-centered CSI_AFTER ROI",
            "half_doppler_bins": _ROI_HALF_DOPPLER, "half_range_bins": _ROI_HALF_RANGE,
            "coordinate_source_method": canonical_identity,
            "coordinates_shared_across_methods_and_roles": True,
            "overlap_policy": "retain per-target cells and record overlap count; overlapping cells are not independent samples",
        },
        "response_semantics": {
            "target_only_complex_response_source": "TO role CSI_AFTER per-cell complex samples; absent for target-free H0",
            "paired_role_diagnostic": "ON-OFF-TO residual is descriptive only because each role can fit an independent CSI calibration",
            "no_linear_closure_assumption": True,
            "calibration_state_id": "case/method/mode/role/branch identity plus period_id and source configuration",
            "h0_target_response": "empty and not applicable; target truth and TO are not required",
        },
        "extractor_identity": {
            "module": "scripts/phase_r/r22_v4_evidence.py",
            "sha256": _sha256(Path(__file__).resolve()),
            "source_snapshot_is_also_recorded_in": "r22_run_manifest.json/source_identity.files",
        },
        "truth_rows_by_period": {str(period): len(rows) for period, rows in truths.items()},
        "target_period_roi_count": len(roi_manifest_rows),
        "complex_response_row_count": response_count,
        "adaptive_role_diagnostic_row_count": len(closure_rows),
        "production_branches": production_branch_records,
        "response_evidence_branch_count": len(evidence_keys),
        "period_processing_states": period_processing_states,
        "files": {},
    }
    for path in (roi_manifest_path, response_rows_path, closure_path, truth_audit_path):
        manifest["files"][path.name] = _file_record(path, root)
    manifest_path = output / "target_complex_response_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return {
        "status": manifest["status"], "status_labels": manifest["status_labels"],
        "manifest_path": str(manifest_path), "response_path": str(response_rows_path),
        "roi_manifest_path": str(roi_manifest_path), "truth_mapping_audit_path": str(truth_audit_path),
        "adaptive_role_diagnostic_path": str(closure_path),
        "response_row_count": response_count, "target_period_roi_count": len(roi_manifest_rows),
        "target_only_response_source": "TO" if target_count > 0 else "not_applicable",
        "adaptive_role_closure_is_diagnostic_only": True,
    }

def validate_target_complex_response(case_root: Path) -> dict[str, Any]:
    """Fail closed before dense CSI taps are removed from a V4 case."""
    root = Path(case_root).expanduser().resolve()
    output = root / "r22_v4_output_evidence"
    manifest_path = output / "target_complex_response_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"V4 target-response manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    labels = manifest.get("status_labels")
    if (not isinstance(labels, Mapping)
            or labels.get("IMPLEMENTED") is not True
            or labels.get("EXECUTED") is not True
            or labels.get("EVALUABLE") is not True
            or labels.get("PERFORMANCE_VERIFIED") is not False):
        raise ValueError("V4 response evidence has invalid status labels")
    extractor = manifest.get("extractor_identity")
    if (not isinstance(extractor, Mapping)
            or extractor.get("module") != "scripts/phase_r/r22_v4_evidence.py"
            or extractor.get("sha256") != _sha256(Path(__file__).resolve())):
        raise ValueError("V4 response extractor identity does not match the running source")
    file_records = manifest.get("files")
    required_names = {
        "target_complex_response.csv",
        "target_roi_manifest.csv",
        "adaptive_role_difference_diagnostic.csv",
        "truth_mapping_audit.json",
    }
    if not isinstance(file_records, Mapping) or not required_names <= set(file_records):
        raise ValueError("V4 response evidence file inventory is incomplete")
    for name in sorted(required_names):
        record = file_records[name]
        path = output / name
        if (not isinstance(record, Mapping) or not path.is_file()
                or path.stat().st_size != record.get("bytes")
                or _sha256(path) != record.get("sha256")):
            raise ValueError(f"V4 response evidence identity mismatch: {path}")
    with (output / "target_complex_response.csv").open(
        "r", encoding="utf-8", newline="",
    ) as stream:
        response_rows = sum(1 for _ in csv.DictReader(stream))
    with (output / "target_roi_manifest.csv").open(
        "r", encoding="utf-8", newline="",
    ) as stream:
        roi_rows = sum(1 for _ in csv.DictReader(stream))
    if response_rows != int(manifest.get("complex_response_row_count", -1)):
        raise ValueError("V4 complex response row count differs from its manifest")
    if roi_rows != int(manifest.get("target_period_roi_count", -1)):
        raise ValueError("V4 target ROI row count differs from its manifest")
    target_count = int(manifest.get("target_count_design", -1))
    period_count = int(manifest.get("period_count", 0))
    expected_status = "NOT_APPLICABLE_NO_TARGETS" if target_count == 0 else "EVALUABLE"
    if (target_count < 0 or period_count <= 0 or manifest.get("status") != expected_status
            or (target_count == 0 and (response_rows != 0 or roi_rows != 0))
            or (target_count > 0 and roi_rows != target_count * period_count)):
        raise ValueError("V4 target-response scope does not match its declared target/period count")
    truth_source = manifest.get("truth_source")
    if target_count > 0 and (not isinstance(truth_source, Mapping) or truth_source.get("exists") is not True):
        raise ValueError("V4 target-bearing response has no auditable truth source")
    truth_audit = json.loads((output / "truth_mapping_audit.json").read_text(encoding="utf-8"))
    if target_count > 0:
        required_truth_roles = {"A0_ON", "A1_ON", "A0_TO", "A1_TO"}
        if (truth_audit.get("status") != "passed"
                or set(truth_audit.get("roles_compared", [])) != required_truth_roles):
            raise ValueError("V4 target truth mapping audit is incomplete")
        case_seed = truth_audit.get("case_seed")
        seeds_by_role = truth_audit.get("seeds_by_role")
        if (isinstance(case_seed, bool) or not isinstance(case_seed, int)
                or not isinstance(seeds_by_role, Mapping)
                or set(seeds_by_role) != required_truth_roles
                or any(value != case_seed for value in seeds_by_role.values())):
            raise ValueError("V4 target truth seed identity is incomplete or inconsistent")
        for source_key in ("sources", "scenario_sources"):
            sources = truth_audit.get(source_key)
            if not isinstance(sources, Mapping) or set(sources) != required_truth_roles:
                raise ValueError(f"V4 target truth {source_key} inventory is incomplete")
            for role, identity in sources.items():
                if not isinstance(identity, Mapping) or identity.get("exists") is not True:
                    raise ValueError(f"V4 target truth source identity is invalid: {source_key}/{role}")
                source_path = Path(str(identity.get("path") or ""))
                if source_path.is_absolute() or ".." in source_path.parts:
                    raise ValueError(f"V4 target truth source path is unsafe: {source_key}/{role}")
                source = (root / source_path).resolve()
                if (not source.is_file()
                        or source.stat().st_size != int(identity.get("bytes", -1))
                        or _sha256(source) != str(identity.get("sha256") or "")):
                    raise ValueError(f"V4 target truth source content identity mismatch: {source_key}/{role}")
    elif (truth_audit.get("status") != "NOT_APPLICABLE_NO_TARGETS"
          or truth_audit.get("identity_row_count") != 0):
        raise ValueError("V4 H0 truth mapping audit must be explicitly not applicable")
    branches = manifest.get("production_branches")
    states = manifest.get("period_processing_states")
    if not isinstance(branches, list) or not isinstance(states, list):
        raise ValueError("V4 branch or per-period processing state inventory is invalid")
    branch_keys = [
        branch_identity_key(branch) for branch in branches if isinstance(branch, Mapping)
    ]
    if len(branch_keys) != len(branches) or len(branch_keys) != len(set(branch_keys)):
        raise ValueError("V4 Production branch identities are incomplete or duplicated")
    response_branches = [
        branch for branch in branches
        if isinstance(branch, Mapping) and branch.get("response_included") is True
    ]
    expected_keys = {
        (*branch_identity_key(branch), period)
        for branch in response_branches for period in range(period_count)
    }
    state_keys = [
        (*branch_identity_key(state), int(state.get("period_id", -1)))
        for state in states if isinstance(state, Mapping)
    ]
    if len(state_keys) != len(states) or set(state_keys) != expected_keys:
        raise ValueError("V4 per-period Processing state identities are incomplete or duplicated")
    return {
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "response_row_count": response_rows,
        "target_period_roi_count": roi_rows,
        "period_processing_state_count": len(states),
    }
