# R22-03：确认实验状态

> **历史状态快照（2026-10-28）：** V2 screening 在一个 C4 Mode-B ON support gate 上 `GATE_FAILED` 后停止；当时确认性180-case、dense、bridge和H0批次均未启动。H0随后于2026-10-31单独完成60个无目标case，不解除target-present screening门；截至2026-11-04，confirmatory、dense、bridge仍未启动。当前筛选终态与后续进入条件见[05](05_resource_and_execution_plan.md)和[S1e审查请求](support_failure_diagnostic/CHATGPT_REVIEW_PROMPT_S1E_20261031.md)。

> **历史 V2 状态快照（2026-10-28 11:16 UTC）：** 本文件开头的 `NOT_STARTED` 与后续“不得启动”文字记录的是 pre-V2 设计状态。11:16时 V2 screening 正在执行；该运行随后因 C4 Mode-B ON support gate失败而于11:34:54 UTC停止。当前 target-present matrix仍为 `GATE_FAILED`，confirmatory 180-case仍未启动；独立 H0 后于2026-10-31完成。此历史快照不改变冻结统计门槛或质量门；当前终态见[05](05_resource_and_execution_plan.md)。

## Pre-V2 初始状态（历史快照）

`NOT_STARTED`。截至2026-10-27，R22 尚未开始完整 screening、confirmatory、40-target dense stress 或独立 H0 统计批次；当时真实 CUDA 运行仅为 seed900101/900202/900203 工程 pilot，不能替代确认实验。该状态已被上方 V2 执行快照取代；当前 screening 与 H0 状态以[05资源与执行记录](05_resource_and_execution_plan.md)及[动态研究状态](../../RESEARCH_STATUS.md)为准。

初始草案计划 screening 为 24 条条件 × 每条件至少 12 个独立 seed；confirmatory 为 6 条条件 × 每条件至少 30 个全新独立 seed；密集目标和 H0 另行计数。该设计量级尚未完成资源审计、seed 冻结与 PKR gate 验收，仍属计划，不是预注册后执行的方案。

## 暂停原因

PKR `range_band_bins=6` 与 `min_support=8` 产生确定性支持量冲突，导致所有参考组不可评估及 Production beam quality fail。整体 residual closure 是比较继续的上游门；目前不能以剔除 PKR、改阈值或只汇报成功方法的方式推进预定比较。

## 重新进入条件

1. 完成离线源码审计，明确 `support_count` 的统计单位与 range grouping 关系。
2. 以保持 `min_support=8` 和生产质量门为前提，提出并审查最小配置/分组修正；同步配置、文档和验证用例。
3. 在单独的工程 case 中证明 PKR reference 有有效组、Production beam quality gate 通过、CSI tap/CFAR geometry 完整，且 A0/PK/PKR residual closure 可计算。
4. 检查 Known 与盲方法的信息隔离、ON/OFF/TO 处理链、目标生成与输入配对。
5. 冻结新的设计、独立 seed、H0 抽样单位、统计分析代码与资源预算，并明确筛选与确认数据隔离。

当前不产生新的确认性 seed，也不自动重跑原大矩阵。
