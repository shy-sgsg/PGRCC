#include "motion_comp.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <set>
#include <string>
#include <vector>

namespace {

struct Observation {
    double alias_hz = 0.0;
    int order = 0;
};

Observation makeObservation(const Config &cfg,
                            double af_geometry_hz,
                            double velocity_mps)
{
    const double sign = cfg.motion_doppler_axis_sign < 0 ? -1.0 : 1.0;
    const double motion_hz = sign * 2.0 * velocity_mps / cfg.lambda;
    Observation out;
    out.order = static_cast<int>(std::llround(motion_hz / cfg.PRF));
    out.alias_hz = af_geometry_hz + motion_hz -
                   static_cast<double>(out.order) * cfg.PRF;
    return out;
}

double positionError(const Config &cfg,
                     const GMTIOutput::Plane &plane,
                     double range_m,
                     double af_est_hz,
                     double af_truth_hz)
{
    const gmti::ctdr::Vec2 est = motionLookFromAfGeo(
        cfg, plane, cfg.lambda, af_est_hz);
    const gmti::ctdr::Vec2 truth = motionLookFromAfGeo(
        cfg, plane, cfg.lambda, af_truth_hz);
    const double ground_range = gmti::ctdr::groundRangeFromSlant(
        range_m, plane.H, cfg.MT_nowz);
    return ground_range * std::hypot(est.e - truth.e, est.n - truth.n);
}

std::vector<double> buildSpeeds(double vamb)
{
    std::vector<double> positive = {
        0.0, 1.0, 5.0, 10.0,
        0.5 * vamb, 0.9 * vamb,
        vamb - 0.5, vamb - 0.1, vamb,
        vamb + 0.1, vamb + 0.5,
        1.5 * vamb, 2.0 * vamb, 3.0 * vamb,
        20.0, 30.0, 40.0, 50.0
    };
    std::vector<double> speeds;
    for (double v : positive) {
        speeds.push_back(v);
        if (v > 0.0) speeds.push_back(-v);
    }
    std::sort(speeds.begin(), speeds.end());
    speeds.erase(std::unique(speeds.begin(), speeds.end(),
                             [](double a, double b) {
                                 return std::abs(a - b) < 1.0e-9;
                             }),
                 speeds.end());
    return speeds;
}

} // namespace

int main(int argc, char **argv)
{
    if (argc != 2) {
        std::cerr << "usage: velocity_ambiguity_sweep <output.csv>\n";
        return 2;
    }

    Config base;
    base.fc = 16.0;
    base.lambda = C / 16.0e9;
    base.PRF = 1300.0;
    base.fd_res = 10.0;
    base.d_channel = 0.17;
    base.squint_side = 1;
    base.rx_baseline_sign = 1;
    base.channel_phase_sign = -1;
    base.motion_doppler_axis_sign = -1;
    base.motion_comp_enable = true;
    base.motion_comp_solver = "root1d";
    base.velocity_ambiguity_enable = true;
    base.velocity_search_min_mps = -55.0;
    base.velocity_search_max_mps = 55.0;
    base.velocity_max_doppler_order = 8;
    base.velocity_max_phase_order = 32;
    base.velocity_max_candidates = 512;
    base.velocity_beam_gate_deg = 0.25;
    base.velocity_phase_sigma_rad = 0.15;
    base.velocity_beam_sigma_deg = 0.1;
    base.velocity_cost_margin_min = 1.0;
    base.velocity_equivalent_cost_tolerance = 1.0e-8;
    base.MT_nowz = 0.0;

    GMTIOutput::Plane plane;
    plane.E = 0.0;
    plane.N = 0.0;
    plane.H = 6000.0;
    plane.V = 60.0;
    plane.V_angle = 0.0;

    const double theta_deg = 38.0;
    const double range_m = 87919.1348494667;
    const double vamb = base.lambda * base.PRF / 2.0;
    const double af_geometry_truth =
        -2.0 * plane.V * std::sin(theta_deg * M_PI / 180.0) / base.lambda;
    const double phase_wrapped = ctdrStaticPhaseFromAfGeo(
        base, plane, base.lambda, range_m, af_geometry_truth);
    const double phase_unwrapped_truth = ctdrStaticPhaseUnwrappedFromAfGeo(
        base, plane, base.lambda, range_m, af_geometry_truth);
    const int phase_order_truth = static_cast<int>(std::llround(
        (phase_unwrapped_truth - wrapPiLocal(phase_wrapped)) / (2.0 * M_PI)));
    const double p38_k = gmti::ctdr::rawInterferometricSlopeRadPerHz(
        base.d_channel, plane.V,
        base.rx_baseline_sign, base.channel_phase_sign);
    const double p38_b = wrapPiLocal(phase_wrapped - p38_k * af_geometry_truth);

    std::ofstream os(argv[1]);
    if (!os) {
        std::cerr << "cannot open output: " << argv[1] << '\n';
        return 2;
    }
    os << std::setprecision(15);
    os << "mode,truth_velocity_mps,lambda_m,prf_hz,velocity_ambiguity_interval_mps,"
          "observed_doppler_hz,unwrapped_doppler_hz,doppler_ambiguity_order_truth,"
          "doppler_ambiguity_order,phase_wrapped_rad,phase_unwrapped_rad,"
          "phase_ambiguity_order_truth,phase_ambiguity_order,estimated_radial_velocity_mps,"
          "velocity_error_mps,position_error_m,velocity_candidate_count,velocity_best_cost,"
          "velocity_second_cost,velocity_cost_margin,velocity_confidence,ambiguity_status,"
          "order_correct,phase_order_correct,resolved,wrong_confident\n";

    int rows = 0;
    int wrong_confident = 0;
    for (const std::string mode : {std::string("single_observation_broad"),
                                   std::string("previous_track_prior_regression")}) {
        for (double truth_v : buildSpeeds(vamb)) {
            Config cfg = base;
            if (mode == "previous_track_prior_regression") {
                // This is a solver regression for a prior supplied by a previous
                // confirmed track.  It is not counted as a single-observation result.
                cfg.velocity_speed_prior_mps = truth_v;
                cfg.velocity_speed_prior_sigma_mps = 2.0;
            }
            const Observation obs = makeObservation(
                cfg, af_geometry_truth, truth_v);
            const MotionCompResult solved = solveMotionCompensation(
                cfg, plane, phase_wrapped, obs.alias_hz,
                p38_k, p38_b, cfg.lambda, range_m, theta_deg);
            const double velocity_error = std::abs(solved.v_radial - truth_v);
            const double position_error = positionError(
                cfg, plane, range_m, solved.af_geometry, af_geometry_truth);
            const bool order_correct =
                solved.doppler_ambiguity_order == obs.order;
            const bool phase_order_correct =
                solved.phase_ambiguity_order == phase_order_truth;
            const bool resolved = solved.ambiguity_status == "resolved";
            const bool is_wrong_confident = resolved && velocity_error > 0.5;
            wrong_confident += is_wrong_confident ? 1 : 0;
            os << mode << ',' << truth_v << ',' << cfg.lambda << ',' << cfg.PRF << ','
               << vamb << ',' << obs.alias_hz << ',' << solved.af_unwrapped_hz << ','
               << obs.order << ',' << solved.doppler_ambiguity_order << ','
               << solved.phase_wrapped_rad << ',' << solved.phase_unwrapped_rad << ','
               << phase_order_truth << ',' << solved.phase_ambiguity_order << ','
               << solved.v_radial << ',' << velocity_error << ',' << position_error << ','
               << solved.velocity_candidate_count << ',' << solved.velocity_best_cost << ','
               << solved.velocity_second_cost << ',' << solved.velocity_cost_margin << ','
               << solved.velocity_confidence << ',' << solved.ambiguity_status << ','
               << (order_correct ? 1 : 0) << ',' << (phase_order_correct ? 1 : 0) << ','
               << (resolved ? 1 : 0) << ',' << (is_wrong_confident ? 1 : 0) << '\n';
            ++rows;
        }
    }
    std::cout << "rows=" << rows
              << " wrong_confident=" << wrong_confident
              << " velocity_ambiguity_interval_mps=" << vamb
              << " output=" << argv[1] << '\n';
    return wrong_confident == 0 ? 0 : 1;
}
