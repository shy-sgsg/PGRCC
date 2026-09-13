import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.audit_router_learnability import (
    _j71_features,
    audit_dataset,
    validate_feature_columns,
)


ROOT = Path(__file__).resolve().parents[1]


class RouterLearnabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads((ROOT / "configs/research/router_opportunity_v1.json")
                                .read_text(encoding="utf-8"))

    def test_forbidden_feature_column_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_feature_columns(
                [{"scene_id": "s", "oracle_gain": 1.0}],
                self.config["forbidden_feature_inputs"],
            )

    def test_j71_reads_builder_coherence_gain_field(self) -> None:
        features = _j71_features({
            "joint_minus_p1_residual_coherence_gain": "0.37",
        })
        self.assertAlmostEqual(
            features["joint_vs_p1_residual_coherence_gain"], 0.37)

    def test_audit_uses_observable_features_and_never_authorizes_training(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            features = []
            metadata = []
            candidates = []
            selections = []
            families = self.config["design"]["families"]
            for index, family in enumerate(families):
                scene_id = f"scene_{index:02d}"
                features.append({
                    "scene_id": scene_id,
                    "observable_signal": float(index),
                    "geometry_look_angle_deg": float(-20 + index),
                })
                metadata.append({
                    "scene_id": scene_id,
                    "family": family,
                    "seed": 2026140000 + index,
                    "look_angle_deg": float(-20 + index),
                })
                candidates.extend([
                    {
                        "scene_id": scene_id, "family": family,
                        "action_id": "A0", "method": "J0_Current",
                        "lambda": 0.0, "cancellation_db": 10.0,
                        "gain_vs_Current": 0.0,
                        "current_detected_targets_lost": 0,
                        "safe": "True",
                    },
                    {
                        "scene_id": scene_id, "family": family,
                        "action_id": "A1",
                        "method": "J5_Selective_Physics_Calibration",
                        "lambda": 0.5, "cancellation_db": 10.2,
                        "gain_vs_Current": 0.2,
                        "current_detected_targets_lost": 0,
                        "safe": "True",
                    },
                    {
                        "scene_id": scene_id, "family": family,
                        "action_id": "A2", "method": "J5_Selective_Physics_Calibration",
                        "lambda": 1.0, "cancellation_db": 10.1,
                        "gain_vs_Current": 0.1,
                        "current_detected_targets_lost": 0, "safe": "False",
                    },
                    {
                        "scene_id": scene_id, "family": family,
                        "action_id": "A3", "method": "J6_Joint_Phase_Surface",
                        "lambda": 0.5, "cancellation_db": 10.05,
                        "gain_vs_Current": 0.05,
                        "current_detected_targets_lost": 0, "safe": "False",
                    },
                    {
                        "scene_id": scene_id, "family": family,
                        "action_id": "A4", "method": "J6_Joint_Phase_Surface",
                        "lambda": 0.75, "cancellation_db": 10.15,
                        "gain_vs_Current": 0.15,
                        "current_detected_targets_lost": 0, "safe": "False",
                    },
                    {
                        "scene_id": scene_id, "family": family,
                        "action_id": "A5", "method": "J6_Joint_Phase_Surface",
                        "lambda": 1.0, "cancellation_db": 10.0,
                        "gain_vs_Current": 0.0,
                        "current_detected_targets_lost": 0, "safe": "False",
                    },
                ])
                selections.append({
                    "scene_id": scene_id,
                    "family": family,
                    "selected_action": "A1",
                    "selected_lambda": 0.5,
                })

            def write(name: str, rows: list[dict[str, object]]) -> None:
                with (output / name).open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)

            write("router_inference_visible_features.csv", features)
            write("router_scene_metadata.csv", metadata)
            write("router_candidate_metrics.csv", candidates)
            write("router_scene_selection.csv", selections)
            materiality = {"materiality_gate_passed": True}
            result = audit_dataset(output, self.config, materiality)
            self.assertEqual(result["labels"]["Y2"], "best_safe_action")
            self.assertFalse(result["ai_training"])
            self.assertFalse(result["training_authorized"])
            self.assertIn("seed-out", result["feature_stability"]["split_families"])
            self.assertIn("logistic_regression", result["baseline_results"])


if __name__ == "__main__":
    unittest.main()
