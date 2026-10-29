#include "config_structs.hpp"
#include "GMTIProcessor.hpp"
#include "rangeCompress.hpp"
#include "runtime_diagnostics.hpp"

#include <cstdio>
#include <cstdlib>
#include <cerrno>
#include <algorithm>
#include <complex>
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
    check(defaults.research_calibration_reference_gamma_csv.empty() &&
              defaults.research_calibration_mode == "not_applicable",
          "reference Gamma fields are inert by default");
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
                  parsed_default.research_calibration_range_band_bins == 0 &&
                  parsed_default.research_calibration_reference_gamma_csv.empty() &&
                  parsed_default.research_calibration_mode == "not_applicable",
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
    snapshot_cfg.research_calibration_reference_gamma_csv = "/tmp/reference_gamma.csv";
    snapshot_cfg.research_calibration_mode = "Mode-A";

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
    gmti::runtime::ProductionCalibrationGammaGroup first_group;
    first_group.az_index = 1;
    first_group.range_start = 0;
    first_group.range_end = 15;
    first_group.status = "OK";
    first_group.support_count = 16;
    first_group.phase_coherence = 0.8;
    first_group.gamma_real = 0.99;
    first_group.gamma_imag = 0.01;
    first_group.truth_used_in_estimator = false;
    first_group.reason = "first_group";
    tap.gamma_groups.push_back(first_group);
    gmti::runtime::ProductionCalibrationGammaGroup second_group = first_group;
    second_group.az_index = 1;
    second_group.range_start = 16;
    second_group.range_end = 31;
    second_group.phase_coherence = 0.7;
    second_group.gamma_real = 0.61;
    second_group.gamma_imag = -0.02;
    second_group.reason = "second_group";
    tap.gamma_groups.push_back(second_group);
    gmti::runtime::recordProductionCalibrationTap(snapshot_cfg, 3, tap);

    Config failure_cells_cfg = snapshot_cfg;
    failure_cells_cfg.research_calibration_reference_gamma_csv.clear();
    failure_cells_cfg.csi_metrics_enable = true;
    failure_cells_cfg.csi_metrics_dump_power_maps = true;
    failure_cells_cfg.csi_metrics_dump_intermediate_maps = true;
    gmti::runtime::ProductionCalibrationTap failure_tap = tap;
    failure_tap.gamma_groups.clear();
    gmti::runtime::ProductionCalibrationGammaGroup failed_group;
    failed_group.az_index = 0;
    failed_group.range_start = 0;
    failed_group.range_end = 3;
    failed_group.status = "NOT_EVALUABLE";
    failed_group.support_count = 1;
    failed_group.phase_coherence = 0.697;
    failed_group.reason = "insufficient_robust_support";
    failure_tap.gamma_groups.push_back(failed_group);
    const std::vector<std::complex<float>> failure_f1(4, {1.0f, 0.0f});
    const std::vector<std::complex<float>> failure_f2 = {
        {1.0f, 0.0f},
        {0.99875027f, 0.04997917f},
        {0.54030228f, 0.84147096f},
        {-0.41614684f, 0.90929741f},
    };
    std::remove((root + "/production_calibration_failure_cells.csv").c_str());
    check(gmti::runtime::recordProductionCalibrationFailureCells(
              failure_cells_cfg, 3, failure_tap, 1, 4,
              failure_f1, failure_f2),
          "failure-cell diagnostics are written from the supplied estimator inputs");
    Config failure_cells_disabled = failure_cells_cfg;
    failure_cells_disabled.result_add = root + "/failure_cells_disabled";
    failure_cells_disabled.csi_metrics_dump_power_maps = false;
    check(gmti::runtime::recordProductionCalibrationFailureCells(
              failure_cells_disabled, 3, failure_tap, 1, 4,
              failure_f1, failure_f2),
          "failure-cell diagnostics remain inert when map dumping is disabled");
    // A selected passing group must be captured even for a fixed-reference
    // application with ordinary map dumps disabled. This is needed to compare
    // ON, OFF and TO inputs at the same group, not only failed estimator rows.
    Config selected_cfg = failure_cells_disabled;
    selected_cfg.result_add = root;
    selected_cfg.research_calibration_reference_gamma_csv = "/tmp/fixed_reference.csv";
    gmti::runtime::ProductionCalibrationTap selected_tap = failure_tap;
    selected_tap.gamma_groups[0].status = "OK";
    const std::vector<std::complex<float>> selected_f1_before = failure_f1;
    const std::vector<std::complex<float>> selected_f2_before = failure_f2;
    std::remove((root + "/production_calibration_selected_cells.csv").c_str());
    check(::setenv("PGRCC_C4_TRACE_GROUP", "1,3,0,0,3", 1) == 0,
          "set selected-cell diagnostic coordinates");
    check(gmti::runtime::recordProductionCalibrationFailureCells(
              selected_cfg, 3, selected_tap, 1, 4, failure_f1, failure_f2),
          "selected-cell diagnostic call succeeds");
    const std::string selected_csv = readText(root + "/production_calibration_selected_cells.csv");
    check(std::count(selected_csv.begin(), selected_csv.end(), '\n') == 5 &&
              selected_csv.find("1,0,1,0\n") != std::string::npos,
          "selected passing fixed-reference group retains all four F1/F2 cells");
    check(failure_f1 == selected_f1_before && failure_f2 == selected_f2_before,
          "selected-cell diagnostic leaves estimator input vectors unchanged");
    ::unsetenv("PGRCC_C4_TRACE_GROUP");

    // Stage-boundary traces are opt-in and contain only the selected row/range
    // cells; this guards the diagnostic path used to localize R22 closure loss.
    const std::string phase_trace_path =
        root + "/production_phase_boundary_trace.csv";
    std::remove(phase_trace_path.c_str());
    check(!gmti::runtime::productionStageBoundaryTraceSelected(selected_cfg, 3),
          "stage-boundary tracing is not selected when its environment selector is unset");
    check(gmti::runtime::recordProductionStageBoundaryTrace(
              selected_cfg, 3, "csi_post_range_phase_pre_p38_fit", 2, 5,
              failure_f1, failure_f2, failure_f1,
              std::vector<float>{0.0f, 0.25f, 0.5f, 0.75f, 1.0f},
              0.125, -0.25),
          "selected stage-boundary trace is written");
    std::ifstream phase_trace_disabled(phase_trace_path.c_str());
    check(!phase_trace_disabled.good(),
          "stage-boundary trace is inert when selector is unset");

    check(::setenv("PGRCC_C4_TRACE_GROUP", "1,3,1,1,3", 1) == 0,
          "set stage-boundary trace coordinates");
    check(gmti::runtime::productionStageBoundaryTraceSelected(selected_cfg, 3) &&
              !gmti::runtime::productionStageBoundaryTraceSelected(selected_cfg, 4),
          "stage-boundary tracing selects only the requested period and beam");
    check(gmti::runtime::recordProductionStageBoundaryTrace(
              selected_cfg, 3, "csi_post_range_phase_pre_p38_fit", 2, 5,
              std::vector<std::complex<float>>(10, {1.0f, 0.0f}),
              std::vector<std::complex<float>>(10, {0.0f, 1.0f}),
              std::vector<std::complex<float>>(10, {0.5f, -0.5f}),
              std::vector<float>{0.0f, 0.25f, 0.5f, 0.75f, 1.0f},
              0.125, -0.25),
          "stage-boundary trace call succeeds for the selected group");
    const std::string phase_trace = readText(phase_trace_path);
    check(std::count(phase_trace.begin(), phase_trace.end(), '\n') == 4 &&
              phase_trace.find("csi_post_range_phase_pre_p38_fit") != std::string::npos &&
              phase_trace.find(
                  "csi_post_range_phase_pre_p38_fit,1,1,1,0,0,1,"
                  "0.5,-0.5,0.25,0.125,-0.25") !=
                  std::string::npos,
          "stage-boundary trace records exactly the selected cells and stage metadata");

    check(::setenv("PGRCC_C4_TRACE_GROUP", "5,3,1,1", 1) == 0,
          "set malformed stage-boundary trace coordinates");
    check(gmti::runtime::productionStageBoundaryTraceSelected(selected_cfg, 3),
          "malformed selector remains selected so the diagnostic fails closed");
    check(!gmti::runtime::recordProductionStageBoundaryTrace(
              selected_cfg, 3, "bad_trace", 2, 5,
              std::vector<std::complex<float>>(10, {1.0f, 0.0f}),
              std::vector<std::complex<float>>(10, {0.0f, 1.0f}), {}, {},
              std::numeric_limits<double>::quiet_NaN(),
              std::numeric_limits<double>::quiet_NaN()),
          "malformed stage-boundary trace coordinates fail closed");
    ::unsetenv("PGRCC_C4_TRACE_GROUP");

    std::remove((root + "/production_calibration_selected_cells.csv").c_str());
    check(::setenv("PGRCC_C4_TRACE_GROUP", "2,3,0,0,3", 1) == 0,
          "set nonmatching selected-cell coordinates");
    check(gmti::runtime::recordProductionCalibrationFailureCells(
              selected_cfg, 3, selected_tap, 1, 4, failure_f1, failure_f2),
          "nonmatching selected-cell diagnostic is inert");
    std::ifstream nonmatching_selected_csv(
        (root + "/production_calibration_selected_cells.csv").c_str());
    check(!nonmatching_selected_csv.good(),
          "nonmatching period does not emit selected-cell rows");
    ::unsetenv("PGRCC_C4_TRACE_GROUP");

    check(::setenv("PGRCC_C4_TRACE_GROUP", "1,3,0,0", 1) == 0,
          "set malformed selected-cell coordinates");
    check(!gmti::runtime::recordProductionCalibrationFailureCells(
              selected_cfg, 3, selected_tap, 1, 4, failure_f1, failure_f2),
          "malformed selected-cell coordinates fail closed");
    ::unsetenv("PGRCC_C4_TRACE_GROUP");
    gmti::runtime::finishRun(snapshot_cfg, true, 0, "selftest");

    const std::string json = readText(root + "/runtime_config_dump.json");
    const std::string text = readText(root + "/runtime_config_dump.txt");
    const std::string csv = readText(root + "/production_calibration_adapter.csv");
    const std::string reference_csv = readText(
        root + "/production_calibration_reference_gamma.csv");
    const std::string failure_cells_csv = readText(
        root + "/production_calibration_failure_cells.csv");
    std::ifstream failure_cells_disabled_csv(
        (root + "/failure_cells_disabled/production_calibration_failure_cells.csv").c_str());
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
    check(json.find("\"research_calibration_reference_gamma_csv\": \"/tmp/reference_gamma.csv\"") != std::string::npos,
          "runtime JSON includes reference Gamma artifact");
    check(json.find("\"research_calibration_mode\": \"Mode-A\"") != std::string::npos,
          "runtime JSON includes calibration mode");
    check(text.find("research_calibration_method = robust_ddc_rb") != std::string::npos,
          "runtime text includes research method");
    check(csv.find("beam_id,run_id") != std::string::npos,
          "research tap CSV has a stable header");
    check(csv.find(",robust_ddc_rb,OK,production_final_f1_f2_after_p38_range_phase") != std::string::npos,
          "research tap CSV records beam and status");
    check(csv.find(",false,test") != std::string::npos,
          "research tap CSV records truth-blind provenance");
    check(reference_csv.find(
              "period_id,result_id,beam_id,method,group_id,az_index,range_start,"
              "range_end,status,support_count,phase_coherence,gamma_real,"
              "gamma_imag,truth_used_in_estimator,reason") != std::string::npos,
          "group reference CSV has the strict raw header");
    check(reference_csv.find(",az_1_range_0_15,1,0,15,OK,16") != std::string::npos &&
              reference_csv.find(",az_1_range_16_31,1,16,31,OK,16") != std::string::npos,
          "group reference CSV preserves distinct row/band groups");
    check(reference_csv.find(",0.99,0.01,false,first_group") != std::string::npos &&
              reference_csv.find(",0.61,-0.02,false,second_group") != std::string::npos,
          "group reference CSV preserves distinct Gamma values");
    check(failure_cells_csv.find(
              "adapter_support_count,adapter_phase_coherence,") !=
                  std::string::npos &&
              failure_cells_csv.find("f1_real,f1_imag,f2_real,f2_imag") !=
                  std::string::npos,
          "failure-cell CSV identifies the exact adapter inputs and gate summary");
    check(std::count(failure_cells_csv.begin(), failure_cells_csv.end(), '\n') == 5,
          "failure-cell CSV records every cell in the failed four-cell group");
    check(failure_cells_csv.find("1,0,1,0\n") != std::string::npos,
          "failure-cell CSV retains the exact complex F1/F2 input cells");
    check(!failure_cells_disabled_csv.good(),
          "failure-cell diagnostics do not create files when map dumping is disabled");

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
