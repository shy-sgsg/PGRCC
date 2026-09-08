#pragma once

#include <complex>
#include <cstddef>
#include <string>
#include <vector>

namespace gmti {
namespace stage3 {

// GPU-facing POD representation of one fixed ground scatterer. Coordinates are
// local ENU metres: e_m is east and n_m is north. amplitude/phase_rad describe
// fixed complex reflectivity; propagation phase is recomputed for every pulse
// and receive channel.
struct Stage3CudaCell {
    double e_m;
    double n_m;
    float amplitude;
    float phase_rad;
};

// Parameters for one contiguous pulse batch from one zero-based beam.
struct Stage3CudaForwardParams {
    int ddc_len;
    int pulse_start;
    int pulse_count;
    int period_id;
    int beam0;
    int beam_count;
    int pulse_num;
    double prf_hz;
    double fs_hz;
    double sample_delay_sec;
    double fc_hz;
    double platform_height_m;
    double ground_z_m;
    double platform_speed_mps;
    double d_chan_m;
    int carrier_phase_sign;
};

struct Stage3CudaTiming {
    float h2d_ms;
    float kernel_ms;
    float d2h_ms;
    float total_ms;
};

// Returns true when the CUDA runtime can see at least one usable device.
bool stage3CudaAvailable();

// Computes pulse_count * ddc_len complex samples for each receive channel.
// The implementation performs one cell upload, one batched kernel launch, and
// one combined two-channel download. Contributions sharing a range bin use
// float atomicAdd, so their last-bit summation order is not deterministic.
bool forwardStage3CudaBatch(const Stage3CudaCell *cells,
                            std::size_t cell_count,
                            const Stage3CudaForwardParams &params,
                            std::vector<std::complex<float>> &channel1,
                            std::vector<std::complex<float>> &channel2,
                            Stage3CudaTiming &timing,
                            std::string &err);

} // namespace stage3
} // namespace gmti

