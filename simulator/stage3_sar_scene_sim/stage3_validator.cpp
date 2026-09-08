#include "stage3_validator.h"

#include <fstream>
#include <iomanip>

namespace gmti {
namespace stage3 {

bool writeStage3PlanningReport(const std::string &path,
                               const Stage3Config &cfg,
                               const Stage3RunOptions &opt,
                               std::string &err)
{
    std::ofstream md(path.c_str());
    if (!md) {
        err = "failed to open " + path;
        return false;
    }
    md << std::setprecision(12);
    md << "# Stage3 SAR 场景仿真配置与复现报告\n\n";
    md << ">本文件在前向计算前写入，记录已解析的实际配置。"
          "运行结果和实际画布尺寸以 `manifest.json`、`truth/sar_surface_metadata.json` "
          "和 `logs/stage3_performance.csv` 为准。\n\n";

    md << "## 实验身份\n\n";
    md << "- case_id: `" << cfg.case_id << "`\n";
    md << "- 输入配置: `" << opt.stage3_config << "`\n";
    md << "- SAR 图像: `" << cfg.sar_input.image_path << "`\n";
    md << "- 输出目录: `" << cfg.output_dir << "`\n";
    md << "- 场景模式: `" << cfg.scene.mode << "`\n";
    md << "- 前向后端: `" << cfg.forward.backend << "`\n";
    md << "- 目标注入: `" << (cfg.targets.enabled ? "true" : "false") << "`\n";
    if (cfg.targets.enabled) {
        md << "- 目标配置: `" << cfg.targets.target_config_path << "`\n";
        md << "- 目标幅度: `" << cfg.targets.amplitude_mode << "`，"
           << cfg.targets.target_snr_db << " dB\n";
    }
    md << "- dry_run: `" << (cfg.forward.dry_run ? "true" : "false") << "`\n\n";

    md << "## SAR 物理尺度与映射\n\n";
    md << "- 输入像元尺寸（距离向 × 方位向）: "
       << cfg.sar_input.pixel_size_range_m << " m × "
       << cfg.sar_input.pixel_size_azimuth_m << " m\n";
    md << "- 归一化分位数: [" << cfg.sar_input.percentile_low << ", "
       << cfg.sar_input.percentile_high << "] %\n";
    md << "- 公共坐标锚点: 参考波束 " << cfg.tiling.reference_beam_id_1based
       << "，斜距 " << cfg.tiling.anchor_slant_range_m << " m\n";
    md << "- 镜像边界: `reflect-101`，周期 `2*(N-1)`，端点不重复\n";
    md << "- 随机起始偏移: `" << (cfg.tiling.random_start_offset ? "true" : "false")
       << "`，随机种子: " << cfg.tiling.random_seed << "\n";
    md << "- 相位策略: `" << cfg.tiling.phase_policy << "`\n\n";
    md << "- 有效源图裁剪: `" << (cfg.sar_input.valid_crop_enabled ? "true" : "false")
       << "`，列区间 [" << cfg.sar_input.valid_col_start << ", "
       << cfg.sar_input.valid_col_end_exclusive << ")，行区间 ["
       << cfg.sar_input.valid_row_start << ", "
       << cfg.sar_input.valid_row_end_exclusive << ")\n\n";

    if (cfg.scene.mode == "roi" || cfg.scene.roi.enabled) {
        md << "ROI 采用原图直接裁剪，超出原图的像元无效，不镜像回卷。"
              "ROI 与 mirror 映射共用上述参考波束/斜距锚点。\n\n";
        md << "- ROI 波束: " << cfg.scene.roi.beam_id_1based << "\n";
        md << "- ROI 中心斜距: " << cfg.scene.roi.center_slant_range_m << " m\n";
        md << "- ROI 范围（距离向 × 方位向）: "
           << cfg.scene.roi.range_extent_m << " m × "
           << cfg.scene.roi.azimuth_extent_m << " m\n";
        md << "- ROI 采样间隔: " << cfg.scene.roi.resolution_m << " m\n\n";
    } else {
        md << "Mirror 模式只为请求的波束子集及高斯增益阈值对应的角向包络建立地面画布，"
              "画布采样间隔为 "
           << cfg.canvas.resolution_m << " m，最大允许 " << cfg.canvas.max_cells
           << " 个网格。\n\n";
    }

    md << "## 雷达与前向子集\n\n";
    md << "- 载频 / 带宽 / 采样率: " << cfg.system.fc_ghz << " GHz / "
       << cfg.system.bandwidth_mhz << " MHz / " << cfg.system.fs_mhz << " MHz\n";
    md << "- DDC 点数 / 通道数: " << cfg.system.ddc_len << " / "
       << cfg.system.new_protocol_channel_count << "\n";
    md << "- 波束子集: " << cfg.forward.beam_start << ".."
       << (cfg.forward.beam_start + cfg.forward.beam_count - 1) << "\n";
    md << "- 脉冲子集: " << cfg.forward.pulse_start << ".."
       << (cfg.forward.pulse_start + cfg.forward.pulse_count - 1) << "\n";
    md << "- CPU/CUDA 数值对比: `"
       << (cfg.forward.compare_cpu_cuda ? "true" : "false") << "`\n";
    md << "- 回波波形: 场景冲激响应与 LFM 时域波形卷积，下游 XML 保持 `isPC=0`\n\n";
    if (cfg.targets.enabled) {
        md << "目标使用与 Stage2 相同的 CTDR 双接收 raw-LFM 正演，在背景 LFM 卷积后、"
              "新协议写盘前逐 PRT 叠加；`truth/truth_pulse.csv` 和 "
              "`truth/truth_targets_by_beam.csv` 保存独立目标 truth。\n\n";
    }

    md << "## 复现命令\n\n";
    md << "```bash\n./build/stage3_scene_mapping_selftest\n"
       << "./build/simulate_stage3_sar_scene --config " << opt.stage3_config
       << " --output-dir " << cfg.output_dir
       << " --backend " << cfg.forward.backend
       << " --dry-run " << (cfg.forward.dry_run ? "true" : "false") << "\n```\n\n";

    md << "## 必查证据\n\n";
    md << "1. `debug/mirror_index_validation.csv`：检查 reflect-101 索引序列。\n";
    md << "2. `truth/sar_surface_metadata.json`：检查锚点、坐标轴、原图尺度和有效网格数。\n";
    md << "3. `debug/mirror_seam_metrics.csv`：比较镜像边界与普通边的幅度差。\n";
    md << "4. `figures/sar_scene_amplitude.pgm`：人工检查场景非空、方向和镜像连续性。\n";
    md << "5. 非 dry-run 时检查 `logs/stage3_performance.csv`、新协议 BIN 大小和配套 XML 子集。\n\n";

    md << "## 限制与解读边界\n\n";
    md << "1. 强度 SAR 图不包含真实相干相位；当前固定地面网格的种子相位只用于可重复统计仿真。\n";
    md << "2. 0.3 m SAR 反射率纹理不等于 50 MHz 雷达的真实距离分辨率（约 3 m）。\n";
    md << "3. SAR 成像几何与 61 波束扫描几何不同；本模型是反射率场景近似，不是原始回波逆反演。\n";
    md << "4. 当前天线为高斯模型，平台为理想直线运动；实测方向图、姿态和导航误差需单独建模。\n";
    md << "5. 当前 TIFF 路径由 libtiff 解码像素，但不读取 GeoTIFF 地理标签；"
          "地面定位由显式像元尺寸、方向、参考波束和锚点定义。\n";
    md << "6. `georef_path`、`nodata_value`、`resample_mode`、旧版稀疏散射点提取参数"
          "目前不进入 dense SAR surface 正演；不得将这些占位字段解读为已生效。\n";
    md << "7. Stage3 已逐项解析 `targets` 数组、校验唯一 ID/名称，并在复数线性域"
          "依次叠加全部启用目标；逐目标 truth 是验收依据。\n";
    md << "8. CPU/CUDA comparison 比较的是目标公共后处理之前的 SAR 背景正演；"
          "目标注入本身为共享 CPU raw-LFM 路径。\n";
    return true;
}

} // namespace stage3
} // namespace gmti
