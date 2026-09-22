#ifndef RUNTIME_DIAGNOSTICS_HPP
#define RUNTIME_DIAGNOSTICS_HPP

#include <chrono>
#include <string>

struct Config;

namespace gmti {
namespace runtime {

struct RunPaths {
    std::string runtime_config_json;
    std::string runtime_config_txt;
    std::string timing_metrics_csv;
    std::string run_manifest_json;
};

// One compact, truth-blind row emitted by the research-only production
// calibration tap. Keep this record independent of the adapter header so
// existing non-CUDA diagnostic selftests do not acquire a new link dependency.
struct ProductionCalibrationTap {
    std::string method;
    std::string status;
    std::string source;
    int support_count = 0;
    int excluded_count = 0;
    int groups_total = 0;
    int valid_groups = 0;
    double gamma_real = 0.0;
    double gamma_imag = 0.0;
    double gamma_abs = 0.0;
    double phase_coherence = 0.0;
    bool truth_used_in_estimator = false;
    std::string reason;
};

void initializeRun(const Config& cfg,
                   const std::string& config_path,
                   const std::string& executable_path);

void finishRun(const Config& cfg, bool normal_exit, int exit_code,
               const std::string& notes = "");

void writeRuntimeConfigDump(const Config& cfg,
                            const std::string& config_path,
                            const std::string& executable_path);

bool recordProductionCalibrationTap(const Config& cfg,
                                    int beam_id,
                                    const ProductionCalibrationTap& tap);

void recordTiming(const char* scope_name,
                  std::chrono::system_clock::time_point start_time,
                  std::chrono::system_clock::time_point end_time,
                  long long elapsed_ms,
                  int period_id = -1,
                  const std::string& extra = "");

void flushTimingMetrics();

bool diagnosticsEnabled();
const std::string& runId();
const std::string& caseId();
const std::string& resultId();
RunPaths paths();
std::string currentIsoTime();

class TimingScope {
public:
    explicit TimingScope(const char* name, int period_id = -1,
                         const std::string& extra = "");
    ~TimingScope();

private:
    const char* name_;
    int period_id_;
    std::string extra_;
    std::chrono::high_resolution_clock::time_point steady_start_;
    std::chrono::system_clock::time_point wall_start_;
};

} // namespace runtime
} // namespace gmti

#endif // RUNTIME_DIAGNOSTICS_HPP
