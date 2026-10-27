# R22 导航：多目标 Production pilot 与科研方向审查

**早期 pilot 状态（2026-10-27）：** seed900101/900202/900203 的本地 Native CUDA 工程运行已归档；PKR 支持量 gate 失败后停止了当时的比较。阶段 `INCOMPLETE / INCONCLUSIVE`，`PERFORMANCE_VERIFIED=false`。这些是历史结果，不代表本次 2026-10-28 延续工程 case 已进入 Production。

> **2026-10-28 最新执行状态：** 扩展工程矩阵的首个 0 ns 条件（seed1930101）完成 Stage2 杂波/配对审计，但在盲时延校正输入前提处 GATE_FAILED，未进入 Production。其余 11 个工程 case、screening、confirmatory、dense、bridge 和独立 H0 均未启动。无任务性能结论；详见[工程门失败记录](10_engineering_gate_failure_20261028.md)和[紧凑证据包](evidence/extension_engineering/seed1930101/)。当前 ChatGPT 审查提示词已更新为本次真实执行快照。

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
11. [ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT.md)：针对本次 gate failure 的双层科研审查请求；交付后填入完整审查 commit SHA。

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
