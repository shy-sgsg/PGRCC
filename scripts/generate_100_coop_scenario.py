#!/usr/bin/env python3
"""Generate the reproducible Stage2 100-cooperative-target resolution scene."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RANGE_SAMPLE_M = 299_792_458.0 / (2.0 * 60_000_000.0)


def physical_calibration(reference_range_m: float) -> dict:
    """Radar-equation calibration, deliberately without ADC mapping.

    The integer-IQ generator has no volts/count calibration.  Therefore this
    records the radar-equation result in watts/dBm and uses ``snr_db`` only as
    the simulator's *post-compression background-relative* control.  It must
    not be interpreted as an ADC-calibrated sample amplitude.
    """
    c = 299_792_458.0
    wavelength_m = 0.0182
    fc_hz = c / wavelength_m
    r_m = reference_range_m
    eirp_dbm = 94.0
    g_over_t_db_per_k = 5.0
    rcs_m2 = 20.0
    loss_db = 7.3
    signal_bandwidth_hz = 50.0e6
    noise_bandwidth_hz = 50.0e6
    pulse_width_s = 130.0e-6
    pulse_count = 128
    time_bandwidth_product = signal_bandwidth_hz * pulse_width_s
    antenna_azimuth_length_m = 0.508
    tx_azimuth_beamwidth_deg = math.degrees(
        0.886 * wavelength_m / antenna_azimuth_length_m)
    rx_beamwidth_weight = 1.2
    rx_azimuth_beamwidth_deg = tx_azimuth_beamwidth_deg * rx_beamwidth_weight
    # With unchanged elevation pattern, directivity is inversely proportional
    # to azimuth beamwidth.  Apply this receive-pattern loss to G/T.
    rx_beamwidth_gain_adjustment_db = -10.0 * math.log10(rx_beamwidth_weight)
    effective_g_over_t_db_per_k = (
        g_over_t_db_per_k + rx_beamwidth_gain_adjustment_db)
    elevation_beamwidth_deg = None
    sigma0_db = -20.0
    atmospheric_specific_db_per_km = 0.033
    # The supplied coefficient is already a two-way attenuation coefficient.
    atmospheric_two_way_db = atmospheric_specific_db_per_km * r_m / 1000.0
    eirp_w = 10.0 ** ((eirp_dbm - 30.0) / 10.0)
    g_over_t_linear = 10.0 ** (effective_g_over_t_db_per_k / 10.0)
    # EIRP/G-over-T radar equation: this directly yields received C/N before
    # pulse compression, without inventing separate Rx gain or ADC scaling.
    pre_compression_cnr = (
        eirp_w * g_over_t_linear * wavelength_m ** 2 * rcs_m2 /
        ((4.0 * math.pi) ** 3 * r_m ** 4 * 1.380649e-23 * noise_bandwidth_hz *
         10.0 ** ((loss_db + atmospheric_two_way_db) / 10.0)))
    post_compression_thermal_snr_db = 10.0 * math.log10(
        pre_compression_cnr * time_bandwidth_product)
    return {
        "mode": "eirp_g_over_t_radar_equation_no_adc_calibration",
        "eirp_dbm": eirp_dbm,
        "g_over_t_input_db_per_k": g_over_t_db_per_k,
        "rx_beamwidth_weight": rx_beamwidth_weight,
        "rx_beamwidth_gain_adjustment_db": rx_beamwidth_gain_adjustment_db,
        "g_over_t_effective_db_per_k": effective_g_over_t_db_per_k,
        "carrier_frequency_hz": fc_hz,
        "wavelength_m": wavelength_m,
        "system_loss_db": loss_db,
        "signal_bandwidth_hz": signal_bandwidth_hz,
        "equivalent_noise_bandwidth_hz": noise_bandwidth_hz,
        "target_rcs_m2": rcs_m2,
        "antenna_length_m": antenna_azimuth_length_m,
        "antenna_width_m": 0.345,
        "tx_azimuth_3db_beamwidth_deg": tx_azimuth_beamwidth_deg,
        "rx_azimuth_3db_beamwidth_deg": rx_azimuth_beamwidth_deg,
        "elevation_3db_beamwidth_deg": elevation_beamwidth_deg,
        "surface_sigma0_db": sigma0_db,
        "reference_range_m": r_m,
        "pulse_width_s": pulse_width_s,
        "pulse_count": pulse_count,
        "time_bandwidth_product": time_bandwidth_product,
        "atmospheric_specific_attenuation_db_per_km": atmospheric_specific_db_per_km,
        "atmospheric_attenuation_interpretation": "given_as_two_way_specific",
        "atmospheric_two_way_loss_db": atmospheric_two_way_db,
        "pre_compression_cnr_db": 10.0 * math.log10(pre_compression_cnr),
        "post_compression_thermal_snr_db": post_compression_thermal_snr_db,
        "post_coherent_integration_snr_db": 10.0 * math.log10(
            pre_compression_cnr * time_bandwidth_product * pulse_count),
        "adc_calibration": "not_applied",
    }


def target(index: int, beam_id: int, expected_bin: int, snr_db: float,
           azimuth_offset_deg: float, radial_speed_mps: float) -> dict:

    # In this left-looking geometry the EN ground-referenced look vector at
    # theta is (-cos(theta), -sin(theta)).  ``radial_speed_mps`` is the GMTI
    # metric: the target ground velocity component relative to stationary
    # ground clutter.  The waveform generator then naturally combines this
    # absolute target velocity with the platform velocity to form raw Doppler.
    # The Stage2 truth uses the instantaneous slant-look vector.  On this
    # platform/beam geometry its projection is 0.997594 from this EN vector
    # to truth vr.  4.176716 m/s therefore yields the specified 15.000 km/h
    # radial-speed boundary in the emitted truth (rather than 14.964 km/h).
    theta_deg = -60.0 + 2.0 * (beam_id - 1) + azimuth_offset_deg
    angle = math.radians(theta_deg)
    return {
        "target_id": f"COOP_{index + 1:03d}",
        "enabled": True,
        "init": {
            "type": "beam_bin_azimuth_offset",
            "beam_id": beam_id,
            "expected_bin": expected_bin,
            "azimuth_offset_deg": azimuth_offset_deg,
        },
        "motion": {
            "type": "enu_velocity",
            "ve_mps": -radial_speed_mps * math.cos(angle),
            "vn_mps": -radial_speed_mps * math.sin(angle),
        },
        "amplitude": {"type": "snr_db", "snr_db": snr_db},
        "visibility": {
            "type": "hard_gate",
            "single_beam_only": True,
            "visible_beam_span": 0,
        },
    }


def build_targets(snr_db: float, resolution_mode: str, target_count: int,
                  period_count: int = 1,
                  far_range_acceptance: bool = False) -> list[dict]:
    """Return either the ten-target phase-1 case or 100--150 targets.

    Beam 31 contains the explicit resolution boundary pair.  Its range-bin
    separation alone is 4.99654 m, so the second target receives a 0.186 m
    cross-range offset and the true 2D spacing is at least 5 m.  They share
    calibrated radial speeds.  ``opposing`` uses +15/-15 km/h for Doppler-
    assisted separation; ``same`` uses +15/+15 km/h for pure range
    resolution.  All other pairs are kilometres apart in range.
    """
    if target_count == 10:
        # Every target stays in an interior, non-adjacent beam and within the
        # configured Stage2 scene.  The hard gate makes the visibility set
        # invariant across the one-period experiment.
        # Beam 8 has a reproducibly adverse F2 background at the physical
        # SNR calibration.  Keep the 4 m/s boundary target in the clean,
        # interior beam 11 instead; all targets remain single-beam visible.
        beams = (11, 13, 18, 23, 28, 33, 38, 43, 48, 53)
        if far_range_acceptance:
            # Centre the five-scan trajectories inside the range window.  The
            # old one-scan bins placed COOP_001 only 100 bins from the near
            # edge, so platform motion drove it outside the processed swath.
            bins = (1000, 450, 900, 1350, 1800, 2250, 2700, 3150, 3600, 4010)
            # Over five scans the line of sight drifts by about +0.8..0.9 deg
            # for this flight geometry.  Start half a degree before beam
            # centre so every target stays inside one 1.8187-deg beam and is
            # never simultaneously visible in an adjacent 2-deg beam.
            azimuth_offset_deg = -0.5
        elif period_count > 1:
            bins = (500, 700, 1000, 1400, 1700, 2100, 2400, 2700, 3000, 3300)
            azimuth_offset_deg = -0.5
        else:
            bins = (100, 480, 860, 1240, 1620, 2000, 2380, 2760, 3140, 3520)
            azimuth_offset_deg = 0.0
        # The target truth uses the instantaneous slant look vector.  4.01
        # m/s in this EN construction yields 4.00 m/s radial truth here.
        # COOP_010 is the >83 km boundary target.  Its larger negative truth
        # radial speed compensates the platform-induced outward range drift,
        # keeping that target inside the acquired swath for all five scans.
        # COOP_001 remains the 4 m/s minimum-speed target and starts below
        # 76 km with enough near-edge margin for the same five scans.
        speeds = (4.01, -4.4, 4.6, -4.8, 5.0, -5.2, 5.4, -5.6, 5.8, -41.5)
        return [target(i, beam, bin_, snr_db, azimuth_offset_deg, speed)
                for i, (beam, bin_, speed) in enumerate(zip(beams, bins, speeds))]
    if not 100 <= target_count <= 150:
        raise ValueError("target_count must be 10 or in [100, 150]")

    targets: list[dict] = []
    close_range_step_m = 2.0 * RANGE_SAMPLE_M
    close_cross_step_m = math.sqrt(5.0 * 5.0 - close_range_step_m ** 2)
    for beam_id in range(6, 56):
        if beam_id == 31:
            targets.append(target(len(targets), beam_id, 1500, snr_db,
                                  0.0, 4.176716))
            offset_deg = math.degrees(close_cross_step_m / 85_000.0)
            pair_second_vr = -4.176716 if resolution_mode == "opposing" else 4.176716
            targets.append(target(len(targets), beam_id, 1502, snr_db,
                                  offset_deg, pair_second_vr))
            continue
        targets.append(target(len(targets), beam_id, 1000, snr_db, 0.0, 6.0))
        targets.append(target(len(targets), beam_id, 3000, snr_db, 0.0, -9.0))
    assert len(targets) == 100
    # The optional candidates are deliberately far from the two base targets
    # in their beam.  They allow a detection-based down-selection without
    # weakening the required 5 m resolution pair.
    for beam_id in range(6, 56):
        if len(targets) >= target_count:
            break
        targets.append(target(len(targets), beam_id, 4000, snr_db, 0.0, 8.0))
    assert len(targets) == target_count
    return targets


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--period-count", type=int, default=1)
    parser.add_argument(
        "--system-parameters", type=Path,
        default=REPO_ROOT / "configs/stage2/gmti_far_system_parameters_20260818.json",
        help="广域GMTI远距离系统参数（由指定Excel提取）")
    parser.add_argument("--ddc-acquired-samples", type=int)
    parser.add_argument("--ddc-padded-samples", type=int)
    parser.add_argument("--iq-domain-scale", type=float, default=1.0,
                        help="numeric scene scale before int16 quantization; does not alter SNR")
    parser.add_argument("--snr-db", type=float, default=40.0)
    parser.add_argument("--resolution-pair-snr-db", type=float,
                        help="optional post-compression SNR override for COOP_051/COOP_052")
    parser.add_argument(
        "--target-snr-calibration", type=Path,
        help=("CSV，至少包含target_id及recommended_injected_snr_db或"
              "injected_snr_db；用于逐目标校准处理后SCNR"))
    parser.add_argument("--target-count", type=int, default=100)
    parser.add_argument("--include-target-ids", type=Path,
                        help="newline-delimited IDs to retain from the generated candidate set")
    parser.add_argument("--simulation-mode",
                        choices=("relative", "physical_calibrated"),
                        default="relative")
    parser.add_argument("--resolution-mode", choices=("opposing", "same"),
                        default="opposing")
    args = parser.parse_args()
    system_parameters = json.loads(args.system_parameters.read_text(encoding="utf-8"))
    if system_parameters.get("source", {}).get("mode") != "广域GMTI（远距离）":
        raise SystemExit("--system-parameters必须是广域GMTI（远距离）参数")
    workbook = system_parameters["workbook_values"]
    derived = system_parameters["derived_processing"]
    non_workbook = system_parameters["non_workbook_inputs"]
    if args.ddc_acquired_samples is None:
        args.ddc_acquired_samples = int(workbook["acquired_samples"])
    if args.ddc_padded_samples is None:
        args.ddc_padded_samples = int(workbook["fpga_uploaded_samples"])
    if args.period_count < 1:
        raise SystemExit("--period-count must be positive")
    if args.ddc_acquired_samples <= 0 or args.ddc_padded_samples < args.ddc_acquired_samples:
        raise SystemExit("invalid DDC acquired/padded sample count")
    if args.ddc_padded_samples % 64 != 0:
        raise SystemExit("--ddc-padded-samples must be a multiple of 64")
    if not math.isfinite(args.iq_domain_scale) or args.iq_domain_scale <= 0.0:
        raise SystemExit("--iq-domain-scale must be positive")
    if args.target_count != 10 and not 100 <= args.target_count <= 150:
        raise SystemExit("--target-count must be 10 or in [100, 150]")

    template = REPO_ROOT / "simulator/scenarios/four_channel_three_targets_5period.run.json"
    scenario = json.loads(template.read_text(encoding="utf-8"))
    scenario["case_id"] = ("stage2_phase1_physical_10_targets"
                           if args.target_count == 10 else
                           "stage2_cooperative_100_5m_15kmh")
    scenario["output_dir"] = args.output_dir
    scenario["random"]["period_count"] = args.period_count
    # Keep target reference geometry anchored at the first scan when the
    # simulator emits several independent period files.  The algorithm
    # replays each file as a fresh local period; anchoring geometry to the
    # emitted period would otherwise make single-period calibration data and
    # the corresponding full-scan data differ in range/Doppler truth.
    scenario["random"]["target_reference_period"] = 0
    scenario["random"]["random_seed"] = 20260727
    scenario["waveform"]["pulse_len"] = args.ddc_padded_samples
    scenario["waveform"]["acquired_pulse_len"] = args.ddc_acquired_samples
    scenario["waveform"]["bandwidth_mhz"] = float(workbook["bandwidth_mhz"])
    scenario["waveform"]["fs_mhz"] = float(workbook["ddc_sample_rate_mhz"])
    scenario["waveform"]["tr_us"] = float(workbook["pulse_width_us"])
    scenario["waveform"]["prf_hz"] = float(workbook["prf_hz"])
    scenario["waveform"]["pulse_num"] = int(non_workbook["pulse_count_per_beam"])
    scenario["waveform"]["fc_ghz"] = 299_792_458.0 / 0.0182 / 1.0e9
    # Use the supplied aperture formula for the simulated transmit pattern;
    # receive pattern is recorded separately in the physical contract.
    scenario["scan"]["beam_width_deg"] = math.degrees(0.886 * 0.0182 / 0.508)
    scenario["scan"]["beam_count"] = int(non_workbook["beam_count"])
    scenario["scan"]["scan_min_deg"] = float(non_workbook["scan_min_deg"])
    scenario["scan"]["scan_step_deg"] = float(non_workbook["scan_step_deg"])
    scenario["range_processing"]["range_fft_len"] = int(derived["range_fft_len"])
    scenario["range_processing"]["range_crop_start"] = int(derived["range_crop_start"])
    scenario["range_processing"]["range_crop_len"] = int(derived["range_compress_len"])
    scenario["range_processing"]["sample_delay_us"] = float(workbook["receive_delay_us"])
    scenario["scene"]["range_min_m"] = float(derived["nearest_slant_range_m"])
    scenario["scene"]["range_max_m"] = float(derived["farthest_slant_range_m"])
    crop_start = scenario["range_processing"]["range_crop_start"]
    fs_hz = scenario["waveform"]["fs_mhz"] * 1.0e6
    sample_delay_s = scenario["range_processing"]["sample_delay_us"] * 1.0e-6
    compressed_range_min_m = 0.5 * 299_792_458.0 * (
        sample_delay_s + crop_start / fs_hz)
    # expected_bin=100 is used as the phase-1 reference point.  In the
    # no-ADC physical mode, the radar-equation post-compression thermal SNR
    # supplies the simulator's normalized target/background ratio.
    reference_range_m = compressed_range_min_m + 100.0 * RANGE_SAMPLE_M
    calibration = (physical_calibration(reference_range_m)
                   if args.simulation_mode == "physical_calibrated" else None)
    target_snr_db = (calibration["post_compression_thermal_snr_db"]
                     if calibration else args.snr_db)
    # Normalized simulator background.  Its target/background ratio is
    # controlled by ``target_snr_db``; absolute watts are documented above.
    scenario["scene"]["clutter_amplitude_scale"] = args.iq_domain_scale
    scenario["scene"]["area_clutter"]["mean_power"] = 1.0e-4
    scenario["scene"]["thermal_noise"]["noise_power"] = (
        1.0e-5 * args.iq_domain_scale * args.iq_domain_scale)
    candidates = build_targets(target_snr_db, args.resolution_mode,
                               args.target_count, args.period_count,
                               far_range_acceptance=int(derived["range_crop_start"]) == 0)
    if args.include_target_ids:
        selected_ids = {
            line.strip() for line in args.include_target_ids.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        scenario["targets"] = [item for item in candidates if item["target_id"] in selected_ids]
        missing_ids = selected_ids - {item["target_id"] for item in candidates}
        if missing_ids:
            raise SystemExit("unknown selected target IDs: " + ", ".join(sorted(missing_ids)))
    else:
        scenario["targets"] = candidates
    calibration_by_target: dict[str, float] = {}
    calibration_by_target_period: dict[str, dict[int, float]] = {}
    if args.target_snr_calibration:
        with args.target_snr_calibration.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                target_id = str(row.get("target_id", "")).strip()
                # The evaluator emits both the amplitude used in the current
                # run and the recommended amplitude for the next iteration.
                # Prefer the recommendation so its CSV can be fed back
                # directly without a lossy/manual conversion step.
                calibrated_value = row.get("recommended_injected_snr_db")
                if calibrated_value in (None, ""):
                    calibrated_value = row.get("injected_snr_db", "")
                try:
                    injected_snr_db = float(calibrated_value)
                except (TypeError, ValueError) as exc:
                    raise SystemExit(f"无效的逐目标SCNR校准行：{row}") from exc
                if not target_id or not math.isfinite(injected_snr_db):
                    raise SystemExit(f"无效的逐目标SCNR校准行：{row}")
                raw_period = str(row.get("period_id", "")).strip()
                if raw_period:
                    try:
                        period_id = int(raw_period)
                    except ValueError as exc:
                        raise SystemExit(f"无效的逐周期SCNR校准行：{row}") from exc
                    bucket = calibration_by_target_period.setdefault(target_id, {})
                    if period_id in bucket:
                        raise SystemExit(f"重复的逐周期SCNR校准行：{target_id}/{period_id}")
                    bucket[period_id] = injected_snr_db
                else:
                    if target_id in calibration_by_target:
                        raise SystemExit(f"重复的逐目标SCNR校准行：{target_id}")
                    calibration_by_target[target_id] = injected_snr_db
        generated_ids = {item["target_id"] for item in scenario["targets"]}
        if calibration_by_target_period:
            if calibration_by_target:
                raise SystemExit("不能混用逐目标与逐目标/周期SCNR校准行")
            if set(calibration_by_target_period) != generated_ids:
                raise SystemExit("逐目标/周期SCNR校准必须覆盖所有目标ID")
            expected_periods = set(range(args.period_count))
            for item in scenario["targets"]:
                target_id = item["target_id"]
                schedule = calibration_by_target_period[target_id]
                if set(schedule) != expected_periods:
                    raise SystemExit(
                        f"目标{target_id}的校准周期必须为0..{args.period_count - 1}")
                values = [schedule[period] for period in range(args.period_count)]
                item["amplitude"]["snr_db"] = values[0]
                item["amplitude"]["snr_db_by_period"] = values
        else:
            if set(calibration_by_target) != generated_ids:
                raise SystemExit("逐目标SCNR校准必须与本次生成的目标ID完全一致")
            for item in scenario["targets"]:
                item["amplitude"]["snr_db"] = calibration_by_target[item["target_id"]]
    if args.resolution_pair_snr_db is not None:
        for item in scenario["targets"]:
            if item["target_id"] in {"COOP_051", "COOP_052"}:
                item["amplitude"]["snr_db"] = args.resolution_pair_snr_db
    scenario["design_contract"] = {
        "target_count": len(scenario["targets"]),
        "candidate_target_count": args.target_count,
        "minimum_neighbour_spacing_m": 5.0 if args.target_count >= 100 else None,
        "minimum_radial_speed_kmh": 15.0 if args.target_count >= 100 else None,
        "simulation_mode": args.simulation_mode,
        "amplitude_control": (
            "per_target_period_output_scnr_calibration"
            if calibration_by_target_period else
            "per_target_output_scnr_calibration"
            if calibration_by_target else "common_simulator_snr_seed"),
        "common_simulator_snr_seed_db": target_snr_db,
        "required_output_scnr_db": 12.0,
        "resolution_group": (
            "COOP_051/COOP_052 are 5 m apart at " +
            ("+/-15 km/h radial speed" if args.resolution_mode == "opposing"
             else "+15/+15 km/h radial speed")) if args.target_count >= 100 else None,
        "single_visible_beams": ("11,13,18,23,28,33,38,43,48,53"
                                 if args.target_count == 10 else
                                 "6..55; every target is constrained to its reference beam"),
        "no_beam_edge": True,
        "no_cross_beam_in_all_periods": True,
        "target_reference_period": 0,
        "ddc_acquired_samples": args.ddc_acquired_samples,
        "ddc_padded_samples": args.ddc_padded_samples,
        "fpga_padding_samples": args.ddc_padded_samples - args.ddc_acquired_samples,
        "system_parameters": system_parameters,
        "system_parameters_file": str(args.system_parameters.resolve()),
        "range_window_boundary_targets": {
            "near_target": "COOP_001", "near_target_expected_bin": 1000,
            "near_target_requirement": "truth slant range < 76 km",
            "far_target": "COOP_010", "far_target_expected_bin": 4010,
            "far_target_requirement": "truth slant range > 83 km"
        } if args.target_count == 10 and int(derived["range_crop_start"]) == 0 else None,
        "iq_domain_scale": args.iq_domain_scale,
        "iq_domain_scale_note": "numeric pre-int16 scale; SNR ratios unchanged; not ADC calibration",
    }
    if args.resolution_pair_snr_db is not None:
        scenario["design_contract"]["resolution_pair_post_compression_snr_db"] = (
            args.resolution_pair_snr_db)
    if calibration:
        scenario["design_contract"]["physical_calibration"] = calibration
    if args.include_target_ids:
        scenario["design_contract"]["selected_target_ids_file"] = str(args.include_target_ids)
    if args.target_snr_calibration:
        scenario["design_contract"]["target_snr_calibration_file"] = str(
            args.target_snr_calibration.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(scenario, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({
        "scenario": str(args.output),
        "target_count": len(scenario["targets"]),
        "period_count": args.period_count,
        "resolution_mode": args.resolution_mode,
        "simulation_mode": args.simulation_mode,
        "target_snr_db": target_snr_db,
        "range_sample_m": RANGE_SAMPLE_M,
        "neighbour_spacing_m": 5.0 if args.target_count >= 100 else None,
        "minimum_radial_speed_kmh": 15.0 if args.target_count >= 100 else None,
        "ddc_acquired_samples": args.ddc_acquired_samples,
        "ddc_padded_samples": args.ddc_padded_samples,
        "fpga_padding_samples": args.ddc_padded_samples - args.ddc_acquired_samples,
        "system_parameters": str(args.system_parameters.resolve()),
        "range_crop_start": scenario["range_processing"]["range_crop_start"],
        "range_compress_len": scenario["range_processing"]["range_crop_len"],
        "iq_domain_scale": args.iq_domain_scale,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
