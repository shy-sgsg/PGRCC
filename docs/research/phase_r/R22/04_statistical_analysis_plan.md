# R22-04：统计分析计划与当前可推断边界

## 预定分析原则

- **主要统计单位：** 独立 seed。每个 seed 内先聚合目标与周期，再对配对 seed 做条件差值；不能把同一 seed 的目标、脉冲、CUT、beam 或周期拆成独立重复。
- **配对比较：** 在相同 seed、目标真值、杂波/噪声底层 realization 和处理版本下比较方法；A0、Current、Known 与盲方法之间的不同信息来源须显式标注。
- **报告量：** 每 seed 的 hits/opportunities、misses、轨迹覆盖/连续性、参数误差、CSI/杂波残余与有效 CUT；筛选使用效应方向和区间，确认阶段报告配对效应量及按 seed 聚类的区间。
- **多重比较：** 只在方法和主要终点预先冻结后指定校正方案；当前没有启动确认分析，不能事后选择有利终点。
- **Pfa：** 独立 H0 seed 才能提供统计抽样单位。CUT 是场景内计数分母，不是独立 seed。当前 H0 表明确 `NOT_EVALUABLE_NO_INDEPENDENT_H0_CUT_UNIT`。
- **任务评价：** truth opportunity、final-union detection 与 Confirmed/matched-this-frame TrackManager observations 分层报告。未匹配目标场景检测不能当 Pfa。

## 当前数据能支持什么

seed900202 与 seed900203 都是 +4 ns、20 targets、单 beam、单周期的工程数据。当前可以描述每个 seed 的 20 个真值机会和方法命中；推断重复数最多为两个 seed，且运行的盲方法集合、Known 条件并不完全一致。目标机会的命中数不能提供 `n=20` 独立统计置信区间。

因此本阶段不计算或宣称具有确认意义的 Pd/Pfa 改善，不从一个或两个 seed 计算论文级显著性。两个 seed 中 C0 命中数低于 A0 的一致观察是后续审查线索，不等于完成性能验证。

## 确认统计的进入门

只有 PKR 参考与 residual-closure gate、Production 路径、multi-period truth/track lineage、H0 抽样设计和跨方法公平性全部通过后，才能冻结 seed 级样本量及区间方法。若确认阶段样本量/精度不足，结果标 `INCOMPLETE/INCONCLUSIVE`，不得放宽预注册门槛。
