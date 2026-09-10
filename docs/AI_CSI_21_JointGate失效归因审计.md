# AI-CSI-21：Joint Gate 失效归因审计

## 结论

本审计只读复用 Physics-Adaptive Joint Calibration V1 的紧凑正式 manifest，未重跑 CUDA、未修改 V1 结果，也没有把机制标签送入估计器或门控器。审计源提交为 `d9f5b883926433414d87b4086976190101f5da56`，`ai_training=false`。

V1 的主要问题不是已经证明 D3/P1 估计器本身有很大的确定性残差，而是一个 global all-or-nothing gate 把 delay 与 phase 绑在一起：一个分量质量不足时，另一个分量即使独立满足置信度、拟合误差和支持范围条件，也只能整体 fallback。下面的 independent 标记是基于 V1 已保存 observable 和 V1 null 阈值的 counterfactual reconstruction，不是 J5/J6 的实测激活结果。

## 审计口径

- `gate_failure_rows.csv` 每行对应一个 V1 场景、`target_off/target_on` 角色和一个 correction method（J1–J4），共 512 行；V1 的 J0 保持为 Current，不需要重复列入 correction 分支。
- `independent_delay_active` 和 `independent_phase_active` 分别移除对另一分量质量的 veto，但仍保留 V1 的 coherence、confidence、RMSE、支持范围和 deadband 条件。
- `fallback_opportunity_cost_db = Oracle cancellation - Current cancellation`，只作为已保存 Oracle headroom 的审计指标，不把 truth 用于运行时决策。
- V1 test 只有 7 个 non-zero family，且没有 held-out zero family；因此以下 test 统计不能作为最终 false-activation 泛化证据。

## M1/M2 归因

| 审计切片 | 行数/场景数 | global uncertain | global decorrelated | V1 fallback | 独立 delay 激活 | 独立 phase 激活 | V1 会阻断 delay | V1 会阻断 phase | uncertain 且 headroom >0.5 dB | >1 dB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| M1 delay | 160 / 20 | 64 | 40 | 118 | 96 | 32 | 64 | 40 | 24 | 8 |
| M2 phase | 160 / 20 | 80 | 8 | 100 | 56 | 96 | 16 | 64 | 48 | 32 |
| M1+M2 delay branch | 40 / 5 | 8 | 8 | 16 | 24 | 32 | 16 | 8 | 8 | 8 |
| M1+M2 phase branch | 40 / 5 | 8 | 8 | 16 | 24 | 32 | 16 | 8 | 8 | 8 |

这里“V1 会阻断”表示该分量在 V1 的全局门控逻辑下不能进入独立校正，并不表示所有阻断都是错误：在 coherence 真正不足时，global veto 仍然是必要的。关键证据是 M1/M2 中存在独立分量满足条件、但另一分量或全局 coherence 使旧 gate fallback 的行；其中有 uncertain 行仍有超过 0.5 dB、甚至 1 dB 的 Oracle headroom，说明需要先拆分状态机再评估可恢复收益。

## 产物与复现

```bash
python3 -m py_compile scripts/audit_joint_gate_failure.py
python3 scripts/audit_joint_gate_failure.py \
  --manifest outputs/physics_adaptive_joint_v1_formal/formal_matrix_manifest.json \
  --output-dir outputs/joint_gate_failure_audit
```

保留的高价值证据：

- `outputs/joint_gate_failure_audit/gate_failure_rows.csv`：逐行状态、独立分量证据和 headroom；
- `outputs/joint_gate_failure_audit/gate_failure_by_family.csv`：按 split/family/method 汇总；
- `outputs/joint_gate_failure_audit/activation_confusion.csv`：M1/M2 与混合分支的核心计数；
- `outputs/joint_gate_failure_audit/fallback_opportunity_cost.csv`：可追溯的 fallback 机会成本；
- `outputs/joint_gate_failure_audit/audit_manifest.json`：源 manifest、提交身份和字段定义。

下一阶段应只允许 decorrelation 作为 global veto，把 delay 与 phase 改成独立状态和 D0P0/D1P0/D0P1/D1P1 分支；随后在 calibration/validation 冻结正向质量门槛，并用新的 held-out zero/Test-V2 检验 false activation、Pfa、Pd 和 preservation。
