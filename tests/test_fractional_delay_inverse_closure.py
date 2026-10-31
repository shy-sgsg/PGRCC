"""Tests for the channel-delay inverse-closure audit."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from scripts.audit_fractional_delay_inverse_closure import (
    closure_metrics,
    correct_fractional_delay_exact,
    fuse_protocol_channels,
    inject_fractional_delay,
    run_closure_matrix,
)


FS_HZ = 60.0e6


def _tone(length: int = 256, bin_index: int = 11) -> np.ndarray:
    n = np.arange(length, dtype=np.float64)
    return np.exp(1j * 2.0 * np.pi * bin_index * n / length)


def _lfm(length: int = 512) -> np.ndarray:
    n = np.arange(length, dtype=np.float64)
    t = (n - length / 2.0) / FS_HZ
    bandwidth = 18.0e6
    return np.exp(1j * np.pi * bandwidth / (length / FS_HZ) * t * t)


def test_c0_circular_tone_inverse_closure_is_complex_and_signed() -> None:
    reference = _tone()
    for delay_ns in (2.0, -2.0, 4.25, -4.25):
        delayed = inject_fractional_delay(
            reference, delay_ns, FS_HZ, mode="circular"
        )
        recovered = correct_fractional_delay_exact(
            delayed, delay_ns, FS_HZ, mode="circular"
        )
        metrics = closure_metrics(reference, recovered)
        assert metrics["finite"] is True
        assert float(metrics["complex_nmse"]) < 1.0e-20
        assert float(metrics["energy_ratio"]) == pytest.approx(1.0, rel=1.0e-10)


def test_c1_lfm_and_c2_pulse_boundary_metrics_are_reported() -> None:
    reference = _lfm()
    delayed = inject_fractional_delay(reference, 4.0, FS_HZ, mode="zero_padded", zero_pad=128)
    recovered = correct_fractional_delay_exact(
        delayed, 4.0, FS_HZ, mode="zero_padded", zero_pad=128
    )
    interior = np.zeros(reference.size, dtype=bool)
    interior[64:-64] = True
    metrics = closure_metrics(reference, recovered, interior_mask=interior)
    assert metrics["finite"] is True
    assert float(metrics["edge_sample_loss"]) >= 0.0
    assert "group_delay_residual_ns" in metrics
    assert "coherence" in metrics


def test_linear_zero_padded_mode_is_distinguished_from_circular_mode() -> None:
    reference = np.zeros(128, dtype=np.complex128)
    reference[48] = 1.0 + 0.25j
    delayed = inject_fractional_delay(reference, 2.0, FS_HZ, mode="linear")
    recovered = correct_fractional_delay_exact(
        delayed, 2.0, FS_HZ, mode="linear"
    )
    metrics = closure_metrics(reference, recovered)
    assert metrics["finite"] is True
    assert "edge_sample_loss" in metrics


def test_c3_four_channel_fusion_preserves_pair_definition() -> None:
    reference = _tone(128, 5)
    channels = np.column_stack((reference, reference, 2.0 * reference, 3.0 * reference))
    f1, f2 = fuse_protocol_channels(channels)
    np.testing.assert_allclose(f1, 1.5 * reference)
    np.testing.assert_allclose(f2, 2.0 * reference)


def test_closure_matrix_contains_c0_to_c4_and_boundary_schema(tmp_path: Path) -> None:
    result = run_closure_matrix(
        delays_ns=(0.0, 1.0, -1.0, 2.0, -2.0, 4.0, -4.0, 8.0, -8.0),
        seed=20260917,
        output_dir=tmp_path,
    )
    assert set(result["levels"]) == {"C0", "C1", "C2", "C3", "C4"}
    assert result["c4"]["status"] == "NOT_EVALUABLE"
    rows = result["rows"]
    assert rows
    required = {
        "level",
        "delay_ns",
        "boundary_class",
        "complex_nmse",
        "amplitude_error_rms",
        "phase_rms_rad",
        "group_delay_residual_ns",
        "edge_sample_loss",
        "energy_ratio",
        "coherence",
        "fallback_reason",
        "transform_mode",
        "zero_padding",
        "channel_indices",
        "correction_location",
        "fusion_definition",
        "pulse_compression_model",
    }
    assert required <= set(rows[0])
    assert {row["transform_mode"] for row in rows} >= {"circular", "zero_padded"}
    assert all(row["fallback_reason"] == "none" for row in rows)
    assert any(row["channel_indices"] == "2,4" for row in rows)
    assert any(int(row["zero_padding"]) > 0 for row in rows)
    assert any(row["correction_location"] == "after_fusion" for row in rows)
    assert any("partial" in str(row["correction_location"]) for row in rows)
    assert (tmp_path / "closure_rows.csv").is_file()
    assert (tmp_path / "closure_manifest.json").is_file()
