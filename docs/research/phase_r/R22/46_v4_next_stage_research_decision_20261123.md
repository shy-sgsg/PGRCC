# R22-V4 下一阶段研究方向裁决

日期：2026-11-23
依据：[P0 独立重分析](41_v4_p0_independent_evidence_reanalysis_20261123.md)、[物理机制与基线差距](42_v4_physical_mechanism_and_baseline_gap_20261123.md)、[资源模型](43_v4_confirmatory_resource_model_revision_20261123.md)、[统计审查](44_v4_confirmatory_statistical_design_review_20261123.md)。

## 1. 阶段状态

| 对象 | IMPLEMENTED | EXECUTED | EVALUABLE | PERFORMANCE_VERIFIED |
|---|---|---|---|---|
| V4 causal-prefix / complete-output runner 与草案 | 是 | 不代表矩阵运行 | 源码与 CPU 证据可审查 | 否 |
| P0 Native CUDA 单 case | 是 | 是，见 P0 原始运行包 | 本场景任务/lineage/CFAR 描述结果可评价 | 仅限单 seed 描述值；总体性能未验证 |
| 本次 P0 离线重分析 | 是 | 是，不运行 GPU | 参数、航迹、CFAR 与目标响应复算通过 | 不适用 |
| 30-seed V4 确认矩阵 | 草案 | 否 | 否 | 否 |
| 独立 H0 确认矩阵 | 草案 | 否 | 否 | 否 |

## 2. 对核心问题的当前回答

1. **未知通道误差是否确实降低 GMTI 性能？** 对 P0 固定的 +4 ns、2.5 m/s、20 dB 单场景，Current 为 26/200、A0/Known 为 140/200，支持该条件下降低目标检测与参数输出的判断。只有一个确认外的工程 seed，不能外推为普遍规律。
2. **已知物理校正恢复多少？** 本场景正确关联覆盖从 13% 到 70%，增加 57 pp，达到 A0 的命中数；A0 与 Known 的共同成功定位 RMSE 差约 0.000063 m。该恢复量是单 seed 描述值，不是总体效应估计。
3. **传统盲估计距 Known 还有多少？** M1 的命中数同为 140/200；在 M1/PK 的 134 个共同成功机会中，M1 定位 RMSE 43.872 m、Known 26.684 m，M1 高约 17.188 m。Fixed D3 与 Huber 各命中142/200；单 seed 不足以量化稳定差距。
4. **是否继续研究未知物理状态估计？** 继续到独立确认性审查：已观察到可恢复任务空间，也有同信息确定性/因果方法候选，但 V4 causal-prefix 证据仅一个 seed，工程阈值未定。不得把继续审查等同于确认创新成立。
5. **转向低速保护或复杂杂波建模吗？** 目前不转向。P0 没有速度层、密度、杂波非平稳性或相应可恢复任务效应的独立比较；这些仍是 `NOT_VERIFIED`。
6. **AI 是否必要？** 当前 `AI_ENTRY_GATE=FAIL`。还未在代表性 V4 seed 上证明强传统方法留下稳定且有任务意义、确定性方法无法充分恢复的残差；不训练 AI、不开发 Router。

## 3. 唯一下一步

先完成本快照 ChatGPT 独立审查与用户/工程需求讨论，只闭合 V4 冻结候选阈值、common-success 缺失规则、H0 边界和 seed registry。审查通过且用户明确批准后，才决定是否进入版本化冻结；冻结后仍需单独明确授权尺寸 pilot 或 CUDA 矩阵。

本阶段停止在离线证据分析和冻结候选。没有新的 CUDA 运行，没有修改算法或实验参数，没有自动开始 240-case 矩阵，也没有修改研究总纲。
