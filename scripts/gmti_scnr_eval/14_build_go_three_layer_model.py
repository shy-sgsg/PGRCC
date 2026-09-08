#!/usr/bin/env python3
"""Build the three-layer GO-CFAR model and a compact validation report.

The script consumes retained production diagnostics only.  It does not run the
algorithm or manufacture an input-SNR axis:

* A: IID Gaussian, no target leakage, with the eight independent blocks and
  shared corner blocks used by the production GO statistic;
* B: the same blocks with non-central chi-square block sums.  The block
  non-centralities are measured from S-only production power maps;
* C: empirical C+N-only directional means, optionally shifted by the measured
  S-only leakage template.

The output directory is a self-contained audit artifact.  The PDF is a new
supplement; callers may append it to the existing mother PDF after inspecting
the files.  All probability curves use output SCNR in dB on the x-axis.
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
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import brentq
from scipy.special import comb
from scipy.stats import ncx2

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(SCRIPT_DIR))

from scnr_eval_lib import read_csv, wilson_interval, write_csv  # noqa: E402

_extractor = None


def extractor_module():
    global _extractor
    if _extractor is None:
        from importlib.machinery import SourceFileLoader

        _extractor = SourceFileLoader(
            "go_diag_extract", str(SCRIPT_DIR / "05_extract_go_diagnostics.py")
        ).load_module()
    return _extractor


def fnum(value: object, default: float = float("nan")) -> float:
    try:
        text = str(value).strip()
        return float(text) if text else default
    except (TypeError, ValueError):
        return default


def finite(value: object) -> bool:
    return math.isfinite(fnum(value))


def read_kv(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            result[key.strip()] = value.strip()
    return result


def read_xml_values(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    return {node.tag: node.text.strip() for node in root.iter()
            if node.text and node.text.strip()}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class Geometry:
    guard_half: int
    background: int = 16

    @property
    def guard_width(self) -> int:
        return 2 * self.guard_half + 1

    @property
    def outer_half(self) -> int:
        return self.guard_half + self.background

    @property
    def outer_width(self) -> int:
        return 2 * self.outer_half + 1

    @property
    def direction_count(self) -> int:
        return self.background * self.outer_width

    @property
    def block_counts(self) -> np.ndarray:
        b = self.background
        w = self.guard_width
        # TL, TC, TR, LC, RC, BL, BC, BR.  The four corner blocks are
        # shared by the horizontal and vertical directional means.
        return np.asarray([b * b, b * w, b * b, w * b,
                           w * b, b * b, b * w, b * b], dtype=np.int64)

    def validate(self) -> None:
        expected = [256, self.background * self.guard_width, 256,
                    self.guard_width * self.background,
                    self.guard_width * self.background, 256,
                    self.background * self.guard_width, 256]
        if self.block_counts.tolist() != expected:
            raise AssertionError(f"GO block geometry mismatch: {self.block_counts}")


def direction_from_blocks(blocks: np.ndarray, geometry: Geometry) -> np.ndarray:
    """Return L/R/T/B means from [TL,TC,TR,LC,RC,BL,BC,BR] sums."""
    tl, tc, tr, lc, rc, bl, bc, br = (blocks[:, i] for i in range(8))
    denom = float(geometry.direction_count)
    return np.column_stack(((tl + lc + bl) / denom,
                            (tr + rc + br) / denom,
                            (tl + tc + tr) / denom,
                            (bl + bc + br) / denom))


def iid_m_samples(geometry: Geometry, count: int, seed: int,
                  batch_size: int = 100_000) -> np.ndarray:
    geometry.validate()
    rng = np.random.default_rng(seed)
    result: list[np.ndarray] = []
    left = count
    while left:
        n = min(left, batch_size)
        # A central chi-square with 2k degrees of freedom divided by 2 is
        # Gamma(k, 1), i.e. a sum of k unit-mean exponential powers.
        blocks = rng.gamma(shape=geometry.block_counts.astype(float),
                            scale=1.0, size=(n, 8))
        result.append(np.max(direction_from_blocks(blocks, geometry), axis=1))
        left -= n
    return np.concatenate(result) if result else np.empty(0, dtype=float)


def calibrate_alpha(m: np.ndarray, pfa: float) -> float:
    if m.size == 0:
        raise ValueError("cannot calibrate alpha from empty training samples")

    def residual(alpha: float) -> float:
        # Given M=m, a target-free complex Gaussian CUT has an exponential
        # power tail exp(-alpha*m).  This is the exact conditional Pfa.
        return float(np.mean(np.exp(-alpha * m)) - pfa)

    return float(brentq(residual, 1.0, 100.0, xtol=1e-12, rtol=1e-12))


def conditional_pd(gamma: np.ndarray, alpha: float, m: np.ndarray,
                   batch_size: int = 20_000) -> np.ndarray:
    gamma = np.asarray(gamma, dtype=float).reshape(-1)
    out = np.zeros_like(gamma)
    for start in range(0, m.size, batch_size):
        part = m[start:start + batch_size]
        out += np.sum(ncx2.sf(2.0 * alpha * part[:, None], 2,
                               2.0 * gamma[None, :]), axis=0)
    return out / max(1, m.size)


def noncentral_pd(gamma: np.ndarray, alpha: float, geometry: Geometry,
                  lambda_per_gamma: np.ndarray, samples: int,
                  seed: int) -> np.ndarray:
    """Evaluate B using non-central 8-block sums.

    ``lambda_per_gamma[j]`` is the measured sum of |s_i|^2/P0 in block j
    when the CUT output SCNR is one (gamma=1).  A block with n cells has
    ``2*sum |s_i|^2/P0`` non-centrality and 2n degrees of freedom.
    """
    gamma = np.asarray(gamma, dtype=float).reshape(-1)
    lam = np.asarray(lambda_per_gamma, dtype=float)
    if lam.shape != (8,):
        raise ValueError(f"expected 8 block non-centralities, got {lam.shape}")
    rng = np.random.default_rng(seed)
    out = np.zeros_like(gamma)
    df = 2.0 * geometry.block_counts.astype(float)
    for index, value in enumerate(gamma):
        blocks = np.empty((samples, 8), dtype=float)
        ncp = np.maximum(0.0, 2.0 * value * lam)
        for block in range(8):
            # ``direction_from_blocks`` expects each column to be the raw
            # block power sum.  Dividing by ``df`` here and then dividing the
            # directional strip by ``n_dir`` would normalize twice (and make
            # the no-leak limit spuriously report Pd≈Pfa).  Since
            # 2|x|^2/P0 is noncentral-chi-square, |x|^2/P0 is that variate
            # divided by 2; the subsequent directional denominator converts
            # the block sums to the production strip means.
            blocks[:, block] = rng.noncentral_chisquare(
                df[block], ncp[block], size=samples) / 2.0
        m = np.max(direction_from_blocks(blocks, geometry), axis=1)
        out[index] = float(np.mean(ncx2.sf(2.0 * alpha * m, 2, 2.0 * value)))
    return out


def block_slices(geometry: Geometry) -> list[tuple[str, range, range]]:
    h = geometry.guard_half
    o = geometry.outer_half
    return [
        ("TL", range(-o, -h), range(-o, -h)),
        ("TC", range(-o, -h), range(-h, h + 1)),
        ("TR", range(-o, -h), range(h + 1, o + 1)),
        ("LC", range(-h, h + 1), range(-o, -h)),
        ("RC", range(-h, h + 1), range(h + 1, o + 1)),
        ("BL", range(h + 1, o + 1), range(-o, -h)),
        ("BC", range(h + 1, o + 1), range(-h, h + 1)),
        ("BR", range(h + 1, o + 1), range(h + 1, o + 1)),
    ]


def directional_means(power: np.ndarray, row: int, col: int,
                      geometry: Geometry) -> np.ndarray:
    rows, cols = power.shape
    h = geometry.guard_half
    o = geometry.outer_half
    dirs = [
        (range(-o, o + 1), range(-o, -h)),
        (range(-o, o + 1), range(h + 1, o + 1)),
        (range(-o, -h), range(-o, o + 1)),
        (range(h + 1, o + 1), range(-o, o + 1)),
    ]
    return np.asarray([
        float(np.mean([power[(row + dr) % rows, col + dc]
                       for dr in rr for dc in cc]))
        for rr, cc in dirs
    ], dtype=float)


def parse_scene_label(angle_dir: str) -> tuple[float, str, str]:
    if angle_dir.endswith("_edge08_fix1"):
        return 0.8, "edge", "0deg_edge"
    match = re.fullmatch(r"angle_(\d+)deg", angle_dir)
    if not match:
        raise ValueError(f"unsupported angle directory: {angle_dir}")
    angle = float(match.group(1))
    return angle, "center", f"{angle:g}deg_center"


def pair_cn_power(s_only_truth: Path) -> Path:
    # .../variants/targets/<case>/s_only/stage2/truth/file.csv
    variants = s_only_truth.parents[3].parent.parent
    return variants / "c_plus_n/stage2/algorithm_result/period_0000/debug/cfar_GMTI01_beam001_power.f32"


@dataclass
class LeakageTemplate:
    group: str
    angle_deg: float
    label: str
    lambda_per_gamma: np.ndarray
    direction_per_gamma: np.ndarray
    sample_count: int
    qualified_count: int
    reference: str


def collect_leakage(root: Path, out: Path, max_cases: int = 10_000) -> dict[str, LeakageTemplate]:
    diag = extractor_module()
    support_rows: list[dict[str, object]] = []
    accum: dict[str, list[dict[str, object]]] = {}
    candidates = sorted(root.glob("angle_*/seed_*/paired/variants/targets/*/s_only/stage2/truth/truth_targets_by_beam.csv"))
    for truth_path in candidates[:max_cases]:
        angle_dir = next((part for part in truth_path.parts if part.startswith("angle_")), "")
        if angle_dir == "angle_00deg_edge08":
            # This directory predates the relative-radial-velocity truth fix.
            continue
        try:
            angle, label, group = parse_scene_label(angle_dir)
        except ValueError:
            continue
        truth_rows = read_csv(truth_path)
        truth = next((row for row in truth_rows if str(row.get("beam_id", "1")) == "1"), None)
        if truth is None:
            continue
        stage2 = truth_path.parents[1]
        algorithm = stage2 / "algorithm_result/period_0000"
        s_path = algorithm / "debug/cfar_GMTI01_beam001_power.f32"
        cn_path = pair_cn_power(truth_path)
        if not s_path.is_file() or not cn_path.is_file():
            continue
        meta = read_kv(algorithm / "debug/cfar_GMTI01_beam001_meta.txt")
        cfg_path = stage2 / "config/temp_config_stage2_newsystem.xml"
        if not cfg_path.is_file():
            continue
        cfg = read_xml_values(cfg_path)
        geometry = Geometry(int(float(cfg.get("cfar_guard_cells", 4))),
                            int(float(cfg.get("cfar_background_cells", 16))))
        geometry.validate()
        rows = int(float(meta.get("rows", 128)))
        cols = int(float(meta.get("cols", 4096)))
        prf = float(cfg.get("PRF", 1300.0))
        row, row_source = diag.truth_row_on_cfar_axis(truth, meta, rows, prf)
        col = int(round(fnum(truth.get("range_bin"), -1)))
        if col < geometry.outer_half or col + geometry.outer_half >= cols:
            continue
        s_map = np.fromfile(s_path, dtype=np.float32).reshape(rows, cols)
        cn_map = np.fromfile(cn_path, dtype=np.float32).reshape(rows, cols)
        # A robust local S-only floor.  It is estimated from the same GO
        # window after removing the guard square, so no leakage power is
        # injected by a hand-entered offset.
        h, o = geometry.guard_half, geometry.outer_half
        floor_values = [float(s_map[(row + dr) % rows, col + dc])
                        for dr in range(-o, o + 1) for dc in range(-o, o + 1)
                        if not (-h <= dr <= h and -h <= dc <= h)]
        s_floor = float(np.median(floor_values))
        p0_dirs = directional_means(cn_map, row, col, geometry)
        p0 = float(np.max(p0_dirs))
        if not math.isfinite(p0) or p0 <= 0.0:
            continue
        cut_signal = max(float(s_map[row % rows, col]) - s_floor, 0.0)
        gamma_ref = cut_signal / p0
        block_sum = []
        for _, rr, cc in block_slices(geometry):
            block_sum.append(sum(max(float(s_map[(row + dr) % rows, col + dc]) - s_floor, 0.0)
                                for dr in rr for dc in cc) / p0)
        block_sum_arr = np.asarray(block_sum, dtype=float)
        dir_sum = np.asarray([
            (block_sum_arr[0] + block_sum_arr[3] + block_sum_arr[5]) / geometry.direction_count,
            (block_sum_arr[2] + block_sum_arr[4] + block_sum_arr[7]) / geometry.direction_count,
            (block_sum_arr[0] + block_sum_arr[1] + block_sum_arr[2]) / geometry.direction_count,
            (block_sum_arr[5] + block_sum_arr[6] + block_sum_arr[7]) / geometry.direction_count,
        ])
        row_out: dict[str, object] = {
            "angle_group": group, "angle_deg": angle, "position": label,
            "seed": next((p for p in truth_path.parts if p.startswith("seed_")), ""),
            "target_id": truth.get("target_id", ""),
            "configured_target_snr_db": truth.get("snr_db", ""),
            "truth_row": row, "truth_col": col, "row_source": row_source,
            "guard": geometry.guard_half, "background": geometry.background,
            "s_only_floor": s_floor, "cut_signal_power": cut_signal,
            "cut_gamma_ref": gamma_ref, "cn_background_p0": p0,
            "cn_mean_L": p0_dirs[0], "cn_mean_R": p0_dirs[1],
            "cn_mean_T": p0_dirs[2], "cn_mean_B": p0_dirs[3],
        }
        for index, value in enumerate(block_sum_arr):
            row_out[f"block_{index}_lambda_ref"] = value
        for index, value in enumerate(dir_sum):
            row_out[f"direction_{index}_delta_ref"] = value
        support_rows.append(row_out)
        accum.setdefault(group, []).append(row_out)
    write_csv(out / "target_leakage_support.csv", support_rows)

    templates: dict[str, LeakageTemplate] = {}
    template_rows: list[dict[str, object]] = []
    for group, rows in sorted(accum.items()):
        # Low-SNR S-only CUTs are retained in the audit CSV, but their ratio
        # is noise-dominated.  The qualification is data-derived: measured
        # S-only excess must be at least the measured C+N training reference.
        qualified = [row for row in rows if fnum(row["cut_gamma_ref"]) >= 1.0]
        source = qualified if qualified else rows
        ratios = np.asarray([
            [fnum(row.get(f"block_{i}_lambda_ref")) / max(fnum(row.get("cut_gamma_ref")), 1e-12)
             for i in range(8)] for row in source
        ], dtype=float)
        dir_ratios = np.asarray([
            [fnum(row.get(f"direction_{i}_delta_ref")) / max(fnum(row.get("cut_gamma_ref")), 1e-12)
             for i in range(4)] for row in source
        ], dtype=float)
        block = np.nanmedian(ratios, axis=0)
        direction = np.nanmedian(dir_ratios, axis=0)
        templates[group] = LeakageTemplate(
            group=group, angle_deg=fnum(source[0].get("angle_deg")),
            label=str(source[0].get("position", "center")),
            lambda_per_gamma=block, direction_per_gamma=direction,
            sample_count=len(rows), qualified_count=len(qualified),
            reference="production S-only GO power; nonnegative excess over local S-only floor; qualified cut_gamma_ref>=1",
        )
        template_rows.append({
            "angle_group": group, "angle_deg": templates[group].angle_deg,
            "position": templates[group].label, "sample_count": len(rows),
            "qualified_count_cut_gamma_ref_ge_1": len(qualified),
            "support_source": templates[group].reference,
            **{f"block_{i}_lambda_per_gamma": block[i] for i in range(8)},
            **{f"direction_{i}_delta_per_gamma": direction[i] for i in range(4)},
        })
    write_csv(out / "target_leakage_templates.csv", template_rows)
    return templates


def collect_real_windows(root: Path, out: Path, max_per_map: int = 8_000,
                         seed: int = 20260826) -> np.ndarray:
    rows: list[dict[str, object]] = []
    geometry = Geometry(4, 16)
    rng = np.random.default_rng(seed)
    paths = sorted(root.glob("angle_*/seed_*/paired/variants/c_plus_n/stage2/algorithm_result/period_0000/debug/*_power.f32"))
    for path in paths:
        angle_dir = next((part for part in path.parts if part.startswith("angle_")), "")
        if angle_dir == "angle_00deg_edge08":
            continue
        try:
            angle, position, group = parse_scene_label(angle_dir)
        except ValueError:
            continue
        meta = read_kv(path.with_name(path.name.replace("_power.f32", "_meta.txt")))
        shape = (int(float(meta.get("rows", 128))), int(float(meta.get("cols", 4096))))
        power = np.fromfile(path, dtype=np.float32)
        if power.size != shape[0] * shape[1]:
            continue
        power = power.reshape(shape)
        o = geometry.outer_half
        valid_rows = np.arange(shape[0], dtype=int)
        valid_cols = np.arange(o, shape[1] - o, dtype=int)
        total = valid_rows.size * valid_cols.size
        count = min(max_per_map, total)
        flat = rng.choice(total, size=count, replace=False)
        row_idx = flat // valid_cols.size
        col_idx = flat % valid_cols.size
        for ri, ci in zip(row_idx, col_idx):
            row = int(valid_rows[ri]); col = int(valid_cols[ci])
            means = directional_means(power, row, col, geometry)
            cut = float(power[row, col]); m = float(np.max(means))
            if not math.isfinite(cut) or cut <= 1e-12 or not math.isfinite(m):
                continue
            rows.append({
                "angle_group": group, "angle_deg": angle, "position": position,
                "seed": next((part for part in path.parts if part.startswith("seed_")), ""),
                "source_map": str(path), "row": row, "col": col,
                "mean_L": means[0], "mean_R": means[1], "mean_T": means[2],
                "mean_B": means[3], "M": m, "CUT_background": cut,
                "M_normalized": m / cut,
                "guard": geometry.guard_half, "background": geometry.background,
            })
    write_csv(out / "empirical_go_windows.csv", rows)
    return np.asarray([[fnum(row["mean_L"]) / fnum(row["CUT_background"]),
                        fnum(row["mean_R"]) / fnum(row["CUT_background"]),
                        fnum(row["mean_T"]) / fnum(row["CUT_background"]),
                        fnum(row["mean_B"]) / fnum(row["CUT_background"]),
                        fnum(row["M_normalized"])] for row in rows], dtype=float)


def empirical_curves(windows_rows: list[dict[str, object]], templates: dict[str, LeakageTemplate],
                     grid_db: np.ndarray, alpha: float, out: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    gamma = 10.0 ** (grid_db / 10.0)
    for group in sorted(templates):
        group_rows = [row for row in windows_rows if row.get("angle_group") == group]
        if not group_rows:
            group_rows = windows_rows
        means = np.asarray([[fnum(row.get(f"mean_{d}")) / max(fnum(row.get("CUT_background")), 1e-12)
                             for d in ("L", "R", "T", "B")] for row in group_rows], dtype=float)
        base_m = np.max(means, axis=1)
        template = templates[group]
        for model, leak in (("C_real_CN", False), ("C_real_CN_leak", True)):
            values = []
            for value in gamma:
                m = base_m
                if leak:
                    m = np.max(means + value * template.direction_per_gamma[None, :], axis=1)
                values.append(float(np.mean(ncx2.sf(2.0 * alpha * m, 2, 2.0 * value))))
            for scnr, pd in zip(grid_db, values):
                result.append({"angle_group": group, "guard": 4, "model": model,
                               "output_scnr_db": scnr, "pd_unit": pd,
                               "window_count": len(group_rows),
                               "leak_source": template.reference if leak else "none"})
    write_csv(out / "empirical_go_curves.csv", result)
    return result


def load_metric_rows(path: Path) -> list[dict[str, str]]:
    return read_csv(path) if path.is_file() else []


def cluster_proxy(row: dict[str, str]) -> int:
    unit = int(round(fnum(row.get("unit_go_cfar_hit"), 0.0)))
    if not unit:
        return 0
    size = fnum(row.get("unit_go_hit_component_size"))
    if not math.isfinite(size):
        return 0
    if size >= 6:
        return 1
    # The production small-cluster rule needs the peak-over-median test.  The
    # retained target audit has no peak field for rejected candidates; a
    # final target output is therefore the auditable lower-bound proxy for
    # the 3--5 point recovery branch.
    return int(3 <= size <= 5 and int(round(fnum(row.get("final_output"), 0.0))) == 1)


def target_layers(metrics: list[dict[str, str]], out: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    grouped: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
    for row in metrics:
        scnr = fnum(row.get("scnr_out_db"))
        if not math.isfinite(scnr):
            continue
        key = (str(row.get("case", "")), str(row.get("label", "")),
               str(row.get("truth_angle_case_deg", row.get("truth_angle_deg", ""))),
               f"{scnr:.6f}")
        grouped.setdefault(key, []).append(row)
    for (case, position, angle, scnr_text), group in sorted(grouped.items()):
        n = len(group)
        unit = np.asarray([int(round(fnum(r.get("unit_go_cfar_hit"), 0.0))) for r in group])
        cluster = np.asarray([cluster_proxy(r) for r in group])
        target = np.asarray([int(round(fnum(r.get("final_output"), 0.0))) for r in group])
        cluster_n = int(cluster.sum())
        target_n = int(target.sum())
        target_after_cluster_n = int(np.sum(target * cluster))
        target_without_cluster_n = target_n - target_after_cluster_n
        p_unit = float(unit.mean())
        p_cluster = float(cluster.mean())
        p_target = float(target.mean())
        # Observable production conditional product.  Selection, relocation,
        # and truth match are not separately logged in the retained CSV; do
        # not invent three independent factors.  Their product is auditable.
        # The intersection is used for a conditional probability.  A target
        # output whose retained unit-component proxy is absent is recorded as
        # a consistency mismatch, never allowed to create a probability > 1.
        p_post = target_after_cluster_n / cluster_n if cluster_n else float("nan")
        # Analytic Poisson-binomial baseline only: 15 candidate positions are
        # the production 3x(2*cluster_max_gap+1) connectivity footprint.
        pb = float(sum(comb(15, k) * p_unit ** k * (1 - p_unit) ** (15 - k)
                       for k in range(6, 16)))
        rows.append({
            "case": case, "position": position, "angle_deg": angle,
            "output_scnr_db": fnum(scnr_text), "n": n,
            "unit_hits": int(unit.sum()), "cluster_proxy_hits": cluster_n,
            "target_hits": target_n, "target_hits_after_cluster_proxy": target_after_cluster_n,
            "target_outputs_without_cluster_proxy": target_without_cluster_n,
            "p_unit": p_unit,
            "p_cluster": p_cluster, "p_target": p_target,
            "p_select_reloc_match_given_cluster": p_post,
            "p_select_given_cluster": "unidentifiable_from_retained_audit",
            "p_reloc_given_select_cluster": "unidentifiable_from_retained_audit",
            "p_match_given_reloc_select_cluster": "unidentifiable_from_retained_audit",
            "poisson_binomial_n15_min6": pb,
            "cluster_rule": "component>=6 OR 3<=component<=5 and final_output (small-branch lower-bound proxy)",
        })
    write_csv(out / "target_layer_retention.csv", rows)
    return rows


def track_stats(track_path: Path, out: Path) -> list[dict[str, object]]:
    source = load_metric_rows(track_path)
    by_group: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in source:
        scnr = fnum(row.get("scnr_out_db"))
        if math.isfinite(scnr):
            by_group.setdefault((str(row.get("case", "")), f"{scnr:.6f}"), []).append(row)
    result: list[dict[str, object]] = []
    for (case, scnr_text), group in sorted(by_group.items()):
        h = np.asarray([[int(round(fnum(row.get(f"frame{i}_target_output"), 0.0))) for i in (1, 2, 3)]
                        for row in group], dtype=int)
        p = h.mean(axis=0)
        p12 = float(np.mean(h[:, 0] * h[:, 1])); p13 = float(np.mean(h[:, 0] * h[:, 2])); p23 = float(np.mean(h[:, 1] * h[:, 2]))
        p123 = float(np.mean(h[:, 0] * h[:, 1] * h[:, 2]))
        p2of3 = float(np.mean(h.sum(axis=1) >= 2))
        pbar = float(np.mean(p))
        iid = 3.0 * pbar * pbar - 2.0 * pbar ** 3
        nonind = p12 + p13 + p23 - 2.0 * p123
        cond12 = p12 / p[0] if p[0] > 0 else float("nan")
        cond21 = (p[1] - p12) / (1.0 - p[0]) if p[0] < 1 else float("nan")
        cond23 = p23 / p[1] if p[1] > 0 else float("nan")
        cond32 = (p[2] - p23) / (1.0 - p[1]) if p[1] < 1 else float("nan")
        phi12 = (p12 - p[0] * p[1]) / math.sqrt(max(p[0] * (1 - p[0]) * p[1] * (1 - p[1]), 1e-15))
        phi23 = (p23 - p[1] * p[2]) / math.sqrt(max(p[1] * (1 - p[1]) * p[2] * (1 - p[2]), 1e-15))
        result.append({
            "case": case, "output_scnr_db": fnum(scnr_text), "n_targets": len(group),
            "p_screen1": p[0], "p_screen2": p[1], "p_screen3": p[2],
            "p12": p12, "p13": p13, "p23": p23, "p123": p123,
            "track_2of3_hit": p2of3, "track_iid_2of3": iid,
            "track_nonind_2of3": nonind, "delta_nonind_minus_iid": nonind - iid,
            "P_D2_given_D1": cond12, "P_D2_given_notD1": cond21,
            "P_D3_given_D2": cond23, "P_D3_given_notD2": cond32,
            "phi12": phi12, "phi23": phi23,
            "track_definition": "truth three-screen 2-of-3; TrackManager only appendix",
        })
    write_csv(out / "track_three_screen_stats.csv", result)
    return result


def crossing(grid: np.ndarray, values: np.ndarray, target: float = 0.5) -> float:
    order = np.argsort(grid)
    x = grid[order]; y = np.asarray(values)[order]
    for i in range(1, len(x)):
        if y[i - 1] < target <= y[i]:
            if y[i] == y[i - 1]:
                return float(x[i])
            return float(x[i - 1] + (target - y[i - 1]) * (x[i] - x[i - 1]) / (y[i] - y[i - 1]))
    return float("nan")


def model_shift_summary(iid_rows: list[dict[str, object]], empirical_rows: list[dict[str, object]],
                        groups: Iterable[str], out: Path) -> list[dict[str, object]]:
    """Compare A/B/C on the same output-SCNR grid.

    A finite crossing is intentionally not extrapolated outside the modeled
    grid.  In particular, a leakage curve that starts above 0.5 is reported as
    ``nan`` rather than receiving a fabricated negative/positive dB shift.
    """
    def curve(rows: list[dict[str, object]], model: str, group: str, empirical: bool) -> tuple[np.ndarray, np.ndarray]:
        selected = [r for r in rows if r.get("model") == model and
                    (r.get("angle_group") == group if empirical else
                     (r.get("angle_group") == group and int(round(fnum(r.get("guard"), 4))) == 4))]
        return (np.asarray([fnum(r.get("output_scnr_db")) for r in selected], dtype=float),
                np.asarray([fnum(r.get("pd_unit")) for r in selected], dtype=float))

    result: list[dict[str, object]] = []
    for group in sorted(groups):
        xa, ya = curve(iid_rows, "A_IID_no_leak", group, False)
        xb, yb = curve(iid_rows, "B_IID_leak", group, False)
        xc, yc = curve(empirical_rows, "C_real_CN", group, True)
        xd, yd = curve(empirical_rows, "C_real_CN_leak", group, True)
        a = crossing(xa, ya); b = crossing(xb, yb); c = crossing(xc, yc); d = crossing(xd, yd)
        result.append({
            "angle_group": group,
            "A_IID_no_leak_0p5_db": a, "B_IID_leak_0p5_db": b,
            "C_real_CN_0p5_db": c, "C_real_CN_leak_0p5_db": d,
            "leakage_shift_iid_db": b - a if math.isfinite(a) and math.isfinite(b) else float("nan"),
            "noniid_shift_no_leak_db": c - a if math.isfinite(a) and math.isfinite(c) else float("nan"),
            "leakage_shift_real_db": d - c if math.isfinite(c) and math.isfinite(d) else float("nan"),
            "noniid_shift_with_leak_db": d - b if math.isfinite(b) and math.isfinite(d) else float("nan"),
            "grid_interpretation": "nan means no 0.5 crossing in the modeled output-SCNR grid; no extrapolation",
        })
    write_csv(out / "model_shift_summary.csv", result)
    return result


def plot_style() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans"],
        "axes.unicode_minus": False, "figure.dpi": 150, "savefig.dpi": 220,
        "axes.grid": True, "grid.alpha": 0.25, "font.size": 9,
    })


def savefig(fig: plt.Figure, path: Path) -> None:
    fig.tight_layout(); fig.savefig(path, bbox_inches="tight"); plt.close(fig)


def plot_outputs(out: Path, iid_rows: list[dict[str, object]], empirical_rows: list[dict[str, object]],
                 target_rows: list[dict[str, object]], track_rows: list[dict[str, object]],
                 metrics: list[dict[str, str]]) -> dict[str, str]:
    plot_style(); figdir = out / "figures"; figdir.mkdir(parents=True, exist_ok=True)
    colors = {"0deg_center": "#1f77b4", "0deg_edge": "#9467bd", "30deg_center": "#d62728", "45deg_center": "#2ca02c"}
    # Main required four panels.  Every x coordinate is output SCNR.
    fig, axes = plt.subplots(2, 2, figsize=(10.4, 7.0))
    panels = [("unit_pd", "单元级 Pd", "pd_unit"), ("target_pd", "目标级 Pd", "p_target"),
              ("track_pd", "航迹级 Pd（三屏选二）", "track_2of3_hit"), ("angle_rmse", "测角 RMSE (deg)", "angle_rmse")]
    # Observed unit/target points by angle and SCNR.
    obs: dict[tuple[str, float], list[dict[str, str]]] = {}
    for row in metrics:
        x = fnum(row.get("scnr_out_db"));
        if math.isfinite(x): obs.setdefault((str(row.get("case", "")), x), []).append(row)
    for ax, (name, title, field) in zip(axes.ravel(), panels):
        ax.set_title(title); ax.set_xlabel("output SCNR (dB)"); ax.set_ylabel("probability" if name != "angle_rmse" else "degrees")
        if name in ("unit_pd", "target_pd"):
            for (case, x), group in sorted(obs.items()):
                ykey = "unit_go_cfar_hit" if name == "unit_pd" else "final_output"
                y = np.mean([int(round(fnum(r.get(ykey), 0.0))) for r in group])
                ax.scatter(x, y, color=colors.get(case, "#333333"), label=case)
            # A production guard-4 empirical layer is the most relevant curve.
            for model, style in [("A_IID_no_leak", "-"), ("B_IID_leak", "--"), ("C_real_CN_leak", ":")]:
                rr = [r for r in iid_rows + empirical_rows if r.get("model") == model and r.get("angle_group", "0deg_center") == "0deg_center"]
                if rr:
                    ax.plot([fnum(r.get("output_scnr_db")) for r in rr], [fnum(r.get("pd_unit")) for r in rr], linestyle=style, color="#111111", label=model)
        elif name == "track_pd":
            for row in track_rows:
                ax.scatter(fnum(row.get("output_scnr_db")), fnum(row.get("track_2of3_hit")), color="#d62728")
        else:
            grouped: dict[float, list[float]] = {}
            for row in metrics:
                x = fnum(row.get("scnr_out_db")); e = fnum(row.get("angle_error_deg"))
                if math.isfinite(x) and math.isfinite(e): grouped.setdefault(x, []).append(e * e)
            for x, vals in sorted(grouped.items()): ax.scatter(x, math.sqrt(float(np.mean(vals))), color="#1f77b4")
        if name != "angle_rmse": ax.set_ylim(-0.05, 1.05)
        ax.set_xlim(-35, 40)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    if handles: axes[0, 0].legend(handles, labels, fontsize=6, loc="lower right")
    main_path = figdir / "01_main_output_scnr_pd_rmse.png"; savefig(fig, main_path)

    # Three-layer GO curves, guard sweep and production guard mark.
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    for model, label, style, color in [("A_IID_no_leak", "A IID 无泄漏", "-", "#111111"),
                                        ("B_IID_leak", "B IID + 实测泄漏", "--", "#d62728"),
                                        ("C_real_CN", "C 真实 C+N", "-.", "#1f77b4"),
                                        ("C_real_CN_leak", "C 真实 C+N + 实测泄漏", ":", "#2ca02c")]:
        rr = [r for r in iid_rows + empirical_rows if r.get("model") == model and int(round(fnum(r.get("guard"), 4))) == 4 and r.get("angle_group", "0deg_center") == "0deg_center"]
        if rr: ax.plot([fnum(r.get("output_scnr_db")) for r in rr], [fnum(r.get("pd_unit")) for r in rr], linestyle=style, color=color, label=label)
    for guard, color in [(3, "#777777"), (5, "#9467bd")]:
        rr = [r for r in iid_rows if r.get("model") == "A_IID_no_leak" and int(round(fnum(r.get("guard")))) == guard]
        if rr: ax.plot([fnum(r.get("output_scnr_db")) for r in rr], [fnum(r.get("pd_unit")) for r in rr], color=color, label=f"A IID guard={guard}")
    ax.set_xlabel("output SCNR (dB)"); ax.set_ylabel("unit Pd"); ax.set_title("三层 GO 理论；生产 guard=4"); ax.set_ylim(-0.02, 1.02); ax.legend(fontsize=7)
    three_path = figdir / "02_three_layer_go_curves.png"; savefig(fig, three_path)

    # Layer loss / crossing shifts.
    fig, ax = plt.subplots(figsize=(7.8, 4.4))
    rows = []
    for group in sorted({str(r.get("angle_group")) for r in empirical_rows}):
        def vals(model):
            rr = [r for r in empirical_rows if r.get("angle_group") == group and r.get("model") == model]
            return np.asarray([fnum(r.get("output_scnr_db")) for r in rr]), np.asarray([fnum(r.get("pd_unit")) for r in rr])
        xa, ya = vals("C_real_CN"); xb, yb = vals("C_real_CN_leak")
        if xa.size and xb.size:
            a = crossing(xa, ya); b = crossing(xb, yb)
            rows.append((group, a, b, b - a))
    labels = [r[0] for r in rows]; shifts = [r[3] for r in rows]
    ax.bar(labels, shifts, color="#d62728"); ax.axhline(0, color="#333333", linewidth=0.8)
    ax.set_ylabel("泄漏造成的 0.5-Pd 右移 (dB)"); ax.set_title("真实 C+N 背景：目标泄漏右移")
    for i, value in enumerate(shifts): ax.text(i, value, f"{value:.1f} dB", ha="center", va="bottom")
    shift_path = figdir / "03_leakage_shift_db.png"; savefig(fig, shift_path)

    # Track screen dependence.
    fig, ax = plt.subplots(figsize=(8.0, 4.4))
    if track_rows:
        x = [fnum(r.get("output_scnr_db")) for r in track_rows]
        ax.scatter(x, [fnum(r.get("track_2of3_hit")) for r in track_rows], label="实测 2/3", color="#d62728")
        ax.scatter(x, [fnum(r.get("track_iid_2of3")) for r in track_rows], marker="x", label="iid 2/3", color="#111111")
    ax.set_xlabel("output SCNR (dB)"); ax.set_ylabel("track Pd"); ax.set_title("三屏选二：实测与独立屏幕辅助公式"); ax.set_ylim(-0.05, 1.05); ax.legend()
    track_path = figdir / "04_three_screen_track_correlation.png"; savefig(fig, track_path)
    return {"main": str(main_path), "three_layer": str(three_path), "shift": str(shift_path), "track": str(track_path)}


def markdown_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def fmt(value: object, digits: int = 3) -> str:
    value = fnum(value)
    return "—" if not math.isfinite(value) else f"{value:.{digits}f}"


def write_report(out: Path, args: argparse.Namespace, alpha_map: dict[int, float],
                 pfa_map: dict[int, float], templates: dict[str, LeakageTemplate],
                 windows: list[dict[str, object]], iid_rows: list[dict[str, object]],
                 empirical_rows: list[dict[str, object]], target_rows: list[dict[str, object]],
                 track_rows: list[dict[str, object]], figures: dict[str, str],
                 metrics: list[dict[str, str]], shift_rows: list[dict[str, object]]) -> Path:
    shifts = []
    for group in sorted(templates):
        base = [r for r in empirical_rows if r.get("angle_group") == group and r.get("model") == "C_real_CN"]
        leak = [r for r in empirical_rows if r.get("angle_group") == group and r.get("model") == "C_real_CN_leak"]
        if base and leak:
            x0 = np.asarray([fnum(r.get("output_scnr_db")) for r in base]); y0 = np.asarray([fnum(r.get("pd_unit")) for r in base])
            x1 = np.asarray([fnum(r.get("output_scnr_db")) for r in leak]); y1 = np.asarray([fnum(r.get("pd_unit")) for r in leak])
            a = crossing(x0, y0); b = crossing(x1, y1); shifts.append((group, a, b, b - a))
    cluster_losses = []
    for row in target_rows:
        if fnum(row.get("p_unit")) > 0:
            cluster_losses.append((row.get("position"), row.get("output_scnr_db"),
                                   fnum(row.get("p_cluster")) / fnum(row.get("p_unit")),
                                   fnum(row.get("p_target")) / fnum(row.get("p_unit"))))
    track_summary = track_rows
    alpha_rows = [[g, fmt(alpha_map[g], 8), f"{pfa_map[g]:.3g}", "生产 guard=4" if g == 4 else "MC校准"] for g in sorted(alpha_map)]
    template_table = [[g, t.sample_count, t.qualified_count, ", ".join(fmt(x, 4) for x in t.lambda_per_gamma), ", ".join(fmt(x, 4) for x in t.direction_per_gamma)] for g, t in sorted(templates.items())]
    shift_table = [[g, fmt(a, 2), fmt(b, 2), fmt(s, 2)] for g, a, b, s in shifts]
    full_shift_table = [[r.get("angle_group"), fmt(r.get("A_IID_no_leak_0p5_db"), 2),
                         fmt(r.get("B_IID_leak_0p5_db"), 2), fmt(r.get("C_real_CN_0p5_db"), 2),
                         fmt(r.get("C_real_CN_leak_0p5_db"), 2),
                         fmt(r.get("leakage_shift_iid_db"), 2),
                         fmt(r.get("noniid_shift_no_leak_db"), 2),
                         fmt(r.get("leakage_shift_real_db"), 2),
                         fmt(r.get("noniid_shift_with_leak_db"), 2)] for r in shift_rows]
    target_table = [[r.get("position"), fmt(r.get("output_scnr_db"), 2), r.get("n"), fmt(r.get("p_unit"), 3), fmt(r.get("p_cluster"), 3), fmt(r.get("p_target"), 3), fmt(r.get("p_select_reloc_match_given_cluster"), 3), fmt(r.get("poisson_binomial_n15_min6"), 3)] for r in target_rows]
    track_table = [[fmt(r.get("output_scnr_db"), 2), r.get("n_targets"), fmt(r.get("track_2of3_hit"), 3), fmt(r.get("track_iid_2of3"), 3), fmt(r.get("track_nonind_2of3"), 3), fmt(r.get("P_D2_given_D1"), 3), fmt(r.get("P_D2_given_notD1"), 3), fmt(r.get("phi12"), 3)] for r in track_rows]
    metric_count = len(metrics)
    report = fr'''---
title: "GMTI 输出 SCNR—测角精度—检测概率：三层 GO-CFAR 理论与模型验证"
subtitle: "非中心八块目标泄漏、真实 C+N 背景与三屏选二航迹"
author: "GMTI 算法验证"
date: "2026-08-26"
geometry: margin=1.55cm
mainfont: Noto Sans CJK SC
CJKmainfont: Noto Sans CJK SC
fontsize: 9.5pt
---

# 1. 本轮结论

本轮先完成模型闭环，再使用已有的少量正式 S+C+N 结果作为 evaluation evidence；没有扩大正式 multi-seed sweep。报告把生产 GO-CFAR 明确拆成三层：A 为 IID Gaussian、无泄漏；B 为 IID Gaussian 加真实 S-only 目标支撑的非中心八块；C 为真实 C+N-only 训练窗，再叠加同一真实泄漏模板。四层主曲线的横轴均为 output SCNR。

目前可以量化回答：

* 生产 guard=4 的八块结构为 4 个 $16\times16$ 角块和 4 个 $16\times9$/$9\times16$ 中间块；用训练窗 Monte-Carlo 校准得到 $\alpha$，并单独检查 $P_{{FA}}$。
* 目标泄漏不是固定偏置：每个块的非中心参数来自 S-only 生产功率图的目标支撑，按 $2|s_i|^2/P_0$ 累加，角块在 L/R/T/B 中共享。
* 真实背景窗保存了 `mean_L/R/T/B`、`M`、`CUT_background` 与 `M_normalized`；C 层用逐窗 Marcum-$Q$ 经验积分。
* 目标级和航迹级不是单元 Pd 的简单替代。当前保留的生产审计可以观测 cluster proxy 与后续联合保留率；选择、重定位、truth match 三个条件因子没有被旧 CSV 分开记录，报告不伪造三个独立概率。
* 三屏主指标是 truth 事件的 2-of-3；同时给出屏幕条件概率、$\phi$ 相关系数以及非独立修正 $P_{{12}}+P_{{13}}+P_{{23}}-2P_{{123}}$。

![主验证图：所有横轴为 output SCNR。](figures/{Path(figures['main']).name})

# 2. 参数、来源和几何

| 参数 | 值/来源 |
|---|---|
| $P_{{FA}}$ | $10^{{-6}}$，生产 meta sidecar |
| guard | 3、4、5 理论；生产 guard=4 |
| background | 16 |
| GO 共享块 | TL/TC/TR/LC/RC/BL/BC/BR |
| cluster | min_points=6；3–5 点小簇 +20 dB；最大 range gap=2 |
| target leakage source | 保留的 production S-only CFAR power map 与 paired C+N map |
| real background source | {len(windows)} 个 C+N-only 训练窗，{len({r.get('source_map') for r in windows})} 幅生产图 |
| evaluation metric rows | {metric_count} |

## 2.1 八块统计量

令 $b=16$、$w_g=2g+1$。每个块的单元数为

$$n=[b^2,bw_g,b^2,w_gb,w_gb,b^2,bw_g,b^2].$$

对目标泄漏训练单元 $x_i=s_i+n_i$，若背景功率为 $P_0$，则

$$\frac{{2|x_i|^2}}{{P_0}}\sim\chi'^2_2\left(\frac{{2|s_i|^2}}{{P_0}}\right).$$

因此第 $j$ 个块的和满足

$$Y_j=\sum_{{i\in j}}\frac{{2|x_i|^2}}{{P_0}}\sim\chi'^2_{{2n_j}}(\Lambda_j),\quad \Lambda_j=\sum_{{i\in j}}\frac{{2|s_i|^2}}{{P_0}}.$$

块均值是 $Y_j/(2n_j)$。四方向均值使用共享角块重组：

$$\bar Z_L=(Y_{{TL}}+Y_{{LC}}+Y_{{BL}})/(2n_{{dir}}),$$
$$\bar Z_R=(Y_{{TR}}+Y_{{RC}}+Y_{{BR}})/(2n_{{dir}}),$$
$$\bar Z_T=(Y_{{TL}}+Y_{{TC}}+Y_{{TR}})/(2n_{{dir}}),$$
$$\bar Z_B=(Y_{{BL}}+Y_{{BC}}+Y_{{BR}})/(2n_{{dir}}),$$
$$M=\max(\bar Z_L,\bar Z_R,\bar Z_T,\bar Z_B).$$

对 production guard=4，$n=[256,144,256,144,144,256,144,256]$；这正是 $16\times16$ 角块和 $16\times9/9\times16$ 中间块，而不是八个独立方向均值。

## 2.2 alpha 验证

目标为空时，给定 $M=m$ 的 CUT 功率为单位均值指数变量，因此

$$P_{{FA}}=E[\exp(-\alpha M)].$$

本脚本对每个 guard 用同一八块结构数值解这个方程。生产 guard=4 的 sidecar alpha 为 13.4495103198；表中同时给出重采样得到的 MC 虚警率。$\alpha$ 不是 CA ring 的 13.8753。

{markdown_table(["guard", "alpha", "MC Pfa", "来源"], alpha_rows)}

# 3. 三层 GO 曲线

![三层 GO 曲线与 guard=3/4/5。](figures/{Path(figures['three_layer']).name})

## 3.1 A：IID Gaussian、无泄漏

对 IID 层，八个块分别是独立 Gamma 和，但角块被共享到两个方向，因此 $M$ 仍然相关。给定 output SCNR $\gamma$：

$$P_{{D,A}}(\gamma)=E_M\left[Q_1(\sqrt{{2\gamma}},\sqrt{{2\alpha M}})\right].$$

这是最干净的算法基线，不含目标进入训练窗的效应。

## 3.2 B：IID Gaussian + noncentral leakage

S-only 图先用同一生产 GO 窗提取目标支撑。局部 S-only floor 用窗内 guard 外功率中位数估计，信号功率为 $\max(P_S-P_{{floor}},0)$；以 paired C+N 的方向最大值作 $P_0$。这样得到每个 guard、每个真实 S-only case 的 $\Lambda_j$。只有实测 CUT excess 不低于 C+N 训练参考的样本用于归一化模板，低信噪比样本仍保存在 support CSV 中但不参与形状拟合。

模板摘要：

{markdown_table(["angle group", "全部样本", "合格样本", "8 块 lambda/gamma", "4 方向 delta/gamma"], template_table)}

对 output SCNR $\gamma$，模板非中心参数按 $\Lambda_j(\gamma)=\gamma\,\lambda_j^{{(per\ gamma)}}$ 缩放，然后直接采样 $\chi'^2$ 块和，再对 $Q_1$ 积分：

$$P_{{D,B}}(\gamma)=E\left[Q_1(\sqrt{{2\gamma}},\sqrt{{2\alpha M(\gamma)}})\right].$$

这一步不把泄漏替换成一个固定功率偏置；随机训练窗的方差和共享角块仍在采样中。

## 3.3 C：真实 C+N 背景 + 真实泄漏

每个 C+N-only 窗保存

$$\{{\bar Z_L,\bar Z_R,\bar Z_T,\bar Z_B,M,Z_{{CUT,bg}},M/Z_{{CUT,bg}}\}}.$$

无泄漏经验层为

$$P_{{D,C}}(\gamma)=\frac1R\sum_rQ_1(\sqrt{{2\gamma}},\sqrt{{2\alpha m_r}}),\quad m_r=M_r/Z_{{CUT,bg}},r.$$

加入真实 S-only 模板后，各窗的方向均值按实测的四方向泄漏增量 $\delta_d$ 加上 $\gamma\delta_d$，得到

$$P_{{D,C+leak}}(\gamma)=\frac1R\sum_rQ_1\left(\sqrt{{2\gamma}},\sqrt{{2\alpha\max_d(m_{{r,d}}+\gamma\delta_d)}}\right).$$

这里 C 层是半解析模型：真实背景的非 IID 形状来自生产图，泄漏方向增量来自生产 S-only 支撑；保存的 `empirical_go_windows.csv` 允许逐窗复核。

![真实背景中目标泄漏造成的 0.5-Pd 右移。](figures/{Path(figures['shift']).name})

{markdown_table(["angle group", "C 无泄漏 0.5-Pd", "C+leak 0.5-Pd", "右移(dB)"], shift_table)}

三层交点与差异的完整机器表（A=IID 无泄漏，B=IID+泄漏，C=真实 C+N）：

{markdown_table(["angle group", "A交点", "B交点", "C交点", "C+leak交点", "B-A泄漏右移", "C-A非IID右移", "C+leak-C泄漏右移", "C+leak-B非IID右移"], full_shift_table)}

# 4. 目标级生产半解析模型

生产目标级事件写成

$$P_{{target}}=P_{{cluster}}P_{{select|cluster}}P_{{reloc|select,cluster}}P_{{match|reloc,select,cluster}}.$$

本轮用保留的 GO hit component 直接应用生产连接 footprint（3 行、$2\times2+1=5$ 个 range 位置，最多 15 个候选位置）：

* component $\ge6$：按当前 min_points=6 计 cluster；
* component 3–5：只有已有 `final_output=1` 才能审计为通过 +20 dB 小簇分支，因此这一列是小簇 branch 的下界 proxy；
* `final_output` 是目标级输出事件，必须同时满足生产选择、定位/重定位和 truth 关联。

保存的目标层表：

{markdown_table(["位置", "SCNR", "n", "unit", "cluster", "target", "后处理|cluster", "Poisson-binomial n=15,k>=6"], target_table)}

Poisson-binomial（这里同概率的特例是二项式）只保留为解析 baseline：

$$P_{{PB}}=\sum_{{k=6}}^{{15}}{{15\choose k}}p_{{cell}}^k(1-p_{{cell}})^{{15-k}}.$$

它不替代真实联合 hit mask；真实 cluster proxy 直接来自生产诊断。旧审计 schema 没有分别记录 select、reloc、match 三个布尔事件，故 `target_layer_retention.csv` 明确把它们标为不可由现有文件单独辨识，而不把联合保留率拆成三个拍脑袋的因子。

# 5. 三屏选二航迹

主指标冻结为 truth 事件：$D_t=1$ 表示第 $t$ 屏目标级 truth output 成功，三屏中至少两屏成功即航迹成功。

若屏幕近似独立且共同概率为 $p$：

$$P_{{track,iid}}=3p^2-2p^3.$$

实测直接统计 $P_{{12}},P_{{13}},P_{{23}},P_{{123}}$：

$$P_{{track}}=P_{{12}}+P_{{13}}+P_{{23}}-2P_{{123}}.$$

同时统计 $P(D_t=1|D_{{t-1}}=1)$、$P(D_t=1|D_{{t-1}}=0)$ 和二元 $\phi$ 相关系数。TrackManager Confirmed+matched_this_frame 只作为附录诊断，不替代本节 truth 事件。

![三屏选二实测与 iid 辅助线。](figures/{Path(figures['track']).name})

{markdown_table(["SCNR", "目标数", "实测2/3", "iid2/3", "非独立2/3", "P(D2|D1)", "P(D2|not D1)", "phi12"], track_table)}

# 6. 差异、损失和当前判断

## 6.1 三层差异

表和 CSV 给出每个角度的 A/B/C 曲线。一般情况下：A 只反映 GO 共享角点和门限；B 相对 A 的变化来自训练窗被目标支撑污染；C 相对 B 的变化来自真实 C+N 的非 IID 背景。目标泄漏右移是 B 或 C+leak 与对应无泄漏曲线的 0.5-Pd 交点差，不用输入 SNR 代替。

## 6.2 单元→cluster→target 损失

`target_layer_retention.csv` 同时给出 $P_{{cluster}}/P_{{unit}}$ 与 $P_{{target}}/P_{{unit}}$。这些比值只在同一 output-SCNR 分箱和同一事件分母中解释；当前 1-seed/少量屏幕的 0/1 不是精确概率。

## 6.3 三屏独立性

如果 $P(D_t=1|D_{{t-1}}=1)$ 与 $P(D_t=1|D_{{t-1}}=0)$ 相近、$\phi$ 接近 0 且非独立公式和 iid 公式差异小，可以把独立式用于辅助；否则报告应使用 pair/triple 公式。当前样本量仍小，结论只作为模型验证，不是最终 90% 门限。

# 7. 限制与下一步

1. 真实背景和 S-only 支撑均来自当前保留的 production diagnostics，未使用拍脑袋泄漏功率；但 C 层把真实四方向均值与 measured leakage template 组合成半解析增量，不能替代未来保留 complex CSI 的逐单元非中心背景模型。
2. 目标级旧 CSV 没有将 select/reloc/match 分开写出；要把四个条件概率都变成独立实测量，下一轮应在正式 clustering/target-selection/relocation 边界写入审计事件，而不是从最终输出反推。
3. 本轮没有扩大正式 multi-seed sweep；当前 evaluation 文件仍是小样本。只有在模型脚本、alpha/Pfa、支撑提取和三屏统计通过复核后，才适合运行 3–5 个独立 seed 的模型验证。
4. `stage2 target_snr_db` 只出现在输入控制变量/校准表，不作为主图横轴；主图四个面板全部使用 output SCNR。

# 8. 可复现入口和产物

```bash
python3 scripts/gmti_scnr_eval/14_build_go_three_layer_model.py \\
  --calibration-root outputs/gmti_scnr_eval/formal_calibration_pfa1e6_v2 \\
  --evaluation-metrics docs/GMTI_输出SCNR_仿真数据补充_20260826/current_one_seed_metrics_with_scnr.csv \\
  --evaluation-track docs/GMTI_输出SCNR_仿真数据补充_20260826/current_one_seed_track_with_scnr.csv \\
  --work-dir docs/GMTI_输出SCNR_三层GO理论模型_20260826
```

CSV、图、`summary.json`、运行清单和本报告 Markdown 全部位于 `work-dir`。本补充只消费已经审计并保留的诊断文件；它不会重新拟合 evaluation seed。
'''
    md = out / "三层GO理论模型与验证报告_20260826.md"
    md.write_text(report, encoding="utf-8")
    return md


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration-root", type=Path, required=True)
    parser.add_argument("--evaluation-metrics", type=Path, required=True)
    parser.add_argument("--evaluation-track", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument("--samples", type=int, default=120_000,
                        help="IID/noncentral MC samples per curve point")
    parser.add_argument("--windows-per-map", type=int, default=8_000)
    parser.add_argument("--scnr-min-db", type=float, default=-35.0)
    parser.add_argument("--scnr-max-db", type=float, default=40.0)
    parser.add_argument("--scnr-step-db", type=float, default=1.0)
    args = parser.parse_args()
    out = args.work_dir.resolve(); out.mkdir(parents=True, exist_ok=True)
    calib = args.calibration_root.resolve()
    for geometry_guard in (3, 4, 5):
        Geometry(geometry_guard).validate()
    templates = collect_leakage(calib, out)
    real_array = collect_real_windows(calib, out, args.windows_per_map, args.seed)
    windows = read_csv(out / "empirical_go_windows.csv")
    grid_db = np.arange(args.scnr_min_db, args.scnr_max_db + 0.5 * args.scnr_step_db, args.scnr_step_db)
    gamma = 10.0 ** (grid_db / 10.0)
    # Production alpha is a recorded runtime fact.  We still independently
    # MC-calibrate guard 3/5; guard 4 uses the sidecar value exactly.
    production_alpha = float("nan")
    for meta in calib.rglob("*_meta.txt"):
        candidate = fnum(read_kv(meta).get("cfar_alpha"))
        if math.isfinite(candidate) and candidate > 0.0:
            production_alpha = candidate; break
    if not math.isfinite(production_alpha):
        raise RuntimeError("calibration root has no production cfar_alpha sidecar")
    alpha_map: dict[int, float] = {}; pfa_map: dict[int, float] = {}
    iid_rows: list[dict[str, object]] = []
    for guard in (3, 4, 5):
        geometry = Geometry(guard)
        m = iid_m_samples(geometry, args.samples, args.seed + guard)
        alpha = production_alpha if guard == 4 else calibrate_alpha(m, 1.0e-6)
        alpha_map[guard] = alpha; pfa_map[guard] = float(np.mean(np.exp(-alpha * m)))
        no_leak = conditional_pd(gamma, alpha, m)
        for x, pd in zip(grid_db, no_leak):
            iid_rows.append({"model": "A_IID_no_leak", "angle_group": "all", "guard": guard,
                             "output_scnr_db": x, "pd_unit": pd, "alpha": alpha,
                             "sample_count": len(m)})
        for group, template in sorted(templates.items()):
            if guard == 4 and group == "0deg_center":
                pass
            # B uses the same leakage template for all guards; the block
            # geometry changes the block membership and hence ncp scaling.
            leak_pd = noncentral_pd(gamma, alpha, geometry, template.lambda_per_gamma,
                                    max(10_000, min(args.samples, 80_000)), args.seed + guard * 100 + int(template.angle_deg * 10))
            for x, pd in zip(grid_db, leak_pd):
                iid_rows.append({"model": "B_IID_leak", "angle_group": group, "guard": guard,
                                 "output_scnr_db": x, "pd_unit": pd, "alpha": alpha,
                                 "sample_count": max(10_000, min(args.samples, 80_000))})
    # Add a duplicated all-angle no-leak curve per group for plotting joins.
    for row in list(iid_rows):
        if row.get("model") == "A_IID_no_leak" and row.get("guard") == 4:
            for group in templates:
                iid_rows.append({**row, "angle_group": group})
    empirical_rows = empirical_curves(windows, templates, grid_db, alpha_map[4], out)
    write_csv(out / "iid_go_curves.csv", iid_rows)
    shift_rows = model_shift_summary(iid_rows, empirical_rows, templates.keys(), out)
    metrics = load_metric_rows(args.evaluation_metrics.resolve())
    target_rows = target_layers(metrics, out)
    track_rows = track_stats(args.evaluation_track.resolve(), out)
    figures = plot_outputs(out, iid_rows, empirical_rows, target_rows, track_rows, metrics)
    summary = {
        "calibration_root": str(calib), "evaluation_metrics": str(args.evaluation_metrics.resolve()),
        "evaluation_track": str(args.evaluation_track.resolve()), "output_dir": str(out),
        "pfa_configured": 1.0e-6, "alpha": alpha_map, "mc_pfa": pfa_map,
        "go_block_counts_guard4": Geometry(4).block_counts.tolist(),
        "real_window_count": len(windows), "leakage_templates": {g: {"sample_count": t.sample_count, "qualified_count": t.qualified_count,
            "lambda_per_gamma": t.lambda_per_gamma.tolist(), "direction_per_gamma": t.direction_per_gamma.tolist(), "source": t.reference} for g, t in templates.items()},
        "iid_samples_per_guard": args.samples, "scnr_axis_db": [float(grid_db[0]), float(grid_db[-1]), float(args.scnr_step_db)],
        "source_sha256": {str(path): sha256(path) for path in (args.evaluation_metrics, args.evaluation_track) if path.is_file()},
        "figures": figures,
        "target_layer_note": "select/reloc/match individual factors are not identifiable from retained target diagnostics; only their joint post-cluster retention is reported",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = write_report(out, args, alpha_map, pfa_map, templates, windows, iid_rows, empirical_rows, target_rows, track_rows, figures, metrics, shift_rows)
    print(json.dumps({"work_dir": str(out), "markdown": str(md), "figures": figures,
                      "alpha": alpha_map, "pfa_mc": pfa_map, "real_windows": len(windows),
                      "leakage_templates": {g: t.sample_count for g, t in templates.items()},
                      "target_rows": len(target_rows), "track_rows": len(track_rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
