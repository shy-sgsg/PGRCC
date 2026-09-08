#include "p38_phase_fit.hpp"
#include "p38_refit_utils.hpp"

#include <cmath>
#include <complex>
#include <cstddef>
#include <iomanip>
#include <iostream>
#include <limits>
#include <random>
#include <vector>

namespace {

#define CHECK_OR_RETURN(condition)                                                \
    do {                                                                          \
        if (!(condition)) {                                                       \
            std::cerr << "p38 phase fit selftest failed: " #condition            \
                      << " at line " << __LINE__ << '\n';                        \
            return 1;                                                             \
        }                                                                         \
    } while (false)

double pi() {
    return 3.141592653589793238462643383279502884;
}

double wrapToPi(double value) {
    const double two_pi = 2.0 * pi();
    value = std::fmod(value + pi(), two_pi);
    if (value < 0.0) {
        value += two_pi;
    }
    return value - pi();
}

std::complex<double> polarCross(double magnitude, double phase) {
    return std::complex<double>(magnitude * std::cos(phase),
                                magnitude * std::sin(phase));
}

struct RansacLineFit {
    double k = std::numeric_limits<double>::quiet_NaN();
    double b = std::numeric_limits<double>::quiet_NaN();
    double rmse = std::numeric_limits<double>::quiet_NaN();
    double inlier_ratio = 0.0;
    std::size_t sample_count = 0U;
    bool valid = false;
};

bool ordinaryLineFit(const std::vector<double>& x,
                     const std::vector<double>& y,
                     const std::vector<unsigned char>& mask,
                     double& k,
                     double& b) {
    if (x.size() != y.size() || x.size() != mask.size()) {
        return false;
    }
    double count = 0.0;
    double sum_x = 0.0;
    double sum_y = 0.0;
    for (std::size_t i = 0U; i < x.size(); ++i) {
        if (!mask[i]) {
            continue;
        }
        count += 1.0;
        sum_x += x[i];
        sum_y += y[i];
    }
    if (!(count >= 2.0)) {
        return false;
    }
    const double mean_x = sum_x / count;
    const double mean_y = sum_y / count;
    double covariance = 0.0;
    double variance = 0.0;
    for (std::size_t i = 0U; i < x.size(); ++i) {
        if (!mask[i]) {
            continue;
        }
        const double dx = x[i] - mean_x;
        covariance += dx * (y[i] - mean_y);
        variance += dx * dx;
    }
    if (!(variance > std::numeric_limits<double>::epsilon())) {
        return false;
    }
    k = covariance / variance;
    b = mean_y - k * mean_x;
    return std::isfinite(k) && std::isfinite(b);
}

RansacLineFit fitLineRansac(const std::vector<double>& x,
                            const std::vector<double>& y,
                            double inlier_threshold_rad,
                            int iterations) {
    RansacLineFit result;
    if (x.size() != y.size() || x.size() < 2U ||
        !(inlier_threshold_rad > 0.0) || iterations <= 0) {
        return result;
    }

    std::mt19937 generator(20260907U);
    std::uniform_int_distribution<std::size_t> index_dist(0U, x.size() - 1U);
    std::vector<unsigned char> best_mask(x.size(), 0U);
    std::size_t best_count = 0U;
    double best_residual_sum = std::numeric_limits<double>::infinity();

    const auto evaluate = [&](double k, double b,
                              std::vector<unsigned char>& mask,
                              std::size_t& count,
                              double& residual_sum) {
        count = 0U;
        residual_sum = 0.0;
        std::fill(mask.begin(), mask.end(), 0U);
        for (std::size_t i = 0U; i < x.size(); ++i) {
            const double residual = std::fabs(y[i] - (k * x[i] + b));
            if (residual <= inlier_threshold_rad) {
                mask[i] = 1U;
                ++count;
                residual_sum += residual;
            }
        }
    };

    for (int iteration = 0; iteration < iterations; ++iteration) {
        const std::size_t first = index_dist(generator);
        std::size_t second = index_dist(generator);
        if (first == second) {
            second = (second + 1U) % x.size();
        }
        const double dx = x[second] - x[first];
        if (!(std::fabs(dx) > std::numeric_limits<double>::epsilon())) {
            continue;
        }
        const double k = (y[second] - y[first]) / dx;
        const double b = y[first] - k * x[first];
        std::vector<unsigned char> mask(x.size(), 0U);
        std::size_t count = 0U;
        double residual_sum = 0.0;
        evaluate(k, b, mask, count, residual_sum);
        if (count > best_count ||
            (count == best_count && residual_sum < best_residual_sum)) {
            best_count = count;
            best_residual_sum = residual_sum;
            best_mask.swap(mask);
        }
    }

    if (best_count < 2U || !ordinaryLineFit(x, y, best_mask,
                                             result.k, result.b)) {
        return result;
    }

    // Refine once using the consensus set, then reselect it so the reported
    // error and inlier ratio are tied to the final line rather than the seed.
    for (int refinement = 0; refinement < 2; ++refinement) {
        std::size_t count = 0U;
        double residual_sum = 0.0;
        evaluate(result.k, result.b, best_mask, count, residual_sum);
        if (count < 2U || !ordinaryLineFit(x, y, best_mask,
                                           result.k, result.b)) {
            return RansacLineFit();
        }
    }

    double squared_error = 0.0;
    for (std::size_t i = 0U; i < x.size(); ++i) {
        if (!best_mask[i]) {
            continue;
        }
        const double residual = y[i] - (result.k * x[i] + result.b);
        squared_error += residual * residual;
        ++result.sample_count;
    }
    if (result.sample_count < 2U) {
        return RansacLineFit();
    }
    result.rmse = std::sqrt(squared_error /
                            static_cast<double>(result.sample_count));
    result.inlier_ratio = static_cast<double>(result.sample_count) /
                          static_cast<double>(x.size());
    result.valid = std::isfinite(result.rmse) &&
                   std::isfinite(result.inlier_ratio);
    return result;
}

}  // namespace

int main() {
    const double truth_k = -0.008878975;
    const double truth_b = 0.73;
    const std::size_t row_count = 112U;
    const std::size_t az_st = 19U;
    const std::size_t az_ed = 94U;
    const std::size_t low_power_1 = 27U;
    const std::size_t low_power_2 = 69U;
    const std::size_t low_coherence = 39U;
    const std::size_t outlier_1 = 51U;
    const std::size_t outlier_2 = 78U;

    std::vector<double> fa(row_count, 0.0);
    std::vector<std::complex<double> > cross(
        row_count, std::complex<double>(0.0, 0.0));
    std::vector<double> energy(row_count, 0.0);
    std::vector<double> weight(row_count, 1.0);
    std::vector<double> observed_fa;
    std::vector<double> observed_phase;

    int clean_wrap_count = 0;
    double previous_clean_phase = 0.0;
    for (std::size_t i = 0U; i < row_count; ++i) {
        fa[i] = -1300.0 + 20.0 * static_cast<double>(i);
        if (i < az_st || i > az_ed) {
            continue;  // Explicit support-exterior zeros.
        }

        const double clean_phase = wrapToPi(truth_k * fa[i] + truth_b);
        if (i > az_st && std::fabs(clean_phase - previous_clean_phase) > pi()) {
            ++clean_wrap_count;
        }
        previous_clean_phase = clean_phase;

        const double noise =
            0.0012 * std::sin(0.37 * static_cast<double>(i)) +
            0.0005 * std::cos(0.19 * static_cast<double>(i));
        double phase = truth_k * fa[i] + truth_b + noise;
        double coherence = 0.96;
        energy[i] = 80.0 + 4.0 * std::sin(0.11 * static_cast<double>(i));

        if (i == low_power_1 || i == low_power_2) {
            energy[i] = 1.0e-10;
            coherence = 0.99;
        }
        if (i == low_coherence) {
            coherence = 0.01;
        }
        if (i == outlier_1) {
            phase += 2.35;
            energy[i] = 1.0e6;  // Must not make this outlier dominate.
            coherence = 0.98;
        }
        if (i == outlier_2) {
            phase -= 2.55;
            coherence = 0.97;
        }
        if (energy[i] > 1.0e-12 && coherence >= 0.10) {
            observed_fa.push_back(fa[i]);
            observed_phase.push_back(phase);
        }
        cross[i] = polarCross(coherence * energy[i], phase);
    }
    CHECK_OR_RETURN(clean_wrap_count >= 2);

    gmti::p38::PhaseFitOptions options;
    options.min_relative_energy = 1.0e-6;
    options.min_coherence = 0.10;
    options.huber_delta_rad = 0.04;
    options.inlier_threshold_rad = 0.03;
    options.max_rmse_rad = 0.004;
    options.min_inlier_ratio = 0.90;
    options.min_sample_count = 40U;

    const gmti::p38::PhaseFitResult full =
        gmti::p38::fitPhaseSlope(fa, cross, energy, weight, options);

    const std::vector<double> slice_fa(fa.begin() + az_st,
                                       fa.begin() + az_ed + 1U);
    const std::vector<std::complex<double> > slice_cross(
        cross.begin() + az_st, cross.begin() + az_ed + 1U);
    const std::vector<double> slice_energy(energy.begin() + az_st,
                                           energy.begin() + az_ed + 1U);
    const std::vector<double> slice_weight(weight.begin() + az_st,
                                           weight.begin() + az_ed + 1U);
    const gmti::p38::PhaseFitResult sliced = gmti::p38::fitPhaseSlope(
        slice_fa, slice_cross, slice_energy, slice_weight, options);
    const gmti::p38::PhaseFitResult mad = gmti::p38::fitPhaseSlopeMad(
        fa, cross, energy, weight, options, 3.5, 5, 1.0e-3);
    const RansacLineFit ransac = fitLineRansac(
        observed_fa, observed_phase, 0.08, 512);

    CHECK_OR_RETURN(full.valid);
    CHECK_OR_RETURN(sliced.valid);
    CHECK_OR_RETURN(mad.valid);
    CHECK_OR_RETURN(ransac.valid);
    CHECK_OR_RETURN(full.samples.size() == row_count);
    CHECK_OR_RETURN(sliced.samples.size() == (az_ed - az_st + 1U));
    CHECK_OR_RETURN(full.sample_count == (az_ed - az_st + 1U) - 3U);
    CHECK_OR_RETURN(sliced.sample_count == full.sample_count);
    CHECK_OR_RETURN(std::fabs(full.k - sliced.k) < 1.0e-13);
    std::cout << "[p38] huber k=" << full.k
              << " rmse=" << full.rmse
              << " inlier_ratio=" << full.inlier_ratio
              << " sample_count=" << full.sample_count
              << " mad k=" << mad.k
              << " rmse=" << mad.rmse
              << " inlier_ratio=" << mad.inlier_ratio
              << " sample_count=" << mad.sample_count << '\n';
    const double ransac_relative_slope_error =
        std::fabs((ransac.k - truth_k) / truth_k);
    const double ransac_circular_intercept_error =
        std::fabs(wrapToPi(ransac.b - truth_b));
    std::cout << "[p38] compare current_huber k=" << full.k
              << " rmse=" << full.rmse
              << " mad k=" << mad.k
              << " rmse=" << mad.rmse
              << " ransac k=" << ransac.k
              << " rmse=" << ransac.rmse
              << " inlier_ratio=" << ransac.inlier_ratio
              << " sample_count=" << ransac.sample_count << '\n';

    const double relative_slope_error =
        std::fabs((full.k - truth_k) / truth_k);
    const double circular_intercept_error =
        std::fabs(wrapToPi(full.b - truth_b));
    CHECK_OR_RETURN(relative_slope_error < 1.0e-4);
    CHECK_OR_RETURN(circular_intercept_error < 8.0e-4);
    CHECK_OR_RETURN(full.rmse < 0.002);
    CHECK_OR_RETURN(full.inlier_ratio > 0.96);
    CHECK_OR_RETURN(std::fabs(mad.k - truth_k) / std::fabs(truth_k) < 1.0e-4);
    CHECK_OR_RETURN(mad.rmse <= full.rmse + 1.0e-6);
    CHECK_OR_RETURN(ransac_relative_slope_error < 1.0e-4);
    CHECK_OR_RETURN(ransac_circular_intercept_error < 8.0e-4);
    CHECK_OR_RETURN(ransac.rmse < 0.01);
    CHECK_OR_RETURN(ransac.inlier_ratio > 0.90);

    std::vector<double> metric_phase;
    std::vector<double> metric_fa;
    for (std::size_t i = 0U; i < full.samples.size(); ++i) {
        if (std::isfinite(full.samples[i].phase) && std::isfinite(fa[i])) {
            metric_phase.push_back(full.samples[i].phase);
            metric_fa.push_back(fa[i]);
        }
    }
    const P38StageMetrics metric_summary = evaluateP38FitMetrics(
        metric_phase, metric_fa, std::array<double, 2>{{full.k, full.b}});
    CHECK_OR_RETURN(metric_summary.valid);
    CHECK_OR_RETURN(std::isfinite(metric_summary.median_abs_residual));
    CHECK_OR_RETURN(metric_summary.median_abs_residual <=
                    metric_summary.p90_abs_residual);
    CHECK_OR_RETURN(metric_summary.p90_abs_residual <=
                    metric_summary.p95_abs_residual);
    CHECK_OR_RETURN(metric_summary.p95_abs_residual <=
                    metric_summary.max_abs_residual);
    std::cout << "[p38] residual_abs median="
              << metric_summary.median_abs_residual
              << " p90=" << metric_summary.p90_abs_residual
              << " p95=" << metric_summary.p95_abs_residual
              << " max=" << metric_summary.max_abs_residual << '\n';

    CHECK_OR_RETURN(!full.samples[low_power_1].used);
    CHECK_OR_RETURN(!full.samples[low_power_2].used);
    CHECK_OR_RETURN(!full.samples[low_coherence].used);
    CHECK_OR_RETURN(!full.samples[outlier_1].used);
    CHECK_OR_RETURN(!full.samples[outlier_2].used);
    CHECK_OR_RETURN(!mad.samples[outlier_1].used);
    CHECK_OR_RETURN(!mad.samples[outlier_2].used);
    CHECK_OR_RETURN(std::fabs(full.samples[outlier_1].residual) > 2.0);
    CHECK_OR_RETURN(std::fabs(full.samples[outlier_2].residual) > 2.0);
    CHECK_OR_RETURN(!full.samples[az_st - 1U].used);
    CHECK_OR_RETURN(!full.samples[az_ed + 1U].used);
    CHECK_OR_RETURN(std::isnan(full.samples[az_st - 1U].phase));

    double first_unwrapped = std::numeric_limits<double>::quiet_NaN();
    double last_unwrapped = std::numeric_limits<double>::quiet_NaN();
    double previous_unwrapped = std::numeric_limits<double>::quiet_NaN();
    for (std::size_t i = az_st; i <= az_ed; ++i) {
        const double phase = full.samples[i].phase;
        if (!std::isfinite(phase)) {
            continue;
        }
        if (!std::isfinite(first_unwrapped)) {
            first_unwrapped = phase;
        }
        if (std::isfinite(previous_unwrapped)) {
            CHECK_OR_RETURN(std::fabs(phase - previous_unwrapped) < pi());
        }
        previous_unwrapped = phase;
        last_unwrapped = phase;
    }
    CHECK_OR_RETURN(std::fabs(last_unwrapped - first_unwrapped) > 4.0 * pi());

    // Sparse accepted rows can be separated by more than pi of phase travel;
    // plain adjacent unwrapping then has an alias choice. Verify that the
    // theory-bounded circular search recovers the observation-supported slope
    // without simply returning the prior value.
    {
        const std::size_t n = 45U;
        std::vector<double> prior_fa(n, 0.0);
        std::vector<std::complex<double> > prior_cross(
            n, std::complex<double>(0.0, 0.0));
        std::vector<double> prior_energy(n, 0.0);
        for (std::size_t i = 0U; i < n; ++i) {
            prior_fa[i] = -220.0 + 10.0 * static_cast<double>(i);
            if (i <= 2U || i >= 39U) {
                prior_energy[i] = 100.0;
                const double phase = truth_k * prior_fa[i] + truth_b +
                    0.001 * std::sin(0.7 * static_cast<double>(i));
                prior_cross[i] = polarCross(98.0, phase);
            }
        }
        gmti::p38::PhaseFitOptions prior_options;
        prior_options.min_sample_count = 8U;
        prior_options.min_inlier_ratio = 0.80;
        prior_options.max_rmse_rad = 0.02;
        prior_options.inlier_threshold_rad = 0.05;
        const double analytic_prior = truth_k * 1.08;
        const gmti::p38::PhaseFitResult guided =
            gmti::p38::fitPhaseSlopeNearPrior(
                prior_fa, prior_cross, prior_energy,
                analytic_prior, 0.25, std::vector<double>(), prior_options);
        CHECK_OR_RETURN(guided.valid);
        CHECK_OR_RETURN(std::fabs((guided.k - truth_k) / truth_k) < 0.01);
        CHECK_OR_RETURN(std::fabs(guided.k - analytic_prior) > 1.0e-4);
    }

    // A fixed-slope fallback recovers only the observation-supported
    // intercept and must still reject insufficient evidence.
    {
        const std::size_t n = 45U;
        std::vector<double> fixed_fa(n, 0.0);
        std::vector<std::complex<double> > fixed_cross(
            n, std::complex<double>(0.0, 0.0));
        std::vector<double> fixed_energy(n, 0.0);
        for (std::size_t i = 0U; i < n; ++i) {
            fixed_fa[i] = -220.0 + 10.0 * static_cast<double>(i);
            if (i >= 8U && i <= 32U) {
                fixed_energy[i] = 100.0;
                double phase = truth_k * fixed_fa[i] + truth_b +
                    0.003 * std::sin(0.9 * static_cast<double>(i));
                if (i == 13U || i == 27U) phase += 2.2;
                fixed_cross[i] = polarCross(97.0, phase);
            }
        }
        gmti::p38::PhaseFitOptions fixed_options;
        fixed_options.min_sample_count = 12U;
        fixed_options.min_inlier_ratio = 0.80;
        fixed_options.max_rmse_rad = 0.02;
        fixed_options.inlier_threshold_rad = 0.05;
        const gmti::p38::PhaseFitResult fixed =
            gmti::p38::fitPhaseInterceptAtFixedSlope(
                fixed_fa, fixed_cross, fixed_energy, truth_k,
                std::vector<double>(), fixed_options);
        CHECK_OR_RETURN(fixed.valid);
        CHECK_OR_RETURN(fixed.k == truth_k);
        CHECK_OR_RETURN(std::fabs(wrapToPi(fixed.b - truth_b)) < 0.01);
        CHECK_OR_RETURN(fixed.inlier_ratio > 0.90);

        fixed_options.min_sample_count = 30U;
        const gmti::p38::PhaseFitResult insufficient =
            gmti::p38::fitPhaseInterceptAtFixedSlope(
                fixed_fa, fixed_cross, fixed_energy, truth_k,
                std::vector<double>(), fixed_options);
        CHECK_OR_RETURN(!insufficient.valid);
    }

    // CPU and CUDA call the same policy: preserve a physically consistent
    // observed slope, but reject a valid-looking biased slope and use the CTDR
    // slope only when measured phases still support a coherent intercept.
    {
        const gmti::p38::TheoryGuidedFitDecision clean_decision =
            gmti::p38::selectPhaseFitWithTheoryFallback(
                fa, cross, energy, full, true, truth_k, 0.50, 0.10,
                weight, options);
        CHECK_OR_RETURN(clean_decision.selected.valid);
        CHECK_OR_RETURN(
            clean_decision.source == gmti::p38::PhaseFitModelSource::Observed);
        CHECK_OR_RETURN(!clean_decision.attempted);

        const std::size_t n = 25U;
        const double biased_k = truth_k * 0.75;
        std::vector<double> biased_fa(n, 0.0);
        std::vector<std::complex<double> > biased_cross(
            n, std::complex<double>(0.0, 0.0));
        std::vector<double> biased_energy(n, 100.0);
        for (std::size_t i = 0U; i < n; ++i) {
            biased_fa[i] = -120.0 + 10.0 * static_cast<double>(i);
            const double phase = biased_k * biased_fa[i] + truth_b +
                0.002 * std::sin(0.5 * static_cast<double>(i));
            biased_cross[i] = polarCross(98.0, phase);
        }
        gmti::p38::PhaseFitOptions biased_options;
        biased_options.min_sample_count = 12U;
        biased_options.min_inlier_ratio = 0.80;
        biased_options.max_rmse_rad = 0.25;
        biased_options.inlier_threshold_rad = 0.35;
        const gmti::p38::PhaseFitResult biased_observed =
            gmti::p38::fitPhaseSlope(
                biased_fa, biased_cross, biased_energy,
                std::vector<double>(), biased_options);
        CHECK_OR_RETURN(biased_observed.valid);
        CHECK_OR_RETURN(
            gmti::p38::relativeSlopeError(biased_observed.k, truth_k) > 0.20);
        const gmti::p38::TheoryGuidedFitDecision biased_decision =
            gmti::p38::selectPhaseFitWithTheoryFallback(
                biased_fa, biased_cross, biased_energy, biased_observed,
                true, truth_k, 0.50, 0.10,
                std::vector<double>(), biased_options);
        CHECK_OR_RETURN(biased_decision.attempted);
        CHECK_OR_RETURN(biased_decision.selected.valid);
        CHECK_OR_RETURN(
            biased_decision.source ==
            gmti::p38::PhaseFitModelSource::TheoryFixedSlope);
        CHECK_OR_RETURN(biased_decision.selected.k == truth_k);
        CHECK_OR_RETURN(biased_decision.selected.rmse < 0.20);

        const gmti::p38::TheoryGuidedFitDecision disabled_decision =
            gmti::p38::selectPhaseFitWithTheoryFallback(
                biased_fa, biased_cross, biased_energy, biased_observed,
                false, truth_k, 0.50, 0.10,
                std::vector<double>(), biased_options);
        CHECK_OR_RETURN(
            disabled_decision.source == gmti::p38::PhaseFitModelSource::Observed);
        CHECK_OR_RETURN(disabled_decision.selected.k == biased_observed.k);
    }

    // Multiple very strong moving-target rows must not make the CTDR theory
    // fallback estimate its intercept from absolute-peak rows alone.  The
    // lower-power but broad static-clutter support remains observable.
    {
        const std::size_t n = 45U;
        std::vector<double> multi_fa(n, 0.0);
        std::vector<std::complex<double> > multi_cross(
            n, std::complex<double>(0.0, 0.0));
        std::vector<double> multi_energy(n, 100.0);
        for (std::size_t i = 0U; i < n; ++i) {
            multi_fa[i] = -220.0 + 10.0 * static_cast<double>(i);
            const double phase = truth_k * multi_fa[i] + truth_b +
                0.004 * std::sin(0.43 * static_cast<double>(i));
            multi_cross[i] = polarCross(97.0, phase);
        }
        multi_energy[11U] = 1.0e8;
        multi_energy[33U] = 1.0e8;
        multi_cross[11U] = polarCross(9.8e7, 2.20);
        multi_cross[33U] = polarCross(9.8e7, -1.10);

        gmti::p38::PhaseFitOptions multi_options;
        multi_options.min_sample_count = 8U;
        multi_options.min_inlier_ratio = 0.60;
        multi_options.max_rmse_rad = 0.60;
        multi_options.inlier_threshold_rad = 0.35;
        multi_options.min_peak_relative_energy = 0.05;
        gmti::p38::PhaseFitResult invalid_observed;
        invalid_observed.k = 0.0;
        invalid_observed.valid = false;
        const gmti::p38::TheoryGuidedFitDecision multi_decision =
            gmti::p38::selectPhaseFitWithTheoryFallback(
                multi_fa, multi_cross, multi_energy, invalid_observed,
                true, truth_k, 0.50, 0.10,
                std::vector<double>(), multi_options);
        CHECK_OR_RETURN(multi_decision.selected.valid);
        CHECK_OR_RETURN(
            multi_decision.source ==
            gmti::p38::PhaseFitModelSource::TheoryFixedSlope);
        CHECK_OR_RETURN(multi_decision.selected.sample_count >= n - 2U);
        CHECK_OR_RETURN(
            std::fabs(wrapToPi(multi_decision.selected.b - truth_b)) < 0.02);
    }

    std::vector<double> zero_energy(12U, 0.0);
    std::vector<double> zero_fa(12U, 0.0);
    std::vector<std::complex<double> > zero_cross(
        12U, std::complex<double>(0.0, 0.0));
    const gmti::p38::PhaseFitResult empty =
        gmti::p38::fitPhaseSlope(zero_fa, zero_cross, zero_energy);
    CHECK_OR_RETURN(!empty.valid);
    CHECK_OR_RETURN(empty.sample_count == 0U);

    {
        const int Na = 4;
        const int Nr = 32;
        std::vector<std::complex<float> > f1(
            static_cast<std::size_t>(Na * Nr), std::complex<float>(1.0f, 0.0f));
        std::vector<std::complex<float> > f2(
            static_cast<std::size_t>(Na * Nr), std::complex<float>(1.0f, 0.0f));
        for (int r = 0; r < Na; ++r) {
            f1[static_cast<std::size_t>(r * Nr + 16)] =
                std::complex<float>(100.0f, 0.0f);
        }
        std::vector<uint8_t> mask(static_cast<std::size_t>(Na * Nr), 0U);
        const int masked = addStrongRangeOutlierMask(
            f1, f2, Na, Nr, 0, 0, Na - 1, Nr - 1, mask, 10.0, 2);
        CHECK_OR_RETURN(masked == 5);
        for (int r = 0; r < Na; ++r) {
            for (int c = 14; c <= 18; ++c) {
                CHECK_OR_RETURN(mask[static_cast<std::size_t>(r * Nr + c)] != 0U);
            }
        }
    }

    std::cout << std::setprecision(12)
              << "p38 phase fit selftest: k=" << full.k
              << " relative_error=" << relative_slope_error
              << " b_circular_error=" << circular_intercept_error
              << " rmse=" << full.rmse
              << " inlier_ratio=" << full.inlier_ratio
              << " sample_count=" << full.sample_count << '\n';
    return 0;
}
