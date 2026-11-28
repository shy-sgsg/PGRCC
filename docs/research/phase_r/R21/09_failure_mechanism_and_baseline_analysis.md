# R21：失败机制与传统基线分析

## 观测到的因果链

```text
+4 ns 通道误差
  → B 输入 F2 相对 A0 的 RMS 约 0.328
  → CSI / CFAR 后 B 正确目标命中低于 A0
  → Known 物理校正将 F2 RMS 降至约 8.8e-5，并恢复 A0 命中数
```

这条因果证据仅限冻结的 `+4 ns`、6.7 m/s、30 dB、面积地杂波和两个 seed。两个独立 seed 的 B 命中均低于 A0，Known 均回到 A0；可支持该受控场景存在任务相关误差损失与已知校正可恢复空间，但不能推出普遍 Pd/Pfa 改善。

## M1 与确定性方法

相对 Huber D3 与 R9 fixed，M1 在两个 seed 中的共同成功机会 angle、position RMSE 均更低，F2 残差也均更小；检测命中与两种 D3 相同（seed1001 为 2/3，seed1002 为 3/3）。M1 仍未达到 Known 的物理闭合或 angle/position 精度。Ground-speed 排名反转，false-confirmed ID 计数也并非每 seed 均优于 Huber，因此不能把中间物理量改善表述为所有 GMTI 输出都改善。

M1 的估计时延与 Known 的差值分别约为 `0.109 ns` 和 `0.365 ns`；估计误差较小对应更低 F2 residual，但 residual 仍比 Known 大约两个至三个数量级。后续针对保留复数输入的离线审计确认：两个 seed 的 F2 RMS 可由 C2 残差完全解释，C4 逐位不变，且 C2 正负频率相位斜率与各自的剩余时延一致；F2 融合后的相位斜率因 C2/C4 混合而不等同 C2 通道时延。401 点目标函数剖面各只有一个内部极小值，1001 点局部密网格与 golden-section 解相差不超过 `1.95e-6` samples，故当前 M1–Known 差距不能归因于局部优化器未找到现有目标函数的最优点。详见 [`13_offline_m1_residual_and_cost_audit.md`](13_offline_m1_residual_and_cost_audit.md)。

该补充分析仍只覆盖两个冻结 seed；它没有扰动几何，也没有更换 M1 的 constant-complex-gain 模型。因而它支持将剩余差距定位到 M1 估计后的 C2 标量时延残差，但不能确认该误差在不同场景中的机制，也不能证明广义系统状态不可辨识。

## CSI/SCNR 与自消边界

- seed1001 B 的 output SCNR proxy 高于 A0，但检测更差；seed1002 B proxy 略低。SCNR 单数值无法解释检测差异。
- 保存的 target-only CSI retention 与 paired complex transfer 均有数据，但 CSI 是自适应处理链，不能等同固定线性目标增益；跨 seed transfer 不稳定，不能由其单独宣称目标自消根因。
- H0 的 observed CFAR hit fraction 约为 `0.000188–0.000244`；CUT 空间相关、缺少独立 H0 试验单位，统计 Pfa 不可评估。
- false confirmed track IDs 是 TrackManager lineage 数，不是 Pfa。混合 ID、周期级 matched detection 和错误关联已单独留表。

## 失败机会和无法归因项

逐机会缺失原因与 truth/detection/parameter 字段见 [`r21_detection_opportunities.csv`](evidence/results/r21_detection_opportunities.csv)。B 在 seed1001 有 2/3 未命中机会，seed1002 有 3/3 未命中；角度/位置只对成功机会计算。Radial velocity 的任务机会均因 ambiguity unresolved 或未命中而无有效值。R21 未通过不公平分母补齐这些缺项。

当前数据能证明“误差、物理闭合和 Detection 随处理条件共同变化”，不能单独定位 CFAR 门限、目标保持、自适应 CSI 或 TrackManager 中哪一步是唯一损失根因。每个分支的 CSI tap、CFAR geometry、Production stage 和错误机会记录都保存在审计包中。
