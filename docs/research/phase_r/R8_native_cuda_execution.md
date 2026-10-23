# R8 本机 CUDA Production 执行记录

日期：2026-10-23
范围：R8 冻结的 A0/B/C 三条件、seed101/202/303、每条件 OFF/ON/TO、每角色三个 period。seed101 复用 R7 修复后证据；只对 seed202、303 执行新增 CUDA Production。

## 执行结论

| 状态 | 结果 | 证据 |
|---|---:|---|
| `IMPLEMENTED` | 是 | [R8 运行器](../../../scripts/phase_r/run_r8_multiseed_causal_pilot.py)、[CFAR 数值审计器](../../../scripts/phase_r/audit_r8_cfar_numeric_reference.py)、[评价器](../../../scripts/phase_r/evaluate_r8_multiseed_causal_pilot.py) |
| `EXECUTED` | 是 | seed202、seed303 共 18 组 condition×role，54 个 period 结果；均为真实本机 CUDA Production |
| `EVALUABLE` | 是 | 面积杂波、历史输入身份、A0/C 物理闭合、CFAR 数值与处理链门均通过 |
| `PERFORMANCE_VERIFIED` | 否 | 三个 seed 只报告描述性重复，不声称总体 Pd/Pfa 改善或统计显著性 |

未运行盲 D3、AI 训练或 Router；本记录不依赖 CoreX。

## 冻结身份与设备

- 仓库基线提交：`c5200b2f280f01f5e2e46f3a6fe5983a936a633f`；运行 manifest 记录运行前后源码身份与工作树状态。
- Production：`build_r7/GMTI_pipe_core`，SHA-256 `de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`。
- Stage2：`build/simulate_stage2_statistical`，SHA-256 `5dd0ac4e2536b8bcf2508cc53db8a30780eba75f40c025e13ffaa930f8ba7b9b`。
- 本机 Native CUDA，NVIDIA GeForce RTX 3050 Laptop GPU；构建缓存 `CMAKE_BUILD_TYPE=Release`、`USE_COREX=OFF`。运行前后原始 GPU 状态快照保存在 manifest。
- CUDA Production 全部使用同一修复版二进制；每个 seed 9 个分支门均通过。运行时 XML 在每个条件内 ON/OFF/TO 归一化后相同，只忽略逐运行输出目录字段 `result_add` 与 `track_debug_dir`。

## 地杂波与输入可比性

平台高度 6000 m、地面 0 m；原不可达距离窗已改为斜距 `[84.5, 85.5] km`，对应地距 `[84.2867, 85.2892] km`。seed202、303 均报告 `continuous_area_packets=390`、`continuous_area_samples=1,952,655`；测得面积杂波功率分别为 `0.111635636238`、`0.112233884509`，平均复样本噪声功率约 `0.01`。

每 seed 的 `A0_ON` 生成共享 C+N 背景；A0/A1 的 OFF、TO 与 A1_ON 复用该背景。因此部分 Stage2 replay 报告的“新增注入计数为 0”表示复用背景，不表示没有面积杂波。背景角色约定和路径写入 case manifest 与 [实验 manifest](evidence/r8_unknown_delay_multiseed_20261023/evaluation/r8_experiment_manifest.json)。seed202/303 的 A0/B/C F1/F2 输入功率及相对 A0 差异在 [逐 seed 条件表](evidence/r8_unknown_delay_multiseed_20261023/evaluation/r8_seed_condition_metrics.csv) 和 manifest 中留存。seed101 的原始 Stage2 IQ 已清理，输入功率重算明确为 `NOT_EVALUABLE`；其 R7 输入 SHA、物理闭合和任务证据仍按历史 manifest 复用。

seed202、303 重建的历史输入均为 27/27 period SHA-256 与字节数匹配，状态 `HISTORICAL_INPUT_RECONSTRUCTED_AND_VERIFIED`。

## 物理、CFAR 与生产链门

- Known Correction 与配对 A0 的 F1/F2 相对复 RMS 门限为 `1e-3`。seed101/202/303 的最大 F2 相对 RMS 分别约 `9.37e-5`、`9.13e-5`、`9.56e-5`，每 seed 的 ON/OFF/TO 均通过。
- seed202、303 各有 A/B/C × ON/OFF 共 6 份 CUDA CFAR 数值审计，合计 12 份；逐期双分支共 72 个 branch-period 检查、36 张 union map 检查通过。每 branch-period 有效 CUT 为 527,280，每三期 1,581,840。
- 保存逐 beam stage 日志、实际 runtime XML、运行日志、CSI tap 与 ROI manifest、CFAR geometry/数值参考、truth、detections、cluster lineage、TrackManager debug；文件索引与 SHA/大小登记在机器 manifest。

## 失败现场与恢复

1. seed303 的 C/ON 一次 CFAR 数值审计在普通沙箱回放时失败：`no CUDA-capable device is detected`。通过本机 GPU 提权访问后，以同一二进制、XML 和输入重试并通过；失败与成功重试都保留在 manifest。
2. seed303 的外层 runner 初次退出码与上游“completed”状态判读不一致，但 9 个 Production 分支均通过；`runner_exit_code_reconciliation.json` 记录了对账依据。没有因此重跑矩阵。

## 本地完整证据与复现入口

完整 CUDA 原始证据保存在本地（每 seed 约 4.5 GB）：

- `outputs/phase_r8_unknown_delay_multiseed_20261023/seed202/`
- `outputs/phase_r8_unknown_delay_multiseed_20261023/seed303/`

提交到仓库的是紧凑审计与评价包：[R8 evidence](evidence/r8_unknown_delay_multiseed_20261023/)。它包含 manifest、任务指标、关联与 lineage 表、目标邻域 CSV 和图；不包含约 9 GB 的重复 raw IQ/全量中间数组。大文件仍保留在本地以满足 runtime、CSI、CFAR、日志复核要求。

重跑 CUDA pilot（每次使用新的空输出目录，并按 seed202 通过后再运行 seed303）：

```bash
python3 scripts/phase_r/run_r8_multiseed_causal_pilot.py \
  --output-root outputs/phase_r8_unknown_delay_multiseed_20261023/seed202 \
  --seed 202

python3 scripts/phase_r/run_r8_multiseed_causal_pilot.py \
  --output-root outputs/phase_r8_unknown_delay_multiseed_20261023/seed303 \
  --seed 303
```

仅重建汇总表、manifest 和图（不运行 CUDA Production）：

```bash
python3 scripts/phase_r/evaluate_r8_multiseed_causal_pilot.py \
  --seed-root outputs/phase_r8_unknown_delay_multiseed_20261023/seed202 \
  --seed-root outputs/phase_r8_unknown_delay_multiseed_20261023/seed303 \
  --output-dir docs/research/phase_r/evidence/r8_unknown_delay_multiseed_20261023/evaluation
```
