#!/usr/bin/env python3
"""用生产脉压/CSI/GO-CFAR 评估 guard 对指定 truth 单元 Pd 的影响。

每个 seed 在一个波位的紧凑统计杂波场景中承载多个相隔 80 range cell 的目标；因此
实际目标旁瓣、Doppler 形状和 GO 训练窗都由生产链形成，同时不会让相邻试验互相进入
训练窗。此脚本只取 GO hit 图的固定 truth 单元，不用 cluster/target_select 代替单元 Pd。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti_scnr_eval_matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from scnr_eval_lib import ensure_dir, parse_grid, read_csv, wilson_interval, write_csv


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]


def parse_ints(text: str) -> list[int]:
    values = [int(token.strip()) for token in text.split(",") if token.strip()]
    if not values or len(values) != len(set(values)):
        raise ValueError("列表必须非空且元素不重复")
    return values


def number(row: dict[str, str], key: str) -> float:
    try:
        return float(row.get(key, ""))
    except (TypeError, ValueError):
        return float("nan")


def integer(row: dict[str, str], key: str) -> int:
    value = number(row, key)
    return int(round(value)) if math.isfinite(value) else 0


def run(argv: list[str], log: Path) -> None:
    ensure_dir(log.parent)
    with log.open("w", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(argv) + "\n\n")
        result = subprocess.run(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                text=True, check=False)
        stream.write(f"\n[exit_code]={result.returncode}\n")
    if result.returncode:
        raise RuntimeError(f"生产 guard sweep 命令失败：{' '.join(argv)}；见 {log}")


def calibration_map(path: Path) -> dict[float, float]:
    result: dict[float, float] = {}
    for row in read_csv(path):
        injection = number(row, "configured_target_snr_db")
        output = number(row, "output_scnr_det_out_db_median")
        count = integer(row, "sample_count")
        if math.isfinite(injection) and math.isfinite(output) and count >= 20:
            result[round(injection, 8)] = output
    if not result:
        raise RuntimeError(f"无有效（每档≥20）配对输出 SCNR 标定：{path}")
    return result


def remove_raw_after_unit_audit(stage2: Path) -> None:
    manifest_path = stage2 / "run_manifest.json"
    diagnostics = stage2.parent.parent / "metrics" / "target_diagnostics.csv"
    if not manifest_path.is_file() or not diagnostics.is_file():
        raise RuntimeError(f"{stage2} 未完成 GO 固定单元审计，拒绝删除 raw")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw = Path(manifest["raw_path"])
    if not raw.is_file():
        raise RuntimeError(f"GO 固定单元审计后 raw 缺失：{raw}")
    raw.unlink()
    manifest["raw_removed"] = True
    manifest["raw_cleanup_stage"] = "05_after_fixed_truth_go_unit_audit"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--paired-output-scnr-summary", type=Path, required=True,
                        help="0°正式配对标定；每个输入档至少 20 个样本")
    parser.add_argument("--seeds", default=(
        "20264001,20264002,20264003,20264004,20264005,20264006,20264007,20264008,"
        "20264009,20264010,20264011,20264012,20264013,20264014,20264015,20264016,"
        "20264017,20264018,20264019,20264020"))
    parser.add_argument("--guards", default="3,4,5")
    parser.add_argument("--background", type=int, default=16)
    parser.add_argument("--pfa", type=float, default=1e-7)
    parser.add_argument("--scnr-grid", default="-45,-40,-35,-30,-25,-20,-15",
                        help="Stage2 target_snr_db 注入档；单元级曲线横轴使用配对实测输出 SCNR")
    parser.add_argument("--replicas", type=int, default=5,
                        help="每 seed/SCNR 的空间隔离目标数；20 seed 时为 100 指定单元")
    parser.add_argument("--delete-raw-after-success", action="store_true",
                        help="在固定单元审计、CSV 写入后删除 raw 和 CFAR 图")
    args = parser.parse_args()
    seeds, guards = parse_ints(args.seeds), parse_ints(args.guards)
    if any(guard < 1 for guard in guards) or args.background < 1 or args.replicas < 1:
        raise SystemExit("guard/background/replicas 必须为正")
    calibration = calibration_map(args.paired_output_scnr_summary.resolve())
    grid = parse_grid(args.scnr_grid)
    out = ensure_dir(args.output_dir.resolve())
    all_rows: list[dict[str, str]] = []
    for guard in guards:
        for ordinal, seed in enumerate(seeds, start=1):
            seed_dir = out / f"guard_{guard:02d}" / f"seed_{seed}"
            scenario_dir = seed_dir / "input"
            scenario = scenario_dir / "scenario.json"
            run([
                sys.executable, str(SCRIPT_DIR / "04_make_compact_scenario.py"),
                "--output-dir", str(scenario_dir), "--seed", str(seed), "--beam-count", "1",
                "--scan-min-deg", "0", "--scan-step-deg", "2", "--target-angles", "0",
                f"--scnr-grid={args.scnr_grid}", "--replicas", str(args.replicas),
                "--slot-rotation", str(ordinal - 1), "--period-count", "1",
                "--background-profile", "compact_statistical",
                "--case-id", f"go_guard_{guard}_seed_{seed}",
            ], out / "logs" / f"guard_{guard:02d}_{ordinal:02d}_make.log")
            run([
                sys.executable, str(SCRIPT_DIR / "04_run_production_case.py"),
                "--scenario", str(scenario), "--diagnostic-beams", "1", "--pfa", str(args.pfa),
                "--cfar-guard", str(guard), "--cfar-background", str(args.background),
            ], out / "logs" / f"guard_{guard:02d}_{ordinal:02d}_run.log")
            extract = [
                sys.executable, str(SCRIPT_DIR / "05_extract_go_diagnostics.py"),
                "--stage2-dir", str(scenario_dir / "stage2"), "--output-dir", str(seed_dir / "metrics"),
                "--diagnostic-beams", "1",
            ]
            if args.delete_raw_after_success:
                extract.append("--cleanup-cfar-binary")
            run(extract, out / "logs" / f"guard_{guard:02d}_{ordinal:02d}_extract.log")
            if args.delete_raw_after_success:
                remove_raw_after_unit_audit(scenario_dir / "stage2")
            for row in read_csv(seed_dir / "metrics" / "target_diagnostics.csv"):
                row = dict(row)
                row["guard_half_width_cells"] = str(guard)
                row["guard_sweep_seed"] = str(seed)
                all_rows.append(row)
    summary: list[dict[str, object]] = []
    grouped: dict[tuple[int, float], list[dict[str, str]]] = defaultdict(list)
    for row in all_rows:
        if integer(row, "unit_cell_window_valid") != 1:
            continue
        hit, injection = number(row, "unit_go_cfar_hit"), number(row, "configured_target_snr_db")
        if math.isfinite(hit) and math.isfinite(injection):
            grouped[(integer(row, "guard_half_width_cells"), injection)].append(row)
    for (guard, injection), rows in sorted(grouped.items()):
        hits, count = sum(integer(row, "unit_go_cfar_hit") for row in rows), len(rows)
        low, high = wilson_interval(hits, count)
        summary.append({
            "cfar_type": "GO", "guard_half_width_cells": guard,
            "background_thickness_cells": args.background, "configured_pfa": args.pfa,
            "configured_target_snr_db": injection,
            "paired_calibrated_output_scnr_db": calibration.get(round(injection, 8), float("nan")),
            "trial_count": count, "independent_seed_count": len({row.get("guard_sweep_seed", "") for row in rows}),
            "unit_go_cfar_hits": hits, "unit_go_cfar_pd": hits / count,
            "unit_go_cfar_pd_wilson95_low": low, "unit_go_cfar_pd_wilson95_high": high,
            "method": "production pulse compression/CSI/GO hit map at fixed Stage2 truth row_truth/range_bin",
            "background_profile": "compact_statistical",
        })
    expected = len(seeds) * args.replicas
    missing = []
    for guard in guards:
        for injection in grid:
            rows = grouped.get((guard, injection), [])
            if len(rows) != expected:
                missing.append(f"g={guard},S={injection:g},n={len(rows)}")
    if missing:
        raise RuntimeError("guard sweep 固定单元样本不完整：" + "; ".join(missing))
    write_csv(out / "go_cfar_guard_production_sweep.csv", summary)
    fig, axis = plt.subplots(figsize=(7.5, 4.7))
    for guard in guards:
        rows = [row for row in summary if int(row["guard_half_width_cells"]) == guard]
        rows.sort(key=lambda row: float(row["configured_target_snr_db"]))
        x = [float(row["paired_calibrated_output_scnr_db"])
             if math.isfinite(float(row["paired_calibrated_output_scnr_db"]))
             else float(row["configured_target_snr_db"]) for row in rows]
        y = [float(row["unit_go_cfar_pd"]) for row in rows]
        low = [float(row["unit_go_cfar_pd_wilson95_low"]) for row in rows]
        high = [float(row["unit_go_cfar_pd_wilson95_high"]) for row in rows]
        axis.errorbar(x, y, yerr=[np.maximum(0.0, np.subtract(y, low)),
                                  np.maximum(0.0, np.subtract(high, y))],
                      marker="o", capsize=3, label=f"guard={guard}")
    axis.axhline(0.9, color="tab:red", linestyle="--", label="Pd=0.90")
    axis.set(xlabel="配对标定输出 SCNR (dB)", ylabel="指定 truth 单元 GO Pd",
             ylim=(-0.02, 1.02), title="F07 生产信号形状下 GO-CFAR guard 敏感性")
    axis.grid(True, alpha=0.3); axis.legend(); fig.tight_layout()
    fig.savefig(out / "F07_go_guard_production_sweep.png", dpi=180)
    plt.close(fig)
    (out / "go_cfar_guard_production_provenance.json").write_text(json.dumps({
        "seeds": seeds, "guards": guards, "background": args.background, "pfa": args.pfa,
        "replicas_per_seed_scnr": args.replicas, "expected_trial_count_per_guard_scnr": expected,
        "paired_output_scnr_summary": str(args.paired_output_scnr_summary.resolve()),
        "raw_removed": args.delete_raw_after_success,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 已完成 {len(summary)} 组生产 GO guard 敏感性：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
