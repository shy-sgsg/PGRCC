#include "beam_visibility.h"

#include <cmath>
#include <iostream>
#include <vector>

int main()
{
    using gmti::target_injection::RadarConfig;
    using gmti::target_injection::TargetGlobalConfig;
    using gmti::target_injection::evaluateVisibility;

    int failed = 0;
    const auto check = [&failed](bool condition, const char* name) {
        std::cout << (condition ? "[PASS] " : "[FAIL] ") << name << '\n';
        if (!condition) ++failed;
    };

    RadarConfig radar;
    radar.beam_width_deg = 2.28;
    radar.scan_step_deg = 2.0;

    TargetGlobalConfig hard_gate;
    hard_gate.visibility_mode = "hard_gate";
    int hard_gate_visible = 0;
    for (double angle_error_deg : std::vector<double>{-2.0, 0.0, 2.0}) {
        hard_gate_visible += evaluateVisibility(
            radar, hard_gate, angle_error_deg).visible ? 1 : 0;
    }
    check(hard_gate_visible == 1,
          "hard gate: beam-centred target is visible in one of adjacent 2-degree beams");
    check(evaluateVisibility(radar, hard_gate, -1.0).visible &&
          evaluateVisibility(radar, hard_gate, 1.0).visible,
          "hard gate: 2.28-degree beamwidth has a 0.28-degree adjacent-beam overlap");
    check(!evaluateVisibility(radar, hard_gate, 1.140001).visible,
          "hard gate: outside half beamwidth is not visible");

    TargetGlobalConfig gaussian;
    gaussian.visibility_mode = "gaussian";
    gaussian.beam_gain_threshold = 0.05;
    int gaussian_visible = 0;
    double adjacent_gain = 0.0;
    for (double angle_error_deg : std::vector<double>{-2.0, 0.0, 2.0}) {
        const auto result = evaluateVisibility(radar, gaussian, angle_error_deg);
        gaussian_visible += result.visible ? 1 : 0;
        if (std::fabs(angle_error_deg) > 1.0) adjacent_gain = result.beam_gain;
    }
    check(gaussian_visible == 3,
          "gaussian 0.05 support threshold includes both adjacent weak tails");
    check(adjacent_gain > 0.11 && adjacent_gain < 0.13,
          "gaussian adjacent-beam gain is approximately 0.118");

    std::cout << "[BEAM_VISIBILITY_SELFTEST] failed=" << failed << '\n';
    return failed == 0 ? 0 : 1;
}
