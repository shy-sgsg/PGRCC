#pragma once

#include "beam_visibility.h"
#include "channel_phase_model.h"

#include <limits>
#include <vector>

namespace gmti {
namespace target_injection {

struct PulseTruth {
    int period_id = 0;
    int beam_id = 0;
    int pulse_id = 0;
    int scan_id = -1;
    int prt_id = -1;
    double servo_azimuth_deg = std::numeric_limits<double>::quiet_NaN();
    double servo_elevation_deg = std::numeric_limits<double>::quiet_NaN();
    int target_id = 0;
    std::string target_name;
    GeometrySample geom;
    double beam_gain = 0.0;
    bool visible_by_beam = false;
    bool target_period_enabled = false;
    bool injection_enabled = false;
    double local_background_rms = 0.0;
    double target_amplitude = 0.0;
    double delta_phi_ch_rad = 0.0;
    int injected_sample_count = 0;
    bool has_ref_geometry = false;
    int ref_pulse_idx = -1;
    double ref_time_s = 0.0;
    Vec3 ref_platform;
    Vec3 ref_target;
    double ref_range_m = 0.0;
    double ref_range_sample_float = 0.0;
    int ref_range_sample_int = 0;
    double echo_delay_sample_center_used = 0.0;
    double platform_e = 0.0;
    double platform_n = 0.0;
    double platform_lat = 0.0;
    double platform_lon = 0.0;
    double target_e = 0.0;
    double target_n = 0.0;
    double target_lat = 0.0;
    double target_lon = 0.0;
    double ref_platform_e = 0.0;
    double ref_platform_n = 0.0;
    double ref_platform_lat = 0.0;
    double ref_platform_lon = 0.0;
    double ref_target_e = 0.0;
    double ref_target_n = 0.0;
    double ref_target_lat = 0.0;
    double ref_target_lon = 0.0;
    double ref_platform_ve = 0.0;
    double ref_platform_vn = 0.0;
    double look_e = 0.0;
    double look_n = 0.0;
    double ground_range_m = 0.0;
    double slant_range_m = 0.0;
    int expected_range_bin = -1;
    std::string geometry_config_name;
    double moving_target_speed_mps = std::numeric_limits<double>::quiet_NaN();
    double rcs_db = std::numeric_limits<double>::quiet_NaN();
    double snr_db = std::numeric_limits<double>::quiet_NaN();
    double target_ve_mps = std::numeric_limits<double>::quiet_NaN();
    double target_vn_mps = std::numeric_limits<double>::quiet_NaN();
    double target_vr_self_mps = std::numeric_limits<double>::quiet_NaN();
    double target_vt_self_mps = std::numeric_limits<double>::quiet_NaN();
    double af_motion_truth_hz = std::numeric_limits<double>::quiet_NaN();
    double af_geometry_truth_hz = std::numeric_limits<double>::quiet_NaN();
    double af_total_truth_hz = std::numeric_limits<double>::quiet_NaN();
    double phi_total_truth_rad = std::numeric_limits<double>::quiet_NaN();
    double phi_total_unwrapped_truth_rad = std::numeric_limits<double>::quiet_NaN();
    int phase_ambiguity_order_truth = 0;
    double phi_static_truth_rad = std::numeric_limits<double>::quiet_NaN();
    double phi_motion_truth_rad = std::numeric_limits<double>::quiet_NaN();
    std::vector<double> channel_path_length_m;
    std::vector<double> channel_phase_rad;
    int row_truth = -1;
};

// Shared by Stage2 clutter/point-target generation and cooperative-target
// injection so every component uses exactly the same receive geometry.
Vec3 receiveChannelOffsetLocal(const RadarConfig &radar,
                               const PlatformState &platform,
                               int channel_1based);
Vec3 receiveChannelOffsetLocal(const RadarConfig &radar,
                               const GeometrySample &geom,
                               int channel_1based);
double receiveChannelPathLength(const RadarConfig &radar,
                                const GeometrySample &geom,
                                int channel_1based);
double receiveChannelPhaseRad(const RadarConfig &radar,
                              const TargetGlobalConfig &global,
                              const GeometrySample &geom,
                              int channel_1based,
                              double lambda);

struct InjectionStats {
    uint64_t packets_read = 0;
    uint64_t packets_written = 0;
    uint64_t pulses_injected = 0;
    uint64_t samples_injected = 0;
    double max_amplitude = 0.0;
    bool has_nan = false;
    bool has_inf = false;
};

float loadF32LE(const uint8_t *p);
void storeF32LE(uint8_t *p, float v);
void fillZeroPacketHeader(std::vector<uint8_t> &packet,
                          const RadarConfig &radar,
                          const TargetGlobalConfig &global,
                          uint32_t prt_counter,
                          double utc,
                          double theta_deg);
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
                                double scan_extent_deg);

PulseTruth injectOnePulse(std::vector<uint8_t> &packet,
                          const RadarConfig &radar,
                          const TargetGlobalConfig &global,
                          const TargetConfig &target,
                          int period_id,
                          int beam_id,
                          int pulse_id,
                          const std::vector<uint8_t> *background_packet = nullptr,
                          double time_override_sec = std::numeric_limits<double>::quiet_NaN(),
                          double servo_azimuth_override_deg = std::numeric_limits<double>::quiet_NaN(),
                          bool apply_discrete_beam_membership = true,
                          int scan_id = -1,
                          int prt_id = -1,
                          double servo_elevation_deg = std::numeric_limits<double>::quiet_NaN());

} // namespace target_injection
} // namespace gmti
