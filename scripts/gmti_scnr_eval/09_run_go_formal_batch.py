#!/usr/bin/env python3
"""执行选定命令波位的低空间 GO-CFAR 三帧统计批次。

每个方位独立生成一个命令波位的三周期回波，并经生产 GO-CFAR 和
TrackManager 处理。这是选定波位子链路，不把结果宣称为全 61 波位扫描统计。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from scnr_eval_lib import ensure_dir


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]


def run(command: list[str], log: Path) -> None:
    ensure_dir(log.parent)
    with log.open("w", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(command) + "\n\n")
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                text=True, check=False)
        stream.write(f"\n[exit_code]={result.returncode}\n")
    if result.returncode:
        raise RuntimeError(f"批次命令失败：{' '.join(command)}；见 {log}")


def parse_seeds(text: str) -> list[int]:
    values = [int(token.strip()) for token in text.split(",") if token.strip()]
    if not values or len(set(values)) != len(values):
        raise ValueError("seeds 必须为不重复的整数列表")
    return values


def parse_angles(text: str) -> list[float]:
    values = [float(token.strip()) for token in text.split(",") if token.strip()]
    if not values or len({round(value, 8) for value in values}) != len(values):
        raise ValueError("target-angles 必须为不重复的数值列表")
    return values


def angle_directory_name(angle: float) -> str:
    """无歧义、跨平台的目录名，同时避免浮点文本直接进入路径。"""
    return "angle_" + f"{angle:.3f}".replace("-", "m").replace(".", "p") + "deg"


def prune_case_intermediates_after_audit(case_dir: Path) -> None:
    """仅在 TrackManager 与 GO metrics 均成功后清理可再生的大型中间物。"""
    stage2 = case_dir / "stage2"
    metrics = case_dir / "metrics"
    manifest_path = stage2 / "go_track_run_manifest.json"
    required_metrics = (metrics / "target_diagnostics_all_periods.csv",
                        metrics / "track_sequence_audit.csv")
    if not manifest_path.is_file() or any(not path.is_file() for path in required_metrics):
        raise RuntimeError(f"{case_dir} 尚无完整 TrackManager/GO 审计，拒绝删除 raw")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("audit_intermediates_pruned"):
        return
    start = int(manifest["period_start"])
    count = int(manifest["period_count"])
    raw_files = [stage2 / "data" / f"stage2_statistical_newprotocol_period_{period:04d}.bin"
                 for period in range(start, start + count)]
    missing = [str(path) for path in raw_files if not path.is_file()]
    if missing:
        raise RuntimeError("审计后 raw 清理前文件缺失，拒绝写入不一致 manifest：" + ", ".join(missing))
    removed: list[dict[str, object]] = []
    for path in raw_files:
        removed.append({"path": str(path), "bytes": path.stat().st_size, "kind": "raw_bin"})
        path.unlink()
    # 逐脉冲真值已被 07 提炼为逐目标/逐航迹审计 CSV；保留 truth_targets_by_beam.csv
    # 供复核，回收两个体积随脉冲数线性增长的重复中间表。
    for path in (stage2 / "truth" / "truth_pulse.csv", stage2 / "truth" / "moving_target_truth.csv"):
        if path.is_file():
            removed.append({"path": str(path), "bytes": path.stat().st_size, "kind": "per_pulse_truth"})
            path.unlink()
    manifest["raw_removed"] = True
    manifest["raw_cleanup_stage"] = "09_after_track_and_go_metrics_audit"
    manifest["audit_intermediates_pruned"] = True
    manifest["pruned_intermediates"] = removed
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def case_audit_complete(case_dir: Path, expect_raw_removed: bool) -> bool:
    """返回可安全跳过的完整波位 case；不把半成品误当作可恢复结果。"""
    stage2 = case_dir / "stage2"
    metrics = case_dir / "metrics"
    manifest_path = stage2 / "go_track_run_manifest.json"
    required = (metrics / "target_diagnostics_all_periods.csv",
                metrics / "track_sequence_audit.csv")
    if not manifest_path.is_file() or any(not path.is_file() for path in required):
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return (not expect_raw_removed) or (
        bool(manifest.get("raw_removed")) and bool(manifest.get("audit_intermediates_pruned")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path,
                        default=Path("outputs/gmti_scnr_eval/formal_go_batch"))
    parser.add_argument("--seeds", default=(
        "20260831,20260832,20260833,20260834,20260835,20260836,20260837,20260838,"
        "20260839,20260840,20260841,20260842,20260843,20260844,20260845,20260846,"
        "20260847,20260848,20260849,20260850"))
    parser.add_argument("--scnr-grid", default="-45,-40,-35,-30,-25,-20,-15",
                        help="Stage2 target_snr_db 注入档；正式曲线横轴使用配对实测的输出 SCNR")
    parser.add_argument("--target-angles", default="0,30,45",
                        help="选定的独立命令波位（度）；每个方位各生成一个实际中心波位")
    parser.add_argument("--background-profile", choices=("compact_statistical",),
                        default="compact_statistical",
                        help="正式批次固定采用紧凑统计杂波，禁止以 target_only 得出结论")
    parser.add_argument("--replicas", type=int, default=4,
                        help="10 档时每波位 40 个相隔 80 cell 的目标，仍满足 GO 训练窗隔离")
    parser.add_argument("--pfa", type=float, default=1e-6,
                        help="生产 GO-CFAR 的 pf")
    parser.add_argument("--min-points", type=int, default=6,
                        help="常规连通域最小单元数")
    parser.add_argument("--csi-split-small-cluster-min-points", type=int, default=3,
                        help="split 小簇恢复的最小单元数；须不大于 --min-points")
    parser.add_argument("--csi-split-small-cluster-peak-over-median-db", type=float, default=20.0,
                        help="仅恢复小簇时相对局部中位数的峰值门限（dB）")
    parser.add_argument("--paired-output-scnr-summary", type=Path, action="append", default=[],
                        help="每个正式命令波位的 04 配对输出 SCNR 标定 CSV")
    parser.add_argument("--minimum-independent-seeds", type=int, default=20,
                        help="每个 SCNR 的最小独立噪声/杂波 seed 数")
    parser.add_argument("--minimum-target-frame-trials-per-angle-scnr", type=int, default=200,
                        help="每个角度、SCNR 的最小目标帧试验数；三周期各算一个实例")
    parser.add_argument("--minimum-paired-calibration-samples", type=int, default=20,
                        help="每个正式 angle/SCNR 横轴所需的最小独立配对标定样本数")
    parser.add_argument("--delete-raw-after-success", action="store_true",
                        help="显式删除每个成功 seed 的约 9 GiB 三周期 raw；正式低空间运行需要此开关")
    parser.add_argument("--prune-audited-only", action="store_true",
                        help="不运行/汇总实验；仅对已有完整 GO/TrackManager 审计的 case 执行安全中间物清理")
    parser.add_argument("--resume", action="store_true",
                        help="仅跳过已通过 GO/TrackManager 审计且 raw 状态一致的 seed")
    parser.add_argument("--minimum-free-gib", type=float, default=2.0)
    args = parser.parse_args()
    if args.replicas < 1:
        raise SystemExit("replicas 必须为正")
    if not 0.0 < args.pfa < 1.0:
        raise SystemExit("pfa 必须在 (0,1) 内")
    if not 1 <= args.csi_split_small_cluster_min_points <= args.min_points:
        raise SystemExit("小簇最小单元数必须在 [1, min-points] 内")
    if args.csi_split_small_cluster_peak_over_median_db < 0.0:
        raise SystemExit("csi split 小簇峰值门限不能为负")
    if args.minimum_paired_calibration_samples < 1:
        raise SystemExit("minimum-paired-calibration-samples 必须为正")
    seeds = parse_seeds(args.seeds)
    angles = parse_angles(args.target_angles)
    if args.prune_audited_only:
        if not args.delete_raw_after_success:
            raise SystemExit("--prune-audited-only 必须同时给出 --delete-raw-after-success")
        root = args.output_root.resolve()
        if not root.is_dir():
            raise SystemExit(f"正式批次目录不存在：{root}")
        for seed in seeds:
            for angle in angles:
                case_dir = root / f"seed_{seed}" / angle_directory_name(angle)
                prune_case_intermediates_after_audit(case_dir)
                if not case_audit_complete(case_dir, expect_raw_removed=True):
                    raise RuntimeError(f"{case_dir} 清理后审计状态不完整")
        print(f"[PASS] 已清理 {len(seeds) * len(angles)} 个已审计 GO/TrackManager case 的 raw 中间物")
        return 0
    if len(seeds) < args.minimum_independent_seeds:
        raise SystemExit(
            f"只有 {len(seeds)} 个独立 seed，小于要求的 {args.minimum_independent_seeds}；"
            "请显式降低门槛并在报告中说明，或补足 seed")
    target_frame_trials = len(seeds) * args.replicas * 3
    if target_frame_trials < args.minimum_target_frame_trials_per_angle_scnr:
        raise SystemExit(
            f"每个角度/SCNR 只有 {target_frame_trials} 个目标帧试验，小于要求的 "
            f"{args.minimum_target_frame_trials_per_angle_scnr}；请增加 seed 或 replicas")
    if not args.paired_output_scnr_summary:
        raise SystemExit(
            "正式 GO-Pd 汇总必须提供 --paired-output-scnr-summary；"
            f"先对 {args.target_angles}°分别运行 04_calibrate_output_scnr.sh，避免用检测条件 SCNR 替代横轴")
    root = ensure_dir(args.output_root.resolve())
    logs = ensure_dir(root / "batch_logs")
    for ordinal, seed in enumerate(seeds, start=1):
        seed_dir = root / f"seed_{seed}"
        for angle in angles:
            free_gib = shutil.disk_usage(root).free / (1024 ** 3)
            if free_gib < args.minimum_free_gib:
                raise RuntimeError(
                    f"磁盘可用 {free_gib:.1f} GiB，小于选定波位三周期所需的安全下限 "
                    f"{args.minimum_free_gib:.1f} GiB；停止而不写入不完整结果")
            case_dir = seed_dir / angle_directory_name(angle)
            if args.resume:
                manifest_path = case_dir / "stage2" / "go_track_run_manifest.json"
                metrics_dir = case_dir / "metrics"
                if manifest_path.is_file() and (metrics_dir / "target_diagnostics_all_periods.csv").is_file() and \
                        (metrics_dir / "track_sequence_audit.csv").is_file() and args.delete_raw_after_success:
                    prune_case_intermediates_after_audit(case_dir)
            if args.resume and case_audit_complete(case_dir, args.delete_raw_after_success):
                print(f"[RESUME] 跳过已完成且审计一致的 seed {seed} / {angle:g}°")
                continue
            if args.resume and case_dir.exists() and any(case_dir.iterdir()):
                raise RuntimeError(
                    f"{case_dir} 是未完成的波位 case，拒绝覆盖；请先检查其 batch_logs 与 stage2 输出后再恢复")
            scenario_dir = case_dir / "stage2"
            scenario = case_dir / "scenario.json"
            run([
                sys.executable, str(SCRIPT_DIR / "04_make_compact_scenario.py"),
                "--output-dir", str(case_dir), "--seed", str(seed), "--beam-count", "1",
                "--scan-min-deg", f"{angle:g}", "--scan-step-deg", "2", "--target-angles", f"{angle:g}",
                f"--scnr-grid={args.scnr_grid}", "--replicas", str(args.replicas),
                "--slot-rotation", str(ordinal - 1), "--period-count", "3",
                "--background-profile", args.background_profile,
                "--case-id", f"selected_beam_go_scnr_seed_{seed}_A{angle:g}",
            ], logs / f"{ordinal:02d}_seed_{seed}_angle_{angle:g}_make_scenario.log")
            run([
                sys.executable, str(SCRIPT_DIR / "06_run_go_track_case.py"),
                "--scenario", str(scenario), "--diagnostic-beams", "1",
                "--pfa", str(args.pfa), "--min-points", str(args.min_points),
                "--csi-split-small-cluster-min-points", str(args.csi_split_small_cluster_min_points),
                "--csi-split-small-cluster-peak-over-median-db",
                str(args.csi_split_small_cluster_peak_over_median_db),
            ], logs / f"{ordinal:02d}_seed_{seed}_angle_{angle:g}_run_track.log")
            metric_command = [
                sys.executable, str(SCRIPT_DIR / "07_extract_go_track_metrics.py"),
                "--stage2-dir", str(scenario_dir), "--output-dir", str(case_dir / "metrics"),
                "--diagnostic-beams", "1",
            ]
            for path in args.paired_output_scnr_summary:
                metric_command.extend(["--paired-output-scnr-summary", str(path.resolve())])
            if args.delete_raw_after_success:
                metric_command.append("--cleanup-cfar-binaries")
            run(metric_command, logs / f"{ordinal:02d}_seed_{seed}_angle_{angle:g}_extract_metrics.log")
            if args.delete_raw_after_success:
                prune_case_intermediates_after_audit(case_dir)
    summary_command = [
        sys.executable, str(SCRIPT_DIR / "08_summarize_go_pd.py"),
        "--batch-root", str(root), "--output-dir", str(root / "summary"),
        "--require-paired-output-scnr",
        "--minimum-paired-calibration-samples", str(args.minimum_paired_calibration_samples),
    ]
    for path in args.paired_output_scnr_summary:
        summary_command.extend(["--paired-output-scnr-summary", str(path.resolve())])
    run(summary_command, logs / "99_summarize.log")
    print(f"[PASS] {len(seeds)} 个正式 GO-CFAR seed 已完成：{root / 'summary'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
