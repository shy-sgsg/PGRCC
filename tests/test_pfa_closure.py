from __future__ import annotations

import unittest

import numpy as np

from scripts import run_pfa_closure as closure


class PfaClosureTests(unittest.TestCase):
    def test_count_uses_independent_valid_cuts_only(self) -> None:
        result = closure.summarize_cut_events(
            cut_power=np.asarray([2.0, 1.0, 5.0, np.nan]),
            threshold=np.asarray([1.0, 1.0, 10.0, 1.0]),
            valid=np.asarray([True, True, False, True]),
            cut_ids=np.asarray([10, 11, 11, 12]),
        )
        self.assertEqual(result["hit_count"], 1)
        self.assertEqual(result["valid_cut_count"], 2)
        self.assertEqual(result["excluded_cut_count"], 2)
        self.assertEqual(result["duplicate_cut_count"], 0)
        self.assertAlmostEqual(result["empirical_rate"], 0.5)

    def test_duplicate_independent_cut_ids_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            closure.summarize_cut_events(
                cut_power=np.asarray([2.0, 3.0]),
                threshold=np.asarray([1.0, 1.0]),
                valid=np.asarray([True, True]),
                cut_ids=np.asarray([7, 7]),
            )

    def test_hypothesis_contract_separates_pfa_and_structured_false_hit(self) -> None:
        self.assertEqual(closure.HYPOTHESES, ("H0", "H1", "H2", "H3", "H4", "H5"))
        self.assertEqual(closure.metric_definition("H0"), "empirical_cell_pfa")
        for hypothesis in ("H1", "H2", "H3", "H4", "H5"):
            self.assertEqual(
                closure.metric_definition(hypothesis),
                "structured_clutter_false_hit_fraction",
            )
        self.assertFalse(closure.configured_pfa_is_claim(closure.metric_definition("H0")))

    def test_go_training_geometry_has_production_cell_counts(self) -> None:
        self.assertEqual(closure.training_cell_counts(4, 16), {
            "corner_block_cells": 256,
            "center_block_cells": 144,
            "directional_strip_cells": 656,
            "unique_training_cells": 1600,
        })

    def test_simulation_records_independence_and_frozen_threshold(self) -> None:
        row = closure.simulate_hypothesis(
            "H0", seed=101, cut_count=128, chunk_size=32
        )
        self.assertEqual(row["hypothesis"], "H0")
        self.assertEqual(row["cut_count_requested"], 128)
        self.assertEqual(row["valid_cut_count"], 128)
        self.assertEqual(row["duplicate_cut_count"], 0)
        self.assertTrue(row["independent_cut_set"])
        self.assertEqual(row["threshold_status"], "frozen_production_alpha")
        self.assertNotIn("configured_pfa_achieved", row)


if __name__ == "__main__":
    unittest.main()
