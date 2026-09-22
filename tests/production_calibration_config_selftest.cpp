#include "config_structs.hpp"
#include "GMTIProcessor.hpp"
#include "rangeCompress.hpp"
#include "runtime_diagnostics.hpp"

#include <cstdio>
#include <cstdlib>
#include <cerrno>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

bool readBeamRawFloat(const Config&, const char*, int,
                      std::vector<std::complex<float>>&,
                      std::vector<double>&, std::vector<double>&)
{
    return false;
}

bool GMTIProcessor::nextGMTIFileName(const std::string&, std::string& out_path,
                                     int& out_index) const
{
    out_path = "/tmp/production_calibration_config_selftest_GMTI01.bin";
    out_index = 1;
    return true;
}

void GMTIProcessor::cleanupCUDAResources()
{
}

namespace {

void check(bool condition, const std::string& message)
{
    if (!condition) {
        std::cerr << "[production_calibration_config_selftest] FAIL: "
                  << message << std::endl;
        std::exit(1);
    }
}

std::string readText(const std::string& path)
{
    std::ifstream input(path.c_str());
    std::ostringstream text;
    text << input.rdbuf();
    return text.str();
}

std::string writeVariant(const std::string& root,
                         const std::string& source,
                         const std::string& name,
                         const std::string& fields)
{
    std::string xml = source;
    const std::string close_tag = "</GMTI_parameter>";
    const std::size_t close_pos = xml.rfind(close_tag);
    check(close_pos != std::string::npos, "source XML has GMTI_parameter close tag");
    xml.insert(close_pos, fields);
    const std::string path = root + "/" + name + ".xml";
    std::ofstream output(path.c_str());
    check(static_cast<bool>(output), "cannot write XML variant " + path);
    output << xml;
    return path;
}

std::string removeResearchFields(std::string xml)
{
    const std::vector<std::string> names = {
        "research_calibration_enable",
        "research_calibration_method",
        "research_calibration_min_support",
        "research_calibration_range_band_bins",
        "research_calibration_robust_phase_threshold_rad",
        "csi_cancellation_mode"
    };
    for (const std::string& name : names) {
        const std::string open = "<" + name + ">";
        const std::string close = "</" + name + ">";
        std::size_t begin = xml.find(open);
        while (begin != std::string::npos) {
            const std::size_t end = xml.find(close, begin);
            check(end != std::string::npos, "unterminated XML test field " + name);
            xml.erase(begin, end + close.size() - begin);
            begin = xml.find(open);
        }
    }
    return xml;
}

} // namespace

int main(int argc, char** argv)
{
    Config defaults{};
    check(!defaults.research_calibration_enable,
          "research calibration is disabled by default");
    check(defaults.research_calibration_method == "production_current",
          "default research method is production_current");
    check(defaults.research_calibration_min_support > 0,
          "default research support minimum is positive");
    check(defaults.research_calibration_range_band_bins == 0,
          "default range-band size is disabled for non-banded methods");
    check(defaults.csi_cancellation_mode == "legacy_min_magnitude",
          "legacy csi_cancellation_mode default is unchanged");

    check(argc >= 2, "selftest requires a source gmti.xml path");
    const std::string source_xml = removeResearchFields(readText(argv[1]));
    const std::string root = "/tmp/production_calibration_config_selftest_" +
                             std::to_string(static_cast<long long>(::getpid()));
    check((::mkdir(root.c_str(), 0755) == 0 || errno == EEXIST),
          "cannot create selftest temporary directory");
    const std::string source_path = root + "/source.xml";
    {
        std::ofstream output(source_path.c_str());
        check(static_cast<bool>(output), "cannot write source XML copy");
        output << source_xml;
    }
    GMTIProcessor parser;
    Config parsed_default{};
    check(parser.readXmlParam(source_path, parsed_default),
          "XML without research fields must parse");
    check(!parsed_default.research_calibration_enable &&
              parsed_default.research_calibration_method == "production_current" &&
              parsed_default.research_calibration_range_band_bins == 0,
          "absent research XML fields preserve safe defaults");
    check(parsed_default.csi_cancellation_mode == "legacy_min_magnitude",
          "absent csi cancellation XML field preserves legacy default");

    const std::vector<std::string> parsed_methods = {
        "production_current", "ordinary_subtraction", "scc", "ddc",
        "robust_ddc", "robust_ddc_rb"
    };
    for (const std::string& method : parsed_methods) {
        const int band = method == "robust_ddc_rb" ? 8 : 0;
        const std::string fields =
            "\n        <research_calibration_enable>true</research_calibration_enable>\n" +
            std::string("        <research_calibration_method>") + method +
            "</research_calibration_method>\n" +
            "        <research_calibration_min_support>9</research_calibration_min_support>\n" +
            "        <research_calibration_range_band_bins>" +
            std::to_string(band) + "</research_calibration_range_band_bins>\n" +
            "        <research_calibration_robust_phase_threshold_rad>0.4</research_calibration_robust_phase_threshold_rad>\n";
        const std::string path = writeVariant(root, source_xml, "legal_" + method, fields);
        Config parsed{};
        check(parser.readXmlParam(path, parsed), "legal XML method rejected: " + method);
        check(parsed.research_calibration_enable &&
                  parsed.research_calibration_method == method &&
                  parsed.research_calibration_min_support == 9 &&
                  parsed.research_calibration_range_band_bins == band,
              "legal XML method values were not retained: " + method);
    }
    {
        const std::string path = writeVariant(
            root, source_xml, "invalid_method",
            "\n        <research_calibration_method>invalid</research_calibration_method>\n");
        Config parsed{};
        check(!parser.readXmlParam(path, parsed),
              "invalid research method must fail XML parsing");
    }
    {
        const std::string path = writeVariant(
            root, source_xml, "invalid_bool",
            "\n        <research_calibration_enable>maybe</research_calibration_enable>\n");
        Config parsed{};
        check(!parser.readXmlParam(path, parsed),
              "invalid research enable must fail XML parsing");
    }

    const std::vector<std::string> legal_methods = {
        "production_current", "ordinary_subtraction", "scc", "ddc",
        "robust_ddc", "robust_ddc_rb"
    };
    for (const std::string& method : legal_methods) {
        Config cfg = defaults;
        cfg.research_calibration_method = method;
        if (method == "robust_ddc_rb") {
            cfg.research_calibration_range_band_bins = 8;
        }
        std::string error;
        check(validateResearchCalibrationConfig(cfg, &error),
              "legal research method rejected: " + method + ": " + error);
    }

    {
        Config cfg = defaults;
        cfg.research_calibration_method = "not_a_method";
        std::string error;
        check(!validateResearchCalibrationConfig(cfg, &error) && !error.empty(),
              "invalid research method must fail closed");
    }
    {
        Config cfg = defaults;
        cfg.research_calibration_min_support = 0;
        std::string error;
        check(!validateResearchCalibrationConfig(cfg, &error) && !error.empty(),
              "zero support minimum must fail closed");
    }
    {
        Config cfg = defaults;
        cfg.research_calibration_method = "robust_ddc_rb";
        cfg.research_calibration_range_band_bins = 0;
        std::string error;
        check(!validateResearchCalibrationConfig(cfg, &error) && !error.empty(),
              "robust DDC-RB without a band must fail closed");
    }

    Config snapshot_cfg = defaults;
    snapshot_cfg.result_add = root;
    snapshot_cfg.runtime_mode = "debug";
    snapshot_cfg.runtime_diagnostics_enabled = true;
    snapshot_cfg.result_file_id = 1;
    snapshot_cfg.research_calibration_enable = true;
    snapshot_cfg.research_calibration_method = "robust_ddc_rb";
    snapshot_cfg.research_calibration_min_support = 12;
    snapshot_cfg.research_calibration_range_band_bins = 16;
    snapshot_cfg.research_calibration_robust_phase_threshold_rad = 0.35;

    gmti::runtime::initializeRun(snapshot_cfg, "production_calibration_selftest.xml",
                                 "production_calibration_config_selftest");
    gmti::runtime::ProductionCalibrationTap tap;
    tap.method = "robust_ddc_rb";
    tap.status = "OK";
    tap.source = "production_final_f1_f2_after_p38_range_phase";
    tap.support_count = 128;
    tap.excluded_count = 7;
    tap.groups_total = 4;
    tap.valid_groups = 4;
    tap.gamma_real = 0.99;
    tap.gamma_imag = 0.01;
    tap.gamma_abs = 1.0;
    tap.phase_coherence = 0.1;
    tap.truth_used_in_estimator = false;
    tap.reason = "test";
    gmti::runtime::recordProductionCalibrationTap(snapshot_cfg, 3, tap);
    gmti::runtime::finishRun(snapshot_cfg, true, 0, "selftest");

    const std::string json = readText(root + "/runtime_config_dump.json");
    const std::string text = readText(root + "/runtime_config_dump.txt");
    const std::string csv = readText(root + "/production_calibration_adapter.csv");
    check(json.find("\"research_calibration_enable\": true") != std::string::npos,
          "runtime JSON includes research enable");
    check(json.find("\"research_calibration_method\": \"robust_ddc_rb\"") != std::string::npos,
          "runtime JSON includes research method");
    check(json.find("\"research_calibration_min_support\": 12") != std::string::npos,
          "runtime JSON includes support minimum");
    check(json.find("\"research_calibration_range_band_bins\": 16") != std::string::npos,
          "runtime JSON includes range-band size");
    check(json.find("\"research_calibration_robust_phase_threshold_rad\": 0.35") != std::string::npos,
          "runtime JSON includes robust phase threshold");
    check(text.find("research_calibration_method = robust_ddc_rb") != std::string::npos,
          "runtime text includes research method");
    check(csv.find("beam_id,run_id") != std::string::npos,
          "research tap CSV has a stable header");
    check(csv.find(",robust_ddc_rb,OK,production_final_f1_f2_after_p38_range_phase") != std::string::npos,
          "research tap CSV records beam and status");
    check(csv.find(",false,test") != std::string::npos,
          "research tap CSV records truth-blind provenance");

    const std::string blocked_parent = root + "/tap_blocker_file";
    {
        std::ofstream blocker(blocked_parent.c_str());
        check(static_cast<bool>(blocker), "cannot create tap blocker file");
        blocker << "not a directory";
    }
    Config unwritable_cfg = snapshot_cfg;
    unwritable_cfg.result_add = blocked_parent + "/child";
    check(!gmti::runtime::recordProductionCalibrationTap(
              unwritable_cfg, 3, tap),
          "research tap must fail closed when result_add cannot be created");

    std::cout << "production calibration config/runtime selftest: PASS" << std::endl;
    return 0;
}
