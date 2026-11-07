# PGRCC R22-S1d 固定快照独立科研审查

> **请首先读取本次 Git 审查提交 `781bf3d96bb327a423eab944ce09a13a65c85ad9` 对应版本的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路与长期技术路线，然后结合这些内容审查本阶段的代码、实验、数据和科研结论。不能只评价阶段任务是否完成，还必须判断本阶段是否推动了整个 GMTI 研究目标，以及当前研究方向是否需要调整。**

## 审查身份

- 仓库：`https://github.com/shy-sgsg/PGRCC`
- 分支：`codex/unknown-system-error-next-stage`
- 固定审查提交：`781bf3d96bb327a423eab944ce09a13a65c85ad9`
- 实验执行时源码 commit：`461fbd949ea7c024c28f30e7aea36ae9642a4456`
- 研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`
- 本任务报告：[`S1D_PHASE_VECTOR_CROSSOVER_RESULT.md`](https://github.com/shy-sgsg/PGRCC/blob/781bf3d96bb327a423eab944ce09a13a65c85ad9/docs/research/phase_r/R22/support_failure_diagnostic/S1D_PHASE_VECTOR_CROSSOVER_RESULT.md)
- 精简 CUDA 证据：[`evidence/s1d/`](https://github.com/shy-sgsg/PGRCC/tree/781bf3d96bb327a423eab944ce09a13a65c85ad9/docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1d)
- S1c 前序结果：[`S1C_SHARED_OFF_PHASE_RESULT.md`](https://github.com/shy-sgsg/PGRCC/blob/781bf3d96bb327a423eab944ce09a13a65c85ad9/docs/research/phase_r/R22/support_failure_diagnostic/S1C_SHARED_OFF_PHASE_RESULT.md)
- 动态状态：[`RESEARCH_STATUS.md`](https://github.com/shy-sgsg/PGRCC/blob/781bf3d96bb327a423eab944ce09a13a65c85ad9/docs/research/RESEARCH_STATUS.md)
- 决策记录：[`RESEARCH_DECISION_LOG.md`](https://github.com/shy-sgsg/PGRCC/blob/781bf3d96bb327a423eab944ce09a13a65c85ad9/docs/research/RESEARCH_DECISION_LOG.md)

如仓库 HEAD 与固定审查提交不同，请只以固定提交作为本次证据快照，并单独说明 HEAD 差异。若无法实际读取总纲或固定文件，应标记 `NOT_VERIFIED`，不要以本提示词的摘要代替读取。

## 推荐阅读顺序

1. 固定提交中的 `docs/research/RESEARCH_MASTER_PLAN.md`。
2. `docs/research/RESEARCH_STATUS.md`、`docs/research/RESEARCH_DECISION_LOG.md`、`docs/research/CHATGPT_REVIEW_PROTOCOL.md`、`AGENTS.md` 和 `docs/research/phase_r/README.md`。
3. `docs/research/phase_r/R22/README.md` 与 `docs/research/phase_r/R22/support_failure_diagnostic/README.md`。
4. S1d 结果报告、S1c 前序报告、S1d runner、manifest、evidence manifest、逐分支 XML/log、运行时配置、Gamma、selected-cell F1/F2 与 support membership 表。
5. 必要时检查 `scripts/phase_r/r22_experiment_matrix.py` 中的 `GATE_FAILED` resume gate 和 S1d 依赖的 S1c 代码。

## 两层审查

### 第一层：阶段任务和实验正确性

独立核对：

- 实际运行的是否为本机 Native CUDA Production；7个分支的 binary/source/runtime XML 身份是否闭合，Production return code=0 与 reference `NOT_EVALUABLE` 是否被正确区分。
- 固定输入是否正确绑定 seed1932001、2.5 m/s、+4 ns、SNR20/SNR30 与 paired OFF；Stage2 零基 `period_0004` 是否正确映射至 `GMTI05`，前期错用完整十期 sidecar 的尝试是否已明确排除。
- 2×3 对照是否只改变 CSI range-phase vector 来源；ON 输入的 raw phase 是否由自身输入估计；目标真值是否未进入估计；OFF 是否被正确标为 privileged diagnostic reference。
- `range_band_bins=133`、`min_support=8`、相位残差门限 ±0.35 rad、无 Current fallback 是否保持冻结。
- Production selected-cell F1/F2 的 `F2·conj(F1)` 相位筛选重算是否与七个 Gamma support 相符；支持集合变化是否支持报告所述结论。
- 证据包的缺项、运行记录与复现边界。GitHub 精简包包含逐组 F1/F2 cells 和各分支日志/XML/manifest，但**不包含原始 Stage2 `.bin` 输入或完整 IQ**；因此可以审计记录并重算所选组 support，不能从 GitHub 快照全链路重放或独立校验输入 IQ 内容。请明确区分 `selected-group recomputation` 与 `end-to-end replay`。
- Matrix runner 是否因 `GATE_FAILED` 而拒绝 `--resume`；是否正确保留 R22 原失败门，没有续跑、跳过失败条件或降低门槛。

### 第二层：总体研究价值与方向

结合研究总纲，判断：

1. 这次诊断对识别传统 GMTI 能力边界的实际贡献是什么？
2. 它证明的是 CSI phase-vector 来源对一个固定组 support 门的敏感性，还是能进一步说明未知系统误差、目标污染或 GMTI 任务性能？哪些因果断言超出证据？
3. privileged OFF 向量把 support 从7推到22，是否只能作为诊断上限而不能当作可部署收益？交叉 SNR ON 向量 support=11 是否存在公平性或输入信息条件问题？
4. S1c 与 S1d 合并后，目标污染是否已被唯一归因？若没有，什么最小证据缺口仍然影响科学裁决？
5. 现有证据是否支持开发 C4 新版本、恢复大规模 Screening、改变研究主线，或启动 AI？请说明支持/不支持的依据。
6. 在 Screening 仍被 fail-closed gate 阻断时，最合理的 `CONTINUE / STOP / PIVOT` 建议是什么？如果建议继续，只提出一个有明确目标、比较条件、评价指标和停止条件的候选；不要启动实验或修改冻结门槛。
7. 该阶段的 `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED` 状态是否恰当？

## 请按此格式给出审查结论

1. **总裁决：** `PASS / PASS_WITH_LIMITATIONS / FAIL / INCONCLUSIVE`，并说明判断针对工程执行还是科研结论。
2. **阶段任务正确性：** 已核验项、问题及 `NOT_VERIFIED` 项。
3. **因果结论：** 哪些由设计支持，哪些仍不能归因。
4. **总体科研价值：** 对 GMTI 总目标的贡献、方法必要性和替代方向。
5. **方向裁决：** `CONTINUE / STOP / PIVOT` 及其证据边界；分别说明是否建议 C4 新版本、恢复 Screening、研究未知物理状态、研究低速目标/复杂杂波或 AI。
6. **最小后续建议：** 如需继续，只列一个下一步及通过/停止条件；不把建议当作用户授权。
7. **证据完整性和清理建议：** 哪些已提交的高价值证据必须长期保留，哪些本机输出可在本次独立审查结束后删除以满足最终项目占用小于20 GB。

阶段结果或单组 support 通过，不自动代表 GMTI 性能改善或总体路线正确。任何研究总纲修改只能提出建议并等待用户明确批准。
