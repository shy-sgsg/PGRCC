# R16 失败与未闭合项

## 实际暴露并修复：Production adapter 缺少 period_id 字段

第一次执行 R16 evaluator 时，读取真实 Production `production_calibration_adapter.csv` 的代码预期 `period_id` 字段，但当前 Production adapter schema 不提供该字段，评价器因而无法将三行 adapter 记录与 period 对齐。这是评价器 schema 假设错误，不是 CUDA 分支失败。

根据实际生产输出 `result_id`（`GMTI01`、`GMTI02`、`GMTI03`），evaluation adapter 为 period 生成 0-based 序号；若文件自带 period 字段，则同时校验一致性；并检查预期 period 集、唯一性、`Gamma=1+j0`、`ordinary_subtraction`、Production tap 来源、正 support、无 truth/reference/fallback。此规则有回归测试，见 `source_snapshot/tests/test_phase_r13_minimal_baseline_pilot.py`。修复后 evaluator 对 9 个 branch 全部完成评价，定向测试 66 passed。

首次失败没有保存单独 stderr 文件；本记录只说明已知的失败条件与修复，不声称保存了原始 traceback。

## 已运行但通用 runner gate 未闭合

- Runner 状态是 `completed_with_gaps`。它的通用 residual-closure gate 要求 A0/PK/PKR，而本次最小方法子集只运行 A0/C0/C1，因此该 gate 合理地返回 `NOT_EVALUABLE`；R16 不属于 PK/PKR closure 实验。
- Runner 的通用 valid-CUT denominator gate 因其多 detector-branch union 条件返回 `NOT_EVALUABLE`。R16 专项 evaluator 单独审计了 27 张 final-union maps、valid CUT 分母和 geometry/log 对齐。这个审计没有把 observed hit fraction 解释为经验 Pfa。
- Empirical Pfa 的 truth-filtered false-CUT 估计器和 unresolved radial velocity 都未提供，所以对应统计仍为 `NOT_EVALUABLE`。

以上状态没有通过放宽门槛或删改日志转为通过。
