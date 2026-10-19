# PGRCC Phase R：研究审查与证据导航

本目录用于判断 PGRCC 的 GMTI 性能瓶颈、可恢复空间和研究方向。阅读时要区分理论推导、软件验证、历史仿真和生产 CUDA 结果；它们不能互相替代。

## 当前判读

- **AI 必要性尚未建立**，但现有证据也没有证明 AI 无价值。是否进入 Physics-AI，仍取决于未知误差造成的可重复损失、已知误差校正上限、盲确定性估计残差及下游生产指标能否闭合。
- R0–R3 的架构、文献、物理边界和评价链审计已有文档。R3 软件修复通过既有软件验证，但尚无修复后的 CUDA production 重跑，故不能声称生产性能已验证。
- 四条件 raw-IQ Stage2 pilot 的方向归一化恢复率约为 1，但地面面积杂波注入计数为 0：平台高 6000 m、地面高 0 m，配置斜距窗 2500–5000 m 不可达。相位残差是越低越好，恢复率按 `(Current−Estimated)/(Current−Known)` 计算；原始 `Known−Current` 和 `Estimated−Current` 均为负。该结果只支持配对参考下的确定性基线相位校正，不支持杂波抑制收益或 AI 必要性，详见[带方向定义的数值摘要](phase_ii_raw_iq_pilot_summary.json)。
- 截至本导航快照，R4 实验设计与新仿真等待用户提供外部审查方向；R5 决策及下一阶段计划尚未形成。

## 推荐阅读顺序

1. [交给 ChatGPT 的下一步审查提示词](00_ChatGPT_next_step_prompt_20261019.md)：聚焦当前 AI 必要性证据、Phase-I 圆周相位敏感度疑点和唯一优先下一步。
2. [R0 系统架构审计](01_system_architecture_audit.md)：四路协议 IQ 到 F1/F2、CSI、CFAR、检测及航迹的实际数据流与信息自由度。
3. [R1 文献审查](02_literature_review.md)及 [文献矩阵](literature_matrix.csv)：研究先例、适用条件和 PGRCC 适配边界。
4. [R2 物理边界分析](03_physical_limits.md)：双通道响应、速度投影/模糊、时延敏感性和可辨识性限制；附带由理想模型生成、明确非 Pd/实测指标的单像素响应曲线及[生成数据](figures/idealized_target_response.csv)。
5. [R3 评价流水线审计](04_evaluation_pipeline_audit.md)：历史结果可评估性、评价代码修复和 production 验证缺口。
6. [Phase-II raw-IQ pilot 摘要](phase_ii_raw_iq_pilot_summary.json)：几何配置、杂波注入计数、相位残差及解释边界；不含原始 IQ。

此前的 `00_ChatGPT_review_request_20261019.md`、`00_minimal_review_brief_20261019.md` 和 ZIP 快照保留为历史材料，不属于本次 GitHub 发布清单。其中 Phase-I 的线性相位差分可能跨越 ±π，导致旧敏感度矩阵及 delay-servo 混淆结论需要重算；不要把旧包中的数值直接当作当前结论。新提示词列出了独立复核所需的疑点及其限制。

## 阶段与交付物状态

| 阶段 | 主要文件 | 当前状态 |
|---|---|---|
| R0 系统架构 | `01_system_architecture_audit.md` | 已审计；不是新的 production 运行结果 |
| R1 文献调研 | `02_literature_review.md`、`literature_matrix.csv` | 已完成本轮审查；全文访问限制按文档标记 |
| R2 物理边界 | `03_physical_limits.md` | 已完成基于源码/配置的计算；不是理论 CRLB 或新实测 |
| R3 评价链 | `04_evaluation_pipeline_audit.md` | 软件问题已审计/修复并有测试；修复后 CUDA production 结果未验证 |
| R4 实验设计与执行 | `05_experiment_design.md`、`06_bottleneck_experiment_results.md` | 尚未形成 Phase R 注册设计或新结果；等待外审方向，不据此启动仿真 |
| R5 方向决策 | `07_research_direction_decision.md`、`08_next_stage_plan.md` | 待 R4 证据与决策门槛满足后形成 |
| 导航与最终复核 | 本文件 | 导航入口已建立；Q1–Q12 全量最终复核仍待 R4/R5 材料齐备 |

R3 文档使用 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED` 区分软件与算法证据。特别是 `completed_with_gaps`、测试通过或产物存在均不单独证明算法有效。

## 关键证据入口

- 四条件 raw-IQ Stage2 pilot：可引用的紧凑摘要在[本目录摘要](phase_ii_raw_iq_pilot_summary.json)。逐 case 报告、配置、日志和 raw IQ 位于本地 `outputs/unknown_system_error_pilot_20260913_v5/`，未纳入本次 GitHub 发布；摘要只支持文中明确限定的几何与相位残差判读。
- 2026-09-14 clean-labelled 重跑与有效斜距控制：几何、注入计数及范围已记入[紧凑摘要](phase_ii_raw_iq_pilot_summary.json)。控制结果来自另一 servo pilot，不是四条件 pilot 的等价复验；逐 case 输出未纳入本次 GitHub 发布。
- 历史 production calibration pilot：[`report.md`](../../evidence/equivalent_vs_physical_pilot/report.md) 与相邻 manifest。该历史矩阵为 `completed_with_gaps`，不能当成 R3 修复后的 CUDA 验证。

## 尚未完成的决策门

Phase R 的最终结论必须回答原任务中的 Q1–Q12，并比较 A/B/C（必要时有证据支持的 D）。在完成最小、配对且可评估的实验前，不把未知系统失配定为主要瓶颈，不把盲确定性估计器失败解释为 AI 优势，也不启动 MLP、通用复权残差、image-to-image 或 Router 训练。
