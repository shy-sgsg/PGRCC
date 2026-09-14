from __future__ import annotations

import unittest

import numpy as np

from scripts import run_information_matched_stap as stap


class InformationMatchedStapTests(unittest.TestCase):
    def test_pair_fusion_is_exact_13_24_mapping(self) -> None:
        raw = np.zeros((2, 3, 4), dtype=np.complex128)
        for channel in range(4):
            raw[:, :, channel] = channel + 1.0
        fused = stap.pair_fuse_channels(raw)
        self.assertEqual(fused.shape, (2, 3, 2))
        np.testing.assert_allclose(fused[:, :, 0], 2.0)
        np.testing.assert_allclose(fused[:, :, 1], 3.0)

    def test_jdl_dimension_tracks_spatial_channels_and_three_taps(self) -> None:
        self.assertEqual(stap.jdl_dimension(2, 3), 6)
        self.assertEqual(stap.jdl_dimension(4, 3), 12)
        with self.assertRaises(ValueError):
            stap.jdl_dimension(3, 3)

    def test_contract_requires_common_processing_and_pure_dof_label(self) -> None:
        contract = stap.common_contract()
        self.assertEqual(contract["J2"]["spatial_channels"], 2)
        self.assertEqual(contract["J4"]["spatial_channels"], 4)
        self.assertEqual(contract["J2"]["fusion"], "production_(1,3)_(2,4)_F1_F2")
        self.assertEqual(contract["J4"]["fusion"], "none_native_four_channel")
        self.assertEqual(contract["comparison"]["pure_spatial_dof_delta"], "J4 - J2")
        self.assertTrue(contract["comparison"]["same_scene_seed_training_loading_steering_cfar_roi"])

    def test_common_config_rejects_mismatched_controls(self) -> None:
        stap.validate_common_controls({
            "doppler_taps": 3,
            "training_support": "rg_st..rg_ed",
            "covariance_estimator": "loaded_sample_covariance",
            "loading_fraction": 0.01,
            "target_steering": "configured_beam_center",
            "cfar": "production_GO_fixed_alpha_guard_background",
            "roi": "same_truth_centered_roi",
        })
        with self.assertRaises(ValueError):
            stap.validate_common_controls({"doppler_taps": 2})

    def test_pure_dof_delta_is_candidate_minus_reference(self) -> None:
        delta = stap.pure_spatial_dof_delta(
            {"output_SCNR_dB": 12.0, "background_Pfa": 0.002},
            {"output_SCNR_dB": 10.0, "background_Pfa": 0.001},
        )
        self.assertEqual(delta["delta_output_SCNR_dB"], 2.0)
        self.assertEqual(delta["delta_background_Pfa"], 0.001)
        self.assertEqual(delta["interpretation"], "pure spatial-DOF delta J4 - J2")


if __name__ == "__main__":
    unittest.main()
