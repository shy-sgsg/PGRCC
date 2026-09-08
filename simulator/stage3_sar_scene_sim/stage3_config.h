#pragma once

#include "../target_injection/channel_impairments.h"

#include <string>

namespace gmti {
namespace stage3 {

struct Stage3SystemConfig {
    double fc_ghz = 16.0;
    double bandwidth_mhz = 50.0;
    double fs_mhz = 60.0;
    double pulse_width_us = 130.0;
    double prf_hz = 1300.0;
    double sample_delay_us = 488.0;
    double sample_window_us = 197.0;
    int ddc_len = 11820;
    int fft_len = 12288;
    int pc_crop_start = 3864;
    int pc_crop_len = 4096;
    double scan_min_deg = -60.0;
    double scan_step_deg = 2.0;
    int beam_count = 61;
    double beam_width_deg = 2.28;
    int pulse_num = 130;
    double platform_height_m = 6000.0;
    double platform_speed_mps = 60.0;
    double d_chan_m = 0.17;
    std::string iq_data_type = "float32";
    int new_protocol_channel_count = 2;
    int new_protocol_read_channel_1 = 1;
    int new_protocol_read_channel_2 = 2;
    double projection_ref_lon_deg = 117.0;
    double origin_lat_deg = 40.4512107203;
    double origin_lon_deg = 116.985931582;
    int squint_side = 1;
};

struct Stage3SarInputConfig {
    std::string image_path = "simulator/scenarios/PGA_KuSAR_Block0.tif";
    std::string input_type = "geotiff";
    std::string image_type = "intensity";
    std::string georef_path;
    std::string image_value_type = "intensity";
    double nodata_value = 0.0;
    double percentile_low = 1.0;
    double percentile_high = 99.0;
    bool percentile_normalize = true;
    std::string resample_mode = "power_average";
    double pixel_size_range_m = 0.3;
    double pixel_size_azimuth_m = 0.3;
    double source_center_col = -1.0;
    double source_center_row = -1.0;
    bool row_increases_with_azimuth = true;
    bool col_increases_with_range = true;
    bool valid_crop_enabled = false;
    int valid_col_start = 0;
    int valid_col_end_exclusive = -1;
    int valid_row_start = 0;
    int valid_row_end_exclusive = -1;
    bool crop_enabled = true;
    double crop_range_min_m = 82800.0;
    double crop_range_max_m = 93000.0;
    double crop_azimuth_min_deg = -60.0;
    double crop_azimuth_max_deg = 60.0;
};

struct Stage3CanvasConfig {
    double resolution_m = 50.0;
    double margin_m = 0.0;
    double range_min_m = 82800.0;
    double range_max_m = 93000.0;
    double ground_z_m = 0.0;
    long long max_cells = 5000000;
};

struct Stage3AreaClutterConfig {
    bool enabled = false;
    std::string model = "continuous_texture";
    int scatterer_count = 0;
    double mean_power = 0.1;
    double texture_sigma = 0.4;
    double spatial_cell_m = 30.0;
};

struct Stage3RoiConfig {
    bool enabled = false;
    int beam_id_1based = 31;
    double center_slant_range_m = 87900.0;
    double range_extent_m = 600.0;
    double azimuth_extent_m = 300.0;
    double resolution_m = 0.3;
    double margin_m = 0.0;
};

struct Stage3SceneConfig {
    std::string mode = "mirror";
    double range_min_m = 82800.0;
    double range_max_m = 93000.0;
    double azimuth_min_deg = -60.0;
    double azimuth_max_deg = 60.0;
    double ground_z_m = 0.0;
    double clutter_amplitude_scale = 1.0;
    Stage3AreaClutterConfig area_clutter;
    Stage3RoiConfig roi;
    bool thermal_noise_enabled = false;
    double thermal_noise_power = 0.01;
};

struct Stage3TilingConfig {
    std::string mode = "mirror";
    bool random_start_offset = false;
    int reference_beam_id_1based = 31;
    double anchor_slant_range_m = 87900.0;
    double tile_gain_jitter_db = 0.0;
    std::string phase_policy = "independent_random_per_ground_cell";
    unsigned int random_seed = 202606;
};

struct Stage3ScattererExtractionConfig {
    std::string method = "grid_adaptive_sampling";
    int max_scatterers = 50000;
    int min_scatterers = 2000;
    double intensity_threshold_percentile = 60.0;
    double strong_threshold_percentile = 99.5;
    double grid_cell_m = 10.0;
    int max_scatterers_per_cell = 3;
    double amplitude_scale = 1.0;
    double amplitude_gamma = 0.5;
    std::string phase_mode = "random_uniform";
    unsigned int random_seed = 202606;
};

struct Stage3ForwardConfig {
    std::string platform_mode = "ideal_straight";
    std::string beam_pattern_mode = "gaussian";
    double beam_gain_threshold = 0.01;
    std::string channel_phase_mode = "ctdr";
    std::string lfm_time_reference = "center";
    int chirp_phase_sign = 1;
    int carrier_phase_sign = -1;
    int period_start = 0;
    int period_count = 1;
    int beam_start = 1;
    int beam_count = 61;
    int pulse_start = 0;
    int pulse_count = 130;
    bool dry_run = false;
    bool enable_openmp = true;
    std::string backend = "cpu";
    bool compare_cpu_cuda = false;
    int cuda_batch_pulses = 130;
    double compare_abs_tolerance = 5.0e-4;
    double compare_rel_tolerance = 5.0e-4;
    double dbs_output_resolution_m = 3.0;
    int truth_max_rows = 250000;
};

struct Stage3TargetsConfig {
    bool enabled = false;
    std::string target_config_path = "targets.json";
    double target_snr_db = 25.0;
    std::string amplitude_mode = "snr_db";
};

struct Stage3Config {
    std::string case_id = "stage3_sar_surface_mirror_tiling";
    std::string output_dir = "outputs/stage3_sar_surface_mirror_tiling";
    Stage3SystemConfig system;
    Stage3SarInputConfig sar_input;
    Stage3CanvasConfig canvas;
    Stage3SceneConfig scene;
    Stage3TilingConfig tiling;
    Stage3ScattererExtractionConfig extraction;
    Stage3ForwardConfig forward;
    Stage3TargetsConfig targets;
    gmti::target_injection::ChannelImpairmentConfig impairments;
};

struct Stage3RunOptions {
    std::string stage3_config = "stage3_config.json";
    std::string run_config;
    std::string sar_image;
    std::string georef_path;
    std::string scatterer_csv;
    std::string output_dir = "outputs/stage3";
    std::string target_config;
    std::string scene_mode;
    std::string backend;
    bool compare_cpu_cuda_override_set = false;
    bool compare_cpu_cuda = false;
    bool target_enabled_override_set = false;
    bool target_enabled = false;
    bool validate = true;
    bool extract_only = false;
    bool forward_only = false;
    bool dry_run_override_set = false;
    bool dry_run = false;
    int period_start = -1;
    int period_count = -1;
    int beam_start = -1;
    int beam_count = -1;
    int pulse_start = -1;
    int pulse_count = -1;
    double single_scatterer_range_m = 85000.0;
    double single_scatterer_azimuth_deg = 0.0;
    double single_scatterer_amplitude = 1.0;
};

bool parseStage3CommandLine(int argc, char **argv, Stage3RunOptions &opt, std::string &err);
bool loadStage3Config(const std::string &path, Stage3Config &cfg, std::string &err);
bool ensureStage3Dirs(const std::string &output_dir, std::string &err);
bool writeDefaultStage3Config(const std::string &path, std::string &err);
std::string pathJoin(const std::string &a, const std::string &b);

} // namespace stage3
} // namespace gmti
