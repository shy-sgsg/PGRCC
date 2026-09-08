#pragma once

#include "config_structs.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <string>
#include <vector>

namespace gmti {
namespace range_compression {

inline double besselI0(double x)
{
    const double ax = std::abs(x);
    if (ax < 3.75) {
        const double y = x / 3.75;
        const double y2 = y * y;
        return 1.0 + y2 * (3.5156229 + y2 * (3.0899424 +
               y2 * (1.2067492 + y2 * (0.2659732 +
               y2 * (0.0360768 + y2 * 0.0045813)))));
    }
    const double y = 3.75 / ax;
    return (std::exp(ax) / std::sqrt(ax)) *
           (0.39894228 + y * (0.01328592 + y * (0.00225319 +
           y * (-0.00157565 + y * (0.00916281 + y * (-0.02057706 +
           y * (0.02635537 + y * (-0.01647633 + y * 0.00392377))))))));
}

// Builds the real spectral weighting applied on top of the existing LFM
// phase-only reference.  "none" is deliberately bit-for-bit compatible with
// the historical full-band reference.  Rectangular/Kaiser modes explicitly
// restrict the reference to the transmitted LFM bandwidth.  Normalisation
// preserves the mean in-band coherent gain, so enabling a taper does not
// silently change downstream absolute thresholds merely because of scale.
inline std::vector<double> buildSpectralWeights(const Config &cfg, int nfft)
{
    if (nfft <= 0) {
        throw std::invalid_argument("range compression nfft must be positive");
    }
    std::vector<double> weights(static_cast<std::size_t>(nfft), 1.0);
    const std::string &mode = cfg.range_compression_window;
    if (mode.empty() || mode == "none") {
        return weights;
    }
    if (mode != "rect" && mode != "rectangular" && mode != "kaiser") {
        throw std::invalid_argument("unsupported range compression window: " + mode);
    }
    if (!(cfg.fs > 0.0) || !(cfg.Br > 0.0) ||
        !(cfg.range_compression_bandwidth_scale > 0.0)) {
        throw std::invalid_argument("invalid range compression bandwidth parameters");
    }

    const double half_band = 0.5 * cfg.Br * cfg.range_compression_bandwidth_scale;
    const double df = cfg.fs / static_cast<double>(nfft);
    const double i0_beta = besselI0(cfg.range_compression_kaiser_beta);
    double nonzero_sum = 0.0;
    int nonzero_count = 0;
    for (int index = 0; index < nfft; ++index) {
        const double frequency =
            (index <= nfft / 2 - 1) ? index * df : (index - nfft) * df;
        const double normalized = frequency / half_band;
        double weight = 0.0;
        if (std::abs(normalized) <= 1.0) {
            weight = 1.0;
            if (mode == "kaiser") {
                // CoreX ivcore's CUDA std::max wrapper may lower one operand
                // to float while compiling a .cu translation unit, which
                // makes template deduction fail for max(float, double).
                // A typed scalar clamp keeps the calculation in double and
                // is numerically identical to max(0, radial_squared).
                const double radial_squared = 1.0 - normalized * normalized;
                const double radial = std::sqrt(
                    radial_squared > 0.0 ? radial_squared : 0.0);
                weight = besselI0(cfg.range_compression_kaiser_beta * radial) / i0_beta;
            }
            nonzero_sum += weight;
            ++nonzero_count;
        }
        weights[static_cast<std::size_t>(index)] = weight;
    }
    if (nonzero_count == 0 || !(nonzero_sum > 0.0)) {
        throw std::invalid_argument("range compression window has an empty passband");
    }
    if (cfg.range_compression_window_normalize) {
        const double scale = static_cast<double>(nonzero_count) / nonzero_sum;
        for (double &weight : weights) {
            weight *= scale;
        }
    }
    return weights;
}

} // namespace range_compression
} // namespace gmti
