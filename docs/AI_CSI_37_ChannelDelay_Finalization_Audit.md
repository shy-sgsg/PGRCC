# AI_CSI_37：Channel-delay Formal-v1 Finalization Audit

## 结论先行

Formal-v1 是一份已经完成、可复用的 development formal，不是 clean frozen-paper
formal。它保留了 Paper-1 的四条件链路和大量有价值的回归数字，但不能在没有补齐
fractional-delay inverse closure、真实 production valid-CUT denominator、TrackManager
association attribution、scene-block hierarchical statistics 和 clean provenance 之前，
被写成最终论文证据。

本审计不修改、不覆盖、不重新生成 Formal-v1 文件。历史文件继续作为 development
baseline；最终论文口径只能来自独立的 Formal-v2 evidence，并且必须在一个 clean frozen
commit 上运行。

## 1. Immutable source and output identity

| 项目 | 当前证据 | 判读 |
|---|---|---|
| Formal-v1 source root | `outputs/formal_delay_stage1_20260916/` | 历史 formal run root，保留 manifest、case manifest、compact source metadata 和失败现场摘要 |
| Formal-v1 compact evidence | `outputs/formal_evidence/stage1_delay/` | 九个 compact 文件，供回归和审计读取，不作为最终 clean-paper run |
| source run manifest SHA-256 | `30e0cc4b945dcc63e4e4a92fb4208653b337ca6d07e541b58f1875a61fddcb9b` | 当前实际计算值与 compact manifest 的 `source_run_manifest_sha256` 一致 |
| source status | `completed` | 表示 runner 完成了该次 development run，不表示方法学验收已完成 |
| source commit at run start | `106aaacee9c73abba133bad33f34fca4f52ce427` | 记录在 compact manifest |
| source commit at recorded end | `3ba37d8816cca6ad8f453aa6730197252b5abc74` | 记录在 compact manifest |
| source dirty state | `dirty=true` at both recorded snapshots | 不能满足 frozen-paper clean provenance |
| AI/Router/native 4ch STAP | `false/false/false` | 与本阶段主线一致，可作为约束证据 |

`outputs/formal_evidence/stage1_delay/manifest.json` 的 source-root hash 当前为
`0a26de4f7c4b05bca184c73c424c07a8ce5b95b067236a19fb6a2a84e2dd4fb8`；其中记录的
`source_run_manifest_sha256` 是上表的 `30e0cc4b...`。两个 hash 的对象不同：前者是
compact evidence manifest，后者是原始 formal run manifest。不能把二者混称为同一个
文件。

## 2. Provenance diff audit

在可见的 source commit span `106aaac..3ba37d8` 中，唯一涉及本 formal runner 的提交
差异为：

```text
scripts/run_delay_stage1_formal.py | 198 lines changed
163 insertions(+), 35 deletions(-)
```

差异内容集中在：

- `--resume` 和 `--max-cases` 参数；
- 非空 output root 的 resume 校验、config/template hash 校验和 `resume_history`；
- interrupted case preservation、raw cleanup 和 progress counters；
- `in_progress` 状态及分批 case 处理。

在这段可见 diff 中没有 D1/D2/D3 delay 公式、F1/F2 公式、A0/A1/A2/A3 correction
语义或 TrackManager gate 的直接改动。然而，运行时 manifest 明确记录了 dirty worktree，
且实际运行跨越了上述 runner 变化；因此“可见 diff 未改变科学公式”只能降低审计疑虑，
不能把本次 run 升级为 clean provenance。Formal-v2 必须在 source/config/template/runner/
analyzer 全部提交、tracked worktree clean 的 frozen commit 上重新运行。

## 3. What Formal-v1 can still support

以下内容可以作为开发回归、方法解释和下一轮设计的输入，但引用时必须标注
`Formal-v1 development formal`：

- production-equivalent 4ch protocol IQ → F1/F2 → two-channel CSI → GO-CFAR → clustering → TrackManager/PIPE 的四条件组织方式；
- target-free A1 OFF estimator boundary，以及 `F1=(C1+C3)/2`、`F2=(C2+C4)/2` 的输入关系；
- delay estimation summary、传统 baseline 对照、paired ON/OFF/TO additive audit 和现有 TrackManager payload audit 的文件结构；
- 作为数量级参考的 v1 estimator 结果：D1 mean RMSE `0.053395 ns`、D2 `0.053359 ns`、D3 `0.054587 ns`；cross-correlation `2.027616 ns`、GCC-PHAT `2.990429 ns`，fallback 均为 0；
- 作为旧口径回归参考的 A0/A1/A2/A3 target-period Pd、confirmed-track Pd、position/angle/ground-speed RMSE 和 OFF cluster/protocol/track counts；
- v1 的 15 个 registered physical scene blocks 和 delay/sweep 覆盖信息，但不把每个 delay 当作独立物理 block。

这些数字没有经过本轮新增的 closure、valid-CUT tap 和 hierarchical confirmatory
statistics，不直接构成最终 materiality 或论文优越性结论。

## 4. Why v1 is not final frozen-paper evidence

### 4.1 A2/A0 residual 未闭环

v1 的 A2 是对 A1 protocol input 使用 injected truth 进行物理 correction，A0 是零误差
scene。A2 不等于 A0 可能来自 per-packet finite-window/circular FFT、pulse/packet
boundary、校正通道与 F1/F2 fusion 的次序或幅相归一化。v1 没有 C0–C4 inverse-closure
矩阵，不能区分有限窗口不可逆效应和 correction bug。因此 A2 的提升不能直接写成
“known correction 已恢复到 ideal”。

### 4.2 CFAR cell fraction 的 valid-CUT denominator 缺失

v1 target-off waterfall 中 `cell_false_hit_fraction` 在 valid CUT denominator 为 0 时
被正确标记 `NOT_EVALUABLE`；v1 的 false clusters、protocol false detections 和 false
tracks 是独立层级，但没有从真实 `cfar_detect_kernel`/branch geometry 导出的 denominator
和 branch-specific hit identity。配置中的 `Pfa=1e-6` 只能是 theoretical GO-CFAR cell Pfa，
不能替代经验分母。

### 4.3 ID-switch attribution 尚不具备机制证据

v1 `id_switch_audit.csv` 共 1040 rows，A0/A1/A2/A3 总 ID switch 参考计数为
`234/248/268/290`；当前分类为 `other` 的比例不能证明某个机制，因为真实
association candidate/rank/innovation/gate/lifecycle 字段没有完整进入审计 join。下一轮
必须优先扩展 production `TrackManager::updateRawDetections` 的只读诊断，再把缺少字段
明确标成 `unclassifiable_production_fields`，而不是从 truth 或简化 tracker 推断。

### 4.4 v1 paired statistics 的层级不足

旧 135-case paired McNemar 将 scene/delay rows 用作探索性统计，不能把同一 physical scene
在多个 delay 下的 repeated treatment 当成独立 scene blocks。Final-v2 必须以
`seed + velocity + SNR + working point/texture/geometry` 构造 physical block，以 delay
作为 block 内 treatment，并输出 block bootstrap、delay×SNR/velocity/working-point 趋势。
旧数字保留为 `Formal-v1 exploratory`。

### 4.5 Materiality and novelty are not frozen

v1 没有在 run 前提交本阶段的 delay/Pd/track/position/angle/velocity/false-alarm/ID
switch engineering materiality file，也没有完成针对经典 calibration、multichannel SAR、
airborne GMTI/STAP 和近年工作的 novelty audit。因此 v1 不能支持“material”“first”或
“novel”这类未经审计的强 claim。

## 5. Required upgrade path to Formal-v2

Formal-v2 的 acceptance 依赖以下独立证据：

1. C0 deterministic tone/impulse、C1 LFM、C2 multi-pulse clutter、C3 4ch/F1/F2、C4 production CSI 的 inverse closure；
2. 真实 production GO-CFAR 每个 branch 的 valid-CUT denominator、hit count/hash、geometry 和 `NOT_EVALUABLE` 规则；
3. 真实 TrackManager association candidates、gates、rank、innovation、lifecycle 和缺失字段审计；
4. scene-block hierarchical effects 与 cluster-aware binary analysis；
5. run 前冻结的 materiality config、1–2 个 fair traditional baselines、literature novelty audit 和 hardware validation protocol；
6. clean frozen source commit 上的独立 v2 root，manifest 中 `dirty=false` 且 before/after commit 相同；
7. `ai_training=false`、`router_enabled=false`、`native_four_channel_stap=false` 的运行时和配置证据。

在这些证据完成前，A2/A3 的 v1 数字只用于定位下一步实验，不用于宣称 Paper-1 已
最终收口，也不触发 Stage-2A。

## 6. Retention and non-destructive cleanup record

本审计依赖的高价值内容仅为 v1 manifest、compact CSV/JSON、source/config identity、
失败/中断摘要和当前报告；大体积原始 `.bin`/`.npy` 已按上一阶段 cleanup ledger
处理，未在本审计中重新删除。任何后续清理必须先保留 manifest、command、hash、metrics
和 failure reason，再对明确的派生中间物做定点处理，不能面向 workspace root 递归删除。
