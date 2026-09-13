import json
import csv
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_router_materiality import (
    _reference_development,
    equal_family_stats,
    evaluate_materiality,
    lambda_distribution,
)


ROOT = Path(__file__).resolve().parents[1]


class RouterMaterialityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads((ROOT / "configs/research/router_opportunity_v1.json")
                                .read_text(encoding="utf-8"))
        cls.gate = json.loads((ROOT / "configs/research/physics_ai_router_gate_v1.json")
                              .read_text(encoding="utf-8"))

    def test_equal_family_weighting_is_not_changed_by_oversampling(self) -> None:
        records = [
            {"family": "M1", "value": 1.0},
            {"family": "M2", "value": 0.0},
            {"family": "M2", "value": 0.0},
            {"family": "M2", "value": 0.0},
        ]
        result = equal_family_stats(records, "value")
        self.assertAlmostEqual(result["equal_family_weighted_mean"], 0.5)

    def test_positive_opportunity_excludes_current_zero_headroom(self) -> None:
        records = [
            {"family": "zero", "value": 0.0},
            {"family": "M2", "value": 0.2},
        ]
        result = equal_family_stats(
            records, "value", 0.0, threshold_inclusive=False)
        self.assertAlmostEqual(result["positive_rate"], 0.5)

    def test_lambda_distribution_is_explicit_and_equal_family_weighted(self) -> None:
        rows = [
            {"family": "M1", "best_safe_lambda": 0.0},
            {"family": "M2", "best_safe_lambda": 0.5},
            {"family": "M2", "best_safe_lambda": 0.5},
        ]
        result = lambda_distribution(rows)
        self.assertEqual([row["lambda"] for row in result], [0.0, 0.5])
        self.assertAlmostEqual(result[0]["selected_fraction_equal_family"], 0.5)
        self.assertAlmostEqual(result[1]["selected_fraction_equal_family"], 0.5)

    def test_materiality_uses_safe_oracle_selection_and_keeps_training_false(self) -> None:
        families = self.config["design"]["families"]
        candidates = []
        selections = []
        for family_index, family in enumerate(families):
            scene_id = f"scene_{family_index:02d}"
            candidates.extend([
                {
                    "scene_id": scene_id, "family": family, "action_id": "A0",
                    "method": "J0_Current", "lambda": 0.0,
                    "cancellation_db": 10.0, "safe": "True",
                },
                {
                    "scene_id": scene_id, "family": family, "action_id": "A1",
                    "method": "J5_Selective_Physics_Calibration", "lambda": 0.5,
                    "cancellation_db": 10.2, "safe": "True",
                },
                {
                    "scene_id": scene_id, "family": family, "action_id": "A2",
                    "method": "J5_Selective_Physics_Calibration", "lambda": 1.0,
                    "cancellation_db": 9.0, "safe": "False",
                },
                {
                    "scene_id": scene_id, "family": family, "action_id": "A3",
                    "method": "J6_Joint_Phase_Surface", "lambda": 0.5,
                    "cancellation_db": 10.1, "safe": "True",
                },
                {
                    "scene_id": scene_id, "family": family, "action_id": "A4",
                    "method": "J6_Joint_Phase_Surface", "lambda": 0.75,
                    "cancellation_db": 10.15, "safe": "True",
                },
                {
                    "scene_id": scene_id, "family": family, "action_id": "A5",
                    "method": "J6_Joint_Phase_Surface", "lambda": 1.0,
                    "cancellation_db": 9.5, "safe": "False",
                },
            ])
            selections.append({
                "scene_id": scene_id, "family": family,
                "selected_action": "A1", "selected_lambda": 0.5,
            })
        result = evaluate_materiality(candidates, selections, self.config, self.gate)
        self.assertEqual(result["scene_count"], 8)
        self.assertAlmostEqual(
            result["overall_equal_family_weighted"]["equal_family_weighted_mean"],
            0.2,
        )
        self.assertTrue(result["materiality_gate_passed"])
        self.assertFalse(result["training_authorized"])
        self.assertEqual(result["router_state"], "REOPEN_ROUTER_RESEARCH")
        self.assertEqual(result["selected_action_distribution"][1]["action_id"], "A1")

    def test_development_reference_reports_m2_concentration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (root / "oracle_scene_selection.csv").open(
                    "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle,
                                        fieldnames=["scene_id", "family",
                                                    "safe_oracle_headroom_db"])
                writer.writeheader()
                writer.writerows([
                    {"scene_id": "a", "family": "M1", "safe_oracle_headroom_db": 0.0},
                    {"scene_id": "b", "family": "M2", "safe_oracle_headroom_db": 0.2},
                ])
            result = _reference_development(root, [0.05, 0.10, 0.25], 0.0)
            self.assertIsNotNone(result)
            self.assertAlmostEqual(
                result["m2_concentration"]["m2_headroom_share_raw"], 1.0)
            self.assertEqual(len(result["m2_containing_breakdown"]), 1)

    def test_development_reference_recovers_family_from_compact_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (root / "oracle_scene_selection.csv").open(
                    "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["scene_id", "selected_method",
                                "safe_oracle_headroom_db"],
                )
                writer.writeheader()
                writer.writerows([
                    {"scene_id": "a", "selected_method": "J0_Current",
                     "safe_oracle_headroom_db": 0.0},
                    {"scene_id": "b", "selected_method": "J6_Joint_Phase_Surface",
                     "safe_oracle_headroom_db": 0.2},
                ])
            (root / "target_safe_oracle_v2_manifest.json").write_text(
                json.dumps({"scene_records": [
                    {"spec": {"scene_id": "a", "label": "M1"}},
                    {"spec": {"scene_id": "b", "label": "M2"}},
                ]}),
                encoding="utf-8",
            )
            result = _reference_development(root, [0.05, 0.10, 0.25], 0.0)
            self.assertIsNotNone(result)
            self.assertEqual(result["m2_concentration"]["classification_status"],
                             "complete")
            self.assertAlmostEqual(
                result["m2_concentration"]["m2_opportunity_scene_fraction_raw"],
                1.0,
            )
            self.assertAlmostEqual(
                result["m2_concentration"]["phase_method_opportunity_scene_fraction_raw"],
                1.0,
            )


if __name__ == "__main__":
    unittest.main()
