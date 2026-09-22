# AI_CSI_40：等效复校准与物理校准定向 Pilot 报告

状态：Phase-I 定向机制 pilot；不等同于生产 CUDA、GO-CFAR 或 TrackManager 验收。

## 结论先行

本次 synthetic sanity 与 targeted mechanism pilot 均通过：`sanity_status=passed`、
`pilot_status=passed`。决策为：

报告状态字段：`sanity_status` = `passed`；`pilot_status` = `passed`。

```text
NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE
```

理由是：Class-E 的传统 SCC/DDC/DDC-RB/Robust baseline 在等效失配上有效，
但 fast-time delay、servo pointing、platform velocity 的物理状态校正和下游安全
尚未接入本轮 production tap。因此不能把 clutter residual 或 sensor prior 写成
物理状态已经恢复，也不能把 `NOT_EVALUABLE` 转成 pass。

本报告对应的 compact evidence 位于
[`outputs/formal_evidence/equivalent_vs_physical_pilot/`](../outputs/formal_evidence/equivalent_vs_physical_pilot/)，
核心文件为：

`manifest.json`、`observations.json`、`method_contract.csv`、`gamma_recovery.csv`、
`clutter_metrics.csv`、`target_transfer.csv`、`detection_metrics.csv`、
`track_metrics.csv`、`decision_matrix.csv`、`report.md`。

## 运行身份与信息边界

- 配置：`configs/research/equivalent_vs_physical_calibration_pilot.json`。
- seed：`20260920`；synthetic F1/F2 尺寸为 `24 × 6`，range-band 为 `6`，
  `min_support=4`，robust phase threshold 为 `0.45 rad`。
- Pilot manifest 记录的源码身份为 Task 4 fix commit `901fa554`，配置 SHA-256、
  命令、退出码 `0`、dirty 状态和 Task 3 sanity 文件的 SHA-256。
- F1/F2 机制数组只在内存中生成，`raw_arrays_written=false`；保留的是 compact
  CSV/JSON/Markdown，不写入大体积 raw array。
- Mode-A：从 `OFF=C+N` 估计并作用于 `ON=S+C+N`；Mode-B：从 ON observable
  估计，不读取 target truth。两种模式的 OFF/ON observable 在 manifest audit 中
  明确不同。
- `ai_training=false`、`router_enabled=false`、native four-channel STAP 仍冻结。

## 1. AGENTS.md 如何重新定义研究边界

`AGENTS.md` 现在把 Phase-I 固定为：

```text
恢复 Class-P 物理配准/状态
→ 用 clutter-derived Gamma 吸收 Class-E 乘性通道失配
→ 普通两通道对消与 robust residual
→ CFAR / Pd / target transfer / TrackManager 安全判定
```

其中：

- `Class-E` 只表示当前 F1/F2 域内可由 `F2(r,fD)≈Γ(r,fD)F1(r,fD)` 吸收的等效
  复失配；SCC、DDC、DDC-RB、Robust DDC、Robust DDC-RB 都是传统 baseline，
  不宣称算法创新。
- `Class-P` 保留 fast-time/range、Doppler registration、pointing/geometry、
  velocity、geolocation 和 track-state 误差；杂波功率下降不是物理恢复证明。
- `Class-D` 表示真实失相干或信息损失，用 coherence floor 和 robust residual
  表达，不能伪装成可完全拟合的系统误差。

## 2. README 如何重新描述 Phase-I

`README.md` 已改为“Phase-I 等效复校准与物理系统校准层次”，并明确：

1. 科学输入仍是四路 protocol IQ 经 `(1,3)`、`(2,4)` 得到 F1/F2；raw six-pair
   仅用于诊断和交叉验证。
2. Phase-I 是 physical coarse correction + robust residual complex calibration，
   不是把每个现象都强行分解为物理参数。
3. channel-delay 仍是 Class-P 候选问题；如果 DDC 能降低 clutter，仍必须检查
   range/angle/position/track 是否正确。
4. native four-channel STAP/JDL、covariance/loading、额外空间自由度和相关 CUDA
   优化仍属于 Phase-II；AI gate 仍关闭。

## 3. 实现了哪些 baseline

新增 `scripts/two_channel_complex_calibration.py` 及其单元测试，固定使用：

```text
Gamma = sum(F2 * conj(F1)) / sum(abs(F1)**2)
Y     = F2 - Gamma * F1
```

交付的方法契约为：

| 方法 | 含义 | 输入/限制 |
|---|---|---|
| M0 `uncalibrated_subtraction_proxy` | 历史机制-only 未复校准 subtraction proxy；不是 production Current | F1/F2 |
| M1 `ordinary_complex_subtraction` | 历史机制-only 直接 `F2-F1` | F1/F2 |
| M2 SCC | 单一复系数 | F1/F2/clutter support |
| M3 DDC | 每 Doppler 一个复系数 | F1/F2/clutter support |
| M4 Robust DDC | circular phase outlier rejection 后的 DDC | 不读 target truth |
| M5 DDC-RB | range-band × Doppler 局部系数 | 显式 local support |
| M6 Robust DDC-RB | 局部稳健复校准 | 显式 local support |
| P1/P2 | blind physical / physical+robust | 物理 observable；P2 仅在有 correction model 时执行 |
| PK/PK+R | known-error correction upper bound | evaluator-only，不可部署 |

物理状态字段固定使用 `RADAR_ESTIMATED`、`SENSOR_PRIOR_ONLY`、
`PRIOR_PLUS_RADAR_RESIDUAL`、`KNOWN_TRUTH`、`NOT_EVALUABLE`；servo/velocity
本轮只有 sensor prior，因此不能写成 `RADAR_ESTIMATED`。支撑不足、零分母、非有限输入和局部不可估计状态均保留 `NOT_EVALUABLE` 或
`PARTIAL`，不会静默回退到 Current。

## 4. Synthetic sanity 是否通过

Task 3 的 `T1–T5` 全部通过，且 Task 4 runner 在覆盖同名 CSV 前将 Task 3 文件
复制到 `equivalent_vs_physical_pilot/sanity/` 并保留哈希。

| case | 结果 | 当前证据 |
|---|---|---|
| T1 constant Gamma | passed | SCC 恢复常数 Gamma |
| T2 Doppler-dependent Gamma | passed | DDC/SCC superiority ratio `2.4356e-32` |
| T3 range-Doppler Gamma | passed | DDC-RB/DDC superiority ratio `1.2502e-30` |
| T4 strong moving-target contamination | passed | Mode-B Robust DDC residual `8.36e-32`，ordinary ON DDC `5.75`，排除 `6` 个 outlier |
| T5 true decorrelation | passed | Mode-A floor `0.09`，理论 coherence `0.992205`；Robust Mode-B `0.090009` |

T2/T3 的比较是同一 F1/F2 信息条件下的传统 baseline 比较；这些结果不把 DDC 或
DDC-RB 改名为新算法。

## 5. delay / servo / velocity / decorrelation pilot 各得到什么结果

本轮五个 mechanism case 的固定标签为：`pure equivalent mismatch`、
`fast-time channel delay`、`servo pointing`、`platform velocity`、
`true decorrelation`。

### 5.1 Pure equivalent mismatch

`E1` 在 Mode-A target-free 条件下，SCC residual power 为约 `1.11e-30`，Robust
DDC-RB 为约 `1.88e-31`；说明纯乘性等效失配可由 clutter-derived Gamma 吸收。
Mode-B 的 ON 目标污染会使普通 SCC residual 变为约 `0.201`，而 Robust DDC-RB
仍约为 `1.74e-31`。这只是通道残差证据，不是 target Pd 或 TrackManager 证据。

### 5.2 Fast-time channel delay

这次不把 delay 偷换成 Doppler-only Gamma。pilot 在 24 个 fast-time frequency
cycles/sample 上生成：

```text
Gamma(f) = exp(-j * 2*pi * f * delay_samples)
```

blind P1 从实际 F1/F2 cross-phase 和频率轴拟合得到 `0.375 samples`，evaluator
误差约 `2.2e-16`。P2 实际执行 physical delay compensation + Robust DDC-RB，
clutter residual 为约 `2.18e-31`；known-error upper bound + robust residual 为
约 `1.61e-31`。这是受控机制级互补证据，不是生产 range/angle/track 恢复证明。

等效 DDC-RB 在 Mode-A 的 residual 仍约 `0.281`，因此本 pilot 不支持“只靠
Gamma 已经等价替代 physical delay correction”的结论。

### 5.3 Servo pointing

pilot 保留数值的 sensor-prior observable：`0.16 deg`，相对 evaluator value
`0.18 deg`，误差 `0.02 deg`。P1 能报告该物理 observable，但没有接入 pointing
model correction、角度/位置 truth-match 或 TrackManager，因此 P2 physical+robust
标记为 `NOT_EVALUABLE`。Clutter residual 的下降不能被解释为 servo state recovery。

### 5.4 Platform velocity

pilot 保留数值的 sensor-prior observable：`1.10 m/s`，相对 evaluator value
`1.25 m/s`，误差 `0.15 m/s`。同样，P1 只证明 observable 可审计；没有生产
kinematic correction、velocity RMSE 或 track-state propagation，因此 P2 和下游
结论保持 `NOT_EVALUABLE`。

### 5.5 True decorrelation

在 Mode-A target-free 条件下，D1 的实际 F1/F2/clutter-support cross-correlation
coherence 为 `0.992509`；局部正交 decorrelation 的 single-coefficient floor 为
`0.0774539`，SCC 与 Robust DDC-RB residual 都达到该 floor。manifest 的
`decorrelation_audit.floor_check_status=passed` 只对 Mode-A M2/M6 floor 检查负责。
Mode-B 仍单独报告 ON 污染影响，不能用它替换 Class-D floor 结论。

## 6. DDC 能替代哪些 physical correction

能被当前 pilot 支持的只有“等效通道关系”层面：

- pure equivalent amplitude/phase mismatch；
- 在某个局部 range-Doppler processing domain 内可近似为 Gamma 的残余乘性失配；
- 对这些情况，SCC/DDC/DDC-RB/Robust variants 可显著降低 clutter residual。

这不等于它恢复了物理坐标或状态。fast-time delay 的数值结果反而显示：虽然局部
DDC-RB 能减少部分 clutter residual，它不能代替 frequency-domain fractional-delay
model correction 的语义验证。

## 7. DDC 不能替代哪些 physical correction

当前不能替代：

- fast-time/range registration 和 fractional-delay correction；
- servo pointing/geometry correction；
- platform velocity、Doppler registration、geolocation 和 track-state correction；
- 任何需要 angle/position/velocity/TrackManager/PIPE 传播验证的状态修复。

本轮 `target_transfer.csv`、`detection_metrics.csv`、`track_metrics.csv` 对未运行的
production 字段明确写 `NOT_EVALUABLE`；历史参考没有混入新的 measured aggregate。

## 8. physical + residual calibration 是否有互补收益

有受控机制级证据，但还没有生产级结论。delay case 中，blind P2 的 physical
delay estimate + Robust DDC-RB residual 约 `2.18e-31`，而单独等效 DDC-RB Mode-A
约 `0.281`；这支持“先物理粗校准、再复通道残差校准”的研究方向。

servo/velocity 的 physical+robust model 尚未实现到生产 tap，故不能外推相同互补
收益。当前 decision matrix 将 Class-P 和 downstream safety 保持为
`NOT_EVALUABLE`，而不是用该 synthetic delay 数字替代真实系统证据。

## 9. Paper-1 channel delay 是否仍值得继续

值得继续作为 Class-P physical-registration study，但不应继续把“复权拟合”本身
写成 novelty。优先级应改为：

1. 用 production F1/F2 和同一 target-free/ON 信息条件验证 fractional delay 的
   单位、符号、频率轴和实际 correction path；
2. 与 SCC/DDC/DDC-RB/Robust DDC-RB 进行等信息比较；
3. 用真实 valid-CUT denominator、CFAR、target transfer、TrackManager/PIPE 和
   ID-switch 审计判断物理 correction 是否仍有独立价值。

历史 channel-delay 文档保留原边界，尤其是
[`AI_CSI_36`](AI_CSI_36_单一系统误差确定性自校准阶段报告.md)、
[`AI_CSI_37`](AI_CSI_37_ChannelDelay_Finalization_Audit.md) 和
[`AI_CSI_38`](AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md)；它们不是本轮
pilot 的新测量结果。

## 10. 下一步应进入什么阶段

当前不是 formal full Cartesian CUDA sweep，也不是直接打开 AI。下一步应进入
小规模、production-tap 驱动的：

```text
coupled physical-state study
```

具体先做 delay/servo/velocity 的 physical correction、Robust residual、target
transfer 和 TrackManager/PIPE 审计；只有这些字段有可追溯 measured 分母后，才判断
是否升级为 formal hierarchical calibration。若 physical correction 对生产状态无
独立贡献，则再收敛到 `GO_EQUIVALENT_CALIBRATION_ONLY`。

## 11. 是否存在任何足够证据允许打开 AI gate

没有。当前证据仍可由确定性的 physical delay fit、传统 clutter-derived Gamma、
Robust outlier rejection 和 Class-D coherence floor 解释；servo/velocity/downstream
还没有完成 production evaluation。故本阶段固定：

```text
ai_training=false
router_enabled=false
本阶段不输出任何 AI decision label。
```

## Decision gate

| Gate | 状态 | 判读 |
|---|---|---|
| Class-E coverage | `PASS` | 同一 F1/F2 输入下的五类传统复校准 baseline 已有 compact rows |
| Class-P need | `NOT_EVALUABLE` | delay/servo/velocity observable 有记录，但 production correction tap 未运行 |
| Class-D limit | `PASS` | 实际 cross-correlation 与 Mode-A 局部 floor 检查通过 |
| Downstream safety | `NOT_EVALUABLE` | 没有本轮 production CFAR/Pd/Pfa/TrackManager/PIPE/ID-switch measured rows |
| AI gate | `CLOSED` | 当前没有 deterministic coupled baseline 失败证据 |
| Phase-II gate | `CLOSED` | native four-channel STAP/JDL 未获得新授权 |

因此本报告的唯一决策为 `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`；生产 tap、下游
CFAR/Pd/Pfa/TrackManager/PIPE 证据仍待当前阶段补齐，AI gate 保持关闭。
