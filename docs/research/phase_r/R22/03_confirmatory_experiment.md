# R22-03：确认实验状态

## 状态

`NOT_STARTED`。R22 没有开始完整 screening、confirmatory、40-target dense stress 或独立 H0 统计批次。当前真实 CUDA 运行仅为 seed900101/900202/900203 工程 pilot，不能替代确认实验。

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
