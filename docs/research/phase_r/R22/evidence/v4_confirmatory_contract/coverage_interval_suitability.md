# R22-V4 离散覆盖率区间方法适用性审计

本审计仅使用 CPU 生成的合成 seed 级覆盖率差，不是 GMTI 实验或性能证据。
独立单位为 seed；每组 30 个 seed，双侧置信水平 95%。

| 场景 | 方法 | 可评估率 | 可评估条件下覆盖率 | 区间宽度中位数 (pp) | NI 边界错误通过率 |
|---|---|---:|---:|---:|---:|
| discrete_ties_true_3pp | paired_t | 1.0000 | 0.9473 | 2.1433 | — |
| discrete_ties_true_3pp | bootstrap_percentile | 1.0000 | 0.9440 | 2.0004 | — |
| discrete_ties_true_3pp | bootstrap_bca | 1.0000 | 0.9253 | 2.0090 | — |
| zero_mean_paired_discrete | paired_t | 1.0000 | 0.9480 | 2.5525 | — |
| zero_mean_paired_discrete | bootstrap_percentile | 1.0000 | 0.9393 | 2.4006 | — |
| zero_mean_paired_discrete | bootstrap_bca | 1.0000 | 0.9433 | 2.4145 | — |
| zero_heavy_skew_true_minus_1pp | paired_t | 0.9560 | 0.9993 | 2.2787 | — |
| zero_heavy_skew_true_minus_1pp | bootstrap_percentile | 0.9633 | 0.9952 | 2.0083 | — |
| zero_heavy_skew_true_minus_1pp | bootstrap_bca | 0.9527 | 0.9965 | 2.0000 | — |
| near_zero_variance_true_minus_1pp | paired_t | 1.0000 | 0.9580 | 0.1506 | — |
| near_zero_variance_true_minus_1pp | bootstrap_percentile | 1.0000 | 0.9573 | 0.1333 | — |
| near_zero_variance_true_minus_1pp | bootstrap_bca | 1.0000 | 0.9533 | 0.1333 | — |
| all_identical_true_minus_0p5pp | paired_t | 0.0000 | — | — | — |
| all_identical_true_minus_0p5pp | bootstrap_percentile | 0.0000 | — | — | — |
| all_identical_true_minus_0p5pp | bootstrap_bca | 0.0000 | — | — | — |
| noninferiority_boundary_minus_1pp | paired_t | 1.0000 | 0.9473 | 2.6062 | 0.0293 |
| noninferiority_boundary_minus_1pp | bootstrap_percentile | 1.0000 | 0.9407 | 2.4337 | 0.0320 |
| noninferiority_boundary_minus_1pp | bootstrap_bca | 1.0000 | 0.9160 | 2.4200 | 0.0440 |

## Outlier sensitivity

| 方法 | 可配对评估数 | 端点最大移动中位数 (pp) | 区间宽度比中位数 |
|---|---:|---:|---:|
| paired_t | 500 | 0.5350 | 1.1854 |
| bootstrap_percentile | 500 | 0.5500 | 1.1803 |
| bootstrap_bca | 500 | 0.5333 | 1.1860 |

所有差异、非评估比例、Monte Carlo 标准误和逐场景数值见相邻 JSON/CSV。
方法选择不得以更容易通过非劣效门为依据；全相同数据必须保持不可评估。

## 暂定方法建议（待用户批准）

在本次六种合成分布与 1,500 次外层模拟中，暂建议以 `paired_t` 作为目标检测覆盖率及航迹覆盖率差的区间候选：可评估且非退化场景覆盖率接近 95%；非劣效边界错误通过率点估计为 2.93%（Monte Carlo SE 0.44个百分点），percentile 为 3.20%（SE 0.45个百分点），BCa 为 4.40%（SE 0.53个百分点）。paired_t 与 percentile 的差距小于模拟误差，不能据此宣称前者显著更优。
这只是有限模拟下的暂定统计方法建议；不证明普遍覆盖率，也不是 GMTI 性能证据。全相同数据仍须 `NOT_EVALUABLE`；在用户明确批准并冻结前，生产设计保持不可执行。
