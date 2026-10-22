# R7：CUDA GO-CFAR 数值根因

日期：2026-10-22
状态：旧实现根因已复现；R7 修复后的数值闭合见 [修复与测试](R7_CUDA_CFAR_fix_and_tests.md)。

## 结论

旧 Production GO-CFAR 把整幅功率图的行前缀和积分图都存为 `float32`。GO 窗口矩形和通过四个大前缀值作包含—排除；当地杂波/强散射动态范围较大时，小窗口和是大数相减的残差，误差可超过真实窗口和，产生负门限或零门限。严格判决仍是 `CUT > threshold`，因此正功率 CUT 会被错误置为 hit。问题是数值稳定性，不是几何、alpha 或门限策略。

## 历史 R6 地图复核

复核只读 R6 已保存的 36 张生产分支地图：A0/C × OFF/ON/TO × 3 periods × dynamic/full。period/result-ID 映射、生产二进制 SHA 和几何行先由 R6 分支审计 manifest 验证；各图用实际 `float32` power 和 geometry CSV 中的运行时 alpha，重新计算独立 CPU float64 GO 参考。

| 模式 | 有效 CUT | 负门限 | 零门限 | 非正但参考为正 | 错误额外命中 | 漏命中 |
|---|---:|---:|---:|---:|---:|---:|
| OFF | 6,327,360 | 1,959 | 7,804 | 9,763 | 73,530 | 147 |
| ON | 6,327,360 | 1,066 | 7,215 | 8,281 | 68,806 | 663 |
| TO | 6,327,360 | 558 | 452,415 | 452,973 | 467,849 | 2,264 |
| 合计 | 18,982,080 | 3,583 | 467,434 | 471,017 | 610,185 | 3,074 |

因此，R6 报告中的 471,017 个非正门限 CUT 不是单纯的“所有零值均为错误”：这次逐 CUT 复核确认这 471,017 个门限都对应正的 float64 参考值。更重要的是，门限错误实际改变了 610,185 个分支 hit 决策，另有 3,074 个参考 hit 被漏掉。分支地图的错误命中统计不能直接视为独立虚警事件或 Pd/Pfa。

R6 数值汇总：[JSON](evidence/r7_cfar_correctness_20261022/analysis/r6_history/historical_r6_comparison.json)、[逐 branch-period CSV](evidence/r7_cfar_correctness_20261022/analysis/r6_history/historical_r6_branch_period_maps.csv)。本轮重算保存了输入/config SHA 指纹，但原 R6 analysis manifest 未提供逐输入 hash 作交叉验证；因此这些是本轮对当前保留文件计算的身份值，不声称与原始 run manifest 核验一致。历史输入、runtime XML、地图和 R6 报告本身均未改写。

## R7 同输入复验

R7 在 A0/B/C × OFF/ON/TO 九组前/后配对上复核；每组 527,280 个有效 CUT。前修复地图共出现 176,994 个错误非正门限（395 负、176,599 零），与独立分支参考相比有 312,779 个额外 hit 和 215 个漏 hit。修复后九组的 threshold 输出逐个 CUT 均等于 float64 参考转为 float32 的结果；branch hit-map 差异为 0。

注意 `union` 的输出 hit map 是 dynamic/full 两个分支的 hit OR；合成 threshold map 按 CUT 处较高功率选择分支，邻域却可能混合两个分支。故本分析先分别核对分支阈值和分支命中，再做 OR，未用 union 合成图错误地重算 GO 训练窗。

机器可复核结果：[R7 paired JSON](evidence/r7_cfar_correctness_20261022/analysis/r7_pair/numeric_comparison.json)、[R7 paired CSV](evidence/r7_cfar_correctness_20261022/analysis/r7_pair/numeric_comparison.csv)。生产二进制身份、输入/XML 哈希和本地 run 路径见[精简 pre-fix manifest](evidence/r7_cfar_correctness_20261022/manifests/pre_fix_experiment_manifest.json)与[精简 post-fix manifest](evidence/r7_cfar_correctness_20261022/manifests/post_fix_experiment_manifest.json)。完整原始运行目录仍只在本机 outputs 树中。

## 证据边界

- 根因和修复的数值正确性：在保存地图、合成测试和当前九组 Production 地图上 `EVALUABLE`。
- Hit-map 的变化表示检测候选输入改变，不等同于聚类/检测/Pd/Pfa 的改善。
- R6/R7 是有限 seed/period 的仿真证据；不支持总体统计显著性结论。
