# R12 seed404：已知上限与确定性估计能力复评

## 任务级结果

R12 case seed404 的 A0/B/Known/D3/M1 五条件都具有匹配的 Production binary、归一化 XML、truth、scene/background 与保存 Doppler axis；身份状态 `IDENTITY_VALID`。每条件三个目标机会：

| 条件 | 目标检测 | 相对 B 的命中差 | 目标匹配航迹 period | 估计/校正身份 |
|---|---:|---:|---:|---|
| A0 | 2/3 | +2 | 1 | 无误差参考 |
| B / Current +4 ns | 0/3 | 0 | 0 | 未校正 |
| Known correction | 2/3 | +2 | 1 | evaluator-only 真实物理校正参考 |
| D3 固定校正 | 3/3 | +3 | 2 | R9 固定 D3 校正量，不等同 M1 盲估计器 |
| M1 Physics-Matched | 3/3 | +3 | 2 | 输入角色 `A1_ON_RAW_C1_C2`（从 correction-source 标签推断；不是 manifest 独立字段）；truth flags 均为 false |

M1 保存的校正量为 `3.759357402 ns`，相对真值 `4 ns` 误差 `−0.240642598 ns`；D3 固定量为 `3.409965032 ns`，误差 `−0.590034968 ns`。R12 case manifest 的 `correction_source` 指向 `M1_A1_ON_raw_C1_C2_blind_estimate`；其输入准备记录仅对 B/A1 ON 的协议 IQ 做估计，没有声明读取 A0、Known、truth 或 target Detection；`truth_used_in_estimator=false` 且 `truth_used_to_apply_correction=false`，三期校正审计状态均为 passed。`input_role` 在历史 manifest 中没有独立显式字段，因此 R20 将该角色标成“从来源标签推断”，不把推断冒充原字段。原始 IQ 未随紧凑 bundle 提交，R20 只审阅历史 manifest，未重新执行或重验证 estimator。该输入角色在保存数据的模拟器条件下符合“从受影响 ON 通道观测进行盲估计”的任务信息约束；真实硬件的独立部署可用性仍未验证。

## 参数精度和已知校正差距

Own-success RMSE（各自可成功机会）中，M1 angle `0.05544°`、2D position `85.10 m`、folded Doppler `4.2472 Hz`、ground-speed `11.0316 m/s`（n=2）；D3 对应 `0.08765°`、`134.53 m`、`4.2472 Hz`、`8.0616 m/s`（n=2）。二者径向速度均 0/3 可评，因为 ambiguity unresolved。own-success 样本不能直接与 Known 不同机会的 RMSE 比较。

在 M1、Known 共同匹配的 2 个 truth opportunities 上：M1 angle RMSE `0.04254°`，Known `0.02191°`；position RMSE `65.55 m`，Known `33.77 m`；folded Doppler 均 `3.2711 Hz`。D3 在相同 2 个共同机会上的 angle/position RMSE 为 `0.06915°` / `106.50 m`。Known 的 2/3 hit rate 低于 M1/D3 的 3/3，但盲/固定校正的共同成功机会参数误差更大，故不能把多 1 个 hit 解读成整体胜过物理上限。

历史信号闭合报告记录 M1 F2 relative RMS `2.6840%`、D3 固定校正 `6.3719%`、Known `0.00873%`。这些 closure 数字本次未用原始 IQ 重算，仍是 retained manifest/history evidence；它们显示 M1 误差比 D3 小，但与 Known 仍有显著输入闭合差距。

逐机会、common-success、参数缺失原因和校正来源见 [R12 逐机会表](evidence/results/r12_opportunity_level_comparison.csv)、[blind/deterministic 对照](evidence/results/r12_blind_estimator_comparison.csv)、[pairwise common-success 参数表](evidence/results/r20_parameter_pairwise_common_success.csv)、[参数完整率](evidence/results/r20_parameter_evaluability.csv) 与 [Known closure audit](evidence/results/r12_known_closure_audit.csv)。R20 的 CFAR geometry 表也保存了 OFF 每条件 full/dynamic union CUT counter：每模式 `1,581,840` valid CUT，hit fraction 约 `0.000196–0.000214`；它们仍是空间相关 CUT 的描述统计。没有独立 H0 sampling unit 和统计设计，statistical Pfa 继续 `NOT_EVALUABLE`。

## 结论限制

R12 支持“这个 seed 中确定性校正可改善最终目标检测机会”的可行性观察；不证明 M1 在重复试验中稳定优于 Known，也不证明参数性能恢复到 Known。三目标机会不是三个独立 seeds。CFAR valid-CUT counters 已存在且可描述，但不足以估计 statistical Pfa。保持 R12 既有窄静态 delay estimator baseline 的定位。
