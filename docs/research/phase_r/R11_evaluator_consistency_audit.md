# R11 评价器一致性审计

日期：2026-10-24；基线：`b5b8f954abab426b6810d1cbefeb8faca4e11960`。范围：离线复核 R11 的六份 CUDA Production detection 与 cluster lineage，不重跑 Production、Stage2 或 CUDA。

## 审计结论

R11 的 Production 输出本身没有被这次审计判为错误。问题出在 R11 分析器把**几何定位匹配**的输出称作冻结任务匹配：`run_r11_local_delay_scan.py::run_match()` 调用 `gmti_eval_match.one_to_one_match()`，依据时空、距离和 EN 位置误差选取几何上更近的 detection；它没有使用 R7/R8 的 truth Doppler 行与 circular Doppler 门限。

使用 R7/R8 已冻结的 `evaluate_r7_task_metrics.evaluate_case()` 重评后，六个校正量都把同一真值关联到 Production detection `(48,2154)`。检测 `(129,2155)` 与真值 range bin 相差 1，range 门可通过；但实际 Doppler 轴映射的真值行为 48，圆周有符号差为 `−49` bins（绝对差 49），超出 ±2 bins 冻结门，因此不得作为正确目标 detection。其 cluster 虽被选中并产生了 Production detection，仍不满足任务关联门。

所以原报告所谓“前五点目标候选与 4 ns 候选切换”，只发生在 R11 的几何匹配诊断结果中；R7/R8 任务级关联并未切换。冻结门下的目标位置误差从 `67.580839 m` 随校正量提高而降至 `10.756963 m`，角度误差也从 `0.04399793°` 降至 `0.00692256°`。这修正了 R11 对任务级趋势的解释，但仍只是一个 seed、一个 period、一个真值机会。

## 根因与可复现核对

| 比较项 | R11 原分析 | R7/R8 冻结任务口径 |
|---|---|---|
| 实际调用 | `gmti_eval_match.one_to_one_match()` | `evaluate_r7_task_metrics.evaluate_case()` |
| 配对规则 | localization config 的时间、距离、位置代价；关闭速度门 | 同 period、同 beam；range ±2 bins、circular Doppler ±2 bins |
| lineage 条件 | 原 runner 的几何候选匹配不消费 cluster lineage | 要求 Production candidate 对应 selected 且有 raw detection 的 cluster |
| `(129,2155)` 对真值的判定 | 前五个校正量中几何代价较低，因而被选作诊断候选 | range `+1` bin、Doppler `−49` bins；由 Doppler gate 拒绝 |
| `(48,2154)` 对真值的判定 | 固定 cell 诊断，原分析前五点未用其作正式匹配误差 | 六个校正量均在门内；range `0`、Doppler `0` |

离线复核还以原 `scan_metrics.csv` 作对照：六个旧几何匹配 cell 和几何位置误差均被原算法逐点复现。新脚本把两种结果保存在不同字段和不同 CSV 中，几何匹配不能写入 `frozen_r8` 任务指标。

## 回归和历史结果保护

新增 7 项回归覆盖了 R11 实际 cell `(48,2154)`、`(129,2155)` 超 Doppler 门、Doppler 圆周边界、range ±2 边界、period/beam 隔离、一对多候选的一对一分配，以及几何结果不能覆盖冻结关联。运行结果：`7 passed`。

随后用当前 R7 evaluator 对既有归档 manifest 逐份复算并与原 JSON 深比较：R8 `18/18`、R9 `12/12` 完全相同；无缺失、差异或复算错误。没有改写 R8/R9 目录或历史指标。

可复跑命令：

```bash
python3 -m pytest -q tests/test_r11_evaluator_consistency.py
python3 scripts/phase_r/reanalyze_r11_evaluator_consistency.py
```

第二条命令从本机 R11 输出读取原始六组小型 detection/lineage 文件，将本次输入复制到审计包 `inputs/`，并生成结果。脚本也可从紧凑输入包复算 R11：

```bash
python3 scripts/phase_r/reanalyze_r11_evaluator_consistency.py \
  --r11-root docs/research/phase_r/evidence/r11_evaluator_consistency_20261024/inputs \
  --output-dir /tmp/r11_evaluator_recheck
```

R8/R9 历史 manifest 的全量一致性复核需要本机保留对应历史 Production CSV 与 R9 evaluation 目录；其本次逐案差异记录随审计包保存。

## 产物和状态

- 脚本：[reanalyze_r11_evaluator_consistency.py](../../../scripts/phase_r/reanalyze_r11_evaluator_consistency.py)
- 回归：[test_r11_evaluator_consistency.py](../../../tests/test_r11_evaluator_consistency.py)
- 机器结果：[r11_reanalysis.json](evidence/r11_evaluator_consistency_20261024/r11_reanalysis.json)、[r11_reanalysis.csv](evidence/r11_evaluator_consistency_20261024/r11_reanalysis.csv)
- 逐候选门限证据：[r11_candidate_pair_audit.csv](evidence/r11_evaluator_consistency_20261024/r11_candidate_pair_audit.csv)
- 原几何匹配与冻结任务结果差异：[r11_evaluator_difference_records.csv](evidence/r11_evaluator_consistency_20261024/r11_evaluator_difference_records.csv)
- R8/R9 历史复算：[r8_r9_historical_consistency.json](evidence/r11_evaluator_consistency_20261024/r8_r9_historical_consistency.json)
- 精简复算输入：[inputs/](evidence/r11_evaluator_consistency_20261024/inputs/)

- `IMPLEMENTED`：独立离线重评脚本与门限回归已实现。
- `EXECUTED`：六组 R11 数据重评以及 30 份 R8/R9 历史 task metrics 复算均已运行。
- `EVALUABLE`：R11 的 detection、selected/raw lineage、Doppler axis、目标真值和固定 cell 参数记录齐全。
- `PERFORMANCE_VERIFIED`：仅限当前 seed303/period1/beam50 的一个目标机会及受控校正扫描；不表示独立 Pd/Pfa 统计或跨场景性能。
