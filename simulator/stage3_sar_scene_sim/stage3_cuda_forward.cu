#include "stage3_cuda_forward.h"

#include <cuda_runtime.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <sstream>
#include <vector>

namespace gmti {
namespace stage3 {

namespace {

const double kC = 299792458.0;
const double kTwoPi = 6.283185307179586476925286766559;
const int kThreadsPerBlock = 256;
const int kMaxBlocks = 65535;

__global__ void forwardStage3Kernel(const Stage3CudaCell *cells,
                                    unsigned long long cell_count,
                                    Stage3CudaForwardParams params,
                                    float2 *two_channel_output,
                                    unsigned long long samples_per_channel)
{
    const unsigned long long contribution_count =
        cell_count * static_cast<unsigned long long>(params.pulse_count);
    const unsigned long long first =
        static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    const unsigned long long stride =
        static_cast<unsigned long long>(gridDim.x) * blockDim.x;
    const double wavelength_m = kC / params.fc_hz;
    const double dz = params.ground_z_m - params.platform_height_m;

    for (unsigned long long contribution = first;
         contribution < contribution_count;
         contribution += stride) {
        const unsigned long long pulse_local = contribution / cell_count;
        const unsigned long long cell_idx = contribution - pulse_local * cell_count;
        const Stage3CudaCell cell = cells[cell_idx];
        if (!(cell.amplitude > 0.0f) || !isfinite(cell.amplitude) ||
            !isfinite(cell.phase_rad) || !isfinite(cell.e_m) || !isfinite(cell.n_m)) {
            continue;
        }

        const int pulse_id = params.pulse_start + static_cast<int>(pulse_local);
        const double sequence_pulse =
            static_cast<double>(params.period_id) * params.beam_count * params.pulse_num +
            static_cast<double>(params.beam0) * params.pulse_num +
            static_cast<double>(pulse_id);
        const double time_sec = sequence_pulse / params.prf_hz;
        const double platform_n = params.platform_speed_mps * time_sec;
        const double de = cell.e_m;
        const double dn_tx = cell.n_m - platform_n;
        const double tx_path = sqrt(de * de + dn_tx * dn_tx + dz * dz);

        const double sample_float =
            (2.0 * tx_path / kC - params.sample_delay_sec) * params.fs_hz;
        const long long sample = static_cast<long long>(floor(sample_float + 0.5));
        if (sample < 0 || sample >= params.ddc_len) {
            continue;
        }

        // Channel 1 is the aft receiver (-d/2 along north); channel 2 is the
        // forward receiver (+d/2), matching the Stage2/Stage3 CTDR convention.
        const double rx1_n = platform_n - 0.5 * params.d_chan_m;
        const double rx2_n = platform_n + 0.5 * params.d_chan_m;
        const double dn_rx1 = cell.n_m - rx1_n;
        const double dn_rx2 = cell.n_m - rx2_n;
        const double path1 = tx_path + sqrt(de * de + dn_rx1 * dn_rx1 + dz * dz);
        const double path2 = tx_path + sqrt(de * de + dn_rx2 * dn_rx2 + dz * dz);
        const double spreading = 1.0 / fmax(1.0, tx_path / 1000.0);
        const double amplitude = static_cast<double>(cell.amplitude) * spreading;

        const double phase_scale =
            static_cast<double>(params.carrier_phase_sign) * kTwoPi / wavelength_m;
        const double phase1 = remainder(phase_scale * path1 +
                                        static_cast<double>(cell.phase_rad), kTwoPi);
        const double phase2 = remainder(phase_scale * path2 +
                                        static_cast<double>(cell.phase_rad), kTwoPi);
        double sin1 = 0.0;
        double cos1 = 0.0;
        double sin2 = 0.0;
        double cos2 = 0.0;
        sincos(phase1, &sin1, &cos1);
        sincos(phase2, &sin2, &cos2);

        const unsigned long long out_idx =
            pulse_local * static_cast<unsigned long long>(params.ddc_len) +
            static_cast<unsigned long long>(sample);
        atomicAdd(&two_channel_output[out_idx].x,
                  static_cast<float>(amplitude * cos1));
        atomicAdd(&two_channel_output[out_idx].y,
                  static_cast<float>(amplitude * sin1));
        atomicAdd(&two_channel_output[samples_per_channel + out_idx].x,
                  static_cast<float>(amplitude * cos2));
        atomicAdd(&two_channel_output[samples_per_channel + out_idx].y,
                  static_cast<float>(amplitude * sin2));
    }
}

std::string cudaErrorMessage(const char *operation, cudaError_t status)
{
    std::ostringstream os;
    os << operation << " failed: " << cudaGetErrorString(status);
    return os.str();
}

bool validFinite(double x)
{
    return std::isfinite(x) != 0;
}

void resetTiming(Stage3CudaTiming &timing)
{
    timing.h2d_ms = 0.0f;
    timing.kernel_ms = 0.0f;
    timing.d2h_ms = 0.0f;
    timing.total_ms = 0.0f;
}

} // namespace

bool stage3CudaAvailable()
{
    int device_count = 0;
    const cudaError_t status = cudaGetDeviceCount(&device_count);
    if (status != cudaSuccess) {
        (void)cudaGetLastError();
        return false;
    }
    return device_count > 0;
}

bool forwardStage3CudaBatch(const Stage3CudaCell *cells,
                            std::size_t cell_count,
                            const Stage3CudaForwardParams &params,
                            std::vector<std::complex<float>> &channel1,
                            std::vector<std::complex<float>> &channel2,
                            Stage3CudaTiming &timing,
                            std::string &err)
{
    resetTiming(timing);
    err.clear();
    channel1.clear();
    channel2.clear();

    if ((cell_count > 0U && cells == NULL) || params.ddc_len <= 0 ||
        params.pulse_start < 0 || params.pulse_count <= 0 || params.period_id < 0 ||
        params.beam0 < 0 || params.beam_count <= 0 || params.beam0 >= params.beam_count ||
        params.pulse_num <= 0 || params.pulse_start > params.pulse_num - params.pulse_count ||
        !(params.prf_hz > 0.0) || !(params.fs_hz > 0.0) || !(params.fc_hz > 0.0) ||
        !(params.d_chan_m >= 0.0) ||
        !validFinite(params.prf_hz) || !validFinite(params.fs_hz) ||
        !validFinite(params.sample_delay_sec) || !validFinite(params.fc_hz) ||
        !validFinite(params.platform_height_m) || !validFinite(params.ground_z_m) ||
        !validFinite(params.platform_speed_mps) || !validFinite(params.d_chan_m) ||
        (params.carrier_phase_sign != -1 && params.carrier_phase_sign != 1)) {
        err = "invalid Stage3 CUDA forward parameters";
        return false;
    }

    const std::size_t pulse_count = static_cast<std::size_t>(params.pulse_count);
    const std::size_t ddc_len = static_cast<std::size_t>(params.ddc_len);
    if (pulse_count > std::numeric_limits<std::size_t>::max() / ddc_len) {
        err = "Stage3 CUDA output size overflow";
        return false;
    }
    const std::size_t samples_per_channel = pulse_count * ddc_len;
    if (samples_per_channel > std::numeric_limits<std::size_t>::max() / 2U ||
        2U * samples_per_channel > std::numeric_limits<std::size_t>::max() / sizeof(float2)) {
        err = "Stage3 CUDA combined output size overflow";
        return false;
    }
    if (cell_count > std::numeric_limits<unsigned long long>::max() /
                         static_cast<unsigned long long>(params.pulse_count)) {
        err = "Stage3 CUDA contribution count overflow";
        return false;
    }

    try {
        channel1.assign(samples_per_channel, std::complex<float>(0.0f, 0.0f));
        channel2.assign(samples_per_channel, std::complex<float>(0.0f, 0.0f));
    } catch (const std::exception &e) {
        err = std::string("failed to allocate Stage3 CUDA host output: ") + e.what();
        return false;
    }
    if (cell_count == 0U) {
        return true;
    }

    int device_count = 0;
    cudaError_t status = cudaGetDeviceCount(&device_count);
    if (status != cudaSuccess || device_count <= 0) {
        err = (status == cudaSuccess)
            ? "no CUDA device is available"
            : cudaErrorMessage("cudaGetDeviceCount", status);
        return false;
    }

    Stage3CudaCell *device_cells = NULL;
    float2 *device_output = NULL;
    cudaStream_t stream = NULL;
    cudaEvent_t total_start = NULL;
    cudaEvent_t h2d_done = NULL;
    cudaEvent_t kernel_start = NULL;
    cudaEvent_t kernel_done = NULL;
    cudaEvent_t d2h_done = NULL;
    bool ok = false;
    std::vector<float2> host_output;

#define STAGE3_CUDA_CHECK(call)                                                    \
    do {                                                                           \
        status = (call);                                                           \
        if (status != cudaSuccess) {                                               \
            err = cudaErrorMessage(#call, status);                                 \
            goto cleanup;                                                          \
        }                                                                          \
    } while (0)

    try {
        host_output.resize(2U * samples_per_channel);
    } catch (const std::exception &e) {
        err = std::string("failed to allocate Stage3 CUDA transfer output: ") + e.what();
        goto cleanup;
    }

    STAGE3_CUDA_CHECK(cudaStreamCreateWithFlags(&stream, cudaStreamNonBlocking));
    STAGE3_CUDA_CHECK(cudaEventCreate(&total_start));
    STAGE3_CUDA_CHECK(cudaEventCreate(&h2d_done));
    STAGE3_CUDA_CHECK(cudaEventCreate(&kernel_start));
    STAGE3_CUDA_CHECK(cudaEventCreate(&kernel_done));
    STAGE3_CUDA_CHECK(cudaEventCreate(&d2h_done));
    STAGE3_CUDA_CHECK(cudaMalloc(reinterpret_cast<void **>(&device_cells),
                                 cell_count * sizeof(Stage3CudaCell)));
    STAGE3_CUDA_CHECK(cudaMalloc(reinterpret_cast<void **>(&device_output),
                                 2U * samples_per_channel * sizeof(float2)));

    STAGE3_CUDA_CHECK(cudaEventRecord(total_start, stream));
    STAGE3_CUDA_CHECK(cudaMemcpyAsync(device_cells, cells,
                                      cell_count * sizeof(Stage3CudaCell),
                                      cudaMemcpyHostToDevice, stream));
    STAGE3_CUDA_CHECK(cudaEventRecord(h2d_done, stream));
    STAGE3_CUDA_CHECK(cudaMemsetAsync(device_output, 0,
                                      2U * samples_per_channel * sizeof(float2), stream));
    STAGE3_CUDA_CHECK(cudaEventRecord(kernel_start, stream));

    {
        const unsigned long long contribution_count =
            static_cast<unsigned long long>(cell_count) *
            static_cast<unsigned long long>(params.pulse_count);
        const unsigned long long blocks_needed =
            (contribution_count + kThreadsPerBlock - 1ULL) / kThreadsPerBlock;
        const int block_count = static_cast<int>(
            std::min<unsigned long long>(blocks_needed, kMaxBlocks));
        forwardStage3Kernel<<<block_count, kThreadsPerBlock, 0, stream>>>(
            device_cells,
            static_cast<unsigned long long>(cell_count),
            params,
            device_output,
            static_cast<unsigned long long>(samples_per_channel));
    }
    STAGE3_CUDA_CHECK(cudaPeekAtLastError());
    STAGE3_CUDA_CHECK(cudaEventRecord(kernel_done, stream));
    STAGE3_CUDA_CHECK(cudaMemcpyAsync(&host_output[0], device_output,
                                      2U * samples_per_channel * sizeof(float2),
                                      cudaMemcpyDeviceToHost, stream));
    STAGE3_CUDA_CHECK(cudaEventRecord(d2h_done, stream));
    STAGE3_CUDA_CHECK(cudaEventSynchronize(d2h_done));
    STAGE3_CUDA_CHECK(cudaEventElapsedTime(&timing.h2d_ms, total_start, h2d_done));
    STAGE3_CUDA_CHECK(cudaEventElapsedTime(&timing.kernel_ms, kernel_start, kernel_done));
    STAGE3_CUDA_CHECK(cudaEventElapsedTime(&timing.d2h_ms, kernel_done, d2h_done));
    STAGE3_CUDA_CHECK(cudaEventElapsedTime(&timing.total_ms, total_start, d2h_done));

    for (std::size_t i = 0; i < samples_per_channel; ++i) {
        channel1[i] = std::complex<float>(host_output[i].x, host_output[i].y);
        const float2 &v2 = host_output[samples_per_channel + i];
        channel2[i] = std::complex<float>(v2.x, v2.y);
    }
    ok = true;

cleanup:
    if (device_output != NULL) (void)cudaFree(device_output);
    if (device_cells != NULL) (void)cudaFree(device_cells);
    if (d2h_done != NULL) (void)cudaEventDestroy(d2h_done);
    if (kernel_done != NULL) (void)cudaEventDestroy(kernel_done);
    if (kernel_start != NULL) (void)cudaEventDestroy(kernel_start);
    if (h2d_done != NULL) (void)cudaEventDestroy(h2d_done);
    if (total_start != NULL) (void)cudaEventDestroy(total_start);
    if (stream != NULL) (void)cudaStreamDestroy(stream);
    if (!ok) {
        channel1.clear();
        channel2.clear();
        resetTiming(timing);
    }
#undef STAGE3_CUDA_CHECK
    return ok;
}

} // namespace stage3
} // namespace gmti

