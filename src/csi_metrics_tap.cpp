#include "csi_metrics_tap.hpp"

#include "config_structs.hpp"
#include "runtime_diagnostics.hpp"

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <mutex>
#include <sstream>
#include <sys/stat.h>

namespace gmti {
namespace metrics {
namespace {

struct PowerStats {
    std::size_t count = 0;
    double mean = std::numeric_limits<double>::quiet_NaN();
    double median = std::numeric_limits<double>::quiet_NaN();
    double rms = std::numeric_limits<double>::quiet_NaN();
    double p95 = std::numeric_limits<double>::quiet_NaN();
    double maximum = std::numeric_limits<double>::quiet_NaN();
};

struct PhaseModelStats {
    std::size_t count = 0;
    double weighted_coherence = std::numeric_limits<double>::quiet_NaN();
    double weighted_circular_rmse_rad = std::numeric_limits<double>::quiet_NaN();
    double median_abs_residual_rad = std::numeric_limits<double>::quiet_NaN();
    double p95_abs_residual_rad = std::numeric_limits<double>::quiet_NaN();
};

std::mutex& tapMutex()
{
    static std::mutex mutex;
    return mutex;
}

std::string joinPath(const std::string& left, const std::string& right)
{
    if (left.empty()) return right;
    if (left[left.size() - 1] == '/' || left[left.size() - 1] == '\\') {
        return left + right;
    }
    return left + "/" + right;
}

bool ensureDir(const std::string& dir)
{
    if (dir.empty()) return false;
    std::string current;
    std::size_t i = 0;
    if (dir[0] == '/') {
        current = "/";
        i = 1;
    }
    for (; i < dir.size(); ++i) {
        current.push_back(dir[i]);
        if (dir[i] == '/' || i + 1 == dir.size()) {
            if (current == "/" || current == "./" || current == ".") continue;
            if (::mkdir(current.c_str(), 0755) != 0 && errno != EEXIST) {
                return false;
            }
        }
    }
    return true;
}

bool nonEmptyFile(const std::string& path)
{
    struct stat st {};
    return ::stat(path.c_str(), &st) == 0 && st.st_size > 0;
}

std::string csvEscape(const std::string& value)
{
    if (value.find_first_of(",\"\n\r") == std::string::npos) return value;
    std::string out = "\"";
    for (std::size_t i = 0; i < value.size(); ++i) {
        if (value[i] == '"') out += "\"\"";
        else out.push_back(value[i]);
    }
    out.push_back('"');
    return out;
}

std::string safeRunId(const Config& cfg)
{
    const std::string runtime_id = gmti::runtime::runId();
    if (!runtime_id.empty()) return runtime_id;
    std::ostringstream os;
    os << "result_" << (cfg.result_file_id > 0 ? cfg.result_file_id : 0);
    return os.str();
}

std::string safeCaseId()
{
    const std::string id = gmti::runtime::caseId();
    return id.empty() ? "unknown_case" : id;
}

std::string safeResultId(const Config& cfg)
{
    const std::string id = gmti::runtime::resultId();
    if (!id.empty()) return id;
    std::ostringstream os;
    os << "GMTI" << std::setw(2) << std::setfill('0')
       << (cfg.result_file_id > 0 ? cfg.result_file_id : 0);
    return os.str();
}

bool writeNpyFloat32(const std::string& path,
                     const std::vector<float>& values,
                     std::size_t rows,
                     std::size_t cols,
                     std::string& error)
{
    if (rows == 0 || cols == 0 || rows * cols != values.size()) {
        error = "invalid NPY shape for " + path;
        return false;
    }
    std::ostringstream dict;
    dict << "{'descr': '<f4', 'fortran_order': False, 'shape': (";
    if (rows == 1) dict << cols << ",";
    else dict << rows << ", " << cols;
    dict << "), }";
    std::string header = dict.str();
    const std::size_t preamble = 10;
    const std::size_t remainder = (preamble + header.size() + 1) % 16;
    const std::size_t padding = remainder == 0 ? 0 : 16 - remainder;
    header.append(padding, ' ');
    header.push_back('\n');
    if (header.size() > 65535U) {
        error = "NPY header too large for " + path;
        return false;
    }

    std::ofstream os(path.c_str(), std::ios::binary | std::ios::trunc);
    if (!os) {
        error = "cannot open " + path;
        return false;
    }
    const char magic[] = {static_cast<char>(0x93), 'N', 'U', 'M', 'P', 'Y'};
    os.write(magic, sizeof(magic));
    const unsigned char version[2] = {1, 0};
    os.write(reinterpret_cast<const char*>(version), sizeof(version));
    const std::uint16_t header_len = static_cast<std::uint16_t>(header.size());
    const unsigned char length_bytes[2] = {
        static_cast<unsigned char>(header_len & 0xffU),
        static_cast<unsigned char>((header_len >> 8) & 0xffU)
    };
    os.write(reinterpret_cast<const char*>(length_bytes), sizeof(length_bytes));
    os.write(header.data(), static_cast<std::streamsize>(header.size()));
    os.write(reinterpret_cast<const char*>(values.data()),
             static_cast<std::streamsize>(values.size() * sizeof(float)));
    if (!os) {
        error = "failed writing " + path;
        return false;
    }
    return true;
}

PowerStats summarize(const std::vector<float>& values,
                     int rows,
                     int cols,
                     int row_begin,
                     int row_end,
                     int col_begin,
                     int col_end)
{
    PowerStats stats;
    std::vector<double> finite;
    finite.reserve(static_cast<std::size_t>(std::max(0, row_end - row_begin + 1)) *
                   static_cast<std::size_t>(std::max(0, col_end - col_begin + 1)));
    long double sum = 0.0;
    for (int row = row_begin; row <= row_end; ++row) {
        if (row < 0 || row >= rows) continue;
        const std::size_t offset = static_cast<std::size_t>(row) *
                                   static_cast<std::size_t>(cols);
        for (int col = col_begin; col <= col_end; ++col) {
            if (col < 0 || col >= cols) continue;
            const double value = values[offset + static_cast<std::size_t>(col)];
            if (!std::isfinite(value) || value < 0.0) continue;
            finite.push_back(value);
            sum += value;
        }
    }
    if (finite.empty()) return stats;
    std::sort(finite.begin(), finite.end());
    stats.count = finite.size();
    stats.mean = static_cast<double>(sum / static_cast<long double>(finite.size()));
    stats.rms = std::sqrt(std::max(0.0, stats.mean));
    const std::size_t middle = finite.size() / 2;
    stats.median = finite.size() % 2 == 0
        ? 0.5 * (finite[middle - 1] + finite[middle])
        : finite[middle];
    const std::size_t p95_index = static_cast<std::size_t>(
        std::ceil(0.95 * static_cast<double>(finite.size()))) - 1U;
    stats.p95 = finite[std::min(p95_index, finite.size() - 1U)];
    stats.maximum = finite.back();
    return stats;
}

double supportQuantile(const std::vector<float>& values,
                       int rows, int cols,
                       int row_begin, int row_end,
                       int col_begin, int col_end,
                       double quantile)
{
    std::vector<double> finite;
    for (int row = row_begin; row <= row_end; ++row) {
        if (row < 0 || row >= rows) continue;
        const std::size_t offset = static_cast<std::size_t>(row) *
                                   static_cast<std::size_t>(cols);
        for (int col = col_begin; col <= col_end; ++col) {
            if (col < 0 || col >= cols) continue;
            const double value = values[offset + static_cast<std::size_t>(col)];
            if (std::isfinite(value) && value >= 0.0) finite.push_back(value);
        }
    }
    if (finite.empty()) return std::numeric_limits<double>::quiet_NaN();
    std::sort(finite.begin(), finite.end());
    const double q = std::max(0.0, std::min(1.0, quantile));
    const std::size_t index = static_cast<std::size_t>(
        std::floor(q * static_cast<double>(finite.size() - 1U)));
    return finite[index];
}

PowerStats summarizeSelected(const std::vector<float>& values,
                             const std::vector<float>& selection,
                             int rows, int cols,
                             int row_begin, int row_end,
                             int col_begin, int col_end,
                             double selection_threshold)
{
    PowerStats stats;
    std::vector<double> finite;
    long double sum = 0.0L;
    for (int row = row_begin; row <= row_end; ++row) {
        if (row < 0 || row >= rows) continue;
        const std::size_t offset = static_cast<std::size_t>(row) *
                                   static_cast<std::size_t>(cols);
        for (int col = col_begin; col <= col_end; ++col) {
            if (col < 0 || col >= cols) continue;
            const std::size_t index = offset + static_cast<std::size_t>(col);
            const double selector = selection[index];
            const double value = values[index];
            if (!std::isfinite(selector) || selector < selection_threshold ||
                !std::isfinite(value) || value < 0.0) continue;
            finite.push_back(value);
            sum += value;
        }
    }
    if (finite.empty()) return stats;
    std::sort(finite.begin(), finite.end());
    stats.count = finite.size();
    stats.mean = static_cast<double>(sum / finite.size());
    stats.rms = std::sqrt(std::max(0.0, stats.mean));
    const std::size_t middle = finite.size() / 2U;
    stats.median = finite.size() % 2U == 0U
        ? 0.5 * (finite[middle - 1U] + finite[middle]) : finite[middle];
    const std::size_t p95_index = static_cast<std::size_t>(
        std::ceil(0.95 * static_cast<double>(finite.size()))) - 1U;
    stats.p95 = finite[std::min(p95_index, finite.size() - 1U)];
    stats.maximum = finite.back();
    return stats;
}

double ratioDb(double numerator, double denominator)
{
    if (!(numerator > 0.0) || !(denominator > 0.0)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    return 10.0 * std::log10(numerator / denominator);
}

double wrapAngleRad(double value)
{
    return std::atan2(std::sin(value), std::cos(value));
}

PhaseModelStats summarizePhaseModel(
    const std::vector<std::complex<float> >& channel1,
    const std::vector<std::complex<float> >& channel2,
    const std::vector<float>& fa_axis_hz,
    int rows,
    int cols,
    int row_begin,
    int row_end,
    int col_begin,
    int col_end,
    const std::array<float, 2>& phase_fit,
    std::vector<float>* residual_map)
{
    PhaseModelStats stats;
    if (residual_map != nullptr) {
        residual_map->assign(
            static_cast<std::size_t>(rows) * static_cast<std::size_t>(cols),
            std::numeric_limits<float>::quiet_NaN());
    }
    std::vector<double> absolute_residuals;
    long double weight_sum = 0.0L;
    long double residual_sq_sum = 0.0L;
    std::complex<long double> coherent_sum(0.0L, 0.0L);
    for (int row = row_begin; row <= row_end; ++row) {
        if (row < 0 || row >= rows) continue;
        const double predicted_phase =
            static_cast<double>(phase_fit[0]) *
                static_cast<double>(fa_axis_hz[static_cast<std::size_t>(row)]) +
            static_cast<double>(phase_fit[1]);
        const std::size_t offset = static_cast<std::size_t>(row) *
                                   static_cast<std::size_t>(cols);
        for (int col = col_begin; col <= col_end; ++col) {
            if (col < 0 || col >= cols) continue;
            const std::size_t index = offset + static_cast<std::size_t>(col);
            const std::complex<double> a(channel1[index].real(), channel1[index].imag());
            const std::complex<double> b(channel2[index].real(), channel2[index].imag());
            const double weight = std::min(std::norm(a), std::norm(b));
            if (!(weight > 0.0) || !std::isfinite(weight)) continue;
            const std::complex<double> cross = a * std::conj(b);
            if (!(std::norm(cross) > 0.0)) continue;
            const double residual = wrapAngleRad(std::arg(cross) - predicted_phase);
            if (residual_map != nullptr) {
                (*residual_map)[index] = static_cast<float>(residual);
            }
            absolute_residuals.push_back(std::fabs(residual));
            weight_sum += static_cast<long double>(weight);
            residual_sq_sum += static_cast<long double>(weight) * residual * residual;
            coherent_sum += static_cast<long double>(weight) *
                std::complex<long double>(std::cos(residual), std::sin(residual));
        }
    }
    if (absolute_residuals.empty() || !(weight_sum > 0.0L)) return stats;
    std::sort(absolute_residuals.begin(), absolute_residuals.end());
    stats.count = absolute_residuals.size();
    stats.weighted_coherence = static_cast<double>(std::abs(coherent_sum) / weight_sum);
    stats.weighted_circular_rmse_rad =
        std::sqrt(static_cast<double>(residual_sq_sum / weight_sum));
    const std::size_t middle = absolute_residuals.size() / 2U;
    stats.median_abs_residual_rad = absolute_residuals.size() % 2U == 0U
        ? 0.5 * (absolute_residuals[middle - 1U] + absolute_residuals[middle])
        : absolute_residuals[middle];
    const std::size_t p95_index = static_cast<std::size_t>(
        std::ceil(0.95 * static_cast<double>(absolute_residuals.size()))) - 1U;
    stats.p95_abs_residual_rad =
        absolute_residuals[std::min(p95_index, absolute_residuals.size() - 1U)];
    return stats;
}

} // namespace

bool writeCsiMetricsTap(const Config& cfg,
                        int period_id,
                        int beam_id,
                        int rows,
                        int cols,
                        int az_st,
                        int az_ed,
                        int rg_st,
                        int rg_ed,
                        const std::vector<float>& fa_axis_hz,
                        const std::vector<double>& range_axis_m,
                        const std::vector<std::complex<float> >& channel1,
                        const std::vector<std::complex<float> >& channel2,
                        const std::vector<std::complex<float> >& ctdr_channel1,
                        const std::vector<std::complex<float> >& ctdr_channel2,
                        const std::vector<std::complex<float> >& csi_after,
                        const std::array<float, 2>& csi_phase_fit,
                        std::string& error)
{
    error.clear();
    if (!cfg.csi_metrics_enable) return true;
    if (cfg.csi_metrics_beam_id >= 0 && cfg.csi_metrics_beam_id != beam_id) {
        return true;
    }
    if (rows <= 0 || cols <= 0) {
        error = "CSI metrics tap received non-positive matrix shape";
        return false;
    }
    const std::size_t total = static_cast<std::size_t>(rows) *
                              static_cast<std::size_t>(cols);
    if (channel1.size() != total || channel2.size() != total ||
        ctdr_channel1.size() != total || ctdr_channel2.size() != total ||
        csi_after.size() != total || fa_axis_hz.size() != static_cast<std::size_t>(rows) ||
        range_axis_m.size() < static_cast<std::size_t>(cols)) {
        error = "CSI metrics tap matrix/axis size mismatch";
        return false;
    }

    std::vector<float> before_power(total);
    std::vector<float> after_power(total);
    std::vector<float> channel1_power(total);
    std::vector<float> channel2_power(total);
    std::vector<float> csi_after_real(total);
    std::vector<float> csi_after_imag(total);
    std::vector<float> ctdr_channel1_real(total);
    std::vector<float> ctdr_channel1_imag(total);
    std::vector<float> ctdr_channel2_real(total);
    std::vector<float> ctdr_channel2_imag(total);
    az_st = std::max(0, std::min(az_st, rows - 1));
    az_ed = std::max(0, std::min(az_ed, rows - 1));
    rg_st = std::max(0, std::min(rg_st, cols - 1));
    rg_ed = std::max(0, std::min(rg_ed, cols - 1));
    if (az_st > az_ed || rg_st > rg_ed) {
        error = "CSI metrics tap received empty active support";
        return false;
    }

    const bool full_csi_detection_band =
        cfg.csi_detection_band_mode == "full";
    const bool union_csi_detection_band =
        cfg.csi_detection_band_mode == "union";
    const bool split_csi_detection_band =
        cfg.csi_detection_band_mode == "split";
    const int detector_csi_band_st = split_csi_detection_band
        ? std::max(0, az_st - cfg.csi_split_boundary_guard_rows) : az_st;
    const int detector_csi_band_ed = split_csi_detection_band
        ? std::min(rows - 1, az_ed + cfg.csi_split_boundary_guard_rows) : az_ed;
    const std::string detector_input_definition =
        full_csi_detection_band
            ? "production_detector_input_abs2_full_csi"
            : union_csi_detection_band
                  ? "production_detector_input_abs2_union_dynamic_and_full_csi"
                  : split_csi_detection_band
                        ? "production_detector_input_abs2_csi_split_band_channel2_outside"
                        : "production_detector_input_abs2_csi_dynamic_band_channel2_outside";
    std::vector<std::complex<float> > detector_input(total);
    for (std::size_t i = 0; i < total; ++i) {
        // clutter_cancel_kernel equalizes both channel magnitudes to their
        // minimum before subtraction.  This is the exact single-channel power
        // immediately before the CSI subtractor; phase rotation preserves it.
        before_power[i] = (cfg.csi_cancellation_mode == "row_complex_ls" ||
                           cfg.csi_cancellation_mode == "row_phase_ls_linear")
            ? std::norm(channel1[i])
            : std::min(std::norm(channel1[i]), std::norm(channel2[i]));
        // 这两个图只在显式 dump 时写出。它们使 S-only/C+N 配对标定可以在
        // 同一真值单元分别估计两路相位观测的有效 SCNR，而不会参与检测。
        channel1_power[i] = std::norm(channel1[i]);
        channel2_power[i] = std::norm(channel2[i]);
        const int row = static_cast<int>(i / static_cast<std::size_t>(cols));
        const bool in_csi_band = full_csi_detection_band ||
            (row >= detector_csi_band_st && row <= detector_csi_band_ed);
        if (union_csi_detection_band) {
            // The union detector retains the larger power from its dynamic
            // mixed source and its full-CSI source, cell by cell.
            detector_input[i] = std::norm(csi_after[i]) >= std::norm(channel2[i])
                ? csi_after[i] : channel2[i];
        } else {
            detector_input[i] = in_csi_band ? csi_after[i] : channel2[i];
        }
        after_power[i] = std::norm(detector_input[i]);
        csi_after_real[i] = detector_input[i].real();
        csi_after_imag[i] = detector_input[i].imag();
        // This is the exact pre-CSI CTDR interferometric pair from which the
        // production raw phase map is captured.  It is a diagnostics-only
        // tap used to validate the phase/angle uncertainty model.
        ctdr_channel1_real[i] = ctdr_channel1[i].real();
        ctdr_channel1_imag[i] = ctdr_channel1[i].imag();
        ctdr_channel2_real[i] = ctdr_channel2[i].real();
        ctdr_channel2_imag[i] = ctdr_channel2[i].imag();
    }

    const PowerStats before = summarize(
        before_power, rows, cols, az_st, az_ed, rg_st, rg_ed);
    const PowerStats after = summarize(
        after_power, rows, cols, az_st, az_ed, rg_st, rg_ed);
    std::vector<float> phase_residual_map;
    const PhaseModelStats phase_model = summarizePhaseModel(
        channel1, channel2, fa_axis_hz, rows, cols,
        az_st, az_ed, rg_st, rg_ed, csi_phase_fit,
        cfg.csi_metrics_dump_power_maps ? &phase_residual_map : nullptr);
    if (before.count == 0 || after.count == 0) {
        error = "CSI metrics tap active support contains no finite cells";
        return false;
    }

    std::lock_guard<std::mutex> lock(tapMutex());
    const std::string run_id = safeRunId(cfg);
    const std::string tap_dir = joinPath(joinPath(cfg.result_add, "csi_metrics"), run_id);
    if (!ensureDir(tap_dir)) {
        error = "cannot create CSI metrics directory " + tap_dir;
        return false;
    }

    std::ostringstream stem;
    stem << "period" << std::setw(3) << std::setfill('0') << period_id
         << "_beam" << std::setw(3) << std::setfill('0') << beam_id;
    std::string before_path;
    std::string after_path;
    std::string channel1_path;
    std::string channel2_path;
    std::string after_real_path;
    std::string after_imag_path;
    std::string ctdr_channel1_real_path;
    std::string ctdr_channel1_imag_path;
    std::string ctdr_channel2_real_path;
    std::string ctdr_channel2_imag_path;
    std::string fa_path;
    std::string range_path;
    std::string phase_residual_path;
    if (cfg.csi_metrics_dump_power_maps) {
        after_path = joinPath(tap_dir, stem.str() + "_after_power.npy");
        fa_path = joinPath(tap_dir, stem.str() + "_fa_axis_hz.npy");
        range_path = joinPath(tap_dir, stem.str() + "_range_axis_m.npy");
        std::vector<float> range_axis_float(static_cast<std::size_t>(cols));
        for (int i = 0; i < cols; ++i) {
            range_axis_float[static_cast<std::size_t>(i)] =
                static_cast<float>(range_axis_m[static_cast<std::size_t>(i)]);
        }
        if (cfg.csi_metrics_dump_intermediate_maps) {
            before_path = joinPath(tap_dir, stem.str() + "_before_power.npy");
            channel1_path = joinPath(tap_dir, stem.str() + "_channel1_power.npy");
            channel2_path = joinPath(tap_dir, stem.str() + "_channel2_power.npy");
            after_real_path = joinPath(tap_dir, stem.str() + "_after_real.npy");
            after_imag_path = joinPath(tap_dir, stem.str() + "_after_imag.npy");
            ctdr_channel1_real_path = joinPath(tap_dir, stem.str() + "_ctdr_channel1_real.npy");
            ctdr_channel1_imag_path = joinPath(tap_dir, stem.str() + "_ctdr_channel1_imag.npy");
            ctdr_channel2_real_path = joinPath(tap_dir, stem.str() + "_ctdr_channel2_real.npy");
            ctdr_channel2_imag_path = joinPath(tap_dir, stem.str() + "_ctdr_channel2_imag.npy");
            phase_residual_path = joinPath(
                tap_dir, stem.str() + "_phase_model_residual_rad.npy");
            if (!writeNpyFloat32(before_path, before_power,
                                 static_cast<std::size_t>(rows),
                                 static_cast<std::size_t>(cols), error) ||
                !writeNpyFloat32(after_path, after_power,
                                 static_cast<std::size_t>(rows),
                                 static_cast<std::size_t>(cols), error) ||
                !writeNpyFloat32(channel1_path, channel1_power,
                                 static_cast<std::size_t>(rows),
                                 static_cast<std::size_t>(cols), error) ||
                !writeNpyFloat32(channel2_path, channel2_power,
                                 static_cast<std::size_t>(rows),
                                 static_cast<std::size_t>(cols), error) ||
                !writeNpyFloat32(after_real_path, csi_after_real,
                                 static_cast<std::size_t>(rows),
                                 static_cast<std::size_t>(cols), error) ||
                !writeNpyFloat32(after_imag_path, csi_after_imag,
                                 static_cast<std::size_t>(rows),
                                 static_cast<std::size_t>(cols), error) ||
                !writeNpyFloat32(ctdr_channel1_real_path, ctdr_channel1_real,
                                 static_cast<std::size_t>(rows),
                                 static_cast<std::size_t>(cols), error) ||
                !writeNpyFloat32(ctdr_channel1_imag_path, ctdr_channel1_imag,
                                 static_cast<std::size_t>(rows),
                                 static_cast<std::size_t>(cols), error) ||
                !writeNpyFloat32(ctdr_channel2_real_path, ctdr_channel2_real,
                                 static_cast<std::size_t>(rows),
                                 static_cast<std::size_t>(cols), error) ||
                !writeNpyFloat32(ctdr_channel2_imag_path, ctdr_channel2_imag,
                                 static_cast<std::size_t>(rows),
                                 static_cast<std::size_t>(cols), error) ||
                !writeNpyFloat32(fa_path, fa_axis_hz, 1,
                                 static_cast<std::size_t>(rows), error) ||
                !writeNpyFloat32(range_path, range_axis_float, 1,
                                 static_cast<std::size_t>(cols), error) ||
                !writeNpyFloat32(phase_residual_path, phase_residual_map,
                                 static_cast<std::size_t>(rows),
                                 static_cast<std::size_t>(cols), error)) {
                return false;
            }
        } else if (!writeNpyFloat32(after_path, after_power,
                                    static_cast<std::size_t>(rows),
                                    static_cast<std::size_t>(cols), error) ||
                   !writeNpyFloat32(fa_path, fa_axis_hz, 1,
                                    static_cast<std::size_t>(rows), error) ||
                   !writeNpyFloat32(range_path, range_axis_float, 1,
                                    static_cast<std::size_t>(cols), error)) {
            return false;
        }
    }

    const std::string manifest_path = joinPath(tap_dir, "csi_roi_manifest.csv");
    const bool manifest_has_data = nonEmptyFile(manifest_path);
    std::ofstream manifest(manifest_path.c_str(), std::ios::app);
    if (!manifest) {
        error = "cannot append " + manifest_path;
        return false;
    }
    if (!manifest_has_data) {
        manifest << "case_id,run_id,result_id,period_id,beam_id,rows,cols,az_st,az_ed,"
                    "rg_st,rg_ed,before_definition,after_definition,cfar_input_definition,"
                    "alignment_mode,"
                    "p38_k_rad_per_hz,p38_b_rad,before_power_path,after_power_path,"
                    "channel1_power_path,channel2_power_path,"
                    "after_real_path,after_imag_path,"
                    "ctdr_channel1_real_path,ctdr_channel1_imag_path,"
                    "ctdr_channel2_real_path,ctdr_channel2_imag_path,"
                    "fa_axis_path,range_axis_path,phase_model_residual_path,"
                    "target_half_range_bins,"
                    "target_half_doppler_bins,guard_range_bins,guard_doppler_bins,"
                    "background_range_bins,background_doppler_bins,"
                    "strong_peak_threshold_db,min_valid_background_cells\n";
    }
    const std::string before_definition =
        (cfg.csi_cancellation_mode == "row_complex_ls" ||
         cfg.csi_cancellation_mode == "row_phase_ls_linear")
        ? "channel1_abs2_after_alignment_fft_dbs_range_phase_correction"
        : "amplitude_equalized_single_channel_power_min_abs2_after_alignment_fft_dbs_range_phase_correction";
    manifest << csvEscape(safeCaseId()) << ',' << csvEscape(run_id) << ','
             << csvEscape(safeResultId(cfg)) << ',' << period_id << ',' << beam_id << ','
             << rows << ',' << cols << ',' << az_st << ',' << az_ed << ',' << rg_st << ','
             << rg_ed << ','
             << csvEscape(before_definition) << ','
             << csvEscape(detector_input_definition) << ','
             << csvEscape(detector_input_definition) << ','
             << csvEscape(cfg.csi_channel_alignment_mode) << ','
             << std::setprecision(15) << csi_phase_fit[0] << ',' << csi_phase_fit[1] << ','
             << csvEscape(before_path) << ',' << csvEscape(after_path) << ','
             << csvEscape(channel1_path) << ',' << csvEscape(channel2_path) << ','
             << csvEscape(after_real_path) << ',' << csvEscape(after_imag_path) << ','
             << csvEscape(ctdr_channel1_real_path) << ',' << csvEscape(ctdr_channel1_imag_path) << ','
             << csvEscape(ctdr_channel2_real_path) << ',' << csvEscape(ctdr_channel2_imag_path) << ','
             << csvEscape(fa_path) << ',' << csvEscape(range_path) << ','
             << csvEscape(phase_residual_path) << ','
             << cfg.metrics_target_half_range_bins << ','
             << cfg.metrics_target_half_doppler_bins << ','
             << cfg.metrics_guard_range_bins << ',' << cfg.metrics_guard_doppler_bins << ','
             << cfg.metrics_background_range_bins << ','
             << cfg.metrics_background_doppler_bins << ','
             << cfg.metrics_strong_peak_threshold_db << ','
             << cfg.metrics_min_valid_background_cells << '\n';

    const std::string summary_path = joinPath(tap_dir, "csi_metric_tap_summary.csv");
    const bool summary_has_data = nonEmptyFile(summary_path);
    std::ofstream summary(summary_path.c_str(), std::ios::app);
    if (!summary) {
        error = "cannot append " + summary_path;
        return false;
    }
    if (!summary_has_data) {
        summary << "case_id,run_id,result_id,period_id,beam_id,roi_name,valid_cells,"
                   "before_mean_power,after_mean_power,before_median_power,"
                   "after_median_power,before_rms,after_rms,before_p95,after_p95,"
                   "before_max,after_max,CA_ROI_dB,phase_model_valid_cells,"
                   "phase_model_weighted_coherence,phase_model_weighted_circular_rmse_rad,"
                   "phase_model_median_abs_residual_rad,phase_model_p95_abs_residual_rad,"
                   "status\n";
    }
    const auto write_summary_row = [&](const std::string& roi_name,
                                       const PowerStats& before_stats,
                                       const PowerStats& after_stats,
                                       const std::string& status) {
        summary << csvEscape(safeCaseId()) << ',' << csvEscape(run_id) << ','
                << csvEscape(safeResultId(cfg)) << ',' << period_id << ',' << beam_id << ','
                << roi_name << ',' << std::min(before_stats.count, after_stats.count) << ','
                << std::setprecision(15) << before_stats.mean << ',' << after_stats.mean << ','
                << before_stats.median << ',' << after_stats.median << ','
                << before_stats.rms << ',' << after_stats.rms << ','
                << before_stats.p95 << ',' << after_stats.p95 << ','
                << before_stats.maximum << ',' << after_stats.maximum << ','
                << ratioDb(before_stats.mean, after_stats.mean) << ','
                << phase_model.count << ',' << phase_model.weighted_coherence << ','
                << phase_model.weighted_circular_rmse_rad << ','
                << phase_model.median_abs_residual_rad << ','
                << phase_model.p95_abs_residual_rad << ',' << status << '\n';
    };
    write_summary_row("active_support_unmasked", before, after,
                      "diagnostic_noise_and_clutter_mixed");
    PowerStats before_top1;
    PowerStats after_top1;
    const double quantiles[] = {0.90, 0.99, 0.999};
    const char* names[] = {"clutter_band_strong_power_top10pct",
                           "clutter_band_strong_power_top1pct",
                           "clutter_band_strong_power_top0p1pct"};
    for (int i = 0; i < 3; ++i) {
        const double threshold = supportQuantile(before_power, rows, cols,
            az_st, az_ed, rg_st, rg_ed, quantiles[i]);
        const PowerStats selected_before = summarizeSelected(
            before_power, before_power, rows, cols, az_st, az_ed, rg_st, rg_ed, threshold);
        const PowerStats selected_after = summarizeSelected(
            after_power, before_power, rows, cols, az_st, az_ed, rg_st, rg_ed, threshold);
        write_summary_row(names[i], selected_before, selected_after,
                          "formal_clutter_band_strong_roi_before_power_selected");
        if (i == 1) {
            before_top1 = selected_before;
            after_top1 = selected_after;
        }
    }
    // 同时给运行日志一条稳定、可 grep 的逐波位摘要；CA_ROI_dB=10log10(P_before/P_after)，
    // 正值才表示对消改善。数值也完整写入上述 CSV，避免只依赖交错的并行 stdout。
    std::cout << "[CSI][CANCELLATION] period=" << period_id
              << " beam=" << beam_id
              << " support_rows=[" << az_st << ',' << az_ed << ']'
              << " before_mean_power=" << std::setprecision(8) << before.mean
              << " after_mean_power=" << after.mean
              << " improvement_db=" << ratioDb(before.mean, after.mean)
              << " strong_top1pct_improvement_db="
              << ratioDb(before_top1.mean, after_top1.mean)
              << " phase_coherence=" << phase_model.weighted_coherence
              << " phase_rmse_rad=" << phase_model.weighted_circular_rmse_rad
              << std::endl;
    return true;
}

} // namespace metrics
} // namespace gmti
