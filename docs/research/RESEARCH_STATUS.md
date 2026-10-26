# PGRCC 动态研究状态

本文件记录会随研究阶段变化的状态，不重新定义长期研究目标。研究主题、总体目标、科学问题、研究内容和长期技术路线以[研究总纲](RESEARCH_MASTER_PLAN.md)为准；本文件不能覆盖、重新定义或暗中替代总纲中的长期目标或路线。

## 状态身份

- R18 执行源码版本：`014352ace1c4ad0bade0d759a099a47fe9ecfaa9`（工作分支 `codex/unknown-system-error-next-stage`）；完整阶段报告随当前交付提交。
- 研究总纲版本：Git blob `e637e3769bde7814298ff012d0f42301884e2985`；R17 仅按用户明确范围恢复总纲第 9 节的三段文字至 R16 前版本，其他正文不变。
- 本状态整理日期：2026-10-26。
- 最近完成阶段：R20 已完成 R19 评价器修正、Doppler truth 映射正控、R18 回归和 R8/R12 保存结果统一复评；完整报告见 [R20 总实施计划](phase_r/R20/00_master_execution_plan.md) 与 [R20 ChatGPT 审查请求](phase_r/R20/11_ChatGPT_review_request.md)。研究总纲 blob 保持 `e637e3769bde7814298ff012d0f42301884e2985`。
- 当前在研阶段：无。R20 仅离线复评，没有运行 CUDA 或产生新 IQ；下一步仅建议在用户/独立审查决定后，考虑两个独立 seed 的窄 M1 复核。当前不启动实验或 AI 训练。

## 当前研究阶段：R20 完成；未知 +4 ns 任务损失获有限支持

R20 使用冻结的 `r20-task-eval-v2` 离线评价器对 R18、R8、R12 保存证据复评。本阶段执行 9 个 R20 evaluator 测试、R19 原 evaluator 回归测试、Python 语法检查和 Doppler positive control；没有 CUDA/Production 重放、原始 IQ 重算、算法修改或参数调整。

- **Doppler gate：** R18 seed606 / 6.7 m/s / 30 dB target-only TO 3/3 落在原有 ±2 行门内；三期 truth 分量与 Production 轴/PRF 均闭合。A0 ON 的三期轴/真值映射均有效，其中 2/3 有命中。R8 seed101 缺 Production axis，三条件任务结果标记 `DOPPLER_TRUTH_MAPPING_NOT_VERIFIED`。
- **R19 回归：** R18 12 场景、84 条 ON condition 中 strict hits、valid truth opportunities、misses 和 hit fraction 与 R19 一致；14/84 的 post-confirmation track match rate 从 1.0 更正为 0.5。新增独立的 confirmed unique-period 统计，避免把 state observations 误叫 periods。
- **非零误差证据：** R8 seed202、303 的 Current +4 ns 都为 0/3；A0/Known 分别为 2/3、3/3。R12 seed404 的 B 为 0/3、A0/Known 为 2/3、固定 D3 与 M1 为 3/3。R8 seed101 不加入 R20 detection 主结论。所有数字是逐场景描述证据，不是统计显著性或总体 Pd。
- **Known / 确定性估计边界：** 可评价的 R8 seeds 中 Known 回到 A0 命中数；R12 的 M1 与固定 D3 比 Known 多 1 个机会，但共同成功机会的角度/位置误差仍高于 Known，且 M1 F2 closure `2.6840%`、Known `0.00873%` 只能引用历史 manifest，raw IQ 不可重算。R12 只有一个 seed。
- **方向：** 有证据继续有限的未知物理误差表征；不恢复静态时延估计为论文主要创新，不启动 AI。低速与复杂杂波不是本阶段因果变量，H0 counter 缺失使统计 Pfa 不可评价。唯一建议是用户审查后再考虑两个独立 seed 的窄 M1 验证；R20 本阶段已停止。
- **证据身份与可复现限制：** R20 machine manifest、逐机会 CSV、R19 v1/v2 差异和测试入口在 `phase_r/R20/evidence/`；R8/R12 raw IQ 文件不可用，已保存的输入 SHA 仅是历史声明。没有把历史 closure 伪装成新计算。
- **磁盘与保留：** 本机工作树约 2.0 GB，小于 20 GB；R20 未删除数据，新增紧凑审计和高价值结果。
- **状态：** `IMPLEMENTED`=R20 offline evaluator/adapter；`EXECUTED`=R18/R8/R12 saved-data re-evaluation 与正控制门；`EVALUABLE`=符合 axis/identity/field 要求的上述逐项指标；`PERFORMANCE_VERIFIED=false`=没有新 CUDA/独立重复，不能宣称统计性性能改善。

## R19 历史阶段记录：未知误差问题当时仍未闭合

R19 仅对 R18 已保存数据离线重评，没有运行 CUDA、生成新 IQ、训练 AI 或改 Production 算法。完整报告从 [R19 总实施计划](phase_r/R19/00_master_execution_plan.md)开始，冻结契约、结果、机制边界、方向评估和 ChatGPT 审查请求列于 [R19 导航](phase_r/README.md)。研究主题和长期目标仍由研究总纲定义。

- **输入实验身份：** R18 runner commit `014352ace1c4ad0bade0d759a099a47fe9ecfaa9`，Release build 源码 commit `4c6d4d303621324f5e174db0ba1811c5691df2e2`，Production binary SHA `3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0`；R18 的配置和输入身份见 R19 evaluation manifest。R19 evaluator 源码 SHA 另见 [R19 执行审计](phase_r/R19/09_execution_and_quality_audit.md)。
- **复评范围：** 12 个 seed/speed/SNR 场景，delay error 全部为 0 ns；按冻结的一对一任务门重算最终 Detection、TrackManager 和字段可用的参数指标。高速度 Mode-A/B 在命中、Gamma 与下游候选数量上出现描述性差异；Mode-A 使用 paired OFF，是 privileged reference。旧 range/position 门的候选数量远高于 strict gate：seed606、6.7 m/s、30 dB 下 C0 为 24→2 条、C3 Mode-B 为 23→1 条候选边；R18 旧命中与 R19 strict 命中之差不能当作新物理性能效应。
- **可评估性：** 3,336 项指标状态中，2,184 `EVALUABLE`、576 `EVALUABLE_DESCRIPTIVE_MIXED_CLUTTER_NOISE`、84 `EVALUABLE_DESCRIPTIVE_ONLY`、276 `NOT_EVALUABLE`、132 `REPLAY_REQUIRED`、84 `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`。统计 Pfa、目标专属复 CSI/SCNR、CFAR 空间归因、radial velocity 与 ID-switch 根因未闭合。
- **方向裁决：** R19 不能回答非零未知误差损失、Known correction 恢复率或盲估计差距；也未证明 AI、低速保护或复杂杂波优先。唯一下一步建议为 `CONTINUE` 一项边界明确的 R8/R12 离线统一复评；R8 的原始 Detection/truth 文件目前可由本机旧 manifest 路径读取，但未纳入 Git evidence 包；记录不足时再提出最小 replay 设计。该建议未获自动执行授权。
- **磁盘与保留：** 工作树当前约 2.0 GB，低于 20 GB 目标；本阶段未删除数据。高价值 R19 结果、manifest、测试记录、审查图及报告位于 `phase_r/R19/`。
- **证据状态：** `IMPLEMENTED`=R19 offline evaluator/contract；`EXECUTED`=12-case saved-data reevaluation；`EVALUABLE`=契约定义且字段完整的指标；`PERFORMANCE_VERIFIED=false`=本阶段无新 CUDA，也无未知误差因果验证。

## 最近完成阶段：R17

R17 审计 R14 与 R16 的 C0 Production CFAR 大幅分歧。审计没有运行 CUDA，没有改变算法、处理参数或历史实验结果。R14/R16 使用同一组 9 个 Stage2 role-period 输入；留存比较记录显示其 F1/F2 tap 与 CSI 输出实虚部 hash 相同、运行 XML 的算法参数相同，但 Production binary 和 CFAR maps 不同。

- **阶段进入条件：** R16 报告发现 R14/R16 九组 C0 CFAR map 均不同，且两次 Production executable SHA 不同；R17 获用户明确授权进行离线身份与数值机制审计。
- **阶段结论：** R14 `GMTI_pipe_core` SHA `f65a8aa4…` 与 R7 的 pre-fix GO-CFAR executable SHA 完全相同。R7 已证明该旧实现的 float32 前缀/积分累加会因消减误差产生负/零阈值和额外 hit。R14 虽声明源码提交晚于 R7 修复，实际运行二进制却是修复前产物；R16 记录了修复路径的方向重算诊断。R14 与 R16 的跨版本 CFAR / task-performance 数值不能直接比较。
- **阶段停止条件：** 已用 R7 pre-fix manifest 的相同 SHA 定位 R14 实际 executable 身份，并用 R7 根因报告和 R16 数值诊断交叉核对；R14 原始功率/阈值图已清理，无法逐 CUT 复算其错误门限数。离线审计到此停止，不补跑 CUDA。

R17 的状态为：`IMPLEMENTED` 不适用（未改算法/软件）；`EXECUTED` 已完成仓库、manifest、XML、源码与历史证据审计；`EVALUABLE` executable 身份差异及其与 GO-CFAR 已知数值缺陷的关联可评估，R14 每个 CUT 的具体门限错误数不可评估；`PERFORMANCE_VERIFIED=false`。本次没有新的 GMTI 性能运行。

## 最近完成的任务与关键结论

- R16 在本机 NVIDIA GeForce RTX 3050 Laptop、`USE_COREX=OFF` Native CUDA 路径运行。连续面积杂波为 390 packets / 1,952,655 samples，测量平均功率 0.0234967；27 张 final-union CFAR map/geometry audit 全部通过。
- 同运行 C0/C1 共用 Stage2 inputs 与 F1/F2 tap。C1 ON/TO 各正确检测 1/3（C0 为 3/3）；C1 H0 active-support cancellation −2.453 dB（C0 +5.582 dB），TO target-only retention −18.223 dB（C0 −0.671 dB）。这些单 seed 数字不是统计显著性或 Pfa 结论。
- R14 的三期 C0 full-band CFAR hits 为 14,243 / 17,628 / 16,253，dynamic-band 为 18,982 / 16,158 / 19,297；R16 对应为 131 / 150 / 147 与 137 / 129 / 131。每图 valid CUT 数均为 527,280，GO、guard=4、background=16、alpha=13.44782257、几何一致。
- R14 的 Production binary SHA `f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962`，等于 [R7 pre-fix manifest](phase_r/evidence/r7_cfar_correctness_20261022/manifests/pre_fix_experiment_manifest.json) 中的 binary SHA。R7 数值根因报告记录旧版在独立配对图上产生负/零门限及错误额外 hit；该实现后来由 `04958a996f6a1449fa3d9f158a4765493c85038d` 修复，而该提交是 R14/R16 声明源码提交的祖先。
- R16 Production binary SHA 为 `ae41c79ee48b530fc71e07b4ade7dcf7095d2ae9e9ee388f0e5ed9943a8fca4f`，R16 build manifest 记录 Release Native CUDA 构建；R16 `cfar_numerical_diagnostics.csv` 记录每期约 1.55M 个 GO 方向直接重算。R14 未保留构建命令、历史 CMake cache / 编译器身份和 CFAR power/threshold map，故不能追算其 per-CUT 错误 hit 数，也不能确定旧 binary 被复用的具体构建环节。
- R18 在 seed606/707/808、6.7/0.5 m/s 与 30/20 dB 的 12 个零时延场景完成 252 个 Native CUDA Production branches。连续面积杂波、输入 causal triplet、valid-CUT geometry/分母与 union-hit gate 均通过；每个方法/速度/SNR 组合的 truth-period 检测观察为 9/9。经验 Pfa、PK/PKR residual closure、target-only 幅相/SCNR、ID-switch lineage 和 radial velocity 未闭合，故不报告性能胜负、显著性或未知误差收益。seed909 C3 参考因预注册 exact-support gate 不通过而停止评价。详见 [R18 任务结果](phase_r/R18/06_signal_and_task_results.md)与[方向裁决](phase_r/R18/09_next_research_direction.md)。
- R14/R15 所记录的检测、候选、航迹与反事实仍是当时真实运行的历史观察，但建立在修复前 CFAR executable 的输入集合上；不得将其直接当作修复后 Production 的 C2/C3 任务表现。R15 回放复用 R14 检测，也继承该限定。R16 同运行 C0/C1 的比较仍是同一 executable 内的冻结场景结果。
- R14/R16 的零时延结果不回答未知系统误差因果效应；AI 必要性仍未建立，也没有证据把低速目标或复杂杂波升为优先长期方向。

## 已停止或未成立的研究假设

- 停止把修复前 R14/R15 检测集上的单次 track switch 当作修复后 Production 通用行为或 Robust DDC 独立算法创新证据。
- 固定 Gamma=1 C1 在 R16 场景中目标漏检及杂波残余代价较大；停止将该固定参数方法视为该场景的可接受整体替代，但不外推至所有盲确定性估计器。
- R14/R15/R16 的零时延证据不支持未知系统失配的因果结论，也不能据此否定该方向。
- 当前证据没有建立 AI 的必要性，也没有证明低速目标或复杂杂波已经成为优先研究瓶颈。

## 尚未解决的主要问题

- paired OFF reference 的现实获取和部署可得性；
- H0 false-hit / 经验 Pfa 的可评估统计口径；
- 径向速度与其他参数评价的闭合范围；
- 使用同一修复后 Production executable 时，C2/C3 相对 C0 的目标保持、候选与 TrackManager 代价；
- 未知系统失配对任务性能的因果影响，尚需由与该问题对应的独立证据回答；
- R14 专属旧版 threshold/power map 已清理，因此不能追溯逐 CUT 的旧版负/零门限数量。

## 当前唯一优先任务与阶段门

- **当前唯一优先任务：** 无。R18 已停止；等待用户决定是否授权下一项范围明确的工作。
- **阶段建议：** 若继续，先对现有证据和评价契约做限界审查，确认独立 H0 false-CUT、PK/PKR residual 与目标/Detection/TrackManager lineage 的可补性；之后才考虑单一非零物理误差的四条件因果设计。
- **停止条件：** 不把 observed hit fraction 或未匹配 Detection 行表述为 Pfa；不得将 R18 零时延结果用来支持未知误差因果结论；不得自动启动新实验、AI 训练或修改研究总纲。
- **后续任务授权：** 上述建议未获自动执行授权。

## 总纲修订讨论

R17 对总纲第 9 节的恢复仅限当时用户明确批准范围。R18 执行、清理、阶段报告和方向建议均未改动研究总纲；其 blob 仍为 e637e3769bde7814298ff012d0f42301884e2985。未来如有新证据提示总纲其他内容需要修改，应先在动态状态或单独提案中列明证据、拟改范围及对既有成果的影响，并等待用户对具体内容或清晰范围明确批准。
