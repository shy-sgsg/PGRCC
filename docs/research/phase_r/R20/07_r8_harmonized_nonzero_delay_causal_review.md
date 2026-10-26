# R8 +4 ns 因果对照统一复评

## 可评价条件

R8 seed202 与 seed303 的 A0 / Current unknown +4 ns / Known correction upper bound 条件均具有同一保存 truth、有效 Production Doppler axis、同一 binary、场景背景和语义配置；其身份状态为 `IDENTITY_VALID`。R8 seed101 的保存 Detection/TrackManager 可读，但 compact records 没有实际 Production axis，三个条件均按冻结 gate 标为 `DOPPLER_TRUTH_MAPPING_NOT_VERIFIED`，不得把 R8 原始 3-seed 汇总数字当作本次已验证结果。

| seed | A0 | Current +4 ns (B) | Known correction | B−A0 | Known−B | Known−A0 |
|---:|---:|---:|---:|---:|---:|---:|
| 202 | 2/3 | 0/3 | 2/3 | −2/3 | +2/3 | 0 |
| 303 | 3/3 | 0/3 | 3/3 | −3/3 | +3/3 | 0 |

R8 共两个 seed 可用严格 Production Doppler 任务评价；每个 seed 内三个 periods 是重复机会，不是独立 seeds。当前唯一正式的命中结果是这两行，不把 seed101 历史 `A0/B/Known` 计数拼进来。

## 物理校正与参数/航迹

R8 历史 Known correction ON F2 relative RMS：seed202 `8.68168e-5`、seed303 `9.12385e-5`；R20 只能从历史 manifest 读取，原始 complex IQ 不在，故属于 retained closure audit。Known hit fraction 与 A0 在这两个可评 seed 中逐 seed 相等。

已匹配机会的参数评估率随 detection count 变化：seed202 A0/Known 各 2/3，seed303 各 3/3；angle、position、range、folded Doppler 在两法共同成功样本上近乎一致。径向速度三个 seed 场景均因 Doppler ambiguity 未 resolved 而不可评估；地速只在有 Production confirmed payload 的命中机会可评估。R8 202 A0/Known 各 1 个目标关联 period，303 各 2 个；两组条件均无目标同期并行航迹。seed303 A0 与 Known 各观察到一次跨期 track ID handoff，不能据此断言算法根因。

所有精确 opportunity 和 common-success 指标见 [R8 逐机会结果](evidence/results/r8_opportunity_level_comparison.csv)、[seed 汇总](evidence/results/r8_seed_level_causal_metrics.csv)、[参数可评估率](evidence/results/r20_parameter_evaluability.csv)、[航迹表](evidence/results/r20_track_metrics.csv) 与 [pairwise common-success 参数表](evidence/results/r20_parameter_pairwise_common_success.csv)。

## 解释

在两个 identity/axis 合格的 R8 seed 中，`+4 ns` Current 均为 0/3，而 A0 与 Known correction 均非零，且 Known 逐 seed 回到 A0 hit count。这支持该受控仿真条件下未知通道时延会降低 GMTI detection task、已知校正可恢复观察到的 hit loss。只覆盖 2 个独立 seeds、一个速度/SNR、一个 +4 ns 通道条件；这不是总体 Pd 或统计显著性结论，也不能代表其它系统误差。

无统计 Pfa：R8 OFF manifests 没有可配对的有效 CUT hit/denominator 字段，当前值为 `NOT_EVALUABLE_MISSING_H0_COUNTER`。
