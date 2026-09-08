#include "imu_interpolation.hpp"

#include <cmath>
#include <iostream>
#include <string>
#include <vector>

namespace {

#define CHECK_OR_RETURN(condition)                                             \
    do {                                                                       \
        if (!(condition)) {                                                    \
            std::cerr << "IMU interpolation selftest failed: " #condition    \
                      << " at line " << __LINE__ << '\n';                     \
            return 1;                                                          \
        }                                                                      \
    } while (false)

bool closeTo(double actual, double expected, double tolerance = 1.0e-12) {
    return std::fabs(actual - expected) <= tolerance;
}

}  // namespace

int main() {
    const std::vector<gmti::imu::ProjectedSample> samples = {
        {10.0, 100.0, -50.0, 3.0},
        {12.0, 108.0, -46.0, 4.0},
        {15.0, 120.0, -40.0, 5.5},
        {20.0, 140.0, -30.0, 8.0},
    };

    gmti::imu::BeamBoundaryInterpolation interpolation;
    std::string error;
    CHECK_OR_RETURN(gmti::imu::interpolateBeamBoundaries(
        samples, 11.0, 17.0, interpolation, &error));
    CHECK_OR_RETURN(closeTo(interpolation.start.east, 104.0));
    CHECK_OR_RETURN(closeTo(interpolation.start.north, -48.0));
    CHECK_OR_RETURN(closeTo(interpolation.start.up, 3.5));
    CHECK_OR_RETURN(interpolation.start.before_index == 0U);
    CHECK_OR_RETURN(interpolation.start.after_index == 1U);
    CHECK_OR_RETURN(closeTo(interpolation.end.east, 128.0));
    CHECK_OR_RETURN(closeTo(interpolation.end.north, -36.0));
    CHECK_OR_RETURN(closeTo(interpolation.end.up, 6.5));
    CHECK_OR_RETURN(interpolation.end.before_index == 2U);
    CHECK_OR_RETURN(interpolation.end.after_index == 3U);
    CHECK_OR_RETURN(closeTo(interpolation.east_velocity, 4.0));
    CHECK_OR_RETURN(closeTo(interpolation.north_velocity, 2.0));
    CHECK_OR_RETURN(closeTo(interpolation.up_velocity, 0.5));

    gmti::imu::InterpolatedPosition exact;
    CHECK_OR_RETURN(gmti::imu::interpolateAt(samples, 15.0, exact, &error));
    CHECK_OR_RETURN(exact.before_index == 2U);
    CHECK_OR_RETURN(exact.after_index == 2U);
    CHECK_OR_RETURN(closeTo(exact.east, 120.0));
    CHECK_OR_RETURN(closeTo(exact.north, -40.0));

    CHECK_OR_RETURN(!gmti::imu::interpolateAt(samples, 9.999, exact, &error));
    CHECK_OR_RETURN(error.find("outside IMU range") != std::string::npos);
    CHECK_OR_RETURN(!gmti::imu::interpolateBeamBoundaries(
        samples, 11.0, 21.0, interpolation, &error));
    CHECK_OR_RETURN(error.find("outside IMU range") != std::string::npos);

    std::cout << "imu_interpolation_selftest passed: boundary interpolation, "
                 "exact timestamp, and out-of-range rejection\n";
    return 0;
}
