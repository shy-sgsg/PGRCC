# AI-CSI-32：Router Opportunity 与 Materiality 审计

## 当前门禁

本阶段只研究“安全 action 相对 Current 是否存在足够大的可利用余量”，不训练
AI。新门禁配置为
`configs/research/physics_ai_router_gate_v1.json`，固定
`status=development_gate`、`ai_training=false`、
`router_state=REOPEN_ROUTER_RESEARCH`。

历史 `configs/research/physics_ai_final_gate_v3.json` 保持不变。Test-V3 仍是
development-only evidence，不能直接授权训练。

## 固定 action space

| action | method | lambda |
|---|---|---:|
| A0 | Current | 0 |
| A1 | J5 | 0.5 |
| A2 | J5 | 1.0 |
| A3 | J6 | 0.5 |
| A4 | J6 | 0.75 |
| A5 | J6 | 1.0 |

该 action pruning 来自已有 development evidence，并在新 Opportunity scene 生成前
冻结。Current 是显式 safe identity baseline。

## 数据构建

配置：`configs/research/router_opportunity_v1.json`。首轮设计是 32 个 fresh
scene，八个 mechanism family 各 4 个；family assignment 与 LHS 环境轴独立，seed
namespace 为 `2026140000–2026140031`。每个 scene 严格生成：

```text
OFF = C+N
ON  = S+C+N
TO  = S-only
```

J5/J6 参数只从 OFF 估计，固定后按 action lambda 应用于 OFF/ON/TO。每个 scene 的
scene-local compact manifest 和 compact evidence 写入后立即清理该 scene 的 raw/runtime
树，不让多个 scene 的 raw 数据同时留在工作区；manifest 的 cleanup 状态在删除后
更新为 `true`。

最短入口：

```bash
python3 scripts/build_router_opportunity_dataset.py \
  --config configs/research/router_opportunity_v1.json \
  --gate-config configs/research/physics_ai_router_gate_v1.json \
  --output-dir outputs/router_opportunity_v1 \
  --build-dir build \
  --formal-dir outputs/physics_adaptive_selective_v2_formal
```

Builder 开始前会读取 MemAvailable、SwapUsed/SwapTotal、GPU free memory 和 disk
free；资源门禁失败时记录拒绝原因且不启动 CUDA。不会执行 `swapoff`。
当前 GPU 门槛为至少 2 GiB 空闲显存；该值低于 RTX 3050 的 4 GiB 总显存，
并参考同卡既有正式链路约 3 GiB 空闲且未发生 OOM 的记录。它不是 CUDA 的硬性
需求，实际运行仍必须满足 MemAvailable、GPU 和磁盘门槛；swap 只记录在
preflight/provenance 中，不再设置使用率拒绝阈值。

## 安全判定与汇总

候选 action 必须满足：

- `L_causal >= -0.25 dB`；
- Current 已检出的 target 不得丢失；
- `delta_Pfa <= 0`；
- `delta_false_clusters <= 0`。

先筛 safe action，再取 `cancellation_db` 最大者。scene 的
`safe_headroom_db = max safe cancellation - Current cancellation`，所有总体汇总按
equal-family weighting；M2 oversampling 不能改变 global opportunity 估计。

离线汇总入口：

```bash
python3 scripts/evaluate_router_materiality.py \
  --output-dir outputs/router_opportunity_v1 \
  --config configs/research/router_opportunity_v1.json \
  --gate-config configs/research/physics_ai_router_gate_v1.json \
  --development-reference outputs/target_safe_oracle_v2
```

该脚本输出 mean、median、p10/p90、equal-family bootstrap CI、
`P(headroom >= 0.05/0.10/0.25)`、positive subset、family/M2 conditional breakdown
和独立的 selected action/lambda distribution CSV。32-scene 若满足预先写入配置的低 materiality
early-stop 条件，输出 `NO_GO_AI_ROUTER_VALUE`，不扩到 96 scenes。
其中 positive opportunity 严格指 `safe_headroom_db > 0 dB`；各 sensitivity probability
按题目定义使用 `>=`。历史 12-scene family 标签从其 compact
`target_safe_oracle_v2_manifest.json` 的 `scene_records` 恢复，缺少标签时不会把 M2
集中度误报为已知结论。

### 现有 12-scene development 参考检查

对现有 compact `outputs/target_safe_oracle_v2/` 离线重算（不是新的 32-scene
Materiality Gate）得到：`safe_headroom_db > 0` 为 5/12，equal-family mean 为
0.06654 dB，`P(headroom >= 0.05/0.10/0.25)` 为 0.333/0.333/0.083。5 个正机会
scene 全部属于 M2-containing，M2-containing 的 raw headroom share 为 1.0；其中
4/5（0.8）由 `J6_Joint_Phase_Surface` 选中。这个结果支持“先审查 phase/M2 机制”的
研究动机，但不能替代新 seed namespace 下的 32-scene formal。

## 32-scene 实际结果

2026-09-13 在 clean source commit `24fc6824e477fafdc4fb59f464780f421ed7f6cd`
上实际完成 32 个 CUDA scene（8 个 family 各 4 个），生成 192 个 action candidate
rows、32 个 scene selection、32 个 inference-visible feature rows，并逐场清理 raw/runtime。
Builder manifest 记录 `source_worktree_dirty_before=false`、`scene_manifest_rows=32`、
`raw_scene_cleanup=true`；运行后的 dirty 状态来自 compact 输出本身，未生成 raw BIN/NPY/F32/log。

Materiality evaluator 的当前结论为 **`NO_GO_AI_ROUTER_VALUE`**，并触发预注册的
32-scene early-stop，不扩展至 64/96，也不进入 Learnability Audit：

| 指标 | 实际结果 |
|---|---:|
| equal-family mean safe headroom | 0.0047138 dB |
| median / p10 / p90 | 0 / 0 / 0.0034096 dB |
| bootstrap 95% CI | [0.0002131, 0.0125069] dB |
| positive opportunity rate (`headroom > 0`) | 4/32 = 0.125 |
| P(headroom ≥ 0.05 / 0.10 / 0.25 dB) | 0.03125 / 0.03125 / 0 |
| selected Current A0 | 28/32 = 0.875 |
| selected non-Current action | A2/J5 λ=1.0，4/32 = 0.125 |

因此当前状态仍为 `REOPEN_ROUTER_RESEARCH`、`ai_training=false`；没有训练 AI/MLP，
也没有创建训练授权配置。完整 compact 证据位于
`outputs/router_opportunity_v1/`，核心入口是
`router_materiality_summary.json`、`router_opportunity_v1_manifest.json` 和
`scene_manifests/`。

## 证据与限制

本文件记录实现、实际结果和复现入口；结论仅适用于当前 seed namespace、配置、
设备和 32-scene development design。V2 的失效归因、方法说明、生产
