# R21：执行与质量审计

## 交付状态

R21 的最终报告位于 `docs/research/phase_r/R21/`：

- 起始审计：[`01_entry_audit_and_evidence_inventory.md`](01_entry_audit_and_evidence_inventory.md)
- evaluator 修正与回归：[`02_evaluator_v3_corrections.md`](02_evaluator_v3_corrections.md)、[`03_evaluator_regression_and_tests.md`](03_evaluator_regression_and_tests.md)
- 方法/冻结设计/执行：[`04_baseline_methods_and_input_fairness.md`](04_baseline_methods_and_input_fairness.md)、[`05_frozen_experiment_contract.md`](05_frozen_experiment_contract.md)、[`06_cuda_execution_and_identity_audit.md`](06_cuda_execution_and_identity_audit.md)
- 任务结果与机制：[`07_detection_and_task_results.md`](07_detection_and_task_results.md)、[`08_parameter_track_and_physical_closure.md`](08_parameter_track_and_physical_closure.md)、[`09_failure_mechanism_and_baseline_analysis.md`](09_failure_mechanism_and_baseline_analysis.md)
- 方向和质量：[`10_scientific_value_and_direction_decision.md`](10_scientific_value_and_direction_decision.md)、本审计与 [`12_ChatGPT_review_request.md`](12_ChatGPT_review_request.md)

机器可读交付含 17 张必需 CSV、`r21_experiment_manifest.json`、`r21_evaluation_manifest.json` 和 6 张 SVG。文本审计证据 ZIP 包含 runtime XML、日志、CSI/CFAR 摘要、Stage2 几何与逐机会结果。大体积二进制输入/数组仅在本机保留必要子集，不进入 Git。

## 验证记录

- `python3 -m pytest -q` 覆盖 R19/R20/R21 evaluator、historical adapter、CUDA runner 与最终证据汇总器的 8 个测试模块：**111 passed**。输出在 [`evidence/test_logs/r21_final_pytest.txt`](evidence/test_logs/r21_final_pytest.txt)。
- `python3 -m py_compile scripts/phase_r/r21_finalize_evidence.py`：通过。
- `python3 scripts/phase_r/r21_finalize_evidence.py --help`：CLI 正常。
- `python3 scripts/phase_r/r21_finalize_evidence.py`：从固定 CUDA 文本审计 ZIP 重新生成 17 CSV 与 6 SVG；Stage1 raw input 可用时重算 B→A0 F2 距离。
- Known closure、有效 CUT、逐机会 Doppler、运行 XML/log/manifest 身份和 area clutter 数值均已核对；没有通过降低阈值制造通过。
- 本阶段 CUDA 执行是既有 seed1001/1002 真正 Native CUDA 运行；本次收尾没有重跑 CUDA。

## 研究总纲与 Git 安全

- `docs/research/RESEARCH_MASTER_PLAN.md` 未修改；入口和当前 blob 均为 `e637e3769bde7814298ff012d0f42301884e2985`。
- 不修改算法、Production 处理参数、历史正式实验结论或冻结实验协议。
- seed1001 外层包装器失败状态未隐藏；评价重放身份单列。
- 提交范围只包含 R21 脚本、测试、精选文档与小型审计结果；不纳入大体积 IQ/CSI arrays、临时构建目录、无关工作树文件或敏感信息。

## 清理原则

清理仅在文本审计 ZIP 校验、运行 manifest/input SHA 核对及必要 A0/B F2 原始复数输入和代表性 corrected ON complex outputs 留存之后执行。每个保留与删除类别、文件数、字节数、SHA 和清理理由写入 [`evidence/cleanup_ledger.md`](evidence/cleanup_ledger.md)。若压缩归档或 raw closure 输入核验失败，则不删除相应源文件。

## 性能验证状态

- `IMPLEMENTED`: 是。
- `EXECUTED`: 两个 seed 的 Native CUDA Production 已执行，收尾重评价已执行。
- `EVALUABLE`: 有效 truth opportunity、CFAR CUT、Known closure 与参数记录可描述性评价。
- `PERFORMANCE_VERIFIED`: 否；两 seed 不等于显著性验证。
