# AI-CSI-28：Physics-AI 最终 Go/No-Go

## 最终结论

**`FINAL_NO_GO_AI`**。

Test-V3 的 physics-only 证据不支持开放 AI 训练、AI 路由部署或构造训练集。
J7 的 observable-only selector 没有 truth leakage，但它在 Test-V3 上仍有目标
传递尾风险和背景 false-cluster/Pfa 回归；Phase-D Target-Safe Oracle 的非零
lambda 也全部未通过安全约束。Oracle identity 通过仅说明 Current 自身没有相对
Current 的回归，不能算作 expert headroom。

## 固定矩阵和实际运行

冻结配置：`configs/research/physics_ai_final_gate_v3.json`。实际运行命令：

    python3 scripts/run_physics_ai_final_gate_v3.py \
      --config configs/research/physics_ai_final_gate_v3.json \
      --output-dir outputs/physics_ai_final_gate_v3 \
      --build-dir build \
      --formal-dir outputs/physics_adaptive_selective_v2_formal

离线判定命令：

    python3 scripts/evaluate_physics_ai_final_gate.py \
      --output-dir outputs/physics_ai_final_gate_v3 \
      --config configs/research/physics_ai_final_gate_v3.json

Test-V3 实际为 64/64 scene，通过 8 mechanism families × 8 SNR levels（10–24 dB），
seed `2026120000–2026120063`；OFF/ON/TO 使用同场景背景配对。运行 source commit
为 `3a11d03`，输出 320 条 transfer、320 条 detection、640 条 production CFAR
rows；`ai_training=false`。GPU 资源快照保存在 output 目录，per-scene raw 树已清理。

## 冻结 guardrails

- 每个 target 的 `L_causal >= −0.25 dB`；
- 总体及每个 SNR 点 paired causal Pd 不低于 Current；
- target-off 平均 Pfa delta≤0；
- target-off 平均 false-cluster delta≤0；
- `ai_training` 必须保持 `false`。

## Test-V3 实际结果

| 方法 | paired causal Pd | `L_causal` mean / p05 / min (dB) | target-off ΔPfa | Δfalse clusters | 通过 |
|---|---:|---:|---:|---:|---:|
| Current | 35/64 = 0.5469 | 0 / 0 / 0 | 0 | 0 | 基线 |
| J5 | 38/64 = 0.5938 | +0.0059 / −0.0078 / −4.6976 | +1.79e−5 | +1.7656 | 否 |
| J6 | 37/64 = 0.5781 | −0.2693 / −1.1899 / −16.6634 | −5.36e−6 | +2.9844 | 否 |
| J7 | 35/64 = 0.5469 | +0.0027 / 0 / −0.4315 | +2.85e−5 | +1.2813 | 否 |
| Oracle lambda=0 | 35/64 = 0.5469 | 0 / 0 / 0 | 0 | 0 | identity 基线 |

J7 的 paired Pd 在每个 SNR 点均不低于 Current，且 selector 输入只含
joint confidence/RMSE/residual coherence、delay/phase observable、不一致度、
raw coherence、support fraction 和 extrapolation 等 inference-visible 字段；
离线检查的 forbidden feature columns 为空。因此本次 No-Go 不是由标签泄漏造成，
而是由目标保护和背景 guardrail 的实际失败造成。

J7 在 Test-V3 只选择 J6 `11/64`，其余 `53/64` 回退 Current；没有选择 J5。
这说明 selector 的保守回退有效降低了调用率，但仍不能消除被调用 J6 的目标
传递负尾和 background cluster 回归。

## 与 Target-Safe Oracle 的关系

Phase D 使用 12 个 transition scenes、lambda `{0,.25,.5,.75,1}` 做 frozen
OFF-derived correction replay。J5/J6 的最大 safe lambda 都是 `0`：非零 lambda
至少违反 causal transfer floor，且同时观察到 target-off Pfa/false-cluster
增加。证据见 `outputs/target_safe_oracle/oracle_constraints.csv` 和
`docs/AI_CSI_27_PhysicsExpertTailRisk与J7安全路由.md`。

## 判定边界和未完成事项

- 本结论是当前源码、配置、Test-V3 输入和 RTX 3050 Laptop GPU 状态下的 physics-only
  final gate；不能外推到未测设备、功率状态或更大场景分布。
- Pfa/false-cluster 是本矩阵的 paired scene 描述统计，不替代更大规模设备泛化
  验证；但当前 guardrail 已失败，因此没有理由先扩大 AI 范围。
- `REOPEN_AI_ROUTER` 未触发。若未来要重新开启，只能先提交新的冻结设计，重新
  建立目标保护、Pd、Pfa/cluster 和 selector leakage 证据；在用户明确授权并且
  新 gate 通过前，`ai_training` 必须保持 `false`。

## 证据路径

- 配置：[physics_ai_final_gate_v3.json](../configs/research/physics_ai_final_gate_v3.json)
- Test-V3 manifest：[physics_ai_final_gate_v3_manifest.json](../outputs/physics_ai_final_gate_v3/physics_ai_final_gate_v3_manifest.json)
- 方法判定：[final_gate_method_results.csv](../outputs/physics_ai_final_gate_v3/final_gate_method_results.csv)
- 最终 JSON：[physics_ai_final_gate_decision.json](../outputs/physics_ai_final_gate_v3/physics_ai_final_gate_decision.json)
- Target Transfer：[AI_CSI_26_TargetTransfer与目标保护审计.md](AI_CSI_26_TargetTransfer与目标保护审计.md)
- J6/J7/Oracle：[AI_CSI_27_PhysicsExpertTailRisk与J7安全路由.md](AI_CSI_27_PhysicsExpertTailRisk与J7安全路由.md)
