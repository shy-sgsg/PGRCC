#!/usr/bin/env python3
"""Generate deterministic Stage2 weak-target velocity and SCNR sweeps."""

import argparse
import json
from pathlib import Path


DEFAULT_SPEEDS = [0.0, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0]
DEFAULT_SNRS = [-10.0, -7.5, -5.0, -2.5, 0.0, 2.5, 5.0, 7.5, 10.0, 12.5]
DEFAULT_SEEDS = [202631, 202632, 202633]


def parse_numbers(value, cast=float):
    return [cast(item.strip()) for item in value.split(",") if item.strip()]


def case_token(value):
    sign = "p" if value >= 0.0 else "n"
    text = f"{abs(value):g}".replace(".", "p")
    return sign + text


def target_motion(radial_speed_mps):
    # Unit EN direction measured from the production beam-50 truth geometry.
    # The signs produce positive truth radial velocity for a positive request.
    return {
        "targets.0.motion.ve_mps": -0.788010753607 * radial_speed_mps,
        "targets.0.motion.vn_mps": -0.615661475326 * radial_speed_mps,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=Path("experiments/p5_weak_target_coarse.json"))
    parser.add_argument("--experiment-id", default="p5_weak_target_coarse_cuda")
    parser.add_argument("--output-root",
                        default="outputs/eval/p5_weak_target_coarse_cuda")
    parser.add_argument("--seeds", default=",".join(map(str, DEFAULT_SEEDS)))
    parser.add_argument("--speeds", default=",".join(map(str, DEFAULT_SPEEDS)))
    parser.add_argument("--snrs", default=",".join(map(str, DEFAULT_SNRS)))
    parser.add_argument("--fixed-snr-db", type=float, default=20.0)
    parser.add_argument("--fixed-speed-mps", type=float, default=5.0)
    parser.add_argument("--pf", type=float, default=1e-8)
    parser.add_argument("--min-points", type=int, default=10,
                        help="minimum connected CFAR cells retained as a detection cluster")
    parser.add_argument("--kaiser-beta", type=float, default=6.0)
    parser.add_argument(
        "--cfar-doppler-circular", choices=("true", "false"), default="true",
        help="use periodic Doppler CFAR/cluster topology (false is legacy edge clipping)")
    parser.add_argument("--strong-small-enable", choices=("true", "false"),
                        default="false")
    parser.add_argument("--strong-small-min-points", type=int, default=3)
    parser.add_argument("--strong-small-peak-db", type=float, default=25.0)
    parser.add_argument(
        "--skip-zero-speed-control", action="store_true",
        help="omit the fixed-SNR zero-speed control when generating an SNR-only matrix")
    args = parser.parse_args()

    seeds = parse_numbers(args.seeds, int)
    speeds = sorted(set(abs(v) for v in parse_numbers(args.speeds)))
    snrs = sorted(set(parse_numbers(args.snrs)))
    cases = []

    for seed in seeds:
        off_id = f"WT_OFF_s{seed}"
        cases.append({
            "case_id": off_id,
            "kind": "stage2",
            "scenario_template":
                "simulator/scenarios/p5_csi/stage2_area_clutter_single_target_seed202606.json",
            "random_seed": seed,
            "sweep_axis": "background",
            "sweep_value": None,
            "scenario_overrides": {
                "targets.0.enabled": False,
                "scene.output_signal_domain": "raw_lfm",
            },
            "metrics": ["p4", "p5"],
        })

        signed_speeds = [] if args.skip_zero_speed_control else [0.0]
        for speed in speeds:
            if speed > 0.0:
                signed_speeds.extend([-speed, speed])
        for speed in sorted(signed_speeds):
            case_id = f"WT_V_{case_token(speed)}_s{seed}"
            overrides = {
                "targets.0.target_id": f"WEAK_V_{case_token(speed)}",
                "targets.0.amplitude.snr_db": args.fixed_snr_db,
                "scene.output_signal_domain": "raw_lfm",
            }
            overrides.update(target_motion(speed))
            cases.append({
                "case_id": case_id,
                "kind": "stage2",
                "scenario_template":
                    "simulator/scenarios/p5_csi/stage2_area_clutter_single_target_seed202606.json",
                "random_seed": seed,
                "paired_off_case_id": off_id,
                "sweep_axis": "radial_velocity_mps",
                "sweep_value": speed,
                "injected_snr_db": args.fixed_snr_db,
                "truth_radial_velocity_mps": speed,
                "p5_allow_censored_target_power": True,
                "scenario_overrides": overrides,
                "metrics": ["p4", "p5"],
            })

        for snr_db in snrs:
            case_id = f"WT_SNR_{case_token(snr_db)}_s{seed}"
            overrides = {
                "targets.0.target_id": f"WEAK_SNR_{case_token(snr_db)}",
                "targets.0.amplitude.snr_db": snr_db,
                "scene.output_signal_domain": "raw_lfm",
            }
            overrides.update(target_motion(args.fixed_speed_mps))
            cases.append({
                "case_id": case_id,
                "kind": "stage2",
                "scenario_template":
                    "simulator/scenarios/p5_csi/stage2_area_clutter_single_target_seed202606.json",
                "random_seed": seed,
                "paired_off_case_id": off_id,
                "sweep_axis": "injected_snr_db",
                "sweep_value": snr_db,
                "injected_snr_db": snr_db,
                "truth_radial_velocity_mps": args.fixed_speed_mps,
                "p5_allow_censored_target_power": True,
                "scenario_overrides": overrides,
                "metrics": ["p4", "p5"],
            })

    matrix = {
        "experiment_id": args.experiment_id,
        "output_root": args.output_root,
        "match_config": "configs/eval/localization_match_config.json",
        "weak_target_design": {
            "seeds": seeds,
            "radial_speed_magnitudes_mps": speeds,
            "injected_snr_db": snrs,
            "velocity_sweep_fixed_snr_db": args.fixed_snr_db,
            "snr_sweep_fixed_radial_velocity_mps": args.fixed_speed_mps,
            "coarse_acceptance": {
                "minimum_pd": 0.9,
                "maximum_median_position_error_m": 10.0,
                "maximum_incremental_false_alarms_per_beam": 1.0,
            },
        },
        "default_xml_overrides": {
            "pf": args.pf,
            "min_points": args.min_points,
            "range_compression_window": "kaiser",
            "range_compression_kaiser_beta": args.kaiser_beta,
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
            "cfar_doppler_circular": args.cfar_doppler_circular == "true",
            "cluster_strong_small_enable": args.strong_small_enable == "true",
            "cluster_strong_small_min_points": args.strong_small_min_points,
            "cluster_strong_small_peak_over_median_db": args.strong_small_peak_db,
            "channel_calibration_enable": 0,
            "runtime_diagnostics_enabled": 1,
        },
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "experiment_id": args.experiment_id,
        "case_count": len(cases),
        "seed_count": len(seeds),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
