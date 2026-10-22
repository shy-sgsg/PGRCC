#!/usr/bin/env python3
"""R7 tests for independent float64 GO-CFAR CPU references."""

from __future__ import annotations

import csv
import unittest
from pathlib import Path

import numpy as np

from scripts.phase_r.go_cfar_reference import (
    CfarGeometry,
    threshold_detail_direct_f64,
    threshold_detail_integral_f64,
    thresholds_direct_f64,
    thresholds_integral_f64,
)


ROOT = Path(__file__).resolve().parents[1]
PRE_FIX_DYNAMIC_RESULT = ROOT / "outputs/phase_r7_cfar_correctness_20261022/pre_fix_focused/branch_dynamic/result"
POWER_FILE = PRE_FIX_DYNAMIC_RESULT / "debug/cfar_GMTI01_beam050_power.f32"
THRESHOLD_FILE = PRE_FIX_DYNAMIC_RESULT / "debug/cfar_GMTI01_beam050_threshold.f32"
GEOMETRY_FILE = PRE_FIX_DYNAMIC_RESULT / "debug/cfar_geometry_diagnostics.csv"
HEIGHT, WIDTH = 130, 4096
GUARD, BACKGROUND = 4, 16
R6_CUT = (111, 3337)


def geometry(rows: int, cols: int, *, circular: bool = True, **overrides: int) -> CfarGeometry:
    values = {
        "height": rows,
        "width": cols,
        "guard_cells": 1,
        "background_cells": 2,
        "doppler_circular": circular,
    }
    values.update(overrides)
    return CfarGeometry(**values)


def production_alpha() -> float:
    with GEOMETRY_FILE.open(newline="", encoding="utf-8") as stream:
        rows = [row for row in csv.DictReader(stream) if row["period_id"] == "0"]
    if not rows:
        raise AssertionError("R6 Production geometry has no period-0 alpha record")
    values = {float(row["alpha"]) for row in rows}
    if len(values) != 1:
        raise AssertionError(f"period-0 Production alpha is ambiguous: {sorted(values)}")
    return values.pop()


def legacy_float32_threshold(power: np.ndarray, geom: CfarGeometry, alpha: float, row: int, col: int) -> float:
    """Reproduce the old CUDA float32 prefix arithmetic for one GO-CFAR CUT."""
    row_prefix = np.cumsum(power, axis=1, dtype=np.float32)
    integral = np.zeros((geom.height + 1, geom.width + 1), dtype=np.float32)
    integral[1:, 1:] = np.cumsum(row_prefix, axis=0, dtype=np.float32)

    def rect(r0: int, r1: int, c0: int, c1: int) -> np.float32:
        return (
            integral[r1 + 1, c1 + 1]
            - integral[r0, c1 + 1]
            - integral[r1 + 1, c0]
            + integral[r0, c0]
        )

    def doppler_rect(r0: int, r1: int, c0: int, c1: int) -> np.float32:
        if not geom.doppler_circular:
            return rect(r0, r1, c0, c1)
        while r0 < 0:
            r0 += geom.height
            r1 += geom.height
        while r0 >= geom.height:
            r0 -= geom.height
            r1 -= geom.height
        if r1 < geom.height:
            return rect(r0, r1, c0, c1)
        return rect(r0, geom.height - 1, c0, c1) + rect(0, r1 - geom.height, c0, c1)

    guard, radius = geom.guard_cells, geom.radius
    r0, r1 = row - radius, row + radius
    c0, c1 = col - radius, col + radius
    row_guard0, row_guard1 = row - guard, row + guard
    col_guard0, col_guard1 = col - guard, col + guard
    counts = (geom.window_size * geom.background_cells,) * 4
    sums = (
        doppler_rect(r0, r1, c0, col_guard0 - 1),
        doppler_rect(r0, r1, col_guard1 + 1, c1),
        doppler_rect(r0, row_guard0 - 1, c0, c1),
        doppler_rect(row_guard1 + 1, r1, c0, c1),
    )
    means = tuple(value / count for value, count in zip(sums, counts))
    return float(np.float32(alpha) * max(means))


class TestGoCfarFloat64Reference(unittest.TestCase):
    def test_integral_reports_direct_fallback_for_ill_conditioned_rectangles(self) -> None:
        power = np.full((32, 64), 1.0e-3, dtype=np.float32)
        power[15, 31] = np.float32(1.0e8)
        result = thresholds_integral_f64(power, geometry(32, 64), 13.0)
        self.assertGreater(result.direct_fallback_count, 0)

    def test_direct_and_integral_references_agree_on_required_power_patterns(self) -> None:
        rows, cols = 32, 64
        geom = geometry(rows, cols)
        rng = np.random.default_rng(101)
        correlated = np.add.outer(np.linspace(0.01, 0.2, rows), np.linspace(0.02, 0.8, cols))
        maps = {
            "uniform_exponential_mean": rng.exponential(1.0, (rows, cols)).astype(np.float32),
            "high_dynamic_range": np.full((rows, cols), 1.0e-3, dtype=np.float32),
            "weak_background_strong_target": np.full((rows, cols), 0.025, dtype=np.float32),
            "sparse_target_only": np.zeros((rows, cols), dtype=np.float32),
            "nonuniform_correlated": correlated.astype(np.float32),
        }
        maps["high_dynamic_range"][15, 31] = np.float32(1.0e8)
        maps["weak_background_strong_target"][15, 31] = np.float32(1.0e7)
        maps["sparse_target_only"][15, 31] = np.float32(1.0e5)

        for name, power in maps.items():
            with self.subTest(pattern=name):
                direct = thresholds_direct_f64(power, geom, 13.0, detail_cuts=((0, 32), (15, 31)))
                integral = thresholds_integral_f64(power, geom, 13.0, detail_cuts=((0, 32), (15, 31)))
                mask = direct.geometry_valid
                np.testing.assert_allclose(direct.noise_level[mask], integral.noise_level[mask], rtol=1.0e-8, atol=1.0e-12)
                np.testing.assert_allclose(direct.threshold[mask], integral.threshold[mask], rtol=1.0e-8, atol=1.0e-12)
                self.assertEqual(direct.details[(0, 32)].window_bounds[0], (-3, 3, 29, 30))

    def test_true_zero_training_windows_remain_exact_zero(self) -> None:
        power = np.zeros((32, 64), dtype=np.float32)
        power[15, 5] = np.float32(1.0e7)
        geom = geometry(32, 64)
        direct = threshold_detail_direct_f64(power, geom, 13.0, 15, 32)
        integral = threshold_detail_integral_f64(power, geom, 13.0, 15, 32)
        self.assertEqual(direct.directional_sums, (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(integral.directional_sums, (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(direct.threshold, 0.0)
        self.assertEqual(integral.threshold, 0.0)

    def test_non_circular_edges_range_edges_exclusion_and_cut_band_masks(self) -> None:
        power = np.ones((24, 40), dtype=np.float32)
        geom = CfarGeometry(
            height=24,
            width=40,
            guard_cells=1,
            background_cells=2,
            doppler_circular=False,
            exclude_row_start=10,
            exclude_row_end=10,
            cut_band_start=8,
            cut_band_end=12,
            cut_band_mode=1,
        )
        result = thresholds_integral_f64(power, geom, 13.0, detail_cuts=((0, 20), (10, 20), (9, 0), (9, 20), (15, 20)))
        self.assertFalse(result.geometry_valid[0, 20])
        self.assertFalse(result.geometry_valid[9, 0])
        self.assertFalse(result.valid_cut[10, 20])  # excluded despite lying in the enabled band
        self.assertTrue(result.valid_cut[9, 20])
        self.assertFalse(result.valid_cut[15, 20])  # outside the configured cut band
        self.assertEqual(result.details[(10, 20)].threshold, 13.0)

    @unittest.skipUnless(POWER_FILE.is_file() and THRESHOLD_FILE.is_file(), "R6 Production map fixture unavailable")
    def test_r6_dynamic_branch_negative_threshold_is_reproduced_by_legacy_float32_and_fixed_references(self) -> None:
        power = np.fromfile(POWER_FILE, dtype="<f4").reshape(HEIGHT, WIDTH)
        old_threshold = np.fromfile(THRESHOLD_FILE, dtype="<f4").reshape(HEIGHT, WIDTH)
        geom = CfarGeometry(HEIGHT, WIDTH, GUARD, BACKGROUND, True)
        row, col = R6_CUT
        alpha = production_alpha()

        self.assertAlmostEqual(float(power[row, col]), 17.0784569, delta=1.0e-5)
        self.assertAlmostEqual(float(old_threshold[row, col]), -2.6239653, delta=1.0e-5)
        self.assertEqual(legacy_float32_threshold(power, geom, alpha, row, col), float(old_threshold[row, col]))
        direct = threshold_detail_direct_f64(power, geom, alpha, row, col)
        integral = threshold_detail_integral_f64(power, geom, alpha, row, col)
        np.testing.assert_allclose(direct.directional_means, integral.directional_means, rtol=1.0e-8, atol=1.0e-12)
        self.assertAlmostEqual(direct.threshold, 114.8018334, delta=5.0e-4)
        self.assertGreater(direct.threshold, float(power[row, col]))
        self.assertLess(float(old_threshold[row, col]), 0.0)
        self.assertGreater(abs(float(old_threshold[row, col]) - direct.threshold), 100.0)

    @unittest.skipUnless(POWER_FILE.is_file(), "R6 Production power-map fixture unavailable")
    def test_r6_saved_map_direct_and_integral_references_agree_at_every_valid_cut(self) -> None:
        power = np.fromfile(POWER_FILE, dtype="<f4").reshape(HEIGHT, WIDTH)
        geom = CfarGeometry(HEIGHT, WIDTH, GUARD, BACKGROUND, True)
        alpha = production_alpha()
        direct = thresholds_direct_f64(power, geom, alpha)
        integral = thresholds_integral_f64(power, geom, alpha)
        valid = direct.geometry_valid
        np.testing.assert_allclose(direct.noise_level[valid], integral.noise_level[valid], rtol=1.0e-8, atol=1.0e-12)
        np.testing.assert_allclose(direct.threshold[valid], integral.threshold[valid], rtol=1.0e-8, atol=1.0e-12)


if __name__ == "__main__":
    unittest.main()
