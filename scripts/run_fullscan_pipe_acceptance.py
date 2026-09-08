#!/usr/bin/env python3
"""Prepare and run the two 61-beam multi-scan GMTI_pipe acceptance cases."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shlex
import struct
import subprocess
import sys
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_SPECS = {
    "stage2": {
        "label": "Stage2 面杂波24合作运动目标三周期全扫描",
        "config": REPO_ROOT / "simulator/scenarios/fullflow_stage2_area_multi_3scan.json",
        "scenario_dir": REPO_ROOT / "outputs/fullflow_stage2_area_multi_3scan",
        "simulator": REPO_ROOT / "build/simulate_stage2_statistical",
        "data_rel": "data/stage2_statistical_newprotocol.bin",
        "xml_rel": "config/temp_config_stage2_newsystem.xml",
        "extra_inputs": {},
    },
    "stage3": {
        "label": "Stage3 真实SAR背景24合作运动目标三周期全扫描",
        "config": REPO_ROOT / "configs/stage3/fullflow_stage3_sar_multi_3scan.json",
        "scenario_dir": REPO_ROOT / "outputs/fullflow_stage3_sar_multi_3scan",
        "simulator": REPO_ROOT / "build/simulate_stage3_sar_scene",
        "data_rel": "data/stage3_sar_scene_newprotocol.bin",
        "xml_rel": "config/temp_config_stage3_newsystem.xml",
        "extra_inputs": {
            "target_config": REPO_ROOT / "configs/stage3/fullflow_stage3_sar_multi_targets.json",
            "sar_source": REPO_ROOT / "simulator/scenarios/PGA_KuSAR_Block0.tif",
        },
    },
}
EXPECTED_CYCLES = 3
EXPECTED_BEAMS = 61
EXPECTED_PRTS_PER_BEAM = 130
EXPECTED_TARGETS = 24
EXPECTED_PULSE_LEN = 11820
EXPECTED_CHANNELS = 2
EXPECTED_PACKET_BYTES = 256 + EXPECTED_PULSE_LEN * EXPECTED_CHANNELS * 2 * 4
EXPECTED_DATA_BYTES = (
    EXPECTED_CYCLES * EXPECTED_BEAMS * EXPECTED_PRTS_PER_BEAM *
    EXPECTED_PACKET_BYTES)


XML_OVERRIDES = {
    # APX15 4 GiB device: the full-scan fusion path's historic hard-coded eight
    # workers can poison the CUDA context after transient CFAR/p38 allocations.
    # Keep the acceptance run deterministic and within measured device memory;
    # processing still uses the production CUDA kernels for every beam.
    "wavepos_parallel": "0",
    # loadXML keeps the deployed XML field name ``raw_fenbianlv`` for the
    # production DBS output resolution.
    "raw_fenbianlv": "250",
    "dbs_mosaic_use_gpu": "false",
    "dbs_max_mosaic_pixels": "100000000",
    # Display closure: accept a scan when at least 58/61 beam slots are valid.
    # The missing-beam quality event remains visible in logs and strict reports.
    "fusion_min_valid_beam_ratio": "0.95",
    "pf": "1e-8",
    "min_points": "3",
    "cluster_strong_small_enable": "false",
    "range_compression_window": "kaiser",
    "range_compression_kaiser_beta": "6.0",
    "range_compression_bandwidth_scale": "1.0",
    "azimuth_fft_window": "none",
    "cfar_doppler_circular": "true",
    "csi_detection_band_mode": "union",
    "csi_channel_alignment_mode": "none",
    "motion_comp_enable": "1",
    "motion_comp_apply_to_localization": "1",
    "motion_comp_solver": "root1d",
    "motion_comp_use_row_doppler": "1",
    "ati_vmax_mps": "50",
    "velocity_ambiguity_enable": "1",
    "velocity_search_min_mps": "-50",
    "velocity_search_max_mps": "50",
    "velocity_max_doppler_order": "8",
    "velocity_max_phase_order": "32",
    "velocity_max_candidates": "512",
    "velocity_beam_gate_deg": "4.56",
    "new_protocol_file_first_beam": "1",
    "shm_scan_beam_count": "61",
    "shm_prt_counter_phase": "0",
    "wavepos_st": "1",
    "wavepos_ed": "61",
    "wavepos_skip": "1",
    "track_output_state_source": "measurement",
    "track_confirm_window": "3",
    "track_confirm_hits": "2",
    "track_truth_threshold": "2",
    "track_max_missed": "2",
    "track_tentative_max_missed": "1",
    "track_gate_m": "300",
    "track_v_max": "50",
    "track_debug_dump": "true",
    "track_debug_dump_level": "1",
    "track_debug_level": "1",
    "track_debug_frames": "0",
}


def command_text(command: list[str]) -> str:
    return shlex.join(str(item) for item in command)


def run(command: list[str], *, env: dict | None = None,
        capture: bool = False, dry_run: bool = False) -> subprocess.CompletedProcess:
    print(f"[fullflow] {command_text(command)}", flush=True)
    if dry_run:
        return subprocess.CompletedProcess(command, 0, "", "")
    return subprocess.run(
        [str(item) for item in command], cwd=REPO_ROOT, env=env,
        text=True, capture_output=capture, check=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_newprotocol_input(path: Path) -> dict:
    """Validate every PRT header against the fixed 3x61x130 scan layout."""
    expected_prts = EXPECTED_CYCLES * EXPECTED_BEAMS * EXPECTED_PRTS_PER_BEAM
    errors: list[str] = []
    max_utc_error_s = 0.0
    observed_min_theta = float("inf")
    observed_max_theta = float("-inf")
    with path.open("rb") as stream:
        for index in range(expected_prts):
            header = stream.read(256)
            if len(header) != 256:
                errors.append(f"short header at PRT {index}: {len(header)} bytes")
                break
            magic_head = struct.unpack_from("<Q", header, 0)[0]
            version = header[8]
            packet_bytes = struct.unpack_from("<I", header, 9)[0]
            utc = struct.unpack_from("<f", header, 16)[0]
            counter = struct.unpack_from("<I", header, 20)[0]
            theta_x100 = struct.unpack_from("<h", header, 218)[0]
            magic_tail = struct.unpack_from("<Q", header, 248)[0]
            within_scan = index % (EXPECTED_BEAMS * EXPECTED_PRTS_PER_BEAM)
            beam_index = within_scan // EXPECTED_PRTS_PER_BEAM
            pulse_index = within_scan % EXPECTED_PRTS_PER_BEAM
            scan_index = index // (EXPECTED_BEAMS * EXPECTED_PRTS_PER_BEAM)
            expected_theta_x100 = int(round((-60.0 + 2.0 * beam_index) * 100.0))
            expected_utc = (
                scan_index * EXPECTED_BEAMS * EXPECTED_PRTS_PER_BEAM / 1300.0 +
                beam_index * EXPECTED_PRTS_PER_BEAM / 1300.0 +
                pulse_index / 1300.0)
            max_utc_error_s = max(max_utc_error_s, abs(utc - expected_utc))
            observed_min_theta = min(observed_min_theta, theta_x100 / 100.0)
            observed_max_theta = max(observed_max_theta, theta_x100 / 100.0)
            checks = (
                (magic_head == 0x5A5A5A5A5A5A5A5A, "magic_head"),
                (version == 9, "version"),
                (packet_bytes == EXPECTED_PACKET_BYTES, "prt_len"),
                (counter == index, "prt_counter"),
                (header[208] == (index & 0xFF), "prt_low_byte"),
                (theta_x100 == expected_theta_x100, "theta_x100"),
                (magic_tail == 0x5B5B5B5B5B5B5B5B, "magic_tail"),
            )
            for passed, field in checks:
                if not passed and len(errors) < 20:
                    errors.append(
                        f"PRT {index} field {field} invalid; "
                        f"theta_x100={theta_x100} expected={expected_theta_x100}")
            stream.seek(EXPECTED_PACKET_BYTES - 256, os.SEEK_CUR)
    if max_utc_error_s > 1.0e-5:
        errors.append(f"max UTC error {max_utc_error_s} exceeds 1e-5 s")
    return {
        "status": "passed" if not errors else "failed",
        "checked_prts": expected_prts,
        "expected_scan_layout": "3 scans x 61 beams x 130 PRT",
        "packet_bytes": EXPECTED_PACKET_BYTES,
        "max_utc_error_s": max_utc_error_s,
        "observed_min_theta_deg": observed_min_theta,
        "observed_max_theta_deg": observed_max_theta,
        "error_count": len(errors),
        "errors": errors,
    }


def audit_truth_summary(path: Path) -> dict:
    errors: list[str] = []
    if not path.is_file():
        return {"status": "failed", "errors": [f"missing truth file: {path}"]}
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    visible = [row for row in rows if row.get("visible", "0") == "1"]
    target_ids = sorted({row.get("target_id", "") for row in rows})
    expected_rows = EXPECTED_CYCLES * EXPECTED_BEAMS * EXPECTED_TARGETS
    if len(rows) != expected_rows:
        errors.append(f"summary rows={len(rows)} expected={expected_rows}")
    if len(target_ids) != EXPECTED_TARGETS or "" in target_ids:
        errors.append(
            f"unique target ids={len(target_ids)} expected={EXPECTED_TARGETS}")
    by_target_period: dict[tuple[str, int], list[dict[str, str]]] = {}
    visible_per_scan = [0] * EXPECTED_CYCLES
    targets_per_beam: dict[tuple[int, int], int] = {}
    for row in visible:
        period = int(row["period_id"])
        beam = int(row["beam_id"])
        by_target_period.setdefault((row["target_id"], period), []).append(row)
        if 0 <= period < EXPECTED_CYCLES:
            visible_per_scan[period] += 1
        targets_per_beam[(period, beam)] = targets_per_beam.get((period, beam), 0) + 1
        try:
            values = [float(row[key]) for key in (
                "utc_mid", "e_mid", "n_mid", "range_m", "ve_mps", "vn_mps")]
            range_bin = int(float(row["range_bin"]))
            if not all(math.isfinite(value) for value in values):
                errors.append(f"non-finite visible truth: {row['target_id']} p{period}")
            if not (0 <= range_bin < 4096):
                errors.append(
                    f"visible truth range_bin={range_bin} outside [0,4095]: "
                    f"{row['target_id']} p{period}")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"invalid visible truth row: {exc}")
    if len(by_target_period) != EXPECTED_TARGETS * EXPECTED_CYCLES:
        errors.append(
            f"visible target-period pairs={len(by_target_period)} "
            f"expected={EXPECTED_TARGETS * EXPECTED_CYCLES}")
    duplicate_visibility = [
        f"{target}:p{period}={len(group)}"
        for (target, period), group in by_target_period.items()
        if len(group) != 1
    ]
    if duplicate_visibility:
        errors.append("target visible in !=1 beam: " + "; ".join(duplicate_visibility[:10]))
    max_velocity_error_mps = 0.0
    for target in target_ids:
        sequence = [
            by_target_period[(target, period)][0]
            for period in range(EXPECTED_CYCLES)
            if (target, period) in by_target_period and
               len(by_target_period[(target, period)]) == 1
        ]
        for left, right in zip(sequence, sequence[1:]):
            dt = float(right["utc_mid"]) - float(left["utc_mid"])
            if dt <= 0.0:
                errors.append(f"non-increasing target time: {target}")
                continue
            observed_ve = (float(right["e_mid"]) - float(left["e_mid"])) / dt
            observed_vn = (float(right["n_mid"]) - float(left["n_mid"])) / dt
            error = math.hypot(
                observed_ve - float(left["ve_mps"]),
                observed_vn - float(left["vn_mps"]))
            max_velocity_error_mps = max(max_velocity_error_mps, error)
    if max_velocity_error_mps > 1.0e-4:
        errors.append(
            f"truth displacement/velocity max error={max_velocity_error_mps} m/s")
    max_targets_in_one_beam = max(targets_per_beam.values(), default=0)
    if max_targets_in_one_beam < 4:
        errors.append(
            "no beam contains four visible targets; multi-target interaction case missing")
    return {
        "status": "passed" if not errors else "failed",
        "summary_rows": len(rows),
        "unique_target_count": len(target_ids),
        "visible_rows": len(visible),
        "visible_per_scan": visible_per_scan,
        "visible_target_period_pairs": len(by_target_period),
        "max_targets_in_one_beam": max_targets_in_one_beam,
        "max_displacement_velocity_error_mps": max_velocity_error_mps,
        "error_count": len(errors),
        "errors": errors,
    }


def git_identity() -> dict:
    commit = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"], cwd=REPO_ROOT,
        text=True, capture_output=True, check=False).stdout.strip()
    dirty = bool(subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT,
        text=True, capture_output=True, check=False).stdout.strip())
    return {"git_commit": commit, "git_dirty": dirty}


def cmake_build_type() -> str:
    cache = REPO_ROOT / "build/CMakeCache.txt"
    if not cache.is_file():
        return "unknown"
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("CMAKE_BUILD_TYPE:STRING="):
            return line.split("=", 1)[1] or "unspecified"
    return "unknown"


def configured_random_seeds(case_name: str, config_path: Path) -> dict:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if case_name == "stage2":
        return {"simulation": payload.get("random", {}).get("random_seed")}
    return {
        "tiling": payload.get("tiling", {}).get("random_seed"),
        "scatterer_extraction": payload.get(
            "scatterer_extraction", {}).get("random_seed"),
    }


def gpu_status() -> dict:
    command = [
        "nvidia-smi",
        "--query-gpu=name,driver_version,pstate,power.draw,power.limit,"
        "clocks.sm,clocks.mem,temperature.gpu,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    result = subprocess.run(
        command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    return {
        "command": command_text(command),
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def latest_run_dir(output_root: Path) -> Path | None:
    values = sorted((path for path in output_root.glob("run_*") if path.is_dir()),
                    key=lambda path: path.stat().st_mtime)
    return values[-1] if values else None


def configure_xml(source: Path, destination: Path, dry_run: bool) -> int:
    command = [
        sys.executable, REPO_ROOT / "scripts/configure_gmti_xml.py",
        "--input", source, "--output", destination,
    ]
    for key, value in XML_OVERRIDES.items():
        command.extend(["--set", f"{key}={value}"])
    return run(command, dry_run=dry_run).returncode


def write_manifest(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=sorted(CASE_SPECS), required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--skip-generate", action="store_true")
    parser.add_argument("--force-generate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--input-rate-bytes-per-sec", type=int, default=100_000_000)
    parser.add_argument("--chunk-bytes", type=int, default=1_048_576)
    parser.add_argument("--result-timeout-ms", type=int, default=900_000)
    parser.add_argument(
        "--output-root", type=Path,
        default=REPO_ROOT / "outputs/fullflow_pipe_acceptance")
    args = parser.parse_args()
    spec = CASE_SPECS[args.case]
    scenario_dir: Path = spec["scenario_dir"]
    data_path = scenario_dir / spec["data_rel"]
    source_xml = scenario_dir / spec["xml_rel"]
    case_root = args.output_root.resolve() / args.case
    runs_root = case_root / "runs"
    report_root = case_root / "latest_report"
    configured_xml = case_root / f"{args.case}_fullscan_pipe.xml"
    commands: list[str] = []
    manifest = {
        "case": args.case,
        "label": spec["label"],
        "created_at": datetime.now().astimezone().isoformat(),
        "scenario_config": str(spec["config"]),
        "scenario_dir": str(scenario_dir),
        "expected_cycles": EXPECTED_CYCLES,
        "expected_beams_per_cycle": EXPECTED_BEAMS,
        "expected_prts_per_beam": EXPECTED_PRTS_PER_BEAM,
        "expected_prts_total": EXPECTED_CYCLES * EXPECTED_BEAMS * EXPECTED_PRTS_PER_BEAM,
        "expected_packet_bytes": EXPECTED_PACKET_BYTES,
        "expected_data_bytes": EXPECTED_DATA_BYTES,
        "xml_overrides": XML_OVERRIDES,
        "build_type": cmake_build_type(),
        "runtime_mode": "debug",
        "random_seeds": configured_random_seeds(args.case, spec["config"]),
        **git_identity(),
        "commands": commands,
        "status": "initializing",
    }
    case_root.mkdir(parents=True, exist_ok=True)

    data_valid = data_path.is_file() and data_path.stat().st_size == EXPECTED_DATA_BYTES
    should_generate = args.force_generate or (not args.skip_generate and not data_valid)
    if should_generate:
        command = [spec["simulator"], "--config", spec["config"]]
        commands.append(command_text(command))
        result = run(command, dry_run=args.dry_run)
        if result.returncode != 0:
            manifest.update({
                "status": "generation_failed",
                "generation_returncode": result.returncode,
            })
            write_manifest(case_root / "run_manifest.json", manifest)
            return result.returncode
    elif not data_valid and args.skip_generate:
        manifest.update({
            "status": "input_missing_or_wrong_size",
            "actual_data_bytes": data_path.stat().st_size if data_path.exists() else None,
        })
        write_manifest(case_root / "run_manifest.json", manifest)
        print(f"[fullflow][ERR] invalid input size: {data_path}", file=sys.stderr)
        return 2

    if not args.dry_run:
        if not data_path.is_file() or data_path.stat().st_size != EXPECTED_DATA_BYTES:
            manifest.update({
                "status": "generated_input_wrong_size",
                "actual_data_bytes": data_path.stat().st_size if data_path.exists() else None,
            })
            write_manifest(case_root / "run_manifest.json", manifest)
            return 3
        if not source_xml.is_file():
            manifest.update({"status": "generated_xml_missing"})
            write_manifest(case_root / "run_manifest.json", manifest)
            return 4
        protocol_audit = audit_newprotocol_input(data_path)
        (case_root / "input_protocol_audit.json").write_text(
            json.dumps(protocol_audit, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        manifest["input_protocol_audit"] = protocol_audit
        if protocol_audit["status"] != "passed":
            manifest["status"] = "input_protocol_invalid"
            write_manifest(case_root / "run_manifest.json", manifest)
            return 4
        truth_audit = audit_truth_summary(
            scenario_dir / "truth" / "truth_targets_by_beam.csv")
        (case_root / "input_truth_audit.json").write_text(
            json.dumps(truth_audit, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        manifest["input_truth_audit"] = truth_audit
        if truth_audit["status"] != "passed":
            manifest["status"] = "input_truth_invalid"
            write_manifest(case_root / "run_manifest.json", manifest)
            return 4

    configure_command = [
        sys.executable, REPO_ROOT / "scripts/configure_gmti_xml.py",
        "--input", source_xml, "--output", configured_xml,
    ]
    for key, value in XML_OVERRIDES.items():
        configure_command.extend(["--set", f"{key}={value}"])
    commands.append(command_text(configure_command))
    if configure_xml(source_xml, configured_xml, args.dry_run) != 0:
        manifest.update({"status": "xml_configuration_failed"})
        write_manifest(case_root / "run_manifest.json", manifest)
        return 5

    if not args.dry_run:
        manifest.update({
            "data_path": str(data_path),
            "data_bytes": data_path.stat().st_size,
            "data_sha256": sha256(data_path),
            "configured_xml": str(configured_xml),
            "configured_xml_sha256": sha256(configured_xml),
            "scenario_config_sha256": sha256(spec["config"]),
            "truth_summary_path": str(
                scenario_dir / "truth" / "truth_targets_by_beam.csv"),
            "truth_summary_sha256": sha256(
                scenario_dir / "truth" / "truth_targets_by_beam.csv"),
            "algorithm_executable": str(REPO_ROOT / "build/GMTI_pipe_core"),
            "algorithm_executable_sha256": sha256(
                REPO_ROOT / "build/GMTI_pipe_core"),
            "simulator_executable": str(spec["simulator"]),
            "simulator_executable_sha256": sha256(spec["simulator"]),
            "extra_inputs": {
                name: {
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
                for name, path in spec["extra_inputs"].items()
            },
        })
    if args.prepare_only or args.dry_run:
        manifest["status"] = "prepared" if not args.dry_run else "dry_run"
        write_manifest(case_root / "run_manifest.json", manifest)
        print(json.dumps({
            "status": manifest["status"], "case": args.case,
            "manifest": str(case_root / "run_manifest.json"),
        }, ensure_ascii=False))
        return 0

    manifest["gpu_status_before"] = gpu_status()
    write_manifest(case_root / "run_manifest.json", manifest)
    integration = [
        REPO_ROOT / "scripts/run_shm_phase1_integration.sh",
        configured_xml, data_path, runs_root,
        "1", str(args.input_rate_bytes_per_sec), str(args.chunk_bytes),
        str(EXPECTED_CYCLES), "300000", "", "", "", "",
        str(args.result_timeout_ms),
    ]
    commands.append("GMTI_RUNTIME_MODE=debug " + command_text(integration))
    env = dict(os.environ)
    env["GMTI_RUNTIME_MODE"] = "debug"
    result = run(integration, env=env, capture=True)
    manifest["gpu_status_after"] = gpu_status()
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    run_dir = latest_run_dir(runs_root)
    manifest.update({
        "pipe_returncode": result.returncode,
        "pipe_run_dir": str(run_dir) if run_dir else None,
    })
    if result.returncode != 0 or run_dir is None:
        manifest["status"] = "pipe_failed"
        write_manifest(case_root / "run_manifest.json", manifest)
        return result.returncode or 6

    report_root.mkdir(parents=True, exist_ok=True)
    visual_command = [
        sys.executable, REPO_ROOT / "scripts/visualize_track_manager.py",
        "--debug-dir", run_dir / "result/track_debug",
        "--coord", "en", "--out-dir", report_root / "track_visualization",
        "--format", "png", "--min-len", "2", "--no-dbs-bg",
        "--make-output-gif", "--keep-frame-png", "false",
    ]
    commands.append(command_text(visual_command))
    visual_result = run(visual_command)
    manifest["visualization_returncode"] = visual_result.returncode

    evaluate_command = [
        sys.executable, REPO_ROOT / "scripts/evaluate_fullscan_pipe_e2e.py",
        "--scenario-dir", scenario_dir,
        "--run-dir", run_dir,
        "--report-dir", report_root,
        "--case-label", spec["label"],
        "--expected-cycles", str(EXPECTED_CYCLES),
    ]
    commands.append(command_text(evaluate_command))
    eval_result = run(evaluate_command)
    manifest.update({
        "evaluation_returncode": eval_result.returncode,
        "report_dir": str(report_root),
        "status": "passed" if eval_result.returncode == 0 and
                    visual_result.returncode == 0 else "failed_acceptance",
    })
    write_manifest(case_root / "run_manifest.json", manifest)
    print(json.dumps({
        "status": manifest["status"], "case": args.case,
        "run_dir": str(run_dir), "report_dir": str(report_root),
        "manifest": str(case_root / "run_manifest.json"),
    }, ensure_ascii=False))
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
