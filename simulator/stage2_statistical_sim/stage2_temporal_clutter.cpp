#include "stage2_temporal_clutter.h"

#include <algorithm>
#include <cmath>

namespace gmti {
namespace stage2 {

namespace {

constexpr double kPi = 3.141592653589793238462643383279502884;

std::uint64_t temporalMix64(std::uint64_t value)
{
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

std::uint64_t temporalCellKey(std::int64_t grid_x, std::int64_t grid_y)
{
    return temporalMix64(
        static_cast<std::uint64_t>(grid_x) ^
        (temporalMix64(static_cast<std::uint64_t>(grid_y)) +
         0x9e3779b97f4a7c15ULL));
}

double centeredVariance(double power_sum,
                        const std::complex<double> &sum,
                        std::uint64_t count)
{
    if (count == 0) return std::numeric_limits<double>::quiet_NaN();
    const double mean_power = power_sum / static_cast<double>(count);
    const double mean_abs_sq = std::norm(sum / static_cast<double>(count));
    return std::max(0.0, mean_power - mean_abs_sq);
}

} // namespace

int temporalCtdrLagPulses(double d_chan_m,
                          double prf_hz,
                          double platform_speed_mps)
{
    if (!(std::abs(d_chan_m) > 0.0) || !(prf_hz > 0.0) ||
        !(platform_speed_mps > 0.0) || !std::isfinite(d_chan_m) ||
        !std::isfinite(prf_hz) || !std::isfinite(platform_speed_mps)) {
        return 1;
    }
    const double equivalent_two_way_spacing_m = 0.5 * std::abs(d_chan_m);
    const double lag = equivalent_two_way_spacing_m * prf_hz /
                       platform_speed_mps;
    if (!std::isfinite(lag)) return 1;
    const double int_max = static_cast<double>(std::numeric_limits<int>::max());
    return std::max(1, lag >= int_max
        ? std::numeric_limits<int>::max()
        : static_cast<int>(std::lround(lag)));
}

void TemporalClutterDiagnostics::configure(double rho, int ctdr_lag, double pri)
{
    configured = true;
    rho_requested = std::max(0.0, std::min(1.0, rho));
    ctdr_lag_pulses = std::max(1, ctdr_lag);
    pri_sec = pri;
}

void TemporalClutterDiagnostics::recordPower(const std::complex<double> &value)
{
    const double power = std::norm(value);
    if (!std::isfinite(power)) return;
    ++sample_count;
    power_sum += power;
    power_square_sum += power * power;
}

void TemporalClutterDiagnostics::recordPair(
    TemporalLagAccumulator &accumulator,
    const std::complex<double> &previous,
    const std::complex<double> &current)
{
    ++accumulator.pair_count;
    accumulator.previous_sum += previous;
    accumulator.current_sum += current;
    accumulator.cross_sum += current * std::conj(previous);
    accumulator.previous_power_sum += std::norm(previous);
    accumulator.current_power_sum += std::norm(current);
}

double TemporalClutterDiagnostics::meanPower() const
{
    return sample_count > 0
        ? power_sum / static_cast<double>(sample_count)
        : std::numeric_limits<double>::quiet_NaN();
}

double TemporalClutterDiagnostics::powerCv() const
{
    const double mean = meanPower();
    if (!(mean > 0.0) || sample_count == 0) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double second = power_square_sum / static_cast<double>(sample_count);
    return std::sqrt(std::max(0.0, second - mean * mean)) / mean;
}

double TemporalClutterDiagnostics::lagCorrelation(
    const TemporalLagAccumulator &accumulator) const
{
    if (accumulator.pair_count == 0) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const double count = static_cast<double>(accumulator.pair_count);
    const std::complex<double> previous_mean = accumulator.previous_sum / count;
    const std::complex<double> current_mean = accumulator.current_sum / count;
    const std::complex<double> covariance = accumulator.cross_sum / count -
        current_mean * std::conj(previous_mean);
    const double previous_var = centeredVariance(
        accumulator.previous_power_sum, accumulator.previous_sum,
        accumulator.pair_count);
    const double current_var = centeredVariance(
        accumulator.current_power_sum, accumulator.current_sum,
        accumulator.pair_count);
    if (!(previous_var > 1.0e-24) || !(current_var > 1.0e-24)) {
        return rho_requested >= 1.0 - 1.0e-12
            ? 1.0 : std::numeric_limits<double>::quiet_NaN();
    }
    return std::abs(covariance) / std::sqrt(previous_var * current_var);
}

double TemporalClutterDiagnostics::correlationTimeMs() const
{
    if (!(rho_requested > 0.0) || !(rho_requested < 1.0) ||
        !(pri_sec > 0.0) || !std::isfinite(pri_sec)) {
        return rho_requested >= 1.0 - 1.0e-12
            ? std::numeric_limits<double>::quiet_NaN() : 0.0;
    }
    return -pri_sec / std::log(rho_requested) * 1000.0;
}

std::uint64_t TemporalClutterProcess::mix64(std::uint64_t value)
{
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

double TemporalClutterProcess::hashUnitCell(std::uint64_t seed,
                                            std::int64_t cell_x,
                                            std::int64_t cell_y,
                                            std::uint64_t salt)
{
    std::uint64_t x = seed;
    x ^= mix64(static_cast<std::uint64_t>(cell_x) + 0x100000001b3ULL * salt);
    x ^= mix64(static_cast<std::uint64_t>(cell_y) + 0x9e3779b97f4a7c15ULL * salt);
    const std::uint64_t y = mix64(x);
    return (static_cast<double>((y >> 11) & ((1ULL << 53) - 1)) + 0.5) *
           (1.0 / static_cast<double>(1ULL << 53));
}

double TemporalClutterProcess::hashGaussianCell(std::uint64_t seed,
                                                 std::int64_t cell_x,
                                                 std::int64_t cell_y,
                                                 std::uint64_t salt0,
                                                 std::uint64_t salt1)
{
    const double u1 = std::max(1.0e-12,
                               hashUnitCell(seed, cell_x, cell_y, salt0));
    const double u2 = hashUnitCell(seed, cell_x, cell_y, salt1);
    return std::sqrt(-2.0 * std::log(u1)) * std::cos(2.0 * kPi * u2);
}

std::complex<double> TemporalClutterProcess::innovation(
    std::int64_t grid_x,
    std::int64_t grid_y,
    std::uint64_t pulse_index) const
{
    const std::uint64_t time_salt = mix64(
        pulse_index + 0x517cc1b727220a95ULL);
    const double real = hashGaussianCell(
        static_cast<std::uint64_t>(random_seed_) ^ time_salt,
        grid_x, grid_y, 0x243f6a8885a308d3ULL, 0x13198a2e03707344ULL);
    const double imag = hashGaussianCell(
        static_cast<std::uint64_t>(random_seed_) ^ mix64(time_salt),
        grid_x, grid_y, 0xa4093822299f31d0ULL, 0x082efa98ec4e6c89ULL);
    return std::complex<double>(real, imag) / std::sqrt(2.0);
}

void TemporalClutterProcess::reset()
{
    states_.clear();
}

void TemporalClutterProcess::configure(
    std::uint32_t random_seed,
    double rho,
    int ctdr_lag_pulses,
    const TemporalClutterDiagnostics *generation)
{
    const double bounded_rho = std::max(0.0, std::min(1.0, rho));
    if (generation_ != generation || random_seed_ != random_seed ||
        std::abs(rho_ - bounded_rho) > 1.0e-15 ||
        ctdr_lag_pulses_ != std::max(1, ctdr_lag_pulses)) {
        reset();
    }
    random_seed_ = random_seed;
    rho_ = bounded_rho;
    innovation_scale_ = std::sqrt(std::max(0.0, 1.0 - rho_ * rho_));
    ctdr_lag_pulses_ = std::max(1, ctdr_lag_pulses);
    generation_ = generation;
}

std::complex<double> TemporalClutterProcess::sample(
    std::int64_t grid_x,
    std::int64_t grid_y,
    std::uint64_t pulse_index,
    double base_abs,
    TemporalClutterDiagnostics &diagnostics)
{
    const std::uint64_t key = temporalCellKey(grid_x, grid_y);
    State &state = states_[key];
    if (!state.initialized || pulse_index < state.last_pulse ||
        std::abs(state.base_abs - base_abs) > 1.0e-12) {
        state = State();
        state.base_abs = base_abs;
        state.value = std::complex<double>(base_abs, 0.0);
        state.history.push_back(state.value);
        state.last_pulse = 0;
        state.initialized = true;
        diagnostics.recordPower(state.value);
    }
    for (std::uint64_t t = state.last_pulse + 1; t <= pulse_index; ++t) {
        const std::complex<double> previous = state.value;
        state.value = rho_ * state.value +
            base_abs * innovation_scale_ * innovation(grid_x, grid_y, t);
        if (state.history.size() >= static_cast<std::size_t>(ctdr_lag_pulses_)) {
            diagnostics.recordPair(
                diagnostics.ctdr_lag,
                state.history[state.history.size() -
                              static_cast<std::size_t>(ctdr_lag_pulses_)],
                state.value);
        }
        diagnostics.recordPair(diagnostics.lag1, previous, state.value);
        diagnostics.recordPower(state.value);
        state.history.push_back(state.value);
        if (state.history.size() > static_cast<std::size_t>(ctdr_lag_pulses_ + 1)) {
            state.history.pop_front();
        }
        state.last_pulse = t;
        if (t == std::numeric_limits<std::uint64_t>::max()) break;
    }
    return state.value;
}

} // namespace stage2
} // namespace gmti
