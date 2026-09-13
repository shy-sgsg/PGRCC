# AI-CSI-33：Router Learnability 审计

> 研究主线更新（2026-09-13）：本文是 Router 分支的历史前置审计说明。由于 Materiality 已为 `NO_GO_AI_ROUTER_VALUE`，该 learnability 路径不进入当前主线；请转读 [真实系统误差参数与可观测性分析](AI_CSI_33_真实系统误差参数与可观测性分析.md)。

## 前置条件

只有 Router Materiality Gate 通过后，才可运行 learnability audit。脚本只读取
`router_opportunity_v1` 的 compact CSV，不重新打开 raw BIN/NPY/F32/log，也不运行
神经网络。

```bash
python3 scripts/audit_router_learnability.py \
  --output-dir outputs/router_opportunity_v1 \
  --config configs/research/router_opportunity_v1.json
```

## 输入与标签

模型输入只来自 `router_inference_visible_features.csv` 的 OFF/Current observable
和 geometry-visible fields。`family`、`seed` 和 target/action 结果留在审计旁路，
不进入 feature matrix。脚本会拒绝 mechanism truth、true delay/phase/rho、target
truth、`L_causal`、safe flag、Oracle action/gain、Pd/Pfa 等字段。

标签为：

- `Y1 = material_safe_opportunity`：`safe_headroom_db >= 0.10 dB`；
- `Y2 = best_safe_action`。

## 审计内容

脚本先计算 effect size、Spearman rank correlation、mutual information 和跨 split
feature stability，然后运行：

- J7.1 deterministic rule；
- logistic regression；
- depth ≤ 3 shallow tree。

验证采用 seed-out、angle-out、family-out split。低 confidence 统一回退 A0 Current。
主安全指标为 safe-action precision、unsafe activation rate、catastrophic
target-loss activation rate、abstain rate 和 captured safe headroom。

即使审计条件满足，脚本仍保持 `ai_training=false`，不自动修改配置、不创建 source
commit、不训练 MLP。只有 Materiality、observable-only learnability 和 deterministic/
simple selector 未恢复大部分 Safe Oracle headroom 同时成立时，输出状态才会进入
`GO_AI_ROUTER_TRAINING` 候选；真正的训练授权仍需独立 source commit 和显式配置变更。
