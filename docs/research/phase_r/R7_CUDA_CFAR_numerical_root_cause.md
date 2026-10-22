# R7：CUDA GO-CFAR 数值根因

日期：2026-10-22
状态：旧实现根因已复现；R7 修复后的数值闭合见 [修复与测试](R7_CUDA_CFAR_fix_and_tests.md)。

## 结论

旧 Production GO-CFAR 把整幅功率图的行前缀和积分图都存为 `float32`。GO 窗口矩形和通过四个大前缀值作包含—排除；当地杂波/强散射动态范围较大时，小窗口和是大数相减的残差，误差可超过真实窗口和，产生负门限或零门限。严格判决仍是 `CUT > threshold`，因此正功率 CUT 会被错误置为 hit。问题是数值稳定性，不是几何、alpha 或门限策略。

## 历史 R6 地图复核

复核只读 R6 已保存的 36 张生产分支地图：A0/C × OFF/ON/TO × 3 periods × dynamic/full。period/result-ID 映射、生产二进制 SHA 和几何行先由 R6 分支审计 manifest 验证；各图用实际 `float32` power 和 geometry CSV 中的运行时 alpha，重新计算独立 CPU float64 GO 参考。

| 模式 | 有效 CUT | CUDA 负门限 | CUDA 零门限 | 参考负门限 | 参考零门限/零背景 | 非正但参考为正 | 最大绝对门限误差 | 最大 float32 ULP | 错误额外命中 | 漏命中 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OFF | 6,327,360 | 1,959 | 7,804 | 0 | 0 | 9,763 | 356.798 | 39,915,487 | 73,530 | 147 |
| ON | 6,327,360 | 1,066 | 7,215 | 0 | 0 | 8,281 | 363.113 | 44,970,386 | 68,806 | 663 |
| TO | 6,327,360 | 558 | 452,415 | 0 | 0 | 452,973 | 4.05935 | 167,108,045 | 467,849 | 2,264 |
| 合计 | 18,982,080 | 3,583 | 467,434 | 0 | 0 | 471,017 | — | — | 610,185 | 3,074 |

CPU float64 参考的负门限数为 0；其零门限数/零背景 CUT 数在 OFF、ON、TO 三种模式也均为 0。因而，这批特定 R6 地图中 TO 的 452,415 个 CUDA 零门限没有对应真实零背景窗；逐 CUT 复核确认全部 471,017 个 CUDA 非正门限均对应正的 float64 参考值。最大绝对门限误差与最大 ULP 分开列出；近零值的 ULP 比值很大，不能代替绝对误差。门限错误实际改变了 610,185 个分支 hit 决策，另有 3,074 个参考 hit 被漏掉。分支地图的错误命中统计不能直接视为独立虚警事件或 Pd/Pfa。

R6 指定反例的四方向窗口和计算明细已保存为 [JSON](evidence/r7_cfar_correctness_20261022/analysis/r6_history/counterexample_cut_111_3337.json)。CUT `(row=111,col=3337)` 的输入功率为 `17.07845687866211`，alpha 为 `13.447822570800781`。左/右/上/下四窗各 656 个训练单元；Doppler 方向循环，原始 inclusive 行区间 `91..131` 对应实际行 `91..129,0..1`，`116..131` 对应 `116..129,0..1`。CPU 直接窗口求和与 CPU float64 积分图给出相同的四向和、均值和最终参考门限 `114.80183343751631`；旧 Production CUDA 门限为 `-2.623965263366699`。输入功率图、几何 CSV 和旧门限图的文件身份列于该 JSON。

R6 数值汇总：[JSON](evidence/r7_cfar_correctness_20261022/analysis/r6_history/historical_r6_comparison.json)、[逐 branch-period CSV](evidence/r7_cfar_correctness_20261022/analysis/r6_history/historical_r6_branch_period_maps.csv)。本轮重算保存了输入/config SHA 指纹，但原 R6 analysis manifest 未提供逐输入 hash 作交叉验证；因此这些是本轮对当前保留文件计算的身份值，不声称与原始 run manifest 核验一致。历史输入、runtime XML、地图和 R6 报告本身均未改写。

## R7 早期单周期同输入复验（历史范围）

R7 在 A0/B/C × OFF/ON/TO 九组前/后配对上复核；每组 527,280 个有效 CUT。前修复地图共出现 176,994 个错误非正门限（395 负、176,599 零），与独立分支参考相比有 312,779 个额外 hit 和 215 个漏 hit。修复后九组的 threshold 输出逐个 CUT 均等于 float64 参考转为 float32 的结果；branch hit-map 差异为 0。

注意 `union` 的输出 hit map 是 dynamic/full 两个分支的 hit OR；合成 threshold map 按 CUT 处较高功率选择分支，邻域却可能混合两个分支。故本分析先分别核对分支阈值和分支命中，再做 OR，未用 union 合成图错误地重算 GO 训练窗。

机器可复核结果：[R7 paired JSON](evidence/r7_cfar_correctness_20261022/analysis/r7_pair/numeric_comparison.json)、[R7 paired CSV](evidence/r7_cfar_correctness_20261022/analysis/r7_pair/numeric_comparison.csv)。生产二进制身份、输入/XML 哈希和本地 run 路径见[精简 pre-fix manifest](evidence/r7_cfar_correctness_20261022/manifests/pre_fix_experiment_manifest.json)与[精简 post-fix manifest](evidence/r7_cfar_correctness_20261022/manifests/post_fix_experiment_manifest.json)。完整原始运行目录仍只在本机 outputs 树中。

## 证据边界

- 根因和修复的数值正确性：在保存地图、合成测试和当前九组 Production 地图上 `EVALUABLE`。
- Hit-map 的变化表示检测候选输入改变，不等同于聚类/检测/Pd/Pfa 的改善。
- R6/R7 是有限 seed/period 的仿真证据；不支持总体统计显著性结论。

## 最终三周期 Production 数值复验

复核了最终 A0/B/C × OFF/ON/TO 九个条件各自的 `GMTI01/02/03` 地图：result ID 1/2/3 与 manifest 中 period ID 0/1/2 一一对应。每张图的输入 hash、pre/post 配对、runtime XML、CFAR geometry、diagnostic hit count 均通过交叉核验。独立 float64 GO 参考分别从实际 branch power 地图重算；post union power 等于两 branch power 的逐 CUT 最大值，post threshold 选取高功率 branch，post hit map 等于两 branch 参考命中的 OR。完整逐 case-period 记录见 [三周期数值 JSON](evidence/r7_cfar_correctness_20261022/analysis/r7_three_period/three_period_numerical_revalidation.json) 与 [CSV](evidence/r7_cfar_correctness_20261022/analysis/r7_three_period/three_period_numerical_revalidation.csv)。

| 模式 | union 有效 CUT | pre unique union hits | 参考 / post hits | pre extra / missed hit flips | pre 负 / 零门限 | post hit flips |
|---|---:|---:|---:|---:|---:|---:|
| OFF | 4,745,520 | 97,424 | 1,249 | 96,267 / 92 | 1,587 / 6,922 | 0 / 0 |
| ON | 4,745,520 | 97,629 | 5,748 | 92,480 / 599 | 994 / 6,726 | 0 / 0 |
| TO | 4,745,520 | 524,380 | 18,635 | 506,485 / 740 | 194 / 376,475 | 0 / 0 |
| 合计 | 14,236,560 | 719,433 | 25,632 | 695,232 / 1,431 | 2,775 / 390,123 | 0 / 0 |

post branch threshold 在全部 `28,473,120` 个有效 branch-CUT 上、post union threshold 在全部 `14,236,560` 个 union-CUT 上，均逐点等于 float64 参考转 float32；post branch 与 unique union hit 决策也均为零翻转。pre A0/C 有 branch-specific 历史图，36 张图合计有 `471,017` 个参考为正但旧门限非正的 CUT；pre B 只保存旧 Production union 地图，没有 branch-specific 地图，因此 B 的 branch-level 拆分不可评估，但 union-level pre/post 数值比较完整。表中 hit flips 是 CUT 决策差异，不是独立虚警事件，也不等同 Pd/Pfa。

此前九组早期单周期数值矩阵的 `4,745,520` CUT、`312,779` extra hits / `215` misses 仍只代表该早期地图范围；不得与上述三周期统计混用。三周期 task-level detection/track 解释见 [H0/目标关联审计](R7_H0_detection_association_audit.md) 和[方向裁决](R7_research_direction_decision.md)。最终 Production binary SHA-256 为 `de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`；完整 manifest 与原始 maps 保存在本地 `outputs/phase_r7_cfar_correctness_20261022/post_fix_matrix_3period_final/`。
