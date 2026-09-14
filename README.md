# PGRCC

真实系统未知误差驱动的多通道 GMTI 杂波抑制研究仓库。

## Research Question

真实 airborne GMTI 中的 INS、平台运动、伺服/波束指向、通道时延、幅相、时钟同步、
基线几何和杂波时间统计可能并不准确或并不完全可用。这些未知误差会破坏跨通道
杂波相干性，降低 CSI/STAP 的杂波抑制和目标保持能力。本仓库当前研究主线是：

```text
真实系统未知误差 → 多通道回波观测 → 未知误差/状态参数估计
→ 物理模型修正与通道自校准 → 恢复相干性
→ CSI / 四通道 STAP → CFAR / Pd / Pfa / 目标保持
```

AI 不是目标本身。当前阶段 `ai_training=false`，先做误差建模、可观测性、确定性
校准和端到端验证；只有确定性方法存在稳定且可量化的残差时，才重新评估 Physics-AI。

## Current Production Chain

Current 的生产定义是：

```text
4-channel raw IQ
  → channel-pair fusion: (1,3), (2,4)
  → F1/F2
  → pulse compression
  → CTDR
  → DBS/Doppler
  → range phase correction
  → P38 phase model
  → CSI
  → production GO-CFAR
```

F1/F2 在 Current CSI 之前完成。四通道 STAP 保留四个空间自由度，因此二者应做
端到端比较；同时提供信息量匹配层，用于区分算法提升与融合/空间自由度提升。

## 当前阶段状态

- 下一阶段名称：`Unknown System Error Characterization / 真实系统未知误差建模与可观测性分析`。
- 已完成 Phase0 paired-reference sanity、Phase1 unknown-only blind estimator、Phase2
  true/reported four-channel geometry、Phase3 27-case blind matrix，以及 Phase4
  B0/B1/B1K 生产 CUDA CSI/CFAR 和 B2/B3/B3K 离线 STAP reference；完整结果和限制见
  [阶段文档](docs/AI_CSI_33_真实系统误差参数与可观测性分析.md)。
- 历史 `baseline_error_m` 仅保留为 `group_baseline_error_legacy_pilot` regression
  mode；真实几何 pilot 使用 `channel_geometry.mode=true_channel_positions`，echo
  读取 true positions，处理端保留 reported positions。
- 当前 E2E 是受控 one-beam/beam-center-clutter moving-target pilot；B2/B3/B3K 是离线
  scientific reference，不宣称生产 CUDA 四通道 STAP。TrackManager/PIPE、servo/beam
  和平台速度 true/report state 仍未完成。
- `NO_GO_AI_ROUTER_VALUE` 只关闭 Current/J5/J6 Router 的安全平均材料性，不关闭
  Physics-AI 或未知系统误差估计主线。

## Research Directions

- **A — Current / two-channel CSI**：保留生产 F1/F2 融合和 Current CSI，作为生产兼容基线。
- **B — Four-channel adaptive / STAP**：直接使用四通道原始 IQ，保留四个空间自由度，
  与 A 做端到端能力比较，并另做信息量匹配比较。
- **C — Unknown system error / self-calibration**：从多通道回波估计 INS、平台运动、
  伺服/波束、通道同步、幅相和基线等未知量，修正物理模型和通道参数。
- **D — Target-safe clutter suppression**：除 SCNR/杂波抑制外，检查 target transfer、
  Pd、Pfa、目标保持和虚警簇。
- **E — Physics-AI only when needed**：仅在确定性估计已经暴露稳定、可量化且难以解析的
  残差后，评估 AI 估计器/残差/置信度；AI 不直接输出清洗后的 RD 图。

明确排除的分支：通用 complex-weight residual AI；Current/J5/J6 Router 的
`NO_GO_AI_ROUTER_VALUE` 分支；当前实现下的 pulse-frequency J8 分支；以及为追逐
0.0047 dB 差异而扩张训练或图像到图像网络。这里排除的是对应方法，不是未知系统误差
建模、自校准或四通道 STAP 主线。

## 第一阶段历史基线

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

这里的 Oracle All 是背景-only 线性复权对照，不是无条件性能上界；当前统一称为
历史的 `Known-error correction upper bound / 已知误差校正上限`。这些数字保留为
第一阶段证据，不直接推出需要训练 AI。新实验应使用 Ideal、Current+unknown-error、
Known-error correction 和 Estimated-error correction 四个条件，分别计算可恢复空间、
实际恢复量和 recovery ratio。

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
docs/AI_CSI_33_真实系统误差参数与可观测性分析.md  下一阶段误差参数、传播和 pilot 设计
configs/research/unknown_system_error_baseline_pilot.json  有界四通道基线误差 pilot 配置
configs/research/unknown_system_error_true_geometry_pilot.json  true/report geometry 与 blind matrix 配置
configs/research/unknown_system_error_end_to_end_pilot.json  生产 CSI/CFAR 与离线 STAP E2E 配置
scripts/run_unknown_system_error_matrix.py  Phase3 unknown-only 几何估计矩阵
scripts/run_unknown_system_error_end_to_end.py  Phase4 B0/B1/B1K/B2/B3/B3K E2E runner
outputs/unknown_system_error_pilot_20260913_v5/  raw-IQ pilot 的紧凑观测与审计产物（raw BIN 本地保留，不入 Git）
outputs/unknown_system_error_geometry_matrix_20260914_v2/  27-case blind matrix 紧凑汇总
outputs/unknown_system_error_end_to_end_20260914/  三速度 E2E 的生产/离线指标和 provenance
outputs/unknown_system_error_pilot_20260914_clean/  clean archive Phase0 provenance
outputs/ai_csi_oracle/      小型 CSV/PNG/JSON 研究交付物
outputs/ai_csi_baseline/   7 场景 × 6 方法的传统 baseline 交付物
outputs/ai_csi_baseline_v2/ Baseline V2 历史 screen 与 sanity 交付物
outputs/ai_csi_baseline_v21/ Baseline V2.1 clean-commit screen/sanity 交付物
outputs/ai_csi_baseline_v21_velocity/ 物理重生成 velocity/MDV 交付物
outputs/ai_csi_baseline_v21_transition/ 低 SNR transition ROC 交付物
outputs/ai_csi_baseline_v21_mixed_stress/ LHS mixed-stress 交付物
outputs/ai_csi_baseline_v21_final_screen/ 最终提交下的 90-case screen 交付物
outputs/ai_csi_baseline_v21_final_velocity/ 最终提交下的 signed velocity/MDV 交付物
outputs/ai_csi_baseline_v21_final_roc/ 最终提交下的独立 ROC 交付物
outputs/ai_csi_baseline_v21_formal/ 正式 470-case 单因素矩阵交付物
outputs/pgrcc_oracle/       PGRCC-v1 bounded residual Oracle headroom 审计交付物
outputs/system_error_inventory/  真实系统误差清单、传播图和审计 manifest
```

V1 原始 BIN 和逐案例中间结果已按清理策略移除；当前 V2 为复现 Stage2 保留
`build/` Release 目标，但不保留逐 case BIN，不把大体量数据复制进 Git 历史。

## 环境要求

构建主工程需要 CMake、C++ 编译器、CUDA Toolkit、FFTW3 和 libtiff。第一阶段
离线分析脚本需要 Python 3、NumPy 和 Matplotlib。

本阶段已验证的工具链包括 CUDA Toolkit 12.4.131、CMake 4.2.3 和 FFTW3 3.3.10。
受限提权后的 `nvidia-smi` 可见 RTX 3050 Laptop GPU（4 GiB）；本阶段 V2.1
统计仿真器和 Python 算子仍走 CPU offline 回放，没有运行 CUDA production pipeline，
所以结果不是 CUDA 生产运行结果。

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

## 复现历史传统 Baseline 矩阵

Baseline 先于 AI 训练执行，分为同一 F1/F2 输入的 strict 组和原始四通道
space-time academic reference 组；历史报告中两组不混排。这里的“不混排”是信息条件
审计，不是禁止四通道 STAP 与两通道 Current CSI 做端到端比较。当前主线要求同时保留
端到端能力层和信息量匹配层。覆盖理想/均匀、非均匀杂波、
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

## Baseline V2.1（历史基线）

Baseline V2.1 是历史基线，明确区分 production replay 与 scientific controlled baseline；后者的
P38、协方差和自适应权重只使用 paired C+N background。四通道 steering 使用当前
配置的 beam-center、按 range block 推导的几何路径和显式 Doppler temporal response；
clutter ridge 只作诊断特征，不作为目标空间角。包含 diagonally-loaded SMI/MVDR、
shrinkage SMI/MVDR、physical JDL、MNEC 和明确标注为 approximate academic reference
的 SA-MNEC。当前不训练 AI。

先运行 steering sanity，再运行 90-case、9 因素、每点 5 seed 的 screen：

```bash
python3 scripts/run_baseline_v2.py --mode sanity --out outputs/ai_csi_baseline_v21_final_screen
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode screen --workers 2 --out outputs/ai_csi_baseline_v21_final_screen
```

正式 sweep 的更密配置见
[`configs/research/ai_csi_baseline_v2_suite.json`](configs/research/ai_csi_baseline_v2_suite.json)。
物理 velocity/MDV 使用独立输出目录，35 cases、7 个速度点、每点 5 seed：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode velocity --workers 2 \
  --out outputs/ai_csi_baseline_v21_final_velocity
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode transition --workers 2 \
  --out outputs/ai_csi_baseline_v21_transition
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode mixed_stress --workers 2 \
  --out outputs/ai_csi_baseline_v21_mixed_stress
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode roc --workers 2 \
  --out outputs/ai_csi_baseline_v21_final_roc
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode formal --workers 2 \
  --out outputs/ai_csi_baseline_v21_formal
```

V2 历史审计报告见 [`AI_CSI_07_Baseline_V2审计与实验报告.md`](docs/AI_CSI_07_Baseline_V2审计与实验报告.md)；
V2.1 物理修正、sanity 门禁、Expert Map 和 PGRCC-v1 边界见
[`AI_CSI_08_Baseline_V2.1物理修正与最终能力边界.md`](docs/AI_CSI_08_Baseline_V2.1物理修正与最终能力边界.md)。
单 case 只保存二值 `target_detected`；经验 Pd 在 seed/trial 聚合层生成并带 Wilson
95% CI。历史主汇总在 `outputs/ai_csi_baseline_v21/`；本次最终 screen、velocity、ROC
分别在 `outputs/ai_csi_baseline_v21_final_screen/`、
`outputs/ai_csi_baseline_v21_final_velocity/` 和 `outputs/ai_csi_baseline_v21_final_roc/`，
正式矩阵在 `outputs/ai_csi_baseline_v21_formal/`，transition ROC 点/汇总/图在
`outputs/ai_csi_baseline_v21_transition/`，不使用 RD `np.roll` 代替真实速度。

## 下一阶段：真实系统未知误差建模与可观测性分析

阶段文档：[AI_CSI_33_真实系统误差参数与可观测性分析](docs/AI_CSI_33_真实系统误差参数与可观测性分析.md)。
当前已完成并正在沿以下顺序推进：

1. 盘点平台速度/位置、roll/pitch/yaw、伺服指向、通道时延、固定相位、相位漂移、
   增益、时钟、基线几何、载频和杂波时间去相干；
2. 沿模拟器和生产代码建立误差传播、六对紧凑观测量
   `C13,C24,C12,C14,C23,C34` 及可辨识性分析；
3. 已实现 B0 Current、B1 blind/known 两通道 CSI、B2 四通道 STAP、B3 blind/known
   四通道 STAP 的公平比较接口；
4. 已完成 9 个几何误差水平 × 3 个 seed 的 unknown-only blind matrix；
5. 已完成三速度 moving-target 的 production GO-CFAR Pd/Pfa、target-only transfer、
   B2/B3 离线 reference 和 `Known−Current`/`Estimated−Current` recovery 记录。

当前清单和传播图位于 `outputs/system_error_inventory/`；Phase0/3/4 的 manifest 和
六对观测分别位于 `outputs/unknown_system_error_pilot_20260914_clean/`、
`outputs/unknown_system_error_geometry_matrix_20260914_v2/` 和
`outputs/unknown_system_error_end_to_end_20260914/`。本轮没有训练 AI，也没有把
离线 B2/B3 reference 写成生产 CUDA 四通道 STAP。

有界 raw-IQ pilot 的最短复现入口：

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target simulate_stage2_statistical -j4
python3 scripts/run_unknown_system_error_pilot.py \
  --output-root /tmp/pgrcc_unknown_system_error_pilot_repro

python3 scripts/run_unknown_system_error_matrix.py \
  --output-root /tmp/pgrcc_unknown_system_error_geometry_matrix_repro

python3 scripts/run_unknown_system_error_end_to_end.py \
  --output-root /tmp/pgrcc_unknown_system_error_end_to_end_repro
```

运行器拒绝覆盖非空输出目录；Phase0 manifest 会记录 paired-reference 的非 blind
边界，Phase3 会记录 27 个 case 的配置/hash/fit/fallback/CI，Phase4 会记录生产
CSI/CFAR、离线 STAP、P4、target-only transfer、Pfa 定义、runtime 和 GPU provenance。
Phase4 需要真实 CUDA 设备；raw BIN 只用于本地审计，不纳入 Git 提交。

## 历史 Physics-AI / Oracle 门禁（已封存）

本节命令和数字只用于复现历史门禁。文中的 Oracle 统一按“已知误差校正上限”理解，
不代表运行时可读取 truth；`NO_GO_AI_FOR_NOW`、`FINAL_NO_GO_AI` 和下列 residual/
routing no-go 也不关闭当前未知系统误差估计方向。

V2.1 冻结后先运行 Oracle Headroom Audit，不直接训练网络：

```bash
python3 -m py_compile scripts/build_pgrcc_oracle.py
python3 scripts/build_pgrcc_oracle.py \
  --config configs/research/pgrcc_oracle_audit.json \
  --out outputs/pgrcc_oracle
```

当前 16-case、3472-region 审计结论为 `NO_GO_ORACLE_NOT_SUFFICIENT`：严格多指标
worthwhile region 为 0%，因此没有生成训练数据或训练 PGRCC-v1。完整判定、exact
GO-CFAR 代表性复核和后续路线边界见
[`AI_CSI_09_PGRCC_Oracle与数据集设计报告.md`](docs/AI_CSI_09_PGRCC_Oracle与数据集设计报告.md)。

随后完成了不改变旧产物的 V1.1 细网格 headroom 审计，并在 no-go 后运行了 Support
Routing Oracle；两者分别为 `NO_GO_COMPLEX_WEIGHT_RESIDUAL` 和
`NO_GO_SUPPORT_ROUTING`，仍不启动 AI 训练。复现命令、资源检查、exact 统计和产物导航见
[`AI_CSI_10_PGRCC_V1.1与Support_Routing审计报告.md`](docs/AI_CSI_10_PGRCC_V1.1与Support_Routing审计报告.md)。

## 研究文档

- [研究主线重构与历史结果重新解释](docs/AI_CSI_研究主线重构与历史结果重新解释.md)
- [真实系统误差参数与可观测性分析](docs/AI_CSI_33_真实系统误差参数与可观测性分析.md)
- [研究进展](docs/AI_CSI_研究进展.md)
- [当前对消数学模型](docs/AI_CSI_01_当前对消数学模型.md)
- [Current–Oracle 实验报告](docs/AI_CSI_02_Current_Oracle实验报告.md)
- [后续 AI 方法建议（历史候选，非当前主线）](docs/AI_CSI_03_后续AI方法建议.md)
- [Baseline 方法与实现说明](docs/AI_CSI_04_Baseline方法与实现说明.md)
- [Baseline 实验报告](docs/AI_CSI_05_Baseline实验报告.md)
- [Baseline 不足与 Physics-AI 方向分析](docs/AI_CSI_06_Baseline不足与Physics_AI方向分析.md)
- [Baseline V2 审计与实验报告](docs/AI_CSI_07_Baseline_V2审计与实验报告.md)
- [Baseline V2.1 物理修正与最终能力边界](docs/AI_CSI_08_Baseline_V2.1物理修正与最终能力边界.md)
- [PGRCC-v1 Oracle 与数据集设计报告](docs/AI_CSI_09_PGRCC_Oracle与数据集设计报告.md)
- [PGRCC V1.1 与 Support Routing Oracle 审计报告](docs/AI_CSI_10_PGRCC_V1.1与Support_Routing审计报告.md)
- [Joint Gate 失效归因审计](docs/AI_CSI_21_JointGate失效归因审计.md)
- [Selective Physics Calibration V2](docs/AI_CSI_22_SelectivePhysicsCalibrationV2.md)
- [Production CFAR 与泛化统计](docs/AI_CSI_23_ProductionCFAR与泛化统计.md)
- [Physics-AI Final Gate](docs/AI_CSI_24_PhysicsAI_FinalGate.md)
- [Physics-AI 最终 Go/No-Go（历史）](docs/AI_CSI_28_PhysicsAI最终GoNoGo.md)
- [Router Opportunity 与 Materiality（历史）](docs/AI_CSI_32_RouterOpportunity与Materiality审计.md)
- [误差参数清单](outputs/system_error_inventory/parameter_inventory.csv)
- [误差传播图](outputs/system_error_inventory/propagation_map.csv)
- [历史 Oracle 分析清单](outputs/ai_csi_oracle/oracle_analysis_manifest.json)

## Git

远程仓库：<https://github.com/shy-sgsg/PGRCC>

当前工作区的预置 `.git` 是只读挂载，因此本地开发环境将 Git 元数据放在
`.git-real`。在该工作区操作 Git 时使用：

```bash
git --git-dir=.git-real --work-tree=. <command>
```
