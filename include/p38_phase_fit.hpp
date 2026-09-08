#ifndef GMTI_P38_PHASE_FIT_HPP
#define GMTI_P38_PHASE_FIT_HPP

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstddef>
#include <limits>
#include <vector>

namespace gmti {
namespace p38 {

struct PhaseFitOptions {
    double min_energy;
    double min_relative_energy;
    double min_peak_relative_energy;
    double energy_reference_quantile;
    double min_cross_magnitude;
    double min_weight;
    double min_coherence;
    double huber_delta_rad;
    double inlier_threshold_rad;
    double max_rmse_rad;
    double min_inlier_ratio;
    std::size_t min_sample_count;
    int max_iterations;
    double convergence_rad;

    PhaseFitOptions()
        : min_energy(1.0e-12),
          min_relative_energy(1.0e-6),
          min_peak_relative_energy(0.05),
          energy_reference_quantile(0.90),
          min_cross_magnitude(1.0e-15),
          min_weight(1.0e-12),
          min_coherence(0.05),
          huber_delta_rad(0.20),
          inlier_threshold_rad(0.35),
          max_rmse_rad(0.20),
          min_inlier_ratio(0.60),
          min_sample_count(8U),
          max_iterations(16),
          convergence_rad(1.0e-11) {}
};

struct PhaseFitSample {
    double fa;
    double energy;
    double input_weight;
    double coherence;
    double phase;
    double residual;
    double robust_weight;
    bool used;

    PhaseFitSample()
        : fa(std::numeric_limits<double>::quiet_NaN()),
          energy(0.0),
          input_weight(0.0),
          coherence(0.0),
          phase(std::numeric_limits<double>::quiet_NaN()),
          residual(std::numeric_limits<double>::quiet_NaN()),
          robust_weight(0.0),
          used(false) {}
};

struct PhaseFitResult {
    double k;
    double b;
    double rmse;
    double inlier_ratio;
    std::size_t sample_count;
    bool valid;
    std::vector<PhaseFitSample> samples;

    PhaseFitResult()
        : k(std::numeric_limits<double>::quiet_NaN()),
          b(std::numeric_limits<double>::quiet_NaN()),
          rmse(std::numeric_limits<double>::quiet_NaN()),
          inlier_ratio(0.0),
          sample_count(0U),
          valid(false) {}
};

enum class PhaseFitModelSource {
    Observed,
    PeakObserved,
    TheoryGuided,
    TheoryFixedSlope,
    Invalid
};

inline const char* phaseFitModelSourceName(PhaseFitModelSource source) {
    switch (source) {
    case PhaseFitModelSource::Observed:
        return "observed";
    case PhaseFitModelSource::PeakObserved:
        return "peak_observed";
    case PhaseFitModelSource::TheoryGuided:
        return "theory_guided";
    case PhaseFitModelSource::TheoryFixedSlope:
        return "theory_fixed_slope";
    case PhaseFitModelSource::Invalid:
        return "invalid";
    }
    return "invalid";
}

struct TheoryGuidedFitDecision {
    PhaseFitResult selected;
    PhaseFitResult peak_observed;
    PhaseFitResult theory_guided;
    PhaseFitResult theory_fixed_slope;
    PhaseFitModelSource source = PhaseFitModelSource::Invalid;
    double observed_relative_error = std::numeric_limits<double>::quiet_NaN();
    double peak_relative_error = std::numeric_limits<double>::quiet_NaN();
    double guided_relative_error = std::numeric_limits<double>::quiet_NaN();
    bool attempted = false;
};

namespace detail {

// CoreX ivcore may lower one operand of std::min/std::max to float while a
// header is compiled as part of a .cu translation unit.  Fixed-signature
// helpers perform the intended conversion at the call boundary and avoid the
// CUDA wrapper's same-type template deduction requirement.
inline double minDouble(double a, double b) { return b < a ? b : a; }
inline double maxDouble(double a, double b) { return a < b ? b : a; }
inline double clampUnit(double value) {
    return maxDouble(0.0, minDouble(1.0, value));
}

inline double pi() {
    return 3.141592653589793238462643383279502884;
}

inline bool finite(double value) {
    return std::isfinite(value) != 0;
}

inline bool finite(const std::complex<double>& value) {
    return finite(value.real()) && finite(value.imag());
}

inline double wrapToPi(double value) {
    if (!finite(value)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double two_pi = 2.0 * pi();
    value = std::fmod(value + pi(), two_pi);
    if (value < 0.0) {
        value += two_pi;
    }
    return value - pi();
}

inline double median(std::vector<double> values) {
    if (values.empty()) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const std::size_t middle = values.size() / 2U;
    std::nth_element(values.begin(), values.begin() + middle, values.end());
    const double upper = values[middle];
    if ((values.size() & 1U) != 0U) {
        return upper;
    }
    const double lower = *std::max_element(values.begin(), values.begin() + middle);
    return 0.5 * (lower + upper);
}

inline double quantile(std::vector<double> values, double q) {
    if (values.empty() || !finite(q)) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    q = clampUnit(q);
    const std::size_t index = static_cast<std::size_t>(
        std::floor(q * static_cast<double>(values.size() - 1U)));
    std::nth_element(values.begin(), values.begin() + index, values.end());
    return values[index];
}

struct WorkSample {
    std::size_t index;
    double x;
    double raw_phase;
    double unwrapped_phase;
    double base_weight;
};

inline bool fitCenteredLine(const std::vector<WorkSample>& work,
                            const std::vector<double>& y,
                            const std::vector<double>& weight,
                            double& k,
                            double& b) {
    if (work.size() != y.size() || work.size() != weight.size()) {
        return false;
    }

    double sum_weight = 0.0;
    double sum_x = 0.0;
    double sum_y = 0.0;
    for (std::size_t i = 0U; i < work.size(); ++i) {
        const double w = weight[i];
        if (!(w > 0.0) || !finite(w) || !finite(y[i])) {
            continue;
        }
        sum_weight += w;
        sum_x += w * work[i].x;
        sum_y += w * y[i];
    }
    if (!(sum_weight > 0.0) || !finite(sum_weight)) {
        return false;
    }

    const double mean_x = sum_x / sum_weight;
    const double mean_y = sum_y / sum_weight;
    double covariance = 0.0;
    double variance_x = 0.0;
    for (std::size_t i = 0U; i < work.size(); ++i) {
        const double w = weight[i];
        if (!(w > 0.0) || !finite(w) || !finite(y[i])) {
            continue;
        }
        const double centered_x = work[i].x - mean_x;
        covariance += w * centered_x * (y[i] - mean_y);
        variance_x += w * centered_x * centered_x;
    }
    if (!(variance_x > std::numeric_limits<double>::epsilon() * sum_weight) ||
        !finite(variance_x) || !finite(covariance)) {
        return false;
    }

    k = covariance / variance_x;
    b = mean_y - k * mean_x;
    return finite(k) && finite(b);
}

inline double predictionChange(double old_k,
                               double old_b,
                               double new_k,
                               double new_b,
                               double center_x,
                               double span_x) {
    const double delta_k = new_k - old_k;
    const double center_change = delta_k * center_x + (new_b - old_b);
    return std::fabs(center_change) + 0.5 * std::fabs(delta_k) * span_x;
}

}  // namespace detail

// energy is the row normalization term used by coherence = |cross| / energy.
// The returned sample phase is unwrapped only across rows that pass input
// filtering. residual is always the circular residual in [-pi, pi).
inline PhaseFitResult fitPhaseSlope(
    const std::vector<double>& fa,
    const std::vector<std::complex<double> >& cross,
    const std::vector<double>& energy,
    const std::vector<double>& weights = std::vector<double>(),
    const PhaseFitOptions& options = PhaseFitOptions()) {
    PhaseFitResult result;
    result.samples.resize(fa.size());

    if (cross.size() != fa.size() || energy.size() != fa.size() ||
        (!weights.empty() && weights.size() != fa.size())) {
        return result;
    }
    if (!(options.min_energy >= 0.0) ||
        !(options.min_relative_energy >= 0.0) ||
        !(options.min_peak_relative_energy >= 0.0) ||
        !(options.energy_reference_quantile >= 0.0 &&
          options.energy_reference_quantile <= 1.0) ||
        !(options.min_cross_magnitude >= 0.0) ||
        !(options.min_weight >= 0.0) ||
        !(options.min_coherence >= 0.0) ||
        !(options.huber_delta_rad > 0.0) ||
        !(options.inlier_threshold_rad > 0.0) ||
        !(options.max_rmse_rad >= 0.0) ||
        !(options.min_inlier_ratio >= 0.0 && options.min_inlier_ratio <= 1.0) ||
        options.max_iterations <= 0) {
        return result;
    }

    std::vector<double> candidate_energy;
    candidate_energy.reserve(fa.size());
    for (std::size_t i = 0U; i < fa.size(); ++i) {
        PhaseFitSample& sample = result.samples[i];
        sample.fa = fa[i];
        sample.energy = energy[i];
        sample.input_weight = weights.empty() ? 1.0 : weights[i];

        if (!detail::finite(fa[i]) || !detail::finite(cross[i]) ||
            !detail::finite(energy[i]) || !detail::finite(sample.input_weight) ||
            !(energy[i] > options.min_energy) ||
            !(sample.input_weight > options.min_weight)) {
            continue;
        }
        const double magnitude = std::abs(cross[i]);
        if (!detail::finite(magnitude) || !(magnitude > options.min_cross_magnitude)) {
            continue;
        }
        candidate_energy.push_back(energy[i]);
    }

    const double median_energy = detail::median(candidate_energy);
    const double robust_peak_energy = detail::quantile(
        candidate_energy, options.energy_reference_quantile);
    if (!detail::finite(median_energy) || !(median_energy > 0.0)) {
        return result;
    }
    const double energy_floor =
        detail::maxDouble(options.min_energy,
            detail::maxDouble(options.min_relative_energy * median_energy,
                              options.min_peak_relative_energy * robust_peak_energy));

    std::vector<detail::WorkSample> work;
    work.reserve(fa.size());
    for (std::size_t i = 0U; i < fa.size(); ++i) {
        PhaseFitSample& sample = result.samples[i];
        if (!detail::finite(fa[i]) || !detail::finite(cross[i]) ||
            !detail::finite(energy[i]) || !detail::finite(sample.input_weight) ||
            !(energy[i] > energy_floor) ||
            !(sample.input_weight > options.min_weight)) {
            continue;
        }

        const double magnitude = std::abs(cross[i]);
        if (!detail::finite(magnitude) || !(magnitude > options.min_cross_magnitude)) {
            continue;
        }
        sample.coherence = detail::minDouble(1.0, magnitude / energy[i]);
        if (!detail::finite(sample.coherence) ||
            !(sample.coherence >= options.min_coherence)) {
            continue;
        }

        const double relative_energy = detail::minDouble(1.0, energy[i] / median_energy);
        const double energy_weight = std::sqrt(detail::maxDouble(0.0, relative_energy));
        const double base_weight = sample.input_weight * energy_weight *
                                   sample.coherence * sample.coherence;
        if (!detail::finite(base_weight) || !(base_weight > options.min_weight)) {
            continue;
        }

        detail::WorkSample item;
        item.index = i;
        item.x = fa[i];
        item.raw_phase = std::arg(cross[i]);
        item.unwrapped_phase = item.raw_phase;
        item.base_weight = base_weight;
        work.push_back(item);
    }

    std::stable_sort(work.begin(), work.end(),
                     [](const detail::WorkSample& lhs,
                        const detail::WorkSample& rhs) {
                         return lhs.x < rhs.x;
                     });
    result.sample_count = work.size();
    if (work.size() < 2U) {
        return result;
    }

    double previous_raw = work[0].raw_phase;
    double running_phase = previous_raw;
    work[0].unwrapped_phase = running_phase;
    result.samples[work[0].index].phase = running_phase;
    for (std::size_t i = 1U; i < work.size(); ++i) {
        const double raw = work[i].raw_phase;
        running_phase += detail::wrapToPi(raw - previous_raw);
        previous_raw = raw;
        work[i].unwrapped_phase = running_phase;
        result.samples[work[i].index].phase = running_phase;
    }

    const double min_x = work.front().x;
    const double max_x = work.back().x;
    const double span_x = max_x - min_x;
    const double center_x = min_x + 0.5 * span_x;
    if (!(span_x > 0.0) || !detail::finite(span_x)) {
        return result;
    }

    std::vector<double> target(work.size());
    std::vector<double> fit_weight(work.size());
    for (std::size_t i = 0U; i < work.size(); ++i) {
        target[i] = work[i].unwrapped_phase;
        fit_weight[i] = work[i].base_weight;
    }

    double k = 0.0;
    double b = 0.0;
    if (!detail::fitCenteredLine(work, target, fit_weight, k, b)) {
        return result;
    }

    for (int iteration = 0; iteration < options.max_iterations; ++iteration) {
        for (std::size_t i = 0U; i < work.size(); ++i) {
            const double prediction = k * work[i].x + b;
            const double residual =
                detail::wrapToPi(work[i].raw_phase - prediction);
            const double absolute_residual = std::fabs(residual);
            const double huber_weight =
                absolute_residual <= options.huber_delta_rad
                    ? 1.0
                    : options.huber_delta_rad / absolute_residual;
            target[i] = prediction + residual;
            fit_weight[i] = work[i].base_weight * huber_weight;
        }

        double new_k = 0.0;
        double new_b = 0.0;
        if (!detail::fitCenteredLine(work, target, fit_weight, new_k, new_b)) {
            return result;
        }
        const double change = detail::predictionChange(
            k, b, new_k, new_b, center_x, span_x);
        k = new_k;
        b = new_b;
        if (change <= options.convergence_rad) {
            break;
        }
    }

    // Finish with a few circular least-squares passes over the Huber-selected
    // inliers. This removes the small bias that clipped outliers can retain.
    for (int refinement = 0; refinement < 4; ++refinement) {
        std::size_t inlier_count = 0U;
        for (std::size_t i = 0U; i < work.size(); ++i) {
            const double prediction = k * work[i].x + b;
            const double residual =
                detail::wrapToPi(work[i].raw_phase - prediction);
            const bool inlier =
                std::fabs(residual) <= options.inlier_threshold_rad;
            target[i] = prediction + residual;
            fit_weight[i] = inlier ? work[i].base_weight : 0.0;
            inlier_count += inlier ? 1U : 0U;
        }
        if (inlier_count < 2U) {
            break;
        }

        double new_k = 0.0;
        double new_b = 0.0;
        if (!detail::fitCenteredLine(work, target, fit_weight, new_k, new_b)) {
            break;
        }
        const double change = detail::predictionChange(
            k, b, new_k, new_b, center_x, span_x);
        k = new_k;
        b = new_b;
        if (change <= options.convergence_rad) {
            break;
        }
    }

    std::size_t inlier_count = 0U;
    double squared_error = 0.0;
    double rmse_weight = 0.0;
    for (std::size_t i = 0U; i < work.size(); ++i) {
        const double residual =
            detail::wrapToPi(work[i].raw_phase - (k * work[i].x + b));
        const double absolute_residual = std::fabs(residual);
        const double huber_weight =
            absolute_residual <= options.huber_delta_rad
                ? 1.0
                : options.huber_delta_rad / absolute_residual;
        const bool inlier =
            absolute_residual <= options.inlier_threshold_rad;

        PhaseFitSample& sample = result.samples[work[i].index];
        sample.residual = residual;
        sample.robust_weight = huber_weight;
        sample.used = inlier;
        if (inlier) {
            ++inlier_count;
            squared_error += work[i].base_weight * residual * residual;
            rmse_weight += work[i].base_weight;
        }
    }

    result.k = k;
    result.b = b;
    result.inlier_ratio =
        static_cast<double>(inlier_count) / static_cast<double>(work.size());
    if (rmse_weight > 0.0) {
        result.rmse = std::sqrt(squared_error / rmse_weight);
    }
    result.valid = detail::finite(result.k) && detail::finite(result.b) &&
                   detail::finite(result.rmse) &&
                   inlier_count >= options.min_sample_count &&
                   result.inlier_ratio >= options.min_inlier_ratio &&
                   result.rmse <= options.max_rmse_rad;
    return result;
}

// Compatibility fallback for the pre-enhancement CUDA path.  The historical
// implementation unwrapped every finite row phase and solved one ordinary
// least-squares line without energy/coherence rejection.  Enhanced fitting
// must not silently turn a beam into a hard failure when its robust support is
// non-identifiable; callers use this only after MAD/theory candidates fail.
inline PhaseFitResult fitPhaseSlopeLegacy(
    const std::vector<double>& fa,
    const std::vector<std::complex<double> >& cross,
    const std::vector<double>& energy = std::vector<double>()) {
    PhaseFitResult result;
    result.samples.resize(fa.size());
    if (fa.size() != cross.size() ||
        (!energy.empty() && energy.size() != fa.size()) || fa.size() < 2U) {
        return result;
    }

    std::vector<double> phase(fa.size(), 0.0);
    for (std::size_t i = 0U; i < fa.size(); ++i) {
        if (!detail::finite(fa[i]) || !detail::finite(cross[i])) {
            return result;
        }
        phase[i] = std::arg(cross[i]);
        if (!detail::finite(phase[i])) return result;
        result.samples[i].fa = fa[i];
        result.samples[i].energy = energy.empty() ? 0.0 : energy[i];
        result.samples[i].input_weight = 1.0;
    }
    for (std::size_t i = 1U; i < phase.size(); ++i) {
        double delta = phase[i] - phase[i - 1U];
        while (delta > detail::pi()) {
            phase[i] -= 2.0 * detail::pi();
            delta -= 2.0 * detail::pi();
        }
        while (delta < -detail::pi()) {
            phase[i] += 2.0 * detail::pi();
            delta += 2.0 * detail::pi();
        }
    }

    double sum_x = 0.0;
    double sum_y = 0.0;
    double sum_xx = 0.0;
    double sum_xy = 0.0;
    for (std::size_t i = 0U; i < fa.size(); ++i) {
        sum_x += fa[i];
        sum_y += phase[i];
        sum_xx += fa[i] * fa[i];
        sum_xy += fa[i] * phase[i];
        result.samples[i].phase = phase[i];
    }
    const double n = static_cast<double>(fa.size());
    const double determinant = n * sum_xx - sum_x * sum_x;
    if (!detail::finite(determinant) || std::fabs(determinant) <= 1.0e-12) {
        return result;
    }
    result.k = (n * sum_xy - sum_x * sum_y) / determinant;
    result.b = (sum_y * sum_xx - sum_x * sum_xy) / determinant;
    if (!detail::finite(result.k) || !detail::finite(result.b)) return result;

    double squared_error = 0.0;
    for (std::size_t i = 0U; i < fa.size(); ++i) {
        const double residual = detail::wrapToPi(
            std::arg(cross[i]) - (result.k * fa[i] + result.b));
        result.samples[i].residual = residual;
        result.samples[i].robust_weight = 1.0;
        result.samples[i].coherence = 1.0;
        result.samples[i].used = true;
        squared_error += residual * residual;
    }
    result.sample_count = fa.size();
    result.inlier_ratio = 1.0;
    result.rmse = std::sqrt(squared_error / n);
    result.valid = detail::finite(result.rmse);
    return result;
}

// Iterative circular-MAD refinement for the enhanced P38 path.  The existing
// Huber fit remains the bootstrap so the new candidate preserves its energy,
// coherence, phase-unwrapping, and validity gates.  Each MAD pass removes
// only rows whose circular residual is outside a robust median/MAD threshold,
// then re-fits the same weighted line.  A small floor prevents a numerically
// perfect synthetic line from producing a zero-width acceptance band.
inline PhaseFitResult fitPhaseSlopeMad(
    const std::vector<double>& fa,
    const std::vector<std::complex<double> >& cross,
    const std::vector<double>& energy,
    const std::vector<double>& weights = std::vector<double>(),
    const PhaseFitOptions& options = PhaseFitOptions(),
    double mad_multiplier = 3.5,
    int max_mad_iterations = 5,
    double minimum_threshold_rad = 1.0e-3) {
    PhaseFitResult current = fitPhaseSlope(fa, cross, energy, weights, options);
    if (fa.size() != cross.size() || fa.size() != energy.size() ||
        (!weights.empty() && weights.size() != fa.size()) ||
        !(mad_multiplier > 0.0) || max_mad_iterations <= 0 ||
        !(minimum_threshold_rad > 0.0) ||
        !detail::finite(mad_multiplier) ||
        !detail::finite(minimum_threshold_rad)) {
        return current;
    }
    if (!detail::finite(current.k) || !detail::finite(current.b)) {
        return current;
    }

    std::vector<double> working_weights(
        fa.size(), 1.0);
    if (!weights.empty()) {
        working_weights = weights;
    }
    PhaseFitResult best = current;
    bool have_valid = current.valid;

    for (int iteration = 0; iteration < max_mad_iterations; ++iteration) {
        std::vector<double> residuals;
        residuals.reserve(fa.size());
        for (std::size_t i = 0U; i < fa.size(); ++i) {
            if (!(working_weights[i] > options.min_weight) ||
                !detail::finite(fa[i]) || !detail::finite(cross[i]) ||
                !detail::finite(current.samples[i].residual)) {
                continue;
            }
            residuals.push_back(current.samples[i].residual);
        }
        if (residuals.size() < 2U) {
            break;
        }
        const double residual_center = detail::median(residuals);
        std::vector<double> deviations;
        deviations.reserve(residuals.size());
        for (double residual : residuals) {
            deviations.push_back(std::fabs(residual - residual_center));
        }
        const double mad = detail::median(deviations);
        if (!detail::finite(residual_center) || !detail::finite(mad)) {
            break;
        }
        const double robust_sigma = 1.4826 * mad;
        const double threshold = detail::maxDouble(
            minimum_threshold_rad, mad_multiplier * robust_sigma);

        std::vector<double> next_weights = working_weights;
        std::size_t retained = 0U;
        bool changed = false;
        for (std::size_t i = 0U; i < fa.size(); ++i) {
            if (!(working_weights[i] > options.min_weight) ||
                !detail::finite(current.samples[i].residual)) {
                continue;
            }
            const bool keep = std::fabs(current.samples[i].residual -
                                        residual_center) <= threshold;
            if (!keep) {
                next_weights[i] = 0.0;
                changed = true;
            } else {
                ++retained;
            }
        }
        if (!changed || retained < 2U) {
            break;
        }

        const PhaseFitResult candidate = fitPhaseSlope(
            fa, cross, energy, next_weights, options);
        if (!detail::finite(candidate.k) || !detail::finite(candidate.b)) {
            break;
        }
        current = candidate;
        working_weights.swap(next_weights);
        if (current.valid || !have_valid) {
            best = current;
            have_valid = current.valid;
        }
    }

    return have_valid ? best : current;
}

// Physics fallback used only when the observation-only slope fit is not
// identifiable. The slope is fixed to the CTDR forward/theoretical value,
// while the intercept and all quality metrics are still estimated from the
// measured cross-channel phases. This is intentionally different from
// fabricating a complete P38 line from theory: the data must still provide a
// coherent intercept and enough inliers.
inline PhaseFitResult fitPhaseInterceptAtFixedSlope(
    const std::vector<double>& fa,
    const std::vector<std::complex<double> >& cross,
    const std::vector<double>& energy,
    double fixed_k,
    const std::vector<double>& weights = std::vector<double>(),
    const PhaseFitOptions& options = PhaseFitOptions()) {
    PhaseFitResult result;
    result.samples.resize(fa.size());
    result.k = fixed_k;
    if (!detail::finite(fixed_k) ||
        cross.size() != fa.size() || energy.size() != fa.size() ||
        (!weights.empty() && weights.size() != fa.size())) {
        return result;
    }

    std::vector<double> candidate_energy;
    candidate_energy.reserve(fa.size());
    for (std::size_t i = 0U; i < fa.size(); ++i) {
        PhaseFitSample& sample = result.samples[i];
        sample.fa = fa[i];
        sample.energy = energy[i];
        sample.input_weight = weights.empty() ? 1.0 : weights[i];
        if (!detail::finite(fa[i]) || !detail::finite(cross[i]) ||
            !detail::finite(energy[i]) || !detail::finite(sample.input_weight) ||
            !(energy[i] > options.min_energy) ||
            !(sample.input_weight > options.min_weight) ||
            !(std::abs(cross[i]) > options.min_cross_magnitude)) {
            continue;
        }
        candidate_energy.push_back(energy[i]);
    }
    if (candidate_energy.empty()) {
        return result;
    }
    // Compute both order statistics from one owned sorted buffer. Besides
    // avoiding two full copies, this keeps GCC's aggressive inliner from
    // producing a spurious -Wfree-nonheap-object warning around two adjacent
    // pass-by-value nth_element calls.
    std::sort(candidate_energy.begin(), candidate_energy.end());
    const std::size_t median_index = candidate_energy.size() / 2U;
    const double median_energy = (candidate_energy.size() & 1U) != 0U
        ? candidate_energy[median_index]
        : 0.5 * (candidate_energy[median_index - 1U] +
                 candidate_energy[median_index]);
    const double clamped_quantile = detail::clampUnit(
        options.energy_reference_quantile);
    const std::size_t reference_index = static_cast<std::size_t>(std::floor(
        clamped_quantile * static_cast<double>(candidate_energy.size() - 1U)));
    const double reference_energy = candidate_energy[reference_index];
    if (!detail::finite(median_energy) || !(median_energy > 0.0) ||
        !detail::finite(reference_energy) || !(reference_energy > 0.0)) {
        return result;
    }
    const double energy_floor = detail::maxDouble(
        options.min_energy,
        detail::maxDouble(options.min_relative_energy * median_energy,
                          options.min_peak_relative_energy * reference_energy));

    struct FixedWork {
        std::size_t index;
        double x;
        double phase;
        double base_weight;
    };
    std::vector<FixedWork> work;
    work.reserve(fa.size());
    for (std::size_t i = 0U; i < fa.size(); ++i) {
        PhaseFitSample& sample = result.samples[i];
        if (!detail::finite(fa[i]) || !detail::finite(cross[i]) ||
            !detail::finite(energy[i]) || !detail::finite(sample.input_weight) ||
            !(energy[i] > energy_floor) ||
            !(sample.input_weight > options.min_weight)) {
            continue;
        }
        const double magnitude = std::abs(cross[i]);
        if (!(magnitude > options.min_cross_magnitude) ||
            !detail::finite(magnitude)) {
            continue;
        }
        sample.coherence = detail::minDouble(1.0, magnitude / energy[i]);
        if (!detail::finite(sample.coherence) ||
            !(sample.coherence >= options.min_coherence)) {
            continue;
        }
        const double relative_energy = detail::minDouble(1.0, energy[i] / median_energy);
        const double base_weight = sample.input_weight *
            std::sqrt(detail::maxDouble(0.0, relative_energy)) *
            sample.coherence * sample.coherence;
        if (!(base_weight > options.min_weight) ||
            !detail::finite(base_weight)) {
            continue;
        }
        FixedWork item;
        item.index = i;
        item.x = fa[i];
        item.phase = std::arg(cross[i]);
        item.base_weight = base_weight;
        work.push_back(item);
    }
    result.sample_count = work.size();
    if (work.size() < 2U) {
        return result;
    }

    const auto circularIntercept = [&](double current_b,
                                       bool use_huber,
                                       bool use_inliers,
                                       double& next_b) -> bool {
        double re = 0.0;
        double im = 0.0;
        double sum_weight = 0.0;
        for (std::size_t i = 0U; i < work.size(); ++i) {
            const double reduced_phase = work[i].phase - fixed_k * work[i].x;
            const double residual = detail::wrapToPi(reduced_phase - current_b);
            if (use_inliers &&
                std::fabs(residual) > options.inlier_threshold_rad) {
                continue;
            }
            double robust_weight = 1.0;
            if (use_huber && std::fabs(residual) > options.huber_delta_rad) {
                robust_weight = options.huber_delta_rad / std::fabs(residual);
            }
            const double w = work[i].base_weight * robust_weight;
            re += w * std::cos(reduced_phase);
            im += w * std::sin(reduced_phase);
            sum_weight += w;
        }
        if (!(sum_weight > 0.0) ||
            !(std::hypot(re, im) > options.min_weight)) {
            return false;
        }
        next_b = std::atan2(im, re);
        return detail::finite(next_b);
    };

    double re0 = 0.0;
    double im0 = 0.0;
    for (std::size_t i = 0U; i < work.size(); ++i) {
        const double reduced_phase = work[i].phase - fixed_k * work[i].x;
        re0 += work[i].base_weight * std::cos(reduced_phase);
        im0 += work[i].base_weight * std::sin(reduced_phase);
    }
    if (!(std::hypot(re0, im0) > options.min_weight)) {
        return result;
    }
    double b = std::atan2(im0, re0);
    for (int iteration = 0; iteration < options.max_iterations; ++iteration) {
        double next_b = b;
        if (!circularIntercept(b, true, false, next_b)) {
            return result;
        }
        const double change = std::fabs(detail::wrapToPi(next_b - b));
        b = next_b;
        if (change <= options.convergence_rad) break;
    }
    for (int refinement = 0; refinement < 4; ++refinement) {
        double next_b = b;
        if (!circularIntercept(b, false, true, next_b)) break;
        const double change = std::fabs(detail::wrapToPi(next_b - b));
        b = next_b;
        if (change <= options.convergence_rad) break;
    }

    std::size_t inlier_count = 0U;
    double squared_error = 0.0;
    double rmse_weight = 0.0;
    for (std::size_t i = 0U; i < work.size(); ++i) {
        const double prediction = fixed_k * work[i].x + b;
        const double residual = detail::wrapToPi(work[i].phase - prediction);
        const bool inlier =
            std::fabs(residual) <= options.inlier_threshold_rad;
        PhaseFitSample& sample = result.samples[work[i].index];
        sample.phase = prediction + residual;
        sample.residual = residual;
        sample.robust_weight = inlier ? 1.0 : 0.0;
        sample.used = inlier;
        if (inlier) {
            ++inlier_count;
            squared_error += work[i].base_weight * residual * residual;
            rmse_weight += work[i].base_weight;
        }
    }
    result.b = b;
    result.inlier_ratio = static_cast<double>(inlier_count) /
                          static_cast<double>(work.size());
    result.rmse = rmse_weight > 0.0
        ? std::sqrt(squared_error / rmse_weight)
        : std::numeric_limits<double>::quiet_NaN();
    result.valid = detail::finite(result.b) && detail::finite(result.rmse) &&
                   inlier_count >= options.min_sample_count &&
                   result.inlier_ratio >= options.min_inlier_ratio &&
                   result.rmse <= options.max_rmse_rad;
    return result;
}

// Circular line search near an analytic slope prior. This is a fallback for
// sparse/noisy supports where unwrap-then-fit can choose different 2*pi
// branches after tiny floating-point changes. The prior only bounds the slope
// search; the intercept, selected slope, residuals and validity all come from
// the observed complex cross phase.
inline PhaseFitResult fitPhaseSlopeNearPrior(
    const std::vector<double>& fa,
    const std::vector<std::complex<double> >& cross,
    const std::vector<double>& energy,
    double expected_k,
    double relative_span,
    const std::vector<double>& weights = std::vector<double>(),
    const PhaseFitOptions& options = PhaseFitOptions()) {
    PhaseFitResult result = fitPhaseSlope(fa, cross, energy, weights, options);
    if (!detail::finite(expected_k) || !(relative_span > 0.0) ||
        cross.size() != fa.size() || energy.size() != fa.size()) {
        result.valid = false;
        return result;
    }
    if (!weights.empty() && weights.size() != fa.size()) {
        result.valid = false;
        return result;
    }

    struct PriorWork {
        std::size_t index;
        double x;
        double phase;
        double weight;
    };
    std::vector<double> prior_candidate_energy;
    prior_candidate_energy.reserve(fa.size());
    for (std::size_t i = 0U; i < fa.size(); ++i) {
        const double input_weight = weights.empty() ? 1.0 : weights[i];
        if (!detail::finite(fa[i]) || !detail::finite(cross[i]) ||
            !detail::finite(energy[i]) || !detail::finite(input_weight) ||
            !(energy[i] > options.min_energy) ||
            !(input_weight > options.min_weight) ||
            !(std::abs(cross[i]) > options.min_cross_magnitude)) {
            continue;
        }
        prior_candidate_energy.push_back(energy[i]);
    }
    const double prior_robust_peak =
        detail::quantile(prior_candidate_energy, 0.90);
    if (!detail::finite(prior_robust_peak) || !(prior_robust_peak > 0.0)) {
        result.valid = false;
        return result;
    }
    std::vector<PriorWork> work;
    work.reserve(fa.size());
    for (std::size_t i = 0U; i < fa.size(); ++i) {
        if (i >= result.samples.size() ||
            !detail::finite(result.samples[i].phase) ||
            !detail::finite(fa[i]) || !detail::finite(cross[i]) ||
            !(result.samples[i].coherence >= options.min_coherence)) {
            continue;
        }
        const double input_weight = weights.empty() ? 1.0 : weights[i];
        const double bounded_coherence = detail::clampUnit(
            result.samples[i].coherence);
        // Rows just above the inclusion floor can bend an otherwise clean
        // phase ridge.  Weight the prior-bounded circular search by observed
        // energy relative to the robust 90th-percentile peak.  Squaring this
        // capped ratio suppresses low-energy wings while a single exceptional
        // bin cannot dominate (it is capped at one).
        const double relative_energy = detail::clampUnit(
            energy[i] / prior_robust_peak);
        const double w = input_weight * relative_energy * relative_energy *
                         bounded_coherence * bounded_coherence;
        if (!(w > options.min_weight) || !detail::finite(w)) continue;
        PriorWork item;
        item.index = i;
        item.x = fa[i];
        item.phase = std::arg(cross[i]);
        item.weight = w;
        work.push_back(item);
    }
    result.sample_count = work.size();
    if (work.size() < 2U) {
        result.valid = false;
        return result;
    }

    std::vector<char> active(work.size(), 1);
    const auto search = [&work](double center,
                                double half_span,
                                const std::vector<char>& mask,
                                double& best_k,
                                double& best_b) -> bool {
        const int steps = 400;
        double best_score = -1.0;
        bool found = false;
        for (int s = 0; s <= steps; ++s) {
            const double k = center - half_span +
                (2.0 * half_span * static_cast<double>(s) /
                 static_cast<double>(steps));
            double re = 0.0;
            double im = 0.0;
            double sum_w = 0.0;
            for (std::size_t i = 0U; i < work.size(); ++i) {
                if (!mask[i]) continue;
                const double residual_phase = work[i].phase - k * work[i].x;
                re += work[i].weight * std::cos(residual_phase);
                im += work[i].weight * std::sin(residual_phase);
                sum_w += work[i].weight;
            }
            if (!(sum_w > 0.0)) continue;
            const double score = std::sqrt(re * re + im * im) / sum_w;
            if (!found || score > best_score + 1.0e-15 ||
                (std::fabs(score - best_score) <= 1.0e-15 &&
                 std::fabs(k - center) < std::fabs(best_k - center))) {
                best_score = score;
                best_k = k;
                best_b = std::atan2(im, re);
                found = true;
            }
        }
        return found && detail::finite(best_k) && detail::finite(best_b);
    };

    const double initial_span = detail::maxDouble(
        1.0e-8, std::fabs(expected_k) * relative_span);
    double k = expected_k;
    double b = 0.0;
    if (!search(expected_k, initial_span, active, k, b)) {
        result.valid = false;
        return result;
    }

    // Reject circular outliers, then refine twice on progressively finer slope
    // grids. At least two observations are retained even when the configured
    // final validity threshold is stricter.
    for (int refinement = 0; refinement < 2; ++refinement) {
        std::size_t inliers = 0U;
        for (std::size_t i = 0U; i < work.size(); ++i) {
            const double residual = detail::wrapToPi(
                work[i].phase - (k * work[i].x + b));
            active[i] = std::fabs(residual) <= options.inlier_threshold_rad ? 1 : 0;
            inliers += active[i] ? 1U : 0U;
        }
        if (inliers < 2U) {
            std::fill(active.begin(), active.end(), 1);
            break;
        }
        const double refine_span = initial_span /
            (refinement == 0 ? 20.0 : 200.0);
        double refined_k = k;
        double refined_b = b;
        if (search(k, refine_span, active, refined_k, refined_b)) {
            k = refined_k;
            b = refined_b;
        }
    }

    std::size_t inlier_count = 0U;
    double squared_error = 0.0;
    double rmse_weight = 0.0;
    for (std::size_t i = 0U; i < result.samples.size(); ++i) {
        result.samples[i].used = false;
    }
    for (std::size_t i = 0U; i < work.size(); ++i) {
        const double prediction = k * work[i].x + b;
        const double residual = detail::wrapToPi(work[i].phase - prediction);
        const bool inlier = std::fabs(residual) <= options.inlier_threshold_rad;
        PhaseFitSample& sample = result.samples[work[i].index];
        sample.phase = prediction + residual;
        sample.residual = residual;
        sample.robust_weight = inlier ? 1.0 : 0.0;
        sample.used = inlier;
        if (inlier) {
            ++inlier_count;
            squared_error += work[i].weight * residual * residual;
            rmse_weight += work[i].weight;
        }
    }

    result.k = k;
    result.b = b;
    result.inlier_ratio = static_cast<double>(inlier_count) /
                          static_cast<double>(work.size());
    result.rmse = rmse_weight > 0.0
        ? std::sqrt(squared_error / rmse_weight)
        : std::numeric_limits<double>::quiet_NaN();
    result.valid = detail::finite(result.k) && detail::finite(result.b) &&
                   detail::finite(result.rmse) &&
                   inlier_count >= options.min_sample_count &&
                   result.inlier_ratio >= options.min_inlier_ratio &&
                   result.rmse <= options.max_rmse_rad;
    return result;
}

inline double relativeSlopeError(double observed_k, double expected_k) {
    if (!detail::finite(observed_k) || !detail::finite(expected_k) ||
        !(std::fabs(expected_k) > 1.0e-12)) {
        return std::numeric_limits<double>::infinity();
    }
    return std::fabs((observed_k - expected_k) / expected_k);
}

// Common CPU/CUDA host-side decision policy for the raw CTDR static phase
// model.  The unconstrained observation fit is always attempted first.  If
// it is invalid or physically inconsistent, retry on the high-energy ridge,
// then use a theory-bounded circular fit.  If the observed slope remains
// non-identifiable, fix only the slope to the CTDR forward/theoretical value
// while estimating the intercept and validity entirely from measured phase.
//
// This function must not be used for the near-zero residual CSI fit after
// integer PRT alignment; callers disable it there by providing no finite
// expected slope.
inline TheoryGuidedFitDecision selectPhaseFitWithTheoryFallback(
    const std::vector<double>& fa,
    const std::vector<std::complex<double> >& cross,
    const std::vector<double>& energy,
    const PhaseFitResult& observed,
    bool enable_theory_fallback,
    double expected_k,
    double prior_relative_span,
    double trigger_relative_error,
    const std::vector<double>& weights = std::vector<double>(),
    const PhaseFitOptions& options = PhaseFitOptions()) {
    TheoryGuidedFitDecision decision;
    decision.selected = observed;
    decision.source = observed.valid
        ? PhaseFitModelSource::Observed
        : PhaseFitModelSource::Invalid;
    decision.observed_relative_error = relativeSlopeError(observed.k, expected_k);

    const bool usable_theory = enable_theory_fallback &&
        detail::finite(expected_k) && std::fabs(expected_k) > 1.0e-12 &&
        detail::finite(prior_relative_span) && prior_relative_span > 0.0 &&
        detail::finite(trigger_relative_error) && trigger_relative_error >= 0.0;
    if (!usable_theory ||
        (observed.valid &&
         decision.observed_relative_error <= trigger_relative_error)) {
        return decision;
    }
    decision.attempted = true;

    PhaseFitOptions peak_options = options;
    peak_options.energy_reference_quantile = 1.0;
    decision.peak_observed = fitPhaseSlope(
        fa, cross, energy, weights, peak_options);
    decision.peak_relative_error = relativeSlopeError(
        decision.peak_observed.k, expected_k);
    if (decision.peak_observed.valid &&
        decision.peak_relative_error <= trigger_relative_error) {
        decision.selected = decision.peak_observed;
        decision.source = PhaseFitModelSource::PeakObserved;
        return decision;
    }

    decision.theory_guided = fitPhaseSlopeNearPrior(
        fa, cross, energy, expected_k, prior_relative_span, weights, options);
    decision.guided_relative_error = relativeSlopeError(
        decision.theory_guided.k, expected_k);

    // Estimate the theory-slope intercept from the ordinary robust support,
    // not only from rows above a fraction of the absolute peak.  With two or
    // more strong moving targets the absolute-peak rows can belong to
    // different motion ridges and cancel circularly, even though the wider
    // static-clutter support still provides a coherent CTDR intercept.
    PhaseFitOptions fixed_options = options;
    // Several strong movers can leave the broad static-clutter support as a
    // minority. Keep the absolute sample/RMSE gates, but allow the physics
    // fallback to use a coherent 30% subset. The observation-only fit is not
    // relaxed by this rule.
    fixed_options.min_inlier_ratio = detail::minDouble(
        fixed_options.min_inlier_ratio, 0.30);
    decision.theory_fixed_slope = fitPhaseInterceptAtFixedSlope(
        fa, cross, energy, expected_k, weights, fixed_options);
    if (decision.theory_fixed_slope.valid) {
        decision.selected = decision.theory_fixed_slope;
        decision.source = PhaseFitModelSource::TheoryFixedSlope;
        return decision;
    }

    if (decision.theory_guided.valid &&
        decision.guided_relative_error <= trigger_relative_error) {
        decision.selected = decision.theory_guided;
        decision.source = PhaseFitModelSource::TheoryGuided;
        return decision;
    }

    // Preserve candidate diagnostics but make the selected result explicitly
    // invalid.  Keeping a numerically valid yet grossly inconsistent observed
    // slope would silently manufacture a wrong static phase model.
    decision.selected.valid = false;
    decision.source = PhaseFitModelSource::Invalid;
    return decision;
}

}  // namespace p38
}  // namespace gmti

#endif  // GMTI_P38_PHASE_FIT_HPP
