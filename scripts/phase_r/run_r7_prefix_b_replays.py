#!/usr/bin/env python3
"""Replay only the missing R7 pre-fix B (4 ns) Production controls."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.phase_r.run_r7_minimal_production_matrix import (
    local_test_echo_specs,
    local_test_period_records,
    period_input_files,
)


ROOT = Path(__file__).resolve().parents[2]
R6 = ROOT / "outputs/phase_r6_stagewise_divergence_20261022"
R7 = ROOT / "outputs/phase_r7_cfar_correctness_20261022"
OUTPUT = R7 / "pre_fix_matrix_3period_id_only"
BINARY = ROOT / "build/GMTI_pipe_core"
R6_CFAR_MANIFEST = R6 / "analysis/cfar_branch_metrics/analysis_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_text(command: list[str]) -> str:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return (result.stdout + result.stderr).strip()
    return (result.stdout + result.stderr).strip()


def save_manifest(path: Path, manifest: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit(f"Refusing to overwrite existing output directory: {OUTPUT}")
    for path in (BINARY, R6_CFAR_MANIFEST):
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit(f"Missing or empty required file: {path}")

    binary_hash = sha256(BINARY)
    expected_binary_hash = json.loads(R6_CFAR_MANIFEST.read_text(encoding="utf-8"))[
        "production_executable_sha256"
    ]
    if binary_hash != expected_binary_hash:
        raise SystemExit(
            f"Legacy executable hash mismatch: expected {expected_binary_hash}, got {binary_hash}"
        )

    stages = R6 / "reconstruction/stages"
    production = R6 / "production"
    config_names = {"OFF": "A0_OFF", "ON": "A0_ON_rep1", "TO": "A0_TO"}
    specs = []
    for mode in ("OFF", "ON", "TO"):
        input_path = stages / f"A1_{mode}_bg_replay1/data/stage2_statistical_newprotocol_period_0000.bin"
        config_path = production / config_names[mode] / "runtime.xml"
        input_paths = period_input_files(input_path)
        for path in (*input_paths, config_path):
            if not path.is_file() or path.stat().st_size == 0:
                raise SystemExit(f"Missing or empty required input/config: {path}")
        config_hash = sha256(config_path)
        input_records = [
            {
                "period_index": index,
                "path": str(path.resolve()),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for index, path in enumerate(input_paths)
        ]
        specs.append((mode, input_paths, config_path, input_records, config_hash))

    OUTPUT.mkdir(parents=True)
    manifest_path = OUTPUT / "experiment_manifest.json"
    manifest = {
        "schema_version": 1,
        "experiment": "PGRCC Phase R7 pre-fix B (+4 ns) CUDA Production replay",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_revision": run_text(["git", "rev-parse", "HEAD"]),
        "tracked_worktree_status_at_start": run_text(
            ["git", "status", "--short", "--untracked-files=no"]
        ),
        "binary_path": str(BINARY),
        "binary_sha256": binary_hash,
        "binary_identity_source": str(R6_CFAR_MANIFEST),
        "pairing_note": "Inputs/configs are recorded per period; post-fix hash pairing is checked after the repaired matrix completes.",
        "period_count": 3,
        "input_read_semantics": (
            "Each Stage2 period file is an independent one-period binary; local-test forces "
            "unique result IDs 1..3 and leaves the in-file period offset at its XML default 0."
        ),
        "gpu_status_before_runs": run_text(
            ["nvidia-smi", "--query-gpu=name,pstate,clocks.gr,clocks.mem,power.draw,temperature.gpu,utilization.gpu,memory.used,memory.total", "--format=csv,noheader"]
        ),
        "runs": {},
    }
    save_manifest(manifest_path, manifest)

    for mode, input_paths, config_path, input_records, config_hash in specs:
        case_id = f"B_{mode}"
        case_dir = OUTPUT / case_id
        result_dir = case_dir / "result"
        track_dir = case_dir / "track_debug"
        case_dir.mkdir(parents=True)
        result_dir.mkdir(parents=True)
        track_dir.mkdir(parents=True)
        frozen_config = case_dir / "runtime.xml"
        shutil.copy2(config_path, frozen_config)
        if sha256(config_path) != sha256(frozen_config):
            raise RuntimeError(f"Frozen runtime XML differs from source: {config_path}")
        command = [
            str(BINARY), "--config", str(frozen_config),
            "--result-dir", str(result_dir), "--track-debug-dir", str(track_dir),
            "--track-debug-dump=on", "--runtime-mode=debug", "--runtime-diagnostics=on",
            "--local-test", *local_test_echo_specs(input_paths),
        ]
        environment = os.environ.copy()
        environment["GMTI_CFAR_DUMP_BEAM"] = "50"
        record = {
            "status": "RUNNING",
            "condition": "B_plus4ns_current",
            "mode": mode,
            "command": command,
            "environment_overrides": {"GMTI_CFAR_DUMP_BEAM": "50"},
            "config_path": str(config_path.resolve()),
            "runtime_xml_path": str(frozen_config.resolve()),
            "config_sha256": config_hash,
            "input_path": input_records[0]["path"],
            "input_bytes": sum(int(row["bytes"]) for row in input_records),
            "input_periods": input_records,
            "binary_sha256": binary_hash,
            "output_dir": str(case_dir),
            "started_utc": datetime.now(timezone.utc).isoformat(),
        }
        manifest["runs"][case_id] = record
        save_manifest(manifest_path, manifest)

        started = time.monotonic()
        with (case_dir / "runtime.log").open("w", encoding="utf-8") as log:
            process = subprocess.run(
                command, cwd=ROOT, env=environment, stdout=log,
                stderr=subprocess.STDOUT, check=False,
            )
        record["elapsed_wall_seconds"] = time.monotonic() - started
        record["finished_utc"] = datetime.now(timezone.utc).isoformat()
        record["exit_code"] = process.returncode
        required = {
            "run_manifest": result_dir / "run_manifest.json",
            "runtime_config_dump": result_dir / "runtime_config_dump.json",
            "timing_metrics": result_dir / "timing_metrics.csv",
            "cfar_geometry": result_dir / "debug/cfar_geometry_diagnostics.csv",
            "csi_tap": result_dir / "debug/paired_range_phase_csi_period050_beam050.f32",
        }
        for index in range(1, len(input_paths) + 1):
            result_id = f"GMTI{index:02d}"
            required[f"detection_results_{result_id}"] = (
                result_dir / f"detection_results_{result_id}.csv"
            )
            required[f"cfar_power_{result_id}"] = (
                result_dir / f"debug/cfar_{result_id}_beam050_power.f32"
            )
            required[f"cfar_threshold_{result_id}"] = (
                result_dir / f"debug/cfar_{result_id}_beam050_threshold.f32"
            )
        present = {name: path.is_file() for name, path in required.items()}
        present["track_debug_nonempty"] = track_dir.is_dir() and any(track_dir.iterdir())
        record["required_outputs"] = {name: str(path) for name, path in required.items()}
        record["required_outputs_present"] = present
        log_text = (case_dir / "runtime.log").read_text(
            encoding="utf-8", errors="replace"
        )
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
        record["period_run_records"] = observed_periods
        record["period_sequence_status"] = (
            "evaluable" if period_sequence_valid else "INCOMPLETE_OR_MISMATCHED"
        )
        success = (
            process.returncode == 0 and all(present.values()) and period_sequence_valid
        )
        record["status"] = "EXECUTED" if success else "FAILED_OR_INCOMPLETE"
        save_manifest(manifest_path, manifest)
        print(f"{case_id}: {record['status']} (exit={process.returncode}, {record['elapsed_wall_seconds']:.2f}s)")
        if not success:
            print(f"Inspect {case_dir / 'runtime.log'}", file=sys.stderr)
            return process.returncode or 1

    manifest["gpu_status_after_runs"] = run_text(
        ["nvidia-smi", "--query-gpu=name,pstate,clocks.gr,clocks.mem,power.draw,temperature.gpu,utilization.gpu,memory.used,memory.total", "--format=csv,noheader"]
    )
    manifest["completed_utc"] = datetime.now(timezone.utc).isoformat()
    save_manifest(manifest_path, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
