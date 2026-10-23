# PGRCC Phase R：研究审查与证据导航

更新日期：2026-10-23。本目录判断 GMTI 瓶颈、可恢复空间和研究方向。请区分理论推导、外部文献、软件测试、历史仿真和本轮真实 CUDA Production 结果；它们不可互相替代。

## 当前决策

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

## 尚待决策的门

R10 已完成冻结范围，不存在自动启动的 R11。若继续，建议先由用户批准一个单 period、固定校正量的 Native CUDA 局部验证，检验融合时延估计到物理 ch2 修正的 residual phase 来源；R10 文档中的扫描点和必要 taps 是方案建议，没有执行。R4 低速 P5 背景 ROI、unique union false-hit 真值关系及 PK/PKR evaluator 缺口仍未闭合；R9 也没有统计性 Pfa 结果。当前证据不支持 AI 训练，也不足以直接切换为低速目标或复杂杂波主线。
