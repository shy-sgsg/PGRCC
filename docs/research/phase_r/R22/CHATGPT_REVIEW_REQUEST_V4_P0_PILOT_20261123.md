# PGRCC R22-V4 P0 Native CUDA Pilot 独立科研审查请求

## 固定审查身份

| 项目 | 身份 |
|---|---|
| 仓库 | https://github.com/shy-sgsg/PGRCC |
| 分支 | codex/unknown-system-error-next-stage |
| 固定报告审查 commit | b4d5e72ef92d9dc43344fce761a0187afad2ae75 |
| 实验执行源码 commit | 715639c8e9aad7359eb1a5058f9238c0d51f99a1 |
| 研究总纲路径 | docs/research/RESEARCH_MASTER_PLAN.md |
| 固定总纲 blob | e637e3769bde7814298ff012d0f42301884e2985 |
| 机器证据导航 | docs/research/phase_r/R22/evidence/v4_native_cuda_pilot_20261123/README.md |

请以固定审查 commit 为准。分支后续增加本提示词和导航链接的提交不改变本次实验报告快照。不要以当前 HEAD 或后续文件替换固定版本中的研究状态、报告或结果。

## 首先阅读总纲

请首先读取审查 commit b4d5e72ef92d9dc43344fce761a0187afad2ae75 中完整的：

docs/research/RESEARCH_MASTER_PLAN.md

理解 PGRCC 的研究主题、总体目标、核心科学问题、研究内容、总体思路和长期技术路线。该文件 blob 应为 e637e3769bde7814298ff012d0f42301884e2985。

然后按以下顺序读取同一 commit 中的文件：

1. docs/research/RESEARCH_STATUS.md
2. docs/research/RESEARCH_DECISION_LOG.md
3. docs/research/CHATGPT_REVIEW_PROTOCOL.md
4. AGENTS.md 和 README.md
5. docs/research/phase_r/R22/README.md、相关 R22-V4 设计和冻结前报告
6. 37_v4_p0_preflight_and_authorization_20261123.md
7. 38_v4_p0_native_cuda_execution_20261123.md
8. 39_v4_p0_preliminary_research_assessment_20261123.md
9. 40_v4_confirmatory_freeze_readiness_20261123.md
10. evidence/v4_native_cuda_pilot_20261123/README.md 及其中的机器可读 JSON、CSV、图表
11. 必要时检查该 commit 的 compact archive identity 修复源码和回归测试。

如果 GitHub 无法读取固定 commit 或总纲，明确报告文件和版本读取失败；不得声称完成了总体科研方向审查。

## 本次任务和实验范围

本阶段执行用户授权的一个 target-present R22-V4-P0 工程 pilot：seed1937003、径向速度2.5 m/s、SNR20 dB、+4 ns、20目标、10周期、beam50。本机 Native CUDA Release，USE_COREX=OFF。GPU 当时由用户的外部训练进程占用；执行没有终止或修改该进程。

Pilot 使用 A0、Current、Known evaluator-only、Fixed D3、M1 causal prefix 和 Huber D3 causal prefix。每个适用方法处理 OFF/ON/TO 配对角色。它测的是一个完整 case 的物理输入、Production、输出捕获、归档和恢复路径，不是预注册确认性矩阵。

实验真实执行源码 commit 是 715639c8e9aad7359eb1a5058f9238c0d51f99a1。归档身份修复和回归测试是在该实验结束后形成，包含在报告快照 b4d5e72 中；该修复影响归档身份校验，不代表重新运行或更改 CUDA 计算结果。

## 重要可核查观察

- Stage2 连续面积杂波有效：1,300 packets、6,508,782 samples、实测平均功率0.022765506、lag-1相关0.979992769。各角色的零新注入计数应结合背景复用映射解读，不能误判为无杂波。
- +4 ns 注入记录在 echo superposition 后、final protocol write 前；十周期配对背景映射均匹配。
- 18/18 Production 分支返回码0且通过分支门。十周期每分支有效 CUT 几何分母为5,272,800；这些相关 CUT 不是独立统计样本。
- 90,000条 CSI_AFTER 固定目标 ROI 复数输出均为有限值，200条真值/ROI身份核验通过。
- M1 与 Huber 各10/10周期有估计、无 fallback、truth-use flags 为 false；估计器仅读取同 case 当前和历史的未知误差 ON C1/C2。
- 单 case target-period 命中：A0 140/200，Current 26/200，Known 140/200，Fixed D3 142/200，M1 140/200，Huber 142/200。
- M1 相对 Fixed D3 的共同成功位置 RMSE 为43.704 m 对56.204 m（n=135）；相对 Huber 为43.583 m 对50.828 m（n=136）。所有单 seed差值均只作描述。
- OFF 是配对角色而非独立 H0 seed。经验 Pfa 状态为 NOT_EVALUABLE；不能把 OFF CUT比例、目标机会、周期、Production分支或未关联航迹当作独立虚警样本。
- 归档 finalize 首次失败后，修复 pipeline local case label 与 external scene identity 的核对；同一输出根续跑只复核归档，已完成 CUDA case 被跳过，没有重跑 Stage2 或 Production。相关 R22 测试 139 项通过。
- 工程判定 PILOT_PASS；IMPLEMENTED、EXECUTED、EVALUABLE 为 true；PERFORMANCE_VERIFIED 为 false。

## 本机完整 ZIP 的审查限制

完整 compact ZIP 仅在执行机器本地，GitHub 上没有上传它：

outputs/r22_v4_p0_seed1937003_20261123/archives/engineering/engineering__seed_1937003__vr_2d5__snr_20__delay_4ns__targets_20__periods_10__attempt_001.zip

其本地记录大小13,037,254,578 bytes，SHA-256为0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02；报告记录 ZIP CRC、archive manifest 和422个必需V4成员均通过，随后清理了该 case 的 raw。你不能从 GitHub 直接打开这份本机 ZIP。请独立审查 Git 快照中可访问的报告和紧凑审计摘要；凡需重新读取 ZIP 内原始 IQ、完整日志或逐成员内容才能验证的事项，标为 NOT_VERIFIED。不要将执行者保存的摘要误称为你亲自检查了 ZIP 内原件。

## 请完成双层审查

### 第一层：阶段任务和证据正确性

独立检查：

1. 固定 commit、执行源码、design、Stage2/Production binary 与研究总纲身份是否区分清楚且互相一致。
2. Native CUDA 证据是否确实来自真实 Stage2/Production 执行，而非 CPU模拟、软件测试或历史数据；外部 GPU 负载是否被正确披露。
3. 连续面积杂波、真实注入条件及配对背景复用是否足以证明 clutter 存在且处理角色可比较。
4. Known 是否只在 evaluator-only 路径读取真值；M1/Huber 是否满足盲信息边界和周期因果前缀，不读取目标真值、配对 OFF、未来周期或其他 oracle。
5. Production branch、runtime、CFAR分母、目标检测/参数估计/航迹 lineage、目标复数响应及所有失败门是否正确解释。
6. 归档首次失败、代码修复、断点续跑、ZIP校验和raw清理的证据链是否充分，且没有把恢复归档写成重跑实验。
7. 当前展示的命中覆盖、位置误差和目标响应统计是否使用了正确分母、配对单位和限制；是否有指标被误写为Pd、Pfa、SCNR或统计显著性。
8. 标出你无法直接验证的内容、竞争解释、证据缺口和任何不一致。

### 第二层：总体科研价值与研究方向

结合研究总纲判断：

1. 本 pilot 实际推动了哪个长期 GMTI 目标，贡献属于工程基础、物理机制证据、评价证据还是算法创新。
2. 当前单 seed 是否足以支持“+4 ns未知误差降低任务性能”及“Known可恢复损失”的一般结论；更准确的限定表述是什么。
3. M1/Huber 与 Known 的差距当前能回答到什么程度；是否仍缺独立 seed、信息匹配及任务非劣门。
4. 这批证据是否支持继续未知物理状态估计，或有理由转向低速目标保护/复杂杂波建模。
5. V4确认性设计是否具备冻结条件；列出优先需要用户/工程需求方决定的MDE、检测覆盖率非劣界值、航迹保护界值、统计功效与资源容量问题。
6. 是否存在更简单的确定性替代方法；当前是否满足 AI 进入门。请明确给出 CONTINUE / STOP / PIVOT 建议及证据。

## 研究总纲修改权限

如认为研究总纲需要调整，只能提出具体建议、证据、影响和替代方案供用户讨论。任何正文修改均须用户对具体内容或明确范围事先批准。不得把本阶段裁决、用户对单 case pilot 的批准或你的建议视为总纲修改授权。

## 输出格式

请先给出审查裁决及信心水平，然后分别报告阶段正确性与总体科研价值。将每条结论标为 VERIFIED、SUPPORTED_WITH_LIMITS、NOT_VERIFIED 或 CONTRADICTED，并引用固定 commit 内的具体文件路径和关键字段。

结尾明确回答：

1. 这一个场景中 Unknown Current 是否出现任务退化信号？
2. Known correction 在此场景恢复到什么程度？
3. M1/Huber 与 Known 相比，目前可以、不能得出什么结论？
4. 是否应继续 V4 未知物理状态评价，是否有理由转向其他 GMTI 瓶颈？
5. AI 是否有必要进入？
6. 确认矩阵冻结前必须由用户决定哪些项目？

不要启动实验、CUDA、矩阵或 AI，也不要把单 seed工程结果描述成统计显著性或完成论文创新验证。
