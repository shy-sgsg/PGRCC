#!/usr/bin/env python3
"""Build mechanism features and a transparent rule diagnosis table."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


def finite(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def classify(row: dict[str, Any]) -> str:
    if finite(row.get("clutter_energy"), math.nan) <= 0.0:
        return "H0_no_clutter"
    coherence = finite(row.get("channel_coherence"), 1.0)
    if coherence < 0.5:
        return "H0_low_coherence"
    m1_slope = abs(finite(row.get("phase_vs_frequency_slope_rad_per_hz"), 0.0))
    m1_r2 = finite(row.get("phase_vs_frequency_r2"), 0.0)
    m2_slope = abs(finite(row.get("pulse_phase_slope_rad_per_pulse"), 0.0))
    m2_curvature = abs(finite(row.get("pulse_phase_curvature_rad_per_pulse2"), 0.0))
    if m1_r2 >= 0.7 and m1_slope >= 1.0e-10:
        return "delay_like"
    if m2_slope >= 1.0e-3 or m2_curvature >= 1.0e-4:
        return "phase_drift_like"
    if coherence < 0.85 or finite(row.get("deterministic_phase_confidence"), 1.0) < 0.4:
        return "decorrelation_like"
    return "unknown_mixed"


def from_summary(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    name = path.name
    if "channel_delay" in name:
        method = data.get("methods", {}).get("D3_Huber_weighted_LS", {})
        return {
            "source": str(path), "mechanism_label": "M1",
            "phase_vs_frequency_slope_rad_per_hz": finite(method.get("phase_slope_rad_per_hz")),
            "phase_vs_frequency_r2": finite(method.get("r2")),
            "wideband_residual_rad": finite(method.get("rmse_rad")),
            "pulse_phase_slope_rad_per_pulse": 0.0,
            "pulse_phase_curvature_rad_per_pulse2": 0.0,
            "channel_coherence": 1.0,
            "deterministic_phase_confidence": finite(method.get("confidence")),
            "clutter_energy": 1.0,
        }
    if "temporal_phase" in name:
        method = data.get("methods", {}).get("P1_robust_constant_linear", {})
        quadratic = data.get("methods", {}).get("P2_robust_quadratic", {})
        coeff = quadratic.get("coefficients") or []
        return {
            "source": str(path), "mechanism_label": "M2",
            "phase_vs_frequency_slope_rad_per_hz": 0.0,
            "phase_vs_frequency_r2": 0.0,
            "wideband_residual_rad": 0.0,
            "pulse_phase_slope_rad_per_pulse": finite(method.get("slope_rad_per_pulse")),
            "pulse_phase_curvature_rad_per_pulse2": finite(coeff[2] if len(coeff) > 2 else 0.0),
            "channel_coherence": 1.0,
            "deterministic_phase_confidence": finite(method.get("confidence")),
            "clutter_energy": 1.0,
        }
    methods = data.get("oracle_methods", [])
    current = next((item for item in methods if item.get("method") == "O1_Current"), {})
    return {
        "source": str(path), "mechanism_label": "M3",
        "phase_vs_frequency_slope_rad_per_hz": 0.0,
        "phase_vs_frequency_r2": 0.0,
        "wideband_residual_rad": finite(current.get("phase_rmse_rad")),
        "pulse_phase_slope_rad_per_pulse": 0.0,
        "pulse_phase_curvature_rad_per_pulse2": 0.0,
        "channel_coherence": finite(data.get("current", {}).get("coherence"), math.nan),
        "deterministic_phase_confidence": finite(data.get("current", {}).get("coherence"), math.nan),
        "clutter_energy": 1.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for path in args.summary:
        row = from_summary(path.resolve())
        row["diagnosis"] = classify(row)
        rows.append(row)
    fields = ["source", "mechanism_label", "phase_vs_frequency_slope_rad_per_hz",
              "phase_vs_frequency_r2", "wideband_residual_rad",
              "pulse_phase_slope_rad_per_pulse", "pulse_phase_curvature_rad_per_pulse2",
              "channel_coherence", "deterministic_phase_confidence", "clutter_energy",
              "diagnosis"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"output": str(args.output.resolve()), "row_count": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
