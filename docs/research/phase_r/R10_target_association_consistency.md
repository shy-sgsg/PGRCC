# R10：目标候选与航迹关联一致性

日期：2026-10-23
基线：86574881277692f052fb5470da6c83f3a2da5752
范围：只读复核 R8/R9 已有 Native CUDA Production 和评价结果；没有重新生成 Stage2、没有新增 CUDA 运行。

## 状态

- IMPLEMENTED：本报告将已有 lineage、检测、TrackManager 与 truth 记录按 seed/period/condition 对齐。
- EXECUTED：表中数值来自 R8/R9 已执行的 CUDA Production；本阶段新执行的是离线读取和归因。
- EVALUABLE：候选位置、冻结 evaluator 正确匹配、cluster/detection lineage、TrackManager 状态可评估。
- PERFORMANCE_VERIFIED：只对 seed202/303 的六个目标机会和本次处理链成立；不是统计显著性结论。

## 条件映射与证据路径

| 报告名称 | 机器条件 | 含义 |
|---|---|---|
| A0 | R8 A | 无新增通道误差的生产基线 |
| Known | R8 C | 已知物理参数校正；真实参数仅供 evaluator |
| 固定 D3 | R9 Static | seed101 target-free 标定值 3.409965 ns |
| 在线 D3 | R9 Online | ON 输入上的确定性盲估计候选 |

紧凑证据：[R8 证据包](evidence/r8_unknown_delay_multiseed_20261023/) 的 seed/condition task metrics 与关联记录；[R9 target lineage](evidence/r9_deterministic_delay_estimator_20261023/records/evaluation/target_lineage_audit.csv)、[任务指标](evidence/r9_deterministic_delay_estimator_20261023/records/evaluation/task_metrics_comparison.csv)。完整 R8/R9 track debug 与生产 detection CSV 留在本机忽略目录 outputs/phase_r8_unknown_delay_multiseed_20261023/ 和 outputs/phase_r_r9_deterministic_delay_20261023/。R9 紧凑包的[导航说明](evidence/r9_deterministic_delay_estimator_20261023/README.md)列出了 manifest、runtime XML、beam log、CSI tap、CFAR geometry 和本地 track-debug 路径。

表中 Δrow、Δcol 均为 detection cell 减 truth cell；cluster 与 TrackManager ID 是各次运行本地编号，不可跨条件按编号解释为同一轨迹。

## 每个正确检出 period 的候选和 TrackManager 记录

| Seed | Period | 方法 | Truth row/col | Detection row/col | Δrow/Δcol | Cluster / det ID | TrackManager ID / 状态 | CFAR margin (dB) |
|---:|---:|---|---|---|---|---|---|---:|
| 202 | 0 | A0 | 50/2048 | 49/2048 | −1/0 | 3/0 | 1 / Tentative | 6.95 |
| 202 | 0 | Known | 50/2048 | 49/2048 | −1/0 | 3/0 | 1 / Tentative | 6.95 |
| 202 | 0 | 固定 D3 | 50/2048 | 49/2048 | −1/0 | 3/0 | 1 / Tentative | 9.74 |
| 202 | 0 | 在线 D3 | 50/2048 | 49/2048 | −1/0 | 4/0 | 1 / Tentative | 10.07 |
| 202 | 2 | A0 | 46/2261 | 46/2261 | 0/0 | 1/0 | 7 / Confirmed | 14.77 |
| 202 | 2 | Known | 46/2261 | 46/2261 | 0/0 | 1/0 | 7 / Confirmed | 14.77 |
| 202 | 2 | 固定 D3 | 46/2261 | 46/2261 | 0/0 | 1/0 | 1 / Confirmed | 16.18 |
| 202 | 2 | 在线 D3 | 46/2261 | 46/2261 | 0/0 | 1/0 | 1 / Confirmed | 16.24 |
| 303 | 0 | A0 | 50/2048 | 50/2048 | 0/0 | 3/0 | 1 / Tentative | 9.69 |
| 303 | 0 | Known | 50/2048 | 50/2048 | 0/0 | 3/0 | 1 / Tentative | 9.69 |
| 303 | 0 | 固定 D3 | 50/2048 | 49/2048 | −1/0 | 3/0 | 1 / Tentative | 12.41 |
| 303 | 0 | 在线 D3 | 50/2048 | 49/2048 | −1/0 | 3/0 | 1 / Tentative | 13.53 |
| 303 | 1 | A0 | 48/2154 | 48/2154 | 0/0 | 0/0 | 7 / Confirmed | 13.33 |
| 303 | 1 | Known | 48/2154 | 48/2154 | 0/0 | 0/0 | 7 / Confirmed | 13.33 |
| 303 | 1 | 固定 D3 | 48/2154 | 48/2154 | 0/0 | 0/0 | 9 / Confirmed | 13.89 |
| 303 | 1 | 在线 D3 | 48/2154 | 48/2154 | 0/0 | 0/0 | 9 / Confirmed | 14.23 |
| 303 | 2 | A0 | 46/2261 | 47/2261 | +1/0 | 1/0 | 15 / Confirmed | 16.01 |
| 303 | 2 | Known | 46/2261 | 47/2261 | +1/0 | 1/0 | 15 / Confirmed | 16.01 |
| 303 | 2 | 固定 D3 | 46/2261 | 46/2261 | 0/0 | 1/0 | 16 / Confirmed | 18.18 |
| 303 | 2 | 在线 D3 | 46/2261 | 46/2261 | 0/0 | 1/0 | 15 / Confirmed | 18.91 |

## 漏检期

seed202 period 1 的 truth cell 为 row 48 / col 2154。A0、Known、固定 D3、在线 D3 均未通过冻结 evaluator 的目标关联门。四种条件都留下同一 target-like candidate：detection ID 2、row 44 / col 2155、cluster 1；相对 truth 为 Doppler row −4、range column +1。冻结 Doppler 门限为 ±2 bins，因此被 gate 排除。它不能计作正确目标 detection 或 track。

## 判读

1. 五个正确检出 period 中，A0 与 Known 的 cell 完全相同。固定 D3、在线 D3 与 A0 在 seed202 的两个正确期和 seed303 period 1 也选中同一 cell；seed303 period 0、2 各偏移一行 Doppler，但 range cell 不变。
2. 因而候选选择差异不能解释全部测角/定位退化：三个相同 cell 的 period 仍有很大的角度和位置误差差异。seed303 另外两个 period 的候选行偏移是额外混杂因素，不能把那两个点单独当作同 cell 的纯测量比较。
3. 四种方法都只在 3/6 个机会形成正确目标的 Confirmed Track：seed202 period 2，以及 seed303 period 1、2。period 0 虽然有正确 detection，但航迹仍为 Tentative。不同运行中的 Track ID 不同，不代表目标身份不同。
4. 固定 D3/在线 D3 的 CFAR margin 对这些正确候选均为正（6.95–18.91 dB）。候选存在且超过 CFAR 门限，并不等于定位精度或确认航迹性能恢复。

这组结果把“候选选错/漏检”和“同一候选的参数误差”分开。精确的逐期角度、位置和地速误差见 [R10 误差归因](R10_angle_position_error_attribution.md)。
