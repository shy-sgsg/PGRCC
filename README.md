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
outputs/ai_csi_baseline/   7 场景 × 6 方法的传统 baseline 交付物
outputs/ai_csi_baseline_v2/ Baseline V2 历史 screen 与 sanity 交付物
outputs/ai_csi_baseline_v21/ Baseline V2.1 clean-commit screen/sanity 交付物
outputs/ai_csi_baseline_v21_velocity/ 物理重生成 velocity/MDV 交付物
outputs/ai_csi_baseline_v21_transition/ 低 SNR transition ROC 交付物
outputs/ai_csi_baseline_v21_mixed_stress/ LHS mixed-stress 交付物
```

V1 原始 BIN 和逐案例中间结果已按清理策略移除；当前 V2 为复现 Stage2 保留
`build/` Release 目标，但不保留逐 case BIN，不把大体量数据复制进 Git 历史。

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

## 复现传统 Baseline 矩阵

Baseline 先于 AI 训练执行，分为同一 F1/F2 输入的 strict 组和原始四通道
space-time academic reference 组；两组不混排。覆盖理想/均匀、非均匀杂波、
通道幅相误差、有效样本不足、低 SCNR、近杂波脊和支撑边缘目标。Oracle/背景
训练权重只使用 clutter+noise，目标 truth 仅用于评价。

```bash
python3 -m py_compile scripts/run_baseline_benchmark.py
python3 scripts/run_baseline_benchmark.py
```

配置、方法边界和指标定义见
[`AI_CSI_04_Baseline方法与实现说明.md`](docs/AI_CSI_04_Baseline方法与实现说明.md)；
结果解释见 [`AI_CSI_05_Baseline实验报告.md`](docs/AI_CSI_05_Baseline实验报告.md)，
不足与后续 Physics-AI 接口见
[`AI_CSI_06_Baseline不足与Physics_AI方向分析.md`](docs/AI_CSI_06_Baseline不足与Physics_AI方向分析.md)。
完整紧凑产物在 `outputs/ai_csi_baseline/`，原始 BIN 和临时矩阵默认逐场景清理。

## Baseline V2.1（当前主线）

Baseline V2.1 明确区分 production replay 与 scientific controlled baseline；后者的
P38、协方差和自适应权重只使用 paired C+N background。四通道 steering 使用当前
配置的 beam-center、按 range block 推导的几何路径和显式 Doppler temporal response；
clutter ridge 只作诊断特征，不作为目标空间角。包含 diagonally-loaded SMI/MVDR、
shrinkage SMI/MVDR、physical JDL、MNEC 和明确标注为 approximate academic reference
的 SA-MNEC。当前不训练 AI。

先运行 steering sanity，再运行 90-case、9 因素、每点 5 seed 的 screen：

```bash
python3 scripts/run_baseline_v2.py --mode sanity --out outputs/ai_csi_baseline_v21
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode screen --workers 2 --out outputs/ai_csi_baseline_v21
```

正式 sweep 的更密配置见
[`configs/research/ai_csi_baseline_v2_suite.json`](configs/research/ai_csi_baseline_v2_suite.json)。
物理 velocity/MDV 使用独立输出目录，35 cases、7 个速度点、每点 5 seed：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode velocity --workers 2 \
  --out outputs/ai_csi_baseline_v21_velocity
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode transition --workers 2 \
  --out outputs/ai_csi_baseline_v21_transition
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode mixed_stress --workers 2 \
  --out outputs/ai_csi_baseline_v21_mixed_stress
```

V2 历史审计报告见 [`AI_CSI_07_Baseline_V2审计与实验报告.md`](docs/AI_CSI_07_Baseline_V2审计与实验报告.md)；
V2.1 物理修正、sanity 门禁、Expert Map 和 PGRCC-v1 边界见
[`AI_CSI_08_Baseline_V2.1物理修正与最终能力边界.md`](docs/AI_CSI_08_Baseline_V2.1物理修正与最终能力边界.md)。
单 case 只保存二值 `target_detected`；经验 Pd 在 seed/trial 聚合层生成并带 Wilson
95% CI。主汇总在 `outputs/ai_csi_baseline_v21/`；MDV 原始曲线和阈值汇总在
`outputs/ai_csi_baseline_v21_velocity/`，transition ROC 点/汇总/图在
`outputs/ai_csi_baseline_v21_transition/`，不使用 RD `np.roll` 代替真实速度。

## 研究文档

- [当前对消数学模型](docs/AI_CSI_01_当前对消数学模型.md)
- [Current–Oracle 实验报告](docs/AI_CSI_02_Current_Oracle实验报告.md)
- [后续 AI 方法建议](docs/AI_CSI_03_后续AI方法建议.md)
- [Baseline 方法与实现说明](docs/AI_CSI_04_Baseline方法与实现说明.md)
- [Baseline 实验报告](docs/AI_CSI_05_Baseline实验报告.md)
- [Baseline 不足与 Physics-AI 方向分析](docs/AI_CSI_06_Baseline不足与Physics_AI方向分析.md)
- [Baseline V2 审计与实验报告](docs/AI_CSI_07_Baseline_V2审计与实验报告.md)
- [Baseline V2.1 物理修正与最终能力边界](docs/AI_CSI_08_Baseline_V2.1物理修正与最终能力边界.md)
- [研究进展](docs/AI_CSI_研究进展.md)
- [Oracle 分析清单](outputs/ai_csi_oracle/oracle_analysis_manifest.json)

## Git

远程仓库：<https://github.com/shy-sgsg/PGRCC>

当前工作区的预置 `.git` 是只读挂载，因此本地开发环境将 Git 元数据放在
`.git-real`。在该工作区操作 Git 时使用：

```bash
git --git-dir=.git-real --work-tree=. <command>
```
