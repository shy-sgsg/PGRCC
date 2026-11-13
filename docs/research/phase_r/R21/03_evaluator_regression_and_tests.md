# R21：评价器回归与验证记录

## 历史回归

独立 `r21-task-eval-v3` 在保留的 R18/R8/R12 Production 证据上执行了历史回放。机器可读结果 [`r21_evaluator_regression.csv`](evidence/results/r21_evaluator_regression.csv) 共 672 行，所有有数值历史比较的行均标记 `EVALUABLE`。R19 的 336 个冻结任务统计逐项不变；其他 336 行记录 R8/R12 对历史 v1/v2 的对照与新 v3 状态。

历史修复细节及其边界见 [`02_evaluator_v3_corrections.md`](02_evaluator_v3_corrections.md) 和 [`02_historical_evaluator_regression.md`](02_historical_evaluator_regression.md)。测试不能替代实际 CUDA Production，历史 replay 也不能当作新实验。

## 当前验证执行

执行命令：

```bash
python3 -m pytest -q \
  tests/test_r19_task_evaluator.py \
  tests/test_r20_task_evaluator.py \
  tests/test_r20_harmonized_task_review.py \
  tests/test_r20_historical_evidence_adapter.py \
  tests/test_r21_cuda_pilot.py \
  tests/test_r21_task_evaluator.py \
  tests/test_r21_historical_task_review.py \
  tests/test_r21_finalize_evidence.py
```

结果：**111 passed in 0.60s**。逐字输出保存在 [`r21_final_pytest.txt`](evidence/test_logs/r21_final_pytest.txt)。另对最终汇总器运行了 `py_compile` 与 `--help` CLI 检查；复算真实保存证据后生成 17 张 CSV、6 张 SVG 和两个 manifest。

## 状态边界

- `IMPLEMENTED`: evaluator、回归适配器及汇总器均已存在。
- `EXECUTED`: 上述 Python 测试与保存数据的离线回归已实际运行。
- `EVALUABLE`: 仅限对应表中有有效物理机会、完整真值映射和有效检测/轨迹字段的记录。
- `PERFORMANCE_VERIFIED`: 否。测试通过不构成 Production 性能证据；性能证据来自单独记录的两次 Native CUDA pilot，且只支持描述性判断。
