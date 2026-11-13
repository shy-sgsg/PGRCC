# R22-V4 确认性契约草案：ChatGPT 独立审查请求

> **固定设计审查快照：** `9db4592dd2ba7d31694acd80bea2c128c59a2ac9`。本提示词随之后的交付提交保存；ChatGPT 必须将本提示词所列的研究文件固定读取自上述设计审查快照，不能用提示词提交或漂移的分支 `HEAD` 替代。

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
8. 在源码审计快照 `1063d67c96af7dbbe7fe871b3a56be0ca453b9b2` 阅读 `scripts/phase_r/run_r22_cuda_pilot.py`、`scripts/phase_r/run_r12_physics_matched_delay.py`、`scripts/phase_r/physics_matched_delay.py`、`scripts/phase_r/run_r21_cuda_pilot.py` 和 `scripts/estimate_channel_delay.py`，核对契约中的实现范围与 D3 定义。

仓库：`https://github.com/shy-sgsg/PGRCC`<br>
分支：`codex/unknown-system-error-next-stage`<br>
审查 commit：`9db4592dd2ba7d31694acd80bea2c128c59a2ac9`<br>
固定审查快照中的契约：[Markdown](https://github.com/shy-sgsg/PGRCC/blob/9db4592dd2ba7d31694acd80bea2c128c59a2ac9/docs/research/phase_r/R22/31_v4_causal_output_complete_confirmatory_contract_draft_20261113.md)；[机器设计 JSON](https://github.com/shy-sgsg/PGRCC/blob/9db4592dd2ba7d31694acd80bea2c128c59a2ac9/docs/research/phase_r/R22/config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json)<br>
V4实验执行源码 commit：无；本阶段只形成方案草案，尚未实现或执行。现有算法源码只读审计快照：`1063d67c96af7dbbe7fe871b3a56be0ca453b9b2`。<br>
研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`<br>
阶段：`R22-V4 output-complete confirmatory contract draft`<br>
CUDA/AI：未运行 / 未训练；CUDA 执行未获授权。

## 本次希望审查的问题

请进行两层独立审查。

### 第一层：契约与方法学正确性

1. 180 个 target-present、60 个新 H0 是 240 个唯一 case identity，按 seed 配对/嵌套；它们不是 240 个独立统计样本。目标任务按30个 seed cluster 推断。该矩阵展开为1,260条 target-present、210条 H0、合计1,470条方法×角色 Production branches。因子、配对、统计单位和分母是否自洽？1936001–1936030 是否应保持候选区间，正式冻结前还需哪些碰撞核查？
2. 当前 R22 源码把全部周期的 ON raw C1/C2 一次交给估计器，M1 拼接全周期后拟合；当前 runner 以单一 case 级 correction scalar 处理所有周期。故 V4 周期前缀 M1/Huber 尚未实现。契约要求每个 `k` 只读 `{0,…,k}` 并只校正当前周期，是否充分区分周期边界因果与实时/脉冲因果？如何审计 `k+1…9` 没有泄漏？失败时显式 C0 零校正是否公平、可评价？
3. Fixed D3 冻结校正值为 `3.4099650318669545 ns`，来源为 R9 对 R3 seed101 target-free A1_OFF C+N 的静态校正。原校准 IQ 的记录 SHA-256 为 `d8bd8673c30605f3817e25e54e5b5659f2d7617f9bba1f27b769449a7e458bd6`，但原始 IQ 已不可用，当前不能从原始数据重算该值；V4 不得重拟合或利用 V4 时延真值。请审查该固定基线是否仍可作为清楚标注的传统参考。
4. 已有 Huber 候选来自 M1 同一 pooled input 的 `physics_matched_delay._slope_baselines`，并复用 M1 的第80百分位 support mask；它做几何校正、幅度加权 robust `fit_weighted_line`（12次 IRLS、MAD scale、1.345 cutoff）。R21 gate 检查 finite estimate、support≥8、span≥3 MHz、D3/M1 support一致，但没有 D3 delay-domain bound。该共享 mask 的拟合规则对照是否足够公平，还是需独立冻结传统选频/有效范围？如何在相同前缀与逐周期校正时序实现？如果实现无法审计，是否应禁止宣称 M1 特有优势？检查是否存在 oracle、未来数据或配对信息泄漏。
5. 位置 RMSE 的共同成功机会、漏检/估计覆盖率保护门、MDE 与非劣界值缺口，以及30-seed推断方案是否适当？明确指出阈值未定时不能确认哪些结论。
6. H0 的 `P_FA_beam50_period`、CUT、cluster 和 TrackManager false-track 单位是否表述准确，是否避免错误宣称完整系统 Pfa？
7. Detection、truth lineage、TrackManager/PIPE、目标专属复数响应、CFAR geometry、身份、compact archive、续跑与 raw cleanup 是否足以支持本次科学问题和复现？
8. 目前 V3.2 compact archive 的输出缺口是否被正确识别，且没有用历史不完整归档代替 V4 新证据？
9. V3.3 历史筛选数据被限定在 `+4 ns / 20 dB / {0.5, 2.5, 6.7 m/s}` 时，M1−Fixed D3 位置差为 `−11.146 ± 4.797 m`、M1−Huber 为 `−5.603 ± 0.985 m`（均为12/12 seed 同向）；检测覆盖差分别为 `+0.083 ± 0.495 pp`、`0.000 ± 0.333 pp`。这些估计器使用十周期 batch，且该切片只用于粗略筛选精度规划。其算法和信息条件与V4周期前缀不同，故这些数是否被正确限制为设计背景，而非因果效果、功效、MDE或非劣界值？

### 第二层：总体 GMTI 科研价值

1. 本 V4 任务是否推动研究总纲中的整体 GMTI 目标，而不是只追求一个筛选指标？
2. target-present 周期因果物理估计相对冻结 Fixed D3 的确认是否值得本阶段资源？同信息 Huber D3 Causal Prefix 是否是必要传统对照，且能否回答 M1 是否提供额外价值？
3. 该设计是否能够区分检测、定位、目标保持和航迹收益，支持停止条件？
4. 现有证据是否足以改变研究主线、支持低速保护/复杂杂波优先级或启动 AI？若否，请明确说明证据缺口。
5. 是否建议 `ACCEPT_DRAFT / REVISE / REJECT`？列出按严重程度排序的问题与最小修改；说明是否可在 MDE/非劣门界值补齐后进入用户审批。

请区分 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED`。本阶段这些实验状态不得被误报为已实现/运行/性能验证。不得直接修改研究总纲；如建议修改，只提出有证据支持的提案并交用户决定。设计审查通过也不等同于 CUDA 授权。

如果你无法实际读取上述 commit 的研究总纲或关键证据，请说明未读取文件和 `NOT_VERIFIED` 边界，不要用本提示词摘要替代真实读取。
