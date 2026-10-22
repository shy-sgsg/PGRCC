# R7：研究方向阶段裁决

日期：2026-10-22
结论级别：单 seed、单 period 可行性检查；不是统计性能裁决。

## 结论摘要

本阶段先发现并修复了一个足以污染虚警、检测候选和任务归因的 CUDA GO-CFAR 数值错误。修复后阈值/分支 hit-map 在当前九组真实 CUDA Production 回放上闭合到独立 float64 reference。当前数据因此能够支持“旧 CFAR 数值实现错误且已修复”，但还不能回答整体 GMTI 的统计 Pd/Pfa 或航迹性能。

| 问题 | 当前证据与结论 | 状态 |
|---|---|---|
| +4 ns 未知通道时延是否降低 GMTI 性能？ | `active_support_unmasked` CSI summary 中，B 相对 A0 的 coherence / phase RMSE：OFF `0.859473/0.589802` → `0.843101/0.628112`；ON `0.994269/0.139912` → `0.973719/0.245405`；TO `0.998277/0.068185` → `0.976468/0.221840`。ON 有 15 个目标邻域 hit cells，但无 target-gated raw detection，而 A0 有一个候选。提示相干性和单次候选链路受损，但这些 CSI cells 混合诊断噪声/杂波，一个 seed/period、一个 target、无 confirmed track，不能升级为总体任务性能结论。 | CSI 指标 `EVALUABLE`；任务统计 `NOT_EVALUABLE` |
| Known Physical Correction 恢复多少？ | C 的 coherence / phase RMSE 接近 A0：OFF `0.859414/0.589910`，ON `0.994269/0.139912`，TO `0.998277/0.068185`。A0/C 的三个模式 union hits 相同，ON 候选相同。说明已知校正在该 CSI summary 口径下恢复了大部分相干性差值；任务级恢复比例因无 confirmed tracks/统计分母而不可计算。 | CSI 相干性恢复 `EVALUABLE`；任务恢复率 `NOT_EVALUABLE` |
| 传统盲估计离已知校正上限多远？ | 本 R7 矩阵没有 Estimated/Blind 条件，故没有可计算的盲估计差距。 | `NOT_AVAILABLE` / `NOT_EVALUABLE` |
| 是否需继续研究未知物理状态估计？ | 仍是值得保留的假设；先补足 cluster-to-detection lineage、目标航迹闭环并用独立 seed 复验。当前不具备估计器/AI 开发门槛。 | 暂缓扩展 |
| 是否转向低速目标保护或复杂杂波建模？ | 本阶段先修复了检测前置数值错误，旧 false hits 会污染方向比较。当前证据不足以因结果差异就转主线；应在正确 CFAR 上先完成 H0 和任务关联，再用误差来源分解决定。 | 不作方向切换 |

## 阶段门控

- `IMPLEMENTED`：GO-CFAR double accumulation、基于舍入误差界的直接方向回退、数值诊断字段、CPU float64 references 与 CUDA test harness。
- `EXECUTED`：RTX 3050 Laptop 上真实 CUDA selftest；旧/新 Production 二进制的 seed101 A0/B/C × OFF/ON/TO 配对回放；A0 ON 首次/重复输出对比。
- `EVALUABLE`：当前输入上所有有效 CUT 的阈值数值和 branch hit decisions；本次 H0 描述计数；CSI coherence/tap 对比；冻结门限内的单次 raw detection 候选。A0 首次/重复输出一致，但首次 executable SHA 缺失，所以 same-binary repeatability `NOT_EVALUABLE`。
- `PERFORMANCE_VERIFIED`：本阶段没有可声明的性能指标；吞吐因 GPU 100% 忙碌未验证，Pd/Pfa、confirmed-track 航迹和速度性能也未验证。CFAR 数值正确性属于 `EVALUABLE`，不是性能验证。

上述相干性来自每个 post-fix run 的 `csi_metric_tap_summary.csv` 的 `active_support_unmasked` 行（159,705 cells，beam 050）；这些 cell 的 status 是 `diagnostic_noise_and_clutter_mixed`。压缩证据副本位于 [`evidence/r7_cfar_correctness_20261022/csi_metrics/`](evidence/r7_cfar_correctness_20261022/csi_metrics/)，保留原始 summary 行和 run ID，不把混合 ROI 指标解释为独立目标性能。

## 下一步建议

1. 在明确的检测/目标关卡上保存最小逐 cluster lineage，能从 CFAR hit 追到选择、过滤和最终 detection；没有 lineage 前不猜测 B 丢失目标的具体 stage。
2. 在线路审计完毕后，在相同处理链和正确 CFAR 上运行冻结 seed101 回归，再选独立 seed 扩展 A0/B/C，报告 H0、ON 目标、位置/角度/速度和 confirmed-track 指标；预先冻结独立统计单位和停止门。
3. 只有当未知误差产生可重复的任务损失、Known correction 可重复恢复且确定性盲估计存在可量化缺口时，才研究估计器。届时先比较可解释的传统估计；现阶段不训练 AI、不开发 Router、不扩展六参数联合估计。

R7 原始 CUDA 结果和 manifest 留在本地 `outputs/phase_r7_cfar_correctness_20261022/`；关键机器可读数值见同目录 `numeric_analysis/` 与 `numeric_analysis_r6/`。本裁决不覆盖或改写 R3–R6 历史报告。
