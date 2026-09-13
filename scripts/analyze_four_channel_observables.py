#!/usr/bin/env python3
"""Read four-channel protocol IQ and form auditable pair observables.

The module deliberately stays deterministic and model-based.  It does not
train a network or select an algorithm.  Its purpose is to expose the six
pairwise cross-channel observables needed by the unknown-system-error pilot
and to fit a simple angle-dependent phase model when the data support it.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

import numpy as np


C = 299_792_458.0
HEADER_BYTES = 256
DEFAULT_CHANNEL_X_M = (-0.085, 0.085, -0.085, 0.085)
PAIR_DEFINITIONS: Tuple[Tuple[str, int, int], ...] = (
    ("C13", 0, 2),
    ("C24", 1, 3),
    ("C12", 0, 1),
    ("C14", 0, 3),
    ("C23", 1, 2),
    ("C34", 2, 3),
)
HORIZONTAL_PAIRS = ("C12", "C14", "C23", "C34")


def _wrap_phase(value: float) -> float:
    """Return a phase in [-pi, pi]."""

    return math.atan2(math.sin(value), math.cos(value))


def _phase_or_none(value: complex) -> Optional[float]:
    if abs(value) == 0.0 or not (math.isfinite(value.real) and math.isfinite(value.imag)):
        return None
    return float(math.atan2(value.imag, value.real))


def _validate_channel_x(channel_x_m: Sequence[float]) -> np.ndarray:
    x = np.asarray(channel_x_m, dtype=np.float64)
    if x.shape != (4,) or not np.all(np.isfinite(x)):
        raise ValueError("channel_x_m must contain four finite values")
    return x


def compute_pair_observables(
    channels: np.ndarray,
    channel_x_m: Sequence[float] = DEFAULT_CHANNEL_X_M,
) -> Dict[str, object]:
    """Compute C13, C24, C12, C14, C23 and C34 for one PRT.

    Cross-correlation uses ``channel_i * conj(channel_j)``.  The additional
    ``right_minus_left_phase_rad`` field puts horizontal pairs into one
    geometric sign convention, independent of the pair's storage order.
    """

    samples = np.asarray(channels)
    if samples.ndim != 2 or samples.shape[1] != 4:
        raise ValueError("channels must have shape (sample_count, 4)")
    if not np.iscomplexobj(samples):
        samples = samples.astype(np.complex128)
    x = _validate_channel_x(channel_x_m)

    pairs: Dict[str, Dict[str, object]] = {}
    for name, i, j in PAIR_DEFINITIONS:
        a = samples[:, i]
        b = samples[:, j]
        valid = (
            np.isfinite(a.real)
            & np.isfinite(a.imag)
            & np.isfinite(b.real)
            & np.isfinite(b.imag)
        )
        a_valid = a[valid]
        b_valid = b[valid]
        cross = np.sum(a_valid * np.conj(b_valid), dtype=np.complex128)
        power_a = float(np.sum(np.abs(a_valid) ** 2, dtype=np.float64))
        power_b = float(np.sum(np.abs(b_valid) ** 2, dtype=np.float64))
        denominator = math.sqrt(power_a * power_b)
        coherence: Optional[float]
        if denominator > 0.0 and math.isfinite(denominator):
            coherence = float(np.clip(abs(cross) / denominator, 0.0, 1.0))
        else:
            coherence = None
        phase = _phase_or_none(complex(cross))
        if phase is None:
            right_minus_left = None
        elif x[i] > x[j]:
            right_minus_left = _wrap_phase(phase)
        elif x[i] < x[j]:
            right_minus_left = _wrap_phase(-phase)
        else:
            right_minus_left = None
        pairs[name] = {
            "channel_i": i + 1,
            "channel_j": j + 1,
            "effective_sample_count": int(np.count_nonzero(valid)),
            "cross_real": float(cross.real),
            "cross_imag": float(cross.imag),
            "cross_magnitude": float(abs(cross)),
            "coherence": coherence,
            "phase_rad": phase,
            "right_minus_left_phase_rad": right_minus_left,
            "x_delta_m": float(x[j] - x[i]),
        }

    c13 = pairs["C13"]["phase_rad"]
    c12 = pairs["C12"]["phase_rad"]
    c23 = pairs["C23"]["phase_rad"]
    if c13 is None or c12 is None or c23 is None:
        closure = None
    else:
        closure = _wrap_phase(float(c12) + float(c23) - float(c13))
    return {
        "pairs": pairs,
        "closure_phase_C12_C23_minus_C13_rad": closure,
    }


def fit_angle_phase_model(
    theta_deg: Sequence[float],
    phase_rad: Sequence[float],
    weights: Optional[Sequence[float]] = None,
) -> Dict[str, object]:
    """Fit ``phase = slope * sin(theta) + intercept`` with phase unwrapping."""

    theta = np.asarray(theta_deg, dtype=np.float64).reshape(-1)
    phase = np.asarray(phase_rad, dtype=np.float64).reshape(-1)
    if theta.shape != phase.shape:
        raise ValueError("theta_deg and phase_rad must have the same length")
    if weights is None:
        weight = np.ones(theta.shape, dtype=np.float64)
    else:
        weight = np.asarray(weights, dtype=np.float64).reshape(-1)
        if weight.shape != theta.shape:
            raise ValueError("weights must have the same length as theta_deg")
    valid = np.isfinite(theta) & np.isfinite(phase) & np.isfinite(weight) & (weight > 0.0)
    theta = theta[valid]
    phase = phase[valid]
    weight = weight[valid]
    result: Dict[str, object] = {
        "fit_status": "underdetermined",
        "n_beams": int(theta.size),
        "slope_rad_per_sin_theta": None,
        "intercept_rad": None,
        "rmse_rad": None,
        "r2": None,
        "confidence": {"status": "not_estimable"},
    }
    if theta.size < 2:
        return result
    x = np.sin(np.deg2rad(theta))
    if np.unique(np.round(x, decimals=12)).size < 2:
        return result
    order = np.argsort(x, kind="mergesort")
    x_sorted = x[order]
    y_sorted = np.unwrap(phase[order])
    w_sorted = weight[order]
    design = np.column_stack((x_sorted, np.ones(x_sorted.shape, dtype=np.float64)))
    sqrt_w = np.sqrt(w_sorted)
    weighted_design = design * sqrt_w[:, None]
    weighted_y = y_sorted * sqrt_w
    if np.linalg.matrix_rank(weighted_design) < 2:
        return result
    coefficients, _, _, _ = np.linalg.lstsq(weighted_design, weighted_y, rcond=None)
    predicted = design @ coefficients
    residual = y_sorted - predicted
    weight_sum = float(np.sum(w_sorted))
    rmse = math.sqrt(float(np.sum(w_sorted * residual * residual)) / weight_sum)
    centered = y_sorted - float(np.sum(w_sorted * y_sorted) / weight_sum)
    total = float(np.sum(w_sorted * centered * centered))
    r2 = None if total == 0.0 else 1.0 - float(np.sum(w_sorted * residual * residual)) / total
    result.update(
        {
            "fit_status": "fit",
            "slope_rad_per_sin_theta": float(coefficients[0]),
            "intercept_rad": _wrap_phase(float(coefficients[1])),
            "rmse_rad": float(rmse),
            "r2": None if r2 is None else float(r2),
            "confidence": {
                "status": "quality_fields_only",
                "n_beams": int(theta.size),
                "weight_sum": weight_sum,
                "rmse_rad": float(rmse),
                "r2": None if r2 is None else float(r2),
                "not_a_calibrated_probability": True,
            },
        }
    )
    return result


def estimate_baseline_error(
    fit: Mapping[str, object],
    fc_hz: float,
    nominal_baseline_m: float = 0.0,
) -> Optional[float]:
    """Convert a fitted phase slope to a baseline error in metres."""

    slope = fit.get("slope_rad_per_sin_theta")
    if fit.get("fit_status") != "fit" or slope is None or not math.isfinite(float(slope)):
        return None
    if not math.isfinite(fc_hz) or fc_hz <= 0.0:
        raise ValueError("fc_hz must be positive and finite")
    return float(float(slope) * C / (2.0 * math.pi * fc_hz) - nominal_baseline_m)


def _iq_dtype(iq_data_type: str) -> np.dtype:
    normalized = iq_data_type.lower()
    if normalized in {"int16", "iq_int16", "short", "s16"}:
        return np.dtype("<i2")
    if normalized in {"float32", "float", "f32"}:
        return np.dtype("<f4")
    raise ValueError(f"unsupported iq_data_type: {iq_data_type}")


def iter_raw_packets(
    path: Path | str,
    pulse_len: int,
    channel_count: int,
    iq_data_type: str,
) -> Iterator[Dict[str, object]]:
    """Yield validated packets from a stage2 new-protocol binary file."""

    path = Path(path)
    if pulse_len <= 0 or channel_count <= 0:
        raise ValueError("pulse_len and channel_count must be positive")
    dtype = _iq_dtype(iq_data_type)
    scalar_bytes = dtype.itemsize
    packet_bytes = HEADER_BYTES + pulse_len * channel_count * 2 * scalar_bytes
    with path.open("rb") as stream:
        packet_index = 0
        while True:
            header = stream.read(HEADER_BYTES)
            if not header:
                break
            if len(header) != HEADER_BYTES:
                raise ValueError(f"truncated protocol header at packet {packet_index}")
            declared_bytes = struct.unpack_from("<I", header, 9)[0]
            if declared_bytes != packet_bytes:
                raise ValueError(
                    f"packet {packet_index} declares {declared_bytes} bytes; "
                    f"expected {packet_bytes} for the supplied layout"
                )
            payload = stream.read(packet_bytes - HEADER_BYTES)
            if len(payload) != packet_bytes - HEADER_BYTES:
                raise ValueError(f"truncated protocol payload at packet {packet_index}")
            values = np.frombuffer(payload, dtype=dtype)
            expected_values = pulse_len * channel_count * 2
            if values.size != expected_values:
                raise ValueError(f"invalid payload scalar count at packet {packet_index}")
            iq = values.reshape(pulse_len, channel_count, 2)
            channels = iq[:, :, 0].astype(np.float64) + 1j * iq[:, :, 1].astype(np.float64)
            if not np.all(np.isfinite(channels)):
                raise ValueError(f"non-finite IQ value at packet {packet_index}")
            theta_cmd_deg = struct.unpack_from("<h", header, 218)[0] / 100.0
            prt_counter = struct.unpack_from("<I", header, 20)[0]
            yield {
                "packet_index": packet_index,
                "prt_counter": int(prt_counter),
                "theta_cmd_deg": float(theta_cmd_deg),
                "channels": channels,
            }
            packet_index += 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _beam_rows_from_accumulator(
    accumulator: Mapping[Tuple[int, str], Dict[str, object]],
    channel_x_m: Sequence[float],
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    x = _validate_channel_x(channel_x_m)
    for (beam_index, pair_name), value in sorted(accumulator.items()):
        cross = complex(value["cross"])
        phase = _phase_or_none(cross)
        _, i, j = next(item for item in PAIR_DEFINITIONS if item[0] == pair_name)
        if phase is None:
            right_minus_left = None
        elif x[i] > x[j]:
            right_minus_left = _wrap_phase(phase)
        elif x[i] < x[j]:
            right_minus_left = _wrap_phase(-phase)
        else:
            right_minus_left = None
        rows.append(
            {
                "beam_index": beam_index,
                "theta_cmd_deg": float(value["theta_cmd_deg"]),
                "pair": pair_name,
                "effective_sample_count": int(value["sample_count"]),
                "cross_real": float(cross.real),
                "cross_imag": float(cross.imag),
                "cross_magnitude": float(abs(cross)),
                "phase_rad": phase,
                "right_minus_left_phase_rad": right_minus_left,
                "coherence": (
                    None
                    if float(value["denominator"]) <= 0.0
                    else float(np.clip(abs(cross) / float(value["denominator"]), 0.0, 1.0))
                ),
            }
        )
    return rows


def analyze_file(
    path: Path | str,
    pulse_len: int,
    channel_count: int,
    iq_data_type: str,
    fc_hz: float,
    pulses_per_beam: int = 1,
    channel_x_m: Sequence[float] = DEFAULT_CHANNEL_X_M,
    output_dir: Optional[Path | str] = None,
) -> Dict[str, object]:
    """Analyze one raw file and optionally write packet/beam CSV artifacts."""

    path = Path(path)
    if channel_count != 4:
        raise ValueError("four-channel observables require channel_count=4")
    if pulses_per_beam <= 0:
        raise ValueError("pulses_per_beam must be positive")
    packet_rows: List[Dict[str, object]] = []
    accumulator: Dict[Tuple[int, str], Dict[str, object]] = {}
    packet_count = 0
    sample_count = 0
    for packet in iter_raw_packets(path, pulse_len, channel_count, iq_data_type):
        packet_index = int(packet["packet_index"])
        beam_index = packet_index // pulses_per_beam
        pulse_index = packet_index % pulses_per_beam
        observable = compute_pair_observables(packet["channels"], channel_x_m)
        packet_row: Dict[str, object] = {
            "packet_index": packet_index,
            "beam_index": beam_index,
            "pulse_index": pulse_index,
            "prt_counter": int(packet["prt_counter"]),
            "theta_cmd_deg": float(packet["theta_cmd_deg"]),
            "closure_phase_C12_C23_minus_C13_rad": observable[
                "closure_phase_C12_C23_minus_C13_rad"
            ],
        }
        sample_count += int(packet["channels"].shape[0])
        for pair_name, pair in observable["pairs"].items():
            for field in (
                "effective_sample_count",
                "cross_real",
                "cross_imag",
                "cross_magnitude",
                "coherence",
                "phase_rad",
                "right_minus_left_phase_rad",
            ):
                packet_row[f"{pair_name}_{field}"] = pair[field]
            key = (beam_index, pair_name)
            current = accumulator.setdefault(
                key,
                {
                    "theta_cmd_deg": float(packet["theta_cmd_deg"]),
                    "sample_count": 0,
                    "cross": 0.0 + 0.0j,
                    "denominator": 0.0,
                },
            )
            current["sample_count"] = int(current["sample_count"]) + int(
                pair["effective_sample_count"]
            )
            current["cross"] = complex(current["cross"]) + complex(
                float(pair["cross_real"]), float(pair["cross_imag"])
            )
            # The denominator is additive only as an approximation to the
            # pooled coherence; it is exact for the pooled channel powers.
            channels = packet["channels"]
            _, i, j = next(item for item in PAIR_DEFINITIONS if item[0] == pair_name)
            valid = np.isfinite(channels[:, i].real) & np.isfinite(channels[:, i].imag)
            valid &= np.isfinite(channels[:, j].real) & np.isfinite(channels[:, j].imag)
            current["denominator"] = float(current["denominator"]) + math.sqrt(
                float(np.sum(np.abs(channels[valid, i]) ** 2))
                * float(np.sum(np.abs(channels[valid, j]) ** 2))
            )
        packet_rows.append(packet_row)
        packet_count += 1

    beam_rows = _beam_rows_from_accumulator(accumulator, channel_x_m)
    pair_summary: Dict[str, Dict[str, object]] = {}
    for pair_name in (item[0] for item in PAIR_DEFINITIONS):
        selected = [row for row in packet_rows if row[f"{pair_name}_coherence"] is not None]
        coherences = [float(row[f"{pair_name}_coherence"]) for row in selected]
        phases = [
            float(row[f"{pair_name}_right_minus_left_phase_rad"])
            for row in selected
            if row[f"{pair_name}_right_minus_left_phase_rad"] is not None
        ]
        pair_summary[pair_name] = {
            "packet_count_with_power": len(selected),
            "mean_coherence": None if not coherences else float(np.mean(coherences)),
            "mean_abs_phase_rad": None if not phases else float(np.mean(np.abs(phases))),
        }

    fit_by_pair: Dict[str, Dict[str, object]] = {}
    for pair_name in HORIZONTAL_PAIRS:
        selected = [
            row
            for row in beam_rows
            if row["pair"] == pair_name
            and row["right_minus_left_phase_rad"] is not None
        ]
        fit_by_pair[pair_name] = fit_angle_phase_model(
            [row["theta_cmd_deg"] for row in selected],
            [row["right_minus_left_phase_rad"] for row in selected],
            [max(1.0, float(row["effective_sample_count"])) for row in selected],
        )

    summary: Dict[str, object] = {
        "schema": "four_channel_observables_v1",
        "input": {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "pulse_len": pulse_len,
            "channel_count": channel_count,
            "iq_data_type": iq_data_type,
            "fc_hz": fc_hz,
            "pulses_per_beam": pulses_per_beam,
            "channel_x_m": [float(value) for value in channel_x_m],
        },
        "packet_count": packet_count,
        "sample_count_total": sample_count,
        "beam_count": 0 if not beam_rows else max(int(row["beam_index"]) for row in beam_rows) + 1,
        "pairs": pair_summary,
        "angle_phase_fit_by_pair": fit_by_pair,
        "beam_rows": beam_rows,
    }
    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        _write_csv(out / "pair_observables.csv", packet_rows)
        _write_csv(out / "beam_observables.csv", beam_rows)
        with (out / "observable_summary.json").open("w", encoding="utf-8") as stream:
            json.dump(summary, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
    summary.pop("beam_rows", None)
    return summary


def paired_phase_difference(
    reference_path: Path | str,
    compared_path: Path | str,
    pulse_len: int,
    channel_count: int,
    iq_data_type: str,
    pulses_per_beam: int,
    channel_x_m: Sequence[float] = DEFAULT_CHANNEL_X_M,
) -> Dict[str, object]:
    """Compare matched raw files using all horizontal pair observables."""

    reference = iter_raw_packets(reference_path, pulse_len, channel_count, iq_data_type)
    compared = iter_raw_packets(compared_path, pulse_len, channel_count, iq_data_type)
    accum: Dict[Tuple[int, str], Dict[str, object]] = {}
    reference_count = 0
    for reference_packet, compared_packet in zip(reference, compared):
        if reference_packet["prt_counter"] != compared_packet["prt_counter"]:
            raise ValueError("paired files have different PRT counters")
        if abs(float(reference_packet["theta_cmd_deg"]) - float(compared_packet["theta_cmd_deg"])) > 1.0e-6:
            raise ValueError("paired files have different beam angles")
        beam_index = int(reference_packet["packet_index"]) // pulses_per_beam
        reference_obs = compute_pair_observables(reference_packet["channels"], channel_x_m)
        compared_obs = compute_pair_observables(compared_packet["channels"], channel_x_m)
        for pair_name in HORIZONTAL_PAIRS:
            ref_pair = reference_obs["pairs"][pair_name]
            cur_pair = compared_obs["pairs"][pair_name]
            ref_phase = ref_pair["right_minus_left_phase_rad"]
            cur_phase = cur_pair["right_minus_left_phase_rad"]
            if ref_phase is None or cur_phase is None:
                continue
            weight = float(
                min(
                    int(ref_pair["effective_sample_count"]),
                    int(cur_pair["effective_sample_count"]),
                )
            )
            ref_coh = ref_pair["coherence"]
            cur_coh = cur_pair["coherence"]
            if ref_coh is not None and cur_coh is not None:
                weight *= max(0.0, min(float(ref_coh), float(cur_coh)))
            if weight <= 0.0:
                continue
            delta = _wrap_phase(float(cur_phase) - float(ref_phase))
            key = (beam_index, pair_name)
            current = accum.setdefault(
                key,
                {
                    "theta_cmd_deg": float(reference_packet["theta_cmd_deg"]),
                    "z": 0.0 + 0.0j,
                    "weight": 0.0,
                },
            )
            current["z"] = complex(current["z"]) + weight * complex(
                math.cos(delta), math.sin(delta)
            )
            current["weight"] = float(current["weight"]) + weight
        reference_count += 1
    try:
        next(reference)
        raise ValueError("paired files have different packet counts")
    except StopIteration:
        pass
    try:
        next(compared)
        raise ValueError("paired files have different packet counts")
    except StopIteration:
        pass

    rows: List[Dict[str, object]] = []
    for (beam_index, pair_name), value in sorted(accum.items()):
        z = complex(value["z"])
        weight = float(value["weight"])
        rows.append(
            {
                "beam_index": beam_index,
                "theta_cmd_deg": float(value["theta_cmd_deg"]),
                "pair": pair_name,
                "phase_error_rad": _phase_or_none(z),
                "resultant_length": None if weight <= 0.0 else float(abs(z) / weight),
                "weight": weight,
            }
        )
    fit_by_pair: Dict[str, Dict[str, object]] = {}
    for pair_name in HORIZONTAL_PAIRS:
        selected = [
            row
            for row in rows
            if row["pair"] == pair_name and row["phase_error_rad"] is not None
        ]
        fit_by_pair[pair_name] = fit_angle_phase_model(
            [row["theta_cmd_deg"] for row in selected],
            [row["phase_error_rad"] for row in selected],
            [row["weight"] for row in selected],
        )
    selected_phase = [
        abs(float(row["phase_error_rad"]))
        for row in rows
        if row["phase_error_rad"] is not None
    ]
    return {
        "schema": "paired_four_channel_phase_difference_v1",
        "reference_path": str(reference_path),
        "compared_path": str(compared_path),
        "packet_count": reference_count,
        "rows": rows,
        "fit_by_pair": fit_by_pair,
        "mean_abs_phase_error_rad": None
        if not selected_phase
        else float(np.mean(selected_phase)),
    }


def _write_csv(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fieldnames: List[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _load_cli_scenario(path: Optional[Path]) -> Dict[str, object]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pulse-len", type=int)
    parser.add_argument("--channel-count", type=int, default=4)
    parser.add_argument("--iq-data-type")
    parser.add_argument("--fc-hz", type=float)
    parser.add_argument("--pulses-per-beam", type=int)
    args = parser.parse_args(argv)
    scenario = _load_cli_scenario(args.scenario)
    waveform = scenario.get("waveform", {})
    pulse_len = args.pulse_len or int(waveform.get("pulse_len", 0))
    channel_count = args.channel_count or int(waveform.get("new_protocol_channel_count", 4))
    iq_data_type = args.iq_data_type or str(waveform.get("iq_data_type", "float32"))
    fc_hz = args.fc_hz or float(waveform.get("fc_ghz", 16.0)) * 1.0e9
    pulses_per_beam = args.pulses_per_beam or int(waveform.get("pulse_num", 1))
    if pulse_len <= 0:
        parser.error("pulse length must be supplied directly or in --scenario")
    summary = analyze_file(
        args.input,
        pulse_len,
        channel_count,
        iq_data_type,
        fc_hz,
        pulses_per_beam=pulses_per_beam,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
