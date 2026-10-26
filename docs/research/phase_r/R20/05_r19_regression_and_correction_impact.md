# R20 对 R19 的回归结果与修正影响

R20 使用保存的 R19 v1 result CSV 与相同 R18 saved branch 数据，按独立的 `r20-task-eval-v2` 复评。R19 源码、结果和 frozen contract 没有修改。

## 数值回归

- 覆盖 R18 的 12 个场景 × 7 条件，共 84 个 ON condition。
- 84/84 的 strict truth hit 数、有效 truth 机会、misses 和 hit fraction 与 R19 v1 相同。此处结论是旧/新评价器对这些 Detection 表的命中计数一致，不代表处理性能已在本次重跑。
- `confirmed_track_periods` 旧列实际计数是 Confirmed state observations；R20 将它明确重命名为 observation count，并另输出 unique period count。84 个条件均新增可核验 unique period 统计。
- 14/84 个条件的 `post_confirmation_match_rate` 从 1.0 更正为 0.5。共同原因是 R19 依赖 UTC float 比较/旧 period 处理，R20 用精确 result ID→Detection period 映射确定 first confirmation、分母和命中期；逐条件差异见 CSV。确认 state observation 总数保持一致。
- R18 的 sequential fragmentation 历史总数仍为 70；R20 同时计算最大并行目标 track count，12 场景 84 条条件均未发现同一期并行的多个目标航迹。R20/R8/R12 保存记录中 payload duplicate key 与 row 计数均为 0。

## 修正影响与限制

检测任务主计数没有改变；航迹“逐 period”解释有 14 个 post-confirmation match fraction 更正，并新增 confirmed unique periods 与并行/顺序分离指标。R20 没有重跑 tracker、Detection、CFAR 或 CUDA，因此这属于旧记录重新关联和分母修正。

[R20 回归 CSV](evidence/results/r20_evaluator_regression.csv) 每行保留 v1/v2 数值、差异、理由、evaluator 版本与状态；完整 R19 v1 输入结果的 SHA256 在 [R20 manifest](evidence/manifests/r20_input_identity_manifest.json)。
