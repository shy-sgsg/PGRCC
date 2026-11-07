# R10：研究方向裁决

日期：2026-10-23
基线：86574881277692f052fb5470da6c83f3a2da5752
阶段状态：已完成；按要求停止，不自动启动后续阶段。

## 结论摘要

固定离线 D3 将未知 +4 ns 场景的正确 detection 从 R8 B 的 0/6 恢复到 5/6，与 A0/Known 的计数相同，但定位精度仍明显落后 Known。三个正确匹配 period 的 A0 与 D3 候选 cell 相同；这些同 cell 记录的 phase 改变量通过生产 solver 的局部 Jacobian 预测了实际 cross-range 改变量，最大闭合残差 0.35 m。另两个 seed303 period 的候选 row 改变，是额外混杂因素。

因此，检测恢复和高精度定位恢复需要不同的复数相位精度。现有证据显示 Known correction 相对固定 D3 仍有可恢复的定位空间，并支持优先提高物理通道 2 的确定性标定精度及校准模型匹配；但不能把 residual phase 的全部来源唯一归因于 delay bias。没有证据要求在线 AI。

## Q1–Q8

| 问题 | 裁决 | 证据和边界 |
|---|---|---|
| Q1 固定 D3 和 Known 的 target candidate 是否相同？ | A0 与 Known 在全部 5 个正确 period 完全同 cell。固定 D3 与 A0 在 3/5 同 cell；seed303 period 0、2 的 row 各偏一格。在线 D3 候选 cell 与固定 D3 相同。 | seed202 period 1 所有条件均未通过 gate；同一个候选在 Doppler 方向偏 4 bins。详情见目标关联报告。 |
| Q2 固定 D3 定位退化主要是角度、候选还是航迹关联？ | 主要证据指向同一候选处的测量相位/角几何误差；candidate 变化影响 seed303 两期，但无法解释三个同 cell period。Confirmed 目标关联计数各条件都是 3/6。 | Track ID 运行间不同；period 0 均为 Tentative。定位差不是由不同 track ID 数字本身推导出来。 |
| Q3 residual phase 与实际角误差之间有可验证的物理关系吗？ | 有，局限于本生产处理链：条件间 raw CTDR phase 与 angle change 同向，局部比率约 1.28 deg/rad；生产 CTDR finite-difference Jacobian 对 cross-range 变化的预测残差 ≤0.35 m。 | 这验证 phase→production geometry，不证明物理时延是 residual phase 的唯一成因。 |
| Q4 D3 的主要限制是 estimator bias、fusion mismatch 还是 observability 不足？ | 单一主因不可辨识。实验里固定 D3 比 +4 ns 真值低约 0.590 ns；输入使用融合 F1/F2 估计、校正却只写物理 ch2，且 Stage2 delay 是频率相关的分数插值算子。 | 可确认估计偏差和结构性模型差异都存在；当前缺少目标局部每频点 ch2/F2 复响应及独立重复，不能分解相对贡献或 phase 方差。 |
| Q5 更准确物理参数估计是否可能继续降低定位误差？ | 有证据支持“存在可能且有上限空间”：Known 的 angle/position RMSE 接近 A0，明显优于固定 D3；raw phase→cross-range 敏感度也足以解释差距。 | Known 使用 evaluator 真值，不证明当前盲估计器能达到该上限。需要后续验证物理 ch2 参数估计是否可稳定达到 Known-like correction。 |
| Q6 固定标定是否基本解决当前 detection 任务？ | 对这两个 seed 的正确 detection 数，基本恢复：A0、Known、固定 D3、在线 D3 均为 5/6。 | seed202 period 1 仍漏检，且没有更大场景、长时间稳定性或统计性 Pd/Pfa 证据。“计数恢复”不等于定位/track 完全恢复。 |
| Q7 是否存在必须依赖在线 AI 的性能问题？ | 没有证据。固定确定性标定已恢复本次检测计数；在线 D3 没有带来角度/定位优势，反而 RMSE 更高。 | 两个 seed 不足以否定所有 AI 方法，但当前没有满足 AI 进入门槛的残差估计证据或不可解析状态估计需求。 |
| Q8 下一步优先更精确物理标定，还是 residual mismatch 下稳健定位？ | 先提高物理 ch2 时延的确定性标定/观测模型匹配；在 Known-like 校正下仍剩余的定位误差，再转向残余失配下的稳健角度/定位。 | 当前 Known 上限差距和同 cell phase→geometry 证据使物理校准优先级更高；这不是立即扩大在线估计或引入 AI 的理由。 |

## 阶段标签

- IMPLEMENTED：固定/在线确定性时延估计、物理校正算子、production CTDR 定位与局部 Jacobian 输出已存在。
- EXECUTED：R8/R9 的 Native CUDA Production 与关联评价已完成；R10 只读复核，没有新 CUDA。
- EVALUABLE：当前两个 seed 的候选 lineage、检测计数、angle/position/ground-speed RMSE 以及 phase→cross-range 局部关系。
- PERFORMANCE_VERIFIED：只支持本次两 seed、三期任务和当前 R8/R9 处理链的有限结论；不支持统计显著性、跨时间/温度泛化或 Pd/Pfa 总体性能主张。

## 下一步建议及停止边界

本阶段不自动执行新的实验。若用户批准一个后续验证，建议将其限制为一个已有输入上的预注册局部检查：

- 选择 seed303 period 1：A0、Known、固定 D3、在线 D3 都选中 row 48 / col 2154，避免本次已见的候选 cell 变化。
- 重用 R8 seed303 的 +4 ns ON protocol input，不生成 Stage2；Native CUDA、USE_COREX=OFF；保持相同配置和生产算子。
- 在运行前冻结标量校正量 {3.009965, 3.209965, 3.409965, 3.609965, 3.809965, 4.000000} ns。4 ns 只在 evaluator 中作为 Known 参考；其它点不得读取 truth 或配对 A0。
- 只保存物理 ch2 和融合 F2 的频响、目标 cell 的 CSI complex ROI、raw/corrected CTDR phase、AF geometry/Jacobian、CFAR margin、detection cell 和 TrackManager debug。
- 预先规定：cell 仍为 row48/col2154 时评估相位与 cross-range 随校正量变化；cell 改变时作为候选变化单独记录，不归为纯 phase 效应。单 period 不用于 Pd/Pfa 或随机误差统计。

当前数据已足以确认 phase 到生产几何误差的局部传播，所以该扫描不是完成 R10 的前置条件。建议它只用于下一步因果拆分 residual physical delay、融合/插值模型和 CTDR 输入相位。R10 之后停止；不训练 AI、不开发 Router、不自动启动 R11。

## 主要证据

- [目标候选与航迹关联一致性](R10_target_association_consistency.md)
- [测角与位置误差归因](R10_angle_position_error_attribution.md)
- [通道时延可观测性](R10_channel_delay_observability.md)
- [检测与任务精度敏感度](R10_task_sensitivity_analysis.md)
- [R8 紧凑 evidence package](evidence/r8_unknown_delay_multiseed_20261023/)
- [R9 紧凑 evidence package](evidence/r9_deterministic_delay_estimator_20261023/)
