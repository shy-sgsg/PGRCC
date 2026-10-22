#!/usr/bin/env python3
"""Run the frozen R7 seed-101 CUDA Production revalidation matrix."""

from __future__ import annotations

import hashlib
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
R6 = ROOT / "outputs/phase_r6_stagewise_divergence_20261022"
R7 = ROOT / "outputs/phase_r7_cfar_correctness_20261022"
DEFAULT_BINARY = ROOT / "build_r7/GMTI_pipe_core"
DEFAULT_OUTPUT_ROOT = R7 / "post_fix_matrix_3period_final"
EXISTING_A0_ON = R7 / "post_fix_focused/A0_ON"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def period_input_files(first_input: Path, period_count: int = 3) -> list[Path]:
    """Resolve and validate consecutive `...period_NNNN.bin` inputs."""
    match = re.fullmatch(r"(.*)period_(\d{4})\.bin", first_input.name)
    if match is None:
        raise ValueError(f"input filename must end in period_NNNN.bin: {first_input}")
    if period_count <= 0:
        raise ValueError("period_count must be positive")
    prefix, first_index = match.groups()
    paths = [
        first_input.with_name(f"{prefix}period_{int(first_index) + offset:04d}.bin")
        for offset in range(period_count)
    ]
    for path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"missing or empty period input: {path}")
    return paths


def local_test_period_records(log_text: str) -> list[dict[str, object]]:
    start_pattern = re.compile(
        r"\[LOCAL-TEST\] ===== (\d+)/(\d+) echo=(.+?) result_id=(\d+) ====="
    )
    cycle_pattern = re.compile(
        r"\[FILE\]\[CYCLE\] result_file_id=(-?\d+) period_index=(\d+) "
        r".*?success=(true|false)"
    )
    records: list[dict[str, object]] = []
    pending: dict[str, object] | None = None
    for line in log_text.splitlines():
        start = start_pattern.search(line)
        if start is not None:
            pending = {
                "sequence": int(start.group(1)),
                "total": int(start.group(2)),
                "input_path": start.group(3),
                "result_id": int(start.group(4)),
            }
            continue
        cycle = cycle_pattern.search(line)
        if cycle is not None and pending is not None:
            records.append(
                {
                    **pending,
                    "cycle_result_id": int(cycle.group(1)),
                    "period_index": int(cycle.group(2)),
                    "cycle_success": cycle.group(3) == "true",
                }
            )
            pending = None
    return records


def local_test_echo_specs(input_paths: list[Path]) -> list[str]:
    """Force unique result IDs while each standalone period file is read from offset 0."""
    return [
        f"{index + 1}={path}" for index, path in enumerate(input_paths)
    ]


def diagnostic_off_required_outputs(
    result_dir: Path,
    track_dir: Path,
    runtime_xml: Path,
    runtime_log: Path,
    period_count: int = 3,
) -> dict[str, Path]:
    outputs = {
        "track_debug": track_dir,
        "runtime_xml": runtime_xml,
        "runtime_log": runtime_log,
    }
    for index in range(1, period_count + 1):
        result_id = f"GMTI{index:02d}"
        outputs[f"detection_results_{result_id}"] = (
            result_dir / f"detection_results_{result_id}.csv"
        )
    return outputs


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
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", recorded_commit, current_revision],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) if recorded_commit else None
    if not recorded_commit or ancestor is None or ancestor.returncode != 0:
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", nargs="?", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    binary = args.binary.resolve()
    matrix_root = args.output_root.resolve()
    if not binary.is_file():
        raise SystemExit(f"Production binary not found: {binary}")
    if not EXISTING_A0_ON.is_dir():
        raise SystemExit(f"Initial A0_ON run not found: {EXISTING_A0_ON}")
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
        "period_count": 3,
        "input_read_semantics": (
            "Each Stage2 period file is an independent one-period binary; local-test forces "
            "unique result IDs 1..3 and leaves the in-file period offset at its XML default 0."
        ),
        "run_command_template": [
            str(binary), "--config", "<runtime.xml>", "--result-dir", "<case>/result",
            "--track-debug-dir", "<case>/track_debug", "--track-debug-dump=on",
            "--runtime-mode=debug", "--runtime-diagnostics=<on|off>",
            "--local-test", "1=<period_0000.bin>", "2=<period_0001.bin>",
            "3=<period_0002.bin>",
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
        input_paths = period_input_files(Path(spec["input"]))
        for required in (config, *input_paths):
            if not required.is_file() or required.stat().st_size == 0:
                raise SystemExit(f"Missing or empty required input/config: {required}")
        result_dir = case_dir / "result"
        track_dir = case_dir / "track_debug"
        case_dir.mkdir(parents=True)
        result_dir.mkdir(parents=True)
        track_dir.mkdir(parents=True)
        frozen_config = case_dir / "runtime.xml"
        shutil.copy2(config, frozen_config)
        if sha256(config) != sha256(frozen_config):
            raise RuntimeError(f"Frozen runtime XML differs from source: {config}")
        diagnostics = bool(spec["diagnostics"])
        command = [
            str(binary), "--config", str(frozen_config), "--result-dir", str(result_dir),
            "--track-debug-dir", str(track_dir), "--track-debug-dump=on",
            "--runtime-mode=debug",
            f"--runtime-diagnostics={'on' if diagnostics else 'off'}",
            "--local-test", *local_test_echo_specs(input_paths),
        ]
        environment = os.environ.copy()
        environment["GMTI_CFAR_DUMP_BEAM"] = "50"
        run_record: dict[str, object] = {
            "status": "RUNNING",
            "condition": spec["condition"],
            "mode": spec["mode"],
            "command": command,
            "environment_overrides": {"GMTI_CFAR_DUMP_BEAM": "50"},
            "config_path": str(config.resolve()),
            "runtime_xml_path": str(frozen_config.resolve()),
            "config_sha256": sha256(config),
            "input_path": str(input_paths[0].resolve()),
            "input_bytes": sum(path.stat().st_size for path in input_paths),
            "input_periods": [
                {
                    "period_index": index,
                    "path": str(path.resolve()),
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
                for index, path in enumerate(input_paths)
            ],
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
            "cluster_detection_lineage": result_dir / "debug/cluster_detection_lineage.csv",
            "csi_metrics": result_dir / "csi_metrics",
            "track_debug": track_dir,
            "runtime_xml": frozen_config,
            "runtime_log": case_dir / "runtime.log",
        }
        for index in range(1, len(input_paths) + 1):
            result_id = f"GMTI{index:02d}"
            generated[f"detection_results_{result_id}"] = (
                result_dir / f"detection_results_{result_id}.csv"
            )
            generated[f"dynamic_threshold_{result_id}"] = (
                result_dir / f"debug/cfar_{result_id}_beam050_dynamic_in_band_threshold.f32"
            )
            generated[f"full_threshold_{result_id}"] = (
                result_dir / f"debug/cfar_{result_id}_beam050_full_band_threshold.f32"
            )
        present = {key: path.is_file() if path.is_file() else path.is_dir()
                   for key, path in generated.items()}
        run_record["required_outputs"] = {key: str(path) for key, path in generated.items()}
        run_record["required_outputs_present"] = present
        log_text = (case_dir / "runtime.log").read_text(encoding="utf-8", errors="replace")
        observed_periods = local_test_period_records(log_text)
        expected_sequence = list(range(1, len(input_paths) + 1))
        period_sequence_valid = (
            [int(row["sequence"]) for row in observed_periods] == expected_sequence
            and all(int(row["total"]) == len(input_paths) for row in observed_periods)
            and [Path(str(row["input_path"])).resolve() for row in observed_periods]
                == [path.resolve() for path in input_paths]
            and [int(row["result_id"]) for row in observed_periods] == expected_sequence
            and [int(row["cycle_result_id"]) for row in observed_periods]
                == expected_sequence
            and [int(row["period_index"]) for row in observed_periods]
                == list(range(len(input_paths)))
            and all(row["cycle_success"] is True for row in observed_periods)
        )
        run_record["period_run_records"] = observed_periods
        run_record["period_sequence_status"] = (
            "evaluable" if period_sequence_valid else "INCOMPLETE_OR_MISMATCHED"
        )
        success = process.returncode == 0 and all(present.values()) and period_sequence_valid
        run_record["status"] = "EXECUTED" if success else "FAILED_OR_INCOMPLETE"
        save_manifest(manifest_path, manifest)
        if not success:
            print(f"{case_id}: failed/incomplete; see {case_dir / 'runtime.log'}", file=sys.stderr)
            return process.returncode or 1
        print(f"{case_id}: EXECUTED ({run_record['elapsed_wall_seconds']:.2f}s)")

    diag_off_dir = matrix_root / "A0_ON_diagoff"
    config = existing_config
    input_paths = period_input_files(existing_input)
    if diag_off_dir.exists():
        raise SystemExit(f"Refusing to overwrite existing case directory: {diag_off_dir}")
    result_dir = diag_off_dir / "result"
    track_dir = diag_off_dir / "track_debug"
    diag_off_dir.mkdir(parents=True)
    result_dir.mkdir(parents=True)
    track_dir.mkdir(parents=True)
    frozen_config = diag_off_dir / "runtime.xml"
    shutil.copy2(config, frozen_config)
    if sha256(config) != sha256(frozen_config):
        raise RuntimeError(f"Frozen diagnostics-off runtime XML differs from source: {config}")
    command = [
        str(binary), "--config", str(frozen_config), "--result-dir", str(result_dir),
        "--track-debug-dir", str(track_dir), "--track-debug-dump=on",
        "--runtime-mode=debug", "--runtime-diagnostics=off",
        "--local-test", *local_test_echo_specs(input_paths),
    ]
    environment = os.environ.copy()
    environment["GMTI_CFAR_DUMP_BEAM"] = "50"
    record = {
        "status": "RUNNING",
        "condition": "A0_0ns_current_diagnostics_off_control",
        "mode": "ON",
        "command": command,
        "environment_overrides": {"GMTI_CFAR_DUMP_BEAM": "50"},
        "config_path": str(config.resolve()),
        "runtime_xml_path": str(frozen_config.resolve()),
        "config_sha256": sha256(config),
        "input_path": str(input_paths[0].resolve()),
        "input_bytes": sum(path.stat().st_size for path in input_paths),
        "input_periods": [
            {
                "period_index": index,
                "path": str(path.resolve()),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for index, path in enumerate(input_paths)
        ],
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
    diagnostic_off_outputs = diagnostic_off_required_outputs(
        result_dir, track_dir, frozen_config, diag_off_dir / "runtime.log",
        period_count=len(input_paths),
    )
    diagnostic_off_present = {
        key: path.is_file() if path.is_file() else path.is_dir()
        for key, path in diagnostic_off_outputs.items()
    }
    record["required_outputs"] = {
        key: str(path) for key, path in diagnostic_off_outputs.items()
    }
    record["required_outputs_present"] = diagnostic_off_present
    diag_off_log = (diag_off_dir / "runtime.log").read_text(
        encoding="utf-8", errors="replace",
    )
    observed_periods = local_test_period_records(diag_off_log)
    expected_sequence = list(range(1, len(input_paths) + 1))
    period_sequence_valid = (
        [int(row["sequence"]) for row in observed_periods] == expected_sequence
        and all(int(row["total"]) == len(input_paths) for row in observed_periods)
        and [Path(str(row["input_path"])).resolve() for row in observed_periods]
            == [path.resolve() for path in input_paths]
        and [int(row["result_id"]) for row in observed_periods] == expected_sequence
        and [int(row["cycle_result_id"]) for row in observed_periods]
            == expected_sequence
        and [int(row["period_index"]) for row in observed_periods]
            == list(range(len(input_paths)))
        and all(row["cycle_success"] is True for row in observed_periods)
    )
    record["period_run_records"] = observed_periods
    record["period_sequence_status"] = (
        "evaluable" if period_sequence_valid else "INCOMPLETE_OR_MISMATCHED"
    )
    record["lineage_absent_with_diagnostics_off"] = not (
        result_dir / "debug/cluster_detection_lineage.csv"
    ).exists()
    success = (
        process.returncode == 0
        and all(diagnostic_off_present.values())
        and period_sequence_valid
        and record["lineage_absent_with_diagnostics_off"]
    )
    record["status"] = "EXECUTED" if success else "FAILED_OR_INCOMPLETE"
    save_manifest(manifest_path, manifest)
    if not success:
        print(f"A0_ON_diagoff: failed/incomplete; see {diag_off_dir / 'runtime.log'}", file=sys.stderr)
        return process.returncode or 1
    print(f"A0_ON_diagoff: EXECUTED ({record['elapsed_wall_seconds']:.2f}s)")
    manifest["completed_utc"] = iso_now()
    save_manifest(manifest_path, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
