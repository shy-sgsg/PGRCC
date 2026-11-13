#!/usr/bin/env python3
"""Run a matched ON/OFF/TO period-5 Native CUDA Production diagnostic."""

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

from scripts.phase_r import run_r22_s1c_period5_shared_off_phase as s1c
from scripts.phase_r import run_r22_s1d_phase_vector_crossover as s1d

DEFAULT_SOURCE_ROOT = ROOT / "outputs/r22_s1_c4_support_replay_20261029"
DEFAULT_OUTPUT_ROOT = ROOT / "outputs/r22_s1e_current_modeb_role_triplet_20261031"
DEFAULT_PIPE = Path("/tmp/pgrcc_r22_s1d_cuda_20261031/GMTI_pipe_core")
DEFAULT_SIMULATOR = ROOT / "build/simulate_stage2_statistical"
CASE_RELATIVE = Path(
    "snr30/pipeline/cases/"
    "r22_multi_target__seed_1932001__v_2d5__snr_30__delay_4ns"
)
ROLES = ("ON", "OFF", "TO")
SOURCE_PERIOD_INDEX = 4
SELECTED_GROUP = {"beam_id": 50, "az_index": 52, "range_start": 1065, "range_end": 1197}
FROZEN = {
    "research_calibration_enable": "true",
    "research_calibration_method": "robust_ddc_rb",
    "research_calibration_min_support": 8,
    "research_calibration_range_band_bins": 133,
    "research_calibration_robust_phase_threshold_rad": 0.35,
    "research_calibration_mode": "Mode-B",
    "research_calibration_reference_gamma_csv": "",
    "csi_metrics_enable": "true",
    "csi_metrics_dump_power_maps": "false",
    "csi_metrics_dump_intermediate_maps": "false",
    "csi_metrics_beam_id": 50,
    "rg_st": 666,
    "rg_ed": 1197,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    )
    return result.stdout.strip()


def gpu_snapshot() -> str:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,pstate,temperature.gpu,power.draw,"
         "utilization.gpu,memory.used,memory.total,clocks.sm", "--format=csv"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--pipe-binary", type=Path, default=DEFAULT_PIPE)
    parser.add_argument("--simulator-binary", type=Path, default=DEFAULT_SIMULATOR)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    pipe = args.pipe_binary.resolve()
    simulator = args.simulator_binary.resolve()
    case_root = source_root / CASE_RELATIVE
    case_manifest_path = case_root / "case_manifest.json"
    if output_root.exists():
        parser.error(f"refusing to reuse or overwrite output root: {output_root}")
    if not case_manifest_path.is_file() or not pipe.is_file() or not simulator.is_file():
        parser.error("case manifest, Production binary, or required simulator binary is missing")

    cmake_cache = pipe.parent / "CMakeCache.txt"
    if not cmake_cache.is_file():
        parser.error(f"Production CMake cache is missing: {cmake_cache}")
    cache_text = cmake_cache.read_text(errors="replace")
    if "USE_COREX:BOOL=OFF" not in cache_text or "CMAKE_BUILD_TYPE:STRING=Release" not in cache_text:
        parser.error("Production build is not confirmed as native CUDA Release")
    if "CMAKE_CUDA_ARCHITECTURES" not in cache_text:
        parser.error("Production build cache lacks CUDA architecture identity")

    case_manifest = json.loads(case_manifest_path.read_text(encoding="utf-8"))
    condition = case_manifest.get("case", {})
    expected_condition = {
        "seed": 1932001, "target_velocity_mps": 2.5,
        "target_snr_db": 30.0, "delay_error_ns": 4.0,
        "target_count": 20, "period_count": 10,
    }
    for key, expected in expected_condition.items():
        if condition.get(key) != expected:
            parser.error(f"case identity mismatch for {key}: {condition.get(key)!r}")

    layout = case_manifest["protocol_layout"]
    source_periods: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        identity = case_manifest["inputs"][role][SOURCE_PERIOD_INDEX]
        path = Path(identity["path"]).resolve()
        if not path.is_file() or path.stat().st_size != int(identity["bytes"]):
            parser.error(f"missing or wrong-sized {role} period-5 input: {path}")
        actual_sha = sha256(path)
        if actual_sha != identity["sha256"]:
            parser.error(f"{role} period-5 input SHA mismatch: {path}")
        source_periods[role] = {
            "path": str(path), "bytes": path.stat().st_size,
            "sha256": actual_sha, "case_manifest_identity": identity,
        }

    method_xml = case_root / "method_xml/C4_Mode-B_ON.xml"
    if not method_xml.is_file():
        parser.error(f"frozen Mode-B source XML is missing: {method_xml}")
    overrides: dict[str, Any] = dict(FROZEN)
    from scripts import run_delay_stage1_formal as stage1
    from scripts import run_track_manager_e2e as e2e

    for expected_mode in ("Mode-B",):
        s1c.configure_preflight(method_xml, overrides, layout, expected_mode)

    output_root.mkdir(parents=True, exist_ok=False)
    shutil_copy = output_root / Path(__file__).name
    shutil_copy.write_bytes(Path(__file__).read_bytes())
    manifest_path = output_root / "s1e_manifest.json"
    source_files = (
        "scripts/phase_r/run_r22_s1e_modeb_role_triplet.py",
        "scripts/phase_r/run_r22_s1c_period5_shared_off_phase.py",
        "scripts/phase_r/run_r22_s1d_phase_vector_crossover.py",
        "scripts/run_delay_stage1_formal.py",
        "scripts/run_track_manager_e2e.py",
        "include/GMTIProcessor.hpp", "include/runtime_diagnostics.hpp",
        "src/runtime_diagnostics.cpp", "src/processOnePeriod.cpp",
        "src/production_calibration_adapter.cpp", "src/gpu/gpu_kernels.cu",
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "task": "R22-S1e matched ON/OFF/TO role triplet through current Mode-B Production",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "repository": str(ROOT), "git_commit": git("rev-parse", "HEAD"),
        "git_tracked_status_at_execution": git("status", "--short", "--untracked-files=no"),
        "runner_worktree_status_at_execution": git(
            "status", "--short", "--untracked-files=all", "--",
            "scripts/phase_r/run_r22_s1e_modeb_role_triplet.py",
        ),
        "runner_path": str(Path(__file__).resolve()),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "source_hashes": {name: sha256(ROOT / name) for name in source_files},
        "source_root": str(source_root), "case_manifest": str(case_manifest_path),
        "case_manifest_sha256": sha256(case_manifest_path),
        "condition": condition, "protocol_layout": layout,
        "source_period_index_zero_based": SOURCE_PERIOD_INDEX,
        "source_period_label": "GMTI05", "production_period_id": 1,
        "selected_group": SELECTED_GROUP,
        "role_input_identities": source_periods,
        "paired_roles": {
            "ON": "target + clutter + noise",
            "OFF": "clutter + noise",
            "TO": "target-only signal; Stage2 signal_only path clears packet payload before target insertion",
        },
        "production_config_source_xml": str(method_xml),
        "production_config_source_xml_sha256": sha256(method_xml),
        "frozen_xml_overrides": overrides,
        "truth_used_in_estimator": False,
        "truth_use_policy": "no truth path is passed to Production; truth may only be used after execution for evaluator-side spatial interpretation",
        "frozen_quality_gates": {
            "range_group_bins": 133, "min_support": 8,
            "phase_residual_rad": 0.35, "fallback_to_current": False,
        },
        "production_binary": str(pipe), "production_binary_sha256": sha256(pipe),
        "production_cmake_cache": str(cmake_cache),
        "production_cmake_cache_sha256": sha256(cmake_cache),
        "production_build": {
            "type": "Release", "native_cuda": "USE_COREX=OFF",
            "cuda_architectures": [line.split("=", 1)[1] for line in cache_text.splitlines()
                                   if line.startswith("CMAKE_CUDA_ARCHITECTURES") and "=" in line],
            "source_commit": "461fbd949ea7c024c28f30e7aea36ae9642a4456",
        },
        "simulator_binary": str(simulator), "simulator_used": False,
        "stage2_regenerated": False, "gpu_snapshot_before": gpu_snapshot(),
        "output_root": str(output_root), "branches": [], "run_status": "RUNNING",
    }
    write_json(manifest_path, manifest)
    stage1.SIMULATOR = simulator
    e2e.PIPE = pipe
    os.environ["PGRCC_C4_TRACE_GROUP"] = s1c.TRACE_SELECTOR

    try:
        for role in ROLES:
            input_dir = output_root / "inputs" / role
            input_dir.mkdir(parents=True, exist_ok=False)
            (input_dir / "period_0000.bin").symlink_to(source_periods[role]["path"])
            branch_name = f"C4_modeb_S1e_{role.lower()}"
            before = gpu_snapshot()
            record = stage1.run_production_branch(
                output_root, branch_name, input_dir, method_xml, {}, 1, "local",
                layout, xml_overrides=overrides,
            )
            branch_root = output_root / "production" / branch_name
            phase_vectors: dict[str, Any] = {}
            for phase in ("raw", "csi"):
                vector = branch_root / "result/debug" / s1c.PHASE_FILENAME.format(stage=phase)
                phase_vectors[phase] = s1c.read_phase_vector(vector) if vector.is_file() else None
            result: dict[str, Any] = {
                "role": role, "branch_name": branch_name,
                "input_path": source_periods[role]["path"],
                "input_sha256": source_periods[role]["sha256"],
                "gpu_snapshot_before": before,
                "production_status": record.get("status"),
                "production_returncode": record.get("production_returncode"),
                "production_elapsed_sec": record.get("production_elapsed_sec"),
                "production_xml": record.get("production_xml"),
                "production_log": record.get("production_log"),
                "branch_manifest": str((branch_root / "branch_manifest.json").resolve()),
                "xml_overrides": overrides, "phase_vectors": phase_vectors,
                "record": record,
            }
            try:
                gamma, cells = s1d.selected_group_rows(branch_root)
                support, coherence, diagnostics = s1d.recompute_membership(cells, 0.35)
                result["selected_group"] = {
                    "gamma_row": gamma, "cell_count": len(cells),
                    "support_recomputed": support,
                    "phase_coherence_recomputed": coherence,
                    "support_matches_production": support == int(gamma["support_count"]),
                    "diagnostics": diagnostics,
                }
            except Exception as exc:
                result["selected_group"] = {
                    "status": "NOT_EVALUABLE",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            manifest["branches"].append(result)
            write_json(manifest_path, manifest)
            if record.get("status") != "passed" or record.get("production_returncode") != 0:
                raise RuntimeError(
                    f"{branch_name} failed: status={record.get('status')} "
                    f"returncode={record.get('production_returncode')}"
                )

        manifest["run_status"] = "THREE_NATIVE_CUDA_PRODUCTION_BRANCHES_COMPLETED"
        manifest["gpu_snapshot_after"] = gpu_snapshot()
        write_json(manifest_path, manifest)
        print(json.dumps({
            "run_status": manifest["run_status"],
            "branches": [{
                "role": b["role"], "status": b["production_status"],
                "returncode": b["production_returncode"],
                "elapsed_sec": b["production_elapsed_sec"],
                "support": b.get("selected_group", {}).get("support_recomputed"),
                "gamma_status": b.get("selected_group", {}).get("gamma_row", {}).get("status"),
            } for b in manifest["branches"]],
            "manifest": str(manifest_path),
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        manifest["run_status"] = "FAILED_CLOSED"
        manifest["failure"] = f"{type(exc).__name__}: {exc}"
        manifest["gpu_snapshot_after"] = gpu_snapshot()
        write_json(manifest_path, manifest)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
