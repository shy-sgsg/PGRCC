#pragma once

#include "gmti_hwbind/security_gate.h"

#include <array>

namespace gmti::security::detail {

struct DecodedCheckpoint {
    std::array<char, 32> text{};

    const char* c_str() const noexcept {
        return text.data();
    }
};

DecodedCheckpoint decode_checkpoint(CheckpointId id);

} // namespace gmti::security::detail

