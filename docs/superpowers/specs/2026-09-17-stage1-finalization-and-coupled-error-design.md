# Stage-1 Channel-Delay Finalization and Stage-2A Coupled-Error Design

## 1. Goal and non-goals

本阶段把 channel-delay Paper-1 从“可运行的开发 formal”收口为可审查、可复现、可
冻结的证据链，并在 Stage-1.1 通过后建立三个小规模耦合误差组合的确定性 baseline。
生产科学链路固定为：

```text
4ch protocol IQ
  -> F1=(C1+C3)/2, F2=(C2+C4)/2
  -> two-channel CSI
  -> production GO-CFAR
  -> clustering/positioning
  -> TrackManager/PIPE
```

本阶段不训练神经网络，不启用 AI Router，不实现 native four-channel STAP/JDL，不改
GO-CFAR、TrackManager 的判决、门限、确认或关联行为。运行时必须继续记录：

```json
{"ai_training": false, "router_enabled": false, "native_four_channel_stap": false}
```

Stage-2B 只允许在 Stage-2A 的 AI gate 通过后写设计文档；本阶段不实现或训练任何
multi-error neural estimator。

## 2. Evidence baseline and immutable boundary

当前工作树为 `codex/unknown-system-error-next-stage`，基线提交为
`cdf8ebc`。上一阶段保留的 compact evidence 位于
`outputs/formal_evidence/stage1_delay/`，Formal-v1 原始/压缩运行根位于
`outputs/formal_delay_stage1_20260916/`。这些目录属于已有审计资产，本设计不覆盖、
不重写、不把其历史数字改成新的验收标准。

Formal-v1 的开发 formal 事实必须作为只读历史记录保留：

- `source_status=completed`，source run manifest SHA-256 为
  `30e0cc4b945dcc63e4e4a92fb4208653b337ca6d07e541b58f1875a61fddcb9b`；
- source commit 从 `106aaacee9c73abba133bad33f34fca4f52ce427` 跨到
  `3ba37d8816cca6ad8f453aa6730197252b5abc74`，且 dirty；
- 该次运行包含 resume/interrupted-case 相关代码变化，不能作为最终 frozen-paper
  clean provenance；
- `ai_training=false`、`router_enabled=false`、`native_four_channel_stap=false`；
- v1 的九个 compact 文件和既有审计 CSV/报告继续作为回归参考，但 v2 必须使用独立
  `outputs/formal_delay_stage1_v2_<date>/` 和
  `outputs/formal_evidence/stage1_delay_v2/`，永不覆盖 v1。

`docs/AI_CSI_37_ChannelDelay_Finalization_Audit.md` 是 v1 到 v2 的解释层，不能通过
修改 v1 manifest 来“修正”历史 provenance。若历史 manifest 的字段无法回写而又需要
明确语义，使用 sidecar audit 和新 v2 manifest；历史文件内容保持字节级不变。

## 3. Repository source map confirmed before implementation

本设计只引用已经扫描并打开检查过的真实入口，不创建平行的伪生产实现。

| 责任 | 真实入口 | 当前事实 | Stage-1.1 变更边界 |
|---|---|---|---|
| GO-CFAR kernel | `src/gpu/gpu_kernels.cu:434` `cfar_detect_kernel` | kernel 已计算 Doppler circular/edge、guard/background geometry、row exclusion、cut-band mask、threshold 和 hit map | 只增加 geometry/计数诊断 tap；不改 `CUT > threshold` 判决 |
| CUDA CFAR wrapper | `src/gpu/gpu_kernels.cu:2468` `GMTIProcessor::dpca_cfar2_fast_cuda` | 计算 `g/b/R/total_bg/alpha`，支持 CA/GO 和 split/dynamic 调用 | 输出分母所需的实际 branch 参数及计数 |
| branch orchestration | `src/processOnePeriod.cpp:2569` 起，summary 在约 `3054`、`4585` | dynamic/full/split/union 会调用独立 CFAR；split 有 in-band/out-of-band 两个 branch | 每个 branch 独立计数，union 不把两次 hit 简单当作独立 CUT |
| TrackManager scoring | `src/TrackManager.cpp:1019` 起 `computeAssocScoreEN` | 欧氏、Mahalanobis、速度、航向、检测速度 gate/cost 已在生产路径计算 | 只记录 candidate、score、reject reason、innovation 和 gate |
| TrackManager update | `src/TrackManager.cpp:2434` 起 `updateRawDetections` | 真实分配、update、Coasted、Deleted、新建、Confirm、PIPE 输出都在此 | 不新建跟踪器，不改变状态机 |
| existing track diagnostics | `src/TrackManager.cpp:741`、约 `951`、`2302`、`2539`、`2985` | 已有 association log、accepts、events、payload、frames/detections | 扩展 schema 时保持旧消费者可读，并记录 schema/version |
| fractional correction | `scripts/estimate_channel_delay.py:248` `rewrite_float32_protocol_delay` | 每个 256-byte packet 内对选定 channel 做 FFT、`exp(+j2πfΔτ)`、IFFT；保留 header/非目标通道 | 先做 inverse-closure 审计；bug 只能经 failing test 后最小修复 |
| correction call site | `scripts/run_delay_stage1_formal.py:529` 起 `prepare_condition_inputs` | A2 使用 truth 仅施加 correction，A3 使用 A1 OFF target-free estimate；失败不应退化 A1 | 保持 A3 truth-blind provenance |
| protocol layout/fusion | `scripts/run_track_manager_e2e.py:363`、约 `718` | `correction_channel_indices=[read_channel_2-1]`；估计前融合为 F1/F2 | closure 必须同时检查“校正前/后、融合前/后、部分通道” |
| A0/A1/A2/A3 construction | `scripts/run_delay_stage1_formal.py:116`、`389`、`464`、`529`、约 `1361` | A0/A1 生成 ON/OFF/TO scene；A2/A3 复用 A1 raw inputs 后校正 | 不把 A2/A3 当重新仿真的独立 scene |
| registered blocks | `scripts/run_delay_stage1_formal.py:1818` 起 `_stage1_case_blocks` | base 12 个（3 seeds×2 velocity×2 SNR），low/high/alternate 各 1 个，共 15 个物理 block | block 是最高层统计单位；delay 是 block 内 repeated treatment |

当前 config 的 formal E2E delay 列表和 Level-1 Monte Carlo delay 列表并不完全相同：
选择器和配置必须分别记录二者，不能从文件名推断。Final Formal-v2 的命令必须显式
使用 `0,1,-1,2,-2,4,-4,8,-8 ns`，并在 manifest 中证明 15 个 scene block 与每个
delay 的覆盖；若某层只存在 7 个 delay，报告必须标明该层而不能冒充 15×9。

## 4. Stage-1.1 design: channel-delay finalization

### 4.1 Formal-v1 finalization audit

新增审计文档必须回答四件事：

1. 哪些 v1 数字仍可作为 development formal / regression reference；
2. 哪些数字因 dirty commit span、中断/resume 变化、缺少 valid-CUT denominator 或
   association attribution 不能作为 final frozen-paper claim；
3. v1 的 source manifest、compact evidence、源码状态和 commit span 如何被逐项审计；
4. v2 需要哪些新证据才能升级结论。

审计文档要明确区分“历史产物未被覆盖”和“历史产物已满足最终验收”；前者不等于
后者。

### 4.2 Fractional-delay inverse closure

新增 `scripts/audit_fractional_delay_inverse_closure.py` 和
`tests/test_fractional_delay_inverse_closure.py`。审计分五级：

| 级别 | 输入 | 目的 |
|---|---|---|
| C0 | deterministic complex tone/impulse | 校验 FFT sign、频率轴、幅度、边界、整数/分数延迟基础语义 |
| C1 | known LFM | 校验宽带分数延迟与 pulse compression 相关边界 |
| C2 | multi-pulse target-free clutter waveform | 校验每 pulse、inter-pulse boundary 和 packet 内累计方式 |
| C3 | full 4ch protocol → F1/F2 | 校验 correction channel、fusion pair 和部分通道情形 |
| C4 | production CSI input | 校验真实输入布局、production call site 与 CSI 前后残差 |

每个 case 对 `0, ±1, ±2, ±4, ±8 ns` 执行：

```text
x -> inject +delay -> exact -truth correction -> compare with x
```

至少输出以下字段：complex NMSE、amplitude error、phase RMS、group-delay residual、
edge-sample loss、energy ratio、coherence、sample/pulse/packet counts、finite/NaN status、
fallback reason。误差必须分别按 interior、pulse boundary、packet boundary 汇总。

审计必须显式记录并测试：linear vs circular shift、FFT zero padding、edge policy、
Fourier sign、amplitude normalization、correction before/after F1/F2 fusion、仅部分
channel correction、packet discontinuity 和 pulse compression model。不能用一个 tone
通过就宣称生产闭环正确。

判定规则：若 C0–C4 在预先声明的数值精度内闭合，且残差随边界策略可解释，则
A2≠A0 的剩余差异归为有限窗口/非可逆边界效应；若在 C0/C1/C3 的 interior 已超出
精度，先写 failing test、定位单一 sign/layout/normalization 假设并最小修复，再重新
跑受影响 formal。不得通过 CFAR 或 TrackManager 调参掩盖 closure 失败。

### 4.3 Production GO-CFAR valid-CUT diagnostic tap

生产 C++ tap 只读，不改变 detector map、threshold、cluster 或 target selection。
每个 period/beam/branch 输出一行 geometry record，至少包含：

```text
period_id, beam_id, band_or_mode, branch_id,
total_cells, excluded_cells, valid_cut_count,
threshold_test_count, hit_cut_count, hit_cut_ids_or_stable_hash,
configured_pfa, cfar_type, alpha,
training_geometry, guard_geometry
```

`valid_cut_count` 必须严格按 production kernel 实际 geometry 定义：二维尺寸、Doppler
circular/非 circular、`R=g+b` 边缘、row exclusions、cut-band mode；split branch
分别计数，union 对重复 CUT 去重并保留 branch provenance。`threshold_test_count` 与
`valid_cut_count` 若在实现中语义相同，也必须分别输出并在 schema 说明，不得用日志中的
`hit_cells` 反推分母。

经验 cell fraction 只有 `valid_cut_count > 0` 才计算；否则写
`status=NOT_EVALUABLE`，绝不填 0。以下层级保持独立：

1. valid-CUT cell false-hit fraction；
2. false clusters；
3. protocol false detections；
4. false tracks。

旧的 `target_off_false_alarm_summary.csv` 可保留，但 v2 必须携带 tap 的来源路径和
branch-specific denominator。需要覆盖 CUDA split/dynamic 和可用 CPU fallback 的
schema/计数测试；CPU fallback 不能伪造 CUDA geometry。
### 4.4 Read-only TrackManager association instrumentation

扩展真实 `TrackManager::updateRawDetections` 的 diagnostic output，不改变任何 gate、
assignment、lifecycle 或 output filtering。每个 target-associated measurement/update
至少记录：

```text
frame/result id, track id, track state before/after,
measurement id/index, candidate count and candidate ids,
innovation position/velocity/angle where already available,
euclidean distance, Mahalanobis distance, gate threshold(s),
association rank, cost, dummy cost, reject reason,
lifecycle event: new/delete/confirm/coast/lost/reacquired,
merge/split evidence only if the current production state exposes it,
source file/schema version
```

若 production state 不保存某个量，字段写空并写 `unavailable_in_production_state`；不得
从 truth 或另一个简化 tracker 反造 candidate。ID-switch audit 读取这些 rows、同周期
detection、track frames、events、payload 和 truth，更新分类：

```text
miss_to_reacquisition
multiple_candidate_competition
false_track_takeover
gate_boundary_crossing
position_velocity_jump
track_confirmation_or_reset
unclassifiable_production_fields
other
```

`other` 不得因缺少字段而 100% 静默出现；若仍无法判断，必须用明确的不可判定原因
和缺失字段计数表达。merge/split 在真实生产没有证据时标记 unavailable，而不是推断。

### 4.5 Hierarchical statistics

新增 `scripts/analyze_delay_stage1_hierarchical.py` 和对应 tests。数据模型为：

```text
physical block = seed + velocity + SNR + working point/texture/geometry + shared scene identity
treatment      = delay error
replicate      = ON/OFF/TO/condition metrics within the same physical block
```

同一 scene block 的多个 delay 不是独立 block。输出：

- `±1/±2/±4/±8 ns` 的 A1→A2 和 A1→A3 per-delay paired effects；
- 以 scene block 为 resampling unit 的 paired block bootstrap 95% CI；
- delay×SNR、delay×velocity、delay×working-point 趋势和每个组合的有效 block 数；
- binary target/cluster/track outcomes 的 paired cluster-aware statistics；
- 旧 135-case McNemar 作为 `Formal-v1 exploratory`，不作为 final confirmatory test。

连续低值指标先按预定义方向转成 loss/recovery，零 recoverable space 写
`NOT_EVALUABLE`。报告必须列出缺失 block、缺失 delay 和不可配对记录。

### 4.6 Materiality, baseline, novelty and hardware

在 Formal-v2 前新增 `configs/research/channel_delay_stage1_materiality.json`，为 delay
RMSE、target Pd、track Pd、position RMSE、angle RMSE、velocity RMSE、false clusters、
false detections、false tracks、ID switches 写阈值、方向、单位、适用层级和 source
classification。可选 source 只能是：

```text
system_requirement
engineering_judgment
historical_production_variability
literature-derived
not_available
```

没有真实依据的阈值必须是 `exploratory_pending`，不能在结论里称为最终 materiality。

传统 baseline 只增加 1–2 个，公平复用同一 F1/F2、OFF、samples、truth-blind boundary
和 delay sweep。候选优先级为 frequency-domain ML、generalized phase-slope ML、
oversampled/sinc cross-correlation、joint phase-slope ML。每个 baseline 输出 bias、
RMSE、95% CI、fallback、runtime；不得削弱现有 D1/D2/D3 或更改其输入条件。

新增 `docs/literature/ChannelDelay_Calibration_Novelty_Audit.md`，覆盖经典 calibration、
multichannel SAR、airborne GMTI/STAP 和近 5–10 年文献，逐篇记录平台、误差、calibration、
target-free、方法、fractional-delay、real-data、下游 Pd/Pfa/tracking 和本文差异。审计
完成前，Paper 不使用 “first/novel”；只能把差异写为 potential contribution。

若没有真实硬件，新增
`docs/experiments/ChannelDelay_Hardware_Validation_Protocol.md`，只写 topology、clock、
delay emulator、instrument、truth uncertainty、repeats、metrics 和 pass/fail protocol。
文档明确当前只有 simulation + production software，不能填写或暗示硬件结果。

### 4.7 Clean Formal-v2

Formal-v2 之前必须满足：

- source/config/template/runner/analyzer 已提交；
- worktree 对 tracked files clean，formal run 前记录 `git status`、commit、build hash、
  config/template hash、GPU 状态和完整命令；
- run 期间不修改 source/config/template/runner/analyzer；
- 任一 bug、schema mismatch、closure failure 或中断状态导致结果不可信时立即停止，
  修复、提交，再从独立 v2 output 重新运行；
- v2 root 和 compact evidence 独立于 v1，manifest 记录 dirty=false、before/after
  commit 相同、resume/attempt、输入 hash、设备状态、命令和清理记录；
- raw 大文件不作为最终交付必需物，保留 compact manifest、汇总、审计和支持结论的
  高价值结果。

## 5. Stage-2A design after Stage-1.1 gate

Stage-2A 只能在 Stage-1.1 A–L 全部通过后启动；若任何 gate 为 false，保留失败证据，
不进入 coupled-error 结论。

### 5.1 Three combinations only

只实现以下三种组合，不扩展为无约束 six-parameter Cartesian product：

1. C1: channel delay + inter-pulse phase drift；
2. C2: channel delay + servo angle error，记录 near-confounding cosine 约 `-0.92`；
3. C3: platform velocity error + inter-pulse phase drift。

每个组合至少包含 zero/zero、A-only、B-only、same-sign、opposite-sign，并覆盖多个
seed、SNR 和 working point。truth 参数、reported/estimated 参数、residual 和 source
必须分开保存。参数量使用小而 engineering-plausible 的范围，不能由一次成功 run 反推
全球范围。

### 5.2 Deterministic baselines and sensor-prior decomposition

固定四个 baseline：

```text
M0 Current: 不校正
M1 sequential traditional: 先估计 A 后估计 B，以及先 B 后 A 两个顺序
M2 joint deterministic: weighted nonlinear LS 或 MAP，但不使用 truth prior
MK Known-error correction upper bound: 只用于上限和 recoverable space
```

sensor-prior decomposition 至少包含 radar-only、sensor-prior-only、sensor-prior + radar
residual，并显式写出：

```text
x_corrected = x_reported + delta_x_radar
```

每个 delay/servo/velocity/yaw 来源要标为 radar observable、sensor prior、simulator truth
（仅 evaluation upper bound）或 unavailable。truth 不得进入 M1/M2 estimator。

### 5.3 Coupled observability and recovery gap

每个组合在多个 working point/SNR/amplitude 上计算 observation Jacobian、SVD、rank、
condition number、parameter-column cosine，并在可适用时输出 posterior covariance / Hessian
conditioning。不能从单一 operating point 的 local rank 宣称 globally identifiable。

指标至少覆盖 parameter error、CSI residual、target transfer、Pd、false hit、track Pd、
false tracks、ID switch、position/angle/velocity RMSE。对高值指标和低值指标分别定义方向，
输出：

```text
MK_minus_M0
M1_minus_M0
M2_minus_M0
MK_minus_M2
recovery_ratio = (M2-M0)/(MK-M0)  # 方向转换后
```

零分母、不可比较分支或缺失 production denominator 均为 `NOT_EVALUABLE`。

### 5.4 Physics-AI gate and Stage-2B boundary

在任何 NN 代码之前新增 `configs/research/physics_ai_gate.json`，固定六项 gate：

1. material recoverable space；
2. deterministic material repeatable gap；
3. held-out seeds/working points；
4. information visible in F1/F2 plus sensor prior；
5. not a known implementation bug；
6. system-metric impact。

任一 core gate 失败，结果为 `NO_GO_PHYSICS_AI`。若全部通过，Stage-2B 仍只新增
`docs/AI_CSI_39_PhysicsAI_CoupledCalibration_Design.md`，记录 input/output/residual/
confidence、held-out split、safety fallback 和 audit schema；本阶段不实现 NN。

## 6. Required artifacts and verification

Stage-1.1 required artifacts：

```text
docs/AI_CSI_37_ChannelDelay_Finalization_Audit.md
docs/AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md
docs/experiments/ChannelDelay_Hardware_Validation_Protocol.md
docs/literature/ChannelDelay_Calibration_Novelty_Audit.md
scripts/audit_fractional_delay_inverse_closure.py
scripts/audit_delay_track_id_switch.py  # enriched read-only audit
scripts/analyze_delay_stage1_hierarchical.py
configs/research/channel_delay_stage1_materiality.json
outputs/formal_delay_stage1_v2_<date>/
outputs/formal_evidence/stage1_delay_v2/
```

Stage-2A required artifacts：

```text
configs/research/coupled_error_stage2a.json
scripts/run_coupled_error_stage2a.py
configs/research/physics_ai_gate.json
docs/AI_CSI_39_PhysicsAI_CoupledCalibration_Design.md  # only if gate supports design
```

所有新增 Python 功能需有 pytest failing-first evidence；C++/CUDA tap 需有编译、CTest 或
对应 selftest 和最小运行记录。最终至少执行并保存：

```bash
python3 -m pytest -q
cmake --build build -j4
ctest --test-dir build --output-on-failure
git diff --check
```

Python/Shell/C++/CUDA 修改还要执行适用的 syntax/build 检查。GPU 运行须在真实 CUDA
设备上记录 `nvidia-smi`、GPU 型号、P-state、频率、功耗、温度、负载、warm-up 和重复
计时；CPU smoke 或退出码 0 不能替代 CUDA 结论。

## 7. Stage-1.1 acceptance gate A–L

只有以下 12 项全部有当前产物和命令证据，才能启动 Stage-2A：

| Gate | 证据要求 |
|---|---|
| A | A2/A0 residual 已用 closure/边界审计解释，或 bug 已修复并重跑受影响 formal |
| B | C0–C4 inverse closure 指标和边界分类通过 |
| C | 真实 production GO-CFAR valid-CUT denominator 已导出，零分母为 NOT_EVALUABLE |
| D | OFF waterfall 分离 cell/cluster/protocol/track，且每层有定义和来源 |
| E | ID switch 有实质归因；否则明确 unclassifiable production fields，不以 100% other 伪装 |
| F | hierarchical block/delay stats 已运行，旧 135-case 仅 exploratory |
| G | materiality 在 Formal-v2 前已冻结并标注 source classification |
| H | 1–2 个 stronger traditional baselines 已公平比较 |
| I | literature novelty audit 和 hardware protocol 已交付，无未经审计的 first/novel claim |
| J | clean frozen commit 的 Formal-v2 before/after 相同，dirty=false，且 v1 未覆盖 |
| K | pytest、CTest、Release build、syntax 和 diff check 有实际输出 |
| L | AI、Router、native four-channel STAP 始终关闭 |

Stage-1.1 完成后更新 `docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md`、
`docs/papers/Paper1_ChannelDelay_SelfCalibration_Outline.md` 和最终结论文档；报告需
明确回答 A2 residual、fractional correction 是否可逆、delay 是否 material、A3 是否
足以继续及真实硬件尚未验证等问题。

Stage-2A 完成后再提交第二份阶段报告，回答三种耦合组合的 observability、M0/M1/M2/MK
差距、sensor prior 价值、系统指标影响和 Physics-AI GO/NO-GO。任何未运行、不可评估或
受限于硬件/权限的项目必须保留为明确 limitation，不得写成通过。
