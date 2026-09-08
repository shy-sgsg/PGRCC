#pragma once

#include <cstdint>

namespace gmti::security {

// The numeric order is part of the flow-integrity check.
enum class CheckpointId : std::uint8_t {
    Initialization = 0,
    Check1 = 1,
    Check2 = 2,
    Check3 = 3,
    Check4 = 4,
    Check5 = 5,
    Check6 = 6,
};

inline constexpr std::uint32_t kAllCheckpointMask = 0x7fu;
inline constexpr std::uint32_t kFinalRefreshMask = 0x40u;

constexpr std::uint8_t checkpoint_order(CheckpointId id) noexcept {
    return static_cast<std::uint8_t>(id);
}

constexpr std::uint32_t checkpoint_bit(CheckpointId id) noexcept {
    return 1u << checkpoint_order(id);
}

constexpr bool checkpoint_requires_refresh(CheckpointId id) noexcept {
    return id == CheckpointId::Check6;
}

enum class GateCode {
    Ok = 0,
    NotConfigured,
    HardwareMismatch,
    BadFlow,
};

struct GateSnapshot {
    std::uint32_t seen_mask = 0;
    std::uint32_t pass_mask = 0;
    std::uint32_t fail_mask = 0;
    std::uint32_t refresh_mask = 0;
    std::uint8_t highest_order = 0;
    bool has_checkpoint = false;
    bool flow_error = false;
    GateCode last_code = GateCode::Ok;
};

// Call once before each GMTI processing flow.
void reset_gate();

// Records one checkpoint. It does not terminate the business flow.
GateCode checkpoint(CheckpointId id);

// Default policy requires all seven checkpoints and a refresh at Check6.
bool final_decision(std::uint32_t required_mask = kAllCheckpointMask,
                    std::uint32_t required_refresh_mask = kFinalRefreshMask);

GateSnapshot gate_snapshot();
const char* gate_code_to_string(GateCode code) noexcept;

} // namespace gmti::security

