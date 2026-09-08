#pragma once

#include "../common/SimulationGeometry.h"
#include "../target_injection/channel_impairments.h"
#include "../target_injection/target_config.h"

#include <limits>
#include <string>
#include <vector>

namespace gmti {
namespace stage2 {

struct AreaClutterConfig {
    bool enabled = true;
    std::string model = "rayleigh_lognormal_texture";
    int scatterer_count = 1000;
    double mean_power = 1.0;
    double texture_sigma = 0.4;
    double spatial_cell_m = 30.0;
    // Mid-point quadrature samples across one commanded beam width.  More than
    // one subcell is required for a continuous surface to occupy a Doppler
    // interval instead of collapsing onto the beam-centre Doppler row.
    int azimuth_subcell_count = 9;
};

struct StrongScattererConfig {
    bool enabled = true;
    int count = 20;
    double rcs_db_min = 10.0;
    double rcs_db_max = 30.0;
};

struct LineScattererConfig {
    bool enabled = true;
    int line_count = 1;
    int points_per_line = 50;
    double rcs_db = 12.0;
};

struct ThermalNoiseConfig {
    bool enabled = true;
    double noise_power = 0.01;
    // target-only was historically noiseless; allow an explicit Monte Carlo
    // override without changing legacy scenarios.
    bool include_target_only = false;
};

struct SceneConfig {
    double range_min_m = 82800.0;
    double range_max_m = 93000.0;
    double azimuth_min_deg = -60.0;
    double azimuth_max_deg = 60.0;
    double ground_z_m = 0.0;
    double clutter_amplitude_scale = 1.0;
    AreaClutterConfig area;
    StrongScattererConfig strong;
    LineScattererConfig line;
    ThermalNoiseConfig noise;
};

struct SimulationConfig {
    int period_start = 0;
    int period_count = 1;
    int target_reference_period = -1;
    int beam_start_1based = 1;
    int beam_count = 0; // 0 means all physical beams from beam_start
    uint32_t random_seed = 202606;
    std::string platform_mode = "ideal_straight";
    std::string beam_pattern_mode = "gaussian";
    std::string channel_phase_mode = "ctdr_exact";
    std::string lfm_time_reference = "center";
    int chirp_phase_sign = 1;
    int carrier_phase_sign = -1;
    std::string output_format = "compatible_with_algorithm";
    // physical: supplied receive centres; paired_same_phase_center: ch3=ch1,
    // ch4=ch2 geometrically, with receiver noise still generated per channel.
    std::string four_channel_phase_center_mode = "physical";
};

struct TargetStage2Config {
    bool enabled = true;
    std::string target_config_path = "targets.json";
    double target_snr_db = 25.0;
    std::string amplitude_mode = "snr_db";
};

struct MechanicalScanStage2Config {
    double scan_start_deg = -60.0;
    double scan_end_deg = 60.0;
    double scan_speed_deg_s = 20.0;
    std::string direction = "forward";
    int cpi_pulse_count = 130;
    int cpi_step_pulse = 130;
    double max_cpi_angle_span_deg = 3.0;
    bool phase_center_rotation_enable = true;
    double phase_center_mount_angle_deg = 0.0;
    int phase_center_rotation_sign = 1;
};

struct Stage2Config {
    gmti::target_injection::RadarConfig radar;
    // FPGA packet payload can be padded beyond the physically acquired DDC
    // samples. Samples [acquired_pulse_len, pulse_len) are zero on wire.
    int acquired_pulse_len = 0;
    double platform_height_m = 6000.0;
    double platform_speed_mps = 60.0;
    double platform_origin_lat_deg = 40.45121057;
    double platform_origin_lon_deg = 116.98377429;
    double platform_origin_alt_m = 0.0;
    double projection_ref_lon_deg = 117.0;
    gmti::sim_geometry::Stage2GeometryConfig geometry;
    SimulationConfig sim;
    SceneConfig scene;
    TargetStage2Config target;
    // Defaults to the historical discrete electronic-beam generator.  In
    // mechanical mode each period is one continuous physical servo sweep.
    std::string scan_mode = "electronic";
    MechanicalScanStage2Config mechanical_scan;
    gmti::target_injection::ChannelImpairmentConfig impairments;
};

struct Stage2RunOptions {
    std::string stage2_config = "stage2_config.json";
    std::string run_config = "";
    std::string target_config = "";
    std::string output_dir = "outputs/stage2";
    std::string scene_mode = "full";
    bool target_enabled = true;
    bool validate = true;
    int period_start = -1;
    int period_count = -1;
    double single_scatterer_range_m = 85000.0;
    int single_point_beam_id_1based = -1;
    int single_point_expected_bin = -1;
    double single_scatterer_azimuth_deg = 0.0;
    double single_scatterer_amplitude = 1.0;
    int moving_target_beam_id_1based = -1;
    int moving_target_expected_bin = -1;
    double moving_target_speed_mps = 0.0;
    std::string moving_target_velocity_mode;
    double moving_target_ve_mps = std::numeric_limits<double>::quiet_NaN();
    double moving_target_vn_mps = std::numeric_limits<double>::quiet_NaN();
    double moving_target_radial_speed_mps = std::numeric_limits<double>::quiet_NaN();
    double moving_target_tangential_speed_mps = std::numeric_limits<double>::quiet_NaN();
    double moving_target_rcs_db = -999.0;
    bool moving_target_single_beam_only = false;
    int moving_target_visible_beam_span = -1;
    double clutter_amplitude_scale = std::numeric_limits<double>::quiet_NaN();
    int area_clutter_scatterer_count = -1;
    double area_clutter_mean_power = std::numeric_limits<double>::quiet_NaN();
    double area_clutter_texture_sigma = std::numeric_limits<double>::quiet_NaN();
    int area_clutter_azimuth_subcell_count = -1;
    int strong_scatterer_count = -1;
    double strong_rcs_db_min = std::numeric_limits<double>::quiet_NaN();
    double strong_rcs_db_max = std::numeric_limits<double>::quiet_NaN();
    int line_scatterer_count = -1;
    int line_points_per_line = -1;
    double line_rcs_db = std::numeric_limits<double>::quiet_NaN();
    double noise_power = std::numeric_limits<double>::quiet_NaN();
    bool legacy_override_used = false;
    std::string parse_error;
};

struct Stage2RunTarget {
    std::string target_id;
    bool enabled = true;
    std::string init_type;
    std::string motion_type;
    std::string amplitude_type;
    std::string visibility_type;
    int beam_id = -1;
    int expected_bin = -1;
    double theta_cmd_deg = std::numeric_limits<double>::quiet_NaN();
    double azimuth_deg = std::numeric_limits<double>::quiet_NaN();
    double azimuth_offset_deg = std::numeric_limits<double>::quiet_NaN();
    double ve_mps = 0.0;
    double vn_mps = 0.0;
    double snr_db = std::numeric_limits<double>::quiet_NaN();
    std::vector<double> snr_db_by_period;
    bool single_beam_only = false;
    int visible_beam_span = -1;
    gmti::target_injection::TargetConfig target;
};

struct Stage2RunConfig {
    std::string case_id;
    std::string output_dir;
    // Optional one-pass C+N partner.  The simulator writes the packet before
    // target injection to this directory, so later target-amplitude changes
    // can be audited against the exact generated background realization.
    std::string paired_background_output_dir;
    // Optional existing C+N dataset to reuse packet-for-packet.  When set,
    // the simulator skips scene/clutter/noise generation and only injects the
    // targets into these saved background packets.
    std::string background_input_dir;
    // Optional payload scale applied after reading a reusable background.
    // It is useful for low-level int16 backgrounds whose target amplitudes
    // would otherwise quantize to only a few codes; the target reference RMS
    // is measured after this same scale is applied.
    double background_input_scale = 1.0;
    std::string scene_mode = "full";
    // Generate the same clutter/noise realization used to normalize every
    // target's configured snr_db, then clear the payload immediately before
    // target injection.  This gives a true S-only partner for a full-scene
    // C+N / S+C+N paired experiment without changing target amplitudes.
    bool signal_only = false;
    // auto preserves legacy behavior; raw_lfm is required for paired target
    // on/off experiments so both cases enter the same production pulse-
    // compression path. range_compressed is only valid without raw targets or
    // materialized point/line/strong scatterers.
    std::string output_signal_domain = "auto";
    bool truth_output = true;
    int beam_index_base = 1;
    Stage2Config cfg;
    gmti::target_injection::TargetGlobalConfig global;
    std::vector<Stage2RunTarget> targets;
    Stage2RunOptions legacy_scene_options;
    bool use_legacy_scene_options = false;
};

bool parseStage2CommandLine(int argc, char **argv, Stage2RunOptions &opt);
bool loadStage2Config(const std::string &path, Stage2Config &cfg, std::string &err);
bool writeDefaultStage2Config(const std::string &path, std::string &err);
bool writeStage2OutputConfig(const Stage2Config &cfg,
                             const std::string &out_xml,
                             const std::string &data_file,
                             bool output_is_precompressed,
                             std::string &err);
gmti::target_injection::TargetGlobalConfig makeTargetGlobal(const Stage2Config &cfg);
bool loadStage2RunConfig(const std::string &path, Stage2RunConfig &run, std::string &err);
bool validateStage2RunConfig(const Stage2RunConfig &run, std::string &err);
bool makeLegacyStage2RunConfig(const Stage2RunOptions &opt, Stage2RunConfig &run, std::string &err);

} // namespace stage2
} // namespace gmti
