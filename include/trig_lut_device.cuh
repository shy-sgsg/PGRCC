#pragma once

#include <cuda_runtime.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

namespace gmti {
namespace trig_lut_device {

constexpr int kTrigLutSize = 16384;
constexpr int kTrigLutMask = kTrigLutSize - 1;
constexpr int kModeLut = 0;
constexpr int kModeMath = 1;
constexpr int kModeCompare = 2;

struct TrigLutConfig {
    const float* sin_table;
    const float* cos_table;
    const float* asin_table;
    const float* atan_table;
    int mode;
};

struct PeriodicLutCoord {
    int lower;
    int upper;
    float fraction;
};

// The periodic tables contain samples at [0, 2*pi).  Interpolate between
// adjacent samples and wrap the upper index at 2*pi.  This must mirror the
// host implementation in src/trig_lut.cpp so CPU/CUDA projection paths have
// the same numerical behavior.
__device__ __forceinline__ PeriodicLutCoord periodicLutCoord(float x)
{
    constexpr float kScale = static_cast<float>(kTrigLutSize) /
                             (2.0f * static_cast<float>(M_PI));
    const float scaled = x * kScale;
    const float lower_real = floorf(scaled);
    const int lower_unwrapped = static_cast<int>(lower_real);
    int lower = lower_unwrapped % kTrigLutSize;
    if (lower < 0) lower += kTrigLutSize;
    return {lower, (lower + 1) & kTrigLutMask, scaled - lower_real};
}

__device__ __forceinline__ float interpolatePeriodic(const float* table,
                                                     const PeriodicLutCoord& coord)
{
    const float y0 = table[coord.lower];
    const float y1 = table[coord.upper];
    return y0 + coord.fraction * (y1 - y0);
}

__device__ __forceinline__ float interpolateUnit(const float* table,
                                                 float x,
                                                 float lower,
                                                 float upper)
{
    x = fminf(upper, fmaxf(lower, x));
    const float scaled = (x - lower) / (upper - lower) *
                         static_cast<float>(kTrigLutSize);
    const int index = static_cast<int>(floorf(scaled));
    if (index >= kTrigLutSize) return table[kTrigLutSize];
    const float fraction = scaled - static_cast<float>(index);
    return table[index] + fraction * (table[index + 1] - table[index]);
}

__device__ __forceinline__ float interpolateZeroOne(const float* table, float x)
{
    return interpolateUnit(table, x, 0.0f, 1.0f);
}

__device__ __forceinline__ float lut_sinf(float x, const TrigLutConfig& cfg)
{
    return interpolatePeriodic(cfg.sin_table, periodicLutCoord(x));
}

__device__ __forceinline__ float lut_cosf(float x, const TrigLutConfig& cfg)
{
    return interpolatePeriodic(cfg.cos_table, periodicLutCoord(x));
}

__device__ __forceinline__ float sinf(float x, const TrigLutConfig& cfg)
{
    return cfg.mode == kModeLut ? lut_sinf(x, cfg) : ::sinf(x);
}

__device__ __forceinline__ float cosf(float x, const TrigLutConfig& cfg)
{
    return cfg.mode == kModeLut ? lut_cosf(x, cfg) : ::cosf(x);
}

__device__ __forceinline__ void sincosf(float x, float* s, float* c,
                                        const TrigLutConfig& cfg)
{
    if (cfg.mode != kModeLut) {
        ::sincosf(x, s, c);
        return;
    }
    const PeriodicLutCoord coord = periodicLutCoord(x);
    *s = interpolatePeriodic(cfg.sin_table, coord);
    *c = interpolatePeriodic(cfg.cos_table, coord);
}

__device__ __forceinline__ float tanf(float x, const TrigLutConfig& cfg)
{
    if (cfg.mode != kModeLut) return ::tanf(x);
    const PeriodicLutCoord coord = periodicLutCoord(x);
    return interpolatePeriodic(cfg.sin_table, coord) /
           interpolatePeriodic(cfg.cos_table, coord);
}

__device__ __forceinline__ float lut_asinf(float x, const TrigLutConfig& cfg)
{
    return interpolateUnit(cfg.asin_table, x, -1.0f, 1.0f);
}

__device__ __forceinline__ float asinf(float x, const TrigLutConfig& cfg)
{
    return cfg.mode == kModeLut ? lut_asinf(x, cfg) : ::asinf(x);
}

__device__ __forceinline__ float acosf(float x, const TrigLutConfig& cfg)
{
    return cfg.mode == kModeLut
        ? 1.57079632679489661923f - lut_asinf(x, cfg)
        : ::acosf(x);
}

__device__ __forceinline__ float lut_atanf(float x, const TrigLutConfig& cfg)
{
    if (x > 1.0f) {
        return 1.57079632679489661923f -
               interpolateZeroOne(cfg.atan_table, 1.0f / x);
    }
    if (x < -1.0f) {
        return -1.57079632679489661923f +
               interpolateZeroOne(cfg.atan_table, -1.0f / x);
    }
    const float ax = fabsf(x);
    const float a = interpolateZeroOne(cfg.atan_table, ax);
    return x < 0.0f ? -a : a;
}

__device__ __forceinline__ float atanf(float x, const TrigLutConfig& cfg)
{
    return cfg.mode == kModeLut ? lut_atanf(x, cfg) : ::atanf(x);
}

__device__ __forceinline__ float lut_atan2f(float y, float x,
                                           const TrigLutConfig& cfg)
{
    if (x > 0.0f) return lut_atanf(y / x, cfg);
    if (x < 0.0f) {
        return (y >= 0.0f ? static_cast<float>(M_PI) : -static_cast<float>(M_PI))
            + lut_atanf(y / x, cfg);
    }
    if (y > 0.0f) return 1.57079632679489661923f;
    if (y < 0.0f) return -1.57079632679489661923f;
    return 0.0f;
}

__device__ __forceinline__ float atan2f(float y, float x,
                                       const TrigLutConfig& cfg)
{
    return cfg.mode == kModeLut ? lut_atan2f(y, x, cfg) : ::atan2f(y, x);
}

} // namespace trig_lut_device
} // namespace gmti
