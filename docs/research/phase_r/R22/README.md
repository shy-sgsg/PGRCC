# R22 导航：多目标 Production pilot 与科研方向审查

> **最新状态（2026-10-31）：** 冻结的 R22 H0 矩阵已按 seed-level case retry 恢复并完成：30 个独立 seed、60/60 个唯一 0/+4 ns case，分析状态 `COMPLETE_EVALUABLE`。最终结果、配对杂波/噪声审计和精简审查证据见[最终报告](18_h0_false_alarm_checkpoint_20261030.md)及[最终证据包](evidence/h0_final_20261031/)；可直接交给 ChatGPT 的固定审查提示词见 [V4](CHATGPT_REVIEW_PROMPT_V4.md)。

> **仍有效的 Screening 门控：** R22 target-present V2 screening 仍为 `GATE_FAILED / INCOMPLETE`。seed1932001、2.5 m/s、30 dB、+4 ns 的 C4 Mode-B ON reference support=7，小于冻结的8。S1c 证明选定支持数对 CSI phase vector 敏感；补充审计确认目标响应与失败组/行重合，但目标真值距离单元只占 SNR20-only 支持格的3/24，具体相位变化贡献和任务收益仍未唯一确定。H0 无目标矩阵不解除该门控。详见 [S1c 结果](support_failure_diagnostic/S1C_SHARED_OFF_PHASE_RESULT.md)、[目标重合交叉表](support_failure_diagnostic/evidence/s1c/v2/s1c_target_overlap_audit.csv)与[固定快照审查提示词](support_failure_diagnostic/CHATGPT_REVIEW_PROMPT_S1C_20261031.md)。

> **S1d 补充诊断（2026-10-31）：** 使用 period-correct GMTI05 输入，同一 Native CUDA binary 完成7分支相位向量交叉实验。SNR30 support 为自身 ON=7、SNR20 ON 交叉=11、配对 OFF=22；后者22格中有4格与目标真值距离坐标重合，交叉 SNR20 的11格均为其他距离格。该空间标签仅用于事后审计，不进入估计，也不证明目标污染根因。冻结门槛未变，SNR30 原生失败仍保留。完整边界见 [S1d 结果](support_failure_diagnostic/S1D_PHASE_VECTOR_CROSSOVER_RESULT.md)、[精简审查证据](support_failure_diagnostic/evidence/s1d/)与[固定 ChatGPT 独立审查提示词](support_failure_diagnostic/CHATGPT_REVIEW_PROMPT_S1D_20261031.md)。本实验不解除 target-present Screening gate，也没有 GMTI task performance 证据。

> **历史检查点（2026-10-30）：** 当时 29/60 case 完成，seed1935015/+4 ns 的 attempt001/002 尚未完成；其归档与检查点数据保留在[历史证据目录](evidence/h0_checkpoint_20261030/)。最终终态以 2026-10-31 报告及其 matrix audit summary 为准。

> **最新 V2 状态（2026-10-28 11:34:54 UTC）：** screening matrix 为 `GATE_FAILED` 并已停止：17 个通过，1 个方法级不可评估 case，4 条更早中断 attempt；无活动 case。根因是 `seed1932001 / 2.5 m/s / 30 dB / +4 ns` 的 C4 Mode-B ON robust reference 有一组 support=7 < 8。详情、失败 ZIP 和当前审查入口见 [05 资源与执行记录](05_resource_and_execution_plan.md) 与 [ChatGPT checkpoint 提示词](CHATGPT_REVIEW_PROMPT_SCREENING_CHECKPOINT_20261028.md)。后续旧的“screening 正在执行”提示属于较早 checkpoint。

**早期 pilot 状态（2026-10-27）：** seed900101/900202/900203 的本地 Native CUDA 工程运行已归档；PKR 支持量 gate 失败后停止了当时的比较。阶段 `INCOMPLETE / INCONCLUSIVE`，`PERFORMANCE_VERIFIED=false`。这些是历史结果，不代表本次 2026-10-28 延续工程 case 已进入 Production。

> **历史 V2 screening 延续快照（截至 2026-10-28 11:16 UTC；随后已停止）：** 固定设计共 594 个 case。seed1932001、0.5 m/s、10 dB、20 targets、10 periods 的 0 ns 与 +4 ns 配对 case 均已通过单 case 工程门；+4 ns attempt004 有 54/54 method/mode/role branches 通过，紧凑 ZIP 与逐文件 SHA 已核验。单 seed 仍不能支持统计性能结论。该时间点完整 288-case screening 已恢复运行，但随后因 C4 Mode-B ON reference support=7<8 触发 `GATE_FAILED` 并停止；当前状态见本文件顶部及最新 matrix manifest。中间实验目录允许超过 20 GB；20 GB 是结果汇总、ChatGPT 审查完成并按清理台账删除可再生大数组后的最终项目占用上限。当前 checkpoint 的独立审查材料见 [V2 screening ChatGPT 审查提示词](CHATGPT_REVIEW_PROMPT_SCREENING_CHECKPOINT_20261028.md)。

> **2026-10-28 V1A 延续执行状态：** 首个 0 ns case（seed1930101）已进入真实 Native CUDA Production，但 C4/PKR robust gamma reference 有少数 133-bin groups 未达到 `min_support=8`，矩阵按 fail-closed 规则 `GATE_FAILED` 并停止。Stage2 连续面积杂波有效。该 case 不可评价，不能据此比较任务性能。早先盲校正预处理失败仍保留在 [原工程门失败记录](10_engineering_gate_failure_20261028.md)；本次实际生产执行、gate 根因和[精简审查证据](evidence/extension_engineering_v1a/seed1930101/)见[V1A 工程门复测记录](11_v1a_engineering_gate_result_20261028.md)。其余工程 seeds、screening、confirmatory、dense、bridge 和独立 H0 均未启动。

## 建议阅读顺序

1. [00 总执行计划](00_master_execution_plan.md)：范围、门控与真实执行状态。
2. [01 实验设计](01_experiment_design.md)：研究问题、信息条件、比较边界。
3. [02 Pilot 结果](02_screening_experiment.md)：两 seed 单周期 CUDA 观察、有效面积杂波与停止根因。
4. [03 确认实验状态](03_confirmatory_experiment.md)与[04 统计分析计划](04_statistical_analysis_plan.md)：未执行部分及正确推断单位。
5. [05 资源与执行计划](05_resource_and_execution_plan.md)：设备、耗时和磁盘记录。
6. [06 科学方向裁决](06_scientific_decision.md)：阶段结论及后续边界。
7. [07 执行与质量审计](07_execution_and_quality_audit.md)：源码/二进制身份、门控、验证和限制。
8. [08 下一实验契约](08_frozen_next_experiment_contract.md)：PKR 修复前的冻结状态。
9. [2026-10-28 延续执行契约](09_extension_execution_contract.md)：冻结门控、扩展矩阵、资源和保留策略。
10. [工程门失败记录](10_engineering_gate_failure_20261028.md)：本次 seed1930101 的真实执行、输入配对、盲估计和失败边界。
11. [V1A 工程门复测记录](11_v1a_engineering_gate_result_20261028.md)：真实 CUDA Production 执行、133-bin support gate 失败和结论边界。
12. [ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT.md)：要求 ChatGPT 读取指定 commit，并审查当前 V1A gate failure 与总体研究价值；交付后填入完整审查 commit SHA。
13. [V2 距离支持域修订](12_v2_range_support_domain_amendment_20261028.md)：说明由面积杂波/目标物理 ROI 推导的共同闭区间 `[666,1197]`、V1A 与 V2 的版本隔离及六 seed 工程门。V2 配置和设计分别为 [方法配置](config/r22_method_config_v2_range_roi.json) 与 [机器可读设计](config/r22_extension_experiment_design_v2_range_roi.json)。
14. [V2 首个配对工程 seed 结果](13_v2_engineering_seed1930101_pair_20261028.md)：seed1930101 的 0 ns / +4 ns 本机 Native CUDA 配对结果；这是一个 seed 的可行性证据，不是统计验证。对应的 [ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT_V2.md) 要求先读指定研究总纲，并附带压缩证据包 `outputs/r22_chatgpt_review_evidence_v2_engineering_pair_20261028.zip`（约 23 MB）。
15. [V2 六 seed 工程结果](15_v2_six_seed_engineering_results_20261028.md)：六个 seed、12 个最终有效 CUDA Production case 的描述统计、审计边界和资源门；显式记录 seed1930101 与后续 seed 的 Stage2 binary 版本差异。
16. [V2 六 seed ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT_V3.md)：用于审查总体科研价值、六 seed 结果、Stage2 Rmin 精度修复及是否需要重跑 seed1930101；精简审查包位于 `outputs/r22_chatgpt_review_evidence_v3_six_seed_20261028.zip`。
17. [V2 screening checkpoint ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT_SCREENING_CHECKPOINT_20261028.md)：可直接复制给 ChatGPT，要求先读取本次研究总纲并核查最新矩阵 manifest、运行证据和总体科研价值；本地 `outputs/` 中的 manifest、日志与 case ZIP 需一并附上。
18. [R22 H0 检查点与最终报告](18_h0_false_alarm_checkpoint_20261030.md)：保留 2026-10-30 的 29/60 检查点，并记录 2026-10-31 恢复后的 60/60 最终结果。
19. [V4 ChatGPT 独立科研审查提示词](CHATGPT_REVIEW_PROMPT_V4.md)：固定审查 commit、优先读取总纲并开展阶段正确性与总体科研价值双层审查。最终 CSV、manifest、矩阵审计与清理记录见 [H0 最终证据包](evidence/h0_final_20261031/)；60 个 compact case ZIP 与 raw IQ 未上传，限制已写入提示词。
20. [S1c 共同 OFF phase 结果](support_failure_diagnostic/S1C_SHARED_OFF_PHASE_RESULT.md)、[目标重合交叉表](support_failure_diagnostic/evidence/s1c/v2/s1c_target_overlap_audit.csv)与[S1c 补充证据独立审查提示词](support_failure_diagnostic/CHATGPT_REVIEW_PROMPT_S1C_20261031.md)：固定审查到明确 commit，评估 phase-vector 干预、目标坐标/响应及当前因果解释边界。
21. [S1d phase-vector crossover 结果](support_failure_diagnostic/S1D_PHASE_VECTOR_CROSSOVER_RESULT.md)、[紧凑 Production 证据](support_failure_diagnostic/evidence/s1d/)与[固定快照 ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT_S1D_20261031.md)：固定审查到 `781bf3d96bb327a423eab944ce09a13a65c85ad9`，检查 S1d 执行正确性、S1c/S1d 因果边界及对整体 GMTI 方向的价值。

## 关键证据

- [R13–R21 baseline 注册表](evidence/results/baseline_registry.csv)与[可比性矩阵](evidence/results/baseline_comparability_matrix.csv)
- [R22 机器可读实验 manifest](evidence/results/r22_experiment_manifest.json)
- [逐条件关键指标](evidence/results/r22_key_metrics.csv)
- [两 seed 描述性检测图](evidence/results/r22_descriptive_target_hits.png)及[生成脚本](../../../../scripts/phase_r/plot_r22_descriptive_hits.py)
- [面积杂波与配对审计](evidence/results/area_pairing_audit.json)
- [seed900101 Native CUDA 20 目标生成/Production smoke](evidence/preflight/seed900101/preflight_manifest.json)
- [seed900202 紧凑 pilot 证据](evidence/preflight/seed900202/README.md)
- [seed900203 PKR gate 失败证据](evidence/preflight/seed900203_closure/README.md)
- [V1A seed1930101 精简工程审查证据](evidence/extension_engineering_v1a/seed1930101/README.md)：Stage2 杂波报告、实际 XML/log、CSI tap、CFAR geometry、robust gamma failure rows、case/matrix 摘要与 cleanup ledger。
- [清理台账](evidence/cleanup_ledger.md)
- [2026-10-28 历史大数组清理记录](14_storage_cleanup_20261028.md)
- [2026-10-30 H0 检查点审查证据](evidence/h0_checkpoint_20261030/README.md)
- [2026-10-31 H0 最终精简审查证据](evidence/h0_final_20261031/README.md)

## 历史 preflight 结果边界（2026-10-27）

seed900202/900203 均为 20 个目标、单 beam、单周期、+4 ns 的工程 pilot。A0/C0 和盲方法命中数可作描述性观察，不能把目标机会当独立 seed。seed900203 面积杂波有效且配对复用可追溯；PKR 六条角色记录不可评价，导致 A0/PK/PKR residual closure 不闭合。H0 经验 Pfa、confirmed track、径向速度与跨周期任务表现均未验证。Stage R22 的完整筛选和确认矩阵尚未启动。

## V2 六 seed 工程结果历史边界（2026-10-28）

六个 seed 的 0 ns / +4 ns 工程 case 已完成，结果见 [V2 六 seed 工程结果](15_v2_six_seed_engineering_results_20261028.md)。最终 12 个 case 均 `IMPLEMENTED / EXECUTED / EVALUABLE`，但 `PERFORMANCE_VERIFIED=false`。seed1930101 使用精度修复前的 Stage2 binary；seed1930102 修复重跑及 seed1930103–1930106 使用修复版。该二进制版本边界和是否需要补跑 seed1930101，须在下一步决策前审查。当前完整 594-case 资源门未通过，不启动 screening；没有 AI 训练或 Router 开发。
