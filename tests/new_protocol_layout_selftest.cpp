#include "dbs/NewProtocolLayout.hpp"

#include <cmath>
#include <iostream>
#include <limits>
#include <vector>

int main()
{
    using gmti::new_protocol::satI16FromDouble;
    int failures = 0;
    const auto check = [&failures](bool condition, const char *name) {
        std::cout << (condition ? "[PASS] " : "[FAIL] ") << name << '\n';
        if (!condition) ++failures;
    };
    check(satI16FromDouble(-6000.0) == -6000,
          "exact negative beam angle is not biased by one LSB");
    check(satI16FromDouble(-1.4) == -1 &&
          satI16FromDouble(-1.5) == -2 &&
          satI16FromDouble(1.4) == 1 &&
          satI16FromDouble(1.5) == 2,
          "nearest rounding is symmetric around zero");
    check(satI16FromDouble(-40000.0) == -32768 &&
          satI16FromDouble(40000.0) == 32767,
          "int16 saturation bounds");
    check(satI16FromDouble(std::numeric_limits<double>::quiet_NaN()) == 0,
          "non-finite protocol scalar is deterministic");
    std::vector<std::uint8_t> header(gmti::new_protocol::kHeaderBytes, 0U);
    gmti::new_protocol::storeU32LE(
        header.data() + gmti::new_protocol::kOffPrtCounter, 123456U);
    gmti::new_protocol::storeF64LE(
        header.data() + gmti::new_protocol::kOffHeadingDeg, 87.25);
    gmti::new_protocol::storeI16LE(
        header.data() + gmti::new_protocol::kOffServoAzimuthDegX100, -1234);
    gmti::new_protocol::storeI16LE(
        header.data() + gmti::new_protocol::kOffServoElevationDegX100, -567);
    gmti::new_protocol::storeI16LE(
        header.data() + gmti::new_protocol::kOffScanCenterAzimuthDegX100, 250);
    gmti::new_protocol::storeI16LE(
        header.data() + gmti::new_protocol::kOffScanCenterElevationDegX100, -400);
    header[gmti::new_protocol::kOffScanSpeedAzDegPerSecX2] = 41U;
    gmti::new_protocol::storeI16LE(
        header.data() + gmti::new_protocol::kOffScanExtentAzDegX100, 6000);
    const gmti::new_protocol::HeaderSample sample =
        gmti::new_protocol::readHeaderSample(header.data());
    check(sample.prt_counter == 123456U &&
          std::abs(sample.heading_deg - 87.25) < 1.0e-12,
          "PRT counter and heading offsets decode exactly");
    check(std::abs(sample.servo_azimuth_deg + 12.34) < 1.0e-12 &&
          std::abs(sample.theta_cmd_deg - sample.servo_azimuth_deg) < 1.0e-12 &&
          std::abs(sample.servo_elevation_deg + 5.67) < 1.0e-12,
          "actual servo azimuth/elevation decode from offsets 218/220");
    check(std::abs(sample.scan_center_azimuth_deg - 2.5) < 1.0e-12 &&
          std::abs(sample.scan_center_elevation_deg + 4.0) < 1.0e-12 &&
          std::abs(sample.scan_speed_az_deg_s - 20.5) < 1.0e-12 &&
          std::abs(sample.scan_extent_az_deg - 60.0) < 1.0e-12,
          "mechanical scan command/status fields decode with protocol scaling");
    std::cout << "[NEW_PROTOCOL_LAYOUT_SELFTEST] failures=" << failures << '\n';
    return failures == 0 ? 0 : 1;
}
