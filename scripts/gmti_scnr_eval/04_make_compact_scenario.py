#!/usr/bin/env python3
"""生成空间分离、可批量承载多档 SCNR 的电子相扫 Stage2 场景。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scnr_eval_lib import ensure_dir, parse_grid


ROOT = Path(__file__).resolve().parents[2]


def target(target_id: str, beam_id: int, azimuth_offset_deg: float,
           expected_bin: int, snr_db: float, replica: int) -> dict:
    # 以 12 m/s 横向/径向混合速度将峰从零多普勒和低速过滤带移开；同一波位
    # 的目标相距 >=100 range cell，远大于正式 GO-CFAR 的 41-cell 外窗宽度。
    return {
        "target_id": target_id,
        "enabled": True,
        "init": {"type": "beam_bin_azimuth_offset", "beam_id": beam_id,
                 "expected_bin": expected_bin, "azimuth_offset_deg": azimuth_offset_deg},
        "motion": {"type": "enu_velocity", "ve_mps": -12.0 - 0.25 * replica,
                   "vn_mps": 9.0 + 0.25 * replica},
        "amplitude": {"type": "snr_db", "snr_db": snr_db},
        "visibility": {"type": "hard_gate", "single_beam_only": True},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument("--beam-count", type=int, default=1)
    parser.add_argument("--scan-min-deg", type=float, default=0.0)
    parser.add_argument("--scan-step-deg", type=float, default=2.0)
    parser.add_argument("--beam-width-deg", type=float, default=1.81871,
                        help="目标 hard_gate 可见主瓣宽度；请求角必须落在最近命令波位的半宽内")
    parser.add_argument("--target-beams", default="1", help="逗号分隔的 1 基波位")
    parser.add_argument("--target-angles", default="",
                        help="逗号分隔的真实相对方位角（度）；指定后替代 --target-beams")
    parser.add_argument("--scnr-grid", default="-45,-40,-35,-30,-25,-20,-15",
                        help="Stage2 target_snr_db 注入档；正式横轴仍由配对测得的输出 SCNR 给出")
    parser.add_argument("--replicas", type=int, default=1)
    parser.add_argument("--slot-rotation", type=int, default=0,
                        help="将注入档循环映射到量程隔离槽位；不同 seed 轮换以消除量程位置混杂")
    parser.add_argument("--period-count", type=int, default=1,
                        help="连续扫描周期数；航迹评估必须为 3")
    parser.add_argument("--background-profile", choices=("compact_statistical", "target_only"),
                        default="compact_statistical",
                        help="正式默认使用紧凑统计杂波；target_only 仅用于受控冒烟或隔离试验")
    parser.add_argument("--case-id", default="gmti_scnr_go_case")
    args = parser.parse_args()
    if args.beam_count < 1 or args.replicas < 1 or args.period_count < 1 or args.beam_width_deg <= 0.0:
        raise SystemExit("beam-count、replicas、period-count 和 beam-width-deg 必须为正")
    scnr = parse_grid(args.scnr_grid)
    requested_angles = [float(token.strip()) for token in args.target_angles.split(",") if token.strip()]
    target_geometry: list[tuple[int, float, float]] = []
    if requested_angles:
        for angle in requested_angles:
            beam = int(round((angle - args.scan_min_deg) / args.scan_step_deg)) + 1
            if beam < 1 or beam > args.beam_count:
                raise SystemExit(f"target angle {angle:g}° 不在扫描覆盖范围内")
            center = args.scan_min_deg + (beam - 1) * args.scan_step_deg
            offset = angle - center
            if abs(offset) > 0.5 * args.beam_width_deg + 1.0e-9:
                raise SystemExit(
                    f"target angle {angle:g}° 距最近命令波位 {center:g}° 为 {offset:g}°，"
                    f"超过 hard_gate 半波束宽 {0.5 * args.beam_width_deg:g}°；"
                    "请使用实际可见的扫描格点，或显式建模非中心波束增益")
            target_geometry.append((beam, offset, angle))
    else:
        beams = [int(token.strip()) for token in args.target_beams.split(",") if token.strip()]
        if not beams or any(beam < 1 or beam > args.beam_count for beam in beams):
            raise SystemExit("target-beams 必须在 [1, beam-count]")
        target_geometry = [
            (beam, 0.0, args.scan_min_deg + (beam - 1) * args.scan_step_deg)
            for beam in beams
        ]
    # 每个 target 使用 80 cell 的隔离间距，接近正式 GO-CFAR 41-cell 外窗的两倍；
    # 因而任一目标的 CUT/训练窗都不包含另一个目标主瓣。8 档×6 副本仍在 4096 cell 内。
    target_count = len(scnr) * args.replicas
    if 160 + 80 * (target_count - 1) >= 4096:
        raise SystemExit("档位×副本过多，无法保证 >41 cell 的 GO-CFAR 空间隔离")
    targets: list[dict] = []
    for beam, offset, angle in target_geometry:
        for index, snr_db in enumerate(scnr):
            for replica in range(args.replicas):
                slot = ((index + args.slot_rotation) % len(scnr)) * args.replicas + replica
                sign = "P" if angle >= 0.0 else "M"
                target_id = f"A{sign}{abs(angle):04.1f}_B{beam:02d}_S{snr_db:05.1f}_R{replica:02d}"
                targets.append(target(target_id, beam, offset, 160 + 80 * slot, snr_db, replica))
    destination = args.output_dir.resolve()
    ensure_dir(destination)
    scenario_dir = destination / "stage2"
    if args.background_profile == "compact_statistical":
        scene_mode = "full"
        # 保留生产 Stage2 的 Rayleigh×lognormal 统计面杂波模型，缩小散射点数而
        # 不缩小 61 波位电子扫描或目标间隔。强/线散射体关闭，避免离散异常点掩盖
        # GO-CFAR 对统计背景的单元级行为。
        scene = {
            "mode": scene_mode, "signal_only": False, "output_signal_domain": "raw_lfm",
            "range_min_m": 82800.0, "range_max_m": 93000.0,
            "azimuth_min_deg": -60.0, "azimuth_max_deg": 60.0, "ground_z_m": 0.0,
            "clutter_amplitude_scale": 1.0,
            "area_clutter": {"enabled": True, "model": "rayleigh_lognormal_texture",
                             "scatterer_count": 240, "mean_power": 1.0,
                             "texture_sigma": 0.4, "spatial_cell_m": 30.0,
                             "azimuth_subcell_count": 9},
            "strong_scatterers": {"enabled": False, "count": 0},
            "line_scatterers": {"enabled": False, "line_count": 0, "points_per_line": 0},
            "thermal_noise": {"enabled": True, "noise_power": 0.01,
                              "include_target_only": False},
        }
    else:
        scene_mode = "target_only"
        scene = {
            "mode": scene_mode, "signal_only": False, "output_signal_domain": "raw_lfm",
            "range_min_m": 82800.0, "range_max_m": 93000.0,
            "azimuth_min_deg": -60.0, "azimuth_max_deg": 60.0, "ground_z_m": 0.0,
            "clutter_amplitude_scale": 0.0,
            "area_clutter": {"enabled": False}, "strong_scatterers": {"enabled": False, "count": 0},
            "line_scatterers": {"enabled": False, "line_count": 0, "points_per_line": 0},
            "thermal_noise": {"enabled": True, "noise_power": 0.01,
                              "include_target_only": True},
        }
    document = {
        "case_id": args.case_id,
        "output_dir": str(scenario_dir),
        "scan_mode": "electronic",
        "scene_mode": scene_mode,
        "truth_output": True,
        "waveform": {"fc_ghz": 16.472113076923076, "bandwidth_mhz": 50.0, "fs_mhz": 60.0,
                     "tr_us": 130.0, "prf_hz": 1300.0, "pulse_len": 11840,
                     "acquired_pulse_len": 11820, "pulse_num": 128, "d_chan_m": 0.17,
                     "iq_data_type": "float32", "new_protocol_channel_count": 4,
                     "new_protocol_read_channel_1": 1, "new_protocol_read_channel_2": 2},
        "range_processing": {"range_fft_len": 12288, "range_crop_start": 3864,
                              "range_crop_len": 4096, "sample_delay_us": 488.0},
        "scan": {"scan_min_deg": args.scan_min_deg, "scan_step_deg": args.scan_step_deg,
                 "beam_count": args.beam_count, "beam_width_deg": args.beam_width_deg, "beam_index_base": 1},
        "platform": {"speed_mps": 60.0, "height_m": 6000.0, "origin_lat_deg": 40.4512107203,
                     "origin_lon_deg": 116.985931582, "origin_alt_m": 6000.0,
                     "projection_ref_lon_deg": 117.0, "squint_side": 1},
        "channel_geometry": {
            "channel_1": {"name": "left_a", "x_m": -0.085, "y_m": 0.0, "z_m": 0.085},
            "channel_2": {"name": "right_a", "x_m": 0.085, "y_m": 0.0, "z_m": 0.085},
            "channel_3": {"name": "left_b", "x_m": -0.085, "y_m": 0.0, "z_m": -0.085},
            "channel_4": {"name": "right_b", "x_m": 0.085, "y_m": 0.0, "z_m": -0.085}},
        "random": {"period_start": 0, "period_count": args.period_count,
                   "beam_start": 1, "beam_count": args.beam_count,
                   "random_seed": args.seed, "four_channel_phase_center_mode": "physical"},
        "scene": scene,
        "channel_impairments": {"enabled": False}, "targets": targets,
    }
    output = destination / "scenario.json"
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
