#ifndef GMTI_GO_CFAR_DEVICE_CUH
#define GMTI_GO_CFAR_DEVICE_CUH

#include <cuda_runtime.h>
#include <cuComplex.h>

#include <cmath>
#include <cstddef>
#include <cstdint>

namespace gmti {
namespace cfar {
namespace device {

struct BoundedSum {
    double value;
    double error_bound;
};

__global__ inline void row_prefix_kernel(
    const float *power, double *row_prefix, int height, int width)
{
    const int row = blockIdx.x * blockDim.x + threadIdx.x;
    if (row >= height) return;

    double sum = 0.0;
    const std::size_t base = static_cast<std::size_t>(row) * width;
    for (int col = 0; col < width; ++col) {
        sum += static_cast<double>(power[base + static_cast<std::size_t>(col)]);
        row_prefix[base + static_cast<std::size_t>(col)] = sum;
    }
}

__global__ inline void col_prefix_to_integral_kernel(
    const double *row_prefix, double *integral, int height, int width)
{
    const int col = blockIdx.x * blockDim.x + threadIdx.x;
    if (col >= width) return;

    double sum = 0.0;
    for (int row = 0; row < height; ++row) {
        sum += row_prefix[static_cast<std::size_t>(row) * width + col];
        const std::size_t out = static_cast<std::size_t>(row + 1) * (width + 1) + col + 1;
        integral[out] = sum;
    }
}

__device__ inline BoundedSum rect_sum_with_bound_device(
    const double *integral, int width, int height,
    int row0, int col0, int row1, int col1)
{
    const std::size_t stride = static_cast<std::size_t>(width + 1);
    const double p00 = integral[static_cast<std::size_t>(row1 + 1) * stride + col1 + 1];
    const double p10 = integral[static_cast<std::size_t>(row0) * stride + col1 + 1];
    const double p01 = integral[static_cast<std::size_t>(row1 + 1) * stride + col0];
    const double p11 = integral[static_cast<std::size_t>(row0) * stride + col0];
    const double value = p00 - p10 - p01 + p11;

    // Bound the two scans that formed each prefix and the three operations in
    // inclusion/exclusion. The input is nonnegative, so corner magnitudes are
    // a conservative scale for accumulated roundoff.
    const double eps = 2.220446049250313080847263336181640625e-16;
    const double scan_ops = static_cast<double>(height + width);
    const double scan_gamma = (scan_ops * eps) / (1.0 - scan_ops * eps);
    const double rect_gamma = (3.0 * eps) / (1.0 - 3.0 * eps);
    const double magnitude = fabs(p00) + fabs(p10) + fabs(p01) + fabs(p11);
    BoundedSum result = {value, (scan_gamma + rect_gamma) * magnitude};
    return result;
}

__device__ inline BoundedSum rect_sum_device(
    const double *integral, int width, int height,
    int row0, int col0, int row1, int col1)
{
    return rect_sum_with_bound_device(integral, width, height,
                                      row0, col0, row1, col1);
}

__device__ inline BoundedSum doppler_rect_sum_device(
    const double *integral, int height, int width,
    int row0, int col0, int row1, int col1, bool circular)
{
    if (!circular) {
        return rect_sum_device(integral, width, height, row0, col0, row1, col1);
    }
    while (row0 < 0) {
        row0 += height;
        row1 += height;
    }
    while (row0 >= height) {
        row0 -= height;
        row1 -= height;
    }
    if (row1 < height) {
        return rect_sum_device(integral, width, height, row0, col0, row1, col1);
    }
    const BoundedSum first = rect_sum_device(
        integral, width, height, row0, col0, height - 1, col1);
    const BoundedSum second = rect_sum_device(
        integral, width, height, 0, col0, row1 - height, col1);
    const double eps = 2.220446049250313080847263336181640625e-16;
    BoundedSum result;
    result.value = first.value + second.value;
    result.error_bound = first.error_bound + second.error_bound +
                         eps * (fabs(first.value) + fabs(second.value));
    return result;
}

__device__ inline double direct_rect_sum_device(
    const float *power, int height, int width,
    int row0, int col0, int row1, int col1, bool circular)
{
    double sum = 0.0;
    for (int row = row0; row <= row1; ++row) {
        int mapped_row = row;
        if (circular) {
            mapped_row %= height;
            if (mapped_row < 0) mapped_row += height;
        }
        const std::size_t base = static_cast<std::size_t>(mapped_row) * width;
        for (int col = col0; col <= col1; ++col) {
            sum += static_cast<double>(power[base + static_cast<std::size_t>(col)]);
        }
    }
    return sum;
}

__device__ inline bool needs_direct_fallback(
    double mean, double sum_error_bound, int sample_count, double alpha)
{
    const double eps = 2.220446049250313080847263336181640625e-16;
    const double mean_error = sum_error_bound / static_cast<double>(sample_count);
    const double noise_tolerance = fmax(1.0e-12, 1.0e-8 * fabs(mean));
    const double threshold = alpha * mean;
    const float threshold_f32 = static_cast<float>(threshold);
    const float abs_threshold = fabsf(threshold_f32);
    const float next = nextafterf(abs_threshold, INFINITY);
    const double half_ulp = 0.5 * fabs(static_cast<double>(next) -
                                      static_cast<double>(abs_threshold));
    const double threshold_tolerance = fmin(
        half_ulp, fmax(1.0e-12, 1.0e-8 * fabs(threshold)));
    (void)eps;
    return mean_error > noise_tolerance || alpha * mean_error > threshold_tolerance;
}

__device__ inline double go_direction_mean_device(
    const float *power, const double *integral,
    int height, int width, int row, int col,
    int guard, int background, bool circular,
    double alpha, unsigned int *fallback_count,
    int direction)
{
    const int radius = guard + background;
    int row0, row1, col0, col1;
    if (direction == 0 || direction == 1) {
        row0 = row - radius;
        row1 = row + radius;
        col0 = direction == 0 ? col - radius : col + guard + 1;
        col1 = direction == 0 ? col - guard - 1 : col + radius;
    } else {
        row0 = direction == 2 ? row - radius : row + guard + 1;
        row1 = direction == 2 ? row - guard - 1 : row + radius;
        col0 = col - radius;
        col1 = col + radius;
    }
    const int count = background * (2 * radius + 1);
    BoundedSum bounded = doppler_rect_sum_device(
        integral, height, width, row0, col0, row1, col1, circular);
    double mean = bounded.value / static_cast<double>(count);
    if (needs_direct_fallback(mean, bounded.error_bound, count, alpha)) {
        const double direct = direct_rect_sum_device(
            power, height, width, row0, col0, row1, col1, circular);
        mean = direct / static_cast<double>(count);
        if (fallback_count) ++(*fallback_count);
    }
    return mean;
}

__device__ inline double go_noise_level_device(
    const float *power, const double *integral,
    int height, int width, int row, int col,
    int guard, int background, bool circular,
    double alpha, unsigned int *fallback_count)
{
    double noise = 0.0;
    for (int direction = 0; direction < 4; ++direction) {
        const double mean = go_direction_mean_device(
            power, integral, height, width, row, col, guard, background,
            circular, alpha, fallback_count, direction);
        if (mean > noise) noise = mean;
    }
    return noise;
}

__global__ inline void cfar_detect_go_kernel(
    const cuFloatComplex *detect, const float *power, const double *integral,
    float *out, float *threshold_map, std::uint8_t *fallback_map,
    int height, int width, int guard, int background, float alpha,
    bool circular, int exclude_row_start, int exclude_row_end,
    int cut_band_start, int cut_band_end, int cut_band_mode)
{
    const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    const std::size_t total = static_cast<std::size_t>(height) * width;
    if (index >= total) return;
    if (threshold_map) threshold_map[index] = 0.0f;
    if (fallback_map) fallback_map[index] = 0U;

    const int row = static_cast<int>(index / static_cast<std::size_t>(width));
    const int col = static_cast<int>(index % static_cast<std::size_t>(width));
    const int radius = guard + background;
    if (col < radius || col >= width - radius ||
        (!circular && (row < radius || row >= height - radius))) {
        out[index] = 0.0f;
        return;
    }

    unsigned int fallbacks = 0U;
    const double noise = go_noise_level_device(
        power, integral, height, width, row, col, guard, background,
        circular, static_cast<double>(alpha), &fallbacks);
    const float threshold = static_cast<float>(static_cast<double>(alpha) * noise);
    if (threshold_map) threshold_map[index] = threshold;
    if (fallback_map) fallback_map[index] = static_cast<std::uint8_t>(fallbacks);

    const cuFloatComplex value = detect[index];
    const float real = cuCrealf(value);
    const float imag = cuCimagf(value);
    const float cut = real * real + imag * imag;
    const bool excluded = exclude_row_start >= 0 &&
                          row >= exclude_row_start && row <= exclude_row_end;
    const bool in_band = row >= cut_band_start && row <= cut_band_end;
    const bool enabled = cut_band_mode == 0 ||
                         (cut_band_mode == 1 && in_band) ||
                         (cut_band_mode == 2 && !in_band);
    out[index] = (cut > threshold && !excluded && enabled) ? cut : 0.0f;
}

} // namespace device
} // namespace cfar
} // namespace gmti

#endif
