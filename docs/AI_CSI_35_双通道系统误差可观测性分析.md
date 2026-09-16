# 双通道系统误差可观测性分析

> 审计日期：2026-09-16
>
> 本文只解释当前 F1/F2 scientific input 上实际运行的 54-case pilot。它是
> Phase-I 的可观测性证据，不是完整 airborne 统计泛化、在线校准收益或 AI 开启依据。

## A. 输入边界和实验设计

科学输入严格为四通道协议 IQ 融合后的：

```text
F1 = (C1 + C3) / 2
F2 = (C2 + C4) / 2
```

六个 raw pair 只用于调试和交叉验证，不进入本矩阵。状态向量为：

```text
[channel_delay_error,
 inter_pulse_phase_error,
 baseline_geometry_error,
 servo_angle_error,
 platform_velocity_error,
 yaw_error]
```

第一轮不引入 pitch/roll。每个参数使用 zero、`+h`、`−h` 三个 case，灵敏度为：

```text
S[o,p] = (o(+h) − o(−h)) / (2h)
```

本轮使用 3 个 seed（101/202/303）、每 case 1 个 compact period、9 个 beam angle
（−20° 到 20°，跨度 40°）和 2048 个 range/frequency bins。compact 只缩短 pilot
输入，未改变融合、观测字段或判读规则。所有 54 个 case 均由 Stage2 simulator
实际生成 raw protocol IQ，解析后先做 F1/F2，再计算观测；观测计算不读取 truth。

## B. 观测字段与判读方法

观测字段包含 cross-channel phase、phase-vs-frequency、phase-vs-pulse、
phase-vs-beam-angle、range-block residual、P38 slope/intercept、clutter Doppler
ridge、ridge-vs-angle、CTDR、slow-time phase、F1/F2 coherence 和 CSI residual power。

不同观测的单位不同：Hz、rad、rad/pulse、deg、dB 不能直接让大数值字段支配列余弦。
因此：

1. 保留 raw sensitivity matrix，报告真实单位量级、符号和跨 seed spread；
2. 将每个观测行按其跨参数 L2 norm 平衡；
3. 再将每个状态列 L2 归一化，用于 rank、condition number 和 column cosine；
4. `zero_control` 保留零误差基线观测；另以同 seed 的显式 error-free reference
   计算 `zero_control_delta`，本轮 delta 最大绝对值为 0（数值容差 1e-9）。这两个
   量不能与 `(+h)−(−h)` 灵敏度差分混用。

该规则避免把 `clutter_doppler_ridge_hz` 的量纲优势误判成所有状态都不可分离，
同时保留物理量级供工程判断。

## C. 实际结果

机器可读证据：
`outputs/two_channel_error_observability_phase_i_20260916/manifest.json`、
`observability_summary.json`、`sensitivity_matrix_raw.csv`、
`sensitivity_matrix_scaled.csv`、`pair_observability.csv`、`zero_control.json`、
`sensitivity_stability.csv`、`range_angle_coverage.csv` 和
`observable_metadata.json`。

manifest 实际状态为 `completed`，54/54 case 通过，缺失 sensitivity 为空；
`ai_training=false`、`router_enabled=false`。行平衡后的矩阵结果为：

| 指标 | 实际值 | 含义 |
|---|---:|---|
| observable rows | 13 | 统一 F1/F2 观测字段 |
| state columns | 6 | 当前六维状态向量 |
| raw rank | 6 | 原始灵敏度矩阵数值满秩 |
| scaled rank | 6 | 行平衡、列归一化后的可辨识性矩阵满秩 |
| condition number | 37.3283545882 | 仍有有限条件数，但不是无条件独立估计证明 |
| seed count | 3 | 101/202/303 |
| angle coverage | 9 points, 40° span | 覆盖波束角变化 |
| range coverage | 2048 bins, 8 blocks | 含 range-block residual 诊断 |

`range_angle_coverage.csv` 还记录每个 case 的物理 slant-range 轴起止、实测 FFT
frequency 轴起止，以及每个 range block 的 bin/range/frequency 边界；不是只保留计数。

平衡后关键列余弦：

| 状态对 | column cosine | 解释 |
|---|---:|---|
| delay / phase drift | −0.09393 | 当前 pilot 中频率斜率与脉冲斜率可分开，不能因 raw cross-phase 共线而误判 |
| delay / servo | −0.91995 | 近混淆；联合估计需外部先验确认 |
| servo / yaw | 0.18023 | 未达到本审计的近混淆阈值；不等于真实系统中无需姿态/伺服来源 |
| geometry / beam-angle response | 由 `phase_vs_beam_angle_slope...` 直接观测 | 该字段是几何误差的主要局部证据 |

当前 0.98 是高余弦 confounding gate，0.90 是 near-confounding diagnostic gate。
本轮没有列达到 0.98，因此 `status_by_state` 均为
`candidate independently observable`；delay/servo 两列达到 near-confounding gate，
报告中已附外部先验需求。若把 delay 和 servo 作为未经 INS/servo encoder 约束的
联合在线状态，本轮证据不足，应标记为 **`not independently observable from current two-channel data`**，
而不是把满秩机械地写成可部署 estimator。

## D. 按参数的物理解释

### D.1 Channel delay

`phase_vs_frequency_slope_rad_per_hz` 是首要观测；本轮 raw median sensitivity 为
`5.9242295597e−7 rad/Hz/ns`，并由 range-block、CTDR 等字段提供旁证。它与 phase
drift 的平衡余弦仅 −0.09393，支持“delay 与脉冲相位漂移在当前 pilot 中有不同
结构”的候选判断；它与 servo 的 −0.91995 又说明实际联合校准仍需要 servo encoder、
factory channel calibration 或 temporal prior。

### D.2 Inter-pulse phase error

`phase_vs_pulse_slope`、slow-time phase 和 P38 slope 对它有响应；本轮
`phase_vs_pulse_slope` 的 raw median sensitivity 为 `−1.0105602e−4 rad/pulse`
per `(deg/pulse)`。它不是 delay 的同义字段，必须保留 frequency-vs-pulse 的双轴
审计。CTDR/P38 在小扰动下部分受噪声影响，不能单独作为成功标准。

### D.3 Baseline geometry

`phase_vs_beam_angle_slope_rad_per_sin_theta` 的 raw median sensitivity 为
`0.250021736 rad/(sin(theta)·mm)`；range/angle coverage 实际覆盖 9 个波束角，
因此 geometry phase-angle 不是由单波位截距臆测。该结果仍是局部 pilot candidate，
不替代真实基线测量和多姿态验证。

### D.4 Servo angle

伺服误差在本矩阵中存在响应，但与 delay 是 near-confounding 对。target-assisted
伺服结果不能被写成 target-free online 能力；实际部署需要 servo encoder、beam-pointing
telemetry 或 temporal prior，并应把 geometry/servo 的 nuisance 关系一起估计或锁定。

### D.5 Platform velocity

本轮把 `clutter_doppler_ridge_hz`、P38 slope、CTDR 和 slow-time 作为不同候选观测，
而不是用一个 threshold 调到能区分。raw matrix 中 velocity 对 P38、phase-angle、
CTDR 有非零响应，但 `clutter_doppler_ridge_hz` 在该 compact 采样下的 central
difference 为 0；这说明“有局部灵敏度”与“每个观测都有效”必须区分。历史
velocity formal 的 blind estimator 仍因模型失配 fallback，故本报告不宣称在线
velocity correction 已完成。

### D.6 Yaw

yaw 在当前 pilot 对 clutter ridge/CTDR/coherence 有明显响应，且与 servo 的 column
cosine 为 0.18023，未显示强 servo/yaw 余弦混淆。这个结果只回答本实验输入中的局部
结构；真实系统仍需要 INS/attitude source、时间同步和多姿态场景来排除 beam geometry
与平台运动的共同 nuisance。

## E. Cross-check 与估计边界

- delay 估计器审计使用 phase-vs-frequency；phase drift 使用 phase-vs-pulse；geometry/
  pointing 使用 phase-vs-angle；velocity 分别保留 ridge、P38、CTDR、slow-time，未用
  一个融合阈值掩盖失败；
- `sensitivity_stability.csv` 给出每个参数/观测的 seed 数、median/min/max、符号一致性
  和 relative spread。符号不一致的弱响应只能作为不稳定候选，不能当作强观测；
- `range_angle_coverage.csv` 记录每个 zero/+/− case 的 bins、range blocks、beam
  count 和 angle span，保证 range/angle 结论可追溯；
- zero/plus/minus case manifest 记录配置 hash、输入 raw hash、模拟器退出码和 raw
  清理状态。raw BIN 在汇总和 hash 后移除，避免把诊断旁路当成第二个科学输入。

## F. TrackManager/PIPE 连接

首个成熟校正参数是 channel delay。`scripts/run_track_manager_e2e.py` 现在有：

- Current：未知 delay，不施加校正；
- blind estimated correction：从 target-free calibration raw 估计，再真实改写协议
  IQ 的 fractional-delay；
- known-error correction upper bound：truth 只用于 evaluation-only correction。

真实 CUDA smoke 产物在 `outputs/track_delay_smoke_4ch_gpu_reaudit_20260916/`，三分支
均消费 4ch protocol IQ（每包 378,496 bytes）；runtime XML audit 还强制核对
`enable_four_channel_fusion=1`、`four_channel_phase_compensation_enable=1` 和 C3/C4
映射。production return code 均为 0，TrackManager audit 0 violation，payload 可反查到同周期
`Confirmed + matched_this_frame` detection。该 smoke 只有 1 seed、1 个目标速度、1 个
SNR、3 个周期；metrics 中的 false-track、Pd、位置/角度/速度误差只证明链路和审计
契约，不构成正式校正收益结论。正式多 seed、多速度、多 SCNR 和 5–7 周期矩阵仍为
pending；校正未真实施加时必须保持 `NOT_EVALUABLE`。

另完成了一个 5-period、1 seed、1 个目标速度、1 个 SNR 的本地代表性 formal
case：`/tmp/pgrcc_track_delay_formal_4ch_onecase_20260916/`。该 case 仍使用真实
4ch protocol IQ、F1/F2 融合和生产 TrackManager；三分支均通过输入布局、runtime XML、
estimator provenance、TrackManager audit（0 violation）和 PIPE 载荷反查。blind 估计为
`4.0412577853 ns`，truth 仅用于 known upper bound 的 evaluation-only correction；
该结果只证明 5-period 链路已经可运行，不代表多 seed/速度/SCNR 的校正收益，完整矩阵仍
待补。

该 smoke 是 CUDA `--local-test` 生产 core 路径；SHM/PIPE 5-period 正式矩阵已启动但在
首个 target/calibration case 完成前因运行窗口过长中止，partial 现场见
`/tmp/pgrcc_track_delay_formal_gpu_20260915/partial_attempt.json`，不能与下面的 smoke
表混算：

| branch | correction applied | delay estimate/reference (ns) | detection records / target-period Pd | eligible payloads / matched payloads | unique false tracks | Track Pd (all / after confirmation) | audit |
|---|---:|---:|---:|---:|---:|---:|---|
| Current | false | — / 4.0 | 125 / 1.0 | 39 / 4 | 31 unique tracks | 0.667 / 1.0 | pass, 0 violation |
| blind estimated | true | 4.08249 / 4.0 | 125 / 1.0 | 42 / 4 | 35 unique tracks | 0.667 / 1.0 | pass, 0 violation |
| known upper bound | true | 4.0 / 4.0 | 124 / 1.0 | 41 / 4 | 35 unique tracks | 0.667 / 1.0 | pass, 0 violation |

表中的 detection records 是 production `detection_results` 行，不是 CFAR hit cells；
target-period Pd 的分母是可见 truth period（每周期一个目标检测机会）。因此它不被
命名为独立 detection-level Pd。`track_branch_metrics.csv` 同时保留
`cfar_hit_cell_count`，但本次 target-on 链路没有 valid CUT 分母、target-off 控制或
CFAR-cell 到目标的身份映射，故 `cfar_pfa` 与 `cell_false_hit_*` 明确为
`not_evaluable`，不把检测行差值冒充 cell-Pfa。协议层统计也区分
`protocol_payload_false_alarm_*`（可计算）与 `protocol_detection_false_alarm_*`
（因 payload 不保留生产 detection identity 而为 `not_evaluable`）。
同理，从生产 `[CFAR][SUMMARY]` 聚类数和同周期 target association 得到的
`cluster_false_alarm_*` 明确标为 `period_association_proxy_no_cluster_id`，不冒充
直接 cluster-ID 统计。上述 false-track、payload 和 proxy 数字不是固定 Pfa，也没有
足够重复次数支持显著性或 recovery claim。blind 分支的校正残差为 `+0.08249 ns`，
known 分支为 0 ns；known 的 truth 只用于上限评价。

## G. 结论、限制与下一步

### 结论

本轮支持以下有限结论：F1/F2 数据对六个状态均存在局部响应候选；delay 与 phase
drift 可由频率/脉冲结构区分；geometry 有 beam-angle phase slope；velocity 有多条
候选观测但其稳定性需继续验证；servo 与 delay 是当前最需要外部先验的近混淆对；
servo/yaw 在本 pilot 中没有强列余弦混淆。满秩不等于无先验可部署，也不等于盲估计器
已经通过。

### 未验证项

- 尚未完成 5–7 period、多个目标速度/SCNR/seed 的 TrackManager correction benefit；
- 尚未运行 temporal decorrelation 五方法 sweep、固定 Pfa 和完整 target-safe 统计；
- 当前 compact 观测使用受控 Stage2 场景，不能外推到真实平台全姿态、全航迹和复杂
  杂波；pitch/roll、clock drift、amplitude/IQ mismatch 尚未进入本六维矩阵；
- Phase-II native four-channel STAP/JDL/covariance/loading/DOF/CUDA 优化仍冻结；
- AI/Router 均未训练、未启用。

后续先补 TrackManager 正式 delay correction 矩阵，再按
`configs/research/two_channel_decorrelation_study.json` 启动同一 F1/F2 输入上的
Current/phase-only/complex LS-Wiener/robust LS/coherence-aware 比较。
