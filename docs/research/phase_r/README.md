# PGRCC Phase R：研究审查与证据导航

更新日期：2026-10-26。本目录是 Phase R 的证据导航，不是独立的长期研究计划。总体主题、阶段安排与当前优先任务以[研究总纲](../RESEARCH_MASTER_PLAN.md)为准；方向变化记录于[研究决策日志](../RESEARCH_DECISION_LOG.md)。请区分理论推导、外部文献、软件测试、历史仿真和真实 CUDA Production 结果；它们不可互相替代。

## 当前决策

- **R19 更新（2026-10-26）：** 对 R18 的 12 个零时延场景完成 strict one-to-one task re-evaluation。R19 没有运行 CUDA；高速度 Mode-A/B 检测机会和下游候选数出现描述性差异，但 Mode-A 使用 paired-OFF privileged reference。经验 Pfa、target-only complex CSI/SCNR、CFAR 空间归因、radial velocity 与 ID-switch 根因仍未闭合。R18 全部为 0 ns，故不能回答未知误差因果效应、Known 恢复量或盲估计差距；AI、低速保护和复杂杂波主线均未获证据支持。唯一后续建议是先离线统一复评 R8/R12 的历史 +4 ns 条件，待用户授权。完整交付从 [R19 总实施计划](R19/00_master_execution_plan.md)进入。
- **R18 更新：** 本阶段已完成 12 场景、252 个 Native CUDA Production branches；有效连续面积杂波、输入 causal triplet、CFAR valid-CUT geometry/分母和 final-union-hit 门均通过。经验 Pfa、PK/PKR residual closure、目标专属幅相/SCNR 与 ID-switch lineage 未闭合，seed909 C3 参考因预注册 exact-support gate 失败而停止任务评价。阶段裁决为 INCONCLUSIVE，停止方法性能排名；固定零时延不能回答未知误差因果效应，不支持 AI、低速保护或复杂杂波路线变更。完整证据与报告见 [R18 导航](R18/00_master_execution_plan.md)及下方 R18 阅读入口。
- **R16 更新：** 本地 Native CUDA seed505、6.7 m/s、30 dB、0 ns pilot 已完成 A0/C0/C1 × OFF/ON/TO 共 9 branches。面积杂波和 27 张 CFAR final-union maps 有效；C1 Gamma=1 在 ON/TO 各正确检测 1/3，C0 为 3/3，C1 的 H0 残余与 TO 目标保持也较差。非目标检测行减少不能解释为 Pfa 改善。R14/R16 输入身份相同，但 Production binary 和 C0 CFAR 输出不同，因此不能用历史 C2/C3 给 C1 排名。该单 seed、零时延结果不回答未知误差因果问题，不支持 AI 或低速/复杂杂波方向变更；阶段结束且不自动扩展。详见 [R16 结果](R16_minimal_c1_results.md)、[方向评估](R16_research_direction_assessment.md)、[冻结设计](R16_experiment_design.md) 和 [紧凑证据包](evidence/r16_c1_gamma1_seed505/README.md)。
- **R14 更新：** 独立 seed505 Native CUDA pilot 复现 R13 的 C3/C2 TO CSI retention 差（+7.175 dB），并观察到 C3 ON 非目标 detection rows 减少 29.3%、false current-frame associations 减少 38.5%；ON 目标检测仍为两方法 3/3。但 R14 C3 TrackManager ground-speed RMSE 为 9.259 m/s，C2 为 1.086 m/s（共同机会 n=2），且末期目标关联从 track 6 切到 64。只裁决 `CONTINUE` 有界 C2/C3 任务权衡复核；当前不宣称整体性能胜出，经验 Pfa 仍 `NOT_EVALUABLE`，不训练 AI。该 seed 的 delay=0 ns，不改变未知系统误差主线。详见 [R14 结果](R14_minimal_comparison_results.md)、[方向评估](R14_research_direction_assessment.md)、[紧凑证据包](evidence/r14_scc_robust_ddc_seed505/README.md)。
- **R13 更新：** seed101 的两个零时延场景、24 个 Native CUDA branches 已执行。Normal-speed TO target-only CSI retention / detection 出现候选差异，但共同成功机会的参数比较显示位置、方位、地速与折叠 Doppler 有权衡；C2/C3 使用 paired OFF 标定信息、Current 不使用，只有一个独立 seed，因此没有验证净任务收益。0.5 m/s 挑战场景没有形成稳定方法差异；经验 Pfa 和径向速度 RMSE 仍 `NOT_EVALUABLE`。裁决为 `CONTINUE` 一次有界独立 seed 复核；R13 阶段停止、不自动启动；无 AI 必要性证据。R8/R9 历史 union count 已由当前 evaluator 复核，R12 因无 final-map audit 继续不可评估。详见 [R13 方向评估](R13_research_direction_assessment.md)、[结果](R13_minimal_comparison_results.md)、[CFAR 历史影响审计](R13_CFAR_and_velocity_audit.md) 和 [紧凑证据包](evidence/r13_baseline_cuda_20261024/README.md)。
- **R12 更新：** Native CUDA seed404 确认本场景未知 C2 `+4 ns` 使冻结任务检测从 A0 的 2/3 降至 0/3；Known correction 输入闭合并返回 2/3。M1 盲估计误差 0.241 ns，固定 D3 0.590 ns，两者均恢复 3/3 检测，但参数 RMSE 互有胜负；本轮只有一个 seed，统计 Pd/Pfa 和性能验证未完成。最终裁决为 `PIVOT`：固定时延估计保留为确定性 Baseline，不作为主要创新方向；转向其他 GMTI 瓶颈的公平 Baseline 评价，不训练 AI，也不预选低速/复杂杂波为下一主线。详见 [R12 盲估计验证](R12_blind_validation.md)、[生产任务比较](R12_production_task_comparison.md)、[阶段方向裁决](R12_final_research_direction_decision.md) 与 [R12 紧凑证据包](evidence/r12_physics_matched_delay_20261024/README.md)。
- **R10 更新：固定 D3 恢复本次 5/6 个正确检测机会，但没有恢复高精度测角/定位；没有证据表明必须使用在线 AI。** 三个正确 period 的 A0/D3 候选 cell 相同，production raw-phase 局部 Jacobian 对 cross-range 条件差的闭合残差不超过 0.35 m；另外两个 seed303 period 的候选 row 改变，需单独看待。Known 校正的定位 RMSE 接近 A0，且优于固定 D3，支持优先提高物理通道 2 的确定性标定精度和模型匹配。残余 phase 的所有物理来源仍未完全分离。以上仅为两个 seed 的描述性 CUDA 结果，无统计显著性 Pd/Pfa。R10 没有新增 CUDA 运行；不训练 AI。详见 [R10 方向裁决](R10_research_direction_decision.md)。
- **E2/R3：** 修复后的 CUDA Production 在 seed 101/202/303 上执行通道 2 `+4 ns` A/B/C。三 seed 的角度、TrackManager 地速和位置 RMSE 方向均为失配变差、Known physical correction 恢复；三项 paired-t 95% CI 均跨 0。径向速度 RMSE、统计性 Pd/Pfa 未验证。详见[合并结果](06_bottleneck_experiment_results.md)和[R3 逐条件报告](06_minimal_cuda_causal_pilot.md)。
- **E1/E4/E5/R4：** seed 101 的 8 case、120 个 CUDA Production 分支已执行且分支通过；连续面积地杂波有效。但低速 P5 背景 ROI、CFAR 唯一 hit-cell/false-hit 聚合、residual-closure 的 PK/PKR 条件未闭合，因此不扩展 seed 202/303、不作方法排名或 Pd/Pfa 性能结论。详见[结果合并](06_bottleneck_experiment_results.md)与[R4 紧凑证据](evidence/r4_e1_e4_e5_seed101_20261022/)。
- **R6/R7/R8/R9：** R8 完成 A0/B/C 三 seed 因果复验；R9 随后完成静态 D3 与 ON 数据 D3 候选回放。R9 新增 12 个 Production 分支、8 组 CFAR 数值审计，均通过对应质量门；两 seed 静态校正已恢复本次正确检测计数。D3 的物理闭合、任务级参数差距和部署边界见下方 R9 报告。
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

1. [R7 数值根因](R7_CUDA_CFAR_numerical_root_cause.md)：历史 R6/R7 数值对照及最终 27 张 period-separated Production union 地图的逐 CUT 参考复核。
2. [R7 修复与测试](R7_CUDA_CFAR_fix_and_tests.md)：双精度积分、误差界直接重算、真实 CUDA selftest、逐 CUT 近门限记录、CoreX 未执行边界、资源成本和性能限制。
3. [R7 H0/目标关联审计](R7_H0_detection_association_audit.md)：逐 period unique union hits、detections/tracks 描述计数、冻结目标门限与未闭合的关联 stage。
4. [R7 Production 复验](R7_minimal_production_revalidation.md)：九个条件、输入/程序身份、完整三周期数值复核、运行产物和复现入口。
5. [R7 方向裁决](R7_research_direction_decision.md)：逐项回答 Q1–Q10，区分数值正确性、三周期任务候选、航迹结果与独立 seed 统计限制。

## R8 未知时延多 seed CUDA 因果复验（2026-10-23）

1. [R8 冻结实验设计](R8_experiment_design.md)：A0/B/C、配对 OFF/ON/TO、面积杂波、处理链和停止门。
2. [R8 Native CUDA 执行记录](R8_native_cuda_execution.md)：源码/二进制身份、运行门、真实设备、完整本地原始结果位置与失败恢复记录。
3. [R8 目标检出因果结果](R8_detection_causal_results.md)：三个 seed 的正确检出、CSI/目标保持、参数误差与 H0 描述计数。
4. [R8 差异级分析](R8_mechanism_analysis.md)：邻域 CFAR hit、冻结关联门外偏移、F1/F2 输入功率及归因边界。
5. [R8 研究方向裁决](R8_research_direction_decision.md)：回答未知误差损失、Known Correction 恢复量、D3 可评估性、AI 与主线选择。
6. [R8 紧凑证据包](evidence/r8_unknown_delay_multiseed_20261023/)：机器 manifest、seed-condition CSV、逐期任务/关联/lineage 证据和图；约 9 GB 完整 runtime/CSI/CFAR 原始数据留在本地 `outputs/phase_r8_unknown_delay_multiseed_20261023/`。

## R9 固定标定与确定性盲估计任务级验证（2026-10-23）

1. [确定性估计器审计](R9_deterministic_delay_estimator_audit.md)：D1/D2/D3 的实现/历史执行状态、物理模型、输入隔离、真实 CUDA Production 身份和复现边界。
2. [固定离线标定基线](R9_static_calibration_baseline.md)：seed101 A1_OFF D3 标定值在 seed202/303 的物理闭合、检测/track recovery、参数误差和 H0 输出。
3. [ON 数据盲估计](R9_online_blind_estimation.md)：冻结选择规则、D1/D2/D3 结果、零注入偏置、期内离散度、在线可部署性限制。
4. [检测恢复与失败机制](R9_detection_recovery_results.md)：与 R8 A0/B/Known 的比较、CSI、目标保持、H0 计数和候选 cluster/detection lineage。
5. [R9 研究方向裁决](R9_research_direction_decision.md)：逐项回答 Q1–Q10；没有 AI 必要性证据，本阶段停止。
6. [R9 紧凑证据包](evidence/r9_deterministic_delay_estimator_20261023/)：估计输入 SHA、production branch/runtime/log/tap 索引、8 组 CFAR 数值审计、task metrics、target lineage、失败记录和复现入口。完整运行文件留在本机 `outputs/phase_r_r9_deterministic_delay_20261023/`。

## R10 固定 D3 后测角与定位误差归因（2026-10-23）

1. [目标候选与航迹关联一致性](R10_target_association_consistency.md)：逐 seed/period 对齐 A0、Known、固定 D3、在线 D3 的 target cell、cluster/detection、TrackManager 状态和 CFAR margin。
2. [测角与位置误差归因](R10_angle_position_error_attribution.md)：生产 CTDR/EN 几何公式、逐期误差、RMSE 与局部 phase→cross-range Jacobian 闭合。
3. [通道时延可观测性](R10_channel_delay_observability.md)：固定标定残差、分数延迟频率响应、融合估计与物理通道校正的可辨识边界。
4. [检测恢复与任务精度敏感度](R10_task_sensitivity_analysis.md)：正确 detection/Confirmed Track 分母、CSI target-only response、CFAR margin、测角/定位/地速敏感度。
5. [R10 研究方向裁决](R10_research_direction_decision.md)：逐项回答 Q1–Q8；建议优先提升物理 ch2 标定模型，不训练 AI，阶段结束后停止。
6. 复用 [R8 compact evidence](evidence/r8_unknown_delay_multiseed_20261023/) 和 [R9 compact evidence](evidence/r9_deterministic_delay_estimator_20261023/)；完整 production/TrackManager 数据保留在本机忽略的 R8/R9 outputs。

## 主要证据位置与保留边界

- R3 compact 证据：[`evidence/r3_minimal_cuda_causal_pilot_20261021/`](evidence/r3_minimal_cuda_causal_pilot_20261021/)（evaluation JSON、逐 seed CSV、图、runtime smoke、XML、manifests 和失败现场）；完整 case 产物在本地 `outputs/phase_r_minimal_cuda_causal_pilot_20261021/`。可重建的原始 `.bin` 已依 cleanup ledger 清理。
- R4 compact 证据：[`evidence/r4_e1_e4_e5_seed101_20261022/`](evidence/r4_e1_e4_e5_seed101_20261022/)（manifest、case/branch/P5/CFAR 表和图）；完整 case roots 在本地 `outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_*`。72 个 Stage2 protocol-IQ 文件已清理，仅保留路径、size、hash；因此输入配对可审计，但 F1/F2 输入功率不能独立重算。
- R6 compact 证据：[`evidence/r6_stagewise_divergence_20261022/`](evidence/r6_stagewise_divergence_20261022/)（A0/C 全 CSI map 比较、A0/A0 repeat controls、CFAR exact hit indices、六条件 task metrics 与 manifest）；完整 Stage2、runtime XML、logs、CSI/CFAR 和 TrackManager 原始结果留在本地 `outputs/phase_r6_stagewise_divergence_20261022/`，未上传。
- R7 compact evidence：[`evidence/r7_cfar_correctness_20261022/`](evidence/r7_cfar_correctness_20261022/) 含 pre/post manifests、runtime XML、必要的 R6 regression fixture、R7/R6 数值 JSON/CSV、九份 CSI summary、最终三周期逐条件 task metrics/association evidence，以及 CUDA selftest 的逐 CUT 近门限日志。完整 `outputs/phase_r7_cfar_correctness_20261022/` 保留本地 logs、CSI/CFAR maps、geometry、TrackManager debug、detections 和失败现场；大地图与输入不纳入 Git。
- 历史 equivalent-vs-physical pilot：[`report.md`](../../evidence/equivalent_vs_physical_pilot/report.md)与相邻 manifest。其源码身份为历史提交，`completed_with_gaps`，不能当作 R3 修复后重跑。
- 历史四条件 Stage2 raw-IQ pilot：[`phase_ii_raw_iq_pilot_summary.json`](phase_ii_raw_iq_pilot_summary.json)记录面积杂波注入为 0；平台 6,000 m、地面 0 m、2.5–5 km 斜距窗不可达。它只支持窄范围相位残差判读，不支持杂波收益。
- Phase-I 观测复核：[`evidence/phase_i_observability_reanalysis_20261021/`](evidence/phase_i_observability_reanalysis_20261021/)含已派生观测、manifest 和重算结果；不含原始 IQ，也不证明全局可辨识或端到端估计精度。

## 尚未闭合的外围评价项

R13/R14 的正常速度 pilot 已完成；经验 Pfa 与未解模糊径向速度仍不可评估。R17 离线审计发现 R14 实际运行的 `GMTI_pipe_core` 与 R7 修复前 GO-CFAR binary 完全同 SHA；R14/R15 的候选与航迹记录因此是修复前处理链的历史观察，不能直接作为修复后 Production 方法排名依据。若用户后续启动 C0/C2/C3 比较，应在同一修复后 binary 上进行有界复验，并保存实际 CFAR 输入/power/threshold；本建议不自动授权 CUDA。Pfa、真实 paired OFF 部署可得性及 AI 必要性仍未闭合，见[研究总纲](../RESEARCH_MASTER_PLAN.md)与[动态研究状态](../RESEARCH_STATUS.md)。

## R11 通道时延误差到测角/定位的因果验证（2026-10-24）

1. [R11 冻结设计](R11_experiment_design.md)：seed303 period 1 的六点 ch2 校正量、物理输入门、固定 cell 指标、Known-to-A0 门及失败停止条件；设计冻结在新增 CUDA Production 之前。
2. [R11 时延—相位敏感度](R11_delay_phase_sensitivity.md)：ch2/F2 closure、目标 cell CSI 复响应、Production raw/corrected CTDR phase 和 Native CUDA 执行状态。
3. [R11 固定 cell 与定位归因](R11_position_error_causal_analysis.md)：更新为 R7/R8 冻结门下同一目标的实际参数误差；旧几何定位候选保留为独立诊断。
4. [R11 评价器一致性审计](R11_evaluator_consistency_audit.md)：说明旧几何 matcher 与 R7/R8 任务 gate 的口径差异、回归覆盖及 R8/R9 历史复算。
5. [R11 冻结门重评](R11_frozen_gate_reanalysis.md)：列出六条件的正确关联、候选 gate 证据、CTDR/Jacobian 闭合和参数误差。
6. [R11 方向裁决](R11_research_direction_decision.md)：逐项回答 Q1–Q8，使用修正后的任务级关联解释。
7. [Phase R 更新方向裁决](Phase_R_updated_research_direction_decision.md)：综合 R3–R11 证据，只选一个后续优先方向并说明 AI 门控。
8. [R11 CUDA 生产证据](evidence/r11_local_delay_scan_20261024/README.md)：原始机器 manifest、runtime XML/config/log、CFAR geometry、CSI tap summary、检测/lineage、图表和入口失败现场。原始 IQ 与全图 CSI maps 不纳入 Git。
9. [R11 evaluator consistency 证据包](evidence/r11_evaluator_consistency_20261024/)：离线重评 JSON/CSV、逐候选 gate 记录、R8/R9 历史指标复算、脚本可重复使用的紧凑输入 bundle。

## R12 物理匹配盲时延估计与独立 seed404 CUDA pilot（2026-10-24）

1. [R12 可观测性分析](R12_observability_analysis.md)：C2 时延单参数的观测条件和未知宽带频响不可辨识边界。
2. [R12 物理匹配估计器](R12_physics_matched_estimator.md)：Stage2 分数时延响应、估计隔离、支持/拒绝门和合成及开发数据验证。
3. [R12 盲估计验证](R12_blind_validation.md)：seed404 的盲估计结果、同输入 D1/D2/D3 对照、Known 输入闭合差距和已知失败边界。
4. [R12 Production 任务比较](R12_production_task_comparison.md)：15 个 Native CUDA branch、面积杂波、CSI/目标保持、H0 描述计数、检测及参数误差。
5. [R12 阶段方向裁决](R12_final_research_direction_decision.md)：明确统计限制、AI 门控和下一步建议；阶段停止，不自动扩 seed。
6. [R12 紧凑证据包](evidence/r12_physics_matched_delay_20261024/README.md)：manifest、source/config 身份、15 个分支 runtime XML/config/log、CSI tap、CFAR geometry、检测/TrackManager 记录、关键 CSV、图表和失败案例。原始 IQ 与全 CSI maps 留在本机 `outputs/phase_r_r12_physics_matched_delay_20261024/`。

## R13 传统 GMTI Baseline 公平比较与最小 CUDA pilot（2026-10-25）

阅读顺序：

1. [R13 评价契约](R13_evaluation_contract.md)：目标机会、CFAR/H0、CSI/速度定义及状态标签。
2. [R13 CFAR 与速度审计](R13_CFAR_and_velocity_audit.md)：final-union map、有效 CUT、输入杂波与径向速度边界。
3. [R13 方法能力矩阵](R13_baseline_capability_matrix.md)：Production 与 offline 实现、数据/信息条件及本次执行状态。
4. [R13 最小实验设计](R13_minimal_experiment_design.md)：冻结场景、方法、配对角色和停止门。
5. [R13 结果报告](R13_minimal_comparison_results.md)：24 个 Native CUDA branches、数值和限制。
6. [R13 方向裁决](R13_research_direction_assessment.md)：独立 seed 建议、AI/低速/杂波主线判断。
7. [R13 紧凑证据包](evidence/r13_baseline_cuda_20261024/README.md)：manifest、脚本、运行记录、历史 CFAR 重算 CSV、关键图和清理账本；Stage2 IQ、全 CSI `.npy` 与 CFAR `.f32` 不纳入 Git。
8. [R8/R9/R12 历史 CFAR 重算入口](../../../scripts/phase_r/audit_r7_historical_cfar_impact.py)：读取历史 evaluation manifests，以当前 R7 evaluator 汇总；R9 本地 outputs 缺失时按证据包 README 指定 `--r9-evaluation-root`。

## R14 SCC / Robust DDC 独立 seed 任务收益复核（2026-10-25）

1. [R14 冻结设计](R14_experiment_design.md)：seed505、C0/C2/C3、Mode-A paired OFF、阶段门与复现命令。
2. [R14 最小比较结果](R14_minimal_comparison_results.md)：生产闭合、CSI/CFAR、检测、TrackManager、共同机会参数和运行限制。
3. [R14 方向评估](R14_research_direction_assessment.md)：逐项回答十个问题，明确 `CONTINUE` 仅限有界传统方法任务复核；长期主线不变、AI 未获证据支持。
4. [R14 紧凑证据包](evidence/r14_scc_robust_ddc_seed505/README.md)：12 branches 的 runtime XML/log、manifest、输入/CFAR/CSI/TrackManager 审计、target cell map 闭合、绘图脚本及清理账本；大体量可复现派生产物已清理。


## R15 杂波对消对航迹关联与参数估计的影响机制审查（2026-10-25）

1. [航迹关联分歧分析](R15_track_association_divergence_analysis.md)：逐期目标与竞争候选 lineage、Euclidean/Mahalanobis gate、Greedy 代价顺序、历史速度来源和固定输入 Hungarian 反事实。
2. [CFAR → Detection → TrackManager 传播审查](R15_cfar_detection_track_propagation.md)：解释 hit cells 增多而 Detection 变少的聚类路径及其对候选集合、关联和地速的传播边界。
3. [OFF reference 部署审查](R15_off_reference_deployability_review.md)：核对 H0/reference 同源身份、reference-in-sample 限制和无 paired OFF 的确定性候选。
4. [阶段研究方向裁决](R15_research_direction_assessment.md)：`STOP` 将该次 track switch 作为 Robust DDC 创新依据；明确广义 GMTI 主线与未启动的下一项建议。
5. [R15 离线关联证据包](evidence/r15_seed505_track_association/README.md)：紧凑 replay 表、p2 候选代价摘录、源代码/输入身份及 CPU 复现命令；复用 R14 Native CUDA Production 数据，不含大体量候选矩阵。

## R16 固定 Gamma=1 ordinary subtraction 最小 Native CUDA pilot（2026-10-25）

1. [R16 冻结实验设计](R16_experiment_design.md)：C1 reference-free 传统基线、seed505 零时延场景、输入与停止门。
2. [R16 最小 C1 Production 结果](R16_minimal_c1_results.md)：真实本机 CUDA 的 9 branches、有效面积杂波、CSI/CFAR/Detection/TrackManager 结果与跨 binary 可比性限制。
3. [R16 方向评估](R16_research_direction_assessment.md)：区分固定 C1 基线失败、未知误差因果问题和 AI 必要性；没有自动启动后续实验。
4. [R16 紧凑证据包](evidence/r16_c1_gamma1_seed505/README.md)：执行与 case manifests、runtime XML/log、真值、CSI/CFAR/航迹审计、27 张无损压缩 final-union maps、源码快照和清理账本。Stage2/Production 原始 IQ 与完整 CSI maps 已清理。

## R17 Production executable 身份与 CFAR 可比性离线审计（2026-10-25）

1. [R17 Production 身份审计](R17_production_identity_audit.md)：以 R7 pre-fix manifest 的 binary SHA 定位 R14 实际执行文件，并记录源码/构建身份边界。
2. [R17 CFAR 分歧分析](R17_CFAR_divergence_analysis.md)：核对两阶段 hit 数、有效 CUT、GO-CFAR 参数及旧数值缺陷，区分已证原因与不可逐 CUT 重算的部分。
3. [R17 Baseline 可比性契约](R17_baseline_comparability_contract.md)：规定后续跨方法比较的 binary、输入、运行配置、CFAR 中间量、评价器与信息条件身份要求。
4. [R17 研究方向评估](R17_research_direction_assessment.md)：判断 R14/R15 历史结果的适用边界及唯一有界后续建议；本阶段无新 CUDA 执行。
5. 本次未运行 CUDA。R14 原始 CFAR power/threshold maps 已清理，故虽已定位旧版 executable 与数值缺陷，无法统计 R14 每个 CUT 的错误门限数。
6. [ChatGPT 独立审查请求](00_ChatGPT_review_request_R17.md)：审查 R17 固定 commit `902572c7a9a6f7b91b14ff9db32514be664a58d0`，要求先读取该提交的研究总纲并完成阶段正确性与总体科研价值双层审查。

## R18 Native CUDA 传统方法 Production 矩阵（2026-10-25）

1. [R18 总实施计划与阅读入口](R18/00_master_execution_plan.md)：科研定位、身份、执行范围、质量门和阶段停止状态。
2. [文献与方法能力审查](R18/01_literature_and_method_review.md)、[Production 基准冻结](R18/02_production_frozen_baseline.md)、[信息公平协议](R18/03_information_fairness_protocol.md)及[实验矩阵](R18/04_experiment_matrix.md)。
3. [执行与质量审计](R18/05_execution_and_quality_audit.md)：源码/配置/二进制身份、252 个分支及通过/未通过门。
4. [信号与任务结果](R18/06_signal_and_task_results.md)、[失败机制](R18/07_failure_mechanism_analysis.md)、[机会排序](R18/08_research_opportunity_ranking.md)及[阶段方向裁决](R18/09_next_research_direction.md)。
5. [ChatGPT 独立审查请求](R18/10_ChatGPT_review_request.md)：先读对应 commit 的研究总纲，再作阶段正确性与总体科研价值双层审查。
6. [12 场景 gate CSV](R18/evidence/formal_matrix/summary/r18_cuda_case_gate_summary.csv)、[ON 条件描述统计](R18/evidence/formal_matrix/summary/r18_cuda_on_method_descriptive_summary.csv)和[杂波可比性图](R18/evidence/formal_matrix/summary/r18_cuda_clutter_comparability.png)。每场景完整 manifest、XML/log、CSI/CFAR/Detection/TrackManager 审计位于对应的 formal_matrix/seed*/experiment_snapshot/。
7. [seed909 独立参考次级验证](R18/evidence/independent_seed909_secondary_attempt3/report.md)因 C3 exact-support gate 未通过而为 NOT_EVALUABLE；[清理台账](R18/evidence/cleanup_ledger.md)记录已归档与删除内容。

## R19 R18 saved-data 严格离线任务复评（2026-10-26）

1. [R19 总实施计划](R19/00_master_execution_plan.md)与[证据盘点](R19/01_evidence_inventory.md)：范围、快照、生产身份及可重算边界。
2. [冻结任务评价契约](R19/02_frozen_task_evaluation_contract.md)与[评价器/测试说明](R19/03_evaluator_implementation_and_tests.md)：strict one-to-one matching、H0/CFAR、TrackManager、参数与状态标签。
3. [12-case 任务复评](R19/04_r18_baseline_task_reevaluation.md)、[Mode-A/B 机制边界](R19/05_mode_a_mode_b_failure_mechanism.md)和[seed909 gate 方法审查](R19/06_independent_reference_methodology_review.md)。
4. [总体研究机会评估](R19/07_research_opportunity_assessment.md)、[唯一下一步建议](R19/08_next_research_direction.md)与[执行和质量审计](R19/09_execution_and_quality_audit.md)。
5. [ChatGPT 独立审查请求](R19/10_ChatGPT_review_request.md)：使用时填入最终交付的完整 40 位审查 commit SHA；提示词要求先从指定 commit 读取研究总纲。
6. [Formal matrix CSV 与 manifest](R19/evidence/results/formal_matrix/) 保存完整离线表，其中 `candidate_gate_audit.csv` 逐 opportunity 对照旧 range/position 候选和 strict range-bin/Doppler 门；[pilot 结果](R19/evidence/results/pilot_seed606_v6p7_snr30/)保留代表性检查；[审计 manifest](R19/evidence/manifests/)及[汇总图](R19/evidence/figures/r19_mode_a_mode_b_pipeline_seed606_707_808_v6p7_snr30.png)可复核 R19 结果。R19 未运行 CUDA，历史原始 IQ/部分 maps 按 R18 cleanup ledger 已不存在。
