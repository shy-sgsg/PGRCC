#!/usr/bin/env python3
"""共享的 GMTI 输出 SCNR 专项工具。

本模块刻意只实现当前生产二维 GO-CFAR 的统计模型：四个方向训练带
的均值取最大值，门限系数沿用 ``dpca_cfar2_fast[_cuda]`` 的实现。
训练单元的重叠相关性按源码窗口几何保留。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from scipy.stats import ncx2


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, rows: Iterable[dict]) -> None:
    materialized = list(rows)
    fields: list[str] = []
    for row in materialized:
        for key in row:
            if key not in fields:
                fields.append(key)
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(materialized)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def target_theory_axis_offset_db(target_theory_dir: Path, angle_deg: float) -> float:
    """Map the legacy 15-cell PB SCNR coordinate to the production 3x3 axis.

    ``17_build_target_level_theory.py`` defines its gamma as the total signal
    in the measured 3x5 support divided by one representative cell
    background.  The formal MC axis instead uses the fixed 3x3 support sum
    divided by the same support's C+N ensemble power.  The measured profile
    gives the conversion without a fitted parameter:

        gamma_3x3 = gamma_3x5 * sum(kappa_3x3) / sum(beta_3x3).

    ``beta`` is normalized to the median per-cell background, so the ratio
    also carries the factor of roughly nine cells in the production support.
    A missing profile is a hard modelling limitation; returning zero keeps
    old callers usable while the caller can record that no mapping was
    available.
    """
    groups = {0.0: "0deg_center", 30.0: "30deg_center", 45.0: "45deg_center"}
    group = groups.get(float(angle_deg))
    if group is None:
        return 0.0
    profile_path = Path(target_theory_dir) / "target_support_profiles.csv"
    try:
        rows = read_csv(profile_path)
    except (OSError, ValueError):
        return 0.0
    profile = next((row for row in rows if row.get("angle_group") == group), None)
    if profile is None:
        return 0.0
    try:
        # The production output support is Doppler offsets -1..1 and range
        # offsets -1..1 inside the 3x5 target-theory footprint.
        indices = [5 * row + col for row in range(3) for col in range(1, 4)]
        kappa = [float(profile[f"kappa_{index:02d}"]) for index in indices]
        beta = [float(profile[f"beta_{index:02d}"]) for index in indices]
        numerator = float(np.sum(kappa)); denominator = float(np.sum(beta))
        if not (math.isfinite(numerator) and math.isfinite(denominator)
                and numerator > 0.0 and denominator > 0.0):
            return 0.0
        return linear_to_db(numerator / denominator)
    except (KeyError, TypeError, ValueError):
        return 0.0


def remap_target_theory_rows(rows: Iterable[dict[str, str]], target_theory_dir: Path,
                             angle_deg: float) -> list[dict[str, str]]:
    """Return target PB rows on the formal fixed-support output-SCNR axis."""
    offset_db = target_theory_axis_offset_db(target_theory_dir, angle_deg)
    mapped: list[dict[str, str]] = []
    for row in rows:
        item = dict(row)
        try:
            value = float(row.get("output_scnr_db", "nan"))
            if math.isfinite(value):
                item["output_scnr_db"] = f"{value + offset_db:.12g}"
        except (TypeError, ValueError):
            pass
        item["target_theory_axis_offset_db"] = f"{offset_db:.12g}"
        item["target_theory_axis_definition"] = "fixed_3x3_output_support_scnr"
        mapped.append(item)
    return mapped


def production_calibration_curve_rows(calibration_dir: Path, angle_deg: float) -> list[dict[str, str]]:
    """Locate the independent production target/track calibration curve."""
    root = Path(calibration_dir)
    candidates: list[Path] = []
    manifest_path = root / "calibration_manifest.json"
    if manifest_path.is_file():
        try:
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            for item in document.get("angles", []):
                try:
                    if abs(float(item.get("angle_deg")) - float(angle_deg)) < 1e-6:
                        angle_dir = item.get("angle_dir")
                        if angle_dir:
                            candidates.append(Path(str(angle_dir)) / "standard_mc_curve_summary.csv")
                except (TypeError, ValueError):
                    continue
        except (OSError, ValueError, TypeError):
            pass
    angle_token = f"angle_p{int(round(float(angle_deg)))}p000"
    candidates.extend([
        root / "calibration" / angle_token / "standard_mc_curve_summary.csv",
        root / angle_token / "standard_mc_curve_summary.csv",
        root.parent / f"calibration_angle{int(round(float(angle_deg)))}_fine_t6" / "calibration" / angle_token / "standard_mc_curve_summary.csv",
    ])
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    return read_csv(path) if path is not None else []


def parse_grid(text: str) -> list[float]:
    values = [float(token.strip()) for token in text.split(",") if token.strip()]
    if not values:
        raise ValueError("SCNR 网格不能为空")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("SCNR 网格包含非有限数")
    return sorted(set(values))


def db_to_linear(value_db: float) -> float:
    return 10.0 ** (value_db / 10.0)


def linear_to_db(value: float) -> float:
    return 10.0 * math.log10(value) if value > 0.0 else float("-inf")


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return float("nan"), float("nan")
    p = successes / total
    denom = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denom
    radius = z * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total)) / denom
    return max(0.0, center - radius), min(1.0, center + radius)


@dataclass(frozen=True)
class GoCfarGeometry:
    """与 ``cfar_detect_kernel`` 相同的二维 GO-CFAR 窗口几何。"""

    guard_half_width: int
    background_thickness: int

    @property
    def outer_half_width(self) -> int:
        return self.guard_half_width + self.background_thickness

    @property
    def outer_width(self) -> int:
        return 2 * self.outer_half_width + 1

    @property
    def guard_width(self) -> int:
        return 2 * self.guard_half_width + 1

    @property
    def code_total_background_cells(self) -> int:
        # 这是源码 alpha 所用的“环”总单元数；GO 四方向条带本身有角点重叠。
        return self.outer_width * self.outer_width - self.guard_width * self.guard_width

    @property
    def directional_strip_cells(self) -> int:
        return self.background_thickness * self.outer_width

    @property
    def alpha_cell_count(self) -> int:
        return self.code_total_background_cells

    def alpha(self, configured_pfa: float) -> float:
        if not 0.0 < configured_pfa < 1.0:
            raise ValueError("PFA 必须位于 (0, 1)")
        n = self.alpha_cell_count
        return n * (configured_pfa ** (-1.0 / n) - 1.0)


def _go_directional_means(geometry: GoCfarGeometry, samples: int,
                          rng: np.random.Generator) -> np.ndarray:
    """生成实际 GO 四个条带的最大均值（背景功率已归一化为 1）。

    不是把四个方向当独立 Gamma 变量。通过八个互不重叠的矩形块重建四条带，
    保留四个角块在横向和纵向条带中的相关性，严格对应 GPU/CPU 的窗口求和。
    """
    g = geometry.guard_width
    b = geometry.background_thickness
    w = geometry.outer_width
    if samples <= 0 or b <= 0:
        raise ValueError("GO-CFAR 样本数和背景带厚度必须为正")
    # TL, TC, TR, LC, RC, BL, BC, BR；每项为指数功率之和。
    shapes = np.asarray([b * b, b * g, b * b, g * b, g * b, b * b, b * g, b * b])
    blocks = rng.gamma(shape=shapes, scale=1.0, size=(samples, 8))
    tl, tc, tr, lc, rc, bl, bc, br = (blocks[:, index] for index in range(8))
    denom = float(b * w)
    left = (tl + lc + bl) / denom
    right = (tr + rc + br) / denom
    top = (tl + tc + tr) / denom
    bottom = (bl + bc + br) / denom
    return np.maximum(np.maximum(left, right), np.maximum(top, bottom))


def go_training_noise_samples(geometry: GoCfarGeometry, samples: int, seed: int,
                              batch_size: int = 100_000) -> np.ndarray:
    rng = np.random.default_rng(seed)
    chunks: list[np.ndarray] = []
    remaining = samples
    while remaining:
        count = min(remaining, batch_size)
        chunks.append(_go_directional_means(geometry, count, rng))
        remaining -= count
    return np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float64)


def fixed_pd(scnr_linear: np.ndarray | float, pfa: float) -> np.ndarray:
    """确定复幅度目标、已知背景功率的平方律检测概率。"""
    gamma = np.asarray(scnr_linear, dtype=np.float64)
    eta = -math.log(pfa)
    return ncx2.sf(2.0 * eta, 2, 2.0 * gamma)


def go_conditional_pd(scnr_linear: np.ndarray | float, alpha: float,
                      noise_samples: np.ndarray, batch_size: int = 20_000) -> np.ndarray:
    """GO-CFAR 条件 Monte Carlo 积分。

    对每个训练窗 ``m=max(mean_left,...,mean_bottom)``，CUT 条件检测概率
    是 ``Q1(sqrt(2 gamma), sqrt(2 alpha m))``。这里采用非中心卡方 survival
    function 精确计算条件项，仅对生产 GO 窗口的随机训练背景作积分。
    """
    gamma = np.atleast_1d(np.asarray(scnr_linear, dtype=np.float64))
    if noise_samples.size == 0:
        raise ValueError("GO-CFAR 训练窗样本为空")
    total = np.zeros(gamma.shape, dtype=np.float64)
    for offset in range(0, noise_samples.size, batch_size):
        noise = noise_samples[offset:offset + batch_size]
        total += np.sum(ncx2.sf(2.0 * alpha * noise[:, None], 2,
                                2.0 * gamma[None, :]), axis=0)
    return total / noise_samples.size


def go_direct_pd(scnr_linear: Sequence[float], alpha: float,
                 noise_samples: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """直接生成复高斯 CUT 的 GO-CFAR 检测 Monte Carlo。"""
    rng = np.random.default_rng(seed)
    gamma = np.asarray(scnr_linear, dtype=np.float64)
    n = noise_samples.size
    standard = (rng.standard_normal((n, gamma.size)) +
                1j * rng.standard_normal((n, gamma.size))) / math.sqrt(2.0)
    received = standard + np.sqrt(gamma)[None, :]
    hits = np.abs(received) ** 2 > alpha * noise_samples[:, None]
    counts = hits.sum(axis=0, dtype=np.int64)
    pd = counts / n
    intervals = np.asarray([wilson_interval(int(count), n) for count in counts])
    return pd, intervals[:, 0], intervals[:, 1]


def go_configured_pfa(alpha: float, noise_samples: np.ndarray) -> tuple[float, float]:
    """利用 P(CUT>T|训练窗)=exp(-T) 的条件 MC 估计真实 GO 虚警率。"""
    values = np.exp(-alpha * noise_samples)
    mean = float(np.mean(values))
    standard_error = float(np.std(values, ddof=1) / math.sqrt(values.size)) if values.size > 1 else float("nan")
    return mean, standard_error


def first_crossing(x: Sequence[float], y: Sequence[float], target: float,
                   direction: str) -> float | None:
    """线性插值给出首次达标点；无交点返回 None。"""
    if len(x) != len(y):
        raise ValueError("x/y 长度不一致")
    for index, value in enumerate(y):
        ok = value <= target if direction == "below" else value >= target
        if not ok:
            continue
        if index == 0:
            return float(x[0])
        x0, x1 = float(x[index - 1]), float(x[index])
        y0, y1 = float(y[index - 1]), float(y[index])
        if y1 == y0:
            return x1
        fraction = (target - y0) / (y1 - y0)
        return x0 + fraction * (x1 - x0)
    return None
