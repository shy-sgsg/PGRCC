#!/usr/bin/env python3
"""生成 91 m/s、三周期、每周期 100 目标的 GMTI 功能测试场景。

场景参数的工作簿来源、派生距离轴和目标布置规则都写入生成的
``scenario_manifest.json``。生成器只负责设计并写出 Stage2 run JSON；实际
回波由生产 ``simulate_stage2_statistical`` 生成，避免测试脚本另造数据格式。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT / "outputs/gmti_91mps_100targets_3period_scnr20_20260901_textured_paired"
)
C = 299_792_458.0
RANGE_SAMPLE_M = C / (2.0 * 60_000_000.0)
PLATFORM_SPEED_MPS = 91.0
PERIOD_COUNT = 3
TARGET_COUNT = 100
TARGET_SCNR_DB = 20.0
SCAN_MIN_DEG = -29.0
SCAN_STEP_DEG = 2.0
SCAN_MAX_DEG = 29.0
BEAM_COUNT = 30
PULSES_PER_BEAM = 128
CHANNEL_COUNT = 4
IQ_DATA_TYPE = "int16"
HEADER_BYTES = 256
MOTION_DOPPLER_AXIS_SIGN = -1
CFAR_ROW_START = 30
CFAR_ROW_END = 98
CFAR_TARGET_ROW = 40
MIN_RELATIVE_RADIAL_SPEED_MPS = 0.0


def numeric(value: object, field: str) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
    else:
        match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", str(value))
        if not match:
            raise ValueError(f"工作簿字段 {field} 不是数值：{value!r}")
        result = float(match.group(0))
    if not math.isfinite(result):
        raise ValueError(f"工作簿字段 {field} 不是有限数：{value!r}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_period_snr_calibration(
    path: Path,
    targets: list[dict],
    period_count: int,
    requested_scnr_db: float,
) -> dict[str, list[float]]:
    """Read a complete per-target/per-period output-SCNR calibration plan.

    The calibration report is deliberately treated as an input artifact, not
    as a new acceptance rule. Every target and every emitted period must have
    exactly one finite recommendation; a report for 18 dB cannot accidentally
    be applied to a 20 dB scene (or vice versa).
    """
    if not path.is_file():
        raise SystemExit(f"找不到逐周期 SCNR 校准表：{path}")
    expected_ids = {str(target["target_id"]) for target in targets}
    values: dict[str, dict[int, float]] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            target_id = str(row.get("target_id", "")).strip()
            if target_id not in expected_ids:
                raise SystemExit(f"校准表包含未知目标：{target_id!r}")
            try:
                period_id = int(row["period_id"])
                value = float(row["recommended_injected_snr_db"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SystemExit(f"校准表缺少有效周期或推荐注入值：{row}") from exc
            if not 0 <= period_id < period_count:
                raise SystemExit(f"校准周期越界：{target_id}/{period_id}")
            if not math.isfinite(value):
                raise SystemExit(f"校准注入值不是有限数：{target_id}/{period_id}")
            raw_target_scnr = str(row.get("target_scnr_db", "")).strip()
            if raw_target_scnr:
                try:
                    report_target_scnr = float(raw_target_scnr)
                except ValueError as exc:
                    raise SystemExit(f"校准表 target_scnr_db 无效：{row}") from exc
                if not math.isclose(report_target_scnr, requested_scnr_db,
                                    rel_tol=0.0, abs_tol=1.0e-6):
                    raise SystemExit(
                        f"校准表目标为 {report_target_scnr:g} dB，"
                        f"当前场景请求 {requested_scnr_db:g} dB：{path}")
            target_values = values.setdefault(target_id, {})
            if period_id in target_values:
                raise SystemExit(f"校准表存在重复项：{target_id}/{period_id}")
            target_values[period_id] = value

    if set(values) != expected_ids:
        raise SystemExit(
            "校准表目标覆盖不完整："
            f"缺少={sorted(expected_ids - set(values))}，"
            f"多余={sorted(set(values) - expected_ids)}")
    schedules: dict[str, list[float]] = {}
    expected_periods = set(range(period_count))
    for target_id in sorted(expected_ids):
        periods = values[target_id]
        if set(periods) != expected_periods:
            raise SystemExit(
                f"目标 {target_id} 校准周期不完整："
                f"缺少={sorted(expected_periods - set(periods))}，"
                f"多余={sorted(set(periods) - expected_periods)}")
        schedules[target_id] = [periods[period] for period in range(period_count)]
    return schedules


def apply_period_snr_calibration(
    scenario: dict,
    manifest: dict,
    calibration_path: Path,
    schedules: dict[str, list[float]],
    requested_scnr_db: float,
) -> None:
    """Attach an auditable amplitude schedule while preserving the base case."""
    calibrated_case_id = f"{scenario['case_id']}_output_calibrated"
    scenario["case_id"] = calibrated_case_id
    calibration_snapshot = (
        Path(str(scenario["output_dir"])) / "reports" /
        "target_scnr_calibration_source.csv"
    )
    calibration_snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(calibration_path, calibration_snapshot)
    calibration_source = str(calibration_snapshot.resolve())
    calibration_source_sha256 = sha256(calibration_snapshot)
    for target in scenario["targets"]:
        schedule = schedules[str(target["target_id"])]
        target["amplitude"]["snr_db"] = schedule[0]
        target["amplitude"]["snr_db_by_period"] = schedule
    scenario["design_contract"] = {
        "amplitude_control": "per_target_period_output_scnr_calibration",
        "requested_output_scnr_db": requested_scnr_db,
        "calibration_source": calibration_source,
        "calibration_source_sha256": calibration_source_sha256,
        "calibration_period_count": len(next(iter(schedules.values()))),
    }
    manifest["case_id"] = calibrated_case_id
    manifest["contract"].update({
        "amplitude_control": "per_target_period_output_scnr_calibration",
        "requested_output_scnr_db": requested_scnr_db,
        "calibration_source": calibration_source,
        "calibration_source_sha256": calibration_source_sha256,
        "calibration_schedule_db": {
            "min": min(value for schedule in schedules.values() for value in schedule),
            "max": max(value for schedule in schedules.values() for value in schedule),
        },
    })


def read_workbook(path: Path, sheet_name: str) -> dict[str, float | str]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on host setup
        raise SystemExit("生成参数需要 openpyxl；请安装后重试") from exc

    if not path.is_file():
        raise SystemExit(f"找不到参数工作簿：{path}")
    workbook = load_workbook(path, data_only=True, read_only=True)
    if sheet_name not in workbook.sheetnames:
        raise SystemExit(
            f"工作簿缺少工作表 {sheet_name!r}；实际工作表：{workbook.sheetnames}"
        )
    sheet = workbook[sheet_name]
    # 75 m/s 以上工作表的 N 列为“广域GMTI（远距离）”。行号是该表的
    # 长期参数布局；同时校验关键值，防止工作簿列或版本被误选。
    rows: dict[str, tuple[int, float | str]] = {
        "range_resolution_m": (3, 5.0),
        "bandwidth_mhz": (4, 80.0),
        "swath_m": (5, 10080.0),
        "swath_time_us": (6, 67.2),
        "pulse_width_us": (7, 150.0),
        "receive_duration_us": (8, 217.2),
        "ddc_sample_rate_mhz": (9, 60.0),
        "acquired_samples": (10, 13032.0),
        "fpga_uploaded_samples": (11, 13056.0),
        "receive_delay_us": (12, 488.0),
        "prt_us": (14, 769.2307692307693),
        "prf_hz": (15, 1300.0),
        "duty_cycle": (16, 0.195),
        "scan_speed_deg_per_s": (18, 20.0),
        "scan_step_deg": (21, 2.0),
        "pulses_per_beam": (20, 128.0),
    }
    extracted: dict[str, float | str] = {}
    for name, (row, expected) in rows.items():
        value = sheet.cell(row=row, column=14).value
        if value is None:
            raise SystemExit(f"工作簿 N{row} 缺少字段 {name}")
        number = numeric(value, name)
        if not math.isclose(number, float(expected), rel_tol=0.0, abs_tol=1.0e-6):
            raise SystemExit(
                f"工作簿字段 {name} 与预期不符：实际={number} 预期={expected}"
            )
        extracted[name] = number
    extracted["sheet_name"] = sheet_name
    extracted["column"] = "N"
    extracted["mode"] = "广域GMTI（远距离）"
    extracted["path"] = str(path.resolve())
    extracted["sha256"] = sha256(path)
    return extracted


def next_power_of_two(value: int) -> int:
    result = 1
    while result < value:
        result *= 2
    return result


def target_relative_radial_speed(
    theta_deg: float,
    explicit_truth_speed_mps: float | None = None,
) -> float:
    """返回相对平台的径向分量；显式参数控制最终 truth 的 vr_self_mps。

    当前生产 XML 的动态支持行是 30--98。目标 truth 的 Doppler 行同时受
    平台几何 Doppler 和目标相对径向 Doppler 影响；只给所有波位使用同一
    速度，会把扫描边缘的目标推到支持域外。这里把目标行置于动态支持域
    中部；不人为施加与该行域冲突的相对速度下限，最低速度由真值评价单独
    记录。
    """
    wavelength_m = 0.0182
    doppler_bin_hz = 1300.0 / float(PULSES_PER_BEAM)
    angle = math.radians(theta_deg)
    # algorithm geometry: platform ENU velocity is (0, speed), and the
    # commanded left-looking unit vector is (-cos(theta), -sin(theta)).
    platform_projection_mps = PLATFORM_SPEED_MPS * (-math.sin(angle))
    if explicit_truth_speed_mps is not None:
        # Stage2 truth's vr_self_mps is the target absolute velocity projected
        # onto the target LOS.  The target motion field is ground referenced,
        # so convert that requested truth value to the relative component that
        # must be added to the platform velocity.
        return explicit_truth_speed_mps - platform_projection_mps

    geometry_doppler_hz = 2.0 * platform_projection_mps / wavelength_m
    first_doppler_hz = geometry_doppler_hz - 0.5 * 1300.0
    desired_total_hz = first_doppler_hz + CFAR_TARGET_ROW * doppler_bin_hz
    relative = desired_total_hz / (
        MOTION_DOPPLER_AXIS_SIGN * 2.0 / wavelength_m
    )
    if MIN_RELATIVE_RADIAL_SPEED_MPS > 0.0 and abs(relative) < MIN_RELATIVE_RADIAL_SPEED_MPS:
        relative = (-MIN_RELATIVE_RADIAL_SPEED_MPS
                    if relative <= 0.0 else MIN_RELATIVE_RADIAL_SPEED_MPS)
    return relative


def target_velocity(
    theta_deg: float,
    index: int,
    explicit_truth_speed_mps: float | None = None,
) -> tuple[float, float, float]:
    """返回 ENU 目标速度，并可直接控制 truth 的 vr_self_mps。"""
    angle = math.radians(theta_deg)
    look_e = -math.cos(angle)
    look_n = -math.sin(angle)
    residual = target_relative_radial_speed(theta_deg, explicit_truth_speed_mps)
    # The target velocity is ground-referenced.  Adding the platform velocity
    # is required so ``residual`` remains the target/platform relative radial
    # speed used by the emitted LFM Doppler truth.
    return residual * look_e, PLATFORM_SPEED_MPS + residual * look_n, residual


def make_target(index: int, beam_id: int, expected_bin: int,
                scan_min_deg: float, scan_step_deg: float,
                snr_db: float,
                explicit_truth_speed_mps: float | None = None) -> dict:
    theta_deg = scan_min_deg + (beam_id - 1) * scan_step_deg
    ve_mps, vn_mps, residual = target_velocity(
        theta_deg, index, explicit_truth_speed_mps)
    platform_projection_mps = PLATFORM_SPEED_MPS * (-math.sin(math.radians(theta_deg)))
    truth_speed_mps = platform_projection_mps + residual
    return {
        "target_id": f"COOP91_{index + 1:03d}_B{beam_id:02d}_R{expected_bin:04d}",
        "enabled": True,
        "init": {
            "type": "beam_bin_azimuth_offset",
            "beam_id": beam_id,
            "expected_bin": expected_bin,
            "azimuth_offset_deg": 0.0,
        },
        "motion": {
            "type": "enu_velocity",
            "ve_mps": ve_mps,
            "vn_mps": vn_mps,
        },
        "amplitude": {
            "type": "snr_db",
            "snr_db": snr_db,
        },
        "visibility": {
            "type": "hard_gate",
            "single_beam_only": True,
            "visible_beam_span": 0,
        },
        "design": {
            "commanded_beam_center_deg": theta_deg,
            "relative_radial_speed_mps": residual,
            "truth_radial_speed_mps": truth_speed_mps,
        },
    }


def build_targets(target_count: int, snr_db: float, scan_min_deg: float,
                  scan_step_deg: float,
                  explicit_truth_speed_mps: float | None = None,
                  low_speed_target_mps: float | None = None,
                  low_speed_target_index: int | None = None) -> list[dict]:
    if target_count != 100:
        raise SystemExit("本功能测试场景固定为每周期 100 个目标")

    def speed_for_index(index: int) -> float | None:
        if (low_speed_target_mps is not None and
                low_speed_target_index == index):
            return low_speed_target_mps
        return explicit_truth_speed_mps

    targets: list[dict] = []
    for beam_id in range(1, BEAM_COUNT + 1):
        bins = [300, 1500, 2700]
        if beam_id == 1:
            # 低速样本放在窗内侧，给三周期相对运动留下距离余量。
            bins = [1500, 300, 2700]
        for expected_bin in bins:
            targets.append(
                make_target(len(targets), beam_id, expected_bin,
                            scan_min_deg, scan_step_deg, snr_db,
                            speed_for_index(len(targets)))
            )
    for beam_id in range(1, 11):
        targets.append(
            make_target(len(targets), beam_id, 3980,
                        scan_min_deg, scan_step_deg, snr_db,
                        speed_for_index(len(targets)))
        )
    if len(targets) != target_count:
        raise AssertionError(f"目标数量构造错误：{len(targets)}")
    return targets


def target_reference_positions(targets: list[dict], *, near_range_m: float,
                               sample_spacing_m: float,
                               platform_height_m: float,
                               scan_min_deg: float,
                               scan_step_deg: float) -> list[tuple[float, float]]:
    positions = []
    for target in targets:
        init = target["init"]
        beam_id = int(init["beam_id"])
        expected_bin = int(init["expected_bin"])
        theta = math.radians(scan_min_deg + (beam_id - 1) * scan_step_deg)
        slant = near_range_m + expected_bin * sample_spacing_m
        ground = math.sqrt(max(0.0, slant * slant - platform_height_m * platform_height_m))
        positions.append((-ground * math.cos(theta), -ground * math.sin(theta)))
    return positions


def minimum_spacing(positions: list[tuple[float, float]]) -> float:
    result = math.inf
    for index, (x0, y0) in enumerate(positions):
        for x1, y1 in positions[index + 1:]:
            result = min(result, math.hypot(x0 - x1, y0 - y1))
    return result


def build_scenario(workbook: dict[str, float | str], output_dir: Path,
                   *, platform_speed_mps: float, period_count: int,
                   target_count: int, snr_db: float,
                   paired_background_dir: Path | None,
                   background_input_dir: Path | None,
                   background_profile: str,
                   background_input_scale: float,
                   target_truth_radial_speed_mps: float | None,
                   low_speed_target_mps: float | None,
                   low_speed_target_index: int | None,
                   test_outline: Path | None) -> tuple[dict, dict]:
    fs_hz = float(workbook["ddc_sample_rate_mhz"]) * 1.0e6
    pulse_width_s = float(workbook["pulse_width_us"]) * 1.0e-6
    pulse_width_samples = round(fs_hz * pulse_width_s)
    acquired_samples = int(float(workbook["acquired_samples"]))
    uploaded_samples = int(float(workbook["fpga_uploaded_samples"]))
    valid_swath_samples = acquired_samples - pulse_width_samples
    near_range_m = 0.5 * C * float(workbook["receive_delay_us"]) * 1.0e-6
    valid_swath_m = 0.5 * C * valid_swath_samples / fs_hz
    farthest_range_m = near_range_m + valid_swath_m
    range_fft_len = next_power_of_two(uploaded_samples)
    fc_ghz = C / 0.0182 / 1.0e9
    beam_width_deg = math.degrees(0.886 * 0.0182 / 0.508)
    # 4096 是写入 int16 前的数值幅度标尺。它不改变生成器内部的
    # target/background 比，只避免目标的原始 chirp 振幅被量化成零。
    iq_domain_scale = 4096.0
    area_mean_power = 1.0e-4
    thermal_noise_power = 1.0e-5 * iq_domain_scale * iq_domain_scale
    packet_bytes = HEADER_BYTES + uploaded_samples * CHANNEL_COUNT * 4

    targets = build_targets(
        target_count, snr_db, SCAN_MIN_DEG, SCAN_STEP_DEG,
        target_truth_radial_speed_mps,
        low_speed_target_mps,
        low_speed_target_index)
    # Keep the emitted scenario self-describing for the three-period test.
    # The scalar snr_db remains the period-0 compatibility field; the explicit
    # schedule is overwritten below when an output-SCNR calibration table is
    # supplied.  This prevents the default one-click path from being mistaken
    # for an incomplete calibration scenario by the strict validator.
    for target in targets:
        target["amplitude"]["snr_db_by_period"] = [float(snr_db)] * period_count
    positions = target_reference_positions(
        targets,
        near_range_m=near_range_m,
        sample_spacing_m=RANGE_SAMPLE_M,
        platform_height_m=6000.0,
        scan_min_deg=SCAN_MIN_DEG,
        scan_step_deg=SCAN_STEP_DEG,
    )
    min_spacing_m = minimum_spacing(positions)

    profile_tag = "noiseonly" if background_profile == "thermal_noise_only" else "textured"
    case_id = (
        f"gmti_91mps_100targets_3period_scnr{snr_db:g}_20260901_"
        f"{profile_tag}_paired")
    area_clutter_enabled = background_profile != "thermal_noise_only"
    scenario = {
        "case_id": case_id,
        "output_dir": str(output_dir.resolve()),
        "scene_mode": "full",
        "truth_output": True,
        "waveform": {
            "fc_ghz": fc_ghz,
            "bandwidth_mhz": float(workbook["bandwidth_mhz"]),
            "fs_mhz": float(workbook["ddc_sample_rate_mhz"]),
            "tr_us": float(workbook["pulse_width_us"]),
            "prf_hz": float(workbook["prf_hz"]),
            "pulse_len": uploaded_samples,
            "acquired_pulse_len": acquired_samples,
            "pulse_num": PULSES_PER_BEAM,
            "d_chan_m": 0.17,
            "iq_data_type": IQ_DATA_TYPE,
            "new_protocol_channel_count": CHANNEL_COUNT,
            "new_protocol_read_channel_1": 1,
            "new_protocol_read_channel_2": 2,
        },
        "range_processing": {
            "range_fft_len": range_fft_len,
            "range_crop_start": 0,
            "range_crop_len": valid_swath_samples,
            "sample_delay_us": float(workbook["receive_delay_us"]),
        },
        "scan": {
            "scan_min_deg": SCAN_MIN_DEG,
            "scan_step_deg": SCAN_STEP_DEG,
            "scan_max_deg": SCAN_MAX_DEG,
            "beam_count": BEAM_COUNT,
            "beam_width_deg": beam_width_deg,
            "beam_index_base": 1,
        },
        "platform": {
            "speed_mps": platform_speed_mps,
            "height_m": 6000.0,
            "origin_lat_deg": 40.4512107203,
            "origin_lon_deg": 116.985931582,
            "origin_alt_m": 6000.0,
            "projection_ref_lon_deg": 117.0,
            "squint_side": 1,
        },
        "simulation_geometry": {
            "geometry_config_name": "algorithm_axis_x_north",
            "local_x_axis": "north",
            "local_y_axis": "east",
            "platform_heading_source": "velocity",
            "platform_heading_deg": 0.0,
            "beam_angle_reference": "algorithm",
            "beam_zero_direction": "algorithm",
            "beam_positive_direction": "algorithm",
            "beam_theta_offset_deg": 0.0,
            "range_geometry": "algorithm",
            "use_ground_range_for_position": True,
            "platform_origin_lat_deg": 40.4512107203,
            "platform_origin_lon_deg": 116.985931582,
            "platform_origin_alt_m": 6000.0,
            "projection_ref_lon_deg": 117.0,
            "squint_side": 1,
        },
        "channel_geometry": {
            "channel_1": {"name": "left_upper", "x_m": -0.085, "y_m": 0.0, "z_m": 0.085},
            "channel_2": {"name": "right_upper", "x_m": 0.085, "y_m": 0.0, "z_m": 0.085},
            "channel_3": {"name": "left_lower", "x_m": -0.085, "y_m": 0.0, "z_m": -0.085},
            "channel_4": {"name": "right_lower", "x_m": 0.085, "y_m": 0.0, "z_m": -0.085},
        },
        "random": {
            "period_start": 0,
            "period_count": period_count,
            "target_reference_period": 0,
            "beam_start": 1,
            "beam_count": 0,
            "random_seed": 20260831,
            "four_channel_phase_center_mode": "physical",
        },
        "scene": {
            "mode": "full",
            "output_signal_domain": "raw_lfm",
            "signal_only": False,
            "range_min_m": near_range_m,
            "range_max_m": farthest_range_m,
            "azimuth_min_deg": -31.0,
            "azimuth_max_deg": 31.0,
            "ground_z_m": 0.0,
            "clutter_amplitude_scale": iq_domain_scale,
            "area_clutter": {
                "enabled": area_clutter_enabled,
                "model": "continuous_texture",
                "scatterer_count": 0,
                "mean_power": area_mean_power,
                "texture_sigma": 0.4,
                "spatial_cell_m": 30.0,
                "azimuth_subcell_count": 9,
            },
            "strong_scatterers": {
                "enabled": False,
                "count": 0,
                "rcs_db_min": 10.0,
                "rcs_db_max": 30.0,
            },
            "line_scatterers": {
                "enabled": False,
                "line_count": 0,
                "points_per_line": 0,
                "rcs_db": 12.0,
            },
            "thermal_noise": {
                "enabled": True,
                "noise_power": thermal_noise_power,
                "include_target_only": False,
            },
        },
        "target": {
            "enabled": True,
            "amplitude_mode": "snr_db",
            "target_snr_db": snr_db,
        },
        "targets": targets,
    }
    if target_truth_radial_speed_mps is not None:
        scenario["target"]["truth_radial_speed_mps"] = (
            target_truth_radial_speed_mps)
    if low_speed_target_mps is not None:
        scenario["target"]["speed_override"] = {
            "target_index": low_speed_target_index,
            "truth_radial_speed_mps": low_speed_target_mps,
        }
    if paired_background_dir is not None:
        scenario["paired_background_output_dir"] = str(
            paired_background_dir.resolve())
    if background_input_dir is not None:
        scenario["background_input_dir"] = str(background_input_dir.resolve())
        scenario["background_input_scale"] = background_input_scale
    manifest = {
        "case_id": scenario["case_id"],
        "source": {
            "workbook": workbook,
            "workbook_note": "读取工作表“飞行速度75以上”的 N 列“广域GMTI（远距离）”",
            "test_outline": str(test_outline.resolve()) if test_outline else "not_provided",
            "false_alarm_test": "cancelled_by_user",
        },
        "contract": {
            "platform_speed_mps": platform_speed_mps,
            "period_count": period_count,
            "targets_per_period": target_count,
            "total_target_events": period_count * target_count,
            "target_scnr_db": snr_db,
            "snr_definition": "Stage2 target_snr_db: pulse-compressed target/background control; output CSI SCNR is measured separately when maps are enabled",
            "scan_min_deg": SCAN_MIN_DEG,
            "scan_max_deg": SCAN_MAX_DEG,
            "scan_step_deg": SCAN_STEP_DEG,
            "beam_count": BEAM_COUNT,
            "pulses_per_beam": PULSES_PER_BEAM,
            "channels": CHANNEL_COUNT,
            "iq_data_type": IQ_DATA_TYPE,
            "packet_bytes": packet_bytes,
            "packets_per_period": BEAM_COUNT * PULSES_PER_BEAM,
            "range_resolution_requirement_m": float(workbook["range_resolution_m"]),
            "range_resolution_from_80mhz_m": C / (2.0 * float(workbook["bandwidth_mhz"]) * 1.0e6),
            "nearest_slant_range_m": near_range_m,
            "valid_swath_m": valid_swath_m,
            "farthest_slant_range_m": farthest_range_m,
            "minimum_reference_target_spacing_m": min_spacing_m,
            "minimum_target_spacing_requirement_m": 500.0,
            "iq_domain_scale": iq_domain_scale,
            "iq_domain_scale_note": "int16 quantization scale only; no ADC calibration claim",
            "background_profile": background_profile,
            "background_profile_definition": (
                "thermal noise only; area clutter disabled"
                if background_profile == "thermal_noise_only" else
                "continuous textured area clutter plus thermal noise"
            ),
            "target_amplitude_ledger": (
                "truth/target_injection_amplitudes.csv"
            ),
        },
        "derived": {
            "pulse_width_samples": pulse_width_samples,
            "valid_swath_samples": valid_swath_samples,
            "fpga_padding_samples": uploaded_samples - acquired_samples,
            "range_sample_m": RANGE_SAMPLE_M,
            "range_fft_len": range_fft_len,
            "packet_bytes": packet_bytes,
            "expected_file_bytes_per_period": packet_bytes * BEAM_COUNT * PULSES_PER_BEAM,
        },
        "target_distribution": {
            "beam_histogram": {str(beam): sum(
                1 for target in targets if target["init"]["beam_id"] == beam
            ) for beam in range(1, BEAM_COUNT + 1)},
            "expected_bins": sorted({target["init"]["expected_bin"] for target in targets}),
            "all_targets_use_single_beam_gate": True,
            "reference_position_model": "same algorithm look vector as Stage2 resolver",
            "runtime_csi_cfar_row_support": [CFAR_ROW_START, CFAR_ROW_END],
            "target_row_design": CFAR_TARGET_ROW,
            "minimum_relative_radial_speed_mps": MIN_RELATIVE_RADIAL_SPEED_MPS,
            "target_truth_radial_speed_mps": target_truth_radial_speed_mps,
            "low_speed_target_mps": low_speed_target_mps,
            "low_speed_target_index": low_speed_target_index,
            "target_speed_control": (
                "explicit_common_speed_with_one_target_override"
                if (target_truth_radial_speed_mps is not None and
                    low_speed_target_mps is not None)
                else "explicit_common_truth_radial_speed"
                if target_truth_radial_speed_mps is not None
                else "geometry_compensated_to_target_row"),
        },
    }
    if paired_background_dir is not None:
        manifest["contract"].update({
            "paired_background_output_dir": str(paired_background_dir.resolve()),
            "paired_background_definition": (
                "same packet bytes after clutter/noise and before target injection"
            ),
        })
    if background_input_dir is not None:
        manifest["contract"].update({
            "background_input_dir": str(background_input_dir.resolve()),
            "background_input_scale": background_input_scale,
            "background_reuse_definition": (
                "target packets are recomposed from the saved C+N packet bytes; "
                "scene/clutter/noise are not regenerated"
            ),
        })
    return scenario, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output", type=Path,
                        help="run JSON 路径；默认 <output-dir>/scenario.json")
    parser.add_argument(
        "--workbook", type=Path, required=True,
        help="采集板参数工作簿；必须显式提供，避免依赖本机绝对路径")
    parser.add_argument(
        "--test-outline", type=Path, default=None,
        help="可选的测试大纲文件，用于写入场景审计元数据")
    parser.add_argument("--sheet", default="飞行速度75以上")
    parser.add_argument("--platform-speed-mps", type=float, default=PLATFORM_SPEED_MPS)
    parser.add_argument("--period-count", type=int, default=PERIOD_COUNT)
    parser.add_argument("--target-count", type=int, default=TARGET_COUNT)
    parser.add_argument("--scnr-db", type=float, default=TARGET_SCNR_DB)
    parser.add_argument(
        "--target-truth-radial-speed-mps", "--target-relative-radial-speed-mps",
        dest="target_truth_radial_speed_mps", type=float, default=None,
        help=("显式设置大多数目标的 truth vr_self_mps（m/s）；旧别名"
              " --target-relative-radial-speed-mps 仅为兼容，缺省按原有几何规则"
              "把目标放在设计 Doppler 行"))
    parser.add_argument(
        "--low-speed-target-mps", type=float, default=None,
        help=("仅覆盖一个低速锚点目标的 truth vr_self_mps（m/s），需同时指定"
              " --low-speed-target-index；用于验证最小可检测速度"))
    parser.add_argument(
        "--low-speed-target-index", type=int, default=None,
        help="低速锚点目标的 0-based 场景索引，默认可选 COOP91_001")
    parser.add_argument(
        "--paired-background-dir", type=Path, default=None,
        help=("空背景 C+N 数据目录；默认 <output-dir>/background_empty，"
              "与目标场景在同一次生成循环中写出"))
    parser.add_argument(
        "--background-input-dir", type=Path, default=None,
        help=("复用已有空背景 C+N 数据目录；只重注入目标，不重新生成"
              "场景、杂波和噪声；与 --paired-background-dir 互斥"))
    parser.add_argument(
        "--background-profile",
        choices=("thermal_noise_only", "textured_area"),
        default="thermal_noise_only",
        help=("初次生成空背景时使用的背景模型；thermal_noise_only 更适合"
              "输出 SCNR 校准，textured_area 保留纹理杂波对照"))
    parser.add_argument(
        "--background-input-scale", type=float, default=1.0,
        help=("复用背景时对每包 IQ 载荷应用的固定倍率；默认 1，"
              "低幅 int16 背景可用 8 改善量化"))
    parser.add_argument(
        "--target-snr-calibration", type=Path,
        help=("可选的逐目标/逐周期 output-SCNR 校准 CSV；必须覆盖 "
              "100 个目标×3 个周期，且 target_scnr_db 与 --scnr-db 一致"))
    parser.add_argument("--overwrite", action="store_true",
                        help="允许覆盖已有 scenario.json/manifest；不会删除已有 BIN")
    args = parser.parse_args()

    if not math.isfinite(args.platform_speed_mps) or args.platform_speed_mps <= 75.0:
        raise SystemExit("--platform-speed-mps 必须大于 75 m/s")
    if args.period_count != PERIOD_COUNT:
        raise SystemExit("本功能测试场景固定为 3 个周期")
    if not any(math.isclose(args.scnr_db, value, rel_tol=0.0, abs_tol=1.0e-9)
               for value in (18.0, 20.0)):
        raise SystemExit("本功能测试场景仅支持 18 dB 主场景或显式 20 dB 对照场景")
    if args.sheet != "飞行速度75以上":
        raise SystemExit("本功能测试必须读取工作表“飞行速度75以上”")
    for name, value in (
        ("--target-truth-radial-speed-mps", args.target_truth_radial_speed_mps),
        ("--low-speed-target-mps", args.low_speed_target_mps),
    ):
        if value is not None and not math.isfinite(value):
            raise SystemExit(f"{name} 必须是有限数")
    if (args.low_speed_target_mps is None) != (args.low_speed_target_index is None):
        raise SystemExit(
            "--low-speed-target-mps 与 --low-speed-target-index 必须同时指定")
    if args.low_speed_target_index is not None and not 0 <= args.low_speed_target_index < args.target_count:
        raise SystemExit(
            "--low-speed-target-index 必须位于 [0,target_count) 内")
    output_dir = args.output_dir.resolve()
    background_input_dir = (
        args.background_input_dir.resolve()
        if args.background_input_dir is not None else None
    )
    if background_input_dir == output_dir:
        raise SystemExit("--background-input-dir 必须与 --output-dir 不同")
    if background_input_dir is not None and args.paired_background_dir is not None:
        raise SystemExit("--background-input-dir 与 --paired-background-dir 互斥")
    if not math.isfinite(args.background_input_scale) or args.background_input_scale <= 0.0:
        raise SystemExit("--background-input-scale 必须为有限正数")
    if background_input_dir is None and not math.isclose(
            args.background_input_scale, 1.0, rel_tol=0.0, abs_tol=1.0e-12):
        raise SystemExit("--background-input-scale 需要同时指定 --background-input-dir")
    paired_background_dir = (
        args.paired_background_dir.resolve()
        if args.paired_background_dir is not None
        else (None if background_input_dir is not None
              else (output_dir / "background_empty").resolve())
    )
    if paired_background_dir is not None and paired_background_dir == output_dir:
        raise SystemExit("--paired-background-dir 必须与 --output-dir 不同")
    if background_input_dir is not None:
        source_manifest = background_input_dir / "data/period_files.csv"
        source_bins = list((background_input_dir / "data").glob("*.bin")) \
            if (background_input_dir / "data").is_dir() else []
        if not source_manifest.is_file() or not source_bins:
            raise SystemExit(
                "--background-input-dir 缺少可复用的 data/period_files.csv 或 BIN："
                f"{background_input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    output = (args.output or output_dir / "scenario.json").resolve()
    manifest_path = output_dir / "scenario_manifest.json"
    existing_data = list((output_dir / "data").glob("*.bin")) if (output_dir / "data").is_dir() else []
    background_manifest_path = (
        paired_background_dir / "data/period_files.csv"
        if paired_background_dir is not None else None
    )
    background_existing_data = (
        list((paired_background_dir / "data").glob("*.bin"))
        if paired_background_dir is not None and
        (paired_background_dir / "data").is_dir() else []
    )
    if ((output.exists() or manifest_path.exists() or existing_data or
         (background_manifest_path is not None and
          background_manifest_path.exists()) or background_existing_data) and
            not args.overwrite):
        raise SystemExit(
            f"输出目录或空背景目录已有场景文件或 BIN，拒绝覆盖：{output_dir} / "
            f"{paired_background_dir or '(复用既有输入背景)'}；"
            "如确认重生成请加 --overwrite"
        )

    workbook = read_workbook(args.workbook.resolve(), args.sheet)
    if args.test_outline is not None and not args.test_outline.is_file():
        raise SystemExit(f"找不到测试大纲：{args.test_outline}")
    scenario, manifest = build_scenario(
        workbook, output_dir,
        platform_speed_mps=args.platform_speed_mps,
        period_count=args.period_count,
        target_count=args.target_count,
        snr_db=args.scnr_db,
        paired_background_dir=paired_background_dir,
        background_input_dir=background_input_dir,
        background_profile=args.background_profile,
        background_input_scale=args.background_input_scale,
        target_truth_radial_speed_mps=args.target_truth_radial_speed_mps,
        low_speed_target_mps=args.low_speed_target_mps,
        low_speed_target_index=args.low_speed_target_index,
        test_outline=args.test_outline,
    )
    if args.target_snr_calibration:
        schedules = read_period_snr_calibration(
            args.target_snr_calibration.resolve(), scenario["targets"],
            args.period_count, args.scnr_db)
        apply_period_snr_calibration(
            scenario, manifest, args.target_snr_calibration.resolve(),
            schedules, args.scnr_db)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(scenario, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["scenario_json"] = str(output)
    manifest["scenario_json_sha256"] = sha256(output)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已生成 Stage2 场景：{output}")
    print(f"已生成场景清单：{manifest_path}")
    if paired_background_dir is not None:
        print(f"空背景场景目录：{paired_background_dir}")
    if background_input_dir is not None:
        print(f"复用空背景输入：{background_input_dir}")
    print(
        "参数："
        f" speed={args.platform_speed_mps:g}m/s periods={args.period_count}"
        f" targets/period={args.target_count} SCNR={args.scnr_db:g}dB"
    )
    print(
        "扫描："
        f" {SCAN_MIN_DEG:g}..{SCAN_MAX_DEG:g}deg step={SCAN_STEP_DEG:g}deg"
        f" beams={BEAM_COUNT} pulses/beam={PULSES_PER_BEAM}"
    )
    print(f"目标参考位置最小间距：{manifest['contract']['minimum_reference_target_spacing_m']:.3f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
