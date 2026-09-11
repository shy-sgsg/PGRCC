# AI-CSI-26：Target Transfer 与目标保护审计

## 目标

V2 的 target-on/target-only peak ratio 不是 primary target-preservation 指标：
target-on 同时包含目标、残余杂波、噪声和 coherent interaction，target-only
还可能被重新估计 calibration。Phase B 改为同场景、同 seed、同 geometry、同
mismatch、同 calibration source 的三角色 paired audit：

    OFF = C + N
    ON  = S + C + N
    TO  = S-only
    DeltaY = Y_ON - Y_OFF

J5/J6 的 primary correction parameters 只从 OFF 估计，然后 freeze 并完全相同地
作用于 OFF/ON/TO；ON-derived calibration 只作为 deployment check，不能进入 primary
transfer 指标。

## 指标

primary：

    L_causal =
    10log10(sum_ROI |DeltaY_candidate|² /
             sum_ROI |DeltaY_Current|²)

secondary：

    L_target_only =
    10log10(sum_ROI |Y_TO_candidate_fixedcal|² /
             sum_ROI |Y_TO_Current_fixedcal|²)

同时保留 integrated ROI energy、peak transfer、range/Doppler centroid shift、
peak-bin shift、ROI spreading，以及每个 target 的 compact complex sums/energy/
peak statistics。不会提交完整 NPY。

paired detection 使用严格定义：

    paired_causal_hit = on_hit AND NOT off_hit

并记录 on/off CFAR margin 与 delta margin；只有这个 paired 指标才能称
paired_causal_pd。

## 运行设计

scripts/audit_target_transfer.py 提供两个模式：

1. snr-screen：12 个 fresh scenes，先找 Current Pd≈0.2–0.8 的 SNR transition；
2. audit：24 个 fresh scenes，覆盖 M1、M2、M1+M2、M1+M3、M2+M3、
   M1+M2+M3，覆盖 D1P0/D0P1/D1P1 的 active candidate cases。

raw runtime 在每个 scene 完成 compact extraction 后删除，只保留 CSV/JSON、manifest
和资源快照。正式结果写入 outputs/target_transfer_audit/，SNR screen 使用独立
输出目录，不与 final Test-V3 混用。

当前阶段仍为 `ai_training=false`；Phase B 只用于建立 physics-only 的目标保护
证据，不授权任何 AI 训练或部署结论。

## SNR screen 实际结果

已完成两轮互相独立的 12-scene CUDA screen，方法均为 J0/J5/J6，且每个 SNR
点包含 6 个 scene、覆盖六个 mismatch family。严格 paired causal hit 定义仍为
`on_hit AND NOT off_hit`。

| 输出目录 | SNR | Current paired causal hits / targets | J5 | J6 |
|---|---:|---:|---:|---:|
| `snr_screen` | -2 dB | 0 / 6 | 0 / 6 | 0 / 6 |
| `snr_screen` | +2 dB | 1 / 6 | 1 / 6 | 1 / 6 |
| `snr_screen_4_8` | +4 dB | 0 / 6 | 0 / 6 | 0 / 6 |
| `snr_screen_4_8` | +8 dB | 1 / 6 | 1 / 6 | 1 / 6 |
| `snr_screen_10_14` | +10 dB | 1 / 6 | 1 / 6 | 0 / 6 |
| `snr_screen_10_14` | +14 dB | 3 / 6 | 3 / 6 | 3 / 6 |

因此 +14 dB 已定位到预设的 Current Pd≈0.2–0.8 transition（`3/6=0.5`）。
正式 24-scene audit 使用独立的 audit seed namespace 和围绕 transition 的
`10,12,14,16 dB` 四个 SNR 点，没有复用上述 screen 的 scene seed。三轮 screen
都没有检测到 OFF-only 命中，且除 +10 dB 的 J6 外三种方法的 paired hit 计数
一致；这只是筛选证据，不是目标保护结论。

三轮目录均无 `.bin/.npy/.f32/.log/.xml/.png` 残留。manifest、资源快照及紧凑
CSV 是当前保留的高价值证据；运行时产生的 per-scene 原始树已逐场景删除。

## 24-scene formal audit 实际结果

运行命令：

    python3 scripts/audit_target_transfer.py --mode audit \
      --snr-values 10,12,14,16 \
      --output-dir outputs/target_transfer_audit/formal_10_16 \
      --build-dir build

本次使用 source commit `43de307`、formal physics source `669df38`，seed namespace
`2026111000`，24/24 scene 通过；输出 72 条 target-transfer、72 条 paired
detection、144 条 production CFAR rows、48 条 deployment calibration rows。

### 目标传递和 paired detection

下表是 24 个 scene 的 compact ROI 统计；J0 作为同一场景的零差分基线，因而
`L_causal` 和 `L_target_only` 恒为 0。

| 方法 | `L_causal` mean / median / p05 / min (dB) | `L_target_only` mean / median / p05 / min (dB) | paired causal Pd |
|---|---:|---:|---:|
| J0 Current | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 6/24 = 0.2500 |
| J5 Selective Physics Calibration | +0.0716 / 0 / −5.1214 / −7.9144 | −0.9144 / 0 / −5.6849 / −12.1204 | 7/24 = 0.2917 |
| J6 Joint Phase Surface | −0.1902 / 0 / −5.4650 / −7.9178 | +0.3420 / 0 / −5.5292 / −12.3728 | 7/24 = 0.2917 |

按 SNR 的 paired causal hits（每点 6 个 scene）为：10 dB 三种方法均 `1/6`；
12 dB 均 `1/6`；14 dB 均 `2/6`；16 dB Current `2/6`、J5/J6 `3/6`。
24 个 scene 中没有 OFF-only hit；这说明 paired hit 口径已严格执行，但不能
替代目标保护的数值尾风险判定。

### branch、CFAR 和 deployment check

J5/J6 的 OFF-derived branch 覆盖相同：D0P0=9、D0P1=7、D1P0=4、D1P1=4；
ON branch 与 OFF branch 在 24 个 scene 中均一致。目标关闭（OFF）production CFAR
的描述统计为：

| 方法 | Pfa mean | false clusters mean |
|---|---:|---:|
| J0 Current | 0.008348 | 27.50 |
| J5 | 0.008364 | 30.42 |
| J6 | 0.008359 | 32.58 |

相对 J0 的逐 scene 均值差为 J5 `+1.41e−5 Pfa / +2.92 clusters`，J6
`+1.31e−5 Pfa / +5.08 clusters`；这里先作为描述性结果，不把它直接提升为
正式 CFAR gate。48 条 deployment check 均记录 `same_correction_primary=True`，
ON-derived calibration 只用于诊断，未进入 primary transfer 指标。

### 阶段判读

本 audit 没有得到“J5/J6 在目标传递上安全且一致优于 Current”的证据：均值接近
零并不能消除 −5 dB 量级的低分位损失，target-only secondary 也存在约 −12 dB
的最差样本。因此 Phase B 支持继续做 tail-risk 和 target-safe oracle 分析，
不支持开放 AI 训练、路由或部署；最终 gate 仍为 `UNRESOLVED`。

formal audit 的紧凑证据在
`outputs/target_transfer_audit/formal_10_16/`；该目录没有保留 `.bin/.npy/.f32/`
等 per-scene 原始中间结果，manifest、资源快照及 CSV 是当前保留的审计记录。
