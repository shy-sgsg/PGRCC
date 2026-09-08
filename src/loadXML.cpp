#include "GMTIProcessor.hpp"
#include <iostream>
#include <string>
#include <fstream>
#include <cassert>
#include <sstream>
#include <cctype>
#include <cmath>
#include <regex>
#include <algorithm>
#include <stdexcept>
#include "rangeCompress.hpp"
#include "track_output_state_source.hpp"

// 读取 XML 配置文件并填充到 Config 结构体中
bool GMTIProcessor::readXmlParam(const std::string &xmlFile, Config &cfg)
{
    TiXmlDocument doc;
    if (!doc.LoadFile(xmlFile.c_str()))
    {
        std::cerr << "Error loading XML file: " << xmlFile;
        if (doc.Error())
        {
            std::cerr << " (" << doc.ErrorDesc() << ")";
        }
        std::cerr << std::endl;
        return false; // 如果加载失败，返回 false
    }

    // 访问根元素
    TiXmlElement *root = doc.FirstChildElement("GMTI");
    if (!root)
    {
        std::cerr << "Root element <GMTI> not found." << std::endl;
        return false; // 如果根元素不存在，返回 false
    }

    // 访问 GMTI_parameter 元素
    TiXmlElement *param = root->FirstChildElement("GMTI_parameter");
    if (!param)
    {
        std::cerr << "GMTI_parameter element not found." << std::endl;
        return false; // 如果没有找到 GMTI_parameter 元素，返回 false
    }

    auto getTextContent = [](TiXmlElement *element) -> std::string
    {
        if (element && element->GetText())
        {
            return element->GetText();
        }
        return "";
    };

    auto trimText = [](const std::string& text) -> std::string
    {
        size_t first = 0;
        while (first < text.size() && std::isspace(static_cast<unsigned char>(text[first]))) {
            ++first;
        }
        size_t last = text.size();
        while (last > first && std::isspace(static_cast<unsigned char>(text[last - 1]))) {
            --last;
        }
        return text.substr(first, last - first);
    };

    auto getOptionalText = [&](const char *name) -> std::string
    {
        return trimText(getTextContent(param->FirstChildElement(name)));
    };

    auto getRequiredText = [&](const char *name) -> std::string
    {
        const std::string text = getOptionalText(name);
        if (text.empty()) {
            std::ostringstream oss;
            oss << "[XML][ERR] Missing required field <" << name << "> in " << xmlFile;
            throw std::runtime_error(oss.str());
        }
        return text;
    };

    auto parseRequiredInt = [&](const char *name) -> int
    {
        const std::string text = getRequiredText(name);
        try {
            size_t pos = 0;
            const int value = std::stoi(text, &pos);
            if (pos != text.size()) {
                throw std::invalid_argument("trailing characters");
            }
            return value;
        } catch (const std::exception& e) {
            std::ostringstream oss;
            oss << "[XML][ERR] Invalid integer field <" << name << ">=\"" << text
                << "\" in " << xmlFile << ": " << e.what();
            throw std::runtime_error(oss.str());
        }
    };

    auto parseRequiredDouble = [&](const char *name) -> double
    {
        const std::string text = getRequiredText(name);
        try {
            size_t pos = 0;
            const double value = std::stod(text, &pos);
            if (pos != text.size()) {
                throw std::invalid_argument("trailing characters");
            }
            return value;
        } catch (const std::exception& e) {
            std::ostringstream oss;
            oss << "[XML][ERR] Invalid numeric field <" << name << ">=\"" << text
                << "\" in " << xmlFile << ": " << e.what();
            throw std::runtime_error(oss.str());
        }
    };

    auto parseOptionalInt = [&](const char *name, int current) -> int
    {
        const std::string text = getOptionalText(name);
        if (text.empty()) {
            return current;
        }
        try {
            size_t pos = 0;
            const int value = std::stoi(text, &pos);
            if (pos != text.size()) {
                throw std::invalid_argument("trailing characters");
            }
            return value;
        } catch (const std::exception& e) {
            std::cerr << "[XML][WARN] Invalid optional integer field <" << name
                      << ">=\"" << text << "\" in " << xmlFile
                      << "; keep default/current value " << current
                      << ". reason=" << e.what() << std::endl;
            return current;
        }
    };

    auto parseOptionalDouble = [&](const char *name, double current) -> double
    {
        const std::string text = getOptionalText(name);
        if (text.empty()) {
            return current;
        }
        try {
            size_t pos = 0;
            const double value = std::stod(text, &pos);
            if (pos != text.size()) {
                throw std::invalid_argument("trailing characters");
            }
            return value;
        } catch (const std::exception& e) {
            std::cerr << "[XML][WARN] Invalid optional numeric field <" << name
                      << ">=\"" << text << "\" in " << xmlFile
                      << "; keep default/current value " << current
                      << ". reason=" << e.what() << std::endl;
            return current;
        }
    };

    auto parseOptionalSizeT = [&](const char *name, size_t current) -> size_t
    {
        const std::string text = getOptionalText(name);
        if (text.empty()) {
            return current;
        }
        try {
            size_t pos = 0;
            const unsigned long long value = std::stoull(text, &pos);
            if (pos != text.size()) {
                throw std::invalid_argument("trailing characters");
            }
            return value > 0 ? static_cast<size_t>(value) : current;
        } catch (const std::exception& e) {
            std::ostringstream oss;
            oss << "[XML][ERR] Invalid optional size field <" << name << ">=\"" << text
                << "\" in " << xmlFile << ": " << e.what();
            throw std::runtime_error(oss.str());
        }
    };

    auto parseOptionalBool = [&](const char *name, bool current) -> bool
    {
        const std::string text = getOptionalText(name);
        if (text.empty()) {
            return current;
        }
        if (text == "true" || text == "1") {
            return true;
        }
        if (text == "false" || text == "0") {
            return false;
        }
        std::cerr << "[XML][WARN] Invalid optional boolean field <" << name
                  << ">=\"" << text << "\" in " << xmlFile
                  << "; keep default/current value " << (current ? "true" : "false")
                  << ". Expected true/false/1/0." << std::endl;
        return current;
    };

    auto parseOptionalString = [&](const char *name, const std::string& current) -> std::string
    {
        const std::string text = getOptionalText(name);
        return text.empty() ? current : text;
    };

    auto parseOptionalDelayUs = [&](const std::vector<const char*>& names,
                                    double& delay_us,
                                    std::string& source_name) -> bool
    {
        for (const char* name : names) {
            const std::string text = getOptionalText(name);
            if (text.empty()) {
                continue;
            }
            try {
                size_t pos = 0;
                const double value = std::stod(text, &pos);
                if (pos != text.size()) {
                    throw std::invalid_argument("trailing characters");
                }
                source_name = name;
                const std::string field(name);
                if (field.size() >= 3 &&
                    field.compare(field.size() - 3, 3, "_us") == 0) {
                    delay_us = value;
                } else {
                    delay_us = (std::abs(value) < 1.0) ? value * 1.0e6 : value;
                }
                return true;
            } catch (const std::exception& e) {
                std::cerr << "[XML][WARN] Invalid optional delay field <" << name
                          << ">=\"" << text << "\" in " << xmlFile
                          << "; sample_delay_us remains unavailable. reason="
                          << e.what() << std::endl;
                return false;
            }
        }
        return false;
    };

    auto parseIntList = [](const std::string& text) -> std::vector<int>
    {
        std::vector<int> values;
        std::string token;
        std::stringstream ss(text);
        while (std::getline(ss, token, ',')) {
            std::string trimmed;
            for (char ch : token) {
                if (!std::isspace(static_cast<unsigned char>(ch))) {
                    trimmed.push_back(ch);
                }
            }
            if (!trimmed.empty()) {
                try {
                    values.push_back(std::stoi(trimmed));
                } catch (const std::exception& e) {
                    std::ostringstream oss;
                    oss << "[XML][ERR] Invalid integer in <track_idx_range>: \""
                        << trimmed << "\": " << e.what();
                    throw std::runtime_error(oss.str());
                }
            }
        }
        return values;
    };

    auto parsePathList = [](std::string text) -> std::vector<std::string>
    {
        std::replace(text.begin(), text.end(), '\n', ';');
        std::replace(text.begin(), text.end(), '\r', ';');
        std::vector<std::string> values;
        std::stringstream ss(text);
        std::string token;
        while (std::getline(ss, token, ';')) {
            const size_t first = token.find_first_not_of(" \t");
            if (first == std::string::npos) continue;
            const size_t last = token.find_last_not_of(" \t");
            values.push_back(token.substr(first, last - first + 1U));
        }
        return values;
    };

    auto extractFileId = [](const std::string& path) -> int
    {
        const size_t pos = path.find_last_of("/\\");
        const std::string filename = (pos == std::string::npos) ? path : path.substr(pos + 1);
        std::smatch match;
        const std::regex taggedPattern(
            R"((?:ID|data[_-]?)(\d+)(?:\.(bin|dat))?$)", std::regex::icase);
        if (std::regex_search(filename, match, taggedPattern) && match.size() >= 2) {
            return std::stoi(match[1].str());
        }
        const std::regex trailingPattern(
            R"((\d+)(?:\.(bin|dat))?$)", std::regex::icase);
        if (std::regex_search(filename, match, trailingPattern) && match.size() >= 2) {
            return std::stoi(match[1].str());
        }
        return -1;
    };

    auto buildTrackIdxRange = [](int id, int window) -> std::vector<int>
    {
        std::vector<int> values;
        if (id <= 0 || window <= 0) {
            return values;
        }
        const int start = id - window + 1;
        values.reserve(static_cast<size_t>(window));
        for (int idx = start; idx <= id; ++idx) {
            values.push_back(idx);
        }
        return values;
    };

    try {
    {
        std::string mode = getOptionalText("scan_mode");
        std::transform(mode.begin(), mode.end(), mode.begin(),
                       [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
        if (mode.empty() || mode == "electronic") {
            cfg.scan_mode = ScanMode::Electronic;
        } else if (mode == "mechanical") {
            cfg.scan_mode = ScanMode::Mechanical;
        } else {
            throw std::runtime_error(
                "field <scan_mode> must be electronic or mechanical");
        }

        TiXmlElement *mechanical = param->FirstChildElement("mechanical_scan");
        if (mechanical) {
            auto nestedText = [&](const char *name) -> std::string {
                return trimText(getTextContent(mechanical->FirstChildElement(name)));
            };
            auto nestedInt = [&](const char *name, int current) -> int {
                const std::string text = nestedText(name);
                if (text.empty()) return current;
                try {
                    size_t pos = 0;
                    const int value = std::stoi(text, &pos);
                    if (pos != text.size()) throw std::invalid_argument("trailing characters");
                    return value;
                } catch (const std::exception& e) {
                    throw std::runtime_error(std::string("invalid <mechanical_scan><") +
                                             name + ">: " + e.what());
                }
            };
            auto nestedDouble = [&](const char *name, double current) -> double {
                const std::string text = nestedText(name);
                if (text.empty()) return current;
                try {
                    size_t pos = 0;
                    const double value = std::stod(text, &pos);
                    if (pos != text.size()) throw std::invalid_argument("trailing characters");
                    return value;
                } catch (const std::exception& e) {
                    throw std::runtime_error(std::string("invalid <mechanical_scan><") +
                                             name + ">: " + e.what());
                }
            };
            auto nestedBool = [&](const char *name, bool current) -> bool {
                std::string text = nestedText(name);
                if (text.empty()) return current;
                std::transform(text.begin(), text.end(), text.begin(),
                               [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
                if (text == "true" || text == "1") return true;
                if (text == "false" || text == "0") return false;
                throw std::runtime_error(std::string("invalid <mechanical_scan><") +
                                         name + ">: expected true/false/1/0");
            };

            MechanicalScanConfig& m = cfg.mechanical_scan;
            m.acquisition_scan_prt_count = nestedInt(
                "acquisition_scan_prt_count", m.acquisition_scan_prt_count);
            m.cpi_pulse_count = nestedInt("cpi_pulse_count", m.cpi_pulse_count);
            m.cpi_step_pulse = nestedInt("cpi_step_pulse", m.cpi_step_pulse);
            m.max_cpi_angle_span_deg = nestedDouble(
                "max_cpi_angle_span_deg", m.max_cpi_angle_span_deg);
            m.use_actual_servo_angle = nestedBool(
                "use_actual_servo_angle", m.use_actual_servo_angle);
            m.allow_scan_reverse = nestedBool(
                "allow_scan_reverse", m.allow_scan_reverse);
            m.scan_edge_guard_deg = nestedDouble(
                "scan_edge_guard_deg", m.scan_edge_guard_deg);
            m.enable_cpi_dedup = nestedBool(
                "enable_cpi_dedup", m.enable_cpi_dedup);
            m.dedup_time_gate_s = nestedDouble(
                "dedup_time_gate_s", m.dedup_time_gate_s);
            m.dedup_position_gate_m = nestedDouble(
                "dedup_position_gate_m", m.dedup_position_gate_m);
            m.dedup_velocity_gate_mps = nestedDouble(
                "dedup_velocity_gate_mps", m.dedup_velocity_gate_mps);
            m.scan_direction_deadband_deg = nestedDouble(
                "scan_direction_deadband_deg", m.scan_direction_deadband_deg);
            m.scan_direction_confirm_pulses = nestedInt(
                "scan_direction_confirm_pulses", m.scan_direction_confirm_pulses);
            m.phase_center_rotation_enable = nestedBool(
                "phase_center_rotation_enable", m.phase_center_rotation_enable);
            m.phase_center_mount_angle_deg = nestedDouble(
                "phase_center_mount_angle_deg", m.phase_center_mount_angle_deg);
            m.phase_center_rotation_sign = nestedInt(
                "phase_center_rotation_sign", m.phase_center_rotation_sign);
        }
        const MechanicalScanConfig& m = cfg.mechanical_scan;
        if (m.acquisition_scan_prt_count < 0 ||
            m.cpi_pulse_count <= 0 || m.cpi_step_pulse <= 0 ||
            !(m.max_cpi_angle_span_deg >= 0.0) ||
            !(m.scan_edge_guard_deg >= 0.0) ||
            !(m.dedup_time_gate_s >= 0.0) ||
            !(m.dedup_position_gate_m >= 0.0) ||
            !(m.dedup_velocity_gate_mps >= 0.0) ||
            !(m.scan_direction_deadband_deg >= 0.0) ||
            m.scan_direction_confirm_pulses <= 0 ||
            !std::isfinite(m.max_cpi_angle_span_deg) ||
            !std::isfinite(m.scan_edge_guard_deg) ||
            !std::isfinite(m.dedup_time_gate_s) ||
            !std::isfinite(m.dedup_position_gate_m) ||
            !std::isfinite(m.dedup_velocity_gate_mps) ||
            !std::isfinite(m.scan_direction_deadband_deg) ||
            !std::isfinite(m.phase_center_mount_angle_deg) ||
            (m.phase_center_rotation_sign != 1 &&
             m.phase_center_rotation_sign != -1)) {
            throw std::runtime_error("invalid <mechanical_scan> configuration");
        }
    }
    const std::string xmlDataAdd = getOptionalText("GMTI_data");
    const std::string xmlDataAdd2 = getOptionalText("GMTI_data2");
    const std::string xmlDataNew = getOptionalText("GMTI_data_new");
    if (!xmlDataAdd.empty()) cfg.GMTI_Data_add = xmlDataAdd;
    if (!xmlDataAdd2.empty()) cfg.GMTI_Data_add2 = xmlDataAdd2;
    if (!xmlDataNew.empty()) cfg.GMTI_Data_new = xmlDataNew;
    cfg.test_mode = parseOptionalInt("test", cfg.test_mode);
    if (cfg.test_mode != 0 && cfg.test_mode != 1 && cfg.test_mode != 2) {
        throw std::runtime_error("field <test> must be 0, 1, or 2");
    }
    {
        const std::string testCyclePaths = getOptionalText("test_cycle_data_paths");
        if (!testCyclePaths.empty()) {
            cfg.test_cycle_data_paths = parsePathList(testCyclePaths);
        }
    }
    cfg.test_cycle_data_playback = parseOptionalString(
        "test_cycle_data_playback", cfg.test_cycle_data_playback);
    std::transform(cfg.test_cycle_data_playback.begin(),
                   cfg.test_cycle_data_playback.end(),
                   cfg.test_cycle_data_playback.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    if (cfg.test_cycle_data_playback != "once" &&
        cfg.test_cycle_data_playback != "loop") {
        throw std::runtime_error(
            "field <test_cycle_data_playback> must be once or loop");
    }
    cfg.result_add = getRequiredText("result_add");
    cfg.enable_result_return = parseOptionalBool(
        "enable_result_return", cfg.enable_result_return);
    {
        const std::string pipeRootPath = getOptionalText("pipe_root_path");
        if (!pipeRootPath.empty()) {
            cfg.pipe_root_path = pipeRootPath;
        }
    }
    cfg.echo_input_mode = parseOptionalString("echo_input_mode", cfg.echo_input_mode);
    std::transform(cfg.echo_input_mode.begin(), cfg.echo_input_mode.end(),
                   cfg.echo_input_mode.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    cfg.shm_name = parseOptionalString("shm_name", cfg.shm_name);
    cfg.shm_read_chunk_bytes = parseOptionalSizeT(
        "shm_read_chunk_bytes", cfg.shm_read_chunk_bytes);
    cfg.shm_reconnect_ms = parseOptionalInt("shm_reconnect_ms", cfg.shm_reconnect_ms);
    cfg.shm_idle_sleep_us = parseOptionalInt("shm_idle_sleep_us", cfg.shm_idle_sleep_us);
    cfg.shm_cycle_timeout_ms = parseOptionalInt(
        "shm_cycle_timeout_ms", cfg.shm_cycle_timeout_ms);
    cfg.shm_expected_prt_mode = parseOptionalInt(
        "shm_expected_prt_mode", cfg.shm_expected_prt_mode);
    cfg.shm_prt_counter_phase = parseOptionalInt(
        "shm_prt_counter_phase", cfg.shm_prt_counter_phase);
    cfg.shm_scan_beam_count = parseOptionalInt(
        "shm_scan_beam_count", cfg.shm_scan_beam_count);
    cfg.shm_allow_partial_first_scan = parseOptionalBool(
        "shm_allow_partial_first_scan", cfg.shm_allow_partial_first_scan);
    if (cfg.shm_scan_beam_count < 0) {
        throw std::runtime_error("field <shm_scan_beam_count> must be >= 0");
    }
    cfg.Plane_POS_add = getOptionalText("Plane_POS");
    cfg.CAR_POS_add = getOptionalText("CAR_POS");
    cfg.reffunc_add = getOptionalText("reffunc_add");
    cfg.channel_mode = getRequiredText("isSeparated");
    cfg.INFO_Type = parseRequiredInt("INFO_Type");
    cfg.legacy_replay_use_companion_parameters = parseOptionalBool(
        "legacy_replay_use_companion_parameters",
        cfg.legacy_replay_use_companion_parameters);
    cfg.iq_compose = getOptionalText("iq_compose");
    cfg.iq_data_type = getOptionalText("iq_data_type");
    if (cfg.iq_data_type.empty()) {
        cfg.iq_data_type = "float32";
    }
    cfg.new_protocol_channel_count = parseOptionalInt("new_protocol_channel_count", cfg.new_protocol_channel_count);
    cfg.new_protocol_read_channel_1 = parseOptionalInt("new_protocol_read_channel_1", cfg.new_protocol_read_channel_1);
    cfg.new_protocol_read_channel_2 = parseOptionalInt("new_protocol_read_channel_2", cfg.new_protocol_read_channel_2);
    cfg.new_protocol_gpu_preprocess = parseOptionalBool(
        "new_protocol_gpu_preprocess", cfg.new_protocol_gpu_preprocess);
    cfg.enable_four_channel_fusion = parseOptionalBool(
        "enable_four_channel_fusion", cfg.enable_four_channel_fusion);
    cfg.four_channel_phase_compensation_enable = parseOptionalBool(
        "four_channel_phase_compensation_enable",
        cfg.four_channel_phase_compensation_enable);
    cfg.four_channel_fusion_channel_3 = parseOptionalInt(
        "four_channel_fusion_channel_3", cfg.four_channel_fusion_channel_3);
    cfg.four_channel_fusion_channel_4 = parseOptionalInt(
        "four_channel_fusion_channel_4", cfg.four_channel_fusion_channel_4);
    cfg.four_channel_fusion_squint_side = parseOptionalInt(
        "four_channel_fusion_squint_side", cfg.four_channel_fusion_squint_side);
    cfg.four_channel_carrier_phase_sign = parseOptionalInt(
        "four_channel_carrier_phase_sign", cfg.four_channel_carrier_phase_sign);
    for (int ch = 0; ch < 4; ++ch) {
        const std::string prefix = "four_channel_ch" + std::to_string(ch + 1);
        cfg.four_channel_offsets_m[static_cast<std::size_t>(ch)][0] =
            parseOptionalDouble((prefix + "_x_m").c_str(),
                                cfg.four_channel_offsets_m[static_cast<std::size_t>(ch)][0]);
        cfg.four_channel_offsets_m[static_cast<std::size_t>(ch)][1] =
            parseOptionalDouble((prefix + "_y_m").c_str(),
                                cfg.four_channel_offsets_m[static_cast<std::size_t>(ch)][1]);
        cfg.four_channel_offsets_m[static_cast<std::size_t>(ch)][2] =
            parseOptionalDouble((prefix + "_z_m").c_str(),
                                cfg.four_channel_offsets_m[static_cast<std::size_t>(ch)][2]);
    }
    cfg.new_protocol_read_channel_1 = parseOptionalInt(
        "shm_read_channel_1", cfg.new_protocol_read_channel_1);
    cfg.new_protocol_read_channel_2 = parseOptionalInt(
        "shm_read_channel_2", cfg.new_protocol_read_channel_2);
    cfg.new_protocol_file_first_beam = parseOptionalInt(
        "new_protocol_file_first_beam", cfg.new_protocol_file_first_beam);
    cfg.new_protocol_file_scan_beam_count = parseOptionalInt(
        "new_protocol_file_scan_beam_count", cfg.new_protocol_file_scan_beam_count);
    cfg.new_protocol_file_period_index = parseOptionalInt(
        "new_protocol_file_period_index", cfg.new_protocol_file_period_index);
    cfg.new_protocol_velocity_scale = parseOptionalDouble(
        "new_protocol_velocity_scale", cfg.new_protocol_velocity_scale);
    cfg.new_protocol_velocity_source = parseOptionalString(
        "new_protocol_velocity_source", cfg.new_protocol_velocity_source);
    if (cfg.new_protocol_channel_count < 1) {
        throw std::runtime_error("field <new_protocol_channel_count> must be >= 1");
    }
    if (cfg.new_protocol_file_first_beam < 1) {
        throw std::runtime_error("field <new_protocol_file_first_beam> must be >= 1");
    }
    if (cfg.new_protocol_file_scan_beam_count < 0 ||
        cfg.new_protocol_file_period_index < 0) {
        throw std::runtime_error(
            "new_protocol_file_scan_beam_count/period_index must be >= 0");
    }
    if (!std::isfinite(cfg.new_protocol_velocity_scale) ||
        !(cfg.new_protocol_velocity_scale > 0.0)) {
        throw std::runtime_error(
            "field <new_protocol_velocity_scale> must be finite and > 0");
    }
    if (cfg.new_protocol_velocity_source != "header" &&
        cfg.new_protocol_velocity_source != "position_delta") {
        throw std::runtime_error(
            "field <new_protocol_velocity_source> must be header or position_delta");
    }
    if (cfg.new_protocol_read_channel_1 < 1 ||
        cfg.new_protocol_read_channel_1 > cfg.new_protocol_channel_count ||
        cfg.new_protocol_read_channel_2 < 1 ||
        cfg.new_protocol_read_channel_2 > cfg.new_protocol_channel_count) {
        throw std::runtime_error("new_protocol_read_channel_1/2 must be within [1, new_protocol_channel_count]");
    }
    if (cfg.enable_four_channel_fusion) {
        if (cfg.new_protocol_channel_count < 4 ||
            cfg.four_channel_fusion_channel_3 < 1 ||
            cfg.four_channel_fusion_channel_3 > cfg.new_protocol_channel_count ||
            cfg.four_channel_fusion_channel_4 < 1 ||
            cfg.four_channel_fusion_channel_4 > cfg.new_protocol_channel_count ||
            cfg.four_channel_fusion_squint_side < 0 ||
            cfg.four_channel_fusion_squint_side > 1 ||
            (cfg.four_channel_carrier_phase_sign != -1 &&
             cfg.four_channel_carrier_phase_sign != 1)) {
            throw std::runtime_error("invalid four-channel fusion configuration");
        }
        for (const auto &offset : cfg.four_channel_offsets_m) {
            for (double value : offset) {
                if (!std::isfinite(value)) {
                    throw std::runtime_error("four-channel offsets must be finite");
                }
            }
        }
    }

    // 解析数字字段并填充到结构体中
    cfg.isPC = parseRequiredInt("isPC");
    cfg.hasRefFunc = parseRequiredInt("hasRefFunc");
    cfg.info_len = parseRequiredInt("info_len");
    cfg.pulse_len = parseRequiredInt("pulse_len");
    cfg.rg_len = parseRequiredInt("rg_len");
    cfg.pulse_num = parseRequiredInt("pulse_num");
    cfg.read_pulse_num = parseOptionalInt("read_pulse_num", cfg.read_pulse_num);
    cfg.read_pulse_offset = parseOptionalInt("read_pulse_offset", cfg.read_pulse_offset);
    cfg.process_pulse_num = parseOptionalInt("process_pulse_num", cfg.process_pulse_num);
    cfg.range_fft_len = parseOptionalInt("range_fft_len", cfg.range_fft_len);
    cfg.range_crop_start = parseOptionalInt("range_crop_start", cfg.range_crop_start);
    cfg.range_compress_len =
        parseOptionalInt("range_compress_len", cfg.range_compress_len);
    cfg.range_compression_window = parseOptionalString(
        "range_compression_window", cfg.range_compression_window);
    std::transform(cfg.range_compression_window.begin(),
                   cfg.range_compression_window.end(),
                   cfg.range_compression_window.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    cfg.range_compression_kaiser_beta = parseOptionalDouble(
        "range_compression_kaiser_beta", cfg.range_compression_kaiser_beta);
    cfg.range_compression_bandwidth_scale = parseOptionalDouble(
        "range_compression_bandwidth_scale", cfg.range_compression_bandwidth_scale);
    cfg.range_compression_window_normalize = parseOptionalBool(
        "range_compression_window_normalize", cfg.range_compression_window_normalize);
    cfg.azimuth_fft_window = parseOptionalString(
        "azimuth_fft_window", cfg.azimuth_fft_window);
    std::transform(cfg.azimuth_fft_window.begin(), cfg.azimuth_fft_window.end(),
                   cfg.azimuth_fft_window.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    cfg.azimuth_fft_window_normalize = parseOptionalBool(
        "azimuth_fft_window_normalize", cfg.azimuth_fft_window_normalize);
    if (cfg.range_fft_len <= 0) {
        cfg.range_fft_len = cfg.pulse_len;
    }
    if (cfg.range_compress_len <= 0) {
        cfg.range_compress_len = cfg.rg_len;
    }
    if (cfg.range_fft_len < cfg.pulse_len) {
        throw std::runtime_error(
            "field <range_fft_len> must be >= <pulse_len>");
    }
    if (cfg.range_crop_start < 0 ||
        cfg.range_crop_start + cfg.range_compress_len > cfg.range_fft_len) {
        throw std::runtime_error(
            "fields <range_crop_start> + <range_compress_len> exceed <range_fft_len>");
    }
    cfg.rg_len = cfg.range_compress_len;
    cfg.pulse_dec = parseRequiredInt("pulse_dec");
    cfg.fc = parseRequiredDouble("fc") * 1e9;  // 转换为赫兹
    cfg.Br = parseRequiredDouble("Br") * 1e6;  // 转换为赫兹
    cfg.fs = parseRequiredDouble("fs") * 1e6;  // 转换为赫兹
    cfg.Tr = parseRequiredDouble("Tr") * 1e-6; // 转换为秒
    cfg.PRF = parseRequiredDouble("PRF");
    if (cfg.range_compression_window != "none" &&
        cfg.range_compression_window != "rect" &&
        cfg.range_compression_window != "rectangular" &&
        cfg.range_compression_window != "kaiser") {
        throw std::runtime_error(
            "field <range_compression_window> must be none, rect, rectangular or kaiser");
    }
    if (cfg.azimuth_fft_window != "none" &&
        cfg.azimuth_fft_window != "hann" &&
        cfg.azimuth_fft_window != "blackman_harris") {
        throw std::runtime_error(
            "field <azimuth_fft_window> must be none, hann or blackman_harris");
    }
    if (!(cfg.range_compression_kaiser_beta >= 0.0) ||
        !std::isfinite(cfg.range_compression_kaiser_beta) ||
        !(cfg.range_compression_bandwidth_scale > 0.0) ||
        !std::isfinite(cfg.range_compression_bandwidth_scale)) {
        throw std::runtime_error("invalid range compression window configuration");
    }
    {
        std::string sampleDelaySource;
        cfg.has_sample_delay_us = parseOptionalDelayUs(
            {"sample_delay_us", "sample_delay", "sampling_delay", "recv_delay",
             "receive_delay", "rx_delay"},
            cfg.sample_delay_us,
            sampleDelaySource);
    }
    cfg.az_count = parseRequiredInt("az_count");
    cfg.beamwidth_deg = parseOptionalDouble("boshu", cfg.beamwidth_deg);
    cfg.loc_beam_gate_deg = parseOptionalDouble("loc_beam_gate_deg", cfg.loc_beam_gate_deg);
    cfg.week = parseRequiredInt("week_offset");
    cfg.d_channel = parseRequiredDouble("d_chan");
    cfg.pf = parseRequiredDouble("pf");
    cfg.R_min = parseRequiredDouble("Rmin");
    cfg.L0 = parseRequiredDouble("ref_lon");
    cfg.MT_nowz = parseRequiredDouble("ref_H");
    cfg.secBias = parseRequiredInt("secBias");
    cfg.skip_az_num = parseRequiredInt("skip_pulses");
    cfg.calib_coef = parseRequiredDouble("calib_coef");

    if (cfg.GMTI_Data_new.empty()) {
        cfg.GMTI_Data_new = cfg.GMTI_Data_add;
    }
    if ((cfg.test_mode == 1 || cfg.test_mode == 2) && cfg.test_cycle_data_paths.empty() &&
        !cfg.GMTI_Data_new.empty()) {
        // Compatibility with existing one-file functional XML. New deployed
        // XML writes the list explicitly, so the test-cycle source is always
        // visible and auditable at startup.
        cfg.test_cycle_data_paths.push_back(cfg.GMTI_Data_new);
    }

    cfg.wavepos_st = parseRequiredInt("wavepos_st");
    cfg.wavepos_ed = parseRequiredInt("wavepos_ed");
    cfg.wavepos_skip = parseRequiredInt("wavepos_skip");
    cfg.scan_min_deg = parseOptionalDouble("scan_min_deg", cfg.scan_min_deg);
    cfg.scan_max_deg = parseOptionalDouble("scan_max_deg", cfg.scan_max_deg);
    cfg.min_points = parseRequiredInt("min_points");
    cfg.min_len = parseRequiredInt("min_len");

    cfg.rg_st = parseRequiredInt("rg_st");
    cfg.rg_ed = parseRequiredInt("rg_ed");
    cfg.squint_side = parseRequiredInt("squint_side");

    cfg.roi_ll_deg[0] = parseRequiredDouble("roi_lat1");
    cfg.roi_ll_deg[1] = parseRequiredDouble("roi_lng1");
    cfg.roi_ll_deg[2] = parseRequiredDouble("roi_Ry1");
    cfg.roi_ll_deg[3] = parseRequiredDouble("roi_Rx1");

    cfg.wavepos_use_roi = parseOptionalBool("wavepos_use_roi", cfg.wavepos_use_roi);

    cfg.wavepos_parallel = parseOptionalBool("wavepos_parallel", cfg.wavepos_parallel);
    cfg.wavepos_parallel_max_workers = parseOptionalInt(
        "wavepos_parallel_max_workers", cfg.wavepos_parallel_max_workers);
    if (cfg.wavepos_parallel_max_workers <= 0) {
        throw std::runtime_error(
            "field <wavepos_parallel_max_workers> must be greater than zero");
    }

    cfg.enable_dbs_fusion = parseOptionalBool("enable_dbs_fusion", cfg.enable_dbs_fusion);
    cfg.dbs_mosaic_use_gpu = parseOptionalBool(
        "dbs_mosaic_use_gpu", cfg.dbs_mosaic_use_gpu);
    cfg.fusion_min_valid_beam_ratio = parseOptionalDouble(
        "fusion_min_valid_beam_ratio", cfg.fusion_min_valid_beam_ratio);
    if (!(cfg.fusion_min_valid_beam_ratio > 0.0 &&
          cfg.fusion_min_valid_beam_ratio <= 1.0)) {
        throw std::runtime_error(
            "field <fusion_min_valid_beam_ratio> must be within (0, 1]");
    }

    {
        cfg.dbs_out_res_m = parseOptionalDouble("raw_fenbianlv", cfg.dbs_out_res_m);
        cfg.dbs_beam_skip = std::max(1, parseOptionalInt("n_tiaoguo", cfg.dbs_beam_skip));
        cfg.dbs_range_skip = std::max(1, parseOptionalInt("len_tiaoguo", cfg.dbs_range_skip));
        cfg.dbs_interp_mode = parseOptionalInt("dbs_interp_mode", cfg.dbs_interp_mode);
        cfg.dbs_mosaic_mode = parseOptionalString(
            "dbs_mosaic_mode", cfg.dbs_mosaic_mode);
        std::transform(cfg.dbs_mosaic_mode.begin(), cfg.dbs_mosaic_mode.end(),
                       cfg.dbs_mosaic_mode.begin(),
                       [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        if (cfg.dbs_mosaic_mode != "max" &&
            cfg.dbs_mosaic_mode != "weighted") {
            throw std::runtime_error(
                "field <dbs_mosaic_mode> must be max or weighted");
        }
        cfg.dbs_max_mosaic_pixels = parseOptionalSizeT("dbs_max_mosaic_pixels",
                                                       cfg.dbs_max_mosaic_pixels);
        cfg.dbs_mosaic_margin_ratio = std::max(
            0.0, parseOptionalDouble("dbs_mosaic_margin_ratio",
                                     cfg.dbs_mosaic_margin_ratio));
        cfg.dbs_mosaic_margin_m = std::max(
            0.0, parseOptionalDouble("dbs_mosaic_margin_m",
                                     cfg.dbs_mosaic_margin_m));
    }

    cfg.estimate_error_angle = parseOptionalBool("estimate_error_angle", cfg.estimate_error_angle);

    cfg.lat_st = parseRequiredDouble("lat_st");
    cfg.lat_ed = parseRequiredDouble("lat_ed");
    cfg.lon_st = parseRequiredDouble("lon_st");
    cfg.lon_ed = parseRequiredDouble("lon_ed");
    cfg.squint_angle = parseOptionalDouble("squint_angle", cfg.squint_angle);

    // 可选调试配置：节点缺失时使用结构体默认值
    cfg.mt_sort_by_utc = parseOptionalBool("mt_sort_by_utc", cfg.mt_sort_by_utc);
    cfg.track_debug_level = parseOptionalInt("track_debug_level", cfg.track_debug_level);
    cfg.track_debug_frames = parseOptionalInt("track_debug_frames", cfg.track_debug_frames);
    cfg.track_debug_points = parseOptionalInt("track_debug_points", cfg.track_debug_points);
    cfg.track_idx_window = parseOptionalInt("track_idx_window", cfg.track_idx_window);
    cfg.track_truth_threshold = parseOptionalInt("track_truth_threshold", cfg.track_truth_threshold);
    cfg.track_distance_mode = parseOptionalInt("track_distance_mode", cfg.track_distance_mode);
    cfg.track_assignment_mode = parseOptionalInt("track_assignment_mode", cfg.track_assignment_mode);
    cfg.track_output_state_source =
        parseOptionalString("track_output_state_source", cfg.track_output_state_source);
    std::string canonical_output_source;
    if (!gmti::tracking::canonicalizeTrackOutputStateSource(
            cfg.track_output_state_source, canonical_output_source)) {
        throw std::runtime_error(
            "field <track_output_state_source> must be measurement, prediction, "
            "or kalman_filtered");
    }
    // The online implementation is a forward Kalman posterior, not an offline
    // RTS smoother.  User-facing aliases are stored under the honest canonical name.
    cfg.track_output_state_source = canonical_output_source;
    cfg.track_use_distance_cost = parseOptionalBool("track_use_distance_cost",
                                                     cfg.track_use_distance_cost);
    cfg.track_use_speed_cost = parseOptionalBool("track_use_speed_cost",
                                                  cfg.track_use_speed_cost);
    cfg.track_use_heading_cost = parseOptionalBool("track_use_heading_cost",
                                                    cfg.track_use_heading_cost);
    cfg.track_use_detection_speed_cost =
        parseOptionalBool("track_use_detection_speed_cost",
                          cfg.track_use_detection_speed_cost);
    cfg.track_distance_weight = parseOptionalDouble("track_distance_weight",
                                                     cfg.track_distance_weight);
    cfg.track_detection_speed_weight =
        parseOptionalDouble("track_detection_speed_weight",
                            cfg.track_detection_speed_weight);
    cfg.track_use_euclidean_gate = parseOptionalBool("track_use_euclidean_gate",
                                                      cfg.track_use_euclidean_gate);
    cfg.track_use_mahalanobis_gate = parseOptionalBool("track_use_mahalanobis_gate",
                                                        cfg.track_use_mahalanobis_gate);
    cfg.track_use_max_speed_gate = parseOptionalBool("track_use_max_speed_gate",
                                                      cfg.track_use_max_speed_gate);
    cfg.track_use_heading_gate = parseOptionalBool("track_use_heading_gate",
                                                    cfg.track_use_heading_gate);
    cfg.track_use_detection_speed_gate =
        parseOptionalBool("track_use_detection_speed_gate",
                          cfg.track_use_detection_speed_gate);
    cfg.track_confirm_window = parseOptionalInt("track_confirm_window", cfg.track_idx_window);
    cfg.track_confirm_hits = parseOptionalInt("track_confirm_hits", cfg.track_truth_threshold);
    cfg.track_max_missed = parseOptionalInt("track_max_missed", cfg.track_max_missed);
    cfg.track_tentative_max_missed = parseOptionalInt("track_tentative_max_missed",
                                                       cfg.track_tentative_max_missed);
    cfg.track_default_dt = parseOptionalDouble("track_default_dt", cfg.track_default_dt);
    cfg.track_chi2_gate = parseOptionalDouble("track_chi2_gate", cfg.track_chi2_gate);
    cfg.track_tentative_gate_scale = parseOptionalDouble("track_tentative_gate_scale",
                                                         cfg.track_tentative_gate_scale);
    cfg.track_tentative_chi2_scale = parseOptionalDouble("track_tentative_chi2_scale",
                                                         cfg.track_tentative_chi2_scale);
    cfg.track_dummy_cost = parseOptionalDouble("track_dummy_cost", cfg.track_dummy_cost);
    cfg.track_invalid_cost = parseOptionalDouble("track_invalid_cost", cfg.track_invalid_cost);
    cfg.track_allow_equal_dummy_cost =
        parseOptionalBool("track_allow_equal_dummy_cost",
                          cfg.track_allow_equal_dummy_cost);
    cfg.track_allow_detection_reuse =
        parseOptionalBool("track_allow_detection_reuse",
                          cfg.track_allow_detection_reuse);
    cfg.track_linearity_window = parseOptionalInt("track_linearity_window", cfg.track_linearity_window);
    cfg.track_min_linearity_confirm = parseOptionalDouble("track_min_linearity_confirm",
                                                          cfg.track_min_linearity_confirm);
    cfg.track_speed_smooth_weight = parseOptionalDouble("track_speed_smooth_weight",
                                                        cfg.track_speed_smooth_weight);
    cfg.track_heading_weight = parseOptionalDouble("track_heading_weight", cfg.track_heading_weight);
    cfg.track_heading_min_displacement_m = parseOptionalDouble(
        "track_heading_min_displacement_m",
        cfg.track_heading_min_displacement_m);
    if (!std::isfinite(cfg.track_heading_min_displacement_m) ||
        cfg.track_heading_min_displacement_m < 0.0) {
        throw std::runtime_error(
            "track_heading_min_displacement_m must be finite and >= 0");
    }
    cfg.track_heading_gate_deg = parseOptionalDouble("track_heading_gate_deg",
                                                      cfg.track_heading_gate_deg);
    cfg.track_detection_speed_gate_mps =
        parseOptionalDouble("track_detection_speed_gate_mps",
                            cfg.track_detection_speed_gate_mps);
    cfg.track_euclidean_cost_scale_m =
        parseOptionalDouble("track_euclidean_cost_scale_m",
                            cfg.track_euclidean_cost_scale_m);
    cfg.track_mahalanobis_cost_scale =
        parseOptionalDouble("track_mahalanobis_cost_scale",
                            cfg.track_mahalanobis_cost_scale);
    cfg.track_speed_cost_scale_mps =
        parseOptionalDouble("track_speed_cost_scale_mps",
                            cfg.track_speed_cost_scale_mps);
    cfg.track_heading_cost_scale_deg =
        parseOptionalDouble("track_heading_cost_scale_deg",
                            cfg.track_heading_cost_scale_deg);
    cfg.track_detection_speed_cost_scale_mps =
        parseOptionalDouble("track_detection_speed_cost_scale_mps",
                            cfg.track_detection_speed_cost_scale_mps);
    cfg.track_process_noise_pos = parseOptionalDouble("track_process_noise_pos",
                                                      cfg.track_process_noise_pos);
    cfg.track_process_noise_vel = parseOptionalDouble("track_process_noise_vel",
                                                      cfg.track_process_noise_vel);
    cfg.track_measurement_noise_pos = parseOptionalDouble("track_measurement_noise_pos",
                                                          cfg.track_measurement_noise_pos);
    cfg.track_debug_dump = parseOptionalBool("track_debug_dump", cfg.track_debug_dump);
    cfg.track_debug_dir = parseOptionalString("track_debug_dir", cfg.track_debug_dir);
    cfg.track_debug_dump_level = parseOptionalInt("track_debug_dump_level",
                                                  cfg.track_debug_dump_level);
    cfg.detection_results_csv_dump = parseOptionalBool(
        "detection_results_csv_dump", cfg.detection_results_csv_dump);
    cfg.stage2_random_seed = parseOptionalInt(
        "stage2_random_seed", cfg.stage2_random_seed);
    cfg.stage2_case_id = parseOptionalString(
        "stage2_case_id", cfg.stage2_case_id);
    cfg.stage2_period_id = parseOptionalInt(
        "stage2_period_id", cfg.stage2_period_id);
    cfg.low_radial_velocity_filter_enabled = parseOptionalBool(
        "low_radial_velocity_filter_enabled", cfg.low_radial_velocity_filter_enabled);
    cfg.low_radial_velocity_threshold_mps = parseOptionalDouble(
        "low_radial_velocity_threshold_mps", cfg.low_radial_velocity_threshold_mps);
    if (!std::isfinite(cfg.low_radial_velocity_threshold_mps) ||
        cfg.low_radial_velocity_threshold_mps < 0.0) {
        throw std::runtime_error(
            "field <low_radial_velocity_threshold_mps> must be finite and >= 0");
    }
    cfg.p38_slope_upper_bound_filter_enabled = parseOptionalBool(
        "p38_slope_upper_bound_filter_enabled",
        cfg.p38_slope_upper_bound_filter_enabled);
    cfg.p38_slope_upper_bound_rad_per_hz = parseOptionalDouble(
        "p38_slope_upper_bound_rad_per_hz",
        cfg.p38_slope_upper_bound_rad_per_hz);
    if (!std::isfinite(cfg.p38_slope_upper_bound_rad_per_hz)) {
        throw std::runtime_error(
            "field <p38_slope_upper_bound_rad_per_hz> must be finite");
    }
    cfg.adjacent_beam_duplicate_suppression_enabled = parseOptionalBool(
        "adjacent_beam_duplicate_suppression_enabled",
        cfg.adjacent_beam_duplicate_suppression_enabled);
    cfg.adjacent_beam_duplicate_position_gate_m = parseOptionalDouble(
        "adjacent_beam_duplicate_position_gate_m",
        cfg.adjacent_beam_duplicate_position_gate_m);
    cfg.adjacent_beam_duplicate_velocity_gate_mps = parseOptionalDouble(
        "adjacent_beam_duplicate_velocity_gate_mps",
        cfg.adjacent_beam_duplicate_velocity_gate_mps);
    if (!std::isfinite(cfg.adjacent_beam_duplicate_position_gate_m) ||
        cfg.adjacent_beam_duplicate_position_gate_m < 0.0 ||
        !std::isfinite(cfg.adjacent_beam_duplicate_velocity_gate_mps) ||
        cfg.adjacent_beam_duplicate_velocity_gate_mps < 0.0) {
        throw std::runtime_error(
            "adjacent-beam duplicate suppression gates must be finite and >= 0");
    }
    cfg.debug_pc_peak = parseOptionalBool("debug_pc_peak", cfg.debug_pc_peak);
    cfg.pc_peak_scene_truth = parseOptionalString("pc_peak_scene_truth", cfg.pc_peak_scene_truth);
    if (cfg.pc_peak_scene_truth.empty()) {
        cfg.pc_peak_scene_truth = parseOptionalString("scene_truth", cfg.pc_peak_scene_truth);
    }
    cfg.motion_comp_enable = parseOptionalBool("motion_comp_enable", cfg.motion_comp_enable);
    cfg.motion_comp_apply_to_localization =
        parseOptionalBool("motion_comp_apply_to_localization",
                          cfg.motion_comp_apply_to_localization);
    cfg.motion_comp_analytic_enable =
        parseOptionalBool("motion_comp_analytic_enable", cfg.motion_comp_analytic_enable);
    cfg.motion_comp_use_row_doppler =
        parseOptionalBool("motion_comp_use_row_doppler", cfg.motion_comp_use_row_doppler);
    {
        const std::string motion_comp_solver = getOptionalText("motion_comp_solver");
        if (!motion_comp_solver.empty()) {
            cfg.motion_comp_solver = motion_comp_solver;
        } else {
            cfg.motion_comp_solver = cfg.motion_comp_analytic_enable ? "analytic" : "old";
        }
    }
    cfg.motion_comp_iter = parseOptionalInt("motion_comp_iter", cfg.motion_comp_iter);
    cfg.motion_comp_iter_tol_mps =
        parseOptionalDouble("motion_comp_iter_tol_mps", cfg.motion_comp_iter_tol_mps);
    cfg.p38_enhanced_enable =
        parseOptionalBool("p38_enhanced_enable", cfg.p38_enhanced_enable);
    cfg.p38_refit_enable = parseOptionalBool("p38_refit_enable", cfg.p38_refit_enable);
    cfg.p38_refit_row_guard_bins =
        parseOptionalInt("p38_refit_row_guard_bins", cfg.p38_refit_row_guard_bins);
    cfg.p38_refit_range_guard_bins =
        parseOptionalInt("p38_refit_range_guard_bins", cfg.p38_refit_range_guard_bins);
    cfg.p38_refit_top_power_frac =
        parseOptionalDouble("p38_refit_top_power_frac", cfg.p38_refit_top_power_frac);
    cfg.p38_refit_min_sample_count =
        parseOptionalInt("p38_refit_min_sample_count", cfg.p38_refit_min_sample_count);
    cfg.p38_refit_min_inlier_ratio =
        parseOptionalDouble("p38_refit_min_inlier_ratio", cfg.p38_refit_min_inlier_ratio);
    cfg.p38_refit_max_rmse_rad =
        parseOptionalDouble("p38_refit_max_rmse_rad", cfg.p38_refit_max_rmse_rad);
    cfg.p38_refit_max_delta_k =
        parseOptionalDouble("p38_refit_max_delta_k", cfg.p38_refit_max_delta_k);
    cfg.p38_refit_max_delta_b_rad =
        parseOptionalDouble("p38_refit_max_delta_b_rad", cfg.p38_refit_max_delta_b_rad);
    cfg.p38_min_peak_row_energy_fraction = parseOptionalDouble(
        "p38_min_peak_row_energy_fraction", cfg.p38_min_peak_row_energy_fraction);
    cfg.p38_theory_guided_fallback = parseOptionalBool(
        "p38_theory_guided_fallback", cfg.p38_theory_guided_fallback);
    cfg.p38_theory_prior_relative_span = parseOptionalDouble(
        "p38_theory_prior_relative_span", cfg.p38_theory_prior_relative_span);
    cfg.p38_theory_prior_trigger_relative_error = parseOptionalDouble(
        "p38_theory_prior_trigger_relative_error",
        cfg.p38_theory_prior_trigger_relative_error);
    cfg.p38_diagnostics_dump = parseOptionalBool(
        "p38_diagnostics_dump", cfg.p38_diagnostics_dump);
    if (!(cfg.p38_theory_prior_relative_span > 0.0) ||
        !(cfg.p38_theory_prior_trigger_relative_error >= 0.0)) {
        throw std::runtime_error("invalid p38 theory-guided fallback configuration");
    }
    cfg.ati_velocity_sign = parseOptionalInt("ati_velocity_sign", cfg.ati_velocity_sign);
    cfg.ati_phase_to_velocity_sign = cfg.ati_velocity_sign;
    cfg.ati_phase_to_velocity_sign =
        parseOptionalInt("ati_phase_to_velocity_sign", cfg.ati_phase_to_velocity_sign);
    cfg.motion_doppler_axis_sign =
        parseOptionalInt("motion_doppler_axis_sign", cfg.motion_doppler_axis_sign);
    {
        const std::string model = getOptionalText("two_channel_phase_model");
        if (!model.empty()) {
            cfg.two_channel_phase_model = model;
        }
    }
    cfg.rx_baseline_sign = parseOptionalInt("rx_baseline_sign", cfg.rx_baseline_sign);
    cfg.channel_phase_sign = parseOptionalInt("channel_phase_sign", cfg.channel_phase_sign);
    cfg.ati_phase_bias_rad = parseOptionalDouble("ati_phase_bias_rad", cfg.ati_phase_bias_rad);
    cfg.channel_calibration_enable = parseOptionalBool(
        "channel_calibration_enable", cfg.channel_calibration_enable);
    cfg.channel_calibration_apply_to_localization = parseOptionalBool(
        "channel_calibration_apply_to_localization",
        cfg.channel_calibration_apply_to_localization);
    cfg.channel_calibration_reference_valid = parseOptionalBool(
        "channel_calibration_reference_valid",
        cfg.channel_calibration_reference_valid);
    cfg.channel_calibration_reference_phase_rad = parseOptionalDouble(
        "channel_calibration_reference_phase_rad",
        cfg.channel_calibration_reference_phase_rad);
    cfg.channel_calibration_range_stride = parseOptionalInt(
        "channel_calibration_range_stride", cfg.channel_calibration_range_stride);
    cfg.channel_calibration_min_sample_count = parseOptionalInt(
        "channel_calibration_min_sample_count", cfg.channel_calibration_min_sample_count);
    cfg.channel_calibration_min_coherence = parseOptionalDouble(
        "channel_calibration_min_coherence", cfg.channel_calibration_min_coherence);
    cfg.channel_calibration_outlier_threshold_rad = parseOptionalDouble(
        "channel_calibration_outlier_threshold_rad",
        cfg.channel_calibration_outlier_threshold_rad);
    cfg.channel_calibration_max_rmse_rad = parseOptionalDouble(
        "channel_calibration_max_rmse_rad", cfg.channel_calibration_max_rmse_rad);
    cfg.channel_calibration_max_range_shift_bins = parseOptionalDouble(
        "channel_calibration_max_range_shift_bins",
        cfg.channel_calibration_max_range_shift_bins);
    cfg.channel_calibration_min_range_correlation = parseOptionalDouble(
        "channel_calibration_min_range_correlation",
        cfg.channel_calibration_min_range_correlation);
    if (cfg.channel_calibration_range_stride <= 0 ||
        cfg.channel_calibration_min_sample_count <= 0 ||
        !(cfg.channel_calibration_min_coherence >= 0.0 &&
          cfg.channel_calibration_min_coherence <= 1.0) ||
        !(cfg.channel_calibration_outlier_threshold_rad > 0.0) ||
        !(cfg.channel_calibration_max_rmse_rad > 0.0) ||
        !std::isfinite(cfg.channel_calibration_max_range_shift_bins) ||
        !(cfg.channel_calibration_min_range_correlation >= 0.0 &&
          cfg.channel_calibration_min_range_correlation <= 1.0) ||
        !std::isfinite(cfg.channel_calibration_reference_phase_rad)) {
        throw std::runtime_error("invalid channel calibration configuration");
    }
    cfg.ati_vmax_mps = parseOptionalDouble("ati_vmax_mps", cfg.ati_vmax_mps);
    cfg.motion_comp_denom_min =
        parseOptionalDouble("motion_comp_denom_min", cfg.motion_comp_denom_min);
    cfg.motion_comp_root_grid_step_mps =
        parseOptionalDouble("motion_comp_root_grid_step_mps", cfg.motion_comp_root_grid_step_mps);
    cfg.motion_comp_root_cost_max =
        parseOptionalDouble("motion_comp_root_cost_max", cfg.motion_comp_root_cost_max);
    cfg.motion_comp_debug = parseOptionalBool("motion_comp_debug", cfg.motion_comp_debug);
    cfg.velocity_ambiguity_enable =
        parseOptionalBool("velocity_ambiguity_enable", cfg.velocity_ambiguity_enable);
    cfg.velocity_search_min_mps =
        parseOptionalDouble("velocity_search_min_mps", cfg.velocity_search_min_mps);
    cfg.velocity_search_max_mps =
        parseOptionalDouble("velocity_search_max_mps", cfg.velocity_search_max_mps);
    cfg.velocity_max_doppler_order =
        parseOptionalInt("velocity_max_doppler_order", cfg.velocity_max_doppler_order);
    cfg.velocity_max_phase_order =
        parseOptionalInt("velocity_max_phase_order", cfg.velocity_max_phase_order);
    cfg.velocity_max_candidates =
        parseOptionalInt("velocity_max_candidates", cfg.velocity_max_candidates);
    cfg.velocity_beam_gate_deg =
        parseOptionalDouble("velocity_beam_gate_deg", cfg.velocity_beam_gate_deg);
    cfg.velocity_phase_sigma_rad =
        parseOptionalDouble("velocity_phase_sigma_rad", cfg.velocity_phase_sigma_rad);
    cfg.velocity_beam_sigma_deg =
        parseOptionalDouble("velocity_beam_sigma_deg", cfg.velocity_beam_sigma_deg);
    cfg.velocity_cost_margin_min =
        parseOptionalDouble("velocity_cost_margin_min", cfg.velocity_cost_margin_min);
    cfg.velocity_equivalent_cost_tolerance = parseOptionalDouble(
        "velocity_equivalent_cost_tolerance", cfg.velocity_equivalent_cost_tolerance);
    cfg.velocity_speed_prior_mps =
        parseOptionalDouble("velocity_speed_prior_mps", cfg.velocity_speed_prior_mps);
    cfg.velocity_speed_prior_sigma_mps = parseOptionalDouble(
        "velocity_speed_prior_sigma_mps", cfg.velocity_speed_prior_sigma_mps);
    if (!(cfg.velocity_search_min_mps < cfg.velocity_search_max_mps) ||
        cfg.velocity_max_doppler_order < 0 ||
        cfg.velocity_max_phase_order < 0 ||
        cfg.velocity_max_candidates <= 0 ||
        !(cfg.velocity_phase_sigma_rad > 0.0) ||
        !(cfg.velocity_beam_sigma_deg > 0.0) ||
        !(cfg.velocity_cost_margin_min >= 0.0) ||
        !(cfg.velocity_equivalent_cost_tolerance >= 0.0) ||
        !(cfg.velocity_speed_prior_sigma_mps >= 0.0)) {
        throw std::runtime_error("invalid velocity ambiguity configuration");
    }
    cfg.csi_bypass_enable =
        parseOptionalBool("csi_bypass_enable", cfg.csi_bypass_enable);
    cfg.csi_metrics_enable =
        parseOptionalBool("csi_metrics_enable", cfg.csi_metrics_enable);
    cfg.csi_metrics_dump_power_maps = parseOptionalBool(
        "csi_metrics_dump_power_maps", cfg.csi_metrics_dump_power_maps);
    cfg.csi_metrics_dump_intermediate_maps = parseOptionalBool(
        "csi_metrics_dump_intermediate_maps", cfg.csi_metrics_dump_intermediate_maps);
    cfg.csi_metrics_beam_id =
        parseOptionalInt("csi_metrics_beam_id", cfg.csi_metrics_beam_id);
    cfg.csi_cancellation_mode = parseOptionalString(
        "csi_cancellation_mode", cfg.csi_cancellation_mode);
    std::transform(cfg.csi_cancellation_mode.begin(), cfg.csi_cancellation_mode.end(),
                   cfg.csi_cancellation_mode.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    if (cfg.csi_cancellation_mode != "legacy_min_magnitude" &&
        cfg.csi_cancellation_mode != "row_complex_ls" &&
        cfg.csi_cancellation_mode != "row_phase_ls_linear" &&
        cfg.csi_cancellation_mode != "row_phase_ls_min_magnitude") {
        throw std::runtime_error(
            "field <csi_cancellation_mode> must be legacy_min_magnitude, "
            "row_complex_ls, row_phase_ls_linear, or "
            "row_phase_ls_min_magnitude");
    }
    cfg.csi_row_coherence_gate_enable = parseOptionalBool(
        "csi_row_coherence_gate_enable", cfg.csi_row_coherence_gate_enable);
    cfg.csi_row_coherence_min = parseOptionalDouble(
        "csi_row_coherence_min", cfg.csi_row_coherence_min);
    if (!std::isfinite(cfg.csi_row_coherence_min) ||
        cfg.csi_row_coherence_min < 0.0 || cfg.csi_row_coherence_min > 1.0) {
        throw std::runtime_error(
            "field <csi_row_coherence_min> must be finite and within [0,1]");
    }
    cfg.csi_subtraction_gain = parseOptionalDouble(
        "csi_subtraction_gain", cfg.csi_subtraction_gain);
    if (!std::isfinite(cfg.csi_subtraction_gain) ||
        cfg.csi_subtraction_gain < 0.0 || cfg.csi_subtraction_gain > 1.0) {
        throw std::runtime_error(
            "field <csi_subtraction_gain> must be finite and within [0,1]");
    }
    cfg.csi_range_phase_correction_enable = parseOptionalBool(
        "csi_range_phase_correction_enable", cfg.csi_range_phase_correction_enable);
    cfg.paired_raw_range_phase_override_f32 = parseOptionalString(
        "paired_raw_range_phase_override_f32", cfg.paired_raw_range_phase_override_f32);
    cfg.paired_csi_range_phase_override_f32 = parseOptionalString(
        "paired_csi_range_phase_override_f32", cfg.paired_csi_range_phase_override_f32);
    if (cfg.paired_raw_range_phase_override_f32.empty() !=
        cfg.paired_csi_range_phase_override_f32.empty()) {
        throw std::runtime_error(
            "paired_raw_range_phase_override_f32 and "
            "paired_csi_range_phase_override_f32 must be supplied together");
    }
    cfg.metrics_target_half_range_bins = parseOptionalInt(
        "metrics_target_half_range_bins", cfg.metrics_target_half_range_bins);
    cfg.metrics_target_half_doppler_bins = parseOptionalInt(
        "metrics_target_half_doppler_bins", cfg.metrics_target_half_doppler_bins);
    cfg.metrics_guard_range_bins = parseOptionalInt(
        "metrics_guard_range_bins", cfg.metrics_guard_range_bins);
    cfg.metrics_guard_doppler_bins = parseOptionalInt(
        "metrics_guard_doppler_bins", cfg.metrics_guard_doppler_bins);
    cfg.metrics_background_range_bins = parseOptionalInt(
        "metrics_background_range_bins", cfg.metrics_background_range_bins);
    cfg.metrics_background_doppler_bins = parseOptionalInt(
        "metrics_background_doppler_bins", cfg.metrics_background_doppler_bins);
    cfg.metrics_strong_peak_threshold_db = parseOptionalDouble(
        "metrics_strong_peak_threshold_db", cfg.metrics_strong_peak_threshold_db);
    cfg.metrics_min_valid_background_cells = parseOptionalInt(
        "metrics_min_valid_background_cells", cfg.metrics_min_valid_background_cells);
    cfg.cfar_guard_cells = parseOptionalInt(
        "cfar_guard_cells", cfg.cfar_guard_cells);
    cfg.cfar_background_cells = parseOptionalInt(
        "cfar_background_cells", cfg.cfar_background_cells);
    cfg.cfar_doppler_circular = parseOptionalBool(
        "cfar_doppler_circular", cfg.cfar_doppler_circular);
    cfg.cluster_strong_small_enable = parseOptionalBool(
        "cluster_strong_small_enable", cfg.cluster_strong_small_enable);
    cfg.cluster_strong_small_min_points = parseOptionalInt(
        "cluster_strong_small_min_points", cfg.cluster_strong_small_min_points);
    cfg.cluster_strong_small_peak_over_median_db = parseOptionalDouble(
        "cluster_strong_small_peak_over_median_db",
        cfg.cluster_strong_small_peak_over_median_db);
    cfg.cluster_max_phase_std_rad = parseOptionalDouble(
        "cluster_max_phase_std_rad", cfg.cluster_max_phase_std_rad);
    cfg.cluster_max_range_gap = parseOptionalInt(
        "cluster_max_range_gap", cfg.cluster_max_range_gap);
    cfg.cfar_type = parseOptionalString("cfar_type", cfg.cfar_type);
    std::transform(cfg.cfar_type.begin(), cfg.cfar_type.end(), cfg.cfar_type.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::toupper(ch)); });
    cfg.csi_detection_band_mode = parseOptionalString(
        "csi_detection_band_mode", cfg.csi_detection_band_mode);
    std::transform(
        cfg.csi_detection_band_mode.begin(), cfg.csi_detection_band_mode.end(),
        cfg.csi_detection_band_mode.begin(),
        [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    cfg.dynamic_cfar_enable =
        parseOptionalBool("dynamic_cfar_enable", cfg.dynamic_cfar_enable);
    cfg.csi_split_boundary_guard_rows = parseOptionalInt(
        "csi_split_boundary_guard_rows", cfg.csi_split_boundary_guard_rows);
    cfg.csi_split_merge_doppler_bins = parseOptionalInt(
        "csi_split_merge_doppler_bins", cfg.csi_split_merge_doppler_bins);
    cfg.csi_split_merge_range_bins = parseOptionalInt(
        "csi_split_merge_range_bins", cfg.csi_split_merge_range_bins);
    cfg.csi_split_in_band_cfar_type = parseOptionalString(
        "csi_split_in_band_cfar_type", cfg.csi_split_in_band_cfar_type);
    std::transform(cfg.csi_split_in_band_cfar_type.begin(),
                   cfg.csi_split_in_band_cfar_type.end(),
                   cfg.csi_split_in_band_cfar_type.begin(),
                   [](unsigned char ch) {
                       return static_cast<char>(std::toupper(ch));
                   });
    cfg.csi_split_small_cluster_enable = parseOptionalBool(
        "csi_split_small_cluster_enable", cfg.csi_split_small_cluster_enable);
    cfg.csi_split_small_cluster_min_points = parseOptionalInt(
        "csi_split_small_cluster_min_points",
        cfg.csi_split_small_cluster_min_points);
    cfg.csi_split_small_cluster_peak_over_median_db = parseOptionalDouble(
        "csi_split_small_cluster_peak_over_median_db",
        cfg.csi_split_small_cluster_peak_over_median_db);
    cfg.csi_split_small_cluster_near_doppler_rows = parseOptionalInt(
        "csi_split_small_cluster_near_doppler_rows",
        cfg.csi_split_small_cluster_near_doppler_rows);
    cfg.csi_split_small_cluster_near_range_bins = parseOptionalInt(
        "csi_split_small_cluster_near_range_bins",
        cfg.csi_split_small_cluster_near_range_bins);
    cfg.csi_split_vertical_line_filter_enable = parseOptionalBool(
        "csi_split_vertical_line_filter_enable",
        cfg.csi_split_vertical_line_filter_enable);
    cfg.csi_split_vertical_line_min_doppler_rows = parseOptionalInt(
        "csi_split_vertical_line_min_doppler_rows",
        cfg.csi_split_vertical_line_min_doppler_rows);
    cfg.csi_split_vertical_line_max_range_bins = parseOptionalInt(
        "csi_split_vertical_line_max_range_bins",
        cfg.csi_split_vertical_line_max_range_bins);
    cfg.csi_split_vertical_line_peak_over_median_db = parseOptionalDouble(
        "csi_split_vertical_line_peak_over_median_db",
        cfg.csi_split_vertical_line_peak_over_median_db);
    cfg.csi_split_out_of_band_phase_filter_enable = parseOptionalBool(
        "csi_split_out_of_band_phase_filter_enable",
        cfg.csi_split_out_of_band_phase_filter_enable);
    cfg.csi_split_out_of_band_phase_max_std_rad = parseOptionalDouble(
        "csi_split_out_of_band_phase_max_std_rad",
        cfg.csi_split_out_of_band_phase_max_std_rad);
    cfg.csi_channel_alignment_mode = parseOptionalString(
        "csi_channel_alignment_mode", cfg.csi_channel_alignment_mode);
    std::transform(
        cfg.csi_channel_alignment_mode.begin(), cfg.csi_channel_alignment_mode.end(),
        cfg.csi_channel_alignment_mode.begin(),
        [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    cfg.cfar_exclude_row_start = parseOptionalInt(
        "cfar_exclude_row_start", cfg.cfar_exclude_row_start);
    cfg.cfar_exclude_row_end = parseOptionalInt(
        "cfar_exclude_row_end", cfg.cfar_exclude_row_end);
    cfg.doppler_center_robust_enable = parseOptionalBool(
        "doppler_center_robust_enable", cfg.doppler_center_robust_enable);
    cfg.doppler_center_trim_top_fraction = parseOptionalDouble(
        "doppler_center_trim_top_fraction", cfg.doppler_center_trim_top_fraction);
    cfg.doppler_center_min_valid_range_bins = parseOptionalInt(
        "doppler_center_min_valid_range_bins", cfg.doppler_center_min_valid_range_bins);
    cfg.doppler_center_theory_guard_enable = parseOptionalBool(
        "doppler_center_theory_guard_enable", cfg.doppler_center_theory_guard_enable);
    cfg.doppler_center_theory_max_error_hz = parseOptionalDouble(
        "doppler_center_theory_max_error_hz", cfg.doppler_center_theory_max_error_hz);
    cfg.doppler_center_override_hz = parseOptionalDouble(
        "doppler_center_override_hz", cfg.doppler_center_override_hz);
    cfg.p38_csi_override_k_rad_per_hz = parseOptionalDouble(
        "p38_csi_override_k_rad_per_hz", cfg.p38_csi_override_k_rad_per_hz);
    cfg.p38_csi_override_b_rad = parseOptionalDouble(
        "p38_csi_override_b_rad", cfg.p38_csi_override_b_rad);
    if (cfg.metrics_target_half_range_bins < 0 ||
        cfg.metrics_target_half_doppler_bins < 0 ||
        cfg.metrics_guard_range_bins < 0 ||
        cfg.metrics_guard_doppler_bins < 0 ||
        cfg.metrics_background_range_bins < 0 ||
        cfg.metrics_background_doppler_bins < 0 ||
        cfg.metrics_min_valid_background_cells <= 0 ||
        !std::isfinite(cfg.metrics_strong_peak_threshold_db) ||
        cfg.cfar_guard_cells < 0 || cfg.cfar_background_cells <= 0 ||
        cfg.cluster_strong_small_min_points < 1 ||
        cfg.cluster_strong_small_min_points > cfg.min_points ||
        !std::isfinite(cfg.cluster_strong_small_peak_over_median_db) ||
        cfg.cluster_strong_small_peak_over_median_db < 0.0 ||
        !std::isfinite(cfg.cluster_max_phase_std_rad) ||
        cfg.cluster_max_phase_std_rad < 0.0 ||
        cfg.cluster_max_range_gap < 1 ||
        (cfg.cfar_type != "CA" && cfg.cfar_type != "GO") ||
        (cfg.csi_split_in_band_cfar_type != "CA" &&
         cfg.csi_split_in_band_cfar_type != "GO") ||
        cfg.csi_split_small_cluster_min_points < 1 ||
        cfg.csi_split_small_cluster_min_points > cfg.min_points ||
        !std::isfinite(cfg.csi_split_small_cluster_peak_over_median_db) ||
        cfg.csi_split_small_cluster_peak_over_median_db < 0.0 ||
        cfg.csi_split_small_cluster_near_doppler_rows < 1 ||
        cfg.csi_split_small_cluster_near_range_bins < 1 ||
        cfg.csi_split_vertical_line_min_doppler_rows < 1 ||
        cfg.csi_split_vertical_line_max_range_bins < 1 ||
        !std::isfinite(cfg.csi_split_vertical_line_peak_over_median_db) ||
        cfg.csi_split_vertical_line_peak_over_median_db < 0.0 ||
        (cfg.csi_detection_band_mode != "dynamic" &&
         cfg.csi_detection_band_mode != "full" &&
         cfg.csi_detection_band_mode != "split" &&
         cfg.csi_detection_band_mode != "union") ||
        cfg.csi_split_boundary_guard_rows < 0 ||
        cfg.csi_split_merge_doppler_bins < 0 ||
        cfg.csi_split_merge_range_bins < 0 ||
        !std::isfinite(cfg.csi_split_out_of_band_phase_max_std_rad) ||
        cfg.csi_split_out_of_band_phase_max_std_rad < 0.0 ||
        (cfg.csi_channel_alignment_mode != "none" &&
         cfg.csi_channel_alignment_mode != "integer_equivalent_delay") ||
        ((cfg.cfar_exclude_row_start < 0) != (cfg.cfar_exclude_row_end < 0)) ||
        (cfg.cfar_exclude_row_start >= 0 &&
         cfg.cfar_exclude_row_start > cfg.cfar_exclude_row_end) ||
        !(cfg.doppler_center_trim_top_fraction >= 0.0 &&
          cfg.doppler_center_trim_top_fraction < 1.0) ||
        cfg.doppler_center_min_valid_range_bins <= 0 ||
        !std::isfinite(cfg.doppler_center_theory_max_error_hz) ||
        (!std::isfinite(cfg.doppler_center_override_hz) &&
         !std::isnan(cfg.doppler_center_override_hz)) ||
        (std::isfinite(cfg.p38_csi_override_k_rad_per_hz) !=
         std::isfinite(cfg.p38_csi_override_b_rad)) ||
        (!std::isfinite(cfg.p38_csi_override_k_rad_per_hz) &&
         !std::isnan(cfg.p38_csi_override_k_rad_per_hz)) ||
        (!std::isfinite(cfg.p38_csi_override_b_rad) &&
         !std::isnan(cfg.p38_csi_override_b_rad))) {
        throw std::runtime_error(
            "invalid CSI metrics, alignment, CFAR, or Doppler-center configuration");
    }
    cfg.runtime_mode = parseOptionalString("runtime_mode", cfg.runtime_mode);
    std::transform(cfg.runtime_mode.begin(), cfg.runtime_mode.end(), cfg.runtime_mode.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    if (cfg.runtime_mode == "release" || cfg.runtime_mode == "formal" ||
        cfg.runtime_mode == "production") {
        cfg.runtime_diagnostics_enabled = false;
    } else if (cfg.runtime_mode == "debug" || cfg.runtime_mode == "trace") {
        cfg.runtime_diagnostics_enabled = true;
    } else {
        std::cerr << "[XML][WARN] Unknown <runtime_mode>=\"" << cfg.runtime_mode
                  << "\"; keep runtime_diagnostics_enabled="
                  << (cfg.runtime_diagnostics_enabled ? "true" : "false") << std::endl;
    }
    cfg.runtime_diagnostics_enabled =
        parseOptionalBool("runtime_diagnostics_enabled", cfg.runtime_diagnostics_enabled);

    const std::string trackIdxRangeStr = getOptionalText("track_idx_range");
    if (!trackIdxRangeStr.empty()) {
        cfg.track_idx_range = parseIntList(trackIdxRangeStr);
    }

    cfg.track_gate_m = parseOptionalDouble("track_gate_m", cfg.track_gate_m);
    cfg.track_v_max = parseOptionalDouble("track_v_max", cfg.track_v_max);
    cfg.track_suppress_coasted_speed_outlier = parseOptionalBool(
        "track_suppress_coasted_speed_outlier",
        cfg.track_suppress_coasted_speed_outlier);

    if (cfg.track_idx_range.empty()) {
        const int trackId = extractFileId(cfg.GMTI_Data_new.empty() ? cfg.GMTI_Data_add : cfg.GMTI_Data_new);
        if (trackId > 0) {
            cfg.result_file_id = trackId;
        } else {
            std::string nextPath;
            int nextId = -1;
            if (!nextGMTIFileName(cfg.result_add, nextPath, nextId)) {
                std::cerr << "[XML][ERR] Cannot allocate incremental GMTI result id from result_add: "
                          << cfg.result_add << std::endl;
                return false;
            }
            cfg.result_file_id = nextId;
        }
        cfg.track_idx_range = buildTrackIdxRange(cfg.result_file_id, cfg.track_idx_window);
    }
    if (cfg.result_file_id <= 0) {
        const int trackId = extractFileId(cfg.GMTI_Data_new.empty() ? cfg.GMTI_Data_add : cfg.GMTI_Data_new);
        if (trackId > 0) {
            cfg.result_file_id = trackId;
        } else {
            std::string nextPath;
            int nextId = -1;
            if (!nextGMTIFileName(cfg.result_add, nextPath, nextId)) {
                std::cerr << "[XML][ERR] Cannot allocate incremental GMTI result id from result_add: "
                          << cfg.result_add << std::endl;
                return false;
            }
            cfg.result_file_id = nextId;
        }
    }
    if (cfg.scan_mode == ScanMode::Mechanical) {
        if (!cfg.INFO_Type) {
            throw std::runtime_error(
                "scan_mode=mechanical requires INFO_Type=1 new-protocol PRT headers");
        }
        // Bind slow-time dimensions before either executable derives fd_res or
        // creates FFT/CUDA plans. Electronic mode never enters this branch.
        cfg.pulse_num = cfg.mechanical_scan.cpi_pulse_count;
        cfg.read_pulse_num = cfg.mechanical_scan.cpi_pulse_count;
        cfg.read_pulse_offset = 0;
        cfg.process_pulse_num = cfg.mechanical_scan.cpi_pulse_count;
    }
    } catch (const std::exception& e) {
        std::cerr << e.what() << std::endl;
        return false;
    }

    return true; // 如果一切顺利，返回 true
}

using namespace std;

// 从 POS 文件读取数据并返回是否成功
bool GMTIProcessor::POS_dataread(const std::string &posFile, std::vector<std::vector<double>> &POS_data, int &POS_num)
{
    // 打开 POS 文件
    const int pos_len = 7;
    std::ifstream posFileStream(posFile, std::ios::binary);
    if (!posFileStream)
    {
        std::cerr << "无法打开 POS 文件: " << posFile << std::endl;
        return false; // 文件打开失败，返回 false
    }

    // 获取文件大小并计算帧数
    posFileStream.seekg(0, std::ios::end);            // 移动到文件末尾
    std::streamsize fileSize = posFileStream.tellg(); // 获取文件大小
    posFileStream.seekg(0, std::ios::beg);            // 移动回文件开头

    // 每一帧数据的字节数：pos_len 个 double，每个 double 占 8 字节
    const int POS_pkg_len = pos_len * sizeof(double);
    // 计算文件中包含的帧数
    POS_num = static_cast<int>(fileSize / POS_pkg_len);

    // 检查文件大小与帧数是否匹配
    if (fileSize % POS_pkg_len != 0)
    {
        std::cerr << "文件大小与帧数不匹配，可能包含损坏数据。" << std::endl;
        return false; // 如果文件大小无法整除每帧字节数，返回 false
    }

    // 读取 POS 数据
    POS_data.resize(POS_num, std::vector<double>(pos_len)); // 根据帧数调整二维 vector 的大小
    for (int i = 0; i < POS_num; ++i)
    {
        for (int j = 0; j < pos_len; ++j)
        {
            posFileStream.read(reinterpret_cast<char *>(&POS_data[i][j]), sizeof(double));
        }
    }

    posFileStream.close();

    DBG("成功读取 POS 数据");

    return true; // 数据读取成功，返回 true
}

// 打开回波文件（兼容单/双文件）
bool GMTIProcessor::openEchoFiles(const Config &cfg, std::ifstream &fid1, std::ifstream &fid2)
{
    if (cfg.channel_mode == "separate")
    { // 双文件模式
        fid1.open(cfg.GMTI_Data_add, std::ios::binary);
        fid2.open(cfg.GMTI_Data_add2, std::ios::binary);
        if (!fid1.is_open() || !fid2.is_open())
        {
            std::cerr << "无法打开回波数据双文件" << std::endl;
            return false;
        }
    }
    else if (cfg.channel_mode == "interleaved")
    { // 单文件模式
        fid1.open(cfg.GMTI_Data_add, std::ios::binary);
        fid2.close(); // 无第二个文件
        if (!fid1.is_open())
        {
            std::cerr << "无法打开交织回波数据文件" << std::endl;
            return false;
        }
    }
    else
    {
        std::cerr << "未知 channel_mode: " << cfg.channel_mode << std::endl;
        return false;
    }

    return true;
}

bool GMTIProcessor::readPulseBlock(const Config& cfg,
                                   int beamskip,
                                   std::vector<std::complex<float>>& data1,
                                   std::vector<std::complex<float>>& data2,
                                   std::vector<double>& utc,
                                   double& theta_sq)
{
    TIMING_SCOPE(readPulseBlock);
    const char* filepath = cfg.GMTI_Data_add2.c_str();
    std::vector<double> fw_angle_deg;

    if(!readBeamRawFloat(cfg, filepath, beamskip, data2, fw_angle_deg, utc)) {
        std::cerr << "读取回波数据失败" << std::endl;
        return false;
    }

    const char* filepath2 = cfg.GMTI_Data_add.c_str();
    if(!readBeamRawFloat(cfg, filepath2, beamskip, data1, fw_angle_deg, utc)) {
        std::cerr << "读取回波数据失败" << std::endl;
        return false;
    }

    // 计算方位角平方的平均值
    theta_sq = fw_angle_deg[fw_angle_deg.size() / 2];

    return true;
}




// 提取 UTC 时间（秒）
bool GMTIProcessor::extractUTC(const std::vector<uint8_t>& headerBytes, const Config& cfg,
                                std::vector<double>& t) {
    size_t pulse_num = headerBytes.size() / cfg.info_len;  // 计算脉冲数量（假设headerBytes为一维数组）

    // 校验headerBytes大小是否足够
    assert(headerBytes.size() >= cfg.info_len * pulse_num && "headerBytes 大小不正确");

    double TickHz = 1e8;  // 固定为 1e8
    double DayBias = cfg.week * 24 * 3600;  // 从 cfg 获取 DayBias
    double SecBias = cfg.secBias;  // 从 cfg 获取 SecBias

    // BCD 解码：时分秒
    auto bcd2dec = [](uint8_t u8) -> double {
        return static_cast<double>(u8) - 6.0 * std::floor(static_cast<double>(u8) / 16.0);
    };

    t.resize(pulse_num);  // 分配时间结果向量

    if (cfg.INFO_Type) {  // 如果 INFO_Type 为 true
        for (size_t i = 0; i < pulse_num; ++i) {
            // 从 headerBytes 中提取 BCD 编码的时、分、秒
            double hh = bcd2dec(headerBytes[36 + i * cfg.info_len]);  // 小时 0..23
            double mm = bcd2dec(headerBytes[37 + i * cfg.info_len]);  // 分钟 0..59
            double ss = bcd2dec(headerBytes[38 + i * cfg.info_len]);  // 秒 0..59

            // 32-bit 计数（小端序）→ 秒的小数部分
            double ds = static_cast<double>(headerBytes[42 + i * cfg.info_len]) * 256.0 * 256.0 * 256.0 +
                        static_cast<double>(headerBytes[41 + i * cfg.info_len]) * 256.0 * 256.0 +
                        static_cast<double>(headerBytes[40 + i * cfg.info_len]) * 256.0 +
                        static_cast<double>(headerBytes[39 + i * cfg.info_len]);

            double fracSec = ds / TickHz;

            // 组合为秒
            t[i] = DayBias + SecBias + hh * 3600.0 + mm * 60.0 + ss + fracSec;
        }
    } else {  // 如果 INFO_Type 为 false
        for (size_t i = 0; i < pulse_num; ++i) {
            // 从 headerBytes 中提取 BCD 编码的时、分、秒
            double hh = bcd2dec(headerBytes[241 + i * cfg.info_len]);  // 小时 0..23
            double mm = bcd2dec(headerBytes[242 + i * cfg.info_len]);  // 分钟 0..59
            double ss = bcd2dec(headerBytes[243 + i * cfg.info_len]);  // 秒 0..59

            // 32-bit 计数（小端序）→ 秒的小数部分
            double ds = static_cast<double>(headerBytes[247 + i * cfg.info_len]) * 256.0 * 256.0 * 256.0 +
                        static_cast<double>(headerBytes[246 + i * cfg.info_len]) * 256.0 * 256.0 +
                        static_cast<double>(headerBytes[245 + i * cfg.info_len]) * 256.0 +
                        static_cast<double>(headerBytes[244 + i * cfg.info_len]);

            double fracSec = ds / TickHz;

            // 组合为秒
            t[i] = DayBias + SecBias + hh * 3600.0 + mm * 60.0 + ss + fracSec;
        }
    }

    return true;  // 成功提取 UTC 时间
}

// 读取脉冲块（float版本, GPU路径优化使用）
bool GMTIProcessor::readPulseBlockFloat(const Config& cfg,
                                        int beamskip,
                                        std::vector<std::complex<float>>& data1,
                                        std::vector<std::complex<float>>& data2,
                                        std::vector<double>& utc,
                                        double& theta_sq)
{
    TIMING_SCOPE(readPulseBlockFloat);
    const char* filepath = cfg.GMTI_Data_add2.c_str();
    std::vector<double> fw_angle_deg;

    if(!readBeamRawFloat(cfg, filepath, beamskip, data2, fw_angle_deg, utc)) {
        std::cerr << "读取回波数据失败 (float)" << std::endl;
        return false;
    }

    const char* filepath2 = cfg.GMTI_Data_add.c_str();
    if(!readBeamRawFloat(cfg, filepath2, beamskip, data1, fw_angle_deg, utc)) {
        std::cerr << "读取回波数据失败 (float)" << std::endl;
        return false;
    }

    // 计算方位角平方的平均值
    theta_sq = fw_angle_deg[fw_angle_deg.size() / 2];

    return true;
}
