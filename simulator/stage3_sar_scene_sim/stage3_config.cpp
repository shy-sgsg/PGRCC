#include "stage3_config.h"

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <sys/stat.h>
#include <sys/types.h>
#include <vector>

namespace gmti {
namespace stage3 {

namespace {

bool mkdirOne(const std::string &p, std::string &err)
{
    if (p.empty()) return true;
    if (::mkdir(p.c_str(), 0775) == 0 || errno == EEXIST) return true;
    err = "mkdir failed for " + p + ": " + std::strerror(errno);
    return false;
}

bool mkdirRecursive(const std::string &p, std::string &err)
{
    std::string cur;
    for (size_t i = 0; i < p.size(); ++i) {
        cur.push_back(p[i]);
        if (p[i] == '/' || i + 1 == p.size()) {
            if (cur == "/" || cur.empty()) continue;
            if (cur.size() > 1 && cur[cur.size() - 1] == '/') cur.resize(cur.size() - 1);
            if (!cur.empty() && !mkdirOne(cur, err)) return false;
            if (i + 1 < p.size() && (cur.empty() || cur[cur.size() - 1] != '/')) cur.push_back('/');
        }
    }
    return true;
}

bool readBool(const std::string &s)
{
    return !(s == "false" || s == "0" || s == "no");
}

std::string readTextFile(const std::string &path)
{
    std::ifstream in(path.c_str());
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

size_t findKeyColon(const std::string &txt, const std::string &key)
{
    const std::string needle = "\"" + key + "\"";
    size_t pos = 0;
    while ((pos = txt.find(needle, pos)) != std::string::npos) {
        size_t i = pos + needle.size();
        while (i < txt.size() && (txt[i] == ' ' || txt[i] == '\t' ||
                                  txt[i] == '\r' || txt[i] == '\n')) {
            ++i;
        }
        if (i < txt.size() && txt[i] == ':') {
            return i;
        }
        pos += needle.size();
    }
    return std::string::npos;
}

std::string sectionObject(const std::string &txt, const std::string &key)
{
    const size_t colon = findKeyColon(txt, key);
    if (colon == std::string::npos) return "";
    const size_t open = txt.find('{', colon + 1);
    if (open == std::string::npos) return "";
    int depth = 0;
    for (size_t i = open; i < txt.size(); ++i) {
        if (txt[i] == '{') ++depth;
        else if (txt[i] == '}') {
            --depth;
            if (depth == 0) return txt.substr(open, i - open + 1);
        }
    }
    return "";
}

double jsonDouble(const std::string &obj, const std::string &key, double fallback)
{
    const size_t colon = findKeyColon(obj, key);
    if (colon == std::string::npos) return fallback;
    return std::strtod(obj.c_str() + colon + 1, nullptr);
}

int jsonInt(const std::string &obj, const std::string &key, int fallback)
{
    return static_cast<int>(jsonDouble(obj, key, static_cast<double>(fallback)));
}

bool jsonBool(const std::string &obj, const std::string &key, bool fallback)
{
    const size_t colon = findKeyColon(obj, key);
    if (colon == std::string::npos) return fallback;
    const std::string tail = obj.substr(colon + 1, 8);
    if (tail.find("true") != std::string::npos) return true;
    if (tail.find("false") != std::string::npos) return false;
    return fallback;
}

std::string jsonString(const std::string &obj, const std::string &key, const std::string &fallback)
{
    const size_t colon = findKeyColon(obj, key);
    if (colon == std::string::npos) return fallback;
    const size_t q1 = obj.find('"', colon + 1);
    if (q1 == std::string::npos) return fallback;
    const size_t q2 = obj.find('"', q1 + 1);
    if (q2 == std::string::npos) return fallback;
    return obj.substr(q1 + 1, q2 - q1 - 1);
}

void parseChannelImpairments(
    const std::string &txt,
    gmti::target_injection::ChannelImpairmentConfig &c)
{
    std::string obj = sectionObject(txt, "channel_impairments");
    if (obj.empty()) obj = sectionObject(txt, "impairments");
    if (obj.empty()) return;
    c.enabled = jsonBool(obj, "enabled", c.enabled);
    c.channel_amp_mismatch_db = jsonDouble(obj, "channel_amp_mismatch_db", c.channel_amp_mismatch_db);
    c.channel_fixed_phase_mismatch_deg = jsonDouble(obj, "channel_fixed_phase_mismatch_deg", c.channel_fixed_phase_mismatch_deg);
    c.channel_phase_jitter_std_deg = jsonDouble(obj, "channel_phase_jitter_std_deg", c.channel_phase_jitter_std_deg);
    c.channel_range_shift_samples = jsonDouble(obj, "channel_range_shift_samples", c.channel_range_shift_samples);
    c.channel_time_delay_ns = jsonDouble(obj, "channel_time_delay_ns", c.channel_time_delay_ns);
    c.channel_noise_power_ratio_db = jsonDouble(obj, "channel_noise_power_ratio_db", c.channel_noise_power_ratio_db);
    c.iq_gain_imbalance_db = jsonDouble(obj, "iq_gain_imbalance_db", c.iq_gain_imbalance_db);
    c.iq_phase_imbalance_deg = jsonDouble(obj, "iq_phase_imbalance_deg", c.iq_phase_imbalance_deg);
    c.baseline_error_m = jsonDouble(obj, "baseline_error_m", c.baseline_error_m);
    c.per_pulse_phase_drift_deg = jsonDouble(obj, "per_pulse_phase_drift_deg", c.per_pulse_phase_drift_deg);
    c.per_beam_phase_bias_deg = jsonDouble(obj, "per_beam_phase_bias_deg", c.per_beam_phase_bias_deg);
    c.sample_clock_error_ppm = jsonDouble(obj, "sample_clock_error_ppm", c.sample_clock_error_ppm);
    c.channel_drop_probability = jsonDouble(obj, "channel_drop_probability", c.channel_drop_probability);
    c.channel_saturation_level = jsonDouble(obj, "channel_saturation_level", c.channel_saturation_level);
}

} // namespace

bool parseStage3CommandLine(int argc, char **argv, Stage3RunOptions &opt, std::string &err)
{
    for (int i = 1; i < argc; ++i) {
        const std::string k = argv[i];
        const char *v = (i + 1 < argc) ? argv[i + 1] : nullptr;
        if (k == "--config" && v) {
            opt.run_config = argv[++i];
            opt.stage3_config = opt.run_config;
        }
        else if (k == "--stage3-config" && v) opt.stage3_config = argv[++i];
        else if (k == "--sar-image" && v) opt.sar_image = argv[++i];
        else if (k == "--georef" && v) opt.georef_path = argv[++i];
        else if (k == "--scatterer-csv" && v) opt.scatterer_csv = argv[++i];
        else if (k == "--output-dir" && v) opt.output_dir = argv[++i];
        else if (k == "--target-config" && v) opt.target_config = argv[++i];
        else if (k == "--target-enabled" && v) {
            opt.target_enabled = readBool(argv[++i]);
            opt.target_enabled_override_set = true;
        }
        else if (k == "--validate" && v) opt.validate = readBool(argv[++i]);
        else if (k == "--extract-only" && v) opt.extract_only = readBool(argv[++i]);
        else if (k == "--forward-only" && v) opt.forward_only = readBool(argv[++i]);
        else if (k == "--dry-run" && v) {
            opt.dry_run = readBool(argv[++i]);
            opt.dry_run_override_set = true;
        }
        else if (k == "--scene-mode" && v) opt.scene_mode = argv[++i];
        else if (k == "--backend" && v) opt.backend = argv[++i];
        else if (k == "--compare-cpu-cuda" && v) {
            opt.compare_cpu_cuda = readBool(argv[++i]);
            opt.compare_cpu_cuda_override_set = true;
        }
        else if (k == "--period-start" && v) opt.period_start = std::atoi(argv[++i]);
        else if (k == "--period-count" && v) opt.period_count = std::atoi(argv[++i]);
        else if (k == "--beam-start" && v) opt.beam_start = std::atoi(argv[++i]);
        else if (k == "--beam-count" && v) opt.beam_count = std::atoi(argv[++i]);
        else if (k == "--pulse-start" && v) opt.pulse_start = std::atoi(argv[++i]);
        else if (k == "--pulse-count" && v) opt.pulse_count = std::atoi(argv[++i]);
        else if (k == "--single-scatterer-range" && v) opt.single_scatterer_range_m = std::atof(argv[++i]);
        else if (k == "--single-scatterer-azimuth" && v) opt.single_scatterer_azimuth_deg = std::atof(argv[++i]);
        else if (k == "--single-scatterer-amplitude" && v) opt.single_scatterer_amplitude = std::atof(argv[++i]);
        else if (!k.empty() && k[0] != '-' && opt.run_config.empty()) {
            opt.run_config = k;
            opt.stage3_config = k;
        } else {
            err = "unknown or incomplete option: " + k;
            return false;
        }
    }
    return true;
}

bool loadStage3Config(const std::string &path, Stage3Config &cfg, std::string &err)
{
    const std::string txt = readTextFile(path);
    if (txt.empty()) {
        err = "failed to read config " + path;
        return false;
    }
    cfg.case_id = jsonString(txt, "case_id", cfg.case_id);
    cfg.output_dir = jsonString(txt, "output_dir", cfg.output_dir);
    cfg.scene.mode = jsonString(txt, "scene_mode", cfg.scene.mode);

    const std::string sys = sectionObject(txt, "system");
    const std::string waveform = sectionObject(txt, "waveform");
    const std::string rp_obj = sectionObject(txt, "range_processing");
    const std::string scan_obj = sectionObject(txt, "scan");
    const std::string platform_obj = sectionObject(txt, "platform");
    const std::string canvas = sectionObject(txt, "canvas");
    const std::string scene = sectionObject(txt, "scene");
    const std::string sar_source = sectionObject(txt, "sar_source");
    const std::string sar_input = sectionObject(txt, "sar_input");
    const std::string extraction = sectionObject(txt, "scatterer_extraction");
    const std::string tiling = sectionObject(txt, "tiling");
    const std::string fwd = sectionObject(txt, "forward");
    const std::string targets = sectionObject(txt, "targets");

    const std::string &w = waveform.empty() ? sys : waveform;
    const std::string &rp = rp_obj.empty() ? sys : rp_obj;
    const std::string &scan = scan_obj.empty() ? sys : scan_obj;
    const std::string &platform = platform_obj.empty() ? sys : platform_obj;
    const std::string &sar = sar_source.empty() ? sar_input : sar_source;
    cfg.system.fc_ghz = jsonDouble(w, "fc_ghz", cfg.system.fc_ghz);
    cfg.system.bandwidth_mhz = jsonDouble(w, "bandwidth_mhz", cfg.system.bandwidth_mhz);
    cfg.system.fs_mhz = jsonDouble(w, "fs_mhz", cfg.system.fs_mhz);
    cfg.system.pulse_width_us = jsonDouble(w, "tr_us", jsonDouble(w, "pulse_width_us", cfg.system.pulse_width_us));
    cfg.system.prf_hz = jsonDouble(w, "prf_hz", cfg.system.prf_hz);
    cfg.system.ddc_len = jsonInt(w, "pulse_len", jsonInt(w, "ddc_len", cfg.system.ddc_len));
    cfg.system.pulse_num = jsonInt(w, "pulse_num", cfg.system.pulse_num);
    cfg.system.d_chan_m = jsonDouble(w, "d_chan_m", cfg.system.d_chan_m);
    cfg.system.iq_data_type = jsonString(w, "iq_data_type", cfg.system.iq_data_type);
    cfg.system.new_protocol_channel_count = jsonInt(w, "new_protocol_channel_count", cfg.system.new_protocol_channel_count);
    cfg.system.new_protocol_read_channel_1 = jsonInt(w, "new_protocol_read_channel_1", cfg.system.new_protocol_read_channel_1);
    cfg.system.new_protocol_read_channel_2 = jsonInt(w, "new_protocol_read_channel_2", cfg.system.new_protocol_read_channel_2);

    cfg.system.fft_len = jsonInt(rp, "range_fft_len", jsonInt(rp, "fft_len", cfg.system.fft_len));
    cfg.system.pc_crop_start = jsonInt(rp, "range_crop_start", jsonInt(rp, "pc_crop_start", cfg.system.pc_crop_start));
    cfg.system.pc_crop_len = jsonInt(rp, "range_crop_len", jsonInt(rp, "pc_crop_len", cfg.system.pc_crop_len));
    cfg.system.sample_delay_us = jsonDouble(rp, "sample_delay_us", cfg.system.sample_delay_us);
    cfg.system.sample_window_us = jsonDouble(rp, "sample_window_us", cfg.system.sample_window_us);

    cfg.system.scan_min_deg = jsonDouble(scan, "scan_min_deg", cfg.system.scan_min_deg);
    cfg.system.scan_step_deg = jsonDouble(scan, "scan_step_deg", cfg.system.scan_step_deg);
    cfg.system.beam_count = jsonInt(scan, "beam_count", cfg.system.beam_count);
    cfg.system.beam_width_deg = jsonDouble(scan, "beam_width_deg", cfg.system.beam_width_deg);

    cfg.system.platform_speed_mps = jsonDouble(platform, "speed_mps", jsonDouble(platform, "platform_speed_mps", cfg.system.platform_speed_mps));
    cfg.system.platform_height_m = jsonDouble(platform, "height_m", jsonDouble(platform, "platform_height_m", cfg.system.platform_height_m));
    cfg.system.origin_lat_deg = jsonDouble(platform, "origin_lat_deg", cfg.system.origin_lat_deg);
    cfg.system.origin_lon_deg = jsonDouble(platform, "origin_lon_deg", cfg.system.origin_lon_deg);
    cfg.system.projection_ref_lon_deg = jsonDouble(platform, "projection_ref_lon_deg", cfg.system.projection_ref_lon_deg);
    cfg.system.squint_side = jsonInt(platform, "squint_side", cfg.system.squint_side);

    cfg.canvas.resolution_m = jsonDouble(canvas, "resolution_m", cfg.canvas.resolution_m);
    cfg.canvas.margin_m = jsonDouble(canvas, "margin_m", cfg.canvas.margin_m);
    cfg.canvas.range_min_m = jsonDouble(canvas, "range_min_m", cfg.canvas.range_min_m);
    cfg.canvas.range_max_m = jsonDouble(canvas, "range_max_m", cfg.canvas.range_max_m);
    cfg.canvas.ground_z_m = jsonDouble(canvas, "ground_z_m", cfg.canvas.ground_z_m);
    cfg.canvas.max_cells = static_cast<long long>(jsonDouble(canvas, "max_cells", static_cast<double>(cfg.canvas.max_cells)));

    cfg.scene.mode = jsonString(scene, "mode", cfg.scene.mode);
    cfg.scene.range_min_m = jsonDouble(scene, "range_min_m", cfg.scene.range_min_m);
    cfg.scene.range_max_m = jsonDouble(scene, "range_max_m", cfg.scene.range_max_m);
    cfg.scene.azimuth_min_deg = jsonDouble(scene, "azimuth_min_deg", cfg.scene.azimuth_min_deg);
    cfg.scene.azimuth_max_deg = jsonDouble(scene, "azimuth_max_deg", cfg.scene.azimuth_max_deg);
    cfg.scene.ground_z_m = jsonDouble(scene, "ground_z_m", cfg.scene.ground_z_m);
    cfg.scene.clutter_amplitude_scale = jsonDouble(scene, "clutter_amplitude_scale", cfg.scene.clutter_amplitude_scale);
    const std::string area = sectionObject(scene, "area_clutter");
    cfg.scene.area_clutter.enabled = jsonBool(area, "enabled", cfg.scene.area_clutter.enabled);
    cfg.scene.area_clutter.model = jsonString(area, "model", cfg.scene.area_clutter.model);
    cfg.scene.area_clutter.scatterer_count = jsonInt(area, "scatterer_count", cfg.scene.area_clutter.scatterer_count);
    cfg.scene.area_clutter.mean_power = jsonDouble(area, "mean_power", cfg.scene.area_clutter.mean_power);
    cfg.scene.area_clutter.texture_sigma = jsonDouble(area, "texture_sigma", cfg.scene.area_clutter.texture_sigma);
    cfg.scene.area_clutter.spatial_cell_m = jsonDouble(area, "spatial_cell_m", cfg.scene.area_clutter.spatial_cell_m);
    const std::string roi = sectionObject(scene, "roi");
    cfg.scene.roi.enabled = jsonBool(roi, "enabled", cfg.scene.roi.enabled);
    cfg.scene.roi.beam_id_1based = jsonInt(roi, "beam_id_1based", cfg.scene.roi.beam_id_1based);
    cfg.scene.roi.center_slant_range_m = jsonDouble(roi, "center_slant_range_m", cfg.scene.roi.center_slant_range_m);
    cfg.scene.roi.range_extent_m = jsonDouble(roi, "range_extent_m", cfg.scene.roi.range_extent_m);
    cfg.scene.roi.azimuth_extent_m = jsonDouble(roi, "azimuth_extent_m", cfg.scene.roi.azimuth_extent_m);
    cfg.scene.roi.resolution_m = jsonDouble(roi, "resolution_m", cfg.scene.roi.resolution_m);
    cfg.scene.roi.margin_m = jsonDouble(roi, "margin_m", cfg.scene.roi.margin_m);
    cfg.scene.thermal_noise_enabled = jsonBool(scene, "thermal_noise_enabled", cfg.scene.thermal_noise_enabled);
    cfg.scene.thermal_noise_power = jsonDouble(scene, "thermal_noise_power", cfg.scene.thermal_noise_power);

    cfg.sar_input.image_path = jsonString(sar, "image_path", cfg.sar_input.image_path);
    cfg.sar_input.input_type = jsonString(sar, "input_type", cfg.sar_input.input_type);
    cfg.sar_input.image_type = jsonString(sar, "image_type", cfg.sar_input.image_type);
    cfg.sar_input.image_value_type = cfg.sar_input.image_type;
    cfg.sar_input.percentile_low = jsonDouble(sar, "percentile_low", cfg.sar_input.percentile_low);
    cfg.sar_input.percentile_high = jsonDouble(sar, "percentile_high", cfg.sar_input.percentile_high);
    cfg.sar_input.percentile_normalize = jsonBool(sar, "percentile_normalize", cfg.sar_input.percentile_normalize);
    cfg.sar_input.resample_mode = jsonString(sar, "resample_mode", cfg.sar_input.resample_mode);
    cfg.sar_input.georef_path = jsonString(sar, "georef_path", cfg.sar_input.georef_path);
    cfg.sar_input.nodata_value = jsonDouble(sar, "nodata_value", cfg.sar_input.nodata_value);
    cfg.sar_input.pixel_size_range_m = jsonDouble(sar, "pixel_size_range_m", cfg.sar_input.pixel_size_range_m);
    cfg.sar_input.pixel_size_azimuth_m = jsonDouble(sar, "pixel_size_azimuth_m", cfg.sar_input.pixel_size_azimuth_m);
    cfg.sar_input.source_center_col = jsonDouble(sar, "source_center_col", cfg.sar_input.source_center_col);
    cfg.sar_input.source_center_row = jsonDouble(sar, "source_center_row", cfg.sar_input.source_center_row);
    cfg.sar_input.row_increases_with_azimuth = jsonBool(sar, "row_increases_with_azimuth", cfg.sar_input.row_increases_with_azimuth);
    cfg.sar_input.col_increases_with_range = jsonBool(sar, "col_increases_with_range", cfg.sar_input.col_increases_with_range);
    cfg.sar_input.valid_crop_enabled = jsonBool(sar, "valid_crop_enabled", cfg.sar_input.valid_crop_enabled);
    cfg.sar_input.valid_col_start = jsonInt(sar, "valid_col_start", cfg.sar_input.valid_col_start);
    cfg.sar_input.valid_col_end_exclusive = jsonInt(sar, "valid_col_end_exclusive", cfg.sar_input.valid_col_end_exclusive);
    cfg.sar_input.valid_row_start = jsonInt(sar, "valid_row_start", cfg.sar_input.valid_row_start);
    cfg.sar_input.valid_row_end_exclusive = jsonInt(sar, "valid_row_end_exclusive", cfg.sar_input.valid_row_end_exclusive);
    cfg.sar_input.crop_enabled = jsonBool(sar, "crop_enabled", cfg.sar_input.crop_enabled);
    cfg.sar_input.crop_range_min_m = jsonDouble(sar, "crop_range_min_m", cfg.sar_input.crop_range_min_m);
    cfg.sar_input.crop_range_max_m = jsonDouble(sar, "crop_range_max_m", cfg.sar_input.crop_range_max_m);
    cfg.sar_input.crop_azimuth_min_deg = jsonDouble(sar, "crop_azimuth_min_deg", cfg.sar_input.crop_azimuth_min_deg);
    cfg.sar_input.crop_azimuth_max_deg = jsonDouble(sar, "crop_azimuth_max_deg", cfg.sar_input.crop_azimuth_max_deg);

    cfg.extraction.method = jsonString(extraction, "method", cfg.extraction.method);
    cfg.extraction.max_scatterers = jsonInt(extraction, "max_scatterers", cfg.extraction.max_scatterers);
    cfg.extraction.min_scatterers = jsonInt(extraction, "min_scatterers", cfg.extraction.min_scatterers);
    cfg.extraction.intensity_threshold_percentile = jsonDouble(extraction, "intensity_threshold_percentile", cfg.extraction.intensity_threshold_percentile);
    cfg.extraction.strong_threshold_percentile = jsonDouble(extraction, "strong_threshold_percentile", cfg.extraction.strong_threshold_percentile);
    cfg.extraction.grid_cell_m = jsonDouble(extraction, "grid_cell_m", cfg.extraction.grid_cell_m);
    cfg.extraction.max_scatterers_per_cell = jsonInt(extraction, "max_scatterers_per_cell", cfg.extraction.max_scatterers_per_cell);
    cfg.extraction.amplitude_scale = jsonDouble(extraction, "amplitude_scale", cfg.extraction.amplitude_scale);
    cfg.extraction.amplitude_gamma = jsonDouble(extraction, "amplitude_gamma", cfg.extraction.amplitude_gamma);
    cfg.extraction.phase_mode = jsonString(extraction, "phase_mode", cfg.extraction.phase_mode);
    cfg.extraction.random_seed = static_cast<unsigned int>(jsonInt(extraction, "random_seed", static_cast<int>(cfg.extraction.random_seed)));

    cfg.tiling.mode = jsonString(tiling, "mode", cfg.tiling.mode);
    cfg.tiling.random_start_offset = jsonBool(tiling, "random_start_offset", cfg.tiling.random_start_offset);
    cfg.tiling.reference_beam_id_1based = jsonInt(tiling, "reference_beam_id_1based", cfg.tiling.reference_beam_id_1based);
    cfg.tiling.anchor_slant_range_m = jsonDouble(tiling, "anchor_slant_range_m", cfg.tiling.anchor_slant_range_m);
    cfg.tiling.tile_gain_jitter_db = jsonDouble(tiling, "tile_gain_jitter_db", cfg.tiling.tile_gain_jitter_db);
    cfg.tiling.phase_policy = jsonString(tiling, "phase_policy", cfg.tiling.phase_policy);
    cfg.tiling.random_seed = static_cast<unsigned int>(jsonInt(tiling, "random_seed", static_cast<int>(cfg.tiling.random_seed)));

    cfg.forward.period_start = jsonInt(fwd, "period_start", cfg.forward.period_start);
    cfg.forward.period_count = jsonInt(fwd, "period_count", cfg.forward.period_count);
    cfg.forward.beam_start = jsonInt(fwd, "beam_start", cfg.forward.beam_start);
    cfg.forward.beam_count = jsonInt(fwd, "beam_count", cfg.forward.beam_count);
    cfg.forward.pulse_start = jsonInt(fwd, "pulse_start", cfg.forward.pulse_start);
    cfg.forward.pulse_count = jsonInt(fwd, "pulse_count", cfg.forward.pulse_count);
    cfg.forward.dry_run = jsonBool(fwd, "dry_run", cfg.forward.dry_run);
    cfg.forward.platform_mode = jsonString(fwd, "platform_mode", cfg.forward.platform_mode);
    cfg.forward.beam_pattern_mode = jsonString(fwd, "beam_pattern_mode", cfg.forward.beam_pattern_mode);
    cfg.forward.beam_gain_threshold = jsonDouble(fwd, "beam_gain_threshold", cfg.forward.beam_gain_threshold);
    cfg.forward.channel_phase_mode = jsonString(fwd, "channel_phase_mode", cfg.forward.channel_phase_mode);
    cfg.forward.lfm_time_reference = jsonString(
        fwd, "lfm_time_reference", cfg.forward.lfm_time_reference);
    cfg.forward.chirp_phase_sign = jsonInt(fwd, "chirp_phase_sign", cfg.forward.chirp_phase_sign);
    cfg.forward.carrier_phase_sign = jsonInt(fwd, "carrier_phase_sign", cfg.forward.carrier_phase_sign);
    cfg.forward.enable_openmp = jsonBool(fwd, "enable_openmp", cfg.forward.enable_openmp);
    cfg.forward.backend = jsonString(fwd, "backend", cfg.forward.backend);
    cfg.forward.compare_cpu_cuda = jsonBool(fwd, "compare_cpu_cuda", cfg.forward.compare_cpu_cuda);
    cfg.forward.cuda_batch_pulses = jsonInt(fwd, "cuda_batch_pulses", cfg.forward.cuda_batch_pulses);
    cfg.forward.compare_abs_tolerance = jsonDouble(fwd, "compare_abs_tolerance", cfg.forward.compare_abs_tolerance);
    cfg.forward.compare_rel_tolerance = jsonDouble(fwd, "compare_rel_tolerance", cfg.forward.compare_rel_tolerance);
    cfg.forward.dbs_output_resolution_m = jsonDouble(fwd, "dbs_output_resolution_m", cfg.forward.dbs_output_resolution_m);
    cfg.forward.truth_max_rows = jsonInt(fwd, "truth_max_rows", cfg.forward.truth_max_rows);

    cfg.targets.enabled = jsonBool(targets, "enabled", cfg.targets.enabled);
    cfg.targets.target_config_path = jsonString(targets, "target_config_path", cfg.targets.target_config_path);
    cfg.targets.target_snr_db = jsonDouble(targets, "target_snr_db", cfg.targets.target_snr_db);
    cfg.targets.amplitude_mode = jsonString(targets, "amplitude_mode", cfg.targets.amplitude_mode);
    parseChannelImpairments(txt, cfg.impairments);

    if (cfg.scene.mode == "roi") cfg.scene.roi.enabled = true;
    if (cfg.scene.mode == "full" || cfg.scene.mode == "sar") cfg.scene.mode = "mirror";
    if (cfg.system.ddc_len <= 0 || cfg.system.pulse_num <= 0 || cfg.system.beam_count <= 0 ||
        cfg.system.prf_hz <= 0.0 || cfg.system.fs_mhz <= 0.0 || cfg.system.fc_ghz <= 0.0) {
        err = "invalid Stage3 system dimensions or waveform";
        return false;
    }
    if (cfg.canvas.resolution_m <= 0.0 || cfg.scene.roi.resolution_m <= 0.0 ||
        cfg.sar_input.pixel_size_range_m <= 0.0 || cfg.sar_input.pixel_size_azimuth_m <= 0.0) {
        err = "Stage3 spatial and SAR pixel resolutions must be positive";
        return false;
    }
    if (cfg.impairments.channel_drop_probability < 0.0 ||
        cfg.impairments.channel_drop_probability > 1.0) {
        err = "channel_impairments.channel_drop_probability must be in [0,1]";
        return false;
    }
    if (cfg.forward.backend != "cpu" && cfg.forward.backend != "cuda" && cfg.forward.backend != "auto") {
        err = "forward.backend must be cpu, cuda, or auto";
        return false;
    }
    if (cfg.forward.lfm_time_reference != "center" &&
        cfg.forward.lfm_time_reference != "start" &&
        cfg.forward.lfm_time_reference != "legacy_start") {
        err = "forward.lfm_time_reference must be center or legacy_start";
        return false;
    }
    if (!(cfg.forward.beam_gain_threshold > 0.0) ||
        cfg.forward.beam_gain_threshold > 1.0) {
        err = "forward.beam_gain_threshold must be in (0, 1]";
        return false;
    }
    return true;
}

bool ensureStage3Dirs(const std::string &output_dir, std::string &err)
{
    const char *names[] = {"config", "configs", "data", "truth", "debug", "figures", "logs", "reports", "algorithm_result"};
    if (!mkdirRecursive(output_dir, err)) return false;
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); ++i) {
        if (!mkdirRecursive(pathJoin(output_dir, names[i]), err)) return false;
    }
    return true;
}

bool writeDefaultStage3Config(const std::string &path, std::string &err)
{
    std::ofstream os(path.c_str());
    if (!os) {
        err = "failed to open " + path;
        return false;
    }
    os << "{\n"
       << "  \"case_id\": \"stage3_p45_mirror\",\n"
       << "  \"output_dir\": \"outputs/stage3_p45_mirror\",\n"
       << "  \"system\": {\"fc_ghz\": 16.0, \"bandwidth_mhz\": 50.0, \"fs_mhz\": 60.0,\n"
       << "    \"pulse_width_us\": 130.0, \"prf_hz\": 1300.0, \"sample_delay_us\": 488.0,\n"
       << "    \"sample_window_us\": 197.0, \"ddc_len\": 11820, \"fft_len\": 12288,\n"
       << "    \"pc_crop_start\": 3864, \"pc_crop_len\": 4096, \"scan_min_deg\": -60.0,\n"
       << "    \"scan_step_deg\": 2.0, \"beam_count\": 61, \"beam_width_deg\": 2.28,\n"
       << "    \"pulse_num\": 130, \"platform_height_m\": 6000.0,\n"
       << "    \"platform_speed_mps\": 60.0, \"d_chan_m\": 0.17,\n"
       << "    \"iq_data_type\": \"float32\", \"new_protocol_channel_count\": 2,\n"
       << "    \"new_protocol_read_channel_1\": 1, \"new_protocol_read_channel_2\": 2,\n"
       << "    \"origin_lat_deg\": 40.4512107203, \"origin_lon_deg\": 116.985931582,\n"
       << "    \"projection_ref_lon_deg\": 117.0, \"squint_side\": 1},\n"
       << "  \"canvas\": {\"resolution_m\": 50.0, \"margin_m\": 0.0,\n"
       << "    \"range_min_m\": 82800.0, \"range_max_m\": 93000.0, \"ground_z_m\": 0.0,\n"
       << "    \"max_cells\": 5000000},\n"
       << "  \"scene\": {\"mode\": \"mirror\", \"range_min_m\": 82800.0, \"range_max_m\": 93000.0,\n"
       << "    \"azimuth_min_deg\": -60.0, \"azimuth_max_deg\": 60.0, \"ground_z_m\": 0.0,\n"
       << "    \"clutter_amplitude_scale\": 1.0, \"area_clutter\": {\"enabled\": false},\n"
       << "    \"roi\": {\"enabled\": false, \"beam_id_1based\": 31,\n"
       << "      \"center_slant_range_m\": 87900.0, \"range_extent_m\": 600.0,\n"
       << "      \"azimuth_extent_m\": 300.0, \"resolution_m\": 0.3, \"margin_m\": 0.0},\n"
       << "    \"thermal_noise_enabled\": false, \"thermal_noise_power\": 0.0},\n"
       << "  \"sar_source\": {\"image_path\": \"simulator/scenarios/PGA_KuSAR_Block0.tif\",\n"
       << "    \"input_type\": \"geotiff\", \"image_type\": \"intensity\",\n"
       << "    \"pixel_size_range_m\": 0.3, \"pixel_size_azimuth_m\": 0.3,\n"
       << "    \"source_center_col\": -1, \"source_center_row\": -1,\n"
       << "    \"row_increases_with_azimuth\": true, \"col_increases_with_range\": true,\n"
       << "    \"valid_crop_enabled\": true, \"valid_col_start\": 5,\n"
       << "    \"valid_col_end_exclusive\": 8170, \"valid_row_start\": 0,\n"
       << "    \"valid_row_end_exclusive\": 3700,\n"
       << "    \"percentile_low\": 1.0, \"percentile_high\": 99.0,\n"
       << "    \"percentile_normalize\": true},\n"
       << "  \"tiling\": {\"mode\": \"mirror\", \"random_start_offset\": false,\n"
       << "    \"reference_beam_id_1based\": 31, \"anchor_slant_range_m\": 87900.0,\n"
       << "    \"tile_gain_jitter_db\": 0.0,\n"
       << "    \"phase_policy\": \"independent_random_per_ground_cell\", \"random_seed\": 202606},\n"
       << "  \"scatterer_extraction\": {\"amplitude_scale\": 1.0, \"random_seed\": 202606},\n"
       << "  \"forward\": {\"platform_mode\": \"ideal_straight\", \"beam_pattern_mode\": \"gaussian\",\n"
       << "    \"beam_gain_threshold\": 0.01, \"channel_phase_mode\": \"ctdr\",\n"
       << "    \"lfm_time_reference\": \"center\",\n"
       << "    \"chirp_phase_sign\": 1, \"carrier_phase_sign\": -1,\n"
       << "    \"period_start\": 0, \"period_count\": 1, \"beam_start\": 1,\n"
       << "    \"beam_count\": 61, \"pulse_start\": 0, \"pulse_count\": 130,\n"
       << "    \"backend\": \"cpu\", \"compare_cpu_cuda\": false,\n"
       << "    \"cuda_batch_pulses\": 130, \"dbs_output_resolution_m\": 3.0,\n"
       << "    \"truth_max_rows\": 250000, \"dry_run\": false},\n"
       << "  \"targets\": {\"enabled\": false, \"target_config_path\": \"targets.json\",\n"
       << "    \"target_snr_db\": 25.0, \"amplitude_mode\": \"snr_db\"}\n"
       << "}\n";
    return true;
}

std::string pathJoin(const std::string &a, const std::string &b)
{
    if (a.empty()) return b;
    if (a[a.size() - 1] == '/') return a + b;
    return a + "/" + b;
}

} // namespace stage3
} // namespace gmti
