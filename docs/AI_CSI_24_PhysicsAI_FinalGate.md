# AI-CSI-24：Physics-AI Final Gate

## 当前判定

本阶段仍是 `ai_training=false`。fresh Test-V2 formal 已完成，但最终 gate 为
**`UNRESOLVED`**：J5/J6 均有 0.5 dB material recovery，却没有同时满足 target
preservation 无回归条件。因此当前既不能判 `NO_GO_AI_M1_M2`，也不能判
`REOPEN_PHYSICS_AI`；没有任何训练授权。

## 最终 gate 规则

`NO_GO_AI_M1_M2`：若 J5/J6 在 material Oracle headroom（主阈值 0.5 dB，并报告 1 dB sensitivity）上有稳定的 deterministic recovery，同时 production Pfa、false clusters、causal Pd 和 target preservation 相对 Current 没有回归，则继续不训练 AI，并把确定性物理校准作为结论。

`REOPEN_PHYSICS_AI`：只有当 J5/J6 在 fresh Test-V2 中真实激活、上述生产指标无回归、但 material recovery 仍不足且 residual gap 可重复时才输出。这个标签只允许重新审查物理估计器和门控定义，**不授权训练**。

任何方法如果只在 Oracle 或机制标签帮助下有效、只改善 score-map diagnostic、或无法通过 held-out zero 的 false activation 检查，都不能进入 AI gate。

## 已完成的 Test-V2

- 新 seed/LHS/family；48 null calibration、16 validation、64 held-out test；test 为 8 families × 8，含 held-out zero；
- J0–J6 + evaluation-only Oracle；质量 gate 只看 calibration+validation；
- production GO-CFAR valid CUT、cluster、causal Pd、target preservation；scene-block bootstrap；
- 0.5 dB 主 material threshold 和 1 dB sensitivity；报告 all-scene gain、activation-conditioned recovery、material recovery、fallback opportunity cost；
- 清理 raw runtime 后保留 manifest/CSV/JSON/资源快照，记录 source commit、dirty 状态、输入/资源和 `ai_training=false`。

## Formal 判定证据

原始 CUDA 运行来自 `669df38` 的 clean worktree；摘要修正来自 `1445f3f`，没有重新
运行。held-out Test-V2 的 Current target preservation 中位数为 `+0.19479 dB`，
J5 为 `+0.00032 dB`，J6 为 `-0.00978 dB`；对应 material recovery median
分别为 `0.8426` 和 `1.0249`。J5 的 Pfa mean 为 `0.0083910`，J6 为 `0.0083717`，
Current 为 `0.0083844`；causal Pd 三者均为 `0.1389`。因此阻塞项是 preservation
regression，而不是缺失 formal 实验。

## 当前证据

screen 的 compact manifest 在 `outputs/physics_adaptive_selective_v2_screen/formal_matrix_manifest.json`；J5/J6 分支、生产 CFAR、bootstrap 和 recovery 证据分别在同目录的 `scene_summary.csv`、`production_cfar_rows.csv`、`production_cfar_bootstrap.csv`、`recovery_metrics.csv` 与 `recovery_summary_0_5db.csv`。这些是定向筛查证据，不作为最终 Test-V2 结论。

最终 formal manifest 为 `outputs/physics_adaptive_selective_v2_formal/formal_matrix_manifest.json`；
held-out gate 指标为 `heldout_test_metrics.csv`，recovery 和 fallback 证据为
`recovery_summary_0_5db.csv` / `recovery_summary_1db.csv`，生产 CFAR 逐行及 bootstrap
证据为 `production_cfar_rows.csv` / `production_cfar_bootstrap_test.csv`。
