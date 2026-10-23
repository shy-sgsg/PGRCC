# R8 阶段研究方向裁决

日期：2026-10-23
裁决级别：三 seed 描述性 CUDA 因果可行性证据；不是总体性能或统计显著性结论。

## 对研究问题的回答

### 1. 未知通道时延是否降低 GMTI 任务表现？

在本次固定仿真场景、通道 2 注入 `+4 ns`、真实连续面积地杂波存在的条件下，结果支持“是”：A0/B/C 三 seed 门内正确目标 detection 分别为 `8/9、0/9、8/9`。B 三 seed 均为 `0/3`，而 A0 与 C 各 seed 为 `3/3、2/3、3/3`。这说明所测误差可导致该 Production 任务链无法把目标关联进冻结门；不应外推成任意机载时延都造成同等下降。

### 2. 已知物理校正恢复多少？

Known Correction 将观察到的 A0−B 检出计数差 `8` 全部恢复，C 达到 A0 的 `8/9` 总计数。confirmed current track matched 计数也由 B 的 `0/9` 回到 A0/C 各 `7/9`。三个 seed、ON/OFF/TO 的 A0/C 融合输入复 RMS 最大约 `9.56e-5`，优于冻结 `1e-3` 闭合限。该恢复量只对当前注入、场景、目标及冻结生产门成立。

### 3. 传统盲估计离已知校正还有多远？

本阶段没有合格盲估计器，D 条件为 `NOT_AVAILABLE`；未运行，也没有盲估计误差、恢复率或与 Known Correction 的差距数据。不能把 Known Correction 的结果冒充盲估计能力。

### 4. 是否继续未知物理状态估计？是否需要 AI？

建议继续验证**确定性的未知时延/通道状态估计**：B 的任务损失在三个 seed 重复出现，C 在相同输入误差下闭合并恢复观察到的检出缺口。这证明“估计并校正未知状态”值得下一步检验，但还没证明任何盲方法能做到。

当前没有 AI 必要性证据：没有盲估计基线与残差、跨场景恢复能力或显式物理估计失败证据。因此不训练 AI、不启用 Router。下一项若获批准，应先实现/审查一个 target-free 的确定性时延估计，再以盲条件 D 对照 A/B/C；估计器不得读取真实时延、目标真值、配对无误差参考或其他 oracle 信息。

### 5. 是否转向低速目标保护或复杂杂波建模？

现在不足以改变主线。面积杂波门确实有效，但只有一种场景配置；本实验没有速度 sweep、低速目标分层，也没有统计 Pfa 闭合。B 的目标邻域有 CFAR hits，但最近检测 Doppler 偏差落在 ±2-bin 门外，提示后续可检查候选保护与关联敏感性。该信号尚不能证明低速保护或复杂杂波建模是首要瓶颈。

## 阶段门结论

- `IMPLEMENTED=true`，`EXECUTED=true`，`EVALUABLE=true`，`PERFORMANCE_VERIFIED=false`。
- 有效面积地杂波：每个新 seed 390 packets、1,952,655 samples；输入历史身份 27/27 匹配；ON/OFF/TO 处理链、CFAR 几何/数值、物理闭合门通过。
- **本阶段在此停止。** 建议下一阶段先定义 blind deterministic delay estimator 的可观测量、允许输入/禁止信息、失败判据及 evaluator，再决定是否实现和执行；不自动启动后续实验或训练。

详见：[检出结果](R8_detection_causal_results.md)、[差异级分析](R8_mechanism_analysis.md)、[CUDA 执行记录](R8_native_cuda_execution.md)、[机器 manifest](evidence/r8_unknown_delay_multiseed_20261023/evaluation/r8_experiment_manifest.json)。
