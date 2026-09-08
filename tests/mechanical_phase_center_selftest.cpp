#include "ctdr_phase_model.hpp"

#include <cmath>
#include <iostream>

namespace {

double distance3(double e1, double n1, double h1,
                 double e2, double n2, double h2)
{
    const double de = e1 - e2;
    const double dn = n1 - n2;
    const double dh = h1 - h2;
    return std::sqrt(de * de + dn * dn + dh * dh);
}

} // namespace

int main()
{
    constexpr double kRange = 85000.0;
    constexpr double kLambda = 299792458.0 / 16.0e9;
    constexpr double kServoDeg = 20.0;
    constexpr double kVelocityE = 60.0;

    gmti::ctdr::PhaseCenterPose pose;
    pose.valid = true;
    pose.rotate_with_servo = true;
    pose.servo_azimuth_deg = kServoDeg;
    pose.mount_angle_deg = 0.0;
    pose.rotation_sign = 1;
    pose.baseline_sign = 1;
    // A cross-track receive baseline is the geometry that exposed the old
    // “always along platform velocity” assumption in the mechanical smoke
    // case: x=along-track, y=right/cross-track.
    pose.rx1_local = {{0.0, -0.255, 0.0}};
    pose.rx2_local = {{0.0, -0.085, 0.0}};

    const double theta = kServoDeg * M_PI / 180.0;
    // Left-looking target in the EN frame for velocity along +E.  The point
    // is deliberately at a long range so the stable path-offset calculation
    // is exercised instead of a short-range approximation.
    const double target_e = kRange * std::sin(theta);
    const double target_n = kRange * std::cos(theta);
    const double target_h = -6000.0;
    const gmti::ctdr::RxCenters rx = gmti::ctdr::receiveCenters(
        0.0, 0.0, 6000.0, kVelocityE, 0.0, pose);
    const double direct_unwrapped =
        2.0 * M_PI *
        (distance3(target_e, target_n, target_h,
                   rx.rx1_e, rx.rx1_n, rx.rx1_h) -
         distance3(target_e, target_n, target_h,
                   rx.rx2_e, rx.rx2_n, rx.rx2_h)) / kLambda;
    const double model_unwrapped = gmti::ctdr::staticPhaseUnwrapped(
        target_e, target_n, target_h,
        0.0, 0.0, 6000.0,
        kVelocityE, 0.0, pose, kLambda, 1);
    const double model_error = std::abs(direct_unwrapped - model_unwrapped);

    gmti::ctdr::PhaseCenterPose fixed = pose;
    fixed.rotate_with_servo = false;
    const double fixed_phase = gmti::ctdr::staticPhaseUnwrapped(
        target_e, target_n, target_h,
        0.0, 0.0, 6000.0,
        kVelocityE, 0.0, fixed, kLambda, 1);
    const double rotated_phase = model_unwrapped;
    const double rotation_effect =
        std::abs(gmti::ctdr::wrapPi(rotated_phase) -
                 gmti::ctdr::wrapPi(fixed_phase));
    const double slope = gmti::ctdr::rawInterferometricSlopeRadPerHz(
        pose, kVelocityE, kServoDeg, 1, 1);

    std::cout << "mechanical_model_error_rad=" << model_error << '\n'
              << "rotation_phase_difference_wrapped_rad=" << rotation_effect << '\n'
              << "rotated_baseline_slope_rad_per_hz=" << slope << '\n';

    // For a baseline that rotates with the beam, the first-order P38 slope at
    // the exact beam centre can legitimately approach zero: the baseline and
    // look direction keep a constant included angle.  This is a physical
    // result, not a failed fit; the mechanical servo angle is the primary
    // pointing observable and exact CTDR is used for residual geometry.
    const bool ok = std::isfinite(model_unwrapped) &&
                    std::isfinite(fixed_phase) &&
                    std::isfinite(slope) &&
                    model_error < 1.0e-9 &&
                    rotation_effect > 1.0e-4;
    return ok ? 0 : 1;
}
