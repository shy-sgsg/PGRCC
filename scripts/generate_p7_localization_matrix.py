#!/usr/bin/env python3
"""Generate the traceable raw-LFM P7 localization smoke/sweep matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


TEMPLATE = (
    "simulator/scenarios/p5_csi/"
    "stage2_area_clutter_single_target_seed202606.json"
)
RADIAL_E = -0.788010753607
RADIAL_N = -0.615661475326


def token(value: float) -> str:
    return ("p" if value >= 0.0 else "n") + str(abs(value)).replace(".", "p")


def target_overrides(target_id: str, speed: float, beam: int = 50,
                     offset_deg: float = 0.0, expected_bin: int = 2048,
                     snr_db: float = -34.0) -> dict:
    return {
        "scene.output_signal_domain": "raw_lfm",
        "random.beam_start": beam,
        "random.beam_count": 1,
        "targets.0.target_id": target_id,
        "targets.0.init.beam_id": beam,
        "targets.0.init.expected_bin": expected_bin,
        "targets.0.init.azimuth_offset_deg": offset_deg,
        "targets.0.visibility.type": "gaussian",
        "targets.0.motion.ve_mps": RADIAL_E * speed,
        "targets.0.motion.vn_mps": RADIAL_N * speed,
        "targets.0.amplitude.snr_db": snr_db,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path,
        default=Path("experiments/p7_localization_rawlfm_smoke.json"))
    parser.add_argument("--experiment-id", default="p7_localization_rawlfm_smoke")
    parser.add_argument(
        "--output-root", default="outputs/eval/p7_localization_rawlfm_smoke")
    parser.add_argument("--seeds", default="202681,202682,202683")
    args = parser.parse_args()
    seeds = [int(value) for value in args.seeds.split(",") if value.strip()]
    cases = []

    for seed in seeds:
        off = target_overrides("P7_BACKGROUND_OFF", 0.0)
        off["targets.0.enabled"] = False
        cases.append({
            "case_id": f"P7_OFF_s{seed}", "kind": "stage2",
            "scenario_template": TEMPLATE, "random_seed": seed,
            "scenario_class": "background_target_off", "sweep_axis": "background",
            "sweep_value": None, "scenario_overrides": off, "metrics": ["p4"],
        })
        for speed in (-50.0, -40.0, -30.0, -20.0, -10.0, -5.0, -1.0,
                      0.0, 1.0, 5.0, 10.0, 20.0, 30.0, 40.0, 50.0):
            cases.append({
                "case_id": f"P7_SPEED_{token(speed)}_s{seed}",
                "kind": "stage2", "scenario_template": TEMPLATE,
                "random_seed": seed, "scenario_class": "speed_sweep",
                "sweep_axis": "radial_velocity_mps", "sweep_value": speed,
                "truth_radial_velocity_mps": speed,
                "scenario_overrides": target_overrides(
                    f"P7_SPEED_{token(speed)}", speed),
                "metrics": ["p4", "p6"],
            })

    beam_width_deg = 2.28
    for normalized in (-1.10, -1.00, -0.90, -0.75, -0.50, -0.25,
                       0.0, 0.25, 0.50, 0.75, 0.90, 1.00, 1.10):
        offset_deg = normalized * beam_width_deg
        cases.append({
            "case_id": f"P7_OFFSET_{token(normalized)}",
            "kind": "stage2", "scenario_template": TEMPLATE,
            "random_seed": 202684, "scenario_class": "beam_offset_sweep",
            "sweep_axis": "beam_offset_normalized", "sweep_value": normalized,
            "beam_offset_normalized": normalized,
            "scenario_overrides": target_overrides(
                f"P7_OFFSET_{token(normalized)}", 5.0,
                offset_deg=offset_deg, snr_db=-30.0),
            "metrics": ["p4", "p6"],
        })

    for beam in (10, 30, 50):
        cases.append({
            "case_id": f"P7_BEAM_{beam:02d}", "kind": "stage2",
            "scenario_template": TEMPLATE, "random_seed": 202685,
            "scenario_class": "beam_position", "sweep_axis": "beam_id",
            "sweep_value": beam,
            "scenario_overrides": target_overrides(
                f"P7_BEAM_{beam:02d}", 5.0, beam=beam, snr_db=-30.0),
            "metrics": ["p4", "p6"],
        })

    for label, expected_bin in (
            ("crop_left", 64), ("near", 320), ("mid", 2048),
            ("far", 3776), ("crop_right", 4032)):
        cases.append({
            "case_id": f"P7_RANGE_{label}", "kind": "stage2",
            "scenario_template": TEMPLATE, "random_seed": 202686,
            "scenario_class": "range_position", "sweep_axis": "expected_bin",
            "sweep_value": expected_bin,
            "scenario_overrides": target_overrides(
                f"P7_RANGE_{label}", 5.0, expected_bin=expected_bin,
                snr_db=-30.0),
            "metrics": ["p4", "p6"],
        })

    matrix = {
        "experiment_id": args.experiment_id,
        "output_root": args.output_root,
        "match_config": "configs/eval/localization_match_config.json",
        "localization_design": {
            "seeds": seeds,
            "speed_limit_mps": 50.0,
            "beam_width_deg": beam_width_deg,
            "position_priority": (
                "exact CTDR/p38 positioning remains valid when the "
                "single-period velocity ambiguity status is unresolved"),
            "final_speed_source": "multi_period_track_displacement_over_elapsed_time",
        },
        "default_xml_overrides": {
            "pf": 1e-8,
            "min_points": 3,
            "cluster_strong_small_enable": False,
            "range_compression_window": "kaiser",
            "range_compression_kaiser_beta": 6.0,
            "range_compression_bandwidth_scale": 1.0,
            "azimuth_fft_window": "none",
            "ati_vmax_mps": 50.0,
            "motion_comp_enable": 1,
            "motion_comp_apply_to_localization": 1,
            "motion_comp_solver": "root1d",
            "motion_comp_use_row_doppler": 1,
            "velocity_ambiguity_enable": 1,
            "velocity_search_min_mps": -50.0,
            "velocity_search_max_mps": 50.0,
            "velocity_max_doppler_order": 8,
            "velocity_max_phase_order": 32,
            "velocity_max_candidates": 512,
            "velocity_beam_gate_deg": 4.56,
            "csi_detection_band_mode": "union",
            "csi_channel_alignment_mode": "none",
            "cfar_doppler_circular": True,
            "channel_calibration_enable": 0,
            "runtime_diagnostics_enabled": 1,
        },
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps({
        "output": str(args.output), "case_count": len(cases),
        "seed_count": len(seeds),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
