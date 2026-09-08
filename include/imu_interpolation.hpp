#ifndef GMTI_IMU_INTERPOLATION_HPP
#define GMTI_IMU_INTERPOLATION_HPP

#include <cmath>
#include <cstddef>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

namespace gmti {
namespace imu {

// Positions are already expressed in the project's projected ENU frame.  The
// interpolation layer deliberately does not difference latitude/longitude.
struct ProjectedSample {
    double time;
    double east;
    double north;
    double up;
};

struct InterpolatedPosition {
    std::size_t before_index;
    std::size_t after_index;
    double before_time;
    double after_time;
    double fraction;
    double east;
    double north;
    double up;

    InterpolatedPosition()
        : before_index(0U),
          after_index(0U),
          before_time(std::numeric_limits<double>::quiet_NaN()),
          after_time(std::numeric_limits<double>::quiet_NaN()),
          fraction(std::numeric_limits<double>::quiet_NaN()),
          east(std::numeric_limits<double>::quiet_NaN()),
          north(std::numeric_limits<double>::quiet_NaN()),
          up(std::numeric_limits<double>::quiet_NaN()) {}
};

struct BeamBoundaryInterpolation {
    InterpolatedPosition start;
    InterpolatedPosition end;
    double duration;
    double east_velocity;
    double north_velocity;
    double up_velocity;

    BeamBoundaryInterpolation()
        : duration(std::numeric_limits<double>::quiet_NaN()),
          east_velocity(std::numeric_limits<double>::quiet_NaN()),
          north_velocity(std::numeric_limits<double>::quiet_NaN()),
          up_velocity(std::numeric_limits<double>::quiet_NaN()) {}
};

inline bool validSample(const ProjectedSample& sample) {
    return std::isfinite(sample.time) &&
           std::isfinite(sample.east) &&
           std::isfinite(sample.north) &&
           std::isfinite(sample.up);
}

inline bool interpolateAt(const std::vector<ProjectedSample>& samples,
                          double time,
                          InterpolatedPosition& output,
                          std::string* error = nullptr) {
    const auto fail = [&](const std::string& message) {
        if (error != nullptr) *error = message;
        return false;
    };

    if (samples.empty()) {
        return fail("no IMU samples");
    }
    if (!std::isfinite(time)) {
        return fail("beam boundary time is not finite");
    }
    for (std::size_t i = 0U; i < samples.size(); ++i) {
        if (!validSample(samples[i])) {
            std::ostringstream message;
            message << "invalid IMU sample at index " << i;
            return fail(message.str());
        }
        if (i > 0U && !(samples[i - 1U].time < samples[i].time)) {
            std::ostringstream message;
            message << "IMU timestamps are not strictly increasing at index " << i;
            return fail(message.str());
        }
    }
    if (time < samples.front().time || time > samples.back().time) {
        std::ostringstream message;
        message << "beam boundary " << time << " is outside IMU range ["
                << samples.front().time << ", " << samples.back().time << "]";
        return fail(message.str());
    }

    std::size_t after = 0U;
    while (after < samples.size() && samples[after].time < time) {
        ++after;
    }
    if (after == 0U) {
        output.before_index = 0U;
        output.after_index = 0U;
        output.before_time = samples[0U].time;
        output.after_time = samples[0U].time;
        output.fraction = 0.0;
        output.east = samples[0U].east;
        output.north = samples[0U].north;
        output.up = samples[0U].up;
        return true;
    }
    if (after == samples.size()) {
        const std::size_t last = samples.size() - 1U;
        output.before_index = last;
        output.after_index = last;
        output.before_time = samples[last].time;
        output.after_time = samples[last].time;
        output.fraction = 1.0;
        output.east = samples[last].east;
        output.north = samples[last].north;
        output.up = samples[last].up;
        return true;
    }

    const std::size_t before = after - 1U;
    const ProjectedSample& left = samples[before];
    const ProjectedSample& right = samples[after];
    if (time == right.time) {
        output.before_index = after;
        output.after_index = after;
        output.before_time = right.time;
        output.after_time = right.time;
        output.fraction = 0.0;
        output.east = right.east;
        output.north = right.north;
        output.up = right.up;
        return true;
    }

    const double denominator = right.time - left.time;
    if (!(denominator > 0.0) || !std::isfinite(denominator)) {
        return fail("invalid IMU interpolation interval");
    }
    const double fraction = (time - left.time) / denominator;
    output.before_index = before;
    output.after_index = after;
    output.before_time = left.time;
    output.after_time = right.time;
    output.fraction = fraction;
    output.east = left.east + fraction * (right.east - left.east);
    output.north = left.north + fraction * (right.north - left.north);
    output.up = left.up + fraction * (right.up - left.up);
    return std::isfinite(output.east) && std::isfinite(output.north) &&
           std::isfinite(output.up);
}

inline bool interpolateBeamBoundaries(
    const std::vector<ProjectedSample>& samples,
    double start_time,
    double end_time,
    BeamBoundaryInterpolation& output,
    std::string* error = nullptr) {
    const auto fail = [&](const std::string& message) {
        if (error != nullptr) *error = message;
        return false;
    };

    if (!(end_time > start_time) || !std::isfinite(start_time) ||
        !std::isfinite(end_time)) {
        return fail("beam interval must have finite end_time > start_time");
    }
    std::string local_error;
    if (!interpolateAt(samples, start_time, output.start, &local_error)) {
        return fail(std::string("start: ") + local_error);
    }
    if (!interpolateAt(samples, end_time, output.end, &local_error)) {
        return fail(std::string("end: ") + local_error);
    }

    output.duration = end_time - start_time;
    output.east_velocity = (output.end.east - output.start.east) / output.duration;
    output.north_velocity = (output.end.north - output.start.north) / output.duration;
    output.up_velocity = (output.end.up - output.start.up) / output.duration;
    if (!std::isfinite(output.east_velocity) ||
        !std::isfinite(output.north_velocity) ||
        !std::isfinite(output.up_velocity)) {
        return fail("interpolated beam velocity is not finite");
    }
    return true;
}

}  // namespace imu
}  // namespace gmti

#endif  // GMTI_IMU_INTERPOLATION_HPP
