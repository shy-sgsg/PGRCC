#include "gmti_hwbind/binding.h"

#include "gmti_hwbind/binding_config.h"
#include "sm3.h"

#include <algorithm>
#include <cctype>
#include <stdexcept>
#include <utility>

namespace gmti::hwbind {
namespace {

std::string trim(const std::string& input) {
    const auto begin = input.find_first_not_of(" \t\r\n");
    if (begin == std::string::npos) {
        return "";
    }
    const auto end = input.find_last_not_of(" \t\r\n");
    return input.substr(begin, end - begin + 1);
}

std::string normalize_hex(std::string input) {
    std::transform(
        input.begin(), input.end(), input.begin(),
        [](unsigned char value) {
            return static_cast<char>(std::tolower(value));
        });
    return input;
}

bool valid_digest(const std::string& digest) {
    return digest.size() == 64u &&
           std::all_of(
               digest.begin(), digest.end(),
               [](unsigned char value) {
                   return std::isxdigit(value) != 0;
               });
}

bool constant_time_equal(const std::string& lhs,
                         const std::string& rhs) {
    if (lhs.size() != rhs.size()) {
        return false;
    }
    unsigned char difference = 0;
    for (std::size_t i = 0; i < lhs.size(); ++i) {
        difference |=
            static_cast<unsigned char>(lhs[i] ^ rhs[i]);
    }
    return difference == 0;
}

std::string require_component(const char* name,
                              const std::string& value) {
    const std::string normalized = trim(value);
    if (normalized.empty()) {
        throw std::runtime_error(
            std::string("missing hardware identity field: ") + name);
    }
    return normalized;
}

CheckResult compute_check_result(
    const std::string& software_type,
    const std::string& software_version,
    const std::string& expected_digest,
    std::string checkpoint) {
    CheckResult result;
    result.checkpoint = std::move(checkpoint);
    result.expected_digest_hex = normalize_hex(expected_digest);
    result.configured = valid_digest(result.expected_digest_hex);
    if (!result.configured) {
        return result;
    }

    result.fingerprint = collect_fingerprint_profile();
    result.actual_digest_hex =
        digest_hex_for_profile(result.fingerprint,
                               software_type,
                               software_version);
    result.matched =
        constant_time_equal(result.actual_digest_hex,
                            result.expected_digest_hex);
    return result;
}

} // namespace

FingerprintProfile build_fingerprint_profile(
    const platform::HardwareInfo& hardware) {
    FingerprintProfile profile;
    profile.hardware = hardware;
    profile.raw_serialized_hardware =
        platform::serialize(hardware);

    const std::string cpu =
        require_component("cpu.serial", hardware.cpu_serial);
    const std::string disk =
        require_component("disk.serial", hardware.disk_serial);
    const std::string machine =
        require_component("system.machine_id", hardware.machine_id);

    profile.canonical_hardware =
        "schema=hwbind_v3|cpu=" + cpu +
        "|disk=" + disk +
        "|machine_id=" + machine;
    return profile;
}

FingerprintProfile collect_fingerprint_profile() {
    return build_fingerprint_profile(
        platform::collect_hardware_info());
}

std::string build_binding_material(
    const std::string& canonical_hardware,
    const std::string& software_type,
    const std::string& software_version) {
    return canonical_hardware + "|" +
           software_type + "|" +
           software_version;
}

std::string digest_hex_for_binding_hardware(
    const std::string& canonical_hardware,
    const std::string& software_type,
    const std::string& software_version) {
    return crypto::sm3_hex(
        build_binding_material(canonical_hardware,
                               software_type,
                               software_version));
}

std::string digest_hex_for_profile(
    const FingerprintProfile& profile,
    const std::string& software_type,
    const std::string& software_version) {
    return digest_hex_for_binding_hardware(
        profile.canonical_hardware,
        software_type,
        software_version);
}

bool configuration_is_valid() {
    return valid_digest(
        normalize_hex(config::kExpectedSm3Hex));
}

RuntimeVerifier::RuntimeVerifier(
    std::string software_type,
    std::string software_version,
    std::string expected_digest_hex)
    : software_type_(std::move(software_type)),
      software_version_(std::move(software_version)),
      expected_digest_hex_(
          normalize_hex(std::move(expected_digest_hex))) {}

CheckResult RuntimeVerifier::verify(bool force_refresh,
                                    std::string checkpoint) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!force_refresh && has_cached_result_) {
        cached_result_.from_cache = true;
        if (!checkpoint.empty()) {
            cached_result_.checkpoint = std::move(checkpoint);
        }
        return cached_result_;
    }

    cached_result_ =
        compute_check_result(software_type_,
                             software_version_,
                             expected_digest_hex_,
                             std::move(checkpoint));
    cached_result_.from_cache = false;
    has_cached_result_ = true;
    return cached_result_;
}

RuntimeVerifier& default_verifier() {
    static RuntimeVerifier verifier(
        config::kSoftwareType,
        config::kSoftwareVersion,
        config::kExpectedSm3Hex);
    return verifier;
}

CheckResult verify_current_machine(bool force_refresh,
                                   std::string checkpoint) {
    return default_verifier().verify(
        force_refresh, std::move(checkpoint));
}

} // namespace gmti::hwbind

