# 物理模型驱动的智能 GMTI 杂波对消：第一阶段进展

## 当前状态

第一阶段已完成，当前没有训练 AI，也没有实现端到端 clutter-free RD 网络。
已完成 Current–Oracle 的两类场景回放、单因素替换、leave-one-error-out 归因、
新增指标和后续方法建议。

## 已完成事项

| 阶段 | 状态 | 证据 |
|---|---|---|
| 初始化副本与版本管理 | 完成 | 源工程复制到当前目录；Git 元数据使用只读 `.git` 挂载旁的显式 `.git-real`，已提交基线 `4747bd8` |
| 当前 CSI 数学模型 | 完成 | [`AI_CSI_01_当前对消数学模型.md`](AI_CSI_01_当前对消数学模型.md) |
| 典型场景 | 完成 | 均匀连续纹理、无通道损伤、单波位、单目标、130 PRT |
| 非理想场景 | 完成 | 非均匀纹理 + 2.5 dB 幅度误差 + 18° 固定相位误差 + 3° 相位抖动 + 40/130 通道丢失 |
| Current/Oracle 单因素与 LOO | 完成 | 三个 CSV 汇总 |
| 指标补充 | 完成 | `target_loss_dB`、`residual_high_energy_points`、`false_alarm_count`、`Pd` |
| 失配归因与 AI 建议 | 完成 | [`AI_CSI_02_Current_Oracle实验报告.md`](AI_CSI_02_Current_Oracle实验报告.md)、[`AI_CSI_03_后续AI方法建议.md`](AI_CSI_03_后续AI方法建议.md) |

## 当前实验事实

- 两类场景目标均为 `row_truth=60`、`expected_bin=2200`、`af_total_truth=-53.2469 Hz`；
  动态 CSI 支撑为行 42–88。
- Typical Current 的 SCNR 提升为 21.632 dB，Oracle All 为 16.761 dB。
- Non-ideal Current 的 SCNR 提升为 19.666 dB，Oracle All 为 14.333 dB。
- Oracle All 相对 Current 的有符号差距分别为 -4.871 dB 和 -5.333 dB；按非负
  定义的可回收正差距均为 0 dB。
- 负差距不是把结果修剪成“Oracle 更好”，而是记录了当前非线性最小幅度算子
  与背景-only 线性复权之间的真实性能/统计权衡。

## 可复现入口

源码构建已完成：

```text
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target simulate_stage2_statistical -j2
```

仿真配置：

- `configs/research/ai_csi_stage2_typical_1beam.json`
- `configs/research/ai_csi_stage2_nonideal_target_1beam.json`
- `configs/research/ai_csi_stage2_nonideal_background_1beam.json`

Oracle 汇总入口：

```text
python3 scripts/run_ai_csi_oracle_suite.py
```

根目录交付物：

- `outputs/ai_csi_oracle/single_factor_summary.csv`
- `outputs/ai_csi_oracle/leave_one_out_summary.csv`
- `outputs/ai_csi_oracle/oracle_overall_summary.csv`
- `outputs/ai_csi_oracle/current_vs_oracle_scnr_improvement.png`
- `outputs/ai_csi_oracle/mismatch_contribution.png`
- `outputs/ai_csi_oracle/ca_target_loss_pd.png`

原始 BIN、仿真报告和逐案例中间回放目录只在本地工作区保留；Git 只跟踪上述
紧凑交付物，避免把大体量原始数据复制进版本历史。

## 证据边界

本机 `nvcc`、CMake、FFTW3 和 Python 分析环境可用，Stage2 仿真目标已成功构建；
但 `nvidia-smi` 无法与 NVIDIA 驱动通信。因此当前 CSV/PNG 是对源码算子顺序的
CPU 离线回放，不宣称已完成 CUDA 生产执行，也不把一个 seed/单周期结果外推为
统计泛化结论。

## 下一步（需另行进入第二阶段）

补充跨 seed/场景统计后，再评估小型“物理模型 + 残差学习”方案。第一候选是
学习分数 delay、复权幅度残差和行级置信度/门控；物理融合、P38、支撑、CSI
算子、CFAR、聚类和跟踪链保持不变。未经新的研究授权，不进入训练或端到端
RD 网络实现。
