# Phase-Corrected Current 基线报告

## 冻结定义

后续本文线中的 Current 指：

```text
Current CSI (phase-corrected + H0 coherence gate)
```

它包含：physical-theta background reuse、`F1 * conj(F2)` 相位定义、`F2 *= exp(+j*phi)` 补偿、现有 CTDR/P38、min-amplitude equalization，以及 `rho < 0.5` 时的 channel-1 H0 fallback。gate 主阈值固定为 `rho_threshold=0.5`，不能按 mismatch case 调整。

机器可读定义在 [`ai_csi_phase_corrected_baseline.json`](../configs/research/ai_csi_phase_corrected_baseline.json)。

## 生产验证

| case | 结果 |
|---|---|
| source-equivalent beam 15 | rho `0.99325982`，phase RMSE `0.11680659 rad`，cancellation `18.697901 dB` |
| beam 14/15/16 | cancellation `19.237249/18.697901/13.233184 dB` |
| H0 pure noise | gate=true 的 75/75 active rows 旁路，hits `0`；gate=false hits `21` |
| 3-period packed workspace | 同一 GMTI 进程连续完成 3 cycles，无 workspace error |

这些不是跨设备的统一 magic number；详细命令、XML 和输出路径见 [`AI_CSI_11_PhaseFix迁移与历史影响审计.md`](AI_CSI_11_PhaseFix迁移与历史影响审计.md)。

## 校准结果

本次重新校准没有采用历史 scalar。当前正式 compact-statistical records：

- seeds：`2026091006`、`2026091007`；每枚 seed 5 个配置 SNR、每点 3 replicas；总样本 30。
- offset mean/median/sample-std：`29.9570/33.6916/12.4939 dB`。
- 仅保留两个 production hit 的 29 个样本时，mean/median/sample-std：`31.3461/33.7159/10.0855 dB`。
- 因此 `gain_db=null`，后续使用 paired output SCNR 记录，不把不稳定的单一 offset 当作标定常数。

证据：[`calibration_phase_corrected_gate.json`](../outputs/ai_csi_phase_corrected_baseline/calibration_phase_corrected_gate.json)。

## 边界

本报告冻结的是算法语义和当前可复现证据，不代表已经完成全场景 Pd/Pfa 统计。当前 mismatch 使用 compact 1-beam/1-period case；Pd 已按生产 detection snapshot 与 visible truth 做单目标正样本审计，并已为 36 个 variant 补跑专门的 negative-control 与 target-only 成对评价，不能由 CFAR selected 数量替代。控制结果见 formal clean [`manifest.json`](../outputs/ai_csi_model_mismatch_metric_controls_formal_clean/manifest.json)；这些 Pfa/target-loss 仍不是跨场景生产统计边界。

formal model-mismatch 产物的 `source_commit` 为 `09bbd624c063126a97ea02196394ea62d3b4c4a6`，最终 clean-source provenance 为 `worktree_dirty=false`。随后 `0b6f7e7` 只修正了测试中的 gate 目录拼接，`4fcc139` 只更新了进展导航，均不改变 C++/CUDA 或实验 runner；当前仓库可直接复用该 formal 结果，但仍应把 compact 单波位/单周期边界与跨场景统计限制分开解读。
