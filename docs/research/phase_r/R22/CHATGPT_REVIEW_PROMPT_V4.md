# PGRCC R22 H0 完整矩阵独立科研审查请求（V4）

本提示词已绑定固定 GitHub 审查快照 `8479e0e93eb91d72c2a80979795a42d51e867222`。请将本文件链接与该 commit 链接一并交给 ChatGPT；不要用远端最新 HEAD 代替该快照。

---

【PGRCC 科研阶段独立审查请求】

仓库：<https://github.com/shy-sgsg/PGRCC>

分支：`codex/unknown-system-error-next-stage`

审查 commit：`8479e0e93eb91d72c2a80979795a42d51e867222`

请首先读取**审查 commit 对应版本**的 `docs/research/RESEARCH_MASTER_PLAN.md`，理解 PGRCC 的研究主题、总体 GMTI 目标、核心科学问题、研究内容、总体思路和长期技术路线。随后按顺序读取：

1. `docs/research/RESEARCH_STATUS.md`
2. `docs/research/RESEARCH_DECISION_LOG.md`
3. `docs/research/CHATGPT_REVIEW_PROTOCOL.md`
4. `AGENTS.md` 和 `docs/research/phase_r/README.md`
5. `docs/research/phase_r/R22/README.md`
6. `docs/research/phase_r/R22/18_h0_false_alarm_checkpoint_20261030.md`
7. `docs/research/phase_r/R22/04_statistical_analysis_plan.md` 与 `05_resource_and_execution_plan.md`
8. `docs/research/phase_r/R22/evidence/h0_final_20261031/` 下的 README、analysis manifest、matrix audit summary、per-seed 表、条件/配对汇总、杂波噪声审计和清理摘要
9. `docs/research/phase_r/R22/evidence/h0_checkpoint_20261030/` 下的历史 29/60 检查点说明、interrupted attempt archive index 和三份中断 ZIP；将其作为恢复/清理审计，不作为最终统计结果
10. 检查执行源码 commit `a688ca52616cc380b07810d4f8fbd79ed73d46bc` 中的 matrix runner、相关 Stage2/Production runner 和评价代码；本次用于生成 seed-clustered 汇总的分析脚本 `scripts/phase_r/analyze_r22_h0_seed_clusters.py` 随审查快照提供，请核对其统计实现。报告中的 Stage2 与 Production binary SHA 作为 CUDA 执行身份核对依据

若无法实际读取研究总纲或固定快照中的文件，请列出缺失项并将对应结论标为 `NOT_VERIFIED`。不得以本提示词中的摘要代替读取。若远端 HEAD 已变化，应区分它与固定审查 commit。

## 固定实验范围与完成状态

本阶段是冻结的**无目标 H0 false-alarm-side screening**：30 个独立 seed（1935001–1935030），每 seed 配对 0 ns 与 +4 ns，共 60 个唯一 case。矩阵最终为 `COMPLETED`，60/60 case 都有终态 compact archive；analysis manifest 为 `COMPLETE_EVALUABLE`。Matrix 保留 63 条 attempt 历史，其中包含 3 次中断 attempt 的独立归档；中断 case 通过创建新 attempt 恢复，不是在单个 Production branch 内断点续算。

源码执行身份：`a688ca52616cc380b07810d4f8fbd79ed73d46bc`。执行为本机 Native CUDA，`USE_COREX=false`。Stage2 binary SHA-256：`47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`。Production binary SHA-256：`d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`。

最终分析按每 seed `observed_cut_hit_count / valid_cut_count` 计算经验 CUT-hit fraction，以 seed 为推断单位；CUT、距离单元、Doppler 单元和处理周期不视为独立样本。Bootstrap 为 10,000 次、NumPy PCG64、seed 22029；配对时延差按同一个 seed 联合重抽样。

- Per-seed 表：1,800 行。
- 方法/条件汇总：60/72 可评价；其余 12 项是设计定义的 0 ns 不适用条件。
- 配对汇总：24/36 可评价；其余 12 项是 0 ns 不适用条件。
- 这是探索性筛选，不是确认性多重比较；区间不代表 CUT 级独立二项区间。
- 报告中的示例配对差异（+4 ns 减 0 ns）：C0 dynamic in-band `+8.648e-6`（95% seed-bootstrap CI `[+7.194e-6,+1.011e-5]`），C0 full-band `-6.745e-6`（`[-7.554e-6,-5.987e-6]`）；PK Known→Current dynamic `-3.161e-8`（`[-1.835e-7,+1.264e-7]`）；C4 Mode-B dynamic `+1.650e-6`（`[+2.086e-7,+3.085e-6]`），full-band `-2.529e-8`（`[-2.402e-7,+2.655e-7]`）。不同冻结 CUT 区域方向不一致。这些只描述经验 CUT-hit fraction，不能称作系统 Pfa 或 GMTI 性能改善。

## 面积杂波、噪声和可比性

60 个 case 的 `A0_ON` Stage2 都通过 continuous-area clutter gate；每个 case 有 1,300 个连续面积杂波 packets、6,508,782 个 samples。平台高度 6,000 m，slant range 84.5–85.5 km，ground range 84.286713–85.289214 km。30 个同 seed 的 0/+4 ns 配对在面积功率、几何、相关性、power CV、噪声和注入计数等审计字段上全部一致。

- 实测面积功率范围：`0.0221935448633–0.023308819045`。
- 实测噪声功率范围：`0.000999645705034–0.00100025854155`。
- `A0_OFF`/`A0_TO` 是关闭面积杂波的对照 variant，其注入计数为零符合设计；验证杂波存在应检查 `A0_ON`，不能拿关闭对照否定它。

## 证据可访问性和限制

GitHub 提交包含 per-seed CSV、统计汇总、30 对 Stage2 杂波/噪声配对审计、矩阵与 archive SHA 索引、分析/运行身份、cleanup summary，以及三份已清理中断 attempt 的小型审查 ZIP 和索引。请核验中断档案的索引与 ZIP 可读取性。完整 60 个已完成 case compact ZIP、raw IQ 和全部成功分支的完整运行日志仍保留在本机 `outputs/`，没有上传到 GitHub。你不能声称已读取、CRC 复核、逐文件 SHA 复核或重放这些未上传的 ZIP/raw IQ；其内部内容和原始信号级结论须标为 `NOT_VERIFIED`。

历史检查点 2026-10-30 的 29/60 状态是当时快照；最终状态以 2026-10-31 报告、analysis manifest 和 matrix audit summary 为准。

同一研究阶段中的 R22 target-present screening 仍是 `GATE_FAILED / INCOMPLETE`：seed1932001、2.5 m/s、30 dB、+4 ns 的 C4 Mode-B ON robust reference support=7，小于冻结的 8。H0 无目标结果不解除该失败门，也不代表目标检测或估计收益。正式 confirmatory、dense、bridge 和 AI 训练均未启动。

## 请完成双层独立审查

### 第一层：H0 执行、统计和证据正确性

1. 检查分析是否以 seed 为推断单位、配对 bootstrap 是否保留同 seed 配对，12 个零时延不适用项是否被正确处理。
2. 核对 60 个唯一 case、63 条 attempt 历史、30 对 0/+4 ns、analysis manifest、per-seed CSV、汇总表和 matrix audit summary 是否互相一致。
3. 核对 `A0_ON` 连续面积杂波是否真实生成，0/+4 ns 配对的场景、杂波、噪声和关键统计量是否可比；区分 `A0_OFF/A0_TO` 对照。
4. 检查是否把经验 CUT-hit fraction 误称为系统 Pfa、把 H0 误作 Pd/目标任务证据，或从筛选性结果夸大显著性。
5. 分开判定 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE` 和 `PERFORMANCE_VERIFIED`；本 H0 的 GMTI target-task `PERFORMANCE_VERIFIED` 为 false。
6. 明确区分可从 GitHub 直接核验的材料与未上传 ZIP/raw IQ 所导致的 `NOT_VERIFIED`。
7. 判断 case-level retry 和 raw cleanup 是否保留了足够的中断审计证据；注意这次实现不支持 Production branch 内部续算。

### 第二层：总体 GMTI 科研价值与方向

1. H0 结果给研究总纲中的目标提供了什么证据？它对未知系统误差的虚警侧影响有何信息，哪些问题仍只能由 target-present 任务数据回答？
2. 结果是否具有跨 CUT 区域一致的方向？是否有必要将其解释为工程/筛选证据，而非算法排名？
3. 它是否改变 R22 target-present screening 的 `GATE_FAILED` 处理，或足以重开目标性能比较？
4. 对继续研究未知系统误差、改做低速目标保护、复杂杂波建模及 AI 必要性，分别给出 `SUPPORTED`、`NOT_SUPPORTED` 或 `NOT_VERIFIED`，并列出证据边界。
5. 提出**一个**最小、证据驱动的下一步建议，并给出明确停止条件。不得自动启动实验、训练 AI 或修改研究总纲。

请先给简短裁决，再列出可核验依据、限制和唯一下一步建议。阶段矩阵完成不等于长期研究路线已得到验证；如果认为研究总纲需要修订，请先提出有证据支持的修改意见供用户讨论，未经用户明确批准不得修改总纲。
