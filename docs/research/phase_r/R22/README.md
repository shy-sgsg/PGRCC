# R22 导航：多目标 Production pilot 与科研方向审查

**阶段状态：** 工程 pilot 已在本地 Native CUDA 上运行；完整 screening/confirmatory matrix 未执行。PKR 支持量 gate 失败后按门控停止。阶段 `INCOMPLETE / INCONCLUSIVE`，`PERFORMANCE_VERIFIED=false`。不训练 AI。

> **2026-10-28 续行状态：** 用户明确要求继续 R22。已修复显式 target-free H0 支持与产物紧凑化；在不降低 `min_support=8` 或 beam-quality gate 的前提下，以 `range_band_bins=133` 完成单 seed CUDA H0 工程检查。六个独立工程 seed、筛选/确认/密集目标/H0 扩展批次仍待执行。当前不得把该单 seed 描述为性能验证。请优先阅读 [延续执行契约](09_extension_execution_contract.md) 和[冻结扩展设计](config/r22_extension_experiment_design.json)；下方旧 R22 文件仍保留早期 pilot 的历史身份与限制。

## 建议阅读顺序

1. [00 总执行计划](00_master_execution_plan.md)：范围、门控与真实执行状态。
2. [01 实验设计](01_experiment_design.md)：研究问题、信息条件、比较边界。
3. [02 Pilot 结果](02_screening_experiment.md)：两 seed 单周期 CUDA 观察、有效面积杂波与停止根因。
4. [03 确认实验状态](03_confirmatory_experiment.md)与[04 统计分析计划](04_statistical_analysis_plan.md)：未执行部分及正确推断单位。
5. [05 资源与执行计划](05_resource_and_execution_plan.md)：设备、耗时和磁盘记录。
6. [06 科学方向裁决](06_scientific_decision.md)：阶段结论及后续边界。
7. [07 执行与质量审计](07_execution_and_quality_audit.md)：源码/二进制身份、门控、验证和限制。
8. [08 下一实验契约](08_frozen_next_experiment_contract.md)：PKR 修复前的冻结状态。
9. [ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT.md)：复制使用前填入完整审查 commit SHA。
10. [2026-10-28 延续执行契约](09_extension_execution_contract.md)：修复后门控、扩展矩阵、资源和保留策略。

## 关键证据

- [R13–R21 baseline 注册表](evidence/results/baseline_registry.csv)与[可比性矩阵](evidence/results/baseline_comparability_matrix.csv)
- [R22 机器可读实验 manifest](evidence/results/r22_experiment_manifest.json)
- [逐条件关键指标](evidence/results/r22_key_metrics.csv)
- [两 seed 描述性检测图](evidence/results/r22_descriptive_target_hits.png)及[生成脚本](../../../../scripts/phase_r/plot_r22_descriptive_hits.py)
- [面积杂波与配对审计](evidence/results/area_pairing_audit.json)
- [seed900101 Native CUDA 20 目标生成/Production smoke](evidence/preflight/seed900101/preflight_manifest.json)
- [seed900202 紧凑 pilot 证据](evidence/preflight/seed900202/README.md)
- [seed900203 PKR gate 失败证据](evidence/preflight/seed900203_closure/README.md)
- [清理台账](evidence/cleanup_ledger.md)

## 结果边界

seed900202/900203 均为 20 个目标、单 beam、单周期、+4 ns 的工程 pilot。A0/C0 和盲方法命中数可作描述性观察，不能把目标机会当独立 seed。seed900203 面积杂波有效且配对复用可追溯；PKR 六条角色记录不可评价，导致 A0/PK/PKR residual closure 不闭合。H0 经验 Pfa、confirmed track、径向速度与跨周期任务表现均未验证。Stage R22 的完整筛选和确认矩阵尚未启动。
