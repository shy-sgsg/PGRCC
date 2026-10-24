# Phase R 瓶颈实验结果合并

日期：2026-10-22。实验定义遵循[冻结设计](05_experiment_design.md)。本报告将 R3 的 E2 因果 pilot、R4 的 E1/E4/E5 seed-101 pilot、R3 修复前历史 E3 分开汇总；不新增实验、不扩展 seed，也不把历史结果改标为当前生产证据。

## 阶段结论与证据状态

| 实验 | `IMPLEMENTED` | `EXECUTED` | `EVALUABLE` | `PERFORMANCE_VERIFIED` | 当前可支持的结论 |
|---|---|---|---|---|---|
| E1：低速目标边界（R4，0 ns） | 是 | 是，8 case，seed 101 | 部分；命中/跟踪记录可用，但 0.5、1 m/s 的 P5 背景 ROI 无有效单元，2 m/s 部分缺失 | 否 | 已跑通零误差低速网格的一部分生产路径；单 seed 且背景评价不完整，不能判定方法性能排序 |
| E2：未知时延失配（R3，通道 2 +4 ns） | 是 | 是；A/B/C 为 101、202、303，D 为 101 | 是，限杂波、物理闭合、CSI、目标传递代理、角度/跟踪地速/位置及分支 hit fraction | 有限：三个 seed 的三项任务 RMSE 方向重复；统计区间跨 0 | 本场景中失配与任务误差恶化同向，已知物理校正把误差拉回；不是显著性或泛化结论 |
| E3：有限样本/复杂杂波 | 有历史实现 | 是，历史 128 场景 CUDA 研究 | 否，属于 R3 CFAR 修复前口径，且原性能门失败 | 否 | 仅作历史失败证据；不能据此评价当前生产性能 |
| E4：CSI 残余与目标保持 | 是 | 是；R3 三 seed，R4 seed 101 | 部分；R3 配对指标可用；R4 P5 背景 ROI 仅部分有效；零时延物理残余闭合不适用 | 否 | CSI 残余、Output SCNR 与任务指标并不等价；R4 可评估的 4 m/s 行中 SCNR/背景 ROI 指标为负向 |
| E5：检测与参数边界 | 是 | 是；R3 A/B/C，R4 8 case | 部分；角度、跟踪地速、位置有 truth-matched 结果；径向速度 RMSE 未报告；R4 可算分支 H0 false-CUT fraction，但跨分支身份/检测级虚警与校准 Pfa 不完整 | 否 | 目标机会命中与条件参数误差须分开；当前不具备可报告的径向速度误差或统计性 Pd/Pfa |

标签仅适用于该行所列范围。`EXECUTED` 不自动推出 `EVALUABLE`，`EVALUABLE` 也不自动推出 `PERFORMANCE_VERIFIED`。

## E2：R3 三 seed 因果结果

条件为 A：0 ns Production Current；B：通道 2 +4 ns Production Current；C：同一 +4 ns 输入的 evaluator-only Known Physical Correction。每 seed 配对 H0/OFF、H1/ON、TO/S-only；每场景 3 个 period。D 是仅 seed 101 的 target-free 确定性盲校正，不参与三 seed 统计。详细配置、逐条件证据与失败现场见[R3 运行报告](06_minimal_cuda_causal_pilot.md)及[R3 紧凑证据包](evidence/r3_minimal_cuda_causal_pilot_20261021/)。

### 核心任务指标

表中 A/B/C 是跨三个 seed 的指标中位数；误差越低越好。配对差按 seed 先计算，区间为 n=3、df=2 的 95% paired-t 区间。区间是小样本描述，不是稳健总体保证。

| 指标 | A 中位数 | B 中位数 | C 中位数 | 配对均值差 B−A（95% CI） | 配对均值差 C−B（95% CI） |
|---|---:|---:|---:|---:|---:|
| 角度 RMSE (°) | 0.04415 | 0.12576 | 0.01318 | +0.12913 [−0.10231, +0.36056] | −0.15950 [−0.37657, +0.05757] |
| TrackManager 地速 RMSE (m/s) | 5.16587 | 9.10970 | 1.27918 | +4.26132 [−7.96077, +16.48341] | −7.25389 [−14.59955, +0.09177] |
| 位置 RMSE (m) | 82.524 | 197.823 | 20.566 | +196.854 [−150.952, +544.660] | −250.160 [−588.696, +88.376] |

三个 seed 均满足 B−A>0、C−B<0。按上表中位数计算，C 相对 B 的 RMSE 降幅分别为 89.5%、86.0%、89.6%；但三项配对区间都跨 0，不能声称统计显著。这里的速度是跟踪地速，不是径向速度。

### 杂波、目标保持与不同指标的冲突

- 实际连续面积地杂波存在：每 seed `390` packets、`1,952,655` samples；地面斜距窗 `[84,500, 85,500) m` 可达。seed 间面积均值功率为 `0.1116356–0.1122339`。A/B/C 在同一 seed 复用背景；B 的 H0 F2 输入功率约下降 `0.235–0.247 dB`，C 与 A 差不超过约 `6.0e−8 dB`。
- H0 CSI cancellation 中位数 A/B/C 为 `5.5908/5.1315/5.5896 dB`；配对 B−A 平均差为 `−0.46337 dB`（95% CI `[−0.52321, −0.40353]`）。已知校正 C 回到 A 附近（中位差 `−0.00118 dB`）。A↔C 的 F1/F2 复数闭合在 ON/OFF/TO 均通过冻结的 `1e−3` 相对 RMS 门。
- 自适应 CSI Output SCNR 代理中位数 A/B/C 是 `70.6523/72.8450/70.6509 dB`，方向与跟踪 RMSE 不同。因此不能只用该代理宣称端到端性能改善；它也不能替代正式目标/背景 ROI 指标。
- R3 H0 动态带内分支 hit fraction 为 `17,579–23,107 / 1,581,840`（约 `1.11%–1.46%`）；每 branch 分母为三个 period 的 valid CUT 总数。它是有结构且相关杂波下的分支观测命中比例，不是名义 `Pfa=1e−6` 的验证。R3 每 seed/条件 H1 命中为 `3/3` 可见 period，也不是统计 Pd。
- 配对 ON−OFF 与 TO 的目标复传递在 A、C 间闭合；CSV 中目标幅度比中位数约为 `−6.8449/−6.8458 dB`，相位误差中位数约为 `1.2285/1.2291°`。这些是所定义 tap/ROI 上的诊断量，不是固定线性增益或全系统目标保持证明。
- 径向速度状态为 `unresolved`，径向速度 RMSE `NOT_EVALUABLE`；角度及跟踪地速、位置 RMSE 可评估。

### D：传统盲确定性校正

D 只在 seed 101 执行，估计器仅读取 A1_OFF 的融合 F1/F2，不读取 truth、A0 配对参考或 ON 数据。估计为 `+3.409965 ns`，相对 `+4 ns` 的 evaluator 真值偏差 `−0.590035 ns`；支持数 1,182，拟合相位 RMS `0.04279 rad`、`R²=0.7478`。D 的角度/地速/位置 RMSE 为 `0.04350° / 3.29191 m/s / 67.575 m`，同 seed C 为 `0.01060° / 1.27918 m/s / 16.728 m`，即 D/C 约 `4.11 / 2.57 / 4.04` 倍。该单 seed 对照不能界定传统盲估计器上限，也不能作为 AI 必要性的证据。

## E1/E4/E5：R4 seed-101 零时延 pilot

固定 `seed=101`、`delay=0 ns`，配置地速 `{0.5,1,2,4} m/s` × SNR `{10,30} dB` 共 8 case；每 case 3 period，C0–C3/Mode-A。实际执行了 `8 × 15 = 120` 个真实 CUDA Production branches，全部通过。GPU 为 RTX 3050 Laptop、driver `580.178.04`、CUDA `13.0`。case/branch/P5/CFAR 汇总及源码身份见[R4 manifest 与 CSV](evidence/r4_e1_e4_e5_seed101_20261022/)。

### 数据、处理与闭合门

| 检查 | 重算结果 | 判定 |
|---|---|---|
| 地杂波 | 每 case `390` packets、`1,952,655` samples；实测均值功率 `0.0223352779188`、lag-1 `0.980006534954`。斜距 `[84.5,85.5] km` 对应地距 `[84.2867,85.2892] km` | 8/8 case 注入有效且数值一致 |
| Production | `120/120` selected branches passed；保留 runtime XML、log、逐 period stage、CSI tap、CFAR geometry、检测/跟踪记录 | 执行门通过；不代表完整评价门通过 |
| 理想/none causal triplet | 每 case `18,439,200` samples；最大绝对误差 `1.2102e−6–1.3278e−6`，冻结容差 `5e−5` | 8/8 case 通过 |
| 残余闭合 | 原 evaluator 要求 A0/PK/PKR；本次 R4 固定零时延，仅运行 C0–C3 | evaluator 原始状态保留为 `NOT_EVALUABLE`；对零时延 R4 物理校正残差闭合为 `NOT_APPLICABLE`，不是 pass，也不作本轮性能门 |
| CFAR valid CUT | A0_OFF 两个 detector branch 各 `1,581,840` valid CUT（每 branch 三个相邻 period）；target-free H0 由 A0_ON 的有效面积报告、pre-target paired-background 写入路径及 A0_OFF 复用/目标关闭配置闭合 | **target-free H0 false-CUT fraction** 可评估：`47,356 / 1,581,840 = 2.9937%`、`51,275 / 1,581,840 = 3.2415%`；只有一个 seed/3 个相关 period，且每 branch 的 8 个 case 行重复同一 hit-hash 签名。跨 branch 唯一 cell union、检测级虚警及经统计校准的 empirical Pfa 仍 `NOT_EVALUABLE` |

H0 provenance 需按生成链解释：A0_OFF 本次 Stage2 报告自身显示新生成面积包为 0、写入 packet 为 390，因为它通过 `background_input_dir` 复用 A0_ON 生成的 `A0_background`。Stage2 源码在插入目标前复制 clutter/noise packet 到 paired background；共享背景的 `scenario_resolved.json` 仍带源运行 `target.enabled=true`，所以该字段不能单独代表 sidecar 实际内容。原始 protocol-IQ 已清理，当前证据是留存报告、场景/配对清单、生成代码和真实 Production tap/CFAR 输出，无法重新读回 IQ 作独立数值复算。

配置地速并非径向真值。Stage2 三期均值径向速度依次约为：配置 `0.5/1/2/4 m/s` 对应 `0.48861/0.97722/1.95445/3.90893 m/s`。

### P5 指标覆盖与有效区结果

P5 每 case 共 30 条记录（方法 × ON/TO × 3 period）。背景 ROI 有效数/总数及目标指标有效数/总数为：

| 配置地速 | 背景 ROI 有效 | 目标指标有效 | 解释 |
|---:|---:|---:|---|
| 0.5 m/s（两个 SNR case） | `0/30` | `15/30` | 背景指标不可评估 |
| 1 m/s（两个 SNR case） | `0/30` | `15/30` | 背景指标不可评估 |
| 2 m/s（两个 SNR case） | `20/30` | `30/30` | 部分背景记录 incomplete |
| 4 m/s（两个 SNR case） | `30/30` | `30/30` | 此速度/SNR 下可报告配对结果 |

4 m/s ON 结果如下；A0 与 C0 数值相同。指标定义取自 manifest：SCNR change=`after−before`，CA_ROI=`10log10(before/after)`，target_loss=`−target_gain`。因此 SCNR/CA_ROI 的负值分别表示输出 SCNR 下降、背景 ROI 功率上升；target_loss 负值表示目标功率增益。

| 方法 | SNR 10：SCNR / CA_ROI / target_loss (dB) | SNR 30：SCNR / CA_ROI / target_loss (dB) |
|---|---:|---:|
| C0（及 A0） | `−4.148 / −1.091 / −0.086` | `−4.148 / −1.054 / −0.085` |
| C1 | `−5.763 / −1.622 / −0.086` | `−5.763 / −1.609 / −0.085` |
| C2 | `−3.742 / −1.150 / −0.086` | `−3.742 / −1.124 / −0.085` |
| C3 | `−5.563 / −1.554 / −0.092` | `−5.550 / −1.579 / −0.105` |

在这一个 seed 的可评估 4 m/s 条件中，所有方法 SCNR 与 CA_ROI 指标均为负；C2 的下降幅度在该组里较小，但不能据此排序或推广。低速背景 ROI 失效是覆盖问题，不通过放宽质量门槛修补。

### E5 检测与参数记录

- 40 个 ON branches 共 `120/120` 个可见 period 目标机会命中。TrackManager `track_pd_all_visible=2/3`，确认后为 `1.0`；这是不同处理阶段的记录，不能混成一个 Pd。每 branch 只有 3 个相邻 period，不能作为统计 Pd。
- 40 个 ON branches 均有 truth-matched angle、TrackManager ground-speed、position RMSE；范围分别为 `0.0104–0.2674°`、`2.423–20.649 m/s`、`16.220–419.472 m`。跨不同速度/SNR/方法的范围不作方法排名。
- Radial-velocity RMSE 在 branch summary 中为 `NOT_REPORTED`；不得用地速误差代替。Detection records 的 unmatched 比例为 `90.93%–98.42%`，这是未关联记录占比，不是 CFAR Pfa。

## E3 历史结果边界

历史 E3 是 128-scene Physics-Adaptive Selective Calibration V2（源码身份 `669df38…`），执行时间早于本次 R3 CFAR 修复。其 observed cell hit fraction 中位数约 `8.4e−3`，与配置 `1e−6` 有明显差距，且 J5/J6 target-preservation no-regression gates 未通过。它能证明旧实验确实运行并暴露评价/目标保持问题，不能支持当前 CFAR 或 CSI 性能结论；本轮没有重跑该矩阵。

## 不确定性、失败项与输入保留

- R3 独立统计单位是 3 个 seed，而不是每 seed 的 3 个 period/CUT。三项 RMSE 的预注册 paired-t 95% CI 均跨 0；R3 单 seed D、R4 单 seed 及其 period 结果均不作显著性推断。
- R4 不扩展 seed 202/303：低速 P5 背景 ROI 仍无有效支持；跨 detector branch 的唯一 hit union/总体虚警也无法从 hash 重建，且本轮仅一个 H0 seed。零时延 residual closure 不适用，不再把通用 evaluator 缺少 PK/PKR 记作物理闭合失败。所有失败/不可评估项保留，未降低阈值。
- R3 清理了可重建的协议 `.bin` 中间件，四个指定 case 的 cleanup ledger 合计记录删除 `12,400,174,666` bytes；配置、manifest、XML、日志、Stage2 报告、CSI/CFAR/航迹审计和指标汇总保留。
- R4 compact manifest 记录 72 个 Stage2 protocol-IQ `.bin` 输入的路径、字节数和 SHA-256，但聚合时文件均不存在。因此输入配对身份可审计，F1/F2 输入功率不能从留存 IQ 独立重算；结论仅基于保留的 Stage2 实测杂波、生产 tap 和下游记录。
- R3 的初次 CFAR period identity 失败及 seed 202 沙箱不可见 CUDA 失败均有记录；修复/受限 CUDA 重跑分别完成。R4 聚合 gate 失败属于评价契约/覆盖缺口，不是 120 个 Production 分支崩溃。

## 证据入口

- [R3 完整执行报告](06_minimal_cuda_causal_pilot.md)；[R3 evaluation JSON](evidence/r3_minimal_cuda_causal_pilot_20261021/r3_minimal_cuda_causal_evaluation.json)、[逐 seed CSV](evidence/r3_minimal_cuda_causal_pilot_20261021/r3_cross_seed_condition_metrics.csv)、[比较图](evidence/r3_minimal_cuda_causal_pilot_20261021/r3_cross_seed_comparison.png)。
- [R4 compact manifest](evidence/r4_e1_e4_e5_seed101_20261022/manifest.json)、[case summary](evidence/r4_e1_e4_e5_seed101_20261022/case_summary.csv)、[production branches](evidence/r4_e1_e4_e5_seed101_20261022/production_branch_summary.csv)、[P5 period rows](evidence/r4_e1_e4_e5_seed101_20261022/p5_period_summary.csv)、[CFAR branches](evidence/r4_e1_e4_e5_seed101_20261022/cfar_off_branch_summary.csv)。
- R4 完整 case-root 路径、实际输入身份及 run commands 由 manifest 指向本地 `outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_maps/`；R3 完整 run roots 及 cleanup ledger 路径见其运行报告与证据包 README。

## 当前停止判断

E2 的 +4 ns 任务指标存在三个 seed 同向退化及已知校正恢复的描述性证据，但置信区间宽、径向速度与 Pd/Pfa 未闭合；R4 又显示零失配下低速 P5 背景指标覆盖不足、可评估的 4 m/s 指标不支持净收益。当前证据足以支持进入下一步方向评估，不足以启动 AI 训练或宣称未知状态估计已成为唯一瓶颈。E1–E5 的决策含义与下一实验 gate 见 [07 方向判断](07_research_direction_decision.md) 和 [08 下一阶段计划](08_next_stage_plan.md)。
