#!/usr/bin/env python3
"""Run a beam-50 Native CUDA diagnostic replay and audit GO-CFAR against float64."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r.go_cfar_reference import CfarGeometry, thresholds_integral_f64  # noqa: E402

EXPECTED_BINARY_SHA256 = "de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00"
BRANCHES = ("dynamic_in_band", "full_band")
R8_CONDITIONS = (
    "A0_Ideal",
    "A1_Current_unknown_error",
    "A2_Known_error_correction_upper_bound",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def read_f32_map(path: Path, height: int, width: int) -> np.ndarray:
    values = np.fromfile(path, dtype="<f4")
    expected = height * width
    if values.size != expected:
        raise ValueError(f"{path}: expected {expected} float32 values, got {values.size}")
    return values.reshape(height, width)


def _geometry(row: Mapping[str, Any]) -> CfarGeometry:
    return CfarGeometry(
        height=int(row["height"]),
        width=int(row["width"]),
        guard_cells=int(row["guard_cells"]),
        background_cells=int(row["background_cells"]),
        doppler_circular=str(row["doppler_circular"]).strip().lower() == "true",
        exclude_row_start=int(row["exclude_row_start"]),
        exclude_row_end=int(row["exclude_row_end"]),
        cut_band_start=int(row["cut_band_start"]),
        cut_band_end=int(row["cut_band_end"]),
        cut_band_mode=int(row["cut_band_mode"]),
    )


def audit_branch_maps(
    geometry_row: Mapping[str, Any],
    power: np.ndarray,
    threshold: np.ndarray,
    hits: np.ndarray,
) -> dict[str, Any]:
    """Check one saved CUDA branch map against the independent float64 reference."""
    geometry = _geometry(geometry_row)
    alpha = float(geometry_row["alpha"])
    if power.dtype != np.float32 or threshold.dtype != np.float32 or hits.dtype != np.float32:
        raise ValueError("Production maps must be float32")
    if power.shape != threshold.shape or power.shape != hits.shape:
        raise ValueError("Production power, threshold, and hit maps must have equal shapes")

    reference = thresholds_integral_f64(power, geometry, alpha)
    valid = reference.valid_cut
    reference_f32 = reference.threshold.astype(np.float32)
    actual_hits = hits > 0.0
    expected_hits = (power > reference_f32) & valid
    hit_flips = int(np.count_nonzero((actual_hits != expected_hits) & valid))
    actual_valid = threshold[valid].astype(np.float64)
    reference_valid = reference.threshold[valid]
    reference_ulp = np.abs(np.spacing(reference_f32[valid]).astype(np.float64))
    difference = np.abs(actual_valid - reference_valid)
    ulp_error = difference / np.where(reference_ulp > 0.0, reference_ulp, np.inf)

    declared_valid = int(geometry_row["valid_cut_count"])
    declared_tests = int(geometry_row["threshold_test_count"])
    nonfinite_power = int(np.count_nonzero(~np.isfinite(power[valid])))
    negative_power = int(np.count_nonzero(power[valid] < 0.0))
    nonfinite_threshold = int(np.count_nonzero(~np.isfinite(threshold[valid])))
    negative_threshold = int(np.count_nonzero(threshold[valid] < 0.0))
    zero_threshold = int(np.count_nonzero(threshold[valid] == 0.0))
    positive_reference_nonpositive = int(
        np.count_nonzero((threshold[valid] <= 0.0) & (reference_valid > 0.0))
    )
    reference_zero = int(np.count_nonzero(reference_valid == 0.0))
    exact_f32 = int(np.count_nonzero(threshold[valid] == reference_f32[valid]))
    over_two_ulp = int(np.count_nonzero(ulp_error > 2.0))

    checks = {
        "reference_valid_cut_count_matches_geometry": declared_valid == int(valid.sum()),
        "threshold_test_count_matches_valid_cut_count": declared_tests == declared_valid,
        "power_is_finite_nonnegative": nonfinite_power == 0 and negative_power == 0,
        "threshold_is_finite": nonfinite_threshold == 0,
        "threshold_has_no_negative_values": negative_threshold == 0,
        "no_nonpositive_threshold_against_positive_reference": positive_reference_nonpositive == 0,
        "production_hits_match_float64_reference": hit_flips == 0,
        "threshold_error_within_two_float32_ulp": over_two_ulp == 0,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "valid_cut_count": int(valid.sum()),
        "declared_valid_cut_count": declared_valid,
        "threshold_test_count": declared_tests,
        "hit_flips": hit_flips,
        "reference_hits": int(np.count_nonzero(expected_hits)),
        "production_hits": int(np.count_nonzero(actual_hits & valid)),
        "threshold_max_abs_error": float(np.max(difference)) if difference.size else 0.0,
        "threshold_p99_abs_error": float(np.quantile(difference, 0.99)) if difference.size else 0.0,
        "threshold_max_float32_ulp_error": float(np.max(ulp_error)) if ulp_error.size else 0.0,
        "threshold_cuts_over_two_float32_ulp": over_two_ulp,
        "threshold_exact_float32_reference_cuts": exact_f32,
        "threshold_negative_count": negative_threshold,
        "threshold_zero_count": zero_threshold,
        "reference_zero_count": reference_zero,
        "nonpositive_threshold_with_positive_reference_count": positive_reference_nonpositive,
        "nonfinite_power_count": nonfinite_power,
        "negative_power_count": negative_power,
        "nonfinite_threshold_count": nonfinite_threshold,
        "checks": checks,
        "cpu_direct_fallback_direction_count": reference.direct_fallback_count,
    }


def _map_path(debug_dir: Path, result_id: int, branch: str, suffix: str) -> Path:
    stem = f"cfar_GMTI{result_id:02d}_beam050"
    branch_name = f"_{branch}" if branch else ""
    return debug_dir / f"{stem}{branch_name}_{suffix}.f32"


def audit_dumped_maps(result_dir: Path, expected_periods: int = 3) -> dict[str, Any]:
    debug_dir = result_dir / "debug"
    geometry_path = debug_dir / "cfar_geometry_diagnostics.csv"
    numerical_path = debug_dir / "cfar_numerical_diagnostics.csv"
    if not geometry_path.is_file() or not numerical_path.is_file():
        raise FileNotFoundError("CFAR geometry or numerical diagnostics are missing")
    geometry_rows = read_csv(geometry_path)
    numerical_rows = read_csv(numerical_path)
    expected_keys = {
        (period, f"union_{branch}")
        for period in range(expected_periods)
        for branch in BRANCHES
    }
    geometry_by_key = {(int(row["period_id"]), row["branch"]): row for row in geometry_rows}
    if len(geometry_by_key) != len(geometry_rows) or set(geometry_by_key) != expected_keys:
        raise ValueError("CFAR geometry rows do not uniquely cover the three periods and two branches")
    numerical_by_key = {(int(row["period_id"]), row["branch"]): row for row in numerical_rows}
    if len(numerical_by_key) != len(numerical_rows) or set(numerical_by_key) != expected_keys:
        raise ValueError("CFAR numerical rows do not uniquely cover the three periods and two branches")

    branch_period_rows: list[dict[str, Any]] = []
    union_period_rows: list[dict[str, Any]] = []
    evidence_paths: set[Path] = {geometry_path, numerical_path}
    for period in range(expected_periods):
        result_id = period + 1
        branch_maps: dict[str, dict[str, np.ndarray]] = {}
        branch_reports: dict[str, dict[str, Any]] = {}
        for branch in BRANCHES:
            geometry_row = geometry_by_key[(period, f"union_{branch}")]
            height, width = int(geometry_row["height"]), int(geometry_row["width"])
            maps = {
                name: read_f32_map(_map_path(debug_dir, result_id, branch, name), height, width)
                for name in ("power", "threshold", "hits")
            }
            for suffix in ("power", "threshold", "hits"):
                evidence_paths.add(_map_path(debug_dir, result_id, branch, suffix))
            report = audit_branch_maps(
                geometry_row, maps["power"], maps["threshold"], maps["hits"]
            )
            numerical = numerical_by_key[(period, f"union_{branch}")]
            numerical_tests = int(numerical["threshold_test_count"])
            fallback_directions = int(numerical["direct_fallback_direction_count"])
            max_fallback = 4 * numerical_tests
            report["geometry"] = {
                key: geometry_row[key]
                for key in (
                    "height", "width", "guard_cells", "background_cells",
                    "doppler_circular", "exclude_row_start", "exclude_row_end",
                    "cut_band_start", "cut_band_end", "cut_band_mode", "alpha",
                    "hit_cut_count", "hit_index_hash",
                )
            }
            report["cuda_direct_fallback_direction_count"] = fallback_directions
            report["cuda_fallback_count_within_four_directions_per_cut"] = (
                0 <= fallback_directions <= max_fallback
            )
            report["status"] = (
                "passed" if report["status"] == "passed"
                and report["cuda_fallback_count_within_four_directions_per_cut"]
                else "failed"
            )
            report["period_id"] = period
            report["result_id"] = result_id
            report["branch"] = branch
            branch_period_rows.append(report)
            branch_maps[branch] = maps
            branch_reports[branch] = report

        union_maps = {
            name: read_f32_map(_map_path(debug_dir, result_id, "", name), 130, 4096)
            for name in ("power", "threshold", "hits")
        }
        for suffix in ("power", "threshold", "hits"):
            evidence_paths.add(_map_path(debug_dir, result_id, "", suffix))
        dynamic = branch_maps["dynamic_in_band"]
        full = branch_maps["full_band"]
        use_dynamic = dynamic["power"] >= full["power"]
        expected_union_threshold = np.where(
            use_dynamic, dynamic["threshold"], full["threshold"]
        )
        # Both branches have the same geometry in this production configuration.
        # Use the independently reconstructed masks for the array comparisons.
        dynamic_reference = thresholds_integral_f64(
            dynamic["power"],
            _geometry(geometry_by_key[(period, "union_dynamic_in_band")]),
            float(geometry_by_key[(period, "union_dynamic_in_band")]["alpha"]),
        )
        full_reference = thresholds_integral_f64(
            full["power"],
            _geometry(geometry_by_key[(period, "union_full_band")]),
            float(geometry_by_key[(period, "union_full_band")]["alpha"]),
        )
        valid_union_mask = dynamic_reference.valid_cut | full_reference.valid_cut
        expected_union_hits = (
            ((dynamic["power"] > dynamic_reference.threshold.astype(np.float32))
             & dynamic_reference.valid_cut)
            | ((full["power"] > full_reference.threshold.astype(np.float32))
               & full_reference.valid_cut)
        )
        union_checks = {
            "union_power_matches_branch_max": bool(np.array_equal(
                union_maps["power"], np.maximum(dynamic["power"], full["power"])
            )),
            "union_threshold_matches_higher_power_branch": bool(np.array_equal(
                union_maps["threshold"][valid_union_mask],
                expected_union_threshold[valid_union_mask],
            )),
            "union_hits_match_float64_reference": bool(np.array_equal(
                (union_maps["hits"] > 0.0)[valid_union_mask],
                expected_union_hits[valid_union_mask],
            )),
            "union_hit_count_matches_float64_reference": int(np.count_nonzero(
                (union_maps["hits"] > 0.0) & valid_union_mask
            )) == int(np.count_nonzero(expected_union_hits)),
            "union_threshold_is_finite_on_valid_cuts": bool(np.isfinite(
                union_maps["threshold"][valid_union_mask]
            ).all()),
        }
        union_period_rows.append({
            "period_id": period,
            "result_id": result_id,
            "status": "passed" if all(union_checks.values()) else "failed",
            "valid_cut_count": int(valid_union_mask.sum()),
            "production_union_hit_count": int(np.count_nonzero(
                (union_maps["hits"] > 0.0) & valid_union_mask
            )),
            "checks": union_checks,
        })

    all_passed = (
        all(row["status"] == "passed" for row in branch_period_rows)
        and all(row["status"] == "passed" for row in union_period_rows)
    )
    return {
        "status": "passed" if all_passed else "failed",
        "branch_period_rows": branch_period_rows,
        "union_period_rows": union_period_rows,
        "evidence_paths": sorted(str(path.resolve()) for path in evidence_paths),
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def on_branch_manifest_path(case_root: str | Path, condition: str) -> Path:
    return role_branch_manifest_path(case_root, condition, "ON")


def role_branch_manifest_path(case_root: str | Path, condition: str, role: str) -> Path:
    if condition not in R8_CONDITIONS:
        raise ValueError(f"unknown R8 condition: {condition}")
    if role not in {"ON", "OFF"}:
        raise ValueError("role must be ON or OFF for R8 CFAR numeric audit")
    return Path(case_root) / "production" / f"{condition}_{role}" / "branch_manifest.json"


def _gpu_snapshot() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,pstate,power.draw,temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader"],
            cwd=ROOT, text=True, capture_output=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "NOT_EVALUABLE", "reason": str(exc)}
    return {
        "status": "captured" if result.returncode == 0 else "NOT_EVALUABLE",
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def run_audit(
    case_root: Path,
    output_root: Path,
    binary: Path,
    condition: str = "A0_Ideal",
    role: str = "ON",
) -> dict[str, Any]:
    case_root = case_root.resolve()
    output_root = output_root.resolve()
    binary = binary.resolve()
    if output_root.exists() and (not output_root.is_dir() or any(output_root.iterdir())):
        raise ValueError(f"refusing to overwrite non-empty output directory: {output_root}")
    case_manifest_path = case_root / "case_manifest.json"
    case = json.loads(case_manifest_path.read_text(encoding="utf-8"))
    branch_path = role_branch_manifest_path(case_root, condition, role)
    branch = json.loads(branch_path.read_text(encoding="utf-8"))
    condition_record = case.get("conditions", {}).get(condition, {})
    role_record = condition_record.get("production", {}).get(role, {})
    if (
        case.get("status") != "completed"
        or branch.get("status") != "passed"
        or role_record.get("status") != "passed"
        or role_record.get("production_returncode") != 0
    ):
        raise ValueError(f"seed case and {condition}/{role} Production branch must be completed and passed")
    if not binary.is_file() or sha256(binary) != EXPECTED_BINARY_SHA256:
        raise ValueError("Native CUDA Production binary differs from the frozen R7 binary")
    source_xml = Path(str(branch["production_xml"])).resolve()
    periods = [Path(str(row["path"])).resolve() for row in branch["period_paths"]]
    if len(periods) != 3 or any(not path.is_file() for path in periods):
        raise ValueError("A0/ON must have three existing Production input periods")
    if not source_xml.is_file():
        raise FileNotFoundError(f"Production runtime XML is missing: {source_xml}")

    output_root.mkdir(parents=True, exist_ok=True)
    result_dir = output_root / "result"
    track_dir = output_root / "track_debug"
    result_dir.mkdir()
    track_dir.mkdir()
    runtime_xml = output_root / "runtime.xml"
    runtime_xml.write_bytes(source_xml.read_bytes())
    if sha256(runtime_xml) != sha256(source_xml):
        raise RuntimeError("copied diagnostic runtime XML differs from the frozen Production XML")
    command = [
        str(binary), "--config", str(runtime_xml),
        "--result-dir", str(result_dir),
        "--track-debug-dir", str(track_dir),
        "--track-debug-dump=on", "--runtime-mode=debug",
        "--runtime-diagnostics=on", "--local-test",
        *[f"{index + 1}={path}" for index, path in enumerate(periods)],
    ]
    environment = os.environ.copy()
    previous_dump_beam = environment.get("GMTI_CFAR_DUMP_BEAM")
    environment["GMTI_CFAR_DUMP_BEAM"] = "50"
    log_path = output_root / "gmt_pipe_core.log"
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "R8 targeted repaired GO-CFAR numerical audit",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "case_root": str(case_root),
        "case_manifest": str(case_manifest_path.resolve()),
        "branch_manifest": str(branch_path.resolve()),
        "condition": condition,
        "role": role,
        "period_count": 3,
        "production_binary": {
            "path": str(binary),
            "sha256": sha256(binary),
            "backend": "Native CUDA",
            "use_corex": False,
        },
        "source_runtime_xml": str(source_xml),
        "source_runtime_xml_sha256": sha256(source_xml),
        "runtime_xml": str(runtime_xml),
        "runtime_xml_sha256": sha256(runtime_xml),
        "input_periods": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in periods
        ],
        "environment_override": {"GMTI_CFAR_DUMP_BEAM": "50"},
        "environment_previous_value": previous_dump_beam,
        "command": command,
        "command_shell": shlex.join(command),
        "gpu_before": _gpu_snapshot(),
        "output_root": str(output_root),
        "result_dir": str(result_dir),
        "track_debug_dir": str(track_dir),
        "log": str(log_path),
    }
    _write_json(output_root / "manifest.json", manifest)
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as stream:
        stream.write(f"$ {shlex.join(command)}\n")
        stream.flush()
        completed = subprocess.run(
            command, cwd=ROOT, env=environment, stdout=stream,
            stderr=subprocess.STDOUT, check=False, timeout=1800,
        )
        stream.write(f"\n[exit_code]={completed.returncode}\n")
    manifest["elapsed_seconds"] = time.monotonic() - started
    manifest["exit_code"] = int(completed.returncode)
    manifest["gpu_after"] = _gpu_snapshot()
    if completed.returncode != 0:
        manifest["status"] = "production_failed"
        _write_json(output_root / "manifest.json", manifest)
        return manifest

    numeric = audit_dumped_maps(result_dir)
    manifest["cfar_numeric_audit"] = numeric
    manifest["evidence_files"] = []
    for raw_path in numeric["evidence_paths"]:
        path = Path(raw_path)
        manifest["evidence_files"].append({
            "path": raw_path, "bytes": path.stat().st_size, "sha256": sha256(path),
        })
    manifest["status"] = "passed" if numeric["status"] == "passed" else "cfar_numeric_gate_failed"
    _write_json(output_root / "manifest.json", manifest)
    _write_json(output_root / "cfar_reference_audit.json", numeric)
    csv_path = output_root / "cfar_reference_audit.csv"
    columns = (
        "period_id", "result_id", "branch", "status", "valid_cut_count",
        "declared_valid_cut_count", "threshold_test_count", "hit_flips",
        "production_hits", "reference_hits", "threshold_max_abs_error",
        "threshold_max_float32_ulp_error", "threshold_cuts_over_two_float32_ulp",
        "threshold_negative_count", "threshold_zero_count",
        "nonpositive_threshold_with_positive_reference_count",
        "cuda_direct_fallback_direction_count",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in numeric["branch_period_rows"]:
            writer.writerow({key: row.get(key) for key in columns})
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--condition", choices=R8_CONDITIONS, default="A0_Ideal")
    parser.add_argument("--role", choices=("ON", "OFF"), default="ON")
    parser.add_argument("--production-binary", type=Path, default=ROOT / "build_r7/GMTI_pipe_core")
    args = parser.parse_args()
    try:
        result = run_audit(
            args.case_root, args.output_root, args.production_binary,
            args.condition, args.role,
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"R8 CFAR numeric audit failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": result["status"],
        "output_root": result["output_root"],
        "binary_sha256": result["production_binary"]["sha256"],
        "cfar_numeric_status": result.get("cfar_numeric_audit", {}).get("status"),
    }, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
