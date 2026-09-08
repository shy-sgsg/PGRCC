#include "runtime_diagnostics.hpp"

#include "config_structs.hpp"

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <dirent.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <sstream>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

#ifndef GMTI_BUILD_TYPE
#ifdef NDEBUG
#define GMTI_BUILD_TYPE "Release"
#else
#define GMTI_BUILD_TYPE "Debug"
#endif
#endif

namespace gmti {
namespace runtime {
namespace {

struct TimingMetric {
    std::string case_id;
    std::string run_id;
    std::string result_id;
    int period_id;
    std::string scope_name;
    std::string start_time;
    std::string end_time;
    long long elapsed_ms;
    std::string extra;
};

struct RunState {
    bool initialized = false;
    bool diagnostics_enabled = false;
    std::string case_id;
    std::string run_id;
    std::string result_id;
    std::string start_time;
    std::string end_time;
    std::string git_commit = "unknown";
    std::string build_type = GMTI_BUILD_TYPE;
    std::string executable;
    std::string config_path;
    std::string working_directory;
    std::string input_data_path;
    std::string output_dir;
    RunPaths paths;
    std::vector<TimingMetric> timings;
};

std::mutex& stateMutex()
{
    static std::mutex m;
    return m;
}

RunState& state()
{
    static RunState s;
    return s;
}

std::string jsonEscape(const std::string& s)
{
    std::ostringstream os;
    for (char ch : s) {
        switch (ch) {
        case '\\': os << "\\\\"; break;
        case '"': os << "\\\""; break;
        case '\n': os << "\\n"; break;
        case '\r': os << "\\r"; break;
        case '\t': os << "\\t"; break;
        default:
            if (static_cast<unsigned char>(ch) < 0x20) {
                os << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                   << static_cast<int>(static_cast<unsigned char>(ch))
                   << std::dec << std::setfill(' ');
            } else {
                os << ch;
            }
        }
    }
    return os.str();
}

std::string q(const std::string& s)
{
    return "\"" + jsonEscape(s) + "\"";
}

std::string jsonNullableDouble(bool available, double value)
{
    if (!available) {
        return "null";
    }
    std::ostringstream os;
    os << std::setprecision(15) << value;
    return os.str();
}

std::string csvEscape(const std::string& s)
{
    if (s.find_first_of(",\"\n\r") == std::string::npos) {
        return s;
    }
    std::string out = "\"";
    for (char ch : s) {
        if (ch == '"') out += "\"\"";
        else out.push_back(ch);
    }
    out += "\"";
    return out;
}

std::string pathJoin(std::string dir, const std::string& file)
{
    if (dir.empty()) {
        return file;
    }
    if (dir.back() != '/' && dir.back() != '\\') {
        dir.push_back('/');
    }
    return dir + file;
}

bool mkdirP(const std::string& dir)
{
    if (dir.empty()) return true;
    std::string cur;
    size_t i = 0;
    if (dir[0] == '/') {
        cur = "/";
        i = 1;
    }
    for (; i < dir.size(); ++i) {
        cur.push_back(dir[i]);
        if (dir[i] == '/' || i == dir.size() - 1) {
            if (cur == "/" || cur == "./" || cur == ".") {
                continue;
            }
            if (::mkdir(cur.c_str(), 0755) != 0 && errno != EEXIST) {
                return false;
            }
        }
    }
    return true;
}

bool fileExists(const std::string& path)
{
    struct stat st {};
    return !path.empty() && ::stat(path.c_str(), &st) == 0 && S_ISREG(st.st_mode);
}

std::string basenameNoExt(const std::string& path)
{
    const size_t slash = path.find_last_of("/\\");
    std::string name = (slash == std::string::npos) ? path : path.substr(slash + 1);
    const size_t dot = name.find_last_of('.');
    if (dot != std::string::npos && dot > 0) {
        name = name.substr(0, dot);
    }
    return name.empty() ? "unknown_case" : name;
}

std::string currentWorkingDirectory()
{
    char buf[4096];
    if (::getcwd(buf, sizeof(buf))) {
        return std::string(buf);
    }
    return "unknown";
}

std::string makeRunIdFromTime()
{
    const auto now = std::chrono::system_clock::now();
    const std::time_t t = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
    localtime_r(&t, &tm);
    const auto micros = std::chrono::duration_cast<std::chrono::microseconds>(
        now.time_since_epoch()).count() % 1000000;
    std::ostringstream os;
    os << std::put_time(&tm, "%Y%m%d_%H%M%S") << "_"
       << std::setw(6) << std::setfill('0') << micros;
    return os.str();
}

std::string runCommandOneLine(const char* cmd)
{
    FILE* pipe = ::popen(cmd, "r");
    if (!pipe) return "unknown";
    char buf[256];
    std::string out;
    if (std::fgets(buf, sizeof(buf), pipe)) {
        out = buf;
    }
    const int rc = ::pclose(pipe);
    if (rc != 0 || out.empty()) {
        return "unknown";
    }
    while (!out.empty() && (out.back() == '\n' || out.back() == '\r' || out.back() == ' ')) {
        out.pop_back();
    }
    return out.empty() ? "unknown" : out;
}

std::string protocolType(const Config& cfg)
{
    return cfg.INFO_Type ? "new_protocol" : "legacy_protocol";
}

std::string payloadFormat(const Config& cfg)
{
    if (cfg.INFO_Type) return "float32_ch1_iq_ch2_iq";
    if (cfg.channel_mode == "separate") return "float32_single_channel_iq";
    if (cfg.channel_mode == "interleaved") return "float32_ch1_iq_ch2_iq";
    return "not_available";
}

std::string assignmentModeName(int v)
{
    if (v == 0) return "greedy_nearest_neighbor";
    if (v == 1) return "hungarian";
    return "unknown";
}

std::string distanceModeName(int v)
{
    if (v == 0) return "euclidean";
    if (v == 1) return "mahalanobis_squared";
    return "unknown";
}

int beamCount(const Config& cfg)
{
    if (cfg.scan_mode == ScanMode::Mechanical) return 0;
    if (cfg.wavepos_skip <= 0 || cfg.wavepos_ed < cfg.wavepos_st) {
        return cfg.az_count;
    }
    return (cfg.wavepos_ed - cfg.wavepos_st) / cfg.wavepos_skip + 1;
}

std::size_t prtCountPerPeriod(const Config& cfg)
{
    if (cfg.scan_mode == ScanMode::Mechanical) {
        if (cfg.mechanical_scan_runtime) {
            return cfg.mechanical_scan_runtime->pulse_meta.size();
        }
        return cfg.mechanical_scan.acquisition_scan_prt_count > 0
            ? static_cast<std::size_t>(
                  cfg.mechanical_scan.acquisition_scan_prt_count)
            : 0U;
    }
    return static_cast<std::size_t>(beamCount(cfg)) *
           static_cast<std::size_t>(effectivePulseNum(cfg));
}

double scanStepDeg(const Config& cfg)
{
    const int count = beamCount(cfg);
    if (count <= 1) return 0.0;
    return (cfg.scan_max_deg - cfg.scan_min_deg) / static_cast<double>(count - 1);
}

long long bytesPerPeriod(const Config& cfg)
{
    return static_cast<long long>(prtCountPerPeriod(cfg)) *
           static_cast<long long>(cfg.pkg_bytes);
}

std::vector<std::string> listFilesWithPrefixSuffix(const std::string& dir,
                                                   const std::string& prefix,
                                                   const std::string& suffix,
                                                   const std::string& exclude_suffix = "")
{
    std::vector<std::string> out;
    DIR* dp = ::opendir(dir.c_str());
    if (!dp) return out;
    while (dirent* ent = ::readdir(dp)) {
        const std::string name = ent->d_name;
        if (name == "." || name == "..") continue;
        const bool prefix_ok = prefix.empty() || name.compare(0, prefix.size(), prefix) == 0;
        const bool suffix_ok = suffix.empty() ||
            (name.size() >= suffix.size() &&
             name.compare(name.size() - suffix.size(), suffix.size(), suffix) == 0);
        const bool exclude_ok = exclude_suffix.empty() ||
            name.size() < exclude_suffix.size() ||
            name.compare(name.size() - exclude_suffix.size(),
                         exclude_suffix.size(),
                         exclude_suffix) != 0;
        if (prefix_ok && suffix_ok && exclude_ok) {
            out.push_back(pathJoin(dir, name));
        }
    }
    ::closedir(dp);
    std::sort(out.begin(), out.end());
    return out;
}

std::string jsonStringArray(const std::vector<std::string>& values, int indent)
{
    if (values.empty()) return "[]";
    std::ostringstream os;
    os << "[\n";
    const std::string pad(static_cast<size_t>(indent), ' ');
    for (size_t i = 0; i < values.size(); ++i) {
        os << pad << q(values[i]);
        if (i + 1 < values.size()) os << ",";
        os << "\n";
    }
    os << std::string(static_cast<size_t>(indent - 2), ' ') << "]";
    return os.str();
}

std::string latestFile(const std::vector<std::string>& files)
{
    return files.empty() ? "" : files.back();
}

void writeTimingCsvUnlocked()
{
    const RunState& s = state();
    if (s.paths.timing_metrics_csv.empty()) return;
    std::ofstream ofs(s.paths.timing_metrics_csv.c_str());
    if (!ofs) {
        std::cerr << "[TIMING][WARN] cannot write " << s.paths.timing_metrics_csv << std::endl;
        return;
    }
    ofs << "case_id,run_id,result_id,period_id,scope_name,start_time,end_time,elapsed_ms,extra\n";
    for (const auto& m : s.timings) {
        ofs << csvEscape(m.case_id) << ","
            << csvEscape(m.run_id) << ","
            << csvEscape(m.result_id) << ","
            << m.period_id << ","
            << csvEscape(m.scope_name) << ","
            << csvEscape(m.start_time) << ","
            << csvEscape(m.end_time) << ","
            << m.elapsed_ms << ","
            << csvEscape(m.extra) << "\n";
    }
}

void writeManifest(const Config& cfg, bool normal_exit, int exit_code,
                   const std::string& notes)
{
    RunState& s = state();
    if (s.paths.run_manifest_json.empty()) return;

    const std::vector<std::string> gmti_bins = listFilesWithPrefixSuffix(cfg.result_add, "GMTI", ".bin", "_track.bin");
    const std::vector<std::string> gmti_tracks = listFilesWithPrefixSuffix(cfg.result_add, "GMTI", "_track.bin");
    const std::vector<std::string> pngs = listFilesWithPrefixSuffix(cfg.result_add, "", ".png");
    const std::vector<std::string> txts = listFilesWithPrefixSuffix(cfg.result_add, "", ".txt");
    const std::string detection_csv = pathJoin(cfg.result_add, "detection_results.csv");
    const std::string calibration_dir = pathJoin(cfg.result_add, "calibration");
    const std::string single_point_calibration_json =
        pathJoin(calibration_dir, "single_point_bin_calibration.json");
    const std::string single_point_calibration_csv =
        pathJoin(calibration_dir, "single_point_bin_calibration.csv");
    const std::string single_point_calibration_report =
        pathJoin(calibration_dir, "single_point_calibration_report.md");

    std::ofstream os(s.paths.run_manifest_json.c_str());
    if (!os) {
        std::cerr << "[MANIFEST][WARN] cannot write " << s.paths.run_manifest_json << std::endl;
        return;
    }
    os << "{\n";
    os << "  \"case_id\": " << q(s.case_id) << ",\n";
    os << "  \"run_id\": " << q(s.run_id) << ",\n";
    os << "  \"result_id\": " << q(s.result_id) << ",\n";
    os << "  \"git_commit\": " << q(s.git_commit) << ",\n";
    os << "  \"build_type\": " << q(s.build_type) << ",\n";
    os << "  \"executable\": " << q(s.executable) << ",\n";
    os << "  \"config_path\": " << q(s.config_path) << ",\n";
    os << "  \"input_data_path\": " << q(s.input_data_path) << ",\n";
    os << "  \"output_dir\": " << q(s.output_dir) << ",\n";
    os << "  \"start_time\": " << q(s.start_time) << ",\n";
    os << "  \"end_time\": " << q(s.end_time) << ",\n";
    os << "  \"normal_exit\": " << (normal_exit ? "true" : "false") << ",\n";
    os << "  \"exit_code\": " << exit_code << ",\n";
    os << "  \"generated_files\": {\n";
    os << "    \"runtime_config_dump_json\": " << q(s.paths.runtime_config_json) << ",\n";
    os << "    \"runtime_config_dump_txt\": " << q(s.paths.runtime_config_txt) << ",\n";
    os << "    \"timing_metrics_csv\": " << q(s.paths.timing_metrics_csv) << ",\n";
    os << "    \"algorithm_log\": " << q("not_available") << ",\n";
    os << "    \"gmti_bin\": " << q(latestFile(gmti_bins)) << ",\n";
    os << "    \"gmti_track_bin\": " << q(latestFile(gmti_tracks)) << ",\n";
    os << "    \"detection_results_csv\": " << q(fileExists(detection_csv) ? detection_csv : "") << ",\n";
    os << "    \"single_point_bin_calibration_json\": " << q(fileExists(single_point_calibration_json) ? single_point_calibration_json : "") << ",\n";
    os << "    \"single_point_bin_calibration_csv\": " << q(fileExists(single_point_calibration_csv) ? single_point_calibration_csv : "") << ",\n";
    os << "    \"single_point_calibration_report\": " << q(fileExists(single_point_calibration_report) ? single_point_calibration_report : "") << ",\n";
    os << "    \"png_outputs\": " << jsonStringArray(pngs, 6) << ",\n";
    os << "    \"txt_outputs\": " << jsonStringArray(txts, 6) << "\n";
    os << "  },\n";
    os << "  \"notes\": " << q(notes) << "\n";
    os << "}\n";
}

void writeRuntimeConfigJson(const Config& cfg)
{
    RunState& s = state();
    std::ofstream os(s.paths.runtime_config_json.c_str());
    if (!os) {
        std::cerr << "[PARAM_DUMP][WARN] cannot write "
                  << s.paths.runtime_config_json << std::endl;
        return;
    }
    const int proc_pulses = effectivePulseNum(cfg);
    const std::size_t period_prts = prtCountPerPeriod(cfg);
    const double period_time_sec = cfg.PRF > 0.0
        ? static_cast<double>(period_prts) / cfg.PRF : 0.0;
    const double doppler_bin_hz = proc_pulses > 0 ? cfg.PRF / proc_pulses : 0.0;
    const double velocity_res = cfg.lambda * doppler_bin_hz / 2.0;
    const double first_blind = cfg.lambda * cfg.PRF / 2.0;

    os << "{\n";
    os << "  \"run_info\": {\n";
    os << "    \"case_id\": " << q(s.case_id) << ",\n";
    os << "    \"run_id\": " << q(s.run_id) << ",\n";
    os << "    \"result_id\": " << q(s.result_id) << ",\n";
    os << "    \"start_time\": " << q(s.start_time) << ",\n";
    os << "    \"git_commit\": " << q(s.git_commit) << ",\n";
    os << "    \"build_type\": " << q(s.build_type) << ",\n";
    os << "    \"runtime_mode\": " << q(cfg.runtime_mode) << ",\n";
    os << "    \"runtime_diagnostics_enabled\": " << (cfg.runtime_diagnostics_enabled ? "true" : "false") << ",\n";
    os << "    \"executable\": " << q(s.executable) << ",\n";
    os << "    \"config_path\": " << q(s.config_path) << ",\n";
    os << "    \"working_directory\": " << q(s.working_directory) << "\n";
    os << "  },\n";
    os << "  \"file_layout\": {\n";
    os << "    \"input_data_path\": " << q(s.input_data_path) << ",\n";
    os << "    \"output_dir\": " << q(cfg.result_add) << ",\n";
    os << "    \"protocol_type\": " << q(protocolType(cfg)) << ",\n";
    os << "    \"header_size_bytes\": " << cfg.info_len << ",\n";
    os << "    \"payload_format\": " << q(payloadFormat(cfg)) << ",\n";
    os << "    \"pulse_len\": " << cfg.pulse_len << ",\n";
    os << "    \"read_pulse_num\": " << cfg.read_pulse_num << ",\n";
    os << "    \"pulse_num\": " << cfg.pulse_num << ",\n";
    os << "    \"beam_count\": " << beamCount(cfg) << ",\n";
    os << "    \"wavepos_st\": " << cfg.wavepos_st << ",\n";
    os << "    \"wavepos_ed\": " << cfg.wavepos_ed << ",\n";
    os << "    \"wavepos_skip\": " << cfg.wavepos_skip << ",\n";
    os << "    \"bytes_per_prt\": " << cfg.pkg_bytes << ",\n";
    os << "    \"prt_per_period\": "
       << period_prts
       << ",\n";
    os << "    \"input_integrity_status\": "
       << q(cfg.echo_cycle_view.partial_scan
                ? "valid_partial_first_scan_missing_leading_beams"
                : "valid_complete_scan_or_file") << ",\n";
    os << "    \"partial_scan\": "
       << (cfg.echo_cycle_view.partial_scan ? "true" : "false") << ",\n";
    os << "    \"partial_first_scan_beam\": "
       << cfg.echo_cycle_view.first_scan_beam << ",\n";
    os << "    \"partial_scan_beam_count\": "
       << cfg.echo_cycle_view.scan_beam_count << ",\n";
    os << "    \"missing_leading_prt_count\": "
       << cfg.echo_cycle_view.missing_leading_prt_count << "\n";
    os << "  },\n";
    os << "  \"scan\": {\n";
    os << "    \"scan_mode\": " << q(scanModeName(cfg.scan_mode)) << ",\n";
    os << "    \"acquisition_scan_prt_count\": "
       << cfg.mechanical_scan.acquisition_scan_prt_count << ",\n";
    os << "    \"cpi_pulse_count\": " << cfg.mechanical_scan.cpi_pulse_count << ",\n";
    os << "    \"cpi_step_pulse\": " << cfg.mechanical_scan.cpi_step_pulse << ",\n";
    os << "    \"max_cpi_angle_span_deg\": "
       << cfg.mechanical_scan.max_cpi_angle_span_deg << ",\n";
    os << "    \"use_actual_servo_angle\": "
       << (cfg.mechanical_scan.use_actual_servo_angle ? "true" : "false") << ",\n";
    os << "    \"allow_scan_reverse\": "
       << (cfg.mechanical_scan.allow_scan_reverse ? "true" : "false") << ",\n";
    os << "    \"scan_edge_guard_deg\": "
       << cfg.mechanical_scan.scan_edge_guard_deg << ",\n";
    os << "    \"scan_direction_deadband_deg\": "
       << cfg.mechanical_scan.scan_direction_deadband_deg << ",\n";
    os << "    \"phase_center_rotation_enable\": "
       << (cfg.mechanical_scan.phase_center_rotation_enable ? "true" : "false") << ",\n";
    os << "    \"phase_center_mount_angle_deg\": "
       << cfg.mechanical_scan.phase_center_mount_angle_deg << ",\n";
    os << "    \"phase_center_rotation_sign\": "
       << cfg.mechanical_scan.phase_center_rotation_sign << ",\n";
    os << "    \"ctdr_servo_azimuth_deg\": "
       << jsonNullableDouble(std::isfinite(cfg.ctdr_servo_azimuth_deg),
                             cfg.ctdr_servo_azimuth_deg) << ",\n";
    os << "    \"phase_center_geometry_source\": "
       << q(cfg.scan_mode == ScanMode::Mechanical &&
                   cfg.mechanical_scan.phase_center_rotation_enable
               ? "per_prt_servo_header_for_fusion_center_pose_for_ctdr"
               : "legacy_velocity_aligned_baseline") << ",\n";
    os << "    \"enable_cpi_dedup\": "
       << (cfg.mechanical_scan.enable_cpi_dedup ? "true" : "false") << ",\n";
    os << "    \"processing_window_count\": "
       << (cfg.mechanical_scan_runtime
               ? cfg.mechanical_scan_runtime->processing_window_indices.size() : 0U)
       << ",\n";
    os << "    \"source_prt_count\": "
       << (cfg.mechanical_scan_runtime
               ? cfg.mechanical_scan_runtime->pulse_meta.size() : 0U) << "\n";
    os << "  },\n";
    os << "  \"waveform\": {\n";
    os << "    \"fc_hz\": " << std::setprecision(15) << cfg.fc << ",\n";
    os << "    \"Br_hz\": " << cfg.Br << ",\n";
    os << "    \"fs_hz\": " << cfg.fs << ",\n";
    os << "    \"Tr_sec\": " << cfg.Tr << ",\n";
    os << "    \"PRF_hz\": " << cfg.PRF << ",\n";
    os << "    \"lambda_m\": " << cfg.lambda << ",\n";
    os << "    \"sample_delay_us\": "
       << jsonNullableDouble(cfg.has_sample_delay_us, cfg.sample_delay_us) << "\n";
    os << "  },\n";
    os << "  \"pulse_compression\": {\n";
    os << "    \"range_fft_len\": " << effectiveRangeFftLen(cfg) << ",\n";
    os << "    \"fft_pulse_len\": " << effectiveRangeFftLen(cfg) << ",\n";
    os << "    \"range_crop_start\": " << cfg.range_crop_start << ",\n";
    os << "    \"range_compress_len\": " << cfg.range_compress_len << ",\n";
    os << "    \"pc_crop_start\": " << cfg.range_crop_start << ",\n";
    os << "    \"pc_crop_len\": " << cfg.range_compress_len << ",\n";
    os << "    \"range_compression_window\": " << q(cfg.range_compression_window) << ",\n";
    os << "    \"range_compression_kaiser_beta\": " << cfg.range_compression_kaiser_beta << ",\n";
    os << "    \"range_compression_bandwidth_scale\": " << cfg.range_compression_bandwidth_scale << ",\n";
    os << "    \"range_compression_window_normalize\": "
       << (cfg.range_compression_window_normalize ? "true" : "false") << ",\n";
    os << "    \"azimuth_fft_window\": " << q(cfg.azimuth_fft_window) << ",\n";
    os << "    \"azimuth_fft_window_normalize\": "
       << (cfg.azimuth_fft_window_normalize ? "true" : "false") << "\n";
    os << "  },\n";
    os << "  \"geometry\": {\n";
    os << "    \"scan_min_deg\": " << cfg.scan_min_deg << ",\n";
    os << "    \"scan_max_deg\": " << cfg.scan_max_deg << ",\n";
    os << "    \"scan_step_deg\": " << scanStepDeg(cfg) << ",\n";
    os << "    \"az_count\": " << cfg.az_count << ",\n";
    os << "    \"beam_width_deg\": " << cfg.beamwidth_deg << ",\n";
    os << "    \"dbs_mosaic_mode\": \"" << cfg.dbs_mosaic_mode << "\",\n";
    os << "    \"squint_angle\": " << cfg.squint_angle << ",\n";
    os << "    \"estimate_error_angle\": " << (cfg.estimate_error_angle ? "true" : "false") << ",\n";
    os << "    \"beam_pointing_bias\": null,\n";
    os << "    \"beam_pointing_bias_source\": " << q("no_explicit_algorithm_config_field") << ",\n";
    os << "    \"fusion_min_valid_beam_ratio\": " << cfg.fusion_min_valid_beam_ratio << ",\n";
    os << "    \"loc_beam_gate_deg\": " << cfg.loc_beam_gate_deg << "\n";
    os << "  },\n";
    os << "  \"channel_and_gmti\": {\n";
    os << "    \"iq_data_type\": " << q(cfg.iq_data_type) << ",\n";
    os << "    \"new_protocol_channel_count\": " << cfg.new_protocol_channel_count << ",\n";
    os << "    \"new_protocol_read_channel_1\": " << cfg.new_protocol_read_channel_1 << ",\n";
    os << "    \"new_protocol_read_channel_2\": " << cfg.new_protocol_read_channel_2 << ",\n";
    os << "    \"new_protocol_gpu_preprocess\": "
       << (cfg.new_protocol_gpu_preprocess ? "true" : "false") << ",\n";
    os << "    \"enable_four_channel_fusion\": "
       << (cfg.enable_four_channel_fusion ? "true" : "false") << ",\n";
    os << "    \"four_channel_phase_compensation_enable\": "
       << (cfg.four_channel_phase_compensation_enable ? "true" : "false") << ",\n";
    os << "    \"four_channel_fusion_channel_3\": "
       << cfg.four_channel_fusion_channel_3 << ",\n";
    os << "    \"four_channel_fusion_channel_4\": "
       << cfg.four_channel_fusion_channel_4 << ",\n";
    os << "    \"four_channel_squint_side\": " << cfg.four_channel_fusion_squint_side << ",\n";
    os << "    \"four_channel_carrier_phase_sign\": "
       << cfg.four_channel_carrier_phase_sign << ",\n";
    os << "    \"four_channel_offsets_local_m\": [";
    for (std::size_t ch = 0; ch < cfg.four_channel_offsets_m.size(); ++ch) {
        if (ch != 0) os << ", ";
        os << "[" << cfg.four_channel_offsets_m[ch][0] << ", "
           << cfg.four_channel_offsets_m[ch][1] << ", "
           << cfg.four_channel_offsets_m[ch][2] << "]";
    }
    os << "],\n";
    os << "    \"new_protocol_file_first_beam\": " << cfg.new_protocol_file_first_beam << ",\n";
    os << "    \"new_protocol_file_scan_beam_count\": "
       << cfg.new_protocol_file_scan_beam_count << ",\n";
    os << "    \"new_protocol_file_period_index\": "
       << cfg.new_protocol_file_period_index << ",\n";
    os << "    \"stage2_period_id\": " << cfg.stage2_period_id << ",\n";
    os << "    \"new_protocol_velocity_scale\": " << cfg.new_protocol_velocity_scale << ",\n";
    os << "    \"new_protocol_velocity_source\": " << q(cfg.new_protocol_velocity_source) << ",\n";
    os << "    \"shm_scan_beam_count\": " << cfg.shm_scan_beam_count << ",\n";
    os << "    \"shm_allow_partial_first_scan\": "
       << (cfg.shm_allow_partial_first_scan ? "true" : "false") << ",\n";
    os << "    \"d_chan\": " << cfg.d_channel << ",\n";
    os << "    \"two_channel_phase_model\": \"" << cfg.two_channel_phase_model << "\",\n";
    os << "    \"rx_baseline_sign\": " << cfg.rx_baseline_sign << ",\n";
    os << "    \"channel_phase_sign\": " << cfg.channel_phase_sign << ",\n";
    os << "    \"calib_coef\": " << cfg.calib_coef << ",\n";
    os << "    \"channel_phase_enabled\": " << q("called_in_processOnePeriod") << ",\n";
    os << "    \"channel_phase_source\": " << q("rg_correct_CUDA applied in processOnePeriod/processOnePeriodFusionCache") << ",\n";
    os << "    \"ctdr_position_phase_source\": "
       << q("wrap(corrected phase_map + first applied range phi_fit at target bin)")
       << ",\n";
    os << "    \"cancellation_enabled\": " << q("called_in_processOnePeriod") << ",\n";
    os << "    \"cancellation_source\": " << q("clutter_cancel_38_paper_1_p38_cuda and clutter_cancel_38_paper_1_cuda called in processOnePeriod/processOnePeriodFusionCache") << ",\n";
    os << "    \"motion_comp_enable\": " << (cfg.motion_comp_enable ? "true" : "false") << ",\n";
    os << "    \"low_radial_velocity_filter_enabled\": "
       << (cfg.low_radial_velocity_filter_enabled ? "true" : "false") << ",\n";
    os << "    \"low_radial_velocity_threshold_mps\": "
       << cfg.low_radial_velocity_threshold_mps << ",\n";
    os << "    \"p38_slope_upper_bound_filter_enabled\": "
       << (cfg.p38_slope_upper_bound_filter_enabled ? "true" : "false") << ",\n";
    os << "    \"p38_slope_upper_bound_rad_per_hz\": "
       << cfg.p38_slope_upper_bound_rad_per_hz << ",\n";
    os << "    \"adjacent_beam_duplicate_suppression_enabled\": "
       << (cfg.adjacent_beam_duplicate_suppression_enabled ? "true" : "false") << ",\n";
    os << "    \"adjacent_beam_duplicate_position_gate_m\": "
       << cfg.adjacent_beam_duplicate_position_gate_m << ",\n";
    os << "    \"adjacent_beam_duplicate_velocity_gate_mps\": "
       << cfg.adjacent_beam_duplicate_velocity_gate_mps << ",\n";
    os << "    \"motion_comp_apply_to_localization\": "
       << (cfg.motion_comp_apply_to_localization ? "true" : "false") << ",\n";
    os << "    \"motion_comp_analytic_enable\": " << (cfg.motion_comp_analytic_enable ? "true" : "false") << ",\n";
    os << "    \"motion_comp_use_row_doppler\": " << (cfg.motion_comp_use_row_doppler ? "true" : "false") << ",\n";
    os << "    \"motion_comp_solver\": " << q(cfg.motion_comp_solver) << ",\n";
    os << "    \"motion_comp_iter\": " << cfg.motion_comp_iter << ",\n";
    os << "    \"motion_comp_iter_tol_mps\": " << cfg.motion_comp_iter_tol_mps << ",\n";
    os << "    \"p38_enhanced_enable\": "
       << (cfg.p38_enhanced_enable ? "true" : "false") << ",\n";
    os << "    \"p38_robust_fit_method\": \""
       << (cfg.p38_enhanced_enable ? "iterative_mad" : "huber") << "\",\n";
    os << "    \"csi_bypass_enable\": " << (cfg.csi_bypass_enable ? "true" : "false") << ",\n";
    os << "    \"csi_metrics_enable\": " << (cfg.csi_metrics_enable ? "true" : "false") << ",\n";
    os << "    \"csi_metrics_dump_power_maps\": " << (cfg.csi_metrics_dump_power_maps ? "true" : "false") << ",\n";
    os << "    \"csi_metrics_dump_intermediate_maps\": " << (cfg.csi_metrics_dump_intermediate_maps ? "true" : "false") << ",\n";
    os << "    \"csi_metrics_beam_id\": " << cfg.csi_metrics_beam_id << ",\n";
    os << "    \"metrics_target_half_range_bins\": " << cfg.metrics_target_half_range_bins << ",\n";
    os << "    \"metrics_target_half_doppler_bins\": " << cfg.metrics_target_half_doppler_bins << ",\n";
    os << "    \"metrics_guard_range_bins\": " << cfg.metrics_guard_range_bins << ",\n";
    os << "    \"metrics_guard_doppler_bins\": " << cfg.metrics_guard_doppler_bins << ",\n";
    os << "    \"metrics_background_range_bins\": " << cfg.metrics_background_range_bins << ",\n";
    os << "    \"metrics_background_doppler_bins\": " << cfg.metrics_background_doppler_bins << ",\n";
    os << "    \"metrics_strong_peak_threshold_db\": " << cfg.metrics_strong_peak_threshold_db << ",\n";
    os << "    \"metrics_min_valid_background_cells\": " << cfg.metrics_min_valid_background_cells << ",\n";
    os << "    \"cfar_guard_cells\": " << cfg.cfar_guard_cells << ",\n";
    os << "    \"cfar_background_cells\": " << cfg.cfar_background_cells << ",\n";
    os << "    \"cfar_doppler_circular\": "
       << (cfg.cfar_doppler_circular ? "true" : "false") << ",\n";
    os << "    \"cluster_strong_small_enable\": "
       << (cfg.cluster_strong_small_enable ? "true" : "false") << ",\n";
    os << "    \"cluster_strong_small_min_points\": "
       << cfg.cluster_strong_small_min_points << ",\n";
    os << "    \"cluster_strong_small_peak_over_median_db\": "
       << cfg.cluster_strong_small_peak_over_median_db << ",\n";
    os << "    \"cfar_type\": " << q(cfg.cfar_type) << ",\n";
    os << "    \"dynamic_cfar_enable\": "
       << (cfg.dynamic_cfar_enable ? "true" : "false") << ",\n";
    os << "    \"csi_detection_band_mode\": "
       << q(cfg.csi_detection_band_mode) << ",\n";
    os << "    \"csi_split_boundary_guard_rows\": "
       << cfg.csi_split_boundary_guard_rows << ",\n";
    os << "    \"csi_split_merge_doppler_bins\": "
       << cfg.csi_split_merge_doppler_bins << ",\n";
    os << "    \"csi_split_merge_range_bins\": "
       << cfg.csi_split_merge_range_bins << ",\n";
    os << "    \"csi_split_in_band_cfar_type\": "
       << q(cfg.csi_split_in_band_cfar_type) << ",\n";
    os << "    \"csi_split_small_cluster_enable\": "
       << (cfg.csi_split_small_cluster_enable ? "true" : "false") << ",\n";
    os << "    \"csi_split_small_cluster_min_points\": "
       << cfg.csi_split_small_cluster_min_points << ",\n";
    os << "    \"csi_split_small_cluster_peak_over_median_db\": "
       << cfg.csi_split_small_cluster_peak_over_median_db << ",\n";
    os << "    \"csi_split_small_cluster_near_doppler_rows\": "
       << cfg.csi_split_small_cluster_near_doppler_rows << ",\n";
    os << "    \"csi_split_small_cluster_near_range_bins\": "
       << cfg.csi_split_small_cluster_near_range_bins << ",\n";
    os << "    \"csi_split_vertical_line_filter_enable\": "
       << (cfg.csi_split_vertical_line_filter_enable ? "true" : "false") << ",\n";
    os << "    \"csi_split_vertical_line_min_doppler_rows\": "
       << cfg.csi_split_vertical_line_min_doppler_rows << ",\n";
    os << "    \"csi_split_vertical_line_max_range_bins\": "
       << cfg.csi_split_vertical_line_max_range_bins << ",\n";
    os << "    \"csi_split_vertical_line_peak_over_median_db\": "
       << cfg.csi_split_vertical_line_peak_over_median_db << ",\n";
    os << "    \"csi_split_out_of_band_phase_filter_enable\": "
       << (cfg.csi_split_out_of_band_phase_filter_enable ? "true" : "false") << ",\n";
    os << "    \"csi_split_out_of_band_phase_max_std_rad\": "
       << cfg.csi_split_out_of_band_phase_max_std_rad << ",\n";
    os << "    \"csi_channel_alignment_mode\": "
       << q(cfg.csi_channel_alignment_mode) << ",\n";
    os << "    \"csi_cancellation_mode\": "
       << q(cfg.csi_cancellation_mode) << ",\n";
    os << "    \"csi_row_coherence_gate_enable\": "
       << (cfg.csi_row_coherence_gate_enable ? "true" : "false") << ",\n";
    os << "    \"csi_row_coherence_min\": "
       << cfg.csi_row_coherence_min << ",\n";
    os << "    \"csi_subtraction_gain\": "
       << cfg.csi_subtraction_gain << ",\n";
    os << "    \"csi_range_phase_correction_enable\": "
       << (cfg.csi_range_phase_correction_enable ? "true" : "false") << ",\n";
    os << "    \"paired_raw_range_phase_override_f32\": "
       << q(cfg.paired_raw_range_phase_override_f32) << ",\n";
    os << "    \"paired_csi_range_phase_override_f32\": "
       << q(cfg.paired_csi_range_phase_override_f32) << ",\n";
    os << "    \"p38_csi_override_k_rad_per_hz\": ";
    if (std::isfinite(cfg.p38_csi_override_k_rad_per_hz)) {
        os << cfg.p38_csi_override_k_rad_per_hz;
    } else {
        os << "null";
    }
    os << ",\n";
    os << "    \"p38_csi_override_b_rad\": ";
    if (std::isfinite(cfg.p38_csi_override_b_rad)) {
        os << cfg.p38_csi_override_b_rad;
    } else {
        os << "null";
    }
    os << ",\n";
    os << "    \"cfar_exclude_row_start\": " << cfg.cfar_exclude_row_start << ",\n";
    os << "    \"cfar_exclude_row_end\": " << cfg.cfar_exclude_row_end << ",\n";
    os << "    \"doppler_center_robust_enable\": "
       << (cfg.doppler_center_robust_enable ? "true" : "false") << ",\n";
    os << "    \"doppler_center_trim_top_fraction\": "
       << cfg.doppler_center_trim_top_fraction << ",\n";
    os << "    \"doppler_center_min_valid_range_bins\": "
       << cfg.doppler_center_min_valid_range_bins << ",\n";
    os << "    \"doppler_center_theory_guard_enable\": "
       << (cfg.doppler_center_theory_guard_enable ? "true" : "false") << ",\n";
    os << "    \"doppler_center_theory_max_error_hz\": "
       << cfg.doppler_center_theory_max_error_hz << ",\n";
    os << "    \"doppler_center_override_hz\": ";
    if (std::isfinite(cfg.doppler_center_override_hz)) {
        os << cfg.doppler_center_override_hz;
    } else {
        os << "null";
    }
    os << ",\n";
    os << "    \"p38_refit_enable\": " << (cfg.p38_refit_enable ? "true" : "false") << ",\n";
    os << "    \"p38_refit_row_guard_bins\": " << cfg.p38_refit_row_guard_bins << ",\n";
    os << "    \"p38_refit_range_guard_bins\": " << cfg.p38_refit_range_guard_bins << ",\n";
    os << "    \"p38_refit_top_power_frac\": " << cfg.p38_refit_top_power_frac << ",\n";
    os << "    \"p38_refit_min_sample_count\": " << cfg.p38_refit_min_sample_count << ",\n";
    os << "    \"p38_refit_min_inlier_ratio\": " << cfg.p38_refit_min_inlier_ratio << ",\n";
    os << "    \"p38_refit_max_rmse_rad\": " << cfg.p38_refit_max_rmse_rad << ",\n";
    os << "    \"p38_refit_max_delta_k\": " << cfg.p38_refit_max_delta_k << ",\n";
    os << "    \"p38_refit_max_delta_b_rad\": " << cfg.p38_refit_max_delta_b_rad << ",\n";
    os << "    \"p38_min_peak_row_energy_fraction\": "
       << cfg.p38_min_peak_row_energy_fraction << ",\n";
    os << "    \"p38_mode\": " << q("clutter_cancel_38_paper_1_p38_cuda") << ",\n";
    os << "    \"geometry_calib_mode\": " << q("linear_p38_phase_vs_doppler") << ",\n";
    os << "    \"p38_theory_sign\": "
       << (((cfg.rx_baseline_sign < 0) ? -1 : 1) *
           ((cfg.channel_phase_sign < 0) ? -1 : 1)) << ",\n";
    os << "    \"p38_theory_guided_fallback\": "
       << (cfg.p38_theory_guided_fallback ? "true" : "false") << ",\n";
    os << "    \"p38_theory_prior_relative_span\": "
       << cfg.p38_theory_prior_relative_span << ",\n";
    os << "    \"p38_theory_prior_trigger_relative_error\": "
       << cfg.p38_theory_prior_trigger_relative_error << ",\n";
    os << "    \"p38_diagnostics_dump\": "
       << (cfg.p38_diagnostics_dump ? "true" : "false") << ",\n";
    os << "    \"wavepos_parallel_max_workers\": "
       << cfg.wavepos_parallel_max_workers << ",\n";
    os << "    \"ati_velocity_sign\": " << cfg.ati_velocity_sign << ",\n";
    os << "    \"ati_phase_to_velocity_sign\": " << cfg.ati_phase_to_velocity_sign << ",\n";
    os << "    \"motion_doppler_axis_sign\": " << cfg.motion_doppler_axis_sign << ",\n";
    os << "    \"ati_phase_bias_rad\": " << cfg.ati_phase_bias_rad << ",\n";
    os << "    \"channel_calibration_enable\": "
       << (cfg.channel_calibration_enable ? "true" : "false") << ",\n";
    os << "    \"channel_calibration_apply_to_localization\": "
       << (cfg.channel_calibration_apply_to_localization ? "true" : "false") << ",\n";
    os << "    \"channel_calibration_reference_valid\": "
       << (cfg.channel_calibration_reference_valid ? "true" : "false") << ",\n";
    os << "    \"channel_calibration_reference_phase_rad\": "
       << cfg.channel_calibration_reference_phase_rad << ",\n";
    os << "    \"channel_calibration_method\": \"ctdr_static_residual\",\n";
    os << "    \"channel_calibration_range_stride\": "
       << cfg.channel_calibration_range_stride << ",\n";
    os << "    \"channel_calibration_min_sample_count\": "
       << cfg.channel_calibration_min_sample_count << ",\n";
    os << "    \"channel_calibration_min_coherence\": "
       << cfg.channel_calibration_min_coherence << ",\n";
    os << "    \"channel_calibration_outlier_threshold_rad\": "
       << cfg.channel_calibration_outlier_threshold_rad << ",\n";
    os << "    \"channel_calibration_max_rmse_rad\": "
       << cfg.channel_calibration_max_rmse_rad << ",\n";
    os << "    \"channel_calibration_max_range_shift_bins\": "
       << cfg.channel_calibration_max_range_shift_bins << ",\n";
    os << "    \"channel_calibration_min_range_correlation\": "
       << cfg.channel_calibration_min_range_correlation << ",\n";
    os << "    \"ati_vmax_mps\": " << cfg.ati_vmax_mps << ",\n";
    os << "    \"motion_comp_denom_min\": " << cfg.motion_comp_denom_min << ",\n";
    os << "    \"motion_comp_root_grid_step_mps\": " << cfg.motion_comp_root_grid_step_mps << ",\n";
    os << "    \"motion_comp_root_cost_max\": " << cfg.motion_comp_root_cost_max << ",\n";
    os << "    \"motion_comp_debug\": " << (cfg.motion_comp_debug ? "true" : "false") << ",\n";
    os << "    \"velocity_ambiguity_enable\": " << (cfg.velocity_ambiguity_enable ? "true" : "false") << ",\n";
    os << "    \"velocity_search_min_mps\": " << cfg.velocity_search_min_mps << ",\n";
    os << "    \"velocity_search_max_mps\": " << cfg.velocity_search_max_mps << ",\n";
    os << "    \"velocity_max_doppler_order\": " << cfg.velocity_max_doppler_order << ",\n";
    os << "    \"velocity_max_phase_order\": " << cfg.velocity_max_phase_order << ",\n";
    os << "    \"velocity_max_candidates\": " << cfg.velocity_max_candidates << ",\n";
    os << "    \"velocity_beam_gate_deg\": " << cfg.velocity_beam_gate_deg << ",\n";
    os << "    \"velocity_phase_sigma_rad\": " << cfg.velocity_phase_sigma_rad << ",\n";
    os << "    \"velocity_beam_sigma_deg\": " << cfg.velocity_beam_sigma_deg << ",\n";
    os << "    \"velocity_cost_margin_min\": " << cfg.velocity_cost_margin_min << ",\n";
    os << "    \"velocity_equivalent_cost_tolerance\": " << cfg.velocity_equivalent_cost_tolerance << ",\n";
    os << "    \"velocity_speed_prior_mps\": ";
    if (std::isfinite(cfg.velocity_speed_prior_mps)) os << cfg.velocity_speed_prior_mps;
    else os << "null";
    os << ",\n";
    os << "    \"velocity_speed_prior_sigma_mps\": " << cfg.velocity_speed_prior_sigma_mps << "\n";
    os << "  },\n";
    os << "  \"detection\": {\n";
    os << "    \"pf\": " << cfg.pf << ",\n";
    os << "    \"threshold\": null,\n";
    os << "    \"threshold_mode\": " << q("runtime_adaptive") << ",\n";
    os << "    \"min_points\": " << cfg.min_points << ",\n";
    os << "    \"min_len\": " << cfg.min_len << ",\n";
    os << "    \"cfar_type\": " << q(cfg.cfar_type) << ",\n";
    os << "    \"cfar_guard_cells\": " << cfg.cfar_guard_cells << ",\n";
    os << "    \"cfar_background_cells\": " << cfg.cfar_background_cells << ",\n";
    os << "    \"cluster_strong_small_enable\": "
       << (cfg.cluster_strong_small_enable ? "true" : "false") << ",\n";
    os << "    \"cluster_strong_small_min_points\": "
       << cfg.cluster_strong_small_min_points << ",\n";
    os << "    \"cluster_strong_small_peak_over_median_db\": "
       << cfg.cluster_strong_small_peak_over_median_db << ",\n";
    os << "    \"cluster_max_gap\": " << cfg.cluster_max_range_gap << ",\n";
    os << "    \"cluster_max_phase_std\": " << cfg.cluster_max_phase_std_rad << ",\n";
    os << "    \"cfar_params\": " << q("not_structured_yet") << "\n";
    os << "  },\n";
    os << "  \"tracking\": {\n";
    os << "    \"enabled\": true,\n";
    os << "    \"assignment_mode\": " << q(assignmentModeName(cfg.track_assignment_mode)) << ",\n";
    os << "    \"allow_detection_reuse\": "
       << (cfg.track_allow_detection_reuse ? "true" : "false") << ",\n";
    os << "    \"distance_mode\": " << q(distanceModeName(cfg.track_distance_mode)) << ",\n";
    os << "    \"output_state_source\": " << q(cfg.track_output_state_source) << ",\n";
    os << "    \"confirm_hits\": " << cfg.track_confirm_hits << ",\n";
    os << "    \"delete_misses\": " << cfg.track_max_missed << ",\n";
    os << "    \"gate_threshold\": " << cfg.track_gate_m << ",\n";
    os << "    \"suppress_coasted_speed_outlier\": "
       << (cfg.track_suppress_coasted_speed_outlier ? "true" : "false") << ",\n";
    os << "    \"track_manager_params\": {\n";
    os << "      \"track_idx_window\": " << cfg.track_idx_window << ",\n";
    os << "      \"track_truth_threshold\": " << cfg.track_truth_threshold << ",\n";
    os << "      \"track_confirm_window\": " << cfg.track_confirm_window << ",\n";
    os << "      \"track_tentative_max_missed\": " << cfg.track_tentative_max_missed << ",\n";
    os << "      \"track_v_max\": " << cfg.track_v_max << ",\n";
    os << "      \"track_chi2_gate\": " << cfg.track_chi2_gate << ",\n";
    os << "      \"track_default_dt\": " << cfg.track_default_dt << ",\n";
    os << "      \"track_process_noise_pos\": " << cfg.track_process_noise_pos << ",\n";
    os << "      \"track_process_noise_vel\": " << cfg.track_process_noise_vel << ",\n";
    os << "      \"track_heading_min_displacement_m\": "
       << cfg.track_heading_min_displacement_m << ",\n";
    os << "      \"track_measurement_noise_pos\": " << cfg.track_measurement_noise_pos << "\n";
    os << "    }\n";
    os << "  },\n";
    os << "  \"derived\": {\n";
    os << "    \"period_time_sec\": " << period_time_sec << ",\n";
    os << "    \"realtime_threshold_80pct_sec\": " << period_time_sec * 0.8 << ",\n";
    os << "    \"prt_per_period\": " << period_prts << ",\n";
    os << "    \"bytes_per_period\": " << bytesPerPeriod(cfg) << ",\n";
    os << "    \"range_resolution_m\": " << (cfg.Br > 0.0 ? C / (2.0 * cfg.Br) : 0.0) << ",\n";
    os << "    \"range_bin_m\": " << cfg.R_bin << ",\n";
    os << "    \"doppler_bin_hz\": " << doppler_bin_hz << ",\n";
    os << "    \"velocity_resolution_mps\": " << velocity_res << ",\n";
    os << "    \"first_blind_speed_mps\": " << first_blind << "\n";
    os << "  }\n";
    os << "}\n";
}

void writeRuntimeConfigTxt(const Config& cfg)
{
    RunState& s = state();
    std::ofstream os(s.paths.runtime_config_txt.c_str());
    if (!os) {
        std::cerr << "[PARAM_DUMP][WARN] cannot write "
                  << s.paths.runtime_config_txt << std::endl;
        return;
    }
    const std::size_t period_prts = prtCountPerPeriod(cfg);
    const double period_time_sec = cfg.PRF > 0.0
        ? static_cast<double>(period_prts) / cfg.PRF : 0.0;

    os << "[RUN]\n";
    os << "case_id = " << s.case_id << "\n";
    os << "run_id = " << s.run_id << "\n";
    os << "config_path = " << s.config_path << "\n";
    os << "output_dir = " << cfg.result_add << "\n";
    os << "git_commit = " << s.git_commit << "\n\n";
    os << "runtime_mode = " << cfg.runtime_mode << "\n";
    os << "runtime_diagnostics_enabled = "
       << (cfg.runtime_diagnostics_enabled ? "true" : "false") << "\n\n";

    os << "[FILE_LAYOUT]\n";
    os << "input_data_path = " << s.input_data_path << "\n";
    os << "pulse_len = " << cfg.pulse_len << "\n";
    os << "read_pulse_num = " << cfg.read_pulse_num << "\n";
    os << "pulse_num = " << cfg.pulse_num << "\n";
    os << "beam_count = " << beamCount(cfg) << "\n";
    os << "bytes_per_prt = " << cfg.pkg_bytes << "\n\n";

    os << "[WAVEFORM]\n";
    os << "fc_hz = " << std::setprecision(15) << cfg.fc << "\n";
    os << "Br_hz = " << cfg.Br << "\n";
    os << "fs_hz = " << cfg.fs << "\n";
    os << "Tr_sec = " << cfg.Tr << "\n";
    os << "PRF_hz = " << cfg.PRF << "\n";
    os << "lambda_m = " << cfg.lambda << "\n\n";
    os << "sample_delay_us = "
       << jsonNullableDouble(cfg.has_sample_delay_us, cfg.sample_delay_us) << "\n\n";

    os << "[PULSE_COMPRESSION]\n";
    os << "range_fft_len = " << effectiveRangeFftLen(cfg) << "\n";
    os << "pc_crop_start = " << cfg.range_crop_start << "\n";
    os << "pc_crop_len = " << cfg.range_compress_len << "\n";
    os << "range_compression_window = " << cfg.range_compression_window << "\n";
    os << "range_compression_kaiser_beta = " << cfg.range_compression_kaiser_beta << "\n";
    os << "range_compression_bandwidth_scale = " << cfg.range_compression_bandwidth_scale << "\n";
    os << "range_compression_window_normalize = "
       << (cfg.range_compression_window_normalize ? "true" : "false") << "\n";
    os << "azimuth_fft_window = " << cfg.azimuth_fft_window << "\n";
    os << "azimuth_fft_window_normalize = "
       << (cfg.azimuth_fft_window_normalize ? "true" : "false") << "\n\n";

    os << "[GEOMETRY]\n";
    os << "scan_mode = " << scanModeName(cfg.scan_mode) << "\n";
    os << "acquisition_scan_prt_count = "
       << cfg.mechanical_scan.acquisition_scan_prt_count << "\n";
    os << "cpi_pulse_count = " << cfg.mechanical_scan.cpi_pulse_count << "\n";
    os << "cpi_step_pulse = " << cfg.mechanical_scan.cpi_step_pulse << "\n";
    os << "max_cpi_angle_span_deg = "
       << cfg.mechanical_scan.max_cpi_angle_span_deg << "\n";
    os << "scan_direction_deadband_deg = "
       << cfg.mechanical_scan.scan_direction_deadband_deg << "\n";
    os << "phase_center_rotation_enable = "
       << (cfg.mechanical_scan.phase_center_rotation_enable ? "true" : "false") << "\n";
    os << "phase_center_mount_angle_deg = "
       << cfg.mechanical_scan.phase_center_mount_angle_deg << "\n";
    os << "phase_center_rotation_sign = "
       << cfg.mechanical_scan.phase_center_rotation_sign << "\n";
    os << "ctdr_servo_azimuth_deg = "
       << (std::isfinite(cfg.ctdr_servo_azimuth_deg)
               ? std::to_string(cfg.ctdr_servo_azimuth_deg) : "null") << "\n";
    os << "phase_center_geometry_source = "
       << (cfg.scan_mode == ScanMode::Mechanical &&
                   cfg.mechanical_scan.phase_center_rotation_enable
               ? "per_prt_servo_header_for_fusion_center_pose_for_ctdr"
               : "legacy_velocity_aligned_baseline") << "\n";
    os << "scan_min_deg = " << cfg.scan_min_deg << "\n";
    os << "scan_max_deg = " << cfg.scan_max_deg << "\n";
    os << "scan_step_deg = " << scanStepDeg(cfg) << "\n";
    os << "az_count = " << cfg.az_count << "\n";
    os << "squint_angle = " << cfg.squint_angle << "\n";
    os << "estimate_error_angle = " << (cfg.estimate_error_angle ? "true" : "false") << "\n\n";
    os << "beam_pointing_bias = null\n";
    os << "beam_pointing_bias_source = no_explicit_algorithm_config_field\n\n";

    os << "[CHANNEL_AND_GMTI]\n";
    os << "iq_data_type = " << cfg.iq_data_type << "\n";
    os << "new_protocol_channel_count = " << cfg.new_protocol_channel_count << "\n";
    os << "new_protocol_read_channel_1 = " << cfg.new_protocol_read_channel_1 << "\n";
    os << "new_protocol_read_channel_2 = " << cfg.new_protocol_read_channel_2 << "\n";
    os << "new_protocol_gpu_preprocess = "
       << (cfg.new_protocol_gpu_preprocess ? "true" : "false") << "\n";
    os << "enable_four_channel_fusion = "
       << (cfg.enable_four_channel_fusion ? "true" : "false") << "\n";
    os << "four_channel_phase_compensation_enable = "
       << (cfg.four_channel_phase_compensation_enable ? "true" : "false") << "\n";
    os << "four_channel_fusion_channel_3 = "
       << cfg.four_channel_fusion_channel_3 << "\n";
    os << "four_channel_fusion_channel_4 = "
       << cfg.four_channel_fusion_channel_4 << "\n";
    os << "four_channel_squint_side = " << cfg.four_channel_fusion_squint_side << "\n";
    os << "four_channel_carrier_phase_sign = "
       << cfg.four_channel_carrier_phase_sign << "\n";
    for (std::size_t ch = 0; ch < cfg.four_channel_offsets_m.size(); ++ch) {
        os << "four_channel_" << (ch + 1) << "_offset_local_m = "
           << cfg.four_channel_offsets_m[ch][0] << ","
           << cfg.four_channel_offsets_m[ch][1] << ","
           << cfg.four_channel_offsets_m[ch][2] << "\n";
    }
    os << "new_protocol_file_first_beam = " << cfg.new_protocol_file_first_beam << "\n";
    os << "new_protocol_file_scan_beam_count = "
       << cfg.new_protocol_file_scan_beam_count << "\n";
    os << "new_protocol_file_period_index = "
       << cfg.new_protocol_file_period_index << "\n";
    os << "stage2_period_id = " << cfg.stage2_period_id << "\n";
    os << "new_protocol_velocity_scale = " << cfg.new_protocol_velocity_scale << "\n";
    os << "new_protocol_velocity_source = " << cfg.new_protocol_velocity_source << "\n";
    os << "shm_scan_beam_count = " << cfg.shm_scan_beam_count << "\n";
    os << "d_chan = " << cfg.d_channel << "\n";
    os << "two_channel_phase_model = " << cfg.two_channel_phase_model << "\n";
    os << "rx_baseline_sign = " << cfg.rx_baseline_sign << "\n";
    os << "channel_phase_sign = " << cfg.channel_phase_sign << "\n";
    os << "calib_coef = " << cfg.calib_coef << "\n";
    os << "channel_phase_enabled = called_in_processOnePeriod\n";
    os << "channel_phase_source = rg_correct_CUDA applied in processOnePeriod/processOnePeriodFusionCache\n";
    os << "ctdr_position_phase_source = wrap(corrected phase_map + first applied range phi_fit at target bin)\n";
    os << "cancellation_enabled = called_in_processOnePeriod\n";
    os << "cancellation_source = clutter_cancel_38_paper_1_p38_cuda and clutter_cancel_38_paper_1_cuda called in processOnePeriod/processOnePeriodFusionCache\n\n";
    os << "motion_comp_enable = " << (cfg.motion_comp_enable ? "true" : "false") << "\n";
    os << "low_radial_velocity_filter_enabled = "
       << (cfg.low_radial_velocity_filter_enabled ? "true" : "false") << "\n";
    os << "low_radial_velocity_threshold_mps = "
       << cfg.low_radial_velocity_threshold_mps << "\n";
    os << "p38_slope_upper_bound_filter_enabled = "
       << (cfg.p38_slope_upper_bound_filter_enabled ? "true" : "false") << "\n";
    os << "p38_slope_upper_bound_rad_per_hz = "
       << cfg.p38_slope_upper_bound_rad_per_hz << "\n";
    os << "adjacent_beam_duplicate_suppression_enabled = "
       << (cfg.adjacent_beam_duplicate_suppression_enabled ? "true" : "false") << "\n";
    os << "adjacent_beam_duplicate_position_gate_m = "
       << cfg.adjacent_beam_duplicate_position_gate_m << "\n";
    os << "adjacent_beam_duplicate_velocity_gate_mps = "
       << cfg.adjacent_beam_duplicate_velocity_gate_mps << "\n";
    os << "motion_comp_apply_to_localization = "
       << (cfg.motion_comp_apply_to_localization ? "true" : "false") << "\n";
    os << "motion_comp_analytic_enable = " << (cfg.motion_comp_analytic_enable ? "true" : "false") << "\n";
    os << "motion_comp_use_row_doppler = " << (cfg.motion_comp_use_row_doppler ? "true" : "false") << "\n";
    os << "motion_comp_solver = " << cfg.motion_comp_solver << "\n";
    os << "motion_comp_iter = " << cfg.motion_comp_iter << "\n";
    os << "motion_comp_iter_tol_mps = " << cfg.motion_comp_iter_tol_mps << "\n";
    os << "p38_enhanced_enable = "
       << (cfg.p38_enhanced_enable ? "true" : "false") << "\n";
    os << "p38_robust_fit_method = "
       << (cfg.p38_enhanced_enable ? "iterative_mad" : "huber") << "\n";
    os << "csi_bypass_enable = " << (cfg.csi_bypass_enable ? "true" : "false") << "\n";
    os << "csi_metrics_enable = " << (cfg.csi_metrics_enable ? "true" : "false") << "\n";
    os << "csi_metrics_dump_power_maps = " << (cfg.csi_metrics_dump_power_maps ? "true" : "false") << "\n";
    os << "csi_metrics_dump_intermediate_maps = " << (cfg.csi_metrics_dump_intermediate_maps ? "true" : "false") << "\n";
    os << "csi_metrics_beam_id = " << cfg.csi_metrics_beam_id << "\n";
    os << "metrics_target_half_range_bins = " << cfg.metrics_target_half_range_bins << "\n";
    os << "metrics_target_half_doppler_bins = " << cfg.metrics_target_half_doppler_bins << "\n";
    os << "metrics_guard_range_bins = " << cfg.metrics_guard_range_bins << "\n";
    os << "metrics_guard_doppler_bins = " << cfg.metrics_guard_doppler_bins << "\n";
    os << "metrics_background_range_bins = " << cfg.metrics_background_range_bins << "\n";
    os << "metrics_background_doppler_bins = " << cfg.metrics_background_doppler_bins << "\n";
    os << "metrics_strong_peak_threshold_db = " << cfg.metrics_strong_peak_threshold_db << "\n";
    os << "metrics_min_valid_background_cells = " << cfg.metrics_min_valid_background_cells << "\n";
    os << "cfar_guard_cells = " << cfg.cfar_guard_cells << "\n";
    os << "cfar_background_cells = " << cfg.cfar_background_cells << "\n";
    os << "cfar_doppler_circular = "
       << (cfg.cfar_doppler_circular ? "true" : "false") << "\n";
    os << "cluster_strong_small_enable = "
       << (cfg.cluster_strong_small_enable ? "true" : "false") << "\n";
    os << "cluster_strong_small_min_points = "
       << cfg.cluster_strong_small_min_points << "\n";
    os << "cluster_strong_small_peak_over_median_db = "
       << cfg.cluster_strong_small_peak_over_median_db << "\n";
    os << "cfar_type = " << cfg.cfar_type << "\n";
    os << "dynamic_cfar_enable = "
       << (cfg.dynamic_cfar_enable ? "true" : "false") << "\n";
    os << "csi_detection_band_mode = " << cfg.csi_detection_band_mode << "\n";
    os << "csi_split_boundary_guard_rows = "
       << cfg.csi_split_boundary_guard_rows << "\n";
    os << "csi_split_merge_doppler_bins = "
       << cfg.csi_split_merge_doppler_bins << "\n";
    os << "csi_split_merge_range_bins = "
       << cfg.csi_split_merge_range_bins << "\n";
    os << "csi_split_in_band_cfar_type = "
       << cfg.csi_split_in_band_cfar_type << "\n";
    os << "csi_split_small_cluster_enable = "
       << (cfg.csi_split_small_cluster_enable ? "true" : "false") << "\n";
    os << "csi_split_small_cluster_min_points = "
       << cfg.csi_split_small_cluster_min_points << "\n";
    os << "csi_split_small_cluster_peak_over_median_db = "
       << cfg.csi_split_small_cluster_peak_over_median_db << "\n";
    os << "csi_split_small_cluster_near_doppler_rows = "
       << cfg.csi_split_small_cluster_near_doppler_rows << "\n";
    os << "csi_split_small_cluster_near_range_bins = "
       << cfg.csi_split_small_cluster_near_range_bins << "\n";
    os << "csi_split_vertical_line_filter_enable = "
       << (cfg.csi_split_vertical_line_filter_enable ? "true" : "false") << "\n";
    os << "csi_split_vertical_line_min_doppler_rows = "
       << cfg.csi_split_vertical_line_min_doppler_rows << "\n";
    os << "csi_split_vertical_line_max_range_bins = "
       << cfg.csi_split_vertical_line_max_range_bins << "\n";
    os << "csi_split_vertical_line_peak_over_median_db = "
       << cfg.csi_split_vertical_line_peak_over_median_db << "\n";
    os << "csi_split_out_of_band_phase_filter_enable = "
       << (cfg.csi_split_out_of_band_phase_filter_enable ? "true" : "false") << "\n";
    os << "csi_split_out_of_band_phase_max_std_rad = "
       << cfg.csi_split_out_of_band_phase_max_std_rad << "\n";
    os << "csi_channel_alignment_mode = " << cfg.csi_channel_alignment_mode << "\n";
    os << "csi_cancellation_mode = " << cfg.csi_cancellation_mode << "\n";
    os << "csi_row_coherence_gate_enable = "
       << (cfg.csi_row_coherence_gate_enable ? "true" : "false") << "\n";
    os << "csi_row_coherence_min = " << cfg.csi_row_coherence_min << "\n";
    os << "csi_subtraction_gain = " << cfg.csi_subtraction_gain << "\n";
    os << "csi_range_phase_correction_enable = "
       << (cfg.csi_range_phase_correction_enable ? 1 : 0) << "\n";
    os << "paired_raw_range_phase_override_f32 = "
       << cfg.paired_raw_range_phase_override_f32 << "\n";
    os << "paired_csi_range_phase_override_f32 = "
       << cfg.paired_csi_range_phase_override_f32 << "\n";
    os << "p38_csi_override_k_rad_per_hz = ";
    if (std::isfinite(cfg.p38_csi_override_k_rad_per_hz)) {
        os << cfg.p38_csi_override_k_rad_per_hz;
    } else {
        os << "none";
    }
    os << "\n";
    os << "p38_csi_override_b_rad = ";
    if (std::isfinite(cfg.p38_csi_override_b_rad)) {
        os << cfg.p38_csi_override_b_rad;
    } else {
        os << "none";
    }
    os << "\n";
    os << "cfar_exclude_row_start = " << cfg.cfar_exclude_row_start << "\n";
    os << "cfar_exclude_row_end = " << cfg.cfar_exclude_row_end << "\n";
    os << "doppler_center_robust_enable = "
       << (cfg.doppler_center_robust_enable ? "true" : "false") << "\n";
    os << "doppler_center_trim_top_fraction = "
       << cfg.doppler_center_trim_top_fraction << "\n";
    os << "doppler_center_min_valid_range_bins = "
       << cfg.doppler_center_min_valid_range_bins << "\n";
    os << "doppler_center_theory_guard_enable = "
       << (cfg.doppler_center_theory_guard_enable ? "true" : "false") << "\n";
    os << "doppler_center_theory_max_error_hz = "
       << cfg.doppler_center_theory_max_error_hz << "\n";
    os << "doppler_center_override_hz = ";
    if (std::isfinite(cfg.doppler_center_override_hz)) {
        os << cfg.doppler_center_override_hz;
    } else {
        os << "none";
    }
    os << "\n";
    os << "p38_refit_enable = " << (cfg.p38_refit_enable ? "true" : "false") << "\n";
    os << "p38_refit_row_guard_bins = " << cfg.p38_refit_row_guard_bins << "\n";
    os << "p38_refit_range_guard_bins = " << cfg.p38_refit_range_guard_bins << "\n";
    os << "p38_refit_top_power_frac = " << cfg.p38_refit_top_power_frac << "\n";
    os << "p38_refit_min_sample_count = " << cfg.p38_refit_min_sample_count << "\n";
    os << "p38_refit_min_inlier_ratio = " << cfg.p38_refit_min_inlier_ratio << "\n";
    os << "p38_refit_max_rmse_rad = " << cfg.p38_refit_max_rmse_rad << "\n";
    os << "p38_refit_max_delta_k = " << cfg.p38_refit_max_delta_k << "\n";
    os << "p38_refit_max_delta_b_rad = " << cfg.p38_refit_max_delta_b_rad << "\n";
    os << "p38_min_peak_row_energy_fraction = "
       << cfg.p38_min_peak_row_energy_fraction << "\n";
    os << "p38_mode = clutter_cancel_38_paper_1_p38_cuda\n";
    os << "geometry_calib_mode = linear_p38_phase_vs_doppler\n";
    os << "p38_theory_sign = "
       << (((cfg.rx_baseline_sign < 0) ? -1 : 1) *
           ((cfg.channel_phase_sign < 0) ? -1 : 1)) << "\n";
    os << "p38_theory_guided_fallback = "
       << (cfg.p38_theory_guided_fallback ? "true" : "false") << "\n";
    os << "p38_theory_prior_relative_span = "
       << cfg.p38_theory_prior_relative_span << "\n";
    os << "p38_theory_prior_trigger_relative_error = "
       << cfg.p38_theory_prior_trigger_relative_error << "\n";
    os << "p38_diagnostics_dump = "
       << (cfg.p38_diagnostics_dump ? "true" : "false") << "\n";
    os << "wavepos_parallel_max_workers = "
       << cfg.wavepos_parallel_max_workers << "\n";
    os << "ati_velocity_sign = " << cfg.ati_velocity_sign << "\n";
    os << "ati_phase_to_velocity_sign = " << cfg.ati_phase_to_velocity_sign << "\n";
    os << "motion_doppler_axis_sign = " << cfg.motion_doppler_axis_sign << "\n";
    os << "ati_phase_bias_rad = " << cfg.ati_phase_bias_rad << "\n";
    os << "channel_calibration_enable = "
       << (cfg.channel_calibration_enable ? "true" : "false") << "\n";
    os << "channel_calibration_apply_to_localization = "
       << (cfg.channel_calibration_apply_to_localization ? "true" : "false") << "\n";
    os << "channel_calibration_reference_valid = "
       << (cfg.channel_calibration_reference_valid ? "true" : "false") << "\n";
    os << "channel_calibration_reference_phase_rad = "
       << cfg.channel_calibration_reference_phase_rad << "\n";
    os << "channel_calibration_method = ctdr_static_residual\n";
    os << "channel_calibration_range_stride = "
       << cfg.channel_calibration_range_stride << "\n";
    os << "channel_calibration_min_sample_count = "
       << cfg.channel_calibration_min_sample_count << "\n";
    os << "channel_calibration_min_coherence = "
       << cfg.channel_calibration_min_coherence << "\n";
    os << "channel_calibration_outlier_threshold_rad = "
       << cfg.channel_calibration_outlier_threshold_rad << "\n";
    os << "channel_calibration_max_rmse_rad = "
       << cfg.channel_calibration_max_rmse_rad << "\n";
    os << "channel_calibration_max_range_shift_bins = "
       << cfg.channel_calibration_max_range_shift_bins << "\n";
    os << "channel_calibration_min_range_correlation = "
       << cfg.channel_calibration_min_range_correlation << "\n";
    os << "ati_vmax_mps = " << cfg.ati_vmax_mps << "\n";
    os << "motion_comp_denom_min = " << cfg.motion_comp_denom_min << "\n";
    os << "motion_comp_root_grid_step_mps = " << cfg.motion_comp_root_grid_step_mps << "\n";
    os << "motion_comp_root_cost_max = " << cfg.motion_comp_root_cost_max << "\n";
    os << "motion_comp_debug = " << (cfg.motion_comp_debug ? "true" : "false") << "\n";
    os << "velocity_ambiguity_enable = " << (cfg.velocity_ambiguity_enable ? "true" : "false") << "\n";
    os << "velocity_search_min_mps = " << cfg.velocity_search_min_mps << "\n";
    os << "velocity_search_max_mps = " << cfg.velocity_search_max_mps << "\n";
    os << "velocity_max_doppler_order = " << cfg.velocity_max_doppler_order << "\n";
    os << "velocity_max_phase_order = " << cfg.velocity_max_phase_order << "\n";
    os << "velocity_max_candidates = " << cfg.velocity_max_candidates << "\n";
    os << "velocity_beam_gate_deg = " << cfg.velocity_beam_gate_deg << "\n";
    os << "velocity_phase_sigma_rad = " << cfg.velocity_phase_sigma_rad << "\n";
    os << "velocity_beam_sigma_deg = " << cfg.velocity_beam_sigma_deg << "\n";
    os << "velocity_cost_margin_min = " << cfg.velocity_cost_margin_min << "\n";
    os << "velocity_equivalent_cost_tolerance = " << cfg.velocity_equivalent_cost_tolerance << "\n";
    os << "velocity_speed_prior_mps = ";
    if (std::isfinite(cfg.velocity_speed_prior_mps)) os << cfg.velocity_speed_prior_mps;
    else os << "nan";
    os << "\n";
    os << "velocity_speed_prior_sigma_mps = " << cfg.velocity_speed_prior_sigma_mps << "\n\n";

    os << "[DETECTION]\n";
    os << "pf = " << cfg.pf << "\n";
    os << "min_points = " << cfg.min_points << "\n";
    os << "min_len = " << cfg.min_len << "\n";
    os << "threshold = null\n";
    os << "threshold_mode = runtime_adaptive\n";
    os << "cfar_type = " << cfg.cfar_type << "\n";
    os << "cfar_guard_cells = " << cfg.cfar_guard_cells << "\n";
    os << "cfar_background_cells = " << cfg.cfar_background_cells << "\n";
    os << "cluster_strong_small_enable = "
       << (cfg.cluster_strong_small_enable ? "true" : "false") << "\n";
    os << "cluster_strong_small_min_points = "
       << cfg.cluster_strong_small_min_points << "\n";
    os << "cluster_strong_small_peak_over_median_db = "
       << cfg.cluster_strong_small_peak_over_median_db << "\n";
    os << "cluster_max_gap = " << cfg.cluster_max_range_gap << "\n";
    os << "cluster_max_phase_std = " << cfg.cluster_max_phase_std_rad << "\n";
    os << "cfar_params = not_structured_yet\n\n";

    os << "[TRACKING]\n";
    os << "assignment_mode = " << assignmentModeName(cfg.track_assignment_mode) << "\n";
    os << "allow_detection_reuse = "
       << (cfg.track_allow_detection_reuse ? "true" : "false") << "\n";
    os << "distance_mode = " << distanceModeName(cfg.track_distance_mode) << "\n";
    os << "output_state_source = " << cfg.track_output_state_source << "\n";
    os << "confirm_hits = " << cfg.track_confirm_hits << "\n";
    os << "delete_misses = " << cfg.track_max_missed << "\n";
    os << "track_gate_m = " << cfg.track_gate_m << "\n";
    os << "track_suppress_coasted_speed_outlier = "
       << (cfg.track_suppress_coasted_speed_outlier ? "true" : "false") << "\n\n";
    os << "track_heading_min_displacement_m = "
       << cfg.track_heading_min_displacement_m << "\n\n";

    os << "[DERIVED]\n";
    os << "period_time_sec = " << period_time_sec << "\n";
    os << "realtime_threshold_80pct_sec = " << period_time_sec * 0.8 << "\n";
    os << "prt_per_period = " << period_prts << "\n";
}

} // namespace

std::string currentIsoTime()
{
    const auto now = std::chrono::system_clock::now();
    const std::time_t t = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
    localtime_r(&t, &tm);
    const auto micros = std::chrono::duration_cast<std::chrono::microseconds>(
        now.time_since_epoch()).count() % 1000000;
    std::ostringstream os;
    os << std::put_time(&tm, "%Y-%m-%dT%H:%M:%S") << "."
       << std::setw(6) << std::setfill('0') << micros;
    return os.str();
}

void initializeRun(const Config& cfg,
                   const std::string& config_path,
                   const std::string& executable_path)
{
    RunState& s = state();
    s.initialized = true;
    s.diagnostics_enabled = cfg.runtime_diagnostics_enabled;
    s.case_id = basenameNoExt(config_path);
    s.run_id = makeRunIdFromTime();
    std::ostringstream rid;
    rid << "GMTI";
    if (cfg.result_file_id > 0) {
        rid << std::setw(2) << std::setfill('0') << cfg.result_file_id;
    } else {
        rid << "auto";
    }
    s.result_id = rid.str();
    s.start_time = currentIsoTime();
    s.end_time.clear();
    s.git_commit = runCommandOneLine("git rev-parse --short HEAD 2>/dev/null");
    s.executable = executable_path;
    s.config_path = config_path;
    s.working_directory = currentWorkingDirectory();
    s.input_data_path = cfg.INFO_Type ? cfg.GMTI_Data_new : cfg.GMTI_Data_add;
    s.output_dir = cfg.result_add;
    s.paths.runtime_config_json = pathJoin(cfg.result_add, "runtime_config_dump.json");
    s.paths.runtime_config_txt = pathJoin(cfg.result_add, "runtime_config_dump.txt");
    s.paths.timing_metrics_csv = pathJoin(cfg.result_add, "timing_metrics.csv");
    s.paths.run_manifest_json = pathJoin(cfg.result_add, "run_manifest.json");

    if (!s.diagnostics_enabled) {
        std::cout << "[RUNTIME] runtime_mode=" << cfg.runtime_mode
                  << ", diagnostics disabled for realtime run" << std::endl;
        return;
    }

    if (!mkdirP(cfg.result_add)) {
        std::cerr << "[PARAM_DUMP][WARN] cannot create output dir: "
                  << cfg.result_add << std::endl;
    }
    writeRuntimeConfigDump(cfg, config_path, executable_path);
    writeTimingCsvUnlocked();
    writeManifest(cfg, false, -1, "run started");

    std::cout << "[PARAM_DUMP] runtime_config_dump = "
              << s.paths.runtime_config_json << std::endl;
    std::cout << "[PARAM_DUMP] pulse_len=" << cfg.pulse_len
              << ", read_pulse_num=" << cfg.read_pulse_num
              << ", fc=" << cfg.fc
              << ", Br=" << cfg.Br
              << ", fs=" << cfg.fs
              << ", Tr=" << cfg.Tr
              << ", PRF=" << cfg.PRF << std::endl;
    std::cout << "[PARAM_DUMP] scan_min=" << cfg.scan_min_deg
              << ", scan_max=" << cfg.scan_max_deg
              << ", az_count=" << cfg.az_count
              << ", pc_crop_start=" << cfg.range_crop_start
              << ", pc_crop_len=" << cfg.range_compress_len << std::endl;
    std::cout << "[MANIFEST] run_manifest = "
              << s.paths.run_manifest_json << std::endl;
    std::cout << "[TIMING] timing_metrics = "
              << s.paths.timing_metrics_csv << std::endl;
}

void finishRun(const Config& cfg, bool normal_exit, int exit_code,
               const std::string& notes)
{
    RunState& s = state();
    if (!s.initialized || !s.diagnostics_enabled) return;
    s.end_time = currentIsoTime();
    writeTimingCsvUnlocked();
    writeManifest(cfg, normal_exit, exit_code, notes);
}

void writeRuntimeConfigDump(const Config& cfg,
                            const std::string&,
                            const std::string&)
{
    if (!diagnosticsEnabled()) return;
    writeRuntimeConfigJson(cfg);
    writeRuntimeConfigTxt(cfg);
}

void recordTiming(const char* scope_name,
                  std::chrono::system_clock::time_point start_time,
                  std::chrono::system_clock::time_point end_time,
                  long long elapsed_ms,
                  int period_id,
                  const std::string& extra)
{
    std::lock_guard<std::mutex> lock(stateMutex());
    RunState& s = state();
    if (!s.initialized || !s.diagnostics_enabled) return;

    auto toIso = [](std::chrono::system_clock::time_point tp) {
        const std::time_t t = std::chrono::system_clock::to_time_t(tp);
        std::tm tm{};
        localtime_r(&t, &tm);
        const auto micros = std::chrono::duration_cast<std::chrono::microseconds>(
            tp.time_since_epoch()).count() % 1000000;
        std::ostringstream os;
        os << std::put_time(&tm, "%Y-%m-%dT%H:%M:%S") << "."
           << std::setw(6) << std::setfill('0') << micros;
        return os.str();
    };

    TimingMetric m;
    m.case_id = s.case_id;
    m.run_id = s.run_id;
    m.result_id = s.result_id;
    m.period_id = period_id;
    m.scope_name = scope_name ? scope_name : "";
    m.start_time = toIso(start_time);
    m.end_time = toIso(end_time);
    m.elapsed_ms = elapsed_ms;
    m.extra = extra;
    s.timings.push_back(m);
    writeTimingCsvUnlocked();
}

void flushTimingMetrics()
{
    std::lock_guard<std::mutex> lock(stateMutex());
    if (!state().diagnostics_enabled) return;
    writeTimingCsvUnlocked();
}

bool diagnosticsEnabled()
{
    std::lock_guard<std::mutex> lock(stateMutex());
    return state().initialized && state().diagnostics_enabled;
}

const std::string& runId()
{
    return state().run_id;
}

const std::string& caseId()
{
    return state().case_id;
}

const std::string& resultId()
{
    return state().result_id;
}

RunPaths paths()
{
    return state().paths;
}

TimingScope::TimingScope(const char* name, int period_id, const std::string& extra)
    : name_(name),
      period_id_(period_id),
      extra_(extra),
      steady_start_(std::chrono::high_resolution_clock::now()),
      wall_start_(std::chrono::system_clock::now())
{
}

TimingScope::~TimingScope()
{
    const auto steady_end = std::chrono::high_resolution_clock::now();
    const auto wall_end = std::chrono::system_clock::now();
    const auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(
        steady_end - steady_start_);
    const bool diagnostics = diagnosticsEnabled();
    // Release/formal runs do not create timing CSVs or other diagnostic
    // files, but the real-time contract still requires one observable total
    // processing time for every complete file/SHM cycle.  Production flows
    // place one main_total scope around each cycle.
    if (diagnostics || (name_ && std::strcmp(name_, "main_total") == 0)) {
        std::cout << (diagnostics ? "[TIMING-SCOPE] " : "[TIMING][CYCLE] ")
                  << name_ << ": "
                  << duration.count() << " ms" << std::endl;
    }
    if (diagnostics) {
        recordTiming(name_, wall_start_, wall_end, duration.count(), period_id_, extra_);
    }
}

} // namespace runtime
} // namespace gmti
