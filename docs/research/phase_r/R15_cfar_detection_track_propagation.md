# R15 CFAR → Detection → TrackManager 传播审查

## 数量变化

R14 seed505 的三期 C2/C3 ON final-union CFAR maps 均已由 geometry、map hash 和 Production log 交叉核验。C3 hit-cell 更多，但选出的 Detection 更少：

| period | C2 unique hit cells | C2 selected components / detections | C3 unique hit cells | C3 selected components / detections |
|---:|---:|---:|---:|---:|
| 0 | 35,563 | 276 | 45,009 | 135 |
| 1 | 31,984 | 343 | 44,217 | 318 |
| 2 | 29,608 | 371 | 61,861 | 248 |
| **合计** | **97,155** | **990** | **151,087** | **701** |

因此 C3 的 unique hit cells 增加 53,932（+55.5%），selected components / Detections 减少 289（−29.2%）。每个 selected Detection 对应的 hit-cell 数比值为 C2 `97,155/990 = 98.1`、C3 `151,087/701 = 215.5`。R14 evaluator 报告的非目标 Detection rows 分别是 987 与 698；每个方法另有三期正确目标候选。

## 生产路径中的压缩步骤

CFAR hit-cell 不是一条最终 Detection。Production 在 CFAR 后进入 `cluster_filter_gap_phase`：按 Doppler 邻接及 range 间断聚成连通分量；要求至少 `min_points=3`；配置若省略相应 XML 字段，则当前源默认 `cluster_max_range_gap=2`、`cluster_max_phase_std_rad=0.2`，满足相位圆标准差条件后，每个保留分量只输出功率最大的一个代表 cell。实现见 [detectAndLocating.cpp](../../../src/detectAndLocating.cpp#L386)；Production 调用和 CFAR summary 见 [processOnePeriod.cpp](../../../src/processOnePeriod.cpp#L3223) 及 [processOnePeriod.cpp](../../../src/processOnePeriod.cpp#L3331)。

R14 runtime XML 两法均配置 `csi_detection_band_mode=union`、`min_points=3`。每期 log 中 `clusters == selected`；C2/C3 合计候选数正好分别是 990/701。二次 small-cluster 记录 `small_rejected=0`、`strong_small_kept=0`，Fusion/loc 的每期 `drop_sin/drop_px/drop_gate=0`、`n_output` 等于输入候选数。因此数量差在 CFAR hit 到组件代表 Detection 的步骤已经出现，并非 Fusion location gate 再削掉了 C3 候选。

C3 的 hit-cell 变多并不会迫使 Detection 数变多：多个相邻或允许 gap 的正 hit 可以并入同一连通分量，而组件只产生一个 peak candidate。R14 数字与这一生产机制相符。由于本轮没有保存全量 final-union `.f32` maps，不能把 289 条差值精确拆成 gap merging、phase rejection 或组件大小分布的贡献；这些子项不应被反推成已测得的事实。

## 对 TrackManager 的传播

C2/C3 处理的是相同 seed、几何和 target cell；p2 的正确目标候选 D0 都在 `(row=47, col=2261)`，坐标相同。p2 的相邻非目标 D3 `(row=2, col=2260)` 也存在于两方法，坐标约 `(429602.1162, 4425903.2378)`。差别在于 p0/p1 其余 Detection 的数量和位置：C3 的 p0 track 7 与 track 64 初始状态、p1 track 7/64 接受的候选均与 C2 不同。到 p2，C2 的 track 7 仍可在 tentative 放宽 gate 内接受 D3；C3 的 track 7 已在远处，超过 Euclidean gate。

最终 C2 分配 D3 给 track 7、把 D0 留给 track 6；C3 先把 D3 分给 track 6，随后把 D0 分给 track 64。关联详细表和 Hungarian 反事实见 [R15 航迹分歧分析](R15_track_association_divergence_analysis.md) 与 [证据包 CSV](evidence/r15_seed505_track_association/period2_candidate_costs.csv)。

## 可支持与不可支持的说法

- 可支持：本 seed 中 C3 的 CFAR unique hit cells 更多、聚类后 Detection 更少；较少候选改变了多期 track 初始化和关联边竞争；末期正确目标速度记录受到 track 历史影响。
- 不可支持：Detection 数减少证明了 CFAR Pfa 降低；C3 的 698 条非目标 Detection 等于统计虚警率；全部数量差只由某个单一 cluster 参数造成；该效应跨场景稳定；CSI 复权本身错误地估计了目标速度。
- 经验 Pfa 在 R14 是 `NOT_EVALUABLE`；单 seed 数字是描述计数。

R15 未调 CFAR、cluster、truth match 或 TrackManager 门限；不做新的 CUDA Production 运行。
