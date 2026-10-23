import unittest
import math
from pathlib import Path
from unittest.mock import patch

import numpy as np

from scripts.phase_r.evaluate_r8_multiseed_causal_pilot import (
    classify_target_loss_stage,
    target_neighborhood_metrics,
    target_neighborhood_slices,
    _truth_path,
    _metrics_row,
    _normalize_csv_line_endings,
    _write_comparison_figure,
    normalized_runtime_xml,
)


class TargetNeighborhoodTests(unittest.TestCase):
    def test_csv_line_ending_normalizer_handles_nested_evidence_csv(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            nested = Path(temp_dir) / "seed202" / "associations.csv"
            nested.parent.mkdir()
            nested.write_bytes(b"id,value\r\n1,2\r\n")
            _normalize_csv_line_endings(Path(temp_dir))
            self.assertEqual(nested.read_bytes(), b"id,value\n1,2\n")

    def test_metrics_row_records_f1_f2_input_power_for_each_role(self):
        csi = {
            "target_transfer": {"target_only_csi_retention_db": -5.25},
            "input_fusion_power": {
                "ON": {"F1_mean_power": 10.0, "F2_mean_power": 11.0},
                "OFF": {"F1_mean_power": 7.0, "F2_mean_power": 8.0},
                "TO": {"F1_mean_power": 2.0, "F2_mean_power": 3.0},
            }
        }
        roles = {
            "ON": {"metrics": {
                "parameter_errors": {},
                "misses": 2,
                "false_associations": 4,
                "correct_track_associations": 1,
                "false_track_associations": 3,
                "cfar_branch_hit_cells": 100,
                "final_detections": 7,
                "confirmed_tracks": 4,
            }},
            "OFF": {"metrics": {"cfar_branch_hit_cells": 50}},
        }
        result = _metrics_row(202, "A", csi, roles)
        self.assertEqual(result["input_f1_mean_power_on"], 10.0)
        self.assertEqual(result["input_f2_mean_power_off"], 8.0)
        self.assertEqual(result["input_f1_mean_power_to"], 2.0)
        self.assertEqual(result["target_only_csi_retention_db"], -5.25)
        self.assertEqual(result["target_misses"], 2)
        self.assertEqual(result["on_non_target_detections"], 4)
        self.assertEqual(result["correct_track_associations"], 1)
        self.assertEqual(result["unassociated_confirmed_tracks"], 3)
        self.assertEqual(result["cfar_branch_hit_cells_on"], 100)
        self.assertEqual(result["cfar_branch_hit_cells_off"], 50)
        self.assertEqual(result["on_final_detections"], 7)
        self.assertEqual(result["on_confirmed_tracks"], 4)

    def test_comparison_figure_keeps_unavailable_condition_as_gap(self):
        import matplotlib.pyplot  # noqa: F401
        import tempfile
        from matplotlib.axes import Axes

        rows = [
            {"seed": 202, "condition": "A", "angle_rmse_deg": 1.0},
            {"seed": 202, "condition": "B", "angle_rmse_deg": None},
            {"seed": 202, "condition": "C", "angle_rmse_deg": 3.0},
        ]
        original_plot = Axes.plot
        plot_calls = []

        def capture_plot(axis, *args, **kwargs):
            plot_calls.append((args, kwargs))
            return original_plot(axis, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("matplotlib.axes.Axes.plot", new=capture_plot):
                _write_comparison_figure(Path(temp_dir) / "unused.png", rows)
        angle_series = [
            args[1]
            for args, _kwargs in plot_calls
            if len(args) > 1 and len(args[1]) == 3
            and args[1][0] == 1.0 and args[1][2] == 3.0
        ]
        self.assertTrue(angle_series)
        self.assertTrue(math.isnan(angle_series[0][1]))

    def test_runtime_xml_comparison_ignores_only_run_output_paths(self):
        base = "<root><result_add>/run/A/result</result_add><threshold>13.4</threshold><track_debug_dir>/run/A/track</track_debug_dir></root>"
        paired = "<root><result_add>/run/B/result</result_add><threshold>13.4</threshold><track_debug_dir>/run/B/track</track_debug_dir></root>"
        changed_operator = "<root><result_add>/run/B/result</result_add><threshold>14.0</threshold><track_debug_dir>/run/B/track</track_debug_dir></root>"
        self.assertEqual(normalized_runtime_xml(base), normalized_runtime_xml(paired))
        self.assertNotEqual(normalized_runtime_xml(base), normalized_runtime_xml(changed_operator))

    def test_known_correction_reuses_injected_error_scene_truth(self):
        case = {"simulation": {
            "A0_ON": {"output_dir": "/case/scenes/A0_ON"},
            "A1_ON": {"output_dir": "/case/scenes/A1_ON"},
        }}
        self.assertEqual(
            _truth_path(case, "A2_Known_error_correction_upper_bound"),
            Path("/case/scenes/A1_ON/truth/truth_targets_by_beam.csv"),
        )

    def test_doppler_neighborhood_wraps_and_range_neighborhood_clamps(self):
        rows, cols = target_neighborhood_slices(
            center_row=0, center_col=0, row_count=6, col_count=4,
            half_doppler=1, half_range=1,
        )
        self.assertEqual(rows, [0, 1, 5])
        self.assertEqual(cols, [0, 1])

    def test_metrics_count_hits_and_best_signed_margin(self):
        power = np.ones((3, 3), dtype=np.float32)
        threshold = np.full((3, 3), 2.0, dtype=np.float32)
        hits = np.zeros((3, 3), dtype=np.float32)
        power[1, 1] = 4.0
        hits[1, 1] = 1.0
        result = target_neighborhood_metrics(
            power, threshold, hits, center_row=1, center_col=1,
            half_doppler=1, half_range=1,
        )
        self.assertEqual(result["valid_cut_count"], 9)
        self.assertEqual(result["hit_count"], 1)
        self.assertAlmostEqual(result["best_margin_db"], 10.0 * np.log10(2.0))

    def test_loss_stage_uses_each_production_stage(self):
        self.assertEqual(classify_target_loss_stage(0, 0, False, False), "no_target_local_cfar_hit")
        self.assertEqual(classify_target_loss_stage(1, 0, False, False), "after_cfar_before_local_cluster")
        self.assertEqual(classify_target_loss_stage(1, 1, False, False), "cluster_to_detection_or_target_association")
        self.assertEqual(classify_target_loss_stage(1, 0, False, False, True), "nearest_cluster_or_detection_outside_frozen_target_gate")
        self.assertEqual(classify_target_loss_stage(1, 1, True, False), "detection_to_track_association")
        self.assertEqual(classify_target_loss_stage(1, 1, True, True), "confirmed_track_match")


if __name__ == "__main__":
    unittest.main()
