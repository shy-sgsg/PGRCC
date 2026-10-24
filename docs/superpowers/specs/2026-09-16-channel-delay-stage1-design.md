# Phase-I Channel-Delay Deterministic Self-Calibration Design

## Goal

把 `channel delay error` 做成 Phase-I 的第一个论文级单误差模板：在生产等效
`4ch protocol IQ → F1/F2 → CSI → GO-CFAR → TrackManager/PIPE` 链路上，统一完成
A0/A1/A2/A3 条件、正负多幅度 delay sweep、target-off/target-on paired control、
传统 estimator baseline、parameter-level Monte Carlo、TrackManager ID-switch 机制审计、
paired statistics 和可提交的 compact evidence。

本设计不扩展 native four-channel STAP/JDL/covariance/loading/DOF/CUDA，不启动 AI 或
Router；`ai_training=false`、`router_enabled=false` 始终写入运行时 manifest。

## Scientific contract

### Input and pairing

所有科学 estimator 只接收已经按生产映射得到的：

```text
F1 = (C1 + C3) / 2
F2 = (C2 + C4) / 2
```

raw four-channel data 只用于 packet/layout/fusion audit。每个 scene identity 绑定
seed、target、clutter/noise、beam、range、velocity、SNR 和生产配置；A0/A1/A2/A3
之间只改变 delay error 或 correction state。OFF/ON/TO 使用同一 scene identity：

```text
OFF = C + N
ON  = S + C + N
TO  = S
```

runner 必须记录三类输入的路径、字节数、SHA-256、协议 layout 和 simulator return code。
若 `ON - OFF - TO` 的 payload additive audit 超出预先定义的 float32 容差，scene 的
target-safe/Pfa 结论标记 `NOT_EVALUABLE`，不能静默当作 paired control。

### Four conditions

```text
A0 Ideal:
    true delay error = 0; nominal processor state
A1 Current:
    nonzero delay error; processor does not know/correct it
A2 Known-error correction:
    A1 input; injected truth used only to apply evaluation upper-bound correction
A3 Blind estimated correction:
    A1 input; target-free F1/F2 estimate only; correction applied physically
```

A2 truth 不进入 estimator；A3 manifest 必须明确 `truth_used_in_estimator=false`、
`correction_applied=true` 和 calibration source。A1/A2/A3 使用同一个 error scene，A0
使用同 scene identity 的 zero-error paired scene。四条件所有高值优指标统一计算：

```text
recoverable_space = A2 - A1
blind_recovery    = A3 - A1
recovery_ratio    = blind_recovery / recoverable_space
```

对低值优指标，先转换为正向 loss/recovery 并在 CSV 中写 `metric_direction`；零分母
写 `NOT_EVALUABLE`，不得填 0 或 1。

### Delay range and working points

parameter-level sweep 固定覆盖：`0, ±1, ±2, ±4, ±8 ns`，并在 manifest 中区分
`engineering_realistic_range` 与 `stress_test_range`。Level-2 CUDA E2E 使用预注册的
selected working points 而不是无界 Cartesian product：基础点沿用既有
`beam=50/range≈85 km/velocity=6.7 m/s/SNR=30 dB/texture_sigma=0.25`，再至少包含一
个低 SNR、一个 12 m/s velocity，以及一个不同 beam/range/clutter-texture 点。完整
delay sweep 在 Level-1 Monte Carlo 覆盖；Level-2 至少覆盖 zero、正负、小/中/大幅度，
并在多个 seed/velocity/SNR/working point 上复核趋势。

## Implementation topology

### 1. Pure delay and statistics core

新增 `scripts/delay_stage1_core.py`，只保存无文件副作用的函数：

- `estimate_delay_d1_d2_d3(f1_time, f2_time, fs_hz)`：调用现有
  `D1_ordinary_LS`、`D2_weighted_LS`、`D3_Huber_weighted_LS` 语义，输入已经是 F1/F2；
- `estimate_delay_cross_correlation(...)` 与 `estimate_delay_gcc_phat(...)`：使用同一
  target-free F1/F2 calibration interval，支持 fractional peak interpolation，无法
  在当前 LFM bandwidth/采样支持下可靠估计时返回结构化 fallback reason；
- `delay_method_suite(...)`：输出 method、estimate、finite/fallback、runtime、support
  and confidence，禁止读取 truth；
- `weighted_slope_variance(...)`、`delay_theory_summary(...)`：实现 WLS slope variance
  和 95% CI 所需的 `Sxx_w = Σ w_i(f_i-f̄_w)^2`；
- `residual_phase_from_delay_error(...)`、`approximate_two_channel_cancellation_loss(...)`：
  输出 `εφ(f)=2πfετ` 和小残差近似 `sin²(εφ/2)`，明确这是解析近似，不等价于生产
  CSI 全链路保证；
- `paired_mcnemar_exact(...)`、`paired_bootstrap_ci(...)`、`aggregate_parameter_mc(...)`：
  分别用于二元 paired exact test、按 scene block 的连续指标 bootstrap 和 estimator
  bias/RMSE/STD/CI/outlier/fallback 汇总。

### 2. Stage-1 formal runner

新增 `scripts/run_delay_stage1_formal.py`，作为唯一 scene/condition 编排入口，复用：

- `scripts/run_track_manager_e2e.py` 的 scenario/layout/XML/production runner 原语；
- `scripts/estimate_channel_delay.py` 的 F1/F2 D1–D3 和 fractional-delay rewrite；
- `scripts/audit_track_manager_run.py` 的生产 debug/payload provenance audit。

runner 的接口必须支持：

```text
--mode pilot|formal
--delay-errors-ns 0,1,-1,2,-2,4,-4,8,-8
--seeds 101,202,303
--target-velocities-mps 6.7,12.0
--snr-db 20,30,35
--working-point <named selection>
--mc-trials 100
--output-root <empty independent directory>
```

每个 case 依次生成/审计 A0/A1 的 ON/OFF/TO 输入；A1 的 target-free OFF input 同时
作为 A3 calibration source；A2/A3 对 A1 的 ON 和 OFF raw 分别做 correction，再进入
相同生产 XML、GO-CFAR、TrackManager 和 PIPE。runner 不改变 gate、confirmation 或
association 规则，不把 correction 失败退化成 A1。

每个 branch manifest 至少保存：condition、error truth、estimate/source/residual、
`correction_applied`、truth-read audit、scene/input hashes、packet/fusion layout、
production return code、CFAR/detection/track/payload audit 路径和 `NOT_EVALUABLE` reason。

### 3. Target-off false-alarm waterfall

formal runner 对 OFF branch 单独解析并输出四层口径，字段不得相互替代：

1. CFAR cell false-hit fraction = hit cells / valid CUT denominator；
2. false clusters = selected clusters / explicit cluster denominator；
3. protocol false detections = OFF `detection_results` rows surviving protocol gate；
4. false tracks = OFF production TrackManager outputs/unique track IDs。

配置中的 `Pfa=1e-6` 只标为 theoretical GO-CFAR cell Pfa。structured clutter 的实测值
命名为 `empirical_structured_clutter_false_hit_fraction`。缺少 valid CUT、cluster ID、
production detection identity 或 OFF track run 时，具体层级写 `NOT_EVALUABLE`，不使用
detection record、payload 或 period proxy 冒充该层级。

### 4. Parameter-level Monte Carlo

formal runner 先做 CPU estimator-only MC：每个选定 delay/SNR point 至少 100 trials，
使用物理可解释的宽带 complex LFM、同一 F1/F2 delay convention、独立 Gaussian noise，
并记录 random seed namespace、fs/bandwidth/pulse count。每个 method 输出 bias、RMSE、
STD、95% CI、outlier rate、fallback rate 和 runtime。MC 不能读取 truth 来选择 frequency
mask；truth 只在汇总时计算误差。

Level-2 只在选定工作点运行生产 CUDA E2E，不能把 estimator-only MC 的数字写成
TrackManager/Pd/Pfa 结论。

### 5. ID-switch audit

新增 `scripts/audit_delay_track_id_switch.py`，只读生产 `track_debug`、同周期 detection、
accepted-assignment 和 payload CSV，逐目标逐周期输出：truth target ID、detection index/ID、
track ID、candidate IDs/count、innovation/residual、association distance、gate threshold、
confirmation state、lost/reacquired、merge/split evidence 和 source file/row。

每个 switch 只在有证据时分类为：

```text
miss_to_reacquisition
multiple_candidate_competition
false_track_takeover
gate_boundary_crossing
position_velocity_jump
track_confirmation_or_reset
other
```

`other` 必须保留不可判定字段和原因。审计器不调整 Track gate、不重算 TrackManager，
也不把每周期样本当独立统计样本。

### 6. Analysis and evidence

新增 `scripts/analyze_delay_stage1_formal.py`，读取 runner manifest、MC rows、OFF/ON/TO
metrics 和 ID-switch rows，按 scene/seed block 生成：

```text
outputs/formal_evidence/stage1_delay/
├── manifest.json
├── delay_estimation_summary.csv
├── delay_baseline_comparison.csv
├── A0_A1_A2_A3_summary.csv
├── target_off_false_alarm_summary.csv
├── target_on_detection_summary.csv
├── track_summary.csv
├── id_switch_audit.csv
└── statistics_summary.csv
```

该目录只保留 compact JSON/CSV/日志和输入 hash/provenance；raw BIN/NPY/F32/PNG 在 hash
和汇总后按明确路径清理，不能覆盖既有 output。上述 compact files 是本阶段需要提交的
正式证据，不提交原始大文件。

## Theory and paper material

`docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md` 必须给出当前证据与 A–G 结论；
`docs/papers/Paper1_ChannelDelay_SelfCalibration_Outline.md` 按论文结构组织以下推导：

```text
x2(t)=x1(t-delta_tau)
X2(f)=X1(f) exp(-j 2*pi*f*delta_tau)
C12(f)=X1(f) conj(X2(f))
phi12(f)=phi0 + 2*pi*f*delta_tau
epsilon_phi(f)=2*pi*f*epsilon_tau
Var(delta_tau_hat) ~= sigma_phi^2 / ((2*pi)^2 Sxx_w)
```

必须解释 `C12=X1*conj(X2)` 的符号 convention、D1/D2/D3 权重和 Huber reweighting，
并把 cancellation loss 标为理论/近似模型。若数据不能支持严格 CRLB，只报告 WLS
slope variance，不编造 CRLB。

论文只写 `potential contributions`：物理可观测性、truth-blind estimation、fractional-
delay physical correction、production CSI/CFAR/TrackManager closure；不把 Huber/WLS
称作创新算法，也不写未经文献检索确认的 novelty。

## Tests and acceptance

先为 `delay_stage1_core.py`、A0/A1/A2/A3 contract、OFF/ON/TO additive audit、baseline
sign/fallback、statistics zero denominator、ID-switch classification 和 evidence schema
写失败测试，再实现最小逻辑。最终必须实际运行：

```text
full pytest
py_compile changed Python scripts
Release cmake build
CTest
git diff --check
real CUDA Stage-1 pilot/formal with nvidia-smi manifest
```

科学验收逐项检查：A0/A1/A2/A3 全部存在；A3 truth-read=false 且 correction_applied=true；
delay zero/正负/多幅度；OFF/ON/TO 同 scene paired 且 additive audit pass；D1/D2/D3 加至少
一个合理传统 baseline；MC bias/RMSE/CI/fallback/runtime；理论 residual-phase/cancellation
trend；四层 OFF false-alarm；target-on Pd/transfer/Track metrics；ID-switch 6 次新增机制；
paired exact/bootstrap effect size/CI/materiality；compact evidence 已提交；AI/Router/STAP
仍关闭。

当前 evidence 若仍缺少 valid CUT、cluster ID 或 SHM transport，必须在 report 中写明
具体 `NOT_EVALUABLE` 层级，不把它隐藏成总体成功。
