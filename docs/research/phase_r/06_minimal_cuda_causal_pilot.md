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
