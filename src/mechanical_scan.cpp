#include "mechanical_scan.hpp"

#include "dbs/NewProtocolLayout.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <cerrno>
#include <sys/stat.h>

namespace {

std::string sourcePath(const Config& cfg)
{
    return cfg.GMTI_Data_new.empty() ? cfg.GMTI_Data_add : cfg.GMTI_Data_new;
}

std::string joinPath(const std::string& dir, const std::string& name)
{
    if (dir.empty()) return name;
    const char last = dir[dir.size() - 1U];
    return (last == '/' || last == '\\') ? dir + name : dir + "/" + name;
}

bool ensureDirectory(const std::string& path)
{
    if (path.empty()) return false;
    struct stat st;
    if (::stat(path.c_str(), &st) == 0) return S_ISDIR(st.st_mode);
    const std::size_t slash = path.find_last_of("/\\");
    if (slash != std::string::npos && slash > 0U &&
        !ensureDirectory(path.substr(0, slash))) {
        return false;
    }
    return ::mkdir(path.c_str(), 0755) == 0 || errno == EEXIST;
}

bool nonEmptyFile(const std::string& path)
{
    struct stat st;
    return ::stat(path.c_str(), &st) == 0 && S_ISREG(st.st_mode) &&
           st.st_size > 0;
}

bool fail(std::string* error, const std::string& message)
{
    if (error) *error = message;
    return false;
}

bool validPrtHeader(const std::uint8_t* packet,
                    std::size_t packet_bytes,
                    int expected_mode)
{
    using namespace gmti::new_protocol;
    if (!packet || packet_bytes < kHeaderBytes) return false;
    for (std::size_t i = 0; i < 8U; ++i) {
        if (packet[i] != 0x5AU || packet[kOffMagicTail + i] != 0x5BU) {
            return false;
        }
    }
    if (loadU32LE(packet + kOffPrtLen) != packet_bytes) return false;
    return expected_mode < 0 || expected_mode > 255 ||
           packet[kOffVersion] == static_cast<std::uint8_t>(expected_mode);
}

PulseMeta toPulseMeta(const gmti::new_protocol::HeaderSample& h,
                      double velocity_scale)
{
    PulseMeta p;
    p.utc = h.utc;
    p.lat_deg = h.lat_deg;
    p.lon_deg = h.lon_deg;
    p.height_m = h.height_m;
    p.velocity_e_mps = h.ve_mps * velocity_scale;
    p.velocity_n_mps = h.vn_mps * velocity_scale;
    p.velocity_u_mps = -h.vd_mps * velocity_scale;
    p.heading_deg = h.heading_deg;
    p.servo_azimuth_deg = h.servo_azimuth_deg;
    p.servo_elevation_deg = h.servo_elevation_deg;
    p.scan_center_azimuth_deg = h.scan_center_azimuth_deg;
    p.scan_center_elevation_deg = h.scan_center_elevation_deg;
    p.scan_speed_az_deg_s = h.scan_speed_az_deg_s;
    p.scan_extent_az_deg = h.scan_extent_az_deg;
    p.prt_counter = h.prt_counter;
    return p;
}

double selectedAzimuth(const PulseMeta& p, const MechanicalScanConfig& cfg)
{
    return cfg.use_actual_servo_angle
        ? p.servo_azimuth_deg : p.scan_center_azimuth_deg;
}

int deltaSign(double delta, double deadband)
{
    if (!std::isfinite(delta) || std::abs(delta) <= deadband) return 0;
    return delta > 0.0 ? 1 : -1;
}

struct ScanSegment {
    std::size_t begin = 0;
    std::size_t end = 0; // exclusive
    int direction = 0;

    ScanSegment() = default;
    ScanSegment(std::size_t begin_value, std::size_t end_value, int direction_value)
        : begin(begin_value), end(end_value), direction(direction_value) {}
};

std::vector<ScanSegment> findScanSegments(const std::vector<PulseMeta>& meta,
                                          const MechanicalScanConfig& cfg)
{
    std::vector<ScanSegment> segments;
    if (meta.empty()) return segments;
    const int confirmation = std::max(1, cfg.scan_direction_confirm_pulses);
    std::size_t scan_begin = 0;
    int direction = 0;
    int candidate_direction = 0;
    int candidate_count = 0;
    std::size_t candidate_boundary = 0;
    std::size_t direction_anchor = 0;

    for (std::size_t i = 1; i < meta.size(); ++i) {
        // Accumulate motion until it exceeds the deadband. A physical servo can
        // move less than the jitter threshold during every individual PRT.
        const int sign = deltaSign(selectedAzimuth(meta[i], cfg) -
                                       selectedAzimuth(meta[direction_anchor], cfg),
                                   cfg.scan_direction_deadband_deg);
        if (sign == 0) continue;
        const std::size_t previous_anchor = direction_anchor;
        direction_anchor = i;
        if (direction == 0) {
            direction = sign;
            continue;
        }
        if (sign == direction) {
            candidate_direction = 0;
            candidate_count = 0;
            continue;
        }
        if (candidate_direction != sign) {
            candidate_direction = sign;
            candidate_count = 1;
            // The turning sample stays in the completed scan; the first pulse
            // moving away from it begins the next scan.
            candidate_boundary = std::min(i, previous_anchor + 1U);
        } else {
            ++candidate_count;
        }
        if (candidate_count >= confirmation) {
            if (candidate_boundary > scan_begin) {
                segments.push_back({scan_begin, candidate_boundary, direction});
            }
            scan_begin = candidate_boundary;
            direction = candidate_direction;
            candidate_direction = 0;
            candidate_count = 0;
        }
    }
    segments.push_back({scan_begin, meta.size(), direction});
    return segments;
}

void applyEdgeGuard(const std::vector<PulseMeta>& meta,
                    const MechanicalScanConfig& cfg,
                    std::size_t& begin,
                    std::size_t& end)
{
    if (!(cfg.scan_edge_guard_deg > 0.0) || begin >= end) return;
    double az_min = std::numeric_limits<double>::infinity();
    double az_max = -std::numeric_limits<double>::infinity();
    for (std::size_t i = begin; i < end; ++i) {
        const double az = selectedAzimuth(meta[i], cfg);
        if (!std::isfinite(az)) continue;
        az_min = std::min(az_min, az);
        az_max = std::max(az_max, az);
    }
    if (!std::isfinite(az_min) || !std::isfinite(az_max) ||
        az_max - az_min <= 2.0 * cfg.scan_edge_guard_deg) {
        begin = end;
        return;
    }
    const double low = az_min + cfg.scan_edge_guard_deg;
    const double high = az_max - cfg.scan_edge_guard_deg;
    while (begin < end) {
        const double az = selectedAzimuth(meta[begin], cfg);
        if (std::isfinite(az) && az >= low && az <= high) break;
        ++begin;
    }
    while (end > begin) {
        const double az = selectedAzimuth(meta[end - 1U], cfg);
        if (std::isfinite(az) && az >= low && az <= high) break;
        --end;
    }
}

MechanicalCpiWindow buildWindow(const std::vector<PulseMeta>& meta,
                                const MechanicalScanConfig& cfg,
                                int scan_id,
                                int window_id,
                                std::size_t start,
                                std::size_t count,
                                int direction)
{
    MechanicalCpiWindow w;
    w.scan_id = scan_id;
    w.window_id = window_id;
    w.pulse_start = start;
    w.pulse_count = count;
    w.scan_direction = direction;
    if (count == 0U || start + count > meta.size()) {
        w.valid = false;
        w.invalid_reason = "pulse_range_out_of_bounds";
        return w;
    }
    const std::size_t center = start + count / 2U;
    const PulseMeta& first = meta[start];
    const PulseMeta& middle = meta[center];
    const PulseMeta& last = meta[start + count - 1U];
    w.utc_start = first.utc;
    w.utc_center = middle.utc;
    w.utc_end = last.utc;
    w.az_start_deg = selectedAzimuth(first, cfg);
    w.az_center_deg = selectedAzimuth(middle, cfg);
    w.az_end_deg = selectedAzimuth(last, cfg);
    w.elevation_center_deg = middle.servo_elevation_deg;
    w.az_span_deg = std::abs(w.az_end_deg - w.az_start_deg);

    if (count != static_cast<std::size_t>(cfg.cpi_pulse_count)) {
        w.valid = false;
        w.invalid_reason = "incomplete_cpi";
    } else if (direction == 0) {
        w.valid = false;
        w.invalid_reason = "scan_direction_unresolved";
    } else if (direction < 0 && !cfg.allow_scan_reverse) {
        w.valid = false;
        w.invalid_reason = "reverse_scan_disabled";
    } else if (!std::isfinite(w.utc_start) || !std::isfinite(w.utc_center) ||
               !std::isfinite(w.utc_end) || !std::isfinite(w.az_start_deg) ||
               !std::isfinite(w.az_center_deg) || !std::isfinite(w.az_end_deg)) {
        w.valid = false;
        w.invalid_reason = "nonfinite_metadata";
    } else if (w.az_span_deg > cfg.max_cpi_angle_span_deg) {
        w.valid = false;
        w.invalid_reason = "angle_span_exceeds_limit";
    }
    return w;
}

} // namespace

bool readMechanicalPulseMeta(const Config& cfg,
                             std::vector<PulseMeta>& pulse_meta,
                             std::string* error)
{
    using namespace gmti::new_protocol;
    pulse_meta.clear();
    if (!cfg.INFO_Type) {
        return fail(error, "mechanical scan requires the new 256-byte PRT protocol");
    }
    if (cfg.pulse_len <= 0 || cfg.new_protocol_channel_count <= 0) {
        return fail(error, "invalid new-protocol pulse/channel dimensions");
    }
    const std::string iq_type = cfg.iq_data_type.empty() ? "float32" : cfg.iq_data_type;
    const std::size_t prt_bytes = packetBytes(
        static_cast<std::size_t>(cfg.pulse_len),
        static_cast<std::size_t>(cfg.new_protocol_channel_count), iq_type);

    const EchoCycleView* cycle = cfg.echo_cycle_view.valid()
        ? &cfg.echo_cycle_view : nullptr;
    std::uint64_t source_bytes = 0U;
    std::ifstream input;
    if (cycle) {
        if (cycle->prt_bytes != prt_bytes) {
            std::ostringstream os;
            os << "EchoCycleView PRT bytes mismatch: " << cycle->prt_bytes
               << " != " << prt_bytes;
            return fail(error, os.str());
        }
        source_bytes = static_cast<std::uint64_t>(cycle->size);
    } else {
        const std::string path = sourcePath(cfg);
        input.open(path.c_str(), std::ios::binary);
        if (!input) return fail(error, "cannot open mechanical echo file: " + path);
        input.seekg(0, std::ios::end);
        const std::streamoff end = input.tellg();
        if (end <= 0) return fail(error, "mechanical echo file is empty: " + path);
        source_bytes = static_cast<std::uint64_t>(end);
        input.seekg(0, std::ios::beg);
    }
    if (source_bytes % prt_bytes != 0U) {
        std::ostringstream os;
        os << "mechanical echo bytes " << source_bytes
           << " are not divisible by PRT bytes " << prt_bytes;
        return fail(error, os.str());
    }
    const std::size_t count = static_cast<std::size_t>(source_bytes / prt_bytes);
    pulse_meta.reserve(count);
    std::vector<std::uint8_t> header(kHeaderBytes);
    for (std::size_t i = 0; i < count; ++i) {
        const std::uint8_t* p = nullptr;
        if (cycle) {
            p = cycle->data + i * prt_bytes;
        } else {
            input.read(reinterpret_cast<char*>(header.data()),
                       static_cast<std::streamsize>(header.size()));
            if (input.gcount() != static_cast<std::streamsize>(header.size())) {
                return fail(error, "short read while reading mechanical PRT header");
            }
            p = header.data();
        }
        if (!validPrtHeader(p, prt_bytes, cfg.shm_expected_prt_mode)) {
            std::ostringstream os;
            os << "invalid PRT header at pulse " << i;
            return fail(error, os.str());
        }
        pulse_meta.push_back(toPulseMeta(readHeaderSample(p),
                                         cfg.new_protocol_velocity_scale));
        if (!cycle && i + 1U < count) {
            input.seekg(static_cast<std::streamoff>(prt_bytes - kHeaderBytes),
                        std::ios::cur);
            if (!input) return fail(error, "seek failure between mechanical PRT headers");
        }
    }
    return !pulse_meta.empty() || fail(error, "mechanical echo contains no PRT");
}

bool makeMechanicalCpiList(const std::vector<PulseMeta>& pulse_meta,
                           const MechanicalScanConfig& cfg,
                           MechanicalScanRuntime& runtime,
                           std::string* error)
{
    runtime = MechanicalScanRuntime();
    runtime.pulse_meta = pulse_meta;
    if (cfg.cpi_pulse_count <= 0 || cfg.cpi_step_pulse <= 0) {
        return fail(error, "cpi_pulse_count and cpi_step_pulse must be positive");
    }
    if (!(cfg.max_cpi_angle_span_deg >= 0.0) ||
        !std::isfinite(cfg.max_cpi_angle_span_deg) ||
        !(cfg.scan_direction_deadband_deg >= 0.0) ||
        !std::isfinite(cfg.scan_direction_deadband_deg) ||
        cfg.scan_direction_confirm_pulses <= 0) {
        return fail(error, "invalid mechanical scan angle/direction configuration");
    }
    if (pulse_meta.empty()) return fail(error, "no PulseMeta available for CPI slicing");

    const std::vector<ScanSegment> segments = findScanSegments(pulse_meta, cfg);
    int scan_id = 0;
    for (const ScanSegment& segment : segments) {
        std::size_t begin = segment.begin;
        std::size_t end = segment.end;
        applyEdgeGuard(pulse_meta, cfg, begin, end);
        int window_id = 0;
        std::size_t start = begin;
        for (; start + static_cast<std::size_t>(cfg.cpi_pulse_count) <= end;
             start += static_cast<std::size_t>(cfg.cpi_step_pulse)) {
            MechanicalCpiWindow w = buildWindow(
                pulse_meta, cfg, scan_id, window_id++, start,
                static_cast<std::size_t>(cfg.cpi_pulse_count), segment.direction);
            const int index = static_cast<int>(runtime.windows.size());
            if (w.valid) runtime.processing_window_indices.push_back(index);
            runtime.windows.push_back(w);
        }
        if (start < end) {
            runtime.windows.push_back(buildWindow(
                pulse_meta, cfg, scan_id, window_id, start, end - start,
                segment.direction));
        }
        ++scan_id;
    }
    if (runtime.processing_window_indices.empty()) {
        return fail(error, "mechanical scan produced no valid full CPI window");
    }
    return true;
}

bool writeMechanicalScanDiagnostics(const Config& cfg,
                                    const MechanicalScanRuntime& runtime,
                                    std::string* error)
{
    if (!ensureDirectory(cfg.result_add)) {
        return fail(error, "cannot create mechanical diagnostic directory: " +
                           cfg.result_add);
    }
    const std::string prt_path = joinPath(cfg.result_add, "mechanical_scan_prt_meta.csv");
    const bool prt_has_header = nonEmptyFile(prt_path);
    std::ofstream prt(prt_path.c_str(), std::ios::out | std::ios::app);
    if (!prt) return fail(error, "cannot create " + prt_path);
    if (!prt_has_header) {
        prt << "prt_id,utc,lat_deg,lon_deg,height_m,velocity_e_mps,velocity_n_mps,"
               "velocity_u_mps,heading_deg,servo_azimuth_deg,servo_elevation_deg,"
               "scan_center_azimuth_deg,scan_center_elevation_deg,scan_speed_az_deg_s,"
               "scan_extent_az_deg,prt_counter,counter_continuous,utc_monotonic\n";
    }
    prt << std::setprecision(17);
    for (std::size_t i = 0; i < runtime.pulse_meta.size(); ++i) {
        const PulseMeta& p = runtime.pulse_meta[i];
        const bool counter_ok = i == 0U || p.prt_counter ==
            static_cast<std::uint32_t>(runtime.pulse_meta[i - 1U].prt_counter + 1U);
        const bool utc_ok = i == 0U || p.utc >= runtime.pulse_meta[i - 1U].utc;
        prt << i << ',' << p.utc << ',' << p.lat_deg << ',' << p.lon_deg << ','
            << p.height_m << ',' << p.velocity_e_mps << ',' << p.velocity_n_mps << ','
            << p.velocity_u_mps << ',' << p.heading_deg << ','
            << p.servo_azimuth_deg << ',' << p.servo_elevation_deg << ','
            << p.scan_center_azimuth_deg << ',' << p.scan_center_elevation_deg << ','
            << p.scan_speed_az_deg_s << ',' << p.scan_extent_az_deg << ','
            << p.prt_counter << ',' << (counter_ok ? 1 : 0) << ','
            << (utc_ok ? 1 : 0) << '\n';
    }
    if (!prt) return fail(error, "failed while writing " + prt_path);

    const std::string cpi_path = joinPath(cfg.result_add, "mechanical_cpi_manifest.csv");
    const bool cpi_has_header = nonEmptyFile(cpi_path);
    std::ofstream cpi(cpi_path.c_str(), std::ios::out | std::ios::app);
    if (!cpi) return fail(error, "cannot create " + cpi_path);
    if (!cpi_has_header) {
        cpi << "scan_id,window_id,pulse_start,pulse_count,utc_start,utc_center,utc_end,"
               "az_start_deg,az_center_deg,az_end_deg,az_span_deg,elevation_center_deg,"
               "scan_direction,valid,invalid_reason\n";
    }
    cpi << std::setprecision(17);
    for (const MechanicalCpiWindow& w : runtime.windows) {
        cpi << w.scan_id << ',' << w.window_id << ',' << w.pulse_start << ','
            << w.pulse_count << ',' << w.utc_start << ',' << w.utc_center << ','
            << w.utc_end << ',' << w.az_start_deg << ',' << w.az_center_deg << ','
            << w.az_end_deg << ',' << w.az_span_deg << ','
            << w.elevation_center_deg << ',' << w.scan_direction << ','
            << (w.valid ? 1 : 0) << ',' << w.invalid_reason << '\n';
        if (!w.valid && w.invalid_reason == "angle_span_exceeds_limit") {
            std::cerr << "[mechanical][WARN] scan=" << w.scan_id
                      << " window=" << w.window_id
                      << " angle_span_deg=" << w.az_span_deg
                      << " exceeds max=" << cfg.mechanical_scan.max_cpi_angle_span_deg
                      << std::endl;
        }
    }
    return static_cast<bool>(cpi) || fail(error, "failed while writing " + cpi_path);
}

bool prepareMechanicalScanRuntime(Config& cfg, std::string* error)
{
    if (cfg.scan_mode != ScanMode::Mechanical) {
        cfg.mechanical_scan_runtime.reset();
        return true;
    }
    std::vector<PulseMeta> pulse_meta;
    if (!readMechanicalPulseMeta(cfg, pulse_meta, error)) return false;
    std::shared_ptr<MechanicalScanRuntime> runtime(new MechanicalScanRuntime());
    if (!makeMechanicalCpiList(pulse_meta, cfg.mechanical_scan, *runtime, error)) {
        return false;
    }
    int scan_id_base = 0;
    if (cfg.mechanical_scan.acquisition_scan_prt_count > 0 &&
        !pulse_meta.empty()) {
        // The protocol PRT counter is the most direct scan identity and is
        // shared by file and SHM paths.  Prefer it over acquisition_cycle_id,
        // whose first observed value is 0 in the production receiver.
        scan_id_base = static_cast<int>(
            pulse_meta.front().prt_counter /
            static_cast<std::uint32_t>(
                cfg.mechanical_scan.acquisition_scan_prt_count));
    } else if (cfg.echo_cycle_view.valid()) {
        scan_id_base = static_cast<int>(std::min<std::uint64_t>(
            static_cast<std::uint64_t>(std::numeric_limits<int>::max()),
            cfg.echo_cycle_view.acquisition_cycle_id));
    }
    for (MechanicalCpiWindow& window : runtime->windows) {
        window.scan_id += scan_id_base;
    }
    if (!writeMechanicalScanDiagnostics(cfg, *runtime, error)) return false;
    cfg.mechanical_scan_runtime = runtime;
    // The established signal chain allocates all slow-time matrices from
    // these three fields. In mechanical mode the physical processing unit is
    // the configured CPI, so bind them once before FFT/CUDA plan creation.
    cfg.pulse_num = cfg.mechanical_scan.cpi_pulse_count;
    cfg.read_pulse_num = cfg.mechanical_scan.cpi_pulse_count;
    cfg.read_pulse_offset = 0;
    cfg.process_pulse_num = cfg.mechanical_scan.cpi_pulse_count;
    return true;
}

bool writeMechanicalDetectionResults(
    const Config& cfg,
    const std::vector<GMTIOutput::DetectionCsvRecord>& detections,
    std::string* error)
{
    if (cfg.scan_mode != ScanMode::Mechanical) return true;
    if (!ensureDirectory(cfg.result_add)) {
        return fail(error, "cannot create mechanical diagnostic directory: " +
                           cfg.result_add);
    }
    const std::string path = joinPath(
        cfg.result_add, "mechanical_detection_results.csv");
    const bool has_header = nonEmptyFile(path);
    std::ofstream out(path.c_str(), std::ios::out | std::ios::app);
    if (!out) return fail(error, "cannot create " + path);
    if (!has_header) {
        out << "scan_mode,scan_id,window_id,beam_id,pulse_start,pulse_end,"
               "cpi_center_utc,az_start_deg,az_center_deg,az_end_deg,az_span_deg,"
               "scan_direction,target_doppler_hz,target_azimuth_deg,range_m,"
               "radial_velocity_mps,e,n,lat,lon,amplitude\n";
    }
    out << std::setprecision(17);
    for (const GMTIOutput::DetectionCsvRecord& r : detections) {
        out << scanModeName(r.scan_mode) << ',' << r.scan_id << ','
            << r.window_id << ',' << r.beam_id << ',' << r.pulse_start << ','
            << r.pulse_end << ',' << r.cpi_center_utc << ',' << r.az_start_deg
            << ',' << r.az_center_deg << ',' << r.az_end_deg << ','
            << r.az_span_deg << ',' << r.scan_direction << ',' << r.af_total
            << ',' << r.theta_true_deg << ',' << r.range_m << ','
            << r.radial_velocity_mps << ',' << r.e << ',' << r.n << ','
            << r.lat << ',' << r.lon << ',' << r.amplitude << '\n';
    }
    return static_cast<bool>(out) || fail(error, "failed while writing " + path);
}

const MechanicalCpiWindow* mechanicalWindow(const Config& cfg,
                                             int runtime_window_index)
{
    if (cfg.scan_mode != ScanMode::Mechanical ||
        !cfg.mechanical_scan_runtime || runtime_window_index < 0 ||
        static_cast<std::size_t>(runtime_window_index) >=
            cfg.mechanical_scan_runtime->windows.size()) {
        return nullptr;
    }
    return &cfg.mechanical_scan_runtime->windows[
        static_cast<std::size_t>(runtime_window_index)];
}
