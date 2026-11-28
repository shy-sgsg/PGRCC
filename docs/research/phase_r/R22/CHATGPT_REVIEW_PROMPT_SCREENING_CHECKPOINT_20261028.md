# R22 V2 Screening Checkpoint：ChatGPT 独立审查提示词

**请首先读取本次 Git 提交对应版本的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路与长期技术路线，然后结合这些内容审查本阶段的代码、实验、数据和科研结论。不能只评价阶段任务是否完成，还必须判断本阶段是否推动了整个 GMTI 研究目标，以及当前研究方向是否需要调整。**

## 审查快照

- GitHub 仓库：<https://github.com/shy-sgsg/PGRCC>
- 分支：`codex/unknown-system-error-next-stage`
- 审查快照 commit：`95a8854b01b9af26e6e955d0871e130db81e2f6e`；请以该版本的研究总纲、状态、决策日志和阶段报告为准，不要用下方的实验执行源码 commit 替代。
- 本次 CUDA 运行的主要源码身份：`5a60a514cf53cab9f23b17ab29992fd4485876fa`。逐 case manifest 还显示首个 0 ns screening case 使用 `7e5a7ee8ebd6c460622425e3d05831b25603e7ec`；Stage2/Production binary 与冻结方法配置一致。两提交间相关代码差异是 truth-blind delay estimator 从整幅批处理改为 packet streaming。该首个 0 ns case 只执行估计诊断、不应用物理校正；其估计诊断不得在不标明源码版本时与 streaming 结果合并。详见 `05_resource_and_execution_plan.md`。
- attempt004 Stage2 binary SHA-256：`47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`
- attempt004 Production binary SHA-256：`d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`
- attempt004 的 `source_identity` 显示执行时 tracked worktree 有治理/研究文档改动；runner 同时对参与的 7 个执行脚本记录逐文件 SHA。请区分 dirty documentation 与算法/仿真代码身份，以 manifest 的逐文件 SHA 为准。续跑的每个 case 也必须分别核对其 `source_identity`。
- 研究总纲 Git blob：`e637e3769bde7814298ff012d0f42301884e2985`
- 冻结设计：`R22_EXTENSION_20261028_V2_RANGE_ROI`，读取 `docs/research/phase_r/R22/config/r22_extension_experiment_design_v2_range_roi.json` 与相邻方法配置，并用矩阵 manifest 中的 `design_sha256` 核对身份。
- 执行路径：本机 Native CUDA；`USE_COREX=false`。Stage2 与 Production binary SHA、设备状态、实际分支数以各 attempt manifest 和 runtime 记录为准。

本提示词对应**阶段性 checkpoint 审查**，不是完整 R22 研究结论。提交审查时，请同时附上本地 `outputs/r22_full_v2_compact/` 中的矩阵 manifest、运行日志和相关 case ZIP。`outputs/` 未必在 GitHub 中；若附件缺失或无法读取，列出具体文件并将依赖结论标为 `NOT_VERIFIED`。不要声称精简 ZIP 能重新运行完整 raw IQ。

## 历史执行快照：2026-10-28 11:16 UTC（已被后续 gate failure 更新）

- 冻结 screening 计划 288 个唯一 case；截至此快照 17 个 `PASSED`、1 个 `RUNNING`，4 条 `INTERRUPTED` 为历史 attempt，不计为新的唯一 case。活动 case 为 seed1932001、2.5 m/s、30 dB、+4 ns。提交审查前应从 matrix manifest 读取最新状态，不沿用此快照作为当前状态。
- 17 个通过 case 使用相同 Stage2 SHA-256 `47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`、Production SHA-256 `d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`，Native CUDA，`USE_COREX=false`，area gate 通过。它们目前仅来自一个 seed；不得作跨 seed 推断或显著性判断。
- 首个 0 ns case 的估计器源码比其他已通过 case 早一个提交。实现对照测试 `tests/test_delay_stage1_core.py::test_streaming_delay_suite_matches_batch_methods_and_metadata` 对状态和计数精确比较、对 delay/phase residual 使用 `rel=1e-9, abs=1e-10`。请审查这种差异是否只影响估计诊断，及其对 screening 纳入/分层的影响；不要把合成软件等价测试称为 CUDA 性能证据。

## 最新 gate failure：2026-10-28 11:34:54 UTC

- Matrix manifest 终态为 `GATE_FAILED`：288 个 screening unique cases 中 17 个 `PASSED`、1 个 `GATE_FAILED`，另有 4 条历史中断 attempt；无活动 case。runner 以退出码 2 停止，没有重试或继续其他条件。
- 失败 case：`screening__seed_1932001__vr_2d5__snr_30__delay_4ns__targets_20__periods_10`。Stage2 面积杂波 gate 通过（1,300 packets / 6,508,782 samples），四个 causal triplets 均通过，blind estimator `truth_used_in_estimator=false`、没有 fallback。C4 Mode-B ON 的 Production return/status 为 passed；失败发生在该方法的 reference/evaluation gate。
- `robust_ddc_rb` reference 共 1,560 组；1,559 组 `OK`。period 5 / GMTI05 / beam 50 / `az_52_range_1065_1197` 这一组 support=7，小于冻结 `min_support=8`，phase coherence=`0.11720003232345`，reason=`insufficient_robust_support`。适配器为 `PARTIAL`（155/156 groups）；reference validator 对非 `OK` 组 fail closed，C4 Mode-B ON 及依赖该 reference 的 OFF/TO rows 变为 `NOT_EVALUABLE`。case 有 54 task rows、17/18 ON evaluator rows、6,800 target-period opportunities、0 invalid truth opportunities、0 independent H0 rows；整体 `EVALUABLE=false`、`PERFORMANCE_VERIFIED=false`，有效 CUT / unique-hit union gate 未闭合。经验 Pfa 仍不可评价。
- 同 seed、速度、时延和目标轨迹的 SNR20 配对 case 在相同组 support=25；SNR30 为 7。SNR20/SNR30 的面积散射体 SHA 均为 `d309e99080e154f13620de7fc1fb8eef64ca561f25e81d2bce6ac3a675e4f0d7`，背景 generation-stat SHA 均为 `f84a2949f5c7d7e1dc666a818e079c6bc03ff59170f81edc7b088989c6b38727`，200 个 target-period 对齐后的目标状态/几何字段（忽略 case label 和 SNR）逐项一致。period 5 的 `R22_T010` 位于 range bin 1175；target-free OFF 同组 support=55。证据与高 SNR 目标污染 ON 侧 blind reference 相符，但只是一 seed 一 case，不能声称存在通用 SNR 临界值。
- 失败审查 ZIP：`outputs/r22_full_v2_compact/failures/screening/screening__seed_1932001__vr_2d5__snr_30__delay_4ns__targets_20__periods_10__GATE_FAILED_attempt_001.zip`，SHA-256 `be76237104d6c6f11f1681de556e49ecad5e694658e8d5c94ce3e7c05850890b`。ZIP CRC 与纳入文件 SHA/size 已由归档器核验；raw case directory 仍保留。审查时同时附上最新 matrix manifest、matrix runner log、失败 ZIP、SNR20 配对 ZIP 和 `05_resource_and_execution_plan.md`。
- 请重点判断：这是应当保留为 C4 Mode-B 高 SNR 的方法能力边界并停止本轮，还是需要先提出保持 `min_support=8` 的目标保护/分组 amendment，再冻结新版本、补工程 gate 后重开受影响矩阵？不得建议静默降低门槛、跳过失败行或把其他方法结果伪装为完整 case。也请判断这一单例是否改变 GMTI 研究优先级；不得据此自动转向 AI 训练。
- 用户规定的 20 GB 是 ChatGPT 审查和结果汇总之后的最终清理上限；当前中间数据与失败 raw case 保留到审查完成。

## 必须读取的顺序

1. 指定审查 commit 中的 `docs/research/RESEARCH_MASTER_PLAN.md`，并确认其 blob 与上列 SHA 相同。
2. `docs/research/RESEARCH_STATUS.md`、`docs/research/RESEARCH_DECISION_LOG.md`、`docs/research/CODEX_TASK_PROTOCOL.md`、`docs/research/CHATGPT_REVIEW_PROTOCOL.md`、`AGENTS.md`、`README.md`、`docs/research/phase_r/README.md`。
3. `docs/research/phase_r/R22/00_master_execution_plan.md`、`01_experiment_design.md`、`02_screening_experiment.md`、`05_resource_and_execution_plan.md`、`06_scientific_decision.md`、`07_execution_and_quality_audit.md`、`09_extension_execution_contract.md`、`12_v2_range_support_domain_amendment_20261028.md`、`15_v2_six_seed_engineering_results_20261028.md`。
4. `docs/research/phase_r/R22/config/r22_extension_experiment_design_v2_range_roi.json`、`r22_method_config_v2_range_roi.json`，以及 `scripts/phase_r/r22_experiment_matrix.py`、`run_r22_cuda_pilot.py`、`r22_compact_archive.py` 和 commit `5a60a514…` 中与流式 blind-delay 计算有关的源码/测试。
5. 附件中的两个 matrix manifest：
   - `outputs/r22_full_v2_compact/matrices/screening/matrix_manifest.json`
   - `outputs/r22_full_v2_compact/matrices/screening_diskquota_retry/matrix_manifest.json`
6. 对照 attempt 清单、每个 compact ZIP 的 `ARCHIVE_MANIFEST.json`、ZIP CRC、SHA-256、case manifest extract、Stage2 报告、运行时 XML、Production branch manifest/log、CSI tap、CFAR geometry、参数/检测/航迹结果与 `raw_array_sha256_before_cleanup.json`。

`RESEARCH_STATUS.md`、Phase R 导航和阶段报告可能比本地矩阵 checkpoint 落后。请以指定 commit 的文件为审查快照，再与随附的 machine manifest 和带时间戳执行记录逐项核对；若有冲突，明确列出来源与日期，不可把较旧状态描述成最新运行结果。

## 当前实验边界

V2 screening 冻结为 288 个 case；完整设计共 594 个 case，含工程、screening、confirmatory、dense、bridge 与 H0 阶段。每个 case 仍使用冻结的 seed、目标数、10 个连续 period、方法集合与质量门。不得因本次失败而减少样本、目标、周期、方法或降低门槛。

seed1932001、0.5 m/s、10 dB、20 targets、10 periods 的 0 ns case 已通过。配对 +4 ns case 的 attempt001 为 OOM、attempt002 记录 `[Errno 122] Disk quota exceeded`、attempt003 在 evaluator 前被操作中断；三者均须保留失败/中断身份。attempt004 已由矩阵记为 `PASSED`：54/54 method/mode/role branch pass，`IMPLEMENTED/EXECUTED/EVALUABLE=true`、`PERFORMANCE_VERIFIED=false`；18 条 ON evaluator rows、54 task rows、7,200 target-period opportunity rows、0 invalid truth opportunities、0 independent H0 rows。面积门与四组 causal triplet 均通过，empirical Pfa 仍 `NOT_EVALUABLE`。压缩包为 `outputs/r22_full_v2_compact/case_archives/screening/screening__seed_1932001__vr_0d5__snr_10__delay_4ns__targets_20__periods_10__attempt_004.zip`，5,775,941 bytes，SHA-256 `cde0e8d0b4521cd1e856c217a6b40606ad0bb313dc59d18df176cc1237756cdf`；ZIP CRC、矩阵 SHA 与 2,356 个归档文件的逐文件 SHA 均已核验，raw 已按规则移除。该配对仅为单 seed 可行性证据，不能作为统计性能证据。完整 screening 已恢复；请以随附最新 matrix manifest 判断当前在跑的 case 和总进度。

截至 11:16 UTC，screening runner 已恢复并仍在运行；该时间点之后，它于 11:34:54 UTC 因本文件“最新 gate failure”所述的 C4 support gate 以退出码 2 停止。因此，下面关于“完整 screening 已恢复”的历史记录不得视为当前运行状态；审查应以最新 gate failure 和随附 matrix manifest 为准。此前完成了 3 项有范围的内存/恢复改动：因果闭合审计改为逐 packet 流式处理；matrix runner 在 case 开始前持久化 checkpoint，并从 manifest 保留 attempt 序号；blind delay methods 改用 packet streaming，保持原方法口径。相关定向测试曾报告 `135 passed`，另有合成资源检查；这些软件/CPU 检查不替代本次 Native CUDA Production 证据。

attempt_003 曾在 evaluator 中断。清理前对 1,841 个 `.bin/.npy` 文件保存了完整 SHA-256 清单；随后按既有 cleanup policy 记录清理 11,319,001,604 bytes 的可重建 `.bin` 和 CSI map，生成失败 ZIP。它必须按 `INTERRUPTED` 审查，不能混入成功结果。用户已澄清：20 GB 是最终清理后的项目上限，中间运行目录可暂时超过；最终清理应在结果汇总和审查材料齐全、ChatGPT 审查完成后按台账执行。

## 第一层：执行与证据正确性

1. 核对两个 manifest 的设计 SHA、case attempt 顺序、0 ns 通过 ZIP、各失败/中断 ZIP、attempt_004 终态与 runner 日志。检查 attempt_003 的 interruption marker、array SHA 清单、cleanup ledger 和 ZIP 内清单是否闭合。
2. 审查 A0_ON 的 Stage2 是否真实产生连续面积地杂波；核验 packets、samples、功率、有效距离窗、F1/F2 输入、与配对 case 的杂波背景复用关系。不能只看 `area_enabled`。
3. 检查 causal triplet、blind estimator 的输入隔离和 truth 使用、Known correction evaluator-only 边界、Production runtime XML、逐分支运行身份、CSI/CFAR geometry、beam gate、目标机会与评价分母。
4. 核验实际 Production 分支是否全通过，attempt_004 是否生成完整任务记录，失败分支和缺失指标是否如实标记。验证 archive hashes 与压缩清单，但不要声称省略的 raw `.bin/.npy` 可由 ZIP 完整重放。
5. 区分 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED`。该单 seed 配对 case 即使成功，也不能作为 Pd/Pfa 的统计验证。
6. 审查 `Disk quota exceeded` 的具体证据与存储策略：原始 attempt 位于 7.5 GB `/tmp`；后续尝试转到项目本地临时根。区分 tmpfs 限额、用户/文件系统配额和最终项目清理上限；没有证据的根因不要写成已确认。

## 第二层：对 GMTI 总体研究价值的判断

1. 本 checkpoint 对总纲的“传统方法能力边界”和“瓶颈识别与机制归因”具体补充了什么？
2. 目前只有一个 screening seed 的 0/+4 ns 配对条件时，哪些任务结论仍为 `NOT_VERIFIED` 或 `NOT_EVALUABLE`？
3. 是否有证据比较未知时延退化、Known recovery、盲估计差距、低速目标、目标密度或复杂杂波？若没有，明确说明不能据此选择长期研究方向。
4. 现阶段是否证明 AI 必要？请按总纲中的确定性强基线、稳定任务残差、部署可用输入、无信息泄漏和独立验证门逐项判断；不得因数据规模增加就建议训练 AI。
5. 是否应继续既定 screening，或先暂停补足新的执行/资源问题？建议必须遵守冻结条件，不能用静默减规模解决资源问题。

## 输出格式

1. 审查范围：仓库、branch、完整审查 commit、总纲 blob、执行源码/二进制身份和实际读到的附件。
2. 对当前 attempt 的状态裁决：`PASS / PASS_WITH_LIMITATIONS / INCONCLUSIVE / FAIL`，并列出支持证据。
3. 核心状态表：`IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED`。
4. 关键实验/存储事实：确认项、未确认项和 ZIP/manifest 路径。
5. 相对总纲的科研价值与当前不能支持的结论。
6. AI entry gate：`PASS / FAIL / NOT_VERIFIED` 及逐项依据。
7. 阶段方向建议：`CONTINUE / STOP / PIVOT / INCONCLUSIVE`；只给一个下一步优先任务，写明进入条件、交付物与停止条件。
8. 总纲若需要修改，只能给出证据与建议供用户讨论，未经用户明确批准不得修改。

不要仅凭软件测试通过、一个 case 成功、某个中间指标改善或文档计划完成作出性能结论。未达到冻结的独立 seed/样本规模时，必须明确保留 `PERFORMANCE_VERIFIED=false`。
