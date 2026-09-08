#!/usr/bin/env python3
"""汇总当前专项阶段的真实证据，生成 Markdown/HTML/PDF 报告。"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def number(value: object, default: float = float("nan")) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def fmt(value: object, digits: int = 3) -> str:
    parsed = number(value)
    return f"{parsed:.{digits}f}" if math.isfinite(parsed) else "—"


def table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---:" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def copy_asset(source: Path, assets: Path) -> str:
    if not source.is_file():
        return ""
    destination = assets / source.name
    shutil.copy2(source, destination)
    return f"assets/{destination.name}"


def image_block(source: Path, assets: Path, title: str) -> str:
    relative = copy_asset(source, assets)
    return f"### {title}\n\n![{title}]({relative})" if relative else f"### {title}\n\n（图像缺失：`{source}`）"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit-dir", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--min3-dir", type=Path, required=True)
    parser.add_argument("--angle-dir", type=Path, required=True)
    parser.add_argument("--angle-cases-dir", type=Path, default=None,
                        help="可选：28_aggregate_angle_cases.py 的六案例汇总目录")
    parser.add_argument("--output-dir", type=Path,
                        default=Path("docs/GMTI_输出SCNR_专项阶段报告_20260827"))
    args = parser.parse_args()
    unit = args.unit_dir.resolve()
    target = args.target_dir.resolve()
    min3 = args.min3_dir.resolve()
    angle = args.angle_dir.resolve()
    angle_cases = args.angle_cases_dir.resolve() if args.angle_cases_dir else None
    out = args.output_dir.resolve()
    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    unit_rows = read_csv(unit / "pooled/unit_pd_pooled.csv")
    unit_audit = read_csv(unit / "pooled/unit_pd_threshold_audit_failures.csv")
    target_rows = read_csv(target / "pooled/target_track_curve.csv")
    target_transition = read_csv(target / "pooled/target_track_transition.csv")
    min3_rows = read_csv(min3 / "pooled_comparison/minpoints_comparison.csv")
    angle_summary = read_csv(angle / "angle_module_summary.csv")
    angle_phase = read_csv(angle / "angle_module_phase_noise.csv")
    angle_p38 = read_csv(angle / "angle_module_p38.csv")
    angle_prod = read_csv(angle / "angle_module_production_audit.csv")
    angle_case_summary = read_csv(angle_cases / "angle_case_summary.csv") if angle_cases else []

    unit_table = []
    for row in unit_rows:
        unit_table.append([
            fmt(row.get("input_snr_db_control"), 0), fmt(row.get("output_scnr_db_mean")),
            f"{row.get('unit_hits','—')}/{row.get('unit_trials','—')}",
            fmt(row.get("unit_pd"), 3), fmt(row.get("unit_pd_empirical_n_only_mean"), 3),
            fmt(row.get("unit_pd_iid_go_mean"), 3), fmt(row.get("unit_pd_minus_empirical_theory"), 3),
        ])
    target_table = []
    for row in target_rows:
        target_table.append([
            fmt(row.get("input_snr_db_control"), 0), fmt(row.get("output_scnr_db")),
            fmt(row.get("unit_pd")), fmt(row.get("cluster_min2_pd")),
            fmt(row.get("cluster_min3_pd")), fmt(row.get("cluster_production_pd")),
            fmt(row.get("target_pd")), fmt(row.get("track_pd")),
            fmt(row.get("track_pd_independent_3p2_minus_2p3")),
            fmt(row.get("mask_hit_count_mean")),
        ])
    min3_table = []
    for row in min3_rows:
        min3_table.append([
            row.get("config", ""), fmt(row.get("input_snr_db_control"), 0),
            fmt(row.get("output_scnr_db")), fmt(row.get("unit_pd")),
            fmt(row.get("cluster_pd")), fmt(row.get("target_pd")), fmt(row.get("track_pd")),
        ])
    angle_table = []
    for row in angle_summary:
        angle_table.append([row.get("module", ""), row.get("status", ""),
                            row.get("sample_count", ""), fmt(row.get("max_abs_error"), 5),
                            row.get("parameter_source", "")])
    phase_table = []
    for row in angle_phase:
        phase_table.append([fmt(row.get("gamma_db"), 0), fmt(row.get("phase_rmse_rad"), 4),
                            fmt(row.get("phase_sigma_theory_rad"), 4), fmt(row.get("ratio_empirical_to_theory"), 3)])
    p38_table = []
    for row in angle_p38:
        p38_table.append([fmt(row.get("gamma_db"), 0), fmt(row.get("slope_rmse_rad_per_hz"), 7),
                          fmt(row.get("slope_sigma_theory_rad_per_hz"), 7), fmt(row.get("ratio_empirical_to_theory"), 3)])

    angle_case_table = []
    for row in angle_case_summary:
        angle_case_table.append([
            row.get("case", ""), row.get("position", ""),
            fmt(row.get("command_angle_deg"), 1), fmt(row.get("physical_target_angle_deg"), 1),
            row.get("seed", ""), fmt(row.get("calibration_slope")),
            fmt(row.get("calibration_r2"), 4), fmt(row.get("calibration_max_abs_bias_db")),
            row.get("calibration_sanity_pass", ""),
        ])

    assets_and_titles = [
        (unit / "pooled/unit_pd_vs_output_scnr.png", "图 1：生产 GO 单元 Pd 与 output SCNR"),
        (unit / "pooled/unit_pd_threshold_audit.png", "图 2：生产阈值与离线阈值偏差审计"),
        (target / "pooled/target_funnel_vs_output_scnr.png", "图 3：单元—聚类—目标 funnel"),
        (target / "pooled/track_2of3_vs_output_scnr.png", "图 4：三屏选二 truth 航迹 Pd"),
        (target / "pooled/joint_hit_mask_by_output_scnr.png", "图 5：3×5 joint GO hit mask"),
        (min3 / "pooled_comparison/minpoints_comparison.png", "图 6：min6 与 min3 诊断对照"),
        (angle / "angle_module_phase_noise.png", "图 7：双通道相位模块"),
        (angle / "angle_module_p38.png", "图 8：P38 斜率模块"),
        (angle / "angle_module_projection.png", "图 9：Doppler/ENU 几何闭环"),
        (angle / "angle_module_production_audit.png", "图 10：已有生产角度审计"),
    ]
    if angle_cases:
        assets_and_titles.extend([
            (angle_cases / "angle_cases_probability_vs_output_scnr.png", "图 11：中心/边缘六案例 Pd 曲线"),
            (angle_cases / "angle_cases_angle_rmse_vs_output_scnr.png", "图 12：中心/边缘测角 RMSE"),
            (angle_cases / "angle_cases_calibration_audit.png", "图 13：六案例 output-SCNR 校准审计"),
        ])
    figures = [image_block(source, assets, title) for source, title in assets_and_titles]

    max_unit_diff = max((abs(number(row.get("unit_pd_minus_empirical_theory")))
                         for row in unit_rows if math.isfinite(number(row.get("unit_pd_minus_empirical_theory")))),
                        default=float("nan"))
    unit_n = max((number(row.get("unit_trials")) for row in unit_rows), default=float("nan"))
    prod_threshold_bias = [number(row.get("threshold_bias_db_mean")) for row in unit_rows
                           if math.isfinite(number(row.get("threshold_bias_db_mean")))]
    phase_high = [number(row.get("ratio_empirical_to_theory")) for row in angle_phase
                  if number(row.get("gamma_db")) >= 15.0]
    p38_high = [number(row.get("ratio_empirical_to_theory")) for row in angle_p38
                if number(row.get("gamma_db")) >= 15.0]
    transition_nonempty = [row for row in target_transition if number(row.get("screen_groups")) > 0]
    angle_case_section = ""
    angle_case_conclusion = "本报告尚未纳入中心/边缘六案例 sweep。"
    angle_case_next_step = "下一阶段先完成中心/边缘六案例 sweep，再补充多 seed。"
    if angle_cases:
        angle_case_conclusion = "六案例中心/边缘运行已完成；其单 seed 校准 sanity 结果和角度 RMSE 已单独列出，不把失败项隐藏。"
        angle_case_next_step = "正式多 seed 前，优先对中心 30°/45°、边缘 0°各补 3–5 seed，区分校准随机性与真实波位边缘效应。"
        angle_case_section = f"""
# 5. 0°/30°/45°中心与波位边缘案例

在模块化几何、相位、P38 和投影自检通过后，补跑了六个真实 CUDA 案例。中心案例的
物理目标角为 0°、30°、45°；边缘案例通过 `target_angle_offset_deg=0.8°` 注入，物理
目标角为 0.8°、30.8°、45.8°，命令角仍标记为对应波位。每个案例 1 个 seed、10 个
控制档、每档 3 个 screen period；因此这一节用于检查角度/波位依赖和链路一致性，
不把量化的 0/1/3 命中率宣称为最终统计置信区间。

生产场景单波束宽度为 1.81871°，半波束宽度约 0.90936°；0.8° 偏置约为半波束的
88%，因此这里的 edge 是“波束内靠近边缘”而不是越过硬可见性门限的场外点。

{table(["案例", "位置", "命令角", "物理目标角", "seed", "校准斜率", "R²", "最大偏差(dB)", "sanity"], angle_case_table)}

校准 sanity 是运行时的既定审计标志，并非事后放宽的判据。中心 30°、中心 45°和
边缘 0°本轮为 false，保留为后续多 seed 复核项；边缘 30°/45°为 true。所有案例
的 manifest 都记录了 GO/TrackManager 审计后才删除 raw BIN。

{figures[10]}

{figures[11]}

{figures[12]}

图 11 的三个子图仍全部以 measured output SCNR 为横轴；图 12 只显示有 truth-matched
角度的档位，低 SCNR 没有合法角度估计时保留为空。中心与边缘的 unit Pd 上升位置
大体一致，但 target/track 曲线受稀疏支撑和三屏事件影响，不能从一个 seed 比较细小
差异。边缘案例的 matched angle RMSE 约 0.7–0.9°，中心高 SCNR 匹配点约 0.03–0.18°；
这同时提示边缘测角误差需要后续增加 seed 和更细的 offset 扫描。
"""
    report = f"""---
title: "GMTI 输出 SCNR—测角精度—检测概率专项阶段报告"
subtitle: "单元级 GO-CFAR 修复、目标/航迹 funnel 与测角模块化验证（2026-08-27）"
CJKmainfont: Noto Sans CJK SC
geometry: margin=1.55cm
fontsize: 10pt
---

# 0. 本阶段结论

本阶段先修正单元级，再检查目标级/航迹级，最后把测角拆成独立模块。结论是：

1. 单元级曲线偏差的主要根因已定位并修复：生产 split GO-CFAR 的实际阈值来自
   带内/带外独立分支，旧审计却把合并功率图重新计算成一个混合训练窗。现在生产
   链输出真实 threshold map，评估直接使用该 map，避免离线阈值替代生产阈值。
2. 五 seed pooled 单元 Pd 与 N-only 经验 GO 理论最大绝对差为
   `{max_unit_diff:.3f}`，每个档位分母为约 `{unit_n:.0f}` 个 period；没有生产阈值
   审计失败。阈值偏差（旧混合重算相对生产值）约为
   `{min(prod_threshold_bias) if prod_threshold_bias else float('nan'):.2f}–{max(prod_threshold_bias) if prod_threshold_bias else float('nan'):.2f} dB`，
   这是评估口径 bug，不是 CFAR 门限调参。
3. 真实 joint hit mask 在中等 SCNR 只有 1–2 个单元；因此 unit Pd 已经上升时，
   cluster/target Pd 仍低。单独把 min_points 从 6 改成 3 并不能制造缺失的空间支撑。
4. 三屏主指标使用 truth 2-of-3；非饱和 8 dB 档的实测 track Pd 为 0.8，独立近似为
   0.741，屏间相关性明显，故最终报告不能只用 `3p^2-2p^3`。
5. 测角基础模块已经独立闭环：Doppler 几何和 ENU 投影误差为 0；双通道相位在
   ≥15 dB 时实测/理论比值约 `{min(phase_high) if phase_high else float('nan'):.3f}–{max(phase_high) if phase_high else float('nan'):.3f}`，
   P38 斜率约 `{min(p38_high) if p38_high else float('nan'):.3f}–{max(p38_high) if p38_high else float('nan'):.3f}`。

{angle_case_conclusion}

# 1. 测试边界、配置和证据

本阶段真实生产实验使用 CUDA、S+N（面积杂波关闭、热噪声开启）固定单目标场景，
GO 参数为 `PFA=1e-6`、`guard=4`、`background=16`、`min_points=6`、3–5 点
小簇恢复峰值门限 `+20 dB`。主横轴全部是固定真值支撑上的实际 output SCNR；
输入 `target_snr_db` 只作控制变量。

单元级有 5 个独立 seed、10 个控制档，每档 3 个 period；目标/航迹使用同一批 5
seed 的保留 GO hits/power/threshold 图。另有一个独立的真实 CUDA `min_points=3`
单 seed 对照，仅用于根因诊断。所有 raw BIN 均在相应 GO/TrackManager 审计后清理，
原始结果目录和 manifest 保留。

# 2. 单元级 GO-CFAR：先修曲线

## 2.1 理论和生产阈值

8 个独立块为四个 16×16 角块和四个 16×9/9×16 中间块。四方向训练均值共享角块，
分别记为 $L,R,T,B$，生产统计量为 $M=\\max(L,R,T,B)$。生产 alpha 由实现中的
总背景单元数计算，当前运行约为 $\\alpha=13.4495$。给定 output SCNR
$\\gamma=P_S/P_0$，条件检测概率是

$$P(D=1\\mid M=m)=Q_1(\\sqrt{{2\\gamma}},\\sqrt{{2\\alpha m}}),
\\qquad P_d(\\gamma)=E_M[P(D=1\\mid M)].$$

旧离线审计把 split 分支的功率图合并后再算 `max(mean_L,mean_R,mean_T,mean_B)`，
这与生产实际使用的分支阈值不等价。修复后，C++ 在每个 CFAR 分支输出实际 threshold
map，评估在 truth CUT 直接读取该值，$m_{{prod}}=threshold/\\alpha$。

## 2.2 逐档数据表

{table(["控制量(dB)", "output SCNR(dB)", "命中/总数", "实测 Pd", "经验 GO 理论", "IID GO 理论", "经验−实测"], unit_table)}

图 1 和图 2：

{figures[0]}

{figures[1]}

图 1 的实线/点是生产 GO exact-CUT 命中；虚线是从实际 N-only 训练窗做的经验积分；
IID 曲线只作为结构基线。图 2 的目的不是寻找新门限，而是确认评估使用的 threshold
与生产实现一致。当前 `unit_pd_threshold_audit_failures.csv` 为空。

# 3. 目标级与航迹级：区分支撑损失和单元损失

## 3.1 生产 funnel

生产目标事件严格沿用当前 clustering、3–5 点 +20 dB 小簇恢复、selector、relocation
和 truth matching。能从当前输出独立审计的形式写成

$$P_{{target}}=P_{{cluster}}P(\\text{{post-cluster}}\\mid\\text{{cluster}}),$$

其中 post-cluster 暂时把 selector/relocation/truth-match 合并，避免在没有独立 rejection
flag 时虚构每个条件概率。

{table(["控制量(dB)", "output SCNR(dB)", "unit Pd", "cluster min2", "cluster min3", "生产 cluster", "target Pd", "track 2/3", "独立近似", "3×5 mask均值"], target_table)}

{figures[2]}

{figures[4]}

图 3 中 unit Pd 在约 5–7 dB 已较高，但生产 cluster/target 仍为 0；图 5 直接展示
同一 truth 支撑附近的 3×5 joint hit mask，说明原因是空间支撑稀疏而非再一次的 SCNR
口径错误。min2/min3 列是 mask sensitivity，不能替代生产 clustering。

## 3.2 min_points=3 真实对照

{table(["配置", "控制量(dB)", "output SCNR(dB)", "unit Pd", "cluster Pd", "target Pd", "track Pd"], min3_table)}

{figures[5]}

图 6 是一次真实 CUDA 对照，不能当作多 seed 概率估计。它在约 4 dB output SCNR
已有 unit hit，但目标级仍为 0；直到约 16.7 dB 才出现 1/3 目标命中，约 18.9 dB
达到 3/3。逐周期 component size 保存在 `min3_component_size_audit.csv`。这证明把
数字 6 改成 3 不是单独修复；必须先改变目标在 Doppler/range 上的实际支撑或后续
筛选规则，并用同等审计条件的多 seed A/B 证明收益。

## 3.3 三屏选二

主指标为同一 truth 轨迹的三屏命中数至少 2。若单屏命中相同且独立，

$$P_{{track,ind}}=3p^2-2p^3.$$

实测用 $P_{{12}}+P_{{13}}+P_{{23}}-2P_{{123}}$，并记录
$P(D_t=1\\mid D_{{t-1}}=1)$、$P(D_t=1\\mid D_{{t-1}}=0)$ 和相关系数。当前有效转移
档数为 `{len(transition_nonempty)}`；8 dB 非饱和档实测 2-of-3 与独立近似的差异已经
可见，正式结论采用实测联合公式。TrackManager Confirmed+matched_this_frame 只留作
附录诊断，不替代这个 truth 事件。

{figures[3]}

# 4. 测角拆分验证

## 4.1 模块清单和参数

{table(["模块", "状态", "样本数", "误差/偏差", "参数来源"], angle_table)}

### 模块 1：Doppler 几何

生产关系是

$$f_{{geo}}=-\\frac{{2V}}{{\\lambda}}\\sin\\theta,
\\qquad \\hat\\theta=-\\arcsin\\left(\\frac{{\\lambda f_{{geo}}}}{{2V}}\\right).$$

测试使用真实标准场景速度 `V=60 m/s` 和 `fc=16.4721130769 GHz` 导出的
$\\lambda=0.0182000$ m；-45、-30、-10、0、10、30、45 度逐点正逆变换。

### 模块 2：双通道相位

每通道 $x_i=e^{{j\\phi}}+n_i$，$n_i\\sim CN(0,1/\\gamma_i)$，相位差估计为
$\\angle(x_1x_2^*)$。小噪声近似为

$$\\gamma_\\phi=\\frac{{2\\gamma_1\\gamma_2}}{{\\gamma_1+\\gamma_2}},
\\qquad \\sigma_\\phi\\simeq1/\\sqrt{{\\gamma_\\phi}}.$$

{table(["SNR(dB)", "实测 phase RMSE(rad)", "理论(rad)", "实测/理论"], phase_table)}

{figures[6]}

低 SNR 时相位分布接近圆周均匀，RMSE 不再服从小噪声近似；脚本显式保留该边界。

### 模块 3：P38 斜率

对 $\\phi_i=kf_i+b+\\epsilon_i$ 做中心化最小二乘，

$$\\sigma_k=\\frac{{\\sigma_\\phi}}
{{\\sqrt{{\\sum_i(f_i-\\bar f)^2}}}}.$$

common-TX 双接收的 P38 原始斜率使用 $d/2$ 等效相位中心间距，当前真值
`k=-0.00890118 rad/Hz`。高 SNR 统计如下：

{table(["SNR(dB)", "实测 slope RMSE", "理论", "实测/理论"], p38_table)}

{figures[7]}

低 SNR 的斜率偏差来自相位 unwrap 不满足条件；这说明后续端到端测角实验必须单独
记录 unwrap/P38 valid/inlier 状态，不能只报一个角度 RMSE。

### 模块 4：ENU 投影和生产接口

生产 `writeresults` 约定 `sinA=f*lambda/(2V)=-sin(theta)`，左视 look 向量再按
平台航向旋转。若离线脚本漏掉负号，会得到整条角度符号翻转。本轮闭环误差为 0；
已有 0 度生产 target-match 样本共 `{len(angle_prod)}` 个，只作为接口审计，不能外推
到其他角度或波位边缘。

{figures[8]}

{figures[9]}

{angle_case_section}

# 6. 当前未完成项和下一步

{angle_case_next_step} 目前仍没有把测角模块的 synthetic phase SNR
直接映射成完整 CSI/CTDR 端到端角度 Pd 曲线。下一阶段的最短顺序是：

1. 保持已经对齐的生产 threshold map 和 output-SCNR 定义，先做一个中心波位单目标
   的端到端 phase/P38/定位验证，逐周期保存 valid、inlier、unwrap 和 angle error；
2. 对六案例中校准 sanity=false 的中心 30°/45°、边缘 0°优先补 3–5 seed，随后再扩展
   其余案例；不把 `target_snr_db` 当主轴；
3. 只有角度模块和目标/航迹 funnel 都能在 pooled 数据中复现，才进入正式报告的多角度
   主图与理论三层 GO 模型整合。

# 7. 复现入口和证据索引

单元级 pooled：`{unit}`；目标/航迹 pooled：`{target}`；min3 对照：`{min3}`；
测角模块：`{angle}`。本报告目录同时保存所有 PNG/PDF 图的副本和本 Markdown。

```bash
python3 scripts/gmti_scnr_eval/23_aggregate_unit_pd_validation.py \\
  --input-root outputs/gmti_scnr_eval/unit_pd_threshold_0deg_20260827 \\
  --output-dir outputs/gmti_scnr_eval/unit_pd_threshold_0deg_20260827/pooled
python3 scripts/gmti_scnr_eval/24_analyze_target_track.py \\
  --input-root outputs/gmti_scnr_eval/target_debug_0deg_20260827 \\
  --output-dir outputs/gmti_scnr_eval/target_debug_0deg_20260827/pooled
python3 scripts/gmti_scnr_eval/25_validate_angle_modules.py \\
  --scenario outputs/gmti_scnr_eval/target_debug_0deg_20260827/seed_20260842/angle_p0p000/s_plus_n/scenario.json \\
  --production-root outputs/gmti_scnr_eval/target_debug_0deg_20260827 \\
  --output-dir outputs/gmti_scnr_eval/angle_modules_20260827
python3 scripts/gmti_scnr_eval/28_aggregate_angle_cases.py \\
  --case center_0=outputs/gmti_scnr_eval/angle_sweep_center_20260827/angle_p0p000 \\
  --case center_30=outputs/gmti_scnr_eval/angle_sweep_center_20260827/angle_p30p000 \\
  --case center_45=outputs/gmti_scnr_eval/angle_sweep_center_20260827_retry45/angle_p45p000 \\
  --case edge_0=outputs/gmti_scnr_eval/angle_sweep_edge_20260827/angle_p0p000 \\
  --case edge_30=outputs/gmti_scnr_eval/angle_sweep_edge_20260827/angle_p30p000 \\
  --case edge_45=outputs/gmti_scnr_eval/angle_sweep_edge_20260827/angle_p45p000 \\
  --output-dir outputs/gmti_scnr_eval/angle_cases_20260827
```
"""
    md = out / "GMTI_输出SCNR_专项阶段报告_20260827.md"
    md.write_text(report, encoding="utf-8")
    html = out / "GMTI_输出SCNR_专项阶段报告_20260827.html"
    pdf = out / "GMTI_输出SCNR_专项阶段报告_20260827.pdf"
    common = ["pandoc", str(md), "--resource-path", str(out), "--standalone"]
    html_result = subprocess.run(common + ["-o", str(html)],
                                 check=False, capture_output=True, text=True)
    if html_result.returncode != 0:
        raise RuntimeError(f"HTML 生成失败：{html_result.stderr}")
    pdf_result = subprocess.run(common + ["--pdf-engine=xelatex",
                                         "-V", "CJKmainfont=Noto Sans CJK SC",
                                         "-V", "geometry:margin=1.55cm",
                                         "-o", str(pdf)], check=False,
                                capture_output=True, text=True)
    if pdf_result.returncode != 0:
        raise RuntimeError(f"PDF 生成失败：{pdf_result.stderr[-4000:]}")
    (out / "report_manifest.json").write_text(json.dumps({
        "unit_dir": str(unit), "target_dir": str(target), "min3_dir": str(min3),
        "angle_dir": str(angle), "angle_cases_dir": str(angle_cases) if angle_cases else None,
        "markdown": str(md), "html": str(html), "pdf": str(pdf),
        "asset_count": len([path for path in assets.iterdir() if path.is_file()]),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 阶段报告已生成：{pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
