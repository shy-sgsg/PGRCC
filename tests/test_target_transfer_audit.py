import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_target_transfer import (  # noqa: E402
    complex_stats,
    design_specs,
    enrich_transfer_rows,
    paired_causal_hit,
)


class TargetTransferAuditTests(unittest.TestCase):
    def test_complex_on_minus_off_increment_is_integrated_not_peak_only(self) -> None:
        current = {
            "target_id": "T1",
            "delta_complex": complex_stats(
                np.asarray([[1.0 + 0.0j, 2.0 + 0.0j]]), 10, 20),
            "target_only_fixedcal": complex_stats(
                np.asarray([[1.0 + 0.0j, 1.0 + 0.0j]]), 10, 20),
        }
        candidate = {
            "target_id": "T1",
            "delta_complex": complex_stats(
                np.asarray([[1.0 + 0.0j, 1.0 + 0.0j]]), 10, 20),
            "target_only_fixedcal": complex_stats(
                np.asarray([[2.0 + 0.0j, 1.0 + 0.0j]]), 10, 20),
        }
        row = enrich_transfer_rows([candidate], {"T1": current})[0]
        self.assertAlmostEqual(row["L_causal_dB"], 10.0 * math.log10(2.0 / 5.0))
        self.assertAlmostEqual(row["L_target_only_dB"], 10.0 * math.log10(5.0 / 2.0))
        self.assertIn("delta_sum_real", row)
        self.assertIn("target_only_energy", row)

    def test_paired_detection_requires_on_hit_and_off_miss(self) -> None:
        self.assertTrue(paired_causal_hit(True, False))
        self.assertFalse(paired_causal_hit(True, True))
        self.assertFalse(paired_causal_hit(False, False))

    def test_target_transfer_design_is_fresh_and_covers_requested_families(self) -> None:
        specs = design_specs("audit", (-2.0, 0.0, 2.0, 4.0))
        self.assertEqual(len(specs), 24)
        self.assertEqual({spec["label"] for spec in specs},
                         {"M1", "M2", "M1+M2", "M1+M3", "M2+M3", "M1+M2+M3"})
        self.assertEqual(len({spec["seed"] for spec in specs}), 24)
        self.assertTrue(all(spec["seed"] >= 2026110000 for spec in specs))
        self.assertTrue(all(spec["multi_target"] is False for spec in specs))


if __name__ == "__main__":
    unittest.main()
