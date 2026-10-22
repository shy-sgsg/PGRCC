# PGRCC Phase R：研究审查与证据导航

更新日期：2026-10-22。本目录判断 GMTI 瓶颈、可恢复空间和研究方向。请区分理论推导、外部文献、软件测试、历史仿真和本轮真实 CUDA Production 结果；它们不可互相替代。

## 当前决策

- **当前方向：`INCONCLUSIVE`。AI 必要性尚未建立，亦未证明 AI 无价值。** R7 发现并修复 CUDA GO-CFAR float32 积分图相消错误；九组真实 CUDA Production 配对地图在修复后闭合到独立 float64 reference。+4 ns 条件的 CSI 相干性下降且单次目标候选缺失，Known correction 的 ON/TO taps 接近 A0，但单 seed/period、无 confirmed track 和完整 cluster lineage，尚不能声明统计 GMTI 任务增益或损失。先闭合任务评价链，再决定是否扩展未知状态估计；不训练 AI、不开发 Router、不扩展六参数联合估计。
- **E2/R3：** 修复后的 CUDA Production 在 seed 101/202/303 上执行通道 2 `+4 ns` A/B/C。三 seed 的角度、TrackManager 地速和位置 RMSE 方向均为失配变差、Known physical correction 恢复；三项 paired-t 95% CI 均跨 0。径向速度 RMSE、统计性 Pd/Pfa 未验证。详见[合并结果](06_bottleneck_experiment_results.md)和[R3 逐条件报告](06_minimal_cuda_causal_pilot.md)。
- **E1/E4/E5/R4：** seed 101 的 8 case、120 个 CUDA Production 分支已执行且分支通过；连续面积地杂波有效。但低速 P5 背景 ROI、CFAR 唯一 hit-cell/false-hit 聚合、residual-closure 的 PK/PKR 条件未闭合，因此不扩展 seed 202/303、不作方法排名或 Pd/Pfa 性能结论。详见[结果合并](06_bottleneck_experiment_results.md)与[R4 紧凑证据](evidence/r4_e1_e4_e5_seed101_20261022/)。
- **R6/R7：** R6 提出的 GO-CFAR 数值修复和 seed101 A0/B/C 最小回归已在 R7 执行；根因、实现、目标关联限制、配对 manifest 与方向裁决见下方五份 R7 报告。R7 之后仍需先补 cluster-to-detection lineage，再进行独立 seed 的任务级验证。
- **R5/R6 证据审计：** R6 保留了输入 SHA 指纹、全 CSI maps、CFAR branch/cell 指标与 TrackManager 链路；R7 重新核算的部分历史输入指纹未能与原 run manifest 逐项交叉核验。R3 历史 binary SHA 缺失，R6 不能宣称与旧二进制相同；R6 当前 CFAR 的非正门限影响虚警和任务归因。R4 低速 ROI/false-CUT 边界仍见原审计报告。
- **历史证据：** 修复前 E3 128-scene 结果、旧 raw-IQ pilot 和 Phase-I 旧敏感度矩阵均保持原身份/日期；不得与当前 R3/R4 的 seed、分母、源码身份拼接。Phase-I 圆周相位差分已于 2026-10-21 修正并重算，见[复核报告](phase_i_observability_reanalysis.md)。

## 推荐阅读顺序：01–08

1. [01 系统架构审计](01_system_architecture_audit.md)：四路协议 IQ 融合 F1/F2、CSI、CFAR、检测、航迹的当前生产信息流与自由度。
2. [02 文献审查](02_literature_review.md)及[文献矩阵](literature_matrix.csv)：六类 GMTI 问题的原始来源、适用条件和 PGRCC 适配；全文访问状态逐条标注。
3. [03 物理边界](03_physical_limits.md)：双通道响应、速度/LOS 投影、多普勒模糊、时延敏感性和不可辨识边界；附明确标为解析模型、非实测 Pd 的曲线与[生成数据](figures/idealized_target_response.csv)。
4. [04 评价流水线审计](04_evaluation_pipeline_audit.md)：当前修复、历史失败根因、CFAR 分母、Mode-B 可比性和仍不可评估项目。
5. [05 冻结实验设计](05_experiment_design.md)：E1–E5 假设、配对条件、统计单位与停止门。
6. [06 实验结果合并](06_bottleneck_experiment_results.md)：按 E1–E5 汇总原 R3/R4 数字与分母；[R3/R4 执行报告](06_minimal_cuda_causal_pilot.md)保留对应阶段证据。R6/R7 的分阶段证据见下方阶段报告。
7. [07 研究方向决策](07_research_direction_decision.md)：R3/R4 阶段的方向快照；R6/R7 的更新决策分别见[R6 阶段裁决](R6_next_stage_decision.md)和[R7 方向裁决](R7_research_direction_decision.md)。[07 初步方向评估](07_preliminary_direction_assessment.md)也是较早阶段快照。
8. [08 下一阶段计划](08_next_stage_plan.md)：按评价门、seed 扩展、D3 盲估计和 AI 决策门排序的输入/输出/测试/停止计划；当前仅为建议，尚未执行。

## 证据状态标签

`IMPLEMENTED` 表示代码/配置/评价能力存在；`EXECUTED` 表示对应路径确已运行；`EVALUABLE` 只对定义、配对、真值和分母完整的具体指标成立；`PERFORMANCE_VERIFIED` 仅限报告明示的场景与指标。测试通过、退出码为 0、case 数量或产物存在本身不证明算法效果。Observed hit fraction、unmatched detection fraction 和 configured Pfa 不能互相替代。

## R5 补充审计（2026-10-22）

1. [R3 A0/C 首个差异级审计](R3_A0_C_divergence_audit.md)：同一 fusion-cache Production 调度、CSI 标量 tap 近闭合、CFAR/hit 与检测数有差别；因全图 CSI `.npy` 不在留存证据中，精确首个差异级 `NOT_EVALUABLE`。
2. [R4 低速 P5 支持域审计](R4_low_speed_P5_audit.md)：区分无效的局部背景 CA ROI 与仍有效的配对 H0 target-ROI SCNR。
3. [R4 H0 CFAR 分层审计](R4_CFAR_H0_audit.md)：GO-CFAR 几何、阈值标定、分支分母、H0 复用与后续虚警层状态。
4. [Phase R 可评估性裁决](Phase_R_evaluability_decision.md)：汇总可保留结论、不可外推结论及 AI/主线门控。
5. [唯一下一最小实验方案](Next_minimal_experiment_plan.md)：R6 执行前的方案快照；其 A0/C 阶段定位任务已由下方 R6 报告执行，不是当前待办。

## R6 A0/C 分歧定位（2026-10-22）

1. [R6 执行报告](R6_execution_report.md)：输入/程序身份、A0/A0 与插桩控制、CUDA 运行状态、失败现场和复核入口。
2. [R6 分阶段差异](R6_stagewise_divergence.md)：物理输入闭合、完整 CSI maps、首个连续与离散差异级。
3. [R6 CFAR 门限敏感性](R6_cfar_threshold_sensitivity.md)：valid CUT、非正门限根因、分支命中和精确 hit-flip 数据。
4. [R6 任务误差归因](R6_task_error_attribution.md)：历史 RMSE 复现、目标检测与 TrackManager association。
5. [R6 下一阶段裁决](R6_next_stage_decision.md)：`INCONCLUSIVE` 结论及只建议、不自动启动的下一步。

## R7 CUDA GO-CFAR 数值修复（2026-10-22）

1. [R7 数值根因](R7_CUDA_CFAR_numerical_root_cause.md)：R6 36 张历史 branch-period 地图与 R7 九组配对的错误门限/命中差异。
2. [R7 修复与测试](R7_CUDA_CFAR_fix_and_tests.md)：双精度积分、误差界直接重算、真实 CUDA selftest、资源成本和性能限制。
3. [R7 H0/目标关联审计](R7_H0_detection_association_audit.md)：逐模式 detections/tracks 描述计数、冻结目标门限、未闭合的关联 stage。
4. [R7 Production 复验](R7_minimal_production_revalidation.md)：九个条件、输入/程序身份、运行产物和复现入口。
5. [R7 方向裁决](R7_research_direction_decision.md)：分开报告数值正确性、单次任务候选和统计性能可评估性。

## 主要证据位置与保留边界

- R3 compact 证据：[`evidence/r3_minimal_cuda_causal_pilot_20261021/`](evidence/r3_minimal_cuda_causal_pilot_20261021/)（evaluation JSON、逐 seed CSV、图、runtime smoke、XML、manifests 和失败现场）；完整 case 产物在本地 `outputs/phase_r_minimal_cuda_causal_pilot_20261021/`。可重建的原始 `.bin` 已依 cleanup ledger 清理。
- R4 compact 证据：[`evidence/r4_e1_e4_e5_seed101_20261022/`](evidence/r4_e1_e4_e5_seed101_20261022/)（manifest、case/branch/P5/CFAR 表和图）；完整 case roots 在本地 `outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_*`。72 个 Stage2 protocol-IQ 文件已清理，仅保留路径、size、hash；因此输入配对可审计，但 F1/F2 输入功率不能独立重算。
- R6 compact 证据：[`evidence/r6_stagewise_divergence_20261022/`](evidence/r6_stagewise_divergence_20261022/)（A0/C 全 CSI map 比较、A0/A0 repeat controls、CFAR exact hit indices、六条件 task metrics 与 manifest）；完整 Stage2、runtime XML、logs、CSI/CFAR 和 TrackManager 原始结果留在本地 `outputs/phase_r6_stagewise_divergence_20261022/`，未上传。
- R7 compact evidence：[`evidence/r7_cfar_correctness_20261022/`](evidence/r7_cfar_correctness_20261022/) 含 pre/post manifests、runtime XML、必要的 R6 regression fixture、R7/R6 数值 JSON/CSV 和九份 CSI summary。完整 `outputs/phase_r7_cfar_correctness_20261022/` 仍保留本地 logs、CSI/CFAR maps、geometry、TrackManager debug、detections 和原始运行树；体积较大的地图/输入不纳入 Git。
- 历史 equivalent-vs-physical pilot：[`report.md`](../../evidence/equivalent_vs_physical_pilot/report.md)与相邻 manifest。其源码身份为历史提交，`completed_with_gaps`，不能当作 R3 修复后重跑。
- 历史四条件 Stage2 raw-IQ pilot：[`phase_ii_raw_iq_pilot_summary.json`](phase_ii_raw_iq_pilot_summary.json)记录面积杂波注入为 0；平台 6,000 m、地面 0 m、2.5–5 km 斜距窗不可达。它只支持窄范围相位残差判读，不支持杂波收益。
- Phase-I 观测复核：[`evidence/phase_i_observability_reanalysis_20261021/`](evidence/phase_i_observability_reanalysis_20261021/)含已派生观测、manifest 和重算结果；不含原始 IQ，也不证明全局可辨识或端到端估计精度。

## 尚待决策的门

R7 的 GO-CFAR 正确性门已通过当前九组矩阵；下一门是逐 cluster 到检测的 lineage 与 confirmed-track 评价闭环，然后再选独立 seed 验证 A0/B/C 任务指标。R4 低速 P5 背景 ROI、CFAR false-hit 真值关系及 PK/PKR evaluator 缺口仍需在后续实验设计中明确。未知时延证据仍限 seed101 和一个受控仿真误差，缺真实 airborne 误差分布与多误差条件；在证据补齐前不启动 AI。
