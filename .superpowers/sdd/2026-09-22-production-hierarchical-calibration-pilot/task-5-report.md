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

已运行：

```text
python3 -m py_compile scripts/analyze_production_hierarchical_calibration_pilot.py scripts/run_production_hierarchical_calibration_pilot.py tests/test_production_hierarchical_calibration_pilot.py
git diff --check
```

两项均通过。Task 5 未改 production Current 默认行为，未引入 AI/router/STAP，未触碰或提交 `outputs/`、`.scratch`。剩余必须由后续 clean formal CUDA run 补齐：A0/PK/PKR 数值 closure、`±2/±4 ns` 多 seed/velocity、完整 causal target metrics、四层 waterfall 和正式 decision。当前允许的阶段结论仍是 `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`。
