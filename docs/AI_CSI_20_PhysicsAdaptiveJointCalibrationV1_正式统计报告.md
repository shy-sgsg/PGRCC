# Physics-Adaptive Joint Calibration V1 正式统计报告

## 技术摘要

本报告记录 Physics-Adaptive Joint Calibration V1 的正式 CUDA 矩阵结果。结论是：**J3/J4 尚未证明可以替代 Current，也没有进入 AI 训练；预注册 gate 返回 `REOPEN_CANDIDATE`，但 deterministic recovery 很小、remaining Oracle gap 很大，当前主要受 global fallback/gating 混杂影响，下一步必须先做物理估计器与状态门控诊断。**

- 使用生产 Stage2 simulator、生产 GMTI_core 和 raw observable estimator，完成 24 个 calibration、8 个 validation、32 个 held-out test 场景，共 64 场；无 AI 训练（`ai_training=false`）。
- 每个场景生成一次 zero-impairment C+N background，并复用于 exact target-off/target-on；9 个代表场景另有 target-only 输出。test 含 7 个 non-zero mechanism family：M1、M2、M3 及其组合。
- held-out test 的固定阈值在 calibration negative controls 上冻结。Pfa=0.001 时，Current/J3/J4 的经验 Pfa 分别为 `0.006045/0.006070/0.006050`，均高于名义值，且 J3/J4 相对 Current 有轻微回归；Pfa=0.01 同样有回归。
- test target 的 causal Pd 在 Pfa=0.001 对 J0–J4 均为 `0.90`，paired causal Pd 均为 `0.85`；J1/J3/J4 的 median target preservation 约 `-0.0018 dB`，J0/J2 为 `0.5464 dB`。
- 30 个含 M1/M2 的 validation+test scene 的 Oracle recovery ratio：J3 median `0.00`、bootstrap 95% CI `[0.00, 0.0260]`；J4 median `0.00`、CI `[0.00, 0.0261]`。因此不满足 `>=0.80` 的 AI GO 条件。

## 1. 矩阵设计与判定口径

### 1.1 数据切分和配对

| split | scene 数 | 用途 | 阈值是否参与拟合 |
|---|---:|---|---|
| calibration | 24 | zero-mismatch C+N null、固定阈值 | 是，仅负控 |
| validation | 8 | 冻结阈值后的独立检查 | 否 |
| held-out test | 32 | 最终 Pd/Pfa/recovery 评价 | 否 |

sampling 使用确定性的分层 Latin hypercube；没有随机 row split。每个场景的 target-off、target-on 和 target-only（若存在）共享同一个确定性背景目录，因此 target contamination 定义为 **target-on estimator − exact paired target-off estimator**。test target contamination 文件包含 8 条 multi-target 记录，说明多目标场景确实被覆盖。

test 机制族计数为：M1 5、M2 5、M3 5、M1+M2 5、M1+M3 4、M2+M3 4、M1+M2+M3 4。V1 test 不包含 held-out zero family，因此不能用于最终 false-activation 泛化结论；calibration/validation 的 zero 只用于 null 与冻结阈值检查。

### 1.2 方法定义

| 方法 | 定义 |
|---|---|
| J0 Current | 当前生产对消基线 |
| J1 D3 delay-only | raw fast-time D3 延迟估计后做 delay correction |
| J2 P1 phase-only | raw slow-time P1 phase 估计后做 phase correction |
| J3 D3→P1 joint | 先 D3 修正，再对 corrected raw 重新估计 P1，再做 phase correction |
| J4 D3→P2 joint | 先 D3 修正，再对 corrected raw 重新估计 P2，再做 phase correction |

A_P1_then_D3、A_D3_original_P1 和 `Oracle_Known_Joint` 仅作 test recovery/机制诊断，不参与阈值 calibration；oracle 使用已知机制参数，不能当作可部署方法。

## 2. 正式结果：固定 Pfa 没有改善，target Pd 基本不变

下表为 held-out test 的全局 fixed-Pfa 结果。阈值只从 24 个 calibration negative controls 冻结，test negative-control cell 数为 16,777,216；J0–J4 均使用各自 calibration-only threshold。经验 Pfa 不是名义 Pfa 的保证，反映了 held-out 分布差异和当前阈值估计误差。

| method | empirical Pfa @0.01 | @0.001 | @0.0001 | causal Pd @0.001 | paired causal Pd | legacy Pd | target preservation median |
|---|---:|---:|---:|---:|---:|---:|---:|
| J0 Current | 0.037157 | 0.006045 | 0.000205 | 0.90 | 0.85 | 0.375 | +0.546 dB |
| J1 D3 delay-only | 0.037192 | 0.006070 | 0.000204 | 0.90 | 0.85 | 0.375 | −0.002 dB |
| J2 P1 phase-only | 0.037157 | 0.006045 | 0.000205 | 0.90 | 0.85 | 0.375 | +0.546 dB |
| J3 D3→P1 joint | 0.037192 | 0.006070 | 0.000204 | 0.90 | 0.85 | 0.375 | −0.002 dB |
| J4 D3→P2 joint | 0.037212 | 0.006050 | 0.000204 | 0.90 | 0.85 | 0.375 | −0.002 dB |

**解释：** 在当前 test 设计和现有 fixed-Pfa 口径下，joint correction 没有带来可分辨的检测收益。J3/J4 在 `0.001` 和 `0.01` 名义点相对 J0 有 Pfa 回归；在 `0.0001` 点仅有很小的改善，不足以支持部署或 AI 训练决策。legacy Pd 保留作兼容对照，不作为本次因果判定依据。

## 3. Null gate、单位边界和状态保护

null gate 只使用 24 个 zero-mismatch C+N calibration scene，阈值如下：

| gate 项 | frozen threshold |
|---|---:|
| delay deadband `epsilon_tau` | 0.6533 ns |
| P1 phase deadband | 0.02184 deg/pulse |
| P2 phase deadband | 0.02170 deg/pulse |
| delay confidence floor | 0.0004468 |
| P1 confidence floor | 0.0005839 |
| P2 confidence floor | 0.03673 |
| delay fit RMSE upper envelope | 0.09488 rad |
| pulse coherence lower floor | 0.7991 |

输入 XML 的 `<fs>60</fs>` 按 60 MHz 解析为 `60,000,000 Hz`；一条 11,840-sample discrete record 的 delay unambiguous window 约为 `±98,666.7 ns`。该单位边界已在正式运行前用 zero-target smoke 校验：延迟估计约 `0.184 ns`，没有把 60 Hz 当作 60 MHz。

formal test 的每个 method、target-off/target-on 角色均有 32 个场景：4 个进入 `DECORRELATED`、17 个进入 `UNCERTAIN`、其余 11 个为可标定状态。`DECORRELATED`/`UNCERTAIN` 使用 fallback，不把低置信或不可辨识场景强行当作已校正成功；这也是 M3 保护机制，不是性能提升声明。

## 4. Oracle recovery 与 AI gate

recovery ratio 定义为 deterministic gain 除以 known-parameter Oracle 的可回收 headroom。它只用于评估 J1–J4 的可观测校正距离，不是数学上界。

| method | rows（M1/M2 validation+test target-off） | median recovery | deterministic bootstrap 95% CI |
|---|---:|---:|---:|
| J1 D3 delay-only | 30 | 0.000 | [0.000, 0.000] |
| J2 P1 phase-only | 30 | 0.000 | [0.000, 0.000] |
| J3 D3→P1 joint | 30 | 0.000 | [0.000, 0.0260] |
| J4 D3→P2 joint | 30 | 0.000 | [0.000, 0.0261] |

预注册规则为：只有 median recovery `>=0.80`、95% bootstrap CI 下界 `>=0.80` 且所有请求 Pfa 点无 held-out regression，才允许 `NO_GO_M1_M2` 规则下的稳定 No-Go 判定；若观察到 CI 有界的稳定 residual gap，则返回 `REOPEN_CANDIDATE` 供 deterministic 方法调查。本次 J3/J4 的 residual gap 候选稳定，但 Pfa 条件不满足（Pfa=0.001 与 0.01 回归），所以 **`REOPEN_CANDIDATE` 不是 AI GO，也不授权构造训练集或训练模型**。

## 5. Target contamination 检查

用 exact paired target-off 作为基准，test 32 个 scene 的 estimator bias 绝对值统计为：

| test estimator bias | median absolute bias | p95 absolute bias |
|---|---:|---:|
| delay | 0.001293 ns | 0.01819 ns |
| phase slope | 5.46×10⁻⁶ deg/pulse | 2.77×10⁻⁵ deg/pulse |

其中 8 个 multi-target scene 的 delay median/p95 为 `0.000510/0.01161 ns`，phase slope median/p95 为 `5.13×10⁻⁶/1.62×10⁻⁵ deg/pulse`。这些数字支持“target-on 不应污染 calibration observables”的当前实现检查，但不等价于所有未覆盖目标结构和 SNR 的普适证明。

## 6. 环境、复现和清理

- 源码提交：`d9f5b883926433414d87b4086976190101f5da56`，formal manifest 记录 `worktree_dirty=false`。
- GPU：NVIDIA GeForce RTX 3050 Laptop GPU，4,096 MiB；运行前约 1,007 MiB 已用、63°C、P0、约 13 W，运行后约 1,381 MiB 已用、55°C、P5、约 7 W。结果不是性能 benchmark；期间存在桌面/浏览器/其他 GPU 进程。
- 运行前工作区约 58 GiB 可用、可用内存约 6.0 GiB，swap 基本耗尽；运行后约 57 GiB 可用、可用内存约 4.7 GiB。未发生 OOM，但 swap pressure 是后续扩大矩阵的风险。
- 正式命令：

  ```bash
  python3 scripts/run_joint_physics_formal_matrix.py \
    --output-dir /tmp/pgrcc_joint_formal_v1_units_final \
    --build-dir build
  ```

- 正式输出保留在 `outputs/physics_adaptive_joint_v1_formal/`；每个场景的 raw BIN/NPY/log/XML/F32/PNG 和 Stage2 runtime directory 已在记录 SHA-256 后逐场清理。紧凑 manifest 仍保留场景身份、paired provenance、方法状态和 raw hash。

## 7. 下一步与开放问题

1. 先调查 J3/J4 在 M1/M2 上的低 recovery：区分“gate 进入 UNCERTAIN/fallback”与“进入 correction 但估计偏差不足”，不要用放宽 gate 或改阈值掩盖问题。
2. 对 held-out fixed-Pfa 的 calibration-to-test 漂移做独立统计审计；在 Pfa 误差没有得到解释前，不宣称 correction 改善检测。
3. 若后续扩大 beam/range/SNR/velocity/mixed-OOD 矩阵，必须保持 exact pairing、calibration-only threshold、target contamination 和 bootstrap 规则；只有 residual gap 可预测且无 Pfa/Pd/target-preservation 回归，才重新评估是否值得构造低维 AI 输入。

需要补充的证据包括更多独立设备/功率状态下的复现、更多参数域覆盖，以及针对 `REOPEN_CANDIDATE` 的机制级失败归因。当前正式结果支持继续做 deterministic physics audit，不支持训练 AI。

## 证据索引

- 主 manifest：[`outputs/physics_adaptive_joint_v1_formal/formal_matrix_manifest.json`](../outputs/physics_adaptive_joint_v1_formal/formal_matrix_manifest.json)
- null gate：[`outputs/physics_adaptive_joint_v1_formal/null_gate.json`](../outputs/physics_adaptive_joint_v1_formal/null_gate.json)
- fixed-Pfa target rows：[`outputs/physics_adaptive_joint_v1_formal/heldout_target_rows.csv`](../outputs/physics_adaptive_joint_v1_formal/heldout_target_rows.csv)
- calibration/validation target rows：[`outputs/physics_adaptive_joint_v1_formal/calibration_target_rows.csv`](../outputs/physics_adaptive_joint_v1_formal/calibration_target_rows.csv)、[`outputs/physics_adaptive_joint_v1_formal/validation_target_rows.csv`](../outputs/physics_adaptive_joint_v1_formal/validation_target_rows.csv)
- estimator target bias：[`outputs/physics_adaptive_joint_v1_formal/estimator_target_bias.csv`](../outputs/physics_adaptive_joint_v1_formal/estimator_target_bias.csv)
- Oracle recovery：[`outputs/physics_adaptive_joint_v1_formal/recovery_summary.csv`](../outputs/physics_adaptive_joint_v1_formal/recovery_summary.csv)
- 清理记录：[`outputs/cleanup_manifest_20260910.json`](../outputs/cleanup_manifest_20260910.json)
