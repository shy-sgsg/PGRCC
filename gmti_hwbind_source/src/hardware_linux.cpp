#include "gmti_hwbind/hardware.h"

#include <cstdio>
#include <fstream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace gmti::platform {
namespace {

struct DetectedValue {
    std::string value;
    std::string source;
};

std::string trim(const std::string& input) {
    const auto begin = input.find_first_not_of(" \t\r\n");
    if (begin == std::string::npos) {
        return "";
    }
    const auto end = input.find_last_not_of(" \t\r\n");
    return input.substr(begin, end - begin + 1);
}

std::string read_first_line(const std::string& path) {
    std::ifstream input(path);
    if (!input) {
        return "";
    }
    std::string line;
    std::getline(input, line);
    return trim(line);
}

std::string run_fixed_command(const char* command) {
    FILE* pipe = popen(command, "r");
    if (pipe == nullptr) {
        return "";
    }

    char buffer[256]{};
    std::string output;
    while (std::fgets(buffer, sizeof(buffer), pipe) != nullptr) {
        output += buffer;
    }
    pclose(pipe);
    return output;
}

std::vector<std::string> nonempty_lines(const std::string& input) {
    std::vector<std::string> lines;
    std::istringstream stream(input);
    std::string line;
    while (std::getline(stream, line)) {
        line = trim(line);
        if (!line.empty()) {
            lines.push_back(line);
        }
    }
    return lines;
}

std::string join(const std::vector<std::string>& values,
                 const char* separator) {
    std::ostringstream output;
    for (std::size_t i = 0; i < values.size(); ++i) {
        if (i != 0u) {
            output << separator;
        }
        output << values[i];
    }
    return output.str();
}

DetectedValue detect_cpu_serial() {
    std::ifstream input("/proc/cpuinfo");
    if (!input) {
        return {"", "/proc/cpuinfo"};
    }

    std::string line;
    while (std::getline(input, line)) {
        if (line.find("Serial") == std::string::npos) {
            continue;
        }
        const auto separator = line.find(':');
        if (separator == std::string::npos) {
            continue;
        }
        const std::string serial = trim(line.substr(separator + 1));
        if (!serial.empty()) {
            return {serial, "/proc/cpuinfo:Serial"};
        }
    }
    return {"", "/proc/cpuinfo:Serial"};
}

DetectedValue detect_disk_serials() {
    const std::vector<std::string> lines =
        nonempty_lines(run_fixed_command(
            "lsblk -d -n -o SERIAL 2>/dev/null"));
    const std::set<std::string> unique(lines.begin(), lines.end());
    const std::vector<std::string> sorted(
        unique.begin(), unique.end());
    return {join(sorted, ","), "lsblk -d -n -o SERIAL"};
}

DetectedValue detect_machine_id() {
    return {read_first_line("/etc/machine-id"),
            "/etc/machine-id"};
}

void require_value(const DetectedValue& detected,
                   const char* field_name) {
    if (detected.value.empty()) {
        throw std::runtime_error(
            std::string("missing hardware identity field: ") +
            field_name + " (" + detected.source + ")");
    }
}

std::string field_value(const std::string& serialized,
                        const std::string& key) {
    const std::string prefix = key + "=";
    const auto position = serialized.find(prefix);
    if (position == std::string::npos) {
        return "";
    }
    const auto value_begin = position + prefix.size();
    const auto value_end = serialized.find('|', value_begin);
    return serialized.substr(
        value_begin,
        value_end == std::string::npos
            ? std::string::npos
            : value_end - value_begin);
}

} // namespace

HardwareInfo collect_hardware_info() {
    const DetectedValue cpu = detect_cpu_serial();
    const DetectedValue disk = detect_disk_serials();
    const DetectedValue machine = detect_machine_id();

    require_value(cpu, "cpu.serial");
    require_value(disk, "disk.serial");
    require_value(machine, "system.machine_id");

    HardwareInfo hardware;
    hardware.cpu_serial = cpu.value;
    hardware.disk_serial = disk.value;
    hardware.machine_id = machine.value;
    hardware.cpu_source = cpu.source;
    hardware.disk_source = disk.source;
    hardware.machine_id_source = machine.source;
    return hardware;
}

std::string serialize(const HardwareInfo& hardware) {
    return "cpu=" + hardware.cpu_serial +
           "|disk=" + hardware.disk_serial +
           "|machine_id=" + hardware.machine_id;
}

HardwareInfo deserialize(const std::string& serialized) {
    HardwareInfo hardware;
    hardware.cpu_serial = field_value(serialized, "cpu");
    hardware.disk_serial = field_value(serialized, "disk");
    hardware.machine_id = field_value(serialized, "machine_id");
    return hardware;
}

} // namespace gmti::platform

