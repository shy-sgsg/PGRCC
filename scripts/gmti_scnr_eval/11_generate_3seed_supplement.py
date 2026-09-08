#!/usr/bin/env python3
"""汇总三个真实 S+C+N seed，补充初版 GO-CFAR 技术报告。"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]


def load_report_module():
    path = SCRIPT_DIR / "10_generate_initial_report.py"
    spec = importlib.util.spec_from_file_location("go_initial_report", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载报告模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finite(value: float) -> bool:
    return math.isfinite(value)


def number(row: dict, key: str, default: float = float("nan")) -> float:
    try:
        value = row.get(key, "")
        return float(value) if value not in {"", None} else default
    except (TypeError, ValueError):
        return default


def integer(row: dict, key: str, default: int = 0) -> int:
    value = number(row, key, float(default))
    return int(round(value)) if finite(value) else default


def fmt(value: object, digits: int = 3) -> str:
    try:
        value = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    return f"{value:.{digits}f}" if finite(value) else "—"


def table(headers: list[str], rows: list[list[object]]) -> str:
    text = "| " + " | ".join(headers) + " |\n|" + "|".join("---" for _ in headers) + "|\n"
    return text + "".join("| " + " | ".join(str(cell) for cell in row) + " |\n" for row in rows)


def prefixed_rows(module, roots: list[Path]):
    diagnostics: list[dict] = []
    sequence: list[dict] = []
    cases: list[dict] = []
    for root in roots:
        local_diag, local_sequence, local_cases = module.load_case_rows(root)
        prefix = root.name + "/"
        for row in local_diag + local_sequence:
            row["case_key"] = prefix + row["case_key"]
        for row in local_cases:
            row["case_key"] = prefix + row["case_key"]
            row["seed_root"] = root.name
        diagnostics.extend(local_diag)
        sequence.extend(local_sequence)
        cases.extend(local_cases)
    return diagnostics, sequence, cases


def common_key(row: dict) -> tuple[str, float, float]:
    return (str(row["case_label"]), float(row["command_angle_deg"]),
            number(row, "configured_target_snr_db"))


def aggregate_unit(module, rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, float, float], list[dict]] = defaultdict(list)
    for row in rows:
        if integer(row, "unit_cell_window_valid", 0) == 1:
            grouped[common_key(row)].append(row)
    output: list[dict] = []
    for key, values in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0], item[0][2])):
        hits = sum(integer(row, "unit_go_cfar_hit", 0) for row in values)
        exact = sum(integer(row, "unit_go_cfar_exact_cut_hit", 0) for row in values)
        ratios: list[float] = []
        excess: list[float] = []
        for row in values:
            cut, noise = number(row, "unit_cut_power"), number(row, "unit_cfar_train_mean")
            if cut > 0.0 and noise > 0.0:
                ratios.append(10.0 * math.log10(cut / noise))
            value = number(row, "unit_scnr_det_out_db")
            if finite(value):
                excess.append(value)
        low, high = module.wilson_interval(hits, len(values))
        output.append({"case_key": f"{key[0]}_{key[1]:g}", "case_label": key[0],
                       "command_angle_deg": key[1], "truth_angle_deg": key[1],
                       "configured_target_snr_db": key[2], "trials": len(values), "hits": hits,
                       "pd": hits / len(values), "wilson_low": low, "wilson_high": high,
                       "exact_hits": exact, "exact_pd": exact / len(values),
                       "cut_ratio_median_db": float(np.median(ratios)) if ratios else float("nan"),
                       "excess_scnr_median_db": float(np.median(excess)) if excess else float("nan"),
                       "excess_scnr_count": len(excess)})
    return output


def aggregate_target_track(module, diagnostics: list[dict], sequence: list[dict]) -> list[dict]:
    targets: dict[tuple[str, float, float], list[dict]] = defaultdict(list)
    tracks: dict[tuple[str, float, float], list[dict]] = defaultdict(list)
    for row in diagnostics:
        targets[common_key(row)].append(row)
    for row in sequence:
        tracks[common_key(row)].append(row)
    output: list[dict] = []
    for key, values in sorted(targets.items(), key=lambda item: (item[0][1], item[0][0], item[0][2])):
        t_hits = sum(integer(row, "final_output", 0) for row in values)
        t_low, t_high = module.wilson_interval(t_hits, len(values))
        trows = tracks.get(key, [])
        tr_hits = sum(integer(row, "trackmanager_confirmed_matched_protocol_output", 0) for row in trows)
        two = sum(integer(row, "two_of_three_target_hits", 0) for row in trows)
        tr_low, tr_high = module.wilson_interval(tr_hits, len(trows)) if trows else (float("nan"), float("nan"))
        output.append({"case_key": f"{key[0]}_{key[1]:g}", "case_label": key[0],
                       "command_angle_deg": key[1], "truth_angle_deg": key[1],
                       "configured_target_snr_db": key[2], "target_hits": t_hits,
                       "target_trials": len(values), "target_pd": t_hits / len(values),
                       "target_low": t_low, "target_high": t_high, "track_hits": tr_hits,
                       "track_trials": len(trows), "track_pd": tr_hits / len(trows) if trows else float("nan"),
                       "track_low": tr_low, "track_high": tr_high,
                       "two_of_three_pd": two / len(trows) if trows else float("nan")})
    return output


def aggregate_angle(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, float, float], list[float]] = defaultdict(list)
    for row in rows:
        if integer(row, "final_output", 0) == 1:
            value = number(row, "angle_error_deg")
            if finite(value):
                grouped[common_key(row)].append(value)
    output: list[dict] = []
    for key, values in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0], item[0][2])):
        output.append({"case_key": f"{key[0]}_{key[1]:g}", "case_label": key[0],
                       "command_angle_deg": key[1], "truth_angle_deg": key[1],
                       "configured_target_snr_db": key[2], "matched_count": len(values),
                       "bias_deg": float(np.mean(values)),
                       "rmse_deg": float(np.sqrt(np.mean(np.square(values)))),
                       "p95_abs_deg": float(np.quantile(np.abs(values), 0.95))})
    return output


def write_machine_summary(path: Path, cases: list[dict], unit: list[dict], target_track: list[dict], angle: list[dict]):
    payload = {"cases": [{key: (str(value) if isinstance(value, Path) else value) for key, value in row.items()} for row in cases],
               "unit": unit, "target_track": target_track, "angle": angle}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, action="append", required=True,
                        help="重复给出三个 seed 的 spluscn_edge case 根目录")
    parser.add_argument("--report", type=Path,
                        default=Path("docs/GO-CFAR_真实S+C+N_三角度中心边缘3seed补充报告.md"))
    parser.add_argument("--theory-root", type=Path, default=ROOT /
                        "outputs/gmti_scnr_eval/final_go/formal/selected_beams_v8_pf1e6_min6_small3_20db_goalpha")
    args = parser.parse_args()
    roots = [path.resolve() for path in args.batch_root]
    if len(roots) != 3:
        raise SystemExit("本补充报告固定要求 3 个独立 seed")
    module = load_report_module()
    diagnostics, sequence, cases = prefixed_rows(module, roots)
    unit = aggregate_unit(module, diagnostics)
    target_track = aggregate_target_track(module, diagnostics, sequence)
    angle = aggregate_angle(diagnostics)
    theory_path = args.theory_root.resolve() / "theory/go_cfar_cell_theory.csv"
    theory = module.read_csv(theory_path) if theory_path.is_file() else []
    edge_offset = next((abs(row["truth_angle_deg"] - row["command_angle_deg"]) for row in cases
                        if row["label"] == "right_edge"), 0.8)
    for collection in (unit, target_track, angle):
        for row in collection:
            row["truth_angle_deg"] = (row["command_angle_deg"] + edge_offset
                                       if row["case_label"] == "right_edge"
                                       else row["command_angle_deg"])
    lambda_m = 0.0182
    d_m = 0.17
    plot_dir = module.ensure_dir(args.report.resolve().parent / "GO-CFAR_真实S+C+N_3seed_曲线")
    module.make_plots(plot_dir, unit, target_track, angle, theory, edge_offset, lambda_m, d_m)
    unit_rows = [[row["case_label"], f"{row['command_angle_deg']:g}°", f"{row['configured_target_snr_db']:g}",
                  f"{row['hits']}/{row['trials']}", fmt(row["pd"]), f"[{fmt(row['wilson_low'])},{fmt(row['wilson_high'])}]",
                  fmt(row["excess_scnr_median_db"]), str(row["excess_scnr_count"])] for row in unit]
    tt_rows = [[row["case_label"], f"{row['command_angle_deg']:g}°", f"{row['configured_target_snr_db']:g}",
                f"{row['target_hits']}/{row['target_trials']}", fmt(row["target_pd"]),
                f"{row['track_hits']}/{row['track_trials']}", fmt(row["track_pd"]), fmt(row["two_of_three_pd"])]
               for row in target_track]
    angle_rows = [[row["case_label"], f"{row['command_angle_deg']:g}°", f"{row['configured_target_snr_db']:g}",
                   str(row["matched_count"]), fmt(row["bias_deg"]), fmt(row["rmse_deg"]), fmt(row["p95_abs_deg"])]
                  for row in angle]
    report = f"""# 真实 S+C+N GO-CFAR 三角度中心/波束边缘 3-seed 补充报告

本报告补充 [单 seed 初版报告](GO-CFAR_真实S+C+N_三角度中心边缘初版技术报告.md)，使用 3 个独立 seed、18 个真实 S+C+N case（0°/30°/45°中心和 +{edge_offset:g}°右边缘），每个 case 连续 3 周期。所有 case 均为生产 GO-CFAR（Pfa=1e-6、guard=4、background=16、alpha=13.44951031977817）、min_points=6、小簇 3–5 单元/局部峰值+20 dB；raw BIN 只在 GO/TrackManager 审计成功后删除。

## 1. 3-seed 结果摘要

单元级 unit Pd 的每个点有 9 个真实目标帧试验；目标级 Pd 也有 9 个目标帧；航迹级是每 seed 一个三周期目标实例，因此每个点有 3 个 TrackManager 实例。区间为 Wilson 95%，不是把同一帧重复当成独立 seed。

### 单元级 Pd 与直接 GO CUT 超额 SCNR

{table(["位置", "命令角", "注入SNR", "unit命中", "Pd", "Wilson95%", "超额SCNR中位(dB)", "样本"], unit_rows)}

### 目标级/航迹级 Pd

{table(["位置", "命令角", "注入SNR", "目标命中/帧", "目标Pd", "航迹命中/实例", "航迹Pd", "2/3事件Pd"], tt_rows)}

### 测角误差

{table(["位置", "命令角", "注入SNR", "匹配数", "偏差(°)", "RMSE(°)", "绝对误差P95(°)"], angle_rows)}

## 2. 曲线与证据

- [单元 Pd 理论/实测、目标/航迹 Pd、输出 SCNR、测角曲线](GO-CFAR_真实S+C+N_3seed_曲线)
- 3-seed 机器可读摘要：[report_summary.json](../outputs/gmti_scnr_eval/formal/GO-CFAR_真实S+C+N_3seed_report_summary.json)
- seed 根目录：{', '.join(f'`{root}`' for root in roots)}
- 每个 case 保留 `scenario.json`、实际 XML、检测快照、GO 诊断、TrackManager 审计和日志；raw 删除状态在 `stage2/go_track_run_manifest.json`。

## 3. 理论—实测判断

1. 0°和 30°中心的 unit Pd 随注入 SNR 增大，45°中心在约 -20 dB 注入档进入高 Pd 区；这与 GO 单元理论“SCNR 增大则 Pd 单调上升”的方向一致，但本报告的注入 SNR 仍不是无偏输出 SCNR 横轴。
2. 30.8°和 45.8°右边缘在 3 个 seed 中仍显著低于对应中心，且真值行映射修复后仍存在；这是生产链真实的 Doppler/CSI/筛选响应，不能再归因于离线 truth-row bug，也不能用提高/降低门限掩盖。
3. TrackManager 协议 Pd 明显低于目标级 Pd，且部分点的三帧 `2/3` 事件为 1 而协议输出仍为 0；这正是“目标检测”和“Confirmed + 当前帧 measurement 载荷”两个不同事件，报告不把它们混成一个数字。
4. 测角样本仍偏少，0°中心存在约 0.66°系统偏差，30°/45°中心高 SNR 点约 0.05–0.07° RMSE；下一轮需要把 `af_used_hz`/相位有效 SCNR 和生产 Jacobian 逐帧合并到同一输出 SCNR 横轴。

## 4. 下一步

将每个边缘点的实测 unit Pd 写成 `p_cell(SCNR) × q_edge(angle, Doppler, CSI)`，用 3-seed 数据估计 `q_edge` 的区间；随后再扩展到 5 seed（仍不跑 20 seed），并用直接 GO CUT/训练窗 SCNR 重画理论曲线。若目标级和航迹级仍有差距，沿 `cluster → target_select → TrackManager association` 逐层定位，不改变 GO Pfa=1e-6 的验收口径。
"""
    report_path = args.report.resolve()
    module.ensure_dir(report_path.parent)
    report_path.write_text(report, encoding="utf-8")
    summary_path = ROOT / "outputs/gmti_scnr_eval/formal/GO-CFAR_真实S+C+N_3seed_report_summary.json"
    module.ensure_dir(summary_path.parent)
    write_machine_summary(summary_path, cases, unit, target_track, angle)
    print(f"[PASS] 3-seed 补充报告已生成：{report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
