# GMTI 杂波对消 Baseline 方法与实现说明

## 1. 本文回答什么问题

本文说明第一阶段新增的无 AI 基准：在暂停 AI 训练的前提下，把当前
`legacy_min_magnitude` CSI 与几个可解释的传统方法放在同一 Stage2 数据链路中
比较，并记录它们在非均匀杂波、通道幅相误差、有效样本不足、低 SCNR、近杂波脊和
支撑边缘目标上的退化位置。

本轮只做离线 CPU source-operator replay，不宣称 CUDA 生产性能。AI 尚未训练，
也没有实现端到端 `clutter-free RD` 网络。

## 2. 比较边界：两组结果不能混读

为了避免把不同输入能力误认为算法收益，结果分成两组：

| 组别 | 方法 | 输入 | 结论用途 |
|---|---|---|---|
| F1/F2 strict | Current legacy CSI、Phase-only CSI（DPCA-class）、Row complex LS / Wiener | 同一前端产生的两路等效 CSI `F1/F2` | 严格比较当前 CSI 接口下的物理对消策略 |
| 4-channel academic reference | JDL-3x4 reduced STAP、MNEC、SA-MNEC | 原始四通道、慢时间 Doppler 数据 | 观察更强输入接口的传统参考，不与 F1/F2 strict 排名 |

两组都使用同一 Stage2 生成器、同一目标/背景配对规则、同一固定评价距离区间和
同一 CPU GO-CFAR 几何。Academic reference 不是当前生产管线的替换实现，而是
一个可运行、可审计的传统算法对照。

## 3. 数据和场景

统一基础配置是
`configs/research/ai_csi_stage2_typical_1beam.json`：16 GHz、50 MHz 带宽、
60 MHz 采样率、1300 Hz PRF、130 个脉冲、单波位 beam 31、距离 bin 2200，
脉压后保留 4096 个距离单元。完整覆盖矩阵写在
[`ai_csi_baseline_suite.json`](../configs/research/ai_csi_baseline_suite.json)。

本轮每个场景使用一个固定 seed；实际生成后的目标频率、杂波中心、动态支撑和
输入身份见 [`baseline_scenario_summary.csv`](../outputs/ai_csi_baseline/baseline_scenario_summary.csv)。

| 场景 | 主要变量 | 本轮实际检查点 |
|---|---|---|
| Ideal / uniform clutter | `texture_sigma=0.4`，9 个方位子单元，无通道损伤 | 目标相对杂波中心约 -53.434 Hz，支撑边缘有符号距离 18 行，130/130 有效 |
| Nonuniform clutter | `texture_sigma=1.1`，17 个方位子单元 | 目标相对杂波中心约 -54.990 Hz，130/130 有效 |
| Channel amplitude / phase error | +2.5 dB 相对幅度、18° 固定相位、3° 相位抖动 | 0 个丢失，130/130 个 impairment truth；target/background 除 `case_id` 外逐字段一致 |
| Effective samples reduced | 通道丢失概率 0.55 | 实际丢失 85/130，只有 45/130 有效 |
| Low SCNR | 目标配置 SNR -2 dB | 目标仍在支撑内，130/130 有效 |
| Low radial velocity / clutter ridge | `ve=0, vn=0` | 目标相对杂波中心约 +1.050 Hz，130/130 有效 |
| Target at clutter-support edge | `ve=-2, vn=2` | 目标相对杂波中心约 -213.531 Hz，支撑边缘有符号距离 2 行，130/130 有效 |

非理想场景使用同 seed 分别生成 target/background。原因是 Stage2 生成器把带目标
包的 paired C+N 冻结写出在 channel impairment 之前；要让背景也包含同一组非零通道
误差，脚本使用独立 background 配置。每个非理想场景的 impairment truth 已做逐行
字段核对，具体证据和输入 SHA-256 记录在 baseline manifest。

## 4. 前端和输入身份

### 4.1 F1/F2 strict

脚本复用 `scripts/run_ai_csi_oracle.py` 的数据解释和物理前端：

```text
NewProtocol 四通道 IQ
  -> 四通道融合 / 距离向脉压
  -> 两路等效 F1/F2
  -> CTDR 对齐、DBS、距离向相位校正
  -> 当前动态杂波支撑
  -> CSI 变体
  -> 固定几何 GO-CFAR
```

Current 和 Phase-only 保留当前实现的 target-observation P38 校准路径，这是为了
忠实复现 Current 现行行为。它不是 Oracle weight。Row-LS 使用独立的
`baseline_background_only` 对齐对象：距离向校正、逐 Doppler 行复权都从 C+N
背景计算，之后冻结并应用到含目标输入。

### 4.2 Academic reference

Academic 路径从同一个四通道 BIN 做距离脉压和慢时间 FFT，不经过 F1/F2 合成。
它只使用固定的全距离训练区 `rg_st..rg_ed`；目标 truth 不进入协方差、 steering
或权重训练。

## 5. 六个基准方法的确切实现

### 5.1 Current legacy CSI

在当前动态支撑内，复用现有 `legacy_min_magnitude`：将两路相位对齐，再把两路
幅度裁到逐单元较小者后相减；支撑外保留原通道用于检测。该方法代表当前生产
算子，不添加新参数。

### 5.2 Phase-only CSI / DPCA-class

在同一 F1/F2 输入和同一支撑内，只使用当前 P38 相位：

\[
Y_m(c)=F_1(m,c)-e^{j\phi_{P38}(m)}F_2(m,c).
\]

它不做幅度均衡，目的是把“相位建模”和“幅度/复权策略”拆开观察。这里的
`DPCA-class` 是接口类比标签，不声称完成某篇论文的完整 DPCA 工程复现。

### 5.3 Row complex LS / Wiener

对每个 Doppler 行使用 C+N 背景估计复权：

\[
\alpha_m^{CN}=\frac{\sum_{c\in\mathcal R}F_{1,CN}(m,c)F_{2,CN}^*(m,c)}
{\sum_{c\in\mathcal R}|F_{2,CN}(m,c)|^2},
\qquad
Y_m=F_{1,m}-\alpha_m^{CN}F_{2,m}.
\]

这里的 `\mathcal R` 是固定配置距离区间。`alpha` 的计算和用于输入的距离向
相位校正均只读取 C+N；含目标数据只在计算完成后被动应用。它是背景训练型
线性参考，不是无条件理论上界。

### 5.4 JDL-3x4 reduced STAP

对每个输出 Doppler 行，拼接相邻 `-1,0,+1` 三行的四通道特征，得到 12 维
向量。固定距离训练区内形成 C+N 协方差，加入对角加载，再用中心 Doppler 块
单位增益约束求最小范数投影权重。实现中保留每一行权重，并对 target/background
使用同一权重。

这是一个低成本 reduced-dimension JDL-like baseline，用于回答“扩大到
space-time 输入后当前差距在哪里”；不是完整工程化 JDL 的全部训练样本选择、
多 steering 约束或秩/加载调参结果。

### 5.5 MNEC

每个 Doppler 行以四通道 C+N 距离快拍形成协方差，按特征值自适应选取主导子空间，
构造

\[
P_\perp=I-U_cU_c^H,
\qquad
w=\frac{P_\perp s}{s^HP_\perp s}.
\]

本轮 `s` 为归一化四通道单位 steering，权重满足单位增益形式。它是可解释的
最小范数 eigencanceler 参考，但在目标 steering 与杂波子空间接近时，目标保持和
残余抑制会出现直接冲突。

### 5.6 SA-MNEC

SA-MNEC 将 C+N 脉冲分为 4 个子孔径，在每个子孔径内用 FFT 相位重构 Doppler
快拍，进行逐通道尺度归一化、协方差 trace 归一化和跨子孔径平均，最后沿用 MNEC
投影。该实现是公开论文摘要所述“子孔径平均协方差 + MNEC”思想的简化离线
参考，不等同论文的完整实验复现。参考文献为
[IEEE Xplore / DOI 10.1109/LGRS.2026.3664295](https://doi.org/10.1109/LGRS.2026.3664295)。

## 6. Oracle 与目标泄漏约束

已有 Current–Oracle 脚本中的 Oracle weight 采用：

```text
paired clutter+noise truth -> 估计复权/协方差/支撑 -> 冻结 -> 作用于 S+C+N
```

禁止使用目标 truth 优化 `alpha`、相位、幅度或支撑。本轮 baseline 中，Row-LS、
JDL、MNEC 和 SA-MNEC 的背景训练量均从 C+N 获取；目标 truth 只用于目标 ROI、
目标损失、Pd 和 MDV proxy 评价。Current/Phase-only 的 target-observation P38
属于现行算法观测路径，不应被称为 Oracle。

## 7. 统一指标定义

所有指标都在同一个固定评价区域上计算：全部 Doppler 行、配置的 `rg_st..rg_ed`
距离列。这样缩小主动支撑不会自动减少 residual 指标。

| 指标 | 定义 |
|---|---|
| `CA_dB` / `CSR_dB` | 固定区域 C+N 输入平均功率 / 输出平均残余功率的 `10log10` |
| `input_SCNR_dB` | target-background 差分目标功率 / 对消前 C+N ROI 功率 |
| `output_SCNR_dB` | 对消后 target-background 差分目标功率 / 对消后 C+N ROI 功率 |
| `SCNR_improvement_dB` | `output_SCNR_dB - input_SCNR_dB` |
| `target_loss_dB` | `10log10(P_target,after / P_target,before)`；负值表示目标功率下降 |
| `background_residual_p95/p99_*` | 固定区域残余功率分位数及相对 C+N 输入中位数的 dB |
| `high_energy_residual_points` | 固定区域中超过 C+N 输入中位数 +15 dB 的单元数；另给 fraction |
| `false_alarm_count` / `background_false_alarm_count` | 纯 C+N background 在同一 GO-CFAR 几何下的命中数 |
| `background_Pfa` | `false_alarm_count / cfar_test_cells`，本轮分母是 `130*(4096-40)=527280` |
| `Pd` | 目标 5×5 ROI 至少命中一个 CFAR 单元的单目标检测代理，不是统计概率估计 |
| `MDV_mps` | 在杂波中心附近固定 Doppler 行偏移集合上的最低检测速度代理，不是完整 MDV 曲线 |
| `runtime_ms` | 单 case CPU 方法运行时间，含背景权重训练，不含仿真生成和 MDV sweep |

`false_alarm_count` 来自纯 C+N，不从含目标图中扣除虚警；目标 truth 不用于背景
虚警统计。完整列名和每个 case×method 的值在
[`baseline_summary.csv`](../outputs/ai_csi_baseline/baseline_summary.csv)。

## 8. 复现和产物策略

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target simulate_stage2_statistical -j4
python3 -m py_compile scripts/run_baseline_benchmark.py
python3 scripts/run_baseline_benchmark.py
```

默认只保留：

- `outputs/ai_csi_baseline/baseline_summary.csv`
- `outputs/ai_csi_baseline/baseline_scenario_summary.csv`
- `outputs/ai_csi_baseline/baseline_benchmark_manifest.json`
- 四张汇总图：SCNR、固定区 p99、目标损失、检测/Pfa/MDV/runtime

每个 case 的原始 BIN 和临时矩阵在处理完成后删除；需要故障现场时可显式使用
`python3 scripts/run_baseline_benchmark.py --keep-data`，但这些大文件仍不会作为
常规 Git 交付物。manifest 保存了工作目录、命令、源码 commit/dirty 状态、Release
仿真器 SHA-256、Python/NumPy/Matplotlib、GPU 探测和每个关键 BIN 的路径、大小、
SHA-256。

## 9. 实现边界

本轮 academic reference 的目的，是提供“传统 space-time 方法是否能直接解决
当前差距”的可运行证据；它没有完成完整论文级 steering/训练样本/秩选择/硬件
标定优化。因此其退化结果只能支持“当前简化接口和实现的失败模式”，不能写成
MNEC、JDL 或 SA-MNEC 在文献意义上的普遍性能结论。

同理，一个 seed、一个周期的七个场景可以定位方向，不能给出跨 seed 统计置信区间。
第一阶段的下一步应先补齐传统基线的物理校准与多 seed 统计，再决定是否进入
Physics-AI。
