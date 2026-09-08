#ifndef CHANNEL_CALIBRATION_HPP
#define CHANNEL_CALIBRATION_HPP

#include "config_structs.hpp"
#include "motion_comp.hpp"

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstddef>
#include <fstream>
#include <iomanip>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

namespace gmti {
namespace calibration {

struct ChannelCalibrationResult {
    bool enabled = false;
    bool valid = false;
    double phase_bias_rad = std::numeric_limits<double>::quiet_NaN();
    double reference_phase_rad = std::numeric_limits<double>::quiet_NaN();
    double applied_phase_bias_rad = 0.0;
    bool reference_valid = false;
    bool applied = false;
    double relative_gain_abs = std::numeric_limits<double>::quiet_NaN();
    double coherence = 0.0;
    double residual_rmse_rad = std::numeric_limits<double>::quiet_NaN();
    int sample_count = 0;
    int inlier_count = 0;
    bool range_shift_gate_enabled = false;
    bool range_alignment_valid = true;
    double estimated_range_shift_bins = std::numeric_limits<double>::quiet_NaN();
    double range_profile_correlation = 0.0;
    double range_profile_peak_margin = 0.0;
    std::string method = "ctdr_static_residual";
    std::string status = "disabled";
};

inline bool insideDetectionGuard(int row, int col,
                                 const std::vector<int>& detection_rows,
                                 const std::vector<int>& detection_cols,
                                 int row_guard, int range_guard)
{
    const std::size_t count = std::min(detection_rows.size(), detection_cols.size());
    for (std::size_t i = 0; i < count; ++i) {
        if (std::abs(row - detection_rows[i]) <= row_guard &&
            std::abs(col - detection_cols[i]) <= range_guard) {
            return true;
        }
    }
    return false;
}

struct RangeProfileShiftEstimate {
    bool valid = false;
    double shift_bins = std::numeric_limits<double>::quiet_NaN();
    double correlation = 0.0;
    double peak_margin = 0.0;
    int sample_count = 0;
};

// Estimate a small relative range displacement from channel magnitude profiles.
// Phase-only and scalar-gain errors leave this estimator at zero; a time/range
// shift moves the correlation peak.  Sub-bin refinement is the standard
// three-point parabolic interpolation around the best integer lag.  It is an
// alignment quality indicator, not a replacement for a calibrated delay solver.
inline RangeProfileShiftEstimate estimateRangeProfileShift(
    const std::vector<std::complex<float> >& ch1,
    const std::vector<std::complex<float> >& ch2,
    int rows, int cols,
    int az_st, int az_ed, int rg_st, int rg_ed,
    const std::vector<int>& detection_rows,
    const std::vector<int>& detection_cols,
    int row_guard, int range_guard,
    int range_stride,
    double min_correlation)
{
    RangeProfileShiftEstimate result;
    const std::size_t total = static_cast<std::size_t>(std::max(0, rows)) *
                              static_cast<std::size_t>(std::max(0, cols));
    if (rows <= 0 || cols <= 4 || ch1.size() != total || ch2.size() != total) {
        return result;
    }
    az_st = std::max(0, std::min(az_st, rows - 1));
    az_ed = std::max(0, std::min(az_ed, rows - 1));
    rg_st = std::max(2, std::min(rg_st, cols - 3));
    rg_ed = std::max(2, std::min(rg_ed, cols - 3));
    if (az_st > az_ed || rg_st > rg_ed) return result;

    const int max_lag = 2;
    std::vector<double> scores(static_cast<std::size_t>(2 * max_lag + 1), 0.0);
    std::vector<int> counts(scores.size(), 0);
    const int stride = std::max(1, range_stride);
    for (int lag = -max_lag; lag <= max_lag; ++lag) {
        double cross = 0.0;
        double p1 = 0.0;
        double p2 = 0.0;
        int count = 0;
        for (int row = az_st; row <= az_ed; ++row) {
            const std::size_t offset = static_cast<std::size_t>(row) *
                                       static_cast<std::size_t>(cols);
            for (int col = rg_st; col <= rg_ed; col += stride) {
                const int col2 = col + lag;
                if (col2 < 0 || col2 >= cols) continue;
                if (insideDetectionGuard(row, col, detection_rows, detection_cols,
                                         row_guard, range_guard) ||
                    insideDetectionGuard(row, col2, detection_rows, detection_cols,
                                         row_guard, range_guard)) {
                    continue;
                }
                const double a = std::abs(ch1[offset + static_cast<std::size_t>(col)]);
                const double b = std::abs(ch2[offset + static_cast<std::size_t>(col2)]);
                if (!std::isfinite(a) || !std::isfinite(b)) continue;
                cross += a * b;
                p1 += a * a;
                p2 += b * b;
                ++count;
            }
        }
        const std::size_t index = static_cast<std::size_t>(lag + max_lag);
        counts[index] = count;
        if (p1 > 0.0 && p2 > 0.0) {
            scores[index] = cross / std::sqrt(p1 * p2);
        }
    }

    const auto best_it = std::max_element(scores.begin(), scores.end());
    const int best_index = static_cast<int>(best_it - scores.begin());
    const int best_lag = best_index - max_lag;
    result.correlation = *best_it;
    result.sample_count = counts[static_cast<std::size_t>(best_index)];
    double second = 0.0;
    for (std::size_t index = 0; index < scores.size(); ++index) {
        if (static_cast<int>(index) != best_index) second = std::max(second, scores[index]);
    }
    result.peak_margin = result.correlation - second;
    double fractional = 0.0;
    if (best_index > 0 && best_index + 1 < static_cast<int>(scores.size())) {
        const double left = scores[static_cast<std::size_t>(best_index - 1)];
        const double center = scores[static_cast<std::size_t>(best_index)];
        const double right = scores[static_cast<std::size_t>(best_index + 1)];
        const double denominator = left - 2.0 * center + right;
        if (std::abs(denominator) > 1.0e-12) {
            fractional = 0.5 * (left - right) / denominator;
            fractional = std::max(-1.0, std::min(1.0, fractional));
        }
    }
    result.shift_bins = static_cast<double>(best_lag) + fractional;
    result.valid = result.sample_count > 0 && std::isfinite(result.shift_bins) &&
        std::isfinite(result.correlation) && result.correlation >= min_correlation;
    return result;
}

// Estimate the residual relative complex gain after removing the exact CTDR
// stationary-clutter phase. F1/F2 are the zero-PRT-alignment matrices after
// range-phase correction. Multiplying F1*conj(F2) by exp(+j*phi_fit[col])
// reconstructs the raw dual-receive interferometric phase. This preserves the
// physical CTDR phase; only the constant residual that the forward model cannot
// explain is identified as channel calibration bias.
inline ChannelCalibrationResult estimateStaticCtdrResidual(
    const Config& cfg,
    const GMTIOutput::Plane& plane,
    const std::vector<double>& fa_axis_unwrapped_hz,
    const std::vector<std::complex<float> >& f1_corrected,
    const std::vector<std::complex<float> >& f2_corrected,
    const std::vector<float>& range_phase_correction,
    int az_st, int az_ed, int rg_st, int rg_ed,
    const std::vector<int>& detection_rows,
    const std::vector<int>& detection_cols)
{
    ChannelCalibrationResult result;
    result.enabled = cfg.channel_calibration_enable;
    if (!result.enabled) {
        return result;
    }

    const int rows = effectivePulseNum(cfg);
    const int cols = cfg.rg_len;
    const std::size_t total = static_cast<std::size_t>(std::max(0, rows)) *
                              static_cast<std::size_t>(std::max(0, cols));
    if (rows <= 0 || cols <= 0 || f1_corrected.size() != total ||
        f2_corrected.size() != total ||
        fa_axis_unwrapped_hz.size() != static_cast<std::size_t>(rows) ||
        range_phase_correction.size() != static_cast<std::size_t>(cols)) {
        result.status = "invalid_input_shape";
        return result;
    }

    double lambda = cfg.lambda;
    if (!(lambda > 0.0) && cfg.fc > 0.0) {
        lambda = C / (cfg.fc * 1.0e9);
    }
    if (!(lambda > 0.0) || !(plane.V > 0.0) || !(cfg.d_channel > 0.0)) {
        result.status = "invalid_geometry";
        return result;
    }

    az_st = std::max(0, std::min(az_st, rows - 1));
    az_ed = std::max(0, std::min(az_ed, rows - 1));
    rg_st = std::max(0, std::min(rg_st, cols - 1));
    rg_ed = std::max(0, std::min(rg_ed, cols - 1));
    if (az_st > az_ed || rg_st > rg_ed) {
        result.status = "empty_support";
        return result;
    }

    struct Sample {
        std::complex<double> residual_unit;
        std::complex<double> residual_cross;
        double cross_weight = 0.0;
        double ch2_power = 0.0;
    };
    std::vector<Sample> samples;
    const int stride = std::max(1, cfg.channel_calibration_range_stride);
    const int row_guard = std::max(0, cfg.p38_refit_row_guard_bins);
    const int range_guard = std::max(0, cfg.p38_refit_range_guard_bins);
    result.range_shift_gate_enabled =
        cfg.channel_calibration_max_range_shift_bins >= 0.0;
    if (result.range_shift_gate_enabled) {
        const RangeProfileShiftEstimate range_shift = estimateRangeProfileShift(
            f1_corrected, f2_corrected, rows, cols,
            az_st, az_ed, rg_st, rg_ed,
            detection_rows, detection_cols,
            row_guard, range_guard,
            cfg.channel_calibration_range_stride,
            cfg.channel_calibration_min_range_correlation);
        result.estimated_range_shift_bins = range_shift.shift_bins;
        result.range_profile_correlation = range_shift.correlation;
        result.range_profile_peak_margin = range_shift.peak_margin;
        result.range_alignment_valid = range_shift.valid &&
            std::abs(range_shift.shift_bins) <=
                cfg.channel_calibration_max_range_shift_bins;
    }
    samples.reserve(static_cast<std::size_t>(az_ed - az_st + 1) *
                    static_cast<std::size_t>((rg_ed - rg_st) / stride + 1));

    for (int row = az_st; row <= az_ed; ++row) {
        const double af_geo = fa_axis_unwrapped_hz[static_cast<std::size_t>(row)];
        if (!std::isfinite(af_geo)) continue;
        const std::size_t row_offset = static_cast<std::size_t>(row) *
                                       static_cast<std::size_t>(cols);
        for (int col = rg_st; col <= rg_ed; col += stride) {
            if (insideDetectionGuard(row, col, detection_rows, detection_cols,
                                     row_guard, range_guard)) {
                continue;
            }
            const double range_m = !cfg.Rg.empty() &&
                static_cast<std::size_t>(col) < cfg.Rg.size()
                    ? cfg.Rg[static_cast<std::size_t>(col)]
                    : cfg.R_min + static_cast<double>(col) * cfg.R_bin;
            const double predicted = ctdrStaticPhaseFromAfGeo(
                cfg, plane, lambda, range_m, af_geo);
            if (!std::isfinite(predicted)) continue;

            const std::size_t offset = row_offset + static_cast<std::size_t>(col);
            const std::complex<double> a(f1_corrected[offset].real(),
                                         f1_corrected[offset].imag());
            const std::complex<double> b(f2_corrected[offset].real(),
                                         f2_corrected[offset].imag());
            const double p1 = std::norm(a);
            const double p2 = std::norm(b);
            if (!(p1 > 0.0) || !(p2 > 0.0) ||
                !std::isfinite(p1) || !std::isfinite(p2)) {
                continue;
            }
            const std::complex<double> corrected_cross = a * std::conj(b);
            const double phi_range = static_cast<double>(
                range_phase_correction[static_cast<std::size_t>(col)]);
            if (!std::isfinite(phi_range)) continue;
            const std::complex<double> raw_cross = corrected_cross *
                std::polar(1.0, phi_range);
            const double cross_abs = std::abs(raw_cross);
            if (!(cross_abs > 0.0) || !std::isfinite(cross_abs)) continue;
            const std::complex<double> residual_cross = raw_cross *
                std::polar(1.0, -predicted);
            Sample sample;
            sample.residual_unit = residual_cross / cross_abs;
            sample.residual_cross = residual_cross;
            sample.cross_weight = cross_abs;
            sample.ch2_power = p2;
            samples.push_back(sample);
        }
    }

    result.sample_count = static_cast<int>(std::min<std::size_t>(
        samples.size(), static_cast<std::size_t>(std::numeric_limits<int>::max())));
    if (result.sample_count < std::max(1, cfg.channel_calibration_min_sample_count)) {
        result.status = "insufficient_samples";
        return result;
    }

    std::vector<double> weights;
    weights.reserve(samples.size());
    for (const Sample& sample : samples) weights.push_back(sample.cross_weight);
    const std::size_t cap_index = static_cast<std::size_t>(
        0.95 * static_cast<double>(weights.size() - 1));
    std::nth_element(weights.begin(), weights.begin() + cap_index, weights.end());
    const double weight_cap = std::max(weights[cap_index],
                                       std::numeric_limits<double>::min());

    auto circular_sum = [&](double center, bool reject_outliers,
                            std::complex<double>& sum, double& sum_weight,
                            int& count) {
        sum = std::complex<double>(0.0, 0.0);
        sum_weight = 0.0;
        count = 0;
        const double threshold = std::max(0.05,
            cfg.channel_calibration_outlier_threshold_rad);
        for (const Sample& sample : samples) {
            if (reject_outliers) {
                const double residual = wrapPiLocal(
                    std::arg(sample.residual_unit) - center);
                if (std::abs(residual) > threshold) continue;
            }
            const double weight = std::min(sample.cross_weight, weight_cap);
            sum += weight * sample.residual_unit;
            sum_weight += weight;
            ++count;
        }
    };

    std::complex<double> initial_sum;
    double initial_weight = 0.0;
    int initial_count = 0;
    circular_sum(0.0, false, initial_sum, initial_weight, initial_count);
    if (!(initial_weight > 0.0) || !(std::abs(initial_sum) > 0.0)) {
        result.status = "zero_coherent_sum";
        return result;
    }
    const double initial_phase = std::arg(initial_sum);

    std::complex<double> robust_sum;
    double robust_weight = 0.0;
    int robust_count = 0;
    circular_sum(initial_phase, true, robust_sum, robust_weight, robust_count);
    result.inlier_count = robust_count;
    if (robust_count < std::max(1, cfg.channel_calibration_min_sample_count) ||
        !(robust_weight > 0.0) || !(std::abs(robust_sum) > 0.0)) {
        result.status = "insufficient_inliers";
        return result;
    }

    result.phase_bias_rad = std::arg(robust_sum);
    result.coherence = std::abs(robust_sum) / robust_weight;
    double weighted_sq = 0.0;
    double gain_den = 0.0;
    std::complex<double> gain_num(0.0, 0.0);
    const double threshold = std::max(0.05,
        cfg.channel_calibration_outlier_threshold_rad);
    for (const Sample& sample : samples) {
        const double residual = wrapPiLocal(
            std::arg(sample.residual_unit) - result.phase_bias_rad);
        if (std::abs(residual) > threshold) continue;
        const double weight = std::min(sample.cross_weight, weight_cap);
        weighted_sq += weight * residual * residual;
        const double scale = weight / sample.cross_weight;
        gain_num += scale * sample.residual_cross;
        gain_den += scale * sample.ch2_power;
    }
    result.residual_rmse_rad = std::sqrt(weighted_sq / robust_weight);
    if (gain_den > 0.0) {
        result.relative_gain_abs = std::abs(gain_num / gain_den);
    }
    result.valid = std::isfinite(result.phase_bias_rad) &&
        result.coherence >= cfg.channel_calibration_min_coherence &&
        result.residual_rmse_rad <= cfg.channel_calibration_max_rmse_rad &&
        result.range_alignment_valid;
    result.reference_valid = cfg.channel_calibration_reference_valid;
    result.reference_phase_rad = cfg.channel_calibration_reference_phase_rad;
    if (result.valid && result.reference_valid &&
        std::isfinite(result.reference_phase_rad)) {
        result.applied_phase_bias_rad = wrapPiLocal(
            result.phase_bias_rad - result.reference_phase_rad);
        result.applied = cfg.channel_calibration_apply_to_localization;
        result.status = "ok_relative_reference";
    } else if (result.valid) {
        result.status = "diagnostic_only_reference_required";
    } else if (!result.range_alignment_valid) {
        result.status = std::isfinite(result.estimated_range_shift_bins)
            ? "range_misalignment_scalar_calibration_rejected"
            : "range_alignment_unobservable";
    } else {
        result.status = "quality_gate_failed";
    }
    return result;
}

inline void writeChannelCalibrationDiagnostics(const Config& cfg,
                                               int beam_id,
                                               const ChannelCalibrationResult& result)
{
    if (!cfg.runtime_diagnostics_enabled || cfg.result_add.empty()) return;
    std::string path = cfg.result_add;
    if (path.back() != '/' && path.back() != '\\') path.push_back('/');
    path += "channel_calibration_beam";
    std::ostringstream name;
    name << std::setw(3) << std::setfill('0') << beam_id;
    path += name.str() + ".json";
    std::ofstream out(path.c_str());
    if (!out) return;
    out << std::setprecision(15)
        << "{\n"
        << "  \"beam_id\": " << beam_id << ",\n"
        << "  \"enabled\": " << (result.enabled ? "true" : "false") << ",\n"
        << "  \"valid\": " << (result.valid ? "true" : "false") << ",\n"
        << "  \"reference_valid\": "
        << (result.reference_valid ? "true" : "false") << ",\n"
        << "  \"applied\": " << (result.applied ? "true" : "false") << ",\n"
        << "  \"method\": \"" << result.method << "\",\n"
        << "  \"status\": \"" << result.status << "\",\n"
        << "  \"phase_bias_rad\": ";
    if (std::isfinite(result.phase_bias_rad)) out << result.phase_bias_rad;
    else out << "null";
    out << ",\n  \"phase_bias_deg\": ";
    if (std::isfinite(result.phase_bias_rad)) out << result.phase_bias_rad * 180.0 / M_PI;
    else out << "null";
    out << ",\n  \"reference_phase_rad\": ";
    if (std::isfinite(result.reference_phase_rad)) out << result.reference_phase_rad;
    else out << "null";
    out << ",\n  \"applied_phase_bias_rad\": "
        << result.applied_phase_bias_rad;
    out << ",\n  \"applied_phase_bias_deg\": "
        << result.applied_phase_bias_rad * 180.0 / M_PI;
    out << ",\n  \"relative_gain_abs\": ";
    if (std::isfinite(result.relative_gain_abs)) out << result.relative_gain_abs;
    else out << "null";
    out << ",\n  \"range_shift_gate_enabled\": "
        << (result.range_shift_gate_enabled ? "true" : "false")
        << ",\n  \"range_alignment_valid\": "
        << (result.range_alignment_valid ? "true" : "false")
        << ",\n  \"estimated_range_shift_bins\": ";
    if (std::isfinite(result.estimated_range_shift_bins))
        out << result.estimated_range_shift_bins;
    else out << "null";
    out << ",\n  \"range_profile_correlation\": "
        << result.range_profile_correlation
        << ",\n  \"range_profile_peak_margin\": "
        << result.range_profile_peak_margin
        << ",\n  \"coherence\": " << result.coherence
        << ",\n  \"residual_rmse_rad\": ";
    if (std::isfinite(result.residual_rmse_rad)) out << result.residual_rmse_rad;
    else out << "null";
    out << ",\n  \"sample_count\": " << result.sample_count
        << ",\n  \"inlier_count\": " << result.inlier_count
        << "\n}\n";
}

}  // namespace calibration
}  // namespace gmti

#endif
