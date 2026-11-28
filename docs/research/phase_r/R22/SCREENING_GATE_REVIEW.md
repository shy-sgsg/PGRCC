# PGRCC R22 screening 阶段独立审查请求

**请首先读取本次 Git 提交对应版本的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路与长期技术路线，然后结合这些内容审查本阶段的代码、实验、数据和科研结论。不能只评价阶段任务是否完成，还必须判断本阶段是否推动了整个 GMTI 研究目标，以及当前研究方向是否需要调整。**

仓库：[shy-sgsg/PGRCC](https://github.com/shy-sgsg/PGRCC)，分支 `codex/unknown-system-error-next-stage`。**研究与实验报告审查快照：`95a8854b01b9af26e6e955d0871e130db81e2f6e`**；主要 CUDA 实验执行源码：`5a60a514cf53cab9f23b17ab29992fd4485876fa`。首个 0 ns screening case 的源码版本不同，见完整提示词。审查入口和证据文件在后续交付提交中；请分别核对这些身份。

请依次读取[指定快照的研究总纲](https://github.com/shy-sgsg/PGRCC/blob/95a8854b01b9af26e6e955d0871e130db81e2f6e/docs/research/RESEARCH_MASTER_PLAN.md)、[研究状态](https://github.com/shy-sgsg/PGRCC/blob/95a8854b01b9af26e6e955d0871e130db81e2f6e/docs/research/RESEARCH_STATUS.md)、[决策日志](https://github.com/shy-sgsg/PGRCC/blob/95a8854b01b9af26e6e955d0871e130db81e2f6e/docs/research/RESEARCH_DECISION_LOG.md)、[审查协议](https://github.com/shy-sgsg/PGRCC/blob/95a8854b01b9af26e6e955d0871e130db81e2f6e/docs/research/CHATGPT_REVIEW_PROTOCOL.md)，再读取[完整阶段审查提示词](CHATGPT_REVIEW_PROMPT_SCREENING_CHECKPOINT_20261028.md)与[资源及执行记录](05_resource_and_execution_plan.md)。若无法读取总纲或证据，应列明缺失并将相关结论标为 `NOT_VERIFIED`。

## 本次结果

V2 screening 计划 288 个 case，完成 17 个 `PASSED` 后，第 18 个（seed1932001、2.5 m/s、30 dB、+4 ns、20 目标、10 周期）因 C4 Mode-B ON 的一个 robust reference 组 support=7，低于冻结门槛 8，触发 `GATE_FAILED`。面积杂波注入与四组因果闭合门通过；失败 case `EVALUABLE=false`，整阶段 `PERFORMANCE_VERIFIED=false`。确认性、dense、bridge 和独立 H0 尚未启动；不能由单 seed 断言总体 Pd/Pfa 或方向收益。

同 seed 和几何的 SNR20 配对组 support=25，SNR30 为 7；目标污染只是待验证解释。生产代码对每个有效单元的互谱相位单位化后求中心，再按相位偏差筛选；更低的相位一致度也可能反映背景波动或其他原因。逐单元复数输入未保存在紧凑包中，详见[补充相位审计](evidence/screening_checkpoint/phase_audit_note.md)。

## 证据入口

- [最终矩阵 manifest](evidence/screening_checkpoint/screening_final_manifest.json)、[初始矩阵 manifest](evidence/screening_checkpoint/screening_initial_manifest.json)、[runner 日志](evidence/screening_checkpoint/matrix_runner.log)。
- [SNR30 失败 gamma CSV](evidence/screening_checkpoint/snr30_c4_modeb_gamma.csv)、[SNR20 配对 gamma CSV](evidence/screening_checkpoint/snr20_c4_modeb_gamma.csv)；对应 [SNR30 运行 manifest](evidence/screening_checkpoint/snr30_run_manifest.json)、[SNR20 运行 manifest](evidence/screening_checkpoint/snr20_run_manifest.json)、[SNR30 Stage2 报告](evidence/screening_checkpoint/snr30_stage2_a0_on.md)和[SNR20 Stage2 报告](evidence/screening_checkpoint/snr20_stage2_a0_on.md)。
- [完整紧凑审查包 ZIP](evidence/screening_checkpoint/complete_review_package.zip)：包含两个 case ZIP、运行日志、配置及诊断；[文本摘录来源和 SHA-256](evidence/screening_checkpoint/provenance.json)。ZIP 不包含完整 raw IQ，不能当作可直接重放的全部原始数据。

如果你的浏览工具不能下载或解压 ZIP，请先读取上述文本证据，并明确标记未读取的 ZIP 内容；不要声称已验证压缩包内的全部数据。

## 请回答

1. **阶段正确性：**核对杂波注入、配对输入、实际运行身份、盲估计信息边界、reference 支持门、失败传播和可评价范围。哪些结论有证据，哪些仍待验证？
2. **科研价值：**本阶段是否推动总纲所述 GMTI 目标？+4 ns 失配、Known 恢复、盲估计、低速目标、目标密度与虚警各能否得出结论？AI 进入条件是否成立？
3. **唯一下一步：**应将 C4 失败保留为方法能力边界，还是提出保持 `min_support=8` 的目标保护或分组修订，并先冻结和验证受影响比较？请提供依据、进入条件、交付物和停止条件，给出 `CONTINUE / STOP / PIVOT / INCONCLUSIVE` 判断。
4. 若认为需要修改研究总纲，请先提出证据和修订建议供用户讨论；未经用户对具体范围明确批准，不得修改总纲。
