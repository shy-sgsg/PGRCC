#!/usr/bin/env python3
"""T4：当前 GMTI 相位测角的理想与生产 Jacobian 理论曲线。

理想模型使用 ``phi=2*pi*d/lambda*sin(theta)`` 与
``sigma_phi=1/sqrt(gamma_phi)``。若给出正式 ``detection_results`` CSV，
则从生产定位器已写出的 ``loc_jacobian_crossrange_m_per_phase_rad`` 反推
``dtheta/dphi``，而不以理想 P38 斜率替代实际 CTDR/P38 链。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import xml.etree.ElementTree as ET
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti_scnr_eval_matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from scnr_eval_lib import db_to_linear, ensure_dir, first_crossing, parse_grid, read_csv, write_csv


ROOT = Path(__file__).resolve().parents[2]


def xml_values(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    return {node.tag: node.text.strip() for node in root.iter() if node.text and node.text.strip()}


def required_float(values: dict[str, str], *keys: str) -> tuple[float, str]:
    for key in keys:
        try:
            value = float(values[key])
        except (KeyError, ValueError):
            continue
        if math.isfinite(value) and value > 0.0:
            return value, key
    raise ValueError(f"XML 中缺少正数参数：{keys}")


def number(row: dict[str, str], key: str) -> float:
    try:
        return float(row.get(key, ""))
    except ValueError:
        return float("nan")


def production_jacobian_by_angle(paths: list[Path]) -> dict[float, list[float]]:
    """从生产写出的交叉向相位 Jacobian 转为角度 Jacobian（rad/rad）。"""
    output: dict[float, list[float]] = {}
    for path in paths:
        for row in read_csv(path):
            jac_cross = number(row, "loc_jacobian_crossrange_m_per_phase_rad")
            target_e, target_n = number(row, "target_e_truth"), number(row, "target_n_truth")
            platform_e, platform_n = number(row, "platform_e"), number(row, "platform_n")
            angle = number(row, "truth_angle_deg")
            ground = math.hypot(target_e - platform_e, target_n - platform_n)
            value = abs(jac_cross / ground) if ground > 1.0 and math.isfinite(jac_cross) else float("nan")
            if math.isfinite(angle) and math.isfinite(value):
                output.setdefault(angle, []).append(value)
    return output


def nearest_angle_value(values: dict[float, list[float]], angle: float) -> tuple[float | None, float | None]:
    candidates = [(abs(key - angle), key, item) for key, items in values.items() for item in items]
    if not candidates:
        return None, None
    _, source_angle, value = min(candidates, key=lambda item: item[0])
    return float(value), float(source_angle)


def center_cut_calibration_path(root: Path, angle: float) -> Path | None:
    """Locate the independent centre-CUT/output-SCNR calibration summary."""
    token = f"angle_p{int(round(angle))}p000"
    candidates = [
        root / f"calibration_angle{int(round(angle))}_fine_t6" / "calibration" /
        token / "processed_output_snr_calibration.csv",
        root / "calibration" / token / "processed_output_snr_calibration.csv",
        root / token / "processed_output_snr_calibration.csv",
        root.parent / f"calibration_angle{int(round(angle))}_fine_t6" / "calibration" /
        token / "processed_output_snr_calibration.csv",
    ]
    return next((path for path in candidates if path.is_file()), None)


def center_cut_mapping(root: Path, angle: float) -> dict[str, object] | None:
    """Fit centre-CUT SCNR against the formal fixed-support SCNR.

    The fit uses a separate calibration realization.  It is a transfer
    function between two output planes, not a fit to the evaluation angle
    errors.
    """
    path = center_cut_calibration_path(root, angle)
    if path is None:
        return None
    x_values: list[float] = []
    y_values: list[float] = []
    for row in read_csv(path):
        x = number(row, "processed_output_scnr_db")
        y = number(row, "unit_cut_scnr_db")
        if math.isfinite(x) and math.isfinite(y):
            x_values.append(x)
            y_values.append(y)
    if len(x_values) < 2:
        return None
    x = np.asarray(x_values, dtype=np.float64)
    y = np.asarray(y_values, dtype=np.float64)
    slope, intercept = np.polyfit(x, y, 1)
    residual = y - (slope * x + intercept)
    total = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - float(np.sum(residual ** 2)) / total if total > 0.0 else float("nan")
    if not (math.isfinite(float(slope)) and float(slope) > 0.0 and
            math.isfinite(float(intercept))):
        return None
    return {
        "path": str(path.resolve()),
        "slope_db_per_db": float(slope),
        "intercept_db": float(intercept),
        "r2": r2,
        "max_abs_residual_db": float(np.max(np.abs(residual))),
        "sample_count": len(x_values),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, default=ROOT / "gmti.xml")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/gmti_scnr_eval/angle_theory"))
    parser.add_argument("--scnr-grid", default="0,2,4,6,8,10,12,14,16,18,20,22,24")
    parser.add_argument("--angles", default="0,30,45")
    parser.add_argument("--production-detection-csv", type=Path, action="append", default=[],
                        help="可重复指定；由正式运行 detection_results*.csv 提供生产 Jacobian")
    parser.add_argument("--production-floor-deg", type=float, default=0.0,
                        help="高 SCNR 系统误差底；只可在正式实测后按证据设置")
    parser.add_argument("--rmse-target-deg", type=float, default=0.2)
    parser.add_argument(
        "--center-cut-calibration-root", type=Path,
        default=Path("outputs/gmti_scnr_eval/output_scnr_mc_standard_20260827/calibration_centers_fine_t6"),
        help="独立校准根目录；用于把中心 CUT 相位 SCNR 映射到固定支撑 output SCNR",
    )
    args = parser.parse_args()
    if args.production_floor_deg < 0.0 or args.rmse_target_deg <= 0.0:
        raise SystemExit("测角误差参数非法")
    values = xml_values(args.xml.resolve())
    fc_ghz, fc_key = required_float(values, "fc")
    lambda_m = 299_792_458.0 / (fc_ghz * 1.0e9)
    d_m, d_key = required_float(values, "d_chan", "d_channel")
    angles = [float(token.strip()) for token in args.angles.split(",") if token.strip()]
    gamma_db = np.asarray(parse_grid(args.scnr_grid), dtype=np.float64)
    gamma = np.asarray([db_to_linear(item) for item in gamma_db])
    production = production_jacobian_by_angle([path.resolve() for path in args.production_detection_csv])
    out = ensure_dir(args.output_dir.resolve())
    calibration_root = args.center_cut_calibration_root.resolve()
    summary: list[dict] = []
    all_rows: list[dict] = []
    target_rad = math.radians(args.rmse_target_deg)
    for angle in angles:
        cos_angle = math.cos(math.radians(angle))
        if cos_angle <= 0.0:
            raise SystemExit("角度必须位于 (-90°,90°)")
        ideal_j = lambda_m / (2.0 * math.pi * d_m * cos_angle)
        production_j, production_source_angle = nearest_angle_value(production, angle)
        source = "production_detection_csv" if production_j is not None else "ideal_geometry_pending_formal_jacobian"
        if production_j is None:
            production_j = ideal_j
        sigma_phi = 1.0 / np.sqrt(gamma)
        ideal_rmse_deg = np.degrees(ideal_j * sigma_phi)
        production_rmse_deg = np.degrees(np.sqrt((production_j * sigma_phi) ** 2 +
                                                  math.radians(args.production_floor_deg) ** 2))
        mapping = center_cut_mapping(calibration_root, angle)
        if mapping is not None:
            # The phase model's gamma is the local centre-CUT SCNR.  Invert
            # the independently measured centre-CUT transfer function to put
            # the curve on the formal fixed-support output-SCNR axis.
            mapped_output_db = (gamma_db - float(mapping["intercept_db"])) / float(mapping["slope_db_per_db"])
            mapped_source = "independent_center_cut_calibration"
            mapped_crossing = first_crossing(mapped_output_db, production_rmse_deg,
                                             args.rmse_target_deg, "below")
        else:
            # Keep an explicit fallback for old archives.  These x values are
            # not called formal output-SCNR and are not used for a threshold
            # claim when the mapping is absent.
            mapped_output_db = gamma_db.copy()
            mapped_source = "phase_effective_unmapped"
            mapped_crossing = None
        rows = []
        for db, linear, phase_std, ideal, prod, mapped_db in zip(
                gamma_db, gamma, sigma_phi, ideal_rmse_deg, production_rmse_deg,
                mapped_output_db):
            row = {
                "truth_angle_deg": angle, "scnr_phase_eff_db": float(db), "scnr_phase_eff_linear": float(linear),
                "center_cut_scnr_db": float(db),
                "output_scnr_db_mapped": float(mapped_db),
                "angle_scnr_axis": mapped_source,
                "phase_std_rad": float(phase_std), "lambda_m": lambda_m, "d_chan_m": d_m,
                "ideal_j_theta_phi_rad_per_rad": ideal_j,
                "production_j_theta_phi_rad_per_rad": production_j,
                "production_jacobian_source": source,
                "production_jacobian_source_angle_deg": production_source_angle,
                "production_floor_deg": args.production_floor_deg,
                "ideal_rmse_theta_deg": float(ideal), "production_jacobian_rmse_theta_deg": float(prod),
                "rmse_target_deg": args.rmse_target_deg,
            }
            if mapping is not None:
                row.update({
                    "center_cut_mapping_slope_db_per_db": float(mapping["slope_db_per_db"]),
                    "center_cut_mapping_intercept_db": float(mapping["intercept_db"]),
                    "center_cut_mapping_r2": float(mapping["r2"]),
                    "center_cut_mapping_max_abs_residual_db": float(mapping["max_abs_residual_db"]),
                    "center_cut_mapping_sample_count": int(mapping["sample_count"]),
                    "center_cut_mapping_source": str(mapping["path"]),
                })
            rows.append(row)
            all_rows.append(row)
        output_name = f"angle_theory_{int(angle) if angle.is_integer() else str(angle).replace('.', 'p')}deg.csv"
        write_csv(out / output_name, rows)
        summary.append({
            "truth_angle_deg": angle, "lambda_m": lambda_m, "d_chan_m": d_m,
            "ideal_j_theta_phi_rad_per_rad": ideal_j,
            "production_j_theta_phi_rad_per_rad": production_j,
            "production_jacobian_source": source,
            "ideal_crossing_phase_scnr_db": first_crossing(gamma_db, ideal_rmse_deg, args.rmse_target_deg, "below"),
            "production_crossing_phase_scnr_db": first_crossing(gamma_db, production_rmse_deg, args.rmse_target_deg, "below"),
            "production_crossing_output_scnr_db": mapped_crossing,
            "center_cut_mapping_source": (str(mapping["path"]) if mapping is not None else ""),
            "center_cut_mapping_slope_db_per_db": (float(mapping["slope_db_per_db"]) if mapping is not None else float("nan")),
            "center_cut_mapping_intercept_db": (float(mapping["intercept_db"]) if mapping is not None else float("nan")),
            "center_cut_mapping_r2": (float(mapping["r2"]) if mapping is not None else float("nan")),
            "center_cut_mapping_max_abs_residual_db": (float(mapping["max_abs_residual_db"]) if mapping is not None else float("nan")),
            "center_cut_mapping_sample_count": (int(mapping["sample_count"]) if mapping is not None else 0),
            "angle_scnr_axis": mapped_source,
        })
    write_csv(out / "angle_theory_summary.csv", summary)
    (out / "angle_theory_provenance.json").write_text(json.dumps({
        "xml": str(args.xml.resolve()), "fc_xml_key": fc_key, "d_xml_key": d_key,
        "lambda_m": lambda_m, "d_chan_m": d_m, "formula":
        "ideal sigma_theta=lambda/(2*pi*d*cos(theta))*1/sqrt(gamma_phi); gamma_phi is centre-CUT SCNR and is mapped to formal support output SCNR using independent calibration",
        "production_detection_csv": [str(path.resolve()) for path in args.production_detection_csv],
        "center_cut_calibration_root": str(calibration_root),
        "production_floor_deg": args.production_floor_deg,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fig, axis = plt.subplots(figsize=(7.6, 4.8))
    for angle in angles:
        rows = [row for row in all_rows if row["truth_angle_deg"] == angle]
        axis.plot([row["output_scnr_db_mapped"] for row in rows],
                  [row["ideal_rmse_theta_deg"] for row in rows], linestyle="--", label=f"{angle:g}° 理想")
        axis.plot([row["output_scnr_db_mapped"] for row in rows],
                  [row["production_jacobian_rmse_theta_deg"] for row in rows], marker="o", label=f"{angle:g}° 生产 Jacobian")
    axis.axhline(args.rmse_target_deg, color="tab:red", linestyle="--", label=f"RMSE={args.rmse_target_deg:g}°")
    axis.set(xlabel="固定支撑 output SCNR（dB）", ylabel="理论方位 RMSE (°)", title="0°/30°/45° GMTI 相位测角理论（中心 CUT 标定映射）")
    axis.set_ylim(bottom=0.0); axis.grid(True, alpha=0.3); axis.legend(ncol=2, fontsize=8); fig.tight_layout()
    fig.savefig(out / "F09_F12_angle_theory.png", dpi=180); plt.close(fig)
    print(f"[PASS] 三角度测角理论已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
