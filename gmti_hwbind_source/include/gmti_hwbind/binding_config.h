#pragma once

namespace gmti::hwbind::config {

// Product and version are part of the SM3 input. Change the version only when
// a new independently authorized build is intended.
inline constexpr const char* kSoftwareType = "gmti";
inline constexpr const char* kSoftwareVersion = "1.0.0";

// Generate this value on the authorized target with gmti_target_keygen.
// An empty or malformed value fails closed.
inline constexpr const char* kExpectedSm3Hex = "";

} // namespace gmti::hwbind::config

