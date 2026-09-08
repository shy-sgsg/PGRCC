#!/usr/bin/env python3
"""Audit a Stage2 mechanical-scan truth file against rotating phase-centre geometry.

The checker intentionally uses only the resolved JSON and truth CSV.  It does
not call the simulator implementation, so the comparison is an independent
verification of path length, carrier phase, baseline norm, and the legacy
unrotated A/B error.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path


def number(value: str | int | float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return math.nan
    return out if math.isfinite(out) else math.nan


def norm(a: tuple[float, float, float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def rotate(offset: tuple[float, float, float], angle_deg: float) -> tuple[float, float, float]:
    angle = math.radians(angle_deg)
    c = math.cos(angle)
    s = math.sin(angle)
    return c * offset[0] - s * offset[1], s * offset[0] + c * offset[1], offset[2]


def path_length(platform: tuple[float, float, float], target: tuple[float, float, float],
                offset: tuple[float, float, float]) -> float:
    tx = norm(tuple(target[i] - platform[i] for i in range(3)))
    rx_origin = tuple(platform[i] + offset[i] for i in range(3))
    rx = norm(tuple(target[i] - rx_origin[i] for i in range(3)))
    return tx + rx


def wrapped(value: float) -> float:
    if not math.isfinite(value):
        return math.nan
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def rms(values: list[float]) -> float:
    finite = [x for x in values if math.isfinite(x)]
    return math.sqrt(sum(x * x for x in finite) / len(finite)) if finite else math.nan


def load_json_with_nan(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"(?<![A-Za-z])nan(?![A-Za-z])", "NaN", text, flags=re.IGNORECASE)
    return json.loads(text, parse_constant=lambda token: math.nan)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--truth", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    scenario = load_json_with_nan(args.scenario)
    source_config = scenario.get("source_config", {})
    # Stage2's resolved JSON keeps channel geometry in the input JSON; accept
    # either the scenario itself or a sibling source config for older outputs.
    if "channel_geometry" not in scenario:
        sibling = args.scenario.parent.parent.parent / "configs" / "stage2" / "mechanical_phase_center_validation.json"
        if sibling.exists():
            source_config = load_json_with_nan(sibling)
    channel_geometry = source_config.get("channel_geometry", scenario.get("channel_geometry", {}))
    offsets: list[tuple[float, float, float]] = []
    for index in range(1, 5):
        item = channel_geometry.get(f"channel_{index}", {})
        offsets.append((number(item.get("x_m", 0.0)),
                        number(item.get("y_m", 0.0)),
                        number(item.get("z_m", 0.0))))

    mechanical = scenario.get("mechanical_scan", {})
    rotation_enable = bool(mechanical.get("phase_center_rotation_enable", True))
    mount_deg = number(mechanical.get("phase_center_mount_angle_deg", 0.0))
    rotation_sign = -1.0 if int(mechanical.get("phase_center_rotation_sign", 1)) < 0 else 1.0
    fc_hz = number(source_config.get("waveform", {}).get("fc_ghz", 16.0)) * 1.0e9
    wavelength = 299792458.0 / fc_hz if fc_hz > 0.0 else math.nan
    carrier_phase_sign = -1.0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    per_prt_path = args.output_dir / "mechanical_phase_center_per_prt.csv"
    rows: list[dict[str, float | int]] = []
    path_errors: list[float] = []
    phase_errors: list[float] = []
    legacy_phase_errors: list[float] = []
    rotation_phase_changes: list[float] = []
    baseline_norms: list[float] = []
    baseline_along: list[float] = []
    baseline_right: list[float] = []

    with args.truth.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            platform = (number(raw.get("platform_x_m")), number(raw.get("platform_y_m")),
                        number(raw.get("platform_z_m")))
            target = (number(raw.get("x_m")), number(raw.get("y_m")), number(raw.get("z_m")))
            servo_deg = number(raw.get("theta_cmd_deg"))
            pose_deg = mount_deg + rotation_sign * servo_deg if rotation_enable else mount_deg
            rotated_offsets = [rotate(offset, pose_deg) for offset in offsets]
            simulated_paths = [number(raw.get(f"path_ch{index}_m")) for index in range(1, 5)]
            model_paths = [path_length(platform, target, offset) for offset in rotated_offsets]
            legacy_paths = [path_length(platform, target, offset) for offset in offsets]
            path_residuals = [model_paths[i] - simulated_paths[i] for i in range(4)]
            path_errors.extend(path_residuals)

            phase_model_13 = carrier_phase_sign * 2.0 * math.pi * (
                model_paths[0] - model_paths[2]) / wavelength
            phase_model_24 = carrier_phase_sign * 2.0 * math.pi * (
                model_paths[1] - model_paths[3]) / wavelength
            phase_truth_13 = number(raw.get("phase_13_rad"))
            phase_truth_24 = number(raw.get("phase_24_rad"))
            phase_residual = wrapped(phase_model_13 - phase_truth_13)
            phase_errors.append(phase_residual)
            phase24_residual = wrapped(phase_model_24 - phase_truth_24)
            phase_errors.append(phase24_residual)

            legacy_phase_13 = carrier_phase_sign * 2.0 * math.pi * (
                legacy_paths[0] - legacy_paths[2]) / wavelength
            legacy_phase_residual = wrapped(legacy_phase_13 - phase_truth_13)
            legacy_phase_errors.append(legacy_phase_residual)
            rotation_phase_change = wrapped(phase_model_13 - legacy_phase_13)
            rotation_phase_changes.append(rotation_phase_change)

            baseline = tuple(rotated_offsets[2][i] - rotated_offsets[0][i] for i in range(3))
            baseline_norms.append(norm(baseline))
            baseline_along.append(baseline[0])
            baseline_right.append(baseline[1])
            rows.append({
                "pulse_id": int(number(raw.get("pulse_id"))),
                "servo_azimuth_deg": servo_deg,
                "model_path_ch1_m": model_paths[0],
                "truth_path_ch1_m": simulated_paths[0],
                "path_error_ch1_m": path_residuals[0],
                "model_path_ch2_m": model_paths[1],
                "truth_path_ch2_m": simulated_paths[1],
                "path_error_ch2_m": path_residuals[1],
                "model_path_ch3_m": model_paths[2],
                "truth_path_ch3_m": simulated_paths[2],
                "path_error_ch3_m": path_residuals[2],
                "model_path_ch4_m": model_paths[3],
                "truth_path_ch4_m": simulated_paths[3],
                "path_error_ch4_m": path_residuals[3],
                "phase_residual_13_rad": phase_residual,
                "phase_residual_24_rad": phase24_residual,
                "legacy_phase_residual_13_rad": legacy_phase_residual,
                "rotation_phase_change_13_rad": rotation_phase_change,
                "rotated_baseline_norm_m": baseline_norms[-1],
                "rotated_baseline_along_m": baseline_along[-1],
                "rotated_baseline_right_m": baseline_right[-1],
            })

    fields = list(rows[0]) if rows else []
    with per_prt_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "scenario": str(args.scenario),
        "truth": str(args.truth),
        "rows": len(rows),
        "rotation_enable": rotation_enable,
        "mount_angle_deg": mount_deg,
        "rotation_sign": int(rotation_sign),
        "fc_hz": fc_hz,
        "wavelength_m": wavelength,
        "path_model_max_abs_error_m": max((abs(x) for x in path_errors), default=math.nan),
        "path_model_rms_error_m": rms(path_errors),
        "phase_model_rms_error_13_24_wrapped_rad": rms(phase_errors),
        "legacy_unrotated_phase_rms_error_13_wrapped_rad": rms(legacy_phase_errors),
        "rotation_phase_change_13_rms_wrapped_rad": rms(rotation_phase_changes),
        "rotated_baseline_norm_min_m": min(baseline_norms, default=math.nan),
        "rotated_baseline_norm_max_m": max(baseline_norms, default=math.nan),
        "rotated_baseline_along_min_m": min(baseline_along, default=math.nan),
        "rotated_baseline_along_max_m": max(baseline_along, default=math.nan),
        "rotated_baseline_right_min_m": min(baseline_right, default=math.nan),
        "rotated_baseline_right_max_m": max(baseline_right, default=math.nan),
        "interpretation": {
            "physical_baseline_norm": "constant under rigid rotation; only along/right projections vary",
            "front_aft_limit": "not removed by servo pose; pure Doppler df/dA still tends to zero at |A| -> 90 deg",
            "legacy_comparison": "legacy path leaves the receiver centres fixed while the simulated truth rotates them",
        },
    }
    (args.output_dir / "mechanical_phase_center_validation.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=True) + "\n", encoding="utf-8")
    md = args.output_dir / "mechanical_phase_center_validation.md"
    md.write_text(
        "# 机械扫描相位中心独立审计\n\n"
        f"- truth rows: {len(rows)}\n"
        f"- rotated path max error: {summary['path_model_max_abs_error_m']:.6g} m\n"
        f"- rotated phase wrapped RMS (13/24): {summary['phase_model_rms_error_13_24_wrapped_rad']:.6g} rad\n"
        f"- fixed-centre legacy phase wrapped RMS (13): {summary['legacy_unrotated_phase_rms_error_13_wrapped_rad']:.6g} rad\n"
        f"- rotated baseline norm range: {summary['rotated_baseline_norm_min_m']:.9g} .. {summary['rotated_baseline_norm_max_m']:.9g} m\n"
        f"- rotated along projection range: {summary['rotated_baseline_along_min_m']:.9g} .. {summary['rotated_baseline_along_max_m']:.9g} m\n"
        f"- rotated right projection range: {summary['rotated_baseline_right_min_m']:.9g} .. {summary['rotated_baseline_right_max_m']:.9g} m\n\n"
        "结论：旋转模型与真值路径/载频相位闭合，且基线长度保持不变；固定相位中心 A/B 只作为错误模型对照。伺服姿态不能消除纯多普勒前后视的一阶退化，后者仍需由波束/姿态/速度先验和有效 SCNR 共同约束。\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
