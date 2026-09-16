"""Tests for observable protocol delay correction."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from scripts.estimate_channel_delay import rewrite_float32_protocol_delay


HEADER_BYTES = 256


def _packet(samples: np.ndarray, counter: int = 0) -> bytes:
    pulse_len, channel_count = samples.shape
    header = bytearray(HEADER_BYTES)
    packet_bytes = HEADER_BYTES + pulse_len * channel_count * 2 * 4
    struct.pack_into("<I", header, 9, packet_bytes)
    struct.pack_into("<I", header, 20, counter)
    struct.pack_into("<h", header, 218, -1_000)
    struct.pack_into("<Q", header, 0, 0x5A5A5A5A5A5A5A5A)
    struct.pack_into("<Q", header, 248, 0x5B5B5B5B5B5B5B5B)
    iq = np.empty((pulse_len, channel_count, 2), dtype="<f4")
    iq[:, :, 0] = samples.real
    iq[:, :, 1] = samples.imag
    return bytes(header) + iq.tobytes(order="C")


def test_rewrite_corrects_fractional_delay_and_preserves_other_bytes(tmp_path) -> None:
    pulse_len = 64
    channel_count = 2
    fs_hz = 60.0e6
    delay_sec = 0.35 / fs_hz
    frequency = np.fft.fftfreq(pulse_len, d=1.0 / fs_hz)
    source = np.exp(1j * 2.0 * np.pi * 4.0 * np.arange(pulse_len) / pulse_len)
    delayed = np.fft.ifft(np.fft.fft(source) * np.exp(-1j * 2.0 * np.pi * delay_sec * frequency))
    samples = np.column_stack((source, delayed))
    input_path = tmp_path / "input.bin"
    output_path = tmp_path / "corrected.bin"
    input_path.write_bytes(_packet(samples))

    audit = rewrite_float32_protocol_delay(
        input_path,
        output_path,
        pulse_len=pulse_len,
        channel_count=channel_count,
        fs_hz=fs_hz,
        delta_tau_sec=delay_sec,
    )

    assert audit["packets_rewritten"] == 1
    raw = np.fromfile(output_path, dtype=np.uint8)
    original = np.fromfile(input_path, dtype=np.uint8)
    assert np.array_equal(raw[:HEADER_BYTES], original[:HEADER_BYTES])
    corrected_payload = raw[HEADER_BYTES:].view("<f4").reshape(pulse_len, channel_count, 2)
    corrected = corrected_payload[:, 1, 0] + 1j * corrected_payload[:, 1, 1]
    first = corrected_payload[:, 0, 0] + 1j * corrected_payload[:, 0, 1]
    assert np.max(np.abs(corrected - source)) < 2.0e-5
    assert np.max(np.abs(first - source)) < 2.0e-7


def test_rewrite_rejects_invalid_layout(tmp_path) -> None:
    with pytest.raises((ValueError, OSError, SystemExit)):
        rewrite_float32_protocol_delay(
            tmp_path / "missing.bin",
            tmp_path / "out.bin",
            pulse_len=0,
            channel_count=2,
            fs_hz=60.0e6,
            delta_tau_sec=0.0,
        )


def test_rewrite_four_channel_protocol_preserves_non_target_channels(tmp_path) -> None:
    pulse_len = 64
    channel_count = 4
    fs_hz = 60.0e6
    delay_sec = 0.35 / fs_hz
    frequency = np.fft.fftfreq(pulse_len, d=1.0 / fs_hz)
    reference = np.exp(1j * 2.0 * np.pi * 4.0 * np.arange(pulse_len) / pulse_len)
    delayed = np.fft.ifft(
        np.fft.fft(reference)
        * np.exp(-1j * 2.0 * np.pi * delay_sec * frequency)
    )
    samples = np.column_stack((
        reference,
        delayed,
        2.0 * reference,
        3.0 * reference,
    ))
    input_path = tmp_path / "input_4ch.bin"
    output_path = tmp_path / "corrected_4ch.bin"
    input_path.write_bytes(_packet(samples))

    audit = rewrite_float32_protocol_delay(
        input_path,
        output_path,
        pulse_len=pulse_len,
        channel_count=channel_count,
        fs_hz=fs_hz,
        delta_tau_sec=delay_sec,
        channel_indices=(1,),
    )

    assert audit["channel_count"] == channel_count
    assert audit["channel_indices"] == [1]
    payload = np.fromfile(output_path, dtype=np.uint8)[HEADER_BYTES:]
    iq = payload.view("<f4").reshape(pulse_len, channel_count, 2)
    corrected = iq[:, 1, 0] + 1j * iq[:, 1, 1]
    np.testing.assert_allclose(corrected, reference, atol=2.0e-5)
    np.testing.assert_allclose(iq[:, 0, 0] + 1j * iq[:, 0, 1], reference, atol=2.0e-7)
    np.testing.assert_allclose(iq[:, 2, 0] + 1j * iq[:, 2, 1], 2.0 * reference, atol=2.0e-7)
    np.testing.assert_allclose(iq[:, 3, 0] + 1j * iq[:, 3, 1], 3.0 * reference, atol=2.0e-7)
