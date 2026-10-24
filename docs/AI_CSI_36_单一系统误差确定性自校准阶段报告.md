# AI-CSI Phase-I：单一系统误差确定性自校准阶段报告

> 本报告把 channel-delay 作为 Phase-I 的第一个论文级单误差模板。结论只针对
> 当前四通道协议 IQ 经 F1/F2 融合后的生产两通道链路；不包含 native 4ch
> STAP/JDL、AI Router 或任何神经网络。

## 0. 2026-09-17 收口增补：证据边界与下一步（历史收口）

本节只记录本次收口已经产生的证据和文档，不把历史 Formal-v1 数字升级为最终论文结论。

- Formal-v1 仍标记为 development formal；不可变身份、dirty provenance 和限制见
  [`AI_CSI_37_ChannelDelay_Finalization_Audit.md`](AI_CSI_37_ChannelDelay_Finalization_Audit.md)。
- fractional-delay inverse closure 已生成 54 行 C0–C4 矩阵；C4 因未提供 production input 保持
  `NOT_EVALUABLE`，入口为 `outputs/fractional_delay_inverse_closure_20260917/`。
- production GO-CFAR geometry/valid-CUT tap、TrackManager association schema 和 retained-v1
  ID-switch reanalysis 已分别保留；v1 的 1040 行旧 switch 没有被重建或覆盖，缺少 v2 association
  字段的行明确标为 `unclassifiable_production_fields`，见
  `outputs/formal_evidence/stage1_delay_v2_reanalysis_20260917/`。
- hierarchical reanalysis 已以 physical scene block 为单位：15 个 block、9 个 delay，重复的
  block-delay-metric 行折叠并记录 duplicate/missingness；当前结果标签为 `Formal-v1 exploratory`，见
  `outputs/formal_evidence/stage1_delay_hierarchical_v1_exploratory_20260917/`。
- materiality 已在 `configs/research/channel_delay_stage1_materiality.json` 预注册；阈值仍为
  `null/exploratory_pending`，因此当前不能做工程 pass/fail。
- D1/D2/D3 之外新增的传统 baseline 为 generalized phase-slope ML 和
  oversampled cross-correlation；所有方法共享 F1/F2、target-free 输入和 truth-blind estimator
  边界，比较仍不等同于 Pd/Pfa 或 tracking 优势。
- 文献新颖性和硬件边界分别见
  [`ChannelDelay_Calibration_Novelty_Audit.md`](literature/ChannelDelay_Calibration_Novelty_Audit.md)
  与 [`ChannelDelay_Hardware_Validation_Protocol.md`](experiments/ChannelDelay_Hardware_Validation_Protocol.md)。
  Paper-1 只保留 potential/unverified 表述；硬件当前没有实测结果。
- 最终 v2 结论槽位见
  [`AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md`](AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md)；
  当时在独立 clean Formal-v2 前不启动 Stage-2A hard gate；Formal-v2 完成后的 A–L 判定见第 0.1 节。

本阶段固定开关为：`ai_training=false`、`router_enabled=false`、`native_four_channel_stap=false`。

## 0.1 2026-09-20 Formal-v2 最终收口

独立 Formal-v2 已从 frozen commit `ebac0b7c649ac23295e36e8aee9ee8749631d30b` 完成
`135/135` 个 case，覆盖 15 个 physical scene block、9 个 delay；source before/after
相同且 tracked dirty=false。当前 compact 和分层证据分别为：

- `outputs/formal_evidence/stage1_delay_v2_full_20260917/`；
- `outputs/formal_evidence/stage1_delay_v2_full_20260917_hierarchical/`。

当前结果支持：A3 是 target-free correction；A2/A3 相对 A1 的 position RMSE 平均改善
分别为 `60.2619/59.0136 m`，target/period detection Pd 为
`A1=0.9556 → A2=0.9867 → A3=0.9793`；但 materiality 阈值仍为
`exploratory_pending`，不能写成工程 pass/fail。OFF waterfall 的 cluster/protocol/track
层分别可评估，而 cell 层 540 行因 `valid_cut_count=0` 为 `NOT_EVALUABLE`；ID-switch
1040 行全部保留 `unclassifiable_production_fields`，缺失字段为
`track_association_audit_v2.csv`。C0–C3 closure 有限输入通过，C4 production input
缺失，A2/A0 residual 的单因素反事实分解仍不充分。

因此 Stage-2A 当前为 `NO_GO_STAGE1_INCOMPLETE`，缺口为 C4 production input、valid-CUT
分母和 A2/A0 residual 分解。除非项目决策者书面允许在这些 `NOT_EVALUABLE` 边界下继续，
本阶段不启动耦合误差 runner、Physics-AI 或 Router；固定开关继续为
`ai_training=false`、`router_enabled=false`、`native_four_channel_stap=false`。

完整 A–L 矩阵、当前数值、限制和最短复现命令见
[`AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md`](AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md)。

## 1. 结论摘要

当前研究边界固定为：

```text
4ch protocol IQ
  → F1=(C1+C3)/2, F2=(C2+C4)/2
  → target-free delay estimate
  → fractional-delay physical correction
  → two-channel CSI/GO-CFAR/TrackManager/PIPE
```

本阶段实现了 D1 ordinary LS、D2 magnitude/power weighted LS、D3 Huber
weighted LS，以及 cross-correlation 和 GCC-PHAT 两个传统 baseline。A0/A1/A2/A3
四条件、ON/OFF/TO paired scene、target-off 四层 waterfall、production
TrackManager debug 反查、ID-switch 审计和 compact evidence 生成器均已接入。

正式矩阵的配置与运行入口为：

```bash
python3 scripts/run_delay_stage1_formal.py \
  --mode formal --input-mode local \
  --output-root outputs/formal_delay_stage1_20260916 \
  --delay-errors-ns 0,1,-1,2,-2,4,-4,8,-8 \
  --seeds 101,202,303 \
  --target-velocities-mps 6.7,12.0 \
  --snr-db 20,30,35 \
  --working-point registered --mc-trials 100 --cleanup-raw
```

正式源 manifest 和 compact evidence 的最终位置约定为：

```text
outputs/formal_evidence/stage1_delay/manifest.json
```

若 compact manifest 的 `status` 不是 `completed`，本文只把它报告为
`completed_with_gaps`，不把退出码 0 或文件存在误写成算法通过。

本轮正式收口结果为：源 manifest 的 135/135 个 case 均为 `completed`，5 个估计器的
Monte Carlo 共 135 行、每个 delay/SNR/method 100 trials，状态为 `passed`。在 9 个
delay × 3 个 SNR 的 27 个参数点上，平均 RMSE（ns）为 D1=`0.053395`、D2=`0.053359`、
D3=`0.054587`、cross-correlation=`2.027616`、GCC-PHAT=`2.990429`；各方法的
fallback rate 均为 0。该汇总是 estimator-level 结果，不把它直接等同为生产 Pd/Pfa
收益。

Level-2 的 135 个 case 均生成 A0/A1/A2/A3、ON/OFF/TO 和 TrackManager/PIPE 审计。
按 135 个 case block 求均值的代表性结果如下；下表只用于读报告，逐 case 值和状态以
compact CSV 为准：

| 指标（均值） | A0 Ideal | A1 Current | A2 Known-error upper bound | A3 Blind target-free |
|---|---:|---:|---:|---:|
| target/period detection Pd | 1.0000 | 0.9556 | 0.9867 | 0.9793 |
| track Pd, all visible | 0.7867 | 0.7289 | 0.7807 | 0.7659 |
| track Pd, after confirmation | 0.9833 | 0.9111 | 0.9759 | 0.9574 |
| position RMSE (m) | 91.3616 | 220.4825 | 160.2206 | 161.4688 |
| ground-speed velocity RMSE (m/s) | 7.3739 | 10.6873 | 9.8726 | 10.2854 |
| angle RMSE (deg) | 0.0457 | 0.1358 | 0.0959 | 0.0969 |

lower-is-better 指标的配对 effect 使用 `-metric` 后计算，因此正值表示 RMSE 下降。
相对 A1，A2/A3 的 position effect 分别为 `60.2619/59.0136 m`，95% CI 分别为
`[35.7428, 86.2300]`、`[42.9415, 74.9848]`；angle effect 为
`0.03990/0.03892 deg`，95% CI 为 `[0.02349, 0.05787]`、`[0.02834, 0.04946]`。
velocity effect 为 `0.8147/0.4019 m/s`，两组 CI 都跨 0。二元 full-scene hit 的
McNemar 配对统计为：A2 相对 A1 的 `Current miss → comparison hit=20`、
`Current hit → comparison miss=4`、effect=`0.1185`、`p=0.00154`；A3 相对 A1
为 `12/1`、effect=`0.0815`、`p=0.00342`。配置没有声明 materiality threshold，
因此这些 p-value 不被写成工程重要性结论。

OFF waterfall 的 135-case 均值为：false clusters A0/A1/A2/A3=`588.20/608.55/
623.62/615.64`，protocol false detections 与该实现的 cluster 计数相同；false
tracks=`192.00/197.95/202.59/201.93`。四个 condition 的独立 valid-CUT 分母均为
0，因此 cell false-hit fraction 的 540 条记录全部 `NOT_EVALUABLE`，不能由配置的
theoretical GO-CFAR `Pfa=1e-6` 推导实测 Pfa。ID-switch audit 共 1040 行，A0/A1/A2/A3
分别为 `234/248/268/290`；由于生产 debug 缺少候选/innovation/lifecycle 归因字段，
1040 行均保守记为 `other`，不从 ID 序列反推机制。

## 2. 研究问题与比较口径

### 2.1 四个条件

| 条件 | 场景误差 | 校正 | truth 在 estimator 中 | 用途 |
|---|---|---|---|---|
| A0 Ideal | 0 ns | 否 | 否 | 零误差基线 |
| A1 Current + unknown error | 注入 delay | 否 | 否 | 当前系统损失 |
| A2 Known-error correction upper bound | 注入 delay | 是，使用 truth 仅生成评价上限输入 | 否 | 可恢复空间上限 |
| A3 Blind target-free estimated correction | 注入 delay | 是，来自 A1 OFF 的估计 | 否 | 盲确定性闭环 |

比较指标方向在 evidence CSV 的 `direction` 字段中声明。对 higher-is-better
指标使用：

```text
recoverable_space = A2 - A1
blind_recovery    = A3 - A1
recovery_ratio    = blind_recovery / recoverable_space
```

对 lower-is-better 指标，先转换为 `-metric` 再计算；分母为零或不存在时保留
`NOT_EVALUABLE`，不填 0 或 1。

### 2.2 Paired scene

每个 case 只有一个 scene identity：seed、target、noise、clutter、beam、range、
velocity、SNR 和 production settings 固定。ON/OFF/TO 定义为：

```text
OFF = C+N
ON  = S+C+N
TO  = S
```

OFF 与 ON 共享相同的 clutter/noise realization。A0 ON 先 materialize 零误差
C+N background，A1 复用该背景后注入 delay，避免背景差异冒充校正收益。

## 3. 物理模型与估计器

### 3.1 时延与互谱相位

令通道 2 相对通道 1 的时间误差为 `delta_tau`，采用当前代码的约定：

```text
x2(t) = x1(t-delta_tau)
X2(f) = X1(f) exp(-j 2*pi*f*delta_tau)
C12(f) = X1(f) conj(X2(f))
      = |X1(f)|^2 exp(+j 2*pi*f*delta_tau)
```

因此：

```text
phi12(f) = phi0 + 2*pi*f*delta_tau
delta_tau = slope / (2*pi)
```

实际 estimator 只接收 target-free A1 OFF 的 F1/F2 矩阵；raw 4ch 只用于协议
解析、F1/F2 fusion 检查和审计，不作为额外特征。

### 3.2 D1/D2/D3

令 `y_i=unwrap(angle(C12(f_i)))`，`a_i=2*pi*f_i`，拟合：

```text
min_{b,delta_tau} sum_i w_i [y_i - b - a_i*delta_tau]^2
```

- D1：`w_i=1` 的 ordinary LS；
- D2：以互谱 magnitude/power 产生的频率权重；
- D3：在 D2 权重基础上，按 Huber influence function 迭代重加权。

D3 是稳健估计工具，不作为单独的算法创新 claim。权重、有效频点、迭代次数、
残差和 fallback 都写入 delay estimation evidence。

### 3.3 传统 baseline

baseline 与 D1–D3 使用相同的 F1/F2、相同 target-free calibration interval、相同
delay truth 只用于评价，不传入 estimator：

1. normalized linear cross-correlation 的亚采样抛物线峰值；
2. GCC-PHAT 的相位变换互相关峰值。

对窄带或低有效带宽样本，相关峰可能不具备唯一的 fractional-delay 解析分辨率；
这类情况记录 failure/fallback，不为凑数量把无效 baseline 写成有效对比。

## 4. 物理校正与生产闭环

A2/A3 都通过相同的 `rewrite_float32_protocol_delay` 对指定 protocol channel 做
fractional-delay correction，manifest 必须同时包含：

```text
correction_applied=true
applied_delay_ns
correction_source
truth_used_in_estimator=false
truth_used_to_apply_correction
correction_audit
```

A2 的 source 是 `truth_evaluation_only`；A3 的 source 是
`A1_OFF_target_free_estimate`。A3 缺少有限估计时直接 NOT_EVALUABLE，禁止静默回退
到 Current。

四个 condition 的生产分支都复用现有 TrackManager/PIPE runner，保留：

```text
runtime XML audit
track_debug (frame, associated detection, track state)
same-period Confirmed + matched_this_frame payload reverse audit
```

协议目标选择不使用全部 raw detection、Tentative track、Coasted prediction 或历史
周期 detection。
## 5. 实验设计

### 5.1 Delay sweep 与工作点

delay sweep 为：`0, ±1, ±2, ±4, ±8 ns`。当前标签：

| 范围 | 值 | 解释 |
|---|---|---|
| engineering realistic range | `0, ±1 ns` | 用于近零误差、线性局部恢复检查；真实硬件适用性仍需实测标定范围支持 |
| stress test range | `±2, ±4, ±8 ns` | 检查幅度、符号和非理想残差下的稳健性，不等同于硬件规格 |

正式 registered case groups 不做无依据的大 Cartesian product：base 为 3 seeds ×
2 velocities × 2 SNR，另有 low-SNR、high-velocity、alternate texture/geometry
组。完整选择为 15 个 case block × 9 个 delay；每 case 5 periods。

### 5.2 Level-1 / Level-2

- Level-1：每个 selected delay/SNR 使用 100 trials 的 estimator-only Monte Carlo，
  覆盖 5 个方法，输出 bias、RMSE、STD、95% CI、outlier/fallback；
- Level-2：selected production CUDA E2E case，运行 A0/A1/A2/A3 × ON/OFF/TO，
  保存 XML、track_debug、CFAR、payload reverse audit 和 ID-switch audit；
- raw BIN/F32/PNG 在 case evidence 完成并记录 ledger 后由 `--cleanup-raw` 清理，
  compact package 不复制 raw。

### 5.3 Target-off waterfall

target-off 指标必须分四层：

1. CFAR cell false-hit：仅在 production 输出提供独立 valid-CUT denominator 时计算；
2. false clusters：使用明确的 OFF CFAR cluster count/IDs；
3. protocol false detections：production detection CSV 行数；
4. false tracks：production debug 中有 Confirmed/output 证据的唯一 track ID。

`Pfa=1e-6` 只写作 theoretical GO-CFAR cell Pfa。结构杂波下的经验量写为
`empirical structured-clutter false-hit fraction`。target-on 没有独立 valid-CUT
分母时，cell-Pfa 保持 NOT_EVALUABLE。

## 6. 理论误差传播

### 6.1 时延估计误差

设：

```text
epsilon_tau = delta_tau_hat - delta_tau
```

校正后残余相位为：

```text
epsilon_phi(f) = 2*pi*f*epsilon_tau
```

因此同一 bandwidth 内的 residual phase RMS 随 `|epsilon_tau|` 和有效频率
spread 增长；它直接进入 F1/F2 cross-spectrum、CSI complex coefficient 和
clutter cancellation residual。

### 6.2 近似 cancellation-loss 模型

在两通道等幅、频带内相位误差均匀且独立权重的近似下，残余相干因子可写为：

```text
rho(epsilon_tau) ≈ |sinc(pi*B*epsilon_tau)|
```

其中 `B` 是有效频带。对实际 LFM、clutter texture、CSI weight 和 CFAR 门限，
不能把该近似直接当作生产性能；正式判断使用 evidence 中的 coherence/CSI residual
与 CUDA 结果，并比较 Monte Carlo 趋势。

### 6.3 WLS 方差

若相位噪声近似独立高斯，且 `w_i` 是逆方差权重，则线性回归斜率给出：

```text
f_bar_w = sum_i w_i f_i / sum_i w_i
Var(delta_tau_hat)
  ≈ 1 / [(2*pi)^2 * sum_i w_i (f_i-f_bar_w)^2]
```

这是当前数据模型下的 weighted-regression variance approximation，不宣称严格
CRLB。它要求有效频点、相位 unwrap、权重模型和独立噪声近似成立；若 experiment
manifest 标记模型不满足，则只报告 Monte Carlo CI。

## 7. ID switch 专项审计

`scripts/audit_delay_track_id_switch.py` 只消费已有 production artifact，不重写
TrackManager。审计关联：

```text
truth target ID
production detection ID/row
track ID
available candidate IDs
innovation/residual
association distance / gate threshold
confirmation / lost / reacquired state
merge/split evidence
```

相邻周期目标 track ID 改变时分类为：

```text
miss_to_reacquisition
multiple_candidate_competition
false_track_takeover
gate_boundary_crossing
position_velocity_jump
track_confirmation_or_reset
other
```

如果 production debug 没有 candidate、innovation 或 lifecycle 字段，审计必须写出
缺失来源并落入 `other`/NOT_EVALUABLE，不能从最终 ID 序列臆造机制。delay 校正导致
检测位置变化、候选排序变化与 TrackManager 的 ID 维护是两个层次，报告分别统计。

## 8. Paired statistics

二元 Pd/track outcome 按 scene/case block 配对，记录：

```text
Current miss → Blind hit
Current hit → Blind miss
exact McNemar result
effect size / discordant counts
```

连续 position RMSE、velocity RMSE、angle RMSE、cancellation 和 transfer 使用
paired scene-block bootstrap 95% CI；同一连续航迹的多个周期不当作完全独立样本。
没有预先声明的 materiality threshold 时，`materiality` 保持 indeterminate，不用
p-value 替代工程重要性。
## 9. Pilot 与正式结果

修正 packet boundary 后的 v3 pilot 已证明：

- simulator 六个 A0/A1 场景、A0–A3 元数据和 production 12 branches 可运行；
- 0 ns、+4 ns、−4 ns 的 D3 选择值约为 `−0.122 ns`、`+3.410 ns`、`−0.714 ns`；
- A3 使用 target-free estimate，`truth_used_in_estimator=false`；
- target-on 的 generic detection Pd 因缺少独立 detection-opportunity denominator
  保持 NOT_EVALUABLE，target-period hit 另行报告；
- OFF waterfall 的 valid-CUT cell layer 保持 NOT_EVALUABLE，而 clusters、protocol
  detections、false tracks 分开记录；
- v3 的结构杂波 pilot 出现明显 case-dependent metric 波动，不能作为 formal
  “校正带来收益”的结论；
- v2 保留为跨 packet 拼接错误的失败证据，不与 v3 混排。

历史 Formal-v1 矩阵已按上述定义完成并收敛到
`outputs/formal_evidence/stage1_delay/`；当前正式结论使用独立 Formal-v2
`outputs/formal_evidence/stage1_delay_v2_full_20260917/`。compact package 恰好包含九个文件：估计器与
baseline、A0/A1/A2/A3、OFF/ON、TrackManager、ID-switch 和 paired statistics；其
`manifest.json` 的 `source_status=completed`、`source_run_manifest_sha256`、源码身份、
GPU/磁盘状态和 row counts 可反查正式源 manifest。Formal-v2 raw source tree 已在 compact
校验后删除；根 run manifest、135 个 case manifest 的压缩归档、命令历史和 retention
manifest 保留在 v2 compact 目录。v1/v2 的精确删除与保留边界见
`docs/清理记录_2026-09-17.md`。

## 10. 其他单误差成熟度

| 单误差 | physical signature | candidate estimator | 当前 blind 状态 | 混淆/外部先验 | 分类 | 下一步 |
|---|---|---|---|---|---|---|
| channel delay | frequency phase slope、F1/F2 mismatch | D1/D2/D3、correlation/GCC-PHAT | 135-case formal 闭环已完成；A3 为 target-free | 与 servo 近混淆；硬件量纲尚未实测 | A | 以硬件标定范围和 materiality threshold 再做外部验证 |
| inter-pulse phase drift | slow-time/pulse phase slope | pulse-domain phase regression | 有观测候选，未完成 blind production | 与 Doppler/velocity 混淆 | B | 加独立 slow-time prior 与 coupled deterministic baseline |
| fixed phase | frequency/pulse constant phase offset | cross-spectrum intercept | 候选观测，未完成生产校正 | 与 channel sign/reference 混淆 | B | 固定参考通道并做 known-error recoverability |
| baseline/geometry | angle-dependent phase slope/CTDR/range block | geometry residual fit | 有观测矩阵，未完成闭环 | 与 servo/yaw 混淆 | B | 引入 reported geometry prior 后再盲估计 |
| servo angle | angle/phase/beam response | sensor prior + residual fit | target-assisted 有证据；target-free 回退 | 与 delay 近混淆 | B | 先做 sensor-fused deterministic estimator |
| platform velocity | slow-time ridge、P38、CTDR | header/INS prior + residual | known/report split 已审计，blind 不足 | 与 Doppler/clutter stats 混淆 | B | 明确 true/reported source 后再做残差估计 |
| yaw | angle/geometry/phase coupling | attitude prior + residual | yaw-first blind 当前回退 | 与 baseline/servo 混淆 | C | 暂停无先验 blind estimator |

Class A 表示 F1/F2 单独已有足够稳定的可观测证据；Class B 表示有响应但需
sensor prior 或 residual disentanglement；Class C 不强行实现 blind estimator。

## 11. 当前限制与 multi-error gate

单一 channel-delay formal 已完成，但不自动满足 multi-error coupled study 入口。
进入下一阶段前还必须有：

1. 对本轮单误差结果补充预先声明的工程 materiality threshold，并单独解释 ID-switch
   增长和结构杂波 false-count 变化；
2. 至少一个耦合误差组合的 deterministic baseline，明确参数混淆矩阵与可恢复空间；
3. 传统方法存在可量化、可重复且有 materiality 的 recovery gap；
4. 明确哪些字段能由 sensor prior 提供，哪些 residual 可由 F1/F2 识别。

在这些条件成立前，`ai_training=false`、`router_enabled=false` 保持不变；不训练
MLP、通用 `delta-alpha`/复权残差网络、RD image-to-image、多误差 neural network，
也不扩展 native 4ch STAP/JDL、4ch covariance/loading 或 4ch CUDA。

## 12. 复现与交付检查

最短正式入口见第 1 节。完整交付前必须在源码 worktree 执行：

```bash
python3 -m pytest -q
python3 -m py_compile scripts/run_delay_stage1_formal.py \
  scripts/analyze_delay_stage1_formal.py \
  scripts/audit_delay_track_id_switch.py \
  scripts/delay_stage1_core.py
python3 scripts/analyze_delay_stage1_formal.py \
  --run-manifest outputs/formal_delay_stage1_20260916/manifest.json \
  --output-root outputs/formal_evidence/stage1_delay
cmake --build build -j4
ctest --test-dir build --output-on-failure
git diff --check
```

并检查：

```text
formal source manifest 的 command/config/template/git/GPU/disk 记录
formal_evidence/stage1_delay/ 恰好九个文件，row count 与 source manifest 一致
AI/Router/STAP flags 全为 false
所有 A3 correction 来源为 target-free estimate
OFF denominator 缺失处为 NOT_EVALUABLE
raw cleanup ledger 与 compact evidence 均可反查
```

上述 analyzer 命令应在删除 production/TrackManager 原始 CSV 前运行；清理完成后，九个
compact 文件是最终证据包，尤其 `id_switch_audit.csv` 不从已删除的原始关联文件重建，避免
把空审计误读为通过。
