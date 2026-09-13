import json
import unittest
from collections import Counter
from pathlib import Path

from scripts.build_router_opportunity_dataset import (
    EXPECTED_ACTIONS,
    FAMILIES,
    action_specs,
    design_specs,
    select_safe_action,
)


ROOT = Path(__file__).resolve().parents[1]


class RouterOpportunityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads((ROOT / "configs/research/router_opportunity_v1.json")
                                .read_text(encoding="utf-8"))
        cls.gate = json.loads((ROOT / "configs/research/physics_ai_router_gate_v1.json")
                              .read_text(encoding="utf-8"))

    def test_frozen_action_space_is_exact(self) -> None:
        actual = [(row["id"], row["method"], float(row["lambda"]))
                  for row in action_specs(self.gate)]
        self.assertEqual(actual, list(EXPECTED_ACTIONS))
        self.assertNotIn(("A1", "J5_Selective_Physics_Calibration", 0.25), actual)
        self.assertNotIn(("A3", "J6_Joint_Phase_Surface", 0.25), actual)

    def test_initial_design_has_equal_families_and_fresh_seeds(self) -> None:
        specs = design_specs(self.config)
        self.assertEqual(len(specs), 32)
        self.assertEqual(Counter(spec["label"] for spec in specs),
                         {family: 4 for family in FAMILIES})
        seeds = [spec["seed"] for spec in specs]
        self.assertEqual(seeds, list(range(2026140000, 2026140032)))
        for key in ("scan_min_deg", "squint_deg", "range_bin", "snr_db",
                    "radial_velocity_mps", "texture_sigma", "sampled_rho",
                    "sampled_delay_ns", "sampled_phase_deg"):
            self.assertEqual(len({spec[key] for spec in specs}), 32,
                             msg=f"LHS axis is not independently populated: {key}")

    def test_safe_selection_prefers_maximum_cancellation_then_current_on_tie(self) -> None:
        rows = [
            {"action_id": "A0", "cancellation_db": 10.0, "safe": True},
            {"action_id": "A1", "cancellation_db": 10.2, "safe": True},
            {"action_id": "A2", "cancellation_db": 10.2, "safe": True},
            {"action_id": "A3", "cancellation_db": 11.0, "safe": False},
        ]
        self.assertEqual(select_safe_action(rows)["action_id"], "A1")
        tied = [
            {"action_id": "A0", "cancellation_db": 10.0, "safe": True},
            {"action_id": "A1", "cancellation_db": 10.0, "safe": True},
        ]
        self.assertEqual(select_safe_action(tied)["action_id"], "A0")


if __name__ == "__main__":
    unittest.main()
