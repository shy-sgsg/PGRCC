# R22-V4 P0：初步科研结果与方向评估

日期：2026-11-23
证据级别：一个独立工程 seed 的描述性观察，不是确认性推断。

## 当前 case 的 target-present 任务记录

每种 ON 方法有 200 个可评价 target-period 机会。命中率为该固定场景的任务覆盖率，不是独立 seed 的 Pd 估计。

| 条件/方法 | 命中 / 机会 | 覆盖率 | own-success position RMSE |
|---|---:|---:|---:|
| A0，无误差参考 | 140/200 | 70% | 26.663 m |
| C0，Current +4 ns | 26/200 | 13% | 135.917 m |
| PK，Known correction | 140/200 | 70% | 26.663 m |
| Fixed D3 | 142/200 | 71% | 57.668 m |
| M1 causal prefix | 140/200 | 70% | 44.963 m |
| Huber D3 causal prefix | 142/200 | 71% | 51.710 m |

own-success RMSE 的方法样本集合不同，不能单独作严格配对排名。共同成功集合上，M1 相对 Fixed D3：135 个共同匹配机会，RMSE 43.704 m 对 56.204 m，差值 −12.500 m；相对 Huber：136 个共同机会，43.583 m 对 50.828 m，差值 −7.245 m。它们仍是单 seed 描述值，不能据此宣称 M1 的可重复优势。

该 seed 的记录与“+4 ns 可造成明显任务退化、已知校正可恢复这一固定场景的检测覆盖”一致：C0 为 26/200，PK 为 140/200，A0 为 140/200。但该结论不能外推为未知通道误差的一般影响或统计显著恢复。

## 因果时延估计和目标保持审计

| 估计器 | 可用周期 | 估计均值 | 相对注入 4 ns 的平均偏差 | RMSE | 范围 |
|---|---:|---:|---:|---:|---:|
| M1 causal prefix | 10/10 | 3.6629 ns | −0.3371 ns | 0.3669 ns | 3.5685–4.0838 ns |
| Huber D3 causal prefix | 10/10 | 3.5167 ns | −0.4833 ns | 0.4937 ns | 3.4098–3.7996 ns |

两估计器均未 fallback，使用当期及历史未知误差 ON C1/C2，真值标志为 false。上述误差只针对单一场景；不能代替跨 seed 的盲估计精度验证。

CSI_AFTER 固定真值 ROI 中，TO-only 输出功率相对同 case A0_TO 的 200 个 ROI 比值，中位数均为 1.0；M1 均值 1.042、P10/P90 0.983/1.185，Huber 均值 1.057、P10/P90 0.979/1.240。此表用于目标响应保真审计，不等于 Pd 或目标能量保持率的统计结论。PK 的比值约为 1；Current 的均值升高且分布更宽。

所有 18 个 Production 分支的 TrackManager 不变量审计为零违规。未匹配 payload、confirmed track 或 cross-target association 不能直接标为 false alarm。当前 12 行 OFF CUT fraction 均被标记 NOT_EVALUABLE_NO_INDEPENDENT_H0_CUT_UNIT：OFF 是配对背景角色，不是独立 H0 seed；每分支 5,272,800 个 CUT 也不是独立统计样本。因此本 pilot 不报告 Pfa。

## 对整体研究目标的判断

1. **未知通道误差是否降低 GMTI？** 这个 +4 ns、2.5 m/s、20 dB、20 目标单场景下，Current 的机会命中率远低于 A0/PK，是明确的工程信号；总体可重复性尚未验证。
2. **Known correction 恢复多少？** 本场景命中率从 13% 回到 70%，与 A0 持平；不能据此给出总体恢复比例。
3. **传统盲估计距离 Known 多远？** 本场景 M1/Huber 命中覆盖为 70%/71%，接近 PK 的 70%；位置误差共同成功比较只做描述，尚无 seed-level不确定度。
4. **是否需要未知物理状态估计？** 仍值得按 V4 冻结方案做独立 seed 评估；单 seed pilot 不能证明算法收益或必要性。
5. **是否转向低速保护/复杂杂波？** 当前没有新增证据支持转向。

方向裁决为继续有界的未知物理误差估计与物理校正评价，不启动 AI。当前只证明了单 case 工程链可运行并产出可评价结果；科研标签为 IMPLEMENTED/EXECUTED/EVALUABLE，PERFORMANCE_VERIFIED=false。
