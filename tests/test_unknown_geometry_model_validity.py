from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "unknown_geometry_model_validity.py"
SPEC = importlib.util.spec_from_file_location("unknown_geometry_model_validity", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


def _result(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "fit_status": "fit",
        "estimated_baseline_error_m": 0.0025,
        "uncertainty_m": 0.001,
        "phase_noise_rad": 0.01,
        "sensitivity_rad_per_m": 10.0,
        "model_disagreement_mean_abs_rad": 0.005,
        "model_disagreement_p95_rad": 0.008,
    }
    result.update(overrides)
    return result


def _observations(
    *,
    residuals: tuple[float, ...] = (0.0,) * 12,
    disagreement: float = 0.005,
    closure: float | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    index = 0
    for beam_id, theta in enumerate((-10.0, 0.0, 10.0)):
        for pulse_index in (0, 1):
            for pair_i, pair_j in ((0, 1), (0, 2)):
                row: dict[str, object] = {
                    "theta_deg": theta,
                    "slant_range_m": 8800.0 + 800.0 * (beam_id % 2),
                    "beam_id": beam_id,
                    "pulse_index": pulse_index,
                    "pair_i": pair_i,
                    "pair_j": pair_j,
                    "observed_phase_rad": 0.0,
                    "residual_rad": residuals[index],
                    "exact_linear_disagreement_rad": disagreement,
                    "weight": 1.0,
                }
                if closure is not None:
                    row["closure_residual_rad"] = closure
                rows.append(row)
                index += 1
    return rows


def test_consistent_exact_model_fit_is_valid() -> None:
    result = GATE.validate_geometry_model(_result(), _observations())
    assert result["status"] == "VALID"
    assert result["reasons"] == []
    assert result["thresholds"]["model_disagreement_budget_rad"] > 0.0


def test_cross_range_residual_trend_blocks_correction() -> None:
    residuals = tuple(theta * 0.01 for theta in (-10.0, -10.0, -10.0, -10.0,
                                                   0.0, 0.0, 0.0, 0.0,
                                                   10.0, 10.0, 10.0, 10.0))
    result = GATE.validate_geometry_model(_result(), _observations(residuals=residuals))
    assert result["status"] == "FALLBACK_MODEL_MISMATCH"
    assert any("cross-range" in reason for reason in result["reasons"])


def test_pair_closure_inconsistency_blocks_correction() -> None:
    result = GATE.validate_geometry_model(
        _result(), _observations(closure=0.25)
    )
    assert result["status"] == "FALLBACK_MODEL_MISMATCH"
    assert any("closure" in reason for reason in result["reasons"])


def test_pair_residual_inconsistency_blocks_correction() -> None:
    residuals = tuple(0.06 if index % 2 == 0 else 0.0 for index in range(12))
    result = GATE.validate_geometry_model(
        _result(), _observations(residuals=residuals)
    )
    assert result["status"] == "FALLBACK_MODEL_MISMATCH"
    assert any("pair residual" in reason for reason in result["reasons"])


def test_exact_linear_disagreement_above_budget_blocks_correction() -> None:
    result = GATE.validate_geometry_model(
        _result(), _observations(disagreement=0.2)
    )
    assert result["status"] == "FALLBACK_MODEL_MISMATCH"
    assert any("exact-versus-linear" in reason for reason in result["reasons"])


def test_insufficient_valid_observations_are_unidentifiable() -> None:
    result = GATE.validate_geometry_model(
        _result(), _observations()[:2]
    )
    assert result["status"] == "FALLBACK_UNIDENTIFIABLE"
    assert any("observations" in reason for reason in result["reasons"])
