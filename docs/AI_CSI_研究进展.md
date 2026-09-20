# 物理模型驱动的智能 GMTI 杂波对消：历史基线与 Phase-I 双通道收束

## 当前状态

当前主线已从“理想/当前链路上的 AI 候选”转为
`Phase-I Unknown System Error Characterization / 双通道系统失配与稳健 CSI`：

```text
真实系统未知误差 → 多通道回波观测 → 误差/状态参数估计
→ 物理模型修正与通道自校准 → 恢复杂波相干性
→ 生产 F1/F2 两通道 CSI → CFAR / Pd / Pfa / 目标保持
```

本轮已完成源码审计、误差参数清单、传播关系、六对紧凑观测量、true/report 几何、
unknown-only geometry estimator、几何扩展矩阵，以及 B0/B1/B1K 生产 CUDA CSI/CFAR、
目标因果传递、Pfa 分母审计、信息量匹配 baseline 和 B2/B3/B3K 离线 STAP reference；
还完成了 target-assisted servo pilot、target-free clutter-only formal、true/report
velocity、yaw-first attitude 和生产 TrackManager/PIPE 连续目标审计，没有训练 AI。机器可读
证据在 `outputs/system_error_inventory/`、`outputs/unknown_system_error_geometry_matrix_20260914_v2/`、
`outputs/unknown_system_error_end_to_end_20260914/`、
`outputs/unknown_system_error_pilot_20260914_clean/`、
`outputs/clutter_only_servo_formal_compact_v2_20260914/`、
`outputs/servo_gmti_e2e_formal_compact_20260914/`、
`outputs/velocity_error_formal_compact_v2_20260915/`、
`outputs/yaw_error_formal_compact_20260915/` 和
`outputs/track_manager_e2e_formal_compact_20260915/`；统一轻量证据入口为
`outputs/formal_evidence/`，详细边界见
[`AI_CSI_33_真实系统误差参数与可观测性分析.md`](AI_CSI_33_真实系统误差参数与可观测性分析.md)。

当前生产 Current 是四通道协议 IQ 经 `(1,3)`、`(2,4)` 融合成 F1/F2 后进入 CSI；
四通道 native STAP 保留四个空间自由度，但已冻结为 Phase-II 独立课题。历史报告中
strict 组与四通道 academic reference 不混排；reference 只用于信息条件审计，不表示
当前 Phase-I 已启动 production STAP。

## 2026-09-17 channel-delay Stage-1.1 收口阅读顺序（历史收口）

本轮新增证据按以下顺序阅读：

1. [`AI_CSI_37_ChannelDelay_Finalization_Audit.md`](AI_CSI_37_ChannelDelay_Finalization_Audit.md)：先确认 Formal-v1 是 development formal，且文件不可覆盖；
2. `outputs/fractional_delay_inverse_closure_20260917/`：读取 C0–C4 closure 及 C4 的 `NOT_EVALUABLE` 原因；
3. `outputs/formal_evidence/stage1_delay_v2_reanalysis_20260917/`：读取 CFAR/TrackManager 归因边界和旧 v1 switch 的保守重分析；
4. `outputs/formal_evidence/stage1_delay_hierarchical_v1_exploratory_20260917/`：读取 scene-block 层级统计和 duplicate/missingness；
5. `configs/research/channel_delay_stage1_materiality.json` 与当前传统 baseline：确认阈值预注册但仍 pending，且比较没有越界为系统 Pd/Pfa claim；
6. [`ChannelDelay_Calibration_Novelty_Audit.md`](literature/ChannelDelay_Calibration_Novelty_Audit.md) 与 [`ChannelDelay_Hardware_Validation_Protocol.md`](experiments/ChannelDelay_Hardware_Validation_Protocol.md)：确认新颖性和硬件结果边界；
7. [`AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md`](AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md)：读取当前 Formal-v2 A–L 矩阵和限制。

截至历史记录时，Formal-v2 尚未冻结；当前状态见下节。固定开关为
`ai_training=false`、`router_enabled=false`、`native_four_channel_stap=false`。

## 2026-09-20 Formal-v2 当前状态

独立 Formal-v2 已完成 `135/135` 个 case，覆盖 15 个 physical block 和 9 个 delay，证据
已压缩到 `outputs/formal_evidence/stage1_delay_v2_full_20260917/`，分层统计在
`outputs/formal_evidence/stage1_delay_v2_full_20260917_hierarchical/`。run manifest 记录
source before/after 相同、tracked dirty=false；raw case 树已在 manifest、compact CSV 和
135 个 case manifest 归档验证后删除，清理记录见 `docs/清理记录_2026-09-17.md`。

当前 A–L 不是全通过：C4 production input、valid-CUT denominator 和 A2/A0 residual
单因素分解仍为 `NOT_EVALUABLE`，因此 Stage-2A 为 `NO_GO_STAGE1_INCOMPLETE`。在项目
决策者书面允许这些边界前，不启动 coupled-error runner、Physics-AI 或 Router。完整
结果和最短复现命令见 [`AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md`](AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md)。

## 2026-09-16 channel-delay 单误差确定性自校准

本轮把 channel delay 定义为 Phase-I 的第一个论文级单误差模板，新增 D1 ordinary
LS、D2 magnitude/power weighted LS、D3 Huber weighted LS、cross-correlation 与
GCC-PHAT 传统 baseline，并固定 A0 Ideal、A1 Current unknown-error、A2 Known-error
correction upper bound、A3 Blind target-free estimated correction 四条件。生产输入
仍严格是 4ch protocol IQ → F1/F2 → two-channel CSI；A2/A3 使用 fractional-delay
physical correction，A3 只从 A1 OFF target-free data 估计，truth 不进入 estimator。

本轮 runner 同时生成 ON/OFF/TO paired control、target-off 四层 waterfall、生产
TrackManager/PIPE 的同周期 `Confirmed + matched_this_frame` 反查、ID-switch 分类和
paired McNemar/bootstrap 统计。Level-1 为每个选定 delay/SNR 100 trials 的 estimator
Monte Carlo，Level-2 为 5-period CUDA production case；delay sweep 覆盖 `0, ±1, ±2,
±4, ±8 ns`，registered group 保持 group-scoped，不制造不支持的 Cartesian product。
正式命令、理论误差传播、限制和 multi-error gate 见
[`AI_CSI_36_单一系统误差确定性自校准阶段报告.md`](AI_CSI_36_单一系统误差确定性自校准阶段报告.md)，
论文结构见 [`Paper1_ChannelDelay_SelfCalibration_Outline.md`](papers/Paper1_ChannelDelay_SelfCalibration_Outline.md)。

正式源产物使用独立 `outputs/formal_delay_stage1_20260916/`，现已完成 135/135 case，
5 方法 Monte Carlo 为 135 行、每格 100 trials 且 `passed`；九个轻量文件已收敛到
`outputs/formal_evidence/stage1_delay/`。27 个 delay/SNR 参数点的平均 estimator RMSE
为 D1/D2/D3=`0.053395/0.053359/0.054587 ns`，cross-correlation/GCC-PHAT 为
`2.027616/2.990429 ns`。生产层按 135 个 case block 的 A1→A3 平均变化为 target-period
Pd `0.9556→0.9793`、position RMSE `220.4825→161.4688 m`、angle RMSE
`0.1358→0.0969 deg`；但 ID switch 为 `248→290`，且工程 materiality threshold 尚未
预注册，因此只报告描述性/配对统计，不宣称无条件整体收益。正式源树已按
[`清理记录_2026-09-16.md`](清理记录_2026-09-16.md) 删除原始/中间 case 产物，保留
root manifest、MC 汇总和逐 case 配置/生产审计 manifest；逐文件 cleanup 记录只保留在
root manifest，pilot 与沙箱失败证据仍分开保存。

当前 Phase-I 固定把 TrackManager/PIPE 也纳入同一条闭环：
`4ch protocol IQ → F1/F2 → CTDR/phase/P38 → CSI → GO-CFAR → clustering/positioning
→ TrackManager/PIPE`。native four-channel STAP/JDL/covariance/loading/DOF/CUDA 优化
冻结为 Phase-II；`ai_training=false`、`router_enabled=false`。

以下 V1/V2、Physics-AI 和 Router 结果均保留为历史证据；不要把旧阶段的主线标题、
Oracle 术语或 Router 状态当作当前待办。

## 2026-09-16 Phase-I 双通道可观测性与校正证据

- F1/F2 observability runner `scripts/audit_two_channel_error_observability.py` 完成
  54/54 Stage2 cases（6 状态 × zero/+/− × 3 seed），只使用
  `F1=(C1+C3)/2`、`F2=(C2+C4)/2` 科学输入；13 个观测覆盖 frequency/pulse/angle、
  range-block、P38、ridge、CTDR、coherence 和 CSI residual。行平衡后的 sensitivity
  matrix 为 scaled rank=`6`、condition number=`37.3283545882`，没有缺失灵敏度。
- delay/phase drift 的平衡列余弦为 `−0.09393`；geometry 有 phase-angle slope；
  velocity 保留 ridge/P38/CTDR/slow-time 分列；delay/servo 为 near-confounding
  (`−0.91995`)，servo/yaw 为 `0.18023`。六状态均只能写 candidate；未加外部约束的
  delay+servo 联合状态写 `not independently observable from current two-channel data`。
- 新证据目录为 `outputs/two_channel_error_observability_phase_i_20260916/`，包含 manifest、
  raw/scaled matrix、pair-level classification、zero-control delta、range/frequency/block
  coverage 和 seed stability；此前无有效快时间
  信号的结果保留在 `outputs/two_channel_error_observability_invalid_zero_signal_20260915/`
  且不引用。
- 首个 TrackManager/PIPE channel-delay correction CUDA smoke 已从 target-free raw
  估计后实际施加 fractional-delay correction；Current/blind/known 三分支的生产运行、
  track_debug 和同周期 confirmed/matched payload reverse audit 通过。该结果只有 1 seed、
  1 速度、1 SNR、3 周期，不能写成正式收益。最新 4ch 产物为
  `outputs/track_delay_smoke_4ch_gpu_reaudit_20260916/`。
- TrackManager/PIPE channel-delay formal 已完成：
  `outputs/track_delay_formal_4ch_local_20260916/` 覆盖 5 periods × 3 seeds × 2 target
  velocities × 2 SNR，共 12/12 case、36/36 branch；所有输入/校准/runtime XML 审计、生产
  `track_debug`、TrackManager audit（0 violation）和同周期 `Confirmed + matched_this_frame`
  payload reverse audit 通过。Current/blind/known 的加权 target-period Pd 为 `55/60`、
  `59/60`、`59/60`，all-visible Track Pd 为 `42/60`、`47/60`、`47/60`；ID switch 总数
  `17→23`，所以只报告描述性差异，不宣称整体收益。blind delay 估计 bias=`+0.0590 ns`、
  RMSE=`0.0610 ns`，残差范围 `+0.0413…+0.0788 ns`；known 残差为 0。该正式运行是
  local-input 生产 core，不是 SHM 吞吐基准；target-on 正例没有 valid-CUT/target-off
  分母，cell-Pfa/false-hit 保持 `not_evaluable`。
- Stage-1 单一 channel-delay 确定性自校准 formal 已完成：
  `outputs/formal_delay_stage1_20260916/manifest.json` 为 `completed`，135/135 case
  完成，MC 为 135 行 × 5 方法 × 100 trials；紧凑证据为
  `outputs/formal_evidence/stage1_delay/` 的九个文件。D1/D2/D3 平均 RMSE 分别为
  `0.053395/0.053359/0.054587 ns`，cross-correlation/GCC-PHAT 为
  `2.027616/2.990429 ns`。A1→A3 平均 target-period Pd=`0.9556→0.9793`、position
  RMSE=`220.4825→161.4688 m`、angle RMSE=`0.1358→0.0969 deg`，但 ID switch
  `248→290`；cell false-hit 因 valid-CUT 分母为 0 保持 `NOT_EVALUABLE`，ID audit
  1040 行均保守为 `other`。原始/中间 case 产物已按
  [`清理记录_2026-09-16.md`](清理记录_2026-09-16.md) 清理，未与 pilot 合并。
- 后续去相关配置 `configs/research/two_channel_decorrelation_study.json` 目前仅为
  contract-only pending；不启动 AI/Router，也不打开 Phase-II native 4ch STAP。

## 2026-09-14 当前阶段实际证据

- Phase0 clean archive paired-reference baseline-phase sanity 已运行：源码 `823ebae`、
  `worktree_dirty_before=false`，10 mm 基线估计误差约 `7.46e-11 m`，恢复比
  `0.9999999991`；manifest 明确 `operational_blind=false`。
- Phase1/2 unknown-only estimator 使用单份 unknown-off 四通道 IQ、reported geometry
  和过滤后的 nominal metadata；三速度 E2E calibration 估计 `2.65 mm`，外部评价真值
  `2.50 mm`，四个水平 pair 间最大差 `0.025 mm`，global phase RMSE `0.007436 rad`。
- Phase3 覆盖 `[0, ±1, ±2.5, ±5, ±10] mm × 3 seeds`，27/27 six-pair 和 C12
  single-pair 均 fit，无 failure/fallback。six-pair 全部 case 的绝对误差均值/最大值
  为 `0.1586/0.1825 mm`；扩展到 `[0, ±0.5, ±1, ±2.5, ±5, ±10] mm × 3 seeds`
  后为 33/33 fit，无 failure/fallback；零误差 floor 为 `0.159 mm`，由基线 sweep
  推导的 deadband 为 `0.165 mm`。
- Phase4 三个 moving-target velocity `(-3,2),(-6,4),(-12,8) m/s` 已完成 27 条
  生产行和 9 条离线 STAP 行。B0 target-on Pd=`0/3`，B1 blind 和 B1K known 均为
  `3/3`；target transfer 审计显示 B1 相对 B0 的 detector-input 因果功率传递约 `0 dB`，
  Pd 差异来自候选/真值门控与位置结果，不是 CSI 目标幅度增益。
- Pfa 审计拆分了 valid CUT、dynamic/split branch、hit/cluster 与 GO-CFAR 分母：历史
  `0.0022` 使用了不一致分母；当前完整 valid-CUT 约 `6.49–6.60e-4`，仍显著高于
  配置 `1e-6`，因此暂不调阈值或宣称绝对 Pfa 已解释。
- 信息量匹配离线矩阵已完成 M0 production/controlled Current、M1 adaptive two-channel、
  M2 pair-fused equivalent two-channel、M3 native four-channel composite baseline，
  并保存 pairwise attribution；M3 的结果仍标注为算法与空间自由度混合的 scientific reference。
- target-assisted servo calibration pilot 已完成 18 个
  `0, ±0.05, ±0.1, ±0.2, ±0.5° × 2 seeds` Stage2 paired cases 和 54 条 Core 分支；
  true angle 驱动回波/增益/LOS，reported angle 驱动 header/processing。estimator 使用
  paired ON-OFF target residual、六对 phase/coherence 和 nominal target/range hypothesis，
  mean bias/RMSE 为 `-0.0108°/0.0138°`；该结果是 offline target-assisted calibration
  pilot，不是 online 或 target-free 能力，AI/Router 保持关闭。
- target-free clutter-only servo formal 已完成 72 个 Stage2 case（9 个误差 × 2 seed
  × 2 texture × 2 range），S1 全部 72/72 因 feature-family disagreement 触发
  `FALLBACK_MODEL_MISMATCH` 并保持 Current；S1 的输入审计为 OFF C+N only，未使用
  target truth、ON-OFF 差分、known error 或 servo truth。独立的 Sassist 72/72 为
  `VALID`，bias/RMSE=`−0.00817°/0.06691°`，但它是 target-assisted 参考，不能归入
  clutter-only 能力。formal 只跳过 Core 并使用显式 compact estimator input；修复后
  2-case CUDA smoke 的四分支 8/8 Core 成功，均不构成生产性能或 Pd/Pfa 结论。
- moving-target servo E2E formal 已完成 96 个 case（`target_free`、slow-near-ridge、medium、fast
  × 2 error × 3 seed × 2 texture × 2 range），ON/TO Stage2 各 96/96 成功。S1 估计器的
  96/96 条输入均为 OFF C+N，未使用 ON、TO、target truth、known servo error 或 servo truth；
  四个含目标场景的 S1 仍全部 `FALLBACK_MODEL_MISMATCH`，target-free 的 Sassist 标为
  `NOT_APPLICABLE_TARGET_FREE`。本轮为 compact estimator-only formal，Core 跳过，因此
  e2e Pd、false-hit、target transfer 和 TrackManager/PIPE 均明确 `not_evaluated`。
- moving-target 的代表性 CUDA smoke（slow-near-ridge、0.2°、seed 101、low texture、8.75 km）
  的 12 个 Core 行均 exit 0，但 4 个 TO 行触发内部 beam-quality gate（4/5 < 0.95），只有
  8/12 行内部质量有效；P4 target match 为 0/4，不能形成正向 Pd 或目标保持结论。
- Pfa H0–H5 closure 已完成独立 CUT 分母审计：canonical 输出含 18 行、18,000,000 个
  有效且不重复 CUT；H0 实测 cell-Pfa 为 `4/3,000,000=1.3333e-6`，Wilson 95% CI
  为 `[5.19e-7,3.43e-6]`。H1–H5 是结构杂波 false-hit control，不是 Pfa，不能写成
  “配置 `1e-6` 已达到”。证据见 `outputs/unknown_system_error_pfa_closure_20260914/`。
- 信息量匹配纯空间自由度矩阵已完成 9 cases（3 seed × 3 velocity）和 9 对 J4−J2：
  J2 为生产 `(1,3)/(2,4)` pair-fused two-channel JDL/STAP，J4 为 native four-channel
  JDL/STAP；平均 output-SCNR 分别为 `31.7349/24.1102 dB`，纯 DOF delta 为
  `−7.6247 dB`，background Pfa delta 为 `+1.9155e-4`。当前没有稳定、material 的
  J4 优势，因此 production CUDA 4ch STAP 仍关闭。证据见
  `outputs/unknown_system_error_pure_spatial_dof_20260915/`。
- 平台速度 true/report split 已完成：Stage2 用 `velocity_true_mps` 驱动物理轨迹、echo
  phase、clutter Doppler 和 target geometry；`velocity_reported_mps` 写入 header，并由
  `new_protocol_velocity_source=header` 进入处理路径。`0, ±0.05, ±0.1, ±0.2, ±0.5 m/s`
  × 3 seed × 2 texture × 2 range 的 108-case compact formal 全部 Stage2 成功，header
  最大误差 `1.53e-6 m/s`；V1K 仅作 known-error evaluation，恢复误差约 0。盲确定性
  estimator 108/108 因观测模型不一致或超出显式 `1.0 m/s` 支持边界回退 Current，未形成
  正向 blind correction claim。代表 CUDA smoke 的 V0/V1K/V1 3/3 Core 成功，运行时 dump
  均回显 true=60、reported=60.2。证据见
  `outputs/velocity_error_formal_compact_v2_20260915/`、`outputs/velocity_error_cuda_smoke_20260915/`。
- yaw-first 姿态 pilot 已完成：保持 pitch/roll=`0`，把 baseline、yaw 和 servo 分成
  三个互斥条件；覆盖 `target_free/moving_target × 3 conditions × {0, ±0.5, ±1}° ×
  2 seeds × 2 textures × 2 ranges`，共 240/240 Stage2 case、720 条 Current/known/blind
  决策。盲估计器逐案只读取一个 OFF C+N 输入，240/240 为
  `FALLBACK_MODEL_MISMATCH` 并保持 Current，0 条 blind estimate 被应用；known branch
  只是 evaluation-only。moving-target 的 paired target-assisted reference 单独保留，
  不能区分 yaw 与 servo 原因；本 compact 阶段未运行 Core、TrackManager/PIPE、Pd 或 Pfa。
  证据见 `outputs/yaw_error_formal_compact_20260915/`，源字段和约束见
  `scripts/run_yaw_error_study.py`。
- 生产 TrackManager/PIPE 连续目标正式审计已完成：3 个 seed × `Current`、
  `blind_calibrated`、`known_error_calibrated` 三个分支，每个分支 3 个连续周期，共 9 行
  branch metrics、27 个 SHM 周期和 27 个 PIPE 结果。全部运行均为 390/390 valid PRT、
  `ring_overrun=0`、`gaps=0`、`duplicates=0`，TrackManager 审计 9/9 `pass` 且 0 violation；
  协议载荷 294 行均可追溯到 production track_debug 的同周期确认关联链。周期去重后
  `track Pd(all visible)=2/3`，确认窗口后的 `track Pd=2/2`；false-track rate 为
  `0.8333–0.8824`，因此只证明链路和因果审计成立，不证明低假轨或在线校准收益。
  blind 分支明确 `fallback_to_current`，known 分支为 `evaluation_only`，两者均未注入校正，
  AI/Router 仍关闭。证据见 `outputs/track_manager_e2e_formal_compact_20260915/` 和
  `outputs/formal_evidence/track_contract.json`。
- 本次清理删除了已失败/被 supersede 的 raw smoke 与逐案例 BIN/PNG/F32，保留命令、配置、
  truth、manifest、关键日志和审计 CSV；失败的 SHM overrun 与修正后的限速 smoke 只保留在
  `outputs/track_manager_e2e_cleanup_summary_20260915.json`，清理清单见
  `outputs/cleanup_manifest_20260915.json`。
- 本阶段 `ai_training=false`；不训练 MLP、通用 `delta-alpha`、RD image-to-image 或 Router。

2026-09-10 已完成生产相位修复迁移后的 CUDA 正确性基线、残余纹理诊断、Stage2
物理假设审计和 M1/M2/M3 model-mismatch challenge 的 3-seed 扩展；仍未训练 AI。formal
challenge/control/diagnostic/oracle manifest 已记录实验代码提交
`09bbd624c063126a97ea02196394ea62d3b4c4a6` 与最终 `worktree_dirty=false`。新的阅读顺序为：
[`AI_CSI_11_PhaseFix迁移与历史影响审计.md`](AI_CSI_11_PhaseFix迁移与历史影响审计.md) →
[`AI_CSI_12_PhaseCorrected_Current基线报告.md`](AI_CSI_12_PhaseCorrected_Current基线报告.md) →
[`AI_CSI_13_修复后CSI残余纹理诊断.md`](AI_CSI_13_修复后CSI残余纹理诊断.md) →
[`AI_CSI_14_Stage2物理假设审计.md`](AI_CSI_14_Stage2物理假设审计.md) →
[`AI_CSI_15_ModelMismatch_Challenge报告.md`](AI_CSI_15_ModelMismatch_Challenge报告.md)。
当前 formal failure map 为 `outputs/ai_csi_model_mismatch_formal_clean/failure_map.csv`，3-seed 补充为
`outputs/ai_csi_model_mismatch_multiseed_formal_clean/failure_map_multiseed.csv`；M1/M2/M3 强档
分别存在 deterministic mechanism Oracle；随后已对 36 个 Current variant 补跑 72 个
paired target-only/negative-control CUDA 运行，failure map 已有 Pd/Pfa/target-loss
三类指标，但总体仍保持 AI No-Go。

本轮继续工作已补充：M3 修正后的正式四档 temporal diagnostics、raw fast-time M1/M2
observable estimator、12 个 mixed/OOD CUDA production case、mechanism feature/confusion
table，以及四个代表 mixed 场景的 causal/fixed-Pfa paired audit。新增报告阅读顺序为
[`AI_CSI_16_M3_Decorrelation模型修正与上界审计.md`](AI_CSI_16_M3_Decorrelation模型修正与上界审计.md)
→ [`AI_CSI_18_M1M2物理参数估计器报告.md`](AI_CSI_18_M1M2物理参数估计器报告.md)
→ [`AI_CSI_17_CausalDetection与FixedPfa评价.md`](AI_CSI_17_CausalDetection与FixedPfa评价.md)
→ [`AI_CSI_19_PhysicsAI_GoNoGo报告.md`](AI_CSI_19_PhysicsAI_GoNoGo报告.md)。随后已完成
Physics-Adaptive Joint Calibration V1 的 24/8/32 正式 CUDA 矩阵，新增阅读入口为
[`AI_CSI_20_PhysicsAdaptiveJointCalibrationV1_正式统计报告.md`](AI_CSI_20_PhysicsAdaptiveJointCalibrationV1_正式统计报告.md)。
V1 的预注册 gate 为 `REOPEN_CANDIDATE`，但这不是 AI GO：J3/J4 Oracle recovery
median 均为 0、95% bootstrap 上界约 0.026，且 held-out Pfa=0.001/0.01 存在轻微回归；
当前仍为 `NO_GO_AI_FOR_NOW`，没有训练 AI。正式 compact evidence 保存在
`outputs/physics_adaptive_joint_v1_formal/`；本轮 raw BIN/NPY/log/PNG 的清理记录在
`outputs/cleanup_manifest_20260910.json`。

随后完成了 selective Physics Calibration V2 的 targeted CUDA screen：12 个 null
场景、24 个 `M1/M2/M1+M2/M3/M1+M3/M2+M3/M1+M2+M3/zero` 组合×强度 screen 场景，
实际运行 J0–J6、生产 GO-CFAR 和 scene-block bootstrap；J5 四分支均出现，J6
出现 D0P0/D1P0，screen 规则输出 `REOPEN_PHYSICS_AI`，但这不是 Test-V2 最终 gate。
screen source commit 为 `451dfed`，仍未训练 AI。V2 的失效归因、方法说明、生产
CFAR/泛化统计和最终 gate 分别见 [`AI_CSI_21_JointGate失效归因审计.md`](AI_CSI_21_JointGate失效归因审计.md)、
[`AI_CSI_22_SelectivePhysicsCalibrationV2.md`](AI_CSI_22_SelectivePhysicsCalibrationV2.md)、
[`AI_CSI_23_ProductionCFAR与泛化统计.md`](AI_CSI_23_ProductionCFAR与泛化统计.md)、
[`AI_CSI_24_PhysicsAI_FinalGate.md`](AI_CSI_24_PhysicsAI_FinalGate.md)。

随后已完成 fresh Test-V2 formal：48 null、16 validation、64 held-out test，8 个
test family 各 8 个；J0–J6、evaluation-only Oracle、生产 GO-CFAR、scene-block
bootstrap、0.5/1 dB recovery 和 fallback opportunity cost 均已实际运行。原始 CUDA
运行来自 `669df38` 的 clean worktree，formal 结果在完整逐场景记录上由 `1445f3f`
重算 gate 摘要；当前 `ai_training=false`，最终 gate=`UNRESOLVED`。J5/J6 的 0.5 dB
material recovery median 分别为 `0.8426/1.0249`，但 target preservation median
从 Current 的 `+0.19479 dB` 回落至 `+0.00032/-0.00978 dB`，因此不能进入 NO_GO
或 REOPEN，仍不训练 AI。formal 紧凑证据保存在
`outputs/physics_adaptive_selective_v2_formal/`，生产 CFAR 副本在
`outputs/production_cfar_formal/`；raw runtime 中间文件已清理。

2026-09-12 的后续审计完成了 Target-Safe Oracle v2、失败机制审计和 J8
Clutter-Support-Only 定向 CUDA replay。Oracle 在 12 个 development scenes 上得到
`safe_oracle_headroom_db=+0.0665415 dB`，但 J5/J6/J7 尚未恢复该余量；修正后的最终
门禁为 `REOPEN_AI_ROUTER`（Test-V3 仅保留 development diagnostics，不参与最终 gate），
这不是 AI 训练或部署授权。四个代表失败 scene 的机制审计
显示 phase surface 近线性，`test_v3_010` 的 J6 target-only transfer 为 `−0.348 dB`
并标记为疑似过校正。J8 三场景六候选均未通过目标安全约束，因此仍保持
`ai_training=false`。推荐阅读顺序为 [`AI_CSI_29_TargetSafeOracle_v2审计.md`](AI_CSI_29_TargetSafeOracle_v2审计.md)
→ [`AI_CSI_30_TargetSafeFailureMechanism审计.md`](AI_CSI_30_TargetSafeFailureMechanism审计.md)
→ [`AI_CSI_31_J8ClutterSupportOnly审计.md`](AI_CSI_31_J8ClutterSupportOnly审计.md)。

Router Opportunity 是已结束的历史分支，不是当前主线。2026-09-13 的 32-scene
结果为 `NO_GO_AI_ROUTER_VALUE`：equal-family mean safe headroom `0.0047138 dB`，
`P(headroom >= 0.10 dB)=0.03125`，触发预注册 early-stop，不扩展 64/96，也不启动
observable-only Learnability audit 或 AI training。该结论只说明 Current/J5/J6
Router 的安全平均材料性不足；不否定未知 INS、伺服、平台运动、通道同步和基线几何
误差的观测、自校准或 Physics-AI 后续可能性。紧凑证据保留在
`outputs/router_opportunity_v1/`；历史阅读顺序为
[`AI_CSI_32_RouterOpportunity与Materiality审计.md`](AI_CSI_32_RouterOpportunity与Materiality审计.md)
→ [`AI_CSI_33_RouterLearnability审计.md`](AI_CSI_33_RouterLearnability审计.md)。

## 历史阶段已完成事项

| 阶段 | 状态 | 证据 |
|---|---|---|
| 初始化副本与版本管理 | 完成 | 源工程复制到当前目录；Git 元数据使用只读 `.git` 挂载旁的显式 `.git-real`，已提交基线 `4747bd8` |
| 当前 CSI 数学模型 | 完成 | [`AI_CSI_01_当前对消数学模型.md`](AI_CSI_01_当前对消数学模型.md) |
| 典型场景 | 完成 | 均匀连续纹理、无通道损伤、单波位、单目标、130 PRT |
| 非理想场景 | 完成 | 非均匀纹理 + 2.5 dB 幅度误差 + 18° 固定相位误差 + 3° 相位抖动 + 40/130 通道丢失 |
| Current/Oracle 单因素与 LOO | 完成 | 三个 CSV 汇总 |
| 指标补充 | 完成 | `target_loss_dB`、`residual_high_energy_points`、`false_alarm_count`、`Pd` |
| 失配归因与 AI 建议 | 完成 | [`AI_CSI_02_Current_Oracle实验报告.md`](AI_CSI_02_Current_Oracle实验报告.md)、[`AI_CSI_03_后续AI方法建议.md`](AI_CSI_03_后续AI方法建议.md) |
| 传统 baseline 方法矩阵 | 完成 | [`AI_CSI_04_Baseline方法与实现说明.md`](AI_CSI_04_Baseline方法与实现说明.md)、[`AI_CSI_05_Baseline实验报告.md`](AI_CSI_05_Baseline实验报告.md) |
| Baseline 不足与 Physics-AI 接口 | 完成 | [`AI_CSI_06_Baseline不足与Physics_AI方向分析.md`](AI_CSI_06_Baseline不足与Physics_AI方向分析.md) |
| Baseline V2 物理 steering、协方差政策、多 seed screen、velocity/MDV、Pd/ROC/Pareto | 完成（CPU 离线） | [`AI_CSI_07_Baseline_V2审计与实验报告.md`](AI_CSI_07_Baseline_V2审计与实验报告.md) |
| PGRCC-v1 Oracle Headroom Audit | 完成，No-Go；未进入训练 | [`AI_CSI_09_PGRCC_Oracle与数据集设计报告.md`](AI_CSI_09_PGRCC_Oracle与数据集设计报告.md)、`outputs/pgrcc_oracle/oracle_audit_manifest.json` |
| M1/M2/M3 paired controls | 完成，36 target-only + 36 negative-control，未训练 AI | `outputs/ai_csi_model_mismatch_metric_controls_formal_clean/manifest.json`、`outputs/ai_csi_model_mismatch_formal_clean/failure_map.csv` |
| Physics-Adaptive Joint Calibration V1 正式矩阵 | 完成，24/8/32 场景；gate=`REOPEN_CANDIDATE`，仍不训练 AI | [`AI_CSI_20_PhysicsAdaptiveJointCalibrationV1_正式统计报告.md`](AI_CSI_20_PhysicsAdaptiveJointCalibrationV1_正式统计报告.md)、`outputs/physics_adaptive_joint_v1_formal/formal_matrix_manifest.json` |
| Selective Physics Calibration V2 targeted CUDA screen | 完成，12 null + 24 screen；仅为定向筛查，非最终 Test-V2 | [`AI_CSI_22_SelectivePhysicsCalibrationV2.md`](AI_CSI_22_SelectivePhysicsCalibrationV2.md)、`outputs/physics_adaptive_selective_v2_screen/formal_matrix_manifest.json` |
| Selective Physics Calibration V2 formal Test-V2 | 完成，48/16/64 场景；gate=`UNRESOLVED`，target preservation 回归，仍不训练 AI | [`AI_CSI_22_SelectivePhysicsCalibrationV2.md`](AI_CSI_22_SelectivePhysicsCalibrationV2.md)、[`AI_CSI_24_PhysicsAI_FinalGate.md`](AI_CSI_24_PhysicsAI_FinalGate.md)、`outputs/physics_adaptive_selective_v2_formal/formal_matrix_manifest.json` |
| Production CFAR formal 泛化统计 | 完成，1792 条 production CFAR 记录，held-out scene-block bootstrap | [`AI_CSI_23_ProductionCFAR与泛化统计.md`](AI_CSI_23_ProductionCFAR与泛化统计.md)、`outputs/production_cfar_formal/production_cfar_rows.csv` |
| Router Opportunity 研究基础设施 | 历史分支已完成 32-scene CUDA/Materiality；因 `NO_GO_AI_ROUTER_VALUE` 停止扩展，未启动 Learnability | [`AI_CSI_32_RouterOpportunity与Materiality审计.md`](AI_CSI_32_RouterOpportunity与Materiality审计.md)、[`AI_CSI_33_RouterLearnability审计.md`](AI_CSI_33_RouterLearnability审计.md) |

## 当前阶段启动清单

| 项目 | 当前状态 | 证据/下一步 |
|---|---|---|
| 真实系统误差参数盘点 | 已完成代码审计初版 | [`AI_CSI_33_真实系统误差参数与可观测性分析.md`](AI_CSI_33_真实系统误差参数与可观测性分析.md)、`outputs/system_error_inventory/parameter_inventory.csv` |
| 误差传播与可辨识性 | 第一版关系已审计；基线几何已完成小规模数值验证 | `outputs/system_error_inventory/propagation_map.csv`、`outputs/unknown_system_error_pilot_20260913_v5/calibration_phase_difference.json` |
| Current 与四通道 STAP 比较口径 | 能力层与信息量匹配层均已运行；J4 仍为离线纯 DOF reference，未形成 production 4ch STAP 优势 | `outputs/unknown_system_error_end_to_end_20260914/manifest.json`、`outputs/unknown_system_error_pure_spatial_dof_20260915/` |
| Phase0 paired-reference baseline-phase sanity | `run`，明确非 blind/online | `outputs/unknown_system_error_pilot_20260914_clean/manifest.json`；估计误差 `0.009999999925 m`，恢复比 `0.9999999991` |
| Phase1/2 true/report geometry + blind estimator | `run`，单参数 baseline geometry | `outputs/unknown_system_error_end_to_end_20260914/calibration/unknown_only_estimator.json` |
| Phase3 blind geometry matrix | `run`，27/27 fit、无 fallback | `outputs/unknown_system_error_geometry_matrix_20260914_v2/matrix_summary.csv`、`single_pair_vs_six_pair.csv` |
| Phase4 production CSI/CFAR + offline STAP | `run`，B0/B1/B1K CUDA；B2/B3/B3K offline reference | `outputs/unknown_system_error_end_to_end_20260914/production_metrics.csv`、`offline_stap_metrics.csv` |
| Phase5 recovery/Pd/Pfa/target transfer | `run`，受控 moving-target fixture | `recovery_metrics.csv`、`target_only_transfer.csv`、`target_off_false_cluster_metrics.csv` |
| Phase6 geometry correction + servo pilot | 几何矩阵、target-assisted pilot 与 target-free clutter-only formal 已运行；S1 当前全 fallback；平台速度随后单独审计 | `outputs/unknown_system_error_geometry_matrix_20260914_v2/`、`outputs/unknown_system_error_pilot_20260914_clean/`、`outputs/clutter_only_servo_formal_compact_v2_20260914/` |
| Phase7 moving-target servo E2E | formal 96 cases 的 OFF-only 输入和 Stage2 角色链已运行；compact formal 跳过 Core；代表性 CUDA smoke 的 TO 内部质量 gate 失败，未形成 Pd/PIPE 正向结论；raw case 树已清理 | `outputs/servo_gmti_e2e_formal_compact_20260914/`、`outputs/servo_gmti_e2e_cuda_smoke_20260914/` |
| Phase8 Pfa H0–H5 closure | 18 rows、18M 独立 CUT；H0 cell-Pfa `1.3333e-6`，H1–H5 仅为结构杂波 false-hit controls | `outputs/unknown_system_error_pfa_closure_20260914/` |
| Phase9 pure spatial DOF J2/J4 | 9 cases；J4−J2 平均 output-SCNR `−7.6247 dB`，未形成 production 4ch STAP 优势 | `outputs/unknown_system_error_pure_spatial_dof_20260915/` |
| Phase10 platform velocity true/report | 108-case compact formal + 3-row CUDA smoke；header 分离通过，blind estimator 全部 fallback | `outputs/velocity_error_formal_compact_v2_20260915/`、`outputs/velocity_error_cuda_smoke_20260915/` |
| Phase11 yaw-first attitude | 240-case compact formal；pitch/roll 固定 0，blind estimator 240/240 fallback，未形成正向修正结论 | `outputs/yaw_error_formal_compact_20260915/` |
| Phase12 production TrackManager/PIPE | 3 seed × 3 branch × 3 period；9/9 SHM/PIPE pass、0 ring overrun/gap/duplicate、9/9 protocol audit pass；Track Pd 2/3 all-visible、2/2 after confirmation，false-track rate 0.8333–0.8824；无校正收益 claim | `outputs/track_manager_e2e_formal_compact_20260915/`、`outputs/formal_evidence/track_contract.json` |
| Phase-I F1/F2 unknown-error observability | 完成 54/54 Stage2 cases；6 状态、13 观测；row-balanced scaled rank=6、condition=37.328；zero-control delta=0；delay/servo pair-level near-confounding，其他结论仍为 candidate | `outputs/two_channel_error_observability_phase_i_20260916/manifest.json`、`observability_summary.json`、`pair_observability.csv`、`zero_control.json`、`sensitivity_matrix_scaled.csv` |
| Phase-I channel-delay TrackManager correction smoke | 真实 4ch protocol CUDA 三分支实际施加/审计通过；1 seed × 1 velocity × 1 SNR × 3 period，仅证明链路契约 | `outputs/track_delay_smoke_4ch_gpu_reaudit_20260916/manifest.json`、`track_branch_metrics.csv`、`track_protocol_payload_audit.csv` |
| Phase-I channel-delay TrackManager formal | 5 periods × 3 seed × 2 velocity × 2 SNR；12/12 case、36/36 branch、runtime/TrackManager/payload audit 全通过；Current→blind/known 的 target-period Pd `55/60→59/60`，ID switch `17→23`；cell-Pfa/false-hit 为 not_evaluable | `outputs/track_delay_formal_4ch_local_20260916/manifest.json`、`track_branch_metrics.csv`、`track_protocol_payload_audit.csv` |
| Phase-I channel-delay Stage-1 deterministic formal | 15 registered blocks × 9 delays、135/135 case；MC 135 行 × 5 方法 × 100 trials；A0/A1/A2/A3、ON/OFF/TO、TrackManager/PIPE、ID-switch 和 paired statistics 已收口；cell false-hit 无 valid-CUT 分母、ID 1040 行均为 `other` | `outputs/formal_delay_stage1_20260916/manifest.json`、`outputs/formal_evidence/stage1_delay/`、[`AI_CSI_36_单一系统误差确定性自校准阶段报告.md`](AI_CSI_36_单一系统误差确定性自校准阶段报告.md) |
| Phase-I temporal decorrelation entry | 已建立 contract-only 配置；runner、五方法 sweep、固定 Pfa/目标安全统计待运行 | `configs/research/two_channel_decorrelation_study.json` |
| AI 训练 | 未进行，按计划关闭 | `ai_training=false`；先完成确定性估计和残差证据 |

## V1 历史实验事实

以下数字属于此前的 V1 单 seed/7 场景记录，保留用于历史回归，不是当前 V2 的验收标准。

- 两类场景目标均为 `row_truth=60`、`expected_bin=2200`、`af_total_truth=-53.2469 Hz`；
  动态 CSI 支撑为行 42–88。
- Typical Current 的 SCNR 提升为 21.632 dB，Oracle All 为 16.761 dB。
- Non-ideal Current 的 SCNR 提升为 19.666 dB，Oracle All 为 14.333 dB。
- Oracle All 相对 Current 的有符号差距分别为 -4.871 dB 和 -5.333 dB；按非负
  定义的可回收正差距均为 0 dB。
- 负差距不是把结果修剪成“Oracle 更好”，而是记录了当前非线性最小幅度算子
  与背景-only 线性复权之间的真实性能/统计权衡。

## V2 当前实验事实

- V2 screen 产出 1080 行（90 cases × 12 methods），0 case failure；每个因素水平
  都有 5 个独立 seed。production replay 180 行与 scientific controlled 900 行
  分开汇总。
- steering sanity 的目标 angle/Doppler scan 峰值为 3°/335.182 Hz；空间和时空
  distortionless 误差分别约 `5.6e-17`、`2.2e-16`，杂波脊陷波 `-59.79 dB`，
  单目标复增益误差 `2.4e-16`，4° 扰动增益 `-9.89 dB`。
- screen 全方法平均 SCNR improvement：Current scientific controlled `13.244 dB`，
  Phase-only `10.421 dB`，Row complex LS/Wiener `11.028 dB`；physical adaptive
  方法在全 screen 平均为负（Corrected JDL `-0.510 dB`、local DL-SMI/MVDR
  `-0.868 dB`），这是真实结果，不做正值截断或门限掩盖。
- velocity mode 实际完成 35 cases、420 行、0 failures；MDV 使用预先固定的
- 单 case 只保留二值 `target_detected`；经验 Pd 只在 5-seed 聚合层计算，并保存
  Wilson 95% CI、成功数和 trial 数。velocity MDV 使用预先固定的 seed-mean
  `Pd >= 0.5` 判据和真实重生成速度 grid，结果见独立 velocity manifest 与
  `baseline_v2_mdv_summary.csv`。在 0 m/s 已满足阈值的方法不能解读为已证明“无盲速”。
- 独立 ROC 使用 `--mode roc`，5 个重新生成的 seed × 7 个 threshold scale，结果在
  `outputs/ai_csi_baseline_v2_roc/`；screen 已保存 Pd–SCNR、三类 Pareto、输入
  feature diagnostics 和 exploratory light predictor，均不构成 AI 训练。

## Baseline 实验事实

- 首轮覆盖 7 个场景 × 6 个方法，共 42 条结果；场景包含理想/均匀、非均匀杂波、
  通道幅相误差、有效样本不足、低 SCNR、近杂波脊和支撑边缘目标。
- F1/F2 strict 组的 7 场景等权平均 SCNR 提升：Current 12.318 dB、Phase-only
  7.403 dB、Row-LS/Wiener 9.246 dB；四通道 academic reference 单独报告，未与
  strict 组混排。
- 有效样本不足场景实际为 45/130，有 85 个通道脉冲丢失；近杂波脊目标相对杂波
  中心约 1.050 Hz；支撑边缘目标距动态支撑边界 2 行。
- Baseline 的 FA/Pfa 使用纯 C+N background 和固定 GO-CFAR；fixed-region high
  点使用全 Doppler、固定距离区间，避免缩小主动支撑自动改善指标。
- 运行 manifest 保存命令、源码 dirty 状态、Release 仿真器身份、环境探测和每个
  关键 BIN 的路径/大小/SHA-256；原始 BIN 默认逐 case 清理。

## 可复现入口

源码构建已完成并记录在 V2 manifest；当前 `build/` 保留可复现实验所需的
`simulate_stage2_statistical` Release 目标，需要复现时可执行：

```text
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target simulate_stage2_statistical -j2
```

仿真配置：

- `configs/research/ai_csi_stage2_typical_1beam.json`
- `configs/research/ai_csi_stage2_nonideal_target_1beam.json`
- `configs/research/ai_csi_stage2_nonideal_background_1beam.json`

Oracle 汇总入口：

```text
python3 scripts/run_ai_csi_oracle_suite.py
```

根目录交付物：

- `outputs/ai_csi_oracle/single_factor_summary.csv`
- `outputs/ai_csi_oracle/leave_one_out_summary.csv`
- `outputs/ai_csi_oracle/oracle_overall_summary.csv`
- `outputs/ai_csi_oracle/current_vs_oracle_scnr_improvement.png`
- `outputs/ai_csi_oracle/mismatch_contribution.png`
- `outputs/ai_csi_oracle/ca_target_loss_pd.png`

## Baseline 传统方法复现入口

```text
python3 -m py_compile scripts/run_baseline_benchmark.py
python3 scripts/run_baseline_benchmark.py
```

实验配置：`configs/research/ai_csi_baseline_suite.json`；方法说明、指标定义和
输入边界见 [`AI_CSI_04_Baseline方法与实现说明.md`](AI_CSI_04_Baseline方法与实现说明.md)，
汇报结果和图表见 [`AI_CSI_05_Baseline实验报告.md`](AI_CSI_05_Baseline实验报告.md)。
完整 CSV/PNG/JSON 在 `outputs/ai_csi_baseline/`。

## Baseline V2 复现入口

```text
python3 -m py_compile scripts/run_baseline_v2.py
python3 scripts/run_baseline_v2.py --mode sanity --out outputs/ai_csi_baseline_v2
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode screen --workers 2 --out outputs/ai_csi_baseline_v2
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode velocity --workers 2 \
  --out outputs/ai_csi_baseline_v2_velocity
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode roc --workers 2 \
  --out outputs/ai_csi_baseline_v2_roc
```

主 V2 交付物为 `baseline_v2_summary.csv`、`baseline_v2_sweep_summary.csv`、
`baseline_v2_feature_diagnostics.csv`、`baseline_v2_feature_correlations.csv`、
`baseline_v2_feature_light_predictor.csv`、`baseline_v2_mdv_summary.csv`、
sanity/Pd/Pareto/feature/heatmap/velocity PNG 和 manifest；velocity 目录保留完整的
regenerated-velocity 汇总，ROC 目录保留 `baseline_v2_roc_points.csv`、
`baseline_v2_roc_summary.csv` 和 `baseline_v2_pfa_roc.png`。原始 BIN 默认逐 case 删除。

历史原始 BIN、仿真报告和逐案例中间回放目录已在记录输入大小与 SHA-256 后按授权
清理；输入身份见 `outputs/ai_csi_oracle/raw_input_inventory.json`。Git 只跟踪上述
紧凑交付物，避免把大体量原始数据复制进版本历史。

## 证据边界

历史 V2 screen/velocity/ROC 记录仍是 CPU 离线回放，不能与本轮 CUDA 证据混算；
本轮 Phase A、M1/M2/M3 challenge 和 V2 formal 使用 RTX 3050 Laptop GPU（driver
580.173.02、CUDA 13.0）实际运行。新的 challenge 仍是单波位、单周期 compact 输入，不能外推为
生产统计泛化结论；3-seed 正例和 paired controls 已在该 compact 场景形成可追溯的
Pd/Pfa/target-loss 证据，但尚不能替代多场景生产统计评价。CPU gate 语义另有
`csi_gate_cpu_selftest` 直接回归，当前全量 CTest 为 18/18 通过。

## 下一步（Phase-I 后续，Phase-II 保持冻结）

channel-delay correction 的 5-period、多 seed/目标速度/SCNR TrackManager/PIPE formal 已
完成；下一步启动 `two_channel_decorrelation_study.json` 的 temporal-rho/internal-motion
sweep，比较 Current、phase-only、complex LS-Wiener、robust LS 和 coherence-aware 五种
方法，并补齐 target-off fixed-Pfa 与完整 target-safe 统计。若 correction 未真实施加，
分支仍必须是 `NOT_EVALUABLE`。

clutter-only servo 和 velocity blind estimator 的模型失配仍是 fallback 证据，不进入在线
部署；J4 没有纯 DOF 稳定优势，production CUDA 4ch STAP 保持关闭。也不训练 MLP、Router、
RD image-to-image 或通用复权残差；只有确定性估计出现稳定、可量化且难以解析的残差后，才重新
评估 Physics-AI。
