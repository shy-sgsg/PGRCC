# PGRCC R22-S1b 处理阶段闭合审计：ChatGPT 独立科研审查

## 固定审查快照

**请首先读取本次 Git 提交对应版本的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解 PGRCC 的 GMTI 研究主题、总体目标、核心科学问题、研究内容、总体思路与长期技术路线；然后再结合本阶段代码、实验身份、数据和报告审查。不能只判断阶段任务是否完成，还要审查它是否推动整体研究目标、当前方向是否仍合理。**

- 仓库：<https://github.com/shy-sgsg/PGRCC>
- 分支：`codex/unknown-system-error-next-stage`
- **固定审查快照 commit：`0d53a10ef4ad10e619cd2456870040da76d600bd`**
- S1b Production 执行源码 HEAD：`1ed2a2e67a88a918858784be81794238ef112c7d`
- 研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`（本阶段提交前后完全相同）
- S1b Native CUDA Production binary SHA-256：`16cb0f2d35596ff98ab8bdcbc9571b972b99e217053cf0b1b7f6b49168c8c998`
- 生产执行：本机 NVIDIA GeForce RTX 3050 Laptop GPU、CUDA 12.4、Release、架构75。
- Stage2：复用既有输入，S1b 未重新执行 Stage2；大型 Stage2 binary 未上传。逐 period 输入路径、大小与 SHA-256 在运行 manifest 中。

如当前远端 HEAD 与上述固定 commit 不同，以此固定 commit 作为审查快照，并明确写出 HEAD 差异。

## 阅读顺序

先读固定 commit 下的：

1. `docs/research/RESEARCH_MASTER_PLAN.md`
2. `docs/research/RESEARCH_STATUS.md`
3. `docs/research/RESEARCH_DECISION_LOG.md`
4. `docs/research/CHATGPT_REVIEW_PROTOCOL.md`
5. `AGENTS.md`
6. `docs/research/phase_r/R22/00_master_execution_plan.md`
7. `docs/research/phase_r/R22/SCREENING_GATE_REVIEW.md`
8. `docs/research/phase_r/R22/support_failure_diagnostic/README.md`
9. `docs/research/phase_r/R22/support_failure_diagnostic/S1B_STAGE_BOUNDARY_AUDIT.md`
10. `docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1b/evidence_manifest.json`、`s1b_manifest.json`、`case_identity_summary.json`
11. `evidence/s1b/analysis/stage_boundary_closure.json` 与 `stage_boundary_closure.md`
12. `evidence/s1b/stage2/` 下的 A0_ON 杂波生成报告、A1_ON/OFF/TO 配置与背景复用映射
13. `evidence/s1b/production/C4_modea_S1b_{ON,OFF,TO}/` 下的 branch manifest、实际 `production.xml`、运行日志、selected cells、gamma 与 stage trace CSV
14. 实现与验证：`src/processOnePeriod.cpp`、`src/gpu/gpu_kernels.cu`、`src/runtime_diagnostics.cpp`、`include/runtime_diagnostics.hpp`、`tests/production_calibration_config_selftest.cpp`、`docs/research/phase_r/R22/support_failure_diagnostic/analyze_stage_boundary_trace.py`
15. S1 原始审查证据：`evidence/mode_comparison/snr20_modeb_on_selected_cells.csv`、`evidence/mode_comparison/snr30_modeb_on_selected_cells.csv`、SNR20/30 的 Stage2 与 Production 摘要。

如果无法真实读取研究总纲、某一配置或必要数据，请列出缺失项，并将对应方向判断标为 `NOT_VERIFIED`。不能用本提示词的概括替代读取总纲和原始证据。

## 阶段问题与已执行范围

研究问题：R22 Screening 中，C4 Robust DDC-RB 在 SNR30 的 `GMTI05 / beam 50 / azimuth row 52 / range 1065–1197` 参考组 support=7，低于冻结最低值8。S1b 检查生产链中 `ON−OFF−TO` 复数 F1/F2 闭合从哪个已采集边界开始显著恶化。

S1b 固定条件：seed1932001、2.5 m/s、SNR30、+4 ns、20 targets、10 periods、beam50；复用原 A1_ON/OFF/TO 输入；仅执行三个10周期 Native CUDA Production 分支。没有扩展 Screening，没有修改算法，没有降低门槛，没有把真值提供给 estimator。

状态和入口：
- S1b 阶段报告：`docs/research/phase_r/R22/support_failure_diagnostic/S1B_STAGE_BOUNDARY_AUDIT.md`
- 机器可读审查材料：`docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1b/`
- 本地原始 Stage2 period binaries 不在 GitHub。请勿声称已独立访问或重新运行这些 binary。

## 关键结果（请逐项核验）

1. ON/OFF/TO Production 三分支返回码均为0；每个分支的 valid-CUT denominator gate 通过，`union_dynamic_in_band` 报告 5,272,800 valid cuts。三分支 `truth_period_count=0`，因此此处不能报告 Pd、Pfa 或任务性能。
2. S1b Mode-A ON 的 C4 前 F1/F2 与原 SNR30 Mode-B 失败行在133个距离单元上逐值一致；原失败为 support7、`insufficient_robust_support`。这只确认重放输入一致，不把 Mode-A 描述成盲部署比较。
3. SNR30 A1 单角色 Stage2 报告的 `continuous_area_packets/samples=0` 表示复用背景时没有新生成杂波。A0_ON 生成共同 A0_background 的实际报告记录1300 packets、6,508,782 samples、平均功率0.0221596696662；A1 ON/OFF/TO 通过背景目录复用，三者映射各10/10为 `matched`。请检查配置和映射，不要单看 A1 的生成计数。
4. Stage2 causal triplet 在61,464,000个样本上通过5e-5容差，最大绝对误差1.7173e-5、RMS约9.36e-7。
5. CSI range-phase correction 前，`ON−OFF−TO` 相对 L2 残差为 F1=1.0316%、F2=1.8056%；校正后为 F1=1.0316%、F2=78.7703%。三个角色的 CSI range-phase 均值分别为 ON 0.05733 rad、OFF −1.76689 rad、TO 0.66254 rad。
6. C4 adapter 使用相同 `gamma=0.1437967723+0.8393605099j`，实际输出符合 `F2−gamma*F1`；各角色公式相对 L2 误差最高约1.09e-7，adapter 未制造大幅闭合残差。
7. raw 和 CSI trace 是不同处理分支。首个被采集的 DBS 边界已有约1–2%闭合残差；采集没有覆盖 pulse compression、数据驱动 Doppler center 等全部上游边界。最大已观测闭合恶化可定位到角色独立的 CSI range-phase correction 之后，但 support 25→7 尚未被唯一归因。
8. `F2·conj(F1)` 是双线性量，不能将它的 ON−OFF−TO 差值当作复数通道的线性闭合失败。
9. 当前证据没有证明目标响应覆盖 azimuth row 52；没有执行独立 H0/Pfa、检测 Pd、目标保持或速度/方位参数误差验证。
10. S1b 报告建议的唯一后续是共同 target-free OFF range-phase 向量反事实，对 SNR20/30 selected group 比较原生逐角色拟合与共同向量下的 support。该工作尚未启动，是否做由用户在本次审查后决定。

## 请完成两层审查

### 第一层：实现和证据正确性

- tap 是否只记录所选 period/beam/row/range，是否保存阶段身份与复数数据，失败时是否 fail-closed？
- 检查 raw 与 CSI 的数据流关系，验证报告没有把分叉路径串成一条链。
- 验证 Mode-B 原失败输入的逐值匹配及其解释边界。
- 核对 A0 面积杂波生成与 A1 背景复用，区分“该角色新生成计数”与“实际 Production 输入是否含背景”。
- 核对 Stage2 的 ON/OFF/TO 因果关系、Stage2 residual、CSI range-phase 变换、adapter gamma 与输出公式。
- 判断当前数据能否解释 support 25→7；区分直接观测、支持的推断、仍未验证解释。
- 检查代码、配置、自检、Runtime XML、manifest、日志与 evidence SHA；说明实际未重新运行/未上传的数据。
- 判断 S1b 的 `PASS_WITH_LIMITATIONS` 与 R22 Screening 的 `INCOMPLETE/INCONCLUSIVE` 是否分别合适。

### 第二层：对整体 GMTI 研究的价值

- 本阶段属于工程基础、物理机制证据还是任务性能证据？它对研究总纲目标有什么实质推进？
- 是否足以证明 C4 的一般性能力缺陷、目标污染因果关系或一项可发表创新？
- 对低速目标、复杂杂波、未知系统误差和 AI 方向分别作证据边界判断。
- 对共同 OFF range-phase 反事实是否合理作独立评价：它能回答什么、不能回答什么；若有更简单的同信息条件确定性诊断，只提出一个优先方案。
- 研究总纲是否仍适用？若建议修改，只列具体文本方向、证据和对既有成果的影响；不得把建议视为用户批准或修改总纲。

## 输出格式

1. `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED` 状态表。
2. 阶段执行裁决 `PASS / PASS_WITH_LIMITATIONS / FAIL` 与整体科研状态分开。
3. 直接观测、支持的推断、`NOT_VERIFIED` 事项。
4. 对总体目标贡献及 `CONTINUE / STOP / PIVOT / INCONCLUSIVE` 建议。
5. 只给一个优先下一步，并列出最小输入、要观测的量、进入条件和停止条件；不要自动启动。
6. 明确报告无法读取的文件、未上传数据、固定快照与远端 HEAD 差异。

不得将单 seed 诊断描述为统计显著性或 Pd/Pfa 改善。不得因 C4 在一个场景 support 不足而直接转向 AI。研究总纲正文未经用户明确批准不得修改。
