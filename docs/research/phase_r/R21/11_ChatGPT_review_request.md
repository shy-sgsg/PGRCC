# PGRCC R21 独立科研审查请求

> 使用说明：R21 两个冻结 seed 已完成。本提示词的审查 commit 在 `0957f49c028377cbd7a15c2ff0b2eda3f75ce9a9` 之后的阶段报告提交中；提交完成后将下方 review commit 字段填入该报告提交的完整 SHA，再连同仓库链接交给 ChatGPT。结果状态和失败记录按现有 manifest 原样披露。

## 可直接提交给 ChatGPT 的提示词

请对 PGRCC R21 开展独立、证据驱动的科研审查。**首先读取本次指定审查 commit 中的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解项目的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路和长期技术路线，然后再审查 R21。**不能只依据本提示词或阶段报告中的概述代替实际读取。

若无法访问指定 commit 或研究总纲，请明确列出无法读取的文件，将总体科研方向判断标为 `NOT_VERIFIED`；可以继续审查可访问的局部实现和证据，但不能声称已完成总体方向审查。不得修改研究总纲，也不得启动后续研究或训练 AI。

### 审查快照与身份

- 仓库：`https://github.com/shy-sgsg/PGRCC`
- 分支：`codex/unknown-system-error-next-stage`
- 审查 commit（完整 SHA）：`[将在阶段报告提交后回填]`
- R21 CUDA 执行源码 commit：seed1001 `6f261b218691cf99f2e1d90c2ed5cda70a96a4f4`；seed1002 `0957f49c028377cbd7a15c2ff0b2eda3f75ce9a9`
- seed1001 evaluator 重放源码 commit：`0957f49c028377cbd7a15c2ff0b2eda3f75ce9a9`
- 研究总纲基线 blob SHA-1：`e637e3769bde7814298ff012d0f42301884e2985`
- 研究总纲：`docs/research/RESEARCH_MASTER_PLAN.md`，取上述审查 commit 中的版本
- 动态状态：`docs/research/RESEARCH_STATUS.md`，取上述审查 commit 中的版本
- 研究决策日志：`docs/research/RESEARCH_DECISION_LOG.md`，取上述审查 commit 中的版本
- R21 证据目录：`docs/research/phase_r/R21/`
- 审查范围：R21 交付文件、代码、测试、冻结配置、实验 manifest、实际 CUDA 运行身份、原始/汇总证据及引用的历史基线

如果远端 HEAD 与指定审查 commit 不同，请分别报告两者的完整 SHA，并以指定快照为主。不要用“最新提交”或“当前 HEAD”替代明确的版本身份。逐项区分实验执行源码、报告生成源码和最终审查快照。

### R21 要回答的科学问题

R21 评估：在 R8/R12/R18/R20 已有证据及其限制下，固定 `+4 ns` 通道时延是否造成可重复、可归因的 GMTI 任务性能损失；已知物理校正能恢复多少；确定性静态延迟估计（固定 D3、M1，以及若通过生产路径审计则包括同输入 Huber D3）能否在不读取真值或配对参考的条件下恢复任务性能；这条静态时延估计主线是否值得继续投入。

R21 的范围限于修复并验证任务评价器、审计方法公平性，以及最多两个预先冻结的独立新 seed。每个 seed 为 `6.7 m/s`、`30 dB`、`+4 ns`、三个目标周期，并使用与 R12 相同的物理和配置口径及连续面积地杂波。实验方法最多六种：A0、B(+4 ns)、Known Physical Correction、R9 fixed D3、M1、以及仅在同输入且生产校正路径得到验证时的 Huber D3。每种适用方法均应有配对 H0/OFF 与 H1/ON 数据。若 Huber 没有经过验证的生产校正路径，必须标为不可用，不得伪装成生产比较。

### 请先独立核验的证据

阅读 R21 的执行计划、证据清单、评价器修复、Doppler/真值映射验证、冻结实验契约、历史回归、方法公平性审计、实验报告、方向评估和审计记录；再检查对应实现与实际产物。至少核验：

1. **评价器正确性与历史兼容性**：区分“含任一未匹配 payload 的 track ID”和“从未匹配任何目标的 track ID”；检查 payload 级未匹配数、目标匹配周期、track handoff、同时多目标、错误关联及同一 ID 关联多个真值等语义。检查 Doppler 正负轴向间隔、行号到物理频率映射和逐机会的 Production 轴/PRF/距离率门控。真值 `range_bin` 必须有限、为合法整数且与场景距离轴一致；无目标确认时指标应为 `NOT_EVALUABLE_NO_TARGET_CONFIRMATION`，不得以空值标成 evaluable。检查 Production Detection、确认轨迹和输出 payload 的同周期关联及重复/交叉关联。确认 R19 冻结的 84 个 ON 命中、机会数、漏检和比例与历史值完全一致；历史 v1/v2 评价器及 CSV 不应被覆写。
2. **方法信息边界与公平性**：Known 校正可以只在 evaluator 中使用真实参数；任何待评估估计器不得使用注入真值、A0/Known IQ、目标真值、未来数据、配对无误差参考或基于 Detection 的调参。逐项检查各条件从输入、校正、处理算子到 Production 输出是否一致。特别区分 R9 fixed D3、R12 Huber D3 和 M1 的估计来源、值、输入与校正算子；不得因名称相似而视为同一方法。
3. **实验可比性**：核对 seed 是否独立且在开发/调参阶段未使用，配置是否在运行前冻结，场景、杂波注入功率/样本/有效距离单元、F1/F2 输入和处理链是否跨条件可比。每 seed 的三个目标周期不是三个独立 seed。
4. **真实生产证据**：确认所报告的正式结果确由本次真实 Native CUDA Production 路径执行；逐一检查实际 runtime XML、源码/构建身份、配置、输入身份、逐 beam/stage 记录、CSI tap、CFAR geometry、日志、manifest、退出状态和输出。编译、CPU 模拟、软件测试、历史数据或仅有文件不能替代正式 CUDA 证据。单次 smoke 不计入正式统计。
5. **物理与任务闭合**：核对连续面积杂波确实注入且有效，A0 与 Known 校正的物理闭合、B 相对 A0 的退化、已知校正可恢复空间、估计校正实际恢复量；检查目标幅相保持、残余杂波、SCNR、有效 CFAR CUT、检测及虚警评估资格、参数估计、Production TrackManager 关联与协议输出、虚假确认轨迹和运行时间。复核指标单位、方向、分母、统计单位及失败机会。

对引用的 R8/R12/R18/R19/R20 数值，请回到其原始报告、输入身份和脚本核验；不得只复制 R21 汇总表。历史结果只能按其原始条件解释，不能改写为 R21 新实验。

### 两层审查

**第一层：R21 阶段任务正确性**

- 评价器缺陷是否以针对性测试复现、修复，并在真实历史证据上重放？修复是否保持 R19 关键冻结统计不变？
- R8/R12/R18/R20 回归、完整测试和新增测试分别实际运行了吗？报告是否给出命令、退出状态和结果？
- 冻结实验是否按约定完成；有无缺 seed、缺 H0/H1、条件不匹配或事后改变配置？
- CUDA Production、杂波、物理校正、CFAR、检测、参数和 track/payload 证据是否完整？哪些指标确实可评价？
- 是否存在错误归因、漏记失败、选择性报告、分母错误、泄漏、对照不公平或不能复现的结论？

**第二层：总体科研价值与方向**

- 本阶段对研究总纲中的长期 GMTI 目标和核心科学问题提供了什么证据？
- 未知静态通道时延在所测场景中是否造成任务相关损失？Known 校正恢复的空间有多大？确定性估计相对 Known 上限还差多少？
- 两个新 seed 是否支持跨 seed 的一致性；其样本量能支持什么、不能支持什么？目标周期不能冒充独立统计样本。若缺少独立 H0 机会或合法统计单位，明确将统计 Pfa 标为 `STATISTICAL_PFA_NOT_EVALUABLE`，不得声称 Pd/Pfa 有统计显著改善。
- 传统静态延迟估计是否达到可重复、对 GMTI 任务有意义的收益？如果没有，建议停止或缩小该方向；若有，说明证据是否足以继续研究估计误差和可恢复收益。不要仅凭估计误差、单次成功或中间 SCNR 指标宣称任务成功。
- 这些结果是否足以说明更广泛的未知系统物理状态估计值得继续？哪些仍未验证？是否应该将优先级转向低速目标保护、复杂杂波建模或其他总纲内问题？方向建议须给出证据、替代方案和局限。
- 不得因当前估计器表现不足而直接推导“需要 AI”。依据项目 AI 使用门槛判断当前证据是否已达到可探索 AI 的前置条件；本次审查不训练 AI。

### 结论格式

请先给出简明结论，再给证据路径和逐项分析。每个关键主张标注其状态：

- `IMPLEMENTED`：代码或协议已实现；
- `EXECUTED`：本次命令/运行确实执行并有身份和产物；
- `EVALUABLE`：数据、分母和门控支持该指标计算；
- `PERFORMANCE_VERIFIED`：任务层收益在所声明范围内由有效证据验证。

针对以下问题分别作答，并引用文件、表格/行或代码位置：

1. 未知 `+4 ns` 时延是否降低 GMTI 任务性能？对每个 seed 和合并描述性结果分别报告，不夸大统计结论。
2. Known Physical Correction 恢复了多少（Known−B），该差值是否可评价并经物理闭合？
3. 每种确定性估计方法实际恢复多少（Estimated−B），恢复比例 `(Estimated−B)/(Known−B)` 如何？指标方向和分母是什么？
4. 估计方法距 Known 上限的差距是多少？R9 fixed D3、Huber D3、M1 是否被公平地区分？
5. 固定延迟估计主线应 `CONTINUE`、`STOP` 还是 `PIVOT`？支持该裁决的最小证据是什么？
6. 是否有证据继续研究更广义未知物理状态估计？是否应优先转向低速目标保护或复杂杂波建模？
7. 本次有哪些 `NOT_EVALUABLE`、`NOT_AVAILABLE`、失败机会、协议偏差和待补证据？

明确区分观察到的事实、从数据得出的推断和建议。单 seed 或两个 seed 的结果只作受限可行性/一致性判断；不宣称统计显著 Pd/Pfa 改善。不要自动启动 R22、额外 seed、参数扫描、新误差类型、算法生产改动或 AI 训练。若建议修改研究总纲，只能提出有证据的修订建议并交用户决定，不能视为已经获批。

### 本次阶段摘要与证据入口

- 阶段结论摘要：在两个独立 seed、每条件 3 个 ON 目标周期中，A0/ Known 各为 2/3；B(+4 ns) 为 1/3、0/3；R9 fixed D3、Huber D3、M1 各为 2/3、3/3。Known F1/F2 输入物理闭合通过，blind estimates 未达到 Known 闭合，但本小样本的目标命中没有落后于 Known。只有两个独立 seed，不得作统计显著性 Pd/Pfa 结论。
- 冻结设计与配置：`docs/research/phase_r/R21/05_experiment_design.md`、`03_frozen_design.json`、`03_method_fairness_and_frozen_design.md`；Stage1 config SHA `ad47b0d02b05ddc4839da39a7498faf1af25ca3ba8eec678597c0ce7a0f651f6`，Stage2 template SHA `1f4f9c8b9383614601dc019414b8c8a8788b195301526438b95a672b1ee13998`。
- 评价器历史回归：`02_historical_evaluator_regression.md`；R19 冻结的 336 项统计保持不变。当前任务 CSV 位于两 seed evidence 的 `evaluation/` 中。
- CUDA Production 运行证据：`evidence/cuda_pilot/seed1001_gatefixed_6f261b2/`、`seed1001_evaluation_replay_0957f49/`、`seed1002_0957f49/`。各实际 seed case 下保存 runtime XML、Production log、CSI tap summaries、CFAR geometry、Stage2 reports/configs、Detection 和 TrackManager 审计。Production binary SHA-256：`9ffc232966bd92a39edfb39b90730851014dfa1ae7237a4ac172a764f31cc566`。
- 关键结果表与图：`evidence/cuda_pilot/two_seed_summary.csv`、`two_seed_hit_fraction.svg`、`06_minimal_cuda_causal_pilot.md`。
- 失败案例：seed1001 初次 Stage2 输入缺 simulator position；`seed1001_attempt2/` 保留旧面积杂波 gate 失败 manifest 和清理账本；gate-fixed seed1001 的 18 个 GPU 分支均通过，但旧 wrapper 在评价打包时报 `KeyError('case_manifest')`，后由修复版 evaluator 对同一 CUDA 数据离线重放。seed1002 runner 完整成功。所有失败身份见各自 manifest 和 `cleanup_record.md`。
- 不可评价项：统计 Pfa (`STATISTICAL_PFA_NOT_EVALUABLE`)；径向速度 RMSE（ambiguity 未 resolved，0/3）；跨 seed 绝对幅相 transfer（CSI 自适应导致结果不稳定）；不同成功机会集合之间的参数 RMSE 配对差。
- 初步方向：固定时延主线 `CONTINUE`（仅限本次有界场景并等待独立审查）；广义未知物理状态仍 `NOT_VERIFIED`；当前没有 AI 必要性证据。不自动启动新 seed、R22、参数扫描或训练。
- 原字节文本审计包 `evidence/cuda_pilot/r21_cuda_text_audit_evidence_20261027.zip` 含 runtime XML、日志、CSI/CFAR、配置、Stage2 和逐机会评价表；多 GB `.bin`/CSI `.npy` 保存在运行机器，不放入 Git。若无法访问本地 raw 数据，请明确不能独立重算 raw-IQ 指标，并据可读文本证据完成其余审查。
