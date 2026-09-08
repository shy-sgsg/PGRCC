#!/usr/bin/env python3
"""为一个方位运行独立 seed 的 GO 输出 SCNR 配对标定并合并统计。"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from scnr_eval_lib import ensure_dir, read_csv, write_csv


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]


def parse_seeds(text: str) -> list[int]:
    seeds = [int(item.strip()) for item in text.split(",") if item.strip()]
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("seeds 必须是非空且不重复的整数列表")
    return seeds


def run(argv: list[str], log: Path) -> None:
    ensure_dir(log.parent)
    with log.open("w", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(argv) + "\n\n")
        result = subprocess.run(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                text=True, check=False)
        stream.write(f"\n[exit_code]={result.returncode}\n")
    if result.returncode:
        raise RuntimeError(f"标定批次命令失败：{' '.join(argv)}；见 {log}")


def number(row: dict[str, str], field: str) -> float:
    try:
        return float(row[field])
    except (KeyError, TypeError, ValueError):
        return float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--angle-deg", type=float, required=True)
    parser.add_argument("--beam-command-angle-deg", type=float, default=None,
                        help="命令波位中心角；默认等于 --angle-deg。用于把同一标定器扩展到波束边缘真值")
    parser.add_argument("--seeds", default="20260921,20260922,20260923,20260924,20260925")
    parser.add_argument("--scnr-grid", default="-45,-40,-35,-30,-25,-20,-15",
                        help="Stage2 target_snr_db 注入档；输出横轴从配对样本实测")
    parser.add_argument("--replicas", type=int, default=4,
                        help="每个 seed/SCNR 的空间隔离目标数；默认 5 seed×4=20 个样本")
    parser.add_argument("--background-profile", choices=("compact_statistical", "target_only"),
                        default="compact_statistical",
                        help="传给紧凑场景生成器；正式标定必须使用 compact_statistical")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--pfa", type=float, default=1e-6)
    parser.add_argument("--cfar-guard", type=int, default=4)
    parser.add_argument("--cfar-background", type=int, default=16)
    parser.add_argument("--delete-raw-after-success", action="store_true",
                        help="仅在已获得明确删除授权时转交给每个三组 variant")
    parser.add_argument("--resume", action="store_true",
                        help="仅跳过已写出完整配对样本的 seed；中断或不完整 seed 必须重跑")
    args = parser.parse_args()
    if args.replicas < 1:
        raise SystemExit("replicas 必须为正")
    if not 0.0 < args.pfa < 1.0 or args.cfar_guard < 0 or args.cfar_background <= 0:
        raise SystemExit("PFA 或 GO-CFAR 窗口参数非法")
    seeds = parse_seeds(args.seeds)
    command_angle = args.angle_deg if args.beam_command_angle_deg is None else args.beam_command_angle_deg
    if abs(args.angle_deg - command_angle) > 0.5 * 1.81871 + 1.0e-9:
        raise SystemExit("真值角与命令波位中心相差超过当前标定场景允许的半波束宽")
    scnr_values = [item.strip() for item in args.scnr_grid.split(",") if item.strip()]
    expected_rows_per_seed = len(scnr_values) * args.replicas
    if not scnr_values:
        raise SystemExit("scnr-grid 不能为空")
    out = ensure_dir(args.output_dir.resolve())
    all_rows: list[dict[str, str]] = []
    for ordinal, seed in enumerate(seeds, start=1):
        seed_dir = out / f"seed_{seed}"
        completed_samples = seed_dir / "paired" / "go_paired_output_scnr_samples.csv"
        if args.resume and completed_samples.is_file():
            existing_rows = read_csv(completed_samples)
            if len(existing_rows) == expected_rows_per_seed:
                all_rows.extend(existing_rows)
                print(f"[RESUME] 跳过已完成的 seed {seed}（{len(existing_rows)} 个配对样本）")
                continue
            print(f"[RESUME] seed {seed} 样本不完整（{len(existing_rows)}/{expected_rows_per_seed}），重新运行")
        scenario_dir = seed_dir / "input"
        scenario = scenario_dir / "scenario.json"
        run([
            sys.executable, str(SCRIPT_DIR / "04_make_compact_scenario.py"),
            "--output-dir", str(scenario_dir), "--seed", str(seed), "--beam-count", "1",
            "--scan-min-deg", f"{command_angle:g}", "--scan-step-deg", "2",
            "--target-angles", f"{args.angle_deg:g}", f"--scnr-grid={args.scnr_grid}",
            "--replicas", str(args.replicas), "--slot-rotation", str(ordinal - 1), "--period-count", "1",
            "--background-profile", args.background_profile,
            "--case-id", f"go_paired_calibration_A{args.angle_deg:g}_seed_{seed}",
        ], out / "logs" / f"{ordinal:02d}_make_seed_{seed}.log")
        calibrate = [
            sys.executable, str(SCRIPT_DIR / "04_calibrate_go_output_scnr.py"),
            "--base-scenario", str(scenario), "--output-dir", str(seed_dir / "paired"),
            "--build-dir", str(args.build_dir), "--diagnostic-beams", "1",
            "--pfa", str(args.pfa), "--cfar-guard", str(args.cfar_guard),
            "--cfar-background", str(args.cfar_background),
            "--replicas-per-scnr", str(args.replicas),
        ]
        if args.delete_raw_after_success:
            calibrate.append("--delete-raw-after-success")
        run(calibrate, out / "logs" / f"{ordinal:02d}_run_seed_{seed}.log")
        rows = read_csv(seed_dir / "paired" / "go_paired_output_scnr_samples.csv")
        if not rows:
            raise RuntimeError(f"seed {seed} 没有配对样本")
        all_rows.extend(rows)
    write_csv(out / "go_paired_output_scnr_samples.csv", all_rows)
    grouped: dict[tuple[float, float], list[dict[str, str]]] = defaultdict(list)
    for row in all_rows:
        # 与单 seed 汇总一致：0° 的浮点舍入噪声不能拆分同一个实验角度。
        grouped[(round(number(row, "truth_angle_deg"), 6),
                 number(row, "configured_target_snr_db"))].append(row)
    summary: list[dict[str, object]] = []
    metric_fields = (
        "output_scnr_det_out_db", "output_scnr_phase_ch1_db", "output_scnr_phase_ch2_db",
        "output_scnr_phase_eff_db", "phase_sigma_ideal_rad", "contextual_incremental_output_scnr_db",
        "effective_output_scnr_det_out_db",
        "s_only_to_contextual_incremental_csi_power_ratio",
    )
    for (angle, injection), rows in sorted(grouped.items()):
        record: dict[str, object] = {
            "truth_angle_deg": angle, "configured_target_snr_db": injection,
            "sample_count": len(rows), "independent_seed_count": len({row.get("calibration_seed", "") for row in rows}),
            "scnr_definition": "see paired samples: S-only and paired nonlinear effective output-SCNR are both normalized by the exact C+N GO directional-max training reference",
        }
        for field in metric_fields:
            values = [number(row, field) for row in rows]
            values = [value for value in values if math.isfinite(value)]
            record[field + "_median"] = float(np.median(values)) if values else float("nan")
            record[field + "_mean"] = float(np.mean(values)) if values else float("nan")
            record[field + "_p05"] = float(np.quantile(values, 0.05)) if values else float("nan")
            record[field + "_p95"] = float(np.quantile(values, 0.95)) if values else float("nan")
        summary.append(record)
    write_csv(out / "go_paired_output_scnr_summary.csv", summary)
    (out / "calibration_batch_provenance.json").write_text(json.dumps({
        "angle_deg": args.angle_deg, "beam_command_angle_deg": command_angle,
        "seeds": seeds, "replicas_per_seed_scnr": args.replicas,
        "background_profile": args.background_profile,
        "cfar": {"type": "GO", "pfa": args.pfa, "guard_half_width": args.cfar_guard,
                 "background_thickness": args.cfar_background},
        "sample_count_per_scnr": len(seeds) * args.replicas,
        "calibration_evaluation_seed_separation": "calibration seed 列表必须与正式 evaluation seed 列表不重叠",
        "raw_removed": bool(args.delete_raw_after_success),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 已合并 angle={args.angle_deg:g}° 的 {len(all_rows)} 个 GO 配对标定样本：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
