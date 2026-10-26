# R19：Mode-A 与 Mode-B 的可见分歧及因果定位边界

## 方法信息差异

- **Mode-A** 使用配对 OFF 数据估计 Gamma，因此是 privileged paired-OFF reference；其权重不是 blind estimator 的可部署性证明。
- **Mode-B** 只用当前 ON 的 F1/F2 估计 Gamma。R19 检查所有对比行 `paired_estimates_truth_flag_false=true`；估计没有读取真值标签、OFF 数据或 TO 控制。
- C2 每 case 有 3 个共同 Gamma groups，C3 每 case 有 117 个共同 groups。Gamma 配对摘要和逐组明细见 `gamma_mode_comparison.csv` 与 `gamma_mode_pair_details.csv`。

## 最早可见的保留差异

在现有留存字段中，Gamma 是最早可以直接比较的 Mode-A/Mode-B 估计量；它是“最早可见差异”，不能据此认定原始回波或完整 CSI 的真实因果起点。每个分层的 median(Mode-B − Mode-A) 复权幅值、相位及 support 差异如下：

| 方法 / 速度 / SNR | `|Gamma|` 差 | 相位差 (rad) | support 差 |
|---|---:|---:|---:|
| C2 / 6.7 m/s / 30 dB | +0.5786 | −0.0707 | 0 |
| C2 / 6.7 m/s / 20 dB | +0.3047 | −0.1788 | 0 |
| C3 / 6.7 m/s / 30 dB | +0.00691 | −1.5088 | +206 |
| C3 / 6.7 m/s / 20 dB | −0.00058 | −1.4904 | −11 |

C2 Mode-A/B 都有 3/3 相同组；C3 两边均有 117/117 组，但 C3 的支持样本数并非相同。C3 在高速度下表现为较小的复权幅值差与较大的相位差；仅凭汇总 tap 无法确定是哪一类空间/Doppler 单元驱动最终任务输出。

## 向下游传播

CSI tap 中可用的 `CA_ROI_dB` 是 active-support 上的 clutter/noise 混合 ROI 摘要，不是 target-only 复增益或目标幅相保持。6.7 m/s 下的中位值为：

| 速度/SNR | C2-A | C2-B | C3-A | C3-B |
|---|---:|---:|---:|---:|
| 6.7 m/s / 30 dB | −0.194 dB | −0.321 dB | −2.715 dB | −0.167 dB |
| 6.7 m/s / 20 dB | −0.885 dB | −1.303 dB | −3.728 dB | −0.614 dB |

高速度 Mode-A 的更多 CUT hits / Detection / confirmed tracks 与更高 target opportunity hit fraction 并存，说明“候选数量更多”并不等于更好的 false-alarm 特性。完整链路图 [`r19_mode_a_mode_b_pipeline_seed606_707_808_v6p7_snr30.png`](evidence/figures/r19_mode_a_mode_b_pipeline_seed606_707_808_v6p7_snr30.png)使用三个 seed 展示 6.7 m/s、30 dB 的 CFAR hit、final Detection 和 false confirmed TrackManager ID；Mode-A 标作 privileged reference，点为独立 seed，误差棒为 min–max，不表达显著性。

## 因果定位上下界

快照保留 Gamma、CSI scalar taps/ROI、CFAR geometry 与 hit counts、Detection/cluster lineage 和 TrackManager 结果。它没有保留完整 F1/F2 数组、CSI complex maps、逐 CUT valid/hit map、CFAR power/threshold maps。因而：

1. 可以确认不同 Gamma 摘要与下游计数/命中差异在这些 R18 分支中同时存在。
2. 可以把 Gamma 视作最早的保留观测差异，并按各层保存值追踪输出数量如何变化。
3. 不能把 Detection 或航迹差异归因为某个具体 CUT、目标 cell CSI complex response、CFAR threshold tail，或某次关联竞争。
4. 不能从 `hit_index_hash` 还原 hit 的空间/Doppler 位置；这些定位需要新 Production 重放，不能以本次离线统计冒充。

因此本阶段的措辞是“分层描述及可见差异传播”，不声称已识别完整算法因果根因。
