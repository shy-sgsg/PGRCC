#include "trig_lut.hpp"

#include <cmath>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

namespace {

struct ErrorStats {
    std::size_t count = 0U;
    double sum_abs = 0.0;
    double sum_square = 0.0;
    double max_abs = 0.0;

    void add(double actual, double expected) {
        const double error = std::fabs(actual - expected);
        ++count;
        sum_abs += error;
        sum_square += error * error;
        if (error > max_abs) max_abs = error;
    }

    double mean() const {
        return count == 0U ? 0.0 : sum_abs / static_cast<double>(count);
    }

    double rms() const {
        return count == 0U
            ? 0.0
            : std::sqrt(sum_square / static_cast<double>(count));
    }
};

#define CHECK_OR_RETURN(condition)                                             \
    do {                                                                       \
        if (!(condition)) {                                                    \
            std::cerr << "trig LUT selftest failed: " #condition              \
                      << " at line " << __LINE__ << '\n';                     \
            return 1;                                                          \
        }                                                                      \
    } while (false)

void printStats(const std::string& name,
                const std::string& range,
                const ErrorStats& stats) {
    std::cout << "[trig-lut] function=" << name
              << " range=" << range
              << " count=" << stats.count
              << " max_abs=" << std::setprecision(12) << stats.max_abs
              << " mean_abs=" << stats.mean()
              << " rms_abs=" << stats.rms() << '\n';
}

}  // namespace

int main() {
    CHECK_OR_RETURN(gmti::trig_lut::initialize(false));
    CHECK_OR_RETURN(gmti::trig_lut::setMode(gmti::trig_lut::Mode::Lut, false));

    const double pi = 3.141592653589793238462643383279502884;
    const double two_pi = 2.0 * pi;
    const int normal_count = 20001;

    ErrorStats sin_normal;
    ErrorStats cos_normal;
    for (int i = 0; i < normal_count; ++i) {
        const double x = -pi + two_pi * static_cast<double>(i) /
                                   static_cast<double>(normal_count - 1);
        sin_normal.add(gmti::trig_lut::sin(x), std::sin(x));
        cos_normal.add(gmti::trig_lut::cos(x), std::cos(x));
    }

    const double boundary_angles[] = {
        -two_pi, -pi, -pi + two_pi / 16384.0, -two_pi / 16384.0,
        0.0, two_pi / 16384.0, pi - two_pi / 16384.0, pi,
        pi + two_pi / 16384.0, two_pi - two_pi / 16384.0, two_pi,
        two_pi + two_pi / 16384.0
    };
    ErrorStats sin_boundary;
    ErrorStats cos_boundary;
    for (double x : boundary_angles) {
        sin_boundary.add(gmti::trig_lut::sin(x), std::sin(x));
        cos_boundary.add(gmti::trig_lut::cos(x), std::cos(x));
    }

    ErrorStats asin_normal;
    ErrorStats acos_normal;
    ErrorStats atan_normal;
    for (int i = 0; i < normal_count; ++i) {
        const double x = -1.0 + 2.0 * static_cast<double>(i) /
                                  static_cast<double>(normal_count - 1);
        asin_normal.add(gmti::trig_lut::asin(x), std::asin(x));
        acos_normal.add(gmti::trig_lut::acos(x), std::acos(x));
        atan_normal.add(gmti::trig_lut::atan(10.0 * x), std::atan(10.0 * x));
    }

    ErrorStats asin_boundary;
    ErrorStats acos_boundary;
    ErrorStats atan_boundary;
    const double unit_boundaries[] = {-1.0, -1.0 + 1.0 / 16384.0,
                                      0.0, 1.0 - 1.0 / 16384.0, 1.0};
    for (double x : unit_boundaries) {
        asin_boundary.add(gmti::trig_lut::asin(x), std::asin(x));
        acos_boundary.add(gmti::trig_lut::acos(x), std::acos(x));
        atan_boundary.add(gmti::trig_lut::atan(x), std::atan(x));
    }

    ErrorStats atan2_normal;
    ErrorStats atan2_boundary;
    for (int i = -100; i <= 100; ++i) {
        for (int j = -100; j <= 100; ++j) {
            if (i == 0 && j == 0) continue;
            const double y = static_cast<double>(i) / 10.0;
            const double x = static_cast<double>(j) / 10.0;
            atan2_normal.add(gmti::trig_lut::atan2(y, x), std::atan2(y, x));
        }
    }
    const double atan2_cases[][2] = {
        {0.0, 1.0}, {0.0, -1.0}, {1.0, 0.0}, {-1.0, 0.0},
        {1.0, 1.0}, {1.0, -1.0}, {-1.0, -1.0}, {-1.0, 1.0}
    };
    for (const auto& pair : atan2_cases) {
        atan2_boundary.add(gmti::trig_lut::atan2(pair[0], pair[1]),
                             std::atan2(pair[0], pair[1]));
    }

    printStats("sin", "normal[-pi,pi]", sin_normal);
    printStats("cos", "normal[-pi,pi]", cos_normal);
    printStats("sin", "boundaries", sin_boundary);
    printStats("cos", "boundaries", cos_boundary);
    printStats("asin", "normal[-1,1]", asin_normal);
    printStats("acos", "normal[-1,1]", acos_normal);
    printStats("atan", "normal[-10,10]", atan_normal);
    printStats("asin", "boundaries", asin_boundary);
    printStats("acos", "boundaries", acos_boundary);
    printStats("atan", "boundaries", atan_boundary);
    printStats("atan2", "normal grid", atan2_normal);
    printStats("atan2", "boundaries", atan2_boundary);

    CHECK_OR_RETURN(sin_normal.max_abs < 1.0e-3);
    CHECK_OR_RETURN(cos_normal.max_abs < 1.0e-3);
    CHECK_OR_RETURN(sin_boundary.max_abs < 1.0e-3);
    CHECK_OR_RETURN(cos_boundary.max_abs < 1.0e-3);
    // The inverse-sine/cosine derivative is singular at +/-1; the
    // piecewise-linear table therefore has a larger but bounded endpoint
    // error than the periodic tables.
    CHECK_OR_RETURN(asin_normal.max_abs < 2.0e-3);
    CHECK_OR_RETURN(acos_normal.max_abs < 2.0e-3);
    CHECK_OR_RETURN(asin_boundary.max_abs < 5.0e-3);
    CHECK_OR_RETURN(acos_boundary.max_abs < 5.0e-3);
    CHECK_OR_RETURN(atan_normal.max_abs < 1.0e-3);
    CHECK_OR_RETURN(atan2_normal.max_abs < 1.0e-3);
    CHECK_OR_RETURN(atan2_boundary.max_abs < 1.0e-3);
    std::cout << "trig_lut_selftest passed: linear LUT normal and boundary errors\n";
    return 0;
}
