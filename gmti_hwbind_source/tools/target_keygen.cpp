#include "gmti_hwbind/binding.h"
#include "gmti_hwbind/binding_config.h"

#include <exception>
#include <iostream>
#include <string>

namespace {

void print_usage(const char* program) {
    std::cout
        << "GMTI target hardware digest generator\n"
        << "Usage:\n"
        << "  " << program
        << " [--soft-ver <version>]\n"
        << "\n"
        << "Default version: "
        << gmti::hwbind::config::kSoftwareVersion << "\n";
}

std::string argument_value(int argc,
                           char* argv[],
                           const std::string& name) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (argv[i] == name) {
            return argv[i + 1];
        }
    }
    return "";
}

bool has_flag(int argc,
              char* argv[],
              const std::string& name) {
    for (int i = 1; i < argc; ++i) {
        if (argv[i] == name) {
            return true;
        }
    }
    return false;
}

bool arguments_are_valid(int argc, char* argv[]) {
    for (int i = 1; i < argc; ++i) {
        const std::string argument = argv[i];
        if (argument == "--soft-ver") {
            if (i + 1 >= argc) {
                return false;
            }
            ++i;
        } else if (argument != "--help" && argument != "-h") {
            return false;
        }
    }
    return true;
}

} // namespace

int main(int argc, char* argv[]) {
    if (!arguments_are_valid(argc, argv)) {
        print_usage(argv[0]);
        return 1;
    }
    if (has_flag(argc, argv, "--help") ||
        has_flag(argc, argv, "-h")) {
        print_usage(argv[0]);
        return 0;
    }

    std::string software_version =
        argument_value(argc, argv, "--soft-ver");
    if (software_version.empty()) {
        software_version =
            gmti::hwbind::config::kSoftwareVersion;
    }

    try {
        const gmti::hwbind::FingerprintProfile profile =
            gmti::hwbind::collect_fingerprint_profile();
        const std::string digest =
            gmti::hwbind::digest_hex_for_profile(
                profile,
                gmti::hwbind::config::kSoftwareType,
                software_version);

        std::cout << "GMTI target hardware digest\n";
        std::cout << "software_type     : "
                  << gmti::hwbind::config::kSoftwareType << "\n";
        std::cout << "software_version  : "
                  << software_version << "\n";
        std::cout << "raw_hardware      : "
                  << profile.raw_serialized_hardware << "\n";
        std::cout << "canonical_hardware: "
                  << profile.canonical_hardware << "\n";
        std::cout << "target_sm3        : "
                  << digest << "\n\n";
        std::cout << "Copy into include/gmti_hwbind/binding_config.h:\n";
        std::cout << "inline constexpr const char* kSoftwareType = \""
                  << gmti::hwbind::config::kSoftwareType
                  << "\";\n";
        std::cout << "inline constexpr const char* kSoftwareVersion = \""
                  << software_version << "\";\n";
        std::cout << "inline constexpr const char* kExpectedSm3Hex = \""
                  << digest << "\";\n";
    } catch (const std::exception& error) {
        std::cerr << "generation failed: "
                  << error.what() << "\n";
        return 1;
    }
    return 0;
}

