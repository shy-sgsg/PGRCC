#include "pipe/ShmEchoInput.h"
#include "dbs/NewProtocolLayout.hpp"
#include "dbs/NewProtocolReader.hpp"
#include "mechanical_scan.hpp"
#include "RingBuffer.h"

#include <cmath>
#include <atomic>
#include <chrono>
#include <complex>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <memory>
#include <fcntl.h>
#include <sys/mman.h>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {

std::vector<uint8_t> makePrt(uint32_t counter,
                             std::size_t samples,
                             std::size_t channels)
{
    const std::size_t bytes = gmti::new_protocol::packetBytes(samples, channels, "int16");
    std::vector<uint8_t> p(bytes, 0U);
    std::memset(p.data(), 0x5A, 8U);
    p[gmti::new_protocol::kOffVersion] = 5U;
    gmti::new_protocol::storeU32LE(p.data() + gmti::new_protocol::kOffPrtLen,
                                   static_cast<uint32_t>(bytes));
    gmti::new_protocol::storeF32LE(p.data() + gmti::new_protocol::kOffUtc,
                                   static_cast<float>(counter) * 0.001f);
    gmti::new_protocol::storeU32LE(p.data() + gmti::new_protocol::kOffPrtCounter,
                                   counter);
    p[gmti::new_protocol::kOffPrtLowByte] =
        static_cast<uint8_t>(counter & 0xffU);
    gmti::new_protocol::storeF64LE(p.data() + gmti::new_protocol::kOffLatDeg, 30.0);
    gmti::new_protocol::storeF64LE(p.data() + gmti::new_protocol::kOffLonDeg, 120.0);
    gmti::new_protocol::storeF64LE(p.data() + gmti::new_protocol::kOffHeightM, 1000.0);
    gmti::new_protocol::storeF32LE(p.data() + gmti::new_protocol::kOffVnMps, 20.0f);
    gmti::new_protocol::storeF32LE(p.data() + gmti::new_protocol::kOffVeMps, 100.0f);
    gmti::new_protocol::storeI16LE(
        p.data() + gmti::new_protocol::kOffServoAzimuthDegX100,
        static_cast<int16_t>(counter));
    std::memset(p.data() + gmti::new_protocol::kOffMagicTail, 0x5B, 8U);
    uint8_t* payload = p.data() + gmti::new_protocol::kHeaderBytes;
    for (std::size_t n = 0; n < samples; ++n) {
        for (std::size_t ch = 0; ch < channels; ++ch) {
            const std::size_t off = n * gmti::new_protocol::sampleBytes(channels, "int16") +
                                    ch * gmti::new_protocol::bytesPerChannel("int16");
            gmti::new_protocol::storeI16LE(payload + off,
                static_cast<int16_t>(counter * 100U + n * 10U + ch));
            gmti::new_protocol::storeI16LE(payload + off + 2U,
                static_cast<int16_t>(-static_cast<int>(counter * 100U + n * 10U + ch)));
        }
    }
    return p;
}

std::vector<uint8_t> makeCycle(uint32_t first,
                               std::size_t count,
                               std::size_t samples,
                               std::size_t channels)
{
    std::vector<uint8_t> out;
    for (std::size_t i = 0; i < count; ++i) {
        const std::vector<uint8_t> p = makePrt(first + static_cast<uint32_t>(i),
                                               samples, channels);
        out.insert(out.end(), p.begin(), p.end());
    }
    return out;
}

bool equalComplex(const std::vector<std::complex<float>>& a,
                  const std::vector<std::complex<float>>& b)
{
    if (a.size() != b.size()) return false;
    for (std::size_t i = 0; i < a.size(); ++i) {
        if (a[i] != b[i]) return false;
    }
    return true;
}

bool fullScanLayoutSemanticsTest()
{
    Config cfg;
    cfg.pkg_bytes = 189376;
    cfg.pulse_num = 130;
    cfg.PRF = 1300.0;
    cfg.wavepos_st = 1;
    cfg.wavepos_ed = 61;
    cfg.wavepos_skip = 2; // processing selection must not shrink acquisition
    cfg.new_protocol_file_first_beam = 1;
    cfg.shm_scan_beam_count = 61;
    cfg.shm_expected_prt_mode = 5;

    EchoCycleLayout layout;
    std::string error;
    if (!deriveEchoScanLayout(cfg, layout, error) ||
        layout.scan_first_beam != 1 ||
        layout.scan_beam_count != 61U ||
        layout.processing_beam_count != 31U ||
        layout.prts_per_cycle != 7930U ||
        layout.cycle_bytes != 1501751680U) {
        std::cerr << "61-beam acquisition layout was incorrectly reduced by wavepos_skip: "
                  << error << '\n';
        return false;
    }

    // A complete 1..61 input scan may legitimately feed a one-beam processing
    // selection. The source layout and the algorithm beam list are separate.
    cfg.wavepos_st = 31;
    cfg.wavepos_ed = 31;
    cfg.wavepos_skip = 1;
    if (!deriveEchoScanLayout(cfg, layout, error) ||
        layout.scan_beam_count != 61U ||
        layout.processing_beam_count != 1U ||
        layout.prts_per_cycle != 7930U) {
        std::cerr << "full-scan input plus subset processing layout failed: "
                  << error << '\n';
        return false;
    }

    cfg.wavepos_ed = 62;
    if (deriveEchoScanLayout(cfg, layout, error)) {
        std::cerr << "processing range outside acquisition scan was accepted\n";
        return false;
    }
    return true;
}

bool mechanicalScanLayoutSemanticsTest()
{
    Config cfg;
    cfg.scan_mode = ScanMode::Mechanical;
    cfg.pkg_bytes = 189376;
    cfg.PRF = 1300.0;
    cfg.pulse_num = 130;
    cfg.mechanical_scan.acquisition_scan_prt_count = 7931;
    // These electronic fields must not affect a mechanical acquisition unit.
    cfg.wavepos_st = 31;
    cfg.wavepos_ed = 61;
    cfg.wavepos_skip = 3;
    cfg.shm_scan_beam_count = 61;

    EchoCycleLayout layout;
    std::string error;
    if (!deriveEchoScanLayout(cfg, layout, error) ||
        layout.prts_per_cycle != 7931U ||
        layout.cycle_bytes != 1501941056U ||
        layout.prts_per_beam != 0U || layout.scan_beam_count != 0U ||
        layout.processing_beam_count != 0U) {
        std::cerr << "mechanical SHM layout used synthetic beam dimensions: "
                  << error << '\n';
        return false;
    }
    cfg.mechanical_scan.acquisition_scan_prt_count = 0;
    if (deriveEchoScanLayout(cfg, layout, error) || error.empty()) {
        std::cerr << "mechanical SHM layout accepted an unknown scan boundary\n";
        return false;
    }
    return true;
}

bool mechanicalScanIdFromPrtCounterTest()
{
    const std::size_t samples = 1U;
    const std::size_t channels = 4U;
    const std::vector<uint8_t> cycle = makeCycle(20U, 10U, samples, channels);
    Config cfg;
    cfg.scan_mode = ScanMode::Mechanical;
    cfg.INFO_Type = 1;
    cfg.shm_expected_prt_mode = 5;
    cfg.pulse_len = static_cast<int>(samples);
    cfg.new_protocol_channel_count = static_cast<int>(channels);
    cfg.iq_data_type = "int16";
    cfg.pkg_bytes = static_cast<int>(
        gmti::new_protocol::packetBytes(samples, channels, "int16"));
    cfg.echo_cycle_view.data = cycle.data();
    cfg.echo_cycle_view.size = cycle.size();
    cfg.echo_cycle_view.prt_bytes = static_cast<std::size_t>(cfg.pkg_bytes);
    // Deliberately conflicts with the counter-derived scan ID. Production SHM
    // cycle IDs are 0-based, while PRT counter/acquisition size is the shared
    // file+SHM source of truth whenever it is available.
    cfg.echo_cycle_view.acquisition_cycle_id = 1U;
    cfg.mechanical_scan.acquisition_scan_prt_count = 10;
    cfg.mechanical_scan.cpi_pulse_count = 5;
    cfg.mechanical_scan.cpi_step_pulse = 5;
    cfg.mechanical_scan.max_cpi_angle_span_deg = 1.0;
    cfg.mechanical_scan.scan_direction_deadband_deg = 0.005;
    cfg.result_add = "/tmp/gmti_mechanical_scan_id_selftest_" +
                     std::to_string(static_cast<long long>(getpid()));

    std::string error;
    const bool prepared = prepareMechanicalScanRuntime(cfg, &error);
    const bool correct = prepared && cfg.mechanical_scan_runtime &&
        cfg.mechanical_scan_runtime->processing_window_indices.size() == 2U &&
        cfg.mechanical_scan_runtime->windows.front().scan_id == 2;
    std::remove((cfg.result_add + "/mechanical_scan_prt_meta.csv").c_str());
    std::remove((cfg.result_add + "/mechanical_cpi_manifest.csv").c_str());
    rmdir(cfg.result_add.c_str());
    if (!correct) {
        std::cerr << "mechanical scan ID did not follow first PRT counter: "
                  << error << '\n';
        return false;
    }
    return true;
}

bool physicalBeamOffsetAcrossFullScanTest()
{
    const std::size_t samples = 1U;
    const std::size_t channels = 4U;
    const std::size_t pulses_per_beam = 2U;
    const std::vector<uint8_t> scan = makeCycle(
        0U, 61U * pulses_per_beam, samples, channels);

    Config cfg;
    cfg.INFO_Type = 1;
    cfg.shm_expected_prt_mode = 5;
    cfg.pulse_len = static_cast<int>(samples);
    cfg.pulse_num = static_cast<int>(pulses_per_beam);
    cfg.read_pulse_num = static_cast<int>(pulses_per_beam);
    cfg.read_pulse_offset = 0;
    cfg.skip_az_num = 0;
    cfg.new_protocol_channel_count = static_cast<int>(channels);
    cfg.new_protocol_read_channel_1 = 1;
    cfg.new_protocol_read_channel_2 = 3;
    cfg.new_protocol_file_first_beam = 1;
    cfg.iq_data_type = "int16";

    EchoCycleView view;
    view.data = scan.data();
    view.size = scan.size();
    view.prt_bytes = gmti::new_protocol::packetBytes(samples, channels, "int16");
    const int beams[] = {1, 31, 61};
    for (int beam : beams) {
        std::vector<std::complex<float>> d1, d2;
        std::vector<double> utc;
        std::vector<std::vector<double>> pos;
        double theta = 0.0;
        if (!readPulseBlockNewProtocol(cfg, view, beam, d1, d2, utc, theta, pos) ||
            d1.size() != pulses_per_beam || utc.size() != pulses_per_beam) {
            std::cerr << "cannot read physical beam " << beam
                      << " from a complete 61-beam memory scan\n";
            return false;
        }
        const int first_prt = (beam - 1) * static_cast<int>(pulses_per_beam);
        if (d1[0].real() != static_cast<float>(first_prt * 100) ||
            d1[0].imag() != static_cast<float>(-first_prt * 100)) {
            std::cerr << "physical beam offset mismatch for beam " << beam << '\n';
            return false;
        }
    }
    return true;
}

bool parserAssemblerAndReaderTest()
{
    const std::size_t samples = 4U;
    const std::size_t channels = 4U;
    const std::size_t prt_bytes = gmti::new_protocol::packetBytes(samples, channels, "int16");
    const std::vector<uint8_t> cycle = makeCycle(0U, 6U, samples, channels);
    EchoCycleLayout layout;
    layout.prt_bytes = prt_bytes;
    layout.prts_per_beam = 3U;
    layout.scan_beam_count = 2U;
    layout.processing_beam_count = 2U;
    layout.prts_per_cycle = 6U;
    layout.cycle_bytes = cycle.size();
    layout.expected_prt_mode = 5U;
    layout.counter_phase = 0U;
    layout.cycle_timeout_ms = 1000;

    EchoCycleAssembler assembler(layout);
    PrtStreamParser parser(prt_bytes, 127U, 5U);
    std::shared_ptr<Config> cfg(new Config());
    cfg->config_generation = 7U;
    // This fixture deliberately exercises the legacy SAR-style mode=5
    // parser path; production V1.5 WAGMTI defaults to mode=9.
    cfg->shm_expected_prt_mode = 5;

    const std::size_t chunks[] = {1U, 7U, 31U, 2U, 127U, 5U, 79U};
    std::size_t off = 0U;
    std::size_t ci = 0U;
    while (off < cycle.size()) {
        const std::size_t n = std::min(chunks[ci++ % 7U], cycle.size() - off);
        parser.feed(cycle.data() + off, n,
            [&](const uint8_t* p, std::size_t len, uint32_t counter, double utc) {
                assembler.acceptPrt(p, len, counter, utc, cfg);
            });
        off += n;
    }

    EchoCycleAssembler::Lease lease;
    if (!assembler.waitReady(lease, 50) || !lease.valid()) {
        std::cerr << "normal split stream did not produce a ready cycle\n";
        return false;
    }
    if (lease.view.size != cycle.size() ||
        std::memcmp(lease.view.data, cycle.data(), cycle.size()) != 0 ||
        lease.metadata.first_prt_counter != 0U ||
        lease.metadata.last_prt_counter != 5U ||
        lease.metadata.config_generation != 7U) {
        std::cerr << "assembled cycle bytes/metadata mismatch\n";
        return false;
    }

    Config read_cfg;
    read_cfg.INFO_Type = 1;
    read_cfg.shm_expected_prt_mode = 5;
    read_cfg.pulse_len = static_cast<int>(samples);
    read_cfg.pulse_num = 3;
    read_cfg.read_pulse_num = 3;
    read_cfg.read_pulse_offset = 0;
    read_cfg.skip_az_num = 0;
    read_cfg.new_protocol_channel_count = static_cast<int>(channels);
    read_cfg.new_protocol_read_channel_1 = 1;
    read_cfg.new_protocol_read_channel_2 = 3;
    read_cfg.new_protocol_file_first_beam = 1;
    read_cfg.iq_data_type = "int16";

    const std::string path = "/tmp/gmti_shm_reader_selftest_" +
                             std::to_string(static_cast<long long>(getpid())) + ".bin";
    {
        std::ofstream out(path.c_str(), std::ios::binary);
        out.write(reinterpret_cast<const char*>(cycle.data()),
                  static_cast<std::streamsize>(cycle.size()));
    }
    read_cfg.GMTI_Data_new = path;
    std::vector<std::complex<float>> f1, f2, m1, m2;
    std::vector<double> futc, mutc;
    std::vector<std::vector<double>> fpos, mpos;
    double ftheta = 0.0, mtheta = 0.0;
    const bool file_ok = readPulseBlockNewProtocol(
        read_cfg, 2, f1, f2, futc, ftheta, fpos);
    const bool memory_ok = readPulseBlockNewProtocol(
        read_cfg, lease.view, 2, m1, m2, mutc, mtheta, mpos);
    std::remove(path.c_str());
    if (!file_ok || !memory_ok || !equalComplex(f1, m1) || !equalComplex(f2, m2) ||
        futc != mutc || fpos != mpos || ftheta != mtheta) {
        std::cerr << "file/memory new-protocol decode mismatch\n";
        return false;
    }
    assembler.release(lease, 1.0);
    return true;
}

bool fourChannelFusionReaderTest()
{
    const std::size_t samples = 3U;
    const std::size_t channels = 4U;
    std::vector<uint8_t> cycle = makeCycle(0U, 2U, samples, channels);
    Config cfg;
    cfg.INFO_Type = 1;
    cfg.shm_expected_prt_mode = 5;
    cfg.pulse_len = static_cast<int>(samples);
    cfg.pulse_num = 2;
    cfg.read_pulse_num = 2;
    cfg.read_pulse_offset = 0;
    cfg.skip_az_num = 0;
    cfg.new_protocol_channel_count = 4;
    cfg.new_protocol_read_channel_1 = 1;
    cfg.new_protocol_read_channel_2 = 2;
    cfg.new_protocol_file_first_beam = 1;
    cfg.iq_data_type = "int16";
    // Match the runtime Config representation after readXmlParam(): Hz.
    // This catches accidental double conversion by the channel-fusion path.
    cfg.fs = 60.0e6;
    cfg.fc = 16.0e9;
    cfg.sample_delay_us = 488.0;
    cfg.enable_four_channel_fusion = true;
    cfg.four_channel_fusion_channel_3 = 3;
    cfg.four_channel_fusion_channel_4 = 4;
    for (auto &offset : cfg.four_channel_offsets_m) offset = {{0.0, 0.0, 0.0}};

    EchoCycleView view;
    view.data = cycle.data();
    view.size = cycle.size();
    view.prt_bytes = gmti::new_protocol::packetBytes(samples, channels, "int16");
    std::vector<std::complex<float>> left, right;
    std::vector<double> utc;
    std::vector<std::vector<double>> pos;
    double theta = 0.0;
    if (!readPulseBlockNewProtocol(cfg, view, 1, left, right, utc, theta, pos)) {
        std::cerr << "four-channel fusion reader failed\n";
        return false;
    }
    NewProtocolGpuInput packed;
    std::vector<double> packed_utc;
    std::vector<std::vector<double>> packed_pos;
    double packed_theta = 0.0;
    std::vector<std::complex<float>> packed_left, packed_right;
    if (!readPulseBlockNewProtocolGpuInput(
            cfg, view, 1, packed, packed_utc, packed_theta, packed_pos) ||
        !decodeNewProtocolGpuInputCpu(cfg, packed, packed_left, packed_right) ||
        !equalComplex(left, packed_left) || !equalComplex(right, packed_right) ||
        utc != packed_utc || pos != packed_pos || theta != packed_theta) {
        std::cerr << "packed new-protocol CPU reference mismatch\n";
        return false;
    }
    const std::string packed_path = "/tmp/gmti_packed_reader_selftest_" +
        std::to_string(static_cast<long long>(getpid())) + ".bin";
    {
        std::ofstream out(packed_path.c_str(), std::ios::binary);
        out.write(reinterpret_cast<const char *>(cycle.data()),
                  static_cast<std::streamsize>(cycle.size()));
    }
    cfg.GMTI_Data_new = packed_path;
    NewProtocolGpuInput packed_file;
    std::vector<double> packed_file_utc;
    std::vector<std::vector<double>> packed_file_pos;
    std::vector<std::complex<float>> packed_file_left, packed_file_right;
    double packed_file_theta = 0.0;
    const bool packed_file_ok = readPulseBlockNewProtocolGpuInput(
        cfg, 1, packed_file, packed_file_utc, packed_file_theta, packed_file_pos);
    const bool packed_file_decode_ok = packed_file_ok &&
        decodeNewProtocolGpuInputCpu(
            cfg, packed_file, packed_file_left, packed_file_right);
    std::remove(packed_path.c_str());
    if (!packed_file_decode_ok || !equalComplex(left, packed_file_left) ||
        !equalComplex(right, packed_file_right) || utc != packed_file_utc ||
        pos != packed_file_pos || theta != packed_file_theta) {
        std::cerr << "packed file-mode new-protocol CPU reference mismatch\n";
        return false;
    }
    for (std::size_t pulse = 0; pulse < 2U; ++pulse) {
        for (std::size_t n = 0; n < samples; ++n) {
            const float base = static_cast<float>(pulse * 100U + n * 10U);
            const std::size_t i = pulse * samples + n;
            if (left[i] != std::complex<float>(base + 1.0f, -(base + 1.0f)) ||
                right[i] != std::complex<float>(base + 2.0f, -(base + 2.0f))) {
                std::cerr << "four-channel normalized pair average mismatch\n";
                return false;
            }
        }
    }
    return true;
}

bool fourChannelPhysicalPhaseCompensationTest()
{
    const std::size_t samples = 1U;
    const std::size_t channels = 4U;
    std::vector<uint8_t> cycle = makeCycle(0U, 1U, samples, channels);
    Config cfg;
    cfg.INFO_Type = 1;
    cfg.shm_expected_prt_mode = 5;
    cfg.pulse_len = 1;
    cfg.pulse_num = 1;
    cfg.read_pulse_num = 1;
    cfg.read_pulse_offset = 0;
    cfg.skip_az_num = 0;
    cfg.new_protocol_channel_count = 4;
    cfg.new_protocol_read_channel_1 = 1;
    cfg.new_protocol_read_channel_2 = 2;
    cfg.new_protocol_file_first_beam = 1;
    cfg.iq_data_type = "int16";
    // Runtime representation after XML parsing is Hz.
    cfg.fs = 60.0e6;
    cfg.fc = 16.0e9;
    cfg.sample_delay_us = 488.0;
    cfg.enable_four_channel_fusion = true;
    cfg.four_channel_fusion_channel_3 = 3;
    cfg.four_channel_fusion_channel_4 = 4;
    cfg.four_channel_fusion_squint_side = 1;
    cfg.four_channel_carrier_phase_sign = -1;
    cfg.four_channel_offsets_m = {{{{-0.085, 0.0, 0.10}},
                                   {{ 0.085, 0.0, 0.10}},
                                   {{-0.085, 0.0,-0.10}},
                                   {{ 0.085, 0.0,-0.10}}}};

    const double c = 299792458.0;
    const double range = 0.5 * c * cfg.sample_delay_us * 1.0e-6;
    const double height = 1000.0;
    const double horizontal = std::sqrt(range * range - height * height);
    const std::array<double, 3> target{{0.0, -horizontal, -height}};
    const auto path = [&target](const std::array<double, 3>& offset) {
        const double dx = target[0] - offset[0];
        const double dy = target[1] - offset[1];
        const double dz = target[2] - offset[2];
        return std::sqrt(dx * dx + dy * dy + dz * dz);
    };
    const double compensation_phase =
        static_cast<double>(cfg.four_channel_carrier_phase_sign) *
        2.0 * M_PI / (c / cfg.fc) *
        (path(cfg.four_channel_offsets_m[0]) - path(cfg.four_channel_offsets_m[2]));
    const double source_phase = -compensation_phase;
    const int16_t amplitude = 10000;
    const int16_t source_i = static_cast<int16_t>(std::lround(amplitude * std::cos(source_phase)));
    const int16_t source_q = static_cast<int16_t>(std::lround(amplitude * std::sin(source_phase)));
    uint8_t* payload = cycle.data() + gmti::new_protocol::kHeaderBytes;
    const std::size_t channel_bytes = gmti::new_protocol::bytesPerChannel("int16");
    for (std::size_t ch = 0; ch < channels; ++ch) {
        const bool source = ch >= 2U;
        gmti::new_protocol::storeI16LE(payload + ch * channel_bytes,
            source ? source_i : amplitude);
        gmti::new_protocol::storeI16LE(payload + ch * channel_bytes + 2U,
            source ? source_q : 0);
    }

    EchoCycleView view;
    view.data = cycle.data();
    view.size = cycle.size();
    view.prt_bytes = gmti::new_protocol::packetBytes(samples, channels, "int16");
    std::vector<std::complex<float>> left, right;
    std::vector<double> utc;
    std::vector<std::vector<double>> pos;
    double theta = 0.0;
    if (!readPulseBlockNewProtocol(cfg, view, 1, left, right, utc, theta, pos) ||
        left.size() != 1U || right.size() != 1U ||
        std::abs(left[0] - std::complex<float>(amplitude, 0.0f)) > 2.0f ||
        std::abs(right[0] - std::complex<float>(amplitude, 0.0f)) > 2.0f) {
        std::cerr << "four-channel physical phase compensation mismatch: left="
                  << (left.empty() ? std::complex<float>() : left[0])
                  << " right=" << (right.empty() ? std::complex<float>() : right[0])
                  << '\n';
        return false;
    }
    return true;
}

bool multiPeriodFileOffsetTest()
{
    const std::size_t samples = 1U;
    const std::size_t channels = 4U;
    const std::size_t pulses = 2U;
    const std::size_t beams = 3U;
    const std::vector<uint8_t> data = makeCycle(
        0U, 2U * beams * pulses, samples, channels);
    const std::string path = "/tmp/gmti_multi_period_reader_" +
        std::to_string(static_cast<long long>(getpid())) + ".bin";
    {
        std::ofstream out(path.c_str(), std::ios::binary);
        out.write(reinterpret_cast<const char *>(data.data()),
                  static_cast<std::streamsize>(data.size()));
    }
    Config cfg;
    cfg.INFO_Type = 1;
    cfg.shm_expected_prt_mode = 5;
    cfg.GMTI_Data_new = path;
    cfg.pulse_len = static_cast<int>(samples);
    cfg.pulse_num = static_cast<int>(pulses);
    cfg.read_pulse_num = static_cast<int>(pulses);
    cfg.read_pulse_offset = 0;
    cfg.skip_az_num = 0;
    cfg.wavepos_ed = static_cast<int>(beams);
    cfg.new_protocol_channel_count = static_cast<int>(channels);
    cfg.new_protocol_read_channel_1 = 1;
    cfg.new_protocol_read_channel_2 = 2;
    cfg.new_protocol_file_first_beam = 1;
    cfg.new_protocol_file_scan_beam_count = static_cast<int>(beams);
    cfg.new_protocol_file_period_index = 1;
    cfg.iq_data_type = "int16";
    std::vector<std::complex<float>> d1, d2;
    std::vector<double> utc;
    std::vector<std::vector<double>> pos;
    double theta = 0.0;
    const bool ok = readPulseBlockNewProtocol(cfg, 2, d1, d2, utc, theta, pos);
    std::remove(path.c_str());
    const int expected_first_counter = static_cast<int>(beams * pulses + pulses);
    if (!ok || d1.empty() ||
        d1[0].real() != static_cast<float>(expected_first_counter * 100)) {
        std::cerr << "multi-period file offset selected the wrong scan\n";
        return false;
    }
    return true;
}

bool corruptionAndResyncTest()
{
    const std::size_t prt_bytes = gmti::new_protocol::packetBytes(2U, 4U, "int16");
    EchoCycleLayout layout;
    layout.prt_bytes = prt_bytes;
    layout.prts_per_beam = 3U;
    layout.scan_beam_count = 2U;
    layout.processing_beam_count = 2U;
    layout.prts_per_cycle = 6U;
    layout.cycle_bytes = prt_bytes * 6U;
    layout.expected_prt_mode = 5U;
    layout.counter_phase = 0U;
    layout.cycle_timeout_ms = 1000;
    EchoCycleAssembler assembler(layout);
    PrtStreamParser parser(prt_bytes, 91U, 5U);
    std::shared_ptr<Config> cfg(new Config());

    std::vector<uint8_t> damaged = makeCycle(0U, 6U, 2U, 4U);
    damaged[2U * prt_bytes] = 0U;
    const std::vector<uint8_t> clean = makeCycle(6U, 6U, 2U, 4U);
    damaged.insert(damaged.end(), clean.begin(), clean.end());
    for (std::size_t off = 0; off < damaged.size();) {
        const std::size_t n = std::min<std::size_t>(73U, damaged.size() - off);
        parser.feed(damaged.data() + off, n,
            [&](const uint8_t* p, std::size_t len, uint32_t counter, double utc) {
                assembler.acceptPrt(p, len, counter, utc, cfg);
            });
        off += n;
    }
    EchoCycleAssembler::Lease lease;
    if (!assembler.waitReady(lease, 50) || lease.metadata.first_prt_counter != 6U ||
        std::memcmp(lease.view.data, clean.data(), clean.size()) != 0 ||
        parser.invalidPrtCount() == 0U || parser.resyncCount() == 0U ||
        assembler.metrics().prt_gap_count == 0U) {
        std::cerr << "corrupt stream was not dropped/resynchronized as a whole cycle\n";
        return false;
    }
    assembler.release(lease, 1.0);
    return true;
}

bool partialFirstScanAtBeamBoundaryTest()
{
    const std::size_t samples = 2U;
    const std::size_t channels = 4U;
    const std::size_t prt_bytes =
        gmti::new_protocol::packetBytes(samples, channels, "int16");
    EchoCycleLayout layout;
    layout.prt_bytes = prt_bytes;
    layout.prts_per_beam = 2U;
    layout.scan_first_beam = 1;
    layout.scan_beam_count = 3U;
    layout.processing_beam_count = 3U;
    layout.prts_per_cycle = 6U;
    layout.cycle_bytes = prt_bytes * 6U;
    layout.expected_prt_mode = 5U;
    layout.counter_phase = 0U;
    layout.cycle_timeout_ms = 1000;
    layout.allow_partial_first_scan = true;
    EchoCycleAssembler assembler(layout);
    std::shared_ptr<Config> cfg(new Config());
    cfg->wavepos_st = 1;
    cfg->wavepos_ed = 3;
    cfg->wavepos_skip = 1;

    const std::vector<uint8_t> partial = makeCycle(2U, 4U, samples, channels);
    for (std::size_t i = 0; i < 4U; ++i) {
        const uint8_t* packet = partial.data() + i * prt_bytes;
        assembler.acceptPrt(packet, prt_bytes, static_cast<uint32_t>(2U + i),
                            static_cast<double>(2U + i), cfg);
    }
    EchoCycleAssembler::Lease lease;
    if (!assembler.waitReady(lease, 50) || !lease.valid() ||
        !lease.metadata.partial_scan || lease.metadata.first_scan_beam != 2 ||
        lease.metadata.last_scan_beam != 3 ||
        lease.metadata.scan_beam_count != 2U ||
        lease.metadata.processing_beam_count != 2U ||
        lease.metadata.missing_leading_prt_count != 2U ||
        lease.metadata.integrity_status !=
            "valid_partial_first_scan_missing_leading_beams" ||
        lease.view.size != partial.size() ||
        std::memcmp(lease.view.data, partial.data(), partial.size()) != 0) {
        std::cerr << "partial first scan was not compacted/audited correctly\n";
        if (lease.valid()) assembler.release(lease, 0.0);
        return false;
    }
    assembler.release(lease, 0.0);

    const std::vector<uint8_t> full = makeCycle(6U, 6U, samples, channels);
    for (std::size_t i = 0; i < 6U; ++i) {
        const uint8_t* packet = full.data() + i * prt_bytes;
        assembler.acceptPrt(packet, prt_bytes, static_cast<uint32_t>(6U + i),
                            static_cast<double>(6U + i), cfg);
    }
    EchoCycleAssembler::Lease full_lease;
    const bool full_ok = assembler.waitReady(full_lease, 50) && full_lease.valid() &&
        !full_lease.metadata.partial_scan && full_lease.view.size == full.size() &&
        std::memcmp(full_lease.view.data, full.data(), full.size()) == 0;
    if (full_lease.valid()) assembler.release(full_lease, 0.0);
    if (!full_ok) {
        std::cerr << "complete scan after partial startup was not preserved\n";
        return false;
    }

    layout.allow_partial_first_scan = false;
    EchoCycleAssembler legacy(layout);
    for (std::size_t i = 0; i < 4U; ++i) {
        legacy.acceptPrt(partial.data() + i * prt_bytes, prt_bytes,
                         static_cast<uint32_t>(2U + i), static_cast<double>(i), cfg);
    }
    EchoCycleAssembler::Lease none;
    if (legacy.waitReady(none, 5)) {
        std::cerr << "legacy compatibility mode unexpectedly accepted partial scan\n";
        if (none.valid()) legacy.release(none, 0.0);
        return false;
    }
    return true;
}

bool fixedHeaderValidationTest()
{
    const std::size_t prt_bytes = gmti::new_protocol::packetBytes(2U, 4U, "int16");
    const auto run_case = [prt_bytes](int mutation) {
        EchoCycleLayout layout;
        layout.prt_bytes = prt_bytes;
        layout.prts_per_beam = 3U;
        layout.scan_beam_count = 2U;
        layout.processing_beam_count = 2U;
        layout.prts_per_cycle = 6U;
        layout.cycle_bytes = prt_bytes * 6U;
        layout.expected_prt_mode = 5U;
        layout.counter_phase = 0U;
        layout.cycle_timeout_ms = 1000;
        EchoCycleAssembler assembler(layout);
        PrtStreamParser parser(prt_bytes, 83U, 5U);
        std::shared_ptr<Config> cfg(new Config());

        std::vector<uint8_t> damaged = makeCycle(0U, 6U, 2U, 4U);
        uint8_t* packet = damaged.data() + 2U * prt_bytes;
        if (mutation == 0) {
            packet[gmti::new_protocol::kOffMagicTail] ^= 0x01U;
        } else if (mutation == 1) {
            gmti::new_protocol::storeU32LE(
                packet + gmti::new_protocol::kOffPrtLen,
                static_cast<uint32_t>(prt_bytes + 4U));
        } else {
            packet[gmti::new_protocol::kOffVersion] = 4U;
        }
        const std::vector<uint8_t> clean = makeCycle(6U, 6U, 2U, 4U);
        damaged.insert(damaged.end(), clean.begin(), clean.end());
        for (std::size_t off = 0U; off < damaged.size();) {
            const std::size_t n = std::min<std::size_t>(67U, damaged.size() - off);
            parser.feed(damaged.data() + off, n,
                [&](const uint8_t* p, std::size_t len, uint32_t counter, double utc) {
                    assembler.acceptPrt(p, len, counter, utc, cfg);
                });
            off += n;
        }
        EchoCycleAssembler::Lease lease;
        const bool ok = assembler.waitReady(lease, 50) &&
            lease.metadata.first_prt_counter == 6U &&
            std::memcmp(lease.view.data, clean.data(), clean.size()) == 0 &&
            parser.invalidPrtCount() > 0U && parser.resyncCount() > 0U;
        if (lease.valid()) assembler.release(lease, 1.0);
        return ok;
    };

    if (!run_case(0) || !run_case(1) || !run_case(2)) {
        std::cerr << "PRT tail/length/mode validation did not reject and recover\n";
        return false;
    }
    return true;
}

bool productionIntegrityRejectsInvalidTimeAndNavigationTest()
{
    const std::size_t samples = 2U;
    const std::size_t channels = 4U;
    const std::size_t prt_bytes =
        gmti::new_protocol::packetBytes(samples, channels, "int16");
    EchoCycleLayout layout;
    layout.prt_bytes = prt_bytes;
    layout.prts_per_beam = 3U;
    layout.scan_beam_count = 2U;
    layout.processing_beam_count = 2U;
    layout.prts_per_cycle = 6U;
    layout.cycle_bytes = prt_bytes * 6U;
    layout.expected_prt_mode = 5U;
    layout.counter_phase = 0U;
    layout.cycle_timeout_ms = 1000;
    EchoCycleAssembler assembler(layout);
    std::shared_ptr<Config> cfg(new Config());

    std::vector<uint8_t> regressed = makeCycle(0U, 6U, samples, channels);
    gmti::new_protocol::storeF32LE(
        regressed.data() + prt_bytes + gmti::new_protocol::kOffUtc, -1.0f);
    for (std::size_t i = 0; i < 6U; ++i) {
        const uint8_t* packet = regressed.data() + i * prt_bytes;
        assembler.acceptPrt(packet, prt_bytes, static_cast<uint32_t>(i),
                            static_cast<double>(gmti::new_protocol::loadF32LE(
                                packet + gmti::new_protocol::kOffUtc)), cfg);
    }

    std::vector<uint8_t> nonfinite = makeCycle(6U, 6U, samples, channels);
    gmti::new_protocol::storeF64LE(
        nonfinite.data() + gmti::new_protocol::kOffLatDeg,
        std::numeric_limits<double>::quiet_NaN());
    for (std::size_t i = 0; i < 6U; ++i) {
        const uint8_t* packet = nonfinite.data() + i * prt_bytes;
        assembler.acceptPrt(packet, prt_bytes, static_cast<uint32_t>(6U + i),
                            static_cast<double>(6U + i), cfg);
    }

    const std::vector<uint8_t> clean = makeCycle(12U, 6U, samples, channels);
    for (std::size_t i = 0; i < 6U; ++i) {
        const uint8_t* packet = clean.data() + i * prt_bytes;
        assembler.acceptPrt(packet, prt_bytes, static_cast<uint32_t>(12U + i),
                            static_cast<double>(12U + i), cfg);
    }
    EchoCycleAssembler::Lease lease;
    const ShmEchoMetricsSnapshot metrics = assembler.metrics();
    const bool ok = assembler.waitReady(lease, 50) && lease.valid() &&
        lease.metadata.first_prt_counter == 12U &&
        std::memcmp(lease.view.data, clean.data(), clean.size()) == 0 &&
        metrics.invalid_prt_count >= 2U &&
        metrics.dropped_incomplete_cycle_count >= 1U;
    if (lease.valid()) assembler.release(lease, 0.0);
    if (!ok) {
        std::cerr << "production assembler accepted invalid UTC/navigation\n";
    }
    return ok;
}

bool layoutReconfigureTest()
{
    const std::size_t old_prt_bytes = gmti::new_protocol::packetBytes(1U, 4U, "int16");
    EchoCycleLayout old_layout;
    old_layout.prt_bytes = old_prt_bytes;
    old_layout.prts_per_beam = 2U;
    old_layout.scan_beam_count = 1U;
    old_layout.processing_beam_count = 1U;
    old_layout.prts_per_cycle = 2U;
    old_layout.cycle_bytes = old_prt_bytes * 2U;
    old_layout.expected_prt_mode = 5U;
    old_layout.counter_phase = 0U;
    old_layout.cycle_timeout_ms = 1000;
    EchoCycleAssembler assembler(old_layout);

    std::shared_ptr<Config> old_cfg(new Config());
    old_cfg->config_generation = 1U;
    const std::vector<uint8_t> partial = makeCycle(0U, 1U, 1U, 4U);
    assembler.acceptPrt(partial.data(), old_prt_bytes, 0U, 0.0, old_cfg);

    const std::size_t new_prt_bytes = gmti::new_protocol::packetBytes(3U, 4U, "int16");
    EchoCycleLayout new_layout;
    new_layout.prt_bytes = new_prt_bytes;
    new_layout.prts_per_beam = 3U;
    new_layout.scan_beam_count = 1U;
    new_layout.processing_beam_count = 1U;
    new_layout.prts_per_cycle = 3U;
    new_layout.cycle_bytes = new_prt_bytes * 3U;
    new_layout.expected_prt_mode = 5U;
    new_layout.counter_phase = 0U;
    new_layout.cycle_timeout_ms = 1000;
    std::string error;
    if (!assembler.reconfigure(new_layout, error)) {
        std::cerr << "layout reconfigure failed: " << error << '\n';
        return false;
    }

    std::shared_ptr<Config> new_cfg(new Config());
    new_cfg->config_generation = 2U;
    const std::vector<uint8_t> clean = makeCycle(3U, 3U, 3U, 4U);
    for (std::size_t i = 0U; i < 3U; ++i) {
        const uint8_t* p = clean.data() + i * new_prt_bytes;
        assembler.acceptPrt(p, new_prt_bytes, 3U + static_cast<uint32_t>(i),
                            0.0, new_cfg);
    }
    EchoCycleAssembler::Lease lease;
    if (!assembler.waitReady(lease, 50) ||
        lease.metadata.config_generation != 2U ||
        lease.view.prt_bytes != new_prt_bytes ||
        lease.view.size != clean.size() ||
        std::memcmp(lease.view.data, clean.data(), clean.size()) != 0 ||
        assembler.metrics().dropped_incomplete_cycle_count == 0U) {
        std::cerr << "layout reconfigure mixed generations or preserved a partial cycle\n";
        return false;
    }
    assembler.release(lease, 1.0);
    return true;
}

bool doubleBufferBackpressureTest()
{
    const std::size_t prt_bytes = gmti::new_protocol::packetBytes(1U, 4U, "int16");
    EchoCycleLayout layout;
    layout.prt_bytes = prt_bytes;
    layout.prts_per_beam = 2U;
    layout.scan_beam_count = 1U;
    layout.processing_beam_count = 1U;
    layout.prts_per_cycle = 2U;
    layout.cycle_bytes = prt_bytes * 2U;
    layout.expected_prt_mode = 5U;
    layout.counter_phase = 0U;
    layout.cycle_timeout_ms = 1000;
    EchoCycleAssembler assembler(layout);
    std::shared_ptr<Config> cfg(new Config());
    const std::vector<uint8_t> c0 = makeCycle(0U, 2U, 1U, 4U);
    const std::vector<uint8_t> c1 = makeCycle(2U, 2U, 1U, 4U);
    const std::vector<uint8_t> c2 = makeCycle(4U, 2U, 1U, 4U);
    const auto feed = [&](const std::vector<uint8_t>& c) {
        for (std::size_t i = 0; i < 2U; ++i) {
            const uint8_t* p = c.data() + i * prt_bytes;
            assembler.acceptPrt(p, prt_bytes,
                gmti::new_protocol::loadU32LE(p + gmti::new_protocol::kOffPrtCounter),
                0.0, cfg);
        }
    };
    feed(c0);
    EchoCycleAssembler::Lease processing;
    if (!assembler.waitReady(processing, 50)) return false;
    const std::vector<uint8_t> held(processing.view.data,
                                    processing.view.data + processing.view.size);
    feed(c1);
    std::atomic<bool> third_started(false);
    std::atomic<bool> third_complete(false);
    std::thread third([&]() {
        third_started.store(true);
        feed(c2);
        third_complete.store(true);
    });
    while (!third_started.load()) std::this_thread::yield();
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    if (std::memcmp(processing.view.data, held.data(), held.size()) != 0 ||
        third_complete.load() ||
        assembler.metrics().dropped_backpressure_cycle_count != 0U) {
        std::cerr << "PROCESSING buffer was overwritten or the third cycle was not backpressured\n";
        assembler.shutdown();
        third.join();
        return false;
    }
    assembler.release(processing, 5.0);
    third.join();
    if (!third_complete.load()) {
        std::cerr << "third cycle did not resume after a buffer was released\n";
        return false;
    }
    EchoCycleAssembler::Lease ready;
    if (!assembler.waitReady(ready, 50) ||
        std::memcmp(ready.view.data, c1.data(), c1.size()) != 0) {
        std::cerr << "READY buffer was not preserved in acquisition order\n";
        return false;
    }
    assembler.release(ready, 1.0);
    EchoCycleAssembler::Lease third_ready;
    if (!assembler.waitReady(third_ready, 50) ||
        std::memcmp(third_ready.view.data, c2.data(), c2.size()) != 0) {
        std::cerr << "backpressured third cycle was not preserved\n";
        return false;
    }
    assembler.release(third_ready, 1.0);
    return true;
}

bool opaqueTestModeByteAccountingTest()
{
    // test=1 must be meaningful even if every payload byte is invalid as a
    // PRT.  This fixture therefore exercises only byte counts and never
    // constructs or feeds PrtStreamParser.
    TestCycleByteAccumulator accumulator(100U, 2U);
    accumulator.account(30U, 1U, 7U);
    TestCycleTrigger trigger;
    if (accumulator.waitReady(trigger, 1)) {
        std::cerr << "partial opaque test block incorrectly triggered a cycle\n";
        return false;
    }

    // One read crosses two cycle boundaries.  The exact 80-byte remainder
    // must remain available for the next read rather than being discarded.
    accumulator.account(250U, 2U, 7U);
    const TestCycleMetricsSnapshot before = accumulator.metrics();
    if (before.shm_blocks_received != 2U || before.shm_bytes_received != 280U ||
        before.completed_cycle_count != 2U || before.accumulated_bytes != 80U ||
        before.cycle_bytes != 100U || before.dropped_slot_count != 0U ||
        before.overwrite_count != 0U) {
        std::cerr << "opaque test byte accounting totals are incorrect\n";
        return false;
    }

    TestCycleTrigger first;
    TestCycleTrigger second;
    if (!accumulator.waitReady(first, 10) || !accumulator.waitReady(second, 10) ||
        first.completed_cycle_id != 1U || second.completed_cycle_id != 2U ||
        first.source_block_index != 2U || second.source_block_index != 2U ||
        first.source_valid_bytes != 250U || second.source_valid_bytes != 250U ||
        first.remaining_bytes != 180U || second.remaining_bytes != 80U ||
        first.config_generation != 7U || second.config_generation != 7U) {
        std::cerr << "opaque test trigger sequence/remainder is incorrect\n";
        return false;
    }
    accumulator.release(first, 0.0);
    accumulator.release(second, 0.0);
    return true;
}

bool auditedTestModeByteAccountingTest()
{
    // test=2 audits the same arbitrary byte stream that test=1 accounts for.
    // Bad framing and a PRT counter gap must be observable, but neither may
    // prevent the independent byte accumulator from emitting its trigger.
    const std::size_t samples = 2U;
    const std::size_t channels = 4U;
    const std::size_t prt_bytes =
        gmti::new_protocol::packetBytes(samples, channels, "int16");
    EchoCycleLayout layout;
    layout.prt_bytes = prt_bytes;
    layout.prts_per_beam = 3U;
    layout.scan_beam_count = 1U;
    layout.processing_beam_count = 1U;
    layout.prts_per_cycle = 3U;
    layout.cycle_bytes = prt_bytes * 3U + 31U;
    layout.expected_prt_mode = 5U;

    std::vector<uint8_t> stream = makePrt(10U, samples, channels);
    stream.insert(stream.end(), 31U, 0x11U); // deliberately not a PRT head
    const std::vector<uint8_t> prt12 = makePrt(12U, samples, channels);
    stream.insert(stream.end(), prt12.begin(), prt12.end()); // missing PRT 11
    std::vector<uint8_t> prt13 = makePrt(13U, samples, channels);
    gmti::new_protocol::storeF64LE(
        prt13.data() + gmti::new_protocol::kOffLatDeg, 95.0);
    stream.insert(stream.end(), prt13.begin(), prt13.end());

    ShmProtocolAudit audit(layout, 37U);
    TestCycleByteAccumulator accumulator(static_cast<uint64_t>(stream.size()), 2U);
    const std::size_t chunks[] = {1U, 17U, 8U, 53U, 3U, 71U};
    std::size_t offset = 0U;
    std::size_t chunk_index = 0U;
    while (offset < stream.size()) {
        const std::size_t len = std::min(
            chunks[chunk_index++ % (sizeof(chunks) / sizeof(chunks[0]))],
            stream.size() - offset);
        audit.inspect(stream.data() + offset, len);
        // This is deliberately unconditional after audit.inspect(), matching
        // test=2's live transport path.
        accumulator.account(len, chunk_index, 23U);
        offset += len;
    }

    TestCycleTrigger trigger;
    if (!accumulator.waitReady(trigger, 10) ||
        trigger.completed_cycle_id != 1U || trigger.remaining_bytes != 0U) {
        std::cerr << "test=2 malformed stream blocked byte trigger\n";
        return false;
    }
    accumulator.release(trigger, 0.0);

    const ShmProtocolAuditSnapshot metrics = audit.snapshot();
    if (metrics.shm_bytes_inspected != stream.size() ||
        metrics.valid_prt_count != 3U ||
        metrics.framing_or_header_invalid_count == 0U ||
        metrics.resync_count == 0U ||
        metrics.counter_discontinuity_count != 1U ||
        metrics.missing_prt_count != 1U ||
        metrics.geodetic_range_error_count != 1U ||
        metrics.prt_low_byte_mismatch_count != 0U) {
        std::cerr << "test=2 protocol audit metrics mismatch\n";
        return false;
    }
    return true;
}

bool preparedTestCycleFileSelectionTest()
{
    Config cfg;
    cfg.test_cycle_data_paths = {"period_0000.bin", "period_0001.bin", "period_0002.bin"};
    cfg.test_cycle_data_playback = "once";
    std::size_t index = 0U;
    std::string path;
    std::string error;
    if (!selectTestCycleDataFile(cfg, 1U, index, path, error) ||
        index != 0U || path != "period_0000.bin" ||
        !selectTestCycleDataFile(cfg, 3U, index, path, error) ||
        index != 2U || path != "period_0002.bin" ||
        selectTestCycleDataFile(cfg, 4U, index, path, error) ||
        error.find("exhausted") == std::string::npos) {
        std::cerr << "test=1 once prepared-file selection is incorrect\n";
        return false;
    }
    cfg.test_cycle_data_playback = "loop";
    if (!selectTestCycleDataFile(cfg, 4U, index, path, error) ||
        index != 0U || path != "period_0000.bin" ||
        !selectTestCycleDataFile(cfg, 5U, index, path, error) ||
        index != 1U || path != "period_0001.bin") {
        std::cerr << "test=1 loop prepared-file selection is incorrect\n";
        return false;
    }
    return true;
}

bool opaqueTestModeRingTransportTest()
{
    // This uses the exact shmdemo POSIX byte ring plus ShmEchoReceiver, but
    // intentionally feeds non-PRT bytes only to TestCycleByteAccumulator.
    // It proves that laboratory mode consumes/relinquishes ring reads without
    // constructing a parser or inspecting the payload contents.
    const std::string name = "/gmti_opaque_ring_selftest_" +
        std::to_string(static_cast<long long>(getpid()));
    RingBufferManager sender(name, RingBufferManager::SENDER, 1024U);
    ShmEchoReceiver receiver(name, 64U);
    TestCycleByteAccumulator accumulator(100U, 2U);
    RingBuffer& tx = sender.get_ring();

    const std::size_t sizes[] = {37U, 63U, 64U};
    for (std::size_t i = 0; i < sizeof(sizes) / sizeof(sizes[0]); ++i) {
        std::vector<uint8_t> invalid_payload(sizes[i],
            static_cast<uint8_t>(0x11U + i));
        // An invalid PRT head is deliberate: test=1 must neither recognize
        // nor reject it, merely consume the valid transport length.
        invalid_payload[0] = 0x00U;
        if (!tx.try_write(invalid_payload.data(), invalid_payload.size())) {
            std::cerr << "opaque ring fixture write failed\n";
            return false;
        }
        const uint8_t* data = nullptr;
        std::size_t len = 0U;
        std::string error;
        if (!receiver.poll(data, len, error) || !error.empty() ||
            data == nullptr || len != invalid_payload.size() ||
            std::memcmp(data, invalid_payload.data(), len) != 0) {
            std::cerr << "opaque ring fixture receive failed: " << error << '\n';
            return false;
        }
        accumulator.account(len, receiver.blockCount(), 11U);
    }

    TestCycleTrigger trigger;
    if (!accumulator.waitReady(trigger, 10) ||
        trigger.completed_cycle_id != 1U || trigger.remaining_bytes != 0U ||
        trigger.source_block_index != 2U || trigger.source_valid_bytes != 63U) {
        std::cerr << "opaque ring transport did not preserve the byte boundary\n";
        return false;
    }
    accumulator.release(trigger, 0.0);
    const TestCycleMetricsSnapshot metrics = accumulator.metrics();
    if (metrics.shm_blocks_received != 3U || metrics.shm_bytes_received != 164U ||
        metrics.accumulated_bytes != 64U || metrics.completed_cycle_count != 1U ||
        metrics.dropped_slot_count != 0U || metrics.overwrite_count != 0U) {
        std::cerr << "opaque ring transport accounting metrics mismatch\n";
        return false;
    }
    return true;
}

bool auditedTestModeRingTransportTest()
{
    // This is the test=2 transport order used by MainCtrl:
    // shmdemo read -> audit-only parser -> unconditional byte accounting.
    // The deliberately corrupt middle PRT must be reported without preventing
    // the third PRT's bytes from completing the trigger cycle.
    const std::size_t samples = 2U;
    const std::size_t channels = 4U;
    const std::size_t prt_bytes =
        gmti::new_protocol::packetBytes(samples, channels, "int16");
    const std::string name = "/gmti_audited_ring_selftest_" +
        std::to_string(static_cast<long long>(getpid()));
    RingBufferManager sender(name, RingBufferManager::SENDER, 4096U);
    ShmEchoReceiver receiver(name, 37U);
    RingBuffer& tx = sender.get_ring();

    EchoCycleLayout layout;
    layout.prt_bytes = prt_bytes;
    layout.prts_per_beam = 3U;
    layout.scan_beam_count = 1U;
    layout.processing_beam_count = 1U;
    layout.prts_per_cycle = 3U;
    layout.cycle_bytes = prt_bytes * 3U;
    layout.expected_prt_mode = 5U;

    std::vector<uint8_t> stream = makePrt(20U, samples, channels);
    std::vector<uint8_t> corrupt = makePrt(21U, samples, channels);
    corrupt[0] ^= 0xffU;
    stream.insert(stream.end(), corrupt.begin(), corrupt.end());
    const std::vector<uint8_t> prt22 = makePrt(22U, samples, channels);
    stream.insert(stream.end(), prt22.begin(), prt22.end());
    ShmProtocolAudit audit(layout, 37U);
    TestCycleByteAccumulator accumulator(layout.cycle_bytes, 2U);
    const std::size_t chunks[] = {1U, 37U, 5U, 19U, 36U, 7U, 31U};
    for (std::size_t consumed = 0U, chunk_index = 0U;
         consumed < stream.size(); ++chunk_index) {
        const std::size_t write_len = std::min(
            chunks[chunk_index % (sizeof(chunks) / sizeof(chunks[0]))],
            stream.size() - consumed);
        if (!tx.try_write(stream.data() + consumed, write_len)) {
            std::cerr << "cannot write audited ring test stream\n";
            return false;
        }
        const uint8_t* data = nullptr;
        std::size_t len = 0U;
        std::string error;
        if (!receiver.poll(data, len, error) || !error.empty() || len != write_len) {
            std::cerr << "cannot read audited ring test stream: " << error << '\n';
            return false;
        }
        audit.inspect(data, len);
        accumulator.account(len, receiver.blockCount(), 31U);
        consumed += len;
    }

    TestCycleTrigger trigger;
    const ShmProtocolAuditSnapshot metrics = audit.snapshot();
    if (!accumulator.waitReady(trigger, 10) ||
        trigger.completed_cycle_id != 1U ||
        metrics.shm_bytes_inspected != stream.size() ||
        metrics.valid_prt_count != 2U ||
        metrics.framing_or_header_invalid_count == 0U ||
        metrics.counter_discontinuity_count != 1U ||
        metrics.missing_prt_count != 1U) {
        std::cerr << "test=2 shmdemo transport audit/trigger mismatch\n";
        return false;
    }
    accumulator.release(trigger, 0.0);
    return true;
}

bool realProtocolRingTransportTest()
{
    // test=0 integration: the same shmdemo byte ring is read in chunks that
    // are unrelated to PRT boundaries.  Only after transport consumption does
    // the real parser/assembler receive the bytes.
    const std::size_t samples = 2U;
    const std::size_t channels = 4U;
    const std::size_t prt_bytes =
        gmti::new_protocol::packetBytes(samples, channels, "int16");
    const std::vector<uint8_t> cycle = makeCycle(0U, 6U, samples, channels);
    const std::string name = "/gmti_real_ring_selftest_" +
        std::to_string(static_cast<long long>(getpid()));
    RingBufferManager sender(name, RingBufferManager::SENDER, 4096U);
    ShmEchoReceiver receiver(name, 37U);
    RingBuffer& tx = sender.get_ring();

    EchoCycleLayout layout;
    layout.prt_bytes = prt_bytes;
    layout.prts_per_beam = 3U;
    layout.scan_beam_count = 2U;
    layout.processing_beam_count = 2U;
    layout.prts_per_cycle = 6U;
    layout.cycle_bytes = cycle.size();
    layout.expected_prt_mode = 5U;
    layout.counter_phase = 0U;
    layout.cycle_timeout_ms = 1000;
    EchoCycleAssembler assembler(layout);
    PrtStreamParser parser(prt_bytes, 37U, 5U);
    std::shared_ptr<Config> cfg(new Config());
    cfg->config_generation = 19U;
    cfg->shm_expected_prt_mode = 5;

    const std::size_t chunks[] = {1U, 37U, 5U, 19U, 36U, 7U, 31U};
    for (std::size_t off = 0U, ci = 0U; off < cycle.size(); ++ci) {
        const std::size_t n = std::min(chunks[ci % 7U], cycle.size() - off);
        if (!tx.try_write(cycle.data() + off, n)) {
            std::cerr << "real ring fixture write failed\n";
            return false;
        }
        const uint8_t* data = nullptr;
        std::size_t len = 0U;
        std::string error;
        if (!receiver.poll(data, len, error) || !error.empty() || len != n) {
            std::cerr << "real ring fixture receive failed: " << error << '\n';
            return false;
        }
        parser.feed(data, len,
            [&](const uint8_t* p, std::size_t plen, uint32_t counter, double utc) {
                assembler.acceptPrt(p, plen, counter, utc, cfg);
            });
        off += n;
    }

    EchoCycleAssembler::Lease lease;
    const bool ok = assembler.waitReady(lease, 50) && lease.valid() &&
        lease.view.size == cycle.size() &&
        std::memcmp(lease.view.data, cycle.data(), cycle.size()) == 0 &&
        lease.metadata.first_prt_counter == 0U &&
        lease.metadata.last_prt_counter == 5U &&
        parser.validPrtCount() == 6U && parser.invalidPrtCount() == 0U &&
        receiver.byteCount() == cycle.size();
    if (!ok) {
        std::cerr << "real protocol ring transport did not assemble split PRTs\n";
        if (lease.valid()) assembler.release(lease, 0.0);
        return false;
    }
    assembler.release(lease, 0.0);
    return true;
}

bool receiverReconnectAfterSenderRecreateTest()
{
    // shmdemo sender cleanup unlinks the POSIX name. A later sender creates a
    // new object with the same name, but an old mmap remains valid and must
    // not be mistaken for the new ring. This validates the receiver-side
    // identity probe/reconnect without modifying the shmdemo ABI.
    const std::string name = "/gmti_ring_reconnect_selftest_" +
        std::to_string(static_cast<long long>(getpid()));
    ShmEchoReceiver receiver(name, 32U);
    {
        RingBufferManager first(name, RingBufferManager::SENDER, 1024U);
        const uint8_t old_payload[] = {1U, 2U, 3U};
        if (!first.get_ring().try_write(old_payload, sizeof(old_payload))) {
            std::cerr << "first reconnect fixture write failed\n";
            return false;
        }
        const uint8_t* data = nullptr;
        std::size_t len = 0U;
        std::string error;
        if (!receiver.poll(data, len, error) || len != sizeof(old_payload) ||
            std::memcmp(data, old_payload, len) != 0) {
            std::cerr << "first reconnect fixture receive failed: " << error << '\n';
            return false;
        }
    } // sender destructor unlinks the first object exactly as shmdemo does.

    RingBufferManager second(name, RingBufferManager::SENDER, 1024U);
    const uint8_t new_payload[] = {9U, 8U, 7U, 6U};
    if (!second.get_ring().try_write(new_payload, sizeof(new_payload))) {
        std::cerr << "second reconnect fixture write failed\n";
        return false;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1100));
    const uint8_t* data = nullptr;
    std::size_t len = 0U;
    std::string error;
    if (receiver.poll(data, len, error) || error.empty() || receiver.attached()) {
        std::cerr << "stale shared-memory mapping was not disconnected\n";
        return false;
    }
    error.clear();
    if (!receiver.poll(data, len, error) || !error.empty() ||
        len != sizeof(new_payload) || std::memcmp(data, new_payload, len) != 0) {
        std::cerr << "receiver did not attach recreated shared-memory object: "
                  << error << '\n';
        return false;
    }
    return true;
}

bool ringBufferDemoCompatibilityTest()
{
    const std::string name = "/gmti_ring_capacity_selftest_" +
                             std::to_string(static_cast<long long>(getpid()));
    // This mirrors the supplied master-control demo: one semaphore token per
    // write/read call, while the payload itself is a byte stream that can wrap.
    RingBufferManager sender(name, RingBufferManager::SENDER, 16U);
    RingBufferManager receiver(name, RingBufferManager::RECEIVER);
    RingBuffer& tx = sender.get_ring();
    RingBuffer& rx = receiver.get_ring();
    const uint8_t first[12] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11};
    const uint8_t second[8] = {12, 13, 14, 15, 16, 17, 18, 19};
    if (!tx.try_write(first, sizeof(first))) {
        std::cerr << "demo-compatible ring first write failed\n";
        return false;
    }
    uint8_t out[12] = {};
    std::size_t n = 8U;
    if (!rx.try_read(out, n) || n != 8U ||
        std::memcmp(out, first, 8U) != 0) {
        std::cerr << "demo-compatible partial read failed\n";
        return false;
    }
    if (!tx.try_write(second, sizeof(second))) {
        std::cerr << "demo-compatible wrapped write failed\n";
        return false;
    }
    n = sizeof(out);
    const uint8_t expected[12] = {8, 9, 10, 11, 12, 13,
                                  14, 15, 16, 17, 18, 19};
    if (!rx.try_read(out, n) || n != sizeof(expected) ||
        std::memcmp(out, expected, sizeof(expected)) != 0) {
        std::cerr << "demo-compatible wrapped byte order mismatch\n";
        return false;
    }
    return true;
}

bool rejectNonRingSharedMemoryTest()
{
    const std::string name = "/gmti_non_ring_shm_selftest_" +
        std::to_string(static_cast<long long>(getpid()));
    const int fd = shm_open(name.c_str(), O_CREAT | O_EXCL | O_RDWR, 0600);
    if (fd < 0) {
        std::cerr << "cannot create malformed shared-memory fixture\n";
        return false;
    }
    const bool sized = ftruncate(fd, 16) == 0;
    close(fd);
    if (!sized) {
        shm_unlink(name.c_str());
        std::cerr << "cannot size malformed shared-memory fixture\n";
        return false;
    }

    ShmEchoReceiver receiver(name, 64U);
    const uint8_t* data = nullptr;
    std::size_t len = 0U;
    std::string error;
    const bool accepted = receiver.poll(data, len, error);
    shm_unlink(name.c_str());
    if (accepted || error.empty() || receiver.attached()) {
        std::cerr << "non-RingBuffer shared-memory object was accepted\n";
        return false;
    }
    return true;
}

} // namespace

int main()
{
    if (!fullScanLayoutSemanticsTest()) return 1;
    if (!mechanicalScanLayoutSemanticsTest()) return 1;
    if (!mechanicalScanIdFromPrtCounterTest()) return 1;
    if (!physicalBeamOffsetAcrossFullScanTest()) return 1;
    if (!parserAssemblerAndReaderTest()) return 1;
    if (!fourChannelFusionReaderTest()) return 1;
    if (!fourChannelPhysicalPhaseCompensationTest()) return 1;
    if (!multiPeriodFileOffsetTest()) return 1;
    if (!corruptionAndResyncTest()) return 1;
    if (!partialFirstScanAtBeamBoundaryTest()) return 1;
    if (!fixedHeaderValidationTest()) return 1;
    if (!productionIntegrityRejectsInvalidTimeAndNavigationTest()) return 1;
    if (!layoutReconfigureTest()) return 1;
    if (!doubleBufferBackpressureTest()) return 1;
    if (!opaqueTestModeByteAccountingTest()) return 1;
    if (!auditedTestModeByteAccountingTest()) return 1;
    if (!preparedTestCycleFileSelectionTest()) return 1;
    if (!opaqueTestModeRingTransportTest()) return 1;
    if (!auditedTestModeRingTransportTest()) return 1;
    if (!realProtocolRingTransportTest()) return 1;
    if (!receiverReconnectAfterSenderRecreateTest()) return 1;
    if (!ringBufferDemoCompatibilityTest()) return 1;
    if (!rejectNonRingSharedMemoryTest()) return 1;
    std::cout << "shm_echo_input_selftest: PASS\n";
    return 0;
}
