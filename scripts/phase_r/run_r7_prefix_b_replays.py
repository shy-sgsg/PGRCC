#!/usr/bin/env python3
"""Replay only the missing R7 pre-fix B (4 ns) Production controls."""

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
OUTPUT = R7 / "pre_fix_matrix"
BINARY = ROOT / "build/GMTI_pipe_core"
R6_CFAR_MANIFEST = R6 / "analysis/cfar_branch_metrics/analysis_manifest.json"
POST_FIX_MANIFEST = R7 / "post_fix_matrix/experiment_manifest.json"


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
    for path in (BINARY, R6_CFAR_MANIFEST, POST_FIX_MANIFEST):
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

    post_fix = json.loads(POST_FIX_MANIFEST.read_text(encoding="utf-8"))
    stages = R6 / "reconstruction/stages"
    production = R6 / "production"
    config_names = {"OFF": "A0_OFF", "ON": "A0_ON_rep1", "TO": "A0_TO"}
    specs = []
    for mode in ("OFF", "ON", "TO"):
        input_path = stages / f"A1_{mode}_bg_replay1/data/stage2_statistical_newprotocol_period_0000.bin"
        config_path = production / config_names[mode] / "runtime.xml"
        reference = post_fix["runs"][f"B_{mode}"]
        for path in (input_path, config_path):
            if not path.is_file() or path.stat().st_size == 0:
                raise SystemExit(f"Missing or empty required input/config: {path}")
        input_hash = sha256(input_path)
        config_hash = sha256(config_path)
        if input_hash != reference["input_sha256"]:
            raise SystemExit(f"B_{mode} input differs from paired post-fix run")
        if config_hash != reference["config_sha256"]:
            raise SystemExit(f"B_{mode} runtime XML differs from paired post-fix run")
        specs.append((mode, input_path, config_path, input_hash, config_hash))

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
        "post_fix_pairing_manifest": str(POST_FIX_MANIFEST),
        "gpu_status_before_runs": run_text(
            ["nvidia-smi", "--query-gpu=name,pstate,clocks.gr,clocks.mem,power.draw,temperature.gpu,utilization.gpu,memory.used,memory.total", "--format=csv,noheader"]
        ),
        "runs": {},
    }
    save_manifest(manifest_path, manifest)

    for mode, input_path, config_path, input_hash, config_hash in specs:
        case_id = f"B_{mode}"
        case_dir = OUTPUT / case_id
        result_dir = case_dir / "result"
        track_dir = case_dir / "track_debug"
        result_dir.mkdir(parents=True)
        track_dir.mkdir(parents=True)
        command = [
            str(BINARY), "--config", str(config_path),
            "--result-dir", str(result_dir), "--track-debug-dir", str(track_dir),
            "--track-debug-dump=on", "--runtime-mode=debug", "--runtime-diagnostics=on",
            "--local-test", str(input_path),
        ]
        environment = os.environ.copy()
        environment["GMTI_CFAR_DUMP_BEAM"] = "50"
        record = {
            "status": "RUNNING",
            "condition": "B_plus4ns_current",
            "mode": mode,
            "command": command,
            "environment_overrides": {"GMTI_CFAR_DUMP_BEAM": "50"},
            "config_path": str(config_path),
            "config_sha256": config_hash,
            "input_path": str(input_path),
            "input_bytes": input_path.stat().st_size,
            "input_sha256": input_hash,
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
            "cfar_power": result_dir / "debug/cfar_GMTI01_beam050_power.f32",
            "cfar_threshold": result_dir / "debug/cfar_GMTI01_beam050_threshold.f32",
            "csi_tap": result_dir / "debug/paired_range_phase_csi_period050_beam050.f32",
        }
        present = {name: path.is_file() for name, path in required.items()}
        present["track_debug_nonempty"] = track_dir.is_dir() and any(track_dir.iterdir())
        record["required_outputs"] = {name: str(path) for name, path in required.items()}
        record["required_outputs_present"] = present
        success = process.returncode == 0 and all(present.values())
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
