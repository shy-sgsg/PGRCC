#include "stage3_config.h"
#include "stage3_validator.h"
#include "sar_scene_forward.h"

#include <fstream>
#include <iostream>
#include <vector>

using namespace gmti::stage3;

int main(int argc, char **argv)
{
    Stage3RunOptions opt;
    std::string err;
    if (!parseStage3CommandLine(argc, argv, opt, err)) {
        std::cerr << "[stage3][ERR] " << err << "\n";
        return 2;
    }
    Stage3Config cfg;
    if (!loadStage3Config(opt.stage3_config, cfg, err)) {
        std::cerr << "[stage3][ERR] " << err << "\n";
        return 2;
    }
    if (!opt.output_dir.empty() && opt.output_dir != "outputs/stage3") {
        cfg.output_dir = opt.output_dir;
    } else {
        opt.output_dir = cfg.output_dir;
    }
    if (!opt.sar_image.empty()) cfg.sar_input.image_path = opt.sar_image;
    if (!opt.georef_path.empty()) cfg.sar_input.georef_path = opt.georef_path;
    if (!opt.scene_mode.empty()) {
        cfg.scene.mode = opt.scene_mode;
        cfg.scene.roi.enabled = (opt.scene_mode == "roi");
        if (cfg.scene.mode == "full" || cfg.scene.mode == "sar") cfg.scene.mode = "mirror";
    }
    if (!opt.backend.empty()) cfg.forward.backend = opt.backend;
    if (opt.compare_cpu_cuda_override_set) cfg.forward.compare_cpu_cuda = opt.compare_cpu_cuda;
    if (!opt.target_config.empty()) cfg.targets.target_config_path = opt.target_config;
    if (opt.target_enabled_override_set) cfg.targets.enabled = opt.target_enabled;
    if (opt.period_start >= 0) cfg.forward.period_start = opt.period_start;
    if (opt.period_count >= 0) cfg.forward.period_count = opt.period_count;
    if (opt.beam_start >= 0) cfg.forward.beam_start = opt.beam_start;
    if (opt.beam_count >= 0) cfg.forward.beam_count = opt.beam_count;
    if (opt.pulse_start >= 0) cfg.forward.pulse_start = opt.pulse_start;
    if (opt.pulse_count >= 0) cfg.forward.pulse_count = opt.pulse_count;
    if (opt.dry_run_override_set) cfg.forward.dry_run = opt.dry_run;

    if (!ensureStage3Dirs(cfg.output_dir, err)) {
        std::cerr << "[stage3][ERR] " << err << "\n";
        return 2;
    }

    const std::string report_path = pathJoin(pathJoin(cfg.output_dir, "reports"), "stage3_sar_scene_report.md");
    if (!writeStage3PlanningReport(report_path, cfg, opt, err)) {
        std::cerr << "[stage3][ERR] " << err << "\n";
        return 3;
    }

    const std::string cfg_path = pathJoin(pathJoin(cfg.output_dir, "config"), "stage3_config.example.json");
    if (!writeDefaultStage3Config(cfg_path, err)) {
        std::cerr << "[stage3][ERR] " << err << "\n";
        return 4;
    }

    const std::string out_bin = pathJoin(pathJoin(cfg.output_dir, "data"), "stage3_sar_scene_newprotocol.bin");
    std::vector<SarScatterer> scatterers;
    if (!forwardSarSceneToDdc(cfg, scatterers, out_bin, err)) {
        std::cerr << "[stage3][ERR] " << err << "\n";
        return 5;
    }

    std::cout << "[stage3] SAR scene forward complete. mode=" << cfg.scene.mode
              << " backend=" << cfg.forward.backend
              << " output_dir=" << cfg.output_dir << "\n";
    if (!cfg.forward.dry_run) {
        std::cout << "[stage3] DDC new-protocol bin=" << out_bin << "\n";
    }
    return 0;
}
