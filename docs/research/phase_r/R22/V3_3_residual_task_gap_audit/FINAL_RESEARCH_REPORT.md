# R22-V3.3 确定性校正后的 GMTI 任务残差审计

**阶段状态：** `OFFLINE_AUDIT_COMPLETE / PERFORMANCE_NOT_VERIFIED`

**研究裁决：** `CONTINUE` 有界确定性物理状态估计的确认性评价；`AI_ENTRY_GATE=FAIL`。

**审查输入：** R22 V3.2 固定设计与 144 个 `+4 ns` compact case ZIP。

**总纲：** `docs/research/RESEARCH_MASTER_PLAN.md` blob `e637e3769bde7814298ff012d0f42301884e2985`，本阶段未修改。

## 结论摘要

离线复核 12 个独立 seed、4 种速度、3 档 SNR 的 144 个 `+4 ns` 场景。所有使用中的 archive SHA 均与 V3.2 冻结索引一致；每个 case 的面积杂波计数为 1,300 packets、6,508,782 samples。A0 no-error 的命中率为 **69.729%**，Current +4 ns 为 **11.694%**，Known correction 为 **69.729%**。target-present 的 Fixed D3、Huber D3 ON、M1 命中率分别为 **69.705%、69.767%、69.767%**。这些是筛选描述，不是统计显著性或已确认的总体性能结论。

核心 target-present deterministic estimators 在检测上接近 Known，但参数仍有残差：唯一可重建完整共同机会的 period 8 子集中，四方法共同成功为 1,982/2,880；M1 方位 RMSE 为 0.03197°、位置 RMSE 为 48.48 m，Known 分别为 0.01782°、27.08 m。完整十周期共同参数、地速共同成功、有效径向速度和 target-only 复数幅相均不可评价。故“检测恢复接近 Known”不能被写成“GMTI 全部任务指标已恢复”。

本证据支持继续一次边界明确的 target-present deterministic confirmation，先补齐共同参数机会、TrackManager lineage 和目标专属复数响应。它不支持直接转向 AI、低速保护或复杂非平稳杂波。

## 研究问题回答

| 问题 | 本轮回答 |
|---|---|
| 固定 +4 ns 是否降低 GMTI 任务表现？ | 在这套仿真、当前 Production 与检测分母下，Current 相对 A0/PK 有明显的筛选命中下降；不外推到一般未知误差或真实平台。 |
| Known correction 恢复多少？ | `Known − Current` 的 seed 等权命中空间为 58.035 个百分点；Known 汇总命中与 A0 相同。 |
| target-present 确定性估计距 Known 多远？ | 检测命中接近 Known；period 8 共同成功参数误差仍落后，M1 相对 Known 的位置和角度残差可见。无法评价全十周期共同参数。 |
| 是否需要继续研究未知物理状态估计？ | 可继续有界的确定性估计确认，但当前残差尚未证明为稳定且有任务重要性的创新缺口。 |
| 是否改为低速目标保护或复杂杂波？ | 暂无证据支持变更；复杂非平稳杂波未被本轮操纵。 |
| 是否需要 AI？ | 否。`AI_ENTRY_GATE=FAIL`；传统 target-present Huber/M1 的检测表现已接近 Known，参数残差需先在确认集上验证并与确定性替代法比较。 |

## 主要量化结果

### 检测和恢复

| 方法 | 命中率（12 seed 均值 ± seed 间 SD） | 机会覆盖 |
|---|---:|---:|
| A0 no-error | 69.729% ± 5.139% | 12/12 seed |
| Current +4 ns | 11.694% ± 2.264% | 12/12 |
| PK Known | 69.729% ± 5.139% | 12/12 |
| Fixed D3 | 69.705% ± 5.160% | 12/12 |
| Huber D3 ON | 69.767% ± 5.146% | 12/12 |
| M1 Physics Matched | 69.767% ± 5.108% | 12/12 |

Known−Current 的恢复空间为 58.035 个百分点。Fixed D3、Huber、M1 分别恢复 58.010、58.073、58.073 个百分点；平均 recovery ratio 为 99.970%、100.071%、100.074%。略高于 100% 是有限筛选汇总的比值，不代表超过物理上界，也不建立优于 Known 的结论。

### 参数误差

period 8 的四方法共同成功集为 1,982/2,880 个预定机会（68.82%）。下表是 seed 均值 ± seed 间 SD：

| 参数 | PK Known | Fixed D3 | Huber D3 ON | M1 |
|---|---:|---:|---:|---:|
| 方位 RMSE（deg） | 0.01782 ± 0.00184 | 0.04214 ± 0.00288 | 0.03678 ± 0.00366 | 0.03197 ± 0.00346 |
| 位置 RMSE（m） | 27.08 ± 2.78 | 63.87 ± 4.37 | 55.75 ± 5.55 | 48.48 ± 5.23 |
| 斜距 RMSE（m） | 0.7162 ± 0.0526 | 0.7197 ± 0.0578 | 0.7234 ± 0.0540 | 0.7224 ± 0.0536 |
| 折叠 Doppler RMSE（Hz） | 3.173 ± 0.504 | 3.226 ± 0.555 | 3.269 ± 0.543 | 3.267 ± 0.564 |

A0 与 PK 的归档总命中均为 20,082/28,800；142/144 个 case 的 hit count 一致。保存的 full-period own-success 参数汇总也接近，但它不是逐机会重算且方法成功集合不同。地速共同成功需要的逐周期 track debug 不在 compact ZIP 中；径向速度没有有效样本。

### 航迹和时延

归档 TrackManager 汇总显示，Current 的目标确认周期和确认后匹配率低于 A0/PK；target-present deterministic estimators 的汇总接近 Known。false-confirmed track 不等于系统 Pfa。

平均有符号时延残差（估计值减真值）为 Fixed D3 −0.5900 ns、Huber D3 ON −0.4555 ns、M1 −0.3342 ns。非零时延估计残差并未在检测命中上形成明显差距，但是否解释角度/位置残差尚未证实。

## 质量、身份与资源

- V3.2 实验源码 commit：`74bccce61f3ec2f74746512d67271c815ce6a3a8`。
- V3.2 design SHA-256：`9fcc56e27c3a78a96ccfbecdf6ef1b3098fb1298d4bac9886058ea9af6081063`。
- archive index SHA-256：`a5bd66302acc12ac410ad4f8560ecd7e137ba40d4e54a09abd7eaadcf4d8bdd1`。
- V3.3 analyzer SHA-256：`9cccfc8f3b0745c67171ade7d8397cebc9e8db22a81e06beedeb1fc91643150d`。
- 144/144 使用中的归档 SHA 与索引一致；全部 144 case 连续面积杂波为正。归档直接读取，没有复制、解压、删除或改写。
- 本轮未运行 Stage2、Production、CUDA 或 AI；无新增实验数据。
- 本机完整 V3.2 ZIP 仍在 `outputs/r22_v3_2_screening_20261106/archives/screening/`，未上传 GitHub。Git 审查包提供重算结果及 archive identities，不包含原始 IQ 和完整运行日志。
- 离线分析输出约 4.5 MB；工作树约 4.8 GiB，文件系统剩余约 12 GiB。只移除了本轮自建的 Matplotlib 临时缓存，没有清理既有数据或归档。

## 状态和下一步

| 标签 | 本轮状态 |
|---|---|
| `IMPLEMENTED` | V3.3 离线分析器、机器证据和报告已生成 |
| `EXECUTED` | 144 个 compact archive 已按索引离线核验并分析 |
| `EVALUABLE` | 检测/航迹筛选指标及 period 8 参数共同成功子集；范围受归档产品限制 |
| `PERFORMANCE_VERIFIED` | `false` |

**唯一建议：** 若用户继续，先冻结 output-complete 的 R22-V4 确认性契约，覆盖全十周期 Detection/TrackManager lineage、目标专属复数幅相、共同参数机会和独立 H0 定义；之后再由用户决定是否运行。建议范围为 30 seed × 3 速度 × 20 dB × 2 时延，共 180 case，仍须在契约阶段核定统计设计与资源成本。此建议不授权 CUDA 执行。

本阶段到此停止，不自动启动新实验。分析表、图、逐项限制和复现命令见本目录 [`README.md`](README.md) 与 [`evidence/manifest.json`](evidence/manifest.json)。
