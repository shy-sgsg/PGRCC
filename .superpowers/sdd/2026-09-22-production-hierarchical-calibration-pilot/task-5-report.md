# Task 5 report — production tap audit and delay analyzer

## 交付范围

本任务完成两份中文文档、analyzer/report 逻辑和 analyzer 的 TDD red→green
测试；未把未完成的 CUDA case 或 contract run 写成算法结论。

- [AI_CSI_41_Production复校准接入点与信息边界](../../../../docs/AI_CSI_41_Production复校准接入点与信息边界.md)
- [AI_CSI_42_Production层次化校准Delay对照实验](../../../../docs/AI_CSI_42_Production层次化校准Delay对照实验.md)
- analyzer：`scripts/analyze_production_hierarchical_calibration_pilot.py`
- runner API 修复：`scripts/run_production_hierarchical_calibration_pilot.py`
- tests：`tests/test_production_hierarchical_calibration_pilot.py`

## 实现内容

1. `residual_closure` 强制 A0/PK/PKR 三个条件都存在且 residual power 在声明容差内；缺失或不闭合时禁止 estimator class 归因，并返回 `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`。
2. `causal_target_metrics` 只计算 OFF/ON/TO 的 `ON-OFF` 与 `TO`，不接收或推导 ON power protection 指标。
3. `empirical_cell_pfa` 只使用 `hit_cut_count/valid_cut_count`，分母非正为 `NOT_EVALUABLE`。
4. `waterfall_layers` 保持 CFAR、cluster、protocol detection、TrackManager track 四层独立；缺少 TrackManager evidence 不被折算为成功。
5. `classify_id_switches` 只在当前 debug 有 frame/track/truth identity 时分类，否则返回 `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`。
6. compact report/decision matrix 保留 residual closure、因果、实测 cell-Pfa、waterfall、ID-switch 五个审计状态，并固定四个允许 decision labels。
7. 修复 Task 4 runner 对已不存在的 `_estimate_delay_from_calibration` 调用，改用现有 Stage1 的 `_load_fused_from_periods`、`delay_stage1_core.delay_method_suite` 和 `_select_delay_estimate`，保持 OFF-only、truth-blind delay estimator 语义。

## TDD red → green

新增 analyzer contract 测试先运行：

```text
PYTHONPATH=. pytest -q tests/test_production_hierarchical_calibration_pilot.py -k 'analyzer_closes_a0 or analyzer_reports_causal or analyzer_keeps_cfar or analyzer_classifies_id_switches'
```

结果：退出码 `1`，4 个 `ImportError`，确认四个 analyzer API 尚不存在。

实现后同一命令结果：`4 passed`。

GPU path check 发现 runner 旧 API 后新增回归测试：

```text
PYTHONPATH=. pytest -q tests/test_production_hierarchical_calibration_pilot.py -k blind_delay_estimator
```

初始结果为 `1 failed`（`estimate_blind_delay` 不存在）；修复后结果为 `1 passed`。

相关回归全集：

```text
PYTHONPATH=. pytest -q tests/test_production_hierarchical_calibration_pilot.py tests/test_delay_stage1_contract.py tests/test_delay_stage1_provenance.py tests/test_channel_delay_correction.py tests/test_production_calibration_tap.py
```

结果：`57 passed in 16.36s`。

## GPU targeted pilot 证据

设备探测由 controller 受限提权执行：RTX 3050 Laptop GPU，driver `580.173.02`，CUDA `13.0`，P8，45°C，约 `6W/80W`。沙箱内 `nvidia-smi` 仍不能通信；本次设备探测不是算法结果。

首次 `0 ns` 单 case 因 runner 旧 API 在生产前置阶段退出；失败现场保留：

```text
.scratch/task5_targeted_gpu_zero/
```

修复后第二次命令：

```text
python3 scripts/run_production_hierarchical_calibration_pilot.py --config configs/research/production_hierarchical_calibration_delay_pilot.json --mode pilot --input-mode local --output-root .scratch/task5_targeted_gpu_zero_retry --evidence-root .scratch/task5_targeted_gpu_zero_retry/compact_evidence --delay-errors-ns 0 --max-cases 1
```

原始现场：`.scratch/task5_targeted_gpu_zero_retry/`。模拟输入、XML、production branch manifest 和 21 个 branch 日志已保留。部分 branch 正常退出并产生 TrackManager/PIPE 输出；`PKR_modeb_ON` 的日志记录 `scan beam quality gate failed: valid=0/1`、`GMTI processing failed`，case manifest 未收口，工具进程随后终止。因此本轮没有 clean formal CUDA algorithmic result，也没有运行 `±2 ns`、`±4 ns`、3 seeds×2 velocities 或 full matrix。

## Compact evidence 审计

为验证六文件 schema，在独立 scratch root 运行了 contract-only 命令：

```text
python3 scripts/run_production_hierarchical_calibration_pilot.py --config configs/research/production_hierarchical_calibration_delay_pilot.json --mode pilot --output-root .scratch/task5_contract --evidence-root .scratch/task5_contract/compact_evidence --skip-cuda --max-cases 1
```

六个文件均已生成并通过 allow-list 检查，仅在 `.scratch`，没有 stage：

```text
.scratch/task5_contract/compact_evidence/manifest.json
.scratch/task5_contract/compact_evidence/method_contract.csv
.scratch/task5_contract/compact_evidence/gamma_recovery.csv
.scratch/task5_contract/compact_evidence/clutter_metrics.csv
.scratch/task5_contract/compact_evidence/decision_matrix.csv
.scratch/task5_contract/compact_evidence/report.md
```

该 manifest 明确 `status=skip_cuda`、`algorithmic_results_claimed=false`、decision=`NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`。这些文件是 schema/contract evidence，不是 formal 算法 evidence，也未复制到 tracked evidence directory。

## 检查与限制

## Fix round 1：reviewer blocker 修复（当前提交前）

本轮修复只针对 Task 5 reviewer 指出的可导致错误 promotion 或空 blind correction 的 blocker；没有修改 `.scratch/` 或 `outputs/`，也没有运行 `±4 ns` 或全矩阵。

1. `estimate_blind_delay()` 的稳定输出字段是 `selected_delay_ns`。P1/P2 的 blind correction 现只读取该字段；其准备记录固定为 `OFF=C+N`、`truth_used_in_estimator=false`、`truth_used_to_apply_correction=false`。缺失/非有限 estimate 仍为 `NOT_EVALUABLE`，不回退 Current。
2. runner 现额外执行实际 production `A0`（ideal/no-error）branch，并收集每个 branch 的 `csi_metric_tap_summary.csv`。A0/PK/PKR closure 只选择 `roi_name=clutter_band_strong_power_top10pct` 且 `status=formal_clutter_band_strong_roi_before_power_selected` 的行；`residual_power_db = 10*log10(mean(after_mean_power))`，按唯一 period×beam 行作 arithmetic mean。缺文件、重复/非有限/非正 power、缺 reference branch 或 closure 超容差一律 `NOT_EVALUABLE`。branch exit status 不参与 closure 推断。
3. analyzer 的 algorithmic promotion 现同时要求 formal clean source、实际 CSI closure source、四层 CFAR/cluster/protocol detection/track 全部显式 evaluable、严格 `hit_cut_count/valid_cut_count` cell-Pfa、OFF/ON/TO 的四个有限 causal 字段，以及可追溯的 track association/state/payload 与 ID-switch 分类。任一 skip、failed、dirty 或缺失证据均固定 `algorithmic_results_claimed=false` 和 `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`。

新增 TDD regression 先运行：

```text
PYTHONPATH=. pytest -q tests/test_production_hierarchical_calibration_pilot.py -k 'blind_delay_preparation_uses_selected_delay or residual_closure_reads_actual_csi_after_power_by_period_and_beam or analyzer_refuses_algorithmic_claim_when_any_formal_gate_is_incomplete'
```

修复前实际结果：`7 failed`（blind/closure API 缺失；cell-Pfa、causal、layer、track provenance、dirty source 均被错误提升）。修复后实际结果：`7 passed`。

本轮完整相关 Python 回归实际结果：`67 passed in 16.48s`；Release `cmake --build build -j4` 成功，`ctest --test-dir build --output-on-failure` 为 `23/23 passed`。

当前 GPU 状态仍是 pending/failed：已有 `.scratch/task5_targeted_gpu_zero_retry/` 是 dirty worktree 的 `0 ns` partial production run，`PKR_modeb_ON` 记录 `scan beam quality gate failed: valid=0/1`，并且该次 run 不含本轮 A0 closure 接线。因此它不是 clean formal evidence，不能生成算法结论。后续 controller 必须从 clean frozen commit 重跑以下最小 GPU path check（仅 0 ns，非 `±4 ns` 全矩阵）：

```text
python3 scripts/run_production_hierarchical_calibration_pilot.py --config configs/research/production_hierarchical_calibration_delay_pilot.json --mode pilot --input-mode local --output-root .scratch/task5_fix_round1_gpu_zero --evidence-root .scratch/task5_fix_round1_gpu_zero/compact_evidence --delay-errors-ns 0 --max-cases 1
```

已运行：

```text
python3 -m py_compile scripts/analyze_production_hierarchical_calibration_pilot.py scripts/run_production_hierarchical_calibration_pilot.py tests/test_production_hierarchical_calibration_pilot.py
git diff --check
```

两项均通过。Task 5 未改 production Current 默认行为，未引入 AI/router/STAP，未触碰或提交 `outputs/`、`.scratch`。剩余必须由后续 clean formal CUDA run 补齐：A0/PK/PKR 数值 closure、`±2/±4 ns` 多 seed/velocity、完整 causal target metrics、四层 waterfall 和正式 decision。当前允许的阶段结论仍是 `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`。
