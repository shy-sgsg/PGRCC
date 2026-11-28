# PGRCC Phase R / R22 V2 配对工程结果独立审查请求

请把本提示词与 `outputs/r22_chatgpt_review_evidence_v2_engineering_pair_20261028.zip` 一起提交给 ChatGPT。证据包中的 `README.md` 列出内容、文件身份和省略的大型可再生成文件。

## 必须先做的事

请先读取证据包内 `research_context/docs/research/RESEARCH_MASTER_PLAN.md`，再读取同一快照中的动态研究状态、决策日志和审查协议，然后阅读本提示词指定的 R22 报告、配置、源码快照和本次 CUDA 产物。研究总纲 Git blob 应为：

`e637e3769bde7814298ff012d0f42301884e2985`

不能只根据本提示词或阶段报告摘要代替读取总纲。若总纲或关键证据在证据包中缺失、损坏或无法读取，请明确列出文件并将相关结论标为 `NOT_VERIFIED`。如能访问 GitHub，请核对仓库指定 commit 和远端 HEAD；不要把未推送的本地源码快照误称为远端已验证源码。

## 审查快照与执行身份

- 仓库：<https://github.com/shy-sgsg/PGRCC>
- 分支：`codex/unknown-system-error-next-stage`
- 本次运行时 HEAD：`c1215354b4354fb598bc747eb799ff9995eeee58`
- 执行时工作区有 5 个 tracked 文件修改。因此该 commit SHA **不单独等同于本次执行的完整源码状态**；两份 run manifest 的 `source_identity.files` 给出了源码 SHA，证据包亦包含相关完整源码和相对该 commit 的差异。
- 先前记录的远端 HEAD：`e27a78e234e7647dd870d06b0db058d4b71fdf02`。请在可访问时重新核验远端状态，并将审查快照与远端 HEAD 分开报告。
- 阶段：R22 V2 距离支持域修订后的首个配对工程 seed。
- 实际运行：seed `1930101`，2.5 m/s，20 dB，20 targets，10 periods，beam 50；A 为 0 ns，B 为 +4 ns。两次运行合为 **一个配对 seed**，不是两个独立样本。
- 生产路径：本机 NVIDIA RTX 3050 Laptop 4 GB 的 Native CUDA；不是 CPU 模拟、CoreX、软件测试或历史结果。
- Production binary SHA-256：`d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`
- Stage2 binary SHA-256：`ca8aa9eb8b7d97e5743a27e67c312e66a4efecbf2c76880db581192afd08ce8e`
- V2 方法配置 SHA-256：`588c36904f62febad8476ddccb789ba67de2e9c23dc2ff12b52418eb06706bbc`
- V2 机器可读设计 SHA-256：`4bd2014b1fe1eab5c8cbcca1a380638231cf26dddf5de683a086f1b1b91a7a1c`
- 当前 R22 完整设计仍为 594 cases；截至本快照只执行了同一 seed 的 0 ns / +4 ns 两个配对 case。其余 5 个工程 seed、screening、confirmatory、dense/stress、bridge 和独立 H0 均未运行。

## 必读文件和证据顺序

1. 证据包内研究总纲、`RESEARCH_STATUS.md`、`RESEARCH_DECISION_LOG.md`、`CHATGPT_REVIEW_PROTOCOL.md`、`AGENTS.md`。
2. `docs/research/phase_r/R22/12_v2_range_support_domain_amendment_20261028.md` 与 `13_v2_engineering_seed1930101_pair_20261028.md`。
3. V2 方法配置、机器可读实验设计、R22 执行契约，以及 R22 runner、scenario factory、evaluator、production calibration runner 和对应源码差异。
4. 两份 `r22_run_manifest.json`、`target_generation_manifest.json`、压缩包内的 Stage2 报告/杂波诊断/配对复用映射、Production runtime XML / branch manifest / 日志 / beam stage / CSI taps / CFAR geometry / 检测航迹记录、完整评价 CSV 和清理台账。
5. 必要时对照历史 R22 报告，但不得把 V1A 的失败门或其他历史运行当成 V2 本次执行结果。

证据包为审查与交叉核验整理，不含可再生成的 `.bin`/`.npy` 大数组和重复的 pulse 级 truth CSV；没有这些数组时，不能声称可以从原始 IQ 完整重放。原始运行目录保留在仓库工作区中，压缩包清单说明每类文件的取舍。

## 当前待独立核实的结果摘要

以下数字来自阶段报告，必须回到证据包的 manifest、CSV、runtime XML 和日志独立核验：

- V2 将所有 Production 方法、角色、误差条件和 evaluator 的距离范围统一设为 0-based 闭区间 `rg_st=666, rg_ed=1197`。该范围来自距离轴 origin `82,802.6768996 m`、间隔 `2.498270483333333 m/bin`，覆盖 84,500–85,500 m 的连续面积杂波 ROI bins 680–1079，并以完整 133-bin groups 对齐。请评估物理依据、闭区间映射和分组边界是否正确，且是否足以支持整个目标/杂波处理链。
- 两个 A0_ON Stage2 报告均记录 continuous-area clutter `1300 packets`、`6,508,782 samples`、平均功率 `0.0225128995081`、lag-1 rho `0.979999807132`，热噪声功率 `0.00100014187201`。+4 ns 条件复用同 seed 的 A0 C+N 背景；其他条件里“新增注入计数为 0”不等于没有杂波。请核对配对映射和五类输入因果 triplet 的误差门。
- 0 ns：36/36 Production 分支通过；5 个零时延校正角色按规则标为 `NOT_APPLICABLE_ZERO_DELAY_REFERENCE`；8/8 reference artifacts 通过；12/12 ON 行 EVALUABLE，2,400 target-period opportunities。
- +4 ns：54/54 Production 分支通过；10/10 reference artifacts 通过；18/18 ON 行 EVALUABLE，3,600 target-period opportunities。
- 两个条件每条分支 `union_dynamic_in_band` 和 `union_full_band` valid CUT 均为 `5,272,800`。请判断分母、门控和跨条件公平性是否从原始审计字段得到支持。
- 单 seed 描述性命中：A0 167/200 (0.835)，+4 ns Current 25/200 (0.125)，Known physical correction 167/200 (0.835)，P1 target-free OFF blind correction 167/200 (0.835)。Mode-A/B P2 为 0.865/0.855；PKR 为 0.875/0.865。目标-周期机会不是独立 seed；这些比例不是显著性或普适 Pd 增益。
- Known correction 只在 evaluator-only apply 使用 4 ns 真值；盲 P1 估计 3.215904742478316 ns，未用真值估计或应用校正。另有 M1 估计 3.7521942288603465 ns、support 2364、normalized model residual 0.02488；请核实两类估计使用的信息边界，不能将 M1 误称为 P1。
- A0 与 Known correction 残余闭合 spread 为 `1.5643801773990162e-6 dB`。PKR 相对 PK 的额外残余抑制为 `-0.02248363873422221 dB`，即本 seed 没有显示额外抑制。不得把 scalar CSI `active_support_unmasked` 混合 tap 当作 clutter-only 指标。
- 所有条件的径向速度 `radial_velocity_mps` 均 0/200 valid，原因是 `ambiguity_unresolved` 或没有匹配检测；不能报告径向速度 RMSE。独立 target-free H0 seeds 未运行，因此没有经验 Pfa。
- 当前两个 case 的状态为 `IMPLEMENTED / EXECUTED / EVALUABLE`，但 `PERFORMANCE_VERIFIED=false`。没有统计显著性、可推广盲估计能力、完整矩阵完成或 AI 必要性的证据。

## 请回答的核心问题

### 第一层：本阶段执行与结论是否可信

1. 距离支持域 `[666,1197]` 的物理推导是否正确？将方法 fit/reference/apply 限定到该共同范围是否符合各方法的任务作用域，是否可能排除有效目标或改变方法定义？有哪些需要标注的边界风险？
2. 面积杂波是否真实注入并在各条件间可比？`A0_background` 复用链、OFF/ON/TO pairing、truth binding、运行输入身份和因果 triplet 是否有证据闭合？
3. 所有 Production 条件是否使用同一 range overrides、相同算子和相同门槛？Production 前置校准门、参考校验、CFAR valid CUT、beam stage、CSI、目标 lineage 是否从产物得到了完整证据？
4. 盲估计是否严格未读真实 delay、目标 truth 或配对无误差参考？Known correction 的真值是否严格限制在 evaluator-only apply？摘要中的 P1、M1、P2、PKR 和 Known 是否准确区分？
5. A0/Known 的闭合指标是否实现了真正的物理校正闭合？残余功率选择是否避免了噪声与杂波混合指标？检测机会和命中分母是否正确？目标-周期层级是否被错误当成独立样本？
6. 哪些参数估计指标不可评价，原因是什么？特别审查径向速度歧义和没有 H0 导致的 Pfa 缺口。
7. 压缩审查包省略了什么原始材料？它足以审查已报告主张到什么层次，哪些结论因为不能重放原始 IQ 必须降级？

### 第二层：总体 GMTI 科研价值与方向

1. 在单 seed 观察下，未知 4 ns 误差导致任务性能退化是否是可信的可行性信号？哪些替代机制仍可能解释观察？
2. Known correction 是否给出明确的 recoverable space？P1 是否真的达到该参考，是否只是同一个 seed 的条件结果？还需要多少独立 seed 或任务场景才能判断可重复性？
3. 当前确定性估计与已知校正上限的差距如何解释？现有证据是否表明盲估计已足够，还是仅说明尚未完成多 seed 评价？
4. 这阶段是否推动总纲中的 GMTI 目标？是否有更简单的传统方法或更重要的任务瓶颈值得优先？是否有任何证据支持 AI 必要性？
5. 结合工程门和资源约束，建议继续、停止还是转向？只给出**一个**下一步建议，并给出其决策价值、进入标准、停止标准和必要的独立 seed 数；不得建议自动启动 AI 训练。

## 审查规则与输出格式

- 严格区分 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED`，对每项注明适用范围和证据。
- 对核心主张标 `CONFIRMED / PLAUSIBLE / NOT_VERIFIED`，引用压缩包内相对路径、JSON 字段、CSV 列或日志位置；不要只重复阶段报告。
- 报告计划、实际启动、完成和可评价 case 数；明确本次只有一个独立配对 seed，594-case 计划尚未完成。
- 分别报告 0 ns、+4 ns、Known correction、盲法结果，说明效应方向、分母、配对单位、缺失值和统计限制。没有 H0 时不得报告经验 Pfa。
- 给出两个层面的裁决：阶段执行正确性 `PASS / PASS_WITH_LIMITATIONS / INCONCLUSIVE / FAIL`；研究方向 `CONTINUE / STOP / PIVOT / INCONCLUSIVE`。说明单 seed 的限制。
- 明确回答：未知通道误差是否降低 GMTI 性能、已知校正恢复多少、盲估计差距多少、是否有必要继续研究未知物理状态估计、当前是否应调整为低速目标保护或复杂杂波建模主线。对无法由现有证据回答的部分标 `NOT_VERIFIED`。
- 研究总纲的任何修改只能作为建议提交用户讨论，未经用户明确批准不得直接修改或当作已生效。

请不要把通过 Production gate 等同于性能已验证；不要把单 seed 当统计结论；不要因为单次已知校正闭合而宣称普适性；不要把盲法一个 seed 的相同命中数当作已证明的盲估计能力；不要把当前证据当作 AI 训练依据。
