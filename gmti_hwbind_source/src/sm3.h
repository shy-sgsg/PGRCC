#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace gmti::crypto {

class Sm3 {
public:
    static constexpr std::size_t kDigestSize = 32;

    Sm3();

    void update(const std::uint8_t* data, std::size_t length);
    void final(std::uint8_t digest[kDigestSize]);

    static void hash(const std::uint8_t* data,
                     std::size_t length,
                     std::uint8_t digest[kDigestSize]);
    static std::vector<std::uint8_t> hash(
        const std::vector<std::uint8_t>& data);

private:
    void reset();
    void compress(const std::uint8_t block[64]);

    std::uint32_t state_[8]{};
    std::uint8_t buffer_[64]{};
    std::uint64_t total_bits_ = 0;
    std::size_t buffer_length_ = 0;
};

std::string sm3_hex(const std::string& input);

} // namespace gmti::crypto

