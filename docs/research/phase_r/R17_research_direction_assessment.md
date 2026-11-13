# R17 研究方向评估

日期：2026-10-25。依据：[R17 Production 身份审计](R17_production_identity_audit.md)、[R17 CFAR 分析](R17_CFAR_divergence_analysis.md)、[R16 结果](R16_minimal_c1_results.md)、[R14 结果](R14_minimal_comparison_results.md)、R7 GO-CFAR 数值根因与本次读取的研究总纲。没有运行新的 CUDA 或实验。

## 核心判断

R17 修正了 R14/R16 可比性解释：R14 的实际 Production binary 是 R7 修复前 GO-CFAR executable，不是由 R14 声明源码版本对应的修复后构建。R14 高 hit 与 R16 低 hit 因此首先是实现身份与数值正确性差异，不是 C0 方法、杂波 realization 或研究方法变化带来的任务收益。

这项审计服务于总纲中的广义 GMTI 物理处理链与可信任务评价目标，贡献属于**工程与数值可信度基础**。它没有新增未知系统误差因果证据，没有推进 Known-error correction 与 blind-estimator 差距量化，也不构成算法创新。

## 两层评价

### 阶段审计正确性

- Stage2 role-period 输入 identity 记录、F1/F2 与 CSI 输出 hash、runtime XML 处理参数和 CFAR geometry 在 R14/R16 比较记录中闭合。
- 实际 Production binary SHA 识别出 R14 pre-fix executable；R7 已对该旧实现的数值缺陷做过同输入验证。R16 运行记录表明修复后方向重算路径被调用。
- R14 原始 CFAR power/threshold/detector-input maps 已删除，不能计算 R14 专属的负/零阈值或逐 CUT 错误 hit 数；R14 的历史检测和 R15 association replay 仍保留为真实运行观察，但适用对象是修复前处理链。
- R17 是 offline audit；不声称本阶段 CUDA 执行、Production 性能验证或统计性能验证。

### 总体科研价值与方向

- **长期目标一致性：** 是。确认数值门限实现会直接改变 CFAR hits、候选集合和后续 GMTI 检测/跟踪评价，因此先统一生产实现身份是可信算法比较的必要条件。
- **科学问题进展：** R14/R16 都是零时延传统 baseline 证据，不回答未知通道误差是否降低 GMTI 性能。R17 没有为该问题增加对照。
- **传统方法边界：** R16 同运行 C0/C1 仍是可解释的单 seed 固定 Gamma=1 边界；R14 C2/C3 在修复前 CFAR 路径上的数值不能与 R16 作公平任务排名，当前没有修复后 C2/C3 结论。
- **AI 必要性：** 没有建立。当前主要问题是 build/executable identity governance 和已知 CFAR 数值正确性，不是需要学习模型的残差。
- **替代方向：** 本审计没有证据选择低速目标保护或复杂杂波建模为新主线。应先在统一的修复后生产链上复核有决策价值的传统比较。
- **总纲合理性：** R17 发现的工程问题不与长期 GMTI 目标或总体路线冲突，无需提出总纲修改。总纲只按用户明确授权恢复第 9 节的三段旧文本；没有把 R17 状态写进总纲。

## 裁决与停止条件

**`STOP`：停止将 R14 修复前与 R16 修复后 CFAR / Detection / TrackManager 结果跨版本排名。** R14/R15 文档保留原始历史数字，但其 C2/C3 候选集合和关联结论不得表述为修复后 Production 性能。

当前没有已授权的新实验。若用户决定继续，唯一建议是对 seed505 做一个有限的 C0/C2/C3 OFF/ON/TO Native CUDA Production 重比较，全部使用同一个确认修复后的 executable、同一组生成身份配对输入和同一 evaluator；保留 CFAR 实际 complex input、power、threshold、geometry 与 map hash。输入文件已清理，需重新生成并记录新 input SHA。未获用户启动前不执行。

若在同 binary、同输入和统一评价下 C2/C3 没有可复现的目标任务收益，停止把它们作为优先候选；若结果仍有任务差异，再依据目标保持、H0 可评价性、Detection 与当前周期 confirmed association 判断是否值得扩大独立 seed。单 seed 只支持可行性观察，不宣称显著性。

## 对核心问题的回答

1. **未知通道误差是否确实降低 GMTI 性能？** R17 不回答；本阶段没有运行未知误差条件。
2. **已知物理校正能够恢复多少？** R17 没有该条件，也没有可据此量化的结果。
3. **传统盲估计距离已知校正参考还有多少差距？** 尚无 R17 新证据；R14/R15 修复前 CFAR 任务输出不能用于更新该问题。
4. **是否有必要继续研究未知物理状态估计？** 总纲长期问题未被 R17 否定，但 R17 本身不构成继续或停止该方向的因果证据。
5. **是否应调整为低速目标保护或复杂杂波建模主线？** 没有证据支持调整。
