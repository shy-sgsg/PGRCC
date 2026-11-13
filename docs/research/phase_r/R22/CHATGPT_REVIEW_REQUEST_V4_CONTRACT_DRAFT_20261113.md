# R22-V4 确认性契约草案：ChatGPT 独立审查请求

> 发起正式审查时，将下方 `审查 commit` 替换为本阶段已推送的完整 Git SHA，并使用该 SHA 固定链接读取文件。审查基线不能用漂移的 `HEAD` 代替。

【PGRCC 科研阶段独立审查请求】

请首先读取审查 commit 对应版本的：

`docs/research/RESEARCH_MASTER_PLAN.md`

完整理解项目研究主题、总体目标、核心科学问题、研究内容、总体研究思路和长期技术路线。随后按此顺序读取：

1. `docs/research/RESEARCH_STATUS.md`
2. `docs/research/RESEARCH_DECISION_LOG.md`
3. `docs/research/CHATGPT_REVIEW_PROTOCOL.md`
4. `AGENTS.md`
5. `docs/research/phase_r/R22/V3_3_residual_task_gap_audit/FINAL_RESEARCH_REPORT.md`
6. `docs/research/phase_r/R22/31_v4_causal_output_complete_confirmatory_contract_draft_20261113.md`
7. `docs/research/phase_r/R22/config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json`

仓库：`https://github.com/shy-sgsg/PGRCC`<br>
分支：`codex/unknown-system-error-next-stage`<br>
审查 commit：`[由阶段交付者填写完整 SHA]`<br>
实验执行源码 commit：无；本阶段只形成方案草案，尚未实现或执行。<br>
研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`<br>
阶段：`R22-V4 output-complete confirmatory contract draft`<br>
CUDA/AI：未运行 / 未训练；CUDA 执行未获授权。

## 本次希望审查的问题

请进行两层独立审查。

### 第一层：契约与方法学正确性

1. 180 个 target-present case 与 60 个新 H0 case 的因子、配对、独立 seed 和分母是否自洽？1936001–1936030 是否应保持为候选区间，正式冻结前还需哪些碰撞核查？
2. M1 每周期仅用当前与过去 ON raw C1/C2、在周期边界估计并用于当前周期，是否正确区分了周期因果与实时/脉冲因果？失败时显式 C0 零校正策略是否公平、可评价？
3. Fixed D3、Known correction、Batch-10 diagnostic 的信息条件和排名边界是否充分？是否存在未声明的 oracle、未来数据或配对信息泄漏？
4. 位置 RMSE 的共同成功机会、漏检/估计覆盖率保护门、MDE 与非劣界值缺口，以及 30 seed 推断方案是否适当？明确指出哪些结论在阈值未定前不能确认。
5. H0 的 `P_FA_beam50_period`、CUT、cluster 和 TrackManager false-track 单位是否表述准确，是否避免错误宣称完整系统 Pfa？
6. Detection、truth lineage、TrackManager/PIPE、目标专属复数响应、CFAR geometry、身份、compact archive、续跑与 raw cleanup 是否足以支持本次科学问题和复现？
7. 目前 V3.2 compact archive 的输出缺口是否被正确识别，且没有用历史不完整归档代替 V4 新证据？

### 第二层：总体 GMTI 科研价值

1. 本 V4 任务是否推动研究总纲中的整体 GMTI 目标，而不是只追求一个筛选指标？
2. target-present 周期因果物理估计相对固定传统校正的确认，是否值得本阶段资源？是否有更简单且同信息条件的确定性 Baseline 必须纳入？
3. 该设计是否能够区分检测、定位、目标保持和航迹收益，支持停止条件？
4. 现有证据是否足以改变研究主线、支持低速保护/复杂杂波优先级或启动 AI？若否，请明确说明证据缺口。
5. 是否建议 `ACCEPT_DRAFT / REVISE / REJECT`？列出按严重程度排序的问题与最小修改；说明是否可在 MDE/非劣门界值补齐后进入用户审批。

请区分 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED`。本阶段这些实验状态不得被误报为已实现/运行/性能验证。不得直接修改研究总纲；如建议修改，只提出有证据支持的提案并交用户决定。设计审查通过也不等同于 CUDA 授权。

如果你无法实际读取上述 commit 的研究总纲或关键证据，请说明未读取文件和 `NOT_VERIFIED` 边界，不要用本提示词摘要替代真实读取。
