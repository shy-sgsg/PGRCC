#!/usr/bin/env python3
"""执行 GO-CFAR 输出 SCNR 专项的可复现实验序列。

正式 evaluation 要求 --delete-raw-after-success：每个 seed 只有在 GO 与
TrackManager 审计成功后才释放三周期 raw BIN。单周期标定不具备该审计，不传递删除开关。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
EVALUATION_ANGLES = (0, 30, 45)


def seeds(start: int, count: int) -> str:
    return ",".join(str(start + index) for index in range(count))


def parse_grid(text: str) -> list[float]:
    values = [float(item.strip()) for item in text.split(",") if item.strip()]
    if not values:
        raise ValueError("scnr-grid 不能为空")
    return values


def execute(argv: list[str], dry_run: bool) -> None:
    print("$ " + " ".join(argv), flush=True)
    if dry_run:
        return
    result = subprocess.run(argv, cwd=ROOT, check=False)
    if result.returncode:
        raise RuntimeError(f"命令失败（exit={result.returncode}）：{' '.join(argv)}")


def output_ready(path: Path, resume: bool) -> bool:
    return resume and path.is_file() and path.stat().st_size > 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/gmti_scnr_eval/final_go"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--seed-count", type=int, default=1,
                        help="正式 GO 评估独立 seed 数；1 用于预备报告，建议随后扩充到 3 或 5")
    parser.add_argument("--scnr-grid", default="-45,-40,-35,-30,-25,-20,-15",
                        help="Stage2 target_snr_db 粗扫注入档；报告横轴为配对实测输出 SCNR")
    parser.add_argument("--refine-scnr-grid", default="-40,-39.5,-39,-38.5,-38,-37.5,-37,-36.5,-36,-35.5,-35,-34.5,-34",
                        help="Stage2 target_snr_db 加密注入档；应根据粗扫输出 SCNR 覆盖情况调整")
    parser.add_argument("--calibration-seed-count", type=int, default=5)
    parser.add_argument("--skip-refinement", action="store_true")
    parser.add_argument("--delete-raw-after-success", action="store_true",
                        help="显式授权逐 seed 在审计成功后删除 raw BIN；正式运行必需")
    args = parser.parse_args()
    if args.seed_count < 1:
        raise SystemExit("seed-count 必须为正")
    if args.calibration_seed_count < 5:
        raise SystemExit("粗扫标定至少需要 5 个独立 calibration seeds")
    coarse_grid = parse_grid(args.scnr_grid)
    refine_grid = parse_grid(args.refine_scnr_grid)
    if not args.dry_run and not args.delete_raw_after_success:
        raise SystemExit("正式 run_all 必须明确给出 --delete-raw-after-success，防止 raw BIN 占满磁盘")
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    py = sys.executable

    simple_steps = [
        (out / "baseline" / "parameter_freeze.csv",
         [py, str(SCRIPT_DIR / "00_audit_current_chain.py"), "--output-dir", str(out / "baseline")]),
        (out / "textbook_cell" / "textbook_fixed_threshold_theory.csv",
         [py, str(SCRIPT_DIR / "01_textbook_fixed_pd.py"), "--output-dir", str(out / "textbook_cell")]),
        (out / "go_cfar_cell" / "go_cfar_cell_theory.csv",
         [py, str(SCRIPT_DIR / "02_go_cfar_cell_pd.py"), "--output-dir", str(out / "go_cfar_cell")]),
        (out / "angle_theory" / "angle_theory_summary.csv",
         [py, str(SCRIPT_DIR / "03_angle_theory.py"), "--output-dir", str(out / "angle_theory")]),
    ]
    for marker, argv in simple_steps:
        if not output_ready(marker, args.resume):
            execute(argv, args.dry_run)

    calibration_paths: list[Path] = []
    for index, angle in enumerate(EVALUATION_ANGLES):
        directory = out / "calibration_coarse_output_scnr_v3" / f"angle_{angle:02d}deg"
        marker = directory / "go_paired_output_scnr_summary.csv"
        calibration_paths.append(marker)
        if output_ready(marker, args.resume):
            continue
        argv = [py, str(SCRIPT_DIR / "04_run_go_calibration_batch.py"),
                "--output-dir", str(directory), "--angle-deg", str(angle),
                "--seeds", seeds(20260921 + 100 * index, args.calibration_seed_count),
                f"--scnr-grid={args.scnr_grid}", "--replicas", "4"]
        if args.resume:
            argv.append("--resume")
        execute(argv, args.dry_run)

    # T3-E：guard 不同会改变实际脉压/Doppler 主旁瓣进入训练窗的方式，不能以 IID
    # 几何曲线代替。固定 truth 单元审计完成后可回收其 raw。
    guard_marker = out / "go_cfar_cell" / "go_cfar_guard_production_sweep.csv"
    if not output_ready(guard_marker, args.resume):
        argv = [py, str(SCRIPT_DIR / "05_run_go_guard_sweep.py"),
                "--output-dir", str(out / "go_cfar_cell"),
                "--paired-output-scnr-summary", str(calibration_paths[0]),
                "--seeds", seeds(20264001, args.seed_count), "--replicas", "4",
                "--delete-raw-after-success"]
        execute(argv, args.dry_run)

    # 选定波位子链路与旧的全 61 波位现场隔离，汇总时绝不混入后者。
    formal_root = out / "formal" / "selected_beams_v1"
    coarse_root = formal_root / "coarse"
    coarse_marker = coarse_root / "summary" / "go_target_and_track_pd_summary.csv"
    if not output_ready(coarse_marker, args.resume):
        argv = [py, str(SCRIPT_DIR / "09_run_go_formal_batch.py"),
                "--output-root", str(coarse_root), "--seeds", seeds(20261001, args.seed_count),
                f"--scnr-grid={args.scnr_grid}", "--replicas", "4",
                "--target-angles", ",".join(map(str, EVALUATION_ANGLES)),
                "--minimum-independent-seeds", str(args.seed_count),
                "--minimum-target-frame-trials-per-angle-scnr", str(args.seed_count * 12)]
        if args.resume:
            argv.append("--resume")
        for path in calibration_paths:
            argv.extend(["--paired-output-scnr-summary", str(path)])
        if args.delete_raw_after_success:
            argv.append("--delete-raw-after-success")
        execute(argv, args.dry_run)

    refine_calibrations: list[Path] = []
    if not args.skip_refinement:
        # 13 个 0.5 dB 档位以 3 个隔离目标/seed 承载；7 seed×3×3 帧=189，
        # 因此按 23 seed 运行以满足每档至少 200 个目标帧试验。
        refine_calibration_seeds = max(7, args.calibration_seed_count)
        for index, angle in enumerate(EVALUATION_ANGLES):
            directory = out / "calibration_refine" / f"angle_{angle:02d}deg"
            marker = directory / "go_paired_output_scnr_summary.csv"
            refine_calibrations.append(marker)
            if output_ready(marker, args.resume):
                continue
            argv = [py, str(SCRIPT_DIR / "04_run_go_calibration_batch.py"),
                    "--output-dir", str(directory), "--angle-deg", str(angle),
                    "--seeds", seeds(20262001 + 100 * index, refine_calibration_seeds),
                    f"--scnr-grid={args.refine_scnr_grid}", "--replicas", "3"]
            if args.resume:
                argv.append("--resume")
            if args.delete_raw_after_success:
                argv.append("--delete-raw-after-success")
            execute(argv, args.dry_run)
        refine_root = formal_root / "refine"
        refine_marker = refine_root / "summary" / "go_target_and_track_pd_summary.csv"
        if not output_ready(refine_marker, args.resume):
            argv = [py, str(SCRIPT_DIR / "09_run_go_formal_batch.py"),
                    "--output-root", str(refine_root), "--seeds", seeds(20263001, 23),
                    f"--scnr-grid={args.refine_scnr_grid}", "--replicas", "3"]
            if args.resume:
                argv.append("--resume")
            for path in refine_calibrations:
                argv.extend(["--paired-output-scnr-summary", str(path)])
            if args.delete_raw_after_success:
                argv.append("--delete-raw-after-success")
            execute(argv, args.dry_run)

    # 同一注入 SCNR 可能同时位于粗扫和 0.5 dB refine 网格。先用原始配对样本
    # 合并，不能把两个 summary 的中位数当作独立或让后者覆盖前者。
    merged_calibrations: list[Path] = []
    for index, angle in enumerate(EVALUATION_ANGLES):
        merged = out / "calibration_final" / f"angle_{angle:02d}deg"
        merged_marker = merged / "go_paired_output_scnr_summary.csv"
        merged_calibrations.append(merged_marker)
        if output_ready(merged_marker, args.resume):
            continue
        inputs = [out / "calibration_coarse_output_scnr_v3" / f"angle_{angle:02d}deg"]
        if not args.skip_refinement:
            inputs.append(out / "calibration_refine" / f"angle_{angle:02d}deg")
        argv = [py, str(SCRIPT_DIR / "04_merge_go_calibration_summaries.py"),
                "--output-dir", str(merged)]
        for path in inputs:
            argv.extend(["--input-dir", str(path)])
        execute(argv, args.dry_run)

    # 在 coarse/refine 的共同父目录汇总，保留各 batch 的输入身份和 seed 分层。
    aggregate_dir = formal_root / "summary"
    aggregate_marker = aggregate_dir / "go_target_and_track_pd_summary.csv"
    if not output_ready(aggregate_marker, args.resume):
        argv = [py, str(SCRIPT_DIR / "08_summarize_go_pd.py"), "--batch-root", str(formal_root),
                "--output-dir", str(aggregate_dir), "--require-paired-output-scnr",
                "--minimum-paired-calibration-samples", "20"]
        for path in merged_calibrations:
            argv.extend(["--paired-output-scnr-summary", str(path)])
        execute(argv, args.dry_run)

    # T3-F：正式 CSI/GO 命中图上的指定 truth 单元统计。它只在 07 已写出逐周期
    # 诊断后运行，不能以 IID 条件 MC 或最终 target output 偷换这一层指标。
    real_cell_marker = out / "go_cfar_cell" / "go_cfar_real_background_sweep.csv"
    if not output_ready(real_cell_marker, args.resume):
        execute([py, str(SCRIPT_DIR / "05_summarize_go_real_background.py"),
                 "--batch-root", str(formal_root), "--output-dir", str(out / "go_cfar_cell"),
                 "--minimum-trials", str(args.seed_count * 12)], args.dry_run)

    joint_dir = out / "joint_threshold"
    joint_marker = joint_dir / "go_joint_scnr_thresholds.csv"
    if not output_ready(joint_marker, args.resume):
        execute([py, str(SCRIPT_DIR / "08_build_go_joint_threshold.py"),
                 "--pd-summary", str(aggregate_dir / "go_target_and_track_pd_summary.csv"),
                 "--angle-summary", str(aggregate_dir / "go_angle_error_summary.csv"),
                 "--output-dir", str(joint_dir), "--evaluation-angles",
                 ",".join(map(str, EVALUATION_ANGLES))], args.dry_run)
    report_marker = ROOT / "docs" / "GMTI_SCNR_测角精度与检测概率联合分析报告.md"
    if not output_ready(report_marker, args.resume):
        argv = [py, str(SCRIPT_DIR / "09_generate_report.py"), "--experiment-root", str(out),
                "--formal-summary-dir", str(aggregate_dir), "--joint-dir", str(joint_dir)]
        for path in merged_calibrations:
            argv.extend(["--calibration-summary", str(path)])
        execute(argv, args.dry_run)
    print(f"[PASS] GO-CFAR 专项流程完成：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
