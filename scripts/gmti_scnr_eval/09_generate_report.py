#!/usr/bin/env python3
"""从正式 GO-CFAR 产物生成最终 Markdown 与 PDF 报告。"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
from pathlib import Path

from scnr_eval_lib import read_csv


ROOT = Path(__file__).resolve().parents[2]


def number(row: dict[str, str], field: str) -> float:
    try:
        return float(row.get(field, ""))
    except (TypeError, ValueError):
        return float("nan")


def fmt(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}f}" if math.isfinite(value) else "—"


def table(headers: list[str], rows: list[list[str]]) -> str:
    return "| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" + "".join("| " + " | ".join(row) + " |\n" for row in rows)


def required(path: Path, name: str) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"缺少{name}：{path}")
    rows = read_csv(path)
    if not rows:
        raise SystemExit(f"{name}为空：{path}")
    return rows


def relative(document: Path, target: Path) -> str:
    try:
        return str(target.resolve().relative_to(document.parent.resolve()))
    except ValueError:
        return str(target.resolve())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", type=Path, required=True)
    parser.add_argument("--formal-summary-dir", type=Path, required=True)
    parser.add_argument("--joint-dir", type=Path, required=True)
    parser.add_argument("--calibration-summary", type=Path, action="append", required=True)
    parser.add_argument("--markdown", type=Path, default=ROOT / "docs" / "GMTI_SCNR_测角精度与检测概率联合分析报告.md")
    parser.add_argument("--pdf", type=Path, default=ROOT / "docs" / "GMTI_SCNR_测角精度与检测概率联合分析报告.pdf")
    parser.add_argument("--skip-pdf", action="store_true")
    args = parser.parse_args()
    root, formal, joint = args.experiment_root.resolve(), args.formal_summary_dir.resolve(), args.joint_dir.resolve()
    document, pdf = args.markdown.resolve(), args.pdf.resolve()
    parameters = required(root / "baseline" / "parameter_freeze.csv", "参数冻结表")
    go = required(root / "go_cfar_cell" / "go_cfar_cell_theory.csv", "GO 单元理论")
    pfa = required(root / "go_cfar_cell" / "go_cfar_pfa_validation.csv", "GO PFA 验证")[0]
    guard = required(root / "go_cfar_cell" / "go_cfar_guard_production_sweep.csv", "生产 GO guard 敏感性")
    real_cell = required(root / "go_cfar_cell" / "go_cfar_real_background_sweep.csv", "正式统计背景 GO 单元结果")
    theory = required(root / "angle_theory" / "angle_theory_summary.csv", "测角理论")
    angle = required(formal / "go_angle_error_summary.csv", "正式测角汇总")
    pd = required(formal / "go_target_and_track_pd_summary.csv", "正式目标航迹汇总")
    thresholds = required(joint / "go_joint_scnr_thresholds.csv", "联合门限")
    calibration = [row for path in args.calibration_summary for row in required(path.resolve(), "配对标定")]
    formal_seed_count = max((int(number(row, "independent_seed_count")) for row in pd
                             if math.isfinite(number(row, "independent_seed_count"))), default=0)
    scope_note = (
        f"本版正式批次包含 {formal_seed_count} 个独立 seed。"
        + ("这是单-seed 预备报告；区间仅反映该场景内目标重复，不能外推为跨场景稳定性。"
           if formal_seed_count < 3 else
           "结果按 seed 分层汇总；仍应结合报告列出的区间和场景限制解读。")
    )
    parameter_wanted = {"pf", "cfar_type", "cfar_guard_cells", "cfar_background_cells", "pulse_num", "fc", "d_chan", "scan_step_deg", "track_confirm_window", "track_confirm_hits"}
    parameter_rows = [[r.get("parameter_name", ""), r.get("runtime_value", r.get("value", "")), r.get("unit", ""), r.get("source_file", ""), r.get("physical_meaning", "")] for r in parameters if r.get("parameter_name") in parameter_wanted]
    go_rows = [[fmt(number(r, "scnr_db"), 1), fmt(number(r, "pd_go_conditional_mc"), 6)] for r in go]
    real_cell_rows = [[fmt(number(r, "truth_angle_deg"), 0),
                       fmt(number(r, "paired_calibrated_output_scnr_db"), 2),
                       fmt(number(r, "unit_go_cfar_pd"), 3),
                       fmt(number(r, "unit_go_cfar_pd_wilson95_low"), 3),
                       r.get("trial_count", ""),
                       fmt(number(r, "unit_directional_mean_spread_db"), 2)] for r in real_cell]
    guard_rows = [[r.get("guard_half_width_cells", ""), fmt(number(r, "paired_calibrated_output_scnr_db"), 2),
                   fmt(number(r, "unit_go_cfar_pd"), 3),
                   fmt(number(r, "unit_go_cfar_pd_wilson95_low"), 3), r.get("trial_count", "")]
                  for r in guard]
    calibration_rows = [[fmt(number(r, "truth_angle_deg"), 0), fmt(number(r, "configured_target_snr_db"), 1), fmt(number(r, "output_scnr_det_out_db_median"), 2), fmt(number(r, "output_scnr_phase_eff_db_median"), 2), r.get("sample_count", "")] for r in calibration]
    theory_rows = [[fmt(number(r, "truth_angle_deg"), 0), fmt(number(r, "ideal_crossing_phase_scnr_db"), 2), fmt(number(r, "production_crossing_phase_scnr_db"), 2), r.get("production_jacobian_source", "")] for r in theory]
    angle_rows = [[fmt(number(r, "truth_angle_deg"), 0), fmt(number(r, "configured_target_snr_db"), 1), fmt(number(r, "angle_rmse_deg"), 3), fmt(number(r, "angle_rmse_seed_bootstrap95_high"), 3), r.get("matched_detection_count", "")] for r in angle]
    pd_rows = [[fmt(number(r, "truth_angle_deg"), 0), fmt(number(r, "paired_calibrated_output_scnr_db"), 2), fmt(number(r, "target_pd"), 3), fmt(number(r, "target_pd_wilson95_low"), 3), fmt(number(r, "trackmanager_protocol_track_pd"), 3), fmt(number(r, "three_frame_independence_formula"), 3), r.get("target_frame_trials", "")] for r in pd]
    threshold_rows = [[r.get("constraint", ""), fmt(number(r, "point_estimate_scnr_db"), 2), fmt(number(r, "conservative_95_scnr_db"), 2), r.get("criterion", "")] for r in thresholds]
    figures = [root / "go_cfar_cell" / "F05_go_train_sweep.png", root / "go_cfar_cell" / "F06_go_pfa_sweep.png", root / "go_cfar_cell" / "F07_go_guard_production_sweep.png", root / "go_cfar_cell" / "F08_go_real_background_cell_pd.png", root / "angle_theory" / "F09_F12_angle_theory.png", formal / "F09_go_angle_rmse.png", formal / "F10_go_target_pd.png", formal / "F11_go_track_pd.png", joint / "F12_go_joint_scnr_thresholds.png"]
    figure_text = "\n".join(f"![{path.stem}]({relative(document, path)})" for path in figures if path.is_file())
    sections = [
        "---\ntitle: \"GMTI GO-CFAR 测角精度与检测概率—输出 SCNR 联合分析报告\"\nCJKmainfont: WenQuanYi Micro Hei\ngeometry: margin=2cm\n---\n",
        "# 结论摘要\n\n本报告仅分析生产二维 GO-CFAR 的选定命令波位子链路；0°、30°、45°各自使用独立单波位三周期输入，未把本结果宣称为全 61 波位扫描统计。联合门限取这些命令波位的正式测角、完整目标 truth 匹配和当前周期 Confirmed/matched TrackManager 协议输出约束的最大值。" + scope_note + "\n\n" + table(["约束", "点估计输出 SCNR (dB)", "95% 保守输出 SCNR (dB)", "判据"], threshold_rows),
        "# 1. 生产处理链与参数来源\n\nraw echo → 脉压 → Doppler/DBS → P38/CTDR → CSI → GO-CFAR → 聚类 → target_select → 运动补偿/重定位 → GMTI 结果 → 三屏选二 TrackManager。所有目标/航迹数据均通过此生产链；载荷只计 Confirmed + matched_this_frame 且来自当前关联 detection 的 measurement。\n\n" + table(["参数", "运行值", "单位", "来源", "物理含义"], parameter_rows),
        "# 2. 输出 SCNR 与目标模型\n\n当前 Stage2 JSON 只允许 `amplitude.type=snr_db`，没有经雷达方程校准的物理 RCS/m² 接口；故本报告给出 Stage2 输入 target_snr_db→实际输出 SCNR，绝不把输入值直接当输出 SCNR 或 dBsm。使用同几何、同 packet-addressed 随机实现的 S-only、C+N、S+C+N，S-only truth-matched 单元固定其它两组采样位置。\n\n$$SCNR_{det,out}=10\\log_{10}(P_{S-only}/P_{C+N}),\\quad\\gamma_\\phi=2\\gamma_1\\gamma_2/(\\gamma_1+\\gamma_2),\\quad\\sigma_\\phi\\simeq1/\\sqrt{\\gamma_\\phi}.$$\n\n" + table(["角度", "输入 target_snr_db", "输出 SCNR_det,out", "输出 SCNR_phase,eff", "样本数"], calibration_rows),
        "# 3. GO 单元级检测\n\n生产 GO-CFAR 使用 $\\hat P_{GO}=\\max(\\bar P_L,\\bar P_R,\\bar P_T,\\bar P_B)$ 和 $T=\\alpha\\hat P_{GO}$；代码以环形 N=1600 计算 $\\alpha=N(P_{FA}^{-1/N}-1)$，而四个方向条带共享角块，条件 MC 保留该相关性。配置 PFA=" + fmt(number(pfa, "configured_pfa"), 9) + "，条件积分估计=" + fmt(number(pfa, "pfa_conditional_mc"), 9) + "。\n\n" + table(["SCNR (dB)", "GO 条件 MC Pd"], go_rows) + "\n\n实际主旁瓣泄漏的 guard 敏感性采用生产脉压/CSI/GO hit 图，固定 truth 单元而非最终目标输出：\n\n" + table(["guard", "配对输出 SCNR", "指定单元 Pd", "Wilson LCB", "试验数"], guard_rows) + "\n\n正式统计背景单元试验固定在 Stage2 `row_truth/range_bin`，直接读取生产 GO hit 图；它不进入 cluster、target_select 或 truth matching。Pd/标定按命令波位 $\\theta_{cmd}$ 归组；测角误差仍逐帧使用运动后的真实方位。\n\n" + table(["命令波位", "配对输出 SCNR", "指定单元 Pd", "Wilson LCB", "试验数", "四方向背景差(dB)"], real_cell_rows),
        "# 4. 测角理论与正式实测\n\n理想相位中心关系 $\\phi=2\\pi d\\sin\\theta/\\lambda$ 给出 $\\sigma_\\theta\\simeq\\lambda/(2\\pi d\\cos\\theta\\sqrt{\\gamma_\\phi})$。正式理论还由生产 detection 的数值 Jacobian 和实测误差验证，不能用理想式取代。\n\n" + table(["命令波位", "理想 0.2°相位 SCNR", "生产 Jacobian 参考", "来源"], theory_rows) + "\n" + table(["命令波位", "输入档", "RMSE (°)", "bootstrap UCB95", "匹配数"], angle_rows),
        "# 5. 目标级与三屏选二航迹检测概率\n\n目标级成功必须是完整生产结果与 truth 一对一匹配。三周期单屏概率为 $p_1,p_2,p_3$ 时，独立两命中理论为 $p_1p_2+p_1p_3+p_2p_3-2p_1p_2p_3$；仅在同分布时才简化为 $3p^2-2p^3$。同时报告实际 TrackManager 协议有效输出。目标/航迹保守判据均结合 Wilson LCB 与 seed-cluster bootstrap LCB。\n\n" + table(["命令波位", "配对输出 SCNR", "目标 Pd", "目标 Wilson LCB", "TrackManager Pd", "独立两命中公式", "目标帧数"], pd_rows),
        "# 6. 联合门限与工程建议\n\n点估计为各约束首次满足的离散正式样本的最大值；95% 保守门限使用角度 RMSE bootstrap UCB 与 Pd LCB。不得跨越未采样区间插值或外推；若交点附近网格不足，必须执行 0.5 dB refinement。工程裕量应在保守门限基础上结合目标模型、环境和设备状态另行确定。\n",
        "# 7. 图与复现证据\n\n" + figure_text + "\n\n每个 batch 的 config、命令、SHA、日志、TrackManager 审计与 CSV 指标均由 manifest 留存；raw BIN 只在对应 seed 的审计成功后释放。",
    ]
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    if not args.skip_pdf:
        pandoc, xelatex = shutil.which("pandoc"), shutil.which("xelatex")
        if not pandoc or not xelatex:
            raise SystemExit("Markdown 已生成，但缺少 pandoc 或 xelatex，不能生成 PDF")
        result = subprocess.run([pandoc, str(document), "--pdf-engine", xelatex, "-o", str(pdf)], cwd=ROOT, check=False)
        if result.returncode:
            raise RuntimeError(f"Pandoc PDF 生成失败：{pdf}")
    print(f"[PASS] 已生成最终报告：{document}" + ("" if args.skip_pdf else f" 与 {pdf}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
