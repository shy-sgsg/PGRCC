#!/usr/bin/env python3
"""绘制正式交付要求的四张 output-SCNR 主图及可审计 PDF。"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import numpy as np

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti_scnr_mc_mplconfig")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
import sys
sys.path.insert(0, str(SCRIPT_DIR))
from scnr_eval_lib import (ensure_dir, fixed_pd, read_csv,
                           production_calibration_curve_rows,
                           remap_target_theory_rows)  # noqa: E402


COLORS = {0.0: "#1f77b4", 30.0: "#d98c00", 45.0: "#8c564b"}
ANGLE_LABELS = {0.0: "0°中心", 30.0: "30°中心", 45.0: "45°中心"}
_CJK_FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if _CJK_FONT.is_file():
    font_manager.fontManager.addfont(str(_CJK_FONT))
    plt.rcParams["font.family"] = "Noto Sans CJK JP"
plt.rcParams["axes.unicode_minus"] = False


def number(value: object) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def token(angle: float) -> str:
    return f"angle_{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    return read_csv(path)


def sorted_xy(rows: list[dict[str, str]], x_field: str, y_field: str) -> tuple[np.ndarray, np.ndarray]:
    values = [(number(row.get(x_field)), number(row.get(y_field))) for row in rows]
    values = [(x, y) for x, y in values if math.isfinite(x) and math.isfinite(y)]
    values.sort()
    return (np.asarray([x for x, _ in values], dtype=float),
            np.asarray([y for _, y in values], dtype=float))


def style(ax: plt.Axes, title: str, ylabel: str, ylim: tuple[float, float] | None = None) -> None:
    ax.set_title(title)
    ax.set_xlabel("输出 SCNR（dB；固定 S-only 支撑 / C+N-only P0）")
    ax.set_ylabel(ylabel)
    ax.grid(True, color="#d9dde3", alpha=0.7, linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if ylim:
        ax.set_ylim(*ylim)


def mc_line(ax: plt.Axes, rows: list[dict[str, str]], angle: float,
            y_field: str, low_field: str | None = None, high_field: str | None = None,
            label: str | None = None) -> None:
    rows = sorted(rows, key=lambda row: number(row.get("output_scnr_db", row.get("desired_scnr_out_db"))))
    # Formal x-axis is measured fixed-support output SCNR.  Desired SCNR is
    # retained as a control/audit field and is only a fallback for old smoke CSVs.
    x = np.asarray([number(row.get("output_scnr_db", row.get("desired_scnr_out_db"))) for row in rows])
    y = np.asarray([number(row.get(y_field)) for row in rows])
    valid = np.isfinite(x) & np.isfinite(y)
    if not np.any(valid):
        return
    color = COLORS.get(angle, "#333333")
    ax.plot(x[valid], y[valid], "o-", color=color, markerfacecolor="white",
            markeredgewidth=1.2, label=label or f"MC {ANGLE_LABELS.get(angle, f'{angle:g}°')}")
    if low_field and high_field:
        lo = np.asarray([number(row.get(low_field)) for row in rows])
        hi = np.asarray([number(row.get(high_field)) for row in rows])
        band = valid & np.isfinite(lo) & np.isfinite(hi)
        if np.any(band):
            ax.fill_between(x[band], lo[band], hi[band], color=color, alpha=0.12, linewidth=0)


def theory_go(ax: plt.Axes, run_dir: Path, angle: float) -> None:
    roots = [run_dir / "runs"]
    manifest_path = run_dir / "run_manifest.json"
    if manifest_path.is_file():
        try:
            import json
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            roots.extend(Path(str(chunk)) / "runs" for chunk in manifest.get("chunks", []))
        except (OSError, ValueError, TypeError):
            pass
    rows: list[dict[str, str]] = []
    for root in roots:
        path = root / token(angle) / token(angle) / "go_theory_vs_mc.csv"
        rows = load_rows(path)
        if rows:
            break
    x, y = sorted_xy(rows, "output_scnr_db", "unit_pd_empirical_n_only")
    if x.size:
        ax.plot(x, y, "--", color=COLORS.get(angle, "#555555"), alpha=0.8,
                label=f"GO经验理论 {ANGLE_LABELS.get(angle, f'{angle:g}°')}")


def center_cut_mapping(angle: float) -> tuple[float, float] | None:
    """Load the independent center-CUT to fixed-support SCNR mapping."""
    path = ROOT / "outputs/gmti_scnr_eval/angle_theory" / f"angle_theory_{int(round(angle))}deg.csv"
    rows = load_rows(path)
    for row in rows:
        slope = number(row.get("center_cut_mapping_slope_db_per_db"))
        intercept = number(row.get("center_cut_mapping_intercept_db"))
        if math.isfinite(slope) and math.isfinite(intercept):
            return float(slope), float(intercept)
    return None


def fixed_threshold_theory(ax: plt.Axes, x_values: np.ndarray, angle: float | None = None) -> None:
    """Add the textbook fixed-threshold reference on a clearly defined axis.

    The textbook formula uses the *single CUT* SCNR.  The formal plots use the
    fixed 3x3 support SCNR, so an independent center-CUT mapping is required
    before evaluating the formula.  When ``angle`` is omitted, retain the old
    support-axis curve only as an explicitly labelled diagnostic.
    """
    if x_values.size == 0:
        return
    x = np.linspace(float(np.nanmin(x_values)), float(np.nanmax(x_values)), 300)
    label = "教材固定门限理论（把3×3总SCNR当CUT，仅诊断）"
    color = "#555555"
    if angle is not None:
        mapping = center_cut_mapping(angle)
        if mapping is not None:
            slope, intercept = mapping
            center_cut_x = slope * x + intercept
            y = fixed_pd(10.0 ** (center_cut_x / 10.0), 1.0e-6)
            label = f"教材固定门限理论（中心CUT映射） {ANGLE_LABELS.get(angle, f'{angle:g}°')}"
            color = COLORS.get(angle, color)
            ax.plot(x, y, color=color, linestyle="-.", linewidth=1.5, alpha=0.9, label=label)
            return
    y = fixed_pd(10.0 ** (x / 10.0), 1.0e-6)
    ax.plot(x, y, color=color, linestyle="-.", linewidth=1.2, alpha=0.75, label=label)


def target_theory(ax: plt.Axes, target_dir: Path, angle: float,
                  field: str, label_suffix: str = "") -> tuple[np.ndarray, np.ndarray]:
    group = {0.0: "0deg_center", 30.0: "30deg_center", 45.0: "45deg_center"}.get(angle)
    rows = [row for row in load_rows(target_dir / "target_pd_minpoints_theory.csv")
            if row.get("angle_group") == group and int(round(number(row.get("min_points")))) == 6]
    rows = remap_target_theory_rows(rows, target_dir, angle)
    x, y = sorted_xy(rows, "output_scnr_db", field)
    if x.size:
        ax.plot(x, y, "--", color=COLORS.get(angle, "#555555"), alpha=0.8,
                label=f"min_points=6 PB基线（3×3轴映射） {ANGLE_LABELS.get(angle, f'{angle:g}°')}{label_suffix}")
    return x, y


def production_model_line(ax: plt.Axes, run_dir: Path, angle: float,
                          field: str, label: str) -> None:
    """Overlay the independent-seed production funnel calibration."""
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        calibration_dir = manifest.get("calibration_dir")
    except (OSError, ValueError, TypeError):
        return
    if not calibration_dir:
        return
    rows = production_calibration_curve_rows(Path(str(calibration_dir)), angle)
    x, y = sorted_xy(rows, "output_scnr_db", field)
    if x.size:
        ax.plot(x, y, ":", color=COLORS.get(angle, "#555555"), alpha=0.95,
                linewidth=1.8, label=f"{label} {ANGLE_LABELS.get(angle, f'{angle:g}°')}")


def angle_theory(ax: plt.Axes, run_dir: Path, angle: float) -> None:
    path = ROOT / "outputs/gmti_scnr_eval/angle_theory" / f"angle_theory_{int(round(angle))}deg.csv"
    rows = load_rows(path)
    # ``03_angle_theory.py`` now stores the inverse centre-CUT calibration
    # explicitly.  The phase model uses centre-CUT SCNR, so the mapped x value
    # is (phase_scnr - centre_intercept) / centre_slope.  This is different
    # from the target-amplitude-to-support transfer used by the MC controller.
    x, y = sorted_xy(rows, "output_scnr_db_mapped", "production_jacobian_rmse_theta_deg")
    label = "测角理论（中心 CUT→固定支撑 output SCNR 映射）"
    if not x.size:
        # Legacy archives predate the explicit centre-CUT mapping.  Preserve
        # their plotability but label the fallback honestly.
        x, y = sorted_xy(rows, "scnr_phase_eff_db", "production_jacobian_rmse_theta_deg")
        label = "测角理论（旧 phase-effective 轴，未映射）"
    if x.size:
        ax.plot(x, y, "--", color=COLORS.get(angle, "#555555"), alpha=0.8,
                label=f"{label} {ANGLE_LABELS.get(angle, f'{angle:g}°')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--metrics-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--target-theory-dir", type=Path,
                        default=ROOT / "docs/GMTI_输出SCNR_目标级minpoints理论_20260826")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    metrics_dir = (args.metrics_dir or run_dir).resolve()
    out = ensure_dir((args.output_dir or run_dir / "final_four_curves").resolve())
    summary = load_rows(metrics_dir / "mc_curve_summary.csv")
    angles = sorted({number(row.get("angle_deg")) for row in summary if math.isfinite(number(row.get("angle_deg")))})
    if not angles:
        raise SystemExit("mc_curve_summary.csv 没有可绘制数据")
    by_angle = {angle: [row for row in summary if abs(number(row.get("angle_deg")) - angle) < 1e-6]
                for angle in angles}
    plt.rcParams.update({"font.size": 10, "axes.titlesize": 12, "legend.fontsize": 8})

    # A: angle RMSE.  The phase/Jacobian model is now independently mapped to
    # the same fixed-support output-SCNR axis as the MC points.
    fig, ax = plt.subplots(figsize=(8.0, 5.0))
    for angle in angles:
        mc_line(ax, by_angle[angle], angle, "angle_rmse_deg", label=f"MC {ANGLE_LABELS.get(angle, f'{angle:g}°')}")
        production_model_line(ax, run_dir, angle, "angle_rmse_deg", "生产测角模型（独立校准）")
        angle_theory(ax, run_dir, angle)
    ax.axhline(0.2, color="#555555", linestyle=":", label="RMSE=0.2°")
    style(ax, "A. 输出 SCNR—测角 RMSE（仅 final target hit）", "角度 RMSE（°）")
    ax.legend(ncol=2); fig.tight_layout(); fig.savefig(out / "figure_A_angle_rmse.png", dpi=180); plt.close(fig)

    # B: unit Pd, including the production GO empirical integral from each run.
    fig, ax = plt.subplots(figsize=(8.0, 5.0))
    all_x = np.asarray([number(row.get("output_scnr_db", row.get("desired_scnr_out_db"))) for row in summary], dtype=float)
    all_x = all_x[np.isfinite(all_x)]
    # Keep the naive support-axis curve as a diagnostic to expose the old
    # textbook-vs-production axis mismatch; the angle-specific mapped curves
    # below are the physically comparable textbook references.
    fixed_threshold_theory(ax, all_x)
    for angle in angles:
        angle_x, _ = sorted_xy(by_angle[angle], "output_scnr_db", "unit_pd")
        fixed_threshold_theory(ax, angle_x, angle)
        mc_line(ax, by_angle[angle], angle, "unit_pd", "unit_wilson_low", "unit_wilson_high")
        theory_go(ax, run_dir, angle)
    ax.axhline(0.9, color="#555555", linestyle=":", label="Pd=0.9")
    style(ax, "B. 输出 SCNR—单元级 GO Pd", "unit Pd", (0.0, 1.03)); ax.legend(ncol=2)
    fig.tight_layout(); fig.savefig(out / "figure_B_unit_pd.png", dpi=180); plt.close(fig)

    # C: target Pd, with section-7 min_points=6 PB baseline.
    fig, ax = plt.subplots(figsize=(8.0, 5.0))
    for angle in angles:
        mc_line(ax, by_angle[angle], angle, "target_pd", "target_wilson_low", "target_wilson_high")
        production_model_line(ax, run_dir, angle, "target_pd", "生产目标漏斗模型（独立校准）")
        target_theory(ax, args.target_theory_dir.resolve(), angle, "pd_target_poisson_binomial_profile")
    ax.axhline(0.9, color="#555555", linestyle=":", label="Pd=0.9")
    style(ax, "C. 输出 SCNR—目标级最终 Pd", "target Pd", (0.0, 1.03)); ax.legend(ncol=2)
    fig.tight_layout(); fig.savefig(out / "figure_C_target_pd.png", dpi=180); plt.close(fig)

    # D: actual 2-of-3 and the independent-screen baseline using the target
    # theory curve.  The actual MC curve is the primary line.
    fig, ax = plt.subplots(figsize=(8.0, 5.0))
    for angle in angles:
        mc_line(ax, by_angle[angle], angle, "track_pd", "track_wilson_low", "track_wilson_high")
        production_model_line(ax, run_dir, angle, "track_pd", "生产三屏2-of-3模型（独立校准）")
        tx, ty = target_theory(ax, args.target_theory_dir.resolve(), angle, "pd_target_poisson_binomial_profile", " → 3p²−2p³")
        if tx.size:
            ax.lines[-1].set_label(f"独立屏幕 baseline {ANGLE_LABELS.get(angle, f'{angle:g}°')}")
            ax.lines[-1].set_ydata(3.0 * ty * ty - 2.0 * ty * ty * ty)
    ax.axhline(0.9, color="#555555", linestyle=":", label="Pd=0.9")
    style(ax, "D. 输出 SCNR—航迹级三屏选二 Pd", "track Pd（2-of-3）", (0.0, 1.03)); ax.legend(ncol=2)
    fig.tight_layout(); fig.savefig(out / "figure_D_track_pd.png", dpi=180); plt.close(fig)

    from matplotlib.backends.backend_pdf import PdfPages
    with PdfPages(out / "final_four_curves.pdf") as pdf:
        for name in ("figure_A_angle_rmse.png", "figure_B_unit_pd.png",
                     "figure_C_target_pd.png", "figure_D_track_pd.png"):
            image = plt.imread(out / name)
            fig, ax = plt.subplots(figsize=(8.0, 5.0)); ax.imshow(image); ax.axis("off")
            pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)
    (out / "final_four_curves.md").write_text(
        "# 输出 SCNR 四张主图\n\n"
        "横轴统一为固定 S-only 支撑功率除以 C+N-only ensemble P0 得到的 output SCNR；Stage2 `target_snr_db` 只用于反解控制量。\n\n"
        "- Figure A：角度 RMSE 只在 `final_target_hit=1` 样本上统计，同时保留有效数/总目标数；点线为独立 calibration seed 的生产测角模型，虚线为已映射到固定支撑 output-SCNR 轴的中心 CUT Jacobian 理论。\n"
        "- Figure B：unit Pd 是生产 GO exact CUT 事件，虚线为本次 N-only 训练窗经验积分。\n"
        "  彩色点划线是把教材固定门限公式先映射到中心 CUT SCNR 后的可比参照；灰色点划线特意保留‘把 3×3 总 SCNR 直接当 CUT SCNR’的旧口径，仅用于解释为什么旧图看起来比教材理论好很多。\n"
        "- Figure C：主曲线是最终 truth target Pd，点线是独立 calibration seed 的真实生产漏斗模型；虚线是实测支撑形状下 min_points=6 的 Poisson-binomial baseline，已按 kappa/beta 映射到固定 3×3 output-SCNR 轴，不是 15 单元总 SCNR。\n"
        "- Figure D：主曲线是三屏 truth 事件的实际 2-of-3，点线是独立 calibration seed 的生产三屏模型，虚线是 `3p²−2p³` 独立屏幕基线；`mc_transition_stats.csv` 给出相关性修正。\n",
        encoding="utf-8")
    print(f"[PASS] 四张 output-SCNR 主图已生成：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
