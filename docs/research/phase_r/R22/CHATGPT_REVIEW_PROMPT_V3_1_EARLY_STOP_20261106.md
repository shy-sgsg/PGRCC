# R22 V3.1 Screening 早停：ChatGPT 独立审查提示词

**仓库：** `https://github.com/shy-sgsg/PGRCC`

**分支：** `codex/unknown-system-error-next-stage`

**审查快照：** 请使用打开本提示词的 GitHub URL 中固定的完整 commit SHA；整个审查均固定在该 SHA，不跟随后续 branch HEAD。

**实验源码 commit：** `a2d8e65514847e4cc97e9e78df0c5cbafe528217`

**研究总纲 blob：** `e637e3769bde7814298ff012d0f42301884e2985`

## 必须遵循的阅读顺序

请首先在上述审查快照读取完整的：

`docs/research/RESEARCH_MASTER_PLAN.md`

理解 PGRCC 的研究主题、总体目标、核心科学问题、研究内容、研究思路、长期技术路线与评价原则。然后按顺序读取：

1. `docs/research/RESEARCH_STATUS.md`
2. `docs/research/RESEARCH_DECISION_LOG.md`
3. `docs/research/CHATGPT_REVIEW_PROTOCOL.md`
4. `AGENTS.md` 与 `docs/research/phase_r/R22/README.md`
5. `docs/research/phase_r/R22/23_v3_1_post_replay_screening_contract_20261106.md`
6. `docs/research/phase_r/R22/24_v3_1_screening_early_stop_20261106.md`
7. `docs/research/phase_r/R22/evidence/v3_1_early_stop_20261106/case_summary.json`
8. `docs/research/phase_r/R22/evidence/v3_1_early_stop_20261106/matrix_manifest.json`
9. `docs/research/phase_r/R22/evidence/v3_1_early_stop_20261106/failure_case.zip`
10. 报告指向的冻结设计 JSON、Production runner/source、method config 与 scenario template。

如任何链接或 ZIP 无法实际读取，请说明文件名并标记 `NOT_VERIFIED`；不能声称检查了未读取的文件。归档中保留运行日志与配置，没有保留原始 IQ 和可重建大数组。

## 阶段身份与关键问题

本阶段冻结计划是288个 case、4,320个适用 ON 方法/模式单元。Native CUDA 实际尝试6个 case：5个通过，1个以 `UNKNOWN_STOP` 终止；已访问90个方法单元中87个可评价、3个未知，282个 case/4,230个方法单元未执行。只有独立 seed1932001。

请重点独立核对：

1. 矩阵是否遵守冻结的 fail-closed 门控；`UNKNOWN_STOP` 是否被正确保留，而非重试、跳过或回退到 Current。
2. 6个 case 的 Stage2 连续面积地杂波是否真实存在；数量、平均功率与配对可比性是否支持报告中的说法。
3. +4 ns / 30 dB 失败 case 的方法级可评价性、失败分支、task rows、valid-CUT/unique-hit gate 与 H0 统计是否被正确解释。
4. `C2 / Mode-B / ON` 的 OOM 是否只能说明 CUDA 执行资源失败；另两条 return code 1 的分支在日志不足时应如何标记。
5. 一个需要明确审计的身份冲突：matrix manifest 中 `design_sha256=696dce5b18fab9209f57c7d49922ff3c1ae4ea52f0a524c34cede8e8b933e635`，仓库 V3.1 设计 JSON 的 SHA-256 为 `26e202fca197ae1f62cfad099cdf895de7b834eed1622c553e80b8cd2b94a839`。请判断这对实验与冻结设计绑定、可复现性和阶段裁决的影响，不要自行推测缺失设计文件内容。
6. 工作树、阶段归档和 raw cleanup 的体量是否准确；区分 ledger 累计逻辑文件大小与实际释放的物理空间。

## 请完成双层独立审查

### 第一层：实验执行与证据正确性

- 复核矩阵分母、case 状态、方法单元计数和独立统计单位。
- 核对面积杂波 gate、ON/OFF/TO 配对、valid CUT、unique hit、Production statuses 与压缩归档证据。
- 判断 OOM 和两条未知分支如何限制 case 与整个 Screening 的可评价性。
- 区分 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED`。
- 评价设计 SHA 不匹配是否使实验配置身份整体 `NOT_VERIFIED`，以及报告当前表述是否充分、准确。
- 说明 GitHub 可直接读取的材料与仍未提供/不可核验的数据边界。

### 第二层：总体 GMTI 科研价值与方向

- 判断本轮是否回答了未知系统误差对 GMTI 任务性能的影响、校正收益或传统方法差距。
- 判断是否有任何证据支持低速目标保护、复杂杂波建模、未知状态估计或 AI 方向调整。
- 评价按完整冻结矩阵继续筛选的科学价值；提出一个最小、可审计的后续动作和明确停止条件。
- 不能把一次资源 OOM 当作算法失败，也不能从5个通过 case 推断总体性能。

## 输出格式

先给裁决（`PASS_WITH_LIMITATIONS` / `INCONCLUSIVE` / 其他有依据的标签），再给：

1. 可验证事实与对应文件；
2. 设计身份冲突的处理；
3. 阶段任务正确性结论；
4. 总体研究价值与方向建议；
5. 一个下一步及停止条件；
6. `NOT_VERIFIED` 清单。

本次审查不是修改授权。不得直接修改 `RESEARCH_MASTER_PLAN.md`、冻结实验协议、算法或实验参数；不得启动新实验、AI 训练或 Router。若建议修改总纲，先列具体修改建议与证据，提交用户讨论，等待明确批准。
