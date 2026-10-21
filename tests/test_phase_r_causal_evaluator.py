from __future__ import annotations

import numpy as np


def test_complex_transfer_recovers_known_gain_and_phase() -> None:
    from scripts.phase_r.evaluate_r3_minimal_cuda_causal_pilot import (
        matched_complex_transfer,
    )

    reference = np.asarray([1 + 2j, -2 + 0.5j, 0.3 - 0.8j])
    gain = 1.7 * np.exp(0.23j)
    estimate = matched_complex_transfer(reference, gain * reference)
    assert estimate["status"] == "evaluable"
    assert np.isclose(estimate["coefficient_real"], gain.real)
    assert np.isclose(estimate["coefficient_imag"], gain.imag)
    assert np.isclose(estimate["amplitude_ratio"], abs(gain))
    assert np.isclose(estimate["phase_error_rad"], 0.23)


def test_complex_transfer_rejects_empty_or_zero_reference() -> None:
    from scripts.phase_r.evaluate_r3_minimal_cuda_causal_pilot import (
        matched_complex_transfer,
    )

    assert matched_complex_transfer(np.zeros(3), np.ones(3))["status"] == "NOT_EVALUABLE"
    assert matched_complex_transfer(np.ones(3), np.ones(2))["status"] == "NOT_EVALUABLE"


def test_active_support_csi_power_ratio_uses_true_csi_output() -> None:
    from scripts.phase_r.evaluate_r3_minimal_cuda_causal_pilot import (
        active_support_csi_power,
    )

    before = np.full((4, 6), 4.0)
    real = np.full((4, 6), 1.0)
    imag = np.zeros((4, 6))
    metrics = active_support_csi_power(
        before,
        real,
        imag,
        row_start=1,
        row_end=2,
        col_start=2,
        col_end=4,
    )
    assert metrics["status"] == "evaluable"
    assert metrics["valid_cells"] == 6
    assert metrics["before_mean_power"] == 4.0
    assert metrics["csi_output_mean_power"] == 1.0
    assert metrics["cancellation_db"] == 6.020599913279624


def test_repeatability_requires_consistent_a_to_b_loss_and_b_to_c_recovery() -> None:
    from scripts.phase_r.evaluate_r3_minimal_cuda_causal_pilot import (
        assess_primary_direction,
    )

    rows = []
    for seed, a, b, c in (
        (101, (1.0, 2.0, 3.0), (2.0, 3.0, 4.0), (0.8, 1.8, 2.8)),
        (202, (1.2, 2.2, 3.2), (2.2, 3.2, 4.2), (1.0, 2.0, 3.0)),
        (303, (1.4, 2.4, 3.4), (2.4, 3.4, 4.4), (1.1, 2.1, 3.1)),
    ):
        for condition, values in zip(("A", "B", "C"), (a, b, c)):
            rows.append({
                "seed": seed,
                "condition": condition,
                "angle_rmse_deg": values[0],
                "ground_speed_rmse_mps": values[1],
                "position_rmse_m": values[2],
            })
    assessment = assess_primary_direction(rows)
    assert assessment["repeatable_tracking_degradation_and_recovery"] is True
    assert assessment["independent_seed_count"] == 3

    rows[-1]["angle_rmse_deg"] = 2.5
    assert assess_primary_direction(rows)["repeatable_tracking_degradation_and_recovery"] is False
