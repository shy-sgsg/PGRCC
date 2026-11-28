# R21：检测与 GMTI 任务结果

## 地杂波与处理有效性

两个 seed 均确认实际连续面积杂波注入：每个 seed 390 packets、1,952,655 samples，实测功率分别为 `0.111666783002` 和 `0.110645343505`。场景地面距离窗为 84.5–85.5 km，平台高度 6 km；六个 scene 使用匹配的背景映射、scale=1。两次实测功率相差约 0.9%。因此这些结果确实来自有面积杂波输入的任务。

每个 seed 的 108 条 CFAR geometry period/band 记录都有 `527280` valid CUT；逐机会 truth range/Doppler 映射有效。完整门控分别见 [`r21_experiment_manifest.json`](evidence/results/r21_experiment_manifest.json)、[`r21_false_output_metrics.csv`](evidence/results/r21_false_output_metrics.csv) 和 [`r21_doppler_truth_mapping.csv`](evidence/results/r21_doppler_truth_mapping.csv)。

## 正确目标检测

每格为 3 个冻结目标机会中的正确命中数：

| 条件 | seed1001 | seed1002 | 描述性合计 |
|---|---:|---:|---:|
| A0，0 ns | 2/3 | 2/3 | 4/6 |
| B，+4 ns Current | 1/3 | 0/3 | 1/6 |
| Known correction | 2/3 | 2/3 | 4/6 |
| R9 fixed D3 | 2/3 | 3/3 | 5/6 |
| Huber D3 | 2/3 | 3/3 | 5/6 |
| M1 | 2/3 | 3/3 | 5/6 |

B 比 A0 在两个 seed 都少命中，Known 各自返回 A0 的命中数。相对 B，Known 可恢复机会分别为 1 和 2；盲校正观察到的命中差为 1 和 3。第二个 seed 的盲方法比 Known 多一个命中，故计数比例可大于 1；这只是小样本的机会计数，不能解释成超越已知校正上限。逐 seed 结果与恢复差值见 [`r21_seed_level_causal_metrics.csv`](evidence/results/r21_seed_level_causal_metrics.csv)，图见 [`r21_detection_hits_by_seed.svg`](evidence/figures/r21_detection_hits_by_seed.svg) 和 [`r21_detection_recovery_space.svg`](evidence/figures/r21_detection_recovery_space.svg)。

只有两个独立 seed，每个条件每 seed 3 个目标机会。上述命中数是可行性/正确性证据，不是显著性检验、统计 Pd 或普遍性能结论。

## 可评估边界

- 检测评价只统计物理映射有效、同周期一对一匹配的 truth opportunity；记录逐机会命中、漏检和原因。
- OFF observed CUT hit fraction 为描述性计数。每个 seed/方法/频带的三周期 valid CUT 分母为 1,581,840，观察比例约 `0.000188–0.000244`；空间 CUT 不独立，统计 `Pfa = NOT_EVALUABLE`。
- CSI 输出 SCNR proxy 不能代替最终 GMTI Detection。seed1001 中 B 的 proxy（70.505 dB）高于 A0（67.329 dB），但 B 命中更少；seed1002 中 B proxy 略低于 A0。该代理指标与任务结果不构成稳定单调关系。
- 完整 Detection、CFAR、H0 和各机会状态见 `evidence/results/` 的对应 CSV；不以非目标输出数冒充 Pfa。

## 状态

- `IMPLEMENTED`: 是，冻结机会和任务指标已实现。
- `EXECUTED`: 是，结果来自两个 seed 的 Native CUDA Production 分支。
- `EVALUABLE`: 对映射有效的机会成立；H0 observed fraction 仅描述可评估。
- `PERFORMANCE_VERIFIED`: 否；不能作统计性或跨场景性能声明。
