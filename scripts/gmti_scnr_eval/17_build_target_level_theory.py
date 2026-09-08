#!/usr/bin/env python3
"""Compute the section-7 target-level GO-CFAR theory curve.

The production report defines the target event as a probability chain after a
set of spatially supported unit hits.  This script implements the part that is
identifiable without refitting evaluation data:

* measure a 3 x 5 production connectivity footprint from retained S-only and
  C+N-only power maps;
* map a common output SCNR to 15 per-cell SCNRs using measured kappa_i/beta_i;
* evaluate the Poisson-binomial tail for min_points=3,4,5,6;
* overlay, without fitting, the retained three-seed production cluster and
  target events.

The Poisson-binomial curves deliberately set the connectivity factor to one.
They are the section-7 analytic baseline, not a claim that the production
cluster is independent.  The empirical overlay shows the loss caused by the
real connected-component, target-select, relocation and truth-match stages.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.machinery
import json
import math
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti_target_level_mplconfig")
import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager
import matplotlib.pyplot as plt
import numpy as np
_CJK_FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if _CJK_FONT.is_file():
    font_manager.fontManager.addfont(str(_CJK_FONT))
    plt.rcParams["font.family"] = "Noto Sans CJK JP"

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[2]
MODEL14 = importlib.machinery.SourceFileLoader(
    "go_three_layer_model", str(SCRIPT_DIR / "14_build_go_three_layer_model.py")
).load_module()
from scnr_eval_lib import go_conditional_pd, go_training_noise_samples  # noqa: E402


def fnum(value: object, default: float = float("nan")) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def inum(value: object, default: int = -1) -> int:
    value = fnum(value)
    return int(round(value)) if math.isfinite(value) else default


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: Iterable[dict[str, object]]) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_scene_group(angle_dir: str) -> tuple[float, str, str]:
    return MODEL14.parse_scene_label(angle_dir)


def support_positions(row: int, col: int, rows: int, cols: int) -> list[tuple[int, int]]:
    """The production 3-row x 5-range candidate footprint (15 cells)."""
    return [((row + dr) % rows, col + dc)
            for dr in (-1, 0, 1) for dc in (-2, -1, 0, 1, 2)
            if 0 <= col + dc < cols]


def local_s_floor(power: np.ndarray, row: int, col: int,
                  geometry: object) -> float:
    h = int(geometry.guard_half)
    o = int(geometry.outer_half)
    values = [float(power[(row + dr) % power.shape[0], col + dc])
              for dr in range(-o, o + 1) for dc in range(-o, o + 1)
              if not (-h <= dr <= h and -h <= dc <= h)
              and 0 <= col + dc < power.shape[1]]
    values = [value for value in values if math.isfinite(value) and value >= 0.0]
    return float(np.median(values)) if values else 0.0


def read_truth_row(truth_path: Path, algorithm: Path, rows: int,
                   prf: float) -> tuple[int, int]:
    truth_rows = read_csv(truth_path)
    truth = next((item for item in truth_rows if str(item.get("beam_id", "1")) == "1"), None)
    if truth is None:
        raise ValueError(f"truth beam 1 missing: {truth_path}")
    meta = MODEL14.read_kv(algorithm / "debug/cfar_GMTI01_beam001_meta.txt")
    extractor = MODEL14.extractor_module()
    row, source = extractor.truth_row_on_cfar_axis(truth, meta, rows, prf)
    col = inum(truth.get("range_bin"))
    return int(row), int(col)


def collect_profiles(calibration_root: Path, out: Path,
                     min_reference_scnr: float) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    """Measure kappa/beta from production maps, never from a hand-entered shape."""
    samples: list[dict[str, object]] = []
    paths = sorted(calibration_root.glob(
        "angle_*/seed_*/paired/variants/targets/*/s_only/stage2/truth/truth_targets_by_beam.csv"))
    for truth_path in paths:
        angle_dir = next((part for part in truth_path.parts if part.startswith("angle_")), "")
        if angle_dir == "angle_00deg_edge08":
            # This old directory predates the fixed truth Doppler axis mapping.
            continue
        try:
            angle, position, group = parse_scene_group(angle_dir)
        except ValueError:
            continue
        stage2 = truth_path.parents[1]
        algorithm = stage2 / "algorithm_result/period_0000"
        s_path = algorithm / "debug/cfar_GMTI01_beam001_power.f32"
        variants = truth_path.parents[3].parent.parent
        cn_path = variants / "c_plus_n/stage2/algorithm_result/period_0000/debug/cfar_GMTI01_beam001_power.f32"
        cn_algorithm = cn_path.parent.parent
        meta_path = algorithm / "debug/cfar_GMTI01_beam001_meta.txt"
        cfg_path = stage2 / "config/temp_config_stage2_newsystem.xml"
        if not (s_path.is_file() and cn_path.is_file() and meta_path.is_file() and cfg_path.is_file()):
            continue
        meta = MODEL14.read_kv(meta_path)
        rows = inum(meta.get("rows"), 128)
        cols = inum(meta.get("cols"), 4096)
        if rows <= 0 or cols <= 0:
            continue
        try:
            cfg = MODEL14.read_xml_values(cfg_path)
            guard = int(float(cfg.get("cfar_guard_cells", 4)))
            background = int(float(cfg.get("cfar_background_cells", 16)))
            prf = float(cfg.get("PRF", 1300.0))
            geometry = MODEL14.Geometry(guard, background)
            geometry.validate()
            row, col = read_truth_row(truth_path, algorithm, rows, prf)
        except (ValueError, RuntimeError, KeyError, ET.ParseError):
            continue
        if guard != 4 or col < geometry.outer_half or col + geometry.outer_half >= cols:
            continue
        s_map = np.fromfile(s_path, dtype=np.float32)
        cn_map = np.fromfile(cn_path, dtype=np.float32)
        if s_map.size != rows * cols or cn_map.size != rows * cols:
            continue
        s_map = s_map.reshape(rows, cols)
        cn_map = cn_map.reshape(rows, cols)
        floor = local_s_floor(s_map, row, col, geometry)
        positions = support_positions(row, col, rows, cols)
        if len(positions) != 15:
            continue
        signal = np.asarray([max(float(s_map[r, c]) - floor, 0.0) for r, c in positions], dtype=float)
        p0 = np.asarray([
            max(float(x) for x in MODEL14.directional_means(cn_map, r, c, geometry))
            for r, c in positions
        ], dtype=float)
        if not np.all(np.isfinite(signal)) or not np.all(np.isfinite(p0)):
            continue
        p0_ref = float(np.median(p0[p0 > 0.0])) if np.any(p0 > 0.0) else float("nan")
        signal_sum = float(np.sum(signal))
        gamma_ref = signal_sum / p0_ref if math.isfinite(p0_ref) and p0_ref > 0 else float("nan")
        if not math.isfinite(gamma_ref) or gamma_ref < min_reference_scnr:
            continue
        kappa = signal / max(signal_sum, 1e-30)
        beta = p0 / max(p0_ref, 1e-30)
        samples.append({
            "angle_group": group, "angle_deg": angle, "position": position,
            "seed": next((part for part in truth_path.parts if part.startswith("seed_")), ""),
            "source_s_only_map": str(s_path), "source_cn_map": str(cn_path),
            "truth_row": row, "truth_col": col, "guard": guard, "background": background,
            "s_only_floor": floor, "support_signal_sum": signal_sum,
            "support_background_ref": p0_ref, "support_scnr_linear": gamma_ref,
            "support_scnr_db": 10.0 * math.log10(max(gamma_ref, 1e-30)),
            **{f"kappa_{i:02d}": float(kappa[i]) for i in range(15)},
            **{f"beta_{i:02d}": float(beta[i]) for i in range(15)},
            **{f"support_row_{i:02d}": int(positions[i][0]) for i in range(15)},
            **{f"support_col_{i:02d}": int(positions[i][1]) for i in range(15)},
        })
    write_csv(out / "target_support_measurements.csv", samples)
    profiles: dict[str, dict[str, object]] = {}
    for group in sorted({str(item["angle_group"]) for item in samples}):
        rows = [item for item in samples if item["angle_group"] == group]
        kappa = np.median(np.asarray([[fnum(item[f"kappa_{i:02d}"]) for i in range(15)] for item in rows]), axis=0)
        kappa = np.maximum(kappa, 0.0); kappa /= max(float(kappa.sum()), 1e-30)
        beta = np.median(np.asarray([[fnum(item[f"beta_{i:02d}"]) for i in range(15)] for item in rows]), axis=0)
        beta = np.maximum(beta, 1e-6); beta /= max(float(np.median(beta)), 1e-30)
        profiles[group] = {
            "angle_deg": fnum(rows[0]["angle_deg"]), "position": str(rows[0]["position"]),
            "sample_count": len(rows), "qualified_scnr_db_median": float(np.median([fnum(x["support_scnr_db"]) for x in rows])),
            "kappa": kappa, "beta": beta,
            "source": "median of qualified production S-only/C+N-only 3x5 support measurements",
        }
    profile_rows: list[dict[str, object]] = []
    for group, profile in sorted(profiles.items()):
        profile_rows.append({
            "angle_group": group, "angle_deg": profile["angle_deg"], "position": profile["position"],
            "sample_count": profile["sample_count"], "qualified_scnr_db_median": profile["qualified_scnr_db_median"],
            **{f"kappa_{i:02d}": float(profile["kappa"][i]) for i in range(15)},
            **{f"beta_{i:02d}": float(profile["beta"][i]) for i in range(15)},
        })
    write_csv(out / "target_support_profiles.csv", profile_rows)
    return profiles, samples


def poisson_binomial_tail(probabilities: np.ndarray, minimum: int) -> np.ndarray:
    """Tail probability for one row per SCNR point and one column per cell."""
    probabilities = np.asarray(probabilities, dtype=float)
    if probabilities.ndim != 2:
        raise ValueError("probabilities must be [grid, support_cells]")
    grid, count = probabilities.shape
    dp = np.zeros((grid, count + 1), dtype=float)
    dp[:, 0] = 1.0
    for index in range(count):
        p = np.clip(probabilities[:, index], 0.0, 1.0)
        old = dp.copy()
        dp[:, 0] = old[:, 0] * (1.0 - p)
        dp[:, 1:index + 2] = (old[:, 1:index + 2] * (1.0 - p[:, None]) +
                               old[:, :index + 1] * p[:, None])
    minimum = max(0, min(int(minimum), count + 1))
    return np.ones(grid, dtype=float) if minimum == 0 else np.zeros(grid, dtype=float) if minimum > count else np.sum(dp[:, minimum:], axis=1)


def crossing(x: np.ndarray, y: np.ndarray, target: float) -> float:
    for i, value in enumerate(y):
        if value < target:
            continue
        if i == 0:
            return float(x[0])
        x0, x1 = float(x[i - 1]), float(x[i]); y0, y1 = float(y[i - 1]), float(y[i])
        if y1 == y0:
            return x1
        return x0 + (target - y0) * (x1 - x0) / (y1 - y0)
    return float("nan")


def build_theory(profiles: dict[str, dict[str, object]], out: Path,
                 alpha: float, grid_db: np.ndarray, samples: int, seed: int) -> list[dict[str, object]]:
    # scnr_eval_lib and model14 use the same eight-block statistic.  Import the
    # library geometry explicitly to avoid accidentally using the CA alpha.
    from scnr_eval_lib import GoCfarGeometry
    noise = go_training_noise_samples(GoCfarGeometry(4, 16), samples, seed)
    gamma = 10.0 ** (grid_db / 10.0)
    result: list[dict[str, object]] = []
    for group, profile in sorted(profiles.items()):
        kappa = np.asarray(profile["kappa"], dtype=float)
        beta = np.asarray(profile["beta"], dtype=float)
        scale = kappa / np.maximum(beta, 1e-12)
        pi = np.column_stack([go_conditional_pd(gamma * value, alpha, noise) for value in scale])
        p_equal = go_conditional_pd(gamma, alpha, noise)
        for minimum in (2, 3, 4, 5, 6):
            tail = poisson_binomial_tail(pi, minimum)
            equal = poisson_binomial_tail(np.repeat(p_equal[:, None], 15, axis=1), minimum)
            for index, scnr_db in enumerate(grid_db):
                result.append({
                    "angle_group": group, "angle_deg": profile["angle_deg"], "position": profile["position"],
                    "output_scnr_db": float(scnr_db), "min_points": minimum,
                    "pd_target_poisson_binomial_profile": float(tail[index]),
                    "pd_target_poisson_binomial_equal_cell": float(equal[index]),
                    "pd_unit_reference_equal_cell": float(p_equal[index]),
                    "support_cells": 15, "support_sample_count": profile["sample_count"],
                    "alpha": alpha, "pfa": 1e-6, "connectivity_factor": 1.0,
                    "model": "section7_PB_conditional_independence_eta1",
                })
    write_csv(out / "target_pd_minpoints_theory.csv", result)
    return result


def build_empirical(metrics_path: Path, out: Path) -> list[dict[str, object]]:
    rows = read_csv(metrics_path)
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in rows:
        scnr = fnum(row.get("scnr_out_db"))
        if not math.isfinite(scnr):
            continue
        case = str(row.get("case", row.get("truth_angle_case_deg", "")))
        label = str(row.get("label", "center"))
        angle = str(row.get("truth_angle_case_deg", case))
        grouped.setdefault((case, label, angle), []).append(row)
    result: list[dict[str, object]] = []
    for (case, label, angle), group in sorted(grouped.items()):
        by_scnr: dict[float, list[dict[str, str]]] = {}
        for row in group:
            by_scnr.setdefault(round(fnum(row.get("scnr_out_db")), 6), []).append(row)
        for scnr, items in sorted(by_scnr.items()):
            unit = np.asarray([inum(row.get("unit_go_cfar_hit"), 0) for row in items], dtype=float)
            sizes = np.asarray([fnum(row.get("unit_go_hit_component_size"), 0.0) for row in items], dtype=float)
            target = np.asarray([inum(row.get("final_output"), 0) for row in items], dtype=float)
            item: dict[str, object] = {"case": case, "label": label, "angle_deg": angle,
                                       "output_scnr_db": scnr, "n": len(items),
                                       "p_unit": float(np.mean(unit)), "p_target": float(np.mean(target)),
                                       "source": str(metrics_path)}
            for minimum in (2, 3, 4, 5, 6):
                cluster = (sizes >= minimum).astype(float)
                item[f"p_cluster_min{minimum}"] = float(np.mean(cluster))
                item[f"n_cluster_min{minimum}"] = int(np.sum(cluster))
                after = target * cluster
                item[f"p_target_after_cluster_min{minimum}"] = float(np.mean(after))
                item[f"p_post_given_cluster_min{minimum}"] = float(np.sum(after) / np.sum(cluster)) if np.sum(cluster) else float("nan")
            result.append(item)
    write_csv(out / "target_pd_empirical_overlay.csv", result)
    return result


def plot_outputs(out: Path, theory: list[dict[str, object]], empirical: list[dict[str, object]]) -> dict[str, str]:
    figures = out / "figures"; figures.mkdir(parents=True, exist_ok=True)
    groups = sorted({str(row["angle_group"]) for row in theory})
    colors = {2: "#1f77b4", 3: "#1b9e77", 4: "#d95f02", 5: "#7570b3", 6: "#d62728"}
    fig, axes = plt.subplots(2, 3, figsize=(16, 8), sharex=True, sharey=True)
    axes = np.asarray(axes).reshape(-1)
    for ax, group in zip(axes, groups):
        for minimum in (2, 3, 4, 5, 6):
            rows = [r for r in theory if r["angle_group"] == group and int(r["min_points"]) == minimum]
            ax.plot([fnum(r["output_scnr_db"]) for r in rows], [fnum(r["pd_target_poisson_binomial_profile"]) for r in rows], label=f"min_points={minimum}", color=colors[minimum], lw=2)
        profile_position = str(next(x["position"] for x in theory if x["angle_group"] == group))
        empirical_position = "right_edge" if profile_position == "edge" else "center"
        observed = [r for r in empirical if str(r["case"]) == str(round(fnum(next(x["angle_deg"] for x in theory if x["angle_group"] == group)), 8)) and str(r["label"]) == empirical_position]
        if observed:
            ax.scatter([fnum(r["output_scnr_db"]) for r in observed], [fnum(r["p_target"]) for r in observed], marker="x", c="black", label="实测 target Pd", zorder=5)
        ax.set_title(group); ax.grid(alpha=0.25); ax.set_ylim(-0.02, 1.02)
    for ax in axes[-3:]: ax.set_xlabel("output SCNR (dB)")
    for ax in axes[::3]: ax.set_ylabel("目标级 Pd")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=5, bbox_to_anchor=(0.5, 1.02))
    fig.suptitle("第 7 节 Poisson-binomial 目标级理论：min_points 阈值比较", y=1.06, fontsize=14)
    fig.tight_layout(); fig.savefig(figures / "target_pd_minpoints_profiles.png", dpi=180, bbox_inches="tight"); plt.close(fig)

    # Overlay all angle groups; this makes the threshold penalty easy to read.
    fig, ax = plt.subplots(figsize=(10, 6))
    for group in groups:
        rows = [r for r in theory if r["angle_group"] == group and int(r["min_points"]) == 6]
        ax.plot([fnum(r["output_scnr_db"]) for r in rows], [fnum(r["pd_target_poisson_binomial_profile"]) for r in rows], lw=2, label=f"{group}, min=6")
    ax.set_xlabel("output SCNR (dB)"); ax.set_ylabel("目标级 Pd"); ax.set_ylim(-0.02, 1.02); ax.grid(alpha=0.25); ax.legend(ncol=2)
    fig.tight_layout(); fig.savefig(figures / "target_pd_minpoints6_by_angle.png", dpi=180); plt.close(fig)

    # Heatmap of measured support shape for auditability.
    profile_path = out / "target_support_profiles.csv"
    profiles = read_csv(profile_path)
    fig, axes = plt.subplots(2, max(1, len(profiles)), figsize=(4 * max(1, len(profiles)), 6), squeeze=False)
    if profiles:
        for j, profile in enumerate(profiles):
            # The support list is row-major in (Doppler offset, range offset).
            kappa = np.asarray([[fnum(profile[f"kappa_{5 * r + c:02d}"]) for c in range(5)] for r in range(3)])
            beta = np.asarray([[fnum(profile[f"beta_{5 * r + c:02d}"]) for c in range(5)] for r in range(3)])
            for ax, data, title in ((axes[0, j], kappa, "kappa"), (axes[1, j], beta, "beta")):
                image = ax.imshow(data, aspect="auto", cmap="viridis")
                ax.set_title(f"{profile['angle_group']} {title}"); ax.set_xticks(range(5)); ax.set_yticks(range(3)); fig.colorbar(image, ax=ax, fraction=0.046)
    fig.tight_layout(); fig.savefig(figures / "target_support_profile_heatmaps.png", dpi=180); plt.close(fig)
    return {"profiles": str(figures / "target_pd_minpoints_profiles.png"), "min6": str(figures / "target_pd_minpoints6_by_angle.png"), "support": str(figures / "target_support_profile_heatmaps.png")}


def build_summary(theory: list[dict[str, object]], out: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for group in sorted({str(row["angle_group"]) for row in theory}):
        for minimum in (2, 3, 4, 5, 6):
            rows = [row for row in theory if row["angle_group"] == group and int(row["min_points"]) == minimum]
            x = np.asarray([fnum(row["output_scnr_db"]) for row in rows]); y = np.asarray([fnum(row["pd_target_poisson_binomial_profile"]) for row in rows])
            row = next(row for row in rows)
            result.append({"angle_group": group, "angle_deg": row["angle_deg"], "position": row["position"], "min_points": minimum,
                           "pd_at_0db": float(np.interp(0.0, x, y)), "pd_at_10db": float(np.interp(10.0, x, y)), "pd_at_15db": float(np.interp(15.0, x, y)), "pd_at_20db": float(np.interp(20.0, x, y)),
                           "scnr_at_pd_0p5_db": crossing(x, y, 0.5), "scnr_at_pd_0p9_db": crossing(x, y, 0.9),
                           "support_sample_count": row["support_sample_count"], "model": row["model"]})
    by_group = {group: [r for r in result if r["angle_group"] == group] for group in sorted({r["angle_group"] for r in result})}
    for group, rows in by_group.items():
        base = next((r for r in rows if int(r["min_points"]) == 3), None)
        if base is None: continue
        for row in rows:
            row["right_shift_vs_min3_at_pd_0p5_db"] = fnum(row["scnr_at_pd_0p5_db"]) - fnum(base["scnr_at_pd_0p5_db"])
            row["right_shift_vs_min3_at_pd_0p9_db"] = fnum(row["scnr_at_pd_0p9_db"]) - fnum(base["scnr_at_pd_0p9_db"])
    write_csv(out / "target_pd_minpoints_summary.csv", result)
    return result


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    def cell(value: object) -> str:
        value = "—" if value is None or (isinstance(value, float) and not math.isfinite(value)) else value
        return str(value).replace("|", "\\|")
    text = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    text.extend("| " + " | ".join(cell(value) for value in row) + " |" for row in rows)
    return "\n".join(text)


def write_report(out: Path, args: argparse.Namespace, profiles: dict[str, dict[str, object]],
                 samples: list[dict[str, object]], theory: list[dict[str, object]],
                 empirical: list[dict[str, object]], summary: list[dict[str, object]], figures: dict[str, str]) -> Path:
    table_rows = []
    for row in summary:
        if int(row["min_points"]) == 6:
            table_rows.append([row["angle_group"], row["support_sample_count"], f"{fnum(row['pd_at_10db']):.3f}", f"{fnum(row['pd_at_15db']):.3f}", f"{fnum(row['pd_at_20db']):.3f}", f"{fnum(row['scnr_at_pd_0p5_db']):.2f}", f"{fnum(row['scnr_at_pd_0p9_db']):.2f}"])
    shift_rows = [[row["angle_group"], row["min_points"], f"{fnum(row['pd_at_10db']):.3f}", f"{fnum(row['pd_at_15db']):.3f}", f"{fnum(row['scnr_at_pd_0p5_db']):.2f}", f"{fnum(row.get('right_shift_vs_min3_at_pd_0p5_db')):.2f}"] for row in summary]
    empirical_count = len(empirical)
    report = fr'''---
title: "GMTI 第 7 节目标级检测概率：Poisson-binomial 与 min_points 阈值"
subtitle: "从实测 3×5 目标支撑到目标级 Pd 曲线"
author: "GMTI 算法验证"
date: "2026-08-26"
geometry: margin=1.55cm
mainfont: Noto Sans CJK SC
CJKmainfont: Noto Sans CJK SC
fontsize: 10pt
---

# 1. 结论先行

本报告把原理论报告第 7 节真正数值化，横轴统一为 **output SCNR**。对保留的生产 S-only/C+N-only 功率图，直接测得目标在生产聚类 footprint 中的 15 个支撑单元（3 行 Doppler × 5 个 range 位置）的信号比例 $\kappa_i$ 和局部背景比例 $\beta_i$，再用生产 GO 单元级 $P_D$ 代入 Poisson-binomial。

* `min_points=6` 的曲线不是“单元 Pd 曲线”，而是 15 个相关空间候选在条件独立近似下的 $P(K\ge6)$；它会明显向右移动，尤其在 0.5 或 0.9 目标级 Pd 附近。
* 该曲线是第 7.2 节解析 baseline，连通性因子暂取 $\eta=1$，所以它只表示“点数通过”这一层；真实生产 cluster、target-select、relocation、truth match 用三 seed 保留事件叠加，不重新拟合理论。
* 是否“太苛刻”必须看目标允许的 Pd 与 output SCNR：报告给出 min=3/4/5/6 的右移量，不能只凭 `6` 这个数字判断。若工作点落在曲线陡峭段，min=6 相比 min=3 的额外 dB 代价就是直接证据。

![各角度与波位的目标级曲线](figures/{Path(figures['profiles']).name})

# 2. 第 7 节公式如何落地

设固定输出参考为 $\gamma_{{out}}$。对第 $i$ 个支撑单元，生产图测得：

$$\kappa_i = P_{{S,i}}/\sum_jP_{{S,j}},\qquad \beta_i=P_{{0,i}}/\operatorname{{median}}_j P_{{0,j}}.$$ 

所以：

$$\gamma_i(\gamma_{{out}})=\gamma_{{out}}\frac{{\kappa_i}}{{\beta_i}},\qquad p_i(\gamma_{{out}})=P_{{D,GO}}(\gamma_i).$$

这里 $P_{{S,i}}$ 来自 S-only 图减去同一 GO 窗外的局部中位 floor，$P_{{0,i}}$ 来自配对 C+N-only 图在该 cell 的四方向 GO 最大均值；没有手工指定主瓣形状或背景常数。共保留 {len(samples)} 个合格支撑样本，按角度/波位取中位数模板，见 `target_support_measurements.csv` 和 `target_support_profiles.csv`。

令 $H_i$ 表示第 $i$ 个单元过 GO 门限，$K=\sum_iH_i$。条件独立时：

$$G(z)=\prod_{{i=1}}^{{15}}[(1-p_i)+p_i z],\qquad P_{{PB,m}}(\gamma_{{out}})=P(K\ge m).$$

本报告计算 $m=2,3,4,5,6$。`min_points=6` 对应原生产配置；2–5 点小簇 +20 dB 规则没有被强行并入 PB 独立假设，而是在实测 overlay 中按真实组件/目标事件观察。

# 3. 生产参数与支撑形状

| 项目 | 实际取值 |
|---|---|
| GO | guard=4，background=16，生产 $\alpha={args.alpha:.10f}$ |
| PFA | $10^{{-6}}$ |
| 支撑 footprint | 3 行 Doppler × 5 个 range 位置 = 15 个候选单元 |
| 支撑来源 | S-only / 配对 C+N-only production power map |
| 合格条件 | 支撑总信号功率 / 局部背景中位数 $\ge$ {args.min_reference_scnr:g} |
| 评价叠加 | {empirical_count} 个三 seed evaluation 分箱，未用于拟合曲线 |

生产 guard=4 的 GO 训练块仍是四个 $16\times16$ 角块和四个 $16\times9/9\times16$ 中间块；四方向共享角块的相关性保留在单元级 GO $P_D$ 的 Monte-Carlo 积分中。目标级 PB 只额外近似不同候选 cell 的 hit 独立性，因此应把它称为半解析 baseline。

![实测支撑模板的 $\kappa$ 与 $\beta$](figures/{Path(figures['support']).name})

# 4. 理论曲线和阈值比较

`target_pd_minpoints_theory.csv` 是完整机器表；下表先列生产 min=6 的几个工作点：

{markdown_table(["角度/波位", "样本数", "Pd@10 dB", "Pd@15 dB", "Pd@20 dB", "0.5 Pd交点(dB)", "0.9 Pd交点(dB)"], table_rows)}

所有阈值的对比（最后一列是相对 min=3 的 0.5-Pd 右移）：

{markdown_table(["角度/波位", "min_points", "Pd@10", "Pd@15", "0.5交点", "相对min3右移"], shift_rows)}

`right_shift_vs_min3_at_pd_0p5_db` 是最直接的“min_points=6 是否苛刻”指标：如果为正，表示为了同一个目标级 Pd，min=6 需要更高的 output SCNR；数值由实测支撑形状和 GO 单元理论共同决定，而不是调参得到。

# 5. 与真实生产目标事件的关系

实测 overlay 文件按 output SCNR 分箱保存：`p_unit`、`p_cluster_min3/4/5/6`、`p_target` 以及各阈值下的 cluster 后目标保留率。这里的 `p_target` 是当前正式 truth output 事件，已经包含生产聚类、target-select、定位/重定位和 truth match；不把这些后级损失误塞进 Poisson-binomial。三 seed 样本很小，黑色叉号只用于检查方向和数量级，不用于重新拟合曲线。

![min_points=6 在各角度/波位的理论曲线](figures/{Path(figures['min6']).name})

真实生产目标级模型仍应写成：

$$P_{{target}}=P_{{cluster}}P_{{select|cluster}}P_{{reloc|select,cluster}}P_{{match|reloc,select,cluster}}.$$ 

本轮能解析计算的是 $P_{{cluster}}$ 的点数 baseline；后三个条件率以及真实连通性必须使用保留审计事件标定。当前旧 CSV 没有将后三者分别记录，因此报告不伪造独立因子。

# 6. 判断与限制

1. `min_points=6` 确实比 3/4/5 更严格；严格程度不是固定 dB，而随角度、波束中心/边缘的 $\kappa/\beta$ 支撑形状变化。请以 CSV 中的 0.5/0.9-Pd 交点和右移量作为结论。
2. 若曲线给出的 6 点工作点仍高于实际 output SCNR，生产 target Pd 会因连通性和后级筛选进一步下降；这时优先检查目标支撑/峰值对齐和后级保留率，不能仅把 CA-CFAR 替换为 GO-CFAR 来掩盖聚类损失。
3. PB 曲线假设候选 hit 条件独立，真实二维 GO hit mask 有相关性；因此本轮不把 PB 曲线宣称为最终生产 Pd，也不宣称 90% 门限。
4. 真实 C+N 背景和 S-only 泄漏的三层单元理论仍见三层报告；本报告专门补齐“单元 → min_points 目标级”的第 7 节数值曲线。

# 7. 复现入口

```bash
python3 scripts/gmti_scnr_eval/17_build_target_level_theory.py \
  --calibration-root outputs/gmti_scnr_eval/formal_calibration_pfa1e6_v2 \
  --evaluation-metrics docs/GMTI_输出SCNR_三层GO模型验证_20260826/validation_3seed_metrics_with_scnr.csv \
  --work-dir docs/GMTI_输出SCNR_目标级minpoints理论_20260826
```

脚本只读生产保留功率图和已有 evaluation CSV，不运行算法、不改变输入、不重新拟合 evaluation seed。输出包括 CSV、PNG、Markdown、PDF、命令清单和输入 SHA-256。
'''
    path = out / "目标级Pd_minpoints理论报告_20260826.md"
    path.write_text(report, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration-root", type=Path, default=Path("outputs/gmti_scnr_eval/formal_calibration_pfa1e6_v2"))
    parser.add_argument("--evaluation-metrics", type=Path, default=Path("docs/GMTI_输出SCNR_三层GO模型验证_20260826/validation_3seed_metrics_with_scnr.csv"))
    parser.add_argument("--work-dir", type=Path, default=Path("docs/GMTI_输出SCNR_目标级minpoints理论_20260826"))
    parser.add_argument("--alpha", type=float, default=13.44951031977817)
    parser.add_argument("--min-reference-scnr", type=float, default=1.0,
                        help="只用实测支撑总信号/背景>=该线性 SCNR 的样本拟合形状")
    parser.add_argument("--scnr-min-db", type=float, default=-25.0)
    parser.add_argument("--scnr-max-db", type=float, default=45.0)
    parser.add_argument("--scnr-step-db", type=float, default=0.5)
    parser.add_argument("--theory-samples", type=int, default=120_000)
    parser.add_argument("--seed", type=int, default=20260826)
    args = parser.parse_args()
    if not 0.0 < args.alpha or args.theory_samples < 1000 or args.scnr_step_db <= 0:
        raise SystemExit("alpha/theory-samples/scnr-step 参数非法")
    out = args.work_dir.resolve(); out.mkdir(parents=True, exist_ok=True)
    calib = args.calibration_root.resolve(); metrics = args.evaluation_metrics.resolve()
    if not calib.is_dir():
        raise SystemExit(f"找不到 calibration-root: {calib}")
    grid = np.arange(args.scnr_min_db, args.scnr_max_db + 0.25 * args.scnr_step_db, args.scnr_step_db)
    profiles, samples = collect_profiles(calib, out, args.min_reference_scnr)
    if not profiles:
        raise SystemExit("没有从生产功率图提取到合格目标支撑模板")
    theory = build_theory(profiles, out, args.alpha, grid, args.theory_samples, args.seed)
    empirical = build_empirical(metrics, out) if metrics.is_file() else []
    summary = build_summary(theory, out)
    figures = plot_outputs(out, theory, empirical)
    manifest = {
        "script": str(Path(__file__).resolve()), "work_dir": str(out),
        "calibration_root": str(calib), "evaluation_metrics": str(metrics),
        "calibration_root_sha256_not_applicable_directory": True,
        "evaluation_metrics_sha256": sha256(metrics) if metrics.is_file() else None,
        "alpha": args.alpha, "pfa": 1e-6, "guard": 4, "background": 16,
        "support_footprint": "3x5 production connectivity candidate footprint",
        "support_measurement_count": len(samples), "profile_count": len(profiles),
        "theory_samples": args.theory_samples, "seed": args.seed,
        "scnr_axis_db": [float(grid[0]), float(grid[-1]), float(args.scnr_step_db)],
        "models": ["section7_PB_conditional_independence_eta1", "empirical_overlay_not_fitted"],
        "figures": figures,
    }
    (out / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = write_report(out, args, profiles, samples, theory, empirical, summary, figures)
    # Render a durable PDF; a Markdown artifact remains authoritative if a TeX
    # installation is unavailable on another machine.
    pdf = out / "目标级Pd_minpoints理论报告_20260826.pdf"
    command = ["pandoc", str(md), "-o", str(pdf), "--pdf-engine=xelatex", "-V", "CJKmainfont=Noto Sans CJK SC", "-V", "geometry:margin=1.55cm"]
    render_log = out / "pandoc_render.log"
    with render_log.open("w", encoding="utf-8") as stream:
        rendered = subprocess.run(command, cwd=out, stdout=stream, stderr=subprocess.STDOUT, check=False, text=True)
    manifest["pdf_render_exit_code"] = int(rendered.returncode)
    (out / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"work_dir": str(out), "markdown": str(md), "pdf": str(pdf) if pdf.is_file() else None,
                      "profiles": len(profiles), "support_measurements": len(samples), "empirical_rows": len(empirical),
                      "summary_rows": len(summary), "pdf_exit_code": rendered.returncode}, ensure_ascii=False))
    return 0 if rendered.returncode == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
