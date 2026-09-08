#!/usr/bin/env python3
"""为 0°/30°/45°建立 target_snr_db -> CSI 输出 SCNR 的可审计标定。

本脚本只负责调用真实 CUDA 链路和汇总标定，不把 target_snr_db 当正式横轴。
输出的 ``calibration_mapping.csv`` 供后续 desired-output-SCNR MC 反推内部
控制量；每个角度的原始审计仍保留在 ``calibration/angle_*/``。
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from scnr_eval_lib import ensure_dir, parse_grid, read_csv, write_csv  # noqa: E402


def angle_token(angle: float) -> str:
    return f"angle_{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def finite(value: object) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--angles", default="0,30,45")
    parser.add_argument("--calibration-grid", default="-20,-10,0,10,20")
    parser.add_argument("--trials-per-level", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument("--theory-samples", type=int, default=20000)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/gmti_scnr_eval/output_scnr_mc"))
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--keep-debug-maps", action="store_true")
    args = parser.parse_args()

    angles = parse_grid(args.angles)
    controls = parse_grid(args.calibration_grid)
    if args.trials_per_level < 1:
        raise SystemExit("--trials-per-level 必须为正")
    output_dir = ensure_dir(args.output_dir.resolve())
    calibration_root = ensure_dir(output_dir / "calibration")
    script = SCRIPT_DIR / "16_run_standard_output_snr_mc.py"
    command = [sys.executable, str(script), "--output-root", str(calibration_root),
               "--angles", ",".join(f"{x:g}" for x in angles),
               "--input-snr-grid=" + ",".join(f"{x:g}" for x in controls),
               "--trials-per-level", str(args.trials_per_level), "--seed", str(args.seed),
               "--theory-samples", str(args.theory_samples), "--build-dir", str(args.build_dir)]
    if args.keep_debug_maps:
        command.append("--keep-debug-maps")
    log = output_dir / "calibration_command.log"
    with log.open("w", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(command) + "\n")
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise SystemExit(f"CUDA 标定失败，退出码={result.returncode}，见 {log}")

    mapping_rows: list[dict[str, object]] = []
    audit_rows: list[dict[str, object]] = []
    angle_manifests: list[dict[str, object]] = []
    for angle in angles:
        angle_dir = calibration_root / angle_token(angle)
        manifest_path = angle_dir / "manifest.json"
        # The production output-SCNR definition is measured from the paired
        # S+N and C+N-only fixed-support outputs.  The S-only table remains a
        # useful linearity diagnostic, but it is not allowed to define the
        # desired-SCNR inverse when CSI/P38 is data-dependent.
        processed_path = angle_dir / "processed_output_snr_calibration.csv"
        summary_path = (processed_path if processed_path.is_file()
                        else angle_dir / "output_snr_calibration_summary.csv")
        if not manifest_path.is_file() or not summary_path.is_file():
            raise SystemExit(f"角度 {angle:g}° 缺少标定产物：{angle_dir}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        summary = read_csv(summary_path)
        x = [finite(row.get("input_snr_db_control")) for row in summary]
        y = [finite(row.get("processed_output_scnr_db", row.get("measured_output_snr_db")))
             for row in summary]
        valid = [(a, b) for a, b in zip(x, y) if math.isfinite(a) and math.isfinite(b)]
        if len(valid) < 2:
            raise SystemExit(f"角度 {angle:g}° 标定有效点不足")
        slope, intercept = np.polyfit(
            np.asarray([v[0] for v in valid]), np.asarray([v[1] for v in valid]), 1).tolist()
        for row in summary:
            control = finite(row.get("input_snr_db_control"))
            measured = finite(row.get("processed_output_scnr_db", row.get("measured_output_snr_db")))
            prediction = float(slope * control + intercept)
            signal_power = finite(row.get("processed_signal_support_power_mean",
                                         row.get("s_only_power_mean")))
            p0_power = finite(row.get("p0_support_power_mean",
                                     manifest.get("p0_mean_power")))
            mapping_rows.append({
                "angle_deg": angle, "input_control_db": control,
                "measured_output_scnr_db": measured,
                "linear_prediction_db": prediction,
                "residual_db": measured - prediction,
                "P_S_out": signal_power,
                "s_only_power_mean": finite(row.get("s_only_power_mean")),
                "P0_background": p0_power,
                "slope_db_per_db": float(slope), "intercept_db": float(intercept),
                "calibration_sanity_pass": int(bool(manifest.get("calibration_sanity_pass"))),
                "mapping_source": "paired_s_plus_n_minus_c_plus_n"
                if processed_path.is_file() else "s_only_linearity_diagnostic",
            })
        source_audit = angle_dir / "scnr_transfer_audit.csv"
        if source_audit.is_file():
            audit_rows.extend({**row, "calibration_angle_dir": str(angle_dir)} for row in read_csv(source_audit))
        angle_manifests.append({
            "angle_deg": angle, "angle_dir": str(angle_dir),
            "slope_db_per_db": float(slope), "intercept_db": float(intercept),
            "calibration_sanity_pass": bool(manifest.get("calibration_sanity_pass")),
            "p0_mean_power": finite(manifest.get("p0_mean_power")),
            "calibration_summary": str(summary_path),
            "mapping_source": "paired_s_plus_n_minus_c_plus_n"
            if processed_path.is_file() else "s_only_linearity_diagnostic",
        })

    write_csv(output_dir / "calibration_mapping.csv", mapping_rows)
    write_csv(output_dir / "output_scnr_transfer_audit.csv", audit_rows)
    (output_dir / "calibration_manifest.json").write_text(json.dumps({
        "angles": angle_manifests, "controls": controls, "seed": args.seed,
        "command": command, "command_log": str(log),
        "axis_definition": "CSI output fixed-support paired E[P_S+N]-E[P_C+N] / C+N-only ensemble mean P0",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 输出 SCNR 标定完成：{output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
