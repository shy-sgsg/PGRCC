# AI_CSI_18：M1/M2 物理参数估计器

## 观测边界

M1/M2 estimator 的生产输入是 raw fast-time channel 1/2 samples，不是 post-Doppler
CSI tap。后者的 range axis 是物理距离，不能直接当作 fast-time frequency；将旧 tap
误作频率轴会得到错误的 delay，并在 correction replay 中造成明显退化。代码现在要求
显式选择 `--raw-bin` 或旧的 `--manifest` diagnostic mode，并在 JSON 中记录
`input_mode` 和 `truth_used_in_estimator=false`。

## M1：通道差分延迟

raw 模式形成

```text
C12(f) = sum_p X1[p,f] * conj(X2[p,f])
angle(C12) = b + 2*pi*f*delta_tau + epsilon
```

提供 D1 ordinary LS、D2 magnitude/support weighted LS、D3 Huber weighted LS；
输出逐频率支持、斜率、R²、RMSE、confidence 和 `delta_tau_ns`。频域校正使用
独立的 zero-padded FFT phase ramp，不复用 simulator 的线性插值实现。

在 `outputs/mechanism_estimators/probe_m1_seed2026091011/estimator_raw/` 的四档
生产 raw probe 中，D3 估计为：

| level | truth ns | D1 ns | D2 ns | D3 ns |
|---|---:|---:|---:|---:|
| zero | 0 | 0.1281 | 0.1569 | 0.1762 |
| weak | 2.0833 | 1.5676 | 1.6306 | 1.6310 |
| moderate | 4.1667 | 3.5541 | 3.6272 | 3.6292 |
| strong | 8.3333 | 8.2392 | 8.2920 | 8.3095 |

strong 档 D3 confidence=`0.8458`。独立 frequency-domain known-parameter replay 的
cancellation 为 22.6549 dB；D3 估计 replay 为 22.6486 dB，gain 9.2755 dB，约为
独立 Oracle gain 的 99.93%。

## M2：慢时间差分相位

对 raw fast-time 每 pulse 的 FFT 形成

```text
c[p,f] = X1[p,f] * conj(X2[p,f])
```

在正频率 support 上估计 phase trajectory。P1 为 robust constant+linear，P2 为
robust quadratic，P3 为 short smooth spline/Kalman-like trajectory。由于使用
`angle(F1*conj(F2))`，观测 slope 是注入 channel-2 phase slope 的负号；代码在
truth evaluation 字段中显式记录该约定，truth 不参与 fit/mask/权重。

`outputs/mechanism_estimators/probe_m2_seed2026091011/estimator_raw/` 的结果为：

| level | 期望观测 slope deg/pulse | P1 | P2 | P3 |
|---|---:|---:|---:|---:|
| zero | 0 | ≈0.0076 | ≈0.0076 | ≈0.0076 |
| weak | −0.05 | −0.0424 | −0.0430 | −0.0431 |
| moderate | −0.20 | −0.1924 | −0.1930 | −0.1930 |
| strong | −0.50 | −0.4924 | −0.4930 | −0.4926 |

strong 档 P1/P2/P3 confidence 分别约 `0.9926/0.9934/0.9941`。P1 estimated replay
在 strong M2 的 cancellation 为 25.2964 dB，gain 3.8887 dB，与 known phase
Oracle 同量级。

## Mixed/OOD 观测

12 个 mixed/OOD production case 位于 `outputs/mechanism_mixed_design/`，含 M1+M2、
M1+M3、M2+M3 和 M1+M2+mild-M3，改变 seed、squint、range、SNR、速度和纹理。
所有 case 生产运行与 raw estimator audit 均通过。规则诊断的
`mechanism_confusion_matrix.csv` 显示：M1+M2 为 delay-like 1/3、phase-drift-like
2/3；M1+M3 分别落入 decorrelation/delay/unknown；M2+M3 三例均 phase-drift-like；
全混合为 delay-like 1/3、phase-drift-like 2/3。这是可观测机制混淆证据，不是训练
classifier 的准确率。

代表性 production correction：

- M1+M2 OOD：D3 `8.6302 ns`，cancellation `12.7414→20.4784 dB`，gain `+7.7370 dB`；
  P1 M2 correction 仅 `+0.1823 dB`。
- M1+M3 OOD：D3 gain `+2.1311 dB`。
- M2+M3 OOD：P1 gain `+0.3716 dB`。
- 全混合 OOD：D3 gain `+0.2639 dB`，P1 gain `+0.1709 dB`。

这些是 production replay 的 cancellation/headroom，不等价于 target Pd；校正后的
paired Pfa 结果见 AI_CSI_17。

## 复现与测试

```bash
python3 -m py_compile scripts/estimate_channel_delay.py scripts/estimate_temporal_phase.py \
  scripts/run_mechanism_estimator_audit.py scripts/run_estimated_m1_correction.py \
  scripts/run_estimated_m2_correction.py
python3 -m unittest tests/test_mechanism_estimators.py
```

truth 仅用于 post-hoc error table；若输入不完整，脚本报告 missing production raw/tap，
不从旧汇总 CSV 伪造估计值。
