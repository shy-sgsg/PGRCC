# R20 ChatGPT 独立科研审查请求

此请求审查冻结的 R20 内容提交。提示词文件作为后续提交加入，以便明确写入内容提交的完整 SHA；不得用当前移动的 branch HEAD 替代下面的审查快照。

```text
【PGRCC 科研阶段独立审查请求｜R20】

请首先读取下列审查 commit 中的完整文件：

docs/research/RESEARCH_MASTER_PLAN.md

完整理解 PGRCC 的 GMTI 研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路和长期技术路线。不得只读阶段报告、不得用本提示词的概括代替真实读取，也不得把最近阶段结果当作长期目标。

仓库：https://github.com/shy-sgsg/PGRCC
分支：codex/unknown-system-error-next-stage
R20 内容审查 commit：866d49fd7943bebd581a4a74bf9751e6f1b84ece
审查总纲：该 commit 的 docs/research/RESEARCH_MASTER_PLAN.md
预期总纲 Git blob：e637e3769bde7814298ff012d0f42301884e2985
阶段：R20，R19 evaluator 修正、Doppler truth mapping 验证、R18 回归、R8/R12 saved-data 统一复评

代码与证据身份：
- `scripts/phase_r/r20_task_evaluator.py` SHA-256：`0a5400c9e2d693f28da9948927ba4f0ede7d3f3eedfc74b5d06992f03aabf9b1`
- `scripts/phase_r/r20_historical_evidence_adapter.py` SHA-256：`1043a83ca28d4f9ac14d15d50fa778c80d3cc1b3d6bd5cb9a371f444061e50a4`
- `scripts/phase_r/r20_harmonized_task_review.py` SHA-256：`198ab502ad11e055c1d3803ea14dde4a02934a2199573140b305013f99445f3a`
- `tests/test_r20_task_evaluator.py` SHA-256：`278e862431e9fdc0ebea4a8ffd60286129e75d41279b620c7c583775ad4b17d9`
- 可离线复评的输入包：`docs/research/phase_r/R20/evidence/inputs/`；566 个逐文件校验输入，另记录 42 个省略的完全相同 Detection 副本；不含 raw IQ。
- 结果与运行身份：`docs/research/phase_r/R20/evidence/results/`、`evidence/manifests/r20_input_identity_manifest.json`、`evidence/audits/r20_offline_validation.log`。

若你无法读取指定 commit 或其总纲：明确列出获取失败的路径，所有整体科研方向判断标 `NOT_VERIFIED`；不能用当前 HEAD、Codex 摘要或此 prompt 的描述冒充读取。

## 必读顺序

1. 上述内容 commit 中的 `docs/research/RESEARCH_MASTER_PLAN.md`。
2. 同一 commit 的 `docs/research/RESEARCH_STATUS.md`、`docs/research/RESEARCH_DECISION_LOG.md`、`docs/research/CHATGPT_REVIEW_PROTOCOL.md` 和 `AGENTS.md`。
3. `docs/research/phase_r/R20/00_master_execution_plan.md`，随后 01–10 全部报告。审查快照中的 `11_ChatGPT_review_request.md` 是早先版本，内部 pin 指向更早的 R20 快照；该旧 pin 已被本次完整提示词中的审查 SHA 取代。以本次完整提示词中的 SHA 和本文件当前版本为准，不要沿用旧版 prompt 的快照号。
4. `docs/research/phase_r/R20/evidence/manifests/r20_input_identity_manifest.json`，以及与下述判断相关的逐机会、回归、Doppler、identity、parameter、track、H0 和 Known closure CSV。
5. `scripts/phase_r/r20_task_evaluator.py`、`r20_historical_evidence_adapter.py`、`r20_harmonized_task_review.py`、R20 tests，以及只读的 R19 evaluator/契约和 R18/R8/R12 对应原报告。

若当前 branch HEAD 与审查 commit 不同，请分别报告两者 SHA；R20 实验与评价结论一律以审查 commit 为准。R20 内容 commit 是上面明确给定的 `866d49fd7943bebd581a4a74bf9751e6f1b84ece`。当前提示词的修订版在该内容提交之后单独提交；该后续提交只固定审查入口和 SHA，不替代审查 commit 中的代码、结果或证据。

## R20 范围与状态

R20 仅离线读取保存数据。没有重新运行 CUDA/Production、没有生成 IQ、没有改信号处理算法或实验配置、没有训练 AI、没有启动 R21。R19 evaluator/历史结果保持不变；新增独立 `r20-task-eval-v2`。

阶段资产声称：12 个 R18 场景、3 个 R8 seed（其中 seed101 因缺保存 Production Doppler axis 而不可评价）、R12 seed404 五条件；210 个分支记录、196 个 ON/TO evaluations、16 个 identity groups 均为 `IDENTITY_VALID`。请从 CSV/manifest 独立核验这些数字和适用范围。

## 第一层：阶段任务与实现正确性

1. **R20 evaluator：** 检查 `result_id → exact Detection period` 的 TrackManager 映射是否正确；UTC float、event period 字段是否只作审计而不误导匹配；未映射 ID 是否 fail closed。
2. **目标和参数分母：** visible 与 invalid truth 是否分开；invalid truth 是否只从有效机会/漏检分母排除并保留审计；各参数的 missing reason、own-success 和 common-success RMSE 是否定义正确。
3. **Detection range-bin：** 缺失字段时 `col` fallback 与 present-invalid 字段是否严格分开；present-invalid 是否不能通过旧 evaluator 的 `col` fallback 匹配；计数是否同步。
4. **identity schema：** 必需字段缺失和固定字段不一致是否能拒绝比较；expected intervention fields 是否保留为可变值；是否有声称身份有效但 raw input SHA 实际未 rehash 的误导。
5. **TrackManager：** state observations、unique periods、simultaneous target tracks、sequential disjoint-ID handoffs、payload duplicate 是否严格分开；指标是否从真实 Production lineage 得出。
6. **Doppler truth mapping：** 是否从物理 truth 运动/几何频率分量、PRF folding、实际 Production 轴起点/有符号间距/行数/方向独立计算；`row_truth` 是否没有被直接当作 CFAR row；PRF/axis closure 是否可核。
7. **正控制：** seed606、6.7 m/s、30 dB 的 TO 是否在原冻结 ±2 row gate 中 3/3；A0 ON 的轴/真值是否 3/3 可闭合，且 2/3 的 Detection 结果是否正确仍作为任务 miss 解释；有没有偷偷放宽 gate。
8. **R19 v1/v2 回归：** 84 个 R18 ON 条件的 hits/opportunities/misses/fractions 是否一致；14 个 post-confirmation match rate 的 `1.0→0.5` 修正能否从 exact period join 和保存数据复核；是否保持 R19 历史输出不变。
9. **R8/R12 身份与缺失数据：** binary/config/truth/background/condition/roles 身份是否充分；input SHA 是声明还是被实际验证必须分开；raw IQ 不在时，Known closure 是否明确为历史 manifest 值而非新重算；R8 seed101 是否确实因 axis 不足被拒绝。
10. **结果口径：** H0 fraction 是否只是 descriptive；无合法 H0 CUT 分母时 statistical Pfa 是否保持 `NOT_EVALUABLE`；radial velocity ambiguity、TrackManager association、target parameter RMSE 是否按实际字段可用性报告；单 seed 三个 periods 是否没有被当成独立统计样本。
11. 检查报告、脚本、测试、manifest 和 CSV 是否一致；寻找公式/单位/符号/排序/行数、来源引用或指标字段错误。测试通过不能作为 GMTI 性能证据。

## 第二层：总体研究价值与方向判断

请独立依据你实际读取的研究总纲判断：

1. R20 是否推动了总纲里的 GMTI 科学目标？它回答了什么问题、没有回答什么问题？
2. 对 +4 ns 失配的任务损失及 Known correction 回收量，现有可评价证据有多强？R8 seed101 缺轴、R8/R12 raw IQ 缺失、每 seed 三个 target opportunities、无统计 Pfa 如何限制结论？
3. R12 M1 与固定 D3 的 detection hit 优势是否被其输入 closure、common-success parameter RMSE 和 track/ground-speed 表现抵消或限制？是否能从一个 seed 推广？
4. R20 建议的“用户审查后，再考虑两个独立 seed 的窄 M1 验证”是不是最小且有价值的下一步？是否应继续未知物理状态 characterization、停止窄静态 delay estimator 作为主创新，或转回公平传统 GMTI baseline？给出你的独立 `CONTINUE / STOP / PIVOT` 和证据。
5. 当前证据是否满足 AI 入口条件，即真实可观测物理误差的确定性估计存在可重复、任务相关的稳定残差？不能仅因 M1 与 Known 有差异就推断需要 AI。
6. 低速目标保护或复杂杂波是否在 R20 数据中被实际操纵并公平比较？若没有，不能把其排除或支持写成实验证明。
7. 研究总纲的科学问题/路线是否仍符合这批证据？如认为需要修改，只提出具体段落、证据、替代文本、对既有成果影响和优先级；不能把建议当作用户批准。

## 请按以下结构提交审查结论

1. **阶段任务判断：** `PASS / PASS_WITH_LIMITATIONS / FAIL`，附可核验依据。
2. **总体方向建议：** `CONTINUE / STOP / PIVOT`，并说明 R20 对既有 R12 决策产生或未产生哪些变化。
3. **核心数值复核：** R19 14/84 track-rate 修正、TO 3/3 正控、A0 2/3、R8 seed202/303 三条件、R12 五条件与 delay residual/参数差距。
4. **不可评价项与影响：** seed101 axis、raw IQ、Known closure re-compute、统计 Pfa、radial velocity、真实硬件泛化。
5. **逐项回答：** 未知通道误差是否降低 GMTI；Known correction 恢复多少；传统估计离 Known 多远；是否需要未知物理状态估计；是否调整为低速目标或复杂杂波；是否有 AI 必要。
6. **唯一下一步：** 最小可证伪任务、所需数据/身份、成功与停止条件；不自动启动实验。
7. **审查可追溯性：** 实际读取的 commit、总纲 blob、文件清单、未能读取的证据和因此受限的判断。

禁止把 `IDENTITY_VALID` 解释成 raw IQ 可重放；禁止把 saved manifest closure 写成 R20 重算；禁止把 2 个 R8 seeds + 1 个 R12 seed 或 period 数伪装成统计显著性；禁止把 observed CUT fraction 当 Pfa；禁止用建议、阶段裁决或代码测试作为总纲修改授权。
```
