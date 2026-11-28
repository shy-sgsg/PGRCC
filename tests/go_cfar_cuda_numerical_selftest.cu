#include "go_cfar_device.cuh"

#include <cuda_runtime.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <random>
#include <sstream>
#include <string>
#include <vector>

namespace {

struct Geometry {
    int height;
    int width;
    int guard;
    int background;
    bool circular;
    int exclude_start = -1;
    int exclude_end = -1;
    int cut_band_start = 0;
    int cut_band_end = -1;
    int cut_band_mode = 0;

    Geometry() = default;
    Geometry(int h, int w, int g, int b, bool is_circular,
             int excluded_start = -1, int excluded_end = -1,
             int band_start = 0, int band_end = -1, int band_mode = 0)
        : height(h), width(w), guard(g), background(b), circular(is_circular),
          exclude_start(excluded_start), exclude_end(excluded_end),
          cut_band_start(band_start), cut_band_end(band_end),
          cut_band_mode(band_mode)
    {
    }
};

template <typename T>
class DeviceBuffer {
public:
    DeviceBuffer() = default;
    DeviceBuffer(const DeviceBuffer &) = delete;
    DeviceBuffer &operator=(const DeviceBuffer &) = delete;
    ~DeviceBuffer() { if (ptr_) cudaFree(ptr_); }

    bool allocate(std::size_t count)
    {
        return cudaMalloc(reinterpret_cast<void **>(&ptr_), count * sizeof(T)) == cudaSuccess;
    }
    T *get() { return ptr_; }
    const T *get() const { return ptr_; }

private:
    T *ptr_ = nullptr;
};

std::size_t index_of(int row, int col, int width)
{
    return static_cast<std::size_t>(row) * static_cast<std::size_t>(width) +
           static_cast<std::size_t>(col);
}

bool geometry_valid(const Geometry &g, int row, int col)
{
    const int radius = g.guard + g.background;
    return col >= radius && col < g.width - radius &&
           (g.circular || (row >= radius && row < g.height - radius));
}

bool cut_enabled(const Geometry &g, int row)
{
    const bool excluded = g.exclude_start >= 0 &&
                          row >= g.exclude_start && row <= g.exclude_end;
    const bool in_band = row >= g.cut_band_start && row <= g.cut_band_end;
    const bool enabled = g.cut_band_mode == 0 ||
                         (g.cut_band_mode == 1 && in_band) ||
                         (g.cut_band_mode == 2 && !in_band);
    return !excluded && enabled;
}

double direct_direction_sum(const std::vector<float> &power,
                            const Geometry &g,
                            int row,
                            int col,
                            int direction)
{
    const int radius = g.guard + g.background;
    double sum = 0.0;
    if (direction < 2) {
        const int col_begin = direction == 0 ? col - radius : col + g.guard + 1;
        const int col_end = direction == 0 ? col - g.guard - 1 : col + radius;
        for (int r = row - radius; r <= row + radius; ++r) {
            int rr = r;
            if (g.circular) {
                rr %= g.height;
                if (rr < 0) rr += g.height;
            }
            for (int c = col_begin; c <= col_end; ++c) {
                sum += static_cast<double>(power[index_of(rr, c, g.width)]);
            }
        }
    } else {
        const int row_begin = direction == 2 ? row - radius : row + g.guard + 1;
        const int row_end = direction == 2 ? row - g.guard - 1 : row + radius;
        for (int r = row_begin; r <= row_end; ++r) {
            int rr = r;
            if (g.circular) {
                rr %= g.height;
                if (rr < 0) rr += g.height;
            }
            for (int c = col - radius; c <= col + radius; ++c) {
                sum += static_cast<double>(power[index_of(rr, c, g.width)]);
            }
        }
    }
    return sum;
}

double direct_noise(const std::vector<float> &power,
                    const Geometry &g,
                    int row,
                    int col)
{
    const int side_count = g.background * (2 * (g.guard + g.background) + 1);
    const int cap_count = side_count;
    double noise = 0.0;
    for (int direction = 0; direction < 4; ++direction) {
        const double mean = direct_direction_sum(power, g, row, col, direction) /
                            static_cast<double>(direction < 2 ? side_count : cap_count);
        noise = std::max(noise, mean);
    }
    return noise;
}

std::uint32_t positive_float_bits(float value)
{
    std::uint32_t bits = 0U;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

std::uint32_t ulp_distance(float lhs, float rhs)
{
    if (!(lhs >= 0.0f) || !(rhs >= 0.0f)) return std::numeric_limits<std::uint32_t>::max();
    const std::uint32_t a = positive_float_bits(lhs);
    const std::uint32_t b = positive_float_bits(rhs);
    return a > b ? a - b : b - a;
}

__global__ void evaluate_cuda_thresholds(
    const float *power,
    const double *integral,
    float *threshold,
    float *hits,
    std::uint8_t *fallback_directions,
    int height,
    int width,
    int guard,
    int background,
    double alpha,
    bool circular,
    int exclude_start,
    int exclude_end,
    int cut_band_start,
    int cut_band_end,
    int cut_band_mode)
{
    const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    const std::size_t total = static_cast<std::size_t>(height) * static_cast<std::size_t>(width);
    if (index >= total) return;
    threshold[index] = 0.0f;
    hits[index] = 0.0f;
    fallback_directions[index] = 0U;

    const int row = static_cast<int>(index / static_cast<std::size_t>(width));
    const int col = static_cast<int>(index % static_cast<std::size_t>(width));
    const int radius = guard + background;
    if (col < radius || col >= width - radius ||
        (!circular && (row < radius || row >= height - radius))) return;

    unsigned int fallback_count = 0U;
    const double noise = gmti::cfar::device::go_noise_level_device(
        power, integral, height, width, row, col, guard, background,
        circular, alpha, &fallback_count);
    const float threshold_f32 = static_cast<float>(alpha * noise);
    threshold[index] = threshold_f32;
    fallback_directions[index] = static_cast<std::uint8_t>(fallback_count);

    const bool excluded = exclude_start >= 0 && row >= exclude_start && row <= exclude_end;
    const bool in_band = row >= cut_band_start && row <= cut_band_end;
    const bool enabled = cut_band_mode == 0 ||
                         (cut_band_mode == 1 && in_band) ||
                         (cut_band_mode == 2 && !in_band);
    if (power[index] > threshold_f32 && !excluded && enabled) hits[index] = power[index];
}

bool run_case(const std::string &name,
              const std::vector<float> &power,
              const Geometry &g,
              double alpha)
{
    const std::size_t total = static_cast<std::size_t>(g.height) * g.width;
    if (power.size() != total) {
        std::cerr << "[CFAR-SELFTEST][FAIL] " << name << ": input size mismatch\n";
        return false;
    }

    DeviceBuffer<float> d_power, d_threshold, d_hits;
    DeviceBuffer<double> d_row_prefix, d_integral;
    DeviceBuffer<std::uint8_t> d_fallbacks;
    const std::size_t integral_count = static_cast<std::size_t>(g.height + 1) * (g.width + 1);
    if (!d_power.allocate(total) || !d_row_prefix.allocate(total) ||
        !d_integral.allocate(integral_count) || !d_threshold.allocate(total) ||
        !d_hits.allocate(total) || !d_fallbacks.allocate(total)) {
        std::cerr << "[CFAR-SELFTEST][FAIL] " << name << ": CUDA allocation failed\n";
        return false;
    }
    if (cudaMemcpy(d_power.get(), power.data(), total * sizeof(float), cudaMemcpyHostToDevice) != cudaSuccess ||
        cudaMemset(d_integral.get(), 0, integral_count * sizeof(double)) != cudaSuccess) {
        std::cerr << "[CFAR-SELFTEST][FAIL] " << name << ": input upload failed\n";
        return false;
    }

    constexpr int threads = 256;
    const int row_blocks = (g.height + threads - 1) / threads;
    const int col_blocks = (g.width + threads - 1) / threads;
    const int map_blocks = static_cast<int>((total + threads - 1) / threads);
    gmti::cfar::device::row_prefix_kernel<<<row_blocks, threads>>>(
        d_power.get(), d_row_prefix.get(), g.height, g.width);
    if (cudaGetLastError() != cudaSuccess) {
        std::cerr << "[CFAR-SELFTEST][FAIL] " << name << ": row prefix launch failed\n";
        return false;
    }
    gmti::cfar::device::col_prefix_to_integral_kernel<<<col_blocks, threads>>>(
        d_row_prefix.get(), d_integral.get(), g.height, g.width);
    if (cudaGetLastError() != cudaSuccess) {
        std::cerr << "[CFAR-SELFTEST][FAIL] " << name << ": integral launch failed\n";
        return false;
    }
    evaluate_cuda_thresholds<<<map_blocks, threads>>>(
        d_power.get(), d_integral.get(), d_threshold.get(), d_hits.get(), d_fallbacks.get(),
        g.height, g.width, g.guard, g.background, alpha, g.circular,
        g.exclude_start, g.exclude_end, g.cut_band_start, g.cut_band_end, g.cut_band_mode);
    if (cudaGetLastError() != cudaSuccess || cudaDeviceSynchronize() != cudaSuccess) {
        std::cerr << "[CFAR-SELFTEST][FAIL] " << name << ": detection kernel failed\n";
        return false;
    }

    std::vector<float> thresholds(total), hits(total);
    std::vector<std::uint8_t> fallbacks(total);
    if (cudaMemcpy(thresholds.data(), d_threshold.get(), total * sizeof(float), cudaMemcpyDeviceToHost) != cudaSuccess ||
        cudaMemcpy(hits.data(), d_hits.get(), total * sizeof(float), cudaMemcpyDeviceToHost) != cudaSuccess ||
        cudaMemcpy(fallbacks.data(), d_fallbacks.get(), total * sizeof(std::uint8_t), cudaMemcpyDeviceToHost) != cudaSuccess) {
        std::cerr << "[CFAR-SELFTEST][FAIL] " << name << ": result download failed\n";
        return false;
    }

    std::size_t valid_cuts = 0U, zero_thresholds = 0U, near_threshold_cuts = 0U;
    std::size_t hit_flips = 0U, near_threshold_hit_flips = 0U, fallback_windows = 0U;
    std::uint32_t max_ulp = 0U;
    std::string first_error;
    for (int row = 0; row < g.height; ++row) {
        for (int col = 0; col < g.width; ++col) {
            const std::size_t index = index_of(row, col, g.width);
            fallback_windows += fallbacks[index];
            if (!geometry_valid(g, row, col)) {
                if (thresholds[index] != 0.0f || hits[index] != 0.0f) {
                    first_error = "nonzero threshold/hit outside valid geometry";
                }
                continue;
            }
            ++valid_cuts;
            const double expected_noise = direct_noise(power, g, row, col);
            const float expected_threshold = static_cast<float>(alpha * expected_noise);
            const float actual_threshold = thresholds[index];
            const std::uint32_t error_ulp = ulp_distance(actual_threshold, expected_threshold);
            max_ulp = std::max(max_ulp, error_ulp);
            if (!(expected_noise >= 0.0) || !(actual_threshold >= 0.0f) || error_ulp > 2U) {
                if (first_error.empty()) {
                    std::ostringstream message;
                    message << "threshold mismatch at (" << row << ',' << col << ") expected="
                            << expected_threshold << " actual=" << actual_threshold
                            << " ulp=" << error_ulp;
                    first_error = message.str();
                }
            }
            if (expected_threshold == 0.0f) {
                ++zero_thresholds;
                if (actual_threshold != 0.0f && first_error.empty())
                    first_error = "true zero reference threshold was not preserved";
            }
            const float next = std::nextafter(std::fabs(expected_threshold),
                                              std::numeric_limits<float>::infinity());
            const double one_ulp = static_cast<double>(next - std::fabs(expected_threshold));
            const bool near = std::fabs(static_cast<double>(power[index]) - expected_threshold) <=
                              2.0 * one_ulp;
            near_threshold_cuts += near ? 1U : 0U;
            const bool expected_hit = power[index] > expected_threshold && cut_enabled(g, row);
            const bool actual_hit = hits[index] > 0.0f;
            if (near) {
                const bool hit_flip = expected_hit != actual_hit;
                std::cout << std::setprecision(std::numeric_limits<float>::max_digits10)
                          << "[CFAR-SELFTEST-NEAR] case=" << name
                          << " row=" << row << " col=" << col
                          << " cut_power_f32=" << power[index]
                          << " reference_threshold_f32=" << expected_threshold
                          << " cuda_threshold_f32=" << actual_threshold
                          << " threshold_error_ulp=" << error_ulp
                          << " reference_hit=" << static_cast<int>(expected_hit)
                          << " actual_hit=" << static_cast<int>(actual_hit)
                          << " hit_flip=" << static_cast<int>(hit_flip) << '\n';
            }
            if (expected_hit != actual_hit) {
                ++hit_flips;
                if (near) ++near_threshold_hit_flips;
                else if (first_error.empty()) {
                    std::ostringstream message;
                    message << "hit flip away from threshold at (" << row << ',' << col << ')';
                    first_error = message.str();
                }
            }
        }
    }
    const bool passed = valid_cuts > 0U && first_error.empty();
    std::cout << "[CFAR-SELFTEST] case=" << name
              << " valid_cuts=" << valid_cuts
              << " max_threshold_ulp=" << max_ulp
              << " direct_fallback_windows=" << fallback_windows
              << " true_zero_thresholds=" << zero_thresholds
              << " near_threshold_cuts=" << near_threshold_cuts
              << " near_threshold_hit_flips=" << near_threshold_hit_flips
              << " total_hit_flips=" << hit_flips
              << " status=" << (passed ? "PASS" : "FAIL") << '\n';
    if (!passed) std::cerr << "[CFAR-SELFTEST][FAIL] case=" << name << " reason=" << first_error << '\n';
    return passed;
}

std::vector<float> exponential_map(int height, int width, unsigned int seed)
{
    std::mt19937 generator(seed);
    std::exponential_distribution<double> distribution(1.0);
    std::vector<float> result(static_cast<std::size_t>(height) * width);
    for (float &value : result) value = static_cast<float>(distribution(generator));
    return result;
}

bool load_float_map(const std::string &path, std::vector<float> &out)
{
    std::ifstream input(path.c_str(), std::ios::binary);
    if (!input) return false;
    input.seekg(0, std::ios::end);
    const std::streamoff bytes = input.tellg();
    input.seekg(0, std::ios::beg);
    if (bytes < 0 || bytes % static_cast<std::streamoff>(sizeof(float)) != 0) return false;
    out.resize(static_cast<std::size_t>(bytes) / sizeof(float));
    input.read(reinterpret_cast<char *>(out.data()), bytes);
    return input.good() || input.eof();
}

bool production_alpha_from_csv(const std::string &path, double &alpha)
{
    std::ifstream input(path.c_str());
    if (!input) return false;
    std::string line;
    if (!std::getline(input, line)) return false;
    std::stringstream header_stream(line);
    std::string cell;
    int period_index = -1, alpha_index = -1, index = 0;
    while (std::getline(header_stream, cell, ',')) {
        if (cell == "period_id") period_index = index;
        if (cell == "alpha") alpha_index = index;
        ++index;
    }
    if (period_index < 0 || alpha_index < 0) return false;
    bool found = false;
    double parsed = 0.0;
    while (std::getline(input, line)) {
        std::stringstream row_stream(line);
        std::vector<std::string> values;
        while (std::getline(row_stream, cell, ',')) values.push_back(cell);
        if (values.size() <= static_cast<std::size_t>(std::max(period_index, alpha_index)) ||
            values[static_cast<std::size_t>(period_index)] != "0") continue;
        const double row_alpha = static_cast<double>(static_cast<float>(std::stod(
            values[static_cast<std::size_t>(alpha_index)])));
        if (found && row_alpha != parsed) return false;
        found = true;
        parsed = row_alpha;
    }
    if (!found) return false;
    alpha = parsed;
    return true;
}

} // namespace

int main(int argc, char **argv)
{
    if (argc != 1 && argc != 3) {
        std::cerr << "Usage: cfar_cuda_numerical_selftest [power.f32 geometry.csv]\n";
        return 2;
    }
    std::vector<float> production_power;
    double production_alpha = 0.0;
    if (argc == 3 &&
        (!load_float_map(argv[1], production_power) ||
         production_power.size() != static_cast<std::size_t>(130) * 4096 ||
         !production_alpha_from_csv(argv[2], production_alpha))) {
        std::cerr << "[CFAR-SELFTEST][FAIL] required R6 Production fixture is missing or invalid\n";
        return 2;
    }

    int device_count = 0;
    const cudaError_t count_status = cudaGetDeviceCount(&device_count);
    if (count_status != cudaSuccess || device_count == 0) {
        std::cerr << "[CFAR-SELFTEST] no CUDA device available\n";
        return 77;
    }
    cudaDeviceProp properties{};
    if (cudaGetDeviceProperties(&properties, 0) != cudaSuccess) return 1;
    std::cout << "[CFAR-SELFTEST] device=" << properties.name << '\n';

    constexpr double alpha = 13.447822570800781;
    bool passed = true;
    Geometry small{32, 64, 1, 2, true};
    auto exponential = exponential_map(small.height, small.width, 101U);
    passed = run_case("uniform_exponential", exponential, small, alpha) && passed;

    std::vector<float> high_dynamic(small.height * small.width, 1.0e-3f);
    high_dynamic[index_of(15, 31, small.width)] = 1.0e8f;
    passed = run_case("high_dynamic_range", high_dynamic, small, alpha) && passed;

    std::vector<float> weak_background(small.height * small.width, 0.025f);
    weak_background[index_of(15, 31, small.width)] = 1.0e7f;
    passed = run_case("weak_background_strong_target", weak_background, small, alpha) && passed;

    std::vector<float> sparse_target(small.height * small.width, 0.0f);
    sparse_target[index_of(15, 31, small.width)] = 1.0e5f;
    passed = run_case("sparse_target_only_and_zero_windows", sparse_target, small, alpha) && passed;

    std::vector<float> correlated(small.height * small.width);
    for (int row = 0; row < small.height; ++row) {
        for (int col = 0; col < small.width; ++col) {
            correlated[index_of(row, col, small.width)] =
                static_cast<float>(0.01 + 0.002 * row + 0.001 * col + 0.0001 * row * col);
        }
    }
    passed = run_case("nonuniform_correlated", correlated, small, alpha) && passed;

    Geometry noncircular{32, 64, 1, 2, false};
    passed = run_case("noncircular_doppler_and_range_edges", exponential, noncircular, alpha) && passed;

    Geometry filtered{32, 64, 1, 2, true, 10, 10, 8, 12, 1};
    passed = run_case("cut_exclusion_and_band", exponential, filtered, alpha) && passed;

    std::vector<float> equality(small.height * small.width, 1.0f);
    passed = run_case("strict_cut_greater_than_threshold", equality, small, 1.0) && passed;

    if (argc == 3) {
        Geometry r6{130, 4096, 4, 16, true};
        passed = run_case("r6_seed101_a0_on_dynamic_branch", production_power,
                          r6, production_alpha) && passed;
    }
    return passed ? 0 : 1;
}
