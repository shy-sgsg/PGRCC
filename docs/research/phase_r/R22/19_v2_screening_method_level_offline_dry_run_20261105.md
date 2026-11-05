# R22 V2 screening 方法级可评价性离线复核

> **范围：** 仅对已归档的 V2 screening 终态 case 做离线重分类，核对新方法级状态契约能否保留有效方法结果并隔离 C4 缺口。没有重跑 Production/CUDA，没有修改 V2 manifest、case 结果或门槛。该复核不是 V3 执行或统计性能验证。

## 身份与输入

| 项目 | 身份 |
|---|---|
| V2 设计 | `R22_EXTENSION_20261028_V2_RANGE_ROI` |
| 设计配置 | `config/r22_extension_experiment_design_v2_range_roi.json` |
| 最终矩阵 manifest | `evidence/screening_checkpoint/screening_final_manifest.json`，SHA-256 `8f3b54fa6a43368b7b5c98f5e8d07e9b28d1ed5a1f388ae46fa61988e0ce3fdb` |
| 离线核对代码快照 | `scripts/phase_r/r22_experiment_matrix.py` 与 `scripts/phase_r/run_r22_cuda_pilot.py`，工作分支执行前 HEAD `05f95db72fe3f4fe07d8f64cad839d95e2d800fa` |
| 失败 ZIP | `outputs/r22_full_v2_compact/failures/screening/screening__seed_1932001__vr_2d5__snr_30__delay_4ns__targets_20__periods_10__GATE_FAILED_attempt_001.zip`，SHA-256 `be76237104d6c6f11f1681de556e49ecad5e694658e8d5c94ce3e7c05850890b` |
| 终态样本范围 | seed1932001 的18个唯一终态 case；17 `PASSED`、1 `GATE_FAILED`。矩阵共22条 attempt 记录，4条为中断 attempt，不算新增终态 case |

18个终态 case ZIP 均完成 CRC 检查。17个成功 ZIP 的 SHA-256 与 matrix manifest 记录一致；失败 ZIP 与 checkpoint provenance 记录一致。4条中断 attempt 保留为历史 attempt，不纳入终态可评价性分母。

SHA/CRC 检查只确认当前归档未损坏，不等于重新执行Stage2/Production。此处统计的 `EVALUABLE` 是归档 `task_metrics.csv` 中原 evaluator 保存的状态；本次只重分类既有行，没有原始 IQ，也没有完整 target truth CSV 来独立重跑truth join。独立审查已将原始target truth读取标为 `NOT_VERIFIED`，本报告沿用该限制。

## 离线复核结果

| 检查项 | 结果 |
|---|---:|
| 冻结计划 case 数 | 288 |
| 唯一终态 case | 18（全部来自 seed1932001） |
| 全局面积杂波门通过 | 18/18 |
| +4 ns 配对 causal triplet 门通过 | 9/9；0 ns 按冻结设计不要求该非零误差 triplet |
| V2 原矩阵状态 | 17 `PASSED`、1 `GATE_FAILED`；剩余270个计划 case 未开始 |
| 终态 ZIP CRC | 18/18 无错误 |
| archive SHA 身份 | 17/17成功 ZIP 匹配 matrix；失败 ZIP 匹配 checkpoint provenance |

按 V2 固定的零时延适用性规则，零时延 correction 不适用项不进入任务分母。18个终态 case 包含9个0 ns case，每个有12个适用的 ON 方法/条件单元；另有9个+4 ns case，每个有18个适用单元。因此此终态子集的计划适用单元数为：

\[
9\times12+9\times18=270
\]

归档 `evaluation/task_metrics.csv` 中这270个 ON 方法/条件单元的**既有 evaluator 状态**为269个 `EVALUABLE`、1个 `NOT_EVALUABLE`。缺失单元唯一对应 `C4 / Mode-B / ON`，原因 `reference_status_not_ok`：该 C4 reference group support=7，小于冻结的8。其他方法/适用条件在这18个终态 case 中均有 ON 任务行，且原 task CSV 将这些行标为可评价；本轮没有重算其任务指标或重新关联原始truth。C4 Mode-B 的 OFF/TO 依赖角色也未完成，故三个角色的任务行均标为不可评价；这三行对应同一个 case×method 缺口，不能当作三个独立失败样本。

失败 case 的归档场景级记录显示连续面积杂波为1300 packets、6,508,782 samples，面积杂波门通过；四个 causal triplet 状态均记录为通过。逐分支 OFF 记录中，其他已生成的方法分支各自记录了 valid-CUT denominator 与 unique-hit 状态。由于 C4 Mode-B OFF 参考缺失，原 V2 汇总的 `valid_cut_denominators` 和 `unique_final_union_hits` 仍为 `NOT_EVALUABLE`；`empirical_pfa` 也为 `NOT_EVALUABLE`。上述是对已保存记录的核对，并未从原始IQ/完整truth重新确认。新契约可以保留其他方法原有的**已评价记录**，但不能把该 case 标为全方法完整，也不能恢复全方法排名或虚警结论。

### 方法×条件覆盖（仅18个终态 case）

| 方法/条件 | 可评价 / 适用单元 | 说明 |
|---|---:|---|
| A0、C0、C1、PK | 各18/18 | 仅记作该终态子集覆盖 |
| C2 Mode-A/B、C3 Mode-A/B、C4 Mode-A | 各18/18 | C4 Mode-A 与 C4 Mode-B 分开统计 |
| C4 Mode-B | **17/18** | 唯一不可评价单元是30 dB、2.5 m/s、+4 ns失败 case |
| P1、P2 Mode-A/B、R22 D3、M1、R9 correction | 各9/9 | 仅适用于9个+4 ns终态 case |

这里的覆盖率描述当前归档子集的可评价性，不是独立重复统计，也不代表其余screening条件。18个case均来自同一个seed；270个方法×条件行不能替代独立seed，也不能用于显著性、Pd/Pfa或泛化结论。

## 对 V3 契约的离线判定

离线重分类支持将两种状态分开，但它只验证压缩归档内记录的传播语义：

1. **场景/输入门记录：** 本次失败 case 的面积杂波门及ON/OFF/TO配对、causal triplet状态在归档中记录为通过；这不等于独立重放了原始IQ或复核了原始truth。V3若要求重新验证原始truth lineage，当前compact包不足，须保留 `NOT_VERIFIED`。
2. **方法级完整性：** C4 Mode-B reference 缺口使该方法在该case不可评价，并使V2的全方法聚合门关闭；它不应抹掉其他方法原有的有效Production与任务行。离线dry-run只保留其历史标记，不将其升级为新执行或新评估。

这只是根据冻结归档执行的离线演练。当前矩阵 runner 仍按V2规则在首个 `GATE_FAILED` 后停止；V3状态机尚未实现。不得通过 `--resume` 恢复V2，不得修改历史状态，也不得把这些历史记录标成 `V3 EXECUTED`。

## 状态与限制

- **IMPLEMENTED：** V3 runner/evaluator 尚未实现；本文件定义离线复核与契约需求。
- **EXECUTED：** 仅完成归档 ZIP、manifest、task rows 的离线读取与重新分类；没有新 CUDA/Production 执行。
- **EVALUABLE：** 18个终态 ZIP、其CRC/SHA身份、全局杂波/配对门，以及270个ON方法任务状态可核对。
- **PERFORMANCE_VERIFIED：** 否。单seed、18个条件case的状态复核不是性能确认实验。
- 原始 Stage2 IQ 不在compact case ZIP中；本轮只评估压缩归档已有字段，不重新构造或推断缺失IQ。
- 独立 H0 60/60 是另一矩阵，不能用来补本子集的经验Pfa，也不能解除 target-present gate。

下一步设计草案见 [R22-V3 方法级可评价性契约](20_v3_method_level_evaluability_contract_draft_20261105.md)。
