# R8 +4 ns 因果对照统一复评

## 可评价条件

R8 seed202 与 seed303 的 A0 / Current unknown +4 ns / Known correction upper bound 条件均具有同一保存 truth、有效 Production Doppler axis、同一 binary、场景背景和语义配置；其身份状态为 `IDENTITY_VALID`。R8 seed101 的保存 Detection/TrackManager 可读，但 compact records 没有实际 Production axis，三个条件均按冻结 gate 标为 `DOPPLER_TRUTH_MAPPING_NOT_VERIFIED`，不得把 R8 原始 3-seed 汇总数字当作本次已验证结果。

| seed | A0 | Current +4 ns (B) | Known correction | L=A0−B | G=Known−B | E=Known−A0 | ρ=G/L |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 101 | NOT_EVALUABLE | NOT_EVALUABLE | NOT_EVALUABLE | — | — | — | — |
| 202 | 2/3 | 0/3 | 2/3 | +2/3 | +2/3 | 0 | 1.0 |
| 303 | 3/3 | 0/3 | 3/3 | +3/3 | +3/3 | 0 | 1.0 |
| 两个可评价 seed 的中位数 [范围] | — | — | — | 5/6 [2/3, 1] | 5/6 [2/3, 1] | 0 [0, 0] | 1.0 [1.0, 1.0] |

其中 `L` 是 A0−B 的失配损失，`G` 是 Known−B 的回收量，`E` 是 Known−A0 的残差，所有差按越高越好的 hit fraction 定义。`ρ` 仅在 L>0 时计算，未裁剪到 [0,1]。R8 seed101 虽保留历史 Detection 和逐机会记录，但缺 Production axis，三个条件全部不纳入任务分母。其余两个 seed 内的三个 periods 是配对目标机会，不是独立 seeds；中位数/范围按 2 个独立 seed 汇总，不作显著性检验。

## 物理校正与参数/航迹

R8 历史 Known correction ON F2 relative RMS：seed202 `8.68168e-5`、seed303 `9.12385e-5`；R20 只能从历史 manifest 读取，原始 complex IQ 不在，故属于 retained closure audit。Known hit fraction 与 A0 在这两个可评 seed 中逐 seed 相等。

逐机会表显示 6 个 A0/B/Known 同一真值期中，有 5 个为 `A0 命中、B 漏检、Known 恢复`，另 1 个三条件均 miss；没有出现 Known 与 A0 命中不一致。5 个恢复机会跨 seed202/303 分别为 2/3 和 3/3。Detection/TrackManager 有额外代价：seed202 A0/Known 各有 2 个正确 Detection 但只有 1 个 target-confirmed output period；seed303 为 3 个 Detection、2 个 target-confirmed output periods，且各有一次跨期 track-ID handoff（原因未识别）。B 没有 target-matched confirmed output period。strict-truth unmatched final Detection 行数分别为 seed202 A0/B/Known `181/189/181`、seed303 `244/226/244`；它们不是 false-alarm 估计。按评价器定义的 unmatched confirmed output track IDs 计数，seed202 A0/B/Known 为 `20/45/20`，seed303 为 `66/75/65`；这是 TrackManager 描述统计，不是 Pfa 或独立虚警概率。

已匹配机会的参数评估率随 detection count 变化：seed202 A0/Known 各 2/3，seed303 各 3/3；angle、position、range、folded Doppler 在两法共同成功样本上近乎一致。径向速度三个 seed 场景均因 Doppler ambiguity 未 resolved 而不可评估；地速只在有 Production confirmed payload 的命中机会可评估。R8 202 A0/Known 各 1 个目标关联 period，303 各 2 个；两组条件均无目标同期并行航迹。seed303 A0 与 Known 各观察到一次跨期 track ID handoff，不能据此断言算法根因。

所有精确 opportunity 和 common-success 指标见 [R8 逐机会结果](evidence/results/r8_opportunity_level_comparison.csv)、[seed 汇总](evidence/results/r8_seed_level_causal_metrics.csv)、[参数可评估率](evidence/results/r20_parameter_evaluability.csv)、[航迹表](evidence/results/r20_track_metrics.csv) 与 [pairwise common-success 参数表](evidence/results/r20_parameter_pairwise_common_success.csv)。

## 解释

在两个 identity/axis 合格的 R8 seed 中，`+4 ns` Current 均为 0/3，而 A0 与 Known correction 均非零，且 Known 逐 seed 回到 A0 hit count。这支持该受控仿真条件下未知通道时延会降低 GMTI detection task、已知校正可恢复观察到的 hit loss。只覆盖 2 个独立 seeds、一个速度/SNR、一个 +4 ns 通道条件；这不是总体 Pd 或统计显著性结论，也不能代表其它系统误差。

R8 OFF 的 CFAR geometry diagnostics 实际保存了 `valid_cut_count` 和 union hit 计数；每个条件的 full-band 与 dynamic-band 均汇总 1,581,840 个 valid CUT。seed202 A0 的 full/dynamic observed hit fractions 分别为 `0.00023643` / `0.00020925`，seed303 A0 为 `0.00020103` / `0.00021178`。这些是空间相关 CUT 上的描述比例；缺少独立 H0 抽样单位和统计不确定性设计，因此 statistical Pfa 仍为 `NOT_EVALUABLE`，不能把它们解释为虚警概率。
