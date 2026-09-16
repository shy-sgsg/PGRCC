# 双通道系统失配与稳健 CSI 研究框架

> 版本：Phase-I 收束版（2026-09-16）
>
> 本文是当前研究框架和阅读入口；历史实验数字仍保留在对应历史报告中，不在本文
> 重新定义验收标准。

## 1. 当前问题

真实 airborne GMTI 的 INS、平台运动、伺服/波束指向、通道同步、基线几何和杂波
时间统计可能存在未知误差。它们先改变回波的跨通道相位、频率斜率、慢时间结构和
杂波相干性，随后才表现为 CSI 对消、GO-CFAR、定位和跟踪性能变化。

Phase-I 的问题不是“在理想 Current 上叠加 AI”，而是：

```text
多通道回波观测
  → 误差/状态可观测性与确定性估计
  → 物理模型修正和通道自校准
  → F1/F2 相干性恢复
  → 两通道 CSI / GO-CFAR / TrackManager
```

`ai_training=false`、`router_enabled=false`。只有确定性估计已经暴露稳定、可量化
且难以显式解析的残差，才重新评估 Physics-AI；Router 不属于当前主线。

## 2. 固定生产链和信息边界

所有 Phase-I 科学结论使用同一生产等效链：

```text
4-channel protocol IQ
  → F1=(C1+C3)/2, F2=(C2+C4)/2
  → CTDR / phase calibration / P38
  → two-channel CSI
  → production GO-CFAR
  → clustering / positioning
  → TrackManager / PIPE
```

四通道原始数据的六个 pair（`C13,C24,C12,C14,C23,C34`）可以用于诊断、交叉验证和
定位代码问题，但不能把它们作为额外空间自由度计入 Phase-I 的 F1/F2 可观测性结论。
四通道 native STAP、JDL、协方差/加载策略、空间自由度和 CUDA 优化全部冻结到
Phase-II；历史结果只作为归档 reference。

## 3. 状态向量和来源

初始状态向量固定为六维，不把 pitch/roll 混入第一轮：

| 状态 | 单位 | 主要传播 | 当前 Phase-I 处理要求 |
|---|---:|---|---|
| `channel_delay_error` | ns | 频率相关相位、脉压/CTDR | 由 target-free 互谱估计并施加 fractional-delay correction |
| `inter_pulse_phase_error` | deg/pulse | 慢时间相位、P38/CSI 协方差 | 与 delay 的频率/脉冲结构分开审计 |
| `baseline_geometry_error` | mm | 随波束角变化的空间相位 | 用 phase-vs-beam-angle 斜率和 geometry truth/report 审计 |
| `servo_angle_error` | deg | 真实 look angle、波束增益和相位 | 与 delay/geometry 的 nuisance 关系显式报告，并保留 servo encoder 需求 |
| `platform_velocity_error` | m/s | 杂波 Doppler ridge、P38、CTDR、slow-time | 分列 velocity observables；当前 pilot 不据此宣称在线校正 |
| `yaw_error` | deg | look vector、姿态投影、杂波/目标 Doppler | 与 servo 分开建模；需 INS/attitude source 做外部约束 |

误差真值只用于 case manifest 和 A2 上限评价。A3 blind estimator 只能读取目标无关
或独立校准输入，不能读取 `true` 字段、目标 truth 或 known error。

## 4. 观测向量

F1/F2 观测由 `scripts/audit_two_channel_error_observability.py` 统一计算：

- `cross_channel_phase_rad`；
- `phase_vs_frequency_slope_rad_per_hz`（delay 候选）；
- `phase_vs_pulse_slope_rad_per_pulse`、`slow_time_phase_slope_rad_per_pulse`
  （phase drift 候选）；
- `phase_vs_beam_angle_slope_rad_per_sin_theta`（geometry/pointing 候选）；
- `range_block_phase_residual_rad`（距离块结构）；
- `p38_slope_residual_hz`、`p38_intercept_residual_rad`；
- `clutter_doppler_ridge_hz`、`clutter_ridge_vs_angle_slope_hz_per_deg`；
- `ctdr_residual_rad`；
- `f1_f2_coherence`、`csi_residual_power_db`。

可观测性矩阵采用 zero/+/− 单参数小扰动和 central difference。不同物理单位不能
直接用原始量纲比较：分类矩阵先按观测行 L2 平衡，再按状态列 L2 归一化；原始
灵敏度仍单独保留用于量级、符号和工程意义。

## 5. 四条件和评价

每个代表性场景保留四个信息/算法条件：

| 条件 | 含义 | truth 使用 |
|---|---|---|
| A0 Ideal / No-error | 无误差参考 | 只用于参考 |
| A1 Current + unknown error | 当前生产 F1/F2 链，误差未校正 | 不读真值 |
| A2 Known-error correction upper bound | 已知误差校正上限 | truth 只进上限评价 |
| A3 Blind estimated correction | 目标无关输入估计并实际施加校正 | 禁止读 truth |

参数层报告 bias/RMSE、F1/F2 residual 和 coherence。杂波层报告 CSI residual power、
杂波 ridge、cell/cluster/protocol false alarm、false track。目标安全层报告 causal
target transfer、GO-CFAR Pd、Track Pd、confirmation latency、continuity、ID switch、
position/angle/velocity RMSE。

符号固定为：

```text
recoverable space = Known − Current
actual recovered  = Estimated − Current
recovery ratio    = actual recovered / recoverable space
```

分母为零时写 `not_evaluable`；不能用非负截断掩盖 signed regression。PIPE 目标只能
由当前处理周期内 `Confirmed + matched_this_frame` 的生产航迹和关联 detection 触发，
原始 detection CSV 只作离线审计旁路。

## 6. 当前实现状态

- channel delay 已有 F1/F2 互谱估计和 protocol raw fractional-delay rewrite；首次
  TrackManager smoke 已真实施加 blind/known correction，未施加时 runner 返回
  `NOT_EVALUABLE`；正式多 seed、多速度、多 SCNR 收益矩阵仍 pending；
- inter-pulse phase、baseline geometry、platform velocity、yaw 已有观测/真值分离审计，
  但不能把局部 sensitivity candidate 写成已完成的在线 estimator；
- servo 的 target-assisted pilot 不等于 target-free clutter-only 能力；本阶段近混淆
  关系和外部先验需求必须保留；
- temporal decorrelation 的五种比较方法已在
  `configs/research/two_channel_decorrelation_study.json` 固定为后续入口，配置当前
  仅为契约，不代表 sweep 已运行；
- Phase-II native four-channel STAP/JDL/DOF/CUDA 仍关闭。

## 7. 后续去相关入口

后续在同一 F1/F2 输入上比较：Current、phase-only、complex LS-Wiener、robust LS、
coherence-aware。场景扫描 temporal clutter `rho`、internal clutter motion、seed 和
连续周期；先用 target-off 估计杂波系数，再用 target-on 做 causal transfer 和固定
Pfa/TrackManager 审计。不得引入 AI/Router 或把 raw six-pair 的信息量混入结论。

## 8. 复现和证据

当前 54-case observability matrix 的正式入口：

```bash
python3 scripts/audit_two_channel_error_observability.py \
  --compact --period-count 1 --seeds 101 202 303 \
  --output-root outputs/two_channel_error_observability_repro
```

本轮实际证据目录为 `outputs/two_channel_error_observability_phase_i_20260916/`；其中
`manifest.json`、`observability_summary.json`、`sensitivity_matrix_raw.csv`、
`sensitivity_matrix_scaled.csv`、`pair_observability.csv`、`zero_control.json`、
`sensitivity_stability.csv` 和 `range_angle_coverage.csv` 必须一起阅读。无有效快时间信号的旧试验保留在
`outputs/two_channel_error_observability_invalid_zero_signal_20260915/`，不作为结论。

TrackManager smoke 的可复现命令和结果限制见 README；真实 CUDA 产物保存了 branch
manifest、metrics、PIPE payload audit 和 production `track_debug` 反查。所有正式报告
必须同时写清命令、配置/hash、源码 dirty 状态、设备状态、实际退出码和未验证项。
