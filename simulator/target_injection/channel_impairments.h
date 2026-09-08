#pragma once

#include "target_config.h"

#include <cstdint>
#include <string>
#include <vector>

namespace gmti {
namespace target_injection {

// Shared Stage2/Stage3 channel-impairment definition.  All values equal to
// zero is a strict bitwise bypass, even when enabled=true.
struct ChannelImpairmentConfig {
    bool enabled = false;
    double channel_amp_mismatch_db = 0.0;
    double channel_fixed_phase_mismatch_deg = 0.0;
    double channel_phase_jitter_std_deg = 0.0;
    double channel_range_shift_samples = 0.0;
    double channel_time_delay_ns = 0.0;
    double channel_noise_power_ratio_db = 0.0;
    double iq_gain_imbalance_db = 0.0;
    double iq_phase_imbalance_deg = 0.0;
    double baseline_error_m = 0.0;
    double per_pulse_phase_drift_deg = 0.0;
    double per_beam_phase_bias_deg = 0.0;
    double sample_clock_error_ppm = 0.0;
    double channel_drop_probability = 0.0;
    double channel_saturation_level = 0.0; // <=0 disables clipping
};

struct ChannelImpairmentRealization {
    bool applied = false;
    int period_id = 0;
    int beam_id = 0;
    int pulse_id = 0;
    double relative_gain = 1.0;
    double relative_phase_deg = 0.0;
    double effective_shift_samples = 0.0;
    double sample_clock_error_ppm = 0.0;
    double added_noise_sigma_ch1 = 0.0;
    double added_noise_sigma_ch2 = 0.0;
    bool channel_dropped = false;
    int saturated_sample_count = 0;
};

bool channelImpairmentsAreZero(const ChannelImpairmentConfig &cfg);

// Injection point: after target/background echo superposition and immediately
// before the final new-protocol packet write.  Values are loaded as float,
// transformed in the complex domain, and stored using the configured protocol
// IQ type.  The same function is called by Stage2 and Stage3.
ChannelImpairmentRealization applyChannelImpairments(
    std::vector<uint8_t> &packet,
    const RadarConfig &radar,
    const ChannelImpairmentConfig &cfg,
    int period_id,
    int beam_id,
    int pulse_id,
    double theta_cmd_deg,
    uint64_t random_seed);

std::string channelImpairmentConfigJson(const ChannelImpairmentConfig &cfg);

} // namespace target_injection
} // namespace gmti
