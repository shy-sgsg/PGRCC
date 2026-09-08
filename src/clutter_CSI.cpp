#include "GMTIProcessor.hpp"
#include "p38_phase_fit.hpp"
#include "trig_lut.hpp"
#include <iostream>
#include <fstream>
#include <vector>
#include <chrono>
#include <cstdint>
#include <complex>
#include <cmath>
#include <algorithm> // lower_bound
#include <numeric>   // accumulate

namespace {

gmti::p38::PhaseFitOptions p38FitOptions(const Config& cfg, std::size_t support_size) {
    gmti::p38::PhaseFitOptions options;
    const std::size_t configured_min = static_cast<std::size_t>(
        std::max(2, cfg.p38_refit_min_sample_count));
    options.min_sample_count = std::min(support_size, configured_min);
    options.min_inlier_ratio = std::max(0.0, std::min(1.0, cfg.p38_refit_min_inlier_ratio));
    options.max_rmse_rad = std::max(0.0, cfg.p38_refit_max_rmse_rad);
    options.min_peak_relative_energy = std::max(
        0.0, cfg.p38_min_peak_row_energy_fraction);
    return options;
}

void copySupportPhaseTrace(const gmti::p38::PhaseFitResult& fit,
                           std::vector<float>* phase_trace) {
    if (phase_trace == nullptr) {
        return;
    }
    phase_trace->assign(fit.samples.size(), std::numeric_limits<float>::quiet_NaN());
    for (std::size_t i = 0; i < fit.samples.size(); ++i) {
        if (std::isfinite(fit.samples[i].phase)) {
            (*phase_trace)[i] = static_cast<float>(fit.samples[i].phase);
        }
    }
}

}  // namespace

bool GMTIProcessor::clutter_cancel_38_paper_1_p38(
    const std::vector<float>& y_faAxis,
    const std::vector<std::complex<float>>& F1f,
    const std::vector<std::complex<float>>& F2f,
    int az_st, int rg_st, int az_ed, int rg_ed,
    const Config& cfg,
    std::array<float,2>& p_38,
    std::vector<float>* phase_tra_38,
    std::string* fit_model_source
) {
    const size_t Na = static_cast<size_t>(effectivePulseNum(cfg));
    const size_t Nr = static_cast<size_t>(cfg.rg_len);
    const size_t total = Na * Nr;

    if (F1f.size() != total || F2f.size() != total) return false;
    if (y_faAxis.size() != Na) return false;

    az_st = std::max(0, std::min(az_st, int(Na) - 1));
    az_ed = std::max(0, std::min(az_ed, int(Na) - 1));
    rg_st = std::max(0, std::min(rg_st, int(Nr) - 1));
    rg_ed = std::max(0, std::min(rg_ed, int(Nr) - 1));
    if (az_st > az_ed) return false;
    if (rg_st > rg_ed) return false;

    const int M = az_ed - az_st + 1;
    std::vector<double> row_fa(static_cast<std::size_t>(M), 0.0);
    std::vector<std::complex<double>> row_cross(
        static_cast<std::size_t>(M), std::complex<double>(0.0, 0.0));
    std::vector<double> row_energy(static_cast<std::size_t>(M), 0.0);

#ifdef _OPENMP
    #pragma omp parallel for schedule(static)
#endif
    for (int i = 0; i < M; ++i) {
        const int rr = az_st + i;
        const size_t off = static_cast<size_t>(rr) * Nr;
        double num_re = 0.0;
        double num_im = 0.0;
        double energy_1 = 0.0;
        double energy_2 = 0.0;
        for (int c = rg_st; c <= rg_ed; ++c) {
            const auto &a = F1f[off + c];
            const auto &b = F2f[off + c];
            const double ar = static_cast<double>(a.real());
            const double ai = static_cast<double>(a.imag());
            const double br = static_cast<double>(b.real());
            const double bi = static_cast<double>(b.imag());
            num_re += ar * br + ai * bi;
            num_im += ai * br - ar * bi;
            energy_1 += ar * ar + ai * ai;
            energy_2 += br * br + bi * bi;
        }
        const std::size_t support_index = static_cast<std::size_t>(i);
        row_cross[support_index] = std::complex<double>(num_re, num_im);
        row_energy[support_index] = std::sqrt(std::max(0.0, energy_1 * energy_2));
        row_fa[support_index] = static_cast<double>(y_faAxis[static_cast<std::size_t>(rr)]);
    }

    const gmti::p38::PhaseFitOptions fit_options =
        p38FitOptions(cfg, static_cast<std::size_t>(M));
    const gmti::p38::PhaseFitResult observed = cfg.p38_enhanced_enable
        ? gmti::p38::fitPhaseSlopeMad(
              row_fa, row_cross, row_energy, std::vector<double>(), fit_options)
        : gmti::p38::fitPhaseSlope(
              row_fa, row_cross, row_energy, std::vector<double>(), fit_options);
    const gmti::p38::TheoryGuidedFitDecision decision =
        gmti::p38::selectPhaseFitWithTheoryFallback(
            row_fa, row_cross, row_energy, observed,
            cfg.p38_theory_guided_fallback,
            cfg.p38_expected_slope_rad_per_hz,
            cfg.p38_theory_prior_relative_span,
            cfg.p38_theory_prior_trigger_relative_error,
            std::vector<double>(),
            p38FitOptions(cfg, static_cast<std::size_t>(M)));
    gmti::p38::PhaseFitResult fit = decision.selected;
    std::string selected_source =
        gmti::p38::phaseFitModelSourceName(decision.source);
    if (!fit.valid && cfg.p38_enhanced_enable) {
        const gmti::p38::PhaseFitResult legacy =
            gmti::p38::fitPhaseSlopeLegacy(row_fa, row_cross, row_energy);
        if (legacy.valid) {
            fit = legacy;
            selected_source = "legacy_compat_fallback";
        }
    }
    if (fit_model_source != nullptr) {
        *fit_model_source = selected_source;
    }
    copySupportPhaseTrace(fit, phase_tra_38);
    DBG("[p38][cpu] support=" << M
        << " accepted=" << fit.sample_count
        << " inlier_ratio=" << fit.inlier_ratio
        << " rmse_rad=" << fit.rmse
        << " k=" << fit.k
        << " b=" << fit.b
        << " model_source="
        << selected_source
        << " observed_relative_theory_error="
        << decision.observed_relative_error
        << " valid=" << (fit.valid ? 1 : 0));
    if (!fit.valid) {
        return false;
    }
    p_38 = {static_cast<float>(fit.k), static_cast<float>(fit.b)};
    return true;
}

bool GMTIProcessor::clutter_cancel_38_paper_1(
    const std::vector<float>& y_faAxis,
    const std::vector<std::complex<float>>& F1f,
    const std::vector<std::complex<float>>& F2f,
    int az_st, int rg_st, int az_ed, int rg_ed,
    const Config& cfg,
    std::vector<std::complex<float>>& prosig_38,
    std::array<float,2>& p_38,
    std::vector<float>& phase_tra_38_cut,
    std::vector<float>& row_fa_cut,
    std::string* fit_model_source
) {
    auto wall_start = std::chrono::high_resolution_clock::now();

    const size_t Na = static_cast<size_t>(effectivePulseNum(cfg));
    const size_t Nr = static_cast<size_t>(cfg.rg_len);
    const size_t total = Na * Nr;

    if (F1f.size() != total || F2f.size() != total) return false;
    if (y_faAxis.size() != Na) return false;

    az_st = std::max(0, std::min(az_st, int(Na) - 1));
    az_ed = std::max(0, std::min(az_ed, int(Na) - 1));
    rg_st = std::max(0, std::min(rg_st, int(Nr) - 1));
    rg_ed = std::max(0, std::min(rg_ed, int(Nr) - 1));
    if (az_st > az_ed) return false;
    if (rg_st > rg_ed) return false;

    const int M = az_ed - az_st + 1;
    if (std::isfinite(cfg.p38_csi_override_k_rad_per_hz)) {
        // The paired S/C+N/S+C+N calibration path deliberately shares the
        // C+N-derived phase model.  Keep this opt-in so production fitting
        // remains completely unchanged, and mirror the CUDA implementation.
        p_38 = {static_cast<float>(cfg.p38_csi_override_k_rad_per_hz),
                static_cast<float>(cfg.p38_csi_override_b_rad)};
        phase_tra_38_cut.resize(static_cast<size_t>(M));
        for (int i = 0; i < M; ++i) {
            phase_tra_38_cut[static_cast<size_t>(i)] =
                p_38[0] * y_faAxis[static_cast<size_t>(az_st + i)] + p_38[1];
        }
        if (fit_model_source != nullptr) {
            *fit_model_source = "paired_override";
        }
    } else if (!clutter_cancel_38_paper_1_p38(
                   y_faAxis, F1f, F2f, az_st, rg_st, az_ed, rg_ed, cfg, p_38,
                   &phase_tra_38_cut, fit_model_source)) {
        return false;
    }

    row_fa_cut.resize(M);
    for (int i = 0; i < M; ++i) {
        row_fa_cut[i] = y_faAxis[size_t(az_st + i)];
    }

    prosig_38.assign(total, std::complex<float>(0.0f, 0.0f));
#ifdef _OPENMP
    #pragma omp parallel for schedule(static)
#endif
    for (size_t r = 0; r < Na; ++r) {
        // The azimuth support is the estimated stationary-clutter Doppler
        // support, not merely a detector display band.  Outside it the input
        // is noise plus moving targets; applying a data-dependent equalizer
        // there changes the noise law without removing stationary clutter.
        // Keep those rows linear and untouched, while the detector may still
        // consume the full CSI image.
        if (r < static_cast<size_t>(az_st) || r > static_cast<size_t>(az_ed)) {
            const size_t off = r * Nr;
            std::copy_n(F1f.begin() + static_cast<std::ptrdiff_t>(off),
                        Nr, prosig_38.begin() + static_cast<std::ptrdiff_t>(off));
            continue;
        }
        if (cfg.csi_cancellation_mode == "row_complex_ls" ||
            cfg.csi_cancellation_mode == "row_phase_ls_linear" ||
            cfg.csi_cancellation_mode == "row_phase_ls_min_magnitude") {
            std::complex<double> cross(0.0, 0.0);
            double energy1 = 0.0;
            double energy2 = 0.0;
            const size_t off = r * Nr;
            for (int c = rg_st; c <= rg_ed; ++c) {
                const std::complex<double> a(F1f[off + static_cast<size_t>(c)]);
                const std::complex<double> b(F2f[off + static_cast<size_t>(c)]);
                cross += a * std::conj(b);
                energy1 += std::norm(a);
                energy2 += std::norm(b);
            }
            std::complex<float> alpha = energy2 > 0.0
                ? std::complex<float>(cross / energy2)
                : std::complex<float>(0.0f, 0.0f);
            const double cross_abs = std::abs(cross);
            const double coherence =
                (energy1 > 0.0 && energy2 > 0.0)
                    ? cross_abs / std::sqrt(energy1 * energy2) : 0.0;
            if (cfg.csi_cancellation_mode == "row_phase_ls_linear" ||
                cfg.csi_cancellation_mode == "row_phase_ls_min_magnitude") {
                const float alpha_abs = std::abs(alpha);
                alpha = alpha_abs > 0.0f
                    ? alpha / alpha_abs : std::complex<float>(1.0f, 0.0f);
            }
            const bool bypass_nonlinear_row =
                cfg.csi_row_coherence_gate_enable &&
                coherence < cfg.csi_row_coherence_min;
            for (size_t c = 0; c < Nr; ++c) {
                if (cfg.csi_cancellation_mode == "row_complex_ls") {
                    prosig_38[off + c] = (cfg.csi_bypass_enable || bypass_nonlinear_row)
                        ? F1f[off + c]
                        : F1f[off + c] - alpha * F2f[off + c];
                } else if (cfg.csi_cancellation_mode == "row_phase_ls_linear") {
                    prosig_38[off + c] = (cfg.csi_bypass_enable || bypass_nonlinear_row)
                        ? F1f[off + c]
                        : F1f[off + c] -
                              static_cast<float>(cfg.csi_subtraction_gain) *
                                  alpha * F2f[off + c];
                } else {
                    const auto& a = F1f[off + c];
                    if (bypass_nonlinear_row) {
                        prosig_38[off + c] = a;
                        continue;
                    }
                    const auto b2 = alpha * F2f[off + c];
                    const float aa = std::abs(a), bb = std::abs(b2);
                    const float m = std::min(aa, bb);
                    const auto a_eq = aa > 0.0f ? (m / aa) * a
                                                : std::complex<float>(0.0f, 0.0f);
                    const auto b_eq = bb > 0.0f ? (m / bb) * b2
                                                : std::complex<float>(0.0f, 0.0f);
                    prosig_38[off + c] = cfg.csi_bypass_enable
                        ? a_eq : (a_eq - static_cast<float>(cfg.csi_subtraction_gain) * b_eq);
                }
            }
            continue;
        }
        const float phi = p_38[0] * y_faAxis[r] + p_38[1];
        const float cs = static_cast<float>(std::cos(phi));
        const float sn = static_cast<float>(std::sin(phi));
        const std::complex<float> az_fai(cs, sn);
        const size_t off = r * Nr;
        for (size_t c = 0; c < Nr; ++c) {
            const auto &a = F1f[off + c];
            const auto b2 = az_fai * F2f[off + c];
            const float aa = std::abs(a), bb = std::abs(b2);
            const float m = (aa < bb) ? aa : bb;
            const auto a_eq = (aa > 0.0f) ? (m / aa) * a : std::complex<float>(0.0f, 0.0f);
            const auto b_eq = (bb > 0.0f) ? (m / bb) * b2 : std::complex<float>(0.0f, 0.0f);
            prosig_38[off + c] = cfg.csi_bypass_enable ? a_eq : (a_eq - b_eq);
        }
    }

    auto wall_end = std::chrono::high_resolution_clock::now();
    std::chrono::duration<double, std::milli> wall_ms = wall_end - wall_start;
    (void)wall_ms;
    return true;
}
