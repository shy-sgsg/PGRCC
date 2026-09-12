# AI-CSI-30：Target-Safe 失败机制审计

## 范围

对 `test_v3_010/018/022/050` 四个 Test-V3 失败代表做 development-only
mechanism audit，比较 OFF 派生 clutter correction 与 target-only differential
phase surface、target coherence、`beta2` 和 Joint 相对 P1 的 residual-coherence
增益。该审计不改 Test-V3 gate，也不把 target truth 指标作为 inference 输入。

## 实际结果

四个 scene 的 `nonlinear_surface_evidence` 均为 `false`；`|beta2|` 仅为
`8.43e-6` 到 `9.79e-5 deg/pulse²`，远低于 J7.1 的 `0.005` 阈值。Joint 相对
P1 的 residual-coherence 增益虽为 `0.0624`–`0.0709`，但不能单独证明应启用 J6。

| scene | `|beta2|` | Joint−P1 residual coherence | J5 target-only transfer (dB) | J6 target-only transfer (dB) | 判读 |
|---|---:|---:|---:|---:|---|
| `test_v3_010` | 8.43e-6 | 0.0632 | −0.0079 | −0.3481 | nearly-linear，J6 overcorrection suspected |
| `test_v3_018` | 4.19e-5 | 0.0693 | +0.0010 | +0.0282 | nearly-linear，未触发 overcorrection 标记 |
| `test_v3_022` | 9.69e-5 | 0.0709 | +0.0005 | +0.0035 | nearly-linear，未触发标记 |
| `test_v3_050` | 9.79e-6 | 0.0624 | +0.0052 | +0.2454 | nearly-linear，未触发标记 |

`test_v3_010` 的 J6 target-only transfer 为 −0.348 dB，低于配置的 −0.25 dB
安全下界，因此被明确标为疑似过校正。理论 spectral CSI attenuation 在四个 scene
均为 0 dB；这是因为该审计中的 delay/phase 操作是功率守恒的 unitary transform，
不能替代生产目标传递和检测安全性判断。

## J7.1 规则

J7.1 只有在 observable-only 的 `|beta2|` 和 Joint−P1 residual-coherence 增益
同时超过冻结阈值时才允许 J6，否则优先 J5/P1 或 Current 回退。该规则不读取
target truth、`L_target_only` 或 detection label。四个审计 scene 的 `beta2` 都不
满足阈值，所以这批 evidence 支持“避免对近线性 phase surface 误用 J6”，但不构成
J5 全局安全通过。

证据：[mechanism_surface_rows.csv](../outputs/target_safe_failure_mechanism_audit/mechanism_surface_rows.csv)。
该结果为 Test-V3 development evidence；没有开启 AI 训练，`ai_training=false`。

