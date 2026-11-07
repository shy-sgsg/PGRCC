# AI_CSI_38：Channel-delay Formal-v2 收口结论

**状态：** Stage-1.1 Formal-v2 结果已完成并压缩归档；本文是当前工程/研究收口，
不是无条件性能声明、硬件验收或 first/novel 论文结论。Stage-2A 仍受 A–L gate
中的 `NOT_EVALUABLE` 项约束。

## 1. Scope and provenance

科学输入和生产链固定为：

```text
4ch protocol IQ
→ F1=(C1+C3)/2, F2=(C2+C4)/2
→ target-free delay estimate
→ fractional-delay physical correction
→ two-channel CSI → production GO-CFAR → TrackManager/PIPE
```

Formal-v2 的高价值证据目录为：

- compact evidence：`outputs/formal_evidence/stage1_delay_v2_full_20260917/`；
- hierarchical evidence：`outputs/formal_evidence/stage1_delay_v2_full_20260917_hierarchical/`；
- run manifest 副本：`formal_v2_run_manifest.json`；
- 135 个 case manifest 压缩归档：`case_manifests.tar.gz`；
- 命令和退出码：`formal_v2_execution_commands.txt`；
- raw 派生物清理记录：`retention_manifest.json`。

Formal-v2 实际完成 `135/135` 个 case，覆盖 15 个 registered physical scene block、
9 个 delay（`0, ±1, ±2, ±4, ±8 ns`），每个 case 5 个 period。源码运行身份为：

| 项目 | 当前证据 |
|---|---|
| source commit before/after | `ebac0b7c649ac23295e36e8aee9ee8749631d30b` / 同值 |
| tracked dirty before/after | `false` / `false` |
| config SHA-256 | `ad47b0d02b05ddc4839da39a7498faf1af25ca3ba8eec678597c0ce7a0f651f6` |
| template SHA-256 | `1f4f9c8b9383614601dc019414b8c8a8788b195301526438b95a672b1ee13998` |
| simulator SHA-256 | `5dd0ac4e2536b8bcf2508cc53db8a30780eba75f40c025e13ffaa930f8ba7b9b` |
| pipe SHA-256 | `b62388e8d2755a7325964c30a4abacfc31423bf5ee7e389126c053fe893144f5` |
| attempt/resume | `2` / `true`；首个代表性 case 已单独完成 |
| v1 root SHA-256 | `30e0cc4b945dcc63e4e4a92fb4208653b337ca6d07e541b58f1875a61fddcb9` |
| v1 compact SHA-256 | `0a26de4f7c4b05bca184c73c424c07a8ce5b95b067236a19fb6a2a84e2dd4fb8` |
| 开关 | `ai_training=false`，`router_enabled=false`，`native_four_channel_stap=false` |

GPU 可见性已实际记录为 RTX 3050 Laptop GPU；运行前为 P8、43°C、6.18 W、7% 利用率，
运行后为 P3、53°C、13.11 W、0% 利用率。该设备有桌面后台负载，结果用于正确性和证据
生成，不作为洁净性能 benchmark。Formal-v2 raw case 树在 compact 和 manifest 校验后
删除，约回收 `7,850,145,780` bytes；run manifest 和 case manifest 归档仍保留。

## 2. Stage-1.1 acceptance A–L

| Gate | 要求 | 当前证据/命令 | result | 限制 |
|---|---|---|---|---|
| A | 4ch protocol IQ 严格按 F1/F2 进入算法 | `configs/research/channel_delay_stage1_formal.json` 的 `scientific_input` | `passed` | 仅生产两通道 CSI，native 4ch STAP 不在本阶段 |
| B | C0–C4 inverse closure | `outputs/fractional_delay_inverse_closure_20260917/closure_manifest.json`、`closure_rows.csv` | `NOT_EVALUABLE` | C0–C3 共 54 行有限输入闭环；C4 因 production input 未提供为 `NOT_EVALUABLE` |
| C | A2/A0 residual 已解释 | `A0_A1_A2_A3_summary.csv`、hierarchical `hierarchical_effects.csv`、closure rows | `NOT_EVALUABLE` | boundary residual 可见，但 finite-window、pulse/packet boundary、fusion/normalization 尚未被单独反事实分解 |
| D | A3 target-free、无 truth leakage | v2 case manifest archive、`formal_v2_run_manifest.json` 的 condition contract | `passed` | A2 仍是 known-error correction upper bound，不是 operational estimator |
| E | physical scene block hierarchical statistics | `hierarchical_manifest.json`、`hierarchical_effects.csv` | `passed` | 15 blocks×9 delays；128 行 passed、16 行 `NOT_EVALUABLE`，重复行已记录并折叠 |
| F | production GO-CFAR valid-CUT 与 OFF waterfall | `target_off_false_alarm_summary.csv`、v2 diagnostic-tap inventory | `NOT_EVALUABLE` | 540 个 cell-layer 行 `valid_cut_count=0`；clusters/protocol/track 三层可评估，不能宣称实测 cell Pfa |
| G | TrackManager 关联和 ID-switch 边界 | `track_summary.csv`、`id_switch_audit.csv`、compact manifest 分类计数 | `passed` | 1040 行均为 `unclassifiable_production_fields`，缺失 `track_association_audit_v2.csv`；机制归因本身不评估 |
| H | materiality 在 Formal-v2 前冻结 | `configs/research/channel_delay_stage1_materiality.json` | `passed` | 10 个阈值仍为 `null/exploratory_pending`，所以只报告描述性/配对统计 |
| I | 传统 baseline 公平可比 | `delay_baseline_comparison.csv` | `passed` | 7 methods、27 delay/SNR 点、每点 100 trials，共 189 rows；共享 F1/F2 和 truth-blind boundary |
| J | clean frozen provenance 和独立 v2 root | compact manifest、run manifest、retention manifest | `passed` | v2 source before/after 相同、dirty=false、v1 未覆盖；raw 派生物已归档后删除 |
| K | novelty/hardware boundary | `literature/ChannelDelay_Calibration_Novelty_Audit.md`、`experiments/ChannelDelay_Hardware_Validation_Protocol.md` | `passed` | 没有硬件实测，没有 first/novel claim |
| L | tests/build/CTest/diff 和固定 flags | 本文第 8 节最终命令 | `passed` | 仅代表当前源码检查；不替代 C4 或 valid-CUT 缺口 |

因此当前 Stage-2A 判定为：

```text
Stage-2A GO/NO-GO: NO_GO_STAGE1_INCOMPLETE
reason: B(C4 production input missing), C(A2/A0 residual not separately decomposed),
        F(production valid-CUT denominator is zero/NOT_EVALUABLE).
```

本判定不是算法失败，也不是把 `NOT_EVALUABLE` 改写成通过；若要在这些缺口存在时进入
Stage-2A，需要项目决策者对明确的 `NOT_EVALUABLE` 边界作出书面允许。当前不启动
Stage-2A runner、耦合误差实验或 Physics-AI 训练。

## 3. Current Formal-v2 results

135 个 paired scene block 的均值如下。higher-is-better 指标直接比较；lower-is-better
指标的 recovery effect 使用 `A1 - candidate`，正值表示 RMSE 下降。

| 指标 | A0 Ideal | A1 Current | A2 Known-error upper bound | A3 Blind target-free |
|---|---:|---:|---:|---:|
| target/period detection Pd | 1.0000 | 0.9556 | 0.9867 | 0.9793 |
| track Pd, all visible | 0.7867 | 0.7289 | 0.7807 | 0.7659 |
| track Pd, after confirmation | 0.9833 | 0.9111 | 0.9759 | 0.9574 |
| position RMSE (m) | 91.3616 | 220.4825 | 160.2206 | 161.4688 |
| ground-speed velocity RMSE (m/s) | 7.3739 | 10.6873 | 9.8726 | 10.2854 |
| angle RMSE (deg) | 0.0457 | 0.1358 | 0.0959 | 0.0969 |

paired scene-block statistics 为：position A2/A3 effect `60.2619/59.0136 m`，95% CI
分别为 `[35.7428,86.2300]`、`[42.9415,74.9848]`；angle effect `0.03990/0.03892 deg`，
CI 为 `[0.02349,0.05787]`、`[0.02834,0.04946]`；velocity effect `0.8147/0.4019 m/s`，
CI 均跨 0。full-scene hit 的 McNemar 结果为 A2-vs-A1 `20/4`、effect `0.1185`、
`p=0.00154`，A3-vs-A1 `12/1`、effect `0.0815`、`p=0.00342`。由于 materiality
threshold 全部 pending，这些数字不构成工程 pass/fail 或无条件收益。

### 3.1 Estimator and traditional baselines

7 个方法各有 27 个 delay/SNR 汇总点，每点 100 trials，fallback rate 均为 0：

| method | mean RMSE (ns) | max RMSE (ns) |
|---|---:|---:|
| D1 ordinary LS | 0.053395 | 0.120516 |
| D2 weighted LS | 0.053359 | 0.120917 |
| D3 Huber weighted LS | 0.054587 | 0.123712 |
| D4 generalized phase-slope ML | 0.030293 | 0.070708 |
| cross-correlation | 2.027616 | 5.192327 |
| GCC-PHAT | 2.990429 | 7.290398 |
| oversampled cross-correlation | 2.240413 | 5.700952 |

该表只说明 delay estimator 的 registered 条件下误差，不把 estimator RMSE 直接当作
生产 Pd/Pfa/tracking 改善。

### 3.2 C0–C4 closure

| level | result | current evidence |
|---|---|---|
| C0 tone/interior | `passed` | 9/9 finite，complex NMSE 最大约 `2.02e-31` |
| C1 known LFM/pulse boundary | `passed` | 9/9 finite；边界损失约 `0.25`，保留 boundary residual |
| C2 multi-pulse clutter/packet boundary | `passed` | 9/9 finite；coherence 最低约 `0.9987` |
| C3 mixed boundary/stress | `passed` | 27/27 finite；coherence 最低约 `0.9121`，不隐藏 stress residual |
| C4 production input | `NOT_EVALUABLE` | `production_input_not_supplied` |

closure 证明 fractional correction 在可供应的模拟/确定性输入上可逆；不证明当前生产
硬件链路的 C4 可逆，也不覆盖硬件时钟、线缆、IF/RF 漂移。

### 3.3 A2/A0 residual

A2 相对 A1 的 paired improvement 是当前数据支持的事实；但 A2 没有回到 A0 的原因
不能压缩为单一因素。C1/C2/C3 显示 finite window、pulse boundary、packet boundary
和 stress 条件会产生边界残差，然而当前证据没有把它们与 fusion order、幅度归一化或
生产输入边界逐一反事实隔离。因此本项保守写为 `NOT_EVALUABLE`，不写“恢复到 Ideal”。

### 3.4 OFF waterfall and valid-CUT boundary

| layer | A0 | A1 | A2 | A3 | status |
|---|---:|---:|---:|---:|---|
| cell false-hit fraction | N/E | N/E | N/E | N/E | 540 rows，valid CUT=0 |
| false clusters | 588.20 | 608.55 | 623.62 | 615.64 | 540 rows/layer，passed |
| protocol false detections | 588.20 | 608.55 | 623.62 | 615.64 | 540 rows/layer，passed |
| false tracks | 192.00 | 197.95 | 202.59 | 201.93 | 540 rows/layer，passed |

这里的 clusters/protocol/tracks 是结构杂波下的 production counts，不是 cell Pfa；配置
中的 theoretical GO-CFAR `Pfa=1e-6` 不能替代缺失的 valid-CUT denominator。

### 3.5 TrackManager and ID-switch attribution

`id_switch_audit.csv` 共 1040 行，A0/A1/A2/A3 分别为 `234/248/268/290`。所有行均为
`unclassifiable_production_fields`，缺失字段计数为
`track_association_audit_v2.csv: 1040`。这表示当前保留 production evidence 不足以
区分 candidate competition、gate crossing、reacquisition、lifecycle 或 takeover；
不从最终 ID 序列或 truth 反推机制，也不把 switch count 写成收益。

## 4. Literature and hardware boundary

文献审计已覆盖 9 个 primary DOI/official sources，当前结论只允许使用
`potential contribution`、`engineering evidence combination` 或
`under the registered conditions`。硬件 protocol 已定义四路 RF/IF、共同 10 MHz/trigger、
programmable delay、phase/drift、pre-fusion capture、仪器不确定度和 C0–C4 验收；当前
没有真实硬件结果。该缺口保持公开，不用仿真结果代替硬件验证。

## 5. Limitations and next authority needed

当前还缺：

1. production C4 输入，用于补齐真实链路 inverse closure；
2. 有效 CUT 分母不为零的 production GO-CFAR tap，或对零分母做明确的工程决策；
3. A2/A0 residual 的单因素反事实分解；
4. 非 pending 的 materiality threshold，特别是 structured-clutter false counts 和 ID-switch；
5. 硬件 delay emulator/四路采集验证。

在这些事项获得数据或项目决策前，不实现 MLP、通用 `Δalpha`/复权残差网络、RD
image-to-image、Router、native four-channel STAP/JDL、四通道 covariance/loading 或
任何以 AI 代替确定性误差估计的路径。

## 6. 最短复现与最终检查

Formal-v2 的完整命令历史在
`outputs/formal_evidence/stage1_delay_v2_full_20260917/formal_v2_execution_commands.txt`。
代码检查入口为：

```bash
python3 -m pytest -q
cmake --build build -j4
ctest --test-dir build --output-on-failure
git diff --check
```

v1 compact evidence 和 v1 manifest 的 SHA-256 见第 1 节；不得覆盖或重建 v1 目录。
