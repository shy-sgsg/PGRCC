#!/usr/bin/env python3
"""运行一轮真实 S+C+N 的中心/右侧波束边缘 GO-CFAR 对照 case。

每个 case 只有一个 seed、连续三个处理周期和完整注入 SNR 网格。场景仍使用
``compact_statistical`` 的面积杂波+热噪声，算法调用生产 ``GMTI_pipe_core``
及其 TrackManager；仅在 GO/TrackManager 审计和逐周期诊断 CSV 写入成功后删除
三周期 raw BIN，保留可复核的场景、XML、日志、检测快照、GO 诊断和航迹审计。
"""

from __future__ import annotations

import argparse
import json
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
        raise RuntimeError(f"命令失败（退出码 {result.returncode}）：{' '.join(command)}；见 {log}")


def parse_float_grid(text: str) -> list[float]:
    values = [float(token.strip()) for token in text.split(",") if token.strip()]
    if not values or len({round(value, 8) for value in values}) != len(values):
        raise ValueError("数值网格不能为空且不能重复")
    return values


def case_name(angle: float, label: str) -> str:
    encoded = f"{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")
    return f"command_{encoded}deg_{label}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path,
                        default=Path("outputs/gmti_scnr_eval/formal/spluscn_edge_v1_seed20261017"))
    parser.add_argument("--seed", type=int, default=20261017)
    parser.add_argument("--angles", default="0,30,45")
    parser.add_argument("--edge-offset-deg", type=float, default=0.8,
                        help="相对命令波位的右侧边缘偏移；1.81871° 波束宽度下 0.8° 仍在 hard_gate 内")
    parser.add_argument("--scnr-grid", default="-45,-40,-35,-30,-25,-20,-15")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--pfa", type=float, default=1e-6)
    parser.add_argument("--min-points", type=int, default=6)
    parser.add_argument("--small-min-points", type=int, default=3)
    parser.add_argument("--small-peak-db", type=float, default=20.0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    angles = parse_float_grid(args.angles)
    scnr_grid = parse_float_grid(args.scnr_grid)
    if not 0.0 < args.pfa < 1.0 or args.edge_offset_deg <= 0.0:
        raise SystemExit("Pfa 和 edge-offset-deg 参数非法")
    if not 1 <= args.small_min_points <= args.min_points:
        raise SystemExit("small-min-points 必须在 [1,min-points] 内")
    if len(scnr_grid) * 1 >= 4096 // 80:
        raise SystemExit("注入档过多，不能满足目标之间至少 80 range-cell 的 GO 训练窗隔离")

    output_root = ensure_dir(args.output_root.resolve())
    logs = ensure_dir(output_root / "batch_logs")
    case_manifest: list[dict[str, object]] = []
    cases = [(angle, "center", 0.0) for angle in angles]
    cases.extend((angle, "right_edge", args.edge_offset_deg) for angle in angles)
    for index, (command_angle, label, offset) in enumerate(cases, start=1):
        case_dir = output_root / case_name(command_angle, label)
        scenario = case_dir / "scenario.json"
        metrics = case_dir / "metrics" / "target_diagnostics_all_periods.csv"
        track_metrics = case_dir / "metrics" / "track_sequence_audit.csv"
        if args.resume and metrics.is_file() and track_metrics.is_file():
            print(f"[RESUME] {case_dir}")
            case_manifest.append({"case_dir": str(case_dir), "status": "reused"})
            continue
        if case_dir.exists() and any(case_dir.iterdir()):
            raise RuntimeError(f"拒绝覆盖已有非空 case：{case_dir}；请换 output-root 或使用 --resume")
        ensure_dir(case_dir)
        angle_text = f"{command_angle:g}"
        truth_angle = command_angle + offset
        run([
            sys.executable, str(SCRIPT_DIR / "04_make_compact_scenario.py"),
            "--output-dir", str(case_dir), "--seed", str(args.seed), "--beam-count", "1",
            "--scan-min-deg", angle_text, "--scan-step-deg", "2",
            "--target-angles", f"{truth_angle:g}", f"--scnr-grid={args.scnr_grid}",
            "--replicas", "1", "--period-count", "3", "--background-profile", "compact_statistical",
            "--case-id", f"real_spluscn_{label}_seed_{args.seed}_cmd_{angle_text}",
        ], logs / f"{index:02d}_{label}_{angle_text}_make_scenario.log")
        run([
            sys.executable, str(SCRIPT_DIR / "06_run_go_track_case.py"),
            "--scenario", str(scenario), "--diagnostic-beams", "1", "--pfa", str(args.pfa),
            "--min-points", str(args.min_points),
            "--csi-split-small-cluster-min-points", str(args.small_min_points),
            "--csi-split-small-cluster-peak-over-median-db", str(args.small_peak_db),
            "--delete-raw-after-success",
        ], logs / f"{index:02d}_{label}_{angle_text}_run_track.log")
        run([
            sys.executable, str(SCRIPT_DIR / "07_extract_go_track_metrics.py"),
            "--stage2-dir", str(case_dir / "stage2"), "--output-dir", str(case_dir / "metrics"),
            "--diagnostic-beams", "1", "--cleanup-cfar-binaries",
        ], logs / f"{index:02d}_{label}_{angle_text}_extract_metrics.log")
        if not metrics.is_file() or not track_metrics.is_file():
            raise RuntimeError(f"{case_dir} 缺少 GO/TrackManager 审计 CSV，拒绝标记成功")
        manifest = json.loads((case_dir / "stage2" / "go_track_run_manifest.json").read_text(encoding="utf-8"))
        if not manifest.get("raw_removed"):
            raise RuntimeError(f"{case_dir} raw 未在审计后删除，运行清单状态不一致")
        case_manifest.append({
            "case_dir": str(case_dir), "command_angle_deg": command_angle,
            "truth_angle_deg": truth_angle, "case_label": label, "status": "completed",
            "metrics": str(metrics), "track_metrics": str(track_metrics),
        })
        print(f"[PASS] {label} command={command_angle:g}° truth={truth_angle:g}°")
    (output_root / "run_manifest.json").write_text(json.dumps({
        "description": "真实 S+C+N GO-CFAR/TrackManager 中心与右侧边缘单 seed 批次",
        "seed": args.seed, "pfa": args.pfa, "min_points": args.min_points,
        "small_min_points": args.small_min_points, "small_peak_over_median_db": args.small_peak_db,
        "edge_offset_deg": args.edge_offset_deg, "scnr_grid_db": scnr_grid,
        "background_profile": "compact_statistical", "period_count": 3,
        "cases": case_manifest,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 完成 {len(case_manifest)} 个真实 S+C+N case：{output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
