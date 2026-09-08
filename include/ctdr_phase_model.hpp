#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>

namespace gmti {
namespace ctdr {

struct Vec2 {
    double e = std::numeric_limits<double>::quiet_NaN();
    double n = std::numeric_limits<double>::quiet_NaN();
};

struct RxCenters {
    double rx1_e = std::numeric_limits<double>::quiet_NaN();
    double rx1_n = std::numeric_limits<double>::quiet_NaN();
    double rx1_h = std::numeric_limits<double>::quiet_NaN();
    double rx2_e = std::numeric_limits<double>::quiet_NaN();
    double rx2_n = std::numeric_limits<double>::quiet_NaN();
    double rx2_h = std::numeric_limits<double>::quiet_NaN();
};

// Mechanical-scan phase-centre pose.  The local antenna frame is defined as
// x=platform-velocity direction, y=right/cross-track, z=up at servo angle 0.
// rx*_local_* are offsets from the transmit/platform reference centre before
// the azimuth servo rotation.  Keeping the two absolute offsets (instead of
// only d) also preserves a non-zero receive-array midpoint when the hardware
// geometry provides one.
struct PhaseCenterPose {
    bool valid = false;
    bool rotate_with_servo = false;
    double servo_azimuth_deg = 0.0;
    double mount_angle_deg = 0.0;
    int rotation_sign = 1;
    int baseline_sign = 1;
    std::array<double, 3> rx1_local{{0.0, 0.0, 0.0}};
    std::array<double, 3> rx2_local{{0.0, 0.0, 0.0}};
};

inline std::array<double, 3> rotateLocalOffsetAzimuth(
    const std::array<double, 3> &offset,
    double servo_azimuth_deg,
    double mount_angle_deg,
    int rotation_sign,
    bool rotate_with_servo)
{
    const double angle_deg = mount_angle_deg +
        (rotate_with_servo ?
             static_cast<double>((rotation_sign < 0) ? -1 : 1) * servo_azimuth_deg
             : 0.0);
    const double angle = angle_deg * M_PI / 180.0;
    const double c = std::cos(angle);
    const double s = std::sin(angle);
    return std::array<double, 3>{{
        c * offset[0] - s * offset[1],
        s * offset[0] + c * offset[1],
        offset[2]
    }};
}

inline bool finitePhaseCenterPose(const PhaseCenterPose &pose)
{
    for (double value : pose.rx1_local) {
        if (!std::isfinite(value)) return false;
    }
    for (double value : pose.rx2_local) {
        if (!std::isfinite(value)) return false;
    }
    return pose.valid && std::isfinite(pose.servo_azimuth_deg) &&
           std::isfinite(pose.mount_angle_deg);
}

// Return the receive-baseline components in the instantaneous platform-local
// frame after the mechanical pose is applied.  The physical norm is invariant
// under the rotation; only the along/right projections used by P38 change.
inline bool rotatedBaselineLocal(const PhaseCenterPose &pose,
                                 double &baseline_along_m,
                                 double &baseline_right_m,
                                 double &baseline_up_m)
{
    if (!finitePhaseCenterPose(pose)) return false;
    const std::array<double, 3> rx1 = rotateLocalOffsetAzimuth(
        pose.rx1_local, pose.servo_azimuth_deg, pose.mount_angle_deg,
        pose.rotation_sign, pose.rotate_with_servo);
    const std::array<double, 3> rx2 = rotateLocalOffsetAzimuth(
        pose.rx2_local, pose.servo_azimuth_deg, pose.mount_angle_deg,
        pose.rotation_sign, pose.rotate_with_servo);
    const double sign = (pose.baseline_sign < 0) ? -1.0 : 1.0;
    baseline_along_m = sign * (rx2[0] - rx1[0]);
    baseline_right_m = sign * (rx2[1] - rx1[1]);
    baseline_up_m = sign * (rx2[2] - rx1[2]);
    return std::isfinite(baseline_along_m) &&
           std::isfinite(baseline_right_m) &&
           std::isfinite(baseline_up_m);
}

// d is the physical spacing between the two receive-antenna phase centers.
// For common-transmit dual-receive, the equivalent two-way phase-centre
// separation is d/2 because the common TX phase centre is the midpoint.
inline double equivalentTwoWayPhaseCenterSeparation(double rx_baseline_m)
{
    if (!(rx_baseline_m > 0.0) || !std::isfinite(rx_baseline_m)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    return 0.5 * rx_baseline_m;
}

inline double equivalentTwoWayDelaySeconds(double rx_baseline_m,
                                           double platform_speed_mps)
{
    const double separation_m =
        equivalentTwoWayPhaseCenterSeparation(rx_baseline_m);
    if (!std::isfinite(separation_m) ||
        !(platform_speed_mps > 0.0) ||
        !std::isfinite(platform_speed_mps)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    return separation_m / platform_speed_mps;
}

// Expected slope of angle(F1 * conj(F2)) versus Doppler for stationary
// clutter in the common-transmit dual-receive model.  The relevant equivalent
// two-way separation is d/2.
inline double rawInterferometricSlopeRadPerHz(double rx_baseline_m,
                                              double platform_speed_mps,
                                              int rx_baseline_sign,
                                              int channel_phase_sign)
{
    if (!(rx_baseline_m > 0.0) ||
        !(platform_speed_mps > 0.0) ||
        !std::isfinite(rx_baseline_m) ||
        !std::isfinite(platform_speed_mps)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const int baseline_sign = (rx_baseline_sign < 0) ? -1 : 1;
    const int phase_sign = (channel_phase_sign < 0) ? -1 : 1;
    const double equivalent_spacing =
        equivalentTwoWayPhaseCenterSeparation(rx_baseline_m);
    return static_cast<double>(baseline_sign * phase_sign) *
           2.0 * M_PI * equivalent_spacing / platform_speed_mps;
}

// First-order P38 slope for a rotated receive baseline.  The old scalar slope
// is recovered when the local baseline is x=d, y=0.  For a cross-track
// component the slope is inherently look-angle dependent; the returned value
// is the local derivative at look angle A and is used only as a theory prior
// and diagnostic.  Exact phase inversion uses the path model below.
inline double rawInterferometricSlopeRadPerHz(
    const PhaseCenterPose &pose,
    double platform_speed_mps,
    double look_angle_deg,
    int look_side,
    int channel_phase_sign)
{
    if (!finitePhaseCenterPose(pose) ||
        !(platform_speed_mps > 0.0) ||
        !std::isfinite(platform_speed_mps) ||
        !std::isfinite(look_angle_deg)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    double baseline_along = 0.0;
    double baseline_right = 0.0;
    double baseline_up = 0.0;
    if (!rotatedBaselineLocal(pose, baseline_along, baseline_right, baseline_up)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    (void)baseline_up;
    const double a = look_angle_deg * M_PI / 180.0;
    const double c = std::cos(a);
    if (std::abs(c) < 1.0e-6) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double side_sign = (look_side == 1) ? -1.0 : 1.0;
    const int phase_sign = (channel_phase_sign < 0) ? -1 : 1;
    return static_cast<double>(phase_sign) * M_PI / platform_speed_mps *
        (baseline_along - side_sign * baseline_right * std::tan(a));
}

inline double equivalentTwoWayDelaySeconds(const PhaseCenterPose &pose,
                                           double platform_speed_mps)
{
    if (!finitePhaseCenterPose(pose) ||
        !(platform_speed_mps > 0.0) ||
        !std::isfinite(platform_speed_mps)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const std::array<double, 3> rx1 = rotateLocalOffsetAzimuth(
        pose.rx1_local, pose.servo_azimuth_deg, pose.mount_angle_deg,
        pose.rotation_sign, pose.rotate_with_servo);
    const std::array<double, 3> rx2 = rotateLocalOffsetAzimuth(
        pose.rx2_local, pose.servo_azimuth_deg, pose.mount_angle_deg,
        pose.rotation_sign, pose.rotate_with_servo);
    const double baseline_along = std::abs(rx2[0] - rx1[0]);
    return 0.5 * baseline_along / platform_speed_mps;
}

// Common-transmit CSI preprocessing: round((d/(2V))*PRF).  P38 is still fitted
// once before this shift for localization and once after it for residual
// phase-slope calibration.
inline int stationaryClutterAlignmentPrt(double rx_baseline_m,
                                         double platform_speed_mps,
                                         double prf_hz,
                                         int rx_baseline_sign,
                                         int channel_phase_sign)
{
    if (!(rx_baseline_m > 0.0) || !(platform_speed_mps > 0.0) ||
        !(prf_hz > 0.0) ||
        !std::isfinite(rx_baseline_m) ||
        !std::isfinite(platform_speed_mps) || !std::isfinite(prf_hz)) {
        return 0;
    }
    (void)channel_phase_sign;
    const double sign = (rx_baseline_sign < 0) ? -1.0 : 1.0;
    const double equivalent_spacing =
        equivalentTwoWayPhaseCenterSeparation(rx_baseline_m);
    const double shift = sign * equivalent_spacing * prf_hz /
                         platform_speed_mps;
    const double int_min = static_cast<double>(std::numeric_limits<int>::min());
    const double int_max = static_cast<double>(std::numeric_limits<int>::max());
    if (shift <= int_min) return std::numeric_limits<int>::min();
    if (shift >= int_max) return std::numeric_limits<int>::max();
    return static_cast<int>(std::lround(shift));
}

inline double residualSlopeAfterPrtAlignment(double raw_slope_rad_per_hz,
                                             double prf_hz,
                                             int aligned_prt_shift)
{
    if (!std::isfinite(raw_slope_rad_per_hz) ||
        !(prf_hz > 0.0) ||
        !std::isfinite(prf_hz)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    return raw_slope_rad_per_hz + 2.0 * M_PI *
        static_cast<double>(aligned_prt_shift) / prf_hz;
}

inline double wrapPi(double phase)
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

inline Vec2 alongFromVelocity(double vE, double vN)
{
    Vec2 out;
    const double vn = std::hypot(vE, vN);
    if (vn > 1.0e-12) {
        out.e = vE / vn;
        out.n = vN / vn;
    }
    return out;
}

inline RxCenters receiveCenters(double platform_e,
                                double platform_n,
                                double platform_h,
                                double vE,
                                double vN,
                                double rx_baseline_m,
                                int rx_baseline_sign)
{
    RxCenters out;
    const Vec2 along = alongFromVelocity(vE, vN);
    if (!std::isfinite(along.e) || !(rx_baseline_m > 0.0)) {
        return out;
    }
    const double s = (rx_baseline_sign < 0) ? -1.0 : 1.0;
    const double half = 0.5 * rx_baseline_m * s;
    out.rx1_e = platform_e - half * along.e;
    out.rx1_n = platform_n - half * along.n;
    out.rx1_h = platform_h;
    out.rx2_e = platform_e + half * along.e;
    out.rx2_n = platform_n + half * along.n;
    out.rx2_h = platform_h;
    return out;
}

inline RxCenters receiveCenters(double platform_e,
                                double platform_n,
                                double platform_h,
                                double vE,
                                double vN,
                                const PhaseCenterPose &pose)
{
    RxCenters out;
    if (!finitePhaseCenterPose(pose)) return out;
    const Vec2 along = alongFromVelocity(vE, vN);
    if (!std::isfinite(along.e)) return out;
    Vec2 right;
    right.e = along.n;
    right.n = -along.e;
    const std::array<double, 3> rx1_local = rotateLocalOffsetAzimuth(
        pose.rx1_local, pose.servo_azimuth_deg, pose.mount_angle_deg,
        pose.rotation_sign, pose.rotate_with_servo);
    const std::array<double, 3> rx2_local = rotateLocalOffsetAzimuth(
        pose.rx2_local, pose.servo_azimuth_deg, pose.mount_angle_deg,
        pose.rotation_sign, pose.rotate_with_servo);
    const double rx1_e = along.e * rx1_local[0] + right.e * rx1_local[1];
    const double rx1_n = along.n * rx1_local[0] + right.n * rx1_local[1];
    const double rx2_e = along.e * rx2_local[0] + right.e * rx2_local[1];
    const double rx2_n = along.n * rx2_local[0] + right.n * rx2_local[1];
    const double midpoint_e = 0.5 * (rx1_e + rx2_e);
    const double midpoint_n = 0.5 * (rx1_n + rx2_n);
    const double sign = (pose.baseline_sign < 0) ? -1.0 : 1.0;
    const double half_e = 0.5 * sign * (rx2_e - rx1_e);
    const double half_n = 0.5 * sign * (rx2_n - rx1_n);
    const double half_h = 0.5 * sign * (rx2_local[2] - rx1_local[2]);
    out.rx1_e = platform_e + midpoint_e - half_e;
    out.rx1_n = platform_n + midpoint_n - half_n;
    out.rx1_h = platform_h + 0.5 * (rx1_local[2] + rx2_local[2]) - half_h;
    out.rx2_e = platform_e + midpoint_e + half_e;
    out.rx2_n = platform_n + midpoint_n + half_n;
    out.rx2_h = platform_h + 0.5 * (rx1_local[2] + rx2_local[2]) + half_h;
    return out;
}

inline double distance3(double e1, double n1, double h1,
                        double e2, double n2, double h2)
{
    return std::sqrt((e1 - e2) * (e1 - e2) +
                     (n1 - n2) * (n1 - n2) +
                     (h1 - h2) * (h1 - h2));
}

// Return |R*u-p|-R without subtracting two nearly equal long ranges.  The
// receiver offsets are only centimetres while R is tens of kilometres, so
// this form is also safe when a float implementation is used by a device
// backend.  The current GMTI geometry model remains host-side double; this
// helper keeps the small path increment numerically well conditioned.
inline double stablePathOffsetFromCenter(double reference_range_m,
                                         double los_e,
                                         double los_n,
                                         double los_h,
                                         double offset_e,
                                         double offset_n,
                                         double offset_h)
{
    if (!(reference_range_m > 0.0) ||
        !std::isfinite(reference_range_m) ||
        !std::isfinite(los_e) || !std::isfinite(los_n) ||
        !std::isfinite(los_h) || !std::isfinite(offset_e) ||
        !std::isfinite(offset_n) || !std::isfinite(offset_h)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double inv_r = 1.0 / reference_range_m;
    const double dot = los_e * offset_e + los_n * offset_n + los_h * offset_h;
    const double offset_sq = offset_e * offset_e +
                             offset_n * offset_n +
                             offset_h * offset_h;
    const double normalized = 1.0 - 2.0 * dot * inv_r +
                              offset_sq * inv_r * inv_r;
    const double radial = std::sqrt(std::max(0.0, normalized));
    const double denominator = radial + 1.0;
    if (!(denominator > 0.0) || !std::isfinite(denominator)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    return (-2.0 * dot + offset_sq * inv_r) / denominator;
}

inline double staticPhaseUnwrapped(double target_e,
                                   double target_n,
                                   double target_h,
                                   double platform_e,
                                   double platform_n,
                                   double platform_h,
                                   double vE,
                                   double vN,
                                   double rx_baseline_m,
                                   double lambda_m,
                                   int rx_baseline_sign,
                                   int channel_phase_sign,
                                   double phi0_rad = 0.0)
{
    if (!(lambda_m > 0.0)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const RxCenters rx = receiveCenters(platform_e, platform_n, platform_h,
                                        vE, vN, rx_baseline_m, rx_baseline_sign);
    if (!std::isfinite(rx.rx1_e) || !std::isfinite(target_e) || !std::isfinite(target_n)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double dx = target_e - platform_e;
    const double dy = target_n - platform_n;
    const double dz = target_h - platform_h;
    const double reference_range = std::sqrt(dx * dx + dy * dy + dz * dz);
    if (!(reference_range > 0.0) || !std::isfinite(reference_range)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double inv_r = 1.0 / reference_range;
    const double los_e = dx * inv_r;
    const double los_n = dy * inv_r;
    const double los_h = dz * inv_r;
    const double d1 = stablePathOffsetFromCenter(
        reference_range, los_e, los_n, los_h,
        rx.rx1_e - platform_e, rx.rx1_n - platform_n, rx.rx1_h - platform_h);
    const double d2 = stablePathOffsetFromCenter(
        reference_range, los_e, los_n, los_h,
        rx.rx2_e - platform_e, rx.rx2_n - platform_n, rx.rx2_h - platform_h);
    if (!std::isfinite(d1) || !std::isfinite(d2)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double s = (channel_phase_sign < 0) ? -1.0 : 1.0;
    return s * 2.0 * M_PI * (d1 - d2) / lambda_m + phi0_rad;
}

inline double staticPhaseUnwrapped(double target_e,
                                   double target_n,
                                   double target_h,
                                   double platform_e,
                                   double platform_n,
                                   double platform_h,
                                   double vE,
                                   double vN,
                                   const PhaseCenterPose &pose,
                                   double lambda_m,
                                   int channel_phase_sign,
                                   double phi0_rad = 0.0)
{
    if (!(lambda_m > 0.0)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const RxCenters rx = receiveCenters(platform_e, platform_n, platform_h,
                                        vE, vN, pose);
    if (!std::isfinite(rx.rx1_e) || !std::isfinite(target_e) || !std::isfinite(target_n)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double dx = target_e - platform_e;
    const double dy = target_n - platform_n;
    const double dz = target_h - platform_h;
    const double reference_range = std::sqrt(dx * dx + dy * dy + dz * dz);
    if (!(reference_range > 0.0) || !std::isfinite(reference_range)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double inv_r = 1.0 / reference_range;
    const double los_e = dx * inv_r;
    const double los_n = dy * inv_r;
    const double los_h = dz * inv_r;
    const double d1 = stablePathOffsetFromCenter(
        reference_range, los_e, los_n, los_h,
        rx.rx1_e - platform_e, rx.rx1_n - platform_n, rx.rx1_h - platform_h);
    const double d2 = stablePathOffsetFromCenter(
        reference_range, los_e, los_n, los_h,
        rx.rx2_e - platform_e, rx.rx2_n - platform_n, rx.rx2_h - platform_h);
    if (!std::isfinite(d1) || !std::isfinite(d2)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double s = (channel_phase_sign < 0) ? -1.0 : 1.0;
    return s * 2.0 * M_PI * (d1 - d2) / lambda_m + phi0_rad;
}

inline double staticPhase(double target_e,
                          double target_n,
                          double target_h,
                          double platform_e,
                          double platform_n,
                          double platform_h,
                          double vE,
                          double vN,
                          double rx_baseline_m,
                          double lambda_m,
                          int rx_baseline_sign,
                          int channel_phase_sign,
                          double phi0_rad = 0.0)
{
    return wrapPi(staticPhaseUnwrapped(target_e, target_n, target_h,
                                       platform_e, platform_n, platform_h,
                                       vE, vN, rx_baseline_m, lambda_m,
                                       rx_baseline_sign, channel_phase_sign,
                                       phi0_rad));
}

inline double staticPhase(double target_e,
                          double target_n,
                          double target_h,
                          double platform_e,
                          double platform_n,
                          double platform_h,
                          double vE,
                          double vN,
                          const PhaseCenterPose &pose,
                          double lambda_m,
                          int channel_phase_sign,
                          double phi0_rad = 0.0)
{
    return wrapPi(staticPhaseUnwrapped(target_e, target_n, target_h,
                                       platform_e, platform_n, platform_h,
                                       vE, vN, pose, lambda_m,
                                       channel_phase_sign, phi0_rad));
}

inline double channelPathLength(double target_e,
                                double target_n,
                                double target_h,
                                double platform_e,
                                double platform_n,
                                double platform_h,
                                double vE,
                                double vN,
                                double rx_baseline_m,
                                int rx_baseline_sign,
                                int channel_index,
                                int read_channel_1,
                                int read_channel_2)
{
    const RxCenters rx = receiveCenters(platform_e, platform_n, platform_h,
                                        vE, vN, rx_baseline_m, rx_baseline_sign);
    if (!std::isfinite(rx.rx1_e)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double tx = distance3(target_e, target_n, target_h,
                                platform_e, platform_n, platform_h);
    if (channel_index == read_channel_2) {
        return tx + distance3(target_e, target_n, target_h, rx.rx2_e, rx.rx2_n, rx.rx2_h);
    }
    if (channel_index == read_channel_1) {
        return tx + distance3(target_e, target_n, target_h, rx.rx1_e, rx.rx1_n, rx.rx1_h);
    }
    return tx + distance3(target_e, target_n, target_h,
                          platform_e, platform_n, platform_h);
}

inline double channelPathLength(double target_e,
                                double target_n,
                                double target_h,
                                double platform_e,
                                double platform_n,
                                double platform_h,
                                double vE,
                                double vN,
                                const PhaseCenterPose &pose,
                                int channel_index,
                                int read_channel_1,
                                int read_channel_2)
{
    const RxCenters rx = receiveCenters(platform_e, platform_n, platform_h,
                                        vE, vN, pose);
    if (!std::isfinite(rx.rx1_e)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double tx = distance3(target_e, target_n, target_h,
                                platform_e, platform_n, platform_h);
    if (channel_index == read_channel_2) {
        return tx + distance3(target_e, target_n, target_h,
                              rx.rx2_e, rx.rx2_n, rx.rx2_h);
    }
    if (channel_index == read_channel_1) {
        return tx + distance3(target_e, target_n, target_h,
                              rx.rx1_e, rx.rx1_n, rx.rx1_h);
    }
    return tx + distance3(target_e, target_n, target_h,
                          platform_e, platform_n, platform_h);
}

inline double groundRangeFromSlant(double slant_range_m,
                                   double platform_h,
                                   double target_h)
{
    if (!std::isfinite(slant_range_m)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double dz = platform_h - target_h;
    return std::sqrt(std::max(0.0, slant_range_m * slant_range_m - dz * dz));
}

} // namespace ctdr
} // namespace gmti
