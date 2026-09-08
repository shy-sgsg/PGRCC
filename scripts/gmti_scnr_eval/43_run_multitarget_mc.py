#!/usr/bin/env python3
"""125目标、三周期的生产链 Monte Carlo；先 prepare/probe，再正式统计。"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
C = 299792458.0
LEVELS = [13 + i * 0.5 for i in range(25)]


def module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


track_runner = module("track_case", "06_run_go_track_case.py")


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def read_csv(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def run(argv: list[str], log: Path, env=None):
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log.open("w") as f:
        f.write(json.dumps(argv, ensure_ascii=False) + "\n")
        f.flush()
        result = subprocess.run(argv, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, env=env)
        f.write(f"\nexit_code={result.returncode}\nwall_seconds={time.monotonic()-started:.6f}\n")
    if result.returncode:
        raise RuntimeError(f"命令失败 {result.returncode}: {log}")


def prepare(destination: Path, seed: int, input_snr: float, layout: str = "compact256"):
    """25 range slots × 5 circular-Doppler lanes; each level visits all lanes."""
    if (destination / "scenario.json").exists():
        raise ValueError(f"拒绝覆盖已有场景：{destination}")
    run([sys.executable, str(HERE / "04_make_compact_scenario.py"),
         "--output-dir", str(destination), "--background-profile", "target_only",
         "--period-count", "3", "--scnr-grid", "0", "--seed", str(seed)],
        destination / "prepare_template.log")
    path = destination / "scenario.json"
    doc = json.loads(path.read_text())
    doc["case_id"] = "mc125_noise_" + destination.name
    # Keep the proven waveform for the first isolation run; crop 25 range slots.
    doc["range_processing"]["range_crop_len"] = 2304
    wave = doc["waveform"]
    if layout == "compact256":
        # 5 separated lanes in the original-channel support, with complete
        # training rings outside the CSI crossover. Shorten the chirp/receive
        # record to offset doubling slow-time samples; retain production LFM.
        wave.update(pulse_num=256, tr_us=16.0, pulse_len=4096, acquired_pulse_len=4096)
        doc["range_processing"].update(range_fft_len=4096, range_crop_start=480)
    prf, pulses = wave["prf_hz"], wave["pulse_num"]
    wavelength = C / (wave["fc_ghz"] * 1e9)
    targets, design = [], []
    for lane in range(5):
        angle = (lane - 2) * 0.3
        theta = math.radians(angle)
        # At northward platform velocity and squint_side=1 the look is west.
        east, north = -math.cos(theta), -math.sin(theta)
        row = [0, 26, 52, 204, 230][lane] if layout == "compact256" else 10 + 25 * lane
        frequency = (row - pulses / 2) * prf / pulses
        for slot in range(25):
            level_index = (slot + lane * 5) % 25
            expected_bin = 160 + slot * 80
            slant = 0.5 * C * (doc["range_processing"]["sample_delay_us"] * 1e-6 +
                    (doc["range_processing"]["range_crop_start"] + expected_bin) /
                    (wave["fs_mhz"] * 1e6))
            ground = math.sqrt(slant * slant - doc["platform"]["height_m"] ** 2)
            horizontal_factor = ground / slant
            # motion_doppler_axis_sign=-1: f=-2 v_relative_LOS/lambda.
            radial_self = -frequency * wavelength / (2 * horizontal_factor) + 60 * north
            target_id = f"S{level_index:02d}_R{lane}"
            targets.append({"target_id": target_id, "enabled": True,
                "init": {"type": "beam_bin_azimuth_offset", "beam_id": 1,
                         "expected_bin": expected_bin, "azimuth_offset_deg": angle},
                "motion": {"type": "enu_velocity", "ve_mps": radial_self * east,
                           "vn_mps": radial_self * north},
                "amplitude": {"type": "snr_db", "snr_db": input_snr},
                "visibility": {"type": "hard_gate", "single_beam_only": True}})
            design.append({"target_id": target_id, "SCNR_target": LEVELS[level_index],
                           "lane": lane, "range_slot": slot, "range_bin": expected_bin,
                           "azimuth_deg": angle, "doppler_hz": frequency})
    doc["targets"] = targets
    write_json(path, doc)
    write_json(destination / "design.json", {"targets": design,
        "noise_seed": seed, "period_count": 3, "target_count": 125,
        "cfar_guard": 4, "cfar_background": 16,
        "range_spacing_bins": 80, "layout": layout,
        "minimum_circular_doppler_spacing_bins": 26 if layout == "compact256" else 25,
        "status": "layout_requires_actual_truth_and_signal_isolation_validation"})
    return path


def make_probes(case: Path):
    truth = read_csv(case / "stage2/truth/truth_targets_by_beam.csv")
    if len(truth) != 375 or any(r["visible"] not in ("1", "true") for r in truth):
        raise ValueError("125 targets × 3 periods must all be visible")
    probe_path = case / "probes.txt"
    with probe_path.open("w") as f:
        for r in truth:
            col = int(float(r["expected_bin"]))
            f.write(f"{int(r['period_id'])+1} 1 {r['target_id']} {r['af_total_truth_hz']} {col}\n")
    # Validate the circular CFAR footprint using emitted physical Doppler truth.
    doc = json.loads((case / "scenario.json").read_text())
    wave = doc["waveform"]
    minimum = math.inf
    unsafe = []
    for p in range(3):
        rows = [r for r in truth if int(r["period_id"]) == p]
        for i, a in enumerate(rows):
            for b in rows[i+1:]:
                dc = abs(float(a["expected_bin"]) - float(b["expected_bin"]))
                dr = abs(math.remainder(float(a["af_total_truth_hz"]) - float(b["af_total_truth_hz"]),
                                        wave["prf_hz"])) / wave["prf_hz"] * wave["pulse_num"]
                distance = math.hypot(float(a["e_mid"])-float(b["e_mid"]),
                                      float(a["n_mid"])-float(b["n_mid"]))
                minimum = min(minimum, distance)
                if dc <= 23 and dr <= 23:
                    unsafe.append([p, a["target_id"], b["target_id"], dc, dr])
    write_json(case / "layout_truth_check.json", {"truth_rows": len(truth),
        "unsafe_cfar_pairs": unsafe, "minimum_target_separation_m": minimum,
        "margin_definition": "outer CFAR half width 20 + 3 cell mainlobe allowance"})
    if unsafe:
        raise ValueError(f"unsafe CFAR neighbour pairs: {len(unsafe)}")
    return probe_path


def process(case: Path, build: Path, min_points: int, profile: str = "baseline"):
    doc = json.loads((case / "scenario.json").read_text())
    stage2 = case / "stage2"
    raw_paths = [stage2 / "data" / f"stage2_statistical_newprotocol_period_{p:04d}.bin" for p in range(3)]
    if not track_runner.raw_files_complete(stage2, raw_paths):
        if any(p.exists() for p in raw_paths):
            raise ValueError("partial raw generation found; retain failure and use a new case directory")
        run([str(build / "simulate_stage2_statistical"), "--config", str(case / "scenario.json")],
            case / "logs/generate.log")
    probes = make_probes(case)
    suffix = "" if profile == "baseline" else "_" + profile
    result = case / f"mp{min_points}{suffix}"
    if result.exists():
        raise ValueError(f"拒绝混合已有生产输出：{result}")
    args = argparse.Namespace(pfa=1e-6, cfar_guard=4, cfar_background=16,
        min_points=min_points, csi_split_small_cluster_min_points=3,
        csi_split_small_cluster_peak_over_median_db=20)
    xml = case / f"mp{min_points}{suffix}.xml"
    track_runner.patch_pipe_xml(stage2 / "config/temp_config_stage2_period_0000.xml",
                                xml, stage2, doc, result, args)
    tree = ET.parse(xml)
    for tag, value in {"csi_split_small_cluster_enable": "false",
                       "cluster_strong_small_enable": "false",
                       "debug_pc_peak": 0, "pc_peak_scene_truth": "",
                       "csi_metrics_enable": "false"}.items():
        track_runner.set_xml(tree.getroot(), tag, value)
    if profile in ("hann", "hann_kaiser"):
        # Existing production Hann window; broadens spectral support and reduces
        # off-grid sidelobes. Identical for every min_points and noise seed.
        track_runner.set_xml(tree.getroot(), "azimuth_fft_window", "hann")
    if profile == "hann_kaiser":
        track_runner.set_xml(tree.getroot(), "range_compression_window", "kaiser")
        track_runner.set_xml(tree.getroot(), "range_compression_kaiser_beta", 4)
    tree.write(xml, encoding="utf-8", xml_declaration=True)
    env = os.environ.copy()
    env["GMTI_CFAR_DUMP_BEAM"] = "1"
    env["GMTI_CFAR_PROBES"] = str(probes)
    run([str(build / "GMTI_pipe_core"), "--config", str(xml), "--result-dir", str(result),
         "--track-debug-dir", str(result / "track_debug"), "--track-debug-dump", "on",
         "--runtime-mode=debug", "--runtime-diagnostics=on", "--local-test",
         *[f"{p+1}={stage2}/data/stage2_statistical_newprotocol_period_{p:04d}.bin" for p in range(3)]],
        case / f"logs/mp{min_points}{suffix}.log", env)
    for p in range(1, 4):
        sparse = result / f"debug/cfar_GMTI{p:02d}_beam001_probes.csv"
        if len(read_csv(sparse)) != 125:
            raise ValueError(f"incomplete sparse probes: {sparse}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "probe"])
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--seed", type=int, default=2026090500)
    parser.add_argument("--input-snr", type=float, default=0)
    parser.add_argument("--min-points", type=int, choices=range(3, 9), default=3)
    parser.add_argument("--profile", choices=["baseline", "hann", "hann_kaiser"], default="baseline")
    parser.add_argument("--layout", choices=["compact256", "initial128"], default="compact256")
    args = parser.parse_args()
    case = args.case.resolve()
    if args.command == "prepare":
        print(prepare(case, args.seed, args.input_snr, args.layout))
    else:
        print(process(case, args.build_dir.resolve(), args.min_points, args.profile))


if __name__ == "__main__":
    main()
