#include "dbs/NewProtocolReader.hpp"
#include "dbs/NewProtocolLayout.hpp"
#include "ctdr_phase_model.hpp"
#include "mechanical_scan.hpp"

#include <cstdint>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iostream>

namespace {

double wrapPhaseForTrig(double phase)
{
    if (!std::isfinite(phase)) return phase;
    phase = std::fmod(phase + M_PI, 2.0 * M_PI);
    if (phase < 0.0) phase += 2.0 * M_PI;
    return phase - M_PI;
}

// readXmlParam() stores frequencies in Hz.  A few host-side selftests and
// legacy direct Config callers still provide the historical GHz/MHz values;
// accept both at this boundary without multiplying an already-normalized
// runtime value a second time.
double carrierFrequencyHz(double value)
{
    return value > 1.0e6 ? value : value * 1.0e9;
}

double sampleFrequencyHz(double value)
{
    return value > 1.0e5 ? value : value * 1.0e6;
}

bool validPrtHeader(const uint8_t* packet,
                    std::size_t packet_bytes,
                    int expected_mode)
{
    if (!packet || packet_bytes < gmti::new_protocol::kHeaderBytes) {
        return false;
    }
    for (std::size_t i = 0; i < 8U; ++i) {
        if (packet[i] != 0x5AU) return false;
    }
    if (gmti::new_protocol::loadU32LE(
            packet + gmti::new_protocol::kOffPrtLen) != packet_bytes) {
        return false;
    }
    if (expected_mode >= 0 && expected_mode <= 255 &&
        packet[gmti::new_protocol::kOffVersion] !=
            static_cast<uint8_t>(expected_mode)) {
        return false;
    }
    for (std::size_t i = 0; i < 8U; ++i) {
        if (packet[gmti::new_protocol::kOffMagicTail + i] != 0x5BU) return false;
    }
    return true;
}

std::complex<float> buildFusionCompensationAtRange(
    const Config &cfg,
    const gmti::new_protocol::HeaderSample &header,
    int reference_channel_1based,
    int source_channel_1based,
    std::size_t range_index)
{
    const double fs_hz = sampleFrequencyHz(cfg.fs);
    const double fc_hz = carrierFrequencyHz(cfg.fc);
    if (!(fs_hz > 0.0) || !(fc_hz > 0.0)) {
        return {1.0f, 0.0f};
    }
    const auto &ref = cfg.four_channel_offsets_m[
        static_cast<std::size_t>(reference_channel_1based - 1)];
    const auto &src = cfg.four_channel_offsets_m[
        static_cast<std::size_t>(source_channel_1based - 1)];
    const double theta = header.theta_cmd_deg * M_PI / 180.0;
    const double side = cfg.four_channel_fusion_squint_side == 1 ? -1.0 : 1.0;
    const bool mechanical_pose = cfg.scan_mode == ScanMode::Mechanical;
    const double velocity_norm = std::hypot(header.ve_mps, header.vn_mps);
    const double along_e = velocity_norm > 1.0e-9
        ? header.ve_mps / velocity_norm : 0.0;
    const double along_n = velocity_norm > 1.0e-9
        ? header.vn_mps / velocity_norm : 1.0;
    // Local channel geometry is x=along-track, y=right/cross-track.  The
    // protocol compensation is evaluated in EN coordinates, so a mechanical
    // array must first rotate the local offsets by the actual servo pose and
    // then project them through the platform velocity frame.
    auto channelOffsetEn = [&](const std::array<double, 3> &offset) {
        if (!mechanical_pose) return offset;
        const std::array<double, 3> rotated =
            gmti::ctdr::rotateLocalOffsetAzimuth(
                offset, header.theta_cmd_deg,
                cfg.mechanical_scan.phase_center_mount_angle_deg,
                cfg.mechanical_scan.phase_center_rotation_sign,
                cfg.mechanical_scan.phase_center_rotation_enable);
        const double right_e = along_n;
        const double right_n = -along_e;
        return std::array<double, 3>{{
            along_e * rotated[0] + right_e * rotated[1],
            along_n * rotated[0] + right_n * rotated[1],
            rotated[2]
        }};
    };
    const std::array<double, 3> ref_offset =
        channelOffsetEn(std::array<double, 3>{{ref[0], ref[1], ref[2]}});
    const std::array<double, 3> src_offset =
        channelOffsetEn(std::array<double, 3>{{src[0], src[1], src[2]}});
    const double height = std::max(0.0, header.height_m);
    const double wavelength = C / fc_hz;
    // Common-transmit four-receive model: the TX path is shared by the two
    // channels being fused, so it cancels in their phase difference.  Only
    // the RX path difference remains and its carrier coefficient is
    // 2*pi/lambda.  The previous 4*pi/lambda coefficient belonged to the
    // equivalent-monostatic model and was inconsistent with CTDR.
    const double phase_scale =
        static_cast<double>(cfg.four_channel_carrier_phase_sign) *
        2.0 * M_PI / wavelength;
    {
        const double range = 0.5 * C *
            (cfg.sample_delay_us * 1.0e-6 + static_cast<double>(range_index) / fs_hz);
        const double horizontal = std::sqrt(std::max(0.0, range * range - height * height));
        double target[3] = {
            side * horizontal * std::sin(theta),
            side * horizontal * std::cos(theta),
            -height
        };
        if (mechanical_pose && velocity_norm > 1.0e-9) {
            const double cross = std::sqrt(std::max(0.0, 1.0 -
                                                     std::sin(theta) *
                                                     std::sin(theta)));
            const double left_e = -along_n;
            const double left_n = along_e;
            const double right_e = along_n;
            const double right_n = -along_e;
            const double side_e = cfg.four_channel_fusion_squint_side == 1
                ? left_e : right_e;
            const double side_n = cfg.four_channel_fusion_squint_side == 1
                ? left_n : right_n;
            target[0] = horizontal * (cross * side_e +
                                      std::sin(theta) * along_e);
            target[1] = horizontal * (cross * side_n +
                                      std::sin(theta) * along_n);
        }
        // target = range * u relative to the common TX phase centre.  Compute
        // each receiver path as a small offset from range, then subtract the
        // small offsets.  This avoids sqrt(~80 km)^2 - sqrt(~80 km)^2 and is
        // stable if this preprocessing is moved to a float device backend.
        const double inv_range = 1.0 / range;
        const double los_e = target[0] * inv_range;
        const double los_n = target[1] * inv_range;
        const double los_h = target[2] * inv_range;
        const double ref_delta = gmti::ctdr::stablePathOffsetFromCenter(
            range, los_e, los_n, los_h,
            ref_offset[0], ref_offset[1], ref_offset[2]);
        const double src_delta = gmti::ctdr::stablePathOffsetFromCenter(
            range, los_e, los_n, los_h,
            src_offset[0], src_offset[1], src_offset[2]);
        if (!std::isfinite(ref_delta) || !std::isfinite(src_delta)) {
            return {1.0f, 0.0f};
        }
        const double compensation_phase = wrapPhaseForTrig(
            phase_scale * (ref_delta - src_delta));
        return std::complex<float>(
            static_cast<float>(std::cos(compensation_phase)),
            static_cast<float>(std::sin(compensation_phase)));
    }
}

std::vector<std::complex<float>> buildFusionCompensation(
    const Config &cfg,
    const gmti::new_protocol::HeaderSample &header,
    int reference_channel_1based,
    int source_channel_1based,
    std::size_t sample_count)
{
    std::vector<std::complex<float>> result(sample_count, {1.0f, 0.0f});
    for (std::size_t n = 0; n < sample_count; ++n) {
        result[n] = buildFusionCompensationAtRange(
            cfg, header, reference_channel_1based, source_channel_1based, n);
    }
    return result;
}

bool readPulseBlockNewProtocolImpl(const Config &cfg,
                                   const EchoCycleView *cycle,
                                   int beamskip,
                                   std::vector<std::complex<float>> &data1,
                                   std::vector<std::complex<float>> &data2,
                                   std::vector<double> &utc,
                                   double &theta_sq,
                                   std::vector<std::vector<double>> &posRaw,
                                   NewProtocolGpuInput *gpu_input)
{
    if (cfg.pulse_len <= 0 || cfg.pulse_num <= 0 ||
        cfg.new_protocol_channel_count <= 0) {
        std::cerr << "[ERR] 新协议尺寸参数非法: pulse_len=" << cfg.pulse_len
                  << " pulse_num=" << cfg.pulse_num
                  << " channel_count=" << cfg.new_protocol_channel_count << std::endl;
        return false;
    }

    const size_t samples_per_prt = static_cast<size_t>(cfg.pulse_len);
    const size_t channel_count = static_cast<size_t>(cfg.new_protocol_channel_count);
    const std::string iq_type = cfg.iq_data_type.empty() ? "float32" : cfg.iq_data_type;
    const size_t prt_bytes = gmti::new_protocol::packetBytes(
        samples_per_prt, channel_count, iq_type);
    const int read_ch_1 = cfg.new_protocol_read_channel_1;
    const int read_ch_2 = cfg.new_protocol_read_channel_2;
    const int fusion_ch_3 = cfg.four_channel_fusion_channel_3;
    const int fusion_ch_4 = cfg.four_channel_fusion_channel_4;

    if (read_ch_1 < 1 || read_ch_2 < 1 ||
        read_ch_1 > cfg.new_protocol_channel_count ||
        read_ch_2 > cfg.new_protocol_channel_count) {
        std::cerr << "[ERR] 新协议读取通道超界: read_ch_1=" << read_ch_1
                  << " read_ch_2=" << read_ch_2
                  << " channel_count=" << cfg.new_protocol_channel_count << std::endl;
        return false;
    }
    if (cfg.enable_four_channel_fusion &&
        (channel_count < 4U || fusion_ch_3 < 1 || fusion_ch_4 < 1 ||
         fusion_ch_3 > cfg.new_protocol_channel_count ||
         fusion_ch_4 > cfg.new_protocol_channel_count)) {
        std::cerr << "[ERR] 四通道合成通道配置非法" << std::endl;
        return false;
    }

    const size_t period_pulses = static_cast<size_t>(cfg.pulse_num);
    const size_t read_pulses = cfg.read_pulse_num > 0
        ? static_cast<size_t>(cfg.read_pulse_num) : period_pulses;
    if (read_pulses == 0U || read_pulses > period_pulses) {
        std::cerr << "[ERR] 新协议 read_pulse_num 非法: read_pulse_num="
                  << cfg.read_pulse_num << " pulse_num=" << cfg.pulse_num << std::endl;
        return false;
    }

    const size_t pulse_offset = cfg.read_pulse_offset >= 0
        ? static_cast<size_t>(cfg.read_pulse_offset)
        : (period_pulses - read_pulses) / 2U;
    if (pulse_offset + read_pulses > period_pulses) {
        std::cerr << "[ERR] 新协议读取窗口越界: pulse_num=" << cfg.pulse_num
                  << " read_pulse_num=" << read_pulses
                  << " read_pulse_offset=" << pulse_offset << std::endl;
        return false;
    }

    std::ifstream fp;
    const uint8_t *memory_base = nullptr;
    uint64_t source_bytes = 0U;
    if (cycle) {
        if (!cycle->valid() || cycle->prt_bytes != prt_bytes) {
            std::cerr << "[ERR] 内存回波周期布局非法: view_size=" << cycle->size
                      << " view_prt_bytes=" << cycle->prt_bytes
                      << " expected_prt_bytes=" << prt_bytes << std::endl;
            return false;
        }
        memory_base = cycle->data;
        source_bytes = static_cast<uint64_t>(cycle->size);
    } else {
        const std::string &echo_path = cfg.GMTI_Data_new.empty()
            ? cfg.GMTI_Data_add : cfg.GMTI_Data_new;
        fp.open(echo_path.c_str(), std::ios::binary);
        if (!fp) {
            std::cerr << "[ERR] 无法打开新协议回波文件: " << echo_path << std::endl;
            return false;
        }
        fp.seekg(0, std::ios::end);
        const std::streamsize file_size = fp.tellg();
        if (file_size <= 0) {
            std::cerr << "[ERR] 新协议回波文件为空: " << echo_path << std::endl;
            return false;
        }
        source_bytes = static_cast<uint64_t>(file_size);
    }

    if ((source_bytes % prt_bytes) != 0U) {
        std::cerr << "[ERR] 新协议回波大小不是完整 PRT 整数倍: bytes="
                  << source_bytes << " prt_bytes=" << prt_bytes << std::endl;
        return false;
    }
    const size_t total_prt = static_cast<size_t>(source_bytes / prt_bytes);
    const MechanicalCpiWindow *mechanical_window = mechanicalWindow(cfg, beamskip);
    if (cfg.scan_mode == ScanMode::Mechanical && !mechanical_window) {
        std::cerr << "[ERR] 机械扫描窗口索引无效: runtime_window_index="
                  << beamskip << std::endl;
        return false;
    }
    if (cfg.scan_mode == ScanMode::Electronic &&
        beamskip < cfg.new_protocol_file_first_beam) {
        std::cerr << "[ERR] 请求波位早于周期首波位: beam=" << beamskip
                  << " first_beam=" << cfg.new_protocol_file_first_beam << std::endl;
        return false;
    }

    const size_t file_beam_index = cfg.scan_mode == ScanMode::Electronic
        ? static_cast<size_t>(beamskip - cfg.new_protocol_file_first_beam) : 0U;
    const size_t scan_beam_count = static_cast<size_t>(
        cfg.new_protocol_file_scan_beam_count > 0
            ? cfg.new_protocol_file_scan_beam_count
            : std::max(1, cfg.wavepos_ed - cfg.new_protocol_file_first_beam + 1));
    const size_t period_base_prt = cycle ? 0U :
        static_cast<size_t>(cfg.new_protocol_file_period_index) *
        scan_beam_count * period_pulses;
    const size_t start_with_skip = cfg.scan_mode == ScanMode::Mechanical
        ? mechanical_window->pulse_start
        : period_base_prt + file_beam_index * period_pulses +
              static_cast<size_t>(cfg.skip_az_num);
    const size_t start_without_skip = cfg.scan_mode == ScanMode::Mechanical
        ? mechanical_window->pulse_start
        : period_base_prt + file_beam_index * period_pulses;
    if (cfg.scan_mode == ScanMode::Mechanical &&
        (mechanical_window->pulse_count != period_pulses ||
         read_pulses > mechanical_window->pulse_count)) {
        std::cerr << "[ERR] 机械 CPI 与处理脉冲数不一致: window_count="
                  << mechanical_window->pulse_count
                  << " pulse_num=" << period_pulses
                  << " read_pulses=" << read_pulses << std::endl;
        return false;
    }
    size_t start_prt = start_with_skip + pulse_offset;
    if (start_prt + read_pulses > total_prt) {
        const size_t alternative = start_without_skip + pulse_offset;
        if (cfg.scan_mode == ScanMode::Electronic &&
            alternative + read_pulses <= total_prt) {
            std::cerr << "[WARN] 新协议带 skip_pulses 读取越界，按已裁剪周期读取: beam="
                      << beamskip << " skip_pulses=" << cfg.skip_az_num << std::endl;
            start_prt = alternative;
        } else {
            std::cerr << "[ERR] 新协议回波不足以读取 beam=" << beamskip
                      << " total_prt=" << total_prt
                      << " need_end_prt=" << (start_prt + read_pulses) << std::endl;
            return false;
        }
    }

    const size_t start_byte = start_prt * prt_bytes;
    if (source_bytes < static_cast<uint64_t>(start_byte + read_pulses * prt_bytes)) {
        std::cerr << "[ERR] 新协议回波字节数不足, beam=" << beamskip << std::endl;
        return false;
    }
    if (!cycle) {
        fp.seekg(static_cast<std::streamoff>(start_byte), std::ios::beg);
    }

    const bool packed_gpu_input = gpu_input != nullptr;
    if (packed_gpu_input) {
        data1.clear();
        data2.clear();
        *gpu_input = NewProtocolGpuInput{};
        gpu_input->pulse_count = read_pulses;
        gpu_input->samples_per_prt = samples_per_prt;
        gpu_input->channel_count = channel_count;
        gpu_input->bytes_per_iq = gmti::new_protocol::bytesPerIq(iq_type);
        gpu_input->header_bytes = gmti::new_protocol::kHeaderBytes;
        gpu_input->prt_bytes = prt_bytes;
        gpu_input->read_channel_1 = read_ch_1;
        gpu_input->read_channel_2 = read_ch_2;
        gpu_input->fusion_channel_3 = fusion_ch_3;
        gpu_input->fusion_channel_4 = fusion_ch_4;
        gpu_input->int16_iq = gmti::new_protocol::isInt16IqType(iq_type);
        gpu_input->four_channel_fusion = cfg.enable_four_channel_fusion;
        gpu_input->four_channel_phase_compensation_enable =
            cfg.four_channel_phase_compensation_enable;
        if (cycle) {
            gpu_input->external_prt_data = memory_base + start_byte;
            gpu_input->external_prt_bytes = read_pulses * prt_bytes;
        } else {
            gpu_input->payload.resize(read_pulses * prt_bytes);
            fp.read(reinterpret_cast<char *>(gpu_input->payload.data()),
                    static_cast<std::streamsize>(gpu_input->payload.size()));
            if (fp.gcount() != static_cast<std::streamsize>(gpu_input->payload.size())) {
                std::cerr << "[ERR] 一次读取新协议 PRT 块失败, beam="
                          << beamskip << " bytes=" << gpu_input->payload.size()
                          << std::endl;
                return false;
            }
        }
    } else {
        data1.resize(read_pulses * samples_per_prt);
        data2.resize(read_pulses * samples_per_prt);
    }
    utc.resize(read_pulses);
    posRaw.assign(read_pulses, std::vector<double>(7, 0.0));
    std::vector<double> fw_angle_deg(read_pulses, 0.0);
    // File mode needs one reusable packet. Memory mode references the cycle
    // buffer directly and performs no per-PRT allocation or copy.
    std::vector<uint8_t> file_packet;
    if (!cycle && !packed_gpu_input) {
        file_packet.resize(prt_bytes);
    }
    std::vector<std::complex<float>> compensation_13;
    std::vector<std::complex<float>> compensation_24;
    const bool pulsewise_mechanical_pose =
        cfg.scan_mode == ScanMode::Mechanical &&
        cfg.mechanical_scan.phase_center_rotation_enable;
    std::vector<std::complex<float>> pulse_compensation_13;
    std::vector<std::complex<float>> pulse_compensation_24;
    std::vector<std::complex<float>> fusion_data3;
    std::vector<std::complex<float>> fusion_data4;
    if (cfg.enable_four_channel_fusion && !packed_gpu_input) {
        fusion_data3.resize(read_pulses * samples_per_prt);
        fusion_data4.resize(read_pulses * samples_per_prt);
        compensation_13.assign(samples_per_prt, {1.0f, 0.0f});
        compensation_24.assign(samples_per_prt, {1.0f, 0.0f});
        if (pulsewise_mechanical_pose &&
            cfg.four_channel_phase_compensation_enable) {
            pulse_compensation_13.resize(samples_per_prt);
            pulse_compensation_24.resize(samples_per_prt);
        }
    }
    const size_t reference_pulse = cfg.scan_mode == ScanMode::Mechanical
        ? read_pulses / 2U : 0U;
    // Electronic and legacy paths use one fixed beam angle.  Mechanical CPU
    // fusion instead evaluates the pose for every physical PRT below; the
    // centre header remains the scalar reference for the later exact CTDR
    // inversion and for the packed GPU metadata.
    std::vector<uint8_t> reference_header;
    if (cfg.enable_four_channel_fusion &&
        cfg.four_channel_phase_compensation_enable && !packed_gpu_input &&
        !pulsewise_mechanical_pose) {
        const uint8_t *reference_packet = nullptr;
        if (cycle) {
            reference_packet = memory_base + start_byte + reference_pulse * prt_bytes;
        } else {
            reference_header.resize(gmti::new_protocol::kHeaderBytes);
            fp.seekg(static_cast<std::streamoff>(
                         start_byte + reference_pulse * prt_bytes),
                     std::ios::beg);
            fp.read(reinterpret_cast<char *>(reference_header.data()),
                    static_cast<std::streamsize>(reference_header.size()));
            if (fp.gcount() != static_cast<std::streamsize>(reference_header.size())) {
                std::cerr << "[ERR] 读取 CPI 中心 PRT 头失败" << std::endl;
                return false;
            }
            reference_packet = reference_header.data();
            fp.clear();
            fp.seekg(static_cast<std::streamoff>(start_byte), std::ios::beg);
        }
        if (!validPrtHeader(reference_packet, prt_bytes,
                            cfg.shm_expected_prt_mode)) {
            std::cerr << "[ERR] CPI 中心 PRT 头校验失败" << std::endl;
            return false;
        }
        const gmti::new_protocol::HeaderSample reference =
            gmti::new_protocol::readHeaderSample(reference_packet);
        compensation_13 = buildFusionCompensation(
            cfg, reference, read_ch_1, fusion_ch_3, samples_per_prt);
        compensation_24 = buildFusionCompensation(
            cfg, reference, read_ch_2, fusion_ch_4, samples_per_prt);
    }

    uint32_t previous_counter = 0U;
    double previous_utc = 0.0;
    bool have_previous_header = false;
    for (size_t k = 0; k < read_pulses; ++k) {
        const uint8_t *packet = nullptr;
        if (packed_gpu_input) {
            packet = gpu_input->data() + k * prt_bytes;
        } else if (cycle) {
            packet = memory_base + start_byte + k * prt_bytes;
        } else {
            fp.read(reinterpret_cast<char *>(file_packet.data()),
                    static_cast<std::streamsize>(prt_bytes));
            if (fp.gcount() != static_cast<std::streamsize>(prt_bytes)) {
                std::cerr << "[ERR] 读取新协议 PRT 失败, beam=" << beamskip
                          << " pulse=" << k << std::endl;
                return false;
            }
            packet = file_packet.data();
        }

        const gmti::new_protocol::HeaderSample hs =
            gmti::new_protocol::readHeaderSample(packet);
        if (!validPrtHeader(packet, prt_bytes, cfg.shm_expected_prt_mode)) {
            std::cerr << "[ERR] 新协议 PRT 头/长度/模式/尾标志校验失败, beam="
                      << beamskip << " pulse=" << k << std::endl;
            return false;
        }
        // vn/ve/vd are optional protocol fields.  Position-delta production
        // mode reconstructs velocity from the held geodetic samples, so an
        // absent/invalid optional velocity field must not reject the PRT.
        if (!std::isfinite(hs.utc) || !std::isfinite(hs.lat_deg) ||
            !std::isfinite(hs.lon_deg) || !std::isfinite(hs.height_m)) {
            std::cerr << "[ERR] 新协议 PRT 时间/位置字段非有限值, beam="
                      << beamskip << " pulse=" << k << std::endl;
            return false;
        }
        if (have_previous_header &&
            (hs.prt_counter != previous_counter + 1U || hs.utc < previous_utc)) {
            std::cerr << "[ERR] 新协议 PRT 完整性校验失败, beam="
                      << beamskip << " pulse=" << k
                      << " previous_counter=" << previous_counter
                      << " counter=" << hs.prt_counter
                      << " previous_utc=" << previous_utc
                      << " utc=" << hs.utc << std::endl;
            return false;
        }
        previous_counter = hs.prt_counter;
        previous_utc = hs.utc;
        have_previous_header = true;
        utc[k] = hs.utc;
        posRaw[k][0] = utc[k];
        posRaw[k][1] = hs.lat_deg * M_PI / 180.0;
        posRaw[k][2] = hs.lon_deg * M_PI / 180.0;
        posRaw[k][3] = hs.height_m;
        posRaw[k][4] = hs.vn_mps * cfg.new_protocol_velocity_scale;
        posRaw[k][5] = hs.ve_mps * cfg.new_protocol_velocity_scale;
        posRaw[k][6] = hs.vd_mps * cfg.new_protocol_velocity_scale;
        fw_angle_deg[k] = hs.theta_cmd_deg;
        if (packed_gpu_input && k == reference_pulse) {
            gpu_input->compensation_theta_deg = hs.theta_cmd_deg;
            gpu_input->compensation_height_m = hs.height_m;
        }

        if (packed_gpu_input) {
            continue;
        }
        const uint8_t *payload = packet + gmti::new_protocol::kHeaderBytes;
        const size_t sample_bytes = gmti::new_protocol::sampleBytes(channel_count, iq_type);
        const size_t iq_bytes = gmti::new_protocol::bytesPerIq(iq_type);
        const size_t ch1_base = gmti::new_protocol::channelOffset(
            static_cast<size_t>(read_ch_1), iq_type);
        const size_t ch2_base = gmti::new_protocol::channelOffset(
            static_cast<size_t>(read_ch_2), iq_type);
        const size_t ch3_base = cfg.enable_four_channel_fusion
            ? gmti::new_protocol::channelOffset(static_cast<size_t>(fusion_ch_3), iq_type)
            : 0U;
        const size_t ch4_base = cfg.enable_four_channel_fusion
            ? gmti::new_protocol::channelOffset(static_cast<size_t>(fusion_ch_4), iq_type)
            : 0U;
        if (cfg.enable_four_channel_fusion &&
            pulsewise_mechanical_pose &&
            cfg.four_channel_phase_compensation_enable) {
            for (size_t n = 0; n < samples_per_prt; ++n) {
                pulse_compensation_13[n] = buildFusionCompensationAtRange(
                    cfg, hs, read_ch_1, fusion_ch_3, n);
                pulse_compensation_24[n] = buildFusionCompensationAtRange(
                    cfg, hs, read_ch_2, fusion_ch_4, n);
            }
        }
        for (size_t n = 0; n < samples_per_prt; ++n) {
            const size_t off = n * sample_bytes;
            const float ch1_i = gmti::new_protocol::loadIqAsFloat(
                payload + off + ch1_base, iq_type);
            const float ch1_q = gmti::new_protocol::loadIqAsFloat(
                payload + off + ch1_base + iq_bytes, iq_type);
            const float ch2_i = gmti::new_protocol::loadIqAsFloat(
                payload + off + ch2_base, iq_type);
            const float ch2_q = gmti::new_protocol::loadIqAsFloat(
                payload + off + ch2_base + iq_bytes, iq_type);
            const std::complex<float> left(ch1_i, ch1_q);
            const std::complex<float> right(ch2_i, ch2_q);
            if (cfg.enable_four_channel_fusion) {
                const std::complex<float> ch3(
                    gmti::new_protocol::loadIqAsFloat(payload + off + ch3_base, iq_type),
                    gmti::new_protocol::loadIqAsFloat(payload + off + ch3_base + iq_bytes, iq_type));
                const std::complex<float> ch4(
                    gmti::new_protocol::loadIqAsFloat(payload + off + ch4_base, iq_type),
                    gmti::new_protocol::loadIqAsFloat(payload + off + ch4_base + iq_bytes, iq_type));
                const std::complex<float> &comp13 = pulsewise_mechanical_pose
                    ? pulse_compensation_13[n] : compensation_13[n];
                const std::complex<float> &comp24 = pulsewise_mechanical_pose
                    ? pulse_compensation_24[n] : compensation_24[n];
                fusion_data3[k * samples_per_prt + n] = ch3 * comp13;
                fusion_data4[k * samples_per_prt + n] = ch4 * comp24;
            }
            data1[k * samples_per_prt + n] = left;
            data2[k * samples_per_prt + n] = right;
        }
    }

    if (cfg.enable_four_channel_fusion && !packed_gpu_input &&
        cfg.four_channel_phase_compensation_enable) {
        // The geometric correction removes the deterministic vertical-array
        // phase.  A wide beam observing distributed terrain still leaves a
        // small range-dependent residual because one beam-centre look vector
        // cannot represent every scatterer.  Estimate that residual from the
        // complete coherent aperture before averaging the two vertical rows.
        // Static area clutter dominates this sum; independent receiver noise
        // and sparse moving targets do not form a coherent bias.
        std::vector<std::complex<float>> residual_13(samples_per_prt, {1.0f, 0.0f});
        std::vector<std::complex<float>> residual_24(samples_per_prt, {1.0f, 0.0f});
        for (size_t n = 0; n < samples_per_prt; ++n) {
            std::complex<double> cross13(0.0, 0.0);
            std::complex<double> cross24(0.0, 0.0);
            for (size_t k = 0; k < read_pulses; ++k) {
                const size_t index = k * samples_per_prt + n;
                cross13 += std::complex<double>(data1[index]) *
                           std::conj(std::complex<double>(fusion_data3[index]));
                cross24 += std::complex<double>(data2[index]) *
                           std::conj(std::complex<double>(fusion_data4[index]));
            }
            if (std::abs(cross13) > 0.0) {
                residual_13[n] = std::complex<float>(
                    static_cast<float>(cross13.real() / std::abs(cross13)),
                    static_cast<float>(cross13.imag() / std::abs(cross13)));
            }
            if (std::abs(cross24) > 0.0) {
                residual_24[n] = std::complex<float>(
                    static_cast<float>(cross24.real() / std::abs(cross24)),
                    static_cast<float>(cross24.imag() / std::abs(cross24)));
            }
        }
        for (size_t index = 0; index < data1.size(); ++index) {
            const size_t n = index % samples_per_prt;
            data1[index] = 0.5f *
                (data1[index] + fusion_data3[index] * residual_13[n]);
            data2[index] = 0.5f *
                (data2[index] + fusion_data4[index] * residual_24[n]);
        }
    } else if (cfg.enable_four_channel_fusion && !packed_gpu_input) {
        // Paired phase-centre tests intentionally bypass all four-channel
        // phase alignment while retaining independent receiver samples.
        for (size_t index = 0; index < data1.size(); ++index) {
            data1[index] = 0.5f * (data1[index] + fusion_data3[index]);
            data2[index] = 0.5f * (data2[index] + fusion_data4[index]);
        }
    }

    theta_sq = fw_angle_deg[fw_angle_deg.size() / 2U];
    return true;
}

} // namespace

bool readPulseBlockNewProtocol(const Config &cfg,
                               int beamskip,
                               std::vector<std::complex<float>> &data1,
                               std::vector<std::complex<float>> &data2,
                               std::vector<double> &utc,
                               double &theta_sq,
                               std::vector<std::vector<double>> &posRaw)
{
    const EchoCycleView *view = cfg.echo_cycle_view.valid()
        ? &cfg.echo_cycle_view : nullptr;
    return readPulseBlockNewProtocolImpl(
        cfg, view, beamskip, data1, data2, utc, theta_sq, posRaw, nullptr);
}

bool readPulseBlockNewProtocol(const Config &cfg,
                               const EchoCycleView &cycle,
                               int beamskip,
                               std::vector<std::complex<float>> &data1,
                               std::vector<std::complex<float>> &data2,
                               std::vector<double> &utc,
                               double &theta_sq,
                               std::vector<std::vector<double>> &posRaw)
{
    return readPulseBlockNewProtocolImpl(
        cfg, &cycle, beamskip, data1, data2, utc, theta_sq, posRaw, nullptr);
}

bool readPulseBlockNewProtocolGpuInput(const Config &cfg,
                                      int beamskip,
                                      NewProtocolGpuInput &gpu_input,
                                      std::vector<double> &utc,
                                      double &theta_sq,
                                      std::vector<std::vector<double>> &posRaw)
{
    std::vector<std::complex<float>> unused1;
    std::vector<std::complex<float>> unused2;
    const EchoCycleView *view = cfg.echo_cycle_view.valid()
        ? &cfg.echo_cycle_view : nullptr;
    return readPulseBlockNewProtocolImpl(
        cfg, view, beamskip, unused1, unused2, utc, theta_sq, posRaw, &gpu_input);
}

bool readPulseBlockNewProtocolGpuInput(const Config &cfg,
                                      const EchoCycleView &cycle,
                                      int beamskip,
                                      NewProtocolGpuInput &gpu_input,
                                      std::vector<double> &utc,
                                      double &theta_sq,
                                      std::vector<std::vector<double>> &posRaw)
{
    std::vector<std::complex<float>> unused1;
    std::vector<std::complex<float>> unused2;
    return readPulseBlockNewProtocolImpl(
        cfg, &cycle, beamskip, unused1, unused2, utc, theta_sq, posRaw, &gpu_input);
}

bool decodeNewProtocolGpuInputCpu(const Config &cfg,
                                  const NewProtocolGpuInput &gpu_input,
                                  std::vector<std::complex<float>> &data1,
                                  std::vector<std::complex<float>> &data2)
{
    if (!gpu_input.valid()) {
        std::cerr << "[ERR] GPU 打包新协议输入非法，无法执行 CPU 回退" << std::endl;
        return false;
    }
    const auto channel_valid = [&gpu_input](int channel) {
        return channel >= 1 && static_cast<std::size_t>(channel) <= gpu_input.channel_count;
    };
    if (!channel_valid(gpu_input.read_channel_1) ||
        !channel_valid(gpu_input.read_channel_2) ||
        (gpu_input.four_channel_fusion &&
         (!channel_valid(gpu_input.fusion_channel_3) ||
          !channel_valid(gpu_input.fusion_channel_4)))) {
        std::cerr << "[ERR] GPU 打包新协议输入的通道编号非法" << std::endl;
        return false;
    }

    const std::string iq_type = gpu_input.int16_iq ? "int16" : "float32";
    const std::size_t sample_bytes =
        gmti::new_protocol::sampleBytes(gpu_input.channel_count, iq_type);
    const std::size_t iq_bytes = gpu_input.bytes_per_iq;
    const std::size_t ch1_base = gmti::new_protocol::channelOffset(
        static_cast<std::size_t>(gpu_input.read_channel_1), iq_type);
    const std::size_t ch2_base = gmti::new_protocol::channelOffset(
        static_cast<std::size_t>(gpu_input.read_channel_2), iq_type);
    const std::size_t total = gpu_input.pulse_count * gpu_input.samples_per_prt;
    data1.resize(total);
    data2.resize(total);

    std::vector<std::complex<float>> fusion_data3;
    std::vector<std::complex<float>> fusion_data4;
    std::vector<std::complex<float>> compensation_13;
    std::vector<std::complex<float>> compensation_24;
    std::size_t ch3_base = 0U;
    std::size_t ch4_base = 0U;
    if (gpu_input.four_channel_fusion) {
        fusion_data3.resize(total);
        fusion_data4.resize(total);
        ch3_base = gmti::new_protocol::channelOffset(
            static_cast<std::size_t>(gpu_input.fusion_channel_3), iq_type);
        ch4_base = gmti::new_protocol::channelOffset(
            static_cast<std::size_t>(gpu_input.fusion_channel_4), iq_type);
        gmti::new_protocol::HeaderSample header;
        header.theta_cmd_deg = gpu_input.compensation_theta_deg;
        header.height_m = gpu_input.compensation_height_m;
        if (gpu_input.four_channel_phase_compensation_enable) {
            // Electronic input keeps one static factor.  Mechanical input
            // evaluates the actual header pose in the per-PRT loop below;
            // this centre factor is only a correctly sized fallback.
            compensation_13 = buildFusionCompensation(
                cfg, header, gpu_input.read_channel_1,
                gpu_input.fusion_channel_3, gpu_input.samples_per_prt);
            compensation_24 = buildFusionCompensation(
                cfg, header, gpu_input.read_channel_2,
                gpu_input.fusion_channel_4, gpu_input.samples_per_prt);
        } else {
            compensation_13.assign(gpu_input.samples_per_prt, {1.0f, 0.0f});
            compensation_24.assign(gpu_input.samples_per_prt, {1.0f, 0.0f});
        }
    }

    const std::uint8_t *prt_block = gpu_input.data();
    for (std::size_t index = 0; index < total; ++index) {
        const std::size_t pulse_index = index / gpu_input.samples_per_prt;
        const std::size_t range_index = index % gpu_input.samples_per_prt;
        const std::size_t off = range_index * sample_bytes;
        const std::uint8_t *sample = prt_block + pulse_index * gpu_input.prt_bytes +
                                     gpu_input.header_bytes + off;
        data1[index] = std::complex<float>(
            gmti::new_protocol::loadIqAsFloat(sample + ch1_base, iq_type),
            gmti::new_protocol::loadIqAsFloat(sample + ch1_base + iq_bytes, iq_type));
        data2[index] = std::complex<float>(
            gmti::new_protocol::loadIqAsFloat(sample + ch2_base, iq_type),
            gmti::new_protocol::loadIqAsFloat(sample + ch2_base + iq_bytes, iq_type));
        if (gpu_input.four_channel_fusion) {
            const std::complex<float> ch3(
                gmti::new_protocol::loadIqAsFloat(sample + ch3_base, iq_type),
                gmti::new_protocol::loadIqAsFloat(sample + ch3_base + iq_bytes, iq_type));
            const std::complex<float> ch4(
                gmti::new_protocol::loadIqAsFloat(sample + ch4_base, iq_type),
                gmti::new_protocol::loadIqAsFloat(sample + ch4_base + iq_bytes, iq_type));
            std::complex<float> comp13 = compensation_13[range_index];
            std::complex<float> comp24 = compensation_24[range_index];
            if (cfg.scan_mode == ScanMode::Mechanical &&
                cfg.mechanical_scan.phase_center_rotation_enable &&
                gpu_input.four_channel_phase_compensation_enable) {
                const gmti::new_protocol::HeaderSample pulse_header =
                    gmti::new_protocol::readHeaderSample(
                        prt_block + pulse_index * gpu_input.prt_bytes);
                comp13 = buildFusionCompensationAtRange(
                    cfg, pulse_header, gpu_input.read_channel_1,
                    gpu_input.fusion_channel_3, range_index);
                comp24 = buildFusionCompensationAtRange(
                    cfg, pulse_header, gpu_input.read_channel_2,
                    gpu_input.fusion_channel_4, range_index);
            }
            fusion_data3[index] = ch3 * comp13;
            fusion_data4[index] = ch4 * comp24;
        }
    }

    if (!gpu_input.four_channel_fusion) {
        return true;
    }

    std::vector<std::complex<float>> residual_13(
        gpu_input.samples_per_prt, {1.0f, 0.0f});
    std::vector<std::complex<float>> residual_24(
        gpu_input.samples_per_prt, {1.0f, 0.0f});
    if (gpu_input.four_channel_phase_compensation_enable) {
    for (std::size_t n = 0; n < gpu_input.samples_per_prt; ++n) {
        std::complex<double> cross13(0.0, 0.0);
        std::complex<double> cross24(0.0, 0.0);
        for (std::size_t k = 0; k < gpu_input.pulse_count; ++k) {
            const std::size_t index = k * gpu_input.samples_per_prt + n;
            cross13 += std::complex<double>(data1[index]) *
                       std::conj(std::complex<double>(fusion_data3[index]));
            cross24 += std::complex<double>(data2[index]) *
                       std::conj(std::complex<double>(fusion_data4[index]));
        }
        const double magnitude13 = std::abs(cross13);
        const double magnitude24 = std::abs(cross24);
        if (magnitude13 > 0.0) {
            residual_13[n] = std::complex<float>(
                static_cast<float>(cross13.real() / magnitude13),
                static_cast<float>(cross13.imag() / magnitude13));
        }
        if (magnitude24 > 0.0) {
            residual_24[n] = std::complex<float>(
                static_cast<float>(cross24.real() / magnitude24),
                static_cast<float>(cross24.imag() / magnitude24));
        }
    }
    }
    for (std::size_t index = 0; index < total; ++index) {
        const std::size_t n = index % gpu_input.samples_per_prt;
        data1[index] = 0.5f *
            (data1[index] + fusion_data3[index] * residual_13[n]);
        data2[index] = 0.5f *
            (data2[index] + fusion_data4[index] * residual_24[n]);
    }
    return true;
}
