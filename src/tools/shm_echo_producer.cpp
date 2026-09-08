#include "RingBuffer.h"
#include "dbs/NewProtocolLayout.hpp"

#include <algorithm>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

volatile std::sig_atomic_t keep_running = 1;

void handleSignal(int)
{
    keep_running = 0;
}

struct Options {
    std::string input;
    std::string shm_name = "/time_seq_shm";
    std::size_t ring_bytes = 1ULL * 1024U * 1024U * 1024U;
    std::size_t chunk_bytes = 1024U * 1024U;
    int repeat = 1;
    double rate_bytes_per_sec = 0.0;
    double prf_hz = 0.0;
    int linger_ms = 5000;
    int drop_prt = -1;
    int duplicate_prt = -1;
    int corrupt_header_prt = -1;
    std::size_t stop_after_bytes = 0U;
    bool append_clean = false;
    bool compute_input_hash = true;
};

struct InputInfo {
    std::size_t bytes = 0U;
    std::size_t prt_bytes = 0U;
    uint64_t fnv1a64 = 1469598103934665603ULL;
};

bool parseSize(const std::string& text, std::size_t& value)
{
    try {
        std::size_t pos = 0U;
        const unsigned long long v = std::stoull(text, &pos);
        if (pos != text.size()) return false;
        value = static_cast<std::size_t>(v);
        return true;
    } catch (...) {
        return false;
    }
}

bool parseArgs(int argc, char** argv, Options& o)
{
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        const auto value = [&](const char* name) -> const char* {
            if (i + 1 >= argc) {
                std::cerr << "missing value for " << name << std::endl;
                return nullptr;
            }
            return argv[++i];
        };
        if (a == "--input") {
            const char* v = value("--input"); if (!v) return false; o.input = v;
        } else if (a == "--shm-name") {
            const char* v = value("--shm-name"); if (!v) return false; o.shm_name = v;
        } else if (a == "--ring-bytes") {
            const char* v = value("--ring-bytes"); if (!v || !parseSize(v, o.ring_bytes)) return false;
        } else if (a == "--chunk-bytes") {
            const char* v = value("--chunk-bytes"); if (!v || !parseSize(v, o.chunk_bytes)) return false;
        } else if (a == "--repeat") {
            const char* v = value("--repeat"); if (!v) return false; o.repeat = std::atoi(v);
        } else if (a == "--rate-bytes-per-sec") {
            const char* v = value("--rate-bytes-per-sec"); if (!v) return false; o.rate_bytes_per_sec = std::atof(v);
        } else if (a == "--prf-hz") {
            const char* v = value("--prf-hz"); if (!v) return false; o.prf_hz = std::atof(v);
        } else if (a == "--linger-ms") {
            const char* v = value("--linger-ms"); if (!v) return false; o.linger_ms = std::atoi(v);
        } else if (a == "--drop-prt") {
            const char* v = value("--drop-prt"); if (!v) return false; o.drop_prt = std::atoi(v);
        } else if (a == "--duplicate-prt") {
            const char* v = value("--duplicate-prt"); if (!v) return false; o.duplicate_prt = std::atoi(v);
        } else if (a == "--corrupt-header-prt") {
            const char* v = value("--corrupt-header-prt"); if (!v) return false; o.corrupt_header_prt = std::atoi(v);
        } else if (a == "--stop-after-bytes") {
            const char* v = value("--stop-after-bytes"); if (!v || !parseSize(v, o.stop_after_bytes)) return false;
        } else if (a == "--append-clean") {
            o.append_clean = true;
        } else if (a == "--skip-input-hash") {
            o.compute_input_hash = false;
        } else {
            std::cerr << "unknown argument: " << a << std::endl;
            return false;
        }
    }
    return !o.input.empty() && o.repeat > 0 && o.chunk_bytes > 0U;
}

std::vector<uint8_t> loadFile(const std::string& path)
{
    std::ifstream in(path.c_str(), std::ios::binary);
    if (!in) throw std::runtime_error("cannot open input: " + path);
    in.seekg(0, std::ios::end);
    const std::streamsize n = in.tellg();
    if (n <= 0) throw std::runtime_error("input is empty: " + path);
    in.seekg(0, std::ios::beg);
    std::vector<uint8_t> bytes(static_cast<std::size_t>(n));
    in.read(reinterpret_cast<char*>(bytes.data()), n);
    if (in.gcount() != n) throw std::runtime_error("short input read");
    return bytes;
}

InputInfo inspectFile(const std::string& path, std::size_t chunk_bytes,
                      bool compute_hash)
{
    std::ifstream in(path.c_str(), std::ios::binary);
    if (!in) throw std::runtime_error("cannot open input: " + path);
    std::vector<uint8_t> header(gmti::new_protocol::kHeaderBytes);
    in.read(reinterpret_cast<char*>(header.data()),
            static_cast<std::streamsize>(header.size()));
    if (in.gcount() != static_cast<std::streamsize>(header.size())) {
        throw std::runtime_error("input is shorter than one PRT header");
    }
    InputInfo info;
    info.prt_bytes = gmti::new_protocol::loadU32LE(
        header.data() + gmti::new_protocol::kOffPrtLen);
    if (info.prt_bytes < gmti::new_protocol::kHeaderBytes) {
        throw std::runtime_error("invalid PRT length in input header");
    }
    in.clear();
    in.seekg(0, std::ios::end);
    const std::streamsize size = in.tellg();
    if (size <= 0) throw std::runtime_error("input is empty: " + path);
    info.bytes = static_cast<std::size_t>(size);
    if (info.bytes % info.prt_bytes != 0U) {
        throw std::runtime_error("input size/PRT length mismatch");
    }
    if (compute_hash) {
        in.clear();
        in.seekg(0, std::ios::beg);
        std::vector<uint8_t> buf(std::max<std::size_t>(4096U, chunk_bytes));
        while (in) {
            in.read(reinterpret_cast<char*>(buf.data()),
                    static_cast<std::streamsize>(buf.size()));
            const std::streamsize got = in.gcount();
            for (std::streamsize i = 0; i < got; ++i) {
                info.fnv1a64 ^= static_cast<uint64_t>(buf[static_cast<std::size_t>(i)]);
                info.fnv1a64 *= 1099511628211ULL;
            }
        }
    }
    return info;
}

std::vector<uint8_t> makeStream(const std::vector<uint8_t>& source,
                                std::size_t prt_bytes,
                                const Options& o)
{
    const std::size_t count = source.size() / prt_bytes;
    std::vector<uint8_t> out;
    out.reserve(source.size() + (o.duplicate_prt >= 0 ? prt_bytes : 0U));
    for (std::size_t i = 0; i < count; ++i) {
        if (static_cast<int>(i) == o.drop_prt) continue;
        const std::size_t old_size = out.size();
        out.insert(out.end(), source.begin() + static_cast<std::ptrdiff_t>(i * prt_bytes),
                   source.begin() + static_cast<std::ptrdiff_t>((i + 1U) * prt_bytes));
        if (static_cast<int>(i) == o.corrupt_header_prt) out[old_size] ^= 0xFFU;
        if (static_cast<int>(i) == o.duplicate_prt) {
            out.insert(out.end(), source.begin() + static_cast<std::ptrdiff_t>(i * prt_bytes),
                       source.begin() + static_cast<std::ptrdiff_t>((i + 1U) * prt_bytes));
        }
    }
    return out;
}

} // namespace

int main(int argc, char** argv)
{
    std::signal(SIGINT, handleSignal);
    std::signal(SIGTERM, handleSignal);
    Options options;
    if (!parseArgs(argc, argv, options)) {
        std::cerr << "Usage: " << argv[0]
                  << " --input FILE [--shm-name /NAME] [--ring-bytes N] [--chunk-bytes N]"
                  << " [--repeat N] [--prf-hz HZ] [--rate-bytes-per-sec N] [--linger-ms N]"
                  << " [--drop-prt I] [--duplicate-prt I] [--corrupt-header-prt I]"
                  << " [--stop-after-bytes N] [--append-clean]\n";
        return 2;
    }

    try {
        const InputInfo input = inspectFile(
            options.input, options.chunk_bytes, options.compute_input_hash);
        const bool transform = options.drop_prt >= 0 || options.duplicate_prt >= 0 ||
                               options.corrupt_header_prt >= 0;
        std::vector<uint8_t> source;
        std::vector<uint8_t> stream;
        uint64_t stream_hash = input.fnv1a64;
        if (transform) {
            source = loadFile(options.input);
            stream = makeStream(source, input.prt_bytes, options);
            std::vector<uint8_t>().swap(source);
            stream_hash = 1469598103934665603ULL;
            for (std::size_t i = 0; i < stream.size(); ++i) {
                stream_hash ^= static_cast<uint64_t>(stream[i]);
                stream_hash *= 1099511628211ULL;
            }
        }
        RingBufferManager manager(options.shm_name, RingBufferManager::SENDER,
                                  options.ring_bytes);
        RingBuffer& ring = manager.get_ring();
        const double replay_prf = options.prf_hz > 0.0
            ? options.prf_hz
            : (options.rate_bytes_per_sec > 0.0
                ? options.rate_bytes_per_sec / static_cast<double>(input.prt_bytes)
                : 0.0);
        const double target_rate = replay_prf > 0.0
            ? replay_prf * static_cast<double>(input.prt_bytes)
            : options.rate_bytes_per_sec;
        const std::size_t source_prts = input.bytes / input.prt_bytes;
        std::cout << "[PRODUCER] shm=" << options.shm_name
                  << " ring_bytes=" << options.ring_bytes
                  << " chunk_bytes=" << options.chunk_bytes
                  << " source_bytes=" << input.bytes
                  << " prt_bytes=" << input.prt_bytes
                  << " source_prts=" << source_prts
                  << " emitted_bytes_per_repeat=" << (transform ? stream.size() : input.bytes)
                  << " repeat=" << options.repeat
                  << " replay_mode=" << (transform ? "memory_transform" : "stream_file")
                  << " prf_hz=" << replay_prf
                  << " target_bytes_per_sec=" << target_rate
                  << " prt_interval_us=" << (replay_prf > 0.0 ? 1.0e6 / replay_prf : 0.0)
                  << " expected_seconds=" << (replay_prf > 0.0
                      ? static_cast<double>(source_prts * options.repeat) / replay_prf : 0.0)
                  << " fnv1a64=";
        if (options.compute_input_hash || transform) {
            std::cout << "0x" << std::hex << stream_hash << std::dec;
        } else {
            std::cout << "skipped_sha256_recorded_by_caller";
        }
        std::cout << std::endl;

        uint64_t total = 0U;
        uint64_t emitted_prts = 0U;
        double ring_wait_seconds = 0.0;
        const auto started = std::chrono::steady_clock::now();
        bool stopped_early = false;
        const auto write_chunk = [&](const uint8_t* data, std::size_t requested) -> std::size_t {
            std::size_t n = requested;
            if (options.stop_after_bytes > 0U && total + n > options.stop_after_bytes) {
                n = static_cast<std::size_t>(options.stop_after_bytes - total);
            }
            if (n == 0U) return 0U;
            const auto wait_started = std::chrono::steady_clock::now();
            if (!ring.write(data, n)) {
                throw std::runtime_error("RingBuffer::write failed");
            }
            ring_wait_seconds += std::chrono::duration<double>(
                std::chrono::steady_clock::now() - wait_started).count();
            total += n;
            if (replay_prf <= 0.0 && options.rate_bytes_per_sec > 0.0) {
                const double expected = static_cast<double>(total) /
                                        options.rate_bytes_per_sec;
                const double elapsed = std::chrono::duration<double>(
                    std::chrono::steady_clock::now() - started).count();
                if (expected > elapsed) {
                    std::this_thread::sleep_for(
                        std::chrono::duration<double>(expected - elapsed));
                }
            }
            return n;
        };
        const int total_repeats = options.repeat + (options.append_clean ? 1 : 0);
        for (int r = 0; r < total_repeats && !stopped_early; ++r) {
            const bool clean_tail = options.append_clean && r == total_repeats - 1;
            if (!transform || clean_tail) {
                std::ifstream in(options.input.c_str(), std::ios::binary);
                if (!in) throw std::runtime_error("cannot reopen input for streaming");
                const std::size_t read_bytes = replay_prf > 0.0
                    ? input.prt_bytes : options.chunk_bytes;
                std::vector<uint8_t> buf(read_bytes);
                while (in && !stopped_early) {
                    if (replay_prf > 0.0) {
                        const auto planned = started + std::chrono::duration_cast<
                            std::chrono::steady_clock::duration>(
                                std::chrono::duration<double>(
                                    static_cast<double>(emitted_prts) / replay_prf));
                        std::this_thread::sleep_until(planned);
                    }
                    in.read(reinterpret_cast<char*>(buf.data()),
                            static_cast<std::streamsize>(buf.size()));
                    const std::streamsize got = in.gcount();
                    if (got <= 0) break;
                    if (replay_prf > 0.0 &&
                        got != static_cast<std::streamsize>(input.prt_bytes)) {
                        throw std::runtime_error("short PRT read during paced replay");
                    }
                    const std::size_t written = write_chunk(
                        buf.data(), static_cast<std::size_t>(got));
                    if (replay_prf > 0.0 && written == input.prt_bytes) ++emitted_prts;
                    if (written != static_cast<std::size_t>(got) ||
                        (options.stop_after_bytes > 0U && total >= options.stop_after_bytes)) {
                        stopped_early = true;
                    }
                }
            } else {
                std::size_t off = 0U;
                while (off < stream.size() && !stopped_early) {
                    const std::size_t requested = std::min(
                        options.chunk_bytes, stream.size() - off);
                    const std::size_t written = write_chunk(stream.data() + off, requested);
                    off += written;
                    if (written != requested ||
                        (options.stop_after_bytes > 0U && total >= options.stop_after_bytes)) {
                        stopped_early = true;
                    }
                }
            }
        }
        const double seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started).count();
        std::cout << "[PRODUCER] complete bytes=" << total
                  << " prts=" << (replay_prf > 0.0 ? emitted_prts : total / input.prt_bytes)
                  << " seconds=" << seconds
                  << " bytes_per_sec=" << (seconds > 0.0 ? total / seconds : 0.0)
                  << " average_prf_hz=" << (seconds > 0.0
                      ? static_cast<double>(replay_prf > 0.0 ? emitted_prts : total / input.prt_bytes) / seconds
                      : 0.0)
                  << " ring_wait_seconds=" << ring_wait_seconds
                  << " stopped_early=" << (stopped_early ? "true" : "false")
                  << " (demo has no block sequence field)" << std::endl;
        if (options.linger_ms < 0) {
            while (keep_running) {
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
        } else if (options.linger_ms > 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(options.linger_ms));
        }
    } catch (const std::exception& e) {
        std::cerr << "[PRODUCER][ERR] " << e.what() << std::endl;
        return 1;
    }
    return 0;
}
