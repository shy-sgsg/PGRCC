# PGRCC R22 V3.2 Screening 独立科研审查请求

**固定审查 commit：`REVIEW_COMMIT_SHA_PENDING`**<br>
仓库：`https://github.com/shy-sgsg/PGRCC`<br>
分支：`codex/unknown-system-error-next-stage`

请先读取上述固定 commit 中的完整文件：

`docs/research/RESEARCH_MASTER_PLAN.md`

完整理解 PGRCC 的研究主题、总体目标、核心科学问题、研究内容、总体研究思路和长期技术路线。不能只看 R22 局部任务或只评价矩阵是否跑完。

然后按顺序读取：

1. `docs/research/RESEARCH_STATUS.md`
2. `docs/research/RESEARCH_DECISION_LOG.md`
3. `docs/research/CHATGPT_REVIEW_PROTOCOL.md`
4. `docs/research/phase_r/R22/27_v3_2_screening_execution_report_20261109.md`
5. `docs/research/phase_r/R22/28_v3_2_preliminary_direction_assessment_20261109.md`
6. `docs/research/phase_r/R22/evidence/v3_2_screening_20261106/README.md` 与其中的 `review_manifest.json`
7. 本证据包中的检测、配对恢复、参数、CSI、航迹和方法缺口 CSV
8. 如本次审查接口能读取，再读取固定 commit 中的 V3.2 design、方法配置、runner/evaluator 源码及归档 SHA 索引。

## 固定身份与审查限制

- 实验源码 commit：`74bccce61f3ec2f74746512d67271c815ce6a3a8`
- 研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`
- V3.2 design raw SHA-256：`9fcc56e27c3a78a96ccfbecdf6ef1b3098fb1298d4bac9886058ea9af6081063`
- Production binary SHA-256：`d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`
- Stage2 binary SHA-256：`47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`
- 执行环境：Native CUDA，NVIDIA GeForce RTX 3050 Laptop GPU，driver 580.178.04；未使用 CoreX。
- 完整 288 个 case ZIP、逐 beam 日志和运行时 XML 保存在本机 outputs，不包含于 GitHub 精简包。若审查时无法读取这些本机归档，必须标记为 `NOT_VERIFIED`，不得声称已独立核验原始日志或完整 Production 归档。
- P1/P2 估计器使用配对 target-free OFF 观测；manifest 标明 `truth_used_in_estimator=false`。这不是目标存在时部署可得观测的验证。
- 本轮状态为 `IMPLEMENTED/EXECUTED/EVALUABLE`（方法级有缺口）且 `PERFORMANCE_VERIFIED=false`。

## 两层审查任务

### 第一层：本阶段是否正确执行和评价？

请独立核对：

1. 12-seed、288-case、4,320 个适用 ON 方法单元的分母是否与冻结设计一致；失败方法是否保留为不可评价，而没有改写成漏检或静默 fallback。
2. Stage2 连续面积杂波注入、功率、lag-1 相关、ON/OFF/TO 配对和跨方法输入公平性是否足以支持当前因果解释。
3. C0/A0/PK 的对照和 P1/P2 的 Known−Current、Estimated−Current recovery 公式是否正确，尤其 P2 Mode-A 必须使用 C4 Mode-A / PKR Mode-A / P2 Mode-A 配对。
4. 12 个 seed 是否被正确视为独立推断单位；目标机会、CUT、周期或方法行是否被错误当作独立重复。
5. P2 Mode-B 仅 1/12 个 seed 有完整 triplet、以及 49 个 robust-support 缺口，是否充分限制了方法覆盖率和比较范围。
6. 参数 RMSE 是否只是 own-success RMSE；缺失估计是否被错误按零处理；径向速度是否实际可评价。
7. CSI `CA_ROI_dB`、OFF false-CUT fraction、track 计数是否被正确限界，是否有证据支持目标专属幅相保持、经验系统 Pfa 或最终杂波抑制结论。
8. Native CUDA、实际源码/二进制身份、设计 SHA、归档校验与 raw 清理记录是否闭合；区分已读取证据与因 GitHub 未含本机 ZIP 而无法核验的内容。

### 第二层：阶段成果是否推动总体 GMTI 研究目标？

请独立判断：

1. +4 ns 未知时延对本次 GMTI 检测任务的影响，能否从结果中得到合理但有限的因果支持？
2. Known correction 恢复量和 A0/PK 物理闭合证据是否充分？
3. P1/P2 与 Known 的差距是否能证明传统估计器足够，还是 OFF 参考限制使结论只能作为诊断上限？
4. 当前证据支持继续研究未知物理状态估计吗？下一阶段最关键的可部署信息条件是什么？
5. 有何证据支持或反对把主线改为低速目标保护或复杂非平稳杂波？
6. AI 是否已有必要性证据？强确定性方案是否已在同等可用信息条件下比较？
7. 本轮报告的下一步是否过宽？请提出一个有证据支持、范围最小、含评价标准和停止条件的建议。

请分别给出：阶段执行裁决（`PASS / PASS_WITH_LIMITATIONS / FAIL`）、科研方向裁决（`CONTINUE / STOP / PIVOT`）、AI 进入门裁决，并逐项区分 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED`。不得把筛选结果写成统计显著结论。

如果建议修改研究总纲，请先列出证据、拟修改范围及对既有成果的影响，与用户讨论。在用户对具体文本或清晰范围明确批准前，不得修改 `RESEARCH_MASTER_PLAN.md`。
