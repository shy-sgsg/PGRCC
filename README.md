# PGRCC

物理模型驱动的智能 GMTI 杂波对消研究仓库。

本仓库是在原 GMTI 工程副本上开展的第一阶段研究，重点是先厘清当前 CSI
对消链路，并用 Current–Oracle 实验定位性能差距与失配来源。第一阶段不训练
AI，也不实现端到端 `clutter-free RD` 网络。

## 第一阶段已完成

- 还原当前 CSI 数学模型：四路协议 IQ 先融合为两路等效通道，再进行 CTDR
  对齐、P38 相位建模、距离向相位校正、动态杂波支撑和 CSI 对消。
- 完成典型场景：连续纹理杂波、单波位、单目标、无通道损伤。
- 完成非理想场景：非均匀杂波、通道幅相误差和有效样本不足（130 个脉冲中
  40 个通道丢失，实际有效 90/130）。
- 完成 Current、单因素 Oracle 和 leave-one-error-out 归因。
- Oracle weight 只由 clutter+noise truth 计算，再固定应用于含目标数据；目标
  truth 不参与权重求解。
- 指标包含 CA/CSR、输入/输出 SCNR、SCNR 提升、`target_loss_dB`、残余高能点、
  `false_alarm_count` 和 `Pd`。

当前实验结果显示：

| 条件 | Current SCNR 提升 | Oracle All SCNR 提升 | 有符号差距 |
|---|---:|---:|---:|
| Typical | 21.632 dB | 16.761 dB | -4.871 dB |
| Non-ideal | 19.666 dB | 14.333 dB | -5.333 dB |

这里的 Oracle All 是背景-only 线性复权对照，不是无条件性能上界。结果表明后续
更适合采用“物理模型保留 + 残差学习 + 置信度门控”，重点学习分数延迟、复权
残差和支撑可信度，而不是直接输出一幅清洗后的 RD 图。

## 目录

```text
include/                    头文件与物理模型
src/                        GMTI/CUDA 主实现
configs/                    工程配置
configs/research/           第一阶段实验配置
simulator/                  Stage2 仿真器
scripts/                    分析、评估和复现实验脚本
tests/                      工程测试
docs/                       数学模型、实验报告和后续 AI 建议
outputs/ai_csi_oracle/      小型 CSV/PNG/JSON 研究交付物
```

原始 BIN、构建目录、缓存和逐案例中间结果不纳入 Git；它们需要在本地生成或
保留用于复核。

## 环境要求

构建主工程需要 CMake、C++ 编译器、CUDA Toolkit、FFTW3 和 libtiff。第一阶段
离线分析脚本需要 Python 3、NumPy 和 Matplotlib。

本阶段已验证的工具链包括 CUDA Toolkit 12.4.131、CMake 4.2.3 和 FFTW3 3.3.10。
CUDA 生产运行还需要可用的 NVIDIA 驱动；当前环境没有可通信的 NVIDIA 驱动，
所以本阶段结果是 CPU 源码算子离线回放，不是 CUDA 生产运行结果。

## 构建

在仓库根目录执行：

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target simulate_stage2_statistical -j2
```

如需构建完整工程，可使用：

```bash
cmake --build build -j2
```

## 复现第一阶段 Current–Oracle 汇总

先构建仿真器，再运行三份研究配置。典型配置会生成 paired C+N；非理想条件
需要用 target/background 两份配置分别生成同 seed 的配对数据：

```bash
./build/simulate_stage2_statistical --config configs/research/ai_csi_stage2_typical_1beam.json
./build/simulate_stage2_statistical --config configs/research/ai_csi_stage2_nonideal_target_1beam.json
./build/simulate_stage2_statistical --config configs/research/ai_csi_stage2_nonideal_background_1beam.json
python3 scripts/run_ai_csi_oracle_suite.py
```

汇总入口会生成：

- `outputs/ai_csi_oracle/single_factor_summary.csv`
- `outputs/ai_csi_oracle/leave_one_out_summary.csv`
- `outputs/ai_csi_oracle/oracle_overall_summary.csv`
- `outputs/ai_csi_oracle/current_vs_oracle_scnr_improvement.png`
- `outputs/ai_csi_oracle/mismatch_contribution.png`
- `outputs/ai_csi_oracle/ca_target_loss_pd.png`

## 研究文档

- [当前对消数学模型](docs/AI_CSI_01_当前对消数学模型.md)
- [Current–Oracle 实验报告](docs/AI_CSI_02_Current_Oracle实验报告.md)
- [后续 AI 方法建议](docs/AI_CSI_03_后续AI方法建议.md)
- [研究进展](docs/AI_CSI_研究进展.md)
- [Oracle 分析清单](outputs/ai_csi_oracle/oracle_analysis_manifest.json)

## Git

远程仓库：<https://github.com/shy-sgsg/PGRCC>

当前工作区的预置 `.git` 是只读挂载，因此本地开发环境将 Git 元数据放在
`.git-real`。在该工作区操作 Git 时使用：

```bash
git --git-dir=.git-real --work-tree=. <command>
```
