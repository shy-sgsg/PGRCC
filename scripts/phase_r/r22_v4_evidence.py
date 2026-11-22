#!/usr/bin/env python3
"""Output-complete target-response evidence for the R22 V4 contract."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from scripts.phase_r import evaluate_r3_minimal_cuda_causal_pilot as r3


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
    simulation: Mapping[str, Any], period_count: int, *, allow_missing: bool = False,
) -> tuple[Path, dict[int, list[dict[str, str]]]]:
    source = simulation.get("A0_ON")
    if not isinstance(source, Mapping):
        raise ValueError("R22 V4 target-response extraction requires the A0_ON truth source")
    output = Path(str(source.get("output_dir", ""))).expanduser().resolve()
    path = output / "truth" / "truth_targets_by_beam.csv"
    if not path.is_file():
        if allow_missing:
            return path, {period: [] for period in range(period_count)}
        raise FileNotFoundError(f"target truth file is missing: {path}")
    by_period: dict[int, list[dict[str, str]]] = {period: [] for period in range(period_count)}
    for row in r3._read_rows(path):
        if row.get("pulse_id") not in {None, "0"}:
            continue
        period_id = int(row["period_id"])
        if period_id not in by_period:
            continue
        by_period[period_id].append(row)
    for period_id, rows in by_period.items():
        target_ids = [row.get("target_id", "") for row in rows]
        if len(set(target_ids)) != len(target_ids):
            raise ValueError(f"duplicate target truth rows in period {period_id}")
    return path, by_period


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
        "branch_name": record.get("branch_name"),
        "method_id": record.get("condition") or record.get("method_id"),
        "mode": record.get("mode"),
        "role": record.get("role"),
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
) -> dict[str, Any]:
    """Save one truth-aligned, fixed CSI ROI for every target/period/branch role.

    TO is the target-only response source. ON/OFF/TO role differences are
    stored as diagnostics because each Production branch can fit its own CSI
    calibration and their outputs are not assumed to add linearly.
    """
    root = Path(case_root).expanduser().resolve()
    output = root / "r22_v4_output_evidence"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite V4 response evidence: {output}")
    if target_count < 0 or period_count <= 0:
        raise ValueError("target_count and period_count are invalid")
    output.mkdir(parents=True)

    truth_path, truths = _truth_rows(
        simulation, period_count, allow_missing=target_count == 0,
    )
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

    branches: dict[str, dict[str, Mapping[str, Any]]] = {}
    for record in branch_records:
        role = str(record.get("role", ""))
        branch_name = str(record.get("branch_name", ""))
        if role not in _ROLES or not branch_name:
            continue
        if record.get("status") != "passed" or record.get("production_returncode") != 0:
            raise ValueError(f"V4 response source branch did not pass Production: {branch_name}")
        method_id = str(record.get("condition") or record.get("method_id") or "")
        if not method_id:
            raise ValueError(f"V4 Production branch lacks method identity: {branch_name}")
        method = branches.setdefault(method_id, {})
        if role in method:
            raise ValueError(f"duplicate V4 Production role for {method_id}/{role}")
        method[role] = record
    if not branches or any(set(method) != set(_ROLES) for method in branches.values()):
        raise ValueError("V4 target-response extraction requires complete ON/OFF/TO method triplets")

    taps: dict[tuple[str, str], dict[int, tuple[Path, dict[str, str]]]] = {}
    for method_id, role_records in branches.items():
        for role, record in role_records.items():
            role_taps = r3._tap_rows(record)
            if set(role_taps) != set(range(period_count)):
                raise ValueError(f"CSI tap period set is incomplete for {method_id}/{role}")
            taps[(method_id, role)] = role_taps

    canonical_method = sorted(branches)[0]
    roi_manifest_rows: list[dict[str, Any]] = []
    response_rows_path = output / "target_complex_response.csv"
    roi_fields = (
        "roi_id", "period_id", "target_id", "truth_visible", "truth_range_m",
        "truth_af_total_hz", "center_doppler_row", "center_range_col",
        "row_start", "row_stop_exclusive", "col_start", "col_stop_exclusive",
        "overlap_cell_count", "overlap_target_ids",
    )
    response_fields = (
        "branch_id", "method_id", "role", "calibration_state_id", "tap_identity_id",
        "processing_tap", "roi_id", "period_id", "target_id", "truth_visible",
        "truth_range_m", "truth_af_total_hz", "center_doppler_row", "center_range_col",
        "doppler_row", "range_col", "doppler_offset", "range_offset",
        "doppler_hz", "range_m", "roi_overlap_count", "csi_after_real",
        "csi_after_imag", "csi_after_power", "before_csi_power",
    )
    closure_rows: list[dict[str, Any]] = []
    period_processing_states: list[dict[str, Any]] = []
    with response_rows_path.open("w", encoding="utf-8", newline="") as response_stream:
        response_writer = csv.DictWriter(response_stream, fieldnames=response_fields, extrasaction="ignore")
        response_writer.writeheader()
        response_count = 0
        for period_id in range(period_count):
            canonical_tap = r3._tap_arrays(taps[(canonical_method, "ON")][period_id])
            canonical_map = canonical_tap["csi_after"]
            fa_axis = canonical_tap["fa_axis"]
            range_axis = canonical_tap["range_axis"]
            if canonical_map.ndim != 2:
                raise ValueError("CSI output map must be two-dimensional")
            rois: dict[str, dict[str, Any]] = {}
            overlap: dict[tuple[int, int], list[str]] = {}
            for truth in truths[period_id]:
                target_id = str(truth.get("target_id", ""))
                center_row, center_col = _target_center(fa_axis, range_axis, truth)
                r0, r1, c0, c1 = _roi_bounds(canonical_map.shape, center_row, center_col)
                cells = [(row, col) for row in range(r0, r1) for col in range(c0, c1)]
                rois[target_id] = {
                    "truth": truth, "center": (center_row, center_col),
                    "bounds": (r0, r1, c0, c1), "cells": cells,
                }
                for cell in cells:
                    overlap.setdefault(cell, []).append(target_id)
            for target_id, spec in rois.items():
                center_row, center_col = spec["center"]
                r0, r1, c0, c1 = spec["bounds"]
                shared_cells = [cell for cell in spec["cells"] if len(overlap[cell]) > 1]
                roi_manifest_rows.append({
                    "roi_id": f"period_{period_id:04d}:{target_id}",
                    "period_id": period_id,
                    "target_id": target_id,
                    "truth_visible": spec["truth"].get("visible", "1"),
                    "truth_range_m": spec["truth"].get("range_m"),
                    "truth_af_total_hz": spec["truth"].get("af_total_truth_hz"),
                    "center_doppler_row": center_row,
                    "center_range_col": center_col,
                    "row_start": r0,
                    "row_stop_exclusive": r1,
                    "col_start": c0,
                    "col_stop_exclusive": c1,
                    "overlap_cell_count": len(shared_cells),
                    "overlap_target_ids": json.dumps(sorted({
                        other for cell in shared_cells for other in overlap[cell]
                    } - {target_id})),
                })

            roi_values: dict[tuple[str, str], dict[str, np.ndarray]] = {}
            for method_id, role_records in branches.items():
                for role in _ROLES:
                    manifest_path, tap_row = taps[(method_id, role)][period_id]
                    arrays = r3._tap_arrays((manifest_path, tap_row))
                    value = arrays["csi_after"]
                    before = arrays["before_power"]
                    if value.shape != before.shape or value.shape != canonical_map.shape:
                        raise ValueError(f"CSI tap shape differs for {method_id}/{role}/{period_id}")
                    if (not np.allclose(arrays["fa_axis"], fa_axis, rtol=0.0, atol=1.0e-9)
                            or not np.allclose(arrays["range_axis"], range_axis, rtol=0.0, atol=1.0e-6)):
                        raise ValueError(f"CSI axes differ from the shared V4 ROI for {method_id}/{role}/{period_id}")
                    branch_record = role_records[role]
                    branch_id = str(branch_record.get("branch_name"))
                    calibration_state_id = f"{branch_id}:period_{period_id:04d}"
                    tap_identity_id = f"{method_id}:{role}:period_{period_id:04d}"
                    for target_id, spec in rois.items():
                        r0, r1, c0, c1 = spec["bounds"]
                        roi_values.setdefault((method_id, target_id), {})[role] = value[r0:r1, c0:c1].copy()
                        truth = spec["truth"]
                        center_row, center_col = spec["center"]
                        for doppler_row, range_col in spec["cells"]:
                            cell_value = value[doppler_row, range_col]
                            truth_visible = truth.get("visible", "1")
                            response_writer.writerow({
                                "branch_id": branch_id,
                                "method_id": method_id,
                                "role": role,
                                "calibration_state_id": calibration_state_id,
                                "tap_identity_id": tap_identity_id,
                                "processing_tap": "CSI_AFTER",
                                "roi_id": f"period_{period_id:04d}:{target_id}",
                                "period_id": period_id,
                                "target_id": target_id,
                                "truth_visible": truth_visible,
                                "truth_range_m": truth.get("range_m"),
                                "truth_af_total_hz": truth.get("af_total_truth_hz"),
                                "center_doppler_row": center_row,
                                "center_range_col": center_col,
                                "doppler_row": doppler_row,
                                "range_col": range_col,
                                "doppler_offset": doppler_row - center_row,
                                "range_offset": range_col - center_col,
                                "doppler_hz": float(fa_axis[doppler_row]),
                                "range_m": float(range_axis[range_col]),
                                "roi_overlap_count": len(overlap[(doppler_row, range_col)]),
                                "csi_after_real": float(cell_value.real),
                                "csi_after_imag": float(cell_value.imag),
                                "csi_after_power": float(abs(cell_value) ** 2),
                                "before_csi_power": float(before[doppler_row, range_col]),
                            })
                            response_count += 1
                    tap_array_records = {}
                    for field in ("csi_after_real_path", "csi_after_imag_path", "before_power_path",
                                  "fa_axis_path", "range_axis_path"):
                        source_path = r3._array_path(manifest_path, tap_row[field])
                        tap_array_records[field] = _file_record(source_path, root)
                    state = _branch_state(root, branch_record, period_id, tap_row)
                    state.update({
                        "calibration_state_id": calibration_state_id,
                        "tap_identity_id": tap_identity_id,
                        "tap_manifest": _file_record(manifest_path, root),
                        "tap_arrays": tap_array_records,
                    })
                    period_processing_states.append({
                        "method_id": method_id,
                        "role": role,
                        "period_id": period_id,
                        **state,
                    })

                for target_id, spec in rois.items():
                    role_values = roi_values[(method_id, target_id)]
                    on = role_values.get("ON")
                    off = role_values.get("OFF")
                    to = role_values.get("TO")
                    if on is None or off is None or to is None:
                        raise ValueError(f"incomplete role response for {method_id}/{target_id}")
                    denominator = float(np.linalg.norm(on.reshape(-1)))
                    residual = float(np.linalg.norm((on - off - to).reshape(-1)))
                    closure_rows.append({
                        "method_id": method_id,
                        "period_id": period_id,
                        "target_id": target_id,
                        "roi_id": f"period_{period_id:04d}:{target_id}",
                        "roi_cell_count": int(on.size),
                        "overlap_cell_count": len([
                            cell for cell in spec["cells"] if len(overlap[cell]) > 1
                        ]),
                        "on_minus_off_minus_to_relative_l2": residual / denominator if denominator > 0 else None,
                        "interpretation": "diagnostic_only_independently_adaptive_CSI_roles_are_not_assumed_to_close",
                    })

    roi_manifest_path = output / "target_roi_manifest.csv"
    _write_csv(roi_manifest_path, roi_fields, roi_manifest_rows)
    closure_path = output / "adaptive_role_difference_diagnostic.csv"
    closure_fields = (
        "method_id", "period_id", "target_id", "roi_id", "roi_cell_count",
        "overlap_cell_count", "on_minus_off_minus_to_relative_l2", "interpretation",
    )
    _write_csv(closure_path, closure_fields, closure_rows)

    branch_manifest_records: list[dict[str, Any]] = []
    for method_id in sorted(branches):
        for role in _ROLES:
            record = branches[method_id][role]
            branch_root = root / "production" / str(record.get("branch_name", ""))
            branch_manifest_records.append({
                "method_id": method_id,
                "role": role,
                "branch_name": record.get("branch_name"),
                "branch_manifest": _file_record(branch_root / "branch_manifest.json", root),
                "production_xml": _file_record(branch_root / "production.xml", root),
                "correction_source": record.get("correction_source"),
                "applied_delay_ns": record.get("applied_delay_ns"),
                "period_corrections": record.get("period_corrections", []),
                "truth_used_in_estimator": record.get("truth_used_in_estimator"),
                "continuous_track_manager_run": record.get("continuous_track_manager_run"),
            })

    target_only_period_count = len({row["period_id"] for row in roi_manifest_rows})
    manifest = {
        "schema_version": 1,
        "status": "NOT_APPLICABLE_NO_TARGETS" if target_count == 0 else "EVALUABLE",
        "status_labels": {
            "IMPLEMENTED": True,
            "EXECUTED": True,
            "EVALUABLE": target_count == 0 or bool(roi_manifest_rows),
            "PERFORMANCE_VERIFIED": False,
        },
        "target_count_design": target_count,
        "period_count": period_count,
        "target_truth_expected": target_count > 0,
        "truth_source": _file_record(truth_path, root),
        "roi": {
            "definition": "shared truth-centered CSI_AFTER ROI",
            "half_doppler_bins": _ROI_HALF_DOPPLER,
            "half_range_bins": _ROI_HALF_RANGE,
            "coordinate_source_method": canonical_method,
            "coordinates_shared_across_methods_and_roles": True,
            "overlap_policy": "retain per-target cells and record overlap count; overlapping cells are not independent samples",
        },
        "response_semantics": {
            "target_only_complex_response_source": "TO role CSI_AFTER per-cell complex samples",
            "paired_role_diagnostic": "ON-OFF-TO residual is descriptive only because each role can fit an independent CSI calibration",
            "no_linear_closure_assumption": True,
            "calibration_state_id": "branch_name plus period_id; full state and source hashes are listed below",
        },
        "extractor_identity": {
            "module": "scripts/phase_r/r22_v4_evidence.py",
            "sha256": _sha256(Path(__file__).resolve()),
            "source_snapshot_is_also_recorded_in": "r22_run_manifest.json/source_identity.files",
        },
        "truth_rows_by_period": {
            str(period): len(rows) for period, rows in truths.items()
        },
        "target_period_roi_count": len(roi_manifest_rows),
        "complex_response_row_count": response_count,
        "adaptive_role_diagnostic_row_count": len(closure_rows),
        "production_branches": branch_manifest_records,
        "period_processing_states": period_processing_states,
        "files": {},
    }
    for path in (roi_manifest_path, response_rows_path, closure_path):
        manifest["files"][path.name] = _file_record(path, root)
    manifest_path = output / "target_complex_response_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return {
        "status": manifest["status"],
        "status_labels": manifest["status_labels"],
        "manifest_path": str(manifest_path),
        "response_path": str(response_rows_path),
        "roi_manifest_path": str(roi_manifest_path),
        "adaptive_role_diagnostic_path": str(closure_path),
        "response_row_count": response_count,
        "target_period_roi_count": len(roi_manifest_rows),
        "target_only_response_source": "TO",
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
    branches = manifest.get("production_branches")
    states = manifest.get("period_processing_states")
    if (not isinstance(branches, list) or not isinstance(states, list)
            or len(states) != len(branches) * period_count):
        raise ValueError("V4 per-period Production processing state is incomplete")
    state_keys = {
        (str(state.get("method_id")), str(state.get("role")), int(state.get("period_id", -1)))
        for state in states if isinstance(state, Mapping)
    }
    expected_keys = {
        (str(branch.get("method_id")), str(branch.get("role")), period)
        for branch in branches if isinstance(branch, Mapping)
        for period in range(period_count)
    }
    if state_keys != expected_keys:
        raise ValueError("V4 per-period Processing state identities are incomplete or duplicated")
    return {
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "response_row_count": response_rows,
        "target_period_roi_count": roi_rows,
        "period_processing_state_count": len(states),
    }
