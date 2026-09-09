import importlib.util
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_pgrcc_oracle.py"
SPEC = importlib.util.spec_from_file_location("build_pgrcc_oracle", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
ORACLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORACLE)


class PgrccOracleHelperTests(unittest.TestCase):
    def test_current_materialization_is_identity(self):
        current_bg = np.asarray([[1.0 + 2.0j, 3.0 - 4.0j]], dtype=np.complex128)
        current_target = np.asarray([[5.0 + 6.0j, 7.0 - 8.0j]], dtype=np.complex128)
        alignment = ORACLE.oracle.Alignment(
            name="test",
            shift=0.0,
            f1_bg=current_bg.copy(),
            f2_bg=current_bg.copy(),
            f1_target=current_target.copy(),
            f2_target=current_target.copy(),
            axis=np.zeros(1, dtype=float),
            dbs_k=0,
            range_phase=np.zeros(2, dtype=float),
            p38={"phase_rows": np.zeros(1, dtype=float)},
            alpha=np.ones(1, dtype=np.complex128),
            coherence=np.ones(1, dtype=float),
            row_phase=np.zeros(1, dtype=float),
            current_support=np.ones(1, dtype=bool),
            oracle_support=np.ones(1, dtype=bool),
        )
        arrays = {
            "alignment": alignment,
            "current_bg": current_bg,
            "current_target": current_target,
        }
        bg, target = ORACLE.materialize_candidate(
            None,
            arrays,
            {"candidate_type": "current"},
        )
        np.testing.assert_array_equal(bg, current_bg)
        np.testing.assert_array_equal(target, current_target)

    def test_pareto_dominance_and_current_fallback(self):
        def row(candidate_type, scnr, preservation, tail):
            return {
                "candidate_type": candidate_type,
                "delta_scnr_dB": scnr,
                "delta_target_preservation_dB": preservation,
                "delta_residual_p95_dB": tail,
                "delta_residual_cvar95_dB": tail,
                "delta_background_pfa": tail,
                "delta_detection_margin_dB": preservation,
            }

        current = row("current", 0.0, 0.0, 0.0)
        dominated = row("bounded_corrected", -1.0, -1.0, 1.0)
        tradeoff = row("bounded_corrected", 1.0, -1.0, 1.0)
        self.assertTrue(ORACLE.dominates(tradeoff, dominated))
        front = ORACLE.pareto_front([current, dominated, tradeoff])
        self.assertTrue(any(item.get("candidate_type") == "current" for item in front))
        self.assertFalse(any(item is dominated for item in front))
        self.assertTrue(any(item.get("candidate_type") == "bounded_corrected" for item in front))

    def test_worthwhile_policy(self):
        policy = {
            "min_delta_scnr_db": 0.25,
            "min_delta_target_preservation_db": -0.25,
            "max_delta_tail_p95_db": 0.0,
            "max_delta_tail_cvar95_db": 0.0,
            "max_delta_background_pfa": 0.0,
            "min_delta_detection_margin_db": -0.25,
        }
        candidate = {
            "delta_scnr_dB": 0.3,
            "delta_target_preservation_dB": -0.1,
            "delta_residual_p95_dB": 0.0,
            "delta_residual_cvar95_dB": -0.01,
            "delta_background_pfa": 0.0,
            "delta_detection_margin_dB": -0.2,
        }
        self.assertTrue(ORACLE.meets_policy(candidate, policy))
        candidate["delta_background_pfa"] = 1.0e-6
        self.assertFalse(ORACLE.meets_policy(candidate, policy))

    def test_portable_provenance(self):
        provenance = ORACLE.source_provenance(["unit-test"])
        self.assertIn(provenance["git_backend"], {"normal", "git-real"})
        self.assertTrue(provenance["source_commit"])
        self.assertEqual(provenance["command"], ["unit-test"])

    def test_transformed_parameter_metadata(self):
        case = {
            "valid_sample_fraction": None,
            "radial_speed_mps": 1.25,
            "impairments": {
                "channel_drop_probability": 0.2,
                "channel_amp_mismatch_db": 2.0,
            },
        }
        requested = {
            "valid_sample_fraction": 0.8,
            "target_radial_speed_mps": 1.25,
            "channel_amp_mismatch_db": 2.0,
        }
        metadata = ORACLE.audit_parameter_metadata(case, requested)
        self.assertEqual(metadata["requested_valid_sample_fraction"], 0.8)
        self.assertAlmostEqual(metadata["realized_valid_sample_fraction"], 0.8)
        self.assertEqual(metadata["requested_target_radial_speed_mps"], 1.25)
        self.assertEqual(metadata["realized_target_radial_speed_mps"], 1.25)
        self.assertEqual(metadata["realized_channel_amp_mismatch_db"], 2.0)


if __name__ == "__main__":
    unittest.main()
