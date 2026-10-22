#!/usr/bin/env python3
"""Run the frozen R7 seed-101 CUDA Production revalidation matrix."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
R6 = ROOT / "outputs/phase_r6_stagewise_divergence_20261022"
R7 = ROOT / "outputs/phase_r7_cfar_correctness_20261022"
DEFAULT_BINARY = ROOT / "build_r7/GMTI_pipe_core"
EXISTING_A0_ON = R7 / "post_fix_focused/A0_ON"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_text(command: list[str], cwd: Path = ROOT) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return (result.stdout + result.stderr).strip()
    return (result.stdout + result.stderr).strip()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_manifest(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def validated_existing_run_record(
    run_manifest_path: Path,
    expected_config: Path,
    expected_input: Path,
    expected_result_dir: Path,
    current_revision: str,
    root: Path = ROOT,
) -> dict[str, object]:
    """Validate a reused run without assigning it the current executable hash."""
    run = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    if run.get("normal_exit") is not True or run.get("exit_code") != 0:
        raise ValueError(f"Existing Production run did not exit cleanly: {run_manifest_path}")
    if run.get("build_type") != "Release" or run.get("executable") != "GMTI_pipe_core":
        raise ValueError(f"Existing Production run has unexpected build identity: {run_manifest_path}")
    recorded_commit = str(run.get("git_commit") or "")
    if not recorded_commit or not current_revision.startswith(recorded_commit):
        raise ValueError(f"Existing Production run source revision differs: {run_manifest_path}")

    def recorded_path(value: object, field: str) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError(f"Existing Production run is missing {field}: {run_manifest_path}")
        path = Path(value)
        return (path if path.is_absolute() else root / path).resolve()

    result_dir = expected_result_dir.resolve()
    if recorded_path(run.get("config_path"), "config_path") != expected_config.resolve():
        raise ValueError(f"Existing Production run config differs: {run_manifest_path}")
    if recorded_path(run.get("input_data_path"), "input_data_path") != expected_input.resolve():
        raise ValueError(f"Existing Production run input differs: {run_manifest_path}")
    if recorded_path(run.get("output_dir"), "output_dir") != result_dir:
        raise ValueError(f"Existing Production run output directory differs: {run_manifest_path}")
    generated = run.get("generated_files")
    if not isinstance(generated, dict):
        raise ValueError(f"Existing Production run has no generated-file record: {run_manifest_path}")
    for key in ("gmti_bin", "detection_results_csv", "timing_metrics_csv"):
        path = recorded_path(generated.get(key), f"generated_files.{key}")
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Existing Production run output is missing or empty: {path}")

    recorded_binary_sha = run.get("binary_sha256")
    if recorded_binary_sha is not None:
        if not isinstance(recorded_binary_sha, str) or len(recorded_binary_sha) != 64:
            raise ValueError(f"Existing Production run has malformed binary SHA: {run_manifest_path}")
        binary_identity_status = "RECORDED_IN_RUN_MANIFEST"
    else:
        binary_identity_status = "NOT_RECORDED_IN_RUN_MANIFEST"

    return {
        "status": "EXECUTED",
        "output_dir": str(result_dir.parent),
        "runtime_log": str(result_dir.parent / "runtime.log"),
        "run_manifest_path": str(run_manifest_path.resolve()),
        "git_commit": recorded_commit,
        "input_path": str(expected_input.resolve()),
        "input_sha256": sha256(expected_input),
        "config_path": str(expected_config.resolve()),
        "config_sha256": sha256(expected_config),
        "binary_sha256": recorded_binary_sha,
        "binary_identity_status": binary_identity_status,
        "exit_code": 0,
        "normal_exit": True,
    }


def build_case_specs() -> list[dict[str, object]]:
    stage = R6 / "reconstruction/stages"
    production = R6 / "production"
    corrected = R6 / "reconstruction/corrected_C"
    specs: list[dict[str, object]] = []
    for mode in ("OFF", "ON", "TO"):
        if mode == "OFF":
            a0_source = stage / "A0_OFF_bg_replay1/data/stage2_statistical_newprotocol_period_0000.bin"
            b_source = stage / "A1_OFF_bg_replay1/data/stage2_statistical_newprotocol_period_0000.bin"
        elif mode == "ON":
            a0_source = stage / "A0_ON/data/stage2_statistical_newprotocol_period_0000.bin"
            b_source = stage / "A1_ON_bg_replay1/data/stage2_statistical_newprotocol_period_0000.bin"
        else:
            a0_source = stage / "A0_TO_bg_replay1/data/stage2_statistical_newprotocol_period_0000.bin"
            b_source = stage / "A1_TO_bg_replay1/data/stage2_statistical_newprotocol_period_0000.bin"
        a0_config_name = "A0_ON_rep1" if mode == "ON" else f"A0_{mode}"
        if mode != "ON":
            specs.append(
                {
                    "case_id": f"A0_{mode}",
                    "condition": "A0_0ns_current",
                    "mode": mode,
                    "config": production / a0_config_name / "runtime.xml",
                    "input": a0_source,
                    "diagnostics": True,
                }
            )
        specs.extend(
            [
                {
                    "case_id": f"B_{mode}",
                    "condition": "B_plus4ns_current",
                    "mode": mode,
                    "config": production / a0_config_name / "runtime.xml",
                    "input": b_source,
                    "diagnostics": True,
                },
                {
                    "case_id": f"C_{mode}",
                    "condition": "C_known_physical_correction",
                    "mode": mode,
                    "config": production / f"C_{mode}" / "runtime.xml",
                    "input": corrected / mode / "prepared_inputs/A2_Known_error_correction_upper_bound/period_0000.bin",
                    "diagnostics": True,
                },
            ]
        )
    return specs


def main() -> int:
    binary = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_BINARY.resolve()
    if not binary.is_file():
        raise SystemExit(f"Production binary not found: {binary}")
    if not EXISTING_A0_ON.is_dir():
        raise SystemExit(f"Initial A0_ON run not found: {EXISTING_A0_ON}")
    matrix_root = R7 / "post_fix_matrix"
    if matrix_root.exists():
        raise SystemExit(f"Refusing to overwrite existing R7 matrix directory: {matrix_root}")
    matrix_root.mkdir(parents=True)

    revision = run_text(["git", "rev-parse", "HEAD"])
    tracked_status = run_text(["git", "status", "--short", "--untracked-files=no"])
    gpu_status = run_text(
        [
            "nvidia-smi",
            "--query-gpu=name,pstate,clocks.gr,clocks.mem,power.draw,temperature.gpu,utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader",
        ]
    )
    binary_hash = sha256(binary)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "experiment": "PGRCC Phase R7 minimal CUDA Production revalidation",
        "created_utc": iso_now(),
        "git_revision": revision,
        "tracked_worktree_status_at_start": tracked_status,
        "binary_path": str(binary),
        "binary_sha256": binary_hash,
        "gpu_status_before_matrix": gpu_status,
        "matrix_root": str(matrix_root),
        "run_command_template": [
            str(binary), "--config", "<runtime.xml>", "--result-dir", "<case>/result",
            "--track-debug-dir", "<case>/track_debug", "--track-debug-dump=on",
            "--runtime-mode=debug", "--runtime-diagnostics=<on|off>",
            "--local-test", "<period_0000.bin>",
        ],
        "runs": {},
    }
    manifest_path = matrix_root / "experiment_manifest.json"

    existing_input = (
        R6 / "reconstruction/stages/A0_ON/data/stage2_statistical_newprotocol_period_0000.bin"
    )
    existing_config = R6 / "production/A0_ON_rep1/runtime.xml"
    initial_record = validated_existing_run_record(
        EXISTING_A0_ON / "result/run_manifest.json",
        existing_config,
        existing_input,
        EXISTING_A0_ON / "result",
        revision,
        ROOT,
    )
    initial_record["note"] = (
        "Existing A0_ON output was validated against its run_manifest; binary SHA is "
        "left unverified because that run manifest did not record it. The hashed A0_ON_repeat "
        "run is used for paired numeric comparison."
    )
    manifest["runs"]["A0_ON_focused_initial"] = initial_record
    save_manifest(manifest_path, manifest)

    repeat_spec = {
        "case_id": "A0_ON_repeat",
        "condition": "A0_0ns_current_repeatability_control",
        "mode": "ON",
        "config": existing_config,
        "input": existing_input,
        "diagnostics": True,
    }
    specs = [repeat_spec] + build_case_specs()
    for spec in specs:
        case_id = str(spec["case_id"])
        case_dir = matrix_root / case_id
        if case_dir.exists():
            raise SystemExit(f"Refusing to overwrite existing case directory: {case_dir}")
        config = Path(spec["config"])
        input_path = Path(spec["input"])
        for required in (config, input_path):
            if not required.is_file() or required.stat().st_size == 0:
                raise SystemExit(f"Missing or empty required input/config: {required}")
        result_dir = case_dir / "result"
        track_dir = case_dir / "track_debug"
        result_dir.mkdir(parents=True)
        track_dir.mkdir(parents=True)
        diagnostics = bool(spec["diagnostics"])
        command = [
            str(binary), "--config", str(config), "--result-dir", str(result_dir),
            "--track-debug-dir", str(track_dir), "--track-debug-dump=on",
            "--runtime-mode=debug",
            f"--runtime-diagnostics={'on' if diagnostics else 'off'}",
            "--local-test", str(input_path),
        ]
        environment = os.environ.copy()
        environment["GMTI_CFAR_DUMP_BEAM"] = "50"
        run_record: dict[str, object] = {
            "status": "RUNNING",
            "condition": spec["condition"],
            "mode": spec["mode"],
            "command": command,
            "environment_overrides": {"GMTI_CFAR_DUMP_BEAM": "50"},
            "config_path": str(config),
            "config_sha256": sha256(config),
            "input_path": str(input_path),
            "input_bytes": input_path.stat().st_size,
            "input_sha256": sha256(input_path),
            "binary_sha256": binary_hash,
            "output_dir": str(case_dir),
            "started_utc": iso_now(),
        }
        manifest["runs"][case_id] = run_record
        save_manifest(manifest_path, manifest)
        started = time.monotonic()
        with (case_dir / "runtime.log").open("w", encoding="utf-8") as log:
            process = subprocess.run(
                command, cwd=ROOT, env=environment, stdout=log,
                stderr=subprocess.STDOUT, check=False,
            )
        run_record["elapsed_wall_seconds"] = time.monotonic() - started
        run_record["finished_utc"] = iso_now()
        run_record["exit_code"] = process.returncode
        generated = {
            "run_manifest": result_dir / "run_manifest.json",
            "runtime_config_dump": result_dir / "runtime_config_dump.json",
            "timing_metrics": result_dir / "timing_metrics.csv",
            "cfar_geometry": result_dir / "debug/cfar_geometry_diagnostics.csv",
            "cfar_numerical": result_dir / "debug/cfar_numerical_diagnostics.csv",
            "dynamic_threshold": result_dir / "debug/cfar_GMTI01_beam050_dynamic_in_band_threshold.f32",
            "full_threshold": result_dir / "debug/cfar_GMTI01_beam050_full_band_threshold.f32",
            "track_debug": track_dir,
        }
        present = {key: path.is_file() if path.is_file() else path.is_dir()
                   for key, path in generated.items()}
        run_record["required_outputs"] = {key: str(path) for key, path in generated.items()}
        run_record["required_outputs_present"] = present
        success = process.returncode == 0 and all(present.values())
        run_record["status"] = "EXECUTED" if success else "FAILED_OR_INCOMPLETE"
        save_manifest(manifest_path, manifest)
        if not success:
            print(f"{case_id}: failed/incomplete; see {case_dir / 'runtime.log'}", file=sys.stderr)
            return process.returncode or 1
        print(f"{case_id}: EXECUTED ({run_record['elapsed_wall_seconds']:.2f}s)")

    diag_off_dir = matrix_root / "A0_ON_diagoff"
    config = existing_config
    input_path = existing_input
    if diag_off_dir.exists():
        raise SystemExit(f"Refusing to overwrite existing case directory: {diag_off_dir}")
    result_dir = diag_off_dir / "result"
    track_dir = diag_off_dir / "track_debug"
    result_dir.mkdir(parents=True)
    track_dir.mkdir(parents=True)
    command = [
        str(binary), "--config", str(config), "--result-dir", str(result_dir),
        "--track-debug-dir", str(track_dir), "--track-debug-dump=on",
        "--runtime-mode=debug", "--runtime-diagnostics=off", "--local-test", str(input_path),
    ]
    environment = os.environ.copy()
    environment["GMTI_CFAR_DUMP_BEAM"] = "50"
    record = {
        "status": "RUNNING",
        "condition": "A0_0ns_current_diagnostics_off_control",
        "mode": "ON",
        "command": command,
        "environment_overrides": {"GMTI_CFAR_DUMP_BEAM": "50"},
        "config_path": str(config),
        "config_sha256": sha256(config),
        "input_path": str(input_path),
        "input_bytes": input_path.stat().st_size,
        "input_sha256": sha256(input_path),
        "binary_sha256": binary_hash,
        "output_dir": str(diag_off_dir),
        "started_utc": iso_now(),
    }
    manifest["runs"]["A0_ON_diagoff"] = record
    save_manifest(manifest_path, manifest)
    started = time.monotonic()
    with (diag_off_dir / "runtime.log").open("w", encoding="utf-8") as log:
        process = subprocess.run(command, cwd=ROOT, env=environment,
                                 stdout=log, stderr=subprocess.STDOUT, check=False)
    record["elapsed_wall_seconds"] = time.monotonic() - started
    record["finished_utc"] = iso_now()
    record["exit_code"] = process.returncode
    record["status"] = "EXECUTED" if process.returncode == 0 and (
        result_dir / "detection_results.csv").is_file() and any(track_dir.iterdir()) else "FAILED_OR_INCOMPLETE"
    save_manifest(manifest_path, manifest)
    if record["status"] != "EXECUTED":
        print(f"A0_ON_diagoff: failed/incomplete; see {diag_off_dir / 'runtime.log'}", file=sys.stderr)
        return process.returncode or 1
    print(f"A0_ON_diagoff: EXECUTED ({record['elapsed_wall_seconds']:.2f}s)")
    manifest["completed_utc"] = iso_now()
    save_manifest(manifest_path, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
