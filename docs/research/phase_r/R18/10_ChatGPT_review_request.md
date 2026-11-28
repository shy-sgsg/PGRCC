# PGRCC R18 科研阶段独立审查请求

请首先读取本次报告提交对应版本的 docs/research/RESEARCH_MASTER_PLAN.md，完整理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路与长期技术路线，然后结合这些内容审查本阶段的代码、实验、数据和科研结论。不能只评价阶段任务是否完成，还必须判断本阶段是否推动了整个 GMTI 研究目标，以及当前研究方向是否需要调整。

## 审查快照

- 仓库：https://github.com/shy-sgsg/PGRCC
- 分支：codex/unknown-system-error-next-stage
- 审查 commit：请使用 Codex 最终交付消息提供的完整 40 位报告提交 SHA。
- 实验执行源码 commit：014352ace1c4ad0bade0d759a099a47fe9ecfaa9。
- Release build 源码 commit：4c6d4d303621324f5e174db0ba1811c5691df2e2。
- 研究总纲：docs/research/RESEARCH_MASTER_PLAN.md，blob e637e3769bde7814298ff012d0f42301884e2985。
- 阶段：R18 Native CUDA 传统 Production 方法矩阵与独立参考次级检查。
- 总体目标：在公平、可审计的 airborne GMTI 生产链中判断传统处理方法的任务边界；长期研究目标仍以研究总纲为准。
- 本阶段科学问题：C0/C1/C2/C3 在有效面积杂波与冻结 Production 链中的目标检测、航迹和参数输出如何；该矩阵不能回答非零未知系统误差的因果损失。

## 阅读顺序

1. docs/research/RESEARCH_MASTER_PLAN.md
2. docs/research/RESEARCH_STATUS.md
3. docs/research/RESEARCH_DECISION_LOG.md
4. docs/research/CHATGPT_REVIEW_PROTOCOL.md 与 AGENTS.md
5. docs/research/phase_r/R18/00_master_execution_plan.md 及 01–04 冻结设计
6. docs/research/phase_r/R18/05_execution_and_quality_audit.md、06_signal_and_task_results.md、07_failure_mechanism_analysis.md、08_research_opportunity_ranking.md、09_next_research_direction.md
7. docs/research/phase_r/R18/evidence/formal_matrix/summary/ 与 12 个 case manifest/snapshot
8. docs/research/phase_r/R18/evidence/independent_seed909_secondary_attempt2/ 和 attempt3/
9. 与审查问题相关的 runner、analyzer、配置、Native Release build identity 和历史研究证据

若无法实际读取指定 commit 的研究总纲或阶段 evidence，请标注 NOT_VERIFIED，不得用本请求中的摘要代替读取。

## 本阶段事实摘要

- 12 个 Native CUDA cases，seed 606/707/808 × 6.7/0.5 m/s × 30/20 dB；每 case 21 个 Production branch，共 252 个 branch，全部运行于同一 GMTI_pipe_core SHA。
- 每场景实际连续面积杂波为 390 packets、1,952,655 samples；area mean power 约 0.022747–0.023201。
- 因果 triplet、CFAR valid-CUT denominator 与 final-union-hit 审计通过；实测 Pfa、PK/PKR residual closure、target-only amplitude/phase closure 不可评估。
- 所有已汇总条件下观察到 9/9 truth-period target matches；该计数是描述性结果，不是统计 Pd。没有统计显著性或 PERFORMANCE_VERIFIED 声明。
- seed909 C3 Gamma branch 实际运行通过，但 117 组中 116 组 support count 与 seed606 Mode-A OFF 不同；因运行前冻结的 exact-support gate 未通过而停止性能评价，没有事后放宽门槛。
- R18 固定 0 ns，不能回答未知误差损失、Known 恢复量或盲估计差距。AI 必要性、低速目标优先级和复杂杂波优先级均未建立。

## 请完成双层审查

### 第一层：阶段任务正确性

审查物理模型、面积杂波输入、实验矩阵、源代码/配置/二进制身份、信息泄漏边界、Mode-A/Mode-B 公平性、有效 CUT 分母、Pfa 定义、目标与 TrackManager 指标、证据封存/清理及结果可复现性。特别核对报告是否把真实 CUDA 证据与软件测试/描述性统计分开，是否把 hit fraction 或非目标 Detection 行误称为 Pfa。

### 第二层：总体科研价值

独立判断：R18 是否推动总纲的 GMTI 研究目标；本阶段实际回答了什么、哪些科学问题仍未回答；现有证据是否支持未知误差估计主线、低速目标保护或复杂杂波建模；是否存在更简单的传统方法；AI 必要性是否成立；下一步最值得投入的工作是什么。

阶段运行完成不代表研究方向正确，任务计数改善也不自动构成论文创新。若建议改变总纲，指出具体文本范围、证据、对历史成果的影响和替代方案；用户明确批准前不得修改总纲。

请给出：已核验的文件与 commit、关键通过项、关键失败/不可评估项、是否存在不应接受的结论、总体方向判断，以及有证据支持的下一步建议。不要仅复述 Codex 总结。
