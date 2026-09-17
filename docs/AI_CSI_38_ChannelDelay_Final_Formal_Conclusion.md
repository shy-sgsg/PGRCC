# AI_CSI_38：Channel-delay Formal-v2 final conclusion template

**状态：** 模板，尚未构成最终结论。

**填充规则：** 只允许填入独立 Formal-v2 manifest、compact evidence、当前提交身份和本次实际命令中的值；不得用 Formal-v1 development formal 的数字填充 v2 槽位。若证据缺失、分母为零或设备不可用，保留 `NOT_EVALUABLE` 及原因。

## 1. Provenance gate

| 字段 | 仅可从当前 v2 证据填入 |
|---|---|
| v2 output root | `<outputs/formal_evidence/stage1_delay_v2/>` |
| source commit before/after | `<commit_before>` / `<commit_after>`；必须相同 |
| tracked dirty state | `<dirty_before>` / `<dirty_after>`；必须为 `false` |
| config/template/build hash | `<config_sha256>` / `<template_sha256>` / `<build_identity>` |
| command / attempt / resume | `<exact_command>` / `<attempt>` / `<resume_history>` |
| GPU/device snapshot | `<nvidia-smi_or_NOT_EVALUABLE_reason>` |
| input hashes | `<protocol_input_hashes>` |
| v1 source manifest hash | `30e0cc4b945dcc63e4e4a92fb4208653b337ca6d07e541b58f1875a61fddcb9b`（若变更则停止并调查） |
| v1 compact manifest hash | `0a26de4f7c4b05bca184c73c424c07a8ce5b95b067236a19fb6a2a84e2dd4fb8`（若变更则停止并调查） |

本阶段固定开关必须在运行时快照和相关新配置/manifest 中同时出现：

```text
ai_training=false
router_enabled=false
native_four_channel_stap=false
```

## 2. A–L acceptance slots

下表是结论的最小证据索引；`result` 只能填 `passed`、`NOT_EVALUABLE` 或 `failed`，不能填“推测通过”。

| Gate | 要求 | 精确证据路径/命令 | result | 限制或缺口 |
|---|---|---|---|---|
| A | 输入为 4ch protocol IQ，科学输入严格为 F1/F2 | `<manifest/config/path>` | `<result>` | `<limitation>` |
| B | C0–C4 inverse closure | `<closure_manifest/rows>` | `<result>` | `<limitation>` |
| C | A2/A0 残差原因已解释 | `<paired_effects/closure/path>` | `<result>` | `<limitation>` |
| D | A3 target-free 且无 truth leakage | `<case manifests/estimator audit>` | `<result>` | `<limitation>` |
| E | hierarchical statistics 使用 physical scene block | `<hierarchical_manifest/effects>` | `<result>` | `<limitation>` |
| F | CFAR valid-CUT 分母与 OFF waterfall 分层 | `<cfar tap/waterfall>` | `<result>` | `<limitation>` |
| G | TrackManager 关联和 ID-switch 机制归因 | `<association/id-switch audit>` | `<result>` | `<limitation>` |
| H | materiality 在运行前冻结 | `configs/research/channel_delay_stage1_materiality.json` | `<result>` | `<limitation>` |
| I | D1/D2/D3 与传统 baseline 公平可比 | `<delay_baseline_comparison.csv>` | `<result>` | `<limitation>` |
| J | clean frozen provenance 与 v2 独立输出根 | `<v2 manifest>` | `<result>` | `<limitation>` |
| K | literature/hardware boundaries recorded | `<novelty audit>/<hardware protocol>` | `<result>` | `<limitation>` |
| L | full checks、AI/Router/STAP flags | `<test/build/ctest output>` | `<result>` | `<limitation>` |

## 3. Required scientific conclusion slots

### 3.1 A2/A0 residual cause

> 当前 paired evidence 显示 A2 相对 A0 的残差主要由 `<finite-window / packet-boundary / pulse-mask / fusion-order / amplitude-normalization / other>` 引起；证据为 `<path, rows, command>`。若未能区分这些原因，写 `NOT_EVALUABLE`，不得写“已恢复到 Ideal”。

### 3.2 Closure status

```text
C0: <passed|NOT_EVALUABLE|failed> — <evidence and reason>
C1: <passed|NOT_EVALUABLE|failed> — <evidence and reason>
C2: <passed|NOT_EVALUABLE|failed> — <evidence and reason>
C3: <passed|NOT_EVALUABLE|failed> — <evidence and reason>
C4: <passed|NOT_EVALUABLE|failed> — <evidence and reason>
```

### 3.3 Materiality and A3 recovery

填写每个预注册指标的 A1、A2、A3、`recoverable_space`、`actual_recovered`、`recovery_ratio`、CI、有效 block 数和缺失原因。threshold 为 `null` 或 `exploratory_pending` 时，只能报告描述性/探索性结果，不能写工程 pass/fail。

### 3.4 OFF false-alarm waterfall

分别填写 cell valid-CUT denominator、cell false-hit fraction、CFAR branch hit、protocol false detections、false clusters、false tracks；理论 GO-CFAR Pfa 与 structured-clutter empirical rate 必须分列。缺失 denominator 的行保持 `NOT_EVALUABLE`。

### 3.5 ID-switch attribution

报告总行数、`classification_counts`、missing production fields、候选/门限/innovation/lifecycle 字段覆盖率，以及每个机制分类的证据行。若 association audit 不完整，结论必须写“unclassifiable production fields”，不得从 ID 序列或 truth 反推机制。

### 3.6 Limitations

至少填入：hardware result 是否存在、GPU/device 是否可见、有效 CUT 分母、覆盖的 delay/scene block/SNR/velocity、未覆盖的 packet/pulse boundary、source dirty/provenance、未验证的外部泛化范围。

## 4. Stage-2A gate

```text
Stage-2A GO/NO-GO: <GO|NO_GO_STAGE1_INCOMPLETE|NO_GO_PHYSICS_AI|NOT_EVALUABLE>
reason: <exact A–L rows and evidence paths>
```

Stage-2A 只有在 Stage-1.1 A–L 全部通过或明确获得允许的 `NOT_EVALUABLE` 判定并有决策者记录后才能启动。即使 Stage-2A deterministic baseline 已运行，也不能把它反填成 Stage-1.1 的缺失证据。任何 Physics-AI core gate 失败都写 `NO_GO_PHYSICS_AI`；本模板不授权 NN、Router 或 native four-channel STAP 实现。

## 5. 可发表表述边界

在本模板全部填完且复核前，只能使用“potential contribution”“engineering evidence combination”或“under the registered conditions”。不得使用“first”“novel”“unconditionally improves Pd/Pfa/tracking”，也不得把 v1 exploratory 数字重标为 v2 final。

## 6. 最短复现入口

```bash
cd /home/shy/AIR/SAR+AI/project/PGRCC/.worktrees/unknown-system-error-next-stage
<frozen-v2-command-from-manifest>
<hierarchical/off-waterfall/id-switch analysis commands>
python3 -m pytest -q
cmake --build build -j4
ctest --test-dir build --output-on-failure
git diff --check
```
