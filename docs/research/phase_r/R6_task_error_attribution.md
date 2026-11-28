# R6：任务误差与目标关联归因

## R3 seed101 历史指标复现

使用生产检测快照、正式 TrackManager debug 记录、`audit_debug_dir` 和 `run_track_manager_e2e._evaluate_branch` 复评；全部 6 条 A0/C 分支审计均 `pass`、0 violations。truth association gate 按冻结 evaluator：位置误差不超过 1,000 m，且 range error 不超过 150 m。

| 条件 | 目标检测机会命中 | 原始 detection rows | 与目标匹配的 detection rows | eligible/matched track payloads | matched periods | ID switches | angle RMSE | ground-speed RMSE | position RMSE |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| A0 ON | 3/3 | 502 | 26 | 192/16 | 1,2 | 1 | 0.0708492° | 6.93165 m/s | 114.892 m |
| C ON | 3/3 | 477 | 25 | 170/16 | 1,2 | 1 | 0.0105973° | 1.27918 m/s | 16.7277 m |
| A0 OFF | 3/3 | 362 | 16 | 149/8 | 1,2 | 1 | 0.175193° | 20.4182 m/s | 283.845 m |
| C OFF | 3/3 | 345 | 16 | 137/8 | 1,2 | 1 | 0.288991° | 15.2742 m/s | 448.750 m |
| A0 TO | 3/3 | 700 | 25 | 327/16 | 1,2 | 0 | 0.0260372° | 3.20627 m/s | 40.1813 m |
| C TO | 3/3 | 709 | 25 | 338/16 | 1,2 | 0 | 0.0260368° | 3.20623 m/s | 40.1806 m |

ON 的三项 R6 数字与 R3 精确一致。A0/A0 两次重复、CSI/CFAR 诊断开关 control 的 evaluator 数字也逐项一致；因此复现不是一次性的 TrackManager 随机波动。

这些结果是冻结 evaluator 定义下的指标，不代表三 period 的统计性 Pd/Pfa。`3/3` 只表示每个可见 truth period 至少有一个 Production detection row 落在上述宽 association gate；它不等于 3 个独立 Pd 样本，更不等于 CFAR hit-cell Pd。

逐周期 cluster→selected→detection 计数也可直接从 Production 日志与 `GMTI01/02/03` 检测 CSV 复核：三者逐周期相等。A0/C cluster 与 detection 数分别为 ON `140/130,133/127,229/220`，OFF `105/110,146/131,111/104`，TO `178/178,221/229,301/302`（A0/C、period 0/1/2）；A0_ON 的四个重复/插桩控制逐期完全相同。此为计数级 lineage，不包含可持续追踪的 cluster ID；不可据此补造特定目标候选的 cluster 身份。

## RMSE 差值由一个弱关联 payload 主导

A0 ON 的 16 个 matched protocol payload 来自 periods 1、2，且 evaluator 对匹配 gate 内的**所有**输出 payload 计算 RMSE，不只取每 period 的最佳目标航迹。最大 A0 payload 为 period 2、track 30、matched detection 68：

- detection cell `(row=119,col=2201)`，range `88,301.39 m`，bearing `-141.9162°`，CFAR margin `1.1551 dB`；
- TrackManager payload position error `454.686 m`、range error `149.070 m`，刚好未越过 150 m gate；输出地速 `34.0396 m/s`，地速误差 `27.3396 m/s`；该 detection 的角度误差 `0.2802°`。
- 只作为诊断而排除此一个 payload，A0 ON 三指标 RMSE 变为 angle `0.01094°`、ground speed `1.19187 m/s`、position `17.2502 m`，接近 C ON 的 `0.01060°`、`1.27918 m/s`、`16.7277 m`。这是 post-hoc sensitivity check，不替代官方结果。

该 cell 在 A0 和 C 的 dynamic CFAR **都命中**：A0 power/threshold `171.1728/131.1983`，C 为 `171.1662/149.5660`。但 A0 最终检测列表包含该 cell，C 列表不包含；邻域 hit topology 不同，A0 另有 `col=2202` 命中。两边共同的邻近 detection `(119,2195)` 仍存在。TrackManager 因而在 A0 输出额外的近门限、近距离边界 track30 payload；C 没有这条误关联输出。

这把变化定位到 **CFAR hit pattern → cluster/candidate selection → TrackManager 多航迹关联**，而不是已确认的目标真值缺测或一次不同的速度模糊解。日志没有持久化 cluster ID 的逐候选 lineage，所以具体是哪条 split/cluster 规则造成 cell 被最终 detection list 抑制仍为 `INCONCLUSIVE`。R6 不把 hit flip 数直接称为虚警数。

## 如何解释 A0/C

- ON：Known correction 的冻结任务 RMSE更低，但位置/速度/角度优势主要来自 A0 的单条边界 payload；共同主目标附近的 detection 参数接近。
- OFF：C 的 angle/position RMSE反而更差，地速更低；TO：A0/C 几乎一致。因此不能把 ON 单例的改善外推为三类任务场景上的普遍收益。
- 生产 CFAR 有大量非正门限；它们会影响 cluster 输入与候选集合。当前一 seed 结果不足以证明已知校正带来可靠任务增益，更不足以证明盲估计或 AI 有必要。
- `Pfa` 与统计性 false-alarm 不可评估。`raw detections - matched detections` 不能当成 Pfa。

机器可读结果：[r6_task_metrics.csv](evidence/r6_stagewise_divergence_20261022/r6_task_metrics.csv)。
