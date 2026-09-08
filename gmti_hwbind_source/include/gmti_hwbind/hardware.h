#pragma once

#include <string>

namespace gmti::platform {

struct HardwareInfo {
    std::string cpu_serial;
    std::string disk_serial;
    std::string machine_id;
    std::string cpu_source;
    std::string disk_source;
    std::string machine_id_source;
};

// Strict Linux collector used by both target_keygen and runtime verification.
// Missing CPU serial, disk serial, or machine-id raises std::runtime_error.
HardwareInfo collect_hardware_info();

std::string serialize(const HardwareInfo& hardware);
HardwareInfo deserialize(const std::string& serialized);

} // namespace gmti::platform

