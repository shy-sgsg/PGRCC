"""Focused tests for the Phase-I F1/F2 observability contract."""

from __future__ import annotations

import math
import json
from pathlib import Path

import numpy as np
import pytest

from scripts.audit_two_channel_error_observability import (
    OBSERVABLE_NAMES,
    OBSERVABLE_METADATA,
    _json_safe,
    classify_observability,
    compute_f1_f2_observables,
    central_difference_sensitivity,
    build_case_config,
    fuse_protocol_channels_to_f1_f2,
    load_f1_f2_protocol,
    summarize_seed_stability,
    summarize_zero_control_delta,
)


ROOT = Path(__file__).resolve().parents[1]


def test_json_safe_preserves_boolean_protocol_fields() -> None:
    payload = _json_safe({"enabled": True, "disabled": False})
    assert payload == {"enabled": True, "disabled": False}
    assert all(isinstance(value, bool) for value in payload.values())


def test_observability_case_centers_raw_lfm_clutter_in_fast_time() -> None:
    template = json.loads(
        (ROOT / "configs/research/unknown_system_error_true_geometry_pilot.json")
        .read_text(encoding="utf-8")
    )
    config = build_case_config(template, "baseline_geometry_error", 0.5)

    calibration_range_m = float(config["scene"]["area_clutter"]["calibration_range_m"])
    expected_delay_us = 2.0 * calibration_range_m / 299_792_458.0 * 1.0e6
    assert config["range_processing"]["sample_delay_us"] == pytest.approx(expected_delay_us)
    assert all(target["enabled"] is False for target in config["targets"])


def test_production_pair_fusion_uses_one_three_and_two_four() -> None:
    channels = np.zeros((1, 2, 4), dtype=np.complex128)
    channels[0, :, 0] = 1.0 + 2.0j
    channels[0, :, 2] = 3.0 + 4.0j
    channels[0, :, 1] = 5.0 + 6.0j
    channels[0, :, 3] = 7.0 + 8.0j

    f1, f2 = fuse_protocol_channels_to_f1_f2(channels)

    np.testing.assert_allclose(f1, 2.0 + 3.0j)
    np.testing.assert_allclose(f2, 6.0 + 7.0j)


def test_scientific_loader_rejects_implicit_two_channel_input() -> None:
    with pytest.raises(ValueError, match="requires four channels"):
        load_f1_f2_protocol(
            [Path("does-not-exist.bin")],
            pulse_len=1,
            channel_count=2,
            iq_data_type="float32",
        )


def test_observable_metadata_declares_source_and_axis_groups() -> None:
    assert set(OBSERVABLE_METADATA) == set(OBSERVABLE_NAMES)
    for name in OBSERVABLE_NAMES:
        metadata = OBSERVABLE_METADATA[name]
        assert metadata["source_id"] == "protocol_f1_f2"
        assert metadata["axis_group"]
        assert metadata["range_group"]
        assert metadata["angle_group"]
        assert metadata["pulse_group"]


def test_observables_recover_frequency_pulse_and_angle_slopes() -> None:
    frequency_hz = np.linspace(-4.0, 4.0, 9)
    pulse_index = np.arange(5, dtype=np.float64)
    beam_angle_deg = np.zeros(5, dtype=np.float64)
    phase = (
        0.2
        + 0.04 * frequency_hz[None, :]
        + 0.01 * pulse_index[:, None]
    )
    f1 = np.ones_like(phase, dtype=np.complex128)
    f2 = np.exp(-1j * phase)

    result = compute_f1_f2_observables(
        f1,
        f2,
        frequency_hz=frequency_hz,
        pulse_index=pulse_index,
        beam_angle_deg=beam_angle_deg,
        prf_hz=1000.0,
    )

    assert result["cross_channel_phase_rad"] == pytest.approx(0.2, abs=0.02)
    assert result["phase_vs_frequency_slope_rad_per_hz"] == pytest.approx(0.04, abs=1e-3)
    assert result["phase_vs_pulse_slope_rad_per_pulse"] == pytest.approx(0.01, abs=1e-3)
    assert result["f1_f2_coherence"] > 0.99
    assert all(name in result for name in OBSERVABLE_NAMES)
    assert math.isfinite(float(result["csi_residual_power_db"]))

    angle_phase = 0.2 + 0.30 * np.sin(np.deg2rad(np.array(
        [-20.0, -10.0, 0.0, 10.0, 20.0]
    )))[:, None]
    angle_result = compute_f1_f2_observables(
        np.ones_like(angle_phase, dtype=np.complex128),
        np.exp(-1j * angle_phase),
        frequency_hz=np.array([0.0]),
        pulse_index=np.zeros(5),
        beam_angle_deg=np.array([-20.0, -10.0, 0.0, 10.0, 20.0]),
        prf_hz=1000.0,
    )
    assert angle_result["phase_vs_beam_angle_slope_rad_per_sin_theta"] == pytest.approx(0.30, abs=1e-3)


def test_central_difference_and_confounded_columns_are_explicit() -> None:
    plus = {"phase": 1.2, "coherence": 0.8}
    minus = {"phase": 0.2, "coherence": 0.9}
    sensitivity = central_difference_sensitivity(plus, minus, perturbation=0.5)
    assert sensitivity["phase"] == pytest.approx(1.0)
    assert sensitivity["coherence"] == pytest.approx(-0.1)

    matrix = np.array(
        [[1.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.5, 0.5, 0.0]],
        dtype=np.float64,
    )
    report = classify_observability(
        matrix,
        state_names=("delay", "phase", "velocity"),
        observable_names=("phase_vs_frequency", "ridge", "coherence"),
        zero_control=np.zeros(3),
    )
    assert report["scaled_rank"] == 2
    assert report["status_by_state"]["delay"]["status"] == (
        "not independently observable from current two-channel data"
    )
    assert report["status_by_state"]["delay"]["external_prior"]


def test_classification_balances_observable_rows_before_column_cosine() -> None:
    # The first observable has a larger physical unit than the second.  Raw
    # column cosine would call the states confounded even though the second
    # observable carries the opposite sign and separates them.
    matrix = np.array([[100.0, 100.0], [1.0, -1.0]], dtype=np.float64)
    report = classify_observability(
        matrix,
        state_names=("delay", "phase"),
        observable_names=("large_unit", "small_unit"),
        zero_control=np.zeros(2),
    )
    assert report["scaled_rank"] == 2
    assert abs(report["column_cosine"][0][1]) < 0.1
    assert report["status_by_state"]["delay"]["status"] == "candidate independently observable"
    assert report["status_by_state"]["phase"]["status"] == "candidate independently observable"
    assert len(report["observable_row_scales"]) == 2
    assert report["classification_matrix"]


def test_classification_exposes_pair_level_near_confounding() -> None:
    report = classify_observability(
        np.array([[1.0, 1.0], [1.0, 0.5]], dtype=np.float64),
        state_names=("delay", "servo"),
        observable_names=("phase", "angle"),
        zero_control=np.zeros(2),
    )
    pair = report["pair_observability"][0]
    assert pair["state_a"] == "delay"
    assert pair["state_b"] == "servo"
    assert pair["status"] == "near-confounded; not independently observable from current two-channel data"


def test_zero_control_delta_is_checked_against_an_error_free_reference() -> None:
    report = summarize_zero_control_delta(
        {
            ("delay", 101): [1.0, 2.0],
            ("servo", 101): [1.0 + 1.0e-10, 2.0 - 1.0e-10],
        },
        observable_names=("phase", "coherence"),
        reference_parameter="delay",
        tolerance=1.0e-9,
    )
    assert report["status"] == "passed"
    assert report["max_abs_delta"] == pytest.approx(1.0e-10)


def test_seed_stability_reports_sign_and_spread() -> None:
    rows = [
        {"parameter": "delay", "seed": 101, "observable": "phase", "sensitivity": 2.0},
        {"parameter": "delay", "seed": 202, "observable": "phase", "sensitivity": 3.0},
        {"parameter": "delay", "seed": 303, "observable": "phase", "sensitivity": 4.0},
        {"parameter": "delay", "seed": 101, "observable": "ridge", "sensitivity": -1.0},
        {"parameter": "delay", "seed": 202, "observable": "ridge", "sensitivity": 1.0},
    ]
    summary = summarize_seed_stability(rows)
    phase = next(row for row in summary if row["observable"] == "phase")
    ridge = next(row for row in summary if row["observable"] == "ridge")
    assert phase["seed_count"] == 3
    assert phase["sign_consistent"] is True
    assert phase["median_sensitivity"] == pytest.approx(3.0)
    assert phase["min_sensitivity"] == pytest.approx(2.0)
    assert phase["max_sensitivity"] == pytest.approx(4.0)
    assert ridge["seed_count"] == 2
    assert ridge["sign_consistent"] is False


@pytest.mark.parametrize(
    "bad_f1,bad_f2",
    [
        (np.zeros((2, 3, 3)), np.zeros((2, 3, 4))),
        (np.zeros((2, 3, 4)), np.full((2, 3, 4), np.nan)),
    ],
)
def test_observability_rejects_bad_input(bad_f1: np.ndarray, bad_f2: np.ndarray) -> None:
    with pytest.raises(ValueError):
        fuse_protocol_channels_to_f1_f2(bad_f1 if bad_f1.shape[-1] != 4 else bad_f2)
    if bad_f1.shape == bad_f2.shape:
        with pytest.raises(ValueError):
            compute_f1_f2_observables(bad_f1[..., 0], bad_f2[..., 0])
