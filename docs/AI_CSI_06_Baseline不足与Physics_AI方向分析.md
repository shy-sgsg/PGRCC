# Baseline 不足与 Physics-AI 方向分析

## 1. 结论先行

本轮 baseline 已经足以决定下一步的接口形态，但还不足以决定最终算法：

- 不应暂停在“挑一个平均 SCNR 最高的方法”上。F1/F2 strict 中 Current 平均
  SCNR 提升最高，但 Row-LS 的 FA/Pfa 和部分高能尾部更低，存在明显多目标冲突。
- 不应把本轮简化 JDL/MNEC/SA-MNEC 的结果解释成文献算法的上限或结论。它们使用
  原始四通道 space-time 输入，且本轮只实现了可审计的低成本参考；其较高 p99 和
  运行时间首先暴露了 steering、训练区、秩/加载和目标保持问题。
- 下一阶段若获准进入 AI，最佳目标不是生成一幅端到端 `clutter-free RD` 图，而是
  在现有物理 CSI 后面学习 **物理参数残差 + 行级置信度/门控**。建议优先预测
  分数 CTDR 延迟/等效 P38 斜率残差、复权幅度残差和 `g_m`，让物理算子继续完成
  对消、支撑、CFAR 和后续检测。

## 2. 本轮基线暴露的主要不足

### 2.1 样本量不足：只有方向证据，没有统计泛化证据

当前矩阵是 7 个场景、每场景 1 个 seed、1 个周期、130 PRT。它足以展示极端条件
下的退化，但无法给出均值置信区间、跨纹理泛化或跨设备稳定性。特别是 `Pd` 是
一个目标 ROI 的布尔检测代理；本轮几乎全部为 1，不能把它写成已估计的检测概率。
`MDV_mps` 也只是局部行偏移 proxy，大多数方法得到 0 m/s，不足以支持正式 MDV
结论。

**处理方向：** 在进入 AI 前补充按 seed 和 scenario 分层的重复回放，并把速度
   sweep、目标 SNR sweep、有效样本率 sweep 和杂波纹理 sweep 分开记录，避免把
   单个联合场景的差异误写成单因素因果。

### 2.2 strict 组存在尾部、SCNR、目标保持的多目标冲突

7 场景等权均值为：

| 方法 | SCNR 提升 | target_loss_dB | p99/input median | high 点 | FA/Pfa |
|---|---:|---:|---:|---:|---:|
| Current | 12.318 dB | -9.184 dB | 13.980 dB | 3,760 | 1,616 / 0.003065 |
| Phase-only | 7.403 dB | -7.867 dB | 18.060 dB | 14,994 | 1,043 / 0.001979 |
| Row-LS/Wiener | 9.246 dB | -8.529 dB | 15.955 dB | 9,390 | 701 / 0.001330 |

Row-LS/Wiener 的 FA/Pfa 最低，但 SCNR 提升低于 Current；Phase-only 也能降低
部分 FA，却明显增加 fixed-region high 点。这个结果说明：只优化 C+N 平均残余
或只优化高能点，都会可能以噪声统计、目标功率或其他 Doppler 行为代价换取局部
改善。后续目标函数必须是多目标且保留 Current 旁路。

### 2.3 非均匀杂波和有效样本不足是更直接的退化入口

在本轮具体场景中，Current 的理想/非均匀结果从 20.534/15.180 dB 变化，非均匀
场景的 fixed-region high 点为 10,427，Pfa 为 0.005864；这些数值是联合 seed
结果，不是单独纹理因果估计。有效样本场景实际只有 45/130 个有效脉冲，Current
SCNR 提升降到 8.360 dB，Phase-only 降到 -0.629 dB，Row-LS 降到 5.456 dB。

**处理方向：** AI 第一版应把有效样本 mask/比例、行相干性、局部纹理非均匀度和
   P38 拟合残差作为显式输入；不能把缺样本后的零值或异常协方差当成普通训练样本。

### 2.4 近杂波脊与支撑边缘主要伤害目标保持

近杂波脊 case 的目标相对杂波中心只有约 +1.050 Hz，Current 的 SCNR 提升为
1.311 dB、`target_loss_dB=-30.199`；支撑边缘 case 距支撑边界仅 2 行，Current
的 `target_loss_dB=+4.090`、SCNR 提升 4.368 dB。两种结果的 Pd 都为 1，但这并
不能抵消目标功率/残余统计已经变化的事实。

**处理方向：** 训练和验收需要同时看 `target_loss_dB`、目标 ROI 输入/输出功率、
   SCNR、Pfa 和 fixed-region high，使用目标 truth 只做独立验证 guardrail，不把
   它放入权重求解或网络输入。

### 2.5 Academic reference 仍是“实现诊断”，不是公平排名

JDL-3x4、MNEC 和 SA-MNEC 使用四通道 space-time 输入，不应和 F1/F2 strict 直接
比较。当前简化实现的均值为：

| 方法 | SCNR 提升 | target_loss_dB | p99/input median | runtime |
|---|---:|---:|---:|---:|
| JDL-3x4 reduced STAP | 2.276 dB | -1.815 dB | 35.775 dB | 199.0 ms |
| MNEC | 0.546 dB | +2.394 dB | 36.921 dB | 41.8 ms |
| SA-MNEC | 0.563 dB | +2.514 dB | 37.029 dB | 260.3 ms |

这里的退化应优先归因于当前参考实现的边界：物理 steering 尚未做完整工程化
验证，协方差训练区固定，秩/对角加载是简化规则，SA 只采用四段和 trace/通道
归一化。不能据此声称 MNEC 或 SA-MNEC 在论文意义上无效。应先完成传统 baseline
的 steering、训练区、样本支持和目标保持审计，再决定它是否值得进入后续 AI 输入。

## 3. 当前 Oracle/权重约束如何延续

继续遵守以下硬边界：

1. 任何称为 Oracle、LS/Wiener、协方差或自适应权重的量，只从 paired 或同 seed
   的 clutter+noise truth / background 估计，估计后冻结。
2. 目标 truth 只用于目标 ROI、`target_loss_dB`、Pd、速度/支撑位置审计；不得用
   来优化 `alpha`、相位、幅度、steering、支撑边界或网络权重。
3. Current 的 target-observation P38 是现行生产行为的复现路径，不能伪装成
   C+N-only Oracle；报告中须把“观测型 Current”与“背景训练型 reference”分列。
4. fixed-region residual 不能随主动支撑缩小而自动变好；FA/Pfa 必须在纯 C+N
   background 上用同一 CFAR 几何计算。
5. 不把 Oracle All 当作跨算子族的理论上界；它只是当前算子族下的背景复权对照。

## 4. Physics-AI 的推荐目标：物理参数残差和可信度

### 4.1 保留的物理链路

保留以下可追溯步骤：

```text
四通道融合 -> 距离脉压 -> CTDR/DBS -> P38/距离向校正
-> 动态支撑 -> CSI 对消 -> CFAR/检测
```

AI 不直接输出二维 RD 图，只对现有物理初值做小幅修正：

\[
\theta_{phys}=
\{k_{P38},b_{P38},\tau_{int},\alpha_m^{phys},\mathcal S_{dyn}\},
\]

\[
\Delta\theta=
\{\Delta k,\Delta b,\Delta\tau,\Delta\log|\alpha_m|,
\Delta\angle\alpha_m,g_m\},
\qquad
Y=CSI(F_1,F_2;\theta_{phys}+\Delta\theta,g).
\]

推荐先实现一个小型 residual head：可以是按 Doppler 行的浅层 MLP、1-D 卷积或
轻量时序模型；`g_m` 是 [0,1] 行级可信度。低有效样本、低相干或大拟合残差时，
`g_m` 应把输出拉回 Current 物理路径或直接旁路，而不是强行使用大幅度复权。

### 4.2 第一批输入特征

全部特征都应来自 C+N 可获得的物理处理量或当前输出诊断：

- P38 拟合斜率/截距、RMSE、内点率、接受样本数和理论斜率差；
- 每个 Doppler 行的互相关幅度、相干性、F1/F2 能量比和 `alpha` 散布；
- 有效脉冲比例、丢失 mask 汇总、局部纹理非均匀度和协方差条件数；
- 当前 CSI 输出的固定区 p95/p99、high 点风险、噪声地板和支撑边界距离；
- 物理当前权重与背景 LS reference 的差异，仅作为 C+N 侧诊断，不使用目标 truth。

### 4.3 输出和损失的优先级

第一版不要同时学习全部参数。建议顺序为：

1. `g_m` 行级置信度/门控；
2. `Δτ` 或等效 P38 斜率/截距残差；
3. `Δlog|alpha_m|`，再考虑相位残差；
4. 最后才考虑支撑边界的小范围残差，而不是让网络自由改变整张支撑。

背景训练目标可写成多目标形式：

\[
L=\lambda_1L_{CN,residual}+\lambda_2L_{tail}
 +\lambda_3L_{noise\ floor}+\lambda_4L_{smooth}
 +\lambda_5L_{residual\ size}.
\]

其中 `L_CN,residual` 约束 C+N 固定区残余，`L_tail` 约束 p95/p99 和 +15 dB high
点，`L_noise floor` 防止用放大噪声换局部低点，`L_smooth` 约束相邻行参数变化，
`L_residual_size` 限制无必要的大修正。目标 truth 只在独立验证阶段提供目标保持
和检测 guardrail，不作为 Oracle weight 或主训练标签。

## 5. 进入 AI 前必须补齐的物理工作

### 阶段 A：传统基线统计化

- 至少扩展多个 seed，并按场景分层报告均值、分位数和失败率；
- 固定并审计 F1/F2 的 Current、phase-only、background-LS 三条路径；
- 为 JDL/MNEC/SA 补物理 steering、训练区、协方差收缩/加载、秩选择和通道校准
  的小范围 A/B；
- 将 `MDV` 从局部 proxy 扩展为固定背景、固定目标幅度下的完整速度 sweep；
- 保留每次输入路径、大小、SHA-256、配置、源码 dirty 状态和设备状态。

### 阶段 B：无 AI 的残差接口

先写一个确定性的参数 residual/门控接口，用人工可解释规则模拟网络输出，验证
`Current -> residual -> CSI -> CFAR` 的输出 schema、边界和旁路逻辑。只有这个
接口在低样本、近杂波脊、支撑边缘和非均匀杂波下不破坏现有链路，才进入小模型训练。

### 阶段 C：小模型和按场景划分

按 seed/scenario 划分训练、验证和测试，不能把同一场景不同脉冲随机拆开冒充独立
样本。测试集必须包含未见纹理、未见幅相误差组合和未见有效样本比例。每次模型
对照都必须与 Current 在同一 CSV schema 下报告：CA/CSR、输入/输出 SCNR、
`target_loss_dB`、p95/p99、high、FA/Pfa、Pd、MDV、runtime。

## 6. 明确不进入的路线

当前不建议直接做 `raw IQ -> clutter-free RD` 或 `RD -> cleaned RD` 的端到端网络：

- 它会把 CTDR、P38、四通道融合、支撑、CFAR 和检测语义隐入黑盒，无法沿用本轮
  的 Current–Oracle 失配归因；
- 单一图像损失可能通过削弱目标或放大噪声换取更漂亮的 RD；本轮已经实测到
  high/FA 下降与整体 SCNR 下降可以同时出现；
- 有效样本不足和通道误差容易成为数据集捷径，跨设备/跨杂波泛化难以审计；
- 目标 truth 禁止参与 Oracle 权重求解的约束，在端到端损失中更难保持清晰边界。

因此后续 AI 入口应严格限制为 **物理参数残差、可信度、门控**；物理 CSI 主链和
CFAR/检测链继续可追溯、可旁路、可按单因素复现。

## 7. 仍待回答的问题

- Row-LS 的 FA/Pfa 优势在多 seed 下是否稳定，还是由少数纹理 realization 主导？
- 哪些 C+N-only 诊断最能预测近杂波脊的 `target_loss_dB`，是 P38 残差、相干性、
  有效样本率还是协方差条件数？
- 对 academic reference，物理 steering 误差、训练区污染和秩选择哪个是首要
  失配源？
- `g_m` 的旁路阈值如何在不查看目标 truth 的情况下由 C+N 风险标定？
- 在固定 false-alarm guardrail 下，能否找到目标保持和残余尾部同时改善的 Pareto
  解，而不把 LS 权重当作唯一标签？
