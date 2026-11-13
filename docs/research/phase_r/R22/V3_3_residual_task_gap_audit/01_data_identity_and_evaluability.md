# 01｜数据身份与可评价性

## 设计范围

V3.2 冻结的 screening 为 12 seeds × 4 speeds × 3 SNR × 2 delay，单 case 20 targets、10 periods、beam 50。V3.3 只读取其中 `+4 ns` 的 144 个 case；每个 seed 覆盖 12 个速度×SNR 组合。四个核心校正条件每个 case 都有 200 个预定目标周期机会，因此每个方法共 28,800 个机会，seed 才是独立推断单位。

设计采用生产距离 bin `[666,1197]`、133-bin groups、`min_support=8`、phase gate `0.35 rad`。V3.3 不改变任何门槛，也不把方法不可评价记录当成 miss。

## 归档和输入证据

| 检查 | 本轮结果 |
|---|---:|
| 使用的 `+4 ns` case ZIP | 144/144 |
| archive SHA 与 V3.2 index 一致 | 144/144 |
| 四核心方法 truth association 集合一致 | 144/144 case |
| 四核心方法 Production branch 历史状态为 passed | 144/144 case |
| 连续面积杂波 packet/sample gate | 144/144：1,300 packets、6,508,782 samples/case |
| 实测面积杂波功率 | 均值 0.0226718，seed/case SD 0.0002995，范围 0.0221597–0.0231585 |
| 本地筛选目录归档 | 288 个最终 ZIP + 1 个历史额外 ZIP；本轮使用 144 个，额外 ZIP 保持不动 |

面积功率 SD 是 144 个 +4 ns case 的描述性离散，不能解释成 144 个独立 seed；同一个 seed 下的 case 共用相应场景背景身份。Stage2 报告记录相同的连续面积杂波几何与配对背景映射。`area_enabled` 字段本身不是本次判断依据，packet、sample 和实测功率均为正。

V3.2 机器审查包的 `review_manifest.json` 固定研究总纲 blob、design SHA、method config SHA、源码 commit 与 binary SHA。V3.3 的 [`evidence/manifest.json`](evidence/manifest.json) 另记录 144 个本地 ZIP 的复算 SHA、分析器 SHA、evaluator 源文件 SHA 和所有输出 SHA。

## 可评价范围与归档缺口

归档内 `evaluation/task_metrics.csv`、`target_associations.csv` 和 `track_level_metrics.csv` 保留了十周期汇总/逐目标机会。核心方法的有效真值键按 `(case_id, sample_key=target_id/period_id/beam_id)` 校验；对有检测产品的 period，再以 `(period_id, beam_id, det_id)` 连接 Production Detection 和真值。

但是 compact ZIP 每个 Production branch 只保留 `detection_results_GMTI09.csv`，其内部记录对应 `period_id=8`。这使下列边界明确：

- 十周期目标检测命中率可由已保存的全周期 task/association rows 评价。
- 四方法在 period 8 的 Detection→truth 参数误差可以用 V3.2 `r19_task_evaluator._parameter_errors` 重算。
- **位置误差是例外：** `target_associations.csv` 为每个目标周期机会保存了冻结 `r19_task_evaluator._position_error` 的 `position_error_m`。本轮对四个核心方法逐机会重建全十周期位置成功集合，并与归档 `parameter_metrics.csv` 的每 case/method own-success位置 RMSE和样本数核对；576/576 parity 通过、0 mismatch。因此全十周期位置 own-success、pairwise common-success 和四方法 common-success 可评价。
- 方位、斜距和折叠 Doppler 的十周期共同成功 RMSE仍无法从现存 Detection 产品重建；这些参数缺少完整的逐周期 Detection lineage。其 archive own-success 汇总仍可引用，但不能替代共同成功比较。
- `track_debug_runs` 和逐机会 TrackManager payload 未在 compact ZIP 中保留，因此 ground-speed common-success 与每个参数机会的航迹状态无法重建。
- radial velocity 在保存 evaluator 记录中没有有效估计样本。
- 完整 CSI maps、target-only complex response 和完整原始 IQ 不在 GitHub 精简包；不得用 CSI ROI 摘要或历史配置替代它们。

分析器读取 144 个被索引 ZIP 中的必要成员并复算其完整 archive SHA。ZIP 内成员 CRC 在读取相应成员时由 ZIP reader 检查；本轮没有声称逐项重验 ZIP 中全部成员 CRC。V3.2 执行报告保存了全矩阵归档核验记录。位置误差 parity 证明离线复算与归档 evaluator 汇总一致，不等于独立重跑 Production 或对原始 IQ 做物理验证。

## V3.2 方法缺口如何影响本轮

V3.2 的 4,320 个适用 ON 方法单元全部分类；共 49 个 robust-support 缺口：C4 Mode-B 24、P2 Mode-B 7、PKR Mode-B 18。它们仍为 `NOT_EVALUABLE`，未改写成 miss 或静默 fallback。四个本轮核心条件 `PK / Fixed D3 / Huber D3 ON / M1 Physics Matched` 的任务行、truth keys 和 Production branch 均可评价，因此 C4/PKR/P2 Mode-B 缺口没有抹掉核心配对分析。

P2 Mode-B 仍必须单独限界：12 个 seed 中只有 1 个 seed 拥有完整 12-case 的 C4-B/PKR-B/P2-B triplet。本报告不将 P2 Mode-B 与 PK 或其它模式混排。

## 虚警边界

当前 V3.2 target-present case 的 paired OFF false-CUT fraction 不是独立 H0 系统 Pfa。false-confirmed tracks 也不等于统计 Pfa。独立 H0 矩阵是另一设计/执行身份，不与 V3.3 目标条件拼接；本报告因此没有经验系统 Pfa 结论。
