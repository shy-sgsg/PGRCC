# 05｜目标保持与航迹输出

## 航迹汇总

compact ZIP 保留了 `track_level_metrics.csv` 的聚合统计，但未保留逐周期 TrackManager debug 和 PIPE payload lineage。因此这里报告归档中可复核的 seed 汇总，不把它们解释成逐目标的完整航迹审计。

| 方法 | 每 case 目标确认唯一周期数 | 确认后匹配率 | 每个目标场景 case 的 false-confirmed tracks |
|---|---:|---:|---:|
| A0 no-error | 8.944 | 99.383% | 155.95 |
| C0 Current +4 ns | 8.444 | 79.996% | 189.06 |
| PK Known correction | 8.944 | 99.383% | 156.13 |
| Fixed D3 | 8.917 | 99.614% | 158.05 |
| Huber D3 ON | 8.924 | 99.614% | 156.72 |
| M1 Physics Matched | 8.944 | 99.460% | 156.45 |

数值为 12 个 seed 的描述性均值。各估计方法的目标确认周期和匹配率接近 PK，但这不等同于完整轨迹质量闭合：逐目标 ID 连续性、状态来源和协议载荷 lineage 无法从 compact ZIP 独立重建。

按 seed 汇总每个 seed 的 12 个 case 的 track fragmentation 总数，A0/PK 为 0.583、C0 为 56.667、Fixed D3 为 1.083、Huber D3 ON 为 0.917、M1 为 1.917。该数是 seed 内 case 总量的均值，不是每 case 数值。

## false-confirmed 与虚警的区别

表中的 false-confirmed track 是 target-present case 中按归档评价器定义的未匹配确认航迹数。它不是 H0 虚警，也不是 CFAR false CUT / false cluster，更不是系统 `Pfa`。本轮没有将其换算成虚警概率或跨方法 Pfa 排名。

## 目标保持与未闭合证据

- A0 与 PK 的归档 track-level 摘要相近；Fixed、Huber、M1 的目标确认周期和确认后匹配率也接近 Known。
- 这些结果说明现有筛选中没有显现明显的确认后目标保持崩塌，但不证明逐目标复数幅相保持、track state 精度或全扫描部署性能相同。
- `track_debug_runs` 与逐字段 PIPE payload 不在 compact ZIP 中，地速共同成功集为 `NOT_EVALUABLE_TRACK_DEBUG_NOT_ARCHIVED`。
- 统计系统 Pfa 仍须独立 H0 与预先定义的统计单位；不能用这里的 target-present false-confirmed 汇总替代。

机器可读表：[`track_screening_summary.csv`](evidence/track_screening_summary.csv) 与 [`track_seed_summary.csv`](evidence/track_seed_summary.csv)。
