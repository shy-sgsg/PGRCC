#include "channel_impairments.h"
#include "dbs/NewProtocolLayout.hpp"

#include <cmath>
#include <complex>
#include <iostream>
#include <vector>

namespace {

std::complex<float> load(const std::vector<std::uint8_t>& packet,
                         const gmti::target_injection::RadarConfig& radar,
                         int channel)
{
    const std::string type = radar.iq_data_type;
    const std::size_t iq_bytes = gmti::new_protocol::bytesPerIq(type);
    const std::size_t offset = gmti::new_protocol::kHeaderBytes +
        gmti::new_protocol::channelOffset(static_cast<std::size_t>(channel), type);
    return std::complex<float>(
        gmti::new_protocol::loadIqAsFloat(packet.data() + offset, type),
        gmti::new_protocol::loadIqAsFloat(packet.data() + offset + iq_bytes, type));
}

void fillPacket(std::vector<std::uint8_t>& packet,
                const gmti::target_injection::RadarConfig& radar)
{
    const std::string type = radar.iq_data_type;
    for (int channel = 1; channel <= radar.new_protocol_channel_count; ++channel) {
        const std::size_t offset = gmti::new_protocol::kHeaderBytes +
            gmti::new_protocol::channelOffset(static_cast<std::size_t>(channel), type);
        gmti::new_protocol::storeIqFromFloat(packet.data() + offset, type, 1.0f);
        gmti::new_protocol::storeIqFromFloat(
            packet.data() + offset + gmti::new_protocol::bytesPerIq(type), type, 0.0f);
    }
}

bool closeComplex(const std::complex<float>& actual,
                  const std::complex<float>& expected,
                  float tolerance)
{
    return std::abs(actual - expected) <= tolerance;
}

} // namespace

int main()
{
    int failures = 0;
    const auto check = [&failures](bool condition, const char* name) {
        std::cout << (condition ? "[PASS] " : "[FAIL] ") << name << '\n';
        if (!condition) ++failures;
    };

    gmti::target_injection::RadarConfig radar;
    radar.pulse_len = 1;
    radar.new_protocol_channel_count = 4;
    radar.new_protocol_read_channel_1 = 1;
    radar.new_protocol_read_channel_2 = 2;
    radar.iq_data_type = "float32";
    radar.fc_hz = 16.0e9;
    const std::size_t four_bytes = gmti::new_protocol::packetBytes(
        1U, 4U, radar.iq_data_type);

    gmti::target_injection::ChannelImpairmentConfig impairment;
    impairment.enabled = true;
    impairment.baseline_error_m = 0.01;
    std::vector<std::uint8_t> packet(four_bytes, 0U);
    fillPacket(packet, radar);
    const auto realization = gmti::target_injection::applyChannelImpairments(
        packet, radar, impairment, 0, 0, 0, 30.0, 20260913U);
    const double lambda = 299792458.0 / radar.fc_hz;
    const double phase = 2.0 * 3.14159265358979323846 * impairment.baseline_error_m *
        std::sin(30.0 * 3.14159265358979323846 / 180.0) / lambda;
    const std::complex<float> expected(
        static_cast<float>(std::cos(phase)), static_cast<float>(std::sin(phase)));
    check(realization.applied, "four-channel baseline impairment is applied");
    check(closeComplex(load(packet, radar, 1), {1.0f, 0.0f}, 1.0e-5f),
          "four-channel baseline leaves left-upper channel unchanged");
    check(closeComplex(load(packet, radar, 2), expected, 1.0e-5f),
          "four-channel baseline rotates right-upper channel");
    check(closeComplex(load(packet, radar, 3), {1.0f, 0.0f}, 1.0e-5f),
          "four-channel baseline leaves left-lower channel unchanged");
    check(closeComplex(load(packet, radar, 4), expected, 1.0e-5f),
          "four-channel baseline rotates right-lower channel");

    radar.new_protocol_channel_count = 2;
    const std::size_t two_bytes = gmti::new_protocol::packetBytes(
        1U, 2U, radar.iq_data_type);
    packet.assign(two_bytes, 0U);
    fillPacket(packet, radar);
    gmti::target_injection::applyChannelImpairments(
        packet, radar, impairment, 0, 0, 0, 30.0, 20260913U);
    check(closeComplex(load(packet, radar, 1), {1.0f, 0.0f}, 1.0e-5f),
          "two-channel compatibility leaves channel one unchanged");
    check(closeComplex(load(packet, radar, 2), expected, 1.0e-5f),
          "two-channel compatibility rotates channel two");

    std::cout << "[CHANNEL_IMPAIRMENTS_SELFTEST] failures=" << failures << '\n';
    return failures == 0 ? 0 : 1;
}
