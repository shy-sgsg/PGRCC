# PGRCC R22-S1d 相位向量与目标距离格审查请求

> **请首先读取固定审查快照中的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路与长期技术路线，再审查本阶段结果。不能只评价局部实验是否完成，还要判断它是否推进总体 GMTI 目标，以及研究方向是否仍有证据支持。**

## 固定审查身份

- 仓库：<https://github.com/shy-sgsg/PGRCC>
- 分支：`codex/unknown-system-error-next-stage`
- 实验与证据审查快照：`c70b35567748c9a359cafef9e63781b10b0a0e4c`
- 研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`
- S1d Production 源码 commit：`461fbd949ea7c024c28f30e7aea36ae9642a4456`
- Production binary SHA-256：`c7db0d12f930b297851f1b3b53f3e56de5caf0fa90394595de0b8babf7d7c9de`
- 阶段：R22-S1d，固定失败组的 CSI phase-vector crossover 与目标真值距离格事后交叉审计

请只使用上面的 commit 作为实验报告、动态状态和证据文件的固定快照。远端 HEAD 若已变化，请分别说明，不要用新文件替换该快照。

## 必须按此顺序读取

1. [研究总纲](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/RESEARCH_MASTER_PLAN.md)
2. [动态研究状态](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/RESEARCH_STATUS.md)、[研究决策日志](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/RESEARCH_DECISION_LOG.md)、[ChatGPT 审查协议](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/CHATGPT_REVIEW_PROTOCOL.md) 和 [AGENTS.md](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/AGENTS.md)
3. [Phase R 导航](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/phase_r/README.md) 与 [R22 导航](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/phase_r/R22/README.md)
4. [R22 Screening gate 审查](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/phase_r/R22/SCREENING_GATE_REVIEW.md) 和 [R22 S1d 结果报告](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/phase_r/R22/support_failure_diagnostic/S1D_PHASE_VECTOR_CROSSOVER_RESULT.md)
5. [S1d manifest](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1d/s1d_manifest.json)、[support 分析](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1d/s1d_analysis.json)、[support 汇总表](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1d/s1d_support_summary.csv) 和 [逐文件证据清单](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1d/evidence_manifest.json)
6. 本次补充的[逐距离格相位与支持集合表](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1d/s1d_phase_vector_target_overlap.csv)、[汇总 JSON](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1d/s1d_phase_vector_target_overlap_summary.json) 和[分析脚本](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1d/analyze_r22_s1d_vector_target_overlap.py)
7. 必要时检查 [Production adapter 源码](https://github.com/shy-sgsg/PGRCC/blob/461fbd949ea7c024c28f30e7aea36ae9642a4456/src/production_calibration_adapter.cpp)、[S1d runner](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/scripts/phase_r/run_r22_s1d_phase_vector_crossover.py) 和 [S1c 目标坐标审计](https://github.com/shy-sgsg/PGRCC/blob/c70b35567748c9a359cafef9e63781b10b0a0e4c/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1c/v2/s1c_target_overlap_audit.csv)

若无法实际读取指定快照中的总纲或关键证据，请列出缺失文件并把对应判断标为 `NOT_VERIFIED`。不能用本提示词中的摘要代替实际读取。

## 实验范围与已知结果

S1d 固定 seed1932001、2.5 m/s、+4 ns，使用 GMTI05（period index 4）、beam 50、azimuth row 52、range bins 1065–1197 的已保存输入，完成 7 个本机 Native CUDA Production 分支。Stage2 没有重跑。冻结设置始终是 133-bin group、`min_support=8`、相对组相位中心残差门限 ±0.35 rad、`fallback_to_current=false`。

SNR30 ON 原生向量下 support 为7；对同一 SNR30 ON 输入交叉应用 SNR20 ON CSI phase vector 后为11，应用 paired target-free OFF phase vector 后为22。各分支从保存的 selected-cell F1/F2 独立重算 support，均与 Production Gamma 记录一致。7个 Production 进程返回0，但这不代表每个 reference 有效；SNR30 原生 reference 仍是 `NOT_EVALUABLE`。

目标真值只在估计完成后用于 evaluator-only 距离坐标交叉。选定组内有11个目标真值距离格。SNR20 ON 与 SNR30 ON phase vector 的逐格 wrapped 差，目标格均值为0.125783 rad，其余格为0.142887 rad；目标格没有更大的平均变化。SNR30 ON 使用 SNR20 ON 向量时保留的11格都不在目标真值距离坐标；使用 OFF 向量时保留22格中有4格与目标真值距离坐标重合。对 target-free OFF 分支，真值距离坐标重合仅是空间位置标签，不表示该 OFF 数据中有目标信号。

逐格 `|phase_a-phase_b|>0.35 rad` 的计数是向量间差值统计；C4 冻结门实际检查的是每个互谱相位相对该组相位中心的残差。两者不可混为一谈。CSI sidecar 与 Production trace 在133格逐格核验，7分支最大绝对误差为 `4.903e-9 rad`。

S1d 证明的是：在这个固定输入和失败组上，CSI phase-vector 来源会因果改变 C4 robust-phase support 及其成员集合。它没有识别 SNR20/30 ON 自估向量差异的物理来源，不能单凭 support 从7跨到11/22 认定目标污染是根因、C4 被修好或 GMTI 任务性能得到恢复。

## 证据限制与阶段状态

GitHub 快照包含 selected-group 复数 F1/F2、7分支运行 XML、Production 日志与记录、CSI sidecar、逐格 phase trace、Gamma/support 结果和身份清单。它**不包含原始 Stage2 `.bin` IQ**，因此可以审查选定组的 support 重算和保存的 Production 证据，但不能从该快照完整重放 Stage2→Production，也不能独立重算原始输入二进制。

R22 target-present Screening 仍为 `GATE_FAILED / INCOMPLETE`：原 SNR30 C4 Mode-B ON reference 的 support=7，小于冻结最低值8。S1d 不解除 gate，也没有提供 Detection、Pd/Pfa、目标保持、SCNR、速度或方位估计误差等任务级结果。确认性矩阵、密集目标矩阵、bridge 和 AI 训练均未开始。

## 请完成双层独立审查

### 第一层：阶段任务与证据正确性

1. 检查固定快照的研究总纲、R22 gate 状态、S1d 配置、运行身份、输入配对和7分支 Production 证据是否一致。
2. 核验 support 计数、筛选成员集合、phase sidecar 与 Production trace 的对应；评估分析脚本和逐格表是否能独立重现报告中的数值。
3. 判断 S1d 能支持的因果结论边界：更换 phase vector 对 support 有何影响；哪些物理根因仍未区分？
4. 评估目标真值距离格事后交叉的证据强度。SNR20/30 ON 向量差在目标格上并未更大、SNR30 原生7格均不在目标坐标、两个替代向量的成员集合不同，这些事实如何影响目标污染假设？
5. 检查是否错误混淆逐格向量差与 C4 相位中心残差门，是否把 support 跨门写成 reference/任务性能恢复。
6. 明确列出哪些项目可在 GitHub 快照中直接核验，哪些因缺少 raw Stage2 IQ、完整本地运行归档或独立设备复跑而为 `NOT_VERIFIED`。
7. 分别裁定 `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED`。

### 第二层：总体 GMTI 科研价值与方向

1. S1d 是否推进研究总纲中的传统 GMTI 能力边界和真实瓶颈识别？具体贡献是什么？
2. 现有证据是否足以认定 C4 support 失败由目标污染导致，或只能确认 phase-vector 敏感性？
3. R22 Screening 是否应继续停在 `GATE_FAILED`？若需补证据，请给出一个最小、可证伪且不降低冻结质量门的下一步，并说明区分哪些根因假设及其停止条件。
4. 对继续研究未知物理状态估计、低速目标保护、复杂/非平稳杂波建模，分别给出 `SUPPORTED / NOT_SUPPORTED / NOT_VERIFIED` 和依据。
5. 当前是否有证据证明 AI 必要？是否存在应该先比较的确定性传统方案？
6. 阶段结论是否足以建议修改研究总纲？如是，只提出有证据支持的具体修改建议，等待用户明确批准；不得直接改总纲。

## 输出格式

- 简短阶段裁决：`PASS / PASS_WITH_LIMITATIONS / FAIL / INCONCLUSIVE`。
- 分列 `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED`。
- 第一层：可核事实、审查问题、`NOT_VERIFIED` 项。
- 第二层：总体研究价值、`CONTINUE / STOP / PIVOT` 建议和证据。
- 明确是否允许恢复 Screening；若不允许，列出解除 gate 的最小前置条件。
- 只提出一个下一步任务和清晰停止条件；不得启动实验、训练或矩阵扩展。
- 若建议修改研究总纲，必须先与用户讨论；未经用户明确批准不得修改。
