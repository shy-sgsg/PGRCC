#include "stage2_lfm_forward.h"

#include "../common/SimulationGeometry.h"
#include "../target_injection/beam_visibility.h"
#include "../target_injection/channel_phase_model.h"
#include "../target_injection/radar_geometry.h"
#include "dbs/NewProtocolLayout.hpp"

#include <fftw3.h>

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstring>
#include <limits>
#include <sstream>

namespace gmti {
namespace stage2 {

using gmti::target_injection::GeometrySample;
using gmti::target_injection::PlatformState;
using gmti::target_injection::TargetGlobalConfig;
using gmti::target_injection::TargetState;
using gmti::target_injection::Vec3;
using gmti::target_injection::deg2rad;
using gmti::target_injection::dot;
using gmti::target_injection::evaluatePlatformState;
using gmti::target_injection::evaluateVisibility;
using gmti::target_injection::kC;
using gmti::target_injection::kPi;
using gmti::target_injection::norm;
using gmti::target_injection::pulseTimeSec;
using gmti::target_injection::rad2deg;
using gmti::target_injection::wrapTo180;

namespace {

const size_t kHeaderBytes = gmti::new_protocol::kHeaderBytes;

std::complex<float> loadCh(const std::vector<uint8_t> &packet,
                           int n,
                           int ch,
                           std::size_t channel_count,
                           const std::string &iq_type)
{
    const std::size_t iq_bytes = gmti::new_protocol::bytesPerIq(iq_type);
    const size_t off = kHeaderBytes +
                       static_cast<size_t>(n) * gmti::new_protocol::sampleBytes(channel_count, iq_type) +
                       gmti::new_protocol::channelOffset(static_cast<size_t>(ch), iq_type);
    return std::complex<float>(
        gmti::new_protocol::loadIqAsFloat(&packet[off], iq_type),
        gmti::new_protocol::loadIqAsFloat(&packet[off + iq_bytes], iq_type));
}

// The continuous-surface generator stores delayed reflectivity before it is
// convolved with the transmitted LFM.  A nearest-bin write makes a fixed
// ground cell jump by one range sample as the platform moves through an
// aperture.  That artificial jump is coherent across slow time and becomes
// false moving energy after CSI.  Keep the actual geometric delay by splitting
// the impulse between its two neighbouring sampled delays.  The weights sum
// to one, so a zero-frequency (range-compressed) response keeps its gain while
// avoiding the phase discontinuity of rounding.
void addFractionalDelayImpulse(std::vector<std::complex<float>> &surface,
                               double sample_delay,
                               const std::complex<float> &value)
{
    if (!std::isfinite(sample_delay) || surface.empty()) return;
    const int left = static_cast<int>(std::floor(sample_delay));
    const double fraction = sample_delay - static_cast<double>(left);
    if (left >= 0 && left < static_cast<int>(surface.size())) {
        surface[static_cast<std::size_t>(left)] +=
            value * static_cast<float>(1.0 - fraction);
    }
    const int right = left + 1;
    if (right >= 0 && right < static_cast<int>(surface.size())) {
        surface[static_cast<std::size_t>(right)] +=
            value * static_cast<float>(fraction);
    }
}

void storeCh(std::vector<uint8_t> &packet,
             int n,
             int ch,
             std::size_t channel_count,
             const std::string &iq_type,
             const std::complex<float> &v)
{
    const std::size_t iq_bytes = gmti::new_protocol::bytesPerIq(iq_type);
    const size_t off = kHeaderBytes +
                       static_cast<size_t>(n) * gmti::new_protocol::sampleBytes(channel_count, iq_type) +
                       gmti::new_protocol::channelOffset(static_cast<size_t>(ch), iq_type);
    gmti::new_protocol::storeIqFromFloat(&packet[off], iq_type, v.real());
    gmti::new_protocol::storeIqFromFloat(&packet[off + iq_bytes], iq_type, v.imag());
}

GeometrySample fixedPointGeometry(const gmti::target_injection::RadarConfig &radar,
                                  const TargetGlobalConfig &global,
                                  const Vec3 &target_position,
                                  int period_id,
                                  int beam_id,
                                  int pulse_id,
                                  double time_override_sec = std::numeric_limits<double>::quiet_NaN(),
                                  double servo_azimuth_override_deg = std::numeric_limits<double>::quiet_NaN())
{
    GeometrySample g;
    g.time_sec = std::isfinite(time_override_sec)
        ? time_override_sec : pulseTimeSec(radar, period_id, beam_id, pulse_id);
    g.theta_cmd_deg = std::isfinite(servo_azimuth_override_deg)
        ? servo_azimuth_override_deg
        : radar.scan_min_deg + radar.scan_step_deg * static_cast<double>(beam_id);
    g.theta_true_deg = g.theta_cmd_deg;
    g.platform = evaluatePlatformState(global, g.time_sec);
    g.target.position = target_position;
    g.target.velocity = Vec3(0.0, 0.0, 0.0);
    g.los = g.target.position - g.platform.position;
    g.range_m = norm(g.los);
    if (g.range_m > 0.0) g.los_unit = g.los * (1.0 / g.range_m);
    g.tau_abs_sec = 2.0 * g.range_m / kC;
    g.tau_rel_sec = g.tau_abs_sec - radar.sample_delay_sec;
    g.range_sample_float = g.tau_rel_sec * radar.fs_hz;
    g.range_sample_int = static_cast<int>(std::floor(g.range_sample_float + 0.5));
    g.in_range_window = (g.range_sample_float >= 0.0 &&
                         g.range_sample_float < static_cast<double>(radar.pulse_len));
    const gmti::sim_geometry::ENUPoint platform_enu =
        gmti::sim_geometry::localToEnu(
            gmti::sim_geometry::LocalPoint(g.platform.position.x, g.platform.position.y, g.platform.position.z),
            global.geometry);
    const gmti::sim_geometry::ENUPoint target_enu =
        gmti::sim_geometry::localToEnu(
            gmti::sim_geometry::LocalPoint(g.target.position.x, g.target.position.y, g.target.position.z),
            global.geometry);
    const gmti::sim_geometry::ENUVelocity platform_vel =
        gmti::sim_geometry::localVelocityToEnu(
            gmti::sim_geometry::LocalVelocity(g.platform.velocity.x, g.platform.velocity.y, g.platform.velocity.z),
            global.geometry);
    const double de = target_enu.e - platform_enu.e;
    const double dn = target_enu.n - platform_enu.n;
    const double horizontal = std::sqrt(de * de + dn * dn);
    const gmti::sim_geometry::LookVectorEN look =
        gmti::sim_geometry::makeAlgorithmLookVectorEN(platform_vel.ve, platform_vel.vn, g.theta_true_deg, global.geometry);
    if (horizontal > 1.0e-9) {
        const double ue = de / horizontal;
        const double un = dn / horizontal;
        const double dot_en = std::max(-1.0, std::min(1.0, look.east * ue + look.north * un));
        const double cross_en = look.east * un - look.north * ue;
        g.angle_error_deg = rad2deg(std::atan2(cross_en, dot_en));
    } else {
        g.angle_error_deg = 0.0;
    }
    g.target_azimuth_deg = wrapTo180(g.theta_true_deg + g.angle_error_deg);
    const Vec3 rel_v = g.target.velocity - g.platform.velocity;
    g.radial_velocity_mps = dot(rel_v, g.los_unit);
    return g;
}

GeometrySample scattererGeometry(const gmti::target_injection::RadarConfig &radar,
                                 const TargetGlobalConfig &global,
                                 const Scatterer &s,
                                 int period_id,
                                 int beam_id,
                                 int pulse_id,
                                 double time_override_sec,
                                 double servo_azimuth_override_deg)
{
    return fixedPointGeometry(radar, global, s.position,
                              period_id, beam_id, pulse_id,
                              time_override_sec, servo_azimuth_override_deg);
}

Vec3 makeFixedGroundPoint(const TargetGlobalConfig &global,
                          const PlatformState &reference_platform,
                          double reference_slant_range_m,
                          double theta_deg,
                          double ground_z_m)
{
    const gmti::sim_geometry::LocalPoint point =
        gmti::sim_geometry::makePointFromRangeAzimuth(
            gmti::sim_geometry::LocalPoint(reference_platform.position.x,
                                           reference_platform.position.y,
                                           reference_platform.position.z),
            gmti::sim_geometry::LocalVelocity(reference_platform.velocity.x,
                                              reference_platform.velocity.y,
                                              reference_platform.velocity.z),
            reference_slant_range_m,
            theta_deg,
            reference_platform.position.z,
            ground_z_m,
            global.geometry);
    return Vec3(point.x, point.y, point.z);
}

uint64_t mix64(uint64_t x)
{
    x += 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    return x ^ (x >> 31);
}

double hashUnitCell(uint64_t seed,
                    int64_t cell_x,
                    int64_t cell_y,
                    uint64_t salt)
{
    uint64_t x = seed;
    x ^= mix64(static_cast<uint64_t>(cell_x) + 0x100000001b3ULL * salt);
    x ^= mix64(static_cast<uint64_t>(cell_y) + 0x9e3779b97f4a7c15ULL * salt);
    const uint64_t y = mix64(x);
    return (static_cast<double>((y >> 11) & ((1ULL << 53) - 1)) + 0.5) *
           (1.0 / static_cast<double>(1ULL << 53));
}

double hashGaussianCell(uint64_t seed,
                        int64_t cell_x,
                        int64_t cell_y,
                        uint64_t salt0,
                        uint64_t salt1)
{
    const double u1 = std::max(1.0e-12, hashUnitCell(seed, cell_x, cell_y, salt0));
    const double u2 = hashUnitCell(seed, cell_x, cell_y, salt1);
    return std::sqrt(-2.0 * std::log(u1)) * std::cos(2.0 * kPi * u2);
}

struct GlobalSurfaceCell {
    Vec3 position;
    std::complex<double> reflectivity;
    double area_sqrt_weight = 1.0;
    double theta_deg = 0.0;
    double slant_range_m = 0.0;
};

struct GlobalSurfaceGrid {
    std::string key;
    double azimuth_step_deg = 0.0;
    double reference_time_sec = 0.0;
    Vec3 reference_platform_position;
    std::vector<std::vector<GlobalSurfaceCell>> azimuth_bins;
};

std::string surfaceGridKey(const gmti::target_injection::RadarConfig &radar,
                           const TargetGlobalConfig &global,
                           const SceneConfig &scene,
                           uint32_t random_seed,
                           double reference_time_sec)
{
    std::ostringstream os;
    os.precision(17);
    os << random_seed << '|' << radar.beam_width_deg << '|'
       << scene.range_min_m << '|' << scene.range_max_m << '|'
       << scene.azimuth_min_deg << '|' << scene.azimuth_max_deg << '|'
       << scene.ground_z_m << '|' << scene.area.mean_power << '|'
       << scene.area.texture_sigma << '|' << scene.area.spatial_cell_m << '|'
       << scene.area.azimuth_subcell_count << '|'
       << global.platform_height_m << '|' << global.platform_speed_mps << '|'
       << reference_time_sec;
    return os.str();
}

const GlobalSurfaceGrid &globalSurfaceGrid(
    const gmti::target_injection::RadarConfig &radar,
    const TargetGlobalConfig &global,
    const SceneConfig &scene,
    uint32_t random_seed,
    double reference_time_sec)
{
    static thread_local GlobalSurfaceGrid grid;
    const std::string key = surfaceGridKey(
        radar, global, scene, random_seed, reference_time_sec);
    if (grid.key == key) return grid;

    grid = GlobalSurfaceGrid();
    grid.key = key;
    grid.reference_time_sec = reference_time_sec;
    const double cell_m = std::max(1.0, scene.area.spatial_cell_m);
    const int az_subcells = std::max(1, std::min(257, scene.area.azimuth_subcell_count));
    grid.azimuth_step_deg = radar.beam_width_deg / static_cast<double>(az_subcells);
    const int az_count = std::max(1, static_cast<int>(
        std::ceil((scene.azimuth_max_deg - scene.azimuth_min_deg) /
                  grid.azimuth_step_deg)));
    grid.azimuth_bins.resize(static_cast<std::size_t>(az_count));

    // Build a deterministic world-coordinate surface around the current
    // period midpoint.  Reflectivity is hashed from absolute ENU cell indices,
    // so overlapping cells keep identical complex coefficients across periods.
    const PlatformState reference_platform =
        evaluatePlatformState(global, reference_time_sec);
    grid.reference_platform_position = reference_platform.position;
    const gmti::sim_geometry::ENUPoint reference_enu =
        gmti::sim_geometry::localToEnu(
            gmti::sim_geometry::LocalPoint(
                reference_platform.position.x,
                reference_platform.position.y,
                reference_platform.position.z),
            global.geometry);
    const gmti::sim_geometry::ENUVelocity reference_velocity_enu =
        gmti::sim_geometry::localVelocityToEnu(
            gmti::sim_geometry::LocalVelocity(
                reference_platform.velocity.x,
                reference_platform.velocity.y,
                reference_platform.velocity.z),
            global.geometry);
    const double height_delta =
        reference_platform.position.z - scene.ground_z_m;
    const double nominal_ground_min_m = std::sqrt(std::max(
        0.0,
        scene.range_min_m * scene.range_min_m -
        height_delta * height_delta));
    const double nominal_ground_max_m = std::sqrt(std::max(
        0.0,
        scene.range_max_m * scene.range_max_m -
        height_delta * height_delta));
    const double scan_duration_sec =
        radar.prf_hz > 0.0
            ? static_cast<double>(radar.beam_count * radar.pulse_num) /
                  radar.prf_hz
            : 0.0;
    const double platform_speed_mps =
        norm(reference_platform.velocity);
    const double footprint_guard_m =
        0.5 * platform_speed_mps * scan_duration_sec +
        2.0 * cell_m;
    const double ground_min_m =
        std::max(0.0, nominal_ground_min_m - footprint_guard_m);
    const double ground_max_m =
        nominal_ground_max_m + footprint_guard_m;

    // Bound the requested annular sector in ENU.  The actual cells below are
    // a Cartesian square lattice; angular bins are only an acceleration
    // index and do not define or duplicate physical scatterers.
    double min_e = std::numeric_limits<double>::infinity();
    double max_e = -std::numeric_limits<double>::infinity();
    double min_n = std::numeric_limits<double>::infinity();
    double max_n = -std::numeric_limits<double>::infinity();
    for (double theta = scene.azimuth_min_deg;
         theta <= scene.azimuth_max_deg + 1.0e-9; theta += 0.25) {
        const gmti::sim_geometry::LookVectorEN look =
            gmti::sim_geometry::makeAlgorithmLookVectorEN(
                reference_velocity_enu.ve, reference_velocity_enu.vn,
                theta, global.geometry);
        for (double radius : {ground_min_m, ground_max_m}) {
            const double e = reference_enu.e + radius * look.east;
            const double n = reference_enu.n + radius * look.north;
            min_e = std::min(min_e, e);
            max_e = std::max(max_e, e);
            min_n = std::min(min_n, n);
            max_n = std::max(max_n, n);
        }
    }
    const double sigma = std::sqrt(std::max(1.0e-12, scene.area.mean_power) / 2.0);
    const uint64_t seed = static_cast<uint64_t>(random_seed);
    const double heading_deg = std::atan2(
        reference_velocity_enu.vn, reference_velocity_enu.ve) * 180.0 / kPi;
    const double side_dir_deg = global.geometry.squint_side == 1 ? -90.0 : 90.0;
    const int64_t ix_begin = static_cast<int64_t>(std::floor(min_e / cell_m));
    const int64_t ix_end = static_cast<int64_t>(std::ceil(max_e / cell_m));
    const int64_t iy_begin = static_cast<int64_t>(std::floor(min_n / cell_m));
    const int64_t iy_end = static_cast<int64_t>(std::ceil(max_n / cell_m));
    for (int64_t ix = ix_begin; ix <= ix_end; ++ix) {
        const double e = (static_cast<double>(ix) + 0.5) * cell_m;
        for (int64_t iy = iy_begin; iy <= iy_end; ++iy) {
            const double n = (static_cast<double>(iy) + 0.5) * cell_m;
            const double de = e - reference_enu.e;
            const double dn = n - reference_enu.n;
            const double ground_range = std::hypot(de, dn);
            if (ground_range < ground_min_m || ground_range >= ground_max_m) continue;
            const double target_azimuth_deg = std::atan2(dn, de) * 180.0 / kPi;
            double theta_deg = target_azimuth_deg - heading_deg +
                               side_dir_deg - global.geometry.beam_theta_offset_deg;
            theta_deg = wrapTo180(theta_deg);
            if (theta_deg < scene.azimuth_min_deg ||
                theta_deg >= scene.azimuth_max_deg) continue;
            const int ai = static_cast<int>(std::floor(
                (theta_deg - scene.azimuth_min_deg) / grid.azimuth_step_deg));
            if (ai < 0 || ai >= az_count) continue;

            GlobalSurfaceCell cell;
            cell.theta_deg = theta_deg;
            cell.slant_range_m = std::hypot(ground_range, height_delta);
            const gmti::sim_geometry::LocalPoint local =
                gmti::sim_geometry::enuToLocal(
                    gmti::sim_geometry::ENUPoint(e, n, scene.ground_z_m),
                    global.geometry);
            cell.position = Vec3(local.x, local.y, local.z);
            cell.area_sqrt_weight = 1.0;
            const double u = std::max(1.0e-12, hashUnitCell(seed, ix, iy, 11ULL));
            const double texture = scene.area.texture_sigma > 0.0
                ? std::exp(scene.area.texture_sigma *
                           hashGaussianCell(seed, ix, iy, 23ULL, 29ULL))
                : 1.0;
            const double amplitude = sigma * std::sqrt(-2.0 * std::log(u)) * texture;
            // The clutter contract for this functional case is that all
            // receive channels observe the same spatial realization; their
            // only channel-dependent phase is the deterministic receive-array
            // geometry applied below.  A per-cell random scatterer phase is an
            // additional phase field.  It is shared between channels, but
            // after summing many cells it becomes channel-dependent texture
            // that the narrowband CSI model cannot cancel as one phase term.
            // Keep the spatial amplitude texture and make the cell coefficient
            // real, so no extra random phase is injected into the scene.
            cell.reflectivity = std::complex<double>(amplitude, 0.0);
            grid.azimuth_bins[static_cast<std::size_t>(ai)].push_back(cell);
        }
    }
    return grid;
}

int nextPowerOfTwo(int n)
{
    int value = 1;
    while (value < n && value <= std::numeric_limits<int>::max() / 2) {
        value <<= 1;
    }
    return value;
}

// Reused by every packet in the single-threaded Stage2 generator.  The input
// is the discrete, range-compressed surface reflectivity sequence; convolution
// with the transmitted chirp produces the equivalent raw-LFM receive stream.
// Reusing plans and the chirp spectrum is important for a 130-PRT aperture.
class ContinuousAreaLfmConvolver {
public:
    ContinuousAreaLfmConvolver() = default;

    ~ContinuousAreaLfmConvolver()
    {
        reset();
    }

    bool configure(const gmti::target_injection::RadarConfig &radar,
                   int chirp_phase_sign,
                   const std::string &lfm_time_reference,
                   std::string &err)
    {
        const int raw_len = radar.pulse_len;
        const int chirp_len = static_cast<int>(
            std::floor(radar.tr_sec * radar.fs_hz + 0.5));
        const int required = raw_len + chirp_len - 1;
        const int fft_len = nextPowerOfTwo(required);
        const int sign = chirp_phase_sign < 0 ? -1 : 1;
        const bool center_reference =
            lfm_time_reference != "legacy_start" && lfm_time_reference != "start";
        if (raw_len <= 0 || chirp_len <= 0 || required <= 0 ||
            fft_len < required || !(radar.fs_hz > 0.0) ||
            !(radar.tr_sec > 0.0) || !(radar.br_hz > 0.0)) {
            err = "invalid continuous-area raw-LFM convolution dimensions";
            return false;
        }
        if (raw_len_ == raw_len && chirp_len_ == chirp_len &&
            fft_len_ == fft_len && fs_hz_ == radar.fs_hz &&
            br_hz_ == radar.br_hz && tr_sec_ == radar.tr_sec &&
            chirp_phase_sign_ == sign &&
            center_reference_ == center_reference && buffer_ != nullptr &&
            forward_ != nullptr && inverse_ != nullptr) {
            return true;
        }

        reset();
        raw_len_ = raw_len;
        chirp_len_ = chirp_len;
        fft_len_ = fft_len;
        fs_hz_ = radar.fs_hz;
        br_hz_ = radar.br_hz;
        tr_sec_ = radar.tr_sec;
        chirp_phase_sign_ = sign;
        center_reference_ = center_reference;
        extraction_shift_ = center_reference_ ? chirp_len_ / 2 : 0;
        buffer_ = static_cast<fftw_complex *>(
            fftw_malloc(sizeof(fftw_complex) * static_cast<size_t>(fft_len_)));
        if (buffer_ == nullptr) {
            err = "FFTW allocation failed for continuous-area raw-LFM convolution";
            reset();
            return false;
        }
        forward_ = fftw_plan_dft_1d(
            fft_len_, buffer_, buffer_, FFTW_FORWARD, FFTW_ESTIMATE);
        inverse_ = fftw_plan_dft_1d(
            fft_len_, buffer_, buffer_, FFTW_BACKWARD, FFTW_ESTIMATE);
        if (forward_ == nullptr || inverse_ == nullptr) {
            err = "FFTW plan creation failed for continuous-area raw-LFM convolution";
            reset();
            return false;
        }

        std::memset(buffer_, 0,
                    sizeof(fftw_complex) * static_cast<size_t>(fft_len_));
        std::complex<double> *time =
            reinterpret_cast<std::complex<double> *>(buffer_);
        const double kr = radar.br_hz / radar.tr_sec;
        for (int n = 0; n < chirp_len_; ++n) {
            const double t = center_reference_
                ? static_cast<double>(n - extraction_shift_) / radar.fs_hz
                : static_cast<double>(n) / radar.fs_hz;
            const double phase = static_cast<double>(sign) * kPi * kr * t * t;
            time[n] = std::complex<double>(std::cos(phase), std::sin(phase));
        }
        fftw_execute(forward_);
        chirp_spectrum_.assign(time, time + fft_len_);
        return true;
    }

    bool apply(std::vector<std::complex<float>> &reflectivity,
               std::string &err)
    {
        if (buffer_ == nullptr || forward_ == nullptr || inverse_ == nullptr ||
            static_cast<int>(reflectivity.size()) != raw_len_ ||
            static_cast<int>(chirp_spectrum_.size()) != fft_len_) {
            err = "continuous-area raw-LFM convolver is not configured";
            return false;
        }
        std::memset(buffer_, 0,
                    sizeof(fftw_complex) * static_cast<size_t>(fft_len_));
        std::complex<double> *data =
            reinterpret_cast<std::complex<double> *>(buffer_);
        for (int n = 0; n < raw_len_; ++n) {
            data[n] = std::complex<double>(reflectivity[n].real(),
                                           reflectivity[n].imag());
        }
        fftw_execute(forward_);
        for (int f = 0; f < fft_len_; ++f) {
            data[f] *= chirp_spectrum_[f];
        }
        fftw_execute(inverse_);
        const double scale = 1.0 / static_cast<double>(fft_len_);
        for (int n = 0; n < raw_len_; ++n) {
            const int source = n + extraction_shift_;
            reflectivity[n] = std::complex<float>(
                static_cast<float>(data[source].real() * scale),
                static_cast<float>(data[source].imag() * scale));
        }
        return true;
    }

private:
    void reset()
    {
        if (forward_ != nullptr) fftw_destroy_plan(forward_);
        if (inverse_ != nullptr) fftw_destroy_plan(inverse_);
        if (buffer_ != nullptr) fftw_free(buffer_);
        forward_ = nullptr;
        inverse_ = nullptr;
        buffer_ = nullptr;
        raw_len_ = 0;
        chirp_len_ = 0;
        fft_len_ = 0;
        fs_hz_ = 0.0;
        br_hz_ = 0.0;
        tr_sec_ = 0.0;
        chirp_phase_sign_ = 0;
        center_reference_ = true;
        extraction_shift_ = 0;
        chirp_spectrum_.clear();
    }

    int raw_len_ = 0;
    int chirp_len_ = 0;
    int fft_len_ = 0;
    double fs_hz_ = 0.0;
    double br_hz_ = 0.0;
    double tr_sec_ = 0.0;
    int chirp_phase_sign_ = 0;
    bool center_reference_ = true;
    int extraction_shift_ = 0;
    fftw_complex *buffer_ = nullptr;
    fftw_plan forward_ = nullptr;
    fftw_plan inverse_ = nullptr;
    std::vector<std::complex<double>> chirp_spectrum_;
};

} // namespace

void addScatterersToPacket(std::vector<uint8_t> &packet,
                           const gmti::target_injection::RadarConfig &radar,
                           const TargetGlobalConfig &global,
                           const ScattererList &scatterers,
                           int period_id,
                           int beam_id,
                           int pulse_id,
                           double beam_gain_threshold,
                           Stage2Stats &stats,
                           double time_override_sec,
                           double servo_azimuth_override_deg)
{
    const double kr = radar.br_hz / radar.tr_sec;
    const double lambda = kC / radar.fc_hz;
    const std::size_t channel_count = static_cast<std::size_t>(std::max(2, radar.new_protocol_channel_count));
    const std::string iq_type = radar.iq_data_type.empty() ? "float32" : radar.iq_data_type;
    for (size_t i = 0; i < scatterers.size(); ++i) {
        const Scatterer &s = scatterers[i];
        const GeometrySample g = scattererGeometry(
            radar, global, s, period_id, beam_id, pulse_id,
            time_override_sec, servo_azimuth_override_deg);
        if (!g.in_range_window) continue;
        const gmti::target_injection::VisibilityResult vis =
            evaluateVisibility(radar, global, g.angle_error_deg);
        if (!vis.visible || vis.beam_gain < beam_gain_threshold) continue;
        const bool center_reference = global.lfm_time_reference != "legacy_start" &&
                                      global.lfm_time_reference != "start";
        const double support_start_sec = center_reference
            ? g.tau_rel_sec - 0.5 * radar.tr_sec
            : g.tau_rel_sec;
        const double support_end_sec = center_reference
            ? g.tau_rel_sec + 0.5 * radar.tr_sec
            : g.tau_rel_sec + radar.tr_sec;
        const int n_start = static_cast<int>(std::floor(
            support_start_sec * radar.fs_hz));
        const int n_end = std::min(
            radar.pulse_len,
            static_cast<int>(std::ceil(support_end_sec * radar.fs_hz)));
        if (n_end <= 0 || n_start >= radar.pulse_len) continue;
        std::vector<std::complex<double>> carriers(channel_count);
        for (std::size_t ch = 0; ch < channel_count; ++ch) {
            const double phase = gmti::target_injection::receiveChannelPhaseRad(
                radar, global, g, static_cast<int>(ch + 1), lambda) + s.phase_rad;
            carriers[ch] = std::exp(std::complex<double>(0.0, phase));
        }
        int touched = 0;
        for (int n = std::max(0, n_start); n < n_end; ++n) {
            const double dt = static_cast<double>(n) / radar.fs_hz - g.tau_rel_sec;
            if (center_reference) {
                if (dt < -0.5 * radar.tr_sec || dt >= 0.5 * radar.tr_sec) continue;
            } else if (dt < 0.0 || dt >= radar.tr_sec) {
                continue;
            }
            const double chirp_phase =
                static_cast<double>(global.chirp_phase_sign) * kPi * kr * dt * dt;
            const std::complex<double> echo =
                s.amplitude * vis.beam_gain *
                std::exp(std::complex<double>(0.0, chirp_phase));
            for (std::size_t ch = 0; ch < channel_count; ++ch) {
                const std::complex<double> echo_ch = echo * carriers[ch];
                const std::complex<float> value(
                    static_cast<float>(echo_ch.real()),
                    static_cast<float>(echo_ch.imag()));
                const int channel_1based = static_cast<int>(ch + 1);
                storeCh(packet, n, channel_1based, channel_count, iq_type,
                        loadCh(packet, n, channel_1based, channel_count, iq_type) + value);
            }
            ++touched;
        }
        if (touched > 0) {
            ++stats.scatterer_echoes;
            stats.scatterer_samples += static_cast<uint64_t>(touched);
        }
    }
}

bool addContinuousAreaClutter(std::vector<uint8_t> &packet,
                              const gmti::target_injection::RadarConfig &radar,
                              const TargetGlobalConfig &global,
                              const SceneConfig &scene,
                              uint32_t random_seed,
                              int reference_period_id,
                              int period_id,
                              int beam_id,
                              int pulse_id,
                              bool raw_lfm_domain,
                              Stage2Stats &stats,
                              std::string &err,
                              double time_override_sec,
                              double servo_azimuth_override_deg,
                              double grid_reference_time_override_sec)
{
    if (!scene.area.enabled) return true;
    const bool continuous_mode =
        scene.area.model == "continuous_texture" ||
        scene.area.model == "continuous_surface" ||
        scene.area.model == "continuous_grid" ||
        scene.area.model == "grid_texture";
    if (!continuous_mode) return true;

    const std::size_t channel_count = static_cast<std::size_t>(std::max(2, radar.new_protocol_channel_count));
    const std::string iq_type = radar.iq_data_type.empty() ? "float32" : radar.iq_data_type;
    const double lambda = kC / radar.fc_hz;
    (void)reference_period_id;
    const double theta_cmd_deg = std::isfinite(servo_azimuth_override_deg)
        ? servo_azimuth_override_deg
        : radar.scan_min_deg + radar.scan_step_deg * static_cast<double>(beam_id);
    const int mid_beam = std::max(0, radar.beam_count / 2);
    const int mid_pulse = std::max(0, radar.pulse_num / 2);
    const double grid_reference_time_sec =
        std::isfinite(grid_reference_time_override_sec)
            ? grid_reference_time_override_sec
            : pulseTimeSec(radar, period_id, mid_beam, mid_pulse);
    const GlobalSurfaceGrid &surface_grid =
        globalSurfaceGrid(
            radar, global, scene, random_seed, grid_reference_time_sec);
    std::vector<std::vector<std::complex<float>>> surfaces;
    if (raw_lfm_domain) {
        surfaces.assign(channel_count,
                        std::vector<std::complex<float>>(
                            static_cast<size_t>(radar.pulse_len),
                            std::complex<float>(0.0f, 0.0f)));
    }
    int injected = 0;
    const double packet_time_sec = std::isfinite(time_override_sec)
        ? time_override_sec : pulseTimeSec(radar, period_id, beam_id, pulse_id);
    const PlatformState current_platform =
        evaluatePlatformState(global, packet_time_sec);
    const double platform_displacement_m =
        norm(current_platform.position -
             surface_grid.reference_platform_position);
    const double min_ground_range_m = std::sqrt(std::max(
        1.0,
        scene.range_min_m * scene.range_min_m -
        std::pow(current_platform.position.z - scene.ground_z_m, 2.0)));
    const double ratio = std::min(
        1.0, platform_displacement_m /
                 std::max(1.0, min_ground_range_m));
    const double motion_guard_deg =
        rad2deg(std::asin(ratio)) + surface_grid.azimuth_step_deg;
    const double candidate_half_width =
        0.5 * radar.beam_width_deg + motion_guard_deg;
    const int first_az = std::max(0, static_cast<int>(std::floor(
        (theta_cmd_deg - candidate_half_width - scene.azimuth_min_deg) /
        surface_grid.azimuth_step_deg)));
    const int last_az = std::min(
        static_cast<int>(surface_grid.azimuth_bins.size()) - 1,
        static_cast<int>(std::floor(
            (theta_cmd_deg + candidate_half_width -
             scene.azimuth_min_deg) /
            surface_grid.azimuth_step_deg)));
    for (int ai = first_az; ai <= last_az; ++ai) {
        const std::vector<GlobalSurfaceCell> &cells =
            surface_grid.azimuth_bins[static_cast<std::size_t>(ai)];
        for (const GlobalSurfaceCell &cell : cells) {
            const Vec3 &target_position = cell.position;
            const GeometrySample g = fixedPointGeometry(
                radar, global, target_position, period_id, beam_id, pulse_id,
                time_override_sec, servo_azimuth_override_deg);
            if (!g.in_range_window) continue;
            if (g.range_m < scene.range_min_m ||
                g.range_m >= scene.range_max_m) {
                continue;
            }
            const gmti::target_injection::VisibilityResult vis =
                evaluateVisibility(radar, global, g.angle_error_deg);
            if (!vis.visible) continue;

            if (g.range_sample_float < 0.0 ||
                g.range_sample_float >= static_cast<double>(radar.pulse_len)) {
                continue;
            }
            // Each physical cell contributes exactly once.  The azimuth grid is
            // shared globally, so overlap beams observe the same complex cell.
            const double amp_scale = scene.clutter_amplitude_scale *
                vis.beam_gain * cell.area_sqrt_weight;
            // The continuous surface is a common clutter realization seen by
            // all receive channels.  Keep one common fast-time delay for the
            // cell and express the receive-array geometry only through its
            // carrier phase.  Applying the per-channel path length here as a
            // second fractional sample delay creates a channel-dependent
            // wideband waveform (and interpolation texture), which violates
            // the Stage2 clutter contract and cannot be removed by the
            // narrowband CSI phase compensation path.
            const double common_range_sample = g.range_sample_float;
            for (std::size_t ch = 0; ch < channel_count; ++ch) {
                const int channel_1based = static_cast<int>(ch + 1);
                if (!std::isfinite(common_range_sample) ||
                    common_range_sample < 0.0 ||
                    common_range_sample >= static_cast<double>(radar.pulse_len)) {
                    continue;
                }
                const double phase = gmti::target_injection::receiveChannelPhaseRad(
                    radar, global, g, channel_1based, lambda);
                const std::complex<double> echo = amp_scale * cell.reflectivity *
                    std::exp(std::complex<double>(0.0, phase));
                const std::complex<float> value(
                    static_cast<float>(echo.real()), static_cast<float>(echo.imag()));
                if (raw_lfm_domain) {
                    addFractionalDelayImpulse(
                        surfaces[ch], common_range_sample, value);
                } else {
                    const int output_sample = static_cast<int>(
                        std::floor(common_range_sample + 0.5));
                    if (output_sample < 0 || output_sample >= radar.pulse_len) {
                        continue;
                    }
                    storeCh(packet, output_sample, channel_1based, channel_count, iq_type,
                            loadCh(packet, output_sample, channel_1based,
                                   channel_count, iq_type) + value);
                }
            }
            ++injected;
        }
    }
    if (injected > 0) {
        if (raw_lfm_domain) {
            static thread_local ContinuousAreaLfmConvolver convolver;
            if (!convolver.configure(
                    radar, global.chirp_phase_sign,
                    global.lfm_time_reference, err)) {
                return false;
            }
            for (std::size_t ch = 0; ch < channel_count; ++ch) {
                if (!convolver.apply(surfaces[ch], err)) return false;
            }
            for (int n = 0; n < radar.pulse_len; ++n) {
                for (std::size_t ch = 0; ch < channel_count; ++ch) {
                    const int channel_1based = static_cast<int>(ch + 1);
                    storeCh(packet, n, channel_1based, channel_count, iq_type,
                            loadCh(packet, n, channel_1based, channel_count, iq_type) +
                                surfaces[ch][static_cast<size_t>(n)]);
                }
            }
            ++stats.continuous_area_raw_lfm_packets;
        } else {
            ++stats.continuous_area_precompressed_packets;
        }
        ++stats.continuous_area_packets;
        stats.continuous_area_samples += static_cast<uint64_t>(injected);
    }
    return true;
}

void addThermalNoise(std::vector<uint8_t> &packet,
                     const gmti::target_injection::RadarConfig &radar,
                     double noise_power,
                     std::mt19937 &rng,
                     Stage2Stats &stats)
{
    if (noise_power <= 0.0) return;
    const double sigma = std::sqrt(noise_power / 2.0);
    std::normal_distribution<float> n01(0.0f, static_cast<float>(sigma));
    const std::size_t channel_count = static_cast<std::size_t>(std::max(2, radar.new_protocol_channel_count));
    const std::string iq_type = radar.iq_data_type.empty() ? "float32" : radar.iq_data_type;
    for (int n = 0; n < radar.pulse_len; ++n) {
        for (std::size_t ch = 0; ch < channel_count; ++ch) {
            const std::complex<float> noise(n01(rng), n01(rng));
            const int channel_1based = static_cast<int>(ch + 1);
            storeCh(packet, n, channel_1based, channel_count, iq_type,
                    loadCh(packet, n, channel_1based, channel_count, iq_type) + noise);
            stats.sum_noise_power += static_cast<double>(std::norm(noise));
            ++stats.noise_samples;
        }
    }
}

void scanPacketStats(const std::vector<uint8_t> &packet,
                     const gmti::target_injection::RadarConfig &radar,
                     Stage2Stats &stats)
{
    const std::size_t channel_count = static_cast<std::size_t>(std::max(2, radar.new_protocol_channel_count));
    const std::string iq_type = radar.iq_data_type.empty() ? "float32" : radar.iq_data_type;
    for (int n = 0; n < radar.pulse_len; ++n) {
        for (std::size_t ch = 0; ch < channel_count; ++ch) {
            const std::complex<float> value = loadCh(
                packet, n, static_cast<int>(ch + 1), channel_count, iq_type);
            const float vals[2] = {value.real(), value.imag()};
            for (int k = 0; k < 2; ++k) {
                if (std::isnan(vals[k])) stats.has_nan = true;
                if (std::isinf(vals[k])) stats.has_inf = true;
                stats.max_abs_component = std::max(
                    stats.max_abs_component, std::fabs(static_cast<double>(vals[k])));
            }
        }
    }
}

} // namespace stage2
} // namespace gmti
