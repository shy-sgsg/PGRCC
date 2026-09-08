#pragma once

#include "gmti_hwbind/hardware.h"

#include <mutex>
#include <string>

namespace gmti::hwbind {

struct FingerprintProfile {
    platform::HardwareInfo hardware;
    std::string raw_serialized_hardware;
    std::string canonical_hardware;
};

struct CheckResult {
    bool configured = false;
    bool matched = false;
    bool from_cache = false;
    std::string checkpoint;
    FingerprintProfile fingerprint;
    std::string actual_digest_hex;
    std::string expected_digest_hex;
};

FingerprintProfile build_fingerprint_profile(
    const platform::HardwareInfo& hardware);
FingerprintProfile collect_fingerprint_profile();

std::string build_binding_material(const std::string& canonical_hardware,
                                   const std::string& software_type,
                                   const std::string& software_version);

std::string digest_hex_for_binding_hardware(
    const std::string& canonical_hardware,
    const std::string& software_type,
    const std::string& software_version);

std::string digest_hex_for_profile(const FingerprintProfile& profile,
                                   const std::string& software_type,
                                   const std::string& software_version);

bool configuration_is_valid();

class RuntimeVerifier {
public:
    RuntimeVerifier(std::string software_type,
                    std::string software_version,
                    std::string expected_digest_hex);

    RuntimeVerifier(const RuntimeVerifier&) = delete;
    RuntimeVerifier& operator=(const RuntimeVerifier&) = delete;

    CheckResult verify(bool force_refresh = false,
                       std::string checkpoint = {});

private:
    std::string software_type_;
    std::string software_version_;
    std::string expected_digest_hex_;
    bool has_cached_result_ = false;
    CheckResult cached_result_;
    std::mutex mutex_;
};

RuntimeVerifier& default_verifier();

CheckResult verify_current_machine(bool force_refresh = false,
                                   std::string checkpoint = {});

} // namespace gmti::hwbind

