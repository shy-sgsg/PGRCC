#pragma once

// GO-CFAR 使用四个方向训练带均值的最大值，而不是 CA-CFAR 的环均值。不能把
// CA 的 N*(Pfa^(-1/N)-1) 系数直接用于 GO，否则配置的 Pfa 不等于实际 IID
// 背景 Pfa。这里依据与检测核完全相同的八个独立矩形块，确定性地校准
// E[exp(-alpha * max(mean_left, mean_right, mean_top, mean_bottom))] = Pfa。

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <map>
#include <mutex>
#include <random>
#include <tuple>
#include <vector>

namespace gmti {
namespace go_cfar {

inline double calibrated_alpha(double pfa, int guard_half_width,
                               int background_thickness)
{
    const int g = guard_half_width < 0 ? 0 : guard_half_width;
    const int b = background_thickness < 1 ? 1 : background_thickness;
    // 所有调用均使用同一个 cache：一次生产进程只为一个 (Pfa,g,b) 做一次校准。
    static std::mutex cache_mutex;
    static std::map<std::tuple<int, int, double>, double> cache;
    const auto key = std::make_tuple(g, b, pfa);
    std::lock_guard<std::mutex> lock(cache_mutex);
    const auto found = cache.find(key);
    if (found != cache.end()) return found->second;

    constexpr int kSamples = 262144;
    const int center_cells = b * (2 * g + 1);
    const int corner_cells = b * b;
    const double strip_cells = static_cast<double>(b) * (2.0 * (g + b) + 1.0);
    // 对参数的稳定哈希，避免调参时重用同一伪随机流；不依赖运行时熵，故运行可复现。
    std::uint64_t seed = 0x9e3779b97f4a7c15ULL;
    seed ^= static_cast<std::uint64_t>(static_cast<unsigned int>(g)) +
            0x9e3779b97f4a7c15ULL + (seed << 6U) + (seed >> 2U);
    seed ^= static_cast<std::uint64_t>(static_cast<unsigned int>(b)) +
            0x9e3779b97f4a7c15ULL + (seed << 6U) + (seed >> 2U);
    const auto pfa_bits = static_cast<std::uint64_t>(
        std::llround(-std::log(pfa) * 1.0e12));
    seed ^= pfa_bits + 0x9e3779b97f4a7c15ULL + (seed << 6U) + (seed >> 2U);
    std::mt19937_64 engine(seed);
    std::gamma_distribution<double> corner(static_cast<double>(corner_cells), 1.0);
    std::gamma_distribution<double> center(static_cast<double>(center_cells), 1.0);
    std::vector<double> maxima;
    maxima.reserve(kSamples);
    for (int sample = 0; sample < kSamples; ++sample) {
        const double tl = corner(engine), tc = center(engine), tr = corner(engine);
        const double lc = center(engine), rc = center(engine);
        const double bl = corner(engine), bc = center(engine), br = corner(engine);
        const double left = (tl + lc + bl) / strip_cells;
        const double right = (tr + rc + br) / strip_cells;
        const double top = (tl + tc + tr) / strip_cells;
        const double bottom = (bl + bc + br) / strip_cells;
        maxima.push_back(std::max(std::max(left, right), std::max(top, bottom)));
    }
    const auto false_alarm_probability = [&maxima](double alpha) {
        long double sum = 0.0L;
        for (const double value : maxima) sum += std::exp(-alpha * value);
        return static_cast<double>(sum / static_cast<long double>(maxima.size()));
    };
    double low = 0.0;
    double high = 1.0;
    while (false_alarm_probability(high) > pfa) high *= 2.0;
    for (int iteration = 0; iteration < 48; ++iteration) {
        const double mid = 0.5 * (low + high);
        if (false_alarm_probability(mid) > pfa) low = mid;
        else high = mid;
    }
    const double alpha = 0.5 * (low + high);
    cache.emplace(key, alpha);
    return alpha;
}

}  // namespace go_cfar
}  // namespace gmti
