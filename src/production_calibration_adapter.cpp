#include "production_calibration_adapter.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <complex>
#include <limits>

namespace gmti {
namespace production_calibration {
namespace {

const double kEpsilon = std::numeric_limits<double>::epsilon();

struct NormalizedBounds {
    int az_start;
    int az_end;
    int range_start;
    int range_end;
};

struct LocalFit {
    std::complex<double> gamma;
    double phase_coherence;
    int support_count;
    int excluded_count;
    Status status;
    std::string reason;

    LocalFit()
        : gamma(std::numeric_limits<double>::quiet_NaN(),
                std::numeric_limits<double>::quiet_NaN()),
          phase_coherence(std::numeric_limits<double>::quiet_NaN()),
          support_count(0), excluded_count(0),
          status(Status::kNotEvaluable), reason() {}
};

bool isKnownMethod(Method method)
{
    return method == Method::kOrdinarySubtraction ||
           method == Method::kScc || method == Method::kDdc ||
           method == Method::kRobustDdc || method == Method::kRobustDdcRb;
}

bool isRobust(Method method)
{
    return method == Method::kRobustDdc || method == Method::kRobustDdcRb;
}

bool isFiniteComplex(const std::complex<float>& value)
{
    return std::isfinite(static_cast<double>(value.real())) &&
           std::isfinite(static_cast<double>(value.imag()));
}

bool finiteInputs(const std::vector<std::complex<float> >& f1,
                  const std::vector<std::complex<float> >& f2)
{
    for (std::size_t index = 0; index < f1.size(); ++index) {
        if (!isFiniteComplex(f1[index]) || !isFiniteComplex(f2[index])) {
            return false;
        }
    }
    return true;
}

bool normalizeBounds(const SupportBounds& requested,
                     int rows,
                     int cols,
                     NormalizedBounds* normalized)
{
    if (normalized == nullptr || rows <= 0 || cols <= 0) return false;

    const int az_start = std::max(0, requested.az_start);
    const int az_end = std::min(rows - 1, requested.az_end);
    const int range_start = std::max(0, requested.range_start);
    const int range_end = std::min(cols - 1, requested.range_end);
    if (az_start > az_end || range_start > range_end) return false;

    normalized->az_start = az_start;
    normalized->az_end = az_end;
    normalized->range_start = range_start;
    normalized->range_end = range_end;
    return true;
}

std::size_t offset(int row, int col, int cols)
{
    return static_cast<std::size_t>(row) * static_cast<std::size_t>(cols) +
           static_cast<std::size_t>(col);
}

std::complex<double> asDouble(const std::complex<float>& value)
{
    return std::complex<double>(static_cast<double>(value.real()),
                                static_cast<double>(value.imag()));
}

bool finiteGamma(const std::complex<double>& gamma)
{
    return std::isfinite(gamma.real()) && std::isfinite(gamma.imag());
}

LocalFit fitPlain(const std::vector<std::complex<float> >& f1,
                  const std::vector<std::complex<float> >& f2,
                  int cols,
                  int row_start,
                  int row_end,
                  int range_start,
                  int range_end,
                  int min_support)
{
    LocalFit result;
    const long long row_count = static_cast<long long>(row_end) -
                                static_cast<long long>(row_start) + 1LL;
    const long long range_count = static_cast<long long>(range_end) -
                                  static_cast<long long>(range_start) + 1LL;
    const long long count64 = row_count * range_count;
    if (count64 <= 0 || count64 > static_cast<long long>(
            std::numeric_limits<int>::max())) {
        result.reason = "invalid_support_size";
        return result;
    }
    const int count = static_cast<int>(count64);
    result.support_count = count;
    if (count < min_support) {
        result.reason = "insufficient_support";
        return result;
    }

    std::complex<double> numerator(0.0, 0.0);
    double denominator = 0.0;
    for (int row = row_start; row <= row_end; ++row) {
        for (int col = range_start; col <= range_end; ++col) {
            const std::complex<double> first = asDouble(f1[offset(row, col, cols)]);
            const std::complex<double> second = asDouble(f2[offset(row, col, cols)]);
            numerator += second * std::conj(first);
            denominator += std::norm(first);
        }
    }
    if (!std::isfinite(denominator) || denominator <= kEpsilon) {
        result.reason = "zero_denominator";
        return result;
    }

    result.gamma = numerator / denominator;
    if (!finiteGamma(result.gamma)) {
        result.gamma = std::complex<double>(
            std::numeric_limits<double>::quiet_NaN(),
            std::numeric_limits<double>::quiet_NaN());
        result.reason = "nonfinite_gamma";
        return result;
    }
    result.status = Status::kOk;
    return result;
}

LocalFit fitRobust(const std::vector<std::complex<float> >& f1,
                   const std::vector<std::complex<float> >& f2,
                   int cols,
                   int row,
                   int range_start,
                   int range_end,
                   int min_support,
                   double phase_threshold_rad)
{
    LocalFit result;
    const int local_count = range_end - range_start + 1;
    std::vector<int> selected;
    selected.reserve(static_cast<std::size_t>(std::max(0, local_count)));
    std::complex<double> phase_sum(0.0, 0.0);
    for (int col = range_start; col <= range_end; ++col) {
        const std::complex<double> first = asDouble(f1[offset(row, col, cols)]);
        const std::complex<double> second = asDouble(f2[offset(row, col, cols)]);
        const std::complex<double> cross = second * std::conj(first);
        const double magnitude = std::abs(cross);
        if (!std::isfinite(magnitude) || magnitude <= kEpsilon) continue;
        selected.push_back(col);
        phase_sum += cross / magnitude;
    }
    result.excluded_count = local_count - static_cast<int>(selected.size());
    if (static_cast<int>(selected.size()) < min_support) {
        result.support_count = static_cast<int>(selected.size());
        result.reason = "insufficient_phase_support";
        return result;
    }

    const double coherence = std::abs(phase_sum / static_cast<double>(selected.size()));
    result.phase_coherence = coherence;
    if (!std::isfinite(coherence) || coherence <= kEpsilon) {
        result.support_count = static_cast<int>(selected.size());
        result.reason = "low_phase_coherence";
        return result;
    }
    const std::complex<double> phase_center =
        (phase_sum / static_cast<double>(selected.size())) / coherence;

    std::vector<int> kept;
    kept.reserve(selected.size());
    for (const int col : selected) {
        const std::complex<double> first = asDouble(f1[offset(row, col, cols)]);
        const std::complex<double> second = asDouble(f2[offset(row, col, cols)]);
        const std::complex<double> cross = second * std::conj(first);
        const std::complex<double> unit_phase = cross / std::abs(cross);
        const double residual_phase = std::arg(unit_phase * std::conj(phase_center));
        if (std::isfinite(residual_phase) &&
            std::abs(residual_phase) <= phase_threshold_rad) {
            kept.push_back(col);
        }
    }
    result.excluded_count = local_count - static_cast<int>(kept.size());
    result.support_count = static_cast<int>(kept.size());
    if (result.support_count < min_support) {
        result.reason = "insufficient_robust_support";
        return result;
    }

    std::complex<double> numerator(0.0, 0.0);
    double denominator = 0.0;
    for (const int col : kept) {
        const std::complex<double> first = asDouble(f1[offset(row, col, cols)]);
        const std::complex<double> second = asDouble(f2[offset(row, col, cols)]);
        numerator += second * std::conj(first);
        denominator += std::norm(first);
    }
    if (!std::isfinite(denominator) || denominator <= kEpsilon) {
        result.reason = "zero_denominator";
        return result;
    }
    result.gamma = numerator / denominator;
    if (!finiteGamma(result.gamma)) {
        result.gamma = std::complex<double>(
            std::numeric_limits<double>::quiet_NaN(),
            std::numeric_limits<double>::quiet_NaN());
        result.reason = "nonfinite_gamma";
        return result;
    }
    result.status = Status::kOk;
    return result;
}

GammaSummary makeSummary(int row,
                         int range_start,
                         int range_end,
                         const LocalFit& fit)
{
    GammaSummary summary;
    summary.az_index = row;
    summary.range_start = range_start;
    summary.range_end = range_end;
    summary.gamma = fit.gamma;
    summary.phase_coherence = fit.phase_coherence;
    summary.support_count = fit.support_count;
    summary.excluded_count = fit.excluded_count;
    summary.status = fit.status;
    summary.reason = fit.reason;
    return summary;
}

void applyResidual(const std::vector<std::complex<float> >& f1,
                   const std::vector<std::complex<float> >& f2,
                   int cols,
                   int row,
                   int range_start,
                   int range_end,
                   const LocalFit& fit,
                   std::vector<std::complex<float> >* output)
{
    if (output == nullptr || fit.status != Status::kOk) return;
    const std::complex<float> gamma(
        static_cast<float>(fit.gamma.real()),
        static_cast<float>(fit.gamma.imag()));
    for (int col = range_start; col <= range_end; ++col) {
        (*output)[offset(row, col, cols)] =
            f2[offset(row, col, cols)] - gamma * f1[offset(row, col, cols)];
    }
}

} // namespace

Result applyProductionCalibration(
    const std::vector<std::complex<float> >& f1,
    const std::vector<std::complex<float> >& f2,
    int rows,
    int cols,
    const SupportBounds& support,
    Method method,
    int min_support,
    int range_band_bins,
    double robust_phase_threshold_rad)
{
    Result result;
    result.method = method;
    result.csi = f1;

    if (!isKnownMethod(method)) {
        result.reason = "unsupported_method";
        return result;
    }
    if (rows <= 0 || cols <= 0 ||
        static_cast<std::size_t>(rows) * static_cast<std::size_t>(cols) != f1.size() ||
        f1.size() != f2.size()) {
        result.reason = "invalid_input_shape";
        result.csi.clear();
        return result;
    }
    if (!finiteInputs(f1, f2)) {
        result.reason = "nonfinite_input";
        result.csi.clear();
        return result;
    }
    if (min_support <= 0) {
        result.reason = "invalid_min_support";
        return result;
    }
    if (method == Method::kRobustDdcRb && range_band_bins <= 0) {
        result.reason = "invalid_range_band_bins";
        return result;
    }
    if (isRobust(method) &&
        (!std::isfinite(robust_phase_threshold_rad) ||
         robust_phase_threshold_rad <= 0.0)) {
        result.reason = "invalid_phase_threshold";
        return result;
    }

    NormalizedBounds bounds;
    if (!normalizeBounds(support, rows, cols, &bounds)) {
        result.reason = "empty_support";
        return result;
    }

    const long long az_count = static_cast<long long>(bounds.az_end) -
                               static_cast<long long>(bounds.az_start) + 1LL;
    const long long range_count = static_cast<long long>(bounds.range_end) -
                                  static_cast<long long>(bounds.range_start) + 1LL;
    const long long support_count = az_count * range_count;
    if (support_count <= 0 ||
        support_count > static_cast<long long>(std::numeric_limits<int>::max())) {
        result.reason = "invalid_support_size";
        return result;
    }
    result.support_count = static_cast<int>(support_count);

    if (method == Method::kOrdinarySubtraction) {
        GammaSummary summary;
        summary.range_start = bounds.range_start;
        summary.range_end = bounds.range_end;
        summary.gamma = std::complex<double>(1.0, 0.0);
        summary.support_count = result.support_count;
        summary.status = result.support_count >= min_support
            ? Status::kOk : Status::kNotEvaluable;
        if (summary.status != Status::kOk) {
            summary.reason = "insufficient_support";
            result.reason = summary.reason;
            result.gamma_summary.push_back(summary);
            return result;
        }
        for (int row = bounds.az_start; row <= bounds.az_end; ++row) {
            for (int col = bounds.range_start; col <= bounds.range_end; ++col) {
                result.csi[offset(row, col, cols)] =
                    f2[offset(row, col, cols)] - f1[offset(row, col, cols)];
            }
        }
        result.status = Status::kOk;
        result.groups_total = 1;
        result.valid_groups = 1;
        result.gamma_summary.push_back(summary);
        return result;
    }

    if (method == Method::kScc) {
        const LocalFit fit = fitPlain(
            f1, f2, cols, bounds.az_start, bounds.az_end,
            bounds.range_start, bounds.range_end, min_support);
        GammaSummary summary = makeSummary(
            -1, bounds.range_start, bounds.range_end, fit);
        summary.support_count = result.support_count;
        result.groups_total = 1;
        result.gamma_summary.push_back(summary);
        if (fit.status != Status::kOk) {
            result.reason = fit.reason;
            return result;
        }
        const std::complex<float> gamma(
            static_cast<float>(fit.gamma.real()),
            static_cast<float>(fit.gamma.imag()));
        for (int row = bounds.az_start; row <= bounds.az_end; ++row) {
            for (int col = bounds.range_start; col <= bounds.range_end; ++col) {
                result.csi[offset(row, col, cols)] =
                    f2[offset(row, col, cols)] -
                    gamma * f1[offset(row, col, cols)];
            }
        }
        result.status = Status::kOk;
        result.valid_groups = 1;
        return result;
    }

    const bool range_banded = method == Method::kRobustDdcRb;
    const bool robust = isRobust(method);
    const int band_size = range_banded ? range_band_bins : range_count;
    for (int row = bounds.az_start; row <= bounds.az_end; ++row) {
        if (range_banded) {
            for (int band_start = bounds.range_start;
                 band_start <= bounds.range_end;
                 band_start += band_size) {
                const int band_end = std::min(
                    bounds.range_end, band_start + band_size - 1);
                const LocalFit fit = robust
                    ? fitRobust(f1, f2, cols, row, band_start, band_end,
                                min_support, robust_phase_threshold_rad)
                    : fitPlain(f1, f2, cols, row, row, band_start, band_end,
                               min_support);
                result.gamma_summary.push_back(
                    makeSummary(row, band_start, band_end, fit));
                ++result.groups_total;
                result.excluded_count += fit.excluded_count;
                if (fit.status == Status::kOk) {
                    ++result.valid_groups;
                    applyResidual(f1, f2, cols, row, band_start, band_end,
                                  fit, &result.csi);
                }
            }
        } else {
            const LocalFit fit = robust
                ? fitRobust(f1, f2, cols, row, bounds.range_start,
                            bounds.range_end, min_support,
                            robust_phase_threshold_rad)
                : fitPlain(f1, f2, cols, row, row, bounds.range_start,
                           bounds.range_end, min_support);
            result.gamma_summary.push_back(
                makeSummary(row, bounds.range_start, bounds.range_end, fit));
            ++result.groups_total;
            result.excluded_count += fit.excluded_count;
            if (fit.status == Status::kOk) {
                ++result.valid_groups;
                applyResidual(f1, f2, cols, row, bounds.range_start,
                              bounds.range_end, fit, &result.csi);
            }
        }
    }

    if (result.valid_groups == result.groups_total && result.groups_total > 0) {
        result.status = Status::kOk;
    } else if (result.valid_groups > 0) {
        result.status = Status::kPartial;
        result.reason = "one_or_more_groups_not_evaluable";
    } else {
        result.status = Status::kNotEvaluable;
        result.reason = "all_groups_not_evaluable";
    }
    return result;
}

const char* methodName(Method method)
{
    switch (method) {
    case Method::kOrdinarySubtraction: return "ordinary_subtraction";
    case Method::kScc: return "scc";
    case Method::kDdc: return "ddc";
    case Method::kRobustDdc: return "robust_ddc";
    case Method::kRobustDdcRb: return "robust_ddc_rb";
    }
    return "unsupported";
}

const char* statusName(Status status)
{
    switch (status) {
    case Status::kOk: return "OK";
    case Status::kPartial: return "PARTIAL";
    case Status::kNotEvaluable: return "NOT_EVALUABLE";
    }
    return "NOT_EVALUABLE";
}

bool parseMethod(const std::string& name, Method* method)
{
    if (method == nullptr) return false;
    if (name == "ordinary_subtraction" ||
        name == "ordinary_complex_subtraction") {
        *method = Method::kOrdinarySubtraction;
        return true;
    }
    if (name == "scc") {
        *method = Method::kScc;
        return true;
    }
    if (name == "ddc") {
        *method = Method::kDdc;
        return true;
    }
    if (name == "robust_ddc") {
        *method = Method::kRobustDdc;
        return true;
    }
    if (name == "robust_ddc_rb") {
        *method = Method::kRobustDdcRb;
        return true;
    }
    return false;
}

} // namespace production_calibration
} // namespace gmti
