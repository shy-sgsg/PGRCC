#!/usr/bin/env python3
"""Synthetic regression tests for v1.6 SCNR and far-range coverage metrics."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


REPO = Path(__file__).resolve().parents[1]
TOOLS = (REPO / "docs/功能测试与实时性测试/GMTI完整测试手册与脚本_20260729/tools")
PARAMETERS = REPO / "configs/stage2/gmti_far_system_parameters_20260818.json"


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class GmtiV16MetricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        required = (
            "evaluate_output_scnr.py",
            "evaluate_range_coverage.py",
        )
        missing = [name for name in required if not (TOOLS / name).is_file()]
        if missing:
            raise unittest.SkipTest(
                "legacy v1.6 evaluator tools are absent after compact-output cleanup: "
                + ", ".join(missing)
            )

    def test_output_scnr_is_measured_from_target_local_background(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metrics = root / "csi_metrics/run"
            metrics.mkdir(parents=True)
            power = np.ones((16, 32), dtype=np.float32)
            ratio = 10.0 ** (11.75 / 10.0)
            power[np.ix_([7, 8, 9], [14, 15, 16])] = 1.0 + ratio
            power_path = metrics / "after.npy"
            np.save(power_path, power)
            manifest = [{"period_id": "0", "beam_id": "5",
                         "after_power_path": str(power_path),
                         "target_half_range_bins": "1", "target_half_doppler_bins": "1",
                         "guard_range_bins": "2", "guard_doppler_bins": "2",
                         "background_range_bins": "6", "background_doppler_bins": "5"}]
            write_csv(metrics / "csi_roi_manifest.csv", manifest)
            truth = root / "truth.csv"
            write_csv(truth, [{"target_id": "T1", "period_id": "0", "beam_id": "5",
                               "visible": "1", "row_truth": "8", "range_bin": "15",
                               "snr_db": "3"}])
            report = root / "report"
            subprocess.run([sys.executable, str(TOOLS / "evaluate_output_scnr.py"),
                            "--run-dir", str(root), "--truth", str(truth),
                            "--report-dir", str(report), "--tolerance-db", "0.5",
                            "--search-half-range-bins", "0",
                            "--search-half-doppler-bins", "0",
                            "--measurement-guard-range-bins", "2",
                            "--measurement-guard-doppler-bins", "2"],
                           check=True, cwd=REPO)
            summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["all_targets_within_tolerance"])
            self.assertTrue(summary["all_samples_within_tolerance"])
            self.assertEqual(summary["acceptance_interval_db"], [11.5, 12.0])
            with (report / "target_scnr_calibration.csv").open(
                    encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertAlmostEqual(float(rows[0]["mean_output_scnr_db"]), 11.75,
                                   places=4)

    def test_output_scnr_does_not_accept_only_a_passing_period_mean(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            truth_rows = []
            for period, scnr_db in enumerate((11.0, 12.6)):
                metrics = root / f"period_{period}/csi_metrics"
                metrics.mkdir(parents=True)
                power = np.ones((16, 32), dtype=np.float32)
                ratio = 10.0 ** (scnr_db / 10.0)
                power[np.ix_([7, 8, 9], [14, 15, 16])] = 1.0 + ratio
                power_path = metrics / "after.npy"
                np.save(power_path, power)
                # Production file mode starts each inner CSI period at zero;
                # GMTIxx is the stable outer-cycle identity.
                write_csv(metrics / "csi_roi_manifest.csv", [{
                    "result_id": f"GMTI{period + 1:02d}",
                    "period_id": "0", "beam_id": "5",
                    "after_power_path": str(power_path),
                    "target_half_range_bins": "1",
                    "target_half_doppler_bins": "1",
                    "guard_range_bins": "2", "guard_doppler_bins": "2",
                    "background_range_bins": "6",
                    "background_doppler_bins": "5"}])
                truth_rows.append({"target_id": "T1", "period_id": str(period),
                                   "beam_id": "5", "visible": "1",
                                   "row_truth": "8", "range_bin": "15",
                                   "snr_db": "3"})
            truth = root / "truth.csv"
            write_csv(truth, truth_rows)
            report = root / "report"
            completed = subprocess.run([
                sys.executable, str(TOOLS / "evaluate_output_scnr.py"),
                "--run-dir", str(root), "--truth", str(truth),
                "--report-dir", str(report), "--tolerance-db", "0.5",
                "--search-half-range-bins", "0",
                "--search-half-doppler-bins", "0",
                "--measurement-guard-range-bins", "2",
                "--measurement-guard-doppler-bins", "2"],
                check=False, cwd=REPO)
            self.assertEqual(completed.returncode, 1)
            summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["all_target_means_within_tolerance"])
            self.assertFalse(summary["all_samples_within_tolerance"])
            self.assertFalse(summary["all_targets_within_tolerance"])

    def test_action_range_and_swath_use_algorithm_runtime_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "runtime.json"
            runtime.write_text(json.dumps({
                "waveform": {"fs_hz": 60_000_000, "Tr_sec": 130e-6,
                             "sample_delay_us": 488},
                "file_layout": {"pulse_len": 11840}}), encoding="utf-8")
            truth = root / "truth.csv"
            write_csv(truth, [
                {"target_id": "near", "period_id": "0", "visible": "1",
                 "range_m": "75000"},
                {"target_id": "far", "period_id": "0", "visible": "1",
                 "range_m": "83100"}])
            report = root / "report"
            subprocess.run([sys.executable, str(TOOLS / "evaluate_range_coverage.py"),
                            "--runtime-config", str(runtime), "--truth", str(truth),
                            "--system-parameters", str(PARAMETERS),
                            "--report-dir", str(report)], check=True, cwd=REPO)
            summary = json.loads(
                (report / "range_coverage_summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["acceptance"]["passed"])
            self.assertGreater(summary["calculated"]["farthest_slant_range_m"], 83_000)
            self.assertGreater(summary["calculated"]["range_swath_m"], 7_000)


if __name__ == "__main__":
    unittest.main()
