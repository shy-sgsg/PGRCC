"""Synthetic paired-control regressions for causal and fixed-Pfa evaluators."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_causal_detection import evaluate  # noqa: E402


def write_run(root: Path, peak: float, detection: bool, target_enabled: bool) -> None:
    tap = root / "stage2/algorithm_result/period_0000/csi_metrics/one"
    tap.mkdir(parents=True)
    truth = root / "stage2/truth"
    truth.mkdir(parents=True)
    shape = (4, 4)
    after = np.ones(shape, dtype=np.float64)
    before = np.ones(shape, dtype=np.float64)
    after[2, 1] = peak
    before[2, 1] = peak
    for name, array in (("after", after), ("before", before)):
        np.save(tap / f"{name}.npy", array)
    np.save(tap / "fa.npy", np.asarray([-150.0, -50.0, 50.0, 150.0]))
    (truth / "truth_targets_by_beam.csv").write_text(
        "target_id,visible,beam_id,af_total_truth_hz,expected_bin\n"
        "T1,1,1,50,1\n", encoding="utf-8")
    manifest = {
        "period_id": "0", "beam_id": "1", "rows": "4", "cols": "4",
        "az_st": "0", "az_ed": "3", "rg_st": "0", "rg_ed": "3",
        "target_half_doppler_bins": "0", "target_half_range_bins": "0",
        "after_power_path": str((tap / "after.npy").resolve()),
        "before_power_path": str((tap / "before.npy").resolve()),
        "fa_axis_path": str((tap / "fa.npy").resolve()),
    }
    with (tap / "csi_roi_manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest))
        writer.writeheader()
        writer.writerow(manifest)
    scenario = {"scene": {"noise": {"power": 1.0}},
                "targets": [{"target_id": "T1", "enabled": target_enabled,
                              "amplitude": {"snr_db": 10.0}}],
                "random": {"seed": 7}}
    (root / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
    result = root / "stage2/algorithm_result/period_0000"
    result.mkdir(parents=True, exist_ok=True)
    if detection:
        (result / "detection_results_GMTI01.csv").write_text(
            "row,col\n2,1\n", encoding="utf-8")
    else:
        (result / "detection_results_GMTI01.csv").write_text("row,col\n", encoding="utf-8")


class CausalAndPfaTests(unittest.TestCase):
    def test_causal_hit_rejects_equal_or_stronger_off_roi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            on = root / "on"
            off = root / "off"
            write_run(on, 10.0, True, True)
            write_run(off, 4.0, False, False)
            args = argparse.Namespace(target_on=on, target_off=off,
                                      target_only=None, output_dir=root / "out")
            result = evaluate(args)
            self.assertEqual(result["legacy_hit_count"], 1)
            self.assertEqual(result["causal_hit_count"], 1)
            off_after = np.load(off / "stage2/algorithm_result/period_0000/csi_metrics/one/after.npy")
            off_after[2, 1] = 10.0
            np.save(off / "stage2/algorithm_result/period_0000/csi_metrics/one/after.npy", off_after)
            result = evaluate(args)
            self.assertEqual(result["legacy_hit_count"], 1)
            self.assertEqual(result["causal_hit_count"], 0)

    def test_fixed_pfa_writes_empirical_curve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            negative = np.asarray([[1.0, 2.0, 3.0, 4.0],
                                   [5.0, 6.0, 7.0, 8.0]])
            positive = negative.copy()
            positive[0, 0] = 100.0
            np.save(root / "negative.npy", negative)
            np.save(root / "positive.npy", positive)
            output = root / "out"
            subprocess.run([
                sys.executable, str(ROOT / "scripts/run_fixed_pfa_evaluation.py"),
                "--negative-score", str(root / "negative.npy"),
                "--positive-score", str(root / "positive.npy"),
                "--target-row", "0", "--target-col", "0",
                "--half-doppler-bins", "0", "--half-range-bins", "0",
                "--pfa", "0.25", "--output-dir", str(output),
            ], check=True, cwd=ROOT, stdout=subprocess.PIPE, text=True)
            with (output / "fixed_pfa_roc.csv").open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["pfa_calibration_status"], "measured_from_negative_control_map")
            self.assertEqual(rows[0]["causal_pd"], "1.0")
            self.assertEqual(rows[0]["paired_causal_hit"], "True")


if __name__ == "__main__":
    unittest.main()
