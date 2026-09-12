# AI-CSI-31：J8 Clutter-Support-Only Calibration 审计

## 设计

J8 只在 OFF-derived 的 clutter support 内校准，support domain 为 pulse-frequency
CSI cross-spectrum magnitude；support 外保留 Current/未校准 channel-2 spectrum，
support 边缘使用 5 percentile guard band 做 soft blend。零 correction 路径保持
source bytes 不变，J8 不读取 target truth，也不训练 AI。

实际实现位于 `run_joint_physics_calibration.py` 的
`support_blend_weights`、`support_only_corrected_spectrum` 和
`apply_support_only_raw_correction`；定向运行脚本为
`scripts/evaluate_j8_support_only.py`。

## 三场景 CUDA 结果

第二轮使用 RTX 3050、source commit `5a21da8`，运行 fast target、support-edge target
和 slow-near-ridge target，共 6 个候选方法。安全条件仍为 `L_causal >= -0.25 dB`、
Current 检出目标不丢失、target-off Pfa/false-cluster 不增加。

| scene / method | cancellation (dB) | `L_causal_min` (dB) | Pfa delta | false-cluster delta | 丢失 Current target | safe |
|---|---:|---:|---:|---:|---:|---|
| fast / P1 | 4.9286 | −1.8463 | −3.47e−5 | +3 | 0 | 否 |
| fast / Joint | 4.9450 | −1.3709 | +9.63e−6 | +4 | 0 | 否 |
| support-edge / P1 | 3.6929 | +0.7606 | −3.85e−5 | +6 | 0 | 否 |
| support-edge / Joint | 4.5362 | −4.3377 | −2.70e−4 | +11 | 1 | 否 |
| slow-near-ridge / P1 | 10.0857 | +0.0077 | +9.63e−6 | 0 | 0 | 否 |
| slow-near-ridge / Joint | 11.2730 | −1.6392 | +1.25e−4 | +5 | 0 | 否 |

因此本轮没有任何 J8 候选达到安全标准。J8 的 support-only 结构已实现并完成目标化
CUDA 证据，但当前不能写入 final gate 的“已恢复”路径；下一步若继续，必须先解决
support 边界造成的 target transfer/cluster 回归，再扩大矩阵。

## 证据与清理

- 配置：[j8_support_only.json](../configs/research/j8_support_only.json)
- 第二轮 manifest：[j8_support_only_manifest.json](../outputs/j8_support_only_targeted_v2/j8_support_only_manifest.json)
- 第二轮方法指标：[j8_method_metrics.csv](../outputs/j8_support_only_targeted_v2/j8_method_metrics.csv)
- 第二轮 transfer/CFAR：[j8_transfer_rows.csv](../outputs/j8_support_only_targeted_v2/j8_transfer_rows.csv)、[j8_cfar_rows.csv](../outputs/j8_support_only_targeted_v2/j8_cfar_rows.csv)

运行过程删除了每场 raw scene runtime，保留紧凑 metrics、CFAR/transfer、manifest、
资源快照和 provenance。首轮未含显式 safe 字段的 72 KiB 结果仍作为独立失败基线保留在
`outputs/j8_support_only_targeted/`，避免覆盖审计现场。

