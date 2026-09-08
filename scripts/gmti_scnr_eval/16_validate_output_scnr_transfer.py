#!/usr/bin/env python3
"""检查输出 SCNR 标定是否单调、近似 1 dB/dB 且误差可接受。"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from scnr_eval_lib import ensure_dir, read_csv, write_csv

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]


def number(value: object) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    # 三屏 smoke 的 batch 均值仍有约 0.6 dB 的有限样本波动；正式批量
    # 标定会增大 trials。1 dB 是 sanity 上限而不是最终曲线误差预算。
    parser.add_argument("--max-error-db", type=float, default=1.0)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--diagnosis-output", type=Path,
                        default=ROOT / "docs/gmti_scnr_eval/output_scnr_transfer_diagnosis.md")
    args = parser.parse_args()
    root = args.input_dir.resolve()
    # 15_calibrate_output_scnr 汇总多角度时把审计表命名为
    # output_scnr_transfer_audit.csv；直接运行 16_run_standard... 做单角度
    # 模块调试时则保留 scnr_transfer_audit.csv。两者内容相同，均可作为
    # 固定支撑 output-SCNR 传递函数的证据。
    audit = root / "output_scnr_transfer_audit.csv"
    if not audit.is_file():
        audit = root / "scnr_transfer_audit.csv"
    if not audit.is_file():
        raise SystemExit(f"缺少 output_scnr_transfer_audit.csv 或 scnr_transfer_audit.csv：{root}")
    grouped: dict[float, list[dict[str, str]]] = {}
    for row in read_csv(audit):
        angle = number(row.get("angle_deg"))
        if math.isfinite(angle):
            grouped.setdefault(angle, []).append(row)
    result_rows: list[dict[str, object]] = []
    all_pass = True
    for angle, rows in sorted(grouped.items()):
        rows.sort(key=lambda row: number(row.get("target_snr_config")))
        x = np.asarray([number(row.get("target_snr_config")) for row in rows])
        y = np.asarray([number(row.get("measured_batch_scnr_out_db")) for row in rows])
        valid = np.isfinite(x) & np.isfinite(y)
        xv, yv = x[valid], y[valid]
        slope, intercept = (np.polyfit(xv, yv, 1).tolist() if xv.size >= 2 else (float("nan"), float("nan")))
        prediction = slope * xv + intercept if math.isfinite(slope) else np.full_like(yv, np.nan)
        residual = yv - prediction
        r2 = (1.0 - float(np.sum(residual ** 2) / np.sum((yv - np.mean(yv)) ** 2))
              if yv.size >= 2 and np.sum((yv - np.mean(yv)) ** 2) > 0.0 else float("nan"))
        monotonic = bool(yv.size >= 2 and np.all(np.diff(yv) >= -1e-9))
        max_abs = float(np.max(np.abs(residual))) if residual.size else float("nan")
        passed = bool(monotonic and math.isfinite(slope) and 0.85 <= slope <= 1.15
                      and math.isfinite(r2) and r2 >= 0.98
                      and math.isfinite(max_abs) and max_abs <= args.max_error_db)
        all_pass = all_pass and passed
        result_rows.append({"angle_deg": angle, "sample_count": int(yv.size),
                            "monotonic": int(monotonic), "slope_db_per_db": slope,
                            "intercept_db": intercept, "r2": r2,
                            "max_abs_residual_db": max_abs, "sanity_pass": int(passed)})
    out = args.output.resolve() if args.output else root / "output_scnr_transfer_validation.csv"
    ensure_dir(out.parent)
    write_csv(out, result_rows)
    (out.parent / "output_scnr_transfer_validation.json").write_text(json.dumps({
        "input": str(audit), "max_error_db": args.max_error_db,
        "all_pass": all_pass, "angles": result_rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    diagnosis_lines = [
        "# output SCNR 传递函数诊断",
        "",
        "本记录由 `16_validate_output_scnr_transfer.py` 根据本次实际审计 CSV 自动生成；"
        "它回答的是同一固定几何下目标幅度增加时 CSI 输出 SCNR 是否单调，不能把控制量当成正式横轴。",
        "",
        "## 定义",
        "",
        "`output SCNR = 10 log10(P_S / P0)`，其中 `P_S` 是固定 reference support 的目标输出功率，"
        "`P0` 是 C+N-only 独立 realization 的 ensemble 平均背景功率。GO 的瞬时 "
        "`M=max(mean_L,mean_R,mean_T,mean_B)` 只用于 CFAR，不作为 SCNR 分母。",
        "",
        "## 本次证据",
        "",
        "| angle | monotonic | slope (dB/dB) | R² | max residual (dB) | sanity |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result_rows:
        diagnosis_lines.append(
            f"| {number(row.get('angle_deg')):.1f} | {row.get('monotonic', '')} | "
            f"{number(row.get('slope_db_per_db')):.5f} | {number(row.get('r2')):.5f} | "
            f"{number(row.get('max_abs_residual_db')):.3f} | {row.get('sanity_pass', '')} |"
        )
    diagnosis_lines += [
        "",
        f"验证输入：`{audit}`；可接受批量拟合残差上限：`{args.max_error_db:g} dB`。",
        "",
        "## 根因判定",
        "",
        "旧 pilot 的非单调/大偏差来自两类可追溯的口径问题：一是把不同 period 的压缩 schedule "
        "与另一 period 的 Doppler/range 支撑混用；二是 N-only 自适应 Doppler center 随噪声漂移，"
        "而支撑映射没有使用当前 run 的冻结 center。修复后，S-only/S+N/C+N-only 共享固定物理支撑，"
        "并把生产 threshold map 与 SCNR 的 P0 分开。若本表仍失败，应优先检查固定 row/range、"
        "Doppler center、P38/CSI 是否随数据改变、量化饱和及 S+C+N 与 C+N 是否为同一算子；"
        "不能用调 CFAR 门限掩盖传递函数问题。",
        "",
        "## 解释边界",
        "",
        "本文件证明的是标尺和链路的一致性。它不证明目标级/航迹级 Pd 已达到 90%，也不把一次"
        "calibration batch 的斜率替代正式 Monte Carlo 的实测 output SCNR。",
    ]
    diagnosis_path = args.diagnosis_output.resolve()
    ensure_dir(diagnosis_path.parent)
    diagnosis_path.write_text("\n".join(diagnosis_lines) + "\n", encoding="utf-8")
    print(f"[{'PASS' if all_pass else 'FAIL'}] output SCNR transfer validation: {out}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
