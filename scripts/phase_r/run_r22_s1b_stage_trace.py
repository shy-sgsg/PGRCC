#!/usr/bin/env python3
"""Replay the frozen R22 SNR30 C4 Mode-A roles with selected stage tracing.

This is a local causal diagnostic only. It reuses the already generated Stage2
period files, keeps the archived Mode-A XML overrides, and does not make a
task-performance claim or restart the R22 matrix.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TRACE_SOURCE_FILES = (
    "include/GMTIProcessor.hpp",
    "include/runtime_diagnostics.hpp",
    "src/runtime_diagnostics.cpp",
    "src/processOnePeriod.cpp",
    "src/gpu/gpu_kernels.cu",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def write_manifest(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pipe-binary", type=Path, required=True)
    parser.add_argument(
        "--simulator-binary", type=Path,
        default=ROOT / "build/simulate_stage2_statistical",
    )
    parser.add_argument(
        "--selector", default="5,50,52,1065,1197",
        help="period,beam,azimuth,range_start,range_end",
    )
    args = parser.parse_args()

    case_root = args.case_root.resolve()
    output_root = args.output_root.resolve()
    pipe_binary = args.pipe_binary.resolve()
    simulator_binary = args.simulator_binary.resolve()
    if output_root.exists():
        parser.error(f"refusing to overwrite existing output root: {output_root}")
    if not pipe_binary.is_file() or not simulator_binary.is_file():
        parser.error("Production binary or Stage2 simulator does not exist")

    case_manifest_path = case_root / "case_manifest.json"
    case_manifest = json.loads(case_manifest_path.read_text(encoding="utf-8"))
    layout = case_manifest["protocol_layout"]
    source_on_manifest = case_root / "production/C4_modea_ON/branch_manifest.json"
    source_on = json.loads(source_on_manifest.read_text(encoding="utf-8"))
    period_count = int(source_on["period_count_expected"])
    if period_count != 10:
        parser.error(f"unexpected frozen S1b period count: {period_count}")

    from scripts import run_delay_stage1_formal as stage1
    from scripts import run_track_manager_e2e as e2e

    stage1.SIMULATOR = simulator_binary
    e2e.PIPE = pipe_binary
    os.environ["PGRCC_C4_TRACE_GROUP"] = args.selector

    device = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,pstate,temperature.gpu,power.draw,"
         "utilization.gpu,memory.used,memory.total,clocks.sm", "--format=csv"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "task": "R22-S1b selected production stage-boundary causal diagnostic",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "repository": str(ROOT),
        "git_commit": git_output("rev-parse", "HEAD"),
        "worktree_status": git_output("status", "--short", "--untracked-files=no"),
        "trace_source_sha256": {
            relative: sha256(ROOT / relative) for relative in TRACE_SOURCE_FILES
        },
        "production_binary": str(pipe_binary),
        "production_binary_sha256": sha256(pipe_binary),
        "stage2_simulator": str(simulator_binary),
        "stage2_regenerated": False,
        "case_root": str(case_root),
        "case_manifest_sha256": sha256(case_manifest_path),
        "selector": args.selector,
        "output_root": str(output_root),
        "period_count": period_count,
        "beam_id": int(layout["beam_id"]),
        "roles": ["ON", "OFF", "TO"],
        "purpose_boundary": (
            "Mode-A reference-assisted paired diagnostic; not a blind-method "
            "deployment comparison and not a task-performance ranking"
        ),
        "truth_used_in_estimator": False,
        "gpu_snapshot_before_replay": device.stdout.strip(),
        "gpu_snapshot_returncode": device.returncode,
        "branches": [],
    }
    manifest_path = output_root / "s1b_manifest.json"
    write_manifest(manifest_path, manifest)

    try:
        for role in ("ON", "OFF", "TO"):
            source_branch_root = case_root / "production" / f"C4_modea_{role}"
            source_branch = json.loads(
                (source_branch_root / "branch_manifest.json").read_text(encoding="utf-8")
            )
            if source_branch.get("status") != "passed":
                raise RuntimeError(f"source branch was not passed: C4_modea_{role}")
            source_xml = case_root / "method_xml" / f"C4_Mode-A_{role}.xml"
            input_path = case_root / "production_inputs" / "C4" / "Mode-A" / role
            source_periods = stage1._input_periods(input_path)
            if len(source_periods) != period_count:
                raise RuntimeError(
                    f"{role}: expected {period_count} existing inputs, got {len(source_periods)}"
                )
            branch = stage1.run_production_branch(
                output_root,
                f"C4_modea_S1b_{role}",
                input_path,
                source_xml,
                {},
                period_count,
                "local",
                layout,
                xml_overrides=source_branch["xml_overrides"],
            )
            manifest["branches"].append({
                "role": role,
                "source_branch_manifest": str(source_branch_root / "branch_manifest.json"),
                "source_branch_manifest_sha256": sha256(
                    source_branch_root / "branch_manifest.json"
                ),
                "source_xml_sha256": sha256(source_xml),
                "input_periods": branch.get("period_paths", []),
                "status": branch.get("status"),
                "production_returncode": branch.get("production_returncode"),
                "output_branch_manifest": str(
                    output_root / "production" / f"C4_modea_S1b_{role}" /
                    "branch_manifest.json"
                ),
            })
            write_manifest(manifest_path, manifest)
            if branch.get("status") != "passed" or branch.get("production_returncode") != 0:
                raise RuntimeError(
                    f"S1b production branch failed closed for {role}: "
                    f"status={branch.get('status')} "
                    f"returncode={branch.get('production_returncode')}"
                )
    except Exception as exc:  # retain partial branch evidence and exact failure
        manifest["run_status"] = "failed"
        manifest["failure"] = f"{type(exc).__name__}: {exc}"
        write_manifest(manifest_path, manifest)
        raise

    passed = all(
        item.get("status") == "passed" and item.get("production_returncode") == 0
        for item in manifest["branches"]
    ) and len(manifest["branches"]) == 3
    manifest["run_status"] = "passed" if passed else "incomplete_or_failed"
    write_manifest(manifest_path, manifest)
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
