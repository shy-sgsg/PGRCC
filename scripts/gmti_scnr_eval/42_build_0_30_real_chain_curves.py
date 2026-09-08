#!/usr/bin/env python3
"""Build the focused 0°/30° real-chain output-SCNR curve report.

The source roots are retained, audited runs of ``16_run_standard_output_snr_mc``.
This script deliberately does not run the detector, does not open raw BIN/F32
files, and never fits a theoretical curve to S+N hit counts.  It produces a
machine-readable table, separate figures for unit/target/track/angle metrics,
and Markdown/PDF/HTML reports for the two requested center wave positions.

The retained batch is the controlled thermal-noise baseline (area clutter was
disabled in that batch to make the output-SCNR axis stable).  Target theory is
the section-7 3x5-support Poisson-binomial point-count baseline, using a
separately retained S-only/C+N-only support profile only as an offline shape
calibration.  It is not the production cluster event; measured target/track
points always come from the real clustering, target matching, and three-screen
truth event in the retained CSVs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager
import matplotlib.pyplot as plt
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
import sys

sys.path.insert(0, str(SCRIPT_DIR))
from scnr_eval_lib import (GoCfarGeometry, go_conditional_pd,
                           go_training_noise_samples, read_csv, write_csv)


ANGLES = (0.0, 30.0)
MINPOINTS = (2, 3, 6)
ANGLE_LABEL = {0.0: "0°中心", 30.0: "30°中心"}
ANGLE_GROUP = {0.0: "0deg_center", 30.0: "30deg_center"}
ANGLE_COLOR = {0.0: "#1f77b4", 30.0: "#d98c00"}
MIN_COLOR = {2: "#1f77b4", 3: "#2ca02c", 6: "#d62728"}
FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if FONT.is_file():
    font_manager.fontManager.addfont(str(FONT))
    plt.rcParams["font.family"] = "Noto Sans CJK JP"
plt.rcParams["axes.unicode_minus"] = False


def fnum(value: object, default: float = float("nan")) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def inum(value: object, default: int = 0) -> int:
    parsed = fnum(value)
    return int(round(parsed)) if math.isfinite(parsed) else default


def fmt(value: object, digits: int = 3) -> str:
    parsed = fnum(value)
    return f"{parsed:.{digits}f}" if math.isfinite(parsed) else "—"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_load(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def wilson(hits: int, trials: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if trials <= 0:
        return float("nan"), float("nan")
    p = hits / trials
    den = 1.0 + z * z / trials
    centre = (p + z * z / (2.0 * trials)) / den
    half = z * math.sqrt(max(0.0, p * (1.0 - p) / trials + z * z / (4.0 * trials * trials))) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def md_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---:" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |"
                 for row in rows)
    return "\n".join(lines)


def root_for(angle: float, minimum: int) -> Path:
    token = "p0p000" if angle == 0.0 else "p30p000"
    return ROOT / "outputs/gmti_scnr_eval/target_minpoints_corrected_20260828" / f"min{minimum}_formal" / "runs" / f"angle_{token}" / f"angle_{token}"


def load_curve(angle: float, minimum: int) -> tuple[list[dict[str, str]], list[dict[str, str]], Path]:
    root = root_for(angle, minimum)
    curve_path = root / "standard_mc_curve_summary.csv"
    theory_path = root / "go_theory_vs_mc.csv"
    if not curve_path.is_file() or not theory_path.is_file():
        raise FileNotFoundError(f"缺少审计汇总: {curve_path} / {theory_path}")
    curve = read_csv(curve_path)
    theory = read_csv(theory_path)
    if len(curve) != 10 or len(theory) != 10:
        raise RuntimeError(f"{root}: 预期 10 个 SCNR 档位，得到 curve={len(curve)}, theory={len(theory)}")
    return curve, theory, root


def load_profile(angle: float, profile_path: Path) -> tuple[np.ndarray, np.ndarray, dict[str, str]]:
    rows = read_csv(profile_path)
    group = ANGLE_GROUP[angle]
    selected = [row for row in rows if row.get("angle_group") == group and row.get("position") == "center"]
    if len(selected) != 1:
        raise RuntimeError(f"目标支撑 profile 缺少唯一 {group}/center 行")
    row = selected[0]
    kappa = np.asarray([fnum(row.get(f"kappa_{index:02d}")) for index in range(15)], dtype=float)
    beta = np.asarray([fnum(row.get(f"beta_{index:02d}")) for index in range(15)], dtype=float)
    if not (np.all(np.isfinite(kappa)) and np.all(np.isfinite(beta)) and np.all(kappa >= 0.0) and np.all(beta > 0.0)):
        raise RuntimeError(f"{profile_path}: {group} 的 kappa/beta 非法")
    if float(kappa.sum()) <= 0.0 or not (kappa[7] > 0.0):
        raise RuntimeError(f"{profile_path}: {group} 的中心信号 profile 无效")
    # The output-SCNR axis in the retained MC is the fixed-support axis.  The
    # central physical CUT is used as the unit-theory anchor; normalize each
    # support-cell ratio to the central cell so p_7 is the GO unit curve.
    ratio = (kappa / beta) / (kappa[7] / beta[7])
    return ratio, kappa, row


def pb_tail(probabilities: np.ndarray, minimum: int) -> np.ndarray:
    """Poisson-binomial P(sum(H_i)>=minimum), rows are SCNR points."""
    p = np.asarray(probabilities, dtype=float)
    if p.ndim != 2 or p.shape[1] != 15:
        raise ValueError(f"期望 [grid,15] 概率矩阵，实际 {p.shape}")
    grid = p.shape[0]
    dp = np.zeros((grid, 16), dtype=float)
    dp[:, 0] = 1.0
    for index in range(15):
        q = np.clip(p[:, index], 0.0, 1.0)
        old = dp.copy()
        dp[:, 0] = old[:, 0] * (1.0 - q)
        dp[:, 1:index + 2] = (old[:, 1:index + 2] * (1.0 - q[:, None]) +
                               old[:, :index + 1] * q[:, None])
    return np.sum(dp[:, minimum:], axis=1)


def fit_cut_mapping(theory_rows: list[dict[str, str]]) -> tuple[float, float, float]:
    x = np.asarray([fnum(row.get("output_scnr_db")) for row in theory_rows], dtype=float)
    y = np.asarray([fnum(row.get("unit_cut_scnr_db")) for row in theory_rows], dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 2:
        raise RuntimeError("输出 SCNR 到物理 CUT 的映射样本不足")
    slope, intercept = np.polyfit(x[mask], y[mask], 1)
    pred = slope * x[mask] + intercept
    denom = float(np.sum((y[mask] - np.mean(y[mask])) ** 2))
    r2 = 1.0 - float(np.sum((y[mask] - pred) ** 2)) / denom if denom > 0.0 else float("nan")
    return float(slope), float(intercept), r2


def threshold_crossing(x: np.ndarray, y: np.ndarray, threshold: float = 0.9) -> float:
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = np.asarray(x)[valid], np.asarray(y)[valid]
    order = np.argsort(x)
    x, y = x[order], y[order]
    for index, value in enumerate(y):
        if value < threshold:
            continue
        if index == 0:
            return float(x[0])
        x0, x1, y0, y1 = float(x[index - 1]), float(x[index]), float(y[index - 1]), float(value)
        if y1 == y0:
            return x1
        return x0 + (threshold - y0) * (x1 - x0) / (y1 - y0)
    return float("nan")


def build_data(output_dir: Path, profile_path: Path, theory_samples: int) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    geometry = GoCfarGeometry(4, 16)
    # The production coefficient is frozen in every retained GO theory CSV.
    # Do not substitute the simple ring-count expression from
    # ``GoCfarGeometry.alpha`` here: that expression is 13.8753 for 1600
    # ring cells, whereas the runtime/shared-corner production calibration is
    # alpha=13.4495103.  Using the runtime metadata keeps this report on the
    # same CFAR implementation as the retained real-chain run.
    alpha_candidates: list[float] = []
    measured: list[dict[str, object]] = []
    dense: list[dict[str, object]] = []
    source_files: list[dict[str, object]] = []
    mapping_info: dict[float, tuple[float, float, float]] = {}
    profile_meta: dict[float, dict[str, object]] = {}

    # All min_points roots in this retained batch use the same ten controlled
    # levels, but load each independently so the audit remains explicit.
    cache: dict[tuple[float, int], tuple[list[dict[str, str]], list[dict[str, str]], Path]] = {}
    for angle in ANGLES:
        for minimum in MINPOINTS:
            curve, theory, root = load_curve(angle, minimum)
            cache[(angle, minimum)] = (curve, theory, root)
            alpha_candidates.extend(fnum(row.get("alpha")) for row in theory
                                    if math.isfinite(fnum(row.get("alpha"))))
            for filename in ("standard_mc_curve_summary.csv", "go_theory_vs_mc.csv", "manifest.json", "standard_mc_screen_metrics.csv"):
                path = root / filename
                if path.is_file():
                    source_files.append({"path": str(path.resolve()), "sha256": sha256(path), "size_bytes": path.stat().st_size})
        ratio, kappa, profile_row = load_profile(angle, profile_path)
        profile_meta[angle] = {"ratio": ratio.tolist(), "kappa": kappa.tolist(), "profile_row": profile_row}
        _, theory, _ = cache[(angle, 2)]
        mapping_info[angle] = fit_cut_mapping(theory)
    if not alpha_candidates:
        raise RuntimeError("留存 GO 理论表没有生产 alpha")
    alpha = float(np.median(np.asarray(alpha_candidates, dtype=float)))
    if not math.isfinite(alpha) or abs(alpha - 13.44951031977817) > 1e-5:
        raise RuntimeError(f"生产 alpha 不一致: {alpha}")
    noise = go_training_noise_samples(geometry, theory_samples, 20260831)

    # Dense theory grid covers the range of retained output SCNR values and
    # uses only the independently measured output->CUT power mapping.
    for angle in ANGLES:
        all_x = [fnum(row.get("output_scnr_db")) for row in cache[(angle, 2)][0]]
        x_grid = np.linspace(float(np.nanmin(all_x)) - 0.25, float(np.nanmax(all_x)) + 0.25, 241)
        slope, intercept, _ = mapping_info[angle]
        cut_db = slope * x_grid + intercept
        gamma_cut = 10.0 ** (cut_db / 10.0)
        ratio = np.asarray(profile_meta[angle]["ratio"], dtype=float)
        p_cells = np.column_stack([go_conditional_pd(gamma_cut * scale, alpha, noise) for scale in ratio])
        unit_iid = go_conditional_pd(gamma_cut, alpha, noise)
        for minimum in MINPOINTS:
            target_pb = pb_tail(p_cells, minimum)
            track_iid = 3.0 * target_pb ** 2 - 2.0 * target_pb ** 3
            coeff = 180.0 * 0.0182 / (2.0 * math.pi ** 2 * 0.17 * math.cos(math.radians(angle)))
            angle_rmse = coeff / np.sqrt(gamma_cut)
            for x_value, unit_value, target_value, track_value, angle_value in zip(x_grid, unit_iid, target_pb, track_iid, angle_rmse):
                dense.append({
                    "angle_deg": angle, "min_points": minimum,
                    "output_scnr_db": float(x_value),
                    "cut_equivalent_scnr_db": float(slope * x_value + intercept),
                    "unit_theory_iid_pd": float(unit_value),
                    "target_theory_pb_pd": float(target_value),
                    "track_theory_independent_pd": float(track_value),
                    "angle_theory_rmse_deg": float(angle_value),
                    "alpha": alpha, "support_cells": 15,
                    "theory_samples": theory_samples,
                    "target_theory": "GO-IID per-cell Marcum-Q + measured 3x5 kappa/beta PB tail",
                })

    # Add measured rows and attach theory at the exact retained SCNR points.
    for angle in ANGLES:
        slope, intercept, mapping_r2 = mapping_info[angle]
        ratio = np.asarray(profile_meta[angle]["ratio"], dtype=float)
        for minimum in MINPOINTS:
            curve, theory, root = cache[(angle, minimum)]
            theory_by_level = {inum(row.get("level_index")): row for row in theory}
            for raw in curve:
                level = inum(raw.get("level_index"))
                tr = theory_by_level.get(level, {})
                x = fnum(raw.get("output_scnr_db"))
                cut_db = fnum(tr.get("unit_cut_scnr_db"), slope * x + intercept)
                gamma_cut = 10.0 ** (cut_db / 10.0) if math.isfinite(cut_db) else float("nan")
                p_cells = np.column_stack([go_conditional_pd(np.asarray([gamma_cut * scale]), alpha, noise) for scale in ratio]) if math.isfinite(gamma_cut) else np.full((1, 15), np.nan)
                target_pb = float(pb_tail(p_cells, minimum)[0]) if math.isfinite(gamma_cut) else float("nan")
                track_pb = 3.0 * target_pb ** 2 - 2.0 * target_pb ** 3 if math.isfinite(target_pb) else float("nan")
                coeff = 180.0 * 0.0182 / (2.0 * math.pi ** 2 * 0.17 * math.cos(math.radians(angle)))
                angle_theory = coeff / math.sqrt(gamma_cut) if math.isfinite(gamma_cut) and gamma_cut > 0.0 else float("nan")
                unit_emp = fnum(tr.get("unit_pd_empirical_n_only"), fnum(tr.get("unit_pd")))
                unit_iid = fnum(tr.get("unit_pd_iid_go"))
                unit_exact = fnum(raw.get("unit_exact_pd"), fnum(raw.get("unit_pd")))
                unit_resolution = fnum(raw.get("unit_resolution_pd"))
                target_pd = fnum(raw.get("target_pd"))
                track_pd = fnum(raw.get("track_pd"))
                target_hits, target_trials = inum(raw.get("target_hits")), inum(raw.get("target_trials"))
                track_hits, track_trials = inum(raw.get("track_hits")), inum(raw.get("track_trials"))
                unit_hits, unit_trials = inum(raw.get("unit_exact_hits"), inum(raw.get("unit_hits"))), inum(raw.get("unit_exact_trials"), inum(raw.get("unit_trials")))
                target_low, target_high = wilson(target_hits, target_trials)
                track_low, track_high = wilson(track_hits, track_trials)
                unit_low, unit_high = wilson(unit_hits, unit_trials)
                measured.append({
                    "angle_deg": angle, "min_points": minimum, "level_index": level,
                    "output_scnr_db": x,
                    "requested_output_scnr_db": fnum(raw.get("requested_output_scnr_db")),
                    "cut_equivalent_scnr_db": cut_db,
                    "unit_exact_pd": unit_exact, "unit_resolution_pd": unit_resolution,
                    "unit_exact_hits": unit_hits, "unit_exact_trials": unit_trials,
                    "unit_wilson_low": fnum(raw.get("unit_wilson_low"), unit_low),
                    "unit_wilson_high": fnum(raw.get("unit_wilson_high"), unit_high),
                    "unit_theory_iid_pd": unit_iid,
                    "unit_theory_empirical_pd": unit_emp,
                    "target_pd": target_pd, "target_hits": target_hits, "target_trials": target_trials,
                    "target_wilson_low": fnum(raw.get("target_wilson_low"), target_low),
                    "target_wilson_high": fnum(raw.get("target_wilson_high"), target_high),
                    "target_theory_pb_pd": target_pb,
                    "track_pd": track_pd, "track_hits": track_hits, "track_trials": track_trials,
                    "track_wilson_low": fnum(raw.get("track_wilson_low"), track_low),
                    "track_wilson_high": fnum(raw.get("track_wilson_high"), track_high),
                    "track_theory_independent_pd": track_pb,
                    "angle_rmse_deg": fnum(raw.get("angle_rmse_deg")),
                    "angle_theory_rmse_deg": angle_theory,
                    "angle_matched": inum(raw.get("angle_matched")),
                    "mapping_slope_db_per_db": slope, "mapping_intercept_db": intercept,
                    "mapping_r2": mapping_r2, "alpha": alpha,
                    "source_curve": str((root / "standard_mc_curve_summary.csv").resolve()),
                    "source_theory": str((root / "go_theory_vs_mc.csv").resolve()),
                })

    fields = list(measured[0].keys()) if measured else []
    write_csv(output_dir / "real_chain_0_30_curves.csv", measured)
    write_csv(output_dir / "real_chain_0_30_theory_dense.csv", dense)
    return measured, dense, {
        "alpha": alpha, "pfa": 1e-6, "cfar_guard": 4, "cfar_background": 16,
        "theory_samples": theory_samples, "source_files": source_files,
        "mapping": {str(angle): {"slope_db_per_db": mapping_info[angle][0], "intercept_db": mapping_info[angle][1], "r2": mapping_info[angle][2]} for angle in ANGLES},
        "profiles": {str(angle): profile_meta[angle] for angle in ANGLES},
        "profile_source": str(profile_path.resolve()),
        "profile_source_sha256": sha256(profile_path),
        "strict_online_no_prior": True,
        "simulation_background": "thermal_noise_only_no_area_clutter",
        "output_axis": "10log10((mean_fixed_3x3_support(P_S+N)-mean_fixed_3x3_support(P_N))/ensemble_mean(3x3_support_P_N))",
        "unit_event": "exact physical truth CUT GO hit; resolution hit is retained as auxiliary",
        "target_event": "production joint hit mask + clustering + truth match, target_trials=15 per level",
        "track_event": "three truth screens, at least two target_event hits; track_trials=5 per level",
        "new_cuda_run_this_turn": False,
        "current_gpu_probe": "nvidia-smi unavailable: couldn't communicate with NVIDIA driver",
        "theory_hit_rate_fit": False,
    }


def plot_unit(out: Path, rows: list[dict[str, object]], dense: list[dict[str, object]], angle: float) -> str:
    selected = sorted([r for r in rows if float(r["angle_deg"]) == angle and int(r["min_points"]) == 2], key=lambda r: float(r["output_scnr_db"]))
    theory = sorted([r for r in dense if float(r["angle_deg"]) == angle and int(r["min_points"]) == 2], key=lambda r: float(r["output_scnr_db"]))
    x = np.asarray([fnum(r["output_scnr_db"]) for r in selected])
    fig, ax = plt.subplots(figsize=(8.0, 5.0))
    ax.errorbar(x, [fnum(r["unit_exact_pd"]) for r in selected], fmt="o-", color="#1f77b4", capsize=3, label="实测 exact CUT Pd")
    ax.plot(x, [fnum(r["unit_resolution_pd"]) for r in selected], "s:", color="#6baed6", label="实测 ±2 bin resolution Pd")
    ax.plot([fnum(r["output_scnr_db"]) for r in theory], [fnum(r["unit_theory_iid_pd"]) for r in theory], "--", color="#d62728", lw=2, label="GO-IID 理论")
    ax.plot(x, [fnum(r["unit_theory_empirical_pd"]) for r in selected], "x-", color="#2ca02c", label="同批 C+N/N-only 经验 GO 理论")
    ax.set(xlabel="实际输出 SCNR (dB)", ylabel="单元级 Pd", title=f"{ANGLE_LABEL[angle]}：输出 SCNR—单元级检测概率")
    ax.set_ylim(-0.03, 1.03); ax.grid(alpha=.25); ax.legend(fontsize=9); fig.tight_layout()
    name = f"unit_pd_{int(angle)}deg.png"; fig.savefig(out / "figures" / name, dpi=180); plt.close(fig); return name


def plot_metric(out: Path, rows: list[dict[str, object]], dense: list[dict[str, object]], minimum: int, metric: str) -> str:
    ylabel = "目标级 Pd" if metric == "target" else "航迹级 Pd（三屏选二）"
    field, theory_field = (("target_pd", "target_theory_pb_pd") if metric == "target" else ("track_pd", "track_theory_independent_pd"))
    fig, ax = plt.subplots(figsize=(8.2, 5.1))
    for angle in ANGLES:
        selected = sorted([r for r in rows if float(r["angle_deg"]) == angle and int(r["min_points"]) == minimum], key=lambda r: float(r["output_scnr_db"]))
        theory = sorted([r for r in dense if float(r["angle_deg"]) == angle and int(r["min_points"]) == minimum], key=lambda r: float(r["output_scnr_db"]))
        x = np.asarray([fnum(r["output_scnr_db"]) for r in selected])
        y = np.asarray([fnum(r[field]) for r in selected])
        lo = np.asarray([fnum(r[f"{metric}_wilson_low"]) for r in selected])
        hi = np.asarray([fnum(r[f"{metric}_wilson_high"]) for r in selected])
        color = ANGLE_COLOR[angle]
        ax.errorbar(x, y, yerr=[y - lo, hi - y], fmt="o", color=color, capsize=3, label=f"{ANGLE_LABEL[angle]} 实测")
        ax.plot([fnum(r["output_scnr_db"]) for r in theory], [fnum(r[theory_field]) for r in theory], "--", color=color, lw=2, label=f"{ANGLE_LABEL[angle]} 理论")
    ax.set(xlabel="实际输出 SCNR (dB)", ylabel=ylabel, title=f"min_points={minimum}：输出 SCNR—{ylabel}")
    ax.set_ylim(-0.03, 1.03); ax.grid(alpha=.25); ax.legend(fontsize=9, ncol=2); fig.tight_layout()
    name = f"{metric}_pd_minpoints_{minimum}.png"; fig.savefig(out / "figures" / name, dpi=180); plt.close(fig); return name


def plot_angle(out: Path, rows: list[dict[str, object]], dense: list[dict[str, object]], minimum: int) -> str:
    fig, ax = plt.subplots(figsize=(8.2, 5.1))
    for angle in ANGLES:
        selected = sorted([r for r in rows if float(r["angle_deg"]) == angle and int(r["min_points"]) == minimum and math.isfinite(fnum(r.get("angle_rmse_deg")))], key=lambda r: float(r["output_scnr_db"]))
        theory = sorted([r for r in dense if float(r["angle_deg"]) == angle and int(r["min_points"]) == minimum], key=lambda r: float(r["output_scnr_db"]))
        color = ANGLE_COLOR[angle]
        if selected:
            ax.plot([fnum(r["output_scnr_db"]) for r in selected], [fnum(r["angle_rmse_deg"]) for r in selected], "o", color=color, label=f"{ANGLE_LABEL[angle]} 实测（有 truth match）")
        ax.plot([fnum(r["output_scnr_db"]) for r in theory], [fnum(r["angle_theory_rmse_deg"]) for r in theory], "--", color=color, lw=2, label=f"{ANGLE_LABEL[angle]} Jacobian 理论")
    ax.set(xlabel="实际输出 SCNR (dB)", ylabel="测角 RMSE (deg)", title=f"min_points={minimum}：输出 SCNR—测角精度")
    ax.set_ylim(bottom=0.0); ax.grid(alpha=.25); ax.legend(fontsize=9, ncol=2); fig.tight_layout()
    name = f"angle_rmse_minpoints_{minimum}.png"; fig.savefig(out / "figures" / name, dpi=180); plt.close(fig); return name


def build_report(out: Path, rows: list[dict[str, object]], dense: list[dict[str, object]], audit: dict[str, object], figure_names: dict[str, str]) -> Path:
    # Summary threshold table: measured points use the first retained point at
    # or above 0.9; theory uses linear interpolation of the dense curve.
    threshold_rows: list[list[object]] = []
    for angle in ANGLES:
        for minimum in MINPOINTS:
            selected = sorted([r for r in rows if float(r["angle_deg"]) == angle and int(r["min_points"]) == minimum], key=lambda r: fnum(r["output_scnr_db"]))
            theory = sorted([r for r in dense if float(r["angle_deg"]) == angle and int(r["min_points"]) == minimum], key=lambda r: fnum(r["output_scnr_db"]))
            mx, my = np.asarray([fnum(r["output_scnr_db"]) for r in selected]), np.asarray([fnum(r["target_pd"]) for r in selected])
            tx, ty = np.asarray([fnum(r["output_scnr_db"]) for r in theory]), np.asarray([fnum(r["target_theory_pb_pd"]) for r in theory])
            mt = next((fnum(r["output_scnr_db"]) for r in selected if fnum(r["target_pd"]) >= .9), float("nan"))
            tt = threshold_crossing(tx, ty)
            track = next((fnum(r["output_scnr_db"]) for r in selected if fnum(r["track_pd"]) >= .9), float("nan"))
            threshold_rows.append([ANGLE_LABEL[angle], minimum, fmt(tt, 2), fmt(mt, 2), fmt(track, 2), fmt(threshold_crossing(tx, np.asarray([fnum(r["track_theory_independent_pd"]) for r in theory])), 2)])

    unit_rows = []
    for angle in ANGLES:
        for r in sorted([x for x in rows if float(x["angle_deg"]) == angle and int(x["min_points"]) == 2], key=lambda x: fnum(x["output_scnr_db"])):
            unit_rows.append([ANGLE_LABEL[angle], fmt(r["output_scnr_db"], 2), f"{inum(r['unit_exact_hits'])}/{inum(r['unit_exact_trials'])}", fmt(r["unit_exact_pd"]), fmt(r["unit_resolution_pd"]), fmt(r["unit_theory_iid_pd"]), fmt(r["unit_theory_empirical_pd"])])

    target_sections: list[str] = []
    for minimum in MINPOINTS:
        table = []
        for angle in ANGLES:
            for r in sorted([x for x in rows if float(x["angle_deg"]) == angle and int(x["min_points"]) == minimum], key=lambda x: fnum(x["output_scnr_db"])):
                table.append([ANGLE_LABEL[angle], fmt(r["output_scnr_db"], 2), f"{inum(r['target_hits'])}/{inum(r['target_trials'])}", fmt(r["target_pd"]), fmt(r["target_theory_pb_pd"]), f"{inum(r['track_hits'])}/{inum(r['track_trials'])}", fmt(r["track_pd"]), fmt(r["track_theory_independent_pd"])])
        target_sections.append(f"### min_points={minimum}\n\n{md_table(['波位','输出 SCNR(dB)','target命中/试验','实测 target Pd','PB 理论 target Pd','track命中/试验','实测 track Pd','独立屏理论 track Pd'], table)}")

    mapping_rows = []
    for angle in ANGLES:
        mapping = audit["mapping"][str(angle)]
        mapping_rows.append([ANGLE_LABEL[angle], fmt(mapping["slope_db_per_db"], 4), fmt(mapping["intercept_db"], 3), fmt(mapping["r2"], 5)])
    profile_rows = []
    for angle in ANGLES:
        meta = audit["profiles"][str(angle)]
        row = meta["profile_row"]
        ratio = np.asarray(meta["ratio"], dtype=float).reshape(3, 5)
        profile_rows.append([ANGLE_LABEL[angle], row.get("sample_count", ""), row.get("qualified_scnr_db_median", ""), fmt(ratio[1, 2], 3), fmt(np.sum(ratio), 3)])

    report = f'''---
title: "GMTI 0°/30°中心输出 SCNR—检测概率—测角精度曲线"
author: "GMTI 算法验证"
date: "2026-08-31"
geometry: margin=1.55cm
mainfont: Noto Sans CJK SC
CJKmainfont: Noto Sans CJK SC
fontsize: 10pt
---

# 结论先行

本报告只覆盖 **0°中心和 30°中心**，横轴全部是实际处理链输出的固定 3×3 支撑 output SCNR（不是 Stage2 `target_snr_db`）。仿真数据来自已留存、已审计的真实 GMTI→GO-CFAR→聚类→目标匹配链路；每个波位、每个 `min_points` 有 10 个输出 SCNR 档位，每档 5 个三屏组，即 15 个周期级单元/目标样本和 5 个三屏航迹样本。

本批是为了稳定 output-SCNR 标尺而关闭面积杂波的热噪声基线（S+N/N-only），不是复杂面积杂波最终结论。当前机器本轮 `nvidia-smi` 无法连接驱动，因此没有声称重新执行 CUDA；图表使用此前完成 GO/TrackManager 审计后保留的真实链路汇总文件。

## 1. 实验数据和真实算法链路

每个周期的处理顺序是：Stage2 生成 S+N 场景 → 生产 GO-CFAR（PFA=$10^{{-6}}$、guard=4、background=16）→ 生产连通组件与 `min_points`/小簇规则 → truth 匹配 → 三屏选二航迹事件。N-only 只在离线计算 output SCNR 和 GO 理论时使用，没有写回在线 XML；S-only 只用于独立的 3×5 支撑形状标定，未用于在线检测或命中率拟合。

| 项目 | 取值 |
|---|---:|
| GO $α$ | {audit['alpha']:.10f} |
| PFA | $10^{{-6}}$ |
| guard/background | 4 / 16 |
| 角度 | 0°中心、30°中心 |
| min_points | 2、3、6 |
| 小簇峰值条件 | 3–5 点分支 +20 dB（生产配置） |
| 每档周期级样本 | 15（5 组×3屏） |
| 每档航迹级样本 | 5（三屏选二） |
| 当前批次 | thermal noise only，area clutter disabled |

## 2. 横轴：输出 SCNR 的定义

固定支撑窗口 $W$ 为物理真值附近的 3×3 单元，不按检测结果重新挑峰。对配对的 S+N 与 N-only 复数/功率图，横轴定义为

$$
\\gamma_{{out}}=\\frac{{E[P_{{S+N}}(W)]-E[P_N(W)]}}{{E[P_N(W)]}},\\qquad
SCNR_{{out}}=10\\log_{{10}}\\gamma_{{out}}.
$$

这一步把用户要求的“不同输出 SCNR”与真实算法输出绑定，而不是把输入 `target_snr_db` 当成横轴。GO 单元理论需要单个物理 CUT 的 $γ_{{CUT}}$，因此从同一批独立的固定支撑功率标尺中得到线性映射

$$SCNR_{{CUT}}=a\\,SCNR_{{out}}+b.$$

映射只用功率/校准列，不用 S+N hit 计数；本批拟合结果为：

{md_table(['波位','a (dB/dB)','b (dB)','R²'], mapping_rows)}

## 3. 单元级检测概率

对 4 个方向训练均值的最大值 $M$，GO-CFAR 条件检测概率是

$$P_{{D,cell}}(\\gamma_{{CUT}})=E_M\\left[Q_1\\left(\\sqrt{{2\\gamma_{{CUT}}}},\\sqrt{{2\\alpha M/P_0}}\\right)\\right].$$

这里 $α={audit['alpha']:.10f}$ 来自 4 个 $16\\times16$ 角块和 4 个 $16\\times9/9\\times16$ 中间块的共享角块模型。红色虚线是 IID Gaussian 八块模型；绿色叉线是同批 C+N/N-only 训练窗与配对目标增量的逐周期经验 GO 理论；蓝色圆点是生产物理真值 CUT exact 命中，浅蓝方点是允许 ±2 FFT bin 的 operational resolution 命中。

{figure_markdown(figure_names['unit_0'], '图 1：0°中心单元级 Pd。每个圆点是 15 个周期样本的命中率，误差条为 Wilson 95% 区间；理论线不读取命中计数。')}

{figure_markdown(figure_names['unit_30'], '图 2：30°中心单元级 Pd。0°和 30°使用各自独立的输出到 CUT 映射。')}

{md_table(['波位','输出 SCNR(dB)','exact 命中/试验','实测 exact Pd','实测 resolution Pd','GO-IID 理论','经验 GO 理论'], unit_rows)}

## 4. 目标级检测概率：真实生产事件与 PB 理论对照

目标级实测值不是把单元概率简单相乘，而是每个周期的真实 joint GO hit mask 经生产连通组件、`min_points`、3–5 点 +20 dB 小簇分支和 truth match 后的 `target_event`。理论线仅作为可解释的点数 baseline：

对 3×5 支撑的第 $i$ 个单元，离线支撑 profile 给出信号比例 $κ_i$ 和相对背景 $β_i$。以物理 CUT 为锚点：

$$
\\rho_i=\\frac{{\\kappa_i/\\beta_i}}{{\\kappa_7/\\beta_7}},\\quad
\\gamma_i=\\gamma_{{CUT}}\\rho_i,\\quad
p_i=P_{{D,cell}}(\\gamma_i).
$$

若只把 15 个点视为条件独立 Bernoulli，生成函数为

$$G(z)=\\prod_{{i=1}}^{{15}}[(1-p_i)+p_i z],\\qquad
P_{{target}}^{{PB}}(m)=P\\left(\\sum_iH_i\\ge m\\right)=\\sum_{{k=m}}^{{15}}[z^k]G(z).$$

因此 PB 理论不是生产目标级 Pd 的替代品；它专门用来回答 `min_points` 造成的点数门槛代价。每个图分开给出一个 `min_points`，避免不同阈值挤在同一张图上。

{figure_markdown(figure_names['target_2'], '图 3：min_points=2，实测目标事件与 3×5 PB baseline。')}

{target_sections[0]}

{figure_markdown(figure_names['target_3'], '图 4：min_points=3，实测目标事件与 3×5 PB baseline。')}

{target_sections[1]}

{figure_markdown(figure_names['target_6'], '图 5：min_points=6，实测目标事件与 3×5 PB baseline。')}

{target_sections[2]}

### 目标级读图

PB 线与生产点不重合是有物理含义的：PB 只保留“15 个候选点中过 $m$ 个”的独立点数层，而生产点还包含二维连通性、实际 target-select、定位/重定位和 truth match；同时本批目标主瓣在 FFT 支撑中的实际 joint hit mask 并不等于静态高 SNR $κ/β$ profile 的独立 Bernoulli 假设。故图中差异应作为“理论 baseline 与真实生产漏斗的差异”读取，不能把 PB 线当成算法故障或最终 90% 门限。

## 5. 航迹级检测概率（三屏选二）

主航迹事件冻结为同一 truth 轨迹的三屏命中数不少于 2：

$$D_{{track}}=1\\left\\{{D_1+D_2+D_3\\ge2\\right\\}}.$$

如果三个屏幕具有相同且近似独立的目标级概率 $p$，理论 baseline 为

$$P_{{track}}^{{ind}}=3p^2-2p^3,$$

其中 $p$ 取同一张 PB 目标理论曲线。实测圆点严格来自 `track_2of3_hit`，不是 TrackManager 的 Confirmed 状态替代。

{figure_markdown(figure_names['track_2'], '图 6：min_points=2，三屏选二航迹 Pd。')}

{figure_markdown(figure_names['track_3'], '图 7：min_points=3，三屏选二航迹 Pd。')}

{figure_markdown(figure_names['track_6'], '图 8：min_points=6，三屏选二航迹 Pd。')}

## 6. 测角精度

对有 truth match 的周期，实测 RMSE 为匹配误差平方均值的平方根。理论曲线采用相位噪声到角度的 Jacobian 基线：

$$
\\sigma_\\theta(\\mathrm{{deg}})=
\\frac{{180\\lambda}}{{2\\pi^2 d\\cos\\theta}}
\\frac{{1}}{{\\sqrt{{\\gamma_{{CUT}}}}}},
\\quad \\lambda=0.0182\\,m,\\ d=0.17\\,m.
$$

它描述“在该输出 SCNR 下、角度估计由相位噪声主导”的理想趋势；低 SCNR 没有 truth match 的点不被填成 0，而是保留为空。

{figure_markdown(figure_names['angle_2'], '图 9：min_points=2 的测角 RMSE 与 Jacobian 理论。')}

{figure_markdown(figure_names['angle_3'], '图 10：min_points=3 的测角 RMSE 与 Jacobian 理论；低 SCNR 无匹配样本处不绘制实测点。')}

{figure_markdown(figure_names['angle_6'], '图 11：min_points=6 的测角 RMSE 与 Jacobian 理论；只有通过目标级门控的样本才进入 RMSE。')}

## 7. 0.9 交点摘要

“实测交点”是离散档位中第一个 `Pd>=0.9` 的 output SCNR，不是用少量点拟合出的精确门限；“理论交点”是 241 点理论网格线性插值得到的参考。

{md_table(['波位','min_points','理论 target 0.9(dB)','实测 target 首点(dB)','实测 track 首点(dB)','理论 track 0.9(dB)'], threshold_rows)}

## 8. 审计边界

- 在线检测器没有读取 truth、N-only 或 S-only 先验；`strict_online_no_prior=true`，N-only/S-only 仅用于离线 output-SCNR、理论积分和 truth 评分。
- 目标支撑 profile 共使用独立留存 calibration 样本，且其 manifest 明确 `evaluation_hit_rates_used_for_fit=false`；本报告没有用 evaluation 命中率反向拟合 PB/GO 参数。
- 本报告的实测数据不是本轮新 CUDA 运行，而是此前 GO/TrackManager 审计成功后保留的 `standard_mc_curve_summary.csv`、`standard_mc_screen_metrics.csv` 和 `go_theory_vs_mc.csv`。
- 当前批次没有面积杂波；因此曲线只回答“稳定 output-SCNR 的真实处理链基线”，不能外推到复杂 C+N 背景。

完整机器表：`real_chain_0_30_curves.csv`；理论密集网格：`real_chain_0_30_theory_dense.csv`；绘图和报告脚本：`scripts/gmti_scnr_eval/42_build_0_30_real_chain_curves.py`。
'''
    md_path = out / "GMTI_0_30中心_输出SCNR_检测概率_测角精度_真实链路对照报告.md"
    md_path.write_text(report, encoding="utf-8")
    return md_path


def figure_markdown(name: str, caption: str) -> str:
    return f"![{caption}](figures/{name})\n\n*{caption}*"


def convert_reports(md_path: Path, out: Path) -> dict[str, str]:
    pdf = out / "GMTI_0_30中心_输出SCNR_检测概率_测角精度_真实链路对照报告.pdf"
    html = out / "GMTI_0_30中心_输出SCNR_检测概率_测角精度_真实链路对照报告.html"
    if shutil.which("pandoc"):
        pdf_cmd = ["pandoc", str(md_path.name), "-o", str(pdf.name), "--pdf-engine=xelatex"]
        pdf_run = subprocess.run(pdf_cmd, cwd=out, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if pdf_run.returncode != 0:
            (out / "pandoc_pdf_error.log").write_text(pdf_run.stdout + "\n" + pdf_run.stderr, encoding="utf-8")
        html_cmd = ["pandoc", str(md_path.name), "-o", str(html.name), "--standalone", "--metadata", "title=GMTI 0/30 output SCNR curves"]
        html_run = subprocess.run(html_cmd, cwd=out, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if html_run.returncode != 0:
            (out / "pandoc_html_error.log").write_text(html_run.stdout + "\n" + html_run.stderr, encoding="utf-8")
    return {"pdf": str(pdf), "html": str(html), "pdf_exists": pdf.is_file(), "html_exists": html.is_file()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/gmti_scnr_eval/real_chain_0_30_curves_20260831")
    parser.add_argument("--profile", type=Path, default=ROOT / "outputs/gmti_scnr_eval/target_support_calibration_corrected_20260830/target_support_profiles.csv")
    parser.add_argument("--theory-samples", type=int, default=60000)
    args = parser.parse_args()
    if args.theory_samples <= 0:
        raise SystemExit("--theory-samples 必须为正")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "figures").mkdir(parents=True, exist_ok=True)
    measured, dense, audit = build_data(args.output_dir, args.profile, args.theory_samples)
    figure_names = {
        "unit_0": plot_unit(args.output_dir, measured, dense, 0.0),
        "unit_30": plot_unit(args.output_dir, measured, dense, 30.0),
        "target_2": plot_metric(args.output_dir, measured, dense, 2, "target"),
        "target_3": plot_metric(args.output_dir, measured, dense, 3, "target"),
        "target_6": plot_metric(args.output_dir, measured, dense, 6, "target"),
        "track_2": plot_metric(args.output_dir, measured, dense, 2, "track"),
        "track_3": plot_metric(args.output_dir, measured, dense, 3, "track"),
        "track_6": plot_metric(args.output_dir, measured, dense, 6, "track"),
        "angle_2": plot_angle(args.output_dir, measured, dense, 2),
        "angle_3": plot_angle(args.output_dir, measured, dense, 3),
        "angle_6": plot_angle(args.output_dir, measured, dense, 6),
    }
    md_path = build_report(args.output_dir, measured, dense, audit, figure_names)
    converted = convert_reports(md_path, args.output_dir)
    manifest = {**audit, "output_dir": str(args.output_dir.resolve()), "script": str(Path(__file__).resolve()), "figures": figure_names, "reports": converted, "measured_rows": len(measured), "dense_rows": len(dense)}
    (args.output_dir / "real_chain_0_30_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "measured_rows": len(measured), "dense_rows": len(dense), "reports": converted}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
