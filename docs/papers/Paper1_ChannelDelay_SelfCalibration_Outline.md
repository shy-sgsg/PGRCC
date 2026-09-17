# Paper 1 outline — Target-free channel-delay self-calibration for production two-channel GMTI

状态：论文草稿框架，不是已投稿稿件。135-case formal evidence 已完成并回填到
`outputs/formal_evidence/stage1_delay/`；仍不得把 pilot 数字、历史 Oracle 术语或
未验证的 novelty 写成结论。工程 materiality threshold 和硬件实测标定尚未声明。

## 1. Introduction

### 1.1 Problem

Airborne GMTI 的多通道系统误差会先破坏通道间相干性，再传递到 CSI、CFAR、
检测、定位和跟踪。本文只研究一个物理含义明确的误差：通道间 fractional time
delay mismatch。

### 1.2 Scope and claim boundary

生产科学输入严格为：

```text
four-channel protocol IQ
→ F1=(C1+C3)/2, F2=(C2+C4)/2
→ two-channel CSI
```

raw 4ch 只用于 protocol/debug/fusion verification。本文不包含 native four-channel
STAP/JDL、四通道 covariance/loading、AI Router 或 neural network。

### 1.3 Potential contributions

以下是待文献检索和 formal evidence 验证的 potential contributions，不是当前
novelty claim：

1. 明确把 channel delay 的频域 phase slope 与生产 F1/F2 CSI degradation 连接；
2. 在 target-free OFF 数据上做 truth-blind deterministic estimation；
3. 使用 fractional-delay physical correction 而非结果层复权；
4. 把 A0/A1/A2/A3、OFF/ON/TO、CFAR/TrackManager/PIPE audit 组成同一闭环；
5. 报告 estimator recovery 与 production tracking trade-off，包括 ID-switch 机制。

## 2. Production two-channel GMTI signal model

### 2.1 Protocol and fusion

定义四个协议通道 `C_k(t)`，生产融合为：

```text
F1(t) = (C1(t)+C3(t))/2
F2(t) = (C2(t)+C4(t))/2
```

所有 D1/D2/D3/baseline 只接收 `F1,F2`。论文中要展示 protocol layout、采样率、
脉冲长度和 fusion channel mapping，并把 raw channel 作为输入协议而非额外科学
自由度。

### 2.2 CSI/CFAR/TrackManager chain

```text
F1/F2 → cross-channel calibration/CSI → GO-CFAR
      → clustering/positioning → TrackManager → PIPE payload
```

结果层目标必须反查到当前周期 `Confirmed + matched_this_frame` detection；不能用
Tentative、Coasted prediction 或全部原始 detection 替代协议目标语义。

## 3. Channel-delay mismatch and CSI degradation

令通道 2 相对通道 1 的时间误差为 `delta_tau`，本文采用：

```text
x2(t) = x1(t-delta_tau)
X2(f) = X1(f) exp(-j 2*pi*f*delta_tau)
```

互谱取：

```text
C12(f) = X1(f) conj(X2(f))
       = |X1(f)|^2 exp(+j 2*pi*f*delta_tau)
```

故 unwrap 后：

```text
phi12(f) = phi0 + 2*pi*f*delta_tau
```

正 delay 对应正 phase-vs-frequency slope；论文需在图中标明 Fourier sign、channel
ordering 和 correction sign，避免把实现 convention 写反。

### 3.1 Residual phase after correction

令：

```text
epsilon_tau = delta_tau_hat-delta_tau
```

physical correction 后的残余相位为：

```text
epsilon_phi(f) = 2*pi*f*epsilon_tau
```

这给出从 delay residual 到 F1/F2 mismatch、CSI coefficient 和 cancellation residual
的可审计传播链。

### 3.2 Approximate cancellation model

在 equal-amplitude、有效频带 `B` 内均匀权重的近似下：

```text
rho(epsilon_tau) ≈ |sinc(pi*B*epsilon_tau)|
```

实际 LFM 和结构杂波不满足全部假设；因此公式只用于趋势/量纲解释，production
结果必须以 measured coherence、CSI residual、CFAR 和 track evidence 为准。

## 4. Target-free delay estimator

### 4.1 Common preprocessing

1. 读 target-free A1 OFF period packets；
2. 逐 packet 解析 protocol IQ，保留 pulse/packet boundary；
3. 形成 F1/F2，不把 raw four-channel 额外 pair 用于估计；
4. FFT、计算 `C12`、去除无效频点、phase unwrap；
5. 估计值和 residual 记录到 manifest/CSV。

### 4.2 D1 ordinary LS

令 `y_i=unwrap(angle(C12(f_i)))`、`a_i=2*pi*f_i`：

```text
min_{b,delta_tau} Σ_i [y_i-b-a_i*delta_tau]^2
```

### 4.3 D2 magnitude/power weighted LS

```text
min_{b,delta_tau} Σ_i w_i [y_i-b-a_i*delta_tau]^2
```

`w_i` 来自互谱 magnitude/power 的确定性权重。论文须报告有效频点规则、权重
归一化和窄带/低功率频点处理。

### 4.4 D3 Huber weighted LS

Huber loss：

```text
rho_k(r) = 1/2*r^2                         |r|≤k
           k*(|r|-1/2*k)                   |r|>k
```

实现以 D2 为初值，按 residual 更新 influence weight，再解 weighted regression，
直到迭代收敛或达到固定上限。D3 是 robust estimation tool，不单独宣称创新。

### 4.5 Conventional baselines

- normalized linear cross-correlation + sub-sample parabolic peak；
- GCC-PHAT cross-correlation。

所有 baseline 使用与 D1–D3 相同的 F1/F2、calibration interval、delay sweep 和
truth-blind输入；truth 只在 evaluation 阶段计算 bias/RMSE/CI。
## 5. Fractional-delay physical calibration

### 5.1 Four evaluation conditions

| Condition | Definition | Truth use |
|---|---|---|
| A0 Ideal | zero delay error, nominal processor | none |
| A1 Current | delay injected, no correction | none |
| A2 Known-error correction upper bound | truth delay used only to create correction input | not in estimator |
| A3 Blind target-free estimated correction | A1 OFF estimate creates correction input | no delay truth |

For every A3 case, assert `truth_used_in_estimator=false` and
`correction_source=A1_OFF_target_free_estimate`. Missing finite estimates are
`NOT_EVALUABLE`, never Current fallback.

### 5.2 Paired scene construction

```text
OFF = C+N
ON  = S+C+N
TO  = S
```

A0 ON materializes zero-error C+N; A1 ON/OFF/TO reuse the same background and only the
declared delay state changes. This controls scene randomness before comparing A1–A3.

### 5.3 Recovery definitions

For higher-is-better metrics:

```text
recoverable_space = A2-A1
blind_recovery    = A3-A1
recovery_ratio    = blind_recovery/recoverable_space
```

For lower-is-better metrics use the sign-transformed metric. A zero or unavailable
denominator is retained as `NOT_EVALUABLE`.

## 6. Theoretical estimation accuracy

If phase errors are independent Gaussian and `w_i` are inverse-variance weights, the
weighted slope variance is approximated by:

```text
f_bar_w = Σ_i w_i f_i / Σ_i w_i
Var(delta_tau_hat)
  ≈ 1 / [(2*pi)^2 Σ_i w_i (f_i-f_bar_w)^2]
```

This is a weighted-regression variance approximation, not an unconditional CRLB. It
requires an explicit phase-noise/unwrap model; if that model is not supported by the
data, report Monte Carlo CI only.

The estimator section must compare, for each selected delay/SNR point and method:

```text
bias, RMSE, STD, 95% CI, outlier rate, fallback rate, runtime
```

Parameter-level Monte Carlo is at least 100 trials in the current configuration. The
formal experiment must show zero, positive/negative small and stress delays; `±1 ns`
is the engineering-local range and `±2/±4/±8 ns` are stress points unless hardware
calibration later changes that label.

## 7. Experimental setup

### 7.1 Registered workpoints

The formal registry contains:

```text
base: 3 seeds × 2 velocities × 2 SNR
low_snr
high_velocity
alternate_texture_geometry
```

The selection remains group-scoped rather than manufacturing an unsupported Cartesian
product. The final command currently resolves 15 case blocks × 9 delay values, 5
periods per case, with ON/OFF/TO for all A0–A3 conditions.

### 7.2 Target-off control and false-alarm waterfall

Report four separate layers:

1. CFAR cell false-hit = hit cells / independent valid CUTs, only with an exported denominator;
2. false clusters = explicit OFF CFAR cluster count/IDs;
3. protocol false detections = production detection CSV rows;
4. false tracks = unique Confirmed/output track IDs in production debug.

Configured `Pfa=1e-6` is theoretical GO-CFAR cell Pfa only. Structured-clutter results
must be labelled empirical structured-clutter false-hit fraction. Target-on without
independent CUT denominator is `NOT_EVALUABLE` for cell Pfa.

### 7.3 Production evidence

Each branch stores runtime XML layout audit, production return code, period-input hashes,
CFAR summary, track_debug audit, protocol payload reverse audit and ID-switch audit.
The production result is not replaced by a simplified tracker.

## 8. Results from compact evidence

### 8.1 Delay estimator and baseline

| formal aggregate over 9 delays × 3 SNR | method | mean absolute bias (ns) | mean RMSE (ns) | max fallback |
|---|---|---:|---:|---:|
| 27 parameter points, 100 trials/cell | D1 ordinary LS | 0.004075 | 0.053395 | 0 |
| 27 parameter points, 100 trials/cell | D2 weighted LS | 0.004046 | 0.053359 | 0 |
| 27 parameter points, 100 trials/cell | D3 Huber weighted LS | 0.003833 | 0.054587 | 0 |
| 27 parameter points, 100 trials/cell | cross-correlation | 2.026209 | 2.027616 | 0 |
| 27 parameter points, 100 trials/cell | GCC-PHAT | 2.980928 | 2.990429 | 0 |

逐 delay/SNR 的 bias、RMSE、STD、95% CI、outlier/fallback 和 runtime 必须直接引用
`delay_baseline_comparison.csv`；上表只是 135 行 formal baseline 的可读聚合。

### 8.2 A0–A3 recovery

| metric (135 case-block mean) | A0 | A1 | A2 | A3 |
|---|---:|---:|---:|---:|
| target/period detection Pd | 1.0000 | 0.9556 | 0.9867 | 0.9793 |
| track Pd, all visible | 0.7867 | 0.7289 | 0.7807 | 0.7659 |
| track Pd, after confirmation | 0.9833 | 0.9111 | 0.9759 | 0.9574 |
| position RMSE (m) | 91.3616 | 220.4825 | 160.2206 | 161.4688 |
| ground-speed velocity RMSE (m/s) | 7.3739 | 10.6873 | 9.8726 | 10.2854 |
| angle RMSE (deg) | 0.0457 | 0.1358 | 0.0959 | 0.0969 |

`recoverable_space`、`blind_recovery` 和 `recovery_ratio` 仍按逐 case、逐 metric
写在 `A0_A1_A2_A3_summary.csv`；zero 或 unavailable denominator 保持
`NOT_EVALUABLE`，不把表中均值重新解释为每个 case 的 ratio。当前 compact output
没有独立 CSI/cancellation metric，不能另造该行。

The signs and `NOT_EVALUABLE` states must be copied from
`A0_A1_A2_A3_summary.csv`, not recomputed by hand in the paper.

### 8.3 Target-off and target-on

| condition | cell false-hit | false clusters (mean) | protocol false detections (mean) | false tracks (mean) | target Pd (mean) |
|---|---|---:|---:|---:|---:|
| A0 Ideal | NOT_EVALUABLE, valid CUT=0 | 588.20 | 588.20 | 192.00 | 1.0000 |
| A1 Current | NOT_EVALUABLE, valid CUT=0 | 608.55 | 608.55 | 197.95 | 0.9556 |
| A2 Known-error upper bound | NOT_EVALUABLE, valid CUT=0 | 623.62 | 623.62 | 202.59 | 0.9867 |
| A3 Blind target-free | NOT_EVALUABLE, valid CUT=0 | 615.64 | 615.64 | 201.93 | 0.9793 |

这里的 counts 是结构杂波下的显式 production 计数，不是实测 cell-Pfa；配置的
theoretical GO-CFAR `Pfa=1e-6` 不因这些 counts 被宣称达成。

Each cell includes denominator/status and definition in the evidence package.

### 8.4 Track and ID switch

正式 135 case 的 TrackManager 均值为：all-visible Pd/continuity
`0.7289/0.7289`（A1）到 `0.7659/0.7659`（A3），after-confirmation Pd/continuity
`0.9111/0.9111`（A1）到 `0.9574/0.9574`（A3）；position/velocity/angle RMSE 的
A1→A3 变化为 `220.4825→161.4688 m`、`10.6873→10.2854 m/s`、
`0.1358→0.0969 deg`。ID switch 总数 A0/A1/A2/A3 为 `234/248/268/290`，
`id_switch_audit.csv` 的 1040 行均为 `other`，因为 candidate/innovation/lifecycle
来源缺失；因此不从 track IDs alone 推断机制，也不把 switch count 写成收益。
## 9. Paired statistical analysis

For binary outcomes, use exact paired McNemar analysis with the two discordant cells:
`Current miss → Blind hit` and `Current hit → Blind miss`.

For continuous outcomes, use paired bootstrap over scene/case blocks, not over individual
periods of one continuous track. Report effect size, 95% CI, discordant counts and a
predeclared materiality interpretation. If no threshold is configured, materiality is
indeterminate even when a p-value is small.

当前 formal 的 135 个 scene blocks 给出：full-scene hit 的 A2-vs-A1 McNemar
discordant cells 为 `20/4`，effect=`0.1185`，`p=0.00154`；A3-vs-A1 为 `12/1`，
effect=`0.0815`，`p=0.00342`。连续指标的 A2/A3 effect（lower-is-better 已做
`-metric` 转换）分别为：position `60.2619/59.0136 m`，95% CI
`[35.7428,86.2300]` / `[42.9415,74.9848]`；angle `0.03990/0.03892 deg`，
95% CI `[0.02349,0.05787]` / `[0.02834,0.04946]`；velocity
`0.8147/0.4019 m/s`，两组 CI 均跨 0。没有预注册 materiality threshold，故不以
p-value 替代工程判据。

## 10. TrackManager explanation of ID switches

The dedicated audit consumes existing production outputs and compares adjacent periods
for the same truth target. It classifies observed switches as miss→reacquisition,
multiple-candidate competition, false-track takeover, gate-boundary crossing,
position/velocity jump, track confirmation/reset, or other.

formal audit 共 1040 条相邻周期 switch 记录，A0/A1/A2/A3 分别为
`234/248/268/290`，全部保守分类为 `other`。这表明当前数据支持“不能做机制归因”，
不支持从 ID 序列推断是候选竞争、gate crossing 或 reacquisition。分析仍必须区分
前端 measurement improvement 与 TrackManager ID maintenance，不能为了降低 switch
count 去调 gate 或 confirmation logic。

## 11. Limitations and validity threats

1. A target-on detection CSV is not an independent CUT denominator; generic detection
   Pd and target-period hit Pd must stay distinct.
2. Structured clutter can produce empirical false alarms above theoretical GO-CFAR Pfa;
   that is a model/scene result, not evidence that the configured Pfa was achieved.
3. A2 is an evaluation upper bound because it uses injected truth to form the correction;
   it is not an operational estimator.
4. D3 robustness does not prove a new robust-estimation contribution.
5. Narrow effective bandwidth, phase unwrap errors, and channel/servo confounding can
   violate the simple regression model.
6. Local-input production correctness is not a SHM throughput benchmark.
7. Pilot data and incomplete/failed runs are kept as separate evidence roots and are never
   pooled with the final formal matrix；正式 raw production/scenes/inputs 已在 compact
   evidence 校验后删除，只保留 root manifest、MC 汇总和逐 case manifest；逐文件 cleanup
   record 的唯一完整副本保存在 root manifest，case 文件仅保留清理摘要与生产审计字段。

## 12. Conclusion template

The final manuscript may claim a single-error deterministic self-calibration result only
after the compact evidence proves all of the following: A0/A1/A2/A3 complete and paired;
A3 target-free with no truth leakage; a zero plus positive/negative multi-amplitude delay
sweep; D1/D2/D3 plus fair conventional baselines; parameter-level bias/RMSE/CI/fallback;
theory trend supported by Monte Carlo; OFF four-layer waterfall with explicit denominators;
production CSI/CFAR/TrackManager/PIPE reverse audit; ID-switch mechanism evidence or an
explicit attribution gap; and paired McNemar/bootstrap statistics.

Potential contributions should then be phrased around physical observability, blind
deterministic estimation, physical correction and production closure. Novelty requires a
separate literature review.

## 13. Data and reproducibility checklist

The paper supplement should link the nine compact files:

```text
manifest.json
delay_estimation_summary.csv
delay_baseline_comparison.csv
A0_A1_A2_A3_summary.csv
target_off_false_alarm_summary.csv
target_on_detection_summary.csv
track_summary.csv
id_switch_audit.csv
statistics_summary.csv
```

The source manifest must also record command, config/template hashes, git identity and
dirty state, input hashes, GPU state, disk state, return codes and cleanup ledger. Raw
binary data is not copied into the compact evidence package; after compact verification,
the final source root keeps the case-specific configuration/production audit fields and the
root manifest's canonical per-file cleanup records, without duplicate copies in each case
manifest.

## 14. Gate to coupled-error / Physics-AI work

This paper does not authorize Phase-II work. Before coupled-error study or any Physics-AI
gate, require a closed single-delay result, a coupled deterministic baseline, a measured
traditional recovery gap with materiality, and an explicit sensor-prior/observability
partition. Until then:

```text
ai_training=false
router_enabled=false
native four-channel STAP/JDL=false
```
