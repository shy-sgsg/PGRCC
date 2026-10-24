#!/usr/bin/env python3
"""Evaluate paired pre/post-fix seed-101 Production runs for Phase R7."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from scripts.phase_r.evaluate_r7_task_metrics import (
    evaluate_manifest,
    summarize_target_only_response,
)
from scripts.phase_r.run_r7_minimal_production_matrix import period_input_files


ROOT = Path(__file__).resolve().parents[2]
R6_DEFAULT = ROOT / "outputs/phase_r6_stagewise_divergence_20261022"
R7_DEFAULT = ROOT / "outputs/phase_r7_cfar_correctness_20261022"
POST_DEFAULT = R7_DEFAULT / "post_fix_matrix_3period_final"
PRE_B_DEFAULT = R7_DEFAULT / "pre_fix_matrix_3period_id_only"
OUTPUT_DEFAULT = R7_DEFAULT / "task_metrics_final_v3"
MODES = ("OFF", "ON", "TO")
CONDITIONS = ("A0", "B", "C")
R6_RUN_NAMES = {
    "A0_OFF": "A0_OFF",
    "A0_ON": "A0_ON_rep1",
    "A0_TO": "A0_TO",
    "C_OFF": "C_OFF",
    "C_ON": "C_ON",
    "C_TO": "C_TO",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["status"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _stage_input(r6_root: Path, condition: str, mode: str) -> Path:
    stages = r6_root / "reconstruction/stages"
    if condition == "A0":
        source = {
            "OFF": "A0_OFF_bg_replay1",
            "ON": "A0_ON",
            "TO": "A0_TO_bg_replay1",
        }[mode]
        return stages / source / "data/stage2_statistical_newprotocol_period_0000.bin"
    if condition == "B":
        source = f"A1_{mode}_bg_replay1"
        return stages / source / "data/stage2_statistical_newprotocol_period_0000.bin"
    if condition == "C":
        return (
            r6_root / "reconstruction/corrected_C" / mode / "prepared_inputs"
            / "A2_Known_error_correction_upper_bound/period_0000.bin"
        )
    raise ValueError(f"unknown condition: {condition}")


def _truth_file(r6_root: Path, condition: str, mode: str) -> Path:
    if condition == "A0":
        source = {"ON": "A0_ON", "TO": "A0_TO_bg_replay1"}[mode]
    else:
        source = {"ON": "A1_ON_bg_replay1", "TO": "A1_TO_bg_replay1"}[mode]
    return r6_root / "reconstruction/stages" / source / "truth/truth_targets_by_beam.csv"


def _production_config(r6_root: Path, condition: str, mode: str) -> Path:
    name = ("A0_ON_rep1" if mode == "ON" else f"A0_{mode}") if condition in {"A0", "B"} else f"C_{mode}"
    return r6_root / "production" / name / "runtime.xml"


def _pre_case_dir(
    condition: str, mode: str, r6_root: Path, pre_b_root: Path,
) -> Path:
    if condition == "B":
        return pre_b_root / f"B_{mode}"
    run_name = R6_RUN_NAMES[f"{condition}_{mode}"]
    return r6_root / "production" / run_name


def _id_paths(result_dir: Path, prefix: str) -> list[Path]:
    paths = [result_dir / f"{prefix}{index:02d}.csv" for index in range(1, 4)]
    for path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"missing per-period production record: {path}")
    return paths


def _track_debug_dir(case_dir: Path, detection_paths: Sequence[Path]) -> Path:
    expected_counts = {
        result_id: len(_read_csv(path))
        for result_id, path in enumerate(detection_paths, 1)
    }
    candidates = sorted((case_dir / "track_debug").glob("local_test_*"))
    matched: list[Path] = []
    for candidate in candidates:
        frames_path = candidate / "track_frames.csv"
        if not frames_path.is_file():
            continue
        rows = _read_csv(frames_path)
        observed: dict[int, int] = {}
        for row in rows:
            digits = "".join(ch for ch in str(row.get("result_id", "")) if ch.isdigit())
            if digits:
                result_id = int(digits)
                if result_id in observed:
                    observed = {}
                    break
                observed[result_id] = int(float(row.get("num_detections", "-1")))
        audit_files_present = all(
            (candidate / name).is_file()
            for name in ("track_detections.csv", "track_output_payloads.csv")
        )
        if observed == expected_counts and audit_files_present:
            matched.append(candidate)
    if len(matched) != 1:
        raise ValueError(
            f"expected one TrackManager debug run whose per-result detection counts match "
            f"Production snapshots for {case_dir}; "
            f"found {len(matched)}: {matched}"
        )
    return matched[0]


def _legacy_doppler_axes(result_dir: Path) -> list[dict[str, Any]]:
    axes: list[dict[str, Any]] = []
    for result_id in range(1, 4):
        path = result_dir / "debug" / f"cfar_GMTI{result_id:02d}_beam050_meta.txt"
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        axes.append({
            "period_id": result_id - 1,
            "beam_id": 50,
            "doppler_axis_first_hz": float(values["doppler_axis_row0_hz"]),
            "doppler_bin_spacing_hz": float(values["doppler_axis_row_step_hz"]),
            "doppler_row_count": int(values["rows"]),
            "doppler_axis_uniform": 1,
        })
    return axes


def _array_from_manifest(manifest_path: Path, row: Mapping[str, str], key: str) -> np.ndarray:
    raw = Path(row[key])
    path = raw if raw.is_absolute() else (manifest_path.parent / raw).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"missing CSI tap array: {path}")
    return np.asarray(np.load(path), dtype=np.float64)


def _target_center(
    fa_axis: np.ndarray, range_axis: np.ndarray, truth: Mapping[str, str],
) -> tuple[int, int]:
    if fa_axis.size < 2 or range_axis.size == 0:
        raise ValueError("invalid CSI Doppler/range axes")
    step = float(np.median(np.diff(fa_axis)))
    prf = step * fa_axis.size
    center = 0.5 * (float(fa_axis[0]) + float(fa_axis[-1]))
    total_hz = float(truth["af_total_truth_hz"])
    wrapped_hz = total_hz + round((center - total_hz) / prf) * prf
    row = int(np.argmin(np.abs(fa_axis - wrapped_hz)))
    col = int(np.argmin(np.abs(range_axis - float(truth["range_m"]))))
    return row, col


def _target_roi(value: np.ndarray, row: int, col: int) -> np.ndarray:
    return value[
        max(0, row - 2):min(value.shape[0], row + 3),
        max(0, col - 2):min(value.shape[1], col + 3),
    ]


def _target_only_response(result_dir: Path, truth_path: Path) -> dict[str, Any]:
    truth_by_period = {
        int(row["period_id"]): row
        for row in _read_csv(truth_path)
        if row.get("visible", "1").lower() not in {"0", "false"}
    }
    tap_rows: dict[int, tuple[Path, dict[str, str]]] = {}
    for manifest_path in sorted(result_dir.rglob("csi_roi_manifest.csv")):
        for row in _read_csv(manifest_path):
            if not row.get("period_id"):
                continue
            period = int(row["period_id"])
            if period in tap_rows:
                raise ValueError(f"duplicate target-only CSI tap for period {period}: {result_dir}")
            tap_rows[period] = (manifest_path, row)
    if set(tap_rows) != set(truth_by_period):
        raise ValueError(
            f"target-only CSI periods {sorted(tap_rows)} do not match truth "
            f"{sorted(truth_by_period)} in {result_dir}"
        )

    before_values: list[complex] = []
    after_values: list[complex] = []
    per_period: list[dict[str, Any]] = []
    for period in sorted(tap_rows):
        manifest_path, row = tap_rows[period]
        truth = truth_by_period[period]
        fa_axis = _array_from_manifest(manifest_path, row, "fa_axis_path").reshape(-1)
        range_axis = _array_from_manifest(manifest_path, row, "range_axis_path").reshape(-1)
        before = (
            _array_from_manifest(manifest_path, row, "ctdr_channel1_real_path")
            + 1j * _array_from_manifest(manifest_path, row, "ctdr_channel1_imag_path")
        )
        after = (
            _array_from_manifest(manifest_path, row, "csi_after_real_path")
            + 1j * _array_from_manifest(manifest_path, row, "csi_after_imag_path")
        )
        if before.shape != after.shape or before.ndim != 2:
            raise ValueError(f"target-only pre/post CSI arrays differ in shape: {result_dir}")
        center_row, center_col = _target_center(fa_axis, range_axis, truth)
        before_roi = _target_roi(before, center_row, center_col)
        after_roi = _target_roi(after, center_row, center_col)
        period_summary = summarize_target_only_response(
            before_roi.reshape(-1), after_roi.reshape(-1),
        )
        period_summary.update({
            "period_id": period,
            "target_row": center_row,
            "target_col": center_col,
            "target_range_m": float(truth["range_m"]),
            "target_af_total_truth_hz": float(truth["af_total_truth_hz"]),
            "target_roi_shape": list(before_roi.shape),
        })
        per_period.append(period_summary)
        before_values.extend(before_roi.reshape(-1).tolist())
        after_values.extend(after_roi.reshape(-1).tolist())
    combined = summarize_target_only_response(before_values, after_values)
    combined["definition"] = (
        "truth-centered 5x5 complex transfer from pre-CSI aligned F1 "
        "(CTDR channel-1 tap) to production csi_after, aggregated over periods"
    )
    combined["per_period"] = per_period
    return combined


def _case_manifest(
    *, case_dir: Path, condition: str, mode: str, manifest_path: Path,
    r6_root: Path, post_fix: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result_dir = case_dir / "result"
    detection_paths = _id_paths(result_dir, "detection_results_GMTI")
    track_dir = _track_debug_dir(case_dir, detection_paths)
    cfar_path = result_dir / "debug/cfar_geometry_diagnostics.csv"
    if not cfar_path.is_file():
        raise FileNotFoundError(f"CFAR geometry diagnostics missing: {cfar_path}")
    lineage_path = result_dir / "debug/cluster_detection_lineage.csv"
    truth_path = _truth_file(r6_root, condition, mode) if mode != "OFF" else None
    evaluator_manifest: dict[str, Any] = {
        "condition": condition,
        "mode": mode,
        "truth_csv": [str(truth_path)] if truth_path is not None else [],
        "detection_csvs": [str(path) for path in detection_paths],
        "cfar_geometry_csv": [str(cfar_path)],
        "track_detections_csvs": [str(track_dir / "track_detections.csv")],
        "track_payload_csvs": [str(track_dir / "track_output_payloads.csv")],
        "track_frames_csv": [str(track_dir / "track_frames.csv")],
        "result_to_period": {f"GMTI{index:02d}": index - 1 for index in range(1, 4)},
        "doppler_rows": 130,
    }
    if lineage_path.is_file():
        evaluator_manifest["lineage_csvs"] = [str(lineage_path)]
    else:
        evaluator_manifest["doppler_axis_rows"] = _legacy_doppler_axes(result_dir)
    if mode == "TO" and truth_path is not None:
        evaluator_manifest["target_only_response_json"] = str(
            case_dir.parent / f"{condition}_{mode}_target_response.json"
        )
    provenance = {
        "case_dir": str(case_dir.resolve()),
        "result_dir": str(result_dir.resolve()),
        "track_debug_dir": str(track_dir.resolve()),
        "truth_csv": str(truth_path.resolve()) if truth_path else None,
        "detection_csvs": [str(path.resolve()) for path in detection_paths],
        "cfar_geometry_csv": str(cfar_path.resolve()),
        "lineage_csv": str(lineage_path.resolve()) if lineage_path.is_file() else None,
        "track_frames_csv": str((track_dir / "track_frames.csv").resolve()),
        "track_detections_csv": str((track_dir / "track_detections.csv").resolve()),
        "track_payload_csv": str((track_dir / "track_output_payloads.csv").resolve()),
        "manifest_source": str(manifest_path.resolve()),
        "post_fix": post_fix,
    }
    return evaluator_manifest, provenance


def _pre_input_paths(
    condition: str, mode: str, case_dir: Path, r6_root: Path,
) -> list[Path]:
    if condition == "B":
        manifest_path = case_dir.parent / "experiment_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = manifest["runs"][f"B_{mode}"]
        return [Path(item["path"]) for item in record["input_periods"]]
    return period_input_files(_stage_input(r6_root, condition, mode))


def _load_run_manifest(case_dir: Path) -> dict[str, Any]:
    path = case_dir / "result/run_manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _run_case_evaluation(
    *, case_dir: Path, case_output_dir: Path, condition: str, mode: str,
    manifest_source: Path, r6_root: Path, post_fix: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    evaluator_manifest, provenance = _case_manifest(
        case_dir=case_dir, condition=condition, mode=mode,
        manifest_path=manifest_source, r6_root=r6_root, post_fix=post_fix,
    )
    case_output_dir.mkdir(parents=True)
    if mode == "TO":
        response = _target_only_response(
            case_dir / "result", Path(str(evaluator_manifest["truth_csv"][0]))
        )
        response_path = case_output_dir / "target_only_response.json"
        _write_json(response_path, response)
        evaluator_manifest["target_only_response_json"] = str(response_path.resolve())
    case_manifest_path = case_output_dir / "evaluation_manifest.json"
    _write_json(case_manifest_path, evaluator_manifest)
    metrics = evaluate_manifest(case_manifest_path)
    metrics_path = case_output_dir / "metrics.json"
    _write_json(metrics_path, metrics)
    _write_csv(
        case_output_dir / "associations.csv",
        metrics["association_evidence"] + metrics["candidate_pair_evidence"],
    )
    _write_csv(case_output_dir / "lineage.csv", metrics["lineage_evidence"])
    provenance.update({
        "evaluation_manifest": str(case_manifest_path.resolve()),
        "metrics_json": str(metrics_path.resolve()),
        "metrics_status": metrics["metrics"].get("association_status", metrics["metrics"].get("target_only_response", {}).get("status", "evaluable")),
    })
    return metrics, provenance


def _validate_execution_manifests(
    post_manifest_path: Path, pre_b_manifest_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    post = json.loads(post_manifest_path.read_text(encoding="utf-8"))
    pre_b = json.loads(pre_b_manifest_path.read_text(encoding="utf-8"))
    for condition in CONDITIONS:
        for mode in MODES:
            case_id = f"{condition}_{mode}"
            run = post.get("runs", {}).get(case_id)
            if not isinstance(run, dict) or run.get("status") != "EXECUTED":
                raise ValueError(f"post-fix matrix case is not executed: {case_id}")
            if run.get("period_sequence_status") != "evaluable":
                raise ValueError(f"post-fix period sequence is not evaluable: {case_id}")
            if condition == "B":
                pre_run = pre_b.get("runs", {}).get(case_id)
                if not isinstance(pre_run, dict) or pre_run.get("status") != "EXECUTED":
                    raise ValueError(f"pre-fix B case is not executed: {case_id}")
                if pre_run.get("period_sequence_status") != "evaluable":
                    raise ValueError(f"pre-fix B sequence is not evaluable: {case_id}")
    return post, pre_b


def _pair_provenance(
    *, condition: str, mode: str, post_run: Mapping[str, Any], pre_case_dir: Path,
    pre_b_manifest: Mapping[str, Any], r6_root: Path,
) -> dict[str, Any]:
    post_inputs = list(post_run["input_periods"])
    pre_paths = _pre_input_paths(condition, mode, pre_case_dir, r6_root)
    pre_input_hashes = [_sha256(path) for path in pre_paths]
    post_input_hashes = [str(item["sha256"]) for item in post_inputs]
    if len(pre_paths) != 3 or len(post_inputs) != 3:
        raise ValueError(f"expected three inputs per paired case: {condition}_{mode}")
    pre_runtime = pre_case_dir / "runtime.xml"
    if not pre_runtime.is_file():
        raise FileNotFoundError(f"pre-fix runtime XML missing: {pre_runtime}")
    if condition == "B":
        pre_run = pre_b_manifest["runs"][f"B_{mode}"]
        pre_binary_sha = str(pre_run["binary_sha256"])
        pre_config_sha = str(pre_run["config_sha256"])
    else:
        pre_run = _load_run_manifest(pre_case_dir)
        pre_binary_sha = json.loads(
            (r6_root / "analysis/cfar_branch_metrics/analysis_manifest.json").read_text(
                encoding="utf-8"
            )
        )["production_executable_sha256"]
        pre_config_sha = _sha256(pre_runtime)
        if Path(str(pre_run["input_data_path"])).resolve() != pre_paths[-1].resolve():
            raise ValueError(f"legacy run manifest last input does not match {condition}_{mode}")
        if Path(str(pre_run["config_path"])).resolve() != pre_runtime.resolve():
            raise ValueError(f"legacy run manifest config mismatch: {condition}_{mode}")
    input_match = pre_input_hashes == post_input_hashes
    config_match = pre_config_sha == str(post_run["config_sha256"])
    if not input_match or not config_match:
        raise ValueError(
            f"pre/post input or runtime config pairing failed for {condition}_{mode}: "
            f"inputs={input_match}, config={config_match}"
        )
    return {
        "condition": condition,
        "mode": mode,
        "input_sha256_pre": pre_input_hashes,
        "input_sha256_post": post_input_hashes,
        "input_hashes_match": input_match,
        "runtime_xml_sha256_pre": pre_config_sha,
        "runtime_xml_sha256_post": str(post_run["config_sha256"]),
        "runtime_xml_hashes_match": config_match,
        "binary_sha256_pre": pre_binary_sha,
        "binary_sha256_post": str(post_run["binary_sha256"]),
        "pre_source": str(pre_case_dir.resolve()),
        "post_source": str(post_run["output_dir"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r6-root", type=Path, default=R6_DEFAULT)
    parser.add_argument("--r7-root", type=Path, default=R7_DEFAULT)
    parser.add_argument("--post-fix-root", type=Path, default=POST_DEFAULT)
    parser.add_argument("--pre-fix-b-root", type=Path, default=PRE_B_DEFAULT)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_DEFAULT)
    args = parser.parse_args()

    r6_root = args.r6_root.resolve()
    r7_root = args.r7_root.resolve()
    post_root = args.post_fix_root.resolve()
    pre_b_root = args.pre_fix_b_root.resolve()
    output_root = args.output_root.resolve()
    if output_root.exists():
        raise SystemExit(f"Refusing to overwrite evaluation outputs: {output_root}")
    post_manifest_path = post_root / "experiment_manifest.json"
    pre_b_manifest_path = pre_b_root / "experiment_manifest.json"
    post_manifest, pre_b_manifest = _validate_execution_manifests(
        post_manifest_path, pre_b_manifest_path,
    )
    output_root.mkdir(parents=True)
    pairs: list[dict[str, Any]] = []
    cases: dict[str, Any] = {}
    summary_rows: list[dict[str, Any]] = []

    legacy_binary_sha = json.loads(
        (r6_root / "analysis/cfar_branch_metrics/analysis_manifest.json").read_text(
            encoding="utf-8"
        )
    )["production_executable_sha256"]
    for condition in CONDITIONS:
        for mode in MODES:
            case_id = f"{condition}_{mode}"
            post_case_dir = post_root / case_id
            pre_case_dir = _pre_case_dir(condition, mode, r6_root, pre_b_root)
            post_run = post_manifest["runs"][case_id]
            pairing = _pair_provenance(
                condition=condition, mode=mode, post_run=post_run,
                pre_case_dir=pre_case_dir, pre_b_manifest=pre_b_manifest,
                r6_root=r6_root,
            )
            pairs.append(pairing)
            pair_label = f"{condition}_{mode}"
            pre_metrics, pre_provenance = _run_case_evaluation(
                case_dir=pre_case_dir,
                case_output_dir=output_root / "pre_fix" / pair_label,
                condition=condition, mode=mode, manifest_source=(
                    pre_b_manifest_path if condition == "B" else pre_case_dir / "result/run_manifest.json"
                ),
                r6_root=r6_root, post_fix=False,
            )
            post_metrics, post_provenance = _run_case_evaluation(
                case_dir=post_case_dir,
                case_output_dir=output_root / "post_fix" / pair_label,
                condition=condition, mode=mode,
                manifest_source=post_manifest_path,
                r6_root=r6_root, post_fix=True,
            )
            cases[pair_label] = {
                "pairing": pairing,
                "pre_fix": {"metrics": pre_metrics["metrics"], "provenance": pre_provenance},
                "post_fix": {"metrics": post_metrics["metrics"], "provenance": post_provenance},
            }
            for stage, result in (("pre_fix", pre_metrics), ("post_fix", post_metrics)):
                metrics = result["metrics"]
                target = metrics.get("target_only_response", {})
                summary_rows.append({
                    "stage": stage,
                    "condition": condition,
                    "mode": mode,
                    "truth_opportunities": metrics.get("truth_opportunities"),
                    "unique_correct_detections": metrics.get("unique_correct_detections"),
                    "misses": metrics.get("misses"),
                    "false_associations": metrics.get("false_associations"),
                    "position_2d_rmse_m": metrics.get("parameter_errors", {}).get("position_2d_rmse_m"),
                    "angle_rmse_deg": metrics.get("parameter_errors", {}).get("angle_rmse_deg"),
                    "ground_speed_rmse_mps": metrics.get("parameter_errors", {}).get("ground_speed_rmse_mps"),
                    "radial_velocity_status": metrics.get("parameter_errors", {}).get("radial_velocity", {}).get("status"),
                    "radial_velocity_rmse_mps": metrics.get("parameter_errors", {}).get("radial_velocity", {}).get("rmse_mps"),
                    "h0_union_hits": metrics.get("unique_union_hit_cells", {}).get("count"),
                    "cfar_branch_hit_cells": metrics.get("cfar_branch_hit_cells"),
                    "postfilter_clusters": metrics.get("postfilter_clusters"),
                    "final_detections": metrics.get("final_detections"),
                    "false_detections": metrics.get("false_detections"),
                    "confirmed_tracks": metrics.get("confirmed_tracks"),
                    "false_confirmed_tracks": metrics.get("false_confirmed_tracks"),
                    "target_only_amplitude_ratio": target.get("amplitude_ratio"),
                    "target_only_phase_error_rad": target.get("phase_error_rad"),
                    "target_only_energy_ratio": target.get("energy_ratio"),
                    "target_only_response_status": target.get("status"),
                    "lineage_status": metrics.get("lineage_status"),
                })

    _write_csv(output_root / "task_metrics_summary.csv", summary_rows)
    _write_json(output_root / "evaluation_manifest.json", {
        "schema_version": 1,
        "experiment": "PGRCC Phase R7 paired pre/post-fix seed-101 Production task evaluation",
        "r6_root": str(r6_root),
        "post_fix_execution_manifest": str(post_manifest_path),
        "pre_fix_b_execution_manifest": str(pre_b_manifest_path),
        "legacy_a0_c_binary_sha256_from_r6_cfar_manifest": legacy_binary_sha,
        "post_fix_binary_sha256": post_manifest["binary_sha256"],
        "frozen_association_gate": {
            "same_period": True,
            "same_beam": True,
            "range_tolerance_bins": 2,
            "circular_doppler_tolerance_bins": 2,
            "assignment": "maximum-cardinality then minimum-sum normalized Euclidean residual",
            "normalized_residual": "hypot(range_delta/2, circular_doppler_delta/2)",
            "legacy_doppler_axis": "exact per-period CFAR metadata axis; row_truth is not used",
        },
        "pairs": pairs,
        "cases": cases,
    })
    print(json.dumps({"output_root": str(output_root), "paired_cases": len(pairs)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
