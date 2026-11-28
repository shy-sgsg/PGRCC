# R21：固定通道时延 CUDA 因果 pilot

R21 已完成并停止。该阶段没有训练 AI、启动 R22、增加第三个 seed 或改变研究总纲。研究主题与长期技术路线仍以 [`RESEARCH_MASTER_PLAN.md`](../../RESEARCH_MASTER_PLAN.md) 为准；动态阶段状态见 [`RESEARCH_STATUS.md`](../../RESEARCH_STATUS.md)，方向决策追加在 [`RESEARCH_DECISION_LOG.md`](../../RESEARCH_DECISION_LOG.md)。

## 推荐阅读顺序

1. [`00_master_execution_plan.md`](00_master_execution_plan.md) 与 [`01_entry_audit_and_evidence_inventory.md`](01_entry_audit_and_evidence_inventory.md)：研究定位、入口状态、范围与证据来源。
2. [`02_evaluator_v3_corrections.md`](02_evaluator_v3_corrections.md)、[`03_evaluator_regression_and_tests.md`](03_evaluator_regression_and_tests.md)：评价器缺陷、历史保护和实际测试。
3. [`04_baseline_methods_and_input_fairness.md`](04_baseline_methods_and_input_fairness.md)、[`05_frozen_experiment_contract.md`](05_frozen_experiment_contract.md)：方法信息边界和运行前冻结条件。
4. [`06_cuda_execution_and_identity_audit.md`](06_cuda_execution_and_identity_audit.md)、[`07_detection_and_task_results.md`](07_detection_and_task_results.md)：Native CUDA 身份、杂波、检测和 Pfa 限制。
5. [`08_parameter_track_and_physical_closure.md`](08_parameter_track_and_physical_closure.md)、[`09_failure_mechanism_and_baseline_analysis.md`](09_failure_mechanism_and_baseline_analysis.md)：共同成功参数比较、物理闭合与失败机会。
6. [`13_offline_m1_residual_and_cost_audit.md`](13_offline_m1_residual_and_cost_audit.md)：用保留输入复核 M1 的 C2/F2 残差、优化收敛与 CPU 计算代价。
7. [`10_scientific_value_and_direction_decision.md`](10_scientific_value_and_direction_decision.md)、[`11_execution_and_quality_audit.md`](11_execution_and_quality_audit.md)：最终阶段判断、质量门与限制。
8. [`12_ChatGPT_review_request.md`](12_ChatGPT_review_request.md)：固定证据 commit 的独立 ChatGPT 审查提示词。

`02_historical_evaluator_regression.md`、`03_method_fairness_and_frozen_design.md`、`05_experiment_design.md`、`06_minimal_cuda_causal_pilot.md` 与 `07_preliminary_direction_assessment.md` 均为早期或阶段执行快照。最终逐项判断以 canonical `02`–`12` 文档及其引用的 `evidence/results/` 为准。

## 机器结果与证据

- 17 张 CSV、两个 JSON manifest：[`evidence/results/`](evidence/results/)。
- 两个 seed 的 M1 离线诊断 JSON：[`seed1001`](evidence/results/r21_m1_offline_diagnostic_seed1001.json)、[`seed1002`](evidence/results/r21_m1_offline_diagnostic_seed1002.json)。
- 6 张实际结果图：[`evidence/figures/`](evidence/figures/)。
- Runtime XML、Production 日志、CSI/CFAR 摘要、Stage2 几何、truth 与逐机会记录：[`r21_cuda_text_audit_evidence_20261027.zip`](evidence/cuda_pilot/r21_cuda_text_audit_evidence_20261027.zip)。
- 本机必要原始数组保留与二进制清理台账：[`evidence/cleanup_ledger.md`](evidence/cleanup_ledger.md)、[`evidence/inputs/r21_retained_binary_manifest.json`](evidence/inputs/r21_retained_binary_manifest.json)。原始二进制不在 Git 中。
- 最终定向回归输出：[`evidence/test_logs/r21_final_pytest.txt`](evidence/test_logs/r21_final_pytest.txt)。

## 当前裁决

R21 阶段 `PASS_WITH_LIMITATIONS`；固定静态时延估计 `CONTINUE`，仅作为有界确定性候选。已知校正在该冻结场景恢复 A0 命中数并通过 F2 闭合；M1 相对 R9/Huber 的 angle/position 与 F2 残差在两个 seed 方向一致地改善，但仍落后 Known。广义未知状态 `NOT_VERIFIED`，AI entry gate `FAIL`，`PERFORMANCE_VERIFIED=false`。两个 seed 是描述性可行性证据，不支持统计显著性或广泛外推。
