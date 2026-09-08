# 第一阶段 Current–Oracle 实验报告

## 1. 本阶段结论

第一阶段已完成：没有训练 AI，先用当前副本的 Stage2 四通道仿真、配对
C+N/S+C+N 数据和 CPU 离线回放，完成 Current–Oracle 性能差距与失配归因。

最重要的结果不是“Oracle 一定更好”，而是：在当前默认的
`legacy_min_magnitude` 非线性对消下，背景-only 行复最小二乘 Oracle All
不是无条件上界。它显著减少了部分残余高能点，但在本次 SCNR 定义下引入了
更大的整体噪声/幅度代价，因此两种条件下均出现负的有符号差距。这说明后续
AI 应学习物理模型的残差、置信度和门控，而不应直接把目标是“输出一幅
clutter-free RD 图”。

## 2. 实验条件与数据配对

两类场景都使用 16 GHz、50 MHz 带宽、60 MHz 采样率、1300 Hz PRF、130 个
脉冲、4 通道、新协议、单波位（beam 31）、单周期、目标配置 SNR 10 dB。
目标在距离 bin 2200，真值多普勒行 60，目标总多普勒为 -53.2469 Hz；两类
场景的动态 CSI 支撑都是行 42–88，因此目标在本次实验的 CSI 支撑内。

| 条件 | 杂波设置 | 通道非理想设置 | 有效样本 | 配对方式 |
|---|---|---|---:|---|
| Typical | 连续纹理，`texture_sigma=0.4`，`mean_power=0.1`，9 个方位子单元 | 无 | 130/130 | 生成器直接冻结 paired C+N，再注入目标 |
| Non-ideal | 非均匀连续纹理，`texture_sigma=1.1`，17 个方位子单元 | 通道相对幅度 +2.5 dB；固定相位 18°；相位抖动标准差 3°；通道丢失概率 0.35 | 90/130，40/130 丢失 | 因生成器禁止带非零 impairment 的 paired 输出，使用同 seed 分别生成 target/background |

非理想场景的两个独立输出的 `channel_impairment_truth.csv` 在 130 个脉冲上
逐字段一致，包含相同的相对增益、相位实现和 40 个丢失脉冲；因此目标差分
不会把通道误差实现差异误认为目标效应。非理想条件中的“有效样本不足”在
本阶段具体落实为通道 2 的脉冲级丢失，实际有效比例为 69.23%。配置分别见：

- [`ai_csi_stage2_typical_1beam.json`](../configs/research/ai_csi_stage2_typical_1beam.json)
- [`ai_csi_stage2_nonideal_target_1beam.json`](../configs/research/ai_csi_stage2_nonideal_target_1beam.json)
- [`ai_csi_stage2_nonideal_background_1beam.json`](../configs/research/ai_csi_stage2_nonideal_background_1beam.json)

## 3. Oracle 定义和防止目标泄漏

各变体的含义如下：

| 变体 | 替换内容 |
|---|---|
| Current | 当前 P38、整数 CTDR 延迟、动态支撑、`legacy_min_magnitude` |
| Oracle phase | 用 paired C+N 的逐 Doppler 行复相关相位替换 Current P38 相位 |
| Oracle amplitude | 用 paired C+N 行复最小二乘幅度，保留 Current 相位 |
| Oracle delay | 用物理等效延迟 1.8417 PRT 的分数延迟，替代 Current 的 2 PRT 整数延迟 |
| Oracle support | 用 C+N 测得的高相干/高功率支撑替代动态理论支撑 |
| Oracle weight | 用 C+N 计算 `alpha_m` 的复行最小二乘权重，再固定应用 |
| Oracle All | 分数延迟、C+N 相位/幅度/支撑和 C+N 复行权重同时启用 |

Oracle weight 的实际公式是

\[
\alpha_m^{CN}=\frac{\sum_{c=rg_{st}}^{rg_{ed}}
F_{1,CN}(m,c)\overline{F_{2,CN}(m,c)}}
{\sum_{c=rg_{st}}^{rg_{ed}}|F_{2,CN}(m,c)|^2}.
\]

它只读取背景 C+N 矩阵；计算结束后，`alpha_m^{CN}` 不再更新，分别应用到
C+N 和 S+C+N。目标 truth 只用于找到评价 ROI、计算目标损失和从 CFAR hits
中排除 5×5 目标 ROI；没有用于任何权重、相位、幅度或支撑估计。逐案例和
汇总约束记录在 [`oracle_analysis_manifest.json`](../outputs/ai_csi_oracle/oracle_analysis_manifest.json)。

## 4. 指标定义

- `CA_ROI_dB`：与当前源码 CSI metrics tap 对齐的 C+N ROI 功率比，
  `10 log10(P_before/P_after)`。
- `CSR_ROI_proxy_dB`：本次离线回放与 CA 使用同一 C+N ROI 功率代理，故数值相同；
  该列保留是为了与既有 CSI 指标表对齐，不能解释成独立的第二种测量。
- `input_SCNR_dB`：paired S+C+N 与 C+N 差分得到的目标功率，除以对消前
  C+N ROI 功率。
- `output_SCNR_dB`：对消后的 S+C+N 与 C+N 差分目标功率，除以对消后
  C+N ROI 功率。
- `SCNR_improvement_dB`：`output_SCNR_dB - input_SCNR_dB`。
- `target_loss_dB`：`10 log10(P_target,after/P_target,before)`；负值表示目标
  功率损失，越接近 0 越好。
- `residual_high_energy_points`：主动对消支撑内、C+N 输出功率高于该支撑
  中位功率 15 dB 的单元数。
- `false_alarm_count`：同一 CPU GO-CFAR 代理下，去掉目标 5×5 ROI 后的命中
  单元数；`Pd` 是该单目标 ROI 至少命中一个单元的检测代理，不是多场景统计
  意义上的概率估计。

GO-CFAR 使用源码相同的 guard=4、background=16、Pfa=1e-6 和环形 Doppler
窗口几何；离线系数通过固定随机流的 Monte Carlo 标定为 13.449716。由于本机
没有可通信的 NVIDIA 驱动，以下是 CPU source-operator replay，不宣称 CUDA
生产运行结果。

## 5. Current 与单因素 Oracle 结果

数值来自 [`single_factor_summary.csv`](../outputs/ai_csi_oracle/single_factor_summary.csv)，
均保留到 3 位小数；`high` 是残余高能点数，`FA` 是残余虚警单元数。

### 5.1 Typical：均匀纹理、无通道损伤

输入 SCNR 为 4.285 dB。

| 变体 | CA/CSR (dB) | 输出 SCNR (dB) | SCNR 提升 (dB) | target_loss_dB | high | FA | Pd |
|---|---:|---:|---:|---:|---:|---:|---:|
| Current | 28.619 | 25.917 | 21.632 | -6.987 | 4044 | 1013 | 1.0 |
| Oracle phase | 28.539 | 25.851 | 21.566 | -6.972 | 4445 | 1115 | 1.0 |
| Oracle amplitude | 23.739 | 21.293 | 17.008 | -6.731 | 935 | 136 | 1.0 |
| Oracle delay | 28.383 | 25.658 | 21.390 | -6.993 | 4288 | 922 | 1.0 |
| Oracle support | 28.619 | 25.917 | 21.632 | -6.987 | 4044 | 1013 | 1.0 |
| Oracle weight | 23.708 | 21.277 | 16.992 | -6.716 | 946 | 157 | 1.0 |
| Oracle All | 23.496 | 21.029 | 16.761 | -6.735 | 1119 | 119 | 1.0 |

### 5.2 Non-ideal：非均匀杂波 + 幅相误差 + 有效样本不足

输入 SCNR 为 1.572 dB。与 Typical 的 Current 相比，SCNR 提升下降约
1.967 dB，CA/CSR 下降约 1.929 dB，残余高能点从 4044 增至 9582，虚警
单元从 1013 增至 3949；单目标 `Pd` 代理仍为 1.0。

| 变体 | CA/CSR (dB) | 输出 SCNR (dB) | SCNR 提升 (dB) | target_loss_dB | high | FA | Pd |
|---|---:|---:|---:|---:|---:|---:|---:|
| Current | 26.690 | 21.237 | 19.666 | -7.024 | 9582 | 3949 | 1.0 |
| Oracle phase | 26.594 | 21.175 | 19.603 | -6.991 | 9542 | 3951 | 1.0 |
| Oracle amplitude | 21.477 | 16.288 | 14.716 | -6.761 | 6021 | 3991 | 1.0 |
| Oracle delay | 25.857 | 20.328 | 18.793 | -7.064 | 9739 | 3986 | 1.0 |
| Oracle support | 26.690 | 21.237 | 19.666 | -7.024 | 6928 | 3740 | 1.0 |
| Oracle weight | 21.443 | 16.287 | 14.715 | -6.728 | 6057 | 3989 | 1.0 |
| Oracle All | 21.127 | 15.868 | 14.333 | -6.794 | 3614 | 3924 | 1.0 |

## 6. Leave-one-error-out 失配归因

`LOO_x` 表示除去某一类 truth，其余 Oracle 条件保留。结果来自
[`leave_one_out_summary.csv`](../outputs/ai_csi_oracle/leave_one_out_summary.csv)。

| 条件 | LOO 变体 | 含义 | SCNR 提升 (dB) | CA/CSR (dB) | high | FA |
|---|---|---|---:|---:|---:|---:|
| Typical | LOO_phase | 其余为 Oracle，phase 留 Current | 16.765 | 23.534 | 1059 | 97 |
| Typical | LOO_amplitude | 其余为 Oracle，amplitude 留 Current | 21.329 | 28.288 | 4813 | 1079 |
| Typical | LOO_delay | 其余为 Oracle，delay 留 Current | 16.992 | 23.708 | 946 | 157 |
| Typical | LOO_support | 其余为 Oracle，support 留 Current | 16.761 | 23.496 | 1119 | 119 |
| Non-ideal | LOO_phase | 其余为 Oracle，phase 留 Current | 14.310 | 21.150 | 3618 | 3918 |
| Non-ideal | LOO_amplitude | 其余为 Oracle，amplitude 留 Current | 18.783 | 25.801 | 6977 | 3754 |
| Non-ideal | LOO_delay | 其余为 Oracle，delay 留 Current | 14.715 | 21.443 | 3564 | 3925 |
| Non-ideal | LOO_support | 其余为 Oracle，support 留 Current | 14.333 | 21.127 | 6172 | 3981 |

以 `Oracle All - LOO_x` 作为有符号的“该误差项在当前组合中的影响”时：

| 条件 | phase | amplitude | delay | support | Oracle All - Current |
|---|---:|---:|---:|---:|---:|
| Typical | -0.004 dB | -4.568 dB | -0.231 dB | 0.000 dB | -4.871 dB |
| Non-ideal | +0.022 dB | -4.450 dB | -0.383 dB | 0.000 dB | -5.333 dB |

这些贡献不能按“越大越坏”机械排序，因为当前 legacy 算子和 Oracle 复权算子
不是同一个线性族：Oracle amplitude/weight 改变了幅度统计，优化了部分残余
高能点，却牺牲了本 ROI 的整体 SCNR。当前数据更可靠地支持以下判断：

1. P38 phase 在这两个单周期案例中不是主要可回收瓶颈，单因素替换只改变约
   0.07 dB（Typical）和 0.06 dB（Non-ideal）的 SCNR 提升。
2. 整数 delay 在非理想条件下影响扩大，单因素 Oracle delay 相比 Current 的
   SCNR 提升差由约 0.24 dB（Typical）扩大到约 0.87 dB；它值得进入后续
   残差模型，但仍不是唯一主因。
3. 非均匀杂波和通道丢失明显抬高残余高能点与虚警；support Oracle 在本次
   目标支撑选择上对 SCNR 不变，却能改变 high/FA，说明“支撑边界”和“检测
   统计”应分开建模。
4. 复行幅度/权重 Oracle 虽把 Typical 的 high 从 4044 降到约 946、Non-ideal
   的 high 从 9582 降到约 6057（Oracle weight），但 SCNR 提升反而降低。
   后续不应直接把 LS weight 当成训练标签的唯一最优答案，必须同时约束噪声
   放大、目标保持和检测误警。

## 7. 交付物和复现入口

根目录小型交付物为：

- [`single_factor_summary.csv`](../outputs/ai_csi_oracle/single_factor_summary.csv)
- [`leave_one_out_summary.csv`](../outputs/ai_csi_oracle/leave_one_out_summary.csv)
- [`oracle_overall_summary.csv`](../outputs/ai_csi_oracle/oracle_overall_summary.csv)
- [`current_vs_oracle_scnr_improvement.png`](../outputs/ai_csi_oracle/current_vs_oracle_scnr_improvement.png)
- [`mismatch_contribution.png`](../outputs/ai_csi_oracle/mismatch_contribution.png)
- [`ca_target_loss_pd.png`](../outputs/ai_csi_oracle/ca_target_loss_pd.png)

脚本入口是 [`run_ai_csi_oracle_suite.py`](../scripts/run_ai_csi_oracle_suite.py)，
它调用 [`run_ai_csi_oracle.py`](../scripts/run_ai_csi_oracle.py) 逐案例回放并汇总。
原始 BIN、仿真日志和逐案例中间目录仍保留在本地工作区，但按体量策略不纳入
Git 历史。
