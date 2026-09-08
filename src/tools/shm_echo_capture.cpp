#include "pipe/ShmEchoInput.h"

#include <chrono>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <memory>
#include <string>
#include <thread>

int main(int argc, char** argv)
{
    std::string shm_name = "/time_seq_shm";
    std::size_t prt_bytes = 0U;
    std::size_t prts_per_cycle = 0U;
    std::size_t chunk_bytes = 1024U * 1024U;
    int expected_cycles = 1;
    int min_dropped = 0;
    int timeout_ms = 10000;
    int cycle_timeout_ms = 1000;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--shm-name" && i + 1 < argc) shm_name = argv[++i];
        else if (a == "--prt-bytes" && i + 1 < argc) prt_bytes = std::stoull(argv[++i]);
        else if (a == "--prts-per-cycle" && i + 1 < argc) prts_per_cycle = std::stoull(argv[++i]);
        else if (a == "--chunk-bytes" && i + 1 < argc) chunk_bytes = std::stoull(argv[++i]);
        else if (a == "--expect-cycles" && i + 1 < argc) expected_cycles = std::stoi(argv[++i]);
        else if (a == "--min-dropped" && i + 1 < argc) min_dropped = std::stoi(argv[++i]);
        else if (a == "--timeout-ms" && i + 1 < argc) timeout_ms = std::stoi(argv[++i]);
        else if (a == "--cycle-timeout-ms" && i + 1 < argc) cycle_timeout_ms = std::stoi(argv[++i]);
        else {
            std::cerr << "Usage: " << argv[0]
                      << " --prt-bytes N --prts-per-cycle N [--shm-name /NAME]"
                      << " [--chunk-bytes N] [--expect-cycles N] [--min-dropped N]"
                      << " [--timeout-ms N] [--cycle-timeout-ms N]\n";
            return 2;
        }
    }
    if (prt_bytes < 256U || prts_per_cycle == 0U) return 2;

    EchoCycleLayout layout;
    layout.prt_bytes = prt_bytes;
    layout.prts_per_beam = prts_per_cycle;
    layout.scan_beam_count = 1U;
    layout.processing_beam_count = 1U;
    layout.prts_per_cycle = prts_per_cycle;
    layout.cycle_bytes = prt_bytes * prts_per_cycle;
    layout.expected_prt_mode = 9U;
    layout.counter_phase = 0U;
    layout.cycle_timeout_ms = cycle_timeout_ms;

    try {
        ShmEchoReceiver receiver(shm_name, chunk_bytes);
        PrtStreamParser parser(prt_bytes, chunk_bytes, 9U);
        EchoCycleAssembler assembler(layout);
        std::shared_ptr<Config> cfg(new Config());
        cfg->config_generation = 1U;
        int captured = 0;
        const auto deadline = std::chrono::steady_clock::now() +
                              std::chrono::milliseconds(timeout_ms);
        while (std::chrono::steady_clock::now() < deadline) {
            const uint8_t* block = nullptr;
            std::size_t len = 0U;
            std::string error;
            if (receiver.poll(block, len, error)) {
                assembler.setReceiverTotals(receiver.blockCount(), receiver.byteCount(),
                                            receiver.overrunCount());
                parser.feed(block, len,
                    [&](const uint8_t* p, std::size_t n, uint32_t counter, double utc) {
                        assembler.acceptPrt(p, n, counter, utc, cfg);
                    });
            } else {
                assembler.pollTimeout();
                std::this_thread::sleep_for(std::chrono::microseconds(100));
            }
            EchoCycleAssembler::Lease lease;
            while (assembler.waitReady(lease, 1)) {
                ++captured;
                std::cout << "[CAPTURE] cycle=" << captured
                          << " counters=" << lease.metadata.first_prt_counter << ".."
                          << lease.metadata.last_prt_counter
                          << " bytes=" << lease.view.size
                          << " fnv1a64=0x" << std::hex << lease.metadata.raw_fnv1a64
                          << std::dec << std::endl;
                assembler.release(lease, 0.0);
            }
            const ShmEchoMetricsSnapshot m = assembler.metrics();
            if (captured >= expected_cycles &&
                static_cast<int>(m.dropped_incomplete_cycle_count) >= min_dropped) {
                break;
            }
        }
        const ShmEchoMetricsSnapshot m = assembler.metrics();
        std::cout << "[CAPTURE][METRICS] blocks=" << receiver.blockCount()
                  << " bytes=" << receiver.byteCount()
                  << " valid_prt=" << parser.validPrtCount()
                  << " invalid_prt=" << parser.invalidPrtCount()
                  << " gaps=" << m.prt_gap_count
                  << " duplicates=" << m.duplicate_prt_count
                  << " resync=" << (parser.resyncCount() + m.resync_count)
                  << " completed=" << m.completed_cycle_count
                  << " dropped_incomplete=" << m.dropped_incomplete_cycle_count
                  << " dropped_backpressure=" << m.dropped_backpressure_cycle_count
                  << " ring_overrun=" << receiver.overrunCount() << std::endl;
        return captured == expected_cycles &&
               static_cast<int>(m.dropped_incomplete_cycle_count) >= min_dropped
            ? 0 : 1;
    } catch (const std::exception& e) {
        std::cerr << "[CAPTURE][ERR] " << e.what() << std::endl;
        return 1;
    }
}
