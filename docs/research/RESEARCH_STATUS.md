# PGRCC 动态研究状态

本文件记录会随研究阶段变化的状态，不重新定义长期研究目标。研究主题、总体目标、科学问题、研究内容和长期技术路线以[研究总纲](RESEARCH_MASTER_PLAN.md)为准；本文件不能覆盖或暗中替代总纲。

## 状态身份

- 状态核对基线：`fb8d4f54ec726395c8f8f46cd844eb1c124d9fb8`（工作分支 `codex/unknown-system-error-next-stage`）。
- 研究总纲版本：Git blob `8b25b73403d768138738b3d950493d0b45572406`；本次仅更新第 9 节当前状态与建议。
- 本状态整理日期：2026-10-25。
- 最新研究阶段证据：R16；详见[研究决策日志](RESEARCH_DECISION_LOG.md)与[Phase R 导航](phase_r/README.md)。

## 当前研究阶段

R16 是最近完成的科研阶段。它使用 seed505、6.7 m/s、30 dB、零时延，在有效连续面积杂波下执行 A0/C0/C1 × OFF/ON/TO 共 9 个本机 Native CUDA Production branches。此零时延基线实验不回答未知通道误差是否降低 GMTI 性能。

- **阶段进入条件：** R15 将固定 C1 `Gamma=1` ordinary subtraction 列为可能的 reference-free 传统 baseline，用户明确启动有界的 R16 Production pilot。
- **阶段停止条件：** 9/9 分支执行成功，面积杂波、F1/F2 输入、CSI、CFAR final-union maps、Detection 和 TrackManager 记录完成专项审计；C1 ON/TO 各 1/3 正确检测而 C0 各 3/3，固定 C1 在本场景不足以作为可接受整体替代。跨 R14/R16 的 Production binary 不同，故不据历史 C2/C3 作严格比较；阶段到此停止。

R16 的定向 runner/evaluator 改动已实现；Native CUDA 已执行；冻结场景的 C0/C1 指标可评价；单 seed `PERFORMANCE_VERIFIED=false`。经验 Pfa 与 radial velocity 仍 `NOT_EVALUABLE`。这不是未知误差因果结果，也没有启动 AI。

## 最近完成的任务与关键结论

- R16 在本机 NVIDIA GeForce RTX 3050 Laptop、`USE_COREX=OFF` Native CUDA 路径运行。连续面积杂波为 390 packets / 1,952,655 samples，测量平均功率 0.0234967；27 张 final-union CFAR map/geometry audit 全部通过。
- 同运行 C0/C1 共用 Stage2 inputs 与 F1/F2 tap。C1 ON/TO 各正确检测 1/3（C0 为 3/3）；C1 H0 active-support cancellation −2.453 dB（C0 +5.582 dB），TO target-only retention −18.223 dB（C0 −0.671 dB）。观察 hit fraction 不是 Pfa，不能因 C1 检测行更少宣称虚警改善。
- R14/R16 九期输入与 C0 CSI hashes 一致，但 Production binary 不同、C0 final-union maps 九期均不同；因此 R14 C2/C3 不作为本次严格因果对照。
- R15 复用了 R14 seed505 Native CUDA Production 检测记录，并对固定检测执行 CPU TrackManager 关联反事实；该阶段没有新跑 CUDA。
- Greedy replay 逐条复现生产关联边：C2 为 `990/990`，C3 为 `701/701`。Hungarian 关联改变大量边且结果方向不一致，因此不是本例的通用修复证据。
- C2/C3 的 Mode-A OFF 与 H0 背景逐期相同，属于 reference-in-sample；现实可部署的 paired OFF 来源仍未验证。C1 ordinary subtraction 已实现，但未执行。
- 该单 seed、零时延反事实不能支持跨场景性能结论、未知通道误差效应或统计显著性主张。经验 Pfa、径向速度等外围评价仍有未闭合项。
- AI 必要性未建立；本状态不提出新的 AI 方向。

## 已停止或未成立的研究假设

- 停止以 seed505 的单次 track switch 作为 Robust DDC 独立算法创新证据。
- 固定 Gamma=1 C1 在 R16 场景中目标漏检及杂波残余代价较大；停止将该固定参数方法视为该场景的可接受整体替代，但不外推至所有 blind deterministic estimators。
- R14/R15 的零时延证据不支持未知系统误差方向的因果结论，也不能据此否定该方向。
- 当前证据没有建立 AI 的必要性，也没有证明低速目标或复杂杂波已经成为优先研究瓶颈。

## 尚未解决的主要问题

- paired OFF reference 的现实获取和部署可得性；
- H0 false-hit / 经验 Pfa 的可评估统计口径；
- 径向速度与其他参数评价的闭合范围；
- C2/C3 候选分布、目标保持、关联和速度代价在更多独立场景下是否稳定；
- 未知系统失配对任务性能的因果影响，尚需由与该问题对应的独立证据回答。
- R14 与 R16 Production binary 差异造成的跨方法可比性限制；经验 Pfa、radial velocity 和更多独立场景的任务指标仍未闭合。

## 当前唯一优先任务与阶段门

- **当前唯一优先任务：暂无已授权的科研执行任务。** R16 已完成，本文件中的下一步建议不自动触发代码修改、Production 实验或 CUDA 运行。
- **最近一次阶段建议（非活动授权）：** 若用户启动新方法比较，先统一 Production executable/source identity 与评价门，再决定是否需要同 binary 的有限传统方法比较；不可用 R14 的不同 binary 输出给 R16 C1 排名。
- **停止条件：** 处理链 identity 或真实任务分母未闭合时停止方法排名；经验 hit fraction 不得表述为 Pfa。单 seed 不得升级为统计性能结论。

## 总纲修订讨论

本次状态更新没有提出研究总纲修改建议。若未来证据提示总纲需要调整，应先在单独提案或本状态中写明问题、证据、拟改范围及对既有研究目标和成果的影响，并等待用户对具体修改内容或清晰范围明确批准。批准前不得修改总纲正文。
