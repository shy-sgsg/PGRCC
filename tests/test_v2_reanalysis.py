import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from reanalyze_v2_final_gate import (  # noqa: E402
    paired_cfar_summary,
    recovery_filter,
    recovery_summary,
    rename_target_rows,
)


class V2ReanalysisTests(unittest.TestCase):
    def test_recovery_primary_filter_excludes_validation_and_target_on(self) -> None:
        rows = [
            {"split": "validation", "role": "target_off", "method": "J5",
             "recoverable_headroom_db": "1", "active": "true", "recovery_ratio": "0.8"},
            {"split": "test", "role": "target_on", "method": "J5",
             "recoverable_headroom_db": "1", "active": "true", "recovery_ratio": "0.8"},
            {"split": "test", "role": "target_off", "method": "J5",
             "recoverable_headroom_db": "0.5", "active": "true", "recovery_ratio": "0.6",
             "scene_id": "s0"},
            {"split": "test", "role": "target_off", "method": "J5",
             "recoverable_headroom_db": "0.4", "active": "true", "recovery_ratio": "0.9",
             "scene_id": "s1"},
        ]
        selected = recovery_filter(rows, "J5", 0.5)
        self.assertEqual(len(selected), 1)
        summary = recovery_summary(rows, "J5", 0.5)
        self.assertEqual(summary["scene_count"], 1)
        self.assertEqual(summary["active_count"], 1)
        self.assertAlmostEqual(summary["median"], 0.6)

    def test_paired_bootstrap_is_scene_paired_and_reports_difference(self) -> None:
        rows = [
            {"scene_id": "s0", "method": "J0_Current", "pfa": "1"},
            {"scene_id": "s0", "method": "J5", "pfa": "2"},
            {"scene_id": "s1", "method": "J0_Current", "pfa": "3"},
            {"scene_id": "s1", "method": "J5", "pfa": "2"},
        ]
        result = paired_cfar_summary(rows, "J5", "pfa", iterations=100)
        self.assertEqual(result["scene_count"], 2)
        self.assertAlmostEqual(result["delta_mean_candidate_minus_current"], 0.0)
        self.assertAlmostEqual(result["p_candidate_le_current"], 0.5)
        self.assertEqual(result["interpretation"], "no resolved difference")

    def test_target_metric_is_renamed_not_reinterpreted_as_causal(self) -> None:
        rows = [{"legacy_hit": "True", "target_id": "T1",
                 "target_preservation_db": "0.2"}]
        renamed = rename_target_rows(rows)[0]
        self.assertNotIn("legacy_hit", renamed)
        self.assertTrue(renamed["matched_target_on_pd"])
        self.assertEqual(renamed["causal_pd_status"],
                         "not_available_from_V2_target_rows")


if __name__ == "__main__":
    unittest.main()
