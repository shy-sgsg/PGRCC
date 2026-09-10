#pragma once

#include "stage2_config.h"
#include "stage2_scatterer.h"
#include "stage2_temporal_clutter.h"

#include "../target_injection/lfm_echo_generator.h"

#include <random>
#include <vector>

namespace gmti {
namespace stage2 {

struct Stage2Stats {
    uint64_t packets_written = 0;
    uint64_t scatterer_echoes = 0;
    uint64_t scatterer_samples = 0;
    uint64_t target_pulses_injected = 0;
    uint64_t target_samples_injected = 0;
    uint64_t continuous_area_packets = 0;
    uint64_t continuous_area_samples = 0;
    uint64_t continuous_area_precompressed_packets = 0;
    uint64_t continuous_area_raw_lfm_packets = 0;
    double max_abs_component = 0.0;
    double sum_noise_power = 0.0;
    uint64_t noise_samples = 0;
    bool has_nan = false;
    bool has_inf = false;
    TemporalClutterDiagnostics temporal_clutter;
};

void addScatterersToPacket(std::vector<uint8_t> &packet,
                           const gmti::target_injection::RadarConfig &radar,
                           const gmti::target_injection::TargetGlobalConfig &global,
                           const ScattererList &scatterers,
                           int period_id,
                           int beam_id,
                           int pulse_id,
                           double beam_gain_threshold,
                           Stage2Stats &stats,
                           double time_override_sec = std::numeric_limits<double>::quiet_NaN(),
                           double servo_azimuth_override_deg = std::numeric_limits<double>::quiet_NaN());

bool addContinuousAreaClutter(std::vector<uint8_t> &packet,
                              const gmti::target_injection::RadarConfig &radar,
                              const gmti::target_injection::TargetGlobalConfig &global,
                              const SceneConfig &scene,
                              uint32_t random_seed,
                              int reference_period_id,
                              int period_id,
                              int beam_id,
                              int pulse_id,
                              bool raw_lfm_domain,
                              Stage2Stats &stats,
                              std::string &err,
                              double time_override_sec = std::numeric_limits<double>::quiet_NaN(),
                              double servo_azimuth_override_deg = std::numeric_limits<double>::quiet_NaN(),
                              double grid_reference_time_override_sec = std::numeric_limits<double>::quiet_NaN());

void addThermalNoise(std::vector<uint8_t> &packet,
                     const gmti::target_injection::RadarConfig &radar,
                     double noise_power,
                     std::mt19937 &rng,
                     Stage2Stats &stats);

void scanPacketStats(const std::vector<uint8_t> &packet,
                     const gmti::target_injection::RadarConfig &radar,
                     Stage2Stats &stats);

} // namespace stage2
} // namespace gmti
