#include "production_calibration_adapter.hpp"

#include <cassert>
#include <cmath>
#include <complex>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

namespace {

const double kPi = 3.1415926535897932384626433832795;

bool closeComplex(const std::complex<float>& actual,
                  const std::complex<float>& expected,
                  float tolerance)
{
    return std::abs(actual - expected) <= tolerance;
}

bool closeDouble(double actual, double expected, double tolerance)
{
    return std::isfinite(actual) && std::abs(actual - expected) <= tolerance;
}

std::vector<std::complex<float> > makeF1(int rows, int cols)
{
    std::vector<std::complex<float> > values(
        static_cast<std::size_t>(rows * cols));
    for (int row = 0; row < rows; ++row) {
        for (int col = 0; col < cols; ++col) {
            values[static_cast<std::size_t>(row * cols + col)] =
                std::complex<float>(
                    1.0f + 0.13f * static_cast<float>(row) +
                        0.07f * static_cast<float>(col),
                    0.2f + 0.05f * static_cast<float>(row) -
                        0.03f * static_cast<float>(col));
        }
    }
    return values;
}

std::vector<std::complex<float> > scaled(
    const std::vector<std::complex<float> >& input,
    const std::complex<float>& scale)
{
    std::vector<std::complex<float> > result = input;
    for (std::complex<float>& value : result) value *= scale;
    return result;
}

void require(bool condition, const char* message)
{
    if (!condition) {
        std::cerr << "[production_calibration_adapter_selftest][FAIL] "
                  << message << '\n';
        std::abort();
    }
}

} // namespace

int main()
{
    using gmti::production_calibration::Method;
    using gmti::production_calibration::Status;
    using gmti::production_calibration::SupportBounds;
    using gmti::production_calibration::applyProductionCalibration;
    using gmti::production_calibration::applyProductionCalibrationReference;

    const int rows = 3;
    const int cols = 7;
    const SupportBounds support(1, 2, 1, 6);
    const std::vector<std::complex<float> > f1 = makeF1(rows, cols);
    const std::complex<float> phase_gain =
        std::polar(0.8f, static_cast<float>(0.37));
    const std::vector<std::complex<float> > f2 = scaled(f1, phase_gain);
    const std::vector<std::complex<float> > f1_before = f1;
    const std::vector<std::complex<float> > f2_before = f2;

    const auto oversized_band = applyProductionCalibration(
        f1, f2, rows, cols, support, Method::kRobustDdcRb,
        2, std::numeric_limits<int>::max(), 0.30);
    require(oversized_band.status == Status::kNotEvaluable &&
                oversized_band.reason == "range_band_bins_exceeds_support",
            "oversized positive range band is rejected before signed arithmetic");

    const int overflow_rows = 1;
    const int overflow_cols = 2;
    const std::vector<std::complex<float> > small_f1(
        static_cast<std::size_t>(overflow_rows * overflow_cols),
        std::complex<float>(1.0e-7f, 0.0f));
    const std::vector<std::complex<float> > large_f2(
        static_cast<std::size_t>(overflow_rows * overflow_cols),
        std::complex<float>(std::numeric_limits<float>::max(), 0.0f));
    const auto float_overflow = applyProductionCalibration(
        small_f1, large_f2, overflow_rows, overflow_cols,
        SupportBounds(0, 0, 0, 1), Method::kScc,
        2, 1, 0.30);
    require(float_overflow.status == Status::kNotEvaluable &&
                float_overflow.reason == "nonfinite_output",
            "finite double Gamma that overflows float output is fail-closed");
    for (const std::complex<float>& value : float_overflow.csi) {
        require(std::isfinite(value.real()) && std::isfinite(value.imag()),
                "fail-closed output remains finite");
    }

    const auto ordinary = applyProductionCalibration(
        f1, f2, rows, cols, support, Method::kOrdinarySubtraction,
        2, 4, 0.30);
    require(ordinary.status == Status::kOk,
            "ordinary subtraction is evaluable");
    require(closeComplex(ordinary.csi[1 * cols + 1],
                         f2[1 * cols + 1] - f1[1 * cols + 1], 1.0e-5f),
            "ordinary method computes F2-F1");
    require(ordinary.csi[0] == f1[0] && ordinary.csi[1 * cols] == f1[1 * cols],
            "ordinary method preserves samples outside support");
    require(!ordinary.truth_used_in_estimator,
            "ordinary metadata is truth blind");

    const auto gamma_one = applyProductionCalibration(
        f1, f1, rows, cols, support, Method::kScc, 2, 4, 0.30);
    require(gamma_one.status == Status::kOk,
            "Gamma=1 SCC fit is evaluable");
    require(gamma_one.gamma_summary.size() == 1U,
            "SCC has one Gamma summary");
    require(closeDouble(gamma_one.gamma_summary[0].gamma.real(), 1.0, 1.0e-6) &&
                closeDouble(gamma_one.gamma_summary[0].gamma.imag(), 0.0, 1.0e-6),
            "Gamma=1 is recovered with the fixed convention");
    require(closeComplex(gamma_one.csi[1 * cols + 2],
                         std::complex<float>(0.0f, 0.0f), 1.0e-5f),
            "Gamma=1 produces the ordinary zero residual");

    const auto phase_fit = applyProductionCalibration(
        f1, f2, rows, cols, support, Method::kScc, 2, 4, 0.30);
    require(phase_fit.status == Status::kOk,
            "positive phase SCC fit is evaluable");
    require(closeDouble(std::abs(phase_fit.gamma_summary[0].gamma), 0.8, 1.0e-5) &&
                closeDouble(std::arg(phase_fit.gamma_summary[0].gamma), 0.37, 1.0e-5),
            "positive phase/sign convention is preserved");

    std::vector<gmti::production_calibration::GammaSummary> persisted_reference =
        phase_fit.gamma_summary;
    persisted_reference[0].phase_coherence = 0.0;
    const auto fixed_reference = applyProductionCalibrationReference(
        f1, f2, rows, cols, support, Method::kScc,
        persisted_reference, 2);
    require(fixed_reference.status == Status::kOk &&
                fixed_reference.reason == "reference_gamma_applied_without_current_input_fit",
            "persisted Gamma is applied without fitting the current input");
    require(closeComplex(fixed_reference.csi[1 * cols + 2],
                         std::complex<float>(0.0f, 0.0f), 2.0e-5f),
            "fixed reference Gamma produces the same residual");
    std::vector<gmti::production_calibration::GammaSummary> bad_reference(
        1U, persisted_reference[0]);
    bad_reference[0].status = Status::kNotEvaluable;
    const auto rejected_reference = applyProductionCalibrationReference(
        f1, f2, rows, cols, support, Method::kScc, bad_reference, 2);
    require(rejected_reference.status == Status::kNotEvaluable &&
                rejected_reference.reason == "reference_group_not_evaluable",
            "invalid reference is NOT_EVALUABLE without Current fallback");

    bad_reference[0].status = Status::kPartial;
    const auto rejected_partial_reference = applyProductionCalibrationReference(
        f1, f2, rows, cols, support, Method::kScc, bad_reference, 2);
    require(rejected_partial_reference.status == Status::kNotEvaluable &&
                rejected_partial_reference.reason == "reference_group_partial_not_evaluable",
            "partial persisted reference is NOT_EVALUABLE without Current fallback");

    bad_reference[0].status = Status::kOk;
    bad_reference[0].support_count = 1;
    const auto rejected_low_support_reference = applyProductionCalibrationReference(
        f1, f2, rows, cols, support, Method::kScc, bad_reference, 2);
    require(rejected_low_support_reference.status == Status::kNotEvaluable &&
                rejected_low_support_reference.reason == "reference_support_below_minimum",
            "low-support persisted reference is NOT_EVALUABLE without Current fallback");

    bad_reference[0].support_count = 2;
    bad_reference[0].phase_coherence = std::numeric_limits<double>::quiet_NaN();
    const auto rejected_missing_metadata_reference = applyProductionCalibrationReference(
        f1, f2, rows, cols, support, Method::kScc, bad_reference, 2);
    require(rejected_missing_metadata_reference.status == Status::kNotEvaluable &&
                rejected_missing_metadata_reference.reason == "reference_phase_coherence_nonfinite",
            "missing persisted reference metadata is NOT_EVALUABLE without Current fallback");

    std::vector<std::complex<float> > f2_ddc = f1;
    for (int row = 0; row < rows; ++row) {
        const std::complex<float> row_gain =
            std::polar(0.7f + 0.1f * static_cast<float>(row),
                       static_cast<float>(0.1 * row));
        for (int col = 0; col < cols; ++col) {
            f2_ddc[static_cast<std::size_t>(row * cols + col)] =
                row_gain * f1[static_cast<std::size_t>(row * cols + col)];
        }
    }
    const auto ddc = applyProductionCalibration(
        f1, f2_ddc, rows, cols, support, Method::kDdc, 2, 4, 0.30);
    require(ddc.status == Status::kOk && ddc.gamma_summary.size() == 2U,
            "DDC fits one Gamma per supported row");
    require(closeComplex(ddc.csi[2 * cols + 6],
                         std::complex<float>(0.0f, 0.0f), 2.0e-5f),
            "DDC applies the row-local residual");

    std::vector<std::complex<float> > f2_robust = f2_ddc;
    f2_robust[1 * cols + 3] *= std::polar(1.0f, static_cast<float>(kPi));
    const auto robust = applyProductionCalibration(
        f1, f2_robust, rows, cols, support, Method::kRobustDdc,
        2, 4, 0.35);
    require(robust.status == Status::kOk && robust.excluded_count >= 1,
            "robust DDC excludes a phase outlier");
    require(robust.gamma_summary[0].phase_coherence > 0.0,
            "robust DDC reports phase coherence");

    std::vector<std::complex<float> > f2_low_coherence = f1;
    const float low_phases[6] = {
        0.0f, static_cast<float>(0.5 * kPi), static_cast<float>(kPi),
        static_cast<float>(-0.5 * kPi), 0.0f,
        static_cast<float>(0.5 * kPi)};
    for (int col = 1; col <= 6; ++col) {
        f2_low_coherence[1 * cols + col] =
            std::polar(1.0f, low_phases[col - 1]) * f1[1 * cols + col];
    }
    const auto low_coherence = applyProductionCalibration(
        f1, f2_low_coherence, rows, cols, support, Method::kRobustDdc,
        2, 4, 0.20);
    require(low_coherence.gamma_summary[0].status == Status::kNotEvaluable &&
                low_coherence.gamma_summary[0].excluded_count > 0,
            "robust DDC rejects a low-coherence local group");

    std::vector<std::complex<float> > f2_rb = f1;
    for (int row = 0; row < rows; ++row) {
        for (int col = 0; col < cols; ++col) {
            const int band = col < 5 ? 0 : 1;
            const std::complex<float> band_gain =
                std::polar(0.9f - 0.1f * static_cast<float>(band),
                           static_cast<float>(0.12 * row + 0.2 * band));
            f2_rb[static_cast<std::size_t>(row * cols + col)] =
                band_gain * f1[static_cast<std::size_t>(row * cols + col)];
        }
    }
    const auto robust_rb = applyProductionCalibration(
        f1, f2_rb, rows, cols, support, Method::kRobustDdcRb,
        2, 4, 0.35);
    require(robust_rb.status == Status::kOk &&
                robust_rb.gamma_summary.size() == 4U,
            "robust DDC-RB supports rows and the final short range band");
    require(robust_rb.gamma_summary.back().range_start == 5 &&
                robust_rb.gamma_summary.back().range_end == 6,
            "DDC-RB closes the final short range band safely");

    const auto zero_support = applyProductionCalibration(
        f1, f2, rows, cols, SupportBounds(0, -1, 0, -1),
        Method::kDdc, 2, 4, 0.30);
    require(zero_support.status == Status::kNotEvaluable,
            "zero support is explicitly NOT_EVALUABLE");
    const auto unsupported = applyProductionCalibration(
        f1, f2, rows, cols, support,
        static_cast<Method>(99), 2, 4, 0.30);
    require(unsupported.status == Status::kNotEvaluable,
            "unsupported method is explicitly NOT_EVALUABLE");

    require(f1 == f1_before && f2 == f2_before,
            "F1/F2 inputs remain read-only");
    require(!robust_rb.truth_used_in_estimator,
            "diagnostic metadata contains truth_used_in_estimator=false");
    require(std::string(gmti::production_calibration::statusName(
                         robust_rb.status)) == "OK",
            "status summary has a stable string representation");

    std::vector<std::complex<float> > f1_nonfinite = f1;
    f1_nonfinite[1] = std::complex<float>(
        std::numeric_limits<float>::quiet_NaN(), 0.0f);
    const auto nonfinite = applyProductionCalibration(
        f1_nonfinite, f2, rows, cols, support, Method::kScc,
        2, 4, 0.30);
    require(nonfinite.status == Status::kNotEvaluable &&
                nonfinite.reason == "nonfinite_input",
            "nonfinite input is fail-closed");

    std::cout << "production_calibration_adapter_selftest passed\n";
    return 0;
}
