#ifndef GMTI_DOPPLER_CENTER_HPP
#define GMTI_DOPPLER_CENTER_HPP

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstddef>
#include <limits>
#include <vector>

namespace gmti {
namespace doppler_center {

struct RobustCenterResult {
    std::complex<double> correlation_sum{0.0, 0.0};
    double magnitude_cap = std::numeric_limits<double>::quiet_NaN();
    std::size_t valid_range_bins = 0;
    std::size_t clipped_range_bins = 0;
    bool valid = false;
};

// 对每个距离门的慢时间相邻相关先做幅度截尾，再求复数和。
// 截尾保留相位，只限制单个强目标/强散射点对全局 Doppler 中心的权重。
inline RobustCenterResult robustCorrelationSum(
    const std::vector<std::complex<double> >& per_range_correlation,
    double trim_top_fraction,
    std::size_t min_valid_range_bins) {
    RobustCenterResult out;
    if (!(trim_top_fraction >= 0.0 && trim_top_fraction < 1.0)) {
        return out;
    }

    std::vector<double> magnitudes;
    magnitudes.reserve(per_range_correlation.size());
    for (const auto& value : per_range_correlation) {
        const double magnitude = std::abs(value);
        if (std::isfinite(value.real()) && std::isfinite(value.imag()) &&
            std::isfinite(magnitude) && magnitude > 0.0) {
            magnitudes.push_back(magnitude);
        }
    }
    out.valid_range_bins = magnitudes.size();
    if (magnitudes.size() < min_valid_range_bins || magnitudes.empty()) {
        return out;
    }

    const double keep_quantile = 1.0 - trim_top_fraction;
    const std::size_t cap_index = static_cast<std::size_t>(std::floor(
        keep_quantile * static_cast<double>(magnitudes.size() - 1U)));
    std::nth_element(magnitudes.begin(), magnitudes.begin() + cap_index,
                     magnitudes.end());
    out.magnitude_cap = magnitudes[cap_index];
    if (!(std::isfinite(out.magnitude_cap) && out.magnitude_cap > 0.0)) {
        return out;
    }

    std::complex<double> sum(0.0, 0.0);
    for (const auto& value : per_range_correlation) {
        const double magnitude = std::abs(value);
        if (!(std::isfinite(value.real()) && std::isfinite(value.imag()) &&
              std::isfinite(magnitude) && magnitude > 0.0)) {
            continue;
        }
        if (magnitude > out.magnitude_cap) {
            sum += value * (out.magnitude_cap / magnitude);
            ++out.clipped_range_bins;
        } else {
            sum += value;
        }
    }
    out.correlation_sum = sum;
    out.valid = std::isfinite(sum.real()) && std::isfinite(sum.imag()) &&
                std::abs(sum) > 0.0;
    return out;
}

}  // namespace doppler_center
}  // namespace gmti

#endif  // GMTI_DOPPLER_CENTER_HPP
