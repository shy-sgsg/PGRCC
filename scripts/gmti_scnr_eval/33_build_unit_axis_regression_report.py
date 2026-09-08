#!/usr/bin/env python3
"""构建单元级 GO 理论/实测闭环的坐标映射回归报告。"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
import sys

sys.path.insert(0, str(SCRIPT_DIR))
from scnr_eval_lib import read_csv, wilson_interval, write_csv  # noqa: E402


def finite(value: object) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def fmt(value: object, digits: int = 3) -> str:
    number = finite(value)
    return f"{number:.{digits}f}" if math.isfinite(number) else "—"


def table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---:" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(str(item) for item in row) + " |" for row in rows)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-pdf", type=Path, required=True)
    parser.add_argument("--output-html", type=Path, required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    curve_path = run_dir / "standard_mc_curve_summary.csv"
    theory_path = run_dir / "go_theory_vs_mc.csv"
    audit_path = run_dir / "information_leakage_audit.json"
    if not curve_path.is_file() or not theory_path.is_file() or not audit_path.is_file():
        raise SystemExit("缺少单元回归汇总 CSV 或信息泄露审计 JSON")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if (audit.get("status") != "pass" or int(audit.get("truth_active_detector_count", 0)) != 0
            or int(audit.get("diagnostic_truth_path_count", 0)) != 0):
        raise SystemExit("单元回归未通过严格信息泄露审计")
    curve = list(read_csv(curve_path))
    theory = {int(round(finite(row.get("level_index")))): row for row in read_csv(theory_path)}
    curve.sort(key=lambda row: int(round(finite(row.get("level_index")))))
    if len(curve) != 10 or len(theory) != 10:
        raise SystemExit(f"期望 10 个 SCNR 档位，实际 curve={len(curve)}, theory={len(theory)}")

    out = run_dir / "unit_axis_regression_report"
    out.mkdir(parents=True, exist_ok=True)
    plot_rows = []
    detail_rows = []
    for row in curve:
        level = int(round(finite(row.get("level_index"))))
        tr = theory[level]
        measured = finite(row.get("unit_pd"))
        n = int(round(finite(row.get("unit_trials"))))
        hits = int(round(finite(row.get("unit_hits"))))
        low, high = wilson_interval(hits, n)
        resolution_hits = int(round(finite(row.get("unit_resolution_hits", row.get("unit_hits")))))
        resolution_trials = int(round(finite(row.get("unit_resolution_trials", row.get("unit_trials")))))
        resolution = finite(row.get("unit_resolution_pd", row.get("unit_pd")))
        resolution_low, resolution_high = wilson_interval(resolution_hits, resolution_trials)
        plot_rows.append({
            "output_scnr_db": finite(row.get("output_scnr_db")),
            "measured_pd": measured, "low": low, "high": high,
            "resolution_pd": resolution, "resolution_low": resolution_low,
            "resolution_high": resolution_high,
            "iid_pd": finite(tr.get("unit_pd_iid_go")),
            "empirical_pd": finite(tr.get("unit_pd_empirical_n_only")),
        })
        detail_rows.append({
            "level_index": level,
            "output_scnr_db": finite(row.get("output_scnr_db")),
            "unit_cut_scnr_db": finite(tr.get("unit_cut_scnr_db")),
            "unit_hits": hits, "unit_trials": n, "unit_pd": measured,
            "wilson_low": low, "wilson_high": high,
            "unit_resolution_hits": resolution_hits, "unit_resolution_trials": resolution_trials,
            "unit_resolution_pd": resolution,
            "unit_resolution_wilson_low": resolution_low,
            "unit_resolution_wilson_high": resolution_high,
            "unit_pd_iid_go": finite(tr.get("unit_pd_iid_go")),
            "unit_pd_empirical_n_only": finite(tr.get("unit_pd_empirical_n_only")),
            "measured_minus_empirical": measured - finite(tr.get("unit_pd_empirical_n_only")),
        })
    write_csv(out / "unit_axis_regression_detail.csv", detail_rows)

    x = np.asarray([row["output_scnr_db"] for row in plot_rows], dtype=float)
    measured = np.asarray([row["measured_pd"] for row in plot_rows], dtype=float)
    low = np.asarray([row["low"] for row in plot_rows], dtype=float)
    high = np.asarray([row["high"] for row in plot_rows], dtype=float)
    iid = np.asarray([row["iid_pd"] for row in plot_rows], dtype=float)
    empirical = np.asarray([row["empirical_pd"] for row in plot_rows], dtype=float)
    order = np.argsort(x)
    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    ax.plot(x[order], measured[order], "o-", color="#1f77b4", markerfacecolor="white",
            label="实测 unit Pd（6 screens/档）")
    ax.fill_between(x[order], low[order], high[order], color="#1f77b4", alpha=.16,
                    label="Wilson 95% 区间")
    ax.plot(x[order], iid[order], "--", color="#d62728", label="IID GO 理论")
    ax.plot(x[order], empirical[order], "-.", color="#2ca02c", label="N-only 经验积分理论")
    ax.set_xlabel("输出 SCNR（dB；固定 3×3 支撑）")
    ax.set_ylabel("单元级 GO Pd")
    ax.set_ylim(0, 1.04)
    ax.grid(True, alpha=.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "output_scnr_vs_unit_pd_axis_fixed.png", dpi=220)
    fig.savefig(out / "output_scnr_vs_unit_pd_axis_fixed.pdf")
    plt.close(fig)

    # The operational production event is a bounded truth-resolution event,
    # not the exact single FFT CUT.  Keep it as a separate figure: comparing
    # it with the single-CUT Q1 curve would mix two different events.
    resolution = np.asarray([row["resolution_pd"] for row in plot_rows], dtype=float)
    resolution_low = np.asarray([row["resolution_low"] for row in plot_rows], dtype=float)
    resolution_high = np.asarray([row["resolution_high"] for row in plot_rows], dtype=float)
    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    ax.plot(x[order], resolution[order], "o-", color="#9467bd", markerfacecolor="white",
            label="实测 unit Pd（±2 bin 分辨率事件）")
    ax.fill_between(x[order], resolution_low[order], resolution_high[order], color="#9467bd", alpha=.16,
                    label="Wilson 95% 区间")
    ax.set_xlabel("输出 SCNR（dB；固定 3×3 支撑）")
    ax.set_ylabel("生产分辨率单元 GO Pd")
    ax.set_ylim(0, 1.04)
    ax.grid(True, alpha=.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "output_scnr_vs_unit_resolution_pd_axis_fixed.png", dpi=220)
    fig.savefig(out / "output_scnr_vs_unit_resolution_pd_axis_fixed.pdf")
    plt.close(fig)

    diff = np.abs(measured - empirical)
    within = [(lo <= emp <= hi) for lo, hi, emp in zip(low, high, empirical)]
    max_diff = float(np.nanmax(diff))
    within_count = int(sum(within))
    md = out / "GMTI_单元级GO理论坐标映射回归报告_20260828.md"
    md_text = """# GMTI 单元级 GO 理论—实测坐标映射回归报告

## 1. 结论

本回归只验证 0°、无面积杂波（thermal noise only）的真实 CUDA 生产链。修复前，严格无先验模式把 N-only 的 Doppler support 行号直接复用给 S-only/S+N；但三条链路的运行时 Doppler 轴中心会因噪声实现不同而变化，导致评分单元不是同一个物理 CUT，unit Pd 因而出现“随输出 SCNR 乱跳”。修复后，三条链路都使用自身运行时元数据映射同一个物理 support 偏移；该 support 只用于离线评分，不写入 detector 配置。

"""
    md_text += (f"在本次 10 档、每档 2 组三屏（每档 6 个单元样本、共 60 periods）回归中，"
                f"实测 unit Pd 与真实 N-only 经验积分理论的 95% Wilson 区间重合 **{within_count}/10** 档，"
                f"最大绝对 Pd 差为 **{max_diff:.3f}**。精确 CUT 事件用于和单元 Q1 理论比较；"
                "生产实际事件另按真值周围 ±2 Doppler/±2 range 分辨单元单独统计，不能把后者直接和单 CUT 理论重合要求混为一谈。"
                "样本量仅用于回归验证，不作为最终门限。\n\n")
    md_text += r"""## 2. 理论公式和参数

GO 检测事件为

$$H=1\{P_{CUT}>\alpha M\},\qquad M=\max(L,R,T,B).$$

在 IID Gaussian、无目标泄漏模型下，固定单元有效信噪比为 $\gamma=P_S/P_0$，单元检测概率为

$$P_d(\gamma)=E_M\left[Q_1\left(\sqrt{2\gamma},\sqrt{2\alpha M}\right)\right].$$

本链路的参数来自生产 XML/GO meta：$P_{fa}=10^{-6}$、guard=4、background=16，四方向每块 16×16，训练单元总数 $N=544$，生产校准的 $\alpha=13.4495103$；理论 Monte Carlo 样本数为 20,000。实测主横轴是

$$SCNR_{out}=10\log_{10}\frac{\overline{P_{S+N,3\times3}}-\overline{P_{N,3\times3}}}{\overline{P_{N,3\times3}}},$$

而理论计算先用同一 CUT 的实测 $SCNR_{CUT}$：$\gamma=10^{SCNR_{CUT}/10}$，避免把 3×3 支撑总能量误当作一个单元能量。

## 3. 修复前后的评分坐标

物理 Doppler 频率 $f_D$ 到当前生产轴的单元行号为

$$r_{run}=\left\lfloor\frac{f_D-(f_c-PRF/2)}{\Delta f}+0.5\right\rfloor\bmod N_D+\Delta r_{ref}.$$

旧逻辑使用 $r_N$（N-only 运行时轴）同时读取 S-only 和 S+N。严格模式不冻结 $f_c$，所以 $r_N\ne r_{S+N}$ 时，检测图、SCNR 和 truth-match 分别指向不同单元。新逻辑在每条链路完成后，用该链路自身观测的 $f_c,\Delta f$ 求 $r_{run}$；reference 只提供固定的局部主瓣偏移 $\Delta r_{ref},\Delta c_{ref}$，不提供目标功率或检测先验。

## 4. 曲线与逐档数值

![unit Pd axis fixed](output_scnr_vs_unit_pd_axis_fixed.png)

上图的蓝色圆点是生产 GO hit map 在固定物理 CUT 的实测计数，浅蓝带是二项 Wilson 95% 区间；红色虚线是 IID Gaussian 理论，绿色点划线是用 N-only 实测 $M/CUT_{background}$ 做的经验积分理论。绿色线更贴近真实非 IID 背景，但不能利用 S+N 结果反拟合。

![unit resolution Pd axis fixed](output_scnr_vs_unit_resolution_pd_axis_fixed.png)

上图的紫线是生产分辨率事件：只要真值周围 Doppler/range 各 ±2 bin 内有任一 GO hit 即记为 1。它解释了“检测峰在相邻 FFT 行但精确 CUT 为 0”的情况；该事件包含量化/主瓣位移，不等于单一 noncentral-$\chi^2$ CUT，因此本报告不把它硬套成红色单 CUT 理论。

"""
    md_text += table(
            ["档位", "output SCNR(dB)", "CUT SCNR(dB)", "精确 hits/n", "精确 Pd", "精确 Wilson 95%", "分辨率 hits/n", "分辨率 Pd", "N-only经验", "精确差值"],
            [[row["level_index"], fmt(row["output_scnr_db"], 2), fmt(row["unit_cut_scnr_db"], 2),
              f"{row['unit_hits']}/{row['unit_trials']}", fmt(row["unit_pd"]),
              f"[{fmt(row['wilson_low'])},{fmt(row['wilson_high'])}]",
              f"{row['unit_resolution_hits']}/{row['unit_resolution_trials']}", fmt(row["unit_resolution_pd"]),
              fmt(row["unit_pd_empirical_n_only"]), fmt(row["measured_minus_empirical"])]
             for row in detail_rows])
    md_text += "\n\n完整 CSV：`unit_axis_regression_detail.csv`。`unit_pd`/`unit_hits` 是精确 CUT；`unit_resolution_pd` 是生产 ±2 bin 分辨率事件。\n\n## 5. 信息泄露检查和实验方法\n\n"
    md_text += table(["检查项", "结果", "说明"], [
            ["生产 pipeline", f"{audit.get('pipeline_count', 0)} 个，pass", "N-only、reference S-only、S-only calibration、S+N"],
            ["在线 truth 路径", audit.get("truth_active_detector_count", 0), "检测器 XML 不含 truth 路径"],
            ["诊断 truth 路径", audit.get("diagnostic_truth_path_count", 0), "truth 只用于离线 support/评分"],
            ["NMS truth tie-break", "已移除", "writeresults.cpp 只用观测功率/波束/行列确定性规则"],
            ["Monte Carlo", "10 档×2组三屏×3屏=60 periods", "每档 unit 分母 n=6；理论样本 20,000"],
        ])
    md_text += ("\n\n没有把实测 unit Pd、S+N 输出或 truth 参数写回生产 detector；新的运行时 support 表在 detector 退出后才生成。"
                "raw BIN 已在 GO/TrackManager/truth-match 审计后清理。\n\n## 6. 复现\n\n"
                "`python3 scripts/gmti_scnr_eval/16_run_standard_output_snr_mc.py --output-root outputs/gmti_scnr_eval/axis_mapping_low_regression_20260828 --angle 0 --seed 20260829 --input-snr-grid=-8,-6,-4,-2,0,2,4,6,8,10 --trials-per-level 2 --strict-online-no-prior`\n"
                "\n报告生成：`python3 scripts/gmti_scnr_eval/33_build_unit_axis_regression_report.py ...`。\n")
    md.write_text(md_text, encoding="utf-8")

    pdf = args.output_pdf.resolve()
    html = args.output_html.resolve()
    pdf.parent.mkdir(parents=True, exist_ok=True)
    html.parent.mkdir(parents=True, exist_ok=True)
    title = "GMTI 单元级 GO 理论坐标映射回归报告"
    pandoc = shutil.which("pandoc")
    xelatex = shutil.which("xelatex")
    if pandoc and xelatex:
        result = subprocess.run([pandoc, str(md), "--resource-path", str(out), "--pdf-engine", xelatex,
                                 "-V", "CJKmainfont=Noto Sans CJK SC", "--metadata", f"title={title}",
                                 "-o", str(pdf)], cwd=ROOT, check=False)
        if result.returncode:
            raise SystemExit(f"pandoc PDF 失败，退出码={result.returncode}")
    if pandoc:
        result = subprocess.run([pandoc, str(md), "--standalone", "--resource-path", str(out), "--self-contained",
                                 "--mathml", "--metadata", f"title={title}", "-o", str(html)], cwd=ROOT, check=False)
        if result.returncode:
            raise SystemExit(f"pandoc HTML 失败，退出码={result.returncode}")
    manifest = {
        "status": "pass", "run_dir": str(run_dir), "audit": str(audit_path),
        "levels": len(curve), "unit_trials_per_level": 6,
        "theory_samples": 20000, "wilson_overlap_count": within_count,
        "max_abs_measured_minus_empirical": max_diff,
        "coordinate_policy": "per-run observed Doppler axis; fixed physical support offset; exact CUT and +/-2-bin resolution events kept separately",
        "unit_pd_semantics": "exact projected physical truth CUT",
        "unit_resolution_pd_semantics": "any GO hit within +/-2 Doppler and +/-2 range bins of physical truth before clustering",
    }
    (out / "unit_axis_regression_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 单元回归报告 Markdown: {md}")
    print(f"[PASS] 单元回归报告 PDF: {pdf}")
    print(f"[PASS] 单元回归报告 HTML: {html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
