import unittest

from scripts.run_physics_ai_final_gate_v3 import (
    V3_FAMILIES,
    V3_SEED_BASE,
    V3_SNR,
    design_specs,
)


class TestV3DesignTests(unittest.TestCase):
    def test_v3_is_8_by_8_and_uses_fresh_seeds(self) -> None:
        specs = design_specs()
        self.assertEqual(len(specs), 64)
        self.assertEqual({spec["label"] for spec in specs}, set(V3_FAMILIES))
        self.assertEqual({spec["snr_db"] for spec in specs}, set(V3_SNR))
        self.assertEqual(len({spec["seed"] for spec in specs}), 64)
        self.assertEqual(min(spec["seed"] for spec in specs), V3_SEED_BASE)
        self.assertEqual(max(spec["seed"] for spec in specs), V3_SEED_BASE + 63)
        self.assertTrue(all(spec["multi_target"] is False for spec in specs))

    def test_v3_has_eight_scenes_per_family_and_snr(self) -> None:
        specs = design_specs()
        for family in V3_FAMILIES:
            self.assertEqual(sum(spec["label"] == family for spec in specs), 8)
        for snr in V3_SNR:
            self.assertEqual(sum(spec["snr_db"] == snr for spec in specs), 8)


if __name__ == "__main__":
    unittest.main()
