# R3 A0/C 首个差异级审计

日期：2026-10-22。基线：`74877fe1463b9362dfce7c935a136d82a26ad26b`。只读既有 R3 产物；未重跑 CUDA。

## 裁决

**精确的首个处理级差异：`NOT_EVALUABLE`。** 当前产物能确认 A0 与 Known Correction C 使用同一类 Production 调度、F1/F2 输入闭合及 CSI 标量统计近似；可观察到 CFAR hit 集和检测记录不同。可是 `csi_roi_manifest.csv` 所引用的逐格 CSI `.npy` 已不存在，留存的 CSI tap 是区域标量，不能据此断言最早差异究竟始于复数 CSI、阈值比较还是 CFAR 命中选择。

## 路径与身份

- A0 与 C 的 seed-101 `production.xml` 均为 `enable_dbs_fusion=1`、`wavepos_parallel=0`、`csi_detection_band_mode=union`。`src/main.cpp` 依据 `enable_dbs_fusion` 调用 `processBeamsParallelFusion`；两边 `gmt_pipe_core.log` 均记录三次 `fusion group start`、每组 1 worker。
- 两份 `run_manifest.json` 均记 `git_commit=74877fe`、`Release`、`GMTI_pipe_core`。binary SHA、实际命令、dirty 状态为空；因此只能确认记录的提交与处理路径相符，不能声称二进制逐字节相同。
- A0 原始几何 CSV 把三期 period ID 都记成 0；`A0_Ideal_ON_retry_cfar_period_identity` 已写成 0/1/2，且命中数与原始 A0 完全一致。C 也写成 0/1/2。因此 period-ID 缺陷影响关联，不解释 A0/C 的 hit-count 差异。

## 最早可审查的保留 tap

Seed 101、beam 50、ON、`active_support_unmasked`（每期 159,705 cells）：

| period | A0 before/after mean | C before/after mean | A0/C coherence | A0/C phase RMSE (rad) |
|---:|---:|---:|---:|---:|
| 0 | 26460.415863 / 27435.250099 | 26460.415316 / 27435.245883 | .994268942 / .994268947 | .139912348 / .139912046 |
| 1 | 28990.952996 / 29991.429085 | 28990.952548 / 29991.432220 | .994600912 / .994600955 | .151917348 / .151906517 |
| 2 | 20963.557121 / 21698.608851 | 20963.556849 / 21698.609128 | .993145075 / .993144818 | .225099447 / .225114347 |

这些值支持“区域统计很接近”，不等于逐 cell 复数相等。manifest 引用的全图 power/complex `.npy` 不存在；少量 16-KB `.f32` debug 向量也不是完整 130×4096 CSI map。

## CFAR 及后续记录

每个数字按 period 0/1/2 排列；A0 使用修正 period identity 的 retry 产物：

| 条件 | dynamic mixed-input hits | full-CSI hits | detection CSV 行数 |
|---|---|---|---|
| A0 | 5549 / 6496 / 7478 | 6286 / 4941 / 6547 | 140 / 133 / 229 |
| C | 5244 / 6360 / 6973 | 6487 / 5126 / 5988 | 130 / 127 / 220 |

两分支每期 `valid_cut_count=527280`。A0/C hit hashes 与计数不同，但不含可恢复的 cell indices；检测计数也不能反推出首个分歧级。主表三 seed 的 A/B/C 任务 RMSE 仍可逐值读取：[R3 cross-seed CSV](evidence/r3_minimal_cuda_causal_pilot_20261021/r3_cross_seed_condition_metrics.csv)。

| seed | A0 angle / ground-speed / position RMSE | C angle / ground-speed / position RMSE |
|---:|---:|---:|
| 101 | .070849° / 6.93165 m/s / 114.892 m | .010597° / 1.27918 m/s / 16.728 m |
| 202 | .013185° / 1.01038 m/s / 20.567 m | .013185° / 1.01053 m/s / 20.566 m |
| 303 | .044153° / 5.16587 m/s / 82.524 m | .013292° / 1.84048 m/s / 20.772 m |

这支持指标层面“+4 ns Current B 比 A 差、已知校正 C 比 B 好”的三 seed 描述性方向；它不证明 A0/C 的下游差异由 CFAR 之前某个具体 stage 引起，也不证明统计显著性。target H1 是每 seed 3/3 可见 period 命中；radial-velocity RMSE 未解析。

## 状态与限制

- `EXECUTED`：A0/C CUDA Production 原运行及 A0 period-ID 修复重跑均有日志、XML、tap summary、CFAR rows、cycle metrics。
- `EVALUABLE`：F1/F2 闭合（预注册相对复 RMS `<1e-3`）、CSI 区域统计、分支级 hit counts、检测记录及报告中的 RMSE。
- `NOT_EVALUABLE`：逐格 CSI 复数差、CFAR 唯一 hit-cell union、精确首个 divergence stage、独立 binary identity。
- 没有证据显示本次评价脚本把 period-ID 修复伪造成性能变化；未发现需要改 evaluator 的已证实定义错误。

证据位置：`outputs/phase_r_minimal_cuda_causal_pilot_20261021/cases/base__seed_101__v_p6d7__snr_p30__delay_p4ns/production/{A0_Ideal_ON,A0_Ideal_ON_retry_cfar_period_identity,A2_Known_error_correction_upper_bound_ON}/`，以及相邻 R3 compact evidence。
