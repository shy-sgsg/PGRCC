# R22-04：统计分析计划与当前可推断边界

> **历史状态快照（2026-10-28）：** screening 当前不是运行中：V2 matrix 在一个 seed1932001、2.5 m/s、30 dB、+4 ns case 因 C4 Mode-B ON 的单个 robust support=7 < 8 而 `GATE_FAILED`。当前没有性能显著性结论，也没有启动 confirmatory/H0 分析；以下统计单位与独立 seed 规则仍适用，H0 CUT 不作独立样本。

> **历史状态快照（2026-10-28 V2）：** 当前完整 screening 尚在运行，以下 seed900202/900203 描述是早期工程 pilot 的历史边界。screening 中同一 seed 的目标、周期、beam、CUT 和方法分支仍须先按 seed 聚合；不因 case 或 opportunity 数增加而把它们视为独立重复。确认性比较尚未开始，独立 H0 矩阵尚未完成，因此当前不能给出确认性 Pd/Pfa 或论文级显著性结论。逐 case `EXECUTED/EVALUABLE` 状态、有效机会数及失败项以矩阵 manifest 和对应 compact archive 为准。

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

## 2026-10-29 独立 H0 seed 汇总补充

本补充只规定 V2 冻结的独立 H0 矩阵如何汇总，不解除 Screening 的 `GATE_FAILED`，不启动目标检测确认性比较，也不改变 30 个独立 seed、0/+4 ns 配对条件、方法集合或有效 CUT 定义。制定本补充前，已查看 seed1935001 和 seed1935002 的 H0 pilot 输出；因此本规则是对尚未完成 H0 矩阵的透明分析约定，不称作盲态预注册。

- **推断单位：** 独立 H0 seed。每个 seed、delay、method、mode、CUT band 先形成一条描述性比率 `observed_cut_hit_count / valid_cut_count`。CUT、距离单元、Doppler 单元和周期不作为独立样本。
- **单条件汇总：** 对每个 method/mode/CUT band 分别计算每 seed 比率的算术平均；95%区间使用按 seed 重抽样的 percentile bootstrap，10,000 次，NumPy `PCG64` 固定种子 `22029`，分位数采用线性插值。每次重抽样整体抽取 seed 行，再计算 seed 比率均值。
- **配对时延差：** 仅对 0 ns 与 +4 ns 都有冻结适用、有效记录的共同 method/mode/CUT band，先按 seed 计算 `+4 ns 比率 − 0 ns 比率`，再按完整 seed 配对重抽样并报告均值和95% percentile 区间。不得将两条件独立重抽样。
- **方法不适用：** V2 明确在 0 ns 不执行 P1、P2、R9 fixed D3、D3 Huber 和 M1 物理校正；这些方法可以单独报告 +4 ns H0 结果，但不得构造 0/+4 ns 配对差或以 Current 结果替代。
- **完整性：** 只有冻结条件下完整的30个独立 seed 记录才标记为完整汇总。缺失、失败或不可评价的 seed 不插补、不静默剔除；可以列出可用 seed 的描述性数据，但该条件的完整区间和配对差标为 `INCOMPLETE / NOT_EVALUABLE`。
- **解释边界：** 这些区间量化独立 seed 间经验 CUT-hit fraction 的变异，不把 CUT 当独立 Bernoulli 样本。H0 结果单独报告，不作为 Screening 通过、跨方法确认性排名或 GMTI 目标 Pd 改善的证据；当前不进行方法间多重比较显著性检验。


## 2026-10-31 独立 H0 完整矩阵结果

冻结 H0 矩阵已完成 60/60 个唯一 case，覆盖 30 个 seed 的 0/+4 ns 配对。分析器生成 1,800 行 per-seed CUT-hit fraction；60/72 个方法/条件汇总与 24/36 个配对汇总完整，剩余项均为设计定义的零时延不适用项。完整统计表、bootstrap manifest 和 Stage2 配对审计见 [H0 最终证据目录](evidence/h0_final_20261031/)。

这项结果只对无目标条件下每 seed `observed_cut_hit_count / valid_cut_count` 的经验比例作 seed-clustered 汇总。95% percentile bootstrap 为 10,000 次、NumPy PCG64 seed 22029；没有把 CUT 当独立样本，也不将结果称为系统 Pfa、target Pd 或确认性方法排名。R22 screening 的 `GATE_FAILED` 不变，target-present confirmatory matrix 未启动。
