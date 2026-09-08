#!/usr/bin/env python3
"""Regression test for confirmed-current-frame track truth evaluation."""

from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
EVALUATOR = (REPO / "docs/功能测试与实时性测试/GMTI完整测试手册与脚本_20260729"
             / "tools/evaluate_track_outputs.py")


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class EvaluateTrackOutputsTests(unittest.TestCase):
    def test_target_level_two_of_three_counts_each_target_once(self):
        spec = importlib.util.spec_from_file_location("evaluate_track_outputs", EVALUATOR)
        evaluator = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(evaluator)

        truths = [
            {"target_id": "A", "period_id": "0"},
            {"target_id": "A", "period_id": "1"},
            {"target_id": "A", "period_id": "2"},
            {"target_id": "B", "period_id": "0"},
            {"target_id": "B", "period_id": "1"},
            {"target_id": "B", "period_id": "2"},
        ]
        matches = [
            {"truth_index": 0}, {"truth_index": 2}, {"truth_index": 3},
        ]
        rows, target_count, detected_count = evaluator.target_level_two_of_three(
            matches, truths, {0, 1, 2})

        self.assertEqual(target_count, 2)
        self.assertEqual(detected_count, 1)
        self.assertEqual(rows[0]["target_id"], "A")
        self.assertEqual(rows[0]["matched_screen_count"], "2")
        self.assertEqual(rows[0]["detected"], "1")
        self.assertEqual(rows[1]["matched_screen_count"], "1")
        self.assertEqual(rows[1]["detected"], "0")

    def test_confirmed_current_measurement_does_not_add_raw_range_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            debug = root / "track_debug"
            debug.mkdir()
            write_csv(debug / "track_states.csv",
                      ["result_id", "track_id", "state", "matched_this_frame",
                       "matched_det_index", "is_output"],
                      [{"result_id": "2", "track_id": "7", "state": "Confirmed",
                        "matched_this_frame": "1", "matched_det_index": "3", "is_output": "1"}])
            write_csv(debug / "track_detections.csv",
                      ["result_id", "det_index", "matched", "matched_track_id",
                       "e", "n", "utc", "range", "direction"],
                      [{"result_id": "2", "det_index": "3", "matched": "1",
                        "matched_track_id": "7", "e": "1000", "n": "2000",
                        "utc": "7.0", "range": "81000", "direction": "0"}])
            write_csv(debug / "track_output_payloads.csv",
                      ["result_id", "track_id", "matched_det_index"],
                      [{"result_id": "GMTI02", "track_id": "7", "matched_det_index": "3"}])
            write_csv(debug / "track_frames.csv", ["result_id", "num_outputs"],
                      [{"result_id": "2", "num_outputs": "1"}])
            write_csv(root / "detection_results_GMTI02.csv", ["range_m"],
                      [{"range_m": "90000"} for _ in range(4)])
            truth = root / "truth.csv"
            write_csv(truth,
                      ["target_id", "period_id", "beam_id", "range_bin", "visible",
                       "utc_mid", "e_mid", "n_mid", "range_m", "vr_self_mps"],
                      [{"target_id": "target", "period_id": "1", "beam_id": "1",
                        "range_bin": "10", "visible": "1", "utc_mid": "7.0",
                        "e_mid": "1000", "n_mid": "2000", "range_m": "90000",
                        "vr_self_mps": "4.1"}])
            cfg = root / "match.json"
            cfg.write_text(json.dumps({"max_period_diff": 0, "max_beam_diff": 1,
                                       "max_time_diff_s": 0.1, "max_range_error_m": 100,
                                       "max_position_error_m": 10,
                                       "max_velocity_error_mps": 50}), encoding="utf-8")
            report = root / "report"
            command = [sys.executable, str(EVALUATOR), "--debug-dir", str(debug),
                       "--run-dir", str(root),
                            "--truth", str(truth), "--match-config", str(cfg),
                            "--report-dir", str(report), "--result-ids", "2"]
            subprocess.run(command, check=True, cwd=REPO)
            summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["track_detection"]["matched_count"], 1)
            self.assertNotIn("false_alarm_count", summary["track_detection"])
            self.assertFalse(summary["input"]["range_gate_enabled"])
            self.assertIsNone(summary["input"]["range_gate_m"])
            self.assertEqual(summary["input"]["range_source"],
                             "associated_raw_detection.range_m")
            self.assertAlmostEqual(
                summary["minimum_detectable_speed"]["truth_speed_kmh"], 14.76)
            self.assertTrue(summary["minimum_detectable_speed"]["passed"])

            write_csv(root / "detection_results_GMTI02.csv", ["range_m"],
                      [{"range_m": "90000"} for _ in range(3)] +
                      [{"range_m": "90101"}])
            no_range_gate_report = root / "no_range_gate_report"
            rejected = subprocess.run(
                [*command[:-4], "--report-dir", str(no_range_gate_report), "--result-ids", "2"],
                check=False, cwd=REPO)
            self.assertEqual(rejected.returncode, 0)
            rejected_summary = json.loads(
                (no_range_gate_report / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(rejected_summary["track_detection"]["matched_count"], 1)


if __name__ == "__main__":
    unittest.main()
