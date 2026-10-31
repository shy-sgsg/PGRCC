# R15 航迹关联分歧与地速误差归因

日期：2026-10-25。审查基线：`2b1942453b002045aaba4c1e35a8b93e75d0c38b`。范围仅为 R14 seed505、0 ns 通道时延、beam 50 的已保存 C2/C3 ON 检测和 TrackManager 记录，以及固定检测输入的 CPU 离线关联反事实。

## 结论

C3 末期目标 track ID 从 C2 的 6 变为 64，直接发生在 **检测候选集合不同之后的普通 GreedyNearestNeighbor 一对一分配**。较大的速度误差来自被目标候选继承的 TrackManager 测量历史；没有证据表明 CSI 复权本身直接错误估计了目标速度。

R14 目标匹配的 D0 检测在 C2/C3 三期物理坐标完全相同。最早的航迹状态差异来自第 0 期其他非目标候选位置不同，令 track 7 和 track 64 的历史不同；第 2 期目标 D0 与相邻非目标 D3 竞争时，贪心代价顺序把 C3 的 D0 分给了已有 track 64。

## 逐期 lineage

| period | 正确目标候选 D0 | C2 关联 | C3 关联 | 解释 |
|---:|---|---|---|---|
| 0 | `(429669.1166, 4425945.0438)` | 新建 track 1，Tentative | 新建 track 1，Tentative | D0 自身没有形成确认输出。两方法都另以 det 5 `(429686.5525, 4425926.7887)` 新建 track 6；它距 D0 约 25.2 m，是目标邻近候选，不是 truth-matched D0。 |
| 1 | `(429655.4084, 4425898.4734)` | D0 → track 6 | D0 → track 6 | 目标候选第一次被确认输出；截至此期目标 D0 的 track ID 一致。 |
| 2 | `(429586.5472, 4425918.8883)` | D0 → track 6 | D0 → track 64 | 目标候选坐标和 CFAR cell 相同，分配结果不同；track 6 的竞争关系受此前候选历史影响。 |

track ID 只在各自 Production 分支内有意义。C2 与 C3 的 track 64 不是同一物理轨迹：C2 从 p0 det 63 `(428939.1470, 4425220.0223)` 开始，C3 从 p0 det 63 `(429345.7200, 4425936.4192)` 开始，后者距当期目标候选约 323.5 m。ID 相同不能跨方法表示同一目标。

两方法的非目标 track 初始化和中间更新也不同。p0 的 det 6 在 C2 位于 `(429615.4048, 4425794.1868)`，在 C3 位于 `(429825.1511, 4425940.5989)`；C2 track 7 在 p1 漏配，C3 track 7 接收 det 30。p1 C3 track 64 接收 det 229 `(429443.5024, 4425933.1863)`，而 C2 track 64 接收远处 det 262 `(428999.4044, 4425080.5242)`。这些是不同候选位置造成的状态历史差异，不是相同 track 预测状态下只换了关联算法。

## p2 关联决策的门与代价

C2 与 C3 的运行时关联设置相同：Mahalanobis squared cost；confirmed Euclidean gate 300 m、Mahalanobis `d² ≤ 9.21`；tentative 放宽 1.5 倍；最大速度门 50 m/s；dummy cost 1.5；speed/heading 代价权重分别 0.3/0.5；输出位置源为 measurement。p2 中被接受的关键边均通过空间/速度门，总代价远小于 dummy cost。切换不是门限拒绝造成的。

| 分支 / track → detection | 欧氏距离 | Mahalanobis `d²` | cost | Greedy 结果 |
|---|---:|---:|---:|---|
| C2 track 7 → D3（tentative） | 109.858 m | 0.07947 | 0.005753 | 接受，先占用 D3 |
| C2 track 6 → D3 | 39.809 m | 0.12819 | 0.013919 | 可行但未分配，D3 已占用 |
| C2 track 6 → D0（目标） | 61.621 m | 0.30715 | 0.033349 | 接受 |
| C3 track 6 → D3 | 39.809 m | 0.12819 | 0.013919 | 接受，先占用 D3 |
| C3 track 64 → D0（目标） | 46.595 m | 0.17562 | 0.019068 | 接受 |
| C3 track 6 → D0（目标） | 61.621 m | 0.30715 | 0.033349 | 可行但未分配，track 6 已占用 |

Greedy 实现在 [TrackManager.cpp](../../../src/TrackManager.cpp#L1489) 将所有低于 dummy 的有效边按 cost 升序全局排序，再作一对一分配。C2 的低代价 track 7 把 D3 留给非目标轨迹，目标 D0 随后给 track 6；C3 的 track 7 对 D0/D3 均超过 300 m gate，track 6 先取 D3，随后 D0 给 track 64。D3 是两方法共有的 p2 邻近非目标 candidate，坐标约 `(429602.1162, 4425903.2378)`，距 D0 约 22.1 m。

## 为什么这影响地速

[TrackManager.cpp](../../../src/TrackManager.cpp#L1134) 的 `updateMotionHistoryFromEN` 在每次匹配时，用当前检测与保留历史中最早测量点的 EN 位移除以时间差更新速度；它不直接输出本期 detector 的速度字段。C2 的正确目标 D0 末期仍在 track 6，其历史由 p0 的邻近 det 5、p1/p2 的目标 D0 组成；C3 的目标 D0 末期落到 track 64，其历史由 p0 非目标 det 63、p1 非目标 det 229、p2 目标 D0 组成。

| p2 正确目标机会 | p2 输出 track | p2 TrackManager 地速 | 对 6.7 m/s 真值的绝对误差 |
|---|---:|---:|---:|
| C2 Production Greedy | 6 | 8.223 m/s | 1.523 m/s |
| C3 Production Greedy | 64 | 19.792 m/s | 13.092 m/s |

R14 三期共同目标机会的 ground-speed RMSE 为 C2 1.086 m/s、C3 9.259 m/s，`n=2`。这是一个 seed 的描述性差异；不是统计显著性或总体 Pd/Pfa 结论。R14 的位置、方位和 folded Doppler 共同机会相同，radial velocity 仍为 `NOT_EVALUABLE`。

## 固定输入的 Hungarian 反事实

R15 将 R14 `track_detections.csv` 的同一批检测、顺序、时间、坐标和速度字段固定，分别调用当前 Production `TrackManager` 的 Greedy 与 Hungarian。输入不含真值或跨方法候选信息。Greedy 对照逐条复现 R14 Production：C2 `990/990`、C3 `701/701` detection-to-track edges 相同。

| 方法 | 关联方式 | D0 的 p0 → p1 → p2 track ID | p2 速度 | p2 绝对误差 | 与 Production Greedy 不同的 detection-track edge |
|---|---|---|---:|---:|---:|
| C2 | Greedy | 1 → 6 → 6 | 8.223 m/s | 1.523 m/s | 0/990 |
| C2 | Hungarian | 1 → 6 → 13 | 12.838 m/s | 6.138 m/s | 582/990 |
| C3 | Greedy | 1 → 6 → 64 | 19.792 m/s | 13.092 m/s | 0/701 |
| C3 | Hungarian | 1 → 4 → 13 | 12.089 m/s | 5.389 m/s | 483/701 |

Hungarian 在 C3 这个 seed 降低了 p2 的目标地速误差，但仍有 p1→p2 track ID 切换；它在 C2 则使误差增大。Hungarian 也让逐期 confirmed current outputs 数从 C2 462 增至 503、C3 285 增至 312；这是计数变化，不等同于正确航迹收益。该反事实说明关联策略会影响结果，但不支持 Hungarian 是通用修复，也不支持门限调优。

## 机制边界与判定

当前证据支持这条局部传播链：

```text
CSI/CFAR 输出的候选集合和候选位置
  → 非目标检测初始化出不同的历史轨迹
  → 同一目标邻域内有效关联边竞争
  → Greedy 一对一分配选择不同历史轨迹
  → TrackManager 历史 EN 速度不同
```

R14 能证明 C2/C3 在本 seed 中的 detector 候选集合不同，并能把地速差追到 TrackManager 的历史与分配；它不能证明这条机制跨 seed 稳定，也不能将地速差直接归因于 CSI 复权估计。R14 的检测结果表明 C3 仍有更高目标-only CSI retention，但任务层出现了地速代价；中间量收益不保证轨迹连续性。

没有重新运行 CUDA：候选代价矩阵由固定检测输入上的 CPU TrackManager replay 取得，现有信息足以解释本次接受与拒绝。完整复现身份和压缩代价表见 [R15 证据包](evidence/r15_seed505_track_association/README.md)。
