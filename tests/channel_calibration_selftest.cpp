#include "channel_calibration.hpp"

#include <cmath>
#include <complex>
#include <iostream>
#include <random>
#include <vector>

namespace {

std::vector<std::complex<float> > shifted(
    const std::vector<std::complex<float> >& input,
    int rows, int cols, double shift)
{
    std::vector<std::complex<float> > output(input.size());
    for (int row = 0; row < rows; ++row) {
        const std::size_t offset = static_cast<std::size_t>(row) * cols;
        for (int col = 0; col < cols; ++col) {
            const double source = static_cast<double>(col) - shift;
            const int left = static_cast<int>(std::floor(source));
            const double fraction = source - left;
            if (left >= 0 && left + 1 < cols) {
                output[offset + col] = static_cast<float>(1.0 - fraction) *
                    input[offset + left] + static_cast<float>(fraction) *
                    input[offset + left + 1];
            }
        }
    }
    return output;
}

} // namespace

int main()
{
    const int rows = 8;
    const int cols = 512;
    std::mt19937 rng(20260714u);
    std::uniform_real_distribution<float> magnitude(0.1f, 1.0f);
    std::vector<std::complex<float> > ch1(static_cast<std::size_t>(rows) * cols);
    for (std::complex<float>& value : ch1) value = {magnitude(rng), 0.0f};
    std::vector<std::complex<float> > phase_only = ch1;
    const std::complex<float> gain = std::polar(0.8f, 0.4f);
    for (std::complex<float>& value : phase_only) value *= gain;
    const std::vector<std::complex<float> > delayed = shifted(ch1, rows, cols, 0.25);

    const std::vector<int> none;
    const auto aligned = gmti::calibration::estimateRangeProfileShift(
        ch1, phase_only, rows, cols, 0, rows - 1, 4, cols - 5,
        none, none, 0, 0, 1, 0.3);
    const auto displaced = gmti::calibration::estimateRangeProfileShift(
        ch1, delayed, rows, cols, 0, rows - 1, 4, cols - 5,
        none, none, 0, 0, 1, 0.3);
    const bool ok = aligned.valid && displaced.valid &&
        std::abs(aligned.shift_bins) < 0.01 &&
        std::abs(displaced.shift_bins) > 0.05 &&
        aligned.correlation > 0.99;
    std::cout << "[CHANNEL_CAL_SELFTEST] aligned_shift=" << aligned.shift_bins
              << " delayed_indicator=" << displaced.shift_bins
              << " aligned_corr=" << aligned.correlation
              << " delayed_corr=" << displaced.correlation
              << " status=" << (ok ? "PASS" : "FAIL") << std::endl;
    return ok ? 0 : 1;
}
