#include "checkpoint_codec.h"

#include <cstddef>
#include <cstdint>

namespace gmti::security::detail {
namespace {

// Volatile prevents the optimizer from replacing the decode operation with
// plaintext checkpoint string literals.
volatile std::uint8_t g_decode_key = 0x5au;

constexpr std::array<std::uint8_t, 15> kInitialization = {
    0x33, 0x34, 0x33, 0x2e, 0x33,
    0x3b, 0x36, 0x33, 0x20, 0x3b,
    0x2e, 0x33, 0x35, 0x34, 0x5a,
};

constexpr std::array<std::uint8_t, 7> kCheck1 = {
    0x39, 0x32, 0x3f, 0x39, 0x31, 0x6b, 0x5a,
};
constexpr std::array<std::uint8_t, 7> kCheck2 = {
    0x39, 0x32, 0x3f, 0x39, 0x31, 0x68, 0x5a,
};
constexpr std::array<std::uint8_t, 7> kCheck3 = {
    0x39, 0x32, 0x3f, 0x39, 0x31, 0x69, 0x5a,
};
constexpr std::array<std::uint8_t, 7> kCheck4 = {
    0x39, 0x32, 0x3f, 0x39, 0x31, 0x6e, 0x5a,
};
constexpr std::array<std::uint8_t, 7> kCheck5 = {
    0x39, 0x32, 0x3f, 0x39, 0x31, 0x6f, 0x5a,
};
constexpr std::array<std::uint8_t, 7> kCheck6 = {
    0x39, 0x32, 0x3f, 0x39, 0x31, 0x6c, 0x5a,
};

template <std::size_t Size>
DecodedCheckpoint decode(
    const std::array<std::uint8_t, Size>& encoded) {
    DecodedCheckpoint result;
    for (std::size_t i = 0;
         i < Size && i < result.text.size();
         ++i) {
        result.text[i] =
            static_cast<char>(encoded[i] ^ g_decode_key);
    }
    return result;
}

} // namespace

DecodedCheckpoint decode_checkpoint(CheckpointId id) {
    switch (id) {
        case CheckpointId::Initialization:
            return decode(kInitialization);
        case CheckpointId::Check1:
            return decode(kCheck1);
        case CheckpointId::Check2:
            return decode(kCheck2);
        case CheckpointId::Check3:
            return decode(kCheck3);
        case CheckpointId::Check4:
            return decode(kCheck4);
        case CheckpointId::Check5:
            return decode(kCheck5);
        case CheckpointId::Check6:
            return decode(kCheck6);
    }
    return {};
}

} // namespace gmti::security::detail

