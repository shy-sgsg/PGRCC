# R21：参数精度、TrackManager 与物理闭合

## 时延和 F2 输入闭合

| seed | 方法 | 估计/应用时延 (ns) | 相对真实 4 ns 残差 (ns) | ON F2 相对 RMS |
|---:|---|---:|---:|---:|
| 1001 | B 未校正 | 0 | 4.000 | 0.327672 |
| 1001 | Known | 4.000 | 0 | 0.00008760 |
| 1001 | R9 fixed D3 | 3.409965 | 0.590035 | 0.06355 |
| 1001 | Huber D3 | 3.800393 | 0.199607 | 0.022292 |
| 1001 | M1 | 3.890649 | 0.109351 | 0.012317 |
| 1002 | B 未校正 | 0 | 4.000 | 0.328735 |
| 1002 | Known | 4.000 | 0 | 0.00008818 |
| 1002 | R9 fixed D3 | 3.409965 | 0.590035 | 0.06370 |
| 1002 | Huber D3 | 3.449202 | 0.550798 | 0.059676 |
| 1002 | M1 | 3.635411 | 0.364589 | 0.040184 |

Known 的 F1/F2 相对 A0 闭合误差均低于冻结 `1e-3` 门。B 的 F2 相对 RMS 约为 0.328；M1 在两个 seed 均优于 R9/Huber，但仍显著高于 Known，未通过 `1e-3` 物理闭合门。D3 与 M1 的 F2 数值来自其保存 evaluator closure 审计；B 相对 A0 的数值由保留的 Stage1 protocol IQ 重新计算。各 ON/OFF/TO 数值、phase RMS、样本数和来源标识见 [`r21_f2_physical_closure.csv`](evidence/results/r21_f2_physical_closure.csv)；完整时延输入见 [`r21_delay_estimates.csv`](evidence/results/r21_delay_estimates.csv)。

## 配对参数比较

使用同 seed、同 target/period/beam 且两方法均成功的共同机会计算 RMSE，避免用不同的成功集合直接比较：

| seed | 比较 | 共同机会 n | 方位 RMSE (deg) | 位置 RMSE (m) |
|---:|---|---:|---:|---:|
| 1001 | M1 vs Known | 2 | 0.04152 vs 0.03151 | 63.59 vs 48.29 |
| 1001 | M1 vs R9 fixed | 2 | 0.04152 vs 0.08330 | 63.59 vs 127.56 |
| 1001 | M1 vs Huber | 2 | 0.04152 vs 0.04964 | 63.59 vs 76.03 |
| 1002 | M1 vs Known | 2 | 0.05366 vs 0.02397 | 82.67 vs 37.00 |
| 1002 | M1 vs R9 fixed | 3 | 0.06843 vs 0.08932 | 105.04 vs 137.09 |
| 1002 | M1 vs Huber | 3 | 0.06843 vs 0.08574 | 105.04 vs 131.61 |

M1 的 angle/position 在两个 seed 都低于两种 blind D3；同时也都差于 Known。RMSE 是成功机会条件下的描述值，样本数只有 2–3。

Ground speed 仅有 seed1001 `n=1`、seed1002 `n=2` 的有效 current Confirmed payload；M1 相对 Huber/R9 的排序跨 seed 不一致。Radial velocity 因 ambiguity 未解决，所有方法均为 `NOT_EVALUABLE`，有效机会数 0。完整指标状态见 [`r21_parameter_evaluability.csv`](evidence/results/r21_parameter_evaluability.csv)、配对行见 [`r21_pairwise_common_success.csv`](evidence/results/r21_pairwise_common_success.csv)。

## TrackManager 输出

两个 seed 合计的“从未匹配目标的 confirmed track ID”描述计数为：A0 41、B 63、Known 41、R9 fixed D3 51、Huber D3 47、M1 41。M1/H0 等条件的 payload 未匹配数、混合 ID、handoff 和重复输出见 [`r21_track_metrics.csv`](evidence/results/r21_track_metrics.csv) 与 [`r21_track_semantics_audit.csv`](evidence/results/r21_track_semantics_audit.csv)。这些 track/detection 数不是统计 Pfa，也未显示 M1 在两 seed 都改善所有 track 指标。

## 状态

- Known 物理闭合：`PASS`。
- B 注入失配：`EXECUTED`，两 seed F2 明显偏离 A0。
- M1 相对 D3 的 F2 与角度/位置：两个 seed 方向一致地较好，仍未闭合到 Known。
- Ground speed / radial velocity / statistical Pfa：前者样本不足且方向不稳；后两者 `NOT_EVALUABLE`。
- `PERFORMANCE_VERIFIED`: 否。
