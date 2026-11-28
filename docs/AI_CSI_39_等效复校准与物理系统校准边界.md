# AI_CSI_39：等效复校准与物理系统校准边界

> 状态：Phase-I durable boundary note（2026-09-20）。本文只冻结研究边界、
> 传统 baseline 归属和 decision gate；不新增 pilot 结果，不覆盖 AI_CSI_34–38 的
> 历史证据、限制或源码身份。

## 1. Phase-I hierarchy

当前 Phase-I 不再把问题写成“只要逐项识别每一种物理误差”，也不把普通复系数拟合
写成 AI 创新。固定层次为：

```text
Class-P physical-state calibration
  → Class-E equivalent complex calibration
  → Class-D decorrelation / information limit
  → two-channel CSI / CFAR / TrackManager safety
```

科学输入仍是生产等效 F1/F2：

```text
4ch protocol IQ → (1,3)/(2,4) F1/F2 → two-channel CSI
```

raw six-pair 只能用于诊断和交叉验证，不把额外空间自由度混入 Phase-I 结论。
`Phase-II native four-channel freeze` 继续生效：native four-channel STAP/JDL、
covariance/loading、额外 DOF 及相关 CUDA 优化冻结为 Phase-II 课题。

## 2. Error classes

| Class | 判定规则 | 允许证据 | 不允许的写法 |
|---|---|---|---|
| `Class-E` equivalent complex calibration | 在 F1/F2 当前处理域内可写作 `F2(r,fD) ~= Gamma(r,fD) F1(r,fD)`，且不造成独立 range/Doppler/pointing/geolocation/kinematic/track-state 错误 | `clutter-derived` Gamma、SCC、DDC、DDC-RB、robust DDC、robust DDC-RB，同一 F1/F2 输入和 target-free/robust 估计约束 | 把 complex LS、per-Doppler Gamma 或 range-Doppler Gamma 本身写成创新 |
| `Class-P` physical-state calibration | 改变 fast-time registration、Doppler registration、物理指向/几何、速度、定位或航迹语义 | 物理估计、传感器先验、model correction、单位/符号审计和下游 TrackManager/PIPE 传播 | 只因 clutter power 下降就宣称物理状态已校准 |
| `Class-D` decorrelation limit | 真实时间变化杂波、不可约失相干或信息损失 | coherence floor、target transfer、robust cancellation、`NOT_EVALUABLE` 分母审计 | 把不可约去相关伪装成可完全拟合的系统误差 |

## 3. Channel-delay interpretation

AI_CSI_36–38 的 channel-delay 证据保持原解释：channel-delay 是 Class-P 物理配准问题。
虽然窄域中的相位斜率残差可能被某些 `Gamma(r,fD)` 局部吸收，但正式报告必须继续记录：

1. delay estimate 的单位、符号和 truth-read audit；
2. fractional-delay physical correction 的实际施加路径；
3. A0/A1/A2/A3 信息条件；
4. CFAR、TrackManager、PIPE、ID-switch 和 `NOT_EVALUABLE` 分母边界。

因此 Paper-1 的 reframing 是：“target-free channel-delay physical self-calibration with
traditional equivalent complex calibration baselines”，不是“首创复权校准”，也不是
“已证明 AI 必要”。Blasone baseline attribution 保留为传统等效复校准归属：clutter-only
或 clutter-derived 复权、子带/多普勒/局部 Gamma 和 robust variants 是强 baseline，
只能作为对照或工程组合的一部分。

## 4. Traditional calibration baselines

Phase-I 等效复校准 baseline 至少保留五类，且全部使用相同 F1/F2 信息条件：

| baseline | 含义 | 信息约束 |
|---|---|---|
| SCC | single complex coefficient | target-free clutter support，固定到 evaluation 输入 |
| DDC | Doppler-dependent complex coefficient | per-Doppler clutter-derived Gamma |
| DDC-RB | Doppler-dependent + range-band coefficient | range-band 与 Doppler 局部 Gamma |
| Robust DDC | ON-derived robust per-Doppler coefficient | 不读取 target truth，用相位/幅度稳健规则抑制目标污染 |
| Robust DDC-RB | robust local range-band/Doppler coefficient | 同上，增加 range-band locality 与支撑不足状态 |

支撑不足、零分母、非有限输入和缺少 production tap 时必须写 `NOT_EVALUABLE`，不得静默
回退到 Current。Target-free Mode-A 与 online robust Mode-B 分开报告，不合并成一个
虚假的平均收益。

## 5. Equal-information modes

比较必须区分信息条件和算法能力：

| mode | 输入 | 可比较的问题 |
|---|---|---|
| Mode-A target-free | OFF = C+N 估计 Gamma，固定作用于 ON/TO | 传统校准在无目标训练下能恢复多少 clutter coherence |
| Mode-B online robust | ON = S+C+N 估计 robust Gamma，不读取 target truth | 目标污染存在时稳健估计是否保护 target transfer |
| Class-P physical | target-free 或传感器先验估计物理状态，再施加 model correction | 是否恢复 range/Doppler/pointing/track-state 语义 |
| Class-D boundary | 同一 F1/F2 输入，报告 coherence floor 和 robust residual | 剩余误差是否是不可约去相关或信息损失 |

已知误差只能进入 `Known-error correction upper bound / 已知误差校正上限` 和 evaluator。
blind estimator 不得读取 target truth、system-error truth 或 injected-error truth。

## 6. AI and Phase-II gates

当前固定：

```text
ai_training=false
router_enabled=false
native_four_channel_stap=false
```

不训练 MLP、通用 `delta-alpha`/复权残差、RD image-to-image 或 Router。只有当 Class-P
deterministic correction、Class-E traditional equivalent calibration 和 Class-D
coherence boundary 均已完成同一输入条件下的 target/CFAR/Track 安全性审计，且剩余残差
稳定、可重复、不是 implementation bug、不是普通 Gamma 可解释、也不是真实信息损失，
才允许重新打开 Physics-AI 讨论。

native four-channel STAP/JDL/covariance/loading/DOF/CUDA 优化保持 Phase-II 冻结；历史
B2/B3/B3K 或四通道 reference 只能作为 archive，不是当前 Phase-I production STAP。

## 7. Decision-gate template

后续报告必须按以下模板给出 gate，不得根据旧结论硬编码：

| Gate item | Evidence required | Status |
|---|---|---|
| Class-E coverage | SCC/DDC/DDC-RB/Robust DDC/Robust DDC-RB 同输入比较、support status、target transfer | `PENDING` |
| Class-P need | 是否存在 range/Doppler/pointing/geolocation/kinematic/track-state 残差，及物理校正证据 | `PENDING` |
| Class-D limit | coherence floor、不可约去相关或 `NOT_EVALUABLE` 分母 | `PENDING` |
| Downstream safety | CFAR/Pd/Pfa/TrackManager/PIPE/ID-switch 可追溯审计 | `PENDING` |
| AI gate | deterministic 与 traditional baseline 后仍有稳定且推理可见残差 | `CLOSED` |
| Phase-II gate | native four-channel STAP/JDL 是否获得明确新授权 | `CLOSED` |

允许的结论标签只有：

```text
GO_HIERARCHICAL_CALIBRATION
GO_EQUIVALENT_CALIBRATION_ONLY
GO_PHYSICAL_CALIBRATION_ONLY
GO_COUPLED_PHYSICAL_STATE_STUDY
```

`GO_PHYSICS_AI` 不属于本轮默认输出分支。
