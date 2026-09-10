#include "GMTIProcessor.hpp"
#include "geo/geoProj.hpp" // 包含 Gaussp3RV
#include "ctdr_phase_model.hpp"
#include "motion_comp.hpp"
#include "runtime_diagnostics.hpp"
#include <vector>
#include <array>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <algorithm>
#include <string>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <sys/stat.h>
#include <sys/types.h>
#include <dirent.h> // opendir, readdir, closedir
#include <cstdio>   // std::snprintf
#include <initializer_list>
#include <limits>

static inline bool starts_with(const char *s, const char *pfx)
{
    while (*pfx)
    {
        if (*s++ != *pfx++)
            return false;
    }
    return true;
}
static inline bool ends_with(const char *s, const char *sfx)
{
    const size_t ls = std::strlen(s), lr = std::strlen(sfx);
    return (ls >= lr) && (std::strcmp(s + (ls - lr), sfx) == 0);
}

static std::string gmti_file_name(int index, const char *suffix)
{
    std::ostringstream os;
    os << "GMTI" << std::setw(2) << std::setfill('0') << index << suffix;
    return os.str();
}

static bool parse_gmti_index(const char *name, const char *suffix, int &index)
{
    index = 0;
    const size_t name_len = std::strlen(name);
    const size_t suffix_len = std::strlen(suffix);
    if (name_len <= 4U + suffix_len || !starts_with(name, "GMTI") ||
        !ends_with(name, suffix)) {
        return false;
    }

    const size_t digits_end = name_len - suffix_len;
    int value = 0;
    for (size_t pos = 4U; pos < digits_end; ++pos) {
        if (name[pos] < '0' || name[pos] > '9') {
            return false;
        }
        const int digit = name[pos] - '0';
        if (value > (std::numeric_limits<int>::max() - digit) / 10) {
            return false;
        }
        value = value * 10 + digit;
    }
    if (value <= 0) {
        return false;
    }
    index = value;
    return true;
}

static inline double wrap180_deg(double angle_deg)
{
    angle_deg = std::fmod(angle_deg + 180.0, 360.0);
    if (angle_deg < 0.0)
        angle_deg += 360.0;
    return angle_deg - 180.0;
}

static std::string csv_escape_local(const std::string &s)
{
    if (s.find_first_of(",\"\n\r") == std::string::npos) {
        return s;
    }
    std::string out = "\"";
    for (char ch : s) {
        out += (ch == '"') ? "\"\"" : std::string(1, ch);
    }
    out += "\"";
    return out;
}

static std::string csv_num(double v)
{
    if (!std::isfinite(v)) {
        return "";
    }
    std::ostringstream os;
    os << std::setprecision(15) << v;
    return os.str();
}

static std::string csv_int_or_blank(int v)
{
    return v >= 0 ? std::to_string(v) : "";
}

static double wrap_pi_csv(double x)
{
    x = std::fmod(x + M_PI, 2.0 * M_PI);
    if (x < 0.0) x += 2.0 * M_PI;
    return x - M_PI;
}

static double clamp_unit_local(double v)
{
    if (!std::isfinite(v)) {
        return v;
    }
    return std::max(-1.0, std::min(1.0, v));
}

static double deg_from_rad_local(double v)
{
    return v * 180.0 / M_PI;
}

static double rad_from_deg_local(double v)
{
    return v * M_PI / 180.0;
}

static double normalize_theta_deg_local(double theta_deg)
{
    theta_deg = std::fmod(theta_deg + 180.0, 360.0);
    if (theta_deg < 0.0) {
        theta_deg += 360.0;
    }
    return theta_deg - 180.0;
}

static double theta_from_position_deg_local(const Config &cfg,
                                            double platform_v_angle_deg,
                                            double platform_e,
                                            double platform_n,
                                            double target_e,
                                            double target_n)
{
    if (!std::isfinite(platform_v_angle_deg) ||
        !std::isfinite(platform_e) ||
        !std::isfinite(platform_n) ||
        !std::isfinite(target_e) ||
        !std::isfinite(target_n)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double dE = target_e - platform_e;
    const double dN = target_n - platform_n;
    const double target_azimuth_deg = deg_from_rad_local(std::atan2(dN, dE));
    const double side_dir = (cfg.squint_side == 1) ? -90.0 : 90.0;
    return normalize_theta_deg_local(side_dir - (platform_v_angle_deg - target_azimuth_deg));
}

static double theta_from_af_geometry_deg_local(const Config &cfg,
                                               double platform_v,
                                               double af_geometry_hz,
                                               double lambda)
{
    if (!(platform_v > 0.0) || !(lambda > 0.0) || !std::isfinite(af_geometry_hz)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double sin_a = clamp_unit_local(af_geometry_hz * lambda / (2.0 * platform_v));
    if (!std::isfinite(sin_a)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    // Production Doppler geometry is f_geo=-2*V*sin(theta)/lambda.
    const double theta_deg = -deg_from_rad_local(std::asin(sin_a));
    (void)cfg;
    return theta_deg;
}

static bool project_from_theta_deg_local(const Config &cfg,
                                         double platform_e,
                                         double platform_n,
                                         double platform_h,
                                         double platform_v,
                                         double platform_v_angle_deg,
                                         double range_m,
                                         double theta_deg,
                                         double &out_e,
                                         double &out_n)
{
    if (!std::isfinite(platform_e) ||
        !std::isfinite(platform_n) ||
        !std::isfinite(platform_h) ||
        !std::isfinite(platform_v) ||
        !std::isfinite(platform_v_angle_deg) ||
        !std::isfinite(range_m) ||
        !std::isfinite(theta_deg) ||
        !(platform_v > 0.0)) {
        return false;
    }

    const double vE = platform_v * std::cos(platform_v_angle_deg * M_PI / 180.0);
    const double vN = platform_v * std::sin(platform_v_angle_deg * M_PI / 180.0);
    const gmti::ctdr::Vec2 along = gmti::ctdr::alongFromVelocity(vE, vN);
    if (!std::isfinite(along.e)) {
        return false;
    }

    const double sinA = std::sin(theta_deg * M_PI / 180.0);
    const double cross = std::sqrt(std::max(0.0, 1.0 - sinA * sinA));
    const bool use_left = (cfg.squint_side == 1);
    const double side_e = use_left ? -along.n : along.n;
    const double side_n = use_left ? along.e : -along.e;
    const double look_e = cross * side_e + sinA * along.e;
    const double look_n = cross * side_n + sinA * along.n;
    const double ground_range =
        gmti::ctdr::groundRangeFromSlant(range_m, platform_h, cfg.MT_nowz);
    if (!std::isfinite(ground_range)) {
        return false;
    }

    out_e = platform_e + ground_range * look_e;
    out_n = platform_n + ground_range * look_n;
    return std::isfinite(out_e) && std::isfinite(out_n);
}

static double horizontal_range_from_slant(double slant_range_m, double dz_m)
{
    if (!std::isfinite(slant_range_m) || !std::isfinite(dz_m)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double v = slant_range_m * slant_range_m - dz_m * dz_m;
    return (v > 0.0) ? std::sqrt(v) : 0.0;
}

static double slant_range_from_horizontal(double ground_range_m, double dz_m)
{
    if (!std::isfinite(ground_range_m) || !std::isfinite(dz_m)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    return std::sqrt(std::max(0.0, ground_range_m * ground_range_m + dz_m * dz_m));
}

static std::string dirname_local(std::string path)
{
    while (!path.empty() && (path.back() == '/' || path.back() == '\\')) {
        path.pop_back();
    }
    const size_t pos = path.find_last_of("/\\");
    if (pos == std::string::npos) return ".";
    if (pos == 0) return "/";
    return path.substr(0, pos);
}

static std::string basename_local(std::string path)
{
    while (!path.empty() && (path.back() == '/' || path.back() == '\\')) {
        path.pop_back();
    }
    const size_t pos = path.find_last_of("/\\\\");
    return (pos == std::string::npos) ? path : path.substr(pos + 1);
}

static bool file_exists_local(const std::string &path)
{
    std::ifstream in(path.c_str());
    return static_cast<bool>(in);
}

static std::vector<std::string> split_csv_simple(const std::string &line)
{
    std::vector<std::string> out;
    std::string cur;
    bool in_quote = false;
    for (size_t i = 0; i < line.size(); ++i) {
        const char ch = line[i];
        if (ch == '"') {
            if (in_quote && i + 1 < line.size() && line[i + 1] == '"') {
                cur.push_back('"');
                ++i;
            } else {
                in_quote = !in_quote;
            }
        } else if (ch == ',' && !in_quote) {
            out.push_back(cur);
            cur.clear();
        } else {
            cur.push_back(ch);
        }
    }
    out.push_back(cur);
    if (!out.empty() && !out.back().empty() && out.back().back() == '\r') {
        out.back().pop_back();
    }
    return out;
}

static int csv_col_index(const std::vector<std::string> &header, const std::string &name)
{
    for (size_t i = 0; i < header.size(); ++i) {
        if (header[i] == name) return static_cast<int>(i);
    }
    return -1;
}

static int csv_col_index_any(const std::vector<std::string> &header,
                             std::initializer_list<const char *> names)
{
    for (const char *name : names) {
        const int idx = csv_col_index(header, name);
        if (idx >= 0) return idx;
    }
    return -1;
}

static double csv_field_double(const std::vector<std::string> &row, int idx)
{
    if (idx < 0 || idx >= static_cast<int>(row.size()) || row[static_cast<size_t>(idx)].empty()) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    return std::strtod(row[static_cast<size_t>(idx)].c_str(), nullptr);
}

static int csv_field_int(const std::vector<std::string> &row, int idx, int fallback = -1)
{
    if (idx < 0 || idx >= static_cast<int>(row.size()) || row[static_cast<size_t>(idx)].empty()) {
        return fallback;
    }
    return static_cast<int>(std::strtol(row[static_cast<size_t>(idx)].c_str(), nullptr, 10));
}

struct MovingTruthCsvRow {
    bool valid = false;
    std::string target_id;
    int period_id = -1;
    int beam_id = -1;
    int range_bin = -1;
    int row_truth = -1;
    double phi_total_truth_rad = std::numeric_limits<double>::quiet_NaN();
    double phi_static_truth_rad = std::numeric_limits<double>::quiet_NaN();
    double phi_motion_truth_rad = std::numeric_limits<double>::quiet_NaN();
    double v_truth_mps = std::numeric_limits<double>::quiet_NaN();
    double af_motion_truth_hz = std::numeric_limits<double>::quiet_NaN();
    double af_geometry_truth_hz = std::numeric_limits<double>::quiet_NaN();
    double af_total_truth_hz = std::numeric_limits<double>::quiet_NaN();
    double target_e_truth = std::numeric_limits<double>::quiet_NaN();
    double target_n_truth = std::numeric_limits<double>::quiet_NaN();
};

static double row_truth_float_on_fa_axis(const MovingTruthCsvRow &truth, const Config &cfg)
{
    if (!(cfg.fd_res > 0.0) ||
        !std::isfinite(truth.af_total_truth_hz) ||
        !std::isfinite(truth.af_geometry_truth_hz) ||
        !(cfg.PRF > 0.0)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double first = truth.af_geometry_truth_hz - 0.5 * cfg.PRF;
    return (truth.af_total_truth_hz - first) / cfg.fd_res;
}

static int row_truth_raw_index(const MovingTruthCsvRow &truth, const Config &cfg)
{
    const double v = row_truth_float_on_fa_axis(truth, cfg);
    return std::isfinite(v) ? static_cast<int>(std::llround(v)) : -1;
}

static int row_truth_after_recenter_index(const MovingTruthCsvRow &truth,
                                         const GMTIOutput::DetectionCsvRecord &r,
                                         const Config &cfg)
{
    if (!(cfg.fd_res > 0.0) ||
        !std::isfinite(truth.af_total_truth_hz) ||
        !std::isfinite(r.fd_ctr_unwrapped) ||
        !(cfg.PRF > 0.0)) {
        return -1;
    }
    const double first = r.fd_ctr_unwrapped - 0.5 * cfg.PRF;
    const double row_float = (truth.af_total_truth_hz - first) / cfg.fd_res;
    return static_cast<int>(std::llround(row_float));
}

static int truth_doppler_row_count(const Config &cfg)
{
    return cfg.process_pulse_num > 0 ? cfg.process_pulse_num : cfg.pulse_num;
}

static int normalize_doppler_row(int row, int row_count)
{
    if (row_count <= 0 || row < 0) return -1;
    const int normalized = row % row_count;
    return normalized < 0 ? normalized + row_count : normalized;
}

static int circular_doppler_row_distance(int lhs, int rhs, int row_count)
{
    if (lhs < 0 || rhs < 0) return std::numeric_limits<int>::max();
    if (row_count <= 0) return std::abs(lhs - rhs);
    const int a = normalize_doppler_row(lhs, row_count);
    const int b = normalize_doppler_row(rhs, row_count);
    if (a < 0 || b < 0) return std::numeric_limits<int>::max();
    const int direct = std::abs(a - b);
    return std::min(direct, row_count - direct);
}

static int truth_row_on_detection_axis(const MovingTruthCsvRow &truth,
                                       const GMTIOutput::DetectionCsvRecord &r,
                                       const Config &cfg)
{
    const int rows = truth_doppler_row_count(cfg);
    const int recentered = row_truth_after_recenter_index(truth, r, cfg);
    if (recentered >= 0) return normalize_doppler_row(recentered, rows);
    // Keep compatibility with historical truth tables that did not carry the
    // physical Doppler fields needed for re-centering.
    return normalize_doppler_row(truth.row_truth, rows);
}

constexpr int kMovingTruthRangeToleranceBins = 2;
constexpr int kMovingTruthDopplerToleranceBins = 2;

static std::string moving_truth_path_from_cfg(const Config &cfg)
{
    const std::string result_dir = cfg.result_add.empty() ? "." : cfg.result_add;
    const std::string root = dirname_local(result_dir);
    // Stage2 writes algorithm output below <case>/algorithm_result/period_x,
    // while truth is rooted directly at <case>/truth.  Keep the old one-level
    // candidates for legacy layouts and add this current generated layout.
    const std::string scenario_root = dirname_local(root);
    // Stage2's truth_pulse table contains the CTDR/Doppler truth and the
    // reference (mid-aperture) target coordinates.  moving_target_truth is a
    // lighter per-pulse table and must only be the compatibility fallback.
    const std::vector<std::string> candidates = {
        root + "/truth/truth_pulse.csv",
        root + "/truth/moving_target_truth.csv",
        scenario_root + "/truth/truth_pulse.csv",
        scenario_root + "/truth/moving_target_truth.csv",
        result_dir + "/../truth/truth_pulse.csv",
        result_dir + "/../truth/moving_target_truth.csv"
    };
    for (const std::string &path : candidates) {
        if (file_exists_local(path)) return path;
    }
    return candidates.front();
}

static std::vector<MovingTruthCsvRow> load_moving_truth_rows(const Config &cfg)
{
    std::vector<MovingTruthCsvRow> rows;
    std::ifstream in(moving_truth_path_from_cfg(cfg).c_str());
    if (!in) return rows;

    std::string line;
    if (!std::getline(in, line)) return rows;
    const std::vector<std::string> header = split_csv_simple(line);
    const int c_period = csv_col_index_any(header, {"period_id"});
    const int c_target_id = csv_col_index_any(header, {"target_name", "target_id"});
    const int c_beam = csv_col_index_any(header, {"beam_id", "beam_id_1based"});
    const int c_range = csv_col_index_any(
        header, {"range_bin", "expected_range_bin", "expected_bin"});
    const int c_row = csv_col_index_any(header, {"row_truth"});
    const int c_phi_total = csv_col_index_any(
        header, {"phi_total_truth_rad", "phase_rad"});
    const int c_phi_static = csv_col_index_any(header, {"phi_static_truth_rad"});
    const int c_phi_motion = csv_col_index_any(header, {"phi_motion_truth_rad"});
    const int c_v = csv_col_index_any(
        header, {"v_truth_mps", "target_vr_self_mps", "vr_self_mps"});
    const int c_af_motion = csv_col_index_any(header, {"af_motion_truth_hz"});
    const int c_af_geometry = csv_col_index_any(header, {"af_geometry_truth_hz"});
    const int c_af_total = csv_col_index_any(header, {"af_total_truth_hz"});
    const int c_e = csv_col_index_any(
        header, {"target_e_truth", "ref_target_e", "e_mid", "target_e", "e"});
    const int c_n = csv_col_index_any(
        header, {"target_n_truth", "ref_target_n", "n_mid", "target_n", "n"});

    while (std::getline(in, line)) {
        if (line.empty()) continue;
        const std::vector<std::string> f = split_csv_simple(line);
        MovingTruthCsvRow r;
        r.valid = true;
        r.target_id = (c_target_id >= 0 && c_target_id < static_cast<int>(f.size()))
            ? f[static_cast<size_t>(c_target_id)] : "";
        r.period_id = csv_field_int(f, c_period);
        r.beam_id = csv_field_int(f, c_beam);
        r.range_bin = csv_field_int(f, c_range);
        r.row_truth = csv_field_int(f, c_row);
        r.phi_total_truth_rad = csv_field_double(f, c_phi_total);
        r.phi_static_truth_rad = csv_field_double(f, c_phi_static);
        r.phi_motion_truth_rad = csv_field_double(f, c_phi_motion);
        r.v_truth_mps = csv_field_double(f, c_v);
        r.af_motion_truth_hz = csv_field_double(f, c_af_motion);
        r.af_geometry_truth_hz = csv_field_double(f, c_af_geometry);
        r.af_total_truth_hz = csv_field_double(f, c_af_total);
        r.target_e_truth = csv_field_double(f, c_e);
        r.target_n_truth = csv_field_double(f, c_n);
        rows.push_back(r);
    }
    return rows;
}

static const MovingTruthCsvRow *match_moving_truth(
    const GMTIOutput::DetectionCsvRecord &r,
    const std::vector<MovingTruthCsvRow> &truths,
    const Config &cfg)
{
    const MovingTruthCsvRow *best = nullptr;
    int best_score = 1 << 30;
    for (size_t i = 0; i < truths.size(); ++i) {
        const MovingTruthCsvRow &t = truths[i];
        if (!t.valid) continue;
        if (r.beam_id >= 0 && t.beam_id >= 0 && r.beam_id != t.beam_id) continue;
        if (r.period_id >= 0 && t.period_id >= 0 && r.period_id != t.period_id) continue;
        const int dr = (r.range_bin >= 0 && t.range_bin >= 0) ? std::abs(r.range_bin - t.range_bin) : 9999;
        if (dr > kMovingTruthRangeToleranceBins) continue;
        const int expected_row = truth_row_on_detection_axis(t, r, cfg);
        const int da = (r.row >= 0 && expected_row >= 0)
            ? circular_doppler_row_distance(r.row, expected_row,
                                            truth_doppler_row_count(cfg))
            : 0;
        // Never turn a range-coincident clutter/false-alarm cell into a
        // truth-labelled target merely because its simulated raw row was in a
        // different Doppler reference frame.  The two-bin tolerance covers
        // nearest-cell quantisation and the production peak/NMS displacement.
        if (r.row >= 0 && expected_row >= 0 &&
            da > kMovingTruthDopplerToleranceBins) {
            continue;
        }
        const int score = dr * 1000 + da;
        if (score < best_score) {
            best_score = score;
            best = &t;
        }
    }
    return best;
}

static std::string result_file_from_cfg(const Config &cfg)
{
    if (cfg.result_file_id <= 0) {
        return "";
    }
    std::string dir = cfg.result_add;
    if (!dir.empty() && dir.back() != '/' && dir.back() != '\\') {
        dir.push_back('/');
    }
    return dir + gmti_file_name(cfg.result_file_id, ".bin");
}

bool GMTIProcessor::nextGMTIFileName(const std::string &dir,
                                     std::string &out_path,
                                     int &out_index) const
{
    out_path.clear();
    out_index = 0;

    // 1) 确保目录存在
    std::string d = dir;
    if (!d.empty() && d.back() != '/' && d.back() != '\\')
        d.push_back('/');
    if (!mkdir_p(d))
    {
        std::cerr << "nextGMTIFileName: 创建目录失败: " << d << "\n";
        return false;
    }

    // 2) 打开目录并扫描现有文件，匹配 "GMTIxx.bin"
    int max_idx = 0;

    DIR *dp = ::opendir(d.c_str());
    if (dp)
    {
        while (dirent *ent = ::readdir(dp))
        {
            const char *name = ent->d_name;
            // 过滤掉 "." ".."
            if (name[0] == '.')
                continue;

            // 格式：GMTI + 一个或多个十进制编号 + .bin。
            // 编号达到 100 后不再限制为两位，%02d 只负责小编号补零。
            int idx = 0;
            if (parse_gmti_index(name, ".bin", idx) && idx > max_idx) {
                max_idx = idx;
            }
        }
        ::closedir(dp);
    }
    else
    {
        // 目录不可读也不致命（可能刚创建），从 01 开始
    }

    // 3) 下一个序号
    if (max_idx == std::numeric_limits<int>::max()) {
        std::cerr << "nextGMTIFileName: 编号超过 int 可表示范围，无法继续编号。\n";
        return false;
    }
    const int next_idx = max_idx + 1;

    // 4) 组装路径
    out_path = d + gmti_file_name(next_idx, ".bin");
    out_index = next_idx;
    return true;
}

static inline void put_u8(std::vector<uint8_t> &buf, size_t pos, uint8_t v)
{
    buf[pos] = v;
}

static inline void put_u16_le(std::vector<uint8_t> &buf, size_t pos, uint16_t v)
{
    buf[pos + 0] = static_cast<uint8_t>(v & 0xFF);
    buf[pos + 1] = static_cast<uint8_t>((v >> 8) & 0xFF);
}

static inline void put_f32_le(std::vector<uint8_t> &buf, size_t pos, float v)
{
    static_assert(sizeof(float) == 4, "float must be 4 bytes");
    uint32_t u;
    std::memcpy(&u, &v, sizeof(u));
    // 写为 little-endian
    buf[pos + 0] = static_cast<uint8_t>(u & 0xFF);
    buf[pos + 1] = static_cast<uint8_t>((u >> 8) & 0xFF);
    buf[pos + 2] = static_cast<uint8_t>((u >> 16) & 0xFF);
    buf[pos + 3] = static_cast<uint8_t>((u >> 24) & 0xFF);
}

static inline void put_f64_le(std::vector<uint8_t> &buf, size_t pos, double v)
{
    static_assert(sizeof(double) == sizeof(uint64_t), "double must be 8 bytes");
    uint64_t u = 0;
    std::memcpy(&u, &v, sizeof(u));
    for (int i = 0; i < 8; ++i)
    {
        buf[pos + static_cast<size_t>(i)] = static_cast<uint8_t>((u >> (8 * i)) & 0xFF);
    }
}

static inline void put_i32_le(std::vector<uint8_t> &buf, size_t pos, int32_t v);
static inline int32_t quant_deg_to_i32(double deg);

static inline uint16_t quant_speed_to_u16(double speed)
{
    const double q = std::round(std::max(0.0, speed) / 0.01);
    if (q > 65535.0)
        return 65535;
    return static_cast<uint16_t>(q);
}

bool GMTIProcessor::writeResult(const std::vector<double> &res, const Config &cfg)
{
    // 布局：2 + N*36
    // 单记录：id(u16) + lon(i32) + lat(i32) + speed(u16) + direction(f64) + range(f64) + utc(f64)
    constexpr size_t REC_BYTES = 36;
    constexpr size_t HEADER_BYTES = 2;
    const size_t n_targets = res.size() / 8;
    const size_t FILE_BYTES = HEADER_BYTES + n_targets * REC_BYTES;

    std::vector<uint8_t> buf(FILE_BYTES, 0u);

    // [0..1] 写总数（u16, LE）
    put_u16_le(buf, 0, static_cast<uint16_t>(n_targets));

    // 逐目标写 36 字节记录（含每目标 utc）
    for (size_t i = 0; i < n_targets; ++i)
    {
        const size_t base = HEADER_BYTES + i * REC_BYTES;

        const double lat = res[i * 8 + 0];
        const double lng = res[i * 8 + 1];
        const double target_utc = res[i * 8 + 5];
        const double direction = wrap180_deg(res[i * 8 + 6]);
        const double range = res[i * 8 + 7];

        // 0..1: id (u16, LE)
        put_u16_le(buf, base + 0, static_cast<uint16_t>(std::max<size_t>(1, std::min<size_t>(65535, i + 1))));

        // 2..5: 经度（int32）
        put_i32_le(buf, base + 2, quant_deg_to_i32(lng));
        // 6..9: 纬度（int32）
        put_i32_le(buf, base + 6, quant_deg_to_i32(lat));
        // 10..11: 速度（u16），当前周期检测结果暂写 0
        put_u16_le(buf, base + 10, 0);
        // 12..19: 方向（double）
        put_f64_le(buf, base + 12, direction);
        // 20..27: 距离（double）
        put_f64_le(buf, base + 20, range);
        // 28..35: 目标 utc（double）
        put_f64_le(buf, base + 28, target_utc);
    }

    // 目标路径：优先使用当前回波文件编号，避免扫描目录自动加一导致关联窗口错位。
    std::string outpath;
    int idx = 0;
    if (cfg.result_file_id > 0) {
        std::string d = cfg.result_add;
        if (!d.empty() && d.back() != '/' && d.back() != '\\') {
            d.push_back('/');
        }
        if (!mkdir_p(d)) {
            std::cerr << "writeResult: 创建目录失败: " << d << "\n";
            return false;
        }
        outpath = d + gmti_file_name(cfg.result_file_id, ".bin");
        idx = cfg.result_file_id;
    } else if (!nextGMTIFileName(cfg.result_add, outpath, idx)) {
        return false;
    }

    std::ofstream ofs(outpath.c_str(), std::ios::binary);
    if (!ofs)
    {
        std::cerr << "writeResult: 无法打开输出文件: " << outpath << "\n";
        return false;
    }
    ofs.write(reinterpret_cast<const char *>(buf.data()),
              static_cast<std::streamsize>(buf.size()));
    if (!ofs)
    {
        std::cerr << "writeResult: 写文件失败: " << outpath << "\n";
        return false;
    }
    ofs.close();
    std::cout << "[GMTI] writeResult: 写入当前检测结果 "
              << outpath << "，目标数: " << n_targets << std::endl;
    return true;
}

bool GMTIProcessor::writeDetectionCsv(
    const std::vector<GMTIOutput::DetectionCsvRecord> &records,
    const Config &cfg,
    const std::string &source_file) const
{
    if (!mkdir_p(cfg.result_add)) {
        std::cerr << "writeDetectionCsv: 创建目录失败: " << cfg.result_add << "\n";
        return false;
    }

    std::string outpath = cfg.result_add;
    if (!outpath.empty() && outpath.back() != '/' && outpath.back() != '\\') {
        outpath.push_back('/');
    }
    outpath += "detection_results.csv";

    std::ofstream os(outpath.c_str());
    if (!os) {
        std::cerr << "writeDetectionCsv: 无法打开输出文件: " << outpath << "\n";
        return false;
    }

    std::string case_id = cfg.stage2_case_id;
    if (case_id.empty()) case_id = gmti::runtime::caseId();
    if (case_id.empty()) {
        case_id = basename_local(dirname_local(dirname_local(cfg.result_add)));
    }
    const std::string run_id = gmti::runtime::runId();
    std::string result_id = gmti::runtime::resultId();
    if (result_id.empty() && cfg.result_file_id > 0) {
        result_id = gmti_file_name(cfg.result_file_id, "");
    }
    const std::string src = source_file.empty() ? result_file_from_cfg(cfg) : source_file;
    const std::vector<MovingTruthCsvRow> moving_truth_rows = load_moving_truth_rows(cfg);
    struct CsvWriteEntry {
        const GMTIOutput::DetectionCsvRecord *rec = nullptr;
        const MovingTruthCsvRow *truth = nullptr;
        double power = std::numeric_limits<double>::quiet_NaN();
        double position_error_m = std::numeric_limits<double>::quiet_NaN();
    };
    std::vector<CsvWriteEntry> write_entries;
    write_entries.reserve(records.size());
    auto same_nms_cluster = [](const GMTIOutput::DetectionCsvRecord &a,
                               const GMTIOutput::DetectionCsvRecord &b) {
        if (a.beam_id != b.beam_id) return false;
        if (a.range_bin >= 0 && b.range_bin >= 0 && std::abs(a.range_bin - b.range_bin) > 1) return false;
        if (a.row >= 0 && b.row >= 0 && std::abs(a.row - b.row) > 1) return false;
        if (std::isfinite(a.radial_velocity_mps) && std::isfinite(b.radial_velocity_mps) &&
            std::abs(a.radial_velocity_mps - b.radial_velocity_mps) > 0.35) return false;
        return true;
    };
    // NMS is part of the production result path.  It must be decided from
    // observed detection quantities only; using truth-derived range/row
    // distance here would make the evaluator select the detection closest to
    // the simulated target and would leak the answer into target Pd/angle
    // metrics.  Truth is attached later as an offline annotation only.
    auto prefer_nms_entry = [](const CsvWriteEntry &lhs, const CsvWriteEntry &rhs) {
        const bool lhs_power_ok = std::isfinite(lhs.power) && lhs.power > 0.0;
        const bool rhs_power_ok = std::isfinite(rhs.power) && rhs.power > 0.0;
        if (lhs_power_ok != rhs_power_ok) {
            return rhs_power_ok;
        }
        if (lhs_power_ok && rhs_power_ok) {
            if (std::abs(lhs.power - rhs.power) > 1.0e-12) {
                return rhs.power > lhs.power;
            }
        }
        // Equal-power ties are resolved deterministically from the detection
        // record itself.  Do not consult CsvWriteEntry::truth or
        // position_error_m here; those fields are populated from simulation
        // labels and are unavailable in a real measurement.
        if (lhs.rec->beam_id != rhs.rec->beam_id) {
            return rhs.rec->beam_id < lhs.rec->beam_id;
        }
        if (lhs.rec->row != rhs.rec->row) {
            return rhs.rec->row < lhs.rec->row;
        }
        if (lhs.rec->range_bin != rhs.rec->range_bin) {
            return rhs.rec->range_bin < lhs.rec->range_bin;
        }
        return false;
    };
    for (const auto &r : records) {
        CsvWriteEntry cur;
        cur.rec = &r;
        cur.truth = match_moving_truth(r, moving_truth_rows, cfg);
        cur.power = std::isfinite(r.amplitude) ? (r.amplitude * r.amplitude)
                                               : std::numeric_limits<double>::quiet_NaN();
        if (cur.truth) {
            cur.position_error_m = std::hypot(r.new_e - cur.truth->target_e_truth,
                                              r.new_n - cur.truth->target_n_truth);
        }
        bool merged = false;
        for (auto &kept : write_entries) {
            if (!same_nms_cluster(*kept.rec, r)) {
                continue;
            }
            if (prefer_nms_entry(kept, cur)) {
                kept = cur;
            }
            merged = true;
            break;
        }
        if (!merged) {
            write_entries.push_back(cur);
        }
    }

    os << "case_id,seed,run_id,result_id,period_id,beam_id,det_id,target_id,range_bin,row,col,range_m,"
          "theta_cmd_deg,theta_true_deg,phase_rad,range_phase_correction_rad,ctdr_raw_phase_rad,"
          "radial_velocity_mps,v_radial_truth_mps,velocity_error_mps,"
          "lambda_m,prf_hz,velocity_ambiguity_interval_mps,configured_speed_search_min_mps,configured_speed_search_max_mps,"
          "af_alias_hz,af_unwrapped_hz,doppler_ambiguity_order,phase_wrapped_rad,phase_unwrapped_rad,phase_ambiguity_order,"
          "velocity_solver,velocity_candidate_count,velocity_best_cost,velocity_second_cost,velocity_cost_margin,velocity_confidence,ambiguity_status,"
          "power,cfar_margin_db,"
          "p38_k,p38_b,fa_shift_hz,"
          "p38_raw_k,p38_raw_b,p38_raw_rmse,p38_raw_inlier_ratio,p38_refit_inlier_ratio,"
          "p38_pre_k,p38_pre_b,p38_pre_af_wrapped_hz,p38_pre_af_hz,p38_pre_theta_deg,p38_pre_e,p38_pre_n,p38_pre_position_error_m,"
          "p38_refit_k,p38_refit_b,p38_refit_af_wrapped_hz,p38_refit_af_hz,p38_refit_theta_deg,p38_refit_e,p38_refit_n,p38_refit_position_error_m,"
          "p38_used_source,af_total_hz,"
          "af_old_total_hz,theta_old_total_deg,old_total_e,old_total_n,old_total_position_error_m,"
          "af_old_hz,theta_old_deg,old_e,old_n,old_position_error_m,"
          "af_iterative_hz,theta_iterative_deg,iterative_e,iterative_n,iterative_position_error_m,"
          "af_analytic_hz,theta_analytic_deg,analytic_e,analytic_n,analytic_position_error_m,"
          "af_root1d_hz,theta_root1d_deg,root1d_e,root1d_n,root1d_position_error_m,"
          "af_used_hz,theta_used_deg,new_e,new_n,platform_e,platform_n,platform_h,position_error_m,"
          "target_e_truth,target_n_truth,truth_af_geometry_hz,truth_af_total_hz,"
          "truth_af_motion_hz,truth_ctdr_phase_rad,source_file,"
          "channel_mode,four_channel_phase_compensation_enable,loc_used_mode,"
          "loc_jacobian_daf_dphase_hz_per_rad,loc_jacobian_daf_doppler_hz_per_hz,"
          "loc_jacobian_crossrange_m_per_phase_rad,"
          "truth_angle_deg,estimated_angle_deg,angle_error_deg,"
          "azimuth_error_m,range_error_m,position_error_check_m\n";

    for (size_t i = 0; i < write_entries.size(); ++i) {
        const auto &entry = write_entries[i];
        const auto &r = *entry.rec;
        const MovingTruthCsvRow *truth = entry.truth;
        const double truth_v_radial = truth
            ? truth->v_truth_mps
            : std::numeric_limits<double>::quiet_NaN();
        const double velocity_error_mps =
            std::isfinite(r.radial_velocity_mps) && std::isfinite(truth_v_radial)
                ? std::abs(r.radial_velocity_mps - truth_v_radial)
                : std::numeric_limits<double>::quiet_NaN();
        const double lambda = (cfg.lambda > 0.0) ? cfg.lambda
                                  : ((cfg.fc > 0.0) ? (C / (cfg.fc * 1.0e9))
                                                    : std::numeric_limits<double>::quiet_NaN());
        const double velocity_ambiguity_interval_mps =
            std::isfinite(lambda) && cfg.PRF > 0.0
                ? lambda * cfg.PRF / 2.0
                : std::numeric_limits<double>::quiet_NaN();
        const double fa_shift_hz =
            (std::isfinite(r.fd_ctr_wrapped) && std::isfinite(r.fd_ctr_unwrapped))
                ? (r.fd_ctr_unwrapped - r.fd_ctr_wrapped)
                : 0.0;
        // The release CSV is also the theory/measurement audit artifact.  The
        // CTDR root search is nonlinear, so obtain its local sensitivity from
        // the production solver rather than substituting a linear P38 model.
        double loc_jacobian_daf_dphase = std::numeric_limits<double>::quiet_NaN();
        double loc_jacobian_daf_ddoppler = std::numeric_limits<double>::quiet_NaN();
        double loc_jacobian_crossrange = std::numeric_limits<double>::quiet_NaN();
        GMTIOutput::Plane jac_plane;
        jac_plane.E = r.platform_e;
        jac_plane.N = r.platform_n;
        jac_plane.H = r.platform_h;
        jac_plane.V = r.platform_v;
        jac_plane.V_angle = r.platform_v_angle_deg;
        const bool jacobian_inputs_ok =
            std::isfinite(r.phase_rad) && std::isfinite(r.af_total) &&
            std::isfinite(r.p38_k) && std::isfinite(r.p38_b) &&
            std::isfinite(lambda) && std::isfinite(r.range_m) &&
            std::isfinite(jac_plane.V) && jac_plane.V > 1.0e-6;
        if (jacobian_inputs_ok) {
            constexpr double phase_step_rad = 1.0e-5;
            constexpr double doppler_step_hz = 1.0e-3;
            const MotionCompResult phase_lo = solveMotionCompensation(
                cfg, jac_plane, r.phase_rad - phase_step_rad, r.af_total,
                r.p38_k, r.p38_b, lambda, r.range_m, r.theta_true_deg,
                r.range_phase_correction_rad);
            const MotionCompResult phase_hi = solveMotionCompensation(
                cfg, jac_plane, r.phase_rad + phase_step_rad, r.af_total,
                r.p38_k, r.p38_b, lambda, r.range_m, r.theta_true_deg,
                r.range_phase_correction_rad);
            const MotionCompResult doppler_lo = solveMotionCompensation(
                cfg, jac_plane, r.phase_rad, r.af_total - doppler_step_hz,
                r.p38_k, r.p38_b, lambda, r.range_m, r.theta_true_deg,
                r.range_phase_correction_rad);
            const MotionCompResult doppler_hi = solveMotionCompensation(
                cfg, jac_plane, r.phase_rad, r.af_total + doppler_step_hz,
                r.p38_k, r.p38_b, lambda, r.range_m, r.theta_true_deg,
                r.range_phase_correction_rad);
            if (phase_lo.ok && phase_hi.ok) {
                loc_jacobian_daf_dphase =
                    (phase_hi.af_geometry - phase_lo.af_geometry) /
                    (2.0 * phase_step_rad);
            }
            if (doppler_lo.ok && doppler_hi.ok) {
                loc_jacobian_daf_ddoppler =
                    (doppler_hi.af_geometry - doppler_lo.af_geometry) /
                    (2.0 * doppler_step_hz);
            }
            const double sin_a = r.sinA_used;
            const double ground_range = gmti::ctdr::groundRangeFromSlant(
                r.range_m, jac_plane.H, cfg.MT_nowz);
            if (std::isfinite(loc_jacobian_daf_dphase) &&
                std::isfinite(sin_a) && std::abs(sin_a) < 1.0 &&
                std::isfinite(ground_range)) {
                loc_jacobian_crossrange = std::abs(ground_range *
                    lambda / (2.0 * jac_plane.V *
                    std::sqrt(1.0 - sin_a * sin_a)) *
                    loc_jacobian_daf_dphase);
            }
        }
        struct MethodSummary {
            double af_hz = std::numeric_limits<double>::quiet_NaN();
            double theta_deg = std::numeric_limits<double>::quiet_NaN();
            double e = std::numeric_limits<double>::quiet_NaN();
            double n = std::numeric_limits<double>::quiet_NaN();
            double position_error_m = std::numeric_limits<double>::quiet_NaN();
        };
        auto summarize_method = [&](double af_hz,
                                    double theta_override_deg,
                                    double e_override,
                                    double n_override) {
            MethodSummary m;
            m.af_hz = af_hz;
            m.theta_deg = std::isfinite(theta_override_deg)
                ? theta_override_deg
                : theta_from_af_geometry_deg_local(cfg, r.platform_v, af_hz, lambda);
            if (std::isfinite(e_override) && std::isfinite(n_override)) {
                m.e = e_override;
                m.n = n_override;
            } else if (project_from_theta_deg_local(cfg,
                                                    r.platform_e,
                                                    r.platform_n,
                                                    r.platform_h,
                                                    r.platform_v,
                                                    r.platform_v_angle_deg,
                                                    r.range_m,
                                                    m.theta_deg,
                                                    m.e,
                                                    m.n)) {
                // projected successfully
            }
            if (truth && std::isfinite(m.e) && std::isfinite(m.n)) {
                m.position_error_m = std::hypot(m.e - truth->target_e_truth,
                                                 m.n - truth->target_n_truth);
            }
            return m;
        };
        auto p38_wrapped_af = [&](double k, double b) {
            return (std::isfinite(k) && std::abs(k) >= 1.0e-12)
                ? ((r.phase_rad - b) / k)
                : std::numeric_limits<double>::quiet_NaN();
        };
        auto summarize_p38_fit = [&](double k, double b) {
            MethodSummary m;
            const double wrapped_af = p38_wrapped_af(k, b);
            m.af_hz = std::isfinite(wrapped_af) ? (wrapped_af + fa_shift_hz)
                                                : std::numeric_limits<double>::quiet_NaN();
            m.theta_deg = theta_from_af_geometry_deg_local(cfg, r.platform_v, m.af_hz, lambda);
            if (project_from_theta_deg_local(cfg,
                                             r.platform_e,
                                             r.platform_n,
                                             r.platform_h,
                                             r.platform_v,
                                             r.platform_v_angle_deg,
                                             r.range_m,
                                             m.theta_deg,
                                             m.e,
                                             m.n) && truth) {
                m.position_error_m = std::hypot(m.e - truth->target_e_truth,
                                                 m.n - truth->target_n_truth);
            }
            return m;
        };
        const double p38_pre_af_wrapped_hz = p38_wrapped_af(r.p38_pre_k, r.p38_pre_b);
        const double p38_refit_af_wrapped_hz = p38_wrapped_af(r.p38_refit_k, r.p38_refit_b);
        const MethodSummary p38_pre_m = summarize_p38_fit(r.p38_pre_k, r.p38_pre_b);
        const MethodSummary p38_refit_m = summarize_p38_fit(r.p38_refit_k, r.p38_refit_b);
        const MethodSummary old_total_m = summarize_method(r.af_total,
                                                            std::numeric_limits<double>::quiet_NaN(),
                                                            std::numeric_limits<double>::quiet_NaN(),
                                                            std::numeric_limits<double>::quiet_NaN());
        const MethodSummary old_m = summarize_method(std::isfinite(r.af_phase) ? (r.af_phase + fa_shift_hz) : r.af_phase,
                                                     std::numeric_limits<double>::quiet_NaN(),
                                                     r.old_e,
                                                     r.old_n);
        const MethodSummary iter_m = summarize_method(r.af_geometry_iterative_hz,
                                                      std::numeric_limits<double>::quiet_NaN(),
                                                      std::numeric_limits<double>::quiet_NaN(),
                                                      std::numeric_limits<double>::quiet_NaN());
        const MethodSummary ana_m = summarize_method(r.af_geometry_analytic_hz,
                                                     std::numeric_limits<double>::quiet_NaN(),
                                                     std::numeric_limits<double>::quiet_NaN(),
                                                     std::numeric_limits<double>::quiet_NaN());
        const MethodSummary root_m = summarize_method(r.af_geometry_root1d_hz,
                                                      std::numeric_limits<double>::quiet_NaN(),
                                                      std::numeric_limits<double>::quiet_NaN(),
                                                      std::numeric_limits<double>::quiet_NaN());
        const MethodSummary used_m = summarize_method(r.af_geometry, r.theta_used_for_position_deg, r.new_e, r.new_n);
        double truth_angle_deg = std::numeric_limits<double>::quiet_NaN();
        double estimated_angle_deg = std::numeric_limits<double>::quiet_NaN();
        double angle_error_deg = std::numeric_limits<double>::quiet_NaN();
        double azimuth_error_m = std::numeric_limits<double>::quiet_NaN();
        double range_error_m = std::numeric_limits<double>::quiet_NaN();
        double position_error_m = std::numeric_limits<double>::quiet_NaN();
        if (truth && std::isfinite(r.platform_e) && std::isfinite(r.platform_n) &&
            std::isfinite(used_m.e) && std::isfinite(used_m.n)) {
            const double truth_de = truth->target_e_truth - r.platform_e;
            const double truth_dn = truth->target_n_truth - r.platform_n;
            const double truth_range = std::hypot(truth_de, truth_dn);
            const double estimate_de = used_m.e - r.platform_e;
            const double estimate_dn = used_m.n - r.platform_n;
            truth_angle_deg = deg_from_rad_local(std::atan2(truth_dn, truth_de));
            estimated_angle_deg = deg_from_rad_local(std::atan2(estimate_dn, estimate_de));
            angle_error_deg = normalize_theta_deg_local(
                estimated_angle_deg - truth_angle_deg);
            const double err_e = used_m.e - truth->target_e_truth;
            const double err_n = used_m.n - truth->target_n_truth;
            position_error_m = std::hypot(err_e, err_n);
            if (truth_range > 1.0e-9) {
                const double ur_e = truth_de / truth_range;
                const double ur_n = truth_dn / truth_range;
                // EN left-normal of the truth line of sight.  This is the
                // signed cross-range/azimuth coordinate used by the study.
                const double ua_e = -ur_n;
                const double ua_n = ur_e;
                range_error_m = err_e * ur_e + err_n * ur_n;
                azimuth_error_m = err_e * ua_e + err_n * ua_n;
            }
        }
        os << csv_escape_local(case_id) << ","
           << cfg.stage2_random_seed << ","
           << csv_escape_local(run_id) << ","
           << csv_escape_local(result_id) << ","
           << csv_int_or_blank(r.period_id) << ","
           << csv_int_or_blank(r.beam_id) << ","
           << i << ","
           << csv_escape_local(truth ? truth->target_id : "") << ","
           << csv_int_or_blank(r.range_bin) << ","
           << csv_int_or_blank(r.row) << ","
           << csv_int_or_blank(r.col) << ","
           << csv_num(r.range_m) << ","
           << csv_num(r.theta_cmd_deg) << ","
           << csv_num(r.theta_true_deg) << ","
           << csv_num(r.phase_rad) << ","
           << csv_num(r.range_phase_correction_rad) << ","
           << csv_num(r.ctdr_raw_phase_rad) << ","
           << csv_num(r.radial_velocity_mps) << ","
           << csv_num(truth_v_radial) << ","
           << csv_num(velocity_error_mps) << ","
           << csv_num(lambda) << ","
           << csv_num(cfg.PRF) << ","
           << csv_num(velocity_ambiguity_interval_mps) << ","
           << csv_num(cfg.velocity_search_min_mps) << ","
           << csv_num(cfg.velocity_search_max_mps) << ","
           << csv_num(r.af_alias_hz) << ","
           << csv_num(r.af_unwrapped_hz) << ","
           << std::to_string(r.doppler_ambiguity_order) << ","
           << csv_num(r.phase_wrapped_rad) << ","
           << csv_num(r.phase_unwrapped_rad) << ","
           << std::to_string(r.phase_ambiguity_order) << ","
           << csv_escape_local(r.velocity_solver) << ","
           << r.velocity_candidate_count << ","
           << csv_num(r.velocity_best_cost) << ","
           << csv_num(r.velocity_second_cost) << ","
           << csv_num(r.velocity_cost_margin) << ","
           << csv_escape_local(r.velocity_confidence) << ","
           << csv_escape_local(r.ambiguity_status) << ","
           << csv_num(entry.power) << ","
           << csv_num(r.cfar_margin_db) << ","
           << csv_num(r.p38_k) << ","
           << csv_num(r.p38_b) << ","
           << csv_num(fa_shift_hz) << ","
           << csv_num(r.p38_raw_k) << ","
           << csv_num(r.p38_raw_b) << ","
           << csv_num(r.p38_raw_rmse) << ","
           << csv_num(r.p38_raw_inlier_ratio) << ","
           << csv_num(r.p38_refit_inlier_ratio) << ","
           << csv_num(r.p38_pre_k) << ","
           << csv_num(r.p38_pre_b) << ","
           << csv_num(p38_pre_af_wrapped_hz) << ","
           << csv_num(p38_pre_m.af_hz) << ","
           << csv_num(p38_pre_m.theta_deg) << ","
           << csv_num(p38_pre_m.e) << ","
           << csv_num(p38_pre_m.n) << ","
           << csv_num(p38_pre_m.position_error_m) << ","
           << csv_num(r.p38_refit_k) << ","
           << csv_num(r.p38_refit_b) << ","
           << csv_num(p38_refit_af_wrapped_hz) << ","
           << csv_num(p38_refit_m.af_hz) << ","
           << csv_num(p38_refit_m.theta_deg) << ","
           << csv_num(p38_refit_m.e) << ","
           << csv_num(p38_refit_m.n) << ","
           << csv_num(p38_refit_m.position_error_m) << ","
           << csv_escape_local(r.p38_used_source) << ","
           << csv_num(r.af_total) << ","
           << csv_num(old_total_m.af_hz) << ","
           << csv_num(old_total_m.theta_deg) << ","
           << csv_num(old_total_m.e) << ","
           << csv_num(old_total_m.n) << ","
           << csv_num(old_total_m.position_error_m) << ","
           << csv_num(old_m.af_hz) << ","
           << csv_num(old_m.theta_deg) << ","
           << csv_num(old_m.e) << ","
           << csv_num(old_m.n) << ","
           << csv_num(old_m.position_error_m) << ","
           << csv_num(iter_m.af_hz) << ","
           << csv_num(iter_m.theta_deg) << ","
           << csv_num(iter_m.e) << ","
           << csv_num(iter_m.n) << ","
           << csv_num(iter_m.position_error_m) << ","
           << csv_num(ana_m.af_hz) << ","
           << csv_num(ana_m.theta_deg) << ","
           << csv_num(ana_m.e) << ","
           << csv_num(ana_m.n) << ","
           << csv_num(ana_m.position_error_m) << ","
           << csv_num(root_m.af_hz) << ","
           << csv_num(root_m.theta_deg) << ","
           << csv_num(root_m.e) << ","
           << csv_num(root_m.n) << ","
           << csv_num(root_m.position_error_m) << ","
           << csv_num(used_m.af_hz) << ","
           << csv_num(r.theta_used_for_position_deg) << ","
           << csv_num(used_m.e) << ","
           << csv_num(used_m.n) << ","
           << csv_num(r.platform_e) << ","
           << csv_num(r.platform_n) << ","
           << csv_num(r.platform_h) << ","
           << csv_num(used_m.position_error_m) << ","
           << csv_num(truth ? truth->target_e_truth : std::numeric_limits<double>::quiet_NaN()) << ","
           << csv_num(truth ? truth->target_n_truth : std::numeric_limits<double>::quiet_NaN()) << ","
           << csv_num(truth ? truth->af_geometry_truth_hz : std::numeric_limits<double>::quiet_NaN()) << ","
           << csv_num(truth ? truth->af_total_truth_hz : std::numeric_limits<double>::quiet_NaN()) << ","
           << csv_num(truth ? truth->af_motion_truth_hz : std::numeric_limits<double>::quiet_NaN()) << ","
           << csv_num(truth ? truth->phi_total_truth_rad : std::numeric_limits<double>::quiet_NaN()) << ","
           << csv_escape_local(src) << ","
           << (cfg.enable_four_channel_fusion ? "4ch" : "2ch") << ","
           << (cfg.four_channel_phase_compensation_enable ? "1" : "0") << ","
           << csv_escape_local(r.loc_used_mode) << ","
           << csv_num(loc_jacobian_daf_dphase) << ","
           << csv_num(loc_jacobian_daf_ddoppler) << ","
           << csv_num(loc_jacobian_crossrange) << ","
           << csv_num(truth_angle_deg) << ","
           << csv_num(estimated_angle_deg) << ","
           << csv_num(angle_error_deg) << ","
           << csv_num(azimuth_error_m) << ","
           << csv_num(range_error_m) << ","
           << csv_num(position_error_m) << "\n";
        if (!truth) {
            // no-op: fields already emitted as blanks
        }
    }

    os.flush();
    if (!os) {
        std::cerr << "writeDetectionCsv: 写文件失败: " << outpath << "\n";
        return false;
    }
    os.close();

    // This dedicated alias is intentionally a sidecar: it exposes the
    // localization audit table under a stable topic-specific name without
    // changing the existing detection CSV path consumed by old tools.
    std::string localization_debug_path = cfg.result_add;
    if (!localization_debug_path.empty() && localization_debug_path.back() != '/' &&
        localization_debug_path.back() != '\\') {
        localization_debug_path.push_back('/');
    }
    localization_debug_path += "azimuth_localization_debug.csv";
    {
        std::ifstream src_debug(outpath.c_str(), std::ios::binary);
        std::ofstream dst_debug(localization_debug_path.c_str(),
                                std::ios::binary | std::ios::trunc);
        if (!src_debug || !dst_debug) {
            std::cerr << "writeDetectionCsv: 无法创建方位定位诊断文件: "
                      << localization_debug_path << "\n";
            return false;
        }
        dst_debug << src_debug.rdbuf();
        dst_debug.flush();
        if (src_debug.bad() || !dst_debug) {
            std::cerr << "writeDetectionCsv: 写方位定位诊断文件失败: "
                      << localization_debug_path << "\n";
            return false;
        }
    }

    // ``detection_results.csv`` is the compatibility "latest cycle" view.
    // A PIPE run processes several complete scans into the same result
    // directory, so keeping only that file silently loses every earlier
    // cycle.  Preserve an immutable result-id snapshot as well; existing
    // consumers continue to read the compatibility path unchanged.
    std::string snapshot_path;
    if (cfg.result_file_id > 0) {
        const std::string snapshot_name =
            "detection_results_" + gmti_file_name(cfg.result_file_id, ".csv");
        snapshot_path = cfg.result_add;
        if (!snapshot_path.empty() && snapshot_path.back() != '/' &&
            snapshot_path.back() != '\\') {
            snapshot_path.push_back('/');
        }
        snapshot_path += snapshot_name;

        std::ifstream src(outpath.c_str(), std::ios::binary);
        std::ofstream dst(snapshot_path.c_str(),
                          std::ios::binary | std::ios::trunc);
        if (!src || !dst) {
            std::cerr << "writeDetectionCsv: 无法创建周期快照: "
                      << snapshot_path << "\n";
            return false;
        }
        dst << src.rdbuf();
        dst.flush();
        if (src.bad() || !dst) {
            std::cerr << "writeDetectionCsv: 写周期快照失败: "
                      << snapshot_path << "\n";
            return false;
        }
    }
    std::cout << "[GMTI] detection_results_csv = " << outpath
              << "，目标数: " << records.size();
    if (!snapshot_path.empty()) {
        std::cout << "，周期快照: " << snapshot_path;
    }
    std::cout << std::endl;
    return true;
}

// ---------- 小工具：递归创建目录 ----------
bool GMTIProcessor::mkdir_p(const std::string &dir)
{
    if (dir.empty())
        return true;
    std::string cur;
    size_t i = 0;
    if (dir[0] == '/')
    {
        cur = "/";
        i = 1;
    }
    for (; i < dir.size(); ++i)
    {
        cur.push_back(dir[i]);
        if (dir[i] == '/' || i == dir.size() - 1)
        {
            if (cur == "/" || cur == "./" || cur == ".")
                continue;
            if (::mkdir(cur.c_str(), 0755) != 0)
            {
                if (errno == EEXIST)
                    continue;
                if (errno == ENOENT)
                    continue; // 父目录尚未就绪，继续循环
                std::cerr << "mkdir_p failed: " << cur << " errno=" << errno << "\n";
                return false;
            }
        }
    }
    return true;
}

// ---------- 占位：xy(m) -> (lat,lng) 度 ----------

// ---------- 写入帮助：LE 编码 ----------
static inline void put_i32_le(std::vector<uint8_t> &buf, size_t pos, int32_t v)
{
    uint32_t u = static_cast<uint32_t>(v);
    buf[pos + 0] = static_cast<uint8_t>(u & 0xFF);
    buf[pos + 1] = static_cast<uint8_t>((u >> 8) & 0xFF);
    buf[pos + 2] = static_cast<uint8_t>((u >> 16) & 0xFF);
    buf[pos + 3] = static_cast<uint8_t>((u >> 24) & 0xFF);
}

// 量化：度 -> int32（LSB=8.38191e-8 度），四舍五入并饱和
static inline int32_t quant_deg_to_i32(double deg)
{
    const double LSB = 8.38191e-8; // deg / LSB
    double q = deg / LSB;
    // 四舍五入
    if (q >= 0.0)
        q = std::floor(q + 0.5);
    else
        q = std::ceil(q - 0.5);
    // 饱和到 int32
    if (q > 2147483647.0)
        q = 2147483647.0;
    if (q < -2147483648.0)
        q = -2147483648.0;
    return static_cast<int32_t>(q);
}

// ---------- 核心：写入所有 tracks 的所有 pos ----------
bool GMTIProcessor::writeTracksBinary(const std::vector<Track> &tracks,
                                      double utcMid,
                                      const GMTIOutput::Plane &plane,
                                      const Config &cfg)
{
    (void)utcMid;
    (void)plane;

    // 逐目标写 28 字节记录，文件尾追加 8 字节 utc
    const size_t REC_BYTES = 28;
    const size_t HEADER_BYTES = 2;
    std::vector<uint8_t> buf;

    // 1) 将所有轨迹的滤波点 kf 展开为点序列（按 track.id 升序；每条内按时间顺序）
    struct Rec
    {
        uint16_t id;
        double x;
        double y;
        double speed;
        double direction;
        double range;
    };
    std::vector<Rec> points;
    points.reserve(64);

    // 拷贝并排序轨迹（确保 id 升序；id<=0 的放后面）
    std::vector<const Track *> order;
    order.reserve(tracks.size());
    for (size_t i = 0; i < tracks.size(); ++i)
        order.push_back(&tracks[i]);
    std::stable_sort(order.begin(), order.end(),
                     [](const Track *a, const Track *b)
                     {
                         const int ia = (a->id > 0) ? a->id : 0x7fffffff;
                         const int ib = (b->id > 0) ? b->id : 0x7fffffff;
                         if (ia != ib)
                             return ia < ib;
                         return a < b;
                     });

    for (size_t ti = 0; ti < order.size(); ++ti)
    {
        const Track &tr = *order[ti];
        const uint16_t tid = (tr.id > 0 && tr.id <= 65535) ? static_cast<uint16_t>(tr.id)
                                                           : static_cast<uint16_t>(std::min<size_t>(65535, ti + 1));
        // 只写入每条轨迹的第一个滤波点
        if (tr.kf.empty())
            continue;
        double speed = 0.0;
        if (tr.pos.size() >= 2 && tr.time.size() >= 2)
        {
            const size_t n = tr.pos.size();
            const double dx = tr.pos[n - 1][0] - tr.pos[n - 2][0];
            const double dy = tr.pos[n - 1][1] - tr.pos[n - 2][1];
            const double dt = tr.time[n - 1] - tr.time[n - 2];
            speed = std::sqrt(dx * dx + dy * dy) / (dt > 1e-6 ? dt : 1.0);
        }
        Rec r;
        r.id = tid;
        r.x = tr.kf.back()[0];
        r.y = tr.kf.back()[1];
        r.speed = speed;
        r.direction = tr.direction;
        r.range = tr.range;
        points.push_back(r);
    }

    std::cout << "[GMTI] writeTracksBinary: 共 " << tracks.size() << " 个目标，"
              << points.size() << " 个点。\n";

    // 2 字节目标数 + N*28 字节记录 + 8 字节 utc
    buf.reserve(HEADER_BYTES + points.size() * REC_BYTES + 8);
    buf.resize(HEADER_BYTES, 0u);
    put_u16_le(buf, 0, static_cast<uint16_t>(std::min<size_t>(65535, points.size())));

    // 逐点写 28 字节记录
    for (size_t i = 0; i < points.size(); ++i)
    {
        const size_t base = HEADER_BYTES + i * REC_BYTES;
        buf.resize(base + REC_BYTES, 0u);

        // 2 byte id
        put_u16_le(buf, base + 0, points[i].id);

        // 坐标转换：x,y(m) → lat,lng(deg)
        double lat = 0.0, lng = 0.0;
        Gaussp3RV(points[i].x, points[i].y, cfg.L0, lat, lng);

        // 2..5 经度，6..9 纬度
        put_i32_le(buf, base + 2, quant_deg_to_i32(lng));
        put_i32_le(buf, base + 6, quant_deg_to_i32(lat));
        // 10..11 速度：轨迹末端速度估计
        put_u16_le(buf, base + 10, quant_speed_to_u16(points[i].speed));
        // 12..19 方向，20..27 距离
        put_f64_le(buf, base + 12, wrap180_deg(points[i].direction));
        put_f64_le(buf, base + 20, points[i].range);
    }

    put_f64_le(buf, HEADER_BYTES + points.size() * REC_BYTES, utcMid);

    // 输出到 result_add 目录
    std::string outpath;
    int idx = 0;
    if (!nextGMTIFileName(cfg.result_add, outpath, idx))
    {
        return false; // 或者回退到固定文件名
    }

    std::ofstream ofs(outpath.c_str(), std::ios::binary);
    if (!ofs)
    {
        std::cerr << "writeTracksBinary: 无法打开输出文件: " << outpath << "\n";
        return false;
    }
    ofs.write(reinterpret_cast<const char *>(buf.data()),
              static_cast<std::streamsize>(buf.size()));
    if (!ofs)
    {
        std::cerr << "writeTracksBinary: 写文件失败: " << outpath << "\n";
        return false;
    }
    std::cout << "writeTracksBinary: 写文件成功: " << outpath << "\n";
    return true;
}
