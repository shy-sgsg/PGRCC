#include "TrackManager.hpp"

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <sys/types.h>
#include <vector>

namespace {

struct InputRow {
    int period_id = 0;
    double utc = 0.0;
    bool valid = false;
    int det_id = -1;
    GMTIDetection det{};
};

std::vector<std::string> splitCsv(const std::string& line)
{
    std::vector<std::string> fields;
    std::string field;
    bool quoted = false;
    for (size_t i = 0; i < line.size(); ++i) {
        const char ch = line[i];
        if (ch == '"') {
            if (quoted && i + 1 < line.size() && line[i + 1] == '"') {
                field.push_back('"');
                ++i;
            } else {
                quoted = !quoted;
            }
        } else if (ch == ',' && !quoted) {
            fields.push_back(field);
            field.clear();
        } else {
            field.push_back(ch);
        }
    }
    fields.push_back(field);
    return fields;
}

double number(const std::vector<std::string>& fields,
              const std::map<std::string, size_t>& columns,
              const std::string& name,
              double fallback = 0.0)
{
    const auto it = columns.find(name);
    if (it == columns.end() || it->second >= fields.size() ||
        fields[it->second].empty()) {
        return fallback;
    }
    return std::stod(fields[it->second]);
}

std::string textValue(const std::vector<std::string>& fields,
                      const std::map<std::string, size_t>& columns,
                      const std::string& name,
                      const std::string& fallback = "")
{
    const auto it = columns.find(name);
    if (it == columns.end() || it->second >= fields.size()) {
        return fallback;
    }
    return fields[it->second];
}

bool parseBool(const std::string& value)
{
    return value == "1" || value == "true" || value == "TRUE" ||
           value == "yes" || value == "on";
}

void ensureDirectory(const std::string& path)
{
    if (path.empty() || path == ".") return;
    std::string current;
    if (path[0] == '/') current = "/";
    std::stringstream ss(path);
    std::string part;
    while (std::getline(ss, part, '/')) {
        if (part.empty()) continue;
        if (!current.empty() && current.back() != '/') current.push_back('/');
        current += part;
        if (::mkdir(current.c_str(), 0775) != 0 && errno != EEXIST) {
            throw std::runtime_error("cannot create directory: " + current);
        }
    }
}

void usage(const char* exe)
{
    std::cerr
        << "usage: " << exe << " --input detections.csv --output-dir DIR [options]\n"
        << "options:\n"
        << "  --distance-mode euclidean|mahalanobis\n"
        << "  --assignment-mode nearest|hungarian\n"
        << "  --output-source measurement|prediction|kalman_filtered\n"
        << "  --runtime-mode debug|release\n"
        << "  --confirm-window N --confirm-hits M --max-missed N\n"
        << "  --gate-m M --chi2-gate X --dummy-cost X\n"
        << "  --measurement-noise-m M --process-noise-pos X --process-noise-vel X\n"
        << "  --use-speed-cost 0|1 --speed-weight X\n"
        << "  --use-heading-cost 0|1 --heading-weight X\n";
}

} // namespace

int main(int argc, char** argv)
{
    try {
        std::string input_path;
        std::string output_dir;
        Config cfg{};
        cfg.L0 = 120.0;
        cfg.result_file_id = 1;
        cfg.result_add = ".";
        // This is an offline evaluator. Keep the event stream required by its
        // scoring scripts; production GMTI_pipe_core release mode suppresses it.
        cfg.runtime_mode = "debug";
        cfg.runtime_diagnostics_enabled = true;
        cfg.track_debug_level = 0;
        cfg.track_debug_dump = false;
        cfg.track_debug_dump_level = 0;
        cfg.track_output_state_source = "measurement";

        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            auto requireValue = [&]() -> std::string {
                if (++i >= argc) throw std::runtime_error("missing value after " + arg);
                return argv[i];
            };
            if (arg == "--input") input_path = requireValue();
            else if (arg == "--output-dir") output_dir = requireValue();
            else if (arg == "--distance-mode") {
                const std::string value = requireValue();
                if (value == "euclidean") cfg.track_distance_mode = 0;
                else if (value == "mahalanobis") cfg.track_distance_mode = 1;
                else throw std::runtime_error("invalid distance mode: " + value);
            } else if (arg == "--assignment-mode") {
                const std::string value = requireValue();
                if (value == "nearest") cfg.track_assignment_mode = 0;
                else if (value == "hungarian") cfg.track_assignment_mode = 1;
                else throw std::runtime_error("invalid assignment mode: " + value);
            } else if (arg == "--output-source") {
                cfg.track_output_state_source = requireValue();
            } else if (arg == "--runtime-mode") {
                cfg.runtime_mode = requireValue();
                if (cfg.runtime_mode == "release") {
                    cfg.runtime_diagnostics_enabled = false;
                    cfg.track_debug_level = 0;
                    cfg.track_debug_dump = false;
                    cfg.track_debug_dump_level = 0;
                } else if (cfg.runtime_mode == "debug") {
                    cfg.runtime_diagnostics_enabled = true;
                } else {
                    throw std::runtime_error(
                        "runtime mode must be debug or release");
                }
            } else if (arg == "--confirm-window") cfg.track_confirm_window = std::stoi(requireValue());
            else if (arg == "--confirm-hits") cfg.track_confirm_hits = std::stoi(requireValue());
            else if (arg == "--max-missed") cfg.track_max_missed = std::stoi(requireValue());
            else if (arg == "--tentative-max-missed") cfg.track_tentative_max_missed = std::stoi(requireValue());
            else if (arg == "--gate-m") cfg.track_gate_m = std::stod(requireValue());
            else if (arg == "--chi2-gate") cfg.track_chi2_gate = std::stod(requireValue());
            else if (arg == "--dummy-cost") cfg.track_dummy_cost = std::stod(requireValue());
            else if (arg == "--measurement-noise-m") cfg.track_measurement_noise_pos = std::stod(requireValue());
            else if (arg == "--process-noise-pos") cfg.track_process_noise_pos = std::stod(requireValue());
            else if (arg == "--process-noise-vel") cfg.track_process_noise_vel = std::stod(requireValue());
            else if (arg == "--use-speed-cost") cfg.track_use_speed_cost = parseBool(requireValue());
            else if (arg == "--speed-weight") cfg.track_speed_smooth_weight = std::stod(requireValue());
            else if (arg == "--use-heading-cost") cfg.track_use_heading_cost = parseBool(requireValue());
            else if (arg == "--heading-weight") cfg.track_heading_weight = std::stod(requireValue());
            else if (arg == "--heading-min-displacement-m") cfg.track_heading_min_displacement_m = std::stod(requireValue());
            else if (arg == "--min-linearity") cfg.track_min_linearity_confirm = std::stod(requireValue());
            else if (arg == "--help" || arg == "-h") {
                usage(argv[0]);
                return 0;
            } else {
                throw std::runtime_error("unknown argument: " + arg);
            }
        }
        if (input_path.empty() || output_dir.empty()) {
            usage(argv[0]);
            return 2;
        }
        if (cfg.track_confirm_window <= 0 || cfg.track_confirm_hits <= 0 ||
            cfg.track_confirm_hits > cfg.track_confirm_window ||
            cfg.track_max_missed < 0 || cfg.track_tentative_max_missed < 0) {
            throw std::runtime_error("invalid confirmation/deletion configuration");
        }
        if (!std::isfinite(cfg.track_heading_min_displacement_m) ||
            cfg.track_heading_min_displacement_m < 0.0) {
            throw std::runtime_error("heading minimum displacement must be finite and >= 0");
        }
        ensureDirectory(output_dir);
        cfg.result_add = output_dir;
        cfg.track_debug_dir = output_dir;

        std::ifstream input(input_path);
        if (!input.is_open()) throw std::runtime_error("cannot open input: " + input_path);
        std::string line;
        if (!std::getline(input, line)) throw std::runtime_error("empty input CSV");
        const std::vector<std::string> header = splitCsv(line);
        std::map<std::string, size_t> columns;
        for (size_t i = 0; i < header.size(); ++i) columns[header[i]] = i;
        for (const char* required : {"period_id", "utc", "valid"}) {
            if (columns.find(required) == columns.end()) {
                throw std::runtime_error(std::string("missing CSV column: ") + required);
            }
        }

        std::map<int, std::vector<InputRow>> frames;
        std::map<int, double> frame_times;
        while (std::getline(input, line)) {
            if (line.empty()) continue;
            const std::vector<std::string> fields = splitCsv(line);
            InputRow row;
            row.period_id = static_cast<int>(number(fields, columns, "period_id"));
            row.utc = number(fields, columns, "utc");
            row.valid = parseBool(textValue(fields, columns, "valid", "0"));
            row.det_id = static_cast<int>(number(fields, columns, "det_id", -1));
            row.det.id = static_cast<uint16_t>(std::max(0, row.det_id));
            row.det.e = number(fields, columns, "e");
            row.det.n = number(fields, columns, "n");
            row.det.lon = number(fields, columns, "lon");
            row.det.lat = number(fields, columns, "lat");
            row.det.speed = number(fields, columns, "speed");
            row.det.direction = number(fields, columns, "direction");
            row.det.range = number(fields, columns, "range");
            row.det.utcMid = number(fields, columns, "det_utc", row.utc);
            if (row.period_id <= 0 || !std::isfinite(row.utc)) {
                throw std::runtime_error("invalid period_id/utc in input row");
            }
            frames[row.period_id].push_back(row);
            frame_times[row.period_id] = row.utc;
        }
        if (frames.empty()) throw std::runtime_error("no frames in input CSV");

        std::ofstream output(output_dir + "/protocol_tracks.csv");
        if (!output.is_open()) throw std::runtime_error("cannot create protocol_tracks.csv");
        output << "period_id,utc,track_id,e,n,lon,lat,speed,direction,range\n";
        TrackManager manager;
        size_t output_count = 0;
        for (const auto& item : frames) {
            std::vector<GMTIDetection> detections;
            for (const auto& row : item.second) {
                if (row.valid) detections.push_back(row.det);
            }
            const double frame_utc = frame_times[item.first];
            const std::vector<GMTIDetection> tracks = manager.updateRawDetections(
                cfg, detections, item.first, frame_utc);
            for (const auto& track : tracks) {
                output << std::setprecision(12)
                       << item.first << ',' << frame_utc << ',' << track.id << ','
                       << track.e << ',' << track.n << ',' << track.lon << ','
                       << track.lat << ',' << track.speed << ',' << track.direction
                       << ',' << track.range << '\n';
                ++output_count;
            }
        }
        std::cout << "[TRACK_EVAL] frames=" << frames.size()
                  << " protocol_outputs=" << output_count
                  << " output_dir=" << output_dir << std::endl;
        return 0;
    } catch (const std::exception& exc) {
        std::cerr << "[TRACK_EVAL][ERR] " << exc.what() << std::endl;
        return 1;
    }
}
