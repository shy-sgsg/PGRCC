# R3 修复后最小四条件 CUDA Production 因果 pilot

日期：2026-10-21。审查基线：`74877fe1463b9362dfce7c935a136d82a26ad26b`。本报告只覆盖通道 2 的 `+4 ns` 单误差、seed 101 的 A/B/C/D 和 seed 202、303 的 A/B/C；不是完整传统 baseline 矩阵。

## 结论摘要与状态

| 标签 | 状态 | 范围 |
|---|---|---|
| `IMPLEMENTED` | 是 | CFAR 诊断生命周期/period identity、有效 CUT 计数、匹配 Stage2 延迟逆算子、盲确定性估计器、实验运行与评价脚本。 |
| `EXECUTED` | 是 | 三个 seed 的 A/B/C 均运行真实 CUDA Production；seed 101 另外执行 D。每个条件保留 OFF/H0、ON/H1、TO/S-only。 |
| `EVALUABLE` | 是（限下表指标） | 面积杂波、输入闭合、CSI、目标复传递、角度/跟踪地速/位置 RMSE、H0 分支 hit fraction。径向速度、汇总 Pfa 不可评估。 |
| `PERFORMANCE_VERIFIED` | 是（限向） | 三个独立 seed 的角度、地速、位置 RMSE 均为 B>A（误差增加）、C<B（误差降低）；不代表统计显著性、Pd/Pfa 或整体系统性能。 |

单 seed 101、202、303 都只有 3 个相邻 CPI/period；即使三 seed 的方向一致，也只作描述性重复验证，不报告显著性或泛化收益。

## 场景、运行与源码身份

- 单一通道时延：通道 2 `+4 ns`；采样率 `60 MHz`，即 `+0.24` fast-time sample。目标位于 beam 50、斜距 85 km、径向速度 6.7 m/s、SNR 30 dB；每场景 3 个 period。
- A：0 ns Production Current；B：+4 ns Production Current；C：B 的 evaluator-only 已知物理时延校正；D：B 的 A1_OFF 无目标 F1/F2 确定性盲估计校正。D 估计过程未读取 truth、A0 配对参考或 ON 数据。
- CUDA：NVIDIA GeForce RTX 3050 Laptop GPU。各 seed 的运行前/后 `nvidia-smi` 快照见完整 run manifest；seed 101 开始为 P8/41°C/6.13 W，结束为 P0/45°C/11.80 W；seed 202 为 P8/44°C/6.20 W → P0/47°C/12.04 W。此处是正确性实验，不是性能基准。
- HEAD 为审查基线且工作树当时为 dirty。run manifest 保存 Production/Stage2 可执行文件和输入脚本身份。seed 101 的 `GMTI_pipe_core` SHA-256 为 `f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962`，`simulate_stage2_statistical` 为 `5dd0ac4e2536b8bcf2508cc53db8a30780eba75f40c025e13ffaa930f8ba7b9b`；Production 修复源 `src/processOnePeriod.cpp` SHA-256 为 `07d875c9ba7cd727b6f33dc69eda234a6ea2d49924bea509aea90d170bb62957`（完整身份以对应 run manifest 为准）。
- 完整 case manifests、命令、阶段配置及环境快照保留在本地 `outputs/phase_r_minimal_cuda_causal_pilot_20261021/`；汇总 JSON/CSV/图、smoke 关键审计文件及 CUDA 环境失败日志归档在本报告相邻 `evidence/r3_minimal_cuda_causal_pilot_20261021/`。

## 修复后 Production 路径门

seed 101 的 A0/0 ns/ON CUDA Production smoke 通过。实际 runtime XML、启动配置转储、完整日志、`timing_metrics.csv`（逐 period processing stage）、CFAR geometry diagnostic、CSI tap summary、branch manifest 和源身份均保留在证据归档。Smoke 的日志含 3 个 CSI cancellation、3 个 CFAR summary、33 条 beam-stage timing；CSI tap 共 12 行。每个 CFAR 分支 3 个 period 共 `1,581,840` 个有效 CUT（每 period `527,280`）。

R3 问题核验：

1. **CFAR geometry 指针生命周期与 period identity**：诊断对象在容器 append 后按索引取用，避免 append 导致失效指针；period ID 取 acquisition cycle ID、Stage2 period ID 或 protocol period index，不再取 beam slot。初次 smoke 的审计器曾看到 period ID 重复；修复后重跑的诊断包含 0/1/2 三个 period，geometry gate 通过。
2. **有效 CUT 分母**：`valid_cut_count`、`threshold_test_count`、`hit_cut_count` 独立记录；hit fraction 为 hits/valid CUTs。OFF/H0 有效，但动态结构杂波命中比例不称为 Pfa；重叠 branch 也不合并成 pooled Pfa。
3. **Mode-B ON/OFF 算子**：代码契约是 ON 估计一次并对 ON/OFF/TO 固定应用同一参考，现有层级校准单测覆盖该契约；本次最小实验未运行 Mode-B 分支，故这里只能标为源码/测试核验，不能标成实际 Production 对比。
4. **A0 ↔ Known Physical Correction 闭合**：F1 与配对 A0 完全相同；F2 的相对复 RMS 在三 seed 的 ON 为 `8.68e-5–9.12e-5`、OFF 为 `9.13e-5–9.56e-5`、TO 为约 `7.10e-8`，均低于预先冻结的 `1e-3`。时延注入使用 Stage2 的零延拓线性分数延迟算子，已知校正使用其匹配逆算子；没有降低闭合容差。
5. **Scan beam quality gate**：本次单 beam Production smoke 通过，没有为成功下调质量门槛。既有 Mode-A/C4 `robust_ddc_rb` scan 审查记录的失败根因是有效相位支持不足：六个 range bin 的 band 低于 `min_support=8`，适配器返回 `insufficient_phase_support`；不得把阈值降到 6。当前工作树未找到该历史完整 scan 失败 manifest，因此该条属于既有审查记录与当前源码逻辑一致的根因说明，不是本次重跑复现；对应 scan 产物不在本次证据包。完整 scan gate 尚未验收。

CFAR geometry 每个 period 有 4096 个 range 列、130 个 Doppler 行、5,200 个 edge-invalid cells；因此有效距离列为 4,056/row，形成 `527,280` 个 valid CUT/period。三 period 合计 `1,581,840` valid CUT/branch。该数与 hit fraction 的分母分开记录，并不把 HIT/CUT 自动解释为标称 Pfa。

## 地杂波注入与输入可比性

此前 6 km 平台高度却将斜距设为 2.5–5 km，不可能形成地面散射。本次将连续纹理面积地杂波窗口设为 `[84,500, 85,500) m` 斜距；地面高程 0 m，对应 ground range `[84,286.713, 85,289.214] m`。Stage2 报告实测 `continuous_area_packets=390`、`continuous_area_samples=1,952,655`，三 seed 均通过；不是只凭 `area_enabled=true`。

| seed | 面积杂波均值功率 | 注入 packets / samples | A→B H0 F2 输入功率变化 | A→C H0 F2 功率差 |
|---:|---:|---:|---:|---:|
| 101 | 0.1116764 | 390 / 1,952,655 | −0.2351 dB | −5.68e−8 dB |
| 202 | 0.1116356 | 390 / 1,952,655 | −0.2380 dB | −6.00e−8 dB |
| 303 | 0.1122339 | 390 / 1,952,655 | −0.2466 dB | −5.72e−8 dB |

每个 seed 的 A0 background 被该 seed 内 A/B/C 共用。F1 H0 功率不变；seed 101 A 的 H0 F1/F2 均值为 27.1803/27.2683，H1 为 29.9297/30.0177。B 的 F2 约下降 0.22–0.25 dB，符合该 Stage2 分数延迟算子对输入功率的改变；C 恢复到 A，故没有通过重新归一化掩盖通道误差。seed 101 D 的 H0 F2 比 A 低 `0.0831 dB`；seed 202/303 的 F1/F2 和 ON/OFF/TO 全表、包括 D 的 seed101 数据，在完整 evaluation JSON 的 `input_fusion_power` 中。

## 结果

下表为三 seed 条件中位数。RMSE 越低越好；SCNR 是按 TO 目标 ROI 与 OFF 的同 truth ROI 计算的**自适应 CSI 输出代理量**，不是固定线性处理增益。

| 条件 | H0 CSI 杂波抑制 (dB) | Output SCNR 代理 (dB) | 角度 RMSE (°) | 跟踪地速 RMSE (m/s) | 位置 RMSE (m) | H1 命中 |
|---|---:|---:|---:|---:|---:|---:|
| A — 0 ns Current | 5.5908 | 70.6523 | 0.04415 | 5.1659 | 82.524 | 3/3 每 seed |
| B — +4 ns Current | 5.1315 | 72.8450 | 0.12576 | 9.1097 | 197.823 | 3/3 每 seed |
| C — +4 ns Known correction | 5.5896 | 70.6509 | 0.01318 | 1.2792 | 20.566 | 3/3 每 seed |

三 seed 每个任务 RMSE 都是 B>A、C<B。B 相对 A 的中位数退化为：角度 RMSE 约 2.84 倍、跟踪地速 RMSE 约 1.76 倍、位置 RMSE 约 2.40 倍。C 相对 B 的误差下降约为 89.5%、86.0%、89.6%；C 与 A 的相位/幅度及 F1/F2 闭合也一致。该结论是这一个目标、一个速度/SNR 与一个误差模型下的因果 pilot，不是多目标/多速度/实际空中数据结论。

H0 杂波残余功率方面，CSI cancellation 中位数 B 比 A 低约 0.459 dB，C 恢复至 A 附近（差约 −0.0012 dB）。另一方面，adaptive-CSI Output SCNR 代理值 B 较高；这与 tracking RMSE 方向相反，故不把单个 SCNR 代理当作全链路改善。成对 ON−OFF 对 TO 的复幅度/相位保持代理值和逐 seed 检测情况均在 JSON/CSV；各条件未出现零复传递，但存在部分幅度/相位差异，C 的传递与 A 逐 seed 闭合。

H0 的 `union_dynamic_in_band` 每个 seed/条件均有 `1,581,840` 个有效 CUT；该 branch 配置标称 `Pfa=1e-6`。命中数 A/B/C 分别为：seed101 `18,501/22,779/17,816`；seed202 `18,110/19,672/18,284`；seed303 `17,579/23,107/17,959`，对应结构杂波 H0 false-hit fraction 约 `1.11%–1.46%`。这是分支各自的 observed structured-clutter hit fraction，不把它当作经独立同分布/虚警校准的 Pfa，也不跨重叠分支 pooled。每 seed OFF false-cluster 数 A/B/C 为 `362/369/345`、`357/401/348`、`342/366/357`。

每个 seed 的 A/B/C 目标检测为 3/3 可见 period，未显示这一场景下 hit-count 差异；这既不是独立 Pd 估计，也不是统计性 Pd 改善。H0  false hit/cluster 很多，当前不宜把检测命中数解读成低虚警下的目标可探测性。径向速度估计 `ambiguity_status=unresolved`，因此径向速度 RMSE 标记 `NOT_EVALUABLE`；表中的速度 RMSE 是跟踪地速误差，不是径向速度误差。角度 RMSE 可以评价。

## D：传统盲确定性校正

D 仅在 seed101 执行。估计器只读 A1_OFF 目标关闭的融合 F1/F2，估计 `+3.409965 ns`，使用 1,182 个支持样本，拟合残差相位 RMS `0.04279 rad`、`R²=0.7478`。相对于事后已知的 `+4 ns`，估计偏差为 `−0.590035 ns`；真值只进入 evaluator 和校正后结果审计，不进入估计器。

seed101 D 的角度/地速/位置 RMSE 为 `0.04350° / 3.2919 m/s / 67.575 m`，C 为 `0.01060° / 1.2792 m/s / 16.728 m`。D 比 C 的三个误差分别约 4.11、2.57、4.04 倍。该盲方法单 seed 下落后于已知校正，但未在 seed202/303 运行；不能据此断言传统盲估计整体上限，更不能据此认定 AI 必要。

## 失败现场和清理

- **seed101 初次 CFAR smoke**：geometry 诊断周期身份重复，导致审计脚本无法将 CUT denominator 与三个 period 对齐；修复 period identity 后重跑，保留了首次失败 manifest/log 与成功重跑文件。
- **seed202 首次普通沙箱运行**：Stage2 已成功且面积杂波有效，但 CUDA 子进程不可见设备，Production 日志为 `no CUDA-capable device is detected`；该失败 manifest/XML/log 保留。受限 CUDA 执行环境重跑 seed202 后，A/B/C 的九个 ON/OFF/TO 分支全部通过。该失败属于运行环境可见性，不是算法失败。
- **几何输入**：无效的 2.5–5 km 斜距窗被替换为物理可达窗口；无效版本不用于任何收益结论。
- **清理策略**：评价 JSON/CSV/PNG、case/run manifests、XML、日志、Stage2 报告、CSI/CFAR/航迹诊断保留。四个指定 case 的逐 case `raw_cleanup.json` 共记录删除 `12,400,174,666` bytes 的可重建 `.bin`，剩余 `.bin` 数为 0；清理只作用于这些 case 根目录，错误日志与配置保留。

## 复现入口

在可见 CUDA 的环境、相同基线/配置下：

```bash
python3 scripts/phase_r/run_r3_minimal_cuda_causal_pilot.py \
  --output-root outputs/phase_r_minimal_cuda_causal_pilot_20261021 --seed 101
python3 scripts/phase_r/run_r3_minimal_cuda_causal_pilot.py \
  --abc-extension-output-root outputs/phase_r_minimal_cuda_causal_pilot_20261021/replication_seed_202_cuda --seed 202
python3 scripts/phase_r/run_r3_minimal_cuda_causal_pilot.py \
  --abc-extension-output-root outputs/phase_r_minimal_cuda_causal_pilot_20261021/replication_seed_303_cuda --seed 303
python3 scripts/phase_r/evaluate_r3_minimal_cuda_causal_pilot.py \
  --case-root outputs/phase_r_minimal_cuda_causal_pilot_20261021/cases/base__seed_101__v_p6d7__snr_p30__delay_p4ns \
  --case-root outputs/phase_r_minimal_cuda_causal_pilot_20261021/replication_seed_202_cuda/cases/base__seed_202__v_p6d7__snr_p30__delay_p4ns \
  --case-root outputs/phase_r_minimal_cuda_causal_pilot_20261021/replication_seed_303_cuda/cases/base__seed_303__v_p6d7__snr_p30__delay_p4ns \
  --output-dir docs/research/phase_r/evidence/r3_minimal_cuda_causal_pilot_20261021
```

Evaluation artifacts: [`evaluation JSON`](evidence/r3_minimal_cuda_causal_pilot_20261021/r3_minimal_cuda_causal_evaluation.json)、[`cross-seed CSV`](evidence/r3_minimal_cuda_causal_pilot_20261021/r3_cross_seed_condition_metrics.csv)、[`figure`](evidence/r3_minimal_cuda_causal_pilot_20261021/r3_cross_seed_comparison.png)。

## Phase R4：E1/E4/E5 seed-101 实际 pilot

本节是对 R3 报告的独立追加，不改写上面的 +4 ns 因果结果。设计冻结见 [05 实验设计](05_experiment_design.md) 的 Phase R4 节；本次使用 0 ns 通道误差，不能用来更新 R3 的未知误差/已知校正效应。

### 结论与状态

seed 101 的 8 个速度输入 × SNR case 均在真实 CUDA Production 上执行；120/120 Production 分支通过，Stage2 连续面积地杂波在每个 case 均有效。但完整性能门没有通过：低速 case 的 P5 背景 ROI 无有效单元，valid-CUT 的跨分支唯一命中集合不可评估，且仅有一个 H0 seed/三个相关 period，无法完成检测级虚警与统计校准 Pfa。分支级 target-free H0 false-CUT fraction 可计算。原 residual-closure evaluator 要求本次未运行的 PK/PKR，但 R4 为零时延，物理残余闭合对此不适用；该原始 evaluator 状态不作为失败门。本轮只形成正确性与可行性证据，不扩展 seed 202/303，不声称 Pd/Pfa 或整体性能收益已验证。

| 标签 | 状态 | 证据边界 |
|---|---|---|
| `IMPLEMENTED` | 是 | runner 支持方法/模式子集和 Production 前面积杂波 fail-closed gate；P5 使用 period-specific truth/tap 对齐。 |
| `EXECUTED` | 是 | seed 101、0 ns、4 个配置地速 × 2 个 SNR、C0–C3/Mode-A；8 case × 15 个真实 CUDA Production 分支。 |
| `EVALUABLE` | 部分 | 面积杂波注入、ideal/none triplet、Production 命中与有限的 TrackManager 参数记录、逐分支 CFAR CUT/hit 计数及 target-free H0 false-CUT fraction、有效 P5 行。检测级虚警/统计校准 Pfa、低速 P5 背景指标和 radial-velocity RMSE 不可评估。 |
| `PERFORMANCE_VERIFIED` | 否 | 单 seed；低速 P5 背景与跨分支/检测级 CFAR 评价未闭合，且没有统计性 Pd/Pfa。R4 零时延物理残余闭合为 `NOT_APPLICABLE`，不计作失败。 |

### 场景、径向真值与真实地杂波

配置的 `target_velocity_mps` 是地速输入，不是径向速度。下表的 `v_r` 是从每期 `moving_target_truth.csv` 的 `vr_self_mps` 按 period 求均值后再汇总；同一地速的两个 SNR 配对使用相同运动真值。

| 配置地速输入 (m/s) | Stage2 径向真值：3 期均值 (m/s) | 3 期均值范围 (m/s) |
|---:|---:|---:|
| 0.5 | 0.48861 | 0.48829–0.48893 |
| 1 | 0.97722 | 0.97658–0.97787 |
| 2 | 1.95445 | 1.95317–1.95573 |
| 4 | 3.90893 | 3.90640–3.91146 |

所有 8 case 使用 6,000 m 平台、0 m 地面高程和可达斜距窗 `[84,500, 85,500] m`（对应地距 `[84,286.713, 85,289.214] m`）。每个 case 的 Stage2 报告均为 `continuous_area_packets=390`、`continuous_area_samples=1,952,655`、实测面积功率 `0.02233528`、lag-1 相关 `0.98000653`；数值跨 case 相同，面积门全部通过。OFF/ON/TO 的 seed、背景身份、目标身份和 Production identity 由 case manifest 配对记录，C1–C3 使用 Mode-A 的 OFF reference。

配对输入的路径、字节数和 SHA-256 保存在 case manifests 与紧凑 manifest 中，但清理后 72 个已记录的 Stage2 protocol-IQ `.bin` 输入文件均不在目录中。因此 F1/F2 原始输入的数值功率无法从保留文件独立重算，报告不宣称其数值功率完全相等；目前可审查的是配对身份/哈希、Stage2 实测杂波功率、CSI tap 与下游结果。该保留边界也写入 [manifest](evidence/r4_e1_e4_e5_seed101_20261022/manifest.json) 的 `input_retention`。

### Production、CFAR 与 E5 记录

- 每个 case 的 15/15 Production 分支均通过，实际 runtime XML、production log、逐 period stage 记录、CSI tap、CFAR geometry、检测和 track 审计仍保存在对应本地 case 根目录；关键路径与源码/二进制身份列于 manifest。
- 40 个 ON 分支（5 个方法/基线 × 8 case）各有 3 个可见目标机会，合计命中 120/120。每分支只有 3 个相邻 period；这不是统计性 Pd。TrackManager 的 `track_pd_all_visible` 均为 2/3，`track_pd_after_confirmation` 均为 1.0。Production detection record 中 unmatched 行占比为 90.93%–98.42%（unmatched rows / all detection rows），不是 CFAR Pfa。
- 40 个 ON 分支均有有限的 truth-matched angle、track ground-speed 和 position RMSE；范围分别为 `0.0104–0.2674°`、`2.423–20.649 m/s` 和 `16.22–419.47 m`。这些是跨不同速度/SNR/方法的描述范围，不作方法间因果优劣排序。Radial-velocity RMSE 未出现在 Production branch metrics 中，状态为 `NOT_REPORTED`，不以地速误差代替。
- **H0 的地杂波与目标自由来源**：紧凑 manifest 的 `area_clutter_gate` 引用 A0_ON 的 Stage2 报告，8 个 case 均记录 `390` 个连续面积包、`1,952,655` 个样本；A0_ON 将 `A0_background` 设为 paired-background 输出。Stage2 生成器在目标注入前冻结 C+N packet 并写入该 sidecar（`simulate_stage2_main.cpp` 的 `target_background_packet` / `background_out.write` 路径）；A0_OFF 的运行配置关闭目标，并从同一 `A0_background` 目录复用输入。因此 A0_OFF 是本实验的 target-free H0。A0_OFF 报告中的 `continuous_area_packets=0` 是因为该次运行复用背景而未二次生成，且仍写入 390 个 packet；不能单独据此判定 H0 无面积杂波。共享背景的 `scenario_resolved.json` 仍保留源运行的 `target.enabled=true`，这是元数据歧义；实际 sidecar 的目标自由属性由 `empty_background_c_plus_n` 角色、A0_OFF 关闭目标、以及目标注入前复制的源码路径共同支持。原始 protocol-IQ 已按清理记录移除，故当前不能再对其内容做数值重放。
- **H0 的 CFAR 命中**：两条 detector branch 各有 `1,581,840` valid CUT（3 个相邻 period 合计）。因为上述 H0 无目标，`union_dynamic_in_band` 的 `47,356 / 1,581,840 = 2.9937%` 与 `union_full_band` 的 `51,275 / 1,581,840 = 3.2415%` 可报告为本次 **target-free H0 false-CUT fraction**（分支级 observed hits/valid CUTs）。它不是聚类/检测级虚警率，也不是经统计校准的 empirical Pfa；每分支仅一个 seed、3 个相关 period，且跨 branch 只留 hit-index hashes，不能计算唯一 cell union。八个速度/SNR 汇总行在每个 branch 上具有完全相同的 hit 数与 hash 签名，不能视作八个独立 H0 复验。聚合 Pfa/跨 branch union 仍为 `NOT_EVALUABLE`。

ideal 与 none causal triplet 每 case 均为 `18,439,200` samples，最大绝对误差范围 `1.2102e-6–1.3278e-6`，低于冻结容差 `5e-5`。原 evaluator 对本次冻结子集仍输出 `NOT_EVALUABLE (residual_branch_missing_or_ambiguous:PK)`；但 R4 是零时延条件，A0/PK/PKR 的物理校正残差闭合对这组 E1/E4/E5 **`NOT_APPLICABLE`**，不是一项通过，也不应作为性能失败门。保留原始 evaluator 状态供审计，且不改评价器代码；R3 的非零时延比较仍须分别检查 A0↔PK 物理闭合及 PK↔PKR 的额外残差抑制。

### P5 覆盖与可评估的 v_g=4 m/s ON 指标

每个 case 有 30 条 period-specific P5 报告（方法 × ON/TO × 3 period）。背景 ROI / target metric 有效记录数如下；“incomplete”保持原评价结果，不用零填补，也未调低有效单元门槛。

| 配置地速 (m/s) | SNR (dB) | 背景 ROI 有效 / 30 | 目标指标有效 / 30 | P5 状态 |
|---:|---:|---:|---:|---|
| 0.5 | 10 | 0 | 15 | 30 incomplete |
| 0.5 | 30 | 0 | 15 | 30 incomplete |
| 1 | 10 | 0 | 15 | 30 incomplete |
| 1 | 30 | 0 | 15 | 30 incomplete |
| 2 | 10 | 20 | 30 | 20 ok, 10 incomplete |
| 2 | 30 | 20 | 30 | 20 ok, 10 incomplete |
| 4 | 10 | 30 | 30 | 30 ok |
| 4 | 30 | 30 | 30 | 30 ok |

0.5/1 m/s 的背景 ROI 与活动杂波 Doppler 支持不相交（目标中心行落在 ROI 外，活动支持约为 row 46–84），故背景有效数为 0；这是评价几何覆盖失败，不是把质量门槛调严后产生的算法退化。2 m/s 仍有 10 条报告不完整。完整逐 period 行在 [`p5_period_summary.csv`](evidence/r4_e1_e4_e5_seed101_20261022/p5_period_summary.csv)。

只有配置地速 4 m/s 的两个 SNR 均有 30/30 背景和目标记录可评价。指标方向：`SCNR improvement = after − before`，负值表示输出 SCNR 下降；`CA_ROI = 10 log10(before power / after power)`，负值表示背景 ROI 功率处理后增加；`target_loss = -target_gain`，负值表示目标功率轻微增加。

| 方法 | SNR (dB) | SCNR change (dB) | CA_ROI (dB) | target_loss (dB) |
|---|---:|---:|---:|---:|
| C0 | 10 | -4.148 | -1.091 | -0.086 |
| C1 | 10 | -5.763 | -1.622 | -0.086 |
| C2 | 10 | -3.742 | -1.150 | -0.086 |
| C3 | 10 | -5.563 | -1.554 | -0.092 |
| C0 | 30 | -4.148 | -1.054 | -0.085 |
| C1 | 30 | -5.763 | -1.609 | -0.085 |
| C2 | 30 | -3.742 | -1.124 | -0.085 |
| C3 | 30 | -5.550 | -1.579 | -0.105 |

C2 的 SCNR 下降幅度在这一个 seed 的 v_g=4 m/s case 中小于 C0/C1/C3；所有方法的 SCNR change 和 CA_ROI 仍为负值。三个 period 属于同一个 seed，故这里只是描述性结果，不能说 C2 有显著或可泛化收益。概览图见 [`pilot_metric_coverage.png`](evidence/r4_e1_e4_e5_seed101_20261022/pilot_metric_coverage.png)；完整 branch/P5/CFAR 表分别见 [`production_branch_summary.csv`](evidence/r4_e1_e4_e5_seed101_20261022/production_branch_summary.csv)、[`p5_method_role_summary.csv`](evidence/r4_e1_e4_e5_seed101_20261022/p5_method_role_summary.csv) 和 [`cfar_off_branch_summary.csv`](evidence/r4_e1_e4_e5_seed101_20261022/cfar_off_branch_summary.csv)。

### 源码身份、失败现场与复现

- Production source commit：`69475e3db0c1bd012f247094daef461d15905fff`，run manifest 记录 `dirty=false`；GPU 为 NVIDIA RTX 3050 Laptop、driver `580.178.04`、CUDA `13.0`，有效运行 `skip_cuda=false`。
- Production runner SHA-256：`dfab0581d23b6b66576a7af9aeb5a6e58223b8a1c4278d6376ee51bfe5b39093`；P5 evaluator SHA-256：`3f3596f6d4df7b850e41ffbdc15e3219bb5f3d344bf3701d55d426e66d79e1cb`；配置 SHA-256：`6c38544849d842ff79903f7489b2907fb7bce615b36413cf9a8e5e89645b71e8`。Stage2/Production binary hashes 与实际命令在 manifest/run-root manifests 中。
- maps 根 `manifest.json` 因 ENOSPC 截断，原文件保留；紧凑 manifest 从有效 JSON 前缀恢复了该 root 的 command、GPU probe、build、config 和 runner 身份。无 case manifest 的 partial v=2/SNR10 目录、初次失败根和重复 final 根均排除在八 case 汇总外，仍保留作审计。
- 本轮未运行 seed 202/303、未训练 AI。下一步须先由用户决定是否修正 evaluator 的分支契约及低速 P5 ROI 覆盖；只有 seed101 关键 gate 全通过后才考虑增加独立 seed。

原 Production 逐 root 命令和输入 identities 见 [`manifest.json`](evidence/r4_e1_e4_e5_seed101_20261022/manifest.json)。重新汇总（写到一个全新目录，不覆盖现有证据）的入口：

```bash
python3 scripts/phase_r/aggregate_r4_evidence.py --output-dir docs/research/phase_r/evidence/r4_e1_e4_e5_seed101_20261022_rebuilt
```
