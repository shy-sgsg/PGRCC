#include "motion_comp.hpp"

#include <cmath>
#include <iostream>
#include <string>

namespace {

struct Observation {
    double af_alias_hz = 0.0;
    int truth_order = 0;
};

Observation aliasObservation(const Config &cfg,
                             double af_geometry_hz,
                             double velocity_mps)
{
    const double sign = (cfg.motion_doppler_axis_sign < 0) ? -1.0 : 1.0;
    const double motion_hz = sign * 2.0 * velocity_mps / cfg.lambda;
    const int order = static_cast<int>(std::llround(motion_hz / cfg.PRF));
    Observation out;
    out.truth_order = order;
    out.af_alias_hz = af_geometry_hz + motion_hz -
                      static_cast<double>(order) * cfg.PRF;
    return out;
}

bool checkCase(const std::string &name,
               Config cfg,
               const GMTIOutput::Plane &plane,
               double theta_deg,
               double range_m,
               double truth_velocity_mps,
               double search_min_mps,
               double search_max_mps,
               const std::string &expected_status,
               bool require_truth_order)
{
    cfg.velocity_search_min_mps = search_min_mps;
    cfg.velocity_search_max_mps = search_max_mps;
    const double af_geometry_hz =
        -2.0 * plane.V * std::sin(theta_deg * M_PI / 180.0) / cfg.lambda;
    const double raw_phase = ctdrStaticPhaseFromAfGeo(
        cfg, plane, cfg.lambda, range_m, af_geometry_hz);
    const Observation obs = aliasObservation(cfg, af_geometry_hz,
                                             truth_velocity_mps);
    const double p38_k = gmti::ctdr::rawInterferometricSlopeRadPerHz(
        cfg.d_channel, plane.V,
        cfg.rx_baseline_sign, cfg.channel_phase_sign);
    const double p38_b = wrapPiLocal(raw_phase - p38_k * af_geometry_hz);
    const MotionCompResult solved = solveMotionCompensation(
        cfg, plane, raw_phase, obs.af_alias_hz, p38_k, p38_b,
        cfg.lambda, range_m, theta_deg);

    const bool velocity_ok = !require_truth_order ||
        std::abs(solved.v_radial - truth_velocity_mps) < 1.0e-5;
    const bool order_ok = !require_truth_order ||
        solved.doppler_ambiguity_order == obs.truth_order;
    const bool geometry_ok = std::isfinite(solved.af_geometry) &&
        std::abs(solved.af_geometry - af_geometry_hz) < 1.0e-4;
    const bool phase_ok = std::abs(wrapPiLocal(
        solved.phase_unwrapped_rad - raw_phase)) < 1.0e-9;
    const bool ok = solved.ok &&
                    solved.ambiguity_status == expected_status &&
                    solved.velocity_candidate_count >= 1 &&
                    geometry_ok && phase_ok && velocity_ok && order_ok;
    std::cout << name
              << " status=" << solved.ambiguity_status
              << " candidates=" << solved.velocity_candidate_count
              << " truth_n=" << obs.truth_order
              << " estimated_n=" << solved.doppler_ambiguity_order
              << " truth_v=" << truth_velocity_mps
              << " estimated_v=" << solved.v_radial
              << " cost_margin=" << solved.velocity_cost_margin
              << " confidence=" << solved.velocity_confidence
              << " ok=" << (ok ? 1 : 0) << '\n';
    return ok;
}

} // namespace

int main()
{
    Config cfg;
    cfg.fc = 16.0;
    cfg.lambda = C / 16.0e9;
    cfg.PRF = 1300.0;
    cfg.fd_res = 10.0;
    cfg.d_channel = 0.17;
    cfg.squint_side = 1;
    cfg.rx_baseline_sign = 1;
    cfg.channel_phase_sign = -1;
    cfg.motion_doppler_axis_sign = -1;
    cfg.motion_comp_enable = true;
    cfg.motion_comp_solver = "root1d";
    cfg.velocity_ambiguity_enable = true;
    cfg.velocity_max_doppler_order = 8;
    cfg.velocity_max_phase_order = 32;
    cfg.velocity_max_candidates = 512;
    cfg.velocity_beam_gate_deg = 0.25;
    cfg.velocity_phase_sigma_rad = 0.15;
    cfg.velocity_beam_sigma_deg = 0.1;
    cfg.velocity_cost_margin_min = 1.0;
    cfg.velocity_equivalent_cost_tolerance = 1.0e-8;
    cfg.MT_nowz = 0.0;

    GMTIOutput::Plane plane;
    plane.E = 0.0;
    plane.N = 0.0;
    plane.H = 6000.0;
    plane.V = 60.0;
    plane.V_angle = 0.0;

    const double theta_deg = 38.0;
    const double range_m = 87919.1348494667;
    const double v_amb = cfg.lambda * cfg.PRF / 2.0;

    bool ok = true;
    ok = checkCase("low_speed_narrow", cfg, plane, theta_deg, range_m,
                   1.0, 0.5, 1.5, "resolved", true) && ok;
    ok = checkCase("low_speed_wide", cfg, plane, theta_deg, range_m,
                   1.0, -30.0, 30.0, "unresolved", false) && ok;
    ok = checkCase("cross_one_prf", cfg, plane, theta_deg, range_m,
                   14.0, 13.5, 14.5, "resolved", true) && ok;
    Config track_prior = cfg;
    track_prior.velocity_speed_prior_mps = 14.0;
    track_prior.velocity_speed_prior_sigma_mps = 2.0;
    ok = checkCase("cross_one_prf_track_prior", track_prior, plane,
                   theta_deg, range_m, 14.0, -50.0, 50.0,
                   "resolved", true) && ok;
    ok = checkCase("exact_blind_narrow", cfg, plane, theta_deg, range_m,
                   v_amb, v_amb - 0.2, v_amb + 0.2, "resolved", true) && ok;
    ok = checkCase("exact_blind_wide", cfg, plane, theta_deg, range_m,
                   v_amb, -30.0, 30.0, "unresolved", false) && ok;

    Config legacy = cfg;
    legacy.velocity_ambiguity_enable = false;
    const double af_geometry_hz =
        -2.0 * plane.V * std::sin(theta_deg * M_PI / 180.0) / legacy.lambda;
    const double raw_phase = ctdrStaticPhaseFromAfGeo(
        legacy, plane, legacy.lambda, range_m, af_geometry_hz);
    const Observation obs = aliasObservation(legacy, af_geometry_hz, 1.0);
    const MotionCompResult legacy_result = solveMotionCompensation(
        legacy, plane, raw_phase, obs.af_alias_hz, -0.0089, 0.0,
        legacy.lambda, range_m, theta_deg);
    const bool legacy_ok = legacy_result.ambiguity_status == "disabled" &&
                           legacy_result.velocity_solver == "legacy_single_branch";
    std::cout << "legacy_default status=" << legacy_result.ambiguity_status
              << " solver=" << legacy_result.velocity_solver
              << " ok=" << (legacy_ok ? 1 : 0) << '\n';
    ok = legacy_ok && ok;

    std::cout << "velocity_ambiguity_interval_mps=" << v_amb << '\n';
    return ok ? 0 : 1;
}
