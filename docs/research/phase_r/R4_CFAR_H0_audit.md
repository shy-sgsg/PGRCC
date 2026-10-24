# R4 target-free H0 CFAR 分层审计

日期：2026-10-22。复核现存 seed-101 R4 几何行、聚合 CSV、GO-CFAR 源码与 Stage2 报告；不重跑 CUDA。

## 输入与阈值

- 地杂波 gate 通过：每 case `continuous_area_packets=390`、`continuous_area_samples=1,952,655`、面积均值功率 `0.0223352779188`、lag-1 相关 `ρ=0.980006535`。这只证明本次地杂波输入真实存在，不证明 IID。
- H0 为目标关闭的配对背景角色；A0_OFF 使用 target-free C+N sidecar。八个速度/SNR case 的两 branch、三 period hit-hash 序列完全重复，指向**一个被复用的 H0 realization**，不是八次独立 H0 复验。
- 几何为 `H=130, W=4096, guard=4, background=16, Doppler circular=true`。总 cells `532,480`，range edge-invalid `5,200`，每 branch/period `valid CUT=527,280`。三 period 合计每 branch `1,581,840`。`cut_band_mode=0` 表示每 branch 对全部有效 CUT 作阈值测试；dynamic branch 的输入是活动带 `[46,84]` 内 CSI、带外 F2 混合图，full branch 是全行 CSI。故两 branch 使用全图 valid-CUT 分母与当前生产执行定义一致，不能改用只数 39 个动态行的分母。
- 两者均是 GO-CFAR、配置 `Pfa=9.99999997e−7`、记录 `α=13.44782257`。源码用 262,144 个确定性伪随机样本，对四个相关方向训练带均值的最大值校准 `E[exp(−α·max directional mean)] = Pfa`，再作 48 次二分；这是 IID 指数背景的配置阈值校准，不是本次实测 Pfa。

## H0 分支级结果

| Detector branch | Hits / valid CUT | observed branch hit fraction | 解释 |
|---|---:|---:|---|
| `union_dynamic_in_band` | 47,356 / 1,581,840 | 2.9937288% | target-free H0 的 branch-specific false-CUT fraction |
| `union_full_band` | 51,275 / 1,581,840 | 3.2414783% | 同上 |

这些分数远高于配置值，但输入 lag-1 相关约 0.98，且只复用单一背景 realization；可说“当前 H0 的观测超阈比例高”，不能据此直接声称已测得系统 Pfa、显著性或普遍失配。代码明确对 union 两 branch 分别运行全有效网格，最终逐格取 hits max；branch hits 不能相加。

## 虚警证据的层级

| 层 | 当前可得量 | 状态 | 缺口/边界 |
|---|---|---|---|
| 配置阈值 | GO 类型、Pfa 参数、α、guard/background 几何 | `EVALUABLE`（配置） | IID 校准假设不等于 correlated clutter 下 empirical Pfa |
| CFAR CUT | 每 branch hits/valid CUTs 与 hit-index hash | `EVALUABLE`（分支描述） | hashes 不是 indices，不能算两 branch 唯一 hit union；period 是同一 realization 的相关 CPI |
| cluster/detection | production detection CSV 和计数存在 | `NOT_EVALUABLE`（虚警率） | 诊断行 `cluster_count=0` 不可当作最终 cluster 数；汇总标记 `period_association_proxy_no_cluster_id`，缺可去重 cluster 身份与可靠 H0 false-detection join |
| TrackManager / PIPE | 有 track/debug integrity 记录 | `NOT_EVALUABLE`（false-track/output rate） | `track_audit_status=pass` 只代表审计约束无违规，不等于零虚轨或已校准 false-track 率；没有独立 H0 曝光/时间单位统计 |

因此可保留的唯一量化虚警结论是两项 branch-specific target-free H0 false-CUT fraction；cell-union、cluster/detection-level、track/PIPE false alarm 与 calibrated Pfa 均不可评估。

证据：[`cfar_off_branch_summary.csv`](evidence/r4_e1_e4_e5_seed101_20261022/cfar_off_branch_summary.csv)、[`case_summary.csv`](evidence/r4_e1_e4_e5_seed101_20261022/case_summary.csv)、[`aggregate_r4_evidence.py`](../../../scripts/phase_r/aggregate_r4_evidence.py)、[`go_cfar_alpha.hpp`](../../../include/go_cfar_alpha.hpp)。
