# AI-CSI-29：Target-Safe Oracle v2 审计

## 结论

Target-Safe Oracle v2 在 12 个定向 transition scenes 上实际完成了 Current 与
J5/J6 的 `lambda={0.25,0.5,0.75,1}` replay，并按 scene 独立选择满足安全约束且
clutter cancellation 最大的候选。平均 `safe_oracle_headroom_db=+0.0665415 dB`；
共有 5 个 scene 选择非零候选（J5 1 个、J6 4 个），其余 7 个 scene 保留 Current
identity。这个结果证明仍有小的、可测的安全余量，但不证明某个固定 correction
或全矩阵方法已经安全。

## 安全约束与选择

- 每个 target 的 `L_causal >= -0.25 dB`；
- Current 已检出 target 不得丢失；
- target-off Pfa 和 false-cluster 相对 Current 均不得增加；
- 选择目标为每个 scene 的最大 clutter cancellation，Current identity 始终是安全回退；
- `ai_training=false`，Oracle 仅为 evaluation-only，不参与训练或阈值拟合。

选择结果为：`000/002/003/006/008/009/011 -> Current`，`001 -> J6 lambda=0.5`，
`004/005 -> J6 lambda=0.75`，`007 -> J5 lambda=0.5`，`010 -> J6 lambda=1`。
因此不能把 J5/J6 的全局固定 lambda 均值当成安全结论。

## 对最终门禁的影响

修正 provenance 布尔值序列化后，实际执行最终门禁得到：

```text
decision = REOPEN_AI_ROUTER
safe_oracle_headroom_db = +0.0665415
deterministic_solved_methods = []
```

这里的 `REOPEN_AI_ROUTER` 只表示“安全 Oracle 仍有残余余量，当前确定性方法尚未
恢复该余量”，不是训练或部署授权。J7 安全本身不能触发重开；在任何 AI 工作前仍须
保持 `ai_training=false` 并重新建立冻结的 router 验证证据。

当前配置明确 `test_v3_development_only=true`：Test-V3 的 J5/J6/J7 指标仍保留为
development diagnostics，但不参与最终分类或 gate reasons。最终分类只使用已证明的
Target-Safe Oracle headroom 与独立的 deterministic evidence 状态；当前没有后者，
所以不是把 Test-V3 的目标指标当作 AI 重开依据。

## 运行身份、限制与证据

该实验是 12-scene 定向开发审计，不是 Test-V3 全矩阵。运行 source commit 为
`f2a9fe0`，start provenance 的 `source_worktree_dirty_before=false`；post-run
dirty 是输出写入造成的正常状态。manifest 中曾因布尔序列化顺序把 bool 写成 `0/1`，
后续仅修复 JSON 表示，未改变任何测量值。

- 配置：[target_safe_oracle_v2.json](../configs/research/target_safe_oracle_v2.json)
- manifest：[target_safe_oracle_v2_manifest.json](../outputs/target_safe_oracle_v2/target_safe_oracle_v2_manifest.json)
- scene 选择：[oracle_scene_selection.csv](../outputs/target_safe_oracle_v2/oracle_scene_selection.csv)
- 候选指标：[oracle_candidate_metrics.csv](../outputs/target_safe_oracle_v2/oracle_candidate_metrics.csv)
- 最终门禁：[physics_ai_final_gate_decision.json](../outputs/physics_ai_final_gate_v3/physics_ai_final_gate_decision.json)
