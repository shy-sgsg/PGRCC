#include "truth_writer.h"

#include <cmath>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

std::vector<std::string> split(const std::string &line)
{
    std::vector<std::string> fields;
    std::stringstream stream(line);
    std::string field;
    while (std::getline(stream, field, ',')) fields.push_back(field);
    return fields;
}

gmti::target_injection::PulseTruth makeTruth(int period, int pulse)
{
    gmti::target_injection::PulseTruth truth;
    truth.period_id = period;
    truth.beam_id = 7;
    truth.pulse_id = pulse;
    truth.target_id = 1;
    truth.target_name = "moving_target";
    truth.has_ref_geometry = true;
    truth.ref_pulse_idx = 65;
    truth.ref_time_s = 0.05;
    truth.ref_target_e = 100.0;
    truth.ref_target_n = 200.0;
    truth.ref_range_m = 80000.0;
    truth.geom.time_sec = 10.0 * period + 0.001 * pulse;
    truth.geom.range_m = 80000.0 - 50.0 * period - pulse;
    truth.geom.theta_cmd_deg = -46.0;
    truth.geom.target_azimuth_deg = -45.5 + 0.1 * period;
    truth.target_e = 100.0 + 4.0 * truth.geom.time_sec;
    truth.target_n = 200.0 + 3.0 * truth.geom.time_sec;
    truth.target_lat = 40.0 + 1.0e-5 * period;
    truth.target_lon = 116.0 + 1.0e-5 * period;
    truth.target_ve_mps = 4.0;
    truth.target_vn_mps = 3.0;
    truth.target_vr_self_mps = 2.0 + 0.1 * period;
    truth.target_vt_self_mps = 1.0;
    truth.expected_range_bin = 700 - 20 * period - pulse;
    truth.visible_by_beam = true;
    truth.snr_db = -30.0;
    return truth;
}

} // namespace

int main()
{
    const std::string output_dir = "/tmp/gmti_truth_writer_midpoint_selftest";
    gmti::target_injection::TruthWriter writer;
    writer.setCaseId("truth_midpoint_regression");
    std::string error;
    if (!writer.open(output_dir, error)) {
        std::cerr << "[FAIL] " << error << '\n';
        return 1;
    }
    for (int period = 0; period < 2; ++period) {
        for (int pulse : {0, 64, 65, 66, 129}) {
            writer.writePulse(makeTruth(period, pulse));
        }
    }
    writer.writeSummary();
    writer.close();

    std::ifstream input(output_dir + "/truth_targets_by_beam.csv");
    std::string header;
    std::string row0;
    std::string row1;
    std::getline(input, header);
    std::getline(input, row0);
    std::getline(input, row1);
    const std::vector<std::string> first = split(row0);
    const std::vector<std::string> second = split(row1);
    const bool shape_ok = first.size() == 27 && second.size() == 27;
    const bool time_ok = shape_ok &&
        std::fabs(std::stod(first[4]) - 0.065) < 1.0e-12 &&
        std::fabs(std::stod(second[4]) - 10.065) < 1.0e-12;
    const bool position_ok = shape_ok &&
        std::fabs(std::stod(first[5]) - 100.26) < 1.0e-9 &&
        std::fabs(std::stod(second[5]) - 140.26) < 1.0e-9;
    const bool range_ok = shape_ok &&
        std::stoi(first[10]) == 635 && std::stoi(second[10]) == 615;
    const bool ok = shape_ok && time_ok && position_ok && range_ok;
    std::cout << "[TRUTH_MIDPOINT_SELFTEST] shape=" << shape_ok
              << " time=" << time_ok
              << " position=" << position_ok
              << " range_bin=" << range_ok
              << " status=" << (ok ? "PASS" : "FAIL") << '\n';
    return ok ? 0 : 1;
}
