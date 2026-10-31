# R20 Doppler 真值映射与物理正控制

## 独立计算

R20 不把 standalone truth 的 `row_truth` 直接当作 Production CFAR 行。评价器将 truth 的运动 Doppler 与几何 Doppler 相加，按该 branch/period/beam 的 PRF 折叠到 principal interval，再使用实际 Production 轴的首频率、有符号行间距、行数和方向，计算预期行号。并核验 `row_count × spacing` 与 PRF 的闭合，并用 circular row distance 与 frozen ±2-bin task gate 比较最终 Detection 候选。

真值字段与 Production 轴来源分别对照 [writeresults.cpp](../../../../src/writeresults.cpp) 的 truth-row 写出逻辑和 [processOnePeriod.cpp](../../../../src/processOnePeriod.cpp) 的逐期 Doppler axis 记录。R20 独立公式实现见 [r20_task_evaluator.py](../../../../scripts/phase_r/r20_task_evaluator.py)；它不把 standalone truth 的 `row_truth` 直接当作 Production CFAR 行。轴输入优先来自 R18 保存的 Production lineage/逐期轴记录；R8/R12 只用其留存的实际 Production axis/beam diagnostics。若物理轴无法从保存记录读到，则对应 task condition 直接标记 `DOPPLER_TRUTH_MAPPING_NOT_VERIFIED`，不以 truth 近似或扩大门限补齐。

另做了不依赖预期 Doppler row 的物理交叉核验：从保存的 `moving_target_truth.csv` 取目标斜距历史，在每个 `utc_mid` 最近的 7 个 pulse 上以局部最小二乘估计 `dR/dt`，再按 `carrier_phase_sign × 2 × dR/dt / (c/fc)` 计算双程 Doppler。载频和相位符号来自同场景 `scenario_resolved.json`。该计算不使用 truth Doppler 总频率来生成结果，只将独立结果与 truth 的分量总和作闭合比较。冻结容差为 `0.01 Hz`；588 行的最大绝对闭合差为 `0.001013469 Hz`。实际行映射还要求 Production 轴行数 × 有符号频率间距的绝对值闭合 PRF；行数/间距不闭合时 fail closed。

## 正控制门

| 控制 | 机会 | Frozen ±2 行内 | 判读 |
|---|---:|---:|---|
| R18 seed606 / 6.7 m/s / 30 dB / 0 ns 的 target-only TO | 3 | 3 | 3/3 落门，物理分量和 Production 轴/PRF 闭合通过。|
| 同场景 A0 ON | 3 | 2 | 三行轴/真值闭合均有效；两项有门内 Detection。一项 miss 保留为任务结果，未作为映射校验失败。|

门控状态为 `DOPPLER_MAPPING_VERIFIED`。本批次 Doppler audit 共 588 行：R18 504 行映射有效，R12 30 行有效，R8 seed202/303 共 36 行有效；R8 seed101 的 18 行虽通过斜距率—truth 频率闭合，但缺实际 Production axis，因此行映射和三条件 GMTI task 仍标为 `DOPPLER_TRUTH_MAPPING_NOT_VERIFIED`。其历史命中数不纳入 R20 统一统计。独立斜距率物理计算在 588 行均可闭合；这项 truth 公式交叉核验不会补造 seed101 缺失的 Production 轴。

## 结果文件

- [逐真值轴与候选审计](evidence/results/r20_doppler_truth_mapping_audit.csv)：truth 分量闭合、PRF/轴闭合、来源路径、预期 row、候选误差、是否在冻结 gate 内。
- [输入身份 manifest](evidence/manifests/r20_input_identity_manifest.json)：positive-control gate 快照。
- 实现：[r20_harmonized_task_review.py](../../../../scripts/phase_r/r20_harmonized_task_review.py) 与 [r20_task_evaluator.py](../../../../scripts/phase_r/r20_task_evaluator.py)。

没有更改 R19 的 ±2 行门，也没有重跑 CUDA。
