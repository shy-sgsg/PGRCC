# R21 v3 历史评价器回归

## 范围与身份

本报告复核 R20 v2 已知评价问题，并将独立实现的 `r21-task-eval-v3` 应用于当前保留的 R18/R8/R12 Production 证据。该步骤是离线证据回放，不运行 Stage2、CUDA 或新的实验，也不修改 R19/R20 脚本、CSV、历史结论或原始证据。

详细数值表和逐机会审计位于 [`evidence/historical_replay_physical_range_audit/`](evidence/historical_replay_physical_range_audit/)。该目录是加入物理距离映射闭合并保存逐机会映射坐标后的权威回放；较早的 [`evidence/historical_replay/`](evidence/historical_replay/) 保留为初始回放记录，不覆盖。

## 评价边界修复

1. **TrackManager lineage：** 分开统计“有任意未匹配 payload 的 ID”“从未匹配目标的 confirmed ID”“同时存在已匹配和未匹配 payload 的混合 ID”及 payload 级未匹配数；按 Production 每结果文件的 `period_id` 关联当前周期检测。R12 seed404 M1 由旧版的 36 个 any-unmatched ID 拆解为 35 个从未目标匹配 ID，以及混合 ID `7`；41 条 confirmed payload 未匹配，其中目标匹配后有 1 条。目标在周期 1、2 匹配，检测航迹 ID 在这两个周期间发生一次顺序 handoff。
2. **Doppler：** 保留带符号间隔的轴方向和行号映射；PRF 闭合使用 `N × |spacing|`，并对每一个 truth opportunity 检查 truth 分量闭合、Production 轴 PRF 闭合及从保存 range-rate 得到的物理 Doppler 闭合。正向和反向轴均有单测。
3. **Range bin：** 仅接受有限、精确整数、落在 Scene/Production 相同 crop 距离轴中的真值 bin；同时核对 Scene `fs_hz`、`sample_delay_sec`、crop 起点与 Production XML 的 `fs`、`Rmin`，检查由 `range_m` 映射出的 Scene 和 Production 距离坐标是否都落到保存的 bin。Production 原点比较容差按 XML `Rmin` 实际序列化精度取半个末位量化单位；真值 `range_bin` 本身不会被取整或强制转换。逐机会记录映射坐标、映射 bin 和拒绝原因。
4. **状态和值：** 没有目标确认时，目标确认后的指标为 `NOT_EVALUABLE_NO_TARGET_CONFIRMATION`；当所有 truth opportunity 的物理门控均失败时，命中、漏检和命中率不提供虚假的零值。

## 历史回归结果

- R18 保存的 84 个 ON 分支共 336 项 `hits / opportunities / misses / hit_fraction` 与 R19、R20 数值逐项一致。
- 所有 210 个 R18/R8/R12 分支的 Scene 和 Production crop 轴通过一致性检查。
- 所有 210 个分支的 Scene/Production 物理距离轴（采样率、bin spacing、距离原点）通过闭合检查；588 个 ON/TO truth opportunity 的 `range_m` 到两条物理轴映射全部通过，bin 不一致数为 0。逐机会数据位于 `evidence/historical_replay_physical_range_audit/results/r21_truth_opportunity_audit.csv`。
- R18 504 个、R12 30 个、R8 seed202/303 共 36 个可用机会通过逐机会物理 Doppler 映射。R8 seed101 的 18 个机会没有保存 Production Doppler 轴，继续为不可评估；R21 不以另一 seed 的 positive control 替代该逐机会门控。
- R8/R12 相对 R20 的 84 项任务指标中，66 项数值和可评估状态不变；18 项两版均不可评估，均来自 R8 seed101 的 3 个条件 × ON/TO。没有数值变化或可评估性反转。
- R18 seed606 正控制保留为全局检查：目标专用 TO 的 3/3 候选落在冻结 ±2 Doppler 行门内；A0 ON 的 3/3 物理映射闭合。
- `r21_metric_evaluability_audit.csv` 共 724 行；不存在值为空但状态为 `EVALUABLE` 或 `EVALUABLE_WITH_EXCLUDED_OPPORTUNITIES` 的记录。
- 最终物理距离门版本回放输出 8 份 CSV；摘要 manifest 记录 210 个分支、588 个机会、0 个物理 bin 不一致，并保留上述历史冻结统计。

## 研究含义与限制

这一步证明 v3 对保留历史输入的映射和指标口径可回放，并保护 R19 冻结数字。它不是新 seed 实验，不证明 +4 ns 的任务损失、Known 恢复能力或盲估计收益具有统计稳健性。R20 紧凑包未包含原始 IQ，R8 seed101 也缺少保存的 Production Doppler 轴，因此两者不被赋予可评估性能数字。

## 状态

- `IMPLEMENTED`: 是，独立 v3 评价器与历史回放脚本已实现。
- `EXECUTED`: 是，仅离线历史回放。
- `EVALUABLE`: 对上文明确列出的历史机会和回归统计成立；seed101 目标检测结果为不可评估。
- `PERFORMANCE_VERIFIED`: 否；没有执行新的 CUDA pilot。

## 本轮验证命令

```bash
python3 -m pytest -q tests/test_r21_task_evaluator.py tests/test_r21_historical_task_review.py tests/test_r21_cuda_pilot.py tests/test_r20_task_evaluator.py tests/test_r20_harmonized_task_review.py tests/test_r20_historical_evidence_adapter.py tests/test_r19_task_evaluator.py
python3 scripts/phase_r/r21_historical_task_review.py --output-root /tmp/r21-historical-replay-range-axis-audit-20261027
```

最终执行版本和命令结果还会在阶段总报告中注明；这份回放只读取保留的 R18/R8/R12 历史 Production 证据。
