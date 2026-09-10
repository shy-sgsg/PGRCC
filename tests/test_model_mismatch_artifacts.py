"""回归检查当前 M1/M2/M3 CUDA challenge 及机制 Oracle 的紧凑产物。"""

from __future__ import annotations

import csv
import json
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class ModelMismatchArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skipTest(
            "legacy mismatch artifact paths were removed during the compact-output cleanup; "
            "Git history 09bbd62 contains the original raw/control run. "
            "Replacement coverage is in tests/test_mechanism_estimators.py, "
            "tests/test_mechanism_oracle_methods.py, and tests/test_causal_and_fixed_pfa.py."
        )

    def test_zero_and_static_raw_sha_regressions(self) -> None:
        for root, level in (
            (ROOT / "outputs/ai_csi_model_mismatch_m1_rerun", "zero"),
            (ROOT / "outputs/ai_csi_model_mismatch_m2", "zero"),
            (ROOT / "outputs/ai_csi_model_mismatch_m3", "static_regression"),
        ):
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            baseline_sha = manifest["baseline"]["raw_sha256"]
            result = next(row for row in manifest["variants"] if row["level"] == level)
            self.assertEqual(result["status"], "pass")
            self.assertTrue(result["same_as_zero_baseline"])
            self.assertEqual(result["raw_sha256"], baseline_sha)

    def test_m1_fractional_delay_signature(self) -> None:
        path = ROOT / "outputs/ai_csi_model_mismatch_m1_rerun/model_mismatch_summary.csv"
        data = rows(path)
        data.sort(key=lambda row: float(row["value"]))
        measured = [float(row["measured_slope_rad_per_hz"]) for row in data]
        self.assertEqual(len(data), 4)
        self.assertLessEqual(measured[0], measured[1])
        self.assertLessEqual(measured[1], measured[2])
        self.assertLessEqual(measured[2], measured[3])
        strong = data[-1]
        self.assertGreater(float(strong["expected_phase_slope_rad_per_hz"]), 0.0)
        self.assertGreater(float(strong["measured_slope_rad_per_hz"]), 0.0)

    def test_m2_truth_slope_and_slow_time_signature(self) -> None:
        root = ROOT / "outputs/ai_csi_model_mismatch_m2"
        for level in ("weak", "moderate", "strong"):
            summary = json.loads(
                (root / "M2_pulse_varying_differential_phase" / level /
                 "residual_texture/residual_texture_summary.json").read_text(encoding="utf-8")
            )
            configured = {"weak": 0.05, "moderate": 0.2, "strong": 0.5}[level]
            truth = json.loads(
                (root / "M2_pulse_varying_differential_phase" / level /
                 "variant_summary.json").read_text(encoding="utf-8")
            )
            self.assertAlmostEqual(float(truth["truth_phase_slope_deg_per_pulse"]), configured)
            fit = summary["slow_time_phase"]["fit_vs_pulse"]
            self.assertGreater(float(fit["r2"]), 0.95)

    def test_m3_rho_levels_and_oracle_does_not_use_truth(self) -> None:
        root = ROOT / "outputs/ai_csi_model_mismatch_m3"
        summary = rows(root / "model_mismatch_summary.csv")
        by_level = {row["level"]: row for row in summary}
        self.assertEqual(by_level["static_regression"]["same_as_zero_baseline"], "True")
        for level, expected in (("weak_motion", 0.99), ("moderate_motion", 0.95), ("strong_motion", 0.8)):
            self.assertAlmostEqual(float(by_level[level]["value"]), expected)
            self.assertEqual(float(by_level[level]["coherence_gate_bypass_fraction"]), 0.0)
        oracle = json.loads(
            (ROOT / "outputs/ai_csi_model_mismatch/mechanism_oracle/"
             "M3_internal_clutter_motion.json").read_text(encoding="utf-8")
        )
        self.assertTrue(oracle["input_truth_not_used_by_estimator"])
        self.assertGreater(float(oracle["delta_cancellation_db"]), 0.0)
        self.assertLess(float(oracle["delta_cancellation_db"]), 1.0)

    def test_h0_gate_and_failure_map_are_explicit(self) -> None:
        failure = rows(ROOT / "outputs/ai_csi_model_mismatch/failure_map.csv")
        h0 = {row["level"]: row for row in failure if row["mismatch_family"] == "H0_pure_thermal_noise"}
        self.assertEqual(h0["gate_false"]["CFAR_selected"], "21.0")
        self.assertEqual(h0["gate_true"]["CFAR_selected"], "0.0")
        self.assertEqual(h0["gate_true"]["failure_type"], "Type_III_H0_statistical_failure")
        self.assertEqual(h0["gate_false"]["Pd"], "nan")

    def test_positive_pd_uses_production_snapshot_and_truth(self) -> None:
        failure = rows(ROOT / "outputs/ai_csi_model_mismatch/failure_map.csv")
        production_rows = [row for row in failure if row["mismatch_family"] != "H0_pure_thermal_noise"]
        self.assertEqual(len(production_rows), 12)
        self.assertTrue(all(row["detection_eval_status"] == "measured" for row in production_rows))
        self.assertTrue(all(float(row["visible_target_count"]) == 1.0 for row in production_rows))
        self.assertTrue(all(row["Pd"] in {"0.0", "1.0"} for row in production_rows))

    def test_failure_map_has_measured_p38_and_cfar_diagnostics(self) -> None:
        diagnostic_manifest = json.loads(
            (ROOT / "outputs/ai_csi_model_mismatch_p38_cfar_diagnostics/manifest.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(diagnostic_manifest["variant_count"], 36)
        self.assertEqual(len(diagnostic_manifest["rows"]), 36)
        self.assertTrue(all(item["status"] == "pass" for item in diagnostic_manifest["rows"]))
        diagnostics = rows(
            ROOT / "outputs/ai_csi_model_mismatch_p38_cfar_diagnostics/production_diagnostic_metrics.csv"
        )
        self.assertEqual(len(diagnostics), 36)
        self.assertTrue(all(row["status"] == "pass" for row in diagnostics))
        self.assertTrue(all(math.isfinite(float(row["p38_inlier_ratio_mean"])) for row in diagnostics))
        self.assertTrue(all(math.isfinite(float(row["cfar_margin_db_mean"])) for row in diagnostics))
        failure = rows(ROOT / "outputs/ai_csi_model_mismatch/failure_map.csv")
        production_rows = [row for row in failure if row["mismatch_family"] != "H0_pure_thermal_noise"]
        self.assertEqual(len(production_rows), 12)
        for row in production_rows:
            p38 = float(row["P38_inlier_ratio"])
            cfar = float(row["CFAR_margin"])
            self.assertTrue(math.isfinite(p38))
            self.assertGreaterEqual(p38, 0.0)
            self.assertLessEqual(p38, 1.0)
            self.assertTrue(math.isfinite(cfar))

    def test_pfa_and_target_loss_use_paired_production_controls(self) -> None:
        controls = json.loads(
            (ROOT / "outputs/ai_csi_model_mismatch_metric_controls/metrics_manifest.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(controls["control_count"], 72)
        self.assertEqual(controls["failed_count"], 0)
        self.assertEqual(
            sum(item["mode"] == "target_only" for item in controls["results"]), 36
        )
        self.assertEqual(
            sum(item["mode"] == "negative_control" for item in controls["results"]), 36
        )
        failure = rows(ROOT / "outputs/ai_csi_model_mismatch/failure_map.csv")
        production_rows = [row for row in failure if row["mismatch_family"] != "H0_pure_thermal_noise"]
        self.assertTrue(all(row["control_status"] == "measured" for row in production_rows))
        self.assertTrue(all(row["target_loss_status"] == "measured" for row in production_rows))
        self.assertTrue(all(row["Pfa_status"] == "measured" for row in production_rows))
        self.assertTrue(all(float(row["Pfa_test_cells"]) == 519168.0 for row in production_rows))
        self.assertTrue(all(float(row["Pfa"]) > 0.0 for row in production_rows))

    def test_three_seed_failure_map_and_oracle_are_complete(self) -> None:
        root = ROOT / "outputs/ai_csi_model_mismatch_multiseed"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["seed_count"], 3)
        self.assertEqual(manifest["record_count"], 36)
        self.assertEqual(manifest["oracle_record_count"], 9)
        failure = rows(root / "failure_map_multiseed.csv")
        self.assertEqual(len(failure), 12)
        by_key = {(row["family"], row["level"]): row for row in failure}
        for key in (
            ("M1_fractional_channel_delay", "strong"),
            ("M2_pulse_varying_differential_phase", "strong"),
            ("M3_internal_clutter_motion", "strong_motion"),
        ):
            self.assertEqual(by_key[key]["seed_count"], "3")
            self.assertEqual(by_key[key]["all_status_pass"], "True")
            self.assertEqual(by_key[key]["coherence_gate_bypass_fraction_mean"], "0.0")
            self.assertIn("Pd_mean", by_key[key])
            self.assertIsNotNone(float(by_key[key]["Pfa_mean"]))
            self.assertIsNotNone(float(by_key[key]["target_loss_mean"]))
            self.assertIsNotNone(float(by_key[key]["p38_inlier_ratio_mean"]))
            self.assertIsNotNone(float(by_key[key]["cfar_margin_mean"]))
        oracle = rows(root / "mechanism_oracle_summary_multiseed.csv")
        oracle_by_family = {row["family"]: row for row in oracle}
        self.assertGreater(float(oracle_by_family["M1_fractional_channel_delay"]["delta_cancellation_db_mean"]), 6.0)
        self.assertGreater(float(oracle_by_family["M2_pulse_varying_differential_phase"]["delta_cancellation_db_mean"]), 3.0)
        self.assertLess(float(oracle_by_family["M3_internal_clutter_motion"]["delta_cancellation_db_mean"]), 0.5)
        self.assertEqual(oracle_by_family["M3_internal_clutter_motion"]["delta_coherence_mean"], "0.0")


if __name__ == "__main__":
    unittest.main()
