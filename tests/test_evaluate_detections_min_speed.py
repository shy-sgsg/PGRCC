#!/usr/bin/env python3
"""Regression tests for v1.6 detection, SCNR and localization metrics."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
EVALUATOR = (REPO / "docs/功能测试与实时性测试/GMTI完整测试手册与脚本_20260729"
             / "tools/evaluate_detections.py")


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class EvaluateDetectionsV16Tests(unittest.TestCase):
    def test_pd_requires_output_scnr_and_localization_uses_rmse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "run"
            run_dir.mkdir()
            fields = ["period_id", "beam_id", "new_e", "new_n", "range_m",
                      "theta_used_deg"]
            for period in range(9):
                write_csv(run_dir / f"detection_results_GMTI{period + 1:02d}.csv", fields,
                          [{"period_id": str(period), "beam_id": "1",
                            "new_e": "1000", "new_n": "2000", "range_m": "90060",
                            "theta_used_deg": "-40.4"}])
            write_csv(run_dir / "detection_results_GMTI10.csv", fields, [])
            truth = root / "truth.csv"
            truth_rows = [{"target_id": "target", "period_id": str(period), "beam_id": "1",
                           "range_bin": "1", "visible": "1", "e_mid": "1000", "n_mid": "2000",
                           "range_m": "90000", "azimuth_deg": "139.5"}
                          for period in range(10)]
            write_csv(truth, list(truth_rows[0]), truth_rows)
            cfg = root / "match.json"
            cfg.write_text(json.dumps({"max_period_diff": 0, "max_beam_diff": 0,
                                       "max_time_diff_s": 1, "max_range_error_m": 100,
                                       "max_position_error_m": 10,
                                       "max_velocity_error_mps": 50}), encoding="utf-8")
            scnr = root / "scnr.json"
            scnr.write_text(json.dumps({"target_scnr_db": 12.0, "tolerance_db": 0.5,
                                        "sample_count": 10, "expected_sample_count": 10,
                                        "all_samples_within_tolerance": True}), encoding="utf-8")
            report = root / "report"
            subprocess.run([sys.executable, str(EVALUATOR), "--run-dir", str(run_dir),
                            "--truth", str(truth), "--match-config", str(cfg),
                            "--report-dir", str(report), "--acceptance-pd", "0.9",
                            "--output-scnr-summary", str(scnr), "--require-output-scnr"],
                           check=True, cwd=REPO)
            summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
            self.assertAlmostEqual(summary["detection"]["detection_probability"], 0.9)
            self.assertTrue(summary["detection"]["passed"])
            self.assertNotIn("false_alarm_count", summary["detection"])
            self.assertAlmostEqual(summary["localization"]["range_abs_error_m"]["rmse"], 60.0)
            self.assertAlmostEqual(summary["localization"]["azimuth_abs_error_deg"]["rmse"], 0.1)
            self.assertTrue(summary["localization"]["passed"])

    def test_file_result_id_overrides_embedded_local_period(self):
        """A fresh BIN replay writes period_id=0; GMTI file ID must restore it."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "run"
            run_dir.mkdir()
            fields = ["period_id", "beam_id", "new_e", "new_n", "range_m",
                      "theta_used_deg"]
            # Deliberately put period_id=0 in both files.  The evaluator must
            # use GMTI01/GMTI02 as periods 0/1 for cross-cycle matching.
            for result_id in (1, 2):
                write_csv(
                    run_dir / f"detection_results_GMTI{result_id:02d}.csv",
                    fields,
                    [{"period_id": "0", "beam_id": "1", "new_e": str(1000 + result_id),
                      "new_n": "2000", "range_m": "90000",
                      "theta_used_deg": "-40.5"}],
                )
            truth = root / "truth.csv"
            truth_fields = ["target_id", "period_id", "beam_id", "range_bin", "visible",
                            "e_mid", "n_mid", "range_m", "azimuth_deg"]
            write_csv(truth, truth_fields, [
                {"target_id": "target0", "period_id": "0", "beam_id": "1",
                 "range_bin": "1", "visible": "1", "e_mid": "1001", "n_mid": "2000",
                 "range_m": "90000", "azimuth_deg": "139.5"},
                {"target_id": "target1", "period_id": "1", "beam_id": "1",
                 "range_bin": "1", "visible": "1", "e_mid": "1002", "n_mid": "2000",
                 "range_m": "90000", "azimuth_deg": "139.5"},
            ])
            cfg = root / "match.json"
            cfg.write_text(json.dumps({"max_period_diff": 0, "max_beam_diff": 0,
                                       "max_time_diff_s": 1, "max_range_error_m": 100,
                                       "max_position_error_m": 10,
                                       "max_velocity_error_mps": 50}), encoding="utf-8")
            scnr = root / "scnr.json"
            scnr.write_text(json.dumps({"target_scnr_db": 12.0, "tolerance_db": 0.5,
                                        "sample_count": 2, "expected_sample_count": 2,
                                        "all_samples_within_tolerance": True}), encoding="utf-8")
            report = root / "report"
            subprocess.run([
                sys.executable, str(EVALUATOR), "--run-dir", str(run_dir),
                "--truth", str(truth), "--match-config", str(cfg),
                "--report-dir", str(report), "--acceptance-pd", "0.9",
                "--output-scnr-summary", str(scnr), "--require-output-scnr",
            ], check=True, cwd=REPO)
            summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["detection"]["matched_count"], 2)
            self.assertAlmostEqual(summary["detection"]["detection_probability"], 1.0)
            self.assertEqual([row["period_id"] for row in summary["per_period"]], [0, 1])
            self.assertNotIn("false_alarm_count", summary["detection"])


if __name__ == "__main__":
    unittest.main()
