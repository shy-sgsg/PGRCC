# R20 Doppler 真值映射与物理正控制

## 独立计算

R20 不把 standalone truth 的 `row_truth` 直接当作 Production CFAR 行。评价器将 truth 的运动 Doppler 与几何 Doppler 相加，按该 branch/period/beam 的 PRF 折叠到 principal interval，再使用实际 Production 轴的首频率、有符号行间距、行数和方向，计算预期行号。并核验 `row_count × spacing` 与 PRF 的闭合，并用 circular row distance 与 frozen ±2-bin task gate 比较最终 Detection 候选。

真值字段与 Production 轴来源分别对照 [writeresults.cpp](../../../../src/writeresults.cpp) 的 truth-row 写出逻辑和 [processOnePeriod.cpp](../../../../src/processOnePeriod.cpp) 的逐期 Doppler axis 记录。R20 独立公式实现见 [r20_task_evaluator.py](../../../../scripts/phase_r/r20_task_evaluator.py)；它不把 standalone truth 的 `row_truth` 直接当作 Production CFAR 行。轴输入优先来自 R18 保存的 Production lineage/逐期轴记录；R8/R12 只用其留存的实际 Production axis/beam diagnostics。若物理轴无法从保存记录读到，则对应 task condition 直接标记 `DOPPLER_TRUTH_MAPPING_NOT_VERIFIED`，不以 truth 近似或扩大门限补齐。

## 正控制门

| 控制 | 机会 | Frozen ±2 行内 | 判读 |
|---|---:|---:|---|
| R18 seed606 / 6.7 m/s / 30 dB / 0 ns 的 target-only TO | 3 | 3 | 3/3 落门，物理分量和 Production 轴/PRF 闭合通过。|
| 同场景 A0 ON | 3 | 2 | 三行轴/真值闭合均有效；两项有门内 Detection。一项 miss 保留为任务结果，未作为映射校验失败。|

门控状态为 `DOPPLER_MAPPING_VERIFIED`。本批次 Doppler audit 总计 594 行：R18 504 行均映射有效；R12 30 行均有效；R8 seed202/303 为 36 行有效；R8 seed101 有 18 行因缺实际 Production axis 而未验证。不得把 seed101 的旧命中数纳入 R20 统一统计。

## 结果文件

- [逐真值轴与候选审计](evidence/results/r20_doppler_truth_mapping_audit.csv)：truth 分量闭合、PRF/轴闭合、来源路径、预期 row、候选误差、是否在冻结 gate 内。
- [输入身份 manifest](evidence/manifests/r20_input_identity_manifest.json)：positive-control gate 快照。
- 实现：[r20_harmonized_task_review.py](../../../../scripts/phase_r/r20_harmonized_task_review.py) 与 [r20_task_evaluator.py](../../../../scripts/phase_r/r20_task_evaluator.py)。

没有更改 R19 的 ±2 行门，也没有重跑 CUDA。
