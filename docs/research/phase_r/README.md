# PGRCC Phase R：研究审查与证据导航

本目录用于判断 PGRCC 的 GMTI 性能瓶颈、可恢复空间和研究方向。阅读时要区分理论推导、软件验证、历史仿真和生产 CUDA 结果；它们不能互相替代。

## 当前判读

- **AI 必要性尚未建立。** 修复后的 CUDA Production A/B/C 在 seed 101、202、303 上显示 +4 ns 通道时延使角度、跟踪地速和位置 RMSE 同向变差，已知物理校正则逐 seed 恢复；这支持继续验证确定性未知时延估计，不支持直接进入 AI。
- R3 的修复后 Production smoke 和最小因果 pilot 已在真实 CUDA 上执行。`PERFORMANCE_VERIFIED` 仅指上述三个跟踪/定位 RMSE 方向在三 seed 重复；Pd/Pfa 显著性、径向速度精度及 AI 增益均未验证。详见 [R3 执行报告](06_minimal_cuda_causal_pilot.md) 与[方向评估](07_preliminary_direction_assessment.md)。
- Phase-I 相位跨 ±π 的差分错误已用 54 组保存观测复算确认并修复。旧 delay–servo 近混淆判读需撤回；修正后的汇总条件数约 3.99，仍有明显 seed/特征/尺度依赖。参见[复核报告与可独立复算的输入](phase_i_observability_reanalysis.md)。
- 四条件 raw-IQ Stage2 pilot 的方向归一化恢复率约为 1，但地面面积杂波注入计数为 0：平台高 6000 m、地面高 0 m，配置斜距窗 2500–5000 m 不可达。相位残差是越低越好，恢复率按 `(Current−Estimated)/(Current−Known)` 计算；原始 `Known−Current` 和 `Estimated−Current` 均为负。该结果只支持配对参考下的确定性基线相位校正，不支持杂波抑制收益或 AI 必要性，详见[带方向定义的数值摘要](phase_ii_raw_iq_pilot_summary.json)。
- 此前 raw-IQ pilot 的面积杂波注入计数为 0，不能支持杂波收益。新 R3 pilot 已改用物理可达斜距窗并验证非零连续面积注入；历史无效结果仍保留为历史，不回写。
- 当前 R3 最小因果任务已完成并停止；下一步仅在用户审阅方向后决定，不自动启动 AI 或更大参数矩阵。

## 推荐阅读顺序

1. [交给 ChatGPT 的下一步审查提示词](00_ChatGPT_next_step_prompt_20261019.md)：聚焦当前 AI 必要性证据、Phase-I 圆周相位敏感度疑点和唯一优先下一步。
2. [R0 系统架构审计](01_system_architecture_audit.md)：四路协议 IQ 到 F1/F2、CSI、CFAR、检测及航迹的实际数据流与信息自由度。
3. [R1 文献审查](02_literature_review.md)及 [文献矩阵](literature_matrix.csv)：研究先例、适用条件和 PGRCC 适配边界。
4. [R2 物理边界分析](03_physical_limits.md)：双通道响应、速度投影/模糊、时延敏感性和可辨识性限制；附带由理想模型生成、明确非 Pd/实测指标的单像素响应曲线及[生成数据](figures/idealized_target_response.csv)。
5. [R3 评价流水线审计](04_evaluation_pipeline_audit.md)：历史结果可评估性、代码修复和生产路径验收要求。
6. [R3 最小四条件实验设计](05_experiment_design.md)：冻结的配对对照与门控。
7. [R3 最小 CUDA 因果 pilot](06_minimal_cuda_causal_pilot.md)：实际运行、杂波证据、闭合、指标、失败现场和复现入口；指标 CSV/JSON/图在[证据目录](evidence/r3_minimal_cuda_causal_pilot_20261021/)。
8. [R3 初步方向评估](07_preliminary_direction_assessment.md)：回答本阶段研究问题，明确哪些方向可继续、哪些仍不可判定。
9. [Phase-II raw-IQ pilot 摘要](phase_ii_raw_iq_pilot_summary.json)：历史几何配置、零杂波注入和解释边界；不含原始 IQ。
10. [Phase-I 局部敏感度复核](phase_i_observability_reanalysis.md)：圆周差分修正、逐 seed 矩阵、FFT 格点与尺度对照；附历史观测 manifest、结果及复算命令。

此前的 `00_ChatGPT_review_request_20261019.md`、`00_minimal_review_brief_20261019.md` 和 ZIP 快照保留为历史材料，不属于当前 GitHub 阅读入口。其中 Phase-I 的线性相位差分跨越 ±π；旧敏感度矩阵及 delay–servo 混淆结论已由 2026-10-21 复核修正。外审应同时读取新复核报告，不能把旧包数值直接当作当前结论。

## 阶段与交付物状态

| 阶段 | 主要文件 | 当前状态 |
|---|---|---|
| R0 系统架构 | `01_system_architecture_audit.md` | 已审计；不是新的 production 运行结果 |
| R1 文献调研 | `02_literature_review.md`、`literature_matrix.csv` | 已完成本轮审查；全文访问限制按文档标记 |
| R2 物理边界 | `03_physical_limits.md` | 已完成基于源码/配置的计算；不是理论 CRLB 或新实测 |
| R3 评价链与实验 | `04_evaluation_pipeline_audit.md`、`05_experiment_design.md`、`06_minimal_cuda_causal_pilot.md` | A/B/C 三 seed 已执行；tracking/localization RMSE 方向重复；Pd/Pfa 显著性和径向速度未验证 |
| R3 方向初判 | `07_preliminary_direction_assessment.md` | 支持继续窄化的确定性未知时延估计评估；不支持 AI 必要性结论 |
| 后续阶段 | 由用户审阅后确定 | 本任务已停止；未训练 AI、未开发 Router、未扩展六参数估计 |
| 导航与阶段结论 | 本文件 | 提供 R3 阅读入口；不替代最终全研究问题审查 |

R3 文档使用 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED` 区分软件与算法证据。特别是 `completed_with_gaps`、测试通过或产物存在均不单独证明算法有效。

## 关键证据入口

- 四条件 raw-IQ Stage2 pilot：可引用的紧凑摘要在[本目录摘要](phase_ii_raw_iq_pilot_summary.json)。活动 worktree 的 `outputs/unknown_system_error_pilot_20260913_v5/` 保留配置、日志和观测汇总，其 `raw/` 子树及原始 IQ 不在本工作树或 GitHub 发布材料中；逐 case Stage2 原报告的历史取证位置见摘要。摘要只支持限定的几何与相位残差判读。
- Phase-I 观测到矩阵复算：[源 manifest](evidence/phase_i_observability_reanalysis_20261021/source_manifest.json)和[完整结果](evidence/phase_i_observability_reanalysis_20261021/summary.json)。包含 54 组已派生观测；不含原始 IQ。
- 2026-09-14 clean-labelled 重跑与有效斜距控制：几何、注入计数及范围已记入[紧凑摘要](phase_ii_raw_iq_pilot_summary.json)。控制结果来自另一 servo pilot，不是四条件 pilot 的等价复验；逐 case 输出未纳入本次 GitHub 发布。
- 历史 production calibration pilot：[`report.md`](../../evidence/equivalent_vs_physical_pilot/report.md) 与相邻 manifest。该历史矩阵为 `completed_with_gaps`，不能当成 R3 修复后的 CUDA 验证。
- 修复后 R3 最小因果 pilot：紧凑 JSON、逐条件 CSV、跨 seed 图和 smoke 原始审计文件见 [`evidence/r3_minimal_cuda_causal_pilot_20261021/`](evidence/r3_minimal_cuda_causal_pilot_20261021/)。可复现 Stage2/Production case 的其余详细日志、CSI 数组和 CFAR/航迹文件保存在本地 `outputs/phase_r_minimal_cuda_causal_pilot_20261021/`；原始可重建 `.bin` 已按清理台账移除。

## 尚未完成的决策门

本次结果回答的是单一 `+4 ns` 时延场景下的因果可行性，不是原任务 Q1–Q12 的最终结论。还不能把该失配视为唯一或主要系统瓶颈，也不能把单 seed D 与已知上限的差距解释为 AI 优势。未启动 MLP、通用复权残差、image-to-image 或 Router 训练。
