#include "stage2_config.h"
#include "stage2_lfm_forward.h"
#include "stage2_scene_generator.h"
#include "stage2_validator.h"

#include "../common/SimulationGeometry.h"
#include "../target_injection/channel_impairments.h"
#include "../target_injection/lfm_echo_generator.h"
#include "../target_injection/radar_geometry.h"
#include "../target_injection/truth_writer.h"
#include "dbs/NewProtocolLayout.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <random>
#include <sstream>
#include <vector>

using namespace gmti::stage2;
using namespace gmti::target_injection;

namespace {

void makeDirs(const std::string &root)
{
    ensureDir(root);
    ensureDir(joinPath(root, "config"));
    ensureDir(joinPath(root, "data"));
    ensureDir(joinPath(root, "truth"));
    ensureDir(joinPath(root, "debug"));
    ensureDir(joinPath(root, "logs"));
    ensureDir(joinPath(root, "reports"));
    ensureDir(joinPath(root, "figures"));
}

int mechanicalPulseCount(const Stage2Config &cfg)
{
    const double duration_sec =
        std::abs(cfg.mechanical_scan.scan_end_deg -
                 cfg.mechanical_scan.scan_start_deg) /
        cfg.mechanical_scan.scan_speed_deg_s;
    return std::max(1, static_cast<int>(std::ceil(
        duration_sec * cfg.radar.prf_hz - 1.0e-12)) + 1);
}

double mechanicalServoAzimuth(const Stage2Config &cfg, int prt_id)
{
    const bool reverse = cfg.mechanical_scan.direction == "reverse";
    const double from = reverse ? cfg.mechanical_scan.scan_end_deg
                                : cfg.mechanical_scan.scan_start_deg;
    const double to = reverse ? cfg.mechanical_scan.scan_start_deg
                              : cfg.mechanical_scan.scan_end_deg;
    const double sign = to >= from ? 1.0 : -1.0;
    const double raw = from + sign * cfg.mechanical_scan.scan_speed_deg_s *
        static_cast<double>(prt_id) / cfg.radar.prf_hz;
    return sign > 0.0 ? std::min(raw, to) : std::max(raw, to);
}

double mechanicalPulseTime(const Stage2Config &cfg, int scan_id, int prt_id)
{
    const int pulses = mechanicalPulseCount(cfg);
    return (static_cast<double>(scan_id) * static_cast<double>(pulses) +
            static_cast<double>(prt_id)) / cfg.radar.prf_hz;
}


std::string periodTag(int period_id)
{
    std::ostringstream ss;
    ss << std::setw(4) << std::setfill('0') << period_id;
    return ss.str();
}

std::string periodDataFile(const std::string &output_dir, int period_id)
{
    return joinPath(
        joinPath(output_dir, "data"),
        "stage2_statistical_newprotocol_period_" + periodTag(period_id) + ".bin");
}

// A reusable background file may contain a full electronic scan while the
// target run intentionally emits only a contiguous beam subset.  Reading it
// as one sequential stream silently pairs the requested target geometry with
// the first source beam (and leaves that source beam's header in the output).
// Build an explicit source-group index from the protocol angle field so every
// copied packet has the same physical beam as the target injection.
struct ReusableBackgroundBeam {
    // Source electronic beam-group index in the complete background file,
    // zero-based.  Keep it explicit in the audit CSV; source_first_packet is
    // the byte-layout identity but is not a semantic beam identifier.
    std::uint64_t source_group = 0U;
    std::uint64_t first_packet = 0U;
    double source_theta_deg = std::numeric_limits<double>::quiet_NaN();
    std::uint32_t source_first_prt_counter = 0U;
};

bool buildReusableBackgroundBeamIndex(
    const std::string &path,
    const RadarConfig &radar,
    int beam_begin,
    int beam_end,
    std::vector<ReusableBackgroundBeam> &selected,
    std::string &err)
{
    selected.clear();
    if (beam_begin < 0 || beam_end <= beam_begin) {
        err = "invalid requested electronic beam subset for reusable background";
        return false;
    }
    const std::size_t channel_count = static_cast<std::size_t>(
        std::max(2, radar.new_protocol_channel_count));
    const std::string iq_type = radar.iq_data_type.empty()
        ? "float32" : radar.iq_data_type;
    const std::uint64_t packet_bytes = static_cast<std::uint64_t>(
        gmti::new_protocol::packetBytes(
            static_cast<std::size_t>(radar.pulse_len), channel_count, iq_type));
    const std::uint64_t pulses_per_beam =
        static_cast<std::uint64_t>(std::max(1, radar.pulse_num));
    if (packet_bytes < gmti::new_protocol::kHeaderBytes ||
        pulses_per_beam == 0U) {
        err = "invalid reusable background packet layout";
        return false;
    }

    std::ifstream probe(path.c_str(), std::ios::binary | std::ios::ate);
    if (!probe) {
        err = "failed to open reusable background input: " + path;
        return false;
    }
    const std::streamoff file_size = probe.tellg();
    if (file_size <= 0 ||
        static_cast<std::uint64_t>(file_size) % packet_bytes != 0U) {
        err = "reusable background size is not an integral PRT count: " + path;
        return false;
    }
    const std::uint64_t packet_count =
        static_cast<std::uint64_t>(file_size) / packet_bytes;
    const std::uint64_t source_beam_count = packet_count / pulses_per_beam;
    if (source_beam_count == 0U || packet_count % pulses_per_beam != 0U) {
        err = "reusable background does not contain complete electronic beam groups: " + path;
        return false;
    }

    const double theta_step = radar.scan_step_deg;
    const double theta_tolerance = std::max(0.011, 0.25 * std::abs(theta_step));
    std::vector<double> source_theta(static_cast<std::size_t>(source_beam_count),
                                     std::numeric_limits<double>::quiet_NaN());
    std::vector<std::uint32_t> source_counter(
        static_cast<std::size_t>(source_beam_count), 0U);
    std::vector<uint8_t> header(gmti::new_protocol::kHeaderBytes, 0U);
    for (std::uint64_t group = 0U; group < source_beam_count; ++group) {
        const std::uint64_t first_packet = group * pulses_per_beam;
        for (std::uint64_t pulse = 0U; pulse < pulses_per_beam; ++pulse) {
            const std::uint64_t packet_index = first_packet + pulse;
            const std::uint64_t byte_offset = packet_index * packet_bytes;
            probe.clear();
            probe.seekg(static_cast<std::streamoff>(byte_offset), std::ios::beg);
            probe.read(reinterpret_cast<char *>(header.data()),
                       static_cast<std::streamsize>(header.size()));
            if (!probe || gmti::new_protocol::loadU32LE(
                    header.data() + gmti::new_protocol::kOffPrtLen) != packet_bytes) {
                err = "invalid reusable background PRT header at packet " +
                      std::to_string(packet_index) + ": " + path;
                return false;
            }
            const gmti::new_protocol::HeaderSample hs =
                gmti::new_protocol::readHeaderSample(header.data());
            if (!std::isfinite(hs.theta_cmd_deg)) {
                err = "reusable background header has non-finite beam angle at group " +
                      std::to_string(group) + ": " + path;
                return false;
            }
            if (pulse == 0U) {
                source_theta[static_cast<std::size_t>(group)] = hs.theta_cmd_deg;
                source_counter[static_cast<std::size_t>(group)] = hs.prt_counter;
            } else if (std::abs(hs.theta_cmd_deg -
                                source_theta[static_cast<std::size_t>(group)]) >
                       theta_tolerance) {
                err = "reusable background beam group has inconsistent theta headers at group " +
                      std::to_string(group) + ": " + path;
                return false;
            }
        }
        for (std::uint64_t prior = 0U; prior < group; ++prior) {
            if (std::abs(source_theta[static_cast<std::size_t>(prior)] -
                         source_theta[static_cast<std::size_t>(group)]) <=
                theta_tolerance) {
                err = "reusable background has duplicate beam theta headers at groups " +
                      std::to_string(prior) + " and " + std::to_string(group) +
                      ": " + path;
                return false;
            }
        }
    }

    std::vector<uint8_t> used(static_cast<std::size_t>(source_beam_count), 0U);
    for (int beam = beam_begin; beam < beam_end; ++beam) {
        const double requested_theta = radar.scan_min_deg +
            radar.scan_step_deg * static_cast<double>(beam);
        if (beam > beam_begin) {
            const double previous_theta = radar.scan_min_deg +
                radar.scan_step_deg * static_cast<double>(beam - 1);
            if (std::abs(previous_theta - requested_theta) <= theta_tolerance) {
                err = "requested reusable background beams have duplicate theta headers";
                return false;
            }
        }
        std::size_t best = 0U;
        double best_error = std::numeric_limits<double>::infinity();
        for (std::size_t group = 0U; group < source_theta.size(); ++group) {
            if (used[group]) {
                continue;
            }
            const double error_deg = std::abs(source_theta[group] - requested_theta);
            if (error_deg < best_error) {
                best_error = error_deg;
                best = group;
            }
        }
        if (!std::isfinite(best_error) || best_error > theta_tolerance) {
            err = "reusable background has no beam header matching requested beam " +
                  std::to_string(beam + 1) + " (theta=" +
            std::to_string(requested_theta) + " deg)";
            return false;
        }
        used[best] = 1U;
        ReusableBackgroundBeam entry;
        entry.source_group = static_cast<std::uint64_t>(best);
        entry.first_packet = static_cast<std::uint64_t>(best) * pulses_per_beam;
        entry.source_theta_deg = source_theta[best];
        entry.source_first_prt_counter = source_counter[best];
        selected.push_back(entry);
    }
    return true;
}

void rewriteReusableBackgroundPrtCounter(
    std::vector<uint8_t> &packet, std::uint32_t prt_counter)
{
    if (packet.size() < gmti::new_protocol::kHeaderBytes) return;
    gmti::new_protocol::storeU32LE(
        packet.data() + gmti::new_protocol::kOffPrtCounter, prt_counter);
    // A legacy consumer also reads this low-byte compatibility field.
    packet[gmti::new_protocol::kOffPrtLowByte] =
        static_cast<uint8_t>(prt_counter & 0xffU);
}

void zeroFpgaPadding(std::vector<uint8_t> &packet,
                     const RadarConfig &radar,
                     int acquired_pulse_len)
{
    if (acquired_pulse_len >= radar.pulse_len) return;
    const std::size_t channel_count = static_cast<std::size_t>(
        std::max(2, radar.new_protocol_channel_count));
    const std::size_t sample_bytes = gmti::new_protocol::sampleBytes(
        channel_count, radar.iq_data_type.empty() ? "float32" : radar.iq_data_type);
    const std::size_t begin = gmti::new_protocol::kHeaderBytes +
        static_cast<std::size_t>(acquired_pulse_len) * sample_bytes;
    if (begin < packet.size()) {
        std::fill(packet.begin() + static_cast<std::ptrdiff_t>(begin),
                  packet.end(), 0U);
    }
}

bool scalePacketPayload(std::vector<uint8_t> &packet,
                        const RadarConfig &radar,
                        double scale,
                        std::string &err)
{
    if (!std::isfinite(scale) || !(scale > 0.0)) {
        err = "background_input_scale must be finite and positive";
        return false;
    }
    if (std::fabs(scale - 1.0) <= 1.0e-12) return true;
    const std::size_t channel_count = static_cast<std::size_t>(
        std::max(2, radar.new_protocol_channel_count));
    const std::string iq_type =
        radar.iq_data_type.empty() ? "float32" : radar.iq_data_type;
    const std::size_t iq_bytes = gmti::new_protocol::bytesPerIq(iq_type);
    for (int n = 0; n < radar.pulse_len; ++n) {
        const std::size_t sample_base =
            gmti::new_protocol::kHeaderBytes +
            static_cast<std::size_t>(n) *
                gmti::new_protocol::sampleBytes(channel_count, iq_type);
        for (std::size_t ch = 0; ch < channel_count; ++ch) {
            const std::size_t off = sample_base +
                gmti::new_protocol::channelOffset(ch + 1U, iq_type);
            const float i = gmti::new_protocol::loadIqAsFloat(&packet[off], iq_type);
            const float q = gmti::new_protocol::loadIqAsFloat(
                &packet[off + iq_bytes], iq_type);
            gmti::new_protocol::storeIqFromFloat(
                &packet[off], iq_type,
                static_cast<float>(static_cast<double>(i) * scale));
            gmti::new_protocol::storeIqFromFloat(
                &packet[off + iq_bytes], iq_type,
                static_cast<float>(static_cast<double>(q) * scale));
        }
    }
    return true;
}

std::string periodConfigFile(const std::string &output_dir, int period_id)
{
    return joinPath(
        joinPath(output_dir, "config"),
        "temp_config_stage2_period_" + periodTag(period_id) + ".xml");
}

struct ChannelRawStats {
    uint64_t complex_sample_count = 0;
    long double sum_power = 0.0L;
    double max_abs_component = 0.0;
    uint64_t zero_complex_count = 0;
    uint64_t saturation_component_count = 0;
    bool has_nan = false;
    bool has_inf = false;
};

void updateRawStats(const std::vector<uint8_t> &packet,
                    const RadarConfig &radar,
                    std::vector<ChannelRawStats> &stats,
                    Stage2Stats &aggregate)
{
    const std::size_t channel_count =
        static_cast<std::size_t>(std::max(2, radar.new_protocol_channel_count));
    const std::string iq_type =
        radar.iq_data_type.empty() ? "float32" : radar.iq_data_type;
    const std::size_t iq_bytes = gmti::new_protocol::bytesPerIq(iq_type);
    if (stats.size() != channel_count) stats.assign(channel_count, ChannelRawStats());

    for (int n = 0; n < radar.pulse_len; ++n) {
        const std::size_t sample_base =
            gmti::new_protocol::kHeaderBytes +
            static_cast<std::size_t>(n) *
                gmti::new_protocol::sampleBytes(channel_count, iq_type);
        for (std::size_t ch = 0; ch < channel_count; ++ch) {
            const std::size_t off =
                sample_base +
                gmti::new_protocol::channelOffset(ch + 1U, iq_type);
            const float i = gmti::new_protocol::loadIqAsFloat(&packet[off], iq_type);
            const float q = gmti::new_protocol::loadIqAsFloat(
                &packet[off + iq_bytes], iq_type);
            ChannelRawStats &s = stats[ch];
            ++s.complex_sample_count;
            if (std::isnan(i) || std::isnan(q)) {
                s.has_nan = true;
                aggregate.has_nan = true;
            }
            if (std::isinf(i) || std::isinf(q)) {
                s.has_inf = true;
                aggregate.has_inf = true;
            }
            if (!std::isfinite(i) || !std::isfinite(q)) continue;
            const double di = static_cast<double>(i);
            const double dq = static_cast<double>(q);
            s.sum_power += static_cast<long double>(di * di + dq * dq);
            const double packet_max =
                std::max(std::fabs(di), std::fabs(dq));
            s.max_abs_component = std::max(
                s.max_abs_component, packet_max);
            aggregate.max_abs_component = std::max(
                aggregate.max_abs_component, packet_max);
            if (i == 0.0f && q == 0.0f) ++s.zero_complex_count;
            if (iq_type == "int16" || iq_type == "signed16" ||
                iq_type == "s16") {
                if (i <= -32768.0f || i >= 32767.0f) {
                    ++s.saturation_component_count;
                }
                if (q <= -32768.0f || q >= 32767.0f) {
                    ++s.saturation_component_count;
                }
            }
        }
    }
}

void writeRawStatsRows(std::ofstream &out,
                       int period_id,
                       uint64_t packet_count,
                       const std::vector<ChannelRawStats> &stats)
{
    for (std::size_t ch = 0; ch < stats.size(); ++ch) {
        const ChannelRawStats &s = stats[ch];
        const double rms = s.complex_sample_count > 0
            ? std::sqrt(static_cast<double>(
                  s.sum_power / static_cast<long double>(s.complex_sample_count)))
            : 0.0;
        const double zero_ratio = s.complex_sample_count > 0
            ? static_cast<double>(s.zero_complex_count) /
                  static_cast<double>(s.complex_sample_count)
            : 0.0;
        out << period_id << ',' << (ch + 1U) << ',' << packet_count << ','
            << s.complex_sample_count << ',' << std::setprecision(17)
            << rms << ',' << s.max_abs_component << ',' << zero_ratio << ','
            << s.saturation_component_count << ','
            << (s.has_nan ? 1 : 0) << ',' << (s.has_inf ? 1 : 0) << '\n';
    }
}

void writePlaceholderEval(const std::string &out_dir)
{
    std::ofstream det(joinPath(joinPath(out_dir, "reports"), "detection_eval.csv").c_str());
    det << "matched_count,missed_count,false_alarm_count,detection_rate,mean_range_error_m,max_range_error_m\n";
    det << "0,0,0,0,0,0\n";
    std::ofstream trk(joinPath(joinPath(out_dir, "reports"), "tracking_eval.csv").c_str());
    trk << "confirmed_track_count,track_start_delay_periods,id_switch_count,track_break_count,track_continuity_rate\n";
    trk << "0,0,0,0,0\n";
}

bool isTargetOnlyMode(const std::string &scene_mode)
{
    return scene_mode == "target_only" ||
           scene_mode == "cooperative_target_only" ||
           scene_mode == "empty_target";
}

bool isContinuousAreaModel(const std::string &model)
{
    return model == "continuous_texture" ||
           model == "continuous_surface" ||
           model == "continuous_grid" ||
           model == "grid_texture";
}

bool sceneModeIncludesAreaClutter(const std::string &scene_mode)
{
    return scene_mode == "area_clutter_only" ||
           scene_mode == "clutter_only" ||
           scene_mode == "full";
}

void applyClutterOverrides(Stage2Config &cfg, const Stage2RunOptions &opt)
{
    if (std::isfinite(opt.clutter_amplitude_scale)) {
        cfg.scene.clutter_amplitude_scale = opt.clutter_amplitude_scale;
    }
    if (opt.area_clutter_scatterer_count >= 0) {
        cfg.scene.area.scatterer_count = opt.area_clutter_scatterer_count;
    }
    if (std::isfinite(opt.area_clutter_mean_power)) {
        cfg.scene.area.mean_power = opt.area_clutter_mean_power;
    }
    if (std::isfinite(opt.area_clutter_texture_sigma)) {
        cfg.scene.area.texture_sigma = opt.area_clutter_texture_sigma;
    }
    if (opt.area_clutter_azimuth_subcell_count > 0) {
        cfg.scene.area.azimuth_subcell_count = opt.area_clutter_azimuth_subcell_count;
    }
    if (opt.strong_scatterer_count >= 0) {
        cfg.scene.strong.count = opt.strong_scatterer_count;
    }
    if (std::isfinite(opt.strong_rcs_db_min)) {
        cfg.scene.strong.rcs_db_min = opt.strong_rcs_db_min;
    }
    if (std::isfinite(opt.strong_rcs_db_max)) {
        cfg.scene.strong.rcs_db_max = opt.strong_rcs_db_max;
    }
    if (opt.line_scatterer_count >= 0) {
        cfg.scene.line.line_count = opt.line_scatterer_count;
    }
    if (opt.line_points_per_line >= 0) {
        cfg.scene.line.points_per_line = opt.line_points_per_line;
    }
    if (std::isfinite(opt.line_rcs_db)) {
        cfg.scene.line.rcs_db = opt.line_rcs_db;
    }
    if (std::isfinite(opt.noise_power)) {
        cfg.scene.noise.noise_power = opt.noise_power;
    }
}

void writeScenarioResolved(const Stage2RunConfig &run,
                           const std::string &output_dir,
                           const std::string &scene_role)
{
    std::ofstream out(joinPath(output_dir, "scenario_resolved.json").c_str());
    out << std::setprecision(12);
    out << "{\n";
    out << "  \"case_id\": \"" << run.case_id << "\",\n";
    out << "  \"output_dir\": \"" << output_dir << "\",\n";
    out << "  \"scene_role\": \"" << scene_role << "\",\n";
    out << "  \"paired_background_output_dir\": \""
        << run.paired_background_output_dir << "\",\n";
    out << "  \"background_input_dir\": \""
        << run.background_input_dir << "\",\n";
    out << "  \"background_input_scale\": "
        << run.background_input_scale << ",\n";
    out << "  \"scene_mode\": \"" << run.scene_mode << "\",\n";
    out << "  \"output_signal_domain_requested\": \""
        << run.output_signal_domain << "\",\n";
    out << "  \"beam_index_base\": " << run.beam_index_base << ",\n";
    out << "  \"scan_mode\": \"" << run.cfg.scan_mode << "\",\n";
    out << "  \"mechanical_scan\": {\n";
    out << "    \"scan_start_deg\": "
        << run.cfg.mechanical_scan.scan_start_deg << ",\n";
    out << "    \"scan_end_deg\": "
        << run.cfg.mechanical_scan.scan_end_deg << ",\n";
    out << "    \"scan_speed_deg_s\": "
        << run.cfg.mechanical_scan.scan_speed_deg_s << ",\n";
    out << "    \"scan_direction\": \""
        << run.cfg.mechanical_scan.direction << "\",\n";
    out << "    \"cpi_pulse_count\": "
        << run.cfg.mechanical_scan.cpi_pulse_count << ",\n";
    out << "    \"cpi_step_pulse\": "
        << run.cfg.mechanical_scan.cpi_step_pulse << ",\n";
    out << "    \"phase_center_rotation_enable\": "
        << (run.cfg.mechanical_scan.phase_center_rotation_enable ? "true" : "false") << ",\n";
    out << "    \"phase_center_mount_angle_deg\": "
        << run.cfg.mechanical_scan.phase_center_mount_angle_deg << ",\n";
    out << "    \"phase_center_rotation_sign\": "
        << run.cfg.mechanical_scan.phase_center_rotation_sign << "\n";
    out << "  },\n";
    // Keep the resolved scenario self-contained: protocol audits must be
    // able to derive the exact packet layout and expected total PRT count
    // without consulting the original hand-written run JSON.
    out << "  \"waveform\": {\n";
    out << "    \"new_protocol_channel_count\": "
        << run.cfg.radar.new_protocol_channel_count << ",\n";
    out << "    \"iq_data_type\": \"" << run.cfg.radar.iq_data_type << "\",\n";
    out << "    \"pulse_len\": " << run.cfg.radar.pulse_len << ",\n";
    out << "    \"acquired_pulse_len\": " << run.cfg.acquired_pulse_len << ",\n";
    out << "    \"pulse_num\": " << run.cfg.radar.pulse_num << ",\n";
    out << "    \"prf_hz\": " << run.cfg.radar.prf_hz << "\n";
    out << "  },\n";
    out << "  \"four_channel_phase_center_mode\": \""
        << run.cfg.sim.four_channel_phase_center_mode << "\",\n";
    out << "  \"scene\": {\n";
    out << "    \"mode\": \"" << run.scene_mode << "\",\n";
    out << "    \"signal_only\": "
        << (run.signal_only ? "true" : "false") << ",\n";
    out << "    \"range_min_m\": " << run.cfg.scene.range_min_m << ",\n";
    out << "    \"range_max_m\": " << run.cfg.scene.range_max_m << ",\n";
    out << "    \"azimuth_min_deg\": " << run.cfg.scene.azimuth_min_deg << ",\n";
    out << "    \"azimuth_max_deg\": " << run.cfg.scene.azimuth_max_deg << ",\n";
    out << "    \"ground_z_m\": " << run.cfg.scene.ground_z_m << ",\n";
    out << "    \"clutter_amplitude_scale\": " << run.cfg.scene.clutter_amplitude_scale << ",\n";
    out << "    \"single_point\": {\n";
    out << "      \"range_m\": " << run.legacy_scene_options.single_scatterer_range_m << ",\n";
    out << "      \"beam_id\": " << run.legacy_scene_options.single_point_beam_id_1based << ",\n";
    out << "      \"expected_bin\": " << run.legacy_scene_options.single_point_expected_bin << ",\n";
    out << "      \"azimuth_deg\": " << run.legacy_scene_options.single_scatterer_azimuth_deg << ",\n";
    out << "      \"amplitude\": " << run.legacy_scene_options.single_scatterer_amplitude << "\n";
    out << "    },\n";
    out << "    \"area_clutter\": {\n";
    out << "      \"enabled\": " << (run.cfg.scene.area.enabled ? "true" : "false") << ",\n";
    out << "      \"model\": \"" << run.cfg.scene.area.model << "\",\n";
    out << "      \"scatterer_count\": " << run.cfg.scene.area.scatterer_count << ",\n";
    out << "      \"mean_power\": " << run.cfg.scene.area.mean_power << ",\n";
    out << "      \"texture_sigma\": " << run.cfg.scene.area.texture_sigma << ",\n";
    out << "      \"spatial_cell_m\": " << run.cfg.scene.area.spatial_cell_m << ",\n";
    out << "      \"temporal_correlation_rho\": "
        << run.cfg.scene.area.temporal_correlation_rho << ",\n";
    const int ctdr_lag_pulses = temporalCtdrLagPulses(
        run.cfg.radar.d_chan_m, run.cfg.radar.prf_hz,
        run.cfg.platform_speed_mps);
    const double correlation_time_ms =
        run.cfg.scene.area.temporal_correlation_rho > 0.0 &&
        run.cfg.scene.area.temporal_correlation_rho < 1.0 &&
        run.cfg.radar.prf_hz > 0.0
            ? -1000.0 / run.cfg.radar.prf_hz /
                  std::log(run.cfg.scene.area.temporal_correlation_rho)
            : std::numeric_limits<double>::quiet_NaN();
    const double rho_at_ctdr_lag =
        std::pow(run.cfg.scene.area.temporal_correlation_rho,
                 static_cast<double>(ctdr_lag_pulses));
    out << "      \"rho_requested\": "
        << run.cfg.scene.area.temporal_correlation_rho << ",\n";
    out << "      \"rho_at_CTDR_lag_theoretical\": ";
    if (std::isfinite(rho_at_ctdr_lag)) out << rho_at_ctdr_lag;
    else out << "null";
    out << ",\n      \"ctdr_lag_pulses\": " << ctdr_lag_pulses << ",\n";
    out << "      \"correlation_time_ms\": ";
    if (std::isfinite(correlation_time_ms)) out << correlation_time_ms;
    else out << "null";
    out << ",\n";
    out << "      \"azimuth_subcell_count\": "
        << run.cfg.scene.area.azimuth_subcell_count << "\n";
    out << "    },\n";
    out << "    \"strong_scatterers\": {\n";
    out << "      \"enabled\": " << (run.cfg.scene.strong.enabled ? "true" : "false") << ",\n";
    out << "      \"count\": " << run.cfg.scene.strong.count << ",\n";
    out << "      \"rcs_db_min\": " << run.cfg.scene.strong.rcs_db_min << ",\n";
    out << "      \"rcs_db_max\": " << run.cfg.scene.strong.rcs_db_max << "\n";
    out << "    },\n";
    out << "    \"line_scatterers\": {\n";
    out << "      \"enabled\": " << (run.cfg.scene.line.enabled ? "true" : "false") << ",\n";
    out << "      \"line_count\": " << run.cfg.scene.line.line_count << ",\n";
    out << "      \"points_per_line\": " << run.cfg.scene.line.points_per_line << ",\n";
    out << "      \"rcs_db\": " << run.cfg.scene.line.rcs_db << "\n";
    out << "    },\n";
    out << "    \"thermal_noise\": {\n";
    out << "      \"enabled\": " << (run.cfg.scene.noise.enabled ? "true" : "false") << ",\n";
    out << "      \"noise_power\": " << run.cfg.scene.noise.noise_power << ",\n";
    out << "      \"include_target_only\": "
        << (run.cfg.scene.noise.include_target_only ? "true" : "false") << "\n";
    out << "    }\n";
    out << "  },\n";
    out << "  \"simulation_subset\": {\n";
    out << "    \"period_start\": " << run.cfg.sim.period_start << ",\n";
    out << "    \"period_count\": " << run.cfg.sim.period_count << ",\n";
    out << "    \"beam_start\": " << run.cfg.sim.beam_start_1based << ",\n";
    out << "    \"beam_count\": " << run.cfg.sim.beam_count << "\n";
    out << "  },\n";
    out << "  \"random\": {\n";
    out << "    \"seed\": " << run.cfg.sim.random_seed << ",\n";
    out << "    \"period_start\": " << run.cfg.sim.period_start << ",\n";
    out << "    \"period_count\": " << run.cfg.sim.period_count << ",\n";
    out << "    \"target_reference_period\": "
        << run.cfg.sim.target_reference_period << "\n";
    out << "  },\n";
    out << "  \"scan\": {\n";
    out << "    \"scan_min_deg\": " << run.cfg.radar.scan_min_deg << ",\n";
    out << "    \"scan_step_deg\": " << run.cfg.radar.scan_step_deg << ",\n";
    out << "    \"beam_count\": " << run.cfg.radar.beam_count << "\n";
    out << "  },\n";
    out << "  \"targets\": [\n";
    for (size_t i = 0; i < run.targets.size(); ++i) {
        const Stage2RunTarget &t = run.targets[i];
        out << "    {\n";
        out << "      \"target_id\": \"" << t.target_id << "\",\n";
        out << "      \"enabled\": " << (t.enabled ? "true" : "false") << ",\n";
        out << "      \"init_type\": \"" << t.init_type << "\",\n";
        out << "      \"beam_id\": " << t.beam_id << ",\n";
        out << "      \"expected_bin\": " << t.expected_bin << ",\n";
        out << "      \"theta_cmd_deg\": " << t.theta_cmd_deg << ",\n";
        out << "      \"azimuth_deg\": " << t.azimuth_deg << ",\n";
        out << "      \"azimuth_offset_deg\": " << t.azimuth_offset_deg << ",\n";
        out << "      \"motion_type\": \"" << t.motion_type << "\",\n";
        out << "      \"ve_mps\": " << t.ve_mps << ",\n";
        out << "      \"vn_mps\": " << t.vn_mps << ",\n";
        out << "      \"amplitude_type\": \"" << t.amplitude_type << "\",\n";
        out << "      \"snr_db\": " << t.snr_db << ",\n";
        out << "      \"snr_db_by_period\": [";
        for (size_t j = 0; j < t.snr_db_by_period.size(); ++j) {
            if (j) out << ", ";
            out << t.snr_db_by_period[j];
        }
        out << "],\n";
        out << "      \"visibility_type\": \"" << t.visibility_type << "\"\n";
        out << "    }" << (i + 1 < run.targets.size() ? "," : "") << "\n";
    }
    out << "  ]\n";
    out << "}\n";
}

int generateStage2Data(const Stage2RunConfig &run)
{
    Stage2Config cfg = run.cfg;
    Stage2RunOptions scene_opt = run.use_legacy_scene_options
        ? run.legacy_scene_options
        : Stage2RunOptions();
    scene_opt.output_dir = run.output_dir;
    scene_opt.scene_mode = run.scene_mode;
    scene_opt.target_enabled = std::any_of(
        run.targets.begin(), run.targets.end(),
        [](const Stage2RunTarget &target) { return target.enabled; });

    std::string err;
    makeDirs(run.output_dir);
    const bool write_paired_background =
        !run.paired_background_output_dir.empty();
    const bool reuse_background = !run.background_input_dir.empty();
    if (write_paired_background) {
        makeDirs(run.paired_background_output_dir);
    }
    writeScenarioResolved(run, run.output_dir, "target_plus_background");

    ScattererList scatterers;
    if (!reuse_background) {
        // Scene geometry uses a dedicated RNG.  Thermal noise is seeded per
        // packet below, so generating one period by itself produces the same
        // noise as the same period inside a multi-period run.
        std::mt19937 scene_rng(cfg.sim.random_seed);
        scatterers = generateScene(cfg, scene_opt, scene_rng);
        if (!writeSceneTruth(joinPath(run.output_dir, "truth"), cfg, scatterers, err)) {
            std::cerr << "[stage2][ERR] " << err << "\n";
            return 1;
        }
        if (write_paired_background &&
            !writeSceneTruth(
                joinPath(run.paired_background_output_dir, "truth"),
                cfg, scatterers, err)) {
            std::cerr << "[stage2][ERR] " << err << "\n";
            return 1;
        }
        if (write_paired_background) {
            writeScenarioResolved(
                run, run.paired_background_output_dir, "empty_background_c_plus_n");
        }
    }

    TargetGlobalConfig global = run.global;
    global.amplitude_mode = cfg.target.amplitude_mode;
    global.target_snr_db = cfg.target.target_snr_db;

    std::vector<TargetConfig> targets;
    for (size_t i = 0; i < run.targets.size(); ++i) {
        if (run.targets[i].enabled) {
            targets.push_back(run.targets[i].target);
        }
    }
    const bool continuous_area_active =
        sceneModeIncludesAreaClutter(run.scene_mode) &&
        cfg.scene.area.enabled && isContinuousAreaModel(cfg.scene.area.model);
    // Decide from the components that were actually materialized, rather than
    // from enabled flags alone.  In particular, point_target_only produces a
    // raw scatterer while the default area config may still say "enabled".
    const bool has_raw_echo_components = !scatterers.empty() || !targets.empty();
    if (run.output_signal_domain == "range_compressed" &&
        has_raw_echo_components) {
        std::cerr << "[stage2][ERR] range_compressed output is incompatible "
                  << "with enabled raw target/scatterer components\n";
        return 1;
    }
    const bool output_is_precompressed =
        continuous_area_active && !has_raw_echo_components &&
        run.output_signal_domain != "raw_lfm";
    const bool continuous_area_raw_lfm =
        continuous_area_active && !output_is_precompressed;

    const size_t packet_bytes = gmti::new_protocol::packetBytes(
        static_cast<size_t>(cfg.radar.pulse_len),
        static_cast<size_t>(std::max(2, cfg.radar.new_protocol_channel_count)),
        cfg.radar.iq_data_type.empty() ? "float32" : cfg.radar.iq_data_type);
    std::vector<uint8_t> packet(packet_bytes, 0U);

    // One physical file per simulation period.  This removes file-period
    // offset ambiguity and lets every file be processed with local period
    // index 0 while retaining the absolute period id in packet UTC/truth.
    const std::string data_manifest_file =
        joinPath(joinPath(run.output_dir, "data"), "period_files.csv");
    std::ofstream data_manifest(data_manifest_file.c_str());
    if (!data_manifest) {
        std::cerr << "[stage2][ERR] failed to open period file manifest\n";
        return 1;
    }
    data_manifest
        << "period_id,file,packet_count,packet_bytes,file_bytes,"
           "first_prt_counter,last_prt_counter,config_file\n";

    const std::string raw_stats_file =
        joinPath(joinPath(run.output_dir, "reports"),
                 "period_raw_generation_stats.csv");
    std::ofstream raw_stats(raw_stats_file.c_str());
    if (!raw_stats) {
        std::cerr << "[stage2][ERR] failed to open period raw stats\n";
        return 1;
    }
    raw_stats
        << "period_id,channel,packet_count,complex_sample_count,"
           "rms_complex,max_abs_component,zero_complex_ratio,"
           "saturation_component_count,has_nan,has_inf\n";

    std::ofstream background_data_manifest;
    std::ofstream background_raw_stats;
    const std::string background_data_manifest_file = write_paired_background
        ? joinPath(joinPath(run.paired_background_output_dir, "data"),
                   "period_files.csv")
        : std::string();
    const std::string background_raw_stats_file = write_paired_background
        ? joinPath(joinPath(run.paired_background_output_dir, "reports"),
                   "period_raw_generation_stats.csv")
        : std::string();
    if (write_paired_background) {
        background_data_manifest.open(background_data_manifest_file.c_str());
        background_raw_stats.open(background_raw_stats_file.c_str());
        if (!background_data_manifest || !background_raw_stats) {
            std::cerr << "[stage2][ERR] failed to open paired background reports\n";
            return 1;
        }
        background_data_manifest
            << "period_id,file,packet_count,packet_bytes,file_bytes,"
               "first_prt_counter,last_prt_counter,config_file\n";
        background_raw_stats
            << "period_id,channel,packet_count,complex_sample_count,"
               "rms_complex,max_abs_component,zero_complex_ratio,"
               "saturation_component_count,has_nan,has_inf\n";
    }

    std::vector<std::string> period_data_files;
    std::vector<std::string> background_period_data_files;

    TruthWriter target_truth;
    const bool write_target_truth = run.truth_output && !targets.empty();
    if (write_target_truth) {
        target_truth.setCaseId(run.case_id);
        if (!target_truth.open(joinPath(run.output_dir, "truth"), err)) {
            std::cerr << "[stage2][ERR] " << err << "\n";
            return 1;
        }
    }
    std::ofstream target_amplitude_ledger;
    if (write_target_truth) {
        target_amplitude_ledger.open(
            joinPath(run.output_dir, "truth/target_injection_amplitudes.csv").c_str());
        if (!target_amplitude_ledger) {
            std::cerr << "[stage2][ERR] failed to open target amplitude ledger\n";
            return 1;
        }
        target_amplitude_ledger
            << "period_id,beam_id_0based,beam_id_1based,pulse_id,time_sec,"
               "target_id,target_name,range_sample_float,range_sample_int,"
               "range_m,target_azimuth_deg,theta_cmd_deg,angle_error_deg,"
               "beam_gain,local_background_rms,target_amplitude,snr_db,"
               "injection_enabled,injected_sample_count\n";
    }
    const bool mechanical_mode = cfg.scan_mode == "mechanical";
    std::ofstream mechanical_prt_truth;
    std::ofstream mechanical_cpi_truth;
    if (write_target_truth && mechanical_mode) {
        const std::string truth_dir = joinPath(run.output_dir, "truth");
        mechanical_prt_truth.open(
            joinPath(truth_dir, "truth_targets_by_prt.csv").c_str());
        mechanical_cpi_truth.open(
            joinPath(truth_dir, "truth_targets_by_cpi.csv").c_str());
        if (!mechanical_prt_truth || !mechanical_cpi_truth) {
            std::cerr << "[stage2][ERR] failed to open mechanical target truth CSVs\n";
            return 1;
        }
        mechanical_prt_truth
            << "target_id,scan_id,prt_id,utc,servo_azimuth_deg,"
               "servo_elevation_deg,target_azimuth_deg,angle_offset_deg,"
               "antenna_gain_db,range_m,doppler_hz,radial_velocity_mps,"
               "relative_radial_velocity_mps,visible\n";
        mechanical_cpi_truth
            << "target_id,scan_id,window_id,pulse_start,pulse_end,utc_center,"
               "cpi_az_start_deg,cpi_az_center_deg,cpi_az_end_deg,cpi_az_span_deg,"
               "target_azimuth_deg,angle_offset_deg,range_m,doppler_hz,"
               "radial_velocity_mps,relative_radial_velocity_mps,visible\n";
    }

    Stage2Stats stats;
    Stage2Stats background_stats;
    std::ofstream impairment_truth;
    if (cfg.impairments.enabled) {
        const std::string truth_dir = joinPath(run.output_dir, "truth");
        impairment_truth.open(joinPath(truth_dir, "channel_impairment_truth.csv").c_str());
        impairment_truth << "case_id,period_id,beam_id,pulse_id,applied,relative_gain,"
            "relative_phase_deg,effective_shift_samples,sample_clock_error_ppm,"
            "added_noise_sigma_ch1,added_noise_sigma_ch2,channel_dropped,"
            "saturated_sample_count\n";
        std::ofstream manifest(joinPath(truth_dir, "channel_impairment_manifest.json").c_str());
        manifest << channelImpairmentConfigJson(cfg.impairments);
    }
    const auto t0 = std::chrono::steady_clock::now();
    // Keep packet sequence numbers reproducible when a contiguous subset of
    // periods is regenerated.  A split run for periods N..M must carry the
    // same PRT counters as those periods in a full N=0 run; resetting to zero
    // changes downstream time/track association semantics even when the echo
    // samples themselves are identical.
    const int counter_beam_begin =
        std::max(0, cfg.sim.beam_start_1based - 1);
    const int counter_beam_end = cfg.sim.beam_count > 0
        ? std::min(cfg.radar.beam_count,
                   counter_beam_begin + cfg.sim.beam_count)
        : cfg.radar.beam_count;
    const uint64_t packets_per_period = mechanical_mode
        ? static_cast<uint64_t>(mechanicalPulseCount(cfg))
        : static_cast<uint64_t>(std::max(0, counter_beam_end - counter_beam_begin)) *
              static_cast<uint64_t>(cfg.radar.pulse_num);
    const uint64_t initial_prt_counter =
        static_cast<uint64_t>(std::max(0, cfg.sim.period_start)) *
        packets_per_period;
    if (initial_prt_counter > std::numeric_limits<uint32_t>::max()) {
        std::cerr << "[stage2][ERR] initial PRT counter exceeds uint32 range\n";
        return 1;
    }
    uint32_t prt_counter = static_cast<uint32_t>(initial_prt_counter);
    for (int pp = 0; pp < cfg.sim.period_count; ++pp) {
        const int period_id = cfg.sim.period_start + pp;
        const int beam_begin = std::max(0, cfg.sim.beam_start_1based - 1);
        const int beam_end = cfg.sim.beam_count > 0
            ? std::min(cfg.radar.beam_count, beam_begin + cfg.sim.beam_count)
            : cfg.radar.beam_count;
        const int period_pulse_count = mechanical_mode
            ? mechanicalPulseCount(cfg) : cfg.radar.pulse_num;
        const uint64_t expected_period_packets = mechanical_mode
            ? static_cast<uint64_t>(period_pulse_count)
            : static_cast<uint64_t>(std::max(0, beam_end - beam_begin)) *
                  static_cast<uint64_t>(cfg.radar.pulse_num);
        const std::string data_file = periodDataFile(run.output_dir, period_id);
        const std::string config_file = periodConfigFile(run.output_dir, period_id);
        std::ofstream out(data_file.c_str(),
                          std::ios::binary | std::ios::trunc);
        if (!out) {
            std::cerr << "[stage2][ERR] failed to open period output data: "
                      << data_file << "\n";
            return 1;
        }
        const std::string background_data_file = write_paired_background
            ? periodDataFile(run.paired_background_output_dir, period_id)
            : std::string();
        const std::string background_config_file = write_paired_background
            ? periodConfigFile(run.paired_background_output_dir, period_id)
            : std::string();
        std::ofstream background_out;
        std::ifstream background_in;
        const std::string background_input_file = reuse_background
            ? periodDataFile(run.background_input_dir, period_id)
            : std::string();
        if (reuse_background) {
            background_in.open(background_input_file.c_str(), std::ios::binary);
            if (!background_in) {
                std::cerr << "[stage2][ERR] failed to open background input data: "
                          << background_input_file << "\n";
                return 1;
            }
        }

        std::vector<ReusableBackgroundBeam> reusable_beams;
        if (reuse_background && !mechanical_mode) {
            if (!buildReusableBackgroundBeamIndex(
                    background_input_file, cfg.radar, beam_begin, beam_end,
                    reusable_beams, err)) {
                std::cerr << "[stage2][ERR] " << err << "\n";
                return 1;
            }
            if (reusable_beams.size() !=
                    static_cast<std::size_t>(std::max(0, beam_end - beam_begin))) {
                std::cerr << "[stage2][ERR] reusable background beam index size mismatch\n";
                return 1;
            }
        }

        std::ofstream reusable_mapping;
        if (reuse_background && !mechanical_mode) {
            const std::string mapping_path = joinPath(
                joinPath(run.output_dir, "reports"),
                "background_reuse_mapping.csv");
            reusable_mapping.open(mapping_path.c_str(), std::ios::out |
                                  (pp == 0 ? std::ios::trunc : std::ios::app));
            if (!reusable_mapping) {
                std::cerr << "[stage2][ERR] failed to open background reuse mapping: "
                          << mapping_path << "\n";
                return 1;
            }
            if (pp == 0) {
                reusable_mapping
                    << "period_id,requested_beam_id,requested_theta_deg,"
                       "source_group,source_first_packet,source_theta_deg,"
                       "output_first_packet,output_first_counter,"
                       "source_first_prt_counter,output_first_prt_counter,"
                       "theta_error_deg,mapping_status\n";
            }
            for (int beam = beam_begin; beam < beam_end; ++beam) {
                const ReusableBackgroundBeam &entry = reusable_beams[
                    static_cast<std::size_t>(beam - beam_begin)];
                const double requested_theta = cfg.radar.scan_min_deg +
                    cfg.radar.scan_step_deg * static_cast<double>(beam);
                const uint64_t output_first_packet =
                    static_cast<uint64_t>(beam - beam_begin) *
                        static_cast<uint64_t>(period_pulse_count);
                const uint64_t output_first_prt_counter =
                    static_cast<uint64_t>(prt_counter) +
                    static_cast<uint64_t>(beam - beam_begin) *
                        static_cast<uint64_t>(period_pulse_count);
                reusable_mapping << period_id << ',' << (beam + 1) << ','
                    << std::setprecision(17) << requested_theta << ','
                    << entry.source_group << ',' << entry.first_packet << ','
                    << entry.source_theta_deg << ','
                    << output_first_packet << ',' << output_first_prt_counter << ','
                    << entry.source_first_prt_counter << ','
                    << output_first_prt_counter << ','
                    << std::abs(entry.source_theta_deg - requested_theta) << ','
                    << "matched\n";
                std::cout << "[stage2][background-reuse] period=" << period_id
                          << " requested_beam=" << (beam + 1)
                          << " source_first_packet=" << entry.first_packet
                          << " source_theta=" << entry.source_theta_deg
                          << " requested_theta=" << requested_theta << '\n';
            }
        }
        if (write_paired_background) {
            background_out.open(background_data_file.c_str(),
                                std::ios::binary | std::ios::trunc);
            if (!background_out) {
                std::cerr << "[stage2][ERR] failed to open paired background data: "
                          << background_data_file << "\n";
                return 1;
            }
        }

        const uint32_t first_prt_counter = prt_counter;
        const uint64_t packets_before = stats.packets_written;
        const uint64_t background_packets_before =
            background_stats.packets_written;
        std::vector<ChannelRawStats> period_raw_stats(
            static_cast<std::size_t>(
                std::max(2, cfg.radar.new_protocol_channel_count)));
        std::vector<ChannelRawStats> background_period_raw_stats(
            static_cast<std::size_t>(
                std::max(2, cfg.radar.new_protocol_channel_count)));
        std::vector<PulseTruth> period_mechanical_truth;
        if (mechanical_mode && write_target_truth) {
            period_mechanical_truth.reserve(
                static_cast<std::size_t>(period_pulse_count) * targets.size());
        }

        const int loop_beam_begin = mechanical_mode ? 0 : beam_begin;
        const int loop_beam_end = mechanical_mode ? 1 : beam_end;
        for (int b = loop_beam_begin; b < loop_beam_end; ++b) {
            if (reuse_background && !mechanical_mode) {
                const std::size_t selected_index =
                    static_cast<std::size_t>(b - beam_begin);
                const std::uint64_t source_packet =
                    reusable_beams[selected_index].first_packet;
                background_in.clear();
                background_in.seekg(static_cast<std::streamoff>(
                    source_packet * static_cast<std::uint64_t>(packet_bytes)),
                    std::ios::beg);
                if (!background_in) {
                    std::cerr << "[stage2][ERR] failed to seek reusable background beam "
                              << (b + 1) << " in " << background_input_file << "\n";
                    return 1;
                }
            }
            for (int m = 0; m < period_pulse_count; ++m) {
                const double t = mechanical_mode
                    ? mechanicalPulseTime(cfg, period_id, m)
                    : pulseTimeSec(cfg.radar, period_id, b, m);
                const double theta = mechanical_mode
                    ? mechanicalServoAzimuth(cfg, m)
                    : cfg.radar.scan_min_deg +
                          cfg.radar.scan_step_deg * static_cast<double>(b);
                if (reuse_background) {
                    background_in.read(
                        reinterpret_cast<char *>(&packet[0]),
                        static_cast<std::streamsize>(packet.size()));
                    if (!background_in) {
                        std::cerr << "[stage2][ERR] background input packet read failed: "
                                  << background_input_file
                                  << " period=" << period_id
                                  << " beam=" << b
                                  << " pulse=" << m << "\n";
                        return 1;
                    }
                    // Preserve the source packet's physical header fields;
                    // only the output transport counter (and its legacy
                    // low-byte compatibility field) belongs to this run.
                    if (!mechanical_mode) {
                        rewriteReusableBackgroundPrtCounter(packet, prt_counter);
                    }
                    if (!scalePacketPayload(
                            packet, cfg.radar, run.background_input_scale, err)) {
                        std::cerr << "[stage2][ERR] " << err << "\n";
                        return 1;
                    }
                } else {
                    if (mechanical_mode) {
                        fillMechanicalPacketHeader(
                            packet, cfg.radar, global, prt_counter, t, theta, 0.0,
                            0.5 * (cfg.mechanical_scan.scan_start_deg +
                                   cfg.mechanical_scan.scan_end_deg),
                            0.0, cfg.mechanical_scan.scan_speed_deg_s,
                            std::abs(cfg.mechanical_scan.scan_end_deg -
                                     cfg.mechanical_scan.scan_start_deg));
                    } else {
                        fillZeroPacketHeader(
                            packet, cfg.radar, global, prt_counter, t, theta);
                    }

                    if (continuous_area_active) {
                        const double grid_reference_time = mechanical_mode
                            ? mechanicalPulseTime(cfg, period_id,
                                                  period_pulse_count / 2)
                            : std::numeric_limits<double>::quiet_NaN();
                        if (!addContinuousAreaClutter(
                                packet, cfg.radar, global, cfg.scene,
                                cfg.sim.random_seed, cfg.sim.period_start,
                                period_id, b, m,
                                continuous_area_raw_lfm, stats, err,
                                mechanical_mode ? t : std::numeric_limits<double>::quiet_NaN(),
                                mechanical_mode ? theta : std::numeric_limits<double>::quiet_NaN(),
                                grid_reference_time)) {
                            std::cerr << "[stage2][ERR] " << err << "\n";
                            return 1;
                        }
                    }

                    // Continuous models omit only generated `area` entries.
                    // Any materialized strong/line/single point is an
                    // independent raw LFM component.
                    if (!scatterers.empty()) {
                        addScatterersToPacket(
                            packet, cfg.radar, global, scatterers,
                            period_id, b, m,
                            global.beam_gain_threshold, stats,
                            mechanical_mode ? t : std::numeric_limits<double>::quiet_NaN(),
                            mechanical_mode ? theta : std::numeric_limits<double>::quiet_NaN());
                    }

                    if (cfg.scene.noise.enabled &&
                        (cfg.scene.noise.include_target_only ||
                         (run.scene_mode != "point_target_only" &&
                          !isTargetOnlyMode(run.scene_mode)))) {
                        // Packet-addressed seeding removes dependence on
                        // generation order and makes split-period output
                        // reproducible.
                        std::seed_seq seed{
                            cfg.sim.random_seed,
                            static_cast<uint32_t>(period_id),
                            static_cast<uint32_t>(b),
                            static_cast<uint32_t>(m),
                            0x4e4f4953U
                        };
                        std::mt19937 packet_noise_rng(seed);
                        addThermalNoise(
                            packet, cfg.radar, cfg.scene.noise.noise_power,
                            packet_noise_rng, stats);
                    }
                }

                // Freeze the clutter/noise-only packet before adding targets.
                // The same bytes are used by injectOnePulse() for local-RMS
                // normalization and, when requested, are written as the
                // paired empty C+N scene.  No second scene RNG or regeneration
                // is involved.
                std::vector<uint8_t> target_background_packet;
                if (!targets.empty() || write_paired_background || reuse_background) {
                    target_background_packet = packet;
                }
                if (write_paired_background) {
                    std::vector<uint8_t> background_packet =
                        target_background_packet;
                    zeroFpgaPadding(
                        background_packet, cfg.radar, cfg.acquired_pulse_len);
                    updateRawStats(
                        background_packet, cfg.radar,
                        background_period_raw_stats, background_stats);
                    background_out.write(
                        reinterpret_cast<const char *>(&background_packet[0]),
                        static_cast<std::streamsize>(background_packet.size()));
                    if (!background_out) {
                        std::cerr << "[stage2][ERR] paired background write failed: "
                                  << background_data_file << "\n";
                        return 1;
                    }
                    ++background_stats.packets_written;
                }
                // A paired S-only run must use the same target amplitude as
                // its C+N and S+C+N partners.  Target amplitude is normalized
                // from target_background_packet above; clearing only the wire
                // payload afterwards preserves that reference while removing
                // every clutter/noise contribution from the emitted packet.
                if (run.signal_only) {
                    std::fill(packet.begin() + static_cast<std::ptrdiff_t>(
                                  gmti::new_protocol::kHeaderBytes),
                              packet.end(), 0U);
                }
                for (size_t ti = 0; ti < targets.size(); ++ti) {
                    TargetConfig period_target = targets[ti];
                    if (!period_target.target_snr_db_by_period.empty()) {
                        const int schedule_index = period_id - period_target.start_period;
                        if (schedule_index < 0 || schedule_index >=
                                static_cast<int>(period_target.target_snr_db_by_period.size())) {
                            std::cerr << "[stage2][ERR] target SCNR schedule index out of range: "
                                      << period_target.name << " period=" << period_id << "\n";
                            return 1;
                        }
                        period_target.target_snr_db =
                            period_target.target_snr_db_by_period[
                                static_cast<size_t>(schedule_index)];
                    }
                    PulseTruth pt =
                        injectOnePulse(
                            packet, cfg.radar, global, period_target,
                            period_id, mechanical_mode ? -1 : b, m,
                            &target_background_packet,
                            mechanical_mode ? t : std::numeric_limits<double>::quiet_NaN(),
                            mechanical_mode ? theta : std::numeric_limits<double>::quiet_NaN(),
                            !mechanical_mode,
                            mechanical_mode ? period_id : -1,
                        mechanical_mode ? m : -1,
                        mechanical_mode ? 0.0 : std::numeric_limits<double>::quiet_NaN());
                    if (pt.injection_enabled) {
                        ++stats.target_pulses_injected;
                        stats.target_samples_injected +=
                            static_cast<uint64_t>(pt.injected_sample_count);
                    }
                    if (write_target_truth) target_truth.writePulse(pt);
                    if (target_amplitude_ledger && pt.injection_enabled) {
                        target_amplitude_ledger
                            << pt.period_id << ',' << pt.beam_id << ','
                            << (pt.beam_id + 1) << ',' << pt.pulse_id << ','
                            << std::setprecision(17) << t << ','
                            << pt.target_id << ',' << pt.target_name << ','
                            << pt.geom.range_sample_float << ','
                            << pt.geom.range_sample_int << ','
                            << pt.geom.range_m << ','
                            << pt.geom.target_azimuth_deg << ','
                            << pt.geom.theta_cmd_deg << ','
                            << pt.geom.angle_error_deg << ','
                            << pt.beam_gain << ','
                            << pt.local_background_rms << ','
                            << pt.target_amplitude << ',' << pt.snr_db << ','
                            << (pt.injection_enabled ? 1 : 0) << ','
                            << pt.injected_sample_count << '\n';
                    }
                    if (mechanical_mode && write_target_truth) {
                        period_mechanical_truth.push_back(pt);
                        const double gain_db = 20.0 * std::log10(
                            std::max(1.0e-300, pt.beam_gain));
                        mechanical_prt_truth
                            << pt.target_name << ',' << period_id << ',' << m << ','
                            << std::setprecision(17) << t << ',' << theta << ",0,"
                            << pt.geom.target_azimuth_deg << ','
                            << pt.geom.angle_error_deg << ',' << gain_db << ','
                            << pt.geom.range_m << ',' << pt.af_total_truth_hz << ','
                            << pt.target_vr_self_mps << ','
                            << pt.geom.radial_velocity_mps << ','
                            << (pt.injection_enabled ? 1 : 0) << '\n';
                    }
                }

                const ChannelImpairmentRealization impairment =
                    applyChannelImpairments(
                        packet, cfg.radar, cfg.impairments,
                        period_id, b, m, theta,
                        cfg.sim.random_seed);
                if (impairment_truth) {
                    impairment_truth
                        << run.case_id << ',' << period_id << ','
                        << (mechanical_mode ? -1 : b + 1) << ',' << m << ','
                        << (impairment.applied ? 1 : 0) << ','
                        << std::setprecision(17)
                        << impairment.relative_gain << ','
                        << impairment.relative_phase_deg << ','
                        << impairment.effective_shift_samples << ','
                        << impairment.sample_clock_error_ppm << ','
                        << impairment.added_noise_sigma_ch1 << ','
                        << impairment.added_noise_sigma_ch2 << ','
                        << (impairment.channel_dropped ? 1 : 0) << ','
                        << impairment.saturated_sample_count << '\n';
                }

                zeroFpgaPadding(packet, cfg.radar, cfg.acquired_pulse_len);

                updateRawStats(
                    packet, cfg.radar, period_raw_stats, stats);
                out.write(
                    reinterpret_cast<const char *>(&packet[0]),
                    static_cast<std::streamsize>(packet.size()));
                if (!out) {
                    std::cerr << "[stage2][ERR] write failed: "
                              << data_file << "\n";
                    return 1;
                }
                ++stats.packets_written;
                ++prt_counter;
            }
        }

        if (mechanical_mode && write_target_truth) {
            const int count = cfg.mechanical_scan.cpi_pulse_count;
            const int step = cfg.mechanical_scan.cpi_step_pulse;
            int window_id = 0;
            for (int start = 0; start + count <= period_pulse_count;
                 start += step, ++window_id) {
                const int center = start + count / 2;
                const int end = start + count - 1;
                for (std::size_t ti = 0; ti < targets.size(); ++ti) {
                    const std::size_t center_index =
                        static_cast<std::size_t>(center) * targets.size() + ti;
                    if (center_index >= period_mechanical_truth.size()) continue;
                    const PulseTruth &pt = period_mechanical_truth[center_index];
                    bool visible = false;
                    for (int p = start; p <= end; ++p) {
                        const std::size_t index =
                            static_cast<std::size_t>(p) * targets.size() + ti;
                        if (index < period_mechanical_truth.size() &&
                            period_mechanical_truth[index].injection_enabled) {
                            visible = true;
                            break;
                        }
                    }
                    const double az_start = mechanicalServoAzimuth(cfg, start);
                    const double az_center = mechanicalServoAzimuth(cfg, center);
                    const double az_end = mechanicalServoAzimuth(cfg, end);
                    mechanical_cpi_truth
                        << pt.target_name << ',' << period_id << ',' << window_id
                        << ',' << start << ',' << end << ','
                        << std::setprecision(17)
                        << mechanicalPulseTime(cfg, period_id, center) << ','
                        << az_start << ',' << az_center << ',' << az_end << ','
                        << std::abs(az_end - az_start) << ','
                        << pt.geom.target_azimuth_deg << ','
                        << pt.geom.angle_error_deg << ',' << pt.geom.range_m << ','
                        << pt.af_total_truth_hz << ','
                        << pt.target_vr_self_mps << ','
                        << pt.geom.radial_velocity_mps << ','
                        << (visible ? 1 : 0) << '\n';
                }
            }
        }

        out.flush();
        if (reuse_background) {
            background_in.close();
        }
        const std::streamoff file_bytes = out.tellp();
        out.close();
        const uint64_t period_packets =
            stats.packets_written - packets_before;
        const uint64_t expected_bytes =
            period_packets * static_cast<uint64_t>(packet_bytes);
        if (period_packets != expected_period_packets ||
            file_bytes < 0 ||
            static_cast<uint64_t>(file_bytes) != expected_bytes) {
            std::cerr
                << "[stage2][ERR] period file size/count mismatch"
                << " period=" << period_id
                << " packets=" << period_packets
                << " expected_packets=" << expected_period_packets
                << " bytes=" << file_bytes
                << " expected_bytes=" << expected_bytes << "\n";
            return 1;
        }

        if (write_paired_background) {
            background_out.flush();
            const std::streamoff background_file_bytes = background_out.tellp();
            background_out.close();
            const uint64_t background_period_packets =
                background_stats.packets_written - background_packets_before;
            const uint64_t background_expected_bytes =
                background_period_packets * static_cast<uint64_t>(packet_bytes);
            if (background_period_packets != expected_period_packets ||
                background_file_bytes < 0 ||
                static_cast<uint64_t>(background_file_bytes) !=
                    background_expected_bytes) {
                std::cerr
                    << "[stage2][ERR] paired background file size/count mismatch"
                    << " period=" << period_id
                    << " packets=" << background_period_packets
                    << " expected_packets=" << expected_period_packets
                    << " bytes=" << background_file_bytes
                    << " expected_bytes=" << background_expected_bytes << "\n";
                return 1;
            }
        }

        Stage2Config period_cfg = cfg;
        period_cfg.sim.period_start = period_id;
        period_cfg.sim.period_count = 1;
        if (!writeStage2OutputConfig(
                period_cfg, config_file, data_file,
                output_is_precompressed, err)) {
            std::cerr << "[stage2][ERR] " << err << "\n";
            return 1;
        }

        period_data_files.push_back(data_file);
        data_manifest
            << period_id << ',' << data_file << ','
            << period_packets << ',' << packet_bytes << ','
            << expected_bytes << ',' << first_prt_counter << ','
            << (period_packets > 0
                    ? static_cast<uint64_t>(first_prt_counter) +
                          period_packets - 1U
                    : static_cast<uint64_t>(first_prt_counter))
            << ',' << config_file << '\n';
        writeRawStatsRows(
            raw_stats, period_id, period_packets, period_raw_stats);
        if (write_paired_background) {
            if (!writeStage2OutputConfig(
                    period_cfg, background_config_file, background_data_file,
                    output_is_precompressed, err)) {
                std::cerr << "[stage2][ERR] " << err << "\n";
                return 1;
            }
            const uint64_t background_period_packets =
                background_stats.packets_written - background_packets_before;
            const uint64_t background_expected_bytes =
                background_period_packets * static_cast<uint64_t>(packet_bytes);
            background_period_data_files.push_back(background_data_file);
            background_data_manifest
                << period_id << ',' << background_data_file << ','
                << background_period_packets << ',' << packet_bytes << ','
                << background_expected_bytes << ',' << first_prt_counter << ','
                << (background_period_packets > 0
                        ? static_cast<uint64_t>(first_prt_counter) +
                              background_period_packets - 1U
                        : static_cast<uint64_t>(first_prt_counter))
                << ',' << background_config_file << '\n';
            writeRawStatsRows(
                background_raw_stats, period_id, background_period_packets,
                background_period_raw_stats);
        }

        std::cout
            << "[stage2][period] id=" << period_id
            << " output=" << data_file
            << " config=" << config_file
            << " packets=" << period_packets
            << " bytes=" << expected_bytes << "\n";
    }
    if (write_target_truth) {
        target_truth.writeSummary();
        target_truth.close();
    }

    const auto t1 = std::chrono::steady_clock::now();
    const double elapsed = std::chrono::duration<double>(t1 - t0).count();
    // Keep the historical XML name as a compatibility alias for period 0
    // (or the first requested period).  Every period also has its own XML.
    if (period_data_files.empty()) {
        std::cerr << "[stage2][ERR] no period data files were generated\n";
        return 1;
    }
    Stage2Config first_period_cfg = cfg;
    first_period_cfg.sim.period_start = cfg.sim.period_start;
    first_period_cfg.sim.period_count = 1;
    if (!writeStage2OutputConfig(
            first_period_cfg,
            joinPath(joinPath(run.output_dir, "config"),
                     "temp_config_stage2_newsystem.xml"),
            period_data_files.front(),
            output_is_precompressed,
            err)) {
        std::cerr << "[stage2][ERR] " << err << "\n";
        return 1;
    }
    if (write_paired_background) {
        if (background_period_data_files.empty()) {
            std::cerr << "[stage2][ERR] no paired background files were generated\n";
            return 1;
        }
        if (!writeStage2OutputConfig(
                first_period_cfg,
                joinPath(joinPath(run.paired_background_output_dir, "config"),
                         "temp_config_stage2_newsystem.xml"),
                background_period_data_files.front(),
                output_is_precompressed,
                err)) {
            std::cerr << "[stage2][ERR] " << err << "\n";
            return 1;
        }
        background_data_manifest.flush();
        background_raw_stats.flush();
    }
    writePlaceholderEval(run.output_dir);
    {
        std::ofstream log(joinPath(joinPath(run.output_dir, "logs"), "simulate_stage2.log").c_str());
        log << "case_id=" << run.case_id << "\n";
        log << "scene_mode=" << run.scene_mode << "\n";
        log << "period_start=" << cfg.sim.period_start << "\n";
        log << "period_count=" << cfg.sim.period_count << "\n";
        log << "data_layout=one_file_per_period\n";
        log << "period_file_manifest=" << data_manifest_file << "\n";
        log << "period_raw_stats=" << raw_stats_file << "\n";
        log << "paired_background_output_dir="
            << (write_paired_background ? run.paired_background_output_dir : "")
            << "\n";
        log << "paired_background_data_manifest="
            << (write_paired_background ? background_data_manifest_file : "")
            << "\n";
        log << "background_input_dir="
            << (reuse_background ? run.background_input_dir : "") << "\n";
        log << "background_input_scale="
            << (reuse_background ? run.background_input_scale : 1.0) << "\n";
        log << "target_amplitude_ledger="
            << (write_target_truth
                    ? joinPath(run.output_dir,
                               "truth/target_injection_amplitudes.csv")
                    : "")
            << "\n";
        log << "scatterers=" << scatterers.size() << "\n";
        log << "targets=" << targets.size() << "\n";
        log << "clutter_amplitude_scale=" << cfg.scene.clutter_amplitude_scale << "\n";
        log << "area_clutter_model=" << cfg.scene.area.model << "\n";
        log << "area_clutter_scatterer_count=" << cfg.scene.area.scatterer_count << "\n";
        log << "continuous_area_packets=" << stats.continuous_area_packets << "\n";
        log << "continuous_area_samples=" << stats.continuous_area_samples << "\n";
        log << "continuous_area_precompressed_packets="
            << stats.continuous_area_precompressed_packets << "\n";
        log << "continuous_area_raw_lfm_packets="
            << stats.continuous_area_raw_lfm_packets << "\n";
        log << "output_signal_domain="
            << (output_is_precompressed ? "range_compressed" : "raw_lfm") << "\n";
        log << "output_signal_domain_requested="
            << run.output_signal_domain << "\n";
        log << "area_clutter_mean_power=" << cfg.scene.area.mean_power << "\n";
        log << "area_clutter_texture_sigma=" << cfg.scene.area.texture_sigma << "\n";
        log << "area_clutter_azimuth_subcell_count="
            << cfg.scene.area.azimuth_subcell_count << "\n";
        log << "strong_scatterer_count=" << cfg.scene.strong.count << "\n";
        log << "strong_rcs_db_min=" << cfg.scene.strong.rcs_db_min << "\n";
        log << "strong_rcs_db_max=" << cfg.scene.strong.rcs_db_max << "\n";
        log << "line_scatterer_count=" << cfg.scene.line.line_count << "\n";
        log << "line_points_per_line=" << cfg.scene.line.points_per_line << "\n";
        log << "line_rcs_db=" << cfg.scene.line.rcs_db << "\n";
        log << "noise_power=" << cfg.scene.noise.noise_power << "\n";
        log << "packets_written=" << stats.packets_written << "\n";
    }
    if (!writeStage2Report(joinPath(joinPath(run.output_dir, "reports"),
                                    "stage2_simulation_report.md"),
                           cfg, scene_opt, scatterers, stats, elapsed,
                           output_is_precompressed, err)) {
        std::cerr << "[stage2][ERR] " << err << "\n";
        return 1;
    }
    if (!writeTemporalClutterDiagnostics(
            joinPath(joinPath(run.output_dir, "reports"),
                     "temporal_clutter_diagnostics.json"),
            stats.temporal_clutter, err)) {
        std::cerr << "[stage2][ERR] " << err << "\n";
        return 1;
    }

    std::cout << "[stage2] data_layout=one_file_per_period"
              << " period_files=" << period_data_files.size()
              << " manifest=" << data_manifest_file
              << " paired_background="
              << (write_paired_background ? run.paired_background_output_dir : "")
              << " packets=" << stats.packets_written
              << " scatterers=" << scatterers.size()
              << " scatterer_echoes=" << stats.scatterer_echoes
              << " target_pulses=" << stats.target_pulses_injected
              << " elapsed_ms=" << elapsed * 1000.0 << "\n";
    return (stats.has_nan || stats.has_inf) ? 2 : 0;
}

} // namespace

int main(int argc, char **argv)
{
    Stage2RunOptions opt;
    if (!parseStage2CommandLine(argc, argv, opt)) {
        std::cerr << "[stage2][ERR] " << opt.parse_error << "\n";
        return 1;
    }
    std::string err;
    Stage2RunConfig run;
    if (!opt.run_config.empty()) {
        if (!loadStage2RunConfig(opt.run_config, run, err)) {
            std::cerr << "[stage2][ERR] " << err << "\n";
            return 1;
        }
    } else {
        if (!makeLegacyStage2RunConfig(opt, run, err)) {
            std::cerr << "[stage2][ERR] " << err << "\n";
            return 1;
        }
        applyClutterOverrides(run.cfg, opt);
        run.global = makeTargetGlobal(run.cfg);
    }
    if (opt.validate && !validateStage2RunConfig(run, err) && !run.targets.empty()) {
        std::cerr << "[stage2][ERR] " << err << "\n";
        return 1;
    }
    return generateStage2Data(run);
}
