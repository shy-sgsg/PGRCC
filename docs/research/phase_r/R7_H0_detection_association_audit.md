# R7：H0 虚警与目标关联审计

日期：2026-10-22
数据：seed101、一个 period 的 CUDA Production 修复后输出。

## 判定口径

H0/OFF 无注入目标，只报告该运行中的 hit、cluster/detection 和 track 数，不报告目标 Pd 或参数 RMSE。ON 的 truth gate 在查看修复后结果前冻结为同 period、同 beam、range 与 circular Doppler 各 ±2 bins，单目标使用一对一候选匹配。这里的匹配仅表示检测行通过冻结门限；它不替代生产聚类/航迹确认，也不构成统计 Pd。

## 修复后 Production 输出

| 条件 | 模式 | CFAR union hits | cluster / raw detections | TrackManager 结果 |
|---|---|---:|---:|---|
| A0 | OFF | 129 | 11 / 11 | 11 tentative；0 confirmed |
| B (+4 ns) | OFF | 145 | 12 / 12 | 12 tentative；0 confirmed |
| C (known correction) | OFF | 129 | 11 / 11 | 11 tentative；0 confirmed |
| A0 | ON | 372 | 19 / 19 | 19 tentative；0 confirmed |
| B (+4 ns) | ON | 692 | 37 / 37 | 37 tentative；0 confirmed |
| C (known correction) | ON | 372 | 19 / 19 | 19 tentative；0 confirmed |
| A0 | TO | 1,781 | 166 / 166 | 166 tentative；0 confirmed |
| B (+4 ns) | TO | 2,267 | 157 / 157 | 157 tentative；0 confirmed |
| C (known correction) | TO | 1,781 | 166 / 166 | 166 tentative；0 confirmed |

ON 条件每个 run 都仍产生不少于 19 个 tentative track；所以 `hit`、raw detection 或 tentative track 不能记为已确认目标航迹。所有运行均为单 period，未产生 confirmed track 或协议输出。

## ON 目标候选

本场景 truth 的 range bin 为 2048、Doppler row 为 50，真值距离约 87,919.1348 m。

- A0 与 C 各有一条 raw detection（row 49、column 2048），通过冻结的 ±2-bin 候选门；检测距离约 87,919.15795 m（差约 0.023 m），估计方位 38.04420° 对真值 38°（差约 0.0442°）。
- B 没有通过该门限的 raw detection；但目标邻域 ±2×±2 bin 内存在 15 个 union hit cells。因此“无检测”发生在 hit 之后，但缺少逐 cluster lineage 时，不能定位到具体聚类选择/过滤关卡。
- A0/C 候选产生的位置约为 (429727.469, 4425870.392)，相对目标真值位置约有 68.7 m 误差；同时仅一周期且无 confirmed track，不能据此称作航迹定位性能。
- 本场景径向速度估计未解析，速度误差 `NOT_EVALUABLE`。不得以 0、预测速度或未确认 track 值填补。

## 可评估性与虚警边界

- H0 单次运行的 hits、聚类和 raw detection 数 `EVALUABLE` 为描述性计数；OFF 中目标不存在，因此这些候选均属 H0 输出。
- 统计 `Pfa` `NOT_EVALUABLE`：一组 seed/period/beam 不提供独立试验分布，cluster merge 也使 hit cell 不能当独立虚警事件。
- 统计 `Pd` `NOT_EVALUABLE`：只有一个场景，且没有 confirmed track；当前最多报告 A0/C 有候选、B 无候选这一可行性观察。
- 参数估计总体误差、径向速度误差与条件性 Pd 均 `NOT_EVALUABLE`。
- 没有保存“被拒 cluster → 最终 detection”逐行 lineage；本报告不从 aggregate cluster/detection 数推断具体失败根因。

详细逐 run detections、TrackManager debug、CFAR maps/geometry、CSI taps 和日志保存在本地 `outputs/phase_r7_cfar_correctness_20261022/{post_fix_matrix,post_fix_focused}/`；配对身份见对应 `experiment_manifest.json`。原始输出不纳入本次轻量源码/文档提交。
