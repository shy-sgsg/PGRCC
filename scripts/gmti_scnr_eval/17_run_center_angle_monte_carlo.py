#!/usr/bin/env python3
"""按 desired output-SCNR 反推控制量并运行 0/30/45°中心波位 Monte Carlo。

``16_run_standard_output_snr_mc.py`` 保留了真实 CUDA 生产链，但其输入参数
仍是 Stage2 的 ``target_snr_db``。本脚本只把该参数当控制量：先读取已经通过
sanity check 的线性标定，再为每个期望输出 SCNR 档位反解控制量。正式曲线的
x 轴由 ``desired_scnr_out_db`` / 实测固定支撑 SCNR 定义，绝不把控制量冒充
输出 SCNR。
"""

from __future__ import annotations

import argparse
import json
import math
import shlex
import subprocess
import sys
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from scnr_eval_lib import ensure_dir, parse_grid, read_csv  # noqa: E402


def finite(value: object) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def angle_token(angle: float) -> str:
    return f"angle_{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def load_mapping(calibration_dir: Path, angle: float) -> tuple[float, float]:
    rows = [row for row in read_csv(calibration_dir / "calibration_mapping.csv")
            if math.isfinite(finite(row.get("angle_deg")))
            and abs(finite(row.get("angle_deg")) - angle) < 1e-6]
    if not rows:
        raise SystemExit(f"标定缺少角度 {angle:g}°")
    slope = finite(rows[0].get("slope_db_per_db"))
    intercept = finite(rows[0].get("intercept_db"))
    if not (math.isfinite(slope) and math.isfinite(intercept) and slope > 0.0):
        raise SystemExit(f"角度 {angle:g}° 的 output-SCNR 标定无效：slope={slope}, intercept={intercept}")
    return slope, intercept


def require_validation(calibration_dir: Path, max_error_db: float) -> dict:
    path = calibration_dir / "output_scnr_transfer_validation.json"
    if not path.is_file():
        raise SystemExit(f"缺少标定验证 {path}，请先运行 16_validate_output_scnr_transfer.py")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not bool(document.get("all_pass")):
        raise SystemExit("output-SCNR 标定未通过 sanity check，拒绝继续正式 MC")
    for row in document.get("angles", []):
        if finite(row.get("max_abs_residual_db")) > max_error_db:
            raise SystemExit(f"标定残差超过限制：{row}")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration-dir", type=Path, required=True)
    parser.add_argument("--angles", default="0,30,45")
    parser.add_argument("--output-scnr-grid", default="6,8,10,12,14,16,18,20,22,24")
    parser.add_argument("--trials-per-level", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument("--theory-samples", type=int, default=100000)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("outputs/gmti_scnr_eval/output_scnr_mc_center_angles"))
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--min-points", type=int, default=6)
    parser.add_argument("--small-min-points", type=int, default=3,
                        help="CSI 小簇恢复的最小点数；必须不大于 min-points")
    parser.add_argument("--small-peak-db", type=float, default=20.0,
                        help="CSI 小簇恢复的峰值/局部背景门限（dB）")
    parser.add_argument("--keep-debug-maps", action="store_true")
    parser.add_argument("--strict-online-no-prior", action="store_true",
                        help="禁止把 N-only 背景统计写回生产 XML；用于无运行前先验验证")
    args = parser.parse_args()
    if args.trials_per_level < 1:
        raise SystemExit("--trials-per-level 必须为正")
    calibration_dir = args.calibration_dir.resolve()
    validation = require_validation(calibration_dir, 1.0)
    angles = parse_grid(args.angles)
    desired_grid = parse_grid(args.output_scnr_grid)
    output_dir = ensure_dir(args.output_dir.resolve())
    run_root = ensure_dir(output_dir / "runs")
    records: list[dict[str, object]] = []
    runner = SCRIPT_DIR / "16_run_standard_output_snr_mc.py"
    for angle_index, angle in enumerate(angles):
        slope, intercept = load_mapping(calibration_dir, angle)
        controls = [(desired - intercept) / slope for desired in desired_grid]
        if any(not math.isfinite(value) for value in controls):
            raise SystemExit(f"角度 {angle:g}° 反解控制量失败")
        angle_root = ensure_dir(run_root / angle_token(angle))
        command = [sys.executable, str(runner), "--output-root", str(angle_root),
                   "--angle", f"{angle:g}", "--seed", str(args.seed + angle_index),
                   "--input-snr-grid=" + ",".join(f"{x:.9g}" for x in controls),
                   "--trials-per-level", str(args.trials_per_level),
                   "--theory-samples", str(args.theory_samples),
                   "--build-dir", str(args.build_dir), "--min-points", str(args.min_points),
                   "--small-min-points", str(args.small_min_points),
                   "--small-peak-db", str(args.small_peak_db)]
        if args.keep_debug_maps:
            command.append("--keep-debug-maps")
        if args.strict_online_no_prior:
            command.append("--strict-online-no-prior")
        log = angle_root / "run_command.log"
        with log.open("w", encoding="utf-8") as stream:
            stream.write("$ " + shlex.join(command) + "\n")
            result = subprocess.run(command, cwd=ROOT, stdout=stream,
                                    stderr=subprocess.STDOUT, check=False)
        record = {
            "angle_deg": angle, "seed": args.seed + angle_index,
            "desired_output_scnr_db": desired_grid,
            "input_snr_db_control": controls,
            "slope_db_per_db": slope, "intercept_db": intercept,
            "output_root": str(angle_root), "angle_dir": str(angle_root / angle_token(angle)),
            "command": command, "command_string": shlex.join(command),
            "log": str(log), "exit_code": result.returncode,
            "strict_online_no_prior": bool(args.strict_online_no_prior),
            "status": "pass" if result.returncode == 0 else "fail",
        }
        records.append(record)
        if result.returncode:
            (output_dir / "run_manifest.json").write_text(json.dumps({
                "calibration_dir": str(calibration_dir), "validation": validation,
                "angles": records, "status": "fail",
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raise SystemExit(f"角度 {angle:g}° Monte Carlo 失败，见 {log}")
    manifest = {
        "calibration_dir": str(calibration_dir), "validation": validation,
        "angles": records, "desired_output_scnr_grid_db": desired_grid,
        "trials_per_level": args.trials_per_level, "base_seed": args.seed,
        "background": "thermal_noise_only_no_area_clutter",
        "axis_definition": "fixed production S+N support excess over N-only ensemble P0; S-only reference only fixes support",
        "target_snr_db_role": "control variable only; not a formal x-axis",
        "min_points": args.min_points, "small_min_points": args.small_min_points,
        "small_peak_db": args.small_peak_db,
        "strict_online_no_prior": bool(args.strict_online_no_prior),
        "status": "pass",
    }
    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False,
                                                              indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 中心角 output-SCNR Monte Carlo 完成：{output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
