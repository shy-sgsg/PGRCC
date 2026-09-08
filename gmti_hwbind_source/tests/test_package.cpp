#include "checkpoint_codec.h"
#include "gmti_hwbind/binding.h"
#include "gmti_hwbind/security_gate.h"
#include "sm3.h"

#include <iostream>
#include <string>

namespace {

int failures = 0;

void expect(bool condition, const char* description) {
    std::cout << (condition ? "[PASS] " : "[FAIL] ")
              << description << "\n";
    if (!condition) {
        ++failures;
    }
}

void test_sm3() {
    expect(
        gmti::crypto::sm3_hex("abc") ==
            "66c7f0f462eeedd9d1f2d46bdc10e4e"
            "24167c4875cf2f7a2297da02b8f4ba8e0",
        "SM3 official abc vector");

    std::string message;
    for (int i = 0; i < 16; ++i) {
        message += "abcd";
    }
    expect(
        gmti::crypto::sm3_hex(message) ==
            "debe9ff92275b8a138604889c18e5a4d"
            "6fdb70e5387e5765293dcba39c0c5732",
        "SM3 official 64-byte vector");
}

void test_binding_material() {
    gmti::platform::HardwareInfo hardware;
    hardware.cpu_serial = "CPU-001";
    hardware.disk_serial = "DISK-002";
    hardware.machine_id =
        "0123456789abcdef0123456789abcdef";

    const gmti::hwbind::FingerprintProfile profile =
        gmti::hwbind::build_fingerprint_profile(hardware);
    expect(
        profile.canonical_hardware ==
            "schema=hwbind_v3|cpu=CPU-001|disk=DISK-002|"
            "machine_id=0123456789abcdef0123456789abcdef",
        "canonical binding material");

    const std::string gmti_digest =
        gmti::hwbind::digest_hex_for_profile(
            profile, "gmti", "1.0.0");
    const std::string other_digest =
        gmti::hwbind::digest_hex_for_profile(
            profile, "mmti", "1.0.0");
    expect(gmti_digest.size() == 64u,
           "binding digest has 64 hex characters");
    expect(gmti_digest != other_digest,
           "software type separates product digests");
}

void test_invalid_configuration_fails_closed() {
    gmti::hwbind::RuntimeVerifier verifier(
        "gmti", "1.0.0", "");
    const gmti::hwbind::CheckResult first =
        verifier.verify(false, "test");
    const gmti::hwbind::CheckResult cached =
        verifier.verify(false, "test_cached");

    expect(!first.configured && !first.matched,
           "empty target digest fails closed");
    expect(cached.from_cache && !cached.matched,
           "failed verification result is cached");
}

void test_checkpoints() {
    using gmti::security::CheckpointId;
    const CheckpointId ids[] = {
        CheckpointId::Initialization,
        CheckpointId::Check1,
        CheckpointId::Check2,
        CheckpointId::Check3,
        CheckpointId::Check4,
        CheckpointId::Check5,
        CheckpointId::Check6,
    };
    const char* names[] = {
        "initialization",
        "check1",
        "check2",
        "check3",
        "check4",
        "check5",
        "check6",
    };

    std::uint32_t mask = 0;
    for (std::size_t i = 0; i < 7u; ++i) {
        const auto decoded =
            gmti::security::detail::decode_checkpoint(ids[i]);
        expect(std::string(decoded.c_str()) == names[i],
               "encoded checkpoint decodes correctly");
        mask |= gmti::security::checkpoint_bit(ids[i]);
    }

    expect(mask == gmti::security::kAllCheckpointMask,
           "seven checkpoint mask is 0x7f");
    expect(
        gmti::security::checkpoint_requires_refresh(
            CheckpointId::Check6),
        "Check6 requires hardware refresh");
    expect(
        !gmti::security::checkpoint_requires_refresh(
            CheckpointId::Check5),
        "Check5 reuses cached verification");
}

} // namespace

int main() {
    test_sm3();
    test_binding_material();
    test_invalid_configuration_fails_closed();
    test_checkpoints();

    std::cout << "failures: " << failures << "\n";
    return failures == 0 ? 0 : 1;
}
