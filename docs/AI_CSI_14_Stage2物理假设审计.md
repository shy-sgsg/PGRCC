# Stage2 物理假设审计

审计范围：`simulator/stage2_statistical_sim/stage2_lfm_forward.cpp`、`stage2_config.cpp/.h`、`simulate_stage2_main.cpp`、`stage2_validator.cpp`，以及 `simulator/target_injection/channel_impairments.cpp`。

## F1≈alpha F2 的必要条件

| 条件 | 当前状态 | 证据/含义 |
|---|---|---|
| 1. background beam/time physical consistency | 已修复并 CUDA 验证 | source group 按 physical theta 选择；header 保留 source UTC/位置/姿态；见 Phase A 报告 |
| 2. common clutter realization | 基线满足，非理想实验需单独标注 | continuous surface 使用确定性 world-coordinate cell/hash，四通道共享 cell reflectivity；通道几何仍可造成 channel-specific summed texture |
| 3. sufficient channel coherence | 生产有 H0 gate | row rho<0.5 保留 channel 1、跳过非线性 equalization/subtraction；不能把 bypass 行和 coherent rows 混合解释 |
| 4. correct CTDR | 当前实现存在并由 CUDA 主链调用 | `rg_correct`/P38/CSI tap 分阶段记录；其误差仍可能表现为 phase residual |
| 5. residual delay negligible | 未假设为永真 | M1 以 raw fractional delay 0/2.083/4.167/8.333 ns challenge；强档 Current cancellation 下降到 `13.37 dB` |
| 6. P38 phase model adequate | 仅是窄/低阶模型假设 | M1 residual 随 delay 增长；不能靠继续调 constant P38 代替 delay model |
| 7. alpha stationary over CPI | M2 challenge 证明可被破坏 | per-pulse drift 的 truth slope 与配置一致，强档 slow-time fit `R²=0.9998` |
| 8. amplitude relation stable | 当前 min-amplitude equalization 只处理局部幅度 | probe coherent rows amplitude residual std `6.18 dB`，需与 phase/delay 分开分析 |
| 9. no severe spatial/channel decorrelation | 不是默认保证 | M3 在 surface cell slow-time 上使用共享 AR(1) reflectivity；rho=1 strict regression，rho<1 时 Current 取消增益明显下降 |

## M3 实现

`AreaClutterConfig.temporal_correlation_rho` 已加入 JSON 解析、默认配置、校验、resolved scenario 和 validator 报告。连续面元使用 physical cell key 的 deterministic AR(1) 状态：

```text
gamma[p+1] = rho * gamma[p] + sqrt(1-rho^2) * epsilon[p]
```

`rho=1` 直接返回静态 cell reflectivity，避免任何随机状态改变；`rho<1` 在 surface/scatterer slow-time 层变化，没有向最终 RD 图直接加噪声，也没有让四通道完全独立。

## 审计边界

上述条件是“模型可解释性前提”，不是声称真实环境一定满足。M1/M2/M3 已在同一紧凑场景上完成 3 个独立 seed，并补齐 36 个 target-only 与 36 个 negative-control 生产 CUDA 控制，足以确认本轮机制方向、Oracle headroom 以及 compact 场景的 Pd/Pfa/target-loss 口径；但不足以给出生产环境的统计 failure boundary。geometry、heterogeneous clutter、spatial decorrelation 仍未进入本轮正式 challenge。
