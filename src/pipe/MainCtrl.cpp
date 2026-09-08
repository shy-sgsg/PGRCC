#include "pipe/MainCtrl.h"
#include "dbs/DbsFusion.hpp"
#include "trackModule.hpp"
#include "pipe/PipeStruDef.h"
#include "../auth/hardware_bind_flow/gate/security_gate.h"
#include "runtime_diagnostics.hpp"
#include "track_output_state_source.hpp"
#include "low_radial_velocity_filter.hpp"
#include "pipe/ShmEchoInput.h"
#include "dbs/NewProtocolLayout.hpp"
#include "mechanical_scan.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cerrno>
#include <cctype>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <mqueue.h>
#include <sstream>
#include <queue>
#include <regex>
#include <limits>
#include <memory>
#include <utility>
#include <sys/sysinfo.h>
#include <sys/stat.h>
#include <time.h>
#include "dbs/lodepng.h"

namespace {

static_assert(sizeof(ResultHeader) == 172U, "ResultHeader must match protocol V1.3 fixed fields");

// The result sender may be waiting for the controller FIFO.  Bound the
// snapshot queue so a disconnected controller cannot turn normal processing
// into unbounded heap growth.  The sender always owns the front snapshot; on
// saturation a newer result is explicitly dropped rather than racing that
// in-flight front item.
constexpr std::size_t kMaxPendingResultPackets = 2U;
constexpr std::size_t kMaxPendingEchoFiles = 2U;
// A cycle above this limit is a real-time violation. Keep detailed stage
// timings off the normal hot path, but make every violation self-diagnosing.
constexpr long long kSlowCycleDiagnosticThresholdMs = 4800LL;

volatile std::sig_atomic_t g_local_test_stop_requested = 0;

void handleLocalTestSignal(int)
{
    g_local_test_stop_requested = 1;
}

std::queue<std::string> g_gmtiFileQueue;
std::mutex g_gmtiFileQueueMutex;

uint64_t readMemAvailableBytes()
{
    std::ifstream in("/proc/meminfo");
    std::string key;
    uint64_t value_kib = 0U;
    std::string unit;
    while (in >> key >> value_kib >> unit) {
        if (key == "MemAvailable:") return value_kib * 1024U;
    }
    struct sysinfo info;
    if (::sysinfo(&info) == 0) {
        return static_cast<uint64_t>(info.freeram) * info.mem_unit;
    }
    return 0U;
}

std::string composePipePath(const std::string& root, const char* leaf)
{
    if (root.empty()) {
        return std::string(leaf);
    }
    if (root.back() == '/') {
        return root + leaf;
    }
    return root + "/" + leaf;
}

bool ensureResultDirectory(const std::string& dir, std::string& error)
{
    if (dir.empty()) {
        error = "result_add must be non-empty";
        return false;
    }
    std::string cur;
    size_t i = 0U;
    if (dir[0] == '/') {
        cur = "/";
        i = 1U;
    }
    for (; i < dir.size(); ++i) {
        cur.push_back(dir[i]);
        if (dir[i] != '/' && i + 1U != dir.size()) continue;
        if (cur == "/" || cur == "./" || cur == ".") continue;
        if (::mkdir(cur.c_str(), 0755) != 0 && errno != EEXIST) {
            error = "cannot create result_add directory: " + cur +
                " (" + std::strerror(errno) + ")";
            return false;
        }
    }
    return true;
}

int extractFileIdFromPath(const std::string& path)
{
    const size_t pos = path.find_last_of("/\\");
    const std::string filename = (pos == std::string::npos) ? path : path.substr(pos + 1);
    std::smatch match;
    const std::regex taggedPattern(
        R"((?:ID|data[_-]?)(\d+)(?:\.(bin|dat))?$)", std::regex::icase);
    if (std::regex_search(filename, match, taggedPattern) && match.size() >= 2) {
        return std::stoi(match[1].str());
    }
    const std::regex trailingPattern(
        R"((\d+)(?:\.(bin|dat))?$)", std::regex::icase);
    if (std::regex_search(filename, match, trailingPattern) && match.size() >= 2) {
        return std::stoi(match[1].str());
    }
    return -1;
}

bool configureLegacySeparatedFileInput(Config& cfg,
                                       const std::string& channel1_path,
                                       std::string& error)
{
    // A local file-cycle spec identifies channel 1.  The Mission068 legacy
    // recorder stores the matching channel beside it with the sole suffix
    // change `_GMTI0` -> `_GMTI1`; deriving it here keeps TrackManager alive
    // across a multi-cycle replay instead of launching one process per file.
    static const std::regex legacy_name(
        R"(^(.*)/RawData/ID([0-9]+)_8H[0-9]+M_GMTI0$)",
        std::regex::icase);
    std::smatch match;
    if (!std::regex_match(channel1_path, match, legacy_name) ||
        match.size() < 3U) {
        error = "legacy separated file input must match "
                ".../RawData/ID<n>_8H<n>M_GMTI0: " + channel1_path;
        return false;
    }

    const std::string channel2_path =
        channel1_path.substr(0, channel1_path.size() - 1U) + "1";
    struct stat st {};
    if (::stat(channel1_path.c_str(), &st) != 0 || !S_ISREG(st.st_mode)) {
        error = "legacy channel-1 file is not a regular file: " + channel1_path;
        return false;
    }
    if (::stat(channel2_path.c_str(), &st) != 0 || !S_ISREG(st.st_mode)) {
        error = "legacy channel-2 file is not a regular file: " + channel2_path;
        return false;
    }

    const std::string pos_path = match[1].str() + "/RTIPOS/RTIPOS_ID" +
                                 match[2].str() + ".out";
    if (::stat(pos_path.c_str(), &st) != 0 || !S_ISREG(st.st_mode)) {
        error = "legacy POS file is not a regular file: " + pos_path;
        return false;
    }

    cfg.GMTI_Data_add = channel1_path;
    cfg.GMTI_Data_add2 = channel2_path;
    cfg.Plane_POS_add = pos_path;
    if (cfg.legacy_replay_use_companion_parameters) {
        const std::string parameter_path = match[1].str() +
            "/DBS_parameter_ID" + match[2].str() + "_8H" +
            match[2].str() + "M.xml";
        std::ifstream parameter_file(parameter_path);
        if (!parameter_file) {
            error = "cannot open legacy companion parameter file: " + parameter_path;
            return false;
        }
        const std::string xml((std::istreambuf_iterator<char>(parameter_file)),
                              std::istreambuf_iterator<char>());
        const std::regex squint_pattern(
            R"(<squint_angle>\s*([-+0-9.eE]+)\s*</squint_angle>)",
            std::regex::icase);
        std::smatch squint_match;
        if (!std::regex_search(xml, squint_match, squint_pattern) ||
            squint_match.size() < 2U) {
            error = "legacy companion parameter file has no squint_angle: " +
                    parameter_path;
            return false;
        }
        try {
            const double squint_deg = std::stod(squint_match[1].str());
            if (!std::isfinite(squint_deg)) {
                error = "legacy companion squint_angle is non-finite: " + parameter_path;
                return false;
            }
            cfg.squint_angle = squint_deg;
        } catch (const std::exception&) {
            error = "invalid legacy companion squint_angle: " + parameter_path;
            return false;
        }
        std::cout << "[LOCAL-TEST][LEGACY] companion_parameters="
                  << parameter_path << " squint_angle_deg="
                  << cfg.squint_angle << std::endl;
    }
    std::cout << "[LOCAL-TEST][LEGACY] ch1=" << cfg.GMTI_Data_add
              << " ch2=" << cfg.GMTI_Data_add2
              << " pos=" << cfg.Plane_POS_add << std::endl;
    return true;
}

bool isWagmtiCommandMode(uint32_t mode)
{
    // Command workMode 7 is retained for deployed legacy clients; V1.5 uses
    // 9 for WAGMTI.  FPGA PRT mode 5 is SAR 3 m and is validated separately.
    return mode == static_cast<uint32_t>(Mode_GMTI) ||
           mode == static_cast<uint32_t>(Mode_WAGMTI);
}

bool isLaboratoryTestMode(int test_mode)
{
    return test_mode == 1 || test_mode == 2;
}

const char* laboratoryTestTag(int test_mode)
{
    return test_mode == 2 ? "TEST2" : "TEST1";
}

const char* testModeDescription(int test_mode)
{
    switch (test_mode) {
    case 0:
        return "shared-memory real echo";
    case 1:
        return "opaque byte accounting + prepared-file cycle";
    case 2:
        return "protocol audit + byte accounting + prepared-file cycle";
    default:
        return "invalid";
    }
}

uint32_t loadWireU32LE(const uint8_t* p)
{
    return static_cast<uint32_t>(p[0]) |
           (static_cast<uint32_t>(p[1]) << 8U) |
           (static_cast<uint32_t>(p[2]) << 16U) |
           (static_cast<uint32_t>(p[3]) << 24U);
}

bool isGmtiCommandAlgorithm(uint8_t algo_type)
{
    return algo_type == kProtocolV15AlgoGmti ||
           algo_type == kProtocolV15AlgoWagmti;
}

bool decodeV15ModeSwitchFrame(const uint8_t* frame,
                              std::size_t frame_bytes,
                              ModeSwitchCmd& cmd,
                              std::string& error)
{
    if (!frame || frame_bytes < sizeof(ModeSwitchCmd)) {
        error = "command frame is shorter than the V1.5 common layout";
        return false;
    }
    if (loadWireU32LE(frame) != kProtocolV15CommandHead) {
        error = "invalid command frame head";
        return false;
    }
    const uint32_t data_len = loadWireU32LE(frame + 5U);
    if (data_len != frame_bytes - kProtocolV15CommandOverhead) {
        error = "command dataLen does not equal frame_bytes-13";
        return false;
    }
    if (!isGmtiCommandAlgorithm(frame[4])) {
        error = "command algorithm type is not GMTI/WAGMTI";
        return false;
    }
    if (loadWireU32LE(frame + frame_bytes - 4U) != kProtocolV15CommandTail) {
        error = "invalid command frame tail";
        return false;
    }

    std::memset(&cmd, 0, sizeof(cmd));
    std::memcpy(&cmd, frame, std::min(frame_bytes, sizeof(cmd)));
    // For an extended frame, common fields remain at the same offsets while
    // the tail is at the actual end of the frame.
    cmd.dataLen = data_len;
    cmd.tail = kProtocolV15CommandTail;
    return true;
}

std::vector<int> buildTrackIdxRange(int id, int window)
{
    std::vector<int> values;
    if (id <= 0 || window <= 0) {
        return values;
    }
    const int start = id - window + 1;
    values.reserve(static_cast<size_t>(window));
    for (int idx = start; idx <= id; ++idx) {
        values.push_back(idx);
    }
    return values;
}

bool allocateNextResultId(GMTIProcessor& proc, const Config& cfg, int& id)
{
    std::string nextPath;
    id = -1;
    return proc.nextGMTIFileName(cfg.result_add, nextPath, id) && id > 0;
}

void clearPendingEchoQueue()
{
    std::queue<std::string> empty;
    std::lock_guard<std::mutex> lock(g_gmtiFileQueueMutex);
    g_gmtiFileQueue.swap(empty);
}

bool popPendingEchoFile(std::string& fileName)
{
    std::lock_guard<std::mutex> lock(g_gmtiFileQueueMutex);
    if (g_gmtiFileQueue.empty()) {
        return false;
    }
    fileName = std::move(g_gmtiFileQueue.front());
    g_gmtiFileQueue.pop();
    return true;
}

void pushPendingEchoFile(const std::string& fileName)
{
    std::lock_guard<std::mutex> lock(g_gmtiFileQueueMutex);
    if (g_gmtiFileQueue.size() >= kMaxPendingEchoFiles) {
        std::cerr << "[ECHO][DROP] file-input backlog is full (limit="
                  << kMaxPendingEchoFiles << "): " << fileName << std::endl;
        return;
    }
    g_gmtiFileQueue.push(fileName);
}

bool parseLocalEchoSpec(const std::string& spec,
                        int& forcedId,
                        int& forcedPeriodIndex,
                        std::string& echoFile)
{
    forcedId = -1;
    forcedPeriodIndex = -1;
    echoFile = spec;

    const size_t eq = spec.find('=');
    if (eq == std::string::npos || eq == 0 || eq + 1 >= spec.size()) {
        return true;
    }

    const std::string selector = spec.substr(0, eq);
    const size_t at = selector.find('@');
    const std::string idText = selector.substr(0, at);
    const std::string periodText = at == std::string::npos
        ? std::string() : selector.substr(at + 1);
    if (idText.empty() ||
        !std::all_of(idText.begin(), idText.end(), [](unsigned char ch) { return std::isdigit(ch); })) {
        return true;
    }
    if (at != std::string::npos &&
        (periodText.empty() ||
         !std::all_of(periodText.begin(), periodText.end(),
                      [](unsigned char ch) { return std::isdigit(ch); }))) {
        return false;
    }

    forcedId = std::stoi(idText);
    if (!periodText.empty()) forcedPeriodIndex = std::stoi(periodText);
    echoFile = spec.substr(eq + 1);
    return forcedId > 0 && !echoFile.empty();
}

static inline int32_t quant_deg_to_i32(double deg)
{
    const double q = deg / LSB;
    const double rounded = (q >= 0.0) ? std::floor(q + 0.5) : std::ceil(q - 0.5);
    if (rounded > 2147483647.0) {
        return 2147483647;
    }
    if (rounded < -2147483648.0) {
        return -2147483648;
    }
    return static_cast<int32_t>(rounded);
}

static inline void put_u16_le(std::vector<uint8_t>& buf, size_t off, uint16_t value)
{
    buf[off + 0] = static_cast<uint8_t>(value & 0xFFU);
    buf[off + 1] = static_cast<uint8_t>((value >> 8) & 0xFFU);
}

static inline void put_u32_le(std::vector<uint8_t>& buf, size_t off, uint32_t value)
{
    buf[off + 0] = static_cast<uint8_t>(value & 0xFFU);
    buf[off + 1] = static_cast<uint8_t>((value >> 8) & 0xFFU);
    buf[off + 2] = static_cast<uint8_t>((value >> 16) & 0xFFU);
    buf[off + 3] = static_cast<uint8_t>((value >> 24) & 0xFFU);
}

static inline void put_i32_le(std::vector<uint8_t>& buf, size_t off, int32_t value)
{
    put_u32_le(buf, off, static_cast<uint32_t>(value));
}

static inline void put_double_le(std::vector<uint8_t>& buf, size_t off, double value)
{
    uint64_t raw = 0;
    std::memcpy(&raw, &value, sizeof(raw));
    for (int i = 0; i < 8; ++i) {
        buf[off + static_cast<size_t>(i)] = static_cast<uint8_t>((raw >> (8 * i)) & 0xFFU);
    }
}

static inline uint8_t quant_pixel_pitch(double meters)
{
    const double q = std::round(std::max(0.0, meters) / 0.01);
    if (q > 255.0) {
        return 255;
    }
    return static_cast<uint8_t>(q);
}

static inline void write_target_packet(std::vector<uint8_t>& pkt, size_t off, const GMTIDetection& det)
{
    put_u16_le(pkt, off + 0, det.id);
    put_i32_le(pkt, off + 2, quant_deg_to_i32(det.lon));
    put_i32_le(pkt, off + 6, quant_deg_to_i32(det.lat));
    // V1.5 defines target distance as ground range in metres. GMTIDetection
    // stores the horizontal EN distance in metres already.
    put_double_le(pkt, off + 10, det.range);
    put_double_le(pkt, off + 18, det.speed);
    put_double_le(pkt, off + 26, det.direction);
    pkt[off + 34] = 0;
}

static std::string makeResultProductPath(const Config& cfg, const char* ext)
{
    if (cfg.result_file_id <= 0) {
        return std::string();
    }
    std::string dir = cfg.result_add;
    if (!dir.empty() && dir.back() != '/' && dir.back() != '\\') {
        dir.push_back('/');
    }
    char name[32];
    std::snprintf(name, sizeof(name), "GMTI%02d.%s", cfg.result_file_id, ext);
    return dir + name;
}

static bool loadDbsImageProduct(const Config& cfg, GMTIResultPacket& packet)
{
    const std::string pngPath = makeResultProductPath(cfg, "png");
    if (pngPath.empty()) {
        return false;
    }

    std::vector<unsigned char> image;
    unsigned width = 0;
    unsigned height = 0;
    const unsigned err = lodepng::decode(image, width, height, pngPath, LCT_GREY, 8);
    if (err != 0U) {
        std::cerr << "[GMTI][WARN] DBS image not available for protocol packet: "
                  << pngPath << " (" << lodepng_error_text(err) << ")" << std::endl;
        return false;
    }
    if (width > 65535U || height > 65535U) {
        std::cerr << "[GMTI][WARN] DBS image exceeds protocol dimensions: "
                  << height << "x" << width << std::endl;
        return false;
    }

    packet.image.assign(image.begin(), image.end());
    packet.image_rows = static_cast<uint16_t>(height);
    packet.image_cols = static_cast<uint16_t>(width);
    packet.image_available = !packet.image.empty();
    return packet.image_available;
}

static bool loadDbsCornerProduct(const Config& cfg, GMTIResultPacket& packet)
{
    const std::string txtPath = makeResultProductPath(cfg, "txt");
    if (txtPath.empty()) {
        return false;
    }

    std::ifstream in(txtPath.c_str());
    if (!in) {
        return false;
    }

    double b[4] = {0.0, 0.0, 0.0, 0.0};
    double l[4] = {0.0, 0.0, 0.0, 0.0};
    bool hasB[4] = {false, false, false, false};
    bool hasL[4] = {false, false, false, false};

    std::string key;
    char eq = '\0';
    double value = 0.0;
    while (in >> key >> eq >> value) {
        if (key.size() == 2 && (key[0] == 'B' || key[0] == 'L') &&
            key[1] >= '0' && key[1] <= '3') {
            const int idx = key[1] - '0';
            if (key[0] == 'B') {
                b[idx] = value;
                hasB[idx] = true;
            } else {
                l[idx] = value;
                hasL[idx] = true;
            }
        }
    }

    for (int i = 0; i < 4; ++i) {
        if (!hasB[i] || !hasL[i]) {
            return false;
        }
    }

    packet.corner_lat[0] = b[3];
    packet.corner_lon[0] = l[3];
    packet.corner_lat[1] = b[0];
    packet.corner_lon[1] = l[0];
    packet.corner_lat[2] = b[2];
    packet.corner_lon[2] = l[2];
    packet.corner_lat[3] = b[1];
    packet.corner_lon[3] = l[1];
    packet.corner_lat[4] = (b[0] + b[1] + b[2] + b[3]) / 4.0;
    packet.corner_lon[4] = (l[0] + l[1] + l[2] + l[3]) / 4.0;
    return true;
}

static GMTIResultPacket buildResultPacketSnapshot(const Config& cfg,
                                                  const std::vector<GMTIDetection>& targets,
                                                  uint64_t acquisitionCycleId = 0U)
{
    GMTIResultPacket packet;
    packet.targets = targets;
    packet.result_file_id = cfg.result_file_id;
    packet.dbs_out_res_m = cfg.dbs_out_res_m;
    packet.squint_angle = cfg.squint_angle;
    packet.squint_side = cfg.squint_side;
    packet.acquisition_cycle_id = acquisitionCycleId;
    packet.config_generation = cfg.config_generation;
    if (cfg.enable_dbs_fusion) {
        (void)loadDbsImageProduct(cfg, packet);
        (void)loadDbsCornerProduct(cfg, packet);
    }
    return packet;
}

static bool runGMTIProcessingFlow(
    MainCtrl* host,
    const std::shared_ptr<const Config>& configSnapshot,
    const std::string& echoFile,
    int forcedResultId = -1,
    int forcedFilePeriodIndex = -1,
    const EchoCycleView* cycleView = nullptr,
    const EchoCycleMetadata* cycleMetadata = nullptr)
{
    TIMING_SCOPE(main_total);
    const auto cycle_start = std::chrono::steady_clock::now();
    if (!host || !configSnapshot) {
        return false;
    }

    // security::reset_gate();

    // run_checkpoint(security::CheckpointId::ProgramStartup, "program_startup");
    // run_checkpoint(security::CheckpointId::SarParameterInit, "sar_parameter_init");
    // run_checkpoint(security::CheckpointId::ImagingKernel, "imaging_kernel");
    // run_checkpoint(security::CheckpointId::RangeCmp, "range_cmp");
    // run_checkpoint(security::CheckpointId::MoCo, "moco");
    // run_checkpoint(security::CheckpointId::AzComp, "az_comp");
    // run_checkpoint(security::CheckpointId::PGA, "pga");

    // if (!security::final_decision()) {
    //     std::cout << "[gate] final_decision: failed\n";
    //     return 1;
    // }

    // The command thread publishes immutable snapshots. Keep the per-cycle
    // copy local for the entire processing lifetime: writing host->cfg_ here
    // would let a concurrent mode command replace fields halfway through FFT,
    // DBS, detection or positioning.
    Config processingCfg = *configSnapshot;
    processingCfg.echo_cycle_view = cycleView ? *cycleView : EchoCycleView();
    if (cycleMetadata && cycleMetadata->partial_scan) {
        if (processingCfg.scan_mode != ScanMode::Electronic ||
            cycleMetadata->scan_beam_count == 0U ||
            cycleMetadata->processing_beam_count == 0U) {
            std::cerr << "[SHM][INTEGRITY][ERR] partial scan has no processable electronic beams"
                      << std::endl;
            return false;
        }
        processingCfg.new_protocol_file_first_beam = cycleMetadata->first_scan_beam;
        processingCfg.new_protocol_file_scan_beam_count = static_cast<int>(
            cycleMetadata->scan_beam_count);
        processingCfg.new_protocol_file_period_index = 0;
        processingCfg.shm_scan_beam_count = static_cast<int>(cycleMetadata->scan_beam_count);
        processingCfg.wavepos_st = std::max(processingCfg.wavepos_st,
                                            cycleMetadata->first_scan_beam);
        processingCfg.wavepos_ed = std::min(processingCfg.wavepos_ed,
                                            cycleMetadata->last_scan_beam);
        std::cout << "[SHM][INTEGRITY] status=" << cycleMetadata->integrity_status
                  << " missing_leading_prts=" << cycleMetadata->missing_leading_prt_count
                  << " available_beams=" << cycleMetadata->first_scan_beam << ".."
                  << cycleMetadata->last_scan_beam << " processing_beams="
                  << processingCfg.wavepos_st << ".." << processingCfg.wavepos_ed
                  << std::endl;
    }

    if (!echoFile.empty()) {
        if (processingCfg.INFO_Type) {
            processingCfg.GMTI_Data_new = echoFile;
        } else {
            std::string legacy_input_error;
            if (!configureLegacySeparatedFileInput(
                    processingCfg, echoFile, legacy_input_error)) {
                std::cerr << "[LOCAL-TEST][LEGACY][ERR] "
                          << legacy_input_error << std::endl;
                return false;
            }
        }
        if (forcedFilePeriodIndex >= 0) {
            processingCfg.new_protocol_file_period_index = forcedFilePeriodIndex;
            std::cout << "[LOCAL-TEST] Use file period index "
                      << forcedFilePeriodIndex << " for echo file: "
                      << echoFile << std::endl;
        }
        // forcedResultId==0 requests a fresh result id even when the SSD test
        // echo filename has a fixed ID. This prevents consecutive test cycles
        // from overwriting one another and preserves TrackManager history.
        int trackId = forcedResultId > 0 ? forcedResultId :
            (forcedResultId == 0 ? -1 : extractFileIdFromPath(echoFile));
        if (trackId <= 0) {
            if (!allocateNextResultId(host->gmti_proc_, processingCfg, trackId)) {
                std::cerr << "[ERR] Cannot allocate incremental GMTI result id from result_add: "
                          << processingCfg.result_add << std::endl;
                return false;
            }
            std::cout << "[WARN] Echo filename has no trailing id. Use next result id "
                      << trackId << " for GMTI result and track window." << std::endl;
        }
        processingCfg.result_file_id = trackId;
        if (forcedFilePeriodIndex >= 0) {
            processingCfg.stage2_period_id = forcedFilePeriodIndex;
        } else if (forcedResultId > 0) {
            // In local replay each BIN is an independent one-period file and
            // therefore has no container period index.  The forced result id
            // is the replay's logical cycle identity; propagate it to the
            // detection/CSI/track audit records instead of retaining the XML
            // template's period 0 for every file.
            processingCfg.stage2_period_id = forcedResultId - 1;
        }
        processingCfg.track_idx_range = buildTrackIdxRange(
            trackId, processingCfg.track_idx_window);
        if (forcedResultId > 0) {
            std::cout << "[LOCAL-TEST] Use forced result id " << forcedResultId
                      << " for echo file: " << echoFile << std::endl;
        }
    } else if (cycleView) {
        int trackId = forcedResultId;
        if (trackId <= 0 &&
            !allocateNextResultId(host->gmti_proc_, processingCfg, trackId)) {
            std::cerr << "[ERR] Cannot allocate result id for acquisition cycle "
                      << cycleView->acquisition_cycle_id << std::endl;
            return false;
        }
        processingCfg.result_file_id = trackId;
        processingCfg.track_idx_range = buildTrackIdxRange(
            trackId, processingCfg.track_idx_window);
    }

    if (processingCfg.track_idx_range.empty()) {
        std::cerr << "track_idx_range is empty after GMTI file override" << std::endl;
        return false;
    }

    if (processingCfg.scan_mode == ScanMode::Mechanical) {
        std::string mechanicalError;
        if (!prepareMechanicalScanRuntime(processingCfg, &mechanicalError)) {
            std::cerr << "[ERR] 机械扫描元数据/CPI 准备失败: "
                      << mechanicalError << std::endl;
            gmti::runtime::finishRun(processingCfg, false, 1, mechanicalError);
            return false;
        }
    }
    gmti::runtime::initializeRun(processingCfg, host->gmti_config_xml_, "GMTI_pipe_core");
    const auto snapshot_time = std::chrono::system_clock::now();
    gmti::runtime::recordTiming(
        "config_snapshot",
        snapshot_time,
        snapshot_time,
        0,
        -1,
        "startup XML snapshot + per-cycle input/id override");

    std::vector<std::vector<double>> posMatrix;
    int POS_num = 0;
    if (!processingCfg.INFO_Type) {
        if (!host->gmti_proc_.POS_dataread(processingCfg.Plane_POS_add, posMatrix, POS_num)) {
            std::cerr << "[ERR] 读取 POS 失败: " << processingCfg.Plane_POS_add << std::endl;
            gmti::runtime::finishRun(processingCfg, false, 1, "POS_dataread failed");
            return false;
        }
    }

    GMTIOutput::Plane plane;
    if (!host->gmti_proc_.extractPlanePV(posMatrix, processingCfg, plane)) {
        std::cerr << "[ERR] 飞机位置提取失败" << std::endl;
        gmti::runtime::finishRun(processingCfg, false, 1, "extractPlanePV failed");
        return false;
    }

    std::vector<int> beamList;
    const bool listOk = processingCfg.scan_mode == ScanMode::Mechanical
        ? host->gmti_proc_.makeMechanicalCpiList(processingCfg, beamList)
        : host->gmti_proc_.makeBeamList(plane, processingCfg, beamList);
    if (!listOk) {
        std::cerr << "[ERR] 生成扫描处理窗口列表失败" << std::endl;
        gmti::runtime::finishRun(processingCfg, false, 1, "makeBeamList failed");
        return false;
    }

    if (processingCfg.track_idx_range.empty()) {
        processingCfg.track_idx_range = beamList;
    }

    std::vector<GMTIOutput> beamResults;
    const std::vector<std::vector<double>> emptyPos;
    FusionGroupContext fusionCtx;
    const std::vector<std::vector<double>> &posSource = processingCfg.INFO_Type
        ? emptyPos : posMatrix;
    bool processOk = false;
    const auto algorithm_start = std::chrono::steady_clock::now();
    {
        TIMING_SCOPE(gmti_processing);
        processOk = processingCfg.enable_dbs_fusion
            ? host->gmti_proc_.processBeamsParallelFusion(beamList,
                                                          processingCfg,
                                                          posSource,
                                                          fusionCtx,
                                                          beamResults)
            : host->gmti_proc_.processBeamsParallel(beamList,
                                                    processingCfg,
                                                    posSource,
                                                    beamResults);
    }
    const auto algorithm_end = std::chrono::steady_clock::now();
    if (!processOk) {
        std::cerr << "[ERR] GMTI period processing failed" << std::endl;
        gmti::runtime::finishRun(
            processingCfg, false, 1, "period processing reported failure");
        return false;
    }
    if (processingCfg.enable_dbs_fusion) {
        TIMING_SCOPE(dbs_processing);
        if (!runDbsFusionImaging(
                fusionCtx, processingCfg, processingCfg.dbs_mosaic_use_gpu)) {
            std::cerr << "[WARN] DBS fusion imaging failed; continue with GMTI detections" << std::endl;
        }
    }
    const auto dbs_end = std::chrono::steady_clock::now();

    std::vector<double> MT_acc;
    std::vector<GMTIOutput::DetectionCsvRecord> detectionCsvRecords;
    for (size_t i = 0; i < beamResults.size(); ++i) {
        auto& res = beamResults[i];
        if (!res.MT.empty()) {
            MT_acc.insert(MT_acc.end(), std::make_move_iterator(res.MT.begin()),
                          std::make_move_iterator(res.MT.end()));
        }
        detectionCsvRecords.insert(detectionCsvRecords.end(),
                                   res.detection_records.begin(),
                                   res.detection_records.end());
        // std::cout << "[OK] Beam " << beamList[i] << " 完成" << std::endl;
    }

    const LowRadialVelocityFilterStats lowVrStats = filterLowRadialVelocity(
        detectionCsvRecords, MT_acc,
        processingCfg.low_radial_velocity_filter_enabled,
        processingCfg.low_radial_velocity_threshold_mps);
    std::cout << "[LOW-VR-FILTER] enabled="
              << (processingCfg.low_radial_velocity_filter_enabled ? 1 : 0)
              << " threshold_mps=" << processingCfg.low_radial_velocity_threshold_mps
              << " input=" << lowVrStats.input_count
              << " removed_low_velocity=" << lowVrStats.removed_low_velocity_count
              << " removed_nonfinite=" << lowVrStats.removed_nonfinite_count
              << " output=" << lowVrStats.output_count << std::endl;

    const P38SlopeUpperBoundFilterStats p38SlopeStats = filterP38SlopeUpperBound(
        detectionCsvRecords, MT_acc,
        processingCfg.p38_slope_upper_bound_filter_enabled,
        processingCfg.p38_slope_upper_bound_rad_per_hz);
    std::cout << "[P38-SLOPE-FILTER] enabled="
              << (processingCfg.p38_slope_upper_bound_filter_enabled ? 1 : 0)
              << " upper_bound_rad_per_hz="
              << processingCfg.p38_slope_upper_bound_rad_per_hz
              << " input=" << p38SlopeStats.input_count
              << " removed_upper_bound=" << p38SlopeStats.removed_upper_bound_count
              << " removed_nonfinite=" << p38SlopeStats.removed_nonfinite_count
              << " output=" << p38SlopeStats.output_count << std::endl;

    const AdjacentBeamDuplicateSuppressionStats duplicateStats =
        suppressAdjacentBeamDuplicates(
            detectionCsvRecords, MT_acc,
            processingCfg.adjacent_beam_duplicate_suppression_enabled,
            processingCfg.adjacent_beam_duplicate_position_gate_m,
            processingCfg.adjacent_beam_duplicate_velocity_gate_mps);
    std::cout << "[ADJACENT-BEAM-NMS] enabled="
              << (processingCfg.adjacent_beam_duplicate_suppression_enabled ? 1 : 0)
              << " position_gate_m=" << processingCfg.adjacent_beam_duplicate_position_gate_m
              << " velocity_gate_mps=" << processingCfg.adjacent_beam_duplicate_velocity_gate_mps
              << " input=" << duplicateStats.input_count
              << " removed_duplicates=" << duplicateStats.removed_duplicate_count
              << " output=" << duplicateStats.output_count << std::endl;

    const MechanicalCpiDedupStats mechanicalDedupStats =
        deduplicateMechanicalDetections(
            detectionCsvRecords, MT_acc, processingCfg);
    if (processingCfg.scan_mode == ScanMode::Mechanical) {
        std::cout << "[MECHANICAL-CPI-DEDUP] enabled="
                  << (processingCfg.mechanical_scan.enable_cpi_dedup ? 1 : 0)
                  << " overlap="
                  << (processingCfg.mechanical_scan.cpi_step_pulse <
                      processingCfg.mechanical_scan.cpi_pulse_count ? 1 : 0)
                  << " input=" << mechanicalDedupStats.input_count
                  << " removed_duplicates="
                  << mechanicalDedupStats.removed_duplicate_count
                  << " output=" << mechanicalDedupStats.output_count << std::endl;
    }

    bool wroteCurrentDetections = false;
    {
        // 空检测是一个有效周期状态，仍写 2-byte count=0 文件供航迹模块读取。
        TIMING_SCOPE(result_write);
        if (!host->gmti_proc_.writeResult(MT_acc, processingCfg)) {
            std::cerr << "[ERR] 整周期检测结果写盘失败" << std::endl;
        } else {
            wroteCurrentDetections = true;
        }
    }

    if (processingCfg.runtime_diagnostics_enabled ||
        processingCfg.detection_results_csv_dump) {
        TIMING_SCOPE(detection_result_csv_write);
        const std::string sourceFile = makeResultProductPath(processingCfg, "bin");
        if (!host->gmti_proc_.writeDetectionCsv(
                detectionCsvRecords, processingCfg, sourceFile)) {
            std::cerr << "[ERR] detection_results.csv 写盘失败" << std::endl;
        }
    }
    if (processingCfg.scan_mode == ScanMode::Mechanical) {
        std::string mechanicalError;
        if (!writeMechanicalDetectionResults(
                processingCfg, detectionCsvRecords, &mechanicalError)) {
            std::cerr << "[ERR] mechanical_detection_results.csv 写盘失败: "
                      << mechanicalError << std::endl;
        }
    }

    if (!wroteCurrentDetections) {
        std::cout << "[TRACK][WARN] 本扫描周期未写入新的检测结果文件，关联窗口中的当前编号文件可能是旧数据。" << std::endl;
    }
    std::vector<GMTIDetection> currentTargets;
    {
        TIMING_SCOPE(tracking);
        currentTargets = trackModuleOnline(processingCfg, &host->track_manager_);
    }
    std::cout << "[GMTI][RESULT] filtered_detection_count="
              << detectionCsvRecords.size()
              << " confirmed_current_detection_count=" << currentTargets.size()
              << " protocol_target_count=" << currentTargets.size() << std::endl;
    if (processingCfg.enable_result_return) {
        GMTIResultPacket packet = buildResultPacketSnapshot(
            processingCfg, currentTargets,
            cycleMetadata ? cycleMetadata->acquisition_cycle_id : 0U);
        std::lock_guard<std::mutex> lock(host->result_mutex_);
        if (host->pending_result_packets_.size() >= kMaxPendingResultPackets) {
            std::cerr << "[RESULT][DROP] result FIFO backlog is full (limit="
                      << kMaxPendingResultPackets
                      << "); current cycle packet was not enqueued" << std::endl;
            host->IsResultReady.store(true);
        } else {
            host->latest_gmti_targets_ = currentTargets;
            host->pending_result_packets_.push_back(std::move(packet));
            host->IsResultReady.store(true);
        }
    } else {
        // Disk products above are intentionally retained. Do not construct or
        // enqueue a protocol packet when result return is disabled, so a FIFO
        // reader cannot block this processing path.
        std::lock_guard<std::mutex> lock(host->result_mutex_);
        host->latest_gmti_targets_.clear();
        host->pending_result_packets_.clear();
        host->IsResultReady.store(false);
        std::cout << "[RESULT] main_control_result_return=disabled; "
                  << "results were written to " << processingCfg.result_add
                  << std::endl;
    }
    gmti::runtime::finishRun(processingCfg, true, 0, "normal exit");
    const auto cycle_end = std::chrono::steady_clock::now();
    const long long total_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        cycle_end - cycle_start).count();
    if (total_ms > kSlowCycleDiagnosticThresholdMs) {
        const long long pre_algorithm_ms =
            std::chrono::duration_cast<std::chrono::milliseconds>(
                algorithm_start - cycle_start).count();
        const long long algorithm_ms =
            std::chrono::duration_cast<std::chrono::milliseconds>(
                algorithm_end - algorithm_start).count();
        const long long dbs_ms =
            std::chrono::duration_cast<std::chrono::milliseconds>(
                dbs_end - algorithm_end).count();
        const long long post_dbs_ms =
            std::chrono::duration_cast<std::chrono::milliseconds>(
                cycle_end - dbs_end).count();
        std::cout << "[TIMING][SLOW-CYCLE] threshold_ms="
                  << kSlowCycleDiagnosticThresholdMs
                  << " total_ms=" << total_ms
                  << " pre_algorithm_ms=" << pre_algorithm_ms
                  << " algorithm_ms=" << algorithm_ms
                  << " dbs_ms=" << dbs_ms
                  << " post_dbs_ms=" << post_dbs_ms << std::endl;
    }
    return true;
}

static void printModeSwitchCmd(const ModeSwitchCmd& cmd,
                               uint64_t command_index,
                               std::size_t frame_bytes)
{
    const std::ios::fmtflags saved_flags = std::cout.flags();
    const std::streamsize saved_precision = std::cout.precision();
    const char saved_fill = std::cout.fill();

    std::cout << std::fixed << std::setprecision(6);

    // Command reception is always logged.  In particular, test=1/2 must
    // expose the command actually sent by the controller even when the
    // workMode is not one of the production WAGMTI values.
    std::cout << "[CMD][RECV] index=" << command_index
              << " frame_bytes=" << frame_bytes << "\n";
    std::cout << "====== ModeSwitchCmd ======\n";
    std::cout << "head              : 0x" << std::hex << std::uppercase << cmd.head << std::dec << "\n";
    std::cout << "algoType          : 0x" << std::hex << std::uppercase << std::setw(2) << std::setfill('0')
              << static_cast<int>(cmd.algoType) << std::dec << "\n";
    std::cout << "dataLen           : " << cmd.dataLen << "\n";
    std::cout << "workMode          : " << cmd.workMode << "\n";
    std::cout << "CenterFreq        : " << cmd.CenterFreq << " Hz\n";
    std::cout << "SamplingRate      : " << cmd.SamplingRate << " Hz\n";
    std::cout << "PulseWidth        : " << cmd.PulseWidth << " s\n";
    std::cout << "BandWidth         : " << cmd.BandWidth << " Hz\n";
    std::cout << "PRF               : " << cmd.prf << " Hz\n";
    std::cout << "Kr_Sign           : " << cmd.Kr_Sign << "\n";
    std::cout << "SampleDelay       : " << cmd.SampleDelay << " s\n";
    std::cout << "updatePointNum    : " << cmd.updatePointNum << "\n";
    std::cout << "reserved0         : " << cmd.reserved0 << "\n";
    std::cout << "LookSide          : " << cmd.LookSide << "\n";
    std::cout << "LookDownAngle     : " << cmd.LookDownAngle << " rad\n";
    std::cout << "SquintAngle       : " << cmd.SquintAngle << " deg\n";
    std::cout << "Theta_bw          : " << cmd.Theta_bw << " rad\n";
    std::cout << "alt_scene         : " << cmd.alt_scene << " m\n";
    std::cout << "Rmin              : " << cmd.Rmin << " m\n";
    std::cout << "rfFreq            : " << cmd.rfFreq << " Hz\n";
    std::cout << "Kr                : " << cmd.Kr << " Hz/s\n";
    std::cout << "ADsamplingLen     : " << cmd.ADsamplingLen << "\n";
    std::cout << "channelSpace      : " << cmd.channelSpace << "\n";
    std::cout << "velocity          : " << cmd.velocity << " m/s\n";
    std::cout << "swath             : " << cmd.swath << " m\n";
    std::cout << "antennaAz         : " << cmd.antennaAz << " deg\n";
    std::cout << "antennaEl         : " << cmd.antennaEl << " deg\n";
    std::cout << "targetLon         : " << cmd.targetLon << " deg\n";
    std::cout << "targetLat         : " << cmd.targetLat << " deg\n";
    std::cout << "targetAlt         : " << cmd.targetAlt << " m\n";
    std::cout << "targetRange       : " << cmd.targetRange << " m\n";
    std::cout << "reserved1         : " << static_cast<unsigned>(cmd.reserved1) << "\n";
    std::cout << "tail              : 0x" << std::hex << cmd.tail << std::dec << "\n";
    std::cout << "===========================\n";

    std::cout.flags(saved_flags);
    std::cout.precision(saved_precision);
    std::cout.fill(saved_fill);
}

bool run_checkpoint(security::CheckpointId id, const char* name) {
    const security::GateCode code = security::checkpoint(id);
    std::cout << "[gate] " << name << ": "
              << security::gate_code_to_string(code) << "\n";
    return code == security::GateCode::Ok;
}

} // namespace

MainCtrl::MainCtrl(const Config& initialConfig,
                   const std::string& configPath,
                   bool enablePipes)
    : cfg_(initialConfig),
      gmti_config_xml_(configPath),
      pipe_root_path_(initialConfig.pipe_root_path),
      pipes_enabled_(enablePipes),
      base_cfg_(initialConfig)
{
    IsResultReady.store(false);
    m_workmode.store(Mode_INIT);
}

MainCtrl::~MainCtrl()
{
    StopThreads();
    if (m_SendBuf) {
        std::free(m_SendBuf);
        m_SendBuf = nullptr;
    }
}

bool MainCtrl::deriveAndValidateConfig(Config& cfg, std::string& error) const
{
    std::transform(cfg.echo_input_mode.begin(), cfg.echo_input_mode.end(),
                   cfg.echo_input_mode.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    std::transform(cfg.iq_data_type.begin(), cfg.iq_data_type.end(),
                   cfg.iq_data_type.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    std::transform(cfg.test_cycle_data_playback.begin(),
                   cfg.test_cycle_data_playback.end(),
                   cfg.test_cycle_data_playback.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    std::string canonical_output_source;
    if (!gmti::tracking::canonicalizeTrackOutputStateSource(
            cfg.track_output_state_source, canonical_output_source)) {
        error = "track_output_state_source must be measurement, prediction, or kalman_filtered";
        return false;
    }
    cfg.track_output_state_source = canonical_output_source;
    if (cfg.echo_input_mode != "file" && cfg.echo_input_mode != "shm") {
        error = "echo_input_mode must be file or shm";
        return false;
    }
    if (cfg.test_mode != 0 && cfg.test_mode != 1 && cfg.test_mode != 2) {
        error = "test must be 0, 1, or 2";
        return false;
    }
    if (cfg.test_cycle_data_playback != "once" &&
        cfg.test_cycle_data_playback != "loop") {
        error = "test_cycle_data_playback must be once or loop";
        return false;
    }
    if (cfg.INFO_Type != 0 && cfg.INFO_Type != 1) {
        error = "INFO_Type must be 0 or 1";
        return false;
    }
    if (cfg.echo_input_mode == "shm" && cfg.INFO_Type != 1) {
        error = "shared-memory phase-1 input requires INFO_Type=1";
        return false;
    }
    if (cfg.pulse_len <= 0 || cfg.pulse_num <= 0 || cfg.rg_len <= 0 ||
        cfg.info_len <= 0 || cfg.PRF <= 0.0 || cfg.fc <= 0.0 ||
        cfg.fs <= 0.0 || cfg.Br <= 0.0 || cfg.Tr <= 0.0) {
        error = "critical radar size/frequency parameter is non-positive";
        return false;
    }
    if (cfg.wavepos_skip <= 0 || cfg.wavepos_ed < cfg.wavepos_st) {
        error = "invalid wave position range/step";
        return false;
    }
    if (cfg.INFO_Type && cfg.echo_input_mode == "file" &&
        cfg.wavepos_st < cfg.new_protocol_file_first_beam) {
        std::ostringstream oss;
        oss << "new-protocol processing starts at beam " << cfg.wavepos_st
            << " before file_first_beam " << cfg.new_protocol_file_first_beam;
        error = oss.str();
        return false;
    }
    if (cfg.new_protocol_file_scan_beam_count < 0 ||
        cfg.new_protocol_file_period_index < 0) {
        error = "new-protocol file scan beam count/period index is invalid";
        return false;
    }
    if (cfg.result_add.empty() || cfg.pipe_root_path.empty()) {
        error = "result_add and pipe_root_path must be non-empty";
        return false;
    }
    if (cfg.new_protocol_channel_count <= 0 ||
        cfg.new_protocol_read_channel_1 < 1 ||
        cfg.new_protocol_read_channel_2 < 1 ||
        cfg.new_protocol_read_channel_1 > cfg.new_protocol_channel_count ||
        cfg.new_protocol_read_channel_2 > cfg.new_protocol_channel_count) {
        error = "new-protocol channel count/selection is invalid";
        return false;
    }
    if (cfg.enable_four_channel_fusion &&
        (cfg.new_protocol_channel_count < 4 ||
         cfg.four_channel_fusion_channel_3 < 1 ||
         cfg.four_channel_fusion_channel_3 > cfg.new_protocol_channel_count ||
         cfg.four_channel_fusion_channel_4 < 1 ||
         cfg.four_channel_fusion_channel_4 > cfg.new_protocol_channel_count)) {
        error = "four-channel fusion requires valid channels 3/4 in a >=4-channel protocol";
        return false;
    }
    if (cfg.echo_input_mode == "shm") {
        if (cfg.shm_name.empty() || cfg.shm_name[0] != '/') {
            error = "shm_name must be a non-empty POSIX name beginning with '/'";
            return false;
        }
        if (cfg.shm_read_chunk_bytes == 0U || cfg.shm_expected_prt_mode < 0 ||
            cfg.shm_expected_prt_mode > 255) {
            error = "shared-memory chunk/mode configuration is invalid";
            return false;
        }
    }

    if (cfg.INFO_Type) {
        const std::size_t packet_bytes = gmti::new_protocol::packetBytes(
            static_cast<std::size_t>(cfg.pulse_len),
            static_cast<std::size_t>(cfg.new_protocol_channel_count),
            cfg.iq_data_type);
        if (packet_bytes > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
            error = "derived PRT bytes exceed Config::pkg_bytes range";
            return false;
        }
        cfg.pkg_bytes = static_cast<int>(packet_bytes);
    } else if (cfg.channel_mode == "separate") {
        cfg.pkg_bytes = cfg.info_len + cfg.pulse_len * 2 * 4;
    } else if (cfg.channel_mode == "interleaved") {
        cfg.pkg_bytes = cfg.info_len + cfg.pulse_len * 4 * 4;
    } else {
        error = "unknown channel_mode: " + cfg.channel_mode;
        return false;
    }

    if (cfg.echo_input_mode == "shm" && isLaboratoryTestMode(cfg.test_mode)) {
        EchoCycleLayout test_layout;
        if (!deriveEchoScanLayout(cfg, test_layout, error)) return false;
        if (cfg.test_cycle_data_paths.empty()) {
            error = "test=1/2 requires non-empty <test_cycle_data_paths>";
            return false;
        }
        for (std::size_t i = 0; i < cfg.test_cycle_data_paths.size(); ++i) {
            const std::string& path = cfg.test_cycle_data_paths[i];
            struct stat test_echo_stat;
            if (path.empty() || ::stat(path.c_str(), &test_echo_stat) != 0 ||
                !S_ISREG(test_echo_stat.st_mode)) {
                error = "test=1/2 prepared-cycle file is not an existing regular file: index=" +
                    std::to_string(i) + " path=" + path;
                return false;
            }
            if (test_echo_stat.st_size < static_cast<off_t>(test_layout.cycle_bytes)) {
                std::ostringstream oss;
                oss << "test=1/2 prepared-cycle file is too small: index=" << i
                    << " path=" << path << " size=" << test_echo_stat.st_size
                    << " required_cycle_bytes=" << test_layout.cycle_bytes;
                error = oss.str();
                return false;
            }
        }
    }

    cfg.lambda = C / cfg.fc;
    cfg.R_bin = C / (2.0 * cfg.fs);
    cfg.Rg.resize(static_cast<std::size_t>(cfg.rg_len));
    for (int i = 0; i < cfg.rg_len; ++i) {
        // 定位/DBS 距离轴固定使用 Rmin + bin*Rbin。新协议头中的
        // SampleDelay 只参与输入时序解释，不再改变定位斜距原点。
        cfg.Rg[static_cast<std::size_t>(i)] =
            cfg.R_min + static_cast<double>(i) * cfg.R_bin;
    }
    const int process_pulses = effectivePulseNum(cfg);
    if (process_pulses <= 0) {
        error = "effective processing pulse count is non-positive";
        return false;
    }
    cfg.fd_res = cfg.PRF / static_cast<double>(process_pulses);
    cfg.az_st = 1;
    cfg.az_ed = process_pulses;
    cfg.az_center = (cfg.az_st + cfg.az_ed) / 2;
    cfg.Loc = true;

    if (!runtime_mode_override_.empty()) {
        cfg.runtime_mode = runtime_mode_override_;
        std::transform(cfg.runtime_mode.begin(), cfg.runtime_mode.end(),
                       cfg.runtime_mode.begin(),
                       [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
        cfg.runtime_diagnostics_enabled = !(
            cfg.runtime_mode == "release" || cfg.runtime_mode == "formal" ||
            cfg.runtime_mode == "production");
    }
    if (runtime_diagnostics_override_set_) {
        cfg.runtime_diagnostics_enabled = runtime_diagnostics_override_;
    }
    if (!cfg.runtime_diagnostics_enabled) {
        cfg.p38_diagnostics_dump = false;
        cfg.debug_pc_peak = false;
        cfg.motion_comp_debug = false;
        // 与离线入口一致：release 默认不采集 CSI 指标，但应尊重 XML
        // 对该审计项的显式开启请求。
        if (!cfg.csi_metrics_enable) {
            cfg.csi_metrics_dump_power_maps = false;
        }
    }
    // 与离线主程序保持一致：debug/trace 管道周期自动采集逐波位 CSI
    // 对消改善因子（仅标量汇总，不保存功率图）；release 不改变实时开销。
    if (cfg.runtime_diagnostics_enabled && !cfg.csi_metrics_enable) {
        cfg.csi_metrics_enable = true;
        cfg.csi_metrics_dump_power_maps = false;
    }

    if (!ensureResultDirectory(cfg.result_add, error)) {
        return false;
    }
    struct stat st;
    if (::stat(cfg.result_add.c_str(), &st) != 0 || !S_ISDIR(st.st_mode)) {
        error = "result_add is not a directory: " + cfg.result_add;
        return false;
    }
    if (pipes_enabled_) {
        if (!ensureResultDirectory(cfg.pipe_root_path, error)) {
            return false;
        }
        if (::stat(cfg.pipe_root_path.c_str(), &st) != 0 || !S_ISDIR(st.st_mode)) {
            error = "pipe_root_path is not an existing directory after create attempt: " +
                cfg.pipe_root_path;
            return false;
        }
    }
    return true;
}

bool MainCtrl::configureEchoBuffers(const Config& cfg,
                                    bool reconfigure,
                                    std::string& error)
{
    if (cfg.echo_input_mode != "shm") return true;
    EchoCycleLayout layout;
    if (!deriveEchoScanLayout(cfg, layout, error)) return false;

    if (isLaboratoryTestMode(cfg.test_mode)) {
        // Laboratory modes never materialize a raw cycle. test=1 keeps the
        // payload opaque; test=2 keeps only the bounded parser staging needed
        // for audit and never passes a parsed PRT to the assembler.
        try {
            std::unique_ptr<TestCycleByteAccumulator> replacement_accumulator(
                new TestCycleByteAccumulator(static_cast<uint64_t>(layout.cycle_bytes)));
            std::unique_ptr<ShmProtocolAudit> replacement_auditor;
            if (cfg.test_mode == 2) {
                replacement_auditor.reset(new ShmProtocolAudit(
                    layout, cfg.shm_read_chunk_bytes));
            }
            test_cycle_accumulator_ = std::move(replacement_accumulator);
            test_protocol_auditor_ = std::move(replacement_auditor);
        } catch (const std::exception& e) {
            error = e.what();
            return false;
        }
        cycle_assembler_.reset();
        {
            std::lock_guard<std::mutex> lock(config_mutex_);
            prt_parser_.reset();
        }
        return true;
    }

    test_cycle_accumulator_.reset();
    test_protocol_auditor_.reset();
    const uint64_t available = readMemAvailableBytes();
    const uint64_t double_buffer_bytes = layout.cycle_bytes <=
        std::numeric_limits<uint64_t>::max() / 2U
        ? static_cast<uint64_t>(layout.cycle_bytes) * 2U
        : std::numeric_limits<uint64_t>::max();
    const uint64_t allocation_reserve = 512U * 1024U * 1024U;
    if (available > 0U &&
        (double_buffer_bytes > available ||
         allocation_reserve > available - double_buffer_bytes)) {
        std::ostringstream oss;
        oss << "insufficient MemAvailable for complete-scan double buffer: need="
            << double_buffer_bytes << " reserve=" << allocation_reserve
            << " available=" << available;
        error = oss.str();
        return false;
    }

    // Construct every fallible replacement before changing the live
    // assembler. Otherwise a parser allocation failure could leave the
    // assembler on the new layout while runtime_config_ still describes the
    // old one.
    std::shared_ptr<PrtStreamParser> replacementParser;
    try {
        replacementParser.reset(new PrtStreamParser(
            layout.prt_bytes, cfg.shm_read_chunk_bytes, layout.expected_prt_mode));
    } catch (const std::exception& e) {
        error = e.what();
        return false;
    }

    if (reconfigure && cycle_assembler_) {
        if (!cycle_assembler_->reconfigure(layout, error)) return false;
    } else {
        try {
            cycle_assembler_.reset(new EchoCycleAssembler(layout));
        } catch (const std::bad_alloc&) {
            error = "insufficient memory for two complete echo cycles";
            return false;
        } catch (const std::exception& e) {
            error = e.what();
            return false;
        }
    }

    {
        std::lock_guard<std::mutex> lock(config_mutex_);
        prt_parser_ = replacementParser;
    }
    return true;
}

void MainCtrl::printStartupConfig(const Config& cfg) const
{
    EchoCycleLayout layout;
    std::string layout_error;
    const bool layout_ok = deriveEchoScanLayout(cfg, layout, layout_error);
    std::size_t scan_beam_count = 0U;
    std::size_t processing_beam_count = 0U;
    std::size_t prts_per_cycle = 0U;
    std::size_t cycle_bytes = 0U;
    if (layout_ok) {
        scan_beam_count = layout.scan_beam_count;
        processing_beam_count = layout.processing_beam_count;
        prts_per_cycle = layout.prts_per_cycle;
        cycle_bytes = layout.cycle_bytes;
    }
    const uint64_t available = readMemAvailableBytes();
    const char* input_mode_log = cfg.echo_input_mode == "shm"
        ? "shared_memory" : "file";
    std::cout << "[STARTUP] runtime_mode=" << cfg.runtime_mode << '\n'
              << "[STARTUP] config_path=" << gmti_config_xml_ << '\n'
              << "[STARTUP] test=" << cfg.test_mode << '\n'
              << "[STARTUP] input_mode=" << input_mode_log << '\n'
              << "[STARTUP][CYCLE] prt_bytes=" << cfg.pkg_bytes
              << " prts_per_beam=" << cfg.pulse_num
              << " beam_count=" << scan_beam_count
              << " prts_per_cycle=" << prts_per_cycle
              << " cycle_bytes=" << cycle_bytes << std::endl;
    if (!cfg.runtime_diagnostics_enabled) {
        std::cout << "[STARTUP] mode=" << cfg.runtime_mode
                  << " input=" << cfg.echo_input_mode
                  << " beams=" << processing_beam_count
                  << " prts_per_scan=" << prts_per_cycle
                  << " result_add=" << cfg.result_add
                  << " track_source=" << cfg.track_output_state_source
                  << " track_vis_dump=" << (cfg.track_debug_dump ? "on" : "off")
                  << " motion_comp=" << (cfg.motion_comp_enable ? "on" : "off")
                  << " p38_enhanced=" << (cfg.p38_enhanced_enable ? "on" : "off")
                  << " p38_dump=" << (cfg.p38_diagnostics_dump ? "on" : "off")
                  << " dynamic_cfar=" << (cfg.dynamic_cfar_enable ? "on" : "off")
                  << std::endl;
        return;
    }
    std::cout << "[STARTUP] XML absolute path: " << gmti_config_xml_ << '\n'
              << "[STARTUP] XML read: success\n"
              << "[STARTUP] echo_input_mode: " << cfg.echo_input_mode << '\n'
              << "[STARTUP] test_mode: " << cfg.test_mode
              << " (" << testModeDescription(cfg.test_mode) << ")" << '\n'
              << "[STARTUP] shm_name: " << cfg.shm_name << '\n'
              << "[STARTUP] pipe_root_path: " << cfg.pipe_root_path << '\n'
              << "[STARTUP] prt_bytes: " << cfg.pkg_bytes << '\n'
              << "[STARTUP] acquisition_unit: complete_scan\n"
              << "[STARTUP] scan_first_beam: " << layout.scan_first_beam << '\n'
              << "[STARTUP] scan_beam_count: " << scan_beam_count << '\n'
              << "[STARTUP] processing_beam_count: " << processing_beam_count << '\n'
              << "[STARTUP] prts_per_beam: " << cfg.pulse_num << '\n'
              << "[STARTUP] prts_per_scan: " << prts_per_cycle << '\n'
              << "[STARTUP] scan_bytes: " << cycle_bytes << '\n'
              << "[STARTUP] payload_buffer_bytes: "
              << (isLaboratoryTestMode(cfg.test_mode) ? 0U : (2U * cycle_bytes)) << '\n'
              << "[STARTUP] available_memory: " << available << '\n'
              << "[STARTUP] channel_count: " << cfg.new_protocol_channel_count << '\n'
              << "[STARTUP] four_channel_fusion: "
              << (cfg.enable_four_channel_fusion ? "enabled" : "disabled") << '\n'
              << "[STARTUP] new_protocol_gpu_preprocess: "
              << (cfg.new_protocol_gpu_preprocess ? "enabled" : "disabled") << '\n'
              << "[STARTUP] selected_channels: " << cfg.new_protocol_read_channel_1
              << ',' << cfg.new_protocol_read_channel_2 << '\n'
              << "[STARTUP] result_add: " << cfg.result_add << '\n'
              << "[STARTUP] result_return: "
              << (cfg.enable_result_return ? "enabled" : "disabled") << '\n'
              << "[STARTUP] test_cycle_data_files: "
              << cfg.test_cycle_data_paths.size()
              << " playback=" << cfg.test_cycle_data_playback << '\n'
              << "[STARTUP] track_output_state_source: "
              << cfg.track_output_state_source << '\n'
              << "[STARTUP] config_generation: " << cfg.config_generation << std::endl;
}

bool MainCtrl::initializePipes()
{
    const std::string cmdPipePath = composePipePath(pipe_root_path_, "pipewagmticmd");
    const std::string resultPipePath = composePipePath(pipe_root_path_, "pipewagmtiimage");
    const bool sharedMemoryInput = cfg_.echo_input_mode == "shm";
    // In formal shared-memory operation, echo data has no FIFO: it is read
    // exclusively from shm_name.  The legacy echo FIFO remains available only
    // for explicit file-input compatibility mode.
    std::cout << "[STARTUP] command_pipe=" << cmdPipePath << '\n'
              << "[STARTUP] result_pipe=" << resultPipePath << '\n';
    std::string echoPipePath;
    if (sharedMemoryInput) {
        std::cout << "[STARTUP] echo_pipe=disabled (input_mode=shared_memory)"
                  << std::endl;
    } else {
        echoPipePath = composePipePath(pipe_root_path_, "pipegmtiecho");
        std::cout << "[STARTUP] echo_pipe=" << echoPipePath << std::endl;
    }
    const bool cmdOk = m_RecvCmdPipe.CreatePipe(cmdPipePath.c_str(), O_RDWR | O_NONBLOCK);
    const bool echoOk = sharedMemoryInput ||
        m_RecvEchoPipe.CreatePipe(echoPipePath.c_str(), O_RDWR | O_NONBLOCK);
    const bool resultOk = m_SendResultPipe.CreatePipe(resultPipePath.c_str(), O_RDWR);
    if (!cmdOk || !echoOk || !resultOk) {
        m_RecvCmdPipe.ClosePipe();
        m_RecvEchoPipe.ClosePipe();
        m_SendResultPipe.ClosePipe();
        return false;
    }
    return true;
}

bool MainCtrl::Init()
{
    if (initialized_) return true;
    Config initial = base_cfg_;
    initial.config_generation = 1U;
    initial.echo_cycle_view = EchoCycleView();
    std::string error;
    if (!deriveAndValidateConfig(initial, error)) {
        std::cerr << "[STARTUP][ERR] " << error << std::endl;
        return false;
    }
    if (!configureEchoBuffers(initial, false, error)) {
        std::cerr << "[STARTUP][ERR] echo buffer initialization failed: "
                  << error << std::endl;
        return false;
    }
    if (initial.echo_input_mode == "shm") {
        try {
            shm_receiver_.reset(new ShmEchoReceiver(
                initial.shm_name, initial.shm_read_chunk_bytes));
        } catch (const std::exception& e) {
            std::cerr << "[STARTUP][ERR] shared-memory receiver configuration: "
                      << e.what() << std::endl;
            return false;
        }
        // Shared input has no filename-derived identity. Allocate only when a
        // complete cycle is acquired for processing.
        initial.result_file_id = -1;
        initial.track_idx_range.clear();
    }
    cfg_ = initial;
    base_cfg_ = initial;
    pipe_root_path_ = initial.pipe_root_path;
    {
        std::lock_guard<std::mutex> lock(config_mutex_);
        runtime_config_.reset(new Config(initial));
    }
    printStartupConfig(initial);

    if (!gmti_proc_.prewarmFusionWorkers(initial)) {
        std::cerr << "[STARTUP][ERR] DBS fusion worker prewarm failed" << std::endl;
        return false;
    }

    if (pipes_enabled_) {
        m_SendBuf = static_cast<char*>(std::malloc(40U * 1024U * 1024U));
        if (!m_SendBuf) {
            std::cerr << "[STARTUP][ERR] cannot allocate result send buffer" << std::endl;
            return false;
        }
        if (!initializePipes()) {
            std::cerr << "[STARTUP][ERR] cannot create/open GMTI FIFO set" << std::endl;
            return false;
        }
        if (!InitThread()) return false;
    }
    initialized_ = true;
    return true;
}

bool MainCtrl::InitThread()
{
    if (!pipes_enabled_) {
        return true;
    }

    running_.store(true);

    int ret = 0;

    ret = pthread_create(&thrRecvCmd_, nullptr, OnRecvCmdThread, this);
    if (ret != 0) {
        std::cerr << "pthread_create OnRecvCmdThread failed: "
                  << std::strerror(ret) << std::endl;
        running_.store(false);
        StopThreads();
        return false;
    }
    recv_cmd_started_ = true;

    ret = pthread_create(&thrRecvEcho_, nullptr,
                         isShmMode() ? ShmEchoRecvThread : OnEchoRecvThread,
                         this);
    if (ret != 0) {
        std::cerr << "pthread_create OnEchoRecvThread failed: "
                  << std::strerror(ret) << std::endl;
        running_.store(false);
        StopThreads();
        return false;
    }
    recv_echo_started_ = true;

    ret = pthread_create(&thrProcData_, nullptr, ProcessDataThread, this);
    if (ret != 0) {
        std::cerr << "pthread_create ProcessDataThread failed: "
                  << std::strerror(ret) << std::endl;
        running_.store(false);
        StopThreads();
        return false;
    }
    proc_data_started_ = true;

    ret = pthread_create(&thrSendRes_, nullptr, OnResSendThread, this);
    if (ret != 0) {
        std::cerr << "pthread_create OnResSendThread failed: "
                  << std::strerror(ret) << std::endl;
        running_.store(false);
        StopThreads();
        return false;
    }
    send_res_started_ = true;
    return true;
}

void MainCtrl::StopThreads()
{
    running_.store(false);
    if (cycle_assembler_) {
        cycle_assembler_->shutdown();
    }
    if (test_cycle_accumulator_) {
        test_cycle_accumulator_->shutdown();
    }
    {
        std::lock_guard<std::mutex> lock(echo_feed_mutex_);
        echo_resetting_ = false;
        echo_feed_cv_.notify_all();
    }

    if (pipes_enabled_) {
        m_RecvCmdPipe.ClosePipe();
        m_RecvEchoPipe.ClosePipe();
        m_SendResultPipe.ClosePipe();
    }

    const pthread_t self = pthread_self();
    auto joinThread = [&](pthread_t& thread, bool& started, const char* name) {
        if (!started) {
            thread = {};
            return;
        }
        if (pthread_equal(thread, self)) {
            std::cerr << "[WARN] Skip joining current thread: " << name << std::endl;
            pthread_detach(thread);
            thread = {};
            started = false;
            return;
        }
        const int ret = pthread_join(thread, nullptr);
        if (ret != 0) {
            std::cerr << "pthread_join " << name << " failed: "
                      << std::strerror(ret) << std::endl;
        }
        thread = {};
        started = false;
    };

    joinThread(thrRecvCmd_, recv_cmd_started_, "OnRecvCmdThread");
    joinThread(thrRecvEcho_, recv_echo_started_, "OnEchoRecvThread");
    joinThread(thrProcData_, proc_data_started_, "ProcessDataThread");
    joinThread(thrSendRes_, send_res_started_, "OnResSendThread");
    clearPendingEchoQueue();
    {
        std::lock_guard<std::mutex> lock(result_mutex_);
        pending_result_packets_.clear();
        latest_gmti_targets_.clear();
        IsResultReady.store(false);
    }
}

bool MainCtrl::isShmMode() const
{
    const std::shared_ptr<const Config> snapshot = currentConfigSnapshot();
    return snapshot && snapshot->echo_input_mode == "shm";
}

std::shared_ptr<const Config> MainCtrl::currentConfigSnapshot() const
{
    std::lock_guard<std::mutex> lock(config_mutex_);
    return runtime_config_;
}

bool MainCtrl::applyModeSwitchCmdToRuntime(const ModeSwitchCmd& cmd)
{
    std::shared_ptr<const Config> previous = currentConfigSnapshot();
    if (!previous) {
        std::cerr << "[CMD][ERR] runtime config is not initialized" << std::endl;
        return false;
    }
    if (isLaboratoryTestMode(previous->test_mode)) {
        // In laboratory modes the controller command is diagnostic-only:
        // data transfer is the sole source of a test-cycle trigger.  Do not
        // let an unmatched controller workMode pause byte accounting, and do
        // not use any command parameter as a radar-configuration source.
        {
            std::lock_guard<std::mutex> lock(config_mutex_);
            last_cmd_ = cmd;
        }
        std::cout << "[CMD] command_ignored_for_trigger: test="
                  << previous->test_mode
                  << " workMode=" << cmd.workMode
                  << " (data_transfer_only)" << std::endl;
        return true;
    }
    Config next = *previous;
    next.echo_cycle_view = EchoCycleView();
    next.config_generation = previous->config_generation + 1U;

    // V1.5 command units are Hz, while the XML/runtime Config stores fc in
    // GHz and fs/Br in MHz.
    if (cmd.CenterFreq > 0.0) next.fc = cmd.CenterFreq / 1.0e9;
    if (cmd.SamplingRate > 0.0) next.fs = cmd.SamplingRate / 1.0e6;
    if (cmd.PulseWidth > 0.0) next.Tr = cmd.PulseWidth;
    if (cmd.BandWidth > 0.0) next.Br = cmd.BandWidth / 1.0e6;
    if (cmd.prf > 0.0) next.PRF = cmd.prf;
    if (cmd.SampleDelay > 0.0) {
        next.has_sample_delay_us = true;
        next.sample_delay_us = cmd.SampleDelay * 1.0e6;
    }
    if (cmd.updatePointNum > 0U) next.pulse_len = static_cast<int>(cmd.updatePointNum);
    if (cmd.LookSide == 0U || cmd.LookSide == 0xFFU) {
        next.squint_side = cmd.LookSide == 0xFFU ? 1 : 0;
    }
    if (cmd.SquintAngle != 0.0) next.squint_angle = cmd.SquintAngle;
    if (cmd.Theta_bw > 0.0) next.beamwidth_deg = cmd.Theta_bw * 180.0 / M_PI;
    if (cmd.Rmin > 0.0f) next.R_min = static_cast<double>(cmd.Rmin);
    if (cmd.channelSpace > 0U) {
        // Protocol: millimetres; Config::d_channel: metres.
        next.d_channel = static_cast<double>(cmd.channelSpace) / 1000.0;
    }

    std::string error;
    if (!deriveAndValidateConfig(next, error)) {
        std::cerr << "[CMD][ERR] rejected generation " << next.config_generation
                  << ": " << error << std::endl;
        return false;
    }
    const bool layout_changed =
        next.scan_mode != previous->scan_mode ||
        next.mechanical_scan.acquisition_scan_prt_count !=
            previous->mechanical_scan.acquisition_scan_prt_count ||
        next.pkg_bytes != previous->pkg_bytes ||
        next.pulse_len != previous->pulse_len ||
        next.pulse_num != previous->pulse_num ||
        next.PRF != previous->PRF ||
        next.wavepos_st != previous->wavepos_st ||
        next.wavepos_ed != previous->wavepos_ed ||
        next.wavepos_skip != previous->wavepos_skip ||
        next.new_protocol_file_first_beam != previous->new_protocol_file_first_beam ||
        next.new_protocol_velocity_scale != previous->new_protocol_velocity_scale ||
        next.new_protocol_velocity_source != previous->new_protocol_velocity_source ||
        next.shm_scan_beam_count != previous->shm_scan_beam_count ||
        next.shm_allow_partial_first_scan != previous->shm_allow_partial_first_scan ||
        next.new_protocol_channel_count != previous->new_protocol_channel_count ||
        next.iq_data_type != previous->iq_data_type;

    {
        std::unique_lock<std::mutex> feed_lock(echo_feed_mutex_);
        echo_resetting_ = true;
        echo_feed_cv_.wait(feed_lock, [this]() { return echo_feeds_in_progress_ == 0U; });
    }

    bool ok = true;
    if (cycle_assembler_) {
        if (layout_changed) {
            ok = configureEchoBuffers(next, true, error);
        } else {
            cycle_assembler_->reset("mode_or_config_generation_change");
            std::shared_ptr<PrtStreamParser> parser;
            {
                std::lock_guard<std::mutex> lock(config_mutex_);
                parser = prt_parser_;
            }
            if (parser) parser->reset();
        }
    }
    if (ok) {
        std::lock_guard<std::mutex> lock(config_mutex_);
        last_cmd_ = cmd;
        runtime_config_.reset(new Config(next));
        cfg_ = next;
    }
    {
        std::lock_guard<std::mutex> feed_lock(echo_feed_mutex_);
        echo_resetting_ = false;
        echo_feed_cv_.notify_all();
    }
    if (!ok) {
        std::cerr << "[CMD][ERR] layout reconfiguration failed: " << error << std::endl;
        return false;
    }

    m_workmode.store(cmd.workMode);
    std::cout << "[CMD] applied config_generation=" << next.config_generation
              << " workMode=" << cmd.workMode
              << " layout_changed=" << (layout_changed ? "true" : "false")
              << std::endl;
    return true;
}

bool MainCtrl::RunLocalTest(const std::vector<std::string>& echoFiles,
                            bool loop,
                            int maxCycles)
{
    if (echoFiles.empty()) {
        std::cerr << "[LOCAL-TEST][ERR] echo file list is empty" << std::endl;
        return false;
    }
    if (loop && echoFiles.size() != 1U) {
        std::cerr << "[LOCAL-TEST][ERR] loop mode requires exactly one echo file"
                  << std::endl;
        return false;
    }
    if (maxCycles < 0) {
        std::cerr << "[LOCAL-TEST][ERR] maxCycles must be non-negative" << std::endl;
        return false;
    }

    m_workmode.store(Mode_GMTI);
    track_manager_.reset();

    bool allOk = true;
    std::cout << "[LOCAL-TEST] XML snapshot: " << gmti_config_xml_ << std::endl;
    std::cout << "[LOCAL-TEST] Echo files: " << echoFiles.size() << std::endl;

    if (loop) {
        int forcedId = -1;
        int forcedPeriodIndex = -1;
        std::string echoFile;
        if (!parseLocalEchoSpec(echoFiles.front(), forcedId, forcedPeriodIndex, echoFile)) {
            std::cerr << "[LOCAL-TEST][ERR] Invalid echo spec: "
                      << echoFiles.front() << std::endl;
            return false;
        }

        // Keep the result id stable so continuous replay retains only the
        // latest product instead of creating unbounded files.  The logical
        // loop cycle is recorded separately in period_index and loop_cycle.
        const int outputResultId = forcedId > 0 ? forcedId : 1;
        g_local_test_stop_requested = 0;
        const auto previousSigint = std::signal(SIGINT, handleLocalTestSignal);
        const auto previousSigterm = std::signal(SIGTERM, handleLocalTestSignal);
        std::cout << "[LOCAL-TEST][LOOP] input=" << echoFile
                  << " result_id=" << outputResultId
                  << " max_cycles=" << maxCycles
                  << " (0=infinite; Ctrl-C stops after current cycle)"
                  << std::endl;

        std::size_t cycleIndex = 0U;
        while (g_local_test_stop_requested == 0 &&
               (maxCycles == 0 || cycleIndex < static_cast<std::size_t>(maxCycles))) {
            std::cout << "[LOCAL-TEST][LOOP] begin cycle=" << (cycleIndex + 1U)
                      << std::endl;
            // The algorithm interval starts immediately before the processing
            // flow.  The end-to-end interval starts before local-test dispatch
            // and includes result packet preparation.
            const auto endToEndStart = std::chrono::steady_clock::now();
            const auto endToEndStartWall = std::chrono::system_clock::now();
            const auto algorithmStart = std::chrono::steady_clock::now();
            const bool cycleOk = runGMTIProcessingFlow(
                this, currentConfigSnapshot(), echoFile, outputResultId,
                forcedPeriodIndex);
            const auto algorithmEnd = std::chrono::steady_clock::now();
            const auto endToEndEnd = algorithmEnd;
            const auto endToEndEndWall = std::chrono::system_clock::now();
            const double cycleProcessMs = std::chrono::duration<double, std::milli>(
                algorithmEnd - algorithmStart).count();
            const double endToEndMs = std::chrono::duration<double, std::milli>(
                endToEndEnd - endToEndStart).count();
            const int timingPeriodId = static_cast<int>(cycleIndex);

            // Flush the per-cycle result before doing any summary bookkeeping;
            // a terminal or tee reader can therefore consume the timing as
            // soon as this cycle has completed.
            std::cout << "[FILE][CYCLE] result_file_id=" << outputResultId
                      << " period_index=" << timingPeriodId
                      << " algorithm_process_ms=" << cycleProcessMs
                      << " end_to_end_ms=" << endToEndMs
                      << " success=" << (cycleOk ? "true" : "false")
                      << " loop_cycle=" << (cycleIndex + 1U)
                      << std::endl;
            gmti::runtime::recordTiming(
                "end_to_end_total",
                endToEndStartWall,
                endToEndEndWall,
                static_cast<long long>(std::llround(endToEndMs)),
                timingPeriodId,
                "continuous single-file replay -> result packet ready");
            if (!cycleOk) {
                allOk = false;
                std::cerr << "[LOCAL-TEST][ERR] GMTI processing failed: "
                          << echoFile << std::endl;
            }
            {
                std::lock_guard<std::mutex> lock(result_mutex_);
                pending_result_packets_.clear();
                IsResultReady.store(false);
            }
            ++cycleIndex;
        }

        const bool stoppedBySignal = g_local_test_stop_requested != 0;
        std::signal(SIGINT, previousSigint);
        std::signal(SIGTERM, previousSigterm);
        std::cout << "[LOCAL-TEST][LOOP] stopped completed_cycles=" << cycleIndex
                  << " reason=" << (stoppedBySignal ? "signal" : "max_cycles")
                  << std::endl;
        return allOk;
    }

    for (size_t i = 0; i < echoFiles.size(); ++i) {
        int forcedId = -1;
        int forcedPeriodIndex = -1;
        std::string echoFile;
        if (!parseLocalEchoSpec(echoFiles[i], forcedId, forcedPeriodIndex, echoFile)) {
            allOk = false;
            std::cerr << "[LOCAL-TEST][ERR] Invalid echo spec: " << echoFiles[i] << std::endl;
            continue;
        }

        std::cout << "\n[LOCAL-TEST] ===== "
                  << (i + 1) << "/" << echoFiles.size()
                  << " echo=" << echoFile;
        if (forcedId > 0) {
            std::cout << " result_id=" << forcedId;
        }
        if (forcedPeriodIndex >= 0) {
            std::cout << " period_index=" << forcedPeriodIndex;
        }
        std::cout << " =====" << std::endl;
        // The algorithm interval starts immediately before the processing
        // flow.  The end-to-end interval starts before local-test dispatch so
        // it also covers input-spec handling and result hand-off.
        const auto endToEndStart = std::chrono::steady_clock::now();
        const auto endToEndStartWall = std::chrono::system_clock::now();
        const auto algorithmStart = std::chrono::steady_clock::now();
        const bool cycleOk = runGMTIProcessingFlow(
            this, currentConfigSnapshot(), echoFile, forcedId,
            forcedPeriodIndex);
        const auto algorithmEnd = std::chrono::steady_clock::now();
        const auto endToEndEnd = algorithmEnd;
        const auto endToEndEndWall = std::chrono::system_clock::now();
        const double cycleProcessMs = std::chrono::duration<double, std::milli>(
            algorithmEnd - algorithmStart).count();
        const double endToEndMs = std::chrono::duration<double, std::milli>(
            endToEndEnd - endToEndStart).count();
        const int timingPeriodId = forcedPeriodIndex >= 0
            ? forcedPeriodIndex : static_cast<int>(i);
        gmti::runtime::recordTiming(
            "end_to_end_total",
            endToEndStartWall,
            endToEndEndWall,
            static_cast<long long>(std::llround(endToEndMs)),
            timingPeriodId,
            "file dispatch -> result packet ready");
        std::cout << "[FILE][CYCLE] result_file_id=" << forcedId
                  << " period_index=" << timingPeriodId
                  << " algorithm_process_ms=" << cycleProcessMs
                  << " end_to_end_ms=" << endToEndMs
                  << " success=" << (cycleOk ? "true" : "false")
                  << std::endl;
        if (!cycleOk) {
            allOk = false;
            std::cerr << "[LOCAL-TEST][ERR] GMTI processing failed: "
                      << echoFile << std::endl;
        }
        {
            std::lock_guard<std::mutex> lock(result_mutex_);
            pending_result_packets_.clear();
            IsResultReady.store(false);
        }
    }

    return allOk;
}

void* MainCtrl::OnRecvCmdThread(void *param)
{
    MainCtrl *pHost = static_cast<MainCtrl*>(param);
    std::vector<uint8_t> pending;
    pending.reserve(sizeof(ModeSwitchCmd) * 2U);
    uint8_t read_buffer[4096];
    uint64_t command_index = 0U;

    while (pHost->running_.load()) {
        const int ret = pHost->m_RecvCmdPipe.ReadData(
            reinterpret_cast<char*>(read_buffer), sizeof(read_buffer));
        if (ret < 0) {
            if (!pHost->running_.load()) break;
            if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
                usleep(10000);
                continue;
            }
            std::cerr << "[CMD][ERR] receive mode switch command failed\n";
            usleep(100000);
            continue;
        }
        if (ret == 0) {
            usleep(10000);
            continue;
        }
        pending.insert(pending.end(), read_buffer, read_buffer + ret);

        while (true) {
            if (pending.size() < 4U) break;
            std::size_t head = 0U;
            while (head + 4U <= pending.size() &&
                   loadWireU32LE(pending.data() + head) != kProtocolV15CommandHead) {
                ++head;
            }
            if (head == pending.size()) {
                pending.erase(pending.begin(), pending.end() - 3);
                break;
            }
            if (head > 0U) {
                pending.erase(pending.begin(),
                              pending.begin() + static_cast<std::ptrdiff_t>(head));
            }
            if (pending.size() < 9U) break;

            const uint32_t data_len = loadWireU32LE(pending.data() + 5U);
            const uint64_t frame_bytes_u64 =
                static_cast<uint64_t>(data_len) + kProtocolV15CommandOverhead;
            if (frame_bytes_u64 < sizeof(ModeSwitchCmd) || frame_bytes_u64 > 4096U) {
                std::cerr << "[CMD][ERR] invalid V1.5 command frame length: dataLen="
                          << data_len << std::endl;
                pending.erase(pending.begin(), pending.begin() + 4);
                continue;
            }
            const std::size_t frame_bytes = static_cast<std::size_t>(frame_bytes_u64);
            if (pending.size() < frame_bytes) break;

            ModeSwitchCmd cmd{};
            std::string decode_error;
            const bool decoded = decodeV15ModeSwitchFrame(
                pending.data(), frame_bytes, cmd, decode_error);
            pending.erase(pending.begin(),
                          pending.begin() + static_cast<std::ptrdiff_t>(frame_bytes));
            if (!decoded) {
                std::cerr << "[CMD][ERR] rejected V1.5 command: "
                          << decode_error << std::endl;
                continue;
            }

            printModeSwitchCmd(cmd, ++command_index, frame_bytes);
            if (!pHost->applyModeSwitchCmdToRuntime(cmd)) {
                std::cerr << "[CMD][ERR] mode command rejected; previous runtime snapshot remains active"
                          << std::endl;
                continue;
            }

            const std::shared_ptr<const Config> applied_cfg =
                pHost->currentConfigSnapshot();
            if (applied_cfg && applied_cfg->test_mode == 0 &&
                !pHost->updateXmlFromModeSwitchCmd(cmd, pHost->gmti_config_xml_)) {
                std::cerr << "Failed to update XML config from ModeSwitchCmd: "
                          << pHost->gmti_config_xml_ << std::endl;
            }
        }
    }
    return nullptr;
}

void* MainCtrl::OnEchoRecvThread(void *param)
{
    MainCtrl *pHost = static_cast<MainCtrl*>(param);

    std::cout << "OnEchoRecvThread start" << std::endl;

    char buf[512] = {0};
    while(pHost->running_.load())
    {
        const int ret = pHost->m_RecvEchoPipe.ReadData(buf, 512);
        if(ret < 1) {
            if (!pHost->running_.load()) {
                break;
            }
            if (ret < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
                usleep(10000);
                continue;
            }
            printf("Receive echo data error\n");
            usleep(100000);
            continue;
        }

        std::string fileName(buf, static_cast<size_t>(ret));
        while (!fileName.empty() && (fileName.back() == '\0' || fileName.back() == '\n' || fileName.back() == '\r')) {
            fileName.pop_back();
        }
        if (!fileName.empty()) {
            pushPendingEchoFile(fileName);
        }
    }
    return nullptr;
}

void* MainCtrl::ShmEchoRecvThread(void *param)
{
    MainCtrl* host = static_cast<MainCtrl*>(param);
    std::cout << "ShmEchoRecvThread start" << std::endl;
    auto last_attach_log = std::chrono::steady_clock::time_point();
    auto next_attach_attempt = std::chrono::steady_clock::time_point();
    auto last_audit_warning = std::chrono::steady_clock::time_point();
    bool ever_attached = false;
    bool audit_reset_for_disconnect = false;

    while (host->running_.load()) {
        const auto loop_now = std::chrono::steady_clock::now();
        const std::shared_ptr<const Config> loop_cfg = host->currentConfigSnapshot();
        if (ever_attached && host->shm_receiver_ && !host->shm_receiver_->attached() &&
            next_attach_attempt.time_since_epoch().count() != 0 &&
            loop_now < next_attach_attempt) {
            if (host->cycle_assembler_) host->cycle_assembler_->pollTimeout();
            const long long remaining_us =
                std::chrono::duration_cast<std::chrono::microseconds>(
                    next_attach_attempt - loop_now).count();
            const int idle_us = loop_cfg ? std::max(1, loop_cfg->shm_idle_sleep_us) : 100;
            const long long sleep_us = std::max<long long>(
                idle_us, std::min<long long>(remaining_us, 100000LL));
            usleep(static_cast<useconds_t>(sleep_us));
            continue;
        }

        const uint8_t* block = nullptr;
        std::size_t block_len = 0U;
        std::string error;
        const bool received = host->shm_receiver_ &&
            host->shm_receiver_->poll(block, block_len, error);
        if (host->shm_receiver_ && host->shm_receiver_->attached()) {
            ever_attached = true;
        }
        if (!received) {
            if (host->cycle_assembler_) host->cycle_assembler_->pollTimeout();
            if (!error.empty()) {
                if (loop_cfg && loop_cfg->test_mode == 2 && host->shm_receiver_ &&
                    !host->shm_receiver_->attached() && !audit_reset_for_disconnect &&
                    host->test_protocol_auditor_) {
                    // Do not splice a partial PRT from an unlinked sender
                    // object to a recreated object with the same POSIX name.
                    host->test_protocol_auditor_->reset();
                    audit_reset_for_disconnect = true;
                }
                const auto now = std::chrono::steady_clock::now();
                const int reconnect_ms = loop_cfg
                    ? std::max(1, loop_cfg->shm_reconnect_ms) : 1000;
                if (ever_attached && host->shm_receiver_ &&
                    !host->shm_receiver_->attached()) {
                    next_attach_attempt = now + std::chrono::milliseconds(reconnect_ms);
                }
                if (last_attach_log.time_since_epoch().count() == 0 ||
                    std::chrono::duration_cast<std::chrono::milliseconds>(
                        now - last_attach_log).count() >= reconnect_ms) {
                    std::cerr << "[SHM] waiting/reconnecting "
                              << (loop_cfg ? loop_cfg->shm_name : std::string())
                              << ": " << error << std::endl;
                    last_attach_log = now;
                }
            }
            const int sleep_us = loop_cfg ? std::max(1, loop_cfg->shm_idle_sleep_us) : 100;
            usleep(static_cast<useconds_t>(sleep_us));
            continue;
        }
        next_attach_attempt = std::chrono::steady_clock::time_point();
        audit_reset_for_disconnect = false;

        if (loop_cfg && isLaboratoryTestMode(loop_cfg->test_mode)) {
            const char* test_tag = laboratoryTestTag(loop_cfg->test_mode);
            // test=1 intentionally treats the shmdemo payload as opaque. In
            // test=2, the only additional operation is a bounded, audit-only
            // parser; it never produces a PRT for the real assembler and it
            // cannot suppress the byte-accounting path below.  Both modes
            // treat every received data block as a trigger input regardless
            // of whether the controller sent a valid/expected workMode.
            if (!host->test_cycle_accumulator_) {
                std::cerr << "[SHM][" << test_tag
                          << "][ERR] byte accumulator is not initialized" << std::endl;
                continue;
            }
            if (loop_cfg->test_mode == 2) {
                if (!host->test_protocol_auditor_) {
                    std::cerr << "[SHM][TEST2][ERR] protocol auditor is not initialized"
                              << std::endl;
                } else {
                    const ShmProtocolAuditSnapshot audit_before =
                        host->test_protocol_auditor_->snapshot();
                    try {
                        host->test_protocol_auditor_->inspect(block, block_len);
                    } catch (const std::exception& e) {
                        // An audit implementation failure must be visible,
                        // but it must not turn malformed lab input into a
                        // trigger/accounting deadlock.
                        std::cerr << "[SHM][TEST2][AUDIT][ERR] " << e.what()
                                  << "; continue byte accounting" << std::endl;
                    }
                    const ShmProtocolAuditSnapshot audit_after =
                        host->test_protocol_auditor_->snapshot();
                    const bool new_protocol_problem =
                        audit_after.framing_or_header_invalid_count >
                            audit_before.framing_or_header_invalid_count ||
                        audit_after.counter_discontinuity_count >
                            audit_before.counter_discontinuity_count ||
                        audit_after.nonfinite_navigation_count >
                            audit_before.nonfinite_navigation_count ||
                        audit_after.geodetic_range_error_count >
                            audit_before.geodetic_range_error_count ||
                        audit_after.prt_low_byte_mismatch_count >
                            audit_before.prt_low_byte_mismatch_count;
                    const auto now = std::chrono::steady_clock::now();
                    if (new_protocol_problem &&
                        (last_audit_warning.time_since_epoch().count() == 0 ||
                         std::chrono::duration_cast<std::chrono::milliseconds>(
                             now - last_audit_warning).count() >= 1000)) {
                        std::cout << "[SHM][TEST2][AUDIT][WARN] block_index="
                                  << host->shm_receiver_->blockCount()
                                  << " valid_prt=" << audit_after.valid_prt_count
                                  << " framing_or_header_invalid="
                                  << audit_after.framing_or_header_invalid_count
                                  << " resync=" << audit_after.resync_count
                                  << " counter_discontinuity="
                                  << audit_after.counter_discontinuity_count
                                  << " missing_prt=" << audit_after.missing_prt_count
                                  << " duplicate_prt=" << audit_after.duplicate_prt_count
                                  << " out_of_order_prt=" << audit_after.out_of_order_prt_count
                                  << " nonfinite_navigation="
                                  << audit_after.nonfinite_navigation_count
                                  << " geodetic_range_error="
                                  << audit_after.geodetic_range_error_count
                                  << " prt_low_byte_mismatch="
                                  << audit_after.prt_low_byte_mismatch_count
                                  << "; continue byte accounting" << std::endl;
                        last_audit_warning = now;
                    }
                }
            }
            const TestCycleMetricsSnapshot before = host->test_cycle_accumulator_->metrics();
            try {
                host->test_cycle_accumulator_->account(
                    block_len, host->shm_receiver_->blockCount(),
                    loop_cfg->config_generation);
            } catch (const std::exception& e) {
                std::cerr << "[SHM][" << test_tag << "][ERR] byte accounting failed: "
                          << e.what() << std::endl;
                continue;
            }
            const TestCycleMetricsSnapshot after = host->test_cycle_accumulator_->metrics();
            if (loop_cfg->runtime_diagnostics_enabled) {
                std::cout << "[SHM][" << test_tag << "][BLOCK] block_index="
                          << host->shm_receiver_->blockCount()
                          << " valid_bytes=" << block_len
                          << " accumulated_bytes=" << after.accumulated_bytes
                          << " cycle_bytes=" << after.cycle_bytes
                          << " completed_cycle_count=" << after.completed_cycle_count
                          << std::endl;
            }
            if (after.completed_cycle_count > before.completed_cycle_count) {
                std::cout << "[SHM][" << test_tag << "][CYCLE] completed_cycle_count="
                          << after.completed_cycle_count
                          << " accumulated_bytes=" << after.accumulated_bytes
                          << " cycle_bytes=" << after.cycle_bytes
                          << " source_block_index=" << host->shm_receiver_->blockCount()
                          << std::endl;
            }
            continue;
        }

        if (host->cycle_assembler_) {
            host->cycle_assembler_->setReceiverTotals(
                host->shm_receiver_->blockCount(),
                host->shm_receiver_->byteCount(),
                host->shm_receiver_->overrunCount());
        }

        // Keep consuming the demo ring outside WAGMTI mode so it cannot grow
        // stale. Only valid WAGMTI mode data is submitted to the real parser.
        if (!isWagmtiCommandMode(host->m_workmode.load())) continue;

        std::shared_ptr<PrtStreamParser> parser;
        std::shared_ptr<const Config> snapshot;
        {
            std::unique_lock<std::mutex> feed_lock(host->echo_feed_mutex_);
            if (host->echo_resetting_) continue;
            ++host->echo_feeds_in_progress_;
        }
        {
            std::lock_guard<std::mutex> config_lock(host->config_mutex_);
            parser = host->prt_parser_;
            snapshot = host->runtime_config_;
        }
        if (parser && snapshot && host->cycle_assembler_) {
            parser->feed(block, block_len,
                [host, snapshot](const uint8_t* prt,
                                 std::size_t len,
                                 uint32_t counter,
                                 double utc) {
                    host->cycle_assembler_->acceptPrt(
                        prt, len, counter, utc, snapshot);
                });
        }
        {
            std::lock_guard<std::mutex> feed_lock(host->echo_feed_mutex_);
            --host->echo_feeds_in_progress_;
            host->echo_feed_cv_.notify_all();
        }
    }
    return nullptr;
}

void* MainCtrl::ProcessDataThread(void* param)
{
    MainCtrl *pHost = static_cast<MainCtrl*>(param);

    std::cout << "ProcessDataThread start" << std::endl;

    while(pHost->running_.load()) {
        if (pHost->isShmMode()) {
            const std::shared_ptr<const Config> shm_cfg = pHost->currentConfigSnapshot();
            if (shm_cfg && isLaboratoryTestMode(shm_cfg->test_mode)) {
                const char* test_tag = laboratoryTestTag(shm_cfg->test_mode);
                TestCycleTrigger trigger;
                if (!pHost->test_cycle_accumulator_ ||
                    !pHost->test_cycle_accumulator_->waitReady(trigger, 100)) {
                    continue;
                }
                std::size_t prepared_index = 0U;
                std::string prepared_file;
                std::string selection_error;
                if (!selectTestCycleDataFile(*shm_cfg,
                                             trigger.completed_cycle_id,
                                             prepared_index,
                                             prepared_file,
                                             selection_error)) {
                    std::cout << "[SHM][" << test_tag << "][SKIP] trigger_cycle_id="
                              << trigger.completed_cycle_id << ' '
                              << selection_error << std::endl;
                    pHost->test_cycle_accumulator_->release(trigger, 0.0);
                    continue;
                }
                const auto process_start = std::chrono::steady_clock::now();
                std::cout << "[SHM][" << test_tag << "][PROCESS] trigger_cycle_id="
                          << trigger.completed_cycle_id
                          << " source_block_index=" << trigger.source_block_index
                          << " source_valid_bytes=" << trigger.source_valid_bytes
                          << " remaining_bytes=" << trigger.remaining_bytes
                          << " prepared_file_index=" << prepared_index
                          << " prepared_file=" << prepared_file << std::endl;

                // This calls the established one-file-period path.  The raw
                // shared-memory block is deliberately absent from this call.
                const bool ok = runGMTIProcessingFlow(
                    pHost, shm_cfg, prepared_file, 0, -1, nullptr, nullptr);
                const auto process_end = std::chrono::steady_clock::now();
                const auto process_end_wall = std::chrono::system_clock::now();
                const double process_ms = std::chrono::duration<double, std::milli>(
                    process_end - process_start).count();
                const bool have_cycle_start_wall =
                    trigger.cycle_start_wall.time_since_epoch().count() != 0;
                const double end_to_end_ms = have_cycle_start_wall
                    ? std::chrono::duration<double, std::milli>(
                        process_end_wall - trigger.cycle_start_wall).count()
                    : process_ms;
                const auto end_to_end_start_wall = have_cycle_start_wall
                    ? trigger.cycle_start_wall : process_end_wall;
                const int timing_period_id = trigger.completed_cycle_id <=
                    static_cast<uint64_t>(std::numeric_limits<int>::max())
                    ? static_cast<int>(trigger.completed_cycle_id) : -1;
                gmti::runtime::recordTiming(
                    "end_to_end_total",
                    end_to_end_start_wall,
                    process_end_wall,
                    static_cast<long long>(std::llround(end_to_end_ms)),
                    timing_period_id,
                    std::string("test=") + std::to_string(shm_cfg->test_mode) +
                        " shared-memory byte accounting -> prepared file result; cycle_id=" +
                        std::to_string(trigger.completed_cycle_id));
                pHost->test_cycle_accumulator_->release(trigger, process_ms);
                const TestCycleMetricsSnapshot metrics =
                    pHost->test_cycle_accumulator_->metrics();
                std::cout << "[SHM][" << test_tag << "][METRICS] blocks="
                          << metrics.shm_blocks_received
                          << " bytes=" << metrics.shm_bytes_received
                          << " accumulated_bytes=" << metrics.accumulated_bytes
                          << " cycle_bytes=" << metrics.cycle_bytes
                          << " completed_cycle_count=" << metrics.completed_cycle_count
                          << " dropped_slot_count=" << metrics.dropped_slot_count
                          << " overwrite_count=" << metrics.overwrite_count
                          << " pending_trigger_high_water="
                          << metrics.max_pending_trigger_count
                          << " algorithm_process_ms=" << process_ms
                          << " end_to_end_ms=" << end_to_end_ms
                          << " success=" << (ok ? "true" : "false") << std::endl;
                if (!ok) {
                    std::cerr << "[SHM][" << test_tag
                              << "][ERR] prepared-file processing failed for cycle "
                              << trigger.completed_cycle_id << std::endl;
                }
                if (shm_cfg->test_mode == 2 && pHost->test_protocol_auditor_) {
                    const ShmProtocolAuditSnapshot audit =
                        pHost->test_protocol_auditor_->snapshot();
                    std::cout << "[SHM][TEST2][AUDIT] blocks="
                              << audit.shm_blocks_inspected
                              << " bytes=" << audit.shm_bytes_inspected
                              << " valid_prt=" << audit.valid_prt_count
                              << " framing_or_header_invalid="
                              << audit.framing_or_header_invalid_count
                              << " resync=" << audit.resync_count
                              << " counter_discontinuity="
                              << audit.counter_discontinuity_count
                              << " missing_prt=" << audit.missing_prt_count
                              << " duplicate_prt=" << audit.duplicate_prt_count
                              << " out_of_order_prt=" << audit.out_of_order_prt_count
                              << " nonfinite_navigation="
                              << audit.nonfinite_navigation_count
                              << " geodetic_range_error="
                              << audit.geodetic_range_error_count
                              << " prt_low_byte_mismatch="
                              << audit.prt_low_byte_mismatch_count
                              << " payload_to_algorithm=false" << std::endl;
                }
                continue;
            }
            EchoCycleAssembler::Lease lease;
            if (!pHost->cycle_assembler_ ||
                !pHost->cycle_assembler_->waitReady(lease, 100)) {
                continue;
            }
            const auto process_start = std::chrono::steady_clock::now();
            std::cout << "[SHM][CYCLE] acquire acquisition_cycle_id="
                      << lease.metadata.acquisition_cycle_id
                      << " generation=" << lease.metadata.config_generation
                      << " counters=" << lease.metadata.first_prt_counter << ".."
                      << lease.metadata.last_prt_counter
                      << " utc=" << lease.metadata.first_utc << ".."
                      << lease.metadata.last_utc
                      << " prts=" << lease.metadata.prt_count
                      << " scan_beams=" << lease.metadata.scan_beam_count
                      << " processing_beams=" << lease.metadata.processing_beam_count
                      << " partial_scan=" << (lease.metadata.partial_scan ? "true" : "false")
                      << " integrity_status=" << lease.metadata.integrity_status
                      << " missing_leading_prts=" << lease.metadata.missing_leading_prt_count
                      << " fill_ms=" << lease.metadata.cycle_fill_ms
                      << " wait_ms=" << lease.metadata.cycle_wait_ms
                      << " raw_fnv1a64=0x" << std::hex
                      << lease.metadata.raw_fnv1a64 << std::dec << std::endl;
            // Only test=0 can reach the real PRT assembler.  Keep the
            // established in-memory processing path here; test=1/2 are
            // handled above from byte-count triggers and never acquire a
            // shared-memory payload lease.
            const bool ok = runGMTIProcessingFlow(
                pHost, lease.config, std::string(), -1,
                -1,
                &lease.view, &lease.metadata);
            const auto process_end = std::chrono::steady_clock::now();
            const auto process_end_wall = std::chrono::system_clock::now();
            const double process_ms = std::chrono::duration<double, std::milli>(
                process_end - process_start).count();
            const bool have_cycle_start_wall =
                lease.metadata.cycle_start_wall.time_since_epoch().count() != 0;
            const double end_to_end_ms = have_cycle_start_wall
                ? std::chrono::duration<double, std::milli>(
                    process_end_wall - lease.metadata.cycle_start_wall).count()
                : lease.metadata.cycle_fill_ms + lease.metadata.cycle_wait_ms + process_ms;
            const auto end_to_end_start_wall = have_cycle_start_wall
                ? lease.metadata.cycle_start_wall
                : process_end_wall - std::chrono::duration_cast<std::chrono::system_clock::duration>(
                    std::chrono::duration<double, std::milli>(end_to_end_ms));
            const int timing_period_id = lease.metadata.acquisition_cycle_id <=
                static_cast<uint64_t>(std::numeric_limits<int>::max())
                ? static_cast<int>(lease.metadata.acquisition_cycle_id) : -1;
            gmti::runtime::recordTiming(
                "end_to_end_total",
                end_to_end_start_wall,
                process_end_wall,
                static_cast<long long>(std::llround(end_to_end_ms)),
                timing_period_id,
                std::string("shared-memory acquisition -> result packet ready; cycle_id=") +
                    std::to_string(lease.metadata.acquisition_cycle_id));
            pHost->cycle_assembler_->release(lease, process_ms);
            const ShmEchoMetricsSnapshot metrics = pHost->cycle_assembler_->metrics();
            std::shared_ptr<PrtStreamParser> parser;
            {
                std::lock_guard<std::mutex> lock(pHost->config_mutex_);
                parser = pHost->prt_parser_;
            }
            const uint64_t parser_valid = parser ? parser->validPrtCount() : 0U;
            const uint64_t parser_invalid = parser ? parser->invalidPrtCount() : 0U;
            const uint64_t parser_resync = parser ? parser->resyncCount() : 0U;
            std::cout << "[SHM][METRICS] blocks=" << metrics.shm_blocks_received
                      << " bytes=" << metrics.shm_bytes_received
                      << " valid_prt=" << parser_valid
                      << " invalid_prt=" << parser_invalid
                      << " gaps=" << metrics.prt_gap_count
                      << " duplicates=" << metrics.duplicate_prt_count
                      << " resync=" << (metrics.resync_count + parser_resync)
                      << " completed_cycles=" << metrics.completed_cycle_count
                      << " dropped_incomplete=" << metrics.dropped_incomplete_cycle_count
                      << " dropped_backpressure=" << metrics.dropped_backpressure_cycle_count
                      << " ring_overrun=" << metrics.ring_overrun_count
                      << " cycle_fill_ms=" << lease.metadata.cycle_fill_ms
                      << " cycle_wait_ms=" << lease.metadata.cycle_wait_ms
                      << " algorithm_process_ms=" << process_ms
                      << " end_to_end_ms=" << end_to_end_ms
                      << " max_processing_latency_ms=" << metrics.max_processing_latency_ms
                      << " success=" << (ok ? "true" : "false") << std::endl;
            if (!ok) {
                std::cerr << "[SHM][ERR] processing failed for acquisition cycle "
                          << lease.metadata.acquisition_cycle_id << std::endl;
            }
            continue;
        }

        std::string fileName;
        if (!popPendingEchoFile(fileName))
        {
            usleep(10000);
            continue;
        }

        std::cout << "open file: " << fileName << std::endl;

        const auto endToEndStart = std::chrono::steady_clock::now();
        const auto endToEndStartWall = std::chrono::system_clock::now();
        const auto algorithmStart = std::chrono::steady_clock::now();
        const bool ok = runGMTIProcessingFlow(
            pHost, pHost->currentConfigSnapshot(), fileName);
        const auto algorithmEnd = std::chrono::steady_clock::now();
        const auto endToEndEndWall = std::chrono::system_clock::now();
        const double algorithmProcessMs = std::chrono::duration<double, std::milli>(
            algorithmEnd - algorithmStart).count();
        const double endToEndMs = std::chrono::duration<double, std::milli>(
            algorithmEnd - endToEndStart).count();
        gmti::runtime::recordTiming(
            "end_to_end_total",
            endToEndStartWall,
            endToEndEndWall,
            static_cast<long long>(std::llround(endToEndMs)),
            -1,
            "file input dispatch -> result packet ready");
        std::cout << "[FILE][CYCLE] result_file_id="
                  << extractFileIdFromPath(fileName)
                  << " period_index=-1"
                  << " algorithm_process_ms=" << algorithmProcessMs
                  << " end_to_end_ms=" << endToEndMs
                  << " success=" << (ok ? "true" : "false")
                  << std::endl;
        {
            std::lock_guard<std::mutex> lock(pHost->result_mutex_);
            pHost->IsResultReady.store(!pHost->pending_result_packets_.empty());
        }
        if (!ok) {
            std::cerr << "[ERR] GMTI processing failed for echo file: " << fileName << std::endl;
        }

        if (!isWagmtiCommandMode(pHost->m_workmode.load()))
        {
            clearPendingEchoQueue();
            std::cout << "Standby mode: cleared pending echo queue after current file" << std::endl;
        }
    }
    return nullptr;
}

void* MainCtrl::OnResSendThread(void* param)
{
    MainCtrl *pHost = static_cast<MainCtrl*>(param);

    std::cout << "OnResSendThread start" << std::endl;

    while(pHost->running_.load())
    {
        GMTIResultPacket packet;
        bool hasPacket = false;
        {
            std::lock_guard<std::mutex> lock(pHost->result_mutex_);
            if (pHost->pending_result_packets_.empty()) {
                pHost->IsResultReady.store(false);
            } else {
                packet = pHost->pending_result_packets_.front();
                pHost->IsResultReady.store(true);
                hasPacket = true;
            }
        }

        if (!hasPacket)
        {
            usleep(100000);
            continue;
        }

        std::cout << "GMTI mode: Sending protocol packet..." << std::endl;

        uint32_t packed_len = 0;
        if (!pHost->packGMTIResults(packet, pHost->m_SendBuf, 40U * 1024U * 1024U, packed_len))
        {
            std::cerr << "Failed to pack GMTI protocol packet" << std::endl;
            usleep(100000);
            continue;
        }

        const auto send_start = std::chrono::steady_clock::now();
        const ssize_t written = pHost->m_SendResultPipe.WriteData(
            pHost->m_SendBuf, static_cast<int>(packed_len));
        const double result_send_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - send_start).count();
        std::cout << "[RESULT][METRICS] acquisition_cycle_id="
                  << packet.acquisition_cycle_id
                  << " config_generation=" << packet.config_generation
                  << " result_file_id=" << packet.result_file_id
                  << " result_bytes=" << packed_len
                  << " result_send_ms=" << result_send_ms
                  << " success="
                  << (written == static_cast<ssize_t>(packed_len) ? "true" : "false")
                  << std::endl;
        if (!pHost->running_.load()) {
            break;
        }
        if (written == static_cast<ssize_t>(packed_len))
        {
            std::cout << "GMTI protocol packet sent: " << packed_len << " bytes" << std::endl;
            std::lock_guard<std::mutex> lock(pHost->result_mutex_);
            if (!pHost->pending_result_packets_.empty()) {
                pHost->pending_result_packets_.pop_front();
            }
            pHost->latest_gmti_targets_.clear();
            pHost->latest_gmti_targets_.shrink_to_fit();
            pHost->IsResultReady.store(!pHost->pending_result_packets_.empty());
        }
        else
        {
            std::cerr << "Failed to send GMTI protocol packet" << std::endl;
            usleep(100000);
        }
        std::cout << "end send res" << std::endl;
    }
    return nullptr;
}

bool MainCtrl::updateXmlFromModeSwitchCmd(const ModeSwitchCmd& cmd, const std::string& xmlPath)
{
    const std::shared_ptr<const Config> snapshot = currentConfigSnapshot();
    if (snapshot && isLaboratoryTestMode(snapshot->test_mode)) {
        std::cout << "[CMD] main_control_parameters_ignored: test="
                  << snapshot->test_mode << std::endl;
        return true;
    }
    TiXmlDocument doc(xmlPath.c_str());
    if (!doc.LoadFile())
    {
        std::cerr << "Failed to load XML file: " << xmlPath << std::endl;
        return false;
    }

    TiXmlElement* root = doc.FirstChildElement("GMTI");
    if (!root)
    {
        std::cerr << "Invalid XML structure: root <GMTI> not found" << std::endl;
        return false;
    }

    TiXmlElement* param = root->FirstChildElement("GMTI_parameter");
    if (!param)
    {
        std::cerr << "Invalid XML structure: <GMTI_parameter> not found" << std::endl;
        return false;
    }

    TiXmlElement* test = param->FirstChildElement("test");
    if (!test || !test->GetText() || std::string(test->GetText()) != "0") {
        std::cerr << "Refuse to persist command parameters unless XML <test> is exactly 0: "
                  << xmlPath << std::endl;
        return false;
    }

    auto setText = [&](const char* name, double value) {
        TiXmlElement* elem = param->FirstChildElement(name);
        if (!elem) {
            elem = new TiXmlElement(name);
            param->LinkEndChild(elem);
        }
        std::ostringstream oss;
        oss << std::setprecision(15) << value;
        elem->Clear();
        elem->LinkEndChild(new TiXmlText(oss.str().c_str()));
    };

    auto setTextInt = [&](const char* name, int value) {
        TiXmlElement* elem = param->FirstChildElement(name);
        if (!elem) {
            elem = new TiXmlElement(name);
            param->LinkEndChild(elem);
        }
        std::ostringstream oss;
        oss << value;
        elem->Clear();
        elem->LinkEndChild(new TiXmlText(oss.str().c_str()));
    };

    if (cmd.CenterFreq > 0)
    {
        setText("fc", cmd.CenterFreq / 1e9);
    }

    if (cmd.SamplingRate > 0)
    {
        setText("fs", cmd.SamplingRate / 1e6);
    }

    if (cmd.prf > 0)
    {
        setText("PRF", cmd.prf);
    }

    if (cmd.SampleDelay > 0)
    {
        setText("sample_delay_us", cmd.SampleDelay * 1e6);
    }

    if (cmd.updatePointNum > 0U)
    {
        setTextInt("pulse_len", static_cast<int>(cmd.updatePointNum));
    }

    if (cmd.BandWidth > 0)
    {
        setText("Br", cmd.BandWidth / 1e6);
    }

    if (cmd.PulseWidth > 0)
    {
        setText("Tr", cmd.PulseWidth * 1e6);
    }

    if (cmd.alt_scene > 0)
    {
        setText("alt_scene", cmd.alt_scene);
    }

    if (cmd.Rmin > 0)
    {
        setText("Rmin", cmd.Rmin);
    }

    if (cmd.velocity > 0)
    {
        setText("v_platform", cmd.velocity);
    }

    if (cmd.LookSide == 0 || cmd.LookSide == 0xFFU)
    {
        setTextInt("squint_side", cmd.LookSide == 0xFFU ? 1 : 0);
    }

    if (cmd.Theta_bw > 0)
    {
        setText("boshu", cmd.Theta_bw * 180.0 / M_PI);
    }

    if (cmd.channelSpace > 0U)
    {
        setText("d_chan", static_cast<double>(cmd.channelSpace) / 1000.0);
    }

    if (cmd.SquintAngle != 0)
    {
        setText("squint_angle", cmd.SquintAngle);
    }

    if (cmd.targetLon != 0)
    {
        setText("targetLon", cmd.targetLon);
    }

    if (cmd.targetLat != 0)
    {
        setText("targetLat", cmd.targetLat);
    }

    const std::string temporary_xml = xmlPath + ".tmp." +
        std::to_string(static_cast<long long>(::getpid())) + "." +
        std::to_string(std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
    if (!doc.SaveFile(temporary_xml.c_str()))
    {
        std::cerr << "Failed to save temporary XML file: " << temporary_xml << std::endl;
        return false;
    }
    if (::rename(temporary_xml.c_str(), xmlPath.c_str()) != 0) {
        const int saved_errno = errno;
        ::unlink(temporary_xml.c_str());
        std::cerr << "Failed to atomically replace XML file: " << xmlPath
                  << " (" << std::strerror(saved_errno) << ')' << std::endl;
        return false;
    }

    std::cout << "[CMD] parameters persisted atomically to " << xmlPath << std::endl;
    return true;
}

bool MainCtrl::validateGMTIParams()
{
    if (cfg_.pulse_num <= 0 || cfg_.rg_len <= 0)
    {
        std::cerr << "Invalid pulse_num or rg_len" << std::endl;
        return false;
    }

    if (cfg_.fc <= 0 || cfg_.fs <= 0 || cfg_.PRF <= 0)
    {
        std::cerr << "Invalid frequency parameters" << std::endl;
        return false;
    }

    if (cfg_.GMTI_Data_new.empty())
    {
        std::cerr << "GMTI data path not specified" << std::endl;
        return false;
    }

    return true;
}

bool MainCtrl::packGMTIResults(const GMTIResultPacket& packet, char* buffer, size_t buffer_size, uint32_t& packed_len)
{
    constexpr size_t kTargetPacketSize = 35U;

    if (!buffer || buffer_size < sizeof(ResultHeader))
    {
        std::cerr << "Invalid buffer for packing results" << std::endl;
        return false;
    }

    const size_t target_count = std::min<size_t>(packet.targets.size(), 65535U);
    const size_t image_bytes = packet.image_available ? packet.image.size() : 0U;
    const size_t total_bytes = sizeof(ResultHeader) + target_count * kTargetPacketSize + image_bytes;
    if (total_bytes > buffer_size) {
        std::cerr << "Buffer overflow: GMTI packet too large" << std::endl;
        return false;
    }
    if (total_bytes > 0xFFFFFFFFULL) {
        std::cerr << "GMTI packet length exceeds protocol U32 range" << std::endl;
        return false;
    }

    std::vector<uint8_t> pkt(total_bytes, 0U);
    ResultHeader header{};
    header.head = 0xAA55;
    header.msgLen = static_cast<uint32_t>(total_bytes - sizeof(uint16_t) - sizeof(uint8_t));
    header.msgAddr = 0;
    header.msgType = 0;
    header.msgCount = next_result_msg_count_++;
    header.srcId = 0;
    header.dstId = 0;
    header.cmdType = static_cast<uint8_t>(Mode_WAGMTI);
    header.cmdCount = static_cast<uint8_t>(header.msgCount & 0xFFU);
    header.height = packet.image_rows;
    header.width = packet.image_cols;
    header.availFlag = packet.image_available ? 0xFFFF : 0x0000;
    header.roll = 0;
    header.heading = 0;
    header.pitch = 0;
    header.navLon = 0;
    header.navLat = 0;
    header.navHeight = 0;
    header.velNorth = 0;
    header.velUp = 0;
    header.velEast = 0;
    header.hour = 0;
    header.minute = 0;
    header.second = 0;
    header.millisec = 0;
    std::memset(header.posReserve, 0, sizeof(header.posReserve));
    header.hLeftTop = 0;
    header.hLeftDown = 0;
    header.hRightDown = 0;
    header.hRightTop = 0;
    header.hCenter = 0;
    header.lonLeftTop = quant_deg_to_i32(packet.corner_lon[0]);
    header.lonLeftDown = quant_deg_to_i32(packet.corner_lon[1]);
    header.lonRightDown = quant_deg_to_i32(packet.corner_lon[2]);
    header.lonRightTop = quant_deg_to_i32(packet.corner_lon[3]);
    header.lonCenter = quant_deg_to_i32(packet.corner_lon[4]);
    header.latLeftTop = quant_deg_to_i32(packet.corner_lat[0]);
    header.latLeftDown = quant_deg_to_i32(packet.corner_lat[1]);
    header.latRightDown = quant_deg_to_i32(packet.corner_lat[2]);
    header.latRightTop = quant_deg_to_i32(packet.corner_lat[3]);
    header.latCenter = quant_deg_to_i32(packet.corner_lat[4]);
    header.rLeftTop = 0;
    header.rLeftDown = 0;
    header.rRightDown = 0;
    header.rRightTop = 0;
    header.rCenter = 0;
    header.reserve1 = 0;
    header.pixelPitch = quant_pixel_pitch(packet.dbs_out_res_m);
    header.LookDownAngle = 0;
    header.SquintAngle = static_cast<uint16_t>(
        std::lround(std::fabs(packet.squint_angle) * 100.0));
    header.LookSide = (packet.squint_side == 0) ? 0x00 : 0xFF;
    std::memset(header.reserve2, 0, sizeof(header.reserve2));
    header.checksum = 0;
    header.targetNum = static_cast<uint16_t>(target_count);

    std::memcpy(pkt.data(), &header, sizeof(header));

    size_t off = sizeof(ResultHeader);
    for (size_t i = 0; i < target_count; ++i) {
        write_target_packet(pkt, off, packet.targets[i]);
        off += kTargetPacketSize;
    }
    if (image_bytes > 0U) {
        std::memcpy(pkt.data() + off, packet.image.data(), image_bytes);
        off += image_bytes;
    }

    uint8_t checksum = 0;
    for (size_t i = 2; i < 169; ++i) {
        checksum = static_cast<uint8_t>(checksum + pkt[i]);
    }
    pkt[169] = checksum;

    std::memcpy(buffer, pkt.data(), total_bytes);
    packed_len = static_cast<uint32_t>(total_bytes);
    return true;
}
