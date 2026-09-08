#include "channel_impairments.h"

#include "dbs/NewProtocolLayout.hpp"

#include <algorithm>
#include <cmath>
#include <complex>
#include <iomanip>
#include <limits>
#include <random>
#include <sstream>

namespace gmti {
namespace target_injection {
namespace {

constexpr double kPiLocal = 3.141592653589793238462643383279502884;
constexpr double kCLocal = 299792458.0;

uint64_t mixSeed(uint64_t x)
{
    x += 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30U)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27U)) * 0x94d049bb133111ebULL;
    return x ^ (x >> 31U);
}

uint64_t packetSeed(uint64_t base, int period, int beam, int pulse)
{
    uint64_t x = mixSeed(base);
    x ^= mixSeed(static_cast<uint64_t>(static_cast<uint32_t>(period)) + 0x100000001b3ULL);
    x ^= mixSeed(static_cast<uint64_t>(static_cast<uint32_t>(beam)) + 0x9e3779b9ULL);
    x ^= mixSeed(static_cast<uint64_t>(static_cast<uint32_t>(pulse)) + 0x85ebca6bULL);
    return mixSeed(x);
}

std::size_t channelCount(const RadarConfig &radar)
{
    return static_cast<std::size_t>(std::max(2, radar.new_protocol_channel_count));
}

std::string iqType(const RadarConfig &radar)
{
    return radar.iq_data_type.empty() ? "float32" : radar.iq_data_type;
}

std::complex<float> loadCh(const std::vector<uint8_t> &packet,
                           const RadarConfig &radar,
                           int sample,
                           int channel)
{
    const std::string type = iqType(radar);
    const std::size_t iq_bytes = gmti::new_protocol::bytesPerIq(type);
    const std::size_t off = gmti::new_protocol::kHeaderBytes +
        static_cast<std::size_t>(sample) * gmti::new_protocol::sampleBytes(channelCount(radar), type) +
        gmti::new_protocol::channelOffset(static_cast<std::size_t>(channel), type);
    return std::complex<float>(
        gmti::new_protocol::loadIqAsFloat(&packet[off], type),
        gmti::new_protocol::loadIqAsFloat(&packet[off + iq_bytes], type));
}

void storeCh(std::vector<uint8_t> &packet,
             const RadarConfig &radar,
             int sample,
             int channel,
             const std::complex<float> &value)
{
    const std::string type = iqType(radar);
    const std::size_t iq_bytes = gmti::new_protocol::bytesPerIq(type);
    const std::size_t off = gmti::new_protocol::kHeaderBytes +
        static_cast<std::size_t>(sample) * gmti::new_protocol::sampleBytes(channelCount(radar), type) +
        gmti::new_protocol::channelOffset(static_cast<std::size_t>(channel), type);
    gmti::new_protocol::storeIqFromFloat(&packet[off], type, value.real());
    gmti::new_protocol::storeIqFromFloat(&packet[off + iq_bytes], type, value.imag());
}

std::complex<float> interpolate(const std::vector<std::complex<float>> &v, double index)
{
    if (index < 0.0 || index > static_cast<double>(v.size() - 1U)) {
        return std::complex<float>(0.0f, 0.0f);
    }
    const std::size_t i0 = static_cast<std::size_t>(std::floor(index));
    const std::size_t i1 = std::min(v.size() - 1U, i0 + 1U);
    const float a = static_cast<float>(index - static_cast<double>(i0));
    return v[i0] * (1.0f - a) + v[i1] * a;
}

std::complex<float> applyIqImbalance(std::complex<float> value,
                                     double gain_db,
                                     double phase_deg)
{
    if (gain_db == 0.0 && phase_deg == 0.0) return value;
    const double g = std::pow(10.0, gain_db / 40.0);
    const double inv_g = 1.0 / g;
    const double eps = phase_deg * kPiLocal / 180.0;
    const double i = static_cast<double>(value.real()) * g;
    const double q = static_cast<double>(value.imag()) * inv_g;
    const double q_skew = q * std::cos(eps) + i * std::sin(eps);
    return std::complex<float>(static_cast<float>(i), static_cast<float>(q_skew));
}

std::complex<float> clipMagnitude(std::complex<float> value,
                                  double limit,
                                  int &count)
{
    if (!(limit > 0.0)) return value;
    const double mag = std::abs(value);
    if (!(mag > limit)) return value;
    ++count;
    return value * static_cast<float>(limit / mag);
}

} // namespace

bool channelImpairmentsAreZero(const ChannelImpairmentConfig &c)
{
    return c.channel_amp_mismatch_db == 0.0 &&
           c.channel_fixed_phase_mismatch_deg == 0.0 &&
           c.channel_phase_jitter_std_deg == 0.0 &&
           c.channel_range_shift_samples == 0.0 &&
           c.channel_time_delay_ns == 0.0 &&
           c.channel_noise_power_ratio_db == 0.0 &&
           c.iq_gain_imbalance_db == 0.0 &&
           c.iq_phase_imbalance_deg == 0.0 &&
           c.baseline_error_m == 0.0 &&
           c.per_pulse_phase_drift_deg == 0.0 &&
           c.per_beam_phase_bias_deg == 0.0 &&
           c.sample_clock_error_ppm == 0.0 &&
           c.channel_drop_probability == 0.0 &&
           c.channel_saturation_level <= 0.0;
}

ChannelImpairmentRealization applyChannelImpairments(
    std::vector<uint8_t> &packet,
    const RadarConfig &radar,
    const ChannelImpairmentConfig &cfg,
    int period_id,
    int beam_id,
    int pulse_id,
    double theta_cmd_deg,
    uint64_t random_seed)
{
    ChannelImpairmentRealization out;
    out.period_id = period_id;
    out.beam_id = beam_id;
    out.pulse_id = pulse_id;
    out.sample_clock_error_ppm = cfg.sample_clock_error_ppm;
    if (!cfg.enabled || channelImpairmentsAreZero(cfg) || radar.pulse_len <= 0) {
        return out;
    }

    const int ch1 = radar.new_protocol_read_channel_1;
    const int ch2 = radar.new_protocol_read_channel_2;
    const int n_samples = radar.pulse_len;
    std::vector<std::complex<float>> v1(static_cast<std::size_t>(n_samples));
    std::vector<std::complex<float>> v2(static_cast<std::size_t>(n_samples));
    double mean_power1 = 0.0;
    double mean_power2 = 0.0;
    for (int n = 0; n < n_samples; ++n) {
        v1[static_cast<std::size_t>(n)] = loadCh(packet, radar, n, ch1);
        v2[static_cast<std::size_t>(n)] = loadCh(packet, radar, n, ch2);
        mean_power1 += std::norm(v1[static_cast<std::size_t>(n)]);
        mean_power2 += std::norm(v2[static_cast<std::size_t>(n)]);
    }
    mean_power1 /= static_cast<double>(n_samples);
    mean_power2 /= static_cast<double>(n_samples);

    std::mt19937_64 rng(packetSeed(random_seed, period_id, beam_id, pulse_id));
    std::normal_distribution<double> normal(0.0, 1.0);
    std::uniform_real_distribution<double> uniform(0.0, 1.0);
    std::mt19937_64 beam_rng(packetSeed(random_seed ^ 0x51f15e5dULL,
                                       period_id, beam_id, 0));
    const double jitter_deg = cfg.channel_phase_jitter_std_deg * normal(rng);
    const double beam_bias_deg = cfg.per_beam_phase_bias_deg * normal(beam_rng);
    double baseline_phase_deg = 0.0;
    if (cfg.baseline_error_m != 0.0 && radar.fc_hz > 0.0) {
        const double lambda = kCLocal / radar.fc_hz;
        baseline_phase_deg = 360.0 * cfg.baseline_error_m *
            std::sin(theta_cmd_deg * kPiLocal / 180.0) / lambda;
    }
    out.relative_phase_deg = cfg.channel_fixed_phase_mismatch_deg + jitter_deg +
        cfg.per_pulse_phase_drift_deg * static_cast<double>(pulse_id) +
        beam_bias_deg + baseline_phase_deg;
    out.relative_gain = std::pow(10.0, cfg.channel_amp_mismatch_db / 20.0);
    out.effective_shift_samples = cfg.channel_range_shift_samples +
        cfg.channel_time_delay_ns * 1.0e-9 * radar.fs_hz;
    out.channel_dropped = cfg.channel_drop_probability > 0.0 &&
        uniform(rng) < std::min(1.0, cfg.channel_drop_probability);

    if (cfg.channel_noise_power_ratio_db > 0.0) {
        const double extra = std::pow(10.0, cfg.channel_noise_power_ratio_db / 10.0) - 1.0;
        out.added_noise_sigma_ch2 = std::sqrt(std::max(0.0, mean_power1 * extra * 0.5));
    } else if (cfg.channel_noise_power_ratio_db < 0.0) {
        const double extra = std::pow(10.0, -cfg.channel_noise_power_ratio_db / 10.0) - 1.0;
        out.added_noise_sigma_ch1 = std::sqrt(std::max(0.0, mean_power2 * extra * 0.5));
    }

    const double phase_rad = out.relative_phase_deg * kPiLocal / 180.0;
    const std::complex<float> relative_gain_phase(
        static_cast<float>(out.relative_gain * std::cos(phase_rad)),
        static_cast<float>(out.relative_gain * std::sin(phase_rad)));
    for (int n = 0; n < n_samples; ++n) {
        std::complex<float> a = v1[static_cast<std::size_t>(n)];
        const double source = static_cast<double>(n) - out.effective_shift_samples -
            static_cast<double>(n) * cfg.sample_clock_error_ppm * 1.0e-6;
        std::complex<float> b = interpolate(v2, source) * relative_gain_phase;
        b = applyIqImbalance(b, cfg.iq_gain_imbalance_db,
                             cfg.iq_phase_imbalance_deg);
        if (out.added_noise_sigma_ch1 > 0.0) {
            a += std::complex<float>(
                static_cast<float>(out.added_noise_sigma_ch1 * normal(rng)),
                static_cast<float>(out.added_noise_sigma_ch1 * normal(rng)));
        }
        if (out.added_noise_sigma_ch2 > 0.0) {
            b += std::complex<float>(
                static_cast<float>(out.added_noise_sigma_ch2 * normal(rng)),
                static_cast<float>(out.added_noise_sigma_ch2 * normal(rng)));
        }
        if (out.channel_dropped) b = std::complex<float>(0.0f, 0.0f);
        a = clipMagnitude(a, cfg.channel_saturation_level,
                          out.saturated_sample_count);
        b = clipMagnitude(b, cfg.channel_saturation_level,
                          out.saturated_sample_count);
        storeCh(packet, radar, n, ch1, a);
        storeCh(packet, radar, n, ch2, b);
    }
    out.applied = true;
    return out;
}

std::string channelImpairmentConfigJson(const ChannelImpairmentConfig &c)
{
    std::ostringstream os;
    os << std::setprecision(17)
       << "{\n"
       << "  \"status\": \"implemented\",\n"
       << "  \"injection_order\": \"after_echo_superposition_before_final_protocol_write\",\n"
       << "  \"enabled\": " << (c.enabled ? "true" : "false") << ",\n"
       << "  \"strict_zero_bypass\": " << (channelImpairmentsAreZero(c) ? "true" : "false") << ",\n"
       << "  \"channel_amp_mismatch_db\": " << c.channel_amp_mismatch_db << ",\n"
       << "  \"channel_fixed_phase_mismatch_deg\": " << c.channel_fixed_phase_mismatch_deg << ",\n"
       << "  \"channel_phase_jitter_std_deg\": " << c.channel_phase_jitter_std_deg << ",\n"
       << "  \"channel_range_shift_samples\": " << c.channel_range_shift_samples << ",\n"
       << "  \"channel_time_delay_ns\": " << c.channel_time_delay_ns << ",\n"
       << "  \"channel_noise_power_ratio_db\": " << c.channel_noise_power_ratio_db << ",\n"
       << "  \"iq_gain_imbalance_db\": " << c.iq_gain_imbalance_db << ",\n"
       << "  \"iq_phase_imbalance_deg\": " << c.iq_phase_imbalance_deg << ",\n"
       << "  \"baseline_error_m\": " << c.baseline_error_m << ",\n"
       << "  \"per_pulse_phase_drift_deg\": " << c.per_pulse_phase_drift_deg << ",\n"
       << "  \"per_beam_phase_bias_deg\": " << c.per_beam_phase_bias_deg << ",\n"
       << "  \"sample_clock_error_ppm\": " << c.sample_clock_error_ppm << ",\n"
       << "  \"channel_drop_probability\": " << c.channel_drop_probability << ",\n"
       << "  \"channel_saturation_level\": " << c.channel_saturation_level << "\n"
       << "}\n";
    return os.str();
}

} // namespace target_injection
} // namespace gmti
