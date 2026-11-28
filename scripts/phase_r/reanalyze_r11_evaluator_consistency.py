#!/usr/bin/env python3
"""Re-evaluate R11 Production detections with the frozen R7/R8 task gate."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from scripts.gmti_eval_match import one_to_one_match  # noqa: E402
from scripts.phase_r.evaluate_r7_task_metrics import evaluate_case, evaluate_manifest  # noqa: E402
from scripts.phase_r.run_r11_local_delay_scan import localization_metrics, wrap_rad  # noqa: E402


DEFAULT_R11_ROOT = ROOT / "outputs/phase_r11_local_delay_scan_20261024"
DEFAULT_OUTPUT = ROOT / "docs/research/phase_r/evidence/r11_evaluator_consistency_20261024"
DEFAULT_R8_EVALUATION = ROOT / "docs/research/phase_r/evidence/r8_unknown_delay_multiseed_20261023/evaluation"
DEFAULT_R9_EVALUATION = ROOT / "outputs/phase_r_r9_deterministic_delay_20261023/evaluation"
DEFAULT_MATCH_CONFIG = ROOT / "configs/eval/localization_match_config.json"
PERIOD_ID = 1
BEAM_ID = 50
TRUTH_RELATIVE = Path(
    "seed303/cases/base__seed_303__v_p6d7__snr_p30__delay_p4ns/"
    "scenes/A0_ON/truth/truth_targets_by_beam.csv"
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_csv(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))


def _repo_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _filtered(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if str(row.get("period_id", "")) == str(PERIOD_ID)
        and str(row.get("beam_id", "")) == str(BEAM_ID)
    ]


def analyze_condition(
    *,
    truths: Sequence[Mapping[str, Any]],
    detections: Sequence[Mapping[str, Any]],
    lineage_rows: Sequence[Mapping[str, Any]],
    doppler_axis_rows: Sequence[Mapping[str, Any]],
    doppler_rows: int,
    match_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep frozen task association and legacy geometry matching disjoint."""

    frozen = evaluate_case(
        mode="ON",
        truths=truths,
        final_detections=detections,
        lineage_rows=lineage_rows,
        doppler_axis_rows=doppler_axis_rows,
        doppler_rows=doppler_rows,
    )
    associations = frozen["association_evidence"]
    association = associations[0] if associations else None
    selected_detection = None
    if association and association.get("matched"):
        selected_detection = next(
            (
                dict(det)
                for det in detections
                if str(det.get("period_id")) == str(association.get("period_id"))
                and str(det.get("beam_id")) == str(association.get("beam_id"))
                and str(det.get("det_id")) == str(association.get("det_id"))
            ),
            None,
        )

    # This is the exact legacy matcher call made by R11's run_match(): same
    # config, beam/range gates, and disabled velocity gate. Its output remains
    # a diagnostic field and is never used to populate frozen R7 metrics.
    legacy_matches, _, _ = one_to_one_match(
        list(detections),
        list(truths),
        dict(match_config),
        "det_id",
        use_beam=True,
        use_range=True,
        use_velocity=False,
    )
    legacy_match = legacy_matches[0] if legacy_matches else None
    legacy_detection = (
        dict(detections[int(legacy_match["item_index"])]) if legacy_match else None
    )
    legacy_truth = (
        dict(truths[int(legacy_match["truth_index"])]) if legacy_match else None
    )
    legacy_localization = (
        localization_metrics(legacy_detection, legacy_truth)
        if legacy_detection is not None and legacy_truth is not None
        else None
    )
    pair_evidence = []
    for source_pair in frozen["candidate_pair_evidence"]:
        pair = dict(source_pair)
        if pair.get("truth_row") is not None and pair.get("detection_row") is not None:
            raw_delta = int(pair["detection_row"]) - int(pair["truth_row"])
            pair["doppler_delta_signed_bins"] = (
                (raw_delta + doppler_rows // 2) % doppler_rows - doppler_rows // 2
            )
        else:
            pair["doppler_delta_signed_bins"] = None
        pair_evidence.append(pair)

    return {
        "frozen_r8": {
            "metrics": frozen["metrics"],
            "association": association,
            "matched_detection": selected_detection,
            "candidate_pair_evidence": pair_evidence,
        },
        "geometry_diagnostic": {
            "match_record": legacy_match,
            "detection": legacy_detection,
            "localization_metrics": legacy_localization,
        },
    }


def _condition_layout(r11_root: Path) -> tuple[Path, Path]:
    if (r11_root / "attempt_02/conditions").is_dir():
        return r11_root / "attempt_02/conditions", r11_root
    if (r11_root / "conditions").is_dir():
        return r11_root / "conditions", r11_root
    raise FileNotFoundError(f"no R11 conditions directory under {r11_root}")


def _truth_path(r11_root: Path, manifest: Mapping[str, Any]) -> Path:
    bundled = r11_root / "truth_targets_by_beam.csv"
    if bundled.is_file():
        return bundled
    truth_record = manifest.get("truth", {})
    candidate = truth_record.get("path") if isinstance(truth_record, dict) else None
    if not candidate:
        candidate = truth_record.get("truth_csv") if isinstance(truth_record, dict) else None
    if not candidate:
        return ROOT / "outputs/phase_r8_unknown_delay_multiseed_20261023" / TRUTH_RELATIVE
    path = Path(str(candidate))
    return path if path.is_absolute() else ROOT / path


def _branch_result_dir(branch: Mapping[str, Any], conditions_root: Path, name: str) -> Path:
    runtime = branch.get("runtime_xml", {})
    raw = runtime.get("path") if isinstance(runtime, dict) else None
    if raw:
        runtime_path = Path(str(raw))
        if not runtime_path.is_absolute():
            runtime_path = ROOT / runtime_path
        candidate = runtime_path.parent / "result"
        if candidate.is_dir():
            return candidate
    candidate = conditions_root / name / "result"
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(f"cannot locate Production result for {name}")


def _compact_input_bundle(
    *, r11_root: Path, conditions_root: Path, truth_path: Path, output_dir: Path,
    manifest_path: Path,
) -> None:
    """Copy only the small source records needed to repeat this audit."""

    bundle = output_dir / "inputs"
    bundle.mkdir(parents=True, exist_ok=True)
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    compact_manifest = {
        "schema_version": 1,
        "experiment": source_manifest.get("experiment"),
        "branches": {
            name: {"correction_ns": branch.get("correction_ns") or _delay_from_name(name)}
            for name, branch in source_manifest.get("branches", {}).items()
        },
        "r8_a0_production_reference": {
            "detection": source_manifest["r8_a0_production_reference"]["detection"],
            "csi_after": source_manifest["r8_a0_production_reference"]["csi_after"],
        },
    }
    (bundle / "experiment_manifest.json").write_text(
        json.dumps(compact_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    copy_csv(truth_path, bundle / "truth_targets_by_beam.csv")
    for name in ("scan_metrics.csv", "target_cell_csi_responses.csv"):
        source = r11_root / name
        if source.is_file():
            copy_csv(source, bundle / name)
    for condition_dir in sorted(conditions_root.glob("delay_*")):
        result_dir = condition_dir / "result"
        for relative in (
            Path("detection_results_GMTI02.csv"),
            Path("debug/cluster_detection_lineage.csv"),
        ):
            source = result_dir / relative
            if not source.is_file():
                raise FileNotFoundError(source)
            destination = bundle / "conditions" / condition_dir.name / "result" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            copy_csv(source, destination)


def _delay_from_name(name: str) -> float:
    return float(name.removeprefix("delay_").removesuffix("ns"))


def reanalyze_r11(
    *, r11_root: Path, output_dir: Path, match_config_path: Path,
    make_bundle: bool = True,
) -> dict[str, Any]:
    manifest_path = r11_root / "experiment_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    conditions_root, metric_root = _condition_layout(r11_root)
    truth_path = _truth_path(r11_root, manifest)
    original_metrics_path = metric_root / "scan_metrics.csv"
    csi_path = metric_root / "target_cell_csi_responses.csv"
    match_config = json.loads(match_config_path.read_text(encoding="utf-8"))
    truths = [
        row
        for row in read_csv(truth_path)
        if str(row.get("period_id")) == str(PERIOD_ID)
        and str(row.get("beam_id")) == str(BEAM_ID)
        and str(row.get("visible", "1")).strip().lower() not in {"0", "false", "no"}
    ]
    if len(truths) != 1:
        raise ValueError(f"expected one registered R11 truth for period={PERIOD_ID}, beam={BEAM_ID}; got {len(truths)}")
    original_rows = {
        f"delay_{float(row['correction_ns']):.6f}ns": row
        for row in read_csv(original_metrics_path)
    }
    csi_rows = read_csv(csi_path) if csi_path.is_file() else []
    reference = manifest.get("r8_a0_production_reference", {})
    a0_detection = reference.get("detection")
    csi_reference = reference.get("csi_after", {})
    if not a0_detection:
        raise ValueError("R11 manifest lacks its original R8 A0 Production detection")
    a0_localization = localization_metrics(a0_detection, truths[0])
    a0_phase = float(a0_detection["ctdr_raw_phase_rad"])
    a0_jacobian = float(a0_detection["loc_jacobian_crossrange_m_per_phase_rad"])
    a0_position_error = a0_localization["position_error_2d_m"]
    a0_angle_error = a0_localization["angle_error_deg"]
    a0_csi = complex(float(csi_reference["real"]), float(csi_reference["imag"]))

    rows: list[dict[str, Any]] = []
    pair_audit: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    condition_dirs = sorted(conditions_root.glob("delay_*"), key=lambda p: _delay_from_name(p.name))
    expected_conditions = set(manifest.get("branches", {}))
    if expected_conditions and {p.name for p in condition_dirs} != expected_conditions:
        raise ValueError("R11 condition input folders do not match manifest branches")
    if len(condition_dirs) != 6:
        raise ValueError(f"expected six R11 correction conditions, found {len(condition_dirs)}")

    for condition_dir in condition_dirs:
        condition = condition_dir.name
        branch = manifest.get("branches", {}).get(condition, {})
        result_dir = _branch_result_dir(branch, conditions_root, condition)
        detection_path = result_dir / "detection_results_GMTI02.csv"
        lineage_path = result_dir / "debug/cluster_detection_lineage.csv"
        detections = _filtered(read_csv(detection_path))
        lineage = _filtered(read_csv(lineage_path))
        axis_rows = [row for row in lineage if str(row.get("record_type", "")).lower() == "beam_summary"]
        row_counts = {int(float(row["doppler_row_count"])) for row in axis_rows if row.get("doppler_row_count")}
        if len(row_counts) != 1:
            raise ValueError(f"{condition}: expected one recorded Production Doppler row count; got {row_counts}")
        doppler_rows = row_counts.pop()
        analysis = analyze_condition(
            truths=truths,
            detections=detections,
            lineage_rows=lineage,
            doppler_axis_rows=axis_rows,
            doppler_rows=doppler_rows,
            match_config=match_config,
        )
        frozen = analysis["frozen_r8"]
        association = frozen["association"] or {}
        accepted_detection = frozen["matched_detection"]
        geometry = analysis["geometry_diagnostic"]
        geometry_detection = geometry["detection"]
        if accepted_detection is not None:
            accepted_loc = localization_metrics(accepted_detection, truths[0])
            phase = float(accepted_detection["ctdr_raw_phase_rad"])
            corrected_phase = float(accepted_detection["phase_rad"])
            jacobian = float(accepted_detection["loc_jacobian_crossrange_m_per_phase_rad"])
            phase_delta = wrap_rad(phase - a0_phase)
            crossrange_delta = accepted_loc["crossrange_error_m"] - a0_localization["crossrange_error_m"]
            predicted_crossrange_delta = a0_jacobian * phase_delta
            closure = crossrange_delta - predicted_crossrange_delta
            same_cell = (int(accepted_detection["row"]), int(accepted_detection["col"])) == (
                int(a0_detection["row"]), int(a0_detection["col"])
            )
        else:
            accepted_loc = None
            phase = corrected_phase = jacobian = phase_delta = None
            crossrange_delta = predicted_crossrange_delta = closure = None
            same_cell = False

        delay_ns = _delay_from_name(condition)
        csi_tap = next(
            (
                row
                for row in csi_rows
                if float(row.get("delay_ns", "nan")) == delay_ns and row.get("tap") == "csi_after"
            ),
            None,
        )
        csi_value = None
        csi_ratio = None
        if csi_tap is not None:
            csi_value = complex(float(csi_tap["real"]), float(csi_tap["imag"]))
            if a0_csi != 0:
                ratio = csi_value / a0_csi
                csi_ratio = {"magnitude": abs(ratio), "phase_rad": math.atan2(ratio.imag, ratio.real)}

        legacy_report = original_rows.get(condition, {})
        frozen_cell = (
            f"{accepted_detection['row']},{accepted_detection['col']}"
            if accepted_detection else None
        )
        geometry_cell = (
            f"{geometry_detection['row']},{geometry_detection['col']}"
            if geometry_detection else None
        )
        old_report_cell = legacy_report.get("target_match_cell") or None
        row = {
            "condition": condition,
            "correction_ns": delay_ns,
            "residual_delay_ns": 4.0 - delay_ns,
            "truth_opportunities": frozen["metrics"].get("truth_opportunities"),
            "frozen_r8_association_status": frozen["metrics"].get("association_status"),
            "final_detections": frozen["metrics"].get("final_detections"),
            "unassociated_final_detections": frozen["metrics"].get("false_associations"),
            "frozen_r8_matched": bool(association.get("matched")),
            "frozen_r8_detection_id": association.get("det_id"),
            "frozen_r8_cell": frozen_cell,
            "range_delta_bins": association.get("range_delta_bins"),
            "truth_doppler_row": association.get("truth_row"),
            "detection_doppler_row": association.get("detection_row"),
            "doppler_delta_bins": association.get("doppler_delta_bins"),
            "frozen_r8_angle_error_deg": association.get("angle_error_deg"),
            "frozen_r8_position_error_m": association.get("position_error_m"),
            "frozen_r8_detection_rate": frozen["metrics"].get("detection_rate"),
            "geometry_diagnostic_cell": geometry_cell,
            "geometry_diagnostic_detection_id": geometry_detection.get("det_id") if geometry_detection else None,
            "geometry_diagnostic_position_error_m": (
                geometry.get("match_record", {}).get("position_error_m") if geometry.get("match_record") else None
            ),
            "original_r11_report_cell": old_report_cell,
            "geometry_match_reproduces_original_report": geometry_cell == old_report_cell,
            "frozen_vs_original_report_cell_differs": frozen_cell != old_report_cell,
            "same_registered_cell_as_r8_a0": same_cell,
            "ctdr_raw_phase_rad": phase,
            "ctdr_corrected_phase_rad": corrected_phase,
            "ctdr_raw_phase_delta_to_r8_a0_rad": phase_delta,
            "production_jacobian_crossrange_m_per_phase_rad": jacobian,
            "r8_a0_jacobian_crossrange_m_per_phase_rad": a0_jacobian,
            "same_cell_crossrange_error_delta_from_r8_a0_m": crossrange_delta,
            "r8_a0_jacobian_predicted_crossrange_delta_m": predicted_crossrange_delta,
            "same_cell_jacobian_closure_residual_m": closure,
            "same_cell_angle_error_deg": accepted_loc["angle_error_deg"] if accepted_loc else None,
            "same_cell_position_error_2d_m": accepted_loc["position_error_2d_m"] if accepted_loc else None,
            "r8_a0_position_error_2d_m": a0_position_error,
            "r8_a0_angle_error_deg": a0_angle_error,
            "csi_after_real": csi_value.real if csi_value is not None else None,
            "csi_after_imag": csi_value.imag if csi_value is not None else None,
            "csi_after_ratio_to_r8_a0": csi_ratio,
            "selected_lineage_cluster": next(
                (
                    dict(item)
                    for item in lineage
                    if item.get("record_type") == "cluster"
                    and str(item.get("cluster_row")) == str(accepted_detection.get("row"))
                    and str(item.get("cluster_col")) == str(accepted_detection.get("col"))
                ),
                None,
            ) if accepted_detection else None,
        }
        rows.append(row)
        for pair in frozen["candidate_pair_evidence"]:
            pair_audit.append({"condition": condition, **pair})
        for label, path in (
            ("detection_csv", detection_path),
            ("lineage_csv", lineage_path),
        ):
            provenance.append({"condition": condition, "role": label, "path": _repo_path(path), "bytes": path.stat().st_size, "sha256": sha256(path)})

    provenance_paths = [
        ("r11_manifest", manifest_path),
        ("truth_csv", truth_path),
        ("original_scan_metrics", original_metrics_path),
        ("target_cell_csi_responses", csi_path),
        ("match_config", match_config_path),
        ("frozen_r7_r8_evaluator", ROOT / "scripts/phase_r/evaluate_r7_task_metrics.py"),
        ("legacy_geometry_matcher", ROOT / "scripts/gmti_eval_match.py"),
        ("r11_original_runner", ROOT / "scripts/phase_r/run_r11_local_delay_scan.py"),
        ("reanalysis_script", Path(__file__).resolve()),
    ]
    for role, path in provenance_paths:
        if path.is_file():
            provenance.append({"role": role, "path": _repo_path(path), "bytes": path.stat().st_size, "sha256": sha256(path)})

    differences: list[dict[str, Any]] = []
    for row in rows:
        candidate = next(
            (
                pair
                for pair in pair_audit
                if pair["condition"] == row["condition"]
                and pair.get("detection_row") is not None
                and int(pair.get("detection_row", -1)) == 129
                and pair.get("range_delta_bins") is not None
                and int(pair.get("range_delta_bins", -1)) == 1
            ),
            {},
        )
        differences.append({
            "condition": row["condition"],
            "original_r11_report_cell": row["original_r11_report_cell"],
            "reproduced_geometry_diagnostic_cell": row["geometry_diagnostic_cell"],
            "frozen_r8_task_cell": row["frozen_r8_cell"],
            "original_geometry_assignment_reproduced": row["geometry_match_reproduces_original_report"],
            "legacy_geometry_vs_frozen_task_cell_differs": row["geometry_diagnostic_cell"] != row["frozen_r8_cell"],
            "row129_range_delta_bins": candidate.get("range_delta_bins"),
            "row129_doppler_delta_signed_bins": candidate.get("doppler_delta_signed_bins"),
            "row129_doppler_delta_abs_bins": candidate.get("doppler_delta_bins"),
            "row129_frozen_gate_result": candidate.get("gate_result"),
            "legacy_geometry_position_error_m": row["geometry_diagnostic_position_error_m"],
            "frozen_r8_position_error_m": row["frozen_r8_position_error_m"],
        })

    result = {
        "schema_version": 1,
        "task": "R11 evaluator consistency audit / frozen R7-R8 reanalysis",
        "baseline_commit": "b5b8f954abab426b6810d1cbefeb8faca4e11960",
        "scope": {"seed": 303, "period_id": PERIOD_ID, "beam_id": BEAM_ID, "production_conditions": len(rows)},
        "frozen_match_rule": {"source": "scripts/phase_r/evaluate_r7_task_metrics.py", "range_gate_bins": 2, "doppler_gate_bins": 2, "circular_doppler": True, "same_period": True, "same_beam": True, "lineage_required": True},
        "legacy_geometry_rule": {"source": "scripts/gmti_eval_match.py", "config": _repo_path(match_config_path), "use_beam": True, "use_range": True, "use_velocity": False, "diagnostic_only": True},
        "r8_a0_reference": {"cell": f"{a0_detection['row']},{a0_detection['col']}", "raw_phase_rad": a0_phase, "corrected_phase_rad": float(a0_detection["phase_rad"]), "jacobian_crossrange_m_per_phase_rad": a0_jacobian, "angle_error_deg": a0_angle_error, "position_error_2d_m": a0_position_error, "csi_after": {"real": a0_csi.real, "imag": a0_csi.imag}},
        "conditions": rows,
        "evaluator_difference_records": differences,
        "candidate_pair_audit": pair_audit,
        "input_provenance": provenance,
        "interpretation": {
            "all_six_frozen_r8_associations_use_same_registered_target_cell": all(row["frozen_r8_cell"] == "48,2154" for row in rows),
            "all_six_old_geometry_matches_reproduced": all(row["geometry_match_reproduces_original_report"] for row in rows),
            "geometry_candidate_switch_is_evaluator_assignment_only": all(row["frozen_r8_cell"] == "48,2154" for row in rows) and any(row["geometry_diagnostic_cell"] != "48,2154" for row in rows),
            "six_conditions_are_one_paired_seed_period_opportunity_not_six_independent_pd_trials": True,
        },
    }
    if make_bundle:
        _compact_input_bundle(
            r11_root=r11_root,
            conditions_root=conditions_root,
            truth_path=truth_path,
            output_dir=output_dir,
            manifest_path=manifest_path,
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "r11_reanalysis.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    write_csv(output_dir / "r11_reanalysis.csv", rows)
    write_csv(output_dir / "r11_candidate_pair_audit.csv", pair_audit)
    write_csv(output_dir / "r11_evaluator_difference_records.csv", differences)
    return result


def _diff_paths(expected: Any, actual: Any, prefix: str = "$") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        paths: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected or key not in actual:
                paths.append(f"{prefix}.{key}")
            else:
                paths.extend(_diff_paths(expected[key], actual[key], f"{prefix}.{key}"))
        return paths
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"{prefix}.length"]
        paths = []
        for index, (left, right) in enumerate(zip(expected, actual)):
            paths.extend(_diff_paths(left, right, f"{prefix}[{index}]"))
        return paths
    return [] if expected == actual else [prefix]


def audit_historical_evaluations(roots: Sequence[tuple[str, Path]]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for phase, root in roots:
        manifests = sorted(root.rglob("evaluation_manifest.json")) if root.is_dir() else []
        if not manifests:
            cases.append({"phase": phase, "status": "NOT_AVAILABLE", "root": _repo_path(root), "case_count": 0})
            continue
        for manifest_path in manifests:
            archived_path = manifest_path.parent / "task_metrics.json"
            case_name = manifest_path.parent.relative_to(root).as_posix()
            if not archived_path.is_file():
                cases.append({"phase": phase, "case": case_name, "status": "MISSING_ARCHIVED_TASK_METRICS"})
                continue
            try:
                archived = json.loads(archived_path.read_text(encoding="utf-8"))
                current = evaluate_manifest(manifest_path)
                differences = _diff_paths(archived, current)
                cases.append({
                    "phase": phase,
                    "case": case_name,
                    "status": "UNCHANGED" if not differences else "DIFFERENT",
                    "difference_paths": differences,
                    "archived_metrics": archived.get("metrics") if isinstance(archived, dict) else None,
                    "reevaluated_metrics": current.get("metrics"),
                    "manifest": _repo_path(manifest_path),
                    "archived_result": _repo_path(archived_path),
                })
            except Exception as exc:  # recorded per case; no history is rewritten
                cases.append({
                    "phase": phase,
                    "case": case_name,
                    "status": "REEVALUATION_ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                    "manifest": _repo_path(manifest_path),
                })
    phase_summaries = {}
    for phase, _ in roots:
        selected = [case for case in cases if case.get("phase") == phase and "case" in case]
        phase_summaries[phase] = {
            "cases": len(selected),
            "unchanged": sum(case["status"] == "UNCHANGED" for case in selected),
            "different": sum(case["status"] == "DIFFERENT" for case in selected),
            "errors_or_missing": sum(case["status"] not in {"UNCHANGED", "DIFFERENT"} for case in selected),
        }
    return {"status": "UNCHANGED" if all(v["different"] == 0 and v["errors_or_missing"] == 0 for v in phase_summaries.values()) else "REVIEW_REQUIRED", "phase_summaries": phase_summaries, "cases": cases}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r11-root", type=Path, default=DEFAULT_R11_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--match-config", type=Path, default=DEFAULT_MATCH_CONFIG)
    parser.add_argument("--skip-input-bundle", action="store_true")
    args = parser.parse_args()

    r11_result = reanalyze_r11(
        r11_root=args.r11_root.resolve(),
        output_dir=args.output_dir.resolve(),
        match_config_path=args.match_config.resolve(),
        make_bundle=not args.skip_input_bundle,
    )
    historical = audit_historical_evaluations(
        [("R8", DEFAULT_R8_EVALUATION), ("R9", DEFAULT_R9_EVALUATION)]
    )
    (args.output_dir / "r8_r9_historical_consistency.json").write_text(
        json.dumps(historical, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    summary = {
        "r11_conditions": len(r11_result["conditions"]),
        "frozen_matched_count": sum(row["frozen_r8_matched"] for row in r11_result["conditions"]),
        "frozen_cells": [row["frozen_r8_cell"] for row in r11_result["conditions"]],
        "geometry_cells": [row["geometry_diagnostic_cell"] for row in r11_result["conditions"]],
        "historical_status": historical["status"],
        "historical_phase_summaries": historical["phase_summaries"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if historical["status"] == "UNCHANGED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
