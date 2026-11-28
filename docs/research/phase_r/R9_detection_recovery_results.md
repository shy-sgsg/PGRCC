# R9 检测恢复与失败机制

## 比较条件和分母

本报告在 seed202/303 上复用 R8 的 A0（无误差）、B（通道2 `+4 ns` 未校正）、C（Known Correction）冻结结果，并加入 R9 固定 D3 标定和 ON 盲 D3 候选。每 seed 三期、一个 truth target；表内 `x/3` 是正确 final detection opportunities，不是统计性 Pd。正确 track association 是冻结 evaluator 的当前周期关联数。R9 Production 对静态和在线各做 ON/OFF/TO；A0/B/C 不重跑。R8 Stage2 连续面积杂波输入直接复用，没有重新生成场景。

| Seed | 条件 | 正确 Detection | 正确 Track association | Angle RMSE (deg) | Ground-speed RMSE (m/s) | Position RMSE (m) |
|---:|---|---:|---:|---:|---:|---:|
| 202 | A0 | 2/3 | 1/3 | 0.0400 (n=2) | 0.0852 (n=1) | 61.40 (n=2) |
| 202 | B 未校正 | 0/3 | 0/3 | NOT_EVALUABLE | NOT_EVALUABLE | NOT_EVALUABLE |
| 202 | C Known | 2/3 | 1/3 | 0.0400 (n=2) | 0.0852 (n=1) | 61.40 (n=2) |
| 202 | R9 固定 D3 | 2/3 | 1/3 | 0.1069 (n=2) | 1.8446 (n=1) | 164.06 (n=2) |
| 202 | R9 ON 候选 D3 | 2/3 | 1/3 | 0.1163 (n=2) | 2.0173 (n=1) | 178.50 (n=2) |
| 303 | A0 | 3/3 | 2/3 | 0.0177 (n=3) | 4.7778 (n=1) | 27.33 (n=3) |
| 303 | B 未校正 | 0/3 | 0/3 | NOT_EVALUABLE | NOT_EVALUABLE | NOT_EVALUABLE |
| 303 | C Known | 3/3 | 2/3 | 0.0177 (n=3) | 4.7778 (n=1) | 27.33 (n=3) |
| 303 | R9 固定 D3 | 3/3 | 2/3 | 0.0901 (n=3) | 0.3094 (n=2) | 138.25 (n=3) |
| 303 | R9 ON 候选 D3 | 3/3 | 2/3 | 0.1272 (n=3) | 1.6082 (n=2) | 195.30 (n=3) |

R9 的两个校正条件都将 detection 从 B 的 `0/6` 提高至 `5/6`，与 A0/C 的 `5/6` 观察值相同；正确 track association 也是 `3/6`，与 A0/C 相同。只有两个 seed、每 seed 三期，故这是受控任务可行性证据，不是统计显著性 Pd 改善。Radial velocity 在所有 R9 conditions 均为 `NOT_EVALUABLE`。

## CSI、目标保持与 H0 输出

| Seed | 条件 | H0 active-support cancellation (dB) | Output SCNR proxy (dB) | ON−OFF/TO 配对复幅度 | 配对相位 |
|---:|---|---:|---:|---:|---:|
| 202 | A0 / B / C | 5.818 / 5.329 / 5.818 | 70.652 / 72.845 / 70.651 | 0.455 / 0.674 / 0.455 | 1.23° / −1.08° / 1.23° |
| 202 | 固定 / ON 候选 | 5.805 / 5.800 | 70.994 / 71.044 | 0.487 / 0.487 | 1.00° / 1.01° |
| 303 | A0 / B / C | 5.436 / 4.995 / 5.437 | 74.462 / 75.050 / 74.467 | 0.410 / 0.604 / 0.410 | 4.95° / 1.46° / 4.95° |
| 303 | 固定 / ON 候选 | 5.431 / 5.374 | 74.470 / 74.305 | 0.433 / 0.453 | 4.54° / 4.11° |

`Output SCNR` 是冻结 CSI 评价器的配对 proxy；不是设备级绝对 SCNR。TO target-only 输出幅度比为 seed202 固定/在线 `0.09369/0.09400`，seed303 `0.14144/0.14090`；其逐期相位/幅度变化较大，详情保留在每个 TO 的 `task_metrics.json`。估计校正恢复了本场景 detection count，但没有恢复成 A0/Known 的所有 CSI/目标传递和参数误差数值。

三期 H0 false detections/clusters 和 unique union hit cells：

| Seed | A0 | B | C | 固定 D3 | 在线 D3 |
|---:|---:|---:|---:|---:|---:|
| 202 | 28 / 181 | 31 / 196 | 28 / 181 | 30 / 181 | 30 / 182 |
| 303 | 26 / 143 | 28 / 156 | 26 / 144 | 27 / 141 | 27 / 141 |

H0 false confirmed tracks 分别为 seed202 `4/4/4/5/5`、seed303 `5/4/5/5/5`。这些是有限输出计数，缺少覆盖运行时误警率所需的校准分母，不能称为 Pfa。R9 纠正时延没有消除 H0 虚假候选。

## 候选形成与漏检 lineage

R8 保存的 B/ON 目标中心 5×5 邻域，每期有 25 个 valid CUT；seed202 hit 数为 `11,15,15`，seed303 为 `13,13,18`。六期的目标邻域都没有形成局部 cluster（`local_cluster_count=0`），但图上更远处有生产 cluster/final detection；最近候选均未通过冻结目标门：

| Seed | 每期最近候选 Δrange (bin) | 每期最近候选 ΔDoppler (bin) | 目标匹配 |
|---:|---|---|---|
| 202 B/ON | `[0, −1, −1]` | `[−6, +24, +25]` | 0/3 |
| 303 B/ON | `[0, −1, −1]` | `[−6, +23, +29]` | 0/3 |

这些候选都可以在 CFAR cluster→final detection lineage 中找到，但它们未被 truth matcher 接受。它们不是已确认的同一目标连续航迹，不能把这些 Doppler bin 偏移解释为时延使真实目标多普勒移动了相同数量。

R9 固定/在线条件恢复了 seed303 三期 truth-matched final detection；seed202 period0/2 检出，period1 仍漏检。该漏检在 A0 和 C 也出现：truth 邻域 hit 存在，但 nearest candidate 位于 range `+1`、Doppler `+4` bins，越过冻结门；R9 lineage 已将其连接到生产 cluster/final detection。故此单期失败不能归因于剩余 `0.59–0.68 ns` 时延误差，也不支持继续提高时延精度就能解决全部漏检。

## 执行和审计状态

R9 生产路径共 12/12 通过；同一 seed/校正下 ON/OFF/TO 的 normalized runtime XML processing fingerprint 一致。8 次真实 CUDA CFAR map audit 均通过 GO-CFAR 数值检查、valid-CUT 几何和阈值精度对照。CFAR geometry 可以审计；不能把 unique hit-cell fraction 当作 Pfa。

完整逐期 association、candidate pair、cluster→detection lineage、CSI tap、运行 XML 和日志留在本机 R9 output root；紧凑文件位于 [R9 证据包](evidence/r9_deterministic_delay_estimator_20261023/)。比较值、分母、可评估状态与不可评估原因可从其中 CSV/JSON 直接复查。
