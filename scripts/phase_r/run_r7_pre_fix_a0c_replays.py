#!/usr/bin/env python3
"""Add the missing same-period pre-fix A0/C runs to the R7 manifest."""

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
MANIFEST_PATH = OUTPUT / "experiment_manifest.json"
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
    return (result.stdout + result.stderr).strip()


def save_manifest(manifest: dict) -> None:
    temporary = MANIFEST_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(MANIFEST_PATH)


def main() -> int:
    if not OUTPUT.is_dir() or not MANIFEST_PATH.is_file():
        raise SystemExit(f"Existing B pre-fix replay manifest not found: {MANIFEST_PATH}")
    for path in (BINARY, R6_CFAR_MANIFEST, POST_FIX_MANIFEST):
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit(f"Missing or empty required file: {path}")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    binary_hash = sha256(BINARY)
    expected_hash = json.loads(R6_CFAR_MANIFEST.read_text(encoding="utf-8"))[
        "production_executable_sha256"
    ]
    if binary_hash != expected_hash or manifest.get("binary_sha256") != binary_hash:
        raise SystemExit("Legacy executable identity does not match the R6 and existing R7 manifests")
    for case_id in ("B_OFF", "B_ON", "B_TO"):
        record = manifest.get("runs", {}).get(case_id, {})
        if record.get("status") != "EXECUTED" or record.get("exit_code") != 0:
            raise SystemExit(f"Existing {case_id} replay is incomplete; refusing to extend manifest")

    post_fix = json.loads(POST_FIX_MANIFEST.read_text(encoding="utf-8"))
    stages = R6 / "reconstruction/stages"
    corrected = R6 / "reconstruction/corrected_C"
    production = R6 / "production"
    specs = []
    for mode in ("OFF", "ON", "TO"):
        config_name = "A0_ON_rep1" if mode == "ON" else f"A0_{mode}"
        input_name = f"A0_{mode}" if mode == "ON" else f"A0_{mode}_bg_replay1"
        a0_input = stages / input_name / "data/stage2_statistical_newprotocol_period_0000.bin"
        a0_config = production / config_name / "runtime.xml"
        c_input = corrected / mode / "prepared_inputs/A2_Known_error_correction_upper_bound/period_0000.bin"
        c_config = production / f"C_{mode}" / "runtime.xml"
        specs.extend(((f"A0_{mode}", a0_input, a0_config), (f"C_{mode}", c_input, c_config)))

    manifest["pre_fix_a0c_scope_note"] = (
        "R6 production output manifests point to period_0002.bin; those outputs were not reused. "
        "These A0/C runs use the same period_0000.bin and runtime XML hashes as the paired post-fix runs."
    )
    manifest["gpu_status_before_a0c_runs"] = run_text(
        ["nvidia-smi", "--query-gpu=name,pstate,clocks.gr,clocks.mem,power.draw,temperature.gpu,utilization.gpu,memory.used,memory.total", "--format=csv,noheader"]
    )
    save_manifest(manifest)

    for case_id, input_path, config_path in specs:
        if case_id in manifest["runs"]:
            existing = manifest["runs"][case_id]
            if existing.get("status") == "EXECUTED" and existing.get("exit_code") == 0:
                continue
            raise SystemExit(f"Existing incomplete case record blocks replay: {case_id}")
        case_dir = OUTPUT / case_id
        if case_dir.exists():
            raise SystemExit(f"Refusing to overwrite unmanifested case directory: {case_dir}")
        for path in (input_path, config_path):
            if not path.is_file() or path.stat().st_size == 0:
                raise SystemExit(f"Missing or empty required input/config: {path}")

        post_id = "A0_ON_repeat" if case_id == "A0_ON" else case_id
        paired = post_fix["runs"][post_id]
        input_hash = sha256(input_path)
        config_hash = sha256(config_path)
        if input_hash != paired["input_sha256"]:
            raise SystemExit(f"{case_id} input is not paired with repaired Production")
        if config_hash != paired["config_sha256"]:
            raise SystemExit(f"{case_id} runtime XML is not paired with repaired Production")

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
            "condition": "A0_0ns_current" if case_id.startswith("A0_") else "C_known_physical_correction",
            "mode": case_id.split("_")[1],
            "command": command,
            "environment_overrides": {"GMTI_CFAR_DUMP_BEAM": "50"},
            "config_path": str(config_path),
            "config_sha256": config_hash,
            "input_path": str(input_path),
            "input_bytes": input_path.stat().st_size,
            "input_sha256": input_hash,
            "binary_sha256": binary_hash,
            "paired_post_fix_case": post_id,
            "output_dir": str(case_dir),
            "started_utc": datetime.now(timezone.utc).isoformat(),
        }
        manifest["runs"][case_id] = record
        save_manifest(manifest)

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
        save_manifest(manifest)
        print(f"{case_id}: {record['status']} (exit={process.returncode}, {record['elapsed_wall_seconds']:.2f}s)")
        if not success:
            print(f"Inspect {case_dir / 'runtime.log'}", file=sys.stderr)
            return process.returncode or 1

    manifest["gpu_status_after_a0c_runs"] = run_text(
        ["nvidia-smi", "--query-gpu=name,pstate,clocks.gr,clocks.mem,power.draw,temperature.gpu,utilization.gpu,memory.used,memory.total", "--format=csv,noheader"]
    )
    manifest["completed_utc"] = datetime.now(timezone.utc).isoformat()
    save_manifest(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
