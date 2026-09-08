#!/usr/bin/env python3
"""Build the current comprehensive GMTI SCNR/Pd/angle report.

This builder deliberately consumes retained, auditable summaries only.  It
does not run the detector and it never uses truth to choose an online peak.
The report axis is the measured production ``output SCNR``.  The physical CUT
axis used by the GO equations is obtained from an independent, paired
output-to-CUT calibration in the corrected one-seed roots.

The generated directory contains the machine-readable tables, figures,
Markdown source, PDF and HTML.  It supersedes the older compact theory
supplement without overwriting it.

The primary abscissa is the production-CUT-equivalent output SCNR.  Older
retained roots also contain a fixed 3x3 integrated-support SCNR; that value is
kept as ``support_output_scnr_db`` for diagnostics, but is never compared
directly with the single-CUT GO equation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, patches
import numpy as np
from scipy.optimize import brentq
from scipy.stats import ncx2

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
from scnr_eval_lib import read_csv, wilson_interval, write_csv  # noqa: E402


FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if FONT.is_file():
    font_manager.fontManager.addfont(str(FONT))
    plt.rcParams["font.family"] = "Noto Sans CJK JP"
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams.update({
    "font.size": 9,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "savefig.dpi": 220,
    "figure.dpi": 150,
})

ANGLES = (0.0, 30.0, 45.0)
# Every requested beam position has a calibration-only profile.  The center
# groups also have the retained formal evaluation curves; edge groups are
# theory/calibration overlays unless an independent evaluation root exists.
GROUPS = ("0deg_center", "0deg_edge", "30deg_center", "30deg_edge",
          "45deg_center", "45deg_edge")
ANGLE_LABEL = {0.0: "0°中心", 30.0: "30°中心", 45.0: "45°中心"}
GROUP_LABEL = {
    "0deg_center": "0°中心",
    "0deg_edge": "0°边缘（+0.8°）",
    "30deg_center": "30°中心",
    "30deg_edge": "30°边缘（+0.8°）",
    "45deg_center": "45°中心",
    "45deg_edge": "45°边缘（+0.8°）",
}
LEGACY_TARGET_PROFILE_PATH = ROOT / "docs/GMTI_输出SCNR_目标级minpoints理论_20260826/target_support_profiles.csv"
# Prefer the compact profile produced by the current strict-online calibration
# run when it exists.  The legacy file remains a read-only fallback so an
# earlier report can still be rebuilt before a new calibration is available.
CORRECTED_TARGET_PROFILE_PATH = ROOT / "outputs/gmti_scnr_eval/target_support_calibration_corrected_20260830/target_support_profiles.csv"
TARGET_PROFILE_PATH = (CORRECTED_TARGET_PROFILE_PATH if CORRECTED_TARGET_PROFILE_PATH.is_file()
                       else LEGACY_TARGET_PROFILE_PATH)
HISTORICAL_MORPHOLOGY_PATH = ROOT / "outputs/gmti_scnr_eval/target_track_analysis_0deg_20260827/target_track_period_audit.csv"
MULTISEED_SUMMARY_PATH = ROOT / "outputs/gmti_scnr_eval/target_minpoints_strict_20260828/min6_3seed_validation_corrected_20260830/min6_multiseed_summary.csv"
MULTISEED_MANIFEST_PATH = ROOT / "outputs/gmti_scnr_eval/target_minpoints_strict_20260828/min6_3seed_validation_corrected_20260830/min6_multiseed_manifest.json"
# The thermal-only three-seed history above remains a regression reference.  The
# current report also embeds the separately audited compact-statistical S+C+N
# validation when it has been generated; keeping the two roots distinct avoids
# silently relabelling an old baseline as a clutter experiment.
# Prefer the latest CUDA-validated 5-seed CUT-axis aggregate.  Earlier
# aggregates remain read-only fallbacks so an older workspace stays
# rebuildable without silently relabelling its sample count.
SCN_MULTISEED_DIR = ROOT / "outputs/gmti_scnr_eval/scn_validation_20260830/scn_5seed_validation_v3"
if not (SCN_MULTISEED_DIR / "scn_multiseed_summary.csv").is_file():
    SCN_MULTISEED_DIR = ROOT / "outputs/gmti_scnr_eval/scn_validation_20260830/scn_3seed_validation_v2"
if not (SCN_MULTISEED_DIR / "scn_multiseed_summary.csv").is_file():
    SCN_MULTISEED_DIR = ROOT / "outputs/gmti_scnr_eval/scn_validation_20260830/scn_3seed_validation"
SCN_MULTISEED_SUMMARY_PATH = SCN_MULTISEED_DIR / "scn_multiseed_summary.csv"
SCN_MULTISEED_MANIFEST_PATH = SCN_MULTISEED_DIR / "scn_multiseed_manifest.json"
SCN_MULTISEED_AUDIT_PATH = SCN_MULTISEED_DIR / "scn_information_leakage_audit.csv"
# Same-seed S+C+N comparison for min_points=2/3/6.  This is deliberately
# separate from the pooled min_points=6 validation above: all three roots use
# the identical background realization and only the clustering threshold
# changes, so the main report can attribute the observed difference correctly.
SCN_MINPOINTS_DIR = ROOT / "outputs/gmti_scnr_eval/scn_validation_20260830/minpoints_20261031"
SCN_MINPOINTS_SUMMARY_PATH = SCN_MINPOINTS_DIR / "scn_minpoints_summary.csv"
SCN_MINPOINTS_MANIFEST_PATH = SCN_MINPOINTS_DIR / "scn_minpoints_manifest.json"
SCN_MINPOINTS_AUDIT_PATH = SCN_MINPOINTS_DIR / "scn_minpoints_information_leakage_audit.json"
SCN_MINPOINTS_MARKDOWN_PATH = SCN_MINPOINTS_DIR / "GMTI_S+C+N_minpoints_2_3_6_验证报告.md"
# A single, deliberately small (+0.8°) S+C+N edge run is retained as a
# feasibility/stress-test appendix.  It is not merged into the centre pooled
# curves and is never used to claim a final Pd90 threshold.
SCN_EDGE_DIR = ROOT / "outputs/gmti_scnr_eval/scn_edge_validation_20260830/seed_20261041_edge_1group"
# Latest targeted CUDA regression for the physical truth-CUT axis.  It is kept
# as a root-cause appendix: only 45° was rerun, so it must not replace the
# three-angle pooled validation above.
TRUTH_AXIS_VALIDATION_DIR = ROOT / "outputs/gmti_scnr_eval/truth_cut_axis_validation_20260830/seed_20261035_truth45_v2/angle_p45p000"
COLORS = {0.0: "#1f77b4", 30.0: "#d62728", 45.0: "#2ca02c"}
MODEL_COLORS = {
    "fixed_m1": "#444444",
    "A_IID_no_leak": "#111111",
    "B_IID_leak": "#d62728",
    "C_real_CN": "#1f77b4",
    "C_real_CN_leak": "#2ca02c",
}
MODEL_LABELS = {
    "fixed_m1": "教材固定 m=1",
    "A_IID_no_leak": "A：IID GO，无泄漏",
    "B_IID_leak": "B：IID GO + 实测泄漏",
    "C_real_CN": "C：真实 C+N 背景",
    "C_real_CN_leak": "C：真实 C+N + 实测泄漏",
}


def is_edge_group(group: str) -> bool:
    """Return whether a calibration group represents a beam-edge target."""
    return str(group).endswith("_edge")


def fnum(value: object, default: float = float("nan")) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def fmt(value: object, digits: int = 3) -> str:
    value = fnum(value)
    return "—" if not math.isfinite(value) else f"{value:.{digits}f}"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def md_table(headers: Sequence[object], rows: Iterable[Sequence[object]]) -> str:
    headers = [str(x) for x in headers]
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def token(angle: float) -> str:
    return f"angle_p{int(round(angle))}p000"


def curve_path(root: Path, angle: float) -> Path:
    path = root / "runs" / token(angle) / token(angle) / "standard_mc_curve_summary.csv"
    if not path.is_file():
        candidates = sorted(root.rglob("standard_mc_curve_summary.csv"))
        if candidates:
            return candidates[0]
        raise FileNotFoundError(f"缺少 {root} 的标准汇总 CSV")
    return path


def mapping_from_root(root: Path, angle: float) -> dict[str, float]:
    path = curve_path(root, angle).parent / "processed_output_snr_calibration.csv"
    xs, ys = [], []
    for row in read_csv(path):
        x = fnum(row.get("processed_output_scnr_db"))
        y = fnum(row.get("unit_cut_scnr_db"))
        if math.isfinite(x) and math.isfinite(y):
            xs.append(x); ys.append(y)
    if len(xs) < 2:
        raise RuntimeError(f"{path} 可用于输出→CUT回归的样本不足")
    x, y = np.asarray(xs), np.asarray(ys)
    slope, intercept = np.polyfit(x, y, 1)
    residual = y - (slope * x + intercept)
    total = float(np.sum((y - np.mean(y)) ** 2))
    return {
        "angle_deg": angle,
        "slope_db_per_db": float(slope),
        "intercept_db": float(intercept),
        "r2": 1.0 - float(np.sum(residual ** 2)) / total if total else float("nan"),
        "max_abs_residual_db": float(np.max(np.abs(residual))),
        "sample_count": len(xs),
        "source": str(path.resolve()),
    }


class Geometry:
    """Production GO geometry, with the eight shared independent blocks."""

    def __init__(self, guard: int, background: int = 16):
        self.guard = int(guard)
        self.background = int(background)
        self.guard_width = 2 * self.guard + 1
        self.outer_width = 2 * (self.guard + self.background) + 1
        self.direction_count = self.background * self.outer_width
        b, w = self.background, self.guard_width
        self.block_counts = np.asarray(
            [b * b, b * w, b * b, w * b, w * b, b * b, b * w, b * b],
            dtype=np.int64)

    def validate(self) -> None:
        if self.guard <= 0 or self.background <= 0:
            raise ValueError("GO guard/background必须为正")


def iid_m_samples(geometry: Geometry, count: int, seed: int,
                  batch_size: int = 100_000) -> np.ndarray:
    geometry.validate()
    rng = np.random.default_rng(seed)
    values = []
    left = int(count)
    while left:
        n = min(left, batch_size)
        blocks = rng.gamma(geometry.block_counts.astype(float), 1.0, size=(n, 8))
        tl, tc, tr, lc, rc, bl, bc, br = (blocks[:, i] for i in range(8))
        d = float(geometry.direction_count)
        dirs = np.column_stack(((tl + lc + bl) / d,
                                (tr + rc + br) / d,
                                (tl + tc + tr) / d,
                                (bl + bc + br) / d))
        values.append(np.max(dirs, axis=1))
        left -= n
    return np.concatenate(values)


def calibrate_alpha(m: np.ndarray, pfa: float) -> float:
    def residual(alpha: float) -> float:
        return float(np.mean(np.exp(-alpha * m)) - pfa)
    return float(brentq(residual, 1.0, 100.0, xtol=1e-12, rtol=1e-12))


def averaged_pd(gamma: np.ndarray, alpha: float, m: np.ndarray,
                batch_size: int = 20_000) -> np.ndarray:
    gamma = np.asarray(gamma, dtype=float).reshape(-1)
    out = np.zeros_like(gamma)
    for start in range(0, len(m), batch_size):
        part = m[start:start + batch_size]
        out += np.sum(ncx2.sf(2.0 * alpha * part[:, None], 2,
                               2.0 * gamma[None, :]), axis=0)
    return out / max(1, len(m))


def noncentral_pd(gamma: np.ndarray, alpha: float, geometry: Geometry,
                  lambda_per_gamma: np.ndarray, samples: int, seed: int) -> np.ndarray:
    """Monte-Carlo integral of the corrected noncentral eight-block model.

    Each block is a raw power sum.  ``2|x|^2/P0`` is noncentral chi-square;
    dividing by two yields the raw ``|x|^2/P0`` sum expected by the shared
    directional reconstruction.  No second division by block degrees of
    freedom is made here.
    """
    rng = np.random.default_rng(seed)
    gamma = np.asarray(gamma, dtype=float).reshape(-1)
    lam = np.asarray(lambda_per_gamma, dtype=float)
    df = 2.0 * geometry.block_counts.astype(float)
    out = np.zeros_like(gamma)
    for i, g in enumerate(gamma):
        blocks = np.empty((samples, 8), dtype=float)
        for j in range(8):
            blocks[:, j] = rng.noncentral_chisquare(
                df[j], max(0.0, 2.0 * g * lam[j]), size=samples) / 2.0
        tl, tc, tr, lc, rc, bl, bc, br = (blocks[:, k] for k in range(8))
        d = float(geometry.direction_count)
        means = np.column_stack(((tl + lc + bl) / d,
                                 (tr + rc + br) / d,
                                 (tl + tc + tr) / d,
                                 (bl + bc + br) / d))
        m = np.max(means, axis=1)
        out[i] = float(np.mean(ncx2.sf(2.0 * alpha * m, 2, 2.0 * g)))
    return out


def load_templates(path: Path) -> dict[str, dict[str, object]]:
    result = {}
    for row in read_csv(path):
        group = str(row.get("angle_group", ""))
        result[group] = {
            "angle_deg": fnum(row.get("angle_deg")),
            "position": row.get("position", ""),
            "sample_count": int(round(fnum(row.get("sample_count"), 0))),
            "qualified_count": int(round(fnum(row.get("qualified_count_cut_gamma_ref_ge_1"), 0))),
            "lambda": np.asarray([fnum(row.get(f"block_{i}_lambda_per_gamma")) for i in range(8)]),
            "direction": np.asarray([fnum(row.get(f"direction_{i}_delta_per_gamma")) for i in range(4)]),
            "source": row.get("support_source", ""),
        }
    return result


def load_windows(path: Path) -> dict[str, dict[str, np.ndarray]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in read_csv(path):
        grouped.setdefault(str(row.get("angle_group", "")), []).append(row)
    result = {}
    for group, rows in grouped.items():
        dirs = np.asarray([[fnum(r.get(f"mean_{d}")) for d in "LRTB"] for r in rows])
        cut = np.asarray([fnum(r.get("CUT_background")) for r in rows])
        denom = np.maximum(cut, 1e-12)
        normalized = dirs / denom[:, None]
        result[group] = {
            "means": dirs,
            "cut": cut,
            "normalized": normalized,
            "m": np.max(normalized, axis=1),
            "count": len(rows),
            "maps": len(set(str(r.get("source_map", "")) for r in rows)),
        }
    return result


def read_metrics(root: Path, minimum: int) -> list[dict[str, object]]:
    result = []
    for angle in ANGLES:
        path = curve_path(root, angle)
        theory_path = path.parent / "go_theory_vs_mc.csv"
        theory_by_level = {
            int(round(fnum(item.get("level_index"), -1))): item
            for item in read_csv(theory_path)
        } if theory_path.is_file() else {}
        for raw in read_csv(path):
            row = dict(raw)
            row["min_points"] = minimum
            row["angle_deg"] = angle
            level = int(round(fnum(row.get("level_index"), -1)))
            # Pre-axis-fix roots wrote the integrated 3x3 support value into
            # output_scnr_db.  The retained theory sidecar contains the
            # independently measured physical CUT SCNR for the same level.
            # Prefer explicit future columns and migrate old audited roots
            # without opening raw maps.
            support_axis = fnum(row.get("support_output_scnr_db"),
                                fnum(row.get("output_scnr_db")))
            cut_axis = fnum(row.get("output_scnr_cut_equiv_db"))
            if not math.isfinite(cut_axis):
                cut_axis = fnum(theory_by_level.get(level, {}).get("unit_cut_scnr_db"))
            row["support_output_scnr_db"] = support_axis
            row["output_scnr_cut_equiv_db"] = cut_axis
            if math.isfinite(cut_axis):
                row["output_scnr_db"] = cut_axis
            for key in ("output_scnr_db", "unit_exact_pd", "unit_resolution_pd", "cluster_pd",
                        "target_pd", "track_pd", "angle_rmse_deg", "angle_bias_deg"):
                row[key] = fnum(row.get(key))
            for key in ("unit_exact_hits", "unit_exact_trials", "unit_resolution_hits",
                        "unit_resolution_trials", "cluster_hits", "cluster_trials",
                        "target_hits", "target_trials", "track_hits", "track_trials"):
                row[key] = int(round(fnum(row.get(key), 0)))
            if math.isfinite(cut_axis):
                row["output_scnr_db"] = cut_axis
            result.append(row)
    return sorted(result, key=lambda r: (r["angle_deg"], r["output_scnr_db"]))


def read_dependency(root: Path, minimum: int) -> list[dict[str, object]]:
    path = root / "minpoints_separate" / "track_dependency_summary.csv"
    if path.is_file():
        return [dict(row, min_points=minimum) for row in read_csv(path)]
    result = []
    for angle in ANGLES:
        p = curve_path(root, angle).parent / "standard_mc_screen_metrics.csv"
        if not p.is_file():
            continue
        values = np.asarray([[int(round(fnum(r.get(f"screen{i}_hit"), 0))) for i in (1, 2, 3)]
                             for r in read_csv(p)], dtype=float)
        if not len(values):
            continue
        p_screen = float(values.mean())
        p12 = float(np.mean(values[:, 0] * values[:, 1]))
        p13 = float(np.mean(values[:, 0] * values[:, 2]))
        p23 = float(np.mean(values[:, 1] * values[:, 2]))
        p123 = float(np.mean(np.prod(values, axis=1)))
        result.append({"min_points": minimum, "angle_deg": angle,
                       "group_count": len(values), "screen_trial_count": len(values) * 3,
                       "p_screen": p_screen,
                       "p_dt1_given_dt0_1": p12 / p_screen if p_screen else float("nan"),
                       "p_dt1_given_dt0_0": float("nan"),
                       "pairwise_correlation_mean": float("nan"),
                       "p12": p12, "p13": p13, "p23": p23, "p123": p123,
                       "track_pd_inclusion_exclusion": p12 + p13 + p23 - 2 * p123,
                       "track_pd_direct": float(np.mean(values.sum(axis=1) >= 2)),
                       "track_pd_independent_baseline": 3 * p_screen ** 2 - 2 * p_screen ** 3})
    return result


def load_target_support_profiles(path: Path) -> dict[str, dict[str, object]]:
    """Load calibration-only 3x5 target support shapes.

    The profiles are retained CSV summaries produced from S-only/C+N-only
    calibration maps.  They are deliberately kept separate from the
    evaluation roots: no evaluation hit count is used to fit the cell shape.
    """
    if not path.is_file():
        return {}
    profiles: dict[str, dict[str, object]] = {}
    for row in read_csv(path):
        group = str(row.get("angle_group", ""))
        if not group:
            continue
        profiles[group] = {
            "angle_deg": fnum(row.get("angle_deg")),
            "position": str(row.get("position", "center")),
            "sample_count": int(round(fnum(row.get("sample_count"), 0))),
            "qualified_count": int(round(fnum(row.get("qualified_count_cut_gamma_ref_ge_1"), 0))),
            "qualified_scnr_db_median": fnum(row.get("qualified_scnr_db_median")),
            "kappa": np.asarray([fnum(row.get(f"kappa_{i:02d}")) for i in range(15)], dtype=float),
            "beta": np.asarray([fnum(row.get(f"beta_{i:02d}")) for i in range(15)], dtype=float),
            "source": str(path.resolve()),
        }
    return profiles


def load_multiseed_summary(path: Path) -> list[dict[str, object]]:
    """Load an already-audited min_points=6 three-seed aggregate, if present."""
    if not path.is_file():
        return []
    result: list[dict[str, object]] = []
    for raw in read_csv(path):
        row = dict(raw)
        for key in ("angle_deg", "requested_output_scnr_db", "output_scnr_db_mean",
                    "output_scnr_db_std", "unit_pd", "cluster_pd", "target_pd",
                    "track_pd", "angle_rmse_deg", "angle_bias_deg"):
            row[key] = fnum(row.get(key))
        for key in ("seed_count", "unit_hits", "unit_trials", "cluster_hits", "cluster_trials",
                    "target_hits", "target_trials", "track_hits", "track_trials", "angle_samples"):
            row[key] = int(round(fnum(row.get(key), 0)))
        result.append(row)
    return sorted(result, key=lambda r: (fnum(r.get("angle_deg")), fnum(r.get("output_scnr_db_mean"))))


def target_profile_table(profiles: dict[str, dict[str, object]]) -> str:
    """Render the numerical support-shape parameters used by PB theory."""
    support_indices = (1, 2, 3, 6, 7, 8, 11, 12, 13)
    rows: list[list[object]] = []
    for group in GROUPS:
        profile = profiles.get(group)
        if profile is None:
            continue
        kappa = np.maximum(np.asarray(profile["kappa"], dtype=float), 0.0)
        beta = np.maximum(np.asarray(profile["beta"], dtype=float), 1e-12)
        if float(np.sum(kappa)) <= 0.0:
            continue
        kappa /= float(np.sum(kappa)); beta /= max(float(np.median(beta)), 1e-12)
        k3 = float(np.sum(kappa[list(support_indices)])); b3 = float(np.sum(beta[list(support_indices)]))
        # The displayed offset is now the CUT-to-3x5 profile anchor, not the
        # deprecated 3x3-total output axis.  Keep the 3x3 sums in the table
        # because they are useful diagnostics for the support-energy loss.
        ref = float(kappa[7] / max(beta[7], 1e-12))
        offset = 10.0 * math.log10(1.0 / max(ref, 1e-12)) if ref > 0.0 else float("nan")
        rows.append([
            group, profile.get("sample_count", "—"), profile.get("qualified_count", "—"),
            fmt(profile.get("qualified_scnr_db_median"), 2), fmt(kappa[7], 4),
            fmt(k3, 4), fmt(b3, 3), fmt(offset, 3), int(np.argmax(kappa)),
        ])
    return md_table(["group", "n_cal", "合格 n", "合格 SCNR中位(dB)", "κ7中心",
                     "Σκ(3×3)", "Σβ(3×3)", "CUT→3×5偏置(dB)", "最大κ索引"], rows)


def multiseed_table(rows: list[dict[str, object]]) -> str:
    """Summarize the pooled three-seed min6 validation for the main report."""
    if not rows:
        return "未发现已审计的三 seed min_points=6 汇总。"
    table_rows = []
    for angle in ANGLES:
        selected = [r for r in rows if abs(fnum(r.get("angle_deg")) - angle) < 1e-9]
        selected.sort(key=lambda r: fnum(r.get("output_scnr_db_mean")))
        target_cross = crossing([fnum(r.get("output_scnr_db_mean")) for r in selected],
                                [fnum(r.get("target_pd")) for r in selected], .9)
        track_cross = crossing([fnum(r.get("output_scnr_db_mean")) for r in selected],
                               [fnum(r.get("track_pd")) for r in selected], .9)
        target_values = [fnum(r.get("target_pd")) for r in selected]
        track_values = [fnum(r.get("track_pd")) for r in selected]
        rmses = [fnum(r.get("angle_rmse_deg")) for r in selected]
        table_rows.append([
            f"{angle:g}°", len(selected),
            f"{int(selected[0].get('seed_count', 0)) if selected else 0}",
            fmt(target_cross, 2), fmt(track_cross, 2),
            fmt(np.nanmean(target_values) if target_values else float("nan"), 3),
            fmt(np.nanmean(track_values) if track_values else float("nan"), 3),
            fmt(np.nanmean(rmses) if any(math.isfinite(x) for x in rmses) else float("nan"), 4),
        ])
    return md_table(["角度", "SCNR档位", "seed数", "target Pd0.9交点(dB)",
                     "track Pd0.9交点(dB)", "平均target Pd", "平均track Pd", "平均RMSE(deg)"], table_rows)


def poisson_binomial_tail(probabilities: np.ndarray, minimum: int) -> np.ndarray:
    """Return P(sum_i H_i >= minimum) for vector-valued Bernoulli p_i.

    ``probabilities`` has shape [SCNR grid, support cells].  The recurrence
    keeps the coefficient of the generating polynomial
    ``prod_i ((1-p_i)+p_i z)``; it therefore does not require an independence
    assumption between different GO directions, only between the candidate
    cells in this analytic baseline.
    """
    p = np.asarray(probabilities, dtype=float)
    if p.ndim != 2:
        raise ValueError("Poisson-binomial probabilities must be [grid, cells]")
    grid_count, cell_count = p.shape
    dp = np.zeros((grid_count, cell_count + 1), dtype=float)
    dp[:, 0] = 1.0
    for index in range(cell_count):
        pi = np.clip(p[:, index], 0.0, 1.0)
        old = dp.copy()
        dp[:, 0] = old[:, 0] * (1.0 - pi)
        dp[:, 1:index + 2] = (
            old[:, 1:index + 2] * (1.0 - pi[:, None])
            + old[:, :index + 1] * pi[:, None]
        )
    minimum = int(minimum)
    if minimum <= 0:
        return np.ones(grid_count, dtype=float)
    if minimum > cell_count:
        return np.zeros(grid_count, dtype=float)
    return np.sum(dp[:, minimum:], axis=1)


def build_target_pb_theory(out: Path, profiles: dict[str, dict[str, object]],
                           mappings: dict[float, dict[str, float]],
                           m_samples: np.ndarray, alpha: float,
                           grid: np.ndarray) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Build the calibration-only target-level Poisson-binomial baseline.

    The report axis is the physical production CUT-equivalent output SCNR.
    The calibration profile is measured on a 3x5 support.  If ``kappa_7`` and
    ``beta_7`` are the measured signal/background fractions of its central
    truth cell, a CUT ratio ``gamma_cut`` implies
    ``gamma_i = gamma_cut*(kappa_i/beta_i)/(kappa_7/beta_7)``.  Thus the
    reference cell has exactly the plotted CUT SCNR; no fixed-support total is
    silently compared with the single-CUT GO equation.  The old 3x3-integrated
    axis is retained as a diagnostic inverse-mapping column.
    """
    rows: list[dict[str, object]] = []
    summary: list[dict[str, object]] = []
    production_groups = GROUPS
    # The 3×5 calibration footprint is indexed row-major; index 7 is its
    # central (zero Doppler/zero range-offset) cell.  It is only reported as a
    # reference shape coordinate, not used to fit any probability.
    truth_index = 7
    for group in production_groups:
        profile = profiles.get(group)
        if profile is None:
            continue
        raw_angle = fnum(profile["angle_deg"])
        mapping_angle = 0.0 if group.startswith("0deg") else raw_angle
        if mapping_angle not in mappings:
            continue
        mapping = mappings[mapping_angle]
        cut_db = np.asarray(grid, dtype=float)
        gamma_output = 10.0 ** (cut_db / 10.0)
        # Equal-cell comparison is on the same physical CUT-equivalent axis.
        gamma_ref = gamma_output.copy()
        kappa = np.maximum(np.asarray(profile["kappa"], dtype=float), 0.0)
        beta = np.maximum(np.asarray(profile["beta"], dtype=float), 1e-12)
        if not np.isfinite(kappa).all() or not np.isfinite(beta).all() or float(kappa.sum()) <= 0:
            continue
        kappa /= float(kappa.sum())
        beta /= max(float(np.median(beta)), 1e-12)
        # Anchor every calibrated cell to the measured truth CUT.  This is
        # the physically identifiable mapping for a point target and avoids
        # the former 3x3-total-to-single-CUT axis mismatch.
        reference_ratio = float(kappa[truth_index] / max(beta[truth_index], 1e-12))
        profile_scale = 1.0 / max(reference_ratio, 1e-12)
        if not math.isfinite(profile_scale) or profile_scale <= 0.0:
            continue
        scale = kappa / beta
        cell_pd = np.column_stack([
            averaged_pd(gamma_output * profile_scale * float(cell_scale), alpha, m_samples)
            for cell_scale in scale
        ])
        equal_pd = np.repeat(averaged_pd(gamma_ref, alpha, m_samples)[:, None], 15, axis=1)
        for minimum in (2, 3, 6):
            pb_profile = poisson_binomial_tail(cell_pd, minimum)
            pb_equal = poisson_binomial_tail(equal_pd, minimum)
            for index, output_db in enumerate(grid):
                rows.append({
                    "angle_group": group,
                    "angle_deg": raw_angle,
                    "mapping_angle_deg": mapping_angle,
                    "position": profile["position"],
                    "output_scnr_db": float(output_db),
                    "support_output_scnr_db": float(
                        (output_db - mapping["intercept_db"]) / mapping["slope_db_per_db"]),
                    "cut_scnr_db": float(cut_db[index]),
                    "profile_support_scnr_db": float(grid[index] + 10.0 * math.log10(profile_scale)),
                    "output_to_profile_support_offset_db": float(10.0 * math.log10(profile_scale)),
                    "min_points": minimum,
                    "pd_cluster_pb_profile": float(pb_profile[index]),
                    "pd_cluster_pb_equal_cell": float(pb_equal[index]),
                    "pd_unit_mean_profile": float(np.mean(cell_pd[index])),
                    "support_cells": 15,
                    "support_sample_count": profile["sample_count"],
                    "qualified_scnr_db_median": profile["qualified_scnr_db_median"],
                    "alpha": alpha,
                    "pfa": 1e-6,
                    "connectivity_factor": 1.0,
                    "model": "PB_calibration_support_eta1",
                    "valid": True,
                    "note": ("3x5 calibration support; point-count baseline only; "
                             "edge calibration support; point-count baseline only" if is_edge_group(group) else
                             "3x5 calibration support; point-count baseline only"),
                })
            x = grid
            for model_name, values in (("profile", pb_profile), ("equal_cell", pb_equal)):
                summary.append({
                    "angle_group": group,
                    "angle_deg": raw_angle,
                    "mapping_angle_deg": mapping_angle,
                    "min_points": minimum,
                    "model": model_name,
                    "pd_at_10db": float(np.interp(10.0, x, values)),
                    "pd_at_15db": float(np.interp(15.0, x, values)),
                    "pd_at_20db": float(np.interp(20.0, x, values)),
                    "output_scnr_at_pd_0p5_db": crossing(x, values, 0.5),
                    "output_scnr_at_pd_0p9_db": crossing(x, values, 0.9),
                    "support_cells": 15,
                    "reference_cell_index": truth_index,
                    "reference_cell_kappa": float(kappa[truth_index]),
                    "reference_cell_beta": float(beta[truth_index]),
                    "support_sample_count": profile["sample_count"],
                    "alpha": alpha,
                    "pfa": 1e-6,
                    "connectivity_factor": 1.0,
                    "output_to_profile_support_offset_db": float(10.0 * math.log10(profile_scale)),
                    "valid": True,
                })
    theory_path = out / "target_pd_poisson_binomial.csv"
    summary_path = out / "target_pd_poisson_binomial_summary.csv"
    write_csv(theory_path, rows)
    write_csv(summary_path, summary)
    return rows, summary


def fixed_m1_pd(cut_db: np.ndarray, alpha: float) -> np.ndarray:
    return ncx2.sf(2.0 * alpha, 2, 2.0 * (10.0 ** (np.asarray(cut_db) / 10.0)))


def crossing(x: Sequence[float], y: Sequence[float], level: float = 0.5) -> float:
    x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float)
    order = np.argsort(x); x, y = x[order], y[order]
    for i in range(1, len(x)):
        if y[i - 1] < level <= y[i]:
            if y[i] == y[i - 1]:
                return float(x[i])
            return float(x[i - 1] + (level - y[i - 1]) * (x[i] - x[i - 1]) /
                         (y[i] - y[i - 1]))
    if len(x) and y[0] >= level:
        return float(x[0])
    return float("nan")


def measured_crossing(rows: list[dict[str, object]], level: float = 0.9) -> tuple[float, str]:
    selected = sorted(rows, key=lambda r: fnum(r.get("output_scnr_db")))
    selected = [r for r in selected if math.isfinite(fnum(r.get("unit_exact_pd")))]
    for i, row in enumerate(selected):
        if fnum(row.get("unit_exact_pd")) >= level:
            if i == 0:
                return fnum(row.get("output_scnr_db")), "upper_bound_first_grid"
            prev = selected[i - 1]
            x0, x1 = fnum(prev.get("output_scnr_db")), fnum(row.get("output_scnr_db"))
            y0, y1 = fnum(prev.get("unit_exact_pd")), fnum(row.get("unit_exact_pd"))
            if y1 == y0:
                return x1, "linear_grid"
            return x0 + (level - y0) * (x1 - x0) / (y1 - y0), "linear_grid"
    return float("nan"), "not_reached_grid"


def make_go_geometry(out: Path) -> Path:
    g = Geometry(4); fig, ax = plt.subplots(figsize=(7.2, 6.5))
    colors = {"TL": "#8dd3c7", "TC": "#ffffb3", "TR": "#bebada", "LC": "#fb8072",
              "RC": "#80b1d3", "BL": "#fdb462", "BC": "#b3de69", "BR": "#fccde5"}
    h, o = 4, 20
    rectangles = {
        "TL": (-o, -o, 16, 16), "TC": (-o, -h, 16, 9), "TR": (-o, h + 1, 16, 16),
        "LC": (-h, -o, 9, 16), "RC": (-h, h + 1, 9, 16),
        "BL": (h + 1, -o, 16, 16), "BC": (h + 1, -h, 16, 9), "BR": (h + 1, h + 1, 16, 16),
    }
    for name, (x, y, w, ht) in rectangles.items():
        ax.add_patch(patches.Rectangle((x, y), w, ht, facecolor=colors[name], edgecolor="black", linewidth=.8))
        ax.text(x + w / 2, y + ht / 2, f"{name}\n{w}×{ht}", ha="center", va="center", fontsize=8)
    ax.add_patch(patches.Rectangle((-h, -h), 2 * h + 1, 2 * h + 1, facecolor="#eeeeee", edgecolor="black", hatch="//", alpha=.7))
    ax.text(0, 0, "guard 9×9\nCUT", ha="center", va="center", fontsize=9)
    ax.set_xlim(-21, 21); ax.set_ylim(-21, 21); ax.set_aspect("equal")
    ax.set_xlabel("range offset (cell)"); ax.set_ylabel("Doppler offset (cell)")
    ax.set_title("生产 GO-CFAR：41×41 外窗、9×9 保护窗、八个共享块")
    # The block-combination equation is explained in the adjacent report text;
    # keeping it out of the axes avoids colliding with the x-axis label in the
    # PDF rendering at small page sizes.
    fig.tight_layout(); path = out / "figures" / "go_geometry.png"; path.parent.mkdir(parents=True, exist_ok=True); fig.savefig(path); fig.savefig(path.with_suffix(".pdf")); plt.close(fig)
    return path


def build_theory(out: Path, mappings: dict[float, dict[str, float]], templates: dict[str, dict[str, object]],
                 windows: dict[str, dict[str, np.ndarray]], samples: int, seed: int,
                 grid: np.ndarray) -> tuple[list[dict[str, object]], dict[int, float], list[dict[str, object]], list[dict[str, object]], dict[int, np.ndarray]]:
    alpha_map: dict[int, float] = {}
    m_samples: dict[int, np.ndarray] = {}
    for guard in (3, 4, 5):
        geom = Geometry(guard)
        m = iid_m_samples(geom, samples, seed + guard)
        alpha = 13.44951031977817 if guard == 4 else calibrate_alpha(m, 1e-6)
        alpha_map[guard] = alpha; m_samples[guard] = m
    rows: list[dict[str, object]] = []
    gamma_cut_by_group = {}
    for group in GROUPS:
        angle = 0.0 if group.startswith("0deg") else 30.0 if group.startswith("30deg") else 45.0
        mp = mappings[angle]
        # ``grid`` is the canonical CUT-equivalent output SCNR axis.  The
        # paired support-to-CUT regression is used only for the diagnostic
        # support coordinate written below; it is no longer applied to the
        # primary x coordinate.
        cut_db = np.asarray(grid, dtype=float)
        support_db = (cut_db - mp["intercept_db"]) / mp["slope_db_per_db"]
        gamma_cut = 10.0 ** (cut_db / 10.0)
        gamma_cut_by_group[group] = gamma_cut
        for guard in (3, 4, 5):
            alpha = alpha_map[guard]
            a = averaged_pd(gamma_cut, alpha, m_samples[guard])
            template = templates.get(group)
            if template is not None and template["qualified_count"] > 0:
                b = noncentral_pd(gamma_cut, alpha, Geometry(guard), template["lambda"],
                                  max(20_000, min(samples, 50_000)), seed + 1000 * guard + int(round(angle * 10)))
            else:
                b = np.full_like(a, np.nan)
            for x, db, support_x, av, bv in zip(grid, cut_db, support_db, a, b):
                rows.append({"angle_group": group, "angle_deg": angle, "position": "edge" if is_edge_group(group) else "center",
                             "output_scnr_db": float(x), "support_output_scnr_db": float(support_x), "cut_scnr_db": float(db), "guard": guard,
                             "model": "A_IID_no_leak", "pd_unit": float(av), "alpha": alpha,
                             "sample_count": len(m_samples[guard]), "valid": True,
                             "note": "IID八块共享角块；guard=4使用生产alpha"})
                rows.append({"angle_group": group, "angle_deg": angle, "position": "edge" if is_edge_group(group) else "center",
                             "output_scnr_db": float(x), "support_output_scnr_db": float(support_x), "cut_scnr_db": float(db), "guard": guard,
                             "model": "B_IID_leak", "pd_unit": float(bv), "alpha": alpha,
                             "sample_count": max(20_000, min(samples, 50_000)),
                             "valid": bool(template is not None and template["qualified_count"] > 0),
                             "note": ("guard=4实测泄漏模板；guard=3/5为同模板反事实投影"
                                      if template is not None and template["qualified_count"] > 0
                                      else "该组无合格实测CUT泄漏模板，禁止断言")})
        # Fixed m=1 textbook curve is independent of guard only when alpha is
        # selected for that guard; production comparison is guard=4.
        fixed = fixed_m1_pd(cut_db, alpha_map[4])
        for x, db, support_x, pv in zip(grid, cut_db, support_db, fixed):
            rows.append({"angle_group": group, "angle_deg": angle, "position": "edge" if is_edge_group(group) else "center",
                         "output_scnr_db": float(x), "support_output_scnr_db": float(support_x), "cut_scnr_db": float(db), "guard": 4,
                         "model": "fixed_m1", "pd_unit": float(pv), "alpha": alpha_map[4],
                         "sample_count": 0, "valid": True, "note": "教材固定m=1，不对训练M积分"})

        # Real empirical C layer, guard=4 only.  The archived window CSV is
        # the retained evidence; current one-seed runs are thermal-only.
        if group in windows:
            base = windows[group]["normalized"]
            m = windows[group]["m"]
            for model, leak in (("C_real_CN", False), ("C_real_CN_leak", True)):
                template = templates.get(group)
                valid = not leak or (template is not None and template["qualified_count"] > 0)
                if leak and valid:
                    # Recompute M for each gamma without materialising an
                    # oversized 96k×141 tensor all at once.
                    c = np.asarray([float(np.mean(ncx2.sf(2.0 * alpha_map[4] * np.max(
                        base + gg * template["direction"][None, :], axis=1), 2, 2.0 * gg)))
                                    for gg in gamma_cut])
                else:
                    c = np.asarray([float(np.mean(ncx2.sf(2.0 * alpha_map[4] * m, 2, 2.0 * gg)))
                                    for gg in gamma_cut])
                for x, db, support_x, cv in zip(grid, cut_db, support_db, c):
                    rows.append({"angle_group": group, "angle_deg": angle, "position": "edge" if is_edge_group(group) else "center",
                                 "output_scnr_db": float(x), "support_output_scnr_db": float(support_x), "cut_scnr_db": float(db), "guard": 4,
                                 "model": model, "pd_unit": float(cv) if valid else float("nan"),
                                 "alpha": alpha_map[4], "sample_count": windows[group]["count"],
                                 "valid": valid,
                                 "note": ("每窗 M/CUT_background 的 Marcum-Q 经验积分"
                                          if not leak else "实测方向泄漏增量叠加；无合格模板时不计算")})
    write_csv(out / "theory_unit_go_curves.csv", rows)
    alpha_rows = []
    for guard, alpha in alpha_map.items():
        alpha_rows.append({"guard": guard, "background": 16, "outer_width": Geometry(guard).outer_width,
                           "guard_width": Geometry(guard).guard_width, "direction_cells": Geometry(guard).direction_count,
                           "block_counts": ",".join(map(str, Geometry(guard).block_counts.tolist())),
                           "alpha": alpha, "mc_pfa": float(np.mean(np.exp(-alpha * m_samples[guard]))),
                           "samples": len(m_samples[guard]), "production_guard": guard == 4})
    write_csv(out / "go_alpha_guard.csv", alpha_rows)
    shifts = []
    for group in GROUPS:
        def get(model):
            rr = [r for r in rows if r["angle_group"] == group and r["guard"] == 4 and r["model"] == model and r["valid"]]
            return np.asarray([r["output_scnr_db"] for r in rr]), np.asarray([r["pd_unit"] for r in rr])
        xa, ya = get("A_IID_no_leak"); xb, yb = get("B_IID_leak"); xc, yc = get("C_real_CN"); xd, yd = get("C_real_CN_leak")
        a50, b50, c50, d50 = crossing(xa, ya), crossing(xb, yb), crossing(xc, yc), crossing(xd, yd)
        shifts.append({"angle_group": group, "A_0p5_db": a50, "B_0p5_db": b50, "C_0p5_db": c50, "C_leak_0p5_db": d50,
                       "B_minus_A_db": b50 - a50 if math.isfinite(a50) and math.isfinite(b50) else float("nan"),
                       "C_minus_A_db": c50 - a50 if math.isfinite(a50) and math.isfinite(c50) else float("nan"),
                       "C_leak_minus_C_db": d50 - c50 if math.isfinite(c50) and math.isfinite(d50) else float("nan"),
                       "C_leak_minus_B_db": d50 - b50 if math.isfinite(b50) and math.isfinite(d50) else float("nan"),
                       "leakage_template_qualified": templates.get(group, {}).get("qualified_count", 0),
                       "valid": bool(math.isfinite(d50))})
    write_csv(out / "model_shift_summary.csv", shifts)
    return rows, alpha_map, alpha_rows, shifts, m_samples


def plot_theory(out: Path, rows: list[dict[str, object]], metrics: list[dict[str, object]], mappings: dict[float, dict[str, float]]) -> dict[str, Path]:
    figs = out / "figures"; figs.mkdir(exist_ok=True)
    paths: dict[str, Path] = {}
    for group in GROUPS:
        angle = 0.0 if group.startswith("0deg") else 30.0 if group.startswith("30deg") else 45.0
        fig, ax = plt.subplots(figsize=(8.2, 5.1))
        for model in ("fixed_m1", "A_IID_no_leak", "B_IID_leak", "C_real_CN", "C_real_CN_leak"):
            rr = [r for r in rows if r["angle_group"] == group and r["guard"] == 4 and r["model"] == model and r["valid"]]
            if rr:
                ax.plot([r["output_scnr_db"] for r in rr], [r["pd_unit"] for r in rr],
                        color=MODEL_COLORS[model], linestyle={"fixed_m1": "--", "A_IID_no_leak": "-", "B_IID_leak": ":", "C_real_CN": "-.", "C_real_CN_leak": (0, (3, 1, 1, 1))}[model],
                        label=MODEL_LABELS[model])
        if not is_edge_group(group):
            rr = [r for r in metrics if r["angle_deg"] == angle]
            rr = sorted(rr, key=lambda r: r["output_scnr_db"])
            ax.errorbar([r["output_scnr_db"] for r in rr], [r["unit_exact_pd"] for r in rr],
                        yerr=[np.asarray([r["unit_exact_pd"] - wilson_interval(r["unit_exact_hits"], r["unit_exact_trials"])[0] for r in rr]),
                              np.asarray([wilson_interval(r["unit_exact_hits"], r["unit_exact_trials"])[1] - r["unit_exact_pd"] for r in rr])],
                        fmt="o", color=COLORS[angle], markerfacecolor="white", capsize=2, label="实测 exact CUT Pd ± Wilson")
        ax.axhline(.9, color="#555", linestyle=":", linewidth=.8)
        ax.set(xlabel="输出 SCNR (dB)", ylabel="单元级 Pd", title=f"{GROUP_LABEL[group]}：GO 三层理论与 exact-CUT 实测")
        ax.set_ylim(-.02, 1.03); ax.legend(fontsize=7); fig.tight_layout()
        p = figs / f"go_layers_{group}.png"; fig.savefig(p); fig.savefig(p.with_suffix(".pdf")); plt.close(fig); paths[f"go_{group}"] = p

    # Guard sweep is shown separately so the production guard marker is clear.
    fig, ax = plt.subplots(figsize=(8.0, 4.8))
    group = "0deg_center"
    for guard, color in ((3, "#777777"), (4, "#d62728"), (5, "#9467bd")):
        rr = [r for r in rows if r["angle_group"] == group and r["guard"] == guard and r["model"] == "A_IID_no_leak"]
        ax.plot([r["output_scnr_db"] for r in rr], [r["pd_unit"] for r in rr], color=color,
                label=f"A IID guard={guard}" + ("（生产）" if guard == 4 else ""))
    ax.set(xlabel="输出 SCNR (dB)", ylabel="unit Pd", title="GO guard=3/4/5：IID无泄漏曲线；guard=4为生产配置")
    ax.set_ylim(-.02, 1.03); ax.legend(); fig.tight_layout()
    p = figs / "go_guard_sweep.png"; fig.savefig(p); fig.savefig(p.with_suffix(".pdf")); plt.close(fig); paths["guard_sweep"] = p

    # Output-to-CUT calibration.
    fig, axes = plt.subplots(1, 3, figsize=(11.5, 3.8), sharey=True)
    for ax, angle in zip(axes, ANGLES):
        path = Path(mappings[angle]["source"])
        x = np.asarray([fnum(r.get("unit_cut_scnr_db")) for r in read_csv(path)])
        y = np.asarray([fnum(r.get("processed_output_scnr_db")) for r in read_csv(path)])
        ax.scatter(x, y, color=COLORS[angle], s=22, label="10 个配对标定点")
        xx = np.linspace(np.min(x), np.max(x), 100)
        ax.plot(xx, (xx - mappings[angle]["intercept_db"]) / mappings[angle]["slope_db_per_db"], "k--", label="线性回归")
        ax.set_title(f"{angle:g}°：R²={mappings[angle]['r2']:.5f}"); ax.set_xlabel("CUT-equivalent output SCNR (dB)"); ax.grid(True, alpha=.25)
    axes[0].set_ylabel("3×3 支撑诊断 SCNR (dB)"); axes[0].legend(fontsize=7)
    fig.suptitle("主轴与支撑诊断的配对关系（CUT-equivalent → 3×3 support）"); fig.tight_layout()
    p = figs / "output_to_cut_mapping.png"; fig.savefig(p); fig.savefig(p.with_suffix(".pdf")); plt.close(fig); paths["mapping"] = p

    # Real-window tail diagnostic.
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    for group in GROUPS:
        if group in windows_for_plot:
            vals = windows_for_plot[group]["m"]
            ax.hist(np.log10(np.maximum(vals, 1e-12)), bins=80, density=True, histtype="step", label=GROUP_LABEL[group])
    ax.set(xlabel=r"$log_{10}(M/CUT_{background})$", ylabel="density", title="真实 C+N 训练窗的 GO 最大方向比值：长尾诊断")
    ax.legend(fontsize=7); fig.tight_layout()
    p = figs / "real_window_m_distribution.png"; fig.savefig(p); fig.savefig(p.with_suffix(".pdf")); plt.close(fig); paths["window_distribution"] = p

    # Leakage shift bars.
    shifts = list(read_csv(out / "model_shift_summary.csv"))
    fig, ax = plt.subplots(figsize=(8.0, 4.4))
    labels = [GROUP_LABEL.get(r["angle_group"], r["angle_group"]) for r in shifts]
    vals = [fnum(r.get("C_leak_minus_C_db")) for r in shifts]
    valid = np.isfinite(vals)
    ax.bar(np.arange(len(labels))[valid], np.asarray(vals)[valid], color="#2ca02c")
    ax.set_xticks(np.arange(len(labels))); ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.set_ylabel("C+leak 相对 C 的 0.5-Pd 右移 (dB)"); ax.set_title("实测目标泄漏右移；无合格模板处留空")
    for i, v in enumerate(vals):
        if math.isfinite(v): ax.text(i, v, f"{v:.2f}", ha="center", va="bottom")
    fig.tight_layout(); p = figs / "model_shift_bars.png"; fig.savefig(p); fig.savefig(p.with_suffix(".pdf")); plt.close(fig); paths["shifts"] = p
    return paths


def go_selected_tables(theory_rows: list[dict[str, object]],
                       metrics: list[dict[str, object]]) -> dict[str, str]:
    """Make compact tables immediately adjacent to every GO-layer figure.

    The complete 0.5-dB grid remains in ``theory_unit_go_curves.csv``.  The
    theory anchor rows are selected from calculated grid points, never
    interpolated or fit to measured hit rates.  Measured rows retain their
    actual output-SCNR coordinate; the theory columns on those rows use the
    nearest 0.5-dB grid point only for a readable side-by-side comparison.
    """
    selected_output = (6.0, 10.0, 15.0, 20.0)
    result: dict[str, str] = {}
    for group in GROUPS:
        group_rows = [r for r in theory_rows if r["angle_group"] == group
                      and int(r["guard"]) == 4]
        if not group_rows:
            result[group] = "—"
            continue
        grid_values = sorted({fnum(r.get("output_scnr_db")) for r in group_rows})
        table_rows = []
        angle = 0.0 if group.startswith("0deg") else 30.0 if group.startswith("30deg") else 45.0
        measured = sorted([r for r in metrics if fnum(r.get("angle_deg")) == angle],
                          key=lambda r: fnum(r.get("output_scnr_db")))
        for requested in selected_output:
            x = min(grid_values, key=lambda value: abs(value - requested))
            by_model = {str(r["model"]): r for r in group_rows
                        if abs(fnum(r.get("output_scnr_db")) - x) < 1e-9}
            table_rows.append([
                fmt(x, 1),
                fmt(by_model.get("fixed_m1", {}).get("pd_unit"), 3),
                fmt(by_model.get("A_IID_no_leak", {}).get("pd_unit"), 3),
                fmt(by_model.get("B_IID_leak", {}).get("pd_unit"), 3),
                fmt(by_model.get("C_real_CN", {}).get("pd_unit"), 3),
                fmt(by_model.get("C_real_CN_leak", {}).get("pd_unit"), 3),
                "—",
            ])
        if measured and not is_edge_group(group):
            for measured_row in measured:
                xm = fnum(measured_row.get("output_scnr_db"))
                xt = min(grid_values, key=lambda value: abs(value - xm))
                by_model = {str(r["model"]): r for r in group_rows
                            if abs(fnum(r.get("output_scnr_db")) - xt) < 1e-9}
                table_rows.append([
                    f"实测 {fmt(xm, 2)}",
                    fmt(by_model.get("fixed_m1", {}).get("pd_unit"), 3),
                    fmt(by_model.get("A_IID_no_leak", {}).get("pd_unit"), 3),
                    fmt(by_model.get("B_IID_leak", {}).get("pd_unit"), 3),
                    fmt(by_model.get("C_real_CN", {}).get("pd_unit"), 3),
                    fmt(by_model.get("C_real_CN_leak", {}).get("pd_unit"), 3),
                    fmt(measured_row.get("unit_exact_pd"), 3),
                ])
        result[group] = md_table(
            ["output SCNR(dB)", "教材 m=1", "A IID", "B IID+leak",
             "C real C+N", "C+leak", "实测 exact"], table_rows)
    return result


# This global is populated by main before plot_theory; keeping it separate
# avoids copying 96k rows into every plotting call.
windows_for_plot: dict[str, dict[str, np.ndarray]] = {}


def write_data_tables(out: Path, metrics_by_min: dict[int, list[dict[str, object]]], dependencies: list[dict[str, object]],
                      mappings: dict[float, dict[str, float]], alpha_rows: list[dict[str, object]],
                      theory_rows: list[dict[str, object]], shifts: list[dict[str, object]]) -> tuple[Path, Path, Path]:
    merged = []
    for minimum, rows in metrics_by_min.items():
        for row in rows:
            merged.append({"min_points": minimum, "angle_deg": row["angle_deg"], "output_scnr_db": row["output_scnr_db"],
                           "support_output_scnr_db": fnum(row.get("support_output_scnr_db")),
                           "input_snr_db_control": fnum(row.get("input_snr_db_control")),
                           "unit_exact_hits": row["unit_exact_hits"], "unit_exact_trials": row["unit_exact_trials"], "unit_exact_pd": row["unit_exact_pd"],
                           "unit_resolution_pd": row["unit_resolution_pd"], "cluster_pd": row["cluster_pd"], "target_pd": row["target_pd"],
                           "target_hits": row["target_hits"], "target_trials": row["target_trials"], "track_pd": row["track_pd"],
                           "track_hits": row["track_hits"], "track_trials": row["track_trials"], "angle_rmse_deg": row["angle_rmse_deg"],
                           "angle_bias_deg": row["angle_bias_deg"]})
    path = out / "unit_target_track_summary.csv"; write_csv(path, merged)
    write_csv(out / "track_dependency_summary.csv", dependencies)
    map_path = out / "output_to_cut_mapping.csv"; write_csv(map_path, mappings.values())
    write_csv(out / "go_alpha_guard.csv", alpha_rows)
    write_csv(out / "theory_unit_go_curves.csv", theory_rows)
    write_csv(out / "model_shift_summary.csv", shifts)
    return path, map_path, out / "track_dependency_summary.csv"


def write_doppler_max_worked_example(out: Path) -> Path:
    """Persist the numerical values used in the report's max-search derivation."""
    rows = [
        {"scope": "single_bin_selfcheck", "quantity": "P_FA", "symbol": "P_FA", "value": 1e-6, "unit": "probability", "note": "本专项冻结虚警概率"},
        {"scope": "single_bin_selfcheck", "quantity": "training_range_cells", "symbol": "N", "value": 16, "unit": "cells", "note": "单指数训练和自检"},
        {"scope": "single_bin_selfcheck", "quantity": "threshold_coefficient", "symbol": "c0", "value": 1.371373705661655, "unit": "linear", "note": "P_FA=(1+c)^(-N)"},
        {"scope": "single_bin_selfcheck", "quantity": "mean_threshold_ratio", "symbol": "N*c0", "value": 21.94197929058648, "unit": "linear", "note": "训练均值口径"},
        {"scope": "doppler_max_side_example", "quantity": "doppler_bins", "symbol": "K", "value": 32, "unit": "bins", "note": "仅解释性旁算例"},
        {"scope": "doppler_max_side_example", "quantity": "range_cells", "symbol": "N", "value": 16, "unit": "cells", "note": "每个D_j独立"},
        {"scope": "doppler_max_side_example", "quantity": "mean_max", "symbol": "E[D]=H_K", "value": 4.05849519543652, "unit": "linear", "note": "调和数 H_32"},
        {"scope": "doppler_max_side_example", "quantity": "naive_mean_threshold", "symbol": "N*c0*E[D]", "value": 89.05141752921286, "unit": "linear", "note": "错误复用c0的门限"},
        {"scope": "doppler_max_side_example", "quantity": "naive_vs_single_shift", "symbol": "10log10(T_naive/T_1bin)", "value": 6.083650361731151, "unit": "dB", "note": "错误口径产生的抬升"},
        {"scope": "doppler_max_side_example", "quantity": "exact_threshold_coefficient", "symbol": "c_star", "value": 0.2798789722891651, "unit": "linear", "note": "Beta/Laplace展开求根"},
        {"scope": "doppler_max_side_example", "quantity": "mean_normalized_coefficient", "symbol": "alpha_search=N*c_star", "value": 4.478063556626641, "unit": "linear", "note": "旁算例，不是生产alpha"},
        {"scope": "doppler_max_side_example", "quantity": "root_check_P_FA", "symbol": "P_FA_search(c_star)", "value": 9.999999999998428e-7, "unit": "probability", "note": "数值根复核"},
        {"scope": "gmtI_production_go", "quantity": "guard_half_width", "symbol": "g", "value": 4, "unit": "cells", "note": "生产配置"},
        {"scope": "gmtI_production_go", "quantity": "background_half_width", "symbol": "b", "value": 16, "unit": "cells", "note": "生产配置"},
        {"scope": "gmtI_production_go", "quantity": "direction_denominator", "symbol": "n_dir", "value": 656, "unit": "cells", "note": "b*(2*(g+b)+1)"},
        {"scope": "gmtI_production_go", "quantity": "alpha_guard4", "symbol": "alpha_4", "value": 13.44951031977817, "unit": "linear", "note": "共享八块M的E[exp(-alpha*M)]=1e-6"},
        {"scope": "gmtI_production_go", "quantity": "iid_M_mean", "symbol": "E[M]", "value": 1.03383, "unit": "linear", "note": "60000样本近似"},
        {"scope": "gmtI_production_go", "quantity": "iid_M_median", "symbol": "median(M)", "value": 1.03278, "unit": "linear", "note": "60000样本近似"},
        {"scope": "gmtI_production_go", "quantity": "iid_M_q90", "symbol": "q0.90(M)", "value": 1.07478, "unit": "linear", "note": "60000样本近似"},
        {"scope": "pd_worked_point", "quantity": "output_scnr", "symbol": "SCNR_out,CUT", "value": 10.0, "unit": "dB", "note": "曲线复算点"},
        {"scope": "pd_worked_point", "quantity": "gamma_cut", "symbol": "gamma_CUT", "value": 10.0, "unit": "linear", "note": "10^(10/10)"},
        {"scope": "pd_worked_point", "quantity": "fixed_m1_pd", "symbol": "P_D,fixed", "value": 0.27099131788670033, "unit": "probability", "note": "教材固定m=1"},
        {"scope": "pd_worked_point", "quantity": "iid_go_pd", "symbol": "P_D,A", "value": 0.24359910768725154, "unit": "probability", "note": "共享八块M积分"},
        {"scope": "pd_worked_point", "quantity": "real_cn_pd", "symbol": "P_D,C", "value": 0.1543467619881767, "unit": "probability", "note": "24000个真实C+N窗"},
        {"scope": "pd_worked_point", "quantity": "real_cn_leak_pd", "symbol": "P_D,C+leak", "value": 0.15412068549304603, "unit": "probability", "note": "加入实测泄漏增量"},
    ]
    path = out / "doppler_max_worked_example.csv"
    write_csv(path, rows)
    return path


def target_plot(out: Path, minimum: int, rows: list[dict[str, object]]) -> Path:
    fig, ax = plt.subplots(figsize=(8.2, 5.0))
    for angle in ANGLES:
        rr = sorted([r for r in rows if r["angle_deg"] == angle], key=lambda r: r["output_scnr_db"])
        if not rr: continue
        x = np.asarray([r["output_scnr_db"] for r in rr]); y = np.asarray([r["target_pd"] for r in rr])
        lo = np.asarray([wilson_interval(r["target_hits"], r["target_trials"])[0] for r in rr])
        hi = np.asarray([wilson_interval(r["target_hits"], r["target_trials"])[1] for r in rr])
        ax.plot(x, y, "o-", color=COLORS[angle], markerfacecolor="white", label=f"{ANGLE_LABEL[angle]}（每点 n=15）")
        ax.fill_between(x, lo, hi, color=COLORS[angle], alpha=.12)
    ax.axhline(.9, color="#555", linestyle=":", linewidth=.8)
    ax.set(xlabel="输出 SCNR (dB)", ylabel="目标级 truth Pd", title=f"min_points={minimum}：目标级检测概率（独立分图）")
    ax.set_ylim(-.02, 1.03); ax.legend(fontsize=8); fig.tight_layout()
    p = out / "figures" / f"target_pd_minpoints_{minimum}.png"; fig.savefig(p); fig.savefig(p.with_suffix(".pdf")); plt.close(fig)
    return p


def funnel_plot(out: Path, minimum: int, rows: list[dict[str, object]]) -> Path:
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8), sharey=True)
    for ax, angle in zip(axes, ANGLES):
        rr = sorted([r for r in rows if r["angle_deg"] == angle], key=lambda r: r["output_scnr_db"])
        x = np.asarray([r["output_scnr_db"] for r in rr])
        for key, label, color in (("unit_exact_pd", "unit exact", "#1f77b4"), ("unit_resolution_pd", "unit ±2bin", "#9467bd"),
                                   ("cluster_pd", "cluster", "#ff7f0e"), ("target_pd", "target", "#2ca02c")):
            ax.plot(x, [r[key] for r in rr], "o-", label=label, color=color, markerfacecolor="white")
        ax.set_title(f"{angle:g}°"); ax.set_xlabel("output SCNR (dB)"); ax.set_ylim(-.02, 1.03)
    axes[0].set_ylabel("Pd / retention"); axes[0].legend(fontsize=7)
    fig.suptitle(f"min_points={minimum}：单元→分辨率→cluster→target 漏斗"); fig.tight_layout()
    p = out / "figures" / f"funnel_minpoints_{minimum}.png"; fig.savefig(p); fig.savefig(p.with_suffix(".pdf")); plt.close(fig); return p


def track_plot(out: Path, minimum: int, rows: list[dict[str, object]]) -> Path:
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    # The main track plot uses the requested output-SCNR axis.  The direct
    # three-screen event is retained; the independent expression is an
    # auxiliary curve made from the simultaneously measured target Pd.
    for angle in ANGLES:
        rr = sorted([r for r in rows if r["angle_deg"] == angle], key=lambda r: r["output_scnr_db"])
        if not rr: continue
        x = np.asarray([r["output_scnr_db"] for r in rr]); y = np.asarray([r["track_pd"] for r in rr])
        p = np.asarray([r["target_pd"] for r in rr]); baseline = 3 * p * p - 2 * p ** 3
        ax.plot(x, y, "o-", color=COLORS[angle], markerfacecolor="white", label=f"{angle:g}° 实测 2/3")
        ax.plot(x, baseline, "--", color=COLORS[angle], alpha=.75, label=f"{angle:g}° 独立辅助式")
    ax.set(xlabel="输出 SCNR (dB)", ylabel="三屏 2-of-3 Pd", title=f"min_points={minimum}：输出 SCNR—航迹 Pd")
    ax.set_ylim(-.02, 1.03); ax.legend(fontsize=7, ncol=2); fig.tight_layout()
    p = out / "figures" / f"track_pd_minpoints_{minimum}.png"; fig.savefig(p); fig.savefig(p.with_suffix(".pdf")); plt.close(fig); return p


def plot_target_pb(out: Path, rows: list[dict[str, object]],
                   metrics_by_min: dict[int, list[dict[str, object]]] | None = None) -> dict[int, Path]:
    """Plot the calibration-only Poisson-binomial cluster baseline.

    Each min_points value gets its own figure so that the baseline is not
    confused with the three separate production target plots.  If supplied,
    black crosses are the current one-seed production target events; they are
    an overlay, never a fit to the calibration curves.
    """
    figures = out / "figures"; figures.mkdir(parents=True, exist_ok=True)
    paths: dict[int, Path] = {}
    profile_groups = GROUPS
    for minimum in (2, 3, 6):
        fig, ax = plt.subplots(figsize=(8.2, 4.9))
        for group in profile_groups:
            rr = sorted([r for r in rows if r["angle_group"] == group
                         and int(r["min_points"]) == minimum],
                        key=lambda r: r["output_scnr_db"])
            if not rr:
                continue
            color = COLORS.get(fnum(rr[0]["mapping_angle_deg"]), "#777777")
            linestyle = "--" if is_edge_group(group) else "-"
            label = f"{GROUP_LABEL[group]} PB η=1"
            ax.plot([r["output_scnr_db"] for r in rr],
                    [r["pd_cluster_pb_profile"] for r in rr],
                    color=color, linestyle=linestyle, linewidth=1.8, label=label)
            # Only center groups have a matching current evaluation angle.
            if metrics_by_min is not None and not is_edge_group(group):
                angle = fnum(rr[0]["mapping_angle_deg"])
                measured = sorted([m for m in metrics_by_min.get(minimum, [])
                                   if fnum(m.get("angle_deg")) == angle],
                                  key=lambda m: fnum(m.get("output_scnr_db")))
                if measured:
                    ax.plot([fnum(m.get("output_scnr_db")) for m in measured],
                            [fnum(m.get("target_pd")) for m in measured],
                            "x", color=color, markersize=5,
                            label=f"{angle:g}° 生产 target（one-seed）")
        ax.axhline(.9, color="#555", linestyle=":", linewidth=.8)
        ax.set(xlabel="输出 SCNR (dB)", ylabel="目标级点数 baseline Pd",
               title=f"Poisson-binomial 目标级 baseline：min_points={minimum}（η_connectivity=1）")
        ax.set_ylim(-.02, 1.03); ax.legend(fontsize=7); fig.tight_layout()
        p = figures / f"target_pb_minpoints_{minimum}.png"
        fig.savefig(p); fig.savefig(p.with_suffix(".pdf")); plt.close(fig); paths[minimum] = p
    return paths


def target_profile_shape_plot(out: Path,
                              profiles: dict[str, dict[str, object]]) -> Path:
    """Plot the calibration-only 3×5 signal/background footprint.

    The figure is intentionally made from the compact S-only/C+N-only
    profile CSV, rather than from an evaluation hit mask.  Showing the
    normalized ``kappa`` and ``beta`` matrices makes the min_points=6 issue
    immediately inspectable: a point target whose signal mass is concentrated
    in the centre cell cannot be treated as six equally likely support hits.
    """
    figures = out / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    # Six groups fit more legibly as two 3-column blocks: the first block is
    # the 0/30/45° centers and the second is the corresponding edges.  Missing
    # profiles remain visible and are labelled rather than silently replaced.
    columns = list(GROUPS)
    ncols = 3
    fig, axes = plt.subplots(4, ncols, figsize=(10.8, 10.0),
                             squeeze=False, constrained_layout=True)
    kappa_axes = []
    beta_axes = []
    kappa_values = [np.maximum(np.asarray(profiles[group]["kappa"], dtype=float), 0.0)
                    for group in columns if group in profiles]
    beta_values = [np.maximum(np.asarray(profiles[group]["beta"], dtype=float), 1e-12)
                   for group in columns if group in profiles]
    kappa_vmax = max(0.80, *(float(np.max(value / max(float(np.sum(value)), 1e-30)))
                            for value in kappa_values)) if kappa_values else 0.80
    beta_vmax = max(1.10, *(float(np.max(value / max(float(np.median(value)), 1e-12)))
                           for value in beta_values)) if beta_values else 1.10
    kappa_image = beta_image = None
    for index, group in enumerate(columns):
        block_row, col = (index // ncols) * 2, index % ncols
        profile = profiles.get(group)
        if profile is None:
            for row in range(2):
                axes[block_row + row, col].axis("off")
                axes[block_row + row, col].set_title(
                    f"{GROUP_LABEL.get(group, group)}\n无校准 profile")
            continue
        kappa = np.maximum(np.asarray(profile["kappa"], dtype=float), 0.0)
        kappa /= max(float(np.sum(kappa)), 1e-30)
        beta = np.maximum(np.asarray(profile["beta"], dtype=float), 1e-12)
        beta /= max(float(np.median(beta)), 1e-12)
        kappa = kappa.reshape(3, 5)
        beta = beta.reshape(3, 5)
        kappa_ax, beta_ax = axes[block_row, col], axes[block_row + 1, col]
        kappa_axes.append(kappa_ax)
        beta_axes.append(beta_ax)
        kappa_image = kappa_ax.imshow(kappa, cmap="magma", vmin=0.0,
                                      vmax=kappa_vmax)
        beta_image = beta_ax.imshow(beta, cmap="viridis", vmin=0.8,
                                    vmax=beta_vmax)
        for row, matrix, fmt_string in ((0, kappa, "{:.1%}"),
                                        (1, beta, "{:.2f}")):
            for rr in range(3):
                for cc in range(5):
                    value = float(matrix[rr, cc])
                    axes[block_row + row, col].text(
                        cc, rr, fmt_string.format(value), ha="center",
                        va="center", fontsize=7,
                        color="white" if (row == 0 and value > 0.25)
                                        or (row == 1 and value > 1.02) else "black")
        kappa_ax.set_title(GROUP_LABEL.get(group, group))
        beta_ax.set_title("β：背景相对系数")
        for row in range(2):
            panel = axes[block_row + row, col]
            panel.set_xticks(range(5), labels=["-2", "-1", "0", "+1", "+2"])
            panel.set_yticks(range(3), labels=["-1", "0", "+1"])
            panel.set_xlabel("range offset")
            panel.set_ylabel("Doppler offset")
    # Use one representative mappable per row.  All κ panels share the same
    # physical [0, 0.8] scale and all β panels share [0.8, max], so a single
    # colorbar is sufficient and does not crowd the six heatmaps.
    if kappa_axes and kappa_image is not None:
        fig.colorbar(kappa_image, ax=kappa_axes, shrink=.78, pad=.02,
                     label="κ signal-energy fraction")
    if beta_axes and beta_image is not None:
        fig.colorbar(beta_image, ax=beta_axes, shrink=.78, pad=.02,
                     label="β / median(β)")
    fig.suptitle("校准目标支撑形状：3×5 κ（信号占比）与 β（背景相对系数）",
                 fontsize=12)
    path = figures / "target_support_profile_shapes.png"
    fig.savefig(path)
    fig.savefig(path.with_suffix(".pdf"))
    plt.close(fig)
    return path


def angle_theory_curves(out: Path, mappings: dict[float, dict[str, float]], metrics_by_min: dict[int, list[dict[str, object]]]) -> dict[int, Path]:
    # Physical values used in the module audit and gmti.xml.
    fc = 16.472113076923076e9; wavelength = 299792458.0 / fc; d = .17; v = 60.0
    grid = np.arange(0.0, 35.01, .25)
    result_rows = []
    paths = {}
    for angle in ANGLES:
        j = wavelength / (2 * math.pi * d * math.cos(math.radians(angle)))
        mp = mappings[angle]
        # The angle model is parameterized by the same physical CUT-
        # equivalent output SCNR used by the GO curves.
        cut_db = grid.copy()
        support_db = (cut_db - mp["intercept_db"]) / mp["slope_db_per_db"]
        gamma = 10 ** (cut_db / 10)
        sigma_phase = 1 / np.sqrt(gamma)
        rmse = np.degrees(j * sigma_phase)
        for x, c, support_x, r in zip(grid, cut_db, support_db, rmse):
            result_rows.append({"angle_deg": angle, "output_scnr_db": x, "cut_scnr_db": c, "wavelength_m": wavelength,
                                "support_output_scnr_db": support_x,
                                "d_chan_m": d, "platform_speed_mps": v, "j_theta_phi_rad_per_rad": j,
                                "phase_sigma_rad": 1 / math.sqrt(10 ** (c / 10)), "ideal_angle_rmse_deg": r,
                                "rmse_target_deg": .2})
    write_csv(out / "angle_theory_curves.csv", result_rows)
    for minimum, rows in metrics_by_min.items():
        fig, axes = plt.subplots(1, 3, figsize=(12, 3.8), sharey=True)
        for ax, angle in zip(axes, ANGLES):
            rr = [r for r in result_rows if r["angle_deg"] == angle]
            ax.plot([r["output_scnr_db"] for r in rr], [r["ideal_angle_rmse_deg"] for r in rr], "k--", label="理想 Jacobian")
            measured = sorted([r for r in rows if r["angle_deg"] == angle and math.isfinite(r["angle_rmse_deg"])], key=lambda r: r["output_scnr_db"])
            if measured:
                ax.plot([r["output_scnr_db"] for r in measured], [r["angle_rmse_deg"] for r in measured], "o-", color=COLORS[angle], markerfacecolor="white", label="生产 truth-match RMSE")
            ax.axhline(.2, color="#d62728", linestyle=":", linewidth=.8); ax.set_title(f"{angle:g}°"); ax.set_xlabel("output SCNR (dB)"); ax.set_ylim(bottom=0)
        axes[0].set_ylabel("angle RMSE (deg)"); axes[0].legend(fontsize=7)
        fig.suptitle(f"min_points={minimum}：输出 SCNR—测角 RMSE；低匹配数点仅作诊断"); fig.tight_layout()
        p = out / "figures" / f"angle_rmse_minpoints_{minimum}.png"; fig.savefig(p); fig.savefig(p.with_suffix(".pdf")); plt.close(fig); paths[minimum] = p
    return paths


def copy_angle_modules(out: Path) -> dict[str, Path]:
    src = ROOT / "outputs/gmti_scnr_eval/angle_modules_20260827"
    dst = out / "angle_modules"; dst.mkdir(exist_ok=True)
    result = {}
    for name in ("angle_module_phase_noise.png", "angle_module_p38.png", "angle_module_projection.png", "angle_module_summary.csv",
                 "angle_module_phase_noise.csv", "angle_module_p38.csv", "angle_module_projection_roundtrip.csv", "angle_module_validation_report.md"):
        path = src / name
        if path.is_file():
            target = dst / name; shutil.copy2(path, target); result[name] = target
    return result


def copy_multiseed_figures(out: Path) -> dict[str, Path]:
    """Copy the five audited min6 three-seed plots into the main report."""
    src = MULTISEED_SUMMARY_PATH.parent
    dst = out / "figures"
    dst.mkdir(parents=True, exist_ok=True)
    names = {
        "unit": "output_scnr_vs_unit_pd_min6_3seed.png",
        "cluster": "output_scnr_vs_cluster_pd_min6_3seed.png",
        "target": "output_scnr_vs_target_pd_min6_3seed.png",
        "track": "output_scnr_vs_track_pd_min6_3seed.png",
        "angle": "output_scnr_vs_angle_rmse_min6_3seed.png",
    }
    result: dict[str, Path] = {}
    for key, name in names.items():
        path = src / name
        if path.is_file():
            target = dst / f"multiseed_min6_{key}.png"
            shutil.copy2(path, target)
            result[key] = target
    return result


def load_scn_multiseed_summary(path: Path) -> list[dict[str, object]]:
    """Load the compact-statistical S+C+N pooled validation, if present."""
    if not path.is_file():
        return []
    result: list[dict[str, object]] = []
    for raw in read_csv(path):
        row = dict(raw)
        for key in (
            "angle_deg", "requested_output_scnr_db", "output_scnr_db", "output_scnr_cut_equiv_db", "support_output_scnr_db", "output_scnr_db_std",
            "unit_exact_pd", "unit_resolution_pd", "cluster_pd", "target_pd", "track_pd",
            "angle_rmse_deg", "angle_bias_deg", "postcluster_combined_retention",
            "screen_pd", "p_dt1_given_dt0_1", "p_dt1_given_dt0_0", "screen_pair_correlation",
            "track_pd_direct", "track_pd_inclusion_exclusion", "track_pd_independent",
            "theory_unit_pd_iid_go", "theory_unit_pd_empirical_n_only", "theory_unit_cut_scnr_db",
        ):
            row[key] = fnum(row.get(key))
        for key in (
            "level_index", "seed_count", "unit_exact_hits", "unit_exact_trials",
            "unit_resolution_hits", "unit_resolution_trials", "cluster_hits", "cluster_trials",
            "target_hits", "target_trials", "track_hits", "track_trials", "screen_count", "angle_n",
        ):
            row[key] = int(round(fnum(row.get(key), 0)))
        result.append(row)
    return sorted(result, key=lambda r: (fnum(r.get("angle_deg")), fnum(r.get("output_scnr_db"))))


def load_scn_seed_background_stats(manifest_path: Path) -> list[dict[str, object]]:
    """Summarize retained C+N-only support-background heterogeneity per seed.

    The compact S+C+N validation uses only a small number of periods per seed.
    A single lognormal-area-clutter realization can therefore dominate the
    ensemble-mean P0 used for the output-SCNR axis.  This diagnostic reads the
    retained ``p0_ensemble.csv`` and angle manifests only; it never opens raw
    maps and never removes an outlier from the experiment.
    """
    if not manifest_path.is_file():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    roots = manifest.get("roots", [])
    if not isinstance(roots, list):
        return []
    result: list[dict[str, object]] = []
    for root_value in roots:
        root = Path(str(root_value))
        for angle in ANGLES:
            angle_dir = root / token(angle)
            p0_path = angle_dir / "p0_ensemble.csv"
            angle_manifest_path = angle_dir / "manifest.json"
            if not p0_path.is_file():
                continue
            values = np.asarray([fnum(row.get("p0_power")) for row in read_csv(p0_path)], dtype=float)
            values = values[np.isfinite(values) & (values > 0.0)]
            if values.size == 0:
                continue
            median = float(np.median(values))
            mean = float(np.mean(values))
            p95 = float(np.percentile(values, 95.0))
            maximum = float(np.max(values))
            angle_manifest = json.loads(angle_manifest_path.read_text(encoding="utf-8")) if angle_manifest_path.is_file() else {}
            result.append({
                "seed": int(round(fnum(angle_manifest.get("seed"), -1))),
                "angle_deg": angle,
                "sample_count": int(values.size),
                "p0_support_mean": mean,
                "p0_support_median": median,
                "p0_support_p95": p95,
                "p0_support_max": maximum,
                "p0_max_to_median": maximum / median if median > 0.0 else float("nan"),
                "p0_outlier_count_gt5x_median": int(np.sum(values > 5.0 * median)) if median > 0.0 else 0,
                "p0_center_mean": fnum(angle_manifest.get("p0_center_mean_power")),
                "calibration_sanity_pass": bool(angle_manifest.get("calibration_sanity_pass")),
                "calibration_slope": fnum(angle_manifest.get("calibration_slope")),
                "calibration_r2": fnum(angle_manifest.get("calibration_r2")),
                "calibration_max_abs_bias_db": fnum(angle_manifest.get("calibration_max_abs_bias_db")),
                "source": str(p0_path.resolve()),
            })
    return sorted(result, key=lambda r: (int(r.get("seed", -1)), fnum(r.get("angle_deg"))))


def copy_scn_multiseed_figures(out: Path) -> dict[str, Path]:
    """Copy current S+C+N plots into the comprehensive report evidence tree."""
    if not SCN_MULTISEED_DIR.is_dir():
        return {}
    destination = out / "figures"
    destination.mkdir(parents=True, exist_ok=True)
    names = {
        "unit_exact": "output_scnr_vs_unit_pd_exact_scn.png",
        "unit_resolution": "output_scnr_vs_unit_pd_resolution_scn.png",
        "cluster": "output_scnr_vs_cluster_pd_scn.png",
        "target": "output_scnr_vs_target_pd_scn.png",
        "track": "output_scnr_vs_track_pd_scn.png",
        "angle": "output_scnr_vs_angle_rmse_scn.png",
    }
    copied: dict[str, Path] = {}
    for key, name in names.items():
        source = SCN_MULTISEED_DIR / name
        if source.is_file():
            target = destination / f"scn_{key}.png"
            shutil.copy2(source, target)
            copied[key] = target
    return copied


def load_scn_minpoints_validation() -> tuple[list[dict[str, object]], dict[str, object]]:
    """Load the same-seed S+C+N min_points=2/3/6 comparison, if present."""
    if not SCN_MINPOINTS_SUMMARY_PATH.is_file() or not SCN_MINPOINTS_AUDIT_PATH.is_file():
        return [], {}
    rows: list[dict[str, object]] = []
    for raw in read_csv(SCN_MINPOINTS_SUMMARY_PATH):
        row = dict(raw)
        for key in ("min_points", "angle_deg", "level_index", "output_scnr_db", "output_scnr_db_std",
                    "unit_exact_pd", "unit_resolution_pd", "cluster_pd", "target_pd", "track_pd",
                    "angle_rmse_deg", "angle_bias_deg", "theory_unit_pd_iid_go",
                    "theory_unit_pd_empirical_n_only", "theory_unit_cut_scnr_db", "go_alpha",
                    "postcluster_combined_retention"):
            row[key] = fnum(row.get(key))
        for key in ("unit_exact_hits", "unit_exact_trials", "unit_resolution_hits", "unit_resolution_trials",
                    "cluster_hits", "cluster_trials", "target_hits", "target_trials", "track_hits", "track_trials",
                    "angle_matched"):
            row[key] = int(round(fnum(row.get(key), 0)))
        for field in ("unit_exact", "unit_resolution", "cluster", "target", "track"):
            lo, hi = wilson_interval(int(row.get(f"{field}_hits", 0)), int(row.get(f"{field}_trials", 0)))
            row[f"{field}_wilson_low"] = lo; row[f"{field}_wilson_high"] = hi
        rows.append(row)
    audit = json.loads(SCN_MINPOINTS_AUDIT_PATH.read_text(encoding="utf-8"))
    return sorted(rows, key=lambda r: (fnum(r.get("min_points")), fnum(r.get("angle_deg")), fnum(r.get("level_index")))), audit


def copy_scn_minpoints_figures(out: Path) -> dict[str, Path]:
    """Copy independent min_points S+C+N figures into the report tree."""
    if not SCN_MINPOINTS_DIR.is_dir():
        return {}
    destination = out / "figures"; destination.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    for source in sorted((SCN_MINPOINTS_DIR / "figures").glob("scn_minpoints_*.png")):
        target = destination / source.name
        shutil.copy2(source, target)
        stem = source.stem.removeprefix("scn_minpoints_")
        result[stem] = target
    return result


def scn_minpoints_markdown(rows: list[dict[str, object]], figures: dict[str, Path], audit: dict[str, object]) -> str:
    """Embed the detailed same-seed S+C+N min-point comparison."""
    if not rows:
        return ""
    # The standalone addendum already contains the detailed method, formulas,
    # per-level tables and explanations.  Reuse its text so PDF and HTML do not
    # diverge; only headings are demoted for insertion below Section 9.
    if SCN_MINPOINTS_MARKDOWN_PATH.is_file():
        content = SCN_MINPOINTS_MARKDOWN_PATH.read_text(encoding="utf-8")
        lines = content.splitlines()
        transformed: list[str] = []
        for line in lines:
            if line.startswith("# GMTI 真实 S+C+N"):
                transformed.append("## 9.2 当前真实 S+C+N：min_points=2/3/6 同场景验证")
            elif line.startswith("## "):
                transformed.append("### " + line[3:])
            else:
                transformed.append(line)
        return "\n".join(transformed).replace("GMTI_S+C+N_minpoints_2_3_6_验证报告.md", "scn_minpoints_summary.csv")
    return ""


def load_scn_edge_validation(root: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Load the retained one-seed edge feasibility summaries.

    The edge run has three screens per level (one three-screen group), so its
    rows are useful for diagnosing geometry/cluster losses but are intentionally
    kept separate from the three-seed centre aggregate.
    """
    if not root.is_dir():
        return [], {}
    rows: list[dict[str, object]] = []
    manifests: list[dict[str, object]] = []
    support_background_stats: list[dict[str, object]] = []
    component_stats: list[dict[str, object]] = []
    for angle in ANGLES:
        angle_dir = root / token(angle)
        curve_path_local = angle_dir / "standard_mc_curve_summary.csv"
        theory_path = angle_dir / "go_theory_vs_mc.csv"
        manifest_path = angle_dir / "manifest.json"
        if not (curve_path_local.is_file() and manifest_path.is_file()):
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifests.append(manifest)
        theory = {int(round(fnum(r.get("level_index"), -1))): r
                  for r in read_csv(theory_path)} if theory_path.is_file() else {}
        support_measurement_path = angle_dir / "target_support_measurements.csv"
        if support_measurement_path.is_file():
            background_values = [fnum(r.get("support_background_ref"))
                                for r in read_csv(support_measurement_path)]
            background_values = [x for x in background_values if math.isfinite(x) and x > 0.0]
            if background_values:
                support_background_stats.append({
                    "angle_deg": angle, "sample_count": len(background_values),
                    "median_support_background_ref": float(np.median(background_values)),
                    "min_support_background_ref": float(np.min(background_values)),
                    "max_support_background_ref": float(np.max(background_values)),
                })
        period_metrics_path = angle_dir / "standard_mc_period_metrics.csv"
        if period_metrics_path.is_file():
            component_values = [fnum(r.get("unit_component_size"))
                                for r in read_csv(period_metrics_path)]
            component_values = [x for x in component_values if math.isfinite(x) and x >= 0.0]
            if component_values:
                component_stats.append({
                    "angle_deg": angle, "sample_count": len(component_values),
                    "median_unit_component_size": float(np.median(component_values)),
                    "max_unit_component_size": float(np.max(component_values)),
                    "count_component_ge_6": int(sum(x >= 6.0 for x in component_values)),
                })
        for raw in read_csv(curve_path_local):
            level = int(round(fnum(raw.get("level_index"), -1)))
            row = dict(raw)
            row.update({
                "angle_deg": angle,
                "target_angle_deg": fnum(manifest.get("target_angle_deg")),
                "target_angle_offset_deg": fnum(manifest.get("target_angle_offset_deg")),
                "background_profile": str(manifest.get("background_profile", "")),
                "theory_unit_pd_empirical_n_only": fnum(theory.get(level, {}).get("unit_pd_empirical_n_only")),
                "theory_unit_cut_scnr_db": fnum(theory.get(level, {}).get("unit_cut_scnr_db")),
            })
            row["support_output_scnr_db"] = fnum(raw.get("support_output_scnr_db"), fnum(raw.get("output_scnr_db")))
            row["output_scnr_cut_equiv_db"] = fnum(raw.get("output_scnr_cut_equiv_db"), row["theory_unit_cut_scnr_db"])
            if math.isfinite(row["output_scnr_cut_equiv_db"]):
                row["output_scnr_db"] = row["output_scnr_cut_equiv_db"]
            for key in ("output_scnr_db", "unit_exact_pd", "unit_resolution_pd", "cluster_pd",
                        "target_pd", "track_pd", "angle_rmse_deg", "angle_bias_deg",
                        "theory_unit_pd_empirical_n_only", "theory_unit_cut_scnr_db"):
                row[key] = fnum(row.get(key))
            for key in ("unit_exact_hits", "unit_exact_trials", "unit_resolution_hits",
                        "unit_resolution_trials", "cluster_hits", "cluster_trials",
                        "target_hits", "target_trials", "track_hits", "track_trials",
                        "angle_matched"):
                row[key] = int(round(fnum(row.get(key), 0)))
            rows.append(row)
    # Reuse the same strict audit facts as the multiseed validator without
    # importing a report-time dependency on that script.
    pipeline_files = sorted(root.rglob("pipeline_manifest.json"))
    raw_remaining = sorted(str(p) for p in root.rglob("*.bin") if p.is_file())
    strict_ok = bool(manifests) and all(bool(m.get("strict_online_no_prior")) for m in manifests)
    profile_ok = bool(manifests) and all(m.get("background_profile") == "compact_statistical" for m in manifests)
    pfa_ok = bool(manifests) and all(abs(fnum(m.get("pfa")) - 1e-6) < 1e-12 for m in manifests)
    min_ok = bool(manifests) and all(int(round(fnum(m.get("min_points"), -1))) == 6 for m in manifests)
    raw_flags_ok = True
    active_truth = 0
    diagnostic_truth_paths: list[str] = []
    for path in pipeline_files:
        item = json.loads(path.read_text(encoding="utf-8"))
        raw_flags_ok = raw_flags_ok and bool(item.get("raw_removed")) and bool(item.get("algorithm_output_bin_removed"))
        xml_path = Path(str(item.get("pipe_xml", "")))
        if not xml_path.is_file():
            raw_flags_ok = False
            continue
        xml = xml_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"<pc_peak_scene_truth>(.*?)</pc_peak_scene_truth>", xml, flags=re.S)
        if match and match.group(1).strip():
            active_truth += 1; diagnostic_truth_paths.append(str(xml_path))
        match = re.search(r"<debug_pc_peak>(.*?)</debug_pc_peak>", xml, flags=re.S)
        if match and match.group(1).strip() not in {"0", "0.0", "false", "False"}:
            active_truth += 1; diagnostic_truth_paths.append(str(xml_path) + ":debug_pc_peak")
    audit = {
        "root": str(root.resolve()), "status": "pass" if (len(manifests) == 3 and len(pipeline_files) == 12
            and strict_ok and profile_ok and pfa_ok and min_ok and raw_flags_ok
            and not raw_remaining and active_truth == 0) else "fail",
        "angle_manifest_count": len(manifests), "pipeline_count": len(pipeline_files),
        "strict_online_no_prior": strict_ok, "background_profile_compact_statistical": profile_ok,
        "pfa_1e-6": pfa_ok, "min_points_6": min_ok, "pipeline_raw_cleanup_flags": raw_flags_ok,
        "truth_active_detector_count": active_truth, "diagnostic_truth_paths": diagnostic_truth_paths,
        "raw_bin_remaining": raw_remaining,
        "seed": sorted({int(round(fnum(m.get("seed"), -1))) for m in manifests}),
        "target_angle_offset_deg": sorted({fnum(m.get("target_angle_offset_deg")) for m in manifests}),
        "support_background_stats": support_background_stats,
        "component_stats": component_stats,
    }
    return sorted(rows, key=lambda r: (fnum(r.get("angle_deg")), int(r.get("level_index", -1)))), audit


def load_truth_axis_validation(root: Path) -> dict[str, object]:
    """Load the targeted truth-CUT/FFT-quantisation regression.

    This appendix is intentionally read-only.  It consumes the retained
    period and theory CSVs after raw/F32 cleanup and never changes the pooled
    three-angle metrics.
    """
    curve_path_local = root / "standard_mc_curve_summary.csv"
    theory_path = root / "go_theory_vs_mc.csv"
    periods_path = root / "go_theory_periods.csv"
    support_path = root / "frozen_support.csv"
    manifest_path = root / "manifest.json"
    required = (curve_path_local, theory_path, periods_path, support_path, manifest_path)
    if not all(path.is_file() for path in required):
        return {}
    curve: list[dict[str, object]] = []
    for raw in read_csv(curve_path_local):
        row = dict(raw)
        for key in ("input_snr_db_control", "output_scnr_db", "unit_exact_pd",
                    "unit_resolution_pd", "unit_exact_hits", "unit_exact_trials",
                    "unit_resolution_hits", "unit_resolution_trials"):
            row[key] = fnum(row.get(key))
        curve.append(row)
    theory: list[dict[str, object]] = []
    for raw in read_csv(theory_path):
        row = dict(raw)
        for key in ("input_snr_db_control", "output_scnr_db",
                    "unit_pd_iid_go", "unit_pd_empirical_n_only",
                    "unit_pd_s_only_truth_mixture", "unit_pd_paired_output_mixture",
                    "unit_pd"):
            row[key] = fnum(row.get(key))
        theory.append(row)
    periods: list[dict[str, object]] = []
    for raw in read_csv(periods_path):
        row = dict(raw)
        for key in ("input_snr_db_control", "signal_gamma_s_only_truth",
                    "m_norm_cplusn_truth", "unit_pd_iid_period",
                    "unit_pd_empirical_period"):
            row[key] = fnum(row.get(key))
        row["period_id"] = int(round(fnum(row.get("period_id"), -1)))
        periods.append(row)
    supports = read_csv(support_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    offset_count = sum(int(round(fnum(row.get("support_row_offset"), 0))) != 0
                       for row in supports)
    return {
        "root": root,
        "curve": sorted(curve, key=lambda row: fnum(row.get("input_snr_db_control"))),
        "theory": sorted(theory, key=lambda row: fnum(row.get("input_snr_db_control"))),
        "periods": periods,
        "supports": supports,
        "manifest": manifest,
        "offset_count": offset_count,
        "offset_fraction": offset_count / len(supports) if supports else float("nan"),
    }


def plot_truth_axis_validation(out: Path, data: dict[str, object]) -> Path | None:
    """Plot exact/resolution Pd and the period-mixture theory side by side."""
    if not data:
        return None
    figures = out / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    curve = list(data["curve"])
    theory_by_level = {int(round(fnum(row.get("level_index"), -1))): row
                       for row in data["theory"]}
    x = np.asarray([fnum(row.get("output_scnr_db")) for row in curve], dtype=float)
    exact = np.asarray([fnum(row.get("unit_exact_pd")) for row in curve], dtype=float)
    resolution = np.asarray([fnum(row.get("unit_resolution_pd")) for row in curve], dtype=float)
    mixture = np.asarray([
        fnum(theory_by_level.get(int(round(fnum(row.get("level_index"), -1))), {})
             .get("unit_pd_paired_output_mixture"))
        for row in curve], dtype=float)
    s_only_mixture = np.asarray([
        fnum(theory_by_level.get(int(round(fnum(row.get("level_index"), -1))), {})
             .get("unit_pd_s_only_truth_mixture"))
        for row in curve], dtype=float)
    good = np.isfinite(x)
    if not np.any(good):
        return None
    fig, ax = plt.subplots(figsize=(8.0, 4.8))
    ax.plot(x[good], exact[good], "o-", color="#1f77b4", label="实测 physical truth-CUT exact")
    ax.plot(x[good], resolution[good], "s-", color="#9467bd", label="实测 ±2-bin resolution event")
    theory_good = good & np.isfinite(mixture)
    if np.any(theory_good):
        ax.plot(x[theory_good], mixture[theory_good], "--", color="#d62728",
                label="逐周期配对输出增量 + C+N M 理论")
    s_only_good = good & np.isfinite(s_only_mixture)
    if np.any(s_only_good):
        ax.plot(x[s_only_good], s_only_mixture[s_only_good], ":", color="#ff7f0e",
                label="S-only truth-CUT 独立诊断")
    ax.set_xlabel("physical truth-CUT 等效输出 SCNR (dB)")
    ax.set_ylabel("unit Pd")
    ax.set_ylim(-0.05, 1.05)
    ax.set_title("45° truth-CUT 轴回归：FFT 量化迁移与逐周期 GO mixture")
    ax.grid(True, alpha=.3); ax.legend(fontsize=8); fig.tight_layout()
    path = figures / "truth_axis_45deg_quantization_validation.png"
    fig.savefig(path, dpi=200); fig.savefig(path.with_suffix(".pdf")); plt.close(fig)
    return path


def truth_axis_markdown(data: dict[str, object], figure: Path | None) -> str:
    """Render the root-cause appendix with a table for every plotted point."""
    if not data or figure is None:
        return ""
    curve = list(data["curve"]); theory_by_level = {
        int(round(fnum(row.get("level_index"), -1))): row for row in data["theory"]}
    rows = []
    for row in curve:
        level = int(round(fnum(row.get("level_index"), -1)))
        theory = theory_by_level.get(level, {})
        rows.append([
            fmt(row.get("input_snr_db_control"), 1), fmt(row.get("output_scnr_db"), 2),
            f"{int(round(fnum(row.get('unit_exact_hits'), 0)))}/{int(round(fnum(row.get('unit_exact_trials'), 0)))}",
            fmt(row.get("unit_exact_pd"), 3), fmt(theory.get("unit_pd_paired_output_mixture"), 3),
            fmt(theory.get("unit_pd_s_only_truth_mixture"), 3), fmt(row.get("unit_resolution_pd"), 3),
        ])
    manifest = data["manifest"]
    return (
        "## 9.3 单元级根因回归：truth-CUT 与 FFT 量化迁移\n\n"
        "本节只追加一个 45°、seed=20261035、10 档×2 个三屏组的 CUDA 回归，"
        "不改写前面的三角度 pooled 结论。修正后的 unit 字段全部来自生产 GO 输入图的"
        "物理 truth CUT；support 字段仅作诊断。审计记录显示 "
        f"{int(data['offset_count'])}/{len(data['supports'])} 个 period 的 S-only 主瓣相对 truth CUT "
        f"偏移了 Doppler 单元（{100.0 * fnum(data['offset_fraction']):.1f}%）。"
        "因此把三屏平均功率先压成一个 SCNR 再代入 $Q_1$，会把少数落在 truth CUT 的高能量 period"
        "与多数落在相邻 bin 的低能量 period 混在一起，理论 Pd 必然偏高。\n\n"
        "逐周期模型不再使用 batch-mean gamma，而是对每个 period 使用"
        "$\\gamma_r=|z_{S+N,r}-z_{N,r}|^2/P_{0,truth}$、"
        "$m_r=M_{C+N}(r)/P_{0,truth}$；S-only truth-CUT gamma 作为独立诊断：\n\n"
        "$$P_{d,mixture}=\\frac{1}{R}\\sum_{r=1}^{R}"
        "Q_1\\left(\\sqrt{2\\gamma_r},\\sqrt{2\\alpha m_r}\\right).$$\n\n"
        f"在线 detector 未读取 truth/S-only；`strict_online_no_prior={bool(manifest.get('strict_online_no_prior'))}`，"
        f"PFA={fnum(manifest.get('pfa')):.1e}，alpha={fnum(manifest.get('go_alpha')):.10f}。"
        "S-only/Truth 只在离线理论与评分阶段使用，raw/F32 已在 pipeline/TrackManager 审计后清理。"
        "CSV 重算的输入、公式和‘不使用命中率拟合’声明记录在"
        " `theory_recompute_manifest.json`。\n\n"
        f"![truth-CUT 量化迁移回归](figures/{figure.name})\n\n"
        + md_table(["target_snr 控制量", "truth-CUT 输出 SCNR", "exact 命中/试验", "实测 exact Pd", "配对输出 mixture", "S-only mixture", "实测 ±2-bin Pd"], rows)
        + "\n\n表中红色虚线是配对输出逐周期 mixture，不是把实测命中率反拟合出来的曲线；橙色点线是 S-only 独立诊断；"
        "蓝线 exact 仍会受到物理真值与离散 FFT bin 的确定性错位影响，紫线才是生产分辨单元事件。"
        "这一结果把此前‘单元实测远高于教材理论’的表象拆成了坐标/量化问题与 CFAR 本身："
        "truth-CUT 字段覆盖修正后，单位阈值约 9（线性功率）且不再错误复用 support 阈值。"
    )


def copy_scn_edge_figures(out: Path, root: Path) -> dict[str, Path]:
    """Render edge plots from retained summaries without copying bad glyphs."""
    if not root.is_dir():
        return {}
    destination = out / "figures"
    destination.mkdir(parents=True, exist_ok=True)
    copied: dict[str, Path] = {}
    names = {
        "unit": "output_snr_unit_pd.png",
        "target": "output_snr_target_pd.png",
        "track": "output_snr_track_pd.png",
        "angle": "output_snr_angle_rmse.png",
    }
    for angle in ANGLES:
        angle_dir = root / token(angle)
        curve = read_csv(angle_dir / "standard_mc_curve_summary.csv")
        theory_by_level = {int(round(fnum(item.get("level_index"), -1))): item
                           for item in read_csv(angle_dir / "go_theory_vs_mc.csv")}
        for key, name in names.items():
            source = angle_dir / "figures" / name
            target = destination / f"scn_edge_{int(angle):02d}_{key}.png"
            field = {"unit": "unit_exact_pd", "target": "target_pd", "track": "track_pd", "angle": "angle_rmse_deg"}[key]
            x = np.asarray([
                fnum(row.get("output_scnr_cut_equiv_db"),
                     fnum(theory_by_level.get(int(round(fnum(row.get("level_index"), -1))), {}).get("unit_cut_scnr_db"),
                          fnum(row.get("output_scnr_db"))))
                for row in curve], dtype=float)
            y = np.asarray([fnum(row.get(field)) for row in curve], dtype=float)
            good = np.isfinite(x) & np.isfinite(y)
            if np.any(good):
                fig, ax = plt.subplots(figsize=(7.2, 4.4))
                ax.plot(x[good], y[good], "o-", color=COLORS.get(angle, "#1f77b4"), label="受控 S+N Monte Carlo")
                if key == "unit":
                    theory = np.asarray([fnum(row.get("theory_unit_pd_empirical_n_only")) for row in curve], dtype=float)
                    theory_good = np.isfinite(x) & np.isfinite(theory)
                    if np.any(theory_good):
                        ax.plot(x[theory_good], theory[theory_good], "--", color="#555555", label="C+N 经验 GO 理论")
                    ax.set_ylabel("单元 exact Pd")
                    ax.set_ylim(-0.05, 1.05)
                elif key == "target":
                    ax.set_ylabel("目标级 Pd")
                    ax.set_ylim(-0.05, 1.05)
                elif key == "track":
                    ax.set_ylabel("航迹三屏选二 Pd")
                    ax.set_ylim(-0.05, 1.05)
                else:
                    ax.set_ylabel("测角 RMSE (deg)")
                ax.set_xlabel("CUT-equivalent 输出 SCNR (dB)")
                ax.set_title(f"{angle:g}°边缘：输出 SCNR—{ {'unit':'单元检测','target':'目标级检测','track':'航迹级检测','angle':'测角 RMSE'}[key] }")
                ax.grid(True, alpha=.3); ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(target, dpi=180); plt.close(fig)
            elif source.is_file():
                shutil.copy2(source, target)
            else:
                continue
            copied[f"{int(angle):02d}_{key}"] = target
    return copied


def scn_edge_markdown(rows: list[dict[str, object]], figures: dict[str, Path],
                      audit: dict[str, object]) -> str:
    """Render a clearly scoped edge stress-test appendix."""
    if not rows:
        return ""
    detail = []
    for row in rows:
        detail.append([
            f"{fnum(row.get('angle_deg')):g}°边缘", f"{fnum(row.get('target_angle_deg')):g}°",
            int(row.get("level_index", 0)), fmt(row.get("output_scnr_db"), 2),
            f"{int(row.get('unit_exact_hits', 0))}/{int(row.get('unit_exact_trials', 0))}", fmt(row.get("unit_exact_pd")),
            f"{int(row.get('unit_resolution_hits', 0))}/{int(row.get('unit_resolution_trials', 0))}", fmt(row.get("unit_resolution_pd")),
            f"{int(row.get('cluster_hits', 0))}/{int(row.get('cluster_trials', 0))}", fmt(row.get("cluster_pd")),
            f"{int(row.get('target_hits', 0))}/{int(row.get('target_trials', 0))}", fmt(row.get("target_pd")),
            f"{int(row.get('track_hits', 0))}/{int(row.get('track_trials', 0))}", fmt(row.get("track_pd")),
            fmt(row.get("angle_rmse_deg"), 3),
        ])
    audit_table = md_table(["审计项", "值"], [
        ["status", audit.get("status", "—")],
        ["seed", ", ".join(str(x) for x in audit.get("seed", []))],
        ["edge offset", ", ".join(fmt(x, 1) + "°" for x in audit.get("target_angle_offset_deg", []))],
        ["pipeline", f"{audit.get('pipeline_count', 0)}（期望 12）"],
        ["strict/no-prior", audit.get("strict_online_no_prior", False)],
        ["active detector truth", audit.get("truth_active_detector_count", "—")],
        ["remaining raw BIN", len(audit.get("raw_bin_remaining", []))],
    ])
    background_rows = []
    for item in audit.get("support_background_stats", []):
        background_rows.append([
            f"{fnum(item.get('angle_deg')):g}°边缘", item.get("sample_count", 0),
            fmt(item.get("median_support_background_ref"), 3),
            fmt(item.get("min_support_background_ref"), 3),
            fmt(item.get("max_support_background_ref"), 3),
        ])
    component_rows = []
    for item in audit.get("component_stats", []):
        component_rows.append([
            f"{fnum(item.get('angle_deg')):g}°边缘", item.get("sample_count", 0),
            fmt(item.get("median_unit_component_size"), 2),
            fmt(item.get("max_unit_component_size"), 2), item.get("count_component_ge_6", 0),
        ])
    image_lines = []
    labels = {"unit": "单元 exact/分辨率", "target": "目标级", "track": "航迹三屏选二", "angle": "测角 RMSE"}
    for angle in ANGLES:
        for key, label in labels.items():
            path = figures.get(f"{int(angle):02d}_{key}")
            if path is not None:
                image_lines.append(
                    f"![{angle:g}°边缘{label}](figures/{path.name})\n\n"
                    f"该图来自同一边缘 seed 的 6 档、每档 3 个 screen；点数很少，仅用于定位边缘几何/聚类损失。"
                )
    return "# 10. 波束边缘 S+C+N 可行性压力测试（+0.8°，非正式门限）\n\n" \
        "本节补充真实 CUDA 的紧凑统计 S+C+N 边缘运行。目标物理角为波位中心 +0.8°，仍使用 Pfa=1e-6、GO guard=4/background=16、min_points=6、3–5 点 +20 dB 小簇恢复。每个角度只有一个 seed、每个 SCNR 档只有 3 个 screen（一个三屏组），因此不与中心三 seed pooled 曲线合并，也不提供 Pd90 结论。\n\n" \
        "逐档结果如下；`exact` 是物理真值 CUT 命中，`resolution` 是 ±2-bin 分辨率事件，后两者刻意分开。\n\n" \
        + md_table(["波位/目标角", "level", "output SCNR", "exact", "Pd", "±2-bin", "Pd", "cluster", "Pd", "target", "Pd", "track", "Pd", "RMSE"], detail) \
        + "\n\n审计摘要：\n\n" + audit_table \
        + "\n\n边缘支撑背景与 component 证据：\n\n" \
        + md_table(["角度", "背景样本数", "支撑背景中位", "最小", "最大"], background_rows) \
        + "\n\n" \
        + md_table(["角度", "component样本数", "component中位", "最大", "≥6点数量"], component_rows) \
        + "\n\n边缘结果的物理解释：0°边缘在高档出现 4–10 点连通 component，因此 min6 和小簇恢复都可能通过；30°边缘多数只有 1–3 点，单元/分辨率已经命中但 min6 会在聚类层截断；45°边缘的 C+N-only 固定支撑背景在真值附近出现约 10^2–4×10^3 的强非 IID 功率，GO 四方向最大均值随杂波纹理抬升，且 component 多为 1–2 点，故 cluster/target 仍为 0。这个差异支持“边缘损失主要发生在支撑形状与聚类连通性/背景非 IID 层”，不能归因于把 target_snr_db 直接当成主横轴。\n\n" \
        + "图表：\n\n" + "\n\n".join(image_lines) \
        + "\n\n机器可读来源：`scn_edge_validation_summary.csv` 与 `scn_edge_information_leakage_audit.json`；原始逐周期图已按授权清理，保留审计 manifest/XML。"


def scn_multiseed_markdown(rows: list[dict[str, object]], figures: dict[str, Path],
                           audit_path: Path, manifest_path: Path,
                           background_stats: list[dict[str, object]]) -> str:
    """Render a concise but explicit S+C+N appendix for the main report."""
    if not rows:
        return ""
    seed_count = max((int(fnum(r.get("seed_count"), 0)) for r in rows), default=0)
    pooled_screen_trials = max((int(fnum(r.get("target_trials"), 0)) for r in rows), default=0)
    pooled_unit_trials = max((int(fnum(r.get("unit_exact_trials"), 0)) for r in rows), default=0)
    pooled_track_events = max((int(fnum(r.get("track_trials"), 0)) for r in rows), default=0)
    pooled_group_count = max((int(fnum(r.get("screen_count"), 0)) for r in rows), default=0)
    background_counts = sorted({int(fnum(item.get("sample_count"), 0)) for item in background_stats
                                if int(fnum(item.get("sample_count"), 0)) > 0})
    background_count_text = "/".join(str(x) for x in background_counts) if background_counts else "按根目录实际保留"
    summary_rows = []
    for angle in ANGLES:
        selected = [r for r in rows if abs(fnum(r.get("angle_deg")) - angle) < 1e-9]
        selected.sort(key=lambda r: fnum(r.get("output_scnr_db")))
        crossing = next((fnum(r.get("output_scnr_db")) for r in selected if fnum(r.get("target_pd")) >= .9), float("nan"))
        rmses = [fnum(r.get("angle_rmse_deg")) for r in selected if math.isfinite(fnum(r.get("angle_rmse_deg")))]
        summary_rows.append([f"{angle:g}°", len(selected), int(selected[0].get("seed_count", 0)) if selected else 0,
                             fmt(crossing, 2), fmt(np.nanmean([fnum(r.get("target_pd")) for r in selected]), 3),
                             fmt(np.mean(rmses) if rmses else float("nan"), 4)])
    detail_rows = []
    for r in rows:
        detail_rows.append([
            f"{fnum(r.get('angle_deg')):g}°", int(r.get("level_index", 0)), fmt(r.get("output_scnr_db"), 2),
            f"{int(r.get('output_axis_valid_seed_count', 0))}/{int(r.get('output_axis_total_seed_count', r.get('seed_count', 0)))}",
            f"{int(r.get('unit_exact_hits', 0))}/{int(r.get('unit_exact_trials', 0))}", fmt(r.get("unit_exact_pd")),
            f"{int(r.get('unit_resolution_hits', 0))}/{int(r.get('unit_resolution_trials', 0))}", fmt(r.get("unit_resolution_pd")),
            f"{int(r.get('cluster_hits', 0))}/{int(r.get('cluster_trials', 0))}", fmt(r.get("cluster_pd")),
            f"{int(r.get('target_hits', 0))}/{int(r.get('target_trials', 0))}", fmt(r.get("target_pd")),
            f"{int(r.get('track_hits', 0))}/{int(r.get('track_trials', 0))}", fmt(r.get("track_pd")), fmt(r.get("angle_rmse_deg"), 3),
        ])
    dependency_rows = []
    for r in rows:
        dependency_rows.append([
            f"{fnum(r.get('angle_deg')):g}°", int(r.get("level_index", 0)), fmt(r.get("screen_pd")),
            fmt(r.get("p_dt1_given_dt0_1")), fmt(r.get("p_dt1_given_dt0_0")),
            fmt(r.get("screen_pair_correlation")), fmt(r.get("track_pd_direct")),
            fmt(r.get("track_pd_inclusion_exclusion")), fmt(r.get("track_pd_independent")),
            fmt(r.get("postcluster_combined_retention")),
        ])
    audit_note = "无审计表"
    if audit_path.is_file():
        audit_rows = read_csv(audit_path)
        def remaining_bin_count(value: object) -> int:
            if value in (None, "", "[]"):
                return 0
            try:
                parsed = json.loads(str(value))
                return len(parsed) if isinstance(parsed, list) else 0
            except (TypeError, ValueError, json.JSONDecodeError):
                return 0
        audit_note = md_table(["seed根目录", "status", "pipeline数", "在线truth", "诊断truth", "剩余BIN"], [
            [Path(str(r.get("root", ""))).name, r.get("status", ""), r.get("pipeline_count", ""),
             r.get("truth_active_detector_count", ""), r.get("diagnostic_truth_path_count", ""),
             remaining_bin_count(r.get("raw_bin_remaining"))]
            for r in audit_rows
        ])
    image_lines = []
    labels = {
        "unit_exact": "S+C+N exact CUT 单元 Pd",
        "unit_resolution": "S+C+N ±2-bin 分辨率单元 Pd",
        "cluster": "S+C+N cluster Pd",
        "target": "S+C+N target Pd",
        "track": "S+C+N 三屏选二 track Pd",
        "angle": "S+C+N angle RMSE",
    }
    for key, label in labels.items():
        if key in figures:
            image_lines.append(f"![{label}](figures/{figures[key].name})\n\n图中点为 {int(rows[0].get('seed_count', 0))} 个独立 seed 的 pooled hits/trials；阴影为二项 Wilson 95% 区间。")
    comparison_notes = []
    for angle in ANGLES:
        selected = [r for r in rows if abs(fnum(r.get("angle_deg")) - angle) < 1e-9]
        theory_gap = [abs(fnum(r.get("unit_resolution_pd")) - fnum(r.get("theory_unit_pd_empirical_n_only")))
                      for r in selected if math.isfinite(fnum(r.get("unit_resolution_pd"))) and math.isfinite(fnum(r.get("theory_unit_pd_empirical_n_only")))]
        exact_gap = [fnum(r.get("unit_resolution_pd")) - fnum(r.get("unit_exact_pd"))
                     for r in selected if math.isfinite(fnum(r.get("unit_resolution_pd"))) and math.isfinite(fnum(r.get("unit_exact_pd")))]
        comparison_notes.append(
            f"{angle:g}°：resolution−C+N理论平均绝对差 {fmt(np.mean(theory_gap) if theory_gap else float('nan'), 3)}，"
            f"resolution−exact 平均差 {fmt(np.mean(exact_gap) if exact_gap else float('nan'), 3)}。"
        )
    comparison_text = (
        "\n\n理论—实测差异的定量判读：" + " ".join(comparison_notes) +
        f" 30°的分辨率事件与 C+N 经验理论最接近；0°和45°的偏差主要来自物理真值 CUT 与 FFT 主瓣/冻结支撑错位，以及每档只有 {pooled_unit_trials} 个 unit 样本。"
        "本批 pooled 主横轴采用 CUT-equivalent 单元功率比的线性域均值再取 dB；原始 3×3 支撑轴仅作审计，逐档有效 seed 数写在表中。"
        "这不是把 evaluation 命中率反向拟合进理论，而是把教材 GO 单 CUT 与生产 exact CUT 放到同一个输出平面。"
    )
    axis_deltas = [
        (abs(fnum(r.get("output_scnr_db")) - fnum(r.get("output_scnr_db_logmean"))), r)
        for r in rows
        if math.isfinite(fnum(r.get("output_scnr_db"))) and math.isfinite(fnum(r.get("output_scnr_db_logmean")))
    ]
    axis_text = ""
    if axis_deltas:
        _, largest_axis_delta = max(axis_deltas, key=lambda item: item[0])
        axis_text = (
            " 功率域汇总相对原始 dB 均值的最大坐标修正出现在 "
            f"{fnum(largest_axis_delta.get('angle_deg')):g}° level {int(largest_axis_delta.get('level_index', 0))}："
            f"{fmt(largest_axis_delta.get('output_scnr_db_logmean'), 2)}→{fmt(largest_axis_delta.get('output_scnr_db'), 2)} dB；"
            "该变化只来自同一输出功率比的聚合顺序，不涉及 detector 阈值或命中筛选。"
        )
    background_text = ""
    if background_stats:
        background_rows = []
        pooled_ready_by_seed: dict[str, bool] = {}
        for item in background_stats:
            background_rows.append([
                item.get("seed", "—"), f"{fnum(item.get('angle_deg')):g}°", item.get("sample_count", 0),
                fmt(item.get("p0_support_mean"), 3), fmt(item.get("p0_support_median"), 3),
                fmt(item.get("p0_support_p95"), 3), fmt(item.get("p0_support_max"), 3),
                fmt(item.get("p0_max_to_median"), 1), item.get("p0_outlier_count_gt5x_median", 0),
                "通过" if item.get("calibration_sanity_pass") else "失败",
            ])
            seed_key = str(item.get("seed", "—"))
            pooled_ready_by_seed[seed_key] = pooled_ready_by_seed.get(seed_key, True) and bool(item.get("calibration_sanity_pass"))
        extreme = max(background_stats, key=lambda x: fnum(x.get("p0_max_to_median"), -1.0))
        not_ready = [seed for seed, ready in pooled_ready_by_seed.items() if not ready]
        readiness_text = (
            "本批严格链路审计的 `status` 均为 pass，但 pooled 轴就绪标志按 seed 汇总为 "
            + ("`model_ready_for_pooled_axis=false`（" + ", ".join(not_ready) + "）。这些根仍保留在本次 pooled 统计，不按命中率删除；但该 pooled 结果只作模型诊断，不能宣称为已校准稳定主曲线。" if not_ready else "true；仍需更长背景批次才能作为正式轴。")
        )
        background_text = (
            "\n\n## 背景 realization 与 output-SCNR 稳定性审计\n\n"
            f"当前 compact_statistical 不是平稳高斯背景。每根 seed/角度实际保留 {background_count_text} 个 C+N-only 支撑样本（由各根 trials-per-level 决定），"
            "而 CUT-equivalent output-SCNR 分母使用该根的 CUT P0 均值；lognormal 面杂波落入目标支撑时，少量样本就可能显著抬高均值。"
            "下表完整保留所有 seed/角度，不删除任何异常值；`max/median` 仅用于识别异质性，不参与 detector 或理论拟合。\n\n"
            + md_table(["seed", "角度", "P0样本", "均值", "中位", "P95", "最大", "最大/中位", ">5×中位", "校准sanity"], background_rows)
            + "\n\n"
            + f"最强异质性出现在 seed {extreme.get('seed')}、{fnum(extreme.get('angle_deg')):g}°：P0 最大/中位约 "
            + f"{fmt(extreme.get('p0_max_to_median'), 1)} 倍，说明该 seed 的 {int(fnum(extreme.get('sample_count'), 0))}-period 均值不足以代表长期 C+N 背景。"
            + "这不是信息泄露，也不是将异常点静默剔除；它解释了 pooled 曲线在不同 seed 间的横轴漂移。"
            + readiness_text
            + "因此本节只能作为 compact S+C+N 模型验证，不能作为最终 Pd90 或稳定输出-SCNR 门限。"
        )
    return f"## 9.1 当前真实 S+C+N {seed_count} seed 模型验证（新增）\n\n" \
        f"本节嵌入 `compact_statistical` 背景的最新验证，不能与上一节 thermal-only 回归混读。每个 seed/角度使用 6 个档位；pooled 每档保留 {pooled_group_count} 个三屏组、{pooled_screen_trials} 个 screen truth trial、{pooled_track_events} 个航迹事件（unit 计数为 {pooled_unit_trials}）。主 min_points=6，Pfa=1e-6，guard=4，background=16，alpha≈13.4495103。输出主横轴来自同一物理 CUT 的 S+C+N 与 C+N 配对增量；固定 3×3 支撑总量只作诊断，多 seed 汇总先在功率域求均值再转 dB，非有限档位不按命中率补值，并保留有效 seed 数。\n\n" \
        + md_table(["角度", "档位", "seed数", "首个 target Pd≥0.9(dB)", "平均 target Pd", "平均 RMSE(deg)"], summary_rows) \
        + "\n\n逐档表：\n\n" + md_table(["角度", "level", "output SCNR", "SCNR有效seed/总seed", "exact", "Pd", "±2-bin", "Pd", "cluster", "Pd", "target", "Pd", "track", "Pd", "RMSE"], detail_rows) \
        + "\n\n三屏依赖和目标漏斗：\n\n" + md_table(["角度", "level", "Pscreen", "P(next|prev=1)", "P(next|prev=0)", "相关", "track直接", "track IE", "track独立基线", "postcluster保留"], dependency_rows) \
        + "\n\n" + "\n\n".join(image_lines) \
        + background_text \
        + comparison_text \
        + axis_text \
        + "\n\n三屏相关性、IE 公式和 combined post-cluster retention 详见 `scn_multiseed_summary.csv`；local-test 尚未独立导出 selector/relocation/match 三个拒绝标志，因此没有伪造三个条件概率。\n\n信息泄露/清理审计：\n\n" + audit_note \
        + f"\n\n机器可读来源：`{manifest_path}`、`{SCN_MULTISEED_SUMMARY_PATH}`。"


def write_report(out: Path, pdf: Path, html: Path, mappings: dict[float, dict[str, float]], alpha_rows: list[dict[str, object]],
                 templates: dict[str, dict[str, object]], windows: dict[str, dict[str, np.ndarray]], theory_rows: list[dict[str, object]],
                 shifts: list[dict[str, object]], metrics_by_min: dict[int, list[dict[str, object]]], dependencies: list[dict[str, object]],
                 figures: dict[str, Path], angle_figures: dict[int, Path], module_files: dict[str, Path],
                 target_pb_summary: list[dict[str, object]], target_pb_figures: dict[int, Path],
                 source_info: dict[str, object], multiseed_rows: list[dict[str, object]],
                 multiseed_figures: dict[str, Path], morphology_rows: list[dict[str, str]],
                 morphology_figure: Path | None, scn_minpoints_markdown_text: str,
                 scn_edge_markdown_text: str) -> Path:
    # Compact tables are repeated next to the relevant figure.  Full rows stay
    # in CSV so PDF pages remain readable while every plotted point is auditable.
    map_table = [[f"{a:g}°", fmt(m["slope_db_per_db"], 6), fmt(m["intercept_db"], 5), fmt(m["r2"], 6), fmt(m["max_abs_residual_db"], 3), m["sample_count"]] for a, m in mappings.items()]
    alpha_table = [[r["guard"], r["guard_width"], r["outer_width"], r["direction_cells"], r["block_counts"], fmt(r["alpha"], 8), f"{r['mc_pfa']:.3g}", "生产" if r["production_guard"] else "反事实"] for r in alpha_rows]
    template_table = []
    for g in GROUPS:
        t = templates.get(g)
        if not t: continue
        template_table.append([g, t["sample_count"], t["qualified_count"], ", ".join(fmt(x, 4) for x in t["lambda"]), ", ".join(fmt(x, 5) for x in t["direction"])])
    shift_table = [[r["angle_group"], fmt(r["A_0p5_db"], 2), fmt(r["B_0p5_db"], 2), fmt(r["C_0p5_db"], 2), fmt(r["C_leak_0p5_db"], 2), fmt(r["B_minus_A_db"], 2), fmt(r["C_minus_A_db"], 2), fmt(r["C_leak_minus_C_db"], 2), r["leakage_template_qualified"]] for r in shifts]

    summary_rows = []
    for minimum, rows in metrics_by_min.items():
        for angle in ANGLES:
            rr = [r for r in rows if r["angle_deg"] == angle]
            mc, typ = measured_crossing(rr)
            summary_rows.append([minimum, f"{angle:g}°", len(rr), sum(r["target_trials"] for r in rr), fmt(mc, 2) + ("（上界）" if typ == "upper_bound_first_grid" else "" if math.isfinite(mc) else "（未达）"), fmt(np.mean([r["target_pd"] for r in rr]), 3), fmt(np.nanmean([r["angle_rmse_deg"] for r in rr]), 3)])

    target_detail = {}
    for minimum, rows in metrics_by_min.items():
        target_detail[minimum] = []
        for r in rows:
            target_detail[minimum].append([f"{r['angle_deg']:g}°", fmt(r["output_scnr_db"], 2), f"{r['target_hits']}/{r['target_trials']}", fmt(r["unit_exact_pd"], 3), fmt(r["unit_resolution_pd"], 3), fmt(r["cluster_pd"], 3), fmt(r["target_pd"], 3), fmt(r["track_pd"], 3), fmt(r["angle_rmse_deg"], 3)])
    dep_table = [[r.get("min_points"), f"{fnum(r.get('angle_deg')):g}°", r.get("group_count"), fmt(r.get("p_screen"), 3), fmt(r.get("p_dt1_given_dt0_1"), 3), fmt(r.get("p_dt1_given_dt0_0"), 3), fmt(r.get("pairwise_correlation_mean"), 3), fmt(r.get("track_pd_direct"), 3), fmt(r.get("track_pd_independent_baseline"), 3), fmt(r.get("track_pd_inclusion_exclusion"), 3)] for r in dependencies]
    go_tables = go_selected_tables(theory_rows, metrics_by_min[2])
    multiseed_md = multiseed_table(multiseed_rows)
    thermal_seed_count = max((int(fnum(r.get("seed_count"), 0)) for r in multiseed_rows), default=0)
    if multiseed_figures:
        multiseed_items = [(key, multiseed_figures.get(key))
                           for key in ("unit", "cluster", "target", "track", "angle")]
        multiseed_figures_md = "\\newpage\n\n" + "\n\n\\newpage\n\n".join(
            f"![min_points=6 {thermal_seed_count or 3} seed {key} 曲线。](figures/{path.name})\n\n"
            f"图中点为 {thermal_seed_count or 3} 个独立 seed 的 pooled hits/trials；阴影是 Wilson 95% 区间。"
            for key, path in multiseed_items
            if path is not None
        )
    else:
        multiseed_figures_md = "未发现可嵌入的三 seed 图；请检查 multiseed_manifest。"

    summary_md = md_table(['min_points','角度','档位行数','target trials','首次 exact CUT Pd≥0.9','10档平均 target Pd','平均 RMSE(deg)'], summary_rows)
    mapping_md = md_table(['角度','斜率 a (dB/dB)','截距 b (dB)','R²','最大残差(dB)','样本数'], map_table)
    alpha_md = md_table(['guard','guard宽','外窗宽','方向单元','八块计数','alpha','MC Pfa','标记'], alpha_table)
    template_md = md_table(['group','全部支撑','合格 CUT','8块 λ/gamma','4方向 δ/gamma'], template_table)
    shift_md = md_table(['group','A 0.5','B 0.5','C 0.5','C+leak 0.5','B-A(dB)','C-A(dB)','C+leak-C(dB)','qualified'], shift_table)
    pd90_md = md_table(['角度','教材固定m=1 CUT/输出 Pd90(dB)','A IID GO 输出 Pd90(dB)','实测 exact crossing','判读'], source_info['pd90_table'])
    dep_md = md_table(['min','角度','三屏组','P_screen','P(next|prev=1)','P(next|prev=0)','平均相关','实测2/3','独立2/3','IE 2/3'], dep_table)
    target2_md = md_table(['角度','output SCNR','target h/n','unit exact','unit ±2bin','cluster','target','track','RMSE(deg)'], target_detail[2])
    target3_md = md_table(['角度','output SCNR','target h/n','unit exact','unit ±2bin','cluster','target','track','RMSE(deg)'], target_detail[3])
    target6_md = md_table(['角度','output SCNR','target h/n','unit exact','unit ±2bin','cluster','target','track','RMSE(deg)'], target_detail[6])
    pb_table_rows = []
    for row in target_pb_summary:
        if row.get("model") != "profile":
            continue
        pb_table_rows.append([
            row.get("angle_group"), row.get("min_points"), row.get("support_sample_count"),
            fmt(row.get("pd_at_10db"), 3), fmt(row.get("pd_at_15db"), 3),
            fmt(row.get("pd_at_20db"), 3), fmt(row.get("output_scnr_at_pd_0p5_db"), 2),
            fmt(row.get("output_scnr_at_pd_0p9_db"), 2),
        ])
    target_pb_md = md_table(
        ['calibration group', 'min_points', 'support n', 'PB Pd@10 dB', 'PB Pd@15 dB',
         'PB Pd@20 dB', 'PB 0.5交点', 'PB 0.9交点'], pb_table_rows)
    morphology_table_rows = []
    for row in morphology_rows:
        morphology_table_rows.append([
            row.get("level_index", "—"),
            fmt(row.get("output_scnr_db_mean"), 2),
            row.get("event_count", "—"),
            fmt(row.get("local_3x5_hit_mean"), 2),
            fmt(row.get("full_component_size_mean"), 2),
            fmt(row.get("cluster_min6_rate"), 3),
            fmt(row.get("production_cluster_rate"), 3),
            fmt(row.get("production_small_branch_fraction"), 3),
            fmt(row.get("production_local_lt6_fraction"), 3),
            fmt(row.get("pb_min6_profile"), 3),
        ])
    morphology_md = md_table(
        ["档位", "output SCNR均值", "n", "局部3×5命中均值", "全图component均值",
         "严格min6", "生产cluster", "生产命中中小簇", "生产命中中局部<6", "PB min6"],
        morphology_table_rows)
    pb_figures_md = "\\newpage\n\n" + "\n\n\\newpage\n\n".join(
        f"![Poisson-binomial min_points={minimum}。](figures/{target_pb_figures[minimum].name})"
        for minimum in (2, 3, 6) if minimum in target_pb_figures
    )
    pb_example = source_info.get("target_pb_example", "")
    pb_offset = source_info.get("target_pb_offset", "")
    angle_images = "\n\n".join(f"![min_points={m} 测角 RMSE。](figures/{angle_figures[m].name})" for m in (2, 3, 6))
    morphology_figure_name = morphology_figure.name if morphology_figure is not None else ""
    text = r'''---
title: "GMTI 输出 SCNR—测角精度—检测概率综合分析报告"
subtitle: "真实 GO-CFAR 链路的详细理论推导、坐标统一、min_points 实测与审计"
author: "GMTI 算法验证"
date: "2026-08-30"
geometry: margin=1.45cm
mainfont: Noto Sans CJK SC
CJKmainfont: Noto Sans CJK SC
fontsize: 9.2pt
---

# 1. 结论先行

本版报告把此前“理论曲线、输出 SCNR、单元事件、聚类事件和航迹事件”容易混在一起的问题全部拆开。主横轴统一为真实生产链物理 CUT 等效的 **CUT-equivalent output SCNR**；固定 3×3 支撑总能量只作为诊断列。GO 方程直接使用该 CUT 等效 SCNR 计算 Marcum-$Q$/非中心 $chi^2$ 概率，因此教材理想值和实测主轴属于同一个单元级随机量。

本版最重要的数值判断如下。

* 生产 GO 直接取四方向均值的最大值；guard=4、background=16 时外窗 41×41、保护窗 9×9，训练环唯一单元数为 $41^2-9^2=1600$，方向分母为 $16×41=656$。八块计数为 $[256,144,256,144,144,256,144,256]$，生产 alpha 为 **__ALPHA__**，独立 MC 虚警率为 **__PFA__**。
* 固定 $m=1$ 的教材单 CUT 曲线在 CUT-equivalent output SCNR=13.0872 dB 时达到 $P_d=0.9$。由于主轴就是生产物理 CUT，该值对 0°、30°、45°均为 **13.0872 dB**；3×3 支撑总 SCNR 仍在诊断列保留，不再拿来压低教材理想值。
* A 层 IID GO 仍对训练最大值 $M$ 做积分，因此其 $P_d=0.9$ output 轴略高于固定 $m=1$；C 层真实 C+N 窗的 $M/CUT_{{background}}$ 分布有明显长尾，所需 output SCNR 再右移。B 层必须用真实 S-only 支撑的非中心参数；本版已修正此前“除以自由度后又按方向单元数重复归一化”的 bug。
* 迁移到同一 CUT-equivalent output SCNR 轴后，one-seed 生产 exact CUT 事件的低于教材 Pd90 现象不再被 3×3 支撑积分人为制造；当前有限档位若未覆盖 13.087 dB，严格报告为“网格未达/上界”，不外推。±2-bin 分辨率事件仍单列，不能替代 exact CUT。
* 第 6–8 章 min_points=2/3/6 的目标级主对照来自同一 GO/Pfa/SCNR/角度设置的 thermal-noise-only 真实 CUDA 生产链；每角度 150 periods、50 个三屏组，每个 SCNR 档 15 个 screen truth trial 与 5 个 track truth events。第 9.2 节另用真实 S+C+N 场景和 3 个独立 seed 复核三种门槛：每个 seed 每角度 36 periods、12 个 screen event、4 个 track event，pooled 后每角度 108/36/12。由于单点样本少，报告给 Wilson 区间，不宣称最终 90% 门限。
* 另有一组独立的 +0.8° 波束边缘 S+C+N 压力测试：每角度 1 个 seed、每档 3 个 screen，仅用于诊断支撑偏移、非 IID 杂波和 min_points=6 的边缘损失，不与中心三 seed 曲线合并。
* 所有修正后根的严格信息泄露审计为 pass（每根 12 条 pipeline、online truth=0、diagnostic truth=0、strict=true）；truth 只用于离线支撑评分、匹配和 RMSE。min_points=2/3/6 的真实 S+C+N 对照均有 3 个独立 seed 的 pooled 验证，但仍不宣称最终 90% 门限。

报告中所有图的横轴、分子、分母和来源均在相邻小节说明；完整逐点值保存在本目录 CSV，不把 `target_snr_db` 当正式主横轴。

# 2. 实验对象、来源和可复现边界

| 项目 | 当前正式值 | 证据/含义 |
|---|---:|---|
| CFAR | GO，$P_{{FA}}=10^{{-6}}$ | 生产 XML/meta；本专项不画 CA 对照 |
| GO 窗 | guard=4，background=16 | 41×41 外窗、9×9 guard、四方向 max |
| 目标级配置 | min_points=2、3、6 | small branch 使用 3–5 点且峰值超过局部中位数 20 dB；min6 的 small_min_points=3 |
| 角度 | 0°、30°、45°中心；各自 +0.8°边缘校准与独立压力测试 | 中心用于正式小样本 evaluation；边缘 pressure test 只作可行性/根因诊断，不冒充正式 Pd |
| 背景 | thermal noise only，area clutter disabled | 先隔离 CFAR/SNR/聚类链；不是复杂杂波最终结论 |
| SCNR 档 | 6、8、10、12、14、16、18、20、22、24 dB（实际略有偏差） | 每点使用实测 output SCNR，控制量仅为 Stage2 输入旋钮 |
| 每点样本 | 15 screen / 5 三屏 track | Wilson 95% 区间；小样本限制必须保留 |
| 历史真实窗 | __WINDOWS__ 个窗、__MAPS__ 幅 C+N 图 | 仅用于 C 层半解析背景；原始 BIN 已按授权清理，保留 CSV |

## 2.1 当前正式 one-seed 数据表

__SUMMARY_TABLE__

上表的“首次”使用真实网格内线性插值；若首个网格点已经高于 0.9，写成上界；若整个网格未达到，写成未达。完整分子/分母在 `unit_target_track_summary.csv`。

## 2.2 输出 SCNR 的唯一口径

固定的生产物理 CUT 记为 $c$，固定的 3×3 支撑窗口记为 $W$（只作诊断）。对同一组 S+N 与 N-only 运行，先在功率域取均值，再用 N-only 的同一位置估计噪声底：

$$P_{{S,\mathrm{{eff}},c}}=\operatorname{{mean}}(P_{{S+N,c}})-\operatorname{{mean}}(P_{{N,c}}),\qquad P_{{0,c}}=\operatorname{{mean}}(P_{{N,c}}),$$
$$SCNR_{{out,CUT,dB}}=10\log_{{10}}\left(\frac{{P_{{S,\mathrm{{eff}},c}}}}{{P_{{0,c}}}}\right).$$

同时保留支撑诊断量
$$SCNR_{{support,dB}}=10\log_{{10}}\frac{\operatorname{{mean}}_W(P_{{S+N}})-\operatorname{{mean}}_W(P_N)}{\operatorname{{mean}}_W(P_N)}.$$

这里的 $SCNR_{{out,CUT}}$ 与 GO 教材公式中的 $\gamma_{{CUT}}=P_{{CUT}}/P_0$ 是同一物理单元口径；3×3 总支撑量不再直接代入单 CUT 理论。二者的差值仍通过 paired calibration 作为诊断联系：

__MAPPING_TABLE__

$$SCNR_{{support,dB}}\simeq a\,SCNR_{{out,CUT,dB}}+b.$$

例如 0° 的 CUT-equivalent output SCNR=13 dB，对应的 3×3 支撑诊断约为 $a\times13+b$（具体配对点见表和 CSV）；支撑总能量不能反向替代物理 CUT。过去把 3×3 的 6 dB 直接放入单 CUT $Q_1$，正是教材曲线与实测轴错位的首要原因。

![物理 CUT-equivalent output SCNR 到 3×3 支撑诊断 SCNR 的配对回归。](figures/__MAPPING_FIG__)

图 1 的每个点来自 retained `processed_output_snr_calibration.csv` 的一对 $(SCNR_{{out,CUT}},SCNR_{{support}})$，虚线是普通最小二乘回归，未使用 evaluation truth 命中率拟合；表格给出残差，因此它不是把测出来的 Pd 反向调到理论上。

# 3. GO-CFAR 的几何、分布和 alpha 推导

![GO 八块几何。](figures/__GEOMETRY_FIG__)

## 3.1 从复高斯噪声到指数功率

设每个背景复采样为 $n_i=I_i+jQ_i$，$I_i,Q_i\sim\mathcal N(0,P_0/2)$ 且独立。令 $u_i=I_i/\sqrt{{P_0/2}}$、$v_i=Q_i/\sqrt{{P_0/2}}$，则 $u_i,v_i\sim\mathcal N(0,1)$，于是

$$\frac{{2|n_i|^2}}{{P_0}}=u_i^2+v_i^2\sim\chi^2_2.$$

自由度为 2 的中心卡方除以 2 就是均值为 1 的指数变量：

$$Z_i=\frac{{|n_i|^2}}{{P_0}}\sim\operatorname{{Exp}}(1),\qquad f_Z(z)=e^{-z},z\ge0.$$

这一步说明教材中“噪声功率服从指数分布”不是经验假设，而是复高斯 I/Q 的直接结果。

## 3.2 八个块为什么是 4 个 16×16 加 4 个中间块

取 background $b=16$、guard 半宽 $g=4$。保护窗宽度 $w_g=2g+1=9$，外窗宽度 $w_o=2(g+b)+1=41$。按照左上、上中、右上、左中、右中、左下、下中、右下编号，八块单元数为

$$n_j=[b^2,bw_g,b^2,w_gb,w_gb,b^2,bw_g,b^2]
=[256,144,256,144,144,256,144,256].$$

方向每个有 $n_{{dir}}=b w_o=16×41=656$ 个训练单元，但角块会被相邻方向共享：

$$L=\frac{{Y_{{TL}}+Y_{{LC}}+Y_{{BL}}}}{{2n_{{dir}}}},\quad R=\frac{{Y_{{TR}}+Y_{{RC}}+Y_{{BR}}}}{{2n_{{dir}}}},$$
$$T=\frac{{Y_{{TL}}+Y_{{TC}}+Y_{{TR}}}}{{2n_{{dir}}}},\quad B=\frac{{Y_{{BL}}+Y_{{BC}}+Y_{{BR}}}}{{2n_{{dir}}}},\quad M=\max(L,R,T,B).$$

这里 $Y_j=\sum_{{i\in j}}2|n_i|^2/P_0$ 是中心卡方和；除以 2 后就是代码中 `direction_from_blocks` 使用的原始功率和。共享角块意味着 $L,R,T,B$ 相关，不能把四个方向当成四个独立 Gamma 变量再套一个独立最大值公式。

## 3.3 alpha 的方程和数值复核

目标为空时，CUT 的归一化功率 $Z_{{CUT}}=|n_{{CUT}}|^2/P_0\sim Exp(1)$。给定训练最大值 $M=m$，GO 检测条件为 $Z_{{CUT}}>\alpha m$，所以

$$P_{{FA}}(m)=P(Z_{{CUT}}>\alpha m\mid M=m)=e^{-\alpha m}.$$

对共享八块结构积分：

$$P_{{FA}}=E_M[e^{-\alpha M}].$$

令 $F(\alpha)=E[e^{-\alpha M}]-10^{-6}$，用数值根 $F(\alpha)=0$。本版对 guard=3/4/5 各自用独立 IID 八块 MC 样本求根，guard=4 同时与生产 sidecar alpha 对照：

__ALPHA_TABLE__

生产 guard=4 的 13.4495103198 是由二维 GO 的四方向共享八块结构直接求得的系数，不是把 1600 个训练单元误当成一个方向的系数。表中的 MC Pfa 只验证根方程数值，曲线使用同一个 alpha。

![guard=3/4/5 的 IID 无泄漏曲线。](figures/__GUARD_FIG__)

图 3 的三条线只改变保护窗 guard，其他 background、$P_{{FA}}$ 和 CUT→支撑诊断映射保持不变；红线 guard=4 是生产设置。
上表给出了对应的八块计数、alpha 和 Monte-Carlo 虚警率，完整逐点曲线仍在 `theory_unit_go_curves.csv`。

## 3.4 Doppler-max 与实际二维 GO 门限：从搜索统计到生产公式

这里先给出一个 **K=32、N=16 的 Doppler-max 一维旁算例**，完整说明“搜索最大值”如何改变虚警；再把推导接回 GMTI 的真实二维 GO-CFAR。旁算例只用于解释和极限自检，**不作为 CA-CFAR 对照，也不替换生产 alpha**。生产算法的最终训练统计量仍是第 3.2 节定义的四方向共享八块最大值 $M=\max(L,R,T,B)$。

### 3.4.1 Doppler 搜索最大值的分布

令一个 range cell 的 $K$ 个独立归一化功率为 $E_k=|n_k|^2/P_0\sim\operatorname{Exp}(1)$，Doppler 搜索输出为

$$D=\max(E_1,\ldots,E_K).$$

“最大值不超过 $d$”等价于每个 bin 都不超过 $d$，因此

$$F_D(d)=P(D\le d)=\left(1-e^{-d}\right)^K,\qquad f_D(d)=K e^{-d}\left(1-e^{-d}\right)^{K-1}.$$

若 CUT 端也使用同一搜索算子，给定训练统计量 $T$ 后，至少一个 Doppler bin 过门限的条件虚警为

$$P(D>T\mid T)=1-\left(1-e^{-T}\right)^K.$$

这说明“max”同时改变 CUT 尾概率和训练统计量分布，不能只在最后把单 bin 概率乘以 $K$。

### 3.4.2 训练端 max 与二维 GO 的组合

若为了说明 max 的影响，把训练端写成 $N$ 个独立 range cell 的搜索统计量 $D_j$，其和为 $S=\sum_{j=1}^{N}D_j$，并把门限写成 $T=cS$，则

$$P_{FA,search}(c)=E\left[1-\left(1-e^{-cS}\right)^K\right]
=\sum_{r=1}^{K}(-1)^{r+1}{K\choose r}\left[L_D(cr)\right]^N,$$

其中

$$L_D(t)=E[e^{-tD}]=K\int_0^1u^t(1-u)^{K-1}du=K B(t+1,K).$$

这只是搜索最大值的一维解释式。生产 GO 不使用 $S$ 或 $c$，而是直接从共享八块 $Y_j$ 构造

$$L=\frac{{Y_{{TL}}+Y_{{LC}}+Y_{{BL}}}}{{2n_{{dir}}}},\quad
R=\frac{{Y_{{TR}}+Y_{{RC}}+Y_{{BR}}}}{{2n_{{dir}}}},\quad
T=\frac{{Y_{{TL}}+Y_{{TC}}+Y_{{TR}}}}{{2n_{{dir}}}},\quad
B=\frac{{Y_{{BL}}+Y_{{BC}}+Y_{{BR}}}}{{2n_{{dir}}}},\quad M=\max(L,R,T,B).$$

因此生产虚警方程仍严格是

$$\boxed{{P_{{FA}}(\alpha)=E[e^{{-\alpha M}}]=10^{{-6}}}},$$

其中八块计数、共享角块相关性和 guard/background 几何均已在 3.2 节固定，不能用上面的 $K$ 或 $N$ 重新估计 $\alpha$。

### 3.4.3 数值自检与生产代入

当 $K=1$ 时，$D$ 退化为单个指数变量，$L_D(t)=1/(1+t)$；这只是本节搜索推导的边界自检，不是生产 CFAR 对照。生产 guard=4、background=16 时，$n_{dir}=656$，八块计数为 $[256,144,256,144,144,256,144,256]$。使用 60,000 个 IID 八块样本得到 $E[M]\approx1.03383$、中位数约 $1.03278$、90%分位数约 $1.07478$，数值求根

$$E[e^{-13.4495103198M}]=1.0\times10^{-6}$$

得到与生产 sidecar 一致的 $\alpha=13.4495103198$。这一步直接把 Doppler 搜索的 max 统计与真实二维 GO 的共享块模型接上，后续三层 $P_d$ 曲线只使用这个生产方程。

### 3.4.4 旁算例逐步数值代入：为什么不能把单 bin 系数直接套到 max

为使公式可以被复核，下面把用户指定的 $K=32,N=16$ 数字逐项代入。单 bin 归一化功率 $E=|n|^2/P_0$ 的尾概率是 $e^{-x}$。若训练和为 $S=\sum_{i=1}^{16}E_i$，则

$$P_{FA,1bin}(c)=E[e^{-cS}]=(1+c)^{-16}.$$ 

令 $P_{FA}=10^{-6}$，得到

$$c_0=10^{6/16}-1=1.3713737057,
\qquad 16c_0=21.9419792906.$$ 

这两个值只描述单 bin 指数训练和的门限自检。若每个 range cell 先做 $K=32$ 个 Doppler bin 的最大值 $D$，则

$$F_D(d)=(1-e^{-d})^{32},\qquad
E[D]=H_{32}=\sum_{k=1}^{32}\frac{1}{k}=4.0584951954.$$ 

把未经修正的 $c_0$ 错套到 16 个 $D_j$ 上，平均门限会变成

$$T_{naive}=16c_0E[D]=16\times1.3713737057\times4.0584951954
=89.0514175292,$$

相对单 bin 自检的 $21.9419792906$ 高出

$$10\log_{10}(89.0514175292/21.9419792906)=6.0836504\ \mathrm{dB}.$$ 

这 6.08 dB 是“把 max 当普通指数”的口径错误所造成的，不是 GMTI 生产 GO 的实测损失。正确的 max-search 虚警方程是

$$P_{FA,search}(c)=\sum_{r=1}^{32}(-1)^{r+1}{32\choose r}\left[32B(cr+1,32)\right]^{16}.$$ 

对它求 $10^{-6}$ 的根，得到

$$c_*=0.2798789723,\qquad \alpha_{search}=16c_*=4.4780635566,$$
$$P_{FA,search}(c_*)=9.999999999998\times10^{-7}.$$ 

边界检查 $K=1$ 时，$D=E$、$32B(t+1,32)$ 退化为 $1/(1+t)$，所以整个式子确实回到 $(1+c)^{-16}$。这一步说明：只要算法在某一维使用了 max，就必须连同 max 的 CDF/PDF 和训练端 Laplace 变换一起重算，不能在最后把单 bin 尾概率乘一个 $K$。

### 3.4.5 真实 GMTI 二维 GO：生产公式为何没有 $K=32$

需要特别区分旁算例和源代码。生产 GPU/CPU 路径不是“每个 range cell 先取 32 个 Doppler 最大值，再把 16 个 max 相加”；它对 **每一个二维 CUT** 直接读取外窗训练单元，计算四个方向均值并取最大值。因此生产公式中没有旁算例的 $S_D$、$c_*$ 或 $\alpha_{search}$。

guard=4、background=16 时，外窗宽 $2(g+b)+1=41$，保护窗宽 $2g+1=9$，训练环单元数为

$$41^2-9^2=1600.$$ 

八块按 TL、TC、TR、LC、RC、BL、BC、BR 计数为

$$n_j=[256,144,256,144,144,256,144,256],$$

四方向的真实归一化分母是

$$n_{dir}=b[2(g+b)+1]=16\times41=656.$$ 

令 $Y_j=\sum_{i\in j}2|n_i|^2/P_0$，则 $Y_j\sim\chi^2_{2n_j}$，并且

$$L=\frac{Y_{TL}+Y_{LC}+Y_{BL} }{ 2\times656},\quad
R=\frac{Y_{TR}+Y_{RC}+Y_{BR} }{ 2\times656},$$
$$T=\frac{Y_{TL}+Y_{TC}+Y_{TR} }{ 2\times656},\quad
B=\frac{Y_{BL}+Y_{BC}+Y_{BR} }{ 2\times656},\quad
M=\max(L,R,T,B).$$

角块同时被两个方向共享，故 $L,R,T,B$ 相关，不能把四方向当成四个独立 Gamma 变量。CUT 背景功率仍满足 $Z_{CUT}\sim Exp(1)$，给定 $M=m$ 时

$$P_{FA}(m)=P(Z_{CUT}>\alpha m\mid M=m)=e^{-\alpha m},$$

最终只解生产方程

$$\boxed{P_{FA,prod}(\alpha)=E[e^{-\alpha M}]=10^{-6} }.$$ 

本版使用 60,000 个 IID 八块样本得到 $E[M]\approx1.03383$、中位数 $\approx1.03278$、90% 分位数 $\approx1.07478$，数值根为

$$\alpha_4=13.4495103198,\qquad E[e^{-13.4495103198M}]=1.0\times10^{-6}.$$ 

下表把旁算例和生产量放在一起，避免读者误把参数混用。

| 量 | 旁算例（解释 max） | GMTI 生产 GO（正式） |
|---|---:|---:|
| Doppler max bin 数 $K$ | 32 | 不先做 Doppler-max，故不进入生产公式 |
| 训练 range 数 $N$ | 16 个 $D_j$ | 由二维窗口八块几何决定 |
| 保护窗/背景窗 | 未定义 | $g=4,b=16$，9×9/41×41 |
| 训练方向分母 | 未定义 | $n_{dir}=656$ |
| 共享块计数 | 未定义 | $[256,144,256,144,144,256,144,256]$ |
| PFA 根 | $c_*=0.2798789723$，$\alpha_{search}=4.4780635566$ | $\alpha_4=13.4495103198$ |
| 用途 | 解释搜索 max 的分布变化 | 生成全部三层 GO $P_d$ 曲线 |

### 3.4.6 从生产 alpha 到单元 Pd：一个可复算点

正式曲线的横轴是物理 CUT 等效 output SCNR。若该横轴为 10 dB，则

$$\gamma_{CUT}=10^{10/10}=10.$$ 

固定教材训练比 $m=1$ 时，门限比为 $\alpha_4m=13.4495103198$，代入非中心卡方尾概率

$$P_{D,fixed}=Q_1(\sqrt{2\gamma_{CUT} },\sqrt{2\alpha_4m })
=Q_1(\sqrt{20},\sqrt{26.8990206})=0.2709913.$$ 

把真实共享八块随机量 $M$ 积分后，A 层得到 $P_{D,A}=0.2435991$；把 24,000 个真实 C+N 训练窗的 $m_r=M_r/CUT_{background,r}$ 逐窗代入，0°中心 C 层为 $P_{D,C}=0.1543468$，再加入实测泄漏增量为 $P_{D,C+leak}=0.1541207$。这些数直接对应 `theory_unit_go_curves.csv` 的 10 dB 行，未使用 evaluation 命中率反拟合。

因此，报告后续三层曲线只使用 $\gamma_{CUT}=10^{SCNR_{out,CUT}/10}$ 和生产 $\alpha_4$。旁算例的 $K=32,N=16,c_*,\alpha_{search}$ 仅用于解释 max 统计的来源，不能被当成生产阈值。

本节每个中间数（$c_0$、$E[D]$、错误门限、精确根、生产 $\alpha_4$ 以及 10 dB 的四个 $P_d$）均同步写入 `doppler_max_worked_example.csv`，便于不依赖 PDF 排版直接复算。

### 3.4.7 Doppler-max 教学式逐步推导：从一句话到最终求根

为了让没有 CFAR 背景的读者也能复算，下面把上一节压缩的等式全部展开。这里的 $K=32$、$N=16$ 是用户指定的 **一维搜索旁算例**；它用于解释“搜索最大值会怎样改变统计量”，不替代真实二维 GO 的八块模型。

#### 步骤 1：先不搜索，普通单 bin CA-CFAR

无目标时复噪声写成

$$n=I+jQ,\qquad I,Q\sim\mathcal N(0,P_0/2),\quad I\perp Q.$$

归一化功率为

$$E=\frac{|n|^2}{P_0}=\frac{I^2+Q^2}{P_0}.$$

令 $u=I/\sqrt{P_0/2}$、$v=Q/\sqrt{P_0/2}$，则 $u,v\sim\mathcal N(0,1)$，所以

$$\frac{2|n|^2}{P_0}=u^2+v^2\sim\chi^2_2.$$

自由度 2 的中心卡方除以 2 是单位均值指数变量，因此

$$E\sim\operatorname{Exp}(1),\qquad f_E(e)=e^{-e},\qquad P(E>x)=e^{-x}.$$

设普通 CA 使用 $N=16$ 个训练单元，训练和为

$$S=E_1+E_2+\cdots+E_{16},$$

门限写为 $T=cS$。给定 $S=s$ 时，CUT 仍是独立单位指数变量，故

$$P(\text{CUT}>T\mid S=s)=P(E_{CUT}>cs)=e^{-cs}.$$

训练和本身是随机量，所以必须再平均一次：

$$P_{FA,1bin}(c)=E[e^{-cS}].$$

利用独立性，指数和的变换可以逐项拆开：

$$E[e^{-cS}]=E\left[\prod_{j=1}^{16}e^{-cE_j}\right]
=\prod_{j=1}^{16}E[e^{-cE_j}].$$

对一个单位指数变量直接积分：

$$E[e^{-cE}]=\int_0^\infty e^{-ce}e^{-e}de
=\int_0^\infty e^{-(1+c)e}de=\frac{1}{1+c}.$$

因此普通单 bin CA 的虚警率是

$$\boxed{P_{FA,1bin}(c)=(1+c)^{-16} }.$$

令 $P_{FA}=10^{-6}$，逐步反解：

$$ (1+c)^{-16}=10^{-6}
\Rightarrow 1+c=10^{6/16}
\Rightarrow c_0=10^{6/16}-1=1.3713737057.$$

若把 $S$ 用其均值 $E[S]=16$ 表示，平均门限为

$$E[T]_{1bin}=c_0E[S]=16c_0=21.9419792906.$$

这个 $c_0$ 只适用于“训练单元本身是单个指数 bin”的情形。

#### 步骤 2：一个 range cell 内做 Doppler-max

现在每个 range cell 不再保留一个 Doppler bin，而是在 $K=32$ 个 bin 中取最大：

$$D=\max(E_1,E_2,\ldots,E_{32}).$$

先求 CDF。事件 $D\le d$ 等价于 32 个 bin 全部不超过 $d$：

$$\begin{aligned}
F_D(d)&=P(D\le d)\\
&=P(E_1\le d,\ldots,E_{32}\le d)\\
&=\prod_{k=1}^{32}P(E_k\le d)\\
&=\left(1-e^{-d}\right)^{32},\qquad d\ge0.
\end{aligned}$$

对 CDF 求导得到 PDF：

$$f_D(d)=32e^{-d}\left(1-e^{-d}\right)^{31}.$$

因此至少一个 Doppler bin 超过固定门限 $t$ 的概率为

$$P(D>t)=1-F_D(t)=1-\left(1-e^{-t}\right)^{32}.$$

一个 $K=2$ 的小例子更直观：

$$P(\max(E_1,E_2)>t)=1-(1-e^{-t})^2
=2e^{-t}-e^{-2t},$$

这比一个 bin 的 $e^{-t}$ 大，因为两个独立机会中只要有一个超过门限就算命中。

#### 步骤 3：训练侧也必须使用 max，不能只改 CUT

第 $j$ 个训练 range cell 的统计量同样是

$$D_j=\max(E_{j,1},\ldots,E_{j,32}),\qquad j=1,\ldots,16.$$

于是训练和不再是普通的 $E_1+\cdots+E_{16}$，而是

$$S_D=D_1+D_2+\cdots+D_{16},\qquad T=cS_D.$$

CUT 端也有一个独立的 $D_0$。真实搜索旁算例的虚警事件是

$$\{D_0>cS_D\}.$$

给定 $S_D=s$ 时，使用步骤 2 的 CDF：

$$P(D_0>c s\mid S_D=s)=1-\left(1-e^{-cs}\right)^{32}.$$

对随机训练和求平均，得到尚未展开的完整表达式：

$$P_{FA,search}(c)=E\left[1-\left(1-e^{-cS_D}\right)^{32}\right].$$

#### 步骤 4：为什么出现二项式求和

对任意 $x$ 有 $(1-x)^K=\sum_{r=0}^{K}(-1)^r{K\choose r}x^r$。令 $x=e^{-cS_D}$、$K=32$：

$$\begin{aligned}
1-(1-e^{-cS_D})^{32}
&=1-\sum_{r=0}^{32}(-1)^r{32\choose r}e^{-crS_D}\\
&=\sum_{r=1}^{32}(-1)^{r+1}{32\choose r}e^{-crS_D}.
\end{aligned}$$

线性期望给出

$$P_{FA,search}(c)=\sum_{r=1}^{32}(-1)^{r+1}{32\choose r}E[e^{-crS_D}].$$

因此问题只剩下如何计算 $E[e^{-tS_D}]$。

#### 步骤 5：用 Laplace 变换处理训练和

定义

$$L_D(t)=E[e^{-tD}].$$

因为 $S_D=\sum_{j=1}^{16}D_j$ 且各训练 range cell 独立：

$$\begin{aligned}
E[e^{-tS_D}]&=E\left[e^{-t(D_1+\cdots+D_{16})}\right]\\
&=E\left[\prod_{j=1}^{16}e^{-tD_j}\right]\\
&=\prod_{j=1}^{16}E[e^{-tD_j}]\\
&=[L_D(t)]^{16}.
\end{aligned}$$

代回步骤 4：

$$\boxed{P_{FA,search}(c)=\sum_{r=1}^{32}(-1)^{r+1}{32\choose r}[L_D(cr)]^{16} }.$$

#### 步骤 6：从 max 的 PDF 到 Beta 函数

由步骤 2 的 PDF，Laplace 变换按期望定义为

$$\begin{aligned}
L_D(t)&=\int_0^\infty e^{-td}f_D(d)\,dd\\
&=32\int_0^\infty e^{-(t+1)d}(1-e^{-d})^{31}dd.
\end{aligned}$$

做换元 $u=e^{-d}$，则 $d=-\ln u$、$dd=-du/u$。积分上下限同步变化：

$$d=0\Rightarrow u=1,\qquad d\to\infty\Rightarrow u\to0.$$

又有 $e^{-(t+1)d}=u^{t+1}$，所以

$$\begin{aligned}
L_D(t)&=32\int_1^0u^{t+1}(1-u)^{31}\left(-\frac{du}{u}\right)\\
&=32\int_0^1u^t(1-u)^{31}du.
\end{aligned}$$

Beta 函数定义为

$$B(a,b)=\int_0^1u^{a-1}(1-u)^{b-1}du.$$

逐项对照可知 $a=t+1$、$b=32$，因此

$$\boxed{L_D(t)=32B(t+1,32)}.$$

最终的完整 max-search 虚警方程为

$$\boxed{P_{FA,search}(c)=\sum_{r=1}^{32}(-1)^{r+1}{32\choose r}\left[32B(cr+1,32)\right]^{16} }.$$

#### 步骤 7：$K=32,N=16$ 的每个数字如何代入

max 的均值可以由尾积分得到：

$$E[D]=\int_0^\infty P(D>d)\,dd
=\int_0^\infty\left[1-(1-e^{-d})^{32}\right]dd
=H_{32}=\sum_{k=1}^{32}\frac1k=4.0584951954.$$

如果错误地继续使用普通单 bin 系数 $c_0=1.3713737057$，训练侧平均门限变为

$$E[T]_{naive}=c_0\,16\,E[D]
=1.3713737057\times16\times4.0584951954
=89.0514175292.$$

与单 bin 的 $21.9419792906$ 比较，功率比和 dB 差分别是

$$\frac{89.0514175292}{21.9419792906}=4.0584951954,$$
$$10\log_{10}(4.0584951954)=6.0836504\ \mathrm{dB}.$$

这 6.08 dB 是把 max 训练量误当成普通指数训练量的门限口径错误，不是实际 GMTI 二维 GO 的额外损失。

正确做法是对上面的 $P_{FA,search}(c)$ 数值求根：

$$P_{FA,search}(c_*)=10^{-6}
\Rightarrow c_*=0.2798789723.$$

若把训练和写成 16 个 range cell 的平均量，等价的平均系数为

$$\alpha_{search}=16c_*=4.4780635566,$$

代回完整求和，得到

$$P_{FA,search}(c_*)=9.999999999998\times10^{-7}.$$

#### 步骤 8：两个边界自检

第一，若 $K=1$，则 $D=E$，

$$L_D(t)=\int_0^\infty e^{-(t+1)d}dd=\frac1{1+t}.$$

求和只剩 $r=1$：

$$P_{FA,search}(c)=\left(\frac1{1+c}\right)^{16},$$

正好回到普通 CA-CFAR，说明推广在 $K=1$ 时退化正确。

第二，若门限固定为 $T$ 而不是随机训练和，则只有 CUT 端搜索，虚警为

$$1-(1-e^{-T})^{32}\approx32e^{-T}\quad(e^{-T}\ll1).$$

因此“PFA 约乘 32”只适用于固定门限的 CUT 搜索；当前链路的 16 个训练单元也同时做 max，不能使用这个近似。

#### 步骤 9：这个旁算例与真实 GMTI 生产 GO 的关系

旁算例假设“每个 range cell 先做 32-bin Doppler-max，再把 16 个 max 相加”。实际 GMTI 生产代码对每个二维 CUT 直接读取 $41\times41$ 外窗，形成四个方向均值并取最大：

$$M=\max(L,R,T,B),$$

其中八个共享块计数为 $[256,144,256,144,144,256,144,256]$，四个方向分母均为 $2\times656$。因此生产链不使用旁算例的 $c_*$、$\alpha_{search}$ 或 $K=32$；生产系数仍由

$$P_{FA,prod}(\alpha)=E[e^{-\alpha M}]=10^{-6}$$

求得，guard=4 的实际值为 $\alpha_4=13.4495103198$。旁算例只解释 max 统计为什么不能套用单 bin CA 系数，生产曲线和后续 Pd90 均使用真实二维 GO 的 $\alpha_4$。

#### 步骤 10：适用条件和报告口径

上述 Beta 闭式只在无目标、复高斯背景、Doppler bins 独立、训练 range cells 独立、CUT 与训练窗独立时成立。真实 C+N 背景会引入非 IID、相关性和训练窗泄漏，所以本报告另外给出真实 C+N 窗逐窗经验积分；不能用旁算例的精确闭式替代真实背景，也不能用实测 hit rate 反向拟合理论。

# 4. 三层 GO 单元级理论

![0°中心三层 GO 曲线与 exact CUT 实测。](figures/__GO_0_FIG__)

__GO_0_TABLE__

上图每条曲线的计算过程如下：先用本章 2.2 的回归把横轴 output SCNR 换成 $\gamma_{{CUT}}$，再用同一 alpha 和对应模型的 $M$ 分布计算 $Q_1$。圆点是 one-seed 真实 CUDA 的 exact physical CUT hit，误差棒是其二项比例的 Wilson 95% 区间。表格上半部是 6、10、15、20 dB 的理论锚点；下半部以“实测 xx.xx”保留每个实验点的真实 output SCNR，理论列取最近的 0.5 dB 网格值，仅作并排读取，绝不把实测点回填到错误的理论横坐标。完整逐点数据在 `theory_unit_go_curves.csv` 和 `unit_target_track_summary.csv`，实测命中率没有参与任何理论拟合。

![30°中心三层 GO 曲线与 exact CUT 实测。](figures/__GO_30_FIG__)

__GO_30_TABLE__

30°图使用同样的八块共享角块和 guard=4 alpha，仅替换该角度的支撑诊断回归；因此它直接显示波束中心角度变化如何影响统一 CUT-equivalent output 轴上的理论曲线和实测点。

![30°边缘三层 GO 曲线（calibration-only）。](figures/__GO_30_EDGE_FIG__)

__GO_30_EDGE_TABLE__

30°边缘曲线来自本轮独立的 +0.8° calibration-only CUDA run。它共享 30°的支撑诊断映射，但目标支撑形状、背景窗口和泄漏模板均不从 30°中心评价命中率复制；由于目前没有对应的边缘 S+C+N evaluation root，图中不绘制实测 Pd 点。

![45°中心三层 GO 曲线与 exact CUT 实测。](figures/__GO_45_FIG__)

__GO_45_TABLE__

45°图的 B/C+leak 若没有合格 S-only 支撑模板会留空，不能用拍脑袋泄漏功率填补；A、C 和固定 $m=1$ 仍可计算。45°实测 exact 点未达到 0.9 时，表格和 Pd90 审计均保留“未达”，不外推。

![45°边缘三层 GO 曲线（calibration-only）。](figures/__GO_45_EDGE_FIG__)

__GO_45_EDGE_TABLE__

45°边缘同样是 +0.8° calibration-only profile。它用于显示波束边缘支撑形状对 PB/泄漏模型参数的影响，不被解释为正式目标级 Pd；正式主图仍只使用 0°/30°/45°中心 evaluation 数据。

![0°边缘三层 GO 曲线（历史支撑模型）。](figures/__GO_EDGE_FIG__)

__GO_EDGE_TABLE__

0° edge 曲线也只有 calibration-only profile，表中实测 exact 列为空；它不冒充当前 0°中心 one-seed 事件。三种边缘曲线的 C+N 经验背景层只有历史 0° edge 窗可用，因此 30°/45° edge 的 C 层会留空，不以中心或历史 0°背景替代。

## 4.1 A 层：IID Gaussian、无目标泄漏

对每个八块，中心噪声功率和满足 $Y_j/2\sim Gamma(n_j,1)$，共享重组得到随机 $M$。给定物理 CUT SCNR $\gamma$，复确定信号加噪声的检测统计量满足

$$\frac{{2|s+n|^2}}{{P_0}}\sim\chi'^2_2(2\gamma).$$

固定训练实现 $M=m$ 时

$$P_D(\gamma\mid m)=P\left(\chi'^2_2(2\gamma)>2\alpha m\right)
=Q_1(\sqrt{{2\gamma}},\sqrt{{2\alpha m}}).$$

因此 A 层是

$$P_{{D,A}}(SCNR_{{out}})=E_M\left[Q_1\left(\sqrt{{2\gamma_{{CUT}}(SCNR_{{out}})}},\sqrt{{2\alpha M}}\right)\right].$$

本版每个 guard 使用 __THEORY_SAMPLES__ 个 IID 八块样本；不是把 $M$ 固定成 1。因为 $E[M]≈1.03$，A 曲线相对固定 $m=1$ 有一个小而正确的右移。

## 4.2 B 层：目标泄漏的 noncentral 八块

训练单元不再被写成“固定偏置”，而是

$$x_i=s_i+n_i,\qquad \frac{{2|x_i|^2}}{{P_0}}\sim\chi'^2_2\left(\frac{{2|s_i|^2}}{{P_0}}\right).$$

第 $j$ 块含 $n_j$ 个单元时，块和为

$$Y_j=\sum_{{i\in j}}\frac{{2|x_i|^2}}{{P_0}}
\sim\chi'^2_{{2n_j}}(\Lambda_j),\qquad
\Lambda_j=\sum_{{i\in j}}\frac{{2|s_i|^2}}{{P_0}}.$$

若把 output 轴对应的 CUT 线性 SCNR 记作 $\gamma$，从 S-only 目标支撑实测得到每块每单位 $\gamma$ 的模板 $\lambda_j$，则 $\Lambda_j(\gamma)=2\gamma\lambda_j$。注意代码先生成 $Y_j/2$ 作为原始功率和，再按方向分母 $656$ 组合；如果先除以自由度、随后再除以 656，就会重复归一化，产生“无泄漏时 Pd 也接近 1”的伪曲线。本版已修正。

S-only 支撑提取流程是：在同一生产 GO 窗内用 guard 外功率中位数估计 floor；对每个训练单元取 $\max(P_S-P_{{floor}},0)$；用 paired C+N 的方向最大值作 $P_0$；只把 measured CUT excess / $P_0≥1$ 的样本作为模板形状，低信噪比样本仍留在 support CSV 但不参与拟合。摘要如下：

__TEMPLATE_TABLE__

对每个 template，直接采样八个 noncentral-$\chi^2$ 块并共享角块：

$$P_{{D,B}}(SCNR_{{out}})=E\left[Q_1\left(\sqrt{{2\gamma_{{CUT}}}},\sqrt{{2\alpha M(\gamma_{{CUT}})}}\right)\right].$$

guard=4 是实际测量的模型。guard=3/5 曲线仍按用户要求生成，但因原始 BIN 已按授权清理，使用 guard=4 逐块模板的形状保持投影，只能叫“反事实投影”，不作为 guard-specific 逐单元泄漏的精确实测。历史泄漏模板中 45°中心 qualified=0，因此该组 B/C+leak 留空而非用低质量模板硬算；边缘 profile 仅用于支撑形状校准。

## 4.3 C 层：真实 C+N 背景经验积分

从保留的 C+N-only CSI/GO 输入图逐窗抽取并保存

$$\{\bar Z_L,\bar Z_R,\bar Z_T,\bar Z_B,M,CUT_{{background}},M/CUT_{{background}}\}.$$

对第 $r$ 个窗令 $m_r=M_r/CUT_{{background,r}}$，则无泄漏 C 层为

$$P_{{D,C}}(SCNR_{{out}})=\frac1R\sum_{{r=1}}^R Q_1\left(\sqrt{{2\gamma_{{CUT}}}},\sqrt{{2\alpha m_r}}\right).$$

加入同角度的实测方向泄漏增量 $\delta_d$ 后：

$$P_{{D,C+leak}}=\frac1R\sum_rQ_1\left(\sqrt{{2\gamma_{{CUT}}}},\sqrt{{2\alpha\max_d(m_{{r,d}}+\gamma_{{CUT}}\delta_d)}}\right).$$

这就是半解析模型：背景形状不假设 IID，而是来自生产窗；CUT 仍用可解析的 noncentral-$\chi^2$/Marcum-$Q$ 尾概率。`empirical_go_windows.csv` 的 96,000 行可以逐窗复核。下图是 $M/CUT_{{background}}$ 长尾的直观证据。

![真实 C+N 训练窗的长尾。](figures/__WINDOW_FIG__)

图中横轴是对每窗归一化比值取 $\log_{{10}}$，不是 Pd 曲线；它解释了为什么 C 层比 IID 层需要更高 output SCNR。C+leak 的右移表如下（45°因无合格泄漏模板留空）：

__SHIFT_TABLE__

![目标泄漏造成的 0.5-Pd 右移。](figures/__SHIFT_FIG__)

“右移”定义为同一模型在 output SCNR 轴上达到 $P_d=0.5$ 的交点差，不是输入 SNR 差，也没有对网格外做外推。完整三层曲线分别在 `theory_unit_go_curves.csv`。

# 5. 为什么实测单元 Pd 会高于教材曲线：逐项排查

1. **先查坐标**：教材的 $\gamma$ 是物理 CUT；现行实测主轴也定义为 CUT-equivalent output SCNR。固定 3×3 支撑增量只在 `support_output_scnr_db` 中诊断，不能直接代入教材式。
2. **再查事件**：`unit_exact_pd` 是物理投影 FFT CUT；`unit_resolution_pd` 是 truth 周围 ±2 range/±2 Doppler bins 内任一 GO hit。后者天然更宽松，45°处接近 1 不能与单 CUT Q1 对照。
3. **再查 max 结构**：A 层对共享八块的随机 $M$ 积分；固定 $m=1$只是教材基线，不能要求 A 与其完全重合。
4. **再查输出链**：S+N 与 N-only 使用固定 support，N-only P0 只离线用于 SCNR 轴，不写回在线 detector。没有利用 evaluation 命中率回拟合 mapping。
5. **再查实现 bug**：历史 NMS truth tie-break 已删除；物理 Doppler 行号映射已修正；本轮还修正 noncentral block 的重复归一化。上述每项都在脚本/manifest/审计中有记录。

6. **最后查表格是否把不同 SCNR 的点错位**：旧版图旁表曾把“距离理论锚点最近的实测行”直接写进 6/10/15/20 dB 行，例如 0° 的 13.94 dB、$P_d=0.867$ 被误放到 6 dB，造成“实测远好于教材”的假象。本版已改为理论锚点与实测真实 output SCNR 分行；在真实坐标上，0° 13.94 dB 实测约 0.867（固定-$m=1$ 理论约 0.976），30° 13.87 dB 实测 1.000（理论约 0.976，属于 $n=15$ 的有限样本波动），45° 13.10 dB 实测约 0.200（理论约 0.888）。因此修正后的证据不支持“单元级实测系统性优于教材”，剩余差异应继续按 CUT 对齐、角度投影和样本量排查。

因此，实测曲线“比教材好”不能直接解释为算法增益；此前主要是教材单 CUT 与 3×3 support 轴混用。统一到 CUT-equivalent 主轴后，教材固定-$m=1$ 的 13.0872 dB 是共同基准；若实测在有限网格内低于它，只能归因于有限样本/实现差异并需继续排查，不能用轴变换掩盖。

## 5.1 Pd90 轴审计表

__PD90_TABLE__

教材物理 CUT/输出值由方程

$$\operatorname{{ncx2.sf}}(2\alpha,2,2\gamma_{{CUT}})=0.9$$

用一维根求得 $\gamma_{{CUT}}=20.36$（13.0872 dB）。A 层的 crossing 用真实随机 $M$ 曲线求得，故略大于教材；实测列严格使用 exact CUT 事件和当前有限网格，未达不外推。3×3 支撑诊断轴只用于解释主瓣能量分散，不参与 Pd90 比较。

# 6. 目标级：min_points=2/3/6 的完整测试

目标级不能由单元 Pd 乘一个拍脑袋系数替代。生产事件为：GO hit map → 连通 component → min_points/小簇 20 dB 分支 → 选择/定位 → truth match。每档目标分母为 15 个 screen events。

$$P_{{target}}=P_{{cluster}}P(select\mid cluster)P(reloc\mid select,cluster)P(match\mid reloc,select,cluster).$$

当前 retained CSV 没有把 select、reloc、match 三个布尔状态逐项落盘，故只报告可审计的最终 `target_pd`，不把一个联合值伪拆成三个因子。

## 6.1 min_points=2

![min_points=2 目标级曲线。](figures/target_pd_minpoints_2.png)

图 6.1 每个圆点是 `target_hits/target_trials`，阴影是 Wilson 95% 区间；目标级事件已包含连通性和 truth match。对应逐档表：

__TARGET2_TABLE__

## 6.2 min_points=3

![min_points=3 目标级曲线。](figures/target_pd_minpoints_3.png)

__TARGET3_TABLE__

## 6.3 min_points=6

![min_points=6 目标级曲线。](figures/target_pd_minpoints_6.png)

__TARGET6_TABLE__

三张图必须分开读：min_points 只改变目标级聚类门槛，不改变 unit GO threshold。低 SCNR 下 exact unit 已有命中但 cluster/target 为 0，正是“注入目标只占一个/少数单元时聚类通不过”的可观测表现；降低 min_points 会提高目标级 Pd，但也必须在正式多 seed 中重新测假警和误聚类。

## 6.4 目标级 Poisson-binomial 解析 baseline

上面三张是生产链的实测目标事件；本节给出此前第 7 节要求保留的 **解析 baseline**，不把它冒充为生产主指标。校准阶段从 S-only/C+N-only 功率图测得 3×5 支撑 footprint，共 $K=15$ 个候选单元。对第 $i$ 个单元，先定义信号占比与背景占比：

$$\kappa_i=\frac{{P_S(i)}}{{\sum_{{j=1}}^{{15}}P_S(j)}},\qquad
\beta_i=\frac{{P_0(i)}}{{\operatorname{{median}}_j P_0(j)}},\qquad
\gamma_i=\gamma_{{CUT}}\frac{{\kappa_i/\beta_i}}{{\kappa_7/\beta_7}},\qquad \gamma_7=\gamma_{{CUT}}.$$ 

其中 $P_{S,i}$ 是 calibration S-only 图减局部 guard 外中位 floor 后的非负增量，$P_{0,i}$ 是配对 C+N-only 图的生产 GO 最大方向均值；所有形状来自 `target_support_profiles.csv`，没有按 evaluation 命中率重新拟合。现行主轴是物理 truth-CUT 的 CUT-equivalent output SCNR，故以 3×5 中心格 $i=7$ 为锚点：

3×3 总支撑 SCNR 仍作为诊断，用来展示主瓣能量分散和背景非均匀性；不再把它当作单 CUT 理论的横轴。当前 profile 的实际形状摘要如下（$\kappa_7$ 是 3×5 中心单元的信号能量占比；$\sum\kappa_{3\times3}$ 与 $\sum\beta_{3\times3}$ 只用于支撑损失诊断）：

__PB_PROFILE_TABLE__

![校准目标支撑形状：κ 信号能量占比与 β 背景相对系数。](figures/__PB_PROFILE_SHAPE_FIG__)

图 6.4a 的每个小格是同一组 calibration S-only/C+N-only 配对图在 3×5 支撑上的中位数。κ 先除以 15 个格子的总信号增量，因此所有 κ 加起来等于 1；β 除以该支撑的中位背景，因此 β=1 表示典型背景。0°/30°/45°的中心与 +0.8°边缘 profile 的中心格（索引 7）约占 79%–82% 信号，而 β 仅在约 0.93–1.06 之间变化。这张图直接说明当前点目标的物理主瓣集中在一个 FFT 分辨单元，min_points=6 不能被解释成“六个等概率单元同时命中”；它也说明报告中的 PB 曲线使用的是实测形状，而不是为了贴合 evaluation 命中率人为摊平功率。

对每个 $\gamma_i$，单元 GO 概率由上一章同一个共享八块 $M$ 积分：

$$p_i(SCNR_{out})=E_M\left[Q_1\left(\sqrt{2\gamma_i},\sqrt{2\alpha M}\right)\right].$$

若暂时只问 15 个候选中有多少个过门限，令 $H_i\sim Bernoulli(p_i)$、$K_H=\sum_iH_i$。生成函数为

$$G(z)=\prod_{i=1}^{15}\big[(1-p_i)+p_i z\big],\qquad
P^{PB}_{cluster}(m)=P(K_H\ge m)=\sum_{k=m}^{15}[z^k]G(z).$$

数值实现不展开 15 重组合，而用系数递推：$d_{0,0}=1$，加入第 $i$ 个单元后

$$d_{i,k}=d_{i-1,k}(1-p_i)+d_{i-1,k-1}p_i,$$

最终将 $\sum_{k=m}^{15}d_{15,k}$ 写入 `target_pd_poisson_binomial.csv`。这里连接性因子固定为 $\eta_{connectivity}=1$，因此它只代表“点数达到 m”的上限式/解析参照；真实连通 component、3–5 点 +20 dB 小簇恢复、select、reloc 和 truth match 仍只能由生产 joint hit mask 和审计事件给出。

具体代入示例：0°中心 CUT-equivalent output SCNR=10 dB 时，中心锚定得到 $\gamma_7=10$ dB；3×5 profile 总量相对中心格的换算偏置为 **__PB_OFFSET__ dB**，$K=15$、$n_{cal}=__PB_NCAL__$、$\alpha=13.4495103$，将 15 个 $\kappa_i/\beta_i$ 逐个带入上式，min_points=6 的 PB 值为 **__PB_EXAMPLE__**。这里没有用 evaluation 命中数调曲线。

下表列出六组 profile 形状的独立 baseline；所有 edge 行来自本轮 +0.8° calibration-only 支撑测量，不等于中心 one-seed evaluation 实测。C+N 经验背景层只有历史 0° edge 窗，因此 edge 的 PB 形状可以比较，C 层经验曲线则只在有对应背景窗时显示：

__TARGET_PB_TABLE__

__TARGET_PB_FIGS__

图中的黑色叉号（若有）只是当前 one-seed 生产 target Pd 的独立叠加，不参与 PB 计算。PB 的随机变量是“固定 3×5 候选 support 中至少有 $m$ 个单元过门限”，且 $eta_{{connectivity}}=1$；生产 target 则使用整幅 hit map 的连通分量、Doppler 环绕、range gap=2、3–5 点小簇 +20 dB 恢复和后级筛选。因此两者不是同一个事件，不能要求曲线逐点重合。

## 6.5 PB 与生产事件不吻合的形态审计

为避免只凭文字猜测，本版对保留的历史联合 hit-mask 紧凑记录做了结构审计。该记录共 5 个 seed、150 个 truth events，父运行没有后来的 `strict_online_no_prior` 标志，所以这里只用于解释“为什么 PB min6 右移”，不作为当前正式 Pd；审计不读取 raw BIN，也没有用 evaluation 命中率拟合任何曲线。

__MORPHOLOGY_TOTALS__

定义 $K_{{local}}$ 为 truth 周围固定 3×5 support 的命中数，$K_{{full}}$ 为生产整图连通 component 大小。PB min6 近似计算 $P(K_{{local}}ge6)$；而生产 min6 事件实际是

$$I_{{prod}}=I(K_{{full}}ge6)\;\lor\;\left[I(3\le K_{{full}}<6)\land I(peak/background\ge 10^{{20/10}})\right].$$

这两个指示变量的差别就是结构性差异，而不是 SCNR 轴或 $alpha$ 调错。逐档数值如下；`生产命中中小簇` 和 `生产命中中局部<6` 是条件比例，分母为该档生产 cluster 命中数，若该档没有命中则留空。

__MORPHOLOGY_TABLE__

![PB 与生产连通形态审计。](figures/__MORPHOLOGY_FIG__)

图 6.5 左上把局部 3×5 support 与全图 component 的平均大小直接对比，红色虚线是 min_points=6；右上同时画出局部/全图达到 6、严格 min6、生产 cluster 和最终 target 的事件率。左下只对生产命中条件化，显示其中绝大多数来自 3–5 点小簇 +20 dB 分支且局部 support 仍少于 6 点。右下把 PB min6 与历史生产/target 事件叠加：在历史最高档 output SCNR≈18.69 dB 时，当前校准 profile 的 PB min6 约为 **__MORPHOLOGY_PB_HIGH__**，而生产 cluster/target 已约为 0.933；差异不是“18–19 dB 仍为零”，而是 PB 只计算固定局部点数事件，生产链还允许小簇峰值恢复，这正是原先 PB 预测右移的可追溯原因。

因此，PB 曲线应保留为“独立单元点数 baseline”，不能再被用作 min6 生产 target 的理论真值。若要建立与生产严格一致的半解析模型，下一步必须对 full-map component 的联合形态和小簇峰值条件建模，而不是增加 PB 的独立候选单元数或用实测命中率反拟合。

## 6.6 单元→cluster→target 漏斗

![min_points=2 漏斗。](figures/funnel_minpoints_2.png)

![min_points=3 漏斗。](figures/funnel_minpoints_3.png)

![min_points=6 漏斗。](figures/funnel_minpoints_6.png)

漏斗中的四条线来自同一逐档记录：exact CUT、±2-bin resolution、cluster、最终 target。它们分别回答“物理 CUT 是否过门限”“主瓣邻域是否有命中”“joint hit mask 是否成簇”“生产目标输出是否成功”。任何一条线都不能替代另外三条。

# 7. 航迹级：三屏选二与相关性

冻结主指标：同一 truth 轨迹的三屏事件 $D_1,D_2,D_3$ 中至少两个为 1。

若三屏独立且单屏目标级 Pd 相同为 $p$：

$$P_{{track,ind}}=P(\sum_tD_t\ge2)=3p^2(1-p)+p^3=3p^2-2p^3.$$

实测不强行独立，直接使用

$$P_{{track}}=P_{{12}}+P_{{13}}+P_{{23}}-2P_{{123}}.$$

并保存 $P(D_t=1\mid D_{{t-1}}=1)$、$P(D_t=1\mid D_{{t-1}}=0)$ 和 pairwise correlation。当前摘要：

__DEP_TABLE__

![三屏 track 对比：min_points=2。](figures/track_pd_minpoints_2.png)

![三屏 track 对比：min_points=3。](figures/track_pd_minpoints_3.png)

![三屏 track 对比：min_points=6。](figures/track_pd_minpoints_6.png)

当条件命中明显高于条件未命中、相关系数显著偏离 0 或 IE 与独立式分开时，必须采用 IE 实测值；不能用 $3p^2-2p^3$掩盖跨屏共享噪声/轨迹状态。TrackManager `Confirmed+matched_this_frame` 只保留作附录诊断，不作为本专项主航迹 Pd。

# 8. 测角理论和逐模块验证

## 8.1 Doppler—角度几何

生产定义为

$$f_D=-\frac{{2V}}{{\lambda}}\sin\theta,\qquad \hat\theta=-\arcsin\left(\frac{{\lambda f_D}}{{2V}}\right).$$

本项目参数来自 `gmti.xml`/模块审计：$f_c=16.4721130769$ GHz，$\lambda=c/f_c=0.0182$ m，双通道距离 $d=0.17$ m，平台速度 $V=60$ m/s。Doppler 和 ENU 投影模块分别在 $-45,-30,-10,0,10,30,45$°上往返误差 0。

## 8.2 双通道相位和 P38 斜率

每通道 $x_i=e^{{j\phi}}+n_i$、$n_i\sim CN(0,1/\gamma_i)$，估计 $\hat\phi=\arg(x_1x_2^*)$。小噪声线性化给出

$$\sigma_\phi^2\simeq\frac1{2\gamma_1}+\frac1{2\gamma_2}=\frac1{\gamma_\phi},\qquad \gamma_\phi=\frac{{2\gamma_1\gamma_2}}{{\gamma_1+\gamma_2}}.$$

对 $\phi_i=kf_i+b+\epsilon_i$ 做中心化 OLS，令 $S_{{ff}}=\sum_i(f_i-\bar f)^2$，则

$$\hat k-k=\frac{\sum_i(f_i-\bar f)\epsilon_i}{S_{{ff}}},\qquad \sigma_k=\frac{\sigma_\phi}{\sqrt{{S_{{ff}}}}}.$$

模块 MC 结果使用 20,000 个 phase trials/点、5,000 个 P38 trials/点；生产验证结果保存在 `angle_modules/angle_module_summary.csv`，Doppler/投影误差均为 0，phase/P38 的高 SNR 区间与上述一阶方差一致。P38 多频点平均只会降低噪声，不能用来把真实低 SNR 的错误 unwrap 隐藏掉。

## 8.3 Jacobian 到角度 RMSE 曲线

相位—角度关系 $\phi=(2\pi d/\lambda)\sin\theta$，微分得

$$J_{{\theta\phi}}=\left|\frac{{d\theta}}{{d\phi}}\right|=\frac{{\lambda}}{{2\pi d\cos\theta}}.$$

代入 $\lambda=0.0182$ m、$d=0.17$ m：

$$J_0=0.01703894,\quad J_{{30}}=0.01967487,\quad J_{{45}}=0.02409670.$$

若有效相位 SCNR 为 $\gamma_\phi$ 且 $\sigma_\phi≈1/\sqrt{{\gamma_\phi}}$，则

$$\sigma_\theta(\theta)=\frac{{\lambda}}{{2\pi d\cos\theta\sqrt{{\gamma_\phi}}}},\quad
\sigma_\theta[deg]=\frac{{180}}{{\pi}}\sigma_\theta[rad].$$

本版直接使用 CUT-equivalent output SCNR 作为相位有效 SCNR 的统一横轴；生产点只用实际 truth-match 的有限误差样本，低匹配数点显式保留，不向下平滑。

__ANGLE_IMAGES__

每张图的黑虚线是上述 Jacobian 理想曲线，彩色点线是该 min_points 的生产 truth-match RMSE。若低档只有 1 个匹配，RMSE 可以偶然很小，不能当作高置信精度；高 SCNR 区间的 RMSE 才适合与理论趋势比较。

# 9. 三 seed min_points=6 模型验证

理论闭环后，使用已通过同一严格在线规则的 3 个独立 seed（20260910、20260920、20260930）做小规模模型验证。每个 seed 在 0°/30°/45°各有 10 个 output-SCNR 档位，每档 15 个 screen truth trial 和 5 个三屏 track event；合并后每档为 45 screens、15 tracks。这里只验证随机性和实现稳定性，背景仍为 thermal-noise-only，不能替代最终 S+C+N 多 seed 统计。

__MULTISEED_TABLE__

三 seed 汇总只累加各根的 hits/trials，主横轴使用各 seed 的 CUT-equivalent 输出功率比在功率域求均值；固定 3×3 支撑量只作诊断，没有按 pooled 命中率重新拟合轴。主航迹仍是同一 truth 轨迹三屏选二，独立 $3p^2-2p^3$ 只做辅助对照。

__MULTISEED_FIGS__

__SCN_MULTISEED__

__SCN_MINPOINTS__

__SCN_EDGE__

__TRUTH_AXIS__

# 11. 信息泄露、实现修复和证据边界

在线检测器可见信息和离线评分信息必须分开：

| 项目 | 规则 |
|---|---|
| truth | 不进入生产 XML；只做离线支撑、评分、matching、RMSE |
| N-only | 严格模式只用于离线 P0/坐标轴；不写回 S+N detector |
| S-only | 只做 support/线性诊断和理论泄漏模板；不改变 S+N 在线门限 |
| NMS | 已删除 truth-derived position-error tie-break |
| Doppler | 评分使用每条运行自身物理 Doppler 元数据映射；不是复用另一条噪声实现的行号 |
| raw BIN | GO/CSI、逐周期 detection、TrackManager payload 和 truth-match 审计成功后按授权删除；当前 corrected roots 剩余 BIN=0 |

当前嵌入的 S+C+N formal 根均通过审计：每根 12 pipelines，`truth_active_detector_count=0`、`diagnostic_truth_path_count=0`、`strict_online_no_prior=true`。新增的 compact calibration seed（中心 20261011、0°边缘 20261012）同样逐 pipeline 使用 `strict_online_no_prior=true`；profile 汇总 manifest 明确 `evaluation_hit_rates_used_for_fit=false`、`maps_opened=false`、`raw_bin_opened=false`。因此“实测比理论高”不能归因于 detector 预先看到了 truth。

本报告仍有三项边界，不能省略：

1. 第 6–8 章的 min_points=2/3/6 主对照仍是 thermal-noise-only，area clutter intentionally disabled；第 9.1 节是 3-seed compact-statistical S+C+N 的 min6 中心验证，第 9.2 节补充了同一 seed 下 min2/3/6 的 S+C+N 对照，第 10 节新增了 +0.8° 边缘压力测试，但它们都只覆盖少量档位/屏数，不能说已完成全场景复杂杂波最终验收。
2. 目标层 retained schema 未逐项记录 select/reloc/match，故只给联合生产事件；要拆出四个条件概率，下一轮必须在边界写入审计字段。
3. min_points=2/3/6 的第 6–8 章 thermal-noise-only 主结果仍是 one‑seed；第 9.2 节真实 S+C+N 三种门槛已补齐 3 个独立 seed，但每角度 pooled 仅 108 periods、36 个 screen event、12 个三屏 track event，Wilson 区间仍较宽。所有配置都不能据此宣称最终 90% 门限；若进入正式 sweep，应保持本报告的 output‑SCNR 轴和审计规则，并用独立背景批次复核假警与误聚类。

# 12. 复现入口、产物和阅读顺序

最短复现（只重建报告，不重新生成 CUDA 原始数据）：

```bash
python3 scripts/gmti_scnr_eval/34_build_comprehensive_report.py \\
  --output-dir outputs/gmti_scnr_eval/comprehensive_report_20260829 \\
  --pdf docs/GMTI_输出SCNR_测角精度与检测概率_综合分析报告_20260829.pdf \\
  --html docs/GMTI_输出SCNR_测角精度与检测概率_综合分析报告_20260829.html
```

主要机器表：`theory_unit_go_curves.csv`、`go_alpha_guard.csv`、`doppler_max_worked_example.csv`、`model_shift_summary.csv`、`output_to_cut_mapping.csv`、`unit_target_track_summary.csv`、`track_dependency_summary.csv`、`angle_theory_curves.csv`、`target_pb_morphology_audit.csv`、`current_formal_morphology_summary.csv`、`target_support_profiles.csv`。主要证据源及 SHA-256 记录在 `report_manifest.json`；图像全部在 `figures/`，模块化测角证据在 `angle_modules/`。形态审计也可单独复现：`python3 scripts/gmti_scnr_eval/35_audit_target_pb_morphology.py --output-dir outputs/gmti_scnr_eval/comprehensive_report_20260829 --pb-path outputs/gmti_scnr_eval/comprehensive_report_20260829/target_pd_poisson_binomial.csv`。目标支撑 profile 的跨 seed 汇总可复现：`python3 scripts/gmti_scnr_eval/36_build_target_support_profiles_from_compact.py --run-root outputs/gmti_scnr_eval/target_support_calibration_corrected_20260830/seed_20261011 --output-dir outputs/gmti_scnr_eval/target_support_calibration_corrected_20260830`。

建议阅读顺序：先看本章结论和第 2.2 坐标定义，再看第 3–5 章单元理论闭环，随后看第 6–7 章目标/航迹，再看第 8 章测角，最后看第 9–10 章三 seed 验证和审计限制。
'''
    replacements = {
        "__ALPHA__": f"{alpha_rows[1]['alpha']:.10f}",
        "__PFA__": f"{alpha_rows[1]['mc_pfa']:.3g}",
        "__WINDOWS__": str(sum(x["count"] for x in windows.values())),
        "__MAPS__": str(sum(x["maps"] for x in windows.values())),
        "__SUMMARY_TABLE__": summary_md,
        "__MAPPING_TABLE__": mapping_md,
        "__ALPHA_TABLE__": alpha_md,
        "__TEMPLATE_TABLE__": template_md,
        "__SHIFT_TABLE__": shift_md,
        "__PD90_TABLE__": pd90_md,
        "__DEP_TABLE__": dep_md,
        "__TARGET2_TABLE__": target2_md,
        "__TARGET3_TABLE__": target3_md,
        "__TARGET6_TABLE__": target6_md,
        "__TARGET_PB_TABLE__": target_pb_md,
        "__TARGET_PB_FIGS__": pb_figures_md,
        "__PB_EXAMPLE__": pb_example,
        "__PB_OFFSET__": pb_offset,
        "__PB_NCAL__": source_info.get("target_pb_ncal", "—"),
        "__PB_PROFILE_TABLE__": source_info.get("target_profile_table", ""),
        "__PB_PROFILE_SHAPE_FIG__": figures["target_profile_shape"].name,
        "__MORPHOLOGY_TABLE__": morphology_md,
        "__MORPHOLOGY_FIG__": morphology_figure_name,
        "__MORPHOLOGY_TOTALS__": source_info.get("morphology_totals_text", ""),
        "__MORPHOLOGY_PB_HIGH__": source_info.get("morphology_pb_highest", "—"),
        "__MULTISEED_TABLE__": multiseed_md,
        "__MULTISEED_FIGS__": multiseed_figures_md,
        "__SCN_MULTISEED__": source_info.get("scn_validation_md", ""),
        "__SCN_MINPOINTS__": scn_minpoints_markdown_text,
        "__SCN_EDGE__": scn_edge_markdown_text,
        "__TRUTH_AXIS__": source_info.get("truth_axis_validation_md", ""),
        "__THEORY_SAMPLES__": str(source_info["theory_samples"]),
        "__MAPPING_FIG__": figures["mapping"].name,
        "__GEOMETRY_FIG__": figures["go_geometry"].name,
        "__GUARD_FIG__": figures["guard_sweep"].name,
        "__GO_0_FIG__": figures["go_0deg_center"].name,
        "__GO_30_FIG__": figures["go_30deg_center"].name,
        "__GO_30_EDGE_FIG__": figures["go_30deg_edge"].name,
        "__GO_45_FIG__": figures["go_45deg_center"].name,
        "__GO_45_EDGE_FIG__": figures["go_45deg_edge"].name,
        "__GO_EDGE_FIG__": figures["go_0deg_edge"].name,
        "__GO_0_TABLE__": go_tables["0deg_center"],
        "__GO_30_TABLE__": go_tables["30deg_center"],
        "__GO_30_EDGE_TABLE__": go_tables["30deg_edge"],
        "__GO_45_TABLE__": go_tables["45deg_center"],
        "__GO_45_EDGE_TABLE__": go_tables["45deg_edge"],
        "__GO_EDGE_TABLE__": go_tables["0deg_edge"],
        "__WINDOW_FIG__": figures["window_distribution"].name,
        "__SHIFT_FIG__": figures["shifts"].name,
        "__ANGLE_IMAGES__": angle_images,
    }
    # The original draft used doubled braces for an f-string.  This report is
    # intentionally a normal string so LaTeX braces must be restored after
    # the static template is prepared.  Dynamic sections (for example the
    # S+C+N min-points addendum) already contain ordinary LaTeX braces; doing
    # this after insertion would collapse legitimate adjacent closing braces
    # in expressions such as ``\\frac{...}{...}``.
    text = text.replace("{{", "{").replace("}}", "}")
    for key, value in replacements.items():
        text = text.replace(key, value)
    # Inject the fixed-m=1 audit after the text has access to the computed table.
    path = out / "GMTI_输出SCNR_测角精度与检测概率_综合分析报告_20260829.md"
    path.write_text(text, encoding="utf-8")
    pdf.parent.mkdir(parents=True, exist_ok=True); html.parent.mkdir(parents=True, exist_ok=True)
    pandoc = shutil.which("pandoc"); xelatex = shutil.which("xelatex")
    if pandoc and xelatex:
        cmd = [pandoc, str(path), "--resource-path", str(out), "--pdf-engine", xelatex,
               "-V", "CJKmainfont=Noto Sans CJK SC", "-o", str(pdf)]
        result = subprocess.run(cmd, cwd=ROOT, check=False)
        if result.returncode:
            raise RuntimeError(f"Pandoc PDF 失败：{result.returncode}")
    if pandoc:
        result = subprocess.run([pandoc, str(path), "--standalone", "--resource-path", str(out),
                                 "--self-contained", "--mathml", "-o", str(html)], cwd=ROOT, check=False)
        if result.returncode:
            raise RuntimeError(f"Pandoc HTML 失败：{result.returncode}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/gmti_scnr_eval/comprehensive_report_20260829")
    parser.add_argument("--pdf", type=Path, default=ROOT / "docs/GMTI_输出SCNR_测角精度与检测概率_综合分析报告_20260829.pdf")
    parser.add_argument("--html", type=Path, default=ROOT / "docs/GMTI_输出SCNR_测角精度与检测概率_综合分析报告_20260829.html")
    parser.add_argument("--samples", type=int, default=60_000, help="每个 guard 的 IID A 样本数")
    parser.add_argument("--grid-min", type=float, default=0.0)
    parser.add_argument("--grid-max", type=float, default=35.0)
    parser.add_argument("--grid-step", type=float, default=.5)
    parser.add_argument("--seed", type=int, default=20260829)
    args = parser.parse_args()
    out = args.output_dir.resolve(); (out / "figures").mkdir(parents=True, exist_ok=True)
    corrected = ROOT / "outputs/gmti_scnr_eval/target_minpoints_corrected_20260828"
    roots = {2: corrected / "min2_formal", 3: corrected / "min3_formal", 6: corrected / "min6_formal"}
    leak_path = ROOT / "docs/GMTI_输出SCNR_三层GO模型验证报告_20260826/target_leakage_templates.csv"
    windows_path = ROOT / "docs/GMTI_输出SCNR_三层GO模型验证报告_20260826/empirical_go_windows.csv"
    templates = load_templates(leak_path); windows = load_windows(windows_path)
    global windows_for_plot
    windows_for_plot = windows
    mappings = {angle: mapping_from_root(roots[2], angle) for angle in ANGLES}
    grid = np.arange(args.grid_min, args.grid_max + .5 * args.grid_step, args.grid_step)
    theory_rows, alpha_map, alpha_rows, shifts, m_samples = build_theory(out, mappings, templates, windows, args.samples, args.seed, grid)
    metrics_by_min = {minimum: read_metrics(root, minimum) for minimum, root in roots.items()}
    if any(len(rows) != 30 for rows in metrics_by_min.values()):
        raise RuntimeError("三种 min_points 均应有 30 行（3角度×10档）")
    target_profiles = load_target_support_profiles(TARGET_PROFILE_PATH)
    target_pb_rows, target_pb_summary = build_target_pb_theory(
        out, target_profiles, mappings, m_samples[4], alpha_map[4], grid)
    # Generate the structural PB/production morphology audit from retained
    # compact CSVs.  It is intentionally a separate, reproducible script so
    # the report never needs raw maps and never fits theory to evaluation hits.
    morphology_script = SCRIPT_DIR / "35_audit_target_pb_morphology.py"
    morphology_csv = out / "target_pb_morphology_audit.csv"
    morphology_manifest_path = out / "target_pb_morphology_audit_manifest.json"
    morphology_cmd = [sys.executable, str(morphology_script), "--output-dir", str(out),
                      "--pb-path", str(out / "target_pd_poisson_binomial.csv")]
    morphology_result = subprocess.run(morphology_cmd, cwd=ROOT, check=False)
    if morphology_result.returncode:
        raise RuntimeError(f"目标PB形态审计失败：{morphology_result.returncode}")
    morphology_rows = read_csv(morphology_csv) if morphology_csv.is_file() else []
    morphology_figure = out / "figures" / "target_pb_morphology_audit.png"
    morphology_totals = {}
    if morphology_manifest_path.is_file():
        morphology_totals = json.loads(morphology_manifest_path.read_text(encoding="utf-8")).get("historical_totals", {})
    target_pb_figures = plot_target_pb(out, target_pb_rows, metrics_by_min)
    dependencies = []
    for minimum, root in roots.items():
        dependencies.extend(read_dependency(root, minimum))
        target_plot(out, minimum, metrics_by_min[minimum]); funnel_plot(out, minimum, metrics_by_min[minimum]); track_plot(out, minimum, metrics_by_min[minimum])
    angle_figures = angle_theory_curves(out, mappings, metrics_by_min)
    geometry = make_go_geometry(out)
    figures = plot_theory(out, theory_rows, metrics_by_min[2], mappings)
    figures["go_geometry"] = geometry
    figures["target_profile_shape"] = target_profile_shape_plot(out, target_profiles)
    module_files = copy_angle_modules(out)
    multiseed_rows = load_multiseed_summary(MULTISEED_SUMMARY_PATH)
    multiseed_figures = copy_multiseed_figures(out) if multiseed_rows else {}
    # Keep the older thermal-only regression section intact and append the
    # current compact-statistical S+C+N validation as a separate appendix.
    scn_multiseed_rows = load_scn_multiseed_summary(SCN_MULTISEED_SUMMARY_PATH)
    scn_multiseed_figures = copy_scn_multiseed_figures(out) if scn_multiseed_rows else {}
    scn_background_stats = load_scn_seed_background_stats(SCN_MULTISEED_MANIFEST_PATH) if scn_multiseed_rows else []
    scn_background_stats_path = out / "scn_seed_background_stats.csv"
    if scn_background_stats:
        write_csv(scn_background_stats_path, scn_background_stats)
    # Keep the independent +0.8° edge stress test separate from the centre
    # pooled curves.  It is a feasibility appendix, not a threshold fit.
    scn_edge_rows, scn_edge_audit = load_scn_edge_validation(SCN_EDGE_DIR)
    scn_edge_figures = copy_scn_edge_figures(out, SCN_EDGE_DIR) if scn_edge_rows else {}
    # Same-seed S+C+N min_points=2/3/6 comparison.  Keep it separate from the
    # pooled min6 section so the report never conflates different sample sizes
    # or background realizations.
    scn_minpoints_rows, scn_minpoints_audit = load_scn_minpoints_validation()
    scn_minpoints_figures = copy_scn_minpoints_figures(out) if scn_minpoints_rows else {}
    scn_minpoints_md = scn_minpoints_markdown(scn_minpoints_rows, scn_minpoints_figures,
                                              scn_minpoints_audit)
    # Append the targeted physical truth-CUT regression without replacing the
    # pooled three-angle/S+C+N sections.  This is the evidence for the
    # deterministic FFT-bin migration found during the unit-Pd audit.
    truth_axis_data = load_truth_axis_validation(TRUTH_AXIS_VALIDATION_DIR)
    truth_axis_figure = plot_truth_axis_validation(out, truth_axis_data)
    truth_axis_md = truth_axis_markdown(truth_axis_data, truth_axis_figure)
    if scn_edge_rows:
        write_csv(out / "scn_edge_validation_summary.csv", scn_edge_rows)
        write_csv(out / "scn_edge_support_background_summary.csv",
                  list(scn_edge_audit.get("support_background_stats", [])) +
                  list(scn_edge_audit.get("component_stats", [])))
        (out / "scn_edge_information_leakage_audit.json").write_text(
            json.dumps(scn_edge_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Fixed-m=1 / A / measured axis audit.
    alpha4 = alpha_map[4]; gamma90 = brentq(lambda g: float(ncx2.sf(2 * alpha4, 2, 2 * g)) - .9, 1e-9, 1e5)
    fixed_cut90 = 10 * math.log10(gamma90)
    pd90_rows = []
    for angle in ANGLES:
        mp = mappings[angle]
        rr = [r for r in theory_rows if r["angle_deg"] == angle and r["angle_group"] == f"{int(angle)}deg_center" and r["guard"] == 4 and r["model"] == "A_IID_no_leak"]
        aout = crossing([r["output_scnr_db"] for r in rr], [r["pd_unit"] for r in rr], .9)
        measured, typ = measured_crossing([r for r in metrics_by_min[2] if r["angle_deg"] == angle])
        pd90_rows.append({"angle_deg": angle, "textbook_fixed_m1_cut_pd90_db": fixed_cut90,
                          "textbook_fixed_m1_output_pd90_db": fixed_cut90, "A_IID_GO_output_pd90_db": aout,
                          "measured_exact_output_crossing_db": measured, "measured_crossing_type": typ,
                          "measured_max_grid_pd": max(r["unit_exact_pd"] for r in metrics_by_min[2] if r["angle_deg"] == angle)})
    write_csv(out / "pd90_axis_audit.csv", pd90_rows)
    pd90_table = [[f"{r['angle_deg']:g}°", fmt(r["textbook_fixed_m1_output_pd90_db"], 4), fmt(r["A_IID_GO_output_pd90_db"], 4), fmt(r["measured_exact_output_crossing_db"], 3) + ("（上界）" if r["measured_crossing_type"] == "upper_bound_first_grid" else "" if math.isfinite(r["measured_exact_output_crossing_db"]) else "（未达）"), r["measured_crossing_type"]] for r in pd90_rows]
    doppler_worked_path = write_doppler_max_worked_example(out)
    unit_summary_path, mapping_path, dependency_path = write_data_tables(
        out, metrics_by_min, dependencies, mappings, alpha_rows, theory_rows, shifts)
    # Keep machine metadata and hashes.  Hash only retained files and report
    # inputs; no raw BIN is copied or reopened.
    source_paths = [leak_path, windows_path, TARGET_PROFILE_PATH,
                    doppler_worked_path, unit_summary_path, mapping_path,
                    dependency_path, out / "theory_unit_go_curves.csv",
                    out / "go_alpha_guard.csv", out / "model_shift_summary.csv",
                    out / "angle_theory_curves.csv", out / "pd90_axis_audit.csv",
                    out / "target_pd_poisson_binomial.csv",
                    out / "target_pd_poisson_binomial_summary.csv",
                    Path(__file__).resolve(),
                    SCRIPT_DIR / "36_build_target_support_profiles_from_compact.py",
                    CORRECTED_TARGET_PROFILE_PATH.parent / "target_support_profile_manifest.json"]
    if CORRECTED_TARGET_PROFILE_PATH.is_file():
        source_paths += sorted(CORRECTED_TARGET_PROFILE_PATH.parent.glob("seed_*/run_manifest.json"))
    for root in roots.values():
        source_paths += [root / "run_manifest.json", root / "information_leakage_audit.json", curve_path(root, 0.0), curve_path(root, 30.0), curve_path(root, 45.0)]
    source_paths += [ROOT / "outputs/gmti_scnr_eval/angle_modules_20260827/angle_module_summary.csv"]
    if multiseed_rows:
        source_paths += [MULTISEED_SUMMARY_PATH, MULTISEED_MANIFEST_PATH]
    if scn_multiseed_rows:
        source_paths += [SCN_MULTISEED_SUMMARY_PATH, SCN_MULTISEED_MANIFEST_PATH,
                         SCN_MULTISEED_AUDIT_PATH, SCRIPT_DIR / "37_build_scn_multiseed_validation.py",
                         scn_background_stats_path]
        source_paths += [Path(str(item["source"])) for item in scn_background_stats
                         if item.get("source")]
    if scn_minpoints_rows:
        source_paths += [SCN_MINPOINTS_SUMMARY_PATH, SCN_MINPOINTS_MANIFEST_PATH,
                         SCN_MINPOINTS_AUDIT_PATH, SCN_MINPOINTS_MARKDOWN_PATH,
                         SCRIPT_DIR / "38_build_scn_minpoints_validation.py",
                         SCRIPT_DIR / "40_recompute_target_gate.py"]
        source_paths += sorted((SCN_MINPOINTS_DIR / "figures").glob("scn_minpoints_*.png"))
    if scn_edge_rows:
        source_paths += [SCN_EDGE_DIR / "run_manifest.json"]
        for angle in ANGLES:
            angle_dir = SCN_EDGE_DIR / token(angle)
            source_paths += [angle_dir / "manifest.json", angle_dir / "standard_mc_curve_summary.csv",
                             angle_dir / "standard_mc_screen_metrics.csv", angle_dir / "go_theory_vs_mc.csv",
                             angle_dir / "target_support_measurements.csv", angle_dir / "standard_mc_period_metrics.csv"]
        source_paths += sorted(SCN_EDGE_DIR.rglob("pipeline_manifest.json"))
    if truth_axis_data:
        source_paths += [TRUTH_AXIS_VALIDATION_DIR / name for name in (
            "manifest.json", "run_manifest.json", "standard_mc_curve_summary.csv",
            "standard_mc_period_metrics.csv", "go_theory_vs_mc.csv",
            "go_theory_periods.csv", "frozen_support.csv",
            "target_support_profiles.csv", "target_support_measurements.csv",
            "theory_recompute_manifest.json")]
        source_paths.append(TRUTH_AXIS_VALIDATION_DIR.parent / "run_manifest.json")
        source_paths += sorted(TRUTH_AXIS_VALIDATION_DIR.rglob("pipeline_manifest.json"))
        if truth_axis_figure is not None:
            source_paths.append(truth_axis_figure)
    source_paths += [morphology_script, morphology_csv, out / "current_formal_morphology_summary.csv",
                     morphology_manifest_path, HISTORICAL_MORPHOLOGY_PATH]
    # Keep the user-authorized intermediate cleanup records in the same
    # manifest as the numerical evidence.  They document exactly which
    # audited scenario trees were removed and which compact summaries remain.
    source_paths += [
        ROOT / "outputs/gmti_scnr_eval/scn_validation_20260830/cleanup_manifest_20260830.json",
        ROOT / "outputs/gmti_scnr_eval/target_support_calibration_corrected_20260830/cleanup_manifest_20260830.json",
        ROOT / "outputs/gmti_scnr_eval/scn_edge_validation_20260830/cleanup_manifest_20260830.json",
        ROOT / "outputs/gmti_scnr_eval/target_minpoints_corrected_20260828/cleanup_manifest.json",
        ROOT / "outputs/gmti_scnr_eval/target_minpoints_strict_20260828/cleanup_manifest_20260830.json",
    ]
    manifest = {"report_date": "2026-08-30", "script": str(Path(__file__).resolve()), "output_dir": str(out),
                "sources": {str(p.resolve()): sha256(p) for p in source_paths if p.is_file()},
                "settings": {"pfa": 1e-6, "go_type": "GO", "guards": [3,4,5], "background": 16,
                             "iid_samples_per_guard": args.samples, "grid_db": [args.grid_min, args.grid_max, args.grid_step],
                             "min_points": [2,3,6], "strict_online_no_prior": True,
                             "axis": "CUT-equivalent output SCNR (production physical CUT); fixed 3x3 support SCNR retained as diagnostic"},
                "pd90": {"gamma_cut_db": fixed_cut90, "gamma_cut_linear": gamma90},
                "leakage_scope": {g: {"qualified_count": templates.get(g, {}).get("qualified_count", 0),
                                       "guard4_measured": True, "guard3_5_counterfactual_projection": True} for g in GROUPS},
                "target_level_model": {"model": "Poisson-binomial calibration support baseline",
                                        "support_profile_source": str(TARGET_PROFILE_PATH.resolve()),
                                        "support_profile_manifest": str((CORRECTED_TARGET_PROFILE_PATH.parent / "target_support_profile_manifest.json").resolve()) if CORRECTED_TARGET_PROFILE_PATH.is_file() else "legacy_profile",
                                        "support_profile_builder": str((SCRIPT_DIR / "36_build_target_support_profiles_from_compact.py").resolve()),
                                        "support_cells": 15, "connectivity_factor": 1.0,
                                        "evaluation_hit_rates_used_for_fit": False,
                                        "morphology_audit": str(morphology_csv.resolve()),
                                        "min6_multiseed_validation": str(MULTISEED_SUMMARY_PATH.resolve()) if multiseed_rows else "not_available",
                                        "scn_multiseed_validation": str(SCN_MULTISEED_SUMMARY_PATH.resolve()) if scn_multiseed_rows else "not_available",
                                        "scn_background_heterogeneity_audit": str(scn_background_stats_path.resolve()) if scn_background_stats else "not_available",
                                        "scn_minpoints_validation": str(SCN_MINPOINTS_SUMMARY_PATH.resolve()) if scn_minpoints_rows else "not_available",
                                        "scn_edge_validation": str((out / "scn_edge_validation_summary.csv").resolve()) if scn_edge_rows else "not_available",
                                        "truth_axis_validation": str(TRUTH_AXIS_VALIDATION_DIR.resolve()) if truth_axis_data else "not_available",
                                        "truth_axis_theory_recompute_manifest": str((TRUTH_AXIS_VALIDATION_DIR / "theory_recompute_manifest.json").resolve()) if (TRUTH_AXIS_VALIDATION_DIR / "theory_recompute_manifest.json").is_file() else "not_available"},
                "raw_bin_policy": "not copied; corrected formal roots audited and cleaned per user authorization"}
    (out / "report_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pb_example_row = next((r for r in target_pb_rows
                           if r["angle_group"] == "0deg_center"
                           and int(r["min_points"]) == 6
                           and abs(float(r["output_scnr_db"]) - 10.0) < 1e-9), None)
    pb_offset = fnum(pb_example_row.get("output_to_profile_support_offset_db")) if pb_example_row else float("nan")
    if pb_example_row:
        pb_value = fnum(pb_example_row["pd_cluster_pb_profile"])
        pb_example = f"{pb_value:.3e}" if math.isfinite(pb_value) and abs(pb_value) < 1e-3 else f"{pb_value:.4f}"
    else:
        pb_example = "—"
    source_info = {"theory_samples": args.samples, "pd90_table": pd90_table,
                   "target_profile_table": target_profile_table(target_profiles),
                   "target_pb_example": pb_example,
                   "target_pb_offset": fmt(pb_offset, 4),
                   "target_pb_ncal": (str(pb_example_row.get("support_sample_count"))
                                      if pb_example_row else "—"),
                   "morphology_pb_highest": fmt(max(
                       (fnum(row.get("pb_min6_profile")) for row in morphology_rows
                        if math.isfinite(fnum(row.get("pb_min6_profile")))),
                       default=float("nan")), 3),
                   "morphology_totals_text": (
                       f"历史形态审计的 150 条记录中，生产 cluster 命中 {int(morphology_totals.get('production_events', 0))} 条；"
                       f"其中 {int(morphology_totals.get('production_small_branch_events', 0))} 条走 3–5 点 +20 dB 小簇分支，"
                       f"只有 {int(morphology_totals.get('production_full_ge6_events', 0))} 条的全图 component 达到 6 点，"
                       f"生产命中中有 {int(morphology_totals.get('production_local_lt6_events', 0))} 条的局部 3×5 命中数仍小于 6。"
                   ),
                   "scn_validation_md": scn_multiseed_markdown(
                       scn_multiseed_rows, scn_multiseed_figures,
                       SCN_MULTISEED_AUDIT_PATH, SCN_MULTISEED_MANIFEST_PATH,
                       scn_background_stats),
                   "truth_axis_validation_md": truth_axis_md,
                   }
    figures["pd90"] = out / "figures" / "pd90_axis_audit.png"
    # Pd90 audit plot (after table data are known).
    fig, ax = plt.subplots(figsize=(8.0, 4.7)); idx = np.arange(3); width=.22
    fixed = [r["textbook_fixed_m1_output_pd90_db"] for r in pd90_rows]; a = [r["A_IID_GO_output_pd90_db"] for r in pd90_rows]
    measured_vals = [r["measured_exact_output_crossing_db"] if math.isfinite(r["measured_exact_output_crossing_db"]) else np.nan for r in pd90_rows]
    ax.bar(idx - width, fixed, width, label="教材 fixed m=1", color="#777777"); ax.bar(idx, a, width, label="A IID GO", color="#111111")
    ax.bar(idx + width, np.nan_to_num(measured_vals, nan=0), width, label="实测 exact（0=未达）", color="#1f77b4")
    for i, r in enumerate(pd90_rows):
        if not math.isfinite(r["measured_exact_output_crossing_db"]): ax.text(i + width, .3, "未达", ha="center", rotation=90, color="#1f77b4")
    ax.set_xticks(idx); ax.set_xticklabels([f"{a:g}°" for a in ANGLES]); ax.set_ylabel("output SCNR at Pd=0.9 (dB)"); ax.set_title("Pd90 坐标审计：实测不应低于教材理想轴"); ax.legend(fontsize=8); ax.grid(axis="y", alpha=.25); fig.tight_layout(); fig.savefig(figures["pd90"]); fig.savefig(figures["pd90"].with_suffix(".pdf")); plt.close(fig)
    report_path = write_report(out, args.pdf.resolve(), args.html.resolve(), mappings, alpha_rows, templates, windows, theory_rows, shifts, metrics_by_min, dependencies, figures, angle_figures, module_files, target_pb_summary, target_pb_figures, source_info, multiseed_rows, multiseed_figures, morphology_rows, morphology_figure,
                               scn_minpoints_md, scn_edge_markdown(scn_edge_rows, scn_edge_figures, scn_edge_audit))
    print(json.dumps({"status": "pass", "output_dir": str(out), "markdown": str(report_path), "pdf": str(args.pdf.resolve()), "html": str(args.html.resolve()), "alpha": alpha_map, "window_count": sum(x["count"] for x in windows.values()), "source_count": len(manifest["sources"]), "pd90": pd90_rows}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
