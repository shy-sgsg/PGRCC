# R13 评价契约：传统 GMTI Baseline 最小比较

日期：2026-10-25。代码、运行与证据范围见 [R13 结果](R13_minimal_comparison_results.md) 和 [紧凑证据包](evidence/r13_baseline_cuda_20261024/README.md)。本契约沿用冻结的 R7 task matcher，并补足逐期 final-union CFAR 与 F1/F2 输入审计。

## 比较对象与机会单位

- 两个 seed101 场景各 3 个 period、beam 50、目标距离 85 km、SNR 30 dB、注入时延误差 0 ns：正常速度 6.7 m/s；困难挑战速度 0.5 m/s。低速仅作挑战样例，不预设为研究主线。
- 数据角色为 OFF=C+N、ON=S+C+N、TO=S。每一正确检测机会是一个可见的 `period × beam × target` 真值行；每场景 3 个机会。
- A0 是重复运行生产 Current 算子的闭合控制（`evaluator_only_current_control` 是分析标签，不是 CPU/offline 路径）；C0 是 Production Current 主比较锚点；C2 是 SCC；C3 是 Robust DDC。每种条件均运行 OFF/ON/TO。
- 目标检测匹配使用最终 Production detection 与 Stage2 truth。固定门为 range-bin 差不超过 2、Doppler 采用圆周距离且不超过 2 bins；每个 truth 与 detection 进行一对一分配。门值来自 [R7 evaluator](../../../scripts/phase_r/evaluate_r7_task_metrics.py)。
- TrackManager 正确关联只沿生产 `track_states`、`track_detections`、`track_output_payloads` 与本期 final detection 连接，并要求 `Confirmed + matched_this_frame=1`。不得用 Tentative、未命中 coast 点或历史 detection 代替。

## 指标定义

| 指标 | 定义与适用范围 |
|---|---|
| 正确检测、漏检 | R7 一对一真值匹配；ON/TO 分母分别为 3。 |
| 错误 detection / Track | OFF 无目标时全部最终 detection 为目标任务错误输出；另报本期 Confirmed 且命中的 TrackManager 关联数。二者不是虚警概率。 |
| final-union CFAR hit | 最终 `max(dynamic_hits, full_hits)` `.f32` 图中的正值唯一 cell 数；按 period、beam 统计。分母为两支 CFAR geometry 一致的有效 CUT。 |
| H0 observed hit fraction | OFF final-union 唯一 hit cells / valid CUT。仅描述当前 clutter+noise 样本，不是经验 Pfa。 |
| CSI H0 active-support power change | `10 log10(P_before/P_after)`，CSI tap 的未遮罩 active support；正数表示该支撑域输出均功率下降。该量混有杂波/噪声，不代表纯杂波消除率。 |
| TO target-only CSI retention | 真值中心 5×5 TO ROI 的 CSI 输出功率相对同 ROI CSI 输入功率。 |
| Output SCNR proxy | `10 log10(mean CSI(TO) target ROI power / mean CSI(OFF) same target ROI C+N power)`。仅是该配对场景的局部代理量。 |
| paired complex target transfer | 在 truth-centered 5×5 ROI 内，以最小二乘复增益把 CSI(TO) 映射到 CSI(ON)-CSI(OFF)。各角色会自适应，不能解释为固定线性增益。 |
| folded Doppler frequency error | 按 Production `.meta.txt` 轴（本次 130 rows、10 Hz/bin）计算圆周 Doppler 差对应的折叠频率误差。 |
| 地速误差 | 只在 R7 正确检测及当前 Confirmed 航迹关联可用时，比较 TrackManager 输出 ground speed 与真值地速。不得当作 LOS 径向速度。 |

## CFAR 与速度边界

R13 evaluator 对每张 final union 图逐项核对维度、有效 CUT mask、两个 geometry component 行、Production `[CFAR][SUMMARY]` hit count 和 `.f32` positive-cell 数；不接受用 component hit 相加或 `max` 猜测跨期 unique union。实际 130×4096 图每 period 有 527,280 个有效 CUT。

径向速度只有 ambiguity status 为 `resolved` 且真值/输出齐备时才评价。本次所有检测仍未解模糊，故 radial velocity RMSE=`NOT_EVALUABLE`；folded Doppler 与地速分开报告。经验 Pfa 需要 truth-filtered false-cell 估计和相应概率单位，本包没有该估计，故 Pfa=`NOT_EVALUABLE`。

## 证据状态标签

- `IMPLEMENTED`：R7 周期聚合修复、R13 final-union/TrackManager/F1-F2 审计代码和测试存在。
- `EXECUTED`：两场景 24 个 Native CUDA Production 分支和 72 张 period-separated final-union 图已执行并留存。
- `EVALUABLE`：仅限有有效真值/分母/关联闭合的上述具体指标；CFAR hit fraction、检测、参数和 CSI 指标各自按定义标记。
- `PERFORMANCE_VERIFIED`：整个 pilot 为 `false`。单 seed 的 3 period 不是独立重复，不得宣称统计显著 Pd/Pfa 或通用性能提升。
