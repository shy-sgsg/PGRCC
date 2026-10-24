# R8 紧凑证据包

本目录由 seed101 的 R7 修复后结果与 seed202/303 的本机 CUDA Production 运行汇总而成。完整日志、runtime XML、逐 beam stage、CSI tap/数组、CFAR geometry/map、detection 与 TrackManager 原始结果仍在本地 `outputs/phase_r8_unknown_delay_multiseed_20261023/`，不在此目录重复存放。

## 文件导航

- `evaluation/r8_experiment_manifest.json`：源码/二进制/Stage2 身份、GPU 与磁盘快照、分支 XML/log/CSI/CFAR/lineage 路径、失败现场和历史输入摘要。
- `evaluation/r8_multiseed_causal_evaluation.json`：三 seed 的结构化条件结果、逐期任务关联、四个阶段状态标签和统计限制。
- `evaluation/r8_seed_condition_metrics.csv`：每 seed×A/B/C 条件的 CSI、目标机会/漏检、非目标 detections、confirmed-track 关联、CFAR branch hits、H0/H1 union hits、目标功率保持、参数误差和 F1/F2 输入融合功率；seed101 原始 Stage2 IQ 已清理，输入功率列为 `NOT_EVALUABLE`，新 seed202/303 可重算。
- `evaluation/target_neighborhood_cfar.csv`：seed202/303 每条件、ON/OFF、每期目标邻域 hit、最近候选偏移、冻结门匹配与阶段归因。
- `evaluation/r8_seed_condition_comparison.png`：目标检出率、SCNR proxy、目标复传递幅相与正确关联目标参数误差图。
- `evaluation/seed*/`：每角色 task metrics、关联/lineage CSV、CSI transfer 和历史输入 SHA 对照。

R8 结果为可行性和正确性验证；不声称统计显著的 Pd/Pfa 改善。详细结论见上级目录的 [R8 结果](../../R8_detection_causal_results.md)、[执行记录](../../R8_native_cuda_execution.md)、[机制分析](../../R8_mechanism_analysis.md) 与 [方向裁决](../../R8_research_direction_decision.md)。
