#include "mechanical_scan.hpp"

#include <cmath>
#include <iostream>
#include <string>
#include <vector>

namespace {

std::vector<PulseMeta> triangleScan(int forward_count, int reverse_count,
                                    double step_deg)
{
    std::vector<PulseMeta> out;
    std::uint32_t counter = 1000U;
    double utc = 50000.0;
    for (int i = 0; i < forward_count; ++i) {
        PulseMeta p;
        p.utc = utc;
        p.prt_counter = counter;
        p.servo_azimuth_deg = -20.0 + step_deg * static_cast<double>(i);
        p.servo_elevation_deg = -10.0;
        out.push_back(p);
        utc += 0.001;
        ++counter;
    }
    const double turn = out.empty() ? -20.0 : out.back().servo_azimuth_deg;
    for (int i = 1; i <= reverse_count; ++i) {
        PulseMeta p;
        p.utc = utc;
        p.prt_counter = counter;
        p.servo_azimuth_deg = turn - step_deg * static_cast<double>(i);
        p.servo_elevation_deg = -10.0;
        out.push_back(p);
        utc += 0.001;
        ++counter;
    }
    return out;
}

} // namespace

int main()
{
    int failures = 0;
    const auto check = [&failures](bool condition, const std::string& name) {
        std::cout << (condition ? "[PASS] " : "[FAIL] ") << name << '\n';
        if (!condition) ++failures;
    };

    MechanicalScanConfig cfg;
    cfg.cpi_pulse_count = 10;
    cfg.cpi_step_pulse = 10;
    cfg.max_cpi_angle_span_deg = 1.0;
    cfg.scan_direction_deadband_deg = 0.005;
    cfg.scan_direction_confirm_pulses = 3;
    const std::vector<PulseMeta> meta = triangleScan(25, 25, 0.02);
    MechanicalScanRuntime runtime;
    std::string error;
    check(makeMechanicalCpiList(meta, cfg, runtime, &error),
          "forward/reverse continuous scan produces valid CPI windows: " + error);
    check(runtime.processing_window_indices.size() == 4U,
          "non-overlap CPI count is computed independently inside each scan");
    if (runtime.processing_window_indices.size() == 4U) {
        const MechanicalCpiWindow& f = runtime.windows[
            static_cast<std::size_t>(runtime.processing_window_indices.front())];
        const MechanicalCpiWindow& r = runtime.windows[
            static_cast<std::size_t>(runtime.processing_window_indices.back())];
        check(f.scan_id == 0 && f.window_id == 0 && f.scan_direction == 1 &&
              f.pulse_start == 0U && f.pulse_count == 10U,
              "forward CPI keeps physical pulse range and direction");
        check(r.scan_id == 1 && r.scan_direction == -1 && r.pulse_count == 10U,
              "stable sign reversal starts a new reverse scan");
        check(std::abs(f.az_center_deg - meta[5].servo_azimuth_deg) < 1.0e-12,
              "CPI reference angle is the actual centre PRT servo angle");
    }

    MechanicalScanConfig overlap_cfg = cfg;
    overlap_cfg.cpi_step_pulse = 5;
    MechanicalScanRuntime overlap;
    error.clear();
    check(makeMechanicalCpiList(meta, overlap_cfg, overlap, &error) &&
          overlap.processing_window_indices.size() == 8U,
          "50 percent overlap is represented by pulse step, not virtual beams");

    std::vector<PulseMeta> noisy = meta;
    noisy[5].servo_azimuth_deg -= 0.004;
    noisy[6].servo_azimuth_deg += 0.004;
    MechanicalScanRuntime noisy_runtime;
    error.clear();
    check(makeMechanicalCpiList(noisy, cfg, noisy_runtime, &error) &&
          noisy_runtime.processing_window_indices.size() == 4U,
          "sub-deadband angle jitter does not create extra scans");

    MechanicalScanConfig slow_cfg = cfg;
    slow_cfg.scan_direction_deadband_deg = 0.05;
    std::vector<PulseMeta> slow;
    for (int i = 0; i < 20; ++i) {
        PulseMeta p;
        p.utc = 100.0 + 0.001 * i;
        p.prt_counter = static_cast<std::uint32_t>(i);
        p.servo_azimuth_deg = 0.02 * i;
        slow.push_back(p);
    }
    MechanicalScanRuntime slow_runtime;
    error.clear();
    check(makeMechanicalCpiList(slow, slow_cfg, slow_runtime, &error) &&
          slow_runtime.processing_window_indices.size() == 2U &&
          slow_runtime.windows.front().scan_direction == 1,
          "cumulative direction detection handles per-PRT motion below deadband");

    MechanicalScanConfig tight = cfg;
    tight.max_cpi_angle_span_deg = 0.1;
    MechanicalScanRuntime rejected;
    error.clear();
    check(!makeMechanicalCpiList(meta, tight, rejected, &error) &&
          rejected.processing_window_indices.empty(),
          "CPI angle-span gate rejects every excessive-span window");
    bool saw_span_reason = false;
    for (const MechanicalCpiWindow& w : rejected.windows) {
        saw_span_reason = saw_span_reason ||
            w.invalid_reason == "angle_span_exceeds_limit";
    }
    check(saw_span_reason, "angle-span rejection remains visible in manifest data");

    std::cout << "[MECHANICAL_SCAN_SELFTEST] failures=" << failures << '\n';
    return failures == 0 ? 0 : 1;
}
