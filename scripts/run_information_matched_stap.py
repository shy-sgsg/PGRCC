#!/usr/bin/env python3
"""Compare J2 pair-fused and J4 native four-channel JDL/STAP references.

This runner isolates the spatial-channel degree-of-freedom contrast.  J2 uses
the production (1,3)/(2,4) F1/F2 fusion already produced by the shared replay
loader; J4 uses the native four-channel RD tensor.  Both methods use the same
scene, seed, three Doppler taps, range training support, covariance estimator,
loading, steering hypothesis, GO-CFAR and truth-centered ROI.

The result is an offline scientific reference only.  It does not claim a
production CUDA four-channel STAP implementation or a production advantage.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_baseline_benchmark as v1  # noqa: E402
import run_baseline_v2 as v2  # noqa: E402


JDL_OFFSETS = (-1, 0, 1)
J2_ID = "J2_F1F2_pair_fused_JDL_STAP"
J4_ID = "J4_native_4ch_JDL_STAP"
METHOD_IDS = (J2_ID, J4_ID)
METRIC_FIELDS = (
    "residual_suppression_dB",
    "output_SCNR_dB",
    "SCNR_improvement_dB",
    "target_loss_dB",
    "background_Pfa",
    "target_detected",
    "target_signal_power_after",
    "background_power_after_roi",
)
COMMON_CONTROL_KEYS = (
    "doppler_taps",
    "training_support",
    "covariance_estimator",
    "loading_fraction",
    "target_steering",
    "cfar",
    "roi",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pair_fuse_channels(
    raw_four_channel: np.ndarray,
    compensation_13: np.ndarray | complex | float | None = None,
    compensation_24: np.ndarray | complex | float | None = None,
) -> np.ndarray:
    """Apply the production pair mapping ``F1=(1,3), F2=(2,4)``.

    The optional compensation factors are applied to channels 3 and 4 before
    averaging.  The shared Stage2 replay loader supplies the exact production
    range-dependent factors before constructing ``CaseData.alignment``; this
    helper is a small contract/reference function for tests and diagnostics.
    """

    raw = np.asarray(raw_four_channel)
    if raw.ndim < 1 or raw.shape[-1] != 4:
        raise ValueError(f"expected a final four-channel axis, got {raw.shape}")
    c1, c2, c3, c4 = [raw[..., index] for index in range(4)]
    if compensation_13 is not None:
        c3 = c3 * np.asarray(compensation_13)
    if compensation_24 is not None:
        c4 = c4 * np.asarray(compensation_24)
    return np.stack((0.5 * (c1 + c3), 0.5 * (c2 + c4)), axis=-1)


def jdl_dimension(spatial_channels: int, doppler_taps: int) -> int:
    if spatial_channels not in (2, 4):
        raise ValueError("spatial_channels must be 2 or 4")
    if doppler_taps < 1:
        raise ValueError("doppler_taps must be positive")
    return int(spatial_channels * doppler_taps)


def common_contract() -> dict[str, object]:
    return {
        "J2": {
            "matrix_id": J2_ID,
            "spatial_channels": 2,
            "fusion": "production_(1,3)_(2,4)_F1_F2",
            "interface": "pair_fused_two_channel_JDL_STAP_offline_reference",
        },
        "J4": {
            "matrix_id": J4_ID,
            "spatial_channels": 4,
            "fusion": "none_native_four_channel",
            "interface": "native_four_channel_JDL_STAP_offline_reference",
        },
        "comparison": {
            "pure_spatial_dof_delta": "J4 - J2",
            "same_scene_seed_training_loading_steering_cfar_roi": True,
            "production_cuda": False,
            "current_deployment_baseline": "production Current F1/F2 CSI is reported separately in the existing baseline matrix",
        },
    }


def validate_common_controls(controls: Mapping[str, object]) -> None:
    missing = [key for key in COMMON_CONTROL_KEYS if key not in controls]
    if missing:
        raise ValueError(f"missing common controls: {missing}")
    if int(controls["doppler_taps"]) != len(JDL_OFFSETS):
        raise ValueError("J2/J4 must use the same three Doppler taps")
    if float(controls["loading_fraction"]) <= 0.0:
        raise ValueError("loading_fraction must be positive")


def _finite_difference(candidate: object, reference: object) -> float:
    try:
        a = float(candidate)  # type: ignore[arg-type]
        b = float(reference)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan
    return a - b if math.isfinite(a) and math.isfinite(b) else math.nan


def pure_spatial_dof_delta(
    j4_metrics: Mapping[str, object], j2_metrics: Mapping[str, object]
) -> dict[str, object]:
    """Return the explicitly named candidate-minus-reference J4-J2 contrast."""

    result: dict[str, object] = {
        "interpretation": "pure spatial-DOF delta J4 - J2",
        "candidate_matrix_id": J4_ID,
        "reference_matrix_id": J2_ID,
    }
    for field in METRIC_FIELDS:
        result[f"delta_{field}"] = _finite_difference(
            j4_metrics.get(field), j2_metrics.get(field)
        )
    return result


def _safe_level(value: float) -> str:
    return str(value).replace("-", "m").replace(".", "p")


def _cases(
    suite: Mapping[str, object], seeds: Sequence[int], velocities: Sequence[float]
) -> list[dict[str, object]]:
    base = v2._base_case(dict(suite))
    cases: list[dict[str, object]] = []
    for velocity in velocities:
        for seed in seeds:
            case = copy.deepcopy(base)
            case["case_id"] = f"pure_dof_v{_safe_level(float(velocity))}_s{int(seed)}"
            case["target_id"] = f"PURE_DOF_TARGET_v{_safe_level(float(velocity))}_s{int(seed)}"
            case["seed"] = int(seed)
            case["velocity_mode"] = "radial"
            case["radial_speed_mps"] = float(velocity)
            case["factor"] = "pure_spatial_dof_velocity"
            case["factor_family"] = "pure_spatial_dof"
            case["factor_level"] = float(velocity)
            case["label"] = f"common scene radial velocity {float(velocity):g} m/s, seed {int(seed)}"
            case["use_paired_background"] = True
            cases.append(case)
    return cases


def _spatial_steering(data: v1.CaseData, spatial_channels: int, range_m: float) -> np.ndarray:
    native = v2.spatial_steering(v2.target_beam_angle_deg(data), range_m, data.p)
    if spatial_channels == 4:
        return native
    fused = pair_fuse_channels(native.reshape(1, 1, 4))[0, 0]
    return fused / max(float(np.linalg.norm(fused)), 1.0e-12) * math.sqrt(2.0)


def _space_time_steering(
    data: v1.CaseData,
    row: int,
    range_m: float,
    spatial_channels: int,
) -> np.ndarray:
    spatial = _spatial_steering(data, spatial_channels, range_m)
    parts: list[np.ndarray] = []
    target_fd = float(data.axis[row] - data.fa_ctr)
    for offset in JDL_OFFSETS:
        feature_row = (row + int(offset)) % data.p.pulse_num
        feature_fd = float(data.axis[feature_row] - data.fa_ctr)
        temporal = v2.temporal_response(target_fd, feature_fd, data.p)
        parts.append(temporal * spatial)
    vector = np.concatenate(parts).astype(np.complex128)
    return vector / max(float(np.linalg.norm(vector)), 1.0e-12) * math.sqrt(len(parts))


def _jdl_run(
    data: v1.CaseData,
    spatial_channels: int,
    method_config: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    p = data.p
    if spatial_channels not in (2, 4):
        raise ValueError("JDL runner supports two or four spatial channels")
    if spatial_channels == 2:
        # background_alignment is made from the exact production fusion path
        # with a background-only range/P38 fit, so target data never trains J2.
        alignment = data.background_alignment
        bg_rd = np.stack((alignment.f1_bg, alignment.f2_bg), axis=2)
        target_rd = np.stack((alignment.f1_target, alignment.f2_target), axis=2)
        fusion_source = "CaseData.background_alignment from oracle.fuse_four_channels production (1,3)/(2,4)"
    else:
        bg_rd = np.asarray(data.raw_bg_rd, dtype=np.complex128)
        target_rd = np.asarray(data.raw_target_rd, dtype=np.complex128)
        fusion_source = "CaseData.raw_*_rd native four-channel input; no pair fusion"

    taps = len(JDL_OFFSETS)
    block_size = int(method_config.get("range_block_size", 256))
    local_half_width = int(method_config.get("local_training_half_width", 256))
    guard = int(method_config.get("cut_guard_bins", 4))
    loading_fraction = float(method_config.get("diagonal_loading_fraction", 0.01))
    if not math.isfinite(loading_fraction) or loading_fraction <= 0.0:
        raise ValueError("diagonal_loading_fraction must be finite and positive")
    edges = v2.block_edges(
        max(0, int(p.rg_st)),
        min(int(getattr(p, "range_compress_len", p.rg_ed + 1)), int(p.rg_ed) + 1),
        block_size,
    )
    if not edges:
        raise RuntimeError("no common range training blocks")

    features_bg = np.concatenate(
        [np.roll(bg_rd, -int(offset), axis=0) for offset in JDL_OFFSETS], axis=2
    )
    features_target = np.concatenate(
        [np.roll(target_rd, -int(offset), axis=0) for offset in JDL_OFFSETS], axis=2
    )
    dimension = jdl_dimension(spatial_channels, taps)
    weights = np.zeros((p.pulse_num, len(edges), dimension), dtype=np.complex128)
    condition_values: list[float] = []
    training_counts: list[float] = []
    steering_errors: list[float] = []
    started = time.perf_counter()
    for row in range(p.pulse_num):
        for block_index, (lo, hi) in enumerate(edges):
            center = (lo + hi - 1) // 2
            range_m = v2.range_at_column(center, p)
            cols = v2.training_columns(center, p, "global", local_half_width, guard)
            samples = features_bg[row, cols, :]
            covariance, stats = v2.loaded_covariance(samples, False, loading_fraction)
            steering = _space_time_steering(data, row, range_m, spatial_channels)
            if steering.size != dimension:
                raise RuntimeError(f"steering dimension mismatch: {steering.size} != {dimension}")
            weights[row, block_index, :] = v2.mvdr_weight(covariance, steering)
            condition_values.append(float(stats["covariance_condition_number"]))
            training_counts.append(float(stats["training_sample_count"]))
            steering_errors.append(abs(np.vdot(weights[row, block_index], steering) - 1.0))

    bg = np.zeros(bg_rd.shape[:2], dtype=np.complex128)
    target = np.zeros(target_rd.shape[:2], dtype=np.complex128)
    for block_index, (lo, hi) in enumerate(edges):
        weight = weights[:, block_index, :]
        bg[:, lo:hi] = np.einsum(
            "md,mrd->mr", weight.conj(), features_bg[:, lo:hi, :], optimize=True
        )
        target[:, lo:hi] = np.einsum(
            "md,mrd->mr", weight.conj(), features_target[:, lo:hi, :], optimize=True
        )
    metrics = v2.output_metrics(
        data,
        bg,
        target,
        bg,
        target,
        bg_rd[:, :, 0],
        target_rd[:, :, 0],
        detection_threshold_scale=1.0,
    )
    finite_condition = np.asarray(condition_values, dtype=float)
    finite_condition = finite_condition[np.isfinite(finite_condition)]
    details: dict[str, object] = {
        "method_kind": "JDL/MVDR",
        "spatial_channels": spatial_channels,
        "jdl_dimension": dimension,
        "doppler_offsets": list(JDL_OFFSETS),
        "training_mode": "global",
        "training_support": "rg_st..rg_ed excluding common cut_guard_bins",
        "range_block_size": block_size,
        "local_training_half_width": local_half_width,
        "cut_guard_bins": guard,
        "covariance_estimator": "loaded sample covariance",
        "loading_fraction": loading_fraction,
        "target_steering": "configured beam-center spatial hypothesis; pair-projected for J2",
        "cfar": "production GO fixed alpha/guard/background via shared evaluator",
        "roi": "same truth-centered ROI and target truth labels for both methods",
        "fusion_source": fusion_source,
        "training_source": "paired C+N background only",
        "training_sample_count_mean": float(np.mean(training_counts)),
        "covariance_condition_number_mean": float(np.mean(finite_condition)) if finite_condition.size else math.nan,
        "covariance_condition_number_p95": float(np.percentile(finite_condition, 95.0)) if finite_condition.size else math.nan,
        "steering_projection_error_mean": float(np.mean(steering_errors)),
        "steering_projection_error_p95": float(np.percentile(steering_errors, 95.0)),
        "weight_build_runtime_ms": 1000.0 * (time.perf_counter() - started),
        "production_cuda": False,
        "ai_training": False,
    }
    return metrics, details


def _result_row(
    data: v1.CaseData,
    matrix_id: str,
    metrics: Mapping[str, object],
    details: Mapping[str, object],
) -> dict[str, object]:
    row: dict[str, object] = {
        "case_id": data.case["case_id"],
        "seed": int(data.case["seed"]),
        "velocity_mps": float(data.case["factor_level"]),
        "matrix_id": matrix_id,
        "spatial_channels": details["spatial_channels"],
        "jdl_dimension": details["jdl_dimension"],
        "fusion_source": details["fusion_source"],
        "same_scene_seed_controls": True,
        "production_cuda": False,
        "ai_training": False,
    }
    for field in METRIC_FIELDS:
        row[field] = metrics.get(field, math.nan)
    for key in (
        "training_sample_count_mean",
        "covariance_condition_number_mean",
        "covariance_condition_number_p95",
        "steering_projection_error_mean",
        "steering_projection_error_p95",
        "weight_build_runtime_ms",
    ):
        row[key] = details.get(key, math.nan)
    return row


def _aggregate(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for matrix_id in METHOD_IDS:
        members = [row for row in rows if row["matrix_id"] == matrix_id]
        if not members:
            continue
        record: dict[str, object] = {
            "matrix_id": matrix_id,
            "spatial_channels": members[0]["spatial_channels"],
            "jdl_dimension": members[0]["jdl_dimension"],
            "n_cases": len(members),
            "n_seeds": len({int(row["seed"]) for row in members}),
            "production_cuda": False,
        }
        for field in METRIC_FIELDS:
            values = [
                float(row[field]) for row in members
                if _is_finite(row.get(field))
            ]
            record[f"{field}_mean"] = float(np.mean(values)) if values else math.nan
            record[f"{field}_std"] = float(np.std(values, ddof=1)) if len(values) >= 2 else math.nan
        result.append(record)
    return result


def _is_finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _pairwise(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    by_case = {(str(row["case_id"]), str(row["matrix_id"])): row for row in rows}
    result: list[dict[str, object]] = []
    for case_id in sorted({str(row["case_id"]) for row in rows}):
        j4 = by_case.get((case_id, J4_ID))
        j2 = by_case.get((case_id, J2_ID))
        if j4 is None or j2 is None:
            continue
        result.append({
            "case_id": case_id,
            "seed": j4["seed"],
            "velocity_mps": j4["velocity_mps"],
            "candidate_matrix_id": J4_ID,
            "reference_matrix_id": J2_ID,
            "interpretation": "pure spatial-DOF delta J4 - J2",
            "same_scene_seed_controls": True,
            **pure_spatial_dof_delta(j4, j2),
        })
    return result


def _pairwise_aggregate(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    if not rows:
        return []
    record: dict[str, object] = {
        "candidate_matrix_id": J4_ID,
        "reference_matrix_id": J2_ID,
        "interpretation": "pure spatial-DOF delta J4 - J2",
        "n_cases": len(rows),
        "n_seeds": len({int(row["seed"]) for row in rows}),
        "same_scene_seed_controls": all(bool(row["same_scene_seed_controls"]) for row in rows),
    }
    for field in METRIC_FIELDS:
        values = [float(row[f"delta_{field}"]) for row in rows if _is_finite(row.get(f"delta_{field}"))]
        record[f"delta_{field}_mean"] = float(np.mean(values)) if values else math.nan
        record[f"delta_{field}_std"] = float(np.std(values, ddof=1)) if len(values) >= 2 else math.nan
        record[f"delta_{field}_positive_fraction"] = (
            float(np.count_nonzero(np.asarray(values) > 0.0) / len(values)) if values else math.nan
        )
    return [record]


def _write_csv(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    materialized = list(rows)
    if not materialized:
        path.write_text("\n", encoding="utf-8")
        return
    fields: list[str] = []
    for row in materialized:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(materialized)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


def run(
    output_dir: Path,
    suite_path: Path = ROOT / "configs/research/ai_csi_baseline_v2_suite.json",
    seeds: Sequence[int] = (101, 202, 303),
    velocities: Sequence[float] = (0.5, 1.0, 2.0),
) -> dict[str, object]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    base_config, source_suite = v2.load_suite(suite_path)
    suite = copy.deepcopy(source_suite)
    suite["output_root"] = str(output_dir / "cases")
    cases = _cases(suite, seeds, velocities)
    method_config = dict(source_suite.get("method_config", {}))
    controls = {
        "doppler_taps": len(JDL_OFFSETS),
        "training_support": "rg_st..rg_ed excluding common cut_guard_bins",
        "covariance_estimator": "loaded sample covariance",
        "loading_fraction": float(method_config.get("diagonal_loading_fraction", 0.01)),
        "target_steering": "configured beam-center spatial hypothesis; pair-projected for J2",
        "cfar": "production GO fixed alpha/guard/background via shared evaluator",
        "roi": "same truth-centered ROI and target truth labels for both methods",
    }
    validate_common_controls(controls)
    rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    started = time.perf_counter()
    for index, case in enumerate(cases, start=1):
        print(json.dumps({
            "stage": "pure_spatial_dof_case",
            "index": index,
            "total": len(cases),
            "case_id": case["case_id"],
        }, ensure_ascii=False), flush=True)
        paths = v1.prepare_case_data(base_config, suite, case, keep_data=False)
        try:
            data = v1.load_case_data(case, paths)
            for matrix_id, channels in ((J2_ID, 2), (J4_ID, 4)):
                metrics, details = _jdl_run(data, channels, method_config)
                rows.append(_result_row(data, matrix_id, metrics, details))
        except Exception as exc:  # keep an auditable failure row in the manifest
            failures.append({"case_id": case["case_id"], "error": repr(exc)})
            print(f"[pure-dof][ERR] {case['case_id']}: {exc}", file=sys.stderr, flush=True)
        finally:
            v1.cleanup_case(paths)

    pairwise = _pairwise(rows)
    aggregates = _aggregate(rows)
    pairwise_aggregate = _pairwise_aggregate(pairwise)
    _write_csv(output_dir / "information_matched_stap_summary.csv", rows)
    _write_csv(output_dir / "information_matched_stap_aggregate.csv", aggregates)
    _write_csv(output_dir / "information_matched_pairwise_attribution.csv", pairwise)
    _write_csv(output_dir / "information_matched_pairwise_aggregate.csv", pairwise_aggregate)
    _write_json(output_dir / "comparison_contract.json", {
        "schema": "unknown_system_error_pure_spatial_dof_contract_v1",
        "ai_training": False,
        "contract": common_contract(),
        "common_controls": controls,
        "method_config": method_config,
        "cases": [
            {"case_id": c["case_id"], "seed": c["seed"], "velocity_mps": c["factor_level"]}
            for c in cases
        ],
    })
    manifest = {
        "schema": "unknown_system_error_pure_spatial_dof_manifest_v1",
        "status": "completed" if not failures else "completed_with_failures",
        "ai_training": False,
        "production_cuda": False,
        "case_count_requested": len(cases),
        "case_count_completed": len({str(row["case_id"]) for row in rows}),
        "row_count": len(rows),
        "pairwise_row_count": len(pairwise),
        "failures": failures,
        "elapsed_s": time.perf_counter() - started,
        "source_commit": _git_commit(),
        "working_directory": str(ROOT),
        "python": sys.version,
        "platform": platform.platform(),
        "suite": str(suite_path.resolve()),
        "suite_sha256": _sha256(suite_path),
        "contract": common_contract(),
        "common_controls": controls,
        "attribution_boundary": "J4-J2 is a pure spatial-DOF contrast only because all listed controls are shared; it is not a production CUDA superiority claim",
        "artifacts": {
            "summary": str((output_dir / "information_matched_stap_summary.csv").resolve()),
            "aggregate": str((output_dir / "information_matched_stap_aggregate.csv").resolve()),
            "pairwise_attribution": str((output_dir / "information_matched_pairwise_attribution.csv").resolve()),
            "pairwise_aggregate": str((output_dir / "information_matched_pairwise_aggregate.csv").resolve()),
            "contract": str((output_dir / "comparison_contract.json").resolve()),
        },
        "limitations": [
            "J2 and J4 are offline scientific JDL/MVDR references; production Current remains the deployment baseline.",
            "The J2 input is production pair-fused F1/F2; J4 retains native four-channel spatial degrees of freedom.",
            "No conclusion is drawn if the J4-J2 delta is unstable or non-material across the stated cases.",
        ],
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=ROOT / "configs/research/ai_csi_baseline_v2_suite.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/unknown_system_error_pure_spatial_dof_20260914")
    parser.add_argument("--seeds", type=int, nargs="+", default=[101, 202, 303])
    parser.add_argument("--velocities", type=float, nargs="+", default=[0.5, 1.0, 2.0])
    args = parser.parse_args(argv)
    manifest = run(args.output_dir.resolve(), args.suite.resolve(), args.seeds, args.velocities)
    print(json.dumps({
        "status": manifest["status"],
        "output_dir": str(args.output_dir.resolve()),
        "case_count_requested": manifest["case_count_requested"],
        "case_count_completed": manifest["case_count_completed"],
        "row_count": manifest["row_count"],
        "pairwise_row_count": manifest["pairwise_row_count"],
    }, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "completed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
