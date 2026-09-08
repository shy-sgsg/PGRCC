#!/usr/bin/env python3
"""受控输出 SNR 校准及中心波位 Monte Carlo。

默认模式是复杂 C+N 经验背景之前的标准基线，面积杂波关闭，只保留生产
Stage2 的热噪声、目标注入和完整 GMTI→GO-CFAR→聚类→目标匹配链路；通过
``--background-profile compact_statistical`` 可切换到紧凑 Rayleigh×lognormal
面积杂波，形成可审计的 S+C+N/C+N-only 配对验证。每个
角度使用同一批 packet-addressed 随机噪声：N-only 与 S+N 配对，S-only
冻结目标支撑并记录 ``target_snr_db`` 到实际 GO 输入输出 SNR 的映射；S-only
仅用于支撑/线性诊断；正式 output-SCNR 主轴采用生产物理 CUT 上 S+N 相对 N-only
的有效功率，固定 3×3 支撑量另存为诊断列。

正式横轴只使用测得的物理 CUT ``output_scnr_db``；输入 target_snr_db 仅作为控制变量。
单元事件使用生产 GO hit map 的 exact CUT，目标事件使用 production truth
matching，三屏航迹事件使用同一目标的三屏 truth hit 做 2-of-3。TrackManager
审计仍然运行并保留，但不替代本专项主航迹事件。

在 S-only/C+N-only 诊断图清理前，脚本额外写出紧凑的
``target_support_measurements.csv``/``target_support_profiles.csv``，用于
目标级 Poisson-binomial 形状标定；它不读取 S+N 命中、不写回在线配置。
GO/TrackManager 审计完成后，Stage2 输入 BIN 与 pipe 结果目录中的
``GMTI*.bin`` 均按 manifest 记录清理；逐周期检测/航迹、truth、F32 与临时配置
一并清除，仅保留角度根目录汇总 CSV/图表以及最小化 pipeline manifest/正式 XML。
target Pd 使用同帧 bounded truth match 且要求 cluster gate 通过，防止 stale target_id
或非本地 Doppler 峰造成 target>cluster 的不一致。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from scipy.stats import ncx2

from scnr_eval_lib import (GoCfarGeometry, ensure_dir, go_conditional_pd,
                           go_training_noise_samples, linear_to_db, read_csv,
                           sha256_file, wilson_interval, write_csv)


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]


def fnum(value: object, default: float = float("nan")) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def inum(value: object, default: int = -1) -> int:
    parsed = fnum(value)
    return int(round(parsed)) if math.isfinite(parsed) else default


def go_period_mixture_pd(signal_gamma: np.ndarray, m_norm: np.ndarray,
                         alpha: float) -> np.ndarray:
    """逐周期 GO-CFAR Pd，保留目标在 FFT 支撑内的确定性形状。

    ``signal_gamma[r]`` 是指定信号口径（S-only 独立诊断，或配对
    S+N/N-only 输出增量）在物理 truth CUT 的实测信号功率/P0，
    ``m_norm[r]`` 是同一 period 的 C+N-only GO 最大训练均值/P0。
    两者只用于离线理论积分，绝不写回 detector 配置或用于选择 hit。
    相比先把所有 period 的功率平均后再代入 Q1，这个 mixture 保留了
    range/Doppler 量化造成的“多数 period 落在邻 bin、少数 period 命中
    truth CUT”的真实几何。
    """
    gamma = np.asarray(signal_gamma, dtype=np.float64)
    m = np.asarray(m_norm, dtype=np.float64)
    if gamma.shape != m.shape:
        raise ValueError("逐周期 GO mixture 的 signal_gamma/m_norm 形状必须一致")
    valid = np.isfinite(gamma) & np.isfinite(m) & (gamma >= 0.0) & (m > 0.0)
    out = np.full(gamma.shape, np.nan, dtype=np.float64)
    if np.any(valid):
        out[valid] = ncx2.sf(2.0 * alpha * m[valid], 2,
                             2.0 * gamma[valid])
    return out


def run_command(argv: list[str], log: Path, env: dict[str, str] | None = None) -> float:
    ensure_dir(log.parent)
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(argv) + "\n\n")
        result = subprocess.run(argv, cwd=ROOT, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, text=True, check=False)
        elapsed = time.monotonic() - started
        stream.write(f"\n[exit_code]={result.returncode}\n[elapsed_sec]={elapsed:.3f}\n")
    if result.returncode:
        raise RuntimeError(f"命令失败（退出码 {result.returncode}）：{' '.join(argv)}；见 {log}")
    return elapsed


def write_json(path: Path, value: object) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_grid(text: str) -> list[float]:
    values = [float(token.strip()) for token in text.split(",") if token.strip()]
    if not values or len(set(round(x, 8) for x in values)) != len(values):
        raise ValueError("网格不能为空且不能重复")
    if any(not math.isfinite(x) for x in values):
        raise ValueError("网格包含非有限值")
    return values


def target_schedule(levels: list[float], trials: int) -> tuple[list[float], list[dict[str, int | float]]]:
    schedule: list[float] = []
    groups: list[dict[str, int | float]] = []
    group = 0
    period = 0
    for level in levels:
        for trial in range(trials):
            first = period
            schedule.extend([level, level, level])
            groups.append({"group_id": group, "level_index": levels.index(level),
                           "trial_index": trial, "input_snr_db": level,
                           "period_start": first, "period_count": 3})
            period += 3
            group += 1
    return schedule, groups


def make_base_scenario(destination: Path, angle: float, seed: int,
                       schedule: list[float], case_id: str,
                       target_angle: float | None = None,
                       background_profile: str = "target_only") -> Path:
    """先复用项目现有紧凑场景模板，再收窄为一个固定几何目标。"""
    ensure_dir(destination)
    target_angle = float(angle if target_angle is None else target_angle)
    command = [
        sys.executable, str(SCRIPT_DIR / "04_make_compact_scenario.py"),
        "--output-dir", str(destination), "--seed", str(seed), "--beam-count", "1",
        "--scan-min-deg", f"{angle:g}", "--scan-step-deg", "2",
        "--target-angles", f"{target_angle:g}", "--scnr-grid", "20", "--replicas", "1",
        "--period-count", str(len(schedule)), "--background-profile", background_profile,
        "--case-id", case_id,
    ]
    run_command(command, destination / "make_scenario.log")
    scenario = destination / "scenario.json"
    document = json.loads(scenario.read_text(encoding="utf-8"))
    document["case_id"] = case_id
    document["output_dir"] = str((destination / "stage2").resolve())
    document["random"]["period_start"] = 0
    document["random"]["period_count"] = len(schedule)
    scene = document["scene"]
    if background_profile == "target_only":
        scene["mode"] = "target_only"
        scene["signal_only"] = False
        scene["clutter_amplitude_scale"] = 0.0
        scene["area_clutter"] = {"enabled": False}
        scene["strong_scatterers"] = {"enabled": False, "count": 0}
        scene["line_scatterers"] = {"enabled": False, "line_count": 0, "points_per_line": 0}
        scene["thermal_noise"] = {"enabled": True, "noise_power": 0.01,
                                   "include_target_only": True}
    elif background_profile == "compact_statistical":
        # Keep the compact generator's Rayleigh×lognormal area clutter while
        # disabling discrete strong/line scatterers.  This isolates the
        # non-IID background term in the S+C+N validation.
        scene["mode"] = "full"
        scene["signal_only"] = False
        scene["clutter_amplitude_scale"] = 1.0
        scene["area_clutter"] = {
            "enabled": True, "model": "rayleigh_lognormal_texture",
            "scatterer_count": 240, "mean_power": 1.0,
            "texture_sigma": 0.4, "spatial_cell_m": 30.0,
            "azimuth_subcell_count": 9,
        }
        scene["strong_scatterers"] = {"enabled": False, "count": 0}
        scene["line_scatterers"] = {"enabled": False, "line_count": 0, "points_per_line": 0}
        scene["thermal_noise"] = {"enabled": True, "noise_power": 0.01,
                                   "include_target_only": False}
    else:
        raise ValueError(f"unsupported background profile: {background_profile}")
    if len(document.get("targets", [])) != 1:
        raise RuntimeError("标准受控场景应恰有一个目标")
    target = document["targets"][0]
    target["target_id"] = "STD_FIXED_TARGET"
    target["enabled"] = True
    target["start_period"] = 0
    target["end_period"] = len(schedule) - 1
    # The compact-scenario template used a generic (-12, 9) m/s ENU velocity.
    # Over a 300-period run that transverse component moves the target out of
    # the one-beam hard gate (the previous formal run became invisible at
    # period 264).  For this controlled MC we keep a non-zero relative radial
    # speed, but cancel the transverse component exactly: target velocity is
    # platform velocity plus ``relative_radial_mps`` times the commanded look
    # vector.  With the project geometry (local x=north, local y=east,
    # platform heading north, left-looking radar), the commanded ENU look is
    # (cos(180+angle), sin(180+angle)).  Thus the physical bearing remains
    # fixed for all periods while the target remains safely away from the
    # low-radial-velocity filter.  This is a geometry correction, not a
    # detection-threshold adjustment.
    relative_radial_mps = 15.0
    heading_deg = 90.0
    side_dir_deg = -90.0 if int(document["platform"].get("squint_side", 1)) == 1 else 90.0
    beam_center_dir_deg = side_dir_deg - target_angle
    look_azimuth_deg = heading_deg - beam_center_dir_deg
    look_azimuth_rad = math.radians(look_azimuth_deg)
    target["motion"] = {
        "type": "enu_velocity",
        "ve_mps": relative_radial_mps * math.cos(look_azimuth_rad),
        "vn_mps": float(document["platform"]["speed_mps"]) +
                  relative_radial_mps * math.sin(look_azimuth_rad),
    }
    target["amplitude"] = {"type": "snr_db", "snr_db": float(schedule[0]),
                            "snr_db_by_period": [float(x) for x in schedule]}
    # 保持同一个位置/速度/波位；每个 period 的支撑由独立的高 S-only 参考轨迹冻结。
    scenario.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return scenario


def variant_scenario(base: Path, destination: Path, enabled: bool,
                     signal_only: bool, schedule: list[float], label: str,
                     truth_carrier_snr_db: float | None = None) -> Path:
    document = json.loads(base.read_text(encoding="utf-8"))
    destination = destination.resolve()
    document["case_id"] = f"{document.get('case_id', 'std')}_{label}"
    document["output_dir"] = str((destination / "stage2").resolve())
    document["random"]["period_count"] = len(schedule)
    document["scene"]["signal_only"] = bool(signal_only)
    # N-only 需要完整 truth 轨迹来把固定支撑映射到每个 period，但生产
    # Stage2 在 target.enabled=false 时不会写 truth_targets_by_beam.csv。
    # 因此允许一个 -200 dB 的“truth carrier”：它只触发 truth 输出，
    # 相对热噪声功率可忽略，不改变 N-only 的背景估计。
    document["targets"][0]["enabled"] = bool(enabled or truth_carrier_snr_db is not None)
    document["targets"][0]["start_period"] = 0
    document["targets"][0]["end_period"] = len(schedule) - 1
    amplitude_schedule = ([float(truth_carrier_snr_db)] * len(schedule)
                          if truth_carrier_snr_db is not None
                          else [float(x) for x in schedule])
    document["targets"][0]["amplitude"] = {
        "type": "snr_db", "snr_db": float(amplitude_schedule[0]),
        "snr_db_by_period": amplitude_schedule,
    }
    ensure_dir(destination)
    scenario = destination / "scenario.json"
    scenario.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return scenario


def set_xml(root: ET.Element, tag: str, value: object) -> None:
    parent = root.find("GMTI_parameter")
    if parent is None:
        raise RuntimeError("生产 XML 缺少 GMTI_parameter")
    node = parent.find(tag)
    if node is None:
        node = ET.SubElement(parent, tag)
    node.text = str(value)


def patch_pipe_xml(source: Path, destination: Path, stage2: Path,
                   scenario: dict, result_dir: Path, args: argparse.Namespace) -> Path:
    tree = ET.parse(source)
    root = tree.getroot()
    scan, wave, random = scenario["scan"], scenario["waveform"], scenario["random"]
    values: dict[str, object] = {
        "result_add": result_dir,
        "GMTI_data_new": stage2 / "data" / "stage2_statistical_newprotocol_period_0000.bin",
        "test": 0, "wavepos_st": 1, "wavepos_ed": int(scan["beam_count"]),
        "wavepos_skip": 1, "new_protocol_file_first_beam": 1,
        "new_protocol_file_scan_beam_count": int(scan["beam_count"]),
        "new_protocol_file_period_index": 0,
        "stage2_period_id": int(random["period_start"]),
        "scan_min_deg": float(scan["scan_min_deg"]),
        "scan_max_deg": float(scan["scan_min_deg"]) +
                        (int(scan["beam_count"]) - 1) * float(scan["scan_step_deg"]),
        "scan_step_deg": float(scan["scan_step_deg"]),
        "pf": args.pfa, "cfar_guard_cells": args.cfar_guard,
        "cfar_background_cells": args.cfar_background, "cfar_type": "GO",
        "cfar_doppler_circular": "true", "dynamic_cfar_enable": "false",
        "csi_detection_band_mode": "split", "csi_split_in_band_cfar_type": "GO",
        "csi_split_boundary_guard_rows": 4, "csi_split_merge_doppler_bins": 2,
        "csi_split_merge_range_bins": 2, "min_points": args.min_points,
        # 即使该可选通用小簇恢复处于关闭状态，生产 XML 校验仍要求它的
        # 最小点数不大于全局 min_points；min_points=2 时需同步下调默认 3。
        "cluster_strong_small_min_points": min(3, args.min_points),
        "csi_split_small_cluster_enable": "true",
        "csi_split_small_cluster_min_points": args.small_min_points,
        "csi_split_small_cluster_peak_over_median_db": args.small_peak_db,
        "csi_split_out_of_band_phase_filter_enable": "false",
        # CSI tap 不导出矩阵，只保存每周期实际使用的 P38 拟合参数；这些参数
        # 由 N-only 首轮测得，随后冻结到 S-only/S+N，避免目标改变自适应参考。
        "detection_results_csv_dump": 1, "csi_metrics_enable": "true",
        "csi_metrics_dump_power_maps": "false", "csi_metrics_beam_id": args.diagnostic_beam,
        "p38_diagnostics_dump": 0,
        "track_debug_dump": 1, "track_debug_dump_level": 1,
        "track_confirm_window": 3, "track_confirm_hits": 2,
        # Ground-truth files are for the offline evaluator only.  The
        # production detector must not receive a truth path, even through the
        # optional pulse-compression debug hook; otherwise a future debug
        # branch could accidentally change a detection decision.
        "debug_pc_peak": 0, "pc_peak_scene_truth": "",
        "pulse_num": int(wave["pulse_num"]),
    }
    freeze = getattr(args, "_processing_freeze", None) or {}
    if freeze.get("doppler_center_hz") is not None:
        values["doppler_center_override_hz"] = freeze["doppler_center_hz"]
    if freeze.get("p38_k") is not None and freeze.get("p38_b") is not None:
        values["p38_csi_override_k_rad_per_hz"] = freeze["p38_k"]
        values["p38_csi_override_b_rad"] = freeze["p38_b"]
    for tag, value in values.items():
        set_xml(root, tag, value)
    ensure_dir(destination.parent)
    tree.write(destination, encoding="utf-8", xml_declaration=True)
    return destination


def pipeline(scenario_path: Path, destination: Path, args: argparse.Namespace,
             label: str) -> dict[str, object]:
    scenario_path = scenario_path.resolve()
    spec = json.loads(scenario_path.read_text(encoding="utf-8"))
    stage2 = Path(spec["output_dir"]).resolve()
    result_dir = stage2 / "algorithm_result" / f"pipe_{int(spec['random']['period_count'])}period"
    pipe_xml = stage2 / "config" / f"gmti_standard_{label}.xml"
    run_command([str(args.build_dir / "simulate_stage2_statistical"), "--config", str(scenario_path)],
                destination / "logs" / "simulate_stage2.log")
    source_xml = stage2 / "config" / "temp_config_stage2_period_0000.xml"
    if not source_xml.is_file():
        raise RuntimeError(f"Stage2 缺少首周期 XML：{source_xml}")
    patch_pipe_xml(source_xml, pipe_xml, stage2, spec, result_dir, args)
    raw_files = [stage2 / "data" / f"stage2_statistical_newprotocol_period_{period:04d}.bin"
                 for period in range(int(spec["random"]["period_start"]),
                                     int(spec["random"]["period_start"]) +
                                     int(spec["random"]["period_count"]))]
    missing = [str(path) for path in raw_files if not path.is_file()]
    if missing:
        raise RuntimeError("Stage2 raw 不完整：" + ", ".join(missing[:5]))
    ensure_dir(result_dir)
    echo_specs = [f"{index + 1}={path}" for index, path in enumerate(raw_files)]
    env = os.environ.copy()
    env["GMTI_CFAR_DUMP_BEAM"] = str(args.diagnostic_beam)
    pipe_command = [str(args.build_dir / "GMTI_pipe_core"), "--config", str(pipe_xml),
                    "--result-dir", str(result_dir), "--track-debug-dir", str(result_dir / "track_debug"),
                    "--track-debug-dump", "on", "--runtime-mode=debug",
                    "--runtime-diagnostics=on", "--local-test", *echo_specs]
    elapsed = run_command(pipe_command, destination / "logs" / "gmti_pipe_core.log", env)
    snapshots = [result_dir / f"detection_results_GMTI{index + 1:02d}.csv"
                 for index in range(len(raw_files))]
    if any(not path.is_file() for path in snapshots):
        raise RuntimeError(f"{label} 缺少逐周期 detection CSV")
    payloads = list((result_dir / "track_debug").rglob("track_output_payloads.csv"))
    if not payloads:
        raise RuntimeError(f"{label} 缺少 TrackManager payload 审计")
    manifest = {
        "label": label, "scenario": str(scenario_path),
        "scenario_sha256": sha256_file(scenario_path), "stage2": str(stage2),
        "pipe_xml": str(pipe_xml), "pipe_xml_sha256": sha256_file(pipe_xml),
        "result_dir": str(result_dir), "period_count": len(raw_files),
        "raw_sha256_before_cleanup": {str(path): sha256_file(path) for path in raw_files},
        "diagnostic_beam": args.diagnostic_beam, "elapsed_pipe_sec": elapsed,
        "track_output_payloads": [str(path) for path in payloads], "raw_removed": False,
        "commands": {"stage2": str(args.build_dir / "simulate_stage2_statistical"),
                     "pipe": str(args.build_dir / "GMTI_pipe_core")},
    }
    write_json(destination / "pipeline_manifest.json", manifest)
    return {"stage2": stage2, "result_dir": result_dir, "pipe_xml": pipe_xml,
            "raw_files": raw_files, "manifest": manifest, "destination": destination,
            "period_count": len(raw_files)}


def find_diag_file(debug: Path, result_id: int, beam: int, suffix: str) -> Path:
    stem = f"cfar_GMTI{result_id:02d}_beam{beam:03d}_{suffix}.f32"
    exact = debug / stem
    if exact.is_file():
        return exact
    candidates = sorted(debug.glob(f"cfar_GMTI{result_id}_beam{beam:03d}_{suffix}.f32"))
    if len(candidates) == 1:
        return candidates[0]
    raise RuntimeError(f"找不到唯一 GO 诊断图 result={result_id} beam={beam} suffix={suffix}: {candidates}")


def load_map(debug: Path, result_id: int, beam: int, suffix: str,
             shape: tuple[int, int]) -> np.ndarray:
    path = find_diag_file(debug, result_id, beam, suffix)
    raw = np.fromfile(path, dtype=np.float32)
    if raw.size != shape[0] * shape[1]:
        raise RuntimeError(f"GO 诊断图长度错误：{path} {raw.size}!={shape[0] * shape[1]}")
    return raw.reshape(shape)


def read_meta(debug: Path, result_id: int, beam: int) -> dict[str, float | str]:
    candidates = [debug / f"cfar_GMTI{result_id:02d}_beam{beam:03d}_meta.txt"]
    candidates.extend(sorted(debug.glob(f"cfar_GMTI{result_id}_beam{beam:03d}_meta.txt")))
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise RuntimeError(f"缺少 GO meta result={result_id} beam={beam}")
    values: dict[str, float | str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        parsed = fnum(value)
        values[key] = parsed if math.isfinite(parsed) else value.strip()
    return values


def processing_freeze(run: dict[str, object], period_count: int,
                       beam: int) -> dict[str, float]:
    """从首轮 N-only 生产诊断提取固定的 Doppler/P38 参考。"""
    debug = Path(run["result_dir"]) / "debug"
    centers: list[float] = []
    for result_id in range(1, period_count + 1):
        value = fnum(read_meta(debug, result_id, beam).get("doppler_axis_center_wrapped_hz"))
        if math.isfinite(value):
            centers.append(value)
    if not centers:
        raise RuntimeError("N-only 没有可冻结的 Doppler center")
    p38: list[tuple[float, float]] = []
    for manifest in sorted((Path(run["result_dir"]) / "csi_metrics").rglob("csi_roi_manifest.csv")):
        for row in read_csv(manifest):
            if inum(row.get("beam_id")) != beam:
                continue
            k, b = fnum(row.get("p38_k_rad_per_hz")), fnum(row.get("p38_b_rad"))
            if math.isfinite(k) and math.isfinite(b):
                p38.append((k, b))
    if not p38:
        raise RuntimeError("N-only CSI tap 没有可冻结的 P38 参数")
    return {"doppler_center_hz": float(np.median(centers)),
            "p38_k": float(np.median([item[0] for item in p38])),
            "p38_b": float(np.median([item[1] for item in p38]))}


def truth_rows(stage2: Path, target_id: str, beam: int) -> dict[int, dict[str, str]]:
    rows = read_csv(stage2 / "truth" / "truth_targets_by_beam.csv")
    result: dict[int, dict[str, str]] = {}
    for row in rows:
        if row.get("target_id") != target_id or inum(row.get("beam_id")) != beam:
            continue
        if row.get("visible", "1").lower() in {"0", "false"}:
            continue
        result[inum(row.get("period_id"))] = row
    return result


def map_truth_row(truth: dict[str, str], meta: dict[str, float | str],
                  rows: int, prf_hz: float) -> int:
    raw = inum(truth.get("row_truth"))
    # The production CFAR map is written after the Doppler axis is recentered
    # by the per-run Doppler center.  The Stage2 ``row_truth`` is the
    # packet-addressed/raw FFT row (and may be negative), so using it directly
    # is only valid for legacy maps that have no axis metadata.  Map the
    # physical Doppler truth onto the actual production axis whenever metadata
    # is available; fall back to the circular raw row otherwise.
    center, step = fnum(meta.get("doppler_axis_center_wrapped_hz")), fnum(meta.get("doppler_axis_row_step_hz"))
    if rows > 0 and math.isfinite(center) and math.isfinite(step) and step > 0.0:
        return int(math.floor((fnum(truth.get("af_total_truth_hz")) -
                               (center - 0.5 * prf_hz)) / step + 0.5)) % rows
    if raw != -1:
        return raw % rows
    raise RuntimeError("truth 没有可用 Doppler 行")


def go_means(power: np.ndarray, row: int, col: int,
             guard: int, background: int) -> tuple[float, float, float, float, float]:
    rows, cols = power.shape
    outer = guard + background
    if not (0 <= row < rows and outer <= col < cols - outer):
        raise RuntimeError(f"固定支撑 ({row},{col}) 越出 GO 窗")
    rr = np.arange(row - outer, row + outer + 1) % rows
    left = float(np.mean(power[np.ix_(rr, np.arange(col - outer, col - guard))]))
    right = float(np.mean(power[np.ix_(rr, np.arange(col + guard + 1, col + outer + 1))]))
    top = float(np.mean(power[np.ix_(np.arange(row - outer, row - guard) % rows,
                                     np.arange(col - outer, col + outer + 1))]))
    bottom = float(np.mean(power[np.ix_(np.arange(row + guard + 1, row + outer + 1) % rows,
                                       np.arange(col - outer, col + outer + 1))]))
    return left, right, top, bottom, max(left, right, top, bottom)


def support_power_sum(power: np.ndarray, row: int, col: int,
                      row_half_width: int, col_half_width: int) -> float:
    """固定真值邻域的能量和，不按 S+N 峰值重新选点。"""
    rows, cols = power.shape
    rr = (row + np.arange(-row_half_width, row_half_width + 1)) % rows
    cc = np.arange(col - col_half_width, col + col_half_width + 1)
    if cc[0] < 0 or cc[-1] >= cols:
        raise RuntimeError(f"输出 SNR 固定支撑窗口越界 ({row},{col})")
    value = float(np.sum(power[np.ix_(rr, cc)], dtype=np.float64))
    return value if math.isfinite(value) and value >= 0.0 else float("nan")


def local_s_floor(power: np.ndarray, row: int, col: int,
                  guard: int, background: int) -> float:
    """Estimate the S-only local floor outside the production guard square."""
    outer = int(guard) + int(background)
    values = [float(power[(row + dr) % power.shape[0], col + dc])
              for dr in range(-outer, outer + 1)
              for dc in range(-outer, outer + 1)
              if not (-guard <= dr <= guard and -guard <= dc <= guard)
              and 0 <= col + dc < power.shape[1]]
    values = [value for value in values if math.isfinite(value) and value >= 0.0]
    return float(np.median(np.asarray(values, dtype=float))) if values else 0.0


def collect_target_support_measurements(s_debug: Path, cn_debug: Path,
                                         s_supports: list[dict[str, object]],
                                         cn_supports: list[dict[str, object]],
                                         args: argparse.Namespace,
                                         pulse_num: int, range_bins: int,
                                         angle: float) -> list[dict[str, object]]:
    """Extract compact, truth-independent S-only/C+N-only 3x5 shapes.

    The returned rows are calibration evidence only.  They contain no maps or
    raw samples, and are never passed to the S+N detector.  Each chain uses
    its own runtime Doppler-axis support mapping; only the physical period id
    pairs the two offline measurements.
    """
    cn_by_period = {int(item["period_id"]): item for item in cn_supports}
    group = f"{int(round(angle)):d}deg_{'edge' if abs(float(args.target_angle_offset_deg)) > 1e-9 else 'center'}"
    rows: list[dict[str, object]] = []
    for support in s_supports:
        period = int(support["period_id"])
        cn_support = cn_by_period.get(period)
        if cn_support is None:
            continue
        s_rid = int(support["result_id"]); cn_rid = int(cn_support["result_id"])
        s_row, s_col = int(support["support_row"]), int(support["support_col"])
        cn_row, cn_col = int(cn_support["support_row"]), int(cn_support["support_col"])
        s_power = load_map(s_debug, s_rid, args.diagnostic_beam, "power", (pulse_num, range_bins))
        cn_power = load_map(cn_debug, cn_rid, args.diagnostic_beam, "power", (pulse_num, range_bins))
        floor = local_s_floor(s_power, s_row, s_col, args.cfar_guard, args.cfar_background)
        signal: list[float] = []; background: list[float] = []
        positions: list[tuple[int, int]] = []
        valid = True
        for dr in (-1, 0, 1):
            for dc in (-2, -1, 0, 1, 2):
                sr, sc = (s_row + dr) % s_power.shape[0], s_col + dc
                cr, cc = (cn_row + dr) % cn_power.shape[0], cn_col + dc
                if not (0 <= sc < s_power.shape[1] and 0 <= cc < cn_power.shape[1]):
                    valid = False; break
                value = float(s_power[sr, sc]) - floor
                means = go_means(cn_power, cr, cc, args.cfar_guard, args.cfar_background)
                signal.append(max(value, 0.0)); background.append(max(float(means[-1]), 0.0))
                positions.append((dr, dc))
            if not valid:
                break
        if not valid or len(signal) != 15 or len(background) != 15:
            continue
        signal_arr = np.asarray(signal, dtype=float); background_arr = np.asarray(background, dtype=float)
        p0_ref = float(np.median(background_arr[background_arr > 0.0])) if np.any(background_arr > 0.0) else float("nan")
        signal_sum = float(np.sum(signal_arr))
        gamma_ref = signal_sum / p0_ref if math.isfinite(p0_ref) and p0_ref > 0.0 else float("nan")
        scnr_db = linear_to_db(gamma_ref) if math.isfinite(gamma_ref) and gamma_ref > 0.0 else float("nan")
        rows.append({
            "angle_group": group, "angle_deg": float(angle),
            "position": "edge" if "edge" in group else "center",
            "seed": int(args.seed), "period_id": period,
            "s_only_support_row": s_row, "s_only_support_col": s_col,
            "cn_support_row": cn_row, "cn_support_col": cn_col,
            "s_only_floor": floor, "support_signal_sum": signal_sum,
            "support_background_ref": p0_ref, "support_scnr_linear": gamma_ref,
            "support_scnr_db": scnr_db,
            "qualified_scnr_ge_1db": int(math.isfinite(scnr_db) and scnr_db >= 1.0),
            **{f"kappa_{i:02d}": float(signal_arr[i] / max(signal_sum, 1e-30)) for i in range(15)},
            **{f"beta_{i:02d}": float(background_arr[i] / max(p0_ref, 1e-30)) for i in range(15)},
            **{f"support_row_offset_{i:02d}": int(positions[i][0]) for i in range(15)},
            **{f"support_col_offset_{i:02d}": int(positions[i][1]) for i in range(15)},
        })
    return rows


def write_target_support_profile(root: Path, measurements: list[dict[str, object]]) -> None:
    """Write a median calibration profile without retaining any power map."""
    write_csv(root / "target_support_measurements.csv", measurements)
    profiles: list[dict[str, object]] = []
    groups = sorted({str(row["angle_group"]) for row in measurements})
    for group in groups:
        all_rows = [row for row in measurements if str(row["angle_group"]) == group]
        qualified = [row for row in all_rows if int(row.get("qualified_scnr_ge_1db", 0))]
        if not qualified:
            qualified = all_rows
        kappa = np.median(np.asarray([[fnum(row.get(f"kappa_{i:02d}")) for i in range(15)] for row in qualified]), axis=0)
        beta = np.median(np.asarray([[fnum(row.get(f"beta_{i:02d}")) for i in range(15)] for row in qualified]), axis=0)
        kappa = np.maximum(kappa, 0.0); kappa /= max(float(np.sum(kappa)), 1e-30)
        beta = np.maximum(beta, 1e-6); beta /= max(float(np.median(beta)), 1e-30)
        profiles.append({
            "angle_group": group,
            "angle_deg": float(all_rows[0]["angle_deg"]),
            "position": str(all_rows[0]["position"]),
            "sample_count": len(all_rows),
            "qualified_count_cut_gamma_ref_ge_1": len(qualified),
            "qualified_scnr_db_median": float(np.median([fnum(row.get("support_scnr_db")) for row in qualified])),
            "profile_source": "same-run S-only/C+N-only compact 3x5 measurements; evaluation hits unused",
            **{f"kappa_{i:02d}": float(kappa[i]) for i in range(15)},
            **{f"beta_{i:02d}": float(beta[i]) for i in range(15)},
        })
    write_csv(root / "target_support_profiles.csv", profiles)


def hit_component_cells(hits: np.ndarray, row: int, col: int,
                        max_range_gap: int = 2) -> list[tuple[int, int]]:
    """复现生产 GO 聚类的 3×(2*gap+1) 连通邻域。"""
    rows, cols = hits.shape
    if not (0 <= row < rows and 0 <= col < cols) or hits[row, col] <= 0.0:
        return []
    todo = [(row, col)]
    seen = {(row, col)}
    while todo:
        current_row, current_col = todo.pop()
        for delta_row in (-1, 0, 1):
            neighbor_row = (current_row + delta_row) % rows
            for delta_col in range(-max_range_gap, max_range_gap + 1):
                if delta_row == 0 and delta_col == 0:
                    continue
                neighbor_col = current_col + delta_col
                if not (0 <= neighbor_col < cols) or hits[neighbor_row, neighbor_col] <= 0.0:
                    continue
                key = (neighbor_row, neighbor_col)
                if key not in seen:
                    seen.add(key)
                    todo.append(key)
    return sorted(seen)


def production_cluster_event(power: np.ndarray, hits: np.ndarray, row: int, col: int,
                              component_size: int, args: argparse.Namespace) -> int:
    """按当前生产规则判定 truth 附近的 cluster event。

    基线是 min_points=6；若只有 3--5 点，则使用 CSI split 小簇的
    local-background +20 dB 峰值要求（near Doppler=4、near range=3，
    与 patch_pipe_xml 的生产默认值一致）。这只评估 truth 处的联合 GO hit
    mask，不把 target matching/relocation 混入 cluster 层。
    """
    if component_size >= args.min_points:
        return 1
    if component_size < args.small_min_points:
        return 0
    component = set(hit_component_cells(hits, row, col, max_range_gap=2))
    if not component:
        return 0
    positive = power[np.isfinite(power) & (power > 0.0)]
    if positive.size == 0:
        return 0
    rr0, cc0 = row, col
    background: list[float] = []
    for dr in range(-4, 5):
        rr = (rr0 + dr) % power.shape[0]
        for dc in range(-3, 4):
            cc = cc0 + dc
            if not (0 <= cc < power.shape[1]) or hits[rr, cc] > 0.0 or (rr, cc) in component:
                continue
            value = float(power[rr, cc])
            if math.isfinite(value) and value > 0.0:
                background.append(value)
    reference = float(np.median(np.asarray(background, dtype=float))) if len(background) >= 3 else float(np.median(positive))
    peak = max(float(power[rr, cc]) for rr, cc in component)
    return int(math.isfinite(reference) and reference > 0.0 and
               peak >= reference * 10.0 ** (args.small_peak_db / 10.0))


def frozen_support(reference: dict[str, object], run: dict[str, object],
                   scenario: dict, args: argparse.Namespace) -> list[dict[str, object]]:
    stage2, result_dir = Path(run["stage2"]), Path(run["result_dir"])
    debug = result_dir / "debug"
    target_truth = truth_rows(stage2, "STD_FIXED_TARGET", args.diagnostic_beam)
    pulse_num = int(scenario["waveform"]["pulse_num"])
    range_bins = int(scenario["range_processing"]["range_crop_len"])
    prf = float(scenario["waveform"]["prf_hz"])
    supports: list[dict[str, object]] = []
    period_count = int(run.get("period_count", run["manifest"]["period_count"]))
    for result_id in range(1, period_count + 1):
        period = result_id - 1
        truth = target_truth.get(period)
        if truth is None:
            raise RuntimeError(f"S-only 高信号参考缺少 period={period} truth")
        meta = read_meta(debug, result_id, args.diagnostic_beam)
        row = map_truth_row(truth, meta, pulse_num, prf)
        col = inum(truth.get("range_bin"), inum(truth.get("expected_bin")))
        power = load_map(debug, result_id, args.diagnostic_beam, "power", (pulse_num, range_bins))
        row_offsets = np.arange(-args.reference_row_half_width,
                                args.reference_row_half_width + 1)
        col_offsets = np.arange(-args.reference_col_half_width,
                                args.reference_col_half_width + 1)
        rows = (row + row_offsets) % pulse_num
        cols = col + col_offsets
        if cols[0] < 0 or cols[-1] >= range_bins:
            raise RuntimeError(f"S-only reference CUT range 越界 period={period}")
        window = power[np.ix_(rows, cols)]
        index = int(np.argmax(window))
        peak_r, peak_c = np.unravel_index(index, window.shape)
        support_row, support_col = int(rows[peak_r]), int(cols[peak_c])
        # Doppler 轴按环绕索引存储；把环绕后的差值还原为最小有符号偏移，
        # 否则真值落在首/尾行时会把 -1 误记成 rows-1。
        signed_row_offset = int(((support_row - row + pulse_num // 2) % pulse_num) - pulse_num // 2)
        supports.append({"result_id": result_id, "period_id": period,
                         "truth_row": row, "truth_col": col,
                         "support_row": support_row, "support_col": support_col,
                         "support_row_offset": signed_row_offset,
                         "support_col_offset": int(support_col - col),
                         "truth_angle_deg": fnum(truth.get("theta_cmd_deg")),
                         "physical_truth_angle_deg": fnum(truth.get("azimuth_deg")),
                         "truth_doppler_hz": fnum(truth.get("af_total_truth_hz")),
                         "truth_target_snr_db": fnum(truth.get("snr_db"))})
    write_csv(Path(reference["destination"]) / "frozen_support.csv", supports)
    return supports


def trajectory_support(reference_support: list[dict[str, object]],
                       run: dict[str, object], scenario: dict,
                       args: argparse.Namespace, destination: Path,
                       output_name: str = "frozen_support.csv") -> list[dict[str, object]]:
    """把高 S-only 参考得到的逐 period 局部支撑偏移复制到目标运行。

    不能让 S-only 运行覆盖完整 150-period：纯信号输入没有背景时 GO 阈值会
    退化为零，生产算法会产生海量假警。这里仅在一个高信号 period 选一次
    主瓣相对 truth 的偏移，随后对 N-only 的每个 period 使用同一偏移，既
    保留固定支撑定义，也把磁盘占用限制在可审计范围内。
    """
    if not reference_support:
        raise RuntimeError("高 S-only 参考没有冻结支撑")
    # 高 S-only 参考覆盖完整目标轨迹时，按 period 复用每个物理位置的
    # 主瓣偏移；只保留一个 period 的旧结果则退化为首 period 偏移。
    reference_by_period = {int(item["period_id"]): item for item in reference_support}
    reference0 = reference_support[0]
    stage2, result_dir = Path(run["stage2"]), Path(run["result_dir"])
    debug = result_dir / "debug"
    target_truth = truth_rows(stage2, "STD_FIXED_TARGET", args.diagnostic_beam)
    pulse_num = int(scenario["waveform"]["pulse_num"])
    range_bins = int(scenario["range_processing"]["range_crop_len"])
    prf = float(scenario["waveform"]["prf_hz"])
    period_count = int(run.get("period_count", run["manifest"]["period_count"]))
    supports: list[dict[str, object]] = []
    for result_id in range(1, period_count + 1):
        period = result_id - 1
        truth = target_truth.get(period)
        if truth is None:
            raise RuntimeError(f"N-only 固定支撑缺少 period={period} truth")
        reference = reference_by_period.get(period, reference0)
        row_offset = int(reference["support_row_offset"])
        col_offset = int(reference["support_col_offset"])
        meta = read_meta(debug, result_id, args.diagnostic_beam)
        # N-only 的自适应 Doppler center 在无目标背景下会随 realization
        # 随机漂移（30° smoke 中出现约 ±650 Hz 的跳变）。后续 reference、
        # S-only 和 S+N 都使用 processing_freeze 的同一 center；支撑表也
        # 必须用这个冻结值映射，不能把 N-only 每周期的噪声中心当成物理
        # 目标 bin，否则同一目标会在校准档之间被错移 100+ 个 Doppler bin。
        frozen_center = fnum(getattr(args, "_processing_freeze", {}).get("doppler_center_hz"))
        if math.isfinite(frozen_center):
            meta["doppler_axis_center_wrapped_hz"] = frozen_center
        row = map_truth_row(truth, meta, pulse_num, prf)
        col = inum(truth.get("range_bin"), inum(truth.get("expected_bin")))
        support_row = (row + row_offset) % pulse_num
        support_col = col + col_offset
        if support_col < 0 or support_col >= range_bins:
            raise RuntimeError(f"固定支撑 range 越界 period={period}: {support_col}")
        supports.append({"result_id": result_id, "period_id": period,
                         "truth_row": row, "truth_col": col,
                         "support_row": support_row, "support_col": support_col,
                         "support_row_offset": row_offset,
                         "support_col_offset": col_offset,
                         "truth_angle_deg": fnum(truth.get("theta_cmd_deg")),
                         "physical_truth_angle_deg": fnum(truth.get("azimuth_deg")),
                         "truth_doppler_hz": fnum(truth.get("af_total_truth_hz")),
                         "truth_target_snr_db": fnum(truth.get("snr_db"))})
    write_csv(destination / output_name, supports)
    return supports


def cleanup_maps(result_dir: Path) -> list[str]:
    removed: list[str] = []
    # 所有逐周期调试图（CFAR map 以及 paired_range_phase raw/CSI）都只在
    # extraction/audit 阶段使用；正式结果仅保留根目录 CSV/JSON/图表，避免
    # 留下无法复现且体积较大的 F32 中间物。
    for path in sorted((result_dir / "debug").glob("*.f32")):
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    return removed


def mark_raw_removed(run: dict[str, object]) -> None:
    manifest_path = Path(run["destination"]) / "pipeline_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    removed = []
    for path in run["raw_files"]:
        path = Path(path)
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    manifest["raw_removed"] = True
    manifest["raw_removed_paths"] = removed
    write_json(manifest_path, manifest)


def cleanup_algorithm_output_bins(run: dict[str, object]) -> list[str]:
    """Remove per-period algorithm BIN outputs after all compact audits finish.

    The Stage2 input BINs are handled by :func:`mark_raw_removed`; the pipe
    executable also emits ``GMTI*.bin`` files under its result directory.  They
    are not needed by the retained CSV/track-debug evidence and can be removed
    only after the caller has completed every extraction that reads them.
    """
    result_dir = Path(run["result_dir"])
    removed: list[str] = []
    for path in sorted(result_dir.rglob("GMTI*.bin")):
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    manifest_path = Path(run["destination"]) / "pipeline_manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["algorithm_output_bin_removed"] = True
        manifest["algorithm_output_bin_removed_paths"] = removed
        write_json(manifest_path, manifest)
    return removed


def cleanup_intermediate_outputs(run: dict[str, object]) -> list[str]:
    """Remove per-cycle directories after all audits and extractions complete.

    Root-level summary CSV/figures live beside ``destination`` and are not
    touched.  Keep the scenario, pipeline manifest and formal XML so the
    retained audit record still identifies the run configuration.
    """
    destination = Path(run["destination"])
    stage2 = Path(run["stage2"])
    removed: list[str] = []
    for path in (
        destination / "logs", destination / "period_metrics",
        stage2 / "logs", stage2 / "debug", stage2 / "reports", stage2 / "figures",
        stage2 / "data", stage2 / "truth", stage2 / "algorithm_result",
    ):
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(str(path))
    for path in sorted((stage2 / "config").glob("temp_config_stage2_period_*.xml")):
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    return removed


def extract_period_metrics(run: dict[str, object], supports: list[dict[str, object]],
                           args: argparse.Namespace, label: str) -> list[dict[str, object]]:
    stage2, result_dir, pipe_xml = Path(run["stage2"]), Path(run["result_dir"]), Path(run["pipe_xml"])
    out = ensure_dir(Path(run["destination"]) / "period_metrics")
    rows: list[dict[str, object]] = []
    for support in supports:
        result_id, period = int(support["result_id"]), int(support["period_id"])
        detection = result_dir / f"detection_results_GMTI{result_id:02d}.csv"
        period_out = out / f"period_{period:04d}"
        command = [sys.executable, str(SCRIPT_DIR / "05_extract_go_diagnostics.py"),
                   "--stage2-dir", str(stage2), "--algorithm-dir", str(result_dir),
                   "--output-dir", str(period_out), "--diagnostic-beams", str(args.diagnostic_beam),
                   "--diagnostic-result-id", str(result_id), "--period-id", str(period),
                   "--detection-csv", str(detection), "--production-xml", str(pipe_xml)]
        run_command(command, period_out / "extract_command.log")
        target_path = period_out / "target_diagnostics.csv"
        extracted = read_csv(target_path)
        if len(extracted) != 1:
            raise RuntimeError(f"{label} period={period} target diagnostics 行数={len(extracted)}")
        row = extracted[0]
        raw_target_event = inum(row.get("truth_matched"), inum(row.get("final_output"), 0))
        rows.append({**support, "label": label,
                     "unit_exact": inum(row.get("unit_go_cfar_exact_cut_hit"), 0),
                     "unit_resolution": inum(row.get("unit_go_cfar_hit"), 0),
                     "unit_hit_row_offset": inum(row.get("unit_go_cfar_hit_row_offset"), 0),
                     "unit_hit_col_offset": inum(row.get("unit_go_cfar_hit_range_offset"), 0),
                     "unit_component_size": inum(row.get("unit_go_hit_component_size"), 0),
                     # 先保存 component 证据；最终 cluster_event 在主循环中
                     # 读取完整 hit/power 图后按生产小簇规则判定。
                     "cluster_event": 0,
                     # This is the raw production truth-match result.  The
                     # final target event is gated by the locally audited
                     # cluster event after the joint GO hit map is read;
                     # retain the raw value to make any rejected stale-ID or
                     # coordinate match visible in the period CSV.
                     "target_event_raw": raw_target_event,
                     "target_event": raw_target_event,
                     "final_output": inum(row.get("final_output"), 0),
                     "target_select_hit": inum(row.get("final_output"), 0),
                     "target_select_source": "production_detection_truth_match_or_bounded_fallback",
                     "unit_cut_power": fnum(row.get("unit_cut_power")),
                     "unit_cfar_train_mean": fnum(row.get("unit_cfar_train_mean")),
                     "unit_cfar_left_mean": fnum(row.get("unit_cfar_left_mean")),
                     "unit_cfar_right_mean": fnum(row.get("unit_cfar_right_mean")),
                     "unit_cfar_top_mean": fnum(row.get("unit_cfar_top_mean")),
                     "unit_cfar_bottom_mean": fnum(row.get("unit_cfar_bottom_mean")),
                     "unit_cfar_threshold": fnum(row.get("unit_cfar_threshold")),
                     "unit_scnr_det_out_db": fnum(row.get("unit_scnr_det_out_db")),
                     "angle_error_deg": fnum(row.get("angle_error_deg")),
                     "angle_est_deg": fnum(row.get("angle_est_deg")),
                     "phase_error_rad": fnum(row.get("phase_error_rad")),
                     "cluster_size_selected": inum(row.get("cluster_size"), 0),
                     "scnr_det_out_legacy": fnum(row.get("scnr_det_out_db")),
                     "extract_source": str(target_path)})
    return rows


def rmse(values: list[float]) -> float:
    finite = [x for x in values if math.isfinite(x)]
    return float(math.sqrt(np.mean(np.square(finite)))) if finite else float("nan")


def aggregate_curves(period_rows: list[dict[str, object]], groups: list[dict[str, object]],
                     controls: list[float], calibration_map: dict[float, float],
                     calibration_cut_map: dict[float, float],
                     p0_mean: float, args: argparse.Namespace) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    group_rows: list[dict[str, object]] = []
    for group in groups:
        start, count = int(group["period_start"]), int(group["period_count"])
        values = period_rows[start:start + count]
        if len(values) != 3:
            raise RuntimeError(f"group={group['group_id']} 不足三屏")
        output_values = [fnum(x.get("output_scnr_db")) for x in values]
        target_hits = [inum(x.get("target_event")) for x in values]
        cut_values = [fnum(x.get("unit_cut_scnr_db")) for x in values]
        group_rows.append({
            **group, "requested_output_scnr_db": calibration_map[float(group["input_snr_db"])],
            "output_scnr_db": float(np.mean([x for x in output_values if math.isfinite(x)]))
            if any(math.isfinite(x) for x in output_values) else float("nan"),
            "support_output_scnr_db": float(np.mean([x for x in output_values if math.isfinite(x)]))
            if any(math.isfinite(x) for x in output_values) else float("nan"),
            "output_scnr_cut_equiv_db": float(np.mean([x for x in cut_values if math.isfinite(x)]))
            if any(math.isfinite(x) for x in cut_values) else float("nan"),
            "screen1_hit": target_hits[0], "screen2_hit": target_hits[1], "screen3_hit": target_hits[2],
            "track_2of3_hit": int(sum(target_hits) >= 2),
            "screen_hit_sum": sum(target_hits),
            "angle_rmse_deg": rmse([fnum(x.get("angle_error_deg")) for x in values]),
        })
    curve_rows: list[dict[str, object]] = []
    for level_index, input_snr in enumerate(controls):
        rows = [row for row in group_rows if int(row["level_index"]) == level_index]
        screens = [row for row in period_rows if int(row["input_snr_db_index"]) == level_index]
        # Main unit Pd is the exact projected CUT so it remains directly
        # comparable with the noncentral/IID GO formula.  The bounded
        # +/-2-bin production-resolution event is reported separately; it is
        # the appropriate operational event when FFT/main-lobe quantisation
        # moves a target to an adjacent cell.
        unit_values = [inum(row.get("unit_exact")) for row in screens]
        resolution_values = [inum(row.get("unit_resolution")) for row in screens]
        cluster_values = [inum(row.get("cluster_event")) for row in screens]
        target_values = [inum(row.get("target_event")) for row in screens]
        matched_errors = [fnum(row.get("angle_error_deg")) for row in screens]
        finite_errors = [x for x in matched_errors if math.isfinite(x)]
        unit_hits, resolution_hits = sum(unit_values), sum(resolution_values)
        cluster_hits, target_hits = sum(cluster_values), sum(target_values)
        track_hits = sum(inum(row.get("track_2of3_hit")) for row in rows)
        unit_low, unit_high = wilson_interval(unit_hits, len(unit_values))
        resolution_low, resolution_high = wilson_interval(resolution_hits, len(resolution_values))
        target_low, target_high = wilson_interval(target_hits, len(target_values))
        track_low, track_high = wilson_interval(track_hits, len(rows))
        curve_rows.append({
            "level_index": level_index, "input_snr_db_control": input_snr,
            "requested_output_scnr_db": calibration_cut_map[input_snr],
            "requested_support_output_scnr_db": calibration_map[input_snr],
            "output_scnr_db": float(np.mean([fnum(row.get("output_scnr_cut_equiv_db")) for row in rows])) if rows else float("nan"),
            "output_scnr_cut_equiv_db": float(np.mean([fnum(row.get("output_scnr_cut_equiv_db")) for row in rows])) if rows else float("nan"),
            "support_output_scnr_db": float(np.mean([fnum(row.get("support_output_scnr_db")) for row in rows])) if rows else float("nan"),
            "output_scnr_db_std": float(np.std([fnum(row.get("output_scnr_cut_equiv_db")) for row in rows], ddof=1)) if len(rows) > 1 else float("nan"),
            "support_output_scnr_db_std": float(np.std([fnum(row.get("support_output_scnr_db")) for row in rows], ddof=1)) if len(rows) > 1 else float("nan"),
            "unit_hits": unit_hits, "unit_trials": len(unit_values), "unit_pd": unit_hits / len(unit_values) if unit_values else float("nan"),
            "unit_wilson_low": unit_low, "unit_wilson_high": unit_high,
            "unit_exact_hits": unit_hits, "unit_exact_trials": len(unit_values),
            "unit_exact_pd": unit_hits / len(unit_values) if unit_values else float("nan"),
            "unit_resolution_hits": resolution_hits, "unit_resolution_trials": len(resolution_values),
            "unit_resolution_pd": resolution_hits / len(resolution_values) if resolution_values else float("nan"),
            "unit_resolution_wilson_low": resolution_low,
            "unit_resolution_wilson_high": resolution_high,
            "cluster_hits": cluster_hits, "cluster_trials": len(cluster_values), "cluster_pd": cluster_hits / len(cluster_values) if cluster_values else float("nan"),
            "target_hits": target_hits, "target_trials": len(target_values), "target_pd": target_hits / len(target_values) if target_values else float("nan"),
            "target_wilson_low": target_low, "target_wilson_high": target_high,
            "track_hits": track_hits, "track_trials": len(rows), "track_pd": track_hits / len(rows) if rows else float("nan"),
            "track_wilson_low": track_low, "track_wilson_high": track_high,
            "angle_matched": len(finite_errors), "angle_rmse_deg": rmse(finite_errors),
            "angle_bias_deg": float(np.mean(finite_errors)) if finite_errors else float("nan"),
            "p0_mean_linear": p0_mean,
        })
    return group_rows, curve_rows


def transition_stats(group_rows: list[dict[str, object]]) -> dict[str, float]:
    hits = [inum(row.get("screen1_hit")) for row in group_rows]
    hits.extend(inum(row.get("screen2_hit")) for row in group_rows)
    hits.extend(inum(row.get("screen3_hit")) for row in group_rows)
    if len(hits) < 2:
        return {"p_hit_given_prev_hit": float("nan"), "p_hit_given_prev_miss": float("nan"), "correlation": float("nan")}
    prev, curr = hits[:-1], hits[1:]
    hit_prev = [curr[i] for i, x in enumerate(prev) if x == 1]
    miss_prev = [curr[i] for i, x in enumerate(prev) if x == 0]
    corr = float(np.corrcoef(prev, curr)[0, 1]) if np.std(prev) > 0 and np.std(curr) > 0 else float("nan")
    return {"p_hit_given_prev_hit": len([x for x in hit_prev if x]) / len(hit_prev) if hit_prev else float("nan"),
            "p_hit_given_prev_miss": len([x for x in miss_prev if x]) / len(miss_prev) if miss_prev else float("nan"),
            "correlation": corr}


def plot_and_report(destination: Path, angle: float, curve: list[dict[str, object]],
                    calibration: list[dict[str, object]], p0_rows: list[dict[str, object]],
                    theory: list[dict[str, object]], manifest: dict[str, object],
                    transitions: dict[str, float], args: argparse.Namespace) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures = ensure_dir(destination / "figures")
    x = np.asarray([fnum(row["output_scnr_db"]) for row in curve])
    def save_curve(name: str, field: str, ylabel: str, title: str, lower: str | None = None) -> None:
        fig, ax = plt.subplots(figsize=(7.2, 4.4))
        y = np.asarray([fnum(row.get(field)) for row in curve])
        ax.plot(x, y, "o-", label="受控 S+N Monte Carlo")
        if lower:
            lo = np.asarray([fnum(row.get(lower)) for row in curve])
            ax.fill_between(x, lo, y, alpha=.18, label="Wilson 95% 下界")
        finite = np.isfinite(x) & np.isfinite(y)
        tx = np.asarray([fnum(row.get("output_scnr_db")) for row in theory])
        ty = np.asarray([fnum(row.get(field)) for row in theory])
        keep = np.isfinite(tx) & np.isfinite(ty)
        if np.any(keep):
            ax.plot(tx[keep], ty[keep], "--", label="GO 理论/经验积分")
        ax.set_xlabel("CUT-equivalent 实际输出 SCNR (dB)")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(True, alpha=.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(figures / name, dpi=160)
        plt.close(fig)
    save_curve("output_snr_unit_pd.png", "unit_pd", "unit Pd", f"{angle:g}°：输出 SNR—单元 Pd", "unit_wilson_low")
    save_curve("output_snr_target_pd.png", "target_pd", "target Pd", f"{angle:g}°：输出 SNR—目标 Pd", "target_wilson_low")
    save_curve("output_snr_track_pd.png", "track_pd", "track 2/3 Pd", f"{angle:g}°：输出 SNR—三屏选二 Pd", "track_wilson_low")
    save_curve("output_snr_angle_rmse.png", "angle_rmse_deg", "angle RMSE (deg)", f"{angle:g}°：输出 SNR—测角 RMSE")
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    cx = [fnum(row.get("input_snr_db_control")) for row in calibration]
    cy = [fnum(row.get("measured_output_snr_db")) for row in calibration]
    py = [fnum(row.get("predicted_output_snr_db")) for row in calibration]
    ax.plot(cx, cy, "o-", label="S-only/P0 实测")
    ax.plot(cx, py, "--", label="线性校准")
    ax.set_xlabel("target_snr_db（仅控制变量）")
    ax.set_ylabel("S-only 支撑诊断 SCNR (dB)")
    ax.set_title(f"{angle:g}°：输出 SNR 校准 sanity check")
    ax.grid(True, alpha=.3)
    ax.legend(); fig.tight_layout(); fig.savefig(figures / "output_snr_calibration.png", dpi=160); plt.close(fig)
    p0 = np.asarray([fnum(row.get("p0_power")) for row in p0_rows])
    fig, ax = plt.subplots(figsize=(7.2, 4.4)); ax.hist(p0[np.isfinite(p0)], bins=20, alpha=.8)
    ax.axvline(float(np.nanmean(p0)), color="r", linestyle="--", label="P0 ensemble mean")
    ax.set_xlabel("固定支撑窗口 N-only 能量"); ax.set_ylabel("样本数"); ax.set_title(f"{angle:g}°：P0 ensemble")
    ax.grid(True, alpha=.3); ax.legend(); fig.tight_layout(); fig.savefig(figures / "p0_ensemble_distribution.png", dpi=160); plt.close(fig)

    rows = []
    for row in curve:
        rows.append("| %.3f | %.3f | %.3f | %d/%d | %.3f | %.3f | %.3f |" % (
            fnum(row["output_scnr_db"]), fnum(row["unit_pd"]), fnum(row["cluster_pd"]),
            inum(row["target_hits"]), inum(row["target_trials"]), fnum(row["target_pd"]),
            fnum(row["track_pd"]), fnum(row["angle_rmse_deg"])))
    table = "| CUT-equivalent output SCNR (dB) | unit Pd | cluster Pd | target hits | target Pd | track 2/3 Pd | angle RMSE (°) |\n|---:|---:|---:|---:|---:|---:|---:|\n" + "\n".join(rows)
    theory_note = ("理论虚线使用同一生产 GO alpha 的逐周期 mixture："
                   "主线以配对 S+N/N-only 的 truth-CUT 有效增量 |z_SN-z_N|²/P0 作为输出信号，"
                   "并使用同 period 的 C+N-only M/P0；S-only truth-CUT mixture 另存于 "
                   "go_theory_vs_mc.csv，作为独立支撑/量化诊断。固定 3×3 支撑 SCNR 仅作诊断。")
    background_profile = str(manifest.get("background_profile", args.background_profile))
    if background_profile == "compact_statistical":
        background_label = "紧凑统计面积杂波 + 热噪声（S+C+N/C+N-only）"
        baseline_reason = ("本轮启用 240 个 Rayleigh×lognormal 面积散射点和热噪声；"
                           "强散射体/线散射体关闭，以隔离真实非 IID 背景对 GO 门限的影响。")
    else:
        background_label = "热噪声-only（S+N/N-only）"
        baseline_reason = ("本轮将 scene.mode 设为 target_only、clutter_amplitude_scale=0，并保留 thermal_noise；"
                           "这不是最终复杂场景结论，而是可控的算法基线。")
    md = rf"""---
title: \"GMTI 受控输出 SNR—检测概率—测角 Monte Carlo（{angle:g}°）\"
CJKmainfont: WenQuanYi Micro Hei
geometry: margin=2cm
---

# 结论先行

本补充报告的背景模式为 **{background_label}**。角度为 {angle:g}°，GO guard={args.cfar_guard}、background={args.cfar_background}、PFA={args.pfa:g}、min_points={args.min_points}。正式横轴是生产物理 CUT 上的有效信号功率比 $(E[P_{{S+N,CUT}}]-E[P_{{N,CUT}}])/P_{{0,CUT}}$ 对应的 CUT-equivalent output SCNR，不使用 Stage2 target_snr_db 作为主图横轴；固定 3×3 支撑 SCNR 与 S-only 只作诊断。

校准斜率 = {fnum(manifest.get('calibration_slope')):.4f}，R² = {fnum(manifest.get('calibration_r2')):.5f}，最大中位偏差 = {fnum(manifest.get('calibration_max_abs_bias_db')):.3f} dB；sanity = **{'PASS' if manifest.get('calibration_sanity_pass') else 'FAIL'}**。P0 ensemble 样本数 = {len(p0_rows)}，固定支撑窗口均值 = {fnum(manifest.get('p0_mean_power')):.6g}，标准差 = {fnum(manifest.get('p0_std_power')):.6g}；中心 CUT 均值 = {fnum(manifest.get('p0_center_mean_power')):.6g}。

{table}

# 1. 为什么先做无杂波基线

S+C+N 中面积杂波的空间纹理、目标训练窗泄漏和前端自适应会同时改变信号功率与 GO 的四方向最大训练均值。若先直接把输入档映射成横轴，曲线不吻合时无法区分“输出 SNR 定义错误”和“检测算法问题”。{baseline_reason}

固定支撑定义为：高 S-only 参考在每个 period 的 physical truth 附近固定 ±{args.reference_row_half_width} Doppler × ±{args.reference_col_half_width} range 模板内取一次主瓣位置；该位置随后复制给 N-only 与 S+N，禁止按检测峰或目标输出重新选点。

# 2. 输出 SNR 定义与推导

令 $z_N(r,c)$ 和 $z_{{S+N}}(r,c)$ 分别是 N-only、S+N 在生产 GO 输入面的复数值，$c$ 是固定的物理 truth CUT，$W$ 是其固定 3×3 支撑诊断窗口。由于 CSI/杂波抑制可能依赖整幅输入，S-only 并不保证等于生产 S+N 中的目标增量。因此主轴采用 CUT 配对集合平均的有效目标功率

$$P_{{S,eff,CUT}}=E\left[|z_{{S+N}}(c)|^2\right]-E\left[|z_N(c)|^2\right],\qquad P_{{0,CUT}}=E\left[|z_N(c)|^2\right],\qquad SCNR_{{out,CUT}}=10\log_{{10}}(P_{{S,eff,CUT}}/P_{{0,CUT}}).$$

支撑诊断量另行定义为

$$SCNR_{{support}}=10\log_{{10}}\left((E[\sum_{{(r,c)\in W}}|z_{{S+N}}(r,c)|^2]-E[\sum_{{(r,c)\in W}}|z_N(r,c)|^2])/E[\sum_{{(r,c)\in W}}|z_N(r,c)|^2]\right).$$

它不是每个 S+N 图重新挑选的峰值单元；GO 理论只使用 CUT 归一化量。

该定义把“目标信号”和“背景功率”分开，$P_{{0,CUT}}$ 是 ensemble mean，不是某次瞬时 GO 最大训练均值。每个 period 的四方向均值 $M_r=\max(\bar P_L,\bar P_R,\bar P_T,\bar P_B)$ 另存用于经验积分，并以 $P_{{0,CUT}}$ 归一化：

$$P_d(\gamma)=E_r\left[Q_1(\sqrt{{2\gamma}},\sqrt{{2\alpha M_r/P_0}})\right].$$

{theory_note}

对 45° 这类存在离散 FFT bin 迁移的轨迹，不能用一个 batch-mean $\gamma$ 代替所有 period。脚本还输出 `go_theory_periods.csv`，逐行保存 $\gamma_r$、$m_r$ 和 Marcum-Q 值；主虚线对应

$$P_{{d,paired}}=\frac1R\sum_r Q_1\left(\sqrt{{2|z_{{S+N,r}}-z_{{N,r}}|^2/P_0}},\sqrt{{2\alpha m_r}}\right),$$

该式没有使用任何 unit/cluster/target hit 计数拟合参数，S-only 版本则单独记录为 `unit_pd_s_only_truth_mixture`。

# 3. 校准 sanity check

S-only 使用与 N-only 完全相同的噪声包来检查支撑位置和幅度平方律；正式映射直接由配对的 S+N/N-only 固定支撑集合平均得到。输入档仅是控制量，不能解释为输出 SNR。

![校准（S-only/支撑诊断）](figures/output_snr_calibration.png)

![P0（CUT/支撑背景诊断）](figures/p0_ensemble_distribution.png)

# 4. 四条主曲线

![unit](figures/output_snr_unit_pd.png)

![target](figures/output_snr_target_pd.png)

![track](figures/output_snr_track_pd.png)

![angle](figures/output_snr_angle_rmse.png)

# 5. 三屏相关性

三屏 truth 事件的转移统计为 $P(D_t=1|D_{{t-1}}=1)={transitions['p_hit_given_prev_hit']:.4f}$、$P(D_t=1|D_{{t-1}}=0)={transitions['p_hit_given_prev_miss']:.4f}$、相邻相关系数={transitions['correlation']:.4f}。主航迹事件是每个独立三屏组内至少两屏 target truth hit；TrackManager Confirmed+matched_this_frame 只留作附录审计。

# 6. 限制与下一步

本轮背景模式为 **{background_label}**。若为热噪声-only，它不能替代三层 GO 理论中的“真实 C+N 背景 + 目标训练窗泄漏”层；若为 compact_statistical，它仍是 240 点紧凑统计背景，不代表全场景强散射体/线杂波。两种模式都不支持把当前小样本结果写成最终 90% 门限。

复现入口：`python3 scripts/gmti_scnr_eval/16_run_standard_output_snr_mc.py --angle {angle:g} --trials-per-level {args.trials_per_level}`。
"""
    md_path = destination / "受控输出SNR_MonteCarlo报告.md"
    md_path.write_text(md, encoding="utf-8")
    if shutil.which("pandoc") and shutil.which("xelatex"):
        result = subprocess.run([shutil.which("pandoc") or "pandoc", str(md_path), "--resource-path", str(destination), "--pdf-engine", shutil.which("xelatex") or "xelatex", "-o", str(destination / "受控输出SNR_MonteCarlo报告.pdf")], cwd=ROOT, check=False)
        if result.returncode:
            (destination / "pdf_generation_failed.txt").write_text(f"exit={result.returncode}\n", encoding="utf-8")


def process_angle(angle: float, args: argparse.Namespace) -> dict[str, object]:
    root = ensure_dir(args.output_root.resolve() / f"angle_{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p"))
    levels = parse_grid(args.input_snr_grid)
    schedule, groups = target_schedule(levels, args.trials_per_level)
    n_periods = len(schedule)
    base_dir = ensure_dir(root / "base")
    target_angle = angle + args.target_angle_offset_deg
    base = make_base_scenario(base_dir, angle, args.seed, schedule,
                              f"standard_snr_cmd_{angle:g}_truth_{target_angle:g}", target_angle,
                              args.background_profile)
    spec = json.loads(base.read_text(encoding="utf-8"))
    cn_scenario = variant_scenario(base, root / "n_only", False, False, schedule, "n_only",
                                   truth_carrier_snr_db=-200.0)
    cn_run = pipeline(cn_scenario, root / "n_only", args, "n_only")
    # 受控模式可由同批 N-only 背景冻结 Doppler/P38，避免目标改变自适应参考。
    # 严格在线模式则完全不把预先运行的 N-only 结果写回生产 XML；N-only 只留作
    # 离线 output-SCNR 分母与 GO 背景统计，便于核查没有任何运行前先验进入 detector。
    args._processing_freeze = ({} if args.strict_online_no_prior else
                               processing_freeze(cn_run, n_periods, args.diagnostic_beam))
    # 纯 S-only 没有背景，GO 阈值会退化并产生海量假警；高信号参考只
    # 用于离线测量每个 period 的“主瓣相对 truth 的固定支撑偏移”。
    # 用完整轨迹的高 S-only 参考测量每个 period 的局部主瓣偏移。若只
    # 测首 period，range migration/FFT 分数行在后续 period 的确定性偏移
    # 会被误当作随机漏检；该参考仍只在离线评分中使用，不进入生产 XML。
    reference_schedule = [args.reference_input_snr_db] * n_periods
    reference_scenario = variant_scenario(base, root / "reference_s_only", True, True, reference_schedule, "reference_s_only")
    reference_run = pipeline(reference_scenario, root / "reference_s_only", args, "reference_s_only")
    reference_run["destination"] = root / "reference_s_only"
    reference_spec = json.loads(reference_scenario.read_text(encoding="utf-8"))
    reference_info = {"destination": str(root / "reference_s_only")}
    reference_support = frozen_support(reference_info, reference_run, reference_spec, args)
    # 每条生产链都用自身运行时的观测 Doppler 轴映射同一个物理支撑。
    # 严格模式不冻结 N-only 的轴中心，不能把 N-only 的行号直接复用给
    # S-only/S+N，否则会把目标评分到另一分辨单元。
    cn_supports = trajectory_support(reference_support, cn_run, spec, args, root)
    # Keep the support table and remove the high-S reference maps immediately.
    cleanup_maps(Path(reference_run["result_dir"]))
    mark_raw_removed(reference_run)
    cleanup_algorithm_output_bins(reference_run)
    cleanup_intermediate_outputs(reference_run)

    cn_run["destination"] = root / "n_only"
    pulse_num, range_bins = int(spec["waveform"]["pulse_num"]), int(spec["range_processing"]["range_crop_len"])
    debug = Path(cn_run["result_dir"]) / "debug"
    cn_debug = debug
    p0_rows: list[dict[str, object]] = []
    cn_support_complex: dict[int, complex] = {}
    m_rows: list[dict[str, object]] = []
    for support in cn_supports:
        rid, row, col = int(support["result_id"]), int(support["support_row"]), int(support["support_col"])
        truth_row, truth_col = int(support["truth_row"]), int(support["truth_col"])
        real = load_map(debug, rid, args.diagnostic_beam, "complex_real", (pulse_num, range_bins))
        imag = load_map(debug, rid, args.diagnostic_beam, "complex_imag", (pulse_num, range_bins))
        power = load_map(debug, rid, args.diagnostic_beam, "power", (pulse_num, range_bins))
        threshold = load_map(debug, rid, args.diagnostic_beam, "threshold", (pulse_num, range_bins))
        meta = read_meta(debug, rid, args.diagnostic_beam)
        unit_alpha = fnum(meta.get("cfar_alpha"))
        if not math.isfinite(unit_alpha) or unit_alpha <= 0.0:
            unit_alpha = GoCfarGeometry(args.cfar_guard, args.cfar_background).alpha(args.pfa)
        value = complex(float(real[row, col]), float(imag[row, col]))
        center_pwr = float(power[row, col])
        pwr = support_power_sum(power, row, col, args.output_support_row_half_width,
                                args.output_support_col_half_width)
        left, right, top, bottom, maximum = go_means(power, row, col, args.cfar_guard, args.cfar_background)
        production_threshold = float(threshold[row, col])
        if not math.isfinite(production_threshold) or production_threshold <= 0.0:
            raise RuntimeError(
                f"N-only production threshold map invalid at result={rid}, "
                f"support=({row},{col}): {production_threshold}")
        production_m = production_threshold / unit_alpha
        truth_center_pwr = float(power[truth_row, truth_col])
        truth_left, truth_right, truth_top, truth_bottom, truth_maximum = go_means(
            power, truth_row, truth_col, args.cfar_guard, args.cfar_background)
        truth_threshold = float(threshold[truth_row, truth_col])
        if not math.isfinite(truth_threshold) or truth_threshold <= 0.0:
            raise RuntimeError(
                f"N-only production threshold map invalid at result={rid}, "
                f"truth=({truth_row},{truth_col}): {truth_threshold}")
        truth_production_m = truth_threshold / unit_alpha
        cn_support_complex[rid] = value
        p0_rows.append({**support, "cn_complex_real": value.real, "cn_complex_imag": value.imag,
                        "p0_power": pwr, "p0_center_power": center_pwr,
                        "p0_truth_center_power": truth_center_pwr,
                        "p0_row_half_width": args.output_support_row_half_width,
                        "p0_col_half_width": args.output_support_col_half_width,
                        "go_left_mean": left, "go_right_mean": right,
                        "go_top_mean": top, "go_bottom_mean": bottom,
                        # The merged diagnostic power map can contain samples
                        # from two independent production CFAR branches in
                        # split mode.  Keep that offline recomputation for
                        # audit, but use the exported branch-correct
                        # threshold as the authoritative GO training level.
                        "go_m_mixed": maximum,
                        "go_threshold": production_threshold,
                        "go_m": production_m,
                        "go_truth_left_mean": truth_left, "go_truth_right_mean": truth_right,
                        "go_truth_top_mean": truth_top, "go_truth_bottom_mean": truth_bottom,
                        "go_truth_m": truth_production_m,
                        "go_truth_threshold": truth_threshold,
                        "go_threshold_source": "production_cfar_threshold_map"})
        m_rows.append({"result_id": rid, "period_id": int(support["period_id"]),
                       "go_m": production_m, "go_m_mixed": maximum,
                       "go_threshold": production_threshold,
                       "go_truth_m": truth_production_m,
                       "go_truth_threshold": truth_threshold})
    p0_values = np.asarray([float(row["p0_power"]) for row in p0_rows], dtype=float)
    p0_center_values = np.asarray([float(row["p0_center_power"]) for row in p0_rows], dtype=float)
    p0_truth_center_values = np.asarray([float(row["p0_truth_center_power"]) for row in p0_rows], dtype=float)
    p0_mean = float(np.mean(p0_values)); p0_std = float(np.std(p0_values, ddof=1))
    p0_center_mean = float(np.mean(p0_center_values)); p0_center_std = float(np.std(p0_center_values, ddof=1))
    p0_truth_center_mean = float(np.mean(p0_truth_center_values)); p0_truth_center_std = float(np.std(p0_truth_center_values, ddof=1))
    write_csv(root / "p0_ensemble.csv", p0_rows)

    # S-only 校准必须沿用与 N-only/S+N 完全相同的整条目标轨迹。
    # 旧实现把每个档位压缩成连续 3 个 period，再拿它去读取原轨迹
    # period_start（例如 level=20 dB 使用了 period=3..5 的地图，却
    # 套用了 N-only 的 period=120..122 支撑），导致固定支撑与目标的
    # range/Doppler 几何错配，出现“注入幅度增大而输出功率下降”。
    # 这里保留完整 schedule；每个档位使用全部独立三屏组作为标定样本，
    # 不能只取首组三屏，否则热噪声实现的偶然性会被错误地写成传递函数残差。
    cal_schedule = list(schedule)
    s_cal_scenario = variant_scenario(base, root / "s_only_calibration", True, True, cal_schedule, "s_only_calibration")
    s_cal_run = pipeline(s_cal_scenario, root / "s_only_calibration", args, "s_only_calibration")
    s_cal_run["destination"] = root / "s_only_calibration"
    s_cal_supports = trajectory_support(reference_support, s_cal_run, spec, args, root,
                                        output_name="frozen_support_s_only.csv")
    s_debug = Path(s_cal_run["result_dir"]) / "debug"
    # 在清理 S-only/C+N-only 诊断图之前，提取仅用于理论标定的紧凑
    # 3×5 footprint。它不读取 S+N 命中，也不写回生产配置；随后只保留
    # 标量/归一化形状表，便于跨 seed 汇总而不保留逐周期中间图。
    support_measurements = collect_target_support_measurements(
        s_debug, cn_debug, s_cal_supports, cn_supports, args,
        pulse_num, range_bins, angle)
    write_target_support_profile(root, support_measurements)
    calibration_rows: list[dict[str, object]] = []
    calibration_supports: list[dict[str, object]] = []
    calibration_levels: list[float] = []
    for level_index, group_value in enumerate(levels):
        for trial in range(args.trials_per_level):
            source_group = groups[level_index * args.trials_per_level + trial]
            source_start = int(source_group["period_start"])
            for replicate in range(3):
                source_support = s_cal_supports[source_start + replicate]
                # S-only 的 result_id 与完整 schedule 中的物理 period 一一对应，
                # 因而不再把不同轨迹位置错误地压缩到 period=0..14。
                calibration_supports.append({**source_support,
                                             "result_id": source_start + replicate + 1,
                                             "period_id": source_start + replicate})
                calibration_levels.append(float(group_value))
    for support, group_value in zip(calibration_supports, calibration_levels):
        rid, row, col = int(support["result_id"]), int(support["support_row"]), int(support["support_col"])
        truth_row, truth_col = int(support["truth_row"]), int(support["truth_col"])
        power = load_map(s_debug, rid, args.diagnostic_beam, "power", (pulse_num, range_bins))
        center_power = float(power[row, col])
        truth_center_power = float(power[truth_row, truth_col])
        support_power = support_power_sum(power, row, col, args.output_support_row_half_width,
                                          args.output_support_col_half_width)
        output = linear_to_db(support_power / p0_mean)
        calibration_rows.append({"result_id": rid, "period_id": int(support["period_id"]),
                                 "input_snr_db_control": float(group_value),
                                 "s_only_power": support_power,
                                 "s_only_center_power": center_power,
                                 "s_only_truth_center_power": truth_center_power,
                                 "p0_mean_power": p0_mean, "measured_output_snr_db": output})
    calibration_summary: list[dict[str, object]] = []
    x = np.asarray([fnum(row["input_snr_db_control"]) for row in calibration_rows])
    y = np.asarray([fnum(row["measured_output_snr_db"]) for row in calibration_rows])
    finite = np.isfinite(x) & np.isfinite(y)
    slope, intercept = (float(np.polyfit(x[finite], y[finite], 1)[0]), float(np.polyfit(x[finite], y[finite], 1)[1])) if np.sum(finite) >= 2 else (float("nan"), float("nan"))
    predicted = slope * x + intercept if math.isfinite(slope) else np.full_like(x, np.nan)
    residual = y - predicted
    r2 = 1.0 - float(np.sum(residual[finite] ** 2) / np.sum((y[finite] - np.mean(y[finite])) ** 2)) if np.sum(finite) >= 2 and np.sum((y[finite] - np.mean(y[finite])) ** 2) > 0 else float("nan")
    for row, pred in zip(calibration_rows, predicted):
        row["predicted_output_snr_db"] = float(pred)
    for level_index, level in enumerate(levels):
        values = [row for row in calibration_rows if abs(float(row["input_snr_db_control"]) - level) < 1e-6]
        measured = [fnum(row["measured_output_snr_db"]) for row in values]
        signal_values = [fnum(row["s_only_power"]) for row in values]
        center_signal_values = [fnum(row["s_only_center_power"]) for row in values]
        truth_center_signal_values = [fnum(row["s_only_truth_center_power"]) for row in values]
        calibration_summary.append({"level_index": level_index, "input_snr_db_control": level,
                                    "predicted_output_scnr_db": float(slope * level + intercept),
                                    "measured_output_snr_db": float(np.mean(measured)),
                                    "measured_output_snr_db_std": float(np.std(measured, ddof=1)) if len(measured) > 1 else float("nan"),
                                    "s_only_power_mean": float(np.mean(signal_values)),
                                    "s_only_power_std": float(np.std(signal_values, ddof=1)) if len(signal_values) > 1 else float("nan"),
                                    # 单元 GO 理论必须使用实际 CFAR CUT 的信号/背景比。
                                    # 主横轴仍是用户定义的固定 support 输出 SCNR；此列只
                                    # 把 support 能量标尺映射回同一冻结主瓣中心单元，不能
                                    # 把 3×3 support 能量直接代入单元 GO 门限公式。
                                    "s_only_center_power_mean": float(np.mean(center_signal_values)),
                                    "s_only_center_power_std": float(np.std(center_signal_values, ddof=1)) if len(center_signal_values) > 1 else float("nan"),
                                    "unit_cut_scnr_db": linear_to_db(float(np.mean(center_signal_values)) / p0_center_mean),
                                    "s_only_truth_center_power_mean": float(np.mean(truth_center_signal_values)),
                                    "s_only_truth_center_power_std": float(np.std(truth_center_signal_values, ddof=1)) if len(truth_center_signal_values) > 1 else float("nan"),
                                    "unit_truth_cut_scnr_db": linear_to_db(float(np.mean(truth_center_signal_values)) / p0_truth_center_mean),
                                    "sample_count": len(measured), "median_bias_db": float(np.median(np.asarray(measured) - (slope * level + intercept)))})
    write_csv(root / "output_snr_calibration.csv", calibration_rows)
    write_csv(root / "output_snr_calibration_summary.csv", calibration_summary)
    calibration_power_map = {float(row["input_snr_db_control"]): float(row["s_only_power_mean"])
                             for row in calibration_summary}
    # transfer audit is one row per control level, not one row per screen.  A
    # per-screen table is useful for raw evidence, but validating its sorted
    # values would incorrectly treat thermal realization scatter as a
    # non-monotonic transfer function.  The batch columns below are the mean of
    # the three calibration screens at that level; the corresponding std stays
    # visible for uncertainty review.
    transfer_rows = []
    reference_control = float(args.reference_input_snr_db)
    for row in calibration_summary:
        control = float(row["input_snr_db_control"])
        measured = fnum(row["measured_output_snr_db"])
        predicted_row = fnum(row["predicted_output_scnr_db"])
        transfer_rows.append({
            "angle_deg": angle,
            "amplitude_scale": 10.0 ** ((control - reference_control) / 20.0),
            "target_snr_config": control,
            "P_S_out": fnum(row["s_only_power_mean"]),
            "P_S_out_std": fnum(row["s_only_power_std"]),
            "P0_background": p0_mean,
            "desired_scnr_out_db": measured,
            "measured_batch_scnr_out_db": measured,
            "measured_batch_scnr_out_db_std": fnum(row["measured_output_snr_db_std"]),
            "sample_count": int(row["sample_count"]),
            "residual_db": measured - predicted_row if math.isfinite(measured) and math.isfinite(predicted_row) else float("nan"),
            "calibration_prediction_db": predicted_row,
        })
    write_csv(root / "scnr_transfer_audit.csv", transfer_rows)
    mark_raw_removed(s_cal_run); cleanup_maps(Path(s_cal_run["result_dir"]))
    cleanup_algorithm_output_bins(s_cal_run)
    cleanup_intermediate_outputs(s_cal_run)
    calibration_map = {float(row["input_snr_db_control"]): float(row["measured_output_snr_db"]) for row in calibration_summary}
    calibration_cut_scnr_map = {float(row["input_snr_db_control"]): float(row["unit_cut_scnr_db"])
                                for row in calibration_summary}

    sn_scenario = variant_scenario(base, root / "s_plus_n", True, False, schedule, "s_plus_n")
    sn_run = pipeline(sn_scenario, root / "s_plus_n", args, "s_plus_n")
    sn_run["destination"] = root / "s_plus_n"
    sn_debug = Path(sn_run["result_dir"]) / "debug"
    sn_supports = trajectory_support(reference_support, sn_run, spec, args, root,
                                     output_name="frozen_support_s_plus_n.csv")
    period_rows = extract_period_metrics(sn_run, sn_supports, args, "s_plus_n")
    for period_row in period_rows:
        rid, row, col = int(period_row["result_id"]), int(period_row["support_row"]), int(period_row["support_col"])
        truth_row, truth_col = int(period_row["truth_row"]), int(period_row["truth_col"])
        real = load_map(sn_debug, rid, args.diagnostic_beam, "complex_real", (pulse_num, range_bins))
        imag = load_map(sn_debug, rid, args.diagnostic_beam, "complex_imag", (pulse_num, range_bins))
        hits = load_map(sn_debug, rid, args.diagnostic_beam, "hits", (pulse_num, range_bins))
        power = load_map(sn_debug, rid, args.diagnostic_beam, "power", (pulse_num, range_bins))
        threshold = load_map(sn_debug, rid, args.diagnostic_beam, "threshold", (pulse_num, range_bins))
        # ``extract_period_metrics`` already evaluated the physical truth cell
        # through the production axis sidecar.  Preserve both events:
        #   unit_exact      = the one projected FFT CUT (directly comparable
        #                      with the textbook Q1 model);
        #   unit_resolution  = a production-resolution event within +/-2 bins,
        #                      which is the operational event used before
        #                      clustering/target matching.
        # Do not overwrite the latter with the former: a one-bin FFT/main-lobe
        # displacement is not a CFAR miss.
        unit_exact = int(period_row.get("unit_exact", 0))
        unit_resolution = int(period_row.get("unit_resolution", 0))
        unit_hit_row_offset = int(period_row.get("unit_hit_row_offset", -1))
        unit_hit_col_offset = int(period_row.get("unit_hit_col_offset", -1))
        unit_component_size = int(period_row.get("unit_component_size", 0))
        meta = read_meta(sn_debug, rid, args.diagnostic_beam)
        unit_alpha = fnum(meta.get("cfar_alpha"))
        if not math.isfinite(unit_alpha):
            unit_alpha = GoCfarGeometry(args.cfar_guard, args.cfar_background).alpha(args.pfa)
        # Keep the production support CUT as a separate diagnostic.  The
        # canonical unit event is evaluated at the physical truth CUT below;
        # mixing these two coordinates made the old exact-Pd curve look
        # artificially optimistic whenever the target peak was one FFT bin
        # away from the truth projection.
        support_left, support_right, support_top, support_bottom, support_maximum = go_means(
            power, row, col, args.cfar_guard, args.cfar_background)
        support_threshold = float(threshold[row, col])
        if not math.isfinite(support_threshold) or support_threshold <= 0.0:
            raise RuntimeError(
                f"S+N production threshold map invalid at result={rid}, "
                f"support=({row},{col}): {support_threshold}")
        support_m = support_threshold / unit_alpha
        truth_left, truth_right, truth_top, truth_bottom, truth_maximum = go_means(
            power, truth_row, truth_col, args.cfar_guard, args.cfar_background)
        truth_threshold = float(threshold[truth_row, truth_col])
        if not math.isfinite(truth_threshold) or truth_threshold <= 0.0:
            raise RuntimeError(
                f"S+N production threshold map invalid at result={rid}, "
                f"truth=({truth_row},{truth_col}): {truth_threshold}")
        truth_m = truth_threshold / unit_alpha
        # The cluster is evaluated at the nearest hit selected by the bounded
        # truth-resolution gate, not necessarily at the exact projected CUT.
        # This mirrors the production target-resolution association while
        # retaining the exact CUT as an independent theory diagnostic.
        event_row = ((int(period_row["truth_row"]) + unit_hit_row_offset) % hits.shape[0]
                     if unit_resolution and unit_hit_row_offset >= 0 else int(row))
        event_col = (int(period_row["truth_col"]) + unit_hit_col_offset
                     if unit_resolution and unit_hit_col_offset >= 0 else int(col))
        component_size = (len(hit_component_cells(hits, event_row, event_col, max_range_gap=2))
                          if unit_resolution else 0)
        if unit_resolution and unit_component_size > 0:
            # Keep the extractor's component size as an auditable cross-check;
            # both implementations use the same hit map and connectivity.
            component_size = unit_component_size
        period_row.update({
            "unit_exact": unit_exact,
            "unit_resolution": unit_resolution,
            "unit_hit_row_offset": unit_hit_row_offset,
            "unit_hit_col_offset": unit_hit_col_offset,
            "unit_component_size": component_size,
            # Unit-level fields are always the physical truth CUT.  Support
            # fields below preserve the production-resolution diagnostic.
            "unit_cut_power": float(power[truth_row, truth_col]),
            "unit_cfar_left_mean": truth_left,
            "unit_cfar_right_mean": truth_right,
            "unit_cfar_top_mean": truth_top,
            "unit_cfar_bottom_mean": truth_bottom,
            "unit_cfar_train_mean_mixed": truth_maximum,
            "unit_cfar_threshold_mixed": unit_alpha * truth_maximum,
            "unit_cfar_train_mean": truth_m,
            "unit_cfar_threshold": truth_threshold,
            "support_cfar_left_mean": support_left,
            "support_cfar_right_mean": support_right,
            "support_cfar_top_mean": support_top,
            "support_cfar_bottom_mean": support_bottom,
            "support_cfar_train_mean": support_m,
            "support_cfar_threshold": support_threshold,
            "support_cfar_train_mean_mixed": support_maximum,
            "support_cfar_threshold_mixed": unit_alpha * support_maximum,
            "unit_cfar_threshold_source": "production_cfar_threshold_map",
            "unit_scnr_det_out_db_mixed": linear_to_db(
                max(float(power[truth_row, truth_col]) - truth_maximum, 0.0) / truth_maximum)
            if truth_maximum > 0.0 else float("nan"),
            "unit_scnr_det_out_db": linear_to_db(
                max(float(power[truth_row, truth_col]) - truth_m, 0.0) / truth_m)
            if truth_m > 0.0 else float("nan"),
            "support_scnr_det_out_db": linear_to_db(
                max(float(power[row, col]) - support_m, 0.0) / support_m)
            if support_m > 0.0 else float("nan"),
            "unit_event_coordinate": "physical_truth_cell_and_bounded_resolution_gate",
        })
        if unit_resolution:
            period_row["cluster_event"] = production_cluster_event(
                power, hits, event_row, event_col, component_size, args)
        raw_target_event = int(period_row.get("target_event_raw", period_row.get("target_event", 0)))
        period_row["target_event"] = int(raw_target_event and int(period_row.get("cluster_event", 0)))
        period_row["target_gate_reason"] = (
            "raw_truth_match_and_cluster_pass" if period_row["target_event"] else
            "raw_truth_match_rejected_without_cluster" if raw_target_event else
            "no_raw_truth_match")
        # 紧凑 joint hit mask 只围绕离线 truth-resolution 事件保存，供
        # 形态审计使用；它不会参与 detector、cluster 或 target 判定。
        # 这样正式运行清理 cfar_*.f32 后，仍能区分“局部 3×5 点数”与
        # “整图连通 component”这两个不同随机变量。
        joint_mask_values: list[int] = []
        for delta_row in (-1, 0, 1):
            for delta_col in (-2, -1, 0, 1, 2):
                mask_row = (event_row + delta_row) % hits.shape[0]
                mask_col = event_col + delta_col
                joint_mask_values.append(
                    int(0 <= mask_col < hits.shape[1] and float(hits[mask_row, mask_col]) > 0.0))
        period_row["joint_mask_hit_count"] = int(sum(joint_mask_values))
        period_row.update({
            f"joint_mask_{index // 5}_{index % 5}": value
            for index, value in enumerate(joint_mask_values)
        })
        period_row["full_component_ge_min_points"] = int(
            unit_resolution and component_size >= args.min_points)
        period_row["small_branch_candidate"] = int(
            unit_resolution and args.small_min_points <= component_size < args.min_points)
        z = complex(float(real[row, col]), float(imag[row, col]))
        # N-only complex sample must be taken at the same physical support
        # after mapping through the S+N runtime axis.  This remains an offline
        # score/reference operation; no N-only value is fed back to the
        # production detector.
        n_real = load_map(cn_debug, rid, args.diagnostic_beam, "complex_real", (pulse_num, range_bins))
        n_imag = load_map(cn_debug, rid, args.diagnostic_beam, "complex_imag", (pulse_num, range_bins))
        z_n = complex(float(n_real[row, col]), float(n_imag[row, col]))
        z_truth = complex(float(real[truth_row, truth_col]), float(imag[truth_row, truth_col]))
        z_n_truth = complex(float(n_real[truth_row, truth_col]), float(n_imag[truth_row, truth_col]))
        ps = abs(z - z_n) ** 2
        truth_ps = abs(z_truth - z_n_truth) ** 2
        period_row["cn_complex_real"] = z_n.real; period_row["cn_complex_imag"] = z_n.imag
        period_row["s_plus_n_complex_real"] = z.real; period_row["s_plus_n_complex_imag"] = z.imag
        period_row["signal_increment_power"] = ps
        period_row["truth_signal_increment_power"] = truth_ps
        period_row["s_plus_n_support_power"] = support_power_sum(
            power, row, col, args.output_support_row_half_width,
            args.output_support_col_half_width)
        period_row["s_plus_n_center_power"] = float(power[row, col])
        period_row["s_plus_n_truth_center_power"] = float(power[truth_row, truth_col])
        period_row["p0_mean_power"] = p0_mean
        period_row["p0_truth_center_mean_power"] = p0_truth_center_mean
        period_row["output_scnr_db"] = linear_to_db(ps / p0_mean)
        period_row["input_snr_db_index"] = levels.index(float(period_row.get("input_snr_db_control", schedule[rid - 1]))) if period_row.get("input_snr_db_control") is not None else next(i for i, x in enumerate(levels) if abs(x - schedule[rid - 1]) < 1e-6)
    # The extractor support rows do not carry the control level; bind by period schedule.
    # S-only 只用来冻结主瓣 support 和检查幅度平方律。首个小批次已经
    # 证实：S-only CUT 功率远大于实际 S+N CUT 的增量，说明 CSI 处理对
    # 信号存在数据依赖，不能把 S-only 功率当作 production output SCNR。
    # 主轴因此采用同一冻结 support 上 E[P_{S+N}] - E[P_{N}] 的集合平均；
    # 复数配对差只保留为非线性诊断，绝不替换该功率定义。
    for period_row in period_rows:
        control = float(schedule[int(period_row["period_id"])])
        period_row["input_snr_db_control"] = control
        period_row["input_snr_db_index"] = min(range(len(levels)), key=lambda i: abs(levels[i] - control))
        period_row["s_only_output_scnr_db"] = calibration_map[control]
        period_row["s_only_unit_cut_scnr_db"] = calibration_cut_scnr_map[control]
        period_row["pair_incremental_output_snr_db"] = float(period_row["output_scnr_db"])
        period_row["P0_reference"] = p0_mean
    processed_summary: list[dict[str, object]] = []
    processed_output_map: dict[float, float] = {}
    processed_cut_scnr_map: dict[float, float] = {}
    # 每组三屏先做自身的集合平均，再按控制档汇总。这样不会把 S+N 的瞬时
    # 噪声功率当成 SCNR 分母，也不会借用 S-only 的不适用线性标定。
    for level_index, control in enumerate(levels):
        group_values: list[dict[str, float]] = []
        for group in [item for item in groups if int(item["level_index"]) == level_index]:
            start = int(group["period_start"])
            screens = period_rows[start:start + int(group["period_count"])]
            if len(screens) != int(group["period_count"]):
                raise RuntimeError(f"S+N group={group['group_id']} 缺少完整三屏")
            support_total = float(np.mean([fnum(row["s_plus_n_support_power"]) for row in screens]))
            center_total = float(np.mean([fnum(row["s_plus_n_center_power"]) for row in screens]))
            truth_center_total = float(np.mean([fnum(row["s_plus_n_truth_center_power"]) for row in screens]))
            signal_support = support_total - p0_mean
            signal_center = center_total - p0_center_mean
            signal_truth = truth_center_total - p0_truth_center_mean
            support_scnr = linear_to_db(signal_support / p0_mean)
            support_cut_scnr = linear_to_db(signal_center / p0_center_mean)
            cut_scnr = linear_to_db(signal_truth / p0_truth_center_mean)
            group_values.append({"support_total": support_total, "center_total": center_total,
                                 "truth_center_total": truth_center_total,
                                 "signal_support": signal_support, "signal_center": signal_center,
                                 "signal_truth": signal_truth,
                                 "support_cut_scnr": support_cut_scnr,
                                 "output_scnr_db": support_scnr, "unit_cut_scnr_db": cut_scnr})
            for row in screens:
                row["requested_output_scnr_db"] = support_scnr
                row["measured_signal_power"] = signal_support
                row["measured_scnr_out_db"] = support_scnr
                row["support_center_scnr_db"] = support_cut_scnr
                row["unit_cut_scnr_db"] = cut_scnr
                row["output_scnr_db"] = support_scnr
        valid_support = [item["output_scnr_db"] for item in group_values if math.isfinite(item["output_scnr_db"])]
        valid_cut = [item["unit_cut_scnr_db"] for item in group_values if math.isfinite(item["unit_cut_scnr_db"])]
        support_mean = float(np.mean([item["support_total"] for item in group_values]))
        center_mean = float(np.mean([item["center_total"] for item in group_values]))
        truth_center_mean = float(np.mean([item["truth_center_total"] for item in group_values]))
        signal_support_mean = support_mean - p0_mean
        signal_center_mean = center_mean - p0_center_mean
        signal_truth_mean = truth_center_mean - p0_truth_center_mean
        output_mean = linear_to_db(signal_support_mean / p0_mean)
        support_cut_mean = linear_to_db(signal_center_mean / p0_center_mean)
        cut_mean = linear_to_db(signal_truth_mean / p0_truth_center_mean)
        processed_output_map[control] = output_mean
        processed_cut_scnr_map[control] = cut_mean
        processed_summary.append({
            "level_index": level_index, "input_snr_db_control": control,
            "s_only_output_scnr_db": calibration_map[control],
            "s_only_unit_cut_scnr_db": calibration_cut_scnr_map[control],
            "s_plus_n_support_power_mean": support_mean,
            "s_plus_n_center_power_mean": center_mean,
            "s_plus_n_truth_center_power_mean": truth_center_mean,
            "p0_support_power_mean": p0_mean,
            "p0_center_power_mean": p0_center_mean,
            "p0_truth_center_power_mean": p0_truth_center_mean,
            "processed_signal_support_power_mean": signal_support_mean,
            "processed_signal_center_power_mean": signal_center_mean,
            "processed_signal_truth_center_power_mean": signal_truth_mean,
            "processed_output_scnr_db": output_mean,
            "processed_output_scnr_db_std": float(np.std(valid_support, ddof=1)) if len(valid_support) > 1 else float("nan"),
            "processed_output_scnr_cut_equiv_db": cut_mean,
            "processed_output_scnr_cut_equiv_db_std": float(np.std(valid_cut, ddof=1)) if len(valid_cut) > 1 else float("nan"),
            "processed_output_scnr_support_center_db": support_cut_mean,
            "unit_cut_scnr_db": cut_mean,
            "unit_cut_scnr_db_std": float(np.std(valid_cut, ddof=1)) if len(valid_cut) > 1 else float("nan"),
            "screen_group_count": len(group_values),
        })
    write_csv(root / "processed_output_snr_calibration.csv", processed_summary)
    processed_x = np.asarray([float(row["input_snr_db_control"]) for row in processed_summary], dtype=float)
    processed_y = np.asarray([fnum(row["processed_output_scnr_db"]) for row in processed_summary], dtype=float)
    processed_finite = np.isfinite(processed_x) & np.isfinite(processed_y)
    if int(np.sum(processed_finite)) >= 2:
        processed_slope, processed_intercept = [float(value) for value in np.polyfit(
            processed_x[processed_finite], processed_y[processed_finite], 1)]
        processed_residual = processed_y - (processed_slope * processed_x + processed_intercept)
        processed_denom = float(np.sum((processed_y[processed_finite] -
                                        np.mean(processed_y[processed_finite])) ** 2))
        processed_r2 = (1.0 - float(np.sum(processed_residual[processed_finite] ** 2)) / processed_denom
                        if processed_denom > 0.0 else float("nan"))
    else:
        processed_slope = processed_intercept = processed_r2 = float("nan")
        processed_residual = np.full_like(processed_y, np.nan)
    # 正式 transfer audit 必须反映真实 S+N production 输出；S-only 只作
    # 非线性诊断，不能继续伪装为 GO 输入面的信号功率。
    processed_transfer_rows = []
    reference_control = float(args.reference_input_snr_db)
    for row, residual_value in zip(processed_summary, processed_residual):
        control = float(row["input_snr_db_control"])
        processed_transfer_rows.append({
            "angle_deg": angle,
            "amplitude_scale": 10.0 ** ((control - reference_control) / 20.0),
            "target_snr_config": control,
            "P_S_out": row["processed_signal_support_power_mean"],
            "P0_background": p0_mean,
            "P_S_truth_cut_out": row["processed_signal_truth_center_power_mean"],
            "P0_truth_cut": p0_truth_center_mean,
            "desired_scnr_out_db": row["processed_output_scnr_db"],
            "measured_batch_scnr_out_db": row["processed_output_scnr_db"],
            "measured_batch_scnr_out_db_std": row["processed_output_scnr_db_std"],
            "sample_count": row["screen_group_count"],
            "residual_db": float(residual_value),
            "calibration_prediction_db": (processed_slope * control + processed_intercept
                                            if math.isfinite(processed_slope) else float("nan")),
            "s_only_output_scnr_db": row["s_only_output_scnr_db"],
            "s_only_unit_cut_scnr_db": row["s_only_unit_cut_scnr_db"],
            "unit_cut_scnr_db": row["unit_cut_scnr_db"],
            "support_center_scnr_db": row["processed_output_scnr_support_center_db"],
            "support_output_scnr_db": row["processed_output_scnr_db"],
            "output_scnr_cut_equiv_db": row["processed_output_scnr_cut_equiv_db"],
        })
    write_csv(root / "scnr_transfer_audit.csv", processed_transfer_rows)
    write_csv(root / "standard_mc_period_metrics.csv", period_rows)
    # N-only maps were retained until S+N scoring so the complex diagnostic
    # reference uses the same runtime support.  They can now be removed while
    # retaining the manifest and all aggregate audit tables.
    mark_raw_removed(cn_run); cleanup_maps(Path(cn_run["result_dir"]))
    cleanup_algorithm_output_bins(cn_run)
    cleanup_intermediate_outputs(cn_run)
    group_rows, curve_rows = aggregate_curves(period_rows, groups, levels, processed_output_map,
                                              processed_cut_scnr_map, p0_mean, args)
    write_csv(root / "standard_mc_screen_metrics.csv", group_rows)
    write_csv(root / "standard_mc_curve_summary.csv", curve_rows)
    transitions = transition_stats(group_rows)
    go_geometry = GoCfarGeometry(args.cfar_guard, args.cfar_background)
    alpha = None
    try:
        meta = read_meta(sn_debug, 1, args.diagnostic_beam)
        alpha = fnum(meta.get("cfar_alpha"))
    except RuntimeError:
        alpha = go_geometry.alpha(args.pfa)
    if not math.isfinite(alpha):
        alpha = go_geometry.alpha(args.pfa)
    noise = go_training_noise_samples(go_geometry, args.theory_samples, args.seed + 991)
    # GO 单元理论必须保留目标在真实轨迹上的逐周期支撑。若先把整组三屏
    # 的功率平均，再代入单一 Q1，会把“少数 period 目标主瓣落在 truth CUT、
    # 其余 period 落在相邻 FFT bin”的量化迁移抹掉，造成理论 Pd 虚高。
    # 因此保留两种量：旧的 batch-mean 结果用于审计对照；主 exact-CUT
    # 理论使用 S-only 生产 truth-CUT 功率和同 period 的 C+N-only GO M，
    # 逐 period 计算 noncentral-chi-square/Marcum-Q 后再求平均。S-only
    # 仍只用于离线理论标定，不进入在线 XML、CFAR、聚类或 TrackManager。
    m_norm = np.asarray([float(row["go_truth_m"]) / p0_truth_center_mean for row in p0_rows])
    m_norm_by_period = {int(row["period_id"]): float(row["go_truth_m"]) / p0_truth_center_mean
                        for row in p0_rows}
    signal_gamma_by_period = {
        int(row["period_id"]): max(fnum(row.get("s_only_truth_center_power")), 0.0)
        / p0_truth_center_mean
        for row in calibration_rows
    }
    # A paired S+N/N-only complex increment is the effective signal actually
    # seen at the production GO input.  It is an output-side diagnostic (not a
    # hit-rate fit): with the same scenario seed, |z_SN-z_N|^2 isolates the
    # target increment even when CSI/adaptive processing attenuates the
    # S-only reference.  Keeping it as a separate column makes that transfer
    # effect visible instead of silently overpredicting exact-CUT Pd.
    paired_signal_gamma_by_period = {
        int(row["period_id"]): max(fnum(row.get("truth_signal_increment_power")), 0.0)
        / p0_truth_center_mean
        for row in period_rows
    }
    period_theory_rows: list[dict[str, object]] = []
    theory_rows: list[dict[str, object]] = []
    for row in curve_rows:
        control = float(row["input_snr_db_control"])
        cut_gamma_db = processed_cut_scnr_map[control]
        support_gamma_db = fnum(row.get("support_output_scnr_db"))
        gamma = 10.0 ** (cut_gamma_db / 10.0) if math.isfinite(cut_gamma_db) else float("nan")
        iid = float(go_conditional_pd(gamma, alpha, noise)[0]) if math.isfinite(gamma) else float("nan")
        empirical = float(np.mean(go_conditional_pd(gamma, alpha, m_norm)[0])) if math.isfinite(gamma) else float("nan")
        level_periods = [item for item in period_rows
                         if int(item.get("input_snr_db_index", -1)) == int(row["level_index"])]
        period_ids = [int(item["period_id"]) for item in level_periods]
        signal_gamma = np.asarray([signal_gamma_by_period.get(period_id, float("nan"))
                                    for period_id in period_ids], dtype=float)
        paired_signal_gamma = np.asarray([
            paired_signal_gamma_by_period.get(period_id, float("nan"))
            for period_id in period_ids
        ], dtype=float)
        period_m = np.asarray([m_norm_by_period.get(period_id, float("nan"))
                               for period_id in period_ids], dtype=float)
        period_pd = go_period_mixture_pd(signal_gamma, period_m, alpha)
        paired_period_pd = go_period_mixture_pd(paired_signal_gamma, period_m, alpha)
        period_pd_iid = np.asarray(go_conditional_pd(signal_gamma, alpha, noise), dtype=float)
        finite_period = np.isfinite(period_pd) & np.isfinite(signal_gamma) & np.isfinite(period_m)
        period_mean = float(np.mean(period_pd[finite_period])) if np.any(finite_period) else float("nan")
        finite_paired = (np.isfinite(paired_period_pd) & np.isfinite(paired_signal_gamma)
                         & np.isfinite(period_m))
        paired_period_mean = (float(np.mean(paired_period_pd[finite_paired]))
                              if np.any(finite_paired) else float("nan"))
        period_iid_mean = float(np.mean(period_pd_iid[finite_period])) if np.any(finite_period) else float("nan")
        for period_id, gamma_period, paired_gamma_period, m_period_value, pd_period, paired_pd_period, pd_iid_period in zip(
                period_ids, signal_gamma, paired_signal_gamma, period_m, period_pd,
                paired_period_pd, period_pd_iid):
            period_theory_rows.append({
                "level_index": int(row["level_index"]), "input_snr_db_control": control,
                "period_id": period_id, "signal_gamma_s_only_truth": float(gamma_period),
                "signal_gamma_paired_output_truth": float(paired_gamma_period),
                "m_norm_cplusn_truth": float(m_period_value),
                "output_scnr_batch_db": cut_gamma_db,
                "unit_pd_iid_period": float(pd_iid_period),
                "unit_pd_empirical_period": float(pd_period),
                "unit_pd_paired_output_period": float(paired_pd_period),
            })
        theory_rows.append({"level_index": int(row["level_index"]), "output_scnr_db": cut_gamma_db,
                            "support_output_scnr_db": support_gamma_db,
                            "unit_cut_scnr_db": cut_gamma_db,
                            "unit_pd_iid_go": iid, "unit_pd_empirical_n_only": empirical,
                            "unit_pd_iid_go_period_mixture": period_iid_mean,
                            "unit_pd_empirical_n_only_period_mixture": period_mean,
                            "unit_pd_s_only_truth_mixture": period_mean,
                            "unit_pd_paired_output_mixture": paired_period_mean,
                            # The production-level unit baseline is the
                            # period mixture; cluster/target/track columns
                            # remain idealized baselines and are explicitly
                            # superseded by their measured event columns.
                            "unit_pd": paired_period_mean, "cluster_pd": paired_period_mean,
                            "target_pd": paired_period_mean,
                            "track_pd": 3.0 * paired_period_mean * paired_period_mean - 2.0 * paired_period_mean ** 3,
                            "target_pd_iid_baseline": iid, "cluster_pd_iid_baseline": iid,
                            "alpha": alpha, "theory_samples": args.theory_samples,
                            "period_count": len(period_ids),
                            "signal_source": "paired S+N/N-only complex truth-CUT increment for main; S-only retained as independent diagnostic",
                            "m_source": "C+N-only production truth-CUT GO M per period"})
    write_csv(root / "go_theory_periods.csv", period_theory_rows)
    write_csv(root / "go_theory_vs_mc.csv", theory_rows)
    max_bias = max((abs(float(value)) for value in processed_residual if math.isfinite(float(value))), default=float("inf"))
    sanity_pass = (math.isfinite(processed_slope) and 0.85 <= processed_slope <= 1.15 and
                   math.isfinite(processed_r2) and processed_r2 >= 0.98 and max_bias <= 1.0)
    manifest = {
        "angle_deg": angle, "target_angle_deg": target_angle,
        "target_angle_offset_deg": args.target_angle_offset_deg,
        "seed": args.seed,
        "background_profile": args.background_profile,
        "background": ("thermal_noise_only_no_area_clutter"
                        if args.background_profile == "target_only"
                        else "compact_statistical_area_clutter_plus_thermal_noise"),
        "pfa": args.pfa, "cfar_guard": args.cfar_guard, "cfar_background": args.cfar_background,
        "min_points": args.min_points, "small_min_points": args.small_min_points,
        "small_peak_db": args.small_peak_db, "period_count": n_periods,
        "strict_online_no_prior": bool(args.strict_online_no_prior),
        "screen_group_count": len(groups), "input_snr_grid_control": levels,
        "output_scnr_axis_definition": "10log10((mean_truth_CUT(P_S_plus_N)-mean_truth_CUT(P_N_only))/ensemble_mean(truth_CUT_P_N_only)); canonical output axis is production physical truth-CUT-equivalent SCNR",
        "support_output_scnr_axis_definition": "10log10((mean_fixed_3x3_support(P_S_plus_N)-mean_fixed_3x3_support(P_N_only))/ensemble_mean(3x3_support_P_N_only)); diagnostic only",
        "support_peak_offset_audit": {
            "row_nonzero_count": int(np.sum([int(item.get("support_row_offset", 0)) != 0 for item in cn_supports])),
            "col_nonzero_count": int(np.sum([int(item.get("support_col_offset", 0)) != 0 for item in cn_supports])),
            "sample_count": len(cn_supports),
            "meaning": "S-only peak/support location versus physical truth CUT; support axis is not used for exact-CUT Pd",
        },
        "p0_mean_power": p0_mean, "p0_std_power": p0_std,
        "p0_median_power": float(np.median(p0_values)),
        "p0_p05_power": float(np.percentile(p0_values, 5)), "p0_p95_power": float(np.percentile(p0_values, 95)),
        "p0_center_mean_power": p0_center_mean, "p0_center_std_power": p0_center_std,
        "p0_center_median_power": float(np.median(p0_center_values)),
        "p0_truth_center_mean_power": p0_truth_center_mean,
        "p0_truth_center_std_power": p0_truth_center_std,
        "p0_truth_center_median_power": float(np.median(p0_truth_center_values)),
        "output_support_row_half_width": args.output_support_row_half_width,
        "output_support_col_half_width": args.output_support_col_half_width,
        "calibration_slope": processed_slope, "calibration_intercept": processed_intercept,
        "calibration_r2": processed_r2,
        "calibration_max_abs_bias_db": max_bias, "calibration_sanity_pass": sanity_pass,
        "support_calibration_slope": processed_slope, "support_calibration_intercept": processed_intercept,
        "cut_output_axis_source": "paired S+N/C+N physical truth-CUT power increment; no hit-rate fitting",
        "s_only_calibration_slope": slope, "s_only_calibration_intercept": intercept,
        "s_only_calibration_r2": r2,
        "scnr_transfer_audit": str(root / "scnr_transfer_audit.csv"),
        "go_alpha": alpha, "theory_samples": args.theory_samples,
        "unit_theory_model": {
            "main": "paired_output_truth_cut_period_mixture",
            "signal_source": "same-seed paired S+N/N-only complex truth-CUT increment; hit counts unused",
            "background_source": "C+N-only truth-CUT GO M per period",
            "independent_diagnostic": "S-only truth-CUT period mixture",
            "period_theory_csv": str(root / "go_theory_periods.csv"),
        },
        "information_leakage_policy": {
            "status": "strict_no_truth_or_background_prior" if args.strict_online_no_prior else "no_truth_to_detector",
            "truth_used_by_detector": False,
            "truth_used_for": ["offline_support_axis", "offline_cut_event_scoring", "offline_truth_matching"],
            "truth_path_in_production_xml": False,
            "pc_peak_debug_enabled": False,
            "n_only_freeze": ("disabled: strict online mode; N-only used only offline"
                              if args.strict_online_no_prior else
                              "background_only_calibration; no target truth or S+N output used"),
            "s_only_reference": "offline_support_calibration_only; never passed to S+N detector",
            "nms_truth_tiebreak_removed": True,
        },
        "track_event_definition": "three truth target screens, at least two target_event hits",
        "trackmanager_role": "appendix audit only; not main track Pd",
        "raw_cleanup": "raw BIN removed only after all period detection/extraction and TrackManager payload audit passed",
        "gpu_note": "recorded externally with nvidia-smi before run",
    }
    write_json(root / "manifest.json", manifest)
    plot_and_report(root, angle, curve_rows, calibration_summary, p0_rows, theory_rows, manifest, transitions, args)
    # 诊断模式保留最终 S+N 的 GO power/hit 图，便于审计目标支撑与连通域；
    # raw BIN 仍在 detection/TrackManager 审计完成后删除。
    mark_raw_removed(sn_run)
    cleanup_algorithm_output_bins(sn_run)
    if not args.keep_debug_maps:
        cleanup_maps(Path(sn_run["result_dir"]))
    cleanup_intermediate_outputs(sn_run)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=Path("outputs/gmti_scnr_eval/standard_output_snr_mc_20260826"))
    parser.add_argument("--angle", type=float, action="append", default=None)
    parser.add_argument("--angles", default="0,30,45")
    parser.add_argument("--target-angle-offset-deg", type=float, default=0.0,
                        help="目标真实角相对命令波位的偏移；默认 0，0.8 可用于波束内边缘模块验证")
    parser.add_argument("--background-profile", choices=("target_only", "compact_statistical"),
                        default="target_only",
                        help="背景模式；target_only 是隔离基线，compact_statistical 是 S+C+N/C+N-only 验证")
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument("--input-snr-grid", default="-20,-10,0,10,20")
    parser.add_argument("--trials-per-level", type=int, default=10,
                        help="每个控制档的独立三屏组数；10档×5水平=50组=150个 period")
    parser.add_argument("--reference-input-snr-db", type=float, default=20.0)
    parser.add_argument("--reference-row-half-width", type=int, default=6)
    parser.add_argument("--reference-col-half-width", type=int, default=4)
    parser.add_argument("--output-support-row-half-width", type=int, default=1,
                        help="输出 SCNR 固定支撑窗口的 Doppler 半宽；默认 1（3 行）")
    parser.add_argument("--output-support-col-half-width", type=int, default=1,
                        help="输出 SCNR 固定支撑窗口的 range 半宽；默认 1（3 列）")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--diagnostic-beam", type=int, default=1)
    parser.add_argument("--pfa", type=float, default=1e-6)
    parser.add_argument("--cfar-guard", type=int, default=4)
    parser.add_argument("--cfar-background", type=int, default=16)
    parser.add_argument("--min-points", type=int, default=6)
    parser.add_argument("--small-min-points", type=int, default=3)
    parser.add_argument("--small-peak-db", type=float, default=20.0)
    parser.add_argument("--theory-samples", type=int, default=100000)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--keep-debug-maps", action="store_true",
                        help="保留最终 S+N 的 cfar_* power/hits 图用于局部支撑审计；raw BIN 仍清理")
    parser.add_argument("--strict-online-no-prior", action="store_true",
                        help="不把 N-only Doppler/P38 统计写回 S-only/S+N 生产 XML；N-only 仅供离线评价")
    args = parser.parse_args()
    args.build_dir = args.build_dir.resolve()
    args.output_root = args.output_root.resolve()
    if not (args.build_dir / "simulate_stage2_statistical").is_file() or not (args.build_dir / "GMTI_pipe_core").is_file():
        raise SystemExit("找不到已构建的 simulate_stage2_statistical/GMTI_pipe_core")
    if args.trials_per_level < 1 or args.theory_samples < 1000:
        raise SystemExit("trials-per-level/theory-samples 参数非法")
    angles = args.angle if args.angle else parse_grid(args.angles)
    manifests = []
    for angle in angles:
        manifest_path = args.output_root / f"angle_{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p") / "manifest.json"
        if args.resume and manifest_path.is_file():
            manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))
            print(f"[RESUME] {manifest_path}")
            continue
        print(f"[RUN] standard controlled output-SNR MC angle={angle:g}° periods={len(parse_grid(args.input_snr_grid)) * args.trials_per_level * 3}")
        manifests.append(process_angle(angle, args))
    write_json(args.output_root / "run_manifest.json", {"angles": manifests, "script": str(Path(__file__).resolve()),
                                                         "background_profile": args.background_profile,
                                                         "scope": ("controlled S+N / N-only; area clutter intentionally disabled"
                                                                   if args.background_profile == "target_only"
                                                                   else "controlled S+C+N / C+N-only; compact area clutter enabled")})
    print(f"[PASS] 受控输出 SNR Monte Carlo 完成：{args.output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
