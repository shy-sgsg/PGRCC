#pragma once

#include "../common/SimulationGeometry.h"
#include "target_common.h"

#include <limits>
#include <string>
#include <vector>

namespace gmti {
namespace target_injection {

struct RadarConfig {
    std::string input_config;
    std::string input_data_file;
    std::string output_data_file;
    int pulse_len = 11820;
    int pulse_num = 130;
    int beam_count = 61;
    std::string iq_data_type = "float32";
    int new_protocol_channel_count = 2;
    int new_protocol_read_channel_1 = 1;
    int new_protocol_read_channel_2 = 2;
    int range_fft_len = 12288;
    int range_crop_start = 3864;
    int range_crop_len = 4096;
    double scan_min_deg = -60.0;
    double scan_step_deg = 2.0;
    double beam_width_deg = 2.28;
    double fc_hz = 16.0e9;
    double br_hz = 50.0e6;
    double fs_hz = 60.0e6;
    double tr_sec = 130.0e-6;
    double prf_hz = 1300.0;
    double sample_delay_sec = 488.0e-6;
    double d_chan_m = 0.17;
    // Receive phase-centre offsets in the simulator local frame (metres,
    // 1-based protocol channel order).  Local x/y/z follow
    // simulation_geometry; with the production test geometry x is along
    // track, y is east/cross-track and z is up.  An empty vector preserves
    // the legacy two-channel +/-d_chan_m/2 along-track geometry.
    std::vector<Vec3> channel_offsets_local_m;
    std::vector<std::string> channel_names;
    // Mechanical whole-platform/receive-array pose.  When enabled, each
    // explicit (or fallback) local channel offset is rotated about local z by
    // mount_angle + sign*servo_azimuth before it is used in the two-path
    // echo model.  Electronic simulation keeps this disabled.
    bool phase_center_rotation_enable = false;
    double phase_center_mount_angle_deg = 0.0;
    int phase_center_rotation_sign = 1;
    int motion_doppler_axis_sign = -1;
};

struct TargetGlobalConfig {
    std::string coordinate_mode = "project_local";
    std::string visibility_mode = "hard_gate";
    std::string amplitude_mode = "snr_db";
    std::string channel_phase_mode = "ctdr_exact";
    std::string platform_mode = "ideal_platform";
    double target_snr_db = 30.0;
    double direct_amplitude = 1.0;
    double beam_gain_threshold = 0.05;
    double rms_floor = 1.0e-6;
    std::string lfm_time_reference = "center"; // center / legacy_start
    int chirp_phase_sign = 1;
    int carrier_phase_sign = -1;
    double platform_speed_mps = 60.0;
    double platform_height_m = 6000.0;
    double platform_origin_lat_deg = 40.45121057;
    double platform_origin_lon_deg = 116.98377429;
    double platform_origin_alt_m = 0.0;
    double projection_ref_lon_deg = 117.0;
    gmti::sim_geometry::Stage2GeometryConfig geometry;
};

struct TargetConfig {
    int id = 1;
    std::string name = "strong_cooperative_target";
    std::string motion_model = "constant_velocity";
    std::string init_mode = "range_azimuth";
    bool enabled = true;
    int start_period = 0;
    int end_period = 10;
    Vec3 p0;
    Vec3 v;
    double slant_range_m = 85000.0;
    double azimuth_deg = 0.0;
    double height_m = 0.0;
    double radial_velocity_mps = 10.0;
    double cross_velocity_mps = 0.0;
    bool has_ref_geometry = false;
    int ref_beam_id = -1;
    int ref_pulse_idx = -1;
    double ref_time_s = 0.0;
    Vec3 ref_platform;
    Vec3 ref_target;
    double ref_range_m = 0.0;
    double ref_range_sample_float = 0.0;
    int ref_range_sample_int = 0;
    double echo_delay_sample_center_used = 0.0;
    double override_speed_mps = std::numeric_limits<double>::quiet_NaN();
    double override_rcs_db = std::numeric_limits<double>::quiet_NaN();
    double target_snr_db = std::numeric_limits<double>::quiet_NaN();
    // Optional schedule indexed by (period_id - start_period).
    std::vector<double> target_snr_db_by_period;
    std::string visibility_mode;
    bool single_beam_only = false;
    int visible_beam_span = -1;
    std::string velocity_mode;
    double target_ve_mps = std::numeric_limits<double>::quiet_NaN();
    double target_vn_mps = std::numeric_limits<double>::quiet_NaN();
    double target_vr_self_mps = std::numeric_limits<double>::quiet_NaN();
    double target_vt_self_mps = std::numeric_limits<double>::quiet_NaN();
    double af_motion_truth_hz = std::numeric_limits<double>::quiet_NaN();
};

struct RunOptions {
    std::string input_config = "outputs/stage1/config/temp_config_stage1_newsystem.xml";
    std::string input_data_dir = "outputs/stage1/data";
    std::string target_config = "targets.json";
    std::string output_dir = "outputs/stage1_target";
    std::string background_mode = "copy";
    std::string visibility_mode;
    std::string amplitude_mode;
    int period_start = 0;
    int period_count = 1;
    int beam_start = 0;
    int beam_count = -1;
    int pulse_start = 0;
    int pulse_count = -1;
    double target_snr_db = -999.0;
    double direct_amplitude = -1.0;
    bool write_truth = true;
    bool validate = true;
    bool write_config = true;
};

struct InjectionConfig {
    RadarConfig radar;
    TargetGlobalConfig global;
    TargetConfig target;
    RunOptions run;
};

bool parseCommandLine(int argc, char **argv, RunOptions &opt);
bool loadRadarConfig(const std::string &xml_path, RadarConfig &cfg, std::string &err);
bool loadTargetConfig(const std::string &json_path,
                      const RadarConfig &radar,
                      TargetGlobalConfig &global,
                      TargetConfig &target,
                      std::string &err);
// Parse every object in the targets array.  The single-target API above is
// retained as a compatibility wrapper that returns the first object.
bool loadTargetConfigs(const std::string &json_path,
                       const RadarConfig &radar,
                       TargetGlobalConfig &global,
                       std::vector<TargetConfig> &targets,
                       std::string &err);
// Recompute p0/v after a caller has made the radar/platform geometry
// authoritative.  Stage3 uses this after loading a shared targets.json so a
// stale platform origin or speed in that file cannot move the target away
// from the SAR scene geometry.
void resolveTargetInitialState(const RadarConfig &radar,
                               const TargetGlobalConfig &global,
                               TargetConfig &target);
void applyRunOverrides(const RunOptions &run, TargetGlobalConfig &global);
bool writeOutputConfig(const std::string &input_xml,
                       const std::string &out_xml,
                       const std::string &out_data_file,
                       std::string &err);
bool writeDefaultTargetsJson(const std::string &path, std::string &err);

} // namespace target_injection
} // namespace gmti
