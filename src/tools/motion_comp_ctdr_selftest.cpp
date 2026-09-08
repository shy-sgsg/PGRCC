#include "motion_comp.hpp"

#include <cmath>
#include <iostream>

namespace {

double posErrorFromAf(const Config &cfg,
                      const GMTIOutput::Plane &plane,
                      double lambda,
                      double range_m,
                      double af_hz,
                      double truth_af_hz)
{
    const gmti::ctdr::Vec2 look = motionLookFromAfGeo(cfg, plane, lambda, af_hz);
    const gmti::ctdr::Vec2 truth_look = motionLookFromAfGeo(cfg, plane, lambda, truth_af_hz);
    const double ground_range = gmti::ctdr::groundRangeFromSlant(range_m, plane.H, cfg.MT_nowz);
    const double de = ground_range * (look.e - truth_look.e);
    const double dn = ground_range * (look.n - truth_look.n);
    return std::hypot(de, dn);
}

} // namespace

int main()
{
    Config cfg;
    cfg.fc = 16.0;
    cfg.lambda = C / (16.0e9);
    cfg.fs = 60.0e6;
    cfg.sample_delay_us = 488.0;
    cfg.range_crop_start = 3864;
    cfg.fd_res = 10.0;
    cfg.d_channel = 0.17;
    cfg.squint_side = 1;
    cfg.rx_baseline_sign = 1;
    cfg.channel_phase_sign = -1;
    cfg.motion_doppler_axis_sign = -1;
    cfg.motion_comp_enable = true;
    cfg.motion_comp_solver = "debug";
    cfg.ati_vmax_mps = 1.6;
    cfg.MT_nowz = 0.0;

    GMTIOutput::Plane plane;
    plane.E = 0.0;
    plane.N = 0.0;
    plane.H = 6000.0;
    plane.V = 60.0;
    plane.V_angle = 0.0;

    const double equivalent_two_way_spacing =
        gmti::ctdr::equivalentTwoWayPhaseCenterSeparation(cfg.d_channel);
    const double equivalent_two_way_delay =
        gmti::ctdr::equivalentTwoWayDelaySeconds(cfg.d_channel, plane.V);
    const double raw_p38_theory =
        gmti::ctdr::rawInterferometricSlopeRadPerHz(
            cfg.d_channel, plane.V,
            cfg.rx_baseline_sign, cfg.channel_phase_sign);
    const int alignment_prt = gmti::ctdr::stationaryClutterAlignmentPrt(
        cfg.d_channel, plane.V, 1300.0,
        cfg.rx_baseline_sign, cfg.channel_phase_sign);
    const double aligned_residual = gmti::ctdr::residualSlopeAfterPrtAlignment(
        raw_p38_theory, 1300.0, alignment_prt);

    const double theta_deg = 40.0;
    const double range_bin = 2048.0;
    const double raw_sample = static_cast<double>(cfg.range_crop_start) + range_bin;
    const double range_m = 0.5 * C * (cfg.sample_delay_us * 1.0e-6 + raw_sample / cfg.fs);
    const double lambda = cfg.lambda;
    const double af_geo_truth = 2.0 * plane.V * std::sin(theta_deg * M_PI / 180.0) / lambda;
    const double v_radial_truth = 1.0;
    const double af_total = af_geo_truth +
        static_cast<double>(cfg.motion_doppler_axis_sign) * 2.0 * v_radial_truth / lambda;
    const double raw_phase = ctdrStaticPhaseFromAfGeo(
        cfg, plane, lambda, range_m, af_geo_truth);

    // The receiver offsets are tiny compared with the ~80 km slant range.
    // Verify that the rationalized path increment agrees with the direct
    // double reference without relying on a long-range subtraction.
    const double target_e = range_m * std::sin(theta_deg * M_PI / 180.0);
    const double target_n = range_m * std::cos(theta_deg * M_PI / 180.0);
    const double target_h = -plane.H;
    const double reference_range = std::sqrt(
        target_e * target_e + target_n * target_n + target_h * target_h);
    const double los_e = target_e / reference_range;
    const double los_n = target_n / reference_range;
    const double los_h = target_h / reference_range;
    // Use long double only in this host-side reference.  A direct double
    // subtraction itself loses several picometres at ~80 km and therefore
    // is not a valid oracle for the rationalized formula.
    const long double target_e_ld = static_cast<long double>(target_e);
    const long double target_n_ld = static_cast<long double>(target_n);
    const long double target_h_ld = static_cast<long double>(target_h);
    const long double reference_range_ld = std::sqrt(
        target_e_ld * target_e_ld + target_n_ld * target_n_ld +
        target_h_ld * target_h_ld);
    const long double direct_delta_1_ld = std::sqrt(
        (target_e_ld + 0.085L) * (target_e_ld + 0.085L) +
        target_n_ld * target_n_ld + target_h_ld * target_h_ld) -
        reference_range_ld;
    const long double direct_delta_2_ld = std::sqrt(
        (target_e_ld - 0.085L) * (target_e_ld - 0.085L) +
        target_n_ld * target_n_ld + target_h_ld * target_h_ld) -
        reference_range_ld;
    const double direct_delta_1 = static_cast<double>(direct_delta_1_ld);
    const double direct_delta_2 = static_cast<double>(direct_delta_2_ld);
    const double stable_delta_1 = gmti::ctdr::stablePathOffsetFromCenter(
        reference_range, los_e, los_n, los_h, -0.085, 0.0, 0.0);
    const double stable_delta_2 = gmti::ctdr::stablePathOffsetFromCenter(
        reference_range, los_e, los_n, los_h, 0.085, 0.0, 0.0);
    const double stable_path_error = std::max(
        std::abs(direct_delta_1 - stable_delta_1),
        std::abs(direct_delta_2 - stable_delta_2));

    // Match the production convention exactly:
    // F2 <- F2 * exp(+j*range_phase_correction), hence the phase map stores
    // raw CTDR phase minus that correction.  The chosen value reproduces the
    // near-zero corrected phase seen by the beam-50 regression case.
    const double range_phase_correction = -2.70;
    const double corrected_phase = wrapPiLocal(raw_phase - range_phase_correction);
    const double p38_k = -0.0087;
    const double p38_b = corrected_phase - p38_k * af_geo_truth;

    const MotionCompResult uncalibrated =
        solveMotionCompensation(cfg, plane, corrected_phase, af_total,
                                p38_k, p38_b, lambda, range_m, theta_deg);
    const MotionCompResult solved =
        solveMotionCompensation(cfg, plane, corrected_phase, af_total,
                                p38_k, p38_b, lambda, range_m, theta_deg,
                                range_phase_correction);

    const double old_total_err = posErrorFromAf(cfg, plane, lambda, range_m,
                                                af_total, af_geo_truth);
    const double new_err = posErrorFromAf(cfg, plane, lambda, range_m,
                                          solved.af_geometry, af_geo_truth);
    const double uncalibrated_err = posErrorFromAf(
        cfg, plane, lambda, range_m, uncalibrated.af_geometry, af_geo_truth);
    const double restored_phase_error = std::abs(wrapPiLocal(
        solved.phi_meas_ctdr_raw - raw_phase));
    std::cout << "beam_id=51 theta_deg=" << theta_deg
              << " range_bin=" << range_bin
              << " range_m=" << range_m << '\n';
    std::cout << "af_geo_truth_hz=" << af_geo_truth
              << " af_total_hz=" << af_total
              << " raw_phase_rad=" << raw_phase
              << " corrected_phase_rad=" << corrected_phase
              << " range_phase_correction_rad=" << range_phase_correction
              << '\n';
    std::cout << "rx_phase_center_spacing_m=" << cfg.d_channel
              << " equivalent_two_way_phase_center_spacing_m="
              << equivalent_two_way_spacing
              << " equivalent_two_way_delay_s=" << equivalent_two_way_delay
              << " raw_p38_theory_rad_per_hz=" << raw_p38_theory
              << " alignment_prt=" << alignment_prt
              << " aligned_residual_rad_per_hz=" << aligned_residual << '\n';
    std::cout << "stable_path_error_m=" << stable_path_error
              << " direct_path_difference_m="
              << (direct_delta_1 - direct_delta_2)
              << " stable_path_difference_m="
              << (stable_delta_1 - stable_delta_2) << '\n';
    std::cout << "old_total_position_error_m=" << old_total_err << '\n';
    std::cout << "uncalibrated_ctdr_position_error_m=" << uncalibrated_err
              << '\n';
    std::cout << "new_af_geometry_hz=" << solved.af_geometry
              << " new_v_radial_mps=" << solved.v_radial
              << " new_position_error_m=" << new_err
              << " restored_phase_error_rad=" << restored_phase_error
              << " root_cost=" << solved.root1d_cost << '\n';
    const bool ok = solved.ok &&
                    std::abs(cfg.d_channel - 0.17) < 1.0e-12 &&
                    std::abs(equivalent_two_way_spacing - 0.085) < 1.0e-12 &&
                    std::abs(equivalent_two_way_delay - 0.17 / (2.0 * 60.0)) < 1.0e-12 &&
                    alignment_prt == 2 &&
                    std::abs(aligned_residual) < 1.0e-3 &&
                    stable_path_error < 1.0e-12 &&
                    new_err < 100.0 &&
                    uncalibrated_err > 500.0 &&
                    restored_phase_error < 1.0e-12 &&
                    std::abs(solved.af_phase - af_geo_truth) < 1.0e-6 &&
                    solved.geometry_calib_mode ==
                        "common_transmit_dual_receive_rg_phase_restored";
    return ok ? 0 : 1;
}
