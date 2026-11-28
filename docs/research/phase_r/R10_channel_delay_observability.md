# R10：通道时延可观测性与剩余失配

日期：2026-10-23
范围：复核 R8/R9 已有物理输入、融合估计、固定/在线校正及 production target phase。无 Stage2 重生成、无新 CUDA 运行。

## 状态

- IMPLEMENTED：时延注入、分数延迟逆校正、融合谱盲估计和 CTDR 观测链已在源码中。
- EXECUTED：相关 R8/R9 Native CUDA Production 已执行；R10 使用已留存结果。
- EVALUABLE：本场景注入值、D3 校正值、目标 cell 的条件间 raw CTDR phase 差、F2 融合闭合可评估。
- PERFORMANCE_VERIFIED：只对 seed202/303 与此次 +4 ns 注入场景成立。目标局部每频点物理通道响应和随机方差不能从现存结果完整评估。

## 注入与校正的物理关系

Stage2 对物理通道 2 用零延拓的线性分数延迟插值。采样率 60 MHz，注入 +4 ns 对应 \(d=4{\rm ns}\times60{\rm MHz}=0.24\) sample。R9 固定 D3 参数为 3.4099650319 ns，即校正量约 0.204598 sample；与 evaluator 真值相差约 −0.590035 ns（−0.035402 sample）。

忽略序列边界时，插值算子的频率响应为
\[
H(d,f)=(1-d)+d\,e^{-j2\pi f/f_s}.
\]
fixed-D3 残余传递可写作
\[
H_{\rm res}(f)=H(0.24,f)/H(0.204598,f).
\]
在当前有效基带至 ±25 MHz 的解析范围内，响应幅度约为 0.9017–1，残余相位随频率变化，端点约为 −2.87°，并非一个与频率无关的纯相位斜坡。该数值是根据代码里的插值和校正算子推导，不是本次目标频谱的实测值。注入插值见 [channel_impairments.cpp](../../../simulator/target_injection/channel_impairments.cpp#L77)，逆算子见 [estimate_channel_delay.py](../../../scripts/estimate_channel_delay.py#L340)。

## D3 估计量和物理校正对象不完全相同

R9 的确定性时延估计从融合后的 F1/F2 频谱拟合时延；F1/F2 又由四路协议 IQ 的通道对融合得到。随后 Production 校正只作用于物理通道 2。因此 D3 输出应被解释为当前融合观测下的有效延时估计，不能直接视为物理通道 2 无偏的真值读数。若真实误差通过线性插值产生频率相关幅相响应，单一标量延时也未必能完全抵消它。

R9 物理闭合记录显示，固定 D3 在 ON 数据上把 F2 相对 A0 的 relative RMS 从 seed202 的 0.327884 降到 0.063500；seed303 从 R9 汇总记录为约 0.063515。F1 不受通道 2 校正影响。Known correction 的 R8 条件与 A0 的任务定位几乎重合，但该条件使用 evaluator 真值，只作为已知参数上限。

## 目标处 raw CTDR phase 残差

下表为每个目标匹配 cell 上 D3 相对 A0 的 Δφraw。正值表示 D3 记录的 raw CTDR phase 大于 A0。candidate 是否同 cell 来自 [目标 lineage](evidence/r9_deterministic_delay_estimator_20261023/records/evaluation/target_lineage_audit.csv)；seed303 period 0、2 的 cell 有一行差异。

| Seed / period | 与 A0 同 cell | 固定 D3 Δphase (rad) | 在线 D3 Δphase (rad) |
|---|---|---:|---:|
| 202 / 0 | 是 | +0.05836 | +0.06653 |
| 202 / 2 | 是 | +0.04542 | +0.05183 |
| 303 / 0 | 否，row −1 | +0.10681 | +0.14362 |
| 303 / 1 | 是 | +0.01777 | +0.02951 |
| 303 / 2 | 否，row −1 | +0.09379 | +0.12587 |

同 cell 的三期存在稳定正向 phase 差，并且已由 R10 生产局部 Jacobian 映射为相应 cross-range 改变量，见 [R10 angle/position attribution](R10_angle_position_error_attribution.md)。但现有 compact package 保存的是 target-only 5×5 ROI 的 CSI 复响应和聚合 phase，不是 CTDR 求解器实际目标像素在每个物理频点上的 ch2/F2 复响应。因而不能把上表全部差值拆成物理时延项与融合/杂波项。

## 可辨识与不可辨识的部分

| 分量 | 当前证据 | R10 判定 |
|---|---|---|
| 固定标定偏差 | evaluator 注入为 4 ns，固定标定为 3.409965 ns | 可确认此实验中标定值低估注入值约 0.590 ns |
| 分数延迟频率响应 | 源码确定为零延拓线性插值；可解析计算 Hres(f) | 算子频率相关性可计算；目标局部频谱上的实际残差不可由聚合 tap 唯一重建 |
| 融合谱到物理 ch2 的估计偏差 | estimator 输入为 F1/F2 融合谱，校正仅写物理 ch2 | 结构性不匹配明确存在；无法仅凭固定 D3 输出分离其数值贡献 |
| 候选/杂波造成的 phase 改变 | 3/5 正确期同 cell；另外 2/5 的 Doppler row 改变 | 可把两个候选变化期标为混杂；同 cell 三期仍有 phase 残差 |
| 随机 phase 误差 | 每个 seed/period/条件只有已保存的一次结果，没有独立重复 | 不可估计方差或置信区间；不能以 period 间差代替随机重复 |

因此，数据支持“固定 D3 后仍存在目标处 phase/几何误差，并且已知物理校正能恢复本次精度”的结论；数据不支持把全部 residual 归因于时延估计偏差、插值响应或单一融合模型项中的任意一个。

可复核证据：[R9 estimator 与公式记录](evidence/r9_deterministic_delay_estimator_20261023/records/estimator/)、[F1/F2 physical closure](evidence/r9_deterministic_delay_estimator_20261023/records/evaluation/physical_closure.csv)、[production input/runtime/tap 索引](evidence/r9_deterministic_delay_estimator_20261023/records/production_branches.csv)。完整本地 CSI/CFAR/production CSV 位于 outputs/phase_r_r9_deterministic_delay_20261023/，R8 原始配对输入位于 outputs/phase_r8_unknown_delay_multiseed_20261023/。
