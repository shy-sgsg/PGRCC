#ifndef SHM_ECHO_INPUT_H
#define SHM_ECHO_INPUT_H

#include "config_structs.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

struct EchoCycleLayout {
    std::size_t prt_bytes = 0;
    std::size_t prts_per_beam = 0;
    int scan_first_beam = 1;
    std::size_t scan_beam_count = 0;
    std::size_t processing_beam_count = 0;
    std::size_t prts_per_cycle = 0;
    std::size_t cycle_bytes = 0;
    uint8_t expected_prt_mode = 9;
    uint32_t counter_phase = 0;
    int cycle_timeout_ms = 0;
    bool allow_partial_first_scan = false;
};

struct EchoCycleMetadata {
    uint64_t acquisition_cycle_id = 0;
    uint64_t config_generation = 0;
    uint32_t first_prt_counter = 0;
    uint32_t last_prt_counter = 0;
    double first_utc = 0.0;
    double last_utc = 0.0;
    std::size_t prt_count = 0;
    std::size_t scan_beam_count = 0;
    std::size_t processing_beam_count = 0;
    int first_scan_beam = 0;
    int last_scan_beam = 0;
    std::size_t missing_leading_prt_count = 0;
    bool partial_scan = false;
    std::string integrity_status;
    double cycle_fill_ms = 0.0;
    double cycle_wait_ms = 0.0;
    // Wall-clock origin of the acquisition cycle.  This is kept in the
    // in-process metadata so the consumer can report an end-to-end interval
    // with the same time origin as the algorithm result hand-off.
    std::chrono::system_clock::time_point cycle_start_wall;
    uint64_t raw_fnv1a64 = 0;
    bool valid = false;
    std::string invalid_reason;
};

// Derive one acquisition unit. Electronic mode uses a contiguous physical-beam
// span. Mechanical mode uses acquisition_scan_prt_count directly and exposes
// no synthetic beam count; CPI windows are cut only after the full sweep is
// assembled and its PRT headers have been parsed.
bool deriveEchoScanLayout(const Config& cfg,
                          EchoCycleLayout& layout,
                          std::string& error);

// Select the preprepared one-period input used only by laboratory test=1/2.
// Trigger IDs are one-based. once selects each configured path at most once;
// loop wraps to the first path. A false return leaves no ambiguous fallback
// path.
bool selectTestCycleDataFile(const Config& cfg,
                             uint64_t completed_cycle_id,
                             std::size_t& selected_index,
                             std::string& selected_path,
                             std::string& error);

struct ShmEchoMetricsSnapshot {
    uint64_t shm_blocks_received = 0;
    uint64_t shm_bytes_received = 0;
    uint64_t valid_prt_count = 0;
    uint64_t invalid_prt_count = 0;
    uint64_t prt_gap_count = 0;
    uint64_t duplicate_prt_count = 0;
    uint64_t resync_count = 0;
    uint64_t completed_cycle_count = 0;
    uint64_t dropped_incomplete_cycle_count = 0;
    uint64_t dropped_backpressure_cycle_count = 0;
    uint64_t ring_overrun_count = 0;
    double max_processing_latency_ms = 0.0;
};

// The shmdemo transport is a byte ring, not a fixed-slot protocol: a read
// reports only the number of bytes copied.  In laboratory test mode those
// bytes deliberately remain opaque.  This small queue carries only boundary
// events to the processing thread; it never retains the shared-memory payload.
struct TestCycleTrigger {
    uint64_t completed_cycle_id = 0;
    uint64_t config_generation = 0;
    uint64_t source_block_index = 0;
    std::size_t source_valid_bytes = 0;
    uint64_t cycle_bytes = 0;
    uint64_t remaining_bytes = 0;
    std::chrono::system_clock::time_point cycle_start_wall;
};

struct TestCycleMetricsSnapshot {
    uint64_t shm_blocks_received = 0;
    uint64_t shm_bytes_received = 0;
    uint64_t completed_cycle_count = 0;
    uint64_t accumulated_bytes = 0;
    uint64_t cycle_bytes = 0;
    uint64_t dropped_slot_count = 0;
    uint64_t overwrite_count = 0;
    uint64_t max_pending_trigger_count = 0;
};

class TestCycleByteAccumulator {
public:
    explicit TestCycleByteAccumulator(uint64_t cycle_bytes,
                                      std::size_t max_pending_triggers = 2U);

    // Accounts for one successfully consumed shmdemo read.  If the byte
    // count crosses one or more cycle boundaries, enqueue one trigger per
    // complete cycle and preserve the exact remainder for the next read.
    void account(std::size_t valid_bytes,
                 uint64_t source_block_index,
                 uint64_t config_generation);
    bool waitReady(TestCycleTrigger& trigger, int timeout_ms);
    void release(const TestCycleTrigger& trigger, double processing_ms);
    void shutdown();
    TestCycleMetricsSnapshot metrics() const;

private:
    mutable std::mutex mutex_;
    std::condition_variable ready_cv_;
    std::condition_variable space_cv_;
    std::deque<TestCycleTrigger> pending_;
    uint64_t cycle_bytes_ = 0;
    uint64_t accumulated_bytes_ = 0;
    uint64_t completed_cycle_count_ = 0;
    uint64_t shm_blocks_received_ = 0;
    uint64_t shm_bytes_received_ = 0;
    uint64_t max_pending_trigger_count_ = 0;
    std::size_t max_pending_triggers_ = 0;
    bool stopping_ = false;
    std::chrono::system_clock::time_point current_cycle_start_wall_;
};

class PrtStreamParser {
public:
    typedef std::function<void(const uint8_t*, std::size_t, uint32_t, double)> PrtCallback;

    PrtStreamParser(std::size_t expected_prt_bytes,
                    std::size_t max_input_chunk,
                    uint8_t expected_mode);

    void feed(const uint8_t* data, std::size_t len, const PrtCallback& callback);
    void reset();
    uint64_t validPrtCount() const { return valid_prt_count_.load(); }
    uint64_t invalidPrtCount() const { return invalid_prt_count_.load(); }
    uint64_t resyncCount() const { return resync_count_.load(); }

private:
    void parseAvailable(const PrtCallback& callback);
    bool hasHead(std::size_t off) const;
    bool hasTail(std::size_t off) const;
    std::size_t findHead(std::size_t from) const;

    std::size_t expected_prt_bytes_;
    uint8_t expected_mode_;
    std::vector<uint8_t> staging_;
    std::size_t used_ = 0;
    std::atomic<uint64_t> valid_prt_count_{0};
    std::atomic<uint64_t> invalid_prt_count_{0};
    std::atomic<uint64_t> resync_count_{0};
};

// test=2 uses the exact production PRT framing parser as a bounded,
// diagnostics-only side path.  It never hands a PRT to EchoCycleAssembler,
// never retains a raw cycle and never controls TestCycleByteAccumulator.
// Therefore malformed laboratory payload is reported but cannot stop the
// prepared-file trigger sequence.
struct ShmProtocolAuditSnapshot {
    uint64_t shm_blocks_inspected = 0;
    uint64_t shm_bytes_inspected = 0;
    uint64_t valid_prt_count = 0;
    uint64_t framing_or_header_invalid_count = 0;
    uint64_t resync_count = 0;
    uint64_t counter_discontinuity_count = 0;
    uint64_t missing_prt_count = 0;
    uint64_t duplicate_prt_count = 0;
    uint64_t out_of_order_prt_count = 0;
    uint64_t nonfinite_navigation_count = 0;
    uint64_t geodetic_range_error_count = 0;
    uint64_t prt_low_byte_mismatch_count = 0;
    bool have_last_counter = false;
    uint32_t last_counter = 0;
};

class ShmProtocolAudit {
public:
    ShmProtocolAudit(const EchoCycleLayout& layout,
                     std::size_t max_input_chunk);

    // Inspect a successfully consumed shmdemo byte block.  Any malformed PRT
    // is counted and resynchronized by PrtStreamParser; no failure here is a
    // reason to discard the block from test=2 byte accounting.
    void inspect(const uint8_t* data, std::size_t len);
    void reset();
    ShmProtocolAuditSnapshot snapshot() const;

private:
    void inspectValidPrt(const uint8_t* prt,
                         std::size_t len,
                         uint32_t counter,
                         double utc);

    mutable std::mutex mutex_;
    PrtStreamParser parser_;
    ShmProtocolAuditSnapshot metrics_;
};

class EchoCycleAssembler {
public:
    enum class State { FREE, FILLING, READY, PROCESSING };

    struct Lease {
        std::size_t buffer_index = static_cast<std::size_t>(-1);
        EchoCycleView view;
        EchoCycleMetadata metadata;
        std::shared_ptr<const Config> config;

        bool valid() const { return buffer_index != static_cast<std::size_t>(-1) && view.valid(); }
    };

    explicit EchoCycleAssembler(const EchoCycleLayout& layout);

    bool acceptPrt(const uint8_t* prt,
                   std::size_t len,
                   uint32_t counter,
                   double utc,
                   const std::shared_ptr<const Config>& config);
    bool waitReady(Lease& lease, int timeout_ms);
    void release(const Lease& lease, double processing_ms);
    void pollTimeout();
    void reset(const std::string& reason);
    bool reconfigure(const EchoCycleLayout& layout, std::string& error);
    void shutdown();

    EchoCycleLayout layout() const;
    ShmEchoMetricsSnapshot metrics() const;
    void setReceiverTotals(uint64_t blocks, uint64_t bytes, uint64_t overruns);

private:
    struct Buffer {
        std::vector<uint8_t> bytes;
        State state = State::FREE;
        EchoCycleMetadata metadata;
        std::shared_ptr<const Config> config;
        uint64_t epoch = 0;
        std::chrono::steady_clock::time_point ready_at;
    };

    bool isCycleStart(uint32_t counter) const;
    int findFreeLocked() const;
    void invalidateFillingLocked(const std::string& reason);
    bool beginCycleLocked(uint32_t counter,
                          double utc,
                          const std::shared_ptr<const Config>& config);

    mutable std::mutex mutex_;
    std::condition_variable ready_cv_;
    std::condition_variable state_cv_;
    std::array<Buffer, 2> buffers_;
    EchoCycleLayout layout_;
    int filling_index_ = -1;
    std::size_t filling_prt_count_ = 0;
    std::size_t filling_target_prt_count_ = 0;
    uint32_t expected_counter_ = 0;
    uint64_t epoch_ = 1;
    bool startup_scan_pending_ = true;
    std::size_t copies_in_progress_ = 0;
    bool accepting_ = true;
    bool stopping_ = false;
    std::chrono::steady_clock::time_point fill_started_;
    uint64_t filling_fnv1a64_ = 1469598103934665603ULL;

    uint64_t shm_blocks_received_ = 0;
    uint64_t shm_bytes_received_ = 0;
    uint64_t valid_prt_count_ = 0;
    uint64_t invalid_prt_count_ = 0;
    uint64_t prt_gap_count_ = 0;
    uint64_t duplicate_prt_count_ = 0;
    uint64_t resync_count_ = 0;
    uint64_t completed_cycle_count_ = 0;
    uint64_t dropped_incomplete_cycle_count_ = 0;
    uint64_t dropped_backpressure_cycle_count_ = 0;
    uint64_t ring_overrun_count_ = 0;
    double max_processing_latency_ms_ = 0.0;
};

class ShmEchoReceiver {
public:
    ShmEchoReceiver(const std::string& name, std::size_t read_chunk_bytes);
    ~ShmEchoReceiver();

    // Returns true only when a non-empty block is available. An empty error
    // means the ring currently has no readable token.
    bool poll(const uint8_t*& data, std::size_t& len, std::string& error);
    void disconnect();
    bool attached() const;
    uint64_t blockCount() const { return block_count_; }
    uint64_t byteCount() const { return byte_count_; }
    uint64_t overrunCount() const { return overrun_count_; }

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
    std::string name_;
    std::vector<uint8_t> block_;
    uint64_t block_count_ = 0;
    uint64_t byte_count_ = 0;
    uint64_t overrun_count_ = 0;
};

#endif // SHM_ECHO_INPUT_H
