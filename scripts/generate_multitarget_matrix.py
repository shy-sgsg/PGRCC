#!/usr/bin/env python3
"""Generate deterministic Stage2 dense multi-target production evaluations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random


TEMPLATE = (
    "simulator/scenarios/p5_csi/"
    "stage2_area_clutter_single_target_seed202606.json"
)
RADIAL_E = -0.788010753607
RADIAL_N = -0.615661475326


def parse_numbers(text, cast):
    return [cast(item.strip()) for item in text.split(",") if item.strip()]


def target(target_id, expected_bin, radial_speed_mps, snr_db,
           azimuth_offset_deg=0.0):
    return {
        "target_id": target_id,
        "enabled": True,
        "init": {
            "type": "beam_bin_azimuth_offset",
            "beam_id": 50,
            "expected_bin": int(expected_bin),
            "azimuth_offset_deg": float(azimuth_offset_deg),
        },
        "motion": {
            "type": "enu_velocity",
            "ve_mps": RADIAL_E * float(radial_speed_mps),
            "vn_mps": RADIAL_N * float(radial_speed_mps),
        },
        "amplitude": {"type": "snr_db", "snr_db": float(snr_db)},
        "visibility": {"type": "hard_gate"},
    }


def scaling_master(max_count, snr_db):
    if max_count < 1:
        return []
    lo, hi = 320, 3776
    bins = [
        int(round(lo + (hi - lo) * index / max(1, max_count - 1)))
        for index in range(max_count)
    ]
    # Prefixes must still cover the whole range swath.  A fixed permutation
    # makes 1/8/16/32/64 cases nested and reproducible without clustering all
    # early targets at near range.
    random.Random(20260715).shuffle(bins)
    speeds = (-5.0, 5.0, -3.0, 3.0)
    offsets = (-0.8, -0.4, 0.0, 0.4, 0.8)
    return [
        target(
            f"DENSE_{index + 1:03d}", bins[index],
            speeds[index % len(speeds)], snr_db,
            offsets[index % len(offsets)],
        )
        for index in range(max_count)
    ]


def stress_cases(snr_db):
    cases = []

    cases.append((
        "M01_diff_range_same_velocity",
        "different_range_same_velocity",
        [target(f"M01_{i + 1:02d}", 640 + 400 * i, 5.0, snr_db,
                (-0.6, -0.2, 0.2, 0.6)[i % 4]) for i in range(8)],
        "resolvable",
    ))
    cases.append((
        "M02_same_range_diff_doppler",
        "same_range_different_doppler",
        [target(f"M02_{i + 1:02d}", 2048, speed, snr_db, 0.0)
         for i, speed in enumerate((-5.0, -4.0, -3.0, -2.0,
                                    2.0, 3.0, 4.0, 5.0))],
        "resolvable_in_doppler",
    ))
    close_bins = (1000, 1002, 1500, 1504, 2000, 2008, 2500, 2516)
    cases.append((
        "M03_close_range_same_doppler",
        "close_range_same_doppler",
        [target(f"M03_{i + 1:02d}", bin_index, 5.0, snr_db, 0.0)
         for i, bin_index in enumerate(close_bins)],
        "partially_resolvable_resolution_stress",
    ))
    mixed_snr = (-40.0, -36.0, -34.0, -30.0)
    mixed_bins = [420 + int(round(i * 3200 / 15)) for i in range(16)]
    cases.append((
        "M05_weak_mixed_16",
        "weak_mixed_velocity",
        [target(f"M05_{i + 1:02d}", mixed_bins[i],
                (-5.0, 5.0, -3.0, 3.0)[i % 4], mixed_snr[i % 4],
                (-0.75, -0.25, 0.25, 0.75)[i % 4])
         for i in range(16)],
        "resolvable_with_near_threshold_targets",
    ))
    cases.append((
        "M_UNOBS_colocated_identical",
        "co_located_same_doppler",
        [target("M_UNOBS_01", 2048, 5.0, snr_db, 0.0),
         target("M_UNOBS_02", 2048, 5.0, snr_db, 0.0)],
        "single_observation_unresolvable",
    ))
    return cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=Path("experiments/p7_multitarget_smoke.json"))
    parser.add_argument("--experiment-id", default="p7_multitarget_smoke_cuda")
    parser.add_argument("--output-root",
                        default="outputs/eval/p7_multitarget_smoke_cuda")
    parser.add_argument("--counts", default="1,8,16,32,64")
    parser.add_argument("--seeds", default="202651")
    parser.add_argument("--snr-db", type=float, default=-34.0)
    parser.add_argument("--min-points", type=int, default=10)
    parser.add_argument("--strong-small-enable", choices=("true", "false"),
                        default="false")
    parser.add_argument("--strong-small-min-points", type=int, default=3)
    parser.add_argument("--strong-small-peak-db", type=float, default=25.0)
    parser.add_argument("--suite", choices=("scaling", "stress", "all"),
                        default="all")
    parser.add_argument(
        "--p5-debug", action="store_true",
        help="add a paired target-off case and enable production CSI matrix taps")
    args = parser.parse_args()

    counts = sorted(set(parse_numbers(args.counts, int)))
    if not counts or counts[0] < 1 or counts[-1] > 256:
        raise SystemExit("counts must contain integers in [1,256]")
    seeds = parse_numbers(args.seeds, int)
    master = scaling_master(max(counts), args.snr_db)
    cases = []
    for seed in seeds:
        off_case_id = f"MT_OFF_s{seed}"
        if args.p5_debug:
            off_target = dict(master[0])
            off_target["enabled"] = False
            cases.append({
                "case_id": off_case_id,
                "kind": "stage2",
                "scenario_template": TEMPLATE,
                "random_seed": seed,
                "scenario_class": "background_target_off",
                "target_count": 0,
                "expected_observability": "no_target",
                "scenario_overrides": {
                    "targets": [off_target],
                    "scene.output_signal_domain": "raw_lfm",
                },
                "metrics": ["p4", "p5"],
            })
        if args.suite in ("scaling", "all"):
            for count in counts:
                case = {
                    "case_id": f"MT_SCALE_n{count:03d}_s{seed}",
                    "kind": "stage2",
                    "scenario_template": TEMPLATE,
                    "random_seed": seed,
                    "scenario_class": "separated_dense_scaling",
                    "target_count": count,
                    "expected_observability": "resolvable",
                    "scenario_overrides": {
                        "targets": master[:count],
                        "scene.output_signal_domain": "raw_lfm",
                    },
                    "metrics": ["p4", "p5"] if args.p5_debug else ["p4"],
                }
                if args.p5_debug:
                    case["paired_off_case_id"] = off_case_id
                    case["p5_allow_censored_target_power"] = True
                cases.append(case)
        if args.suite in ("stress", "all"):
            for case_id, scenario_class, targets, observability in stress_cases(
                    args.snr_db):
                case = {
                    "case_id": f"{case_id}_s{seed}",
                    "kind": "stage2",
                    "scenario_template": TEMPLATE,
                    "random_seed": seed,
                    "scenario_class": scenario_class,
                    "target_count": len(targets),
                    "expected_observability": observability,
                    "scenario_overrides": {
                        "targets": targets,
                        "scene.output_signal_domain": "raw_lfm",
                    },
                    "metrics": ["p4", "p5"] if args.p5_debug else ["p4"],
                }
                if args.p5_debug:
                    case["paired_off_case_id"] = off_case_id
                    case["p5_allow_censored_target_power"] = True
                cases.append(case)

    matrix = {
        "experiment_id": args.experiment_id,
        "output_root": args.output_root,
        "match_config": "configs/eval/localization_match_config.json",
        "multi_target_design": {
            "counts": counts,
            "seeds": seeds,
            "scaling_injected_snr_db": args.snr_db,
            "target_amplitude_reference":
                "common target-free per-pulse clutter/noise packet",
            "truth_matching": "rectangular_hungarian_one_to_one",
        },
        "default_xml_overrides": {
            "pf": 1e-8,
            "min_points": args.min_points,
            "cluster_strong_small_enable": args.strong_small_enable == "true",
            "cluster_strong_small_min_points": args.strong_small_min_points,
            "cluster_strong_small_peak_over_median_db": args.strong_small_peak_db,
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
    maximum_target_count = max(
        (int(case.get("target_count", 0)) for case in cases), default=0)
    matrix["multi_target_design"]["maximum_target_count"] = (
        maximum_target_count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "experiment_id": args.experiment_id,
        "case_count": len(cases),
        "maximum_target_count": maximum_target_count,
        "seeds": seeds,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
