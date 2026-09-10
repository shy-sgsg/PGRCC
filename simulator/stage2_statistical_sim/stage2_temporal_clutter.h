#pragma once

#include <complex>
#include <cstdint>
#include <deque>
#include <limits>
#include <unordered_map>

namespace gmti {
namespace stage2 {

int temporalCtdrLagPulses(double d_chan_m,
                          double prf_hz,
                          double platform_speed_mps);

struct TemporalLagAccumulator {
    std::uint64_t pair_count = 0;
    std::complex<double> previous_sum{0.0, 0.0};
    std::complex<double> current_sum{0.0, 0.0};
    std::complex<double> cross_sum{0.0, 0.0};
    double previous_power_sum = 0.0;
    double current_power_sum = 0.0;
};

struct TemporalClutterDiagnostics {
    bool configured = false;
    double rho_requested = 1.0;
    int ctdr_lag_pulses = 1;
    double pri_sec = std::numeric_limits<double>::quiet_NaN();
    std::uint64_t sample_count = 0;
    double power_sum = 0.0;
    double power_square_sum = 0.0;
    TemporalLagAccumulator lag1;
    TemporalLagAccumulator ctdr_lag;

    void configure(double rho, int ctdr_lag, double pri);
    void recordPower(const std::complex<double> &value);
    void recordPair(TemporalLagAccumulator &accumulator,
                    const std::complex<double> &previous,
                    const std::complex<double> &current);

    double meanPower() const;
    double powerCv() const;
    double lagCorrelation(const TemporalLagAccumulator &accumulator) const;
    double correlationTimeMs() const;
};

// Stateful, deterministic complex AR(1) process keyed by physical surface
// cell.  The initial state is exactly the static surface reflectivity; only
// p>0 applies the innovation.  This makes rho=1 byte-identical to the static
// model and isolates temporal decorrelation from initial-realization changes.
class TemporalClutterProcess {
public:
    void configure(std::uint32_t random_seed,
                   double rho,
                   int ctdr_lag_pulses,
                   const TemporalClutterDiagnostics *generation);

    std::complex<double> sample(std::int64_t grid_x,
                                std::int64_t grid_y,
                                std::uint64_t pulse_index,
                                double base_abs,
                                TemporalClutterDiagnostics &diagnostics);

private:
    struct State {
        std::complex<double> value{0.0, 0.0};
        std::deque<std::complex<double>> history;
        std::uint64_t last_pulse = 0;
        double base_abs = 0.0;
        bool initialized = false;
    };

    static std::uint64_t mix64(std::uint64_t value);
    static double hashUnitCell(std::uint64_t seed,
                               std::int64_t cell_x,
                               std::int64_t cell_y,
                               std::uint64_t salt);
    static double hashGaussianCell(std::uint64_t seed,
                                   std::int64_t cell_x,
                                   std::int64_t cell_y,
                                   std::uint64_t salt0,
                                   std::uint64_t salt1);
    std::complex<double> innovation(std::int64_t grid_x,
                                    std::int64_t grid_y,
                                    std::uint64_t pulse_index) const;
    void reset();

    std::uint32_t random_seed_ = 0;
    double rho_ = 1.0;
    double innovation_scale_ = 0.0;
    int ctdr_lag_pulses_ = 1;
    const TemporalClutterDiagnostics *generation_ = nullptr;
    std::unordered_map<std::uint64_t, State> states_;
};

} // namespace stage2
} // namespace gmti
