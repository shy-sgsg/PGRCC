# R10：检测恢复与任务精度敏感度

日期：2026-10-23
范围：只对照 R8/R9 已保存的 ON/TO/CUDA Production 与 evaluator 输出。不运行新的 Stage2、实验矩阵或 CUDA case。

## 指标定义

- **正确 detection**：Production detection 通过冻结 evaluator 的 target row/range gate，并由 cluster→detection lineage 追溯到 truth target。
- **正确 Confirmed Track**：该 period 的正确目标 detection 在 TrackManager 中形成 Confirmed 且匹配当前帧的目标轨迹；Tentative detection 不计入。
- **测角/定位恢复**：truth 匹配目标的 angle RMSE / EN 平面 2D position RMSE 接近 A0/Known。
- **地速恢复**：evaluator 的 ground-speed RMSE 接近 A0/Known；不以径向速度代替。

## 计数与误差

| 条件 | seed202 正确 detection | seed303 正确 detection | 合计 | 正确 Confirmed Track | angle RMSE (seed202 / seed303) | position RMSE m (seed202 / seed303) |
|---|---:|---:|---:|---:|---|---|
| 未校正 +4 ns（R8 B） | 0/3 | 0/3 | 0/6 | 0/6 | 无匹配结果 | 无匹配结果 |
| A0 | 2/3 | 3/3 | 5/6 | 3/6 | 0.0400° / 0.0177° | 61.40 / 27.33 |
| Known correction | 2/3 | 3/3 | 5/6 | 3/6 | 0.0400° / 0.0177° | 61.40 / 27.33 |
| 固定 D3 | 2/3 | 3/3 | 5/6 | 3/6 | 0.1069° / 0.0901° | 164.06 / 138.25 |
| 在线 D3 | 2/3 | 3/3 | 5/6 | 3/6 | 0.1163° / 0.1272° | 178.50 / 195.30 |

这是两个 seed 的描述性计数，不是统计显著性 Pd。seed202 period 1 在所有校正条件都未通过冻结 Doppler gate；其候选 row 44/col 2155 距 truth row 48/col 2154 为 −4 Doppler bins、+1 range bin。固定 D3 恢复了本次 5 个可匹配机会的计数，但没有恢复 seed202 的这一期，也没有恢复测角/定位 RMSE。

正确 detection 是 5/6，正确 Confirmed Track 是 3/6；两个分母不同。每个 seed 的 period 0 虽然检测正确，TrackManager 状态仍为 Tentative。R8/R9 evaluator 和 TrackManager 逐期字段见 [target lineage](evidence/r9_deterministic_delay_estimator_20261023/records/evaluation/target_lineage_audit.csv) 与本目录的 [目标关联报告](R10_target_association_consistency.md)。

## CSI 目标复响应与 CFAR margin

TO 数据的 amplitude ratio 和 phase error 是 truth-centered 5×5 ROI 上、pre-CSI aligned F1 到 csi_after 的聚合复传递；它不是 motion-compensation 求解器输入的单像素 CTDR phase。它适合观察目标复响应是否恢复，不应与上节的 raw CTDR phase 混为一个量。

| Seed / period | A0/Known amplitude, phase | 固定 D3 amplitude, phase | 在线 D3 amplitude, phase |
|---|---|---|---|
| 202 / 0 | 0.1481, −2.2989 rad | 0.1421, −2.2852 rad | 0.1413, −2.2823 rad |
| 202 / 1 | 0.1463, +0.5290 rad | 0.1430, +0.5122 rad | 0.1426, +0.5094 rad |
| 202 / 2 | 0.3083, +0.2499 rad | 0.3116, +0.2362 rad | 0.3116, +0.2340 rad |
| 303 / 0 | 0.1344, −2.3029 rad | 0.1277, −2.2864 rad | 0.1227, −2.2690 rad |
| 303 / 1 | 0.0771, +0.5624 rad | 0.0742, +0.5353 rad | 0.0717, +0.5103 rad |
| 303 / 2 | 0.5081, +0.1474 rad | 0.5015, +0.1396 rad | 0.4945, +0.1333 rad |

五个正确匹配 cell 的 CFAR margin 均为正，跨条件范围 6.95–18.91 dB。即使存在 margin，CSI target-only amplitude ratio 仍约 0.072–0.508，D3 的聚合幅相也未与 A0 完全重合。这说明门限通过只证明候选可被选出，不证明目标复响应、相干相位、定位或轨迹确认完全恢复。

## 地速敏感度

已匹配记录的 per-period ground-speed error（估计减 truth）：

| Seed / period | A0 / Known | 固定 D3 | 在线 D3 |
|---|---:|---:|---:|
| 202 / 2 | −0.085 m/s | +1.845 m/s | +2.017 m/s |
| 303 / 1 | −0.569 m/s | −0.321 m/s | −0.437 m/s |
| 303 / 2 | +6.733 m/s | +0.298 m/s | −2.232 m/s |

Ground-speed 方向受 period 影响：seed303 period 2 的 D3 误差小于 A0/Known，而 seed202 period 2 大幅变差。有限样本不能支持“D3 一致恢复/损坏地速”的总体结论。径向速度统计不在 R10 可评估范围。

## 可复核数据路径

- seed-condition RMSE、detection 数与 status：[R9 task_metrics_comparison.csv](evidence/r9_deterministic_delay_estimator_20261023/records/evaluation/task_metrics_comparison.csv)。
- R8 配对的 A0/B/Known、H0/OFF 与 TO 指标：[R8 evaluation](evidence/r8_unknown_delay_multiseed_20261023/evaluation/)。
- CSI 目标 ROI 汇总：[R8 seed202 evaluation](evidence/r8_unknown_delay_multiseed_20261023/evaluation/seed202/)、[seed303](evidence/r8_unknown_delay_multiseed_20261023/evaluation/seed303/)。
- 每个实际 detection、CFAR margin、raw phase 和 position Jacobian 的本地 Production CSV：outputs/phase_r_r9_deterministic_delay_20261023/cases/seed{202,303}/production/。

没有以软件测试、CPU 模拟或文件存在代替 CUDA production 证据。R10 没有重跑 CUDA；所有性能数字均明确沿用 R8/R9 已记录的本机 Native CUDA 运行身份。
