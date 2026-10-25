# PGRCC 动态研究状态

本文件记录会随研究阶段变化的状态，不重新定义长期研究目标。研究主题、总体目标、科学问题、研究内容和长期技术路线以[研究总纲](RESEARCH_MASTER_PLAN.md)为准；本文件不能覆盖、重新定义或暗中替代总纲中的长期目标或路线。

## 状态身份

- 本次动态状态修订基线：`c57c6e53598b9ab4087a7764a392244873a8fe66`（工作分支 `codex/unknown-system-error-next-stage`）；该 SHA 是状态更新前的代码/文档基线。
- 研究总纲版本：Git blob `e637e3769bde7814298ff012d0f42301884e2985`；R17 仅按用户明确范围恢复总纲第 9 节的三段文字至 R16 前版本，其他正文不变。
- 本状态整理日期：2026-10-25。
- 最近完成的方向裁决：R17 Production executable / GO-CFAR 跨版本身份审计；这是离线证据审查，不是新实验。详见[研究决策日志](RESEARCH_DECISION_LOG.md)与[Phase R 导航](phase_r/README.md)。
- 当前在研阶段：R18 传统方法 Production 最小比较；实验矩阵已冻结，seed101 技术 pilot 尚未闭合，尚无正式矩阵结论或方向裁决。

## 当前研究阶段：R18 pilot gate（未闭环）

R18 的长期研究定位仍遵循总纲中的 GMTI 任务瓶颈识别与公平传统 Baseline 评价。已提交的冻结设计和配置见 [R18 总实施计划](phase_r/R18/00_master_execution_plan.md) 与 [R18 实验矩阵](phase_r/R18/04_experiment_matrix.md)。当前状态只记录已有执行，不代表本次治理修订启动了 R18 实验。

- **执行源码身份：** R18 设计/runner identity 改动在 `4c6d4d303621324f5e174db0ba1811c5691df2e2`；适配器 tap 审计根因修复在 `c57c6e53598b9ab4087a7764a392244873a8fe66`。修复有对应回归测试；它不改信号处理或检测参数。
- **pilot 已运行的证据：** seed101、6.7 m/s、30 dB、0 ns、3 periods 的本地 pilot manifest 位于被忽略的 `.scratch/r18_gate_pilot_seed101_retry_20261025/manifest.json`，状态为 `completed_with_gaps`；记录 21 个 Production branches 均返回 passed。Stage2 area gate 与 target triplet gate 通过：390 continuous-area packets、1,952,655 samples、实测 mean power 0.0223353；triplet 最大误差约 `1.35e-6`，小于冻结容差 `5e-5`。
- **尚未闭合：** 同一 manifest 将 `valid_cut_denominators` 标为 `NOT_EVALUABLE`。原始运行位于本地忽略目录，R18 紧凑证据包尚未完成并提交；因此这些 pilot 数字是待审计的运行状态，不是可供独立审查的阶段结果。首个 seed101 尝试因 tap-audit runner 符号错误退出，失败记录保留在 [R18 pilot 失败记录](phase_r/R18/evidence/pilot_seed101/pilot_failure_record.md)，后由上述提交修复。
- **正式矩阵状态：** 3 seeds × 2 speeds × 2 SNR 的 12 场景正式矩阵尚未执行；不作方法性能排名，不形成新的 `CONTINUE / STOP / PIVOT` 科研裁决。
- **当前证据状态：** 冻结设计与配置、runner 身份入口及修复为 `IMPLEMENTED`；seed101 pilot 分支为 `EXECUTED`；area/triplet 门在该 pilot 内可评估并通过；有效 CUT denominator 门为 `NOT_EVALUABLE`；`PERFORMANCE_VERIFIED=false`。
- **阶段停止条件：** 在 valid-CUT denominator 标签及其根因被审计、pilot 关键产物被紧凑化并可独立核查之前，不进入 R18 正式矩阵；不得以 branch 状态 passed、area gate 通过或退出码代替任务性能结论。

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

- **当前唯一优先任务：** 闭合 R18 seed101 pilot 的有效 CUT denominator 门，并完成可独立审查的紧凑证据。pilot gate 通过前不启动冻结的 12 场景正式矩阵。
- **当前阶段建议：** 先核查 pilot 中 CFAR geometry、valid CUT 数量、H0 false-hit 指标与 `NOT_EVALUABLE` 判定的关系，确认统计口径后再决定 pilot 是否满足进入门；不得降低门槛或仅重标状态。
- **停止条件：** valid CUT denominator、truth association、生产输入身份或紧凑证据任一无法闭合时，停止性能比较并记录原因；observed hit fraction 不得表述为 Pfa；单 seed 不得升级为统计性能结论。
- **后续任务授权：** R18 冻结设计中已有的正式矩阵属于先前阶段计划；本次治理修订没有启动或扩展其运行，也没有授权 AI 训练或新的研究方向。

## 总纲修订讨论

本次治理修订和 R18 pilot 状态记录均未改动研究总纲。R17 对第 9 节的恢复仅限当时用户明确批准范围，实验事实与历史报告保持原样。未来如有新证据提示总纲其他内容需要修改，应先在动态状态或单独提案中列明证据、拟改范围及对既有成果的影响，并等待用户对具体内容或清晰范围明确批准。
