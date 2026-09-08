#ifndef GMTI_AZIMUTH_FFT_WINDOW_HPP
#define GMTI_AZIMUTH_FFT_WINDOW_HPP

#include <cmath>
#include <cstddef>
#include <string>

namespace gmti {
namespace azimuth_window {

enum Type {
    None = 0,
    Hann = 1,
    BlackmanHarris = 2
};

inline Type parseType(const std::string& name)
{
    if (name == "hann") return Hann;
    if (name == "blackman_harris") return BlackmanHarris;
    return None;
}

inline double rawValue(Type type, std::size_t row, std::size_t row_count)
{
    if (type == None || row_count <= 1U) return 1.0;
    const double x = 2.0 * 3.141592653589793238462643383279502884 *
        static_cast<double>(row) / static_cast<double>(row_count - 1U);
    if (type == Hann) {
        return 0.5 - 0.5 * std::cos(x);
    }
    return 0.35875 - 0.48829 * std::cos(x) +
           0.14128 * std::cos(2.0 * x) - 0.01168 * std::cos(3.0 * x);
}

inline double coherentGain(Type type, std::size_t row_count)
{
    if (type == None || row_count == 0U) return 1.0;
    double sum = 0.0;
    for (std::size_t row = 0U; row < row_count; ++row) {
        sum += rawValue(type, row, row_count);
    }
    return sum / static_cast<double>(row_count);
}

inline double normalizedValue(Type type,
                              std::size_t row,
                              std::size_t row_count,
                              bool normalize)
{
    const double raw = rawValue(type, row, row_count);
    if (!normalize || type == None) return raw;
    const double gain = coherentGain(type, row_count);
    return gain > 0.0 ? raw / gain : raw;
}

}  // namespace azimuth_window
}  // namespace gmti

#endif  // GMTI_AZIMUTH_FFT_WINDOW_HPP
