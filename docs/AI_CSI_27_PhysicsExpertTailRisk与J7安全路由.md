# AI-CSI-27：Physics Expert Tail Risk 与确定性 J7 安全路由

## 目的和输入

Phase C 不训练 AI，只审计 V2 physics-only 结果中的 J6 tail risk，并实现一个
可审查、可复现、只读 inference-visible observable 的 J7 selector。输入为：

- `outputs/physics_adaptive_selective_v2_formal/formal_matrix_manifest.json`；
- `outputs/physics_adaptive_selective_v2_formal/recovery_metrics.csv`。

J6 tail-risk 的严格过滤是 `split=test AND role=target_off`。本次严格数据包含
48 条 J5 和 48 条 J6 recovery rows；validation、target-on 和 calibration 不
进入 J6 held-out tail 统计。

复现命令：

    python3 scripts/audit_physics_expert_tail_risk.py \
      --formal-dir outputs/physics_adaptive_selective_v2_formal \
      --output-dir outputs/physics_expert_tail_risk

全程 `ai_training=false`；脚本为离线读取和汇总，不启动 CUDA、不生成训练文件。

## J6/J5 tail risk 实际结果

| 方法 | n | mean (dB) | median | p05 | p10 | min | CVaR5 | `<−0.1` | `<−0.5` | `<−1.0` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| J5 | 48 | +0.0611 | +0.0004 | −0.0344 | −0.0126 | −0.6324 | −0.2480 | 2.08% | 2.08% | 0% |
| J6 | 48 | −0.2123 | 0 | −0.1139 | 0 | −16.2851 | −5.6397 | 6.25% | 2.08% | 2.08% |

J6 的均值不能掩盖极端负尾，因而不能作为“专家安全”的充分证据。输出还按
family、branch、angle、range、SNR、radial velocity、clutter ridge、texture
和 rho 分层，保留 mean/median/p05/p10/p90/min/CVaR5 及三个负阈值比例。

## Inference-visible 特征与 J7 规则

`inference_visible_features.csv` 只包含 split/scene_id 和以下 observable-derived
字段，不含 `actual_mechanism`、truth 参数、family、SNR、角度、range、速度、
clutter ridge、texture 或 rho：

- joint confidence、joint RMSE、residual coherence、raw coherence、support fraction；
- tau、beta1、beta2、D3-vs-J6 delay disagreement、P1-vs-J6 phase disagreement；
- delay/phase confidence、delay/phase signal、delay extrapolation ratio。

确定性规则在 `scripts/safe_expert_selector.py`：

1. 缺失、退相关、质量不足、不一致或外推时回退 `J0_Current`；
2. 高置信 delay-only 选择 J5；
3. 高置信 phase 或 mixed 选择 J6；
4. 零/弱信号保持 Current。

代码测试覆盖 `safe_expert_zero_identity` 和
`safe_expert_decorrelation_fallback`，另覆盖 delay/phase 正常路由。

阈值 profile 只用 calibration+validation 的 physics gain 选择，Test-V2 不参与
选择。实际选择为 `strict`：joint confidence≥0.80、joint RMSE≤0.45、residual
coherence≥0.90、raw coherence≥0.82、delay disagreement≤0.50 ns、phase
disagreement≤0.15 deg/pulse 等条件。Test-V2 结果：

- J7 选择 Current 55/64、J6 8/64、J5 1/64；专家调用率 14.06%；
- 全 test 选择结果 mean −0.0028 dB，p05=0，min −0.4624 dB，CVaR5 −0.1204 dB；
- 严格 recovery 覆盖的 48 个 scene 中，Current 40、J6 8、J5 0；均值 −0.0034 dB，
  `<−0.5 dB` 和 `<−1 dB` 均为 0，但 J6 选择子集仍有 −0.4624 dB 样本。

这些数字只说明一个保守的 physics-only 路由可以减少专家调用和极端 tail 暴露，
不等价于已通过目标保护或生产 CFAR gate。Phase D 仍需 evaluation-only
Target-Safe Oracle，且 AI 训练继续关闭。

## 证据

`outputs/physics_expert_tail_risk/` 保留紧凑的 tail rows、分层汇总、inference-visible
features、profile selection、test eval 和 manifest；没有产生或保留 raw BIN/NPY/F32/XML/
log/PNG 中间产物。

## Phase D Target-Safe Oracle 实际结果

复现命令：

    python3 scripts/evaluate_target_safe_oracle.py \
      --output-dir outputs/target_safe_oracle \
      --build-dir build \
      --formal-dir outputs/physics_adaptive_selective_v2_formal

这是 evaluation-only 的 12-scene paired replay，覆盖 transition 附近的 14/16 dB、
六个 mismatch family；每个 scene 使用同一 OFF/ON/TO 背景和 OFF-derived frozen
参数，按 `{0,0.25,0.5,0.75,1}` 缩放 estimated delay/phase correction。lambda=0
与 Current identity 相同，lambda=1 是完整 frozen correction replay。不会用该结果
拟合 J7，也不进入 AI 训练。

安全约束预先固定为：每个 target 的 `L_causal >= −0.25 dB`、总体及每个 SNR
点 paired causal Pd 不低于 Current、target-off mean Pfa delta≤0、target-off mean
false-cluster delta≤0。实际 Current baseline 为 paired Pd=`3/12=0.25`、Pfa
`0.0083863`、false clusters `29.1667`。

| 方法 | lambda | `L_causal` min (dB) | paired Pd | ΔPfa | Δfalse clusters | safe |
|---|---:|---:|---:|---:|---:|---:|
| J5 | 0 | 0.000 | 0.25 | 0 | 0 | 是 |
| J5 | 0.25 | −10.633 | 0.25 | +3.26e−5 | +1.75 | 否 |
| J5 | 0.50 | −10.899 | 0.25 | +1.78e−5 | +2.00 | 否 |
| J5 | 0.75 | −10.167 | 0.25 | +1.64e−5 | +1.75 | 否 |
| J5 | 1 | −9.263 | 0.25 | +2.41e−5 | +2.08 | 否 |
| J6 | 0 | 0.000 | 0.25 | 0 | 0 | 是 |
| J6 | 0.25 | −10.812 | 0.25 | +3.93e−5 | +1.83 | 否 |
| J6 | 0.50 | −4.810 | 0.25 | +3.26e−5 | +1.33 | 否 |
| J6 | 0.75 | −8.649 | 0.25 | +1.62e−5 | +2.00 | 否 |
| J6 | 1 | −9.479 | 0.25 | +3.58e−5 | +2.33 | 否 |

因此 Phase D 的 physics-only safe headroom 为 J5=`0`、J6=`0`；非零 correction
strength 在本次 paired audit 上不能通过目标保护与背景 guardrail。结果支持
`Current-only` 的保守路由，不支持把 lambda 作为可部署增益，也不支持开放 AI。

证据位于 `outputs/target_safe_oracle/`，包括逐 lambda compact transfer/detection/
CFAR、summary、constraints、manifest 和资源快照；per-scene raw 中间结果已删除。
