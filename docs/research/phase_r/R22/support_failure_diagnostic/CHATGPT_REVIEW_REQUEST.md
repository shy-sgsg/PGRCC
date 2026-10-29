# PGRCC R22-S1 独立科研审查请求

**请首先读取本次 Git 提交对应版本的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路与长期技术路线，然后结合这些内容审查本阶段的代码、实验、数据和科研结论。不能只评价阶段任务是否完成，还必须判断本阶段是否推动了整个 GMTI 研究目标，以及当前研究方向是否需要调整。**

- 仓库：`https://github.com/shy-sgsg/PGRCC`
- 分支：`codex/unknown-system-error-next-stage`
- 本次离线阶段审查快照：`6ab7310794893d1f71747860b73cfc0643a0b253`
- 原 CUDA 主要执行源码：`5a60a514cf53cab9f23b17ab29992fd4485876fa`
- 研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`
- 总体研究目标：公平刻画传统 GMTI 方法的能力边界，并识别可证伪的任务瓶颈。
- 本阶段科学问题：C4 Mode-B 局部 reference support=7 的直接门控原因是什么，现有证据能否区别目标污染与其他物理解释？

请读取上述快照的 `docs/research/RESEARCH_MASTER_PLAN.md`、`RESEARCH_STATUS.md`、`RESEARCH_DECISION_LOG.md` 与 `CHATGPT_REVIEW_PROTOCOL.md`，再读取 [R22-S1 审计报告](README.md)、[组级对照](group_comparison.csv)、[输入和结果摘要](group_comparison_summary.json)、[可复现脚本](compare_groups.py)、[原 screening 审查入口](../SCREENING_GATE_REVIEW.md) 和相关源码。若不能实际读取总纲或某项证据，应列出缺失项并标记相关判断 `NOT_VERIFIED`。

本次从已有 gamma 摘要离线对齐 1,560 组：失败组 ON20、ON30、OFF30 support 为 25、7、55；ON20→ON30 的 1,560 组中，791 下降、642 上升、127 不变。仅一个筛选 seed。原失败目录缺少筛选前逐单元复数输入；保留的距离相位 `.f32` 是校正向量。原 Stage2 和 Production 可执行文件、两份场景模板身份已核对。未重跑 CUDA、训练 AI、改变门槛或修改总纲。

请完成两层审查：

1. **任务正确性：** 检查 ON20/ON30/OFF30 的分组键与 Mode-A/OFF 和 Mode-B/ON 信息条件；验证对照表、直接门控、原始输入可用性和运行身份。已保存摘要能否支持目标污染的因果结论？哪些潜在处理链差异与反事实仍未闭合？
2. **总体科研价值：** 判断这一离线审计是否推进 GMTI 总目标，是否足以选择长期方向、确认可重复任务收益或通过 AI 进入门；检查是否存在更简单的确定性处理方式。

重点审查报告中提出的**待用户批准的最小补采方案**：是否需要两个原场景 10 周期生成、固定 period 5 / beam 50 / az 52 / range 1065–1197 的 ON20、ON30、OFF30、TO 复数旁路 tap？如何避免真值泄漏、识别全部目标 TO 与单个 T010 的区别、核验 Stage2/处理链身份及非线性步骤，并确认诊断构建不会改变原输出？原 raw hash 或原支持数不能重现时应如何停止或收窄结论？是否存在更小但充分的方案？

请给出 `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED` 状态、`CONTINUE / STOP / PIVOT / INCONCLUSIVE` 方向建议、一项下一步任务、进入和停止条件。不要把组级 support 恢复视为任务性能改善。需要修改研究总纲时，只提出证据与建议，未经用户针对具体范围明确批准不得修改。
