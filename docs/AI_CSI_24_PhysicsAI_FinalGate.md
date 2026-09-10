# AI-CSI-24：Physics-AI Final Gate

## 当前判定

本阶段仍是 `ai_training=false`。V2 targeted screen 的规则输出为 `REOPEN_PHYSICS_AI`，但它只说明 screen 中存在实际激活的 selective branch，不能替代 fresh Test-V2，也不能授权训练。最终 gate 当前状态是 **待 Test-V2 formal 完成**。

## 最终 gate 规则

`NO_GO_AI_M1_M2`：若 J5/J6 在 material Oracle headroom（主阈值 0.5 dB，并报告 1 dB sensitivity）上有稳定的 deterministic recovery，同时 production Pfa、false clusters、causal Pd 和 target preservation 相对 Current 没有回归，则继续不训练 AI，并把确定性物理校准作为结论。

`REOPEN_PHYSICS_AI`：只有当 J5/J6 在 fresh Test-V2 中真实激活、上述生产指标无回归、但 material recovery 仍不足且 residual gap 可重复时才输出。这个标签只允许重新审查物理估计器和门控定义，**不授权训练**。

任何方法如果只在 Oracle 或机制标签帮助下有效、只改善 score-map diagnostic、或无法通过 held-out zero 的 false activation 检查，都不能进入 AI gate。

## 必须完成的 Test-V2

- 新 seed/LHS/family；48–64 null calibration、16–24 validation、64 held-out test；test 为 8 families × 8，含 held-out zero；
- J0–J6 + evaluation-only Oracle；质量 gate 只看 calibration+validation；
- production GO-CFAR valid CUT、cluster、causal Pd、target preservation；scene-block bootstrap；
- 0.5 dB 主 material threshold 和 1 dB sensitivity；报告 all-scene gain、activation-conditioned recovery、material recovery、fallback opportunity cost；
- 清理 raw runtime 后保留 manifest/CSV/JSON/必要图，记录 source commit、dirty 状态、输入/资源和 `ai_training=false`。

## 当前证据

screen 的 compact manifest 在 `outputs/physics_adaptive_selective_v2_screen/formal_matrix_manifest.json`；J5/J6 分支、生产 CFAR、bootstrap 和 recovery 证据分别在同目录的 `scene_summary.csv`、`production_cfar_rows.csv`、`production_cfar_bootstrap.csv`、`recovery_metrics.csv` 与 `recovery_summary_0_5db.csv`。这些是定向筛查证据，不作为最终 Test-V2 结论。
