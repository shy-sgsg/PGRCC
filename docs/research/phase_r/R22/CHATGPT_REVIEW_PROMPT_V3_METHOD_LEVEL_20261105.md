# R22-V3 方法级可评价性契约：ChatGPT 独立审查请求

## 固定审查快照

请把以下 Git commit 作为唯一审查快照，不要用较新的分支 HEAD 替代其中的文档：

`3b99486406d9d224f4968df23bbcfeec1315e378`

仓库：<https://github.com/shy-sgsg/PGRCC/tree/3b99486406d9d224f4968df23bbcfeec1315e378>

**请先打开并完整读取该 commit 中的 `docs/research/RESEARCH_MASTER_PLAN.md`，理解 PGRCC 的研究主题、总体目标、核心科学问题、研究内容、总体思路和长期路线，再审查阶段材料。**

研究总纲 Git blob 应为：`e637e3769bde7814298ff012d0f42301884e2985`。若实际读到的 blob 不同，或无法读取该文件，请先说明，不能声称已完成总体科研方向审查。

## 推荐阅读顺序

1. [研究总纲](https://github.com/shy-sgsg/PGRCC/blob/3b99486406d9d224f4968df23bbcfeec1315e378/docs/research/RESEARCH_MASTER_PLAN.md)
2. [动态研究状态](https://github.com/shy-sgsg/PGRCC/blob/3b99486406d9d224f4968df23bbcfeec1315e378/docs/research/RESEARCH_STATUS.md)
3. [研究决策日志](https://github.com/shy-sgsg/PGRCC/blob/3b99486406d9d224f4968df23bbcfeec1315e378/docs/research/RESEARCH_DECISION_LOG.md)
4. [ChatGPT 独立审查协议](https://github.com/shy-sgsg/PGRCC/blob/3b99486406d9d224f4968df23bbcfeec1315e378/docs/research/CHATGPT_REVIEW_PROTOCOL.md)
5. [AGENTS.md](https://github.com/shy-sgsg/PGRCC/blob/3b99486406d9d224f4968df23bbcfeec1315e378/AGENTS.md)
6. [R22 导航与冻结 V2 入口](https://github.com/shy-sgsg/PGRCC/blob/3b99486406d9d224f4968df23bbcfeec1315e378/docs/research/phase_r/R22/README.md)
7. V2 冻结设计 `docs/research/phase_r/R22/config/r22_extension_experiment_design_v2_range_roi.json`，以及 [V2 Screening gate 审查](https://github.com/shy-sgsg/PGRCC/blob/3b99486406d9d224f4968df23bbcfeec1315e378/docs/research/phase_r/R22/SCREENING_GATE_REVIEW.md)
8. [V2 方法级可评价性离线复核](https://github.com/shy-sgsg/PGRCC/blob/3b99486406d9d224f4968df23bbcfeec1315e378/docs/research/phase_r/R22/19_v2_screening_method_level_offline_dry_run_20261105.md)
9. [R22-V3 方法级可评价性契约草案](https://github.com/shy-sgsg/PGRCC/blob/3b99486406d9d224f4968df23bbcfeec1315e378/docs/research/phase_r/R22/20_v3_method_level_evaluability_contract_draft_20261105.md)
10. [磁盘清理台账](https://github.com/shy-sgsg/PGRCC/blob/3b99486406d9d224f4968df23bbcfeec1315e378/docs/research/phase_r/R22/evidence/cleanup_ledger.md)

## 研究与执行身份

- 被审查的 S1e 科研快照：`d2462657803375c3b0ebf45076d8eb4b94d63a8e`
- S1e Production 源码 commit：`461fbd949ea7c024c28f30e7aea36ae9642a4456`
- S1e 报告所记 Production binary SHA-256：`c7db0d12f930b297851f1b3b53f3e56de5cafef0fa90394595de0b8babf7d7c9de`
- V2 离线复核使用的工作分支执行前 HEAD：`05f95db72fe3f4fe07d8f64cad839d95e2d800fa`
- 本次 GitHub 文档审查快照：`3b99486406d9d224f4968df23bbcfeec1315e378`

## 审查任务

请分别完成以下两层审查，并将**报告中陈述的证据**与**你能从固定 Git 快照直接读取或独立验证的证据**区分开。

### 第一层：阶段与契约正确性

1. V2 的 `GATE_FAILED / INCOMPLETE` 是否被完整保留？V3 草案是否避免改写 V2 历史、降低 `min_support=8`、自动回退 Current 或通过 `--resume` 绕过失败？
2. 场景级输入/物理门与方法级 reference、Production、任务评价状态的边界是否合理？遇到归属不明时，`UNKNOWN_STOP` 是否能阻止把全局故障错误降格为单方法问题？
3. `CASE_RECORDED_GATE_PASS_NOT_REPLAYED` 是否准确表达“归档中记录门通过，但缺少原始 IQ/完整真值，未独立重放”的证据强度？
4. `EVALUABLE`、`REFERENCE_NOT_EVALUABLE`、`PRODUCTION_FAILED`、`TASK_NOT_EVALUABLE`、`NOT_APPLICABLE` 和 `NOT_RUN_GLOBAL_GATE` 的定义是否互斥、可实现，是否有遗漏或状态重叠？
5. V2 18 个终态 case 的离线重分类，是否正确区分 270 个适用 ON 方法单元、269 条归档为可评价的历史行和唯一 C4 Mode-B 缺口？是否把三个角色依赖失败误当成三个独立失败？
6. 全方法 CUT/unique-hit 汇总与方法级结果是否应并列保留？经验 Pfa 是否保持独立 H0 口径？
7. 覆盖率分母 `4,320` 是否由冻结 V2 的适用性规则正确推导：144 个 0 ns case × 12 个适用 ON 条件，加 144 个 +4 ns case × 18 个适用 ON 条件？若设计文件不支持该推导，请指出具体差异。
8. 覆盖率、方法自身可评价子集性能、预先冻结的共同可评价配对结果是否能避免困难 case 被事后剔除、算法排名受选择偏差影响？seed 是否被正确视为推断单位？
9. `CASE_VALID` 后单个方法失败时继续后续冻结 case，是否在全局输入、truth、配对、面积杂波、共享 Production 身份等门仍然 fail-closed 的前提下保持科学公平？是否需要额外明确任何停止或重试规则？
10. 草案的 IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED 区分是否准确？

若可从固定 commit 读取对应源码，请检查 V2 runner/evaluator 对 `GATE_FAILED` 的实际语义；若无法读取，明确标为 `NOT_VERIFIED`，不要仅凭报告复述后声称独立核验。

### 第二层：总体研究价值与决策

1. 方法级失败覆盖契约是否服务于研究总纲中的可信物理和任务评价基准、传统方法能力边界识别及 GMTI 瓶颈判定？
2. 这项契约是否只是评价基础设施工作，还是已经支持任何算法性能、AI必要性或新主线结论？
3. V3 是否值得进入 evaluator/runner 实现阶段？若值得，指出必须先修订或冻结的条款；若不值得，给出具体证据和更有价值的替代任务。
4. 是否应保持 `AI_ENTRY_GATE=FAIL`、继续传统 Baseline 能力边界识别，并暂不将 C4 修订、低速目标、复杂杂波或未知状态估计提升为已证实瓶颈？
5. 该契约是否与总纲冲突，或需要修改总纲？若建议改总纲，只能列出具体提案、证据、影响和替代措辞，不能把建议视为批准或直接修改文件。

## 关键限制

- 本次 V3 仍是 `PROPOSED_NOT_FROZEN`：没有实现新的 runner/evaluator，没有启动 CUDA，也没有执行新的 Screening。
- V2 compact case ZIP、V2 失败 case ZIP、展开后的 truth/manifest 数据位于本机 `outputs/`，并未包含在该 GitHub 审查快照中。ZIP CRC/SHA 核验和 269/270 统计是离线复核报告记录的结果；若你无法访问这些本机文件，应将其视为“报告声称且有本地记录”，而非你独立复算确认。
- 原始 Stage2 IQ 未随 GitHub 审查快照提供；完整 target truth CSV 也不能从该快照独立读取。不得声称完成原始 IQ 重放或独立 truth join。
- 单 seed、18 个终态 case 只能用于流程和状态语义审查，不是独立 seed 性能验证，不支持显著性、Pd/Pfa 改善或泛化结论。
- S1e 复数闭合残差不等同目标能量损失；目标污染仍未证明；该阶段没有 GMTI 任务性能结果。
- ChatGPT 的审查结论不是用户对实现 V3、修改总纲或启动 CUDA 的授权。

## 请按此格式给出结论

1. **总体裁决：** `APPROVE_DRAFT / APPROVE_WITH_CHANGES / REJECT_DRAFT / INCONCLUSIVE`
2. **第一层：** 契约及离线复核的正确性；逐项标出 `VERIFIED / REPORT_ONLY / NOT_VERIFIED / ISSUE`。
3. **第二层：** 对 GMTI 总体研究目标的价值，是否建议进入 V3 实现。
4. **必须修改项：** 按严重性排序，引用固定快照中的文件与章节。
5. **科研结论边界：** 明确当前不能支持哪些算法、Pfa/Pd、AI必要性或方向结论。
6. **建议用户决策：** 说明批准实现所涵盖的具体范围；将实现批准与任何新 CUDA 运行授权分开。

若无法读取研究总纲或关键冻结设计，请将相应总体审查标记为 `NOT_VERIFIED`，说明缺失材料，并停止声称已完成整体研究方向判断。
