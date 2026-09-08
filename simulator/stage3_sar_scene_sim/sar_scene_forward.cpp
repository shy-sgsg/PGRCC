#include "sar_scene_forward.h"

#include "sar_image_reader.h"
#include "stage3_cuda_forward.h"
#include "stage3_scene_mapping.h"
#include "../target_injection/channel_impairments.h"
#include "../target_injection/lfm_echo_generator.h"
#include "../target_injection/radar_geometry.h"
#include "../target_injection/target_config.h"
#include "../target_injection/truth_writer.h"
#include "dbs/NewProtocolLayout.hpp"
#include <fftw3.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <complex>
#include <cstdint>
#include <unistd.h>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <random>
#include <sstream>
#include <vector>

namespace gmti {
namespace stage3 {

namespace {

const double kPi = 3.14159265358979323846;
const double kC = 299792458.0;

struct CanvasCell {
    double e = 0.0;
    double n = 0.0;
    float amplitude = 0.0f;
    float phase = 0.0f;
    uint8_t valid = 0;
    uint8_t footprint = 0;
    int tile_id = 0;
    int tile_x = 0;
    int tile_y = 0;
    int flip_mode = 0;
    int source_row = -1;
    int source_col = -1;
};

struct SceneCanvas {
    int width = 0;
    int height = 0;
    double resolution_m = 1.0;
    double min_e = 0.0;
    double min_n = 0.0;
    double max_e = 0.0;
    double max_n = 0.0;
    bool roi_enabled = false;
    int roi_beam_id_1based = -1;
    double anchor_e = 0.0;
    double anchor_n = 0.0;
    double range_axis_e = -1.0;
    double range_axis_n = 0.0;
    double azimuth_axis_e = 0.0;
    double azimuth_axis_n = -1.0;
    int source_offset_col = 0;
    int source_offset_row = 0;
    std::vector<CanvasCell> cells;
};

struct BeamCellRef {
    int idx = -1;
    float beam_gain = 1.0f;
};

struct Stage3TargetContext {
    gmti::target_injection::RadarConfig radar;
    gmti::target_injection::TargetGlobalConfig global;
    std::vector<gmti::target_injection::TargetConfig> targets;
    gmti::target_injection::InjectionStats stats;
    gmti::target_injection::TruthWriter truth;
    bool truth_open = false;
};

uint64_t mix64(uint64_t x)
{
    x += 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    return x ^ (x >> 31);
}

double hashUnit(uint64_t seed, uint64_t a, uint64_t b, uint64_t c)
{
    uint64_t x = seed;
    x ^= mix64(a + 0x100000001b3ULL);
    x ^= mix64(b + 0x9e3779b97f4a7c15ULL);
    x ^= mix64(c + 0xbf58476d1ce4e5b9ULL);
    x = mix64(x);
    return (static_cast<double>((x >> 11) & ((1ULL << 53) - 1)) + 0.5) /
           static_cast<double>(1ULL << 53);
}

void storeCh(std::vector<uint8_t> &packet,
             int n,
             int ch,
             std::size_t channel_count,
             const std::string &iq_type,
             const std::complex<float> &v);

std::complex<float> loadCh(const std::vector<uint8_t> &packet,
                           int n,
                           int ch,
                           std::size_t channel_count,
                           const std::string &iq_type);

bool cropSarImageToValidArea(SarImage &img, const Stage3Config &cfg, std::string &err)
{
    if (img.original_width <= 0) img.original_width = img.width;
    if (img.original_height <= 0) img.original_height = img.height;
    img.crop_col_start = 0;
    img.crop_row_start = 0;
    if (!cfg.sar_input.valid_crop_enabled) return true;

    const int x0 = cfg.sar_input.valid_col_start;
    const int y0 = cfg.sar_input.valid_row_start;
    const int x1 = cfg.sar_input.valid_col_end_exclusive < 0
        ? img.width : cfg.sar_input.valid_col_end_exclusive;
    const int y1 = cfg.sar_input.valid_row_end_exclusive < 0
        ? img.height : cfg.sar_input.valid_row_end_exclusive;
    if (x0 < 0 || y0 < 0 || x1 > img.width || y1 > img.height ||
        x0 >= x1 || y0 >= y1) {
        std::ostringstream os;
        os << "invalid SAR valid crop [" << x0 << "," << x1 << ") x ["
           << y0 << "," << y1 << ") for source " << img.width << "x" << img.height;
        err = os.str();
        return false;
    }
    const int cropped_width = x1 - x0;
    const int cropped_height = y1 - y0;
    std::vector<float> cropped(static_cast<size_t>(cropped_width) * cropped_height);
    for (int y = 0; y < cropped_height; ++y) {
        const float *src_row = &img.value[static_cast<size_t>(y + y0) * img.width + x0];
        std::copy(src_row, src_row + cropped_width,
                  cropped.begin() + static_cast<size_t>(y) * cropped_width);
    }
    img.value.swap(cropped);
    img.width = cropped_width;
    img.height = cropped_height;
    img.crop_col_start = x0;
    img.crop_row_start = y0;
    return true;
}

void normalizeSarImage(SarImage &img, const Stage3Config &cfg)
{
    if (img.value.empty()) return;
    std::vector<float> sorted = img.value;
    std::sort(sorted.begin(), sorted.end());
    const auto pct = [&](double p) -> float {
        const double q = std::max(0.0, std::min(100.0, p)) / 100.0;
        const size_t idx = static_cast<size_t>(q * static_cast<double>(sorted.size() - 1));
        return sorted[idx];
    };
    float lo = cfg.sar_input.percentile_normalize ? pct(cfg.sar_input.percentile_low) : sorted.front();
    float hi = cfg.sar_input.percentile_normalize ? pct(cfg.sar_input.percentile_high) : sorted.back();
    if (!(hi > lo)) hi = lo + 1.0f;
    const std::string typ = cfg.sar_input.image_type.empty() ? cfg.sar_input.image_value_type : cfg.sar_input.image_type;
    for (float &v : img.value) {
        double x = static_cast<double>(v);
        if (typ == "db") {
            x = std::pow(10.0, x / 10.0);
        } else if (typ == "amplitude") {
            x = x * x;
        } else if (typ == "complex") {
            x = x * x;
        }
        x = (x - lo) / (hi - lo);
        x = std::max(0.0, std::min(1.0, x));
        v = static_cast<float>(std::sqrt(x));
    }
}

void writePgmPreview(const std::string &path, int w, int h, const std::vector<float> &v)
{
    std::ofstream os(path.c_str(), std::ios::binary);
    if (!os || w <= 0 || h <= 0) return;
    os << "P5\n" << w << " " << h << "\n255\n";
    float maxv = 0.0f;
    for (float x : v) maxv = std::max(maxv, x);
    if (maxv <= 0.0f) maxv = 1.0f;
    for (float x : v) {
        const unsigned char b = static_cast<unsigned char>(std::max(0.0f, std::min(255.0f, 255.0f * x / maxv)));
        os.write(reinterpret_cast<const char *>(&b), 1);
    }
}

bool buildCanvas(const Stage3Config &cfg, SceneCanvas &c, std::string &err)
{
    const bool roi_enabled = (cfg.scene.mode == "roi" || cfg.scene.roi.enabled) &&
                             cfg.scene.roi.beam_id_1based >= 1 &&
                             cfg.scene.roi.beam_id_1based <= cfg.system.beam_count;
    const double h = cfg.system.platform_height_m - cfg.scene.ground_z_m;
    const double gr_min = std::sqrt(std::max(0.0, cfg.scene.range_min_m * cfg.scene.range_min_m - h * h));
    const double gr_max = std::sqrt(std::max(0.0, cfg.scene.range_max_m * cfg.scene.range_max_m - h * h));
    const Stage3SceneFrame frame = makeStage3SceneFrame(cfg);
    c.range_axis_e = frame.range_axis_e;
    c.range_axis_n = frame.range_axis_n;
    c.azimuth_axis_e = frame.azimuth_axis_e;
    c.azimuth_axis_n = frame.azimuth_axis_n;
    c.anchor_e = frame.anchor_e_m;
    c.anchor_n = frame.anchor_n_m;
    c.resolution_m = std::max(0.05, roi_enabled ? cfg.scene.roi.resolution_m : cfg.canvas.resolution_m);
    c.roi_enabled = roi_enabled;
    c.roi_beam_id_1based = roi_enabled ? cfg.scene.roi.beam_id_1based : -1;

    if (roi_enabled) {
        const int roi_beam = cfg.scene.roi.beam_id_1based - 1;
        const double theta = cfg.system.scan_min_deg + cfg.system.scan_step_deg * roi_beam;
        double look_e = 0.0, look_n = 0.0;
        stage3ComputeLook(theta, cfg.system.squint_side, look_e, look_n);
        const double az_e = -look_n;
        const double az_n = look_e;
        const double center_gr = std::sqrt(std::max(0.0,
            cfg.scene.roi.center_slant_range_m * cfg.scene.roi.center_slant_range_m - h * h));
        const double center_e = center_gr * look_e;
        const double center_n = center_gr * look_n;
        const double range_extent = cfg.scene.roi.range_extent_m + 2.0 * cfg.scene.roi.margin_m;
        const double az_extent = cfg.scene.roi.azimuth_extent_m + 2.0 * cfg.scene.roi.margin_m;
        c.width = std::max(1, static_cast<int>(std::ceil(range_extent / c.resolution_m)));
        c.height = std::max(1, static_cast<int>(std::ceil(az_extent / c.resolution_m)));
        c.min_e = c.min_n = std::numeric_limits<double>::infinity();
        c.max_e = c.max_n = -std::numeric_limits<double>::infinity();
        const long long count = static_cast<long long>(c.width) * c.height;
        if (count > cfg.canvas.max_cells) {
            std::ostringstream os;
            os << "ROI canvas requires " << count << " cells, exceeding canvas.max_cells="
               << cfg.canvas.max_cells;
            err = os.str();
            return false;
        }
        c.cells.resize(static_cast<size_t>(count));
        for (int y = 0; y < c.height; ++y) {
            const double v = (static_cast<double>(y) + 0.5 - 0.5 * c.height) * c.resolution_m;
            for (int x = 0; x < c.width; ++x) {
                const double u = (static_cast<double>(x) + 0.5 - 0.5 * c.width) * c.resolution_m;
                CanvasCell &cell = c.cells[static_cast<size_t>(y) * c.width + x];
                cell.e = center_e + u * look_e + v * az_e;
                cell.n = center_n + u * look_n + v * az_n;
                c.min_e = std::min(c.min_e, cell.e);
                c.max_e = std::max(c.max_e, cell.e);
                c.min_n = std::min(c.min_n, cell.n);
                c.max_n = std::max(c.max_n, cell.n);
            }
        }
    } else {
        double min_e = std::numeric_limits<double>::infinity();
        double min_n = std::numeric_limits<double>::infinity();
        double max_e = -std::numeric_limits<double>::infinity();
        double max_n = -std::numeric_limits<double>::infinity();
        // Bound only the requested beam subset.  The angular halo is derived
        // from the same Gaussian threshold used by markFootprint/buildBeamRefs,
        // so no contributing cell is discarded while a one-beam run no longer
        // allocates the all-61-beam envelope.
        const int beam_begin = std::max(0, cfg.forward.beam_start - 1);
        const int beam_end = std::min(cfg.system.beam_count - 1,
            beam_begin + std::max(1, cfg.forward.beam_count) - 1);
        const double max_error_deg = cfg.system.beam_width_deg * std::sqrt(
            -std::log(cfg.forward.beam_gain_threshold) / (4.0 * std::log(2.0)));
        for (int b = beam_begin; b <= beam_end; ++b) {
            const double theta_center = cfg.system.scan_min_deg + cfg.system.scan_step_deg * b;
            const double angles[3] = {
                std::max(-89.999, theta_center - max_error_deg),
                theta_center,
                std::min(89.999, theta_center + max_error_deg)
            };
            for (double theta : angles) {
                double le = 0.0, ln = 0.0;
                stage3ComputeLook(theta, cfg.system.squint_side, le, ln);
                const double ranges[2] = {gr_min, gr_max};
                for (double gr : ranges) {
                    min_e = std::min(min_e, gr * le);
                    max_e = std::max(max_e, gr * le);
                    min_n = std::min(min_n, gr * ln);
                    max_n = std::max(max_n, gr * ln);
                }
            }
        }
        min_e -= cfg.canvas.margin_m;
        min_n -= cfg.canvas.margin_m;
        max_e += cfg.canvas.margin_m;
        max_n += cfg.canvas.margin_m;
        c.min_e = min_e;
        c.min_n = min_n;
        c.max_e = max_e;
        c.max_n = max_n;
        c.width = std::max(1, static_cast<int>(std::ceil((max_e - min_e) / c.resolution_m)));
        c.height = std::max(1, static_cast<int>(std::ceil((max_n - min_n) / c.resolution_m)));
        const long long count = static_cast<long long>(c.width) * c.height;
        if (count > cfg.canvas.max_cells) {
            std::ostringstream os;
            os << "mirror canvas requires " << count << " cells at " << c.resolution_m
               << " m, exceeding canvas.max_cells=" << cfg.canvas.max_cells
               << "; increase resolution_m or max_cells explicitly";
            err = os.str();
            return false;
        }
        c.cells.resize(static_cast<size_t>(count));
        for (int y = 0; y < c.height; ++y) {
            for (int x = 0; x < c.width; ++x) {
                CanvasCell &cell = c.cells[static_cast<size_t>(y) * c.width + x];
                cell.e = c.min_e + (static_cast<double>(x) + 0.5) * c.resolution_m;
                cell.n = c.min_n + (static_cast<double>(y) + 0.5) * c.resolution_m;
            }
        }
    }
    return true;
}

bool fillCanvasFromSar(SceneCanvas &canvas, const SarImage &src, const Stage3Config &cfg, std::string &err)
{
    const int sw = std::max(1, src.width);
    const int sh = std::max(1, src.height);
    const int off_x = cfg.tiling.random_start_offset
        ? static_cast<int>(hashUnit(cfg.tiling.random_seed, 1, 2, 3) * sw)
        : 0;
    const int off_y = cfg.tiling.random_start_offset
        ? static_cast<int>(hashUnit(cfg.tiling.random_seed, 4, 5, 6) * sh)
        : 0;
    canvas.source_offset_col = off_x;
    canvas.source_offset_row = off_y;
    const double source_center_col = cfg.sar_input.source_center_col >= 0.0
        ? cfg.sar_input.source_center_col - src.crop_col_start
        : 0.5 * static_cast<double>(sw - 1);
    const double source_center_row = cfg.sar_input.source_center_row >= 0.0
        ? cfg.sar_input.source_center_row - src.crop_row_start
        : 0.5 * static_cast<double>(sh - 1);
    if (!canvas.roi_enabled && cfg.tiling.mode != "mirror") {
        err = "only tiling.mode=mirror is supported for the expanded scene";
        return false;
    }
    Stage3SceneFrame frame;
    frame.anchor_e_m = canvas.anchor_e;
    frame.anchor_n_m = canvas.anchor_n;
    frame.range_axis_e = canvas.range_axis_e;
    frame.range_axis_n = canvas.range_axis_n;
    frame.azimuth_axis_e = canvas.azimuth_axis_e;
    frame.azimuth_axis_n = canvas.azimuth_axis_n;
    Stage3SourceGrid source_grid;
    source_grid.width = sw;
    source_grid.height = sh;
    source_grid.center_col = source_center_col;
    source_grid.center_row = source_center_row;
    source_grid.pixel_size_range_m = cfg.sar_input.pixel_size_range_m;
    source_grid.pixel_size_azimuth_m = cfg.sar_input.pixel_size_azimuth_m;
    source_grid.col_increases_with_range = cfg.sar_input.col_increases_with_range;
    source_grid.row_increases_with_azimuth = cfg.sar_input.row_increases_with_azimuth;
    source_grid.offset_col = off_x;
    source_grid.offset_row = off_y;
    for (CanvasCell &cell : canvas.cells) {
            const Stage3SourceIndex mapped = stage3MapGroundToSource(
                frame, source_grid, cell.e, cell.n, !canvas.roi_enabled);
            if (!mapped.valid) continue;
            const int sx = mapped.source_col;
            const int sy = mapped.source_row;
            const long long tx = mapped.tile_x;
            const long long ty = mapped.tile_y;
            const double jitter = cfg.tiling.tile_gain_jitter_db *
                (2.0 * hashUnit(cfg.tiling.random_seed, static_cast<uint64_t>(tx), static_cast<uint64_t>(ty), 9ULL) - 1.0);
            const double gain = std::pow(10.0, jitter / 20.0);
            const int64_t ge = static_cast<int64_t>(std::floor(cell.e / cfg.sar_input.pixel_size_range_m));
            const int64_t gn = static_cast<int64_t>(std::floor(cell.n / cfg.sar_input.pixel_size_azimuth_m));
            const double phase0 = 2.0 * kPi * hashUnit(
                cfg.tiling.random_seed, static_cast<uint64_t>(ge), static_cast<uint64_t>(gn), 17ULL);
            const double src_amp = src.value[static_cast<size_t>(sy) * sw + sx];
            cell.amplitude = static_cast<float>(src_amp * gain * cfg.extraction.amplitude_scale *
                                                cfg.scene.clutter_amplitude_scale);
            cell.phase = static_cast<float>(phase0);
            cell.valid = 1;
            cell.tile_x = static_cast<int>(std::max<long long>(std::numeric_limits<int>::min(),
                                                               std::min<long long>(std::numeric_limits<int>::max(), tx)));
            cell.tile_y = static_cast<int>(std::max<long long>(std::numeric_limits<int>::min(),
                                                               std::min<long long>(std::numeric_limits<int>::max(), ty)));
            cell.tile_id = static_cast<int>(mix64(static_cast<uint64_t>(tx) ^
                                                  (mix64(static_cast<uint64_t>(ty)) << 1)) & 0x7fffffffULL);
            cell.flip_mode = mapped.flip_mode;
            cell.source_col = sx;
            cell.source_row = sy;
    }
    return true;
}

void markFootprint(SceneCanvas &canvas, const Stage3Config &cfg)
{
    const double h = cfg.system.platform_height_m - cfg.scene.ground_z_m;
    const bool roi_enabled = canvas.roi_enabled;
    const int roi_beam_0 = canvas.roi_beam_id_1based > 0 ? canvas.roi_beam_id_1based - 1 : -1;
    for (CanvasCell &cell : canvas.cells) {
        const double gr = std::sqrt(cell.e * cell.e + cell.n * cell.n);
        const double r = std::sqrt(gr * gr + h * h);
        if (r < cfg.scene.range_min_m || r > cfg.scene.range_max_m) continue;
        const int configured_begin = std::max(0, cfg.forward.beam_start - 1);
        const int configured_end = std::min(cfg.system.beam_count - 1,
            cfg.forward.beam_start - 1 + std::max(1, cfg.forward.beam_count) - 1);
        const int b_begin = roi_enabled ? roi_beam_0 : configured_begin;
        const int b_end = roi_enabled ? roi_beam_0 : configured_end;
        for (int b = b_begin; b <= b_end; ++b) {
            const double theta = cfg.system.scan_min_deg + cfg.system.scan_step_deg * b;
            double le = 0.0, ln = 0.0;
            stage3ComputeLook(theta, cfg.system.squint_side, le, ln);
            const double dot = (gr > 0.0) ? (cell.e / gr * le + cell.n / gr * ln) : 0.0;
            const double err_deg = std::acos(std::max(-1.0, std::min(1.0, dot))) * 180.0 / kPi;
            const double beam_gain = std::exp(-4.0 * std::log(2.0) *
                (err_deg / std::max(1.0e-6, cfg.system.beam_width_deg)) *
                (err_deg / std::max(1.0e-6, cfg.system.beam_width_deg)));
            if (beam_gain >= cfg.forward.beam_gain_threshold) {
                cell.footprint = 1;
                break;
            }
        }
    }
}

std::vector<Stage3CudaCell> makeForwardCells(const SceneCanvas &canvas,
                                             const std::vector<BeamCellRef> &refs)
{
    std::vector<Stage3CudaCell> cells;
    cells.reserve(refs.size());
    for (const BeamCellRef &ref : refs) {
        if (ref.idx < 0 || static_cast<size_t>(ref.idx) >= canvas.cells.size()) continue;
        const CanvasCell &src = canvas.cells[static_cast<size_t>(ref.idx)];
        Stage3CudaCell dst;
        dst.e_m = src.e;
        dst.n_m = src.n;
        dst.amplitude = src.amplitude * ref.beam_gain;
        dst.phase_rad = src.phase;
        cells.push_back(dst);
    }
    return cells;
}

Stage3CudaForwardParams makeForwardParams(const Stage3Config &cfg,
                                           int period_id,
                                           int beam0,
                                           int pulse_start,
                                           int pulse_count)
{
    Stage3CudaForwardParams p;
    p.ddc_len = cfg.system.ddc_len;
    p.pulse_start = pulse_start;
    p.pulse_count = pulse_count;
    p.period_id = period_id;
    p.beam0 = beam0;
    p.beam_count = cfg.system.beam_count;
    p.pulse_num = cfg.system.pulse_num;
    p.prf_hz = cfg.system.prf_hz;
    p.fs_hz = cfg.system.fs_mhz * 1.0e6;
    p.sample_delay_sec = cfg.system.sample_delay_us * 1.0e-6;
    p.fc_hz = cfg.system.fc_ghz * 1.0e9;
    p.platform_height_m = cfg.system.platform_height_m;
    p.ground_z_m = cfg.scene.ground_z_m;
    p.platform_speed_mps = cfg.system.platform_speed_mps;
    p.d_chan_m = cfg.system.d_chan_m;
    p.carrier_phase_sign = cfg.forward.carrier_phase_sign < 0 ? -1 : 1;
    return p;
}

bool forwardStage3CpuBatch(const std::vector<Stage3CudaCell> &cells,
                           const Stage3CudaForwardParams &p,
                           std::vector<std::complex<float>> &channel1,
                           std::vector<std::complex<float>> &channel2,
                           double &elapsed_ms,
                           std::string &err)
{
    const auto start = std::chrono::steady_clock::now();
    if (p.ddc_len <= 0 || p.pulse_count <= 0 || p.prf_hz <= 0.0 ||
        p.fs_hz <= 0.0 || p.fc_hz <= 0.0) {
        err = "invalid Stage3 CPU forward parameters";
        return false;
    }
    const size_t sample_count = static_cast<size_t>(p.pulse_count) * static_cast<size_t>(p.ddc_len);
    channel1.assign(sample_count, std::complex<float>(0.0f, 0.0f));
    channel2.assign(sample_count, std::complex<float>(0.0f, 0.0f));
    const double lambda = kC / p.fc_hz;
    const double dz = p.ground_z_m - p.platform_height_m;
    const double phase_scale = static_cast<double>(p.carrier_phase_sign) * 2.0 * kPi / lambda;
    for (int pl = 0; pl < p.pulse_count; ++pl) {
        const int pulse_id = p.pulse_start + pl;
        const double sequence_pulse =
            static_cast<double>(p.period_id) * p.beam_count * p.pulse_num +
            static_cast<double>(p.beam0) * p.pulse_num + pulse_id;
        const double time_sec = sequence_pulse / p.prf_hz;
        const double platform_n = p.platform_speed_mps * time_sec;
        const size_t pulse_off = static_cast<size_t>(pl) * p.ddc_len;
        for (const Stage3CudaCell &cell : cells) {
            if (!(cell.amplitude > 0.0f) || !std::isfinite(cell.amplitude) ||
                !std::isfinite(cell.e_m) || !std::isfinite(cell.n_m) ||
                !std::isfinite(cell.phase_rad)) continue;
            const double de = cell.e_m;
            const double dn_tx = cell.n_m - platform_n;
            const double tx_path = std::sqrt(de * de + dn_tx * dn_tx + dz * dz);
            const long long sample = static_cast<long long>(std::floor(
                (2.0 * tx_path / kC - p.sample_delay_sec) * p.fs_hz + 0.5));
            if (sample < 0 || sample >= p.ddc_len) continue;
            const double dn_rx1 = cell.n_m - (platform_n - 0.5 * p.d_chan_m);
            const double dn_rx2 = cell.n_m - (platform_n + 0.5 * p.d_chan_m);
            const double path1 = tx_path + std::sqrt(de * de + dn_rx1 * dn_rx1 + dz * dz);
            const double path2 = tx_path + std::sqrt(de * de + dn_rx2 * dn_rx2 + dz * dz);
            const double spreading = 1.0 / std::max(1.0, tx_path / 1000.0);
            const double amp = static_cast<double>(cell.amplitude) * spreading;
            const double phi1 = std::remainder(phase_scale * path1 + cell.phase_rad, 2.0 * kPi);
            const double phi2 = std::remainder(phase_scale * path2 + cell.phase_rad, 2.0 * kPi);
            const size_t idx = pulse_off + static_cast<size_t>(sample);
            channel1[idx] += std::complex<float>(static_cast<float>(amp * std::cos(phi1)),
                                                 static_cast<float>(amp * std::sin(phi1)));
            channel2[idx] += std::complex<float>(static_cast<float>(amp * std::cos(phi2)),
                                                 static_cast<float>(amp * std::sin(phi2)));
        }
    }
    elapsed_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    return true;
}

struct NumericComparison {
    double max_abs = 0.0;
    double rmse = 0.0;
    double relative_l2 = 0.0;
    double max_phase_error_rad = 0.0;
    size_t sample_count = 0;
};

NumericComparison compareComplexVectors(const std::vector<std::complex<float>> &reference,
                                        const std::vector<std::complex<float>> &test)
{
    NumericComparison out;
    const size_t n = std::min(reference.size(), test.size());
    double err2 = 0.0;
    double ref2 = 0.0;
    for (size_t i = 0; i < n; ++i) {
        const std::complex<double> a(reference[i].real(), reference[i].imag());
        const std::complex<double> b(test[i].real(), test[i].imag());
        const double e = std::abs(a - b);
        out.max_abs = std::max(out.max_abs, e);
        err2 += e * e;
        ref2 += std::norm(a);
        if (std::abs(a) > 1.0e-8 && std::abs(b) > 1.0e-8) {
            out.max_phase_error_rad = std::max(out.max_phase_error_rad,
                std::abs(std::arg(a * std::conj(b))));
        }
    }
    out.sample_count = n;
    if (n > 0) out.rmse = std::sqrt(err2 / static_cast<double>(n));
    out.relative_l2 = std::sqrt(err2 / std::max(1.0e-30, ref2));
    return out;
}

int nextPowerOfTwo(int n)
{
    int v = 1;
    while (v < n && v <= std::numeric_limits<int>::max() / 2) v <<= 1;
    return v;
}

bool applyLfmWaveform(const Stage3Config &cfg,
                      std::vector<std::complex<float>> &channel1,
                      std::vector<std::complex<float>> &channel2,
                      int pulse_count,
                      double &elapsed_ms,
                      std::string &err)
{
    const auto start = std::chrono::steady_clock::now();
    const int raw_len = cfg.system.ddc_len;
    const double fs_hz = cfg.system.fs_mhz * 1.0e6;
    const double tr_sec = cfg.system.pulse_width_us * 1.0e-6;
    const double br_hz = cfg.system.bandwidth_mhz * 1.0e6;
    const int chirp_len = static_cast<int>(std::floor(tr_sec * fs_hz + 0.5));
    const bool center_reference =
        cfg.forward.lfm_time_reference != "legacy_start" &&
        cfg.forward.lfm_time_reference != "start";
    const int extraction_shift = center_reference ? chirp_len / 2 : 0;
    const int nfft = nextPowerOfTwo(raw_len + chirp_len - 1);
    const int rows = 2 * pulse_count;
    if (raw_len <= 0 || pulse_count <= 0 || chirp_len <= 0 || nfft < raw_len ||
        channel1.size() != static_cast<size_t>(pulse_count) * raw_len ||
        channel2.size() != channel1.size()) {
        err = "invalid LFM convolution dimensions";
        return false;
    }
    fftw_complex *chirp = static_cast<fftw_complex *>(fftw_malloc(sizeof(fftw_complex) * nfft));
    fftw_complex *buf = static_cast<fftw_complex *>(
        fftw_malloc(sizeof(fftw_complex) * static_cast<size_t>(rows) * nfft));
    if (chirp == nullptr || buf == nullptr) {
        if (chirp) fftw_free(chirp);
        if (buf) fftw_free(buf);
        err = "FFTW allocation failed for Stage3 LFM convolution";
        return false;
    }
    std::fill(reinterpret_cast<std::complex<double> *>(chirp),
              reinterpret_cast<std::complex<double> *>(chirp) + nfft,
              std::complex<double>(0.0, 0.0));
    std::fill(reinterpret_cast<std::complex<double> *>(buf),
              reinterpret_cast<std::complex<double> *>(buf) + static_cast<size_t>(rows) * nfft,
              std::complex<double>(0.0, 0.0));
    const double kr = br_hz / tr_sec;
    for (int n = 0; n < chirp_len; ++n) {
        const double t = center_reference
            ? static_cast<double>(n - extraction_shift) / fs_hz
            : static_cast<double>(n) / fs_hz;
        const double phase = static_cast<double>(cfg.forward.chirp_phase_sign < 0 ? -1 : 1) *
                             kPi * kr * t * t;
        reinterpret_cast<std::complex<double> *>(chirp)[n] =
            std::complex<double>(std::cos(phase), std::sin(phase));
    }
    for (int p = 0; p < pulse_count; ++p) {
        std::complex<double> *r1 = reinterpret_cast<std::complex<double> *>(buf) +
                                   static_cast<size_t>(2 * p) * nfft;
        std::complex<double> *r2 = r1 + nfft;
        for (int n = 0; n < raw_len; ++n) {
            const size_t idx = static_cast<size_t>(p) * raw_len + n;
            r1[n] = std::complex<double>(channel1[idx].real(), channel1[idx].imag());
            r2[n] = std::complex<double>(channel2[idx].real(), channel2[idx].imag());
        }
    }
    fftw_plan chirp_plan = fftw_plan_dft_1d(nfft, chirp, chirp, FFTW_FORWARD, FFTW_ESTIMATE);
    int dims[1] = {nfft};
    fftw_plan fwd = fftw_plan_many_dft(1, dims, rows, buf, dims, 1, nfft,
                                        buf, dims, 1, nfft, FFTW_FORWARD, FFTW_ESTIMATE);
    fftw_plan inv = fftw_plan_many_dft(1, dims, rows, buf, dims, 1, nfft,
                                        buf, dims, 1, nfft, FFTW_BACKWARD, FFTW_ESTIMATE);
    if (chirp_plan == nullptr || fwd == nullptr || inv == nullptr) {
        if (chirp_plan) fftw_destroy_plan(chirp_plan);
        if (fwd) fftw_destroy_plan(fwd);
        if (inv) fftw_destroy_plan(inv);
        fftw_free(chirp);
        fftw_free(buf);
        err = "FFTW plan creation failed for Stage3 LFM convolution";
        return false;
    }
    fftw_execute(chirp_plan);
    fftw_execute(fwd);
    const std::complex<double> *h = reinterpret_cast<const std::complex<double> *>(chirp);
    std::complex<double> *data = reinterpret_cast<std::complex<double> *>(buf);
    for (int row = 0; row < rows; ++row) {
        for (int f = 0; f < nfft; ++f) {
            data[static_cast<size_t>(row) * nfft + f] *= h[f];
        }
    }
    fftw_execute(inv);
    const double scale = 1.0 / nfft;
    for (int p = 0; p < pulse_count; ++p) {
        const std::complex<double> *r1 = data + static_cast<size_t>(2 * p) * nfft;
        const std::complex<double> *r2 = r1 + nfft;
        for (int n = 0; n < raw_len; ++n) {
            const size_t idx = static_cast<size_t>(p) * raw_len + n;
            const int source = n + extraction_shift;
            channel1[idx] = std::complex<float>(static_cast<float>(r1[source].real() * scale),
                                                static_cast<float>(r1[source].imag() * scale));
            channel2[idx] = std::complex<float>(static_cast<float>(r2[source].real() * scale),
                                                static_cast<float>(r2[source].imag() * scale));
        }
    }
    fftw_destroy_plan(chirp_plan);
    fftw_destroy_plan(fwd);
    fftw_destroy_plan(inv);
    fftw_free(chirp);
    fftw_free(buf);
    elapsed_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    return true;
}

void storeCh(std::vector<uint8_t> &packet,
             int n,
             int ch,
             std::size_t channel_count,
             const std::string &iq_type,
             const std::complex<float> &v)
{
    const std::size_t iq_bytes = gmti::new_protocol::bytesPerIq(iq_type);
    const size_t off = gmti::new_protocol::kHeaderBytes +
                       static_cast<size_t>(n) * gmti::new_protocol::sampleBytes(channel_count, iq_type) +
                       gmti::new_protocol::channelOffset(static_cast<size_t>(ch), iq_type);
    gmti::new_protocol::storeIqFromFloat(&packet[off], iq_type, v.real());
    gmti::new_protocol::storeIqFromFloat(&packet[off + iq_bytes], iq_type, v.imag());
}

std::complex<float> loadCh(const std::vector<uint8_t> &packet,
                           int n,
                           int ch,
                           std::size_t channel_count,
                           const std::string &iq_type)
{
    const std::size_t iq_bytes = gmti::new_protocol::bytesPerIq(iq_type);
    const size_t off = gmti::new_protocol::kHeaderBytes +
                       static_cast<size_t>(n) * gmti::new_protocol::sampleBytes(channel_count, iq_type) +
                       gmti::new_protocol::channelOffset(static_cast<size_t>(ch), iq_type);
    return std::complex<float>(
        gmti::new_protocol::loadIqAsFloat(&packet[off], iq_type),
        gmti::new_protocol::loadIqAsFloat(&packet[off + iq_bytes], iq_type));
}

bool configureStage3Target(const Stage3Config &cfg,
                           const std::string &output_path,
                           Stage3TargetContext &ctx,
                           std::string &err)
{
    if (!cfg.targets.enabled) return true;
    std::ifstream target_file(cfg.targets.target_config_path.c_str());
    if (!target_file) {
        err = "Stage3 target config does not exist: " + cfg.targets.target_config_path;
        return false;
    }

    ctx.radar.input_data_file = output_path;
    ctx.radar.output_data_file = output_path;
    ctx.radar.pulse_len = cfg.system.ddc_len;
    ctx.radar.pulse_num = cfg.system.pulse_num;
    ctx.radar.beam_count = cfg.system.beam_count;
    ctx.radar.iq_data_type = cfg.system.iq_data_type;
    ctx.radar.new_protocol_channel_count = cfg.system.new_protocol_channel_count;
    ctx.radar.new_protocol_read_channel_1 = cfg.system.new_protocol_read_channel_1;
    ctx.radar.new_protocol_read_channel_2 = cfg.system.new_protocol_read_channel_2;
    ctx.radar.range_fft_len = cfg.system.fft_len;
    ctx.radar.range_crop_start = cfg.system.pc_crop_start;
    ctx.radar.range_crop_len = cfg.system.pc_crop_len;
    ctx.radar.scan_min_deg = cfg.system.scan_min_deg;
    ctx.radar.scan_step_deg = cfg.system.scan_step_deg;
    ctx.radar.beam_width_deg = cfg.system.beam_width_deg;
    ctx.radar.fc_hz = cfg.system.fc_ghz * 1.0e9;
    ctx.radar.br_hz = cfg.system.bandwidth_mhz * 1.0e6;
    ctx.radar.fs_hz = cfg.system.fs_mhz * 1.0e6;
    ctx.radar.tr_sec = cfg.system.pulse_width_us * 1.0e-6;
    ctx.radar.prf_hz = cfg.system.prf_hz;
    ctx.radar.sample_delay_sec = cfg.system.sample_delay_us * 1.0e-6;
    ctx.radar.d_chan_m = cfg.system.d_chan_m;
    ctx.radar.motion_doppler_axis_sign = -1;

    // Seed the shared loader with Stage3 geometry.  The system values below
    // are then made authoritative and p0/v are recomputed, so a target file
    // can describe the target without silently changing the SAR platform.
    ctx.global.coordinate_mode = "project_local";
    ctx.global.platform_mode = "ideal_platform";
    ctx.global.platform_speed_mps = cfg.system.platform_speed_mps;
    ctx.global.platform_height_m = cfg.system.platform_height_m;
    ctx.global.platform_origin_lat_deg = cfg.system.origin_lat_deg;
    ctx.global.platform_origin_lon_deg = cfg.system.origin_lon_deg;
    ctx.global.platform_origin_alt_m = cfg.system.platform_height_m;
    ctx.global.projection_ref_lon_deg = cfg.system.projection_ref_lon_deg;
    ctx.global.geometry.geometry_config_name = "algorithm_axis_x_north";
    ctx.global.geometry.local_x_axis = "north";
    ctx.global.geometry.local_y_axis = "east";
    ctx.global.geometry.platform_heading_source = "velocity";
    ctx.global.geometry.beam_angle_reference = "algorithm";
    ctx.global.geometry.beam_zero_direction = "algorithm";
    ctx.global.geometry.beam_positive_direction = "algorithm";
    ctx.global.geometry.range_geometry = "algorithm";
    ctx.global.geometry.use_ground_range_for_position = true;
    ctx.global.geometry.platform_origin_lat_deg = cfg.system.origin_lat_deg;
    ctx.global.geometry.platform_origin_lon_deg = cfg.system.origin_lon_deg;
    ctx.global.geometry.platform_origin_alt_m = cfg.system.platform_height_m;
    ctx.global.geometry.projection_ref_lon_deg = cfg.system.projection_ref_lon_deg;
    ctx.global.geometry.squint_side = cfg.system.squint_side;

    if (!gmti::target_injection::loadTargetConfigs(
            cfg.targets.target_config_path, ctx.radar, ctx.global, ctx.targets, err)) {
        return false;
    }
    ctx.global.coordinate_mode = "project_local";
    ctx.global.platform_mode = "ideal_platform";
    ctx.global.platform_speed_mps = cfg.system.platform_speed_mps;
    ctx.global.platform_height_m = cfg.system.platform_height_m;
    ctx.global.platform_origin_lat_deg = cfg.system.origin_lat_deg;
    ctx.global.platform_origin_lon_deg = cfg.system.origin_lon_deg;
    ctx.global.platform_origin_alt_m = cfg.system.platform_height_m;
    ctx.global.projection_ref_lon_deg = cfg.system.projection_ref_lon_deg;
    ctx.global.amplitude_mode = cfg.targets.amplitude_mode;
    ctx.global.target_snr_db = cfg.targets.target_snr_db;
    ctx.global.lfm_time_reference = cfg.forward.lfm_time_reference;
    ctx.global.chirp_phase_sign = cfg.forward.chirp_phase_sign;
    ctx.global.carrier_phase_sign = cfg.forward.carrier_phase_sign;
    ctx.global.channel_phase_mode = "ctdr_exact";
    ctx.global.geometry.geometry_config_name = "algorithm_axis_x_north";
    ctx.global.geometry.local_x_axis = "north";
    ctx.global.geometry.local_y_axis = "east";
    ctx.global.geometry.platform_heading_source = "velocity";
    ctx.global.geometry.beam_angle_reference = "algorithm";
    ctx.global.geometry.beam_zero_direction = "algorithm";
    ctx.global.geometry.beam_positive_direction = "algorithm";
    ctx.global.geometry.range_geometry = "algorithm";
    ctx.global.geometry.use_ground_range_for_position = true;
    ctx.global.geometry.platform_origin_lat_deg = cfg.system.origin_lat_deg;
    ctx.global.geometry.platform_origin_lon_deg = cfg.system.origin_lon_deg;
    ctx.global.geometry.platform_origin_alt_m = cfg.system.platform_height_m;
    ctx.global.geometry.projection_ref_lon_deg = cfg.system.projection_ref_lon_deg;
    ctx.global.geometry.squint_side = cfg.system.squint_side;
    if (ctx.targets.empty()) {
        err = "Stage3 targets.enabled=true but targets array is empty";
        return false;
    }

    const int ref_pulse = cfg.forward.pulse_start +
        std::max(0, std::min(cfg.system.pulse_num - cfg.forward.pulse_start,
                            cfg.forward.pulse_count) - 1) / 2;
    bool any_enabled = false;
    for (gmti::target_injection::TargetConfig &target : ctx.targets) {
        if (!std::isfinite(target.target_snr_db)) {
            target.target_snr_db = cfg.targets.target_snr_db;
        }
        gmti::target_injection::resolveTargetInitialState(ctx.radar, ctx.global, target);
        any_enabled = any_enabled || target.enabled;

        // Anchor each target at its nearest emitted beam, not at the first
        // target/first beam.  This makes all target truth rows independently
        // meaningful for beam-offset and multi-target evaluation.
        const int nearest_beam0 = static_cast<int>(std::llround(
            (target.azimuth_deg - cfg.system.scan_min_deg) /
            cfg.system.scan_step_deg));
        const int emitted_first0 = cfg.forward.beam_start - 1;
        const int emitted_last0 = emitted_first0 + cfg.forward.beam_count - 1;
        const int ref_beam0 = std::max(emitted_first0,
            std::min(emitted_last0, nearest_beam0));
        const gmti::target_injection::GeometrySample ref_geom =
            gmti::target_injection::evaluateGeometry(
                ctx.radar, ctx.global, target,
                cfg.forward.period_start, ref_beam0, ref_pulse);
        target.has_ref_geometry = true;
        target.ref_beam_id = ref_beam0;
        target.ref_pulse_idx = ref_pulse;
        target.ref_time_s = ref_geom.time_sec;
        target.ref_platform = ref_geom.platform.position;
        target.ref_target = ref_geom.target.position;
        target.ref_range_m = ref_geom.range_m;
        target.ref_range_sample_float = ref_geom.range_sample_float;
        target.ref_range_sample_int = ref_geom.range_sample_int;
        target.echo_delay_sample_center_used = ref_geom.range_sample_float;
        const gmti::sim_geometry::ENUVelocity target_velocity_en =
            gmti::sim_geometry::localVelocityToEnu(
                gmti::sim_geometry::LocalVelocity(
                    target.v.x, target.v.y, target.v.z),
                ctx.global.geometry);
        target.target_ve_mps = target_velocity_en.ve;
        target.target_vn_mps = target_velocity_en.vn;
        target.target_vr_self_mps =
            gmti::target_injection::dot(target.v, ref_geom.los_unit);
        const double target_speed_sq =
            gmti::target_injection::dot(target.v, target.v);
        target.target_vt_self_mps = std::sqrt(std::max(
            0.0, target_speed_sq -
            target.target_vr_self_mps * target.target_vr_self_mps));
        target.override_speed_mps = std::sqrt(target_speed_sq);
        target.af_motion_truth_hz =
            static_cast<double>(ctx.radar.motion_doppler_axis_sign) *
            2.0 * target.target_vr_self_mps /
            (kC / ctx.radar.fc_hz);
    }
    if (!any_enabled) {
        err = "Stage3 targets.enabled=true but every configured target is disabled";
        return false;
    }

    ctx.truth.setCaseId(cfg.case_id);
    if (!ctx.truth.open(cfg.output_dir + "/truth", err)) return false;
    ctx.truth_open = true;
    return true;
}

void updateStage3TargetPacketStats(const Stage3Config &cfg,
                                   const std::vector<uint8_t> &packet,
                                   std::size_t channel_count,
                                   const std::string &iq_type,
                                   Stage3TargetContext &ctx)
{
    ++ctx.stats.packets_written;
    for (int n = 0; n < cfg.system.ddc_len; ++n) {
        const std::complex<float> ch1 = loadCh(
            packet, n, cfg.system.new_protocol_read_channel_1, channel_count, iq_type);
        const std::complex<float> ch2 = loadCh(
            packet, n, cfg.system.new_protocol_read_channel_2, channel_count, iq_type);
        const float values[4] = {ch1.real(), ch1.imag(), ch2.real(), ch2.imag()};
        for (float value : values) {
            ctx.stats.has_nan = ctx.stats.has_nan || std::isnan(value);
            ctx.stats.has_inf = ctx.stats.has_inf || std::isinf(value);
            ctx.stats.max_amplitude = std::max(
                ctx.stats.max_amplitude, std::abs(static_cast<double>(value)));
        }
    }
}

bool finalizeStage3Target(const Stage3Config &cfg,
                          const std::string &output_path,
                          Stage3TargetContext &ctx,
                          std::string &err)
{
    if (!cfg.targets.enabled) return true;
    if (ctx.truth_open) {
        ctx.truth.writeSummary();
        ctx.truth.close();
        ctx.truth_open = false;
    }
    gmti::target_injection::InjectionConfig report_cfg;
    report_cfg.radar = ctx.radar;
    report_cfg.radar.input_data_file = output_path;
    report_cfg.radar.output_data_file = output_path;
    report_cfg.global = ctx.global;
    // The legacy Markdown helper has a single-target schema; detailed truth
    // for every target is authoritative in truth_targets_by_beam.csv.
    report_cfg.target = ctx.targets.front();
    report_cfg.run.input_config = cfg.output_dir + "/configs/expanded_config.json";
    report_cfg.run.target_config = cfg.targets.target_config_path;
    report_cfg.run.input_data_dir = cfg.output_dir + "/data";
    report_cfg.run.output_dir = cfg.output_dir;
    report_cfg.run.period_start = cfg.forward.period_start;
    report_cfg.run.period_count = cfg.forward.period_count;
    report_cfg.run.beam_start = cfg.forward.beam_start - 1;
    report_cfg.run.beam_count = cfg.forward.beam_count;
    report_cfg.run.pulse_start = cfg.forward.pulse_start;
    report_cfg.run.pulse_count = cfg.forward.pulse_count;
    const std::string notes =
        "Stage3 在内存中将 " + std::to_string(ctx.targets.size()) +
        " 个目标的 raw-LFM 回波在线性复数域叠加到 SAR 背景；"
        "完整逐目标 truth 见 truth_targets_by_beam.csv；"
        "CPU/CUDA comparison 只比较共享目标注入之前的背景几何正演。";
    return gmti::target_injection::writeTargetInjectionReport(
        cfg.output_dir + "/reports/stage3_target_injection_report.md",
        report_cfg, ctx.stats, notes, err);
}

std::string makeAbsolutePath(const std::string &path)
{
    if (path.empty() || path[0] == '/') {
        return path;
    }
    char cwd[4096] = {0};
    if (!::getcwd(cwd, sizeof(cwd))) {
        return path;
    }
    return std::string(cwd) + "/" + path;
}

void writeHeader(std::vector<uint8_t> &packet,
                 const Stage3Config &cfg,
                 int prt_counter,
                 double time_sec,
                 double theta_deg)
{
    if (packet.size() < gmti::new_protocol::kHeaderBytes) return;
    uint8_t *h = packet.data();
    gmti::new_protocol::storeU64LE(h + gmti::new_protocol::kOffMagicHead, 0x5A5A5A5A5A5A5A5AULL);
    h[gmti::new_protocol::kOffVersion] = 5;
    gmti::new_protocol::storeU32LE(h + gmti::new_protocol::kOffPrtLen, static_cast<uint32_t>(packet.size()));
    gmti::new_protocol::storeF32LE(h + gmti::new_protocol::kOffUtc, static_cast<float>(time_sec));
    gmti::new_protocol::storeU32LE(h + gmti::new_protocol::kOffPrtCounter, static_cast<uint32_t>(prt_counter));
    h[88] = 0x02;
    h[90] = 0x40;
    h[92] = 0x0B;
    h[93] = 0x01;
    gmti::new_protocol::storeU32LE(h + 96, static_cast<uint32_t>(std::floor(time_sec * 1000.0 + 0.5)));
    const double earth_radius_m = 6378137.0;
    const double platform_n = cfg.system.platform_speed_mps * time_sec;
    const double lat_deg = cfg.system.origin_lat_deg +
        platform_n / earth_radius_m * 180.0 / kPi;
    gmti::new_protocol::storeF64LE(h + gmti::new_protocol::kOffLatDeg, lat_deg);
    gmti::new_protocol::storeF64LE(h + gmti::new_protocol::kOffLonDeg, cfg.system.origin_lon_deg);
    gmti::new_protocol::storeF64LE(h + gmti::new_protocol::kOffHeightM, cfg.system.platform_height_m);
    gmti::new_protocol::storeF32LE(h + gmti::new_protocol::kOffVnMps, static_cast<float>(cfg.system.platform_speed_mps));
    gmti::new_protocol::storeF32LE(h + gmti::new_protocol::kOffVeMps, 0.0f);
    gmti::new_protocol::storeF32LE(h + gmti::new_protocol::kOffVdMps, 0.0f);
    gmti::new_protocol::storeF32LE(h + gmti::new_protocol::kOffSpeedMps, static_cast<float>(cfg.system.platform_speed_mps));
    h[gmti::new_protocol::kOffPrtLowByte] = static_cast<uint8_t>(prt_counter & 0xff);
    gmti::new_protocol::storeI16LE(h + gmti::new_protocol::kOffThetaDegX100,
                                   gmti::new_protocol::satI16FromDouble(
                                       theta_deg * 100.0));
    gmti::new_protocol::storeU64LE(h + gmti::new_protocol::kOffMagicTail, 0x5B5B5B5B5B5B5B5BULL);
}

std::vector<std::vector<BeamCellRef>> buildBeamRefs(const SceneCanvas &canvas, const Stage3Config &cfg)
{
    std::vector<std::vector<BeamCellRef>> refs(static_cast<size_t>(cfg.system.beam_count));
    const bool roi_enabled = canvas.roi_enabled;
    const int roi_beam_0 = canvas.roi_beam_id_1based > 0 ? canvas.roi_beam_id_1based - 1 : -1;
    const int configured_begin = std::max(0, cfg.forward.beam_start - 1);
    const int configured_end = std::min(cfg.system.beam_count - 1,
        cfg.forward.beam_start - 1 + std::max(1, cfg.forward.beam_count) - 1);
    for (int b = configured_begin; b <= configured_end; ++b) {
        if (roi_enabled && b != roi_beam_0) continue;
        const double theta = cfg.system.scan_min_deg + cfg.system.scan_step_deg * b;
        double le = 0.0, ln = 0.0;
        stage3ComputeLook(theta, cfg.system.squint_side, le, ln);
        for (size_t i = 0; i < canvas.cells.size(); ++i) {
            const CanvasCell &cell = canvas.cells[i];
            if (!cell.valid || !cell.footprint || cell.amplitude <= 0.0f) continue;
            const double gr = std::sqrt(cell.e * cell.e + cell.n * cell.n);
            if (gr <= 0.0) continue;
            const double dot = std::max(-1.0, std::min(1.0, cell.e / gr * le + cell.n / gr * ln));
            const double err = std::acos(dot) * 180.0 / kPi;
            const double ratio = err / std::max(1.0e-6, cfg.system.beam_width_deg);
            const double beam_gain = std::exp(-4.0 * std::log(2.0) * ratio * ratio);
            if (beam_gain >= cfg.forward.beam_gain_threshold) {
                BeamCellRef ref;
                ref.idx = static_cast<int>(i);
                ref.beam_gain = static_cast<float>(beam_gain);
                refs[static_cast<size_t>(b)].push_back(ref);
            }
        }
    }
    return refs;
}

void writeCanvasTruth(const SceneCanvas &canvas, const SarImage &src, const Stage3Config &cfg, const std::string &output_dir)
{
    size_t valid_count = 0;
    for (const CanvasCell &c : canvas.cells) if (c.valid) ++valid_count;
    {
        std::ofstream os((output_dir + "/truth/sar_surface_metadata.json").c_str());
        os << "{\n"
           << "  \"case_id\": \"" << cfg.case_id << "\",\n"
           << "  \"canvas_width\": " << canvas.width << ",\n"
           << "  \"canvas_height\": " << canvas.height << ",\n"
           << "  \"resolution_m\": " << canvas.resolution_m << ",\n"
           << "  \"source_width\": " << src.width << ",\n"
           << "  \"source_height\": " << src.height << ",\n"
           << "  \"source_original_width\": " << src.original_width << ",\n"
           << "  \"source_original_height\": " << src.original_height << ",\n"
           << "  \"source_valid_crop_enabled\": "
           << (cfg.sar_input.valid_crop_enabled ? "true" : "false") << ",\n"
           << "  \"source_crop_col_start\": " << src.crop_col_start << ",\n"
           << "  \"source_crop_row_start\": " << src.crop_row_start << ",\n"
           << "  \"source_pixel_size_range_m\": " << cfg.sar_input.pixel_size_range_m << ",\n"
           << "  \"source_pixel_size_azimuth_m\": " << cfg.sar_input.pixel_size_azimuth_m << ",\n"
           << "  \"source_physical_range_extent_m\": " << src.width * cfg.sar_input.pixel_size_range_m << ",\n"
           << "  \"source_physical_azimuth_extent_m\": " << src.height * cfg.sar_input.pixel_size_azimuth_m << ",\n"
           << "  \"tiling_mode\": \"" << cfg.tiling.mode << "\",\n"
           << "  \"mirror_boundary_policy\": \"reflect_101_period_2N_minus_2\",\n"
           << "  \"phase_policy\": \"" << cfg.tiling.phase_policy << "\",\n"
           << "  \"random_seed\": " << cfg.tiling.random_seed << ",\n"
           << "  \"source_offset_col\": " << canvas.source_offset_col << ",\n"
           << "  \"source_offset_row\": " << canvas.source_offset_row << ",\n"
           << "  \"anchor_e_m\": " << canvas.anchor_e << ",\n"
           << "  \"anchor_n_m\": " << canvas.anchor_n << ",\n"
           << "  \"range_axis_e\": " << canvas.range_axis_e << ",\n"
           << "  \"range_axis_n\": " << canvas.range_axis_n << ",\n"
           << "  \"azimuth_axis_e\": " << canvas.azimuth_axis_e << ",\n"
           << "  \"azimuth_axis_n\": " << canvas.azimuth_axis_n << ",\n"
           << "  \"valid_cells\": " << valid_count << ",\n"
           << "  \"roi_enabled\": " << (canvas.roi_enabled ? "true" : "false") << ",\n"
           << "  \"roi_beam_id_1based\": " << canvas.roi_beam_id_1based << "\n"
           << "}\n";
    }
    {
        std::ofstream os((output_dir + "/truth/sar_tile_placement.csv").c_str());
        os << "tile_x,tile_y,flip_mode,gain_jitter_db,phase_policy\n";
        std::map<std::pair<int, int>, int> tiles;
        for (const CanvasCell &c : canvas.cells) {
            if (c.valid) tiles[std::make_pair(c.tile_x, c.tile_y)] = c.flip_mode;
        }
        for (const auto &entry : tiles) {
            const int tx = entry.first.first;
            const int ty = entry.first.second;
            const double jitter = cfg.tiling.tile_gain_jitter_db *
                (2.0 * hashUnit(cfg.tiling.random_seed, static_cast<uint64_t>(tx),
                                static_cast<uint64_t>(ty), 9) - 1.0);
            os << tx << "," << ty << "," << entry.second << "," << jitter
               << "," << cfg.tiling.phase_policy << "\n";
        }
    }
    {
        std::ofstream os((output_dir + "/truth/sar_surface_tile_stats.csv").c_str());
        os << "tile_x,tile_y,count,mean_amplitude\n";
        std::map<std::pair<int, int>, std::pair<size_t, double>> stats;
        for (const CanvasCell &c : canvas.cells) {
            if (!c.valid) continue;
            auto &s = stats[std::make_pair(c.tile_x, c.tile_y)];
            ++s.first;
            s.second += c.amplitude;
        }
        for (const auto &entry : stats) {
            os << entry.first.first << "," << entry.first.second << ","
               << entry.second.first << ","
               << (entry.second.second / std::max<size_t>(1, entry.second.first)) << "\n";
        }
    }
    {
        std::ofstream os((output_dir + "/truth/sar_scatterers_truth.csv").c_str());
        os << "scatterer_id,grid_row,grid_col,e_m,n_m,source_row,source_col,tile_x,tile_y,flip_mode,amplitude,phase_rad,valid,footprint\n";
        const size_t max_rows = static_cast<size_t>(std::max(0, cfg.forward.truth_max_rows));
        const size_t stride = (max_rows > 0 && canvas.cells.size() > max_rows)
            ? (canvas.cells.size() + max_rows - 1) / max_rows : 1;
        size_t written = 0;
        for (size_t i = 0; i < canvas.cells.size(); i += stride) {
            const CanvasCell &c = canvas.cells[i];
            if (!c.valid) continue;
            os << i << "," << (i / static_cast<size_t>(canvas.width)) << ","
               << (i % static_cast<size_t>(canvas.width)) << ","
               << std::setprecision(12) << c.e << "," << c.n << ","
               << c.source_row << "," << c.source_col << ","
               << c.tile_x << "," << c.tile_y << "," << c.flip_mode << ","
               << c.amplitude << "," << c.phase << ","
               << static_cast<int>(c.valid) << "," << static_cast<int>(c.footprint) << "\n";
            ++written;
        }
        std::ofstream meta((output_dir + "/truth/sar_scatterers_truth_metadata.json").c_str());
        meta << "{\n  \"total_cells\": " << canvas.cells.size()
             << ",\n  \"valid_cells\": " << valid_count
             << ",\n  \"written_rows\": " << written
             << ",\n  \"sampling_stride\": " << stride
             << ",\n  \"truncated\": " << (stride > 1 ? "true" : "false") << "\n}\n";
    }
}

void writeDebugPreviews(const SceneCanvas &canvas,
                        const SarImage &src,
                        const Stage3Config &cfg,
                        const std::string &output_dir)
{
    writePgmPreview(output_dir + "/figures/sar_source_normalized.pgm",
                    src.width, src.height, src.value);
    const int out_w = std::max(1, std::min(2048, canvas.width));
    const int out_h = std::max(1, std::min(2048, canvas.height));
    std::vector<float> preview(static_cast<size_t>(out_w) * out_h, 0.0f);
    for (int y = 0; y < out_h; ++y) {
        const int sy = std::min(canvas.height - 1,
            static_cast<int>((static_cast<long long>(y) * canvas.height) / out_h));
        for (int x = 0; x < out_w; ++x) {
            const int sx = std::min(canvas.width - 1,
                static_cast<int>((static_cast<long long>(x) * canvas.width) / out_w));
            preview[static_cast<size_t>(y) * out_w + x] =
                canvas.cells[static_cast<size_t>(sy) * canvas.width + sx].amplitude;
        }
    }
    writePgmPreview(output_dir + "/figures/sar_scene_amplitude.pgm", out_w, out_h, preview);

    {
        const int band = std::max(1, std::min(8, std::min(src.width, src.height)));
        auto edgeMean = [&](char edge) {
            double sum = 0.0;
            size_t count = 0;
            for (int y = 0; y < src.height; ++y) {
                for (int x = 0; x < src.width; ++x) {
                    const bool use = (edge == 't' && y < band) ||
                                     (edge == 'b' && y >= src.height - band) ||
                                     (edge == 'l' && x < band) ||
                                     (edge == 'r' && x >= src.width - band);
                    if (use) {
                        sum += src.value[static_cast<size_t>(y) * src.width + x];
                        ++count;
                    }
                }
            }
            return count ? sum / static_cast<double>(count) : 0.0;
        };
        double global_sum = 0.0;
        for (float value : src.value) global_sum += value;
        const double global_mean = src.value.empty() ? 0.0 :
            global_sum / static_cast<double>(src.value.size());
        std::ofstream edge((output_dir + "/debug/source_edge_metrics.csv").c_str());
        edge << "edge,band_pixels,mean_amplitude,ratio_to_global_mean\n";
        const char codes[] = {'t', 'b', 'l', 'r'};
        const char *names[] = {"top", "bottom", "left", "right"};
        for (size_t i = 0; i < 4; ++i) {
            const double value = edgeMean(codes[i]);
            edge << names[i] << ',' << band << ',' << value << ','
                 << (global_mean > 0.0 ? value / global_mean : 0.0) << "\n";
        }
    }

    struct EdgeStats {
        double seam_sum = 0.0;
        double regular_sum = 0.0;
        size_t seam_count = 0;
        size_t regular_count = 0;
    } x_stats, y_stats;
    for (int y = 0; y < canvas.height; ++y) {
        for (int x = 1; x < canvas.width; ++x) {
            const CanvasCell &a = canvas.cells[static_cast<size_t>(y) * canvas.width + x - 1];
            const CanvasCell &b = canvas.cells[static_cast<size_t>(y) * canvas.width + x];
            if (!a.valid || !b.valid) continue;
            const double d = std::abs(static_cast<double>(a.amplitude) - b.amplitude);
            if (a.tile_x != b.tile_x || a.tile_y != b.tile_y) {
                x_stats.seam_sum += d;
                ++x_stats.seam_count;
            } else {
                x_stats.regular_sum += d;
                ++x_stats.regular_count;
            }
        }
    }
    for (int y = 1; y < canvas.height; ++y) {
        for (int x = 0; x < canvas.width; ++x) {
            const CanvasCell &a = canvas.cells[static_cast<size_t>(y - 1) * canvas.width + x];
            const CanvasCell &b = canvas.cells[static_cast<size_t>(y) * canvas.width + x];
            if (!a.valid || !b.valid) continue;
            const double d = std::abs(static_cast<double>(a.amplitude) - b.amplitude);
            if (a.tile_x != b.tile_x || a.tile_y != b.tile_y) {
                y_stats.seam_sum += d;
                ++y_stats.seam_count;
            } else {
                y_stats.regular_sum += d;
                ++y_stats.regular_count;
            }
        }
    }
    auto mean = [](double sum, size_t count) {
        return count ? sum / static_cast<double>(count) : 0.0;
    };
    auto ratio = [&](const EdgeStats &stats) {
        const double regular = mean(stats.regular_sum, stats.regular_count);
        return regular > 0.0 ? mean(stats.seam_sum, stats.seam_count) / regular : 0.0;
    };
    auto lagCorrelation = [&](int dx, int dy) {
        if (dx < 0 || dy < 0 || (dx == 0 && dy == 0) ||
            dx >= canvas.width || dy >= canvas.height) return 0.0;
        double sx = 0.0, sy = 0.0, sxx = 0.0, syy = 0.0, sxy = 0.0;
        size_t count = 0;
        for (int y = 0; y + dy < canvas.height; ++y) {
            for (int x = 0; x + dx < canvas.width; ++x) {
                const CanvasCell &a = canvas.cells[static_cast<size_t>(y) * canvas.width + x];
                const CanvasCell &b = canvas.cells[static_cast<size_t>(y + dy) * canvas.width + x + dx];
                if (!a.valid || !b.valid) continue;
                const double va = a.amplitude;
                const double vb = b.amplitude;
                sx += va;
                sy += vb;
                sxx += va * va;
                syy += vb * vb;
                sxy += va * vb;
                ++count;
            }
        }
        if (count < 2) return 0.0;
        const double n = static_cast<double>(count);
        const double cov = sxy - sx * sy / n;
        const double vx = sxx - sx * sx / n;
        const double vy = syy - sy * sy / n;
        return (vx > 0.0 && vy > 0.0) ? cov / std::sqrt(vx * vy) : 0.0;
    };
    const int period_x_bins = std::max(1, static_cast<int>(std::llround(
        2.0 * std::max(1, src.width - 1) * cfg.sar_input.pixel_size_range_m /
        canvas.resolution_m)));
    const int period_y_bins = std::max(1, static_cast<int>(std::llround(
        2.0 * std::max(1, src.height - 1) * cfg.sar_input.pixel_size_azimuth_m /
        canvas.resolution_m)));
    EdgeStats all_stats;
    all_stats.seam_sum = x_stats.seam_sum + y_stats.seam_sum;
    all_stats.regular_sum = x_stats.regular_sum + y_stats.regular_sum;
    all_stats.seam_count = x_stats.seam_count + y_stats.seam_count;
    all_stats.regular_count = x_stats.regular_count + y_stats.regular_count;
    std::ofstream seam((output_dir + "/debug/mirror_seam_metrics.csv").c_str());
    seam << "axis,seam_edge_count,regular_edge_count,seam_mean_abs_diff,regular_mean_abs_diff,"
            "seam_to_regular_ratio,reflect_period_bins,periodic_autocorrelation\n";
    seam << "x," << x_stats.seam_count << ',' << x_stats.regular_count << ','
         << mean(x_stats.seam_sum, x_stats.seam_count) << ','
         << mean(x_stats.regular_sum, x_stats.regular_count) << ',' << ratio(x_stats)
         << ',' << period_x_bins << ',' << lagCorrelation(period_x_bins, 0) << "\n";
    seam << "y," << y_stats.seam_count << ',' << y_stats.regular_count << ','
         << mean(y_stats.seam_sum, y_stats.seam_count) << ','
         << mean(y_stats.regular_sum, y_stats.regular_count) << ',' << ratio(y_stats)
         << ',' << period_y_bins << ',' << lagCorrelation(0, period_y_bins) << "\n";
    seam << "combined," << all_stats.seam_count << ',' << all_stats.regular_count << ','
         << mean(all_stats.seam_sum, all_stats.seam_count) << ','
         << mean(all_stats.regular_sum, all_stats.regular_count) << ',' << ratio(all_stats)
         << ",0,0\n";

    std::ofstream idx((output_dir + "/debug/mirror_index_validation.csv").c_str());
    idx << "raw_index,mapped_index\n";
    const int n = std::max(2, std::min(16, src.width));
    for (int raw = -2 * n; raw <= 3 * n; ++raw) {
        idx << raw << "," << stage3MirrorIndexReflect101(raw, n) << "\n";
    }
}

void writeExpandedConfig(const Stage3Config &cfg, const std::string &output_dir)
{
    std::ofstream os((output_dir + "/configs/expanded_config.json").c_str());
    os << "{\n"
       << "  \"case_id\": \"" << cfg.case_id << "\",\n"
       << "  \"output_dir\": \"" << cfg.output_dir << "\",\n"
       << "  \"data_file\": \"data/stage3_sar_scene_newprotocol.bin\",\n"
       << "  \"canvas\": {\"resolution_m\": " << cfg.canvas.resolution_m
       << ", \"margin_m\": " << cfg.canvas.margin_m << "},\n"
       << "  \"scene\": {\"mode\": \"" << cfg.scene.mode
       << "\", \"roi_enabled\": " << (cfg.scene.roi.enabled ? "true" : "false")
       << ", \"roi_beam_id_1based\": " << cfg.scene.roi.beam_id_1based
       << ", \"roi_center_slant_range_m\": " << cfg.scene.roi.center_slant_range_m
       << ", \"roi_range_extent_m\": " << cfg.scene.roi.range_extent_m
       << ", \"roi_azimuth_extent_m\": " << cfg.scene.roi.azimuth_extent_m
       << ", \"roi_resolution_m\": " << cfg.scene.roi.resolution_m
       << ", \"roi_margin_m\": " << cfg.scene.roi.margin_m << "},\n"
       << "  \"tiling\": {\"mode\": \"" << cfg.tiling.mode
       << "\", \"tile_gain_jitter_db\": " << cfg.tiling.tile_gain_jitter_db
       << ", \"phase_policy\": \"" << cfg.tiling.phase_policy
       << "\", \"random_seed\": " << cfg.tiling.random_seed << "},\n"
       << "  \"forward\": {\"backend\": \"" << cfg.forward.backend
       << "\", \"compare_cpu_cuda\": " << (cfg.forward.compare_cpu_cuda ? "true" : "false")
       << ", \"beam_start\": " << cfg.forward.beam_start
       << ", \"beam_count\": " << cfg.forward.beam_count
       << ", \"pulse_start\": " << cfg.forward.pulse_start
       << ", \"pulse_count\": " << cfg.forward.pulse_count << "},\n"
       << "  \"targets\": {\"enabled\": " << (cfg.targets.enabled ? "true" : "false")
       << ", \"target_config_path\": \"" << cfg.targets.target_config_path
       << "\", \"target_snr_db\": " << cfg.targets.target_snr_db
       << ", \"amplitude_mode\": \"" << cfg.targets.amplitude_mode << "\"},\n"
       << "  \"channel_impairments\": {\"enabled\": "
       << (cfg.impairments.enabled ? "true" : "false")
       << ", \"strict_zero_bypass\": "
       << (gmti::target_injection::channelImpairmentsAreZero(cfg.impairments)
               ? "true" : "false")
       << ", \"channel_amp_mismatch_db\": " << cfg.impairments.channel_amp_mismatch_db
       << ", \"channel_fixed_phase_mismatch_deg\": " << cfg.impairments.channel_fixed_phase_mismatch_deg
       << ", \"channel_phase_jitter_std_deg\": " << cfg.impairments.channel_phase_jitter_std_deg
       << ", \"channel_range_shift_samples\": " << cfg.impairments.channel_range_shift_samples
       << ", \"channel_time_delay_ns\": " << cfg.impairments.channel_time_delay_ns
       << ", \"channel_noise_power_ratio_db\": " << cfg.impairments.channel_noise_power_ratio_db
       << ", \"iq_gain_imbalance_db\": " << cfg.impairments.iq_gain_imbalance_db
       << ", \"iq_phase_imbalance_deg\": " << cfg.impairments.iq_phase_imbalance_deg
       << ", \"baseline_error_m\": " << cfg.impairments.baseline_error_m
       << ", \"per_pulse_phase_drift_deg\": " << cfg.impairments.per_pulse_phase_drift_deg
       << ", \"per_beam_phase_bias_deg\": " << cfg.impairments.per_beam_phase_bias_deg
       << ", \"sample_clock_error_ppm\": " << cfg.impairments.sample_clock_error_ppm
       << ", \"channel_drop_probability\": " << cfg.impairments.channel_drop_probability
       << ", \"channel_saturation_level\": " << cfg.impairments.channel_saturation_level
       << "}\n"
       << "}\n";
}

void writeManifest(const Stage3Config &cfg,
                   const SceneCanvas &canvas,
                   bool wrote_bin,
                   const gmti::target_injection::InjectionStats *target_stats,
                   const std::string &output_dir)
{
    std::ofstream os((output_dir + "/logs/stage3_manifest.json").c_str());
    os << "{\n"
       << "  \"case_id\": \"" << cfg.case_id << "\",\n"
       << "  \"wrote_bin\": " << (wrote_bin ? "true" : "false") << ",\n"
       << "  \"dry_run\": " << (cfg.forward.dry_run ? "true" : "false") << ",\n"
       << "  \"beam_start\": " << cfg.forward.beam_start << ",\n"
       << "  \"beam_count\": " << cfg.forward.beam_count << ",\n"
       << "  \"pulse_start\": " << cfg.forward.pulse_start << ",\n"
       << "  \"pulse_count\": " << cfg.forward.pulse_count << ",\n"
       << "  \"backend\": \"" << cfg.forward.backend << "\",\n"
       << "  \"canvas_cells\": " << canvas.cells.size() << ",\n"
       << "  \"targets_enabled\": " << (cfg.targets.enabled ? "true" : "false") << ",\n"
       << "  \"target_config_path\": \"" << cfg.targets.target_config_path << "\",\n"
       << "  \"target_pulses_injected\": "
       << (target_stats ? target_stats->pulses_injected : 0) << ",\n"
       << "  \"target_samples_injected\": "
       << (target_stats ? target_stats->samples_injected : 0) << ",\n"
       << "  \"target_has_nan\": "
       << (target_stats && target_stats->has_nan ? "true" : "false") << ",\n"
       << "  \"target_has_inf\": "
       << (target_stats && target_stats->has_inf ? "true" : "false") << ",\n"
       << "  \"channel_impairments_enabled\": "
       << (cfg.impairments.enabled ? "true" : "false") << ",\n"
       << "  \"channel_impairments_zero_bypass\": "
       << (gmti::target_injection::channelImpairmentsAreZero(cfg.impairments)
               ? "true" : "false") << ",\n"
       << "  \"seed\": " << cfg.tiling.random_seed << "\n"
       << "}\n";
}

void writeAlgorithmXml(const Stage3Config &cfg, const std::string &output_dir, const std::string &data_file)
{
    std::ofstream os((output_dir + "/config/temp_config_stage3_newsystem.xml").c_str());
    const double scan_max = cfg.system.scan_min_deg + cfg.system.scan_step_deg * (cfg.system.beam_count - 1);
    const int beam_end = std::min(cfg.system.beam_count,
        cfg.forward.beam_start + std::max(1, cfg.forward.beam_count) - 1);
    const int actual_beam_count = std::max(0, beam_end - cfg.forward.beam_start + 1);
    const int actual_pulse_count = std::max(1, std::min(cfg.system.pulse_num - cfg.forward.pulse_start,
                                                       cfg.forward.pulse_count));
    const std::string abs_data_file = makeAbsolutePath(data_file);
    const std::string abs_output_dir = makeAbsolutePath(output_dir);
    os << "<?xml version=\"1.0\" encoding=\"utf-8\" ?>\n"
       << "<GMTI>\n  <GMTI_parameter>\n"
       << "    <GMTI_data>" << abs_data_file << "</GMTI_data>\n"
       << "    <GMTI_data2>" << abs_data_file << "</GMTI_data2>\n"
       << "    <Plane_POS></Plane_POS>\n"
       << "    <reffunc_add></reffunc_add>\n"
       << "    <isSeparated>new_protocol</isSeparated>\n"
       << "    <INFO_Type>1</INFO_Type>\n"
       << "    <GMTI_data_new>" << abs_data_file << "</GMTI_data_new>\n"
       << "    <result_add>" << abs_output_dir << "/algorithm_result</result_add>\n"
       << "    <pipe_root_path>/tmp</pipe_root_path>\n"
       << "    <iq_compose>I+jQ</iq_compose>\n"
       << "    <isPC>0</isPC>\n"
       << "    <hasRefFunc>0</hasRefFunc>\n"
       << "    <info_len>256</info_len>\n"
       << "    <pulse_len>" << cfg.system.ddc_len << "</pulse_len>\n"
       << "    <rg_len>" << cfg.system.pc_crop_len << "</rg_len>\n"
       << "    <pulse_num>" << actual_pulse_count << "</pulse_num>\n"
       << "    <read_pulse_num>" << actual_pulse_count << "</read_pulse_num>\n"
       << "    <read_pulse_offset>0</read_pulse_offset>\n"
       << "    <process_pulse_num>" << actual_pulse_count << "</process_pulse_num>\n"
       << "    <pulse_dec>1</pulse_dec>\n"
       << "    <range_fft_len>" << cfg.system.fft_len << "</range_fft_len>\n"
       << "    <range_crop_start>" << cfg.system.pc_crop_start << "</range_crop_start>\n"
       << "    <range_compress_len>" << cfg.system.pc_crop_len << "</range_compress_len>\n"
       << "    <fc>" << cfg.system.fc_ghz << "</fc>\n"
       << "    <Br>" << cfg.system.bandwidth_mhz << "</Br>\n"
       << "    <fs>" << cfg.system.fs_mhz << "</fs>\n"
       << "    <Tr>" << cfg.system.pulse_width_us << "</Tr>\n"
       << "    <PRF>" << cfg.system.prf_hz << "</PRF>\n"
       << "    <d_chan>" << cfg.system.d_chan_m << "</d_chan>\n"
       << "    <ref_lon>" << cfg.system.projection_ref_lon_deg << "</ref_lon>\n"
       << "    <ref_H>" << cfg.scene.ground_z_m << "</ref_H>\n"
       << "    <Rmin>" << 0.5 * kC * cfg.system.sample_delay_us * 1.0e-6 << "</Rmin>\n"
       << "    <secBias>18</secBias>\n"
       << "    <week_offset>0</week_offset>\n"
       << "    <skip_pulses>0</skip_pulses>\n"
       << "    <calib_coef>1.0</calib_coef>\n"
       << "    <sample_delay_us>" << cfg.system.sample_delay_us << "</sample_delay_us>\n"
       << "    <sample_window_us>" << cfg.system.sample_window_us << "</sample_window_us>\n"
       << "    <iq_data_type>" << cfg.system.iq_data_type << "</iq_data_type>\n"
       << "    <new_protocol_channel_count>" << cfg.system.new_protocol_channel_count << "</new_protocol_channel_count>\n"
       << "    <new_protocol_read_channel_1>" << cfg.system.new_protocol_read_channel_1 << "</new_protocol_read_channel_1>\n"
       << "    <new_protocol_read_channel_2>" << cfg.system.new_protocol_read_channel_2 << "</new_protocol_read_channel_2>\n"
       << "    <new_protocol_file_first_beam>" << cfg.forward.beam_start << "</new_protocol_file_first_beam>\n"
       << "    <two_channel_phase_model>ctdr</two_channel_phase_model>\n"
       << "    <rx_baseline_sign>1</rx_baseline_sign>\n"
       << "    <channel_phase_sign>-1</channel_phase_sign>\n"
       << "    <estimate_error_angle>0</estimate_error_angle>\n"
       << "    <wavepos_parallel>1</wavepos_parallel>\n"
       << "    <enable_dbs_fusion>1</enable_dbs_fusion>\n"
       << "    <wavepos_use_roi>false</wavepos_use_roi>\n"
       << "    <roi_lat1>" << cfg.system.origin_lat_deg << "</roi_lat1>\n"
       << "    <roi_lng1>" << cfg.system.origin_lon_deg << "</roi_lng1>\n"
       << "    <roi_Ry1>400</roi_Ry1>\n"
       << "    <roi_Rx1>400</roi_Rx1>\n"
       << "    <wavepos_st>" << cfg.forward.beam_start << "</wavepos_st>\n"
       << "    <wavepos_ed>" << beam_end << "</wavepos_ed>\n"
       << "    <wavepos_skip>1</wavepos_skip>\n"
       << "    <scan_min_deg>" << cfg.system.scan_min_deg << "</scan_min_deg>\n"
       << "    <scan_max_deg>" << scan_max << "</scan_max_deg>\n"
       << "    <scan_step_deg>" << cfg.system.scan_step_deg << "</scan_step_deg>\n"
       << "    <az_count>" << cfg.system.beam_count << "</az_count>\n"
       << "    <boshu>" << cfg.system.beam_width_deg << "</boshu>\n"
       << "    <beam_width_deg>" << cfg.system.beam_width_deg << "</beam_width_deg>\n"
       << "    <loc_beam_gate_deg>" << cfg.system.beam_width_deg * 2.0 << "</loc_beam_gate_deg>\n"
       << "    <max_theta>60</max_theta>\n"
       << "    <period_first>" << cfg.forward.beam_start << "</period_first>\n"
       << "    <period_num>" << actual_beam_count << "</period_num>\n"
       << "    <squint_side>" << cfg.system.squint_side << "</squint_side>\n"
       << "    <pf>1e-6</pf>\n"
       << "    <min_points>10</min_points>\n"
       << "    <min_len>1</min_len>\n"
       << "    <rg_st>1</rg_st>\n"
       << "    <rg_ed>" << (cfg.system.pc_crop_len - 1) << "</rg_ed>\n"
       << "    <lat_st>2</lat_st>\n"
       << "    <lat_ed>2</lat_ed>\n"
       << "    <lon_st>2</lon_st>\n"
       << "    <lon_ed>2</lon_ed>\n"
       << "    <n_tiaoguo>1</n_tiaoguo>\n"
       << "    <len_tiaoguo>200</len_tiaoguo>\n"
       << "    <raw_fenbianlv>" << cfg.forward.dbs_output_resolution_m << "</raw_fenbianlv>\n"
       << "    <dbs_max_mosaic_pixels>300000000</dbs_max_mosaic_pixels>\n"
       << "    <dbs_mosaic_margin_ratio>0.08</dbs_mosaic_margin_ratio>\n"
       << "    <dbs_mosaic_margin_m>500</dbs_mosaic_margin_m>\n"
       << "    <squint_angle>0</squint_angle>\n"
       << "    <motion_comp_enable>1</motion_comp_enable>\n"
       << "    <motion_comp_apply_to_localization>1</motion_comp_apply_to_localization>\n"
       << "    <motion_comp_solver>debug</motion_comp_solver>\n"
       << "    <p38_min_peak_row_energy_fraction>0.05</p38_min_peak_row_energy_fraction>\n"
       << "  </GMTI_parameter>\n</GMTI>\n";
}

} // namespace

bool forwardSarSceneToDdc(const Stage3Config &cfg,
                          const std::vector<SarScatterer> &,
                          const std::string &output_path,
                          std::string &err)
{
    const int beam_end = std::min(cfg.system.beam_count,
        cfg.forward.beam_start + std::max(1, cfg.forward.beam_count) - 1);
    const int pulse_end = std::min(cfg.system.pulse_num,
        cfg.forward.pulse_start + std::max(1, cfg.forward.pulse_count));
    const int actual_pulse_count = pulse_end - cfg.forward.pulse_start;
    if (cfg.forward.beam_start < 1 || cfg.forward.beam_start > beam_end ||
        cfg.forward.pulse_start < 0 || actual_pulse_count <= 0 ||
        cfg.forward.period_start < 0 || cfg.forward.period_count <= 0) {
        err = "invalid Stage3 forward beam/pulse/period subset";
        return false;
    }
    if (cfg.scene.area_clutter.enabled) {
        err = "Stage3 SAR forward requires scene.area_clutter.enabled=false; "
              "the legacy statistical area model is not coherent with the fixed SAR surface";
        return false;
    }
    if ((cfg.scene.mode == "roi" || cfg.scene.roi.enabled) &&
        (cfg.scene.roi.beam_id_1based < cfg.forward.beam_start ||
         cfg.scene.roi.beam_id_1based > beam_end)) {
        err = "ROI beam must be included in the requested forward beam subset";
        return false;
    }

    SarImage src;
    if (!readSarImage(cfg.sar_input.image_path, cfg.sar_input.input_type, src, err)) {
        return false;
    }
    if (!cropSarImageToValidArea(src, cfg, err)) return false;
    normalizeSarImage(src, cfg);
    SceneCanvas canvas;
    if (!buildCanvas(cfg, canvas, err)) return false;
    if (!fillCanvasFromSar(canvas, src, cfg, err)) return false;
    markFootprint(canvas, cfg);

    writeCanvasTruth(canvas, src, cfg, cfg.output_dir);
    writeDebugPreviews(canvas, src, cfg, cfg.output_dir);
    writeExpandedConfig(cfg, cfg.output_dir);

    if (cfg.forward.dry_run) {
        writeManifest(cfg, canvas, false, nullptr, cfg.output_dir);
        return true;
    }

    Stage3TargetContext target_ctx;
    if (!configureStage3Target(cfg, output_path, target_ctx, err)) return false;

    gmti::target_injection::RadarConfig impairment_radar;
    impairment_radar.pulse_len = cfg.system.ddc_len;
    impairment_radar.pulse_num = cfg.system.pulse_num;
    impairment_radar.beam_count = cfg.system.beam_count;
    impairment_radar.iq_data_type = cfg.system.iq_data_type;
    impairment_radar.new_protocol_channel_count = cfg.system.new_protocol_channel_count;
    impairment_radar.new_protocol_read_channel_1 = cfg.system.new_protocol_read_channel_1;
    impairment_radar.new_protocol_read_channel_2 = cfg.system.new_protocol_read_channel_2;
    impairment_radar.fs_hz = cfg.system.fs_mhz * 1.0e6;
    impairment_radar.fc_hz = cfg.system.fc_ghz * 1.0e9;
    std::ofstream impairment_truth;
    if (cfg.impairments.enabled) {
        impairment_truth.open((cfg.output_dir + "/truth/channel_impairment_truth.csv").c_str());
        impairment_truth << "case_id,period_id,beam_id,pulse_id,applied,relative_gain,"
            "relative_phase_deg,effective_shift_samples,sample_clock_error_ppm,"
            "added_noise_sigma_ch1,added_noise_sigma_ch2,channel_dropped,"
            "saturated_sample_count\n";
        std::ofstream manifest((cfg.output_dir + "/truth/channel_impairment_manifest.json").c_str());
        manifest << gmti::target_injection::channelImpairmentConfigJson(cfg.impairments);
    }

    const std::vector<std::vector<BeamCellRef>> refs = buildBeamRefs(canvas, cfg);
    const std::size_t channel_count = static_cast<std::size_t>(std::max(2, cfg.system.new_protocol_channel_count));
    const std::string iq_type = cfg.system.iq_data_type.empty() ? "float32" : cfg.system.iq_data_type;
    const size_t packet_bytes = gmti::new_protocol::packetBytes(
        static_cast<size_t>(cfg.system.ddc_len), channel_count, iq_type);
    std::ofstream out(output_path.c_str(), std::ios::binary);
    if (!out) {
        err = "failed to open output " + output_path;
        return false;
    }

    bool wrote_profile = false;
    bool comparison_passed = true;
    std::ofstream perf((cfg.output_dir + "/logs/stage3_performance.csv").c_str());
    perf << "period_id,beam_id,backend,scatterer_count,pulse_count,cpu_geometry_ms,"
            "cuda_h2d_ms,cuda_kernel_ms,cuda_d2h_ms,cuda_total_ms,lfm_ms,pack_write_ms,"
            "geometry_speedup,total_speedup,max_abs_error,relative_l2_error,rmse,"
            "max_phase_error_rad,comparison_pass\n";
    std::ofstream comparison((cfg.output_dir + "/reports/stage3_cpu_cuda_comparison.json").c_str());
    comparison << "{\n  \"case_id\": \"" << cfg.case_id << "\",\n"
               << "  \"comparison_requested\": "
               << (cfg.forward.compare_cpu_cuda ? "true" : "false") << ",\n"
               << "  \"abs_tolerance\": " << cfg.forward.compare_abs_tolerance << ",\n"
               << "  \"rel_tolerance\": " << cfg.forward.compare_rel_tolerance << ",\n"
               << "  \"beams\": [\n";
    bool first_comparison = true;

    for (int period = cfg.forward.period_start;
         period < cfg.forward.period_start + cfg.forward.period_count; ++period) {
        for (int b1 = cfg.forward.beam_start; b1 <= beam_end; ++b1) {
            const int b0 = b1 - 1;
            const double theta = cfg.system.scan_min_deg + cfg.system.scan_step_deg * b0;
            const std::vector<Stage3CudaCell> forward_cells =
                makeForwardCells(canvas, refs[static_cast<size_t>(b0)]);
            std::vector<std::complex<float>> cpu_ch1, cpu_ch2, cuda_ch1, cuda_ch2;
            double cpu_ms = 0.0;
            Stage3CudaTiming cuda_timing = {0.0f, 0.0f, 0.0f, 0.0f};
            const bool cuda_available = stage3CudaAvailable();
            const bool selected_cuda = cfg.forward.backend == "cuda" ||
                                       (cfg.forward.backend == "auto" && cuda_available);
            const bool need_cpu = !selected_cuda || cfg.forward.compare_cpu_cuda;
            const bool need_cuda = selected_cuda || cfg.forward.compare_cpu_cuda;
            if (cfg.forward.backend == "cuda" && !cuda_available) {
                err = "forward.backend=cuda requested but no CUDA device is visible";
                return false;
            }
            if (need_cpu) {
                const Stage3CudaForwardParams params = makeForwardParams(
                    cfg, period, b0, cfg.forward.pulse_start, actual_pulse_count);
                if (!forwardStage3CpuBatch(forward_cells, params, cpu_ch1, cpu_ch2,
                                           cpu_ms, err)) return false;
            }
            if (need_cuda) {
                if (!cuda_available) {
                    err = "CPU/CUDA comparison requested but no CUDA device is visible";
                    return false;
                }
                const int batch_size = std::max(1, std::min(actual_pulse_count,
                                                            cfg.forward.cuda_batch_pulses));
                for (int start = 0; start < actual_pulse_count; start += batch_size) {
                    const int count = std::min(batch_size, actual_pulse_count - start);
                    const Stage3CudaForwardParams params = makeForwardParams(
                        cfg, period, b0, cfg.forward.pulse_start + start, count);
                    std::vector<std::complex<float>> c1, c2;
                    Stage3CudaTiming timing;
                    if (!forwardStage3CudaBatch(forward_cells.data(), forward_cells.size(),
                                                params, c1, c2, timing, err)) return false;
                    cuda_ch1.insert(cuda_ch1.end(), c1.begin(), c1.end());
                    cuda_ch2.insert(cuda_ch2.end(), c2.begin(), c2.end());
                    cuda_timing.h2d_ms += timing.h2d_ms;
                    cuda_timing.kernel_ms += timing.kernel_ms;
                    cuda_timing.d2h_ms += timing.d2h_ms;
                    cuda_timing.total_ms += timing.total_ms;
                }
            }

            NumericComparison numeric;
            bool beam_comparison_pass = true;
            if (cfg.forward.compare_cpu_cuda) {
                const NumericComparison c1 = compareComplexVectors(cpu_ch1, cuda_ch1);
                const NumericComparison c2 = compareComplexVectors(cpu_ch2, cuda_ch2);
                numeric.max_abs = std::max(c1.max_abs, c2.max_abs);
                numeric.rmse = std::max(c1.rmse, c2.rmse);
                numeric.relative_l2 = std::max(c1.relative_l2, c2.relative_l2);
                numeric.max_phase_error_rad = std::max(c1.max_phase_error_rad,
                                                       c2.max_phase_error_rad);
                numeric.sample_count = c1.sample_count + c2.sample_count;
                beam_comparison_pass = numeric.max_abs <= cfg.forward.compare_abs_tolerance &&
                    numeric.relative_l2 <= cfg.forward.compare_rel_tolerance;
                comparison_passed = comparison_passed && beam_comparison_pass;
                if (!first_comparison) comparison << ",\n";
                comparison << "    {\"period_id\": " << period
                           << ", \"beam_id\": " << b1
                           << ", \"sample_count\": " << numeric.sample_count
                           << ", \"max_abs_error\": " << numeric.max_abs
                           << ", \"relative_l2_error\": " << numeric.relative_l2
                           << ", \"rmse\": " << numeric.rmse
                           << ", \"max_phase_error_rad\": " << numeric.max_phase_error_rad
                           << ", \"pass\": " << (beam_comparison_pass ? "true" : "false") << "}";
                first_comparison = false;
            }

            std::vector<std::complex<float>> beam_ch1 = selected_cuda ? cuda_ch1 : cpu_ch1;
            std::vector<std::complex<float>> beam_ch2 = selected_cuda ? cuda_ch2 : cpu_ch2;
            double lfm_ms = 0.0;
            if (!applyLfmWaveform(cfg, beam_ch1, beam_ch2, actual_pulse_count,
                                  lfm_ms, err)) return false;
            const auto pack_start = std::chrono::steady_clock::now();
            for (int pl = 0; pl < actual_pulse_count; ++pl) {
                const int p = cfg.forward.pulse_start + pl;
                std::vector<uint8_t> packet(packet_bytes, 0);
                const int global_prt = period * cfg.system.beam_count * cfg.system.pulse_num +
                                       b0 * cfg.system.pulse_num + p;
                const double time_sec = static_cast<double>(global_prt) / cfg.system.prf_hz;
                writeHeader(packet, cfg, global_prt, time_sec, theta);
                const size_t pulse_off = static_cast<size_t>(pl) * cfg.system.ddc_len;
                for (int n = 0; n < cfg.system.ddc_len; ++n) {
                    storeCh(packet, n, cfg.system.new_protocol_read_channel_1,
                            channel_count, iq_type, beam_ch1[pulse_off + n]);
                    storeCh(packet, n, cfg.system.new_protocol_read_channel_2,
                            channel_count, iq_type, beam_ch2[pulse_off + n]);
                }
                if (cfg.targets.enabled) {
                    bool packet_has_injection = false;
                    for (const gmti::target_injection::TargetConfig &target :
                         target_ctx.targets) {
                        const gmti::target_injection::PulseTruth target_truth =
                            gmti::target_injection::injectOnePulse(
                                packet, target_ctx.radar, target_ctx.global,
                                target, period, b0, p);
                        target_ctx.truth.writePulse(target_truth);
                        if (target_truth.injection_enabled) {
                            packet_has_injection = true;
                            target_ctx.stats.samples_injected +=
                                static_cast<uint64_t>(target_truth.injected_sample_count);
                        }
                    }
                    if (packet_has_injection) ++target_ctx.stats.pulses_injected;
                }
                const gmti::target_injection::ChannelImpairmentRealization impairment =
                    gmti::target_injection::applyChannelImpairments(
                        packet, impairment_radar, cfg.impairments,
                        period, b0, p, theta, cfg.tiling.random_seed);
                if (impairment_truth) {
                    impairment_truth << cfg.case_id << ',' << period << ',' << b1
                        << ',' << p << ',' << (impairment.applied ? 1 : 0) << ','
                        << std::setprecision(17) << impairment.relative_gain << ','
                        << impairment.relative_phase_deg << ','
                        << impairment.effective_shift_samples << ','
                        << impairment.sample_clock_error_ppm << ','
                        << impairment.added_noise_sigma_ch1 << ','
                        << impairment.added_noise_sigma_ch2 << ','
                        << (impairment.channel_dropped ? 1 : 0) << ','
                        << impairment.saturated_sample_count << '\n';
                }
                if (cfg.targets.enabled) {
                    updateStage3TargetPacketStats(
                        cfg, packet, channel_count, iq_type, target_ctx);
                }
                if (!wrote_profile) {
                    std::ofstream prof((cfg.output_dir + "/debug/first_prt_range_profile.csv").c_str());
                    prof << "sample,ch1_abs,ch2_abs\n";
                    for (int n = 0; n < cfg.system.ddc_len; ++n) {
                        const std::complex<float> c1 = loadCh(packet, n, cfg.system.new_protocol_read_channel_1, channel_count, iq_type);
                        const std::complex<float> c2 = loadCh(packet, n, cfg.system.new_protocol_read_channel_2, channel_count, iq_type);
                        prof << n << "," << std::abs(c1) << "," << std::abs(c2) << "\n";
                    }
                    wrote_profile = true;
                }
                out.write(reinterpret_cast<const char *>(packet.data()), static_cast<std::streamsize>(packet.size()));
            }
            if (!out) {
                err = "failed while writing Stage3 new-protocol output";
                return false;
            }
            const double pack_ms = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - pack_start).count();
            const double geometry_speedup = cuda_timing.total_ms > 0.0f
                ? cpu_ms / cuda_timing.total_ms : 0.0;
            const double total_speedup = cpu_ms > 0.0 && cuda_timing.total_ms > 0.0f
                ? (cpu_ms + lfm_ms + pack_ms) /
                  (static_cast<double>(cuda_timing.total_ms) + lfm_ms + pack_ms) : 0.0;
            perf << period << "," << b1 << "," << (selected_cuda ? "cuda" : "cpu") << ","
                 << forward_cells.size() << "," << actual_pulse_count << "," << cpu_ms << ","
                 << cuda_timing.h2d_ms << "," << cuda_timing.kernel_ms << ","
                 << cuda_timing.d2h_ms << "," << cuda_timing.total_ms << ","
                 << lfm_ms << "," << pack_ms << "," << geometry_speedup << ","
                 << total_speedup << "," << numeric.max_abs << "," << numeric.relative_l2
                 << "," << numeric.rmse << "," << numeric.max_phase_error_rad << ","
                 << (cfg.forward.compare_cpu_cuda ? (beam_comparison_pass ? 1 : 0) : -1) << "\n";
        }
    }
    comparison << "\n  ],\n  \"comparison_performed\": "
               << (cfg.forward.compare_cpu_cuda ? "true" : "false")
               << ",\n  \"all_passed\": ";
    if (cfg.forward.compare_cpu_cuda) {
        comparison << (comparison_passed ? "true" : "false");
    } else {
        comparison << "null";
    }
    comparison << "\n}\n";
    if (!finalizeStage3Target(cfg, output_path, target_ctx, err)) return false;
    writeAlgorithmXml(cfg, cfg.output_dir, output_path);
    writeManifest(cfg, canvas, true,
                  cfg.targets.enabled ? &target_ctx.stats : nullptr,
                  cfg.output_dir);
    if (cfg.forward.compare_cpu_cuda && !comparison_passed) {
        err = "Stage3 CPU/CUDA numerical comparison exceeded configured tolerance";
        return false;
    }
    return true;
}

} // namespace stage3
} // namespace gmti
