# 物理模型驱动的智能 GMTI 杂波对消：历史基线与下一阶段

## 当前状态

当前主线已从“理想/当前链路上的 AI 候选”转为
`Unknown System Error Characterization / 真实系统未知误差建模与可观测性分析`：

```text
真实系统未知误差 → 多通道回波观测 → 误差/状态参数估计
→ 物理模型修正与通道自校准 → 恢复杂波相干性
→ CSI / 四通道 STAP → CFAR / Pd / Pfa / 目标保持
```

本轮已完成源码审计、误差参数清单、传播关系、六对紧凑观测量、true/report 几何、
unknown-only blind estimator、几何扩展矩阵，以及 B0/B1/B1K 生产 CUDA CSI/CFAR、
目标因果传递、Pfa 分母审计、信息量匹配 baseline 和 B2/B3/B3K 离线 STAP reference；
还启动了 servo true/report pilot，没有训练 AI。机器可读证据在
`outputs/system_error_inventory/`、`outputs/unknown_system_error_geometry_matrix_20260914_v2/`
、`outputs/unknown_system_error_geometry_matrix_extended_20260914_formal_v2/`、
`outputs/unknown_system_error_geometry_nuisance_sweep_20260914_formal_v1/`、
`outputs/unknown_system_error_end_to_end_20260914_formal_v4/` 和
`outputs/unknown_system_error_servo_pilot_20260914_v3/`，详细边界见
[`AI_CSI_33_真实系统误差参数与可观测性分析.md`](AI_CSI_33_真实系统误差参数与可观测性分析.md)。

当前生产 Current 是四通道协议 IQ 经 `(1,3)`、`(2,4)` 融合成 F1/F2 后进入 CSI；
四通道 STAP 保留四个空间自由度。历史报告中 strict 组与四通道 academic reference
不混排，只表示信息条件审计，不表示二者不能端到端比较。

以下 V1/V2、Physics-AI 和 Router 结果均保留为历史证据；不要把旧阶段的主线标题、
Oracle 术语或 Router 状态当作当前待办。

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
- servo/beam pilot 已完成 18 个 `0, ±0.05, ±0.1, ±0.2, ±0.5° × 2 seeds` Stage2
  paired cases 和 54 条 Core 分支；true angle 驱动回波/增益/LOS，reported angle 驱动
  header/processing，unknown-only six-pair estimator 的 mean bias/RMSE 为
  `-0.0108°/0.0138°`，AI/Router 保持关闭。
- B2/B3/B3K 是离线 `JDL-3x4 reduced STAP` scientific reference，不是生产 CUDA
  四通道 STAP；TrackManager/PIPE、平台速度/姿态 true/report、servo-specific 多场景
  Pd/Pfa 仍是后续项。
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
| Current 与四通道 STAP 比较口径 | 能力层与信息量匹配层均已运行；M3 仍为离线混合 reference | `outputs/unknown_system_error_end_to_end_20260914_formal_v4/manifest.json`、`outputs/unknown_system_error_information_matched_baseline_20260914_v1/` |
| Phase0 paired-reference baseline-phase sanity | `run`，明确非 blind/online | `outputs/unknown_system_error_pilot_20260914_clean/manifest.json`；估计误差 `0.009999999925 m`，恢复比 `0.9999999991` |
| Phase1/2 true/report geometry + blind estimator | `run`，单参数 baseline geometry | `outputs/unknown_system_error_end_to_end_20260914/calibration/unknown_only_estimator.json` |
| Phase3 blind geometry matrix | `run`，27/27 fit、无 fallback | `outputs/unknown_system_error_geometry_matrix_20260914_v2/matrix_summary.csv`、`single_pair_vs_six_pair.csv` |
| Phase4 production CSI/CFAR + offline STAP | `run`，B0/B1/B1K CUDA；B2/B3/B3K offline reference | `outputs/unknown_system_error_end_to_end_20260914/production_metrics.csv`、`offline_stap_metrics.csv` |
| Phase5 recovery/Pd/Pfa/target transfer | `run`，受控 moving-target fixture | `recovery_metrics.csv`、`target_only_transfer.csv`、`target_off_false_cluster_metrics.csv` |
| Phase6 geometry correction + nuisance + servo pilot | 几何扩展矩阵、deadband、nuisance sweep 与 servo true/report pilot 已运行；平台速度未开始 | `outputs/unknown_system_error_geometry_matrix_extended_20260914_formal_v2/`、`outputs/unknown_system_error_geometry_nuisance_sweep_20260914_formal_v1/`、`outputs/unknown_system_error_servo_pilot_20260914_v3/` |
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

## 下一步（当前第二阶段）

下一步是补齐平台速度/姿态的独立 true/report state，并在不改变已冻结 baseline 的前提下
扩展 servo-specific 多场景 Pd/Pfa、TrackManager/PIPE 目标保持和生产 CUDA 四通道 STAP
边界。当前不训练 MLP、Router、RD image-to-image 或通用复权残差；只有确定性估计出现
稳定、可量化且难以解析的残差后，才重新评估 Physics-AI。
