# R20 评价器修正与测试

R20 新增独立契约 `r20-task-eval-v2`。R19 v1 源码、冻结契约和已提交结果保持不变。本模块只修正离线评价，不改 Production 算法。

## 修正项

1. **TrackManager period 映射：** 状态表的 `result_id` 与 Detection 结果的 period 对应关系用精确整数 key 映射。事件的 `GMTI01` 等 ID 也映射到实际 Detection period。UTC float 只用于记录精度差，不再用作 period join；事件自身报告的 period 不覆盖精确结果映射。未映射 result ID 会报错，映射审计在 `track_period_mapping_audit`。
2. **目标机会分母：** 可见 truth 先逐行校验 `range_bin/range_m/e_mid/n_mid/af_total_truth_hz`。非法 truth 进入审计并排除于有效漏检分母；`visible_truth_opportunities_total`、`valid_truth_opportunities`、`invalid_truth_opportunities` 和 `misses` 分开保存。
3. **检测字段 fallback：** 仅 `range_bin` 缺失/空值时允许从合法 `col` 回退。若字段存在但非法，删除局部副本中的 `col` fallback，按无效 range-bin 排除命中并增加 `invalid_detection_rows`；逐候选审计标记 `invalid_present_range_bin`。
4. **参数完整率：** 每个参数分别输出 truth opportunity、匹配 detection、有效参数样本、缺失数和缺失原因。RMSE 仅在字段有效的成功样本上计算，不能把成功样本 RMSE 当作总体可评估率。
5. **身份完整性：** `validate_comparison_identity_v2` 要求 schema 声明的必需字段在每个 identity row 中存在；所需相等字段不一致时报 `IDENTITY_MISMATCH`，干预变量单列，不再对两边现存键静默求交集。
6. **航迹语义：** 分开记录 Confirmed state observations、unique periods、目标匹配 periods、并行目标航迹数和观测到的跨 period disjoint-ID handoff。`track_fragmentation_count_observed` 不再把并行航迹与顺序 fragment 混为一谈。重复 payload rows/keys 有独立计数。

## 测试与实际结果

新增 [test_r20_task_evaluator.py](../../../../tests/test_r20_task_evaluator.py) 的 9 个测试，覆盖：身份字段缺失/不匹配/允许变化、非法 truth 分母、range-bin missing 与 present-invalid、参数缺失原因、Doppler 正反轴、result ID period 映射与 UTC 精度、重复 payload/并行航迹/顺序 fragmentation。

- 首次 range-bin 定向测试按预期 RED：旧匹配器仍从非法 present field 回退到 `col`。
- 修复后：`9 passed`。
- 三个 R20 Python 文件 `py_compile` 通过。
- 完整历史复评入口已执行并生成 210 个 branch identity、196 个 ON/TO evaluation 和 13 个结果/manifest 文件；机器门为 `DOPPLER_MAPPING_VERIFIED`。

R20 这些软件测试证明评价契约行为，不是 CUDA 或 GMTI 算法性能证据。结果差异以 [R19 回归表](evidence/results/r20_evaluator_regression.csv) 为准。
