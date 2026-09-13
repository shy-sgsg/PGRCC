# 真实系统误差参数与可观测性分析

> 阶段：`Unknown System Error Characterization / 真实系统未知误差建模与可观测性分析`
>
> 审计日期：2026-09-13；源码基线：`8be2c89`；本文件是代码审计与 pilot 设计，
> 不是已经完成的 CUDA 正式实验报告。

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

其中 `baseline_error_m` 的当前实现是：

```text
baseline_phase_deg
  = 360 × baseline_error_m × sin(theta_cmd) / wavelength
relative_phase_deg
  = fixed_phase + jitter + per_pulse_drift × pulse_id
    + per_beam_bias + baseline_phase
```

这是一阶、角度相关的基线误差相位模型。当前 `applyChannelImpairments` 使用
`new_protocol_read_channel_1/2`（默认 1/2）作为被变换的读入通道，因此该入口目前
不是完整的四通道独立阵列几何误差注入；这一限制已写入 pilot 边界。

### 2.2 平台、姿态和伺服角的当前假设

- `simulator/target_injection/radar_geometry.cpp:18-23` 的
  `evaluatePlatformState` 用单一 `platform_speed_mps` 生成直线位置和速度；
- 同文件 `:48-49` 直接令 `theta_true_deg = theta_cmd_deg`，没有独立的真实波束角与
  上报/命令伺服角；
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
- `scripts/estimate_channel_delay.py` 和 `scripts/estimate_temporal_phase.py` 主要
  使用通道 1/2 的互谱，尚无统一的 `C13/C24/C12/C14/C23/C34` 六对观测接口；
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
| 平台速度 vs P38/目标径向速度 | 需要 header/position-delta/几何先验和跨波位/跨脉冲结构 | 当前生产可记录来源，但模拟器未分离真值与上报值 |
| 伺服角 vs yaw/基线投影 | 需要 servo/header 与多通道空间相位的联合观测 | 当前没有独立真实伺服角注入 |
| 四通道几何误差 | 需要六 pair 和已知阵列 offset；单独 1/2 pair 只能给局部证据 | 当前脚本未统一实现 |

## 6. 首个 pilot：基线几何误差的多波位观测

### 6.1 选择理由

首个 pilot 暂选 `baseline_error_m`，理由是它已经有配置、真值记录和明确的一阶物理
相位关系，并且能直接检验“多波位回波能否把角度相关几何误差从固定相位中分离”。
相比之下：

- 平台速度误差虽然重要，但当前模拟器用同一个速度驱动真实运动、杂波和输出 header，
  需要先增加独立的 true/report state 才能做忠实 pilot；
- 伺服/波束指向误差当前 `theta_true_deg = theta_cmd_deg`，也需要先增加独立真实角；
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
2. 对每个波位和频率支持计算六对互谱 `C13,C24,C12,C14,C23,C34`，同时输出幅度、
   相位、相干系数、有效样本数和质量标志。
3. 对每个 pair 先估计频率不变的相位统计，再按 `sin(theta_b)` 做稳健斜率拟合；将
   固定相位、角度零点、通道 gain 和低 coherence 作为 nuisance/质量控制项。
4. 用 pair 闭合相位和四通道 offset 约束检查估计是否符合单一几何误差模型；若只在
   1/2 pair 上成立，必须标成“局部 pair 证据”，不能宣称完整四通道自校准。
5. 只把估计出的参数和置信度写入校准接口，再运行 Current CSI 和四通道 STAP；保留
   逐字段来源、估计残差和失败/fallback 状态。

### 6.5 评价指标

至少报告：

- 六 pair 的幅度、相位残差、频率/角度斜率和 coherence；
- CSI 杂波抑制、四通道 STAP 杂波抑制及两者的输入信息条件；
- target causal transfer、目标幅度/相位保持和目标检测迁移；
- production GO-CFAR 的 Pd、Pfa、虚警簇和漏检；
- `Known − Current`、`Estimated − Current` 和 recovery ratio；
- 估计失败率、质量门限、fallback 次数及 OFF/ON 数据隔离。

### 6.6 当前 pilot 状态和阻塞项

截至本次审计，pilot 仍是设计态，没有把“文件存在”或“配置可解析”写成实验通过。
可复用的基础包括 `channel_impairments.cpp` 的注入入口、Stage2 raw IQ、四通道生产
配置和已有 1/2 通道时延/慢时间估计脚本；缺口是：

1. 多波位四通道观测输出和六 pair 统一接口；
2. 把当前只作用于所选读入通道 1/2 的基线扰动扩展或明确建模为可审计的四通道几何
   误差；
3. 基线误差确定性校正函数及 estimated-only 估计器；
4. 同一场景接入 Current CSI、四通道 STAP 和生产 CFAR 的四条件评价。

因此本轮不启动大规模 CUDA，也不创建一个只看单波位、只看通道 1/2 的伪 pilot。上述
缺口已经成为下一步最短实现路径。

## 7. 后续阶段顺序

1. 完成六 pair compact observable 的只读分析和质量字段；
2. 补齐基线误差的四通道/多波位忠实注入与已知误差校正；
3. 做小规模四条件 CPU/最小 CUDA 回放，先验证相位模型和闭合相位；
4. 接入 Current CSI 与四通道 STAP 的端到端及信息量匹配比较；
5. 扩展到平台速度/姿态/伺服真值—上报误差，最后再判断确定性估计是否不足；
6. 只有第 5 步之后仍存在稳定、可量化且难以解析的残差，才评估 Physics-AI。

## 8. 证据入口

- 机器可读参数清单：[`outputs/system_error_inventory/parameter_inventory.csv`](../outputs/system_error_inventory/parameter_inventory.csv)
- 传播关系：[`outputs/system_error_inventory/propagation_map.csv`](../outputs/system_error_inventory/propagation_map.csv)
- 生成说明：[`outputs/system_error_inventory/manifest.json`](../outputs/system_error_inventory/manifest.json)
- 生产四通道融合：`include/config_structs.hpp`、`src/dbs/NewProtocolReader.cpp`
- 模拟器误差入口：`simulator/target_injection/channel_impairments.{h,cpp}`
- 运动与定位模型：`include/motion_comp.hpp`、`include/ctdr_phase_model.hpp`、
  `src/processOnePeriod.cpp`
- 历史时延/相位估计：`scripts/estimate_channel_delay.py`、
  `scripts/estimate_temporal_phase.py`
