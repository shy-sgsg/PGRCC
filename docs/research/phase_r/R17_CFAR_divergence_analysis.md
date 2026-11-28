# R17：R14/R16 GO-CFAR 命中差异分析

日期：2026-10-25。分析范围为 R14/R16 seed505 C0 Production 的 OFF/ON/TO 三期 compact evidence。没有重建输入、没有重新运行 CUDA，也没有把 observed hit fraction 解释为 Pfa。

## 结论

CFAR 差异的主要原因已定位到实际执行文件：R14 使用的 `GMTI_pipe_core` 与 R7 的修复前 GO-CFAR binary SHA 完全一致；R16 运行使用另一份由 R16 Release Native CUDA build 生成的 binary，并记录了 GO 方向数值 fallback 直接重算。R7 对修复前实现已复现明确的 float32 累积消减问题。因此 R14 的高 hit 数来自与 R16 不同且已知存在数值缺陷的 CFAR 实现，R14/R16 CFAR 和下游 Detection 数不可直接横向比较。

R14 原始 CFAR 功率和门限图已经删除，故无法算出 R14 这九个 branch-period 中有多少 CUT 的门限实际为负/零，也不能将某个 hit 逐 cell 判为额外错误 hit。这里确认的是**修复前 executable 身份和已知缺陷机制**，不是重算 R14 的逐 CUT 错误率。

## 数值对照

每个 period 的 `threshold_test_count` 与 `valid_cut_count` 均为 527,280，几何为 130×4096、5200 edge-invalid cells。两边记录的 GO alpha、Pfa 和 training geometry 相同。下表为 compact geometry CSV 中各 CFAR branch 的 hit-cell 数；它们是 branch hit 数，不是独立虚警事件或经验 Pfa。

| Role | Branch | R14 periods 0/1/2 | R14 总数 | R16 periods 0/1/2 | R16 总数 | R14/R16 总数比 |
|---|---|---:|---:|---:|---:|---:|
| OFF | Dynamic in-band | 18,982 / 16,158 / 19,297 | 54,437 | 137 / 129 / 131 | 397 | 137.1× |
| OFF | Full-band | 14,243 / 17,628 / 16,253 | 48,124 | 131 / 150 / 147 | 428 | 112.4× |
| ON | Dynamic in-band | 15,405 / 15,224 / 11,976 | 42,605 | 349 / 204 / 691 | 1,244 | 34.2× |
| ON | Full-band | 15,410 / 14,186 / 13,175 | 42,771 | 445 / 207 / 683 | 1,335 | 32.0× |
| TO | Dynamic in-band | 96,659 / 40,195 / 68,718 | 205,572 | 3,357 / 2,220 / 4,208 | 9,785 | 21.0× |
| TO | Full-band | 51,358 / 39,206 / 67,992 | 158,556 | 3,393 / 2,191 / 4,184 | 9,768 | 16.2× |

R14/R16 每个 C0 role-period 的 final-union CFAR map hash 均不同（9/9）。hit 数比例只描述两份不等价 binary 的输出差异；不能作为虚警改善幅度、Pd/Pfa 或方法收益使用。

## 与 R7 已验证机制的对应

R7 的旧版 GO-CFAR 用 float32 存储行前缀和与二维积分图。GO 背景窗口通过多个大前缀值的包含—排除获得，在局部背景远小于累加前缀时会发生严重消减。R7 已用 R6/R7 保留功率图、门限图和独立 float64 参考，逐 CUT 验证负/零阈值可让正 CUT 产生额外 hit。R7 同输入 pre/post Production 对照观察到旧实现 312,779 个额外 hit、215 个漏 hit；修复后二者均为零 hit-map flips。

R14 的 executable SHA 与 R7 pre-fix artifact 相同；R16 `cfar_numerical_diagnostics.csv` 记录 `direct_fallback_direction_count`，OFF 的三个 period 分别约为 1.55M 次方向重算，且 source 记录了 double accumulation 与直接重算机制。该线索与 R7 修复实现相符。R16 的 fallback 计数是被直接重算的方向数，不是 CUT 错误数，也不能单独当作精度指标。

R14/R16 两份 C0 runtime XML 的 238 个标量叶子只有数据/结果/Track debug 路径不同。CFAR 共同值为 `cfar_type=GO`、`configured_pfa≈1e-6`、`alpha=13.447822570800781`、guard=4、background=16、Doppler circular=true；Dynamic branch 使用 row 46–84，full branch 使用 row 0–129。CFAR geometry CSV 也给出相同的 valid CUT denominator。因此本例不支持把 hit 数变化归因于门限、窗口几何或 CUT 分母改变。

## 为什么不把差异归因到 Stage2 或 CSI 场景

- R14/R16 的 9 个 Stage2 role-period 文件在留存 identity CSV 中 size 与 SHA 全部一致。
- C0 的 F1、F2 及 CSI 输出 real/imag hash 在留存可比性 CSV 中 9/9 相同。
- Runtime XML 的处理参数语义一致，C0 CFAR geometry 一致。
- 最关键的是 R14 binary 直接匹配 R7 pre-fix binary，而 R16 运行记录了数值修复路径。

这些结论基于封存时产生的 hash 比较表。Stage2 原始输入和完整 CSI `.npy` 已清理，R14 的 CFAR power/threshold/detector-input maps 也已清理；因此不能在本次审计中重新计算所有数组 hash 或重现 R14 逐 CUT 的门限轨迹。

## 对实际 GMTI 结果的影响

R14 的 hit-cell 过多会改变后续 cluster、detection 候选和 TrackManager 输入。R15 的 CPU association replay 固定复用了 R14 Detection，因此其候选竞争、track switch 和地速分解只描述旧 binary 产生的检测集合，不能外推为修复后 Production 的相同行为。

R16 C0/C1 则在同一 R16 executable 内对照；R17 未发现同运行 C0/C1 的 binary identity 不一致。R16 关于固定 Gamma=1 C1 在该场景的结果仍为单 seed、非统计结论。R14/R16 的跨阶段 C2/C3/C1 排名无效，须在用户明确授权后另作同 binary 有界复验。

## 状态与限制

- `EXECUTED`：读取了 R14/R16 provenance、branch manifests、runtime XML、CFAR geometry/numerical diagnostics、两阶段比较 CSV 和 R7 pre/post 历史记录。
- `EVALUABLE`：旧版 executable 身份与已知 GO-CFAR bug 的关联可评估；当前压缩证据不足以还原 R14 每个 CUT 的具体门限。
- `PERFORMANCE_VERIFIED`：本次未做新性能验证；没有 CUDA 运行。
- 不可支持的主张：不能把 R14/R16 hit 数差称作 Pfa 改善；不能给出 R14 本输入的错误 hit 精确数量；不能由 R16 fallback 次数推导显著性或性能。
