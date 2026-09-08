#ifndef MOTION_COMP_HPP
#define MOTION_COMP_HPP

#include "config_structs.hpp"
#include "ctdr_phase_model.hpp"

#include <algorithm>
#include <cmath>
#include <cctype>
#include <limits>
#include <string>
#include <vector>

inline double wrapPiLocal(double phase)
{
    if (!std::isfinite(phase)) {
        return phase;
    }
    phase = std::fmod(phase + M_PI, 2.0 * M_PI);
    if (phase < 0.0) {
        phase += 2.0 * M_PI;
    }
    return phase - M_PI;
}

inline double unwrapNearLocal(double phase, double ref)
{
    return ref + wrapPiLocal(phase - ref);
}

// Build the receive phase-centre pose used by the exact CTDR model.  The
// electronic path intentionally returns an invalid pose so all historical
// scalar-baseline callers retain their previous behaviour.  Mechanical mode
// uses the actual CPI centre servo angle populated by processOnePeriod(); a
// direct Config caller may still use squint_angle as the explicit fallback.
inline gmti::ctdr::PhaseCenterPose configuredCtdrPhaseCenterPose(
    const Config &cfg)
{
    gmti::ctdr::PhaseCenterPose pose;
    if (cfg.scan_mode != ScanMode::Mechanical ||
        !cfg.mechanical_scan.phase_center_rotation_enable) {
        return pose;
    }

    const auto valid_channel = [](int channel, std::size_t count) {
        return channel >= 1 && static_cast<std::size_t>(channel) <= count;
    };
    const std::size_t offset_count = cfg.four_channel_offsets_m.size();
    if (valid_channel(cfg.new_protocol_read_channel_1, offset_count) &&
        valid_channel(cfg.new_protocol_read_channel_2, offset_count)) {
        pose.rx1_local = cfg.four_channel_offsets_m[
            static_cast<std::size_t>(cfg.new_protocol_read_channel_1 - 1)];
        pose.rx2_local = cfg.four_channel_offsets_m[
            static_cast<std::size_t>(cfg.new_protocol_read_channel_2 - 1)];
    } else if (cfg.d_channel > 0.0 && std::isfinite(cfg.d_channel)) {
        pose.rx1_local = {{-0.5 * cfg.d_channel, 0.0, 0.0}};
        pose.rx2_local = {{0.5 * cfg.d_channel, 0.0, 0.0}};
    } else {
        return pose;
    }
    pose.valid = true;
    pose.rotate_with_servo = true;
    pose.servo_azimuth_deg = std::isfinite(cfg.ctdr_servo_azimuth_deg)
        ? cfg.ctdr_servo_azimuth_deg : cfg.squint_angle;
    pose.mount_angle_deg = cfg.mechanical_scan.phase_center_mount_angle_deg;
    pose.rotation_sign = cfg.mechanical_scan.phase_center_rotation_sign;
    pose.baseline_sign = cfg.rx_baseline_sign;
    return pose;
}

inline double configuredCtdrEquivalentTwoWayDelay(
    const Config &cfg, double platform_speed_mps)
{
    const gmti::ctdr::PhaseCenterPose pose =
        configuredCtdrPhaseCenterPose(cfg);
    if (gmti::ctdr::finitePhaseCenterPose(pose)) {
        return gmti::ctdr::equivalentTwoWayDelaySeconds(
            pose, platform_speed_mps);
    }
    return gmti::ctdr::equivalentTwoWayDelaySeconds(
        cfg.d_channel, platform_speed_mps);
}

// rg_correct fits arg(F1 * conj(F2)) and applies F2 *= exp(+j*phi_fit).
// Consequently, the phase map used by CFAR/p38 is
//     phi_corrected = wrap(phi_raw_ctdr - phi_fit).
// The exact CTDR forward model predicts phi_raw_ctdr, so add the actually
// applied range correction back only at the CTDR solver boundary.  Keeping
// the corrected phase everywhere else is intentional: p38 is fitted in that
// corrected phase domain.
inline double restoreCtdrRawPhase(double corrected_phase,
                                  double applied_range_phase_correction)
{
    if (!std::isfinite(corrected_phase)) {
        return corrected_phase;
    }
    if (!std::isfinite(applied_range_phase_correction)) {
        return corrected_phase;
    }
    return wrapPiLocal(corrected_phase + applied_range_phase_correction);
}

inline std::string normalizeMotionCompSolver(std::string solver)
{
    std::string out;
    out.reserve(solver.size());
    for (unsigned char ch : solver) {
        if (!std::isspace(ch)) {
            out.push_back(static_cast<char>(std::tolower(ch)));
        }
    }
    if (out.empty()) {
        return "analytic";
    }
    if (out == "iter") {
        return "iterative";
    }
    if (out == "root") {
        return "root1d";
    }
    if (out == "dbg") {
        return "debug";
    }
    return out;
}

inline double staticPhaseModel(double af_geo,
                               double k,
                               double b,
                               double ati_phase_bias_rad)
{
    return k * af_geo + b + ati_phase_bias_rad;
}

inline double strictGeometryStaticPhaseModel(double af_geo,
                                             double delta_t,
                                             double ati_phase_bias_rad)
{
    return 2.0 * M_PI * delta_t * af_geo + ati_phase_bias_rad;
}

inline double motionStaticPhaseModel(double af_geo,
                                     double k,
                                     double b,
                                     double ati_phase_bias_rad,
                                     double delta_t,
                                     double static_phase_ref = std::numeric_limits<double>::quiet_NaN())
{
    if (std::isfinite(static_phase_ref)) {
        return static_phase_ref + ati_phase_bias_rad;
    }
    if (std::isfinite(delta_t) && delta_t > 0.0) {
        return strictGeometryStaticPhaseModel(af_geo, delta_t, ati_phase_bias_rad);
    }
    return staticPhaseModel(af_geo, k, b, ati_phase_bias_rad);
}

inline double motionStaticPhaseKeff(double delta_t,
                                    double fallback_k,
                                    double static_phase_ref = std::numeric_limits<double>::quiet_NaN())
{
    if (std::isfinite(static_phase_ref)) {
        return 0.0;
    }
    if (std::isfinite(delta_t) && delta_t > 0.0) {
        return 2.0 * M_PI * delta_t;
    }
    return fallback_k;
}

inline double beamStaticPhaseRef(const Config &cfg,
                                 const GMTIOutput::Plane &plane,
                                 double lambda,
                                 double theta_true_deg)
{
    if (!(lambda > 0.0) || !(plane.V > 0.0) ||
        !std::isfinite(theta_true_deg)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double theta = theta_true_deg * M_PI / 180.0;
    const double af_stationary = -2.0 * plane.V * std::sin(theta) / lambda;
    const gmti::ctdr::PhaseCenterPose pose =
        configuredCtdrPhaseCenterPose(cfg);
    const double raw_slope = gmti::ctdr::finitePhaseCenterPose(pose)
        ? gmti::ctdr::rawInterferometricSlopeRadPerHz(
              pose, plane.V, theta_true_deg, cfg.squint_side,
              cfg.channel_phase_sign)
        : gmti::ctdr::rawInterferometricSlopeRadPerHz(
              cfg.d_channel, plane.V,
              cfg.rx_baseline_sign, cfg.channel_phase_sign);
    return raw_slope * af_stationary;
}

inline gmti::ctdr::Vec2 motionLookFromAfGeo(const Config &cfg,
                                            const GMTIOutput::Plane &plane,
                                            double lambda,
                                            double af_geo)
{
    gmti::ctdr::Vec2 out;
    if (!(lambda > 0.0) || !(plane.V > 1.0e-9) || !std::isfinite(af_geo)) {
        return out;
    }
    const double sinA = af_geo * lambda / (2.0 * plane.V);
    if (!std::isfinite(sinA) || std::abs(sinA) > 1.0) {
        return out;
    }
    const double vE = plane.V * std::cos(plane.V_angle * M_PI / 180.0);
    const double vN = plane.V * std::sin(plane.V_angle * M_PI / 180.0);
    const gmti::ctdr::Vec2 along = gmti::ctdr::alongFromVelocity(vE, vN);
    if (!std::isfinite(along.e)) {
        return out;
    }
    const double left_e = -along.n;
    const double left_n = along.e;
    const double right_e = along.n;
    const double right_n = -along.e;
    const double cross = std::sqrt(std::max(0.0, 1.0 - sinA * sinA));
    const bool use_left = (cfg.squint_side == 1);
    const double side_e = use_left ? left_e : right_e;
    const double side_n = use_left ? left_n : right_n;
    out.e = cross * side_e + sinA * along.e;
    out.n = cross * side_n + sinA * along.n;
    const double norm = std::hypot(out.e, out.n);
    if (norm > 1.0e-12) {
        out.e /= norm;
        out.n /= norm;
    }
    return out;
}

inline double ctdrStaticPhaseFromAfGeo(const Config &cfg,
                                       const GMTIOutput::Plane &plane,
                                       double lambda,
                                       double range_m,
                                       double af_geo)
{
    const gmti::ctdr::Vec2 look =
        motionLookFromAfGeo(cfg, plane, lambda, af_geo);
    if (!std::isfinite(look.e) || !std::isfinite(range_m)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double ground_range =
        gmti::ctdr::groundRangeFromSlant(range_m, plane.H, cfg.MT_nowz);
    const double target_e = plane.E + ground_range * look.e;
    const double target_n = plane.N + ground_range * look.n;
    const double vE = plane.V * std::cos(plane.V_angle * M_PI / 180.0);
    const double vN = plane.V * std::sin(plane.V_angle * M_PI / 180.0);
    const gmti::ctdr::PhaseCenterPose pose =
        configuredCtdrPhaseCenterPose(cfg);
    if (gmti::ctdr::finitePhaseCenterPose(pose)) {
        return gmti::ctdr::staticPhase(
            target_e, target_n, cfg.MT_nowz,
            plane.E, plane.N, plane.H, vE, vN, pose, lambda,
            cfg.channel_phase_sign, cfg.ati_phase_bias_rad);
    }
    return gmti::ctdr::staticPhase(target_e, target_n, cfg.MT_nowz,
                                   plane.E, plane.N, plane.H,
                                   vE, vN, cfg.d_channel, lambda,
                                   cfg.rx_baseline_sign,
                                   cfg.channel_phase_sign,
                                   cfg.ati_phase_bias_rad);
}

inline double ctdrStaticPhaseUnwrappedFromAfGeo(const Config &cfg,
                                                const GMTIOutput::Plane &plane,
                                                double lambda,
                                                double range_m,
                                                double af_geo)
{
    const gmti::ctdr::Vec2 look =
        motionLookFromAfGeo(cfg, plane, lambda, af_geo);
    if (!std::isfinite(look.e) || !std::isfinite(range_m)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double ground_range =
        gmti::ctdr::groundRangeFromSlant(range_m, plane.H, cfg.MT_nowz);
    const double target_e = plane.E + ground_range * look.e;
    const double target_n = plane.N + ground_range * look.n;
    const double vE = plane.V * std::cos(plane.V_angle * M_PI / 180.0);
    const double vN = plane.V * std::sin(plane.V_angle * M_PI / 180.0);
    const gmti::ctdr::PhaseCenterPose pose =
        configuredCtdrPhaseCenterPose(cfg);
    if (gmti::ctdr::finitePhaseCenterPose(pose)) {
        return gmti::ctdr::staticPhaseUnwrapped(
            target_e, target_n, cfg.MT_nowz,
            plane.E, plane.N, plane.H, vE, vN, pose, lambda,
            cfg.channel_phase_sign, cfg.ati_phase_bias_rad);
    }
    return gmti::ctdr::staticPhaseUnwrapped(
        target_e, target_n, cfg.MT_nowz,
        plane.E, plane.N, plane.H,
        vE, vN, cfg.d_channel, lambda,
        cfg.rx_baseline_sign,
        cfg.channel_phase_sign,
        cfg.ati_phase_bias_rad);
}

inline double calcStaticPhaseKeff(double af_total,
                                  double k,
                                  double b,
                                  double ati_phase_bias_rad,
                                  double fd_res_hint)
{
    const double df = std::max(1.0, 0.25 * std::max(1.0, fd_res_hint));
    const double phi0 = staticPhaseModel(af_total, k, b, ati_phase_bias_rad);
    double phip = staticPhaseModel(af_total + df, k, b, ati_phase_bias_rad);
    double phim = staticPhaseModel(af_total - df, k, b, ati_phase_bias_rad);
    phip = unwrapNearLocal(phip, phi0);
    phim = unwrapNearLocal(phim, phi0);
    return (phip - phim) / (2.0 * df);
}

struct MotionCompResult {
    bool ok = false;
    bool used_motion_comp = false;
    bool fallback_legacy = false;
    double phi_meas = std::numeric_limits<double>::quiet_NaN();
    double phi_meas_ctdr_raw = std::numeric_limits<double>::quiet_NaN();
    double range_phase_correction = std::numeric_limits<double>::quiet_NaN();
    double phi_static_model = std::numeric_limits<double>::quiet_NaN();
    double phi_static = std::numeric_limits<double>::quiet_NaN();
    double phi_static_total = std::numeric_limits<double>::quiet_NaN();
    double phi_res = std::numeric_limits<double>::quiet_NaN();
    double phi_static_at_zero = std::numeric_limits<double>::quiet_NaN();
    double phi_res_at_zero = std::numeric_limits<double>::quiet_NaN();
    double phi_static_geometry = std::numeric_limits<double>::quiet_NaN();
    double af_phase = std::numeric_limits<double>::quiet_NaN();
    double af_total = std::numeric_limits<double>::quiet_NaN();
    double af_geometry = std::numeric_limits<double>::quiet_NaN();
    double af_motion = std::numeric_limits<double>::quiet_NaN();
    double phi_motion = std::numeric_limits<double>::quiet_NaN();
    double delta_t = std::numeric_limits<double>::quiet_NaN();
    double denom = std::numeric_limits<double>::quiet_NaN();
    double denom_without_k = std::numeric_limits<double>::quiet_NaN();
    double k = std::numeric_limits<double>::quiet_NaN();
    double b = std::numeric_limits<double>::quiet_NaN();
    double C_ati = std::numeric_limits<double>::quiet_NaN();
    double k_eff_static_phase_df = std::numeric_limits<double>::quiet_NaN();
    double v_radial = std::numeric_limits<double>::quiet_NaN();
    double v_from_phase_raw = std::numeric_limits<double>::quiet_NaN();
    double v_from_phi_res = std::numeric_limits<double>::quiet_NaN();
    double v_iterative_mps = std::numeric_limits<double>::quiet_NaN();
    double v_analytic_mps = std::numeric_limits<double>::quiet_NaN();
    double v_root1d_mps = std::numeric_limits<double>::quiet_NaN();
    double v_old_mps = std::numeric_limits<double>::quiet_NaN();
    double af_geometry_old_hz = std::numeric_limits<double>::quiet_NaN();
    double af_geometry_iterative_hz = std::numeric_limits<double>::quiet_NaN();
    double af_geometry_analytic_hz = std::numeric_limits<double>::quiet_NaN();
    double af_geometry_root1d_hz = std::numeric_limits<double>::quiet_NaN();
    double root1d_cost = std::numeric_limits<double>::quiet_NaN();
    double af_alias_hz = std::numeric_limits<double>::quiet_NaN();
    double af_unwrapped_hz = std::numeric_limits<double>::quiet_NaN();
    int doppler_ambiguity_order = 0;
    double phase_wrapped_rad = std::numeric_limits<double>::quiet_NaN();
    double phase_unwrapped_rad = std::numeric_limits<double>::quiet_NaN();
    int phase_ambiguity_order = 0;
    int velocity_candidate_count = 0;
    double velocity_best_cost = std::numeric_limits<double>::quiet_NaN();
    double velocity_second_cost = std::numeric_limits<double>::quiet_NaN();
    double velocity_cost_margin = std::numeric_limits<double>::quiet_NaN();
    int p38_theory_sign = -1;
    int motion_doppler_axis_sign = 1;
    int ati_phase_to_velocity_sign = 1;
    std::string phi_static_model_name = "p38_linear_fit";
    std::string p38_mode = "clutter_cancel_38_paper_1_p38_cuda";
    std::string geometry_calib_mode = "linear_p38_phase_vs_doppler";
    std::string solver_requested = "analytic";
    std::string solver = "old";
    std::string velocity_solver = "legacy_single_branch";
    std::string velocity_confidence = "not_evaluated";
    std::string ambiguity_status = "disabled";
};

inline int configuredP38TheorySign(const Config &cfg)
{
    const int baseline_sign = (cfg.rx_baseline_sign < 0) ? -1 : 1;
    const int phase_sign = (cfg.channel_phase_sign < 0) ? -1 : 1;
    return baseline_sign * phase_sign;
}

inline MotionCompResult solveCtdrMotionComp(const Config &cfg,
                                            const GMTIOutput::Plane &plane,
                                            double phase,
                                            double af_total,
                                            double k,
                                            double b,
                                            double lambda,
                                            double range_m,
                                            const std::string &solver_name,
                                            double raw_ctdr_phase = std::numeric_limits<double>::quiet_NaN(),
                                            double range_phase_correction = std::numeric_limits<double>::quiet_NaN())
{
    MotionCompResult out;
    out.k = k;
    out.b = b;
    out.phi_meas = phase;
    const double phase_ctdr = std::isfinite(raw_ctdr_phase) ? raw_ctdr_phase : phase;
    out.phi_meas_ctdr_raw = phase_ctdr;
    out.range_phase_correction = range_phase_correction;
    out.af_phase = (std::abs(k) >= 1.0e-12) ? ((phase - b) / k) : af_total;
    out.af_total = std::isfinite(af_total) ? af_total : out.af_phase;
    out.phi_static_model_name = "ctdr_true_path_difference";
    out.geometry_calib_mode = std::isfinite(range_phase_correction)
        ? "common_transmit_dual_receive_rg_phase_restored"
        : "common_transmit_dual_receive";
    out.solver_requested = solver_name;
    out.solver = solver_name;
    out.p38_theory_sign = configuredP38TheorySign(cfg);
    out.motion_doppler_axis_sign = (cfg.motion_doppler_axis_sign < 0) ? -1 : 1;
    out.ati_phase_to_velocity_sign = (cfg.ati_phase_to_velocity_sign < 0) ? -1 : 1;
    out.used_motion_comp = true;
    out.delta_t = std::numeric_limits<double>::quiet_NaN();
    out.C_ati = std::numeric_limits<double>::quiet_NaN();
    out.denom_without_k = std::numeric_limits<double>::quiet_NaN();
    out.denom = std::numeric_limits<double>::quiet_NaN();

    if (!(lambda > 0.0) && cfg.fc > 0.0) {
        lambda = C / (cfg.fc * 1.0e9);
    }
    if (!std::isfinite(phase) ||
        !std::isfinite(phase_ctdr) ||
        !std::isfinite(out.af_total) ||
        !std::isfinite(range_m) ||
        !(lambda > 0.0) ||
        !(plane.V > 1.0e-6) ||
        !(cfg.d_channel > 1.0e-9)) {
        out.fallback_legacy = true;
        return out;
    }

    const double af_limit = 2.0 * plane.V / lambda;
    const double fd_step = std::max(0.5, (cfg.fd_res > 0.0) ? cfg.fd_res * 0.25 : 2.0);
    const double s_dop = (cfg.motion_doppler_axis_sign < 0) ? -1.0 : 1.0;
    const double vmax = (cfg.ati_vmax_mps > 0.0)
        ? cfg.ati_vmax_mps
        : std::numeric_limits<double>::infinity();
    const double af_motion_limit = std::isfinite(vmax)
        ? (2.0 * vmax / lambda)
        : std::numeric_limits<double>::infinity();
    const double af_motion_margin = std::max(fd_step * 2.0,
                                             (cfg.fd_res > 0.0) ? 0.75 * cfg.fd_res : 2.0);
    auto phase_cost = [&](double af_geo) -> double {
        const double phi_static = ctdrStaticPhaseFromAfGeo(cfg, plane, lambda, range_m, af_geo);
        if (!std::isfinite(phi_static)) {
            return std::numeric_limits<double>::infinity();
        }
        return 1.0 - std::cos(wrapPiLocal(phase_ctdr - phi_static));
    };
    auto branch_allowed = [&](double af_geo) -> bool {
        if (!std::isfinite(af_motion_limit)) {
            return true;
        }
        return std::abs(out.af_total - af_geo) <= af_motion_limit + af_motion_margin;
    };
    auto better_branch = [&](double cost,
                             double af_geo,
                             double best_cost,
                             double best_af) -> bool {
        if (!std::isfinite(cost)) {
            return false;
        }
        if (!std::isfinite(best_cost)) {
            return true;
        }
        constexpr double kCostTie = 1.0e-10;
        if (cost + kCostTie < best_cost) {
            return true;
        }
        if (std::abs(cost - best_cost) <= kCostTie) {
            const double motion_abs = std::abs(out.af_total - af_geo);
            const double best_motion_abs = std::abs(out.af_total - best_af);
            return motion_abs < best_motion_abs;
        }
        return false;
    };

    double best_af = std::max(-af_limit, std::min(af_limit, out.af_total));
    double best_cost = branch_allowed(best_af) ? phase_cost(best_af)
                                               : std::numeric_limits<double>::infinity();
    for (double af = -af_limit; af <= af_limit + 0.5 * fd_step; af += fd_step) {
        if (!branch_allowed(af)) {
            continue;
        }
        const double cost = phase_cost(af);
        if (better_branch(cost, af, best_cost, best_af)) {
            best_cost = cost;
            best_af = af;
        }
    }
    if (!std::isfinite(best_cost)) {
        best_af = std::max(-af_limit, std::min(af_limit, out.af_total));
        best_cost = phase_cost(best_af);
        for (double af = -af_limit; af <= af_limit + 0.5 * fd_step; af += fd_step) {
            const double cost = phase_cost(af);
            if (better_branch(cost, af, best_cost, best_af)) {
                best_cost = cost;
                best_af = af;
            }
        }
    }

    double lo = std::max(-af_limit, best_af - fd_step);
    double hi = std::min(af_limit, best_af + fd_step);
    const double gr = 0.6180339887498949;
    double c = hi - gr * (hi - lo);
    double d = lo + gr * (hi - lo);
    double fc = phase_cost(c);
    double fd = phase_cost(d);
    for (int i = 0; i < 48; ++i) {
        if (fc < fd) {
            hi = d;
            d = c;
            fd = fc;
            c = hi - gr * (hi - lo);
            fc = phase_cost(c);
        } else {
            lo = c;
            c = d;
            fc = fd;
            d = lo + gr * (hi - lo);
            fd = phase_cost(d);
        }
    }
    out.af_geometry = (fc < fd) ? c : d;
    out.root1d_cost = std::min(fc, fd);
    out.af_motion = out.af_total - out.af_geometry;
    out.v_radial = out.af_motion * lambda / (2.0 * s_dop);
    out.phi_static = ctdrStaticPhaseFromAfGeo(cfg, plane, lambda, range_m, out.af_geometry);
    out.phi_static_geometry = out.phi_static;
    out.phi_static_total = ctdrStaticPhaseFromAfGeo(cfg, plane, lambda, range_m, out.af_total);
    out.phi_static_model = out.phi_static_total;
    out.phi_static_at_zero = ctdrStaticPhaseFromAfGeo(cfg, plane, lambda, range_m, 0.0);
    out.phi_res = wrapPiLocal(phase_ctdr - out.phi_static_total);
    out.phi_res_at_zero = wrapPiLocal(phase_ctdr - out.phi_static_at_zero);
    out.phi_motion = wrapPiLocal(phase_ctdr - out.phi_static_geometry);
    out.v_from_phase_raw = std::numeric_limits<double>::quiet_NaN();
    out.v_from_phi_res = std::numeric_limits<double>::quiet_NaN();
    out.k_eff_static_phase_df = std::numeric_limits<double>::quiet_NaN();
    out.v_analytic_mps = (solver_name == "analytic") ? out.v_radial : out.v_analytic_mps;
    out.v_iterative_mps = (solver_name == "iterative") ? out.v_radial : out.v_iterative_mps;
    out.v_root1d_mps = (solver_name == "root1d") ? out.v_radial : out.v_root1d_mps;
    out.af_geometry_analytic_hz = (solver_name == "analytic") ? out.af_geometry : out.af_geometry_analytic_hz;
    out.af_geometry_iterative_hz = (solver_name == "iterative") ? out.af_geometry : out.af_geometry_iterative_hz;
    out.af_geometry_root1d_hz = (solver_name == "root1d") ? out.af_geometry : out.af_geometry_root1d_hz;
    out.ok = std::isfinite(out.v_radial) &&
             std::isfinite(out.af_geometry) &&
             std::isfinite(out.af_motion) &&
             std::isfinite(out.phi_static);
    out.fallback_legacy = !out.ok;
    return out;
}

inline MotionCompResult solveOldMotionComp(const Config &cfg,
                                           double phase,
                                           double af_total,
                                           double k,
                                           double b,
                                           double lambda,
                                           double static_phase_ref = std::numeric_limits<double>::quiet_NaN())
{
    MotionCompResult out;
    (void)static_phase_ref;
    out.k = k;
    out.b = b;
    out.phi_meas = phase;
    out.af_phase = (std::abs(k) >= 1.0e-12) ? ((phase - b) / k) : 0.0;
    out.af_total = std::isfinite(af_total) ? af_total : out.af_phase;
    out.af_geometry = out.af_phase;
    out.phi_static_model = staticPhaseModel(out.af_total, k, b, cfg.ati_phase_bias_rad);
    out.phi_static = out.phi_static_model;
    out.phi_static_total = out.phi_static_model;
    out.phi_static_at_zero = staticPhaseModel(0.0, k, b, cfg.ati_phase_bias_rad);
    out.phi_res_at_zero = wrapPiLocal(phase - out.phi_static_at_zero);
    out.phi_res = wrapPiLocal(phase - out.phi_static_total);
    out.phi_static_geometry = out.phi_static_model;
    out.phi_motion = out.phi_res;
    out.phi_static_model_name = "p38_linear_fit";
    out.geometry_calib_mode = "linear_p38_phase_vs_doppler";
    out.solver_requested = normalizeMotionCompSolver(cfg.motion_comp_solver);
    out.solver = "old";
    out.v_radial = 0.0;
    out.af_motion = 0.0;
    out.v_old_mps = 0.0;
    out.af_geometry_old_hz = out.af_geometry;
    out.used_motion_comp = false;
    if (!(lambda > 0.0) && cfg.fc > 0.0) {
        lambda = C / (cfg.fc * 1.0e9);
    }
    const double delta_t = std::numeric_limits<double>::quiet_NaN();
    out.delta_t = delta_t;
    out.C_ati = std::numeric_limits<double>::quiet_NaN();
    out.k_eff_static_phase_df = std::numeric_limits<double>::quiet_NaN();
    return out;
}

inline MotionCompResult solveIterativeMotionComp(const Config &cfg,
                                                 const GMTIOutput::Plane &plane,
                                                 double phase,
                                                 double af_total,
                                                 double k,
                                                 double b,
                                                 double lambda,
                                                 double static_phase_ref = std::numeric_limits<double>::quiet_NaN())
{
    MotionCompResult out;
    out.k = k;
    out.b = b;
    out.phi_meas = phase;
    out.af_phase = (std::abs(k) >= 1.0e-12) ? ((phase - b) / k) : 0.0;
    out.af_total = std::isfinite(af_total) ? af_total : out.af_phase;
    out.phi_static_model_name = "strict_two_channel_geometry";
    out.geometry_calib_mode = "strict_two_channel_forward";
    out.solver_requested = "iterative";
    out.solver = "iterative";
    out.p38_theory_sign = configuredP38TheorySign(cfg);
    out.motion_doppler_axis_sign = (cfg.motion_doppler_axis_sign < 0) ? -1 : 1;
    out.ati_phase_to_velocity_sign = (cfg.ati_phase_to_velocity_sign < 0) ? -1 : 1;
    out.used_motion_comp = true;

    if (!(lambda > 0.0) && cfg.fc > 0.0) {
        lambda = C / (cfg.fc * 1.0e9);
    }
    if (!(lambda > 0.0) || !(cfg.d_channel > 0.0) || !(cfg.ati_vmax_mps > 0.0)) {
        out.fallback_legacy = true;
        return out;
    }

    const double delta_t = configuredCtdrEquivalentTwoWayDelay(cfg, plane.V);
    out.delta_t = delta_t;
    const double s_phi = (cfg.ati_phase_to_velocity_sign < 0) ? -1.0 : 1.0;
    const double s_dop = (cfg.motion_doppler_axis_sign < 0) ? -1.0 : 1.0;
    const double C_ati = s_phi * 4.0 * M_PI * delta_t / lambda;
    out.C_ati = C_ati;
    out.denom_without_k = C_ati;
    out.k_eff_static_phase_df = motionStaticPhaseKeff(delta_t, k, static_phase_ref);
    if (!std::isfinite(C_ati) || std::abs(C_ati) < std::max(0.0, cfg.motion_comp_denom_min)) {
        out.fallback_legacy = true;
        return out;
    }

    double v = 0.0;
    double last_v = v;
    const int max_iter = std::max(1, cfg.motion_comp_iter);
    const double vmax = (cfg.ati_vmax_mps > 0.0) ? cfg.ati_vmax_mps : std::numeric_limits<double>::infinity();
    for (int iter = 0; iter < max_iter; ++iter) {
        const double af_motion = s_dop * 2.0 * v / lambda;
        const double af_geo = out.af_total - af_motion;
        const double phi_static = motionStaticPhaseModel(af_geo, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref);
        const double phi_res = wrapPiLocal(phase - phi_static);
        const double v_new = phi_res / C_ati;
        if (!std::isfinite(v_new) || std::abs(v_new) > vmax) {
            out.fallback_legacy = true;
            return out;
        }
        last_v = v;
        v = v_new;
        if (std::abs(v - last_v) < std::max(0.0, cfg.motion_comp_iter_tol_mps)) {
            break;
        }
    }

    out.v_radial = v;
    out.v_iterative_mps = v;
    out.af_motion = s_dop * 2.0 * v / lambda;
    out.af_geometry = out.af_total - out.af_motion;
    out.af_geometry_iterative_hz = out.af_geometry;
    out.phi_static_total = motionStaticPhaseModel(out.af_total, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref);
    out.phi_static_model = out.phi_static_total;
    out.phi_static = motionStaticPhaseModel(out.af_geometry, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref);
    out.phi_static_geometry = out.phi_static;
    out.phi_static_at_zero = motionStaticPhaseModel(0.0, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref);
    out.phi_res_at_zero = wrapPiLocal(phase - out.phi_static_at_zero);
    out.phi_res = wrapPiLocal(phase - out.phi_static_total);
    out.phi_motion = wrapPiLocal(phase - out.phi_static_geometry);
    out.v_from_phase_raw = phase / C_ati;
    out.v_from_phi_res = out.phi_res / C_ati;
    out.denom = C_ati;
    out.ok = std::isfinite(out.v_radial) &&
             std::isfinite(out.af_geometry) &&
             std::isfinite(out.af_motion) &&
             std::isfinite(out.phi_motion);
    out.fallback_legacy = !out.ok;
    return out;
}

inline MotionCompResult solveAnalyticMotionComp(const Config &cfg,
                                                const GMTIOutput::Plane &plane,
                                                double phase,
                                                double af_total,
                                                double k,
                                                double b,
                                                double lambda,
                                                double static_phase_ref = std::numeric_limits<double>::quiet_NaN())
{
    MotionCompResult out;
    out.k = k;
    out.b = b;
    out.phi_meas = phase;
    out.af_phase = (std::abs(k) >= 1.0e-12) ? ((phase - b) / k) : 0.0;
    out.af_total = std::isfinite(af_total) ? af_total : out.af_phase;
    out.phi_static_model_name = "strict_two_channel_geometry";
    out.geometry_calib_mode = "strict_two_channel_forward";
    out.solver_requested = "analytic";
    out.solver = "analytic";
    out.p38_theory_sign = configuredP38TheorySign(cfg);
    out.motion_doppler_axis_sign = (cfg.motion_doppler_axis_sign < 0) ? -1 : 1;
    out.ati_phase_to_velocity_sign = (cfg.ati_phase_to_velocity_sign < 0) ? -1 : 1;
    out.used_motion_comp = true;

    if (!(lambda > 0.0) && cfg.fc > 0.0) {
        lambda = C / (cfg.fc * 1.0e9);
    }
    const double delta_t = configuredCtdrEquivalentTwoWayDelay(cfg, plane.V);
    out.delta_t = delta_t;
    if (!std::isfinite(phase) ||
        !(lambda > 0.0) ||
        !(plane.V > 1.0e-6) ||
        !(cfg.d_channel > 1.0e-6) ||
        !std::isfinite(delta_t) ||
        !(std::abs(k) >= 1.0e-12)) {
        out.fallback_legacy = true;
        return out;
    }

    const double s_phi = (cfg.ati_phase_to_velocity_sign < 0) ? -1.0 : 1.0;
    const double s_dop = (cfg.motion_doppler_axis_sign < 0) ? -1.0 : 1.0;
    const double C_ati = s_phi * 4.0 * M_PI * delta_t / lambda;
    const double phi_static_total = motionStaticPhaseModel(out.af_total, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref);
    const double phi_res = wrapPiLocal(phase - phi_static_total);
    const double k_eff = motionStaticPhaseKeff(delta_t, k, static_phase_ref);
    const double denom = C_ati - k_eff * s_dop * 2.0 / lambda;

    out.phi_static_model = phi_static_total;
    out.phi_static_total = phi_static_total;
    out.phi_static_at_zero = motionStaticPhaseModel(0.0, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref);
    out.phi_res_at_zero = wrapPiLocal(phase - out.phi_static_at_zero);
    out.phi_res = phi_res;
    out.C_ati = C_ati;
    out.k_eff_static_phase_df = k_eff;
    out.denom_without_k = C_ati;
    out.denom = denom;
    out.v_from_phase_raw = phase / C_ati;
    out.v_from_phi_res = phi_res / C_ati;

    if (!std::isfinite(denom) || std::abs(denom) < std::max(0.0, cfg.motion_comp_denom_min)) {
        out.fallback_legacy = true;
        return out;
    }

    auto analytic_residual = [&](double v) -> double {
        const double af_motion = s_dop * 2.0 * v / lambda;
        const double af_geo = out.af_total - af_motion;
        const double phi_static = motionStaticPhaseModel(af_geo, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref);
        const double phi_pred = phi_static + C_ati * v;
        return wrapPiLocal(phase - phi_pred);
    };
    auto analytic_cost = [&](double v) -> double {
        const double r = analytic_residual(v);
        return 1.0 - std::cos(r);
    };
    const double vmax = (cfg.ati_vmax_mps > 0.0) ? cfg.ati_vmax_mps : 50.0;
    const double step = std::max(0.001, cfg.motion_comp_root_grid_step_mps);
    double best_v = std::max(-vmax, std::min(vmax, phi_res / denom));
    double best_cost = analytic_cost(best_v);
    for (double v = -vmax; v <= vmax + 0.5 * step; v += step) {
        const double c = analytic_cost(v);
        if (c < best_cost) {
            best_cost = c;
            best_v = v;
        }
    }
    double lo = std::max(-vmax, best_v - step);
    double hi = std::min(vmax, best_v + step);
    const double gr = 0.6180339887498949;
    double c = hi - gr * (hi - lo);
    double d = lo + gr * (hi - lo);
    double fc = analytic_cost(c);
    double fd = analytic_cost(d);
    for (int i = 0; i < 48; ++i) {
        if (fc < fd) {
            hi = d;
            d = c;
            fd = fc;
            c = hi - gr * (hi - lo);
            fc = analytic_cost(c);
        } else {
            lo = c;
            c = d;
            fc = fd;
            d = lo + gr * (hi - lo);
            fd = analytic_cost(d);
        }
    }
    out.v_radial = (fc < fd) ? c : d;
    out.af_motion = s_dop * 2.0 * out.v_radial / lambda;
    out.af_geometry = out.af_total - out.af_motion;
    out.phi_static = motionStaticPhaseModel(out.af_geometry, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref);
    out.phi_static_geometry = out.phi_static;
    out.phi_motion = wrapPiLocal(phase - motionStaticPhaseModel(out.af_geometry, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref));
    out.ok = std::isfinite(out.v_radial) &&
             std::isfinite(out.af_geometry) &&
             std::isfinite(out.af_motion) &&
             std::isfinite(out.phi_motion);
    out.fallback_legacy = !out.ok;
    out.v_analytic_mps = out.v_radial;
    out.af_geometry_analytic_hz = out.af_geometry;
    return out;
}

inline MotionCompResult solveRoot1DMotionComp(const Config &cfg,
                                              const GMTIOutput::Plane &plane,
                                              double phase,
                                              double af_total,
                                              double k,
                                              double b,
                                              double lambda,
                                              double static_phase_ref = std::numeric_limits<double>::quiet_NaN())
{
    MotionCompResult out;
    out.k = k;
    out.b = b;
    out.phi_meas = phase;
    out.af_phase = (std::abs(k) >= 1.0e-12) ? ((phase - b) / k) : 0.0;
    out.af_total = std::isfinite(af_total) ? af_total : out.af_phase;
    out.phi_static_model_name = "strict_two_channel_geometry";
    out.geometry_calib_mode = "strict_two_channel_forward";
    out.solver_requested = "root1d";
    out.solver = "root1d";
    out.p38_theory_sign = configuredP38TheorySign(cfg);
    out.motion_doppler_axis_sign = (cfg.motion_doppler_axis_sign < 0) ? -1 : 1;
    out.ati_phase_to_velocity_sign = (cfg.ati_phase_to_velocity_sign < 0) ? -1 : 1;
    out.used_motion_comp = true;

    if (!(lambda > 0.0) && cfg.fc > 0.0) {
        lambda = C / (cfg.fc * 1.0e9);
    }
    const double delta_t = configuredCtdrEquivalentTwoWayDelay(cfg, plane.V);
    out.delta_t = delta_t;
    if (!std::isfinite(phase) ||
        !(lambda > 0.0) ||
        !(plane.V > 1.0e-6) ||
        !(cfg.d_channel > 1.0e-6) ||
        !std::isfinite(delta_t)) {
        out.fallback_legacy = true;
        return out;
    }

    const double s_phi = (cfg.ati_phase_to_velocity_sign < 0) ? -1.0 : 1.0;
    const double s_dop = (cfg.motion_doppler_axis_sign < 0) ? -1.0 : 1.0;
    const double C_ati = s_phi * 4.0 * M_PI * delta_t / lambda;
    out.C_ati = C_ati;
    out.denom_without_k = C_ati;
    out.k_eff_static_phase_df = motionStaticPhaseKeff(delta_t, k, static_phase_ref);

    const double vmax = (cfg.ati_vmax_mps > 0.0) ? cfg.ati_vmax_mps : 50.0;
    const double step = std::max(0.001, cfg.motion_comp_root_grid_step_mps);

    auto residual = [&](double v) -> double {
        const double af_motion = s_dop * 2.0 * v / lambda;
        const double af_geo = out.af_total - af_motion;
        const double phi_static = motionStaticPhaseModel(af_geo, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref);
        const double phi_pred = phi_static + C_ati * v;
        return wrapPiLocal(phase - phi_pred);
    };
    auto cost = [&](double v) -> double {
        const double r = residual(v);
        return 1.0 - std::cos(r);
    };

    double best_v = 0.0;
    double best_cost = std::numeric_limits<double>::infinity();
    for (double v = -vmax; v <= vmax + 0.5 * step; v += step) {
        const double c = cost(v);
        if (c < best_cost) {
            best_cost = c;
            best_v = v;
        }
    }

    double lo = std::max(-vmax, best_v - step);
    double hi = std::min(vmax, best_v + step);
    const double gr = 0.6180339887498949;
    double c = hi - gr * (hi - lo);
    double d = lo + gr * (hi - lo);
    double fc = cost(c);
    double fd = cost(d);
    for (int i = 0; i < 48; ++i) {
        if (fc < fd) {
            hi = d;
            d = c;
            fd = fc;
            c = hi - gr * (hi - lo);
            fc = cost(c);
        } else {
            lo = c;
            c = d;
            fc = fd;
            d = lo + gr * (hi - lo);
            fd = cost(d);
        }
    }
    double v = (fc < fd) ? c : d;
    const double final_cost = std::min(fc, fd);
    out.root1d_cost = final_cost;
    out.v_radial = v;
    out.v_root1d_mps = v;

    if (!std::isfinite(v) || final_cost > std::max(0.0, cfg.motion_comp_root_cost_max)) {
        out.fallback_legacy = true;
        return out;
    }

    out.af_motion = s_dop * 2.0 * v / lambda;
    out.af_geometry = out.af_total - out.af_motion;
    out.af_geometry_root1d_hz = out.af_geometry;
    out.phi_static_model = motionStaticPhaseModel(out.af_total, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref);
    out.phi_static_total = motionStaticPhaseModel(out.af_total, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref);
    out.phi_static_at_zero = motionStaticPhaseModel(0.0, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref);
    out.phi_res_at_zero = wrapPiLocal(phase - out.phi_static_at_zero);
    out.phi_res = wrapPiLocal(phase - out.phi_static_total);
    out.phi_static = motionStaticPhaseModel(out.af_geometry, k, b, cfg.ati_phase_bias_rad, delta_t, static_phase_ref);
    out.phi_static_geometry = out.phi_static;
    out.phi_motion = wrapPiLocal(phase - out.phi_static_geometry);
    out.v_from_phase_raw = phase / C_ati;
    out.v_from_phi_res = out.phi_res / C_ati;
    out.ok = std::isfinite(out.v_radial) &&
             std::isfinite(out.af_geometry) &&
             std::isfinite(out.af_motion) &&
             std::isfinite(out.phi_motion);
    out.fallback_legacy = !out.ok;
    return out;
}

struct VelocityAmbiguityCandidate {
    int doppler_order = 0;
    int phase_order = 0;
    double af_alias_hz = std::numeric_limits<double>::quiet_NaN();
    double af_unwrapped_hz = std::numeric_limits<double>::quiet_NaN();
    double af_geometry_hz = std::numeric_limits<double>::quiet_NaN();
    double velocity_mps = std::numeric_limits<double>::quiet_NaN();
    double phase_unwrapped_rad = std::numeric_limits<double>::quiet_NaN();
    double phase_residual_rad = std::numeric_limits<double>::quiet_NaN();
    double beam_residual_deg = std::numeric_limits<double>::quiet_NaN();
    double cost = std::numeric_limits<double>::infinity();
};

inline double ambiguityThetaFromAfGeoDeg(double af_geo,
                                         double platform_speed_mps,
                                         double lambda)
{
    if (!(platform_speed_mps > 0.0) || !(lambda > 0.0) || !std::isfinite(af_geo)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double sin_a = af_geo * lambda / (2.0 * platform_speed_mps);
    if (!std::isfinite(sin_a) || std::abs(sin_a) > 1.0) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    // Production estimateSquintAngleDeg() uses f_geo=-2V*sin(theta)/lambda.
    return -std::asin(std::max(-1.0, std::min(1.0, sin_a))) * 180.0 / M_PI;
}

inline double ambiguityBeamGateDeg(const Config &cfg)
{
    if (cfg.velocity_beam_gate_deg > 0.0) {
        return cfg.velocity_beam_gate_deg;
    }
    if (cfg.loc_beam_gate_deg > 0.0) {
        return cfg.loc_beam_gate_deg;
    }
    return std::max(0.0, cfg.beamwidth_deg * 0.5) + 0.5;
}

inline std::vector<VelocityAmbiguityCandidate> buildVelocityAmbiguityCandidates(
    const Config &cfg,
    const GMTIOutput::Plane &plane,
    double raw_phase_wrapped,
    double af_alias_hz,
    double lambda,
    double range_m,
    double theta_true_deg)
{
    std::vector<VelocityAmbiguityCandidate> candidates;
    if (!(cfg.PRF > 0.0) || !(lambda > 0.0) || !(plane.V > 1.0e-6) ||
        !(cfg.d_channel > 1.0e-9) || !std::isfinite(raw_phase_wrapped) ||
        !std::isfinite(af_alias_hz) || !std::isfinite(range_m)) {
        return candidates;
    }

    const double af_physical_limit = 2.0 * plane.V / lambda;
    double af_lo = -af_physical_limit;
    double af_hi = af_physical_limit;
    const double beam_gate_deg = ambiguityBeamGateDeg(cfg);
    if (std::isfinite(theta_true_deg) && beam_gate_deg > 0.0) {
        const double theta_lo = std::max(-89.999, theta_true_deg - beam_gate_deg);
        const double theta_hi = std::min(89.999, theta_true_deg + beam_gate_deg);
        const double a = -2.0 * plane.V * std::sin(theta_lo * M_PI / 180.0) / lambda;
        const double b = -2.0 * plane.V * std::sin(theta_hi * M_PI / 180.0) / lambda;
        af_lo = std::max(af_lo, std::min(a, b));
        af_hi = std::min(af_hi, std::max(a, b));
    }
    if (!(af_hi > af_lo)) {
        return candidates;
    }

    struct PhaseRoot {
        int order = 0;
        double af_geometry_hz = std::numeric_limits<double>::quiet_NaN();
        double phase_unwrapped_rad = std::numeric_limits<double>::quiet_NaN();
        double phase_residual_rad = std::numeric_limits<double>::quiet_NaN();
    };
    std::vector<PhaseRoot> phase_roots;
    const double phase_wrapped = wrapPiLocal(raw_phase_wrapped);
    const int phase_order_max = std::max(0, cfg.velocity_max_phase_order);
    const int grid_count = 128;
    const double root_merge_hz = std::max(1.0e-6, (af_hi - af_lo) * 1.0e-9);
    for (int m = -phase_order_max; m <= phase_order_max; ++m) {
        const double target_phase = phase_wrapped + 2.0 * M_PI * static_cast<double>(m);
        auto residual = [&](double af_geo) {
            return ctdrStaticPhaseUnwrappedFromAfGeo(
                cfg, plane, lambda, range_m, af_geo) - target_phase;
        };
        double x_prev = af_lo;
        double r_prev = residual(x_prev);
        for (int gi = 1; gi <= grid_count; ++gi) {
            const double x_cur = af_lo + (af_hi - af_lo) *
                static_cast<double>(gi) / static_cast<double>(grid_count);
            const double r_cur = residual(x_cur);
            if (!std::isfinite(r_prev) || !std::isfinite(r_cur)) {
                x_prev = x_cur;
                r_prev = r_cur;
                continue;
            }
            const bool endpoint_root = std::abs(r_prev) <= 1.0e-12;
            const bool crosses = (r_prev < 0.0 && r_cur > 0.0) ||
                                 (r_prev > 0.0 && r_cur < 0.0) ||
                                 std::abs(r_cur) <= 1.0e-12;
            if (endpoint_root || crosses) {
                double lo = x_prev;
                double hi = x_cur;
                double rlo = r_prev;
                double root = endpoint_root ? x_prev : x_cur;
                if (!endpoint_root && std::abs(r_cur) > 1.0e-12) {
                    for (int iter = 0; iter < 64; ++iter) {
                        const double mid = 0.5 * (lo + hi);
                        const double rm = residual(mid);
                        if (!std::isfinite(rm)) break;
                        root = mid;
                        if (std::abs(rm) <= 1.0e-13) break;
                        if ((rlo <= 0.0 && rm >= 0.0) ||
                            (rlo >= 0.0 && rm <= 0.0)) {
                            hi = mid;
                        } else {
                            lo = mid;
                            rlo = rm;
                        }
                    }
                }
                bool duplicate = false;
                for (const PhaseRoot &old : phase_roots) {
                    if (old.order == m &&
                        std::abs(old.af_geometry_hz - root) <= root_merge_hz) {
                        duplicate = true;
                        break;
                    }
                }
                if (!duplicate) {
                    PhaseRoot pr;
                    pr.order = m;
                    pr.af_geometry_hz = root;
                    pr.phase_unwrapped_rad = target_phase;
                    pr.phase_residual_rad = residual(root);
                    phase_roots.push_back(pr);
                }
            }
            x_prev = x_cur;
            r_prev = r_cur;
        }
    }

    const double s_dop = (cfg.motion_doppler_axis_sign < 0) ? -1.0 : 1.0;
    const double phase_sigma = std::max(1.0e-12, cfg.velocity_phase_sigma_rad);
    const double beam_sigma = std::max(1.0e-12, cfg.velocity_beam_sigma_deg);
    const bool use_speed_prior = std::isfinite(cfg.velocity_speed_prior_mps) &&
                                 cfg.velocity_speed_prior_sigma_mps > 0.0;
    const int doppler_order_max = std::max(0, cfg.velocity_max_doppler_order);
    for (const PhaseRoot &root : phase_roots) {
        const double theta_candidate = ambiguityThetaFromAfGeoDeg(
            root.af_geometry_hz, plane.V, lambda);
        const double beam_residual = std::isfinite(theta_true_deg) &&
                                     std::isfinite(theta_candidate)
            ? (theta_candidate - theta_true_deg)
            : 0.0;
        for (int n = -doppler_order_max; n <= doppler_order_max; ++n) {
            const double af_unwrapped = af_alias_hz + static_cast<double>(n) * cfg.PRF;
            const double velocity = (af_unwrapped - root.af_geometry_hz) *
                                    lambda / (2.0 * s_dop);
            if (!std::isfinite(velocity) ||
                velocity < cfg.velocity_search_min_mps - 1.0e-12 ||
                velocity > cfg.velocity_search_max_mps + 1.0e-12) {
                continue;
            }
            VelocityAmbiguityCandidate candidate;
            candidate.doppler_order = n;
            candidate.phase_order = root.order;
            candidate.af_alias_hz = af_alias_hz;
            candidate.af_unwrapped_hz = af_unwrapped;
            candidate.af_geometry_hz = root.af_geometry_hz;
            candidate.velocity_mps = velocity;
            candidate.phase_unwrapped_rad = root.phase_unwrapped_rad;
            candidate.phase_residual_rad = root.phase_residual_rad;
            candidate.beam_residual_deg = beam_residual;
            const double phase_cost = root.phase_residual_rad / phase_sigma;
            const double beam_cost = beam_residual / beam_sigma;
            candidate.cost = phase_cost * phase_cost + beam_cost * beam_cost;
            if (use_speed_prior) {
                const double speed_cost =
                    (velocity - cfg.velocity_speed_prior_mps) /
                    cfg.velocity_speed_prior_sigma_mps;
                candidate.cost += speed_cost * speed_cost;
            }
            candidates.push_back(candidate);
        }
    }
    std::sort(candidates.begin(), candidates.end(),
              [](const VelocityAmbiguityCandidate &a,
                 const VelocityAmbiguityCandidate &b) {
        if (std::abs(a.cost - b.cost) > 1.0e-12) return a.cost < b.cost;
        if (std::abs(std::abs(a.velocity_mps) - std::abs(b.velocity_mps)) > 1.0e-12) {
            return std::abs(a.velocity_mps) < std::abs(b.velocity_mps);
        }
        if (std::abs(a.doppler_order) != std::abs(b.doppler_order)) {
            return std::abs(a.doppler_order) < std::abs(b.doppler_order);
        }
        return std::abs(a.phase_order) < std::abs(b.phase_order);
    });
    return candidates;
}

inline MotionCompResult solveJointVelocityAmbiguity(
    const Config &cfg,
    const GMTIOutput::Plane &plane,
    double corrected_phase,
    double raw_ctdr_phase,
    double af_alias_hz,
    double k,
    double b,
    double lambda,
    double range_m,
    double theta_true_deg,
    double range_phase_correction)
{
    MotionCompResult out;
    out.k = k;
    out.b = b;
    out.phi_meas = corrected_phase;
    out.phi_meas_ctdr_raw = raw_ctdr_phase;
    out.range_phase_correction = range_phase_correction;
    out.af_phase = (std::abs(k) >= 1.0e-12)
        ? ((corrected_phase - b) / k)
        : af_alias_hz;
    out.af_alias_hz = af_alias_hz;
    out.af_total = af_alias_hz;
    out.phase_wrapped_rad = wrapPiLocal(raw_ctdr_phase);
    out.p38_theory_sign = configuredP38TheorySign(cfg);
    out.motion_doppler_axis_sign = (cfg.motion_doppler_axis_sign < 0) ? -1 : 1;
    out.ati_phase_to_velocity_sign = (cfg.ati_phase_to_velocity_sign < 0) ? -1 : 1;
    out.phi_static_model_name = "ctdr_true_path_difference";
    out.geometry_calib_mode = "joint_prf_phase_ambiguity_ctdr";
    out.solver_requested = "joint_ambiguity";
    out.solver = "joint_n_m_ctdr";
    out.velocity_solver = "joint_n_m_ctdr";
    out.ambiguity_status = "no_candidate";
    out.velocity_confidence = "none";
    out.used_motion_comp = true;
    out.delta_t = configuredCtdrEquivalentTwoWayDelay(cfg, plane.V);

    std::vector<VelocityAmbiguityCandidate> candidates =
        buildVelocityAmbiguityCandidates(cfg, plane, raw_ctdr_phase,
                                         af_alias_hz, lambda, range_m,
                                         theta_true_deg);
    const std::size_t total_candidates = candidates.size();
    out.velocity_candidate_count = static_cast<int>(std::min<std::size_t>(
        total_candidates, static_cast<std::size_t>(std::numeric_limits<int>::max())));
    const std::size_t max_candidates = static_cast<std::size_t>(
        std::max(1, cfg.velocity_max_candidates));
    const bool truncated = candidates.size() > max_candidates;
    if (truncated) {
        candidates.resize(max_candidates);
    }
    if (candidates.empty()) {
        out.fallback_legacy = true;
        return out;
    }

    const VelocityAmbiguityCandidate &best = candidates.front();
    out.af_unwrapped_hz = best.af_unwrapped_hz;
    out.af_total = best.af_unwrapped_hz;
    out.doppler_ambiguity_order = best.doppler_order;
    out.phase_unwrapped_rad = best.phase_unwrapped_rad;
    out.phase_ambiguity_order = best.phase_order;
    out.velocity_best_cost = best.cost;
    if (candidates.size() > 1) {
        out.velocity_second_cost = candidates[1].cost;
        out.velocity_cost_margin = candidates[1].cost - best.cost;
    } else {
        out.velocity_second_cost = std::numeric_limits<double>::infinity();
        out.velocity_cost_margin = std::numeric_limits<double>::infinity();
    }

    const bool equivalent_best = candidates.size() > 1 &&
        out.velocity_cost_margin <= cfg.velocity_equivalent_cost_tolerance;
    const bool resolved = !truncated && !equivalent_best &&
        (candidates.size() == 1 ||
         out.velocity_cost_margin >= cfg.velocity_cost_margin_min);
    out.ambiguity_status = resolved ? "resolved"
        : (truncated ? "unresolved_truncated" : "unresolved");
    if (!resolved) {
        out.velocity_confidence = "low";
    } else if (!std::isfinite(out.velocity_cost_margin) ||
               out.velocity_cost_margin >= 4.0 * cfg.velocity_cost_margin_min) {
        out.velocity_confidence = "high";
    } else {
        out.velocity_confidence = "medium";
    }

    out.v_radial = best.velocity_mps;
    out.af_geometry = best.af_geometry_hz;
    out.af_motion = out.af_total - out.af_geometry;
    out.phi_static = ctdrStaticPhaseFromAfGeo(
        cfg, plane, lambda, range_m, out.af_geometry);
    out.phi_static_geometry = out.phi_static;
    out.phi_static_total = ctdrStaticPhaseFromAfGeo(
        cfg, plane, lambda, range_m, out.af_total);
    out.phi_static_model = out.phi_static_total;
    out.phi_static_at_zero = ctdrStaticPhaseFromAfGeo(
        cfg, plane, lambda, range_m, 0.0);
    out.phi_res = wrapPiLocal(raw_ctdr_phase - out.phi_static_total);
    out.phi_res_at_zero = wrapPiLocal(raw_ctdr_phase - out.phi_static_at_zero);
    out.phi_motion = wrapPiLocal(raw_ctdr_phase - out.phi_static_geometry);
    out.v_root1d_mps = out.v_radial;
    out.af_geometry_root1d_hz = out.af_geometry;
    out.root1d_cost = out.velocity_best_cost;
    out.ok = std::isfinite(out.v_radial) &&
             std::isfinite(out.af_geometry) &&
             std::isfinite(out.af_motion) &&
             std::isfinite(out.phi_static);
    out.fallback_legacy = !out.ok;
    return out;
}

inline MotionCompResult solveMotionCompensation(const Config &cfg,
                                                const GMTIOutput::Plane &plane,
                                                double phase,
                                                double af_total,
                                                double k,
                                                double b,
                                                double lambda,
                                                double range_m = std::numeric_limits<double>::quiet_NaN(),
                                                double theta_true_deg = std::numeric_limits<double>::quiet_NaN(),
                                                double range_phase_correction = std::numeric_limits<double>::quiet_NaN())
{
    if (!(lambda > 0.0) && cfg.fc > 0.0) {
        lambda = C / (cfg.fc * 1.0e9);
    }
    const double static_phase_ref = beamStaticPhaseRef(cfg, plane, lambda, theta_true_deg);
    const double raw_ctdr_phase =
        restoreCtdrRawPhase(phase, range_phase_correction);
    const std::string solver = normalizeMotionCompSolver(cfg.motion_comp_solver);
    auto populate_single_branch_diagnostics = [&](MotionCompResult &result) {
        result.af_alias_hz = af_total;
        result.af_unwrapped_hz = result.af_total;
        result.doppler_ambiguity_order = 0;
        result.phase_wrapped_rad = wrapPiLocal(raw_ctdr_phase);
        result.phase_unwrapped_rad = result.phase_wrapped_rad;
        result.phase_ambiguity_order = 0;
        result.velocity_candidate_count = result.ok ? 1 : 0;
        result.velocity_solver = "legacy_single_branch";
        result.velocity_confidence = "not_evaluated";
        result.ambiguity_status = "disabled";
    };
    MotionCompResult out;
    if (cfg.motion_comp_enable && cfg.velocity_ambiguity_enable && solver != "old") {
        return solveJointVelocityAmbiguity(
            cfg, plane, phase, raw_ctdr_phase, af_total, k, b, lambda,
            range_m, theta_true_deg, range_phase_correction);
    }
    if (!cfg.motion_comp_enable || solver == "old") {
        out = solveOldMotionComp(cfg, phase, af_total, k, b, lambda, static_phase_ref);
    } else if (solver == "debug") {
        const MotionCompResult old_res = solveOldMotionComp(cfg, phase, af_total, k, b, lambda, static_phase_ref);
        const MotionCompResult iter_res = solveCtdrMotionComp(
            cfg, plane, phase, af_total, k, b, lambda, range_m, "iterative",
            raw_ctdr_phase, range_phase_correction);
        const MotionCompResult ana_res = solveCtdrMotionComp(
            cfg, plane, phase, af_total, k, b, lambda, range_m, "analytic",
            raw_ctdr_phase, range_phase_correction);
        const MotionCompResult root_res = solveCtdrMotionComp(
            cfg, plane, phase, af_total, k, b, lambda, range_m, "root1d",
            raw_ctdr_phase, range_phase_correction);

        out = root_res.ok ? root_res : ana_res;
        out.solver_requested = "debug";
        out.solver = "debug";
        out.used_motion_comp = true;
        out.v_old_mps = old_res.v_radial;
        out.af_geometry_old_hz = old_res.af_geometry;
        out.v_iterative_mps = iter_res.v_radial;
        out.v_analytic_mps = ana_res.v_radial;
        out.v_root1d_mps = root_res.v_radial;
        out.af_geometry_iterative_hz = iter_res.af_geometry;
        out.af_geometry_analytic_hz = ana_res.af_geometry;
        out.af_geometry_root1d_hz = root_res.af_geometry;
        out.root1d_cost = root_res.root1d_cost;
        populate_single_branch_diagnostics(out);
        return out;
    } else {
        out = solveCtdrMotionComp(cfg, plane, phase, af_total, k, b, lambda,
                                  range_m, solver, raw_ctdr_phase,
                                  range_phase_correction);
    }

    const bool want_compare = (solver == "debug");
    if (want_compare && cfg.motion_comp_enable) {
        const MotionCompResult old_res = solveOldMotionComp(cfg, phase, af_total, k, b, lambda, static_phase_ref);
        const MotionCompResult iter = solveIterativeMotionComp(cfg, plane, phase, af_total, k, b, lambda, static_phase_ref);
        const MotionCompResult ana = solveAnalyticMotionComp(cfg, plane, phase, af_total, k, b, lambda, static_phase_ref);
        const MotionCompResult root = solveRoot1DMotionComp(cfg, plane, phase, af_total, k, b, lambda, static_phase_ref);
        out.v_old_mps = old_res.v_radial;
        out.af_geometry_old_hz = old_res.af_geometry;
        out.v_iterative_mps = iter.v_radial;
        out.v_analytic_mps = ana.v_radial;
        out.v_root1d_mps = root.v_radial;
        out.af_geometry_iterative_hz = iter.af_geometry;
        out.af_geometry_analytic_hz = ana.af_geometry;
        out.af_geometry_root1d_hz = root.af_geometry;
        out.root1d_cost = root.root1d_cost;
    }
    populate_single_branch_diagnostics(out);
    return out;
}

#endif // MOTION_COMP_HPP
