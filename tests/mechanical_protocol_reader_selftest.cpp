#include "dbs/NewProtocolReader.hpp"
#include "mechanical_scan.hpp"

#include <algorithm>
#include <cmath>
#include <complex>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

namespace {

Config makeMechanicalConfig(const std::string& echo_path,
                            const std::string& result_path)
{
    Config cfg;
    cfg.GMTI_Data_new = echo_path;
    cfg.result_add = result_path;
    cfg.scan_mode = ScanMode::Mechanical;
    cfg.INFO_Type = 1;
    cfg.shm_expected_prt_mode = 9;
    cfg.iq_data_type = "float32";
    cfg.pulse_len = 512;
    cfg.pulse_num = 130;
    cfg.read_pulse_num = 130;
    cfg.read_pulse_offset = 0;
    cfg.process_pulse_num = 130;
    cfg.new_protocol_channel_count = 4;
    cfg.new_protocol_read_channel_1 = 1;
    cfg.new_protocol_read_channel_2 = 2;
    cfg.enable_four_channel_fusion = true;
    cfg.four_channel_phase_compensation_enable = true;
    cfg.four_channel_fusion_channel_3 = 3;
    cfg.four_channel_fusion_channel_4 = 4;
    cfg.four_channel_fusion_squint_side = 1;
    cfg.four_channel_carrier_phase_sign = -1;
    cfg.four_channel_offsets_m = {{
        {{0.0, -0.255, 0.0}},
        {{0.0, -0.085, 0.0}},
        {{0.0,  0.085, 0.0}},
        {{0.0,  0.255, 0.0}}
    }};
    // Direct Config callers historically used GHz/MHz. The reader accepts
    // these units at its boundary, matching the generated XML fixture.
    cfg.fc = 16.0;
    cfg.fs = 2.0;
    cfg.sample_delay_us = 488.0;
    cfg.mechanical_scan.acquisition_scan_prt_count = 131;
    cfg.mechanical_scan.cpi_pulse_count = 130;
    cfg.mechanical_scan.cpi_step_pulse = 130;
    cfg.mechanical_scan.max_cpi_angle_span_deg = 2.0;
    cfg.mechanical_scan.use_actual_servo_angle = true;
    cfg.mechanical_scan.allow_scan_reverse = true;
    cfg.mechanical_scan.scan_direction_deadband_deg = 0.05;
    cfg.mechanical_scan.scan_direction_confirm_pulses = 3;
    cfg.mechanical_scan.phase_center_rotation_enable = true;
    cfg.mechanical_scan.phase_center_mount_angle_deg = 0.0;
    cfg.mechanical_scan.phase_center_rotation_sign = 1;
    return cfg;
}

bool sameMetadata(const std::vector<double>& a, const std::vector<double>& b)
{
    if (a.size() != b.size()) return false;
    for (std::size_t i = 0; i < a.size(); ++i) {
        if (a[i] != b[i]) return false;
    }
    return true;
}

double maxAbsDifference(const std::vector<std::complex<float>>& a,
                        const std::vector<std::complex<float>>& b)
{
    if (a.size() != b.size()) return std::numeric_limits<double>::infinity();
    double maximum = 0.0;
    for (std::size_t i = 0; i < a.size(); ++i) {
        if (!std::isfinite(a[i].real()) || !std::isfinite(a[i].imag()) ||
            !std::isfinite(b[i].real()) || !std::isfinite(b[i].imag())) {
            return std::numeric_limits<double>::infinity();
        }
        maximum = std::max(maximum,
                           static_cast<double>(std::abs(a[i] - b[i])));
    }
    return maximum;
}

double rmsDifference(const std::vector<std::complex<float>>& a,
                     const std::vector<std::complex<float>>& b)
{
    if (a.size() != b.size() || a.empty()) {
        return std::numeric_limits<double>::infinity();
    }
    long double sum = 0.0L;
    for (std::size_t i = 0; i < a.size(); ++i) {
        sum += static_cast<long double>(std::norm(a[i] - b[i]));
    }
    return std::sqrt(static_cast<double>(sum / a.size()));
}

} // namespace

int main(int argc, char** argv)
{
    if (argc < 3) {
        std::cerr << "usage: mechanical_protocol_reader_selftest "
                     "<new_protocol_bin> <result_dir>\n";
        return 2;
    }

    Config cfg = makeMechanicalConfig(argv[1], argv[2]);
    std::string error;
    if (!prepareMechanicalScanRuntime(cfg, &error)) {
        std::cerr << "prepareMechanicalScanRuntime failed: " << error << '\n';
        return 1;
    }

    std::vector<std::complex<float>> direct_left;
    std::vector<std::complex<float>> direct_right;
    std::vector<double> direct_utc;
    std::vector<std::vector<double>> direct_pos;
    double direct_theta = 0.0;
    if (!readPulseBlockNewProtocol(cfg, 0, direct_left, direct_right,
                                   direct_utc, direct_theta, direct_pos)) {
        std::cerr << "direct mechanical CPU reader failed\n";
        return 1;
    }

    NewProtocolGpuInput packed;
    std::vector<double> packed_utc;
    std::vector<std::vector<double>> packed_pos;
    double packed_theta = 0.0;
    if (!readPulseBlockNewProtocolGpuInput(
            cfg, 0, packed, packed_utc, packed_theta, packed_pos)) {
        std::cerr << "mechanical GPU-input packer failed\n";
        return 1;
    }
    std::vector<std::complex<float>> packed_left;
    std::vector<std::complex<float>> packed_right;
    if (!decodeNewProtocolGpuInputCpu(cfg, packed, packed_left, packed_right)) {
        std::cerr << "packed CPU reference decoder failed\n";
        return 1;
    }

    const double packed_left_error = maxAbsDifference(direct_left, packed_left);
    const double packed_right_error = maxAbsDifference(direct_right, packed_right);
    const bool packed_match = packed_left_error <= 1.0e-3 &&
                              packed_right_error <= 1.0e-3 &&
                              sameMetadata(direct_utc, packed_utc) &&
                              std::abs(direct_theta - packed_theta) <= 1.0e-12 &&
                              direct_pos == packed_pos;

    Config legacy = cfg;
    legacy.mechanical_scan.phase_center_rotation_enable = false;
    std::vector<std::complex<float>> legacy_left;
    std::vector<std::complex<float>> legacy_right;
    std::vector<double> legacy_utc;
    std::vector<std::vector<double>> legacy_pos;
    double legacy_theta = 0.0;
    if (!readPulseBlockNewProtocol(legacy, 0, legacy_left, legacy_right,
                                   legacy_utc, legacy_theta, legacy_pos)) {
        std::cerr << "legacy fixed-phase-center A/B reader failed\n";
        return 1;
    }

    const double pose_vs_legacy_left_rms = rmsDifference(direct_left, legacy_left);
    const double pose_vs_legacy_right_rms = rmsDifference(direct_right, legacy_right);
    std::cout << "mechanical_reader_pulses=" << packed.pulse_count << '\n'
              << "mechanical_reader_samples_per_prt=" << packed.samples_per_prt << '\n'
              << "packed_left_max_abs_error=" << packed_left_error << '\n'
              << "packed_right_max_abs_error=" << packed_right_error << '\n'
              << "pose_vs_legacy_left_rms=" << pose_vs_legacy_left_rms << '\n'
              << "pose_vs_legacy_right_rms=" << pose_vs_legacy_right_rms << '\n'
              << "pose_center_servo_azimuth_deg=" << direct_theta << '\n';

    // The production fusion stage estimates one coherent residual phase over
    // the CPI before averaging the two receive rows.  For a noiseless single
    // target this intentionally removes the common pose-vs-legacy phase, so
    // the final fused samples may be identical even though the raw channel
    // phase model differs (covered by mechanical_phase_center_selftest and
    // the independent truth audit).  Keep the A/B values as diagnostics, but
    // make the chain-level acceptance the direct/packed CPU equivalence.
    return packed_match ? 0 : 1;
}
