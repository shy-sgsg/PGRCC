#!/usr/bin/env python3
"""Regression tests for production evaluation truth assignment."""

import itertools
from pathlib import Path
import random
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from gmti_eval_match import min_cost_assignment, one_to_one_match  # noqa: E402


class EvalMatchTests(unittest.TestCase):
    def test_hungarian_matches_bruteforce_for_small_rectangles(self):
        for nrows in range(1, 5):
            for ncols in range(1, 6):
                for seed in range(10):
                    rng = random.Random(seed + 100 * nrows + ncols)
                    cost = [
                        [rng.randint(0, 99) for _ in range(ncols)]
                        for _ in range(nrows)
                    ]
                    assignment = min_cost_assignment(cost)
                    actual = sum(cost[row][col]
                                 for row, col in assignment.items())
                    if nrows <= ncols:
                        expected = min(
                            sum(cost[row][cols[row]]
                                for row in range(nrows))
                            for cols in itertools.permutations(
                                range(ncols), nrows)
                        )
                    else:
                        expected = min(
                            sum(cost[rows[col]][col]
                                for col in range(ncols))
                            for rows in itertools.permutations(
                                range(nrows), ncols)
                        )
                    self.assertEqual(len(assignment), min(nrows, ncols))
                    self.assertEqual(actual, expected)

    def test_more_than_24_targets_are_not_pruned(self):
        n_truth = 64
        n_detection = 80
        cost = [[1000.0] * n_detection for _ in range(n_truth)]
        for row in range(n_truth):
            cost[row][(37 * row) % n_detection] = float(row % 7)

        assignment = min_cost_assignment(cost)

        self.assertEqual(len(assignment), n_truth)
        self.assertTrue(all(cost[row][col] < 1000.0
                            for row, col in assignment.items()))

    def test_invalid_pairs_remain_one_to_one(self):
        cost = [
            [0.1, 1e9, 1e9],
            [0.2, 1e9, 1e9],
            [1e9, 0.3, 1e9],
        ]
        assignment = min_cost_assignment(cost)
        self.assertEqual(len(set(assignment.values())), len(assignment))

    def test_mechanical_match_uses_scan_and_nearest_cpi_angle_without_beam_gate(self):
        cfg = {
            "max_period_diff": 0,
            "max_beam_diff": 0,
            "max_time_diff_s": 0.2,
            "max_range_error_m": 50.0,
            "max_position_error_m": 50.0,
            "max_velocity_error_mps": 5.0,
        }
        truth = [{
            "target_id": "T1", "scan_id": "4", "window_id": "0",
            "utc_center": "10.0", "range_m": "1000.0",
            "target_azimuth_deg": "0.0", "radial_velocity_mps": "2.0",
            "beam_id": "99",
        }]
        detections = [
            {"id": "far", "scan_mode": "mechanical", "scan_id": "4",
             "center_utc": "10.0", "range_m": "1000.0",
             "radial_velocity_mps": "2.0", "reference_azimuth_deg": "-1.0",
             "beam_id": "-1"},
            {"id": "near", "scan_mode": "mechanical", "scan_id": "4",
             "center_utc": "10.0", "range_m": "1000.0",
             "radial_velocity_mps": "2.0", "reference_azimuth_deg": "0.1",
             "beam_id": "-1"},
        ]
        matches, _, _ = one_to_one_match(
            detections, truth, cfg, "id", scan_mode="mechanical")
        self.assertEqual(matches[0]["item_id"], "near")

        detections[1]["scan_id"] = "5"
        matches, _, _ = one_to_one_match(
            detections, truth, cfg, "id", scan_mode="mechanical")
        self.assertEqual(matches[0]["item_id"], "far")


if __name__ == "__main__":
    unittest.main()
