# PGRCC R22-S1c 补充证据独立科研审查

> **请首先读取固定审查快照中的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路与长期技术路线，然后结合这些内容审查本阶段的代码、实验、数据和科研结论。不能只评价阶段任务是否完成，还必须判断本阶段是否推动了整个 GMTI 研究目标，以及当前研究方向是否需要调整。**

## 固定审查身份

- 仓库：`https://github.com/shy-sgsg/PGRCC`
- 分支：`codex/unknown-system-error-next-stage`
- 固定审查快照：`a2cd0841a0c126acfbfaa17615dda585906357b6`
- 研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`
- S1c Production 源码 commit：`1ed2a2e67a88a918858784be81794238ef112c7d`
- Production binary SHA-256：`16cb0f2d35596ff98ab8bdcbc9571b972b99e217053cf0b1b7f6b49168c8c998`
- 核心实现：[Production calibration adapter](https://github.com/shy-sgsg/PGRCC/blob/1ed2a2e67a88a918858784be81794238ef112c7d/src/production_calibration_adapter.cpp)
- S1c 执行源码：[runner](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/scripts/phase_r/run_r22_s1c_period5_shared_off_phase.py)、[analyzer](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/scripts/phase_r/analyze_r22_s1c_period5_shared_off_phase.py)
- 阶段：R22-S1c 共同 target-free OFF CSI phase 反事实及目标距离坐标补充审计

请只把上述 commit 作为实验报告、动态状态和证据文件的审查快照。若远端 HEAD 后续变化，请明确区分，不要用更新后的文件替换该快照。

## 必须按此顺序阅读

1. [`docs/research/RESEARCH_MASTER_PLAN.md`](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/RESEARCH_MASTER_PLAN.md)
2. [`docs/research/RESEARCH_STATUS.md`](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/RESEARCH_STATUS.md)
3. [`docs/research/RESEARCH_DECISION_LOG.md`](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/RESEARCH_DECISION_LOG.md)
4. [`docs/research/CHATGPT_REVIEW_PROTOCOL.md`](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/CHATGPT_REVIEW_PROTOCOL.md) 与 [`AGENTS.md`](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/AGENTS.md)
5. [`R22 总执行计划`](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/phase_r/R22/00_master_execution_plan.md)、[`S1b 阶段闭合审计`](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/phase_r/R22/support_failure_diagnostic/S1B_STAGE_BOUNDARY_AUDIT.md)
6. [`S1c 结果与坐标补记`](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/phase_r/R22/support_failure_diagnostic/S1C_SHARED_OFF_PHASE_RESULT.md)
7. [`11 个目标距离单元交叉审计表`](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1c/v2/s1c_target_overlap_audit.csv)、[S1c evidence manifest](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1c/v2/evidence_manifest.json)、[phase-bin diagnostics](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1c/v2/s1c_period5_phase_bin_diagnostics.csv)、[Gamma 对照表](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1c/v2/s1c_period5_group_comparison.csv)、[S1c 分析 JSON](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1c/v2/s1c_period5_analysis.json) 与 [S1b 复数闭合分析](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1b/analysis/stage_boundary_closure.json)。
8. 必要时读取 [`H0 最终报告`](https://github.com/shy-sgsg/PGRCC/blob/a2cd0841a0c126acfbfaa17615dda585906357b6/docs/research/phase_r/R22/18_h0_false_alarm_checkpoint_20261030.md) 以理解无目标虚警侧证据的适用边界。

若无法读取指定快照中的总纲或关键证据，请明确列出文件并把相关判断标记为 `NOT_VERIFIED`；不能用 Codex 的文字概括代替实际阅读。

## 本阶段已执行内容

- S1b 使用同一 seed1932001、SNR30、+4 ns、beam 50、GMTI05 的 ON/OFF/TO 输入重放三个 Native CUDA Production 分支。ON−OFF−TO 的 F1/F2 在 CSI range-phase correction 前相对 L2 残差约 1–2%；CSI correction 后 F2 残差为 78.77%。C4 adapter 按 `F2−γF1` 公式闭合。S1b 未唯一解释后续 support 失败。
- S1c 对同一场景 SNR20/30 的 GMTI05 单周期数据运行五个 Native CUDA Production 分支。各自 ON CSI phase 下 robust support 为25/7；把两个 ON 输入都换成同一 target-free OFF CSI phase 后，support 为21/22，support gap 从18降为1。门槛始终为 `min_support=8`、phase residual ±0.35 rad、133-bin range group。
- 补充真值坐标审计确认 T010 的距离单元为1076/1077，不是1175。其所在 group 为1065–1197；T004、T012、T017、T019也有真值距离单元落入该组。S1b target-only TO 在 `GMTI05 / beam50 / az_index52 / range1076` 有明显 F1/F2 响应。
- SNR20 与 SNR30 baseline 的保留集合交集为1格；SNR20-only 为24格，其中3格位于目标真值距离单元、21格不在目标真值距离单元；SNR30-only 为6格。交叉审计 CSV 中保留了逐目标/bin残差、保留状态、TO响应幅度与 ON/TO/真值/phase 表 SHA-256。
- R22 Screening 仍为 `GATE_FAILED / INCOMPLETE`：原 SNR30 C4 Mode-B ON 参考有一组 support=7<8。H0 的30-seed、60-case矩阵已完成，但只评价无目标经验 CUT-hit fraction，不解除 target-present gate，也不证明 Pd、系统 Pfa 或目标任务收益。

S1c 使用的原始 Stage2 二进制和 S1b 完整 selected-cell CSV 未上传到 GitHub。交叉审计表提供提取值及原始文件 SHA，但这不能替代读取原始二进制/CSV。若审查端无法访问这些本地文件，请把对应原始数据层面的核验标为 `NOT_VERIFIED`。

## 请完成双层审查

### 第一层：阶段任务正确性

1. 检查 S1c 的输入配对、SNR20/30 条件身份、周期映射、运行 XML、Production binary/source 身份和 CUDA 证据是否相互一致。
2. 独立复核 support=25/7 与共同 OFF phase 下21/22 的计算、逐 bin 支持集合统计及 `min_support=8` 门控传播。
3. 检查目标距离坐标：T010 是否为1076/1077；S1b target-only TO 记录是否确实对应 `az_index=52, range=1076`。区分已直接验证的输出与无法访问原始文件的摘录。
4. 判断 S1b 的 ON−OFF−TO 阶段闭合和 S1c 的 phase-vector intervention 能支持哪些因果结论，哪些仍属于推断。
5. 检查实验可比性、评价分母、是否存在目标自消除或其他未评估任务代价。不得把 support 可恢复写成检测/跟踪性能改善。

### 第二层：总体研究价值

1. 本阶段是否推进研究总纲中的传统 GMTI 能力边界识别和真实瓶颈定位？
2. 目标分量是否已被证明会通过 CSI range-phase 估计影响 C4 reference support，还是证据只说明 phase vector 敏感且目标空间重合？请说明证据强度。
3. SNR20/30 支持集合多数差异落在非目标真值距离单元，这如何影响“目标污染导致 support=7”的解释？
4. 现有证据是否足以恢复 R22 Screening，还是应继续停在 gate？如果需要新证据，请提出一个最小、可证伪且不降低冻结质量门的下一步，并说明它能区分哪些假设。
5. 未知系统误差估计是否仍值得研究？是否有理由转向低速目标保护或复杂杂波主线？是否已经证明 AI 有必要？

## 输出格式

- 阶段裁决：`PASS / PASS_WITH_LIMITATIONS / FAIL / INCONCLUSIVE`。
- 分开列出 `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED`。
- 第一层列出已核实事实、问题和 `NOT_VERIFIED` 项。
- 第二层给出 `CONTINUE / STOP / PIVOT` 建议及证据依据。
- 明确指出是否支持恢复 Screening，以及恢复前必须满足的门。
- 若建议修改研究总纲，只提出具体建议与证据；用户明确批准前不得修改总纲。
- 不启动实验、训练、矩阵扩展或清理。审查完成后由用户决定下一步。
