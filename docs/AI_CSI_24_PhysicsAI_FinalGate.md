# AI-CSI-24：Physics-AI Final Gate

## 当前判定

全流程仍是 `ai_training=false`。Phase B 的 paired Target Transfer、Phase C 的
J6 tail/J7 audit、Phase D 的 Target-Safe Oracle 和 fresh Test-V3 已完成。正式
Test-V3 final gate 当前判定为 **`FINAL_NO_GO_AI`**：J7 虽然保持 paired causal
Pd 不低于 Current，但 causal target-transfer floor、target-off Pfa 和
false-cluster guardrail 未同时通过。Oracle 的 safe lambda 只有 identity `0`，
不是专家增益。该判定不授权任何 AI 训练或部署。

## 最终 gate 规则

`NO_GO_AI_M1_M2`：若 J5/J6 在 material Oracle headroom（主阈值 0.5 dB，并报告 1 dB sensitivity）上有稳定的 deterministic recovery，同时 production Pfa、false clusters、causal Pd 和 target preservation 相对 Current 没有回归，则继续不训练 AI，并把确定性物理校准作为结论。

`REOPEN_AI_ROUTER`：只有当 fresh Test-V3 的非 identity J7 路由同时满足目标
传递、paired Pd 和背景 guardrails，且只是剩余 residual gap 需要路由复审时才可
考虑。这个标签只允许重新审查路由/物理定义，**不授权训练**。本次未触发。

任何方法如果只在 Oracle 或机制标签帮助下有效、只改善 score-map diagnostic、或无法通过 held-out zero 的 false activation 检查，都不能进入 AI gate。

## 已完成的 Test-V2

- 新 seed/LHS/family；48 null calibration、16 validation、64 held-out test；test 为 8 families × 8，含 held-out zero；
- J0–J6 + evaluation-only Oracle；质量 gate 只看 calibration+validation；
- production GO-CFAR valid CUT、cluster、causal Pd、target preservation；scene-block bootstrap；
- 0.5 dB 主 material threshold 和 1 dB sensitivity；报告 all-scene gain、activation-conditioned recovery、material recovery、fallback opportunity cost；
- 清理 raw runtime 后保留 manifest/CSV/JSON/资源快照，记录 source commit、dirty 状态、输入/资源和 `ai_training=false`。

## Formal 判定证据

原始 CUDA 运行来自 `669df38` 的 clean worktree；摘要修正来自 `1445f3f`，没有重新
运行。历史 V2 aggregate 的 J5/J6 material recovery median 为 `0.8426/1.0249`，
但它混合了 role 和 split，不能作为纯 held-out Test-V2 recovery。Phase A 的严格
重算只保留 `split=test AND role=target_off`，结果与 paired CFAR 见
[`AI_CSI_25_V2FinalGate方法学审计.md`](AI_CSI_25_V2FinalGate方法学审计.md)。
当前 gate 的正确含义是“V2 证据不足以确认最终安全性”，不是已经证明 target
transfer regression 的物理根因。

## 当前证据

screen 的 compact manifest 在 `outputs/physics_adaptive_selective_v2_screen/formal_matrix_manifest.json`；J5/J6 分支、生产 CFAR、bootstrap 和 recovery 证据分别在同目录的 `scene_summary.csv`、`production_cfar_rows.csv`、`production_cfar_bootstrap.csv`、`recovery_metrics.csv` 与 `recovery_summary_0_5db.csv`。这些是定向筛查证据，不作为最终 Test-V2 结论。

最终 formal manifest 为 `outputs/physics_adaptive_selective_v2_formal/formal_matrix_manifest.json`；
held-out gate 指标为 `heldout_test_metrics.csv`，recovery 和 fallback 证据为
`recovery_summary_0_5db.csv` / `recovery_summary_1db.csv`，生产 CFAR 逐行及 bootstrap
证据为 `production_cfar_rows.csv` / `production_cfar_bootstrap_test.csv`。

最终 Test-V3 的完整判定、逐方法 guardrail、J7 selector 计数和限制见
[`AI_CSI_28_PhysicsAI最终GoNoGo.md`](AI_CSI_28_PhysicsAI最终GoNoGo.md)。
