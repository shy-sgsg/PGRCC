#include "low_radial_velocity_filter.hpp"

#include <cmath>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {

GMTIOutput::DetectionCsvRecord makeDetection(int beam,
                                             double e,
                                             double n,
                                             double vr,
                                             double amplitude)
{
    GMTIOutput::DetectionCsvRecord rec;
    rec.beam_id = beam;
    rec.e = e;
    rec.n = n;
    rec.radial_velocity_mps = vr;
    rec.amplitude = amplitude;
    rec.p38_used_k = -0.003;
    return rec;
}

void appendMt(std::vector<double>& mt, double marker)
{
    for (int k = 0; k < 8; ++k) mt.push_back(marker + k);
}

void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

} // namespace

int main()
{
    try {
        std::vector<GMTIOutput::DetectionCsvRecord> detections{
            makeDetection(2, 0.0, 0.0, 0.03, 10.0),     // static residue
            makeDetection(16, 100.0, 200.0, 3.50, 20.0), // weaker overlap observation
            makeDetection(17, 112.0, 216.0, 3.55, 80.0), // retained overlap observation
            makeDetection(17, 105.0, 200.0, 3.50, 40.0), // same-beam close target: must remain
            makeDetection(30, 500.0, 200.0, 3.50, 30.0)  // independent target
        };
        std::vector<double> mt;
        for (int i = 0; i < 5; ++i) appendMt(mt, 100.0 * i);

        const auto low = filterLowRadialVelocity(detections, mt, true, 2.0);
        require(low.input_count == 5 && low.removed_low_velocity_count == 1 &&
                    low.output_count == 4,
                "low radial velocity filtering result mismatch");
        require(mt.size() == detections.size() * 8U,
                "MT records lost synchronization after speed filtering");

        const auto nms = suppressAdjacentBeamDuplicates(detections, mt, true, 30.0, 0.35);
        require(nms.input_count == 4 && nms.removed_duplicate_count == 1 &&
                    nms.output_count == 3,
                "adjacent-beam duplicate suppression result mismatch");
        require(mt.size() == detections.size() * 8U,
                "MT records lost synchronization after adjacent-beam suppression");
        require(detections[0].beam_id == 17 && std::abs(detections[0].amplitude - 80.0) < 1e-12,
                "stronger adjacent-beam observation was not retained");
        require(detections[1].beam_id == 17 && std::abs(detections[1].amplitude - 40.0) < 1e-12,
                "same-beam close target was incorrectly suppressed");
        require(detections[2].beam_id == 30,
                "independent target was incorrectly suppressed");

        detections[0].p38_used_k = -0.002;
        detections[1].p38_used_k = 0.0005;
        detections[2].p38_used_k = 0.0006;
        const auto p38 = filterP38SlopeUpperBound(detections, mt, true, 0.0005);
        require(p38.input_count == 3 && p38.removed_upper_bound_count == 1 &&
                    p38.removed_nonfinite_count == 0 && p38.output_count == 2,
                "P38 slope upper-bound filtering result mismatch");
        require(mt.size() == detections.size() * 8U,
                "MT records lost synchronization after P38 slope filtering");
        require(detections[0].beam_id == 17 && detections[1].beam_id == 17,
                "P38 slope filtering retained the wrong records");

        Config mechanical_cfg;
        mechanical_cfg.scan_mode = ScanMode::Mechanical;
        mechanical_cfg.mechanical_scan.cpi_pulse_count = 130;
        mechanical_cfg.mechanical_scan.cpi_step_pulse = 65;
        mechanical_cfg.mechanical_scan.enable_cpi_dedup = true;
        mechanical_cfg.mechanical_scan.dedup_time_gate_s = 0.15;
        mechanical_cfg.mechanical_scan.dedup_position_gate_m = 50.0;
        mechanical_cfg.mechanical_scan.dedup_velocity_gate_mps = 5.0;
        detections.clear();
        mt.clear();
        for (int i = 0; i < 3; ++i) {
            GMTIOutput::DetectionCsvRecord rec =
                makeDetection(-1, 100.0 + 2.0 * i, 200.0, 8.0, 10.0 + i);
            rec.scan_mode = ScanMode::Mechanical;
            rec.scan_id = 4;
            rec.window_id = i;
            rec.utc = 10.0 + 0.05 * i;
            rec.az_center_deg = -1.0 + i;
            rec.theta_true_deg = 0.1;
            detections.push_back(rec);
            appendMt(mt, 1000.0 + 100.0 * i);
        }
        // Window 1 is closest to target angle and must suppress its duplicate
        // observations in adjacent overlapping windows.
        const auto mechanical = deduplicateMechanicalDetections(
            detections, mt, mechanical_cfg);
        require(mechanical.input_count == 3 &&
                    mechanical.removed_duplicate_count == 2 &&
                    mechanical.output_count == 1,
                "mechanical overlapping-CPI deduplication result mismatch");
        require(detections.front().window_id == 1 && detections.front().beam_id == -1,
                "mechanical dedup did not retain the observation nearest CPI centre");
        require(mt.size() == detections.size() * 8U,
                "MT records lost synchronization after mechanical CPI deduplication");
    } catch (const std::exception& ex) {
        std::cerr << "low_radial_velocity_filter_selftest failed: " << ex.what() << '\n';
        return 1;
    }
    std::cout << "low_radial_velocity_filter_selftest passed\n";
    return 0;
}
