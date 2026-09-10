#include "stage2_temporal_clutter.h"

#include <cmath>
#include <complex>
#include <cstdlib>
#include <iostream>

namespace {

void require(bool condition, const char *message)
{
    if (!condition) {
        std::cerr << "[stage2_temporal_clutter_selftest][FAIL] "
                  << message << "\n";
        std::exit(1);
    }
}

bool nearlyEqual(double lhs, double rhs, double tolerance)
{
    return std::isfinite(lhs) && std::isfinite(rhs) &&
           std::abs(lhs - rhs) <= tolerance;
}

} // namespace

int main()
{
    constexpr int kCellCount = 256;
    constexpr int kPulseCount = 128;
    constexpr double kBaseAbs = 2.5;
    constexpr double kRho = 0.93;
    constexpr int kCtdrLag = 4;
    constexpr double kPriSec = 1.0 / 1300.0;

    gmti::stage2::TemporalClutterDiagnostics static_diagnostics;
    gmti::stage2::TemporalClutterDiagnostics dynamic_diagnostics;
    static_diagnostics.configure(1.0, kCtdrLag, kPriSec);
    dynamic_diagnostics.configure(kRho, kCtdrLag, kPriSec);

    gmti::stage2::TemporalClutterProcess static_process;
    gmti::stage2::TemporalClutterProcess dynamic_process;
    static_process.configure(20260910U, 1.0, kCtdrLag, &static_diagnostics);
    dynamic_process.configure(20260910U, kRho, kCtdrLag,
                              &dynamic_diagnostics);

    for (int pulse = 0; pulse < kPulseCount; ++pulse) {
        for (int cell = 0; cell < kCellCount; ++cell) {
            const std::complex<double> static_value = static_process.sample(
                cell, cell % 17, static_cast<std::uint64_t>(pulse),
                kBaseAbs, static_diagnostics);
            const std::complex<double> dynamic_value = dynamic_process.sample(
                cell, cell % 17, static_cast<std::uint64_t>(pulse),
                kBaseAbs, dynamic_diagnostics);
            if (pulse == 0) {
                require(static_value == std::complex<double>(kBaseAbs, 0.0),
                        "rho=1 pulse 0 must equal static reflectivity");
                require(dynamic_value == std::complex<double>(kBaseAbs, 0.0),
                        "rho<1 pulse 0 must share the static realization");
            }
            if (pulse > 0 && static_value != std::complex<double>(kBaseAbs, 0.0)) {
                require(false, "rho=1 sequence must remain static");
            }
        }
    }

    require(nearlyEqual(static_diagnostics.meanPower(), kBaseAbs * kBaseAbs,
                        1.0e-12),
            "rho=1 mean power must equal static power");
    require(nearlyEqual(static_diagnostics.powerCv(), 0.0, 1.0e-12),
            "rho=1 power must have zero variation");
    require(nearlyEqual(static_diagnostics.lagCorrelation(
                            static_diagnostics.lag1), 1.0, 1.0e-12),
            "rho=1 lag-1 correlation must be one");
    require(nearlyEqual(static_diagnostics.lagCorrelation(
                            static_diagnostics.ctdr_lag), 1.0, 1.0e-12),
            "rho=1 CTDR-lag correlation must be one");

    const double expected_power = kBaseAbs * kBaseAbs;
    require(std::abs(dynamic_diagnostics.meanPower() - expected_power) /
                expected_power < 0.10,
            "rho<1 power must remain approximately stationary");
    const double lag1 = dynamic_diagnostics.lagCorrelation(
        dynamic_diagnostics.lag1);
    const double ctdr_lag = dynamic_diagnostics.lagCorrelation(
        dynamic_diagnostics.ctdr_lag);
    require(nearlyEqual(lag1, kRho, 0.04),
            "empirical lag-1 correlation must match requested rho");
    require(nearlyEqual(ctdr_lag, std::pow(kRho, kCtdrLag), 0.08),
            "empirical CTDR-lag correlation must match rho^lag");
    const double expected_tau_ms = -kPriSec / std::log(kRho) * 1000.0;
    require(nearlyEqual(dynamic_diagnostics.correlationTimeMs(),
                        expected_tau_ms, 1.0e-12),
            "correlation time must use -PRI/log(rho)");

    require(gmti::stage2::temporalCtdrLagPulses(0.17, 1300.0, 60.0) == 2,
            "CTDR lag must use equivalent two-way spacing");

    std::cout << "[stage2_temporal_clutter_selftest][PASS]"
              << " mean_power=" << dynamic_diagnostics.meanPower()
              << " lag1=" << lag1
              << " ctdr_lag=" << ctdr_lag
              << " tau_ms=" << dynamic_diagnostics.correlationTimeMs()
              << "\n";
    return 0;
}
