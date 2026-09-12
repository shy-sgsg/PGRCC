# 物理模型驱动的智能 GMTI 杂波对消：第一阶段进展

## 当前状态

第一阶段已完成，当前没有训练 AI，也没有实现端到端 clutter-free RD 网络。
已完成 Current–Oracle 的两类场景回放、单因素替换、leave-one-error-out 归因、
新增指标、传统 baseline 矩阵和后续方法建议。AI 仍未训练。

当前主线已进入 Baseline V2：90 个 screen cases（9 个因素 × 2 个水平 × 5
seeds）和 35 个独立物理 velocity cases（7 个速度点 × 5 seeds）均已在 CPU 离线
链路实际完成；另有 5-seed、7 阈值点的 ROC 运行，共 420 个 ROC point，AI 仍未训练。
正式更密 sweep 的配置已经准备，但不把未运行的矩阵
写成已完成实验。

V2.1 冻结后已完成 PGRCC-v1 Oracle Headroom Audit：16 个新 scene/seed、3472
个 local region、7409 条 Pareto 候选和 80 条 full-map GO-CFAR 代表性 exact
复核均已实际运行，0 case failure。严格多指标 worthwhile region 为 0%，manifest
结论为 `NO_GO_ORACLE_NOT_SUFFICIENT`，因此当前不构造训练集、不训练 PGRCC-v1；
详见 [`AI_CSI_09_PGRCC_Oracle与数据集设计报告.md`](AI_CSI_09_PGRCC_Oracle与数据集设计报告.md)。

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
门禁为 `REOPEN_AI_ROUTER`，这不是 AI 训练或部署授权。四个代表失败 scene 的机制审计
显示 phase surface 近线性，`test_v3_010` 的 J6 target-only transfer 为 `−0.348 dB`
并标记为疑似过校正。J8 三场景六候选均未通过目标安全约束，因此仍保持
`ai_training=false`。推荐阅读顺序为 [`AI_CSI_29_TargetSafeOracle_v2审计.md`](AI_CSI_29_TargetSafeOracle_v2审计.md)
→ [`AI_CSI_30_TargetSafeFailureMechanism审计.md`](AI_CSI_30_TargetSafeFailureMechanism审计.md)
→ [`AI_CSI_31_J8ClutterSupportOnly审计.md`](AI_CSI_31_J8ClutterSupportOnly审计.md)。

## 已完成事项

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

## 下一步（需另行进入第二阶段）

若后续重新授权，再评估小型“物理模型 + 残差学习”方案。第一候选是
学习分数 delay、复权幅度残差和行级置信度/门控；物理融合、P38、支撑、CSI
算子、CFAR、聚类和跟踪链保持不变。未经新的研究授权，不进入训练或端到端
RD 网络实现。
