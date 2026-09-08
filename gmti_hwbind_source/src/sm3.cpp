#include "sm3.h"

#include <cstring>
#include <iomanip>
#include <sstream>

namespace gmti::crypto {
namespace {

constexpr std::uint32_t kInitialState[8] = {
    0x7380166f, 0x4914b2b9, 0x172442d7, 0xda8a0600,
    0xa96f30bc, 0x163138aa, 0xe38dee4d, 0xb0fb0e4e,
};

std::uint32_t rotate_left(std::uint32_t value, unsigned int shift) {
    shift &= 31u;
    if (shift == 0u) {
        return value;
    }
    return (value << shift) | (value >> (32u - shift));
}

std::uint32_t permutation0(std::uint32_t value) {
    return value ^ rotate_left(value, 9u) ^ rotate_left(value, 17u);
}

std::uint32_t permutation1(std::uint32_t value) {
    return value ^ rotate_left(value, 15u) ^ rotate_left(value, 23u);
}

std::uint32_t boolean_ff(std::uint32_t x,
                         std::uint32_t y,
                         std::uint32_t z,
                         int round) {
    return round < 16 ? (x ^ y ^ z)
                      : ((x & y) | (x & z) | (y & z));
}

std::uint32_t boolean_gg(std::uint32_t x,
                         std::uint32_t y,
                         std::uint32_t z,
                         int round) {
    return round < 16 ? (x ^ y ^ z)
                      : ((x & y) | (~x & z));
}

std::uint32_t load_be32(const std::uint8_t* input) {
    return (static_cast<std::uint32_t>(input[0]) << 24u) |
           (static_cast<std::uint32_t>(input[1]) << 16u) |
           (static_cast<std::uint32_t>(input[2]) << 8u) |
           static_cast<std::uint32_t>(input[3]);
}

void store_be32(std::uint8_t* output, std::uint32_t value) {
    output[0] = static_cast<std::uint8_t>(value >> 24u);
    output[1] = static_cast<std::uint8_t>(value >> 16u);
    output[2] = static_cast<std::uint8_t>(value >> 8u);
    output[3] = static_cast<std::uint8_t>(value);
}

} // namespace

Sm3::Sm3() {
    reset();
}

void Sm3::reset() {
    std::memcpy(state_, kInitialState, sizeof(kInitialState));
    std::memset(buffer_, 0, sizeof(buffer_));
    total_bits_ = 0;
    buffer_length_ = 0;
}

void Sm3::compress(const std::uint8_t block[64]) {
    std::uint32_t words[68]{};
    std::uint32_t derived_words[64]{};

    for (int i = 0; i < 16; ++i) {
        words[i] = load_be32(block + i * 4);
    }
    for (int i = 16; i < 68; ++i) {
        words[i] =
            permutation1(words[i - 16] ^ words[i - 9] ^
                         rotate_left(words[i - 3], 15u)) ^
            rotate_left(words[i - 13], 7u) ^
            words[i - 6];
    }
    for (int i = 0; i < 64; ++i) {
        derived_words[i] = words[i] ^ words[i + 4];
    }

    std::uint32_t a = state_[0];
    std::uint32_t b = state_[1];
    std::uint32_t c = state_[2];
    std::uint32_t d = state_[3];
    std::uint32_t e = state_[4];
    std::uint32_t f = state_[5];
    std::uint32_t g = state_[6];
    std::uint32_t h = state_[7];

    for (int round = 0; round < 64; ++round) {
        const std::uint32_t round_constant =
            round < 16 ? 0x79cc4519u : 0x7a879d8au;
        const std::uint32_t ss1 =
            rotate_left(rotate_left(a, 12u) + e +
                        rotate_left(round_constant,
                                    static_cast<unsigned int>(round)),
                        7u);
        const std::uint32_t ss2 = ss1 ^ rotate_left(a, 12u);
        const std::uint32_t tt1 =
            boolean_ff(a, b, c, round) + d + ss2 +
            derived_words[round];
        const std::uint32_t tt2 =
            boolean_gg(e, f, g, round) + h + ss1 +
            words[round];

        d = c;
        c = rotate_left(b, 9u);
        b = a;
        a = tt1;
        h = g;
        g = rotate_left(f, 19u);
        f = e;
        e = permutation0(tt2);
    }

    state_[0] ^= a;
    state_[1] ^= b;
    state_[2] ^= c;
    state_[3] ^= d;
    state_[4] ^= e;
    state_[5] ^= f;
    state_[6] ^= g;
    state_[7] ^= h;
}

void Sm3::update(const std::uint8_t* data, std::size_t length) {
    total_bits_ += static_cast<std::uint64_t>(length) * 8u;
    std::size_t offset = 0;

    if (buffer_length_ != 0u) {
        const std::size_t needed = 64u - buffer_length_;
        if (length < needed) {
            std::memcpy(buffer_ + buffer_length_, data, length);
            buffer_length_ += length;
            return;
        }
        std::memcpy(buffer_ + buffer_length_, data, needed);
        compress(buffer_);
        buffer_length_ = 0;
        offset = needed;
    }

    while (offset + 64u <= length) {
        compress(data + offset);
        offset += 64u;
    }

    if (offset < length) {
        buffer_length_ = length - offset;
        std::memcpy(buffer_, data + offset, buffer_length_);
    }
}

void Sm3::final(std::uint8_t digest[kDigestSize]) {
    buffer_[buffer_length_++] = 0x80u;
    if (buffer_length_ > 56u) {
        std::memset(buffer_ + buffer_length_, 0,
                    64u - buffer_length_);
        compress(buffer_);
        buffer_length_ = 0;
    }

    std::memset(buffer_ + buffer_length_, 0,
                56u - buffer_length_);
    for (int i = 7; i >= 0; --i) {
        buffer_[56 + (7 - i)] =
            static_cast<std::uint8_t>(total_bits_ >> (i * 8));
    }
    compress(buffer_);

    for (int i = 0; i < 8; ++i) {
        store_be32(digest + i * 4, state_[i]);
    }
    reset();
}

void Sm3::hash(const std::uint8_t* data,
               std::size_t length,
               std::uint8_t digest[kDigestSize]) {
    Sm3 hasher;
    hasher.update(data, length);
    hasher.final(digest);
}

std::vector<std::uint8_t> Sm3::hash(
    const std::vector<std::uint8_t>& data) {
    std::vector<std::uint8_t> digest(kDigestSize);
    hash(data.data(), data.size(), digest.data());
    return digest;
}

std::string sm3_hex(const std::string& input) {
    std::uint8_t digest[Sm3::kDigestSize]{};
    Sm3::hash(reinterpret_cast<const std::uint8_t*>(input.data()),
              input.size(),
              digest);

    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (std::uint8_t byte : digest) {
        output << std::setw(2)
               << static_cast<unsigned int>(byte);
    }
    return output.str();
}

} // namespace gmti::crypto

