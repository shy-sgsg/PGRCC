#include <iostream>
#include <fstream>
#include <vector>
#include <cstdint>
#include <complex>
#include <cmath>
#include <chrono>
#include <algorithm> // lower_bound
#include <numeric>   // accumulate
#include <cstring>
#include <cstdlib>
#include <limits>
#include <sstream>
#include <iomanip>
#include <map>
#include <unordered_map>
#include <unordered_set>
#include <cerrno>
#include <mutex>
#include <sys/stat.h>
//#include <Eigen/Dense>
#include "GMTIProcessor.hpp"
#include "rangeCompress.hpp"
#include "geo/geoProj.hpp"
#include "../simulator/common/SimulationGeometry.h"
#include "rotation_xy.hpp"
#include "unwrap_fd.hpp"
#include "motion_comp.hpp"
#include "dbs/NewProtocolReader.hpp"
#include "trig_lut.hpp"
#include "p38_refit_utils.hpp"
#include "csi_metrics_tap.hpp"
#include "doppler_center.hpp"
#include "channel_calibration.hpp"
#include "mechanical_scan.hpp"
#include "go_cfar_alpha.hpp"
#include "imu_interpolation.hpp"

using cudacd = cuFloatComplex;
using cd = std::complex<float>;

inline int sgn(double x, double eps = 1e-6) { return (x > eps) - (x < -eps); }

namespace {

std::string joinPathLocal(const std::string &a, const std::string &b)
{
    if (a.empty()) return b;
    if (a.back() == '/' || a.back() == '\\') return a + b;
    return a + "/" + b;
}

bool ensureDirLocal(const std::string &dir)
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
            if (cur == "/" || cur == "./" || cur == ".") continue;
            if (::mkdir(cur.c_str(), 0755) != 0 && errno != EEXIST) {
                return false;
            }
        }
    }
    return true;
}

bool loadPairedRangePhaseOverride(const std::string &path,
                                  std::size_t expected_size,
                                  std::vector<float> &phi)
{
    std::ifstream input(path.c_str(), std::ios::binary | std::ios::ate);
    if (!input) {
        std::cerr << "[paired-calibration][ERR] cannot open range-phase reference: "
                  << path << std::endl;
        return false;
    }
    const std::streamoff expected_bytes = static_cast<std::streamoff>(
        expected_size * sizeof(float));
    if (input.tellg() != expected_bytes) {
        std::cerr << "[paired-calibration][ERR] range-phase reference has unexpected size: "
                  << path << " expected=" << expected_bytes << " bytes" << std::endl;
        return false;
    }
    input.seekg(0, std::ios::beg);
    phi.resize(expected_size);
    input.read(reinterpret_cast<char *>(phi.data()), expected_bytes);
    if (!input || std::any_of(phi.begin(), phi.end(),
                              [](float value) { return !std::isfinite(value); })) {
        std::cerr << "[paired-calibration][ERR] invalid range-phase reference: "
                  << path << std::endl;
        return false;
    }
    return true;
}

void writePairedRangePhaseReference(const Config &cfg,
                                    int period_id,
                                    int beam_id,
                                    const char *stage,
                                    const std::vector<float> &phi)
{
    if (!cfg.csi_metrics_enable || cfg.result_add.empty() || phi.empty()) return;
    const std::string debug_dir = joinPathLocal(cfg.result_add, "debug");
    if (!ensureDirLocal(debug_dir)) return;
    std::ostringstream name;
    name << "paired_range_phase_" << stage << "_period"
         << std::setw(3) << std::setfill('0') << period_id
         << "_beam" << std::setw(3) << std::setfill('0') << beam_id << ".f32";
    std::ofstream output(joinPathLocal(debug_dir, name.str()).c_str(),
                         std::ios::binary | std::ios::trunc);
    if (!output) {
        std::cerr << "[paired-calibration][WARN] cannot write range-phase reference "
                  << name.str() << std::endl;
        return;
    }
    output.write(reinterpret_cast<const char *>(phi.data()),
                 static_cast<std::streamsize>(phi.size() * sizeof(float)));
}

bool cfarDumpSelectedBeam(int beam_id)
{
    const char *value = std::getenv("GMTI_CFAR_DUMP_BEAM");
    if (!value || !*value) return false;
    // 与 GMTI_PC_DUMP_BEAMS 一致，允许以逗号列出少量待审计波位。正式
    // 批量评估只导出目标所在的波位，避免为全部电子扫描保存 61 幅功率图。
    std::stringstream ss(value);
    std::string item;
    while (std::getline(ss, item, ',')) {
        char *end = nullptr;
        const long requested = std::strtol(item.c_str(), &end, 10);
        if (end != item.c_str() && *end == '\0' && requested == beam_id) {
            return true;
        }
    }
    return false;
}

std::string cfarDiagnosticStem(const Config &cfg, int beam_id)
{
    std::ostringstream stem;
    // PIPE 本地三周期回放会把每个周期写进同一结果目录。以结果编号区分
    // 功率图，保证三帧的 GO 窗口证据不会互相覆盖；单周期旧命名仍保持兼容。
    if (cfg.result_file_id > 0) {
        stem << "cfar_GMTI" << std::setw(2) << std::setfill('0')
             << cfg.result_file_id << "_";
    } else {
        stem << "cfar_";
    }
    stem << "beam" << std::setw(3) << std::setfill('0') << beam_id;
    return stem.str();
}

void writeCfarDiagnostic(const Config &cfg,
                         int beam_id,
                         double fd_ctr_wrapped_hz,
                         const std::vector<float> &hits,
                         const std::vector<float> &power,
                         const std::vector<std::complex<float> > &detect_complex,
                         const std::vector<float> *threshold_map = nullptr)
{
    if (!cfg.runtime_diagnostics_enabled || !cfarDumpSelectedBeam(beam_id) ||
        cfg.result_add.empty() || hits.empty() || power.size() != hits.size()) {
        return;
    }
    const std::string debug_dir = joinPathLocal(cfg.result_add, "debug");
    if (!ensureDirLocal(debug_dir)) return;
    const std::string stem = cfarDiagnosticStem(cfg, beam_id);
    std::ofstream meta(joinPathLocal(debug_dir, stem + "_meta.txt").c_str());
    if (meta) {
        const int rows = effectivePulseNum(cfg);
        const double row_step_hz = rows > 0
            ? cfg.PRF / static_cast<double>(rows)
            : std::numeric_limits<double>::quiet_NaN();
        // GO power/hit rows are indexed on the same centered Doppler axis as
        // the detector.  Persist the actual (data-derived, wrapped) center so
        // an offline truth evaluator can map a target to the very cell that
        // was tested, rather than assuming the simulator's geometric center.
        // Adding any integer multiple of PRF gives the same row modulo rows.
        meta << std::setprecision(17)
             << "rows=" << rows << "\ncols=" << cfg.rg_len
             << "\nhit_value=positive_float\npower_value=linear_power\n"
             << "cfar_type=" << cfg.cfar_type << "\n"
             << "cfar_pfa_configured=" << cfg.pf << "\n"
             << "cfar_alpha=" << (cfg.cfar_type == "GO"
                 ? gmti::go_cfar::calibrated_alpha(
                       cfg.pf, cfg.cfar_guard_cells, cfg.cfar_background_cells)
                 : 0.0) << "\n"
             << "cfar_alpha_method=go_iid_directional_max_calibrated\n"
             << "threshold_map_dump="
             << (threshold_map && threshold_map->size() == hits.size() ? "true" : "false")
             << "\n"
             << "detect_input_complex_dump="
             << (detect_complex.size() == hits.size() ? "true" : "false") << "\n"
             << "doppler_axis_center_wrapped_hz=" << fd_ctr_wrapped_hz << "\n"
             << "doppler_axis_row0_hz="
             << (fd_ctr_wrapped_hz - 0.5 * cfg.PRF) << "\n"
             << "doppler_axis_row_step_hz=" << row_step_hz << "\n";
    }
    // Sparse, read-only Monte Carlo tap. Probe rows are supplied only to this
    // diagnostic after CFAR has completed; they never enter detection logic.
    // Format (no header): result_id beam_id label physical_doppler_hz range_bin.
    // Selecting probes replaces dense matrix dumps, including for missed CUTs.
    const char *probe_path = std::getenv("GMTI_CFAR_PROBES");
    if (probe_path && *probe_path) {
        std::ifstream probes(probe_path);
        std::ofstream out(joinPathLocal(debug_dir, stem + "_probes.csv").c_str());
        if (!probes || !out) throw std::runtime_error("cannot open CFAR sparse probes or output");
        out << "label,row,col,power,real,imag,hit,threshold,background_mean,background_count,patch_peak,peak_row,peak_col\n";
        out << std::setprecision(17);
        const int rows = effectivePulseNum(cfg), cols = cfg.rg_len;
        if (rows <= 0 || cols <= 0 || power.size() != static_cast<size_t>(rows) * cols)
            throw std::runtime_error("invalid CFAR sparse probe matrix dimensions");
        int rid, beam, col;
        double hz;
        std::string label;
        while (probes >> rid >> beam >> label >> hz >> col) {
            if (rid != cfg.result_file_id || beam != beam_id) continue;
            if (!std::isfinite(hz) || col < 0 || col >= cols)
                throw std::runtime_error("invalid CFAR sparse probe coordinate");
            // cuda_stage_dbs_async rotates whole FFT rows using floor(center/df).
            // Subtracting the unquantized center before rounding is wrong by
            // one cell whenever its fractional row offset exceeds one half.
            const double offset = std::remainder(hz, cfg.PRF);
            const float center_f = static_cast<float>(fd_ctr_wrapped_hz);
            const float prf_f = static_cast<float>(cfg.PRF);
            const int shift = static_cast<int>(std::floor(
                (center_f + 0.5f * prf_f) / prf_f * rows)) - rows / 2;
            const int raw_row = static_cast<int>(std::floor((offset / cfg.PRF + 0.5) * rows + 0.5));
            const int row = ((raw_row - shift) % rows + rows) % rows;
            const int guard = cfg.cfar_guard_cells;
            const int outer = guard + cfg.cfar_background_cells;
            if (col < outer || col + outer >= cols)
                throw std::runtime_error("CFAR sparse probe training window outside range axis");
            double background = 0.0;
            int count = 0, peak_row = row, peak_col = col;
            float peak = -1.0f;
            for (int dr = -outer; dr <= outer; ++dr) {
                const int rr = (row + dr + rows) % rows;
                for (int dc = -outer; dc <= outer; ++dc) {
                    const float value = power[static_cast<size_t>(rr) * cols + col + dc];
                    if (std::abs(dr) > guard || std::abs(dc) > guard) {
                        background += value;
                        ++count;
                    }
                    if (std::abs(dr) <= 3 && std::abs(dc) <= 3 && value > peak) {
                        peak = value; peak_row = rr; peak_col = col + dc;
                    }
                }
            }
            const size_t index = static_cast<size_t>(row) * cols + col;
            const double nan = std::numeric_limits<double>::quiet_NaN();
            out << label << ',' << row << ',' << col << ',' << power[index] << ','
                << (detect_complex.size() == power.size() ? detect_complex[index].real() : nan) << ','
                << (detect_complex.size() == power.size() ? detect_complex[index].imag() : nan) << ','
                << hits[index] << ','
                << (threshold_map && threshold_map->size() == power.size() ? (*threshold_map)[index] : nan) << ','
                << background / count << ',' << count << ',' << peak << ',' << peak_row << ',' << peak_col << '\n';
        }
        if (!probes.eof()) throw std::runtime_error("malformed CFAR sparse probe file");
        if (!out) throw std::runtime_error("failed writing CFAR sparse probes");
        return;
    }
    std::ofstream hit_out(joinPathLocal(debug_dir, stem + "_hits.f32").c_str(),
                          std::ios::binary);
    std::ofstream power_out(joinPathLocal(debug_dir, stem + "_power.f32").c_str(),
                            std::ios::binary);
    if (hit_out) hit_out.write(reinterpret_cast<const char *>(hits.data()),
                               static_cast<std::streamsize>(hits.size() * sizeof(float)));
    if (power_out) power_out.write(reinterpret_cast<const char *>(power.data()),
                            static_cast<std::streamsize>(power.size() * sizeof(float)));
    if (threshold_map && threshold_map->size() == hits.size()) {
        std::ofstream threshold_out(
            joinPathLocal(debug_dir, stem + "_threshold.f32").c_str(),
            std::ios::binary);
        if (threshold_out) {
            threshold_out.write(reinterpret_cast<const char *>(threshold_map->data()),
                                static_cast<std::streamsize>(threshold_map->size() * sizeof(float)));
        }
    }
    if (detect_complex.size() == hits.size()) {
        std::vector<float> real(detect_complex.size()), imag(detect_complex.size());
        for (std::size_t i = 0; i < detect_complex.size(); ++i) {
            real[i] = detect_complex[i].real();
            imag[i] = detect_complex[i].imag();
        }
        std::ofstream real_out(
            joinPathLocal(debug_dir, stem + "_complex_real.f32").c_str(), std::ios::binary);
        std::ofstream imag_out(
            joinPathLocal(debug_dir, stem + "_complex_imag.f32").c_str(), std::ios::binary);
        if (real_out) real_out.write(reinterpret_cast<const char *>(real.data()),
                                     static_cast<std::streamsize>(real.size() * sizeof(float)));
        if (imag_out) imag_out.write(reinterpret_cast<const char *>(imag.data()),
                                     static_cast<std::streamsize>(imag.size() * sizeof(float)));
    }
}

void writeSplitClusterFilterDiagnostic(const Config &cfg,
                                       int beam_id,
                                       std::size_t vertical_removed,
                                       std::size_t small_recovered,
                                       std::size_t small_rejected,
                                       std::size_t in_clusters,
                                       std::size_t out_clusters)
{
    if (!cfg.runtime_diagnostics_enabled ||
        !cfarDumpSelectedBeam(beam_id) || cfg.result_add.empty()) {
        return;
    }
    const std::string debug_dir = joinPathLocal(cfg.result_add, "debug");
    if (!ensureDirLocal(debug_dir)) return;
    std::ofstream out(joinPathLocal(
        debug_dir, cfarDiagnosticStem(cfg, beam_id) + "_split_filter.txt").c_str());
    if (!out) return;
    out << "vertical_removed=" << vertical_removed << "\n"
        << "small_recovered=" << small_recovered << "\n"
        << "small_rejected=" << small_rejected << "\n"
        << "in_clusters=" << in_clusters << "\n"
        << "out_clusters=" << out_clusters << "\n";
}

double wrapP38ResidualLocal(double value)
{
    if (!std::isfinite(value)) return value;
    value = std::fmod(value + M_PI, 2.0 * M_PI);
    if (value < 0.0) value += 2.0 * M_PI;
    return value - M_PI;
}

double p38TheorySlopeLocal(const Config &cfg, double platform_speed_mps)
{
    const gmti::ctdr::PhaseCenterPose pose =
        configuredCtdrPhaseCenterPose(cfg);
    if (gmti::ctdr::finitePhaseCenterPose(pose)) {
        const double look_angle_deg = std::isfinite(cfg.ctdr_servo_azimuth_deg)
            ? cfg.ctdr_servo_azimuth_deg : cfg.squint_angle;
        return gmti::ctdr::rawInterferometricSlopeRadPerHz(
            pose, platform_speed_mps, look_angle_deg, cfg.squint_side,
            cfg.channel_phase_sign);
    }
    return gmti::ctdr::rawInterferometricSlopeRadPerHz(
        cfg.d_channel, platform_speed_mps,
        cfg.rx_baseline_sign, cfg.channel_phase_sign);
}

double p38TheorySlopeAfterIntegerAlignment(const Config &cfg,
                                           double platform_speed_mps,
                                           int aligned_prt_shift)
{
    const double raw_slope = p38TheorySlopeLocal(cfg, platform_speed_mps);
    if (!std::isfinite(raw_slope) || !(cfg.PRF > 0.0)) {
        return raw_slope;
    }
    return gmti::ctdr::residualSlopeAfterPrtAlignment(
        raw_slope, cfg.PRF, aligned_prt_shift);
}

int configuredCsiChannelAlignmentPrt(const Config &cfg,
                                     double platform_speed_mps)
{
    // A rotated baseline generally has a cross-track component, so one
    // scalar integer PRT shift cannot align the full receive-channel path.
    // Mechanical mode therefore stays in the simultaneous-sample domain;
    // exact CTDR/P38 geometry handles the phase instead.
    if (cfg.csi_channel_alignment_mode == "none" ||
        gmti::ctdr::finitePhaseCenterPose(
            configuredCtdrPhaseCenterPose(cfg))) {
        return 0;
    }
    return gmti::ctdr::stationaryClutterAlignmentPrt(
        cfg.d_channel, platform_speed_mps, cfg.PRF,
        cfg.rx_baseline_sign, cfg.channel_phase_sign);
}

struct StrongSmallClusterFilterStats {
    double global_median_power = std::numeric_limits<double>::quiet_NaN();
    std::size_t baseline_kept = 0;
    std::size_t strong_small_kept = 0;
    std::size_t small_rejected = 0;
    std::size_t vertical_removed = 0;
};

StrongSmallClusterFilterStats filterStrongSmallClusters(
    const std::vector<float> &cfar_hits,
    const std::vector<float> &power_map,
    int rows,
    int cols,
    int max_gap,
    const Config &cfg,
    std::vector<int> &peak_rows,
    std::vector<int> &peak_cols,
    std::vector<float> &phase_std)
{
    StrongSmallClusterFilterStats stats;
    if (!cfg.cluster_strong_small_enable || rows <= 0 || cols <= 0 ||
        cfar_hits.size() != static_cast<std::size_t>(rows) *
                                static_cast<std::size_t>(cols) ||
        power_map.size() != cfar_hits.size() ||
        peak_rows.size() != peak_cols.size()) {
        stats.baseline_kept = peak_rows.size();
        return stats;
    }

    std::vector<float> finite_power;
    finite_power.reserve(power_map.size());
    for (float value : power_map) {
        // Split-mode branch maps are zero outside their CUT support.  Zeros
        // are padding, not measured power; including them makes the median
        // zero and turns the strong-small threshold into an unconditional
        // small-component acceptor.
        if (std::isfinite(value) && value > 0.0f) {
            finite_power.push_back(value);
        }
    }
    if (finite_power.empty()) {
        stats.small_rejected = peak_rows.size();
        peak_rows.clear();
        peak_cols.clear();
        phase_std.clear();
        return stats;
    }
    const std::size_t middle = finite_power.size() / 2U;
    std::nth_element(finite_power.begin(), finite_power.begin() + middle,
                     finite_power.end());
    stats.global_median_power = finite_power[middle];
    const double strong_threshold = stats.global_median_power * std::pow(
        10.0, cfg.cluster_strong_small_peak_over_median_db / 10.0);

    const std::size_t total = cfar_hits.size();
    std::vector<std::uint8_t> visited(total, 0U);
    std::vector<std::size_t> stack;
    stack.reserve(256);
    std::unordered_map<std::size_t, int> component_size_by_peak;
    for (int row0 = 0; row0 < rows; ++row0) {
        for (int col0 = 0; col0 < cols; ++col0) {
            const std::size_t start = static_cast<std::size_t>(row0) * cols + col0;
            if (visited[start] || !(cfar_hits[start] > 0.0f)) continue;
            visited[start] = 1U;
            stack.clear();
            stack.push_back(start);
            int component_size = 0;
            std::size_t best = start;
            float best_value = cfar_hits[start];
            while (!stack.empty()) {
                const std::size_t index = stack.back();
                stack.pop_back();
                ++component_size;
                if (cfar_hits[index] > best_value) {
                    best_value = cfar_hits[index];
                    best = index;
                }
                const int row = static_cast<int>(index / cols);
                const int col = static_cast<int>(index % cols);
                for (int dr = -1; dr <= 1; ++dr) {
                    int next_row = row + dr;
                    if (cfg.cfar_doppler_circular) {
                        next_row = (next_row % rows + rows) % rows;
                    }
                    if (next_row < 0 || next_row >= rows) continue;
                    for (int dc = -max_gap; dc <= max_gap; ++dc) {
                        if (dr == 0 && dc == 0) continue;
                        const int next_col = col + dc;
                        if (next_col < 0 || next_col >= cols) continue;
                        const std::size_t next =
                            static_cast<std::size_t>(next_row) * cols + next_col;
                        if (visited[next] || !(cfar_hits[next] > 0.0f)) continue;
                        visited[next] = 1U;
                        stack.push_back(next);
                    }
                }
            }
            component_size_by_peak[best] = component_size;
        }
    }

    std::vector<int> kept_rows;
    std::vector<int> kept_cols;
    std::vector<float> kept_phase_std;
    kept_rows.reserve(peak_rows.size());
    kept_cols.reserve(peak_cols.size());
    kept_phase_std.reserve(phase_std.size());
    for (std::size_t i = 0; i < peak_rows.size(); ++i) {
        const int row = peak_rows[i];
        const int col = peak_cols[i];
        if (row < 0 || row >= rows || col < 0 || col >= cols) {
            ++stats.small_rejected;
            continue;
        }
        const std::size_t peak = static_cast<std::size_t>(row) * cols + col;
        const auto found = component_size_by_peak.find(peak);
        const int component_size = found == component_size_by_peak.end()
            ? 0 : found->second;
        const bool baseline = component_size >= cfg.min_points;
        const bool strong_enough =
            static_cast<double>(power_map[peak]) >= strong_threshold;
        const bool strong_small =
            component_size >= cfg.cluster_strong_small_min_points &&
            strong_enough;
        // Once the optional strong-small mode is enabled, apply its power
        // consistency test to every CFAR cluster.  Otherwise a weak regular
        // cluster can bypass the very second-stage false-alarm screen that
        // protects the newly admitted small clusters.
        if ((!baseline && !strong_small) || !strong_enough) {
            ++stats.small_rejected;
            continue;
        }
        stats.baseline_kept += baseline ? 1U : 0U;
        stats.strong_small_kept += (!baseline && strong_small) ? 1U : 0U;
        kept_rows.push_back(row);
        kept_cols.push_back(col);
        if (i < phase_std.size()) kept_phase_std.push_back(phase_std[i]);
    }
    // A single range/beam resolution cell can yield a weak Doppler mirror
    // beside the physical peak after circular CFAR.  It is not spatially
    // resolvable as another target, so retain only the strongest candidate
    // in the same range cell.  This is intentionally inside the optional
    // secondary filter, preserving legacy behavior when it is disabled.
    std::unordered_map<int, std::size_t> best_by_col;
    for (std::size_t i = 0; i < kept_cols.size(); ++i) {
        const auto existing = best_by_col.find(kept_cols[i]);
        if (existing == best_by_col.end() ||
            power_map[static_cast<std::size_t>(kept_rows[i]) * cols + kept_cols[i]] >
                power_map[static_cast<std::size_t>(kept_rows[existing->second]) * cols +
                          kept_cols[existing->second]]) {
            best_by_col[kept_cols[i]] = i;
        }
    }
    std::vector<int> unique_rows;
    std::vector<int> unique_cols;
    std::vector<float> unique_phase_std;
    for (std::size_t i = 0; i < kept_cols.size(); ++i) {
        if (best_by_col[kept_cols[i]] != i) continue;
        unique_rows.push_back(kept_rows[i]);
        unique_cols.push_back(kept_cols[i]);
        if (i < kept_phase_std.size()) unique_phase_std.push_back(kept_phase_std[i]);
    }
    peak_rows.swap(unique_rows);
    peak_cols.swap(unique_cols);
    phase_std.swap(unique_phase_std);
    return stats;
}

// Residual channel cancellation can leave a nearly vertical CFAR component:
// one range bin is hit through many circular Doppler rows.  A moving point
// target in this data has a compact 2-D footprint, so reject only the explicit
// long/single-range-bin morphology.  This is applied per split branch before
// branch merge; it never compares CSI and original-channel powers.
std::size_t filterVerticalLineClusters(
    const std::vector<float> &cfar_hits,
    const std::vector<float> &power_map,
    int rows,
    int cols,
    const Config &cfg,
    std::vector<int> &peak_rows,
    std::vector<int> &peak_cols,
    std::vector<float> &phase_std)
{
    if (!cfg.csi_split_vertical_line_filter_enable || rows <= 0 || cols <= 0 ||
        cfar_hits.size() != static_cast<std::size_t>(rows) * cols ||
        power_map.size() != cfar_hits.size() ||
        peak_rows.size() != peak_cols.size()) {
        return 0U;
    }
    const std::size_t total = cfar_hits.size();
    std::vector<std::uint8_t> visited(total, 0U);
    std::vector<int> component_by_peak(total, 0);
    std::vector<int> component_size;
    std::vector<int> component_row_count;
    std::vector<int> component_col_min;
    std::vector<int> component_col_max;
    std::vector<std::unordered_set<int>> component_cols;
    std::vector<double> component_peak_power;
    std::vector<std::vector<float>> component_power_values;
    std::vector<std::size_t> stack;
    for (int row0 = 0; row0 < rows; ++row0) {
        for (int col0 = 0; col0 < cols; ++col0) {
            const std::size_t start = static_cast<std::size_t>(row0) * cols + col0;
            if (visited[start] || !(cfar_hits[start] > 0.0f)) continue;
            const int component = static_cast<int>(component_size.size());
            component_size.push_back(0);
            component_row_count.push_back(0);
            component_col_min.push_back(col0);
            component_col_max.push_back(col0);
            component_cols.emplace_back();
            component_peak_power.push_back(0.0);
            component_power_values.emplace_back();
            std::unordered_set<int> rows_seen;
            stack.clear();
            stack.push_back(start);
            visited[start] = 1U;
            while (!stack.empty()) {
                const std::size_t index = stack.back();
                stack.pop_back();
                const int row = static_cast<int>(index / cols);
                const int col = static_cast<int>(index % cols);
                component_by_peak[index] = component;
                ++component_size[component];
                rows_seen.insert(row);
                component_cols[component].insert(col);
                const float cell_power = power_map[index];
                if (std::isfinite(cell_power) && cell_power > 0.0f) {
                    component_peak_power[component] = std::max(
                        component_peak_power[component], static_cast<double>(cell_power));
                    component_power_values[component].push_back(cell_power);
                }
                component_col_min[component] = std::min(component_col_min[component], col);
                component_col_max[component] = std::max(component_col_max[component], col);
                for (int dr = -1; dr <= 1; ++dr) {
                    int next_row = row + dr;
                    if (cfg.cfar_doppler_circular) {
                        next_row = (next_row % rows + rows) % rows;
                    }
                    if (next_row < 0 || next_row >= rows) continue;
                    for (int dc = -cfg.cluster_max_range_gap;
                         dc <= cfg.cluster_max_range_gap; ++dc) {
                        if (dr == 0 && dc == 0) continue;
                        const int next_col = col + dc;
                        if (next_col < 0 || next_col >= cols) continue;
                        const std::size_t next = static_cast<std::size_t>(next_row) * cols + next_col;
                        if (visited[next] || !(cfar_hits[next] > 0.0f)) continue;
                        visited[next] = 1U;
                        stack.push_back(next);
                    }
                }
            }
            component_row_count[component] = static_cast<int>(rows_seen.size());
        }
    }
    std::vector<int> kept_rows;
    std::vector<int> kept_cols;
    std::vector<float> kept_phase;
    kept_rows.reserve(peak_rows.size());
    kept_cols.reserve(peak_cols.size());
    kept_phase.reserve(phase_std.size());
    std::size_t removed = 0U;
    for (std::size_t i = 0; i < peak_rows.size(); ++i) {
        const int row = peak_rows[i];
        const int col = peak_cols[i];
        if (row < 0 || row >= rows || col < 0 || col >= cols) {
            ++removed;
            continue;
        }
        const int component = component_by_peak[static_cast<std::size_t>(row) * cols + col];
        const bool vertical = component_row_count[component] >=
                                  cfg.csi_split_vertical_line_min_doppler_rows &&
                              static_cast<int>(component_cols[component].size()) <=
                                  cfg.csi_split_vertical_line_max_range_bins;
        // A true point target is retained if it creates a pronounced peak
        // inside the component.  The known residual-column false alarms are
        // nearly flat along Doppler (peak/median ~= 0 dB), so the morphology
        // gate only removes a vertical component when its peak is below the
        // configured contrast threshold.  This makes the protection
        // measurable and avoids an unconditional hard-coded shape veto.
        double component_median = 0.0;
        if (!component_power_values[component].empty()) {
            auto &values = component_power_values[component];
            const auto middle = values.begin() + values.size() / 2U;
            std::nth_element(values.begin(), middle, values.end());
            component_median = static_cast<double>(*middle);
        }
        const double peak_over_median_db =
            component_median > 0.0 && component_peak_power[component] > 0.0
                ? 10.0 * std::log10(component_peak_power[component] / component_median)
                : std::numeric_limits<double>::infinity();
        const bool low_contrast = peak_over_median_db <=
                                  cfg.csi_split_vertical_line_peak_over_median_db;
        if (vertical && low_contrast) {
            ++removed;
            continue;
        }
        kept_rows.push_back(row);
        kept_cols.push_back(col);
        if (i < phase_std.size()) kept_phase.push_back(phase_std[i]);
    }
    peak_rows.swap(kept_rows);
    peak_cols.swap(kept_cols);
    phase_std.swap(kept_phase);
    return removed;
}

// Split-mode-only small-component recovery.  Unlike the historical optional
// strong-small filter, this keeps every component already meeting min_points
// and applies the power test only to the newly admitted compact components.
// That prevents a global power threshold from deleting weak but valid targets.
std::size_t filterSplitSmallComponents(
    const std::vector<float> &cfar_hits,
    const std::vector<float> &power_map,
    int rows,
    int cols,
    const Config &cfg,
    std::vector<int> &peak_rows,
    std::vector<int> &peak_cols,
    std::vector<float> &phase_std,
    std::size_t *recovered,
    std::size_t *rejected)
{
    if (!cfg.csi_split_small_cluster_enable || rows <= 0 || cols <= 0 ||
        cfar_hits.size() != static_cast<std::size_t>(rows) * cols ||
        power_map.size() != cfar_hits.size() ||
        peak_rows.size() != peak_cols.size()) {
        return 0U;
    }
    std::vector<float> positive_power;
    positive_power.reserve(power_map.size());
    for (float value : power_map) {
        if (std::isfinite(value) && value > 0.0f) positive_power.push_back(value);
    }
    if (positive_power.empty()) return 0U;
    const auto global_middle = positive_power.begin() + positive_power.size() / 2U;
    std::nth_element(positive_power.begin(), global_middle, positive_power.end());
    const double global_median = static_cast<double>(*global_middle);
    const std::size_t total = cfar_hits.size();
    std::vector<std::uint8_t> visited(total, 0U);
    std::vector<int> component_by_cell(total, -1);
    std::vector<int> component_sizes;
    std::vector<std::size_t> stack;
    for (int row0 = 0; row0 < rows; ++row0) {
        for (int col0 = 0; col0 < cols; ++col0) {
            const std::size_t start = static_cast<std::size_t>(row0) * cols + col0;
            if (visited[start] || !(cfar_hits[start] > 0.0f)) continue;
            const int component = static_cast<int>(component_sizes.size());
            visited[start] = 1U;
            stack.clear();
            stack.push_back(start);
            int size = 0;
            while (!stack.empty()) {
                const std::size_t index = stack.back();
                stack.pop_back();
                ++size;
                component_by_cell[index] = component;
                const int row = static_cast<int>(index / cols);
                const int col = static_cast<int>(index % cols);
                for (int dr = -1; dr <= 1; ++dr) {
                    int next_row = row + dr;
                    if (cfg.cfar_doppler_circular) {
                        next_row = (next_row % rows + rows) % rows;
                    }
                    if (next_row < 0 || next_row >= rows) continue;
                    for (int dc = -cfg.cluster_max_range_gap;
                         dc <= cfg.cluster_max_range_gap; ++dc) {
                        if (dr == 0 && dc == 0) continue;
                        const int next_col = col + dc;
                        if (next_col < 0 || next_col >= cols) continue;
                        const std::size_t next = static_cast<std::size_t>(next_row) * cols + next_col;
                        if (visited[next] || !(cfar_hits[next] > 0.0f)) continue;
                        visited[next] = 1U;
                        stack.push_back(next);
                    }
                }
            }
            component_sizes.push_back(size);
        }
    }

    std::vector<int> kept_rows;
    std::vector<int> kept_cols;
    std::vector<float> kept_phase;
    kept_rows.reserve(peak_rows.size());
    kept_cols.reserve(peak_cols.size());
    kept_phase.reserve(phase_std.size());
    std::size_t recovered_count = 0U;
    std::size_t rejected_count = 0U;
    for (std::size_t i = 0; i < peak_rows.size(); ++i) {
        const int row = peak_rows[i];
        const int col = peak_cols[i];
        if (row < 0 || row >= rows || col < 0 || col >= cols) {
            ++rejected_count;
            continue;
        }
        const std::size_t peak = static_cast<std::size_t>(row) * cols + col;
        const int component = component_by_cell[peak];
        const int size = component >= 0 &&
                                 component < static_cast<int>(component_sizes.size())
                             ? component_sizes[component]
                             : 0;
        const bool baseline = size >= cfg.min_points;
        double local_median = std::numeric_limits<double>::quiet_NaN();
        if (!baseline && component >= 0) {
            std::vector<float> local_background;
            const int row_radius = cfg.csi_split_small_cluster_near_doppler_rows;
            const int col_radius = cfg.csi_split_small_cluster_near_range_bins;
            local_background.reserve(static_cast<std::size_t>(
                (2 * row_radius + 1) * (2 * col_radius + 1)));
            const int peak_row = row;
            const int peak_col = col;
            for (int dr = -row_radius; dr <= row_radius; ++dr) {
                int rr = peak_row + dr;
                if (cfg.cfar_doppler_circular) rr = (rr % rows + rows) % rows;
                if (rr < 0 || rr >= rows) continue;
                for (int dc = -col_radius; dc <= col_radius; ++dc) {
                    const int cc = peak_col + dc;
                    if (cc < 0 || cc >= cols) continue;
                    const std::size_t index = static_cast<std::size_t>(rr) * cols + cc;
                    // Use only non-hit samples and exclude the whole connected
                    // component.  This prevents the target's own sidelobes or
                    // a neighbouring hit cluster from becoming its background.
                    if (cfar_hits[index] > 0.0f || component_by_cell[index] == component) {
                        continue;
                    }
                    const float value = power_map[index];
                    if (std::isfinite(value) && value > 0.0f) local_background.push_back(value);
                }
            }
            if (local_background.size() >= 3U) {
                const auto middle_local = local_background.begin() +
                    local_background.size() / 2U;
                std::nth_element(local_background.begin(), middle_local,
                                 local_background.end());
                local_median = static_cast<double>(*middle_local);
            }
        }
        const double reference_median = std::isfinite(local_median) && local_median > 0.0
            ? local_median : global_median;
        const double local_threshold = reference_median * std::pow(
            10.0, cfg.csi_split_small_cluster_peak_over_median_db / 10.0);
        const bool admit_small = size >= cfg.csi_split_small_cluster_min_points &&
            std::isfinite(power_map[peak]) &&
            static_cast<double>(power_map[peak]) >= local_threshold;
        if (!baseline && !admit_small) {
            ++rejected_count;
            continue;
        }
        if (!baseline) ++recovered_count;
        kept_rows.push_back(row);
        kept_cols.push_back(col);
        if (i < phase_std.size()) kept_phase.push_back(phase_std[i]);
    }
    peak_rows.swap(kept_rows);
    peak_cols.swap(kept_cols);
    phase_std.swap(kept_phase);
    if (recovered) *recovered += recovered_count;
    if (rejected) *rejected += rejected_count;
    return recovered_count;
}

void writeP38FitDiagnostics(const Config &cfg,
                            int beam_id,
                            double platform_speed_mps,
                            int az_st,
                            int az_ed,
                            int rg_st,
                            int rg_ed,
                            const std::vector<double> &row_fa,
                            const std::vector<double> &raw_trace,
                            const P38StageMetrics &raw,
                            const std::vector<double> &pre_trace,
                            const P38StageMetrics &pre,
                            const std::vector<double> &refit_trace,
                            const P38StageMetrics &refit,
                            const std::string &used_source)
{
    if (!cfg.p38_diagnostics_dump ||
        cfg.result_add.empty() || !ensureDirLocal(cfg.result_add)) {
        return;
    }
    static std::mutex write_mutex;
    const std::lock_guard<std::mutex> lock(write_mutex);
    std::ostringstream stem;
    stem << "p38_phase_fit_beam" << std::setw(3) << std::setfill('0') << beam_id;
    const std::string json_path = joinPathLocal(cfg.result_add, stem.str() + ".json");
    const std::string csv_path = joinPathLocal(cfg.result_add, stem.str() + "_samples.csv");
    const int theory_sign = configuredP38TheorySign(cfg);
    const double theory_k = p38TheorySlopeLocal(cfg, platform_speed_mps);
    const gmti::ctdr::PhaseCenterPose phase_pose =
        configuredCtdrPhaseCenterPose(cfg);
    double pose_baseline_along_m = std::numeric_limits<double>::quiet_NaN();
    double pose_baseline_right_m = std::numeric_limits<double>::quiet_NaN();
    double pose_baseline_up_m = std::numeric_limits<double>::quiet_NaN();
    const bool pose_valid = gmti::ctdr::rotatedBaselineLocal(
        phase_pose, pose_baseline_along_m, pose_baseline_right_m,
        pose_baseline_up_m);

    auto jsonNumber = [](double value) -> std::string {
        if (!std::isfinite(value)) return "null";
        std::ostringstream os;
        os << std::setprecision(15) << value;
        return os.str();
    };
    auto relativeError = [&](double k) -> double {
        return std::isfinite(k) && std::isfinite(theory_k) && std::abs(theory_k) > 0.0
            ? std::abs((k - theory_k) / theory_k)
            : std::numeric_limits<double>::quiet_NaN();
    };
    auto writeStageJson = [&](std::ostream &os,
                              const char *name,
                              const P38StageMetrics &metrics,
                              bool trailing_comma) {
        os << "    \"" << name << "\": {"
           << "\"k_rad_per_hz\": " << jsonNumber(metrics.p38[0])
           << ", \"b_rad\": " << jsonNumber(metrics.p38[1])
           << ", \"rmse_rad\": " << jsonNumber(metrics.rmse)
           << ", \"median_abs_residual_rad\": "
           << jsonNumber(metrics.median_abs_residual)
           << ", \"p90_abs_residual_rad\": "
           << jsonNumber(metrics.p90_abs_residual)
           << ", \"p95_abs_residual_rad\": "
           << jsonNumber(metrics.p95_abs_residual)
           << ", \"max_abs_residual_rad\": "
           << jsonNumber(metrics.max_abs_residual)
           << ", \"sample_count\": " << metrics.sample_count
           << ", \"inlier_ratio\": " << jsonNumber(metrics.inlier_ratio)
           << ", \"strong_range_masked_bins\": " << metrics.strong_range_masked_bins
           << ", \"relative_theory_error\": " << jsonNumber(relativeError(metrics.p38[0]))
           << ", \"model_source\": \"" << metrics.source << "\""
           << ", \"valid\": " << (metrics.valid ? "true" : "false") << "}"
           << (trailing_comma ? "," : "") << "\n";
    };
    {
        std::ofstream os(json_path.c_str());
        if (os) {
            os << "{\n"
               << "  \"beam_id\": " << beam_id << ",\n"
               << "  \"azimuth_support\": [" << az_st << ", " << az_ed << "],\n"
               << "  \"range_support\": [" << rg_st << ", " << rg_ed << "],\n"
               << "  \"platform_speed_mps\": " << jsonNumber(platform_speed_mps) << ",\n"
               << "  \"baseline_m\": " << jsonNumber(cfg.d_channel) << ",\n"
               << "  \"phase_center_pose_model\": \""
               << (pose_valid ? "mechanical_rotating_receive_centres"
                              : "velocity_aligned_scalar_baseline") << "\",\n"
               << "  \"servo_azimuth_deg\": "
               << jsonNumber(pose_valid ? phase_pose.servo_azimuth_deg
                                        : std::numeric_limits<double>::quiet_NaN()) << ",\n"
               << "  \"physical_baseline_norm_m\": "
               << jsonNumber(pose_valid
                                  ? std::hypot(std::hypot(pose_baseline_along_m,
                                                           pose_baseline_right_m),
                                               pose_baseline_up_m)
                                  : cfg.d_channel) << ",\n"
               << "  \"baseline_along_m\": " << jsonNumber(pose_baseline_along_m) << ",\n"
               << "  \"baseline_right_m\": " << jsonNumber(pose_baseline_right_m) << ",\n"
               << "  \"baseline_up_m\": " << jsonNumber(pose_baseline_up_m) << ",\n"
               << "  \"rx_baseline_sign\": " << cfg.rx_baseline_sign << ",\n"
               << "  \"channel_phase_sign\": " << cfg.channel_phase_sign << ",\n"
               << "  \"theory_sign\": " << theory_sign << ",\n"
               << "  \"theory_k_rad_per_hz\": " << jsonNumber(theory_k) << ",\n"
               << "  \"robust_fit_method\": \""
               << (cfg.p38_enhanced_enable ? "iterative_mad" : "huber")
               << "\",\n"
               << "  \"used_source\": \"" << used_source << "\",\n"
               << "  \"used_model_source\": \""
               << (used_source == "refit" ? refit.source : pre.source)
               << "\",\n"
               << "  \"stages\": {\n";
            writeStageJson(os, "raw", raw, true);
            writeStageJson(os, "pre", pre, true);
            writeStageJson(os, "refit", refit, false);
            os << "  }\n}\n";
        }
    }
    {
        std::ofstream os(csv_path.c_str());
        if (!os) return;
        os << "stage,support_row,absolute_row,fa_hz,phase_unwrapped_rad,predicted_rad,"
              "circular_residual_rad,k_rad_per_hz,b_rad,finite,theory_sign,"
              "theory_k_rad_per_hz\n";
        auto writeTrace = [&](const char *stage,
                              const std::vector<double> &trace,
                              const P38StageMetrics &metrics) {
            const size_t count = std::min(row_fa.size(), trace.size());
            for (size_t i = 0; i < count; ++i) {
                const double fa = row_fa[i];
                const double phase = trace[i];
                const bool finite = std::isfinite(fa) && std::isfinite(phase) &&
                                    std::isfinite(metrics.p38[0]) &&
                                    std::isfinite(metrics.p38[1]);
                const double prediction = finite
                    ? metrics.p38[0] * fa + metrics.p38[1]
                    : std::numeric_limits<double>::quiet_NaN();
                const double residual = finite
                    ? wrapP38ResidualLocal(phase - prediction)
                    : std::numeric_limits<double>::quiet_NaN();
                os << stage << ',' << i << ',' << (az_st + static_cast<int>(i)) << ','
                   << jsonNumber(fa) << ',' << jsonNumber(phase) << ','
                   << jsonNumber(prediction) << ',' << jsonNumber(residual) << ','
                   << jsonNumber(metrics.p38[0]) << ',' << jsonNumber(metrics.p38[1])
                   << ',' << (finite ? 1 : 0) << ',' << theory_sign << ','
                   << jsonNumber(theory_k) << '\n';
            }
        };
        writeTrace("raw", raw_trace, raw);
        writeTrace("pre", pre_trace, pre);
        writeTrace("refit", refit_trace, refit);
    }
}

void writeP38AlignmentDiagnostics(const Config &cfg,
                                  int beam_id,
                                  double platform_speed_mps,
                                  int aligned_prt_shift,
                                  const std::vector<double> &row_fa,
                                  const std::vector<double> &phase_trace,
                                  const std::array<float, 2> &aligned_fit)
{
    if (!cfg.p38_diagnostics_dump ||
        cfg.result_add.empty() || !ensureDirLocal(cfg.result_add)) {
        return;
    }
    static std::mutex write_mutex;
    const std::lock_guard<std::mutex> lock(write_mutex);

    const std::array<double, 2> aligned_fit_double{{
        static_cast<double>(aligned_fit[0]),
        static_cast<double>(aligned_fit[1])
    }};
    const P38StageMetrics metrics = evaluateP38FitMetrics(
        phase_trace, row_fa, aligned_fit_double);
    const double raw_theory = p38TheorySlopeLocal(cfg, platform_speed_mps);
    const double aligned_theory = p38TheorySlopeAfterIntegerAlignment(
        cfg, platform_speed_mps, aligned_prt_shift);
    [[maybe_unused]] const double equivalent_two_way_spacing_m =
        gmti::ctdr::equivalentTwoWayPhaseCenterSeparation(cfg.d_channel);
    [[maybe_unused]] const double equivalent_two_way_delay_s =
        configuredCtdrEquivalentTwoWayDelay(cfg, platform_speed_mps);
    const double continuous_shift_prt =
        std::isfinite(equivalent_two_way_delay_s) && cfg.PRF > 0.0
            ? -raw_theory * cfg.PRF / (2.0 * M_PI)
            : std::numeric_limits<double>::quiet_NaN();
    const gmti::ctdr::PhaseCenterPose phase_pose =
        configuredCtdrPhaseCenterPose(cfg);
    double pose_baseline_along_m = std::numeric_limits<double>::quiet_NaN();
    double pose_baseline_right_m = std::numeric_limits<double>::quiet_NaN();
    double pose_baseline_up_m = std::numeric_limits<double>::quiet_NaN();
    const bool pose_valid = gmti::ctdr::rotatedBaselineLocal(
        phase_pose, pose_baseline_along_m, pose_baseline_right_m,
        pose_baseline_up_m);

    auto jsonNumber = [](double value) -> std::string {
        if (!std::isfinite(value)) return "null";
        std::ostringstream os;
        os << std::setprecision(15) << value;
        return os.str();
    };
    std::ostringstream stem;
    stem << "p38_alignment_beam" << std::setw(3) << std::setfill('0') << beam_id
         << ".json";
    std::ofstream os(joinPathLocal(cfg.result_add, stem.str()).c_str());
    if (!os) return;
    os << "{\n"
       << "  \"meaning\": \"CSI integer-PRT preprocessing; not the raw P38 model\",\n"
       << "  \"beam_id\": " << beam_id << ",\n"
       << "  \"alignment_mode\": \"" << cfg.csi_channel_alignment_mode << "\",\n"
       << "  \"rx_phase_center_spacing_m\": " << jsonNumber(cfg.d_channel) << ",\n"
       << "  \"phase_center_pose_model\": \""
       << (pose_valid ? "mechanical_rotating_receive_centres"
                      : "velocity_aligned_scalar_baseline") << "\",\n"
       << "  \"servo_azimuth_deg\": "
       << jsonNumber(pose_valid ? phase_pose.servo_azimuth_deg
                                : std::numeric_limits<double>::quiet_NaN()) << ",\n"
       << "  \"physical_baseline_norm_m\": "
       << jsonNumber(pose_valid
                          ? std::hypot(std::hypot(pose_baseline_along_m,
                                                   pose_baseline_right_m),
                                       pose_baseline_up_m)
                          : cfg.d_channel) << ",\n"
       << "  \"baseline_along_m\": " << jsonNumber(pose_baseline_along_m) << ",\n"
       << "  \"baseline_right_m\": " << jsonNumber(pose_baseline_right_m) << ",\n"
       << "  \"baseline_up_m\": " << jsonNumber(pose_baseline_up_m) << ",\n"
       << "  \"equivalent_two_way_phase_center_spacing_m\": "
       << jsonNumber(equivalent_two_way_spacing_m) << ",\n"
       << "  \"platform_speed_mps\": " << jsonNumber(platform_speed_mps) << ",\n"
       << "  \"prf_hz\": " << jsonNumber(cfg.PRF) << ",\n"
       << "  \"equivalent_two_way_delay_s\": "
       << jsonNumber(equivalent_two_way_delay_s) << ",\n"
       << "  \"continuous_alignment_prt\": " << jsonNumber(continuous_shift_prt) << ",\n"
       << "  \"integer_alignment_prt\": " << aligned_prt_shift << ",\n"
       << "  \"raw_p38_theory_k_rad_per_hz\": " << jsonNumber(raw_theory) << ",\n"
       << "  \"aligned_residual_theory_k_rad_per_hz\": " << jsonNumber(aligned_theory) << ",\n"
       << "  \"aligned_observed_k_rad_per_hz\": " << jsonNumber(aligned_fit_double[0]) << ",\n"
       << "  \"aligned_observed_b_rad\": " << jsonNumber(aligned_fit_double[1]) << ",\n"
       << "  \"aligned_fit_rmse_rad\": " << jsonNumber(metrics.rmse) << ",\n"
       << "  \"aligned_fit_sample_count\": " << metrics.sample_count << ",\n"
       << "  \"aligned_fit_inlier_ratio\": " << jsonNumber(metrics.inlier_ratio) << ",\n"
       << "  \"aligned_fit_valid\": " << (metrics.valid ? "true" : "false") << "\n"
       << "}\n";
}

std::string parentDirLocal(const std::string &path)
{
    const size_t pos = path.find_last_of("/\\");
    return pos == std::string::npos ? std::string() : path.substr(0, pos);
}

std::string inferSceneTruthPath(const Config &cfg)
{
    if (!cfg.pc_peak_scene_truth.empty()) {
        return cfg.pc_peak_scene_truth;
    }
    const std::string out_parent = parentDirLocal(cfg.result_add);
    return joinPathLocal(joinPathLocal(out_parent, "truth"), "scene_truth.csv");
}

void convertFloatComplexToDouble(const std::vector<std::complex<float>> &src,
                                 std::vector<std::complex<double>> &dst)
{
    dst.resize(src.size());
    for (size_t i = 0; i < src.size(); ++i) {
        dst[i] = std::complex<double>(src[i].real(), src[i].imag());
    }
}

std::vector<std::string> splitCsvLine(const std::string &line)
{
    std::vector<std::string> out;
    std::string cur;
    bool quoted = false;
    for (size_t i = 0; i < line.size(); ++i) {
        const char ch = line[i];
        if (ch == '"') {
            if (quoted && i + 1 < line.size() && line[i + 1] == '"') {
                cur.push_back('"');
                ++i;
            } else {
                quoted = !quoted;
            }
        } else if (ch == ',' && !quoted) {
            out.push_back(cur);
            cur.clear();
        } else {
            cur.push_back(ch);
        }
    }
    out.push_back(cur);
    return out;
}

double toDoubleLocal(const std::string &s, double fallback = std::numeric_limits<double>::quiet_NaN())
{
    if (s.empty()) return fallback;
    char *end = nullptr;
    const double v = std::strtod(s.c_str(), &end);
    return end && *end == '\0' ? v : fallback;
}

int toIntLocal(const std::string &s, int fallback = -1)
{
    const double v = toDoubleLocal(s);
    return std::isfinite(v) ? static_cast<int>(std::lround(v)) : fallback;
}

struct PcPeakTruth {
    bool ok = false;
    double range_m = std::numeric_limits<double>::quiet_NaN();
    double range_sample_float = std::numeric_limits<double>::quiet_NaN();
    int range_sample_int = -1;
    int beam_id = -1;
    double theta_cmd_deg = std::numeric_limits<double>::quiet_NaN();
};

PcPeakTruth loadPcPeakTruth(const Config &cfg)
{
    PcPeakTruth truth;
    const std::string path = inferSceneTruthPath(cfg);
    std::ifstream in(path.c_str());
    if (!in) {
        return truth;
    }
    std::string line;
    if (!std::getline(in, line)) {
        return truth;
    }
    const std::vector<std::string> header = splitCsvLine(line);
    std::map<std::string, size_t> col;
    for (size_t i = 0; i < header.size(); ++i) col[header[i]] = i;
    auto get = [&](const std::vector<std::string> &cells, const std::string &name) -> std::string {
        const auto it = col.find(name);
        return (it == col.end() || it->second >= cells.size()) ? std::string() : cells[it->second];
    };

    std::vector<std::string> best;
    double best_score = -1.0e300;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        const std::vector<std::string> cells = splitCsvLine(line);
        const std::string type = get(cells, "type");
        double score = 0.0;
        if (type == "single_point") score += 1.0e9;
        const double amp = toDoubleLocal(get(cells, "amplitude"), 0.0);
        const double rcs = toDoubleLocal(get(cells, "rcs_db"), -999.0);
        score += amp * 1.0e6 + rcs;
        if (score > best_score) {
            best_score = score;
            best = cells;
        }
    }
    if (best.empty()) {
        return truth;
    }
    truth.range_m = toDoubleLocal(get(best, "range_m"));
    if (!std::isfinite(truth.range_m)) {
        truth.range_m = toDoubleLocal(get(best, "initial_range_m"));
    }
    truth.range_sample_float = toDoubleLocal(get(best, "range_sample_float"));
    if (!std::isfinite(truth.range_sample_float) &&
        cfg.has_sample_delay_us && std::isfinite(truth.range_m) && cfg.fs > 0.0) {
        const double tau_abs_sec = 2.0 * truth.range_m / C;
        const double tau_rel_sec = tau_abs_sec - cfg.sample_delay_us * 1.0e-6;
        truth.range_sample_float = tau_rel_sec * cfg.fs;
    }
    truth.range_sample_int = toIntLocal(get(best, "range_sample_int"), -1);
    if (truth.range_sample_int < 0 && std::isfinite(truth.range_sample_float)) {
        truth.range_sample_int = static_cast<int>(std::lround(truth.range_sample_float));
    }
    truth.beam_id = toIntLocal(get(best, "beam_id_0based"), -1);
    if (truth.beam_id < 0) {
        truth.beam_id = toIntLocal(get(best, "beam_id"), -1);
    }
    truth.theta_cmd_deg = toDoubleLocal(get(best, "theta_cmd_deg"));
    if (!std::isfinite(truth.theta_cmd_deg)) {
        truth.theta_cmd_deg = toDoubleLocal(get(best, "initial_azimuth_deg"));
    }
    truth.ok = std::isfinite(truth.range_m) && std::isfinite(truth.range_sample_float);
    return truth;
}

void writePcPeakDebug(const Config &cfg,
                      int beamIdx,
                      const std::vector<std::complex<float>> &data1,
                      const std::vector<std::complex<float>> &data2)
{
    static std::mutex pc_peak_mutex;
    if (!cfg.debug_pc_peak || data1.empty() || data2.empty()) {
        return;
    }
    std::lock_guard<std::mutex> lock(pc_peak_mutex);
    const int Na = effectivePulseNum(cfg);
    const int Nr = cfg.rg_len;
    if (Na <= 0 || Nr <= 0 ||
        data1.size() < static_cast<size_t>(Na) * static_cast<size_t>(Nr) ||
        data2.size() < static_cast<size_t>(Na) * static_cast<size_t>(Nr)) {
        return;
    }
    const PcPeakTruth truth = loadPcPeakTruth(cfg);
    const int expected = truth.ok ? (truth.range_sample_int - cfg.range_crop_start) : -1;
    const int left = expected >= 0 ? std::max(0, expected - 50) : -1;
    const int right = expected >= 0 ? std::min(Nr - 1, expected + 50) : -1;

    std::vector<double> power(static_cast<size_t>(Nr), 0.0);
    for (int c = 0; c < Nr; ++c) {
        double acc = 0.0;
        for (int r = 0; r < Na; ++r) {
            const size_t off = static_cast<size_t>(r) * static_cast<size_t>(Nr) + static_cast<size_t>(c);
            acc += 0.5 * (std::norm(data1[off]) + std::norm(data2[off]));
        }
        power[static_cast<size_t>(c)] = acc / static_cast<double>(Na);
    }

    auto maxInRange = [&](int l, int r) {
        int idx = -1;
        double val = -1.0;
        if (l <= r && l >= 0 && r < Nr) {
            for (int c = l; c <= r; ++c) {
                if (power[static_cast<size_t>(c)] > val) {
                    val = power[static_cast<size_t>(c)];
                    idx = c;
                }
            }
        }
        return std::pair<int, double>(idx, val);
    };
    const auto local_peak = maxInRange(left, right);
    const auto global_peak = maxInRange(0, Nr - 1);
    const bool valid = truth.ok && expected >= 0 && expected < Nr &&
                       local_peak.first >= 0 && local_peak.second > 0.0;

    const std::string debug_dir = joinPathLocal(cfg.result_add, "debug");
    if (!ensureDirLocal(debug_dir)) {
        return;
    }

    {
        char name[128];
        std::snprintf(name, sizeof(name), "pc_range_profile_beam%02d.csv", beamIdx);
        std::ofstream prof(joinPathLocal(debug_dir, name).c_str());
        prof << "range_bin,range_m,power,in_expected_window\n";
        for (int c = 0; c < Nr; ++c) {
            const double range_m = (c >= 0 && c < static_cast<int>(cfg.Rg.size()))
                ? cfg.Rg[static_cast<size_t>(c)]
                : cfg.R_min + static_cast<double>(c) * cfg.R_bin;
            prof << c << "," << std::setprecision(15) << range_m << ","
                 << power[static_cast<size_t>(c)] << ","
                 << ((left >= 0 && c >= left && c <= right) ? 1 : 0) << "\n";
        }
    }

    const std::string check_path = joinPathLocal(debug_dir, "pc_peak_check.csv");
    const bool need_header = !static_cast<bool>(std::ifstream(check_path.c_str()));
    std::ofstream os(check_path.c_str(), std::ios::app);
    if (!os) return;
    if (need_header) {
        os << "case_id,run_id,period_id,beam_id,"
              "truth_range_m,truth_range_sample_float,truth_range_sample_int,"
              "pc_crop_start,expected_pc_bin_without_offset,"
              "search_win_left,search_win_right,"
              "pc_peak_bin,pc_peak_range_m,pc_peak_power,"
              "pc_peak_offset_bin,pc_peak_range_error_m,"
              "global_peak_bin,global_peak_range_m,global_peak_power,"
              "valid\n";
    }
    auto rangeAt = [&](int bin) -> double {
        if (bin >= 0 && bin < static_cast<int>(cfg.Rg.size())) return cfg.Rg[static_cast<size_t>(bin)];
        return std::numeric_limits<double>::quiet_NaN();
    };
    const double pc_range = rangeAt(local_peak.first);
    const double global_range = rangeAt(global_peak.first);
    os << gmti::runtime::caseId() << ","
       << gmti::runtime::runId() << ","
       << 0 << ","
       << beamIdx << ","
       << std::setprecision(15)
       << truth.range_m << ","
       << truth.range_sample_float << ","
       << truth.range_sample_int << ","
       << cfg.range_crop_start << ","
       << expected << ","
       << left << ","
       << right << ","
       << local_peak.first << ","
       << pc_range << ","
       << local_peak.second << ","
       << (local_peak.first >= 0 && expected >= 0 ? local_peak.first - expected : 0) << ","
       << (std::isfinite(pc_range) && std::isfinite(truth.range_m) ? pc_range - truth.range_m : std::numeric_limits<double>::quiet_NaN()) << ","
       << global_peak.first << ","
       << global_range << ","
       << global_peak.second << ","
       << (valid ? 1 : 0) << "\n";
}

bool pcProfileEnvEnabled()
{
    const char *v = std::getenv("GMTI_DEBUG_PC_PROFILE");
    return v && std::string(v) == "1";
}

bool pcProfile2dEnvEnabled()
{
    const char *v = std::getenv("GMTI_PC_DUMP_2D");
    return v && std::string(v) == "1";
}

bool pcProfileBeamEnabled(int beamIdx)
{
    const char *v = std::getenv("GMTI_PC_DUMP_BEAMS");
    if (!v || !*v) return true;
    std::stringstream ss(v);
    std::string item;
    while (std::getline(ss, item, ',')) {
        if (toIntLocal(item, -1) == beamIdx) return true;
    }
    return false;
}

int pcProfileDopplerRow(int beamIdx)
{
    const char *v = std::getenv("GMTI_PC_DUMP_ROWS");
    if (!v || !*v) return -1;
    std::stringstream ss(v);
    std::string item;
    while (std::getline(ss, item, ',')) {
        const std::size_t pos = item.find(':');
        if (pos == std::string::npos) continue;
        if (toIntLocal(item.substr(0, pos), -1) == beamIdx) {
            return toIntLocal(item.substr(pos + 1), -1);
        }
    }
    return -1;
}

void writeManualPcProfile(const Config &cfg,
                          int beamIdx,
                          const std::vector<std::complex<float>> &data1,
                          const std::vector<std::complex<float>> &data2)
{
    if (!cfg.runtime_diagnostics_enabled || !pcProfileEnvEnabled() ||
        !pcProfileBeamEnabled(beamIdx) || data1.empty()) return;
    const int Na = effectivePulseNum(cfg);
    const int Nr = cfg.rg_len;
    const bool has_ch2 = data2.size() >= static_cast<size_t>(Na) * static_cast<size_t>(Nr);
    if (Na <= 0 || Nr <= 0 ||
        data1.size() < static_cast<size_t>(Na) * static_cast<size_t>(Nr)) {
        return;
    }
    const std::string debug_dir = joinPathLocal(cfg.result_add, "debug");
    if (!ensureDirLocal(debug_dir)) return;

    auto rangeAt = [&](int bin) {
        if (bin >= 0 && bin < static_cast<int>(cfg.Rg.size())) return cfg.Rg[static_cast<size_t>(bin)];
        return cfg.R_min + static_cast<double>(bin) * cfg.R_bin;
    };

    std::vector<double> power(static_cast<size_t>(Nr), 0.0);
    const int doppler_row_raw = pcProfileDopplerRow(beamIdx);
    const int doppler_row = doppler_row_raw >= 0
        ? ((doppler_row_raw % Na) + Na) % Na
        : -1;
    const bool coherent_doppler_profile = doppler_row >= 0 && doppler_row < Na;
    int peak_bin = 0;
    double peak_power = -1.0;
    for (int c = 0; c < Nr; ++c) {
        double acc = 0.0;
        if (coherent_doppler_profile) {
            std::complex<double> sum1(0.0, 0.0), sum2(0.0, 0.0);
            const double normalized_frequency =
                static_cast<double>(doppler_row - Na / 2) / static_cast<double>(Na);
            for (int p = 0; p < Na; ++p) {
                const size_t off = static_cast<size_t>(p) * static_cast<size_t>(Nr) + static_cast<size_t>(c);
                // Production Doppler processing uses the conjugate sign relative
                // to the diagnostic DFT convention here; use the matching sign
                // so the truth row coherently focuses instead of suppressing it.
                const double phase = 2.0 * M_PI * normalized_frequency * static_cast<double>(p);
                const std::complex<double> rot(std::cos(phase), std::sin(phase));
                sum1 += static_cast<std::complex<double>>(data1[off]) * rot;
                if (has_ch2) sum2 += static_cast<std::complex<double>>(data2[off]) * rot;
            }
            acc = has_ch2 ? 0.5 * (std::norm(sum1) + std::norm(sum2)) : std::norm(sum1);
            acc /= static_cast<double>(Na) * static_cast<double>(Na);
        } else {
            for (int p = 0; p < Na; ++p) {
                const size_t off = static_cast<size_t>(p) * static_cast<size_t>(Nr) + static_cast<size_t>(c);
                const double p1 = std::norm(data1[off]);
                acc += has_ch2 ? 0.5 * (p1 + std::norm(data2[off])) : p1;
            }
            acc /= static_cast<double>(Na);
        }
        power[static_cast<size_t>(c)] = acc;
        if (power[static_cast<size_t>(c)] > peak_power) {
            peak_power = power[static_cast<size_t>(c)];
            peak_bin = c;
        }
    }

    char name[128];
    std::snprintf(name, sizeof(name), "pc_range_profile_beam%02d.csv", beamIdx);
    const std::string profile_path = joinPathLocal(debug_dir, name);
    std::ofstream prof(profile_path.c_str());
    prof << "bin,range_m,power,power_db,doppler_row,profile_mode\n";
    for (int c = 0; c < Nr; ++c) {
        const double pwr = power[static_cast<size_t>(c)];
        prof << c << "," << std::setprecision(15) << rangeAt(c) << ","
             << pwr << "," << 10.0 * std::log10(pwr + 1.0e-30) << ","
             << doppler_row << ","
             << (coherent_doppler_profile ? "coherent_doppler_row" : "incoherent_pulse_mean")
             << "\n";
    }

    if (pcProfile2dEnvEnabled()) {
        std::snprintf(name, sizeof(name), "pc_amplitude_2d_beam%02d.csv", beamIdx);
        std::ofstream out2d(joinPathLocal(debug_dir, name).c_str());
        out2d << "pulse_idx,bin,range_m,amp_ch1,amp_ch2,amp_mean,power_mean,power_db\n";
        for (int p = 0; p < Na; ++p) {
            for (int c = 0; c < Nr; ++c) {
                const size_t off = static_cast<size_t>(p) * static_cast<size_t>(Nr) + static_cast<size_t>(c);
                const double a1 = std::abs(data1[off]);
                if (has_ch2) {
                    const double a2 = std::abs(data2[off]);
                    const double pm = 0.5 * (a1 * a1 + a2 * a2);
                    out2d << p << "," << c << "," << std::setprecision(15) << rangeAt(c) << ","
                          << a1 << "," << a2 << "," << 0.5 * (a1 + a2) << ","
                          << pm << "," << 10.0 * std::log10(pm + 1.0e-30) << "\n";
                } else {
                    const double pm = a1 * a1;
                    out2d << p << "," << c << "," << std::setprecision(15) << rangeAt(c) << ","
                          << a1 << ",," << a1 << "," << pm << ","
                          << 10.0 * std::log10(pm + 1.0e-30) << "\n";
                }
            }
        }
    }

    const double peak_db = 10.0 * std::log10(peak_power + 1.0e-30);
    std::cout << "[PC_PROFILE] wrote " << profile_path
              << ", beam=" << beamIdx
              << ", Na=" << Na
              << ", Nr=" << Nr
              << ", peak_bin=" << peak_bin
              << ", peak_range_m=" << rangeAt(peak_bin)
              << ", peak_power_db=" << peak_db << std::endl;
}

void writeDetectionRangeProfile(const Config &cfg,
                                int beamIdx,
                                const std::vector<float> &power_map)
{
    if (!cfg.runtime_diagnostics_enabled || cfg.result_add.empty()) return;
    const int Na = effectivePulseNum(cfg);
    const int Nr = cfg.rg_len;
    const int raw_row = pcProfileDopplerRow(beamIdx);
    if (Na <= 0 || Nr <= 0 || raw_row < 0 ||
        power_map.size() != static_cast<size_t>(Na) * static_cast<size_t>(Nr)) return;
    const int row = ((raw_row % Na) + Na) % Na;
    const std::string debug_dir = joinPathLocal(cfg.result_add, "debug");
    if (!ensureDirLocal(debug_dir)) return;
    char name[128];
    std::snprintf(name, sizeof(name), "detection_range_profile_beam%02d.csv", beamIdx);
    std::ofstream out(joinPathLocal(debug_dir, name).c_str());
    if (!out) return;
    out << "bin,range_m,power,power_db,doppler_row,profile_mode\n";
    const size_t off = static_cast<size_t>(row) * static_cast<size_t>(Nr);
    for (int c = 0; c < Nr; ++c) {
        const double pwr = static_cast<double>(power_map[off + static_cast<size_t>(c)]);
        const double range_m = c < static_cast<int>(cfg.Rg.size())
            ? cfg.Rg[static_cast<size_t>(c)]
            : cfg.R_min + static_cast<double>(c) * cfg.R_bin;
        out << c << ',' << std::setprecision(15) << range_m << ',' << pwr << ','
            << 10.0 * std::log10(pwr + 1.0e-30) << ',' << row
            << ",production_cfar_power\n";
    }
}

void writeDetectionComplexRangeProfile(
    const Config &cfg,
    int beamIdx,
    const std::vector<std::complex<float>> &detect_map)
{
    if (!cfg.runtime_diagnostics_enabled || cfg.result_add.empty()) return;
    const int Na = effectivePulseNum(cfg);
    const int Nr = cfg.rg_len;
    const int raw_row = pcProfileDopplerRow(beamIdx);
    if (Na <= 0 || Nr <= 0 || raw_row < 0 ||
        detect_map.size() != static_cast<size_t>(Na) * static_cast<size_t>(Nr)) return;
    const int row = ((raw_row % Na) + Na) % Na;
    const std::string debug_dir = joinPathLocal(cfg.result_add, "debug");
    if (!ensureDirLocal(debug_dir)) return;
    char name[128];
    std::snprintf(name, sizeof(name), "detection_complex_profile_beam%02d.csv", beamIdx);
    std::ofstream out(joinPathLocal(debug_dir, name).c_str());
    if (!out) return;
    out << "bin,range_m,real,imag,doppler_row,profile_mode\n";
    const size_t off = static_cast<size_t>(row) * static_cast<size_t>(Nr);
    for (int c = 0; c < Nr; ++c) {
        const auto value = detect_map[off + static_cast<size_t>(c)];
        const double range_m = c < static_cast<int>(cfg.Rg.size())
            ? cfg.Rg[static_cast<size_t>(c)]
            : cfg.R_min + static_cast<double>(c) * cfg.R_bin;
        out << c << ',' << std::setprecision(15) << range_m << ','
            << value.real() << ',' << value.imag() << ',' << row
            << ",production_cfar_complex\n";
    }
}

} // namespace
// 返回同样编号：1=SW,2=NE,3=NW,4=SE,0=未知
int flight_flag_by_sign(double Vx, double Vy, double eps = 1e-6)
{
    int sE = sgn(Vx, eps), sN = sgn(Vy, eps);
    if (sE == 0 && sN == 0)
        return 0;
    if (sN < 0 && sE < 0)
        return 1; // SW
    if (sN > 0 && sE > 0)
        return 2; // NE
    if (sN > 0 && sE < 0)
        return 3; // NW
    if (sN < 0 && sE > 0)
        return 4; // SE
    // 轴上用另一分量决定
    if (sE == 0)
        return (sN > 0) ? 3 : 4; // 正北→NW，正南→SE
    if (sN == 0)
        return (sE > 0) ? 2 : 1; // 正东→NE，正西→SW
    return 0;
}

static void compare_cpu_gpu_vector(const std::vector<std::complex<double>> &cpu,
                                   const std::vector<std::complex<double>> &gpu,
                                   const std::string &tag,
                                   double tol = 1e-8)
{
    if (cpu.size() != gpu.size()) {
        std::cerr << "[TEST] " << tag << " size mismatch: " << cpu.size() << " vs " << gpu.size() << "\n";
        return;
    }

    double maxAbs = 0.0;
    double maxRel = 0.0;
    size_t maxIdx = 0;
    for (size_t i = 0; i < cpu.size(); ++i) {
        std::complex<double> diff = cpu[i] - gpu[i];
        double absErr = std::abs(diff);
        double relErr = (std::abs(cpu[i]) > 1e-16) ? absErr / std::abs(cpu[i]) : absErr;
        if (absErr > maxAbs) {
            maxAbs = absErr;
            maxRel = relErr;
            maxIdx = i;
        }
    }

    std::cout << "[TEST] " << tag << " -> maxAbs=" << maxAbs << ", maxRel=" << maxRel
              << ", maxIdx=" << maxIdx << "\n";

    if (maxAbs > tol) {
        std::cout << "[TEST] " << tag << " FAILED at idx=" << maxIdx << ", cpu=" << cpu[maxIdx]
                  << ", gpu=" << gpu[maxIdx] << "\n";
    } else {
        std::cout << "[TEST] " << tag << " PASSED (tol=" << tol << ")\n";
    }
}

namespace {

static inline double normalize_azimuth_deg(double angle_deg)
{
    angle_deg = std::fmod(angle_deg, 360.0);
    if (angle_deg < 0.0)
        angle_deg += 360.0;
    return angle_deg;
}

static inline double wrap180_deg(double angle_deg)
{
    angle_deg = std::fmod(angle_deg + 180.0, 360.0);
    if (angle_deg < 0.0)
        angle_deg += 360.0;
    return angle_deg - 180.0;
}

static inline double beam_center_relative_dir_deg(int squint_side, double theta_deg)
{
    const double side_dir = (squint_side == 1) ? -90.0 : 90.0;
    return wrap180_deg(side_dir - theta_deg);
}

static inline double clamp_unit_local(double v)
{
    if (!std::isfinite(v)) {
        return v;
    }
    return std::max(-1.0, std::min(1.0, v));
}

static inline double theta_from_sinA_deg_local(double sinA)
{
    if (!std::isfinite(sinA)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    return -gmti::trig_lut::asin(clamp_unit_local(sinA)) * 180.0 / M_PI;
}

static inline void project_position_from_sinA_local(const GMTIOutput::Plane &plane,
                                                    const Config &cfg,
                                                    double range_m,
                                                    double sinA,
                                                    double &theta_used_deg,
                                                    double &look_e,
                                                    double &look_n,
                                                    double &e,
                                                    double &n)
{
    if (!std::isfinite(sinA) || !std::isfinite(range_m) || !(range_m > 0.0)) {
        look_e = std::numeric_limits<double>::quiet_NaN();
        look_n = std::numeric_limits<double>::quiet_NaN();
        e = std::numeric_limits<double>::quiet_NaN();
        n = std::numeric_limits<double>::quiet_NaN();
        theta_used_deg = std::numeric_limits<double>::quiet_NaN();
        return;
    }

    const double vE = plane.V * gmti::trig_lut::cos(plane.V_angle * M_PI / 180.0);
    const double vN = plane.V * gmti::trig_lut::sin(plane.V_angle * M_PI / 180.0);
    const gmti::sim_geometry::LookVectorEN look =
        gmti::sim_geometry::computeLookFromSinA(sinA, vE, vN, cfg.squint_side);
    look_e = look.east;
    look_n = look.north;
    theta_used_deg = gmti::trig_lut::atan2(look_n, look_e) * 180.0 / M_PI;
    e = plane.E + range_m * look_e;
    n = plane.N + range_m * look_n;
}

static inline double location_beam_gate_deg(const Config &cfg)
{
    if (cfg.loc_beam_gate_deg > 0.0) {
        return cfg.loc_beam_gate_deg;
    }
    return std::max(0.0, cfg.beamwidth_deg * 0.5) + 0.5;
}

static inline bool applyDopplerCenterTheoryGuard(const Config &cfg,
                                                 const GMTIOutput::Plane &plane,
                                                 double theta_cmd_deg,
                                                 double &fd_ctr)
{
    if (!cfg.doppler_center_theory_guard_enable ||
        !(cfg.PRF > 0.0) || !(plane.V > 0.0) || !std::isfinite(fd_ctr)) {
        return false;
    }
    const double lambda = (cfg.lambda > 0.0)
        ? cfg.lambda : ((cfg.fc > 0.0) ? C / cfg.fc : 0.0);
    if (!(lambda > 0.0) || !std::isfinite(theta_cmd_deg)) {
        return false;
    }
    const double theta_rad = theta_cmd_deg * M_PI / 180.0;
    const double fd_theory_unwrapped =
        -2.0 * plane.V * gmti::trig_lut::sin(theta_rad) / lambda;
    const double fd_theory_wrapped = std::remainder(fd_theory_unwrapped, cfg.PRF);
    const double error_hz = std::abs(std::remainder(
        fd_ctr - fd_theory_wrapped, cfg.PRF));
    const double beam_half_rad =
        0.5 * std::max(0.0, cfg.beamwidth_deg) * M_PI / 180.0;
    const double derived_limit_hz =
        2.0 * plane.V * std::abs(gmti::trig_lut::sin(beam_half_rad)) / lambda +
        2.0 * std::max(0.0, cfg.fd_res);
    const double limit_hz = (cfg.doppler_center_theory_max_error_hz > 0.0)
        ? cfg.doppler_center_theory_max_error_hz
        : derived_limit_hz;
    if (!(error_hz > limit_hz)) {
        return false;
    }
    const double fd_observed = fd_ctr;
    fd_ctr = fd_theory_wrapped;
    if (cfg.runtime_diagnostics_enabled) {
        std::cout << "[fd-center][theory-guard] observed_hz=" << fd_observed
                  << " theory_wrapped_hz=" << fd_theory_wrapped
                  << " theory_unwrapped_hz=" << fd_theory_unwrapped
                  << " sin_theta=" << gmti::trig_lut::sin(theta_rad)
                  << " speed_mps=" << plane.V
                  << " lambda_m=" << lambda
                  << " prf_hz=" << cfg.PRF
                  << " circular_error_hz=" << error_hz
                  << " limit_hz=" << limit_hz
                  << " theta_cmd_deg=" << theta_cmd_deg
                  << " action=use_theory" << std::endl;
    }
    return true;
}

static inline bool applyDopplerCenterOverride(const Config &cfg, double &fd_ctr)
{
    if (!std::isfinite(cfg.doppler_center_override_hz)) return false;
    const double observed = fd_ctr;
    fd_ctr = cfg.doppler_center_override_hz;
    if (cfg.runtime_diagnostics_enabled) {
        std::cout << "[fd-center][paired-override] observed_hz=" << observed
                  << " override_hz=" << fd_ctr << std::endl;
    }
    return true;
}

} // namespace

double GMTIProcessor::estimateSquintAngleDeg(const GMTIOutput::Plane &plane, const Config &cfg, double fd_ctr)
{
    const double lambda = (cfg.lambda > 0.0) ? cfg.lambda : (C / cfg.fc);
    const double v = plane.V;

    if (std::isfinite(fd_ctr))
    {
        if (v <= 0.0 || lambda <= 0.0)
        {
            std::cout << "[SQUINT] fd_ctr=" << fd_ctr
                      << ", v=" << v
                      << ", lambda=" << lambda
                      << ", squint=0 (invalid v/lambda)" << std::endl;
            return 0.0;
        }

        const double ratio = -fd_ctr * lambda / (2.0 * v);
        const double clipped = std::max(-1.0, std::min(1.0, ratio));
        const double squint_deg = gmti::trig_lut::asin(clipped) * 180.0 / M_PI;

        // std::cout << "[SQUINT] fd_ctr=" << fd_ctr
        //           << ", v=" << v
        //           << ", lambda=" << lambda
        //           << ", ratio=" << ratio
        //           << ", squint=" << squint_deg
        //           << std::endl;
        return squint_deg;
    }

    double refE = 0.0;
    double refN = 0.0;
    Gaussp3(cfg.roi_ll_deg[0], cfg.roi_ll_deg[1], cfg.L0, refE, refN);
    const double dE = refE - plane.E;
    const double dN = refN - plane.N;
    const double bearing_deg = gmti::trig_lut::atan2(dN, dE) * 180.0 / M_PI;
    const double squint_deg = wrap180_deg(bearing_deg - plane.V_angle + 90.0);
    
    // std::cout << "[SQUINT] fd_ctr=nan"
    //           << ", v=" << v
    //           << ", lambda=" << lambda
    //           << ", refE=" << refE
    //           << ", refN=" << refN
    //           << ", planeE=" << plane.E
    //           << ", planeN=" << plane.N
    //           << ", bearing=" << bearing_deg
    //           << ", V_angle=" << plane.V_angle
    //           << ", squint=" << squint_deg
    //           << std::endl;
    
    return squint_deg;
}

double GMTIProcessor::estimateSquintAngleDeg(const std::vector<std::complex<float>> &data,
                                             const GMTIOutput::Plane &plane,
                                             const Config &cfg)
{
    double fd_ctr = 0.0;
    int start_pulse = 0;
    int window_pulses = 0;

    if (estimateCenterFdCtrFromData(data, cfg, fd_ctr, start_pulse, window_pulses))
    {
        // std::cout << "[SQUINT] center-window start=" << start_pulse
        //           << ", count=" << window_pulses
        //           << ", fd_ctr=" << fd_ctr
        //           << std::endl;
        return estimateSquintAngleDeg(plane, cfg, fd_ctr);
    }

    // std::cout << "[SQUINT] center-window fd_ctr estimate failed, fallback to geometric estimate" << std::endl;
    return estimateSquintAngleDeg(plane, cfg);
}

bool GMTIProcessor::pulseCompressionGpuResident(const std::vector<std::complex<float>> &data1,
                                                const std::vector<std::complex<float>> &data2,
                                                const Config &cfg)
{
    const int W = effectivePulseNum(cfg);
    if (W <= 0 || cfg.pulse_len <= 0 || cfg.rg_len <= 0) {
        return false;
    }
    if (data1.size() != static_cast<size_t>(W) * static_cast<size_t>(cfg.pulse_len) ||
        data2.size() != static_cast<size_t>(W) * static_cast<size_t>(cfg.pulse_len) ||
        gpu_ptrs_.d1 == nullptr || gpu_ptrs_.d2 == nullptr) {
        return false;
    }

    const size_t total_bytes = data1.size() * sizeof(std::complex<float>);
    if (cudaMemcpyAsync(gpu_ptrs_.d1, data1.data(), total_bytes, cudaMemcpyHostToDevice, stream_compute_) != cudaSuccess ||
        cudaMemcpyAsync(gpu_ptrs_.d2, data2.data(), total_bytes, cudaMemcpyHostToDevice, stream_compute_) != cudaSuccess) {
        return false;
    }

    return rangeCompressCUFFT_device_pair(cfg.pulse_len, cfg.rg_len, cfg);
}

bool GMTIProcessor::pulseCompressionGpuResident(const NewProtocolGpuInput &input,
                                                const Config &cfg)
{
    const int W = effectivePulseNum(cfg);
    if (W <= 0 || cfg.pulse_len <= 0 || cfg.rg_len <= 0 ||
        input.pulse_count != static_cast<std::size_t>(W) ||
        input.samples_per_prt != static_cast<std::size_t>(cfg.pulse_len)) {
        return false;
    }
    if (!cuda_decode_new_protocol_async(input, cfg)) {
        return false;
    }
    return rangeCompressCUFFT_device_pair(cfg.pulse_len, cfg.rg_len, cfg);
}

// 辅助函数：进行脉压处理（支持GPU加速 + CPU回退）
bool GMTIProcessor::pulseCompression(std::vector<std::complex<float>> &data1,
                                     std::vector<std::complex<float>> &data2,
                                     const Config &cfg)
{
    // TIMING_SCOPE(pulseCompression);
    // 对两个通道分别进行脉压
    const int W = effectivePulseNum(cfg);
    if (data1.size() != (size_t)W * cfg.pulse_len ||
        data2.size() != (size_t)W * cfg.pulse_len)
    {
        std::cerr << "输入数据尺寸不匹配脉冲数和脉冲长度。" << std::endl;
        return false;
    }

    // GPU 脉压使用 pulseCompressionGpuResident()，成功后不下载整幅矩阵。
    // 本函数只作为 CPU 回退路径使用。
    auto compress_one = [&](std::vector<std::complex<float>>& data) -> bool {
        std::vector<std::complex<double>> in_d(data.size());
        for (size_t i = 0; i < data.size(); ++i) {
            in_d[i] = std::complex<double>(data[i].real(), data[i].imag());
        }
        std::vector<std::complex<double>> out_d;
        if (!rangeCompressFFT(cfg, in_d, out_d)) {
            return false;
        }
        data.resize(out_d.size());
        for (size_t i = 0; i < out_d.size(); ++i) {
            data[i] = std::complex<float>(static_cast<float>(out_d[i].real()),
                                          static_cast<float>(out_d[i].imag()));
        }
        return true;
    };

    if (!compress_one(data1))
        return false;
    if (!compress_one(data2))
        return false;

    if (data1.size() != (size_t)W * cfg.rg_len ||
        data2.size() != (size_t)W * cfg.rg_len)
    {
        std::cerr << "输出数据尺寸不匹配脉冲数和距离长度。" << std::endl;
        return false;
    }

    DBG("[CPU] 脉压处理成功 (CPU FFTW)");
    return true;
}

// 辅助函数：计算多普勒频率轴
inline bool computeDoppler(const std::vector<std::complex<float>> &data, int k, const Config &cfg,
                           std::vector<double> &faAxis, double &fa2, const double &v, const double &theta_deg)
{
    // 获取数据的尺寸
    size_t Na = effectivePulseNum(cfg); // 方位向点数
    size_t Nr = cfg.rg_len;    // 距离向点数

    // 按距离门保留慢时间相邻相关，以便对强目标距离门做稳健截尾。
    std::vector<std::complex<double>> per_range_correlation(Nr, {0.0, 0.0});
    for (size_t mm = k; mm < Na; ++mm)
    {
        for (size_t nn = 0; nn < Nr; ++nn)
        {
            size_t index_mm = mm * Nr + nn;      // 计算一维数组的索引
            size_t index_k = (mm - k) * Nr + nn; // 偏移后的索引

            per_range_correlation[nn] +=
                static_cast<std::complex<double>>(data[index_mm]) *
                std::conj(static_cast<std::complex<double>>(data[index_k]));
        }
    }

    std::complex<double> R_m2 = {0.0, 0.0};
    if (cfg.doppler_center_robust_enable) {
        const gmti::doppler_center::RobustCenterResult robust =
            gmti::doppler_center::robustCorrelationSum(
                per_range_correlation,
                cfg.doppler_center_trim_top_fraction,
                static_cast<size_t>(cfg.doppler_center_min_valid_range_bins));
        if (!robust.valid) {
            return false;
        }
        R_m2 = robust.correlation_sum;
    } else {
        for (const auto &value : per_range_correlation) {
            R_m2 += value;
        }
    }

    // 计算多普勒频率
    fa2 = (cfg.PRF / (2 * M_PI)) * std::arg(R_m2); // 使用复数的相位（`arg`）

    // 生成频率轴 faAxis
    faAxis.clear();
    faAxis.resize(Na);
    const double df = (Na > 0) ? (cfg.PRF / static_cast<double>(Na)) : 0.0;
    for (size_t i = 0; i < Na; ++i)
    {
        faAxis[i] = -0.5 * cfg.PRF + static_cast<double>(i) * df;
    }

    // fa2 = unwrap_prf_to_model(fa2 , cfg.PRF, theta_deg, v, cfg.fc); // 解除模糊

    // 将 fa2 加到 faAxis 上
    for (size_t i = 0; i < faAxis.size(); ++i)
    {
        faAxis[i] += fa2;
    }

    return true; // 返回 true 表示成功
}


bool estimateCenterFdCtrFromData(const std::vector<std::complex<float>> &data,
                                        const Config &cfg,
                                        double &fd_ctr,
                                        int &start_pulse,
                                        int &window_pulses)
{
    fd_ctr = 0.0;
    start_pulse = 0;
    window_pulses = 0;

    const int W = effectivePulseNum(cfg);
    if (W < 2 || cfg.rg_len <= 0) {
        return false;
    }

    const size_t nr = static_cast<size_t>(cfg.rg_len);
    if (data.size() < static_cast<size_t>(W) * nr) {
        return false;
    }

    std::vector<double> faAxis;
    double fa_tmp = 0.0;
    if (!computeDoppler(data, 1, cfg, faAxis, fa_tmp, 0.0, 0.0)) {
        return false;
    }

    fd_ctr = fa_tmp;
    start_pulse = 0;
    window_pulses = W;
    return true;
}

// 处理一个周期的数据
bool GMTIProcessor::processOnePeriod(int periodIdx, const Config &cfg_, const std::vector<std::vector<double>> &posRaw, GMTIOutput &out)
{
    TIMING_SCOPE(processOnePeriod);
    Config cfg = cfg_; // 复制配置，局部修改
    const MechanicalCpiWindow *process_window = mechanicalWindow(cfg, periodIdx);
    const std::string timing_beam_extra = "beam_id=" + std::to_string(periodIdx);
    // 0) 读取脉冲块（适配双文件/交织）
    std::vector<std::complex<float>> data1, data2;
    NewProtocolGpuInput new_protocol_gpu_input;
    const bool use_packed_new_protocol_gpu_input =
        cfg.INFO_Type && !cfg.isPC && cfg.new_protocol_gpu_preprocess;
    std::vector<double> utc;
    std::vector<std::uint8_t> headers;
    std::vector<std::vector<double>> echoPosRaw;

    // 读取脉冲块
    double theta_sq = 0.0; // 方位角平方
    bool readSuccess = false;
    {
        gmti::runtime::TimingScope timing_data_read("data_read", 0, timing_beam_extra);
        if (cfg.INFO_Type) {
            if (use_packed_new_protocol_gpu_input) {
                readSuccess = readPulseBlockNewProtocolGpuInput(
                    cfg, periodIdx, new_protocol_gpu_input,
                    utc, theta_sq, echoPosRaw);
            } else {
                readSuccess = readPulseBlockNewProtocol(
                    cfg, periodIdx, data1, data2, utc, theta_sq, echoPosRaw);
            }
        } else {
            readSuccess = readPulseBlock(cfg, periodIdx, data1, data2, utc, theta_sq);
        }
    }
    if (!readSuccess)
    {
        std::cerr << "读取脉冲块失败。" << std::endl;
        return false;
    }
    if (process_window) {
        theta_sq = process_window->az_center_deg;
    }
    if (cfg.scan_mode == ScanMode::Mechanical && std::isfinite(theta_sq)) {
        // The PRT header/window angle is the physical servo pose for this
        // CPI.  Bind it before deriving P38/CTDR theory so a mechanical
        // receive baseline is not accidentally evaluated at servo zero.
        cfg.ctdr_servo_azimuth_deg = theta_sq;
    }
    if (cfg.INFO_Type) {
        cfg.process_pulse_num = static_cast<int>(utc.size());
    }
    DBG("读取脉冲块成功，脉冲数: " << utc.size());
    
    bool data_on_gpu = false;

    {
        gmti::runtime::TimingScope timing_pulse_compression("pulse_compression", 0, timing_beam_extra);
        // 如果没有脉压，则需要脉压
        if (!cfg.isPC)
        {
            // P1.5 debug needs a stable host-side matrix immediately after pulse compression.
            // Keep the normal GPU-resident path unchanged when debug_pc_peak is disabled.
            data_on_gpu = use_packed_new_protocol_gpu_input
                ? pulseCompressionGpuResident(new_protocol_gpu_input, cfg)
                : pulseCompressionGpuResident(data1, data2, cfg);
            bool pc_ok = data_on_gpu;
            if (!pc_ok) {
                if (use_packed_new_protocol_gpu_input &&
                    !decodeNewProtocolGpuInputCpu(
                        cfg, new_protocol_gpu_input, data1, data2)) {
                    std::cerr << "GPU 新协议解码失败，CPU 回退解码也失败。" << std::endl;
                    return false;
                }
                pc_ok = pulseCompression(data1, data2, cfg);
            }
            if (!pc_ok) {
                std::cerr << "脉压处理失败。" << std::endl;
                return false;
            }
            DBG(data_on_gpu ? "脉压处理成功，结果驻留GPU" : "脉压处理成功，结果位于CPU内存");
        }
        else
        {
            // 只进行抽取
            // 归一化 + 裁剪到 M，输出到 rc_out（W×M 行主）
            std::vector<std::complex<float>> rc_out;
            const int Lraw = cfg.pulse_len;
            const int W = effectivePulseNum(cfg);
            const int M1 = cfg.rg_len;
            const int Lraw2M = Lraw / M1;
            rc_out.resize((size_t)W * M1);
            for (int k = 0; k < W; ++k)
            {
                for (int m = 0; m < M1; ++m)
                {
                    rc_out[(size_t)k * M1 + m] = data1[(size_t)k * Lraw + m * Lraw2M];
                }
            }
            data1.swap(rc_out);

            rc_out.resize((size_t)W * M1);
            for (int k = 0; k < W; ++k)
            {
                for (int m = 0; m < M1; ++m)
                {
                    rc_out[(size_t)k * M1 + m] = data2[(size_t)k * Lraw + m * Lraw2M];
                }
            }
            data2.swap(rc_out);
            DBG("跳过脉压，直接抽取数据成功");
        }
    }

    // 脉压完成后再更新采样率，避免影响 rangeCompressFFT / cuFFT 的匹配滤波器构造
    if (!usesRangeCropWindow(cfg)) {
        cfg.fs = cfg.fs * cfg.rg_len / cfg.pulse_len;
    }
    cfg.pulse_len = cfg.rg_len; // 更新脉冲长度为距离采样长度
    // 通用方位向抽取：cfg.pulse_dec 合 1 抽取
    if (!data_on_gpu &&
        (data1.size() != (size_t)effectivePulseNum(cfg) * cfg.rg_len ||
         data2.size() != (size_t)effectivePulseNum(cfg) * cfg.rg_len))
    {
        ERR("数据尺寸不匹配脉冲数和距离长度，无法继续处理。");
        return false;
    }

    const int W_orig = effectivePulseNum(cfg);
    const int M = cfg.rg_len;
    const int dec = cfg.pulse_dec;
    if (dec <= 0)
    {
        ERR("cfg.pulse_dec 必须大于 0（当前 = " << dec << "）。");
        return false;
    }
    if (W_orig % dec != 0)
    {
        ERR("处理脉冲数不是" << dec << "的倍数，无法进行" << dec << "合 1 抽取（当前 process_pulse_num = " << W_orig << "）。");
        return false;
    }

    const int W_new = W_orig / dec;
    if (data_on_gpu)
    {
        if (!cuda_stage_az_decimate_async(W_orig, M, dec)) {
            ERR("GPU 方位向抽取失败。");
            return false;
        }
        data1.clear();
        data2.clear();
        data1.shrink_to_fit();
        data2.shrink_to_fit();
        if (W_new == W_orig) {
            DBG("cfg.pulse_dec == 1，GPU数据保持原方位长度。");
        } else {
            DBG("GPU 方位向" << dec << " 合 1 抽取完成：处理脉冲数 " << W_orig << " -> " << W_new);
        }
    }
    else if (W_new == W_orig)
    {
        DBG("cfg.pulse_dec == 1，跳过抽取。");
    }
    else
    {
        // 临时缓冲，复用（data1 先处理，后处理 data2）
        std::vector<std::complex<float>> tmp((size_t)W_new * M);

        // 处理 data1
        for (int k_new = 0; k_new < W_new; ++k_new)
        {
            size_t out_base = (size_t)k_new * M;
            size_t in_base = (size_t)(dec * k_new) * M;
            for (int m = 0; m < M; ++m)
            {
                std::complex<float> acc = 0.0f;
                for (int d = 0; d < dec; ++d)
                {
                    acc += data1[in_base + (size_t)d * M + m];
                }
                tmp[out_base + m] = acc;
            }
        }
        data1.swap(tmp);

        // 复用 tmp 处理 data2（重新分配确保大小正确）
        tmp.assign((size_t)W_new * M, std::complex<float>(0.0f, 0.0f));
        for (int k_new = 0; k_new < W_new; ++k_new)
        {
            size_t out_base = (size_t)k_new * M;
            size_t in_base = (size_t)(dec * k_new) * M;
            for (int m = 0; m < M; ++m)
            {
                std::complex<float> acc = 0.0f;
                for (int d = 0; d < dec; ++d)
                {
                    acc += data2[in_base + (size_t)d * M + m];
                }
                tmp[out_base + m] = acc;
            }
        }
        data2.swap(tmp);
    }

    // ========== 抽取 utc 同步 ==========
    if ((int)utc.size() != W_orig)
    {
        ERR("utc 长度" << utc.size() << " 与处理脉冲数(" << W_orig << ") 不匹配，无法同步抽取。");
        return false;
    }
    std::vector<double> utc_tmp(W_new);
    for (int k_new = 0; k_new < W_new; ++k_new)
    {
        // 平均法（推荐）
        double acc = 0.0;
        for (int d = 0; d < dec; ++d)
            acc += utc[dec * k_new + d];
        utc_tmp[k_new] = acc / (double)dec;

        // 若想使用取中值法（整数下标），可替换为：
        // utc_tmp[k_new] = utc[dec * k_new + dec/2];
    }
    utc.swap(utc_tmp);

    // 更新脉冲数
    cfg.process_pulse_num = W_new;
    cfg.PRF = cfg.PRF / dec; // 更新 PRF
    DBG("方位向" << dec << " 合 1 抽取完成：处理脉冲数 " << W_orig << " -> " << cfg.process_pulse_num << "，距离长度 M = " << M << "。");

    // 从帧头提取 UTC
    double utc_mean = 0.0;
    for (int i = 0; i < utc.size(); ++i)
    {
        utc_mean += utc[i];
    }
    utc_mean /= utc.size();

    for (int i = 0; i < utc.size(); ++i)
    {
        if (utc_mean - utc[i] > 0.6)
        {
            utc[i] += 1.0; // UTC 时间向前调整1秒
        }
        else if (utc_mean - utc[i] < -0.6)
        {
            utc[i] -= 1.0; // UTC 时间向后调整1秒
        }
    }

    out.utcMid = utc[utc.size() / 2];


    // 通道2 乘以系数，补偿幅度差异
    double coef = cfg.calib_coef; // 可根据实际情况调整
    if (data_on_gpu) {
        if (!cuda_scale_channel2_async(static_cast<float>(coef),
                                       static_cast<size_t>(effectivePulseNum(cfg)) * static_cast<size_t>(cfg.rg_len))) {
            ERR("GPU 通道2幅度校准失败。");
            return false;
        }
        if (pcProfileEnvEnabled() && pcProfileBeamEnabled(periodIdx)) {
            std::vector<std::complex<float>> pc1, pc2;
            const size_t total = static_cast<size_t>(effectivePulseNum(cfg)) * static_cast<size_t>(cfg.rg_len);
            pc1.resize(total);
            pc2.resize(total);
            cudaMemcpyAsync(pc1.data(), gpu_ptrs_.d1, total * sizeof(cd), cudaMemcpyDeviceToHost, stream_compute_);
            cudaMemcpyAsync(pc2.data(), gpu_ptrs_.d2, total * sizeof(cd), cudaMemcpyDeviceToHost, stream_compute_);
            cudaStreamSynchronize(stream_compute_);
            writeManualPcProfile(cfg, periodIdx, pc1, pc2);
        }
    } else {
        const size_t N = data2.size();
        for (size_t i = 0; i < N; ++i)
            data2[i] *= coef;
        writeManualPcProfile(cfg, periodIdx, data1, data2);
    }

    // 1) 飞机位姿/速度
    GMTIOutput::Plane plane;
    if (cfg.Loc)
    {
        const std::vector<std::vector<double>> &planePosSource = cfg.INFO_Type ? echoPosRaw : posRaw;
        bool planePosExtracted = extractPlanePos(utc, planePosSource, cfg, plane,
                                                 periodIdx);
        if (!planePosExtracted)
        {
            std::cerr << "无法提取飞机位姿。" << std::endl;
            return false;
        }
        DBG("飞机位置提取成功: E=" << plane.E << " N=" << plane.N << " H=" << plane.H << " V=" << plane.V);
    }
    else
    {
        plane.V = 40 / 3.6; // 设置默认速度
    }
    cfg.p38_expected_slope_rad_per_hz = p38TheorySlopeLocal(cfg, plane.V);

    // 2) 多普勒中心 & 动态支撑域
    double fa_ctr = 0.0;
    std::vector<double> faAxis;
    if (data_on_gpu) {
        if (!cuda_compute_fd_ctr_from_d1(1, cfg, fa_ctr)) {
            ERR("GPU 多普勒中心估计失败。");
            return false;
        }
        const size_t axisN = static_cast<size_t>(effectivePulseNum(cfg));
        faAxis.resize(axisN);
        const double df = (axisN > 0) ? (cfg.PRF / static_cast<double>(axisN)) : 0.0;
        for (size_t i = 0; i < axisN; ++i) {
            faAxis[i] = -0.5 * cfg.PRF + static_cast<double>(i) * df + fa_ctr;
        }
    } else if (!computeDoppler(data1, 1, cfg, faAxis, fa_ctr,
                               plane.V, theta_sq)) {
        return false;
    }
    if (!applyDopplerCenterOverride(cfg, fa_ctr)) {
        applyDopplerCenterTheoryGuard(cfg, plane, theta_sq, fa_ctr);
    }
    const double angle_deg = GMTIProcessor::estimateSquintAngleDeg(plane, cfg, fa_ctr);
    // Electronic files historically treat estimateSquintAngleDeg() as a
    // residual around the commanded beam.  A mechanical scan header already
    // carries the absolute platform-relative servo/look angle, so adding the
    // command a second time would double the angle and corrupt phase/alias
    // inversion.
    const double theta_deg = cfg.scan_mode == ScanMode::Mechanical
        ? angle_deg : angle_deg + theta_sq;
    const size_t axisN = faAxis.size();
    const double df = (axisN > 0) ? (cfg.PRF / static_cast<double>(axisN)) : 0.0;
    for (size_t i = 0; i < axisN; ++i) {
        faAxis[i] = -0.5 * cfg.PRF + static_cast<double>(i) * df + fa_ctr;
    }
    DBG("GMTI estimated residual angle_deg = " << angle_deg << " deg");
    DBG("多普勒中心计算成功: fa_ctr=" << fa_ctr);

    // 动态支撑域
    int az_center = 0, az_st = 0, az_ed = 0, rg_st = 0, rg_ed = 0;
    double fd_st = 0.0, fd_ed = 0.0, BW_az = 0.0;

    bool domainCalculated = computeDynamicSupportDomain(faAxis, fa_ctr, plane, cfg,
                                                        az_center, az_st, az_ed,
                                                        fd_st, fd_ed, BW_az,
                                                        rg_st, rg_ed);

    if (!domainCalculated)
    {
        std::cerr << "动态支撑域计算失败。" << std::endl;
        return false;
    }
    DBG("动态支撑域计算成功: beam_half_deg=" << 0.5 * cfg.beamwidth_deg
        << " support_half_deg="
        << std::max(2.0, 0.5 * cfg.beamwidth_deg)
        << " fd_st=" << fd_st << " fd_ed=" << fd_ed
        << " BW_az=" << BW_az
        << " az_st=" << az_st << " az_ed=" << az_ed
        << " rg_st=" << rg_st << " rg_ed=" << rg_ed);

    cfg.az_center = az_center;
    cfg.az_st = az_st;
    cfg.az_ed = az_ed;
    cfg.rg_st = rg_st;
    cfg.rg_ed = rg_ed;

    // 3) CTDR 慢时间对齐。d 是两个接收天线相位中心的物理间距；共发双收
    // 的等效双程相位中心间距为 d/2。原始 P38 始终在 skip=0 的双通道
    // 干涉相位上拟合，整数移位仅供后续 CSI 使用。
    [[maybe_unused]] const double equivalent_two_way_spacing_m =
        gmti::ctdr::equivalentTwoWayPhaseCenterSeparation(cfg.d_channel);
    [[maybe_unused]] const double equivalent_two_way_delay_s =
        configuredCtdrEquivalentTwoWayDelay(cfg, plane.V);
    const int skipInt_theory = configuredCsiChannelAlignmentPrt(cfg, plane.V);
    DBG("CTDR CSI 对齐: mode=" << cfg.csi_channel_alignment_mode
        << " rx_baseline_m=" << cfg.d_channel
        << " equivalent_two_way_spacing_m=" << equivalent_two_way_spacing_m
        << " equivalent_two_way_delay_s=" << equivalent_two_way_delay_s
        << " integer_prt_shift=" << skipInt_theory
        << " raw_p38_theory_rad_per_hz="
        << p38TheorySlopeLocal(cfg, plane.V)
        << " aligned_residual_theory_rad_per_hz="
        << p38TheorySlopeAfterIntegerAlignment(cfg, plane.V, skipInt_theory));

    int skipInt = 0;
    std::vector<std::complex<double>> F1, F2, F1_r, F2_r;
    std::vector<std::complex<float>> F1_pre, F2_pre;
    const size_t Na = static_cast<size_t>(effectivePulseNum(cfg));
    const size_t Nr = static_cast<size_t>(cfg.rg_len);

    std::vector<float> phi_fit;
    std::vector<float> phase_map_rg_correction;
    std::vector<double> cpu_phi_fit;
    std::vector<double> phi_diss_phase; // 存储相位值
    std::vector<int>    phi_diss_range; // 存储对应的距离向索引

    std::array<float, 2> p_38;
    std::array<float, 2> p_38_refit;
    std::vector<float> phase_map;
    std::vector<float> power_map;
    std::vector<float> threshold_map;
    std::vector<std::complex<float>> detection_complex_map;
    std::array<float, 2> p_38_raw = {
        std::numeric_limits<float>::quiet_NaN(),
        std::numeric_limits<float>::quiet_NaN()
    };
    std::vector<double> p38_raw_trace;
    std::vector<double> p38_pre_trace;
    std::vector<double> p38_refit_trace;
    std::string p38_raw_model_source = "not_run";
    std::string p38_pre_model_source = "not_run";
    std::string p38_refit_model_source = "not_run";
    std::array<float, 2> p_38_aligned = {
        std::numeric_limits<float>::quiet_NaN(),
        std::numeric_limits<float>::quiet_NaN()
    };
    std::vector<double> p38_aligned_trace;
    std::vector<double> p38_aligned_row_fa;
    {
        gmti::runtime::TimingScope timing_channel_cancellation("channel_cancellation", 0, timing_beam_extra);
        if (!data_on_gpu) {
            cuda_upload_async(data1, data2, Na, Nr);
        }

        if (!cuda_stage_align_async(skipInt, Na, Nr, cfg)) {
            return false;
        }
        if (!cuda_stage_fft_async(Na, Nr)) {
            return false;
        }
        if (!cuda_stage_dbs_async(static_cast<float>(fa_ctr), static_cast<float>(cfg.PRF), Na, Nr)) {
            return false;
        }
        cfg.p38_expected_slope_rad_per_hz =
            p38TheorySlopeAfterIntegerAlignment(cfg, plane.V, skipInt);

        double fa2 = unwrap_prf_to_model(fa_ctr, cfg.PRF, theta_deg, plane.V, cfg.fc); // 解除模糊
        double fa_shift = fa2 - fa_ctr;
        for (size_t i = 0; i < faAxis.size(); ++i) faAxis[i] += fa_shift;
        std::vector<float> faAxis_f(faAxis.begin(), faAxis.end());

        std::array<float, 2> p_38_raw_f;
        std::vector<float> p38_raw_trace_f;
        if (!clutter_cancel_38_paper_1_p38_cuda(
            faAxis_f,
            cfg.az_st, cfg.rg_st, cfg.az_ed, cfg.rg_ed,
            cfg,
            p_38_raw_f,
            &p38_raw_trace_f,
            &p38_raw_model_source
        )) return false;
        p_38_raw = p_38_raw_f;
        p38_raw_trace.assign(p38_raw_trace_f.begin(), p38_raw_trace_f.end());

        // --- 4) 距离向初相校正 ---
        double thre_rg = 0.1 * M_PI;

        if (!cfg.paired_raw_range_phase_override_f32.empty()) {
            if (!loadPairedRangePhaseOverride(cfg.paired_raw_range_phase_override_f32,
                                               Nr, phi_fit)) return false;
        } else if (!rg_correct_CUDA(cfg, thre_rg, phi_fit, phi_diss_phase, phi_diss_range)) {
            std::cout << "相位校正失败。" << std::endl;
            return false;
        }
        if (!cuda_apply_rg_correction_async(phi_fit, Na, Nr)) return false;
        // phase_map below is downloaded from this first, zero-shift DBS
        // pass.  Preserve its exact correction before phi_fit is reused
        // by the later CSI pass.
        phase_map_rg_correction = phi_fit;
        writePairedRangePhaseReference(cfg, periodIdx, periodIdx, "raw", phi_fit);
        DBG("最终对齐完成，使用移位脉冲数: " << skipInt);

        std::array<float, 2> p_38_f;
        std::vector<float> p38_pre_trace_f;
        if (!clutter_cancel_38_paper_1_p38_cuda(
            faAxis_f,
            cfg.az_st, cfg.rg_st, cfg.az_ed, cfg.rg_ed,
            cfg,
            p_38_f,
            &p38_pre_trace_f,
            &p38_pre_model_source
        )) return false;
        p_38 = p_38_f;
        p38_pre_trace.assign(p38_pre_trace_f.begin(), p38_pre_trace_f.end());

        // 保存零移位、距离相位校正后的相位快照。常规 GPU 聚类直接在设备端
        // 使用它；只有 P38 二次拟合等确实需要整幅图的路径才回传到主机。
        if (!cuda_capture_phase_map_async(Na * Nr)) return false;
        if (cfg.p38_enhanced_enable && cfg.p38_refit_enable &&
            !cuda_download_phase_map(phase_map, Na * Nr)) return false;
        const bool need_pre_csi_host_copy =
            (cfg.p38_enhanced_enable && cfg.p38_refit_enable) ||
            cfg.channel_calibration_enable || cfg.csi_metrics_enable;
        if (need_pre_csi_host_copy &&
            !cuda_download_sync(F1_pre, F2_pre, Na * Nr)) {
            return false;
        }

        // 对消
        cuda_stage_align_async(skipInt_theory, Na, Nr, cfg);
        cuda_stage_fft_async(Na, Nr);
        cuda_stage_dbs_async(static_cast<float>(fa_ctr), static_cast<float>(cfg.PRF), Na, Nr);
        if (!cfg.csi_range_phase_correction_enable) {
            phi_fit.assign(Nr, 0.0f);
        } else if (!cfg.paired_csi_range_phase_override_f32.empty()) {
            if (!loadPairedRangePhaseOverride(cfg.paired_csi_range_phase_override_f32,
                                               Nr, phi_fit)) return false;
        } else if (!rg_correct_CUDA(cfg, thre_rg, phi_fit, phi_diss_phase, phi_diss_range)) {
            std::cout << "相位校正失败。" << std::endl;
            return false;
        }
        if (cfg.csi_range_phase_correction_enable &&
            !cuda_apply_rg_correction_async(phi_fit, Na, Nr)) return false;
        writePairedRangePhaseReference(cfg, periodIdx, periodIdx, "csi", phi_fit);
         DBG("最终对齐完成，使用移位脉冲数: " << skipInt_theory);

        // --- 8) 最终 CSI 对消 ---
        // 第二次 P38 在整数移位和距离向相位补偿之后拟合，用于抹平两通道
        // 位置差残余相位斜率，随后再做幅度均衡相减。
        Config csi_fit_cfg = cfg;
        csi_fit_cfg.p38_expected_slope_rad_per_hz =
            p38TheorySlopeAfterIntegerAlignment(cfg, plane.V, skipInt_theory);
        std::array<float, 2> p_38_csi_f;
        std::vector<float> ph_trace_f;
        std::vector<float> fa_cut_f;
        std::vector<std::complex<float>> F1_r_f;
        if (!clutter_cancel_38_paper_1_cuda(
            faAxis_f,
            cfg.az_st, cfg.rg_st, cfg.az_ed, cfg.rg_ed,
            csi_fit_cfg,
            F1_r_f, p_38_csi_f, ph_trace_f, fa_cut_f
        )) return false;
        p_38_aligned = p_38_csi_f;
        p38_aligned_trace.assign(ph_trace_f.begin(), ph_trace_f.end());
        p38_aligned_row_fa.assign(fa_cut_f.begin(), fa_cut_f.end());
        if (cfg.csi_metrics_enable &&
            (cfg.csi_metrics_beam_id < 0 || cfg.csi_metrics_beam_id == periodIdx)) {
            gmti::runtime::TimingScope timing_csi_metrics(
                "csi_metrics", periodIdx, timing_beam_extra);
            std::vector<std::complex<float> > csi_before_ch1;
            std::vector<std::complex<float> > csi_before_ch2;
            if (!cuda_download_sync(csi_before_ch1, csi_before_ch2, Na * Nr)) {
                std::cerr << "[CSI_METRICS][ERR] failed to download production CSI inputs"
                          << std::endl;
                return false;
            }
            std::string metrics_error;
            const int metrics_period_id = cfg.stage2_period_id >= 0
                ? cfg.stage2_period_id : cfg.new_protocol_file_period_index;
            if (!gmti::metrics::writeCsiMetricsTap(
                    cfg, metrics_period_id, periodIdx, static_cast<int>(Na), static_cast<int>(Nr),
                    cfg.az_st, cfg.az_ed, cfg.rg_st, cfg.rg_ed,
                    faAxis_f, cfg.Rg, csi_before_ch1, csi_before_ch2,
                    F1_pre, F2_pre, F1_r_f,
                    p_38_csi_f, metrics_error)) {
                std::cerr << "[CSI_METRICS][ERR] " << metrics_error << std::endl;
                return false;
            }
        }
    }

    // --- 10) CFAR 处理 ---
    GMTIOutput::Detect targetSel;
    bool targetDetection = false;
    const int dynamic_band_st =
        std::max(0, std::min(az_st, effectivePulseNum(cfg) - 1));
    const int dynamic_band_ed =
        std::max(0, std::min(az_ed, effectivePulseNum(cfg) - 1));
    const bool full_csi_detection_band =
        cfg.csi_detection_band_mode == "full";
    const bool union_csi_detection_band =
        cfg.csi_detection_band_mode == "union";
    const bool split_csi_detection_band =
        cfg.csi_detection_band_mode == "split";
    const int band_st = full_csi_detection_band ? 0 : dynamic_band_st;
    const int band_ed = full_csi_detection_band
        ? effectivePulseNum(cfg) - 1 : dynamic_band_ed;
    if (cfg.runtime_diagnostics_enabled) {
        std::cout << "[CFAR][CSI-BAND] mode=" << cfg.csi_detection_band_mode
                  << " dynamic=[" << dynamic_band_st << ',' << dynamic_band_ed
                  << "] applied="
                  << (split_csi_detection_band ? "CSI/in-band + channel2/out-of-band (independent CFAR)" :
                      union_csi_detection_band ? "dynamic+full" :
                      ("[" + std::to_string(band_st) + "," +
                       std::to_string(band_ed) + "]"))
                  << std::endl;
    }
    std::vector<int> prow_new, pcol_new;
    {
        gmti::runtime::TimingScope timing_detection("detection", 0, timing_beam_extra);
        std::vector<int> prow, pcol;
        std::vector<float> mydata;
        const int cfar_bnum = cfg.cfar_background_cells;
        const int c_num = cfg.cfar_guard_cells;
        bool cfarSuccess1 = false;
        bool cfarSuccess2 = false;
        bool gpu_cfar_resident = false;
        std::size_t gpu_cfar_hit_count = 0U;
        bool split_cluster_complete = false;
        StrongSmallClusterFilterStats split_cluster_filter_stats;

        std::vector<std::complex<float>> CSI_out;
        if (split_csi_detection_band) {
            const int guard = cfg.csi_split_boundary_guard_rows;
            const int H = effectivePulseNum(cfg);
            const int csi_cut_st = std::max(0, dynamic_band_st - guard);
            const int csi_cut_ed = std::min(H - 1, dynamic_band_ed + guard);
            // The CFAR row arguments define the cut-band mask, not an
            // evaluation ROI. Keep the guard overlap in the two branches,
            // but use the mixed detector input for the in-band branch so its
            // in-band sensitivity matches dynamic mode. Guard-row in-band
            // hits are discarded after clustering; outside the actual
            // support, only the independent original-channel branch owns
            // the candidate.
            const int original_cut_st = std::min(H - 1, dynamic_band_st + guard + 1);
            const int original_cut_ed = std::max(0, dynamic_band_ed - guard - 1);
            std::vector<float> in_hits, out_hits, in_power, out_power;
            std::vector<float> in_threshold, out_threshold;
            std::size_t in_hit_count = 0U, out_hit_count = 0U;
            const bool in_ok = dpca_cfar2_fast_cuda(
                CSI_out, csi_cut_st, csi_cut_ed, cfg.pf, c_num, cfar_bnum,
                cfg.csi_split_in_band_cfar_type, cfg, in_hits, &in_power, nullptr,
                &in_hit_count, true, 0, 1, &in_threshold);
            const bool out_ok = dpca_cfar2_fast_cuda(
                CSI_out, original_cut_st, original_cut_ed, cfg.pf,
                c_num, cfar_bnum, cfg.cfar_type, cfg, out_hits, &out_power,
                nullptr, &out_hit_count, true, 2, 2, &out_threshold);
            cfarSuccess1 = in_ok && out_ok && in_hits.size() == out_hits.size() &&
                           in_power.size() == out_power.size();
            if (cfarSuccess1) {
                auto cluster_branch = [&](const std::vector<float>& hits,
                                          const std::vector<float>& power,
                                          std::vector<int>& rows,
                                          std::vector<int>& cols,
                                          StrongSmallClusterFilterStats& stats,
                                          bool phase_filter_enable,
                                          bool strong_small_enable,
                                          bool out_of_band_branch) {
                    Config branch_cfg = cfg;
                    const bool small_recovery_enable =
                        cfg.csi_split_small_cluster_enable;
                    branch_cfg.cluster_strong_small_enable = strong_small_enable;
                    const int branch_min_points =
                        (strong_small_enable || small_recovery_enable)
                        ? std::min(cfg.min_points,
                                   small_recovery_enable
                                       ? cfg.csi_split_small_cluster_min_points
                                       : cfg.cluster_strong_small_min_points)
                        : cfg.min_points;
                    std::vector<float> refined, phase_std;
                    bool strong_applied = false;
                    const float phase_limit = phase_filter_enable
                        ? static_cast<float>(
                              out_of_band_branch
                                  ? cfg.csi_split_out_of_band_phase_max_std_rad
                                  : cfg.cluster_max_phase_std_rad)
                        : 0.0f;
                    const bool ok = cluster_filter_gap_phase_cuda(
                        hits, phase_map, branch_min_points,
                        cfg.cluster_max_range_gap,
                        phase_limit,
                        branch_cfg, refined, rows, cols, phase_std, false, false,
                        &stats.global_median_power, &stats.baseline_kept,
                        &stats.strong_small_kept, &stats.small_rejected,
                        &strong_applied);
                    if (!ok) return false;
                    // Split-mode CFAR downloads independent maps for both
                    // branches, so the resident-device strong-small path is
                    // intentionally unavailable here.  Apply the same
                    // second-stage power/size test on the host instead of
                    // silently bypassing it (the old path marked clustering
                    // complete and never reached this filter).
                    if (strong_small_enable) {
                        stats = filterStrongSmallClusters(
                            hits, power, H, cfg.rg_len,
                            cfg.cluster_max_range_gap, branch_cfg, rows, cols,
                            phase_std);
                    } else if (small_recovery_enable) {
                        filterSplitSmallComponents(
                            hits, power, H, cfg.rg_len, cfg, rows, cols,
                            phase_std, &stats.strong_small_kept,
                            &stats.small_rejected);
                    }
                    if (out_of_band_branch) {
                        stats.vertical_removed += filterVerticalLineClusters(
                            hits, power, H, cfg.rg_len, cfg, rows, cols, phase_std);
                    }
                    return true;
                };
                std::vector<int> in_rows, in_cols, out_rows, out_cols;
                StrongSmallClusterFilterStats in_stats, out_stats;
                cfarSuccess1 = cluster_branch(
                              in_hits, in_power, in_rows, in_cols, in_stats, true, false, false) &&
                          cluster_branch(
                              out_hits, out_power, out_rows, out_cols, out_stats,
                              cfg.csi_split_out_of_band_phase_filter_enable,
                              cfg.cluster_strong_small_enable, true);
                if (cfarSuccess1) {
                    size_t kept = 0U;
                    for (size_t i = 0; i < in_rows.size(); ++i) {
                        if (in_rows[i] < dynamic_band_st ||
                            in_rows[i] > dynamic_band_ed) continue;
                        in_rows[kept] = in_rows[i];
                        in_cols[kept] = in_cols[i];
                        ++kept;
                    }
                    in_rows.resize(kept);
                    in_cols.resize(kept);
                    prow_new = in_rows;
                    pcol_new = in_cols;
                    auto row_distance = [H](int a, int b) {
                        const int d = std::abs(a - b);
                        return std::min(d, H - d);
                    };
                    std::size_t duplicates = 0U;
                    for (std::size_t i = 0; i < out_rows.size(); ++i) {
                        int duplicate = -1;
                        for (std::size_t j = 0; j < prow_new.size(); ++j) {
                            if (row_distance(out_rows[i], prow_new[j]) <=
                                    cfg.csi_split_merge_doppler_bins &&
                                std::abs(out_cols[i] - pcol_new[j]) <=
                                    cfg.csi_split_merge_range_bins) {
                                duplicate = static_cast<int>(j);
                                break;
                            }
                        }
                        if (duplicate < 0) {
                            prow_new.push_back(out_rows[i]);
                            pcol_new.push_back(out_cols[i]);
                        } else {
                            // Different branches have different power scales.
                            // The overlap only protects the boundary; ownership
                            // of the physical row determines the retained target.
                            if (out_rows[i] < dynamic_band_st ||
                                out_rows[i] > dynamic_band_ed) {
                                prow_new[duplicate] = out_rows[i];
                                pcol_new[duplicate] = out_cols[i];
                            }
                            ++duplicates;
                        }
                    }
                    mydata.resize(in_hits.size());
                    power_map.resize(in_power.size());
                    threshold_map.resize(in_power.size());
                    for (std::size_t i = 0; i < mydata.size(); ++i) {
                        mydata[i] = std::max(in_hits[i], out_hits[i]);
                        const int row = static_cast<int>(i / Nr);
                        power_map[i] = row >= dynamic_band_st && row <= dynamic_band_ed
                            ? in_power[i] : out_power[i];
                        threshold_map[i] = row >= dynamic_band_st && row <= dynamic_band_ed
                            ? in_threshold[i] : out_threshold[i];
                    }
                    gpu_cfar_hit_count = in_hit_count + out_hit_count;
                    split_cluster_filter_stats.baseline_kept =
                        in_stats.baseline_kept + out_stats.baseline_kept;
                    split_cluster_filter_stats.strong_small_kept =
                        in_stats.strong_small_kept + out_stats.strong_small_kept;
                    split_cluster_filter_stats.small_rejected =
                        in_stats.small_rejected + out_stats.small_rejected;
                    split_cluster_filter_stats.vertical_removed =
                        in_stats.vertical_removed + out_stats.vertical_removed;
                    split_cluster_complete = true;
                    std::cout << "[CFAR][SPLIT] beam=" << periodIdx
                              << " csi_cut=[" << csi_cut_st << ',' << csi_cut_ed << ']'
                              << " in_eval_rows=[" << dynamic_band_st << ','
                              << dynamic_band_ed << "] out_cut_band=["
                              << original_cut_st << ',' << original_cut_ed << ']'
                              << " in_hits=" << in_hit_count
                              << " out_hits=" << out_hit_count
                              << " in_clusters=" << in_rows.size()
                              << " out_clusters=" << out_rows.size()
                              << " in_cfar=" << cfg.csi_split_in_band_cfar_type
                              << " out_cfar=" << cfg.cfar_type
                              << " out_phase_filter="
                              << (cfg.csi_split_out_of_band_phase_filter_enable ? 1 : 0)
                              << " vertical_removed="
                              << split_cluster_filter_stats.vertical_removed
                              << " small_recovered="
                              << split_cluster_filter_stats.strong_small_kept
                              << " small_rejected="
                              << split_cluster_filter_stats.small_rejected
                              << " overlap_duplicates=" << duplicates
                              << " merged_clusters=" << prow_new.size() << std::endl;
                    writeSplitClusterFilterDiagnostic(
                        cfg, periodIdx, split_cluster_filter_stats.vertical_removed,
                        split_cluster_filter_stats.strong_small_kept,
                        split_cluster_filter_stats.small_rejected,
                        in_rows.size(), out_rows.size());
                }
            }
        } else if (union_csi_detection_band) {
            std::vector<float> dynamic_hits, full_hits;
            std::vector<float> dynamic_power, full_power;
            std::vector<std::complex<float>> dynamic_complex, full_complex;
            const bool dump_complex = pcProfileDopplerRow(periodIdx) >= 0;
            const bool dynamic_ok = dpca_cfar2_fast_cuda(
                CSI_out, dynamic_band_st, dynamic_band_ed,
                cfg.pf, c_num, cfar_bnum, cfg.cfar_type, cfg,
                dynamic_hits, &dynamic_power,
                dump_complex ? &dynamic_complex : nullptr);
            const bool full_ok = dpca_cfar2_fast_cuda(
                CSI_out, 0, effectivePulseNum(cfg) - 1,
                cfg.pf, c_num, cfar_bnum, cfg.cfar_type, cfg,
                full_hits, &full_power,
                dump_complex ? &full_complex : nullptr);
            cfarSuccess1 = dynamic_ok && full_ok &&
                           dynamic_hits.size() == full_hits.size() &&
                           dynamic_power.size() == full_power.size() &&
                           (!dump_complex ||
                            (dynamic_complex.size() == dynamic_power.size() &&
                             full_complex.size() == full_power.size()));
            if (cfarSuccess1) {
                mydata.resize(dynamic_hits.size());
                power_map.resize(dynamic_power.size());
                if (dump_complex) detection_complex_map.resize(dynamic_power.size());
                for (size_t i = 0; i < mydata.size(); ++i) {
                    mydata[i] = std::max(dynamic_hits[i], full_hits[i]);
                    const bool use_dynamic = dynamic_power[i] >= full_power[i];
                    power_map[i] = use_dynamic ? dynamic_power[i] : full_power[i];
                    if (dump_complex) {
                        detection_complex_map[i] = use_dynamic
                            ? dynamic_complex[i] : full_complex[i];
                    }
                }
            }
        } else {
            // The production strong-small filter consumes the resident device
            // power map.  Download full maps only for host-side diagnostics or
            // P38 refit; normal detection returns compact candidates only.
            const bool need_host_maps =
                (cfg.p38_enhanced_enable && cfg.p38_refit_enable) ||
                (cfg.runtime_diagnostics_enabled && cfarDumpSelectedBeam(periodIdx)) ||
                pcProfileDopplerRow(periodIdx) >= 0;
            cfarSuccess1 = dpca_cfar2_fast_cuda(
                CSI_out, band_st, band_ed, cfg.pf, c_num, cfar_bnum,
                cfg.cfar_type, cfg, mydata,
                need_host_maps ? &power_map : nullptr,
                pcProfileDopplerRow(periodIdx) >= 0 ? &detection_complex_map : nullptr,
                cfg.runtime_diagnostics_enabled ? &gpu_cfar_hit_count : nullptr,
                need_host_maps);
            gpu_cfar_resident = cfarSuccess1;
        }
        if (!cfarSuccess1)
        {
            if (split_csi_detection_band) {
                std::cerr << "Split CFAR/cluster failed; refusing a mixed-map CPU fallback."
                          << std::endl;
                return false;
            }
            std::cerr << "GPU CFAR 处理失败，尝试回退到 CPU 路径。" << std::endl;
            // CPU 回退路径：需要回传 F1/F2
            std::vector<std::complex<float>> F1_f, F2_f;
            cuda_download_sync(F1_f, F2_f, Na * Nr);
            convertFloatComplexToDouble(F1_f, F1);
            convertFloatComplexToDouble(F2_f, F2);
            if (!cuda_download_csi_sync(CSI_out, Na * Nr))
            {
                std::cerr << "CSI 回传失败。" << std::endl;
                return false;
            }
            auto run_cpu_band = [&](int cpu_band_st, int cpu_band_ed,
                                    std::vector<double> &hits,
                                    std::vector<float> &powers) {
                std::vector<std::complex<double>> detect_data = F2;
                if (cpu_band_st <= cpu_band_ed) {
                    for (int r = cpu_band_st; r <= cpu_band_ed; ++r) {
                        const size_t off = static_cast<size_t>(r) *
                                           static_cast<size_t>(cfg.rg_len);
                        for (size_t c = 0;
                             c < static_cast<size_t>(cfg.rg_len); ++c) {
                            const auto v = CSI_out[off + c];
                            detect_data[off + c] =
                                std::complex<double>(v.real(), v.imag());
                        }
                    }
                }
                std::vector<int> cpu_prow, cpu_pcol;
                if (pcProfileDopplerRow(periodIdx) >= 0) {
                    detection_complex_map.resize(detect_data.size());
                    for (size_t i = 0; i < detect_data.size(); ++i) {
                        detection_complex_map[i] = std::complex<float>(
                            static_cast<float>(detect_data[i].real()),
                            static_cast<float>(detect_data[i].imag()));
                    }
                }
                return dpca_cfar2_fast(
                    detect_data, cfg.pf, c_num, cfar_bnum, cfg.cfar_type,
                    cfg, hits, cpu_prow, cpu_pcol, &powers);
            };
            if (union_csi_detection_band) {
                std::vector<double> dynamic_hits, full_hits;
                std::vector<float> dynamic_power, full_power;
                cfarSuccess2 =
                    run_cpu_band(dynamic_band_st, dynamic_band_ed,
                                 dynamic_hits, dynamic_power) &&
                    run_cpu_band(0, effectivePulseNum(cfg) - 1,
                                 full_hits, full_power) &&
                    dynamic_hits.size() == full_hits.size() &&
                    dynamic_power.size() == full_power.size();
                if (cfarSuccess2) {
                    mydata.resize(dynamic_hits.size());
                    power_map.resize(dynamic_power.size());
                    for (size_t i = 0; i < mydata.size(); ++i) {
                        mydata[i] = static_cast<float>(
                            std::max(dynamic_hits[i], full_hits[i]));
                        power_map[i] = std::max(dynamic_power[i], full_power[i]);
                    }
                }
            } else {
                std::vector<double> mydata_d;
                std::vector<float> power_map_d;
                cfarSuccess2 = run_cpu_band(
                    band_st, band_ed, mydata_d, power_map_d);
                if (cfarSuccess2) {
                    mydata.assign(mydata_d.begin(), mydata_d.end());
                    power_map.assign(power_map_d.begin(), power_map_d.end());
                }
            }
            if (cfarSuccess2) {
                gpu_cfar_resident = false;
                DBG("合成检测数据完成(CPU)：mode=" << cfg.csi_detection_band_mode);
            }
            else {
                std::cerr << "CPU CFAR 处理也失败。" << std::endl;
                return false;
            }
        }

        const size_t cfar_hit_cells = split_csi_detection_band
            ? gpu_cfar_hit_count : cfg.runtime_diagnostics_enabled
            ? (gpu_cfar_resident
                   ? gpu_cfar_hit_count
                   : static_cast<size_t>(std::count_if(
                         mydata.begin(), mydata.end(),
                         [](float value) { return value > 0.0f; })))
            : 0U;
        writeDetectionRangeProfile(cfg, periodIdx, power_map);
        writeDetectionComplexRangeProfile(cfg, periodIdx, detection_complex_map);

        // --- 11) 聚类滤波 ---
        std::vector<float> refined_mydata;
        std::vector<float> phase_std_list;
        const int cluster_run_min_points = cfg.cluster_strong_small_enable
            ? std::min(cfg.min_points, cfg.cluster_strong_small_min_points)
            : cfg.min_points;
        const bool need_refined_mydata =
            !gpu_cfar_resident && power_map.size() != mydata.size();
        // cluster_filter(mydata, cfg.min_points, cfg, refined_mydata, prow_new, pcol_new);
        const bool use_gpu_cluster = true;
        bool cluster_ok = split_cluster_complete;
        StrongSmallClusterFilterStats cluster_filter_stats = split_cluster_filter_stats;
        bool strong_filter_applied_on_gpu = false;
        if (use_gpu_cluster && !split_cluster_complete)
        {
            cluster_ok = cluster_filter_gap_phase_cuda(mydata, phase_map, cluster_run_min_points,
                                   cfg.cluster_max_range_gap,
                                   static_cast<float>(cfg.cluster_max_phase_std_rad),
                                   cfg, refined_mydata, prow_new, pcol_new, phase_std_list,
                                   need_refined_mydata, gpu_cfar_resident,
                                   &cluster_filter_stats.global_median_power,
                                   &cluster_filter_stats.baseline_kept,
                                   &cluster_filter_stats.strong_small_kept,
                                   &cluster_filter_stats.small_rejected,
                                   &strong_filter_applied_on_gpu);
            if (!cluster_ok)
            {
                std::cerr << "GPU 聚类失败，回退到 CPU 路径。" << std::endl;
            }
        }
        if (!cluster_ok)
        {
            if (mydata.empty() && gpu_cfar_resident &&
                !cuda_download_cfar_maps(mydata, power_map, Na * Nr)) {
                std::cerr << "聚类 CPU 回退时 CFAR 图回传失败。" << std::endl;
                return false;
            }
            if (phase_map.empty() && !cuda_download_phase_map(phase_map, Na * Nr)) {
                std::cerr << "聚类 CPU 回退时相位图回传失败。" << std::endl;
                return false;
            }
            std::vector<double> mydata_d(mydata.begin(), mydata.end());
            std::vector<double> phase_map_d(phase_map.begin(), phase_map.end());
            std::vector<double> refined_mydata_d;
            std::vector<double> phase_std_list_d;
            cluster_ok = cluster_filter_gap_phase(mydata_d, phase_map_d, cluster_run_min_points,
                                                  cfg.cluster_max_range_gap,
                                                  cfg.cluster_max_phase_std_rad,
                                                  cfg, refined_mydata_d, prow_new, pcol_new, phase_std_list_d);
            if (cluster_ok) {
                refined_mydata.assign(refined_mydata_d.begin(), refined_mydata_d.end());
                phase_std_list.assign(phase_std_list_d.begin(), phase_std_list_d.end());
            }
        }
        if (!cluster_ok) {
            std::cerr << "CPU/GPU 聚类均失败。" << std::endl;
            return false;
        }
        if (!split_cluster_complete && !strong_filter_applied_on_gpu) {
            cluster_filter_stats = filterStrongSmallClusters(
                mydata, power_map, effectivePulseNum(cfg), cfg.rg_len, 2, cfg,
                prow_new, pcol_new, phase_std_list);
        }

#ifdef DEBUG
        // 保存 CFAR 结果以便调试
        {
            std::ofstream fout("debug_CFI_mydata.bin", std::ios::binary);
            fout.write(reinterpret_cast<const char *>(mydata.data()), mydata.size() * sizeof(float));
            fout.close();
        }
#endif

        if (!cfarSuccess1 && !cfarSuccess2)
        {
            std::cerr << "CFAR 处理失败。" << std::endl;
            return false;
        }
        DBG("CFAR 处理成功，目标数: " << prow_new.size());


#ifdef DEBUG
    {
        std::ofstream fout1("debug_F1_final.bin", std::ios::binary);
        fout1.write(reinterpret_cast<const char *>(F1.data()), F1.size() * sizeof(std::complex<double>));
        fout1.close();

        std::ofstream fout2("debug_F2_final.bin", std::ios::binary);
        fout2.write(reinterpret_cast<const char *>(F2.data()), F2.size() * sizeof(std::complex<double>));
        fout2.close();
    }
#endif
        const bool use_gpu_target = true;
        if (use_gpu_target)
        {
            targetDetection = target_select_cuda(prow_new, pcol_new, cfg, targetSel);
            if (!targetDetection)
            {
                std::cerr << "GPU target_select 失败，回退到 CPU 路径。" << std::endl;
            }
        }
        if (!targetDetection)
        {
            std::vector<std::complex<float>> F1_f, F2_f;
            cuda_download_sync(F1_f, F2_f, Na * Nr);
            convertFloatComplexToDouble(F1_f, F1);
            convertFloatComplexToDouble(F2_f, F2);
            targetDetection = target_select(F1, F2, prow_new, pcol_new, cfg, targetSel);
        }
        if (cfg.runtime_diagnostics_enabled) {
            std::cout << "[CFAR][SUMMARY] beam=" << periodIdx
                      << " hit_cells=" << cfar_hit_cells
                      << " clusters=" << prow_new.size()
                      << " selected=" << targetSel.prow.size()
                      << " min_points=" << cfg.min_points
                      << " strong_small_kept="
                      << cluster_filter_stats.strong_small_kept
                      << " small_rejected=" << cluster_filter_stats.small_rejected
                      << " global_median_power="
                      << cluster_filter_stats.global_median_power
                      << " pf=" << cfg.pf << std::endl;
        }
    }

    std::vector<double> p38_pre_row_fa;
    p38_pre_row_fa.reserve(static_cast<size_t>(std::max(0, az_ed - az_st + 1)));
    for (int r = az_st; r <= az_ed && r >= 0 && r < static_cast<int>(faAxis.size()); ++r) {
        p38_pre_row_fa.push_back(faAxis[static_cast<size_t>(r)]);
    }
    std::array<double, 2> p_38_eval{{static_cast<double>(p_38[0]), static_cast<double>(p_38[1])}};
    std::array<double, 2> p_38_raw_eval{{static_cast<double>(p_38_raw[0]), static_cast<double>(p_38_raw[1])}};
    P38StageMetrics p38_raw_metrics = evaluateP38FitMetrics(p38_raw_trace, p38_pre_row_fa, p_38_raw_eval);
    P38StageMetrics p38_pre_metrics = evaluateP38FitMetrics(p38_pre_trace, p38_pre_row_fa, p_38_eval);
    p38_raw_metrics.source = p38_raw_model_source;
    p38_pre_metrics.source = p38_pre_model_source;

    std::array<float, 2> p38_used = p_38;
    std::string p38_used_source = "pre";
    P38StageMetrics p38_refit_metrics;
    if (cfg.p38_enhanced_enable && cfg.p38_refit_enable &&
        !power_map.empty() && !F1_pre.empty() &&
        F1_pre.size() == F2_pre.size()) {
            std::array<float, 2> p38_refit_candidate = {static_cast<float>(p_38[0]), static_cast<float>(p_38[1])};
            auto estimate_p38 = [this, &p38_refit_model_source](const std::vector<double> &fa_axis,
                                       const std::vector<std::complex<float>> &F1_masked,
                                       const std::vector<std::complex<float>> &F2_masked,
                                       int az_st_local,
                                       int rg_st_local,
                                       int az_ed_local,
                                       int rg_ed_local,
                                       const Config &cfg_local,
                                       std::vector<std::complex<float>> &prosig_38,
                                       std::array<float, 2> &p38_out,
                                       std::vector<double> &phase_samples,
                                       std::vector<double> &row_samples) -> bool {
                std::vector<float> fa_axis_f(fa_axis.begin(), fa_axis.end());
                std::vector<float> phase_samples_f;
                std::vector<float> row_samples_f;
                const bool ok = this->clutter_cancel_38_paper_1(
                    fa_axis_f,
                    F1_masked,
                    F2_masked,
                    az_st_local, rg_st_local, az_ed_local, rg_ed_local,
                    cfg_local,
                    prosig_38,
                    p38_out,
                    phase_samples_f,
                    row_samples_f,
                    &p38_refit_model_source);
                phase_samples.assign(phase_samples_f.begin(), phase_samples_f.end());
                row_samples.assign(row_samples_f.begin(), row_samples_f.end());
                return ok;
            };
            p38_refit_metrics = refitP38WithMask(cfg,
                                             faAxis, F1_pre, F2_pre,
                                             az_st, rg_st, az_ed, rg_ed,
                                             prow_new, pcol_new,
                                             targetSel.prow, targetSel.pcol,
                                             phase_map, power_map,
                                             estimate_p38,
                                             p38_refit_candidate,
                                             &p38_refit_trace);
        p38_refit_metrics.source = p38_refit_model_source;
        p_38_refit = p38_refit_candidate;
        const double delta_k = std::abs(static_cast<double>(p38_refit_candidate[0]) - p_38[0]);
        const double delta_b = std::abs(wrapPiLocal(static_cast<double>(p38_refit_candidate[1]) - p_38[1]));
        const bool refit_valid = p38_refit_metrics.valid &&
                                 p38_refit_metrics.sample_count >= std::max(0, cfg.p38_refit_min_sample_count) &&
                                 p38_refit_metrics.inlier_ratio >= cfg.p38_refit_min_inlier_ratio &&
                                 p38_refit_metrics.rmse <= cfg.p38_refit_max_rmse_rad &&
                                 delta_k <= cfg.p38_refit_max_delta_k &&
                                 delta_b <= cfg.p38_refit_max_delta_b_rad;
        p38_refit_metrics.p38 = {p38_refit_candidate[0], p38_refit_candidate[1]};
        p38_refit_metrics.valid = refit_valid;
        if (refit_valid) {
            p38_used = p38_refit_candidate;
            p38_used_source = "refit";
        }
    }

    const gmti::calibration::ChannelCalibrationResult channel_calibration =
        gmti::calibration::estimateStaticCtdrResidual(
            cfg, plane, faAxis, F1_pre, F2_pre, phase_map_rg_correction,
            az_st, az_ed, rg_st, rg_ed,
            targetSel.prow, targetSel.pcol);
    gmti::calibration::writeChannelCalibrationDiagnostics(
        cfg, periodIdx, channel_calibration);
    const double localization_phase_bias =
        cfg.channel_calibration_enable &&
        cfg.channel_calibration_apply_to_localization &&
        channel_calibration.applied
            ? channel_calibration.applied_phase_bias_rad
            : 0.0;

    writeP38FitDiagnostics(cfg, periodIdx, plane.V,
                           az_st, az_ed, rg_st, rg_ed,
                           p38_pre_row_fa,
                           p38_raw_trace, p38_raw_metrics,
                           p38_pre_trace, p38_pre_metrics,
                           p38_refit_trace, p38_refit_metrics,
                           p38_used_source);
    writeP38AlignmentDiagnostics(cfg, periodIdx, plane.V, skipInt_theory,
                                 p38_aligned_row_fa, p38_aligned_trace,
                                 p_38_aligned);

    double ref_phase = faAxis[az_center] * static_cast<double>(p38_used[0]) + static_cast<double>(p38_used[1]);

    auto deg2rad = [](double d)
    { return d * M_PI / 180.0; };

    // 常规 GPU 路径只为最终候选点回传相位；P38 二次拟合路径已经保有完整
    // phase_map。这样不再为每个波位传输整幅 128x4096 相位图。
    std::vector<float> selected_phase;
    if (phase_map.empty() &&
        !cuda_download_phase_samples(targetSel.prow, targetSel.pcol,
                                     static_cast<int>(Nr), selected_phase)) {
        std::cerr << "候选点相位回传失败。" << std::endl;
        return false;
    }

    // 结果容器
    const size_t L = targetSel.prow.size();
    std::vector<int> row_af(L);
    std::vector<double> af_ransac(L);
    std::vector<double> MT; // MT(i,:) = [lat,lng,cfg.MT_nowz,xP,yP]
    // MT.resize(L * 6, 0.0);

    size_t n_selected = L;
    size_t n_old_valid = 0;
    size_t n_comp_valid = 0;
    size_t n_old_invalid_comp_valid = 0;
    size_t n_old_valid_comp_invalid = 0;
    size_t n_both_valid = 0;
    size_t n_both_invalid = 0;
    size_t n_output = 0;

    // 参考相位：ref_phase 已在外部给定
    const double k = p38_used[0]; // slope
    const double b = p38_used[1]; // intercept
    DBG("多普勒斜率 k = " << k << ", 截距 b = " << b
        << " p38_used_source=" << p38_used_source);
    const double thetaRot = plane.V_angle; // 度
    DBG("旋转角 thetaRot = " << thetaRot << " 度");
    const double cosT = std::abs(gmti::trig_lut::cos(deg2rad(thetaRot)));
    const double sinT = std::abs(gmti::trig_lut::sin(deg2rad(thetaRot)));
    const double VE = plane.V * gmti::trig_lut::cos(deg2rad(plane.V_angle));
    const double VN = plane.V * gmti::trig_lut::sin(deg2rad(plane.V_angle));

    int flag = flight_flag_by_sign(VE, VN); // 1/2/3/4
    flag += cfg_.squint_side * 4;           // 根据斜视侧调整方向标志

    for (size_t i = 0; i < L; ++i)
    {
        const int r = targetSel.prow[i];
        const int c = targetSel.pcol[i];
        const size_t off = static_cast<size_t>(r) * Nr + static_cast<size_t>(c);

        // dphi = angle(F1f * conj(F2f))
        // const std::complex<double> v = F1[off] * std::conj(F2[off]);
        // double dphi = std::arg(v);
        const double dphi_sample = phase_map.empty()
            ? static_cast<double>(selected_phase[i])
            : static_cast<double>(phase_map[off]);
        double dphi = dphi_sample;

        // dphi = phaseUnwrap(dphi, ref_phase);  —— 相对 ref_phase 的最短距离解缠
        {
            double diff = dphi - ref_phase;
            if (diff > M_PI)
                diff -= 2.0 * M_PI;
            if (diff < -M_PI)
                diff += 2.0 * M_PI;
            dphi = ref_phase + diff;
        }

        const double af_row = (r >= 0 && r < static_cast<int>(faAxis.size()))
            ? faAxis[static_cast<size_t>(r)]
            : std::numeric_limits<double>::quiet_NaN();
        const double af_phase_legacy = (std::abs(k) < 1e-12) ? 0.0 : ((dphi - b) / k);
        const double af_total = cfg.motion_comp_use_row_doppler ? af_row : af_phase_legacy;
        const double Rg = cfg.Rg[static_cast<size_t>(c)];
        const double range_phase_correction =
            (c >= 0 && c < static_cast<int>(phase_map_rg_correction.size()))
                ? static_cast<double>(phase_map_rg_correction[static_cast<size_t>(c)])
                : std::numeric_limits<double>::quiet_NaN();
        const double calibrated_range_phase_correction =
            std::isfinite(range_phase_correction)
                ? range_phase_correction - localization_phase_bias
                : range_phase_correction;
        // Release P38 inverse localization does not need the single-period
        // CTDR velocity solvers.  Previously this call still ran even with
        // motion_comp_enable=0, wasting work and making a disabled feature
        // appear in the hot path.  Keep a lightweight diagnostic record and
        // invoke the solver only when motion compensation is explicitly on.
        MotionCompResult motion_pre;
        motion_pre.phi_meas = dphi;
        motion_pre.range_phase_correction = calibrated_range_phase_correction;
        motion_pre.k = p_38[0];
        motion_pre.b = p_38[1];
        motion_pre.af_phase = (std::abs(p_38[0]) < 1.0e-12)
            ? af_total : ((dphi - p_38[1]) / p_38[0]);
        motion_pre.af_total = af_total;
        motion_pre.solver_requested = "skipped_disabled";
        motion_pre.solver = "skipped_disabled";
        if (cfg.motion_comp_enable) {
            motion_pre = solveMotionCompensation(
                cfg, plane, dphi, af_total, p_38[0], p_38[1],
                cfg.lambda, Rg, theta_deg, calibrated_range_phase_correction);
        }
        MotionCompResult motion_refit = motion_pre;
        if (cfg.motion_comp_enable && cfg.p38_enhanced_enable && p38_used_source == "refit") {
            motion_refit = solveMotionCompensation(
                cfg, plane, dphi, af_total,
                p38_refit_metrics.p38[0], p38_refit_metrics.p38[1],
                cfg.lambda, Rg, theta_deg,
                calibrated_range_phase_correction);
        }
        MotionCompResult motion =
            (cfg.p38_enhanced_enable && p38_used_source == "refit")
                ? motion_refit : motion_pre;
        af_ransac[i] = motion.af_phase;

        // row_af(i) = round((af_ransac - faAxis(1)) / fd_res) + 1
        // —— 这里改为 0-based：不再 +1
        // row_af[i] = static_cast<int>(std::llround((af_ransac[i] - faAxis.front()) / cfg.fd_res));
        // 支撑域剔除
        // if (row_af[i] < az_st + 10 || row_af[i] > az_ed - 10) {
            // DBG("目标 " << i << " 因超出支撑域被剔除: row_af=" << row_af[i]
            //             << " 有效区间=[" << az_st + 10 << "," << az_ed - 10 << "]");
            // continue;
        // }

        // 几何定位（需要 plane.V, plane.H, plane.E, plane.N；cfg.Rg 为距离轴）
        double lambda_loc = cfg.lambda;
        if (!(lambda_loc > 0.0) && cfg.fc > 0.0) {
            lambda_loc = C / (cfg.fc * 1.0e9);
        }
        const double denom = 2.0 * plane.V;
        const double sinA_old = (denom != 0.0) ? (motion.af_phase * lambda_loc / denom)
                                               : std::numeric_limits<double>::quiet_NaN();
        const double sinA_comp = (denom != 0.0) ? (motion.af_geometry * lambda_loc / denom)
                                                : std::numeric_limits<double>::quiet_NaN();
        const bool old_valid = std::isfinite(sinA_old) && std::abs(sinA_old) <= 1.0;
        const bool comp_valid = cfg.motion_comp_enable && motion.ok &&
                                std::isfinite(sinA_comp) && std::abs(sinA_comp) <= 1.0;
        if (old_valid) ++n_old_valid;
        if (comp_valid) ++n_comp_valid;
        if (!old_valid && comp_valid) ++n_old_invalid_comp_valid;
        if (old_valid && !comp_valid) ++n_old_valid_comp_invalid;
        if (old_valid && comp_valid) ++n_both_valid;
        if (!old_valid && !comp_valid) ++n_both_invalid;

        // The single-period phase/Doppler velocity branch is diagnostic by default.
        // It must not override a valid p38/CTDR inverse position merely because a
        // velocity candidate exists.  Historical behaviour remains opt-in.
        bool use_comp_loc = cfg.motion_comp_enable &&
                            cfg.motion_comp_apply_to_localization &&
                            motion.ok && comp_valid;
        double sinA = use_comp_loc ? sinA_comp : sinA_old;
        const char *loc_used_mode = use_comp_loc
            ? (motion.ambiguity_status == "unresolved" ||
               motion.ambiguity_status == "unresolved_truncated"
                   ? "comp_ambiguity_unresolved_geometry"
                   : "comp")
            : "old";
        const std::string requested_solver = normalizeMotionCompSolver(cfg.motion_comp_solver);
        const char *motion_comp_status = "disabled";
        if (cfg.motion_comp_enable) {
            if (!cfg.motion_comp_apply_to_localization) {
                motion_comp_status = "diagnostic_only";
            } else if (cfg.velocity_ambiguity_enable) {
                motion_comp_status = motion.ok
                    ? (motion.ambiguity_status == "resolved"
                           ? "ambiguity_resolved"
                           : "ambiguity_unresolved_geometry_valid")
                    : "ambiguity_no_candidate";
            } else if (requested_solver == "debug") {
                motion_comp_status = comp_valid
                    ? "debug_analytic_valid"
                    : (motion.fallback_legacy ? "debug_analytic_fallback_old"
                                              : "debug_analytic_invalid");
            } else {
                motion_comp_status = comp_valid
                    ? "analytic_valid"
                    : (motion.fallback_legacy ? "analytic_fallback_old"
                                              : "analytic_invalid");
            }
        }
        const bool used_valid = use_comp_loc ? comp_valid : old_valid;
        if (!used_valid) {
            DBG("目标 " << i << " 当前定位方法 sinA 无效，剔除"
                        << " loc_used_mode=" << loc_used_mode
                        << " sinA_old=" << sinA_old
                        << " sinA_comp=" << sinA_comp
                        << " old_valid=" << old_valid
                        << " comp_valid=" << comp_valid
                        << " af_phase=" << motion.af_phase
                        << " af_total=" << motion.af_total
                        << " af_geometry=" << motion.af_geometry
                        << " af_motion=" << motion.af_motion
                        << " v_radial=" << motion.v_radial
                        << " phi_motion=" << motion.phi_motion);
            continue;
        }
        const double dz = (plane.H - cfg.MT_nowz);
        gmti::sim_geometry::Stage2GeometryConfig geom_cfg;
        geom_cfg.squint_side = cfg.squint_side;
        const double ground_range = gmti::sim_geometry::slantRangeToGroundRange(Rg, plane.H, cfg.MT_nowz, geom_cfg);

        double theta_used_deg = std::numeric_limits<double>::quiet_NaN();
        double look_from_sinA_e = std::numeric_limits<double>::quiet_NaN();
        double look_from_sinA_n = std::numeric_limits<double>::quiet_NaN();
        double xP = std::numeric_limits<double>::quiet_NaN();
        double yP = std::numeric_limits<double>::quiet_NaN();
        project_position_from_sinA_local(plane, cfg, ground_range, sinA,
                                         theta_used_deg, look_from_sinA_e, look_from_sinA_n,
                                         xP, yP);

        double oldXP = std::numeric_limits<double>::quiet_NaN();
        double oldYP = std::numeric_limits<double>::quiet_NaN();
        double theta_old_used_deg = std::numeric_limits<double>::quiet_NaN();
        double look_old_e = std::numeric_limits<double>::quiet_NaN();
        double look_old_n = std::numeric_limits<double>::quiet_NaN();
        if (std::isfinite(sinA_old) && std::abs(sinA_old) <= 1.0) {
            project_position_from_sinA_local(plane, cfg, ground_range, sinA_old,
                                             theta_old_used_deg, look_old_e, look_old_n,
                                             oldXP, oldYP);
        }

        double lat = 0.0, lng = 0.0;               // 由投影坐标反解经纬
        (void)Gaussp3RV(xP, yP, cfg.L0, lat, lng); // 假定返回 bool，忽略失败则保持 0

        double dE = xP - plane.E;
        double dN = yP - plane.N;
        double target_azimuth_deg = normalize_azimuth_deg(gmti::trig_lut::atan2(dN, dE) * 180.0 / M_PI);  // 东为0°, 逆时针为正
        // 相对方向：顺时针为正 => 计算 plane - target，再映射到 [-180,180]
        double direction = wrap180_deg(plane.V_angle - target_azimuth_deg);
        const double beam_center_dir = beam_center_relative_dir_deg(cfg.squint_side, theta_deg);
        const double beam_half_width = location_beam_gate_deg(cfg);
        double beam_dir_err = wrap180_deg(direction - beam_center_dir);
        // Exact-CTDR refinement may improve high-speed localization, but an
        // unresolved velocity branch must never discard a physically valid p38
        // inverse position. Try the p38 candidate before applying the beam gate.
        if (std::abs(beam_dir_err) > beam_half_width && use_comp_loc && old_valid &&
            std::isfinite(oldXP) && std::isfinite(oldYP)) {
            const double oldDE = oldXP - plane.E;
            const double oldDN = oldYP - plane.N;
            const double oldAz = normalize_azimuth_deg(
                gmti::trig_lut::atan2(oldDN, oldDE) * 180.0 / M_PI);
            const double oldDirection = wrap180_deg(plane.V_angle - oldAz);
            const double oldBeamErr = wrap180_deg(oldDirection - beam_center_dir);
            if (std::abs(oldBeamErr) <= beam_half_width) {
                use_comp_loc = false;
                sinA = sinA_old;
                loc_used_mode = "p38_beam_fallback";
                motion_comp_status = "ctdr_outside_beam_fallback_p38";
                theta_used_deg = theta_old_used_deg;
                look_from_sinA_e = look_old_e;
                look_from_sinA_n = look_old_n;
                xP = oldXP;
                yP = oldYP;
                dE = oldDE;
                dN = oldDN;
                target_azimuth_deg = oldAz;
                direction = oldDirection;
                beam_dir_err = oldBeamErr;
                (void)Gaussp3RV(xP, yP, cfg.L0, lat, lng);
            }
        }
        if (std::abs(beam_dir_err) > beam_half_width) {
            DBG("目标 " << i << " 因方向超出波束被剔除: direction=" << direction
                        << " beam_center_dir=" << beam_center_dir
                        << " err=" << beam_dir_err
                        << " gate=" << beam_half_width);
            continue;
        }
        const double range = std::sqrt(dE * dE + dN * dN);
        
        // debug printing removed

        // MT(i,:) = [lat, lng, cfg.MT_nowz, xP, yP, utc, relative_dir, range]
        MT.push_back(lat);
        MT.push_back(lng);
        MT.push_back(cfg.MT_nowz);
        MT.push_back(xP);
        MT.push_back(yP);
        MT.push_back(out.utcMid);
        MT.push_back(direction);
        MT.push_back(range);
        GMTIOutput::DetectionCsvRecord rec;
        rec.period_id = cfg.echo_cycle_view.valid()
            ? static_cast<int>(cfg.echo_cycle_view.acquisition_cycle_id)
            : (cfg.stage2_period_id >= 0
                   ? cfg.stage2_period_id
            : ((cfg.INFO_Type && !cfg.GMTI_Data_new.empty())
                   ? cfg.new_protocol_file_period_index
                   : 0));
        rec.beam_id = cfg.scan_mode == ScanMode::Mechanical ? -1 : periodIdx;
        rec.scan_mode = cfg.scan_mode;
        if (process_window) {
            rec.scan_id = process_window->scan_id;
            rec.window_id = process_window->window_id;
            rec.pulse_start = process_window->pulse_start;
            rec.pulse_end = process_window->pulse_start + process_window->pulse_count - 1U;
            rec.cpi_center_utc = process_window->utc_center;
            rec.az_start_deg = process_window->az_start_deg;
            rec.az_center_deg = process_window->az_center_deg;
            rec.az_end_deg = process_window->az_end_deg;
            rec.az_span_deg = process_window->az_span_deg;
            rec.scan_direction = process_window->scan_direction;
        }
        rec.platform_e = plane.E;
        rec.platform_n = plane.N;
        rec.platform_h = plane.H;
        rec.platform_v = plane.V;
        rec.platform_v_angle_deg = plane.V_angle;
        rec.fd_ctr_wrapped = fa_ctr;
        rec.fd_ctr_unwrapped = unwrap_prf_to_model(
            fa_ctr, cfg.PRF, theta_deg, plane.V, cfg.fc);
        rec.range_bin = c;
        rec.row = r;
        rec.col = c;
        rec.range_m = Rg;
        rec.theta_cmd_deg = theta_sq;
        rec.theta_true_deg = theta_deg;
        rec.e = xP;
        rec.n = yP;
        rec.lat = lat;
        rec.lon = lng;
        rec.utc = out.utcMid;
        rec.radial_velocity_mps = motion.v_radial;
        rec.phase_rad = dphi;
        rec.range_phase_correction_rad = motion.range_phase_correction;
        rec.ctdr_raw_phase_rad = motion.phi_meas_ctdr_raw;
        rec.p38_k = motion.k;
        rec.p38_b = motion.b;
        rec.phi_static_model_rad = motion.phi_static_model;
        rec.phi_static_model_name = motion.phi_static_model_name;
        rec.C_ati = motion.C_ati;
        rec.k_eff_static_phase_df = motion.k_eff_static_phase_df;
        rec.phi_static_rad = motion.phi_static;
        rec.phi_static_total_rad = motion.phi_static_total;
        rec.phi_res_rad = motion.phi_res;
        rec.phi_static_at_zero = motion.phi_static_at_zero;
        rec.phi_res_at_zero = motion.phi_res_at_zero;
        rec.phi_static_geometry_rad = motion.phi_static_geometry;
        rec.af_phase = motion.af_phase;
        rec.af_total = motion.af_total;
        rec.af_geometry = motion.af_geometry;
        rec.af_motion = motion.af_motion;
        rec.phi_motion = motion.phi_motion;
        rec.delta_t_s = motion.delta_t;
        rec.motion_comp_denom = motion.denom;
        rec.denom_without_k = motion.denom_without_k;
        rec.v_from_phase_raw = motion.v_from_phase_raw;
        rec.v_from_phi_res = motion.v_from_phi_res;
        rec.v_old_mps = motion.v_old_mps;
        rec.v_iterative_mps = motion.v_iterative_mps;
        rec.v_analytic_mps = motion.v_analytic_mps;
        rec.v_root1d_mps = motion.v_root1d_mps;
        rec.af_geometry_old_hz = motion.af_geometry_old_hz;
        rec.af_geometry_iterative_hz = motion.af_geometry_iterative_hz;
        rec.af_geometry_analytic_hz = motion.af_geometry_analytic_hz;
        rec.af_geometry_root1d_hz = motion.af_geometry_root1d_hz;
        rec.root1d_cost = motion.root1d_cost;
        rec.af_alias_hz = motion.af_alias_hz;
        rec.af_unwrapped_hz = motion.af_unwrapped_hz;
        rec.doppler_ambiguity_order = motion.doppler_ambiguity_order;
        rec.phase_wrapped_rad = motion.phase_wrapped_rad;
        rec.phase_unwrapped_rad = motion.phase_unwrapped_rad;
        rec.phase_ambiguity_order = motion.phase_ambiguity_order;
        rec.velocity_candidate_count = motion.velocity_candidate_count;
        rec.velocity_best_cost = motion.velocity_best_cost;
        rec.velocity_second_cost = motion.velocity_second_cost;
        rec.velocity_cost_margin = motion.velocity_cost_margin;
        rec.velocity_solver = motion.velocity_solver;
        rec.velocity_confidence = motion.velocity_confidence;
        rec.ambiguity_status = motion.ambiguity_status;
        rec.p38_raw_k = p_38_raw[0];
        rec.p38_raw_b = p_38_raw[1];
        rec.p38_raw_rmse = p38_raw_metrics.rmse;
        rec.p38_pre_k = p_38[0];
        rec.p38_pre_b = p_38[1];
        rec.p38_pre_rmse = p38_pre_metrics.rmse;
        rec.p38_refit_k = p38_refit_metrics.p38[0];
        rec.p38_refit_b = p38_refit_metrics.p38[1];
        rec.p38_refit_rmse = p38_refit_metrics.rmse;
        rec.p38_refit_sample_count = p38_refit_metrics.sample_count;
        rec.p38_refit_inlier_ratio = p38_refit_metrics.inlier_ratio;
        rec.p38_refit_valid = p38_refit_metrics.valid ? 1 : 0;
        rec.p38_used_k = p38_used[0];
        rec.p38_used_b = p38_used[1];
        rec.p38_used_source = p38_used_source;
        rec.phi_static_pre_rad = motion_pre.phi_static_total;
        rec.phi_res_pre_rad = motion_pre.phi_res;
        rec.v_pre_mps = motion_pre.v_radial;
        rec.phi_static_refit_rad = motion_refit.phi_static_total;
        rec.phi_res_refit_rad = motion_refit.phi_res;
        rec.v_refit_mps = motion_refit.v_radial;
        rec.sinA_old = sinA_old;
        rec.sinA_comp = sinA_comp;
        rec.sinA_used = sinA;
        rec.angle_from_sinA_deg = gmti::trig_lut::asin(clamp_unit_local(sinA)) * 180.0 / M_PI;
        rec.theta_used_for_position_deg = theta_used_deg;
        rec.look_from_sinA_e = look_from_sinA_e;
        rec.look_from_sinA_n = look_from_sinA_n;
        rec.look_e_diff = look_from_sinA_e - ((ground_range > 1.0e-9) ? (xP - plane.E) / ground_range : 0.0);
        rec.look_n_diff = look_from_sinA_n - ((ground_range > 1.0e-9) ? (yP - plane.N) / ground_range : 0.0);
        rec.old_e = oldXP;
        rec.old_n = oldYP;
        rec.new_e = xP;
        rec.new_n = yP;
        rec.old_valid = old_valid ? 1 : 0;
        rec.comp_valid = comp_valid ? 1 : 0;
        rec.old_invalid_comp_valid = (!old_valid && comp_valid) ? 1 : 0;
        rec.motion_comp_valid = motion.ok ? 1 : 0;
        rec.motion_comp_enable = cfg.motion_comp_enable ? 1 : 0;
        rec.motion_comp_used = motion.used_motion_comp ? 1 : 0;
        rec.motion_comp_fallback = motion.fallback_legacy ? 1 : 0;
        rec.p38_theory_sign = motion.p38_theory_sign;
        rec.motion_doppler_axis_sign = motion.motion_doppler_axis_sign;
        rec.ati_phase_to_velocity_sign = motion.ati_phase_to_velocity_sign;
        rec.p38_mode = motion.p38_mode;
        rec.geometry_calib_mode = motion.geometry_calib_mode;
        rec.loc_used_mode = loc_used_mode;
        rec.motion_comp_status = motion_comp_status;
        rec.motion_comp_solver = motion.solver;
        out.detection_records.push_back(rec);
        ++n_output;
        DBG("目标 " << i << " 定位成功: Lat=" << lat << " Lng=" << lng);
    }

    if (cfg.runtime_diagnostics_enabled && cfg.motion_comp_debug) {
        std::cout << "[motion_comp][loc][period=" << periodIdx
                  << "] n_selected=" << n_selected
                  << " n_old_valid=" << n_old_valid
                  << " n_comp_valid=" << n_comp_valid
                  << " n_old_invalid_comp_valid=" << n_old_invalid_comp_valid
                  << " n_old_valid_comp_invalid=" << n_old_valid_comp_invalid
                  << " n_both_valid=" << n_both_valid
                  << " n_both_invalid=" << n_both_invalid
                  << " n_output=" << n_output << std::endl;
    }

    out.detect = targetSel;
    out.MT = MT;

    if (!targetDetection)
    {
        std::cerr << "动目标定位失败。" << std::endl;
        return false;
    }
//    DBG("动目标定位成功");

    return true; // 所有处理成功，返回 true
}

bool GMTIProcessor::processOnePeriodFusionCache(int periodIdx,
                                                const Config &cfg_,
                                                const std::vector<std::vector<double>> &posRaw,
                                                size_t slot,
                                                FusionGroupContext &ctx)
{
    Config cfg = cfg_;
    const MechanicalCpiWindow *process_window = mechanicalWindow(cfg, periodIdx);
    const auto failBeamStage = [&cfg, periodIdx, slot](const char *stage) -> bool {
        if (cfg.runtime_diagnostics_enabled) {
            std::cerr << "[fusion][BEAM-ERR] beam=" << periodIdx
                      << " slot=" << slot
                      << " stage=" << stage << std::endl;
        }
        return false;
    };
    const std::string timing_beam_extra = "beam_id=" + std::to_string(periodIdx);
    std::vector<std::complex<float>> data1, data2;
    NewProtocolGpuInput new_protocol_gpu_input;
    const bool use_packed_new_protocol_gpu_input =
        cfg.INFO_Type && !cfg.isPC && cfg.new_protocol_gpu_preprocess;
    std::vector<double> utc;
    std::vector<std::vector<double>> echoPosRaw;
    double theta_sq = 0.0;

    bool readSuccess = false;
    {
        gmti::runtime::TimingScope timing_data_read("data_read", 0, timing_beam_extra);
        if (cfg.INFO_Type) {
            if (use_packed_new_protocol_gpu_input) {
                readSuccess = readPulseBlockNewProtocolGpuInput(
                    cfg, periodIdx, new_protocol_gpu_input,
                    utc, theta_sq, echoPosRaw);
            } else {
                readSuccess = readPulseBlockNewProtocol(
                    cfg, periodIdx, data1, data2, utc, theta_sq, echoPosRaw);
            }
        } else {
            readSuccess = readPulseBlock(cfg, periodIdx, data1, data2, utc, theta_sq);
        }
    }
    if (!readSuccess) {
        return failBeamStage("read_input");
    }
    if (process_window) {
        theta_sq = process_window->az_center_deg;
    }
    if (cfg.scan_mode == ScanMode::Mechanical && std::isfinite(theta_sq)) {
        cfg.ctdr_servo_azimuth_deg = theta_sq;
    }
    if (cfg.INFO_Type) {
        cfg.process_pulse_num = static_cast<int>(utc.size());
    }

    bool data_on_gpu = false;
    {
        gmti::runtime::TimingScope timing_pulse_compression("pulse_compression", 0, timing_beam_extra);
        if (!cfg.isPC) {
            // P1.5 debug needs a stable host-side matrix immediately after pulse compression.
            // Keep the normal GPU-resident path unchanged when debug_pc_peak is disabled.
            data_on_gpu = use_packed_new_protocol_gpu_input
                ? pulseCompressionGpuResident(new_protocol_gpu_input, cfg)
                : pulseCompressionGpuResident(data1, data2, cfg);
            bool pc_ok = data_on_gpu;
            if (!pc_ok) {
                if (use_packed_new_protocol_gpu_input &&
                    !decodeNewProtocolGpuInputCpu(
                        cfg, new_protocol_gpu_input, data1, data2)) {
                    return failBeamStage("new_protocol_cpu_fallback_decode");
                }
                pc_ok = pulseCompression(data1, data2, cfg);
            }
            if (!pc_ok) {
                return failBeamStage("pulse_compression");
            }
        } else {
            const int Lraw = cfg.pulse_len;
            const int W = effectivePulseNum(cfg);
            const int M1 = cfg.rg_len;
            const int Lraw2M = Lraw / M1;
            std::vector<std::complex<float>> rc_out((size_t)W * M1);
            for (int k = 0; k < W; ++k) {
                for (int m = 0; m < M1; ++m) {
                    rc_out[(size_t)k * M1 + m] = data1[(size_t)k * Lraw + m * Lraw2M];
                }
            }
            data1.swap(rc_out);

            rc_out.assign((size_t)W * M1, std::complex<float>(0.0f, 0.0f));
            for (int k = 0; k < W; ++k) {
                for (int m = 0; m < M1; ++m) {
                    rc_out[(size_t)k * M1 + m] = data2[(size_t)k * Lraw + m * Lraw2M];
                }
            }
            data2.swap(rc_out);
        }
    }

    if (!usesRangeCropWindow(cfg)) {
        cfg.fs = cfg.fs * cfg.rg_len / cfg.pulse_len;
    }
    cfg.pulse_len = cfg.rg_len;

    const int W_orig = effectivePulseNum(cfg);
    const int M = cfg.rg_len;
    const int dec = cfg.pulse_dec;
    if (dec <= 0 || W_orig % dec != 0) {
        return failBeamStage("azimuth_decimation_config");
    }
    const int W_new = W_orig / dec;
    if (data_on_gpu) {
        if (!cuda_stage_az_decimate_async(W_orig, M, dec)) {
            return failBeamStage("azimuth_decimation_cuda");
        }
        data1.clear();
        data2.clear();
        data1.shrink_to_fit();
        data2.shrink_to_fit();
    } else if (W_new != W_orig) {
        std::vector<std::complex<float>> tmp((size_t)W_new * M);
        for (int k_new = 0; k_new < W_new; ++k_new) {
            const size_t out_base = (size_t)k_new * M;
            const size_t in_base = (size_t)(dec * k_new) * M;
            for (int m = 0; m < M; ++m) {
                std::complex<float> acc = 0.0f;
                for (int d = 0; d < dec; ++d) {
                    acc += data1[in_base + (size_t)d * M + m];
                }
                tmp[out_base + m] = acc;
            }
        }
        data1.swap(tmp);

        tmp.assign((size_t)W_new * M, std::complex<float>(0.0f, 0.0f));
        for (int k_new = 0; k_new < W_new; ++k_new) {
            const size_t out_base = (size_t)k_new * M;
            const size_t in_base = (size_t)(dec * k_new) * M;
            for (int m = 0; m < M; ++m) {
                std::complex<float> acc = 0.0f;
                for (int d = 0; d < dec; ++d) {
                    acc += data2[in_base + (size_t)d * M + m];
                }
                tmp[out_base + m] = acc;
            }
        }
        data2.swap(tmp);
    }

    if (W_new != W_orig) {
        std::vector<double> utc_tmp(W_new);
        for (int k_new = 0; k_new < W_new; ++k_new) {
            double acc = 0.0;
            for (int d = 0; d < dec; ++d) {
                acc += utc[dec * k_new + d];
            }
            utc_tmp[k_new] = acc / static_cast<double>(dec);
        }
        utc.swap(utc_tmp);
        cfg.process_pulse_num = W_new;
        cfg.PRF = cfg.PRF / dec;
    }

    double utc_mean = 0.0;
    for (double t : utc) {
        utc_mean += t;
    }
    if (!utc.empty()) {
        utc_mean /= static_cast<double>(utc.size());
    }
    for (double &t : utc) {
        if (utc_mean - t > 0.6) {
            t += 1.0;
        } else if (utc_mean - t < -0.6) {
            t -= 1.0;
        }
    }

    const double coef = cfg.calib_coef;
    if (data_on_gpu) {
        if (!cuda_scale_channel2_async(static_cast<float>(coef),
                                       static_cast<size_t>(effectivePulseNum(cfg)) * static_cast<size_t>(cfg.rg_len))) {
            return failBeamStage("channel_gain_calibration");
        }
        if (pcProfileEnvEnabled() && pcProfileBeamEnabled(periodIdx)) {
            std::vector<std::complex<float>> pc1, pc2;
            const size_t total = static_cast<size_t>(effectivePulseNum(cfg)) * static_cast<size_t>(cfg.rg_len);
            pc1.resize(total);
            pc2.resize(total);
            cudaMemcpyAsync(pc1.data(), gpu_ptrs_.d1, total * sizeof(cd), cudaMemcpyDeviceToHost, stream_compute_);
            cudaMemcpyAsync(pc2.data(), gpu_ptrs_.d2, total * sizeof(cd), cudaMemcpyDeviceToHost, stream_compute_);
            cudaStreamSynchronize(stream_compute_);
            writeManualPcProfile(cfg, periodIdx, pc1, pc2);
        }
    } else {
        for (auto &v : data2) {
            v *= coef;
        }
        writeManualPcProfile(cfg, periodIdx, data1, data2);
    }

    GMTIOutput::Plane plane;
    if (cfg.Loc) {
        const std::vector<std::vector<double>> &planePosSource = cfg.INFO_Type ? echoPosRaw : posRaw;
        if (!extractPlanePos(utc, planePosSource, cfg, plane, periodIdx)) {
            return failBeamStage("plane_position");
        }
    } else {
        plane.V = 40.0 / 3.6;
        plane.E = plane.N = 0.0;
        plane.H = cfg.MT_nowz;
        plane.V_angle = 0.0;
    }
    cfg.p38_expected_slope_rad_per_hz = p38TheorySlopeLocal(cfg, plane.V);

    std::vector<double> faAxis;
    double fa_ctr = 0.0;
    if (data_on_gpu) {
        if (!cuda_compute_fd_ctr_from_d1(1, cfg, fa_ctr)) {
            return failBeamStage("doppler_center");
        }
        const size_t axisN = static_cast<size_t>(effectivePulseNum(cfg));
        faAxis.resize(axisN);
        const double df = (axisN > 0) ? (cfg.PRF / static_cast<double>(axisN)) : 0.0;
        for (size_t i = 0; i < axisN; ++i) {
            faAxis[i] = -0.5 * cfg.PRF + static_cast<double>(i) * df + fa_ctr;
        }
    } else if (!computeDoppler(data1, 1, cfg, faAxis, fa_ctr,
                               plane.V, theta_sq)) {
        return failBeamStage("doppler_center_cpu");
    }
    if (!applyDopplerCenterOverride(cfg, fa_ctr)) {
        applyDopplerCenterTheoryGuard(cfg, plane, theta_sq, fa_ctr);
    }
    const double angle_deg = GMTIProcessor::estimateSquintAngleDeg(plane, cfg, fa_ctr);
    const double theta_deg = cfg.scan_mode == ScanMode::Mechanical
        ? angle_deg : angle_deg + theta_sq;
    const size_t axisN = faAxis.size();
    const double df = (axisN > 0) ? (cfg.PRF / static_cast<double>(axisN)) : 0.0;
    for (size_t i = 0; i < axisN; ++i) {
        faAxis[i] = -0.5 * cfg.PRF + static_cast<double>(i) * df + fa_ctr;
    }
    // std::cout << "[fusion][fd] beam=" << periodIdx
    //           << " theta_sq=" << theta_sq
    //           << " angle_from_fd=" << angle_deg
    //           << " theta_for_support=" << theta_deg
    //           << " fd_wrapped=" << fa_ctr
    //           << " PRF=" << cfg.PRF << std::endl;

    int az_center = 0, az_st = 0, az_ed = 0, rg_st = 0, rg_ed = 0;
    double fd_st = 0.0, fd_ed = 0.0, BW_az = 0.0;
    if (!computeDynamicSupportDomain(faAxis, fa_ctr, plane, cfg,
                                     az_center, az_st, az_ed,
                                     fd_st, fd_ed, BW_az,
                                     rg_st, rg_ed)) {
        return failBeamStage("dynamic_support");
    }
    cfg.az_center = az_center;
    cfg.az_st = az_st;
    cfg.az_ed = az_ed;
    cfg.rg_st = rg_st;
    cfg.rg_ed = rg_ed;
    DBG("[fusion] 动态支撑域: beam_half_deg=" << 0.5 * cfg.beamwidth_deg
        << " support_half_deg="
        << std::max(2.0, 0.5 * cfg.beamwidth_deg)
        << " fd_st=" << fd_st << " fd_ed=" << fd_ed
        << " BW_az=" << BW_az
        << " az_st=" << az_st << " az_ed=" << az_ed
        << " rg_st=" << rg_st << " rg_ed=" << rg_ed);

    [[maybe_unused]] const double equivalent_two_way_spacing_m =
        gmti::ctdr::equivalentTwoWayPhaseCenterSeparation(cfg.d_channel);
    [[maybe_unused]] const double equivalent_two_way_delay_s =
        configuredCtdrEquivalentTwoWayDelay(cfg, plane.V);
    const int skipInt_theory = configuredCsiChannelAlignmentPrt(cfg, plane.V);
    const int skipInt = 0;
    DBG("[fusion] CTDR CSI 对齐: mode=" << cfg.csi_channel_alignment_mode
        << " rx_baseline_m=" << cfg.d_channel
        << " equivalent_two_way_spacing_m=" << equivalent_two_way_spacing_m
        << " equivalent_two_way_delay_s=" << equivalent_two_way_delay_s
        << " integer_prt_shift=" << skipInt_theory
        << " raw_p38_theory_rad_per_hz="
        << p38TheorySlopeLocal(cfg, plane.V)
        << " aligned_residual_theory_rad_per_hz="
        << p38TheorySlopeAfterIntegerAlignment(cfg, plane.V, skipInt_theory));

    const size_t Na = static_cast<size_t>(effectivePulseNum(cfg));
    const size_t Nr = static_cast<size_t>(cfg.rg_len);
    FusionBeamMeta beamMeta;
    std::array<float, 2> p_38;
    std::array<float, 2> p_38_refit;
    std::vector<float> phase_map;
    std::vector<float> phase_map_rg_correction;
    std::vector<float> power_map;
    std::vector<float> threshold_map;
    std::vector<std::complex<float>> detection_complex_map;
    std::array<float, 2> p_38_raw = {
        std::numeric_limits<float>::quiet_NaN(),
        std::numeric_limits<float>::quiet_NaN()
    };
    std::vector<double> p38_raw_trace;
    std::vector<double> p38_pre_trace;
    std::vector<double> p38_refit_trace;
    std::string p38_raw_model_source = "not_run";
    std::string p38_pre_model_source = "not_run";
    std::string p38_refit_model_source = "not_run";
    std::array<float, 2> p_38_aligned = {
        std::numeric_limits<float>::quiet_NaN(),
        std::numeric_limits<float>::quiet_NaN()
    };
    std::vector<double> p38_aligned_trace;
    std::vector<double> p38_aligned_row_fa;
    std::vector<std::complex<float>> F1_pre;
    std::vector<std::complex<float>> F2_pre;
    {
        gmti::runtime::TimingScope timing_channel_cancellation("channel_cancellation", 0, timing_beam_extra);
        if (!data_on_gpu) {
            if (!cuda_upload_async(data1, data2, Na, Nr)) {
                return failBeamStage("cuda_upload");
            }
        }
        if (!cuda_stage_align_async(skipInt, Na, Nr, cfg)) {
            return failBeamStage("raw_align");
        }
        if (!cuda_stage_fft_async(Na, Nr)) {
            return failBeamStage("raw_fft");
        }
        if (!cuda_stage_dbs_async(static_cast<float>(fa_ctr), static_cast<float>(cfg.PRF), Na, Nr)) {
            return failBeamStage("raw_dbs");
        }
        cfg.p38_expected_slope_rad_per_hz =
            p38TheorySlopeAfterIntegerAlignment(cfg, plane.V, skipInt);

        beamMeta.beam_index = periodIdx;
        beamMeta.slot = static_cast<int>(slot);
        beamMeta.scan_mode = cfg.scan_mode;
        if (process_window) {
            beamMeta.scan_id = process_window->scan_id;
            beamMeta.window_id = process_window->window_id;
            beamMeta.pulse_start = process_window->pulse_start;
            beamMeta.pulse_count = process_window->pulse_count;
            beamMeta.az_start_deg = process_window->az_start_deg;
            beamMeta.az_center_deg = process_window->az_center_deg;
            beamMeta.az_end_deg = process_window->az_end_deg;
            beamMeta.az_span_deg = process_window->az_span_deg;
            beamMeta.scan_direction = process_window->scan_direction;
        }
        beamMeta.theta_sq = theta_sq;
        beamMeta.theta_true = theta_sq;
        beamMeta.fd_ctr_wrapped = fa_ctr;
        beamMeta.fd_ctr_unwrapped = fa_ctr;
        beamMeta.utc_mid = utc.empty() ? 0.0 : utc[utc.size() / 2];
        beamMeta.plane = plane;
        beamMeta.PRF = cfg.PRF;
        beamMeta.fc_hz = cfg.fc;
        beamMeta.lambda = (cfg.lambda > 0.0) ? cfg.lambda : (C / cfg.fc); 

        if (!exportDbsCacheAfterRecenter(cfg, beamMeta, slot, Na, Nr, ctx.rd, ctx.meta)) {
            return failBeamStage("dbs_cache_export");
        }

        const double thre_rg = 0.1 * M_PI;
        std::vector<float> phi_fit;
        std::vector<double> phi_diss_phase;
        std::vector<int> phi_diss_range;
        std::vector<float> faAxis_f(faAxis.begin(), faAxis.end());
        std::array<float, 2> p_38_raw_f;
        std::vector<float> p38_raw_trace_f;
        if (!clutter_cancel_38_paper_1_p38_cuda(
                faAxis_f,
                cfg.az_st, cfg.rg_st, cfg.az_ed, cfg.rg_ed,
                cfg,
                p_38_raw_f,
                &p38_raw_trace_f,
                &p38_raw_model_source)) {
            return failBeamStage("p38_raw");
        }
        p_38_raw = p_38_raw_f;
        p38_raw_trace.assign(p38_raw_trace_f.begin(), p38_raw_trace_f.end());

        if (!cfg.paired_raw_range_phase_override_f32.empty()) {
            if (!loadPairedRangePhaseOverride(cfg.paired_raw_range_phase_override_f32,
                                               Nr, phi_fit)) {
                return failBeamStage("range_phase_reference_raw");
            }
        } else if (!rg_correct_CUDA(cfg, thre_rg, phi_fit, phi_diss_phase, phi_diss_range)) {
            return failBeamStage("range_phase_correction_raw");
        }
        if (!cuda_apply_rg_correction_async(phi_fit, Na, Nr)) {
            return failBeamStage("range_phase_correction_apply_raw");
        }
        phase_map_rg_correction = phi_fit;
        writePairedRangePhaseReference(cfg, periodIdx, periodIdx, "raw", phi_fit);

        std::array<float, 2> p_38_f;
        std::vector<float> p38_pre_trace_f;
        if (!clutter_cancel_38_paper_1_p38_cuda(
                faAxis_f,
                cfg.az_st, cfg.rg_st, cfg.az_ed, cfg.rg_ed,
                cfg,
                p_38_f,
                &p38_pre_trace_f,
                &p38_pre_model_source)) {
            return failBeamStage("p38_pre");
        }
        p_38 = {p_38_f[0], p_38_f[1]};
        p38_pre_trace.assign(p38_pre_trace_f.begin(), p38_pre_trace_f.end());

        if (!cuda_capture_phase_map_async(Na * Nr)) {
            return failBeamStage("phase_map_capture");
        }
        if (cfg.p38_enhanced_enable && cfg.p38_refit_enable &&
            !cuda_download_phase_map(phase_map, Na * Nr)) {
            return failBeamStage("phase_map_download");
        }
        const bool need_pre_csi_host_copy =
            (cfg.p38_enhanced_enable && cfg.p38_refit_enable) ||
            cfg.channel_calibration_enable || cfg.csi_metrics_enable;
        if (need_pre_csi_host_copy &&
            !cuda_download_sync(F1_pre, F2_pre, Na * Nr)) {
            return failBeamStage("pre_csi_download");
        }

        if (!cuda_stage_align_async(skipInt_theory, Na, Nr, cfg)) {
            return failBeamStage("csi_align");
        }
        if (!cuda_stage_fft_async(Na, Nr)) {
            return failBeamStage("csi_fft");
        }
        if (!cuda_stage_dbs_async(static_cast<float>(fa_ctr), static_cast<float>(cfg.PRF), Na, Nr)) {
            return failBeamStage("csi_dbs");
        }
        if (!cfg.csi_range_phase_correction_enable) {
            phi_fit.assign(Nr, 0.0f);
        } else {
            if (!cfg.paired_csi_range_phase_override_f32.empty()) {
                if (!loadPairedRangePhaseOverride(cfg.paired_csi_range_phase_override_f32,
                                                   Nr, phi_fit)) {
                    return failBeamStage("range_phase_reference_csi");
                }
            } else if (!rg_correct_CUDA(cfg, thre_rg, phi_fit, phi_diss_phase, phi_diss_range)) {
                return failBeamStage("range_phase_correction_csi");
            }
            if (!cuda_apply_rg_correction_async(phi_fit, Na, Nr)) {
                return failBeamStage("range_phase_correction_apply_csi");
            }
        }
        writePairedRangePhaseReference(cfg, periodIdx, periodIdx, "csi", phi_fit);

        std::array<float, 2> p_38_csi_f;
        std::vector<float> ph_trace_f;
        std::vector<float> fa_cut_f;
        std::vector<std::complex<float>> csi_trace_f;
        Config csi_fit_cfg = cfg;
        csi_fit_cfg.p38_expected_slope_rad_per_hz =
            p38TheorySlopeAfterIntegerAlignment(cfg, plane.V, skipInt_theory);
        if (!clutter_cancel_38_paper_1_cuda(
                faAxis_f,
                cfg.az_st, cfg.rg_st, cfg.az_ed, cfg.rg_ed,
                csi_fit_cfg,
                csi_trace_f, p_38_csi_f, ph_trace_f, fa_cut_f)) {
            return failBeamStage("p38_csi");
        }
        p_38_aligned = p_38_csi_f;
        p38_aligned_trace.assign(ph_trace_f.begin(), ph_trace_f.end());
        p38_aligned_row_fa.assign(fa_cut_f.begin(), fa_cut_f.end());
        if (cfg.csi_metrics_enable &&
            (cfg.csi_metrics_beam_id < 0 || cfg.csi_metrics_beam_id == periodIdx)) {
            gmti::runtime::TimingScope timing_csi_metrics(
                "csi_metrics", periodIdx, timing_beam_extra);
            std::vector<std::complex<float> > csi_before_ch1;
            std::vector<std::complex<float> > csi_before_ch2;
            if (!cuda_download_sync(csi_before_ch1, csi_before_ch2, Na * Nr)) {
                std::cerr << "[CSI_METRICS][ERR] failed to download production CSI inputs"
                          << std::endl;
                return false;
            }
            std::string metrics_error;
            const int metrics_period_id = cfg.stage2_period_id >= 0
                ? cfg.stage2_period_id : cfg.new_protocol_file_period_index;
            if (!gmti::metrics::writeCsiMetricsTap(
                    cfg, metrics_period_id, periodIdx, static_cast<int>(Na), static_cast<int>(Nr),
                    cfg.az_st, cfg.az_ed, cfg.rg_st, cfg.rg_ed,
                    faAxis_f, cfg.Rg, csi_before_ch1, csi_before_ch2,
                    F1_pre, F2_pre, csi_trace_f,
                    p_38_csi_f, metrics_error)) {
                std::cerr << "[CSI_METRICS][ERR] " << metrics_error << std::endl;
                return false;
            }
        }
    }

    const int dynamic_band_st =
        std::max(0, std::min(az_st, effectivePulseNum(cfg) - 1));
    const int dynamic_band_ed =
        std::max(0, std::min(az_ed, effectivePulseNum(cfg) - 1));
    const bool full_csi_detection_band =
        cfg.csi_detection_band_mode == "full";
    const bool union_csi_detection_band =
        cfg.csi_detection_band_mode == "union";
    const bool split_csi_detection_band =
        cfg.csi_detection_band_mode == "split";
    const int band_st = full_csi_detection_band ? 0 : dynamic_band_st;
    const int band_ed = full_csi_detection_band
        ? effectivePulseNum(cfg) - 1 : dynamic_band_ed;
    if (cfg.runtime_diagnostics_enabled) {
        std::cout << "[CFAR][CSI-BAND] mode=" << cfg.csi_detection_band_mode
                  << " dynamic=[" << dynamic_band_st << ',' << dynamic_band_ed
                  << "] applied="
                  << (split_csi_detection_band
                          ? "CSI/in-band + channel2/out-of-band (independent CFAR)"
                          : union_csi_detection_band ? "dynamic+full" :
                      ("[" + std::to_string(band_st) + "," +
                       std::to_string(band_ed) + "]"))
                  << std::endl;
    }
    size_t cfar_hits = 0;
    std::vector<int> prow_new, pcol_new;
    std::vector<float> refined_mydata;
    GMTIOutput::Detect targetSel;
    StrongSmallClusterFilterStats cluster_filter_stats;
    bool cfar_power_resident_for_output = false;
    {
        gmti::runtime::TimingScope timing_detection("detection", 0, timing_beam_extra);
        std::vector<float> mydata;
        std::vector<std::complex<float>> CSI_out;
        bool cfar_ok = false;
        bool gpu_cfar_resident = false;
        std::size_t gpu_cfar_hit_count = 0U;
        bool split_cluster_complete = false;
        if (split_csi_detection_band) {
            const int guard = cfg.csi_split_boundary_guard_rows;
            const int csi_cut_st = std::max(0, dynamic_band_st - guard);
            const int csi_cut_ed = std::min(effectivePulseNum(cfg) - 1,
                                            dynamic_band_ed + guard);
            // The CFAR row arguments define the cut-band mask, not an
            // evaluation ROI. Keep the guard overlap in the two branches,
            // but use the mixed detector input for the in-band branch so its
            // in-band sensitivity matches dynamic mode. Guard-row in-band
            // hits are discarded after clustering; outside the actual
            // support, only the independent original-channel branch owns
            // the candidate.
            const int original_cut_st = std::min(
                effectivePulseNum(cfg) - 1, dynamic_band_st + guard + 1);
            const int original_cut_ed = std::max(
                0, dynamic_band_ed - guard - 1);
            std::vector<float> in_hits, out_hits, in_power, out_power;
            std::vector<float> in_threshold, out_threshold;
            std::vector<std::complex<float> > in_complex, out_complex;
            std::size_t in_hit_count = 0U, out_hit_count = 0U;
            const bool dump_complex = cfg.runtime_diagnostics_enabled &&
                                      cfarDumpSelectedBeam(periodIdx);
            const bool in_ok = dpca_cfar2_fast_cuda(
                CSI_out, csi_cut_st, csi_cut_ed, cfg.pf,
                cfg.cfar_guard_cells, cfg.cfar_background_cells,
                cfg.csi_split_in_band_cfar_type, cfg, in_hits, &in_power,
                dump_complex ? &in_complex : nullptr,
                &in_hit_count, true, 0, 1, &in_threshold);
            const bool out_ok = dpca_cfar2_fast_cuda(
                CSI_out, original_cut_st, original_cut_ed, cfg.pf,
                cfg.cfar_guard_cells, cfg.cfar_background_cells,
                cfg.cfar_type, cfg, out_hits, &out_power,
                dump_complex ? &out_complex : nullptr,
                &out_hit_count, true, 2, 2, &out_threshold);
            cfar_ok = in_ok && out_ok && in_hits.size() == out_hits.size() &&
                      in_power.size() == out_power.size() &&
                      (!dump_complex ||
                       (in_complex.size() == in_power.size() &&
                        out_complex.size() == out_power.size()));
            if (cfar_ok) {
                auto cluster_branch = [&](const std::vector<float>& hits,
                                          const std::vector<float>& power,
                                          std::vector<int>& branch_rows,
                                          std::vector<int>& branch_cols,
                                          StrongSmallClusterFilterStats& stats,
                                          bool phase_filter_enable,
                                          bool strong_small_enable,
                                          bool out_of_band_branch) {
                    Config branch_cfg = cfg;
                    const bool small_recovery_enable =
                        cfg.csi_split_small_cluster_enable;
                    branch_cfg.cluster_strong_small_enable = strong_small_enable;
                    const int branch_min_points =
                        (strong_small_enable || small_recovery_enable)
                        ? std::min(cfg.min_points,
                                   small_recovery_enable
                                       ? cfg.csi_split_small_cluster_min_points
                                       : cfg.cluster_strong_small_min_points)
                        : cfg.min_points;
                    std::vector<float> refined;
                    std::vector<float> phase_std;
                    bool strong_applied = false;
                    const float phase_limit = phase_filter_enable
                        ? static_cast<float>(
                              out_of_band_branch
                                  ? cfg.csi_split_out_of_band_phase_max_std_rad
                                  : cfg.cluster_max_phase_std_rad)
                        : 0.0f;
                    const bool ok = cluster_filter_gap_phase_cuda(
                        hits, phase_map, branch_min_points,
                        cfg.cluster_max_range_gap,
                        phase_limit,
                        branch_cfg, refined, branch_rows, branch_cols, phase_std,
                        false, false, &stats.global_median_power,
                        &stats.baseline_kept, &stats.strong_small_kept,
                        &stats.small_rejected, &strong_applied);
                    if (!ok) return false;
                    if (strong_small_enable) {
                        stats = filterStrongSmallClusters(
                            hits, power, effectivePulseNum(cfg), cfg.rg_len,
                            cfg.cluster_max_range_gap, branch_cfg, branch_rows,
                            branch_cols, phase_std);
                    } else if (small_recovery_enable) {
                        filterSplitSmallComponents(
                            hits, power, effectivePulseNum(cfg), cfg.rg_len,
                            cfg, branch_rows, branch_cols, phase_std,
                            &stats.strong_small_kept, &stats.small_rejected);
                    }
                    if (out_of_band_branch) {
                        stats.vertical_removed += filterVerticalLineClusters(
                            hits, power, effectivePulseNum(cfg), cfg.rg_len, cfg,
                            branch_rows, branch_cols, phase_std);
                    }
                    return ok;
                };
                std::vector<int> in_rows, in_cols, out_rows, out_cols;
                StrongSmallClusterFilterStats in_stats, out_stats;
                cfar_ok = cluster_branch(
                              in_hits, in_power, in_rows, in_cols, in_stats, true, false, false) &&
                          cluster_branch(
                              out_hits, out_power, out_rows, out_cols, out_stats,
                              cfg.csi_split_out_of_band_phase_filter_enable,
                              cfg.cluster_strong_small_enable, true);
                if (cfar_ok) {
                    size_t kept = 0U;
                    for (size_t i = 0; i < in_rows.size(); ++i) {
                        if (in_rows[i] < dynamic_band_st ||
                            in_rows[i] > dynamic_band_ed) continue;
                        in_rows[kept] = in_rows[i];
                        in_cols[kept] = in_cols[i];
                        ++kept;
                    }
                    in_rows.resize(kept);
                    in_cols.resize(kept);
                    prow_new = in_rows;
                    pcol_new = in_cols;
                    const int H = effectivePulseNum(cfg);
                    auto circular_row_distance = [H](int a, int b) {
                        const int d = std::abs(a - b);
                        return std::min(d, H - d);
                    };
                    std::size_t overlap_duplicates = 0U;
                    for (std::size_t i = 0; i < out_rows.size(); ++i) {
                        int duplicate = -1;
                        for (std::size_t j = 0; j < prow_new.size(); ++j) {
                            if (circular_row_distance(out_rows[i], prow_new[j]) <=
                                    cfg.csi_split_merge_doppler_bins &&
                                std::abs(out_cols[i] - pcol_new[j]) <=
                                    cfg.csi_split_merge_range_bins) {
                                duplicate = static_cast<int>(j);
                                break;
                            }
                        }
                        if (duplicate < 0) {
                            prow_new.push_back(out_rows[i]);
                            pcol_new.push_back(out_cols[i]);
                        } else {
                            // Do not compare raw CSI and original-channel power:
                            // cancellation changes their absolute scales.  The
                            // overlap is only a boundary anti-miss guard.  Keep
                            // the branch that owns the physical row: CSI inside
                            // the clutter support, original channel outside it.
                            if (out_rows[i] < dynamic_band_st ||
                                out_rows[i] > dynamic_band_ed) {
                                prow_new[duplicate] = out_rows[i];
                                pcol_new[duplicate] = out_cols[i];
                            }
                            ++overlap_duplicates;
                        }
                }
                mydata.resize(in_hits.size());
                power_map.resize(in_power.size());
                threshold_map.resize(in_power.size());
                if (dump_complex) detection_complex_map.resize(in_power.size());
                for (std::size_t i = 0; i < mydata.size(); ++i) {
                    mydata[i] = std::max(in_hits[i], out_hits[i]);
                    const int row = static_cast<int>(i / Nr);
                    power_map[i] = row >= dynamic_band_st && row <= dynamic_band_ed
                        ? in_power[i] : out_power[i];
                    threshold_map[i] = row >= dynamic_band_st && row <= dynamic_band_ed
                        ? in_threshold[i] : out_threshold[i];
                    if (dump_complex) {
                        detection_complex_map[i] =
                            row >= dynamic_band_st && row <= dynamic_band_ed
                                ? in_complex[i] : out_complex[i];
                    }
                }
                    cfar_hits = in_hit_count + out_hit_count;
                    gpu_cfar_hit_count = cfar_hits;
                    cluster_filter_stats.baseline_kept =
                        in_stats.baseline_kept + out_stats.baseline_kept;
                    cluster_filter_stats.strong_small_kept =
                        in_stats.strong_small_kept + out_stats.strong_small_kept;
                    cluster_filter_stats.small_rejected =
                        in_stats.small_rejected + out_stats.small_rejected;
                    cluster_filter_stats.vertical_removed =
                        in_stats.vertical_removed + out_stats.vertical_removed;
                    split_cluster_complete = true;
                    std::cout << "[CFAR][SPLIT] beam=" << periodIdx
                              << " csi_cut=[" << csi_cut_st << ',' << csi_cut_ed << ']'
                              << " in_eval_rows=[" << dynamic_band_st << ','
                              << dynamic_band_ed << "] out_cut_band=["
                              << original_cut_st << ',' << original_cut_ed << ']'
                              << " in_hits=" << in_hit_count
                              << " out_hits=" << out_hit_count
                              << " in_clusters=" << in_rows.size()
                              << " out_clusters=" << out_rows.size()
                              << " in_cfar=" << cfg.csi_split_in_band_cfar_type
                              << " out_cfar=" << cfg.cfar_type
                              << " out_phase_filter="
                              << (cfg.csi_split_out_of_band_phase_filter_enable ? 1 : 0)
                              << " vertical_removed="
                              << cluster_filter_stats.vertical_removed
                              << " small_recovered="
                              << cluster_filter_stats.strong_small_kept
                              << " small_rejected="
                              << cluster_filter_stats.small_rejected
                              << " overlap_duplicates=" << overlap_duplicates
                              << " merged_clusters=" << prow_new.size() << std::endl;
                    writeSplitClusterFilterDiagnostic(
                        cfg, periodIdx, cluster_filter_stats.vertical_removed,
                        cluster_filter_stats.strong_small_kept,
                        cluster_filter_stats.small_rejected,
                        in_rows.size(), out_rows.size());
                }
            }
        } else if (union_csi_detection_band) {
            std::vector<float> dynamic_hits, full_hits;
            std::vector<float> dynamic_power, full_power;
            std::vector<std::complex<float>> dynamic_complex, full_complex;
            const bool dump_complex = pcProfileDopplerRow(periodIdx) >= 0;
            const bool dynamic_ok = dpca_cfar2_fast_cuda(
                CSI_out, dynamic_band_st, dynamic_band_ed,
                cfg.pf, cfg.cfar_guard_cells, cfg.cfar_background_cells,
                cfg.cfar_type, cfg, dynamic_hits, &dynamic_power,
                dump_complex ? &dynamic_complex : nullptr);
            const bool full_ok = dpca_cfar2_fast_cuda(
                CSI_out, 0, effectivePulseNum(cfg) - 1,
                cfg.pf, cfg.cfar_guard_cells, cfg.cfar_background_cells,
                cfg.cfar_type, cfg, full_hits, &full_power,
                dump_complex ? &full_complex : nullptr);
            cfar_ok = dynamic_ok && full_ok &&
                      dynamic_hits.size() == full_hits.size() &&
                      dynamic_power.size() == full_power.size() &&
                      (!dump_complex ||
                       (dynamic_complex.size() == dynamic_power.size() &&
                        full_complex.size() == full_power.size()));
            if (cfar_ok) {
                mydata.resize(dynamic_hits.size());
                power_map.resize(dynamic_power.size());
                if (dump_complex) detection_complex_map.resize(dynamic_power.size());
                for (size_t i = 0; i < mydata.size(); ++i) {
                    mydata[i] = std::max(dynamic_hits[i], full_hits[i]);
                    const bool use_dynamic = dynamic_power[i] >= full_power[i];
                    power_map[i] = use_dynamic ? dynamic_power[i] : full_power[i];
                    if (dump_complex) {
                        detection_complex_map[i] = use_dynamic
                            ? dynamic_complex[i] : full_complex[i];
                    }
                }
            }
        } else {
            // Keep the fixed-CFAR and cached-fusion paths consistent.  The
            // production strong-small filter consumes the resident device
            // power map; only host diagnostics/P38 refit download full maps.
            const bool need_host_maps =
                (cfg.p38_enhanced_enable && cfg.p38_refit_enable) ||
                (cfg.runtime_diagnostics_enabled && cfarDumpSelectedBeam(periodIdx)) ||
                pcProfileDopplerRow(periodIdx) >= 0;
            cfar_ok = dpca_cfar2_fast_cuda(
                CSI_out, band_st, band_ed, cfg.pf, cfg.cfar_guard_cells,
                cfg.cfar_background_cells, cfg.cfar_type, cfg,
                mydata, need_host_maps ? &power_map : nullptr,
                pcProfileDopplerRow(periodIdx) >= 0 ? &detection_complex_map : nullptr,
                cfg.runtime_diagnostics_enabled ? &gpu_cfar_hit_count : nullptr,
                need_host_maps);
            gpu_cfar_resident = cfar_ok;
        }
        if (!cfar_ok) {
            if (split_csi_detection_band) {
                return failBeamStage("split_cfar_or_cluster");
            }
            gpu_cfar_resident = false;
            std::vector<std::complex<float>> F1_f, F2_f;
            if (!cuda_download_sync(F1_f, F2_f, Na * Nr)) {
                return false;
            }
            if (!cuda_download_csi_sync(CSI_out, Na * Nr)) {
                return false;
            }

            auto run_cpu_band = [&](int cpu_band_st, int cpu_band_ed,
                                    std::vector<double> &hits,
                                    std::vector<float> &powers) {
                std::vector<std::complex<double>> detect_data(F2_f.size());
                for (size_t i = 0; i < F2_f.size(); ++i) {
                    detect_data[i] = std::complex<double>(
                        F2_f[i].real(), F2_f[i].imag());
                }
                if (cpu_band_st <= cpu_band_ed) {
                    for (int r = cpu_band_st; r <= cpu_band_ed; ++r) {
                        const size_t off = static_cast<size_t>(r) * Nr;
                        for (size_t c = 0; c < Nr; ++c) {
                            const auto v = CSI_out[off + c];
                            detect_data[off + c] = std::complex<double>(
                                v.real(), v.imag());
                        }
                    }
                }
                std::vector<int> prow_cpu, pcol_cpu;
                if (pcProfileDopplerRow(periodIdx) >= 0) {
                    detection_complex_map.resize(detect_data.size());
                    for (size_t i = 0; i < detect_data.size(); ++i) {
                        detection_complex_map[i] = std::complex<float>(
                            static_cast<float>(detect_data[i].real()),
                            static_cast<float>(detect_data[i].imag()));
                    }
                }
                return dpca_cfar2_fast(
                    detect_data, cfg.pf, cfg.cfar_guard_cells,
                    cfg.cfar_background_cells, cfg.cfar_type, cfg,
                    hits, prow_cpu, pcol_cpu, &powers);
            };
            if (union_csi_detection_band) {
                std::vector<double> dynamic_hits, full_hits;
                std::vector<float> dynamic_power, full_power;
                cfar_ok =
                    run_cpu_band(dynamic_band_st, dynamic_band_ed,
                                 dynamic_hits, dynamic_power) &&
                    run_cpu_band(0, effectivePulseNum(cfg) - 1,
                                 full_hits, full_power) &&
                    dynamic_hits.size() == full_hits.size() &&
                    dynamic_power.size() == full_power.size();
                if (cfar_ok) {
                    mydata.resize(dynamic_hits.size());
                    power_map.resize(dynamic_power.size());
                    for (size_t i = 0; i < mydata.size(); ++i) {
                        mydata[i] = static_cast<float>(
                            std::max(dynamic_hits[i], full_hits[i]));
                        power_map[i] = std::max(dynamic_power[i], full_power[i]);
                    }
                }
            } else {
                std::vector<double> mydata_d;
                std::vector<float> power_map_d;
                cfar_ok = run_cpu_band(band_st, band_ed,
                                       mydata_d, power_map_d);
                if (cfar_ok) {
                    mydata.assign(mydata_d.begin(), mydata_d.end());
                    power_map.assign(power_map_d.begin(), power_map_d.end());
                }
            }
        }
        if (!cfar_ok) {
            return false;
        }
        cfar_hits = split_csi_detection_band ? gpu_cfar_hit_count : cfg.runtime_diagnostics_enabled
            ? (gpu_cfar_resident
                   ? gpu_cfar_hit_count
                   : static_cast<size_t>(std::count_if(
                         mydata.begin(), mydata.end(),
                         [](float v) { return v > 0.0f; })))
            : 0U;
        writeCfarDiagnostic(cfg, periodIdx, fa_ctr, mydata, power_map,
                            detection_complex_map, &threshold_map);
        writeDetectionRangeProfile(cfg, periodIdx, power_map);
        writeDetectionComplexRangeProfile(cfg, periodIdx, detection_complex_map);

        std::vector<float> phase_std_list;
        const int cluster_run_min_points = cfg.cluster_strong_small_enable
            ? std::min(cfg.min_points, cfg.cluster_strong_small_min_points)
            : cfg.min_points;
        const bool need_refined_mydata =
            !gpu_cfar_resident && power_map.size() != mydata.size();
        bool strong_filter_applied_on_gpu = false;
        bool cluster_ok = split_cluster_complete;
        if (!split_cluster_complete) cluster_ok = cluster_filter_gap_phase_cuda(mydata, phase_map, cluster_run_min_points,
                                                        cfg.cluster_max_range_gap,
                                                        static_cast<float>(cfg.cluster_max_phase_std_rad),
                                                        cfg, refined_mydata, prow_new, pcol_new,
                                                        phase_std_list, need_refined_mydata,
                                                        gpu_cfar_resident,
                                                        &cluster_filter_stats.global_median_power,
                                                        &cluster_filter_stats.baseline_kept,
                                                        &cluster_filter_stats.strong_small_kept,
                                                        &cluster_filter_stats.small_rejected,
                                                        &strong_filter_applied_on_gpu);
        if (!cluster_ok) {
            if (mydata.empty() && gpu_cfar_resident &&
                !cuda_download_cfar_maps(mydata, power_map, Na * Nr)) {
                return failBeamStage("cfar_map_download_for_cluster_cpu_fallback");
            }
            if (phase_map.empty() && !cuda_download_phase_map(phase_map, Na * Nr)) {
                return failBeamStage("phase_map_download_for_cluster_cpu_fallback");
            }
            std::vector<double> mydata_d(mydata.begin(), mydata.end());
            std::vector<double> phase_map_d(phase_map.begin(), phase_map.end());
            std::vector<double> refined_mydata_d;
            std::vector<double> phase_std_list_d;
            cluster_ok = cluster_filter_gap_phase(mydata_d, phase_map_d, cluster_run_min_points,
                                                  cfg.cluster_max_range_gap,
                                                  cfg.cluster_max_phase_std_rad,
                                                  cfg, refined_mydata_d, prow_new, pcol_new,
                                                  phase_std_list_d);
            if (cluster_ok) {
                refined_mydata.assign(refined_mydata_d.begin(), refined_mydata_d.end());
                phase_std_list.assign(phase_std_list_d.begin(), phase_std_list_d.end());
            }
        }
        if (!cluster_ok) {
            return false;
        }
        if (!split_cluster_complete && !strong_filter_applied_on_gpu) {
            cluster_filter_stats = filterStrongSmallClusters(
                mydata, power_map, effectivePulseNum(cfg), cfg.rg_len, 2, cfg,
                prow_new, pcol_new, phase_std_list);
        }

        bool targetDetection = target_select_cuda(prow_new, pcol_new, cfg, targetSel);
        if (!targetDetection) {
            std::vector<std::complex<float>> F1_f, F2_f;
            if (!cuda_download_sync(F1_f, F2_f, Na * Nr)) {
                return false;
            }
            std::vector<std::complex<double>> F1(F1_f.size()), F2(F2_f.size());
            for (size_t i = 0; i < F1_f.size(); ++i) {
                F1[i] = std::complex<double>(F1_f[i].real(), F1_f[i].imag());
                F2[i] = std::complex<double>(F2_f[i].real(), F2_f[i].imag());
            }
            targetDetection = target_select(F1, F2, prow_new, pcol_new, cfg, targetSel);
        }
        if (!targetDetection) {
            return false;
        }
        cfar_power_resident_for_output = gpu_cfar_resident && !split_csi_detection_band;
    }

    if (cfg.runtime_diagnostics_enabled) {
        std::cout << "[CFAR][SUMMARY] beam=" << periodIdx
                  << " slot=" << slot
                  << " hit_cells=" << cfar_hits
                  << " clusters=" << prow_new.size()
                  << " selected=" << targetSel.prow.size()
                  << " min_points=" << cfg.min_points
                  << " strong_small_kept="
                  << cluster_filter_stats.strong_small_kept
                  << " small_rejected=" << cluster_filter_stats.small_rejected
                  << " global_median_power="
                  << cluster_filter_stats.global_median_power
                  << " pf=" << cfg.pf << std::endl;
    }

    std::vector<double> p38_pre_row_fa;
    p38_pre_row_fa.reserve(static_cast<size_t>(std::max(0, az_ed - az_st + 1)));
    for (int r = az_st; r <= az_ed && r >= 0 && r < static_cast<int>(faAxis.size()); ++r) {
        p38_pre_row_fa.push_back(faAxis[static_cast<size_t>(r)]);
    }
    std::array<double, 2> p_38_eval{{static_cast<double>(p_38[0]), static_cast<double>(p_38[1])}};
    std::array<double, 2> p_38_raw_eval{{static_cast<double>(p_38_raw[0]), static_cast<double>(p_38_raw[1])}};
    P38StageMetrics p38_raw_metrics = evaluateP38FitMetrics(p38_raw_trace, p38_pre_row_fa, p_38_raw_eval);
    P38StageMetrics p38_pre_metrics = evaluateP38FitMetrics(p38_pre_trace, p38_pre_row_fa, p_38_eval);
    p38_raw_metrics.source = p38_raw_model_source;
    p38_pre_metrics.source = p38_pre_model_source;

    std::array<float, 2> p38_used = p_38;
    std::string p38_used_source = "pre";
    P38StageMetrics p38_refit_metrics;
    if (cfg.p38_enhanced_enable && cfg.p38_refit_enable &&
        !power_map.empty() && !F1_pre.empty() &&
        F1_pre.size() == F2_pre.size()) {
        std::array<float, 2> p38_refit_candidate = p_38;
        auto estimate_p38 = [this, &p38_refit_model_source](const std::vector<double> &fa_axis,
                                   const std::vector<std::complex<float>> &F1_masked,
                                   const std::vector<std::complex<float>> &F2_masked,
                                   int az_st_local,
                                   int rg_st_local,
                                   int az_ed_local,
                                   int rg_ed_local,
                                   const Config &cfg_local,
                                   std::vector<std::complex<float>> &prosig_38,
                                   std::array<float, 2> &p38_out,
                                   std::vector<double> &phase_samples,
                                   std::vector<double> &row_samples) -> bool {
            std::vector<float> fa_axis_f(fa_axis.begin(), fa_axis.end());
            std::vector<float> phase_samples_f;
            std::vector<float> row_samples_f;
            const bool ok = this->clutter_cancel_38_paper_1(
                fa_axis_f,
                F1_masked,
                F2_masked,
                az_st_local, rg_st_local, az_ed_local, rg_ed_local,
                cfg_local,
                prosig_38,
                p38_out,
                phase_samples_f,
                row_samples_f,
                &p38_refit_model_source);
            phase_samples.assign(phase_samples_f.begin(), phase_samples_f.end());
            row_samples.assign(row_samples_f.begin(), row_samples_f.end());
            return ok;
        };
        p38_refit_metrics = refitP38WithMask(cfg,
                                             faAxis, F1_pre, F2_pre,
                                             az_st, rg_st, az_ed, rg_ed,
                                             prow_new, pcol_new,
                                             targetSel.prow, targetSel.pcol,
                                             phase_map, power_map,
                                             estimate_p38,
                                             p38_refit_candidate,
                                             &p38_refit_trace);
        p38_refit_metrics.source = p38_refit_model_source;
        p_38_refit = p38_refit_candidate;
        const double delta_k = std::abs(static_cast<double>(p38_refit_candidate[0] - p_38[0]));
        const double delta_b = std::abs(wrapPiLocal(static_cast<double>(p38_refit_candidate[1] - p_38[1])));
        const bool refit_valid = p38_refit_metrics.valid &&
                                 p38_refit_metrics.sample_count >= std::max(0, cfg.p38_refit_min_sample_count) &&
                                 p38_refit_metrics.inlier_ratio >= cfg.p38_refit_min_inlier_ratio &&
                                 p38_refit_metrics.rmse <= cfg.p38_refit_max_rmse_rad &&
                                 delta_k <= cfg.p38_refit_max_delta_k &&
                                 delta_b <= cfg.p38_refit_max_delta_b_rad;
        p38_refit_metrics.p38 = {p38_refit_candidate[0], p38_refit_candidate[1]};
        p38_refit_metrics.valid = refit_valid;
        if (refit_valid) {
            p38_used = p38_refit_candidate;
            p38_used_source = "refit";
        }
    }

    std::vector<double> calibration_fa_axis = faAxis;
    const double calibration_fd_unwrapped = unwrap_prf_to_model(
        fa_ctr, cfg.PRF, theta_deg, plane.V, cfg.fc);
    const double calibration_fa_shift = calibration_fd_unwrapped - fa_ctr;
    for (double& value : calibration_fa_axis) value += calibration_fa_shift;
    const gmti::calibration::ChannelCalibrationResult channel_calibration =
        gmti::calibration::estimateStaticCtdrResidual(
            cfg, plane, calibration_fa_axis,
            F1_pre, F2_pre, phase_map_rg_correction,
            az_st, az_ed, rg_st, rg_ed,
            targetSel.prow, targetSel.pcol);
    gmti::calibration::writeChannelCalibrationDiagnostics(
        cfg, periodIdx, channel_calibration);

    writeP38FitDiagnostics(cfg, periodIdx, plane.V,
                           az_st, az_ed, rg_st, rg_ed,
                           p38_pre_row_fa,
                           p38_raw_trace, p38_raw_metrics,
                           p38_pre_trace, p38_pre_metrics,
                           p38_refit_trace, p38_refit_metrics,
                           p38_used_source);
    writeP38AlignmentDiagnostics(cfg, periodIdx, plane.V, skipInt_theory,
                                 p38_aligned_row_fa, p38_aligned_trace,
                                 p_38_aligned);

    beamMeta.phase_slope = p38_used[0];
    beamMeta.phase_intercept = p38_used[1];
    beamMeta.az_center = az_center;
    beamMeta.p38_raw_k = p_38_raw[0];
    beamMeta.p38_raw_b = p_38_raw[1];
    beamMeta.p38_raw_rmse = p38_raw_metrics.rmse;
    beamMeta.p38_pre_k = p_38[0];
    beamMeta.p38_pre_b = p_38[1];
    beamMeta.p38_pre_rmse = p38_pre_metrics.rmse;
    beamMeta.p38_refit_k = p38_refit_metrics.p38[0];
    beamMeta.p38_refit_b = p38_refit_metrics.p38[1];
    beamMeta.p38_refit_rmse = p38_refit_metrics.rmse;
    beamMeta.p38_refit_sample_count = p38_refit_metrics.sample_count;
    beamMeta.p38_refit_inlier_ratio = p38_refit_metrics.inlier_ratio;
    beamMeta.p38_refit_valid = p38_refit_metrics.valid ? 1 : 0;
    beamMeta.p38_used_k = p38_used[0];
    beamMeta.p38_used_b = p38_used[1];
    beamMeta.p38_used_source = p38_used_source;
    beamMeta.channel_calibration_phase_bias_rad =
        channel_calibration.applied ? channel_calibration.applied_phase_bias_rad : 0.0;
    beamMeta.channel_calibration_relative_gain_abs =
        channel_calibration.relative_gain_abs;
    beamMeta.channel_calibration_coherence = channel_calibration.coherence;
    beamMeta.channel_calibration_rmse_rad = channel_calibration.residual_rmse_rad;
    beamMeta.channel_calibration_sample_count = channel_calibration.sample_count;
    beamMeta.channel_calibration_valid = channel_calibration.applied ? 1 : 0;

    if (slot < ctx.detections.size()) {
        auto &raw = ctx.detections[slot];
        raw.clear();
        raw.reserve(targetSel.prow.size());
        const double ref_phase = faAxis[static_cast<size_t>(az_center)] * p38_used[0] + p38_used[1];
        std::vector<float> selected_phase;
        if (phase_map.empty() &&
            !cuda_download_phase_samples(targetSel.prow, targetSel.pcol,
                                         static_cast<int>(Nr), selected_phase)) {
            return failBeamStage("selected_phase_download");
        }
        std::vector<float> selected_cfar_power;
        if (power_map.empty() && cfar_power_resident_for_output &&
            !cuda_download_cfar_power_samples(
                targetSel.prow, targetSel.pcol, static_cast<int>(Nr),
                selected_cfar_power)) {
            return failBeamStage("selected_cfar_power_download");
        }
        for (size_t i = 0; i < targetSel.prow.size(); ++i) {
            const int r = targetSel.prow[i];
            const int c = targetSel.pcol[i];
            if (r < 0 || c < 0 || r >= static_cast<int>(Na) || c >= static_cast<int>(Nr)) {
                continue;
            }
            const size_t off = static_cast<size_t>(r) * Nr + static_cast<size_t>(c);
            const double dphi_sample = phase_map.empty()
                ? static_cast<double>(selected_phase[i])
                : static_cast<double>(phase_map[off]);
            double dphi = dphi_sample;
            double diff = dphi - ref_phase;
            if (diff > M_PI) {
                diff -= 2.0 * M_PI;
            }
            if (diff < -M_PI) {
                diff += 2.0 * M_PI;
            }
            dphi = ref_phase + diff;

            const double af_wrapped = (std::abs(p38_used[0]) < 1e-12)
                ? ((r < static_cast<int>(faAxis.size())) ? faAxis[static_cast<size_t>(r)] : fa_ctr)
                : ((dphi - p38_used[1]) / p38_used[0]);
            const double af_phase = (std::abs(p38_used[0]) < 1e-12)
                ? 0.0
                : ((dphi - p38_used[1]) / p38_used[0]);
            const double af_total = (r >= 0 && r < static_cast<int>(faAxis.size()))
                ? faAxis[static_cast<size_t>(r)]
                : af_wrapped;

            DetectionRaw d;
            d.beam_index = periodIdx;
            d.slot = static_cast<int>(slot);
            d.prow = r;
            d.pcol = c;
            d.range_m = cfg.Rg.empty() ? (cfg.R_min + static_cast<double>(c) * cfg.R_bin)
                                       : cfg.Rg[static_cast<size_t>(c)];
            d.af_wrapped = af_wrapped;
            d.af_row = af_total;
            d.af_phase = af_phase;
            d.af_total = af_total;
            d.af_geometry = af_phase;
            d.phase = dphi;
            d.range_phase_correction =
                (c >= 0 && c < static_cast<int>(phase_map_rg_correction.size()))
                    ? static_cast<double>(phase_map_rg_correction[static_cast<size_t>(c)])
                    : std::numeric_limits<double>::quiet_NaN();
            const double det_power = (off < power_map.size())
                ? static_cast<double>(power_map[off])
                : ((i < selected_cfar_power.size())
                       ? static_cast<double>(selected_cfar_power[i])
                       : ((off < refined_mydata.size())
                              ? static_cast<double>(refined_mydata[off]) : 0.0));
            d.amplitude = det_power > 0.0 ? std::sqrt(det_power) : 0.0;
            d.utc_mid = beamMeta.utc_mid;
            raw.push_back(d);
        }
    }

    if (slot < ctx.beam_meta.size()) {
        ctx.beam_meta[slot] = beamMeta;
    }
    if (slot < ctx.done.size()) {
        ctx.done[slot] = 1;
    }

    return true;
}

bool GMTIProcessor::extractPlanePos(const std::vector<double> &t_utc,
                                    const std::vector<std::vector<double>> &POS, // [POS_num][17]
                                    const Config &cfg,
                                    GMTIOutput::Plane &plane,
                                    int beam_id)
{
    // 1) 退化条件
    const bool empty_or_zero = t_utc.empty() ||
                               std::all_of(t_utc.begin(), t_utc.end(), [](double x)
                                           { return x == 0.0; });

    if (empty_or_zero)
    {
        plane.E = plane.N = 0.0;
        plane.H = cfg.MT_nowz;
        plane.V = 40.0 / 3.6; // 40 km/h -> m/s
        plane.V_angle = 0.0;
        return true;
    }

    // 2) 基本检查
    const size_t pos_num = POS.size();
    if (pos_num == 0 || POS[0].size() < 4)
        return false;

    // 拿出 POS 时间轴（秒）
    std::vector<double> t_pos(pos_num);
    for (size_t i = 0; i < pos_num; ++i)
        t_pos[i] = POS[i][0];

    // 3) 逐个 t_utc 做线性插值 -> lat/lon/alt（弧度/弧度/米）
    const size_t M = t_utc.size();
    std::vector<double> lat_rad(M), lon_rad(M), alt_m(M);
    lat_rad.reserve(M);
    lon_rad.reserve(M);
    alt_m.reserve(M);

    auto interp_scalar = [&](double t, int col) -> double
    {
        // 边界：落在两端时取端点
        if (t <= t_pos.front())
            return POS.front()[col];
        if (t >= t_pos.back())
            return POS.back()[col];

        // lower_bound 找到第一个 >= t 的位置
        auto it = std::lower_bound(t_pos.begin(), t_pos.end(), t);
        size_t ir = size_t(it - t_pos.begin());
        size_t il = ir - 1;

        double t1 = t_pos[il], t2 = t_pos[ir];
        double y1 = POS[il][col], y2 = POS[ir][col];
        double r = (t - t1) / (t2 - t1);
        return y1 + r * (y2 - y1);
    };

    for (size_t i = 0; i < M; ++i)
    {
        double lat_val = interp_scalar(t_utc[i], 1);
        double lon_val = interp_scalar(t_utc[i], 2);

        // Compatibility: old POS usually stores radians; some new inputs may contain degrees.
        lat_rad[i] = (std::abs(lat_val) > M_PI) ? (lat_val * M_PI / 180.0) : lat_val;
        lon_rad[i] = (std::abs(lon_val) > M_PI) ? (lon_val * M_PI / 180.0) : lon_val;
        alt_m[i] = interp_scalar(t_utc[i], 3);   // 列4：高度(米)
    }

    // 4) 经纬 -> 投影 EN
    std::vector<double> E(M), N(M);
    for (size_t i = 0; i < M; ++i)
    {
        double lat_deg = lat_rad[i] * 180.0 / M_PI;
        double lon_deg = lon_rad[i] * 180.0 / M_PI;
        double e = 0.0, n = 0.0;
        Gaussp3(lat_deg, lon_deg, cfg.L0, e, n); // 若你的实现返回bool
        E[i] = e;
        N[i] = n;
    }

    // 5) 平均高度/位置
    auto mean = [](const std::vector<double> &v) -> double
    {
        if (v.empty())
            return 0.0;
        double s = std::accumulate(v.begin(), v.end(), 0.0);
        return s / double(v.size());
    };

    plane.H = mean(alt_m);
    plane.E = mean(E);
    plane.N = mean(N);

    // 6) 速度估计
    // position_delta 是由经纬度/高度投影坐标反推的生产路径；它不能依赖
    // 协议是否提供 vn/ve/vd。header 只在显式选择 new_protocol_velocity_source
    // == "header" 时使用，不能作为 position_delta 的隐含输入。
    double Vx = 0.0;
    double Vy = 0.0;
    double Vz = 0.0;
    const char *velocity_source_used = "default";
    std::size_t valid_delta_intervals = 0U;
    std::size_t diagnostic_anomaly_intervals = 0U;
    gmti::imu::BeamBoundaryInterpolation imu_boundary;
    bool imu_boundary_valid = false;
    if (cfg.INFO_Type &&
        cfg.new_protocol_velocity_source == "header" &&
        std::all_of(POS.begin(), POS.end(),
                    [](const std::vector<double> &row) {
                        return row.size() >= 7 &&
                               std::isfinite(row[4]) &&
                               std::isfinite(row[5]) &&
                               std::isfinite(row[6]);
                    })) {
        double sum_vn = 0.0;
        double sum_ve = 0.0;
        double sum_vd = 0.0;
        for (const auto &row : POS) {
            sum_vn += row[4];
            sum_ve += row[5];
            sum_vd += row[6];
        }
        const double inv_count = 1.0 / static_cast<double>(POS.size());
        Vx = sum_ve * inv_count;
        Vy = sum_vn * inv_count;
        Vz = -sum_vd * inv_count;
        velocity_source_used = "header";
    } else {
        if (cfg.INFO_Type && cfg.new_protocol_velocity_source == "position_delta") {
            velocity_source_used = "position_delta";
            // Interpolate both beam boundaries from their adjacent,
            // time-tagged INS samples in projected ENU coordinates.  This
            // preserves partial leading/trailing intervals and rejects a
            // boundary outside the available sample range.
            std::vector<gmti::imu::ProjectedSample> imu_samples;
            imu_samples.reserve(POS.size());
            for (std::size_t i = 0U; i < POS.size(); ++i) {
                const std::vector<double> &row = POS[i];
                if (row.size() < 4U || !std::isfinite(row[0]) ||
                    !std::isfinite(row[1]) || !std::isfinite(row[2]) ||
                    !std::isfinite(row[3])) {
                    std::cerr << "[plane-pos][ERR] invalid IMU position sample "
                              << "beam_id=" << beam_id << " index=" << i
                              << std::endl;
                    return false;
                }
                const double lat_value =
                    std::abs(row[1]) > M_PI ? row[1] * M_PI / 180.0 : row[1];
                const double lon_value =
                    std::abs(row[2]) > M_PI ? row[2] * M_PI / 180.0 : row[2];
                double east = 0.0;
                double north = 0.0;
                Gaussp3(lat_value * 180.0 / M_PI,
                        lon_value * 180.0 / M_PI,
                        cfg.L0, east, north);
                imu_samples.push_back({row[0], east, north, row[3]});
            }

            std::string interpolation_error;
            if (!gmti::imu::interpolateBeamBoundaries(
                    imu_samples, t_utc.front(), t_utc.back(), imu_boundary,
                    &interpolation_error)) {
                std::cerr << "[plane-pos][ERR] IMU boundary interpolation failed"
                          << " beam_id=" << beam_id
                          << " t_start=" << t_utc.front()
                          << " t_end=" << t_utc.back()
                          << " reason=" << interpolation_error << std::endl;
                return false;
            }
            Vx = imu_boundary.east_velocity;
            Vy = imu_boundary.north_velocity;
            Vz = imu_boundary.up_velocity;
            valid_delta_intervals = 1U;
            imu_boundary_valid = true;
        } else {
            velocity_source_used = "legacy_position_delta";
            // 旧协议保持原算法：首末位置差 / 波位时长。
            const int W = effectivePulseNum(cfg);
            const double scale = (W > 0) ? (cfg.PRF / double(W)) : 0.0;
            Vx = (E.back() - E.front()) * scale;
            Vy = (N.back() - N.front()) * scale;
            Vz = (alt_m.back() - alt_m.front()) * scale;
        }
    }

    plane.V = std::sqrt(Vx * Vx + Vy * Vy + Vz * Vz);
    plane.V_angle = gmti::trig_lut::atan2(Vy, Vx) * 180.0 / M_PI; // 东北平面速度方向角(°)

    if (cfg.runtime_diagnostics_enabled && cfg.INFO_Type) {
        std::cout << "[plane-pos] source=" << velocity_source_used
                  << " samples=" << M
                  << " delta_intervals=" << valid_delta_intervals
                  << " diagnostic_anomalies=" << diagnostic_anomaly_intervals
                  << " t_first=" << t_utc.front()
                  << " t_last=" << t_utc.back()
                  << " e_first=" << E.front()
                  << " e_last=" << E.back()
                  << " n_first=" << N.front()
                  << " n_last=" << N.back()
                  << " vx_mps=" << Vx
                  << " vy_mps=" << Vy
                  << " vz_mps=" << Vz
                  << " speed_mps=" << plane.V << std::endl;
    }

    if (cfg.runtime_diagnostics_enabled && cfg.INFO_Type &&
        imu_boundary_valid) {
        std::cout << "[imu-interp] beam_id=" << beam_id
                  << " t_start=" << t_utc.front()
                  << " t_end=" << t_utc.back()
                  << " imu_before_start=" << imu_boundary.start.before_index
                  << "@" << imu_boundary.start.before_time
                  << " imu_after_start=" << imu_boundary.start.after_index
                  << "@" << imu_boundary.start.after_time
                  << " imu_before_end=" << imu_boundary.end.before_index
                  << "@" << imu_boundary.end.before_time
                  << " imu_after_end=" << imu_boundary.end.after_index
                  << "@" << imu_boundary.end.after_time
                  << " P_start_E=" << imu_boundary.start.east
                  << " P_start_N=" << imu_boundary.start.north
                  << " P_start_U=" << imu_boundary.start.up
                  << " P_end_E=" << imu_boundary.end.east
                  << " P_end_N=" << imu_boundary.end.north
                  << " P_end_U=" << imu_boundary.end.up
                  << " V_E_mps=" << imu_boundary.east_velocity
                  << " V_N_mps=" << imu_boundary.north_velocity
                  << " V_U_mps=" << imu_boundary.up_velocity
                  << " speed_mps=" << plane.V << std::endl;
    }

    DBG("飞机速度分量: Vx=" << Vx << " Vy=" << Vy << " Vz=" << Vz);

    return true;
}

bool GMTIProcessor::extractPlanePVFromEcho(const Config &cfg,
                                           GMTIOutput::Plane &plane)
{
    Config local_cfg = cfg;
    std::vector<std::complex<float>> data1, data2;
    std::vector<double> utc;
    std::vector<std::vector<double>> echoPosRaw;
    double theta_sq = 0.0;

    const int reference_index =
        local_cfg.scan_mode == ScanMode::Mechanical &&
        local_cfg.mechanical_scan_runtime &&
        !local_cfg.mechanical_scan_runtime->processing_window_indices.empty()
            ? local_cfg.mechanical_scan_runtime->processing_window_indices.front()
            : local_cfg.new_protocol_file_first_beam;
    if (!readPulseBlockNewProtocol(local_cfg,
                                   reference_index,
                                   data1, data2, utc, theta_sq, echoPosRaw)) {
        std::cerr << "extractPlanePVFromEcho: failed to read new-protocol echo" << std::endl;
        return false;
    }
    local_cfg.process_pulse_num = static_cast<int>(utc.size());

    if (!extractPlanePos(utc, echoPosRaw, local_cfg, plane, -1)) {
        std::cerr << "extractPlanePVFromEcho: failed to extract plane from embedded echo pose" << std::endl;
        return false;
    }

    return true;
}

// Compute dataset-level squint by reading center period(s) and estimating fd_ctr.
// This replicates DBS semantics: estimate fd_ctr from the central period(s),
// unwrap PRF ambiguity, convert to squint angle and average when two centers exist.
bool GMTIProcessor::computeDatasetSquintFromCenter(const std::vector<int> &periodList,
                                                   const Config &cfg,
                                                   const std::vector<std::vector<double>> &posRaw,
                                                   double &out_squint)
{
    out_squint = 0.0;
    if (periodList.empty()) return false;

    // Determine center period(s). Electronic mode preserves the exact list-
    // midpoint behavior. Mechanical mode selects actual CPI centre angles
    // nearest the commanded scan centre and never assumes a centre beam id.
    std::vector<int> centers;
    size_t n = periodList.size();
    if (cfg.scan_mode == ScanMode::Mechanical && cfg.mechanical_scan_runtime) {
        double target_center_deg = 0.0;
        for (const PulseMeta& p : cfg.mechanical_scan_runtime->pulse_meta) {
            if (std::isfinite(p.scan_center_azimuth_deg)) {
                target_center_deg = p.scan_center_azimuth_deg;
                break;
            }
        }
        std::vector<std::pair<double, int>> ranked;
        ranked.reserve(periodList.size());
        for (int index : periodList) {
            const MechanicalCpiWindow *w = mechanicalWindow(cfg, index);
            if (w && w->valid && std::isfinite(w->az_center_deg)) {
                ranked.push_back(std::make_pair(
                    std::abs(w->az_center_deg - target_center_deg), index));
            }
        }
        std::stable_sort(ranked.begin(), ranked.end(),
                         [](const std::pair<double, int>& lhs,
                            const std::pair<double, int>& rhs) {
                             return lhs.first < rhs.first;
                         });
        const std::size_t take = std::min<std::size_t>(2U, ranked.size());
        for (std::size_t i = 0; i < take; ++i) centers.push_back(ranked[i].second);
    } else if (n % 2 == 1) {
        centers.push_back(periodList[n/2]);
    } else {
        centers.push_back(periodList[n/2 - 1]);
        centers.push_back(periodList[n/2]);
    }
    if (centers.empty()) return false;

    std::vector<double> angles_deg;
    for (int per : centers) {
        std::vector<std::complex<float>> data1, data2;
        std::vector<double> utc;
        std::vector<std::vector<double>> echoPosRaw;
        double theta_sq_local = 0.0;

        bool readOk = false;
        if (cfg.INFO_Type) {
            readOk = readPulseBlockNewProtocol(cfg, per, data1, data2, utc, theta_sq_local, echoPosRaw);
        } else {
            readOk = readPulseBlock(cfg, per, data1, data2, utc, theta_sq_local);
        }
        if (!readOk) {
            std::cerr << "computeDatasetSquintFromCenter: failed to read period " << per << std::endl;
            return false;
        }

        // Range-compress if necessary
        Config local_cfg = cfg;
        if (local_cfg.INFO_Type) {
            local_cfg.process_pulse_num = static_cast<int>(utc.size());
        }
        if (!local_cfg.isPC) {
            if (!pulseCompression(data1, data2, local_cfg)) {
                std::cerr << "computeDatasetSquintFromCenter: pulseCompression failed for period " << per << std::endl;
                return false;
            }
        } else {
            // Extract/normalize path (same as processOnePeriod extraction)
            const int Lraw = local_cfg.pulse_len;
            const int W = effectivePulseNum(local_cfg);
            const int M1 = local_cfg.rg_len;
            const int Lraw2M = Lraw / M1;
            std::vector<std::complex<float>> rc_out((size_t)W * M1);
            for (int k = 0; k < W; ++k) {
                for (int m = 0; m < M1; ++m) {
                    rc_out[(size_t)k * M1 + m] = data1[(size_t)k * Lraw + m * Lraw2M];
                }
            }
            data1.swap(rc_out);
            rc_out.assign((size_t)W * M1, std::complex<float>(0.0f,0.0f));
            for (int k = 0; k < W; ++k) {
                for (int m = 0; m < M1; ++m) {
                    rc_out[(size_t)k * M1 + m] = data2[(size_t)k * Lraw + m * Lraw2M];
                }
            }
            data2.swap(rc_out);
        }

        // After range compression/extraction, update sampling parameters like processOnePeriod does
        if (!usesRangeCropWindow(local_cfg)) {
            local_cfg.fs =
                local_cfg.fs * local_cfg.rg_len / local_cfg.pulse_len;
        }
        local_cfg.pulse_len = local_cfg.rg_len;

        // Apply azimuth decimation (pulse_dec) same as processOnePeriod
        const int dec = local_cfg.pulse_dec;
        if (dec <= 0) {
            std::cerr << "computeDatasetSquintFromCenter: invalid pulse_dec" << std::endl;
            return false;
        }
        const int W_orig = effectivePulseNum(local_cfg);
        if (W_orig % dec != 0) {
            std::cerr << "computeDatasetSquintFromCenter: pulse_num not divisible by pulse_dec" << std::endl;
            return false;
        }
        const int W_new = W_orig / dec;
        if (W_new != W_orig) {
            // decimate data1 in-place
            std::vector<std::complex<float>> tmp((size_t)W_new * local_cfg.rg_len);
            for (int k_new = 0; k_new < W_new; ++k_new) {
                size_t out_base = (size_t)k_new * local_cfg.rg_len;
                size_t in_base = (size_t)(dec * k_new) * local_cfg.rg_len;
                for (int m = 0; m < local_cfg.rg_len; ++m) {
                    std::complex<float> acc = 0.0f;
                    for (int d = 0; d < dec; ++d) acc += data1[in_base + (size_t)d * local_cfg.rg_len + m];
                    tmp[out_base + m] = acc;
                }
            }
            data1.swap(tmp);
            tmp.assign((size_t)W_new * local_cfg.rg_len, std::complex<float>(0.0f,0.0f));
            for (int k_new = 0; k_new < W_new; ++k_new) {
                size_t out_base = (size_t)k_new * local_cfg.rg_len;
                size_t in_base = (size_t)(dec * k_new) * local_cfg.rg_len;
                for (int m = 0; m < local_cfg.rg_len; ++m) {
                    std::complex<float> acc = 0.0f;
                    for (int d = 0; d < dec; ++d) acc += data2[in_base + (size_t)d * local_cfg.rg_len + m];
                    tmp[out_base + m] = acc;
                }
            }
            data2.swap(tmp);
            local_cfg.process_pulse_num = W_new;
            local_cfg.PRF = local_cfg.PRF / dec;
        }

        // Extract plane for this period (use echoPosRaw if available)
        GMTIOutput::Plane plane;
        bool plane_ok = false;
        if (!echoPosRaw.empty()) {
            plane_ok = extractPlanePos(utc, echoPosRaw, local_cfg, plane, per);
        } else if (!posRaw.empty()) {
            plane_ok = extractPlanePos(utc, posRaw, local_cfg, plane, per);
        } else {
            plane_ok = extractPlanePVFromEcho(local_cfg, plane);
        }
        if (!plane_ok) {
            std::cerr << "computeDatasetSquintFromCenter: failed to extract plane for period " << per << std::endl;
            return false;
        }

        // Estimate wrapped fd_ctr from data.
        double fd_ctr = 0.0;
        int start_pulse = 0, window_pulses = 0;
        if (!estimateCenterFdCtrFromData(data1, local_cfg, fd_ctr, start_pulse, window_pulses)) {
            std::cerr << "computeDatasetSquintFromCenter: center fd estimate failed for period " << per << std::endl;
            return false;
        }

        // Unwrap PRF ambiguity using model
        double fd_unwrapped = unwrap_prf_to_model(fd_ctr, local_cfg.PRF, theta_sq_local, plane.V, local_cfg.fc);

        // Convert to squint angle (deg)
        const double lambda = (local_cfg.lambda > 0.0) ? local_cfg.lambda : (C / local_cfg.fc);
        if (plane.V <= 0.0 || lambda <= 0.0) {
            std::cerr << "computeDatasetSquintFromCenter: invalid V/lambda" << std::endl;
            return false;
        }
        double ratio = -fd_unwrapped * lambda / (2.0 * plane.V);
        if (ratio > 1.0) ratio = 1.0;
        if (ratio < -1.0) ratio = -1.0;
        double angle_deg = gmti::trig_lut::asin(ratio) * 180.0 / M_PI;

        // Use the raw local angle as the reference and take the difference.
        // This is the actual error angle that should be applied globally.
        double bias_deg = wrap180_deg(angle_deg - theta_sq_local);

        // std::cout << "[SQUINT] beam=" << per
        //           << ", theta_sq_local=" << theta_sq_local
        //           << ", estimated_angle=" << angle_deg
        //           << ", bias_angle=" << bias_deg
        //           << std::endl;

        angles_deg.push_back(bias_deg);
    }

    if (angles_deg.empty()) return false;
    double sum = 0.0; for (double a : angles_deg) sum += a;
    out_squint = sum / double(angles_deg.size());
    return true;
}

bool GMTIProcessor::cuda_upload_async(const std::vector<cd> &data1,
                                     const std::vector<cd> &data2,
                                     size_t Na, size_t Nr) {
    size_t total_bytes = Na * Nr * sizeof(cd);
    
    // 异步拷贝数据到 GPU 原始区 (d1, d2)
    cudaError_t error = cudaMemcpyAsync(
        gpu_ptrs_.d1, data1.data(), total_bytes,
        cudaMemcpyHostToDevice, stream_compute_);
    if (error == cudaSuccess) {
        error = cudaMemcpyAsync(
            gpu_ptrs_.d2, data2.data(), total_bytes,
            cudaMemcpyHostToDevice, stream_compute_);
    }
    return error == cudaSuccess;
}

bool GMTIProcessor::cuda_download_sync(std::vector<cd> &out1, std::vector<cd> &out2, 
                                      size_t total) {
    out1.resize(total);
    out2.resize(total);

    // 1. 异步回传结果
    cudaError_t error = cudaMemcpyAsync(
        out1.data(), gpu_ptrs_.t1, total * sizeof(cd),
        cudaMemcpyDeviceToHost, stream_compute_);
    if (error == cudaSuccess) {
        error = cudaMemcpyAsync(
            out2.data(), gpu_ptrs_.t2, total * sizeof(cd),
            cudaMemcpyDeviceToHost, stream_compute_);
    }

    // 2. 显式流同步：确保 CPU 下一行代码拿到的 out1/out2 是完整的
    if (error == cudaSuccess) {
        error = cudaStreamSynchronize(stream_compute_);
    }

    return error == cudaSuccess;
}

bool GMTIProcessor::exportDbsCacheAfterRecenter(const Config& cfg,
                                                const FusionBeamMeta& beamMeta,
                                                size_t slot,
                                                size_t Na,
                                                size_t Nr,
                                                RDData& rd,
                                                MetaPack& meta)
{
    if (Na == 0 || Nr == 0 || gpu_ptrs_.t1 == nullptr) {
        return false;
    }
    if (slot >= rd.amp.size() || slot >= rd.fd_axis.size() ||
        slot >= rd.rg_axis.size() || slot >= meta.beams.size()) {
        return false;
    }

    const size_t total = Na * Nr;
    Image2D<float> amp(static_cast<int>(Na), static_cast<int>(Nr));
    bool has_signal = false;
    if (!cuda_export_dbs_amplitude_sync(amp.buf, has_signal, total)) {
        return false;
    }

    std::vector<float> fd_axis(Na, 0.0f);
    const double df = (Na > 0) ? (cfg.PRF / static_cast<double>(Na)) : 0.0;
    for (size_t r = 0; r < Na; ++r) {
        fd_axis[r] = static_cast<float>(-0.5 * cfg.PRF + static_cast<double>(r) * df);
    }

    std::vector<float> rg_axis(Nr, 0.0f);
    for (size_t c = 0; c < Nr; ++c) {
        if (c < cfg.Rg.size()) {
            rg_axis[c] = static_cast<float>(cfg.Rg[c]);
        } else {
            rg_axis[c] = static_cast<float>(cfg.R_min + static_cast<double>(c) * cfg.R_bin);
        }
    }

    rd.nEff = static_cast<int>(Na);
    rd.amp[slot] = amp;
    if (rd.has_signal.size() < rd.amp.size()) {
        rd.has_signal.resize(rd.amp.size(), 0);
    }
    rd.has_signal[slot] = has_signal ? 1 : 0;
    rd.fd_axis[slot] = fd_axis;
    rd.rg_axis[slot] = rg_axis;

    MetaPerBeam m;
    m.vN = static_cast<float>(beamMeta.plane.V * gmti::trig_lut::sin(beamMeta.plane.V_angle * M_PI / 180.0));
    m.vE = static_cast<float>(beamMeta.plane.V * gmti::trig_lut::cos(beamMeta.plane.V_angle * M_PI / 180.0));
    m.vU = 0.0f;
    m.x = static_cast<float>(beamMeta.plane.E);
    m.y = static_cast<float>(beamMeta.plane.N);
    m.z = static_cast<float>(beamMeta.plane.H);
    m.fd_ctr = static_cast<float>(beamMeta.fd_ctr_wrapped);
    m.angle_deg = static_cast<float>(beamMeta.theta_sq);
    meta.beams[slot] = m;

    return true;
}

bool GMTIProcessor::debug_compare_range(const Config &cfg, int periodIdx)
{
    Config local_cfg = cfg;
    std::vector<std::complex<float>> data1, data2;
    std::vector<double> utc;
    double theta_sq = 0.0;

    if (!readPulseBlock(local_cfg, periodIdx, data1, data2, utc, theta_sq)) {
        std::cerr << "debug_compare_range: readPulseBlock failed" << std::endl;
        return false;
    }

    if (!local_cfg.isPC) {
        // CPU reference path
        std::vector<std::complex<double>> data1_d(data1.size());
        for (size_t i = 0; i < data1.size(); ++i) {
            data1_d[i] = std::complex<double>(data1[i].real(), data1[i].imag());
        }
        std::vector<std::complex<double>> cpu_out;
        if (!rangeCompressFFT(local_cfg, data1_d, cpu_out)) {
            std::cerr << "debug_compare_range: CPU rangeCompressFFT failed" << std::endl;
            return false;
        }

        // GPU cuFFT path (host-side convenience wrapper)
        std::vector<std::complex<float>> gpu_out;
        if (!rangeCompressCUFFT(data1, gpu_out, local_cfg)) {
            std::cerr << "debug_compare_range: GPU rangeCompressCUFFT failed" << std::endl;
            return false;
        }

        const size_t total = std::min(cpu_out.size(), gpu_out.size());
        double max_abs = 0.0;
        double sum_abs = 0.0;
        size_t max_idx = 0;
        std::vector<std::pair<double, size_t>> top;
        top.reserve(10);

        for (size_t i = 0; i < total; ++i) {
            const double cre = cpu_out[i].real();
            const double cim = cpu_out[i].imag();
            const double gre = (double)gpu_out[i].real();
            const double gim = (double)gpu_out[i].imag();
            const double abs_err = std::hypot(cre - gre, cim - gim);
            sum_abs += abs_err;
            if (abs_err > max_abs) {
                max_abs = abs_err;
                max_idx = i;
            }
            if (top.size() < 10) {
                top.emplace_back(abs_err, i);
                std::sort(top.begin(), top.end(), [](const std::pair<double,size_t>& a, const std::pair<double,size_t>& b){ return a.first > b.first; });
            } else if (abs_err > top.back().first) {
                top.back() = std::make_pair(abs_err, i);
                std::sort(top.begin(), top.end(), [](const std::pair<double,size_t>& a, const std::pair<double,size_t>& b){ return a.first > b.first; });
            }
        }

        const double mean_abs = total ? sum_abs / double(total) : 0.0;
        std::cout << "[RANGE-COMPARE] total=" << total
                  << " max_abs=" << max_abs
                  << " mean_abs=" << mean_abs
                  << " max_idx=" << max_idx << std::endl;
        std::cout << "[RANGE-COMPARE] cpu[max]=(" << cpu_out[max_idx].real() << "," << cpu_out[max_idx].imag()
                  << ") gpu[max]=(" << gpu_out[max_idx].real() << "," << gpu_out[max_idx].imag() << ")" << std::endl;
        std::cout << "[RANGE-COMPARE] top differences:" << std::endl;
        for (const auto &e : top) {
            const size_t i = e.second;
            std::cout << "  idx=" << i << " abs=" << e.first
                      << " cpu=(" << cpu_out[i].real() << "," << cpu_out[i].imag() << ")"
                      << " gpu=(" << gpu_out[i].real() << "," << gpu_out[i].imag() << ")" << std::endl;
        }
        return true;
    }

    std::cerr << "debug_compare_range: cfg.isPC=true not supported for this compare" << std::endl;
    return false;
}

// Debug helper: download device buffer `d1` (assumed cuFloatComplex) into host float complex vector
bool GMTIProcessor::debug_download_d1(std::vector<std::complex<float>> &out, size_t total) {
    if (gpu_ptrs_.d1 == nullptr) return false;
    out.resize(total);
    CUDA_CHECK(cudaMemcpyAsync(out.data(), gpu_ptrs_.d1, total * sizeof(cuFloatComplex), cudaMemcpyDeviceToHost, stream_compute_));
    CUDA_CHECK(cudaStreamSynchronize(stream_compute_));
    return true;
}
