#!/usr/bin/env python3
"""Quantify four-channel carrier compensation residuals from Stage2 truth."""

import argparse
import csv
import json
import math
from pathlib import Path


C = 299792458.0


def wrap_pi(value: float) -> float:
    return math.atan2(math.sin(value), math.cos(value))


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    index = q * (len(ordered) - 1)
    lo = int(math.floor(index))
    hi = int(math.ceil(index))
    weight = index - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth-pulse", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    waveform = cfg["waveform"]
    geometry = cfg["channel_geometry"]
    offsets = []
    for channel in range(1, 5):
        item = geometry[f"channel_{channel}"]
        offsets.append((float(item["x_m"]), float(item["y_m"]), float(item["z_m"])))
    fs_hz = float(waveform["fs_mhz"]) * 1e6
    wavelength = C / (float(waveform["fc_ghz"]) * 1e9)
    sample_delay_s = float(cfg["range_processing"]["sample_delay_us"]) * 1e-6
    side = -1.0 if int(cfg["platform"]["squint_side"]) == 1 else 1.0
    carrier_sign = -1.0

    residual_13: list[float] = []
    residual_24: list[float] = []
    gain_13: list[float] = []
    gain_24: list[float] = []
    by_target: dict[str, list[float]] = {}
    with args.truth_pulse.open(newline="", encoding="utf-8") as src:
        for row in csv.DictReader(src):
            if row["injection_enabled"] != "1":
                continue
            n = float(row["echo_delay_sample_center_used"])
            slant_range = 0.5 * C * (sample_delay_s + n / fs_hz)
            height = float(row["platform_z_m"]) - float(row["target_z"])
            horizontal = math.sqrt(max(0.0, slant_range * slant_range - height * height))
            theta = math.radians(float(row["theta_cmd_deg"]))
            target = (side * horizontal * math.sin(theta),
                      side * horizontal * math.cos(theta), -height)

            def rx_path(offset: tuple[float, float, float]) -> float:
                return math.sqrt(sum((target[i] - offset[i]) ** 2 for i in range(3)))

            comp13 = carrier_sign * 2.0 * math.pi / wavelength * (
                rx_path(offsets[0]) - rx_path(offsets[2]))
            comp24 = carrier_sign * 2.0 * math.pi / wavelength * (
                rx_path(offsets[1]) - rx_path(offsets[3]))
            r13 = wrap_pi(float(row["phase_ch3_rad"]) + comp13 - float(row["phase_ch1_rad"]))
            r24 = wrap_pi(float(row["phase_ch4_rad"]) + comp24 - float(row["phase_ch2_rad"]))
            residual_13.append(abs(r13))
            residual_24.append(abs(r24))
            gain_13.append(abs(1.0 + complex(math.cos(r13), math.sin(r13))))
            gain_24.append(abs(1.0 + complex(math.cos(r24), math.sin(r24))))
            by_target.setdefault(row["target_id"], []).extend((abs(r13), abs(r24)))

    def summary(values: list[float]) -> dict:
        return {
            "count": len(values),
            "min": min(values),
            "mean": sum(values) / len(values),
            "p05": percentile(values, 0.05),
            "p50": percentile(values, 0.50),
            "p95": percentile(values, 0.95),
            "max": max(values),
        }

    result = {
        "model": "reader beam-centre/range-dependent carrier compensation evaluated at truth echo centre",
        "residual_abs_rad_ch1_ch3": summary(residual_13),
        "residual_abs_rad_ch2_ch4": summary(residual_24),
        "coherent_pair_sum_gain_ch1_ch3": summary(gain_13),
        "coherent_pair_sum_gain_ch2_ch4": summary(gain_24),
        "per_target_residual_abs_rad": {key: summary(value) for key, value in by_target.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
