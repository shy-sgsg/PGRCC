# AI-CSI-22：Selective Physics Calibration V2

## 目标与实现

V1 的 global all-or-nothing gate 已拆成三个独立状态：`decorrelation_state`、`delay_state`、`phase_state`。只有 decorrelation 才能触发 global veto；delay 质量不再阻断 phase，phase 质量不再阻断 delay。J5 输出四个显式分支：`D0P0`、`D1P0`、`D0P1`、`D1P1`，保留 J0–J4 的兼容入口。

J6 对实测交叉谱拟合：

```text
C12(p,f) ≈ A exp(j[b + 2π f τ + β1 p + β2 p²])
```

实现使用 centered/scaled 物理轴、圆周残差 `angle(exp(j·residual))` 和 Huber IRLS；估计器不读取 delay、phase、rho 或其他场景真值。输出 `tau_ns`、`beta1_deg_per_pulse`、`beta2_deg_per_pulse2`、拟合残差、confidence 和 residual coherence。null gate 只提供 false-activation/decorrelation 控制；正向质量门槛从 calibration+validation 冻结，Test-V2 不参与冻结，V1 null RMSE envelope 不再作为统一 positive mismatch gate。

## 公式级验证

`tests/test_joint_physics_calibration.py` 已覆盖：

- phase 质量坏时仍允许独立 delay，delay 质量坏时仍允许独立 phase；
- `D0P0/D1P0/D0P1` 分支与 global decorrelation veto；
- J6 zero identity；
- 已知 `τ=6 ns、β1=0.12°/pulse、β2=0.003°/pulse²` 的混合表面，恢复误差接近数值精度且 residual coherence > 0.999；
- V1 J0–J4 原有符号、顺序、null fallback 和 provenance 回归。

## Targeted CUDA screen

本次实际运行的 source commit 为 `451dfed`，`worktree_dirty=false`，`ai_training=false`。设计为 12 个 null 场景和 24 个 screen 场景，覆盖 `M1/M2/M1+M2/M3/M1+M3/M2+M3/M1+M2+M3` 与 zero/weak/moderate/strong 组合；`texture_sigma` 与 `rho` 使用独立 LHS 维度，scan 角度覆盖 `-20/-10/0/10/20°`，并记录 true radial velocity 与 distance-to-clutter-ridge。

screen 的 J5/J6 实测分支计数（target-off/target-on 合计）为：

| 方法 | D0P0 | D1P0 | D0P1 | D1P1 | global decorrelated |
|---|---:|---:|---:|---:|---:|
| J5 | 22 | 6 | 12 | 8 | 10 |
| J6 | 34 | 14 | 0 | 0 | 10 |

screen 中 J6 的 0.5 dB material-headroom recovery median 为 `0.7218`；J5 为 `0.029999`。这只是定向筛查结果，不是 held-out Test-V2 的最终验收。screen 规则输出 `REOPEN_PHYSICS_AI`，但该状态仍不授权训练 AI。

## 证据与复现

```bash
python3 -m py_compile scripts/run_joint_physics_calibration.py \
  scripts/run_joint_physics_selective_v2.py
python3 -m unittest tests.test_joint_physics_calibration -v
python3 scripts/run_joint_physics_selective_v2.py \
  --mode screen \
  --output-dir /tmp/pgrcc_v2_screen_replay \
  --build-dir build
```

已保留的紧凑结果位于 `outputs/physics_adaptive_selective_v2_screen/`，包括 `formal_matrix_manifest.json`、null/positive gate、scene summary、J0–J6 target rows、production CFAR rows/bootstrap 和 0.5/1 dB recovery 汇总。原始 BIN/NPY/F32/XML/log/PNG 已在每场景清理。
