# R6：CFAR 门限数值稳定性与 hit-flip 审计

## 口径与有效 CUT

审计读取真实 CUDA Production 导出的功率、门限、hit、几何和 complex taps。逐 cell hit 校验严格采用 `power > threshold`。有效 CUT mask 按每条 geometry row 的尺寸、guard/background、Doppler circular、排除行和 cut-band 配置重建；每幅 map 为 527,280 valid CUT。36 个独立 branch-period maps 的 geometry count/hash 与实际 maps 相符；18 个 dynamic/full OR mask 均精确等于 Production union hit map。

总计 471,017 个 valid CUT 的门限为非正数，且 471,017 个全部命中；这些 cell 的 dB signed margin 不可定义，不能纳入有效 margin/Pfa 解释。

| 条件 | 非正门限 / 3,163,680 valid CUT | 命中数 |
|---|---:|---:|
| A0_ON | 4,519 | 37,297 |
| A0_OFF | 4,019 | 37,498 |
| A0_TO | 226,175 | 242,889 |
| C_ON | 3,762 | 36,178 |
| C_OFF | 5,744 | 37,240 |
| C_TO | 226,798 | 243,522 |

TO 条件的非正门限约占其 valid CUT 的 7.2%；不是可用 `Pfa=1e-6` 解释的正常 GO-CFAR 输出。

## 根因证据

当前 CUDA 路径在 `src/gpu/gpu_kernels.cu` 的 `row_prefix_kernel` 与 `col_prefix_to_integral_kernel` 中用 `float` 累加二维积分图，随后 `rect_sum_device` 用四个大累计量做 inclusion-exclusion 相减，再组合四个方向的训练窗均值。功率动态范围很大，前缀量相消后可生成明显负噪声估计和负门限。R6 没有修改这段 Production 算法。

独立复核 A0_ON dynamic、period 0、CUT `(row=111,col=3337)`：功率 `17.0784569`，Production 门限 `-2.6239653`，所以当前 kernel 命中；同一 F32 功率输入、相同四方向训练窗用 float64 直接求和得到最大方向均值 `8.5368343`，门限约 `114.8018334`，应为 miss。这是精确数值稳定性反例，不是对一般 Pfa 的推测。

因此本次门限近邻比例不能单独解释所有 hit flips：非正门限本身是明确异常；对正门限 cell，A0/C 微小地图差异会改变 GO 门限及严格 hit 决策。hit flip 是确定性的 map/门限分支改变，不能自动等同于真实虚警率改变。

## A0/C hit flips

下表跨 period 汇总。union 定义为 dynamic 与 full 两支 hit mask 的 OR，并已验证和 Production union 一致；`近阈值`指 flip cell 在 A0 或 C 任一有效、正门限下的绝对 dB margin 不超过 1 dB。

| 角色 | 分支 | A0 hits | C hits | shared | A0-only | C-only | flips | 近阈值 flips |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| ON | dynamic | 19,523 | 18,577 | 8,920 | 10,603 | 9,657 | 20,260 | 36.5% |
| ON | full | 17,774 | 17,601 | 8,050 | 9,724 | 9,551 | 19,275 | 30.3% |
| ON | union | 30,854 | 30,267 | 17,080 | 13,774 | 13,187 | 26,961 | 40.8% |
| OFF | dynamic | 18,501 | 17,816 | 7,493 | 11,008 | 10,323 | 21,331 | 34.4% |
| OFF | full | 18,997 | 19,424 | 9,475 | 9,522 | 9,949 | 19,471 | 28.9% |
| OFF | union | 31,193 | 30,935 | 17,188 | 14,005 | 13,747 | 27,752 | 39.4% |
| TO | dynamic | 147,687 | 147,504 | 133,254 | 14,433 | 14,250 | 28,683 | 5.7% |
| TO | full | 95,202 | 96,018 | 89,918 | 5,284 | 6,100 | 11,384 | 4.9% |
| TO | union | 161,137 | 162,359 | 149,781 | 11,356 | 12,578 | 23,934 | 6.1% |

ON/OFF 中约 29–41% flips 位于 ±1 dB；TO 只有约 5–6%。所以不能概括为“所有差异只是阈值附近的微小翻转”。同时，非正门限导致门限敏感性分析部分失去物理可解释性。

## 评价边界

- signed margin 只对正门限 cell 计算；非正门限 cell 标记不可评估，不替换成 epsilon 或更宽容差。
- 本轮不报告 Pfa 改善或 cell-level false-alarm rate。三个周期与有缺陷门限图不足以支撑统计性 Pd/Pfa 结论。
- 逐 cell 的 shared/A0-only/C-only 和非正门限索引在 [`cfar_a0_c_exact_hit_indices.npz`](evidence/r6_stagewise_divergence_20261022/cfar/cfar_a0_c_exact_hit_indices.npz)；全部 branch summary 在相邻 CSV。
- 首要后续工作应是单精度积分图与 float64/reference direct-window 的数值一致性修复和门限正值回归，不应在 R6 中改动 Production 或据此选算法。
