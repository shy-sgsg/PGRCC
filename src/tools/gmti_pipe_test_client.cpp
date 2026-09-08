#include "pipe/PipeStruDef.h"
#include "pipe/PipeRW.h"

#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <poll.h>
#include <string>
#include <unistd.h>
#include <vector>

namespace {

std::string pipePath(const std::string& root, const char* leaf)
{
    return root + (root.empty() || root.back() == '/' ? "" : "/") + leaf;
}

bool readExact(int fd, void* out, std::size_t bytes, int timeout_ms)
{
    uint8_t* p = static_cast<uint8_t*>(out);
    std::size_t done = 0U;
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::milliseconds(timeout_ms);
    while (done < bytes) {
        const auto now = std::chrono::steady_clock::now();
        if (now >= deadline) return false;
        const int remain = static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline - now).count());
        struct pollfd pfd{fd, POLLIN, 0};
        if (poll(&pfd, 1, remain) <= 0) return false;
        const ssize_t n = read(fd, p + done, bytes - done);
        if (n > 0) done += static_cast<std::size_t>(n);
        else if (n < 0 && errno != EINTR) return false;
    }
    return true;
}

} // namespace

int main(int argc, char** argv)
{
    std::string root;
    uint32_t mode = 9U;
    uint32_t look_side = 0xFFU;
    int timeout_ms = 120000;
    int result_count = 1;
    uint32_t toggle_point_num = 0U;
    uint32_t restore_point_num = 0U;
    int standby_after_ms = -1;
    int resume_after_ms = -1;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--pipe-root" && i + 1 < argc) root = argv[++i];
        else if (a == "--mode" && i + 1 < argc) mode = static_cast<uint32_t>(std::stoul(argv[++i]));
        else if (a == "--timeout-ms" && i + 1 < argc) timeout_ms = std::stoi(argv[++i]);
        else if (a == "--look-side" && i + 1 < argc) look_side = static_cast<uint32_t>(std::stoul(argv[++i]));
        else if (a == "--result-count" && i + 1 < argc) result_count = std::stoi(argv[++i]);
        else if (a == "--toggle-point-num" && i + 1 < argc) toggle_point_num = static_cast<uint32_t>(std::stoul(argv[++i]));
        else if (a == "--restore-point-num" && i + 1 < argc) restore_point_num = static_cast<uint32_t>(std::stoul(argv[++i]));
        else if (a == "--standby-after-ms" && i + 1 < argc) standby_after_ms = std::stoi(argv[++i]);
        else if (a == "--resume-after-ms" && i + 1 < argc) resume_after_ms = std::stoi(argv[++i]);
        else {
            std::cerr << "Usage: " << argv[0]
                      << " --pipe-root DIR [--mode 7|9] [--look-side 0|255]"
                      << " [--result-count N] [--timeout-ms N]"
                      << " [--toggle-point-num N --restore-point-num N]"
                      << " [--standby-after-ms N --resume-after-ms N]\n";
            return 2;
        }
    }
    if (root.empty() || standby_after_ms < -1 || resume_after_ms < -1 ||
        ((standby_after_ms >= 0) != (resume_after_ms >= 0)) ||
        (standby_after_ms >= 0 && toggle_point_num > 0U)) {
        return 2;
    }

    const std::string cmd_path = pipePath(root, "pipewagmticmd");
    const std::string result_path = pipePath(root, "pipewagmtiimage");
    const int cmd_fd = open(cmd_path.c_str(), O_WRONLY);
    const int result_fd = open(result_path.c_str(), O_RDONLY | O_NONBLOCK);
    if (cmd_fd < 0 || result_fd < 0) {
        std::cerr << "[PIPE-CLIENT][ERR] open failed: " << std::strerror(errno) << std::endl;
        if (cmd_fd >= 0) close(cmd_fd);
        if (result_fd >= 0) close(result_fd);
        return 1;
    }

    ModeSwitchCmd cmd{};
    cmd.head = 0x5F5F5F5FU;
    cmd.algoType = 0xBBU;
    cmd.dataLen = static_cast<uint32_t>(sizeof(cmd) - kProtocolV15CommandOverhead);
    cmd.workMode = mode;
    cmd.LookSide = look_side;
    cmd.tail = 0xFCFCFCFCU;
    ssize_t sent = 0;
    if (standby_after_ms >= 0) {
        sent = write(cmd_fd, &cmd, sizeof(cmd));
        if (sent == static_cast<ssize_t>(sizeof(cmd))) {
            std::cout << "[PIPE-CLIENT] sent initial WAGMTI command" << std::endl;
            usleep(static_cast<useconds_t>(standby_after_ms) * 1000U);
            cmd.workMode = Mode_INIT;
            sent = write(cmd_fd, &cmd, sizeof(cmd));
        }
        if (sent == static_cast<ssize_t>(sizeof(cmd))) {
            std::cout << "[PIPE-CLIENT] sent standby command after "
                      << standby_after_ms << " ms" << std::endl;
            usleep(static_cast<useconds_t>(resume_after_ms) * 1000U);
            cmd.workMode = mode;
            sent = write(cmd_fd, &cmd, sizeof(cmd));
            std::cout << "[PIPE-CLIENT] sent WAGMTI resume after another "
                      << resume_after_ms << " ms" << std::endl;
        }
    } else if (toggle_point_num > 0U) {
        if (restore_point_num == 0U) {
            std::cerr << "[PIPE-CLIENT][ERR] restore point count is required\n";
            close(cmd_fd);
            close(result_fd);
            return 2;
        }
        cmd.updatePointNum = toggle_point_num;
        sent = write(cmd_fd, &cmd, sizeof(cmd));
        if (sent == static_cast<ssize_t>(sizeof(cmd))) {
            std::cout << "[PIPE-CLIENT] sent layout toggle updatePointNum="
                      << toggle_point_num << std::endl;
            usleep(100000);
            cmd.updatePointNum = restore_point_num;
            sent = write(cmd_fd, &cmd, sizeof(cmd));
            std::cout << "[PIPE-CLIENT] sent layout restore updatePointNum="
                      << restore_point_num << std::endl;
        }
    } else {
        sent = write(cmd_fd, &cmd, sizeof(cmd));
    }
    close(cmd_fd);
    if (sent != static_cast<ssize_t>(sizeof(cmd))) {
        std::cerr << "[PIPE-CLIENT][ERR] command write failed" << std::endl;
        close(result_fd);
        return 1;
    }
    std::cout << "[PIPE-CLIENT] sent ModeSwitchCmd bytes=" << sent
              << " workMode=" << mode << std::endl;

    bool all_ok = result_count > 0;
    for (int result_index = 0; result_index < result_count; ++result_index) {
        ResultHeader header{};
        if (!readExact(result_fd, &header, sizeof(header), timeout_ms)) {
            std::cerr << "[PIPE-CLIENT][ERR] result header timeout/short read index="
                      << result_index << std::endl;
            all_ok = false;
            break;
        }
        const std::size_t total = static_cast<std::size_t>(header.msgLen) + 3U;
        if (total < sizeof(header) || total > 64U * 1024U * 1024U) {
            std::cerr << "[PIPE-CLIENT][ERR] invalid result length " << total << std::endl;
            all_ok = false;
            break;
        }
        std::vector<uint8_t> tail(total - sizeof(header));
        if (!tail.empty() && !readExact(result_fd, tail.data(), tail.size(), timeout_ms)) {
            std::cerr << "[PIPE-CLIENT][ERR] result body timeout/short read index="
                      << result_index << std::endl;
            all_ok = false;
            break;
        }
        std::cout << "[PIPE-CLIENT] result_index=" << result_index
                  << " result_bytes=" << total
                  << " cmdType=" << static_cast<unsigned>(header.cmdType)
                  << " targetNum=" << header.targetNum
                  << " image=" << header.height << 'x' << header.width
                  << " availFlag=0x" << std::hex << header.availFlag << std::dec
                  << " checksum=" << static_cast<unsigned>(header.checksum)
                  << std::endl;
        const std::size_t target_bytes =
            static_cast<std::size_t>(header.targetNum) * 35U;
        if (header.head != 0xAA55U ||
            total < sizeof(header) + target_bytes ||
            header.cmdType != static_cast<uint8_t>(Mode_WAGMTI)) {
            all_ok = false;
        }
        uint8_t checksum = 0U;
        const uint8_t* header_bytes = reinterpret_cast<const uint8_t*>(&header);
        for (std::size_t i = 2U; i < 169U; ++i) {
            checksum = static_cast<uint8_t>(checksum + header_bytes[i]);
        }
        if (checksum != header.checksum) {
            std::cerr << "[PIPE-CLIENT][ERR] result header checksum mismatch"
                      << " expected=" << static_cast<unsigned>(checksum)
                      << " actual=" << static_cast<unsigned>(header.checksum)
                      << std::endl;
            all_ok = false;
        }
    }
    close(result_fd);
    return all_ok ? 0 : 1;
}
