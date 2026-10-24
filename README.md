# PGRCC：物理模型驱动的 GMTI 稳健处理研究

**当前研究主题建议：物理模型驱动的 GMTI 稳健杂波抑制、动目标检测与参数估计。**
它是可根据证据调整的研究框架，不是已定稿的博士论文题目，也不预设必须使用 AI。

## 项目研究定位

| 问题 | 当前回答（截至 2026-10-24） |
|---|---|
| 研究什么、为什么研究？ | 研究机载 GMTI 中杂波、动目标和系统约束如何共同影响杂波抑制、目标检测及参数估计；避免只优化中间信号指标而忽略任务结果。 |
| 最终目标是什么？ | 找到可复现的性能瓶颈，解释其物理/算法机制，建立公平传统 Baseline，并针对证据最充分的问题提出能改善任务性能的方法。 |
| 有哪些研究模块？ | 物理处理链与传统能力边界；瓶颈识别与机制归因；物理模型约束的稳健抑制/参数估计；满足证据门后可选的物理与数据驱动协同。 |
| 总体技术路线？ | 物理与任务建模 → 生产链/信息边界审计 → 评价契约 → 公平 Baseline → 瓶颈归因 → 单一创新问题 → 强基线对照 → 独立场景验证。 |
| 当前做到哪里？ | Phase R 的 R3–R12 已审查受控 C2 `+4 ns` 时延损失、已知校正、确定性估计和生产评价链。R12 最终决定 `PIVOT` 到其他 GMTI 瓶颈的公平 Baseline 评价。 |
| 哪些方向已停止或未成立？ | 固定静态时延估计不再是主要创新方向，保留为确定性 Baseline；当前证据未建立 AI 必要性，也未判定低速目标或复杂杂波谁是优先瓶颈。 |
| 下一项优先任务？ | 冻结小规模传统 Baseline 比较的共享评价契约，先审计 CFAR unique-union false-hit/Pfa 与径向速度可评估性，再界定公平可比的任务指标。 |
| 从哪里读长期计划？ | [研究总纲](docs/research/RESEARCH_MASTER_PLAN.md)；方向变化见[研究决策日志](docs/research/RESEARCH_DECISION_LOG.md)。 |

上述阶段结果仅适用于报告中注明的源码、配置、场景与指标。R3/R8 的时延退化与 Known 恢复属于受控仿真证据，不证明真实机载误差分布，也不等于统计显著的总体 Pd/Pfa 提升。R12 仅一个 seed、每条件三个目标机会；统计性 Pfa、最终 CFAR 唯一 hit-cell 和径向速度 RMSE 仍不可评估。详见 [Phase R 证据导航](docs/research/phase_r/README.md) 和 [R12 方向裁决](docs/research/phase_r/R12_final_research_direction_decision.md)。

## 当前生产处理链

```text
4-channel protocol IQ
  → (1,3)/(2,4) 融合为 F1/F2
  → 脉冲压缩、CTDR/DBS/P38 与相位处理
  → 双通道 CSI
  → production GO-CFAR
  → 聚类、筛选、定位/速度估计
  → TrackManager / PIPE
```

当前生产 CSI 的信息边界是 F1/F2 双通道。四通道 STAP/JDL 等若使用额外空间自由度，应单独说明或通过信息量匹配层拆分算法贡献；不同信息条件不能混为一个方法排名。

## 研究模块与阶段路线

1. **物理模型和传统方法能力：** 核实生产处理、输入信息与候选算法。
2. **瓶颈识别：** 区分物理可分性、系统/杂波模型、算法、实现和评价问题。
3. **针对性创新：** 只选择证据最充分的一项问题，与强传统 Baseline 公平比较。
4. **可选数据驱动方法：** 仅在确定性方法留下稳定、任务相关且推理时可观测的残差后评估 AI。
5. **独立验证与论文：** 在未参与方法选择的场景验证任务收益、目标保持、错误输出和计算代价。

分阶段目标、进入/退出/停止条件及方向变更规则见[研究总纲](docs/research/RESEARCH_MASTER_PLAN.md)。完整阶段审查按[ChatGPT 科研审查规范](docs/research/CHATGPT_REVIEW_PROTOCOL.md)执行。

## 推荐阅读顺序

1. [研究总纲](docs/research/RESEARCH_MASTER_PLAN.md)：当前唯一有效的总体研究计划；
2. [研究决策日志](docs/research/RESEARCH_DECISION_LOG.md)：阶段结论如何改变当前方向；
3. [Phase R 证据导航](docs/research/phase_r/README.md)：R3–R12 报告、实验身份和限制；
4. [ChatGPT 科研审查规范](docs/research/CHATGPT_REVIEW_PROTOCOL.md)：完整审查流程与可复制提示词；
5. [研究进展](docs/AI_CSI_研究进展.md)：历史阶段记录；
6. 具体阶段报告、源码、配置和对应证据包。

## 源码和研究材料导航

- `src/`、`include/`：GMTI/CUDA 生产处理；
- `simulator/`：Stage1/Stage2/Stage3 仿真与输入生成；
- `configs/`、`configs/research/`：工程与研究配置；
- `scripts/`、`tests/`：复现、分析、评价及工程验证；
- `docs/research/phase_r/`：Phase R 阶段报告与紧凑证据导航；
- `docs/papers/`、`docs/literature/`：论文候选与定向文献审查，须按日期和适用条件阅读。

以下构建、历史实验复现和旧阶段结果用于工程复现，不自动构成当前研究计划。

## 环境要求

构建主工程需要 CMake、C++ 编译器、CUDA Toolkit、FFTW3 和 libtiff。第一阶段
离线分析脚本需要 Python 3、NumPy 和 Matplotlib。

以下是历史验证环境记录：CUDA Toolkit 12.4.131、CMake 4.2.3、FFTW3 3.3.10；当时
受限提权后的 `nvidia-smi` 可见 RTX 3050 Laptop GPU（4 GiB），并完成了 TrackManager/PIPE
correction smoke 和 12-case formal。它不代表当前会话的设备可见性或运行状态；每次需要
CUDA 的任务都应先探测设备，权限阻挡时主动申请受限提权。历史 local-input production
formal 是正确性与指标闭环，不是 SHM 吞吐或部署性能基准；历史 CPU offline 结果不得写成
CUDA 生产性能结果。

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

## 历史实验复现：第一阶段 Current–Oracle 汇总

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
space-time academic reference 组；历史报告中两组不混排。这里的四通道 reference
属于冻结的 Phase-II 归档，不是当前 Phase-I production STAP。覆盖理想/均匀、
非均匀杂波、
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

## Phase-I 双通道未知误差与稳健 CSI（历史阶段快照）

> 以下记录保留 Phase-I 当时的处理定义、实验数字和复现入口，不代表 R12 之后的当前
> 研究主线或下一步计划。当前方向以[研究总纲](docs/research/RESEARCH_MASTER_PLAN.md)
> 与[研究决策日志](docs/research/RESEARCH_DECISION_LOG.md)为准。

推荐阅读顺序是 [双通道研究框架](docs/AI_CSI_34_双通道系统失配与稳健CSI研究框架.md) →
[双通道可观测性报告](docs/AI_CSI_35_双通道系统误差可观测性分析.md) →
[历史参数与可观测性分析](docs/AI_CSI_33_真实系统误差参数与可观测性分析.md)。
当前实现和证据沿以下顺序组织：

1. 以四通道协议 IQ 为输入，先按 `(1,3)`、`(2,4)` 融合为 F1/F2；六个 raw pair 只作
   debugging/cross-validation，不计入科学可观测性自由度；
2. 对六维状态做 zero/+/− 单参数扰动和 central difference，分别检查 cross-channel phase、
   phase-vs-frequency/pulse/angle、range-block、P38、clutter ridge、CTDR、slow-time、
   coherence 和 CSI residual；
3. 用观测行平衡后的 sensitivity matrix 做 rank/condition/column-cosine 判读，同时保留
   原始灵敏度、符号、range/angle 覆盖和 seed stability；
4. 首个可落地校正是 channel delay：blind 只读取 target-free calibration raw，known
   只作 `Known-error correction upper bound`，三分支均复用生产 TrackManager/PIPE 和
   `track_debug` 反查；
5. 后续 temporal decorrelation/robust CSI 只按契约配置启动，必须先通过当前误差残差、
   target causal transfer、GO-CFAR 和 TrackManager 因果审计。

channel-delay 单误差的论文级方法、A0/A1/A2/A3 比较、D1/D2/D3 与传统 baseline、
target-off 四层 waterfall、ID-switch 审计和正式矩阵复现入口见
[`AI_CSI_36_单一系统误差确定性自校准阶段报告.md`](docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md)。
论文结构见
[`Paper1_ChannelDelay_SelfCalibration_Outline.md`](docs/papers/Paper1_ChannelDelay_SelfCalibration_Outline.md)。

最新 54-case F1/F2 可观测性证据位于
`outputs/two_channel_error_observability_phase_i_20260916/`，其 `manifest.json` 为
`completed`、54/54 case 通过、scaled rank=6、condition number 约 37.328；
`sensitivity_matrix_raw.csv`、`sensitivity_matrix_scaled.csv`、`pair_observability.csv`、
`zero_control.json`（同 seed error-free reference delta=0，容差 1e-9）、
`sensitivity_stability.csv` 和含物理 range/frequency/block 边界的
`range_angle_coverage.csv` 共同构成判读依据。此前无有效快时间信号的矩阵保留在
`outputs/two_channel_error_observability_invalid_zero_signal_20260915/`，不作为结果引用。

TrackManager/PIPE delay correction CUDA smoke 证据位于
`outputs/track_delay_smoke_4ch_gpu_reaudit_20260916/`；该 smoke 的三分支生产运行和
payload reverse audit 通过，实际为 4ch protocol（378,496 bytes/packet），但仅为
1 seed、1 个目标速度、1 个 SNR、3 个周期，不能替代正式统计收益结论。
正式矩阵证据位于 `outputs/track_delay_formal_4ch_local_20260916/`：5 periods × 3
seeds × 2 target velocities × 2 SNR，共 12/12 case、36/36 branch 完成；所有 runtime XML、
calibration provenance、TrackManager audit 和 PIPE payload reverse audit 通过，0 violation，
blind 与 known 均记录 `correction_applied=true`，估计器未读取 truth。加权 target-period Pd
为 Current `55/60=0.9167`、blind/known `59/60=0.9833`；all-visible Track Pd 为
`42/60=0.7000`、`47/60=0.7833`。ID switch 总数从 `17` 增至 `23`，因此不能写成整体
校正收益；正例 target-on 链路没有 valid-CUT/target-off 分母，cell-Pfa 与 cell false-hit
为 `not_evaluable`，detection-record、payload 和 cluster-association proxy 仅作分层诊断。
该矩阵使用 local-input 生产 core，不代表 SHM 吞吐或部署性能。

新的 Stage-1 Formal-v2 已完成 135/135 case；7 方法 Monte Carlo 为 189 行、每格 100
trials 且 `passed`，紧凑证据位于
`outputs/formal_evidence/stage1_delay_v2_full_20260917/`；不可变 Formal-v1 仍在
`outputs/formal_evidence/stage1_delay/`。Formal-v2 的 27 个 delay/SNR 参数点平均
estimator RMSE 为 D1/D2/D3=`0.053395/0.053359/0.054587 ns`，D4=`0.030293 ns`，
cross-correlation/GCC-PHAT=`2.027616/2.990429 ns`。A1→A3 的 135-case 平均 target-period Pd 为
`0.9556→0.9793`，position RMSE 为 `220.4825→161.4688 m`，angle RMSE 为
`0.1358→0.0969 deg`；ID switch 为 `248→290`，且 materiality threshold 尚未预注册，
所以只报告描述性/配对统计，不宣称无条件整体收益。Formal-v2 raw case 树已在 compact
和 manifest 校验后删除，保留 root run manifest、135 个 case manifest 压缩归档、命令历史
和 retention manifest；精确边界见 [`清理记录_2026-09-17.md`](docs/清理记录_2026-09-17.md)。

Formal-v2 channel-delay 的清洁重跑入口（需要已构建的
`simulate_stage2_statistical` 和真实 CUDA；本次已执行的 representative/resume 命令另见
`outputs/formal_evidence/stage1_delay_v2_full_20260917/formal_v2_execution_commands.txt`）：

```bash
python3 scripts/run_delay_stage1_formal.py --mode formal --evidence-version formal-v2 --input-mode local \
  --output-root outputs/formal_delay_stage1_v2_20260917 \
  --delay-errors-ns 0,1,-1,2,-2,4,-4,8,-8 \
  --working-point registered --mc-trials 100 --cleanup-raw
python3 scripts/analyze_delay_stage1_formal.py \
  --run-manifest outputs/formal_delay_stage1_v2_20260917/manifest.json \
  --output-root outputs/formal_evidence/stage1_delay_v2_full_20260917
```

此前 12-case TrackManager formal 的历史兼容入口为：

```bash
python3 scripts/run_track_manager_e2e.py --input-mode local --period-count 5 \
  --seeds 101,202,303 --target-velocities-mps 6.7,12.0 --snr-db 30,35 \
  --output-root /tmp/pgrcc_track_delay_formal_4ch_local_repro
```

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

本轮可观测性矩阵最短复现入口（需要已构建的 `simulate_stage2_statistical` 和真实 CUDA）：

```bash
python3 scripts/audit_two_channel_error_observability.py \
  --compact --period-count 1 --seeds 101 202 303 \
  --output-root outputs/two_channel_error_observability_repro
```

首个 TrackManager delay correction smoke：

```bash
python3 scripts/run_track_manager_e2e.py --smoke --input-mode local \
  --period-count 3 --target-velocities-mps 6.7 --snr-db 30 \
  --output-root /tmp/pgrcc_track_delay_smoke_4ch_gpu_repro
```

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

- [PGRCC 研究总纲（当前唯一有效计划）](docs/research/RESEARCH_MASTER_PLAN.md)
- [研究决策日志](docs/research/RESEARCH_DECISION_LOG.md)
- [ChatGPT 科研审查规范与提示词](docs/research/CHATGPT_REVIEW_PROTOCOL.md)
- [Phase R：GMTI 性能瓶颈与 AI 必要性审查入口](docs/research/phase_r/README.md)
- [研究主线重构与历史结果重新解释（历史快照）](docs/AI_CSI_研究主线重构与历史结果重新解释.md)
- [真实系统误差参数与可观测性分析](docs/AI_CSI_33_真实系统误差参数与可观测性分析.md)
- [双通道系统失配与稳健 CSI 研究框架（历史快照）](docs/AI_CSI_34_双通道系统失配与稳健CSI研究框架.md)
- [双通道系统误差可观测性分析](docs/AI_CSI_35_双通道系统误差可观测性分析.md)
- [单一系统误差确定性自校准阶段报告](docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md)
- [Channel-delay self-calibration 论文候选提纲（历史候选，非当前 Paper 1）](docs/papers/Paper1_ChannelDelay_SelfCalibration_Outline.md)
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
