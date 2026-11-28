"""Focused status and low-coherence metadata regressions for the mechanism pilot."""

from __future__ import annotations

import math

import numpy as np
import pytest

from scripts.analyze_equivalent_vs_physical_calibration import (
    ALLOWED_DECISIONS,
    PHYSICAL_STATUS_VOCABULARY,
)
from scripts.run_equivalent_vs_physical_calibration import coherence_metadata
from scripts.two_channel_complex_calibration import estimate_robust_ddc


def test_current_stage_status_vocabulary_is_explicit_and_excludes_historical_decision() -> None:
    assert set(PHYSICAL_STATUS_VOCABULARY) == {
        "RADAR_ESTIMATED",
        "SENSOR_PRIOR_ONLY",
        "PRIOR_PLUS_RADAR_RESIDUAL",
        "KNOWN_TRUTH",
        "NOT_EVALUABLE",
    }
    assert "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE" in ALLOWED_DECISIONS
    assert "GO_COUPLED_PHYSICAL_STATE_STUDY" not in ALLOWED_DECISIONS


@pytest.mark.parametrize(
    ("coherence", "reason"),
    [
        (float("nan"), "invalid_coherence"),
        (0.2, "low_coherence"),
    ],
)
def test_invalid_or_low_coherence_metadata_is_not_evaluable(
    coherence: float, reason: str
) -> None:
    metadata = coherence_metadata(coherence, threshold=0.8)

    assert metadata["status"] == "NOT_EVALUABLE"
    assert metadata["reason"] == reason


def test_robust_low_phase_coherence_keeps_not_evaluable_metadata() -> None:
    f1 = np.ones((4, 1), dtype=np.complex128)
    phases = np.array([0.0, math.pi / 2.0, math.pi, -math.pi / 2.0])
    f2 = np.exp(1j * phases)[:, np.newaxis]
    support = np.ones_like(f1, dtype=bool)

    estimate = estimate_robust_ddc(
        f1,
        f2,
        support,
        min_support=4,
        phase_threshold_rad=0.45,
    )

    assert estimate.status == "NOT_EVALUABLE"
    assert estimate.metadata["reasons"] == ["low_phase_coherence"]
    assert float(estimate.metadata["local_phase_coherence"][0]) == pytest.approx(0.0)
