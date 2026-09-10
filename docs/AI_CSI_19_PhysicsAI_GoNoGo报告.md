# AI_CSI_19：Physics-AI Go/No-Go

## 当前结论

当前结论为 **`NO_GO_AI_FOR_NOW`**：不启动训练，不把 AI 引入 PGRCC-v2。这个结论
不是宣称未来永远不需要 AI，而是表示目前 deterministic observable estimator、
显式物理 correction 和 Current 生产链已经提供足够的可解释证据；同时 mixed/OOD
诊断仍不稳定，尚未证明有一个清晰、可预测且必须由 AI 填补的 residual gap。

## 已完成证据

1. **M3 模型与 floor**：AR(1) 初始化已修正；rho=0.99/0.95/0.8 的实测 lag-1
   分别为 0.987858/0.950564/0.801862。strong M3 的 O1–O5 机制感知诊断 oracle
   候选中，最佳观测到的诊断 headroom 只有 +0.2746 dB；O3/O4/O5 相对 Current
   反而为负。它们不是数学上界或生产收益证明；后验 O1–O5 fixed-Pfa diagnostic
   没有 causal hit。
2. **M1/M2 可观测估计**：strong M1 D3=`8.3095 ns`（truth 8.3333 ns），strong
   M2 P1=`−0.4924 deg/pulse`（期望观测 −0.5）；独立生产 replay 分别达到约
   99.93% M1 known-Oracle gain 和同量级 M2 Oracle gain。
3. **多 seed 与 mixed/OOD**：已有 seed 2026091011/1012/1013 的 36 个 single-factor
   production variants；另有 12 个 mixed/OOD case（四种组合各三例），全部生产与
   raw estimator audit 通过。compact evidence 为
   `outputs/mechanism_mixed_design/manifest.json`、`mechanism_feature_table.csv`、
   `mechanism_confusion_matrix.csv`。
4. **检测语义**：四个 mixed/OOD 代表场景均完成 target-only/negative-control；
   Current 在经验 Pfa=0.01 均配对命中，M1+M2 和全混合在 0.001 仍命中，M1+M3/M2+M3
   在 0.001 未命中。M1+M2 D3、M2+M3 P1 的校正后 paired Pfa 也没有观察到 target
   preservation 回归，证据在 `outputs/causal_detection_audit/mixed_*_b/`。

## 为什么不是 GO

- mixed/OOD 的规则诊断有明确混淆：M1+M2、M1+M3、全混合会在 delay-like、
  phase-drift-like、decorrelation-like、unknown 之间跳变；这说明 inference-visible
  feature 仍需机制联合建模，但尚未给出稳定的 estimator→Oracle residual gap。
- M1/M2 的 mixed replay headroom 在不同组合明显变化：M1+M2 的 M1 D3 为 +7.737 dB，
  但全混合 D3 仅 +0.264 dB、P1 仅 +0.171 dB；M3 相关场景的 deterministic headroom
  已明显收缩。不能用单一 strong 单因素数字证明 AI 必要。
- 当前 12 个 mixed/OOD case 是小设计，不是每个 family 48–96 个 scene 的最终统计；
  paired Pfa correction 也只对代表场景完成，尚不具备跨 beam/range/SNR 的置信区间。

因此保留 deterministic physics correction、confidence/fallback 和机制诊断为下一步，
暂不构造 AI 训练集、不训练、不修改生产输出协议。

## 若未来重新评估 GO

只有在新增实验同时满足以下条件时，才重新打开 Physics-AI gate：

1. 多 seed/beam/range/SNR/速度和 mixed/OOD 中，known Oracle headroom 稳定存在；
2. raw-observable estimator 在不使用 truth 时能稳定恢复物理量；
3. estimator→Oracle gap 可重复，且可由可见机制特征/confidence 预测；
4. 校正后的 matched empirical Pfa、causal Pd 和 target preservation 无回归；
5. 预先固定统计口径和置信区间，而不是看结果后调门槛。

若这些条件仍不成立，应继续使用 `NO_GO_AI_FOR_THIS_MECHANISM`；若出现稳定、可预测、
且 deterministic correction 无法覆盖的 gap，AI 输出也只能是 `delta_tau`、低维
phase trajectory 或 decorrelation confidence，不输出无约束 RD image。

## 不训练声明

本阶段所有脚本和实验 manifest 均记录 `ai_training=false`。没有生成训练集、没有安装
额外依赖、没有改变 Current/CFAR/跟踪协议。
