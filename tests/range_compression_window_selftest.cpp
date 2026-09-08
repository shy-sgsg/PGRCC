#include "config_structs.hpp"
#include "rangeCompress.hpp"
#include "range_compression_window.hpp"
#include "azimuth_fft_window.hpp"

#include <cmath>
#include <complex>
#include <iostream>
#include <vector>

int main()
{
    Config cfg{};
    cfg.pulse_len = 11820;
    cfg.rg_len = 4096;
    cfg.range_fft_len = 12288;
    cfg.range_crop_start = 3864;
    cfg.range_compress_len = 4096;
    cfg.pulse_num = 1;
    cfg.process_pulse_num = 1;
    cfg.hasRefFunc = 0;
    cfg.fs = 225.0e6;
    cfg.Br = 50.0e6;
    cfg.Tr = 38.0e-6;
    cfg.range_compression_window = "none";

    const auto bh = gmti::azimuth_window::BlackmanHarris;
    const double bh_gain = gmti::azimuth_window::coherentGain(bh, 130U);
    double bh_normalized_sum = 0.0;
    for (std::size_t row = 0U; row < 130U; ++row) {
        bh_normalized_sum += gmti::azimuth_window::normalizedValue(
            bh, row, 130U, true);
    }
    if (!(bh_gain > 0.30 && bh_gain < 0.40) ||
        std::fabs(bh_normalized_sum / 130.0 - 1.0) > 1.0e-12 ||
        !(gmti::azimuth_window::rawValue(bh, 0U, 130U) < 1.0e-3)) {
        std::cerr << "Blackman-Harris slow-time window is invalid" << std::endl;
        return 6;
    }

    const auto no_window = gmti::range_compression::buildSpectralWeights(cfg, cfg.range_fft_len);
    for (double value : no_window) {
        if (value != 1.0) {
            std::cerr << "none window changed legacy transfer" << std::endl;
            return 4;
        }
    }
    cfg.range_compression_window = "kaiser";
    cfg.range_compression_kaiser_beta = 4.0;
    const auto kaiser = gmti::range_compression::buildSpectralWeights(cfg, cfg.range_fft_len);
    const int passband_edge = static_cast<int>(std::floor(
        0.5 * cfg.Br / (cfg.fs / cfg.range_fft_len)));
    if (!(kaiser[0] > kaiser[static_cast<size_t>(passband_edge)]) ||
        kaiser[static_cast<size_t>(cfg.range_fft_len / 2)] != 0.0) {
        std::cerr << "Kaiser passband/taper shape is invalid" << std::endl;
        return 5;
    }

    std::vector<std::complex<double>> input(
        static_cast<size_t>(cfg.pulse_len),
        std::complex<double>(0.0, 0.0));
    input[0] = std::complex<double>(1.0, 0.0);

    std::vector<std::complex<double>> output;
    if (!rangeCompressFFT(cfg, input, output)) {
        std::cerr << "rangeCompressFFT returned false" << std::endl;
        return 1;
    }
    if (output.size() != static_cast<size_t>(cfg.range_compress_len)) {
        std::cerr << "output size mismatch: " << output.size() << std::endl;
        return 2;
    }
    for (size_t i = 0; i < output.size(); ++i) {
        if (!std::isfinite(output[i].real()) ||
            !std::isfinite(output[i].imag())) {
            std::cerr << "non-finite output at index " << i << std::endl;
            return 3;
        }
    }

    std::cout << "[SELFTEST] PASS: legacy none is unchanged; Kaiser is band-limited; "
              << "11820 -> zero-pad 12288 -> crop [3864, 7960) -> 4096 samples"
              << std::endl;
    return 0;
}
