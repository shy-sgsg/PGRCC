#include "lfm_echo_generator.h"
#include "../common/SimulationGeometry.h"
#include "ctdr_phase_model.hpp"
#include "dbs/NewProtocolLayout.hpp"

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstring>

namespace gmti {
namespace target_injection {

namespace {

void storeU16LE(std::vector<uint8_t> &b, size_t off, uint16_t v)
{
    b[off] = static_cast<uint8_t>(v & 0xffU);
    b[off + 1] = static_cast<uint8_t>((v >> 8) & 0xffU);
}

void storeI16LE(std::vector<uint8_t> &b, size_t off, int16_t v)
{
    storeU16LE(b, off, static_cast<uint16_t>(v));
}

void storeU32LE(std::vector<uint8_t> &b, size_t off, uint32_t v)
{
    for (int i = 0; i < 4; ++i) b[off + static_cast<size_t>(i)] = static_cast<uint8_t>((v >> (8 * i)) & 0xffU);
}

void storeU64LE(std::vector<uint8_t> &b, size_t off, uint64_t v)
{
    for (int i = 0; i < 8; ++i) b[off + static_cast<size_t>(i)] = static_cast<uint8_t>((v >> (8 * i)) & 0xffU);
}

void storeF64LE(std::vector<uint8_t> &b, size_t off, double v)
{
    uint64_t raw = 0;
    std::memcpy(&raw, &v, sizeof(double));
    storeU64LE(b, off, raw);
}

double wrapPiLocal(double x)
{
    x = std::fmod(x + kPi, 2.0 * kPi);
    if (x < 0.0) x += 2.0 * kPi;
    return x - kPi;
}

std::size_t protocolChannelCount(const RadarConfig &radar)
{
    return static_cast<std::size_t>(std::max(2, radar.new_protocol_channel_count));
}

std::complex<float> loadComplexCh(const std::vector<uint8_t> &packet,
                                  const RadarConfig &radar,
                                  int n,
                                  int ch)
{
    const std::string iq_type = radar.iq_data_type.empty() ? "float32" : radar.iq_data_type;
    const std::size_t iq_bytes = gmti::new_protocol::bytesPerIq(iq_type);
    const std::size_t channel_count = protocolChannelCount(radar);
    const size_t off = gmti::new_protocol::kHeaderBytes +
                       static_cast<size_t>(n) * gmti::new_protocol::sampleBytes(channel_count, iq_type) +
                       gmti::new_protocol::channelOffset(static_cast<size_t>(ch), iq_type);
    return std::complex<float>(
        gmti::new_protocol::loadIqAsFloat(&packet[off], iq_type),
        gmti::new_protocol::loadIqAsFloat(&packet[off + iq_bytes], iq_type));
}

void storeComplexCh(std::vector<uint8_t> &packet,
                    const RadarConfig &radar,
                    int n,
                    int ch,
                    const std::complex<float> &v)
{
    const std::string iq_type = radar.iq_data_type.empty() ? "float32" : radar.iq_data_type;
    const std::size_t iq_bytes = gmti::new_protocol::bytesPerIq(iq_type);
    const std::size_t channel_count = protocolChannelCount(radar);
    const size_t off = gmti::new_protocol::kHeaderBytes +
                       static_cast<size_t>(n) * gmti::new_protocol::sampleBytes(channel_count, iq_type) +
                       gmti::new_protocol::channelOffset(static_cast<size_t>(ch), iq_type);
    gmti::new_protocol::storeIqFromFloat(&packet[off], iq_type, v.real());
    gmti::new_protocol::storeIqFromFloat(&packet[off + iq_bytes], iq_type, v.imag());
}

double estimateLocalRms(const std::vector<uint8_t> &packet, const RadarConfig &radar, int center, double floor_value)
{
    const int half = 64;
    const int lo = std::max(0, center - half);
    const int hi = std::min(radar.pulse_len - 1, center + half);
    double power = 0.0;
    int count = 0;
    const int ch1 = radar.new_protocol_read_channel_1;
    const int ch2 = radar.new_protocol_read_channel_2;
    for (int n = lo; n <= hi; ++n) {
        const std::complex<float> c1 = loadComplexCh(packet, radar, n, ch1);
        const std::complex<float> c2 = loadComplexCh(packet, radar, n, ch2);
        power += static_cast<double>(std::norm(c1) + std::norm(c2)) * 0.5;
        ++count;
    }
    if (count <= 0) return floor_value;
    return std::max(floor_value, std::sqrt(power / static_cast<double>(count)));
}

gmti::sim_geometry::LocalPoint toLocalPoint(const Vec3 &v)
{
    return gmti::sim_geometry::LocalPoint(v.x, v.y, v.z);
}

gmti::sim_geometry::LocalVelocity toLocalVelocity(const Vec3 &v)
{
    return gmti::sim_geometry::LocalVelocity(v.x, v.y, v.z);
}

gmti::sim_geometry::GeoPoint localToGeoPose(const TargetGlobalConfig &global, const Vec3 &local)
{
    const gmti::sim_geometry::ENUPoint enu =
        gmti::sim_geometry::localToEnu(toLocalPoint(local), global.geometry);
    return gmti::sim_geometry::enuToLatLon(enu.e, enu.n, local.z, global.geometry);
}

GeometrySample geometryAtTime(const RadarConfig &radar,
                              const TargetGlobalConfig &global,
                              const TargetConfig &target,
                              int beam_id,
                              double time_sec)
{
    GeometrySample g;
    g.time_sec = time_sec;
    g.theta_cmd_deg = radar.scan_min_deg + radar.scan_step_deg * static_cast<double>(beam_id);
    g.theta_true_deg = g.theta_cmd_deg;
    g.platform = evaluatePlatformState(global, time_sec);
    g.target = evaluateTargetState(target, time_sec);
    g.los = g.target.position - g.platform.position;
    g.range_m = norm(g.los);
    if (g.range_m > 0.0) {
        g.los_unit = g.los * (1.0 / g.range_m);
    }
    g.tau_abs_sec = 2.0 * g.range_m / kC;
    g.tau_rel_sec = g.tau_abs_sec - radar.sample_delay_sec;
    g.range_sample_float = g.tau_rel_sec * radar.fs_hz;
    g.range_sample_int = static_cast<int>(std::floor(g.range_sample_float + 0.5));
    g.in_range_window = (g.range_sample_float >= 0.0 &&
                         g.range_sample_float < static_cast<double>(radar.pulse_len));

    const gmti::sim_geometry::ENUPoint platform_enu =
        gmti::sim_geometry::localToEnu(toLocalPoint(g.platform.position), global.geometry);
    const gmti::sim_geometry::ENUPoint target_enu =
        gmti::sim_geometry::localToEnu(toLocalPoint(g.target.position), global.geometry);
    const gmti::sim_geometry::ENUVelocity platform_vel =
        gmti::sim_geometry::localVelocityToEnu(toLocalVelocity(g.platform.velocity), global.geometry);
    const double de = target_enu.e - platform_enu.e;
    const double dn = target_enu.n - platform_enu.n;
    const double horizontal = std::sqrt(de * de + dn * dn);
    const gmti::sim_geometry::LookVectorEN look =
        gmti::sim_geometry::makeAlgorithmLookVectorEN(platform_vel.ve,
                                                      platform_vel.vn,
                                                      g.theta_true_deg,
                                                      global.geometry);
    if (horizontal > 1.0e-9) {
        const double ue = de / horizontal;
        const double un = dn / horizontal;
        const double dot_en = std::max(-1.0, std::min(1.0, look.east * ue + look.north * un));
        const double cross_en = look.east * un - look.north * ue;
        g.angle_error_deg = rad2deg(std::atan2(cross_en, dot_en));
    } else {
        g.angle_error_deg = 0.0;
    }
    g.target_azimuth_deg = wrapTo180(g.theta_true_deg + g.angle_error_deg);
    const Vec3 rel_v = g.target.velocity - g.platform.velocity;
    g.radial_velocity_mps = dot(rel_v, g.los_unit);
    return g;
}

void addForwardEchoForChannel(std::vector<uint8_t> &packet,
                              const RadarConfig &radar,
                              const TargetGlobalConfig &global,
                              const GeometrySample &geom,
                              int channel,
                              double amplitude,
                              double beam_gain,
                              double lambda,
                              double kr,
                              int &count)
{
    if (!geom.in_range_window || amplitude <= 0.0 || beam_gain <= 0.0) {
        return;
    }
    const bool center_reference = global.lfm_time_reference != "legacy_start" &&
                                  global.lfm_time_reference != "start";
    const double channel_tau_rel_sec =
        receiveChannelPathLength(radar, geom, channel) / kC -
        radar.sample_delay_sec;
    const double support_start_sec = center_reference
        ? channel_tau_rel_sec - 0.5 * radar.tr_sec
        : channel_tau_rel_sec;
    const double support_end_sec = center_reference
        ? channel_tau_rel_sec + 0.5 * radar.tr_sec
        : channel_tau_rel_sec + radar.tr_sec;
    const int n_start = static_cast<int>(std::floor(
        support_start_sec * radar.fs_hz));
    const int n_end = std::min(
        radar.pulse_len,
        static_cast<int>(std::ceil(support_end_sec * radar.fs_hz)));
    if (n_end <= 0 || n_start >= radar.pulse_len) {
        return;
    }
    const double carrier_phase =
        receiveChannelPhaseRad(radar, global, geom, channel, lambda);
    const std::complex<double> carrier = std::exp(std::complex<double>(0.0, carrier_phase));
    for (int n = std::max(0, n_start); n < n_end; ++n) {
        const double t_fast = static_cast<double>(n) / radar.fs_hz;
        const double dt = t_fast - channel_tau_rel_sec;
        if (center_reference) {
            if (dt < -0.5 * radar.tr_sec || dt >= 0.5 * radar.tr_sec) continue;
        } else if (dt < 0.0 || dt >= radar.tr_sec) {
            continue;
        }
        const double chirp_phase =
            static_cast<double>(global.chirp_phase_sign) * kPi * kr * dt * dt;
        const std::complex<double> echo =
            amplitude * beam_gain *
            std::exp(std::complex<double>(0.0, chirp_phase)) * carrier;
        const std::complex<float> e(static_cast<float>(echo.real()), static_cast<float>(echo.imag()));
        const std::complex<float> cur = loadComplexCh(packet, radar, n, channel);
        storeComplexCh(packet, radar, n, channel, cur + e);
        ++count;
    }
}

} // namespace

Vec3 receiveChannelOffsetLocal(const RadarConfig &radar,
                               const PlatformState &platform,
                               int channel_1based)
{
    if (channel_1based >= 1 &&
        static_cast<std::size_t>(channel_1based) <= radar.channel_offsets_local_m.size()) {
        return radar.channel_offsets_local_m[static_cast<std::size_t>(channel_1based - 1)];
    }
    if (!(radar.d_chan_m > 0.0)) {
        return Vec3(0.0, 0.0, 0.0);
    }
    // Keep the fallback in the local-array frame.  The GeometrySample
    // overload applies the commanded mechanical yaw to this offset for every
    // PRT; constructing it from the global platform velocity would rotate the
    // same vector twice or silently assume a velocity-aligned array.
    (void)platform;
    double sign = 0.0;
    if (channel_1based == radar.new_protocol_read_channel_1) sign = -1.0;
    if (channel_1based == radar.new_protocol_read_channel_2) sign = 1.0;
    return Vec3(sign * 0.5 * radar.d_chan_m, 0.0, 0.0);
}

Vec3 receiveChannelOffsetLocal(const RadarConfig &radar,
                               const GeometrySample &geom,
                               int channel_1based)
{
    const Vec3 base = receiveChannelOffsetLocal(
        radar, geom.platform, channel_1based);
    if (!radar.phase_center_rotation_enable) {
        return base;
    }
    const std::array<double, 3> rotated =
        gmti::ctdr::rotateLocalOffsetAzimuth(
            std::array<double, 3>{{base.x, base.y, base.z}},
            geom.theta_cmd_deg,
            radar.phase_center_mount_angle_deg,
            radar.phase_center_rotation_sign,
            true);
    return Vec3(rotated[0], rotated[1], rotated[2]);
}

double receiveChannelPathLength(const RadarConfig &radar,
                                const GeometrySample &geom,
                                int channel_1based)
{
    // Common-transmit receive-channel model: every channel uses the same TX
    // phase centre and its own RX phase centre.  The old implementation used
    // 2*distance(target, phase_center), which was an equivalent-monostatic
    // model and did not match the CTDR model used by localization/CSI.
    const Vec3 tx_phase_center = geom.platform.position;
    const Vec3 rx_phase_center =
        geom.platform.position +
        receiveChannelOffsetLocal(radar, geom, channel_1based);
    return norm(geom.target.position - tx_phase_center) +
           norm(geom.target.position - rx_phase_center);
}

double receiveChannelPhaseRad(const RadarConfig &radar,
                              const TargetGlobalConfig &global,
                              const GeometrySample &geom,
                              int channel_1based,
                              double lambda)
{
    return static_cast<double>(global.carrier_phase_sign) * 2.0 * kPi *
           receiveChannelPathLength(radar, geom, channel_1based) / lambda;
}

float loadF32LE(const uint8_t *p)
{
    uint32_t raw = 0;
    raw |= static_cast<uint32_t>(p[0]);
    raw |= static_cast<uint32_t>(p[1]) << 8;
    raw |= static_cast<uint32_t>(p[2]) << 16;
    raw |= static_cast<uint32_t>(p[3]) << 24;
    float v = 0.0f;
    std::memcpy(&v, &raw, sizeof(float));
    return v;
}

void storeF32LE(uint8_t *p, float v)
{
    uint32_t raw = 0;
    std::memcpy(&raw, &v, sizeof(float));
    p[0] = static_cast<uint8_t>(raw & 0xffU);
    p[1] = static_cast<uint8_t>((raw >> 8) & 0xffU);
    p[2] = static_cast<uint8_t>((raw >> 16) & 0xffU);
    p[3] = static_cast<uint8_t>((raw >> 24) & 0xffU);
}

void fillZeroPacketHeader(std::vector<uint8_t> &packet,
                          const RadarConfig &radar,
                          const TargetGlobalConfig &global,
                          uint32_t prt_counter,
                          double utc,
                          double theta_deg)
{
    std::fill(packet.begin(), packet.end(), 0U);
    const uint32_t prt_len = static_cast<uint32_t>(packet.size());
    storeU64LE(packet, gmti::new_protocol::kOffMagicHead, 0x5A5A5A5A5A5A5A5AULL);
    // FPGA protocol V1.5: WAGMTI PRT mode is 9.  Mode 5 is SAR 3 m.
    packet[gmti::new_protocol::kOffVersion] = 9;
    storeU32LE(packet, gmti::new_protocol::kOffPrtLen, prt_len);
    storeF32LE(&packet[gmti::new_protocol::kOffUtc], static_cast<float>(utc));
    storeU32LE(packet, gmti::new_protocol::kOffPrtCounter, prt_counter);
    packet[88] = 0x02;
    packet[90] = 0x40;
    packet[92] = 0x0B;
    packet[93] = 0x01;
    storeI16LE(packet, 94, 0);
    storeU32LE(packet, 96, static_cast<uint32_t>(std::floor(utc * 1000.0 + 0.5)));
    // The simulated navigation solution updates at 25 Hz.  Keep the PRT UTC
    // continuous, but emit the latest navigation sample unchanged for the
    // following 40 ms, exactly as the new-protocol receiver does between INS
    // updates.  Echo geometry remains evaluated at the actual PRT time.
    constexpr double kInsUpdateHz = 25.0;
    const double ins_utc = std::floor(utc * kInsUpdateHz + 1.0e-9) /
                           kInsUpdateHz;
    const PlatformState platform = evaluatePlatformState(global, ins_utc);
    const gmti::sim_geometry::PosSample pos =
        gmti::sim_geometry::makeProtocolPosSample(toLocalPoint(platform.position),
                                                  toLocalVelocity(platform.velocity),
                                                  global.geometry);
    storeF64LE(packet, gmti::new_protocol::kOffLatDeg, pos.lat_deg);
    storeF64LE(packet, gmti::new_protocol::kOffLonDeg, pos.lon_deg);
    storeF64LE(packet, gmti::new_protocol::kOffHeightM, pos.height_m);
    storeF32LE(&packet[gmti::new_protocol::kOffVnMps], static_cast<float>(pos.vn_mps));
    storeF32LE(&packet[gmti::new_protocol::kOffVeMps], static_cast<float>(pos.ve_mps));
    storeF32LE(&packet[gmti::new_protocol::kOffVdMps], static_cast<float>(pos.vd_mps));
    storeF32LE(&packet[gmti::new_protocol::kOffSpeedMps], static_cast<float>(global.platform_speed_mps));
    packet[gmti::new_protocol::kOffPrtLowByte] = static_cast<uint8_t>(prt_counter & 0xffU);
    storeI16LE(packet, gmti::new_protocol::kOffThetaDegX100,
               gmti::new_protocol::satI16FromDouble(theta_deg * 100.0));
    storeU64LE(packet, gmti::new_protocol::kOffMagicTail, 0x5B5B5B5B5B5B5B5BULL);
    (void)radar;
}

void fillMechanicalPacketHeader(std::vector<uint8_t> &packet,
                                const RadarConfig &radar,
                                const TargetGlobalConfig &global,
                                uint32_t prt_counter,
                                double utc,
                                double servo_azimuth_deg,
                                double servo_elevation_deg,
                                double scan_center_azimuth_deg,
                                double scan_center_elevation_deg,
                                double scan_speed_deg_s,
                                double scan_extent_deg)
{
    fillZeroPacketHeader(packet, radar, global, prt_counter, utc,
                         servo_azimuth_deg);
    storeI16LE(packet, gmti::new_protocol::kOffServoElevationDegX100,
               gmti::new_protocol::satI16FromDouble(servo_elevation_deg * 100.0));
    storeI16LE(packet, gmti::new_protocol::kOffScanCenterAzimuthDegX100,
               gmti::new_protocol::satI16FromDouble(scan_center_azimuth_deg * 100.0));
    storeI16LE(packet, gmti::new_protocol::kOffScanCenterElevationDegX100,
               gmti::new_protocol::satI16FromDouble(scan_center_elevation_deg * 100.0));
    const long speed_x2 = std::lround(std::max(0.0, scan_speed_deg_s) * 2.0);
    packet[gmti::new_protocol::kOffScanSpeedAzDegPerSecX2] =
        static_cast<uint8_t>(std::min(255L, speed_x2));
    storeU16LE(packet, gmti::new_protocol::kOffScanExtentAzDegX100,
               static_cast<uint16_t>(std::min(
                   65535L, std::max(0L, std::lround(scan_extent_deg * 100.0)))));
}

PulseTruth injectOnePulse(std::vector<uint8_t> &packet,
                          const RadarConfig &radar,
                          const TargetGlobalConfig &global,
                          const TargetConfig &target,
                          int period_id,
                          int beam_id,
                          int pulse_id,
                          const std::vector<uint8_t> *background_packet,
                          double time_override_sec,
                          double servo_azimuth_override_deg,
                          bool apply_discrete_beam_membership,
                          int scan_id,
                          int prt_id,
                          double servo_elevation_deg)
{
    PulseTruth truth;
    truth.period_id = period_id;
    truth.beam_id = beam_id;
    truth.pulse_id = pulse_id;
    truth.scan_id = scan_id;
    truth.prt_id = prt_id;
    truth.servo_azimuth_deg = servo_azimuth_override_deg;
    truth.servo_elevation_deg = servo_elevation_deg;
    truth.target_id = target.id;
    truth.target_name = target.name;
    truth.has_ref_geometry = target.has_ref_geometry;
    truth.ref_pulse_idx = target.ref_pulse_idx;
    truth.ref_time_s = target.ref_time_s;
    truth.ref_platform = target.ref_platform;
    truth.ref_target = target.ref_target;
    truth.ref_range_m = target.ref_range_m;
    truth.ref_range_sample_float = target.ref_range_sample_float;
    truth.ref_range_sample_int = target.ref_range_sample_int;
    truth.echo_delay_sample_center_used = target.echo_delay_sample_center_used;
    truth.moving_target_speed_mps = target.override_speed_mps;
    truth.rcs_db = target.override_rcs_db;
    truth.snr_db = std::isfinite(target.target_snr_db)
        ? target.target_snr_db
        : global.target_snr_db;
    truth.target_ve_mps = target.target_ve_mps;
    truth.target_vn_mps = target.target_vn_mps;
    truth.geom = std::isfinite(time_override_sec) &&
                         std::isfinite(servo_azimuth_override_deg)
        ? evaluateGeometryAtTimeAndAngle(radar, global, target,
                                         time_override_sec,
                                         servo_azimuth_override_deg)
        : evaluateGeometry(radar, global, target, period_id, beam_id, pulse_id);
    if (!std::isfinite(truth.servo_azimuth_deg)) {
        truth.servo_azimuth_deg = truth.geom.theta_true_deg;
    }
    // These are observation-time truth values.  The TargetConfig values are
    // defined at the scenario reference pulse and must not be repeated across
    // later scans when the line of sight has changed.
    truth.target_vr_self_mps = dot(truth.geom.target.velocity, truth.geom.los_unit);
    const double target_speed_sq = dot(truth.geom.target.velocity,
                                       truth.geom.target.velocity);
    const double target_vt_magnitude = std::sqrt(std::max(
        0.0, target_speed_sq -
        truth.target_vr_self_mps * truth.target_vr_self_mps));
    truth.target_vt_self_mps =
        std::isfinite(target.target_vt_self_mps) && target.target_vt_self_mps < 0.0
            ? -target_vt_magnitude : target_vt_magnitude;
    const double lambda = kC / radar.fc_hz;
    {
        const gmti::sim_geometry::GeoPoint platform_geo = localToGeoPose(global, truth.geom.platform.position);
        const gmti::sim_geometry::GeoPoint target_geo = localToGeoPose(global, truth.geom.target.position);
        truth.platform_e = platform_geo.e;
        truth.platform_n = platform_geo.n;
        truth.platform_lat = platform_geo.lat;
        truth.platform_lon = platform_geo.lon;
        truth.target_e = target_geo.e;
        truth.target_n = target_geo.n;
        truth.target_lat = target_geo.lat;
        truth.target_lon = target_geo.lon;
        const Vec3 ref_platform = target.has_ref_geometry ? target.ref_platform : truth.geom.platform.position;
        const Vec3 ref_target = target.has_ref_geometry ? target.ref_target : truth.geom.target.position;
        const gmti::sim_geometry::GeoPoint ref_platform_geo = localToGeoPose(global, ref_platform);
        const gmti::sim_geometry::GeoPoint ref_target_geo = localToGeoPose(global, ref_target);
        truth.ref_platform_e = ref_platform_geo.e;
        truth.ref_platform_n = ref_platform_geo.n;
        truth.ref_platform_lat = ref_platform_geo.lat;
        truth.ref_platform_lon = ref_platform_geo.lon;
        truth.ref_target_e = ref_target_geo.e;
        truth.ref_target_n = ref_target_geo.n;
        truth.ref_target_lat = ref_target_geo.lat;
        truth.ref_target_lon = ref_target_geo.lon;
        const gmti::sim_geometry::ENUVelocity ref_vel_en =
            gmti::sim_geometry::localVelocityToEnu(
                gmti::sim_geometry::LocalVelocity(truth.geom.platform.velocity.x,
                                                  truth.geom.platform.velocity.y,
                                                  truth.geom.platform.velocity.z),
                global.geometry);
        const gmti::sim_geometry::LookVectorEN look =
            gmti::sim_geometry::makeAlgorithmLookVectorEN(ref_vel_en.ve,
                                                          ref_vel_en.vn,
                                                          truth.geom.theta_true_deg,
                                                          global.geometry);
        truth.ref_platform_ve = ref_vel_en.ve;
        truth.ref_platform_vn = ref_vel_en.vn;
        truth.look_e = look.east;
        truth.look_n = look.north;
        truth.af_geometry_truth_hz =
            2.0 * (ref_vel_en.ve * look.east + ref_vel_en.vn * look.north) / lambda;
        // The emitted LFM phase is generated from the instantaneous
        // bistatic path length, i.e. from the relative target/platform
        // radial velocity in ``geom.radial_velocity_mps``.  The previous
        // truth calculation used target-only radial velocity and then added
        // a commanded-look platform term.  That is only equivalent when the
        // target is exactly on the beam centre.  At a beam edge the target
        // LOS and commanded look differ, so the omitted platform projection
        // moves the expected Doppler by several FFT cells and makes a real
        // target appear to miss its own truth row.
        const double f_bistatic =
            static_cast<double>(radar.motion_doppler_axis_sign) * 2.0 *
            truth.geom.radial_velocity_mps / lambda;
        // Keep af_geometry_truth_hz on the same commanded-look axis used by
        // the production processor.  The motion field is therefore the
        // residual needed to obtain the physically emitted total Doppler;
        // af_total_truth_hz is the authoritative row/matching quantity.
        truth.af_total_truth_hz = f_bistatic;
        truth.af_motion_truth_hz = truth.af_total_truth_hz -
                                   truth.af_geometry_truth_hz;
        const double fd_res = (radar.pulse_num > 0) ? (radar.prf_hz / static_cast<double>(radar.pulse_num)) : 0.0;
        if (fd_res > 0.0 && std::isfinite(truth.af_total_truth_hz) &&
            std::isfinite(truth.af_geometry_truth_hz)) {
            const double first = truth.af_geometry_truth_hz - 0.5 * radar.prf_hz;
            truth.row_truth = static_cast<int>(std::floor((truth.af_total_truth_hz - first) / fd_res + 0.5));
        }
        truth.slant_range_m = truth.geom.range_m;
        truth.ground_range_m =
            gmti::sim_geometry::slantRangeToGroundRange(truth.slant_range_m,
                                                        truth.geom.platform.position.z,
                                                        truth.geom.target.position.z,
                                                        global.geometry);
        truth.expected_range_bin =
            static_cast<int>(std::floor(
                (truth.geom.range_sample_float - radar.range_crop_start) + 0.5));
        truth.geometry_config_name = global.geometry.geometry_config_name;
    }
    TargetGlobalConfig target_global = global;
    if (!target.visibility_mode.empty()) {
        target_global.visibility_mode = target.visibility_mode;
    }
    const VisibilityResult vr = evaluateVisibility(radar, target_global, truth.geom.angle_error_deg);
    truth.beam_gain = vr.beam_gain;
    truth.visible_by_beam = vr.visible;
    if (apply_discrete_beam_membership &&
        (target.single_beam_only || target.visible_beam_span >= 0)) {
        const int span = target.single_beam_only ? 0 : target.visible_beam_span;
        if (target.ref_beam_id >= 0 && std::abs(beam_id - target.ref_beam_id) > span) {
            truth.visible_by_beam = false;
            truth.beam_gain = 0.0;
        }
    }
    truth.target_period_enabled = target.enabled &&
                                  period_id >= target.start_period &&
                                  period_id <= target.end_period;
    truth.channel_path_length_m.resize(
        static_cast<std::size_t>(std::max(2, radar.new_protocol_channel_count)));
    truth.channel_phase_rad.resize(truth.channel_path_length_m.size());
    for (std::size_t ch = 0; ch < truth.channel_path_length_m.size(); ++ch) {
        truth.channel_path_length_m[ch] = receiveChannelPathLength(
            radar, truth.geom, static_cast<int>(ch + 1));
        truth.channel_phase_rad[ch] = receiveChannelPhaseRad(
            radar, global, truth.geom, static_cast<int>(ch + 1), lambda);
    }
    const double ch1_phase = truth.channel_phase_rad[
        static_cast<std::size_t>(radar.new_protocol_read_channel_1 - 1)];
    const double ch2_phase = truth.channel_phase_rad[
        static_cast<std::size_t>(radar.new_protocol_read_channel_2 - 1)];
    truth.phi_total_unwrapped_truth_rad = ch1_phase - ch2_phase;
    truth.delta_phi_ch_rad = wrapPiLocal(truth.phi_total_unwrapped_truth_rad);
    truth.phi_total_truth_rad = truth.delta_phi_ch_rad;
    truth.phase_ambiguity_order_truth = static_cast<int>(std::llround(
        (truth.phi_total_unwrapped_truth_rad - truth.phi_total_truth_rad) /
        (2.0 * kPi)));
    truth.phi_static_truth_rad = truth.delta_phi_ch_rad;
    truth.phi_motion_truth_rad = wrapPiLocal(truth.phi_total_truth_rad - truth.phi_static_truth_rad);
    // A multi-target packet must give every target the same target-free
    // clutter/noise reference.  Otherwise a target injected earlier can enter
    // the local RMS estimate of a later nearby target, making the configured
    // SNR depend on JSON array order and artificially amplifying dense scenes.
    const std::vector<uint8_t> &rms_packet =
        background_packet ? *background_packet : packet;
    truth.local_background_rms = estimateLocalRms(
        rms_packet, radar, truth.geom.range_sample_int, global.rms_floor);
    if (global.amplitude_mode == "direct_amplitude") {
        truth.target_amplitude = global.direct_amplitude;
    } else {
        const double snr_db = std::isfinite(target.target_snr_db)
            ? target.target_snr_db
            : global.target_snr_db;
        // local_background_rms is measured in the raw-LFM stream.  Distributed
        // surface clutter has already accumulated the incoherent convolution
        // gain sqrt(Tr*fs), while a point target receives the full coherent
        // gain only during pulse compression.  Using the raw RMS directly as
        // the point-chirp amplitude therefore overstates the configured
        // post-compression SNR by Tr*fs in power (about 39 dB for this scene).
        // Remove that raw convolution gain so snr_db describes the compressed
        // range cell, which is the domain consumed by CSI/CFAR.
        const double coherent_samples = std::max(1.0, radar.tr_sec * radar.fs_hz);
        truth.target_amplitude = truth.local_background_rms *
            std::pow(10.0, snr_db / 20.0) / std::sqrt(coherent_samples);
    }
    truth.injection_enabled = truth.target_period_enabled &&
                              truth.geom.in_range_window &&
                              truth.visible_by_beam;
    if (!truth.injection_enabled) return truth;

    const double kr = radar.br_hz / radar.tr_sec;
    int max_count = 0;
    for (int ch = 1; ch <= std::max(2, radar.new_protocol_channel_count); ++ch) {
        int count = 0;
        addForwardEchoForChannel(packet, radar, global, truth.geom, ch,
                                 truth.target_amplitude, truth.beam_gain,
                                 lambda, kr, count);
        max_count = std::max(max_count, count);
    }
    truth.injected_sample_count = max_count;
    return truth;
}

} // namespace target_injection
} // namespace gmti
