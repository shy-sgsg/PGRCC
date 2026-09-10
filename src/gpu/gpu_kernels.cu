#include <vector>
#include <complex>
#include <algorithm>
#include <cmath>
#include <cctype>
#include <string>
#include <limits>
#include <thrust/device_vector.h>
#include <thrust/sort.h>
#include <thrust/iterator/zip_iterator.h>
#include <thrust/functional.h>
#include <thrust/count.h>
#include <thrust/copy.h>
#include <thrust/device_ptr.h>
#include <thrust/system/cuda/execution_policy.h>
#include <cuda_runtime.h>
#include <cufft.h>
#include <cuComplex.h>
#include <cstdint>
#include <iostream>
#include <chrono>
#include "GMTIProcessor.hpp"
#include "dbs/NewProtocolReader.hpp"
#include "azimuth_fft_window.hpp"
#include "doppler_center.hpp"
#include "p38_phase_fit.hpp"
#include "go_cfar_alpha.hpp"
#include "trig_lut.hpp"
#include "trig_lut_device.cuh"

extern "C" gmti::trig_lut_device::TrigLutConfig gmtiGetDeviceTrigLutConfig();

// Use cuFFT / CUDA types for device kernels
using cudacd = cuFloatComplex;

#define CUDA_CHECK(call) \
do { \
    cudaError_t err = call; \
    if (err != cudaSuccess) { \
        std::cerr << "CUDA error: " << cudaGetErrorString(err) << " at " << __FILE__ << ":" << __LINE__ << std::endl; \
        return false; \
    } \
} while (0)

#define CUFFT_CHECK(call) \
do { \
    cufftResult err = call; \
    if (err != CUFFT_SUCCESS) { \
        std::cerr << "cuFFT error: " << err << " at " << __FILE__ << ":" << __LINE__ << std::endl; \
        return false; \
    } \
} while (0)

__global__ void init_labels_kernel(const float* mydata, int* labels, int total);
__global__ void propagate_labels_kernel(const int* labels_in, int* labels_out,
                                        int H, int W, int max_gap,
                                        bool doppler_circular, int* changed);
__global__ void accumulate_stats_kernel(const float* mydata, const float* phase_map,
                                        const int* labels, int total,
                                        int* counts, float* sum_cos, float* sum_sin,
                                        float* max_power, int* max_idx,
                                        bool use_phase,
                                        gmti::trig_lut_device::TrigLutConfig trig_cfg);
__global__ void compute_mean_phase_kernel(const int* counts, const float* sum_cos,
                                          const float* sum_sin, float* mean_phi, int total,
                                          gmti::trig_lut_device::TrigLutConfig trig_cfg);
__global__ void accumulate_phase_var_kernel(const float* phase_map, const int* labels,
                                            const float* mean_phi, int total,
                                            float* sum_sq);
__global__ void compute_phase_std_kernel(const int* counts, const float* sum_sq,
                                         float* phase_std, int total);
__global__ void build_cluster_outputs_kernel(const float* mydata,
                                             const int* labels,
                                             const int* counts,
                                             const float* max_power,
                                             const int* max_idx,
                                             const float* phase_std,
                                             int total,
                                             int min_points,
                                             int baseline_min_points,
                                             float max_phase_std,
                                             bool use_phase,
                                             bool strong_filter,
                                             float strong_threshold,
                                             float* refined,
                                             int* candidate_idx,
                                             float* candidate_phase,
                                             float* candidate_power,
                                             int* filter_stats);
__global__ void compute_amp_kernel(const cuFloatComplex* F1,
                                   const int* prow,
                                   const int* pcol,
                                   float* amp,
                                   int total_points,
                                   int H,
                                   int W);
__global__ void gather_phase_samples_kernel(const float* phase_map,
                                            const int* sample_indices,
                                            float* phase_samples,
                                            int sample_count,
                                            int total);

__device__ float wrap_angle_rad_device(float x)
{
    const float PI = 3.14159265358979323846f;
    const float TWO_PI = 6.28318530717958647692f;
    while (x <= -PI) x += TWO_PI;
    while (x > PI) x -= TWO_PI;
    return x;
}

__device__ float atomic_max_float(float* addr, float value)
{
    int* addr_as_int = reinterpret_cast<int*>(addr);
    int old = *addr_as_int;
    int assumed;
    do {
        assumed = old;
        float old_val = __int_as_float(assumed);
        if (old_val >= value) break;
        old = atomicCAS(addr_as_int, assumed, __float_as_int(value));
    } while (assumed != old);
    return __int_as_float(old);
}

__device__ float atomic_add_float(float* addr, float value)
{
    return atomicAdd(addr, value);
}

struct NonNegativeAmp
{
    __host__ __device__ bool operator()(float v) const { return v >= 0.0f; }
};

struct NonNegativeIndex
{
    __host__ __device__ bool operator()(int v) const { return v >= 0; }
};

struct FiniteNonNegativePower
{
    __host__ __device__ bool operator()(float v) const
    {
        // Avoid host/device overload differences in CoreX's <cmath> wrapper.
        // NaN fails both comparisons and infinity exceeds finite float max.
        return v >= 0.0f && v <= 3.402823466e+38F;
    }
};

struct PositivePower
{
    __host__ __device__ bool operator()(float v) const { return v > 0.0f; }
};


/**
 * @brief 距离向相位补偿核函数
 * 每个线程负责一个距离门 (Column)，纵向处理所有脉冲
 */
__global__ void apply_phase_correction_kernel(
    cuFloatComplex* F2,
    const float* phi_fit, 
    int Na, int Nr,
    gmti::trig_lut_device::TrigLutConfig trig_cfg)
{
    int c = blockIdx.x * blockDim.x + threadIdx.x;
    if (c >= Nr) return;

    float angle = phi_fit[c];
    float s, cr;
    gmti::trig_lut_device::sincosf(angle, &s, &cr, trig_cfg);
    cuFloatComplex phi_factor = make_cuFloatComplex(cr, s);

    for (int r = 0; r < Na; ++r) {
        size_t idx = (size_t)r * Nr + c;
        F2[idx] = cuCmulf(F2[idx], phi_factor);
    }
}

// 计算两通道共轭相乘的纵向累加
__global__ void compute_phase_sum_kernel(
    const cuFloatComplex* F1, 
    const cuFloatComplex* F2,
    cuFloatComplex* out_sums,
    int az_st, int az_ed,
    int Na, int Nr,
    int rg_st, int rg_ed) 
{
    // 每个线程处理一个距离向索引 c
    int c = blockIdx.x * blockDim.x + threadIdx.x;
    
    // 越界检查：只处理用户指定的距离向范围 [rg_st, rg_ed]
    if (c < 0 || c >= Nr || c < rg_st || c > rg_ed) return;

    cuFloatComplex sum = make_cuFloatComplex(0.0f, 0.0f);

    // 纵向累加指定的多普勒范围 [az_st, az_ed]
    for (int r = az_st; r <= az_ed; ++r) {
        size_t idx = (size_t)r * Nr + c;
        
        cuFloatComplex val1 = F1[idx];
        cuFloatComplex val2 = F2[idx];
        
        // 计算 F1 * conj(F2)
        // cuCmulf(a, cuConjf(b))
        cuFloatComplex res = cuCmulf(val1, cuConjf(val2));
        
        sum = cuCaddf(sum, res);
    }

    // 将结果写入输出向量（长度为 Nr 的显存空间）
    out_sums[c] = sum;
}

__global__ void compute_row_statistics_kernel(
    const cuFloatComplex* F1,
    const cuFloatComplex* F2,
    float2* cross_out,
    float* energy_out,
    int Na,
    int Nr,
    int az_st,
    int rg_st,
    int az_ed,
    int rg_ed,
    const uint8_t* range_mask)
{
    int r = blockIdx.x * blockDim.x + threadIdx.x;
    if (r < az_st || r > az_ed || r >= Na) return;

    const size_t off = static_cast<size_t>(r) * static_cast<size_t>(Nr);
    float num_re = 0.0f;
    float num_im = 0.0f;
    float energy_1 = 0.0f;
    float energy_2 = 0.0f;

    for (int c = rg_st; c <= rg_ed; ++c) {
        if (range_mask != nullptr && range_mask[c] != 0U) continue;
        const cuFloatComplex a = F1[off + c];
        const cuFloatComplex b = F2[off + c];
        const float ar = cuCrealf(a);
        const float ai = cuCimagf(a);
        const float br = cuCrealf(b);
        const float bi = cuCimagf(b);
        num_re += ar * br + ai * bi;
        num_im += ai * br - ar * bi;
        energy_1 += ar * ar + ai * ai;
        energy_2 += br * br + bi * bi;
    }

    cross_out[r] = make_float2(num_re, num_im);
    energy_out[r] = sqrtf(fmaxf(0.0f, energy_1 * energy_2));
}

// v21-compatible lightweight P38 path.  It computes one coherent phase per
// Doppler row and performs a simple unwrapped linear fit on the host.  The
// enhanced robust statistics/theory/fallback path remains available behind
// p38_enhanced_enable.
__global__ void compute_row_phase_legacy_kernel(
    const cuFloatComplex* F1,
    const cuFloatComplex* F2,
    float* phase_out,
    int Na,
    int Nr,
    int az_st,
    int az_ed,
    gmti::trig_lut_device::TrigLutConfig trig_cfg)
{
    const int r = blockIdx.x * blockDim.x + threadIdx.x;
    if (r < az_st || r > az_ed || r >= Na) return;

    const std::size_t off =
        static_cast<std::size_t>(r) * static_cast<std::size_t>(Nr);
    float cross_re = 0.0f;
    float cross_im = 0.0f;
    for (int c = 0; c < Nr; ++c) {
        const cuFloatComplex a = F1[off + static_cast<std::size_t>(c)];
        const cuFloatComplex b = F2[off + static_cast<std::size_t>(c)];
        cross_re += cuCrealf(a) * cuCrealf(b) +
                    cuCimagf(a) * cuCimagf(b);
        cross_im += cuCimagf(a) * cuCrealf(b) -
                    cuCrealf(a) * cuCimagf(b);
    }
    phase_out[r] =
        gmti::trig_lut_device::atan2f(cross_im, cross_re, trig_cfg);
}

__global__ void phase_map_kernel(
    const cuFloatComplex* F1,
    const cuFloatComplex* F2,
    float* out,
    size_t total,
    gmti::trig_lut_device::TrigLutConfig trig_cfg)
{
    size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= total) return;

    const cuFloatComplex a = F1[idx];
    const cuFloatComplex b = F2[idx];
    float num_re = (cuCrealf(a) * cuCrealf(b) + cuCimagf(a) * cuCimagf(b));
    float num_im = (cuCimagf(a) * cuCrealf(b) - cuCrealf(a) * cuCimagf(b));
    out[idx] = gmti::trig_lut_device::atan2f(num_im, num_re, trig_cfg);
}

__global__ void gather_phase_samples_kernel(const float* phase_map,
                                            const int* sample_indices,
                                            float* phase_samples,
                                            int sample_count,
                                            int total)
{
    const int sample = blockIdx.x * blockDim.x + threadIdx.x;
    if (sample >= sample_count) return;
    const int index = sample_indices[sample];
    phase_samples[sample] = (index >= 0 && index < total)
        ? phase_map[index]
        : 0.0f;
}

__global__ void mix_detect_data_kernel(
    const cuFloatComplex* f2_in,
    const cuFloatComplex* csi_in,
    cuFloatComplex* out,
    int H,
    int W,
    int band_st,
    int band_ed,
    int detect_source_mode)
{
    size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    size_t total = static_cast<size_t>(H) * static_cast<size_t>(W);
    if (idx >= total) return;

    int r = static_cast<int>(idx / W);
    if (detect_source_mode == 1) {
        out[idx] = csi_in[idx];
    } else if (detect_source_mode == 2) {
        out[idx] = f2_in[idx];
    } else if (r >= band_st && r <= band_ed) {
        out[idx] = csi_in[idx];
    } else {
        out[idx] = f2_in[idx];
    }
}

__global__ void power_map_kernel(
    const cuFloatComplex* in,
    float* power,
    size_t total)
{
    size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= total) return;

    const cuFloatComplex v = in[idx];
    const float re = cuCrealf(v);
    const float im = cuCimagf(v);
    power[idx] = re * re + im * im;
}

__global__ void row_prefix_kernel(
    const float* power,
    float* row_prefix,
    int H,
    int W)
{
    int r = blockIdx.x * blockDim.x + threadIdx.x;
    if (r >= H) return;

    float sum = 0.0f;
    size_t base = static_cast<size_t>(r) * static_cast<size_t>(W);
    for (int c = 0; c < W; ++c) {
        sum += power[base + static_cast<size_t>(c)];
        row_prefix[base + static_cast<size_t>(c)] = sum;
    }
}

__global__ void col_prefix_to_integral_kernel(
    const float* row_prefix,
    float* integral,
    int H,
    int W)
{
    int c = blockIdx.x * blockDim.x + threadIdx.x;
    if (c >= W) return;

    float sum = 0.0f;
    for (int r = 0; r < H; ++r) {
        sum += row_prefix[static_cast<size_t>(r) * static_cast<size_t>(W) + static_cast<size_t>(c)];
        size_t idx = static_cast<size_t>(r + 1) * static_cast<size_t>(W + 1) + static_cast<size_t>(c + 1);
        integral[idx] = sum;
    }
}

__device__ float rect_sum_device(const float* integral, int W, int r0, int c0, int r1, int c1)
{
    size_t a = static_cast<size_t>(r1 + 1) * static_cast<size_t>(W + 1) + static_cast<size_t>(c1 + 1);
    size_t b0 = static_cast<size_t>(r0) * static_cast<size_t>(W + 1) + static_cast<size_t>(c1 + 1);
    size_t c0i = static_cast<size_t>(r1 + 1) * static_cast<size_t>(W + 1) + static_cast<size_t>(c0);
    size_t d = static_cast<size_t>(r0) * static_cast<size_t>(W + 1) + static_cast<size_t>(c0);
    return integral[a] - integral[b0] - integral[c0i] + integral[d];
}

__device__ float doppler_rect_sum_device(
    const float* integral, int H, int W,
    int r0, int c0, int r1, int c1, bool circular)
{
    if (!circular) {
        return rect_sum_device(integral, W, r0, c0, r1, c1);
    }
    // GO-CFAR evaluates directional background strips as well as the whole
    // training window.  A strip can lie entirely below row 0 or entirely
    // above row H-1, not merely straddle one boundary.  Normalize its start
    // first, then split at most once; every caller supplies a strip no taller
    // than H.
    while (r0 < 0) {
        r0 += H;
        r1 += H;
    }
    while (r0 >= H) {
        r0 -= H;
        r1 -= H;
    }
    if (r1 < H) {
        return rect_sum_device(integral, W, r0, c0, r1, c1);
    }
    return rect_sum_device(integral, W, r0, c0, H - 1, c1) +
           rect_sum_device(integral, W, 0, c0, r1 - H, c1);
}

__global__ void cfar_detect_kernel(
    const cuFloatComplex* detect,
    const float* integral,
    float* out,
    int H,
    int W,
    int g,
    int b,
    int total_bg,
    float alpha,
    float* threshold_map,
    bool use_go,
    bool doppler_circular,
    int exclude_row_start,
    int exclude_row_end,
    int cut_band_st,
    int cut_band_ed,
    int cut_band_mode)
{
    size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    size_t total = static_cast<size_t>(H) * static_cast<size_t>(W);
    if (idx >= total) return;

    if (threshold_map) threshold_map[idx] = 0.0f;

    int r = static_cast<int>(idx / W);
    int c = static_cast<int>(idx % W);

    const int R = g + b;
    const int r_min = doppler_circular ? 0 : R;
    const int r_max = doppler_circular ? H - 1 : H - 1 - R;
    const int c_min = R;
    const int c_max = W - 1 - R;

    if (r < r_min || r > r_max || c < c_min || c > c_max) {
        out[idx] = 0.0f;
        return;
    }

    const cuFloatComplex v = detect[idx];
    const float re = cuCrealf(v);
    const float im = cuCimagf(v);
    const float CUT = re * re + im * im;

    const int r0 = r - R, r1 = r + R;
    const int c0 = c - R, c1 = c + R;

    const int rg0 = r - g, rg1 = r + g;
    const int cg0 = c - g, cg1 = c + g;

    float noise_level = 0.0f;
    if (!use_go) {
        const float sum_full = doppler_rect_sum_device(
            integral, H, W, r0, c0, r1, c1, doppler_circular);
        const float sum_guard = doppler_rect_sum_device(
            integral, H, W, rg0, cg0, rg1, cg1, doppler_circular);
        const float bg_sum = sum_full - sum_guard;
        noise_level = bg_sum / static_cast<float>(total_bg);
    } else {
        float mleft = 0.0f, mright = 0.0f, mtop = 0.0f, mbot = 0.0f;

        const int lc0 = c0, lc1 = cg0 - 1;
        const int rc0 = cg1 + 1, rc1 = c1;
        const int tr0 = r0, tr1 = rg0 - 1;
        const int br0 = rg1 + 1, br1 = r1;

        if (lc0 <= lc1) {
            const float s = doppler_rect_sum_device(
                integral, H, W, r0, lc0, r1, lc1, doppler_circular);
            const int npx = (r1 - r0 + 1) * (lc1 - lc0 + 1);
            if (npx > 0) mleft = s / npx;
        }
        if (rc0 <= rc1) {
            const float s = doppler_rect_sum_device(
                integral, H, W, r0, rc0, r1, rc1, doppler_circular);
            const int npx = (r1 - r0 + 1) * (rc1 - rc0 + 1);
            if (npx > 0) mright = s / npx;
        }
        if (tr0 <= tr1) {
            const float s = doppler_rect_sum_device(
                integral, H, W, tr0, c0, tr1, c1, doppler_circular);
            const int npx = (tr1 - tr0 + 1) * (c1 - c0 + 1);
            if (npx > 0) mtop = s / npx;
        }
        if (br0 <= br1) {
            const float s = doppler_rect_sum_device(
                integral, H, W, br0, c0, br1, c1, doppler_circular);
            const int npx = (br1 - br0 + 1) * (c1 - c0 + 1);
            if (npx > 0) mbot = s / npx;
        }

        float m1 = (mleft > mright) ? mleft : mright;
        float m2 = (mtop > mbot) ? mtop : mbot;
        noise_level = (m1 > m2) ? m1 : m2;
    }

    const float thr = alpha * noise_level;
    if (threshold_map) threshold_map[idx] = thr;
    const bool excluded = exclude_row_start >= 0 &&
                          r >= exclude_row_start && r <= exclude_row_end;
    const bool in_cut_band = r >= cut_band_st && r <= cut_band_ed;
    const bool cut_enabled = cut_band_mode == 0 ||
                             (cut_band_mode == 1 && in_cut_band) ||
                             (cut_band_mode == 2 && !in_cut_band);
    if (CUT > thr && !excluded && cut_enabled) {
        out[idx] = CUT;
    } else {
        out[idx] = 0.0f;
    }
}

__global__ void init_labels_kernel(const float* mydata, int* labels, int total)
{
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= total) return;
    labels[idx] = (mydata[idx] > 0.0f) ? idx : -1;
}

__global__ void propagate_labels_kernel(const int* labels_in, int* labels_out,
                                        int H, int W, int max_gap,
                                        bool doppler_circular, int* changed)
{
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    int total = H * W;
    if (idx >= total) return;

    int label = labels_in[idx];
    if (label < 0) {
        labels_out[idx] = -1;
        return;
    }

    int r = idx / W;
    int c = idx - r * W;
    int min_label = label;

    for (int dr = -1; dr <= 1; ++dr) {
        int rr = r + dr;
        if (doppler_circular) rr = (rr % H + H) % H;
        if (rr < 0 || rr >= H) continue;
        int row_base = rr * W;
        for (int dc = -max_gap; dc <= max_gap; ++dc) {
            if (dr == 0 && dc == 0) continue;
            int cc = c + dc;
            if (cc < 0 || cc >= W) continue;
            int nidx = row_base + cc;
            int nlabel = labels_in[nidx];
            if (nlabel >= 0 && nlabel < min_label) min_label = nlabel;
        }
    }

    labels_out[idx] = min_label;
    if (min_label != label) atomicExch(changed, 1);
}

__global__ void accumulate_stats_kernel(const float* mydata, const float* phase_map,
                                        const int* labels, int total,
                                        int* counts, float* sum_cos, float* sum_sin,
                                        float* max_power, int* max_idx,
                                        bool use_phase,
                                        gmti::trig_lut_device::TrigLutConfig trig_cfg)
{
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= total) return;

    int label = labels[idx];
    if (label < 0) return;

    atomicAdd(&counts[label], 1);

    if (use_phase) {
        float phi = phase_map[idx];
        atomic_add_float(&sum_cos[label], gmti::trig_lut_device::cosf(phi, trig_cfg));
        atomic_add_float(&sum_sin[label], gmti::trig_lut_device::sinf(phi, trig_cfg));
    }

    float p = mydata[idx];
    float old = atomic_max_float(&max_power[label], p);
    if (p > old) {
        atomicExch(&max_idx[label], idx);
    }
}

__global__ void compute_mean_phase_kernel(const int* counts, const float* sum_cos,
                                          const float* sum_sin, float* mean_phi, int total,
                                          gmti::trig_lut_device::TrigLutConfig trig_cfg)
{
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= total) return;
    if (counts[idx] > 0) {
        mean_phi[idx] = gmti::trig_lut_device::atan2f(
            sum_sin[idx], sum_cos[idx], trig_cfg);
    } else {
        mean_phi[idx] = 0.0f;
    }
}

__global__ void accumulate_phase_var_kernel(const float* phase_map, const int* labels,
                                            const float* mean_phi, int total,
                                            float* sum_sq)
{
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= total) return;

    int label = labels[idx];
    if (label < 0) return;

    float dphi = phase_map[idx] - mean_phi[label];
    dphi = wrap_angle_rad_device(dphi);
    atomic_add_float(&sum_sq[label], dphi * dphi);
}

__global__ void compute_phase_std_kernel(const int* counts, const float* sum_sq,
                                         float* phase_std, int total)
{
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= total) return;
    if (counts[idx] > 1) {
        phase_std[idx] = sqrtf(sum_sq[idx] / static_cast<float>(counts[idx] - 1));
    } else {
        phase_std[idx] = 0.0f;
    }
}

__global__ void build_cluster_outputs_kernel(const float* mydata,
                                             const int* labels,
                                             const int* counts,
                                             const float* max_power,
                                             const int* max_idx,
                                             const float* phase_std,
                                             int total,
                                             int min_points,
                                             int baseline_min_points,
                                             float max_phase_std,
                                             bool use_phase,
                                             bool strong_filter,
                                             float strong_threshold,
                                             float* refined,
                                             int* candidate_idx,
                                             float* candidate_phase,
                                             float* candidate_power,
                                             int* filter_stats)
{
    const int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= total) return;

    const int label = labels[idx];
    const bool label_base_valid = label >= 0 && label < total &&
        counts[label] >= min_points &&
        (!use_phase || phase_std[label] <= max_phase_std);
    const bool label_valid = label_base_valid &&
        (!strong_filter || max_power[label] >= strong_threshold);
    refined[idx] = label_valid ? mydata[idx] : 0.0f;

    const bool root_base_valid = counts[idx] >= min_points &&
        (!use_phase || phase_std[idx] <= max_phase_std) &&
        max_idx[idx] >= 0 && max_idx[idx] < total;
    const bool root_valid = root_base_valid &&
        (!strong_filter || max_power[idx] >= strong_threshold);
    if (root_base_valid && strong_filter) {
        if (!root_valid) {
            atomicAdd(filter_stats + 2, 1);
        } else if (counts[idx] >= baseline_min_points) {
            atomicAdd(filter_stats, 1);
        } else {
            atomicAdd(filter_stats + 1, 1);
        }
    }
    candidate_idx[idx] = root_valid ? max_idx[idx] : -1;
    candidate_phase[idx] = root_valid && use_phase ? phase_std[idx] : 0.0f;
    candidate_power[idx] = root_valid ? max_power[idx] : 0.0f;
}

__global__ void compute_amp_kernel(const cuFloatComplex* F1,
                                   const int* prow,
                                   const int* pcol,
                                   float* amp,
                                   int total_points,
                                   int H,
                                   int W)
{
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= total_points) return;

    int r = prow[idx];
    int c = pcol[idx];
    if (r < 0 || c < 0 || r >= H || c >= W) {
        amp[idx] = -1.0f;
        return;
    }

    size_t off = static_cast<size_t>(r) * static_cast<size_t>(W) + static_cast<size_t>(c);
    const cuFloatComplex v = F1[off];
    float re = cuCrealf(v);
    float im = cuCimagf(v);
    amp[idx] = sqrtf(re * re + im * im);
}

__global__ void clutter_cancel_kernel(
    const cuFloatComplex* F1,
    const cuFloatComplex* F2,
    const float* y_faAxis,
    cuFloatComplex* out,
    int Na,
    int Nr,
    int az_st,
    int az_ed,
    float k,
    float b,
    int bypass_enable,
    const float* coherence,
    int coherence_gate_enable,
    float coherence_min,
    gmti::trig_lut_device::TrigLutConfig trig_cfg)
{
    size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    size_t total = static_cast<size_t>(Na) * static_cast<size_t>(Nr);
    if (idx >= total) return;

    int r = static_cast<int>(idx / Nr);
    if (r < az_st || r > az_ed) {
        out[idx] = F1[idx];
        return;
    }
    // The historical min-magnitude equalizer is nonlinear.  Applying it to
    // an incoherent row changes the H0 noise law and produces long CFAR tails.
    // Keep such rows linear (channel 1 only), while retaining the legacy
    // operation on rows that contain coherent stationary clutter.
    if (coherence_gate_enable && coherence != nullptr &&
        coherence[r] < coherence_min) {
        out[idx] = F1[idx];
        return;
    }
    const cuFloatComplex a = F1[idx];
    const cuFloatComplex f2 = F2[idx];

    float phi = k * y_faAxis[r] + b;
    float s, c;
    gmti::trig_lut_device::sincosf(phi, &s, &c, trig_cfg);
    const cuFloatComplex az_fai = make_cuFloatComplex(c, s);
    const cuFloatComplex b2 = cuCmulf(az_fai, f2);

    float aa = cuCabsf(a);
    float bb = cuCabsf(b2);
    float m = (aa < bb) ? aa : bb;

    cuFloatComplex a_eq = make_cuFloatComplex(0.0f, 0.0f);
    cuFloatComplex b_eq = make_cuFloatComplex(0.0f, 0.0f);
    if (aa > 0.0f) {
        float scale = m / aa;
        a_eq = make_cuFloatComplex(cuCrealf(a) * scale, cuCimagf(a) * scale);
    }
    if (bb > 0.0f) {
        float scale = m / bb;
        b_eq = make_cuFloatComplex(cuCrealf(b2) * scale, cuCimagf(b2) * scale);
    }

    out[idx] = bypass_enable ? a_eq : cuCsubf(a_eq, b_eq);
}

// Per-Doppler-row complex least-squares coefficient.  For each row, alpha
// minimizes sum_c |F1-alpha*F2|^2 over the configured range training support.
// A single target occupies very few of the thousands of training cells, while
// stationary distributed clutter contributes coherently.
__global__ void compute_row_complex_ls_kernel(
    const cuFloatComplex* F1,
    const cuFloatComplex* F2,
    cuFloatComplex* alpha,
    float* coherence,
    int Na,
    int Nr,
    int rg_st,
    int rg_ed)
{
    const int r = blockIdx.x * blockDim.x + threadIdx.x;
    if (r >= Na) return;
    const size_t off = static_cast<size_t>(r) * static_cast<size_t>(Nr);
    float cross_re = 0.0f;
    float cross_im = 0.0f;
    float energy_1 = 0.0f;
    float energy_2 = 0.0f;
    for (int c = rg_st; c <= rg_ed; ++c) {
        const cuFloatComplex a = F1[off + static_cast<size_t>(c)];
        const cuFloatComplex b = F2[off + static_cast<size_t>(c)];
        const float ar = cuCrealf(a);
        const float ai = cuCimagf(a);
        const float br = cuCrealf(b);
        const float bi = cuCimagf(b);
        energy_1 += ar * ar + ai * ai;
        cross_re += ar * br + ai * bi;
        cross_im += ai * br - ar * bi;
        energy_2 += br * br + bi * bi;
    }
    if (energy_2 > 0.0f && isfinite(energy_2)) {
        alpha[r] = make_cuFloatComplex(
            cross_re / energy_2,
            cross_im / energy_2);
    } else {
        alpha[r] = make_cuFloatComplex(0.0f, 0.0f);
    }
    const float cross_power = cross_re * cross_re + cross_im * cross_im;
    const float denominator = energy_1 * energy_2;
    coherence[r] = denominator > 0.0f && isfinite(denominator)
        ? fminf(1.0f, sqrtf(fmaxf(0.0f, cross_power / denominator)))
        : 0.0f;
}

__global__ void row_complex_ls_cancel_kernel(
    const cuFloatComplex* F1,
    const cuFloatComplex* F2,
    const cuFloatComplex* alpha,
    cuFloatComplex* out,
    size_t total,
    int Nr,
    int az_st,
    int az_ed,
    int bypass_enable,
    const float* coherence,
    int coherence_gate_enable,
    float coherence_min)
{
    const size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= total) return;
    const int r = static_cast<int>(idx / static_cast<size_t>(Nr));
    if (r < az_st || r > az_ed) {
        out[idx] = F1[idx];
        return;
    }
    if (coherence_gate_enable && coherence[r] < coherence_min) {
        out[idx] = F1[idx];
        return;
    }
    const cuFloatComplex predicted = cuCmulf(alpha[r], F2[idx]);
    out[idx] = bypass_enable ? F1[idx] : cuCsubf(F1[idx], predicted);
}

// Per-Doppler-row phase-only linear cancellation.  Unlike the historical
// min-magnitude path, this keeps the operation linear for every range cell:
// y = F1 - gain * exp(j*angle(alpha_row)) * F2.
__global__ void row_phase_ls_linear_cancel_kernel(
    const cuFloatComplex* F1,
    const cuFloatComplex* F2,
    const cuFloatComplex* alpha,
    cuFloatComplex* out,
    size_t total,
    int Nr,
    int az_st,
    int az_ed,
    int bypass_enable,
    float subtraction_gain,
    const float* coherence,
    int coherence_gate_enable,
    float coherence_min)
{
    const size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= total) return;
    const int r = static_cast<int>(idx / static_cast<size_t>(Nr));
    if (r < az_st || r > az_ed) {
        out[idx] = F1[idx];
        return;
    }
    if (coherence_gate_enable && coherence[r] < coherence_min) {
        out[idx] = F1[idx];
        return;
    }
    const float alpha_abs = cuCabsf(alpha[r]);
    const cuFloatComplex phase = alpha_abs > 0.0f
        ? make_cuFloatComplex(cuCrealf(alpha[r]) / alpha_abs,
                             cuCimagf(alpha[r]) / alpha_abs)
        : make_cuFloatComplex(1.0f, 0.0f);
    const cuFloatComplex predicted = cuCmulf(phase, F2[idx]);
    const cuFloatComplex scaled = make_cuFloatComplex(
        subtraction_gain * cuCrealf(predicted),
        subtraction_gain * cuCimagf(predicted));
    out[idx] = bypass_enable ? F1[idx] : cuCsubf(F1[idx], scaled);
}

__global__ void row_phase_ls_min_magnitude_cancel_kernel(
    const cuFloatComplex* F1,
    const cuFloatComplex* F2,
    const cuFloatComplex* alpha,
    cuFloatComplex* out,
    size_t total,
    int Nr,
    int az_st,
    int az_ed,
    int bypass_enable,
    float subtraction_gain,
    const float* coherence,
    int coherence_gate_enable,
    float coherence_min)
{
    const size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= total) return;
    const int r = static_cast<int>(idx / static_cast<size_t>(Nr));
    if (r < az_st || r > az_ed) {
        out[idx] = F1[idx];
        return;
    }
    if (coherence_gate_enable && coherence[r] < coherence_min) {
        out[idx] = F1[idx];
        return;
    }
    const float alpha_abs = cuCabsf(alpha[r]);
    const cuFloatComplex phase = alpha_abs > 0.0f
        ? make_cuFloatComplex(cuCrealf(alpha[r]) / alpha_abs,
                             cuCimagf(alpha[r]) / alpha_abs)
        : make_cuFloatComplex(1.0f, 0.0f);
    const cuFloatComplex a = F1[idx];
    const cuFloatComplex b = cuCmulf(phase, F2[idx]);
    const float aa = cuCabsf(a);
    const float bb = cuCabsf(b);
    const float magnitude = fminf(aa, bb);
    const cuFloatComplex a_eq = aa > 0.0f
        ? make_cuFloatComplex(cuCrealf(a) * magnitude / aa,
                             cuCimagf(a) * magnitude / aa)
        : make_cuFloatComplex(0.0f, 0.0f);
    const cuFloatComplex b_eq = bb > 0.0f
        ? make_cuFloatComplex(cuCrealf(b) * magnitude / bb,
                             cuCimagf(b) * magnitude / bb)
        : make_cuFloatComplex(0.0f, 0.0f);
    out[idx] = bypass_enable
        ? a_eq
        : cuCsubf(a_eq, make_cuFloatComplex(
              subtraction_gain * cuCrealf(b_eq),
              subtraction_gain * cuCimagf(b_eq)));
}

// CUDA kernel for channel alignment
// *** wichtig: 与 CPU 版本对齐，应用 (-1)^row 预乘因子（模拟 FFTshift）***
__global__ void align_two_channels_fast_cuda(const cudacd* d1,
                                             const cudacd* d2,
                                             cudacd* a1,
                                             cudacd* a2,
                                             int skip,
                                             size_t Na,
                                             size_t Nr,
                                             int window_type,
                                             float window_normalization)
{
    size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    size_t total = Na * Nr;
    if (idx >= total) return;

    size_t row = idx / Nr;
    size_t col = idx % Nr;
    
    // 边界检查
    if (row >= Na || col >= Nr) return;
    
    float window = 1.0f;
    if (Na > 1U && window_type != 0) {
        const float x = 6.2831853071795864769f *
            static_cast<float>(row) / static_cast<float>(Na - 1U);
        if (window_type == 1) {
            window = 0.5f - 0.5f * cosf(x);
        } else {
            window = 0.35875f - 0.48829f * cosf(x) +
                     0.14128f * cosf(2.0f * x) -
                     0.01168f * cosf(3.0f * x);
        }
    }
    // 同时应用 fftshift 预乘、慢时间窗和相干增益归一化。
    const float s = ((row & 1) ? -1.0f : 1.0f) *
                    window * window_normalization;

    // 默认填零
    cudacd res1 = make_cuFloatComplex(0.0f, 0.0f);
    cudacd res2 = make_cuFloatComplex(0.0f, 0.0f);

    if (skip > 0) {
        // skip 表示准确的 PRT 行数：d1[skip] 复制到 a1[0]。
        size_t src_row0 = (size_t)(skip);
        if (src_row0 < Na && row < (Na - src_row0)) {
            // 有效行：从 d1[src_row0 + row] 复制
            size_t src_idx = (src_row0 + row) * Nr + col;
            res1 = d1[src_idx];
        } else {
            // 无效行：填零
            res1 = make_cuFloatComplex(0.0f, 0.0f);
        }
        // d2 保持不变
        res2 = d2[idx];
    } 
    else if (skip < 0) {
        // 负值同理表示准确的 PRT 行数，作用于 d2。
        size_t src_row0 = (size_t)(-skip);
        if (src_row0 < Na && row < (Na - src_row0)) {
            // 有效行：从 d2[src_row0 + row] 复制
            size_t src_idx = (src_row0 + row) * Nr + col;
            res2 = d2[src_idx];
        } else {
            // 无效行：填零
            res2 = make_cuFloatComplex(0.0f, 0.0f);
        }
        // d1 保持不变
        res1 = d1[idx];
    }
    else {
        // skip == 0，直接复制
        res1 = d1[idx];
        res2 = d2[idx];
    }

    // 统一应用预乘因子 s 并写入
    a1[idx] = make_cuFloatComplex(cuCrealf(res1) * s, cuCimagf(res1) * s);
    a2[idx] = make_cuFloatComplex(cuCrealf(res2) * s, cuCimagf(res2) * s);
}

// CUDA kernel for DBS center (circular shift rows)
__global__ void dbs_center_by_fa_fast_cuda(const cudacd* inA,
                                           const cudacd* inB,
                                           cudacd* outA,
                                           cudacd* outB,
                                           size_t Na,
                                           size_t Nr,
                                           size_t k)
{
    size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    size_t total = Na * Nr;
    if (idx >= total) return;

    size_t row = idx / Nr;
    size_t col = idx % Nr;
    
    // 边界检查
    if (row >= Na || col >= Nr) return;
    
    // 鲁棒的取模运算：确保结果永远为正
    size_t shift = (Na - k) % Na;
    size_t new_row = (row + shift) % Na;
    size_t new_idx = new_row * Nr + col;

    outA[new_idx] = inA[idx];
    outB[new_idx] = inB[idx];
}

__global__ void az_decimate_two_channels_kernel(const cudacd* in1,
                                                const cudacd* in2,
                                                cudacd* out1,
                                                cudacd* out2,
                                                int W_orig,
                                                int M,
                                                int dec,
                                                int W_new)
{
    size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    size_t total = static_cast<size_t>(W_new) * static_cast<size_t>(M);
    if (idx >= total) return;

    int k_new = static_cast<int>(idx / M);
    int m = static_cast<int>(idx % M);
    size_t in_base = static_cast<size_t>(k_new) * static_cast<size_t>(dec) * static_cast<size_t>(M);
    cudacd acc1 = make_cuFloatComplex(0.0f, 0.0f);
    cudacd acc2 = make_cuFloatComplex(0.0f, 0.0f);
    for (int d = 0; d < dec; ++d) {
        size_t in_idx = in_base + static_cast<size_t>(d) * static_cast<size_t>(M) + static_cast<size_t>(m);
        if (in_idx < static_cast<size_t>(W_orig) * static_cast<size_t>(M)) {
            acc1 = cuCaddf(acc1, in1[in_idx]);
            acc2 = cuCaddf(acc2, in2[in_idx]);
        }
    }
    out1[idx] = acc1;
    out2[idx] = acc2;
}

__global__ void scale_complex_kernel(cudacd* data, size_t total, float coef)
{
    size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= total) return;
    cudacd v = data[idx];
    data[idx] = make_cuFloatComplex(v.x * coef, v.y * coef);
}

__global__ void complex_magnitude_export_kernel(const cudacd* input,
                                                float* amplitude,
                                                int* has_signal,
                                                size_t total)
{
    const size_t index = blockIdx.x * blockDim.x + threadIdx.x;
    if (index >= total) return;
    const cudacd value = input[index];
    const float magnitude = hypotf(value.x, value.y);
    amplitude[index] = magnitude;
    if (magnitude > 0.0f && isfinite(magnitude)) {
        atomicExch(has_signal, 1);
    }
}

struct NewProtocolDecodeParams
{
    int pulse_count;
    int samples_per_prt;
    int channel_count;
    int bytes_per_iq;
    int header_bytes;
    int prt_bytes;
    int read_channel_1;
    int read_channel_2;
    int fusion_channel_3;
    int fusion_channel_4;
    int four_channel_fusion;
    int four_channel_phase_compensation_enable;
    int mechanical_phase_center_pose;
    int phase_center_rotation_enable;
    int phase_center_rotation_sign;
    float sample_delay_s;
    float inv_fs_s;
    float phase_scale;
    float theta_rad;
    float phase_center_mount_angle_rad;
    float height_m;
    float squint_side;
    float offset_1[3];
    float offset_2[3];
    float offset_3[3];
    float offset_4[3];
};

__device__ __forceinline__ float load_new_protocol_component(
    const unsigned char* payload,
    size_t scalar_index,
    int bytes_per_iq)
{
    if (bytes_per_iq == 2) {
        return static_cast<float>(
            reinterpret_cast<const int16_t*>(payload)[scalar_index]);
    }
    return reinterpret_cast<const float*>(payload)[scalar_index];
}

__device__ __forceinline__ cudacd load_new_protocol_channel(
    const unsigned char* prt_block,
    size_t sample_index,
    int samples_per_prt,
    int channel_count,
    int channel_1based,
    int bytes_per_iq,
    int header_bytes,
    int prt_bytes)
{
    const size_t pulse_index = sample_index /
        static_cast<size_t>(samples_per_prt);
    const size_t range_index = sample_index - pulse_index *
        static_cast<size_t>(samples_per_prt);
    const size_t scalar_index =
        (range_index * static_cast<size_t>(channel_count) +
         static_cast<size_t>(channel_1based - 1)) * 2U;
    const unsigned char* payload = prt_block +
        pulse_index * static_cast<size_t>(prt_bytes) +
        static_cast<size_t>(header_bytes);
    return make_cuFloatComplex(
        load_new_protocol_component(payload, scalar_index, bytes_per_iq),
        load_new_protocol_component(payload, scalar_index + 1U, bytes_per_iq));
}

__device__ __forceinline__ int load_new_protocol_i16_le(
    const unsigned char* p)
{
    const unsigned int raw = static_cast<unsigned int>(p[0]) |
        (static_cast<unsigned int>(p[1]) << 8);
    return static_cast<int>(static_cast<short>(raw));
}

__device__ __forceinline__ float load_new_protocol_f32_le(
    const unsigned char* p)
{
    const unsigned int raw = static_cast<unsigned int>(p[0]) |
        (static_cast<unsigned int>(p[1]) << 8) |
        (static_cast<unsigned int>(p[2]) << 16) |
        (static_cast<unsigned int>(p[3]) << 24);
    return __int_as_float(static_cast<int>(raw));
}

__device__ __forceinline__ cudacd load_new_protocol_channel(
    const unsigned char* prt_block,
    size_t sample_index,
    int channel_1based,
    const NewProtocolDecodeParams& p)
{
    return load_new_protocol_channel(
        prt_block, sample_index, p.samples_per_prt, p.channel_count,
        channel_1based, p.bytes_per_iq, p.header_bytes, p.prt_bytes);
}

// |R*u-p|-R 的有理化 float 公式。避免两个约 80 km 的 float 距离直接相减，
// 国产 GPU 无需 double 也能保持载频相位精度。
__device__ __forceinline__ float stable_path_offset_float(
    float range_m,
    float los_e,
    float los_n,
    float los_h,
    const float offset[3])
{
    const float inv_r = 1.0f / range_m;
    const float dot = los_e * offset[0] +
                      los_n * offset[1] +
                      los_h * offset[2];
    const float offset_sq = offset[0] * offset[0] +
                            offset[1] * offset[1] +
                            offset[2] * offset[2];
    const float normalized = fmaxf(
        0.0f, 1.0f - 2.0f * dot * inv_r + offset_sq * inv_r * inv_r);
    return (-2.0f * dot + offset_sq * inv_r) /
           (sqrtf(normalized) + 1.0f);
}

__device__ __forceinline__ void mechanical_offset_to_en(
    const NewProtocolDecodeParams& p,
    const float local_offset[3],
    float theta_rad,
    float along_e,
    float along_n,
    float output_offset[3])
{
    const float angle = p.phase_center_mount_angle_rad +
        (p.phase_center_rotation_enable
             ? static_cast<float>((p.phase_center_rotation_sign < 0) ? -1 : 1) * theta_rad
             : 0.0f);
    float s = 0.0f;
    float c = 1.0f;
    sincosf(angle, &s, &c);
    const float local_x = c * local_offset[0] - s * local_offset[1];
    const float local_y = s * local_offset[0] + c * local_offset[1];
    const float right_e = along_n;
    const float right_n = -along_e;
    output_offset[0] = along_e * local_x + right_e * local_y;
    output_offset[1] = along_n * local_x + right_n * local_y;
    output_offset[2] = local_offset[2];
}

__device__ __forceinline__ cudacd geometric_compensation_factor(
    const NewProtocolDecodeParams& p,
    int range_index,
    const float reference_offset[3],
    const float source_offset[3],
    float theta_rad,
    float along_e,
    float along_n)
{
    const float light_speed = 299792458.0f;
    const float range_m = 0.5f * light_speed *
        (p.sample_delay_s + static_cast<float>(range_index) * p.inv_fs_s);
    const float horizontal_sq = fmaxf(
        0.0f, range_m * range_m - p.height_m * p.height_m);
    const float horizontal = sqrtf(horizontal_sq);
    float sin_theta = 0.0f;
    float cos_theta = 1.0f;
    sincosf(theta_rad, &sin_theta, &cos_theta);
    const float inv_range = 1.0f / range_m;
    float los_e = p.squint_side * horizontal * sin_theta * inv_range;
    float los_n = p.squint_side * horizontal * cos_theta * inv_range;
    float ref_offset_en[3] = {
        reference_offset[0], reference_offset[1], reference_offset[2]};
    float src_offset_en[3] = {
        source_offset[0], source_offset[1], source_offset[2]};
    if (p.mechanical_phase_center_pose) {
        const float cross = sqrtf(fmaxf(
            0.0f, 1.0f - sin_theta * sin_theta));
        const float left_e = -along_n;
        const float left_n = along_e;
        const float right_e = along_n;
        const float right_n = -along_e;
        const float side_e = p.squint_side < 0.0f ? left_e : right_e;
        const float side_n = p.squint_side < 0.0f ? left_n : right_n;
        los_e = horizontal * (cross * side_e + sin_theta * along_e) * inv_range;
        los_n = horizontal * (cross * side_n + sin_theta * along_n) * inv_range;
        mechanical_offset_to_en(p, reference_offset, theta_rad,
                                along_e, along_n, ref_offset_en);
        mechanical_offset_to_en(p, source_offset, theta_rad,
                                along_e, along_n, src_offset_en);
    }
    const float los_h = -p.height_m * inv_range;
    const float ref_delta = stable_path_offset_float(
        range_m, los_e, los_n, los_h, ref_offset_en);
    const float src_delta = stable_path_offset_float(
        range_m, los_e, los_n, los_h, src_offset_en);
    const float two_pi = 6.28318530717958647692f;
    float phase = p.phase_scale * (ref_delta - src_delta);
    phase -= floorf((phase + 3.14159265358979323846f) / two_pi) * two_pi;
    float sin_phase = 0.0f;
    float cos_phase = 1.0f;
    sincosf(phase, &sin_phase, &cos_phase);
    return make_cuFloatComplex(cos_phase, sin_phase);
}

__device__ __forceinline__ cudacd geometric_compensation_factor_for_pulse(
    const unsigned char* payload,
    const NewProtocolDecodeParams& p,
    int range_index,
    int pulse_index,
    const float reference_offset[3],
    const float source_offset[3])
{
    if (!p.mechanical_phase_center_pose) {
        return geometric_compensation_factor(
            p, range_index, reference_offset, source_offset,
            p.theta_rad, 0.0f, 1.0f);
    }
    const unsigned char* header = payload +
        static_cast<size_t>(pulse_index) * static_cast<size_t>(p.prt_bytes);
    const float theta_rad = static_cast<float>(
        static_cast<double>(load_new_protocol_i16_le(
            header + 218)) * 0.01 * M_PI / 180.0);
    const float ve = load_new_protocol_f32_le(header + 132);
    const float vn = load_new_protocol_f32_le(header + 128);
    const float speed = hypotf(ve, vn);
    const float along_e = speed > 1.0e-6f ? ve / speed : 0.0f;
    const float along_n = speed > 1.0e-6f ? vn / speed : 1.0f;
    return geometric_compensation_factor(
        p, range_index, reference_offset, source_offset,
        theta_rad, along_e, along_n);
}

// 每个线程负责一个距离门，跨完整相干脉冲积累残余相位。采用 float Kahan
// 累加以弥补国产 GPU 不支持 device double，同时保持与 CPU double 参考接近。
__global__ void new_protocol_fusion_factor_kernel(
    const unsigned char* payload,
    cudacd* factor_13,
    cudacd* factor_24,
    NewProtocolDecodeParams p)
{
    const int n = blockIdx.x * blockDim.x + threadIdx.x;
    if (n >= p.samples_per_prt) return;
    if (!p.four_channel_phase_compensation_enable) {
        factor_13[n] = make_cuFloatComplex(1.0f, 0.0f);
        factor_24[n] = make_cuFloatComplex(1.0f, 0.0f);
        return;
    }

    float sum13_re = 0.0f;
    float sum13_im = 0.0f;
    float corr13_re = 0.0f;
    float corr13_im = 0.0f;
    float sum24_re = 0.0f;
    float sum24_im = 0.0f;
    float corr24_re = 0.0f;
    float corr24_im = 0.0f;
    for (int k = 0; k < p.pulse_count; ++k) {
        const size_t index = static_cast<size_t>(k) *
            static_cast<size_t>(p.samples_per_prt) + static_cast<size_t>(n);
        const cudacd comp13 = geometric_compensation_factor_for_pulse(
            payload, p, n, k, p.offset_1, p.offset_3);
        const cudacd comp24 = geometric_compensation_factor_for_pulse(
            payload, p, n, k, p.offset_2, p.offset_4);
        const cudacd ch1 = load_new_protocol_channel(
            payload, index, p.read_channel_1, p);
        const cudacd ch2 = load_new_protocol_channel(
            payload, index, p.read_channel_2, p);
        const cudacd ch3 = cuCmulf(load_new_protocol_channel(
            payload, index, p.fusion_channel_3, p), comp13);
        const cudacd ch4 = cuCmulf(load_new_protocol_channel(
            payload, index, p.fusion_channel_4, p), comp24);
        const cudacd cross13 = cuCmulf(ch1, cuConjf(ch3));
        const cudacd cross24 = cuCmulf(ch2, cuConjf(ch4));

        float y = cross13.x - corr13_re;
        float t = sum13_re + y;
        corr13_re = (t - sum13_re) - y;
        sum13_re = t;
        y = cross13.y - corr13_im;
        t = sum13_im + y;
        corr13_im = (t - sum13_im) - y;
        sum13_im = t;
        y = cross24.x - corr24_re;
        t = sum24_re + y;
        corr24_re = (t - sum24_re) - y;
        sum24_re = t;
        y = cross24.y - corr24_im;
        t = sum24_im + y;
        corr24_im = (t - sum24_im) - y;
        sum24_im = t;
    }

    const float mag13 = hypotf(sum13_re, sum13_im);
    const float mag24 = hypotf(sum24_re, sum24_im);
    const cudacd residual13 = mag13 > 0.0f
        ? make_cuFloatComplex(sum13_re / mag13, sum13_im / mag13)
        : make_cuFloatComplex(1.0f, 0.0f);
    const cudacd residual24 = mag24 > 0.0f
        ? make_cuFloatComplex(sum24_re / mag24, sum24_im / mag24)
        : make_cuFloatComplex(1.0f, 0.0f);
    // The geometric factor is evaluated again for the corresponding PRT in
    // the decode kernel.  Keep only the aperture residual here; using one
    // centre-angle factor for every PRT would collapse a mechanical sweep
    // back to the old electronic-scan approximation.
    factor_13[n] = residual13;
    factor_24[n] = residual24;
}

__global__ void new_protocol_decode_fuse_kernel(
    const unsigned char* payload,
    const cudacd* factor_13,
    const cudacd* factor_24,
    cudacd* out1,
    cudacd* out2,
    NewProtocolDecodeParams p)
{
    const size_t index = blockIdx.x * blockDim.x + threadIdx.x;
    const size_t total = static_cast<size_t>(p.pulse_count) *
                         static_cast<size_t>(p.samples_per_prt);
    if (index >= total) return;
    const cudacd ch1 = load_new_protocol_channel(
        payload, index, p.read_channel_1, p);
    const cudacd ch2 = load_new_protocol_channel(
        payload, index, p.read_channel_2, p);
    if (!p.four_channel_fusion) {
        out1[index] = ch1;
        out2[index] = ch2;
        return;
    }
    const int n = static_cast<int>(index % static_cast<size_t>(p.samples_per_prt));
    const cudacd ch3 = load_new_protocol_channel(
        payload, index, p.fusion_channel_3, p);
    const cudacd ch4 = load_new_protocol_channel(
        payload, index, p.fusion_channel_4, p);
    cudacd fused3 = ch3;
    cudacd fused4 = ch4;
    if (p.four_channel_phase_compensation_enable) {
        const int pulse_index = static_cast<int>(
            index / static_cast<size_t>(p.samples_per_prt));
        const cudacd comp13 = geometric_compensation_factor_for_pulse(
            payload, p, n, pulse_index, p.offset_1, p.offset_3);
        const cudacd comp24 = geometric_compensation_factor_for_pulse(
            payload, p, n, pulse_index, p.offset_2, p.offset_4);
        fused3 = cuCmulf(ch3, cuCmulf(comp13, factor_13[n]));
        fused4 = cuCmulf(ch4, cuCmulf(comp24, factor_24[n]));
    }
    out1[index] = make_cuFloatComplex(
        0.5f * (ch1.x + fused3.x),
        0.5f * (ch1.y + fused3.y));
    out2[index] = make_cuFloatComplex(
        0.5f * (ch2.x + fused4.x),
        0.5f * (ch2.y + fused4.y));
}

__global__ void adjacent_corr_reduce_kernel(const cudacd* data,
                                            cudacd* block_sums,
                                            int k,
                                            int Na,
                                            int Nr)
{
    extern __shared__ cudacd shared[];
    int tid = threadIdx.x;
    size_t total = static_cast<size_t>(Na - k) * static_cast<size_t>(Nr);
    cudacd acc = make_cuFloatComplex(0.0f, 0.0f);

    for (size_t idx = blockIdx.x * blockDim.x + tid;
         idx < total;
         idx += static_cast<size_t>(blockDim.x) * static_cast<size_t>(gridDim.x)) {
        int row = static_cast<int>(idx / Nr) + k;
        int col = static_cast<int>(idx % Nr);
        size_t cur = static_cast<size_t>(row) * static_cast<size_t>(Nr) + static_cast<size_t>(col);
        size_t prev = static_cast<size_t>(row - k) * static_cast<size_t>(Nr) + static_cast<size_t>(col);
        acc = cuCaddf(acc, cuCmulf(data[cur], cuConjf(data[prev])));
    }

    shared[tid] = acc;
    __syncthreads();

    for (int stride = blockDim.x / 2; stride > 0; stride >>= 1) {
        if (tid < stride) {
            shared[tid] = cuCaddf(shared[tid], shared[tid + stride]);
        }
        __syncthreads();
    }

    if (tid == 0) {
        block_sums[blockIdx.x] = shared[0];
    }
}

__global__ void adjacent_corr_per_range_kernel(const cudacd* data,
                                               cudacd* range_sums,
                                               int k,
                                               int Na,
                                               int Nr)
{
    const int col = blockIdx.x * blockDim.x + threadIdx.x;
    if (col >= Nr) return;
    cudacd acc = make_cuFloatComplex(0.0f, 0.0f);
    for (int row = k; row < Na; ++row) {
        const size_t cur = static_cast<size_t>(row) * static_cast<size_t>(Nr) +
                           static_cast<size_t>(col);
        const size_t prev = static_cast<size_t>(row - k) * static_cast<size_t>(Nr) +
                            static_cast<size_t>(col);
        acc = cuCaddf(acc, cuCmulf(data[cur], cuConjf(data[prev])));
    }
    range_sums[col] = acc;
}

// Production-grade CUDA implementation with pre-allocated workspace
bool GMTIProcessor::alignFFTAndDBS_CUDA(const std::vector<std::complex<float>> &data1,
                                        const std::vector<std::complex<float>> &data2,
                                        int skip,
                                        float fa2,
                                        const Config &cfg,
                                        std::vector<std::complex<float>> &out1,
                                        std::vector<std::complex<float>> &out2)
{
    // --- 准备 CUDA Event 用于测量内核执行耗时 ---
    cudaEvent_t start, stop;
    cudaEventCreate(&start);
    cudaEventCreate(&stop);

    auto wall_start = std::chrono::high_resolution_clock::now();

    const size_t Na = static_cast<size_t>(effectivePulseNum(cfg));
    const size_t Nr = static_cast<size_t>(cfg.rg_len);
    const size_t total = Na * Nr;
    if (Na == 0 || Nr == 0) return false;
    if (data1.size() != total || data2.size() != total) return false;

    // ===== 1) 内存管理：按需分配或复用 =====
    // 需要 6 个缓冲：d_d1, d_d2, d_a1, d_a2, d_t1, d_t2
    size_t needed_bytes = 6 * total * sizeof(cudacd);
    
    if (d_workspace == nullptr || d_workspace_bytes < needed_bytes) {
        // 释放旧内存
        if (d_workspace != nullptr) {
            CUDA_CHECK(cudaFree(d_workspace));
        }
        // 新分配
        CUDA_CHECK(cudaMalloc(&d_workspace, needed_bytes));
        d_workspace_bytes = needed_bytes;
        DBG("CUDA workspace allocated: " << (needed_bytes / (1024*1024)) << " MB");
    }

    // 指针分散：将 workspace 分成 6 个部分
    cudacd* d_d1 = reinterpret_cast<cudacd*>(d_workspace) + 0 * total;
    cudacd* d_d2 = reinterpret_cast<cudacd*>(d_workspace) + 1 * total;
    cudacd* d_a1 = reinterpret_cast<cudacd*>(d_workspace) + 2 * total;
    cudacd* d_a2 = reinterpret_cast<cudacd*>(d_workspace) + 3 * total;
    cudacd* d_t1 = reinterpret_cast<cudacd*>(d_workspace) + 4 * total;
    cudacd* d_t2 = reinterpret_cast<cudacd*>(d_workspace) + 5 * total;

    // Host to Device
    CUDA_CHECK(cudaMemcpy(d_d1, reinterpret_cast<const cudacd*>(data1.data()), total * sizeof(cudacd), cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(d_d2, reinterpret_cast<const cudacd*>(data2.data()), total * sizeof(cudacd), cudaMemcpyHostToDevice));

    // 初始化中间缓冲区为零，避免残留数据导致的NaN
    CUDA_CHECK(cudaMemset(d_t1, 0, total * sizeof(cudacd)));
    CUDA_CHECK(cudaMemset(d_t2, 0, total * sizeof(cudacd)));

    cudaEventRecord(start);
    // ===== 2) 通道对齐 =====
    int threads = 256;
    int blocks = static_cast<int>((total + threads - 1) / threads);
    const gmti::azimuth_window::Type window_type =
        gmti::azimuth_window::parseType(cfg.azimuth_fft_window);
    const double coherent_gain = gmti::azimuth_window::coherentGain(window_type, Na);
    const float window_normalization =
        cfg.azimuth_fft_window_normalize && coherent_gain > 0.0
            ? static_cast<float>(1.0 / coherent_gain) : 1.0f;
    align_two_channels_fast_cuda<<<blocks, threads>>>(
        d_d1, d_d2, d_a1, d_a2, skip, Na, Nr,
        static_cast<int>(window_type), window_normalization);
    CUDA_CHECK(cudaGetLastError());
    CUDA_CHECK(cudaDeviceSynchronize());

    // ===== 3) 列向 FFT （使用缓存的计划）=====
    // 检查是否需要重建计划
    if (cached_Na_ != (int)Na || cached_Nr_ != (int)Nr) {
        // 销毁旧计划
        if (cufft_plan_ != -1) {
            CUFFT_CHECK(cufftDestroy((cufftHandle)cufft_plan_));
        }

        // 创建新计划
        cufftHandle plan;
        int n[] = {(int)Na}; 
        int howmany = (int)Nr;

        // 输入布局：
        // 相邻元素间距 istride = Nr (跨过一行取同列下一个点)
        // 相邻批次间距 idist = 1   (第一列算完，下一批从第一行第二个点开始)
        int istride = (int)Nr;
        int idist = 1;

        // 输出布局：
        // 如果你希望输出保持 HxW 布局（不转置），则 ostride/odist 必须与输入一致
        int ostride = (int)Nr;
        int odist = 1;

        CUFFT_CHECK(cufftPlanMany(&plan, 1, n,
                      n, istride, idist,
                      n, ostride, odist,
                      CUFFT_C2C, howmany));

        // 缓存计划（cufftHandle 本质是 int）
        cufft_plan_ = (int)plan;
        cached_Na_ = static_cast<int>(Na);
        cached_Nr_ = static_cast<int>(Nr);
        DBG("cuFFT plan created and cached for Na=" << Na << ", Nr=" << Nr);
    }

    // 使用缓存的计划执行 FFT
    cufftHandle plan = (cufftHandle)cufft_plan_;
    CUFFT_CHECK(cufftExecC2C(plan, reinterpret_cast<cufftComplex*>(d_a1), 
                             reinterpret_cast<cufftComplex*>(d_a1), CUFFT_FORWARD));
    CUFFT_CHECK(cufftExecC2C(plan, reinterpret_cast<cufftComplex*>(d_a2), 
                             reinterpret_cast<cufftComplex*>(d_a2), CUFFT_FORWARD));

    // ===== 4) DBS 中心化 =====
    int width = static_cast<int>(Na);
    if (!(cfg.PRF > 0.0)) return false;
    int center_num = static_cast<int>(std::floor((fa2 + 0.5f * static_cast<float>(cfg.PRF)) /
                                                 static_cast<float>(cfg.PRF) * width)) + 1;
    int cstart = center_num - width / 2;
    int k_1b = cstart;
    while (k_1b < 1) k_1b += width;
    while (k_1b > width) k_1b -= width;
    size_t k = static_cast<size_t>(k_1b - 1);

    // 使用明确的最终指针，避免局部指针交换导致的内存访问错误
    cudacd* res_ptr1 = d_a1;
    cudacd* res_ptr2 = d_a2;

    if (k != 0) {
        dbs_center_by_fa_fast_cuda<<<blocks, threads>>>(d_a1, d_a2, d_t1, d_t2, Na, Nr, k);
        CUDA_CHECK(cudaGetLastError());
        CUDA_CHECK(cudaDeviceSynchronize());

        // 明确指向 DBS 后的结果
        res_ptr1 = d_t1;
        res_ptr2 = d_t2;
    }
    cudaEventRecord(stop);

    // ===== 5) Device to Host =====
    out1.resize(total);
    out2.resize(total);
    CUDA_CHECK(cudaMemcpy(reinterpret_cast<cudacd*>(out1.data()), res_ptr1, total * sizeof(cudacd), cudaMemcpyDeviceToHost));
    CUDA_CHECK(cudaGetLastError()); // 检查可能的内存越界错误
    CUDA_CHECK(cudaMemcpy(reinterpret_cast<cudacd*>(out2.data()), res_ptr2, total * sizeof(cudacd), cudaMemcpyDeviceToHost));
    CUDA_CHECK(cudaGetLastError()); // 检查可能的内存越界错误

    auto wall_end = std::chrono::high_resolution_clock::now();

    float milliseconds = 0;
    cudaEventElapsedTime(&milliseconds, start, stop);

    std::chrono::duration<float, std::milli> wall_ms = wall_end - wall_start;

    // printf("[TIME] GPU Kernel Pure Exec: %.3f ms\n", milliseconds);
    // printf("[TIME] Total Wall Time (Inc. Memcpy): %.3f ms\n", wall_ms.count());

    cudaEventDestroy(start);
    cudaEventDestroy(stop);

    return true;
}

bool GMTIProcessor::cuda_decode_new_protocol_async(
    const NewProtocolGpuInput &input,
    const Config &cfg)
{
    if (!input.valid() || gpu_ptrs_.d1 == nullptr || gpu_ptrs_.d2 == nullptr ||
        gpu_ptrs_.a1 == nullptr || gpu_ptrs_.csi == nullptr ||
        gpu_single_buffer_bytes_ == 0U) {
        return false;
    }
    if (input.pulse_count != static_cast<size_t>(effectivePulseNum(cfg)) ||
        input.samples_per_prt != static_cast<size_t>(cfg.pulse_len) ||
        input.channel_count > static_cast<size_t>(std::numeric_limits<int>::max()) ||
        input.header_bytes > static_cast<size_t>(std::numeric_limits<int>::max()) ||
        input.prt_bytes > static_cast<size_t>(std::numeric_limits<int>::max()) ||
        input.byteSize() > 4U * gpu_single_buffer_bytes_ ||
        2U * input.samples_per_prt * sizeof(cudacd) > gpu_single_buffer_bytes_) {
        ERR("packed new-protocol input does not fit the persistent CUDA workspace");
        return false;
    }
    const auto valid_channel = [&input](int channel) {
        return channel >= 1 && static_cast<size_t>(channel) <= input.channel_count;
    };
    if (!valid_channel(input.read_channel_1) ||
        !valid_channel(input.read_channel_2) ||
        (input.four_channel_fusion &&
         (!valid_channel(input.fusion_channel_3) ||
          !valid_channel(input.fusion_channel_4)))) {
        return false;
    }

    CUDA_CHECK(cudaMemcpyAsync(
        gpu_ptrs_.a1, input.data(), input.byteSize(),
        cudaMemcpyHostToDevice, stream_compute_));

    NewProtocolDecodeParams p{};
    p.pulse_count = static_cast<int>(input.pulse_count);
    p.samples_per_prt = static_cast<int>(input.samples_per_prt);
    p.channel_count = static_cast<int>(input.channel_count);
    p.bytes_per_iq = static_cast<int>(input.bytes_per_iq);
    p.header_bytes = static_cast<int>(input.header_bytes);
    p.prt_bytes = static_cast<int>(input.prt_bytes);
    p.read_channel_1 = input.read_channel_1;
    p.read_channel_2 = input.read_channel_2;
    p.fusion_channel_3 = input.fusion_channel_3;
    p.fusion_channel_4 = input.fusion_channel_4;
    p.four_channel_fusion = input.four_channel_fusion ? 1 : 0;
    p.four_channel_phase_compensation_enable =
        input.four_channel_phase_compensation_enable ? 1 : 0;
    p.mechanical_phase_center_pose =
        cfg.scan_mode == ScanMode::Mechanical ? 1 : 0;
    p.phase_center_rotation_enable =
        cfg.mechanical_scan.phase_center_rotation_enable ? 1 : 0;
    p.phase_center_rotation_sign = cfg.mechanical_scan.phase_center_rotation_sign;
    p.sample_delay_s = static_cast<float>(cfg.sample_delay_us * 1.0e-6);
    // XML parsing has already normalized fc/fs to Hz.  Keep compatibility
    // with legacy direct Config callers that still use GHz/MHz units, but do
    // not multiply normal runtime values a second time.
    const double fs_hz = cfg.fs > 1.0e5 ? cfg.fs : cfg.fs * 1.0e6;
    const double fc_hz = cfg.fc > 1.0e6 ? cfg.fc : cfg.fc * 1.0e9;
    if (!(fc_hz > 0.0) || !(fs_hz > 0.0)) {
        return false;
    }
    p.inv_fs_s = static_cast<float>(1.0 / fs_hz);
    p.phase_scale = static_cast<float>(
        static_cast<double>(cfg.four_channel_carrier_phase_sign) *
        2.0 * M_PI * fc_hz / C);
    p.theta_rad = static_cast<float>(
        input.compensation_theta_deg * M_PI / 180.0);
    p.phase_center_mount_angle_rad = static_cast<float>(
        cfg.mechanical_scan.phase_center_mount_angle_deg * M_PI / 180.0);
    const float compensation_height_m = static_cast<float>(input.compensation_height_m);
    p.height_m = compensation_height_m > 0.0f ? compensation_height_m : 0.0f;
    p.squint_side = cfg.four_channel_fusion_squint_side == 1 ? -1.0f : 1.0f;

    const int channels[4] = {
        input.read_channel_1, input.read_channel_2,
        input.fusion_channel_3, input.fusion_channel_4};
    float *offsets[4] = {p.offset_1, p.offset_2, p.offset_3, p.offset_4};
    for (int i = 0; i < 4; ++i) {
        const int channel = channels[i] > 0 ? channels[i] : channels[i % 2];
        if (!valid_channel(channel) || channel > 4) {
            return false;
        }
        for (int axis = 0; axis < 3; ++axis) {
            offsets[i][axis] = static_cast<float>(
                cfg.four_channel_offsets_m[static_cast<size_t>(channel - 1)]
                                          [static_cast<size_t>(axis)]);
        }
    }

    cudacd *factor13 = static_cast<cudacd*>(gpu_ptrs_.csi);
    cudacd *factor24 = factor13 + input.samples_per_prt;
    const int threads = 256;
    if (input.four_channel_fusion) {
        const int range_blocks = static_cast<int>(
            (input.samples_per_prt + static_cast<size_t>(threads) - 1U) /
            static_cast<size_t>(threads));
        new_protocol_fusion_factor_kernel<<<
            range_blocks, threads, 0, stream_compute_>>>(
                static_cast<const unsigned char*>(gpu_ptrs_.a1),
                factor13, factor24, p);
        if (cudaGetLastError() != cudaSuccess) {
            ERR("new_protocol_fusion_factor_kernel launch failed");
            return false;
        }
    }

    const size_t total = input.pulse_count * input.samples_per_prt;
    const int blocks = static_cast<int>(
        (total + static_cast<size_t>(threads) - 1U) /
        static_cast<size_t>(threads));
    new_protocol_decode_fuse_kernel<<<blocks, threads, 0, stream_compute_>>>(
        static_cast<const unsigned char*>(gpu_ptrs_.a1),
        factor13, factor24,
        static_cast<cudacd*>(gpu_ptrs_.d1),
        static_cast<cudacd*>(gpu_ptrs_.d2), p);
    if (cudaGetLastError() != cudaSuccess) {
        ERR("new_protocol_decode_fuse_kernel launch failed");
        return false;
    }
    return true;
}

bool GMTIProcessor::cuda_export_dbs_amplitude_sync(
    std::vector<float> &amplitude,
    bool &has_signal,
    size_t total)
{
    has_signal = false;
    if (total == 0U || gpu_ptrs_.t1 == nullptr ||
        gpu_ptrs_.dbs_amplitude == nullptr ||
        dbs_amplitude_elements_ < total || gpu_ptrs_.d_rg_sums == nullptr) {
        return false;
    }
    amplitude.resize(total);
    int *device_signal = static_cast<int*>(gpu_ptrs_.d_rg_sums);
    CUDA_CHECK(cudaMemsetAsync(device_signal, 0, sizeof(int), stream_compute_));
    const int threads = 256;
    const int blocks = static_cast<int>(
        (total + static_cast<size_t>(threads) - 1U) /
        static_cast<size_t>(threads));
    // d1/d2 remain the range-compressed channel inputs for the subsequent
    // CSI second pass.  The scalar amplitude has its own dedicated buffer;
    // no complex channel or CSI workspace is aliased here.
    complex_magnitude_export_kernel<<<blocks, threads, 0, stream_compute_>>>(
        static_cast<const cudacd*>(gpu_ptrs_.t1),
        static_cast<float*>(gpu_ptrs_.dbs_amplitude), device_signal, total);
    if (cudaGetLastError() != cudaSuccess) {
        ERR("complex_magnitude_export_kernel launch failed");
        return false;
    }
    int host_signal = 0;
    CUDA_CHECK(cudaMemcpyAsync(
        amplitude.data(), gpu_ptrs_.dbs_amplitude, total * sizeof(float),
        cudaMemcpyDeviceToHost, stream_compute_));
    CUDA_CHECK(cudaMemcpyAsync(
        &host_signal, device_signal, sizeof(int),
        cudaMemcpyDeviceToHost, stream_compute_));
    CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
    has_signal = host_signal != 0;
    return true;
}

bool GMTIProcessor::cuda_stage_align_async(
    int skip, size_t Na, size_t Nr, const Config& cfg) {
    if (gpu_ptrs_.d1 == nullptr) {
        std::cerr << "CRITICAL: gpu_ptrs_.d1 is NULL before kernel launch!" << std::endl;
        return false;
    }
    
    int total = static_cast<int>(Na * Nr);
    int threads = 256;
    int blocks = (total + threads - 1) / threads;

    const gmti::azimuth_window::Type window_type =
        gmti::azimuth_window::parseType(cfg.azimuth_fft_window);
    const double coherent_gain = gmti::azimuth_window::coherentGain(window_type, Na);
    const float window_normalization =
        cfg.azimuth_fft_window_normalize && coherent_gain > 0.0
            ? static_cast<float>(1.0 / coherent_gain) : 1.0f;

    // 执行对齐核函数：从 d1/d2 到 a1/a2，并在 FFT 前加慢时间窗。
    align_two_channels_fast_cuda<<<blocks, threads, 0, stream_compute_>>>(
        (cudacd*)gpu_ptrs_.d1, (cudacd*)gpu_ptrs_.d2, 
        (cudacd*)gpu_ptrs_.a1, (cudacd*)gpu_ptrs_.a2, 
        skip, (int)Na, (int)Nr,
        static_cast<int>(window_type), window_normalization
    );
    return cudaGetLastError() == cudaSuccess;
}

bool GMTIProcessor::cuda_stage_fft_async(size_t Na, size_t Nr) {
    // 执行 FFT：a1/a2 原地 (In-place) 计算
    cufftResult result = cufftExecC2C(
        cufft_plan_,
        (cufftComplex*)gpu_ptrs_.a1,
        (cufftComplex*)gpu_ptrs_.a1,
        CUFFT_FORWARD);
    if (result != CUFFT_SUCCESS) {
        return false;
    }
                 
    result = cufftExecC2C(
        cufft_plan_,
        (cufftComplex*)gpu_ptrs_.a2,
        (cufftComplex*)gpu_ptrs_.a2,
        CUFFT_FORWARD);
    return result == CUFFT_SUCCESS;
}

bool GMTIProcessor::cuda_stage_dbs_async(float fa2, float prf, size_t Na, size_t Nr) {
    // 1. 计算偏移 k
    if (!(prf > 0.0f) || Na == 0 || Nr == 0) {
        return false;
    }
    int width = (int)Na;
    int center_num = (int)std::floor((fa2 + 0.5f * prf) / prf * width) + 1;
    int k_1b = center_num - width / 2;
    while (k_1b < 1) k_1b += width;
    while (k_1b > width) k_1b -= width;
    size_t k = (size_t)(k_1b - 1);

    // 2. 启动 DBS 核函数：从 a1/a2 到 t1/t2
    int total = (int)(Na * Nr);
    int threads = 256;
    int blocks = (total + threads - 1) / threads;

    dbs_center_by_fa_fast_cuda<<<blocks, threads, 0, stream_compute_>>>(
        (cudacd*)gpu_ptrs_.a1, (cudacd*)gpu_ptrs_.a2, 
        (cudacd*)gpu_ptrs_.t1, (cudacd*)gpu_ptrs_.t2, 
        (int)Na, (int)Nr, k
    );
    return cudaGetLastError() == cudaSuccess;
}

bool GMTIProcessor::cuda_stage_az_decimate_async(int W_orig, int M, int dec) {
    if (W_orig <= 0 || M <= 0 || dec <= 0 || gpu_ptrs_.d1 == nullptr || gpu_ptrs_.d2 == nullptr ||
        gpu_ptrs_.a1 == nullptr || gpu_ptrs_.a2 == nullptr) {
        return false;
    }
    if (W_orig % dec != 0) {
        return false;
    }
    const int W_new = W_orig / dec;
    if (dec == 1) {
        return true;
    }

    const size_t total_out = static_cast<size_t>(W_new) * static_cast<size_t>(M);
    const int threads = 256;
    const int blocks = static_cast<int>((total_out + threads - 1) / threads);
    az_decimate_two_channels_kernel<<<blocks, threads, 0, stream_compute_>>>(
        (const cudacd*)gpu_ptrs_.d1, (const cudacd*)gpu_ptrs_.d2,
        (cudacd*)gpu_ptrs_.a1, (cudacd*)gpu_ptrs_.a2,
        W_orig, M, dec, W_new);
    if (cudaGetLastError() != cudaSuccess) {
        ERR("az_decimate_two_channels_kernel launch failed");
        return false;
    }

    const size_t bytes = total_out * sizeof(cudacd);
    CUDA_CHECK(cudaMemcpyAsync(gpu_ptrs_.d1, gpu_ptrs_.a1, bytes, cudaMemcpyDeviceToDevice, stream_compute_));
    CUDA_CHECK(cudaMemcpyAsync(gpu_ptrs_.d2, gpu_ptrs_.a2, bytes, cudaMemcpyDeviceToDevice, stream_compute_));
    CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
    return true;
}

bool GMTIProcessor::cuda_scale_channel2_async(float coef, size_t total) {
    if (gpu_ptrs_.d2 == nullptr || total == 0) {
        return false;
    }
    const int threads = 256;
    const int blocks = static_cast<int>((total + threads - 1) / threads);
    scale_complex_kernel<<<blocks, threads, 0, stream_compute_>>>((cudacd*)gpu_ptrs_.d2, total, coef);
    if (cudaGetLastError() != cudaSuccess) {
        ERR("scale_complex_kernel launch failed");
        return false;
    }
    CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
    return true;
}

bool GMTIProcessor::cuda_compute_fd_ctr_from_d1(int k, const Config& cfg, double& fd_ctr) {
    fd_ctr = 0.0;
    const int Na = effectivePulseNum(cfg);
    const int Nr = cfg.rg_len;
    if (k <= 0 || Na <= k || Nr <= 0 || !(cfg.PRF > 0.0) || gpu_ptrs_.d1 == nullptr) {
        return false;
    }

    const int threads = 256;
    if (gpu_ptrs_.d_rg_sums == nullptr) return false;

    if (cfg.doppler_center_robust_enable) {
        // d_rg_sums is not consumed by a later stage until this synchronous
        // center estimate has completed. Reuse it instead of allocating and
        // freeing an Nr-sized buffer for every beam.
        cudacd* d_range_sums = static_cast<cudacd*>(gpu_ptrs_.d_rg_sums);
        const int range_blocks = (Nr + threads - 1) / threads;
        adjacent_corr_per_range_kernel<<<range_blocks, threads, 0, stream_compute_>>>(
            (const cudacd*)gpu_ptrs_.d1, d_range_sums, k, Na, Nr);
        if (cudaGetLastError() != cudaSuccess) {
            ERR("adjacent_corr_per_range_kernel launch failed");
            return false;
        }
        std::vector<cudacd> h_range_sums(static_cast<size_t>(Nr));
        CUDA_CHECK(cudaMemcpyAsync(h_range_sums.data(), d_range_sums,
                                   static_cast<size_t>(Nr) * sizeof(cudacd),
                                   cudaMemcpyDeviceToHost, stream_compute_));
        CUDA_CHECK(cudaStreamSynchronize(stream_compute_));

        std::vector<std::complex<double> > per_range(static_cast<size_t>(Nr));
        for (int col = 0; col < Nr; ++col) {
            per_range[static_cast<size_t>(col)] = std::complex<double>(
                static_cast<double>(h_range_sums[static_cast<size_t>(col)].x),
                static_cast<double>(h_range_sums[static_cast<size_t>(col)].y));
        }
        const gmti::doppler_center::RobustCenterResult robust =
            gmti::doppler_center::robustCorrelationSum(
                per_range, cfg.doppler_center_trim_top_fraction,
                static_cast<size_t>(cfg.doppler_center_min_valid_range_bins));
        if (!robust.valid) {
            ERR("robust Doppler-center estimate has insufficient valid range bins");
            return false;
        }
        fd_ctr = (cfg.PRF / (2.0 * M_PI)) * gmti::trig_lut::atan2(
            robust.correlation_sum.imag(), robust.correlation_sum.real());
        if (cfg.runtime_diagnostics_enabled) {
            std::cout << "[fd-center][robust] fd_ctr_hz=" << fd_ctr
                      << " valid_range_bins=" << robust.valid_range_bins
                      << " clipped_range_bins=" << robust.clipped_range_bins
                      << " magnitude_cap=" << robust.magnitude_cap << std::endl;
        }
        return true;
    }

    const int blocks = 256;
    // The same d_rg_sums slot is large enough for the 256 partial sums in the
    // non-robust path as well; this path is synchronous before the slot is
    // reused by range-sum processing.
    cudacd* d_block_sums = static_cast<cudacd*>(gpu_ptrs_.d_rg_sums);
    adjacent_corr_reduce_kernel<<<blocks, threads, threads * sizeof(cudacd), stream_compute_>>>(
        (const cudacd*)gpu_ptrs_.d1, d_block_sums, k, Na, Nr);
    if (cudaGetLastError() != cudaSuccess) {
        ERR("adjacent_corr_reduce_kernel launch failed");
        return false;
    }

    std::vector<cudacd> h_sums(blocks);
    CUDA_CHECK(cudaMemcpyAsync(h_sums.data(), d_block_sums,
                               static_cast<size_t>(blocks) * sizeof(cudacd),
                               cudaMemcpyDeviceToHost, stream_compute_));
    CUDA_CHECK(cudaStreamSynchronize(stream_compute_));

    double re = 0.0;
    double im = 0.0;
    for (const auto& v : h_sums) {
        re += static_cast<double>(v.x);
        im += static_cast<double>(v.y);
    }
    fd_ctr = (cfg.PRF / (2.0 * M_PI)) * gmti::trig_lut::atan2(im, re);
    return true;
}

bool GMTIProcessor::cuda_stage_rg_sum_async(const Config& cfg) {
    int Nr = cfg.rg_len;
    const int Na = effectivePulseNum(cfg);
    if (Nr <= 0 || Na <= 0) return false;
    const int rg_st = std::max(0, std::min(cfg.rg_st, Nr - 1));
    const int rg_ed = std::max(0, std::min(cfg.rg_ed, Nr - 1));
    const int az_st = std::max(0, std::min(cfg.az_st, Na - 1));
    const int az_ed = std::max(0, std::min(cfg.az_ed, Na - 1));
    if (rg_st > rg_ed || az_st > az_ed) return false;
    int threads = 256;
    int blocks = (Nr + threads - 1) / threads;

    // 使用刚才在 initcuFFTPlans 中算好的指针
    // 注意：输入通常是 a1, a2 (对齐+FFT+DBS 后的结果)
    compute_phase_sum_kernel<<<blocks, threads, 0, stream_compute_>>>(
        (const cuFloatComplex*)gpu_ptrs_.t1,
        (const cuFloatComplex*)gpu_ptrs_.t2,
        (cuFloatComplex*)gpu_ptrs_.d_rg_sums,
        az_st, az_ed,
        Na, Nr,
        rg_st, rg_ed
    );

    // 检查核函数启动是否有误
    cudaError_t err = cudaGetLastError();
    if (err != cudaSuccess) {
        ERR("Kernel launch failed (rg_sum): " << cudaGetErrorString(err));
        return false;
    }
    
    return true;
}

/**
 * @brief 距离向相位校正 (GPU 异步实现)
 * @param phi_fit CPU 拟合出的相位曲线 (长度为 Nr)
 * @param Na 脉冲数
 * @param Nr 距离门数
 */
bool GMTIProcessor::cuda_apply_rg_correction_async(const std::vector<float>& phi_fit, int Na, int Nr) {
    if (Na <= 0 || Nr <= 0 || phi_fit.size() < static_cast<std::size_t>(Nr) ||
        d_phi_fit_ == nullptr || gpu_ptrs_.t2 == nullptr) {
        return false;
    }

    // 1. 拷贝到专属区域，绝无冲突
    if (cudaMemcpyAsync(d_phi_fit_, phi_fit.data(), Nr * sizeof(float),
                        cudaMemcpyHostToDevice, stream_compute_) != cudaSuccess) {
        return false;
    }

    // 2. 校正必须作用于 DBS 重排后的 t2；rg_correct_CUDA、p38、phase_map
    //    和后续下载都读 t1/t2。校正 a2 会被遗留在上游缓冲区，对 p38 无效。
    int threads = 256;
    int blocks = (Nr + threads - 1) / threads;
    const gmti::trig_lut_device::TrigLutConfig trig_cfg =
        gmtiGetDeviceTrigLutConfig();
    apply_phase_correction_kernel<<<blocks, threads, 0, stream_compute_>>>(
        (cuFloatComplex*)gpu_ptrs_.t2,
        d_phi_fit_, // 使用专属指针
        Na, Nr,
        trig_cfg
    );
    return cudaGetLastError() == cudaSuccess;
}

bool GMTIProcessor::cuda_download_rg_sums_sync(std::vector<std::complex<float>>& h_sums, int Nr) {
    h_sums.resize(Nr);
    
    // 异步拷贝
    cudaMemcpyAsync(h_sums.data(), 
                    gpu_ptrs_.d_rg_sums, 
                    Nr * sizeof(std::complex<float>), 
                    cudaMemcpyDeviceToHost, 
                    stream_compute_);
    
    // 强制同步流，确保 CPU 拿到完整数据后再进行后续的 RANSAC 拟合
    cudaStreamSynchronize(stream_compute_);
    
    return true;
}

bool GMTIProcessor::cuda_capture_phase_map_async(size_t total)
{
    if (total == 0 || gpu_ptrs_.t1 == nullptr || gpu_ptrs_.t2 == nullptr) {
        return false;
    }
    if (d_phase_snapshot_ == nullptr || d_phase_snapshot_elements_ < total) {
        CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
        if (d_phase_snapshot_ != nullptr) {
            CUDA_CHECK(cudaFree(d_phase_snapshot_));
            d_phase_snapshot_ = nullptr;
            d_phase_snapshot_elements_ = 0;
        }
        CUDA_CHECK(cudaMalloc(&d_phase_snapshot_, total * sizeof(float)));
        d_phase_snapshot_elements_ = total;
    }

    const int threads = 256;
    const int blocks = static_cast<int>((total + threads - 1U) / threads);
    const gmti::trig_lut_device::TrigLutConfig trig_cfg =
        gmtiGetDeviceTrigLutConfig();
    phase_map_kernel<<<blocks, threads, 0, stream_compute_>>>(
        static_cast<const cuFloatComplex*>(gpu_ptrs_.t1),
        static_cast<const cuFloatComplex*>(gpu_ptrs_.t2),
        d_phase_snapshot_, total, trig_cfg);
    return cudaGetLastError() == cudaSuccess;
}

bool GMTIProcessor::cuda_download_phase_map(std::vector<float>& phase_map, size_t total)
{
    if (total == 0) return false;
    if (d_phase_snapshot_ == nullptr || d_phase_snapshot_elements_ < total) {
        if (!cuda_capture_phase_map_async(total)) return false;
    }
    phase_map.resize(total);
    CUDA_CHECK(cudaMemcpyAsync(phase_map.data(), d_phase_snapshot_,
                               total * sizeof(float), cudaMemcpyDeviceToHost,
                               stream_compute_));
    CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
    return true;
}

bool GMTIProcessor::cuda_download_phase_samples(
    const std::vector<int>& prow,
    const std::vector<int>& pcol,
    int width,
    std::vector<float>& phase_samples)
{
    phase_samples.clear();
    if (prow.size() != pcol.size() || width <= 0 || d_phase_snapshot_ == nullptr ||
        d_phase_snapshot_elements_ == 0) {
        return false;
    }
    const size_t count = prow.size();
    if (count == 0) return true;
    if (count > static_cast<size_t>(std::numeric_limits<int>::max())) return false;

    const size_t needed_bytes = count * (sizeof(int) + sizeof(float)) + 2U * 256U;
    if (!ensureDetectionWorkspace(needed_bytes)) return false;
    const uintptr_t raw = reinterpret_cast<uintptr_t>(d_detection_workspace_);
    const uintptr_t aligned = (raw + 255U) & ~uintptr_t(255U);
    int* d_indices = reinterpret_cast<int*>(aligned);
    float* d_samples = reinterpret_cast<float*>(
        aligned + count * sizeof(int));

    std::vector<int> indices(count);
    for (size_t i = 0; i < count; ++i) {
        const long long row = static_cast<long long>(prow[i]);
        const long long col = static_cast<long long>(pcol[i]);
        const long long index = row * static_cast<long long>(width) + col;
        indices[i] = (row >= 0 && col >= 0 &&
                      index >= 0 &&
                      index < static_cast<long long>(d_phase_snapshot_elements_))
            ? static_cast<int>(index)
            : -1;
    }
    CUDA_CHECK(cudaMemcpyAsync(d_indices, indices.data(), count * sizeof(int),
                               cudaMemcpyHostToDevice, stream_compute_));
    const int threads = 256;
    const int blocks = static_cast<int>((count + threads - 1U) / threads);
    gather_phase_samples_kernel<<<blocks, threads, 0, stream_compute_>>>(
        d_phase_snapshot_, d_indices, d_samples, static_cast<int>(count),
        static_cast<int>(d_phase_snapshot_elements_));
    CUDA_CHECK(cudaGetLastError());
    phase_samples.resize(count);
    CUDA_CHECK(cudaMemcpyAsync(phase_samples.data(), d_samples,
                               count * sizeof(float), cudaMemcpyDeviceToHost,
                               stream_compute_));
    CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
    return true;
}

bool GMTIProcessor::cuda_download_cfar_maps(std::vector<float>& hits,
                                            std::vector<float>& power,
                                            size_t total)
{
    if (!d_cfar_maps_valid_ || d_cfar_maps_ == nullptr || total == 0U ||
        d_cfar_map_elements_ < total) {
        return false;
    }
    hits.resize(total);
    power.resize(total);
    CUDA_CHECK(cudaMemcpyAsync(hits.data(), d_cfar_maps_,
                               total * sizeof(float), cudaMemcpyDeviceToHost,
                               stream_compute_));
    CUDA_CHECK(cudaMemcpyAsync(power.data(), d_cfar_maps_ + d_cfar_map_elements_,
                               total * sizeof(float), cudaMemcpyDeviceToHost,
                               stream_compute_));
    CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
    return true;
}

bool GMTIProcessor::cuda_download_cfar_power_samples(
    const std::vector<int>& prow,
    const std::vector<int>& pcol,
    int width,
    std::vector<float>& power_samples)
{
    power_samples.clear();
    if (prow.size() != pcol.size() || width <= 0 || !d_cfar_maps_valid_ ||
        d_cfar_maps_ == nullptr || d_cfar_map_elements_ == 0U) {
        return false;
    }
    const size_t count = prow.size();
    if (count == 0U) return true;
    if (count > static_cast<size_t>(std::numeric_limits<int>::max())) return false;

    const size_t needed_bytes = count * (sizeof(int) + sizeof(float)) + 2U * 256U;
    if (!ensureDetectionWorkspace(needed_bytes)) return false;
    const uintptr_t raw = reinterpret_cast<uintptr_t>(d_detection_workspace_);
    const uintptr_t aligned = (raw + 255U) & ~uintptr_t(255U);
    int* d_indices = reinterpret_cast<int*>(aligned);
    float* d_samples = reinterpret_cast<float*>(aligned + count * sizeof(int));

    std::vector<int> indices(count);
    for (size_t i = 0; i < count; ++i) {
        const long long row = static_cast<long long>(prow[i]);
        const long long col = static_cast<long long>(pcol[i]);
        const long long index = row * static_cast<long long>(width) + col;
        indices[i] = row >= 0 && col >= 0 && index >= 0 &&
                     index < static_cast<long long>(d_cfar_map_elements_)
            ? static_cast<int>(index) : -1;
    }
    CUDA_CHECK(cudaMemcpyAsync(d_indices, indices.data(), count * sizeof(int),
                               cudaMemcpyHostToDevice, stream_compute_));
    const int threads = 256;
    const int blocks = static_cast<int>((count + threads - 1U) / threads);
    gather_phase_samples_kernel<<<blocks, threads, 0, stream_compute_>>>(
        d_cfar_maps_ + d_cfar_map_elements_, d_indices, d_samples,
        static_cast<int>(count), static_cast<int>(d_cfar_map_elements_));
    CUDA_CHECK(cudaGetLastError());
    power_samples.resize(count);
    CUDA_CHECK(cudaMemcpyAsync(power_samples.data(), d_samples,
                               count * sizeof(float), cudaMemcpyDeviceToHost,
                               stream_compute_));
    CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
    return true;
}

bool GMTIProcessor::cuda_download_csi_sync(std::vector<std::complex<float>> &out, size_t total)
{
    if (total == 0) return false;
    if (gpu_ptrs_.csi == nullptr) return false;

    out.resize(total);
    CUDA_CHECK(cudaMemcpyAsync(out.data(), gpu_ptrs_.csi, total * sizeof(cudacd),
                               cudaMemcpyDeviceToHost, stream_compute_));
    CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
    return true;
}

bool GMTIProcessor::ensureP38PhaseWorkspace(size_t elements)
{
    if (elements == 0U) return false;
    if (d_p38_phase_ != nullptr && d_p38_phase_elements_ >= elements) {
        return true;
    }
    if (stream_compute_ != nullptr &&
        cudaStreamSynchronize(stream_compute_) != cudaSuccess) {
        return false;
    }
    if (d_p38_phase_ != nullptr) {
        cudaFree(d_p38_phase_);
        d_p38_phase_ = nullptr;
        d_p38_phase_elements_ = 0U;
    }
    const cudaError_t error = cudaMalloc(
        reinterpret_cast<void**>(&d_p38_phase_),
        elements * sizeof(float));
    if (error != cudaSuccess) {
        d_p38_phase_ = nullptr;
        std::cerr << "[CUDA][ERR] P38 phase workspace allocation failed: "
                  << cudaGetErrorString(error) << std::endl;
        return false;
    }
    d_p38_phase_elements_ = elements;
    return true;
}

bool GMTIProcessor::ensureCsiFrequencyWorkspace(size_t elements)
{
    if (elements == 0U) return false;
    if (d_csi_fa_ != nullptr && d_csi_fa_elements_ >= elements) {
        return true;
    }
    if (stream_compute_ != nullptr &&
        cudaStreamSynchronize(stream_compute_) != cudaSuccess) {
        return false;
    }
    if (d_csi_fa_ != nullptr) {
        cudaFree(d_csi_fa_);
        d_csi_fa_ = nullptr;
        d_csi_fa_elements_ = 0U;
    }
    const cudaError_t error = cudaMalloc(
        reinterpret_cast<void**>(&d_csi_fa_),
        elements * sizeof(float));
    if (error != cudaSuccess) {
        d_csi_fa_ = nullptr;
        std::cerr << "[CUDA][ERR] CSI frequency workspace allocation failed: "
                  << cudaGetErrorString(error) << std::endl;
        return false;
    }
    d_csi_fa_elements_ = elements;
    return true;
}

bool GMTIProcessor::ensureCsiRowWorkspace(size_t elements)
{
    if (elements == 0U) return false;
    if (d_csi_alpha_ != nullptr && d_csi_alpha_elements_ >= elements &&
        d_csi_coherence_ != nullptr &&
            d_csi_coherence_elements_ >= elements) {
        return true;
    }
    if (stream_compute_ != nullptr &&
        cudaStreamSynchronize(stream_compute_) != cudaSuccess) {
        return false;
    }
    if (d_csi_alpha_ != nullptr) {
        cudaFree(d_csi_alpha_);
        d_csi_alpha_ = nullptr;
        d_csi_alpha_elements_ = 0U;
    }
    if (d_csi_coherence_ != nullptr) {
        cudaFree(d_csi_coherence_);
        d_csi_coherence_ = nullptr;
        d_csi_coherence_elements_ = 0U;
    }
    cudaError_t error = cudaMalloc(
        reinterpret_cast<void**>(&d_csi_alpha_),
        elements * sizeof(cuFloatComplex));
    if (error == cudaSuccess) {
        error = cudaMalloc(
            reinterpret_cast<void**>(&d_csi_coherence_),
            elements * sizeof(float));
    }
    if (error != cudaSuccess) {
        if (d_csi_alpha_ != nullptr) cudaFree(d_csi_alpha_);
        if (d_csi_coherence_ != nullptr) cudaFree(d_csi_coherence_);
        d_csi_alpha_ = nullptr;
        d_csi_coherence_ = nullptr;
        d_csi_alpha_elements_ = 0U;
        d_csi_coherence_elements_ = 0U;
        std::cerr << "[CUDA][ERR] CSI row workspace allocation failed: "
                  << cudaGetErrorString(error) << std::endl;
        return false;
    }
    d_csi_alpha_elements_ = elements;
    d_csi_coherence_elements_ = elements;
    return true;
}

bool GMTIProcessor::ensureDetectionWorkspace(size_t needed_bytes)
{
    if (needed_bytes == 0) return false;
    if (d_detection_workspace_ != nullptr &&
        d_detection_workspace_bytes_ >= needed_bytes) {
        return true;
    }
    if (stream_compute_ != nullptr) {
        CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
    }
    if (d_detection_workspace_ != nullptr) {
        CUDA_CHECK(cudaFree(d_detection_workspace_));
        d_detection_workspace_ = nullptr;
        d_detection_workspace_bytes_ = 0;
    }
    CUDA_CHECK(cudaMalloc(&d_detection_workspace_, needed_bytes));
    d_detection_workspace_bytes_ = needed_bytes;
    return true;
}

bool GMTIProcessor::ensureCfarMaps(size_t total)
{
    if (total == 0U) return false;
    if (d_cfar_maps_ != nullptr && d_cfar_map_elements_ >= total) {
        return true;
    }
    if (stream_compute_ != nullptr) {
        CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
    }
    if (d_cfar_maps_ != nullptr) {
        CUDA_CHECK(cudaFree(d_cfar_maps_));
        d_cfar_maps_ = nullptr;
        d_cfar_map_elements_ = 0U;
    }
    CUDA_CHECK(cudaMalloc(&d_cfar_maps_, 2U * total * sizeof(float)));
    d_cfar_map_elements_ = total;
    d_cfar_maps_valid_ = false;
    return true;
}

bool GMTIProcessor::dpca_cfar2_fast_cuda(const std::vector<std::complex<float>> &CSI_out,
                                         int band_st, int band_ed,
                                         float pf, int c_num, int b_num, const std::string &type,
                                         const Config &cfg,
                                         std::vector<float> &mydata,
                                         std::vector<float> *power_map,
                                         std::vector<std::complex<float>> *detect_map,
                                         std::size_t *hit_count,
                                         bool download_maps,
                                         int detect_source_mode,
                                         int cut_band_mode,
                                         std::vector<float> *threshold_map)
{
    const int H = effectivePulseNum(cfg);
    const int W = cfg.rg_len;
    if (H <= 0 || W <= 0) return false;

    const size_t total = static_cast<size_t>(H) * static_cast<size_t>(W);
    if (!CSI_out.empty() && CSI_out.size() != total) {
        std::cerr << "dpca_cfar2_cuda: CSI_out size mismatch\n";
        return false;
    }
    if (!(pf > 0.0f && pf < 1.0f)) {
        std::cerr << "dpca_cfar2_cuda: pf should be in (0,1)\n";
        return false;
    }

    const int g = std::max(0, c_num);
    const int b = std::max(1, b_num);
    const int R = g + b;
    const int win = 2 * R + 1;
    const int cut = 2 * g + 1;
    const int total_bg = win * win - cut * cut;
    if (total_bg <= 0) {
        std::cerr << "dpca_cfar2_cuda: invalid background count\n";
        return false;
    }
    if (cfg.cfar_doppler_circular && win > H) {
        std::cerr << "dpca_cfar2_cuda: circular Doppler CFAR window exceeds row count.\n";
        return false;
    }

    std::string ty = type;
    for (auto &ch : ty) ch = static_cast<char>(std::toupper(static_cast<unsigned char>(ch)));
    if (ty != "CA" && ty != "GO" && ty != "OS") ty = "CA";
    if (ty == "OS") {
        std::cerr << "dpca_cfar2_cuda: OS not implemented, fallback to CA.\n";
        ty = "CA";
    }
    const bool use_go = (ty == "GO");

    // GO 是四个相关方向训练均值的最大值，CA 的环均值系数会把实际 Pfa
    // 错配为更保守的值。CPU/CUDA 共用同一 IID 校准器，且按 (pf,g,b) 缓存。
    const float alpha = use_go
        ? static_cast<float>(gmti::go_cfar::calibrated_alpha(pf, g, b))
        : static_cast<float>(total_bg) *
              (std::pow(pf, -1.0f / static_cast<float>(total_bg)) - 1.0f);

    cuFloatComplex* d_csi = nullptr;
    bool owns_csi = false;
    d_cfar_maps_valid_ = false;
    cuFloatComplex* d_detect = nullptr;
    float* d_power = nullptr;
    float* d_row_prefix = nullptr;
    float* d_integral = nullptr;
    float* d_mydata = nullptr;
    float* d_threshold = nullptr;

    if (gpu_ptrs_.csi != nullptr) {
        d_csi = reinterpret_cast<cuFloatComplex*>(gpu_ptrs_.csi);
    } else {
        CUDA_CHECK(cudaMalloc(&d_csi, total * sizeof(cudacd)));
        owns_csi = true;
    }
    const size_t integral_count =
        (static_cast<size_t>(H) + 1) * (static_cast<size_t>(W) + 1);
    if (!ensureCfarMaps(total)) return false;
    d_mydata = d_cfar_maps_;
    d_power = d_cfar_maps_ + d_cfar_map_elements_;
    const size_t detection_needed =
        total * sizeof(cudacd) +
        total * sizeof(float) +
        total * sizeof(float) +
        integral_count * sizeof(float) + 5U * 256U;
    if (!ensureDetectionWorkspace(detection_needed)) return false;
    size_t detection_offset = 0;
    auto take_detection = [&](size_t bytes) -> void* {
        detection_offset = (detection_offset + 255U) & ~size_t(255U);
        char* ptr = static_cast<char*>(d_detection_workspace_) + detection_offset;
        detection_offset += bytes;
        return ptr;
    };
    d_detect = static_cast<cuFloatComplex*>(
        take_detection(total * sizeof(cudacd)));
    d_row_prefix = static_cast<float*>(take_detection(total * sizeof(float)));
    d_integral = static_cast<float*>(
        take_detection(integral_count * sizeof(float)));
    d_threshold = static_cast<float*>(take_detection(total * sizeof(float)));

    if (owns_csi) {
        if (CSI_out.empty()) {
            std::cerr << "dpca_cfar2_cuda: CSI_out is empty and no device CSI buffer is available.\n";
            CUDA_CHECK(cudaFree(d_csi));
            return false;
        }
        CUDA_CHECK(cudaMemcpyAsync(d_csi, CSI_out.data(), total * sizeof(cudacd),
                                   cudaMemcpyHostToDevice, stream_compute_));
    }

    band_st = std::max(0, std::min(band_st, H - 1));
    band_ed = std::max(0, std::min(band_ed, H - 1));

    int threads = 256;
    int blocks = static_cast<int>((total + threads - 1) / threads);
    mix_detect_data_kernel<<<blocks, threads, 0, stream_compute_>>>(
        (const cuFloatComplex*)gpu_ptrs_.t2,
        d_csi,
        d_detect,
        H,
        W,
        band_st,
        band_ed,
        detect_source_mode);
    CUDA_CHECK(cudaGetLastError());

    power_map_kernel<<<blocks, threads, 0, stream_compute_>>>(
        d_detect,
        d_power,
        total);
    CUDA_CHECK(cudaGetLastError());

    CUDA_CHECK(cudaMemsetAsync(d_integral, 0, (static_cast<size_t>(H) + 1) * (static_cast<size_t>(W) + 1) * sizeof(float), stream_compute_));

    int row_blocks = (H + threads - 1) / threads;
    row_prefix_kernel<<<row_blocks, threads, 0, stream_compute_>>>(
        d_power,
        d_row_prefix,
        H,
        W);
    CUDA_CHECK(cudaGetLastError());

    int col_blocks = (W + threads - 1) / threads;
    col_prefix_to_integral_kernel<<<col_blocks, threads, 0, stream_compute_>>>(
        d_row_prefix,
        d_integral,
        H,
        W);
    CUDA_CHECK(cudaGetLastError());

    CUDA_CHECK(cudaMemsetAsync(d_mydata, 0, total * sizeof(float), stream_compute_));

    cfar_detect_kernel<<<blocks, threads, 0, stream_compute_>>>(
        d_detect,
        d_integral,
        d_mydata,
        H,
        W,
        g,
        b,
        total_bg,
        alpha,
        d_threshold,
        use_go,
        cfg.cfar_doppler_circular,
        cfg.cfar_exclude_row_start,
        cfg.cfar_exclude_row_end,
        band_st,
        band_ed,
        cut_band_mode);
    CUDA_CHECK(cudaGetLastError());

    d_cfar_maps_valid_ = true;
    if (hit_count) {
        const auto policy = thrust::cuda::par.on(stream_compute_);
        *hit_count = static_cast<std::size_t>(thrust::count_if(
            policy,
            thrust::device_pointer_cast(d_mydata),
            thrust::device_pointer_cast(d_mydata) + total,
            PositivePower()));
    }
    if (download_maps) {
        mydata.resize(total);
        if (power_map) power_map->resize(total);
    } else {
        mydata.clear();
        if (power_map) power_map->clear();
    }
    if (detect_map) {
        detect_map->resize(total);
    }
    if (threshold_map) {
        if (download_maps) threshold_map->resize(total);
        else threshold_map->clear();
    }
    if (download_maps) {
        CUDA_CHECK(cudaMemcpyAsync(mydata.data(), d_mydata, total * sizeof(float),
                                   cudaMemcpyDeviceToHost, stream_compute_));
        if (power_map) {
            CUDA_CHECK(cudaMemcpyAsync(power_map->data(), d_power, total * sizeof(float),
                                       cudaMemcpyDeviceToHost, stream_compute_));
        }
    }
    if (detect_map) {
        CUDA_CHECK(cudaMemcpyAsync(detect_map->data(), d_detect,
                                   total * sizeof(cudacd),
                                   cudaMemcpyDeviceToHost, stream_compute_));
    }
    if (threshold_map && download_maps) {
        CUDA_CHECK(cudaMemcpyAsync(threshold_map->data(), d_threshold,
                                   total * sizeof(float),
                                   cudaMemcpyDeviceToHost, stream_compute_));
    }
    if (download_maps || detect_map != nullptr || hit_count != nullptr) {
        CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
    }

    if (owns_csi) {
        CUDA_CHECK(cudaFree(d_csi));
    }

    return true;
}

bool GMTIProcessor::cluster_filter_gap_phase_cuda(const std::vector<float> &mydata,
                                                  const std::vector<float> &phase_map,
                                                  int min_points,
                                                  int max_gap,
                                                  float maxPhaseStd,
                                                  const Config &cfg,
                                                  std::vector<float> &refined_data,
                                                  std::vector<int> &prow_new,
                                                  std::vector<int> &pcol_new,
                                                  std::vector<float> &phaseStdList,
                                                  bool download_refined_data,
                                                  bool use_resident_cfar,
                                                  double *global_median_power,
                                                  std::size_t *baseline_kept,
                                                  std::size_t *strong_small_kept,
                                                  std::size_t *small_rejected,
                                                  bool *strong_filter_applied)
{
    const int H = effectivePulseNum(cfg);
    const int W = cfg.rg_len;
    if (H <= 0 || W <= 0) return false;

    const int total = H * W;
    const bool resident_hits = use_resident_cfar && d_cfar_maps_valid_ &&
        d_cfar_maps_ != nullptr && d_cfar_map_elements_ >= static_cast<size_t>(total);
    if (!resident_hits && (int)mydata.size() != total) return false;
    if (!phase_map.empty() && (int)phase_map.size() != total) return false;

    if (global_median_power) {
        *global_median_power = std::numeric_limits<double>::quiet_NaN();
    }
    if (baseline_kept) *baseline_kept = 0U;
    if (strong_small_kept) *strong_small_kept = 0U;
    if (small_rejected) *small_rejected = 0U;
    if (strong_filter_applied) *strong_filter_applied = false;

    if (min_points < 1) min_points = 1;
    if (max_gap < 1) max_gap = 1;

    // phase_map is the zero-shift, range-corrected snapshot captured before
    // the final CSI pass.  Do not regenerate it from the current t1/t2 here:
    // those buffers have already advanced to a different processing stage.
    // The normal GPU path keeps this snapshot on device; a non-empty host
    // vector is retained only for CPU fallback/P38 consumers.
    const bool have_device_phase = d_phase_snapshot_ != nullptr &&
        d_phase_snapshot_elements_ >= static_cast<size_t>(total);
    const bool use_phase = maxPhaseStd > 0.0f &&
        (!phase_map.empty() || have_device_phase);

    float* d_mydata = nullptr;
    float* d_phase = nullptr;
    int* d_labels_a = nullptr;
    int* d_labels_b = nullptr;
    int* d_changed = nullptr;
    int* d_counts = nullptr;
    float* d_sum_cos = nullptr;
    float* d_sum_sin = nullptr;
    float* d_max_power = nullptr;
    int* d_max_idx = nullptr;
    float* d_mean_phi = nullptr;
    float* d_sum_sq = nullptr;
    float* d_phase_std = nullptr;
    float* d_refined = nullptr;
    int* d_candidate_idx = nullptr;
    float* d_candidate_phase = nullptr;
    float* d_candidate_power = nullptr;
    float* d_power_scratch = nullptr;
    int* d_filter_stats = nullptr;

    const size_t map_float_bytes = static_cast<size_t>(total) * sizeof(float);
    const size_t map_int_bytes = static_cast<size_t>(total) * sizeof(int);
    const bool apply_strong_filter = cfg.cluster_strong_small_enable &&
        resident_hits;
    const size_t cluster_needed =
        map_float_bytes * (use_phase ? 12U : 11U) +
        map_int_bytes * 5U + 4U * sizeof(int) + 20U * 256U;
    if (!ensureDetectionWorkspace(cluster_needed)) return false;
    size_t cluster_offset = 0;
    auto take_cluster = [&](size_t bytes) -> void* {
        cluster_offset = (cluster_offset + 255U) & ~size_t(255U);
        char* ptr = static_cast<char*>(d_detection_workspace_) + cluster_offset;
        cluster_offset += bytes;
        return ptr;
    };

    if (resident_hits) {
        d_mydata = d_cfar_maps_;
    } else {
        d_mydata = static_cast<float*>(take_cluster(map_float_bytes));
        CUDA_CHECK(cudaMemcpyAsync(d_mydata, mydata.data(), total * sizeof(float),
                                   cudaMemcpyHostToDevice, stream_compute_));
    }

    if (use_phase) {
        if (!phase_map.empty()) {
            d_phase = static_cast<float*>(take_cluster(map_float_bytes));
            CUDA_CHECK(cudaMemcpyAsync(d_phase, phase_map.data(), total * sizeof(float),
                                       cudaMemcpyHostToDevice, stream_compute_));
        } else {
            d_phase = d_phase_snapshot_;
        }
    }

    d_labels_a = static_cast<int*>(take_cluster(map_int_bytes));
    d_labels_b = static_cast<int*>(take_cluster(map_int_bytes));
    d_changed = static_cast<int*>(take_cluster(sizeof(int)));

    float strong_threshold = 0.0f;
    float median_power = std::numeric_limits<float>::quiet_NaN();
    const auto stream_policy = thrust::cuda::par.on(stream_compute_);
    if (apply_strong_filter) {
        d_power_scratch = static_cast<float*>(take_cluster(map_float_bytes));
        thrust::device_ptr<float> source_begin = thrust::device_pointer_cast(
            d_cfar_maps_ + d_cfar_map_elements_);
        thrust::device_ptr<float> scratch_begin = thrust::device_pointer_cast(
            d_power_scratch);
        const thrust::device_ptr<float> scratch_end = thrust::copy_if(
            stream_policy,
            source_begin, source_begin + total, scratch_begin,
            FiniteNonNegativePower());
        const std::ptrdiff_t finite_count = scratch_end - scratch_begin;
        if (finite_count <= 0) {
            median_power = std::numeric_limits<float>::infinity();
            strong_threshold = std::numeric_limits<float>::infinity();
        } else {
            thrust::sort(stream_policy, scratch_begin, scratch_end);
            CUDA_CHECK(cudaMemcpyAsync(&median_power,
                                       d_power_scratch + finite_count / 2,
                                       sizeof(float), cudaMemcpyDeviceToHost,
                                       stream_compute_));
            CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
            strong_threshold = median_power * powf(
                10.0f,
                static_cast<float>(cfg.cluster_strong_small_peak_over_median_db / 10.0));
        }
        if (global_median_power) *global_median_power = median_power;
        if (strong_filter_applied) *strong_filter_applied = true;
    }

    int threads = 256;
    int blocks = (total + threads - 1) / threads;

    init_labels_kernel<<<blocks, threads, 0, stream_compute_>>>(d_mydata, d_labels_a, total);
    CUDA_CHECK(cudaGetLastError());

    int max_iters = H + W;
    for (int it = 0; it < max_iters; ++it) {
        CUDA_CHECK(cudaMemsetAsync(d_changed, 0, sizeof(int), stream_compute_));
        propagate_labels_kernel<<<blocks, threads, 0, stream_compute_>>>(
            d_labels_a, d_labels_b, H, W, max_gap,
            cfg.cfar_doppler_circular, d_changed);
        CUDA_CHECK(cudaGetLastError());

        int h_changed = 0;
        CUDA_CHECK(cudaMemcpyAsync(&h_changed, d_changed, sizeof(int),
                                   cudaMemcpyDeviceToHost, stream_compute_));
        CUDA_CHECK(cudaStreamSynchronize(stream_compute_));

        std::swap(d_labels_a, d_labels_b);
        if (!h_changed) break;
    }

    d_counts = static_cast<int*>(take_cluster(map_int_bytes));
    d_sum_cos = static_cast<float*>(take_cluster(map_float_bytes));
    d_sum_sin = static_cast<float*>(take_cluster(map_float_bytes));
    d_max_power = static_cast<float*>(take_cluster(map_float_bytes));
    d_max_idx = static_cast<int*>(take_cluster(map_int_bytes));
    d_mean_phi = static_cast<float*>(take_cluster(map_float_bytes));
    d_sum_sq = static_cast<float*>(take_cluster(map_float_bytes));
    d_phase_std = static_cast<float*>(take_cluster(map_float_bytes));

    CUDA_CHECK(cudaMemsetAsync(d_counts, 0, total * sizeof(int), stream_compute_));
    CUDA_CHECK(cudaMemsetAsync(d_sum_cos, 0, total * sizeof(float), stream_compute_));
    CUDA_CHECK(cudaMemsetAsync(d_sum_sin, 0, total * sizeof(float), stream_compute_));
    CUDA_CHECK(cudaMemsetAsync(d_sum_sq, 0, total * sizeof(float), stream_compute_));
    CUDA_CHECK(cudaMemsetAsync(d_max_power, 0, total * sizeof(float), stream_compute_));
    CUDA_CHECK(cudaMemsetAsync(d_max_idx, 0xff, total * sizeof(int), stream_compute_));

    const gmti::trig_lut_device::TrigLutConfig trig_cfg =
        gmtiGetDeviceTrigLutConfig();
    accumulate_stats_kernel<<<blocks, threads, 0, stream_compute_>>>(
        d_mydata,
        use_phase ? d_phase : d_mydata,
        d_labels_a,
        total,
        d_counts,
        d_sum_cos,
        d_sum_sin,
        d_max_power,
        d_max_idx,
        use_phase,
        trig_cfg);
    CUDA_CHECK(cudaGetLastError());

    if (use_phase) {
        compute_mean_phase_kernel<<<blocks, threads, 0, stream_compute_>>>(
            d_counts, d_sum_cos, d_sum_sin, d_mean_phi, total, trig_cfg);
        CUDA_CHECK(cudaGetLastError());

        accumulate_phase_var_kernel<<<blocks, threads, 0, stream_compute_>>>(
            d_phase, d_labels_a, d_mean_phi, total, d_sum_sq);
        CUDA_CHECK(cudaGetLastError());

        compute_phase_std_kernel<<<blocks, threads, 0, stream_compute_>>>(
            d_counts, d_sum_sq, d_phase_std, total);
        CUDA_CHECK(cudaGetLastError());
    } else {
        CUDA_CHECK(cudaMemsetAsync(d_phase_std, 0, total * sizeof(float), stream_compute_));
    }

    d_refined = static_cast<float*>(take_cluster(map_float_bytes));
    d_candidate_idx = static_cast<int*>(take_cluster(map_int_bytes));
    d_candidate_phase = static_cast<float*>(take_cluster(map_float_bytes));
    d_candidate_power = static_cast<float*>(take_cluster(map_float_bytes));
    d_filter_stats = static_cast<int*>(take_cluster(3U * sizeof(int)));
    CUDA_CHECK(cudaMemsetAsync(d_filter_stats, 0, 3U * sizeof(int), stream_compute_));
    build_cluster_outputs_kernel<<<blocks, threads, 0, stream_compute_>>>(
        d_mydata, d_labels_a, d_counts, d_max_power, d_max_idx, d_phase_std,
        total, min_points, cfg.min_points, maxPhaseStd, use_phase,
        apply_strong_filter, strong_threshold,
        d_refined, d_candidate_idx, d_candidate_phase, d_candidate_power,
        d_filter_stats);
    CUDA_CHECK(cudaGetLastError());
    thrust::device_ptr<int> candidate_begin =
        thrust::device_pointer_cast(d_candidate_idx);
    // The label/stat arrays are dead after build_cluster_outputs_kernel.  Reuse
    // them as compact candidate storage instead of cudaMalloc'ing three
    // thrust::device_vector objects for every beam.
    int* d_compact_idx = d_labels_b;
    float* d_compact_phase = d_sum_cos;
    float* d_compact_power = d_sum_sin;
    auto input_begin = thrust::make_zip_iterator(thrust::make_tuple(
        candidate_begin, thrust::device_pointer_cast(d_candidate_phase),
        thrust::device_pointer_cast(d_candidate_power)));
    auto output_begin = thrust::make_zip_iterator(thrust::make_tuple(
        thrust::device_pointer_cast(d_compact_idx),
        thrust::device_pointer_cast(d_compact_phase),
        thrust::device_pointer_cast(d_compact_power)));
    const auto output_end = thrust::copy_if(
        stream_policy, input_begin, input_begin + total, candidate_begin,
        output_begin, NonNegativeIndex());
    const int valid_count = static_cast<int>(output_end - output_begin);

    if (download_refined_data) {
        refined_data.resize(static_cast<size_t>(total));
    } else {
        refined_data.clear();
    }
    std::vector<int> compact_idx_host(static_cast<size_t>(valid_count));
    std::vector<float> compact_phase_host(static_cast<size_t>(valid_count));
    std::vector<float> compact_power_host(static_cast<size_t>(valid_count));
    if (download_refined_data) {
        CUDA_CHECK(cudaMemcpyAsync(refined_data.data(), d_refined,
                                   total * sizeof(float), cudaMemcpyDeviceToHost,
                                   stream_compute_));
    }
    if (valid_count > 0) {
        CUDA_CHECK(cudaMemcpyAsync(compact_idx_host.data(),
                                   d_compact_idx,
                                   static_cast<size_t>(valid_count) * sizeof(int),
                                   cudaMemcpyDeviceToHost, stream_compute_));
        CUDA_CHECK(cudaMemcpyAsync(compact_phase_host.data(),
                                   d_compact_phase,
                                   static_cast<size_t>(valid_count) * sizeof(float),
                                   cudaMemcpyDeviceToHost, stream_compute_));
        CUDA_CHECK(cudaMemcpyAsync(compact_power_host.data(),
                                   d_compact_power,
                                   static_cast<size_t>(valid_count) * sizeof(float),
                                   cudaMemcpyDeviceToHost, stream_compute_));
    }
    int host_filter_stats[3] = {0, 0, 0};
    if (apply_strong_filter) {
        CUDA_CHECK(cudaMemcpyAsync(host_filter_stats, d_filter_stats,
                                   sizeof(host_filter_stats), cudaMemcpyDeviceToHost,
                                   stream_compute_));
    }
    CUDA_CHECK(cudaStreamSynchronize(stream_compute_));

    prow_new.clear();
    pcol_new.clear();
    phaseStdList.clear();
    prow_new.reserve(static_cast<size_t>(valid_count));
    pcol_new.reserve(static_cast<size_t>(valid_count));
    phaseStdList.reserve(static_cast<size_t>(valid_count));
    std::vector<int> best_for_col;
    if (apply_strong_filter) best_for_col.assign(static_cast<size_t>(W), -1);
    for (int i = 0; i < valid_count; ++i) {
        const int best = compact_idx_host[static_cast<size_t>(i)];
        const int r = best / W;
        const int c = best - r * W;
        if (apply_strong_filter && c >= 0 && c < W) {
            const int previous = best_for_col[static_cast<size_t>(c)];
            if (previous >= 0 &&
                compact_power_host[static_cast<size_t>(previous)] >=
                    compact_power_host[static_cast<size_t>(i)]) {
                continue;
            }
            best_for_col[static_cast<size_t>(c)] = i;
        }
    }
    for (int i = 0; i < valid_count; ++i) {
        const int best = compact_idx_host[static_cast<size_t>(i)];
        const int r = best / W;
        const int c = best - r * W;
        if (apply_strong_filter && c >= 0 && c < W &&
            best_for_col[static_cast<size_t>(c)] != i) {
            continue;
        }
        prow_new.push_back(r);
        pcol_new.push_back(c);
        if (use_phase) {
            phaseStdList.push_back(compact_phase_host[static_cast<size_t>(i)]);
        }
    }

    if (apply_strong_filter) {
        if (baseline_kept) {
            *baseline_kept = static_cast<size_t>(std::max(0, host_filter_stats[0]));
        }
        if (strong_small_kept) {
            *strong_small_kept = static_cast<size_t>(std::max(0, host_filter_stats[1]));
        }
        if (small_rejected) {
            *small_rejected = static_cast<size_t>(std::max(0, host_filter_stats[2]));
        }
    } else if (baseline_kept) {
        *baseline_kept = prow_new.size();
    }

    return true;
}

bool GMTIProcessor::target_select_cuda(const std::vector<int> &prow,
                                       const std::vector<int> &pcol,
                                       const Config &cfg,
                                       GMTIOutput::Detect &S_target)
{
    const int H = effectivePulseNum(cfg);
    const int W = cfg.rg_len;
    if (H <= 0 || W <= 0) return false;
    if (prow.size() != pcol.size()) return false;
    if (gpu_ptrs_.t1 == nullptr) return false;

    const int n = static_cast<int>(prow.size());
    S_target.prow.clear();
    S_target.pcol.clear();
    if (n == 0) return true;

    const size_t needed_bytes = static_cast<size_t>(n) *
        (2U * sizeof(int) + sizeof(float)) + 3U * 256U;
    if (!ensureDetectionWorkspace(needed_bytes)) return false;
    size_t offset = 0U;
    auto take = [&](size_t bytes) -> void* {
        offset = (offset + 255U) & ~size_t(255U);
        char* ptr = static_cast<char*>(d_detection_workspace_) + offset;
        offset += bytes;
        return ptr;
    };
    int* d_prow = static_cast<int*>(take(static_cast<size_t>(n) * sizeof(int)));
    int* d_pcol = static_cast<int*>(take(static_cast<size_t>(n) * sizeof(int)));
    float* d_amp = static_cast<float*>(take(static_cast<size_t>(n) * sizeof(float)));
    CUDA_CHECK(cudaMemcpyAsync(d_prow, prow.data(), static_cast<size_t>(n) * sizeof(int),
                               cudaMemcpyHostToDevice, stream_compute_));
    CUDA_CHECK(cudaMemcpyAsync(d_pcol, pcol.data(), static_cast<size_t>(n) * sizeof(int),
                               cudaMemcpyHostToDevice, stream_compute_));

    int threads = 256;
    int blocks = (n + threads - 1) / threads;
    compute_amp_kernel<<<blocks, threads, 0, stream_compute_>>>(
        (const cuFloatComplex*)gpu_ptrs_.t1,
        d_prow,
        d_pcol,
        d_amp,
        n,
        H,
        W);
    CUDA_CHECK(cudaGetLastError());

    const auto policy = thrust::cuda::par.on(stream_compute_);
    auto amp_begin = thrust::device_pointer_cast(d_amp);
    auto zip_begin = thrust::make_zip_iterator(thrust::make_tuple(
        thrust::device_pointer_cast(d_prow), thrust::device_pointer_cast(d_pcol)));
    thrust::sort_by_key(policy, amp_begin, amp_begin + n, zip_begin,
                        thrust::greater<float>());

    const auto valid_count = thrust::count_if(
        policy, amp_begin, amp_begin + n, NonNegativeAmp());

    if (valid_count == 0) return true;

    S_target.prow.resize(valid_count);
    S_target.pcol.resize(valid_count);
    CUDA_CHECK(cudaMemcpyAsync(S_target.prow.data(), d_prow,
                               static_cast<size_t>(valid_count) * sizeof(int),
                               cudaMemcpyDeviceToHost, stream_compute_));
    CUDA_CHECK(cudaMemcpyAsync(S_target.pcol.data(), d_pcol,
                               static_cast<size_t>(valid_count) * sizeof(int),
                               cudaMemcpyDeviceToHost, stream_compute_));
    CUDA_CHECK(cudaStreamSynchronize(stream_compute_));

    return true;
}

static gmti::p38::PhaseFitOptions p38_fit_options_cuda(
    const Config& cfg, std::size_t support_size) {
    gmti::p38::PhaseFitOptions options;
    const std::size_t configured_min = static_cast<std::size_t>(
        std::max(2, cfg.p38_refit_min_sample_count));
    options.min_sample_count = std::min(support_size, configured_min);
    options.min_inlier_ratio = gmti::p38::detail::clampUnit(
        cfg.p38_refit_min_inlier_ratio);
    options.max_rmse_rad = gmti::p38::detail::maxDouble(
        0.0, cfg.p38_refit_max_rmse_rad);
    options.min_peak_relative_energy = gmti::p38::detail::maxDouble(
        0.0, cfg.p38_min_peak_row_energy_fraction);
    return options;
}

bool GMTIProcessor::clutter_cancel_38_paper_1_p38_cuda(
    const std::vector<float>& y_faAxis,
    int az_st, int rg_st, int az_ed, int rg_ed,
    const Config& cfg,
    std::array<float,2>& p_38,
    std::vector<float>* phase_tra_38,
    std::string* fit_model_source)
{
    const size_t Na = static_cast<size_t>(effectivePulseNum(cfg));
    const size_t Nr = static_cast<size_t>(cfg.rg_len);

    if (y_faAxis.size() != Na) return false;

    az_st = std::max(0, std::min(az_st, int(Na) - 1));
    az_ed = std::max(0, std::min(az_ed, int(Na) - 1));
    rg_st = std::max(0, std::min(rg_st, int(Nr) - 1));
    rg_ed = std::max(0, std::min(rg_ed, int(Nr) - 1));
    if (az_st > az_ed) return false;
    if (rg_st > rg_ed) return false;

    if (!initcuFFTPlans(cfg)) return false;

    if (!cfg.p38_enhanced_enable) {
        if (!ensureP38PhaseWorkspace(Na)) return false;
        float* d_phase = d_p38_phase_;
        cudaError_t error = cudaMemsetAsync(
            d_phase, 0, Na * sizeof(float), stream_compute_);
        const int threads = 256;
        const int blocks = (static_cast<int>(Na) + threads - 1) / threads;
        if (error == cudaSuccess) {
            compute_row_phase_legacy_kernel<<<
                blocks, threads, 0, stream_compute_>>>(
                (const cuFloatComplex*)gpu_ptrs_.t1,
                (const cuFloatComplex*)gpu_ptrs_.t2,
                d_phase,
                static_cast<int>(Na),
                static_cast<int>(Nr),
                az_st,
                az_ed,
                gmtiGetDeviceTrigLutConfig());
            error = cudaGetLastError();
        }
        std::vector<float> phase_all(Na, 0.0f);
        if (error == cudaSuccess) {
            error = cudaMemcpyAsync(
                phase_all.data(), d_phase, Na * sizeof(float),
                cudaMemcpyDeviceToHost, stream_compute_);
        }
        if (error == cudaSuccess) {
            error = cudaStreamSynchronize(stream_compute_);
        }
        if (error != cudaSuccess) {
            ERR("legacy p38 phase calculation failed: "
                << cudaGetErrorString(error));
            return false;
        }

        const int M = az_ed - az_st + 1;
        std::vector<double> x(static_cast<std::size_t>(M), 0.0);
        std::vector<double> y(static_cast<std::size_t>(M), 0.0);
        for (int i = 0; i < M; ++i) {
            x[static_cast<std::size_t>(i)] =
                static_cast<double>(y_faAxis[static_cast<std::size_t>(az_st + i)]);
            y[static_cast<std::size_t>(i)] =
                static_cast<double>(phase_all[static_cast<std::size_t>(az_st + i)]);
        }
        for (int i = 1; i < M; ++i) {
            double delta = y[static_cast<std::size_t>(i)] -
                           y[static_cast<std::size_t>(i - 1)];
            while (delta > M_PI) {
                y[static_cast<std::size_t>(i)] -= 2.0 * M_PI;
                delta -= 2.0 * M_PI;
            }
            while (delta < -M_PI) {
                y[static_cast<std::size_t>(i)] += 2.0 * M_PI;
                delta += 2.0 * M_PI;
            }
        }

        double sx = 0.0;
        double sy = 0.0;
        double sxx = 0.0;
        double sxy = 0.0;
        for (int i = 0; i < M; ++i) {
            const double xi = x[static_cast<std::size_t>(i)];
            const double yi = y[static_cast<std::size_t>(i)];
            sx += xi;
            sy += yi;
            sxx += xi * xi;
            sxy += xi * yi;
        }
        const double det = static_cast<double>(M) * sxx - sx * sx;
        if (M < 2 || !std::isfinite(det) || std::abs(det) < 1.0e-12) {
            return false;
        }
        const double k =
            (static_cast<double>(M) * sxy - sx * sy) / det;
        const double b = (sy * sxx - sx * sxy) / det;
        if (!std::isfinite(k) || !std::isfinite(b)) return false;
        p_38 = {static_cast<float>(k), static_cast<float>(b)};
        if (phase_tra_38 != nullptr) {
            phase_tra_38->resize(static_cast<std::size_t>(M));
            for (int i = 0; i < M; ++i) {
                (*phase_tra_38)[static_cast<std::size_t>(i)] =
                    static_cast<float>(y[static_cast<std::size_t>(i)]);
            }
        }
        if (fit_model_source != nullptr) {
            *fit_model_source = "legacy_linear";
        }
        return true;
    }

    void* d_statistics = nullptr;
    // 国产GPU设备核不使用double：设备侧统计量均为float，下载到主机后
    // 再转换为double供稳健P38拟合使用。
    const std::size_t statistics_bytes =
        Na * sizeof(float2) + Na * sizeof(float);
    cudaError_t error = cudaMalloc(&d_statistics, statistics_bytes);
    if (error != cudaSuccess) {
        ERR("cudaMalloc failed for p38 row statistics: " << cudaGetErrorString(error));
        return false;
    }
    float2* d_cross = static_cast<float2*>(d_statistics);
    float* d_energy = reinterpret_cast<float*>(d_cross + Na);
    error = cudaMemsetAsync(d_statistics, 0, statistics_bytes, stream_compute_);
    if (error != cudaSuccess) {
        cudaFree(d_statistics);
        ERR("cudaMemsetAsync failed for p38 row statistics: " << cudaGetErrorString(error));
        return false;
    }

    int threads = 256;
    int blocks = (static_cast<int>(Na) + threads - 1) / threads;
    compute_row_statistics_kernel<<<blocks, threads, 0, stream_compute_>>>(
        (const cuFloatComplex*)gpu_ptrs_.t1,
        (const cuFloatComplex*)gpu_ptrs_.t2,
        d_cross,
        d_energy,
        (int)Na,
        (int)Nr,
        az_st,
        rg_st,
        az_ed,
        rg_ed,
        nullptr);
    error = cudaGetLastError();
    if (error != cudaSuccess) {
        cudaFree(d_statistics);
        ERR("compute_row_statistics_kernel launch failed: " << cudaGetErrorString(error));
        return false;
    }

    std::vector<float2> row_cross_all(Na);
    std::vector<float> row_energy_all(Na, 0.0f);
    error = cudaMemcpyAsync(row_cross_all.data(), d_cross, Na * sizeof(float2),
                            cudaMemcpyDeviceToHost, stream_compute_);
    if (error == cudaSuccess) {
        error = cudaMemcpyAsync(row_energy_all.data(), d_energy, Na * sizeof(float),
                                cudaMemcpyDeviceToHost, stream_compute_);
    }
    if (error == cudaSuccess) {
        error = cudaStreamSynchronize(stream_compute_);
    }
    cudaFree(d_statistics);
    if (error != cudaSuccess) {
        ERR("p38 row-statistics download failed: " << cudaGetErrorString(error));
        return false;
    }

    const int M = az_ed - az_st + 1;
    std::vector<double> row_fa(static_cast<std::size_t>(M), 0.0);
    std::vector<std::complex<double>> row_cross(
        static_cast<std::size_t>(M), std::complex<double>(0.0, 0.0));
    std::vector<double> row_energy(static_cast<std::size_t>(M), 0.0);
    for (int i = 0; i < M; ++i) {
        const std::size_t source_index = static_cast<std::size_t>(az_st + i);
        const std::size_t support_index = static_cast<std::size_t>(i);
        row_fa[support_index] = static_cast<double>(y_faAxis[source_index]);
        row_cross[support_index] = std::complex<double>(
            static_cast<double>(row_cross_all[source_index].x),
            static_cast<double>(row_cross_all[source_index].y));
        row_energy[support_index] =
            static_cast<double>(row_energy_all[source_index]);
    }

    const gmti::p38::PhaseFitOptions fit_options =
        p38_fit_options_cuda(cfg, static_cast<std::size_t>(M));
    const gmti::p38::PhaseFitResult legacy_compatibility =
        gmti::p38::fitPhaseSlopeLegacy(
            row_fa, row_cross, row_energy);
    gmti::p38::PhaseFitResult fit = gmti::p38::fitPhaseSlopeMad(
        row_fa, row_cross, row_energy, std::vector<double>(), fit_options);
    if (phase_tra_38 != nullptr) {
        phase_tra_38->assign(
            static_cast<std::size_t>(M), std::numeric_limits<float>::quiet_NaN());
        for (std::size_t i = 0; i < fit.samples.size(); ++i) {
            if (std::isfinite(fit.samples[i].phase)) {
                (*phase_tra_38)[i] = static_cast<float>(fit.samples[i].phase);
            }
        }
    }
    DBG("[p38][cuda] support=" << M
        << " accepted=" << fit.sample_count
        << " inlier_ratio=" << fit.inlier_ratio
        << " rmse_rad=" << fit.rmse
        << " k=" << fit.k
        << " b=" << fit.b
        << " valid=" << (fit.valid ? 1 : 0));

    std::string selected_model_source = "observed";
    const auto try_theory_guided_fit = [&](const char* stage) -> bool {
        const gmti::p38::TheoryGuidedFitDecision decision =
            gmti::p38::selectPhaseFitWithTheoryFallback(
                row_fa, row_cross, row_energy, fit,
                cfg.p38_theory_guided_fallback,
                cfg.p38_expected_slope_rad_per_hz,
                cfg.p38_theory_prior_relative_span,
                cfg.p38_theory_prior_trigger_relative_error,
                std::vector<double>(),
                p38_fit_options_cuda(cfg, static_cast<std::size_t>(M)));
        fit = decision.selected;
        selected_model_source =
            gmti::p38::phaseFitModelSourceName(decision.source);
        if (decision.attempted && cfg.runtime_diagnostics_enabled) {
            std::cout << "[p38][cuda] theory-policy " << stage
                      << ": expected_k=" << cfg.p38_expected_slope_rad_per_hz
                      << " observed_relative_error="
                      << decision.observed_relative_error
                      << " peak_k=" << decision.peak_observed.k
                      << " peak_relative_error=" << decision.peak_relative_error
                      << " guided_k=" << decision.theory_guided.k
                      << " guided_relative_error=" << decision.guided_relative_error
                      << " fixed_k=" << decision.theory_fixed_slope.k
                      << " fixed_rmse_rad=" << decision.theory_fixed_slope.rmse
                      << " fixed_valid="
                      << (decision.theory_fixed_slope.valid ? 1 : 0)
                      << " selected_source=" << selected_model_source
                      << " selected_k=" << fit.k
                      << " selected_rmse_rad=" << fit.rmse
                      << " selected_valid=" << (fit.valid ? 1 : 0)
                      << std::endl;
        }
        return fit.valid;
    };

    if (cfg.p38_enhanced_enable) {
        try_theory_guided_fit("primary");
    }

    if (cfg.p38_enhanced_enable && !fit.valid &&
        legacy_compatibility.valid) {
        fit = legacy_compatibility;
        selected_model_source = "legacy_compat_fallback";
        if (cfg.runtime_diagnostics_enabled) {
            std::cout << "[p38][cuda] MAD/theory invalid; using legacy compatibility "
                         "fallback k=" << fit.k
                      << " rmse_rad=" << fit.rmse << std::endl;
        }
    }

    // Before CFAR is available, a very strong moving target can dominate a
    // range cell (and its LFM sidelobes), leaving too few background rows for
    // p38.  Retry only after the ordinary fit fails: build a robust range mask
    // from coherent cross sums, dilate the exceptional cells, and fit the
    // remaining SAR background.  Normal scenes keep the original path.
    if (cfg.p38_enhanced_enable && !fit.valid) {
        if (cfg.runtime_diagnostics_enabled) {
            std::cout << "[p38][cuda] primary fit invalid: support=" << M
                      << " accepted=" << fit.sample_count
                      << " inlier_ratio=" << fit.inlier_ratio
                      << " rmse_rad=" << fit.rmse
                      << "; trying strong-range-mask fallback" << std::endl;
        }

        Config mask_cfg = cfg;
        mask_cfg.az_st = az_st;
        mask_cfg.az_ed = az_ed;
        mask_cfg.rg_st = rg_st;
        mask_cfg.rg_ed = rg_ed;
        std::vector<std::complex<float>> range_cross_sum;
        if (!cuda_stage_rg_sum_async(mask_cfg) ||
            !cuda_download_rg_sums_sync(range_cross_sum, static_cast<int>(Nr))) {
            ERR("[p38][cuda] failed to obtain fallback range statistics");
            return false;
        }

        std::vector<double> magnitudes;
        magnitudes.reserve(static_cast<std::size_t>(rg_ed - rg_st + 1));
        for (int c = rg_st; c <= rg_ed; ++c) {
            const double value = std::abs(range_cross_sum[static_cast<std::size_t>(c)]);
            if (std::isfinite(value) && value > 0.0) magnitudes.push_back(value);
        }
        if (magnitudes.size() < 8U) {
            ERR("[p38][cuda] too few finite range statistics for fallback");
            return false;
        }
        const std::size_t middle = magnitudes.size() / 2U;
        std::nth_element(magnitudes.begin(), magnitudes.begin() + middle, magnitudes.end());
        const double median_magnitude = magnitudes[middle];
        const double strong_threshold = gmti::p38::detail::maxDouble(
            1.0e-12, 10.0 * median_magnitude);
        const int dilation_bins = 32;
        std::vector<uint8_t> range_mask(Nr, 0U);
        for (int c = rg_st; c <= rg_ed; ++c) {
            const double value = std::abs(range_cross_sum[static_cast<std::size_t>(c)]);
            if (!(std::isfinite(value) && value > strong_threshold)) continue;
            const int lo = std::max(rg_st, c - dilation_bins);
            const int hi = std::min(rg_ed, c + dilation_bins);
            for (int j = lo; j <= hi; ++j) {
                range_mask[static_cast<std::size_t>(j)] = 1U;
            }
        }
        const std::size_t masked_bins = static_cast<std::size_t>(std::count(
            range_mask.begin() + rg_st, range_mask.begin() + rg_ed + 1,
            static_cast<uint8_t>(1U)));
        const std::size_t support_bins = static_cast<std::size_t>(rg_ed - rg_st + 1);
        if (masked_bins == 0U || masked_bins + 8U >= support_bins) {
            if (cfg.runtime_diagnostics_enabled) {
                std::cout << "[p38][cuda] unusable fallback range mask: masked="
                          << masked_bins << " support=" << support_bins << std::endl;
            }
            return false;
        }

        uint8_t* d_range_mask = nullptr;
        void* d_fallback_statistics = nullptr;
        error = cudaMalloc(reinterpret_cast<void**>(&d_range_mask), Nr * sizeof(uint8_t));
        if (error == cudaSuccess) {
            error = cudaMalloc(&d_fallback_statistics, statistics_bytes);
        }
        if (error != cudaSuccess) {
            if (d_range_mask != nullptr) cudaFree(d_range_mask);
            if (d_fallback_statistics != nullptr) cudaFree(d_fallback_statistics);
            ERR("[p38][cuda] fallback allocation failed: " << cudaGetErrorString(error));
            return false;
        }
        float2* d_fallback_cross = static_cast<float2*>(d_fallback_statistics);
        float* d_fallback_energy = reinterpret_cast<float*>(d_fallback_cross + Na);
        error = cudaMemcpyAsync(d_range_mask, range_mask.data(), Nr * sizeof(uint8_t),
                                cudaMemcpyHostToDevice, stream_compute_);
        if (error == cudaSuccess) {
            error = cudaMemsetAsync(d_fallback_statistics, 0, statistics_bytes,
                                    stream_compute_);
        }
        if (error == cudaSuccess) {
            compute_row_statistics_kernel<<<blocks, threads, 0, stream_compute_>>>(
                (const cuFloatComplex*)gpu_ptrs_.t1,
                (const cuFloatComplex*)gpu_ptrs_.t2,
                d_fallback_cross,
                d_fallback_energy,
                static_cast<int>(Na),
                static_cast<int>(Nr),
                az_st,
                rg_st,
                az_ed,
                rg_ed,
                d_range_mask);
            error = cudaGetLastError();
        }
        if (error == cudaSuccess) {
            error = cudaMemcpyAsync(row_cross_all.data(), d_fallback_cross,
                                    Na * sizeof(float2), cudaMemcpyDeviceToHost,
                                    stream_compute_);
        }
        if (error == cudaSuccess) {
            error = cudaMemcpyAsync(row_energy_all.data(), d_fallback_energy,
                                    Na * sizeof(float), cudaMemcpyDeviceToHost,
                                    stream_compute_);
        }
        if (error == cudaSuccess) error = cudaStreamSynchronize(stream_compute_);
        cudaFree(d_range_mask);
        cudaFree(d_fallback_statistics);
        if (error != cudaSuccess) {
            ERR("[p38][cuda] fallback statistics failed: " << cudaGetErrorString(error));
            return false;
        }

        for (int i = 0; i < M; ++i) {
            const std::size_t source_index = static_cast<std::size_t>(az_st + i);
            const std::size_t support_index = static_cast<std::size_t>(i);
            row_cross[support_index] = std::complex<double>(
                static_cast<double>(row_cross_all[source_index].x),
                static_cast<double>(row_cross_all[source_index].y));
            row_energy[support_index] =
                static_cast<double>(row_energy_all[source_index]);
        }
        gmti::p38::PhaseFitOptions fallback_options =
            p38_fit_options_cuda(cfg, static_cast<std::size_t>(M));
        fallback_options.min_peak_relative_energy = 0.0;
        fit = gmti::p38::fitPhaseSlopeMad(
            row_fa, row_cross, row_energy, std::vector<double>(), fallback_options);
        if (cfg.runtime_diagnostics_enabled) {
            std::cout << "[p38][cuda] fallback result: masked_bins=" << masked_bins
                      << " median_range_cross=" << median_magnitude
                      << " threshold=" << strong_threshold
                      << " accepted=" << fit.sample_count
                      << " inlier_ratio=" << fit.inlier_ratio
                      << " rmse_rad=" << fit.rmse
                      << " k=" << fit.k
                      << " b=" << fit.b
                      << " valid=" << (fit.valid ? 1 : 0) << std::endl;
        }
        try_theory_guided_fit("strong-range-mask");
        if (!fit.valid) {
            const gmti::p38::PhaseFitResult masked_legacy =
                gmti::p38::fitPhaseSlopeLegacy(
                    row_fa, row_cross, row_energy);
            if (masked_legacy.valid) {
                fit = masked_legacy;
                selected_model_source = "legacy_compat_fallback_masked";
            }
        }
        if (!fit.valid) return false;

        if (phase_tra_38 != nullptr) {
            phase_tra_38->assign(
                static_cast<std::size_t>(M), std::numeric_limits<float>::quiet_NaN());
            for (std::size_t i = 0; i < fit.samples.size(); ++i) {
                if (std::isfinite(fit.samples[i].phase)) {
                    (*phase_tra_38)[i] = static_cast<float>(fit.samples[i].phase);
                }
            }
        }
    }
    if (!fit.valid) {
        return false;
    }
    if (phase_tra_38 != nullptr) {
        phase_tra_38->assign(
            static_cast<std::size_t>(M), std::numeric_limits<float>::quiet_NaN());
        for (std::size_t i = 0; i < fit.samples.size(); ++i) {
            if (std::isfinite(fit.samples[i].phase)) {
                (*phase_tra_38)[i] = static_cast<float>(fit.samples[i].phase);
            }
        }
    }
    if (fit_model_source != nullptr) {
        *fit_model_source = selected_model_source;
    }
    p_38 = {static_cast<float>(fit.k), static_cast<float>(fit.b)};
    return true;
}

bool GMTIProcessor::clutter_cancel_38_paper_1_cuda(
    const std::vector<float>& y_faAxis,
    int az_st, int rg_st, int az_ed, int rg_ed,
    const Config& cfg,
    std::vector<std::complex<float>>& prosig_38,
    std::array<float,2>& p_38,
    std::vector<float>& phase_tra_38_cut,
    std::vector<float>& row_fa_cut)
{
    const size_t Na = static_cast<size_t>(effectivePulseNum(cfg));
    const size_t Nr = static_cast<size_t>(cfg.rg_len);
    const size_t total = Na * Nr;

    if (y_faAxis.size() != Na) return false;

    az_st = std::max(0, std::min(az_st, int(Na) - 1));
    az_ed = std::max(0, std::min(az_ed, int(Na) - 1));
    if (az_st > az_ed) return false;

    const int M = az_ed - az_st + 1;
    std::vector<float> phase_tra_38;
    if (std::isfinite(cfg.p38_csi_override_k_rad_per_hz)) {
        p_38 = {static_cast<float>(cfg.p38_csi_override_k_rad_per_hz),
                static_cast<float>(cfg.p38_csi_override_b_rad)};
        phase_tra_38.resize(static_cast<std::size_t>(M));
        for (int i = 0; i < M; ++i) {
            phase_tra_38[static_cast<std::size_t>(i)] =
                p_38[0] * y_faAxis[static_cast<std::size_t>(az_st + i)] + p_38[1];
        }
        if (cfg.runtime_diagnostics_enabled) {
            std::cout << "[p38][cuda][paired-override] k=" << p_38[0]
                      << " b=" << p_38[1] << std::endl;
        }
    } else if (!clutter_cancel_38_paper_1_p38_cuda(
                   y_faAxis, az_st, rg_st, az_ed, rg_ed, cfg, p_38,
                   &phase_tra_38)) {
        return false;
    }

    if (phase_tra_38.size() != static_cast<std::size_t>(M)) {
        return false;
    }
    phase_tra_38_cut = phase_tra_38;
    row_fa_cut.resize(M);
    for (int i = 0; i < M; ++i) {
        row_fa_cut[i] = y_faAxis[size_t(az_st + i)];
    }

    std::vector<float> y_faAxis_f(Na);
    for (size_t i = 0; i < Na; ++i) y_faAxis_f[i] = y_faAxis[i];

    if (!ensureCsiFrequencyWorkspace(Na)) return false;
    float* d_fa = d_csi_fa_;
    CUDA_CHECK(cudaMemcpyAsync(d_fa, y_faAxis_f.data(), Na * sizeof(float),
                               cudaMemcpyHostToDevice, stream_compute_));

    if (gpu_ptrs_.csi == nullptr) {
        std::cerr << "CSI device buffer is not initialized.\n";
        return false;
    }

    int threads = 256;
    int blocks = (static_cast<int>(total) + threads - 1) / threads;
    const gmti::trig_lut_device::TrigLutConfig trig_cfg =
        gmtiGetDeviceTrigLutConfig();
    if (cfg.csi_cancellation_mode == "row_complex_ls" ||
        cfg.csi_cancellation_mode == "row_phase_ls_linear" ||
        cfg.csi_cancellation_mode == "row_phase_ls_min_magnitude") {
        if (!ensureCsiRowWorkspace(Na)) return false;
        cuFloatComplex* d_alpha = d_csi_alpha_;
        float* d_coherence = d_csi_coherence_;
        const int row_blocks = (static_cast<int>(Na) + threads - 1) / threads;
        compute_row_complex_ls_kernel<<<row_blocks, threads, 0, stream_compute_>>>(
            (const cuFloatComplex*)gpu_ptrs_.t1,
            (const cuFloatComplex*)gpu_ptrs_.t2,
            d_alpha,
            d_coherence,
            static_cast<int>(Na),
            static_cast<int>(Nr),
            rg_st,
            rg_ed);
        CUDA_CHECK(cudaGetLastError());
        if (cfg.csi_metrics_enable) {
            std::vector<cuFloatComplex> alpha_host(Na);
            CUDA_CHECK(cudaMemcpyAsync(alpha_host.data(), d_alpha,
                                       Na * sizeof(cuFloatComplex),
                                       cudaMemcpyDeviceToHost, stream_compute_));
            CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
            double alpha_min = std::numeric_limits<double>::infinity();
            double alpha_max = 0.0;
            double alpha_sum = 0.0;
            std::size_t alpha_valid = 0;
            for (const cuFloatComplex value : alpha_host) {
                const double magnitude = std::hypot(
                    static_cast<double>(cuCrealf(value)),
                    static_cast<double>(cuCimagf(value)));
                if (!std::isfinite(magnitude)) continue;
                alpha_min = std::min(alpha_min, magnitude);
                alpha_max = std::max(alpha_max, magnitude);
                alpha_sum += magnitude;
                ++alpha_valid;
            }
            std::cout << "[CSI][ROW-LS] alpha_abs_min=" << alpha_min
                      << " alpha_abs_mean="
                      << (alpha_valid > 0 ? alpha_sum / alpha_valid : 0.0)
                      << " alpha_abs_max=" << alpha_max
                      << " valid_rows=" << alpha_valid << '/' << Na << std::endl;
        }
        if (cfg.csi_cancellation_mode == "row_complex_ls") {
            row_complex_ls_cancel_kernel<<<blocks, threads, 0, stream_compute_>>>(
                (const cuFloatComplex*)gpu_ptrs_.t1,
                (const cuFloatComplex*)gpu_ptrs_.t2,
                d_alpha,
                (cuFloatComplex*)gpu_ptrs_.csi,
                total,
                static_cast<int>(Nr),
                az_st,
                az_ed,
                cfg.csi_bypass_enable ? 1 : 0,
                d_coherence,
                cfg.csi_row_coherence_gate_enable ? 1 : 0,
                static_cast<float>(cfg.csi_row_coherence_min));
        } else if (cfg.csi_cancellation_mode == "row_phase_ls_linear") {
            row_phase_ls_linear_cancel_kernel<<<blocks, threads, 0, stream_compute_>>>(
                (const cuFloatComplex*)gpu_ptrs_.t1,
                (const cuFloatComplex*)gpu_ptrs_.t2,
                d_alpha,
                (cuFloatComplex*)gpu_ptrs_.csi,
                total,
                static_cast<int>(Nr),
                az_st,
                az_ed,
                cfg.csi_bypass_enable ? 1 : 0,
                static_cast<float>(cfg.csi_subtraction_gain),
                d_coherence,
                cfg.csi_row_coherence_gate_enable ? 1 : 0,
                static_cast<float>(cfg.csi_row_coherence_min));
        } else {
            row_phase_ls_min_magnitude_cancel_kernel<<<blocks, threads, 0, stream_compute_>>>(
                (const cuFloatComplex*)gpu_ptrs_.t1,
                (const cuFloatComplex*)gpu_ptrs_.t2,
                d_alpha,
                (cuFloatComplex*)gpu_ptrs_.csi,
                total,
                static_cast<int>(Nr),
                az_st,
                az_ed,
                cfg.csi_bypass_enable ? 1 : 0,
                static_cast<float>(cfg.csi_subtraction_gain),
                d_coherence,
                cfg.csi_row_coherence_gate_enable ? 1 : 0,
                static_cast<float>(cfg.csi_row_coherence_min));
        }
        CUDA_CHECK(cudaGetLastError());
    } else {
        float* d_legacy_coherence = nullptr;
        const bool legacy_coherence_gate = cfg.csi_row_coherence_gate_enable;
        if (legacy_coherence_gate) {
            if (!ensureCsiRowWorkspace(Na)) return false;
            const int row_blocks = (static_cast<int>(Na) + threads - 1) / threads;
            compute_row_complex_ls_kernel<<<row_blocks, threads, 0, stream_compute_>>>(
                (const cuFloatComplex*)gpu_ptrs_.t1,
                (const cuFloatComplex*)gpu_ptrs_.t2,
                d_csi_alpha_,
                d_csi_coherence_,
                static_cast<int>(Na),
                static_cast<int>(Nr),
                rg_st,
                rg_ed);
            CUDA_CHECK(cudaGetLastError());
            d_legacy_coherence = d_csi_coherence_;
            if (cfg.runtime_diagnostics_enabled || cfg.csi_metrics_enable) {
                std::vector<float> coherence_host(Na, 0.0f);
                CUDA_CHECK(cudaMemcpyAsync(
                    coherence_host.data(), d_legacy_coherence,
                    Na * sizeof(float), cudaMemcpyDeviceToHost, stream_compute_));
                CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
                std::size_t gated_rows = 0U;
                double coherence_sum = 0.0;
                for (int row = az_st; row <= az_ed; ++row) {
                    const float value = coherence_host[static_cast<std::size_t>(row)];
                    coherence_sum += static_cast<double>(value);
                    gated_rows += value < static_cast<float>(cfg.csi_row_coherence_min)
                        ? 1U : 0U;
                }
                const int row_count = az_ed - az_st + 1;
                std::cout << "[CSI][LEGACY-GATE] rows=" << row_count
                          << " gated_rows=" << gated_rows
                          << " coherence_mean="
                          << (row_count > 0 ? coherence_sum / row_count : 0.0)
                          << " min=" << cfg.csi_row_coherence_min
                          << std::endl;
            }
        }
        clutter_cancel_kernel<<<blocks, threads, 0, stream_compute_>>>(
            (const cuFloatComplex*)gpu_ptrs_.t1,
            (const cuFloatComplex*)gpu_ptrs_.t2,
            d_fa,
            (cuFloatComplex*)gpu_ptrs_.csi,
            (int)Na,
            (int)Nr,
            az_st,
            az_ed,
            p_38[0],
            p_38[1],
            cfg.csi_bypass_enable ? 1 : 0,
            d_legacy_coherence,
            legacy_coherence_gate ? 1 : 0,
            static_cast<float>(cfg.csi_row_coherence_min),
            trig_cfg);
    }
    CUDA_CHECK(cudaGetLastError());

    prosig_38.resize(total);
    CUDA_CHECK(cudaMemcpyAsync(prosig_38.data(), gpu_ptrs_.csi, total * sizeof(cudacd),
                               cudaMemcpyDeviceToHost, stream_compute_));
    CUDA_CHECK(cudaStreamSynchronize(stream_compute_));

    return true;
}

// 清理 CUDA 资源（在析构或程序结束时调用）
void GMTIProcessor::cleanupCUDAResources()
{
    if (d_csi_coherence_ != nullptr) {
        cudaFree(d_csi_coherence_);
        d_csi_coherence_ = nullptr;
        d_csi_coherence_elements_ = 0U;
    }

    if (d_csi_alpha_ != nullptr) {
        cudaFree(d_csi_alpha_);
        d_csi_alpha_ = nullptr;
        d_csi_alpha_elements_ = 0U;
    }

    if (d_csi_fa_ != nullptr) {
        cudaFree(d_csi_fa_);
        d_csi_fa_ = nullptr;
        d_csi_fa_elements_ = 0U;
    }

    if (d_p38_phase_ != nullptr) {
        cudaFree(d_p38_phase_);
        d_p38_phase_ = nullptr;
        d_p38_phase_elements_ = 0U;
    }

    if (d_cfar_maps_ != nullptr) {
        cudaFree(d_cfar_maps_);
        d_cfar_maps_ = nullptr;
        d_cfar_map_elements_ = 0U;
        d_cfar_maps_valid_ = false;
        DBG("CUDA resident CFAR maps freed");
    }

    if (d_phase_snapshot_ != nullptr) {
        cudaFree(d_phase_snapshot_);
        d_phase_snapshot_ = nullptr;
        d_phase_snapshot_elements_ = 0;
        DBG("CUDA phase snapshot freed");
    }

    if (d_detection_workspace_ != nullptr) {
        cudaFree(d_detection_workspace_);
        d_detection_workspace_ = nullptr;
        d_detection_workspace_bytes_ = 0;
        DBG("CUDA detection workspace freed");
    }

    if (d_workspace != nullptr) {
        cudaFree(d_workspace);
        d_workspace = nullptr;
        d_workspace_bytes = 0;
        gpu_single_buffer_bytes_ = 0;
        DBG("CUDA workspace freed");
    }

    if (gpu_ptrs_.dbs_amplitude != nullptr) {
        cudaFree(gpu_ptrs_.dbs_amplitude);
        gpu_ptrs_.dbs_amplitude = nullptr;
        dbs_amplitude_elements_ = 0;
        DBG("CUDA DBS amplitude buffer freed");
    }

    if (d_phi_fit_ != nullptr) {
        cudaFree(d_phi_fit_);
        d_phi_fit_ = nullptr;
    }

    if (cufft_plan_ != 0) {
        cufftDestroy(cufft_plan_);
        cufft_plan_ = 0;
        cached_Na_ = cached_Nr_ = 0;
        DBG("cuFFT plan destroyed");
    }
}
