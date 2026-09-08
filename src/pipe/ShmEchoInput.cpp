#include "pipe/ShmEchoInput.h"

#include "dbs/NewProtocolLayout.hpp"
#include "RingBuffer.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <exception>
#include <fcntl.h>
#include <limits>
#include <new>
#include <sstream>
#include <sys/stat.h>
#include <unistd.h>

namespace {

const std::size_t kNotFound = static_cast<std::size_t>(-1);
constexpr int kShmIdentityProbeMs = 1000;

// RingBufferManager deliberately exposes only the shmdemo transport ABI.  Do
// not extend or alter that demo ABI here.  This separate POSIX probe lets the
// GMTI receiver detect the important lifecycle case where a sender exits
// (therefore unlinks its object) and a later sender recreates the same name.
// An existing mmap keeps pointing at the deleted old object otherwise.
bool queryPosixShmIdentity(const std::string& name,
                           dev_t& device,
                           ino_t& inode,
                           std::string& error)
{
    const int fd = shm_open(name.c_str(), O_RDWR, 0666);
    if (fd < 0) {
        error = std::string("shm_open identity probe failed: ") + std::strerror(errno);
        return false;
    }
    struct stat st;
    const int stat_rc = fstat(fd, &st);
    const int saved_errno = errno;
    close(fd);
    if (stat_rc != 0) {
        error = std::string("fstat identity probe failed: ") + std::strerror(saved_errno);
        return false;
    }
    device = st.st_dev;
    inode = st.st_ino;
    return true;
}

} // namespace

bool deriveEchoScanLayout(const Config& cfg,
                          EchoCycleLayout& layout,
                          std::string& error)
{
    layout = EchoCycleLayout();
    if (cfg.scan_mode == ScanMode::Mechanical) {
        if (cfg.pkg_bytes <= 0 || cfg.PRF <= 0.0 ||
            cfg.mechanical_scan.acquisition_scan_prt_count <= 0) {
            error = "mechanical SHM acquisition requires pkg_bytes, PRF, and "
                    "mechanical_scan.acquisition_scan_prt_count";
            return false;
        }
        layout.prt_bytes = static_cast<std::size_t>(cfg.pkg_bytes);
        layout.prts_per_beam = 0U;
        layout.scan_first_beam = 0;
        layout.scan_beam_count = 0U;
        layout.processing_beam_count = 0U;
        layout.prts_per_cycle = static_cast<std::size_t>(
            cfg.mechanical_scan.acquisition_scan_prt_count);
        if (layout.prts_per_cycle > std::numeric_limits<std::size_t>::max() /
                                    layout.prt_bytes) {
            error = "mechanical scan byte count overflow";
            return false;
        }
        layout.cycle_bytes = layout.prts_per_cycle * layout.prt_bytes;
        layout.expected_prt_mode = static_cast<uint8_t>(cfg.shm_expected_prt_mode);
        layout.counter_phase = static_cast<uint32_t>(
            std::max(0, cfg.shm_prt_counter_phase));
        layout.cycle_timeout_ms = cfg.shm_cycle_timeout_ms > 0
            ? cfg.shm_cycle_timeout_ms
            : std::max(1000, static_cast<int>(std::ceil(
                  3000.0 * static_cast<double>(layout.prts_per_cycle) / cfg.PRF)));
        layout.allow_partial_first_scan = false;
        return true;
    }
    if (cfg.pkg_bytes <= 0 || cfg.pulse_num <= 0 || cfg.PRF <= 0.0 ||
        cfg.wavepos_skip <= 0 || cfg.wavepos_ed < cfg.wavepos_st ||
        cfg.new_protocol_file_first_beam < 1 || cfg.shm_scan_beam_count < 0) {
        error = "invalid echo scan size/range configuration";
        return false;
    }

    const long long first = cfg.new_protocol_file_first_beam;
    const long long scan_beams = cfg.shm_scan_beam_count > 0
        ? static_cast<long long>(cfg.shm_scan_beam_count)
        : static_cast<long long>(cfg.wavepos_ed) - first + 1LL;
    if (scan_beams <= 0LL ||
        first + scan_beams - 1LL > static_cast<long long>(std::numeric_limits<int>::max())) {
        error = "invalid acquisition scan beam span";
        return false;
    }
    const long long last = first + scan_beams - 1LL;
    if (static_cast<long long>(cfg.wavepos_st) < first ||
        static_cast<long long>(cfg.wavepos_ed) > last) {
        std::ostringstream oss;
        oss << "processing beam range " << cfg.wavepos_st << ".." << cfg.wavepos_ed
            << " is outside acquisition scan " << first << ".." << last;
        error = oss.str();
        return false;
    }

    layout.prt_bytes = static_cast<std::size_t>(cfg.pkg_bytes);
    layout.prts_per_beam = static_cast<std::size_t>(cfg.pulse_num);
    layout.scan_first_beam = static_cast<int>(first);
    layout.scan_beam_count = static_cast<std::size_t>(scan_beams);
    layout.processing_beam_count = static_cast<std::size_t>(
        (cfg.wavepos_ed - cfg.wavepos_st) / cfg.wavepos_skip + 1);
    if (layout.scan_beam_count > std::numeric_limits<std::size_t>::max() /
                                 layout.prts_per_beam) {
        error = "PRTs per scan overflow";
        return false;
    }
    layout.prts_per_cycle = layout.scan_beam_count * layout.prts_per_beam;
    if (layout.prts_per_cycle > std::numeric_limits<std::size_t>::max() /
                                layout.prt_bytes) {
        error = "scan byte count overflow";
        return false;
    }
    layout.cycle_bytes = layout.prts_per_cycle * layout.prt_bytes;
    layout.expected_prt_mode = static_cast<uint8_t>(cfg.shm_expected_prt_mode);
    layout.counter_phase = static_cast<uint32_t>(std::max(0, cfg.shm_prt_counter_phase));
    layout.cycle_timeout_ms = cfg.shm_cycle_timeout_ms > 0
        ? cfg.shm_cycle_timeout_ms
        : std::max(1000, static_cast<int>(std::ceil(
              3000.0 * static_cast<double>(layout.prts_per_cycle) / cfg.PRF)));
    layout.allow_partial_first_scan = cfg.shm_allow_partial_first_scan;
    return true;
}

bool selectTestCycleDataFile(const Config& cfg,
                             uint64_t completed_cycle_id,
                             std::size_t& selected_index,
                             std::string& selected_path,
                             std::string& error)
{
    selected_index = 0U;
    selected_path.clear();
    error.clear();
    if (completed_cycle_id == 0U) {
        error = "test-cycle trigger id must be one-based";
        return false;
    }
    if (cfg.test_cycle_data_paths.empty()) {
        error = "no prepared-cycle files are configured";
        return false;
    }
    const uint64_t trigger_index = completed_cycle_id - 1U;
    if (cfg.test_cycle_data_playback == "once" &&
        trigger_index >= cfg.test_cycle_data_paths.size()) {
        std::ostringstream oss;
        oss << "prepared-file list exhausted (mode=once, files="
            << cfg.test_cycle_data_paths.size() << ')';
        error = oss.str();
        return false;
    }
    if (cfg.test_cycle_data_playback != "once" &&
        cfg.test_cycle_data_playback != "loop") {
        error = "invalid test_cycle_data_playback: " + cfg.test_cycle_data_playback;
        return false;
    }
    selected_index = static_cast<std::size_t>(
        trigger_index % static_cast<uint64_t>(cfg.test_cycle_data_paths.size()));
    selected_path = cfg.test_cycle_data_paths[selected_index];
    if (selected_path.empty()) {
        error = "selected prepared-cycle file path is empty";
        return false;
    }
    return true;
}

TestCycleByteAccumulator::TestCycleByteAccumulator(
    uint64_t cycle_bytes,
    std::size_t max_pending_triggers)
    : cycle_bytes_(cycle_bytes),
      max_pending_triggers_(max_pending_triggers)
{
    if (cycle_bytes_ == 0U) {
        throw std::invalid_argument("test cycle byte count must be non-zero");
    }
    if (max_pending_triggers_ == 0U) {
        throw std::invalid_argument("test trigger queue depth must be non-zero");
    }
}

void TestCycleByteAccumulator::account(std::size_t valid_bytes,
                                       uint64_t source_block_index,
                                       uint64_t config_generation)
{
    if (valid_bytes == 0U) return;

    std::unique_lock<std::mutex> lock(mutex_);
    if (stopping_) return;
    ++shm_blocks_received_;
    shm_bytes_received_ += static_cast<uint64_t>(valid_bytes);
    if (accumulated_bytes_ == 0U) {
        current_cycle_start_wall_ = std::chrono::system_clock::now();
    }
    if (static_cast<uint64_t>(valid_bytes) >
        std::numeric_limits<uint64_t>::max() - accumulated_bytes_) {
        throw std::overflow_error("test=1/2 accumulated shared-memory byte counter overflow");
    }
    accumulated_bytes_ += static_cast<uint64_t>(valid_bytes);

    while (accumulated_bytes_ >= cycle_bytes_) {
        // Bound the event queue instead of retaining payload or allowing
        // unbounded trigger growth.  Waiting here intentionally propagates
        // backpressure through the shmdemo ring while the processing thread
        // handles an earlier prepared-file cycle.
        space_cv_.wait(lock, [this]() {
            return stopping_ || pending_.size() < max_pending_triggers_;
        });
        if (stopping_) return;

        TestCycleTrigger trigger;
        trigger.completed_cycle_id = ++completed_cycle_count_;
        trigger.config_generation = config_generation;
        trigger.source_block_index = source_block_index;
        trigger.source_valid_bytes = valid_bytes;
        trigger.cycle_bytes = cycle_bytes_;
        trigger.cycle_start_wall = current_cycle_start_wall_;
        accumulated_bytes_ -= cycle_bytes_;
        trigger.remaining_bytes = accumulated_bytes_;
        pending_.push_back(trigger);
        max_pending_trigger_count_ = std::max<uint64_t>(
            max_pending_trigger_count_, static_cast<uint64_t>(pending_.size()));
        current_cycle_start_wall_ = std::chrono::system_clock::now();
        ready_cv_.notify_one();
    }
}

bool TestCycleByteAccumulator::waitReady(TestCycleTrigger& trigger, int timeout_ms)
{
    std::unique_lock<std::mutex> lock(mutex_);
    if (!ready_cv_.wait_for(lock, std::chrono::milliseconds(timeout_ms), [this]() {
            return stopping_ || !pending_.empty();
        })) {
        return false;
    }
    if (stopping_ || pending_.empty()) return false;
    trigger = pending_.front();
    pending_.pop_front();
    return true;
}

void TestCycleByteAccumulator::release(const TestCycleTrigger&, double)
{
    std::lock_guard<std::mutex> lock(mutex_);
    space_cv_.notify_one();
}

void TestCycleByteAccumulator::shutdown()
{
    std::lock_guard<std::mutex> lock(mutex_);
    stopping_ = true;
    ready_cv_.notify_all();
    space_cv_.notify_all();
}

TestCycleMetricsSnapshot TestCycleByteAccumulator::metrics() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    TestCycleMetricsSnapshot m;
    m.shm_blocks_received = shm_blocks_received_;
    m.shm_bytes_received = shm_bytes_received_;
    m.completed_cycle_count = completed_cycle_count_;
    m.accumulated_bytes = accumulated_bytes_;
    m.cycle_bytes = cycle_bytes_;
    // shmdemo has no slot sequence or overwrite flag.  Its semaphore-based
    // contract backpressures a single producer instead, so these remain zero
    // unless the transport ABI itself is extended by its owner.
    m.dropped_slot_count = 0U;
    m.overwrite_count = 0U;
    m.max_pending_trigger_count = max_pending_trigger_count_;
    return m;
}

PrtStreamParser::PrtStreamParser(std::size_t expected_prt_bytes,
                                 std::size_t max_input_chunk,
                                 uint8_t expected_mode)
    : expected_prt_bytes_(expected_prt_bytes), expected_mode_(expected_mode)
{
    if (expected_prt_bytes_ < gmti::new_protocol::kHeaderBytes) {
        throw std::invalid_argument("expected PRT length is smaller than 256-byte header");
    }
    if (max_input_chunk == 0U) {
        throw std::invalid_argument("shared-memory read chunk must be non-zero");
    }
    staging_.resize(expected_prt_bytes_ + max_input_chunk + 8U);
}

bool PrtStreamParser::hasHead(std::size_t off) const
{
    if (off + 8U > used_) return false;
    for (std::size_t i = 0; i < 8U; ++i) {
        if (staging_[off + i] != 0x5AU) return false;
    }
    return true;
}

bool PrtStreamParser::hasTail(std::size_t off) const
{
    const std::size_t tail = off + gmti::new_protocol::kOffMagicTail;
    if (tail + 8U > used_) return false;
    for (std::size_t i = 0; i < 8U; ++i) {
        if (staging_[tail + i] != 0x5BU) return false;
    }
    return true;
}

std::size_t PrtStreamParser::findHead(std::size_t from) const
{
    if (used_ < 8U || from > used_ - 8U) return kNotFound;
    for (std::size_t i = from; i + 8U <= used_; ++i) {
        if (hasHead(i)) return i;
    }
    return kNotFound;
}

void PrtStreamParser::parseAvailable(const PrtCallback& callback)
{
    std::size_t cursor = 0U;
    while (cursor < used_) {
        const std::size_t head = findHead(cursor);
        if (head == kNotFound) {
            // Preserve only a possible split magic prefix.
            const std::size_t keep = std::min<std::size_t>(7U, used_ - cursor);
            if (keep > 0U) {
                std::memmove(staging_.data(), staging_.data() + used_ - keep, keep);
            }
            if (used_ - cursor > keep) {
                ++invalid_prt_count_;
                ++resync_count_;
            }
            used_ = keep;
            return;
        }
        if (head > cursor) {
            ++invalid_prt_count_;
            ++resync_count_;
        }
        if (used_ - head < gmti::new_protocol::kHeaderBytes) {
            std::memmove(staging_.data(), staging_.data() + head, used_ - head);
            used_ -= head;
            return;
        }

        const uint32_t wire_len = gmti::new_protocol::loadU32LE(
            staging_.data() + head + gmti::new_protocol::kOffPrtLen);
        const bool fixed_header_ok = hasTail(head);
        const bool length_ok = static_cast<std::size_t>(wire_len) == expected_prt_bytes_;
        const bool mode_ok = staging_[head + gmti::new_protocol::kOffVersion] == expected_mode_;
        if (!fixed_header_ok || !length_ok || !mode_ok) {
            ++invalid_prt_count_;
            ++resync_count_;
            cursor = head + 1U;
            continue;
        }
        if (used_ - head < expected_prt_bytes_) {
            std::memmove(staging_.data(), staging_.data() + head, used_ - head);
            used_ -= head;
            return;
        }

        const uint8_t* packet = staging_.data() + head;
        const uint32_t counter = gmti::new_protocol::loadU32LE(
            packet + gmti::new_protocol::kOffPrtCounter);
        const double utc = static_cast<double>(gmti::new_protocol::loadF32LE(
            packet + gmti::new_protocol::kOffUtc));
        callback(packet, expected_prt_bytes_, counter, utc);
        ++valid_prt_count_;
        cursor = head + expected_prt_bytes_;
    }
    used_ = 0U;
}

void PrtStreamParser::feed(const uint8_t* data,
                           std::size_t len,
                           const PrtCallback& callback)
{
    if (!data || len == 0U) return;
    std::size_t consumed = 0U;
    while (consumed < len) {
        if (used_ == staging_.size()) {
            parseAvailable(callback);
            if (used_ == staging_.size()) {
                used_ = 0U;
                ++invalid_prt_count_;
                ++resync_count_;
            }
        }
        const std::size_t take = std::min(len - consumed, staging_.size() - used_);
        std::memcpy(staging_.data() + used_, data + consumed, take);
        used_ += take;
        consumed += take;
        parseAvailable(callback);
    }
}

void PrtStreamParser::reset()
{
    used_ = 0U;
    ++resync_count_;
}

ShmProtocolAudit::ShmProtocolAudit(const EchoCycleLayout& layout,
                                   std::size_t max_input_chunk)
    : parser_(layout.prt_bytes, max_input_chunk, layout.expected_prt_mode)
{
    if (layout.prt_bytes < gmti::new_protocol::kHeaderBytes) {
        throw std::invalid_argument("test=2 audit PRT length is smaller than 256-byte header");
    }
}

void ShmProtocolAudit::inspectValidPrt(const uint8_t* prt,
                                       std::size_t len,
                                       uint32_t counter,
                                       double utc)
{
    // Called synchronously by parser_ while inspect() holds mutex_.  This is
    // intentionally a header-only audit: no I/Q payload is decoded or copied.
    if (!prt || len < gmti::new_protocol::kHeaderBytes) return;

    ++metrics_.valid_prt_count;
    if (prt[gmti::new_protocol::kOffPrtLowByte] !=
        static_cast<uint8_t>(counter & 0xffU)) {
        ++metrics_.prt_low_byte_mismatch_count;
    }

    const gmti::new_protocol::HeaderSample header =
        gmti::new_protocol::readHeaderSample(prt);
    // Header velocity fields are optional.  Runtime position_delta velocity
    // reconstruction must remain usable when vn/ve/vd are not populated.
    const bool navigation_finite = std::isfinite(utc) &&
        std::isfinite(header.lat_deg) && std::isfinite(header.lon_deg) &&
        std::isfinite(header.height_m) &&
        std::isfinite(header.theta_cmd_deg);
    if (!navigation_finite) {
        ++metrics_.nonfinite_navigation_count;
    } else if (header.lat_deg < -90.0 || header.lat_deg > 90.0 ||
               header.lon_deg < -180.0 || header.lon_deg > 180.0) {
        ++metrics_.geodetic_range_error_count;
    }

    if (metrics_.have_last_counter) {
        const uint32_t expected = metrics_.last_counter + 1U;
        if (counter != expected) {
            ++metrics_.counter_discontinuity_count;
            if (counter == metrics_.last_counter) {
                ++metrics_.duplicate_prt_count;
            } else {
                const uint32_t forward_gap = counter - expected;
                if (forward_gap < 0x80000000U) {
                    metrics_.missing_prt_count += static_cast<uint64_t>(forward_gap);
                    metrics_.last_counter = counter;
                } else {
                    ++metrics_.out_of_order_prt_count;
                }
            }
        } else {
            metrics_.last_counter = counter;
        }
    } else {
        metrics_.have_last_counter = true;
        metrics_.last_counter = counter;
    }
}

void ShmProtocolAudit::inspect(const uint8_t* data, std::size_t len)
{
    if (!data || len == 0U) return;
    std::lock_guard<std::mutex> lock(mutex_);
    ++metrics_.shm_blocks_inspected;
    metrics_.shm_bytes_inspected += static_cast<uint64_t>(len);
    parser_.feed(data, len,
        [this](const uint8_t* prt, std::size_t prt_len,
               uint32_t counter, double utc) {
            inspectValidPrt(prt, prt_len, counter, utc);
        });
    metrics_.framing_or_header_invalid_count = parser_.invalidPrtCount();
    metrics_.resync_count = parser_.resyncCount();
}

void ShmProtocolAudit::reset()
{
    std::lock_guard<std::mutex> lock(mutex_);
    // Keep cumulative audit evidence across a sender reconnect, but discard a
    // partial old-object PRT so it cannot be combined with a new object.
    parser_.reset();
    metrics_.resync_count = parser_.resyncCount();
    metrics_.have_last_counter = false;
    metrics_.last_counter = 0U;
}

ShmProtocolAuditSnapshot ShmProtocolAudit::snapshot() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return metrics_;
}

EchoCycleAssembler::EchoCycleAssembler(const EchoCycleLayout& layout)
    : layout_(layout)
{
    if (layout_.prt_bytes == 0U || layout_.prts_per_cycle == 0U ||
        layout_.cycle_bytes != layout_.prt_bytes * layout_.prts_per_cycle) {
        throw std::invalid_argument("invalid echo cycle layout");
    }
    for (std::size_t i = 0; i < buffers_.size(); ++i) {
        buffers_[i].bytes.resize(layout_.cycle_bytes);
    }
}

bool EchoCycleAssembler::isCycleStart(uint32_t counter) const
{
    const uint32_t n = static_cast<uint32_t>(layout_.prts_per_cycle);
    return n != 0U && (counter % n) == (layout_.counter_phase % n);
}

int EchoCycleAssembler::findFreeLocked() const
{
    for (std::size_t i = 0; i < buffers_.size(); ++i) {
        if (buffers_[i].state == State::FREE) return static_cast<int>(i);
    }
    return -1;
}

void EchoCycleAssembler::invalidateFillingLocked(const std::string& reason)
{
    if (filling_index_ >= 0) {
        Buffer& buffer = buffers_[static_cast<std::size_t>(filling_index_)];
        buffer.metadata.valid = false;
        buffer.metadata.invalid_reason = reason;
        buffer.state = State::FREE;
        buffer.config.reset();
        ++dropped_incomplete_cycle_count_;
    }
    filling_index_ = -1;
    filling_prt_count_ = 0U;
    filling_target_prt_count_ = 0U;
    ++epoch_;
}

bool EchoCycleAssembler::beginCycleLocked(
    uint32_t counter,
    double utc,
    const std::shared_ptr<const Config>& config)
{
    const int free_index = findFreeLocked();
    if (free_index < 0) {
        ++dropped_backpressure_cycle_count_;
        return false;
    }
    Buffer& buffer = buffers_[static_cast<std::size_t>(free_index)];
    buffer.state = State::FILLING;
    buffer.metadata = EchoCycleMetadata();
    buffer.metadata.first_prt_counter = counter;
    buffer.metadata.last_prt_counter = counter;
    buffer.metadata.first_utc = utc;
    buffer.metadata.last_utc = utc;
    const uint32_t cycle_size = static_cast<uint32_t>(layout_.prts_per_cycle);
    const uint32_t phase = cycle_size ? layout_.counter_phase % cycle_size : 0U;
    const std::size_t offset = cycle_size
        ? static_cast<std::size_t>(
              (static_cast<uint64_t>(counter) + cycle_size - phase) % cycle_size)
        : 0U;
    const bool partial = offset != 0U;
    const std::size_t skipped_beams = layout_.prts_per_beam > 0U
        ? offset / layout_.prts_per_beam : 0U;
    buffer.metadata.partial_scan = partial;
    buffer.metadata.missing_leading_prt_count = offset;
    buffer.metadata.first_scan_beam = layout_.scan_first_beam + static_cast<int>(skipped_beams);
    buffer.metadata.last_scan_beam = layout_.scan_first_beam +
        static_cast<int>(layout_.scan_beam_count) - 1;
    buffer.metadata.scan_beam_count = partial
        ? layout_.scan_beam_count - skipped_beams : layout_.scan_beam_count;
    if (config && layout_.prts_per_beam > 0U) {
        const int first_processing = std::max(config->wavepos_st, buffer.metadata.first_scan_beam);
        const int last_processing = std::min(config->wavepos_ed, buffer.metadata.last_scan_beam);
        buffer.metadata.processing_beam_count = first_processing <= last_processing
            ? static_cast<std::size_t>((last_processing - first_processing) /
                                       std::max(1, config->wavepos_skip) + 1) : 0U;
    } else {
        buffer.metadata.processing_beam_count = layout_.processing_beam_count;
    }
    buffer.metadata.integrity_status = partial
        ? "valid_partial_first_scan_missing_leading_beams"
        : "valid_complete_scan";
    buffer.metadata.config_generation = config ? config->config_generation : 0U;
    buffer.metadata.acquisition_cycle_id = layout_.prts_per_cycle > 0U
        ? static_cast<uint64_t>(counter / static_cast<uint32_t>(layout_.prts_per_cycle))
        : completed_cycle_count_;
    buffer.metadata.cycle_start_wall = std::chrono::system_clock::now();
    buffer.config = config;
    buffer.epoch = epoch_;
    filling_index_ = free_index;
    filling_prt_count_ = 0U;
    filling_target_prt_count_ = layout_.prts_per_cycle - offset;
    expected_counter_ = counter;
    fill_started_ = std::chrono::steady_clock::now();
    filling_fnv1a64_ = 1469598103934665603ULL;
    return true;
}

bool EchoCycleAssembler::acceptPrt(
    const uint8_t* prt,
    std::size_t len,
    uint32_t counter,
    double utc,
    const std::shared_ptr<const Config>& config)
{
    std::unique_lock<std::mutex> lock(mutex_);
    if (!accepting_ || stopping_) return false;

    // layout_ can be replaced by the command thread. Validate the incoming
    // packet only after taking the assembler mutex so this read cannot race a
    // layout reconfiguration.
    if (!prt || len != layout_.prt_bytes) {
        ++invalid_prt_count_;
        invalidateFillingLocked("invalid_prt_length");
        return false;
    }
    const gmti::new_protocol::HeaderSample header =
        gmti::new_protocol::readHeaderSample(prt);
    // Header velocity fields are optional and are not part of the production
    // position_delta estimator.  Validate only fields required to assemble a
    // valid PRT and reconstruct the platform pose.
    const bool navigation_valid = std::isfinite(utc) &&
        std::isfinite(header.lat_deg) && std::isfinite(header.lon_deg) &&
        std::isfinite(header.height_m) &&
        std::isfinite(header.theta_cmd_deg) &&
        header.lat_deg >= -90.0 && header.lat_deg <= 90.0 &&
        header.lon_deg >= -180.0 && header.lon_deg <= 180.0;
    if (!navigation_valid) {
        ++invalid_prt_count_;
        invalidateFillingLocked("invalid_navigation_or_time");
        ++resync_count_;
        return false;
    }

    if (filling_index_ >= 0 && layout_.cycle_timeout_ms > 0) {
        const double elapsed_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - fill_started_).count();
        if (elapsed_ms > static_cast<double>(layout_.cycle_timeout_ms)) {
            invalidateFillingLocked("cycle_timeout");
            ++resync_count_;
        }
    }

    if (filling_index_ < 0) {
        const uint32_t cycle_size = static_cast<uint32_t>(layout_.prts_per_cycle);
        const uint32_t phase = cycle_size ? layout_.counter_phase % cycle_size : 0U;
        const std::size_t offset = cycle_size
            ? static_cast<std::size_t>(
                  (static_cast<uint64_t>(counter) + cycle_size - phase) % cycle_size)
            : 0U;
        const bool partial_boundary = layout_.allow_partial_first_scan &&
            startup_scan_pending_ && layout_.prts_per_beam > 0U && offset > 0U &&
            offset % layout_.prts_per_beam == 0U;
        if (!isCycleStart(counter) && !partial_boundary) return false;
        // A complete scan must not be discarded merely because both scan
        // buffers are READY/PROCESSING.  Waiting here stops the receiver from
        // draining the byte ring; the resulting backpressure propagates to
        // the producer and preserves the input stream until processing frees
        // a buffer.  The previous immediate failure silently lost an entire
        // scan whenever acquisition outran processing.
        state_cv_.wait(lock, [this]() {
            return stopping_ || !accepting_ || findFreeLocked() >= 0;
        });
        if (stopping_ || !accepting_) return false;
        if (!beginCycleLocked(counter, utc, config)) return false;
        startup_scan_pending_ = false;
    } else if (filling_prt_count_ > 0U &&
               utc < buffers_[static_cast<std::size_t>(filling_index_)].metadata.last_utc) {
        ++invalid_prt_count_;
        invalidateFillingLocked("utc_regression");
        ++resync_count_;
        return false;
    } else if (filling_prt_count_ > 0U && counter != expected_counter_) {
        const uint32_t last = buffers_[static_cast<std::size_t>(filling_index_)].metadata.last_prt_counter;
        if (counter == last) ++duplicate_prt_count_;
        else ++prt_gap_count_;
        invalidateFillingLocked(counter == last ? "duplicate_prt" : "prt_counter_gap_or_reorder");
        ++resync_count_;
        if (!isCycleStart(counter) || !beginCycleLocked(counter, utc, config)) return false;
    }

    const std::size_t index = static_cast<std::size_t>(filling_index_);
    const std::size_t prt_index = filling_prt_count_;
    const uint64_t copy_epoch = buffers_[index].epoch;
    ++copies_in_progress_;
    lock.unlock();

    std::memcpy(buffers_[index].bytes.data() + prt_index * layout_.prt_bytes,
                prt, layout_.prt_bytes);
    uint64_t next_hash = filling_fnv1a64_;
    for (std::size_t i = 0; i < layout_.prt_bytes; ++i) {
        next_hash ^= static_cast<uint64_t>(prt[i]);
        next_hash *= 1099511628211ULL;
    }

    lock.lock();
    --copies_in_progress_;
    state_cv_.notify_all();
    if (!accepting_ || buffers_[index].state != State::FILLING ||
        buffers_[index].epoch != copy_epoch || filling_index_ != static_cast<int>(index)) {
        return false;
    }

    ++valid_prt_count_;
    filling_fnv1a64_ = next_hash;
    ++filling_prt_count_;
    expected_counter_ = counter + 1U;
    Buffer& buffer = buffers_[index];
    buffer.metadata.last_prt_counter = counter;
    buffer.metadata.last_utc = utc;
    buffer.metadata.prt_count = filling_prt_count_;

    if (filling_prt_count_ == filling_target_prt_count_) {
        buffer.metadata.cycle_fill_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - fill_started_).count();
        buffer.metadata.valid = true;
        buffer.metadata.raw_fnv1a64 = filling_fnv1a64_;
        buffer.metadata.invalid_reason.clear();
        buffer.ready_at = std::chrono::steady_clock::now();
        buffer.state = State::READY;
        filling_index_ = -1;
        filling_prt_count_ = 0U;
        filling_target_prt_count_ = 0U;
        ++completed_cycle_count_;
        ready_cv_.notify_one();
    }
    return true;
}

bool EchoCycleAssembler::waitReady(Lease& lease, int timeout_ms)
{
    std::unique_lock<std::mutex> lock(mutex_);
    const auto has_ready = [this]() {
        if (stopping_) return true;
        for (std::size_t i = 0; i < buffers_.size(); ++i) {
            if (buffers_[i].state == State::READY) return true;
        }
        return false;
    };
    if (!ready_cv_.wait_for(lock, std::chrono::milliseconds(timeout_ms), has_ready)) {
        return false;
    }
    if (stopping_) return false;
    std::size_t oldest = buffers_.size();
    for (std::size_t i = 0; i < buffers_.size(); ++i) {
        if (buffers_[i].state != State::READY) continue;
        if (oldest == buffers_.size() ||
            buffers_[i].ready_at < buffers_[oldest].ready_at) {
            oldest = i;
        }
    }
    if (oldest == buffers_.size()) return false;
    Buffer& buffer = buffers_[oldest];
    buffer.metadata.cycle_wait_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - buffer.ready_at).count();
    buffer.state = State::PROCESSING;
    lease.buffer_index = oldest;
    lease.metadata = buffer.metadata;
    lease.config = buffer.config;
    lease.view.data = buffer.bytes.data();
    lease.view.size = buffer.metadata.prt_count * layout_.prt_bytes;
    lease.view.prt_bytes = layout_.prt_bytes;
    lease.view.acquisition_cycle_id = buffer.metadata.acquisition_cycle_id;
    lease.view.partial_scan = buffer.metadata.partial_scan;
    lease.view.first_scan_beam = buffer.metadata.first_scan_beam;
    lease.view.scan_beam_count = buffer.metadata.scan_beam_count;
    lease.view.missing_leading_prt_count = buffer.metadata.missing_leading_prt_count;
    return true;
}

void EchoCycleAssembler::release(const Lease& lease, double processing_ms)
{
    std::lock_guard<std::mutex> lock(mutex_);
    if (lease.buffer_index >= buffers_.size()) return;
    Buffer& buffer = buffers_[lease.buffer_index];
    if (buffer.state == State::PROCESSING) {
        buffer.state = State::FREE;
        buffer.config.reset();
        max_processing_latency_ms_ = std::max(max_processing_latency_ms_, processing_ms);
        state_cv_.notify_all();
    }
}

void EchoCycleAssembler::pollTimeout()
{
    std::lock_guard<std::mutex> lock(mutex_);
    if (filling_index_ < 0 || layout_.cycle_timeout_ms <= 0) return;
    const double elapsed_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - fill_started_).count();
    if (elapsed_ms > static_cast<double>(layout_.cycle_timeout_ms)) {
        invalidateFillingLocked("cycle_timeout");
        ++resync_count_;
    }
}

void EchoCycleAssembler::reset(const std::string& reason)
{
    std::unique_lock<std::mutex> lock(mutex_);
    accepting_ = false;
    state_cv_.wait(lock, [this]() { return copies_in_progress_ == 0U; });
    invalidateFillingLocked(reason);
    for (std::size_t i = 0; i < buffers_.size(); ++i) {
        if (buffers_[i].state == State::READY) {
            buffers_[i].state = State::FREE;
            buffers_[i].config.reset();
        }
    }
    ++resync_count_;
    accepting_ = true;
    startup_scan_pending_ = true;
    state_cv_.notify_all();
}

bool EchoCycleAssembler::reconfigure(const EchoCycleLayout& layout, std::string& error)
{
    if (layout.prt_bytes == 0U || layout.prts_per_cycle == 0U ||
        layout.cycle_bytes != layout.prt_bytes * layout.prts_per_cycle) {
        error = "invalid replacement echo cycle layout";
        return false;
    }

    {
        std::unique_lock<std::mutex> lock(mutex_);
        accepting_ = false;
        state_cv_.wait(lock, [this]() { return copies_in_progress_ == 0U; });
        invalidateFillingLocked("layout_change");
        for (std::size_t i = 0; i < buffers_.size(); ++i) {
            if (buffers_[i].state == State::READY) {
                buffers_[i].state = State::FREE;
                buffers_[i].config.reset();
            }
        }
        state_cv_.wait(lock, [this]() {
            for (std::size_t i = 0; i < buffers_.size(); ++i) {
                if (buffers_[i].state == State::PROCESSING) return false;
            }
            return true;
        });
    }

    std::array<std::vector<uint8_t>, 2> replacement;
    try {
        replacement[0].resize(layout.cycle_bytes);
        replacement[1].resize(layout.cycle_bytes);
    } catch (const std::bad_alloc&) {
        std::lock_guard<std::mutex> lock(mutex_);
        accepting_ = true;
        error = "cannot allocate replacement double echo buffers";
        state_cv_.notify_all();
        return false;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    layout_ = layout;
    for (std::size_t i = 0; i < buffers_.size(); ++i) {
        buffers_[i].bytes.swap(replacement[i]);
        buffers_[i].state = State::FREE;
        buffers_[i].config.reset();
        buffers_[i].metadata = EchoCycleMetadata();
        buffers_[i].epoch = ++epoch_;
    }
    accepting_ = true;
    startup_scan_pending_ = true;
    ++resync_count_;
    state_cv_.notify_all();
    return true;
}

void EchoCycleAssembler::shutdown()
{
    std::lock_guard<std::mutex> lock(mutex_);
    stopping_ = true;
    accepting_ = false;
    ready_cv_.notify_all();
    state_cv_.notify_all();
}

EchoCycleLayout EchoCycleAssembler::layout() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return layout_;
}

ShmEchoMetricsSnapshot EchoCycleAssembler::metrics() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    ShmEchoMetricsSnapshot m;
    m.shm_blocks_received = shm_blocks_received_;
    m.shm_bytes_received = shm_bytes_received_;
    m.valid_prt_count = valid_prt_count_;
    m.invalid_prt_count = invalid_prt_count_;
    m.prt_gap_count = prt_gap_count_;
    m.duplicate_prt_count = duplicate_prt_count_;
    m.resync_count = resync_count_;
    m.completed_cycle_count = completed_cycle_count_;
    m.dropped_incomplete_cycle_count = dropped_incomplete_cycle_count_;
    m.dropped_backpressure_cycle_count = dropped_backpressure_cycle_count_;
    m.ring_overrun_count = ring_overrun_count_;
    m.max_processing_latency_ms = max_processing_latency_ms_;
    return m;
}

void EchoCycleAssembler::setReceiverTotals(uint64_t blocks,
                                           uint64_t bytes,
                                           uint64_t overruns)
{
    std::lock_guard<std::mutex> lock(mutex_);
    shm_blocks_received_ = blocks;
    shm_bytes_received_ = bytes;
    ring_overrun_count_ = overruns;
}

class ShmEchoReceiver::Impl {
public:
    std::unique_ptr<RingBufferManager> manager;
    dev_t attached_device = 0;
    ino_t attached_inode = 0;
    bool has_identity = false;
    std::chrono::steady_clock::time_point next_identity_probe;
};

ShmEchoReceiver::ShmEchoReceiver(const std::string& name,
                                 std::size_t read_chunk_bytes)
    : impl_(new Impl()), name_(name), block_(read_chunk_bytes)
{
    if (name_.empty() || name_[0] != '/') {
        throw std::invalid_argument("POSIX shared-memory name must start with '/'");
    }
    if (block_.empty()) {
        throw std::invalid_argument("shared-memory read block must be non-empty");
    }
}

ShmEchoReceiver::~ShmEchoReceiver() = default;

bool ShmEchoReceiver::poll(const uint8_t*& data,
                           std::size_t& len,
                           std::string& error)
{
    data = nullptr;
    len = 0U;
    error.clear();
    try {
        if (!impl_->manager) {
            impl_->manager.reset(new RingBufferManager(
                name_, RingBufferManager::RECEIVER));
            const std::size_t mapped = impl_->manager->mapped_size();
            const std::size_t capacity = impl_->manager->get_ring().capacity();
            const bool power_of_two = capacity != 0U &&
                (capacity & (capacity - 1U)) == 0U;
            const bool in_mapping = mapped >= sizeof(SharedControl) &&
                capacity <= mapped - sizeof(SharedControl);
            if (!power_of_two || !in_mapping) {
                std::ostringstream oss;
                oss << "shared-memory object does not match expected RingBuffer ABI"
                    << " (mapped=" << mapped << ", capacity=" << capacity << ')';
                throw std::runtime_error(oss.str());
            }
            std::string identity_error;
            if (!queryPosixShmIdentity(name_, impl_->attached_device,
                                       impl_->attached_inode, identity_error)) {
                // The mapping is still valid at this instant, but cannot be
                // tied to a currently named POSIX object. Treat it as a
                // disconnected sender so a subsequent recreation is not
                // confused with this stale map.
                throw std::runtime_error(identity_error);
            }
            impl_->has_identity = true;
            impl_->next_identity_probe = std::chrono::steady_clock::now() +
                std::chrono::milliseconds(kShmIdentityProbeMs);
        } else if (std::chrono::steady_clock::now() >= impl_->next_identity_probe) {
            dev_t device = 0;
            ino_t inode = 0;
            std::string identity_error;
            const bool identity_ok = queryPosixShmIdentity(
                name_, device, inode, identity_error);
            impl_->next_identity_probe = std::chrono::steady_clock::now() +
                std::chrono::milliseconds(kShmIdentityProbeMs);
            if (!identity_ok || !impl_->has_identity ||
                device != impl_->attached_device || inode != impl_->attached_inode) {
                if (identity_ok) {
                    std::ostringstream oss;
                    oss << "shared-memory object was recreated (old inode="
                        << impl_->attached_inode << ", new inode=" << inode << ')';
                    error = oss.str();
                } else {
                    error = identity_error;
                }
                impl_->manager.reset();
                impl_->has_identity = false;
                return false;
            }
        }
        std::size_t request = block_.size();
        if (!impl_->manager->get_ring().try_read(block_.data(), request)) {
            return false;
        }
        if (request == 0U) {
            // Defensive only: the byte-counted shmdemo ring never reports a
            // successful zero-byte read.
            ++overrun_count_;
            return false;
        }
        data = block_.data();
        len = request;
        ++block_count_;
        byte_count_ += request;
        return true;
    } catch (const std::exception& e) {
        error = e.what();
        impl_->manager.reset();
        impl_->has_identity = false;
        return false;
    }
}

void ShmEchoReceiver::disconnect()
{
    impl_->manager.reset();
    impl_->has_identity = false;
}

bool ShmEchoReceiver::attached() const
{
    return impl_ && impl_->manager.get() != nullptr;
}
