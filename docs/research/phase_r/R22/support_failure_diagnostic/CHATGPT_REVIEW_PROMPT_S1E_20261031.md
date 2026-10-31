# PGRCC R22-S1e 当前 Mode-B 三角色诊断：独立科研审查请求

> **请首先读取固定审查快照中的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路与长期技术路线，再审查本阶段结果。不能只评价局部实验是否完成，还要判断它是否推进总体 GMTI 目标，以及研究方向是否仍有证据支持。**

## 固定审查身份

- 仓库：<https://github.com/shy-sgsg/PGRCC>
- 分支：`codex/unknown-system-error-next-stage`
- **阶段报告、状态、决策和紧凑证据的审查快照：** `d2462657803375c3b0ebf45076d8eb4b94d63a8e`
- **研究总纲 blob：** `e637e3769bde7814298ff012d0f42301884e2985`
- Production 源码 commit：`461fbd949ea7c024c28f30e7aea36ae9642a4456`
- Production binary SHA-256：`c7db0d12f930b297851f1b3b53f3e56de5cafef0fa90394595de0b8babf7d7c9de`
- 阶段：R22-S1e，当前 Mode-B 配对 ON/OFF/TO 单周期因果诊断

请只用上述 commit 读取阶段报告、动态状态和紧凑证据。若远端 HEAD 已变化，请报告两个身份，不得用 HEAD 文件替换此快照。

## 必须先后读取

1. [固定快照研究总纲](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/RESEARCH_MASTER_PLAN.md)
2. [动态研究状态](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/RESEARCH_STATUS.md)、[研究决策日志](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/RESEARCH_DECISION_LOG.md)、[ChatGPT 审查协议](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/CHATGPT_REVIEW_PROTOCOL.md) 和 [AGENTS.md](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/AGENTS.md)
3. [Phase R 导航](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/phase_r/README.md) 和 [R22 导航](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/phase_r/R22/README.md)
4. [R22 Screening gate 审查](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/phase_r/R22/SCREENING_GATE_REVIEW.md) 与 [S1e 结果报告](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/phase_r/R22/support_failure_diagnostic/S1E_MODEB_ROLE_TRIPLET_RESULT.md)
5. [S1e manifest](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1e/s1e_manifest.json)、[分析 JSON](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1e/s1e_analysis.json)、[逐距离格复数审计表](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1e/s1e_complex_cell_audit.csv)、[逐文件 SHA/大小清单](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1e/evidence_manifest.json)
6. 核对 [S1e runner](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/scripts/phase_r/run_r22_s1e_modeb_role_triplet.py)、[离线分析器](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/scripts/phase_r/analyze_r22_s1e_modeb_role_triplet.py)、[Production adapter](https://github.com/shy-sgsg/PGRCC/blob/461fbd949ea7c024c28f30e7aea36ae9642a4456/src/production_calibration_adapter.cpp) 和紧凑证据中的三个 ON/OFF/TO runtime XML、branch manifest、运行日志、phase trace、CSI tap、selected-cell 复数结果及 CFAR geometry。
7. 为判断证据如何累积，可读取 [S1d phase-vector crossover 结果](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/phase_r/R22/support_failure_diagnostic/S1D_PHASE_VECTOR_CROSSOVER_RESULT.md)、[S1c 共同 OFF phase 结果](https://github.com/shy-sgsg/PGRCC/blob/d2462657803375c3b0ebf45076d8eb4b94d63a8e/docs/research/phase_r/R22/support_failure_diagnostic/S1C_SHARED_OFF_PHASE_RESULT.md) 及其提示词固定的历史证据；仅在改变判断所必需时查看更早的 S1b 材料。

如指定快照、总纲或关键证据不可读，请列出具体缺失项并将相关判断标为 `NOT_VERIFIED`；不能用本提示词里的摘要代替实际读取。

## S1e 固定问题与已报告观察

S1e 在 seed1932001、2.5 m/s、30 dB、+4 ns、20 targets 的同一配对场景中，对 GMTI05（Stage2 零基 period 4）、beam 50、azimuth row 52、range 1065–1197 执行 ON、OFF、TO 三个当前 Mode-B Native CUDA Production 分支。复用已保存 Stage2 输入，未重跑 Stage2。三分支使用相同 binary、Mode-B runtime XML、133-bin group、`min_support=8`、相位中心残差门限 `±0.35 rad`；没有降低门槛或替换失败 reference。

报告的关键观测：

- C4 reference support：ON=7（`NOT_EVALUABLE / insufficient_robust_support`）、OFF=55、TO=14；各自 Production return code 为0。ON 的原生失败保留。
- 选定133个距离格上，range-phase 校正前 `ON−OFF−TO` relative-L2：raw DBS F1/F2=`0.009367/0.018056`；CSI DBS=`0.010316/0.018056`。每个角色独立估计 CSI range-phase vector 并分别校正后，C4 adapter 前 F1/F2=`0.010316/0.787703`。
- TO-only 能量约71.13%/71.31%落在11个目标真值距离格；目标格 TO 幅度明显高于其余122格。
- ON/OFF CSI phase-vector wrapped 差的133格均值为1.824229 rad；目标真值距离格均值1.727056 rad，其他格1.832990 rad。简单的“相位差只在目标距离格局部增大”没有得到支持；不能排除估计器全局响应或目标-背景交互。
- ON 保留的7个 support cells 与目标真值距离格无交集；OFF 的55格有3格与这些坐标重合（OFF没有目标信号）；TO的14格也无交集。support成员交集 ON/OFF=1、ON/TO=0、OFF/TO=7。
- 每角色两条 CFAR geometry 记录均有527,280 valid CUT/threshold tests；false CUT/cluster/track 指标因 truth join 状态为 `NOT_EVALUABLE`。这些记录不构成 Pd/Pfa 或任务性能结果。
- 独立逐格复算的 robust support 与 Production Gamma 记录一致。

## 第一层：审查阶段正确性和因果边界

请核查并判断：

1. 固定 commit 的研究总纲、动态状态、R22 gate 和 S1e 报告是否互相一致；报告是否准确地区分“本次三分支 Production 成功执行”与“ON reference 不可评价”。
2. 检查 ON/OFF/TO 输入身份、Stage2 background reuse mapping、面积杂波证据、signal-only TO 配置、runtime XML、Production binary/source 身份及 truth 隔离。A1 角色报告中注入计数为0时，是否正确解释为复用了 A0 背景而不是没有杂波？
3. 沿代码和数据核验 raw/CSI DBS 的 `ON−OFF−TO` 复数闭合、逐角色 CSI phase correction、phase-vector 差、robust support筛选和成员集合。评估用各角色独立校正后的非闭合能否支持“角色相关 CSI 相位校正是 support 变化的近端机制”，以及该推论还缺什么反事实。
4. 检查目标真值只用于事后 evaluator 标签；评估支持格与真值坐标重合、TO空间集中度及 ON/OFF CSI phase差的证据强度。区分“目标在目标距离坐标”与“目标响应污染了 azimuth row 52 的参考估计”。
5. 检查冻结门限是否始终保持，是否存在 fallback、目标真值参与 Production、错误周期映射或漏报失败分支。
6. 核查 CFAR valid CUT 的含义与分母；不要把它误称为独立样本、检测数、Pd 或 Pfa。
7. 列明可从该 Git 快照直接复核的结论，以及因 raw Stage2 IQ/target truth 原件未纳入 Git、没有独立 Stage2→Production 重放而属于 `NOT_VERIFIED` 的结论。
8. 分别给出 `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED` 状态。

## 第二层：总体 GMTI 研究价值和方向

请先基于实际读取的研究总纲，独立回答：

1. S1e 对“传统 GMTI 方法能力边界与真实瓶颈识别”增加了什么证据？这是物理机制证据、算法实现证据还是任务收益证据？
2. S1c/S1d/S1e 合并后，是否足以把 C4 failure 唯一归因为目标污染？哪些竞争解释仍成立，例如分支特定 CSI 估计器响应、噪声/背景交互、空间非平稳性或算法参考支持脆弱性？
3. R22 target-present Screening 是否应继续保持 `GATE_FAILED / INCOMPLETE`？准确列出恢复 screening 前的最小 gate 条件；不得把本次成功的三个进程等同于 screening gate 通过。
4. 对继续研究未知物理状态估计、低速目标保护、复杂/非平稳杂波建模，分别标为 `SUPPORTED / NOT_SUPPORTED / NOT_VERIFIED` 并给证据。
5. AI 是否有必要？若无，指出当前仍应优先检验的确定性传统方案和缺失的任务级证据。
6. 总纲本身是否仍符合最新证据？如建议调整，只提出具体、有证据的提案及对既有成果的影响，交用户讨论；未经用户针对具体范围明确批准，不得修改总纲。

## 审查输出

请按 `docs/research/CHATGPT_REVIEW_PROTOCOL.md` 的双层审查与完整输出结构，给出：

1. 审查身份、固定 commit、实际读取材料及限制；
2. 阶段裁决：`PASS / PASS_WITH_LIMITATIONS / FAIL / INCONCLUSIVE`；
3. 阶段正确性、可核事实与 `NOT_VERIFIED` 项；
4. 状态表：`IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED`；
5. 相对研究总纲的实际进展及不能支持的结论；
6. 科研方向裁决：`CONTINUE / STOP / PIVOT`，含证据和停止条件；
7. 是否允许恢复 Screening；若否，给出解除 gate 的最小前置条件；
8. **只给一个**下一步优先任务，说明最小输入、交付物、验收和停止条件，以及什么结果会改变你的方向建议；
9. 总纲是否需要修改建议，明确这只是建议、需要用户批准。

请把“本次诊断跑通”与“C4 reference 有效”“目标任务性能改善”“统计显著性”严格分开。除非用户另行授权，不要启动 CUDA 实验、重跑 Screening 矩阵、训练 AI 或修改研究总纲。
