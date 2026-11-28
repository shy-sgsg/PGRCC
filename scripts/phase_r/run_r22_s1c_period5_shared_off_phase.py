#!/usr/bin/env python3
"""Run the R22-S1c GMTI05 single-period shared OFF phase counterfactual."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_SOURCE_ROOT = ROOT / "outputs/r22_s1_c4_support_replay_20261029"
DEFAULT_PIPE = Path("/tmp/pgrcc_r22_s1b_cuda_20261029/GMTI_pipe_core")
DEFAULT_SIMULATOR = ROOT / "build/simulate_stage2_statistical"
EXPECTED_BINARY_SHA256 = "16cb0f2d35596ff98ab8bdcbc9571b972b99e217053cf0b1b7f6b49168c8c998"
SOURCE_PERIOD_INDEX = 4  # zero-based input period_0004.bin, reported as GMTI05
PRODUCTION_PERIOD_ID = 1  # the one supplied file is local-test input 1=...
TRACE_SELECTOR = "1,50,52,1065,1197"
PHASE_FILENAME = "paired_range_phase_{stage}_period050_beam050.f32"
SOURCE_FILES = (
    "include/GMTIProcessor.hpp",
    "include/runtime_diagnostics.hpp",
    "src/runtime_diagnostics.cpp",
    "src/processOnePeriod.cpp",
    "src/production_calibration_adapter.cpp",
    "src/gpu/gpu_kernels.cu",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def one_case(source_root: Path, snr_db: int) -> tuple[Path, dict[str, Any]]:
    matches = sorted((source_root / f"snr{snr_db}" / "pipeline/cases").glob("*/case_manifest.json"))
    if len(matches) != 1:
        raise ValueError(f"SNR{snr_db}: expected one case manifest, found {len(matches)}")
    manifest_path = matches[0]
    return manifest_path.parent, json.loads(manifest_path.read_text(encoding="utf-8"))


def read_phase_vector(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size != 4096 * 4:
        raise ValueError(f"phase vector is missing or has unexpected length: {path}")
    values = struct.unpack("<4096f", path.read_bytes())
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"phase vector contains a non-finite value: {path}")
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "count": len(values),
        "sha256": sha256(path),
        "min_rad": min(values),
        "max_rad": max(values),
    }


def branch_source(case_root: Path, name: str) -> dict[str, Any]:
    path = case_root / "production" / name / "branch_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "passed":
        raise ValueError(f"source branch did not pass: {path}")
    if payload.get("truth_used_in_estimator") is not False:
        raise ValueError(f"source branch truth-use audit is not false: {path}")
    return payload


def select_period(
    case_root: Path,
    case_manifest: dict[str, Any],
    role: str,
    branch_name: str,
) -> dict[str, Any]:
    source = branch_source(case_root, branch_name)
    input_dir = Path(source["input_path"])
    source_file = input_dir / f"period_{SOURCE_PERIOD_INDEX:04d}.bin"
    if not source_file.is_file():
        raise ValueError(f"source period file is missing: {source_file}")
    identity = case_manifest["inputs"][role][SOURCE_PERIOD_INDEX]
    if sha256(source_file) != identity["sha256"]:
        raise ValueError(f"source period file SHA mismatch: {source_file}")
    return {
        "role": role,
        "source_branch_name": branch_name,
        "source_branch_manifest": str((case_root / "production" / branch_name / "branch_manifest.json").resolve()),
        "source_branch_manifest_sha256": sha256(case_root / "production" / branch_name / "branch_manifest.json"),
        "source_xml": source["source_xml"],
        "source_xml_sha256": sha256(Path(source["source_xml"])),
        "source_xml_overrides": dict(source.get("xml_overrides", {})),
        "source_period_index_zero_based": SOURCE_PERIOD_INDEX,
        "source_period_label": "GMTI05",
        "source_period_file": str(source_file.resolve()),
        "source_period_bytes": source_file.stat().st_size,
        "source_period_sha256": sha256(source_file),
        "production_period_id": PRODUCTION_PERIOD_ID,
        "truth_used_in_estimator": False,
    }


def load_conditions(source_root: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    conditions: dict[int, dict[str, Any]] = {}
    for snr_db in (20, 30):
        case_root, case_manifest = one_case(source_root, snr_db)
        case = case_manifest["case"]
        expected = {
            "seed": 1932001,
            "target_velocity_mps": 2.5,
            "target_snr_db": float(snr_db),
            "delay_error_ns": 4.0,
            "target_count": 20,
            "period_count": 10,
        }
        for key, value in expected.items():
            if case.get(key) != value:
                raise ValueError(f"SNR{snr_db}: unexpected {key}: {case.get(key)!r}")
        on = select_period(case_root, case_manifest, "ON", "C4_modeb_ON")
        off = select_period(case_root, case_manifest, "OFF", "C4_modea_OFF")
        conditions[snr_db] = {
            "snr_db": snr_db,
            "case_root": str(case_root.resolve()),
            "case_manifest": str((case_root / "case_manifest.json").resolve()),
            "case_manifest_sha256": sha256(case_root / "case_manifest.json"),
            "case_status_before_replay": case_manifest.get("status"),
            "identity": case,
            "protocol_layout": case_manifest["protocol_layout"],
            "input_identities": case_manifest.get("inputs", {}),
            "source_ON": on,
            "source_OFF": off,
        }

    off_periods = {
        snr_db: row["input_identities"].get("OFF", [])
        for snr_db, row in conditions.items()
    }
    if any(len(periods) != 10 for periods in off_periods.values()):
        raise ValueError("expected ten OFF input identities per SNR case")
    if [item.get("sha256") for item in off_periods[20]] != [
        item.get("sha256") for item in off_periods[30]
    ]:
        raise ValueError("the SNR20 and SNR30 target-free OFF inputs differ across periods")

    if conditions[20]["source_OFF"]["source_period_sha256"] != conditions[30]["source_OFF"]["source_period_sha256"]:
        raise ValueError("the SNR20 and SNR30 target-free OFF period-5 inputs differ")
    if conditions[20]["source_ON"]["source_period_sha256"] == conditions[30]["source_ON"]["source_period_sha256"]:
        raise ValueError("the SNR20 and SNR30 target period-5 ON inputs unexpectedly match")
    shared_off = {
        **conditions[20]["source_OFF"],
        "source_snr_db": 20,
        "cross_snr30_off_input_identical": True,
    }
    return conditions, shared_off


def configure_preflight(
    source_xml: Path,
    overrides: dict[str, Any],
    layout: dict[str, Any],
    expected_mode: str,
) -> None:
    from scripts import run_track_manager_e2e as e2e

    with tempfile.TemporaryDirectory(prefix="r22_s1c_period5_preflight_") as temp:
        root = Path(temp)
        xml_path = root / "production.xml"
        rc, log_path = e2e._configure_xml(
            source_xml, xml_path, root / "result", root / "track_debug",
            layout, xml_overrides=overrides,
        )
        if rc != 0 or not xml_path.is_file():
            raise ValueError(f"override XML failed to generate: {log_path}")
        if e2e._audit_runtime_xml_layout(xml_path, layout).get("status") != "passed":
            raise ValueError(f"override XML protocol layout failed: {xml_path}")
        params = ET.parse(xml_path).getroot().find("GMTI_parameter")
        if params is None:
            raise ValueError(f"runtime XML lacks GMTI_parameter: {xml_path}")
        for key, expected in (
            ("research_calibration_enable", "true"),
            ("research_calibration_method", "robust_ddc_rb"),
            ("research_calibration_mode", expected_mode),
            ("research_calibration_min_support", "8"),
            ("research_calibration_range_band_bins", "133"),
            ("research_calibration_robust_phase_threshold_rad", "0.35"),
            ("csi_metrics_enable", "true"),
            ("csi_metrics_beam_id", "50"),
        ):
            node = params.find(key)
            if node is None or node.text != str(expected):
                raise ValueError(f"runtime XML field mismatch: {key}")


def validate_xml_plan(conditions: dict[int, dict[str, Any]], output_root: Path) -> None:
    for snr_db, row in conditions.items():
        on = row["source_ON"]
        configure_preflight(
            Path(on["source_xml"]), dict(on["source_xml_overrides"]),
            row["protocol_layout"], "Mode-B",
        )
    off = conditions[20]["source_OFF"]
    configure_preflight(
        Path(off["source_xml"]), dict(off["source_xml_overrides"]),
        conditions[20]["protocol_layout"], "Mode-A",
    )
    for snr_db, row in conditions.items():
        on = row["source_ON"]
        overrides = dict(on["source_xml_overrides"])
        overrides["paired_raw_range_phase_override_f32"] = str(
            output_root / "production" /
            f"C4_modeb_S1c_period5_baseline_snr{snr_db}" / "result/debug" /
            PHASE_FILENAME.format(stage="raw")
        )
        overrides["paired_csi_range_phase_override_f32"] = str(
            output_root / "production/C4_modea_S1c_period5_target_free_OFF/result/debug" /
            PHASE_FILENAME.format(stage="csi")
        )
        validate_mode = "Mode-B"
        configure_preflight(
            Path(on["source_xml"]), overrides, row["protocol_layout"], validate_mode
        )


def make_period_link(output_root: Path, name: str, source_period: Path) -> Path:
    directory = output_root / "inputs" / name
    directory.mkdir(parents=True, exist_ok=False)
    link = directory / "period_0000.bin"
    link.symlink_to(source_period.resolve())
    return directory


def gpu_snapshot() -> str:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,pstate,temperature.gpu,power.draw,"
         "utilization.gpu,memory.used,memory.total,clocks.sm", "--format=csv"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    return result.stdout.strip()


def run_branch(
    *, output_root: Path, name: str, input_dir: Path, source: dict[str, Any],
    layout: dict[str, Any], overrides: dict[str, Any], stage1: Any,
) -> dict[str, Any]:
    before = gpu_snapshot()
    record = stage1.run_production_branch(
        output_root,
        name,
        input_dir,
        Path(source["source_xml"]),
        {},
        1,
        "local",
        layout,
        xml_overrides=overrides,
    )
    branch_manifest = output_root / "production" / name / "branch_manifest.json"
    vectors = {}
    for stage in ("raw", "csi"):
        path = output_root / "production" / name / "result/debug" / PHASE_FILENAME.format(stage=stage)
        vectors[stage] = read_phase_vector(path) if path.is_file() else None
    return {
        "branch_name": name,
        "source_role": source["role"],
        "source_period_index_zero_based": SOURCE_PERIOD_INDEX,
        "source_period_label": "GMTI05",
        "production_period_id": PRODUCTION_PERIOD_ID,
        "input_symlink": str((input_dir / "period_0000.bin").resolve()),
        "input_sha256": sha256(input_dir / "period_0000.bin"),
        "source_branch_manifest": source["source_branch_manifest"],
        "source_xml": source["source_xml"],
        "gpu_snapshot_before": before,
        "status": record.get("status"),
        "production_returncode": record.get("production_returncode"),
        "production_elapsed_sec": record.get("production_elapsed_sec"),
        "production_branch_manifest": str(branch_manifest.resolve()),
        "production_xml": record.get("production_xml"),
        "production_log": record.get("production_log"),
        "xml_overrides": dict(overrides),
        "phase_vectors": vectors,
        "branch_record": record,
    }


def require_passed(branch: dict[str, Any]) -> None:
    if branch.get("status") != "passed" or branch.get("production_returncode") != 0:
        raise RuntimeError(
            f"Production branch failed closed: {branch['branch_name']} "
            f"status={branch.get('status')} rc={branch.get('production_returncode')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pipe-binary", type=Path, default=DEFAULT_PIPE)
    parser.add_argument("--simulator-binary", type=Path, default=DEFAULT_SIMULATOR)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    pipe_binary = args.pipe_binary.resolve()
    simulator_binary = args.simulator_binary.resolve()
    if output_root.exists():
        parser.error(f"refusing to reuse or overwrite output root: {output_root}")
    if not pipe_binary.is_file() or not simulator_binary.is_file():
        parser.error("Production binary or required Stage2 simulator binary is missing")
    pipe_sha = sha256(pipe_binary)
    if pipe_sha != EXPECTED_BINARY_SHA256:
        parser.error(f"Production binary SHA mismatch: {pipe_sha}")
    cmake_cache = pipe_binary.parent / "CMakeCache.txt"
    if not cmake_cache.is_file() or "USE_COREX:BOOL=OFF" not in cmake_cache.read_text(errors="replace"):
        parser.error("Production build cache does not confirm native CUDA (USE_COREX=OFF)")

    source_hashes = {relative: sha256(ROOT / relative) for relative in SOURCE_FILES}
    source_manifest_path = (
        ROOT / "docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1b/s1b_manifest.json"
    )
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    for relative, expected in source_manifest["trace_source_sha256"].items():
        if source_hashes.get(relative) != expected:
            parser.error(f"Production diagnostic source identity changed: {relative}")

    conditions, shared_off = load_conditions(source_root)
    validate_xml_plan(conditions, output_root)
    if args.preflight_only:
        print(json.dumps({
            "preflight": "passed",
            "output_root_available": True,
            "source_period_index_zero_based": SOURCE_PERIOD_INDEX,
            "source_period_label": "GMTI05",
            "production_period_id": PRODUCTION_PERIOD_ID,
            "trace_selector": TRACE_SELECTOR,
            "snr20_snr30_off_period5_sha_equal": True,
            "production_binary_sha256": pipe_sha,
            "native_cuda": "USE_COREX=OFF",
            "support_gate": 8,
            "phase_threshold_rad": 0.35,
            "target_free_off_input_sha256": shared_off["source_period_sha256"],
        }, ensure_ascii=False, indent=2))
        return 0

    output_root.mkdir(parents=True, exist_ok=False)
    from scripts import run_delay_stage1_formal as stage1
    from scripts import run_track_manager_e2e as e2e

    stage1.SIMULATOR = simulator_binary
    e2e.PIPE = pipe_binary
    os.environ["PGRCC_C4_TRACE_GROUP"] = TRACE_SELECTOR

    frozen_off = dict(shared_off)
    frozen_off["output_phase_vector"] = None
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "task": "R22-S1c GMTI05 selected-group shared target-free OFF CSI phase counterfactual",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "repository": str(ROOT),
        "git_commit": git_output("rev-parse", "HEAD"),
        "tracked_worktree_status": git_output("status", "--short", "--untracked-files=no"),
        "runner_file": str(Path(__file__).resolve()),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "source_hashes": source_hashes,
        "production_binary": str(pipe_binary),
        "production_binary_sha256": pipe_sha,
        "production_binary_source_commit": source_manifest.get("git_commit"),
        "cmake_cache": str(cmake_cache),
        "cmake_cache_sha256": sha256(cmake_cache),
        "native_cuda": "USE_COREX=OFF",
        "simulator_binary": str(simulator_binary),
        "simulator_used": False,
        "stage2_regenerated": False,
        "source_root": str(source_root),
        "output_root": str(output_root),
        "selected_group": {
            "source_period_index_zero_based": SOURCE_PERIOD_INDEX,
            "source_period_label": "GMTI05",
            "production_period_id": PRODUCTION_PERIOD_ID,
            "trace_selector": TRACE_SELECTOR,
            "beam_id": 50,
            "azimuth_row": 52,
            "range_start": 1065,
            "range_end": 1197,
        },
        "conditions": {str(key): value for key, value in conditions.items()},
        "shared_target_free_off_input": shared_off,
        "method": "C4 Robust DDC-RB Mode-B ON estimator",
        "truth_used_in_estimator": False,
        "raw_range_phase_policy": "preserve period-5 raw phase vector from the same ON Production input",
        "csi_range_phase_policy": "apply one period-5 CSI phase vector estimated from the paired target-free OFF input",
        "quality_gates": {
            "research_calibration_min_support": 8,
            "research_calibration_robust_phase_threshold_rad": 0.35,
            "research_calibration_range_band_bins": 133,
            "fallback_to_current": False,
        },
        "gpu_snapshot_before": gpu_snapshot(),
        "branches": [],
    }
    manifest_path = output_root / "s1c_manifest.json"
    write_json(manifest_path, manifest)

    def append_run(branch: dict[str, Any]) -> None:
        manifest["branches"].append(branch)
        write_json(manifest_path, manifest)
        require_passed(branch)

    try:
        for snr_db in (20, 30):
            row = conditions[snr_db]
            source = row["source_ON"]
            input_dir = make_period_link(
                output_root, f"snr{snr_db}_ON_period5",
                Path(source["source_period_file"]),
            )
            name = f"C4_modeb_S1c_period5_baseline_snr{snr_db}"
            branch = run_branch(
                output_root=output_root, name=name, input_dir=input_dir,
                source=source, layout=row["protocol_layout"],
                overrides=dict(source["source_xml_overrides"]), stage1=stage1,
            )
            append_run(branch)

        off_source = conditions[20]["source_OFF"]
        off_input_dir = make_period_link(
            output_root, "target_free_OFF_period5",
            Path(off_source["source_period_file"]),
        )
        off_name = "C4_modea_S1c_period5_target_free_OFF"
        off_branch = run_branch(
            output_root=output_root, name=off_name, input_dir=off_input_dir,
            source=off_source, layout=conditions[20]["protocol_layout"],
            overrides=dict(off_source["source_xml_overrides"]), stage1=stage1,
        )
        append_run(off_branch)
        off_vector_path = output_root / "production" / off_name / "result/debug" / PHASE_FILENAME.format(stage="csi")
        if not off_vector_path.is_file():
            raise RuntimeError("target-free OFF Production did not save CSI phase vector")
        frozen_off["output_phase_vector"] = read_phase_vector(off_vector_path)
        manifest["frozen_target_free_off_csi_phase_vector"] = frozen_off["output_phase_vector"]
        write_json(manifest_path, manifest)

        for snr_db in (20, 30):
            row = conditions[snr_db]
            source = row["source_ON"]
            baseline_name = f"C4_modeb_S1c_period5_baseline_snr{snr_db}"
            raw_vector_path = output_root / "production" / baseline_name / "result/debug" / PHASE_FILENAME.format(stage="raw")
            baseline_raw = read_phase_vector(raw_vector_path)
            input_dir = make_period_link(
                output_root, f"snr{snr_db}_ON_period5_shared_off_phase",
                Path(source["source_period_file"]),
            )
            name = f"C4_modeb_S1c_period5_shared_off_phase_snr{snr_db}"
            overrides = dict(source["source_xml_overrides"])
            overrides["paired_raw_range_phase_override_f32"] = baseline_raw["path"]
            overrides["paired_csi_range_phase_override_f32"] = frozen_off["output_phase_vector"]["path"]
            branch = run_branch(
                output_root=output_root, name=name, input_dir=input_dir,
                source=source, layout=row["protocol_layout"],
                overrides=overrides, stage1=stage1,
            )
            append_run(branch)
            if branch["phase_vectors"]["raw"]["sha256"] != baseline_raw["sha256"]:
                raise RuntimeError(f"SNR{snr_db}: raw phase override was not applied byte-for-byte")
            if branch["phase_vectors"]["csi"]["sha256"] != frozen_off["output_phase_vector"]["sha256"]:
                raise RuntimeError(f"SNR{snr_db}: shared OFF CSI phase override was not applied byte-for-byte")

        manifest["run_status"] = "period5_production_branches_passed"
        manifest["gpu_snapshot_after"] = gpu_snapshot()
        write_json(manifest_path, manifest)
        return 0
    except Exception as exc:
        manifest["run_status"] = "failed_closed"
        manifest["failure"] = f"{type(exc).__name__}: {exc}"
        manifest["gpu_snapshot_after"] = gpu_snapshot()
        write_json(manifest_path, manifest)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
