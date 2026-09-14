#include "lfm_echo_generator.h"

#include <cmath>
#include <iostream>

namespace {

bool close(double actual, double expected)
{
    return std::abs(actual - expected) <= 1.0e-12;
}

} // namespace

int main()
{
    using gmti::target_injection::PlatformState;
    using gmti::target_injection::RadarConfig;
    using gmti::target_injection::Vec3;

    RadarConfig radar;
    radar.new_protocol_channel_count = 4;
    radar.channel_offsets_local_m = {
        Vec3(-0.085, 0.0, 0.085), Vec3(0.085, 0.0, 0.085),
        Vec3(-0.085, 0.0, -0.085), Vec3(0.085, 0.0, -0.085)};
    radar.true_channel_offsets_local_m = radar.channel_offsets_local_m;
    radar.true_channel_offsets_local_m[1].x += 0.0025;
    radar.true_channel_offsets_local_m[3].x += 0.0025;

    const PlatformState platform{Vec3(0.0, 0.0, 6000.0), Vec3(60.0, 0.0, 0.0)};
    radar.channel_geometry_mode = "reported_channel_positions";
    const Vec3 reported = gmti::target_injection::receiveChannelOffsetLocal(
        radar, platform, 2);
    radar.channel_geometry_mode = "true_channel_positions";
    const Vec3 truth = gmti::target_injection::receiveChannelOffsetLocal(
        radar, platform, 2);

    const bool pass = close(reported.x, 0.085) &&
                      close(truth.x, 0.0875) &&
                      close(reported.z, truth.z);
    std::cout << (pass ? "[PASS] " : "[FAIL] ")
              << "echo geometry selects true positions only in true mode\n";
    return pass ? 0 : 1;
}
