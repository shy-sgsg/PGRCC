# 真实系统误差参数与可观测性分析

> 阶段：`Unknown System Error Characterization / 真实系统未知误差建模与可观测性分析`
>
> 审计日期：2026-09-14；本文件覆盖源码审计、Phase0–6 实现和实际运行证据。
> Phase0 是 paired-reference baseline-phase sanity pilot；Phase1–3 是 blind
> true-geometry estimator/matrix；Phase4–5 已完成 B0/B1/B1K 生产 CUDA CSI/CFAR、
> B2/B3/B3K 离线 STAP reference、bias/Pfa/target-transfer 和信息量匹配审计；
> Phase6 已启动独立 target-assisted servo calibration pilot。B2/B3 仍不是生产 CUDA
> 四通道 STAP；该 servo pilot 也不是 production online estimator。旧 pilot 产物见
> `outputs/unknown_system_error_pilot_20260913_v5/`，本轮产物见
> `outputs/unknown_system_error_pilot_20260914_clean/`、
> `outputs/unknown_system_error_geometry_matrix_20260914_v2/`、
> `outputs/unknown_system_error_geometry_matrix_extended_20260914_formal_v2/`、
> `outputs/unknown_system_error_geometry_nuisance_sweep_20260914_formal_v1/`、
> `outputs/unknown_system_error_end_to_end_20260914_formal_v4/`、
> `outputs/unknown_system_error_pfa_audit_20260914_v8/` 和
> `outputs/unknown_system_error_servo_pilot_20260914_v3/`。

## 1. 研究目标和判定边界

本阶段回答三个先后问题：

1. 真实系统中哪些误差会进入当前生产链，当前代码从哪里读取或假设它们；
2. 哪些误差在四通道回波中留下可分离的观测，哪些误差与固定相位、速度或杂波统计
   混淆而暂时不可辨识；
3. 已知误差校正能恢复多少、只用回波估计能恢复多少，以及恢复后的目标安全性。

主线为：

```text
真实系统未知误差
  → 多通道回波观测
  → 误差/状态参数估计
  → 物理模型修正和通道自校准
  → 通道相干性恢复
  → CSI / 四通道 STAP
  → CFAR、Pd、Pfa、目标保持
```

本阶段不训练 AI。`NO_GO_AI_ROUTER_VALUE` 只关闭 Current/J5/J6 路由的材料性假设，
不关闭未知系统误差研究。

## 2. 当前源码审计摘要

### 2.1 模拟器已经有的误差入口

`simulator/target_injection/channel_impairments.h` 和
`channel_impairments.cpp` 提供了通道幅度、固定相位、逐脉冲相位漂移、范围采样偏移、
时间延迟、通道噪声、IQ 幅相不平衡、基线误差、逐波位相位偏置、采样时钟 ppm、通道
丢失和饱和等入口。配置解析在
`simulator/stage2_statistical_sim/stage2_config.cpp:187-207`，注入发生在
`simulator/stage2_statistical_sim/simulate_stage2_main.cpp` 调用目标注入后、协议写出前。

其中历史 `baseline_error_m` 的 legacy 回归注入是：

```text
baseline_phase_deg
  = 360 × baseline_error_m × sin(theta_cmd) / wavelength
relative_phase_deg
  = fixed_phase + jitter + per_pulse_drift × pulse_id
    + per_beam_bias + baseline_phase
```

这是一阶、角度相关的基线误差相位模型，仅保留为显式
`group_baseline_error_legacy_pilot` 回归模式。新的物理 four-channel geometry 模式
使用 `channel_geometry.mode=true_channel_positions`：echo generator 读取 true
channel positions，报告/处理链保留 reported positions；`reported_channel_positions`
模式则两者一致。幅相、时延、漂移等其他既有损伤仍使用
`new_protocol_read_channel_1/2` 选定通道，因此本阶段只把“通道位置 true/report 分离”
做成真实几何误差，不把所有系统误差假装已经建模。

### 2.2 平台、姿态和伺服角的当前假设

- `simulator/target_injection/radar_geometry.cpp:18-23` 的
  `evaluatePlatformState` 用单一 `platform_speed_mps` 生成直线位置和速度；
- 当前 `simulator/stage2_statistical_sim/stage2_config.{h,cpp}` 和
  `simulate_stage2_main.cpp` 已增加独立 `servo_angle_error`：
  `theta_true_deg = theta_reported_deg + true_minus_reported_deg`；真实角驱动回波、
  波束增益和 LOS，报告角写入协议/header 并供生产 processing/P38 使用。旧的
  `theta_true_deg = theta_cmd_deg` 是默认关闭该开关时的兼容行为；
- `simulator/stage2_statistical_sim/stage2_config.cpp:680` 解析的平台速度同时被
  目标几何、地表/杂波生成和输出链使用，当前没有独立的 true/report velocity；
- 生产 `include/config_structs.hpp:169-195` 保留协议位置、速度、heading、servo 等
  元数据；`src/processOnePeriod.cpp:4915-5007` 默认从位置差重建速度，只有显式选择
  `new_protocol_velocity_source=header` 才使用有效 header 速度；
- 生产 P38/CSI 对齐和运动补偿在 `src/processOnePeriod.cpp:336-379`、
  `:2370`、`:3243-3346` 等处显式使用平台速度、通道间距、伺服/相位中心姿态和
  Doppler。当前已有诊断字段，但没有把“真实运动”和“导航上报运动”的误差作为
  独立可控输入。

### 2.3 四通道生产口径和观测脚本现状

- `include/config_structs.hpp:171-195` 定义读入通道、四通道融合、通道偏移和速度来源；
- `src/dbs/NewProtocolReader.cpp:209-222` 读取通道配置，四通道有效路径为
  `(ch1+ch3)/(ch2+ch4)`，并在 `:698-797` 继续做四通道相位/数据处理；
- `scripts/estimate_channel_delay.py` 和 `scripts/estimate_temporal_phase.py` 仍主要
  使用通道 1/2 的互谱；本轮新增 `scripts/analyze_four_channel_observables.py`，统一
  输出 `C13/C24/C12/C14/C23/C34` 六对观测，但它是离线 raw-IQ 分析器，不是生产运行时
  estimator；
- `scripts/analyze_four_channel_phase_truth.py` 是基于 truth 的四通道相位残差分析，
  不是运行时的未知误差估计器；
- `configs/research/ai_csi_model_mismatch_suite.json` 已有时延、逐脉冲相位漂移和
  `temporal_correlation_rho` 机制，但当前 suite 不是本阶段完整的四通道联合标定矩阵。

## 3. 误差参数清单的字段定义

完整机器可读清单见 [`outputs/system_error_inventory/parameter_inventory.csv`](../outputs/system_error_inventory/parameter_inventory.csv)。
每行至少回答：

- 物理含义；
- 生产代码位置；
- 当前来源（INS/header/position delta/固定配置/模拟器）；
- 是否有独立的 servo 或导航来源；
- 是否能从回波直接看到，以及会影响哪个处理步骤；
- 模拟器是否能注入真值—上报值差异；
- 当前是否已有估计器；
- 当前状态是 `implemented`、`partial`、`not_faithful_injection`、`observable_candidate`
  还是 `confounded_or_nuisance`。

清单覆盖以下最小集合：平台速度、平台位置、roll/pitch/yaw、波束/伺服指向、通道
时延、固定相位、相位漂移、增益失配、时钟/定时、基线几何、载频，以及杂波时间去相干；
同时记录 IQ 幅相失配、范围采样偏移和通道丢失/饱和等已有注入项。

## 4. 误差传播关系

传播关系和证据位置见 [`outputs/system_error_inventory/propagation_map.csv`](../outputs/system_error_inventory/propagation_map.csv)。
当前可直接从实现读出的主关系如下；它们是模型审计的第一版，不代替后续逐项推导和
数值验证。

| 误差/状态 | 先改变的物理量 | 进入生产链的位置 | 需要观察的结果 |
|---|---|---|---|
| 平台速度/速度方向 | 平台状态、相对径向速度、等效两程延迟 | CTDR、P38、CSI 对齐、运动补偿和定位 | 杂波脊、慢时间相位、导向失配、定位偏差 |
| 平台位置 | LOS、距离、地面投影和相位中心位置 | 几何、距离单元、P38/定位 | range/azimuth 偏差、相位残差、目标迁移 |
| roll/pitch/yaw 与 heading | 波束 look vector、相位中心姿态和基线投影 | 几何、四通道相位补偿、STAP steering | pair phase、beam gain、空间导向残差 |
| 伺服/波束指向 | 命令角与真实角的差、基线在波束方向的投影 | 波束增益、通道相位、P38/CSI | 随波位变化的相位斜率、目标增益变化 |
| 通道时延 | 频率相关相位斜率 | 脉压、CTDR、CSI | range-frequency residual、相干性 |
| 固定相位 | 常数通道相位差 | 四通道融合、CSI/STAP | 固定 pair phase；与几何截距混淆 |
| 慢时间相位漂移 | pulse-to-pulse 相位 | CSI/STAP 协方差和 Doppler | slow-time phase residual、杂波抑制 |
| 增益/IQ 幅相失配 | 通道复增益和镜像分量 | 融合、协方差、目标幅度 | 幅度比、协方差污染、目标保持 |
| 时钟/定时 ppm | fast-time 采样轴和慢时间相位 | 脉压、Doppler、跨通道相位 | range/Doppler 漂移、互谱斜率变化 |
| 基线几何 | pair-specific 空间相位 | 四通道相位补偿、CSI/STAP steering | 随 `sin(theta)` 变化的 pair phase、闭合相位 |
| 载频 | wavelength、chirp/相位尺度 | 几何相位、脉压、Doppler/导向 | 全链路相位比例变化；需先确认可观测性 |
| 杂波时间去相干 | clutter innovation / slow-time correlation | 协方差、CSI/STAP、CFAR | coherence 下降；不能当成固定通道相位修正 |

对当前实现，基线误差的一阶观测模型可写为：

```text
phi_pair(b) ≈ 2π · delta_d · sin(theta_b) / lambda + phi_fixed
```

多波位的角度变化用于分离斜率 `delta_d` 与固定相位截距 `phi_fixed`；单波位时二者
通常不可分离。平台运动模型还必须保留 `velocity → equivalent two-way delay / P38`
的关系，不能只在最终检测坐标上做经验补偿。

## 5. 多通道紧凑观测量与可辨识性

对四通道复数回波 `X_1...X_4`，下一版观测接口应按波位、脉冲和快时间/频率保留六对
互谱：

```text
C13, C24, C12, C14, C23, C34
```

建议从无目标或目标掩膜后的 clutter-support 计算稳健统计量，并同时保留：

- 每对互谱的幅度、展开相位和频率斜率；
- 慢时间相位增量和波位间相位斜率；
- 闭合相位，例如 `arg(C12)+arg(C23)-arg(C13)`，用于削弱单通道公共相位的影响；
- pair 之间的相干系数、有效样本量和质量/置信度；
- 当前波位的命令角、header servo、位置/速度来源和四通道几何配置。

可辨识性先按以下规则审计，而不预先声称唯一可解：

| 参数 | 最小所需变化/冗余 | 当前判断 |
|---|---|---|
| 固定相位 vs 单波位基线误差 | 需要多波位角度变化，或独立几何参考 | 单波位混淆 |
| 时延 vs 固定相位 | 需要频率维互谱斜率 | 时延有候选观测，已有 1/2 通道估计器 |
| 相位漂移 vs 杂波去相干 | 需要逐脉冲相位轨迹、相干幅度和多 pair 交叉验证 | 不能只看一个平均 coherence |
| 平台速度 vs P38/目标径向速度 | 需要 header/position-delta/几何先验和跨波位/跨脉冲结构 | 当前 pilot 只使用 velocity header 作为 nominal metadata，true/report velocity 尚未分离 |
| 伺服角 vs yaw/基线投影 | 需要 servo/header 与多通道空间相位的联合观测 | Phase6 已有 true/report 注入和 deterministic pilot；与 yaw/基线的联合可辨识性仍未完成 |
| 四通道几何误差 | 需要六 pair 和已知阵列 offset；单独 1/2 pair 只能给局部证据 | 已实现 true/report geometry、离线六-pair blind fit 和生产前 raw-IQ correction |

## 6. 首个 pilot：基线几何误差的多波位观测

### 6.1 选择理由

首个 pilot 选择 `baseline_error_m`，理由是它已经有配置、真值记录和明确的一阶物理
相位关系，并且能直接检验“多波位回波能否把角度相关几何误差从固定相位中分离”。
相比之下：

- 平台速度误差虽然重要，但当前模拟器用同一个速度驱动真实运动、杂波和输出 header，
  需要先增加独立的 true/report state 才能做忠实 pilot；
- 伺服/波束指向误差曾使用 `theta_true_deg = theta_cmd_deg`；Phase6 已增加独立
  true/report 角，并先以多波位、多 seed pilot 验证观测链；
- 时延和慢时间漂移已有历史估计证据，适合作为校准链回归，不适合作为新的首个
  可观测性问题。

### 6.2 四条件设计

每个多波位场景保留同一输入、同一随机种子和同一评价口径：

| 条件 | 误差处理 | 用途 |
|---|---|---|
| Ideal/No-error | 关闭所有相关误差 | 干净基线 |
| Current + unknown error | 注入 `baseline_error_m`，生产链不知道该值 | 量化退化 |
| Known-error correction upper bound | 使用注入真值按物理模型校正 | 可恢复空间上限，不是运行时方法 |
| Estimated-error correction | 只用 OFF/clutter-support 多通道观测估计，再校正 ON/target 数据 | 真实自校准能力 |

Estimated 条件必须禁止读取 `channel_impairment_truth.csv`、目标 truth 或未来周期
数据；Known 条件可以读真值，但只能用于评价上限。优先从 OFF 数据估计，防止目标
回波进入校准闭环。

### 6.3 Baseline layers 与公平性

误差条件和算法层要正交记录，避免把“校准有效”与“增加空间自由度”混成一个增益：

| 层 | 输入与处理 | 比较用途 |
|---|---|---|
| B0 Current | 四通道协议 IQ → `(1,3)/(2,4)` → F1/F2 → 生产 CSI | 生产兼容基线 |
| B1 calibrated 2ch CSI | 同一 F1/F2/CSI 链，在已知或估计校准参数后运行 | 隔离通道/物理校准对 Current 的贡献 |
| B2 four-channel STAP/reference | 四通道 raw IQ 保留四个空间自由度，按同一场景构造 STAP | 测量增加空间自由度与四通道算法层的能力 |
| B3 calibrated four-channel STAP | 四通道 raw IQ 先应用已知或估计校准，再运行 STAP | 测量校准与四通道 STAP 的组合收益 |

四层应使用相同的目标/背景、波形、波位和随机种子；训练/协方差数据只来自 OFF 或
clutter+noise，target protection、steering 来源、CFAR、Pd/Pfa 和目标传递评价保持
一致。Estimated 层不得读取误差 truth；Known 层必须显式标成上限。另设信息量匹配
变体，用于回答“算法收益”而非“多了两个空间自由度后的总收益”。

### 6.4 观测与估计流程

1. 用现有 Stage2 四通道 raw IQ 生成多个命令角/波位，记录每波位 header、geometry、
   通道配置和注入 manifest；单波位不能作为基线误差可辨识性结论。
2. `scripts/analyze_four_channel_observables.py` 流式读取协议包，对每个波位/频率支持
   计算六对 `C13,C24,C12,C14,C23,C34`，输出幅度、相位、相干系数、有效样本数和闭合相位。
3. 对每个 pair 先估计频率不变的相位统计，再按
   `phi_obs - phi_nominal = projection*delta_d + delta_phi_pair` 做加权 circular
   fit；固定相位是每 pair nuisance，垂直 pair 用作 control fit，水平 pair 提供
   `delta_d` 信息。
4. Phase1 blind estimator 只消费一份 unknown-off 四通道 IQ、reported/nominal
   geometry 和过滤后的 waveform/platform/beam metadata；不消费 ideal、truth、known
   或 future metrics。六-pair 模式要求公共 fit，单 pair `C12` 仅用于对照并显式记录。
5. Phase4 把估计值作为 raw-IQ 物理相位修正输入，再运行生产 Current CSI/GO-CFAR；
   B2/B3/B3K 使用同一 raw 条件的离线 JDL-3x4 conventional STAP reference。每条结果
   保留逐字段来源、估计残差、失败/fallback、输入 SHA-256 和 runtime。

### 6.5 评价指标

至少报告：

- 六 pair 的幅度、相位残差、频率/角度斜率和 coherence；
- CSI 杂波抑制、四通道 STAP 杂波抑制及两者的输入信息条件；
- target causal transfer、目标幅度/相位保持和目标检测迁移；
- production GO-CFAR 的 Pd、Pfa、虚警簇和漏检；
- `Known − Current`、`Estimated − Current` 和 recovery ratio；
- 估计失败率、质量门限、fallback 次数及 OFF/ON 数据隔离。

### 6.6 Phase0 paired-reference sanity 状态

有界 raw-IQ pilot 已运行，证据位于
[`outputs/unknown_system_error_pilot_20260913_v5/`](../outputs/unknown_system_error_pilot_20260913_v5/)。
它实际使用一个固定种子、1 个 period、7 个命令角、8 个脉冲/波位和 4 通道 float32
协议包。四个条件和结果如下：

| 条件 | 实际证据 | 平均相位残差（相对 Ideal，rad） |
|---|---|---:|
| Ideal/No-error | Stage2 target-on，无基线扰动 | 0 |
| Current+unknown-error | Stage2 target-on，`baseline_error_m=0.01 m` | 0.2004197259 |
| Known-error correction upper bound | 用配置真值反向旋转通道 2/4 | 9.8716e-9 |
| Estimated-error correction | OFF 六-pair 拟合后反向旋转通道 2/4 | 1.0053e-8 |

OFF 多波位拟合得到 `0.009999999925 m`，绝对估计误差约 `7.5e-11 m`；按
`Known − Current`、`Estimated − Current` 计算的相位恢复比为 `0.9999999991`。由于
这是纯相位注入，原始 pair coherence 本身几乎不变（约 `0.01417115`），所以该
结果只证明观测/估计/逆相位链在受控输入上的闭环，不证明 CSI/STAP 对消改善。

该结果已在 clean archive `/tmp/pgrcc_clean_823ebae` 重跑，manifest 标记
`operational_blind=false`、`estimator_mode=paired_ideal_reference`；因此只能作为
Phase0 的 baseline-phase sanity，不能作为线上估计器或 CSI/STAP 结果。

## 7. Phase1–6 实际运行结果（2026-09-14）

### 7.1 Phase1/2 blind estimator 与 true/report geometry

Phase2 的 true geometry fixture 让通道 2/4 的 true `x` 相对 reported `x` 增加
`delta_d`，处理端只读取 reported geometry。Phase1 的 estimator 输入契约由
`estimator_allowed_metadata.json` 和 manifest 记录，禁止读取 ideal/truth/known/future
字段。三速度 E2E calibration 的 unknown-off 输入仅得到
`estimated_delta_d=0.00265 m`，外部评价真值为 `0.00250 m`；四个水平 pair 的估计范围
为 2.635–2.660 mm，最大 pair 间差 0.025 mm，global phase RMSE 0.007436 rad，
closure residual mean 0.000297 rad、P95 0.000685 rad。

### 7.2 Phase3 blind matrix

矩阵覆盖 9 个水平 `[0, ±1, ±2.5, ±5, ±10] mm`、3 个 seed、至少 7 个指定角度，
固定幅相/噪声/纹理 nuisance；每个 case 都用单独 unknown-off raw IQ，并在运行后删除
临时 raw，仅保留 hash、配置、日志、estimator JSON 和汇总 CSV。27/27 case 的六-pair
和 C12 single-pair 都 fit，无 failure/fallback。下表单位为 mm；CI 是 4000 次
percentile bootstrap 的估计均值 95% CI。

| true delta | six mean | six CI95 | six bias | six RMSE | single RMSE |
|---:|---:|---:|---:|---:|---:|
| 0 | 0.1592 | [0.1475, 0.1650] | 0.1592 | 0.1594 | 0.1756 |
| +1 | 1.1583 | [1.1450, 1.1650] | 0.1583 | 0.1586 | 0.1741 |
| −1 | −0.8425 | [−0.8550, −0.8350] | 0.1575 | 0.1578 | 0.1756 |
| +2.5 | 2.6617 | [2.6475, 2.6700] | 0.1617 | 0.1620 | 0.1758 |
| −2.5 | −2.3442 | [−2.3550, −2.3350] | 0.1558 | 0.1561 | 0.1722 |
| +5 | 5.1625 | [5.1475, 5.1725] | 0.1625 | 0.1629 | 0.1776 |
| −5 | −4.8458 | [−4.8575, −4.8375] | 0.1542 | 0.1544 | 0.1722 |
| +10 | 10.1692 | [10.1550, 10.1825] | 0.1692 | 0.1695 | 0.1812 |
| −10 | −9.8508 | [−9.8600, −9.8400] | 0.1492 | 0.1494 | 0.1670 |

跨全部 27 case，six-pair 绝对误差均值/最大值为 0.1586/0.1825 mm，single-pair
为 0.1739/0.2050 mm；six-pair phase residual RMSE 均值 0.006332 rad，closure
mean absolute residual 0.000282 rad，pair consistency 均值/最大值为 0.0378/0.0650 mm。
零误差 case 仍稳定返回约 0.16 mm，而不是静默伪造零值；这属于当前受控 fixture 的
系统偏置，不能写成“无偏估计”。

扩展 formal matrix 改用 `[0, ±0.5, ±1, ±2.5, ±5, ±10] mm × 3 seeds`，共 33/33
case fit，无 failure/fallback；各水平的结果和 4000 次 bootstrap CI 保存在
`outputs/unknown_system_error_geometry_matrix_extended_20260914_formal_v2/`。零误差
six-pair 平均估计 `0.15917 mm`，最大绝对误差 `0.16500 mm`；各非零水平的误差主要
保持在约 `0.15–0.18 mm`，因此 deadband 取自独立 zero-baseline sensitivity audit，
而不是事后把矩阵结果减去一个经验常数。

独立 one-factor-at-a-time nuisance sweep 从 clean source `dfe7b67` 导出运行，固定
三 seed、三 geometry level `[0, ±2.5] mm`，覆盖固定相位 `0/8/16°`、增益失配
`0/0.35/0.70 dB`、噪声功率 `0.0001/0.001/0.01`、纹理标准差 `0/0.1/0.3`、角域
半宽 `10/15/20°` 和标定距离 `8800/9000/9600 m`，共 18 profiles、162/162 case
fit，无 failure/fallback。固定相位、增益和纹理在该 fixture 下保持约 `+0.159 mm`
零点 bias；噪声功率升到 `0.01` 时零点 bias 降至约 `+0.055 mm`，但这是观测质量/随机
误差敏感性，不能解释为校正；角域半宽 `10°` 的零点 bias 约 `+0.169 mm`；标定距离
`8800/9600 m` 分别产生约 `−0.496/−0.300 mm` bias，而 `9000 m` 为约 `+0.159 mm`。
因此距离/角域和噪声必须作为报告 nuisance 记录，不能把固定 fixture 的 `0.165 mm`
deadband 直接外推到所有工作点。raw IQ 按 runner 策略在每个 nested matrix 后删除，
保留配置、日志、raw SHA、估计 JSON、汇总 CSV 和 manifest。

### 7.3 Phase4 端到端 CUDA/离线 reference

正式 E2E 覆盖 3 个 moving-target velocity：`(-3,2)`、`(-6,4)`、`(-12,8) m/s`。
生产 B0/B1/B1K 各运行 on/off/target-only 三种 split，共 27 行；退出码、CSI manifest
和 P4 evaluation 均完整。下表为三速度均值；生产 Pfa 是 target-off 有效 CUT 上
`hit_cells / (active_rows × dynamic-band-valid-CUTs)`，不是把配置 `pf=1e-6` 当作实测值。

| 条件 | CA dB | coherence | RMSE rad | target-on Pd | target-off Pfa | main runtime ms |
|---|---:|---:|---:|---:|---:|---:|
| B0 Current | 3.1225 | 0.875813 | 0.576723 | 0/3 | 0.002204 | 911 |
| B1 blind estimated Current | 3.1208 | 0.875668 | 0.576985 | 3/3 | 0.002202 | 862 |
| B1K known-error upper bound Current | 3.1207 | 0.875667 | 0.576985 | 3/3 | 0.002204 | 886 |

target-only transfer 的目标匹配结果为 B0 `0/3`、B1 `3/3`、B1K `3/3`；这证明的是
本受控 moving-target fixture 的目标迁移差异，不等价于完整跟踪/PIPE 目标保持。

四通道 STAP 使用仓库既有的离线 `JDL-3x4 reduced STAP` scientific reference，
不是生产 CUDA STAP。三速度均值如下：

| 条件 | SCNR improvement dB | target loss dB | residual P95 power | residual P99 power | Pd | background Pfa |
|---|---:|---:|---:|---:|---:|---:|
| B2 uncalibrated 4ch STAP | −0.3467 | −0.3701 | 4.3788 | 7.2913 | 3/3 | 0.000248 |
| B3 blind 4ch STAP | −0.3426 | −0.3333 | 4.3834 | 7.3449 | 3/3 | 0.000266 |
| B3K known-error 4ch STAP | −0.3437 | −0.3376 | 4.3826 | 7.3391 | 3/3 | 0.000265 |

按 `Known−Current`、`Estimated−Current` 记录的 recovery rows 在
`recovery_metrics.csv`；B3 相对 B2 的 SCNR 和 target-loss 略有改善，但 residual
P95/P99 与 background Pfa 略变差，因此不能把 B3 概括为全面提升。生产 B1 相对 B0
的 CSI 数值恢复量很小，而 target-on Pd 在该 fixture 中从 0/3 到 3/3；两者必须分开
解释。

### 7.4 运行边界与下一步

正式 E2E 使用 clean source commit `c580131`、`worktree_dirty_before=false`，RTX 3050
Laptop GPU、driver 580.173.02、CUDA 13.0，约 50°C、P8、6W/80W；runtime 使用
`main_total` timing scope。历史 exploratory 目录仍保留，但不与该 formal provenance
混用。

仍未完成且不能由本阶段替代的部分：

1. B2/B3/B3K 的生产 CUDA 四通道 STAP；信息量匹配 baseline 已完成离线矩阵，但
   `M3−M2/M1` 仍是包含算法和空间自由度的 composite delta，不能写成纯 DOF 增益；
2. TrackManager/PIPE 的 causal target retention，以及多场景、多周期统计泛化；
3. platform velocity/姿态的独立 true/report state 与 Phase6 后续估计；
4. servo-specific Pd/Pfa 的专门 target-only/paired 评价；当前 servo Core 分支只作为
   处理链有效性审计，不能替代上述性能结论；
5. 只有确定性估计残差稳定且难以解析后，才重新评估 Physics-AI；当前 `ai_training=false`，
   不训练 MLP、通用 `delta-alpha`、RD image-to-image 或 Router。

### 7.5 Phase6 target-assisted servo calibration pilot

在 Phase3/4 clean-source formal 证据稳定后，新增
`servo_angle_error.enabled` 和 `true_minus_reported_deg`。v3 formal 运行从 clean
archive `0a3c7a1` 导出，记录的模拟器/Core SHA-256 分别为
`dacd7af8de2ad7c975957a78f0bca2cf0fc0fe6dc5b4b5459b4865dbd7d91229` 和
`a6eace827e54080fbbf574b510d3d4cc69eb172c431de48dd3963855ac5e3756`；运行前
worktree dirty 为 false。GPU 是 RTX 3050 Laptop GPU，driver 580.173.02，CUDA 13.0，
运行前查询为 P0、48°C、12.56 W、676/4096 MiB、28% GPU utilization。

矩阵为 `0, ±0.05, ±0.1, ±0.2, ±0.5° × {101,202}`，5 个电子扫描波位；
`theta_true` 驱动物理 echo/beam gain/LOS，`theta_reported` 写入协议 header 并供
processing/P38 使用。18/18 Stage2 estimator 为 `fit`；估计误差的 mean bias、RMSE、
mean absolute error、max absolute error 分别为 `−0.01079°`、`0.01385°`、`0.01133°`、
`0.03561°`。两条零误差样本估计为 `−0.0143846°`、`−0.0052771°`，由零误差 sweep
导出 `0.0143846°` deadband，均返回 `NO_CORRECTION_NEEDED`；其余 16 条返回
`APPLY_ESTIMATED_CORRECTION`，无 `FALLBACK_UNIDENTIFIABLE`。

该 estimator 的信息条件是 target-assisted：拟合输入包含 paired ON-OFF target residual、
六对 cross-channel phase/coherence、reported header beam angle 和 configured nominal
target/range hypothesis。它不读取 `servo_angle_truth.csv` 或 known offset 来拟合 servo
估计；这些字段只用于分离 invariant 与结果评价。因此这组数字不能解释为 target-free
servo calibration 能力。

54/54 production Core 分支 exit code 和内部 beam-quality gate 均有效，且 18/18
known correction 副本的 payload byte/hash 不变；16 条 unknown correction 只在 header
副本上修改，2 条 deadband fallback 直接复用 Current raw file。估计器不读取
`servo_angle_truth.csv`，AI/Router 均未调用。v1/v2 失败现场也保留：分别暴露了过短
INS aperture、缺少 clutter support 和 zero-range 几何奇异点，修正为 130 PRT、paired
clutter 与 1 µs 正采样起点后才得到 v3 completed。

该 pilot 仍是 deterministic offline target-assisted calibration，不是 online deployment
estimator；不宣称 servo-specific Pd/Pfa、完整 TrackManager/PIPE 保持或 target-free
泛化。baseline geometry 与 servo 的联合混淆、platform velocity/姿态后续单独处理。

### 7.6 Phase6 target-free clutter-only servo formal

为验证“只用目标自由的 C+N 多通道观测”是否足以支持伺服角估计，新增
`scripts/estimate_clutter_only_servo.py` 与
`scripts/run_clutter_only_servo_pilot.py`。S1 的 estimator 输入固定为 OFF raw
文件和 reported context；代码审计拒绝 ON、ON-OFF 差分、target truth、known error
和 servo truth。Sassist 保留为单独的 target-assisted 对照，S0/S1K 仅作评价分支。

formal 命令为：

```bash
python3 scripts/run_clutter_only_servo_pilot.py \
  --output outputs/clutter_only_servo_formal_compact_v2_20260914 \
  --errors-deg 0,0.05,-0.05,0.1,-0.1,0.2,-0.2,0.5,-0.5 \
  --seeds 101,202 --textures low_texture high_texture \
  --ranges-m 7500,10000 --skip-core
```

该矩阵包含 72 个 Stage2 case、144 条估计行和 288 条决策行；所有 Stage2 exit code
为 0。S1 的 72/72 行均为 `FALLBACK_MODEL_MISMATCH`，原因是 phase、Doppler ridge、
P38 slope 和 multi-beam power 四个 feature family 未通过一致性 gate；因此没有一条
S1 estimate 被用于校正，72/72 决策为 `KEEP_CURRENT`。其输入路径逐行为 OFF only，
四个禁用信息标志均为 false，未产生 target-free 能力的正向 claim。

Sassist 的 72/72 行均为 `VALID`，相对注入 `true_minus_reported_deg` 的 bias/RMSE/MAE
分别为 `−0.00817°/0.06691°/0.04788°`。low/high texture 的 RMSE 分别为
`0.03954°/0.08597°`，7.5/10 km 的 RMSE 分别为 `0.08147°/0.04813°`。这些数字
只描述 paired ON-OFF target-assisted reference，不能转写为 S1 clutter-only 结果。

formal estimator matrix 使用显式标记的 compact input（4096 samples、8 PRT、4096
range crop），manifest 中 `core_skipped=true`、`compact_estimator_input=true`，所以
不作为生产 Core 性能或 Pd/Pfa 证据。为验证修复后的生产链路，另跑了
`outputs/clutter_only_servo_cuda_smoke_boolfix_20260914/`：2 cases × 4 branches 共
8/8 `GMTI_core` 成功，内部 beam-quality gate 全部 valid；GPU 查询为 RTX 3050 Laptop
GPU、driver 580.173.02、CUDA 13.0、P8、49°C、6.26 W/80 W。该 smoke 仍不含目标保持
统计，Core 的 CFAR 配置字段出现 `pf=1e-6` 也不等于实测 Pfa 已达标。

早期错误配置/中断目录保留为审计现场，但不进入上述结论：
`clutter_only_servo_formal_20260914/`（boolean 配置序列化错误）、
`clutter_only_servo_formal_v2_20260914/`（旧输入矩阵中断）、
`clutter_only_servo_formal_compact_20260914/`（range crop 约束失败）。

### 7.7 Phase7 moving-target servo E2E

为把 target-free S1 的 OFF-only 约束带入移动目标链路，新增
`scripts/run_servo_gmti_e2e.py`。场景合同包含 `target_free`、`slow_near_ridge`、
`medium` 和 `fast`；OFF 固定为 C+N calibration input，ON 仅用于 target-bearing
evaluation，TO 仅用于 causal target-transfer evaluation。S1 不能读取 ON、TO、target
truth、nominal target metadata、known injected servo error 或 servo truth；Sassist
另列为 target-assisted reference，不冒充 clutter-only 能力。

formal 命令为：

```bash
python3 scripts/run_servo_gmti_e2e.py \
  --scene target_free slow_near_ridge medium fast \
  --errors-deg 0.2,-0.2 --seeds 101,202,303 \
  --textures low_texture high_texture --ranges-m 7500,10000 \
  --output outputs/servo_gmti_e2e_formal_compact_20260914 --skip-core
```

该矩阵完成 96 个 case、192 条估计、384 条决策，ON/TO Stage2 各 96/96 退出成功；
`e2e_metrics.csv` 的 1152 条记录全部标为 `not_evaluated_core_skipped`。S1 的 96/96
条估计均来自 OFF C+N 且通过输入审计，但全部因 phase/ridge/P38/power feature-family
disagreement 返回 `FALLBACK_MODEL_MISMATCH` 并保持 Current。target-free 的 Sassist
为 `NOT_APPLICABLE_TARGET_FREE`；slow/medium/fast 的 Sassist 是独立 target-assisted
参考，bias/RMSE 分别为 `0.00131°/0.10923°`、`0.00135°/0.10938°`、
`0.00143°/0.10970°`，不转写为 S1 结果。compact formal 不评价 Core、Pd、false-hit、
target transfer 或 TrackManager/PIPE。

另有代表性 CUDA smoke：
`outputs/servo_gmti_e2e_cuda_smoke_20260914/`，1 个 slow-near-ridge case（0.2°、seed
101、low texture、8.75 km）× 4 branches × OFF/ON/TO，共 12 行，全部 `GMTI_core`
exit code 为 0，生产配置签名一致；OFF/ON 的 8 行内部质量有效，TO 的 4 行均因
`[fusion][BEAM-ERR] beam=5 slot=4 stage=doppler_center` 和 `valid=4/5 required=5`
质量门失败。P4 target match 为 0/4、target Pd 为 0，故该 smoke 不提供正向目标保持
结论；Core 中出现 `pf=1e-6` 也不等价于实测 Pfa 达标。

### 7.8 Phase8 Pfa H0–H5 closure

新增 `scripts/run_pfa_closure.py`，把配置的 GO-CFAR `alpha=13.44951031977817`、
配置字段 `pf=1e-6` 与实测独立 CUT 结果分开。H0 使用生产八个 gamma training blocks
和独立 exponential CUT；H1–H5 是固定的局部 ridge synthetic controls，指标名称为
`structured_clutter_false_hit_fraction`，不称为 Pfa，也不输出“configured Pfa achieved”。
重复有效 CUT ID 会直接失败，不能重复计数。

正式命令：

```bash
python3 scripts/run_pfa_closure.py \
  --output-dir outputs/unknown_system_error_pfa_closure_20260914 \
  --seeds 101 202 303
```

结果为 18 rows、18,000,000 个有效且独立的 CUT，excluded/duplicate 均为 0。H0 聚合
命中 `4/3,000,000`，cell-Pfa=`1.3333333333333334e-6`，Wilson 95% CI 为
`[5.185074128686217e-7,3.4286404730940667e-6]`；各 seed 命中数为 1、2、1。H1–H5
结构 false-hit fraction 分别约为 `0.0216097/0.0217493/0.0216097/0.0217050/0.0216097`，
只能作为受控结构杂波分数，不与历史生产 Pfa 直接比较。

### 7.9 Phase9 pure spatial DOF J2/J4

新增 `scripts/run_information_matched_stap.py`，固定 scene、seed、velocity、ROI、
training support、covariance loading、Doppler taps、target steering 和 GO-CFAR，
只改变空间维度：J2 为生产 `(1,3)/(2,4)` pair-fused F1/F2 two-channel JDL/STAP，
J4 为 native four-channel JDL/STAP。该脚本是离线 scientific comparison，不能把结果
写成生产 CUDA 四通道 STAP 优势。

正式命令：

```bash
python3 scripts/run_information_matched_stap.py \
  --output-dir outputs/unknown_system_error_pure_spatial_dof_20260915 \
  --seeds 101 202 303 --velocities 0.5 1.0 2.0
```

9/9 cases、18 method rows、9 pairwise rows 成功且无失败。J2/J4 平均 output-SCNR 为
`31.7348603/24.1101992 dB`，background Pfa 为 `0.00413632226/0.00432787134`，
target-detected mean 均为 1.0；J4−J2 的纯 DOF delta 为 output-SCNR `−7.6246611 dB`、
background Pfa `+0.0001915491`，target loss `−0.6083 dB`。因此当前没有稳定、material
的 J4 优势，production CUDA 4ch STAP 保持关闭。

### 7.10 Phase10 platform velocity true/report split

Stage2 新增显式 `velocity_true_mps` 与 `velocity_reported_mps`，旧 `speed_mps` 仍作为
兼容别名且在未拆分配置时同时填充两者。true 速度只进入
`true_platform_trajectory`、echo phase、clutter Doppler 和 target-relative geometry；
reported 速度写入 INS/header，且 pilot 明确选择 `new_protocol_velocity_source=header`，
由生产 CTDR/P38、clutter ridge model、steering 和 velocity conversion 消费。resolved
scenario、Stage2 XML 以及生产 runtime diagnostics 都保存两者和差值。

符号约定固定为：

```text
delta_v_reported_minus_true = reported - true
estimated correction = estimated_true - reported
```

测试先锁定 V0 Current（unknown error）、V1K Known Correction（evaluation-only upper
bound）和 V1 Blind Deterministic（OFF C+N + reported metadata only）。V1 观测量包括
clutter Doppler ridge displacement、P38 phase slope、CTDR residual、four-channel
slow-time phase，并拒绝 ON/TO、target truth、true velocity 与 known error。

formal 命令：

```bash
python3 scripts/run_velocity_error_study.py \
  --delta-v-mps 0,-0.05,0.05,-0.1,0.1,-0.2,0.2,-0.5,0.5 \
  --seeds 101,202,303 --textures low_texture high_texture \
  --ranges-m 7500,10000 --skip-core \
  --output outputs/velocity_error_formal_compact_v2_20260915
```

108/108 Stage2 case 成功，header 最大绝对误差 `1.5258789076710855e-6 m/s`，输入角色
均为 `OFF_C_PLUS_N_TARGET_FREE_ONLY`。V1K 108/108 仅作已知误差评价，估计 bias 约 0；
V1 108/108 均为 `FALLBACK_MODEL_MISMATCH`，其中 81 条为 observables disagreement，
27 条超出显式 `max_supported_error_mps=1.0`，所以没有盲修正被应用。该质量门是为了
阻止候选一致但整体偏离 reported 的错误“VALID”状态；它是 pilot 的公开假设，不是生产
部署阈值。

代表性 CUDA smoke：

```bash
python3 scripts/run_velocity_error_study.py \
  --delta-v-mps 0.2 --seeds 101 --textures low_texture --ranges-m 8750 \
  --output outputs/velocity_error_cuda_smoke_20260915
```

V0/V1K/V1 共 3/3 `GMTI_core` exit code 0，内部质量均有效；GPU 为 RTX 3050 Laptop
GPU、driver 580.173.02、P8、约 47°C、6.54 W。三分支 runtime dump 均记录
`true_mps=60`、`reported_mps=60.2`、差值 `0.2`，XML 的 source 为 `header`。由于 V1
在该 case 回退 Current，不能从该 smoke 声称 Pd、Pfa 或 target retention 改善。

### 7.11 Phase11 yaw-first attitude pilot

为先拆开姿态误差与伺服指向误差，新增 `scripts/run_yaw_error_study.py`。本阶段只改变
一个因素：`baseline` 保持 yaw/servo 均为 0，`yaw` 只改变
`simulation_geometry.platform_heading_deg`，`servo` 只改变
`servo_angle_error.true_minus_reported_deg`；pitch 和 roll 在配置、source snapshot 和
估计结果中均固定为 0，不做三姿态联合拟合。true yaw 进入物理平台 heading、clutter/target
几何和 echo phase；reported yaw 只作为 nominal reported geometry、beam-steering reference
和 angle-conversion context。当前生产协议没有在本 pilot 中独立消费的 yaw header 字段，
因此不能把该 reported context 写成已经具备的 operational yaw source。

正式 compact 命令：

```bash
python3 scripts/run_yaw_error_study.py \
  --errors-deg 0,-0.5,0.5,-1.0,1.0 \
  --seeds 101,202 --textures low_texture high_texture \
  --ranges-m 7500,10000 --scene target_free moving_target \
  --output outputs/yaw_error_formal_compact_20260915
```

矩阵覆盖 `target_free/moving_target × {baseline,yaw,servo} × {0, ±0.5, ±1}° × 2 seeds ×
2 textures × 2 range centers`，共 240/240 Stage2 case 成功，生成 240 条 blind estimate、
720 条 Current/known/blind decision 和 240 个 source snapshot。blind estimator 的输入审计
为每个 case 一个 `OFF_C_PLUS_N_TARGET_FREE_ONLY` 路径；`target_truth_used=false`、
`true_yaw_used=false`、`known_yaw_error_used=false`，且没有 ON/TO 输入。结果为 240/240
`FALLBACK_MODEL_MISMATCH`、0/240 `VALID`，所有 blind decision 均 `KEEP_CURRENT`；known
branch 仅作为 evaluation-only correction reference，不能当作运行时估计能力。

moving-target 的 120 个 paired target-assisted reference 为 `VALID`，target-free 的 120
个 reference 标为 `NOT_APPLICABLE_TARGET_FREE`。该 reference 仍然估计 effective geometry，
不能在 yaw 条件下把 yaw 与 servo 原因分离，也没有被盲分支使用。由于本矩阵设置
`core_skipped=true`，未评价生产 Core、TrackManager/PIPE、Pd、Pfa、目标保持或部署性能；GPU
查询在该次 compact run 中不可用，但这不影响 Stage2/离线输入审计结果，也不构成 CUDA 结论。

## 8. 后续阶段顺序

1. 保持已完成的六-pair observables、bias/deadband、nuisance 和目标/Pfa 审计可复现；
2. 建立 3–5 period 的生产 TrackManager/PIPE 闭环，逐条审计 Confirmed +
   matched_this_frame 的协议目标来源；
3. 完成 servo-specific 多场景 Pd/Pfa 边界；纯 DOF J4 当前没有足够证据开启 production
   CUDA 4ch STAP；
4. 只有确定性估计残差仍稳定、可量化且难以解析，才评估 Physics-AI；当前
   `ai_training=false`，不训练 MLP、通用 `delta-alpha`、RD image-to-image 或 Router。

## 9. 证据入口

- 机器可读参数清单：[`outputs/system_error_inventory/parameter_inventory.csv`](../outputs/system_error_inventory/parameter_inventory.csv)
- 传播关系：[`outputs/system_error_inventory/propagation_map.csv`](../outputs/system_error_inventory/propagation_map.csv)
- 生成说明：[`outputs/system_error_inventory/manifest.json`](../outputs/system_error_inventory/manifest.json)
- 生产四通道融合：`include/config_structs.hpp`、`src/dbs/NewProtocolReader.cpp`
- 模拟器误差入口：`simulator/target_injection/channel_impairments.{h,cpp}`
- 六对 raw-IQ 观测器：`scripts/analyze_four_channel_observables.py`
- pilot 配置/运行器：`configs/research/unknown_system_error_baseline_pilot.json`、
  `scripts/run_unknown_system_error_pilot.py`
- pilot manifest 和结果：`outputs/unknown_system_error_pilot_20260913_v5/manifest.json`、
  `condition_metrics.json`、`calibration_phase_difference.json`
- Phase3 blind matrix：`outputs/unknown_system_error_geometry_matrix_20260914_v2/manifest.json`、
  `matrix_summary.csv`、`single_pair_vs_six_pair.csv`
- Phase4 E2E：`outputs/unknown_system_error_end_to_end_20260914/manifest.json`、
  `production_metrics.csv`、`offline_stap_metrics.csv`、`recovery_metrics.csv`、
  `target_only_transfer.csv`、`target_off_false_cluster_metrics.csv`
- Phase0 clean rerun：`outputs/unknown_system_error_pilot_20260914_clean/manifest.json`
- 运动与定位模型：`include/motion_comp.hpp`、`include/ctdr_phase_model.hpp`、
  `src/processOnePeriod.cpp`
- 历史时延/相位估计：`scripts/estimate_channel_delay.py`、
  `scripts/estimate_temporal_phase.py`
- bias exact-path audit：`outputs/unknown_system_error_geometry_bias_audit_20260914_v1/`
- deadband/sensitivity audit：`outputs/unknown_system_error_geometry_deadband_audit_20260914_v2/`
- extended geometry formal matrix：`outputs/unknown_system_error_geometry_matrix_extended_20260914_formal_v2/`
- independent nuisance sweep：`outputs/unknown_system_error_geometry_nuisance_sweep_20260914_formal_v1/`
- causal target transfer：`outputs/unknown_system_error_target_transfer_audit_20260914_v2/`
- empirical Pfa controls：`outputs/unknown_system_error_pfa_audit_20260914_v8/`、
  `outputs/unknown_system_error_pfa_formal_reference_20260914_v2/`
- information-matched baseline：`outputs/unknown_system_error_information_matched_baseline_20260914_v1/`，
  以及其 `pairwise_postprocess_manifest.json`
- Pfa H0–H5 closure：`outputs/unknown_system_error_pfa_closure_20260914/manifest.json`、
  `pfa_closure_summary.csv`
- pure spatial DOF J2/J4：`outputs/unknown_system_error_pure_spatial_dof_20260915/manifest.json`、
  `information_matched_pairwise_aggregate.csv`
- platform velocity true/report：`outputs/velocity_error_formal_compact_v2_20260915/manifest.json`、
  `velocity_estimates.csv`、`velocity_decisions.csv`、`aggregate_metrics.csv`；CUDA audit
  为 `outputs/velocity_error_cuda_smoke_20260915/manifest.json` 和 `core_metrics.csv`
- yaw-first attitude：`outputs/yaw_error_formal_compact_20260915/manifest.json`、
  `case_index.csv`、`attitude_estimates.csv`、`attitude_decisions.csv`、
  `aggregate_metrics.csv`；实现与输入审计为 `scripts/run_yaw_error_study.py`，契约测试为
  `tests/test_attitude_source_split.py`
- target-assisted servo calibration pilot：canonical runner
  `scripts/run_target_assisted_servo_calibration_pilot.py`（旧命令
  `scripts/run_unknown_system_error_servo_pilot.py` 保持兼容），结果见
  `outputs/unknown_system_error_servo_pilot_20260914_v3/manifest.json`、
  `servo_estimates.csv`、`servo_decisions.csv`、`core_metrics.csv`
- target-free clutter-only servo formal：
  `outputs/clutter_only_servo_formal_compact_v2_20260914/manifest.json`、
  `servo_estimates.csv`、`servo_decisions.csv`、`aggregate_metrics.csv`；修复后 CUDA
  smoke：`outputs/clutter_only_servo_cuda_smoke_boolfix_20260914/manifest.json`、
  `core_metrics.csv`
- moving-target servo E2E formal：
  `outputs/servo_gmti_e2e_formal_compact_20260914/manifest.json`、`case_index.csv`、
  `servo_estimates.csv`、`servo_decisions.csv`、`e2e_metrics.csv`、`aggregate_metrics.csv`；
  representative CUDA smoke：`outputs/servo_gmti_e2e_cuda_smoke_20260914/manifest.json`、
  `core_metrics.csv`、对应 `core/*/gmticore.log`
