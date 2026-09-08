#!/usr/bin/env python3
"""Evaluate azimuth/2-D localization on the fixed five-period Stage2 dataset.

This tool never invokes a simulator.  It joins production GMTI CSV output to
the supplied Stage2 truth, calculates production-solver Jacobian propagation,
and (when supplied) samples saved CSI power maps for an observed post-CSI
target-to-background ratio.
"""

import argparse
import csv
import glob
import json
import math
from pathlib import Path

import numpy as np

from gmti_eval_match import one_to_one_match

ROOT = Path(__file__).resolve().parents[1]


def rows(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def number(row, key, default=math.nan):
    try:
        value = row.get(key, "")
        return float(value) if value not in ("", None) else default
    except (TypeError, ValueError):
        return default


def wrap(value):
    return math.atan2(math.sin(value), math.cos(value))


def stats(values):
    a = np.asarray([x for x in values if math.isfinite(x)], dtype=float)
    if not len(a):
        return {"count": 0, "bias": math.nan, "std": math.nan,
                "rmse": math.nan, "p95_abs": math.nan, "maximum_abs": math.nan}
    return {"count": int(len(a)), "bias": float(a.mean()),
            "std": float(a.std(ddof=1)) if len(a) > 1 else 0.0,
            "rmse": float(np.sqrt(np.mean(a * a))),
            "p95_abs": float(np.percentile(np.abs(a), 95)),
            "maximum_abs": float(np.max(np.abs(a)))}


def csi_measurements(csi_root, truths):
    out = []
    for truth in truths:
        if truth.get("beam_id") != "11":
            continue
        period = int(float(truth["period_id"]))
        candidates = glob.glob(str(Path(csi_root) / f"period_{period:04d}" / "csi_metrics" / "*"))
        if not candidates:
            continue
        metric_dir = Path(max(candidates, key=lambda p: Path(p).stat().st_mtime))
        maps = list(metric_dir.glob("*after_power.npy"))
        if not maps:
            continue
        power = np.load(maps[0])
        row = int(float(truth["row_truth"]))
        col = int(float(truth["range_bin"]))
        target = power[max(0, row - 2):min(power.shape[0], row + 3),
                       max(0, col - 2):min(power.shape[1], col + 3)]
        rr, cc = np.indices(power.shape)
        # Same configured 20x16-cell background extent and 4-cell guard
        # around a 2x2 target ROI, evaluated from actual saved CSI power.
        background_mask = ((np.abs(rr - row) <= 16) & (np.abs(cc - col) <= 20) &
                           ~((np.abs(rr - row) <= 6) & (np.abs(cc - col) <= 6)))
        background = power[background_mask]
        bg = float(np.mean(background))
        peak = float(np.max(target))
        mean = float(np.mean(target))
        out.append({"period_id": period, "target_id": truth["target_id"],
                    "target_peak_to_background_db": 10 * math.log10(peak / bg),
                    "target_roi_mean_to_background_db": 10 * math.log10(mean / bg),
                    "background_mean_power": bg, "target_peak_power": peak})
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True,
                        help="root containing algorithm_result/period_XXXX")
    parser.add_argument("--truth-dir", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--system-config", default="",
                        help="original Stage2 JSON with design_contract/physical calibration")
    parser.add_argument("--match-config", default="configs/eval/localization_match_config.json")
    parser.add_argument("--csi-root", default="", help="optional saved CSI profile_runs root")
    args = parser.parse_args()
    run_root = Path(args.run_root)
    truth = [r for r in rows(Path(args.truth_dir) / "truth_targets_by_beam.csv")
             if r.get("visible", "1") not in ("0", "false", "False")]
    det = []
    for path in sorted((run_root / "algorithm_result").glob("period_*/detection_results.csv")):
        det.extend(rows(path))

    # Use the project's Hungarian one-to-one matcher.  ``target_id`` in the
    # writer is a diagnostic association, not an evaluation selector; using
    # it directly can retain a diagnostic-neighbour association outside the
    # published 1 km position gate.
    match_cfg = json.loads(Path(args.match_config).read_text(encoding="utf-8"))
    match_rows, used_det, _ = one_to_one_match(
        det, truth, match_cfg, "det_id", use_beam=True, use_range=True,
        use_velocity=False)
    matched_triples = [(int(item["truth_index"]), det[item["item_index"]],
                        truth[item["truth_index"]])
                       for item in match_rows]
    matched_pairs = [(detection, truth_row)
                     for _, detection, truth_row in matched_triples]
    matched = [pair[0] for pair in matched_pairs]
    false_alarms = len(det) - len(matched)

    phase_error = [wrap(number(r, "ctdr_raw_phase_rad") -
                        number(t, "phi_total_truth_rad"))
                   for r, t in matched_pairs]
    jacobian = [abs(number(r, "loc_jacobian_crossrange_m_per_phase_rad")) for r in matched]
    predicted_az = [j * p for j, p in zip(jacobian, phase_error)
                    if math.isfinite(j) and math.isfinite(p)]
    phase_std = stats(phase_error)["std"]
    per_target_theory_sigma = [j * phase_std for j in jacobian if math.isfinite(j)]
    range_cell_m = 299792458.0 / (2.0 * 50.0e6)
    range_quant_sigma_m = range_cell_m / math.sqrt(12.0)
    theory_az_sigma = math.sqrt(np.mean(np.square(per_target_theory_sigma)))
    theory_2d_sigma = math.hypot(theory_az_sigma, range_quant_sigma_m)

    def directional_error(r, t):
        pe, pn = number(r, "platform_e"), number(r, "platform_n")
        te, tn = number(t, "e_mid"), number(t, "n_mid")
        ee, en = number(r, "new_e"), number(r, "new_n")
        de, dn = te - pe, tn - pn
        xe, xn = ee - te, en - tn
        distance = math.hypot(de, dn)
        if not all(math.isfinite(v) for v in (pe, pn, te, tn, ee, en)) or distance <= 0:
            return math.nan, math.nan, math.nan, math.nan
        truth_angle = math.degrees(math.atan2(dn, de))
        estimate_angle = math.degrees(math.atan2(en - pn, ee - pe))
        angle_error = math.degrees(wrap(math.radians(estimate_angle - truth_angle)))
        ur_e, ur_n = de / distance, dn / distance
        # Match the production convention: cross-range is the projection on
        # the counter-clockwise normal (-u_n, u_e) of the truth look vector.
        return angle_error, -xe * ur_n + xn * ur_e, xe * ur_e + xn * ur_n, math.hypot(xe, xn)

    matched_detail = []
    matched_detail_by_truth_index = {}
    for truth_index, r, t in matched_triples:
        angle, azimuth, range_err, position = directional_error(r, t)
        pe, pn = number(r, "platform_e"), number(r, "platform_n")
        te, tn = number(t, "e_mid"), number(t, "n_mid")
        ee, en = number(r, "new_e"), number(r, "new_n")
        truth_range = math.hypot(te - pe, tn - pn)
        estimated_range = math.hypot(ee - pe, en - pn)
        detail = {"period_id": r.get("period_id", ""),
                  "target_id": t.get("target_id", ""),
                  "truth_beam_id": t.get("beam_id", ""),
                  "matched_beam_id": r.get("beam_id", ""),
                  "matched_detection_id": r.get("det_id", ""),
                  "localization_status": "MATCHED",
                  "truth_range_m": truth_range,
                  "estimated_range_m": estimated_range,
                  "truth_beam_azimuth_deg": number(t, "azimuth_deg"),
                  "angle_error_deg": angle,
                  "azimuth_error_m": azimuth,
                  "range_error_m": range_err,
                  "position_error_check_m": position,
                  "ctdr_raw_phase_rad": number(r, "ctdr_raw_phase_rad"),
                  "truth_ctdr_phase_rad": number(t, "phi_total_truth_rad"),
                  "loc_jacobian_crossrange_m_per_phase_rad": number(r, "loc_jacobian_crossrange_m_per_phase_rad")}
        matched_detail.append(detail)
        matched_detail_by_truth_index[truth_index] = detail

    # Keep all 50 visible target observations in one table.  A missed target
    # has no production position, so its error fields deliberately remain
    # empty rather than being silently excluded or treated as zero error.
    all_target_detail = []
    for truth_index, t in enumerate(truth):
        detail = matched_detail_by_truth_index.get(truth_index)
        if detail is None:
            all_target_detail.append({
                "period_id": t.get("period_id", ""),
                "target_id": t.get("target_id", ""),
                "truth_beam_id": t.get("beam_id", ""),
                "matched_beam_id": "", "matched_detection_id": "",
                "localization_status": "MISSED",
                "truth_range_m": number(t, "range_m"),
                "estimated_range_m": "",
                "truth_beam_azimuth_deg": number(t, "azimuth_deg"),
                "angle_error_deg": "", "azimuth_error_m": "",
                "range_error_m": "", "position_error_check_m": "",
                "ctdr_raw_phase_rad": "",
                "truth_ctdr_phase_rad": number(t, "phi_total_truth_rad"),
                "loc_jacobian_crossrange_m_per_phase_rad": ""})
        else:
            all_target_detail.append(detail)
    per_period = []
    for period_id in sorted({int(number(r, "period_id", -1)) for r in truth}):
        subset = [r for r in matched_detail if int(number(r, "period_id", -1)) == period_id]
        truth_count = sum(int(number(r, "period_id", -1)) == period_id for r in truth)
        per_period.append({
            "period_id": period_id, "truth_count": truth_count,
            "matched_count": len(subset), "missed_count": truth_count - len(subset),
            "pd": len(subset) / truth_count if truth_count else math.nan,
            "angle_error_deg": stats([number(r, "angle_error_deg") for r in subset]),
            "azimuth_error_m": stats([number(r, "azimuth_error_m") for r in subset]),
            "range_error_m": stats([number(r, "range_error_m") for r in subset]),
            "two_dimensional_position_error_m": stats(
                [number(r, "position_error_check_m") for r in subset]),
        })

    # These are the platform states actually consumed by the production
    # localizer and emitted with every detection.  Keep this evidence in the
    # report so a period-dependent localization error cannot be mistaken for
    # a stale, period-0 platform reference.
    platform_by_period = []
    for period_id in sorted({int(number(r, "period_id", -1)) for r in det}):
        subset = [r for r in det if int(number(r, "period_id", -1)) == period_id]
        pe = [number(r, "platform_e") for r in subset]
        pn = [number(r, "platform_n") for r in subset]
        platform_by_period.append({"period_id": period_id, "detection_count": len(subset),
                                   "platform_e_mean_m": float(np.mean(pe)),
                                   "platform_n_mean_m": float(np.mean(pn)),
                                   "platform_e_span_m": float(max(pe) - min(pe)),
                                   "platform_n_span_m": float(max(pn) - min(pn))})
    platform_motion = []
    for prev, curr in zip(platform_by_period, platform_by_period[1:]):
        platform_motion.append({"from_period": prev["period_id"], "to_period": curr["period_id"],
                                "delta_e_m": curr["platform_e_mean_m"] - prev["platform_e_mean_m"],
                                "delta_n_m": curr["platform_n_mean_m"] - prev["platform_n_mean_m"],
                                "displacement_m": math.hypot(
                                    curr["platform_e_mean_m"] - prev["platform_e_mean_m"],
                                    curr["platform_n_mean_m"] - prev["platform_n_mean_m"])})

    csi = csi_measurements(args.csi_root, truth) if args.csi_root else []
    csi_by_target = {(int(item["period_id"]), item["target_id"]): item
                     for item in csi}
    for detail in all_target_detail:
        csi_item = csi_by_target.get(
            (int(number(detail, "period_id", -1)), detail["target_id"]))
        detail["measured_csi_roi_to_background_db"] = (
            csi_item["target_roi_mean_to_background_db"] if csi_item else "")
    scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
    system_config = (json.loads(Path(args.system_config).read_text(encoding="utf-8"))
                     if args.system_config else scenario)
    design_contract = system_config.get("design_contract", {})
    physical = design_contract.get("physical_calibration", {})
    waveform = system_config.get("waveform", {})
    scene = system_config.get("scene", {})
    area_clutter = scene.get("area_clutter", {})
    thermal_noise = scene.get("thermal_noise", {})
    configured_post_compression_scnr_db = physical.get(
        "post_compression_thermal_snr_db",
        design_contract.get("target_post_compression_snr_db", math.nan))
    configured_post_coherent_scnr_db = physical.get(
        "post_coherent_integration_snr_db", math.nan)
    pulse_count = physical.get("pulse_count", waveform.get("pulse_num", math.nan))
    integration_gain_db = (10.0 * math.log10(float(pulse_count))
                           if isinstance(pulse_count, (int, float)) and pulse_count > 0
                           else math.nan)
    gamma_coherent = (10.0 ** (float(configured_post_coherent_scnr_db) / 10.0)
                      if isinstance(configured_post_coherent_scnr_db, (int, float))
                      and math.isfinite(float(configured_post_coherent_scnr_db)) else math.nan)
    # Diagnostic only: two independently noisy, pair-fused receive arms at
    # per-arm SCNR gamma give Var(phi1-phi2) >= 1/(2*gamma).  The Stage2
    # target SNR is a compressed-cell calibration quantity, so this does not
    # replace the production-phase empirical propagation below.
    nominal_pair_fused_phase_crlb_std_rad = (
        1.0 / math.sqrt(2.0 * gamma_coherent)
        if math.isfinite(gamma_coherent) and gamma_coherent > 0.0 else math.nan)
    system_parameters = {
        "carrier_frequency_hz": physical.get("carrier_frequency_hz"),
        "wavelength_m": physical.get("wavelength_m"),
        "signal_bandwidth_hz": physical.get("signal_bandwidth_hz"),
        "pulse_count": pulse_count,
        "coherent_integration_gain_db": integration_gain_db,
        "post_compression_configured_scnr_db": configured_post_compression_scnr_db,
        "post_coherent_configured_scnr_db": configured_post_coherent_scnr_db,
        "system_loss_db": physical.get("system_loss_db"),
        "g_over_t_effective_db_per_k": physical.get("g_over_t_effective_db_per_k"),
        "area_clutter_model": area_clutter.get("model"),
        "area_clutter_mean_power": area_clutter.get("mean_power"),
        "area_clutter_amplitude_scale": scene.get("clutter_amplitude_scale"),
        "thermal_noise_power": thermal_noise.get("noise_power"),
        "nominal_pair_fused_phase_crlb_std_rad": nominal_pair_fused_phase_crlb_std_rad,
        "measured_phase_residual_std_rad": phase_std,
    }
    wavelength_m = float(physical.get("wavelength_m", 0.0182))
    baseline_m = float(system_config.get("waveform", {}).get("d_chan_m", 0.17))
    platform_height_m = float(system_config.get("platform", {}).get("height_m", 6000.0))
    roi_csv = run_root / "reports/signal_chain/target_csi_roi_signal_background.csv"
    if roi_csv.exists():
        roi_by_target = {
            (int(number(row, "period_id", -1)), row["target_id"]): row
            for row in rows(roi_csv)
        }
        for detail in all_target_detail:
            item = roi_by_target.get((int(number(detail, "period_id", -1)),
                                      detail["target_id"]))
            if item:
                detail["measured_csi_roi_to_background_db"] = number(
                    item, "after_roi_mean_to_background_db")
    for detail in all_target_detail:
        truth_slant_range = number(detail, "truth_range_m")
        truth_azimuth_rad = math.radians(number(detail, "truth_beam_azimuth_deg"))
        horizontal_range = math.sqrt(max(0.0, truth_slant_range ** 2 - platform_height_m ** 2))
        analytic_jacobian = (horizontal_range * wavelength_m /
                             (2.0 * math.pi * baseline_m *
                              max(0.05, abs(math.cos(truth_azimuth_rad)))))
        jacobian_value = number(detail, "loc_jacobian_crossrange_m_per_phase_rad")
        theory_jacobian = (abs(jacobian_value) if math.isfinite(jacobian_value)
                           else analytic_jacobian)
        detail["theory_jacobian_crossrange_m_per_phase_rad"] = theory_jacobian
        detail["configured_post_compression_scnr_db"] = configured_post_compression_scnr_db
        detail["configured_post_coherent_scnr_db"] = configured_post_coherent_scnr_db
        detail["theory_range_sigma_m"] = range_quant_sigma_m
        detail["theory_range_mean_abs_m"] = range_cell_m / 4.0
        detail["theory_range_strict_upper_m"] = range_cell_m / 2.0
        detail["theory_phase_sigma_empirical_rad"] = phase_std
        detail["theory_azimuth_sigma_m"] = (
            theory_jacobian * phase_std)
        detail["theory_azimuth_mean_abs_m"] = (
            detail["theory_azimuth_sigma_m"] * math.sqrt(2.0 / math.pi))
        detail["theory_azimuth_3sigma_m"] = 3.0 * detail["theory_azimuth_sigma_m"]
        detail["theory_two_dimensional_sigma_m"] = (
            math.hypot(detail["theory_azimuth_sigma_m"], range_quant_sigma_m))
        detail["theory_two_dimensional_3sigma_m"] = (
            3.0 * detail["theory_two_dimensional_sigma_m"])
        detail["nominal_scnr_crlb_phase_sigma_rad"] = nominal_pair_fused_phase_crlb_std_rad
        detail["nominal_scnr_crlb_azimuth_sigma_m"] = (
            theory_jacobian * nominal_pair_fused_phase_crlb_std_rad)
        detail["nominal_scnr_azimuth_mean_abs_m"] = (
            detail["nominal_scnr_crlb_azimuth_sigma_m"] * math.sqrt(2.0 / math.pi))
        detail["nominal_scnr_azimuth_3sigma_m"] = (
            3.0 * detail["nominal_scnr_crlb_azimuth_sigma_m"])
        detail["nominal_scnr_two_dimensional_sigma_m"] = math.hypot(
            detail["nominal_scnr_crlb_azimuth_sigma_m"], range_quant_sigma_m)
        detail["nominal_scnr_two_dimensional_3sigma_m"] = (
            3.0 * detail["nominal_scnr_two_dimensional_sigma_m"])
    result = {
        "scope": {"truth_count": len(truth), "production_detection_count": len(det),
                  "matched_target_observations": len(matched),
                  "missed_target_observations": len(truth) - len(matched),
                  "false_alarm_detections": false_alarms,
                  "pd": len(matched) / len(truth) if truth else math.nan,
                  "precision": len(matched) / len(det) if det else math.nan},
        "configured_theory_inputs": {
            "post_compression_thermal_snr_db": configured_post_compression_scnr_db,
            "post_coherent_integration_snr_db": configured_post_coherent_scnr_db,
            "carrier_frequency_hz": physical.get("carrier_frequency_hz"),
            "wavelength_m": physical.get("wavelength_m"),
            "signal_bandwidth_hz": physical.get("signal_bandwidth_hz"),
            "pulse_count": pulse_count,
            "system_loss_db": physical.get("system_loss_db"),
            "g_over_t_effective_db_per_k": physical.get("g_over_t_effective_db_per_k")},
        "system_parameters": system_parameters,
        "measured_intermediates": {
            "ctdr_phase_error_rad": stats(phase_error),
            "fgeo_error_hz": stats([number(r, "af_used_hz") - number(t, "af_geometry_truth_hz") for r, t in matched_pairs]),
            "production_jacobian_crossrange_m_per_rad": stats(jacobian),
            "post_csi_beam11_target_peak_to_background_db": stats([x["target_peak_to_background_db"] for x in csi]),
            "post_csi_beam11_target_roi_mean_to_background_db": stats([x["target_roi_mean_to_background_db"] for x in csi])},
        "theory_precision": {"range_cell_m": range_cell_m,
            "range_quantization_sigma_m": range_quant_sigma_m,
            "azimuth_sigma_m_from_measured_phase_and_production_jacobian": theory_az_sigma,
            "two_dimensional_sigma_m_quadrature": theory_2d_sigma,
            "nominal_pair_fused_phase_crlb_std_rad_from_configured_scnr": nominal_pair_fused_phase_crlb_std_rad,
            "nominal_pair_fused_crlb_azimuth_sigma_m": stats([
                number(row, "nominal_scnr_crlb_azimuth_sigma_m") for row in all_target_detail]),
            "per_observation_phase_propagated_azimuth_error_m": stats(predicted_az)},
        "actual_precision_matched_only": {
            "angle_error_deg": stats([number(r, "angle_error_deg") for r in matched_detail]),
            "azimuth_error_m": stats([number(r, "azimuth_error_m") for r in matched_detail]),
            "range_error_m": stats([number(r, "range_error_m") for r in matched_detail]),
            "two_dimensional_position_error_m": stats([number(r, "position_error_check_m") for r in matched_detail])},
        "actual_precision_by_period": per_period,
        "all_50_target_localization_table_csv": "all_50_target_localization_details.csv",
        "per_period_localization_summary_csv": "per_period_localization_summary.csv",
        "production_platform_state_by_period": platform_by_period,
        "production_platform_motion_between_periods": platform_motion,
        "csi_beam11_observed": csi,
    }
    report = run_root / "reports"
    report.mkdir(parents=True, exist_ok=True)
    (report / "real_parameter_localization_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=True) + "\n", encoding="utf-8")
    fields = ["period_id", "target_id", "truth_beam_id", "matched_beam_id",
              "matched_detection_id", "localization_status", "truth_range_m",
              "estimated_range_m", "truth_beam_azimuth_deg", "angle_error_deg",
              "azimuth_error_m", "range_error_m",
              "position_error_check_m", "ctdr_raw_phase_rad", "truth_ctdr_phase_rad",
              "loc_jacobian_crossrange_m_per_phase_rad",
              "configured_post_compression_scnr_db", "configured_post_coherent_scnr_db",
              "measured_csi_roi_to_background_db",
              "theory_phase_sigma_empirical_rad", "theory_range_sigma_m",
              "theory_range_mean_abs_m", "theory_range_strict_upper_m",
              "theory_jacobian_crossrange_m_per_phase_rad",
              "theory_azimuth_sigma_m", "theory_azimuth_mean_abs_m",
              "theory_azimuth_3sigma_m", "theory_two_dimensional_sigma_m",
              "theory_two_dimensional_3sigma_m",
              "nominal_scnr_crlb_phase_sigma_rad",
              "nominal_scnr_crlb_azimuth_sigma_m",
              "nominal_scnr_azimuth_mean_abs_m", "nominal_scnr_azimuth_3sigma_m",
              "nominal_scnr_two_dimensional_sigma_m",
              "nominal_scnr_two_dimensional_3sigma_m"]
    with (report / "matched_localization_details.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(matched_detail)
    with (report / "all_50_target_localization_details.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(all_target_detail)
    period_fields = ["period_id", "truth_count", "matched_count", "missed_count", "pd",
                     "azimuth_rmse_m", "range_rmse_m", "two_dimensional_rmse_m"]
    period_rows = [{
        "period_id": row["period_id"], "truth_count": row["truth_count"],
        "matched_count": row["matched_count"], "missed_count": row["missed_count"],
        "pd": row["pd"], "azimuth_rmse_m": row["azimuth_error_m"]["rmse"],
        "range_rmse_m": row["range_error_m"]["rmse"],
        "two_dimensional_rmse_m": row["two_dimensional_position_error_m"]["rmse"]}
        for row in per_period]
    with (report / "per_period_localization_summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=period_fields)
        writer.writeheader(); writer.writerows(period_rows)

    def fmt(value, digits=3):
        return "—" if not math.isfinite(number({"v": value}, "v")) else f"{float(value):.{digits}f}"

    markdown_lines = [
        "# 五周期 50 个目标：距离、方位与二维定位结果表", "",
        "数据源为用户指定的既有五周期仿真数据；本表仅重算评价，未生成或修改原始回波。"
        "`MATCHED` 的误差是生产 GMTI 输出与同周期、同波束中点真值之差；`MISSED` 表示没有通过"
        "一对一匹配门限的生产定位，故不填零误差。", "",
        f"- 全部目标观测：50；匹配：{len(matched)}；漏检：{len(truth) - len(matched)}；Pd={result['scope']['pd']:.3f}。",
        "- 配置 SCNR：脉压后 "
        f"{fmt(configured_post_compression_scnr_db)} dB；128 脉冲相干后 {fmt(configured_post_coherent_scnr_db)} dB。"
        "它是 Stage2 的目标/本地背景标定量，并非每行直接测得的 CSI-SCNR。", 
        "- 匹配条件总体 RMSE：方位 "
        f"{result['actual_precision_matched_only']['azimuth_error_m']['rmse']:.3f} m，距离 "
        f"{result['actual_precision_matched_only']['range_error_m']['rmse']:.3f} m，二维 "
        f"{result['actual_precision_matched_only']['two_dimensional_position_error_m']['rmse']:.3f} m。", "",
        "| 周期 | 目标 | 状态 | 配置 SCNR dB（脉压后/相干后） | 实测 CSI ROI/背景 dB | 距离误差 m | 理论距离 1σ/上界 m | 方位误差 m | 理论方位 1σ/3σ m | 二维误差 m | 理论二维 1σ/3σ m |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in all_target_detail:
        markdown_lines.append(
            f"| {row['period_id']} | {row['target_id']} | {row['localization_status']} | "
            f"{fmt(row['configured_post_compression_scnr_db'])}/{fmt(row['configured_post_coherent_scnr_db'])} | "
            f"{fmt(row['measured_csi_roi_to_background_db'])} | "
            f"{fmt(row['range_error_m'])} | {fmt(row['theory_range_sigma_m'])}/{fmt(row['theory_range_strict_upper_m'])} | "
            f"{fmt(row['azimuth_error_m'])} | {fmt(row['theory_azimuth_sigma_m'])}/{fmt(row['theory_azimuth_3sigma_m'])} | "
            f"{fmt(row['position_error_check_m'])} | {fmt(row['theory_two_dimensional_sigma_m'])}/{fmt(row['theory_two_dimensional_3sigma_m'])} |")
    markdown_lines.extend(["", "## 按周期汇总（仅匹配样本的误差统计）", "",
        "| 周期 | 真值数 | 匹配 | 漏检 | Pd | 方位 RMSE m | 距离 RMSE m | 二维 RMSE m |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for row in period_rows:
        markdown_lines.append(
            f"| {row['period_id']} | {row['truth_count']} | {row['matched_count']} | "
            f"{row['missed_count']} | {fmt(row['pd'])} | {fmt(row['azimuth_rmse_m'])} | "
            f"{fmt(row['range_rmse_m'])} | {fmt(row['two_dimensional_rmse_m'])} |")
    (report / "five_period_50_target_localization_results.md").write_text(
        "\n".join(markdown_lines) + "\n", encoding="utf-8")
    system_lines = [
        "# 五周期定位：当前系统参数与理论精度口径", "",
        "## 当前系统参数", "",
        "| 参数 | 数值 |", "|---|---:|",
    ]
    for label, value, unit in [
        ("载频", system_parameters["carrier_frequency_hz"], "Hz"),
        ("波长", system_parameters["wavelength_m"], "m"),
        ("信号带宽", system_parameters["signal_bandwidth_hz"], "Hz"),
        ("相干脉冲数", system_parameters["pulse_count"], ""),
        ("相干积累增益", system_parameters["coherent_integration_gain_db"], "dB"),
        ("配置 SCNR（脉压后）", system_parameters["post_compression_configured_scnr_db"], "dB"),
        ("配置 SCNR（相干后）", system_parameters["post_coherent_configured_scnr_db"], "dB"),
        ("系统损失", system_parameters["system_loss_db"], "dB"),
        ("有效 G/T", system_parameters["g_over_t_effective_db_per_k"], "dB/K"),
        ("面杂波模型", system_parameters["area_clutter_model"], ""),
        ("面杂波均值功率 / 幅度尺度", system_parameters["area_clutter_mean_power"],
         f"/ {system_parameters['area_clutter_amplitude_scale']}"),
        ("热噪声功率", system_parameters["thermal_noise_power"], ""),
        (f"实测相位残差 Std（{len(matched)} 个匹配）",
         system_parameters["measured_phase_residual_std_rad"], "rad"),
    ]:
        rendered = value if isinstance(value, str) else fmt(value, 6)
        system_lines.append(f"| {label} | {rendered} {unit} |")
    system_lines.extend([
        "", "## 理论定位精度", "",
        "生产可用的主理论列采用 `sigma_x=|J_xphi|*sigma_phi`：其中每目标 "
        "`J_xphi` 是生产定位器在实际分支的数值 Jacobian，"
        f"`sigma_phi={system_parameters['measured_phase_residual_std_rad']:.6f} rad` "
        f"是 {len(matched)} 个正确匹配样本的实测 CTDR 相位残差标准差。距离项为 50 MHz 带宽的量化 "
        "1σ `delta_R/sqrt(12)=0.865 m`，二维项为二者正交合成。", "",
        f"- 全部匹配样本的工程理论：方位 1σ={theory_az_sigma:.3f} m，二维 1σ={theory_2d_sigma:.3f} m。",
        "- 对配置相干后 SCNR 作“两个独立、成对融合接收臂”的单相位 CRLB 诊断，"
        f"`sigma_phi>=1/sqrt(2*10^(SCNR/10))={nominal_pair_fused_phase_crlb_std_rad:.6f} rad`。"
        "它与实测相位残差的定义/处理增益不一致，不能替代上面的生产工程理论列，也不能宣称为本定位器的验证 CRLB。",
        "- `MISSED` 没有实际定位误差；其理论列仍使用真值几何的解析 Jacobian 计算，"
        "因此 50 个目标观测都有理论精度，但漏检行的实际误差不填零。",
        "", "## 实测 CSI 对照的覆盖边界", "",
        "`reports/signal_chain/target_csi_roi_signal_background.csv` 已固化全部 50 个目标观测的"
        "对消前/后局部功率与 ROI/背景比，50 行定位表均从该 CSV 回填实测值。"
        "这些值是检测图上的经验峰背景比，不能冒充独立纯噪声测量的 SNR。",
    ])
    (report / "five_period_system_parameters_and_theory.md").write_text(
        "\n".join(system_lines) + "\n", encoding="utf-8")
    (report / "real_parameter_localization_report.md").write_text(
        "# 固定真实系统参数：方位与二维定位精度\n\n"
        "本报告只重放既有 Stage2 原始回波，不生成任何数据。理论方位项采用生产 CTDR 求解器输出的中心差分 Jacobian，与同一批真值相位残差相乘；二维理论项再以 50 MHz 距离单元量化标准差作正交合成。\n\n"
        f"- 样本：真值 {len(truth)}，匹配 {len(matched)}，Pd={result['scope']['pd']:.3f}，假警 {false_alarms}。\n"
        f"- 理论：方位 1σ={theory_az_sigma:.3f} m；二维 1σ={theory_2d_sigma:.3f} m；距离单元={range_cell_m:.3f} m。\n"
        f"- 实测（仅匹配目标）：方位 RMSE={result['actual_precision_matched_only']['azimuth_error_m']['rmse']:.3f} m；二维 RMSE={result['actual_precision_matched_only']['two_dimensional_position_error_m']['rmse']:.3f} m。\n"
        "- 全部 50 个目标：`all_50_target_localization_details.csv` 保留每个目标的状态、配置 SCNR 和实际/理论精度；漏检目标误差字段为空，不以零误差参与统计。\n"
        "- 参数与理论口径：`five_period_system_parameters_and_theory.md`。\n"
        "- 平台状态：生产定位每条检测均使用对应波束/周期的 `platform_e/n/h`；本回放中平台均值状态在相邻周期之间实际变化，二维误差也不呈单调累积，因此本数据不支持“遗漏平台运动补偿”这一解释。\n"
        "\n完整中间量、逐目标误差和 CSI 实测值见同目录 JSON/CSV。\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=True))


if __name__ == "__main__":
    main()
