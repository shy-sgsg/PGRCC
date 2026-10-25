# PGRCC 动态研究状态

本文件记录会随研究阶段变化的状态，不重新定义长期研究目标。研究主题、总体目标、科学问题、研究内容和长期技术路线以[研究总纲](RESEARCH_MASTER_PLAN.md)为准；本文件不能覆盖、重新定义或暗中替代总纲中的长期目标或路线。

## 状态身份

- R18 执行源码版本：`014352ace1c4ad0bade0d759a099a47fe9ecfaa9`（工作分支 `codex/unknown-system-error-next-stage`）；完整阶段报告随当前交付提交。
- 研究总纲版本：Git blob `e637e3769bde7814298ff012d0f42301884e2985`；R17 仅按用户明确范围恢复总纲第 9 节的三段文字至 R16 前版本，其他正文不变。
- 本状态整理日期：2026-10-25。
- 最近完成的方向裁决：R18 正式矩阵完成后判为 INCONCLUSIVE，并停止本轮方法性能排名。详见[研究决策日志](RESEARCH_DECISION_LOG.md)与[Phase R 导航](phase_r/README.md)。
- 当前在研阶段：无。R18 已结束；下一项建议待用户决定，不自动启动实验或 AI 训练。

## 当前研究阶段：R18 完成；性能结论未闭合

R18 依照冻结设计完成 Native CUDA 正式矩阵。完整报告从 [R18 总实施计划](phase_r/R18/00_master_execution_plan.md)开始，结果与审计分别见阶段导航。研究主题和长期目标仍由研究总纲定义。

- **执行源码身份：** 正式 runner manifest 记录源码 commit `014352ace1c4ad0bade0d759a099a47fe9ecfaa9`；独立 Release build identity 记录 build 源码 commit `4c6d4d303621324f5e174db0ba1811c5691df2e2`。配置、runner、analyzer 和 Production 二进制 SHA 在 R18 执行报告中列明。
- **正式矩阵：** seed 606/707/808 × 6.7/0.5 m/s × 30/20 dB，共 12 场景、每场景 21 branches、总计 252/252 Production 分支通过执行门。每 case 390 continuous-area packets、1,952,655 samples。
- **可评估门：** 12/12 area、因果 triplet、valid-CUT denominator/geometry 与 final-union hit gate 通过。真实 Pfa、PK/PKR residual closure、目标专属幅相/SCNR、ID-switch 归因及径向速度误差未闭合。
- **独立参考：** seed909 Stage2 及 C2/C3 Gamma 检查已执行。C3 的 117 个 group 中 116 个 support count 不同，违反预注册 exact-support gate；没有启动后续任务评价，结果为 NOT_EVALUABLE。
- **方向裁决：** INCONCLUSIVE；停止本轮性能排名。矩阵 delay error 固定为 0 ns，不能回答未知误差退化、Known 恢复量或盲估计差距。低速、复杂杂波和 AI 优先级均未建立。
- **清理与容量：** R18 可重建数组在保存/核验非数组审计快照后清理；整个项目根目录实测约 2.8 GB，低于用户要求的 20 GB。清理台账记录在 [R18 cleanup ledger](phase_r/R18/evidence/cleanup_ledger.md)。
- **证据状态：** IMPLEMENTED=冻结配置、runner 与评价路径；EXECUTED=12 场景矩阵和 seed909 次级检查；EVALUABLE=上述物理/生产门及描述性任务计数；PERFORMANCE_VERIFIED=false。

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
