# AI-CSI-41：Production 复校准接入点与信息边界

## 目的与冻结边界

本文记录 `Production Hierarchical Calibration Pilot` 中研究性复校准的接入位置、输入输出和可使用的信息。它不改变 Production Current 的默认路径。未打开 `research_calibration_enable` 时，原有 CUDA 路径、CFAR、聚类、定位、TrackManager 和 PIPE 结果保持不变。

本阶段只研究 channel delay 单误差；servo、velocity、decorrelation、耦合 physical-state 以及 AI/router/native 4-channel STAP 均关闭。

## 接入位置与坐标

四通道协议 IQ 按生产协议融合：`channel 1 + channel 3 → F1`，`channel 2 + channel 4 → F2`。研究 tap 读取 production 已完成粗对齐、FFT/DBS、P38 相位处理和 range phase correction 后的最终 F1/F2。代码入口为 `GMTIProcessor::clutter_cancel_38_paper_1_cuda`；研究分支在 `clutter_cancel_38_paper_1_p38_cuda` 完成后、CSI 输出进入 CFAR 前运行。

F1/F2 是只读输入，适配器只写入现有 CSI 输出缓冲区，不覆盖 F1/F2，也不复制 TrackManager 或 CFAR。行轴是 production 的 azimuth/slow-time/DBS 后行，列轴是 range/fast-time 单元；尺寸由有效 pulse 数和 `rg_len` 推导，不在实验脚本中硬编码。

P38 phase fit 和 range correction 属于复校准前的 production 前置处理。因此 DDC/ordinary subtraction 是对最终 F1/F2 的 residual equivalent calibration，不是粗对齐、FFT/DBS、P38 或 range correction 的替代品。

## 复数约定与信息边界

统一使用：

```text
Gamma = sum(F2 * conj(F1)) / sum(|F1|^2)
y = F2 - Gamma * F1
```

`gamma_real/gamma_imag` 遵守这一方向和共轭约定。support 由 production support/P38/CSI validity、有限值、功率和相干性检查得到；target position、truth、注入 delay label 和 known error 不得进入 Gamma estimator。零/低 support、非有限 Gamma、低相干、group 缺失/重复或越界时必须是 `NOT_EVALUABLE`，不得静默回退到 Current。

Production Current 仍采用现有的最小幅度/legacy CSI 语义；它不是一个已估计的可解释复 Gamma。研究适配器输出 residual `F2-Gamma*F1`，二者的机制含义、输入信息和边界不能混写。

Mode-A 只从 `OFF=C+N` 估计 Gamma，`ON=S+C+N` 使用同一份 persisted reference fixed apply，`TO=S` 只作为 evaluator。Mode-B 从 truth-blind `ON=S+C+N` 估计并应用，`TO` 仍是 evaluator。reference CSV 必须包含 canonical `result_id/group_id`、完整 support、有限 Gamma 和明确的 `truth_used_in_estimator=false`。

## 运行时审计

每个研究分支保留 aggregate adapter CSV 和逐 group 的 `production_calibration_reference_gamma.csv`，审计方法、模式、role、输入身份、实际 XML override、status、support、Gamma、phase coherence、truth marker 及 group 覆盖。下游仍使用同一 GO-CFAR、cluster、protocol detection、localization、TrackManager、PIPE 链。

实测 cell-Pfa 只定义为 `hit_cut_count / valid_cut_count`，且 `valid_cut_count > 0`；configured Pfa 不是实测 Pfa。`clutter_metrics.csv` 将 CFAR cell、cluster、protocol detection、track 分开。目标保护只使用 `ON-OFF` 和 `TO` 的因果结果，不能使用 ON power alone。

TrackManager debug 若缺少关联候选、innovation、gate、lifecycle 等字段，ID-switch 原因写为 `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`，不由分析器猜测。发现 production implementation bug 时停止归因，先修复、测试、提交，再重启实验。

AI gate 固定为 `ai_training=false`、`router_enabled=false`、`native_4ch_stap=false`。缺少单误差 production evidence 的结论只能是 `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`。
