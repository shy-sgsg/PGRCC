# R21：v3 评价器修正

## 结论

R20 审查中发现的航迹语义、Doppler 轴方向、truth range-bin、不可评价状态和值分母问题，已在独立 `r21-task-eval-v3` 路径中修正。没有回写或改动 R19/R20 evaluator 与历史结果。实现和历史案例细节见 [`02_historical_evaluator_regression.md`](02_historical_evaluator_regression.md)；机器可读回归表为 [`r21_evaluator_regression.csv`](evidence/results/r21_evaluator_regression.csv)。

## 修正项

1. **TrackManager lineage：** 分开报告有任意未匹配 payload 的 ID、从未匹配目标的 confirmed ID、混合 ID、payload 级错误数、目标匹配周期、handoff、同时多目标与跨目标关联。R12 seed404 的旧 36 个 any-unmatched ID 被正确拆分为 35 个从未匹配目标 ID 与混合 ID `7`；没有把混合轨迹整体算成虚假航迹。
2. **Doppler 映射：** 保留有符号轴 spacing、行号方向、PRF 闭合和 range-rate 独立闭合；按每个 target opportunity 核验，不以跨场景 positive control 替代单机会验证。正向与反向轴都有回归覆盖。
3. **Range-bin：** truth bin 必须有限、精确为整数、位于实际距离轴范围内，并与 Scene/Production 物理距离映射相符。非法项保留审计行，但从有效机会分母排除；不再静默取整或强制转换。
4. **状态与分母：** 没有目标确认、没有有效物理机会、无匹配或关键字段歧义时均输出显式不可评价状态。不得出现空值却标为 `EVALUABLE`，也不得把物理门控失败伪装为零命中。
5. **Ground speed：** 只使用当前周期、已确认且唯一关联到当前匹配 detection 的 Production payload；Tentative、重复 payload 与不唯一关联排除。

## 历史保护

- R18 的 336 个冻结 `hits / opportunities / misses / hit_fraction` 与 R19 完全相同。
- Scene/Production 距离轴和 588 个历史机会的 range 映射闭合，物理 bin 不一致数为 0。
- 642 个历史/当前 Doppler opportunity 映射为 `MAPPING_VERIFIED`；18 条旧 R8 seed101 记录因缺 Production Doppler axis 仍不可验证。当前 seed1001/1002 的 72 条机会均通过映射。
- R19/R20 文件、历史 CSV 和结论保持原样；R21 以追加记录提供新解释。

## 状态

- `IMPLEMENTED`: 是，独立 v3 evaluator 与审计适配器已实现。
- `EXECUTED`: 是，历史 Production 记录离线重评；此步骤没有 CUDA 执行。
- `EVALUABLE`: 对逐行状态明确、输入字段齐备的历史机会成立；缺少 axis 的记录仍是 `NOT_VERIFIED`。
- `PERFORMANCE_VERIFIED`: 不适用；此报告验证评价口径，不验证算法性能。

详细历史表、单位边界及限制以 [`02_historical_evaluator_regression.md`](02_historical_evaluator_regression.md) 为准。
