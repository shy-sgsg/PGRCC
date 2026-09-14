# Unknown System Error Phase0–7 实施与验收计划

> 本计划对应 2026-09-14 的真实系统未知误差建模主线。历史
> `2026-09-13-unknown-system-error-characterization.md` 保留其原始阶段记录，
> 不回写旧实验数字。

## 目标与边界

建立一条可审计的链路：

```text
true/reported channel geometry
  → four-channel echo IQ
  → unknown-only multi-pair estimator
  → physical raw-IQ correction
  → production Current CSI/GO-CFAR
  → offline four-channel STAP reference
  → recovery / Pd / Pfa / target transfer
```

本阶段只实现首个可观测参数 `delta_d`（通道 2/4 的第一轴位置偏差）和 constant
pair-phase nuisance。估计器不得读取 ideal、truth、known-error 或 future metrics。
Known-error correction 仅是评价上限。AI、Router、通用复权残差和 RD image-to-image
均关闭。

## 阶段状态

- [x] **Phase0 — paired-reference baseline-phase sanity**
  - 运行器和 manifest 显式标记 `estimator_mode=paired_ideal_reference`、
    `operational_blind=false`。
  - clean archive `823ebae` 已运行，结果在
    `outputs/unknown_system_error_pilot_20260914_clean/`。
- [x] **Phase1 — unknown-only estimator**
  - 只消费 unknown-off 四通道 IQ、reported geometry 和过滤后的 nominal metadata。
  - 六对 `C13,C24,C12,C14,C23,C34` 联合 circular fit；单 `C12` 只作为对照。
  - 无可辨识角度跨度时显式 fallback。
- [x] **Phase2 — true/reported four-channel geometry**
  - `true_channel_positions` 驱动 echo，`reported_channel_positions` 保持处理端视图。
  - 旧 `group_baseline_error_legacy_pilot` 仅保留 regression mode。
  - `channel_geometry_selftest` 覆盖两种模式。
- [x] **Phase3 — blind targeted matrix**
  - `[0, ±1, ±2.5, ±5, ±10] mm × 3 seeds`，覆盖指定多波位角度。
  - 输出 bias、RMSE、4000-resample bootstrap CI、failure/fallback、pair residual、
    closure 和 cross-pair consistency；27/27 case 已完成。
- [x] **Phase4 — true-geometry end-to-end**
  - `OFF=C+N`、`ON=target+C+N`、`TO=target-only`，三组移动目标速度。
  - B0 Current、B1 blind、B1K known 运行生产 CUDA CSI/GO-CFAR。
  - B2/B3/B3K 运行离线 `JDL-3x4 reduced STAP`，明确为 scientific reference。
- [x] **Phase5 — metric and evidence closure**
  - 保存 `Known−Current`、`Estimated−Current`、方向、recovery ratio、Pd、target-off
    Pfa、false clusters、runtime、输入 SHA-256 和 GPU provenance。
  - 结果汇总在 `outputs/unknown_system_error_end_to_end_20260914/`。
- [ ] **Phase6 — servo/beam or platform-state extension**
  - 只有 Phase3/4 证据稳定后才扩展。
  - 需要独立 true/report servo angle 或 velocity state；本阶段不假装已实现。
- [x] **Phase7 — AI boundary**
  - `ai_training=false`；当前没有训练 MLP、Router、通用 `delta-alpha` 或
    image-to-image 网络。
  - 重新评估 AI 的前置条件仍是：Known recovery、deterministic blind gap 稳定，且
    残差能由多通道 inference-time observation 预测。

## 复现与验收

代码级验证：

```bash
python3 -m py_compile scripts/analyze_four_channel_observables.py \
  scripts/run_unknown_system_error_pilot.py \
  scripts/run_unknown_system_error_matrix.py \
  scripts/run_unknown_system_error_end_to_end.py \
  scripts/unknown_geometry_correction.py
pytest -q tests/test_four_channel_observables.py \
  tests/test_unknown_system_error_matrix.py \
  tests/test_unknown_geometry_correction.py \
  tests/test_unknown_system_error_end_to_end.py
cmake --build build -j4
ctest --test-dir build --output-on-failure
git --git-dir=.git-real --work-tree=. diff --check
```

实验入口：

```bash
python3 scripts/run_unknown_system_error_matrix.py \
  --output-root /tmp/pgrcc_unknown_system_error_geometry_matrix_repro
python3 scripts/run_unknown_system_error_end_to_end.py \
  --output-root /tmp/pgrcc_unknown_system_error_end_to_end_repro
```

E2E 的 B0/B1/B1K 必须在真实 CUDA 设备运行；B2/B3/B3K 的当前实现是离线 reference，
不能把 CPU/离线结果写成生产 CUDA STAP。输出目录必须是新目录；raw BIN 不提交，
manifest/CSV/JSON 保留可复现和审计所需信息。

## 当前未完成项

1. 生产 CUDA 四通道 STAP 与信息量匹配矩阵；
2. TrackManager/PIPE 的逐周期 `Confirmed + matched_this_frame` 目标保持验收；
3. servo/beam/platform velocity 的独立 true/report state；
4. 多场景、多周期和硬件负载稳定统计；
5. 在上述证据前不进入 AI 训练或 Router 分支。
