#ifndef PRODUCTION_CALIBRATION_ADAPTER_HPP
#define PRODUCTION_CALIBRATION_ADAPTER_HPP

#include <complex>
#include <limits>
#include <string>
#include <vector>

namespace gmti {
namespace production_calibration {

// The production F1/F2 layout is [azimuth/Doppler row][range bin].  Bounds
// are inclusive and describe the only rectangle on which a requested
// research calibration is allowed to estimate or apply a correction.
struct SupportBounds {
    int az_start;
    int az_end;
    int range_start;
    int range_end;

    SupportBounds()
        : az_start(0), az_end(-1), range_start(0), range_end(-1) {}

    SupportBounds(int az_start_value, int az_end_value,
                  int range_start_value, int range_end_value)
        : az_start(az_start_value), az_end(az_end_value),
          range_start(range_start_value), range_end(range_end_value) {}
};

enum class Method {
    kOrdinarySubtraction,
    kScc,
    kDdc,
    kRobustDdc,
    kRobustDdcRb
};

enum class Status {
    kOk,
    kPartial,
    kNotEvaluable
};

// One local Gamma fit.  SCC produces one record, DDC one record per azimuth /
// Doppler row, and DDC-RB one record per row and range band.
struct GammaSummary {
    int az_index;
    int range_start;
    int range_end;
    std::complex<double> gamma;
    double phase_coherence;
    int support_count;
    int excluded_count;
    Status status;
    std::string reason;

    GammaSummary()
        : az_index(-1), range_start(-1), range_end(-1),
          gamma(std::numeric_limits<double>::quiet_NaN(),
                std::numeric_limits<double>::quiet_NaN()),
          phase_coherence(std::numeric_limits<double>::quiet_NaN()),
          support_count(0), excluded_count(0),
          status(Status::kNotEvaluable), reason() {}
};

struct Result {
    std::vector<std::complex<float> > csi;
    Method method;
    Status status;
    int support_count;
    int excluded_count;
    int groups_total;
    int valid_groups;
    bool truth_used_in_estimator;
    std::string reason;
    std::vector<GammaSummary> gamma_summary;

    Result()
        : csi(), method(Method::kOrdinarySubtraction),
          status(Status::kNotEvaluable), support_count(0),
          excluded_count(0), groups_total(0), valid_groups(0),
          truth_used_in_estimator(false), reason(), gamma_summary() {}
};

// Estimate and apply a truth-blind research calibration to final production
// F1/F2.  The function is host-only and has no dependency on Config,
// TrackManager, XML, target truth, or known error labels.
//
// A valid result leaves every sample outside support unchanged as F1.  A
// local group that cannot be estimated also remains F1 and is exposed through
// its status; a globally unavailable method returns kNotEvaluable and never
// silently masquerades as a Current result.
Result applyProductionCalibration(
    const std::vector<std::complex<float> >& f1,
    const std::vector<std::complex<float> >& f2,
    int rows,
    int cols,
    const SupportBounds& support,
    Method method,
    int min_support,
    int range_band_bins,
    double robust_phase_threshold_rad);

// Apply a previously persisted, truth-blind Gamma reference without fitting
// the current input.  The reference summaries carry period/beam/group
// provenance and are rejected when no valid group is available; callers must
// not fall back to the per-input estimator or Production Current.
Result applyProductionCalibrationReference(
    const std::vector<std::complex<float> >& f1,
    const std::vector<std::complex<float> >& f2,
    int rows,
    int cols,
    const SupportBounds& support,
    Method method,
    const std::vector<GammaSummary>& reference,
    int min_support);

const char* methodName(Method method);
const char* statusName(Status status);
bool parseMethod(const std::string& name, Method* method);

} // namespace production_calibration
} // namespace gmti

#endif // PRODUCTION_CALIBRATION_ADAPTER_HPP
