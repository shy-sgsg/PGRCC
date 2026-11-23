# R22-V4 确认性设计资源模型复核

日期：2026-11-23
范围：从 P0 唯一完整归档与 V4 源码计划推导资源；没有启动新矩阵。

## 1. 设计规模（源码推导）

| 类别 | 数量 |
|---|---:|
| target-present case identities | 180 |
| 独立 H0 case identities | 60 |
| 总 case identities | 240 |
| 已注册科学槽位 | 1,470（1,260 Production + 210 离线 M1 Batch-10 诊断） |
| 实际 Production 调用 | 2,520（含 1,260 个 runner 内部辅助调用） |
| Stage2 调用 | 1,200–1,440 |

0 ns 每 case 3 次 Production 调用，+4 ns 每 case 18 次。以上来自 V4 execution plan 和测试展开，不是已执行数量；`execution_authorized=false`、`cuda_authorized=false`。

## 2. P0 实测资源

- 完整 ZIP：13,037,254,578 bytes（13.037 GB）；必要 422 个 V4 成员在当前 ZIP 中的压缩数据合计 17,070,955 bytes（17.071 MB），未压缩 110,225,502 bytes。这里是成员数据大小之和，不是另行生成并实测的 compact ZIP 大小。
- P0 有 6 次 Stage2 调用（203.66 s）、18 个 Production 分支（431.15 s）；整个 case wall time 877.27 s。GPU 同时被外部训练占用，因此这些 wall time 不是性能基准。
- P0 测得峰值 free-space 消耗 29,821,104,128 bytes，运行最小剩余空间 27,726,299,136 bytes；该峰值说明运行中间目录超过 20 GB 是正常且可接受的资源现象，**20 GB 只用于最终审查和清理后的保留态**。
- 以 P0 422-member 压缩数据合计保守乘 240，结果数据估算 4.097 GB；加 25% 余量为 5.121 GB。模型对每类 case 都按完整 P0 allowlist 计费，但尚未生成 compact ZIP 来实测 ZIP/目录开销，也未用 H0 与 0 ns case schema 验证，因此不是最终保证。
- 如果把完整 13 GB P0 ZIP 复制到 240 个 case，粗略量级 3.129 TB；不应把完整 raw/CSI 包重复保留为每 case 最终归档。

更细的逐类别压缩字节、时间投影、free-space 门和局限见 [resource_model.json](evidence/v4_confirmatory_freeze_readiness/resource_model.json)、[case_plan_counts.json](evidence/v4_confirmatory_freeze_readiness/case_plan_counts.json) 与 [P0 类别表](evidence/v4_confirmatory_freeze_readiness/p0_resource_categories.csv)。

## 3. 磁盘策略

用户已明确：20 GB 是最终上限，允许试验中间空间暂时超过。按 V4 契约，运行阶段以卷真实剩余空间、单 case 原子完成能力和恢复余量门控，不把最终 20 GB 上限当作运行期间的硬上限。每个 case 先完成 compact archive 身份、CRC、schema、任务和 lineage 验证，再按 cleanup ledger 删除已被验证归档替代、可重建的临时数据。

本次重分析期间没有删除已有研究数据。唯一完整 P0 ZIP 保留到独立审查完成；它是当前复核的本地原始证据，GitHub 不含该文件。P0 compact 证据与报告验收且 ChatGPT/用户审查结束后，再按已授权清理要求删除大 ZIP 和可再生缓存，并记录保留/删除清单。

## 4. 资源决策

空间模型支持“逐 case 执行、逐 case 验证与整理”的设计方向，不支持将完整 P0 归档复制到全矩阵。未来任务开始前仍须重新检查 GPU、真实 free space、设备负载和试验分区；P0 的一次资源测量不能保证 240-case 长任务。当前只形成资源模型，**不启动尺寸 pilot 或确认性矩阵**。
