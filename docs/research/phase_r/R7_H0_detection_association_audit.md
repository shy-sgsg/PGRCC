# R7：H0 虚警与目标关联审计

日期：2026-10-22
最终结论以 seed101、三个配对 period 的 CUDA Production 复验为准。下文原有单周期数字保留为历史快照，不可与三周期计数混用。

## 最终三周期复验（当前权威结果）

ON 关联门限在查看修复后结果前冻结：同 period、同 beam，range 与 circular Doppler 各 ±2 bins；按最大匹配数优先、再最小化归一化欧氏残差做一对一分配。OFF 没有目标，不计算 Pd 或目标参数误差。修复后 lineage 对每个最终 detection 均可精确反查 cluster 行列；legacy pre-fix 输出没有该 lineage，因此旧 cluster 数标为不可评估。

### H0 / OFF

`GMTI01/02/03` 分别对应 period 0/1/2，saved hit maps 足以恢复逐 CUT 的 unique union（dynamic OR full）；因此下表同时列 branch hit-cell 和与不重复的 union hit 数。OFF 的 detection / confirmed track 是 H0 输出中的描述性虚警候选，不据此推算统计 Pfa。

| 条件 | 阶段 | unique union hit cells | 分支 hit-cell 和 | post-filter cluster | final detection | false confirmed track |
|---|---|---:|---:|---:|---:|---:|
| A0 | pre-fix | 31,193 | 37,498 | 不可评估（legacy 无 lineage） | 362 | 149 |
| A0 | post-fix | 404 | 679 | 24 | 24 | 4 |
| B (+4 ns) | pre-fix | 35,296 | 41,947 | 不可评估（legacy 无 lineage） | 369 | 152 |
| B (+4 ns) | post-fix | 442 | 674 | 27 | 27 | 4 |
| C (Known Correction) | pre-fix | 30,935 | 37,240 | 不可评估（legacy 无 lineage） | 345 | 137 |
| C (Known Correction) | post-fix | 403 | 676 | 24 | 24 | 4 |

逐 period 的 pre/post unique union hits 为：A0 `10,903/129`、`10,095/176`、`10,195/99`；B `11,968/145`、`11,135/186`、`12,193/111`；C `11,013/129`、`9,370/175`、`10,552/99`。27 条 case-period 数值记录见 [CFAR 数值 CSV](evidence/r7_cfar_correctness_20261022/analysis/r7_three_period/three_period_numerical_revalidation.csv)。修复后每个 case 的 cluster 与最终 detection 一一对应；lineage 完整。pre-fix cluster 缺少 lineage，仍不可评估。unique union hit 是 CUT 计数，不是独立虚警事件；修复前后计数只作同一场景下的实现差异描述，不代表统计 Pfa 或独立 seed 统计。

### ON / 目标检测与航迹关联

| 条件 | 阶段 | truth opportunities | 唯一门内 detection | misses | 未关联 final detections | confirmed tracks | 正确关联 confirmed track |
|---|---|---:|---:|---:|---:|---:|---:|
| A0 | pre-fix | 3 | 3 | 0 | 499 | 192 | 2 |
| A0 | post-fix | 3 | 3 | 0 | 172 | 29 | 2 |
| B (+4 ns) | pre-fix | 3 | 0 | 3 | 477 | 201 | 0 |
| B (+4 ns) | post-fix | 3 | 0 | 3 | 161 | 42 | 0 |
| C (Known Correction) | pre-fix | 3 | 3 | 0 | 474 | 170 | 2 |
| C (Known Correction) | post-fix | 3 | 3 | 0 | 172 | 29 | 2 |

每个条件跨 3 个 period 的配对输入、配置与输出评价记录见 `outputs/phase_r7_cfar_correctness_20261022/task_metrics_final_v4/evaluation_manifest.json` 及同目录 `task_metrics_summary.csv`；本地逐 case 的 `metrics.json` 和 `associations.csv` 保存每期真值、匹配、漏检与误差。

在 post-fix A0/C 中，period 0、1、2 均各有唯一门内 detection；period 1、2 关联到 confirmed matched-this-frame track，period 0 的 detection 没有对应 confirmed track。B 三期均无门内 detection，confirmed tracks 42 条但没有一条可关联到目标。

| 条件（post-fix） | 期 | range Δ bins | circular Doppler Δ bins | 位置误差 (m) | 方位误差 (°) | 地速误差 (m/s) | 径向速度 |
|---|---:|---:|---:|---:|---:|---:|---|
| A0 | 0 | 0 | 1 | 68.7378 | 0.0448771 | 不适用（无目标航迹） | 未解析 |
| A0 | 1 | 0 | 0 | 12.0669 | 0.0077847 | +0.252687 | 未解析 |
| A0 | 2 | 1 | 2 | 3.3330 | 0.0021394 | −0.478519 | 未解析 |
| C | 0 | 0 | 1 | 68.7379 | 0.0448771 | 不适用（无目标航迹） | 未解析 |
| C | 1 | 0 | 0 | 12.0670 | 0.0077848 | +0.252687 | 未解析 |
| C | 2 | 1 | 2 | 3.3327 | 0.0021392 | −0.478493 | 未解析 |

A0 与 C 的 post-fix RMSE 分别为：位置 `40.338603` / `40.338642 m`，方位 `0.02632572` / `0.02632575°`，ground speed `0.382643` / `0.382627 m/s`；径向速度三次均未解析。两个位置 RMSE 几乎相同。period 0 的 68.7 m 误差贡献两者各自约 96.79% 的位置平方误差和（只作为影响诊断：period 1–2 的 position RMSE 约 8.852 m；原数据未删除），但并不能解释 R3 先前 A0/C 的大幅差异。所有记录均保留。

### TO / 目标-only 响应

用真值中心 5×5 ROI 内的 pre-CSI 输入与 CSI 输出估计复标量传递系数 `sum(after * conj(before))/sum(|before|²)`；它是 CSI tap 响应诊断，不是 detection Pd 或真值幅度恢复率。pre/post CFAR 修复前后数值完全相同，因为 CFAR 位于该 CSI 输出之后。

| 条件 | 幅度比 | 能量比 | 传递相位 (rad) | 状态 |
|---|---:|---:|---:|---|
| A0 | 0.062682 | 0.014924 | 0.364889 | evaluable |
| B (+4 ns) | 0.075411 | 0.049537 | 0.028992 | evaluable |
| C | 0.062682 | 0.014924 | 0.364889 | evaluable |

## 历史单周期快照（下列内容不代表最终三周期计数）

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
