#!/usr/bin/env python3
"""Audit finite-window fractional-delay inverse closure.

The audit is deliberately independent of CFAR and TrackManager decisions.  It
uses the repository's positive-delay convention: a positive delay is applied
with ``exp(-j*2*pi*f*tau)`` and an exact correction applies the opposite ramp.
Packet/pulse boundaries are retained as metadata so an interior closure result
cannot hide an edge or packet discontinuity.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


DEFAULT_FS_HZ = 60.0e6
DEFAULT_DELAYS_NS = (0.0, 1.0, -1.0, 2.0, -2.0, 4.0, -4.0, 8.0, -8.0)
_TWO_PI = 2.0 * math.pi


def _finite_float(value: object, name: str) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _validate_signal(signal: np.ndarray | Sequence[complex]) -> np.ndarray:
    array = np.asarray(signal, dtype=np.complex128)
    if array.ndim == 0 or array.shape[-1] <= 0:
        raise ValueError("signal must have a non-empty sample axis")
    if not np.all(np.isfinite(array.real)) or not np.all(np.isfinite(array.imag)):
        raise ValueError("signal must be finite")
    return array


def _apply_fractional_delay(
    signal: np.ndarray | Sequence[complex],
    delay_ns: float,
    fs_hz: float,
    *,
    mode: str,
    zero_pad: int = 0,
) -> np.ndarray:
    """Apply a signed fractional delay along the last axis."""

    source = _validate_signal(signal)
    delay = _finite_float(delay_ns, "delay_ns")
    sample_rate = _finite_float(fs_hz, "fs_hz")
    if sample_rate <= 0.0:
        raise ValueError("fs_hz must be positive")
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"circular", "zero_padded", "linear"}:
        raise ValueError("mode must be circular, zero_padded or linear")
    pad = int(zero_pad)
    if pad < 0:
        raise ValueError("zero_pad must be non-negative")
    if normalized_mode == "circular":
        pad = 0
    elif normalized_mode == "linear" and pad == 0:
        # A complete record of zero padding is the conservative linear model.
        pad = source.shape[-1]

    if pad:
        padded = np.pad(source, [(0, 0)] * (source.ndim - 1) + [(pad, pad)])
    else:
        padded = source
    length = padded.shape[-1]
    frequency_hz = np.fft.fftfreq(length, d=1.0 / sample_rate)
    ramp = np.exp(-1j * _TWO_PI * frequency_hz * delay * 1.0e-9)
    shifted = np.fft.ifft(np.fft.fft(padded, axis=-1) * ramp, axis=-1)
    if pad:
        shifted = shifted[..., pad : pad + source.shape[-1]]
    return np.asarray(shifted, dtype=np.complex128)


def inject_fractional_delay(
    signal: np.ndarray | Sequence[complex],
    delay_ns: float,
    fs_hz: float,
    *,
    mode: str = "circular",
    zero_pad: int = 0,
) -> np.ndarray:
    """Inject a positive/negative delay using the production sign convention."""

    return _apply_fractional_delay(signal, delay_ns, fs_hz, mode=mode, zero_pad=zero_pad)


def correct_fractional_delay_exact(
    signal: np.ndarray | Sequence[complex],
    delay_ns: float,
    fs_hz: float,
    *,
    mode: str = "circular",
    zero_pad: int = 0,
) -> np.ndarray:
    """Apply the exact opposite ramp to a signal with a known injected delay."""

    return _apply_fractional_delay(signal, -float(delay_ns), fs_hz, mode=mode, zero_pad=zero_pad)


def fuse_protocol_channels(channels: np.ndarray | Sequence[complex]) -> tuple[np.ndarray, np.ndarray]:
    """Return the fixed production pair fusion for a final channel axis of four."""

    array = _validate_signal(np.asarray(channels))
    if array.shape[-1] != 4:
        raise ValueError("protocol channels must have four channels on the final axis")
    return 0.5 * (array[..., 0] + array[..., 2]), 0.5 * (array[..., 1] + array[..., 3])


def _group_delay_residual_ns(
    reference: np.ndarray, recovered: np.ndarray, fs_hz: float
) -> float | None:
    ref = np.asarray(reference, dtype=np.complex128).reshape(-1)
    rec = np.asarray(recovered, dtype=np.complex128).reshape(-1)
    if ref.size < 8 or rec.size != ref.size:
        return None
    spectrum = np.fft.fft(ref) * np.conj(np.fft.fft(rec))
    frequency = np.fft.fftfreq(ref.size, d=1.0 / float(fs_hz))
    positive = frequency > 0.0
    magnitude = np.abs(spectrum)
    finite = positive & np.isfinite(magnitude) & (magnitude > 0.0)
    if int(np.count_nonzero(finite)) < 3:
        return None
    threshold = float(np.percentile(magnitude[finite], 20.0))
    finite &= magnitude >= threshold
    if int(np.count_nonzero(finite)) < 3:
        return None
    x = frequency[finite]
    order = np.argsort(x)
    phase = np.unwrap(np.angle(spectrum[finite][order]))
    try:
        slope = float(np.polyfit(x[order], phase, 1)[0])
    except (TypeError, ValueError, np.linalg.LinAlgError):
        return None
    return slope / _TWO_PI * 1.0e9


def closure_metrics(
    reference: np.ndarray | Sequence[complex],
    recovered: np.ndarray | Sequence[complex],
    *,
    interior_mask: np.ndarray | Sequence[bool] | None = None,
    fs_hz: float = DEFAULT_FS_HZ,
) -> dict[str, object]:
    """Compute complex, amplitude, phase, edge, energy and coherence metrics."""

    ref = _validate_signal(np.asarray(reference))
    rec = _validate_signal(np.asarray(recovered))
    if ref.shape != rec.shape:
        raise ValueError("reference and recovered must have equal shapes")
    if interior_mask is None:
        mask = np.ones(ref.shape, dtype=bool)
        has_boundary_mask = False
    else:
        mask = np.asarray(interior_mask, dtype=bool)
        if mask.shape != ref.shape:
            raise ValueError("interior_mask must have the same shape as the signal")
        has_boundary_mask = True

    ref_flat = ref.reshape(-1)
    rec_flat = rec.reshape(-1)
    mask_flat = mask.reshape(-1)
    energy = float(np.sum(np.abs(ref_flat) ** 2))
    finite = bool(np.all(np.isfinite(ref_flat)) and np.all(np.isfinite(rec_flat)))
    if not finite or energy <= 0.0:
        return {
            "finite": finite,
            "complex_nmse": None,
            "amplitude_error_rms": None,
            "phase_rms_rad": None,
            "group_delay_residual_ns": None,
            "edge_sample_loss": None,
            "energy_ratio": None,
            "coherence": None,
            "sample_count": int(ref_flat.size),
            "interior_sample_count": int(np.count_nonzero(mask_flat)),
            "reason": "non_finite_or_zero_reference_energy",
        }

    eval_ref = ref_flat[mask_flat]
    eval_rec = rec_flat[mask_flat]
    eval_energy = float(np.sum(np.abs(eval_ref) ** 2))
    residual = eval_rec - eval_ref
    complex_nmse = float(np.sum(np.abs(residual) ** 2) / max(eval_energy, np.finfo(float).tiny))
    amplitude_error = np.abs(eval_rec) - np.abs(eval_ref)
    amplitude_error_rms = float(np.sqrt(np.mean(amplitude_error * amplitude_error)))
    support = np.abs(eval_ref) > max(np.finfo(float).eps, np.max(np.abs(eval_ref)) * 1.0e-12)
    phase_values = np.angle(eval_rec[support] * np.conj(eval_ref[support])) if np.any(support) else np.empty(0)
    phase_rms = float(np.sqrt(np.mean(phase_values * phase_values))) if phase_values.size else None
    outside_energy = float(np.sum(np.abs(ref_flat[~mask_flat]) ** 2))
    edge_loss = outside_energy / energy if has_boundary_mask else 0.0
    recovered_energy = float(np.sum(np.abs(rec_flat) ** 2))
    energy_ratio = recovered_energy / energy
    correlation = np.vdot(ref_flat, rec_flat)
    coherence = float(abs(correlation) / math.sqrt(energy * max(recovered_energy, np.finfo(float).tiny)))
    sample_rate = _finite_float(fs_hz, "fs_hz")
    if sample_rate <= 0.0:
        raise ValueError("fs_hz must be positive")
    return {
        "finite": True,
        "complex_nmse": complex_nmse,
        "amplitude_error_rms": amplitude_error_rms,
        "phase_rms_rad": phase_rms,
        "group_delay_residual_ns": _group_delay_residual_ns(ref, rec, sample_rate),
        "edge_sample_loss": float(edge_loss),
        "energy_ratio": float(energy_ratio),
        "coherence": coherence,
        "sample_count": int(ref_flat.size),
        "interior_sample_count": int(np.count_nonzero(mask_flat)),
        "reason": "closed_or_boundary_residual_measured",
    }


def _tone(length: int, bin_index: int = 11) -> np.ndarray:
    n = np.arange(length, dtype=np.float64)
    return np.exp(1j * _TWO_PI * bin_index * n / length)


def _lfm(length: int, fs_hz: float) -> np.ndarray:
    n = np.arange(length, dtype=np.float64)
    t = (n - length / 2.0) / fs_hz
    bandwidth = min(18.0e6, 0.3 * fs_hz)
    duration = length / fs_hz
    return np.exp(1j * math.pi * bandwidth / duration * t * t)


def _record(
    level: str,
    delay_ns: float,
    boundary_class: str,
    reference: np.ndarray,
    recovered: np.ndarray,
    *,
    interior_mask: np.ndarray | None,
    fs_hz: float,
    mode: str,
    zero_padding: int = 0,
    channel_indices: str = "none",
    fallback_reason: str = "none",
    correction_location: str,
    fusion_definition: str,
    pulse_compression_model: str,
    case_id: str,
) -> dict[str, object]:
    metrics = closure_metrics(reference, recovered, interior_mask=interior_mask, fs_hz=fs_hz)
    return {
        "level": level,
        "case_id": case_id,
        "delay_ns": float(delay_ns),
        "boundary_class": boundary_class,
        "mode": mode,
        "transform_mode": mode,
        "zero_padding": int(zero_padding),
        "channel_indices": channel_indices,
        "fallback_reason": fallback_reason,
        "correction_location": correction_location,
        "fusion_definition": fusion_definition,
        "pulse_compression_model": pulse_compression_model,
        **metrics,
    }


def _c4_status(production_input: Path | None) -> dict[str, object]:
    if production_input is None:
        return {
            "level": "C4",
            "status": "NOT_EVALUABLE",
            "reason": "production_input_not_supplied",
        }
    path = Path(production_input)
    if not path.is_file():
        return {
            "level": "C4",
            "status": "NOT_EVALUABLE",
            "reason": "production_input_missing",
            "path": str(path),
        }
    if path.suffix.lower() != ".npz":
        return {
            "level": "C4",
            "status": "NOT_EVALUABLE",
            "reason": "production_fixture_must_be_npz_with_reference_and_recovered_arrays",
            "path": str(path),
        }
    try:
        values = np.load(path)
        reference = _validate_signal(values["reference"])
        recovered = _validate_signal(values["recovered"])
        metrics = closure_metrics(reference, recovered)
        return {"level": "C4", "status": "passed", "path": str(path), **metrics}
    except (KeyError, OSError, ValueError, TypeError) as exc:
        return {
            "level": "C4",
            "status": "NOT_EVALUABLE",
            "reason": f"production_fixture_invalid:{exc}",
            "path": str(path),
        }


def run_closure_matrix(
    *,
    delays_ns: Sequence[float] = DEFAULT_DELAYS_NS,
    seed: int = 20260917,
    output_dir: Path,
    fs_hz: float = DEFAULT_FS_HZ,
    production_input: Path | None = None,
) -> dict[str, object]:
    """Run C0–C3 synthetic closure and an explicit C4 production status row."""

    sample_rate = _finite_float(fs_hz, "fs_hz")
    if sample_rate <= 0.0:
        raise ValueError("fs_hz must be positive")
    delays = [float(value) for value in delays_ns]
    if not delays or not all(math.isfinite(value) for value in delays):
        raise ValueError("delays_ns must contain finite values")
    rng = np.random.default_rng(int(seed))
    rows: list[dict[str, object]] = []
    for delay_ns in delays:
        tone = _tone(256)
        delayed_tone = inject_fractional_delay(tone, delay_ns, sample_rate, mode="circular")
        recovered_tone = correct_fractional_delay_exact(
            delayed_tone, delay_ns, sample_rate, mode="circular"
        )
        rows.append(_record(
            "C0", delay_ns, "interior", tone, recovered_tone,
            interior_mask=None, fs_hz=sample_rate, mode="circular",
            correction_location="single_signal", fusion_definition="none", case_id="tone",
            pulse_compression_model="not_applicable",
        ))

        lfm = _lfm(512, sample_rate)
        delayed_lfm = inject_fractional_delay(
            lfm, delay_ns, sample_rate, mode="zero_padded", zero_pad=128
        )
        recovered_lfm = correct_fractional_delay_exact(
            delayed_lfm, delay_ns, sample_rate, mode="zero_padded", zero_pad=128
        )
        lfm_mask = np.zeros(lfm.size, dtype=bool)
        lfm_mask[64:-64] = True
        rows.append(_record(
            "C1", delay_ns, "pulse_boundary", lfm, recovered_lfm,
            interior_mask=lfm_mask, fs_hz=sample_rate, mode="zero_padded",
            zero_padding=128,
            correction_location="single_pulse", fusion_definition="none", case_id="known_lfm",
            pulse_compression_model="raw_lfm_no_matched_filter",
        ))

        clutter = (
            rng.normal(size=(4, 384)) + 1j * rng.normal(size=(4, 384))
        ) / math.sqrt(2.0)
        delayed_clutter = inject_fractional_delay(
            clutter, delay_ns, sample_rate, mode="zero_padded", zero_pad=96
        )
        recovered_clutter = correct_fractional_delay_exact(
            delayed_clutter, delay_ns, sample_rate, mode="zero_padded", zero_pad=96
        )
        clutter_mask = np.zeros(clutter.shape, dtype=bool)
        clutter_mask[..., 48:-48] = True
        rows.append(_record(
            "C2", delay_ns, "packet_boundary", clutter, recovered_clutter,
            interior_mask=clutter_mask, fs_hz=sample_rate, mode="zero_padded",
            zero_padding=96,
            correction_location="per_pulse", fusion_definition="none", case_id="multi_pulse_clutter",
            pulse_compression_model="not_applicable_target_free_clutter",
        ))

        packet_count = 2
        packet_length = 384
        base = (
            rng.normal(size=(packet_count, packet_length, 4))
            + 1j * rng.normal(size=(packet_count, packet_length, 4))
        ) / math.sqrt(2.0)
        impaired = base.copy()
        for channel_index in (1, 3):
            impaired[..., channel_index] = inject_fractional_delay(
                base[..., channel_index], delay_ns, sample_rate,
                mode="zero_padded", zero_pad=96,
            )
        corrected = impaired.copy()
        for channel_index in (1, 3):
            corrected[..., channel_index] = correct_fractional_delay_exact(
                impaired[..., channel_index], delay_ns, sample_rate,
                mode="zero_padded", zero_pad=96,
            )
        reference_f1, reference_f2 = fuse_protocol_channels(base)
        recovered_f1, recovered_f2 = fuse_protocol_channels(corrected)
        fusion_reference = np.concatenate((reference_f1.reshape(-1), reference_f2.reshape(-1)))
        fusion_recovered = np.concatenate((recovered_f1.reshape(-1), recovered_f2.reshape(-1)))
        packet_mask = np.zeros((packet_count, packet_length), dtype=bool)
        packet_mask[:, 48:-48] = True
        fusion_mask = np.concatenate((packet_mask.reshape(-1), packet_mask.reshape(-1)))
        rows.append(_record(
            "C3", delay_ns, "packet_boundary", fusion_reference, fusion_recovered,
            interior_mask=fusion_mask, fs_hz=sample_rate, mode="zero_padded",
            zero_padding=96,
            channel_indices="2,4",
            correction_location="before_fusion_channels_2_and_4",
            fusion_definition="F1=(C1+C3)/2,F2=(C2+C4)/2",
            pulse_compression_model="raw_protocol_iq_no_matched_filter",
            case_id="four_channel_protocol",
        ))

        # The following two rows are diagnostic controls, not production
        # conditions: they isolate whether correction placement or an
        # incompletely corrected fusion pair can explain a residual.
        partial = impaired.copy()
        partial[..., 1] = correct_fractional_delay_exact(
            impaired[..., 1], delay_ns, sample_rate,
            mode="zero_padded", zero_pad=96,
        )
        partial_f1, partial_f2 = fuse_protocol_channels(partial)
        partial_recovered = np.concatenate((partial_f1.reshape(-1), partial_f2.reshape(-1)))
        rows.append(_record(
            "C3", delay_ns, "packet_boundary", fusion_reference, partial_recovered,
            interior_mask=fusion_mask, fs_hz=sample_rate, mode="zero_padded",
            zero_padding=96,
            channel_indices="2",
            correction_location="before_fusion_partial_channel_2",
            fusion_definition="F1=(C1+C3)/2,F2=(C2+C4)/2",
            pulse_compression_model="raw_protocol_iq_no_matched_filter",
            case_id="four_channel_partial_channel",
        ))
        delayed_f2 = inject_fractional_delay(
            reference_f2, delay_ns, sample_rate,
            mode="zero_padded", zero_pad=96,
        )
        corrected_f2 = correct_fractional_delay_exact(
            delayed_f2, delay_ns, sample_rate,
            mode="zero_padded", zero_pad=96,
        )
        after_fusion_recovered = np.concatenate((reference_f1.reshape(-1), corrected_f2.reshape(-1)))
        rows.append(_record(
            "C3", delay_ns, "packet_boundary", fusion_reference, after_fusion_recovered,
            interior_mask=fusion_mask, fs_hz=sample_rate, mode="zero_padded",
            zero_padding=96,
            channel_indices="F2",
            correction_location="after_fusion",
            fusion_definition="F1=(C1+C3)/2,F2=(C2+C4)/2",
            pulse_compression_model="fused_domain_no_matched_filter",
            case_id="fused_domain_control",
        ))

    c4 = _c4_status(production_input)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with (output / "closure_rows.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "schema_version": 1,
        "seed": int(seed),
        "fs_hz": sample_rate,
        "delays_ns": delays,
        "levels": ["C0", "C1", "C2", "C3", "C4"],
        "rows": len(rows),
        "c4": c4,
        "boundary_classes": ["interior", "pulse_boundary", "packet_boundary"],
        "correction_definition": "inject=exp(-j*2*pi*f*tau); exact correction=exp(+j*2*pi*f*tau)",
    }
    (output / "closure_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"levels": manifest["levels"], "rows": rows, "c4": c4, "manifest": manifest}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--delays-ns", default=",".join(str(v) for v in DEFAULT_DELAYS_NS))
    parser.add_argument("--seed", type=int, default=20260917)
    parser.add_argument("--fs-hz", type=float, default=DEFAULT_FS_HZ)
    parser.add_argument("--production-input", type=Path, default=None)
    return parser


def _parse_delays(value: str) -> list[float]:
    result = [float(token.strip()) for token in str(value).split(",") if token.strip()]
    if not result:
        raise ValueError("--delays-ns must not be empty")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run_closure_matrix(
        delays_ns=_parse_delays(args.delays_ns),
        seed=args.seed,
        output_dir=args.output_dir,
        fs_hz=args.fs_hz,
        production_input=args.production_input,
    )
    print(json.dumps(result["manifest"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "closure_metrics",
    "correct_fractional_delay_exact",
    "fuse_protocol_channels",
    "inject_fractional_delay",
    "run_closure_matrix",
]
